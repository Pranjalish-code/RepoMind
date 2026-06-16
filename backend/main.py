"""
main.py — RepoMind AI FastAPI application entry point.

Responsibilities:
  - Create the FastAPI app with CORS middleware
  - Run database table creation on startup
  - Mount all routers
  - Provide the health-check endpoint
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from db.session import AsyncSessionLocal, create_all_tables

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)
logger = logging.getLogger("repomind")


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Run startup tasks before yielding, then shutdown tasks after."""
    # Startup ──────────────────────────────────────────────────────────────────
    logger.info("Starting RepoMind AI backend …")

    # Ensure the local repos cache directory exists.
    settings.repos_path.mkdir(parents=True, exist_ok=True)
    logger.info("Repos cache directory: %s", settings.repos_path)

    # Create all database tables (idempotent).
    await create_all_tables()
    logger.info("Database tables verified / created.")

    # Ensure the system user exists (pre-auth MVP mode).
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
            "Codebase Q&A, PR Review, and Architecture Visualizer — "
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
            "http://localhost:3000",   # common dev fallback
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    from api.repos import router as repos_router
    app.include_router(repos_router, prefix="/repos", tags=["Repositories"])

    from api.chat import router as chat_router
    app.include_router(chat_router, prefix="/chat", tags=["Chat"])

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

        Returns the service name, status, and key runtime settings
        (without exposing sensitive secrets).
        """
        return {
            "status": "healthy",
            "service": "repomind-backend",
            "version": "0.1.0",
            "database_url": settings.database_url.split("///")[0] + "///***",
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
