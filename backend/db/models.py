"""
db/models.py — SQLAlchemy ORM models for RepoMind AI.

Tables:
  users, repositories, indexed_files, code_chunks,
  chat_messages, pr_reviews, architecture_diagrams
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.session import Base


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def _uuid() -> str:
    """Return a new UUID4 string."""
    return str(uuid.uuid4())


# ── users ─────────────────────────────────────────────────────────────────────

class User(Base):
    """Application user."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    repositories: Mapped[list["Repository"]] = relationship(
        "Repository", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email}>"


# ── repositories ──────────────────────────────────────────────────────────────

class Repository(Base):
    """A GitHub repository imported by a user."""

    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repo_url: Mapped[str] = mapped_column(String(512), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(128), default="main", nullable=False)
    local_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # status: pending | cloning | indexing | ready | error
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="repositories")
    indexed_files: Mapped[list["IndexedFile"]] = relationship(
        "IndexedFile", back_populates="repository", cascade="all, delete-orphan"
    )
    code_chunks: Mapped[list["CodeChunk"]] = relationship(
        "CodeChunk", back_populates="repository", cascade="all, delete-orphan"
    )
    chat_messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="repository", cascade="all, delete-orphan"
    )
    pr_reviews: Mapped[list["PRReview"]] = relationship(
        "PRReview", back_populates="repository", cascade="all, delete-orphan"
    )
    architecture_diagrams: Mapped[list["ArchitectureDiagram"]] = relationship(
        "ArchitectureDiagram", back_populates="repository", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Repository id={self.id} name={self.repo_name} status={self.status}>"


# ── indexed_files ─────────────────────────────────────────────────────────────

class IndexedFile(Base):
    """Represents a source file that has been indexed for a repository."""

    __tablename__ = "indexed_files"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid, index=True
    )
    repo_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    repository: Mapped["Repository"] = relationship(
        "Repository", back_populates="indexed_files"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<IndexedFile id={self.id} path={self.file_path}>"


# ── code_chunks ───────────────────────────────────────────────────────────────

class CodeChunk(Base):
    """
    A logical chunk of source code (function / class / block)
    that maps to a Qdrant vector point.
    """

    __tablename__ = "code_chunks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid, index=True
    )
    repo_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    symbol_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # symbol_type: function | class | method | module | block
    symbol_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qdrant_point_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    repository: Mapped["Repository"] = relationship(
        "Repository", back_populates="code_chunks"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CodeChunk id={self.id} file={self.file_path} "
            f"symbol={self.symbol_name} lines={self.start_line}-{self.end_line}>"
        )


# ── chat_messages ─────────────────────────────────────────────────────────────

class ChatMessage(Base):
    """A single message in the repository Q&A chat history."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid, index=True
    )
    repo_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # role: user | assistant | system
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Stored as JSON list of {file_path, start_line, end_line, snippet}
    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    repository: Mapped["Repository"] = relationship(
        "Repository", back_populates="chat_messages"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChatMessage id={self.id} role={self.role}>"


# ── pr_reviews ────────────────────────────────────────────────────────────────

class PRReview(Base):
    """AI-generated pull request review result."""

    __tablename__ = "pr_reviews"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid, index=True
    )
    repo_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # status: pending | reviewing | done | error
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full structured review as JSON
    review_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    repository: Mapped["Repository"] = relationship(
        "Repository", back_populates="pr_reviews"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PRReview id={self.id} pr={self.pr_number} status={self.status}>"


# ── architecture_diagrams ─────────────────────────────────────────────────────

class ArchitectureDiagram(Base):
    """AI-generated Mermaid architecture diagram for a repository."""

    __tablename__ = "architecture_diagrams"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid, index=True
    )
    repo_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mermaid_code: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # JSON list of detected component names / types
    detected_components_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    repository: Mapped["Repository"] = relationship(
        "Repository", back_populates="architecture_diagrams"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ArchitectureDiagram id={self.id} repo={self.repo_id}>"
