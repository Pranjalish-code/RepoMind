"""
graph/nodes/off_topic.py — Off-topic rejection node for RepoMind AI.

Called when the classifier detects an intent that is not codebase-related.
Returns a friendly, deterministic response (no LLM call) directing the user
back to valid query types.
"""

from __future__ import annotations

import logging
from typing import Any

from graph.state import AgentState

logger = logging.getLogger(__name__)

_OFF_TOPIC_RESPONSE = (
    "I'm RepoMind AI — a codebase assistant. I can help you with:\n\n"
    "- 🔍 **Codebase Q&A** — Ask anything about functions, classes, or logic "
    "in your indexed repository.\n"
    "- 📄 **File Review** — Request an analysis or explanation of a specific file.\n"
    "- 🔀 **PR Review** — Ask about a specific pull request (e.g., \"Review PR #42\").\n"
    "- 🗺️ **Architecture** — Request a Mermaid diagram of the repository structure.\n\n"
    "Your question doesn't appear to be related to a codebase. "
    "Please try one of the topics above!"
)


async def off_topic_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: respond to off-topic queries without calling the LLM.

    Reads:   query (for logging), intent
    Writes:  draft_response, final_response
    """
    query: str = state.get("query", "")
    logger.info("Off-topic query detected: %r", query[:120])

    return {
        "draft_response": _OFF_TOPIC_RESPONSE,
        "final_response": _OFF_TOPIC_RESPONSE,
        "retrieved_chunks": [],
        "related_files": [],
        "error": None,
    }
