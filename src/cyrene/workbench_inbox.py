"""Run-scoped inbox for Workbench conversation agents.

The inbox decouples a model turn from slow tool execution.  Tools run in
background tasks and publish their terminal result into the inbox; the agent
waits on inbox events, so user guidance can be accepted while a tool is still
running. Tool lifecycle telemetry is written to a separate audit table so it
cannot interfere with queue priority or wakeups. This module is deliberately
scoped to Workbench conversations via a ContextVar. Task sessions and the
legacy chat loop keep their existing execution semantics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)

ToolRunner = Callable[[], Awaitable[str]]
_MAX_PARALLEL_TOOL_CALLS = 8


@dataclass(frozen=True)
class _BatchCall:
    tool_call_id: str
    tool_name: str
    runner: ToolRunner
    read_only: bool = False
    resource_keys: tuple[str, ...] = ()
    requires_order: bool = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkbenchAgentInbox:
    """One reliable, session-isolated event inbox for a live chat run."""

    def __init__(self, session_id: str, db_path: str = "", *, run_id: str = "") -> None:
        self.session_id = str(session_id)
        self.run_id = str(run_id or f"run_{uuid4().hex}")
        self.db_path = str(db_path or "")
        self.round_id = ""
        self._queue: asyncio.PriorityQueue[tuple[int, int, dict[str, Any]]] = asyncio.PriorityQueue()
        self._sequence = 0
        self._guidance: list[dict[str, Any]] = []
        self._pending_tool_results: dict[str, dict[str, Any]] = {}
        self._guidance_signal = asyncio.Event()
        self._guidance_pending_count = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self._persistence_tasks: set[asyncio.Task[Any]] = set()
        self._tool_results_persisting: set[str] = set()
        self._tool_results_completed_early: set[str] = set()
        self._termination_reason = ""
        self._telemetry_tail: asyncio.Task[Any] | None = None
        self._result_queued_at: dict[str, float] = {}
        self._tool_submitted_at: dict[str, float] = {}
        self._closed = False
        if self.db_path:
            self._ensure_schema()
            self._recover_pending_guidance()

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workbench_agent_inbox (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    round_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    dedupe_key TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(workbench_agent_inbox)")
            }
            for name, definition in (
                ("run_id", "TEXT NOT NULL DEFAULT ''"),
                ("batch_id", "TEXT NOT NULL DEFAULT ''"),
                ("termination_reason", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE workbench_agent_inbox ADD COLUMN {name} {definition}"
                    )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_workbench_agent_inbox_dedupe "
                "ON workbench_agent_inbox(session_id, dedupe_key) WHERE dedupe_key <> ''"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_workbench_agent_inbox_pending "
                "ON workbench_agent_inbox(session_id, status, priority, created_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workbench_agent_run_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    round_id TEXT NOT NULL DEFAULT '',
                    batch_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL DEFAULT '',
                    tool_name TEXT NOT NULL DEFAULT '',
                    queue_length INTEGER NOT NULL DEFAULT 0,
                    duration_ms REAL,
                    tool_queue_wait_ms REAL,
                    tool_execution_ms REAL,
                    agent_wait_ms REAL,
                    result_wait_ms REAL,
                    result_queue_delay_ms REAL,
                    termination_reason TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            trace_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(workbench_agent_run_events)")
            }
            for name in (
                "tool_queue_wait_ms", "tool_execution_ms", "agent_wait_ms",
                "result_wait_ms", "result_queue_delay_ms",
            ):
                if name not in trace_columns:
                    conn.execute(
                        f"ALTER TABLE workbench_agent_run_events ADD COLUMN {name} REAL"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_workbench_agent_run_events_run "
                "ON workbench_agent_run_events(session_id, run_id, created_at)"
            )

    def _queue_length(self) -> int:
        return self._queue.qsize() + len(self._guidance) + len(self._pending_tool_results)

    def _record_event(
        self,
        event_type: str,
        *,
        batch_id: str = "",
        tool_call_id: str = "",
        tool_name: str = "",
        duration_ms: float | None = None,
        tool_queue_wait_ms: float | None = None,
        tool_execution_ms: float | None = None,
        agent_wait_ms: float | None = None,
        result_wait_ms: float | None = None,
        result_queue_delay_ms: float | None = None,
        termination_reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Persist operational telemetry without placing it in the agent queue."""
        queue_length = self._queue_length()
        logger.info(
            "workbench_inbox event=%s session_id=%s run_id=%s round_id=%s "
            "batch_id=%s tool_call_id=%s tool_name=%s queue_length=%s duration_ms=%s "
            "tool_queue_wait_ms=%s tool_execution_ms=%s agent_wait_ms=%s "
            "result_wait_ms=%s result_queue_delay_ms=%s "
            "termination_reason=%s",
            event_type, self.session_id, self.run_id, self.round_id, batch_id,
            tool_call_id, tool_name, queue_length, duration_ms, tool_queue_wait_ms,
            tool_execution_ms, agent_wait_ms, result_wait_ms,
            result_queue_delay_ms, termination_reason,
        )
        if not self.db_path:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO workbench_agent_run_events
                    (event_id, session_id, run_id, round_id, batch_id, event_type,
                     tool_call_id, tool_name, queue_length, duration_ms,
                     tool_queue_wait_ms, tool_execution_ms, agent_wait_ms,
                     result_wait_ms, result_queue_delay_ms,
                     termination_reason, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"trace_{uuid4().hex}", self.session_id, self.run_id,
                        self.round_id, str(batch_id), str(event_type),
                        str(tool_call_id), str(tool_name), queue_length, duration_ms,
                        tool_queue_wait_ms, tool_execution_ms, agent_wait_ms,
                        result_wait_ms, result_queue_delay_ms,
                        str(termination_reason),
                        json.dumps(payload or {}, ensure_ascii=False), _now(),
                    ),
                )
        except Exception:
            logger.exception("Failed to persist Workbench inbox telemetry event")

    def _persist(self, event: dict[str, Any]) -> bool | None:
        if not self.db_path:
            return True
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO workbench_agent_inbox
                    (event_id, session_id, run_id, round_id, batch_id, event_type,
                     status, priority, dedupe_key, payload_json, created_at,
                     completed_at, termination_reason)
                    VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, '', '')
                    """,
                    (
                        event["event_id"], self.session_id, self.run_id,
                        event.get("round_id", ""), event.get("batch_id", ""),
                        event["type"], int(event.get("priority", 0)),
                        event.get("dedupe_key", ""),
                        json.dumps(event.get("payload") or {}, ensure_ascii=False),
                        event["created_at"],
                    ),
                )
                return cursor.rowcount > 0
        except Exception:
            logger.exception("Failed to persist Workbench inbox event")
            return None

    def _existing_event_id(self, dedupe_key: str) -> str:
        if not self.db_path or not dedupe_key:
            return ""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT event_id FROM workbench_agent_inbox WHERE session_id=? AND dedupe_key=?",
                    (self.session_id, dedupe_key),
                ).fetchone()
            return str(row[0]) if row else ""
        except Exception:
            logger.exception("Failed to resolve duplicate Workbench inbox event")
            return ""

    def _complete(self, event_id: str) -> None:
        if not self.db_path:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE workbench_agent_inbox SET status='completed', completed_at=? "
                    "WHERE event_id=? AND session_id=?",
                    (_now(), str(event_id), self.session_id),
                )
        except Exception:
            logger.exception("Failed to acknowledge Workbench inbox event %s", event_id)

    def _claim(self, event_id: str) -> None:
        if not self.db_path:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE workbench_agent_inbox SET status='claimed' "
                    "WHERE event_id=? AND session_id=? AND status='queued'",
                    (str(event_id), self.session_id),
                )
        except Exception:
            logger.exception("Failed to claim Workbench inbox event %s", event_id)

    def _recover_pending_guidance(self) -> None:
        """Requeue guidance after a crash; discard orphaned tool results.

        Tool jobs themselves cannot survive a process restart, while user
        guidance remains valid and should be applied when this conversation is
        next resumed.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE workbench_agent_inbox SET status='failed', completed_at=?, "
                    "termination_reason='process_recovery' "
                    "WHERE session_id=? AND event_type='tool_result' AND status IN ('queued','claimed')",
                    (_now(), self.session_id),
                )
                rows = conn.execute(
                    "SELECT event_id, round_id, priority, dedupe_key, payload_json, created_at "
                    "FROM workbench_agent_inbox WHERE session_id=? AND event_type='guidance' "
                    "AND status IN ('queued','claimed') ORDER BY priority DESC, created_at",
                    (self.session_id,),
                ).fetchall()
                conn.execute(
                    "UPDATE workbench_agent_inbox SET status='queued', run_id=? WHERE session_id=? "
                    "AND event_type='guidance' AND status='claimed'",
                    (self.run_id, self.session_id),
                )
                conn.execute(
                    "UPDATE workbench_agent_inbox SET run_id=? WHERE session_id=? "
                    "AND event_type='guidance' AND status='queued'",
                    (self.run_id, self.session_id),
                )
            for row in rows:
                self._enqueue_nowait({
                    "event_id": str(row[0]),
                    "session_id": self.session_id,
                    "round_id": str(row[1] or ""),
                    "type": "guidance",
                    "priority": int(row[2] or 0),
                    "dedupe_key": str(row[3] or ""),
                    "payload": json.loads(str(row[4]) or "{}"),
                    "created_at": str(row[5] or ""),
                })
            if rows:
                self._guidance_pending_count += len(rows)
                self._guidance_signal.set()
        except Exception:
            logger.exception("Failed to recover Workbench inbox for %s", self.session_id)

    def _queue_item(self, event: dict[str, Any]) -> tuple[int, int, dict[str, Any]]:
        self._sequence += 1
        # asyncio.PriorityQueue returns the smallest key first. Negating the
        # priority makes guidance (100) outrank normal tool results (0), while
        # the monotonic sequence preserves FIFO order inside one priority.
        return (-int(event.get("priority", 0)), self._sequence, event)

    def _enqueue_nowait(self, event: dict[str, Any]) -> None:
        self._queue.put_nowait(self._queue_item(event))

    def _complete_tool_result(self, event_id: str) -> None:
        """Acknowledge a result without racing its background INSERT."""
        if event_id in self._tool_results_persisting:
            self._tool_results_completed_early.add(event_id)
            return
        self._complete(event_id)

    def _schedule_tool_result_persistence(self, event: dict[str, Any]) -> None:
        """Persist a live result without putting SQLite in the wakeup path."""
        event_id = str(event["event_id"])
        self._tool_results_persisting.add(event_id)

        async def persist() -> None:
            durable_event_id = event_id
            try:
                persisted = await asyncio.to_thread(self._persist, event)
                if persisted is False:
                    durable_event_id = await asyncio.to_thread(
                        self._existing_event_id, str(event.get("dedupe_key") or "")
                    ) or event_id
                    logger.info(
                        "Workbench tool result already persisted; live result was still delivered "
                        "[session_id=%s event_id=%s durable_event_id=%s]",
                        self.session_id,
                        event_id,
                        durable_event_id,
                    )
                elif persisted is None:
                    logger.warning(
                        "Workbench tool-result persistence failed after live delivery "
                        "[session_id=%s event_id=%s]",
                        self.session_id,
                        event_id,
                    )
                if event_id in self._tool_results_completed_early and persisted is not None:
                    await asyncio.to_thread(self._complete, durable_event_id)
                elif self._closed and persisted is not None:
                    await asyncio.to_thread(
                        self._cancel_event,
                        durable_event_id,
                        self._termination_reason or "completed",
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Workbench tool-result persistence failed after live delivery "
                    "[session_id=%s event_id=%s]",
                    self.session_id,
                    event_id,
                )
            finally:
                self._tool_results_persisting.discard(event_id)
                self._tool_results_completed_early.discard(event_id)

        task = asyncio.create_task(persist())
        self._persistence_tasks.add(task)
        task.add_done_callback(self._persistence_tasks.discard)

    def _record_event_background(self, event_type: str, **kwargs: Any) -> None:
        """Write ordered handoff telemetry without blocking inbox consumption."""
        previous = self._telemetry_tail

        async def record() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            await asyncio.to_thread(self._record_event, event_type, **kwargs)

        task = asyncio.create_task(record())
        self._telemetry_tail = task
        self._persistence_tasks.add(task)
        task.add_done_callback(self._persistence_tasks.discard)

    def _run_persistence_background(
        self, operation: Callable[..., Any], *args: Any
    ) -> None:
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        self._persistence_tasks.add(task)
        task.add_done_callback(self._persistence_tasks.discard)

    async def put(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        priority: int = 0,
        dedupe_key: str = "",
        round_id: str = "",
        batch_id: str = "",
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("Workbench agent inbox is closed")
        event = {
            "event_id": f"evt_{uuid4().hex}",
            "session_id": self.session_id,
            "round_id": str(round_id or self.round_id),
            "run_id": self.run_id,
            "batch_id": str(batch_id or ""),
            "type": str(event_type),
            "payload": dict(payload or {}),
            "priority": int(priority),
            "dedupe_key": str(dedupe_key or ""),
            "created_at": _now(),
        }
        if event_type == "tool_result":
            # The live agent must be woken before any optional durability work.
            # A slow or unavailable SQLite connection can no longer strand an
            # already-completed tool call between execution and inbox delivery.
            self._enqueue_nowait(event)
            self._schedule_tool_result_persistence(event)
            return event

        persisted = self._persist(event)
        if persisted is False:
            # A duplicate dedupe key is already represented in the queue/log.
            if dedupe_key:
                return {
                    **event,
                    "event_id": self._existing_event_id(dedupe_key) or event["event_id"],
                    "duplicate": True,
                }
            raise RuntimeError("Failed to persist Workbench inbox event")
        if persisted is None:
            raise RuntimeError("Failed to persist Workbench inbox event")
        if event_type == "guidance":
            self._guidance_pending_count += 1
            self._guidance_signal.set()
        await self._queue.put(self._queue_item(event))
        return event

    async def put_guidance(self, text: str, *, client_request_id: str = "") -> dict[str, Any]:
        event = await self.put(
            "guidance",
            {"text": str(text).strip(), "client_request_id": str(client_request_id)},
            priority=100,
            dedupe_key=f"guidance:{client_request_id}" if client_request_id else "",
        )
        if not event.get("duplicate"):
            self._record_event(
                "guidance_queued",
                payload={"event_id": event["event_id"], "client_request_id": client_request_id},
            )
        return event

    async def _run_tool(
        self,
        tool_call_id: str,
        tool_name: str,
        runner: ToolRunner,
        *,
        batch_id: str,
    ) -> None:
        started = time.perf_counter()
        submitted_at = self._tool_submitted_at.pop(tool_call_id, started)
        self._record_event(
            "tool_started", batch_id=batch_id, tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_queue_wait_ms=(started - submitted_at) * 1000,
        )
        try:
            result = await runner()
            payload = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "result": str(result),
                "is_error": False,
            }
        except asyncio.CancelledError:
            payload = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "result": "Tool cancelled because the chat run was interrupted.",
                "is_error": True,
            }
            duration_ms = (time.perf_counter() - started) * 1000
            event = await self.put(
                "tool_result", payload, batch_id=batch_id,
                dedupe_key=f"tool-result:{tool_call_id}"
            )
            self._result_queued_at[tool_call_id] = time.perf_counter()
            self._record_event_background(
                "tool_result_queued", batch_id=batch_id,
                tool_call_id=tool_call_id, tool_name=tool_name,
                duration_ms=duration_ms, tool_execution_ms=duration_ms,
                payload={"event_id": event["event_id"], "cancelled": True},
            )
            raise
        except Exception as exc:
            payload = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "result": f"Tool failed: {exc}",
                "is_error": True,
            }
        duration_ms = (time.perf_counter() - started) * 1000
        event = await self.put(
            "tool_result", payload, batch_id=batch_id,
            dedupe_key=f"tool-result:{tool_call_id}"
        )
        self._result_queued_at[tool_call_id] = time.perf_counter()
        self._record_event_background(
            "tool_result_queued", batch_id=batch_id,
            tool_call_id=tool_call_id, tool_name=tool_name,
            duration_ms=duration_ms, tool_execution_ms=duration_ms,
            payload={"event_id": event["event_id"], "is_error": payload["is_error"]},
        )

    def submit_tool(
        self,
        tool_call_id: str,
        tool_name: str,
        runner: ToolRunner,
        *,
        batch_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if self._closed:
            raise RuntimeError("Workbench agent inbox is closed")
        batch_id = str(batch_id or f"batch_{uuid4().hex}")
        self._tool_submitted_at[tool_call_id] = time.perf_counter()
        self._record_event(
            "tool_submitted", batch_id=batch_id, tool_call_id=tool_call_id,
            tool_name=tool_name, payload=dict(metadata or {}),
        )
        task = asyncio.create_task(
            self._run_tool(tool_call_id, tool_name, runner, batch_id=batch_id)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return batch_id

    @staticmethod
    def _normalize_batch_call(raw: tuple[Any, ...]) -> _BatchCall:
        if len(raw) not in {3, 4}:
            raise ValueError("tool batch calls must contain 3 items plus optional metadata")
        tool_call_id, tool_name, runner = raw[:3]
        metadata = raw[3] if len(raw) == 4 and isinstance(raw[3], dict) else {}
        read_only = bool(metadata.get("read_only"))
        resource_keys = tuple(
            str(key) for key in (metadata.get("resource_keys") or ()) if str(key)
        )
        return _BatchCall(
            tool_call_id=str(tool_call_id),
            tool_name=str(tool_name),
            runner=runner,
            read_only=read_only,
            resource_keys=resource_keys,
            requires_order=bool(
                metadata.get("requires_order", True)
                or (not read_only and not resource_keys)
            ),
        )

    @staticmethod
    def _resource_keys_overlap(left: str, right: str) -> bool:
        if left == right:
            return True
        if left.endswith(":*") and right.startswith(left[:-1]):
            return True
        if right.endswith(":*") and left.startswith(right[:-1]):
            return True
        if not left.startswith("fs:") or not right.startswith("fs:"):
            return False
        left_path = left[3:]
        right_path = right[3:]
        if "workspace" in {left_path, right_path}:
            return True
        try:
            left_obj = Path(left_path)
            right_obj = Path(right_path)
            return left_obj == right_obj or left_obj in right_obj.parents or right_obj in left_obj.parents
        except Exception:
            return False

    @classmethod
    def _calls_conflict(cls, left: _BatchCall, right: _BatchCall) -> bool:
        if left.requires_order or right.requires_order:
            return True
        if left.read_only and right.read_only:
            return False
        return any(
            cls._resource_keys_overlap(left_key, right_key)
            for left_key in left.resource_keys
            for right_key in right.resource_keys
        )

    async def _queue_skipped_call(self, call: _BatchCall, batch_id: str) -> None:
        submitted_at = self._tool_submitted_at.pop(
            call.tool_call_id, time.perf_counter()
        )
        skipped_at = time.perf_counter()
        event = await self.put(
            "tool_result",
            {
                "tool_call_id": call.tool_call_id,
                "tool_name": call.tool_name,
                "result": "Skipped before execution because new user guidance superseded this tool-call batch.",
                "is_error": False,
                "skipped": True,
            },
            batch_id=batch_id,
            dedupe_key=f"tool-result:{call.tool_call_id}",
        )
        self._result_queued_at[call.tool_call_id] = time.perf_counter()
        self._record_event_background(
            "tool_result_queued", batch_id=batch_id,
            tool_call_id=call.tool_call_id, tool_name=call.tool_name,
            duration_ms=0.0,
            tool_queue_wait_ms=(skipped_at - submitted_at) * 1000,
            tool_execution_ms=0.0,
            payload={"event_id": event["event_id"], "skipped": True},
        )

    def submit_tool_batch(
        self,
        calls: list[tuple[Any, ...]],
        *,
        batch_id: str = "",
    ) -> str:
        """Run non-conflicting calls concurrently while preserving barriers."""
        if self._closed:
            raise RuntimeError("Workbench agent inbox is closed")
        batch_id = str(batch_id or f"batch_{uuid4().hex}")
        normalized = [self._normalize_batch_call(call) for call in calls]
        for call in normalized:
            self._tool_submitted_at[call.tool_call_id] = time.perf_counter()
            self._record_event(
                "tool_submitted", batch_id=batch_id,
                tool_call_id=call.tool_call_id, tool_name=call.tool_name,
                payload={
                    "read_only": call.read_only,
                    "resource_keys": list(call.resource_keys),
                    "requires_order": call.requires_order,
                },
            )

        async def run_batch() -> None:
            pending = list(normalized)
            running: dict[asyncio.Task[Any], _BatchCall] = {}
            try:
                while pending or running:
                    if self._guidance_signal.is_set() and pending:
                        skipped, pending = pending, []
                        for call in skipped:
                            await self._queue_skipped_call(call, batch_id)

                    launched = False
                    for call in list(pending):
                        if len(running) >= _MAX_PARALLEL_TOOL_CALLS:
                            break
                        index = pending.index(call)
                        earlier_pending = pending[:index]
                        if any(item.requires_order for item in earlier_pending):
                            break
                        if call.requires_order:
                            if index == 0 and not running:
                                pending.remove(call)
                                task = asyncio.create_task(self._run_tool(
                                    call.tool_call_id, call.tool_name, call.runner,
                                    batch_id=batch_id,
                                ))
                                running[task] = call
                                launched = True
                            break
                        if any(self._calls_conflict(call, item) for item in running.values()):
                            continue
                        if any(self._calls_conflict(call, item) for item in earlier_pending):
                            continue
                        pending.remove(call)
                        task = asyncio.create_task(self._run_tool(
                            call.tool_call_id, call.tool_name, call.runner,
                            batch_id=batch_id,
                        ))
                        running[task] = call
                        launched = True

                    if launched:
                        continue
                    if not running and pending:
                        # Conservative deadlock escape; the first pending call
                        # has no predecessor and is always safe to start alone.
                        call = pending.pop(0)
                        task = asyncio.create_task(self._run_tool(
                            call.tool_call_id, call.tool_name, call.runner,
                            batch_id=batch_id,
                        ))
                        running[task] = call

                    if running:
                        guidance_wait = (
                            asyncio.create_task(self._guidance_signal.wait())
                            if pending and not self._guidance_signal.is_set()
                            else None
                        )
                        waitables = [*running]
                        if guidance_wait is not None:
                            waitables.append(guidance_wait)
                        done, _ = await asyncio.wait(
                            waitables,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if guidance_wait is not None and guidance_wait not in done:
                            guidance_wait.cancel()
                            await asyncio.gather(guidance_wait, return_exceptions=True)
                        for task in done:
                            if guidance_wait is not None and task is guidance_wait:
                                continue
                            running.pop(task, None)
                            await task
            finally:
                for task in running:
                    if not task.done():
                        task.cancel()
                if running:
                    await asyncio.gather(*running, return_exceptions=True)

        task = asyncio.create_task(run_batch())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return batch_id

    async def wait_for_tool_result(self, tool_call_id: str) -> str:
        """Wait for one result while retaining guidance for the next model turn."""
        wait_started = time.perf_counter()
        while True:
            event = self._pending_tool_results.pop(tool_call_id, None)
            if event is None:
                _priority, _sequence, event = await self._queue.get()
            event_type = str(event.get("type") or "")
            if event_type == "guidance":
                self._guidance.append(event)
                self._claim(event["event_id"])
                self._record_event(
                    "guidance_claimed", payload={"event_id": event["event_id"]}
                )
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if event_type == "tool_result" and str(payload.get("tool_call_id") or "") == tool_call_id:
                self._complete_tool_result(str(event["event_id"]))
                queued_at = self._result_queued_at.pop(tool_call_id, None)
                queue_delay_ms = (
                    (time.perf_counter() - queued_at) * 1000 if queued_at is not None else None
                )
                self._record_event_background(
                    "tool_result_consumed",
                    batch_id=str(event.get("batch_id") or ""),
                    tool_call_id=tool_call_id,
                    tool_name=str(payload.get("tool_name") or ""),
                    duration_ms=(time.perf_counter() - wait_started) * 1000,
                    agent_wait_ms=(time.perf_counter() - wait_started) * 1000,
                    result_wait_ms=(time.perf_counter() - wait_started) * 1000,
                    result_queue_delay_ms=queue_delay_ms,
                    payload={
                        "event_id": event["event_id"],
                        "result_queue_delay_ms": queue_delay_ms,
                    },
                )
                return str(payload.get("result") or "")
            if event_type == "tool_result":
                unexpected_id = str(payload.get("tool_call_id") or "")
                if unexpected_id:
                    self._pending_tool_results[unexpected_id] = event
                    continue
            # Retain future event types rather than acknowledging or dropping.
            await self._queue.put(self._queue_item(event))
            await asyncio.sleep(0)

    def collect_guidance_nowait(self) -> list[dict[str, Any]]:
        while True:
            try:
                _priority, _sequence, event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if str(event.get("type") or "") == "guidance":
                self._guidance.append(event)
                self._claim(event["event_id"])
                self._record_event(
                    "guidance_claimed", payload={"event_id": event["event_id"]}
                )
            else:
                self._enqueue_nowait(event)
                break
        items, self._guidance = self._guidance, []
        return items

    def has_guidance_nowait(self) -> bool:
        """Collect queued guidance without consuming the retained messages."""
        while True:
            try:
                _priority, _sequence, event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if str(event.get("type") or "") == "guidance":
                self._guidance.append(event)
                self._claim(event["event_id"])
                self._record_event(
                    "guidance_claimed", payload={"event_id": event["event_id"]}
                )
            else:
                self._enqueue_nowait(event)
                break
        return bool(self._guidance)

    def acknowledge(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            self._complete(str(event.get("event_id") or ""))
            if str(event.get("type") or "") == "guidance":
                self._guidance_pending_count = max(0, self._guidance_pending_count - 1)
                self._record_event(
                    "guidance_applied", payload={"event_id": event.get("event_id", "")}
                )
        if self._guidance_pending_count == 0:
            self._guidance_signal.clear()

    def _cancel_pending(self, termination_reason: str) -> None:
        if not self.db_path:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE workbench_agent_inbox SET status='cancelled', completed_at=?, "
                    "termination_reason=? WHERE session_id=? AND run_id=? "
                    "AND status IN ('queued','claimed')",
                    (_now(), termination_reason, self.session_id, self.run_id),
                )
        except Exception:
            logger.exception("Failed to clean pending Workbench inbox events")

    def _cancel_event(self, event_id: str, termination_reason: str) -> None:
        if not self.db_path:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE workbench_agent_inbox SET status='cancelled', completed_at=?, "
                    "termination_reason=? WHERE event_id=? AND session_id=? "
                    "AND status IN ('queued','claimed')",
                    (_now(), termination_reason, str(event_id), self.session_id),
                )
        except Exception:
            logger.exception("Failed to clean Workbench inbox event %s", event_id)

    async def close(self, *, termination_reason: str = "completed") -> None:
        if self._closed:
            return
        self._termination_reason = str(termination_reason or "completed")
        tasks = list(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=2.0)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            # A third-party/MCP tool may suppress cancellation. Do not let one
            # such tool prevent the Workbench chat run from settling forever.
            for task in pending:
                task.cancel()
        self._tasks.clear()
        self._closed = True
        self._run_persistence_background(self._cancel_pending, self._termination_reason)
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._guidance.clear()
        self._pending_tool_results.clear()
        self._guidance_pending_count = 0
        self._guidance_signal.clear()
        self._record_event_background(
            "run_terminated", termination_reason=self._termination_reason
        )


_workbench_agent_inbox: ContextVar[WorkbenchAgentInbox | None] = ContextVar(
    "_workbench_agent_inbox", default=None
)


def current_workbench_inbox() -> WorkbenchAgentInbox | None:
    return _workbench_agent_inbox.get()


__all__ = [
    "WorkbenchAgentInbox",
    "_workbench_agent_inbox",
    "current_workbench_inbox",
]
