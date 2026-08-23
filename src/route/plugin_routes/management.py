"""Install and lifecycle routes for project plugins."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from cyrene.plugins.manager import PluginError, PluginManager
from route.plugin_routes.common import plugin_error, project_id


def register_plugin_management_routes(
    router: APIRouter, manager: PluginManager
) -> None:
    @router.get("/api/plugins")
    async def api_list_plugins(request: Request):
        return {"plugins": await manager.list_plugins(project_id(request))}

    @router.post("/api/plugins/install")
    async def api_install_plugin(request: Request):
        try:
            body = await request.json()
            raw_path = str(body.get("path") or "").strip()
            if not raw_path:
                raise PluginError("plugin path is required")
            return await manager.install(
                Path(raw_path), replace=body.get("replace") is True
            )
        except (OSError, ValueError, PluginError) as exc:
            return plugin_error(exc)

    @router.post("/api/plugins/{plugin_id}/enabled")
    async def api_set_plugin_enabled(plugin_id: str, request: Request):
        try:
            body = await request.json()
            if not isinstance(body.get("enabled"), bool):
                raise PluginError("enabled must be a boolean")
            return await manager.set_enabled(
                plugin_id, project_id(request, body), body["enabled"]
            )
        except (OSError, ValueError, PluginError) as exc:
            return plugin_error(exc)

    @router.post("/api/plugins/{plugin_id}/reload")
    async def api_reload_plugin(plugin_id: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            return await manager.reload(plugin_id, project_id(request, body))
        except (OSError, ValueError, PluginError) as exc:
            return plugin_error(exc)

    @router.delete("/api/plugins/{plugin_id}")
    async def api_delete_plugin(plugin_id: str, delete_data: bool = False):
        try:
            return await manager.delete(plugin_id, delete_data=delete_data)
        except (OSError, ValueError, PluginError) as exc:
            return plugin_error(exc)


__all__ = ["register_plugin_management_routes"]
