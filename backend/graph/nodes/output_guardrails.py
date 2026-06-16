"""
graph/nodes/output_guardrails.py — Output safety layer for RepoMind AI.

Applied after the LLM generates draft_response.

Checks:
1. Secret / credential redaction.
2. .env content leak blocking.
3. File-reference validation.
4. Prevents false warnings for valid file references with line ranges.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from graph.state import AgentState

logger = logging.getLogger(__name__)


# ── Secret patterns to redact ────────────────────────────────────────────────

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI keys
    (re.compile(r"sk-[A-Za-z0-9]{20,}", re.I), "[REDACTED_OPENAI_KEY]"),

    # Google / Gemini API keys
    (re.compile(r"AIza[A-Za-z0-9_-]{35}", re.I), "[REDACTED_GOOGLE_KEY]"),

    # GitHub tokens
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}", re.I), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{60,}", re.I), "[REDACTED_GITHUB_TOKEN]"),

    # AWS keys
    (re.compile(r"AKIA[0-9A-Z]{16}", re.I), "[REDACTED_AWS_KEY]"),
    (
        re.compile(r"(aws_secret_access_key\s*=\s*)[A-Za-z0-9/+]{40}", re.I),
        r"\1[REDACTED]",
    ),

    # Bearer tokens
    (
        re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}", re.I),
        "Bearer [REDACTED_TOKEN]",
    ),

    # Password-like assignments
    (
        re.compile(
            r'(password|passwd|pwd)\s*[:=]\s*["\']?[^\s"\']{8,}["\']?',
            re.I,
        ),
        r"\1=[REDACTED]",
    ),

    # Private key block
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]+?"
            r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.I,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),

    # Secret-like env assignments
    (
        re.compile(
            r"(SECRET|TOKEN|KEY|PASSWORD|CREDENTIAL|AUTH)\s*=\s*"
            r'["\']?[A-Za-z0-9_\-./]{8,}["\']?',
            re.I,
        ),
        r"\1=[REDACTED_SECRET_VALUE]",
    ),
]


# ── .env content detection ──────────────────────────────────────────────────

_ENV_LEAK_PATTERN = re.compile(
    r"(?:^|\n)\s*[A-Z][A-Z0-9_]{2,}\s*=\s*\S+",
    re.MULTILINE,
)

_ENV_LINE_THRESHOLD = 3


# ── File-reference detection ────────────────────────────────────────────────

CODE_EXTENSIONS = (
    ".tsx",
    ".jsx",
    ".py",
    ".ts",
    ".js",
    ".json",
    ".md",
    ".html",
    ".css",
)

_FILE_PATH_PATTERN = re.compile(
    r"[\w./-]+\.(?:tsx|jsx|py|ts|js|json|md|html|css)"
    r"(?::\d+(?:-\d+)?)?"
    r"(?![A-Za-z0-9_])"
)


def _normalize_file_ref(ref: str) -> str:
    """
    Normalize file references.

    Examples:
    `server/middleware/auth.js:17-25`       -> server/middleware/auth.js
    server/middleware/auth.js, lines 17-25  -> server/middleware/auth.js
    [`server/middleware/auth.js`]           -> server/middleware/auth.js
    """

    ref = ref.strip()

    # Remove markdown characters
    ref = ref.strip("`")
    ref = ref.strip("[]()")
    ref = ref.replace("`", "")

    # Remove markdown link text noise
    ref = ref.replace("[", "").replace("]", "")

    # Remove line suffix like :17 or :17-25
    ref = re.sub(r":\d+(?:-\d+)?$", "", ref)

    # Remove ", lines 10-20"
    ref = re.sub(
        r",?\s*lines?\s+\d+(?:-\d+)?",
        "",
        ref,
        flags=re.IGNORECASE,
    )

    return ref.strip()


def _extract_mentioned_files(text: str) -> set[str]:
    """
    Extract only real file paths.

    This intentionally ignores API routes like:
    /api/admin/login
    /admin/login

    because they do not end with code file extensions.
    """

    files: set[str] = set()

    # Extract file paths from anywhere in text
    for match in _FILE_PATH_PATTERN.findall(text):
        cleaned = _normalize_file_ref(match)

        if cleaned.endswith(CODE_EXTENSIONS) and "/" in cleaned:
            files.add(cleaned)

    # Extract backtick content and filter only file paths
    for match in re.findall(r"`([^`]+)`", text):
        cleaned = _normalize_file_ref(match)

        if cleaned.endswith(CODE_EXTENSIONS) and "/" in cleaned:
            files.add(cleaned)

    return files


def _extract_retrieved_file_paths(retrieved_chunks: list[dict]) -> set[str]:
    """
    Extract file paths from retrieved chunks.

    Supports both:
    {"file_path": "..."}
    {"metadata": {"file_path": "..."}}
    """

    files: set[str] = set()

    for chunk in retrieved_chunks:
        if not isinstance(chunk, dict):
            continue

        direct_path = chunk.get("file_path")
        if direct_path:
            files.add(str(direct_path).strip())

        metadata = chunk.get("metadata")
        if isinstance(metadata, dict):
            metadata_path = metadata.get("file_path")
            if metadata_path:
                files.add(str(metadata_path).strip())

    return files


# ── Redaction helper ─────────────────────────────────────────────────────────

def _redact_secrets(text: str) -> tuple[str, int]:
    total = 0

    for pattern, replacement in _REDACT_PATTERNS:
        text, count = pattern.subn(replacement, text)
        total += count

    return text, total


# ── .env leak check ──────────────────────────────────────────────────────────

def _has_env_leak(text: str) -> bool:
    matches = _ENV_LEAK_PATTERN.findall(text)
    return len(matches) >= _ENV_LINE_THRESHOLD


# ── Node ────────────────────────────────────────────────────────────────────

async def output_guardrail_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: sanitize and validate draft_response.

    Reads:
    - draft_response
    - retrieved_chunks

    Writes:
    - final_response
    - guardrail_result
    """

    draft: str = state.get("draft_response", "")
    retrieved_chunks: list[dict] = state.get("retrieved_chunks", [])
    existing_guardrail: dict = state.get("guardrail_result", {})

    # 1. Hard block for .env-like output
    if _has_env_leak(draft):
        logger.warning("Output guardrail blocked possible .env leak")

        return {
            "final_response": (
                "⚠️ The response was blocked because it appeared to contain "
                "environment variable assignments or secret-like values. "
                "Please rephrase your question."
            ),
            "guardrail_result": {
                **existing_guardrail,
                "output_passed": False,
                "output_reason": "Possible .env leak detected",
                "redactions": 0,
                "hallucinated_files": [],
            },
        }

    # 2. Redact secrets
    cleaned, redaction_count = _redact_secrets(draft)

    if redaction_count > 0:
        logger.warning(
            "Output guardrail redacted %d secret pattern(s)",
            redaction_count,
        )

    # 3. File hallucination check
    valid_file_paths = _extract_retrieved_file_paths(retrieved_chunks)
    mentioned_files = _extract_mentioned_files(cleaned)

    hallucinated = {
        file for file in mentioned_files
        if file not in valid_file_paths
    }

    warning_suffix = ""

    if hallucinated:
        logger.warning(
            "Output guardrail found possible hallucinated file references: %s",
            hallucinated,
        )

        warning_suffix = (
            "\n\n> ⚠️ **Note:** Some file references could not be verified "
            "from the retrieved context: "
            + ", ".join(f"`{file}`" for file in sorted(hallucinated))
        )

    final_response = cleaned + warning_suffix

    return {
        "final_response": final_response,
        "guardrail_result": {
            **existing_guardrail,
            "output_passed": True,
            "output_reason": None,
            "redactions": redaction_count,
            "hallucinated_files": sorted(hallucinated),
        },
    }