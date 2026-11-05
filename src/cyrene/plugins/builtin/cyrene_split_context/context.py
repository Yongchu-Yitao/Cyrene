"""TurnStart projection of the current Workbench pane layout."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
import logging
from typing import Any

from cyrene.core.hook import TURN_START, HookEvent
from cyrene.core.plugin import PluginSetupContext

_HOOK_ID = "cyrene-split-context-turn-start"
_PLUGIN_ID = "cyrene_split_context.mount"
_PANE_WORKSPACE_NODE_ID = "pane_workspace"
_MAX_PANES = 12
_CONTEXT_KIND = "current_workbench_split"
_CLOSED_CONTEXT = (
    "<current_workbench_split>\n"
    "The current conversation has no split view. This latest snapshot supersedes "
    "earlier split layouts; do not treat their panes as currently open.\n"
    "</current_workbench_split>"
)
_UNAVAILABLE_CONTEXT = (
    "<current_workbench_split>\n"
    "The current conversation's pane layout could not be read for this turn. "
    "Earlier split snapshots are historical and must not be treated as the current "
    "layout. This does not mean those panes have been closed.\n"
    "</current_workbench_split>"
)
logger = logging.getLogger(__name__)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pane_items(snapshot: Mapping[str, Any]) -> list[dict[str, str]]:
    root = _mapping(snapshot.get("root"))
    if str(root.get("node_id") or "") != _PANE_WORKSPACE_NODE_ID:
        return []
    panes: list[dict[str, str]] = []
    for raw in root.get("children") or ():
        node = _mapping(raw)
        state = _mapping(node.get("state"))
        kind = str(
            state.get("content_kind") or node.get("value_summary") or "pane"
        ).strip()
        name = str(node.get("name") or kind or "Pane").strip()
        if not name:
            continue
        panes.append({
            "name": name[:300],
            "kind": kind[:80],
            "side": str(state.get("side") or "").strip()[:16],
            "position": str(state.get("position") or "").strip()[:16],
            "content_id": str(state.get("content_id") or "").strip()[:300],
        })
        if len(panes) >= _MAX_PANES:
            break
    return panes


def _previous_context(context: PluginSetupContext, event: HookEvent) -> str | None:
    """Use only this turn's ancestors, including after a session is reopened."""
    user_node_id = str(_mapping(event.payload).get("user_node_id") or "").strip()
    if not user_node_id:
        return None
    for node in reversed(context.tree.get_path(context.tree_id, user_node_id)):
        value = _mapping(node.value)
        if value.get("role") == "context" and value.get("context_kind") == _CONTEXT_KIND:
            return str(value.get("content") or "")
    return None


def _context_text(panes: list[dict[str, str]]) -> str:
    if not panes:
        return ""
    lines = [
        "<current_workbench_split>",
        (
            "This is a read-only observation of the panes currently visible "
            "in the same Cyrene conversation that started this turn."
        ),
        "This latest snapshot supersedes earlier split-layout snapshots.",
        (
            "Use pane names and positions to resolve references such as the "
            "current split, the left or right pane, or the visible file. "
            "For file contents, use the file-reading capability rather than "
            "assuming the full document text is present here."
        ),
    ]
    for pane in panes:
        attrs = [
            f'name="{escape(pane["name"], quote=True)}"',
            f'kind="{escape(pane["kind"], quote=True)}"',
        ]
        if pane["side"]:
            attrs.append(f'side="{escape(pane["side"], quote=True)}"')
        if pane["position"]:
            attrs.append(f'position="{escape(pane["position"], quote=True)}"')
        if pane["content_id"]:
            attrs.append(
                f'content_id="{escape(pane["content_id"], quote=True)}"'
            )
        lines.append(f"- <pane {' '.join(attrs)} />")
    lines.append("</current_workbench_split>")
    return "\n".join(lines)


def setup_split_context(context: PluginSetupContext) -> None:
    """Mount the latest same-conversation pane layout on every user turn."""

    run_context = _mapping(context.data.get("run_context"))
    ui_instance_id = str(run_context.get("ui_instance_id") or "").strip()
    session_id = str(context.tree_id or run_context.get("session_id") or "").strip()

    async def mount(event: HookEvent) -> dict[str, str]:
        def contribution(content: str) -> dict[str, str]:
            return {
                "context": content,
                "context_kind": _CONTEXT_KIND,
                "context_source": "cyrene_split_context",
            }

        def invalidate(content: str) -> dict[str, str]:
            previous = _previous_context(context, event)
            # No stale state to retract on a fresh branch. Repeated absence
            # needs no duplicate block; recovery still emits a fresh snapshot.
            return contribution(content) if previous is not None and previous != content else {}

        if not ui_instance_id or not session_id:
            return invalidate(_UNAVAILABLE_CONTEXT)

        # Import lazily so the context pack remains loadable for CLI and other
        # core-only hosts. The Workbench owns the live UI connection and the
        # semantic pane registry; this plugin only projects a bounded snapshot.
        from cyrene.workbench.ui import ui_surface

        try:
            snapshot = _mapping(await ui_surface.request(
                ui_instance_id,
                "snapshot",
                {
                    "parent_node_id": _PANE_WORKSPACE_NODE_ID,
                    "include": ["interactive", "text"],
                    "max_depth": 2,
                    "page_size": _MAX_PANES,
                },
                timeout=1.5,
            ))
        except Exception:
            logger.warning("Could not read the current split layout", exc_info=True)
            return invalidate(_UNAVAILABLE_CONTEXT)
        if snapshot.get("ok") is not True:
            return invalidate(_UNAVAILABLE_CONTEXT)
        surface = _mapping(snapshot.get("surface"))
        visible_session_id = str(surface.get("visible_session_id") or "").strip()
        # Never leak the layout of whichever conversation happens to be visible
        # into a background, remote, side-Agent, or otherwise different run.
        if visible_session_id != session_id:
            return invalidate(_UNAVAILABLE_CONTEXT)
        panes = _pane_items(snapshot)
        if not panes:
            return invalidate(_UNAVAILABLE_CONTEXT)
        if len(panes) == 1:
            return invalidate(_CLOSED_CONTEXT)
        return contribution(_context_text(panes))

    existing = {hook.id: hook for hook in context.hooks.list()}
    if _HOOK_ID in existing:
        # Reopening a conversation may bind it to a new renderer instance.
        context.hooks.bind_plugin(_PLUGIN_ID, mount, replace=True)
        return
    context.hooks.register(
        TURN_START,
        mount,
        plugin_id=_PLUGIN_ID,
        hook_id=_HOOK_ID,
        root_only=True,
        # A missing, stale, or closing UI surface must not block the user's turn.
        failure_policy="open",
    )


__all__ = ["setup_split_context"]
