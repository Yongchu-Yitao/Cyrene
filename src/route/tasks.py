"""Legacy scheduled-task HTTP compatibility over the schedule application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.workbench.schedule_service import (
    CreateScheduleCommand,
    ScheduleApplicationError,
    ScheduleApplicationService,
    UpdateScheduleCommand,
)


def _legacy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "workspace"}


async def _invoke(
    operation: Awaitable[dict[str, Any]],
) -> dict[str, Any] | JSONResponse:
    try:
        return _legacy_payload(await operation)
    except ScheduleApplicationError as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)


def register_task_routes(
    router: APIRouter,
    *,
    application_service: ScheduleApplicationService,
    request_shutdown: Callable[[], None],
) -> None:
    @router.get("/api/tasks")
    async def api_list_tasks():
        return await _invoke(application_service.list_tasks("default"))

    @router.post("/api/tasks")
    async def api_create_task(request: Request):
        return await _invoke(
            application_service.create(
                CreateScheduleCommand("default", await request.json())
            )
        )

    @router.put("/api/tasks/{task_id}")
    async def api_update_task(task_id: str, request: Request):
        return await _invoke(
            application_service.update(
                UpdateScheduleCommand(task_id, "default", await request.json())
            )
        )

    @router.delete("/api/tasks/{task_id}")
    async def api_delete_task(task_id: str):
        return await _invoke(application_service.delete(task_id, "default"))

    @router.post("/api/shutdown")
    async def api_shutdown():
        request_shutdown()
        return {"ok": True}


__all__ = ["register_task_routes"]
