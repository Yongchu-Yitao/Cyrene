"""Process-level registry of in-flight Workbench *conversation* runs.

The normal Workbench chat path used to run the agent **inside** the HTTP
streaming request (``asyncio.create_task(_run())`` whose ``finally`` called
``task.cancel()``). The moment the client disconnected — a network blip, the
laptop sleeping, a closed tab, a server restart — the generator's ``finally``
fired and cancelled the agent before it could persist its reply. The exchange
was lost, while tool side effects (written files, etc.) were half-applied.

This module decouples the run from the request through a process-owned
background ``asyncio.Task`` registry scoped to the conversation path:

* The agent runs as a **background task owned by the registry**, not the
  request. When the HTTP request ends, the task is *not* cancelled.
* The run **always finalizes** (persists the assistant reply to SQLite) when the agent completes, whether or not a client
  is still attached.
* Each run keeps an **append-only event log / ring buffer** so a reconnecting
  client can replay the events it missed while disconnected (``ack`` /
  ``intermediate_message`` / ``reasoning_*`` / ``reply_start`` /
  ``reply_delta`` / ``reply_done`` / ``run_finalizing`` / ``awaiting_user`` /
  ``saved`` / ``error``) and then join the live stream.

The Agent ContextTree is the durable model-run checkpoint. The transport replay
buffer is additionally projected to SQLite, while the public result is written
to the Workbench chat document. Run-scoped inbox events are also stored in
SQLite, so accepted guidance remains session-isolated and idempotent.
"""

from __future__ import annotations

import asyncio
from bisect import bisect_right
import json
import logging
import sqlite3
import threading
import time
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Awaitable, Callable
from uuid import uuid4

from cyrene.localization import localized
from cyrene.model.status import model_status_message, register_model_status_persister
from cyrene.observability.trace import trace_span
from cyrene.platform.run_coordinator import RunCoordinator, RunLease, run_coordinator_for
from cyrene.workbench.chat.chat_application import (
    merge_chat_messages_chronologically,
    utc_now_iso,
)
from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.chat.run_timeline import RunTimeline

logger = logging.getLogger(__name__)

# How long an attached stream waits for a change before re-checking whether the
# run has finished. Small enough that termination is snappy, large enough that an
# idle attached client is not a busy-loop.
_STREAM_POLL_SECONDS = 0.25
# Shared replay history is bounded by count and serialized size. A subscriber
# behind the retained cursor receives the authoritative timeline snapshot.
_MAX_BUFFER_EVENTS = 6000
_MAX_BUFFER_BYTES = 8 * 1024 * 1024
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
_DURABLE_EVENT_BUSY_TIMEOUT_SECONDS = 1.0
_COMPRESSED_EVENT_PREFIX = b"CYE1"
_COMPRESS_EVENT_MIN_BYTES = 512
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

# Control-plane tools do not create user-visible activity rows.  They therefore
# cannot be used as a causal boundary between streamed prose and the next
# visible activity.  Keep this list aligned with the Web UI's tool filter.
_NON_VISIBLE_TOOL_NAMES = frozenset({
    "use_tools",
    "quit",
    "send_message",
    "update_plan_progress",
})

# A runner is the per-send coroutine supplied by the route layer. It runs the
# agent, finalizes, and publishes terminal events via ``run.publish``.
Runner = Callable[["ChatRun"], Awaitable[None]]
Settler = Callable[["ChatRun"], Awaitable[None]]


def _ndjson_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _encode_durable_event(event: dict[str, Any]) -> str | memoryview:
    """Encode one event without changing its cursor or replay payload."""
    raw = json.dumps(
        event,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    if len(raw) < _COMPRESS_EVENT_MIN_BYTES:
        return raw.decode("utf-8")
    compressed = zlib.compress(raw, level=3)
    if len(compressed) + len(_COMPRESSED_EVENT_PREFIX) >= len(raw):
        return raw.decode("utf-8")
    return sqlite3.Binary(_COMPRESSED_EVENT_PREFIX + compressed)


def _decode_durable_event(value: Any) -> dict[str, Any]:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        if value.startswith(_COMPRESSED_EVENT_PREFIX):
            value = zlib.decompress(value[len(_COMPRESSED_EVENT_PREFIX):])
        text = value.decode("utf-8")
    else:
        text = str(value)
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise ValueError("durable chat event must be a JSON object")
    return decoded


def _trim_durable_events(
    conn: sqlite3.Connection,
    run_id: str,
    last_seq: int,
) -> None:
    """Match the live ring buffer: retain ack plus the newest events."""
    cutoff = int(last_seq) - (_MAX_BUFFER_EVENTS - 1)
    if cutoff < 2:
        return
    rows = conn.execute(
        "SELECT seq, event_json FROM workbench_chat_run_events WHERE run_id = ? AND seq <= ? ORDER BY seq",
        (str(run_id), cutoff),
    ).fetchall()
    if any(int(row["seq"]) > 1 for row in rows):
        baseline = RunTimeline(str(run_id))
        ack: dict[str, Any] | None = None
        for row in rows:
            item = _decode_durable_event(row["event_json"])
            if int(row["seq"]) == 1:
                ack = item
            patch = item.get("timeline")
            if isinstance(patch, dict) and patch.get("version") in {1, 2}:
                baseline.ingest(patch)
            else:
                baseline.apply(item)
        if ack is not None:
            ack = {**ack, "timeline": baseline.snapshot()}
            conn.execute(
                "UPDATE workbench_chat_run_events SET event_json = ? WHERE run_id = ? AND seq = 1",
                (_encode_durable_event(ack), str(run_id)),
            )
    conn.execute(
        "UPDATE workbench_chat_runs SET replay_base_seq = MAX(replay_base_seq, ?) WHERE run_id = ?",
        (cutoff, str(run_id)),
    )
    conn.execute(
        """
        DELETE FROM workbench_chat_run_events
        WHERE run_id = ? AND seq > 1 AND seq <= ?
        """,
        (str(run_id), cutoff),
    )


class ChatRunEventStore:
    """SQLite-backed run metadata and cursor-addressable event history."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path or "")
        self._lock = threading.RLock()
        if self.db_path:
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        # Run events are a replay projection, not part of the Agent result.
        # Keep lock waits bounded so an unrelated runtime writer cannot stall
        # an otherwise completed conversation. Failed batches remain queued in
        # memory and are retried at the next flush/finalize boundary.
        conn = sqlite3.connect(
            self.db_path,
            timeout=_DURABLE_EVENT_BUSY_TIMEOUT_SECONDS,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(
            f"PRAGMA busy_timeout = {int(_DURABLE_EVENT_BUSY_TIMEOUT_SECONDS * 1000)}"
        )
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
                    last_seq INTEGER NOT NULL DEFAULT 0,
                    replay_base_seq INTEGER NOT NULL DEFAULT 1
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
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(workbench_chat_runs)")}
            if "replay_base_seq" not in columns:
                conn.execute("ALTER TABLE workbench_chat_runs ADD COLUMN replay_base_seq INTEGER NOT NULL DEFAULT 1")
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
            oversized = conn.execute(
                """
                SELECT run_id, last_seq FROM workbench_chat_runs
                WHERE last_seq >= ?
                """,
                (_MAX_BUFFER_EVENTS,),
            ).fetchall()
            for row in oversized:
                _trim_durable_events(
                    conn,
                    str(row["run_id"]),
                    int(row["last_seq"]),
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
            base = conn.execute("SELECT replay_base_seq FROM workbench_chat_runs WHERE run_id = ?", (str(run_id),)).fetchone()
            # Cancelled thread flushes may have committed before being retried.
            # Never resurrect operations already folded into the baseline.
            events = [event for event in events if int(event.get("_seq") or 0) > (int(base[0]) if base else 1)]
            if not events:
                return
            rows = [
                (
                    str(run_id),
                    int(event.get("_seq") or 0),
                    _encode_durable_event(event),
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
            _trim_durable_events(conn, str(run_id), last_seq)

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
                _encode_durable_event(event),
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
        _trim_durable_events(conn, str(run_id), seq)

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
                    "message": localized(
                        "The Cyrene process restarted before this run completed.",
                        "Cyrene 进程在本次运行完成前已重启。",
                    ),
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
                SELECT event_json, seq FROM workbench_chat_run_events
                WHERE run_id = ? ORDER BY seq
                """,
                (str(row["run_id"]),),
            ).fetchall()
        events = []
        for event_row in event_rows:
            try:
                event = _decode_durable_event(event_row["event_json"])
            except Exception:
                logger.warning(
                    "Corrupt durable event row dropped (run=%s, row=%s)",
                    str(row["run_id"]), event_row["seq"],
                    exc_info=True,
                )
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

    def __init__(
        self,
        chat_id: str,
        ack_event: dict[str, Any],
        *,
        max_buffer: int = _MAX_BUFFER_EVENTS,
        db_path: str = "",
        persist_live_message: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        from cyrene.workbench.application.inbox import (
            WorkbenchAgentInbox,
            WorkbenchGuidanceChannel,
        )

        ack = dict(ack_event)
        self._timing_enabled = bool(ack.pop("_timingEnabled", False))
        self._latency_started_monotonic = float(
            ack.pop("_latencyStartedMonotonic", time.monotonic())
        )
        self._first_delta_timed = False
        self.chat_id = str(chat_id)
        self.run_id = f"run_{uuid4().hex}"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self._event_store: ChatRunEventStore | None = None
        self._event_store_pending: list[dict[str, Any]] = []
        self._event_store_flush_lock = asyncio.Lock()
        self._event_store_flush_task: asyncio.Task[None] | None = None
        self._publish_lock = asyncio.Lock()
        self._persist_live_message = persist_live_message
        self.inbox = WorkbenchAgentInbox(
            self.chat_id, db_path=db_path, run_id=self.run_id
        )
        self.guidance_channel = WorkbenchGuidanceChannel(self.inbox)
        self.max_buffer = max(2, int(max_buffer))
        self.seq = 1
        # Event 1 is always the ack so a fresh attach (cursor 0) replays the
        # whole exchange from the top.
        self.events: list[dict[str, Any]] = [
            {"_seq": 1, "runId": self.run_id, **ack}
        ]
        self.subscribers: set[asyncio.Event] = set()
        self.done = asyncio.Event()
        self.ready = asyncio.Event()
        self.task: asyncio.Task[Any] | None = None
        self.saw_reply_events = False
        self.timeline = RunTimeline(self.run_id)
        self.events[0]["timeline"] = self.timeline.snapshot()
        self._event_sizes = [len(_ndjson_line(self.events[0]).encode("utf-8"))]
        self._buffer_bytes = sum(self._event_sizes)
        self.status = "running"
        self.termination_reason = ""
        # Result for non-streaming callers, set by the runner:
        #   {"kind": "reply", "payload": {...}}    — assistant reply persisted
        #   {"kind": "awaiting", "pending": {...}} — paused for an answer
        #   {"kind": "error", "exc": Exception}    — run failed
        self.outcome: dict[str, Any] | None = None
        self.settler: Settler | None = None

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
        run._publish_lock = asyncio.Lock()
        run._persist_live_message = None
        run.inbox = None
        run.guidance_channel = None
        run.max_buffer = max(_MAX_BUFFER_EVENTS, len(events))
        run.seq = int(last_seq)
        run.events = list(events)
        run._event_sizes = [len(_ndjson_line(event).encode("utf-8")) for event in events]
        run._buffer_bytes = sum(run._event_sizes)
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
        run.timeline = RunTimeline(run.run_id)
        for event in events:
            patch = event.get("timeline")
            if isinstance(patch, dict) and patch.get("version") in {1, 2}:
                run.timeline.ingest(patch)
            else:
                run.timeline.apply(event)
        run.timeline.counter = max((int(message.get("timelineOrder") or 0)
                                    for message in run.timeline.records.values()), default=0)
        run.status = str(status)
        run.termination_reason = str(termination_reason)
        run.outcome = {"kind": str(outcome_kind)} if outcome_kind else None
        run.settler = None
        run._latency_started_monotonic = time.monotonic()
        run._timing_enabled = False
        run._first_delta_timed = True
        return run

    def _timing_event(self, stage: str, **attributes: Any) -> dict[str, Any]:
        return {
            "type": "chat_timing",
            "stage": str(stage),
            "source": "server",
            "serverElapsedMs": round(
                max(0.0, time.monotonic() - self._latency_started_monotonic) * 1000,
                3,
            ),
            "clientRequestId": str(
                (self.events[0] if self.events else {}).get("clientRequestId") or ""
            ),
            **attributes,
        }

    async def mark_timing(self, stage: str, **attributes: Any) -> None:
        await self.publish(self._timing_event(stage, **attributes))

    async def configure_event_store(self, store: ChatRunEventStore) -> None:
        await asyncio.to_thread(store.create, self)
        # Attach only after the initial write succeeds. If setup fails, the
        # manager can continue with the in-memory replay buffer without later
        # publishes repeatedly hitting the unavailable projection.
        self._event_store = store

    def _schedule_event_store_flush(self, *, immediate: bool = False) -> None:
        task = self._event_store_flush_task
        if task is not None and not task.done():
            if not immediate:
                return
            # A terminal/non-batchable event should not sit behind the normal
            # batching delay. Cancellation is safe because failed/in-flight
            # batches are re-queued idempotently by sequence number.
            task.cancel()
        delay = 0.0 if immediate else _DURABLE_EVENT_BATCH_INTERVAL_SECONDS
        task = asyncio.create_task(self._flush_event_store_after_delay(delay))
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

    async def _flush_event_store_after_delay(self, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
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

    @staticmethod
    def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
        payload = event.get("payload")
        return payload if isinstance(payload, dict) else event

    @classmethod
    def _visible_reply_delta(cls, event: dict[str, Any]) -> str:
        event_type = str(event.get("type") or "")
        payload = cls._event_payload(event)
        if event_type in {"reply_delta", "message.delta"}:
            return str(
                payload.get("delta")
                if payload.get("delta") is not None
                else payload.get("text") or payload.get("content") or ""
            )
        return ""

    def terminal_timeline_messages(
        self,
        activity_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return the same identified records delivered to live clients."""

        return self.timeline.messages()

    async def _publish_one(self, event: dict[str, Any]) -> None:
        """Append one already-ordered event and fan it out to attached clients.

        Used both as the agent's ``_reply_stream_writer`` (so the agent's own
        ``reply_*`` / ``intermediate_message`` events are captured) and directly
        by the runner for terminal events. Event fanout is authoritative for
        the live exchange; durable replay flushes in the background so SQLite
        contention cannot delay or fail the Agent result.
        """
        try:
            from cyrene.workbench.application.usage_events import publish_usage_event

            await publish_usage_event(event, session_id=self.chat_id)
        except Exception:
            logger.debug(
                "Failed to publish Workbench usage event for %s",
                self.chat_id,
                exc_info=True,
            )
        if self._persist_live_message is not None:
            patch = event.get("timeline") or {}
            changed_messages = [*patch.get("messages", []),
                                *(self.timeline.records[update["id"]] for update in patch.get("updates", []))]
            for message in changed_messages:
                if message.get("status") == "running":
                    continue
                try:
                    await asyncio.to_thread(self._persist_live_message, self.chat_id, message)
                except Exception:
                    logger.exception("Failed to checkpoint timeline record for %s", self.chat_id)
        self.seq += 1
        stored = {"_seq": self.seq, "runId": self.run_id, **dict(event)}
        self.events.append(stored)
        size = len(_ndjson_line(stored).encode("utf-8"))
        self._event_sizes.append(size)
        self._buffer_bytes += size
        if self._event_store is not None:
            self._event_store_pending.append(stored)
            event_type = str(event.get("type") or "")
            flush_immediately = (
                event_type not in _BATCHABLE_DURABLE_EVENT_TYPES
                or len(self._event_store_pending) >= _DURABLE_EVENT_BATCH_MAX
            )
            self._schedule_event_store_flush(immediate=flush_immediately)
        overflow = max(0, len(self.events) - self.max_buffer)
        removed_bytes = sum(self._event_sizes[1:1 + overflow])
        while self._buffer_bytes - removed_bytes > _MAX_BUFFER_BYTES and len(self.events) - overflow > 2:
            removed_bytes += self._event_sizes[1 + overflow]
            overflow += 1
        if overflow:
            del self.events[1:1 + overflow]
            del self._event_sizes[1:1 + overflow]
            self._buffer_bytes -= removed_bytes
        if str(event.get("type") or "").startswith(_REPLY_EVENT_PREFIX):
            self.saw_reply_events = True
        for signal in self.subscribers:
            signal.set()

    async def publish(self, event: dict[str, Any]) -> None:
        """Project once, then deliver the same records to storage and clients."""

        value = dict(event)
        execution_run_id = str(value.get("executionRunId") or value.get("runId") or "")
        if execution_run_id:
            value["executionRunId"] = execution_run_id
        value["runId"] = self.run_id
        async with self._publish_lock:
            if value.get("type") == "chat_timing" and "serverElapsedMs" not in value:
                value = self._timing_event(str(value.get("stage") or "unknown"),
                                          **{key: item for key, item in value.items()
                                             if key not in {"type", "stage", "source", "serverElapsedMs"}})
            if (not self._first_delta_timed and self._timing_enabled
                and (self._visible_reply_delta(value)
                     or value.get("type") == "reasoning_delta" and value.get("delta"))):
                self._first_delta_timed = True
                await self._publish_one(self._timing_event("first_delta"))
            value["timeline"] = self.timeline.apply(value)
            await self._publish_one(value)


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
        self._repository = ChatRepository()
        # Unconfigured managers (mostly isolated tests) get a private control
        # plane. ``configure`` switches production to the DB-scoped coordinator.
        self._coordinator = RunCoordinator(f"chat-manager:{id(self)}")
        self._leases: dict[str, RunLease] = {}
        from cyrene.workbench.core_adapter.conversation_runtime import ConversationRuntime

        self.conversation_runtime = ConversationRuntime()

    def configure(self, db_path: str) -> None:
        """Configure durable inbox and run-event storage before runs start."""
        if self._coordinator.active_leases(owner_type="conversation"):
            raise RuntimeError("cannot reconfigure ChatRunManager while runs are active")
        self._db_path = str(db_path or "")
        if self._db_path:
            self._repository.configure(self._db_path)
        self.conversation_runtime.configure(self._db_path)
        if self._db_path:
            from cyrene.workbench.persistence.schema import ensure_schema

            ensure_schema(self._db_path)
        self._coordinator = (
            run_coordinator_for(self._db_path)
            if self._db_path
            else RunCoordinator(f"chat-manager:{id(self)}")
        )
        try:
            self._event_store = (
                ChatRunEventStore(self._db_path) if self._db_path else None
            )
        except Exception:
            # Durable run replay is an optional projection. A locked or
            # unavailable event store must not prevent Workbench Chat itself
            # from starting; the live in-memory buffer remains usable.
            self._event_store = None
            logger.exception(
                "Chat durable event store is unavailable; continuing with in-memory replay"
            )

    @property
    def configured_db_path(self) -> str:
        return self._db_path

    def settle_chat_running_status(self, chat_id: str) -> None:
        self._settle_chat_running_status(chat_id)

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
        self.conversation_runtime.request_cancel(chat_id, "user_interrupted")
        run.status = "cancelled"
        run.termination_reason = "user_interrupted"
        run.outcome = {"kind": "interrupted"}
        self._coordinator.interrupt(
            "conversation",
            run.chat_id,
            reason=run.termination_reason,
        )
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
            self.conversation_runtime.request_cancel(target, run.termination_reason)
            task = run.task
            self._coordinator.interrupt(
                "conversation",
                target,
                reason=run.termination_reason,
            )
            if task is not None and not task.done() and self._coordinator.get(
                "conversation", target
            ) is None:
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
                for signal in run.subscribers:
                    signal.set()
            if self.runs.get(target) is run:
                self.runs.pop(target, None)
            lease = self._leases.pop(run.run_id, None)
            if lease is not None:
                self._coordinator.finish(
                    lease,
                    status=run.status,
                    termination_reason=run.termination_reason,
                )
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
        settler: Settler | None = None,
    ) -> tuple[ChatRun, bool]:
        """Start a background run for ``chat_id``, or return the live one.

        When a run is already in flight for this conversation (e.g. a double
        submit, or a reconnect that raced the original), the existing run is
        returned with ``is_new=False`` so the caller attaches to it instead of
        starting a competing agent — keeping one agent per conversation.

        ``stream`` remains part of the route contract, but native Agent and
        external-Agent adapters both receive :meth:`ChatRun.publish` explicitly;
        no legacy reply-writer ContextVar is installed by the manager.

        ``settler`` runs after the manager has persisted the terminal outcome
        and before :attr:`ChatRun.done` is exposed. Route-specific projections
        can therefore publish one final state without owning task lifecycle or
        duplicating run-outcome persistence.
        """
        chat_id = str(chat_id)
        existing = self.get(chat_id)
        if existing is not None:
            return existing, False

        # Do not open SQLite while handling the HTTP request.  The driver
        # attaches storage from a worker thread before invoking the runner.
        run = ChatRun(
            chat_id,
            ack_event,
            max_buffer=self._max_buffer,
            db_path="",
            persist_live_message=self._persist_live_public_message,
        )
        run.settler = settler
        lease = self._coordinator.try_acquire(
            "conversation",
            chat_id,
            run.run_id,
            request_id=str(ack_event.get("clientRequestId") or ""),
            run_type="conversation",
            bind_current_task=False,
            payload=run,
        )
        if lease is None:
            active = self._coordinator.get("conversation", chat_id)
            attached = active.payload if active is not None else None
            if isinstance(attached, ChatRun):
                return attached, False
            # The owner exists but was not created by this transcript adapter.
            # This should never happen because owner namespaces are distinct,
            # but surfacing it is safer than starting an unowned duplicate.
            raise RuntimeError(f"conversation run ownership conflict: {chat_id}")
        self.runs[chat_id] = run
        self._leases[run.run_id] = lease

        try:
            # The native ConversationRuntime receives its publisher explicitly.
            # Keep only the Workbench guidance inbox binding for the route-level
            # admission API; no legacy Agent ContextVars are installed here.
            del stream
            from cyrene.workbench.application.inbox import _workbench_agent_inbox

            inbox_token = _workbench_agent_inbox.set(run.inbox)
            try:
                run.task = asyncio.create_task(self._drive(run, runner))
            finally:
                _workbench_agent_inbox.reset(inbox_token)
            if run.task is None or not self._coordinator.attach_task(lease, run.task):
                raise RuntimeError(f"conversation run lost ownership: {chat_id}")
        except Exception:
            if self.runs.get(chat_id) is run:
                self.runs.pop(chat_id, None)
            self._leases.pop(run.run_id, None)
            self._coordinator.finish(
                lease,
                status="error",
                termination_reason="start_failed",
            )
            raise
        return run, True

    async def _drive(self, run: ChatRun, runner: Runner) -> None:
        run.guidance_channel.bind_owner_loop(asyncio.get_running_loop())
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
                try:
                    await run.configure_event_store(self._event_store)
                except Exception:
                    logger.exception(
                        "Failed to attach durable event log for run %s; "
                        "continuing with in-memory replay",
                        run.run_id,
                    )
            if self._db_path:
                await asyncio.to_thread(run.inbox.configure_storage, self._db_path)
                if run.inbox.has_guidance_nowait():
                    run.guidance_channel.notify()
            run.ready.set()
            await runner(run)
        except asyncio.CancelledError:
            run.status = "cancelled"
            if not run.termination_reason:
                run.termination_reason = "cancelled"
            raise
        except Exception as exc:
            outcome_kind = str((run.outcome or {}).get("kind") or "")
            if outcome_kind in {"reply", "awaiting"}:
                # Once the runner has persisted and published a terminal
                # outcome, later projection/telemetry failures cannot revoke
                # it.  In particular, a delayed SQLite status repair used to
                # emit an error after ``saved`` and poison the following run.
                logger.exception(
                    "Chat run post-terminal cleanup failed for %s",
                    run.chat_id,
                )
            else:
                logger.exception("Chat run driver crashed for %s", run.chat_id)
                from cyrene.platform.doctor.repository import record_runtime_incident
                incident_id = record_runtime_incident(exc, chat_id=run.chat_id, run_id=run.run_id, stage="run_driver_or_finalization")
                run.status = "error"
                run.termination_reason = "driver_error"
                run.outcome = {"kind": "error", "exc": exc}
                try:
                    await run.publish({
                        "type": "error",
                        "error": "chat_run_driver_failed",
                        "code": "chat_run_driver_failed",
                        **({"incidentId": incident_id} if incident_id else {}),
                        "detail_key": "workbenchChat.error.driverFailed",
                        "message": localized(
                            "The agent run stopped unexpectedly. Please retry.",
                            "智能体运行意外停止，请重试。",
                        ),
                    })
                except Exception:
                    logger.exception("Failed to publish chat driver error for %s", run.chat_id)
                try:
                    await asyncio.to_thread(self._settle_chat_running_status, run.chat_id)
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
                run.guidance_channel.close()
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
                        self._record_chat_run_outcome,
                        run.chat_id,
                        run_id=run.run_id,
                        status=run.status,
                        termination_reason=run.termination_reason,
                        outcome_kind=outcome_kind,
                        created_at=run.created_at,
                    )
                except Exception:
                    logger.exception("Failed to persist chat run outcome for %s", run.chat_id)
            if run._event_store is not None:
                try:
                    await run.flush_event_store()
                    await asyncio.to_thread(run._event_store.finalize, run)
                except Exception:
                    # ``_flush_event_store_now`` re-queued the failed batch, so
                    # the pending count is exactly what never reached SQLite.
                    # Log it loudly so the lock-contention tradeoff (bounded
                    # wait vs. event loss on restart) stays observable.
                    logger.exception(
                        "Failed to finalize durable event log for run %s; "
                        "%d event(s) not persisted (in-memory only, lost on restart)",
                        run.run_id,
                        len(run._event_store_pending),
                    )
            if run.settler is not None:
                try:
                    await run.settler(run)
                except Exception:
                    logger.exception(
                        "Failed to publish terminal chat state for %s",
                        run.chat_id,
                    )
            await persistence_span.finish()
            await run_span.finish(status=run.status)
            # Durable transcript/event projection is terminal at this point.
            # Release ownership before exposing ``done`` so a new send cannot
            # attach to a completed-but-not-yet-released run during shell wake.
            lease = self._leases.pop(run.run_id, None)
            if lease is not None:
                self._coordinator.finish(
                    lease,
                    status=run.status,
                    termination_reason=run.termination_reason,
                )
            # Shell-wake checks ``done`` to decide whether this chat is still
            # busy, so expose completion before attempting the pending wake.
            run.done.set()
            # A shell-exit wake may have been queued while this chat was busy.
            if run.termination_reason != "chat_deleted":
                try:
                    from cyrene.platform.shell_wake import get_shell_wake_service

                    await get_shell_wake_service().try_dispatch(run.chat_id)
                except Exception:
                    logger.exception(
                        "Failed to dispatch pending shell wake for chat %s", run.chat_id
                    )
            # Nudge attached streams so they re-check ``done`` immediately rather
            # than waiting out the poll timeout.
            for signal in run.subscribers:
                signal.set()
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
        last = int(cursor or 0)
        changed = asyncio.Event()
        run.subscribers.add(changed)
        try:
            while True:
                # No await between clearing the signal and taking the replay
                # view: publications cannot fall between subscription and read.
                changed.clear()
                start = bisect_right(run.events, last, key=lambda event: int(event.get("_seq") or 0))
                backlog = run.events[start:]
                first_retained = int(run.events[1]["_seq"]) if len(run.events) > 1 else run.seq + 1
                if run.seq > 1 and last < first_retained - 1:
                    terminal = [event for event in backlog if event.get("type") in {"saved", "error", "interrupted", "awaiting_user"}]
                    snapshot = {"type": "timeline_snapshot", "_seq": run.seq, "runId": run.run_id,
                                "timeline": run.timeline.snapshot()}
                    backlog = ([run.events[0]] if last == 0 else []) + [snapshot, *terminal]
                for event in backlog:
                    last = max(last, int(event.get("_seq") or 0))
                    yield _ndjson_line(event)
                # Release the bounded replay view before waiting. A subscriber
                # owns a cursor and signal, never an accumulating event queue.
                backlog.clear()
                if run.done.is_set() and last >= run.seq:
                    break
                if last < run.seq:
                    continue
                try:
                    await asyncio.wait_for(changed.wait(), timeout=_STREAM_POLL_SECONDS)
                except asyncio.TimeoutError:
                    continue
        finally:
            run.subscribers.discard(changed)

    async def persist_model_status_message(
        self,
        chat_id: str,
        round_id: str,
        *,
        status: str,
        model: str,
        retry_count: int = 0,
        retry_limit: int = 0,
    ) -> None:
        """Insert or update the single durable model-status card for a round."""

        session = str(chat_id or "").strip()
        if not session:
            return
        message = model_status_message(
            session,
            round_id,
            status=status,
            model=model,
            retry_count=retry_count,
            retry_limit=retry_limit,
        )
        await asyncio.to_thread(
            self._persist_live_public_message,
            session,
            message,
        )

    def _persist_live_public_message(
        self,
        chat_id: str,
        message: dict[str, Any],
    ) -> None:
        """Checkpoint one already-visible intermediate message exactly once."""

        if not isinstance(message, dict) or not str(message.get("id") or "").strip():
            return
        entry = dict(message)
        if not entry.get("timelineVersion"):
            entry["intermediate"] = True
            entry.pop("trace", None)
            entry.pop("opensActivity", None)

        def persist(chat: dict[str, Any]) -> None:
            merge_chat_messages_chronologically(chat, [entry])
            model_status = (
                entry.get("modelStatus")
                if isinstance(entry.get("modelStatus"), dict)
                else {}
            )
            if str(model_status.get("status") or "") == "switched":
                actual_model = str(model_status.get("model") or "").strip()
                if actual_model:
                    chat["lastModel"] = actual_model
            chat["updatedAt"] = str(
                entry.get("createdAt") or chat.get("updatedAt") or utc_now_iso()
            )

        self._repository.mutate_one(str(chat_id or ""), persist)

    def _settle_chat_running_status(self, chat_id: str) -> None:
        def settle(chat: dict[str, Any]) -> bool:
            if chat.get("status") != "running":
                return False
            chat["status"] = "idle"
            chat.pop("pendingQuestion", None)
            chat["updatedAt"] = utc_now_iso()
            return True

        self._repository.mutate_one(str(chat_id or ""), settle)

    def _record_chat_run_outcome(
        self,
        chat_id: str,
        *,
        run_id: str,
        status: str,
        termination_reason: str = "",
        outcome_kind: str = "",
        created_at: str = "",
    ) -> None:
        def record(chat: dict[str, Any]) -> bool:
            previous = (
                chat.get("lastRun")
                if isinstance(chat.get("lastRun"), dict)
                else {}
            )
            previous_created = str(previous.get("createdAt") or "")
            if previous_created and created_at and previous_created > created_at:
                return False
            completed_at = utc_now_iso()
            chat["lastRun"] = {
                "id": str(run_id or ""),
                "status": str(status or "idle"),
                "terminationReason": str(termination_reason or ""),
                "outcome": str(outcome_kind or ""),
                "createdAt": str(created_at or ""),
                "completedAt": completed_at,
            }
            if chat.get("status") == "running":
                chat["status"] = "idle"
            chat["updatedAt"] = completed_at
            return True

        self._repository.mutate_one(str(chat_id or ""), record)

    def _reconcile_inbox_guidance_messages(self) -> int:
        """Repair the inbox/transcript crash window from durable inbox rows."""

        if not self._db_path:
            return 0
        from cyrene.workbench.application.inbox import read_workbench_guidance_records

        records = read_workbench_guidance_records(self._db_path)
        if not records:
            return 0
        payload = self._repository.read()
        chats = {
            str(chat.get("id") or ""): chat
            for chat in payload.get("chats", [])
            if isinstance(chat, dict)
        }
        repaired = 0
        for record in records:
            chat = chats.get(str(record.get("sessionId") or ""))
            if chat is None:
                continue
            messages = chat.setdefault("messages", [])
            event_id = str(record.get("eventId") or "")
            message_id = str(record.get("messageId") or "")
            if any(
                isinstance(item, dict)
                and (
                    str(item.get("id") or "") == message_id
                    or (
                        event_id
                        and str(item.get("guidanceEventId") or "") == event_id
                    )
                )
                for item in messages
            ):
                continue
            entry = {
                "id": message_id,
                "role": "user",
                "content": str(record.get("content") or ""),
                "createdAt": str(record.get("createdAt") or utc_now_iso()),
                "guidance": True,
                "guidanceEventId": event_id,
                "runId": str(record.get("runId") or ""),
            }
            client_request_id = str(record.get("clientRequestId") or "")
            if client_request_id:
                entry["clientRequestId"] = client_request_id
            merge_chat_messages_chronologically(chat, [entry])
            chat["updatedAt"] = max(
                str(chat.get("updatedAt") or ""),
                str(entry["createdAt"]),
            )
            repaired += 1
        if repaired:
            self._repository.write(payload)
        return repaired

    def startup(self) -> None:
        """Reconcile Chat projections while preserving durable ContextTree runs."""
        self.closed = False
        try:
            if not self._db_path:
                return
            payload = self._repository.read()
            changed = False
            for chat in payload.get("chats", []) or []:
                if str(chat.get("status") or "") == "running":
                    checkpoint = (
                        self.conversation_runtime.context_checkpoint(
                            str(chat.get("id") or "")
                        )
                        if self._db_path
                        else None
                    )
                    chat["status"] = "idle"
                    if checkpoint and checkpoint.get("status") == "awaiting_user":
                        pending = checkpoint.get("pending_question")
                        chat["pendingQuestion"] = (
                            pending.as_dict()
                            if hasattr(pending, "as_dict")
                            else None
                        )
                        plan = checkpoint.get("active_plan")
                        if plan is not None:
                            chat["activePlan"] = plan
                        chat["lastRun"] = {
                            "id": str(checkpoint.get("run_id") or ""),
                            "status": "awaiting_user",
                            "terminationReason": "awaiting_user",
                            "outcome": "awaiting",
                            "createdAt": str(chat.get("updatedAt") or ""),
                            "completedAt": datetime.now(timezone.utc).isoformat(),
                        }
                    elif checkpoint and checkpoint.get("status") == "running":
                        # The Agent tree remains resumable under its original
                        # run id. Do not synthesize a cancellation marker.
                        chat["lastRun"] = {
                            "id": str(checkpoint.get("run_id") or ""),
                            "status": "recoverable",
                            "terminationReason": "process_restarted",
                            "outcome": "recoverable",
                            "createdAt": str(chat.get("updatedAt") or ""),
                            "completedAt": "",
                        }
                    else:
                        chat.pop("pendingQuestion", None)
                        chat["lastRun"] = {
                            "id": "",
                            "status": "error",
                            "terminationReason": "process_restarted",
                            "outcome": "error",
                            "createdAt": str(chat.get("updatedAt") or ""),
                            "completedAt": datetime.now(timezone.utc).isoformat(),
                        }
                    completed_at = str(chat["lastRun"].get("completedAt") or "")
                    if completed_at:
                        chat["updatedAt"] = completed_at
                    changed = True
            if changed:
                self._repository.write(payload)
        except Exception:
            logger.exception("Chat run startup recovery failed")
        try:
            if self._db_path:
                self._reconcile_inbox_guidance_messages()
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
                        self._coordinator.interrupt(
                            "conversation",
                            run.chat_id,
                            reason=run.termination_reason,
                        )
                        break
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        for run_id, lease in list(self._leases.items()):
            if lease.task is None or lease.task.done():
                self._coordinator.finish(
                    lease,
                    status="cancelled",
                    termination_reason=lease.termination_reason or "shutdown",
                )
                self._leases.pop(run_id, None)
        for task in list(self._cleanup_tasks):
            task.cancel()
        if self._cleanup_tasks:
            await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)
        self._cleanup_tasks.clear()
        await self.conversation_runtime.shutdown()


# Detached post-reply bookkeeping (workspace-changes finalize, structured
# memory capture, learning schedule) owned at the cyrene layer so both the
# route and the runtime shutdown path can drain it without a layering cycle.
_POST_REPLY_BOOKKEEPING_TASKS: set[asyncio.Task[Any]] = set()


async def drain_post_reply_bookkeeping_tasks() -> None:
    """Cancel-safe shutdown drain for the detached post-reply bookkeeping tasks.

    Workspace-changes finalize, structured memory capture and the learning
    schedule are fire-and-forget; without a drain they are silently lost on app
    exit (previously they ran inline inside the run task, which ChatRunManager's
    shutdown waits for). Await them with a bounded grace period, cancel whatever
    is still pending, and discard the registry so the drain stays idempotent.
    """
    tasks = list(_POST_REPLY_BOOKKEEPING_TASKS)
    if not tasks:
        return
    try:
        _done, pending = await asyncio.wait(tasks, timeout=10.0)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        raise
    finally:
        _POST_REPLY_BOOKKEEPING_TASKS.clear()


def schedule_post_reply_bookkeeping(coro: Any, *, error_context: str) -> None:
    """Track one detached post-reply bookkeeping task in the registry.

    The done callback drops the task reference and surfaces its exception (if
    any) so a failing detached workload never goes silent.
    """

    def _done(task: asyncio.Task[Any]) -> None:
        _POST_REPLY_BOOKKEEPING_TASKS.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.error("Detached task failed: %s", error_context, exc_info=exc)

    task = asyncio.create_task(coro)
    _POST_REPLY_BOOKKEEPING_TASKS.add(task)
    task.add_done_callback(_done)


# One process-wide owner for ordinary Workbench conversation runs.  Keeping
# the singleton here makes the lifecycle independent from the retired route
# composition module and gives WebUI startup/shutdown one explicit boundary.
_CHAT_RUN_MANAGER = ChatRunManager()

# Model providers are deliberately independent from the Workbench package.
# Bind their low-level status seam here, where the process-owned conversation
# manager and its configured repository are both available.
register_model_status_persister(_CHAT_RUN_MANAGER.persist_model_status_message)


def get_chat_run_manager() -> ChatRunManager:
    return _CHAT_RUN_MANAGER


def startup_chat_runs(db_path: str = "") -> None:
    if str(db_path or "").strip() and _CHAT_RUN_MANAGER._db_path != str(db_path):
        _CHAT_RUN_MANAGER.configure(str(db_path))
    _CHAT_RUN_MANAGER.startup()


async def shutdown_chat_runs() -> None:
    await _CHAT_RUN_MANAGER.shutdown()
    await drain_post_reply_bookkeeping_tasks()


__all__ = [
    "ChatRun",
    "ChatRunManager",
    "drain_post_reply_bookkeeping_tasks",
    "get_chat_run_manager",
    "schedule_post_reply_bookkeeping",
    "shutdown_chat_runs",
    "startup_chat_runs",
]
