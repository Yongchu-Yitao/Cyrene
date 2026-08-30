"""SQLite persistence for one durable Goal lifecycle per conversation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationGoalRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self._ready = False

    async def ensure_schema(self) -> None:
        if self._ready:
            return
        async with aiosqlite.connect(self.db_path, timeout=15) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_goals (
                    chat_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_goals_status
                    ON conversation_goals(status, updated_at);
                CREATE TABLE IF NOT EXISTS conversation_goal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_goal_events_chat
                    ON conversation_goal_events(chat_id, id DESC);
                """
            )
            await db.commit()
        self._ready = True

    async def get(self, chat_id: str) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path, timeout=15) as db:
            row = await (
                await db.execute(
                    "SELECT payload_json FROM conversation_goals WHERE chat_id = ?",
                    (str(chat_id),),
                )
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    async def save(self, goal: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_schema()
        value = dict(goal)
        now = utc_iso()
        value.setdefault("createdAt", now)
        value["updatedAt"] = now
        async with aiosqlite.connect(self.db_path, timeout=15) as db:
            await db.execute(
                """
                INSERT INTO conversation_goals(
                    chat_id, goal_id, revision, status, payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    goal_id = excluded.goal_id,
                    revision = excluded.revision,
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    str(value.get("chatId") or ""),
                    str(value.get("id") or ""),
                    int(value.get("revision") or 1),
                    str(value.get("status") or "negotiating"),
                    json.dumps(value, ensure_ascii=False, default=str),
                    str(value["createdAt"]),
                    now,
                ),
            )
            await db.commit()
        return value

    async def delete(self, chat_id: str) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path, timeout=15) as db:
            await db.execute(
                "DELETE FROM conversation_goal_events WHERE chat_id = ?",
                (str(chat_id),),
            )
            await db.execute(
                "DELETE FROM conversation_goals WHERE chat_id = ?",
                (str(chat_id),),
            )
            await db.commit()

    async def rollback_creation(
        self,
        chat_id: str,
        goal_id: str,
        previous_goal: dict[str, Any] | None,
    ) -> None:
        """Remove one failed Goal creation without erasing older Goal history."""

        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path, timeout=15) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM conversation_goal_events WHERE chat_id = ? AND goal_id = ?",
                (str(chat_id), str(goal_id)),
            )
            if previous_goal is None:
                await db.execute(
                    "DELETE FROM conversation_goals WHERE chat_id = ? AND goal_id = ?",
                    (str(chat_id), str(goal_id)),
                )
            else:
                value = dict(previous_goal)
                await db.execute(
                    """
                    INSERT INTO conversation_goals(
                        chat_id, goal_id, revision, status, payload_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        goal_id = excluded.goal_id,
                        revision = excluded.revision,
                        status = excluded.status,
                        payload_json = excluded.payload_json,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(value.get("chatId") or chat_id),
                        str(value.get("id") or ""),
                        int(value.get("revision") or 1),
                        str(value.get("status") or "negotiating"),
                        json.dumps(value, ensure_ascii=False, default=str),
                        str(value.get("createdAt") or utc_iso()),
                        str(value.get("updatedAt") or utc_iso()),
                    ),
                )
            await db.commit()

    async def active(self) -> list[dict[str, Any]]:
        await self.ensure_schema()
        terminal = ("completed", "aborted")
        async with aiosqlite.connect(self.db_path, timeout=15) as db:
            rows = await (
                await db.execute(
                    "SELECT payload_json FROM conversation_goals "
                    "WHERE status NOT IN (?, ?) ORDER BY updated_at",
                    terminal,
                )
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(str(row[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                result.append(value)
        return result

    async def event(
        self,
        goal: dict[str, Any],
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path, timeout=15) as db:
            await db.execute(
                """
                INSERT INTO conversation_goal_events(
                    chat_id, goal_id, attempt, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(goal.get("chatId") or ""),
                    str(goal.get("id") or ""),
                    int(goal.get("attempt") or 0),
                    str(event_type),
                    json.dumps(payload or {}, ensure_ascii=False, default=str),
                    utc_iso(),
                ),
            )
            await db.commit()

    async def events(self, chat_id: str, limit: int = 100) -> list[dict[str, Any]]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path, timeout=15) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    "SELECT * FROM conversation_goal_events WHERE chat_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (str(chat_id), max(1, min(int(limit), 500))),
                )
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["payload"] = json.loads(str(item.pop("payload_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                item["payload"] = {}
            result.append(item)
        return result


__all__ = ["ConversationGoalRepository", "utc_iso"]
