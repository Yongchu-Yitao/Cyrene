"""HTTP routes owned by the schedule Plugin pack."""

from __future__ import annotations

import logging
from typing import Any, Awaitable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .workbench_service import (
    CreateScheduleCommand,
    ScheduleApplicationError,
    ScheduleApplicationService,
    UpdateScheduleCommand,
)
from route import schemas as api_models
from route.errors import error_response

logger = logging.getLogger(__name__)


async def _invoke(
    operation: Awaitable[dict[str, Any]],
    *,
    failure_message: str,
    failure_code: str,
    log_message: str,
    log_args: tuple[Any, ...],
) -> dict[str, Any] | JSONResponse:
    try:
        return await operation
    except ScheduleApplicationError as exc:
        payload = {"error": exc.message, **({"code": exc.code} if exc.code else {})}
        return JSONResponse(payload, status_code=exc.status_code)
    except Exception:
        logger.exception(log_message, *log_args)
        return error_response(failure_message, 500, failure_code)


def register_workbench_schedule_routes(
    router: APIRouter,
    *,
    application_service: ScheduleApplicationService,
) -> None:
    @router.get("/api/workbench/schedule/tasks")
    async def wb_list_tasks(workspace: str = "default"):
        """Raw scheduled tasks (agent 定时任务) for the management/list view."""
        return await _invoke(
            application_service.list_tasks(workspace),
            failure_message="List failed", failure_code="schedule_list_failed",
            log_message="Failed to list scheduled tasks for %s", log_args=(workspace,),
        )

    @router.get("/api/workbench/schedule/occurrences")
    async def wb_list_occurrences(start: str = "", end: str = "", workspace: str = "default"):
        """Expand tasks + entity deadlines into dated events within a window.

        ``start`` / ``end`` are ISO-8601 (any tz; naive treated as UTC). When
        omitted the window defaults to the next 60 days from now.
        """
        return await _invoke(
            application_service.list_occurrences(workspace, start, end),
            failure_message="Occurrences failed", failure_code="schedule_occurrences_failed",
            log_message="Failed to list schedule occurrences for %s", log_args=(workspace,),
        )

    @router.post("/api/workbench/schedule/tasks")
    async def wb_create_task(
        body_model: api_models.ScheduleCreateBody, workspace: str = "default"
    ):
        """Create a workspace-only scheduled task through the schedule Plugin.

        Full-access schedules must be created through ``schedule.create`` in
        chat, where the exact Plugin call is reviewed before execution.
        """
        return await _invoke(
            application_service.create(CreateScheduleCommand(workspace, api_models.body_dict(body_model))),
            failure_message="Create failed", failure_code="schedule_create_failed",
            log_message="Failed to create scheduled task for %s", log_args=(workspace,),
        )

    @router.put("/api/workbench/schedule/tasks/{task_id}")
    async def wb_update_task(
        task_id: str,
        body_model: api_models.ScheduleUpdateBody,
        workspace: str = "default",
    ):
        """Update a task's prompt / schedule / status. Recomputes ``next_run``
        when the schedule changes (an invalid schedule is a 400)."""
        return await _invoke(
            application_service.update(UpdateScheduleCommand(
                task_id, workspace, api_models.body_dict(body_model)
            )),
            failure_message="Update failed", failure_code="schedule_update_failed",
            log_message="Failed to update scheduled task %s for %s", log_args=(task_id, workspace),
        )

    @router.delete("/api/workbench/schedule/tasks/{task_id}")
    async def wb_delete_task(task_id: str, workspace: str = "default"):
        return await _invoke(
            application_service.delete(task_id, workspace),
            failure_message="Delete failed", failure_code="schedule_delete_failed",
            log_message="Failed to delete scheduled task %s for %s", log_args=(task_id, workspace),
        )

    @router.get("/api/workbench/schedule/tasks/{task_id}/runs")
    async def wb_task_runs(task_id: str, limit: int = 20, workspace: str = "default"):
        """Recent run history for a task (from ``task_run_logs``)."""
        return await _invoke(
            application_service.list_runs(task_id, workspace, limit),
            failure_message="Runs failed", failure_code="schedule_runs_failed",
            log_message="Failed to list runs for scheduled task %s in %s", log_args=(task_id, workspace),
        )


__all__ = ["register_workbench_schedule_routes"]
