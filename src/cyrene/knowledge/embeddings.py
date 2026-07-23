"""Embedding and vector utilities for knowledge base search.

Provides optional vector embeddings via HTTP API (no numpy dependency).
All vector operations degrade gracefully when embeddings are unconfigured.
"""

import math
import os
from array import array

import httpx


def _persisted() -> dict:
    """Read the current encrypted runtime settings without caching secrets."""
    try:
        from cyrene.integration_settings import get_embedding_settings

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


async def embed_texts_with_config(texts: list[str], config: dict) -> list[list[float]]:
    """Embed texts with an explicit, already validated provider config.

    This is also used by the settings connectivity probe so draft values can be
    tested without saving them first.
    """
    if not texts:
        return []

    provider = str(config.get("provider") or "openai_compatible").strip().lower().replace("-", "_")
    base_url = str(config.get("base_url") or "").strip().rstrip("/")
    api_key = str(config.get("api_key") or "").strip()
    model = str(config.get("model") or "").strip()
    try:
        dimensions = int(config.get("dimensions") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Invalid embedding dimensions") from exc
    if provider not in {"openai_compatible", "ollama"}:
        raise RuntimeError("Unsupported embedding provider")
    if dimensions < 0 or dimensions > 65_536:
        raise RuntimeError("Invalid embedding dimensions")
    if not base_url or not model:
        raise RuntimeError("Embeddings not configured")

    payload = {
        "model": model,
        "input": texts,
    }
    if dimensions > 0:
        payload["dimensions"] = dimensions

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    endpoint = f"{base_url}/embeddings"
    if provider == "ollama":
        endpoint = f"{base_url}/api/embed"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()

    # Parse embeddings from response
    result_vectors = []
    if provider == "ollama":
        candidates = data.get("embeddings", [])
        if candidates and isinstance(candidates[0], (int, float)):
            candidates = [candidates]
        result_vectors = [item for item in candidates if isinstance(item, list)]
    else:
        ordered = sorted(
            [item for item in data.get("data", []) if isinstance(item, dict)],
            key=lambda item: int(item.get("index", 0)),
        )
        result_vectors = [item.get("embedding") for item in ordered if isinstance(item.get("embedding"), list)]

    if len(result_vectors) != len(texts):
        raise RuntimeError(
            f"Expected {len(texts)} embeddings, got {len(result_vectors)}"
        )

    vector_size = len(result_vectors[0]) if result_vectors else 0
    if vector_size == 0 or any(len(vector) != vector_size for vector in result_vectors):
        raise RuntimeError("Embedding provider returned inconsistent vector dimensions")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for vector in result_vectors
        for value in vector
    ):
        raise RuntimeError("Embedding provider returned an invalid vector")

    return result_vectors


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
