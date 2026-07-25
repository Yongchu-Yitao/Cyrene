"""Claude Code routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *


def register_claude_code_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Claude Code terminal / learning ----

    @router.get("/api/cc/status")
    async def api_cc_status():
        return get_cc_status(_CC_PROJECT_DIR)

    @router.get("/api/status")
    async def api_status():
        return await _build_status()

    async def _build_cc_learning_snapshot() -> dict[str, Any]:
        status = get_cc_status(_CC_PROJECT_DIR)
        latest_jsonl = str(status.get("latest_jsonl") or "").strip()
        if not latest_jsonl:
            return {
                "available": False,
                "reason": "No Claude transcript found for learning.",
                "summary": {"highlights": [], "top_tools": [], "top_tasks": []},
            }
        analysis = await asyncio.to_thread(analyze_session, Path(latest_jsonl))
        return {
            "available": True,
            **analysis,
        }

    @router.get("/api/cc/learning")
    async def api_cc_learning():
        return await _build_cc_learning_snapshot()

    @router.post("/api/cc/learn")
    async def api_cc_learn():
        status = get_cc_status(_CC_PROJECT_DIR)
        latest_jsonl = str(status.get("latest_jsonl") or "").strip()
        if not latest_jsonl:
            return JSONResponse({"error": "no Claude transcript found"}, status_code=404)
        result = await asyncio.to_thread(learn_from_session, Path(latest_jsonl))
        await debug.publish_event(
            {
                "type": "cc_learning",
                "phase": "completed",
                "user_input": "",
                "latest_jsonl": latest_jsonl,
                "highlights": result.get("summary", {}).get("highlights", []),
                "top_tools": result.get("summary", {}).get("top_tools", []),
                "top_tasks": result.get("summary", {}).get("top_tasks", []),
            }
        )
        return result

    @router.websocket("/ws/cc-terminal/{tmux_session}")
    async def ws_cc_terminal(websocket: WebSocket, tmux_session: str):
        await websocket.accept()
        session = CCTerminalSession(tmux_session)
        input_buffer = ""

        try:
            await session.start()
        except Exception:
            logger.exception("Failed to attach CC terminal to tmux session %s", tmux_session)
            await websocket.send_text("\r\n[Cyrene] Failed to attach to tmux session.\r\n")
            await websocket.close(code=1011)
            return

        stream_task = asyncio.create_task(session.stream_to_ws(websocket))
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                message_type = str(payload.get("type") or "").strip()
                if message_type == "input":
                    data = str(payload.get("data") or "")
                    await session.handle_input(data)
                    input_buffer, submitted = _consume_cc_input_buffer(input_buffer, data)
                    for prompt in submitted:
                        asyncio.create_task(_publish_cc_learning(prompt, tmux_session=tmux_session))
                elif message_type == "resize":
                    await session.handle_resize(int(payload.get("cols") or 80), int(payload.get("rows") or 24))
        except WebSocketDisconnect:
            pass
        finally:
            stream_task.cancel()
            await session.stop()
