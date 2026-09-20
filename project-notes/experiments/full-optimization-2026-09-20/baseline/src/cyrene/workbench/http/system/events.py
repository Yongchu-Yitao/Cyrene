"""Thin runtime event and context-debug HTTP adapters."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from cyrene.observability.debug_event_repository import DebugEventRepository
from cyrene.workbench.http.errors import localized_error_response


def register_event_routes(
    router: APIRouter, repository: DebugEventRepository
) -> None:
    @router.get("/api/events")
    async def api_events(request: Request, session_id: str = ""):
        async def event_stream():
            async for event in repository.subscribe(session_id):
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
        return repository.recent_summaries(session_id)

    @router.get("/api/events/{event_id}")
    async def api_event_detail(event_id: str):
        event = repository.get(event_id)
        if event is None:
            return localized_error_response(
                "Event not found.",
                "未找到事件。",
                404,
                "event_not_found",
            )
        return event

    @router.get("/api/context-debug/events")
    async def api_context_debug_events(request: Request):
        """List recent LLM calls that have context trace metadata."""
        try:
            limit = int(request.query_params.get("limit") or "120")
        except ValueError:
            limit = 120
        return repository.context_events(max(1, min(limit, 500)))

    @router.get("/api/context-debug/events/{event_id}")
    async def api_context_debug_event_detail(event_id: str):
        event = repository.get_llm_call(event_id)
        if event is None:
            return localized_error_response(
                "Event not found.",
                "未找到事件。",
                404,
                "event_not_found",
            )
        return event


__all__ = ["register_event_routes"]
