"""Thin HTTP adapters for the global agent chat."""
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from cyrene.agent import SessionRunConflictError
from cyrene.model_runtime.errors import format_httpx_error
from cyrene.workbench.global_chat_service import (
    GlobalChatAnswerCommand, GlobalChatApplicationError,
    GlobalChatApplicationService, GlobalChatResult, GlobalChatTurnCommand,
)
logger = logging.getLogger(__name__)


def register_chat_routes(
    router: APIRouter,
    service: GlobalChatApplicationService,
) -> None:
    @router.post("/api/chat/upload")
    async def api_chat_upload(files: list[UploadFile]):
        return await _execute(service.upload(files))

    @router.get("/api/chat/upload/{upload_id}")
    async def api_chat_upload_file(upload_id: str):
        return _execute_sync(lambda: GlobalChatResult(file_path=service.resolve_upload(upload_id)))

    @router.get("/api/chat/export/{export_id}")
    async def api_chat_export_file(export_id: str):
        return _execute_sync(lambda: GlobalChatResult(file_path=service.resolve_export(export_id)))

    @router.post("/api/chat")
    async def api_chat(request: Request):
        return await _execute(service.submit(GlobalChatTurnCommand.from_payload(await request.json())))

    @router.post("/api/chat/answer-question")
    async def api_answer_question(request: Request):
        command = GlobalChatAnswerCommand.from_payload(await request.json())
        return await _execute(service.answer(command))

    @router.get("/api/chat/history")
    async def api_chat_history():
        return _execute_sync(lambda: GlobalChatResult(payload=service.history()))

    @router.get("/api/chat/state")
    async def api_chat_state():
        """Return raw session state (with round_id, tool_calls, etc.)."""
        return _execute_sync(lambda: GlobalChatResult(payload=service.state()))
    @router.post("/api/chat/interrupt")
    async def api_interrupt_chat(session_id: str = ""):
        return await _execute(service.interrupt(session_id))

    @router.post("/api/chat/clear")
    async def api_clear_session():
        return await _execute(service.clear())

    @router.get("/api/subagents")
    async def api_subagents(session_id: str = ""):
        return _execute_sync(lambda: GlobalChatResult(payload=service.list_subagents(session_id)))

    @router.get("/api/rounds/live")
    async def api_live_rounds():
        return service.live_rounds()


async def _execute(operation: Awaitable[GlobalChatResult | dict[str, Any]]):
    try:
        result = await operation
        return _render(result if isinstance(result, GlobalChatResult) else GlobalChatResult(payload=result))
    except Exception as exc:
        return _error_response(exc)


def _execute_sync(operation: Callable[[], GlobalChatResult]):
    try:
        return _render(operation())
    except Exception as exc:
        return _error_response(exc)


def _render(result: GlobalChatResult):
    if result.file_path is not None:
        return FileResponse(result.file_path)
    if result.events is not None:
        return StreamingResponse(
            _ndjson(result.events),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )
    return result.payload or {}


async def _ndjson(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    async for event in events:
        yield json.dumps(event, ensure_ascii=False) + "\n"


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, GlobalChatApplicationError):
        return JSONResponse(exc.payload, status_code=exc.status_code)
    if isinstance(exc, SessionRunConflictError):
        return JSONResponse(
            {"error": "该会话已有正在执行的请求，请等待完成或先明确停止它。", "code": "task_run_in_progress"},
            status_code=409,
        )
    if isinstance(exc, ValueError):
        return JSONResponse({"error": str(exc)}, status_code=400)
    if isinstance(exc, httpx.TimeoutException):
        logger.exception("Chat request timed out: %s", format_httpx_error(exc))
        return JSONResponse({"error": "upstream model timed out", "detail": str(exc)}, status_code=504)
    if isinstance(exc, httpx.HTTPError):
        logger.exception("Chat request failed: %s", format_httpx_error(exc))
        return JSONResponse({"error": "upstream model request failed", "detail": str(exc)}, status_code=502)
    logger.exception("Chat request crashed")
    return JSONResponse({"error": "internal server error", "detail": str(exc)}, status_code=500)
