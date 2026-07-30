"""Embedding and vector utilities for knowledge base search.

Provides optional vector embeddings via HTTP API (no numpy dependency).
All vector operations degrade gracefully when embeddings are unconfigured.
"""

import math
import os
from array import array

from cyrene.knowledge.embedding_client import embed_texts_with_config


def _persisted() -> dict:
    """Read the current encrypted runtime settings without caching secrets."""
    try:
        from cyrene.runtime.integration_settings import get_embedding_settings

        return get_embedding_settings()
    except Exception:
        return {}


def _base_url() -> str:
    """Get embedding base URL from env or config."""
    env_val = os.environ.get("EMBEDDING_BASE_URL", "").strip()
    if env_val:
        return env_val

    persisted = str(_persisted().get("base_url") or "").strip()
    if persisted:
        return persisted

    try:
        from cyrene import config

        return getattr(config, "EMBEDDING_BASE_URL", "")
    except Exception:
        return ""


def _api_key() -> str:
    """Get embedding API key from env or config."""
    env_val = os.environ.get("EMBEDDING_API_KEY", "").strip()
    if env_val:
        return env_val

    persisted = str(_persisted().get("api_key") or "").strip()
    if persisted:
        return persisted

    try:
        from cyrene import config

        return getattr(config, "EMBEDDING_API_KEY", "")
    except Exception:
        return ""


def _model() -> str:
    """Get embedding model from env or config."""
    env_val = os.environ.get("EMBEDDING_MODEL", "").strip()
    if env_val:
        return env_val

    persisted = str(_persisted().get("model") or "").strip()
    if persisted:
        return persisted

    try:
        from cyrene import config

        return getattr(config, "EMBEDDING_MODEL", "")
    except Exception:
        return ""


def is_configured() -> bool:
    """Check if all embedding configuration is present."""
    return bool(_base_url() and _model())


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts using the configured embedding API.

    Raises an exception if embeddings are not configured or the API call fails.
    """
    if not is_configured():
        raise RuntimeError("Embeddings not configured")

    persisted = _persisted()
    config = {
        "provider": str(os.environ.get("EMBEDDING_PROVIDER") or persisted.get("provider") or "openai_compatible"),
        "base_url": _base_url(),
        "api_key": _api_key(),
        "model": _model(),
        "dimensions": int(os.environ.get("EMBEDDING_DIMENSIONS") or persisted.get("dimensions") or 0),
    }
    return await embed_texts_with_config(texts, config)


def pack_vector(vec: list[float] | array) -> bytes:
    """Pack a vector into a byte blob."""
    if isinstance(vec, array):
        return vec.tobytes()
    return array("f", vec).tobytes()


def unpack_vector(blob: bytes) -> array:
    """Unpack a byte blob into a vector."""
    vec = array("f")
    vec.frombytes(blob)
    return vec


def cosine(a: array | list[float], b: array | list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns 0.0 if vectors have different lengths or zero norm.
    """
    if isinstance(a, array):
        a = list(a)
    if isinstance(b, array):
        b = list(b)

    if len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot_product / (norm_a * norm_b)
