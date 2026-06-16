"""
graph/nodes/file_review_agent.py — File-level code review node for RepoMind AI.

Flow inside this node
---------------------
1. Validate selected_file from AgentState (must exist in the indexed repo).
2. Resolve the absolute path safely — block path traversal, .env, ignored dirs.
3. Read the file content (up to MAX_FILE_SIZE_BYTES).
4. Build a structured review prompt grounded ONLY in the file content.
5. Call the LLM and parse the structured review output.
6. Return the review as draft_response (JSON-serialisable dict in draft_review).

Security rules enforced here (defence in depth)
------------------------------------------------
* Path is resolved with Path.resolve() and checked to be inside repo root.
* Filename is matched against IGNORED_FILENAMES (.env, lock files …).
* Any directory component matched against IGNORED_DIRS is rejected.
* Files containing null bytes are treated as binary and rejected.
* File size is capped at MAX_FILE_SIZE_BYTES (1 MB).
* The LLM is explicitly instructed to use ONLY the file content provided.
* Line numbers in the output are validated against actual file length.
* Output guardrail node (existing) runs afterward for secret redaction.

Output format (draft_review dict)
----------------------------------
{
  "file": "backend/routes/auth.js",
  "summary": "...",
  "issues": [
    {
      "index": 1,
      "title": "Missing input validation on login endpoint",
      "line": 42,
      "severity": "High",
      "problem": "...",
      "impact": "...",
      "suggested_fix": "..."
    }
  ],
  "final_recommendation": "...",
  "issue_count": N,
  "severity_counts": {"High": N, "Medium": N, "Low": N}
}
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from graph.state import AgentState
from tools.repo_scanner import (
    IGNORED_DIRS,
    IGNORED_FILENAMES,
    MAX_FILE_SIZE_BYTES,
    SUPPORTED_EXTENSIONS,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_PROMPT_CHARS = 60_000   # hard cap on content sent to LLM
_MAX_ISSUES       = 20       # cap LLM issue count
_VALID_SEVERITIES = {"Low", "Medium", "High"}

# ── Review system prompt ───────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are RepoMind AI — an expert code reviewer.

You will receive a complete source file. Your task is to review it and produce
a STRUCTURED JSON report.

STRICT RULES
------------
1. Base your review ONLY on the file content provided — do not hallucinate code.
2. Use EXACT line numbers from the file (first line = line 1).
3. Each issue MUST have a severity: exactly one of "Low", "Medium", or "High".
4. Do NOT suggest auto-generated patches or diffs.
5. Do NOT reveal or reference secrets, API keys, or credentials in your output.
6. Focus on: logic bugs, missing validation, bad error handling, security risks,
   performance issues, and code quality problems.
7. If a file looks clean, say so with 0 issues.

OUTPUT FORMAT — return ONLY valid JSON, no prose before or after:

{
  "summary": "<one-paragraph summary of the file's purpose and overall quality>",
  "issues": [
    {
      "index": 1,
      "title": "<short descriptive title>",
      "line": <integer or null if not pinpointable>,
      "severity": "High|Medium|Low",
      "problem": "<what is wrong and why>",
      "impact": "<what can go wrong because of this>",
      "suggested_fix": "<description of how to fix, no code patch>"
    }
  ],
  "final_recommendation": "<overall verdict and top priority action>"
}
"""


# ── Path safety helpers ───────────────────────────────────────────────────────

class FileAccessError(Exception):
    """Raised when the requested file fails safety checks."""
    def __init__(self, reason: str, http_status: int = 400):
        super().__init__(reason)
        self.http_status = http_status


def _safe_resolve(repo_root: Path, relative_path: str) -> Path:
    """
    Resolve *relative_path* inside *repo_root* safely.

    Enforces:
      - No path traversal (resolved path must start with repo_root).
      - No access to .env or IGNORED_FILENAMES.
      - No access to IGNORED_DIRS at any depth.
      - File must exist and be a regular file.
      - File must have a supported extension.
      - File must not be binary.
      - File must not exceed MAX_FILE_SIZE_BYTES.

    Returns the resolved absolute Path.
    Raises FileAccessError with a user-safe message on any violation.
    """
    import os

    root = repo_root.resolve()

    # ── 0. Normalise the user-supplied path ────────────────────────────────────
    # Accept forward or back slashes, but DO NOT strip leading dots
    # (that would turn ".env" into "env").
    # We only strip leading explicit slashes that make it absolute.
    stripped = relative_path.replace("\\", "/")
    while stripped.startswith("/"):
        stripped = stripped[1:]

    # Strip a single leading "./" but nothing else (preserves ".env", "../")
    if stripped.startswith("./"):
        stripped = stripped[2:]

    if not stripped:
        raise FileAccessError("file_path must not be empty.")

    # ── 1. Pre-resolve traversal check ────────────────────────────────────────
    # Split on "/" and check for ".." components BEFORE resolving so Windows
    # normalisation cannot hide the traversal.
    parts = [p for p in stripped.replace("\\", "/").split("/") if p]
    if ".." in parts:
        raise FileAccessError(
            "Access denied: path traversal detected ('..' is not allowed). "
            "file_path must be relative to the repository root.",
            http_status=403,
        )
    if not parts:
        raise FileAccessError("file_path must not be empty.")

    # ── 2. Resolve candidate ───────────────────────────────────────────────────
    candidate = (root / Path(*parts)).resolve()

    # ── 3. Post-resolve containment check (belt-and-suspenders) ───────────────
    try:
        candidate.relative_to(root)
    except ValueError:
        raise FileAccessError(
            "Access denied: path resolves outside the repository root.",
            http_status=403,
        )

    # ── 4. Existence check ────────────────────────────────────────────────────
    if not candidate.exists():
        raise FileAccessError(f"File not found: {relative_path}", http_status=404)
    if not candidate.is_file():
        raise FileAccessError(
            f"'{relative_path}' is a directory, not a file.", http_status=400
        )

    # ── 5. Ignored filename check (.env, lock files) ──────────────────────────
    if candidate.name in IGNORED_FILENAMES:
        raise FileAccessError(
            f"Access denied: '{candidate.name}' is a protected file "
            "and cannot be reviewed.",
            http_status=403,
        )

    # Explicitly block any .env variant (belt-and-suspenders — IGNORED_FILENAMES
    # already covers ".env", but let's be explicit).
    lower_name = candidate.name.lower()
    if lower_name == ".env" or (
        lower_name.startswith(".env.") and lower_name != ".env.example"
    ):
        raise FileAccessError(
            "Access denied: .env files cannot be reviewed.",
            http_status=403,
        )

    # ── 6. Ignored directory check ────────────────────────────────────────────
    rel = candidate.relative_to(root)
    for part in rel.parts[:-1]:   # all directory components (not the filename)
        if part in IGNORED_DIRS or part.startswith("."):
            raise FileAccessError(
                f"Access denied: '{relative_path}' is inside an ignored directory "
                f"('{part}').",
                http_status=403,
            )

    # ── 7. Extension check ────────────────────────────────────────────────────
    suffix = candidate.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise FileAccessError(
            f"File type '{suffix}' is not supported for review. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            http_status=400,
        )

    # ── 8. Size check ─────────────────────────────────────────────────────────
    size = candidate.stat().st_size
    if size == 0:
        raise FileAccessError("File is empty — nothing to review.", http_status=400)
    if size > MAX_FILE_SIZE_BYTES:
        raise FileAccessError(
            f"File is too large ({size // 1024} KB). "
            f"Maximum allowed: {MAX_FILE_SIZE_BYTES // 1024} KB.",
            http_status=400,
        )

    # ── 9. Binary check ───────────────────────────────────────────────────────
    with candidate.open("rb") as fh:
        probe = fh.read(8192)
    if b"\x00" in probe:
        raise FileAccessError(
            f"'{relative_path}' appears to be a binary file and cannot be reviewed.",
            http_status=400,
        )

    return candidate


def _read_file(abs_path: Path) -> tuple[str, int]:
    """
    Read the file as UTF-8 (fallback: latin-1).

    Returns (content_string, line_count).
    Truncates to _MAX_PROMPT_CHARS if necessary.
    """
    try:
        content = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise FileAccessError(f"Cannot read file: {exc}", http_status=500)

    line_count = content.count("\n") + 1

    if len(content) > _MAX_PROMPT_CHARS:
        content = content[:_MAX_PROMPT_CHARS]
        content += f"\n\n[... TRUNCATED at {_MAX_PROMPT_CHARS} characters ...]"
        logger.info("File truncated for review: %s", abs_path.name)

    return content, line_count


# ── LLM call ─────────────────────────────────────────────────────────────────

async def _call_review_llm(
    file_path: str,
    language: str,
    content: str,
    query: str,
) -> dict:
    """
    Send the file content to the LLM and return a parsed review dict.

    Falls back to a structured error dict on any LLM failure.
    """
    from config import settings
    from langchain_core.messages import HumanMessage, SystemMessage

    language_label = language or "unknown"
    user_content = (
        f"## File to Review\n\n"
        f"**Path:** `{file_path}`  \n"
        f"**Language:** {language_label}\n\n"
        f"**Reviewer's focus:** {query}\n\n"
        f"---\n\n"
        f"```{language_label}\n{content}\n```\n\n"
        f"Produce the JSON review now."
    )

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    # Select LLM
    if settings.gemini_api_key.strip():
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            temperature=0.1,
            google_api_key=settings.gemini_api_key,
            max_output_tokens=4096,
        )
    elif settings.openai_api_key.strip():
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,          # low temp for consistent structured output
            openai_api_key=settings.openai_api_key,
            max_tokens=4096,
        )
    else:
        return _error_review(
            file_path,
            "No LLM API key configured. Set GEMINI_API_KEY or OPENAI_API_KEY.",
        )

    try:
        response = await llm.ainvoke(messages)
        raw = response.content.strip()
    except Exception as exc:
        logger.error("LLM review call failed: %s", exc)
        return _error_review(file_path, f"LLM call failed: {exc}")

    return _parse_review_json(raw, file_path)


def _error_review(file_path: str, reason: str) -> dict:
    """Return a structured review dict indicating an LLM error."""
    return {
        "file": file_path,
        "summary": f"Review could not be completed: {reason}",
        "issues": [],
        "final_recommendation": "Please try again.",
        "issue_count": 0,
        "severity_counts": {"High": 0, "Medium": 0, "Low": 0},
        "_error": reason,
    }


def _parse_review_json(raw: str, file_path: str) -> dict:
    """
    Extract and parse the JSON review from the LLM response.

    The LLM may wrap the JSON in markdown fences; we strip those first.
    If parsing fails we return a structured error dict.
    """
    # Strip ```json ... ``` fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    json_str = fence_match.group(1) if fence_match else raw

    # Find the outermost JSON object
    brace_match = re.search(r"\{[\s\S]*\}", json_str)
    if brace_match:
        json_str = brace_match.group(0)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse LLM review JSON: %s\nRaw:\n%s", exc, raw[:500])
        return _error_review(file_path, f"Could not parse LLM response as JSON: {exc}")

    return data


def _validate_and_normalise_review(
    review: dict,
    file_path: str,
    actual_line_count: int,
) -> dict:
    """
    Validate and normalise the parsed review dict.

    - Ensures required keys exist.
    - Clamps line numbers to [1, actual_line_count].
    - Normalises severity values.
    - Caps issues at _MAX_ISSUES.
    - Injects `file` and aggregated counts.
    """
    # Ensure top-level keys
    review.setdefault("summary", "No summary provided.")
    review.setdefault("issues", [])
    review.setdefault("final_recommendation", "No recommendation provided.")

    # Normalise and validate issues
    validated_issues: list[dict] = []
    for i, issue in enumerate(review["issues"][:_MAX_ISSUES], start=1):
        if not isinstance(issue, dict):
            continue

        # Clamp line number
        raw_line = issue.get("line")
        if isinstance(raw_line, (int, float)) and raw_line is not None:
            clamped = max(1, min(int(raw_line), actual_line_count))
            issue["line"] = clamped
        else:
            issue["line"] = None  # null means "not pinpointable"

        # Normalise severity
        sev = str(issue.get("severity", "")).strip().capitalize()
        if sev not in _VALID_SEVERITIES:
            # Try to map common variants
            sev_lower = sev.lower()
            if "high" in sev_lower or "critical" in sev_lower or "severe" in sev_lower:
                sev = "High"
            elif "medium" in sev_lower or "moderate" in sev_lower or "warn" in sev_lower:
                sev = "Medium"
            else:
                sev = "Low"
        issue["severity"] = sev

        # Ensure required text fields
        issue.setdefault("title", f"Issue {i}")
        issue.setdefault("problem", "No description provided.")
        issue.setdefault("impact", "Unknown impact.")
        issue.setdefault("suggested_fix", "No suggestion provided.")
        issue["index"] = i

        validated_issues.append(issue)

    review["issues"] = validated_issues
    review["file"] = file_path
    review["issue_count"] = len(validated_issues)
    review["severity_counts"] = {
        "High":   sum(1 for iss in validated_issues if iss["severity"] == "High"),
        "Medium": sum(1 for iss in validated_issues if iss["severity"] == "Medium"),
        "Low":    sum(1 for iss in validated_issues if iss["severity"] == "Low"),
    }
    return review


def _format_review_as_markdown(review: dict) -> str:
    """
    Render the review dict as the structured markdown output format specified.

    This is what goes into draft_response / final_response.
    """
    lines: list[str] = []

    lines.append("# File Review Result\n")
    lines.append(f"**File:** `{review['file']}`\n")
    lines.append(f"**Summary:** {review['summary']}\n")

    sc = review.get("severity_counts", {})
    lines.append(
        f"**Issues Found:** {review['issue_count']} "
        f"(🔴 High: {sc.get('High', 0)}, "
        f"🟡 Medium: {sc.get('Medium', 0)}, "
        f"🟢 Low: {sc.get('Low', 0)})\n"
    )

    if review["issues"]:
        lines.append("---\n")
        lines.append("## Issues\n")
        for iss in review["issues"]:
            sev = iss["severity"]
            sev_icon = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(sev, "⚪")
            line_ref = f"Line {iss['line']}" if iss.get("line") else "General"

            lines.append(f"### {iss['index']}. {iss['title']}")
            lines.append(f"- **Location:** {line_ref}")
            lines.append(f"- **Severity:** {sev_icon} {sev}")
            lines.append(f"- **Problem:** {iss['problem']}")
            lines.append(f"- **Impact:** {iss['impact']}")
            lines.append(f"- **Suggested Fix:** {iss['suggested_fix']}\n")
    else:
        lines.append("\n✅ **No issues found.** The file looks clean.\n")

    lines.append("---\n")
    lines.append(f"## Final Recommendation\n\n{review['final_recommendation']}")

    return "\n".join(lines)


# ── Node ──────────────────────────────────────────────────────────────────────

async def file_review_agent_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: perform a structured code review of a single file.

    Reads from AgentState:
        selected_file — relative path inside the repo (set by the API layer)
        query         — reviewer's focus / instruction
        repo_id       — used to look up the local_path from DB

    Writes to AgentState:
        draft_response  — formatted markdown review
        draft_review    — structured dict (for programmatic access)
        final_response  — same as draft_response (set here; output_guardrail
                          will sanitise secrets afterward)
        selected_file   — echoed back (normalised POSIX path)
        error           — error message string or None
    """
    selected_file: str | None = state.get("selected_file")
    query: str = state.get("query", "Review this file for bugs and issues.").strip()
    repo_id: str = state.get("repo_id", "")

    # ── Validate inputs ───────────────────────────────────────────────────────
    if not selected_file:
        msg = "No file selected for review. Set selected_file in the request."
        return {
            "draft_response": msg,
            "draft_review": _error_review("", msg),
            "final_response": msg,
            "error": "missing_selected_file",
        }

    if not repo_id:
        msg = "No repository ID provided."
        return {
            "draft_response": msg,
            "draft_review": _error_review(selected_file, msg),
            "final_response": msg,
            "error": "missing_repo_id",
        }

    # ── Resolve repo local_path from DB ───────────────────────────────────────
    local_path: str | None = None
    try:
        from db.session import AsyncSessionLocal
        from db.crud import get_repository
        async with AsyncSessionLocal() as db:
            repo = await get_repository(db, repo_id)
            if repo is None:
                msg = f"Repository '{repo_id}' not found."
                return {
                    "draft_response": msg,
                    "draft_review": _error_review(selected_file, msg),
                    "final_response": msg,
                    "error": "repo_not_found",
                }
            local_path = repo.local_path
    except Exception as exc:
        logger.error("DB lookup failed in file_review_agent: %s", exc)
        msg = f"Database error: {exc}"
        return {
            "draft_response": msg,
            "draft_review": _error_review(selected_file, msg),
            "final_response": msg,
            "error": "db_error",
        }

    if not local_path:
        msg = (
            f"Repository '{repo_id}' has no local clone path. "
            "Please re-import the repository."
        )
        return {
            "draft_response": msg,
            "draft_review": _error_review(selected_file, msg),
            "final_response": msg,
            "error": "missing_local_path",
        }

    repo_root = Path(local_path).resolve()

    # ── Safety-check and resolve the file path ────────────────────────────────
    try:
        abs_path = _safe_resolve(repo_root, selected_file)
    except FileAccessError as exc:
        msg = str(exc)
        logger.warning("File access denied: %s (file=%r)", msg, selected_file)
        return {
            "draft_response": msg,
            "draft_review": _error_review(selected_file, msg),
            "final_response": msg,
            "error": "file_access_denied",
        }

    # Normalise to POSIX relative path
    rel_posix = abs_path.relative_to(repo_root).as_posix()
    language = SUPPORTED_EXTENSIONS.get(abs_path.suffix.lower(), "text")

    # ── Read file ─────────────────────────────────────────────────────────────
    try:
        content, line_count = _read_file(abs_path)
    except FileAccessError as exc:
        msg = str(exc)
        return {
            "draft_response": msg,
            "draft_review": _error_review(rel_posix, msg),
            "final_response": msg,
            "error": "file_read_error",
        }

    logger.info(
        "Reviewing file: %s (%s, %d lines, %d chars)",
        rel_posix, language, line_count, len(content),
    )

    # ── Call LLM ──────────────────────────────────────────────────────────────
    raw_review = await _call_review_llm(rel_posix, language, content, query)

    # ── Validate and normalise ────────────────────────────────────────────────
    review = _validate_and_normalise_review(raw_review, rel_posix, line_count)

    # ── Format as markdown ────────────────────────────────────────────────────
    draft_response = _format_review_as_markdown(review)

    return {
        "draft_response":  draft_response,
        "draft_review":    review,
        "final_response":  draft_response,   # output_guardrail refines this
        "selected_file":   rel_posix,
        "related_files":   [rel_posix],
        "retrieved_chunks": [],              # file review doesn't use Qdrant
        "error":           review.get("_error"),
    }
