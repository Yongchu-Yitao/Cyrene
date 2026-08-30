"""Durable Goal state projection into the owning Conversation ContextTree."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from cyrene.core.plugin import PluginContext, plugin_session_state, with_plugin_session_state

PACK_ID = "cyrene_goal"
TERMINAL_STATUSES = frozenset({"completed", "aborted"})


def public_goal(goal: Mapping[str, Any]) -> dict[str, Any] | None:
    status = str(goal.get("status") or "")
    if status in TERMINAL_STATUSES:
        return None
    return {
        key: deepcopy(goal.get(key))
        for key in (
            "id",
            "chatId",
            "revision",
            "status",
            "phase",
            "objective",
            "acceptanceCriteria",
            "constraints",
            "outOfScope",
            "durationSeconds",
            "activeSeconds",
            "activeStartedAt",
            "attempt",
            "candidate",
            "review",
            "pausedFromStatus",
            "waitingFromStatus",
            "stopReason",
            "createdAt",
            "updatedAt",
        )
        if goal.get(key) is not None
    }


def _state(previous: Mapping[str, Any], goal: Mapping[str, Any]) -> dict[str, Any]:
    child_ids = [
        str(item or "").strip()
        for item in goal.get("childContextIds", ())
        if str(item or "").strip()
    ] if isinstance(goal.get("childContextIds"), (list, tuple)) else []
    active = public_goal(goal)
    snapshot = dict(previous.get("public_snapshot") or {}) if isinstance(
        previous.get("public_snapshot"), Mapping
    ) else {}
    if active is None:
        snapshot.pop("activeGoal", None)
    else:
        snapshot["activeGoal"] = active
    return {
        **dict(previous),
        "schema_version": 1,
        "goal": deepcopy(dict(goal)),
        "child_context_ids": child_ids,
        "public_snapshot": snapshot,
    }


def persist_goal(context: PluginContext, goal: Mapping[str, Any]) -> bool:
    if context.tree is None or not context.tree_id or not context.node_id:
        return False
    try:
        path = context.tree.get_path(context.tree_id, context.node_id)
    except Exception:
        return False
    if not path or not isinstance(path[0].value, Mapping):
        return False
    root = path[0]
    previous = plugin_session_state(root.value, PACK_ID)
    context.tree.update_node(
        context.tree_id,
        root.id,
        with_plugin_session_state(root.value, PACK_ID, _state(previous, goal)),
    )
    return True


def persist_goal_by_id(db_path: str, chat_id: str, goal: Mapping[str, Any]) -> bool:
    """Best-effort application-side projection for controller state changes."""

    from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
    from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory

    router = ContextStoreRouter(workbench_agent_data_directory(db_path) / "context")
    try:
        try:
            tree = router.get_tree(str(chat_id))
            root = router.get_node(tree.id, tree.root_id)
        except TreeNotFoundError:
            return False
        if not isinstance(root.value, Mapping):
            return False
        previous = plugin_session_state(root.value, PACK_ID)
        router.update_node(
            tree.id,
            root.id,
            with_plugin_session_state(root.value, PACK_ID, _state(previous, goal)),
        )
        return True
    finally:
        router.close()


def clear_goal_by_id(db_path: str, chat_id: str) -> bool:
    """Remove the Goal projection after a failed atomic workflow start."""

    from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
    from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory

    router = ContextStoreRouter(workbench_agent_data_directory(db_path) / "context")
    try:
        try:
            tree = router.get_tree(str(chat_id))
            root = router.get_node(tree.id, tree.root_id)
        except TreeNotFoundError:
            return False
        if not isinstance(root.value, Mapping):
            return False
        router.update_node(
            tree.id,
            root.id,
            with_plugin_session_state(root.value, PACK_ID, None),
        )
        return True
    finally:
        router.close()


__all__ = [
    "PACK_ID",
    "TERMINAL_STATUSES",
    "clear_goal_by_id",
    "persist_goal",
    "persist_goal_by_id",
    "public_goal",
]
