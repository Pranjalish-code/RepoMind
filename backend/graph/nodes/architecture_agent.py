"""
graph/nodes/architecture_agent.py — Architecture diagram generator node for RepoMind AI.

Flow
----
1. Read repo_id from AgentState.
2. Look up local_path from DB.
3. Run CodeAnalyzer (static analysis) in a thread.
4. Build component graph description (no LLM yet).
5. Call LLM — pass ONLY detected components; instruct it to render Mermaid.
6. Extract and validate Mermaid output.
7. If validation fails → fallback to static diagram (no hallucinations).
8. Return draft_review dict + draft_response (markdown).

LLM rules
---------
* LLM must ONLY use components from the analysis result.
* LLM must NOT invent components.
* Diagram must start with 'flowchart TD'.
* LLM receives 2 retry chances if validation fails.

Security
--------
* .env files are never passed to the LLM.
* Repository local_path is validated against DB record.
* Secrets/credentials in diagram text are blocked by mermaid_validator.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from graph.state import AgentState

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are RepoMind AI — an expert software architect.

You will receive a structured description of DETECTED components from static analysis.
Your ONLY job is to convert this into a Mermaid.js flowchart diagram.

STRICT RULES
------------
1. The diagram MUST start with exactly: flowchart TD
2. You MUST ONLY use the component names listed in 'Detected Components'.
   Do NOT invent new nodes. Do NOT add components that were not detected.
3. You MUST ONLY use the edges listed in 'Detected Edges'.
   Do NOT invent new connections.
4. Node labels MUST be wrapped in double quotes to prevent parse errors (e.g. NodeID["Label Name"]).
5. Do NOT include API keys, passwords, secrets, or .env values.
6. If there are no detected edges, draw nodes only with no arrows.
7. Return ONLY the Mermaid diagram — no prose, no explanations, no markdown fence.

Example valid output:
flowchart TD
    User(["User"])
    Frontend["Frontend React"]
    BackendAPI["Backend API FastAPI"]
    Database[("SQLite")]
    User --> Frontend
    Frontend --> BackendAPI
    BackendAPI --> Database
"""

_MAX_RETRIES = 2


# ── LLM call ──────────────────────────────────────────────────────────────────

async def _call_llm(prompt: str) -> str | None:
    """
    Call the LLM to generate a Mermaid diagram.

    Returns raw LLM text, or None if no LLM is configured.
    """
    try:
        from config import settings
        from langchain_core.messages import HumanMessage, SystemMessage

        if settings.gemini_api_key.strip():
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                temperature=0.1,
                google_api_key=settings.gemini_api_key,
                max_output_tokens=2048,
            )
        elif settings.openai_api_key.strip():
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.1,
                openai_api_key=settings.openai_api_key,
                max_tokens=2048,
            )
        else:
            logger.warning("No LLM key configured — using fallback diagram only")
            return None

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        response = await llm.ainvoke(messages)
        return response.content.strip()

    except Exception as exc:
        logger.warning("LLM call failed in architecture_agent: %s", exc)
        return None


# ── Node ──────────────────────────────────────────────────────────────────────

async def architecture_agent_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: generate an architecture diagram for a repository.

    Reads:
        repo_id   — repository UUID
        query     — user question (may influence explanation)

    Writes:
        diagram_mermaid      — validated Mermaid diagram string
        diagram_explanation  — bullet-point explanation
        diagram_confidence   — 0-100 confidence integer
        draft_response       — formatted markdown output (for output_guardrail)
        final_response       — same as draft_response (pre-guardrail)
        draft_review         — dict with diagram metadata
        error                — error code or None
    """
    repo_id: str = state.get("repo_id", "")

    def _error(msg: str, code: str) -> dict:
        return {
            "draft_response": msg,
            "final_response": msg,
            "draft_review": {"summary": msg},
            "diagram_mermaid": "",
            "diagram_explanation": "",
            "diagram_confidence": 0,
            "error": code,
        }

    # ── 1. Validate repo_id ────────────────────────────────────────────────────
    if not repo_id:
        return _error("No repository ID provided.", "missing_repo_id")

    # ── 2. Fetch local_path from DB ───────────────────────────────────────────
    try:
        from db.session import AsyncSessionLocal
        from db.crud import get_repository
        async with AsyncSessionLocal() as db:
            repo = await get_repository(db, repo_id)
            if repo is None:
                return _error(f"Repository '{repo_id}' not found.", "repo_not_found")
            local_path = repo.local_path
            repo_name  = repo.repo_name
    except Exception as exc:
        logger.error("DB lookup failed in architecture_agent: %s", exc)
        return _error(f"Database error: {exc}", "db_error")

    if not local_path:
        return _error(
            "Repository has no local clone. Please re-import the repository.",
            "missing_local_path",
        )

    # ── 3. Static analysis ────────────────────────────────────────────────────
    try:
        from tools.code_analyzer import CodeAnalyzer
        analysis = await asyncio.to_thread(
            lambda: CodeAnalyzer(local_path).analyze()
        )
    except Exception as exc:
        logger.error("Code analysis failed for repo %s: %s", repo_id, exc)
        return _error(f"Code analysis failed: {exc}", "analysis_error")

    confidence = analysis.confidence
    logger.info(
        "Architecture analysis done: %d components, %d edges, confidence=%d%%",
        len(analysis.components),
        len(analysis.edges),
        confidence,
    )

    # ── 4. Build prompt ────────────────────────────────────────────────────────
    from tools.diagram_generator import (
        build_component_graph_description,
        build_fallback_diagram,
        build_explanation,
    )
    from tools.mermaid_validator import validate_mermaid, extract_mermaid_from_llm_response

    graph_desc = build_component_graph_description(analysis)
    allowed_ids = {c.name for c in analysis.components}

    prompt = (
        f"Repository: **{repo_name}**\n\n"
        f"{graph_desc}\n\n"
        "Convert the above detected components and edges into a Mermaid flowchart diagram.\n"
        "Use only the component names and edges listed above.\n"
        "Do NOT add any components that were not listed."
    )

    # ── 5. LLM call with retries ───────────────────────────────────────────────
    mermaid_diagram = ""
    validation_warnings: list[str] = []

    for attempt in range(_MAX_RETRIES + 1):
        raw_llm = await _call_llm(prompt)

        if raw_llm is None:
            # No LLM key — use fallback
            logger.info("No LLM available — using fallback diagram")
            mermaid_diagram = build_fallback_diagram(analysis)
            break

        # Extract diagram from response
        extracted = extract_mermaid_from_llm_response(raw_llm)
        val = validate_mermaid(extracted, allowed_node_ids=allowed_ids)

        if val.valid:
            mermaid_diagram = val.cleaned
            validation_warnings = val.warnings
            logger.info(
                "LLM diagram validated on attempt %d (nodes=%d, edges=%d)",
                attempt + 1, len(val.node_ids_found), val.edge_count,
            )
            break
        else:
            logger.warning(
                "LLM diagram validation failed (attempt %d): %s",
                attempt + 1, val.errors,
            )
            if attempt < _MAX_RETRIES:
                # Retry with error feedback
                prompt += (
                    f"\n\nYour previous response had errors:\n"
                    + "\n".join(f"- {e}" for e in val.errors)
                    + "\nPlease fix and return ONLY the corrected Mermaid diagram."
                )
            else:
                # All retries exhausted — fallback
                logger.warning("All LLM retries failed — using fallback diagram")
                mermaid_diagram = build_fallback_diagram(analysis)

    # ── 6. Final validation of chosen diagram ─────────────────────────────────
    final_val = validate_mermaid(mermaid_diagram, allowed_node_ids=allowed_ids)
    if not final_val.valid:
        logger.error("Even fallback diagram is invalid: %s", final_val.errors)
        mermaid_diagram = "flowchart TD\n    Repo[Repository — analysis incomplete]"

    # ── 7. Build explanation and formatted output ─────────────────────────────
    explanation = build_explanation(analysis, confidence)

    # Detected components list for DB storage
    detected_components = [
        {
            "name":     c.name,
            "kind":     c.kind,
            "label":    c.label,
            "evidence": c.evidence[:3],
        }
        for c in analysis.components
    ]

    # Format the final response
    formatted = _format_architecture_response(
        repo_name=repo_name,
        mermaid=mermaid_diagram,
        explanation=explanation,
        confidence=confidence,
        warnings=validation_warnings,
    )

    return {
        "diagram_mermaid":      mermaid_diagram,
        "diagram_explanation":  explanation,
        "diagram_confidence":   confidence,
        "draft_response":       formatted,
        "final_response":       formatted,
        "draft_review": {
            "mermaid_code":             mermaid_diagram,
            "explanation":              explanation,
            "confidence_score":         confidence,
            "detected_components_json": detected_components,
            "repo_name":                repo_name,
        },
        "error": None,
    }


# ── Formatter ─────────────────────────────────────────────────────────────────

def _format_architecture_response(
    repo_name: str,
    mermaid: str,
    explanation: str,
    confidence: int,
    warnings: list[str],
) -> str:
    """Render the architecture response as a formatted markdown string."""
    lines: list[str] = []

    lines.append(f"## Architecture Diagram — {repo_name}\n")
    lines.append(f"**Confidence:** {confidence}%\n")

    lines.append("### Mermaid Diagram\n")
    lines.append("```mermaid")
    lines.append(mermaid)
    lines.append("```\n")

    lines.append("### Explanation\n")
    lines.append(explanation)
    lines.append("")

    if warnings:
        lines.append("### Validator Notes\n")
        for w in warnings:
            lines.append(f"> ℹ️ {w}")
        lines.append("")

    lines.append("---")
    lines.append(
        "> 📌 **Note**: This diagram is AI-assisted and based on static analysis only. "
        "It may not capture runtime dependencies or dynamic behavior. "
        "Manual verification is recommended."
    )

    return "\n".join(lines)
