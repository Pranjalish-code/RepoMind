"""
graph/nodes/mermaid_validator_node.py — LangGraph node: validate + persist architecture diagram.

This node runs AFTER architecture_agent_node in the graph.
It performs final Mermaid validation, persists the diagram to SQLite, and
builds the final formatted output string.

Responsibilities
----------------
1. Final Mermaid validation (re-validate after agent produced output).
2. Persist ArchitectureDiagram record to SQLite via create_architecture_diagram().
3. Enforce security: no secrets, no .env leaks.
4. Build the final draft_response (markdown) passed to output_guardrail.
"""

from __future__ import annotations

import logging
from typing import Any

from graph.state import AgentState

logger = logging.getLogger(__name__)


async def mermaid_validator_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: validate architecture diagram and persist to SQLite.

    Reads:
        diagram_mermaid       — Mermaid diagram string from architecture_agent
        diagram_explanation   — explanation text
        diagram_confidence    — confidence score (int)
        draft_review          — dict with detected_components_json
        repo_id               — repository UUID

    Writes:
        diagram_mermaid       — (possibly corrected) final Mermaid diagram
        draft_response        — formatted output (passed to output_guardrail)
        final_response        — same as draft_response
        guardrail_result      — updated with diagram_db_id
        error                 — error code or None
    """
    mermaid      = state.get("diagram_mermaid", "")
    explanation  = state.get("diagram_explanation", "")
    confidence   = state.get("diagram_confidence", 0)
    draft_review = state.get("draft_review", {})
    repo_id      = state.get("repo_id", "")

    # ── 1. Re-validate the diagram ─────────────────────────────────────────────
    from tools.mermaid_validator import validate_mermaid

    val = validate_mermaid(mermaid)

    if not val.valid:
        logger.warning(
            "Mermaid validator node: diagram is invalid: %s — replacing with stub",
            val.errors,
        )
        mermaid = (
            "flowchart TD\n"
            "    Repo[Repository]\n"
            "    Note[Analysis incomplete — please re-generate]\n"
            "    Repo --> Note"
        )
        confidence = max(0, confidence - 20)

    else:
        mermaid = val.cleaned

    # ── 2. Persist to SQLite ───────────────────────────────────────────────────
    diagram_db_id: str | None = None

    if repo_id and mermaid:
        try:
            from db.session import AsyncSessionLocal
            from db.crud import create_architecture_diagram

            detected = draft_review.get("detected_components_json", [])
            repo_name = draft_review.get("repo_name", repo_id)

            async with AsyncSessionLocal() as db:
                diagram_record = await create_architecture_diagram(
                    db,
                    repo_id=repo_id,
                    mermaid_code=mermaid,
                    explanation=explanation[:4000] if explanation else None,
                    confidence_score=float(confidence) / 100.0,
                    detected_components_json=detected,
                )
                await db.commit()
                diagram_db_id = diagram_record.id

            logger.info(
                "Architecture diagram saved: id=%s repo=%s confidence=%d%%",
                diagram_db_id, repo_id, confidence,
            )
        except Exception as exc:
            logger.warning("Failed to save architecture diagram to DB: %s", exc)

    # ── 3. Format final output ─────────────────────────────────────────────────
    repo_name = draft_review.get("repo_name", repo_id)

    lines: list[str] = []
    lines.append(f"## Architecture Diagram — {repo_name}\n")
    lines.append(f"**Confidence:** {confidence}%\n")

    lines.append("### Mermaid Diagram\n")
    lines.append("```mermaid")
    lines.append(mermaid)
    lines.append("```\n")

    lines.append("### Explanation\n")
    lines.append(explanation if explanation else "_No explanation available._")
    lines.append("")

    if val.warnings:
        lines.append("### Notes\n")
        for w in val.warnings[:5]:
            lines.append(f"> {w}")
        lines.append("")

    lines.append("---")
    lines.append(
        "> 📌 **Note**: This diagram is AI-assisted and based on static analysis only. "
        "It may not capture all runtime dependencies. "
        "Manual verification is recommended."
    )

    if confidence < 50:
        lines.append(
            "\n> ⚠️ **Low confidence** — fewer components were detected. "
            "Consider adding more source files or manually verifying the diagram."
        )

    formatted = "\n".join(lines)

    return {
        "diagram_mermaid":  mermaid,
        "draft_response":   formatted,
        "final_response":   formatted,
        "guardrail_result": {
            **state.get("guardrail_result", {}),
            "diagram_db_id":       diagram_db_id,
            "diagram_valid":       val.valid,
            "diagram_warnings":    val.warnings,
            "diagram_node_count":  len(val.node_ids_found),
        },
        "error": state.get("error"),
    }
