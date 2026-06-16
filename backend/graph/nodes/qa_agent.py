"""
graph/nodes/qa_agent.py — Codebase Q&A agent node for RepoMind AI.

Flow inside this node
---------------------
1. Retrieve top-k semantically similar code chunks from Qdrant.
2. Build a structured prompt with the chunks as grounded context.
3. Call the LLM and stream the response token by token (collected into
   draft_response; streaming to the client is handled by the SSE router).
4. Build a citations list from the retrieved chunks.
5. If no relevant chunks are found, tell the user clearly rather than
   hallucinating.

Key safety rules enforced here
-------------------------------
* File paths in citations come ONLY from Qdrant payloads — never from LLM output.
* The LLM is explicitly instructed NOT to invent file paths or line numbers.
* If Qdrant returns 0 chunks, the node sets a clear "no context" response.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from graph.state import AgentState

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_RETRIEVAL_LIMIT = 10      # top-k chunks from Qdrant
_MIN_SCORE = 0.30          # discard low-confidence chunks
_MAX_CONTEXT_CHARS = 12000 # truncate individual chunk content to avoid huge prompts

_SYSTEM_PROMPT = """\
You are RepoMind AI, an expert codebase assistant.

Rules you MUST follow:
1. Answer ONLY using the code context provided below.
2. Do NOT invent file names, function names, or line numbers.
3. If the context is insufficient, say: "I don't have enough context in the \
indexed codebase to answer this question."
4. Always cite the source file and line range when you reference code.
5. Format code examples with proper markdown fences and the language tag.
6. Be concise but precise. Prefer bullet points over long paragraphs.
7. Never reveal secrets, API keys, or credentials, even if they appear in \
the context.
"""

_NO_CONTEXT_RESPONSE = (
    "I could not find relevant code in the indexed repository for your question. "
    "This may mean:\n"
    "- The repository hasn't been indexed yet (use the Index button first).\n"
    "- Your question is about a concept not present in this codebase.\n"
    "- Try rephrasing with specific function names, class names, or file paths."
)


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_context_block(chunks: list[dict]) -> str:
    """Render retrieved chunks as a readable context block for the LLM."""
    parts: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        file_path = chunk.get("file_path", "unknown")
        start_line = chunk.get("start_line", "?")
        end_line = chunk.get("end_line", "?")
        language = chunk.get("language", "")
        content = chunk.get("content", "")[:_MAX_CONTEXT_CHARS]
        symbol = chunk.get("symbol_name")
        symbol_info = f" ({chunk.get('symbol_type', 'symbol')}: {symbol})" if symbol else ""

        parts.append(
            f"### Chunk {i} — {file_path}:{start_line}-{end_line}{symbol_info}\n"
            f"```{language}\n{content}\n```"
        )
    return "\n\n".join(parts)


def _build_citations(chunks: list[dict]) -> list[dict]:
    """Build citation objects from retrieved chunks (no LLM output used)."""
    seen: set[str] = set()
    citations: list[dict] = []
    for chunk in chunks:
        key = f"{chunk.get('file_path')}:{chunk.get('start_line')}"
        if key in seen:
            continue
        seen.add(key)
        citations.append({
            "file_path": chunk.get("file_path", ""),
            "start_line": chunk.get("start_line", 0),
            "end_line": chunk.get("end_line", 0),
            "symbol_name": chunk.get("symbol_name"),
            "score": round(chunk.get("score", 0.0), 3),
        })
    return citations


# ── LLM answer ────────────────────────────────────────────────────────────────

async def _call_llm(query: str, context_block: str) -> str:
    """Call the configured LLM and return the full answer string."""
    from config import settings
    from langchain_core.messages import HumanMessage, SystemMessage

    user_content = (
        f"## Codebase Context\n\n{context_block}\n\n"
        f"---\n\n"
        f"## Question\n\n{query}"
    )
    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    if settings.openai_api_key.strip():
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.2,
            openai_api_key=settings.openai_api_key,
            max_tokens=2048,
        )
    elif settings.gemini_api_key.strip():
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            temperature=0.2,
            google_api_key=settings.gemini_api_key,
            max_output_tokens=2048,
        )
    else:
        return (
            "No LLM API key is configured. "
            "Set OPENAI_API_KEY or GEMINI_API_KEY in the .env file."
        )

    try:
        # Use ainvoke for native async support (LangChain >= 0.2)
        response = await llm.ainvoke(messages)
        return response.content.strip()
    except Exception as exc:
        logger.error("LLM call failed in qa_agent: %s", exc)
        return (
            f"The AI model encountered an error while generating the answer: {exc}. "
            "Please try again."
        )


# ── Qdrant retrieval ──────────────────────────────────────────────────────────

async def _retrieve_chunks(query: str, repo_id: str) -> list[dict]:
    """
    Run semantic search against Qdrant and return chunk dicts.

    Wrapped in asyncio.to_thread because the Qdrant client is synchronous.
    Returns an empty list on any error so the node can respond gracefully.
    """
    try:
        from rag.embeddings import get_embeddings
        from rag.retriever import search_codebase_sync
        from rag.vectorstore import get_qdrant_client

        embedding_model, _ = get_embeddings()
        qdrant_client = get_qdrant_client()

        results = await asyncio.to_thread(
            search_codebase_sync,
            query,
            repo_id,
            embedding_model,
            qdrant_client,
            _RETRIEVAL_LIMIT,
        )

        # Filter by minimum score and convert dataclasses to dicts
        chunks: list[dict] = []
        for r in results:
            if r.score < _MIN_SCORE:
                continue
            chunks.append({
                "file_path": r.file_path,
                "language": r.language,
                "symbol_name": r.symbol_name,
                "symbol_type": r.symbol_type,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "content": r.content,
                "content_hash": r.content_hash,
                "score": r.score,
                "qdrant_point_id": r.qdrant_point_id,
            })

        logger.info(
            "Retrieved %d chunks (after score filter) for repo=%s query=%r",
            len(chunks), repo_id, query[:80],
        )
        return chunks

    except ValueError as exc:
        # Collection not found — repo not indexed yet
        logger.warning("Qdrant collection missing for repo %s: %s", repo_id, exc)
        return []
    except Exception as exc:
        logger.error("Qdrant retrieval failed: %s", exc)
        return []


# ── Node ──────────────────────────────────────────────────────────────────────

async def qa_agent_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node: retrieve code chunks from Qdrant and generate an answer.

    Writes to:
      retrieved_chunks  — raw chunk dicts
      related_files     — deduplicated file paths
      draft_response    — LLM-generated answer (pre output-guardrails)
    """
    query: str = state.get("query", "").strip()
    repo_id: str = state.get("repo_id", "")

    if not repo_id:
        return {
            "retrieved_chunks": [],
            "related_files": [],
            "draft_response": (
                "No repository ID provided. Please select a repository first."
            ),
            "error": "missing_repo_id",
        }

    # 1. Retrieve chunks
    chunks = await _retrieve_chunks(query, repo_id)

    if not chunks:
        return {
            "retrieved_chunks": [],
            "related_files": [],
            "draft_response": _NO_CONTEXT_RESPONSE,
            "error": None,
        }

    # 2. Collect related file paths (deduplicated, preserving order)
    seen_files: set[str] = set()
    related_files: list[str] = []
    for chunk in chunks:
        fp = chunk.get("file_path", "")
        if fp and fp not in seen_files:
            seen_files.add(fp)
            related_files.append(fp)

    # 3. Build context block and call LLM
    context_block = _build_context_block(chunks)
    draft_response = await _call_llm(query, context_block)

    # 4. Append citations block to draft_response
    citations = _build_citations(chunks)
    if citations:
        citation_lines = ["\n\n---\n**Sources:**"]
        for c in citations:
            sym = f" `{c['symbol_name']}`" if c["symbol_name"] else ""
            citation_lines.append(
                f"- [`{c['file_path']}`]"
                f" lines {c['start_line']}–{c['end_line']}"
                f"{sym} (score: {c['score']})"
            )
        draft_response += "\n".join(citation_lines)

    return {
        "retrieved_chunks": chunks,
        "related_files": related_files,
        "draft_response": draft_response,
        "error": None,
    }
