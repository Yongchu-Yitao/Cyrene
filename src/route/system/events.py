"""Runtime event and context-debug routes."""

# ruff: noqa: F403,F405

from cyrene.workbench.runtime import *


def register_event_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- SSE ----

    @router.get("/api/events")
    async def api_events(request: Request, session_id: str = ""):
        from cyrene.observability.debug import subscribe

        async def event_stream():
            async for event in subscribe(session_id=session_id):
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/api/events/list")
    async def api_events_list(session_id: str = ""):
        """List recent event IDs."""
        from cyrene.observability.debug import get_recent_events
        events = get_recent_events(50)
        result = []
        for e in events:
            if session_id and e.get("session_id") not in (session_id, ""):
                continue
            eid = e.get("event_id", "")
            if eid:
                result.append({"id": eid, "type": e.get("type", "?"), "caller": e.get("caller", "?")})
        return {"events": result}

    @router.get("/api/events/{event_id}")
    async def api_event_detail(event_id: str):
        from cyrene.observability.debug import get_full_event
        event = get_full_event(event_id)
        if event is None:
            return JSONResponse({"error": "event not found"}, status_code=404)
        return event

    @router.get("/api/context-debug/events")
    async def api_context_debug_events(request: Request):
        """List recent LLM calls that have context trace metadata."""
        try:
            limit = int(request.query_params.get("limit") or "120")
        except ValueError:
            limit = 120
        limit = max(1, min(limit, 500))
        events_by_id: dict[str, dict[str, Any]] = {}

        def add_event(raw: dict[str, Any], log_file: str = "") -> None:
            if raw.get("type") != "llm_call":
                return
            event_id = str(raw.get("event_id") or "").strip()
            if not event_id:
                return
            trace = raw.get("context_trace") if isinstance(raw.get("context_trace"), dict) else {}
            included = trace.get("included") if isinstance(trace.get("included"), list) else []
            events_by_id[event_id] = {
                "id": event_id,
                "timestamp": raw.get("timestamp") or "",
                "caller": raw.get("caller") or "",
                "phase": raw.get("phase") or "",
                "model": raw.get("model") or "",
                "duration_ms": raw.get("duration_ms"),
                "total_tokens_est": int(trace.get("total_tokens_est") or 0),
                "block_count": len(included),
                "message_count": len(raw.get("messages") or []),
                "token_by_type": trace.get("token_by_type") or {},
                "source_log": log_file,
            }

        for event in debug.get_recent_events(500):
            add_event(event)

        if DATA_DIR.exists():
            for log_file in sorted(DATA_DIR.glob("debug_*.jsonl"), reverse=True)[:20]:
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                add_event(json.loads(line), log_file.name)
                            except Exception:
                                continue
                except Exception:
                    continue

        events = sorted(
            events_by_id.values(),
            key=lambda item: str(item.get("timestamp") or ""),
            reverse=True,
        )[:limit]
        return {"events": events}

    @router.get("/api/context-debug/events/{event_id}")
    async def api_context_debug_event_detail(event_id: str):
        event = debug.get_full_event(event_id)
        if event is None or event.get("type") != "llm_call":
            return JSONResponse({"error": "event not found"}, status_code=404)
        return event
