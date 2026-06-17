"""
rag/retriever.py — Semantic search over Qdrant for RepoMind AI.

Key design decisions:
  - Always filters by repo_id payload field → results are repo-scoped.
  - Optionally filters by file_path → used for exact file review.
  - Retrieval is synchronous; call via asyncio.to_thread() from async context.
  - The full chunk content is stored in the Qdrant payload by the indexer.
  - Uses Qdrant query_points() instead of deprecated/removed search().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

from rag.embeddings import EmbeddingModel
from rag.vectorstore import (
    check_qdrant_connection,
    collection_exists,
    collection_name_for,
)

logger = logging.getLogger(__name__)


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
    score: float
    qdrant_point_id: str


def search_codebase_sync(
    query: str,
    repo_id: str,
    embedding_model: EmbeddingModel,
    qdrant_client: QdrantClient,
    limit: int = 10,
    file_path: str | None = None,
) -> list[SearchResult]:
    """
    Semantic search over a repository's Qdrant collection.

    Args:
        query: Natural-language or code search query.
        repo_id: Repository UUID.
        embedding_model: LangChain-compatible embedding model.
        qdrant_client: Connected Qdrant client.
        limit: Maximum results to return.
        file_path: Optional exact repository file path to restrict results.

    Returns:
        List of SearchResult objects ordered by similarity score.
    """

    limit = max(1, min(limit, 50))
    col = collection_name_for(repo_id)

    check_qdrant_connection(qdrant_client)

    if not collection_exists(qdrant_client, col):
        raise ValueError(
            f"Repository '{repo_id}' has not been indexed yet. "
            f"Call POST /repos/{repo_id}/index first."
        )

    try:
        query_vector: list[float] = embedding_model.embed_query(query)
    except Exception as exc:
        raise RuntimeError(f"Failed to embed query: {exc}") from exc

    if not query_vector:
        raise RuntimeError("Query embedding returned an empty vector.")

    must_conditions = [
        qmodels.FieldCondition(
            key="repo_id",
            match=qmodels.MatchValue(value=repo_id),
        )
    ]

    if file_path:
        must_conditions.append(
            qmodels.FieldCondition(
                key="file_path",
                match=qmodels.MatchValue(value=file_path),
            )
        )

    repo_filter = qmodels.Filter(must=must_conditions)

    try:
        response = qdrant_client.query_points(
            collection_name=col,
            query=query_vector,
            query_filter=repo_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        hits = response.points

    except TypeError:
        # Fallback for qdrant-client versions that use `filter`
        # instead of `query_filter`.
        try:
            response = qdrant_client.query_points(
                collection_name=col,
                query=query_vector,
                filter=repo_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )

            hits = response.points

        except Exception as exc:
            raise RuntimeError(f"Qdrant search failed: {exc}") from exc

    except Exception as exc:
        raise RuntimeError(f"Qdrant search failed: {exc}") from exc

    results: list[SearchResult] = []

    for hit in hits:
        payload = hit.payload or {}

        results.append(
            SearchResult(
                file_path=str(payload.get("file_path", "")),
                language=str(payload.get("language", "")),
                symbol_name=payload.get("symbol_name"),
                symbol_type=payload.get("symbol_type"),
                start_line=int(payload.get("start_line", 0) or 0),
                end_line=int(payload.get("end_line", 0) or 0),
                content=str(payload.get("content", "")),
                content_hash=str(payload.get("content_hash", "")),
                score=float(hit.score or 0.0),
                qdrant_point_id=str(hit.id),
            )
        )

    logger.info(
        "Search for repo %s returned %d results query=%r file_path=%r",
        repo_id,
        len(results),
        query[:80],
        file_path,
    )

    return results