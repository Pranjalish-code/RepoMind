"""
main.py — RepoMind AI FastAPI application entry point.

Responsibilities:
  - Create the FastAPI app with CORS middleware
  - Run database table creation on startup
  - Mount all routers
  - Provide the health-check endpoint
  - Provide file-content API for CodeViewer
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from config import settings
from db.models import Repository
from db.session import AsyncSessionLocal, create_all_tables

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)

logger = logging.getLogger("repomind")


# ── Lifespan: startup / shutdown ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Run startup tasks before yielding, then shutdown tasks after."""

    # Startup ──────────────────────────────────────────────────────────────────
    logger.info("Starting RepoMind AI backend …")

    # Ensure the local repos cache directory exists.
    settings.repos_path.mkdir(parents=True, exist_ok=True)
    logger.info("Repos cache directory: %s", settings.repos_path)

    # Create all database tables.
    await create_all_tables()
    logger.info("Database tables verified / created.")

    # Ensure the system user exists.
    from db.crud import get_or_create_system_user

    async with AsyncSessionLocal() as db:
        system_user = await get_or_create_system_user(db)
        logger.info("System user ready: id=%s", system_user.id)

    yield

    # Shutdown ─────────────────────────────────────────────────────────────────
    logger.info("Shutting down RepoMind AI backend …")


# ── App factory ───────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title="RepoMind AI",
        description=(
            "Codebase Q&A and PR Review Assistant — "
            "powered by LangGraph, Qdrant, and Gemini / GPT-4o-mini."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            settings.frontend_url,
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    from api.repos import router as repos_router

    app.include_router(
        repos_router,
        prefix="/repos",
        tags=["Repositories"],
    )

    from api.chat import router as chat_router

    app.include_router(
        chat_router,
        prefix="/chat",
        tags=["Chat"],
    )

    # ── File content API for CodeViewer ───────────────────────────────────────
    @app.get("/repos/{repo_id}/file", tags=["Repositories"])
    async def get_repo_file(
        repo_id: str,
        path: str = Query(..., description="Relative file path inside the repo"),
    ) -> dict[str, Any]:
        """
        Return real source code content for a file inside an imported repository.

        Used by the frontend CodeViewer when the user clicks a citation.

        Example:
            GET /repos/{repo_id}/file?path=client/src/App.jsx
        """

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Repository).where(Repository.id == repo_id)
            )
            repo = result.scalar_one_or_none()

        if repo is None:
            raise HTTPException(
                status_code=404,
                detail="Repository not found",
            )

        repo_root = Path(repo.local_path).resolve()
        requested_path = Path(path)

        # Block absolute paths like C:/Users/... or /etc/passwd
        if requested_path.is_absolute():
            raise HTTPException(
                status_code=403,
                detail="Absolute paths are not allowed",
            )

        file_path = (repo_root / requested_path).resolve()

        # Prevent path traversal attacks like ../../.env
        try:
            file_path.relative_to(repo_root)
        except ValueError:
            raise HTTPException(
                status_code=403,
                detail="Invalid file path",
            )

        ignored_file_names = {
            ".env",
            ".env.local",
            ".env.production",
            ".env.development",
            ".env.test",
        }

        ignored_dirs = {
            ".git",
            "node_modules",
            "venv",
            ".venv",
            "dist",
            "build",
            "coverage",
            "__pycache__",
        }

        if file_path.name in ignored_file_names:
            raise HTTPException(
                status_code=403,
                detail="Reading environment files is not allowed",
            )

        if any(part in ignored_dirs for part in file_path.parts):
            raise HTTPException(
                status_code=403,
                detail="Reading ignored folders is not allowed",
            )

        if not file_path.exists():
            raise HTTPException(
                status_code=404,
                detail="File not found",
            )

        if not file_path.is_file():
            raise HTTPException(
                status_code=400,
                detail="Requested path is not a file",
            )

        allowed_extensions = {
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".json",
            ".md",
            ".html",
            ".css",
        }

        if file_path.suffix.lower() not in allowed_extensions:
            raise HTTPException(
                status_code=403,
                detail="Unsupported file type",
            )

        try:
            content = file_path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not read file: {exc}",
            ) from exc

        return {
            "repo_id": repo_id,
            "file_path": path,
            "content": content,
            "total_lines": len(content.splitlines()),
        }

    # ── Root / Health ─────────────────────────────────────────────────────────
    @app.get("/", tags=["Root"])
    async def root() -> dict[str, str]:
        return {
            "service": "RepoMind AI",
            "status": "running",
            "docs": "/docs",
        }

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, Any]:
        """
        Health-check endpoint.

        Returns service status and safe runtime settings.
        Sensitive secrets are not exposed.
        """

        safe_database_url = settings.database_url

        if "///" in safe_database_url:
            safe_database_url = safe_database_url.split("///")[0] + "///***"
        else:
            safe_database_url = "***"

        return {
            "status": "healthy",
            "service": "repomind-backend",
            "version": "0.1.0",
            "database_url": safe_database_url,
            "qdrant_url": settings.qdrant_url,
            "repos_dir": str(settings.repos_path),
            "frontend_url": settings.frontend_url,
        }

    return app


# ── Application singleton ─────────────────────────────────────────────────────
app = create_app()


# ── Dev runner ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )