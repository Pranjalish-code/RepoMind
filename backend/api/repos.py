"""
api/repos.py — Repository import, listing, detail, indexing, search, and PR review endpoints.

Routes:
    POST /repos/import                         — Import a GitHub repository (clone + scan)
    GET  /repos                                — List all imported repositories
    GET  /repos/{repo_id}                      — Get a single repository + its indexed files
    POST /repos/{repo_id}/index                — RAG-index a repository into Qdrant
    POST /repos/{repo_id}/search               — Semantic search over an indexed repository
    POST /repos/{repo_id}/file-review          — AI code review for a single file
    GET  /repos/{repo_id}/pulls                — List open/closed PRs from GitHub
    POST /repos/{repo_id}/pulls/{pr_number}/review — Run the full AI PR review pipeline
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import (
    bulk_create_code_chunks,
    bulk_create_indexed_files,
    create_repository,
    delete_code_chunks_for_repo,
    get_or_create_system_user,
    get_repository,
    get_repository_by_url,
    list_indexed_files,
    list_pr_reviews,
    list_repositories,
    update_repository_local_path,
    update_repository_status,
)
from db.session import get_db
from github.clone_repo import clone_repository, remove_clone, validate_github_url
from github.github_client import fetch_repo_metadata
from tools.repo_scanner import scan_repository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Repositories"])


# ══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ══════════════════════════════════════════════════════════════════════════════

class RepoImportRequest(BaseModel):
    """Request body for POST /repos/import."""

    repo_url: str = Field(
        ...,
        description="Full GitHub HTTPS URL, e.g. https://github.com/owner/repo",
        examples=["https://github.com/tiangolo/fastapi"],
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "Optional user UUID. "
            "If omitted the system user is used (pre-auth MVP mode)."
        ),
    )

    @field_validator("repo_url")
    @classmethod
    def must_be_github_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("https://github.com/"):
            raise ValueError("repo_url must start with https://github.com/")
        return v


class IndexedFileResponse(BaseModel):
    """Serialised IndexedFile record."""

    id: str
    file_path: str
    language: str | None
    content_hash: str | None
    total_chunks: int
    created_at: datetime

    model_config = {"from_attributes": True}


class RepoResponse(BaseModel):
    """Serialised Repository record."""

    id: str
    user_id: str
    repo_url: str
    repo_name: str
    default_branch: str
    local_path: str | None
    last_indexed_at: datetime | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RepoDetailResponse(RepoResponse):
    """Repository + its indexed files."""

    files: list[IndexedFileResponse] = Field(default_factory=list)
    file_count: int = 0


class RepoImportResponse(BaseModel):
    """Response from a successful import."""

    repo: RepoResponse
    files_indexed: int
    scan_summary: dict[str, Any]
    message: str


class IndexRequest(BaseModel):
    """Request body for POST /repos/{repo_id}/index."""

    force: bool = Field(
        default=False,
        description="Re-index even if a Qdrant collection already exists.",
    )


class IndexResponse(BaseModel):
    """Response from a successful RAG indexing run."""

    repo_id: str
    collection_name: str
    chunks_indexed: int
    files_processed: int
    files_skipped: int
    message: str


class SearchRequest(BaseModel):
    """Request body for POST /repos/{repo_id}/search."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Natural-language or code search query.",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results (1-50).",
    )


class SearchResultItem(BaseModel):
    """A single search result chunk."""

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


class SearchResponse(BaseModel):
    """Response from POST /repos/{repo_id}/search."""

    repo_id: str
    query: str
    results: list[SearchResultItem]
    total: int


class FileReviewRequest(BaseModel):
    """Request body for POST /repos/{repo_id}/file-review."""

    file_path: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description=(
            "Repository-relative path to the file to review, "
            "e.g. 'backend/routes/auth.js'"
        ),
    )
    query: str = Field(
        default="Review this file for bugs, security issues, and code quality problems.",
        max_length=1000,
        description="Reviewer's focus or specific question about the file.",
    )


class FileReviewIssue(BaseModel):
    """A single issue in the file review."""

    index: int
    title: str
    line: int | None
    severity: str               # High | Medium | Low
    problem: str
    impact: str
    suggested_fix: str


class FileReviewResponse(BaseModel):
    """Response from POST /repos/{repo_id}/file-review."""

    repo_id: str
    file: str
    language: str | None
    summary: str
    issues: list[FileReviewIssue]
    issue_count: int
    severity_counts: dict[str, int]
    final_recommendation: str
    formatted_review: str        # Full markdown output


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

async def _resolve_user_id(user_id: str | None, db: AsyncSession) -> str:
    """Return provided user_id or create/fetch the system user."""
    if user_id:
        return user_id
    system_user = await get_or_create_system_user(db)
    return system_user.id


async def _abort_import(
    db: AsyncSession,
    repo_id: str,
    local_path: str | None,
    detail: str,
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
) -> None:
    """
    Set repo status to 'error', clean up disk clone, and raise HTTPException.
    Ensures no orphaned records with status='cloning' remain after a failure.
    """
    try:
        await update_repository_status(db, repo_id, "error")
        await db.commit()
    except Exception:
        logger.exception("Failed to mark repo %s as error in DB", repo_id)

    if local_path:
        remove_clone(local_path)

    raise HTTPException(status_code=http_status, detail=detail)


def _get_rag_components():
    """
    Lazy-import and return (embedding_model, vector_dim, qdrant_client).

    Raises HTTPException 503 if:
      - No embedding API key is configured.
      - Qdrant server is unreachable.
    """
    try:
        from rag.embeddings import get_embeddings
        embedding_model, vector_dim = get_embeddings()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Embedding provider not configured: {exc}",
        )

    try:
        from rag.vectorstore import check_qdrant_connection, get_qdrant_client
        qdrant_client = get_qdrant_client()
        check_qdrant_connection(qdrant_client)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    return embedding_model, vector_dim, qdrant_client


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/import",
    response_model=RepoImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a GitHub repository",
)
async def import_repository(
    body: RepoImportRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RepoImportResponse:

    repo_url = body.repo_url.strip().rstrip("/")
    local_path: str | None = None
    repo_id: str | None = None

    # ── 1. Validate URL ───────────────────────────────────────────────────────
    try:
        owner, repo_name = validate_github_url(repo_url)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    # ── 2. Duplicate check ────────────────────────────────────────────────────
    existing = await get_repository_by_url(db, repo_url)
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Repository '{repo_url}' was already imported "
            f"(id={existing.id}, status={existing.status}). "
            "Use the existing repo or delete it first.",
        )

    # ── 3. GitHub metadata ────────────────────────────────────────────────────
    try:
        meta = await asyncio.to_thread(fetch_repo_metadata, owner, repo_name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except Exception as exc:
        logger.exception("GitHub API error for %s/%s", owner, repo_name)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not fetch GitHub metadata: {exc}")

    clone_url = meta["clone_url"] or repo_url
    default_branch: str = meta["default_branch"]
    canonical_name: str = meta["name"]

    # ── 4. Create DB record ───────────────────────────────────────────────────
    user_id = await _resolve_user_id(body.user_id, db)
    repo = await create_repository(
        db, user_id=user_id, repo_url=repo_url,
        repo_name=canonical_name, default_branch=default_branch,
    )
    await update_repository_status(db, repo.id, "cloning")
    await db.commit()
    await db.refresh(repo)
    repo_id = repo.id

    # ── 5. Clone ──────────────────────────────────────────────────────────────
    try:
        cloned_path = await asyncio.to_thread(
            clone_repository, clone_url, repo_id, canonical_name, default_branch,
        )
        local_path = str(cloned_path)
    except ValueError as exc:
        await _abort_import(db, repo_id, None, str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
    except Exception as exc:
        logger.exception("Clone failed for %s", clone_url)
        await _abort_import(db, repo_id, None, f"Failed to clone repository: {exc}")

    # ── 6. Persist local_path ─────────────────────────────────────────────────
    await update_repository_local_path(db, repo_id, local_path)
    await update_repository_status(db, repo_id, "indexing")
    await db.commit()

    # ── 7. Scan files ─────────────────────────────────────────────────────────
    try:
        scanned_files, scan_summary = await asyncio.to_thread(scan_repository, local_path)
    except Exception as exc:
        logger.exception("Scan failed for %s", local_path)
        await _abort_import(db, repo_id, local_path, f"Repository scan failed: {exc}")

    # ── 8. Bulk-insert IndexedFile rows ───────────────────────────────────────
    await bulk_create_indexed_files(
        db, repo_id,
        [{"file_path": sf.file_path, "language": sf.language,
          "content_hash": sf.content_hash, "total_chunks": 0}
         for sf in scanned_files],
    )

    # ── 9. Mark ready ─────────────────────────────────────────────────────────
    await update_repository_status(db, repo_id, "ready")
    await db.commit()
    await db.refresh(repo)

    logger.info("Import complete: %s (id=%s, files=%d)", canonical_name, repo_id, len(scanned_files))

    return RepoImportResponse(
        repo=RepoResponse.model_validate(repo),
        files_indexed=len(scanned_files),
        scan_summary={
            "total_indexed":     scan_summary.total_indexed,
            "skipped_ignored":   scan_summary.skipped_ignored,
            "skipped_extension": scan_summary.skipped_extension,
            "skipped_large":     scan_summary.skipped_large,
            "skipped_binary":    scan_summary.skipped_binary,
            "skipped_empty":     scan_summary.skipped_empty,
            "skipped_error":     scan_summary.skipped_error,
        },
        message=f"Repository '{canonical_name}' imported successfully.",
    )


# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[RepoResponse], summary="List all repositories")
async def list_repos(
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RepoResponse]:
    system_user = await get_or_create_system_user(db)
    repos = await list_repositories(db, system_user.id, skip=skip, limit=limit)
    return [RepoResponse.model_validate(r) for r in repos]


# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{repo_id}", response_model=RepoDetailResponse, summary="Get a repository")
async def get_repo(
    repo_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    file_skip: Annotated[int, Query(ge=0)] = 0,
    file_limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> RepoDetailResponse:
    repo = await get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository '{repo_id}' not found.")

    files = await list_indexed_files(db, repo_id, skip=file_skip, limit=file_limit)
    file_responses = [IndexedFileResponse.model_validate(f) for f in files]

    return RepoDetailResponse(
        **RepoResponse.model_validate(repo).model_dump(),
        files=file_responses,
        file_count=len(file_responses),
    )


# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{repo_id}/index",
    response_model=IndexResponse,
    summary="RAG-index a repository into Qdrant",
    description=(
        "Chunks all scanned files, generates embeddings, and stores vectors "
        "in Qdrant. Pass force=true to re-index (deletes old vectors first)."
    ),
)
async def index_repository(
    repo_id: str,
    body: IndexRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> IndexResponse:

    # ── 1. Fetch repo ─────────────────────────────────────────────────────────
    repo = await get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository '{repo_id}' not found.")

    if repo.status not in ("ready", "error"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Repository is in status='{repo.status}'. Wait for 'ready'.",
        )

    if not repo.local_path:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Repository has no local_path. Re-import the repository first.",
        )

    # ── 2. RAG components ─────────────────────────────────────────────────────
    embedding_model, vector_dim, qdrant_client = _get_rag_components()

    from rag.vectorstore import collection_exists, collection_name_for, recreate_collection

    col = collection_name_for(repo_id)

    # ── 3. Re-index cleanup ───────────────────────────────────────────────────
    if collection_exists(qdrant_client, col):
        if not body.force:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Repository is already indexed (collection='{col}'). "
                "Pass force=true to re-index.",
            )
        deleted = await delete_code_chunks_for_repo(db, repo_id)
        await db.commit()
        logger.info("Deleted %d stale code_chunk rows for repo %s", deleted, repo_id)
    else:
        # Defensive: clear any stale SQLite chunks from a failed prior index
        await delete_code_chunks_for_repo(db, repo_id)
        await db.commit()

    # Recreate Qdrant collection (delete + create fresh to clear all stale vectors)
    await asyncio.to_thread(recreate_collection, qdrant_client, col, vector_dim)

    # ── 4. Fetch indexed files ────────────────────────────────────────────────
    indexed_files = await list_indexed_files(db, repo_id, limit=10_000)
    files_dicts = [
        {"file_path": f.file_path, "language": f.language or "text",
         "content_hash": f.content_hash}
        for f in indexed_files
    ]

    if not files_dicts:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No indexed files found. Import the repository first.",
        )

    # ── 5. Run indexing pipeline in thread pool ───────────────────────────────
    await update_repository_status(db, repo_id, "indexing")
    await db.commit()

    from rag.indexer import run_indexing_sync

    try:
        db_records, index_result = await asyncio.to_thread(
            run_indexing_sync,
            repo_id,
            repo.local_path,
            files_dicts,
            embedding_model,
            qdrant_client,
            col,
        )
    except Exception as exc:
        logger.exception("Indexing failed for repo %s", repo_id)
        await update_repository_status(db, repo_id, "error")
        await db.commit()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Indexing failed: {exc}")

    # ── 6. Bulk-insert code_chunks into SQLite ────────────────────────────────
    await bulk_create_code_chunks(
        db, repo_id,
        [
            {
                "file_path":       r.file_path,
                "language":        r.language,
                "symbol_name":     r.symbol_name,
                "symbol_type":     r.symbol_type,
                "start_line":      r.start_line,
                "end_line":        r.end_line,
                "qdrant_point_id": r.qdrant_point_id,
                "content_hash":    r.content_hash,
            }
            for r in db_records
        ],
    )

    # ── 7. Mark ready ─────────────────────────────────────────────────────────
    await update_repository_status(db, repo_id, "ready")
    await db.commit()

    logger.info(
        "RAG indexing complete: repo=%s chunks=%d collection=%s",
        repo_id, index_result.chunks_indexed, col,
    )

    return IndexResponse(
        repo_id=repo_id,
        collection_name=col,
        chunks_indexed=index_result.chunks_indexed,
        files_processed=index_result.files_processed,
        files_skipped=index_result.files_skipped,
        message=(
            f"Indexed {index_result.chunks_indexed} chunks "
            f"from {index_result.files_processed} files into '{col}'."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{repo_id}/search",
    response_model=SearchResponse,
    summary="Semantic search over an indexed repository",
    description=(
        "Embeds the query and retrieves the most similar code chunks from "
        "Qdrant, always filtered by repo_id."
    ),
)
async def search_repository(
    repo_id: str,
    body: SearchRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SearchResponse:

    repo = await get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository '{repo_id}' not found.")

    embedding_model, _dim, qdrant_client = _get_rag_components()

    from rag.retriever import search_codebase_sync

    try:
        results = await asyncio.to_thread(
            search_codebase_sync,
            body.query,
            repo_id,
            embedding_model,
            qdrant_client,
            body.limit,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except Exception as exc:
        logger.exception("Search failed for repo %s", repo_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Search failed: {exc}")

    return SearchResponse(
        repo_id=repo_id,
        query=body.query,
        results=[
            SearchResultItem(
                file_path=r.file_path,
                language=r.language,
                symbol_name=r.symbol_name,
                symbol_type=r.symbol_type,
                start_line=r.start_line,
                end_line=r.end_line,
                content=r.content,
                content_hash=r.content_hash,
                score=r.score,
                qdrant_point_id=r.qdrant_point_id,
            )
            for r in results
        ],
        total=len(results),
    )


# ─────────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{repo_id}/file-review",
    response_model=FileReviewResponse,
    summary="AI code review for a single repository file",
    description=(
        "Reads a file from the cloned repository, validates it for safety "
        "(no path traversal, no .env, no binary files), then runs an AI code "
        "review checking for bugs, security risks, missing validation, "
        "performance issues, and code quality problems."
    ),
)
async def file_review(
    repo_id: str,
    body: FileReviewRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileReviewResponse:
    """
    POST /repos/{repo_id}/file-review

    Request body::

        {
            "file_path": "backend/routes/auth.js",
            "query": "Review this file for bugs"
        }

    Validates the file is inside the repository, safe to read, then
    invokes the LangGraph file_review_agent node and returns a structured
    review with issues, severities, and line numbers.
    """
    # ── 1. Validate repository ────────────────────────────────────────────────────────
    repo = await get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository '{repo_id}' not found.")

    if repo.status not in ("ready", "indexing"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Repository status is '{repo.status}'. "
            "The repository must be in 'ready' status to review files.",
        )

    if not repo.local_path:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Repository has no local clone. Please re-import the repository.",
        )

    # ── 2. Resolve user ───────────────────────────────────────────────────────────
    user_id = await _resolve_user_id(None, db)

    # ── 3. Build AgentState and invoke graph ──────────────────────────────────
    from graph.graph import graph
    from graph.state import AgentState

    initial_state: AgentState = {
        "user_id":            user_id,
        "repo_id":            repo_id,
        "query":              body.query,
        "intent":             "file_review",    # bypass classifier
        "selected_file":      body.file_path,
        "pr_number":          None,
        "indexed_files":      [],
        "changed_files":      [],
        "retrieved_chunks":   [],
        "related_files":      [],
        "draft_response":     "",
        "draft_review":       {},
        "final_response":     "",
        "diagram_mermaid":    "",
        "diagram_explanation": "",
        "diagram_confidence": 0,
        "guardrail_result":   {"passed": True},  # API layer already validated
        "error":              None,
    }

    try:
        final_state: AgentState = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.exception("Graph execution failed for file-review: %s", exc)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Review failed: {exc}",
        )

    # ── 4. Extract results ─────────────────────────────────────────────────────────
    draft_review: dict = final_state.get("draft_review", {})
    final_response: str = final_state.get("final_response", "")
    err = final_state.get("error")

    # Propagate file access errors as HTTP errors
    if err == "file_access_denied":
        raise HTTPException(status.HTTP_403_FORBIDDEN, final_response)
    if err == "repo_not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, final_response)
    if err in ("missing_local_path", "missing_selected_file"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, final_response)

    # Build issues list (filter out internal _error key)
    raw_issues = draft_review.get("issues", [])
    from tools.repo_scanner import SUPPORTED_EXTENSIONS
    from pathlib import Path
    file_suffix = Path(body.file_path).suffix.lower()
    language = SUPPORTED_EXTENSIONS.get(file_suffix)

    return FileReviewResponse(
        repo_id=repo_id,
        file=draft_review.get("file", body.file_path),
        language=language,
        summary=draft_review.get("summary", ""),
        issues=[
            FileReviewIssue(
                index=iss.get("index", i + 1),
                title=iss.get("title", ""),
                line=iss.get("line"),
                severity=iss.get("severity", "Low"),
                problem=iss.get("problem", ""),
                impact=iss.get("impact", ""),
                suggested_fix=iss.get("suggested_fix", ""),
            )
            for i, iss in enumerate(raw_issues)
        ],
        issue_count=draft_review.get("issue_count", len(raw_issues)),
        severity_counts=draft_review.get(
            "severity_counts", {"High": 0, "Medium": 0, "Low": 0}
        ),
        final_recommendation=draft_review.get("final_recommendation", ""),
        formatted_review=final_response,
    )
@router.delete(
    "/{repo_id}",
    summary="Delete an imported repository",
)
async def delete_repo(
    repo_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    repo = await get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository '{repo_id}' not found.")

    local_path = repo.local_path

    # delete Qdrant collection
    try:
        from rag.vectorstore import get_qdrant_client, collection_name_for
        qdrant_client = get_qdrant_client()
        col = collection_name_for(repo_id)
        qdrant_client.delete_collection(collection_name=col)
    except Exception as exc:
        logger.warning("Could not delete Qdrant collection for repo %s: %s", repo_id, exc)

    # delete local clone
    if local_path:
        try:
            remove_clone(local_path)
        except Exception as exc:
            logger.warning("Could not delete local clone for repo %s: %s", repo_id, exc)

    # delete DB rows
    try:
        await delete_code_chunks_for_repo(db, repo_id)
        await db.delete(repo)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("Failed to delete repo %s", repo_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Delete failed: {exc}")

    return {
        "repo_id": repo_id,
        "message": "Repository deleted successfully."
    }

# ══════════════════════════════════════════════════════════════════════════════
# PR Review Schemas
# ══════════════════════════════════════════════════════════════════════════════

class PRListItem(BaseModel):
    """Summary of a single pull request from GitHub."""

    number: int
    title: str
    state: str
    draft: bool
    user_login: str
    head_ref: str
    base_ref: str
    html_url: str
    created_at: str
    updated_at: str
    body_preview: str
    additions: int
    deletions: int
    changed_files: int


class PRListResponse(BaseModel):
    """Response from GET /repos/{repo_id}/pulls."""

    repo_id: str
    repo_url: str
    state_filter: str
    page: int
    per_page: int
    pull_requests: list[PRListItem]
    total: int


class PRReviewRequest(BaseModel):
    """Request body for POST /repos/{repo_id}/pulls/{pr_number}/review."""

    query: str = Field(
        default="Review this PR for bugs, security issues, and breaking changes.",
        max_length=1000,
        description="Reviewer's focus or specific question about the PR.",
    )


class PRReviewIssue(BaseModel):
    """A single issue in the PR review."""

    title: str
    file: str
    line: int | None
    severity: str
    evidence: str
    problem: str
    impact: str
    suggested_fix: str


class PRReviewResult(BaseModel):
    """Response from POST /repos/{repo_id}/pulls/{pr_number}/review."""

    repo_id: str
    pr_number: int
    status: str                    # Safe to merge | Needs changes | Risky PR
    risk_score: int
    summary: str
    issues: list[PRReviewIssue]
    issue_count: int
    severity_counts: dict[str, int]
    final_recommendation: str
    formatted_review: str
    review_db_id: str | None       # SQLite UUID if saved


# ══════════════════════════════════════════════════════════════════════════════
# PR Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/{repo_id}/pulls",
    response_model=PRListResponse,
    summary="List pull requests from GitHub",
    description=(
        "Fetches open (or closed/all) pull requests directly from the GitHub API "
        "for the given repository. Requires GITHUB_TOKEN to be set in .env for "
        "private repos and higher rate limits."
    ),
)
async def list_pull_requests(
    repo_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    state: Annotated[str, Query(pattern="^(open|closed|all)$")] = "open",
    per_page: Annotated[int, Query(ge=1, le=100)] = 30,
    page: Annotated[int, Query(ge=1)] = 1,
) -> PRListResponse:
    """
    GET /repos/{repo_id}/pulls

    Lists pull requests from GitHub for the imported repository.
    The repository must be in 'ready' status (or 'indexing') and have a valid repo_url.
    """
    # ── 1. Fetch repo ──────────────────────────────────────────────────────────
    repo = await get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository '{repo_id}' not found.")

    if repo.status not in ("ready", "indexing"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Repository status is '{repo.status}'. Must be 'ready' to list PRs.",
        )

    # ── 2. Fetch PRs from GitHub ───────────────────────────────────────────────
    try:
        from github.pr_fetcher import fetch_pull_requests
        pr_list = await asyncio.to_thread(
            fetch_pull_requests,
            repo.repo_url,
            state,
            per_page,
            page,
        )
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"GitHub API error: {exc}",
        )
    except Exception as exc:
        logger.exception("Failed to list PRs for repo %s", repo_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Failed to fetch pull requests: {exc}",
        )

    return PRListResponse(
        repo_id=repo_id,
        repo_url=repo.repo_url,
        state_filter=state,
        page=page,
        per_page=per_page,
        pull_requests=[
            PRListItem(**pr) for pr in pr_list
        ],
        total=len(pr_list),
    )


# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{repo_id}/pulls/{pr_number}/review",
    response_model=PRReviewResult,
    summary="Run an AI review on a GitHub pull request",
    description=(
        "Fetches the PR diff from GitHub, retrieves related code from Qdrant, "
        "runs the LLM analysis, applies PR guardrails (file validation, line "
        "validation, secret redaction), formats the structured review, and saves "
        "it to SQLite. Does NOT post comments to GitHub or apply patches."
    ),
)
async def review_pull_request(
    repo_id: str,
    pr_number: int,
    body: PRReviewRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PRReviewResult:
    """
    POST /repos/{repo_id}/pulls/{pr_number}/review

    Invokes the full PR review LangGraph pipeline:
      pr_review_agent → pr_guardrail → review_formatter → output_guardrail

    Returns a structured review with risk score, issues, and a formatted
    markdown report. Secrets are redacted; fake file references are removed.
    """
    # ── 1. Validate repo ───────────────────────────────────────────────────────
    repo = await get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository '{repo_id}' not found.")

    if repo.status not in ("ready", "indexing"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Repository status is '{repo.status}'. Must be 'ready' to review PRs.",
        )

    if pr_number <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "pr_number must be a positive integer.",
        )

    # ── 2. Resolve user ────────────────────────────────────────────────────────
    user_id = await _resolve_user_id(None, db)

    # ── 3. Build initial AgentState and invoke the LangGraph pipeline ──────────
    from graph.graph import graph
    from graph.state import AgentState

    initial_state: AgentState = {
        "user_id":             user_id,
        "repo_id":             repo_id,
        "query":               body.query,
        "intent":              "pr_review",   # bypass classifier
        "selected_file":       None,
        "pr_number":           pr_number,
        "indexed_files":       [],
        "changed_files":       [],
        "retrieved_chunks":    [],
        "related_files":       [],
        "draft_response":      "",
        "draft_review":        {},
        "final_response":      "",
        "diagram_mermaid":     "",
        "diagram_explanation": "",
        "diagram_confidence":  0,
        "guardrail_result":    {"passed": True},  # API layer already validated input
        "error":               None,
    }

    try:
        final_state: AgentState = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.exception("Graph execution failed for PR review: repo=%s pr=%d", repo_id, pr_number)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"PR review failed: {exc}",
        )

    # ── 4. Extract results from final state ────────────────────────────────────
    draft_review: dict = final_state.get("draft_review", {})
    final_response: str = final_state.get("final_response", "")
    guardrail_ctx: dict = final_state.get("guardrail_result", {})
    err = final_state.get("error")

    # Propagate known errors as HTTP errors
    if err == "missing_pr_number":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, final_response)
    if err == "repo_not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, final_response)
    if err == "github_api_error":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, final_response)
    if err == "db_error":
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, final_response)

    # ── 5. Build structured response ──────────────────────────────────────────
    raw_issues = draft_review.get("issues", [])

    # Count severities
    severity_counts: dict[str, int] = {"High": 0, "Medium": 0, "Low": 0}
    for iss in raw_issues:
        sev = iss.get("severity", "Low")
        if sev in severity_counts:
            severity_counts[sev] += 1

    review_db_id: str | None = guardrail_ctx.get("review_db_id")

    return PRReviewResult(
        repo_id=repo_id,
        pr_number=pr_number,
        status=draft_review.get("status", "Needs changes"),
        risk_score=int(draft_review.get("risk_score", 0)),
        summary=draft_review.get("summary", ""),
        issues=[
            PRReviewIssue(
                title=iss.get("title", ""),
                file=iss.get("file", ""),
                line=iss.get("line"),
                severity=iss.get("severity", "Low"),
                evidence=iss.get("evidence", ""),
                problem=iss.get("problem", ""),
                impact=iss.get("impact", ""),
                suggested_fix=iss.get("suggested_fix", ""),
            )
            for iss in raw_issues
        ],
        issue_count=len(raw_issues),
        severity_counts=severity_counts,
        final_recommendation=draft_review.get("final_recommendation", ""),
        formatted_review=final_response,
        review_db_id=review_db_id,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Architecture Diagram Schemas
# ══════════════════════════════════════════════════════════════════════════════

class ArchitectureGenerateRequest(BaseModel):
    """Request body for POST /repos/{repo_id}/architecture/generate."""

    query: str = Field(
        default="Generate an architecture diagram for this repository.",
        max_length=500,
        description="Optional user query or focus area (e.g. 'focus on the auth flow').",
    )


class DetectedComponent(BaseModel):
    """A single detected component in the architecture."""
    name: str
    kind: str
    label: str
    evidence: list[str]


class ArchitectureDiagramResponse(BaseModel):
    """Response from POST /repos/{repo_id}/architecture/generate."""
    repo_id: str
    repo_name: str
    confidence: int                     # 0-100
    mermaid_code: str
    explanation: str
    formatted_output: str              # Full formatted markdown
    detected_components: list[DetectedComponent]
    component_count: int
    diagram_db_id: str | None          # SQLite record UUID
    note: str                          # Always present disclaimer


class ArchitectureListItem(BaseModel):
    """Summary of a stored architecture diagram."""
    id: str
    repo_id: str
    confidence_score: float | None
    component_count: int
    created_at: str
    mermaid_preview: str               # First 200 chars of Mermaid code


# ══════════════════════════════════════════════════════════════════════════════
# Architecture Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{repo_id}/architecture/generate",
    response_model=ArchitectureDiagramResponse,
    summary="Generate an architecture diagram for a repository",
    description=(
        "Runs static code analysis on the repository to detect components "
        "(frontend, backend, database, auth, services), builds a component graph, "
        "calls the LLM ONLY to convert the detected graph into Mermaid.js, "
        "validates the diagram, and stores it in SQLite. "
        "Confidence score and disclaimer are always included. "
        "The LLM is not allowed to invent components not found by analysis."
    ),
)
async def generate_architecture(
    repo_id: str,
    body: ArchitectureGenerateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ArchitectureDiagramResponse:
    """
    POST /repos/{repo_id}/architecture/generate

    Invokes the LangGraph architecture pipeline:
      architecture_agent → mermaid_validator → output_guardrail

    Returns a Mermaid diagram, explanation, and confidence score.
    """
    # ── 1. Validate repo ───────────────────────────────────────────────────────
    repo = await get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository '{repo_id}' not found.")

    if repo.status not in ("ready", "indexing"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Repository status is '{repo.status}'. Must be 'ready' to generate architecture.",
        )

    if not repo.local_path:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Repository has no local clone. Please re-import the repository.",
        )

    # ── 2. Resolve user ────────────────────────────────────────────────────────
    user_id = await _resolve_user_id(None, db)

    # ── 3. Build initial state and invoke LangGraph ────────────────────────────
    from graph.graph import graph
    from graph.state import AgentState

    initial_state: AgentState = {
        "user_id":             user_id,
        "repo_id":             repo_id,
        "query":               body.query,
        "intent":              "architecture",   # bypass classifier
        "selected_file":       None,
        "pr_number":           None,
        "indexed_files":       [],
        "changed_files":       [],
        "retrieved_chunks":    [],
        "related_files":       [],
        "draft_response":      "",
        "draft_review":        {},
        "final_response":      "",
        "diagram_mermaid":     "",
        "diagram_explanation": "",
        "diagram_confidence":  0,
        "guardrail_result":    {"passed": True},
        "error":               None,
    }

    try:
        final_state: AgentState = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.exception("Architecture graph failed for repo %s", repo_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Architecture generation failed: {exc}",
        )

    # ── 4. Extract results ─────────────────────────────────────────────────────
    draft_review    = final_state.get("draft_review", {})
    mermaid_code    = final_state.get("diagram_mermaid", "")
    explanation     = final_state.get("diagram_explanation", "")
    confidence      = int(final_state.get("diagram_confidence", 0))
    final_response  = final_state.get("final_response", "")
    guardrail_ctx   = final_state.get("guardrail_result", {})
    err             = final_state.get("error")

    if err in ("repo_not_found", "missing_local_path"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, final_response)
    if err == "db_error":
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, final_response)
    if err == "analysis_error":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, final_response)

    # ── 5. Build response ──────────────────────────────────────────────────────
    raw_components: list[dict] = draft_review.get("detected_components_json", [])
    diagram_db_id: str | None = guardrail_ctx.get("diagram_db_id")
    repo_name = draft_review.get("repo_name", repo.repo_name)

    return ArchitectureDiagramResponse(
        repo_id=repo_id,
        repo_name=repo_name,
        confidence=confidence,
        mermaid_code=mermaid_code,
        explanation=explanation,
        formatted_output=final_response,
        detected_components=[
            DetectedComponent(
                name=c.get("name", ""),
                kind=c.get("kind", ""),
                label=c.get("label", ""),
                evidence=c.get("evidence", []),
            )
            for c in raw_components
        ],
        component_count=len(raw_components),
        diagram_db_id=diagram_db_id,
        note=(
            "This diagram is AI-assisted and based on static analysis only. "
            "It may not capture all runtime dependencies or dynamic behavior. "
            "Manual verification is recommended."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{repo_id}/architecture",
    response_model=list[ArchitectureListItem],
    summary="List stored architecture diagrams for a repository",
    description=(
        "Returns all previously generated architecture diagrams for the repository, "
        "ordered by creation date descending."
    ),
)
async def list_architecture_diagrams(
    repo_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[ArchitectureListItem]:
    """
    GET /repos/{repo_id}/architecture

    Lists all saved architecture diagrams for the given repo.
    Returns a summary list with mermaid previews.
    """
    # ── 1. Validate repo ───────────────────────────────────────────────────────
    repo = await get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository '{repo_id}' not found.")

    # ── 2. List diagrams ───────────────────────────────────────────────────────
    from db.crud import list_architecture_diagrams as _list_diagrams

    diagrams = await _list_diagrams(db, repo_id, skip=skip, limit=limit)

    return [
        ArchitectureListItem(
            id=str(d.id),
            repo_id=str(d.repo_id),
            confidence_score=d.confidence_score,
            component_count=len(d.detected_components_json or []),
            created_at=d.created_at.isoformat() if d.created_at else "",
            mermaid_preview=d.mermaid_code[:200] if d.mermaid_code else "",
        )
        for d in diagrams
    ]
