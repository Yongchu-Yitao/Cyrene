"""Process-level registry of in-flight Workbench *conversation* runs.

The normal Workbench chat path used to run the agent **inside** the HTTP
streaming request (``asyncio.create_task(_run())`` whose ``finally`` called
``task.cancel()``). The moment the client disconnected — a network blip, the
laptop sleeping, a closed tab, a server restart — the generator's ``finally``
fired and cancelled the agent before it could persist its reply. The exchange
was lost, while tool side effects (written files, etc.) were half-applied.

This module decouples the run from the request, mirroring the durability
pattern of :mod:`webui.workbench_goal_loop` (its ``GoalLoopManager.tasks``
registry, background ``asyncio.Task`` ownership, lifecycle hooks) but scoped to
the conversation path:

* The agent runs as a **background task owned by the registry**, not the
  request. When the HTTP request ends, the task is *not* cancelled.
* The run **always finalizes** (persists the assistant reply to
  ``workbench_chats.json``) when the agent completes, whether or not a client
  is still attached.
* Each run keeps an **append-only event log / ring buffer** so a reconnecting
  client can replay the events it missed while disconnected (``ack`` /
  ``intermediate_message`` / ``reply_start`` / ``reply_delta`` / ``reply_done``
  / ``awaiting_user`` / ``saved`` / ``error``) and then join the live stream.

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
from typing import Any, AsyncGenerator, Awaitable, Callable
from uuid import uuid4

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

# Event types that suppress the synthesized reply (the agent already streamed a
# real reply). Mirrors the legacy generator's ``startswith("reply_")`` check.
_REPLY_EVENT_PREFIX = "reply_"

# A runner is the per-send coroutine supplied by the route layer. It runs the
# agent, finalizes, and publishes terminal events via ``run.publish``.
Runner = Callable[["ChatRun"], Awaitable[None]]


def _ndjson_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


class ChatRun:
    """One in-flight conversation exchange and its replayable event buffer."""

    def __init__(self, chat_id: str, ack_event: dict[str, Any], *, max_buffer: int = _MAX_BUFFER_EVENTS, db_path: str = "") -> None:
        from cyrene.workbench_inbox import WorkbenchAgentInbox

        self.chat_id = str(chat_id)
        self.run_id = f"run_{uuid4().hex}"
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

    async def publish(self, event: dict[str, Any]) -> None:
        """Append an event to the buffer and fan it out to attached clients.

        Used both as the agent's ``_reply_stream_writer`` (so the agent's own
        ``reply_*`` / ``intermediate_message`` events are captured) and directly
        by the runner for terminal events. Awaitable but never blocks.
        """
        if str(event.get("type") or "") == "intermediate_message" and isinstance(event.get("message"), dict):
            try:
                from webui.routes_workbench_chat import _persist_live_public_message
                await asyncio.to_thread(
                    _persist_live_public_message, self.chat_id, event["message"]
                )
            except Exception:
                logger.exception("Failed to checkpoint intermediate chat message for %s", self.chat_id)
        self.seq += 1
        stored = {"_seq": self.seq, "runId": self.run_id, **dict(event)}
        self.events.append(stored)
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

    def configure(self, db_path: str) -> None:
        """Configure durable inbox storage before chat routes start runs."""
        self._db_path = str(db_path or "")

    def get(self, chat_id: str) -> ChatRun | None:
        run = self.runs.get(str(chat_id))
        if run is not None and run.done.is_set():
            return None
        return run

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
        for queue in list(run.subscribers):
            try:
                queue.put_nowait(None)
            except Exception:
                pass
        return True

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
            from cyrene.agent.state import _reply_stream_writer
            from cyrene.workbench_inbox import _workbench_agent_inbox

            token = _reply_stream_writer.set(run.publish)
            inbox_token = _workbench_agent_inbox.set(run.inbox)
            try:
                run.task = asyncio.create_task(self._drive(run, runner))
            finally:
                _workbench_agent_inbox.reset(inbox_token)
                _reply_stream_writer.reset(token)
        else:
            from cyrene.workbench_inbox import _workbench_agent_inbox

            inbox_token = _workbench_agent_inbox.set(run.inbox)
            try:
                run.task = asyncio.create_task(self._drive(run, runner))
            finally:
                _workbench_agent_inbox.reset(inbox_token)
        return run, True

    async def _drive(self, run: ChatRun, runner: Runner) -> None:
        try:
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
                from webui.routes_workbench_chat import _settle_chat_running_status

                await asyncio.to_thread(_settle_chat_running_status, run.chat_id)
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
            run.done.set()
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
        from webui import routes_workbench_chat as chat_mod

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
                    changed = True
            if changed:
                chat_mod._write_chats_store(payload)
        except Exception:
            logger.exception("Chat run startup recovery failed")

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
