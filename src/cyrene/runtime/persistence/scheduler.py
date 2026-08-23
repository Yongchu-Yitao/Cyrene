"""Scheduled-task persistence and typed row models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
import uuid

import aiosqlite


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """Stable representation of one ``scheduled_tasks`` row.

    ``to_legacy_dict`` deliberately preserves the dictionary payload consumed
    by existing scheduler and HTTP code while giving new application code a
    concrete model to depend on.
    """

    id: str
    chat_id: int
    origin_session_id: str | None
    project_id: str | None
    prompt: str
    action_type: str | None
    schedule_type: str
    schedule_value: str
    schedule_timezone: str | None
    next_run: str | None
    last_run: str | None
    last_result: str | None
    status: str | None
    created_at: str
    permission_mode: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> ScheduledTask:
        values = dict(row)
        return cls(
            id=str(values["id"]),
            chat_id=int(values["chat_id"]),
            origin_session_id=values.get("origin_session_id"),
            project_id=values.get("project_id"),
            prompt=str(values["prompt"]),
            action_type=values.get("action_type"),
            schedule_type=str(values["schedule_type"]),
            schedule_value=str(values["schedule_value"]),
            schedule_timezone=values.get("schedule_timezone"),
            next_run=values.get("next_run"),
            last_run=values.get("last_run"),
            last_result=values.get("last_result"),
            status=values.get("status"),
            created_at=str(values["created_at"]),
            permission_mode=values.get("permission_mode"),
        )

    def to_legacy_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TaskTimeTotals:
    total_ms: int
    longest_ms: int
    runs: int

    def to_legacy_dict(self) -> dict[str, int]:
        return asdict(self)


class SchedulerRepository:
    """SQLite repository for scheduled tasks and their run history."""

    _EDITABLE_FIELDS = (
        "prompt",
        "action_type",
        "schedule_type",
        "schedule_value",
        "schedule_timezone",
        "next_run",
        "permission_mode",
    )

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def create(
        self,
        *,
        chat_id: int,
        prompt: str,
        schedule_type: str,
        schedule_value: str,
        next_run: str,
        permission_mode: str = "workspace_only",
        project_id: str = "default",
        schedule_timezone: str = "UTC",
        origin_session_id: str = "",
        action_type: str = "agent_task",
    ) -> str:
        task_id = uuid.uuid4().hex[:8]
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO scheduled_tasks "
                "(id, chat_id, origin_session_id, project_id, prompt, action_type, "
                "schedule_type, schedule_value, schedule_timezone, next_run, "
                "created_at, permission_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    chat_id,
                    str(origin_session_id or "").strip(),
                    project_id or "default",
                    prompt,
                    action_type if action_type in {"message", "agent_task"} else "agent_task",
                    schedule_type,
                    schedule_value,
                    schedule_timezone or "UTC",
                    next_run,
                    datetime.now(timezone.utc).isoformat(),
                    permission_mode,
                ),
            )
            await db.commit()
        return task_id

    async def list(self, project_id: str | None = None) -> list[ScheduledTask]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if project_id is None:
                cursor = await db.execute("SELECT * FROM scheduled_tasks")
            else:
                cursor = await db.execute(
                    "SELECT * FROM scheduled_tasks "
                    "WHERE COALESCE(project_id, 'default') = ?",
                    (project_id or "default",),
                )
            return [ScheduledTask.from_row(row) for row in await cursor.fetchall()]

    async def get(self, task_id: str) -> ScheduledTask | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
        return ScheduledTask.from_row(row) if row is not None else None

    async def edit(self, task_id: str, updates: dict[str, Any]) -> bool:
        fields = [field for field in self._EDITABLE_FIELDS if field in updates]
        if not fields:
            return False
        assignments = ", ".join(f"{field} = ?" for field in fields)
        values = [updates[field] for field in fields]
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"UPDATE scheduled_tasks SET {assignments} WHERE id = ?",
                (*values, task_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_due(self, now: datetime | None = None) -> list[ScheduledTask]:
        due_before = (now or datetime.now(timezone.utc)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM scheduled_tasks "
                "WHERE status = 'active' AND next_run <= ?",
                (due_before,),
            )
            return [ScheduledTask.from_row(row) for row in await cursor.fetchall()]

    async def update_status(self, task_id: str, status: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE scheduled_tasks SET status = ? WHERE id = ?",
                (status, task_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete(self, task_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM scheduled_tasks WHERE id = ?",
                (task_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_project(self, project_id: str) -> int:
        """Delete every scheduled task owned by one project data key."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM scheduled_tasks "
                "WHERE COALESCE(project_id, 'default') = ?",
                (project_id,),
            )
            await db.commit()
            return max(0, int(cursor.rowcount or 0))

    async def update_after_run(
        self,
        task_id: str,
        last_result: str,
        next_run: str | None,
        status: str = "active",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE scheduled_tasks SET last_run = ?, last_result = ?, "
                "next_run = ?, status = ? WHERE id = ?",
                (now, last_result, next_run, status, task_id),
            )
            await db.commit()

    async def log_run(
        self,
        task_id: str,
        duration_ms: int,
        status: str,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO task_run_logs "
                "(task_id, run_at, duration_ms, status, result, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    datetime.now(timezone.utc).isoformat(),
                    duration_ms,
                    status,
                    result,
                    error,
                ),
            )
            await db.commit()

    async def time_totals(self) -> TaskTimeTotals:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COALESCE(SUM(duration_ms), 0), "
                "COALESCE(MAX(duration_ms), 0), COUNT(*) FROM task_run_logs"
            )
            task_total, task_longest, task_runs = await cursor.fetchone()
            cursor = await db.execute(
                "SELECT COALESCE(SUM(active_seconds), 0), "
                "COALESCE(MAX(active_seconds), 0), COUNT(*) FROM goal_runs"
            )
            goal_total_s, goal_longest_s, goal_runs = await cursor.fetchone()
        goal_total_ms = int(round(float(goal_total_s or 0) * 1000))
        goal_longest_ms = int(round(float(goal_longest_s or 0) * 1000))
        return TaskTimeTotals(
            total_ms=int(task_total or 0) + goal_total_ms,
            longest_ms=max(int(task_longest or 0), goal_longest_ms),
            runs=int(task_runs or 0) + int(goal_runs or 0),
        )
