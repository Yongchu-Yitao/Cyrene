"""Per-turn context mount for Workbench pinned resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cyrene.core.hook import TURN_START, HookEvent
from cyrene.core.plugin import PluginSetupContext

_HOOK_ID = "pinned-topbar-context-turn-start"
_PLUGIN_ID = "pinned_topbar_context.mount"
_CONTEXT_KIND = "pinned_topbar_resources"
_NO_RESOURCES_CONTEXT = "\n".join((
    "<pinned_topbar_resources>",
    (
        "No Workbench resources are currently pinned. This latest snapshot "
        "supersedes earlier pinned-resource snapshots."
    ),
    "</pinned_topbar_resources>",
))


def _database_path(data: Mapping[str, Any]) -> str:
    """Resolve the Workbench database supplied by the conversation host."""

    return str(data.get("db_path") or "").strip()


def _latest_injected_context(
    context: PluginSetupContext,
    event: HookEvent,
) -> str | None:
    """Return the newest persisted snapshot on the current conversation path."""

    payload = event.payload if isinstance(event.payload, Mapping) else {}
    user_node_id = str(payload.get("user_node_id") or "").strip()
    nodes = (
        context.tree.get_path(context.tree_id, user_node_id)
        if user_node_id
        else context.tree.get_subtree(context.tree_id, context.root_id)
    )
    matches = []
    for node in nodes:
        value = node.value if isinstance(node.value, Mapping) else {}
        if (
            value.get("role") == "context"
            and str(value.get("context_kind") or "") == _CONTEXT_KIND
        ):
            matches.append(node)
    if not matches:
        return None
    latest = max(matches, key=lambda node: (node.created_at, node.id))
    value = latest.value if isinstance(latest.value, Mapping) else {}
    return str(value.get("content") or "").strip()


def setup_context(context: PluginSetupContext) -> None:
    """Bind a dynamic mount to this conversation's persistent ContextTree."""

    db_path = _database_path(context.data)
    session_id = str(context.tree_id or "").strip()
    previous_context: str | None = None
    previous_context_loaded = False
    has_injected_context = False

    async def mount(event: HookEvent) -> dict[str, str]:
        nonlocal previous_context, previous_context_loaded, has_injected_context

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
        event_payload = event.payload if isinstance(event.payload, Mapping) else {}
        has_current_path = bool(str(event_payload.get("user_node_id") or "").strip())
        if has_current_path or not previous_context_loaded:
            previous_context = _latest_injected_context(context, event)
            has_injected_context = previous_context is not None
            previous_context_loaded = True

        # An initially empty shelf contributes nothing. Once a snapshot has
        # existed, however, clearing the shelf is itself an update and needs a
        # final empty snapshot so historical blocks are not mistaken as current.
        if not content and not has_injected_context:
            previous_context = ""
            return {}
        snapshot = content or _NO_RESOURCES_CONTEXT
        if snapshot == previous_context:
            return {}
        previous_context = snapshot
        has_injected_context = True
        return {
            "context": snapshot,
            "context_kind": _CONTEXT_KIND,
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
