import asyncio
import pytest
from cyrene.platform.native_browser import NativeBrowserHost


class Socket:
    def __init__(self):
        self.messages = []
    async def send_json(self, value):
        self.messages.append(value)


def test_native_browser_reply_ownership_and_disconnect():
    async def run():
        host = NativeBrowserHost()
        socket, stranger = Socket(), Socket()
        host.attach(socket)
        with pytest.raises(RuntimeError):
            host.attach(stranger)
        task = asyncio.create_task(host.request({"method": "inspect", "sessionId": "chat-A"}, 5))
        await asyncio.sleep(0)
        message = socket.messages[0]
        assert message["sessionId"] == "chat-A"
        host.receive(stranger, {"id": message["id"], "result": {"ok": True}})
        assert not task.done()
        host.receive(socket, {"id": message["id"], "result": {"ok": True, "title": "Same page"}})
        assert (await task)["title"] == "Same page"
        waiting = asyncio.create_task(host.request({"method": "navigate"}, 5))
        await asyncio.sleep(0)
        host.detach(stranger)
        assert not waiting.done()
        host.detach(socket)
        with pytest.raises(ConnectionError):
            await waiting
        assert host.selected  # Must not fall back to a different browser/profile.
        with pytest.raises(ConnectionError):
            await host.request({"method": "navigate"}, 1)
        assert not host._pending
    asyncio.run(run())


def test_native_browser_invalid_reply_and_cancel_cleanup():
    async def run():
        host, socket = NativeBrowserHost(), Socket()
        host.attach(socket)
        task = asyncio.create_task(host.request({"method": "inspect"}, 5))
        await asyncio.sleep(0)
        host.receive(socket, {"id": socket.messages[-1]["id"], "result": []})
        with pytest.raises(ValueError):
            await task
        task = asyncio.create_task(host.request({"method": "inspect"}, 5))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not host._pending
        host.detach(socket)
    asyncio.run(run())


def test_android_runtime_routes_agent_commands_without_electron_or_playwright(monkeypatch):
    from cyrene.platform import native_browser
    from cyrene.plugins.builtin.cyrene_browser import runtime
    host, socket = NativeBrowserHost(), Socket()
    monkeypatch.setattr(native_browser, "native_browser_host", host)
    monkeypatch.delenv("CYRENE_ELECTRON_RPC_PORT", raising=False)
    monkeypatch.delenv("CYRENE_ELECTRON_RPC_TOKEN", raising=False)
    host.attach(socket)
    assert runtime.electron_browser_available()
    async def run():
        task = asyncio.create_task(runtime._electron_browser_rpc("inspect", {"maxElements": 20}, session_id="chat-A", round_id="run-A"))
        await asyncio.sleep(0)
        request = socket.messages[0]
        assert request["sessionId"] == "chat-A"
        assert request["roundId"] == "run-A"
        host.receive(socket, {"id": request["id"], "result": {"ok": True, "tabId": "native"}})
        assert (await task)["tabId"] == "native"
        host.detach(socket)
        assert runtime.electron_browser_available()
    asyncio.run(run())


def test_native_host_websocket_requires_auth_and_local_origin(monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from cyrene.platform import native_browser
    from cyrene.plugins.builtin.cyrene_browser.routes import register_browser_routes
    from cyrene.workbench.webui.auth import LocalAuthMiddleware
    monkeypatch.setenv("CYRENE_AUTH_TOKEN", "native-browser-test-token")
    host = NativeBrowserHost()
    host.selected = False
    monkeypatch.setattr(native_browser, "native_browser_host", host)
    app, router = FastAPI(), APIRouter()
    register_browser_routes(router, None, ":memory:", service=object())
    app.include_router(router)
    app.add_middleware(LocalAuthMiddleware)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for headers in ({}, {"X-Cyrene-Token": "native-browser-test-token", "Origin": "https://evil.example"}):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("ws://127.0.0.1/ws/browser/native-host", headers=headers):
                    pass
        assert not host.selected
        with client.websocket_connect("ws://127.0.0.1/ws/browser/native-host", headers={"X-Cyrene-Token": "native-browser-test-token", "Origin": "http://127.0.0.1"}) as socket:
            assert socket.receive_json() == {"type": "ready"}
            assert host.selected
            socket.send_json([])
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()
        assert host._socket is None
