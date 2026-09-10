"""Authenticated reverse RPC to the Android host; never spawn a second browser.

Owned by the backend event loop. Routes still pass through LocalAuthMiddleware.
A host which disconnects remains selected, so an Agent cannot silently move to
another browser/profile while the user is looking at the Android tabs.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any


class NativeBrowserHost:
    def __init__(self) -> None:
        self.selected = os.environ.get("CYRENE_BROWSER_HOST") == "android"
        self._socket: Any = None
        self._pending: dict[str, asyncio.Future] = {}

    def attach(self, socket: Any) -> None:
        if self._socket is not None:
            raise RuntimeError("A native browser host is already connected")
        self.selected = True
        self._socket = socket

    def detach(self, socket: Any) -> None:
        if self._socket is not socket:
            return
        self._socket = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Android browser host disconnected"))
        self._pending.clear()

    def receive(self, socket: Any, message: dict[str, Any]) -> None:
        if socket is not self._socket:
            return
        future = self._pending.get(str(message.get("id", "")))
        result = message.get("result")
        if future is not None and not future.done():
            if not isinstance(result, dict):
                future.set_exception(ValueError("Invalid Android browser response"))
            else:
                future.set_result(result)

    async def request(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        socket = self._socket
        if socket is None:
            raise ConnectionError("Open Cyrene on Android to reconnect its browser")
        if len(self._pending) >= 32:
            raise RuntimeError("Too many pending browser commands")
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await socket.send_json({"id": request_id, **payload})
            return await asyncio.wait_for(future, min(max(timeout, 1), 90))
        finally:
            self._pending.pop(request_id, None)


native_browser_host = NativeBrowserHost()
