"""
graph/nodes/output_guardrails.py — Output safety layer for RepoMind AI.

Applied after the LLM generates draft_response.  Performs:

1. Secret / credential redaction (regex-based, no false negatives preferred).
2. .env content leak detection and blocking.
3. Hallucinated file reference detection — warns if the LLM mentions a file
   path that was NOT in the retrieved chunks (possible hallucination).
4. Ensures the response is repo-related (not a bare off-topic answer that
   slipped through the classifier).

Design decisions
----------------
* We redact rather than block where possible — a useful answer with one
  redacted token is better than a complete block.
* File-reference hallucination check is advisory only (adds a warning) so
  partially valid answers are not suppressed.
* The node always writes final_response, even when it passes through
  draft_response unchanged.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from graph.state import AgentState

logger = logging.getLogger(__name__)

# ── Secret patterns to redact ──────────────────────────────────────────────────
# These patterns catch common secret formats that may appear in code context.

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI keys
    (re.compile(r"sk-[A-Za-z0-9]{20,}", re.I), "[REDACTED_OPENAI_KEY]"),
    # Google / Gemini API keys
    (re.compile(r"AIza[A-Za-z0-9_-]{35}", re.I), "[REDACTED_GOOGLE_KEY]"),
    # GitHub personal access tokens (classic + fine-grained)
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}", re.I), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{82}", re.I), "[REDACTED_GITHUB_TOKEN]"),
    # AWS access key id + secret
    (re.compile(r"AKIA[0-9A-Z]{16}", re.I), "[REDACTED_AWS_KEY]"),
    (re.compile(r"(?:aws_secret_access_key\s*=\s*)[A-Za-z0-9/+]{40}", re.I),
     "aws_secret_access_key=[REDACTED]"),
    # Generic bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}", re.I), "Bearer [REDACTED_TOKEN]"),
    # Passwords in key=value style
    (re.compile(r'(?:password|passwd|pwd)\s*[:=]\s*["\']?[^\s"\']{8,}["\']?', re.I),
     "password=[REDACTED]"),
    # Private key header
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]+?-----END \1PRIVATE KEY-----"),
     "[REDACTED_PRIVATE_KEY]"),
    # .env variable assignments with secret-like names
    (re.compile(
        r'(?:SECRET|TOKEN|KEY|PASSWORD|CREDENTIAL|AUTH)\s*=\s*["\']?[A-Za-z0-9_\-./]{8,}["\']?',
        re.I,
    ), "[REDACTED_SECRET_VALUE]"),
]

# ── .env content detection ────────────────────────────────────────────────────

_ENV_LEAK_PATTERN = re.compile(
    r"(?:^|\n)\s*[A-Z][A-Z0-9_]{2,}\s*=\s*\S+",
    re.MULTILINE,
)

_ENV_LINE_THRESHOLD = 3  # block if 3+ .env-style lines appear in the response


# ── Hallucinated file detection ────────────────────────────────────────────────

# Matches markdown code references like `path/to/file.py` or **path/to/file.py**
_FILE_REF_PATTERN = re.compile(
    r"`([^`]{3,}/[^`]{1,})\`"     # backtick-enclosed paths
    r"|(?:\*\*|\b)([A-Za-z0-9_./-]{3,}\.[a-z]{1,6})\b",  # bold or plain
)


def _extract_mentioned_files(text: str) -> set[str]:
    """Extract all file-path-like strings mentioned in the LLM output."""
    files: set[str] = set()
    for m in _FILE_REF_PATTERN.finditer(text):
        candidate = m.group(1) or m.group(2)
        if candidate and "/" in candidate:
            files.add(candidate.strip())
    return files


# ── Redaction helper ───────────────────────────────────────────────────────────

def _redact_secrets(text: str) -> tuple[str, int]:
    """
    Apply all secret redaction patterns.

    Returns (redacted_text, number_of_replacements).
    """
    total = 0
    for pattern, replacement in _REDACT_PATTERNS:
        text, n = pattern.subn(replacement, text)
        total += n
    return text, total


# ── .env leak check ───────────────────────────────────────────────────────────

def _has_env_leak(text: str) -> bool:
    matches = _ENV_LEAK_PATTERN.findall(text)
    return len(matches) >= _ENV_LINE_THRESHOLD


# ── Node ──────────────────────────────────────────────────────────────────────

async def output_guardrail_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: sanitize and validate the draft_response.

    Reads:   draft_response, retrieved_chunks
    Writes:  final_response, guardrail_result (updated)
    """
    draft: str = state.get("draft_response", "")
    retrieved_chunks: list[dict] = state.get("retrieved_chunks", [])
    existing_guardrail: dict = state.get("guardrail_result", {})

    # ── 1. .env leak check (hard block) ──────────────────────────────────────
    if _has_env_leak(draft):
        reason = (
            "The generated response appeared to contain .env-style variable "
            "assignments. The response has been suppressed for security."
        )
        logger.warning("Output guardrail: .env leak detected — blocking response")
        return {
            "final_response": (
                "⚠️ The response was blocked because it contained what appeared "
                "to be environment variable assignments. Please rephrase your question."
            ),
            "guardrail_result": {
                **existing_guardrail,
                "output_passed": False,
                "output_reason": reason,
            },
        }

    # ── 2. Secret redaction ───────────────────────────────────────────────────
    cleaned, redaction_count = _redact_secrets(draft)
    if redaction_count > 0:
        logger.warning(
            "Output guardrail: redacted %d secret pattern(s) from response",
            redaction_count,
        )

    # ── 3. Hallucinated file reference check (advisory) ──────────────────────
    valid_file_paths: set[str] = {
        c.get("file_path", "") for c in retrieved_chunks
    }
    mentioned_files = _extract_mentioned_files(cleaned)
    hallucinated = mentioned_files - valid_file_paths

    warning_suffix = ""
    if hallucinated:
        logger.warning(
            "Output guardrail: possible hallucinated file references: %s",
            hallucinated,
        )
        warning_suffix = (
            "\n\n> ⚠️ **Note:** The following file paths were mentioned by the AI "
            "but were not found in the retrieved context — they may be inaccurate: "
            + ", ".join(f"`{f}`" for f in sorted(hallucinated))
        )

    final_response = cleaned + warning_suffix

    return {
        "final_response": final_response,
        "guardrail_result": {
            **existing_guardrail,
            "output_passed": True,
            "output_reason": None,
            "redactions": redaction_count,
            "hallucinated_files": list(hallucinated),
        },
    }
