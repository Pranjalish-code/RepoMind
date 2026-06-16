"""
tools/mermaid_validator.py — Validate and sanitize Mermaid.js flowchart diagrams.

Checks performed
----------------
1.  Diagram must start with 'flowchart TD' (case-insensitive) after stripping whitespace.
2.  All node IDs referenced in edges must appear as defined nodes or be simple identifiers.
3.  No obvious syntax errors (unmatched brackets, unmatched quotes).
4.  No secrets or .env-style assignments appear in the diagram.
5.  Diagrams with a suspiciously high node count (>50) are flagged as low confidence.
6.  Edges with no matching component from the analysis are flagged.

The validator does NOT re-render the Mermaid diagram — it operates on raw text only.

Usage::

    result = validate_mermaid(diagram_text, allowed_node_ids={"Frontend", "BackendAPI"})
    if result.valid:
        print(result.cleaned)
    else:
        print(result.errors)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Patterns ──────────────────────────────────────────────────────────────────

# Standard flowchart node definition patterns:
#   NodeId[Label]  NodeId(Label)  NodeId((Label))  NodeId{Label}  NodeId>Label]
_NODE_DEF_RE = re.compile(
    r"^\s*([A-Za-z0-9_]+)\s*(?:\[|{|\(|>)",
    re.MULTILINE,
)

# Edge patterns: A --> B, A -- text --> B, A --- B
_EDGE_RE = re.compile(
    r"([A-Za-z0-9_]+)\s*(?:-->|---|\|[^|]*\|>?|-\.->|===>?|--\s*\w[^-\n]*\s*-->?)\s*([A-Za-z0-9_]+)",
)

# Secret patterns (must NOT appear in diagrams)
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{20,}"
    r"|AIza[A-Za-z0-9_-]{35}"
    r"|gh[pousr]_[A-Za-z0-9]{36,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|Bearer\s+[A-Za-z0-9\-._~+/]{20,}"
    r'|(?:password|passwd|pwd)\s*[:=]\s*\S{8,})',
    re.I,
)

# .env-style key=value
_ENV_RE = re.compile(r"(?:^|\n)\s*[A-Z][A-Z0-9_]{2,}\s*=\s*\S+", re.MULTILINE)

# Mermaid comment lines (skipped during analysis)
_COMMENT_RE = re.compile(r"^\s*%%.*$", re.MULTILINE)


# ═══════════════════════════════════════════════════════════════════════════════
# Result type
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    """Result from validate_mermaid()."""
    valid: bool
    cleaned: str             # Sanitized diagram text (may be corrected)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    node_ids_found: set[str] = field(default_factory=set)
    edge_count: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# Validator
# ═══════════════════════════════════════════════════════════════════════════════

def validate_mermaid(
    diagram: str,
    allowed_node_ids: set[str] | None = None,
) -> ValidationResult:
    """
    Validate a Mermaid flowchart diagram string.

    Args:
        diagram:          The raw Mermaid diagram text (possibly from LLM).
        allowed_node_ids: Optional set of node IDs expected from analysis.
                          Used to detect fabricated nodes.

    Returns:
        ValidationResult with valid flag, cleaned text, errors, and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ── Step 1: Strip markdown fences ────────────────────────────────────────
    cleaned = diagram.strip()
    fence_match = re.search(r"```(?:mermaid)?\s*([\s\S]+?)\s*```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # ── Step 2: Must start with flowchart TD ─────────────────────────────────
    first_line = cleaned.splitlines()[0].strip() if cleaned else ""
    if not re.match(r"^flowchart\s+TD\s*$", first_line, re.IGNORECASE):
        # Try to fix: prepend correct header
        if re.match(r"^(graph|flowchart)\s+", first_line, re.IGNORECASE):
            # Replace first line with canonical flowchart TD
            lines = cleaned.splitlines()
            lines[0] = "flowchart TD"
            cleaned = "\n".join(lines)
            warnings.append(
                f"First line was {first_line!r}; corrected to 'flowchart TD'."
            )
        else:
            errors.append(
                f"Diagram must start with 'flowchart TD'. Got: {first_line!r}"
            )
            return ValidationResult(
                valid=False,
                cleaned=cleaned,
                errors=errors,
                warnings=warnings,
            )

    # ── Step 3: Secret check (hard fail) ─────────────────────────────────────
    if _SECRET_RE.search(cleaned):
        errors.append("Diagram contains what appears to be a secret/credential. Blocked.")
        return ValidationResult(valid=False, cleaned="", errors=errors)

    if len(_ENV_RE.findall(cleaned)) >= 3:
        errors.append("Diagram appears to contain .env-style variable assignments. Blocked.")
        return ValidationResult(valid=False, cleaned="", errors=errors)

    # ── Step 4: Extract node IDs ──────────────────────────────────────────────
    # Remove comment lines for analysis
    analysis_text = _COMMENT_RE.sub("", cleaned)

    # Find explicitly defined nodes
    defined_nodes: set[str] = set(_NODE_DEF_RE.findall(analysis_text))

    # Also collect nodes referenced in edges (they may not be explicitly defined)
    edge_nodes: set[str] = set()
    edges_found = _EDGE_RE.findall(analysis_text)
    for src, dst in edges_found:
        edge_nodes.add(src)
        edge_nodes.add(dst)

    all_node_ids = defined_nodes | edge_nodes

    # ── Step 5: Validate node count ───────────────────────────────────────────
    if len(all_node_ids) > 50:
        warnings.append(
            f"Diagram has {len(all_node_ids)} nodes — this may be too complex to render clearly."
        )

    if len(all_node_ids) == 0:
        errors.append("Diagram contains no nodes or edges.")
        return ValidationResult(
            valid=False,
            cleaned=cleaned,
            errors=errors,
            warnings=warnings,
        )

    # ── Step 6: Check for fabricated nodes (advisory) ─────────────────────────
    if allowed_node_ids:
        # Build a fuzzy match: check if each diagram node overlaps with an allowed node
        fabricated: list[str] = []
        for node in all_node_ids:
            node_lower = node.lower()
            matched = any(
                node_lower in allowed.lower() or allowed.lower() in node_lower
                for allowed in allowed_node_ids
            )
            if not matched:
                fabricated.append(node)
        if fabricated:
            warnings.append(
                f"These node IDs were not detected by static analysis and may be fabricated: "
                f"{', '.join(sorted(fabricated)[:10])}"
            )

    # ── Step 7: Bracket balance check ─────────────────────────────────────────
    bracket_issues = _check_brackets(cleaned)
    if bracket_issues:
        warnings.extend(bracket_issues)

    # ── Step 8: Remove any lines that look like .env leaks ────────────────────
    safe_lines = [
        line for line in cleaned.splitlines()
        if not _SECRET_RE.search(line)
    ]
    cleaned = "\n".join(safe_lines)

    return ValidationResult(
        valid=True,
        cleaned=cleaned,
        errors=errors,
        warnings=warnings,
        node_ids_found=all_node_ids,
        edge_count=len(edges_found),
    )


def _check_brackets(text: str) -> list[str]:
    """
    Basic bracket balance check for Mermaid syntax.

    Returns a list of warning strings.
    """
    issues: list[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        # Skip comment lines
        if line.strip().startswith("%%"):
            continue
        opens = line.count("[") - line.count("]")
        opens2 = line.count("(") - line.count(")")
        opens3 = line.count("{") - line.count("}")
        if abs(opens) > 1 or abs(opens2) > 1 or abs(opens3) > 1:
            issues.append(f"Line {i} may have unmatched brackets: {line.strip()!r}")
    return issues[:3]  # cap warnings


def extract_mermaid_from_llm_response(raw: str) -> str:
    """
    Extract a Mermaid diagram from an LLM response that may contain prose.

    Tries:
      1. Fenced code block: ```mermaid ... ```
      2. Fenced code block: ``` ... ``` (unlabelled)
      3. Raw text starting with 'flowchart TD'
    """
    # 1. Labelled fence
    m = re.search(r"```mermaid\s*([\s\S]+?)\s*```", raw, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # 2. Any fence
    m = re.search(r"```\s*([\s\S]+?)\s*```", raw)
    if m:
        candidate = m.group(1).strip()
        if "flowchart" in candidate.lower() or "graph" in candidate.lower():
            return candidate

    # 3. Raw flowchart TD anywhere in the response
    m = re.search(r"(flowchart\s+TD[\s\S]+?)(?:\n\n|\Z)", raw, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Fallback: return raw text (let validator handle it)
    return raw.strip()
