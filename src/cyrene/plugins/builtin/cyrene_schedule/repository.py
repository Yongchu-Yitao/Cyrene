"""Schedule Plugin-owned storage with lease-based execution claims."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    instant = value or _utc_now()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).isoformat()


def _run_id(task_id: str, scheduled_for: str) -> str:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"cyrene:schedule:{task_id}:{scheduled_for}",
    ).hex


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    id: str
    chat_id: int
    origin_session_id: str
    project_id: str
    prompt: str
    action_type: str
    schedule_type: str
    schedule_value: str
    schedule_timezone: str
    next_run: str | None
    last_run: str | None
    last_result: str | None
    status: str
    created_at: str
    updated_at: str
    permission_mode: str
    definition_revision: int
    schedule_revision: int
    lease_token: str | None
    lease_until: str | None
    current_run_id: str | None
    scheduled_for: str | None
    last_error: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "ScheduledTask":
        values = dict(row)
        return cls(
            id=str(values["id"]),
            chat_id=int(
                values.get("chat_id")
                if values.get("chat_id") is not None
                else -1
            ),
            origin_session_id=str(values.get("origin_session_id") or ""),
            project_id=str(values.get("project_id") or "default"),
            prompt=str(values.get("prompt") or ""),
            action_type=str(values.get("action_type") or "agent_task"),
            schedule_type=str(values.get("schedule_type") or ""),
            schedule_value=str(values.get("schedule_value") or ""),
            schedule_timezone=str(values.get("schedule_timezone") or "UTC"),
            next_run=values.get("next_run"),
            last_run=values.get("last_run"),
            last_result=values.get("last_result"),
            status=str(values.get("status") or "active"),
            created_at=str(values.get("created_at") or ""),
            updated_at=str(values.get("updated_at") or values.get("created_at") or ""),
            permission_mode=str(values.get("permission_mode") or "workspace_only"),
            definition_revision=int(values.get("definition_revision") or 1),
            schedule_revision=int(values.get("schedule_revision") or 1),
            lease_token=values.get("lease_token"),
            lease_until=values.get("lease_until"),
            current_run_id=values.get("current_run_id"),
            scheduled_for=values.get("scheduled_for"),
            last_error=values.get("last_error"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["run_state"] = "running" if self.lease_token else "idle"
        return payload


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    task: ScheduledTask
    lease_token: str
    lease_until: str
    run_id: str
    scheduled_for: str
    claimed_at: str


@dataclass(frozen=True, slots=True)
class TaskTimeTotals:
    total_ms: int
    longest_ms: int
    runs: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class ScheduleRepository:
    """The sole SQLite boundary for schedules and their run history."""

    _EDITABLE_FIELDS = frozenset(
        {
            "prompt",
            "action_type",
            "schedule_type",
            "schedule_value",
            "schedule_timezone",
            "next_run",
            "permission_mode",
            "status",
        }
    )
    _SCHEDULE_FIELDS = frozenset(
        {"schedule_type", "schedule_value", "schedule_timezone", "next_run", "status"}
    )

    def __init__(self, db_path: str):
        self.db_path = str(db_path)

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
        task_id = uuid.uuid4().hex[:12]
        now = _iso()
        async with aiosqlite.connect(self.db_path) as database:
            await database.execute(
                "INSERT INTO scheduled_tasks "
                "(id, chat_id, origin_session_id, project_id, prompt, action_type, "
                "schedule_type, schedule_value, schedule_timezone, next_run, status, "
                "created_at, updated_at, permission_mode, definition_revision, "
                "schedule_revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', "
                "?, ?, ?, 1, 1)",
                (
                    task_id,
                    int(chat_id),
                    str(origin_session_id or "").strip(),
                    str(project_id or "default"),
                    str(prompt),
                    str(action_type),
                    str(schedule_type),
                    str(schedule_value),
                    str(schedule_timezone or "UTC"),
                    str(next_run),
                    now,
                    now,
                    str(permission_mode),
                ),
            )
            await database.commit()
        return task_id

    async def list(self, project_id: str | None = None) -> list[ScheduledTask]:
        async with aiosqlite.connect(self.db_path) as database:
            database.row_factory = aiosqlite.Row
            if project_id is None:
                cursor = await database.execute(
                    "SELECT * FROM scheduled_tasks ORDER BY created_at DESC, id DESC"
                )
            else:
                cursor = await database.execute(
                    "SELECT * FROM scheduled_tasks "
                    "WHERE COALESCE(project_id, 'default') = ? "
                    "ORDER BY created_at DESC, id DESC",
                    (str(project_id or "default"),),
                )
            return [ScheduledTask.from_row(row) for row in await cursor.fetchall()]

    async def get(
        self,
        task_id: str,
        project_id: str | None = None,
    ) -> ScheduledTask | None:
        query = "SELECT * FROM scheduled_tasks WHERE id = ?"
        values: tuple[Any, ...] = (str(task_id),)
        if project_id is not None:
            query += " AND COALESCE(project_id, 'default') = ?"
            values += (str(project_id or "default"),)
        async with aiosqlite.connect(self.db_path) as database:
            database.row_factory = aiosqlite.Row
            cursor = await database.execute(query, values)
            row = await cursor.fetchone()
        return ScheduledTask.from_row(row) if row is not None else None

    async def edit(
        self,
        task_id: str,
        updates: dict[str, Any],
        project_id: str | None = None,
    ) -> bool:
        fields = [field for field in updates if field in self._EDITABLE_FIELDS]
        if not fields:
            return False
        assignments = [f"{field} = ?" for field in fields]
        values: list[Any] = [updates[field] for field in fields]
        assignments.extend(
            [
                "updated_at = ?",
                "definition_revision = definition_revision + 1",
            ]
        )
        values.append(_iso())
        if any(field in self._SCHEDULE_FIELDS for field in fields):
            assignments.append("schedule_revision = schedule_revision + 1")
        query = f"UPDATE scheduled_tasks SET {', '.join(assignments)} WHERE id = ?"
        values.append(str(task_id))
        if project_id is not None:
            query += " AND COALESCE(project_id, 'default') = ?"
            values.append(str(project_id or "default"))
        async with aiosqlite.connect(self.db_path) as database:
            cursor = await database.execute(query, values)
            await database.commit()
            return cursor.rowcount > 0

    async def update_status(
        self,
        task_id: str,
        status: str,
        *,
        project_id: str | None = None,
        next_run: str | None | object = ...,
    ) -> bool:
        updates: dict[str, Any] = {"status": status}
        if next_run is not ...:
            updates["next_run"] = next_run
        return await self.edit(task_id, updates, project_id)

    async def delete(self, task_id: str, project_id: str | None = None) -> bool:
        query = "SELECT id FROM scheduled_tasks WHERE id = ?"
        values: list[Any] = [str(task_id)]
        if project_id is not None:
            query += " AND COALESCE(project_id, 'default') = ?"
            values.append(str(project_id or "default"))
        async with aiosqlite.connect(self.db_path) as database:
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute(query, values)
            exists = await cursor.fetchone() is not None
            if exists:
                await database.execute(
                    "DELETE FROM task_run_logs WHERE task_id = ?", (str(task_id),)
                )
                await database.execute(
                    "DELETE FROM scheduled_tasks WHERE id = ?", (str(task_id),)
                )
            await database.commit()
            return exists

    async def delete_project(self, project_id: str) -> int:
        async with aiosqlite.connect(self.db_path) as database:
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute(
                "SELECT id FROM scheduled_tasks "
                "WHERE COALESCE(project_id, 'default') = ?",
                (str(project_id or "default"),),
            )
            task_ids = [str(row[0]) for row in await cursor.fetchall()]
            if task_ids:
                placeholders = ",".join("?" for _ in task_ids)
                await database.execute(
                    f"DELETE FROM task_run_logs WHERE task_id IN ({placeholders})",
                    task_ids,
                )
                await database.execute(
                    f"DELETE FROM scheduled_tasks WHERE id IN ({placeholders})",
                    task_ids,
                )
            await database.commit()
        return len(task_ids)

    async def claim_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        lease_seconds: int = 120,
    ) -> list[ClaimedTask]:
        instant = now or _utc_now()
        now_iso = _iso(instant)
        lease_until = _iso(instant + timedelta(seconds=max(15, int(lease_seconds))))
        claims: list[ClaimedTask] = []
        async with aiosqlite.connect(self.db_path) as database:
            database.row_factory = aiosqlite.Row
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute(
                "SELECT * FROM scheduled_tasks "
                "WHERE status = 'active' AND next_run IS NOT NULL AND next_run <= ? "
                "AND (lease_until IS NULL OR lease_until = '' OR lease_until <= ?) "
                "ORDER BY next_run, created_at LIMIT ?",
                (now_iso, now_iso, max(1, min(int(limit), 100))),
            )
            rows = await cursor.fetchall()
            for row in rows:
                task = ScheduledTask.from_row(row)
                scheduled_for = str(task.next_run or now_iso)
                run_id = _run_id(task.id, scheduled_for)
                token = uuid.uuid4().hex
                updated = await database.execute(
                    "UPDATE scheduled_tasks SET lease_token = ?, lease_until = ?, "
                    "current_run_id = ?, scheduled_for = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'active' AND next_run = ? "
                    "AND (lease_until IS NULL OR lease_until = '' OR lease_until <= ?)",
                    (
                        token,
                        lease_until,
                        run_id,
                        scheduled_for,
                        now_iso,
                        task.id,
                        task.next_run,
                        now_iso,
                    ),
                )
                if updated.rowcount <= 0:
                    continue
                await database.execute(
                    "INSERT OR IGNORE INTO task_run_logs "
                    "(task_id, run_at, duration_ms, status, result, error, run_id, "
                    "scheduled_for, started_at, completed_at) "
                    "VALUES (?, ?, 0, 'running', NULL, NULL, ?, ?, ?, NULL)",
                    (task.id, now_iso, run_id, scheduled_for, now_iso),
                )
                await database.execute(
                    "UPDATE task_run_logs SET status = 'running', error = NULL, "
                    "completed_at = NULL WHERE task_id = ? AND run_id = ?",
                    (task.id, run_id),
                )
                claimed_task = replace(
                    task,
                    lease_token=token,
                    lease_until=lease_until,
                    current_run_id=run_id,
                    scheduled_for=scheduled_for,
                    updated_at=now_iso,
                )
                claims.append(
                    ClaimedTask(
                        task=claimed_task,
                        lease_token=token,
                        lease_until=lease_until,
                        run_id=run_id,
                        scheduled_for=scheduled_for,
                        claimed_at=now_iso,
                    )
                )
            await database.commit()
        return claims

    async def renew_lease(
        self,
        claim: ClaimedTask,
        *,
        lease_seconds: int = 120,
    ) -> bool:
        until = _iso(_utc_now() + timedelta(seconds=max(15, int(lease_seconds))))
        async with aiosqlite.connect(self.db_path) as database:
            cursor = await database.execute(
                "UPDATE scheduled_tasks SET lease_until = ? "
                "WHERE id = ? AND lease_token = ? AND current_run_id = ? "
                "AND status = 'active'",
                (until, claim.task.id, claim.lease_token, claim.run_id),
            )
            await database.commit()
            return cursor.rowcount > 0

    async def finalize_claim(
        self,
        claim: ClaimedTask,
        *,
        run_status: str,
        result: str | None,
        error: str | None,
        duration_ms: int,
        next_run: str | None,
        task_status: str,
    ) -> bool:
        completed = _iso()
        async with aiosqlite.connect(self.db_path) as database:
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute(
                "UPDATE scheduled_tasks SET last_run = ?, last_result = ?, "
                "last_error = ?, next_run = CASE WHEN schedule_revision = ? THEN ? "
                "ELSE next_run END, status = CASE WHEN schedule_revision = ? THEN ? "
                "ELSE status END, lease_token = NULL, lease_until = NULL, "
                "current_run_id = NULL, scheduled_for = NULL, updated_at = ? "
                "WHERE id = ? AND lease_token = ? AND current_run_id = ?",
                (
                    completed,
                    result,
                    error,
                    claim.task.schedule_revision,
                    next_run,
                    claim.task.schedule_revision,
                    task_status,
                    completed,
                    claim.task.id,
                    claim.lease_token,
                    claim.run_id,
                ),
            )
            if cursor.rowcount <= 0:
                await database.rollback()
                return False
            await database.execute(
                "UPDATE task_run_logs SET duration_ms = ?, status = ?, result = ?, "
                "error = ?, completed_at = ? WHERE task_id = ? AND run_id = ?",
                (
                    max(0, int(duration_ms)),
                    str(run_status),
                    result,
                    error,
                    completed,
                    claim.task.id,
                    claim.run_id,
                ),
            )
            await database.commit()
        return True

    async def release_claim(
        self,
        claim: ClaimedTask,
        *,
        reason: str = "execution interrupted",
    ) -> bool:
        completed = _iso()
        async with aiosqlite.connect(self.db_path) as database:
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute(
                "UPDATE scheduled_tasks SET lease_token = NULL, lease_until = NULL, "
                "current_run_id = NULL, scheduled_for = NULL, updated_at = ? "
                "WHERE id = ? AND lease_token = ? AND current_run_id = ?",
                (completed, claim.task.id, claim.lease_token, claim.run_id),
            )
            if cursor.rowcount > 0:
                await database.execute(
                    "UPDATE task_run_logs SET status = 'interrupted', error = ?, "
                    "completed_at = ? WHERE task_id = ? AND run_id = ?",
                    (reason, completed, claim.task.id, claim.run_id),
                )
            await database.commit()
            return cursor.rowcount > 0

    async def list_runs(
        self,
        task_id: str,
        *,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT l.id, l.task_id, l.run_at, l.duration_ms, l.status, l.result, "
            "l.error, l.run_id, l.scheduled_for, l.started_at, l.completed_at "
            "FROM task_run_logs l JOIN scheduled_tasks t ON t.id = l.task_id "
            "WHERE l.task_id = ?"
        )
        values: list[Any] = [str(task_id)]
        if project_id is not None:
            query += " AND COALESCE(t.project_id, 'default') = ?"
            values.append(str(project_id or "default"))
        query += " ORDER BY l.id DESC LIMIT ?"
        values.append(max(1, min(int(limit), 100)))
        async with aiosqlite.connect(self.db_path) as database:
            database.row_factory = aiosqlite.Row
            cursor = await database.execute(query, values)
            return [dict(row) for row in await cursor.fetchall()]

    async def log_run(
        self,
        task_id: str,
        duration_ms: int,
        status: str,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        now = _iso()
        async with aiosqlite.connect(self.db_path) as database:
            await database.execute(
                "INSERT INTO task_run_logs "
                "(task_id, run_at, duration_ms, status, result, error, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, now, max(0, int(duration_ms)), status, result, error, now),
            )
            await database.commit()

    async def time_totals(self) -> TaskTimeTotals:
        async with aiosqlite.connect(self.db_path) as database:
            cursor = await database.execute(
                "SELECT COALESCE(SUM(duration_ms), 0), "
                "COALESCE(MAX(duration_ms), 0), COUNT(*) FROM task_run_logs"
            )
            task_total, task_longest, task_runs = await cursor.fetchone()
        return TaskTimeTotals(
            total_ms=int(task_total or 0),
            longest_ms=int(task_longest or 0),
            runs=int(task_runs or 0),
        )


__all__ = ["ClaimedTask", "ScheduledTask", "ScheduleRepository", "TaskTimeTotals"]
