"""
graph/nodes/input_guardrails.py — Input safety checks for RepoMind AI.

Blocks:
  - Requests for secrets / API keys / tokens
  - .env file access attempts
  - Prompt injection patterns ("ignore previous instructions", etc.)
  - Queries that are clearly empty or malformed

Returns a guardrail_result dict and, when blocked, sets final_response
immediately so the router can short-circuit to END.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from graph.state import AgentState

logger = logging.getLogger(__name__)

# ── Compiled pattern sets ──────────────────────────────────────────────────────

# Patterns that suggest the user is trying to extract secrets
_SECRET_PATTERNS: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in [
    r"\.env",
    r"api[_\s-]?key",
    r"secret[_\s-]?key",
    r"access[_\s-]?token",
    r"auth[_\s-]?token",
    r"bearer[_\s-]?token",
    r"github[_\s-]?token",
    r"openai[_\s-]?key",
    r"gemini[_\s-]?key",
    r"private[_\s-]?key",
    r"password",
    r"credential",
    r"show.*secret",
    r"reveal.*key",
    r"print.*token",
    r"display.*password",
]]

# Prompt injection patterns
_INJECTION_PATTERNS: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in [
    r"ignore\s+(previous|prior|all|above)\s+instructions?",
    r"disregard\s+(previous|prior|all|above)\s+instructions?",
    r"forget\s+(previous|prior|all|above)\s+instructions?",
    r"you\s+are\s+now\s+(a\s+)?(different|new|another)\s+(ai|model|assistant|bot)",
    r"act\s+as\s+(a\s+)?(different|new|another|unrestricted)\s+(ai|model|assistant)",
    r"pretend\s+(you\s+are|to\s+be)\s+(a\s+)?(different|another|unrestricted)",
    r"override\s+(your\s+)?(system|core|base)\s+(prompt|instruction|programming)",
    r"jailbreak",
    r"DAN\s+mode",
    r"do\s+anything\s+now",
    r"your\s+(real|true|actual)\s+(instructions?|prompt|system\s+prompt)",
    r"show\s+(me\s+)?(your\s+)?(system\s+prompt|instructions?|prompt)",
    r"reveal\s+(your\s+)?(system\s+prompt|instructions?|prompt)",
    r"what\s+(are|is)\s+your\s+(system\s+prompt|instructions?)",
]]

# Minimum / maximum query lengths
_MIN_QUERY_LEN = 3
_MAX_QUERY_LEN = 4000


def _check_secrets(query: str) -> str | None:
    """Return a reason string if the query appears to request secrets, else None."""
    for pat in _SECRET_PATTERNS:
        if pat.search(query):
            return (
                "Your query appears to request sensitive credentials or secrets. "
                "RepoMind cannot share API keys, tokens, passwords, or .env file contents."
            )
    return None


def _check_injection(query: str) -> str | None:
    """Return a reason string if prompt injection is detected, else None."""
    for pat in _INJECTION_PATTERNS:
        if pat.search(query):
            return (
                "Your query contains patterns that look like prompt injection. "
                "RepoMind only answers questions about codebases."
            )
    return None


def _check_length(query: str) -> str | None:
    """Return a reason string if the query is too short or too long."""
    stripped = query.strip()
    if len(stripped) < _MIN_QUERY_LEN:
        return "Query is too short. Please ask a complete question about the codebase."
    if len(stripped) > _MAX_QUERY_LEN:
        return f"Query exceeds the maximum allowed length of {_MAX_QUERY_LEN} characters."
    return None


# ── Node ──────────────────────────────────────────────────────────────────────

async def input_guardrail_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: validate the incoming query before any LLM call.

    Returns a partial state dict.  If blocked, sets:
      - guardrail_result["passed"] = False
      - final_response  (the rejection message shown to the user)

    If safe, sets:
      - guardrail_result["passed"] = True
    """
    query: str = state.get("query", "").strip()

    # Run all checks in order (first failure wins)
    for check_fn in (_check_length, _check_secrets, _check_injection):
        reason = check_fn(query)
        if reason:
            logger.warning(
                "Input guardrail blocked query (reason=%r, query_prefix=%r)",
                reason[:60],
                query[:80],
            )
            return {
                "guardrail_result": {
                    "passed": False,
                    "stage": "input",
                    "reason": reason,
                },
                "final_response": reason,
                "error": None,
            }

    logger.debug("Input guardrail passed for query: %r", query[:80])
    return {
        "guardrail_result": {
            "passed": True,
            "stage": "input",
            "reason": None,
        },
    }
