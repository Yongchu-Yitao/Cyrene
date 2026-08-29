"""Instance-bound request/reply broker for web-only current UI surfaces."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SurfaceConnection:
    websocket: Any
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)


_connections: dict[str, SurfaceConnection] = {}
_lock = asyncio.Lock()


async def register(ui_instance_id: str, websocket: Any) -> SurfaceConnection:
    connection = SurfaceConnection(websocket=websocket)
    async with _lock:
        previous = _connections.get(ui_instance_id)
        _connections[ui_instance_id] = connection
    if previous:
        for future in previous.pending.values():
            if not future.done():
                future.set_result({"ok": False, "error": "surface_replaced"})
    return connection


async def unregister(ui_instance_id: str, connection: SurfaceConnection) -> None:
    async with _lock:
        if _connections.get(ui_instance_id) is connection:
            _connections.pop(ui_instance_id, None)
    for future in connection.pending.values():
        if not future.done():
            future.set_result({"ok": False, "error": "no_current_surface"})


def receive(connection: SurfaceConnection, payload: dict[str, Any]) -> None:
    request_id = str(payload.get("requestId") or "")
    future = connection.pending.pop(request_id, None)
    if future and not future.done():
        result = payload.get("result")
        future.set_result(result if isinstance(result, dict) else {"ok": False, "error": "invalid_surface_response"})


async def request(ui_instance_id: str, method: str, args: dict[str, Any], *, timeout: float = 8.0) -> dict[str, Any]:
    async with _lock:
        connection = _connections.get(str(ui_instance_id or ""))
    if not connection:
        return {"ok": False, "error": "no_current_surface"}
    request_id = uuid.uuid4().hex
    future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    connection.pending[request_id] = future
    try:
        await connection.websocket.send_json({
            "type": "request",
            "requestId": request_id,
            "method": method,
            "args": args,
        })
        return await asyncio.wait_for(future, timeout=max(0.25, min(timeout, 15.0)))
    except (asyncio.TimeoutError, RuntimeError):
        return {"ok": False, "error": "surface_timeout"}
    finally:
        connection.pending.pop(request_id, None)


__all__ = ["SurfaceConnection", "receive", "register", "request", "unregister"]
