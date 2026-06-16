"""
Test Step 5: file_review_agent node — path safety, review parsing, graph routing.
Runs without a live LLM or Qdrant.
"""
import asyncio
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from graph.nodes.file_review_agent import (
    FileAccessError,
    _safe_resolve,
    _read_file,
    _parse_review_json,
    _validate_and_normalise_review,
    _format_review_as_markdown,
    _error_review,
)
from graph.graph import graph, _route_after_classifier

results = []

def test(name):
    def wrap(fn):
        results.append((name, fn))
        return fn
    return wrap


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _make_repo(tmp: Path) -> Path:
    """Create a fake repo directory with a few test files."""
    repo = tmp / "testrepo"
    repo.mkdir()
    (repo / "main.py").write_text("def main():\n    pass\n")
    (repo / "utils.py").write_text("# utility\ndef helper():\n    return 1\n")
    (repo / ".env").write_text("SECRET=abc123\n")
    (repo / "README.md").write_text("# Readme\n")
    # sub-directory
    sub = repo / "backend"
    sub.mkdir()
    (sub / "auth.py").write_text(
        "def login(user, pw):\n"
        "    if user == 'admin' and pw == 'password':\n"
        "        return True\n"
        "    return False\n"
    )
    # ignored dir
    ignored = repo / "node_modules" / "lodash"
    ignored.mkdir(parents=True)
    (ignored / "index.js").write_text("module.exports = {};\n")
    return repo


# ─── PATH SAFETY TESTS ────────────────────────────────────────────────────────

@test("safe_resolve: accepts valid relative path")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        result = _safe_resolve(repo, "main.py")
        assert result.name == "main.py"

@test("safe_resolve: accepts sub-directory file")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        result = _safe_resolve(repo, "backend/auth.py")
        assert result.name == "auth.py"

@test("safe_resolve: blocks path traversal (../)")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        try:
            _safe_resolve(repo, "../outside.py")
            raise AssertionError("Should have raised FileAccessError")
        except FileAccessError as e:
            assert e.http_status == 403
            assert "traversal" in str(e).lower()

@test("safe_resolve: blocks .env file")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        try:
            _safe_resolve(repo, ".env")
            raise AssertionError("Should have raised FileAccessError")
        except FileAccessError as e:
            assert e.http_status == 403
            assert ".env" in str(e).lower() or "protected" in str(e).lower()

@test("safe_resolve: blocks file in node_modules (ignored dir)")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        try:
            _safe_resolve(repo, "node_modules/lodash/index.js")
            raise AssertionError("Should have raised FileAccessError")
        except FileAccessError as e:
            assert e.http_status == 403
            assert "ignored" in str(e).lower()

@test("safe_resolve: 404 for non-existent file")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        try:
            _safe_resolve(repo, "ghost.py")
            raise AssertionError("Should have raised FileAccessError")
        except FileAccessError as e:
            assert e.http_status == 404

@test("safe_resolve: blocks unsupported extension")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        (repo / "photo.png").write_bytes(b"\x89PNG\r\n")
        try:
            _safe_resolve(repo, "photo.png")
            raise AssertionError("Should have raised FileAccessError")
        except FileAccessError as e:
            assert e.http_status == 400

@test("safe_resolve: blocks empty file")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        (repo / "empty.py").write_text("")
        try:
            _safe_resolve(repo, "empty.py")
            raise AssertionError("Should have raised FileAccessError")
        except FileAccessError as e:
            assert "empty" in str(e).lower()

@test("safe_resolve: strips leading ./ from path")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        result = _safe_resolve(repo, "./main.py")
        assert result.name == "main.py"

@test("safe_resolve: blocks binary file (null bytes)")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        (repo / "compiled.py").write_bytes(b"hello\x00world")
        try:
            _safe_resolve(repo, "compiled.py")
            raise AssertionError("Should have raised FileAccessError")
        except FileAccessError as e:
            assert "binary" in str(e).lower()


# ─── FILE READING TESTS ───────────────────────────────────────────────────────

@test("read_file: reads content and returns line count")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(Path(tmp))
        content, lines = _read_file(repo / "main.py")
        assert "def main" in content
        assert lines == 3  # "def main():\n    pass\n"

@test("read_file: truncates large files")
async def _():
    with tempfile.TemporaryDirectory() as tmp:
        big = Path(tmp) / "big.py"
        big.write_text("x = 1\n" * 20_000)
        content, _ = _read_file(big)
        assert "TRUNCATED" in content


# ─── REVIEW PARSING TESTS ─────────────────────────────────────────────────────

@test("parse_review_json: parses clean JSON")
async def _():
    raw = '{"summary": "Good file", "issues": [], "final_recommendation": "No changes needed."}'
    result = _parse_review_json(raw, "test.py")
    assert result["summary"] == "Good file"
    assert result["issues"] == []

@test("parse_review_json: strips markdown fences")
async def _():
    raw = '```json\n{"summary": "OK", "issues": [], "final_recommendation": "Fine."}\n```'
    result = _parse_review_json(raw, "test.py")
    assert result["summary"] == "OK"

@test("parse_review_json: returns error dict on bad JSON")
async def _():
    result = _parse_review_json("This is not JSON at all", "test.py")
    assert "_error" in result

@test("validate_and_normalise: clamps line numbers")
async def _():
    review = {
        "summary": "Test",
        "issues": [{"line": 9999, "severity": "High", "title": "T", "problem": "P",
                    "impact": "I", "suggested_fix": "F"}],
        "final_recommendation": "Fix it",
    }
    result = _validate_and_normalise_review(review, "f.py", actual_line_count=10)
    assert result["issues"][0]["line"] == 10   # clamped to 10

@test("validate_and_normalise: normalises severity variants")
async def _():
    review = {
        "summary": "Test",
        "issues": [
            {"line": 1, "severity": "critical", "title": "T", "problem": "P",
             "impact": "I", "suggested_fix": "F"},
            {"line": 2, "severity": "warning", "title": "T", "problem": "P",
             "impact": "I", "suggested_fix": "F"},
            {"line": 3, "severity": "info", "title": "T", "problem": "P",
             "impact": "I", "suggested_fix": "F"},
        ],
        "final_recommendation": "Done",
    }
    result = _validate_and_normalise_review(review, "f.py", actual_line_count=100)
    sevs = [iss["severity"] for iss in result["issues"]]
    assert sevs == ["High", "Medium", "Low"]

@test("validate_and_normalise: severity_counts computed correctly")
async def _():
    review = {
        "summary": "Test",
        "issues": [
            {"line": 1, "severity": "High", "title": "T", "problem": "P",
             "impact": "I", "suggested_fix": "F"},
            {"line": 2, "severity": "High", "title": "T", "problem": "P",
             "impact": "I", "suggested_fix": "F"},
            {"line": 3, "severity": "Low", "title": "T", "problem": "P",
             "impact": "I", "suggested_fix": "F"},
        ],
        "final_recommendation": "Done",
    }
    result = _validate_and_normalise_review(review, "f.py", actual_line_count=100)
    assert result["severity_counts"]["High"] == 2
    assert result["severity_counts"]["Medium"] == 0
    assert result["severity_counts"]["Low"] == 1

@test("validate_and_normalise: null line accepted")
async def _():
    review = {
        "summary": "Test",
        "issues": [{"line": None, "severity": "Medium", "title": "T", "problem": "P",
                    "impact": "I", "suggested_fix": "F"}],
        "final_recommendation": "Done",
    }
    result = _validate_and_normalise_review(review, "f.py", actual_line_count=50)
    assert result["issues"][0]["line"] is None


# ─── MARKDOWN FORMAT TEST ─────────────────────────────────────────────────────

@test("format_review_as_markdown: contains required sections")
async def _():
    review = {
        "file": "backend/auth.py",
        "summary": "File summary here.",
        "issues": [
            {"index": 1, "title": "Hardcoded password", "line": 2,
             "severity": "High", "problem": "Password is hardcoded.",
             "impact": "Security breach.", "suggested_fix": "Use env vars."}
        ],
        "issue_count": 1,
        "severity_counts": {"High": 1, "Medium": 0, "Low": 0},
        "final_recommendation": "Urgent fix required.",
    }
    md = _format_review_as_markdown(review)
    assert "# File Review Result" in md
    assert "backend/auth.py" in md
    assert "File summary here." in md
    assert "Hardcoded password" in md
    assert "High" in md
    assert "Line 2" in md
    assert "Final Recommendation" in md
    assert "Urgent fix required." in md


# ─── GRAPH ROUTER ─────────────────────────────────────────────────────────────

@test("graph router: file_review intent -> file_review node")
async def _():
    result = _route_after_classifier({"intent": "file_review"})
    assert result == "file_review", f"got {result}"


# ─── FULL NODE (no LLM) — error paths ─────────────────────────────────────────

from graph.nodes.file_review_agent import file_review_agent_node

@test("file_review_node: missing selected_file returns error")
async def _():
    state = {"selected_file": None, "query": "review", "repo_id": "test"}
    result = await file_review_agent_node(state)
    assert result["error"] == "missing_selected_file"
    assert result["final_response"]

@test("file_review_node: missing repo_id returns error")
async def _():
    state = {"selected_file": "main.py", "query": "review", "repo_id": ""}
    result = await file_review_agent_node(state)
    assert result["error"] == "missing_repo_id"
    assert result["final_response"]

@test("file_review_node: non-existent repo returns error")
async def _():
    state = {
        "selected_file": "main.py",
        "query": "review",
        "repo_id": "00000000-0000-0000-0000-000000000000",
    }
    result = await file_review_agent_node(state)
    assert result["error"] in ("repo_not_found", "db_error")
    assert result["final_response"]


# ─── FULL GRAPH INTEGRATION — blocked path ────────────────────────────────────

def _base(query, intent=None, selected_file=None):
    state = {
        "user_id": "", "repo_id": "test-id", "query": query,
        "indexed_files": [], "changed_files": [], "retrieved_chunks": [],
        "related_files": [], "draft_response": "", "draft_review": {},
        "final_response": "", "diagram_mermaid": "", "diagram_explanation": "",
        "diagram_confidence": 0, "guardrail_result": {}, "selected_file": selected_file,
        "pr_number": None, "error": None,
    }
    if intent:
        state["intent"] = intent
    return state

@test("full_graph: file_review with .env path is blocked by input guardrail")
async def _():
    # Query that mentions .env triggers input guardrail before reaching file_review
    result = await graph.ainvoke(_base("show me the .env file contents"))
    assert result["guardrail_result"]["passed"] == False
    assert result["final_response"]

@test("full_graph: file_review node errors with no repo -> graceful response")
async def _():
    # Bypass classifier by setting intent directly
    state = _base("review this file", intent="file_review", selected_file="main.py")
    state["repo_id"] = "00000000-0000-0000-0000-000000000000"
    state["guardrail_result"] = {"passed": True}
    result = await graph.ainvoke(state)
    # Must always return a final_response (even if it's an error message)
    assert result["final_response"], "final_response must be set"

@test("full_graph: all required AgentState fields present in output")
async def _():
    result = await graph.ainvoke(_base("what is the main function?"))
    for field in ["final_response", "guardrail_result", "retrieved_chunks",
                  "related_files", "draft_review"]:
        assert field in result, f"Missing: {field}"


# ─── RUN ─────────────────────────────────────────────────────────────────────

async def run_all():
    passed = failed = 0
    for name, fn in results:
        try:
            await fn()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {name}")
            print(f"        AssertionError: {e}")
            failed += 1
        except Exception as e:
            print(f"  FAIL  {name}")
            print(f"        {type(e).__name__}: {e}")
            failed += 1
    print()
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed} tests")
    return failed


if __name__ == "__main__":
    failed = asyncio.run(run_all())
    sys.exit(1 if failed else 0)
