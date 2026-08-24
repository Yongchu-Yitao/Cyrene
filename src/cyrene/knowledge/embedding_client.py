"""Validated HTTP transport for embedding providers."""

from __future__ import annotations

import math

import httpx


async def embed_texts_with_config(
    texts: list[str],
    config: dict,
) -> list[list[float]]:
    """Embed texts with an explicit, already validated provider config."""
    if not texts:
        return []

    provider = (
        str(config.get("provider") or "openai_compatible")
        .strip()
        .lower()
        .replace("-", "_")
    )
    base_url = str(config.get("base_url") or "").strip().rstrip("/")
    api_key = str(config.get("api_key") or "").strip()
    model = str(config.get("model") or "").strip()
    try:
        dimensions = int(config.get("dimensions") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Invalid embedding dimensions") from exc
    if provider not in {"openai_compatible", "ollama", "local_onnx"}:
        raise RuntimeError("Unsupported embedding provider")
    if dimensions < 0 or dimensions > 65_536:
        raise RuntimeError("Invalid embedding dimensions")
    if provider == "local_onnx":
        if model != "qwen3-embedding-0.6b":
            raise RuntimeError("Unsupported local embedding model")
        from cyrene.knowledge.local_onnx import embed_texts

        result_vectors = await embed_texts(
            texts, query=str(config.get("input_type") or "") == "query"
        )
        return _normalize_vectors(result_vectors, len(texts))

    if not base_url or not model:
        raise RuntimeError("Embeddings not configured")

    payload: dict = {"model": model, "input": texts}
    if dimensions > 0:
        payload["dimensions"] = dimensions

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    endpoint = (
        f"{base_url}/api/embed"
        if provider == "ollama"
        else f"{base_url}/embeddings"
    )
    from cyrene.runtime.network_proxy import configured_proxy_url

    proxy_url = configured_proxy_url(opt_in=config.get("use_proxy") is True)
    async with httpx.AsyncClient(proxy=proxy_url or None) as client:
        response = await client.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()

    if provider == "ollama":
        candidates = data.get("embeddings", [])
        if candidates and isinstance(candidates[0], (int, float)):
            candidates = [candidates]
        result_vectors = [
            item
            for item in candidates
            if isinstance(item, list)
        ]
    else:
        ordered = sorted(
            [
                item
                for item in data.get("data", [])
                if isinstance(item, dict)
            ],
            key=lambda item: int(item.get("index", 0)),
        )
        result_vectors = [
            item.get("embedding")
            for item in ordered
            if isinstance(item.get("embedding"), list)
        ]

    return _normalize_vectors(result_vectors, len(texts))


def _normalize_vectors(result_vectors: list[list[float]], expected: int) -> list[list[float]]:
    """Validate and L2-normalize provider output before persistence/search."""
    if len(result_vectors) != expected:
        raise RuntimeError(
            f"Expected {expected} embeddings, got {len(result_vectors)}"
        )

    vector_size = len(result_vectors[0]) if result_vectors else 0
    if vector_size == 0 or any(
        len(vector) != vector_size
        for vector in result_vectors
    ):
        raise RuntimeError(
            "Embedding provider returned inconsistent vector dimensions"
        )
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for vector in result_vectors
        for value in vector
    ):
        raise RuntimeError("Embedding provider returned an invalid vector")

    normalized = []
    for vector in result_vectors:
        norm = math.sqrt(sum(float(value) * float(value) for value in vector))
        if norm <= 0:
            raise RuntimeError("Embedding provider returned a zero vector")
        normalized.append([float(value) / norm for value in vector])
    return normalized
