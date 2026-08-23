"""Thin HTTP adapter for tool settings."""

from __future__ import annotations

from fastapi import APIRouter, Request

from route.settings.tool_service import ToolSettingsApplicationService


def register_tool_routes(
    router: APIRouter,
    service: ToolSettingsApplicationService,
) -> None:
    @router.get("/api/settings/tools")
    async def api_get_tools():
        return service.get_settings()

    @router.put("/api/settings/tools")
    async def api_update_tools(request: Request):
        return await service.update_settings(await request.json())
