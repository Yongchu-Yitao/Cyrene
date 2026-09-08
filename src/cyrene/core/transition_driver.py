"""Own the transition queue, worker thread and active asyncio task.

Callbacks retain domain decisions in AgentSession; no host object is retained.
"""
from __future__ import annotations
import asyncio
import logging
import queue
import threading
from dataclasses import dataclass
from collections.abc import Callable, Coroutine
from typing import Any
from .context import ContextNode
from .observability import log_operation, operation

logger = logging.getLogger("cyrene.core.session")

@dataclass(frozen=True)
class TransitionCallbacks:
    key: Callable[[ContextNode], str]
    run_id: Callable[[ContextNode], str]
    cancelled: Callable[[str], bool]
    coroutine: Callable[[str, ContextNode], Coroutine[Any, Any, None]]
    failure: Callable[[ContextNode, str, BaseException, str], Coroutine[Any, Any, None] | None]
    idle: Callable[[str], None]
    snapshot: Callable[[], dict[str, str]]

class TransitionDriver:
    def __init__(self, tree_id: str, callbacks: TransitionCallbacks) -> None:
        self.tree_id = tree_id
        self.callbacks = callbacks
        self.closed = False
        self.condition = threading.Condition(threading.RLock())
        self.pending: set[str] = set()
        self.work: queue.Queue[tuple[str, ContextNode] | None] = queue.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.active_task: asyncio.Task[None] | None = None
        self.active_run_id = ""
        self.thread = threading.Thread(target=self.worker_main,
            name=f"agent-transition-{tree_id}", daemon=True)

    def stop_locked(self) -> None:
        """Stop while the caller holds condition, preserving close serialization."""
        self.closed = True
        if self.loop is not None and self.active_task is not None and not self.active_task.done():
            self.loop.call_soon_threadsafe(self.active_task.cancel)
        self.work.put(None)
        self.condition.notify_all()

    def join(self) -> None:
        if self.thread is not threading.current_thread():
            self.thread.join()

    def enqueue(self, kind: str, node: ContextNode) -> None:
        key = f"{kind}:{self.callbacks.key(node)}"
        run_id = self.callbacks.run_id(node)
        cancelled = self.callbacks.cancelled(run_id)
        with self.condition:
            if self.closed or cancelled or key in self.pending:
                log_operation(
                    logger,
                    "cyrene.core.session",
                    "enqueue_transition",
                    phase="skipped",
                    tree_id=self.tree_id,
                    run_id=run_id,
                    node_id=node.id,
                    transition_kind=kind,
                    transition_key=key,
                    closed=self.closed,
                    cancelled=cancelled,
                    duplicate=key in self.pending,
                )
                return
            self.pending.add(key)
            self.work.put((kind, node))
            self.condition.notify_all()
        log_operation(
            logger,
            "cyrene.core.session",
            "enqueue_transition",
            phase="queued",
            tree_id=self.tree_id,
            run_id=run_id,
            node_id=node.id,
            transition_kind=kind,
            transition_key=key,
        )


    def worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self.condition:
            self.loop = loop
            self.condition.notify_all()
        log_operation(
            logger,
            "cyrene.core.session",
            "transition_worker",
            phase="started",
            tree_id=self.tree_id,
            thread=threading.current_thread().name,
        )
        try:
            while True:
                item = self.work.get()
                if item is None:
                    return
                self.run_transition(loop, item)
        finally:
            with self.condition:
                self.active_task = None
                self.active_run_id = ""
                self.loop = None
                self.condition.notify_all()
            loop.close()
            log_operation(
                logger,
                "cyrene.core.session",
                "transition_worker",
                phase="stopped",
                tree_id=self.tree_id,
                thread=threading.current_thread().name,
            )


    def run_transition(self, loop, item) -> None:
        kind, node = item
        key = f"{kind}:{self.callbacks.key(node)}"
        run_id = self.callbacks.run_id(node)
        try:
            cancelled = self.callbacks.cancelled(run_id)
            with self.condition:
                if self.closed or cancelled:
                    log_operation(
                        logger,
                        "cyrene.core.session",
                        "transition",
                        phase="skipped",
                        tree_id=self.tree_id,
                        run_id=run_id,
                        node_id=node.id,
                        transition_kind=kind,
                        transition_key=key,
                        closed=self.closed,
                        cancelled=cancelled,
                    )
                    return
            coroutine = self.callbacks.coroutine(kind, node)
            with operation(
                logger,
                "cyrene.core.session",
                "transition",
                tree_id=self.tree_id,
                run_id=run_id,
                node_id=node.id,
                transition_kind=kind,
                transition_key=key,
            ) as op:
                task = loop.create_task(coroutine)
                with self.condition:
                    self.active_task = task
                    self.active_run_id = run_id
                    self.condition.notify_all()
                loop.run_until_complete(task)
                op.finish(**self.callbacks.snapshot())
        except asyncio.CancelledError as exc:
            log_operation(
                logger,
                "cyrene.core.session",
                "transition_cancelled",
                phase="completed",
                level=logging.WARNING,
                tree_id=self.tree_id,
                run_id=run_id,
                node_id=node.id,
                transition_kind=kind,
                reason=exc,
            )
            if not self.closed and not self.callbacks.cancelled(run_id):
                self._handle_failure(loop, node, run_id, exc, kind)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            log_operation(
                logger,
                "cyrene.core.session",
                "transition_failure",
                phase="failed",
                level=logging.ERROR,
                exc_info=True,
                tree_id=self.tree_id,
                run_id=run_id,
                node_id=node.id,
                transition_kind=kind,
                error=exc,
            )
            self._handle_failure(loop, node, run_id, exc, kind)
        finally:
            with self.condition:
                self.active_task = None
                self.active_run_id = ""
                self.pending.discard(key)
                no_pending_transitions = not self.pending
                self.condition.notify_all()
            if no_pending_transitions:
                try:
                    self.callbacks.idle(run_id)
                except Exception:
                    logger.exception("Transition idle callback failed")

    def _handle_failure(self, loop, node, run_id, exc, kind) -> None:
        try:
            failure = self.callbacks.failure(node, run_id, exc, kind)
            if failure is not None:
                loop.run_until_complete(failure)
        except (Exception, asyncio.CancelledError):
            # A failed diagnostic write/hook must not kill the queue worker.
            logger.exception("Transition failure callback failed")

    def wait(self) -> None:
        with self.condition:
            while self.pending:
                self.condition.wait()
