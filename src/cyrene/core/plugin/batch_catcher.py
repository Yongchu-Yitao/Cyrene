"""Ordered collection of concurrently completed Plugin results."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from collections.abc import Callable

from .plugin import PluginCall, PluginCallResult


class PluginBatchCatcher:
    """Collect a batch without exposing completion-order nondeterminism."""

    def __init__(
        self,
        calls: Iterable[PluginCall],
        *,
        on_result: Callable[[PluginCallResult], None] | None = None,
    ) -> None:
        self._order = tuple(call.id for call in calls)
        if len(self._order) != len(set(self._order)):
            raise ValueError("Plugin batch call ids must be unique")
        self._expected = set(self._order)
        self._results: dict[str, PluginCallResult] = {}
        self._lock = threading.RLock()
        self._on_result = on_result

    def catch(self, result: PluginCallResult, *, notify: bool = True) -> None:
        with self._lock:
            if result.call_id not in self._expected:
                raise ValueError(f"unexpected Plugin call result: {result.call_id}")
            if result.call_id in self._results:
                raise ValueError(f"duplicate Plugin call result: {result.call_id}")
            self._results[result.call_id] = result
        if notify and self._on_result is not None:
            self._on_result(result)

    @property
    def complete(self) -> bool:
        with self._lock:
            return len(self._results) == len(self._order)

    def results(self) -> tuple[PluginCallResult, ...]:
        with self._lock:
            missing = [call_id for call_id in self._order if call_id not in self._results]
            if missing:
                raise RuntimeError(f"Plugin batch is incomplete: {', '.join(missing)}")
            return tuple(self._results[call_id] for call_id in self._order)


__all__ = ["PluginBatchCatcher"]
