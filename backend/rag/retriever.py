"""
rag/retriever.py — Semantic search over Qdrant for RepoMind AI.

Key design decisions:
  - Always filters by repo_id payload field → results are repo-scoped.
  - Retrieval is synchronous; call via asyncio.to_thread() from async context.
  - The full chunk content is stored in the Qdrant payload (by the indexer)
    so retrieval is self-contained (no disk reads needed).
  - Scores are normalised COSINE distances (higher = more similar).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

from rag.embeddings import EmbeddingModel
from rag.vectorstore import check_qdrant_connection, collection_exists, collection_name_for

logger = logging.getLogger(__name__)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    """A single retrieved code chunk with similarity score."""

    file_path: str
    language: str
    symbol_name: str | None
    symbol_type: str | None
    start_line: int
    end_line: int
    content: str
    content_hash: str
    score: float             # COSINE similarity [0, 1]; higher = more relevant
    qdrant_point_id: str


# ── Retrieval pipeline ────────────────────────────────────────────────────────

def search_codebase_sync(
    query: str,
    repo_id: str,
    embedding_model: EmbeddingModel,
    qdrant_client: QdrantClient,
    limit: int = 10,
) -> list[SearchResult]:
    """
    Semantic search over a repository's Qdrant collection.

    Always filters by repo_id so results are scoped to the given repo,
    even if multiple repos share the same Qdrant instance.

    Args:
        query:           Natural-language or code search query.
        repo_id:         Repository UUID to filter results.
        embedding_model: LangChain-compatible embedding model.
        qdrant_client:   Connected Qdrant client.
        limit:           Maximum results to return (default 10, max 50).

    Returns:
        List of SearchResult objects, ordered by descending similarity score.

    Raises:
        ValueError: if the Qdrant collection does not exist for this repo.
        RuntimeError: if Qdrant server is unreachable.
    """
    limit = max(1, min(limit, 50))   # clamp to [1, 50]
    col = collection_name_for(repo_id)

    # ── Connectivity + collection check ───────────────────────────────────────
    check_qdrant_connection(qdrant_client)

    if not collection_exists(qdrant_client, col):
        raise ValueError(
            f"Repository '{repo_id}' has not been indexed yet. "
            f"Call POST /repos/{repo_id}/index first."
        )

    # ── Embed query ───────────────────────────────────────────────────────────
    try:
        query_vector: list[float] = embedding_model.embed_query(query)
    except Exception as exc:
        raise RuntimeError(f"Failed to embed query: {exc}") from exc

    if not query_vector:
        raise RuntimeError("Query embedding returned an empty vector.")

    # ── Search with repo_id filter ────────────────────────────────────────────
    repo_filter = qmodels.Filter(
        must=[
            qmodels.FieldCondition(
                key="repo_id",
                match=qmodels.MatchValue(value=repo_id),
            )
        ]
    )

    try:
        hits = qdrant_client.search(
            collection_name=col,
            query_vector=query_vector,
            query_filter=repo_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,    # Don't return the raw vectors
        )
    except Exception as exc:
        raise RuntimeError(f"Qdrant search failed: {exc}") from exc

    # ── Map hits to SearchResult ──────────────────────────────────────────────
    results: list[SearchResult] = []
    for hit in hits:
        payload = hit.payload or {}
        results.append(
            SearchResult(
                file_path=payload.get("file_path", ""),
                language=payload.get("language", ""),
                symbol_name=payload.get("symbol_name"),
                symbol_type=payload.get("symbol_type"),
                start_line=int(payload.get("start_line", 0)),
                end_line=int(payload.get("end_line", 0)),
                content=payload.get("content", ""),
                content_hash=payload.get("content_hash", ""),
                score=float(hit.score),
                qdrant_point_id=str(hit.id),
            )
        )

    logger.info(
        "Search for repo %s returned %d results (query=%r)", repo_id, len(results), query[:80]
    )
    return results
