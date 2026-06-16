"""
graph/nodes/pr_review_agent.py — GitHub PR Review agent node for RepoMind AI.

Flow
----
1. Read pr_number + repo_id from AgentState.
2. Fetch PR detail + changed files from GitHub API.
3. Parse unified diffs into structured FileDiff objects.
4. Retrieve related code context from Qdrant for each changed file.
5. Build a rich LLM prompt: PR metadata + diffs + related context.
6. Call LLM → expect structured JSON review.
7. Return draft_review dict (validated) + draft_response (markdown).

PR guardrails (in pr_guardrails.py) run AFTER this node via output_guardrail.

Security notes
--------------
* GitHub API token is never logged or included in the LLM prompt.
* Diff content that includes secrets is flagged by output_guardrail.
* File paths in issues are validated against the PR changed file list.
* Line numbers are validated against the parsed diff.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from graph.state import AgentState

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_RETRIEVAL_LIMIT  = 5    # Qdrant chunks per changed file
_MIN_SCORE        = 0.28 # lower threshold for PR context (broader search)
_MAX_FILES        = 50   # cap to prevent runaway prompts
_MAX_ISSUES       = 25   # cap LLM issue count

_VALID_SEVERITIES = {"Low", "Medium", "High"}
_VALID_STATUSES   = {"Safe to merge", "Needs changes", "Risky PR"}

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are RepoMind AI — an expert pull request reviewer.

You will receive:
1. PR metadata (title, author, base/head branch, body).
2. Changed files with unified diffs (lines prefixed + / - / space).
3. Related code context from the repository (for deeper understanding).

Your task: produce a structured JSON code review.

STRICT RULES
------------
1. Every issue MUST reference a file that appears in the Changed Files section.
2. Every line number MUST be a new-file line ('+' side) from the diff.
   If you cannot pinpoint a line, set "line": null.
3. "evidence" MUST be a short verbatim quote or description from the diff.
4. Severity MUST be exactly: "Low", "Medium", or "High".
5. risk_score MUST be an integer between 0 and 10 (0 = no risk, 10 = critical).
6. status MUST be exactly one of:
     "Safe to merge" | "Needs changes" | "Risky PR"
7. Do NOT reveal, print, or reference secrets, tokens, passwords, or API keys.
   Replace them with [REDACTED_SECRET].
8. Do NOT invent file names. Only use files from the Changed Files section.
9. Focus on: logic bugs, runtime errors, breaking changes, frontend/backend
   mismatch, missing validation, weak auth/security, database mismatch,
   performance risks, edge cases after merge.
10. If there are no issues, say so with an empty issues list.

OUTPUT FORMAT — return ONLY valid JSON, no prose before or after:

{
  "status": "Safe to merge | Needs changes | Risky PR",
  "risk_score": 0,
  "summary": "...",
  "issues": [
    {
      "title": "...",
      "file": "...",
      "line": <integer or null>,
      "severity": "Low | Medium | High",
      "evidence": "...",
      "problem": "...",
      "impact": "...",
      "suggested_fix": "..."
    }
  ],
  "final_recommendation": "..."
}
"""


# ── Qdrant retrieval for PR context ───────────────────────────────────────────

async def _retrieve_related_chunks(
    repo_id: str,
    changed_files: list[dict],
    pr_summary: str,
) -> list[dict]:
    """
    Retrieve Qdrant chunks for each changed file using its filename as query.

    Returns a flat list of chunk dicts (deduplicated by file+start_line).
    Fails gracefully — returns [] if Qdrant is unavailable.
    """
    try:
        from rag.embeddings import get_embeddings
        from rag.retriever import search_codebase_sync
        from rag.vectorstore import get_qdrant_client

        embedding_model, _ = get_embeddings()
        qdrant_client = get_qdrant_client()

        seen: set[str] = set()
        all_chunks: list[dict] = []

        # Query 1: PR-level summary query
        queries = [pr_summary[:200]] if pr_summary else []

        # Query 2+: per-file queries using filename as the query text
        for f in changed_files[:_MAX_FILES]:
            fname = f.get("filename", "")
            if fname:
                queries.append(fname)

        for query in queries:
            results = await asyncio.to_thread(
                search_codebase_sync,
                query,
                repo_id,
                embedding_model,
                qdrant_client,
                _RETRIEVAL_LIMIT,
            )
            for r in results:
                if r.score < _MIN_SCORE:
                    continue
                key = f"{r.file_path}:{r.start_line}"
                if key in seen:
                    continue
                seen.add(key)
                all_chunks.append({
                    "file_path":   r.file_path,
                    "language":    r.language,
                    "symbol_name": r.symbol_name,
                    "start_line":  r.start_line,
                    "end_line":    r.end_line,
                    "content":     r.content,
                    "score":       r.score,
                })

        logger.info("Retrieved %d related chunks for PR review", len(all_chunks))
        return all_chunks

    except Exception as exc:
        logger.warning("Qdrant retrieval failed for PR review: %s", exc)
        return []


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_pr_prompt(
    pr_detail: dict,
    diff_context: str,
    related_chunks: list[dict],
    query: str,
) -> str:
    """Build the full user-turn content for the LLM."""
    lines: list[str] = []

    # PR metadata
    lines.append("## Pull Request")
    lines.append(f"**Title:** {pr_detail.get('title', '')}")
    lines.append(f"**Author:** {pr_detail.get('user_login', '')}")
    lines.append(f"**Branch:** `{pr_detail.get('head_ref', '')}` → `{pr_detail.get('base_ref', '')}`")
    lines.append(f"**State:** {pr_detail.get('state', '')}  |  "
                 f"**Additions:** +{pr_detail.get('additions', 0)}  |  "
                 f"**Deletions:** -{pr_detail.get('deletions', 0)}")

    body = (pr_detail.get("body") or "").strip()
    if body:
        lines.append(f"\n**Description:**\n{body[:800]}")

    if query and query.lower() not in ("review pr", "review this pr"):
        lines.append(f"\n**Reviewer's focus:** {query}")

    # Changed files diff
    lines.append("\n---\n## Changed Files\n")
    if diff_context:
        lines.append(diff_context)
    else:
        lines.append("_(No diff available — files may be binary or too large)_")

    # Related context from Qdrant
    if related_chunks:
        lines.append("\n---\n## Related Repository Context\n")
        lines.append("_(These are existing code chunks related to the changed files.)_\n")
        for chunk in related_chunks[:15]:   # cap context chunks
            fp = chunk.get("file_path", "")
            sl = chunk.get("start_line", "?")
            el = chunk.get("end_line", "?")
            lang = chunk.get("language", "")
            sym = chunk.get("symbol_name", "")
            sym_info = f" — `{sym}`" if sym else ""
            lines.append(f"**{fp}:{sl}-{el}**{sym_info}")
            lines.append(f"```{lang}\n{chunk.get('content', '')[:2000]}\n```")

    lines.append("\n---\nProduce the JSON review now.")
    return "\n".join(lines)


# ── LLM call ──────────────────────────────────────────────────────────────────

async def _call_llm(prompt: str) -> str:
    """Call the configured LLM and return raw response text."""
    from config import settings
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]

    if settings.gemini_api_key.strip():
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            temperature=0.1,
            google_api_key=settings.gemini_api_key,
            max_output_tokens=4096,
        )
    elif settings.openai_api_key.strip():
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=settings.openai_api_key,
            max_tokens=4096,
        )
    else:
        return '{"status":"Needs changes","risk_score":0,"summary":"No LLM API key configured.","issues":[],"final_recommendation":"Set GEMINI_API_KEY or OPENAI_API_KEY."}'

    try:
        response = await llm.ainvoke(messages)
        return response.content.strip()
    except Exception as exc:
        logger.error("LLM call failed in pr_review_agent: %s", exc)
        return f'{{"status":"Needs changes","risk_score":0,"summary":"LLM error: {exc}","issues":[],"final_recommendation":"Please retry."}}'


# ── JSON parsing ──────────────────────────────────────────────────────────────

import json
import re

def _parse_review_json(raw: str) -> dict:
    """Extract and parse the JSON review from the LLM response."""
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    json_str = fence_match.group(1) if fence_match else raw

    brace_match = re.search(r"\{[\s\S]*\}", json_str)
    if brace_match:
        json_str = brace_match.group(0)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse PR review JSON: %s", exc)
        return {
            "status": "Needs changes",
            "risk_score": 5,
            "summary": f"Could not parse LLM response: {exc}",
            "issues": [],
            "final_recommendation": "Please retry the review.",
            "_parse_error": str(exc),
        }


# ── Node ──────────────────────────────────────────────────────────────────────

async def pr_review_agent_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: fetch PR from GitHub and generate a structured review.

    Reads:
        pr_number     — from AgentState (set by classifier or API)
        repo_id       — repository UUID
        query         — reviewer's focus question

    Writes:
        draft_review   — validated review dict
        draft_response — formatted markdown (passed to output_guardrail)
        changed_files  — list of file dicts from GitHub
        related_files  — deduplicated list of referenced file paths
        retrieved_chunks — related Qdrant chunks
        error          — error string or None
    """
    pr_number: int | None = state.get("pr_number")
    repo_id: str = state.get("repo_id", "")
    query: str = state.get("query", "").strip()

    def _error(msg: str, code: str) -> dict:
        return {
            "draft_response": msg,
            "draft_review": {
                "status": "Needs changes",
                "risk_score": 0,
                "summary": msg,
                "issues": [],
                "final_recommendation": "Fix the error and retry.",
            },
            "final_response": msg,
            "error": code,
        }

    # ── Validate inputs ───────────────────────────────────────────────────────
    if not pr_number:
        return _error("No PR number provided. Use 'Review PR #N' syntax.", "missing_pr_number")
    if not repo_id:
        return _error("No repository ID provided.", "missing_repo_id")

    # ── Fetch repo metadata from DB ───────────────────────────────────────────
    try:
        from db.session import AsyncSessionLocal
        from db.crud import get_repository
        async with AsyncSessionLocal() as db:
            repo = await get_repository(db, repo_id)
            if repo is None:
                return _error(f"Repository '{repo_id}' not found.", "repo_not_found")
            repo_url = repo.repo_url
    except Exception as exc:
        logger.error("DB lookup failed in pr_review_agent: %s", exc)
        return _error(f"Database error: {exc}", "db_error")

    # ── Fetch PR from GitHub ───────────────────────────────────────────────────
    try:
        from github.pr_fetcher import fetch_pr_detail, fetch_pr_files
        pr_detail = await asyncio.to_thread(fetch_pr_detail, repo_url, pr_number)
        pr_files  = await asyncio.to_thread(fetch_pr_files,  repo_url, pr_number)
    except ValueError as exc:
        return _error(f"GitHub API error: {exc}", "github_api_error")
    except Exception as exc:
        logger.error("PR fetch failed: %s", exc)
        return _error(f"Failed to fetch PR #{pr_number}: {exc}", "github_fetch_error")

    # ── Parse diffs ────────────────────────────────────────────────────────────
    from github.diff_parser import parse_pr_files, build_diff_context_block
    file_diffs = parse_pr_files(pr_files)
    diff_context = build_diff_context_block(file_diffs)

    changed_file_paths = {f["filename"] for f in pr_files if f.get("filename")}

    # ── Qdrant related context ─────────────────────────────────────────────────
    pr_summary = f"{pr_detail.get('title', '')} {pr_detail.get('body', '')[:200]}"
    related_chunks = await _retrieve_related_chunks(repo_id, pr_files, pr_summary)

    # ── Build LLM prompt ──────────────────────────────────────────────────────
    prompt = _build_pr_prompt(pr_detail, diff_context, related_chunks, query)

    # ── Call LLM ──────────────────────────────────────────────────────────────
    raw_response = await _call_llm(prompt)
    raw_review = _parse_review_json(raw_response)

    # ── Store intermediate state ───────────────────────────────────────────────
    return {
        "draft_review":    raw_review,
        "draft_response":  "",              # formatter fills this in pr_guardrails
        "changed_files":   pr_files,
        "related_files":   list(changed_file_paths),
        "retrieved_chunks": related_chunks,
        "pr_number":       pr_number,
        "error":           raw_review.get("_parse_error"),
        # Pass the parsed diffs forward via guardrail_result for validation
        "guardrail_result": {
            "passed":           True,
            "changed_file_paths": list(changed_file_paths),
            "file_diffs_json":   {
                k: {
                    "added_lines":   list(v.added_lines),
                    "changed_range": list(v.changed_line_range) if v.changed_line_range else [],
                }
                for k, v in file_diffs.items()
            },
        },
    }
