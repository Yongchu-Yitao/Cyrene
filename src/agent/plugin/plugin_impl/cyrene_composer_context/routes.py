"""HTTP adapters owned by the composer-context Plugin pack."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request

from route.errors import localized_error_response


async def _request_object(request: Request) -> tuple[dict[str, Any] | None, Any | None]:
    try:
        body = await request.json()
    except ValueError:
        return None, localized_error_response(
            "request body must be valid JSON",
            "请求体必须是有效的 JSON。",
            400,
            "invalid_json",
        )
    if not isinstance(body, dict):
        return None, localized_error_response(
            "request body must be an object",
            "请求体必须是对象。",
            400,
            "invalid_request",
        )
    return body, None


def register_routes(router: APIRouter, service: Any) -> None:
    @router.get("/api/context/state")
    async def api_context_state():
        return await asyncio.to_thread(service.context_state)

    @router.post("/api/context/remove-soul")
    async def api_remove_soul():
        return service.set_soul_active(False)

    @router.post("/api/context/add-soul")
    async def api_add_soul():
        return service.set_soul_active(True)

    @router.post("/api/context/remove-workspace")
    async def api_remove_workspace():
        return await asyncio.to_thread(service.set_workspace_active, False)

    @router.post("/api/context/add-workspace")
    async def api_add_workspace(request: Request):
        body, error = await _request_object(request)
        if error is not None:
            return error
        assert body is not None
        return await asyncio.to_thread(
            service.activate_workspace,
            str(body.get("path", "")),
        )

    @router.post("/api/context/pick-directory")
    async def api_pick_directory():
        return await service.pick_directory()


__all__ = ["register_routes"]
