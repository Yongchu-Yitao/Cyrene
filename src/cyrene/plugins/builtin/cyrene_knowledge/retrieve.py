"""Retrieval boundary for the Plugin-owned knowledge store."""

from __future__ import annotations

import asyncio
from typing import Any

from .store import KnowledgeStore


async def search_knowledge(
    store: KnowledgeStore,
    workspace: str | None,
    query: str,
    *,
    limit: int = 6,
    query_vector: list[float] | None = None,
    embedding_model: str = "",
    embedding_dimensions: int = 0,
) -> list[dict[str, Any]]:
    needle = str(query or "").strip()
    if not needle:
        return []
    return await asyncio.to_thread(
        store.search_chunks,
        workspace,
        needle,
        limit=max(1, min(int(limit or 6), 50)),
        query_vector=query_vector,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )


__all__ = ["search_knowledge"]
