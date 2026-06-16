"""
rag/vectorstore.py — Qdrant collection management for RepoMind AI.

Responsibilities:
  - Build and cache a QdrantClient connected to settings.qdrant_url.
  - Derive collection names from repo IDs.
  - Create / recreate Qdrant collections with correct vector config.
  - Create a payload index on 'repo_id' for fast filtered search.
  - Batch upsert PointStruct objects.
  - Check collection existence.
  - Delete collections (re-index cleanup).

All functions are synchronous — call via asyncio.to_thread() from async context.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

from config import settings

logger = logging.getLogger(__name__)

# ── Batch size for Qdrant upserts ─────────────────────────────────────────────
UPSERT_BATCH_SIZE: int = 100

# ── Client singleton ──────────────────────────────────────────────────────────
_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    """
    Return a cached QdrantClient.

    Uses settings.qdrant_url and optionally settings.qdrant_api_key.
    Raises ConnectionError (propagated from Qdrant) if server is unreachable.
    """
    global _client
    if _client is None:
        api_key = settings.qdrant_api_key.strip() or None
        _client = QdrantClient(
            url=settings.qdrant_url,
            api_key=api_key,
            timeout=60,
        )
        logger.info("QdrantClient initialised: %s", settings.qdrant_url)
    return _client


def reset_client() -> None:
    """Clear the cached client (useful in tests)."""
    global _client
    _client = None


# ── Collection name ───────────────────────────────────────────────────────────

def collection_name_for(repo_id: str) -> str:
    """
    Derive the Qdrant collection name for a repository.

    Format: ``repo_{repo_id}_code``
    Hyphens in the UUID are replaced with underscores because some Qdrant
    versions handle collection names with hyphens inconsistently.
    """
    safe_id = repo_id.replace("-", "_")
    return f"repo_{safe_id}_code"


# ── Collection lifecycle ──────────────────────────────────────────────────────

def collection_exists(client: QdrantClient, name: str) -> bool:
    """Return True if a Qdrant collection with the given name exists."""
    try:
        return client.collection_exists(name)
    except Exception:
        return False


def create_collection(
    client: QdrantClient,
    name: str,
    vector_size: int,
) -> None:
    """
    Create a new Qdrant collection with COSINE distance.

    Also creates a keyword payload index on the 'repo_id' field
    for O(1) filtered search performance.

    Raises:
        UnexpectedResponse: if Qdrant returns an error (e.g. already exists).
    """
    client.create_collection(
        collection_name=name,
        vectors_config=qmodels.VectorParams(
            size=vector_size,
            distance=qmodels.Distance.COSINE,
            on_disk=False,
        ),
        # Build HNSW index immediately (better for small collections)
        optimizers_config=qmodels.OptimizersConfigDiff(
            indexing_threshold=0,
        ),
    )
    logger.info("Created Qdrant collection: %s (dim=%d)", name, vector_size)

    # Create a keyword index on repo_id for fast filtered search
    try:
        client.create_payload_index(
            collection_name=name,
            field_name="repo_id",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
        logger.debug("Created payload index on 'repo_id' for %s", name)
    except Exception as exc:
        # Non-fatal — search will still work, just slower
        logger.warning("Could not create payload index for %s: %s", name, exc)


def recreate_collection(
    client: QdrantClient,
    name: str,
    vector_size: int,
) -> None:
    """
    Delete the collection if it exists, then create it fresh.

    Used on re-index to guarantee no stale vectors remain.
    """
    if collection_exists(client, name):
        client.delete_collection(name)
        logger.info("Deleted existing collection: %s", name)
    create_collection(client, name, vector_size)


def ensure_collection(
    client: QdrantClient,
    name: str,
    vector_size: int,
) -> None:
    """Create the collection only if it does not already exist."""
    if not collection_exists(client, name):
        create_collection(client, name, vector_size)


def delete_collection(client: QdrantClient, name: str) -> bool:
    """Delete a collection. Returns True if deleted, False if not found."""
    if collection_exists(client, name):
        client.delete_collection(name)
        logger.info("Deleted collection: %s", name)
        return True
    return False


# ── Point construction ────────────────────────────────────────────────────────

def make_point_id() -> str:
    """Generate a new UUID4 string suitable as a Qdrant point ID."""
    return str(uuid.uuid4())


def build_point(
    point_id: str,
    vector: list[float],
    payload: dict[str, Any],
) -> qmodels.PointStruct:
    """Build a Qdrant PointStruct from components."""
    return qmodels.PointStruct(
        id=point_id,
        vector=vector,
        payload=payload,
    )


# ── Batch upsert ──────────────────────────────────────────────────────────────

def upsert_points_batch(
    client: QdrantClient,
    collection_name: str,
    points: list[qmodels.PointStruct],
    batch_size: int = UPSERT_BATCH_SIZE,
) -> int:
    """
    Upsert points into Qdrant in fixed-size batches.

    Args:
        client:          Connected QdrantClient.
        collection_name: Target collection.
        points:          All PointStruct objects to upsert.
        batch_size:      Points per Qdrant API call.

    Returns:
        Total number of points upserted.
    """
    if not points:
        return 0

    total = 0
    for start in range(0, len(points), batch_size):
        batch = points[start : start + batch_size]
        client.upsert(collection_name=collection_name, points=batch, wait=True)
        total += len(batch)
        logger.debug("Upserted batch %d-%d to %s", start, start + len(batch), collection_name)

    return total


# ── Connectivity check ────────────────────────────────────────────────────────

def check_qdrant_connection(client: QdrantClient) -> None:
    """
    Verify that the Qdrant server is reachable.

    Raises:
        RuntimeError: with a descriptive message if the server is unreachable.
    """
    try:
        client.get_collections()
    except Exception as exc:
        raise RuntimeError(
            f"Qdrant server is not reachable at '{settings.qdrant_url}'. "
            f"Start Qdrant with: docker run -p 6333:6333 qdrant/qdrant\n"
            f"Error: {exc}"
        ) from exc
