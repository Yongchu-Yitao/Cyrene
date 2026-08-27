"""HTTP adapters for Workbench conversation presentation and data actions."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.workbench.presentation_service import (
    WorkbenchSessionApplicationService,
)
from cyrene.workbench.session_presentation import WorkbenchSessionError


async def _session_call(call: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await call()
    except WorkbenchSessionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)


def register_session_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    """Register repository-backed Workbench session endpoints.

    ``bot`` remains in the composition signature until the route registry is
    simplified; the session presentation layer does not use it.
    """
    del bot
    service = WorkbenchSessionApplicationService(db_path)

    @router.get("/api/workbench/sessions")
    async def api_workbench_sessions():
        return await _session_call(service.list_sessions)

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
