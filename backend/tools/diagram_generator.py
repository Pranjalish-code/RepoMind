"""
tools/diagram_generator.py — Convert an AnalysisResult into a Mermaid prompt + fallback.

This module provides two things:
  1. build_component_graph_description()  — render detected components into a
     structured text prompt for the LLM (LLM only converts, does NOT invent).
  2. build_fallback_diagram()             — generate a valid Mermaid diagram
     from the analysis result WITHOUT any LLM call (used when LLM fails or
     confidence is too low).

Design decisions
----------------
* The LLM is NOT allowed to invent components.  The prompt explicitly lists
  which nodes and edges are allowed and instructs the LLM to only render them.
* build_fallback_diagram() is used when:
    - No LLM key is configured.
    - LLM returns invalid Mermaid.
    - LLM validation fails after 2 retries.
* All node IDs used in Mermaid are derived from ComponentInfo.name (which is
  already sanitized to alphanumeric+underscore by CodeAnalyzer).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.code_analyzer import AnalysisResult, ComponentInfo

# ── Node style hints for Mermaid ──────────────────────────────────────────────
# kind → Mermaid node shape
_KIND_SHAPE: dict[str, str] = {
    "frontend":  "([{label}])",    # rounded rectangle
    "backend":   "[{label}]",       # rectangle
    "database":  "[({{label}})]",  # cylinder via HTML  — simplified to [label]
    "auth":      ">{label}]",       # flag shape
    "service":   "({label})",       # stadium
    "api":       "[{label}]",       # rectangle
    "config":    "{{{{{label}}}}}",# hexagon — too complex; use rectangle
}


def _node_label(comp: "ComponentInfo") -> str:
    """Return a clean display label for a Mermaid node."""
    # Escape brackets that would break Mermaid
    label = comp.label.replace("[", "(").replace("]", ")").replace('"', "'")
    return label


def _node_def(comp: "ComponentInfo") -> str:
    """
    Return the Mermaid node definition string.

    Examples:
        Frontend[Frontend React]
        BackendAPI[Backend API FastAPI]
        SQLAlchemyORM[(SQLAlchemy ORM)]
    """
    label = _node_label(comp)
    name = comp.name

    if comp.kind == "database":
        return f'{name}[("{label}")]'
    elif comp.kind == "auth":
        return f'{name}["{label}"]'
    elif comp.kind == "service":
        return f'{name}("{label}")'
    elif comp.kind == "frontend" and name == "User":
        return f'{name}(["{label}"])'
    else:
        return f'{name}["{label}"]'


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt builder
# ═══════════════════════════════════════════════════════════════════════════════

def build_component_graph_description(result: "AnalysisResult") -> str:
    """
    Build the structured text that describes the detected component graph
    for the LLM prompt.

    The LLM is told ONLY to convert this description into Mermaid — it must NOT
    add any components that are not in this list.
    """
    lines: list[str] = []

    lines.append("## Detected Components\n")
    for comp in result.components:
        evidence_str = (", ".join(comp.evidence[:3])) if comp.evidence else "detected"
        lines.append(f"- **{comp.name}** | kind={comp.kind} | label={comp.label!r} | evidence: {evidence_str}")

    lines.append("\n## Detected Edges (directed connections)\n")
    if result.edges:
        for src, dst in result.edges:
            lines.append(f"- {src} --> {dst}")
    else:
        lines.append("- (no edges detected — repo may be too simple or analysis found few files)")

    lines.append("\n## Raw Analysis Facts\n")
    facts = result.raw_facts
    lines.append(f"- Frameworks detected: {facts.get('frameworks', [])}")
    lines.append(f"- Databases detected: {facts.get('databases', [])}")
    lines.append(f"- Auth detected: {facts.get('auth_detected', False)}")
    lines.append(f"- External APIs: {facts.get('external_apis', [])}")
    lines.append(f"- Python files analyzed: {facts.get('py_files', 0)}")
    lines.append(f"- JS/TS files analyzed: {facts.get('js_files', 0)}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Fallback diagram builder
# ═══════════════════════════════════════════════════════════════════════════════

def build_fallback_diagram(result: "AnalysisResult") -> str:
    """
    Build a valid Mermaid flowchart directly from the AnalysisResult
    without any LLM call.

    This is used:
    - When no LLM key is configured.
    - When LLM output fails validation after retries.
    - As a safety net.

    Always starts with 'flowchart TD'.
    """
    lines: list[str] = ["flowchart TD"]

    # Node definitions
    for comp in result.components:
        lines.append(f"    {_node_def(comp)}")

    # Edge definitions
    for src, dst in result.edges:
        # Look up labels
        src_comp = next((c for c in result.components if c.name == src), None)
        dst_comp = next((c for c in result.components if c.name == dst), None)
        if src_comp and dst_comp:
            lines.append(f"    {src} --> {dst}")

    if len(lines) == 1:
        # Empty analysis — produce minimal valid diagram
        lines.append("    Repo[Repository]")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Explanation builder
# ═══════════════════════════════════════════════════════════════════════════════

def build_explanation(result: "AnalysisResult", confidence: int) -> str:
    """Build a bullet-point explanation of the architecture diagram."""
    lines: list[str] = []

    kinds_seen: dict[str, list[str]] = {}
    for comp in result.components:
        if comp.name == "User":
            continue
        kinds_seen.setdefault(comp.kind, []).append(comp.label)

    if "frontend" in kinds_seen:
        labels = ", ".join(kinds_seen["frontend"])
        lines.append(f"- **Frontend**: {labels} — handles the user interface.")
    if "backend" in kinds_seen:
        labels = ", ".join(kinds_seen["backend"])
        lines.append(f"- **Backend API**: {labels} — processes requests and business logic.")
    if "database" in kinds_seen:
        labels = ", ".join(kinds_seen["database"])
        lines.append(f"- **Database**: {labels} — stores and retrieves application data.")
    if "auth" in kinds_seen:
        labels = ", ".join(kinds_seen["auth"])
        lines.append(f"- **Auth**: {labels} — manages authentication and authorization.")
    if "service" in kinds_seen:
        labels = ", ".join(kinds_seen["service"])
        lines.append(f"- **Services**: {labels} — auxiliary infrastructure services.")
    if "api" in kinds_seen:
        labels = ", ".join(kinds_seen["api"])
        lines.append(f"- **External APIs**: {labels} — third-party integrations.")
    if "config" in kinds_seen:
        labels = ", ".join(kinds_seen["config"])
        lines.append(f"- **Infrastructure**: {labels} — deployment and infrastructure.")

    facts = result.raw_facts
    if facts.get("frameworks"):
        lines.append(f"- **Frameworks detected**: {', '.join(facts['frameworks'])}")

    if confidence < 50:
        lines.append(
            "\n> ⚠️ Confidence is low. Manual verification of the diagram is strongly recommended."
        )

    lines.append(
        "\n> 📌 **Note**: This diagram is AI-assisted and based on static analysis only. "
        "It may not capture all runtime dependencies or dynamic behavior. "
        "Manual verification is recommended."
    )

    return "\n".join(lines)
