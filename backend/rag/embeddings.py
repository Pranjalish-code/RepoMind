"""
rag/embeddings.py — Configurable embedding model factory for RepoMind AI.

Supported providers:
  1. Gemini gemini-embedding-001
  2. OpenAI text-embedding-3-small

The factory returns a (model, vector_dimension) tuple.
The dimension is required for Qdrant collection creation.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from config import settings

logger = logging.getLogger(__name__)

VECTOR_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,

    # Gemini current embedding model
    "gemini-embedding-001": 3072,
    "models/gemini-embedding-001": 3072,
}

PROVIDER_OPENAI = "openai"
PROVIDER_GEMINI = "gemini"

@runtime_checkable
class EmbeddingModel(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...

_cached_model: EmbeddingModel | None = None
_cached_dim: int | None = None
_cached_provider: str | None = None


def reset_embedding_cache() -> None:
    global _cached_model, _cached_dim, _cached_provider
    _cached_model = None
    _cached_dim = None
    _cached_provider = None


def get_embeddings() -> tuple[EmbeddingModel, int]:
    global _cached_model, _cached_dim, _cached_provider

    if _cached_model is not None and _cached_dim is not None:
        return _cached_model, _cached_dim

    openai_key = settings.openai_api_key.strip()
    gemini_key = settings.gemini_api_key.strip()

    if gemini_key:
        model, dim = _build_gemini(gemini_key)
        _cached_provider = PROVIDER_GEMINI
    elif openai_key:
        model, dim = _build_openai(openai_key)
        _cached_provider = PROVIDER_OPENAI
    else:
        raise RuntimeError(
            "No embedding API key configured. "
            "Set GEMINI_API_KEY or OPENAI_API_KEY in your .env file."
        )

    _cached_model = model
    _cached_dim = dim

    logger.info("Embedding provider: %s (dim=%d)", _cached_provider, _cached_dim)
    return _cached_model, _cached_dim


def get_provider_name() -> str:
    return _cached_provider or "unknown"


def _build_openai(api_key: str) -> tuple[EmbeddingModel, int]:
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
        max_retries=3,
    )

    dim = VECTOR_DIMS[model_name]
    return model, dim  # type: ignore[return-value]


def _build_gemini(api_key: str) -> tuple[EmbeddingModel, int]:
    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
    except ImportError as exc:
        raise ImportError(
            "langchain-google-genai is required for Gemini embeddings. "
            "Run: pip install langchain-google-genai"
        ) from exc

    # Use current Gemini embedding model.
    # If your installed langchain-google-genai expects no "models/" prefix,
    # change this to "gemini-embedding-001".
    model_name = "models/gemini-embedding-001"

    model = GoogleGenerativeAIEmbeddings(
        model=model_name,
        google_api_key=api_key,
    )

    dim = VECTOR_DIMS[model_name]
    return model, dim  # type: ignore[return-value]


EMBED_BATCH_SIZE = 100


def embed_texts_batched(
    model: EmbeddingModel,
    texts: list[str],
    batch_size: int = EMBED_BATCH_SIZE,
) -> list[list[float]]:
    if not texts:
        return []

    all_embeddings: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        logger.debug(
            "Embedding batch %d-%d of %d",
            start,
            start + len(batch),
            len(texts),
        )
        batch_embeddings = model.embed_documents(batch)
        all_embeddings.extend(batch_embeddings)

    return all_embeddings