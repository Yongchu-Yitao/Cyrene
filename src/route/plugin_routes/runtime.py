"""Contribution, RPC, and log routes for enabled project plugins."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from cyrene.plugins.manager import PluginError, PluginManager
from route.plugin_routes.common import plugin_error, project_id


def register_plugin_runtime_routes(
    router: APIRouter, manager: PluginManager
) -> None:
    @router.get("/api/plugins/contributions")
    async def api_list_plugin_contributions(request: Request, point: str = ""):
        target_project = project_id(request)
        if not target_project:
            return plugin_error(PluginError("project_id is required"))
        return {
            "projectId": target_project,
            "contributions": await manager.contributions(target_project, point),
        }

    @router.post("/api/plugins/{plugin_id}/call")
    async def api_call_plugin(plugin_id: str, request: Request):
        try:
            body = await request.json()
            method = str(body.get("method") or "").strip()
            if not method:
                raise PluginError("plugin method is required")
            result = await manager.call(
                plugin_id,
                project_id(request, body),
                method,
                body.get("args"),
                float(body.get("timeout") or 120.0),
            )
            return {"ok": True, "result": result}
        except asyncio.TimeoutError:
            return plugin_error(PluginError("plugin call timed out"), 504)
        except (OSError, ValueError, PluginError) as exc:
            return plugin_error(exc)

    @router.get("/api/plugins/{plugin_id}/logs")
    async def api_plugin_logs(plugin_id: str, request: Request):
        try:
            return manager.logs(plugin_id, project_id(request))
        except (OSError, ValueError, PluginError) as exc:
            return plugin_error(exc)


__all__ = ["register_plugin_runtime_routes"]
