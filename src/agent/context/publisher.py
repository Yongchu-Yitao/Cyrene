"""Process-local publication of committed context changes."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from ..observability import log_operation
from .tree import ContextChange

logger = logging.getLogger(__name__)

ChangeListener = Callable[[ContextChange], None]


class ChangePublisher:
    """Fan committed changes out without coupling storage to Hook semantics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._listeners: dict[int, tuple[ChangeListener, str | None]] = {}
        self._next_listener_id = 1

    def subscribe(
        self,
        listener: ChangeListener,
        *,
        tree_id: str | None = None,
    ) -> Callable[[], None]:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = (listener, str(tree_id) if tree_id is not None else None)
        log_operation(
            logger,
            "context.publisher",
            "subscribe",
            phase="completed",
            listener_id=listener_id,
            tree_id=tree_id,
            listener=getattr(listener, "__qualname__", type(listener).__qualname__),
        )

        def unsubscribe() -> None:
            with self._lock:
                removed = self._listeners.pop(listener_id, None) is not None
            log_operation(
                logger,
                "context.publisher",
                "unsubscribe",
                phase="completed",
                listener_id=listener_id,
                tree_id=tree_id,
                removed=removed,
            )

        return unsubscribe

    def publish(self, change: ContextChange) -> None:
        with self._lock:
            listeners = tuple(self._listeners.items())
        matched = 0
        failed = 0
        log_operation(
            logger,
            "context.publisher",
            "publish",
            phase="started",
            tree_id=change.tree_id,
            node_id=change.node_id,
            context_action=change.action,
            change=change,
            listener_count=len(listeners),
        )
        for listener_id, (listener, tree_filter) in listeners:
            if tree_filter is not None and tree_filter != change.tree_id:
                continue
            matched += 1
            try:
                listener(change)
            except Exception as exc:
                failed += 1
                log_operation(
                    logger,
                    "context.publisher",
                    "notify_listener",
                    phase="failed",
                    level=logging.ERROR,
                    exc_info=True,
                    tree_id=change.tree_id,
                    node_id=change.node_id,
                    context_action=change.action,
                    listener_id=listener_id,
                    listener=getattr(listener, "__qualname__", type(listener).__qualname__),
                    message="Context change listener failed",
                    error=exc,
                )
            else:
                log_operation(
                    logger,
                    "context.publisher",
                    "notify_listener",
                    phase="completed",
                    tree_id=change.tree_id,
                    node_id=change.node_id,
                    context_action=change.action,
                    listener_id=listener_id,
                )
        log_operation(
            logger,
            "context.publisher",
            "publish",
            phase="completed",
            tree_id=change.tree_id,
            node_id=change.node_id,
            context_action=change.action,
            matched=matched,
            failed=failed,
        )

    def clear(self) -> None:
        with self._lock:
            count = len(self._listeners)
            self._listeners.clear()
        log_operation(
            logger,
            "context.publisher",
            "clear",
            phase="completed",
            removed=count,
        )
