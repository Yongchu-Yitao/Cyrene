"""Shared in-process ownership for conversation and task runs.

The coordinator is deliberately domain-neutral.  It owns admission,
``asyncio.Task`` binding, explicit interruption and terminal release; callers
keep projecting lifecycle events into their own domain stores (conversation
transcripts or task state machines).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunLease:
    """Exclusive ownership of one domain object while a run is in flight."""

    owner_type: str
    owner_id: str
    run_id: str
    request_id: str = ""
    run_type: str = ""
    task: asyncio.Task[Any] | None = None
    loop: asyncio.AbstractEventLoop | None = None
    payload: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    acquired_at: str = field(default_factory=_utc_now_iso)
    released_at: str = ""
    status: str = "running"
    termination_reason: str = ""
    released: bool = False

    @property
    def owner_key(self) -> tuple[str, str]:
        return self.owner_type, self.owner_id


class RunCoordinator:
    """Atomic, domain-neutral run admission and cancellation control plane.

    ``owner_type`` keeps conversation and task identifiers in separate
    namespaces.  A second request may attach to the returned active lease or be
    rejected by its domain adapter; replacement is never implicit.
    """

    def __init__(self, scope: str = "") -> None:
        self.scope = str(scope or "")
        self._lock = threading.RLock()
        self._active: dict[tuple[str, str], RunLease] = {}
        self._by_run_id: dict[str, RunLease] = {}

    @staticmethod
    def _current_task_and_loop() -> tuple[
        asyncio.Task[Any] | None,
        asyncio.AbstractEventLoop | None,
    ]:
        try:
            return asyncio.current_task(), asyncio.get_running_loop()
        except RuntimeError:
            return None, None

    def try_acquire(
        self,
        owner_type: str,
        owner_id: str,
        run_id: str,
        *,
        request_id: str = "",
        run_type: str = "",
        task: asyncio.Task[Any] | None = None,
        bind_current_task: bool = True,
        payload: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunLease | None:
        """Atomically acquire an owner, returning ``None`` on conflict."""
        normalized_type = str(owner_type or "")
        normalized_owner = str(owner_id or "")
        normalized_run = str(run_id or "")
        if not normalized_type or not normalized_owner or not normalized_run:
            raise ValueError("owner_type, owner_id and run_id are required")

        current_task, current_loop = self._current_task_and_loop()
        bound_task = task if task is not None else (
            current_task if bind_current_task else None
        )
        bound_loop = current_loop if bound_task is not None else None
        key = (normalized_type, normalized_owner)
        with self._lock:
            existing = self._active.get(key)
            if existing is not None and not existing.released:
                return None
            lease = RunLease(
                owner_type=normalized_type,
                owner_id=normalized_owner,
                run_id=normalized_run,
                request_id=str(request_id or ""),
                run_type=str(run_type or normalized_type),
                task=bound_task,
                loop=bound_loop,
                payload=payload,
                metadata=dict(metadata or {}),
            )
            self._active[key] = lease
            self._by_run_id[normalized_run] = lease
            return lease

    def get(self, owner_type: str, owner_id: str) -> RunLease | None:
        with self._lock:
            lease = self._active.get((str(owner_type or ""), str(owner_id or "")))
            return lease if lease is not None and not lease.released else None

    def get_by_run_id(self, run_id: str) -> RunLease | None:
        with self._lock:
            lease = self._by_run_id.get(str(run_id or ""))
            return lease if lease is not None and not lease.released else None

    def attach_task(
        self,
        lease: RunLease,
        task: asyncio.Task[Any],
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> bool:
        """Bind the actual execution task after admission but before yielding."""
        with self._lock:
            if lease.released or self._active.get(lease.owner_key) is not lease:
                return False
            lease.task = task
            if loop is None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
            lease.loop = loop
            return True

    def finish(
        self,
        lease: RunLease,
        *,
        status: str = "completed",
        termination_reason: str = "",
    ) -> bool:
        """Release a lease once its domain projector has reached a terminal state."""
        with self._lock:
            if lease.released:
                return False
            if self._active.get(lease.owner_key) is lease:
                self._active.pop(lease.owner_key, None)
            if self._by_run_id.get(lease.run_id) is lease:
                self._by_run_id.pop(lease.run_id, None)
            lease.status = str(status or "completed")
            lease.termination_reason = str(termination_reason or "")
            lease.released_at = _utc_now_iso()
            lease.released = True
            return True

    # ``release`` keeps domain adapters terse and makes the terminal action
    # explicit even when they do not need to report a richer status.
    def release(self, lease: RunLease) -> bool:
        return self.finish(lease)

    @staticmethod
    def _cancel(lease: RunLease) -> None:
        task = lease.task
        if task is None or task.done():
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if lease.loop is not None and lease.loop.is_running() and lease.loop is not current_loop:
            lease.loop.call_soon_threadsafe(task.cancel)
            return
        task.cancel()

    def interrupt(
        self,
        owner_type: str,
        owner_id: str,
        *,
        reason: str = "user_interrupted",
    ) -> bool:
        """Explicitly cancel the active task; its finalizer still owns release."""
        lease = self.get(owner_type, owner_id)
        if lease is None:
            return False
        lease.termination_reason = str(reason or "user_interrupted")
        self._cancel(lease)
        return True

    def active_leases(self, *, owner_type: str = "") -> list[RunLease]:
        normalized_type = str(owner_type or "")
        with self._lock:
            return [
                lease
                for lease in self._active.values()
                if not lease.released
                and (not normalized_type or lease.owner_type == normalized_type)
            ]


_SHARED_COORDINATORS: dict[str, RunCoordinator] = {}
_SHARED_COORDINATORS_LOCK = threading.Lock()


def run_coordinator_for(scope: str) -> RunCoordinator:
    """Return the shared coordinator for one application/storage scope."""
    key = str(scope or "")
    with _SHARED_COORDINATORS_LOCK:
        coordinator = _SHARED_COORDINATORS.get(key)
        if coordinator is None:
            coordinator = RunCoordinator(key)
            _SHARED_COORDINATORS[key] = coordinator
        return coordinator


__all__ = ["RunCoordinator", "RunLease", "run_coordinator_for"]
