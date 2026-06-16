"""
graph/state.py — Shared TypedDict state for the RepoMind LangGraph agent.

Every node receives this dict and returns a *partial* dict with only the
fields it actually modifies.  LangGraph merges the partial update into the
running state automatically.

Design decisions
----------------
* ``total=False`` lets nodes return partial dicts without mypy complaints.
* All list fields default to empty list – callers can always iterate safely.
* Guardrail fields are dicts so the nodes can embed reason strings.
"""

from __future__ import annotations

from typing import Literal
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    """Top-level state flowing through the RepoMind LangGraph agent."""

    # ── Request identity ──────────────────────────────────────────────────────
    user_id: str          # optional; empty string when unauthenticated
    repo_id: str          # repository UUID (required)
    query: str            # raw user question

    # ── Routing / classification ──────────────────────────────────────────────
    intent: Literal[
        "repo_qa",
        "file_review",
        "pr_review",
        "architecture",
        "off_topic",
    ]

    # ── Optional context for specialised agents ───────────────────────────────
    selected_file: str | None   # set by file_review intent
    pr_number: int | None       # set by pr_review intent

    # ── Repository metadata (populated by QA agent) ───────────────────────────
    indexed_files: list[str]        # list of file paths in the index
    changed_files: list[dict]       # PR diff items (pr_review path)

    # ── RAG retrieval ─────────────────────────────────────────────────────────
    retrieved_chunks: list[dict]    # raw SearchResult payloads from Qdrant
    related_files: list[str]        # deduplicated file paths from chunks

    # ── Generation outputs ────────────────────────────────────────────────────
    draft_response: str             # LLM answer before output guardrails
    draft_review: dict              # structured PR review before guardrails
    final_response: str             # cleaned / redacted final text

    # ── Architecture diagram (architecture intent) ────────────────────────────
    diagram_mermaid: str
    diagram_explanation: str
    diagram_confidence: int         # 0-100

    # ── Guardrail metadata ────────────────────────────────────────────────────
    guardrail_result: dict          # {"passed": bool, "reason": str, ...}

    # ── Error channel ─────────────────────────────────────────────────────────
    error: str | None
