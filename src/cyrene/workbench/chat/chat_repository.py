"""SQLite persistence boundary for Workbench conversation documents."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, cast

from cyrene.workbench.chat.chat_dto import ChatDetailDTO, ChatStoreDTO
from cyrene.workbench.persistence.store import (
    mutate_chat,
    read_chat,
    read_chat_summaries,
    read_document,
    write_chat,
    write_document,
)
from cyrene.workbench.persistence.schema import connect


def _empty_store() -> dict[str, Any]:
    return {"chats": []}


class ChatRepository:
    """Own the durable Workbench chat store without importing an Agent loop."""

    def __init__(self, db_path: str = "") -> None:
        self._db_path = str(db_path or "")
        self._lock = threading.RLock()

    def configure(self, db_path: str) -> None:
        normalized = str(db_path or "").strip()
        if not normalized:
            raise ValueError("Workbench chat repository requires a database path")
        self._db_path = normalized

    def _database(self) -> str:
        if not self._db_path:
            raise RuntimeError("Workbench chat repository is not configured")
        return self._db_path

    def read(self) -> ChatStoreDTO:
        value = read_document(
            self._database(),
            "chats",
            _empty_store,
        )
        return cast(ChatStoreDTO, value if isinstance(value, dict) else _empty_store())

    def read_summaries(self) -> ChatStoreDTO:
        return cast(
            ChatStoreDTO,
            {
                "chats": read_chat_summaries(
                    self._database(),
                    _empty_store,
                )
            },
        )

    def get(self, chat_id: str) -> ChatDetailDTO | None:
        return cast(
            ChatDetailDTO | None,
            read_chat(
                self._database(),
                str(chat_id or ""),
                _empty_store,
            ),
        )

    def write(self, payload: ChatStoreDTO | dict[str, Any]) -> None:
        merged = write_document(
            self._database(),
            "chats",
            cast(dict[str, Any], payload),
            _empty_store,
        )
        if isinstance(payload, dict) and isinstance(merged, dict):
            payload.clear()
            payload.update(merged)
            if hasattr(payload, "_workbench_base"):
                payload._workbench_base = getattr(  # type: ignore[attr-defined]
                    merged,
                    "_workbench_base",
                    dict(merged),
                )
                payload._workbench_versions = getattr(  # type: ignore[attr-defined]
                    merged,
                    "_workbench_versions",
                    {},
                )

    def write_one(
        self,
        chat: ChatDetailDTO | dict[str, Any],
        *,
        base_chat: ChatDetailDTO | dict[str, Any] | None = None,
        commit_event: dict[str, Any] | None = None,
    ) -> ChatDetailDTO | None:
        return cast(
            ChatDetailDTO | None,
            write_chat(
                self._database(),
                cast(dict[str, Any], chat),
                _empty_store,
                base_chat=(
                    cast(dict[str, Any], base_chat)
                    if base_chat is not None
                    else None
                ),
                commit_event=commit_event,
            ),
        )

    def pending_commit_events(
        self,
        chat_id: str = "",
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Claim durable public-commit events for idempotent side effects."""

        with self._lock:
            conn = connect(self._database())
            try:
                conn.execute("BEGIN IMMEDIATE")
                params: list[Any] = []
                where = "status = 'pending'"
                if str(chat_id or "").strip():
                    where += " AND chat_id = ?"
                    params.append(str(chat_id))
                params.append(max(1, int(limit)))
                rows = conn.execute(
                    "SELECT event_id, payload_json "
                    "FROM workbench_conversation_commit_outbox "
                    f"WHERE {where} ORDER BY created_at, event_id LIMIT ?",
                    params,
                ).fetchall()
                events: list[dict[str, Any]] = []
                for event_id, payload_json in rows:
                    payload = json.loads(str(payload_json))
                    if not isinstance(payload, dict):
                        raise ValueError(
                            f"invalid conversation commit payload: {event_id}"
                        )
                    payload.setdefault("event_id", str(event_id))
                    events.append(payload)
                event_ids = [str(row[0]) for row in rows]
                if event_ids:
                    conn.executemany(
                        "UPDATE workbench_conversation_commit_outbox "
                        "SET status = 'running', attempts = attempts + 1 "
                        "WHERE event_id = ?",
                        [(event_id,) for event_id in event_ids],
                    )
                conn.commit()
                return events
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def complete_commit_event(self, event_id: str) -> None:
        with self._lock:
            conn = connect(self._database())
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE workbench_conversation_commit_outbox "
                    "SET status = 'completed', completed_at = ?, last_error = '' "
                    "WHERE event_id = ?",
                    (datetime.now(timezone.utc).isoformat(), str(event_id)),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def fail_commit_event(self, event_id: str, error: str) -> None:
        with self._lock:
            conn = connect(self._database())
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE workbench_conversation_commit_outbox "
                    "SET status = 'pending', last_error = ? WHERE event_id = ?",
                    (str(error or "")[:1000], str(event_id)),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def mutate_one(
        self,
        chat_id: str,
        mutation: Callable[[dict[str, Any]], Any],
    ) -> ChatDetailDTO | None:
        return cast(
            ChatDetailDTO | None,
            mutate_chat(
                self._database(),
                str(chat_id or ""),
                mutation,
                _empty_store,
            ),
        )

    @staticmethod
    def find(
        payload: ChatStoreDTO | dict[str, Any],
        chat_id: str,
    ) -> ChatDetailDTO | None:
        target = str(chat_id or "")
        return cast(
            ChatDetailDTO | None,
            next(
                (
                    chat
                    for chat in payload.get("chats", [])
                    if isinstance(chat, dict)
                    and str(chat.get("id") or "") == target
                ),
                None,
            ),
        )

    def mutate(self, mutation: Callable[[ChatStoreDTO], Any]) -> Any:
        with self._lock:
            payload = self.read()
            result = mutation(payload)
            self.write(payload)
            return result

    @property
    def lock(self) -> AbstractContextManager[Any]:
        return self._lock


__all__ = ["ChatRepository"]
