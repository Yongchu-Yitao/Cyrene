"""Workbench HTTP application adapter over the schedule Plugin pack."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.plugin import (
    PluginContext,
    PluginRegistry,
    PluginRuntime,
    default_plugin_impl_directory,
)
from agent.plugin.native_tools import seed_builtin_plugin_directory

from cyrene.runtime.schedule_runtime import get_schedule_runtime
from cyrene.workbench.schedule_domain import entity_events, occurrence_window
from cyrene.workbench.schedule_repository import WorkspaceProjectResolver


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


class SchedulePluginGateway:
    """Invoke the installed editable pack through the normal Plugin Runtime."""

    def __init__(
        self,
        db_path: str,
        *,
        bot: Any = None,
        plugin_directory: str | Path | None = None,
    ) -> None:
        self.plugin_directory = Path(
            plugin_directory or default_plugin_impl_directory()
        ).expanduser().resolve()
        seed_builtin_plugin_directory(self.plugin_directory)
        self.registry = PluginRegistry()
        failures = self.registry.load_directory(self.plugin_directory)
        self._raise_schedule_load_failure(failures)
        self.runtime = PluginRuntime(self.registry)
        self.service = get_schedule_runtime(
            db_path,
            bot=bot,
            plugin_directory=self.plugin_directory,
        )

    @staticmethod
    def _raise_schedule_load_failure(failures: Any) -> None:
        failure = next(
            (
                item
                for item in failures
                if getattr(getattr(item, "path", None), "name", "")
                == "cyrene_schedule"
            ),
            None,
        )
        if failure is not None:
            raise RuntimeError(
                "cyrene_schedule Plugin failed to load: " + str(failure.error)
            )

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        project_id: str,
    ) -> dict[str, Any]:
        failures = self.registry.refresh()
        self._raise_schedule_load_failure(failures)
        result = await self.runtime.call(
            name,
            dict(arguments),
            PluginContext(
                workspace=self.service.workspace_for_project(project_id),
                data={
                    "source": "workbench",
                    "project_id": project_id,
                    "session_id": "",
                    "chat_id": -1,
                },
                services={"schedules": self.service},
            ),
            call_id=f"workbench:schedule:{uuid4().hex}",
        )
        if not result.success:
            message = str(result.error or f"{name} failed")
            status = 404 if "not found" in message.lower() else 400
            raise ScheduleApplicationError(
                message,
                status,
                code="schedule_plugin_failed",
            )
        if not isinstance(result.value, dict):
            raise ScheduleApplicationError(
                f"{name} returned an invalid result",
                500,
                code="schedule_plugin_result_invalid",
            )
        return dict(result.value)


class ScheduleApplicationService:
    def __init__(
        self,
        db_path: str,
        workspace_resolver: WorkspaceProjectResolver,
        notify: Callable[..., Any],
        *,
        entities: Any,
        bot: Any = None,
        plugin_directory: str | Path | None = None,
    ) -> None:
        self.workspace_resolver = workspace_resolver
        self.notify = notify
        self.gateway = SchedulePluginGateway(
            db_path,
            bot=bot,
            plugin_directory=plugin_directory,
        )
        if entities is None:
            raise RuntimeError("The cyrene_entity application Plugin is required")
        self.entities = entities

    async def tasks_for_project(self, project_id: str) -> list[dict[str, Any]]:
        result = await self.gateway.call(
            "schedule.list", {}, project_id=project_id
        )
        tasks = result.get("tasks")
        return (
            [dict(item) for item in tasks if isinstance(item, dict)]
            if isinstance(tasks, list)
            else []
        )

    async def list_tasks(self, workspace: str) -> dict[str, Any]:
        resolved = self.workspace_resolver.resolve(workspace)
        return {
            "tasks": await self.tasks_for_project(resolved),
            "workspace": resolved,
        }

    async def list_all_tasks(self) -> list[dict[str, Any]]:
        groups = await asyncio.gather(
            *(
                self.tasks_for_project(project_id)
                for project_id in self.workspace_resolver.scopes()
            )
        )
        return [task for group in groups for task in group]

    async def list_occurrences(
        self, workspace: str, start: str, end: str
    ) -> dict[str, Any]:
        resolved = self.workspace_resolver.resolve(workspace)
        schedule_result, entities = await asyncio.gather(
            self.gateway.call(
                "schedule.occurrences",
                {"start": str(start or ""), "end": str(end or "")},
                project_id=resolved,
            ),
            self.entities.list(
                has_due_date=True,
                project_id=resolved,
                limit=500,
            ),
        )
        start_at, end_at = occurrence_window(
            str(schedule_result.get("start") or ""),
            str(schedule_result.get("end") or ""),
        )
        raw_events = schedule_result.get("events")
        events = (
            [dict(item) for item in raw_events if isinstance(item, dict)]
            if isinstance(raw_events, list)
            else []
        )
        events.extend(entity_events(entities, start_at, end_at))
        events.sort(key=lambda event: str(event.get("start") or ""))
        return {
            "events": events,
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "workspace": resolved,
        }

    async def create(self, command: CreateScheduleCommand) -> dict[str, Any]:
        resolved = self.workspace_resolver.resolve(command.workspace)
        arguments = {
            key: value
            for key, value in command.values.items()
            if key
            in {
                "prompt",
                "schedule_type",
                "schedule_value",
                "schedule_timezone",
                "action_type",
            }
            and value is not None
        }
        arguments["permission_mode"] = "workspace_only"
        created = await self.gateway.call(
            "schedule.create",
            arguments,
            project_id=resolved,
        )
        task_id = str(created.get("id") or "")
        self.notify(
            title="日程提醒已创建",
            body=f"已添加提醒：{str(arguments.get('prompt') or '')[:80]}",
            tab="system",
            project_ref=resolved,
            source="schedule_created",
            source_label="日程",
            link_label="日程",
            meta={"taskId": task_id},
        )
        return {
            "ok": True,
            "id": task_id,
            "tasks": await self.tasks_for_project(resolved),
            "workspace": resolved,
        }

    async def update(self, command: UpdateScheduleCommand) -> dict[str, Any]:
        resolved = self.workspace_resolver.resolve(command.workspace)
        arguments = {
            key: value
            for key, value in command.values.items()
            if key
            in {
                "prompt",
                "schedule_type",
                "schedule_value",
                "schedule_timezone",
                "action_type",
                "status",
            }
            and value is not None
        }
        arguments["task_id"] = command.task_id
        await self.gateway.call(
            "schedule.edit", arguments, project_id=resolved
        )
        self.notify(
            title="日程提醒已更新",
            body=(
                "提醒任务已更新："
                + str(arguments.get("prompt") or command.task_id)[:80]
            ),
            tab="system",
            project_ref=resolved,
            source="schedule_updated",
            source_label="日程",
            link_label="日程",
            meta={"taskId": command.task_id},
        )
        return {
            "ok": True,
            "tasks": await self.tasks_for_project(resolved),
            "workspace": resolved,
        }

    async def delete(self, task_id: str, workspace: str) -> dict[str, Any]:
        resolved = self.workspace_resolver.resolve(workspace)
        await self.gateway.call(
            "schedule.cancel", {"task_id": task_id}, project_id=resolved
        )
        return {
            "ok": True,
            "tasks": await self.tasks_for_project(resolved),
            "workspace": resolved,
        }

    async def list_runs(
        self, task_id: str, workspace: str, limit: int
    ) -> dict[str, Any]:
        resolved = self.workspace_resolver.resolve(workspace)
        result = await self.gateway.call(
            "schedule.runs",
            {"task_id": task_id, "limit": max(1, min(int(limit), 100))},
            project_id=resolved,
        )
        return {"runs": result.get("runs") or []}


__all__ = [
    "CreateScheduleCommand",
    "ScheduleApplicationError",
    "ScheduleApplicationService",
    "SchedulePluginGateway",
    "UpdateScheduleCommand",
]
