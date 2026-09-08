"""Ordered collection of concurrently completed Plugin results."""

from __future__ import annotations

import threading
from dataclasses import replace
from collections.abc import Iterable
from collections.abc import Callable

from .plugin import PluginCall, PluginCallResult, PluginFailure
from .result_codec import json_value


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
        self.execution_results: dict[str, PluginCallResult] = {}
        self._lock = threading.RLock()
        self._on_result = on_result

    def catch(self, result: PluginCallResult, *, notify: bool = True) -> None:
        with self._lock:
            if result.call_id not in self._expected:
                raise ValueError(f"unexpected Plugin call result: {result.call_id}")
            if result.call_id in self._results:
                raise ValueError(f"duplicate Plugin call result: {result.call_id}")
            self._results[result.call_id] = result
            self.execution_results[result.call_id] = result
        if notify and self._on_result is not None:
            try:
                self._on_result(result)
            except Exception as exc:
                # Execution and durability are separate outcomes. Keep the
                # actual result, but prohibit replay when its receipt is lost.
                failure = PluginFailure(
                    "tool_result_not_persisted",
                    "Tool returned, but its result could not be saved. Do not replay; reconcile external state.",
                    retryable=False, retry_scope="never", circuit_scope="none",
                    details={"execution_success": result.success, "persistence_error_type": type(exc).__name__},
                )
                try:
                    execution_value = json_value(result.value)
                except Exception:
                    execution_value = {"unserializable_type": type(result.value).__name__}
                with self._lock:
                    self._results[result.call_id] = replace(
                        result, success=False, error=failure.message, failure=failure,
                        value={"execution_success": result.success, "execution_value": execution_value,
                               "execution_error": result.error},
                        error_details={"code": failure.error_code, "retryable": False,
                                       "retry_scope": "never", "replay_safe": False},
                    )

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
