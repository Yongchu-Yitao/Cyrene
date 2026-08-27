"""Thin HTTP adapter for Plugin activation settings."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from route.settings.plugin_service import PluginSettingsApplicationService


def register_plugin_settings_routes(
    router: APIRouter,
    service: PluginSettingsApplicationService,
) -> None:
    @router.get("/api/settings/plugins")
    async def api_get_plugins():
        return service.get_settings()

    @router.put("/api/settings/plugins")
    async def api_update_plugins(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": "request body must be valid JSON"},
                status_code=400,
            )
        return await service.update_activation(body)


__all__ = ["register_plugin_settings_routes"]
