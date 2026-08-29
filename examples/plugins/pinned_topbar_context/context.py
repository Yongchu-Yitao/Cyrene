"""Per-turn context mount for Workbench pinned resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cyrene.core.hook import TURN_START, HookEvent
from cyrene.core.plugin import PluginSetupContext

_HOOK_ID = "pinned-topbar-context-turn-start"
_PLUGIN_ID = "pinned_topbar_context.mount"


def _database_path(data: Mapping[str, Any]) -> str:
    """Resolve the Workbench database supplied by the conversation host."""

    return str(data.get("db_path") or "").strip()


def setup_context(context: PluginSetupContext) -> None:
    """Bind a dynamic mount to this conversation's persistent ContextTree."""

    db_path = _database_path(context.data)
    session_id = str(context.tree_id or "").strip()

    async def mount(_event: HookEvent) -> dict[str, str]:
        # Non-Workbench hosts do not expose a Workbench database. Returning no
        # contribution avoids accidentally reading process-global state left by
        # another host.
        if not db_path:
            return {}

        # Import lazily so the pack remains loadable in core-only runtimes. The
        # canonical Workbench service owns resource normalization, selected-text
        # materialization, bounded conversation summaries, and browser access
        # labels. Reconfigure on every turn before reading: the mount must reflect
        # pins added after this conversation was opened.
        from cyrene.workbench.chat import pinned_resources

        pinned_resources.configure(db_path)
        content = pinned_resources.global_agent_context(session_id).strip()
        if not content:
            return {}
        return {
            "context": content,
            "context_kind": "pinned_topbar_resources",
            "context_source": "pinned_topbar_context",
        }

    existing = {hook.id: hook for hook in context.hooks.list()}
    if _HOOK_ID in existing:
        # Hook identities persist with the ContextTree. Rebind the current
        # implementation whenever an existing conversation is reopened.
        context.hooks.bind_plugin(_PLUGIN_ID, mount, replace=True)
        return
    context.hooks.register(
        TURN_START,
        mount,
        plugin_id=_PLUGIN_ID,
        hook_id=_HOOK_ID,
        root_only=True,
        # A corrupt optional shelf entry must not prevent the user's message
        # from running. The Hook registry records failures while failing open.
        failure_policy="open",
    )


__all__ = ["setup_context"]
