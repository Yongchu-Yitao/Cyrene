"""Browser-session cleanup owned by the editable browser Plugin pack."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent.hook import SESSION_END, STOP, HookEvent
from agent.plugin import PluginSetupContext

_SESSION_END_HOOK_ID = "cyrene-browser-session-end"
_SESSION_END_PLUGIN_ID = "cyrene_browser.session_end"
_STOP_HOOK_ID = "cyrene-browser-stop"
_STOP_PLUGIN_ID = "cyrene_browser.stop"


def _session_id(context: PluginSetupContext) -> str:
    run_context = context.data.get("run_context")
    if isinstance(run_context, Mapping):
        value = str(run_context.get("session_id") or "").strip()
        if value:
            return value
    return context.tree_id


def _run_id(event: HookEvent) -> str:
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    return str(payload.get("run_id") or "").strip()


def _bind(
    context: PluginSetupContext,
    *,
    event: str,
    hook_id: str,
    plugin_id: str,
    handler: Callable[[HookEvent], Any | Awaitable[Any]],
) -> None:
    if hook_id in {hook.id for hook in context.hooks.list()}:
        context.hooks.bind_plugin(plugin_id, handler, replace=True)
        return
    context.hooks.register(
        event,
        handler,
        plugin_id=plugin_id,
        hook_id=hook_id,
        root_only=True,
        failure_policy="open",
    )


def setup_browser_lifecycle(context: PluginSetupContext) -> None:
    """Finalize Electron tabs after either completion or cancellation."""

    session_id = _session_id(context)

    async def finish(event: HookEvent) -> None:
        run_id = _run_id(event)
        if not run_id:
            return
        from cyrene.browser import finish_electron_browser_round

        await finish_electron_browser_round(session_id, run_id)

    _bind(
        context,
        event=SESSION_END,
        hook_id=_SESSION_END_HOOK_ID,
        plugin_id=_SESSION_END_PLUGIN_ID,
        handler=finish,
    )
    _bind(
        context,
        event=STOP,
        hook_id=_STOP_HOOK_ID,
        plugin_id=_STOP_PLUGIN_ID,
        handler=finish,
    )


__all__ = ["setup_browser_lifecycle"]
