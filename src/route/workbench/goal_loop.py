"""Thin FastAPI adapters for durable Workbench goal-loop execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.goal_loop_service import (
    GoalLoopApplicationError,
    GoalLoopApplicationService,
    GoalLoopLimitsCommand,
    GoalLoopPreviewCommand,
    GoalLoopStartCommand,
)
from route import schemas as api_models


@dataclass(frozen=True)
class GoalLoopRouteRegistration:
    manager: Any
    control_adapter: dict[str, Callable[..., Awaitable[Any]]]


async def _invoke(operation: Awaitable[dict[str, Any]]) -> dict[str, Any] | JSONResponse:
    try:
        return await operation
    except GoalLoopApplicationError as exc:
        return JSONResponse(exc.payload, status_code=exc.status_code)


def register_goal_loop_routes(
    router: APIRouter,
    app: Any,
    *,
    application_service: GoalLoopApplicationService,
    manager: Any,
) -> GoalLoopRouteRegistration:
    app.state.goal_loop_manager = manager

    @router.post("/api/task-sessions/{session_id}/goal-loop/preview")
    async def preview_goal_loop(
        session_id: str, body_model: api_models.GoalLoopPreviewBody
    ):
        return await _invoke(application_service.preview(GoalLoopPreviewCommand(
            session_id, api_models.body_dict(body_model)
        )))

    @router.post("/api/task-sessions/{session_id}/goal-loop/start")
    async def start_goal_loop(
        session_id: str, body_model: api_models.GoalLoopStartBody
    ):
        body = api_models.body_dict(body_model)
        return await _invoke(application_service.start(GoalLoopStartCommand(
            session_id, str(body.get("draftId") or "").strip()
        )))

    @router.get("/api/task-sessions/{session_id}/goal-loop")
    async def get_goal_loop(session_id: str):
        return await _invoke(application_service.get(session_id))

    @router.post("/api/task-sessions/{session_id}/goal-loop/pause")
    async def pause_goal_loop(session_id: str):
        return await _invoke(application_service.pause(session_id))

    @router.post("/api/task-sessions/{session_id}/goal-loop/resume")
    async def resume_goal_loop(session_id: str):
        return await _invoke(application_service.resume(session_id))

    @router.post("/api/task-sessions/{session_id}/goal-loop/cancel")
    async def cancel_goal_loop(session_id: str):
        return await _invoke(application_service.cancel(session_id))

    @router.patch("/api/task-sessions/{session_id}/goal-loop/limits")
    async def update_goal_loop_limits(
        session_id: str, body_model: api_models.GoalLoopLimitsBody
    ):
        return await _invoke(application_service.update_limits(GoalLoopLimitsCommand(
            session_id, api_models.body_dict(body_model)
        )))

    return GoalLoopRouteRegistration(manager, {
        "get": get_goal_loop,
        "pause": pause_goal_loop,
        "resume": resume_goal_loop,
        "cancel": cancel_goal_loop,
    })


__all__ = ["GoalLoopRouteRegistration", "register_goal_loop_routes"]
