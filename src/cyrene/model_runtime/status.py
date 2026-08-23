"""Low-level publication seam for durable model status updates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable


ModelStatusPersister = Callable[..., None]
_persister: ModelStatusPersister | None = None


def register_model_status_persister(persister: ModelStatusPersister) -> None:
    global _persister
    _persister = persister


async def persist_model_status(
    chat_id: str,
    round_id: str,
    *,
    status: str,
    model: str,
    retry_count: int = 0,
    retry_limit: int = 0,
) -> None:
    persister = _persister
    if persister is None:
        return
    await asyncio.to_thread(
        persister,
        chat_id,
        round_id,
        status=status,
        model=model,
        retry_count=retry_count,
        retry_limit=retry_limit,
    )
