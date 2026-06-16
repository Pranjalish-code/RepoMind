"""
test_step6.py — Integration tests for Step 6: GitHub PR Review Bot

Tests:
  - diff_parser: parse_patch, parse_pr_files, build_diff_context_block, line_in_diff
  - pr_guardrails: issue validation, secret redaction, line validation, risk clamping
  - review_formatter: format_review output structure

Usage:
    cd backend
    python -m pytest test_step6.py -v
    # OR run directly:
    python test_step6.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# ── Add backend to path ────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Diff parser tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_parse_patch_basic():
    from github.diff_parser import parse_patch
    patch = """@@ -1,4 +1,5 @@
 def foo():
+    x = 1
     return x
-    pass
 
"""
    fd = parse_patch(patch, filename="test.py", status="modified", additions=1, deletions=1)
    assert fd.filename == "test.py"
    assert len(fd.hunks) == 1
    hunk = fd.hunks[0]
    assert any(dl.line_type == "added" for dl in hunk.lines)
    assert any(dl.line_type == "removed" for dl in hunk.lines)
    assert 2 in fd.added_lines
    print("OK test_parse_patch_basic passed")


def test_parse_patch_empty():
    from github.diff_parser import parse_patch
    fd = parse_patch("", filename="binary.bin", status="added")
    assert fd.hunks == []
    assert fd.added_lines == set()
    print("OK test_parse_patch_empty passed")


def test_parse_pr_files():
    from github.diff_parser import parse_pr_files
    pr_files = [
        {
            "filename": "src/app.py",
            "status": "modified",
            "additions": 2,
            "deletions": 1,
            "patch": "@@ -1,3 +1,4 @@\n def main():\n+    print('hello')\n     pass\n-    return\n"
        },
        {
            "filename": "README.md",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": "@@ -1,2 +1,3 @@\n # Project\n+Updated readme\n "
        }
    ]
    diffs = parse_pr_files(pr_files)
    assert "src/app.py" in diffs
    assert "README.md" in diffs
    assert len(diffs["src/app.py"].hunks) == 1
    print("OK test_parse_pr_files passed")


def test_line_in_diff():
    from github.diff_parser import parse_pr_files, line_in_diff
    pr_files = [
        {
            "filename": "src/app.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": "@@ -1,2 +1,3 @@\n def main():\n+    x = 1\n     pass\n"
        }
    ]
    diffs = parse_pr_files(pr_files)
    assert line_in_diff(diffs, "src/app.py", 2) is True
    assert line_in_diff(diffs, "src/app.py", 999) is False
    assert line_in_diff(diffs, "nonexistent.py", 1) is False
    print("OK test_line_in_diff passed")


def test_build_diff_context_block():
    from github.diff_parser import parse_pr_files, build_diff_context_block
    pr_files = [
        {
            "filename": "main.py",
            "status": "added",
            "additions": 3,
            "deletions": 0,
            "patch": "@@ -0,0 +1,3 @@\n+def hello():\n+    print('hi')\n+    return True\n"
        }
    ]
    diffs = parse_pr_files(pr_files)
    block = build_diff_context_block(diffs)
    assert "main.py" in block
    assert "added" in block
    assert "+def hello():" in block
    print("OK test_build_diff_context_block passed")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PR Guardrails tests
# ═══════════════════════════════════════════════════════════════════════════════

async def test_pr_guardrail_removes_invalid_file():
    """Issues referencing files not in the PR should be removed."""
    from graph.nodes.pr_guardrails import pr_guardrail_node
    from graph.state import AgentState

    state: AgentState = {
        "draft_review": {
            "status": "Needs changes",
            "risk_score": 7,
            "summary": "This PR has issues.",
            "issues": [
                {
                    "title": "Valid issue",
                    "file": "src/app.py",
                    "line": 10,
                    "severity": "High",
                    "evidence": "x = 1 is unsafe",
                    "problem": "Unvalidated input",
                    "impact": "Security risk",
                    "suggested_fix": "Validate before use",
                },
                {
                    "title": "Invalid file issue",
                    "file": "fake/nonexistent.py",
                    "line": 5,
                    "severity": "Low",
                    "evidence": "Some evidence",
                    "problem": "Problem",
                    "impact": "Impact",
                    "suggested_fix": "Fix it",
                },
            ],
            "final_recommendation": "Fix the security issue.",
        },
        "guardrail_result": {
            "passed": True,
            "changed_file_paths": ["src/app.py"],
            "file_diffs_json": {
                "src/app.py": {
                    "added_lines": [10, 11, 12],
                    "changed_range": [10, 15],
                }
            },
        },
        "retrieved_chunks": [],
    }

    result = await pr_guardrail_node(state)
    cleaned = result["draft_review"]
    assert len(cleaned["issues"]) == 1, f"Expected 1 issue, got {len(cleaned['issues'])}"
    assert cleaned["issues"][0]["file"] == "src/app.py"
    print("OK test_pr_guardrail_removes_invalid_file passed")


async def test_pr_guardrail_redacts_secrets():
    """Issues containing API keys should be redacted."""
    from graph.nodes.pr_guardrails import pr_guardrail_node
    from graph.state import AgentState

    state: AgentState = {
        "draft_review": {
            "status": "Risky PR",
            "risk_score": 9,
            "summary": "Found API key sk-12345678901234567890 in source.",
            "issues": [
                {
                    "title": "Exposed key",
                    "file": "config.py",
                    "line": 3,
                    "severity": "High",
                    "evidence": "sk-12345678901234567890 found in code",
                    "problem": "API key in source",
                    "impact": "Credential leak",
                    "suggested_fix": "Use env vars",
                }
            ],
            "final_recommendation": "Remove sk-12345678901234567890 immediately.",
        },
        "guardrail_result": {
            "passed": True,
            "changed_file_paths": ["config.py"],
            "file_diffs_json": {
                "config.py": {"added_lines": [3], "changed_range": [1, 10]}
            },
        },
        "retrieved_chunks": [],
    }

    result = await pr_guardrail_node(state)
    cleaned = result["draft_review"]
    # Secrets must be redacted
    assert "sk-12345678901234567890" not in cleaned["summary"]
    assert "sk-12345678901234567890" not in cleaned["issues"][0]["evidence"]
    assert "[REDACTED" in cleaned["summary"] or "[REDACTED" in cleaned["issues"][0]["evidence"]
    print("OK test_pr_guardrail_redacts_secrets passed")


async def test_pr_guardrail_clamps_risk_score():
    """risk_score must be clamped to [0, 10]."""
    from graph.nodes.pr_guardrails import pr_guardrail_node
    from graph.state import AgentState

    for raw_score, expected in [(-5, 0), (15, 10), (7, 7), ("invalid", 5)]:
        state: AgentState = {
            "draft_review": {
                "status": "Needs changes",
                "risk_score": raw_score,
                "summary": "Test",
                "issues": [],
                "final_recommendation": "No action",
            },
            "guardrail_result": {"passed": True, "changed_file_paths": [], "file_diffs_json": {}},
            "retrieved_chunks": [],
        }
        result = await pr_guardrail_node(state)
        actual = result["draft_review"]["risk_score"]
        assert actual == expected, f"risk_score {raw_score!r} → expected {expected}, got {actual}"
    print("OK test_pr_guardrail_clamps_risk_score passed")


async def test_pr_guardrail_removes_no_evidence():
    """Issues without evidence must be removed."""
    from graph.nodes.pr_guardrails import pr_guardrail_node
    from graph.state import AgentState

    state: AgentState = {
        "draft_review": {
            "status": "Needs changes",
            "risk_score": 3,
            "summary": "Issues found.",
            "issues": [
                {
                    "title": "Issue with evidence",
                    "file": "app.py",
                    "line": 5,
                    "severity": "Medium",
                    "evidence": "func() returns None unexpectedly",
                    "problem": "None returned",
                    "impact": "Runtime error",
                    "suggested_fix": "Add a guard",
                },
                {
                    "title": "Issue without evidence",
                    "file": "app.py",
                    "line": 10,
                    "severity": "Low",
                    "evidence": "",  # empty — should be removed
                    "problem": "Something",
                    "impact": "Minor",
                    "suggested_fix": "Fix it",
                },
            ],
            "final_recommendation": "Fix issues.",
        },
        "guardrail_result": {
            "passed": True,
            "changed_file_paths": ["app.py"],
            "file_diffs_json": {
                "app.py": {"added_lines": [5, 10], "changed_range": [1, 20]}
            },
        },
        "retrieved_chunks": [],
    }

    result = await pr_guardrail_node(state)
    cleaned = result["draft_review"]
    assert len(cleaned["issues"]) == 1
    assert cleaned["issues"][0]["title"] == "Issue with evidence"
    print("OK test_pr_guardrail_removes_no_evidence passed")


async def test_pr_guardrail_validates_severity():
    """Invalid severity must be replaced with 'Medium'."""
    from graph.nodes.pr_guardrails import pr_guardrail_node
    from graph.state import AgentState

    state: AgentState = {
        "draft_review": {
            "status": "Needs changes",
            "risk_score": 4,
            "summary": "Test",
            "issues": [
                {
                    "title": "Bad severity",
                    "file": "main.py",
                    "line": 1,
                    "severity": "Critical",  # invalid
                    "evidence": "some evidence",
                    "problem": "x",
                    "impact": "y",
                    "suggested_fix": "z",
                }
            ],
            "final_recommendation": "Fix",
        },
        "guardrail_result": {
            "passed": True,
            "changed_file_paths": ["main.py"],
            "file_diffs_json": {
                "main.py": {"added_lines": [1], "changed_range": [1, 5]}
            },
        },
        "retrieved_chunks": [],
    }

    result = await pr_guardrail_node(state)
    assert result["draft_review"]["issues"][0]["severity"] == "Medium"
    print("OK test_pr_guardrail_validates_severity passed")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Review formatter tests
# ═══════════════════════════════════════════════════════════════════════════════

async def test_review_formatter_output():
    """Formatter should produce markdown with all required sections."""
    from graph.nodes.review_formatter import review_formatter_node
    from graph.state import AgentState

    state: AgentState = {
        "draft_review": {
            "status": "Needs changes",
            "risk_score": 6,
            "summary": "The PR introduces a potential SQL injection vulnerability.",
            "issues": [
                {
                    "title": "SQL Injection Risk",
                    "file": "db/queries.py",
                    "line": 42,
                    "severity": "High",
                    "evidence": "f\"SELECT * FROM users WHERE id={user_id}\"",
                    "problem": "String formatting in SQL",
                    "impact": "Database compromise",
                    "suggested_fix": "Use parameterized queries",
                }
            ],
            "final_recommendation": "Do not merge until SQL injection is fixed.",
        },
        "pr_number": 7,
        "repo_id": "test-repo-id",
        "guardrail_result": {"formatter_done": False},
    }

    result = await review_formatter_node(state)
    formatted = result.get("draft_response", "") or result.get("final_response", "")

    assert "PR Review Result" in formatted
    assert "Status:" in formatted
    assert "Risk Score:" in formatted
    assert "Summary" in formatted
    assert "Issues Found" in formatted
    assert "SQL Injection Risk" in formatted
    assert "Final Recommendation" in formatted
    print("OK test_review_formatter_output passed")


async def test_review_formatter_no_issues():
    """Formatter should handle a clean PR with no issues."""
    from graph.nodes.review_formatter import review_formatter_node
    from graph.state import AgentState

    state: AgentState = {
        "draft_review": {
            "status": "Safe to merge",
            "risk_score": 1,
            "summary": "This PR looks clean. No issues found.",
            "issues": [],
            "final_recommendation": "Approve and merge.",
        },
        "pr_number": 3,
        "repo_id": "test-repo-id",
        "guardrail_result": {},
    }

    result = await review_formatter_node(state)
    formatted = result.get("draft_response", "") or result.get("final_response", "")

    assert "Safe to merge" in formatted
    assert "No issues detected" in formatted
    print("OK test_review_formatter_no_issues passed")


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════

async def run_async_tests():
    await test_pr_guardrail_removes_invalid_file()
    await test_pr_guardrail_redacts_secrets()
    await test_pr_guardrail_clamps_risk_score()
    await test_pr_guardrail_removes_no_evidence()
    await test_pr_guardrail_validates_severity()
    await test_review_formatter_output()
    await test_review_formatter_no_issues()


def main():
    print("\n" + "=" * 60)
    print("  RepoMind AI — Step 6 Tests: PR Review Bot")
    print("=" * 60 + "\n")

    # Sync tests
    test_parse_patch_basic()
    test_parse_patch_empty()
    test_parse_pr_files()
    test_line_in_diff()
    test_build_diff_context_block()

    # Async tests
    asyncio.run(run_async_tests())

    print("\n" + "=" * 60)
    print("  All Step 6 tests passed! OK")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
