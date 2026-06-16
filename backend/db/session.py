"""
db/session.py — Async SQLAlchemy engine + session factory.

Uses aiosqlite for async SQLite support.
Table creation is done at app startup via `create_all_tables()`.
"""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings

# ── Engine ────────────────────────────────────────────────────────────────────
# connect_args check_same_thread=False is required for SQLite with async drivers.
engine = create_async_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=False,           # Set True for SQL debug output
    future=True,
)

# ── Session factory ───────────────────────────────────────────────────────────
# Note: autocommit / autobegin are managed by SQLAlchemy 2.x automatically.
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ── Declarative base ──────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


# ── Table creation helper ─────────────────────────────────────────────────────
async def create_all_tables() -> None:
    """Create all tables defined by ORM models (idempotent)."""
    # Import models so their metadata is registered on Base before create_all.
    import db.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── FastAPI dependency ────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session; rolls back on error, closes when done."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
