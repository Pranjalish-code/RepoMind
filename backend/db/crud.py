"""
db/crud.py — Async CRUD helpers for all ORM models.

Each function accepts an AsyncSession and returns typed ORM objects or None.
Pagination uses limit/offset throughout.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    ArchitectureDiagram,
    ChatMessage,
    CodeChunk,
    IndexedFile,
    PRReview,
    Repository,
    User,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# Users
# ═══════════════════════════════════════════════════════════════════════════════

async def create_user(db: AsyncSession, *, name: str, email: str) -> User:
    user = User(name=name, email=email)
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def get_user(db: AsyncSession, user_id: str) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def list_users(
    db: AsyncSession, *, skip: int = 0, limit: int = 100
) -> Sequence[User]:
    result = await db.execute(select(User).offset(skip).limit(limit))
    return result.scalars().all()


async def get_or_create_system_user(db: AsyncSession) -> User:
    """
    Return the singleton system user, creating it on first call.

    The system user represents the default authenticated identity used
    before real auth (OAuth / JWT) is wired up in a later step.
    """
    system_email = "system@repomind.ai"
    user = await get_user_by_email(db, system_email)
    if user is None:
        user = await create_user(
            db, name="System User", email=system_email
        )
        await db.commit()
        await db.refresh(user)
    return user


# ═══════════════════════════════════════════════════════════════════════════════
# Repositories
# ═══════════════════════════════════════════════════════════════════════════════

async def create_repository(
    db: AsyncSession,
    *,
    user_id: str,
    repo_url: str,
    repo_name: str,
    default_branch: str = "main",
) -> Repository:
    repo = Repository(
        user_id=user_id,
        repo_url=repo_url,
        repo_name=repo_name,
        default_branch=default_branch,
    )
    db.add(repo)
    await db.flush()
    await db.refresh(repo)
    return repo


async def get_repository(db: AsyncSession, repo_id: str) -> Repository | None:
    result = await db.execute(
        select(Repository).where(Repository.id == repo_id)
    )
    return result.scalar_one_or_none()


async def list_repositories(
    db: AsyncSession, user_id: str, *, skip: int = 0, limit: int = 100
) -> Sequence[Repository]:
    result = await db.execute(
        select(Repository)
        .where(Repository.user_id == user_id)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


async def get_repository_by_url(
    db: AsyncSession, repo_url: str
) -> Repository | None:
    """
    Find a repository by its canonical GitHub URL.

    Used to detect duplicate imports before cloning starts, avoiding
    wasted disk space and conflicting DB records.
    """
    # Normalise: strip trailing slash and .git suffix for comparison
    normalised = repo_url.strip().rstrip("/")
    if normalised.endswith(".git"):
        normalised = normalised[:-4]

    result = await db.execute(
        select(Repository).where(
            # Match both with and without .git
            (Repository.repo_url == normalised)
            | (Repository.repo_url == normalised + ".git")
        )
    )
    return result.scalar_one_or_none()


async def update_repository_status(
    db: AsyncSession, repo_id: str, status: str
) -> Repository | None:
    repo = await get_repository(db, repo_id)
    if repo is None:
        return None
    repo.status = status
    if status == "ready":
        repo.last_indexed_at = _now()
    await db.flush()
    await db.refresh(repo)
    return repo


async def update_repository_local_path(
    db: AsyncSession, repo_id: str, local_path: str
) -> Repository | None:
    repo = await get_repository(db, repo_id)
    if repo is None:
        return None
    repo.local_path = local_path
    await db.flush()
    await db.refresh(repo)
    return repo


async def delete_repository(db: AsyncSession, repo_id: str) -> bool:
    repo = await get_repository(db, repo_id)
    if repo is None:
        return False
    await db.delete(repo)
    await db.flush()
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# IndexedFiles
# ═══════════════════════════════════════════════════════════════════════════════

async def create_indexed_file(
    db: AsyncSession,
    *,
    repo_id: str,
    file_path: str,
    language: str | None = None,
    content_hash: str | None = None,
    total_chunks: int = 0,
) -> IndexedFile:
    indexed_file = IndexedFile(
        repo_id=repo_id,
        file_path=file_path,
        language=language,
        content_hash=content_hash,
        total_chunks=total_chunks,
    )
    db.add(indexed_file)
    await db.flush()
    await db.refresh(indexed_file)
    return indexed_file


async def list_indexed_files(
    db: AsyncSession, repo_id: str, *, skip: int = 0, limit: int = 500
) -> Sequence[IndexedFile]:
    result = await db.execute(
        select(IndexedFile)
        .where(IndexedFile.repo_id == repo_id)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


async def bulk_create_indexed_files(
    db: AsyncSession,
    repo_id: str,
    files: list[dict],
) -> int:
    """
    Insert multiple IndexedFile records in a single database round-trip.

    Args:
        db:      Open async session.
        repo_id: Repository UUID.
        files:   List of dicts with keys: file_path, language,
                 content_hash, total_chunks (all optional except file_path).

    Returns:
        Number of rows inserted.
    """
    if not files:
        return 0

    objects = [
        IndexedFile(
            repo_id=repo_id,
            file_path=f["file_path"],
            language=f.get("language"),
            content_hash=f.get("content_hash"),
            total_chunks=f.get("total_chunks", 0),
        )
        for f in files
    ]
    db.add_all(objects)
    await db.flush()
    return len(objects)


# ═══════════════════════════════════════════════════════════════════════════════
# CodeChunks
# ═══════════════════════════════════════════════════════════════════════════════

async def create_code_chunk(
    db: AsyncSession,
    *,
    repo_id: str,
    file_path: str,
    symbol_name: str | None = None,
    symbol_type: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    qdrant_point_id: str | None = None,
    content_hash: str | None = None,
) -> CodeChunk:
    chunk = CodeChunk(
        repo_id=repo_id,
        file_path=file_path,
        symbol_name=symbol_name,
        symbol_type=symbol_type,
        start_line=start_line,
        end_line=end_line,
        qdrant_point_id=qdrant_point_id,
        content_hash=content_hash,
    )
    db.add(chunk)
    await db.flush()
    await db.refresh(chunk)
    return chunk


async def list_code_chunks(
    db: AsyncSession, repo_id: str, *, skip: int = 0, limit: int = 1000
) -> Sequence[CodeChunk]:
    result = await db.execute(
        select(CodeChunk)
        .where(CodeChunk.repo_id == repo_id)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


async def delete_code_chunks_for_repo(db: AsyncSession, repo_id: str) -> int:
    """
    Delete ALL code_chunks rows for a repository.

    Called before re-indexing to prevent duplicate chunks.
    Returns the number of rows deleted.
    """
    result = await db.execute(
        sa_delete(CodeChunk).where(CodeChunk.repo_id == repo_id)
    )
    await db.flush()
    return result.rowcount  # type: ignore[return-value]


async def bulk_create_code_chunks(
    db: AsyncSession,
    repo_id: str,
    records: list[dict],
) -> int:
    """
    Batch-insert CodeChunk rows from indexer output.

    Args:
        db:      Open async session.
        repo_id: Repository UUID.
        records: List of dicts with keys:
                   file_path, language, symbol_name, symbol_type,
                   start_line, end_line, qdrant_point_id, content_hash.

    Returns:
        Number of rows inserted.
    """
    if not records:
        return 0
    objects = [
        CodeChunk(
            repo_id=repo_id,
            file_path=r["file_path"],
            symbol_name=r.get("symbol_name"),
            symbol_type=r.get("symbol_type"),
            start_line=r.get("start_line"),
            end_line=r.get("end_line"),
            qdrant_point_id=r.get("qdrant_point_id"),
            content_hash=r.get("content_hash"),
        )
        for r in records
    ]
    db.add_all(objects)
    await db.flush()
    return len(objects)


# ═══════════════════════════════════════════════════════════════════════════════
# ChatMessages
# ═══════════════════════════════════════════════════════════════════════════════

async def create_chat_message(
    db: AsyncSession,
    *,
    repo_id: str,
    role: str,
    content: str,
    citations: list | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        repo_id=repo_id,
        role=role,
        content=content,
        citations=citations,
    )
    db.add(msg)
    await db.flush()
    await db.refresh(msg)
    return msg


async def list_chat_messages(
    db: AsyncSession, repo_id: str, *, skip: int = 0, limit: int = 100
) -> Sequence[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.repo_id == repo_id)
        .order_by(ChatMessage.created_at)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


# ═══════════════════════════════════════════════════════════════════════════════
# PRReviews
# ═══════════════════════════════════════════════════════════════════════════════

async def create_pr_review(
    db: AsyncSession, *, repo_id: str, pr_number: int
) -> PRReview:
    review = PRReview(repo_id=repo_id, pr_number=pr_number)
    db.add(review)
    await db.flush()
    await db.refresh(review)
    return review


async def get_pr_review(db: AsyncSession, review_id: str) -> PRReview | None:
    result = await db.execute(
        select(PRReview).where(PRReview.id == review_id)
    )
    return result.scalar_one_or_none()


async def list_pr_reviews(
    db: AsyncSession, repo_id: str, *, skip: int = 0, limit: int = 50
) -> Sequence[PRReview]:
    result = await db.execute(
        select(PRReview)
        .where(PRReview.repo_id == repo_id)
        .order_by(PRReview.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


async def update_pr_review(
    db: AsyncSession,
    review_id: str,
    *,
    status: str | None = None,
    risk_score: float | None = None,
    summary: str | None = None,
    review_json: dict | None = None,
) -> PRReview | None:
    review = await get_pr_review(db, review_id)
    if review is None:
        return None
    if status is not None:
        review.status = status
    if risk_score is not None:
        review.risk_score = risk_score
    if summary is not None:
        review.summary = summary
    if review_json is not None:
        review.review_json = review_json
    await db.flush()
    await db.refresh(review)
    return review


# ═══════════════════════════════════════════════════════════════════════════════
# ArchitectureDiagrams
# ═══════════════════════════════════════════════════════════════════════════════

async def create_architecture_diagram(
    db: AsyncSession,
    *,
    repo_id: str,
    mermaid_code: str,
    explanation: str | None = None,
    confidence_score: float | None = None,
    detected_components_json: list | None = None,
) -> ArchitectureDiagram:
    diagram = ArchitectureDiagram(
        repo_id=repo_id,
        mermaid_code=mermaid_code,
        explanation=explanation,
        confidence_score=confidence_score,
        detected_components_json=detected_components_json,
    )
    db.add(diagram)
    await db.flush()
    await db.refresh(diagram)
    return diagram


async def list_architecture_diagrams(
    db: AsyncSession, repo_id: str, *, skip: int = 0, limit: int = 20
) -> Sequence[ArchitectureDiagram]:
    result = await db.execute(
        select(ArchitectureDiagram)
        .where(ArchitectureDiagram.repo_id == repo_id)
        .order_by(ArchitectureDiagram.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()
