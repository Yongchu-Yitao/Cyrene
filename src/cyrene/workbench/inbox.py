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
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from cyrene.observability.trace import trace_span
from cyrene.localization import localized
from cyrene.workbench.persistence.schema import ensure_schema

logger = logging.getLogger(__name__)

ToolRunner = Callable[[], Awaitable[str]]
_MAX_PARALLEL_TOOL_CALLS = 8
_TELEMETRY_FLUSH_INTERVAL_SECONDS = 0.05
_TELEMETRY_BATCH_MAX = 64
_TELEMETRY_INSERT = """
    INSERT OR IGNORE INTO workbench_agent_run_events
    (event_id, session_id, run_id, round_id, batch_id, event_type,
     tool_call_id, tool_name, queue_length, duration_ms,
     tool_queue_wait_ms, tool_execution_ms, agent_wait_ms,
     result_wait_ms, result_queue_delay_ms,
     termination_reason, payload_json, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class GuidanceAdmissionClosed(RuntimeError):
    """Raised when a run has crossed its final guidance boundary."""


@dataclass(frozen=True)
class _BatchCall:
    tool_call_id: str
    tool_name: str
    runner: ToolRunner
    read_only: bool = False
    resource_keys: tuple[str, ...] = ()
    requires_order: bool = True
    arguments: dict[str, Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_result_is_error(result: Any) -> bool:
    """Detect structured tool failures that were returned instead of raised."""
    text = str(result or "").strip()
    if not text:
        return False
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        return str(parsed.get("status") or "").strip().lower() in {
            "error",
            "failed",
            "failure",
            "uncertain",
        }
    return text.lower().startswith(("error", "tool failed", "failed to", "failed:"))


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
        # Guidance persistence and the agent's terminal check share this lock.
        # Exactly one side wins: either the durable guidance is queued before
        # the agent checks, or the agent seals admission and the HTTP request is
        # told to promote the text to a normal follow-up.
        self._guidance_admission_lock = asyncio.Lock()
        self._guidance_admission_open = True
        self._guidance_pending_count = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self._persistence_tasks: set[asyncio.Task[Any]] = set()
        self._live_events_persisting: set[str] = set()
        self._live_events_completed_early: set[str] = set()
        self._live_events_claimed_early: set[str] = set()
        self._live_dedupe_events: dict[str, dict[str, Any]] = {}
        self._termination_reason = ""
        self._telemetry_pending: list[tuple[Any, ...]] = []
        self._telemetry_flush_signal = asyncio.Event()
        self._telemetry_flush_lock = asyncio.Lock()
        self._telemetry_flush_task: asyncio.Task[None] | None = None
        # Telemetry and queue acknowledgements can run concurrently in worker
        # threads. Serialize this inbox's short SQLite transactions so a
        # background trace write cannot hold up the agent's result path.
        self._db_lock = threading.RLock()
        self._result_queued_at: dict[str, float] = {}
        self._tool_submitted_at: dict[str, float] = {}
        self._live_tool_states: dict[str, dict[str, Any]] = {}
        self._closed = False
        self._closing = False
        if self.db_path:
            ensure_schema(self.db_path)
            self._recover_pending_guidance()

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.db_path).expanduser().resolve()
        conn = sqlite3.connect(str(path), timeout=5)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def _db_connection(self):
        """Commit and close one serialized inbox database transaction."""
        with self._db_lock:
            conn = self._connect()
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def configure_storage(self, db_path: str) -> None:
        """Attach durable storage and recover events from a worker thread.

        ``ChatRunManager`` deliberately constructs an inbox without a database
        path on the HTTP event loop, then calls this method through
        ``asyncio.to_thread`` before the agent starts.  Direct callers keep the
        existing eager-construction behavior.
        """
        if self.db_path:
            return
        self.db_path = str(db_path or "")
        if self.db_path:
            ensure_schema(self.db_path)
            self._recover_pending_guidance()

    def _queue_length(self) -> int:
        return self._queue.qsize() + len(self._guidance) + len(self._pending_tool_results)

    def live_snapshot(self) -> dict[str, Any]:
        """Return the current in-memory state for the Workbench inspector.

        SQLite remains the durable source of truth, but tool results are put on
        the agent queue before their background INSERT completes.  Including a
        bounded live view here lets the Context tab show that event immediately
        instead of briefly displaying counters without the corresponding
        content.
        """
        events: list[dict[str, Any]] = []
        seen: set[str] = set()

        def append_event(event: dict[str, Any], status: str) -> None:
            event_id = str(event.get("event_id") or "")
            if not event_id or event_id in seen:
                return
            seen.add(event_id)
            payload = event.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            event_type = str(event.get("type") or "event")
            item: dict[str, Any] = {
                "eventId": event_id,
                "runId": str(event.get("run_id") or self.run_id),
                "roundId": str(event.get("round_id") or ""),
                "batchId": str(event.get("batch_id") or ""),
                "type": event_type,
                "status": status,
                "priority": int(event.get("priority") or 0),
                "createdAt": str(event.get("created_at") or ""),
            }
            if event_type == "guidance":
                item["preview"] = _preview(payload.get("text"))
                item["clientRequestId"] = str(
                    payload.get("client_request_id") or ""
                )
            elif event_type == "tool_result":
                item.update({
                    "toolCallId": str(payload.get("tool_call_id") or ""),
                    "toolName": str(payload.get("tool_name") or ""),
                    "preview": _preview(payload.get("result")),
                    "isError": bool(payload.get("is_error")),
                    "skipped": bool(payload.get("skipped")),
                })
            events.append(item)

        for _priority, _sequence, event in list(self._queue._queue):
            append_event(event, "queued")
        for event in self._guidance:
            append_event(event, "claimed")
        for event in self._pending_tool_results.values():
            append_event(event, "queued")
        events.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        return {
            "queueDepth": self._queue_length(),
            "pendingGuidance": self._guidance_pending_count,
            "guidanceAdmissionOpen": self._guidance_admission_open,
            "activeTasks": sum(1 for task in self._tasks if not task.done()),
            "persistenceTasks": sum(
                1 for task in self._persistence_tasks if not task.done()
            ),
            "closed": self._closed,
            "events": events[:40],
            "tools": sorted(
                self._live_tool_states.values(),
                key=lambda item: str(item.get("updatedAt") or ""),
                reverse=True,
            )[:24],
        }

    def _set_live_tool_state(
        self,
        tool_call_id: str,
        tool_name: str,
        state: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        previous = self._live_tool_states.get(str(tool_call_id)) or {}
        item: dict[str, Any] = {
            "toolCallId": str(tool_call_id),
            "toolName": str(tool_name),
            "state": str(state),
            "updatedAt": _now(),
        }
        visible_arguments = (
            arguments if arguments is not None else previous.get("arguments")
        )
        if isinstance(visible_arguments, dict):
            item["arguments"] = dict(visible_arguments)
        self._live_tool_states[str(tool_call_id)] = item

    def _telemetry_row(
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
    ) -> tuple[Any, ...]:
        """Materialize operational telemetry at the point where it occurs."""
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
        return (
            f"trace_{uuid4().hex}", self.session_id, self.run_id,
            self.round_id, str(batch_id), str(event_type),
            str(tool_call_id), str(tool_name), queue_length, duration_ms,
            tool_queue_wait_ms, tool_execution_ms, agent_wait_ms,
            result_wait_ms, result_queue_delay_ms,
            str(termination_reason),
            json.dumps(payload or {}, ensure_ascii=False), _now(),
        )

    def _record_events(self, rows: list[tuple[Any, ...]]) -> None:
        """Persist one ordered telemetry batch in a single transaction."""
        if not self.db_path or not rows:
            return
        with self._db_connection() as conn:
            conn.executemany(_TELEMETRY_INSERT, rows)

    def _persist(self, event: dict[str, Any]) -> bool | None:
        if not self.db_path:
            return True
        try:
            with self._db_connection() as conn:
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
            with self._db_connection() as conn:
                row = conn.execute(
                    "SELECT event_id FROM workbench_agent_inbox WHERE session_id=? AND dedupe_key=?",
                    (self.session_id, dedupe_key),
                ).fetchone()
            return str(row[0]) if row else ""
        except Exception:
            logger.exception("Failed to resolve duplicate Workbench inbox event")
            return ""

    def _existing_event(self, dedupe_key: str) -> dict[str, Any] | None:
        """Load the durable event represented by an idempotency key.

        The in-memory dedupe map only spans one Python process.  HTTP retries
        can arrive after a restart, so guidance must consult SQLite before it
        is delivered to the live agent; otherwise ``INSERT OR IGNORE`` notices
        the duplicate only after the second copy has already reached the queue.
        """
        if not self.db_path or not dedupe_key:
            return None
        try:
            with self._db_connection() as conn:
                row = conn.execute(
                    """
                    SELECT event_id, run_id, round_id, batch_id, event_type,
                           priority, dedupe_key, payload_json, created_at
                    FROM workbench_agent_inbox
                    WHERE session_id=? AND dedupe_key=?
                    """,
                    (self.session_id, dedupe_key),
                ).fetchone()
            if not row:
                return None
            try:
                payload = json.loads(str(row[7]) or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            return {
                "event_id": str(row[0]),
                "session_id": self.session_id,
                "run_id": str(row[1] or ""),
                "round_id": str(row[2] or ""),
                "batch_id": str(row[3] or ""),
                "type": str(row[4] or "event"),
                "priority": int(row[5] or 0),
                "dedupe_key": str(row[6] or ""),
                "payload": payload if isinstance(payload, dict) else {},
                "created_at": str(row[8] or ""),
            }
        except Exception:
            logger.exception("Failed to load duplicate Workbench inbox event")
            return None

    def _complete(self, event_id: str) -> None:
        if not self.db_path:
            return
        try:
            with self._db_connection() as conn:
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
            with self._db_connection() as conn:
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
            with self._db_connection() as conn:
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
                event = {
                    "event_id": str(row[0]),
                    "session_id": self.session_id,
                    "round_id": str(row[1] or ""),
                    "type": "guidance",
                    "priority": int(row[2] or 0),
                    "dedupe_key": str(row[3] or ""),
                    "payload": json.loads(str(row[4]) or "{}"),
                    "created_at": str(row[5] or ""),
                }
                dedupe_key = str(event.get("dedupe_key") or "")
                if dedupe_key:
                    self._live_dedupe_events[dedupe_key] = event
                self._enqueue_nowait(event)
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

    def _complete_live_event(self, event_id: str) -> None:
        """Acknowledge a live event without racing its background INSERT."""
        if event_id in self._live_events_persisting:
            self._live_events_completed_early.add(event_id)
            return
        self._complete(event_id)

    def _claim_live_event(self, event_id: str) -> None:
        """Claim guidance without waiting for its background INSERT."""
        if event_id in self._live_events_persisting:
            self._live_events_claimed_early.add(event_id)
            return
        self._claim(event_id)

    def _schedule_live_event_persistence(
        self, event: dict[str, Any]
    ) -> asyncio.Task[Any]:
        """Persist a live inbox event without putting SQLite in its wakeup path."""
        event_id = str(event["event_id"])
        event_type = str(event.get("type") or "event")
        self._live_events_persisting.add(event_id)

        async def persist() -> None:
            durable_event_id = event_id
            try:
                persisted = await asyncio.to_thread(self._persist, event)
                if persisted is False:
                    durable_event_id = await asyncio.to_thread(
                        self._existing_event_id, str(event.get("dedupe_key") or "")
                    ) or event_id
                    logger.info(
                        "Workbench inbox event already persisted; live event was still delivered "
                        "[session_id=%s event_type=%s event_id=%s durable_event_id=%s]",
                        self.session_id,
                        event_type,
                        event_id,
                        durable_event_id,
                    )
                elif persisted is None:
                    logger.warning(
                        "Workbench inbox persistence failed after live delivery "
                        "[session_id=%s event_type=%s event_id=%s]",
                        self.session_id,
                        event_type,
                        event_id,
                    )
                if event_id in self._live_events_completed_early and persisted is not None:
                    await asyncio.to_thread(self._complete, durable_event_id)
                elif self._closed and persisted is not None:
                    await asyncio.to_thread(
                        self._cancel_event,
                        durable_event_id,
                        self._termination_reason or "completed",
                    )
                elif event_id in self._live_events_claimed_early and persisted is not None:
                    await asyncio.to_thread(self._claim, durable_event_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Workbench inbox persistence failed after live delivery "
                    "[session_id=%s event_type=%s event_id=%s]",
                    self.session_id,
                    event_type,
                    event_id,
                )
            finally:
                self._live_events_persisting.discard(event_id)
                self._live_events_completed_early.discard(event_id)
                self._live_events_claimed_early.discard(event_id)

        task = asyncio.create_task(persist())
        self._persistence_tasks.add(task)
        task.add_done_callback(self._persistence_tasks.discard)
        return task

    def _record_event_background(self, event_type: str, **kwargs: Any) -> None:
        """Queue ordered telemetry without putting SQLite on the result path."""
        if not self.db_path:
            return
        try:
            self._telemetry_pending.append(self._telemetry_row(event_type, **kwargs))
        except Exception:
            logger.exception("Failed to prepare Workbench inbox telemetry event")
            return
        if len(self._telemetry_pending) >= _TELEMETRY_BATCH_MAX:
            self._telemetry_flush_signal.set()
        task = self._telemetry_flush_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self._telemetry_flush_loop())
        self._telemetry_flush_task = task
        self._persistence_tasks.add(task)

        def settled(done: asyncio.Task[None]) -> None:
            self._persistence_tasks.discard(done)
            if self._telemetry_flush_task is done:
                self._telemetry_flush_task = None

        task.add_done_callback(settled)

    async def _telemetry_flush_loop(self) -> None:
        while self._telemetry_pending:
            if len(self._telemetry_pending) < _TELEMETRY_BATCH_MAX:
                try:
                    await asyncio.wait_for(
                        self._telemetry_flush_signal.wait(),
                        timeout=_TELEMETRY_FLUSH_INTERVAL_SECONDS,
                    )
                except TimeoutError:
                    pass
            self._telemetry_flush_signal.clear()
            try:
                await self._flush_telemetry_batch()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to persist Workbench inbox telemetry batch")
                return

    async def _flush_telemetry_batch(self) -> None:
        async with self._telemetry_flush_lock:
            if not self._telemetry_pending:
                return
            batch = self._telemetry_pending
            self._telemetry_pending = []
            try:
                await asyncio.to_thread(self._record_events, batch)
            except asyncio.CancelledError:
                self._telemetry_pending = [*batch, *self._telemetry_pending]
                raise
            except Exception:
                self._telemetry_pending = [*batch, *self._telemetry_pending]
                raise

    async def _flush_telemetry(self) -> None:
        """Drain telemetry at a terminal boundary without waiting for the timer."""
        task = self._telemetry_flush_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            self._telemetry_flush_signal.set()
            await asyncio.gather(task, return_exceptions=True)
        if not self._telemetry_pending:
            return
        try:
            await self._flush_telemetry_batch()
        except Exception:
            logger.exception("Failed to flush Workbench inbox telemetry at close")

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
        dedupe_key = str(dedupe_key or "")
        if dedupe_key and dedupe_key in self._live_dedupe_events:
            existing = self._live_dedupe_events[dedupe_key]
            return {**existing, "duplicate": True}

        # Guidance is an accepted user command, not disposable telemetry.  Its
        # recoverable copy must exist before the running agent can act on it.
        # This also makes idempotency survive process restarts: a completed row
        # still wins over a retried client request id and is never re-enqueued.
        if event_type == "guidance" and dedupe_key and self.db_path:
            existing = await asyncio.to_thread(self._existing_event, dedupe_key)
            if existing is not None:
                self._live_dedupe_events[dedupe_key] = existing
                return {**existing, "duplicate": True}
        event = {
            "event_id": f"evt_{uuid4().hex}",
            "session_id": self.session_id,
            "round_id": str(round_id or self.round_id),
            "run_id": self.run_id,
            "batch_id": str(batch_id or ""),
            "type": str(event_type),
            "payload": dict(payload or {}),
            "priority": int(priority),
            "dedupe_key": dedupe_key,
            "created_at": _now(),
        }
        if event_type == "tool_result":
            # The live agent must be woken before optional durability work.
            # Tool jobs cannot be resumed after a crash, so a slow or unavailable
            # SQLite connection must not strand an already-completed tool call.
            if dedupe_key:
                self._live_dedupe_events[dedupe_key] = event
            self._enqueue_nowait(event)
            self._schedule_live_event_persistence(event)
            return event

        persisted = await asyncio.to_thread(self._persist, event)
        if persisted is False:
            # A concurrent request/process may have inserted the idempotency key
            # after the preflight read. Return that durable event without ever
            # delivering this second copy.
            if dedupe_key:
                existing = await asyncio.to_thread(self._existing_event, dedupe_key)
                if existing is not None:
                    self._live_dedupe_events[dedupe_key] = existing
                    return {**existing, "duplicate": True}
            raise RuntimeError("Failed to persist Workbench inbox event")
        if persisted is None:
            raise RuntimeError("Failed to persist Workbench inbox event")
        if dedupe_key:
            self._live_dedupe_events[dedupe_key] = event
        if event_type == "guidance":
            self._guidance_pending_count += 1
            self._guidance_signal.set()
        await self._queue.put(self._queue_item(event))
        return event

    async def put_guidance(
        self,
        text: str,
        *,
        client_request_id: str = "",
        public_message_id: str = "",
        public_created_at: str = "",
    ) -> dict[str, Any]:
        dedupe_key = f"guidance:{client_request_id}" if client_request_id else ""
        async with self._guidance_admission_lock:
            if self._closed or self._closing or not self._guidance_admission_open:
                # Preserve idempotency for an acknowledgement whose response was
                # lost just before the run sealed its admission window.
                existing = self._live_dedupe_events.get(dedupe_key) if dedupe_key else None
                if existing is None and dedupe_key and self.db_path:
                    existing = await asyncio.to_thread(self._existing_event, dedupe_key)
                if existing is not None:
                    return {**existing, "duplicate": True}
                raise GuidanceAdmissionClosed(
                    "Workbench agent inbox is no longer accepting guidance"
                )
            event = await self.put(
                "guidance",
                {
                    "text": str(text).strip(),
                    "client_request_id": str(client_request_id),
                    "public_message_id": str(public_message_id),
                    "public_created_at": str(public_created_at),
                },
                priority=100,
                dedupe_key=dedupe_key,
            )
        if not event.get("duplicate"):
            self._record_event_background(
                "guidance_queued",
                payload={"event_id": event["event_id"], "client_request_id": client_request_id},
            )
        return event

    async def wait_for_guidance(self) -> bool:
        """Wake a model wait as soon as durable user guidance is available."""
        while True:
            if self._guidance_signal.is_set():
                return self.has_guidance_nowait()
            if self._closed or self._closing or not self._guidance_admission_open:
                return False
            await self._guidance_signal.wait()

    async def collect_guidance_or_seal(self) -> list[dict[str, Any]]:
        """Atomically collect pending guidance or close the admission window.

        A concurrent ``put_guidance`` holds the same lock across its durable
        INSERT and live enqueue, so an accepted command can never land after an
        empty terminal check.
        """
        async with self._guidance_admission_lock:
            events = self.collect_guidance_nowait()
            if not events:
                self._guidance_admission_open = False
            return events

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
        tool_span = trace_span(
            "tool",
            tool_name,
            span_id=tool_call_id,
            attributes={
                "batch_id": batch_id,
                "queue_wait_ms": (started - submitted_at) * 1000,
            },
        ).start()
        self._set_live_tool_state(tool_call_id, tool_name, "running")
        self._record_event_background(
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
                "is_error": _tool_result_is_error(result),
            }
        except asyncio.CancelledError:
            payload = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "result": localized(
                    "The tool was cancelled because the chat run was interrupted.",
                    "聊天运行被中断，工具已取消。",
                ),
                "is_error": True,
            }
            duration_ms = (time.perf_counter() - started) * 1000
            event = await self.put(
                "tool_result", payload, batch_id=batch_id,
                dedupe_key=f"tool-result:{tool_call_id}"
            )
            self._set_live_tool_state(tool_call_id, tool_name, "ready")
            self._result_queued_at[tool_call_id] = time.perf_counter()
            self._record_event_background(
                "tool_result_queued", batch_id=batch_id,
                tool_call_id=tool_call_id, tool_name=tool_name,
                duration_ms=duration_ms, tool_execution_ms=duration_ms,
                payload={"event_id": event["event_id"], "cancelled": True},
            )
            tool_span.set_attribute("result_chars", len(payload["result"]))
            await tool_span.finish(status="cancelled")
            raise
        except Exception:
            logger.exception("Workbench tool %s failed", tool_name)
            payload = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "result": localized(
                    "The tool failed.",
                    "工具执行失败。",
                ),
                "is_error": True,
            }
        duration_ms = (time.perf_counter() - started) * 1000
        event = await self.put(
            "tool_result", payload, batch_id=batch_id,
            dedupe_key=f"tool-result:{tool_call_id}"
        )
        self._set_live_tool_state(tool_call_id, tool_name, "ready")
        self._result_queued_at[tool_call_id] = time.perf_counter()
        self._record_event_background(
            "tool_result_queued", batch_id=batch_id,
            tool_call_id=tool_call_id, tool_name=tool_name,
            duration_ms=duration_ms, tool_execution_ms=duration_ms,
            payload={"event_id": event["event_id"], "is_error": payload["is_error"]},
        )
        tool_span.set_attribute("result_chars", len(payload["result"]))
        await tool_span.finish(status="error" if payload["is_error"] else "ok")

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
        visible_arguments = (
            metadata.get("arguments") if isinstance(metadata, dict) else None
        )
        self._set_live_tool_state(
            tool_call_id,
            tool_name,
            "queued",
            arguments=visible_arguments if isinstance(visible_arguments, dict) else None,
        )
        self._record_event_background(
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
            arguments=(
                dict(metadata.get("arguments"))
                if isinstance(metadata.get("arguments"), dict)
                else None
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
                "result": localized(
                    "Skipped before execution because new user guidance superseded this tool-call batch.",
                    "新的用户指导已取代此工具调用批次，因此该工具在执行前被跳过。",
                ),
                "is_error": False,
                "skipped": True,
            },
            batch_id=batch_id,
            dedupe_key=f"tool-result:{call.tool_call_id}",
        )
        self._set_live_tool_state(call.tool_call_id, call.tool_name, "ready")
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
            raise RuntimeError(localized(
                "The Workbench Agent inbox is closed.",
                "Workbench Agent 收件箱已关闭。",
            ))
        batch_id = str(batch_id or f"batch_{uuid4().hex}")
        normalized = [self._normalize_batch_call(call) for call in calls]
        for call in normalized:
            self._tool_submitted_at[call.tool_call_id] = time.perf_counter()
            self._set_live_tool_state(
                call.tool_call_id,
                call.tool_name,
                "queued",
                arguments=call.arguments,
            )
            self._record_event_background(
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
        consume_span = trace_span(
            "tool_consume",
            "wait_for_tool_result",
            span_id=f"{tool_call_id}.consume",
        ).start()
        while True:
            event = self._pending_tool_results.pop(tool_call_id, None)
            if event is None:
                _priority, _sequence, event = await self._queue.get()
            event_type = str(event.get("type") or "")
            if event_type == "guidance":
                self._guidance.append(event)
                self._claim_live_event(str(event["event_id"]))
                self._record_event_background(
                    "guidance_claimed", payload={"event_id": event["event_id"]}
                )
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if event_type == "tool_result" and str(payload.get("tool_call_id") or "") == tool_call_id:
                self._complete_live_event(str(event["event_id"]))
                self._set_live_tool_state(
                    tool_call_id, str(payload.get("tool_name") or ""), "consumed"
                )
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
                consume_span.set_attribute(
                    "result_queue_delay_ms", queue_delay_ms
                )
                await consume_span.finish()
                return str(payload.get("result") or "")
            if event_type == "tool_result":
                unexpected_id = str(payload.get("tool_call_id") or "")
                if unexpected_id:
                    self._pending_tool_results[unexpected_id] = event
                    continue
            # Retain future event types rather than acknowledging or dropping.
            await self._queue.put(self._queue_item(event))
            await asyncio.sleep(0)

    async def wait_for_active_tools(self) -> None:
        """Wait for already-submitted tool work without cancelling or starting work.

        A terminal model response must not discard a tool that is already queued
        or running. Batch tasks remain active until
        all of their child tool runners finish, so waiting on this set covers
        both single calls and concurrent batches.
        """
        while True:
            active = [task for task in self._tasks if not task.done()]
            if not active:
                return
            await asyncio.gather(*active, return_exceptions=True)

    def collect_guidance_nowait(self) -> list[dict[str, Any]]:
        while True:
            try:
                _priority, _sequence, event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if str(event.get("type") or "") == "guidance":
                self._guidance.append(event)
                self._claim_live_event(str(event["event_id"]))
                self._record_event_background(
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
                self._claim_live_event(str(event["event_id"]))
                self._record_event_background(
                    "guidance_claimed", payload={"event_id": event["event_id"]}
                )
            else:
                self._enqueue_nowait(event)
                break
        return bool(self._guidance)

    def acknowledge(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            self._complete_live_event(str(event.get("event_id") or ""))
            if str(event.get("type") or "") == "guidance":
                self._guidance_pending_count = max(0, self._guidance_pending_count - 1)
                self._record_event_background(
                    "guidance_applied", payload={"event_id": event.get("event_id", "")}
                )
        if self._guidance_pending_count == 0:
            self._guidance_signal.clear()

    def _cancel_pending(self, termination_reason: str) -> None:
        if not self.db_path:
            return
        try:
            with self._db_connection() as conn:
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
            with self._db_connection() as conn:
                conn.execute(
                    "UPDATE workbench_agent_inbox SET status='cancelled', completed_at=?, "
                    "termination_reason=? WHERE event_id=? AND session_id=? "
                    "AND status IN ('queued','claimed')",
                    (_now(), termination_reason, str(event_id), self.session_id),
                )
        except Exception:
            logger.exception("Failed to clean Workbench inbox event %s", event_id)

    async def close(self, *, termination_reason: str = "completed") -> None:
        async with self._guidance_admission_lock:
            if self._closed or self._closing:
                return
            self._closing = True
            self._guidance_admission_open = False
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
            if pending:
                cancelled, _still_pending = await asyncio.wait(pending, timeout=0.1)
                if cancelled:
                    await asyncio.gather(*cancelled, return_exceptions=True)
        self._tasks.clear()
        self._closed = True
        self._closing = False
        self._run_persistence_background(self._cancel_pending, self._termination_reason)
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._guidance.clear()
        self._pending_tool_results.clear()
        for item in self._live_tool_states.values():
            if str(item.get("state") or "") in {"queued", "running", "ready"}:
                item["state"] = "cancelled"
                item["updatedAt"] = _now()
        self._guidance_pending_count = 0
        self._guidance_signal.clear()
        self._record_event_background(
            "run_terminated", termination_reason=self._termination_reason
        )
        await self._flush_telemetry()
        persistence = [
            task for task in self._persistence_tasks
            if task is not asyncio.current_task() and not task.done()
        ]
        if persistence:
            done, pending = await asyncio.wait(persistence, timeout=2.0)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)


def _inbox_payload(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _preview(value: Any, limit: int = 600) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def read_workbench_inbox_snapshot(
    db_path: str,
    session_id: str,
    *,
    run_id: str = "",
    limit: int = 40,
) -> dict[str, Any]:
    """Read one conversation's latest inbox run for the diagnostic UI.

    This is deliberately read-only and safe to call via ``asyncio.to_thread``.
    Payloads are reduced to short user/tool previews so a large tool result
    cannot bloat the sidebar response.
    """
    session_id = str(session_id or "")
    selected_run_id = str(run_id or "")
    empty = {
        "sessionId": session_id,
        "runId": selected_run_id,
        "counts": {
            "queued": 0,
            "claimed": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "total": 0,
        },
        "events": [],
        "tools": [],
        "updatedAt": "",
    }
    if not db_path or not session_id:
        return empty
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        return empty
    try:
        with sqlite3.connect(str(path), timeout=5) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            if not selected_run_id:
                row = conn.execute(
                    """
                    SELECT run_id FROM (
                        SELECT run_id, created_at FROM workbench_agent_inbox
                        WHERE session_id=? AND run_id<>''
                        UNION ALL
                        SELECT run_id, created_at FROM workbench_agent_run_events
                        WHERE session_id=? AND run_id<>''
                    ) ORDER BY created_at DESC LIMIT 1
                    """,
                    (session_id, session_id),
                ).fetchone()
                selected_run_id = str(row[0]) if row else ""

            where = "session_id=?"
            params: list[Any] = [session_id]
            if selected_run_id:
                where += " AND run_id=?"
                params.append(selected_run_id)

            counts = dict(empty["counts"])
            for status, count in conn.execute(
                f"SELECT status, COUNT(*) FROM workbench_agent_inbox WHERE {where} GROUP BY status",
                params,
            ).fetchall():
                key = str(status or "")
                if key:
                    counts[key] = int(count or 0)
            counts["total"] = sum(
                int(value or 0) for key, value in counts.items() if key != "total"
            )

            rows = conn.execute(
                f"""
                SELECT event_id, run_id, round_id, batch_id, event_type, status,
                       priority, payload_json, created_at, completed_at,
                       termination_reason
                FROM workbench_agent_inbox WHERE {where}
                ORDER BY created_at DESC LIMIT ?
                """,
                [*params, max(1, min(int(limit or 40), 100))],
            ).fetchall()
            events: list[dict[str, Any]] = []
            for row in rows:
                payload = _inbox_payload(row[7])
                event_type = str(row[4] or "event")
                item: dict[str, Any] = {
                    "eventId": str(row[0]),
                    "runId": str(row[1] or ""),
                    "roundId": str(row[2] or ""),
                    "batchId": str(row[3] or ""),
                    "type": event_type,
                    "status": str(row[5] or ""),
                    "priority": int(row[6] or 0),
                    "createdAt": str(row[8] or ""),
                    "completedAt": str(row[9] or ""),
                    "terminationReason": str(row[10] or ""),
                }
                if event_type == "guidance":
                    item["preview"] = _preview(payload.get("text"))
                    item["clientRequestId"] = str(
                        payload.get("client_request_id") or ""
                    )
                elif event_type == "tool_result":
                    item.update({
                        "toolCallId": str(payload.get("tool_call_id") or ""),
                        "toolName": str(payload.get("tool_name") or ""),
                        "preview": _preview(payload.get("result")),
                        "isError": bool(payload.get("is_error")),
                        "skipped": bool(payload.get("skipped")),
                    })
                events.append(item)

            trace_rows = conn.execute(
                f"""
                SELECT event_type, tool_call_id, tool_name, created_at
                FROM workbench_agent_run_events
                WHERE {where} AND tool_call_id<>''
                ORDER BY created_at
                """,
                params,
            ).fetchall()
            tool_states: dict[str, dict[str, Any]] = {}
            state_map = {
                "tool_submitted": "queued",
                "tool_started": "running",
                "tool_result_queued": "ready",
                "tool_result_consumed": "consumed",
            }
            for event_type, tool_call_id, tool_name, created_at in trace_rows:
                call_id = str(tool_call_id or "")
                if not call_id:
                    continue
                tool_states[call_id] = {
                    "toolCallId": call_id,
                    "toolName": str(tool_name or ""),
                    "state": state_map.get(str(event_type or ""), str(event_type or "")),
                    "updatedAt": str(created_at or ""),
                }
            tools = sorted(
                tool_states.values(), key=lambda item: item["updatedAt"], reverse=True
            )[:24]
            timestamps = [
                str(item.get("completedAt") or item.get("createdAt") or "")
                for item in events
            ] + [str(item.get("updatedAt") or "") for item in tools]
            return {
                "sessionId": session_id,
                "runId": selected_run_id,
                "counts": counts,
                "events": events,
                "tools": tools,
                "updatedAt": max((stamp for stamp in timestamps if stamp), default=""),
            }
    except sqlite3.OperationalError as exc:
        # Older/empty databases may not have the inspector tables yet.
        if "no such table" not in str(exc).lower():
            logger.exception("Failed to inspect Workbench inbox for %s", session_id)
        return empty
    except Exception:
        logger.exception("Failed to inspect Workbench inbox for %s", session_id)
        return empty


def read_workbench_guidance_records(db_path: str) -> list[dict[str, Any]]:
    """Return guidance rows carrying enough metadata to repair the transcript."""
    if not db_path:
        return []
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        return []
    try:
        with sqlite3.connect(str(path), timeout=5) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            rows = conn.execute(
                """
                SELECT session_id, event_id, run_id, payload_json, created_at
                FROM workbench_agent_inbox
                WHERE event_type='guidance'
                  AND payload_json LIKE '%public_message_id%'
                ORDER BY created_at
                """
            ).fetchall()
        records: list[dict[str, Any]] = []
        for session_id, event_id, run_id, payload_json, created_at in rows:
            payload = _inbox_payload(payload_json)
            message_id = str(payload.get("public_message_id") or "").strip()
            text = str(payload.get("text") or "").strip()
            if not message_id or not text:
                continue
            records.append({
                "sessionId": str(session_id or ""),
                "eventId": str(event_id or ""),
                "runId": str(run_id or ""),
                "messageId": message_id,
                "clientRequestId": str(payload.get("client_request_id") or ""),
                "content": text,
                "createdAt": str(payload.get("public_created_at") or created_at or ""),
            })
        return records
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            logger.exception("Failed to read Workbench guidance records")
        return []
    except Exception:
        logger.exception("Failed to read Workbench guidance records")
        return []


_workbench_agent_inbox: ContextVar[WorkbenchAgentInbox | None] = ContextVar(
    "_workbench_agent_inbox", default=None
)


def current_workbench_inbox() -> WorkbenchAgentInbox | None:
    return _workbench_agent_inbox.get()


__all__ = [
    "GuidanceAdmissionClosed",
    "WorkbenchAgentInbox",
    "_workbench_agent_inbox",
    "current_workbench_inbox",
    "read_workbench_guidance_records",
    "read_workbench_inbox_snapshot",
]
