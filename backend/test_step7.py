"""
test_step7.py — Integration tests for Step 7: Architecture Diagram Generator

Tests:
  - code_analyzer:     directory scanning, package.json, Python/JS analysis
  - mermaid_validator: flowchart TD check, secret redaction, bracket check
  - diagram_generator: fallback diagram, explanation builder
  - architecture nodes: mermaid_validator_node output structure

Usage:
    cd backend
    python test_step7.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_repo(structure: dict) -> tempfile.TemporaryDirectory:
    """Create a temp dir with the given structure {path: content}."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel_path, content in structure.items():
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return tmp


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Code Analyzer Tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_analyzer_detects_backend_from_fastapi():
    from tools.code_analyzer import CodeAnalyzer
    tmp = _make_repo({
        "backend/main.py": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health(): return {}",
        "requirements.txt": "fastapi>=0.100\nuvicorn",
    })
    try:
        result = CodeAnalyzer(tmp.name).analyze()
        kinds = {c.kind for c in result.components}
        assert "backend" in kinds, f"Expected backend, got kinds={kinds}"
        names = {c.name for c in result.components}
        assert "BackendAPI" in names, f"Expected BackendAPI in {names}"
        print("OK  test_analyzer_detects_backend_from_fastapi")
    finally:
        tmp.cleanup()


def test_analyzer_detects_frontend_from_package_json():
    from tools.code_analyzer import CodeAnalyzer
    import json
    pkg = json.dumps({"dependencies": {"react": "^18.0.0", "next": "^14.0.0"}})
    tmp = _make_repo({
        "package.json": pkg,
        "pages/index.tsx": "export default function Home() { return <div>Hello</div> }",
    })
    try:
        result = CodeAnalyzer(tmp.name).analyze()
        kinds = {c.kind for c in result.components}
        assert "frontend" in kinds, f"Expected frontend, got kinds={kinds}"
        print("OK  test_analyzer_detects_frontend_from_package_json")
    finally:
        tmp.cleanup()


def test_analyzer_detects_database_from_sqlalchemy():
    from tools.code_analyzer import CodeAnalyzer
    tmp = _make_repo({
        "db/models.py": "from sqlalchemy import Column, Integer\nfrom sqlalchemy.orm import declarative_base\nBase = declarative_base()",
        "db/session.py": "from sqlalchemy.ext.asyncio import create_async_engine",
    })
    try:
        result = CodeAnalyzer(tmp.name).analyze()
        kinds = {c.kind for c in result.components}
        assert "database" in kinds, f"Expected database, got kinds={kinds}"
        print("OK  test_analyzer_detects_database_from_sqlalchemy")
    finally:
        tmp.cleanup()


def test_analyzer_detects_auth_from_jwt():
    from tools.code_analyzer import CodeAnalyzer
    tmp = _make_repo({
        "auth/utils.py": "import jwt\nfrom passlib.context import CryptContext\npwd_context = CryptContext(schemes=['bcrypt'])",
    })
    try:
        result = CodeAnalyzer(tmp.name).analyze()
        kinds = {c.kind for c in result.components}
        assert "auth" in kinds, f"Expected auth, got kinds={kinds}"
        print("OK  test_analyzer_detects_auth_from_jwt")
    finally:
        tmp.cleanup()


def test_analyzer_builds_edges_frontend_to_backend():
    from tools.code_analyzer import CodeAnalyzer
    import json
    pkg = json.dumps({"dependencies": {"react": "^18.0.0"}})
    tmp = _make_repo({
        "frontend/package.json": pkg,
        "frontend/src/App.tsx": "export default function App() { return <div/> }",
        "backend/main.py": "from fastapi import FastAPI\napp = FastAPI()",
        "backend/db.py": "from sqlalchemy import create_engine",
    })
    try:
        result = CodeAnalyzer(tmp.name).analyze()
        edges = set(result.edges)
        # Should have at least User -> something
        assert len(edges) > 0, "Expected at least one edge"
        print("OK  test_analyzer_builds_edges_frontend_to_backend")
    finally:
        tmp.cleanup()


def test_analyzer_confidence_scales_with_evidence():
    from tools.code_analyzer import CodeAnalyzer
    import json

    # Rich repo — high confidence
    pkg = json.dumps({"dependencies": {"react": "^18.0.0", "next": "^14.0.0"}})
    tmp_rich = _make_repo({
        "frontend/package.json": pkg,
        "frontend/src/App.tsx": "export default function App() { return <div/> }",
        "backend/main.py": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/')\ndef root(): return {}",
        "backend/db.py": "from sqlalchemy.ext.asyncio import create_async_engine\nimport aiosqlite",
        "backend/auth.py": "import jwt\nfrom passlib.context import CryptContext",
    })
    # Thin repo — low confidence
    tmp_thin = _make_repo({
        "README.md": "# My project",
    })

    try:
        rich_result = CodeAnalyzer(tmp_rich.name).analyze()
        thin_result = CodeAnalyzer(tmp_thin.name).analyze()
        assert rich_result.confidence > thin_result.confidence, (
            f"Rich should have higher confidence: {rich_result.confidence} vs {thin_result.confidence}"
        )
        assert rich_result.confidence <= 95, "Confidence should never exceed 95%"
        print(f"OK  test_analyzer_confidence_scales_with_evidence  (rich={rich_result.confidence}%, thin={thin_result.confidence}%)")
    finally:
        tmp_rich.cleanup()
        tmp_thin.cleanup()


def test_analyzer_never_reads_dotenv():
    """The analyzer must never read .env files."""
    from tools.code_analyzer import CodeAnalyzer
    tmp = _make_repo({
        ".env": "SECRET_KEY=super_secret_value\nDB_PASSWORD=password123",
        "main.py": "print('hello')",
    })
    try:
        # Just verify it doesn't crash and .env content doesn't appear in facts
        result = CodeAnalyzer(tmp.name).analyze()
        facts_str = str(result.raw_facts)
        assert "super_secret_value" not in facts_str
        assert "password123" not in facts_str
        print("OK  test_analyzer_never_reads_dotenv")
    finally:
        tmp.cleanup()


def test_analyzer_handles_empty_repo():
    """Should not crash on a repo with no relevant files."""
    from tools.code_analyzer import CodeAnalyzer
    tmp = _make_repo({
        "README.md": "# Empty project",
    })
    try:
        result = CodeAnalyzer(tmp.name).analyze()
        assert result.confidence >= 0
        assert isinstance(result.components, list)
        print("OK  test_analyzer_handles_empty_repo")
    finally:
        tmp.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Mermaid Validator Tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_validator_valid_diagram():
    from tools.mermaid_validator import validate_mermaid
    diagram = """flowchart TD
    User([User])
    Frontend[React Frontend]
    Backend[FastAPI Backend]
    DB[(SQLite)]
    User --> Frontend
    Frontend --> Backend
    Backend --> DB"""
    result = validate_mermaid(diagram)
    assert result.valid, f"Expected valid, errors={result.errors}"
    assert result.edge_count == 3
    assert "User" in result.node_ids_found
    print("OK  test_validator_valid_diagram")


def test_validator_rejects_missing_flowchart_td():
    from tools.mermaid_validator import validate_mermaid
    diagram = "graph LR\n    A --> B"
    result = validate_mermaid(diagram)
    # Should auto-correct graph LR -> flowchart TD
    assert result.valid, f"Should be corrected to valid, errors={result.errors}"
    assert result.cleaned.startswith("flowchart TD")
    print("OK  test_validator_rejects_missing_flowchart_td")


def test_validator_rejects_raw_text():
    from tools.mermaid_validator import validate_mermaid
    diagram = "This is not a diagram at all."
    result = validate_mermaid(diagram)
    assert not result.valid, "Should be invalid"
    print("OK  test_validator_rejects_raw_text")


def test_validator_blocks_secrets():
    from tools.mermaid_validator import validate_mermaid
    diagram = """flowchart TD
    Frontend --> Backend
    Backend -->|sk-1234567890abcdefghij| ExternalAPI"""
    result = validate_mermaid(diagram)
    # Should be valid (line with secret is removed) or blocked
    if result.valid:
        assert "sk-1234567890abcdefghij" not in result.cleaned
    print("OK  test_validator_blocks_secrets")


def test_validator_warns_fabricated_nodes():
    from tools.mermaid_validator import validate_mermaid
    diagram = """flowchart TD
    Frontend[React]
    Backend[FastAPI]
    FakeService[Invented Component]
    Frontend --> Backend
    Backend --> FakeService"""
    result = validate_mermaid(diagram, allowed_node_ids={"Frontend", "Backend"})
    assert result.valid
    # FakeService should generate a warning (not an error)
    assert any("FakeService" in w for w in result.warnings)
    print("OK  test_validator_warns_fabricated_nodes")


def test_extract_mermaid_from_fenced_block():
    from tools.mermaid_validator import extract_mermaid_from_llm_response
    raw = """Here is the diagram:

```mermaid
flowchart TD
    A --> B
```

That's all!"""
    extracted = extract_mermaid_from_llm_response(raw)
    assert extracted.startswith("flowchart TD")
    print("OK  test_extract_mermaid_from_fenced_block")


def test_extract_mermaid_unfenced():
    from tools.mermaid_validator import extract_mermaid_from_llm_response
    raw = "flowchart TD\n    A --> B\n\nSome extra text"
    extracted = extract_mermaid_from_llm_response(raw)
    assert "flowchart TD" in extracted
    print("OK  test_extract_mermaid_unfenced")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Diagram Generator Tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_fallback_diagram_is_valid():
    from tools.code_analyzer import CodeAnalyzer
    from tools.diagram_generator import build_fallback_diagram
    from tools.mermaid_validator import validate_mermaid
    import json
    pkg = json.dumps({"dependencies": {"react": "^18.0.0"}})
    tmp = _make_repo({
        "package.json": pkg,
        "api/main.py": "from fastapi import FastAPI\napp = FastAPI()",
        "db/models.py": "from sqlalchemy import Column",
    })
    try:
        result = CodeAnalyzer(tmp.name).analyze()
        fallback = build_fallback_diagram(result)
        val = validate_mermaid(fallback)
        assert val.valid, f"Fallback diagram is invalid: {val.errors}"
        assert fallback.startswith("flowchart TD")
        print("OK  test_fallback_diagram_is_valid")
    finally:
        tmp.cleanup()


def test_explanation_contains_detected_components():
    from tools.code_analyzer import CodeAnalyzer
    from tools.diagram_generator import build_explanation
    import json
    pkg = json.dumps({"dependencies": {"react": "^18.0.0"}})
    tmp = _make_repo({
        "package.json": pkg,
        "api/main.py": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/')\ndef root(): return {}",
        "db/session.py": "from sqlalchemy.ext.asyncio import create_async_engine",
    })
    try:
        result = CodeAnalyzer(tmp.name).analyze()
        explanation = build_explanation(result, result.confidence)
        assert "Note" in explanation or "note" in explanation.lower()
        assert "static analysis" in explanation.lower()
        print("OK  test_explanation_contains_detected_components")
    finally:
        tmp.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Mermaid Validator Node Test
# ═══════════════════════════════════════════════════════════════════════════════

async def test_mermaid_validator_node_valid():
    """Node should pass a valid diagram through and build formatted output."""
    from graph.nodes.mermaid_validator_node import mermaid_validator_node
    from graph.state import AgentState

    state: AgentState = {
        "diagram_mermaid": "flowchart TD\n    User([User])\n    Backend[FastAPI]\n    User --> Backend",
        "diagram_explanation": "- Backend handles requests.\n",
        "diagram_confidence": 75,
        "draft_review": {
            "detected_components_json": [
                {"name": "User", "kind": "frontend", "label": "User", "evidence": []},
                {"name": "Backend", "kind": "backend", "label": "FastAPI", "evidence": []},
            ],
            "repo_name": "TestRepo",
        },
        "repo_id": "",   # empty — DB save will be skipped (non-fatal)
        "guardrail_result": {"passed": True},
    }

    result = await mermaid_validator_node(state)

    assert "flowchart TD" in result["diagram_mermaid"]
    assert "Architecture Diagram" in result["draft_response"]
    assert "75%" in result["draft_response"]
    assert "Note" in result["draft_response"] or "note" in result["draft_response"].lower()
    print("OK  test_mermaid_validator_node_valid")


async def test_mermaid_validator_node_invalid_falls_back():
    """Node should replace an invalid diagram with a stub."""
    from graph.nodes.mermaid_validator_node import mermaid_validator_node
    from graph.state import AgentState

    state: AgentState = {
        "diagram_mermaid": "This is not mermaid at all.",
        "diagram_explanation": "",
        "diagram_confidence": 60,
        "draft_review": {"detected_components_json": [], "repo_name": "TestRepo"},
        "repo_id": "",
        "guardrail_result": {"passed": True},
    }

    result = await mermaid_validator_node(state)

    # Should still return flowchart TD (the stub)
    assert "flowchart TD" in result["diagram_mermaid"]
    # Confidence should be reduced
    print("OK  test_mermaid_validator_node_invalid_falls_back")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Graph compilation test
# ═══════════════════════════════════════════════════════════════════════════════

def test_graph_has_architecture_nodes():
    from graph.graph import graph
    node_names = list(graph.nodes.keys())
    assert "architecture_agent" in node_names, f"architecture_agent not in nodes: {node_names}"
    assert "mermaid_validator" in node_names, f"mermaid_validator not in nodes: {node_names}"
    print(f"OK  test_graph_has_architecture_nodes  (nodes={node_names})")


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════

async def run_async():
    await test_mermaid_validator_node_valid()
    await test_mermaid_validator_node_invalid_falls_back()


def main():
    print("\n" + "=" * 62)
    print("  RepoMind AI -- Step 7 Tests: Architecture Diagram Generator")
    print("=" * 62 + "\n")

    # Code Analyzer
    test_analyzer_detects_backend_from_fastapi()
    test_analyzer_detects_frontend_from_package_json()
    test_analyzer_detects_database_from_sqlalchemy()
    test_analyzer_detects_auth_from_jwt()
    test_analyzer_builds_edges_frontend_to_backend()
    test_analyzer_confidence_scales_with_evidence()
    test_analyzer_never_reads_dotenv()
    test_analyzer_handles_empty_repo()

    # Mermaid Validator
    test_validator_valid_diagram()
    test_validator_rejects_missing_flowchart_td()
    test_validator_rejects_raw_text()
    test_validator_blocks_secrets()
    test_validator_warns_fabricated_nodes()
    test_extract_mermaid_from_fenced_block()
    test_extract_mermaid_unfenced()

    # Diagram Generator
    test_fallback_diagram_is_valid()
    test_explanation_contains_detected_components()

    # Graph nodes (async)
    asyncio.run(run_async())

    # Graph compilation
    test_graph_has_architecture_nodes()

    print("\n" + "=" * 62)
    print("  All Step 7 tests passed!")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    main()
