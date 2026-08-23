"""Project resolution and persistence adapters for Workbench schedules."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol

import aiosqlite

from cyrene.runtime.persistence.scheduler import SchedulerRepository
from cyrene.tool_impl.entity.store import list_entities


def safe_workspace_id(workspace_id: str | None) -> str:
    raw = str(workspace_id or "").strip()
    if not raw:
        return "default"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._") or "default"


class WorkspaceProjectResolver:
    """Resolve canonical project ids and legacy data keys to schedule scope keys."""

    def __init__(
        self,
        *,
        find_project_lightweight: Callable[[str], dict[str, Any] | None],
        read_projects: Callable[[], list[dict[str, Any]]],
    ) -> None:
        self._find_project_lightweight = find_project_lightweight
        self._read_projects = read_projects

    def resolve(self, workspace_id: str | None) -> str:
        raw = str(workspace_id or "").strip()
        project = self._find_project_lightweight(raw)
        if project:
            return self._project_key(project)
        requested_key = safe_workspace_id(raw)
        for candidate in self._read_projects():
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("id") or "").strip() == raw or self._project_key(candidate) == requested_key:
                return self._project_key(candidate)
        return requested_key

    @staticmethod
    def _project_key(project: dict[str, Any]) -> str:
        return safe_workspace_id(project.get("dataKey") or project.get("id"))


class ScheduleRepositoryPort(Protocol):
    async def list_tasks(self, workspace_id: str) -> list[dict[str, Any]]: ...
    async def list_deadline_entities(self, workspace_id: str) -> list[dict[str, Any]]: ...
    async def create(self, values: dict[str, Any]) -> str: ...
    async def update(self, task_id: str, workspace_id: str, values: dict[str, Any]) -> bool: ...
    async def delete(self, task_id: str, workspace_id: str) -> bool: ...
    async def list_runs(self, task_id: str, workspace_id: str, limit: int) -> list[dict[str, Any]]: ...


class ScheduleRepository:
    """All SQL and persistence-facade calls used by the schedule application."""

    EDITABLE_FIELDS = (
        "prompt", "action_type", "schedule_type", "schedule_value",
        "schedule_timezone", "next_run", "status",
    )

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self._tasks = SchedulerRepository(self.db_path)

    async def list_tasks(self, workspace_id: str) -> list[dict[str, Any]]:
        return [task.to_legacy_dict() for task in await self._tasks.list(workspace_id)]

    async def list_deadline_entities(self, workspace_id: str) -> list[dict[str, Any]]:
        return await list_entities(
            self.db_path,
            has_due_date=True,
            project_id=workspace_id,
            limit=500,
        )

    async def create(self, values: dict[str, Any]) -> str:
        return await self._tasks.create(**values)

    async def update(self, task_id: str, workspace_id: str, values: dict[str, Any]) -> bool:
        fields = [field for field in self.EDITABLE_FIELDS if field in values]
        assignments = ", ".join(f"{field} = ?" for field in fields)
        async with aiosqlite.connect(self.db_path) as database:
            cursor = await database.execute(
                f"UPDATE scheduled_tasks SET {assignments} "
                "WHERE id = ? AND COALESCE(project_id, 'default') = ?",
                (*(values[field] for field in fields), task_id, workspace_id),
            )
            await database.commit()
        return cursor.rowcount > 0

    async def delete(self, task_id: str, workspace_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as database:
            cursor = await database.execute(
                "DELETE FROM scheduled_tasks WHERE id = ? AND COALESCE(project_id, 'default') = ?",
                (task_id, workspace_id),
            )
            await database.commit()
        return cursor.rowcount > 0

    async def list_runs(self, task_id: str, workspace_id: str, limit: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as database:
            database.row_factory = aiosqlite.Row
            cursor = await database.execute(
                "SELECT l.id, l.task_id, l.run_at, l.duration_ms, l.status, l.result, l.error "
                "FROM task_run_logs l JOIN scheduled_tasks t ON t.id = l.task_id "
                "WHERE l.task_id = ? AND COALESCE(t.project_id, 'default') = ? "
                "ORDER BY l.run_at DESC LIMIT ?",
                (task_id, workspace_id, limit),
            )
            return [dict(row) for row in await cursor.fetchall()]


__all__ = ["ScheduleRepository", "ScheduleRepositoryPort", "WorkspaceProjectResolver", "safe_workspace_id"]
