"""
rag/embeddings.py — Configurable embedding model factory for RepoMind AI.

Supported providers (auto-selected by available API keys):
  1. OpenAI text-embedding-3-small (1536 dims)  — preferred if OPENAI_API_KEY set
  2. Gemini text-embedding-004 (768 dims)        — fallback if GEMINI_API_KEY set

The factory returns a (model, vector_dimension) tuple.
The dimension is required for Qdrant collection creation.

Singletons are cached at module level to avoid recreating model objects on
every request.  Call `reset_embedding_cache()` in tests to clear the cache.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from config import settings

logger = logging.getLogger(__name__)

# ── Dimension registry ────────────────────────────────────────────────────────

VECTOR_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "models/text-embedding-004": 768,
    "models/embedding-001": 768,
}

# ── Provider labels ───────────────────────────────────────────────────────────

PROVIDER_OPENAI = "openai"
PROVIDER_GEMINI = "gemini"


# ── Protocol for type-checking ────────────────────────────────────────────────

@runtime_checkable
class EmbeddingModel(Protocol):
    """Minimal protocol that LangChain embedding classes satisfy."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


# ── Module-level singleton cache ──────────────────────────────────────────────

_cached_model: EmbeddingModel | None = None
_cached_dim: int | None = None
_cached_provider: str | None = None


def reset_embedding_cache() -> None:
    """Clear the singleton cache (useful in tests or after config changes)."""
    global _cached_model, _cached_dim, _cached_provider
    _cached_model = None
    _cached_dim = None
    _cached_provider = None


# ── Public factory ────────────────────────────────────────────────────────────

def get_embeddings() -> tuple[EmbeddingModel, int]:
    """
    Return a (LangChain-compatible embedding model, vector_dimension) tuple.

    Selection priority:
      1. OpenAI if OPENAI_API_KEY is set.
      2. Gemini if GEMINI_API_KEY is set.
      3. Raise RuntimeError if neither key is configured.

    The result is cached after the first call.

    Returns:
        (model, dimension)

    Raises:
        RuntimeError: if no API key is configured.
        ImportError:  if the required LangChain package is not installed.
    """
    global _cached_model, _cached_dim, _cached_provider

    if _cached_model is not None and _cached_dim is not None:
        return _cached_model, _cached_dim

    openai_key = settings.openai_api_key.strip()
    gemini_key = settings.gemini_api_key.strip()

    if openai_key:
        model, dim = _build_openai(openai_key)
        _cached_provider = PROVIDER_OPENAI
    elif gemini_key:
        model, dim = _build_gemini(gemini_key)
        _cached_provider = PROVIDER_GEMINI
    else:
        raise RuntimeError(
            "No embedding API key configured. "
            "Set OPENAI_API_KEY or GEMINI_API_KEY in your .env file."
        )

    _cached_model = model
    _cached_dim = dim
    logger.info(
        "Embedding provider: %s (dim=%d)", _cached_provider, _cached_dim
    )
    return _cached_model, _cached_dim


def get_provider_name() -> str:
    """Return the currently cached provider name, or 'unknown'."""
    return _cached_provider or "unknown"


# ── Provider builders ─────────────────────────────────────────────────────────

def _build_openai(api_key: str) -> tuple[EmbeddingModel, int]:
    """Build an OpenAI text-embedding-3-small model."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError as exc:
        raise ImportError(
            "langchain-openai is required for OpenAI embeddings. "
            "Run: pip install langchain-openai"
        ) from exc

    model_name = "text-embedding-3-small"
    model = OpenAIEmbeddings(
        model=model_name,
        openai_api_key=api_key,
        # Retry on transient errors
        max_retries=3,
    )
    dim = VECTOR_DIMS[model_name]
    return model, dim  # type: ignore[return-value]


def _build_gemini(api_key: str) -> tuple[EmbeddingModel, int]:
    """Build a Gemini text-embedding-004 model."""
    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
    except ImportError as exc:
        raise ImportError(
            "langchain-google-genai is required for Gemini embeddings. "
            "Run: pip install langchain-google-genai"
        ) from exc

    model_name = "models/text-embedding-004"
    model = GoogleGenerativeAIEmbeddings(
        model=model_name,
        google_api_key=api_key,
    )
    dim = VECTOR_DIMS[model_name]
    return model, dim  # type: ignore[return-value]


# ── Batch embed helper ────────────────────────────────────────────────────────

EMBED_BATCH_SIZE = 100  # max texts per API call


def embed_texts_batched(
    model: EmbeddingModel,
    texts: list[str],
    batch_size: int = EMBED_BATCH_SIZE,
) -> list[list[float]]:
    """
    Embed a list of texts in batches to respect API rate/size limits.

    Args:
        model:      LangChain-compatible embedding model.
        texts:      List of strings to embed.
        batch_size: Maximum texts per API call.

    Returns:
        List of embedding vectors in the same order as *texts*.
    """
    if not texts:
        return []

    all_embeddings: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        logger.debug("Embedding batch %d-%d of %d", start, start + len(batch), len(texts))
        batch_embeddings = model.embed_documents(batch)
        all_embeddings.extend(batch_embeddings)

    return all_embeddings
