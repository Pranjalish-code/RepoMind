"""
graph/graph.py — LangGraph compiled agent graph for RepoMind AI.

Graph topology (Step 4 + Step 5 + Step 6 + Step 7)
----------------------------------------------------

    input_guardrail_node
           │
           ▼
    intent_classifier_node
           │
           ▼
        [router] ─── repo_qa      ──► qa_agent_node               ─┐
                 ─── file_review  ──► file_review_agent_node       ─┤
                 ─── pr_review    ──► pr_review_agent_node          │
                 │                        │                          │─► output_guardrail ──► END
                 │               pr_guardrail_node                   │
                 │                        │                          │
                 │               review_formatter_node ──────────────┤
                 ─── architecture ──► architecture_agent_node        │
                 │                        │                          │
                 │               mermaid_validator_node ─────────────┤
                 └── off_topic    ──► off_topic_node ────────────────┘

Router logic
------------
* If input guardrail blocked (guardrail_result.passed == False) → skip to END.
* If API already sets a valid intent, bypass classifier and route directly.
* Otherwise route through classifier.
* pr_review goes through 3-node pipeline.
* architecture goes through 2-node pipeline.

The graph is compiled once at import time and reused across requests.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import StateGraph, END

from graph.state import AgentState
from graph.nodes.input_guardrails import input_guardrail_node
from graph.nodes.classifier import intent_classifier_node
from graph.nodes.qa_agent import qa_agent_node
from graph.nodes.file_review_agent import file_review_agent_node
from graph.nodes.pr_review_agent import pr_review_agent_node
from graph.nodes.pr_guardrails import pr_guardrail_node
from graph.nodes.review_formatter import review_formatter_node
from graph.nodes.architecture_agent import architecture_agent_node      # Step 7
from graph.nodes.mermaid_validator_node import mermaid_validator_node   # Step 7
from graph.nodes.output_guardrails import output_guardrail_node
from graph.nodes.off_topic import off_topic_node

logger = logging.getLogger(__name__)

# ── Node name constants ────────────────────────────────────────────────────────

_INPUT_GUARD      = "input_guardrail"
_CLASSIFIER       = "intent_classifier"
_QA_AGENT         = "qa_agent"
_FILE_REVIEW      = "file_review"
_PR_REVIEW        = "pr_review_agent"
_PR_GUARDRAIL     = "pr_guardrail"
_PR_FORMATTER     = "review_formatter"
_ARCH_AGENT       = "architecture_agent"
_ARCH_VALIDATOR   = "mermaid_validator"
_OFF_TOPIC        = "off_topic"
_OUTPUT_GUARD     = "output_guardrail"

_VALID_INTENTS = {"repo_qa", "file_review", "pr_review", "architecture", "off_topic"}


# ── Router functions ──────────────────────────────────────────────────────────

def _route_after_guardrail(state: AgentState) -> str:
    """
    Conditional edge evaluated after input_guardrail_node.

    If the guardrail blocked the query, go straight to END — final_response
    is already set and no LLM call should be made.

    If an API endpoint has already set a valid intent, bypass the classifier.
    This is important for endpoints like:
      - POST /repos/{repo_id}/file-review
      - POST /repos/{repo_id}/pulls/{pr_number}/review
      - POST /repos/{repo_id}/architecture/generate
    """
    result = state.get("guardrail_result", {})
    if not result.get("passed", True):
        logger.debug("Input guardrail blocked — routing to END")
        return END  # type: ignore[return-value]

    intent = state.get("intent")
    if intent in _VALID_INTENTS:
        destination = _route_after_classifier(state)
        logger.debug("Pre-set intent %r — bypassing classifier -> %r", intent, destination)
        return destination

    return _CLASSIFIER


def _route_after_classifier(state: AgentState) -> str:
    """
    Conditional edge evaluated after intent_classifier_node.

    Routes on the intent field set by the classifier or API layer.
    """
    intent: str = state.get("intent", "repo_qa")
    route_map: dict[str, str] = {
        "repo_qa":      _QA_AGENT,
        "file_review":  _FILE_REVIEW,
        "pr_review":    _PR_REVIEW,        # ← Step 6: real PR review pipeline
        "architecture": _ARCH_AGENT,        # ← Step 7: real architecture pipeline
        "off_topic":    _OFF_TOPIC,
    }
    destination = route_map.get(intent, _QA_AGENT)
    logger.debug("Intent %r -> node %r", intent, destination)
    return destination


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph() -> Any:
    """Construct and compile the RepoMind LangGraph agent graph."""
    builder: StateGraph = StateGraph(AgentState)

    # ── Register nodes ─────────────────────────────────────────────────────────
    builder.add_node(_INPUT_GUARD,    input_guardrail_node)
    builder.add_node(_CLASSIFIER,     intent_classifier_node)
    builder.add_node(_QA_AGENT,       qa_agent_node)
    builder.add_node(_FILE_REVIEW,    file_review_agent_node)
    builder.add_node(_PR_REVIEW,      pr_review_agent_node)      # Step 6
    builder.add_node(_PR_GUARDRAIL,   pr_guardrail_node)         # Step 6
    builder.add_node(_PR_FORMATTER,   review_formatter_node)     # Step 6
    builder.add_node(_ARCH_AGENT,     architecture_agent_node)   # Step 7
    builder.add_node(_ARCH_VALIDATOR, mermaid_validator_node)    # Step 7
    builder.add_node(_OFF_TOPIC,      off_topic_node)
    builder.add_node(_OUTPUT_GUARD,   output_guardrail_node)

    # ── Entry point ────────────────────────────────────────────────────────────
    builder.set_entry_point(_INPUT_GUARD)

    # ── Edges ──────────────────────────────────────────────────────────────────
    # input_guardrail → (blocked → END) OR classifier OR direct intent node
    builder.add_conditional_edges(
        _INPUT_GUARD,
        _route_after_guardrail,
        {
            END:          END,
            _CLASSIFIER:  _CLASSIFIER,
            _QA_AGENT:    _QA_AGENT,
            _FILE_REVIEW: _FILE_REVIEW,
            _PR_REVIEW:   _PR_REVIEW,
            _ARCH_AGENT:  _ARCH_AGENT,
            _OFF_TOPIC:   _OFF_TOPIC,
        },
    )

    # classifier → intent-based routing
    builder.add_conditional_edges(
        _CLASSIFIER,
        _route_after_classifier,
        {
            _QA_AGENT:    _QA_AGENT,
            _FILE_REVIEW: _FILE_REVIEW,
            _PR_REVIEW:   _PR_REVIEW,
            _ARCH_AGENT:  _ARCH_AGENT,
            _OFF_TOPIC:   _OFF_TOPIC,
        },
    )

    # PR review sub-pipeline: agent → guardrail → formatter → output_guardrail
    builder.add_edge(_PR_REVIEW,      _PR_GUARDRAIL)
    builder.add_edge(_PR_GUARDRAIL,   _PR_FORMATTER)
    builder.add_edge(_PR_FORMATTER,   _OUTPUT_GUARD)

    # Architecture sub-pipeline: agent → validator → output_guardrail
    builder.add_edge(_ARCH_AGENT,     _ARCH_VALIDATOR)
    builder.add_edge(_ARCH_VALIDATOR, _OUTPUT_GUARD)

    # Simple answer nodes → output_guardrail → END
    builder.add_edge(_QA_AGENT,       _OUTPUT_GUARD)
    builder.add_edge(_FILE_REVIEW,    _OUTPUT_GUARD)
    builder.add_edge(_OFF_TOPIC,      _OUTPUT_GUARD)
    builder.add_edge(_OUTPUT_GUARD,   END)

    compiled = builder.compile()
    logger.info("RepoMind LangGraph compiled successfully (nodes: %s)", list(compiled.nodes))
    return compiled


# ── Module-level singleton ─────────────────────────────────────────────────────

graph = build_graph()