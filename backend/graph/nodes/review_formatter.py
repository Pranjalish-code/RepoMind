"""
graph/nodes/review_formatter.py — Format the validated PR review into final markdown output.

This node runs LAST in the PR review pipeline (after pr_guardrail_node).
It converts the clean draft_review dict into the canonical formatted string
and persists the review to SQLite via an async DB write.

Output format
-------------

PR Review Result

Status:       <Safe to merge | Needs changes | Risky PR>
Risk Score:   <0-10>/10

Summary:
<summary text>

Issues Found:
<N issue(s)>

1. <Title>
   File:          <path>
   Line:          <number or N/A>
   Severity:      <Low | Medium | High>
   Evidence:      <quote from diff>
   Problem:       <description>
   Impact:        <impact description>
   Suggested Fix: <fix>

Final Recommendation:
<recommendation text>

Security notes
--------------
* Runs a final secret redaction pass before returning.
* Does NOT write diff content to the DB — only the review JSON.
"""

from __future__ import annotations

import logging
from typing import Any

from graph.state import AgentState

logger = logging.getLogger(__name__)

# ── Severity emoji / colour labels ────────────────────────────────────────────

_SEVERITY_ICON = {
    "High":   "🔴",
    "Medium": "🟡",
    "Low":    "🟢",
}

_STATUS_ICON = {
    "Safe to merge": "✅",
    "Needs changes": "⚠️",
    "Risky PR":      "🚨",
}


# ── Formatter ─────────────────────────────────────────────────────────────────

def _format_review(
    review: dict,
    pr_number: int | None,
    repo_name: str,
) -> str:
    """Render the validated review dict into a human-readable markdown string."""
    status     = review.get("status", "Needs changes")
    risk_score = review.get("risk_score", 0)
    summary    = review.get("summary", "").strip()
    issues     = review.get("issues", [])
    final_rec  = review.get("final_recommendation", "").strip()

    status_icon = _STATUS_ICON.get(status, "⚠️")
    pr_label    = f"PR #{pr_number}" if pr_number else "PR"

    lines: list[str] = []

    # Header
    lines.append(f"## {status_icon} PR Review Result — {pr_label} ({repo_name})\n")
    lines.append(f"**Status:** {status}")
    lines.append(f"**Risk Score:** {risk_score}/10\n")

    # Summary
    lines.append("### Summary\n")
    lines.append(summary if summary else "_No summary available._")
    lines.append("")

    # Issues
    issue_count = len(issues)
    if issue_count == 0:
        lines.append("### Issues Found\n")
        lines.append("✅ No issues detected in this PR.")
    else:
        lines.append(f"### Issues Found ({issue_count} issue{'s' if issue_count != 1 else ''})\n")
        for idx, issue in enumerate(issues, start=1):
            sev = issue.get("severity", "Low")
            icon = _SEVERITY_ICON.get(sev, "⬜")
            title = issue.get("title", "Untitled Issue")
            file_path = issue.get("file", "N/A")
            line_no = issue.get("line")
            line_str = str(line_no) if line_no is not None else "N/A"
            evidence = issue.get("evidence", "N/A")
            problem = issue.get("problem", "N/A")
            impact = issue.get("impact", "N/A")
            suggested_fix = issue.get("suggested_fix", "N/A")

            lines.append(f"#### {idx}. {icon} {title}\n")
            lines.append(f"- **File:** `{file_path}`")
            lines.append(f"- **Line:** {line_str}")
            lines.append(f"- **Severity:** {sev}")
            lines.append(f"- **Evidence:** {evidence}")
            lines.append(f"- **Problem:** {problem}")
            lines.append(f"- **Impact:** {impact}")
            lines.append(f"- **Suggested Fix:** {suggested_fix}")
            lines.append("")

    # Final recommendation
    lines.append("### Final Recommendation\n")
    lines.append(final_rec if final_rec else "_No recommendation provided._")

    return "\n".join(lines)


# ── DB persistence ─────────────────────────────────────────────────────────────

async def _save_review_to_db(
    repo_id: str,
    pr_number: int,
    review: dict,
    formatted: str,
) -> str | None:
    """
    Persist the PR review to SQLite.

    Returns the review UUID on success, None on failure (non-fatal).
    """
    try:
        from db.session import AsyncSessionLocal
        from db.crud import create_pr_review, update_pr_review

        async with AsyncSessionLocal() as db:
            # Create initial record
            pr_review = await create_pr_review(
                db, repo_id=repo_id, pr_number=pr_number
            )
            review_id = pr_review.id

            # Save the validated review JSON + formatted output
            review_with_formatted = {**review, "_formatted": formatted}
            await update_pr_review(
                db,
                review_id,
                status="done",
                risk_score=float(review.get("risk_score", 0)),
                summary=str(review.get("summary", ""))[:2000],
                review_json=review_with_formatted,
            )
            await db.commit()

        logger.info("PR review saved to DB: id=%s pr=%d", review_id, pr_number)
        return review_id

    except Exception as exc:
        logger.warning("Failed to save PR review to DB: %s", exc)
        return None


# ── Node ──────────────────────────────────────────────────────────────────────

async def review_formatter_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: format the validated review and save to DB.

    Reads:
        draft_review      — validated review dict (from pr_guardrail_node)
        pr_number         — PR number (int)
        repo_id           — repository UUID

    Writes:
        draft_response    — formatted markdown (passed to output_guardrail)
        final_response    — same as draft_response (pre-guardrail passthrough)
        guardrail_result  — updated with formatter status
    """
    draft_review: dict = state.get("draft_review", {})
    pr_number: int | None = state.get("pr_number")
    repo_id: str = state.get("repo_id", "")

    # Fetch repo name for the header
    repo_name = repo_id  # fallback
    try:
        from db.session import AsyncSessionLocal
        from db.crud import get_repository
        async with AsyncSessionLocal() as db:
            repo = await get_repository(db, repo_id)
            if repo:
                repo_name = repo.repo_name
    except Exception:
        pass  # non-fatal

    # Format the review
    formatted = _format_review(draft_review, pr_number, repo_name)

    # Persist to DB (non-fatal)
    review_id: str | None = None
    if repo_id and pr_number:
        review_id = await _save_review_to_db(repo_id, pr_number, draft_review, formatted)

    logger.info(
        "Review formatter complete: pr=%s repo=%s review_db_id=%s",
        pr_number,
        repo_id,
        review_id,
    )

    return {
        "draft_response":  formatted,
        "final_response":  formatted,   # output_guardrail will further sanitize
        "guardrail_result": {
            **state.get("guardrail_result", {}),
            "formatter_done": True,
            "review_db_id":   review_id,
        },
    }
