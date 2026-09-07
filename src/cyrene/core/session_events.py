"""Synchronous session event publication with one lock and listener registry."""
from __future__ import annotations

import logging
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .observability import log_operation


class SessionEvents:
    def __init__(self, tree_id, replay_events, event_factory, logger, listener=None):
        self.tree_id = tree_id
        self.events = replay_events
        self.event_factory = event_factory
        self.logger = logger
        self._event_lock = threading.RLock()
        self._event_sequence = 0
        self._event_listeners = {}
        if listener is not None:
            self._event_listeners[0] = listener
        self._next_event_listener_id = 1

    def initialize_sequence(self, sequence):
        # Called before the Context Store subscription, as in AgentSession.
        self._event_sequence = sequence

    @property
    def sequence(self):
        with self._event_lock:
            return self._event_sequence

    def subscribe(
        self,
        listener: Callable[[Any], None],
        *,
        replay: bool = False,
        after_sequence: int = 0,
    ) -> Callable[[], None]:
        """Subscribe to structured output without coupling the Agent to Workbench."""

        if not callable(listener):
            raise TypeError("listener must be callable")
        if replay:
            replayed = self.events(after_sequence=after_sequence)
            for event in replayed:
                listener(event)
        else:
            replayed = ()
        with self._event_lock:
            listener_id = self._next_event_listener_id
            self._next_event_listener_id += 1
            self._event_listeners[listener_id] = listener
        log_operation(
            self.logger,
            "cyrene.core.session",
            "subscribe",
            phase="completed",
            tree_id=self.tree_id,
            listener_id=listener_id,
            listener=getattr(listener, "__qualname__", type(listener).__qualname__),
            replay=replay,
            after_sequence=after_sequence,
            replayed=len(replayed),
        )

        def unsubscribe() -> None:
            with self._event_lock:
                removed = self._event_listeners.pop(listener_id, None) is not None
            log_operation(
                self.logger,
                "cyrene.core.session",
                "unsubscribe",
                phase="completed",
                tree_id=self.tree_id,
                listener_id=listener_id,
                removed=removed,
            )

        return unsubscribe

    def emit(
        self,
        event_type: str,
        *,
        run_id: str = "",
        node_id: str | None = None,
        time: datetime | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> Any:
        with self._event_lock:
            self._event_sequence += 1
            event = self.event_factory(
                sequence=self._event_sequence,
                type=event_type,
                tree_id=self.tree_id,
                run_id=str(run_id or ""),
                node_id=node_id,
                time=time or datetime.now(timezone.utc),
                data=deepcopy(dict(data or {})),
            )
            listeners = tuple(self._event_listeners.values())
        log_operation(
            self.logger,
            "cyrene.core.session",
            "emit_event",
            phase="started",
            tree_id=self.tree_id,
            run_id=event.run_id,
            node_id=event.node_id,
            sequence=event.sequence,
            event_type=event.type,
            event_time=event.time,
            data=event.data,
            listener_count=len(listeners),
        )
        delivered = 0
        failed = 0
        for listener in listeners:
            try:
                listener(event)
            except Exception as exc:
                failed += 1
                log_operation(
                    self.logger,
                    "cyrene.core.session",
                    "notify_listener",
                    phase="failed",
                    level=logging.ERROR,
                    exc_info=True,
                    tree_id=self.tree_id,
                    run_id=event.run_id,
                    node_id=event.node_id,
                    sequence=event.sequence,
                    event_type=event.type,
                    listener=getattr(listener, "__qualname__", type(listener).__qualname__),
                    error=exc,
                )
            else:
                delivered += 1
        log_operation(
            self.logger,
            "cyrene.core.session",
            "emit_event",
            phase="completed",
            tree_id=self.tree_id,
            run_id=event.run_id,
            node_id=event.node_id,
            sequence=event.sequence,
            event_type=event.type,
            delivered=delivered,
            failed=failed,
        )
        return event
