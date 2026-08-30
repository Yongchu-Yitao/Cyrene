"""Host shutdown route."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter


def register_shutdown_route(
    router: APIRouter,
    *,
    request_shutdown: Callable[[], None],
) -> None:
    @router.post("/api/shutdown")
    async def api_shutdown():
        request_shutdown()
        return {"ok": True}


__all__ = ["register_shutdown_route"]
