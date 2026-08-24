"""Shared HTTP serialization for Control route slices."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Awaitable

from fastapi.responses import FileResponse, JSONResponse

from cyrene.runtime.remote_commands import public_remote_event
from cyrene.workbench.control_services import ControlServiceError
from route.control_schemas import (
    ControlChatDetail,
    ControlChatSummary,
    ControlErrorResponse,
    ControlMessage,
    ControlRunEvent,
    ControlRunResponse,
    ControlTaskDetail,
    ControlTaskSummary,
)


COMMON_ERRORS = {
    400: {"model": ControlErrorResponse},
    404: {"model": ControlErrorResponse},
    409: {"model": ControlErrorResponse},
}


async def control_call(awaitable: Awaitable[Any]) -> Any:
    try:
        return await awaitable
    except ControlServiceError as exc:
        return control_error(exc)


def control_sync(call: Any) -> Any:
    try:
        return call()
    except ControlServiceError as exc:
        return control_error(exc)


def control_error(exc: ControlServiceError) -> JSONResponse:
    return JSONResponse(exc.payload, status_code=exc.status_code)


def chat_summary(raw: dict[str, Any], run_manager: Any) -> ControlChatSummary:
    chat_id = str(raw.get("id") or "")
    return ControlChatSummary(
        id=chat_id,
        project_id=str(raw.get("projectId") or ""),
        title=str(raw.get("title") or ""),
        status=str(raw.get("status") or "idle"),
        created_at=str(raw.get("createdAt") or ""),
        updated_at=str(raw.get("updatedAt") or ""),
        message_count=int(raw.get("messageCount") or len(raw.get("messages") or [])),
        running=run_manager.get(chat_id) is not None,
        awaiting_user=isinstance(raw.get("pendingQuestion"), dict),
    )


def _control_trace(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    public_keys = (
        "toolCallId", "tool_call_id", "tool", "text", "preview", "status",
        "failed", "kind", "detailKey", "detail_key", "detailParams",
        "detail_params", "progress", "progressCurrent", "progressTotal",
    )
    return [
        {key: item[key] for key in public_keys if key in item}
        for item in raw
        if isinstance(item, dict)
    ]


def message(raw: dict[str, Any], chat_id: str) -> ControlMessage:
    attachments = raw.get("attachments")
    trace = _control_trace(raw.get("trace"))
    reasoning_available = bool(str(raw.get("reasoning") or "").strip())
    return ControlMessage(
        id=str(raw.get("id") or ""), role=str(raw.get("role") or ""),
        content=str(raw.get("content") or ""), created_at=str(raw.get("createdAt") or ""),
        attachments=[
            {key: item[key] for key in ("id", "name", "type", "mediaType", "content_type", "kind", "size", "width", "height") if key in item}
            | {"download_url": f"/v1/control/chats/{chat_id}/attachments/{item.get('id')}"}
            for item in attachments or []
            if isinstance(item, dict) and str(item.get("id") or "")
        ],
        question_id=str(raw.get("questionId") or ""),
        question_kind=str(raw.get("questionKind") or ""),
        activity_card=bool(raw.get("activityCard") or reasoning_available or trace),
        intermediate=bool(raw.get("intermediate")),
        reasoning_available=reasoning_available,
        trace=trace,
    )


def chat_detail(raw: dict[str, Any], run_manager: Any) -> ControlChatDetail:
    summary = chat_summary(raw, run_manager)
    return ControlChatDetail(
        **summary.model_dump(),
        messages=[message(item, str(raw.get("id") or "")) for item in raw.get("messages") or [] if isinstance(item, dict)],
    )


def run_response(run: Any) -> ControlRunResponse:
    outcome = run.outcome if isinstance(run.outcome, dict) else {}
    return ControlRunResponse(
        run_id=run.run_id, chat_id=run.chat_id, status=str(run.status or "running"),
        created_at=str(run.created_at or ""), completed=run.done.is_set(),
        termination_reason=str(run.termination_reason or ""), outcome=str(outcome.get("kind") or ""),
        last_event_cursor=max((int(event.get("_seq") or 0) for event in run.events), default=0),
    )


def task_summary(raw: dict[str, Any]) -> ControlTaskSummary:
    return ControlTaskSummary(
        id=str(raw.get("id") or ""), project_id=str(raw.get("projectId") or ""),
        title=str(raw.get("title") or ""), goal=str(raw.get("goal") or ""),
        status=str(raw.get("status") or "idle"), priority=str(raw.get("priority") or "medium"),
        created_at=str(raw.get("createdAt") or ""), updated_at=str(raw.get("updatedAt") or ""),
        artifact_count=len(raw.get("artifacts") or []),
    )


def task_detail(raw: dict[str, Any]) -> ControlTaskDetail:
    summary = task_summary(raw)
    public_types = {"AgentResponseEvent", "ExecutionFailed", "ExecutionFinished", "ExecutionStarted", "PlanApproved", "PlanGenerated", "PlanRevised", "UserMessageEvent"}
    pending = raw.get("pendingQuestion")
    goal_loop = raw.get("goalLoop")
    return ControlTaskDetail(
        **summary.model_dump(),
        plan=[{key: item[key] for key in ("id", "title", "description", "status", "dependsOn") if key in item} for item in raw.get("plan") or [] if isinstance(item, dict)],
        pending_question=({key: pending[key] for key in ("id", "questionId", "kind", "questionKind", "prompt", "question", "title", "options", "choices") if key in pending} if isinstance(pending, dict) else None),
        events=[{key: item[key] for key in ("id", "type", "createdAt", "body", "stepId") if key in item} for item in raw.get("events") or [] if isinstance(item, dict) and str(item.get("type") or "") in public_types],
        artifacts=[{key: item[key] for key in ("id", "name", "type", "status", "createdAt", "size") if key in item} for item in raw.get("artifacts") or [] if isinstance(item, dict)],
        goal_loop=({key: goal_loop[key] for key in ("id", "status", "phase", "currentStepId", "stopReason", "activeSeconds", "maxActiveSeconds", "repairRound", "maxRepairRounds", "updatedAt") if key in goal_loop} if isinstance(goal_loop, dict) else None),
    )


def public_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    if str(raw.get("type") or "") in {"reasoning_delta", "reasoning_done", "reasoning_start"}:
        return None
    return public_remote_event(raw)


def run_event(raw: dict[str, Any]) -> ControlRunEvent:
    data = {key: value for key, value in raw.items() if key not in {"cursor", "run_id", "type"}}
    return ControlRunEvent(cursor=int(raw["cursor"]), run_id=str(raw["run_id"]), type=str(raw["type"]), data=data)


def file_response(path: Path, filename: str, media_type: str = "") -> FileResponse:
    return FileResponse(path, filename=filename, media_type=media_type or mimetypes.guess_type(filename)[0] or "application/octet-stream")


__all__ = [
    "COMMON_ERRORS", "chat_detail", "chat_summary", "control_call", "control_sync",
    "file_response", "message", "public_event", "run_event", "run_response", "task_detail", "task_summary",
]
