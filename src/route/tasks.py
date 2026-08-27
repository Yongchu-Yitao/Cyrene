"""Host lifecycle route retained after removing the legacy task REST API."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter


def register_task_routes(
    router: APIRouter,
    *,
    request_shutdown: Callable[[], None],
) -> None:
    @router.post("/api/shutdown")
    async def api_shutdown():
        request_shutdown()
        return {"ok": True}


__all__ = ["register_task_routes"]
