"""
graph/nodes/classifier.py — Intent classification node for RepoMind AI.

Uses an LLM (or fast heuristics first, LLM second) to classify the user
query into one of the defined intent labels.

Intent labels
-------------
  repo_qa       — General codebase question (default fallback)
  file_review   — User references a specific file and asks for review
  pr_review     — User asks about a pull request
  architecture  — User wants a diagram or architectural overview
  off_topic     — Completely unrelated to the codebase

Heuristics run first (no LLM cost).  If heuristics are confident, we skip
the LLM call.  Otherwise we call the LLM with a structured prompt.

Design decisions
----------------
* We avoid JSON-mode because older models may not support it; we parse the
  first line of the LLM reply instead.
* The node is resilient: any LLM error falls back to repo_qa rather than
  failing the whole graph.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Literal

from graph.state import AgentState

logger = logging.getLogger(__name__)

Intent = Literal["repo_qa", "file_review", "pr_review", "architecture", "off_topic"]

# ── Heuristic keyword sets ────────────────────────────────────────────────────

_OFF_TOPIC_PATTERNS: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in [
    r"\bweather\b",
    r"\brecipe\b",
    r"\bcooking\b",
    r"\bsports?\b",
    r"\bfootball\b",
    r"\bbasketball\b",
    r"\bcricket\b",
    r"\bpolitics\b",
    r"\bnews\b",
    r"\bstock\s+market\b",
    r"\bbitcoin\b",
    r"\bcryptocurrenc",
    r"\bpoem\b",
    r"\bwrite\s+(me\s+)?(a\s+)?(story|joke|poem|essay|song)\b",
    r"\btranslate\s+(this\s+)?(sentence|text|word)\b",
]]

_PR_PATTERNS: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in [
    r"\bpr\s*#?\d+\b",
    r"\bpull\s+request\b",
    r"\bpull\s*request\s*#?\d+\b",
    r"\bpr\s+review\b",
    r"\breview\s+(the\s+)?pull\s+request\b",
    r"\bchanges?\s+in\s+(this\s+)?pr\b",
    r"\bdiff\b.*\bpr\b",
]]

_ARCH_PATTERNS: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in [
    r"\barchitecture\b",
    r"\bdiagram\b",
    r"\bmermaid\b",
    r"\bsystem\s+design\b",
    r"\bhigh[- ]level\s+overview\b",
    r"\bcomponents?\s+diagram\b",
    r"\bdependenc(y|ies)\s+(graph|diagram|map)\b",
    r"\bvisualiz(e|ation)\b.*\bcodebase\b",
    r"\bhow\s+(does|do)\s+(the\s+)?system\s+(work|fit|connect)\b",
]]

_FILE_REVIEW_PATTERNS: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in [
    r"\breview\s+.+\.(py|ts|js|go|java|rs|cpp|c|cs|rb|kt)\b",
    r"\baudit\s+.+\.(py|ts|js|go|java|rs|cpp|c|cs|rb|kt)\b",
    r"\bwhat('s|\s+is)\s+wrong\s+with\s+.+\.(py|ts|js|go|java|rs|cpp|c|cs|rb|kt)\b",
    r"\bcheck\s+.+\.(py|ts|js|go|java|rs|cpp|c|cs|rb|kt)\b",
]]
# Extract PR number from query
_PR_NUMBER_RE = re.compile(r"(?:pr|pull\s*request)\s*#?(\d+)", re.IGNORECASE)


def _extract_pr_number(query: str) -> int | None:
    m = _PR_NUMBER_RE.search(query)
    return int(m.group(1)) if m else None


def _heuristic_classify(query: str) -> Intent | None:
    """
    Fast heuristic classification.

    Returns an Intent if confident, or None to defer to LLM.
    """
    if any(p.search(query) for p in _OFF_TOPIC_PATTERNS):
        return "off_topic"
    if any(p.search(query) for p in _PR_PATTERNS):
        return "pr_review"
    if any(p.search(query) for p in _ARCH_PATTERNS):
        return "architecture"
    if any(p.search(query) for p in _FILE_REVIEW_PATTERNS):
        return "file_review"
    return None


async def _llm_classify(query: str, repo_id: str) -> Intent:
    """
    Ask the LLM to classify the intent.

    Falls back to 'repo_qa' on any error.
    """
    try:
        from config import settings
        from langchain_core.messages import HumanMessage, SystemMessage

        system_prompt = (
            "You are an intent classifier for RepoMind AI, a codebase Q&A tool.\n"
            "Classify the user query into exactly ONE of these intents:\n"
            "  repo_qa      — A general question about the codebase\n"
            "  file_review  — User wants a specific file reviewed or explained\n"
            "  pr_review    — User asks about a pull request\n"
            "  architecture — User wants a diagram or architectural overview\n"
            "  off_topic    — Completely unrelated to software or codebases\n\n"
            "Respond with ONLY the intent label on the first line. No explanation."
        )
        user_message = f"Repository ID: {repo_id}\nQuery: {query}"

        # Choose LLM based on available keys (fast model preferred for classifier)
        if settings.gemini_api_key.strip():
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                temperature=0,
                google_api_key=settings.gemini_api_key,
                max_output_tokens=20,
            )
        elif settings.openai_api_key.strip():
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0,
                openai_api_key=settings.openai_api_key,
                max_tokens=20,
            )
        else:
            logger.warning("No LLM key available; defaulting intent to repo_qa")
            return "repo_qa"

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        response = await asyncio.to_thread(llm.invoke, messages)
        raw = response.content.strip().lower().splitlines()[0].strip()

        valid_intents: set[str] = {
            "repo_qa", "file_review", "pr_review", "architecture", "off_topic"
        }
        if raw in valid_intents:
            return raw  # type: ignore[return-value]

        logger.warning("LLM returned unexpected intent %r; defaulting to repo_qa", raw)
        return "repo_qa"

    except Exception as exc:
        logger.error("LLM classifier error: %s — defaulting to repo_qa", exc)
        return "repo_qa"


# ── Node ──────────────────────────────────────────────────────────────────────

async def intent_classifier_node(state: AgentState) -> dict[str, Any]:
    query: str = state.get("query", "").strip()
    repo_id: str = state.get("repo_id", "")
    selected_file = state.get("selected_file")

    if selected_file:
        return {
            "intent": "file_review",
            "selected_file": selected_file,
            "pr_number": None,
        }

    intent = _heuristic_classify(query)

    if intent == "file_review":
        intent = "repo_qa"

    if intent is None:
        logger.debug("Heuristics inconclusive; calling LLM classifier")
        intent = await _llm_classify(query, repo_id)

    if intent == "file_review":
        intent = "repo_qa"

    logger.info("Classified query as intent=%r (query=%r)", intent, query[:80])

    update: dict[str, Any] = {
        "intent": intent,
        "pr_number": None,
        "selected_file": selected_file,
    }

    if intent == "pr_review":
        update["pr_number"] = _extract_pr_number(query)

    return update