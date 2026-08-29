"""TurnStart projection of the current Workbench pane layout."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any

from cyrene.core.hook import TURN_START, HookEvent
from cyrene.core.plugin import PluginSetupContext

_HOOK_ID = "cyrene-split-context-turn-start"
_PLUGIN_ID = "cyrene_split_context.mount"
_PANE_WORKSPACE_NODE_ID = "pane_workspace"
_MAX_PANES = 12


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
    return panes if len(panes) >= 2 else []


def _context_text(panes: list[dict[str, str]]) -> str:
    if not panes:
        return ""
    lines = [
        "<current_workbench_split>",
        (
            "This is a read-only observation of the panes currently visible "
            "in the same Cyrene conversation that started this turn."
        ),
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

    async def mount(_event: HookEvent) -> dict[str, str]:
        if not ui_instance_id or not session_id:
            return {}

        # Import lazily so the context pack remains loadable for CLI and other
        # core-only hosts. The Workbench owns the live UI connection and the
        # semantic pane registry; this plugin only projects a bounded snapshot.
        from cyrene.workbench.ui import ui_surface

        snapshot = await ui_surface.request(
            ui_instance_id,
            "snapshot",
            {
                "parent_node_id": _PANE_WORKSPACE_NODE_ID,
                "include": ["interactive", "text"],
                "max_depth": 2,
                "page_size": _MAX_PANES,
            },
            timeout=1.5,
        )
        if snapshot.get("ok") is not True:
            return {}
        surface = _mapping(snapshot.get("surface"))
        visible_session_id = str(surface.get("visible_session_id") or "").strip()
        # Never leak the layout of whichever conversation happens to be visible
        # into a background, remote, side-Agent, or otherwise different run.
        if visible_session_id != session_id:
            return {}
        content = _context_text(_pane_items(snapshot))
        if not content:
            return {}
        return {
            "context": content,
            "context_kind": "current_workbench_split",
            "context_source": "cyrene_split_context",
        }

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
