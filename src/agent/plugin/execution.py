"""Task-local services available to code running inside a Plugin handler.

The active Runtime is intentionally kept out of :class:`PluginContext.data`:
that mapping is observable application data and may be logged or serialized.
This module instead binds an execution frame for the lifetime of one handler.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from .plugin import PluginCall, PluginCallResult, PluginContext

if TYPE_CHECKING:
    from .runtime import PluginRuntime


class PluginExecutionUnavailableError(RuntimeError):
    """Raised when a handler-only service is used outside PluginRuntime."""


class PluginInvocationError(RuntimeError):
    """Raised when a nested Plugin invocation returns a failed result."""

    def __init__(self, result: PluginCallResult) -> None:
        self.result = result
        super().__init__(result.error or f"Plugin invocation failed: {result.name}")


@dataclass(frozen=True, slots=True)
class PluginExecution:
    """The Runtime scope currently executing one Plugin call."""

    runtime: PluginRuntime
    call: PluginCall
    context: PluginContext
    review_context: PluginContext


_active_execution: ContextVar[PluginExecution | None] = ContextVar(
    "agent_plugin_execution",
    default=None,
)


class PluginExecutionBinding:
    """Idempotent reset handle for one task-local execution binding."""

    def __init__(self, token: Token[PluginExecution | None]) -> None:
        self._token: Token[PluginExecution | None] | None = token

    def reset(self) -> None:
        token, self._token = self._token, None
        if token is not None:
            _active_execution.reset(token)

    def __enter__(self) -> PluginExecutionBinding:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.reset()


def bind_plugin_execution(
    runtime: PluginRuntime,
    call: PluginCall,
    context: PluginContext,
) -> PluginExecutionBinding:
    """Bind one handler while retaining the outermost review-capable context."""

    parent = _active_execution.get()
    review_context = parent.review_context if parent is not None else context
    execution = PluginExecution(
        runtime=runtime,
        call=call,
        context=context,
        review_context=review_context,
    )
    return PluginExecutionBinding(_active_execution.set(execution))


def current_plugin_execution() -> PluginExecution | None:
    """Return the active handler scope, if called from PluginRuntime."""

    return _active_execution.get()


def require_plugin_execution() -> PluginExecution:
    execution = current_plugin_execution()
    if execution is None:
        raise PluginExecutionUnavailableError(
            "Plugin execution services require an active PluginRuntime handler"
        )
    return execution


async def invoke_plugin(
    name: str,
    arguments: dict[str, Any],
    *,
    review: bool = True,
    call_id: str | None = None,
) -> Any:
    """Invoke another Plugin through the active Runtime and return its value.

    ``review=True`` copies only the outermost review Hooks onto the current
    nested context. This is required when a handler dynamically chooses the
    actual Plugin name or arguments while preserving the current data, tree,
    node, and workspace. A fixed implementation detail that has already been
    reviewed may pass ``review=False`` while still receiving Runtime
    resolution and schema validation.
    """

    execution = current_plugin_execution()
    if execution is not None:
        context = replace(
            execution.context,
            hooks=(
                execution.review_context.hooks
                if review
                else None
            ),
        )
        result = await execution.runtime.call(
            str(name),
            dict(arguments),
            context,
            call_id=(execution.call.id if call_id is None else call_id),
        )
        if not result.success:
            raise PluginInvocationError(result)
        return result.value

    raise PluginExecutionUnavailableError(
        "nested Plugin invocation requires an active PluginRuntime handler"
    )


async def publish_plugin_progress(
    *,
    current: int,
    total: int,
    label: str = "",
) -> None:
    """Publish bounded progress for the active Plugin call."""

    execution = current_plugin_execution()
    if execution is None:
        raise PluginExecutionUnavailableError(
            "Plugin progress requires an active PluginRuntime handler"
        )

    from cyrene.agent.context import current_run_context, publish_runtime_event

    run_context = current_run_context()
    safe_total = max(0, int(total))
    safe_current = max(0, min(int(current), safe_total)) if safe_total else 0
    await publish_runtime_event(
        {
            "type": "tool_call_progress",
            "tool_call_id": execution.call.id,
            "current": safe_current,
            "total": safe_total,
            "progress": (
                1.0
                if safe_total == 0
                else min(1.0, safe_current / safe_total)
            ),
            "label": str(label or "")[:160],
            "round_id": run_context.round_id,
            "session_id": run_context.session_id,
        }
    )


__all__ = [
    "PluginExecution",
    "PluginExecutionBinding",
    "PluginExecutionUnavailableError",
    "PluginInvocationError",
    "bind_plugin_execution",
    "current_plugin_execution",
    "invoke_plugin",
    "publish_plugin_progress",
    "require_plugin_execution",
]
