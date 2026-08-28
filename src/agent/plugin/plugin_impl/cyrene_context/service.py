"""TurnStart context contribution for host-owned run metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.hook import TURN_START, HookEvent
from agent.plugin import PluginSetupContext

_RUNTIME_HOOK_ID = "cyrene-context-turn-start"
_LEGACY_HOOK_ID = "cyrene-context-session-start"
_RUNTIME_PLUGIN_ID = "cyrene_context.mount"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def setup_runtime_context(context: PluginSetupContext) -> None:
    """Mount the current turn's host context below higher-priority providers."""

    async def mount_runtime_context(event: HookEvent) -> dict[str, str]:
        details = _mapping(event.payload)
        metadata = _mapping(details.get("metadata"))
        # The user-node copy is authoritative: it survives process restarts
        # and belongs to one exact turn.  Every host must submit this field;
        # there is deliberately no process-local compatibility fallback.
        content = str(metadata.get("ephemeral_context") or "").strip()
        return {
            "context": content,
            "context_kind": "runtime_context",
            "context_source": "cyrene_context",
        } if content else {}

    existing = {hook.id: hook for hook in context.hooks.list()}
    if _LEGACY_HOOK_ID in existing:
        context.hooks.unregister(_LEGACY_HOOK_ID)
    if _RUNTIME_HOOK_ID in existing:
        context.hooks.bind_plugin(
            _RUNTIME_PLUGIN_ID,
            mount_runtime_context,
            replace=True,
        )
        return
    context.hooks.register(
        TURN_START,
        mount_runtime_context,
        plugin_id=_RUNTIME_PLUGIN_ID,
        hook_id=_RUNTIME_HOOK_ID,
        root_only=True,
        failure_policy="open",
    )


__all__ = ["setup_runtime_context"]
