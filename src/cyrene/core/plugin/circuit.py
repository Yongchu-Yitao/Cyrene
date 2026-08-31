"""Run-scoped circuit enforcement for structured Plugin failures."""

from __future__ import annotations

import threading
from collections.abc import Mapping

from .plugin import PluginFailure


class PluginCircuitBreaker:
    """Block a Plugin for the remainder of one run after a declared failure.

    Durable state remains in ContextTree tool-result nodes. This object is the
    session-local execution index and can be rebuilt from those nodes on load.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._open: dict[tuple[str, str], PluginFailure] = {}

    @staticmethod
    def _key(run_id: str, canonical_name: str) -> tuple[str, str] | None:
        normalized_run = str(run_id or "").strip()
        normalized_name = str(canonical_name or "").strip()
        if not normalized_run or not normalized_name:
            return None
        return normalized_run, normalized_name

    def failure_for(
        self,
        run_id: str,
        canonical_name: str,
    ) -> PluginFailure | None:
        key = self._key(run_id, canonical_name)
        if key is None:
            return None
        with self._lock:
            return self._open.get(key)

    def record(
        self,
        run_id: str,
        canonical_name: str,
        failure: PluginFailure | Mapping[str, object] | None,
    ) -> None:
        if failure is None:
            return
        structured = (
            failure
            if isinstance(failure, PluginFailure)
            else PluginFailure.from_dict(failure)
        )
        if structured.circuit_scope != "run_plugin":
            return
        key = self._key(run_id, canonical_name)
        if key is None:
            return
        with self._lock:
            self._open[key] = structured

    def blocked_failure(
        self,
        run_id: str,
        canonical_name: str,
    ) -> PluginFailure | None:
        cause = self.failure_for(run_id, canonical_name)
        if cause is None:
            return None
        return PluginFailure(
            error_code="plugin_circuit_open",
            message=(
                "Plugin execution is blocked for this run because an earlier "
                "non-immediate-retry failure opened its circuit."
            ),
            retryable=cause.retryable,
            retry_scope=cause.retry_scope,
            retry_after_ms=cause.retry_after_ms,
            circuit_scope="run_plugin",
            details={
                "blocked_plugin": str(canonical_name),
                "cause": cause.as_dict(),
            },
        )

    def reset_run(self, run_id: str) -> None:
        normalized = str(run_id or "").strip()
        if not normalized:
            return
        with self._lock:
            for key in tuple(self._open):
                if key[0] == normalized:
                    self._open.pop(key, None)


__all__ = ["PluginCircuitBreaker"]
