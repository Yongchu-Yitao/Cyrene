"""HTTP adapter for ordered web-search provider settings."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.runtime.search_settings_service import (
    SearchSettingsApplicationError,
    SearchSettingsApplicationService,
)


def register_search_settings_routes(
    router: APIRouter,
    service: SearchSettingsApplicationService,
) -> None:
    @router.get("/api/settings/search")
    async def api_get_search_settings():
        return service.get_settings()

    @router.put("/api/settings/search")
    async def api_update_search_settings(request: Request):
        try:
            return await service.update_settings(await request.json())
        except SearchSettingsApplicationError as exc:
            payload = {"error": str(exc)}
            if exc.revision is not None:
                payload["revision"] = exc.revision
            return JSONResponse(payload, status_code=exc.status_code)


__all__ = ["register_search_settings_routes"]
