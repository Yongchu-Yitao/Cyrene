"""Persistence ports and SQLite repository for durable goal-loop state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Protocol

import aiosqlite

SQLITE_TIMEOUT_SECONDS = 15
_SCHEMA_READY: set[str] = set()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    return utc_now().isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def json_loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


async def ensure_schema(db_path: str) -> None:
    if db_path in _SCHEMA_READY:
        return
    async with aiosqlite.connect(db_path, timeout=SQLITE_TIMEOUT_SECONDS) as db:
        await db.execute(f"PRAGMA busy_timeout = {SQLITE_TIMEOUT_SECONDS * 1000}")
        await db.execute("PRAGMA journal_mode = WAL")
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS goal_loop_drafts (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, project_id TEXT NOT NULL,
                base_plan_revision INTEGER NOT NULL, goal TEXT NOT NULL,
                goal_changed INTEGER NOT NULL DEFAULT 0, plan_json TEXT NOT NULL,
                acceptance_json TEXT NOT NULL, limits_json TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_goal_loop_drafts_session ON goal_loop_drafts(session_id);
            CREATE INDEX IF NOT EXISTS idx_goal_loop_drafts_expires ON goal_loop_drafts(expires_at);
            CREATE TABLE IF NOT EXISTS goal_runs (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL, objective TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running', phase TEXT NOT NULL DEFAULT 'executing',
                plan_definition_revision INTEGER NOT NULL, current_step_id TEXT,
                permission_mode TEXT NOT NULL DEFAULT 'auto',
                reflection_mode TEXT NOT NULL DEFAULT 'proactive',
                max_active_seconds INTEGER NOT NULL, max_repair_rounds INTEGER NOT NULL,
                active_seconds REAL NOT NULL DEFAULT 0, active_started_at TEXT,
                repair_round INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, lease_until TEXT,
                stop_reason TEXT, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_goal_runs_status ON goal_runs(status);
            CREATE INDEX IF NOT EXISTS idx_goal_runs_lease ON goal_runs(lease_until);
            CREATE TABLE IF NOT EXISTS goal_run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                event_type TEXT NOT NULL, step_id TEXT, payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_goal_run_events_run ON goal_run_events(run_id);
            """
        )
        await db.commit()
    _SCHEMA_READY.add(db_path)


async def fetch_one(db_path: str, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    await ensure_schema(db_path)
    async with aiosqlite.connect(db_path, timeout=SQLITE_TIMEOUT_SECONDS) as db:
        await db.execute(f"PRAGMA busy_timeout = {SQLITE_TIMEOUT_SECONDS * 1000}")
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(sql, args)).fetchone()
        return dict(row) if row is not None else None


async def fetch_all(db_path: str, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    await ensure_schema(db_path)
    async with aiosqlite.connect(db_path, timeout=SQLITE_TIMEOUT_SECONDS) as db:
        await db.execute(f"PRAGMA busy_timeout = {SQLITE_TIMEOUT_SECONDS * 1000}")
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(sql, args)).fetchall()
        return [dict(row) for row in rows]


async def execute(db_path: str, sql: str, args: tuple[Any, ...] = ()) -> int:
    await ensure_schema(db_path)
    async with aiosqlite.connect(db_path, timeout=SQLITE_TIMEOUT_SECONDS) as db:
        await db.execute(f"PRAGMA busy_timeout = {SQLITE_TIMEOUT_SECONDS * 1000}")
        cursor = await db.execute(sql, args)
        await db.commit()
        return int(cursor.rowcount or 0)


class GoalLoopRepositoryPort(Protocol):
    async def get_draft(self, draft_id: str, session_id: str) -> dict[str, Any] | None: ...
    async def save_draft(self, draft: dict[str, Any]) -> None: ...
    async def delete_draft(self, draft_id: str) -> None: ...
    async def delete_expired_drafts(self, expires_before: str) -> None: ...
    async def get_run_by_id(self, run_id: str) -> dict[str, Any] | None: ...
    async def get_run_by_session(self, session_id: str) -> dict[str, Any] | None: ...
    async def delete_run(self, run_id: str) -> None: ...
    async def update_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None: ...
    async def set_inactive(
        self, run: dict[str, Any], status: str, *, phase: str | None = None,
        stop_reason: str = "", last_error: str = "",
    ) -> dict[str, Any] | None: ...
    async def add_event(self, run_id: str, event_type: str, *, step_id: str = "", payload: dict[str, Any] | None = None) -> None: ...
    async def list_events(self, run_id: str) -> list[dict[str, Any]]: ...
    def is_busy(self, exc: BaseException) -> bool: ...


class GoalLoopTransactionPort(Protocol):
    """Cross-store reservation boundary used while projecting a new run."""

    async def reserve_run(self, run: dict[str, Any]) -> None: ...
    async def rollback_run(self, run_id: str) -> None: ...


class SqliteGoalLoopRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)

    async def get_draft(self, draft_id: str, session_id: str) -> dict[str, Any] | None:
        return await fetch_one(self.db_path, "SELECT * FROM goal_loop_drafts WHERE id = ? AND session_id = ?", (draft_id, session_id))

    async def save_draft(self, draft: dict[str, Any]) -> None:
        await execute(self.db_path, """INSERT INTO goal_loop_drafts
            (id, session_id, project_id, base_plan_revision, goal, goal_changed,
             plan_json, acceptance_json, limits_json, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", tuple(draft[key] for key in (
                "id", "session_id", "project_id", "base_plan_revision", "goal", "goal_changed",
                "plan_json", "acceptance_json", "limits_json", "created_at", "expires_at",
            )))

    async def delete_draft(self, draft_id: str) -> None:
        await execute(self.db_path, "DELETE FROM goal_loop_drafts WHERE id = ?", (draft_id,))

    async def delete_expired_drafts(self, expires_before: str) -> None:
        await execute(self.db_path, "DELETE FROM goal_loop_drafts WHERE expires_at < ?", (expires_before,))

    async def get_run_by_id(self, run_id: str) -> dict[str, Any] | None:
        return await fetch_one(self.db_path, "SELECT * FROM goal_runs WHERE id = ?", (run_id,))

    async def get_run_by_session(self, session_id: str) -> dict[str, Any] | None:
        return await fetch_one(self.db_path, "SELECT * FROM goal_runs WHERE session_id = ?", (session_id,))

    async def reserve_run(self, run: dict[str, Any]) -> None:
        await execute(self.db_path, """INSERT INTO goal_runs
            (id, session_id, project_id, objective, status, phase, plan_definition_revision,
             current_step_id, permission_mode, reflection_mode, max_active_seconds,
             max_repair_rounds, active_seconds, active_started_at, repair_round, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'running', 'executing', ?, NULL, ?, ?, ?, ?, 0, ?, 0, ?, ?)""",
            tuple(run[key] for key in ("id", "session_id", "project_id", "objective",
                "plan_definition_revision", "permission_mode", "reflection_mode",
                "max_active_seconds", "max_repair_rounds", "active_started_at", "created_at", "updated_at")))

    async def rollback_run(self, run_id: str) -> None:
        await self.delete_run(run_id)

    async def delete_run(self, run_id: str) -> None:
        await execute(self.db_path, "DELETE FROM goal_runs WHERE id = ?", (run_id,))

    async def update_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return await self.get_run_by_id(run_id)
        fields["updated_at"] = utc_iso()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        await execute(self.db_path, f"UPDATE goal_runs SET {assignments} WHERE id = ?", (*fields.values(), run_id))
        return await self.get_run_by_id(run_id)

    async def set_inactive(
        self, run: dict[str, Any], status: str, *, phase: str | None = None,
        stop_reason: str = "", last_error: str = "",
    ) -> dict[str, Any] | None:
        active_seconds = float(run.get("active_seconds") or 0)
        started = str(run.get("active_started_at") or "").strip()
        if started:
            try:
                active_seconds += max(0.0, (utc_now() - datetime.fromisoformat(started)).total_seconds())
            except ValueError:
                pass
        fields: dict[str, Any] = {
            "status": status, "active_seconds": active_seconds, "active_started_at": None,
            "lease_owner": None, "lease_until": None, "stop_reason": stop_reason or None,
            "last_error": last_error or None,
        }
        if phase is not None:
            fields["phase"] = phase
        return await self.update_run(str(run["id"]), **fields)

    async def add_event(self, run_id: str, event_type: str, *, step_id: str = "", payload: dict[str, Any] | None = None) -> None:
        await execute(self.db_path, "INSERT INTO goal_run_events (run_id, event_type, step_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, event_type, step_id or None, json_dumps(payload or {}), utc_iso()))

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        return await fetch_all(self.db_path, "SELECT * FROM goal_run_events WHERE run_id = ? ORDER BY id DESC LIMIT 100", (run_id,))

    @staticmethod
    def is_busy(exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.OperationalError) and any(marker in str(exc).lower() for marker in (
            "database is locked", "database table is locked", "database is busy"))


__all__ = ["GoalLoopRepositoryPort", "GoalLoopTransactionPort", "SqliteGoalLoopRepository"]
