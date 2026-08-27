"""Embedding and vector utilities for knowledge base search.

Provides optional vector embeddings via HTTP API (no numpy dependency).
All vector operations degrade gracefully when embeddings are unconfigured.
"""

import math
from array import array
from typing import Any

from agent.plugin import PluginContext
from agent.plugin.model_catalog import (
    application_model_runtime,
    candidate_provider_id,
    resolve_model_plugin,
)


def _configuration_service():
    from agent.plugin import active_plugin_service

    return active_plugin_service("model_configuration")


def _configured_candidate() -> dict[str, Any]:
    """Resolve the canonical embedding route without caching credentials."""

    try:
        service = _configuration_service()
        candidates = service.candidates_for_route("embedding") if service is not None else []
    except Exception:
        return {}
    return dict(candidates[0]) if candidates else {}


def _provider(candidate: dict[str, Any]) -> str:
    return candidate_provider_id(candidate) or str(
        candidate.get("adapter") or candidate.get("provider") or ""
    ).strip().lower()


def is_configured() -> bool:
    """Check whether semantic embeddings are currently usable.

    Local models are optional enhancements.  A saved local provider must not
    disable the knowledge base while its explicitly managed model pack is
    absent; callers should keep using the existing lexical retrieval path.
    """
    candidate = _configured_candidate()
    if not candidate:
        return False
    provider = _provider(candidate).replace("-", "_")
    if provider == "local_onnx":
        if str(candidate.get("model") or "") != "qwen3-embedding-0.6b":
            return False
        try:
            from . import local_models

            return local_models.is_ready("qwen3-embedding-0.6b")
        except Exception:
            return False
    return bool(candidate.get("model") and candidate.get("base_url"))


def current_identity() -> tuple[str, int]:
    candidate = _configured_candidate()
    model = str(candidate.get("model") or "")
    dimensions = int(candidate.get("dimensions") or 0)
    if _provider(candidate) == "local_onnx" and not dimensions:
        dimensions = 1024
    return model, dimensions


async def embed_texts(texts: list[str], *, input_type: str = "document") -> list[list[float]]:
    """Embed a list of texts using the configured embedding API.

    Raises an exception if embeddings are not configured or the API call fails.
    """
    if not texts:
        return []
    if not is_configured():
        raise RuntimeError("Embeddings not configured")

    candidate = _configured_candidate()
    service = _configuration_service()
    if service is None:
        raise RuntimeError("Model configuration Plugin is not available")
    configuration = service.get_model_configuration()
    connection = next(
        (
            dict(item)
            for item in configuration.get("connections") or []
            if item.get("id") == candidate.get("connection_id")
        ),
        None,
    )
    profile = next(
        (
            dict(item)
            for item in configuration.get("profiles") or []
            if item.get("id") == candidate.get("profile_id")
        ),
        None,
    )
    if connection is None or profile is None:
        raise RuntimeError("Embedding route is invalid")
    return await _invoke_embedding_plugin(
        texts,
        candidate=candidate,
        connection=connection,
        profile=profile,
        input_type=input_type,
    )


async def _invoke_embedding_plugin(
    texts: list[str],
    *,
    candidate: dict[str, Any],
    connection: dict[str, Any],
    profile: dict[str, Any],
    input_type: str,
) -> list[list[float]]:
    provider = _provider(candidate)
    adapter = str(candidate.get("adapter") or connection.get("adapter") or "")
    registry, plugin = resolve_model_plugin(provider, adapter)
    if plugin is None:
        raise RuntimeError(f"Embedding model Plugin is not available: {provider or adapter}")
    arguments: dict[str, Any] = {
        "operation": "embed",
        "inputs": [str(text) for text in texts],
        "input_type": input_type,
    }
    dimensions = int(candidate.get("dimensions") or profile.get("dimensions") or 0)
    if dimensions > 0:
        arguments["dimensions"] = dimensions
    safe_candidate = {
        key: value
        for key, value in candidate.items()
        if key not in {"api_key", "base_url", "endpoints", "preferred_endpoint"}
    }
    outcome = await application_model_runtime(registry).call(
        plugin.name,
        arguments,
        PluginContext(
            data={
                "caller": "knowledge_embedding",
                "model_call_kind": "embedding",
                "model_candidate": safe_candidate,
            },
            services={
                "model_connection": connection,
                "model_profile": profile,
            },
        ),
    )
    if not outcome.success:
        raise RuntimeError(outcome.error or "Embedding model Plugin failed")
    payload = outcome.value
    vectors = payload.get("embeddings") if isinstance(payload, dict) else None
    if not isinstance(vectors, list):
        raise RuntimeError("Embedding model Plugin returned an invalid result")
    return _normalize_plugin_vectors(vectors, len(texts))


def _normalize_plugin_vectors(vectors: list[Any], expected: int) -> list[list[float]]:
    if len(vectors) != expected or not vectors:
        raise RuntimeError(f"Expected {expected} embeddings, got {len(vectors)}")
    if not all(isinstance(vector, list) and vector for vector in vectors):
        raise RuntimeError("Embedding model Plugin returned invalid vectors")
    size = len(vectors[0])
    if any(len(vector) != size for vector in vectors):
        raise RuntimeError("Embedding model Plugin returned inconsistent dimensions")
    normalized: list[list[float]] = []
    for vector in vectors:
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in vector
        ):
            raise RuntimeError("Embedding model Plugin returned an invalid vector")
        norm = math.sqrt(sum(float(value) * float(value) for value in vector))
        if norm <= 0:
            raise RuntimeError("Embedding model Plugin returned a zero vector")
        normalized.append([float(value) / norm for value in vector])
    return normalized


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
