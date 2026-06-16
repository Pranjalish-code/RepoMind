"""
config.py — Centralised settings loader using pydantic-settings.

All values come from environment variables (or .env file).
Settings is a singleton; import `settings` directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration resolved from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── AI provider keys ─────────────────────────────────────────────────────
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # ── GitHub ───────────────────────────────────────────────────────────────
    github_token: str = ""

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./repomind.db"

    # ── Qdrant ───────────────────────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection_prefix: str = "repo"

    # ── Paths / URLs ─────────────────────────────────────────────────────────
    repos_dir: str = "./repos"
    backend_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"

    # ── Derived helpers ──────────────────────────────────────────────────────
    @property
    def repos_path(self) -> Path:
        """Absolute Path object for the local repos cache directory."""
        return Path(self.repos_dir).resolve()

    @property
    def sqlite_url_sync(self) -> str:
        """Synchronous SQLite URL (for Alembic / table creation)."""
        return self.database_url.replace("sqlite+aiosqlite", "sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached singleton Settings instance."""
    return Settings()


# Module-level singleton for direct import convenience.
settings: Settings = get_settings()
