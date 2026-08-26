"""Process-local publication of committed context changes."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

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

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.pop(listener_id, None)

        return unsubscribe

    def publish(self, change: ContextChange) -> None:
        with self._lock:
            listeners = tuple(self._listeners.values())
        for listener, tree_filter in listeners:
            if tree_filter is not None and tree_filter != change.tree_id:
                continue
            try:
                listener(change)
            except Exception:
                logger.exception(
                    "Context change listener failed (tree=%s, node=%s, action=%s)",
                    change.tree_id,
                    change.node_id,
                    change.action,
                )

    def clear(self) -> None:
        with self._lock:
            self._listeners.clear()
