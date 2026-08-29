"""Web-only current-surface request/reply transport."""

from __future__ import annotations

import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from cyrene.workbench.ui import ui_surface

_UI_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,160}$")


def register_app_control_routes(router: APIRouter) -> None:
    @router.websocket("/api/app-control/ui-surface/{ui_instance_id}")
    async def ui_surface_socket(websocket: WebSocket, ui_instance_id: str):
        if not _UI_ID_RE.fullmatch(str(ui_instance_id or "")):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        connection = await ui_surface.register(ui_instance_id, websocket)
        try:
            while True:
                payload = await websocket.receive_json()
                if isinstance(payload, dict) and payload.get("type") == "response":
                    ui_surface.receive(connection, payload)
        except WebSocketDisconnect:
            pass
        finally:
            await ui_surface.unregister(ui_instance_id, connection)


__all__ = ["register_app_control_routes"]
