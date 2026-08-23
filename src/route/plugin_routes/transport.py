"""Event-stream and iframe-asset routes for project plugins."""

from __future__ import annotations

import asyncio
import json
import mimetypes

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse

from cyrene.plugins.manager import PluginError, PluginManager
from route.plugin_routes.common import plugin_error, project_id


async def _event_stream(request: Request, manager: PluginManager, target_project: str):
    async for queue in manager.subscribe(target_project):
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def register_plugin_transport_routes(
    router: APIRouter, manager: PluginManager
) -> None:
    @router.get("/api/plugins/events")
    async def api_plugin_events(request: Request):
        target_project = project_id(request)
        if not target_project:
            return plugin_error(PluginError("project_id is required"))
        return StreamingResponse(
            _event_stream(request, manager, target_project),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/plugins/{plugin_id}/projects/{project_id}/assets/{asset_path:path}")
    async def api_project_plugin_asset(
        plugin_id: str, project_id: str, asset_path: str
    ):
        try:
            target = manager.asset_path(plugin_id, project_id, asset_path)
        except (OSError, ValueError, PluginError) as exc:
            return plugin_error(exc, 404)
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(
            target,
            media_type=media_type,
            headers={
                "Cache-Control": "no-cache",
                "Content-Security-Policy": (
                    "default-src 'self' data: blob:; "
                    "script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: blob: https:; "
                    "media-src 'self' data: blob: https:; "
                    "connect-src 'none'"
                ),
            },
        )


__all__ = ["register_plugin_transport_routes"]
