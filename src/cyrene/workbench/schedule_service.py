"""Application operations for the Workbench schedule/calendar API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cyrene.runtime.schedule_spec import compute_next_run, resolve_schedule_timezone
from cyrene.workbench.schedule_domain import entity_events, occurrence_window, task_events
from cyrene.workbench.schedule_repository import ScheduleRepositoryPort, WorkspaceProjectResolver

DEFAULT_CHAT_ID = -1


class ScheduleApplicationError(Exception):
    def __init__(self, message: str, status_code: int, *, code: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True, slots=True)
class CreateScheduleCommand:
    workspace: str
    values: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UpdateScheduleCommand:
    task_id: str
    workspace: str
    values: dict[str, Any]


class ScheduleApplicationService:
    def __init__(
        self,
        repository: ScheduleRepositoryPort,
        workspace_resolver: WorkspaceProjectResolver,
        notify: Callable[..., Any],
    ) -> None:
        self.repository = repository
        self.workspace_resolver = workspace_resolver
        self.notify = notify

    async def list_tasks(self, workspace: str) -> dict[str, Any]:
        resolved = self.workspace_resolver.resolve(workspace)
        return {"tasks": await self.repository.list_tasks(resolved), "workspace": resolved}

    async def list_occurrences(self, workspace: str, start: str, end: str) -> dict[str, Any]:
        start_at, end_at = occurrence_window(start, end)
        resolved = self.workspace_resolver.resolve(workspace)
        tasks, entities = await asyncio.gather(
            self.repository.list_tasks(resolved),
            self.repository.list_deadline_entities(resolved),
        )
        events: list[dict[str, Any]] = []
        for task in tasks:
            events.extend(task_events(task, start_at, end_at))
        events.extend(entity_events(entities, start_at, end_at))
        events.sort(key=lambda event: str(event.get("start") or ""))
        return {"events": events, "start": start_at.isoformat(), "end": end_at.isoformat(), "workspace": resolved}

    async def create(self, command: CreateScheduleCommand) -> dict[str, Any]:
        values = _create_values(command.values)
        resolved = self.workspace_resolver.resolve(command.workspace)
        task_id = await self.repository.create({**values, "project_id": resolved})
        self.notify(
            title="日程提醒已创建",
            body=f"已添加提醒：{values['prompt'][:80]}",
            tab="system",
            project_ref=resolved,
            source="schedule_created",
            source_label="日程",
            link_label="日程",
            meta={"taskId": task_id},
        )
        return {"ok": True, "id": task_id, "tasks": await self.repository.list_tasks(resolved), "workspace": resolved}

    async def update(self, command: UpdateScheduleCommand) -> dict[str, Any]:
        values = _update_values(command.values)
        resolved = self.workspace_resolver.resolve(command.workspace)
        await self.repository.update(command.task_id, resolved, values)
        self.notify(
            title="日程提醒已更新",
            body=f"提醒任务已更新：{str(values.get('prompt') or command.task_id)[:80]}",
            tab="system",
            project_ref=resolved,
            source="schedule_updated",
            source_label="日程",
            link_label="日程",
            meta={"taskId": command.task_id},
        )
        return {"ok": True, "tasks": await self.repository.list_tasks(resolved), "workspace": resolved}

    async def delete(self, task_id: str, workspace: str) -> dict[str, Any]:
        resolved = self.workspace_resolver.resolve(workspace)
        if not await self.repository.delete(task_id, resolved):
            raise ScheduleApplicationError("task not found", 404)
        return {"ok": True, "tasks": await self.repository.list_tasks(resolved), "workspace": resolved}

    async def list_runs(self, task_id: str, workspace: str, limit: int) -> dict[str, Any]:
        resolved = self.workspace_resolver.resolve(workspace)
        bounded_limit = max(1, min(int(limit), 100))
        return {"runs": await self.repository.list_runs(task_id, resolved, bounded_limit)}


def _create_values(body: dict[str, Any]) -> dict[str, Any]:
    prompt = str(body.get("prompt") or "").strip()
    schedule_type = str(body.get("schedule_type") or "").strip()
    schedule_value = str(body.get("schedule_value") or "").strip()
    schedule_timezone = str(body.get("schedule_timezone") or "UTC").strip() or "UTC"
    if not prompt:
        raise ScheduleApplicationError("prompt is required", 400)
    if not schedule_type or not schedule_value:
        raise ScheduleApplicationError("schedule_type and schedule_value are required", 400)
    if schedule_type == "cron":
        _validate_timezone(schedule_timezone)
    next_run = str(body.get("next_run") or "").strip()
    if not next_run:
        next_run = _compute_next_run(schedule_type, schedule_value, schedule_timezone)
    return {
        "chat_id": int(body.get("chat_id", DEFAULT_CHAT_ID)), "prompt": prompt,
        "schedule_type": schedule_type, "schedule_value": schedule_value, "next_run": next_run,
        "permission_mode": "workspace_only", "schedule_timezone": schedule_timezone,
        "origin_session_id": str(body.get("origin_session_id") or "").strip(),
        "action_type": str(body.get("action_type") or "agent_task"),
    }


def _update_values(body: dict[str, Any]) -> dict[str, Any]:
    values = dict(body)
    schedule_type = values.get("schedule_type")
    schedule_value = values.get("schedule_value")
    schedule_timezone = values.get("schedule_timezone") or "UTC"
    if values.get("schedule_timezone") is not None:
        _validate_timezone(str(schedule_timezone))
    if schedule_type and schedule_value and "next_run" not in values:
        values["next_run"] = _compute_next_run(str(schedule_type), str(schedule_value), str(schedule_timezone))
    editable = {
        field: values[field]
        for field in (
            "prompt", "action_type", "schedule_type", "schedule_value",
            "schedule_timezone", "next_run", "status",
        )
        if field in values
    }
    if not editable:
        raise ScheduleApplicationError("no updatable fields provided", 400)
    return editable


def _validate_timezone(timezone_name: str) -> None:
    try:
        resolve_schedule_timezone(timezone_name)
    except ValueError as exc:
        raise ScheduleApplicationError(str(exc), 400) from exc


def _compute_next_run(schedule_type: str, schedule_value: str, timezone_name: str) -> str:
    try:
        return compute_next_run(schedule_type, schedule_value, timezone_name=timezone_name)
    except ValueError as exc:
        raise ScheduleApplicationError(str(exc), 400) from exc


__all__ = ["CreateScheduleCommand", "ScheduleApplicationError", "ScheduleApplicationService", "UpdateScheduleCommand"]
