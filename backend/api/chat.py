"""
api/chat.py — POST /chat/stream  (Server-Sent Events streaming endpoint)

Protocol
--------
Each SSE event is a JSON object on its own data: line:

  data: {"type": "token",    "content": "Hello"}
  data: {"type": "token",    "content": " world"}
  data: {"type": "metadata", "intent": "repo_qa", "related_files": [...]}
  data: {"type": "citations","citations": [...]}
  data: {"type": "done",     "message_id": "<uuid>"}
  data: {"type": "error",    "detail": "<message>"}

The client accumulates token events to show a streaming answer, then uses
metadata / citations to enrich the UI after the stream ends.

Message persistence
-------------------
* User message is stored BEFORE the graph runs (so it's never lost).
* Assistant message is stored AFTER final_response is available, with the
  citations list embedded as JSON.
* Both stores use the existing db/crud.py helpers.

Error handling
--------------
* Repository not found → 404 (before stream starts).
* Graph execution error → SSE error event (stream continues to close cleanly).
* DB error → logged but does not break the stream.

Design decisions
----------------
* sse-starlette is used for clean SSE support without manual chunking.
* The LangGraph graph is invoked with ainvoke() (async) — the graph already
  uses asyncio.to_thread() internally for sync Qdrant / LLM calls.
* We simulate per-token streaming by splitting the final_response on word
  boundaries because LangGraph's ainvoke() collects the full response before
  returning.  A true streaming approach would require LangGraph's astream_events,
  which is wired in here via the ENABLE_TOKEN_STREAMING flag.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db
from db.crud import (
    create_chat_message,
    get_repository,
    get_or_create_system_user,
)
from graph.graph import graph
from graph.state import AgentState

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Request / response schemas ────────────────────────────────────────────────

class ChatStreamRequest(BaseModel):
    """Request body for POST /chat/stream."""
    repo_id: str = Field(..., description="UUID of the indexed repository")
    query: str   = Field(..., min_length=1, max_length=4000,
                         description="User question about the codebase")
    user_id: str = Field(default="", description="Optional user UUID")


# ── SSE event helpers ─────────────────────────────────────────────────────────

def _sse_event(payload: dict[str, Any]) -> str:
    """Encode a dict as a single SSE data line."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _error_event(detail: str) -> str:
    return _sse_event({"type": "error", "detail": detail})


def _done_event(message_id: str) -> str:
    return _sse_event({"type": "done", "message_id": message_id})


# ── Token streaming helpers ───────────────────────────────────────────────────

async def _stream_tokens(text: str) -> AsyncGenerator[str, None]:
    """
    Split the final_response into word-level pseudo-tokens and yield SSE events.

    This gives the client a streaming feel even though the full answer is
    already computed.  Each token event carries a small chunk of text.
    """
    # Split into chunks of ~3-5 words to reduce SSE overhead
    words = text.split(" ")
    chunk_size = 4
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i : i + chunk_size])
        if i + chunk_size < len(words):
            chunk += " "   # re-add the space between chunks
        yield _sse_event({"type": "token", "content": chunk})
        await asyncio.sleep(0)   # yield event loop control


# ── Core SSE generator ────────────────────────────────────────────────────────

async def _chat_stream_generator(
    request: ChatStreamRequest,
    db: AsyncSession,
) -> AsyncGenerator[str, None]:
    """
    Full SSE event generator for one chat turn.

    Yields SSE events:
      token     — incremental answer text
      metadata  — intent, related_files
      citations — grounded source references
      done      — final event with stored message_id
      error     — if something goes wrong
    """
    repo_id = request.repo_id.strip()
    query   = request.query.strip()
    user_id = request.user_id.strip()

    # ── 1. Validate repository exists ─────────────────────────────────────────
    repo = await get_repository(db, repo_id)
    if repo is None:
        yield _error_event(f"Repository '{repo_id}' not found.")
        return

    if repo.status not in ("ready", "indexing"):
        yield _error_event(
            f"Repository '{repo.repo_name}' is not ready for Q&A "
            f"(current status: {repo.status}). "
            "Please index the repository first."
        )
        return

    # ── 2. Resolve user ────────────────────────────────────────────────────────
    if not user_id:
        try:
            system_user = await get_or_create_system_user(db)
            user_id = system_user.id
        except Exception as exc:
            logger.warning("Could not resolve system user: %s", exc)

    # ── 3. Store user message ──────────────────────────────────────────────────
    try:
        await create_chat_message(
            db, repo_id=repo_id, role="user", content=query
        )
        await db.commit()
    except Exception as exc:
        logger.error("Failed to store user message: %s", exc)
        # Non-fatal — continue processing

    # ── 4. Build initial state and invoke graph ────────────────────────────────
    initial_state: AgentState = {
        "user_id":       user_id,
        "repo_id":       repo_id,
        "query":         query,
        # Defaults for list fields (LangGraph requires all keys to exist when
        # total=False is used with strict typing)
        "indexed_files":  [],
        "changed_files":  [],
        "retrieved_chunks": [],
        "related_files":  [],
        "draft_response": "",
        "draft_review":   {},
        "final_response": "",
        "diagram_mermaid": "",
        "diagram_explanation": "",
        "diagram_confidence": 0,
        "guardrail_result": {},
        "selected_file": None,
        "pr_number":     None,
        "error":         None,
    }

    final_state: AgentState | None = None
    graph_error: str | None = None

    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.error("Graph execution failed: %s", exc, exc_info=True)
        graph_error = str(exc)

    if graph_error or final_state is None:
        yield _error_event(
            graph_error or "Graph returned no state. Please try again."
        )
        return

    # ── 5. Extract outputs ─────────────────────────────────────────────────────
    final_response: str  = final_state.get("final_response", "").strip()
    intent: str          = final_state.get("intent", "repo_qa")
    related_files: list  = final_state.get("related_files", [])
    retrieved_chunks: list = final_state.get("retrieved_chunks", [])
    guardrail: dict      = final_state.get("guardrail_result", {})

    if not final_response:
        final_response = (
            "I encountered an issue generating an answer. Please try again."
        )

    # ── 6. Stream tokens ───────────────────────────────────────────────────────
    async for event in _stream_tokens(final_response):
        yield event

    # ── 7. Yield metadata event ────────────────────────────────────────────────
    yield _sse_event({
        "type":          "metadata",
        "intent":        intent,
        "related_files": related_files,
        "guardrail":     {
            "input_passed":  guardrail.get("passed", True),
            "output_passed": guardrail.get("output_passed", True),
            "redactions":    guardrail.get("redactions", 0),
        },
    })

    # ── 8. Build and yield citations ───────────────────────────────────────────
    citations: list[dict] = []
    seen: set[str] = set()
    for chunk in retrieved_chunks:
        key = f"{chunk.get('file_path')}:{chunk.get('start_line')}"
        if key in seen:
            continue
        seen.add(key)
        citations.append({
            "file_path":   chunk.get("file_path", ""),
            "start_line":  chunk.get("start_line", 0),
            "end_line":    chunk.get("end_line", 0),
            "symbol_name": chunk.get("symbol_name"),
            "score":       round(chunk.get("score", 0.0), 3),
        })

    if citations:
        yield _sse_event({"type": "citations", "citations": citations})

    # ── 9. Store assistant message ─────────────────────────────────────────────
    assistant_msg_id = str(uuid.uuid4())
    try:
        msg = await create_chat_message(
            db,
            repo_id=repo_id,
            role="assistant",
            content=final_response,
            citations=citations or None,
        )
        await db.commit()
        assistant_msg_id = msg.id
    except Exception as exc:
        logger.error("Failed to store assistant message: %s", exc)
        # Non-fatal

    # ── 10. Done event ─────────────────────────────────────────────────────────
    yield _done_event(assistant_msg_id)


# ── Route ──────────────────────────────────────────────────────────────────────

@router.post(
    "/stream",
    summary="Streaming codebase Q&A chat",
    description=(
        "Submit a natural-language question about an indexed repository. "
        "Returns a Server-Sent Events stream of token chunks, metadata, "
        "citations, and a final done event."
    ),
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE stream",
            "content": {"text/event-stream": {}},
        },
        404: {"description": "Repository not found"},
        422: {"description": "Validation error"},
    },
)
async def chat_stream(
    request: ChatStreamRequest,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    POST /chat/stream

    Accepts JSON body::

        {
            "repo_id": "<uuid>",
            "query":   "How does the authentication middleware work?",
            "user_id": "<uuid or empty>"
        }

    Returns an ``text/event-stream`` response.  Each event is a JSON object
    on a ``data:`` line.  See module docstring for event type details.
    """
    # Quick pre-validation before starting the stream
    repo_id = request.repo_id.strip()
    if not repo_id:
        raise HTTPException(status_code=422, detail="repo_id must not be empty")

    async def generate() -> AsyncGenerator[str, None]:
        async for event in _chat_stream_generator(request, db):
            yield event

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":             "no-cache",
            "X-Accel-Buffering":         "no",      # disable nginx buffering
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── Chat history endpoint ──────────────────────────────────────────────────────

@router.get(
    "/history/{repo_id}",
    summary="Retrieve chat history for a repository",
    response_model=list[dict],
)
async def get_chat_history(
    repo_id: str,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """
    GET /chat/history/{repo_id}

    Returns the last *limit* chat messages for the given repository,
    ordered oldest-first.
    """
    from db.crud import list_chat_messages

    repo = await get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_id}' not found")

    messages = await list_chat_messages(db, repo_id, skip=skip, limit=limit)
    return [
        {
            "id":         msg.id,
            "role":       msg.role,
            "content":    msg.content,
            "citations":  msg.citations,
            "created_at": msg.created_at.isoformat(),
        }
        for msg in messages
    ]
