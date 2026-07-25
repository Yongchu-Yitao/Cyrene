"""Browser HTTP and WebSocket routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *


def register_browser_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    @router.websocket("/ws/browser")
    async def ws_browser(websocket: WebSocket):
        """Live screencast of the agent's browser session.

        Streams CDP JPEG frames to the chat-side browser panel. The control
        channel (start/stop/set_quality) is reserved for later; login takeover
        (M3) happens in the native window, not over this socket.
        """
        await websocket.accept()
        from cyrene import browser as _browser

        if _browser._ensure_playwright() is None:
            await websocket.send_json({"type": "error", "error": _browser.browser_runtime_unavailable_message()})
            await websocket.close()
            return

        try:
            session = await _browser.get_session()
        except Exception as exc:
            await websocket.send_json({"type": "error", "error": f"Browser launch failed: {_browser.browser_runtime_unavailable_message(exc)}"})
            await websocket.close()
            return

        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        await session.start_screencast(queue)

        async def _pump() -> None:
            try:
                while True:
                    frame = await queue.get()
                    data = frame.get("data") or b""
                    await websocket.send_json({
                        "type": "frame",
                        "url": frame.get("url") or "",
                        "content_type": frame.get("content_type") or "image/jpeg",
                    })
                    if data:
                        await websocket.send_bytes(data)
            except Exception:
                return

        pump_task = asyncio.create_task(_pump())
        browser_context: dict[str, str] = {"session_id": "", "round_id": ""}

        async def _browser_page_meta() -> dict[str, str]:
            page = getattr(session, "_page", None)
            if page is None:
                return {"url": "", "title": ""}
            try:
                title = await page.title()
            except Exception:
                title = ""
            return {"url": str(getattr(page, "url", "") or ""), "title": str(title or "")}

        async def _record_browser_event(kind: str, payload: dict[str, Any]) -> None:
            try:
                from cyrene import behavior_learning as _behavior_learning

                meta = await _browser_page_meta()
                await _behavior_learning.record_browser_user_event(
                    session_id=browser_context.get("session_id", ""),
                    round_id=browser_context.get("round_id", ""),
                    event_kind=kind,
                    payload=payload,
                    browser_url=meta.get("url", ""),
                    browser_title=meta.get("title", ""),
                    target={
                        "x": payload.get("x"),
                        "y": payload.get("y"),
                        "button": payload.get("button"),
                    },
                )
            except Exception:
                logger.debug("failed to record browser user event", exc_info=True)

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                mtype = str(msg.get("type") or "")
                try:
                    if mtype == "context":
                        browser_context["session_id"] = str(msg.get("sessionId") or "").strip()
                        browser_context["round_id"] = str(msg.get("roundId") or "").strip()
                        continue
                    if mtype == "control":
                        # User took/released live control of the headless page.
                        on = bool(msg.get("on"))
                        session.set_user_control(on)
                        await _record_browser_event("control_start" if on else "control_stop", {"on": on})
                    elif mtype == "mouse":
                        payload = {
                            "event": str(msg.get("event") or ""),
                            "x": float(msg.get("x") or 0),
                            "y": float(msg.get("y") or 0),
                            "button": str(msg.get("button") or "none"),
                            "clickCount": int(msg.get("clickCount") or 0),
                            "deltaX": float(msg.get("deltaX") or 0),
                            "deltaY": float(msg.get("deltaY") or 0),
                            "modifiers": int(msg.get("modifiers") or 0),
                        }
                        await session.dispatch_mouse(
                            type=payload["event"],
                            x=payload["x"],
                            y=payload["y"],
                            button=payload["button"],
                            click_count=payload["clickCount"],
                            delta_x=payload["deltaX"],
                            delta_y=payload["deltaY"],
                            modifiers=payload["modifiers"],
                        )
                        if payload["event"] in {"mouseReleased", "mouseWheel"}:
                            await _record_browser_event("click" if payload["event"] == "mouseReleased" else "scroll", payload)
                    elif mtype == "key":
                        payload = {
                            "event": str(msg.get("event") or ""),
                            "key": str(msg.get("key") or ""),
                            "code": str(msg.get("code") or ""),
                            "text": str(msg.get("text") or ""),
                            "keyCode": int(msg.get("keyCode") or 0),
                            "modifiers": int(msg.get("modifiers") or 0),
                        }
                        await session.dispatch_key(
                            type=payload["event"],
                            key=payload["key"],
                            code=payload["code"],
                            text=payload["text"],
                            key_code=payload["keyCode"],
                            modifiers=payload["modifiers"],
                        )
                        if payload["event"] == "keyDown":
                            await _record_browser_event("key", payload)
                    elif mtype == "text":
                        # Committed string (IME composition result / paste).
                        text = str(msg.get("text") or "")
                        await session.insert_text(text)
                        await _record_browser_event("text", {"text": text})
                except Exception:
                    logger.debug("browser input dispatch failed", exc_info=True)
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            session.set_user_control(False)
            await session.stop_screencast(queue)

    @router.post("/api/browser/user-event")
    async def api_browser_user_event(request: Request):
        """Record a user-driven browser operation from the Electron BrowserView."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        try:
            from cyrene import behavior_learning as _behavior_learning

            await _behavior_learning.record_browser_user_event(
                session_id=str(body.get("sessionId") or body.get("session_id") or ""),
                round_id=str(body.get("roundId") or body.get("round_id") or ""),
                event_kind=str(body.get("eventKind") or body.get("kind") or "event"),
                payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
                browser_url=str(body.get("browserUrl") or body.get("url") or ""),
                browser_title=str(body.get("browserTitle") or body.get("title") or ""),
                target=body.get("target") if isinstance(body.get("target"), dict) else {},
            )
            return {"ok": True}
        except Exception as exc:
            logger.debug("failed to record browser user event", exc_info=True)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


    # ---- Browser API ----

    @router.post("/api/browser/navigate")
    async def api_browser_navigate(request: Request):
        from cyrene.browser import navigate
        body = await request.json()
        url = str(body.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "url is required"}
        result = await navigate(url)
        return result

    @router.post("/api/browser/takeover")
    async def api_browser_takeover():
        """User-initiated escape hatch: open the real (headed) browser window for
        sites that block headless (e.g. CAPTCHA). The in-panel live view pauses
        until the user returns via /api/browser/release (or closes the window)."""
        from cyrene import browser as _browser
        if _browser._ensure_playwright() is None:
            return {"ok": False, "error": _browser.browser_runtime_unavailable_message()}
        try:
            session = await _browser.get_session()
            url = await session.current_url()
            await session.open_user_window(url)
            return {"ok": True, "url": url, "mode": "headed"}
        except Exception as exc:
            logger.exception("user browser takeover failed")
            return {"ok": False, "error": _browser.browser_runtime_unavailable_message(exc)}

    @router.post("/api/browser/release")
    async def api_browser_release():
        """Return the user-opened native window to the in-panel headless view."""
        from cyrene import browser as _browser
        if _browser._ensure_playwright() is None:
            return {"ok": False, "error": _browser.browser_runtime_unavailable_message()}
        try:
            session = await _browser.get_session()
            await session.close_user_window(await session.current_url())
            return {"ok": True, "mode": "headless"}
        except Exception as exc:
            logger.exception("user browser release failed")
            return {"ok": False, "error": _browser.browser_runtime_unavailable_message(exc)}
