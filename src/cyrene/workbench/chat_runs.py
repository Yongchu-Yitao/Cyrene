"""Process-level registry of in-flight Workbench *conversation* runs.

The normal Workbench chat path used to run the agent **inside** the HTTP
streaming request (``asyncio.create_task(_run())`` whose ``finally`` called
``task.cancel()``). The moment the client disconnected — a network blip, the
laptop sleeping, a closed tab, a server restart — the generator's ``finally``
fired and cancelled the agent before it could persist its reply. The exchange
was lost, while tool side effects (written files, etc.) were half-applied.

This module decouples the run from the request, mirroring the durability
pattern of :mod:`cyrene.workbench.goal_loop` (its ``GoalLoopManager.tasks``
registry, background ``asyncio.Task`` ownership, lifecycle hooks) but scoped to
the conversation path:

* The agent runs as a **background task owned by the registry**, not the
  request. When the HTTP request ends, the task is *not* cancelled.
* The run **always finalizes** (persists the assistant reply to
  ``workbench_chats.json``) when the agent completes, whether or not a client
  is still attached.
* Each run keeps an **append-only event log / ring buffer** so a reconnecting
  client can replay the events it missed while disconnected (``ack`` /
  ``intermediate_message`` / ``reasoning_*`` / ``reply_start`` /
  ``reply_delta`` / ``reply_done`` / ``run_finalizing`` / ``awaiting_user`` /
  ``saved`` / ``error``) and then join the live stream.

Unlike the goal loop, the model-run checkpoint and replay buffer remain
in-memory for this single bounded exchange. The *result* is made durable by the
finalize callback (``workbench_chats.json``). Run-scoped inbox events are stored
in SQLite, however, so accepted guidance is session-isolated, idempotent, and
recoverable if the process stops before the agent applies it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Awaitable, Callable
from uuid import uuid4

from cyrene.observability.trace import trace_span
from cyrene.workbench.compat import chat_service

logger = logging.getLogger(__name__)

# How long an attached stream blocks on the queue before re-checking whether the
# run has finished. Small enough that termination is snappy, large enough that an
# idle attached client is not a busy-loop.
_STREAM_POLL_SECONDS = 0.25
# Upper bound on buffered events for one run. A single reply streamed token by
# token can emit thousands of ``reply_delta`` events; this caps memory while a
# run is live. On overflow the OLDEST non-ack events are dropped — a reconnect
# from before the trim still converges because ``reply_done`` carries the full
# text and ``saved`` carries the persisted messages. For a normal exchange the
# buffer never trims.
_MAX_BUFFER_EVENTS = 6000
# Keep a finished run in the registry this long so a client that reconnects just
# after completion can still replay the terminal events (``saved`` etc.) before
# falling back to a transcript re-pull.
_RETENTION_SECONDS = 45.0
# On graceful shutdown, wait up to this long for in-flight runs to finalize
# before cancelling them (so a planned restart still persists replies).
_SHUTDOWN_GRACE_SECONDS = 20.0
_DURABLE_RETENTION_DAYS = 7
# High-frequency model deltas stay individually cursor-addressable, but their
# SQLite work is grouped into one connection/transaction. This removes database
# backpressure from the upstream token loop without changing replay semantics.
# Streaming deltas are still delivered to attached clients immediately. Their
# durable copies can share a slightly wider transaction window, substantially
# reducing SQLite writer pressure during fast token streams while terminal
# events continue to force an immediate flush.
_DURABLE_EVENT_BATCH_INTERVAL_SECONDS = 1.0
_DURABLE_EVENT_BATCH_MAX = 512
_BATCHABLE_DURABLE_EVENT_TYPES = frozenset({
    "reasoning_delta",
    "reply_delta",
    "message.delta",
    "reasoning.delta",
    "tool.updated",
    "usage.updated",
    "artifact.updated",
})

# Event types that suppress the synthesized reply (the agent already streamed a
# real reply). Mirrors the legacy generator's ``startswith("reply_")`` check.
_REPLY_EVENT_PREFIX = "reply_"

# A runner is the per-send coroutine supplied by the route layer. It runs the
# agent, finalizes, and publishes terminal events via ``run.publish``.
Runner = Callable[["ChatRun"], Awaitable[None]]


def _ndjson_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


class ChatRunEventStore:
    """SQLite-backed run metadata and cursor-addressable event history."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path or "")
        self._lock = threading.RLock()
        if self.db_path:
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        # Same busy_timeout as cyrene.workbench.store: the event store shares
        # the main SQLite file with document writers, so a shorter timeout
        # here turned lock contention into hard event loss during finalize.
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS workbench_chat_runs (
                    run_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    termination_reason TEXT NOT NULL DEFAULT '',
                    outcome_kind TEXT NOT NULL DEFAULT '',
                    last_seq INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_workbench_chat_runs_chat
                    ON workbench_chat_runs(chat_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS workbench_chat_run_events (
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, seq),
                    FOREIGN KEY(run_id) REFERENCES workbench_chat_runs(run_id)
                        ON DELETE CASCADE
                );
                """
            )
            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(days=_DURABLE_RETENTION_DAYS)
            ).isoformat()
            expired = [
                str(row["run_id"])
                for row in conn.execute(
                    """
                    SELECT run_id FROM workbench_chat_runs
                    WHERE completed_at != '' AND completed_at < ?
                    """,
                    (cutoff,),
                ).fetchall()
            ]
            if expired:
                conn.executemany(
                    "DELETE FROM workbench_chat_run_events WHERE run_id = ?",
                    [(run_id,) for run_id in expired],
                )
                conn.executemany(
                    "DELETE FROM workbench_chat_runs WHERE run_id = ?",
                    [(run_id,) for run_id in expired],
                )

    def create(self, run: "ChatRun") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO workbench_chat_runs(
                    run_id, chat_id, status, created_at, last_seq
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.chat_id,
                    run.status,
                    run.created_at,
                    run.seq,
                ),
            )
            for event in run.events:
                self._append_locked(conn, run.run_id, event)

    def append(self, run_id: str, event: dict[str, Any]) -> None:
        self.append_many(run_id, [event])

    def append_many(
        self,
        run_id: str,
        events: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> None:
        """Persist a cursor-preserving event batch in one transaction."""
        if not events:
            return
        with self._lock, self._connect() as conn:
            rows = [
                (
                    str(run_id),
                    int(event.get("_seq") or 0),
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                    datetime.now(timezone.utc).isoformat(),
                )
                for event in events
            ]
            conn.executemany(
                """
                INSERT OR REPLACE INTO workbench_chat_run_events(
                    run_id, seq, event_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            last_seq = max(row[1] for row in rows)
            conn.execute(
                """
                UPDATE workbench_chat_runs
                SET last_seq = CASE WHEN last_seq < ? THEN ? ELSE last_seq END
                WHERE run_id = ?
                """,
                (last_seq, last_seq, str(run_id)),
            )

    @staticmethod
    def _append_locked(
        conn: sqlite3.Connection,
        run_id: str,
        event: dict[str, Any],
    ) -> None:
        seq = int(event.get("_seq") or 0)
        conn.execute(
            """
            INSERT OR REPLACE INTO workbench_chat_run_events(
                run_id, seq, event_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                str(run_id),
                seq,
                json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.execute(
            """
            UPDATE workbench_chat_runs
            SET last_seq = CASE WHEN last_seq < ? THEN ? ELSE last_seq END
            WHERE run_id = ?
            """,
            (seq, seq, str(run_id)),
        )

    def finalize(self, run: "ChatRun") -> None:
        outcome = run.outcome if isinstance(run.outcome, dict) else {}
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE workbench_chat_runs
                SET status = ?, completed_at = ?, termination_reason = ?,
                    outcome_kind = ?, last_seq = ?
                WHERE run_id = ?
                """,
                (
                    run.status,
                    datetime.now(timezone.utc).isoformat(),
                    run.termination_reason,
                    str(outcome.get("kind") or ""),
                    run.seq,
                    run.run_id,
                ),
            )

    def delete_chat(self, chat_id: str) -> None:
        """Remove durable replay state after its owning chat is deleted."""
        with self._lock, self._connect() as conn:
            run_ids = [
                str(row["run_id"])
                for row in conn.execute(
                    "SELECT run_id FROM workbench_chat_runs WHERE chat_id = ?",
                    (str(chat_id),),
                ).fetchall()
            ]
            if run_ids:
                conn.executemany(
                    "DELETE FROM workbench_chat_run_events WHERE run_id = ?",
                    [(run_id,) for run_id in run_ids],
                )
            conn.execute(
                "DELETE FROM workbench_chat_runs WHERE chat_id = ?",
                (str(chat_id),),
            )

    def recover_interrupted(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, last_seq FROM workbench_chat_runs
                WHERE completed_at = ''
                """
            ).fetchall()
            for row in rows:
                event = {
                    "_seq": int(row["last_seq"]) + 1,
                    "runId": str(row["run_id"]),
                    "type": "error",
                    "code": "process_restarted",
                    "message": "The Cyrene process restarted before this run completed.",
                }
                self._append_locked(conn, str(row["run_id"]), event)
                conn.execute(
                    """
                    UPDATE workbench_chat_runs
                    SET status = 'error', completed_at = ?,
                        termination_reason = 'process_restarted',
                        outcome_kind = 'error'
                    WHERE run_id = ?
                    """,
                    (now, str(row["run_id"])),
                )
            return len(rows)

    def load_by_run_id(self, run_id: str) -> "ChatRun | None":
        return self._load("run_id = ?", (str(run_id),))

    def load_latest_for_chat(self, chat_id: str) -> "ChatRun | None":
        return self._load(
            "chat_id = ?",
            (str(chat_id),),
            order_by="created_at DESC",
        )

    def _load(
        self,
        where: str,
        args: tuple[Any, ...],
        *,
        order_by: str = "created_at DESC",
    ) -> "ChatRun | None":
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT run_id, chat_id, status, created_at, completed_at,
                       termination_reason, outcome_kind, last_seq
                FROM workbench_chat_runs
                WHERE {where}
                ORDER BY {order_by}
                LIMIT 1
                """,
                args,
            ).fetchone()
            if row is None:
                return None
            event_rows = conn.execute(
                """
                SELECT event_json FROM workbench_chat_run_events
                WHERE run_id = ? ORDER BY seq
                """,
                (str(row["run_id"]),),
            ).fetchall()
        events = []
        for event_row in event_rows:
            try:
                event = json.loads(str(event_row["event_json"]))
            except Exception:
                continue
            if isinstance(event, dict):
                events.append(event)
        return ChatRun.restore(
            run_id=str(row["run_id"]),
            chat_id=str(row["chat_id"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            termination_reason=str(row["termination_reason"]),
            outcome_kind=str(row["outcome_kind"]),
            last_seq=int(row["last_seq"]),
            events=events,
            completed=bool(row["completed_at"]),
        )


class ChatRun:
    """One in-flight conversation exchange and its replayable event buffer."""

    def __init__(self, chat_id: str, ack_event: dict[str, Any], *, max_buffer: int = _MAX_BUFFER_EVENTS, db_path: str = "") -> None:
        from cyrene.workbench.inbox import WorkbenchAgentInbox

        self.chat_id = str(chat_id)
        self.run_id = f"run_{uuid4().hex}"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self._event_store: ChatRunEventStore | None = None
        self._event_store_pending: list[dict[str, Any]] = []
        self._event_store_flush_lock = asyncio.Lock()
        self._event_store_flush_task: asyncio.Task[None] | None = None
        self.inbox = WorkbenchAgentInbox(
            self.chat_id, db_path=db_path, run_id=self.run_id
        )
        self.max_buffer = int(max_buffer)
        self.seq = 1
        # Event 1 is always the ack so a fresh attach (cursor 0) replays the
        # whole exchange from the top.
        self.events: list[dict[str, Any]] = [
            {"_seq": 1, "runId": self.run_id, **dict(ack_event)}
        ]
        self.subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self.done = asyncio.Event()
        self.ready = asyncio.Event()
        self.task: asyncio.Task[Any] | None = None
        self.saw_reply_events = False
        self.status = "running"
        self.termination_reason = ""
        # Result for non-streaming callers, set by the runner:
        #   {"kind": "reply", "payload": {...}}    — assistant reply persisted
        #   {"kind": "awaiting", "pending": {...}} — paused for an answer
        #   {"kind": "error", "exc": Exception}    — run failed
        self.outcome: dict[str, Any] | None = None

    @classmethod
    def restore(
        cls,
        *,
        run_id: str,
        chat_id: str,
        status: str,
        created_at: str,
        termination_reason: str,
        outcome_kind: str,
        last_seq: int,
        events: list[dict[str, Any]],
        completed: bool,
    ) -> "ChatRun":
        """Rehydrate a completed/crash-recovered run without creating an inbox."""
        run = cls.__new__(cls)
        run.chat_id = str(chat_id)
        run.run_id = str(run_id)
        run.created_at = str(created_at)
        run._event_store = None
        run._event_store_pending = []
        run._event_store_flush_lock = asyncio.Lock()
        run._event_store_flush_task = None
        run.inbox = None
        run.max_buffer = max(_MAX_BUFFER_EVENTS, len(events))
        run.seq = int(last_seq)
        run.events = list(events)
        run.subscribers = set()
        run.done = asyncio.Event()
        run.ready = asyncio.Event()
        run.ready.set()
        if completed:
            run.done.set()
        run.task = None
        run.saw_reply_events = any(
            str(event.get("type") or "").startswith(_REPLY_EVENT_PREFIX)
            for event in events
        )
        run.status = str(status)
        run.termination_reason = str(termination_reason)
        run.outcome = {"kind": str(outcome_kind)} if outcome_kind else None
        return run

    async def configure_event_store(self, store: ChatRunEventStore) -> None:
        self._event_store = store
        await asyncio.to_thread(store.create, self)

    def _schedule_event_store_flush(self) -> None:
        task = self._event_store_flush_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self._flush_event_store_after_delay())
        self._event_store_flush_task = task

        def _settled(done: asyncio.Task[None]) -> None:
            if self._event_store_flush_task is done:
                self._event_store_flush_task = None
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "Failed to batch durable chat events for run %s",
                    self.run_id,
                )

        task.add_done_callback(_settled)

    async def _flush_event_store_after_delay(self) -> None:
        await asyncio.sleep(_DURABLE_EVENT_BATCH_INTERVAL_SECONDS)
        await self._flush_event_store_now()

    async def _flush_event_store_now(self) -> None:
        store = self._event_store
        if store is None:
            self._event_store_pending.clear()
            return
        async with self._event_store_flush_lock:
            if not self._event_store_pending:
                return
            batch = self._event_store_pending
            self._event_store_pending = []
            try:
                await asyncio.to_thread(store.append_many, self.run_id, batch)
            except asyncio.CancelledError:
                # ``to_thread`` work may still commit after its awaiter is
                # cancelled. Re-queueing is safe because writes are idempotent
                # by (run_id, seq), and guarantees a terminal flush cannot race
                # past an in-flight batch.
                self._event_store_pending = [*batch, *self._event_store_pending]
                raise
            except Exception:
                # Preserve order for a later terminal flush/retry.
                self._event_store_pending = [*batch, *self._event_store_pending]
                raise

    async def flush_event_store(self) -> None:
        """Flush queued durable events before terminal/finalize boundaries."""
        scheduled = self._event_store_flush_task
        current = asyncio.current_task()
        if scheduled is not None and scheduled is not current and not scheduled.done():
            scheduled.cancel()
            await asyncio.gather(scheduled, return_exceptions=True)
        if self._event_store_flush_task is scheduled:
            self._event_store_flush_task = None
        await self._flush_event_store_now()

    async def publish(self, event: dict[str, Any]) -> None:
        """Append an event to the buffer and fan it out to attached clients.

        Used both as the agent's ``_reply_stream_writer`` (so the agent's own
        ``reply_*`` / ``intermediate_message`` events are captured) and directly
        by the runner for terminal events. Awaitable but never blocks.
        """
        if str(event.get("type") or "") == "intermediate_message" and isinstance(event.get("message"), dict):
            try:
                await asyncio.to_thread(
                    chat_service()._persist_live_public_message,
                    self.chat_id,
                    event["message"],
                )
            except Exception:
                logger.exception("Failed to checkpoint intermediate chat message for %s", self.chat_id)
        self.seq += 1
        stored = {"_seq": self.seq, "runId": self.run_id, **dict(event)}
        self.events.append(stored)
        if self._event_store is not None:
            self._event_store_pending.append(stored)
            event_type = str(event.get("type") or "")
            if (
                event_type not in _BATCHABLE_DURABLE_EVENT_TYPES
                or len(self._event_store_pending) >= _DURABLE_EVENT_BATCH_MAX
            ):
                await self.flush_event_store()
            else:
                self._schedule_event_store_flush()
        if len(self.events) > self.max_buffer:
            # Keep the ack (events[0]); drop the oldest events after it.
            overflow = len(self.events) - self.max_buffer
            del self.events[1:1 + overflow]
        if str(event.get("type") or "").startswith(_REPLY_EVENT_PREFIX):
            self.saw_reply_events = True
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(stored)
            except Exception:
                pass


class ChatRunManager:
    """Owns background conversation runs so they outlive their HTTP requests."""

    def __init__(
        self,
        *,
        max_buffer: int = _MAX_BUFFER_EVENTS,
        retention_seconds: float = _RETENTION_SECONDS,
        shutdown_grace_seconds: float = _SHUTDOWN_GRACE_SECONDS,
    ) -> None:
        self.runs: dict[str, ChatRun] = {}
        self.closed = False
        self._max_buffer = int(max_buffer)
        self._retention_seconds = float(retention_seconds)
        self._shutdown_grace_seconds = float(shutdown_grace_seconds)
        self._cleanup_tasks: set[asyncio.Task[Any]] = set()
        self._db_path = ""
        self._event_store: ChatRunEventStore | None = None

    def configure(self, db_path: str) -> None:
        """Configure durable inbox and run-event storage before runs start."""
        self._db_path = str(db_path or "")
        self._event_store = (
            ChatRunEventStore(self._db_path) if self._db_path else None
        )

    def get(self, chat_id: str) -> ChatRun | None:
        """Return only an actively running exchange."""
        run = self.runs.get(str(chat_id))
        if run is not None and run.done.is_set():
            return None
        return run

    def get_replayable(self, chat_id: str) -> ChatRun | None:
        """Return a retained run, including one that has just finished.

        Finished runs stay in ``runs`` for the retention window specifically so
        a reconnect can replay terminal events.  Keeping this separate from
        :meth:`get` prevents a completed exchange from blocking a new send.
        """
        run = self.runs.get(str(chat_id))
        if run is not None:
            return run
        if self._event_store is not None:
            return self._event_store.load_latest_for_chat(str(chat_id))
        return None

    def get_by_run_id(self, run_id: str) -> ChatRun | None:
        """Return an active run by its public run identifier."""
        target = str(run_id or "")
        for run in self.runs.values():
            if run.run_id == target and not run.done.is_set():
                return run
        return None

    def get_replayable_by_run_id(self, run_id: str) -> ChatRun | None:
        """Return a retained run, including one that has just finished."""
        target = str(run_id or "")
        for run in self.runs.values():
            if run.run_id == target:
                return run
        if self._event_store is not None:
            return self._event_store.load_by_run_id(target)
        return None

    def interrupt(self, chat_id: str) -> bool:
        """Cancel a live run and wake attached streams immediately."""
        run = self.get(chat_id)
        if run is None:
            return False
        run.status = "cancelled"
        run.termination_reason = "user_interrupted"
        run.outcome = {"kind": "interrupted"}
        if run.task is not None and not run.task.done():
            run.task.cancel()
        try:
            asyncio.create_task(run.publish({"type": "interrupted", "chatId": run.chat_id}))
        except RuntimeError:
            pass
        # Keep streams attached until the cancelled runner has closed its inbox
        # and persisted the terminal outcome. ``publish`` provides immediate UI
        # feedback; ``_drive`` sends the final wake after ``run.done`` is set.
        return True

    async def terminate(
        self,
        chat_id: str,
        *,
        termination_reason: str = "chat_deleted",
    ) -> bool:
        """Cancel, await, and forget a run before its chat record is deleted."""
        target = str(chat_id or "")
        run = self.runs.get(target)
        had_run = run is not None
        if run is not None:
            run.status = "cancelled"
            run.termination_reason = str(termination_reason or "chat_deleted")
            run.outcome = {"kind": "deleted"}
            task = run.task
            if task is not None and not task.done():
                task.cancel()
            if task is not None and task is not asyncio.current_task():
                await asyncio.gather(task, return_exceptions=True)
            # A task cancelled before its first event-loop turn never enters
            # ``_drive`` and therefore cannot execute its normal finalizer.
            # Close/wake explicitly so subscribers and inbox workers cannot
            # survive deletion in that narrow startup window.
            if not run.done.is_set():
                try:
                    await run.inbox.close(termination_reason=run.termination_reason)
                except Exception:
                    logger.exception("Failed to close deleted chat inbox %s", target)
                run.ready.set()
                run.done.set()
                for queue in list(run.subscribers):
                    try:
                        queue.put_nowait(None)
                    except Exception:
                        pass
            if self.runs.get(target) is run:
                self.runs.pop(target, None)
        if self._event_store is not None:
            try:
                await asyncio.to_thread(self._event_store.delete_chat, target)
            except Exception:
                # Durable replay cleanup must never resurrect or keep a live
                # agent merely because its old database became unavailable.
                logger.exception("Failed to delete durable run history for chat %s", target)
        return had_run

    def start_or_get(
        self,
        chat_id: str,
        ack_event: dict[str, Any],
        runner: Runner,
        *,
        stream: bool = True,
    ) -> tuple[ChatRun, bool]:
        """Start a background run for ``chat_id``, or return the live one.

        When a run is already in flight for this conversation (e.g. a double
        submit, or a reconnect that raced the original), the existing run is
        returned with ``is_new=False`` so the caller attaches to it instead of
        starting a competing agent — keeping one agent per conversation.

        ``stream`` controls whether the agent's internal reply is streamed: when
        ``True`` the run's :meth:`ChatRun.publish` is installed as the agent's
        ``_reply_stream_writer`` (captured by the background task's context), so
        the agent emits ``reply_*`` / ``intermediate_message`` events into the
        buffer. Non-streaming callers pass ``False`` to preserve the legacy
        single-shot reply behavior; they read :attr:`ChatRun.outcome` instead.
        """
        chat_id = str(chat_id)
        existing = self.get(chat_id)
        if existing is not None:
            return existing, False

        # Do not open SQLite while handling the HTTP request.  The driver
        # attaches storage from a worker thread before invoking the runner.
        run = ChatRun(chat_id, ack_event, max_buffer=self._max_buffer, db_path="")
        self.runs[chat_id] = run

        if stream:
            # The background task copies the current context at create_task time,
            # so set the writer immediately before and reset right after — the
            # task keeps its own captured copy (same pattern the legacy generator
            # used). Other request-scoped ContextVars (attachment map, etc.) ride
            # along because start_or_get is called synchronously from the handler.
            from cyrene.agent.context import bind_run_context
            from cyrene.workbench.inbox import _workbench_agent_inbox

            binding = bind_run_context(
                reply_stream_writer=run.publish,
                runtime_event_writer=run.publish,
            )
            inbox_token = _workbench_agent_inbox.set(run.inbox)
            try:
                run.task = asyncio.create_task(self._drive(run, runner))
            finally:
                _workbench_agent_inbox.reset(inbox_token)
                binding.reset()
        else:
            from cyrene.workbench.inbox import _workbench_agent_inbox

            inbox_token = _workbench_agent_inbox.set(run.inbox)
            try:
                run.task = asyncio.create_task(self._drive(run, runner))
            finally:
                _workbench_agent_inbox.reset(inbox_token)
        return run, True

    async def _drive(self, run: ChatRun, runner: Runner) -> None:
        run_span = trace_span(
            "run",
            "workbench_chat",
            span_id=run.run_id,
            trace_id=run.run_id,
            run_id=run.run_id,
            db_path=self._db_path,
            attributes={"chat_id": run.chat_id},
        ).start()
        try:
            if self._event_store is not None:
                await run.configure_event_store(self._event_store)
            if self._db_path:
                await asyncio.to_thread(run.inbox.configure_storage, self._db_path)
            run.ready.set()
            await runner(run)
        except asyncio.CancelledError:
            run.status = "cancelled"
            if not run.termination_reason:
                run.termination_reason = "cancelled"
            raise
        except Exception as exc:
            logger.exception("Chat run driver crashed for %s", run.chat_id)
            run.status = "error"
            run.termination_reason = "driver_error"
            run.outcome = {"kind": "error", "exc": exc}
            try:
                await run.publish({
                    "type": "error",
                    "error": "chat_run_driver_failed",
                    "message": "The agent run stopped unexpectedly. Please retry.",
                })
            except Exception:
                logger.exception("Failed to publish chat driver error for %s", run.chat_id)
            try:
                await asyncio.to_thread(
                    chat_service()._settle_chat_running_status,
                    run.chat_id,
                )
            except Exception:
                logger.exception("Failed to settle crashed chat %s", run.chat_id)
        finally:
            run.ready.set()
            outcome_kind = str((run.outcome or {}).get("kind") or "")
            # Close the guidance admission window before the first await in
            # finalization. This prevents an error-path guidance request from
            # being accepted while inbox cleanup is already underway.
            if run.status == "running":
                run.status = "error" if outcome_kind == "error" else "finishing"
            if not run.termination_reason:
                if run.status == "error" or outcome_kind == "error":
                    run.termination_reason = "agent_error"
                elif outcome_kind == "awaiting":
                    run.termination_reason = "awaiting_user"
                else:
                    run.termination_reason = "completed"
            try:
                await run.inbox.close(termination_reason=run.termination_reason)
            except Exception:
                # Cleanup must never prevent ``done`` from waking streams and
                # non-streaming callers.
                logger.exception("Failed to close chat inbox for %s", run.chat_id)
            run.status = "done" if run.status in {"running", "finishing"} else run.status
            persistence_span = trace_span(
                "persistence",
                "run_finalize",
                attributes={"durable_store": bool(self._db_path)},
            ).start()
            if self._db_path:
                try:
                    await asyncio.to_thread(
                        chat_service()._record_chat_run_outcome,
                        run.chat_id,
                        run_id=run.run_id,
                        status=run.status,
                        termination_reason=run.termination_reason,
                        outcome_kind=outcome_kind,
                        created_at=run.created_at,
                    )
                except Exception:
                    logger.exception("Failed to persist chat run outcome for %s", run.chat_id)
            if self._event_store is not None:
                try:
                    await run.flush_event_store()
                    await asyncio.to_thread(self._event_store.finalize, run)
                except Exception:
                    # ``_flush_event_store_now`` re-queued the failed batch, so
                    # the pending count is exactly what never reached SQLite.
                    # Log it loudly: with a 30s busy_timeout the lock-contention
                    # tradeoff (stall vs. silent event loss) stays observable.
                    logger.exception(
                        "Failed to finalize durable event log for run %s; "
                        "%d event(s) not persisted (in-memory only, lost on restart)",
                        run.run_id,
                        len(run._event_store_pending),
                    )
            await persistence_span.finish()
            await run_span.finish(status=run.status)
            # Shell-wake checks ``done`` to decide whether this chat is still
            # busy, so expose completion before attempting the pending wake.
            run.done.set()
            # A shell-exit wake may have been queued while this chat was busy.
            if run.termination_reason != "chat_deleted":
                try:
                    from cyrene.runtime.shell_wake import get_shell_wake_service

                    await get_shell_wake_service().try_dispatch(run.chat_id)
                except Exception:
                    logger.exception(
                        "Failed to dispatch pending shell wake for chat %s", run.chat_id
                    )
            # Nudge attached streams so they re-check ``done`` immediately rather
            # than waiting out the poll timeout.
            for queue in list(run.subscribers):
                try:
                    queue.put_nowait(None)
                except Exception:
                    pass
            self._schedule_cleanup(run)

    def _schedule_cleanup(self, run: ChatRun) -> None:
        """Drop a finished run from the registry after the retention window."""
        if run.termination_reason == "chat_deleted":
            if self.runs.get(run.chat_id) is run:
                self.runs.pop(run.chat_id, None)
            return
        if self._retention_seconds <= 0:
            if self.runs.get(run.chat_id) is run:
                self.runs.pop(run.chat_id, None)
            return
        try:
            task = asyncio.create_task(self._cleanup(run))
        except RuntimeError:
            # No running loop (shutdown / teardown) — drop immediately.
            if self.runs.get(run.chat_id) is run:
                self.runs.pop(run.chat_id, None)
            return
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _cleanup(self, run: ChatRun) -> None:
        await asyncio.sleep(self._retention_seconds)
        if self.runs.get(run.chat_id) is run:
            self.runs.pop(run.chat_id, None)

    async def stream(self, run: ChatRun, cursor: int = 0) -> AsyncGenerator[str, None]:
        """Yield NDJSON lines for ``run``: replay events after ``cursor``, then
        follow the live stream until the run finishes.

        ``cursor`` is the highest ``_seq`` the client has already seen; a fresh
        client passes ``0`` to replay from the ack. The replay snapshot and the
        live subscription are registered with no ``await`` between them, so no
        event can slip through the gap; a ``_seq`` high-water mark dedupes the
        boundary defensively.
        """
        cursor = int(cursor or 0)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        backlog = [event for event in run.events if int(event.get("_seq") or 0) > cursor]
        run.subscribers.add(queue)
        try:
            last = cursor
            for event in backlog:
                last = max(last, int(event.get("_seq") or 0))
                yield _ndjson_line(event)
            while True:
                if run.done.is_set() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_STREAM_POLL_SECONDS)
                except asyncio.TimeoutError:
                    continue
                if event is None:  # wakeup nudge from _drive — re-check done at top
                    continue
                seq = int(event.get("_seq") or 0)
                if seq <= last:
                    continue
                last = seq
                yield _ndjson_line(event)
        finally:
            run.subscribers.discard(queue)

    def startup(self) -> None:
        """Recover from a hard crash: a chat left ``status="running"`` in the
        store has no live task in this fresh process, so reset it to ``idle`` and
        clear any stale pending question that can no longer be resumed."""
        self.closed = False
        chat_mod = chat_service()

        try:
            payload = chat_mod._read_chats_store()
            changed = False
            for chat in payload.get("chats", []) or []:
                if str(chat.get("status") or "") == "running":
                    chat["status"] = "idle"
                    # A question left on a record that was subsequently marked
                    # running belongs to the crashed exchange. There is no live
                    # agent state in this process that can resume it safely.
                    chat.pop("pendingQuestion", None)
                    chat["lastRun"] = {
                        "id": "",
                        "status": "error",
                        "terminationReason": "process_restarted",
                        "outcome": "error",
                        "createdAt": str(chat.get("updatedAt") or ""),
                        "completedAt": datetime.now(timezone.utc).isoformat(),
                    }
                    chat["updatedAt"] = chat["lastRun"]["completedAt"]
                    changed = True
            if changed:
                chat_mod._write_chats_store(payload)
        except Exception:
            logger.exception("Chat run startup recovery failed")
        try:
            if self._db_path:
                chat_mod._reconcile_inbox_guidance_messages(self._db_path)
        except Exception:
            logger.exception("Chat guidance transcript reconciliation failed")
        try:
            if self._event_store is not None:
                self._event_store.recover_interrupted()
        except Exception:
            logger.exception("Chat durable run recovery failed")

    async def shutdown(self) -> None:
        """On graceful shutdown, give in-flight runs a chance to finalize (so a
        planned restart still persists replies) before cancelling the rest."""
        self.closed = True
        tasks = [run.task for run in self.runs.values() if run.task is not None and not run.task.done()]
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=self._shutdown_grace_seconds)
            for task in pending:
                for run in self.runs.values():
                    if run.task is task:
                        run.termination_reason = "shutdown_timeout"
                        break
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        for task in list(self._cleanup_tasks):
            task.cancel()
        if self._cleanup_tasks:
            await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)
        self._cleanup_tasks.clear()


__all__ = ["ChatRun", "ChatRunManager"]
