"""Small durable ContextTree projections owned by non-chat runtimes.

External runtimes such as the scheduler and media worker sometimes need to
record trusted system context without opening an AgentSession.  Keeping that
operation here avoids recreating a legacy session file as a second source of
truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cyrene.core.context import ContextError, ContextStoreRouter, TreeNotFoundError
from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory

_DIALOGUE_ROLES = frozenset(
    {
        "system",
        "user",
        "context",
        "context_compaction",
        "context_reflection",
        "assistant",
        "tool_results",
    }
)


def _is_terminal_dialogue(value: Mapping[str, Any]) -> bool:
    role = str(value.get("role") or "")
    if role == "assistant":
        return bool(
            value.get("session_end_complete") is True
            or value.get("error") is True
            or value.get("cancelled") is True
        )
    if role in {"context_compaction", "context_reflection"}:
        return value.get("resume_model") is not True
    # A trusted system record appended by this module represents an idle
    # boundary.  The root system node is also an idle boundary for a newly
    # created tree.
    return role == "system"


def append_context_record(
    db_path: str | Path,
    session_id: str,
    value: Mapping[str, Any],
    *,
    node_id: str,
    create_tree: bool = False,
    require_idle: bool = False,
) -> bool:
    """Append one idempotent record to the canonical dialogue branch.

    Returns ``False`` when the tree does not exist, the record already exists,
    or ``require_idle`` protects an in-flight / awaiting-user Agent run.  The
    latter is important for media workers: their visible result is still
    committed immediately, while the normal media wake later injects it into
    the Agent without creating a competing ContextTree branch.
    """

    target = str(session_id or "").strip()
    identity = str(node_id or "").strip()
    if not target or not identity:
        raise ValueError("session_id and node_id are required")

    router = ContextStoreRouter(
        workbench_agent_data_directory(str(db_path or "")) / "context"
    )
    try:
        try:
            tree = router.get_tree(target)
        except TreeNotFoundError:
            if not create_tree:
                return False
            try:
                tree = router.create_tree(
                    {
                        "role": "system",
                        "content": "",
                    },
                    tree_id=target,
                )
            except ContextError:
                # Another process may have initialized the same durable chat
                # after our lookup.
                tree = router.get_tree(target)

        nodes = router.get_subtree(tree.id, tree.root_id)
        for node in nodes:
            node_value = node.value if isinstance(node.value, Mapping) else {}
            if node.id == identity or str(node_value.get("message_id") or "") == identity:
                return False

        dialogue = [
            node
            for node in nodes
            if isinstance(node.value, Mapping)
            and str(node.value.get("role") or "") in _DIALOGUE_ROLES
        ]
        leaf = max(dialogue or nodes, key=lambda item: (item.created_at, item.id))
        leaf_value = leaf.value if isinstance(leaf.value, Mapping) else {}
        if require_idle and not _is_terminal_dialogue(leaf_value):
            return False
        try:
            router.mount(tree.id, leaf.id, dict(value), node_id=identity)
        except ContextError:
            # The deterministic id makes retries and concurrent reconciliation
            # converge on the same record.
            existing = router.get_node(tree.id, identity)
            existing_value = (
                existing.value if isinstance(existing.value, Mapping) else {}
            )
            if str(existing_value.get("message_id") or "") != identity:
                raise
            return False
        return True
    finally:
        router.close()


__all__ = ["append_context_record"]
