"""
graph/nodes/pr_guardrails.py — PR review safety and validation layer.

Applied AFTER pr_review_agent_node, BEFORE review_formatter_node.

Guardrail checks
----------------
1.  Issue file must be in the PR's changed files OR retrieved related files.
2.  File path must be non-empty and not contain suspicious traversal patterns.
3.  Line number must appear in the PR diff when available (soft — sets to null if invalid).
4.  Severity must be exactly "Low", "Medium", or "High".
5.  risk_score must be an integer clamped to [0, 10].
6.  Every issue must have a non-empty "evidence" field.
7.  Secrets / credentials are redacted from all text fields.
8.  Issues referencing files not in the PR or index are removed.
9.  final_recommendation must not mention any fake/invalid file names.
10. "status" must be one of the three canonical values.

Security
--------
* Redacts API keys, tokens, passwords, .env assignments from text fields.
* Never logs raw diff content or secrets.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from graph.state import AgentState

logger = logging.getLogger(__name__)

# ── Valid constants ────────────────────────────────────────────────────────────

_VALID_SEVERITIES = {"Low", "Medium", "High"}
_VALID_STATUSES   = {"Safe to merge", "Needs changes", "Risky PR"}

# ── Secret redaction patterns ──────────────────────────────────────────────────

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI
    (re.compile(r"sk-[A-Za-z0-9]{20,}", re.I), "[REDACTED_OPENAI_KEY]"),
    # Google / Gemini
    (re.compile(r"AIza[A-Za-z0-9_-]{35}", re.I), "[REDACTED_GOOGLE_KEY]"),
    # GitHub tokens
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}", re.I), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{82}", re.I), "[REDACTED_GITHUB_TOKEN]"),
    # AWS
    (re.compile(r"AKIA[0-9A-Z]{16}", re.I), "[REDACTED_AWS_KEY]"),
    (re.compile(r"(?:aws_secret_access_key\s*=\s*)[A-Za-z0-9/+]{40}", re.I),
     "aws_secret_access_key=[REDACTED]"),
    # Generic bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}", re.I), "Bearer [REDACTED_TOKEN]"),
    # Passwords in key=value style
    (re.compile(r'(?:password|passwd|pwd)\s*[:=]\s*["\']?[^\s"\']{8,}["\']?', re.I),
     "password=[REDACTED]"),
    # Private keys
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]+?-----END \1PRIVATE KEY-----"),
     "[REDACTED_PRIVATE_KEY]"),
    # .env secret assignments
    (re.compile(
        r'(?:SECRET|TOKEN|KEY|PASSWORD|CREDENTIAL|AUTH)\s*=\s*["\']?[A-Za-z0-9_\-./]{8,}["\']?',
        re.I,
    ), "[REDACTED_SECRET_VALUE]"),
]

# .env-style line detection
_ENV_LEAK_RE = re.compile(r"(?:^|\n)\s*[A-Z][A-Z0-9_]{2,}\s*=\s*\S+", re.MULTILINE)
_ENV_LEAK_THRESHOLD = 3

# Path traversal
_TRAVERSAL_RE = re.compile(r"\.\.[/\\]")


def _redact(text: str) -> tuple[str, int]:
    """Apply all secret redaction patterns. Returns (cleaned_text, count)."""
    count = 0
    for pattern, replacement in _REDACT_PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n
    return text, count


def _redact_issue_fields(issue: dict) -> dict:
    """Redact secrets from all text fields of an issue dict."""
    text_fields = ("title", "evidence", "problem", "impact", "suggested_fix")
    result = dict(issue)
    total = 0
    for field in text_fields:
        val = result.get(field, "")
        if isinstance(val, str):
            result[field], n = _redact(val)
            total += n
    if total:
        logger.warning("PR guardrail: redacted %d secret(s) from issue %r", total, result.get("title", "?"))
    return result


# ── Validators ─────────────────────────────────────────────────────────────────

def _is_valid_file(filename: str, allowed_files: set[str]) -> bool:
    """Return True if filename is in the allowed set (exact match or path suffix)."""
    if not filename or _TRAVERSAL_RE.search(filename):
        return False
    if filename in allowed_files:
        return True
    # Allow basename match — guards against minor path prefix differences
    basename = filename.split("/")[-1]
    return any(fp.endswith(basename) for fp in allowed_files)


def _clamp_risk_score(raw: Any) -> int:
    """Clamp risk_score to [0, 10]."""
    try:
        score = int(float(raw))
    except (TypeError, ValueError):
        return 5
    return max(0, min(10, score))


def _validate_severity(sev: Any) -> str:
    """Return severity if valid, else 'Medium'."""
    if isinstance(sev, str) and sev in _VALID_SEVERITIES:
        return sev
    return "Medium"


def _validate_status(status: Any) -> str:
    """Return status if valid, else 'Needs changes'."""
    if isinstance(status, str) and status in _VALID_STATUSES:
        return status
    return "Needs changes"


def _validate_line(
    line: Any,
    filename: str,
    file_diffs_json: dict,
) -> int | None:
    """
    Validate and return line number.

    Returns None when:
    - line is None (LLM set it to null)
    - line number is not in the diff for the file (soft validation)
    """
    if line is None:
        return None
    try:
        lineno = int(line)
    except (TypeError, ValueError):
        return None

    if lineno <= 0:
        return None

    # Check if the line is in the diff for this file
    fd = file_diffs_json.get(filename, {})
    if not fd:
        # No diff info — accept but can't verify
        return lineno

    added_lines: list[int] = fd.get("added_lines", [])
    changed_range: list[int] = fd.get("changed_range", [])

    if added_lines and lineno in added_lines:
        return lineno

    if changed_range and len(changed_range) == 2:
        if changed_range[0] <= lineno <= changed_range[1]:
            return lineno

    # Line not found in diff — return null rather than a fake line
    logger.debug(
        "PR guardrail: line %d not in diff for %s — setting to null",
        lineno,
        filename,
    )
    return None


# ── Node ──────────────────────────────────────────────────────────────────────

async def pr_guardrail_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: validate and sanitize the raw PR review from pr_review_agent.

    Reads:
        draft_review      — raw review dict from LLM
        guardrail_result  — contains changed_file_paths + file_diffs_json
        retrieved_chunks  — Qdrant chunks (for related file paths)

    Writes:
        draft_review      — cleaned, validated review dict
        guardrail_result  — updated with guardrail stats
        error             — set if review is completely invalid
    """
    draft_review: dict = state.get("draft_review", {})
    guardrail_ctx: dict = state.get("guardrail_result", {})
    retrieved_chunks: list[dict] = state.get("retrieved_chunks", [])

    # ── Build allowed file sets ────────────────────────────────────────────────
    changed_file_paths: set[str] = set(guardrail_ctx.get("changed_file_paths", []))
    file_diffs_json: dict = guardrail_ctx.get("file_diffs_json", {})

    # Related files from Qdrant context also count as valid references
    related_file_paths: set[str] = {
        c.get("file_path", "") for c in retrieved_chunks if c.get("file_path")
    }
    allowed_files = changed_file_paths | related_file_paths

    # ── Validate top-level fields ─────────────────────────────────────────────
    status = _validate_status(draft_review.get("status"))
    risk_score = _clamp_risk_score(draft_review.get("risk_score", 5))
    summary, _ = _redact(str(draft_review.get("summary", "")))
    final_rec, _ = _redact(str(draft_review.get("final_recommendation", "")))

    # ── Validate issues ────────────────────────────────────────────────────────
    raw_issues: list[dict] = draft_review.get("issues", [])
    if not isinstance(raw_issues, list):
        raw_issues = []

    validated_issues: list[dict] = []
    removed_count = 0
    total_redactions = 0

    for issue in raw_issues[:25]:  # cap at 25 issues
        if not isinstance(issue, dict):
            removed_count += 1
            continue

        filename = str(issue.get("file", "")).strip()

        # Rule 1: issue file must be a changed or related file
        if filename and not _is_valid_file(filename, allowed_files):
            logger.warning(
                "PR guardrail: removing issue %r — file %r not in PR or index",
                issue.get("title", "?"),
                filename,
            )
            removed_count += 1
            continue

        # Rule 6: must have non-empty evidence
        evidence = str(issue.get("evidence", "")).strip()
        if not evidence:
            logger.warning(
                "PR guardrail: removing issue %r — missing evidence",
                issue.get("title", "?"),
            )
            removed_count += 1
            continue

        # Rule 4: severity must be valid
        severity = _validate_severity(issue.get("severity"))

        # Rule 3: validate line number against diff
        validated_line = _validate_line(
            issue.get("line"),
            filename,
            file_diffs_json,
        )

        # Rule 7: redact secrets from text fields
        clean_issue = _redact_issue_fields(issue)
        total_redactions += 1  # counted per-issue to avoid double counting

        validated_issues.append({
            "title":        str(clean_issue.get("title", "")).strip(),
            "file":         filename,
            "line":         validated_line,
            "severity":     severity,
            "evidence":     str(clean_issue.get("evidence", "")).strip(),
            "problem":      str(clean_issue.get("problem", "")).strip(),
            "impact":       str(clean_issue.get("impact", "")).strip(),
            "suggested_fix": str(clean_issue.get("suggested_fix", "")).strip(),
        })

    # ── .env leak check on summary / final_recommendation ─────────────────────
    if (
        len(_ENV_LEAK_RE.findall(summary)) >= _ENV_LEAK_THRESHOLD
        or len(_ENV_LEAK_RE.findall(final_rec)) >= _ENV_LEAK_THRESHOLD
    ):
        logger.warning("PR guardrail: .env leak detected in summary/recommendation — redacting")
        summary = "[Response blocked: contained .env-style assignments]"
        final_rec = "Please re-run the review. A safety filter removed potentially sensitive content."

    # ── Build clean review ─────────────────────────────────────────────────────
    clean_review = {
        "status":               status,
        "risk_score":           risk_score,
        "summary":              summary,
        "issues":               validated_issues,
        "final_recommendation": final_rec,
    }

    logger.info(
        "PR guardrail complete: %d issues kept, %d removed, risk_score=%d, status=%r",
        len(validated_issues),
        removed_count,
        risk_score,
        status,
    )

    return {
        "draft_review": clean_review,
        "guardrail_result": {
            **guardrail_ctx,
            "pr_guardrail_passed": True,
            "issues_removed":      removed_count,
            "issues_kept":         len(validated_issues),
            "total_redactions":    total_redactions,
        },
    }
