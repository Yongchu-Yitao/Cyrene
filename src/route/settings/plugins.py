"""Thin HTTP adapter for Plugin activation settings."""

from __future__ import annotations

from fastapi import APIRouter, Request

from route.errors import localized_error_response
from route.settings.plugin_service import PluginSettingsApplicationService


def register_plugin_settings_routes(
    router: APIRouter,
    service: PluginSettingsApplicationService,
) -> None:
    @router.put("/api/plugins/activation")
    async def api_update_plugins(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return localized_error_response(
                "request body must be valid JSON",
                "请求体必须是有效的 JSON。",
                400,
                "invalid_json",
            )
        return await service.update_activation(body)


__all__ = ["register_plugin_settings_routes"]
