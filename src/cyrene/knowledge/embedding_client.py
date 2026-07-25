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
    if provider not in {"openai_compatible", "ollama"}:
        raise RuntimeError("Unsupported embedding provider")
    if dimensions < 0 or dimensions > 65_536:
        raise RuntimeError("Invalid embedding dimensions")
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
    async with httpx.AsyncClient() as client:
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

    if len(result_vectors) != len(texts):
        raise RuntimeError(
            f"Expected {len(texts)} embeddings, got {len(result_vectors)}"
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

    return result_vectors
