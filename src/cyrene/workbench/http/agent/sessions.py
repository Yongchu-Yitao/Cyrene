"""HTTP adapters for Workbench conversation presentation and data actions."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.workbench.artifacts.presentation_service import (
    WorkbenchSessionApplicationService,
)
from cyrene.workbench.sessions.session_presentation import WorkbenchSessionError
from cyrene.workbench.http.errors import error_response


async def _session_call(call: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await call()
    except WorkbenchSessionError as exc:
        code = {
            404: "conversation_not_found",
            409: "conversation_running",
            503: "session_service_unavailable",
        }.get(exc.status_code, "invalid_session_request")
        return error_response(str(exc), exc.status_code, code)


def register_session_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    """Register repository-backed Workbench session endpoints.

    ``bot`` remains in the composition signature until the route registry is
    simplified; the session presentation layer does not use it.
    """
    del bot
    service = WorkbenchSessionApplicationService(db_path)

    @router.get("/api/workbench/sessions")
    async def api_workbench_sessions(include_status: bool = False):
        result = await _session_call(lambda: service.list_sessions(include_status=include_status))
        if isinstance(result, dict) and result.get("status_error"):
            # Keep HTTP failure visible to diagnostics while allowing the UI
            # to apply the independently successful session projection.
            return JSONResponse(status_code=500, content=result)
        return result

    @router.post("/api/workbench/sessions/{chat_id}/clear")
    async def api_workbench_clear_session(chat_id: str):
        return await _session_call(lambda: service.clear_session(chat_id))

    @router.delete("/api/workbench/sessions/{chat_id}")
    async def api_workbench_delete_session(chat_id: str):
        return await _session_call(lambda: service.delete_session(chat_id))

    @router.get("/api/workbench/sessions/{chat_id}/export")
    async def api_workbench_export_session(chat_id: str, format: str = "markdown"):
        result = await _session_call(lambda: service.export_session(chat_id, format))
        if isinstance(result, JSONResponse):
            return result
        return StreamingResponse(
            iter([result.content]),
            media_type=result.media_type,
            headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
        )


__all__ = ["register_session_routes"]
