"""Hook bindings and ordered delivery queue stored inside one tree database."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime

from ..observability import log_operation
from ..hook.hook import Hook, HookEvent
from ..hook.storage import QueuedHookEvent, decode_event_payload, encode_event_payload
from .schema import transaction

logger = logging.getLogger(__name__)


class TreeHookStore:
    """Persist Hooks using the owning ContextTreeStore connection and lock."""

    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock) -> None:
        self._connection = connection
        self._lock = lock

    @staticmethod
    def _hook_from_row(row: sqlite3.Row) -> Hook:
        return Hook(
            id=str(row["hook_id"]),
            event=str(row["event"]),
            plugin_id=str(row["plugin_id"]),
            root_only=bool(row["root_only"]),
            matcher=str(row["matcher"]) if row["matcher"] is not None else None,
            failure_policy=str(row["failure_policy"]),  # type: ignore[arg-type]
            config=json.loads(str(row["config_json"])),
            enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def list_hooks(self) -> tuple[Hook, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT hook_id, event, plugin_id, root_only, matcher,
                       failure_policy, config_json, enabled, created_at
                FROM hook_bindings
                ORDER BY created_at, hook_id
                """
            ).fetchall()
        hooks = tuple(self._hook_from_row(row) for row in rows)
        log_operation(
            logger,
            "hook.store",
            "list_bindings",
            phase="completed",
            count=len(hooks),
            hooks=[
                {"hook_id": hook.id, "event": hook.event, "plugin_id": hook.plugin_id}
                for hook in hooks
            ],
        )
        return hooks

    def recover(self) -> None:
        """Release deliveries left claimed by a previous process."""

        with self._lock, transaction(self._connection):
            cursor = self._connection.execute(
                "UPDATE hook_queue SET status = 'pending' WHERE status = 'running'"
            )
        log_operation(
            logger,
            "hook.store",
            "recover_queue",
            phase="completed",
            released=max(cursor.rowcount, 0),
        )

    def save_hook(self, hook: Hook) -> None:
        config_json = json.dumps(
            dict(hook.config),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        with self._lock, transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO hook_bindings(
                    hook_id, event, plugin_id, root_only, matcher,
                    failure_policy, config_json, enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hook.id,
                    hook.event,
                    hook.plugin_id,
                    int(hook.root_only),
                    hook.matcher,
                    hook.failure_policy,
                    config_json,
                    int(hook.enabled),
                    hook.created_at.isoformat() if hook.created_at else datetime.now().astimezone().isoformat(),
                ),
            )
        log_operation(
            logger,
            "hook.store",
            "save_binding",
            phase="completed",
            hook_id=hook.id,
            event=hook.event,
            plugin_id=hook.plugin_id,
            root_only=hook.root_only,
            matcher=hook.matcher,
            failure_policy=hook.failure_policy,
            config=dict(hook.config),
            enabled=hook.enabled,
        )

    def delete_hook(self, hook_id: str) -> bool:
        with self._lock, transaction(self._connection):
            cursor = self._connection.execute(
                "DELETE FROM hook_bindings WHERE hook_id = ?",
                (str(hook_id),),
            )
        removed = cursor.rowcount > 0
        log_operation(
            logger,
            "hook.store",
            "delete_binding",
            phase="completed",
            hook_id=hook_id,
            removed=removed,
        )
        return removed

    def enqueue(self, event: HookEvent) -> int:
        """Queue one delivery per currently matching binding.

        The caller may already own a transaction; this method deliberately does
        not begin or commit one so Context mutation and queue insertion are atomic.
        """

        cursor = self._connection.execute(
            """
            INSERT INTO hook_queue(
                hook_id, event, tree_id, event_time, payload_json, node_id, is_root
            )
            SELECT hook_id, ?, ?, ?, ?, ?, ?
            FROM hook_bindings
            WHERE event = ?
              AND enabled = 1
              AND (root_only = 0 OR ? = 1)
            ORDER BY created_at, hook_id
            """,
            (
                event.name,
                event.tree_id,
                event.time.isoformat(),
                encode_event_payload(event),
                event.node_id,
                int(event.is_root),
                event.name,
                int(event.is_root),
            ),
        )
        count = max(cursor.rowcount, 0)
        log_operation(
            logger,
            "hook.store",
            "enqueue_event",
            phase="completed",
            tree_id=event.tree_id,
            event=event.name,
            node_id=event.node_id,
            is_root=event.is_root,
            payload=event.payload,
            deliveries=count,
        )
        return count

    def enqueue_committed(self, event: HookEvent) -> int:
        with self._lock, transaction(self._connection):
            return self.enqueue(event)

    def claim_next(self) -> QueuedHookEvent | None:
        with self._lock, transaction(self._connection):
            row = self._connection.execute(
                """
                SELECT queue.sequence, queue.event, queue.tree_id, queue.event_time,
                       queue.payload_json, queue.node_id, queue.is_root, queue.attempts,
                       binding.hook_id, binding.plugin_id, binding.root_only,
                       binding.matcher, binding.failure_policy, binding.config_json,
                       binding.enabled, binding.created_at
                FROM hook_queue AS queue
                JOIN hook_bindings AS binding ON binding.hook_id = queue.hook_id
                WHERE queue.status = 'pending'
                ORDER BY queue.sequence
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            sequence = int(row["sequence"])
            self._connection.execute(
                """
                UPDATE hook_queue
                SET status = 'running', attempts = attempts + 1, last_error = ''
                WHERE sequence = ?
                """,
                (sequence,),
            )
        hook = self._hook_from_row(row)
        event = HookEvent(
            name=str(row["event"]),
            tree_id=str(row["tree_id"]),
            time=datetime.fromisoformat(str(row["event_time"])),
            payload=decode_event_payload(str(row["event"]), str(row["payload_json"])),
            node_id=str(row["node_id"]) if row["node_id"] is not None else None,
            is_root=bool(row["is_root"]),
        )
        delivery = QueuedHookEvent(sequence, hook, event, int(row["attempts"]) + 1)
        log_operation(
            logger,
            "hook.store",
            "claim_delivery",
            phase="completed",
            sequence=sequence,
            tree_id=event.tree_id,
            event=event.name,
            node_id=event.node_id,
            hook_id=hook.id,
            plugin_id=hook.plugin_id,
            attempt=delivery.attempts,
            payload=event.payload,
        )
        return delivery

    def _set_status(self, sequence: int, status: str, error: str = "") -> None:
        with self._lock, transaction(self._connection):
            self._connection.execute(
                "UPDATE hook_queue SET status = ?, last_error = ? WHERE sequence = ?",
                (status, str(error), int(sequence)),
            )
        log_operation(
            logger,
            "hook.store",
            "set_delivery_status",
            phase="completed",
            sequence=sequence,
            status=status,
            error=error,
        )

    def complete(self, sequence: int) -> None:
        with self._lock, transaction(self._connection):
            self._connection.execute(
                "DELETE FROM hook_queue WHERE sequence = ?",
                (int(sequence),),
            )
        log_operation(
            logger,
            "hook.store",
            "complete_delivery",
            phase="completed",
            sequence=sequence,
            removed=True,
        )

    def fail(self, sequence: int, error: str) -> None:
        self._set_status(sequence, "failed", error)

    def block(self, sequence: int, error: str) -> None:
        self._set_status(sequence, "blocked", error)

    def release(self, sequence: int) -> None:
        self._set_status(sequence, "pending")

    def requeue_blocked(self, plugin_id: str) -> int:
        with self._lock, transaction(self._connection):
            cursor = self._connection.execute(
                """
                UPDATE hook_queue
                SET status = 'pending', last_error = ''
                WHERE status = 'blocked'
                  AND hook_id IN (
                      SELECT hook_id FROM hook_bindings WHERE plugin_id = ?
                  )
                """,
                (str(plugin_id),),
            )
        count = max(cursor.rowcount, 0)
        log_operation(
            logger,
            "hook.store",
            "requeue_blocked",
            phase="completed",
            plugin_id=plugin_id,
            count=count,
        )
        return count

    def retry_failed(self) -> int:
        with self._lock, transaction(self._connection):
            cursor = self._connection.execute(
                """
                UPDATE hook_queue
                SET status = 'pending', last_error = ''
                WHERE status = 'failed'
                """
            )
        count = max(cursor.rowcount, 0)
        log_operation(
            logger,
            "hook.store",
            "retry_failed",
            phase="completed",
            count=count,
        )
        return count

    def has_work(self) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM hook_queue WHERE status = 'pending' LIMIT 1"
            ).fetchone()
        return row is not None
