"""Plugin-owned application service for live browser control and learning capture."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from cyrene.localization import localized

from . import runtime


logger = logging.getLogger(__name__)


def _active_learning_service() -> Any | None:
    from agent.plugin import active_plugin_service

    return active_plugin_service("skills")


class BrowserSessionPort(Protocol):
    async def start_screencast(self, queue: asyncio.Queue[Any]) -> None: ...
    async def stop_screencast(self, queue: asyncio.Queue[Any]) -> None: ...
    def set_user_control(self, on: bool) -> None: ...
    async def dispatch_mouse(self, **kwargs: Any) -> None: ...
    async def dispatch_key(self, **kwargs: Any) -> None: ...
    async def insert_text(self, text: str) -> None: ...
    async def current_url(self) -> str: ...
    async def current_page_metadata(self) -> dict[str, str]: ...
    async def open_user_window(self, url: str = "") -> None: ...
    async def close_user_window(self, url: str = "") -> None: ...


class BrowserRuntimePort(Protocol):
    def available(self) -> bool: ...
    async def session(self) -> BrowserSessionPort: ...
    async def navigate(self, url: str) -> dict[str, Any]: ...
    def unavailable_message(self, exc: Exception | str | None = None) -> str: ...


class DefaultBrowserRuntime:
    def available(self) -> bool:
        return runtime.browser_runtime_available()

    async def session(self) -> BrowserSessionPort:
        return await runtime.get_session()

    async def navigate(self, url: str) -> dict[str, Any]:
        return await runtime.navigate(url)

    def unavailable_message(self, exc: Exception | str | None = None) -> str:
        return runtime.browser_runtime_unavailable_message(exc)


@dataclass(slots=True)
class BrowserServiceError(RuntimeError):
    message: str
    status_code: int = 500
    code: str = "browser_error"

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True)
class BrowserLiveController:
    session: BrowserSessionPort
    frame_queue: asyncio.Queue[Any] = field(default_factory=lambda: asyncio.Queue(maxsize=2))
    session_id: str = ""
    round_id: str = ""

    async def start(self) -> None:
        await self.session.start_screencast(self.frame_queue)

    async def stop(self) -> None:
        try:
            self.session.set_user_control(False)
            await self.session.stop_screencast(self.frame_queue)
        except Exception as exc:
            logger.warning("Failed to stop browser live session", exc_info=True)
            raise BrowserServiceError(
                localized(
                    "The live browser session could not be stopped.",
                    "无法停止浏览器实时会话。",
                ),
                code="browser_live_stop_failed",
            ) from exc

    async def handle(self, message: dict[str, Any]) -> bool:
        try:
            return await self._handle(message)
        except BrowserServiceError:
            raise
        except (TypeError, ValueError) as exc:
            logger.debug("Invalid live browser input", exc_info=True)
            raise BrowserServiceError(
                localized("Invalid browser input.", "浏览器输入无效。"),
                400,
                "invalid_browser_input",
            ) from exc
        except Exception as exc:
            logger.warning("Browser input dispatch failed", exc_info=True)
            raise BrowserServiceError(
                localized(
                    "The browser input could not be processed.",
                    "无法处理浏览器输入。",
                ),
                code="browser_input_dispatch_failed",
            ) from exc

    async def _handle(self, message: dict[str, Any]) -> bool:
        kind = str(message.get("type") or "")
        if kind == "context":
            self.session_id = str(message.get("sessionId") or "").strip()
            self.round_id = str(message.get("roundId") or "").strip()
            return True
        if kind == "control":
            on = bool(message.get("on"))
            self.session.set_user_control(on)
            await self._record("control_start" if on else "control_stop", {"on": on})
            return True
        if kind == "mouse":
            await self._mouse(message)
            return True
        if kind == "key":
            await self._key(message)
            return True
        if kind == "text":
            text = str(message.get("text") or "")
            await self.session.insert_text(text)
            await self._record("text", {"text": text})
            return True
        return False

    async def _mouse(self, message: dict[str, Any]) -> None:
        payload = {
            "event": str(message.get("event") or ""),
            "x": float(message.get("x") or 0),
            "y": float(message.get("y") or 0),
            "button": str(message.get("button") or "none"),
            "clickCount": int(message.get("clickCount") or 0),
            "deltaX": float(message.get("deltaX") or 0),
            "deltaY": float(message.get("deltaY") or 0),
            "modifiers": int(message.get("modifiers") or 0),
        }
        await self.session.dispatch_mouse(
            type=payload["event"], x=payload["x"], y=payload["y"],
            button=payload["button"], click_count=payload["clickCount"],
            delta_x=payload["deltaX"], delta_y=payload["deltaY"],
            modifiers=payload["modifiers"],
        )
        if payload["event"] in {"mouseReleased", "mouseWheel"}:
            await self._record("click" if payload["event"] == "mouseReleased" else "scroll", payload)

    async def _key(self, message: dict[str, Any]) -> None:
        payload = {
            "event": str(message.get("event") or ""),
            "key": str(message.get("key") or ""),
            "code": str(message.get("code") or ""),
            "text": str(message.get("text") or ""),
            "keyCode": int(message.get("keyCode") or 0),
            "modifiers": int(message.get("modifiers") or 0),
        }
        await self.session.dispatch_key(
            type=payload["event"], key=payload["key"], code=payload["code"],
            text=payload["text"], key_code=payload["keyCode"],
            modifiers=payload["modifiers"],
        )
        if payload["event"] == "keyDown":
            await self._record("key", payload)

    async def _record(self, kind: str, payload: dict[str, Any]) -> None:
        learning = _active_learning_service()
        if learning is None:
            return
        try:
            metadata = await self.session.current_page_metadata()
            await learning.record_browser_user_event(
                session_id=self.session_id,
                round_id=self.round_id,
                event_kind=kind,
                payload=payload,
                browser_url=metadata.get("url", ""),
                browser_title=metadata.get("title", ""),
                target={
                    "x": payload.get("x"),
                    "y": payload.get("y"),
                    "button": payload.get("button"),
                },
            )
        except Exception as exc:
            logger.warning("Failed to record browser user event", exc_info=True)
            raise BrowserServiceError(
                localized(
                    "The browser interaction could not be recorded.",
                    "无法记录浏览器交互。",
                ),
                code="browser_event_record_failed",
            ) from exc


class BrowserLiveApplicationService:
    def __init__(self, runtime: BrowserRuntimePort | None = None) -> None:
        self.runtime = runtime or DefaultBrowserRuntime()

    async def open_live(self) -> BrowserLiveController:
        if not self.runtime.available():
            raise BrowserServiceError(
                localized(
                    "Cyrene browser runtime is unavailable.",
                    "Cyrene 浏览器运行时不可用。",
                ),
                503,
                "browser_runtime_unavailable",
            )
        try:
            session = await self.runtime.session()
            controller = BrowserLiveController(session)
            await controller.start()
            return controller
        except BrowserServiceError:
            raise
        except Exception as exc:
            logger.warning("Browser launch failed", exc_info=True)
            raise BrowserServiceError(
                localized(
                    "The live browser could not be launched.",
                    "无法启动实时浏览器。",
                ),
                503,
                "browser_launch_failed",
            ) from exc

    async def record_user_event(self, body: dict[str, Any]) -> dict[str, Any]:
        learning = _active_learning_service()
        if learning is None:
            return {"ok": False}
        try:
            await learning.record_browser_user_event(
                session_id=str(body.get("sessionId") or body.get("session_id") or ""),
                round_id=str(body.get("roundId") or body.get("round_id") or ""),
                event_kind=str(body.get("eventKind") or body.get("kind") or "event"),
                payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
                browser_url=str(body.get("browserUrl") or body.get("url") or ""),
                browser_title=str(body.get("browserTitle") or body.get("title") or ""),
                target=body.get("target") if isinstance(body.get("target"), dict) else {},
            )
        except Exception as exc:
            logger.warning("Failed to record browser user event", exc_info=True)
            raise BrowserServiceError(
                localized(
                    "The browser interaction could not be recorded.",
                    "无法记录浏览器交互。",
                ),
                code="browser_event_record_failed",
            ) from exc
        return {"ok": True}

    async def navigate(self, url: str) -> dict[str, Any]:
        if not url:
            return {
                "ok": False,
                "error": localized("A URL is required.", "必须提供网址。"),
                "code": "url_required",
            }
        try:
            return await self.runtime.navigate(url)
        except Exception:
            logger.warning("Browser navigation failed", exc_info=True)
            return {
                "ok": False,
                "error": localized(
                    "The browser could not navigate to that URL.",
                    "浏览器无法导航到该网址。",
                ),
                "code": "browser_navigation_failed",
            }

    async def takeover(self) -> dict[str, Any]:
        if not self.runtime.available():
            return {
                "ok": False,
                "error": localized(
                    "Cyrene browser runtime is unavailable.",
                    "Cyrene 浏览器运行时不可用。",
                ),
                "code": "browser_runtime_unavailable",
            }
        try:
            session = await self.runtime.session()
            url = await session.current_url()
            await session.open_user_window(url)
            return {"ok": True, "url": url, "mode": "headed"}
        except Exception:
            logger.warning("Browser takeover failed", exc_info=True)
            return {
                "ok": False,
                "error": localized(
                    "The browser could not enter user-control mode.",
                    "浏览器无法进入用户接管模式。",
                ),
                "code": "browser_takeover_failed",
            }

    async def release(self) -> dict[str, Any]:
        if not self.runtime.available():
            return {
                "ok": False,
                "error": localized(
                    "Cyrene browser runtime is unavailable.",
                    "Cyrene 浏览器运行时不可用。",
                ),
                "code": "browser_runtime_unavailable",
            }
        try:
            session = await self.runtime.session()
            await session.close_user_window(await session.current_url())
            return {"ok": True, "mode": "headless"}
        except Exception:
            logger.warning("Browser release failed", exc_info=True)
            return {
                "ok": False,
                "error": localized(
                    "The browser could not leave user-control mode.",
                    "浏览器无法退出用户接管模式。",
                ),
                "code": "browser_release_failed",
            }


__all__ = [
    "BrowserLiveApplicationService", "BrowserLiveController", "BrowserServiceError",
    "DefaultBrowserRuntime",
]
