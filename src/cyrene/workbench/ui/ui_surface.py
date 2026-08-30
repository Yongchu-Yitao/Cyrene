"""Instance-bound request/reply broker for web-only current UI surfaces."""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SurfaceConnection:
    websocket: Any
    loop: asyncio.AbstractEventLoop
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    closed: bool = False


_connections: dict[str, SurfaceConnection] = {}
_lock = threading.RLock()


def _settle(
    connection: SurfaceConnection,
    result: dict[str, Any],
) -> None:
    def finish() -> None:
        for future in tuple(connection.pending.values()):
            if not future.done():
                future.set_result(dict(result))

    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if current is connection.loop:
        finish()
    elif not connection.loop.is_closed():
        connection.loop.call_soon_threadsafe(finish)


async def register(ui_instance_id: str, websocket: Any) -> SurfaceConnection:
    connection = SurfaceConnection(
        websocket=websocket,
        loop=asyncio.get_running_loop(),
    )
    with _lock:
        previous = _connections.get(ui_instance_id)
        _connections[ui_instance_id] = connection
        if previous:
            previous.closed = True
    if previous:
        _settle(previous, {"ok": False, "error": "surface_replaced"})
    return connection


async def unregister(ui_instance_id: str, connection: SurfaceConnection) -> None:
    with _lock:
        if _connections.get(ui_instance_id) is connection:
            _connections.pop(ui_instance_id, None)
        connection.closed = True
    _settle(connection, {"ok": False, "error": "no_current_surface"})


def receive(connection: SurfaceConnection, payload: dict[str, Any]) -> None:
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if current is not connection.loop:
        if not connection.loop.is_closed():
            connection.loop.call_soon_threadsafe(receive, connection, payload)
        return
    request_id = str(payload.get("requestId") or "")
    future = connection.pending.pop(request_id, None)
    if future and not future.done():
        result = payload.get("result")
        future.set_result(result if isinstance(result, dict) else {"ok": False, "error": "invalid_surface_response"})


async def _request_on_connection(
    connection: SurfaceConnection,
    method: str,
    args: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    """Execute one WebSocket round trip on the connection's owner loop."""

    if connection.closed:
        return {"ok": False, "error": "no_current_surface"}
    request_id = uuid.uuid4().hex
    future: asyncio.Future[dict[str, Any]] = connection.loop.create_future()
    connection.pending[request_id] = future
    try:
        await connection.websocket.send_json({
            "type": "request",
            "requestId": request_id,
            "method": method,
            "args": args,
        })
        return await asyncio.wait_for(future, timeout=max(0.25, min(timeout, 15.0)))
    except asyncio.TimeoutError:
        return {"ok": False, "error": "surface_timeout"}
    except RuntimeError:
        return {"ok": False, "error": "surface_disconnected"}
    finally:
        connection.pending.pop(request_id, None)


async def request(ui_instance_id: str, method: str, args: dict[str, Any], *, timeout: float = 8.0) -> dict[str, Any]:
    with _lock:
        connection = _connections.get(str(ui_instance_id or ""))
    if not connection or connection.closed:
        return {"ok": False, "error": "no_current_surface"}
    operation = _request_on_connection(
        connection,
        method,
        dict(args),
        timeout=timeout,
    )
    current = asyncio.get_running_loop()
    if current is connection.loop:
        return await operation
    if connection.loop.is_closed():
        operation.close()
        return {"ok": False, "error": "no_current_surface"}
    try:
        concurrent = asyncio.run_coroutine_threadsafe(operation, connection.loop)
    except RuntimeError:
        operation.close()
        return {"ok": False, "error": "no_current_surface"}
    return await asyncio.wrap_future(concurrent)


__all__ = ["SurfaceConnection", "receive", "register", "request", "unregister"]
