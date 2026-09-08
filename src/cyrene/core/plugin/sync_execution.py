"""Own synchronous handlers independently of the asyncio task waiting for them.

Python threads cannot be forcibly stopped. A timed-out/cancelled waiter must
therefore leave an unknown execution outcome, never a replayable timeout.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field

from .plugin import PluginExecutionError, PluginFailure


@dataclass(eq=False)
class _Execution:
    future: Future = field(default_factory=Future)
    abandoned: bool = False


_lock = threading.RLock()
_active: dict[tuple[str, str], list[_Execution]] = {}


def _abandon(execution):
    with _lock:
        execution.abandoned = True
        if execution.future.done() and execution.future.exception() is None:
            value = execution.future.result()
            if inspect.iscoroutine(value):
                value.close()


def _unknown() -> PluginExecutionError:
    return PluginExecutionError(PluginFailure(
        "plugin_execution_unknown",
        "Stopped waiting for a synchronous tool, but its execution may still be running. "
        "Do not replay; reconcile external state.",
        retryable=False, retry_scope="never", circuit_scope="run_plugin",
        details={"execution_state": "unknown", "replay_safe": False},
    ))


async def invoke_sync(plugin, arguments, context):
    key = (str(context.workspace or ""), plugin.name)
    execution = _Execution()
    copied_context = contextvars.copy_context()
    with _lock:
        if any(item.abandoned and not item.future.done() for item in _active.get(key, ())):
            raise _unknown()
        _active.setdefault(key, []).append(execution)

    def retire(future):
        with _lock:
            items = _active.get(key, [])
            if execution in items:
                items.remove(execution)
            if not items:
                _active.pop(key, None)
            abandoned = execution.abandoned
        # A sync factory may return a coroutine. No waiter owns it after an
        # abandoned execution, so close it instead of leaking it unawaited.
        if abandoned and future.exception() is None:
            value = future.result()
            if inspect.iscoroutine(value):
                value.close()

    def run():
        try:
            value = copied_context.run(plugin.handler, arguments, context)
        except BaseException as exc:
            execution.future.set_exception(exc)
        else:
            execution.future.set_result(value)

    execution.future.add_done_callback(retire)
    try:
        threading.Thread(target=run, name=f"plugin-{plugin.name}", daemon=True).start()
    except BaseException:
        with _lock:
            _active[key].remove(execution)
            if not _active[key]:
                _active.pop(key, None)
        raise
    wrapped = asyncio.wrap_future(execution.future)
    wrapped.add_done_callback(lambda future: None if future.cancelled() else future.exception())
    try:
        done, _ = await asyncio.wait({wrapped}, timeout=plugin.timeout_seconds)
        if done:
            return wrapped.result()
        _abandon(execution)
        raise _unknown()
    except asyncio.CancelledError:
        _abandon(execution)
        raise
