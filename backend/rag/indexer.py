"""
rag/indexer.py — RAG indexing pipeline orchestrator.

Indexing flow (all synchronous — run via asyncio.to_thread):
  1. Read each indexed file from disk.
  2. Chunk the content semantically (chunker.py).
  3. Embed all chunks in batches (embeddings.py).
  4. Build Qdrant PointStructs with full metadata payloads.
  5. Upsert to Qdrant (vectorstore.py).
  6. Return IndexedChunkRecord list for SQLite storage by the async API layer.

Re-indexing:
  - Caller must recreate_collection() before calling run_indexing_sync().
  - This function always writes fresh points; it does not check for duplicates.

Safety:
  - Files that fail to read are logged and skipped (never crash the pipeline).
  - Empty chunk lists (empty files, parse failures) are skipped gracefully.
  - Vector dimension is validated against the collection before upserting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from rag.chunker import CodeChunk, chunk_file
from rag.embeddings import EmbeddingModel, embed_texts_batched
from rag.vectorstore import (
    QdrantClient,
    build_point,
    make_point_id,
    upsert_points_batch,
)

logger = logging.getLogger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class IndexedChunkRecord:
    """
    Minimal data needed to create a code_chunks SQLite row.
    Returned from run_indexing_sync for async DB insertion by the caller.
    """

    file_path: str
    language: str
    symbol_name: str | None
    symbol_type: str | None
    start_line: int
    end_line: int
    qdrant_point_id: str
    content_hash: str


@dataclass
class IndexingResult:
    """Summary statistics returned from a completed indexing run."""

    repo_id: str
    collection_name: str
    chunks_indexed: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    chunks_skipped_empty: int = 0


# ── Core pipeline ─────────────────────────────────────────────────────────────

def run_indexing_sync(
    repo_id: str,
    local_path: str,
    files: list[dict],           # [{file_path, language, content_hash}]
    embedding_model: EmbeddingModel,
    qdrant_client: QdrantClient,
    collection_name: str,
) -> tuple[list[IndexedChunkRecord], IndexingResult]:
    """
    Run the full RAG indexing pipeline for a repository.

    This function is intentionally synchronous — call via asyncio.to_thread()
    from an async context so the event loop remains unblocked.

    Args:
        repo_id:          Repository UUID (stored in every chunk payload).
        local_path:       Absolute path to the cloned repo on disk.
        files:            List of file dicts from SQLite indexed_files table.
        embedding_model:  LangChain-compatible embedding model.
        qdrant_client:    Connected Qdrant client (collection must already exist).
        collection_name:  Qdrant collection to write into.

    Returns:
        (list[IndexedChunkRecord], IndexingResult)
        The caller is responsible for inserting the records into SQLite.
    """
    root = Path(local_path).resolve()
    result = IndexingResult(repo_id=repo_id, collection_name=collection_name)
    db_records: list[IndexedChunkRecord] = []

    # ── Phase 1: Collect all chunks from disk ─────────────────────────────────
    all_chunks: list[CodeChunk] = []
    chunk_to_file: list[str] = []        # parallel list: which file each chunk came from

    for file_info in files:
        rel_path: str = file_info["file_path"]
        language: str = file_info.get("language") or "text"
        abs_path = (root / rel_path).resolve()

        # Safety: ensure path is inside repo root
        try:
            abs_path.relative_to(root)
        except ValueError:
            logger.warning("Skipping path outside repo root: %s", rel_path)
            result.files_skipped += 1
            continue

        # Read file
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", rel_path, exc)
            result.files_skipped += 1
            continue

        if not content or not content.strip():
            result.files_skipped += 1
            result.chunks_skipped_empty += 1
            continue

        # Chunk
        try:
            file_chunks = chunk_file(content, rel_path, language, repo_id)
        except Exception as exc:
            logger.warning("Chunking failed for %s: %s", rel_path, exc)
            result.files_skipped += 1
            continue

        if not file_chunks:
            result.chunks_skipped_empty += 1
            result.files_skipped += 1
            continue

        all_chunks.extend(file_chunks)
        chunk_to_file.extend([rel_path] * len(file_chunks))
        result.files_processed += 1

    if not all_chunks:
        logger.warning("No chunks generated for repo %s", repo_id)
        return [], result

    logger.info(
        "Embedding %d chunks from %d files for repo %s",
        len(all_chunks), result.files_processed, repo_id,
    )

    # ── Phase 2: Embed all chunks in batches ──────────────────────────────────
    texts = [chunk.content for chunk in all_chunks]

    try:
        embeddings = embed_texts_batched(embedding_model, texts)
    except Exception as exc:
        raise RuntimeError(f"Embedding failed: {exc}") from exc

    if len(embeddings) != len(all_chunks):
        raise RuntimeError(
            f"Embedding count mismatch: expected {len(all_chunks)}, "
            f"got {len(embeddings)}"
        )

    # ── Phase 3: Build Qdrant points ──────────────────────────────────────────
    points = []
    for chunk, vector in zip(all_chunks, embeddings):
        if not vector:
            logger.debug("Empty vector for chunk %s:%d, skipping", chunk.file_path, chunk.start_line)
            continue

        point_id = make_point_id()
        payload = chunk.to_metadata()  # includes repo_id, file_path, content, etc.

        points.append(build_point(point_id, vector, payload))

        db_records.append(
            IndexedChunkRecord(
                file_path=chunk.file_path,
                language=chunk.language,
                symbol_name=chunk.symbol_name,
                symbol_type=chunk.symbol_type,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                qdrant_point_id=point_id,
                content_hash=chunk.content_hash,
            )
        )

    # ── Phase 4: Upsert to Qdrant ─────────────────────────────────────────────
    try:
        total_upserted = upsert_points_batch(qdrant_client, collection_name, points)
    except Exception as exc:
        raise RuntimeError(
            f"Qdrant upsert failed for collection '{collection_name}': {exc}"
        ) from exc

    result.chunks_indexed = total_upserted

    logger.info(
        "Indexing complete for repo %s: %d chunks upserted to '%s'",
        repo_id, total_upserted, collection_name,
    )
    return db_records, result
