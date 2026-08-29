"""Plugin-owned HTTP and WebSocket adapters for live browser control."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from cyrene.localization import localized
from .live_service import (
    BrowserLiveApplicationService,
    BrowserLiveController,
    BrowserServiceError,
)
from cyrene.workbench.http.errors import localized_error_response


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
    if isinstance(error, BrowserServiceError):
        message = error.message
        code = error.code
    else:
        logger.error(
            "Unexpected browser WebSocket error",
            exc_info=(type(error), error, error.__traceback__),
        )
        message = localized(
            "The live browser request failed.",
            "浏览器实时请求失败。",
        )
        code = "browser_websocket_error"
    await websocket.send_json({"type": "error", "error": message, "code": code})


def register_browser_routes(
    router: APIRouter,
    bot: Any,
    db_path: str,
    *,
    service: BrowserLiveApplicationService | None = None,
) -> BrowserLiveApplicationService:
    service = service or BrowserLiveApplicationService()

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
                    await _send_browser_error(
                        websocket,
                        BrowserServiceError(
                            localized(
                                "The browser message must be valid JSON.",
                                "浏览器消息必须是有效的 JSON。",
                            ),
                            400,
                            "invalid_browser_json",
                        ),
                    )
                    continue
                if not isinstance(message, dict):
                    await _send_browser_error(
                        websocket,
                        BrowserServiceError(
                            localized(
                                "The browser message must be an object.",
                                "浏览器消息必须是对象。",
                            ),
                            400,
                            "invalid_browser_message",
                        ),
                    )
                    continue
                try:
                    handled = await controller.handle(message)
                    if not handled:
                        await _send_browser_error(
                            websocket,
                            BrowserServiceError(
                                localized(
                                    "The browser message type is not supported.",
                                    "不支持该浏览器消息类型。",
                                ),
                                400,
                                "unsupported_browser_message",
                            ),
                        )
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
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.debug("Invalid browser user-event JSON", exc_info=True)
            return localized_error_response(
                "The request body must be valid JSON.",
                "请求正文必须是有效的 JSON。",
                400,
                "invalid_json",
                ok=False,
            )
        if not isinstance(body, dict):
            return localized_error_response(
                "The request body must be an object.",
                "请求正文必须是对象。",
                400,
                "invalid_request_body",
                ok=False,
            )
        try:
            return await service.record_user_event(body)
        except BrowserServiceError as exc:
            return JSONResponse(
                {"ok": False, "error": exc.message, "code": exc.code},
                status_code=exc.status_code,
            )

    @router.post("/api/browser/navigate")
    async def api_browser_navigate(request: Request):
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.debug("Invalid browser navigation JSON", exc_info=True)
            return localized_error_response(
                "The request body must be valid JSON.",
                "请求正文必须是有效的 JSON。",
                400,
                "invalid_json",
                ok=False,
            )
        if not isinstance(body, dict):
            return localized_error_response(
                "The request body must be an object.",
                "请求正文必须是对象。",
                400,
                "invalid_request_body",
                ok=False,
            )
        return await service.navigate(str(body.get("url") or "").strip())

    @router.post("/api/browser/takeover")
    async def api_browser_takeover():
        return await service.takeover()

    @router.post("/api/browser/release")
    async def api_browser_release():
        return await service.release()

    return service


__all__ = ["register_browser_routes"]
