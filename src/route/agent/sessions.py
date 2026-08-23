"""HTTP adapters for legacy and Workbench conversation sessions."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.agent.session_services import SessionApplicationService, SessionServiceError


async def _session_call(call: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await call()
    except SessionServiceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)


def _session_sync(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except SessionServiceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)


def register_session_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    service = SessionApplicationService(db_path)

    @router.get("/api/sessions")
    async def api_sessions():
        return await _session_call(service.list_sessions)

    @router.post("/api/sessions")
    async def api_create_session():
        return await _session_call(service.create_session)

    @router.get("/api/sessions/archive-context")
    async def api_archive_context(cursor: str = ""):
        return _session_sync(lambda: service.archive_context(cursor))

    @router.delete("/api/sessions/{session_id}")
    async def api_delete_session(session_id: str):
        return await _session_call(lambda: service.delete_session(session_id))

    @router.get("/api/sessions/{session_id}/export")
    async def api_export_session(session_id: str, format: str = "markdown"):
        result = await _session_call(lambda: service.export_session(session_id, format))
        if isinstance(result, JSONResponse):
            return result
        return StreamingResponse(
            iter([result.content]),
            media_type=result.media_type,
            headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
        )


__all__ = ["register_session_routes"]
