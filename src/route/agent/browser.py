"""HTTP and WebSocket adapters for live browser control."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from cyrene.workbench.browser_live_service import (
    BrowserLiveApplicationService,
    BrowserLiveController,
    BrowserServiceError,
)


logger = logging.getLogger(__name__)


async def _pump_frames(websocket: WebSocket, controller: BrowserLiveController) -> None:
    try:
        while True:
            frame = await controller.frame_queue.get()
            data = frame.get("data") or b""
            await websocket.send_json({
                "type": "frame",
                "url": frame.get("url") or "",
                "content_type": frame.get("content_type") or "image/jpeg",
            })
            if data:
                await websocket.send_bytes(data)
    except (WebSocketDisconnect, asyncio.CancelledError):
        raise


async def _send_browser_error(websocket: WebSocket, error: Exception) -> None:
    await websocket.send_json({"type": "error", "error": str(error)})


def register_browser_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    service = BrowserLiveApplicationService()

    @router.websocket("/ws/browser")
    async def ws_browser(websocket: WebSocket):
        await websocket.accept()
        try:
            controller = await service.open_live()
        except BrowserServiceError as exc:
            await _send_browser_error(websocket, exc)
            await websocket.close()
            return

        pump_task = asyncio.create_task(_pump_frames(websocket, controller))
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                try:
                    await controller.handle(message)
                except BrowserServiceError as exc:
                    await _send_browser_error(websocket, exc)
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            try:
                await controller.stop()
            except BrowserServiceError:
                logger.debug("browser live session cleanup failed", exc_info=True)
            await asyncio.gather(pump_task, return_exceptions=True)

    @router.post("/api/browser/user-event")
    async def api_browser_user_event(request: Request):
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "request body must be an object"}, status_code=400)
        try:
            return await service.record_user_event(body)
        except BrowserServiceError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status_code)

    @router.post("/api/browser/navigate")
    async def api_browser_navigate(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "request body must be an object"}, status_code=400)
        return await service.navigate(str(body.get("url") or "").strip())

    @router.post("/api/browser/takeover")
    async def api_browser_takeover():
        return await service.takeover()

    @router.post("/api/browser/release")
    async def api_browser_release():
        return await service.release()


__all__ = ["register_browser_routes"]
