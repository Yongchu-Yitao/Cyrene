"""Shared read-only selection of the durable public conversation path."""
from collections.abc import Mapping

from cyrene.core.restore_decision import terminal_run_ancestor

DIALOGUE_ROLES = frozenset({
    "system", "user", "context", "assistant", "tool_results",
    "context_compaction", "context_reflection",
})


def context_path(nodes, leaf_id, *, allow_partial=False):
    by_id = {node.id: node for node in nodes}
    path, visited = [], set()
    while leaf_id:
        if allow_partial and leaf_id not in by_id:
            break
        if leaf_id in visited or leaf_id not in by_id:
            raise ValueError("Invalid context ancestry")
        visited.add(leaf_id)
        node = by_id[leaf_id]
        path.append(node)
        leaf_id = node.parent_id
    return list(reversed(path))


def select_context_leaf(nodes, committed_leaf_id=""):
    dialogue = [n for n in nodes if isinstance(n.value, Mapping)
                and n.value.get("role") in DIALOGUE_ROLES]
    if not dialogue:
        raise ValueError("No dialogue nodes")
    leaf = terminal_run_ancestor(max(dialogue, key=lambda n: (n.created_at, n.id)), nodes)
    path = context_path(nodes, leaf.id, allow_partial=True)
    run_id = next((str(n.value.get("run_id")) for n in reversed(path)
                   if n.value.get("run_id")), "")
    user = next((n for n in reversed(path) if n.value.get("role") == "user"
                 and not n.value.get("runtime_guidance")
                 and str(n.value.get("run_id") or "") == run_id), None)
    value = leaf.value
    pending = value.get("pending_question")
    awaiting = (value.get("role") == "tool_results" and value.get("trigger_model") is False
                and isinstance(pending, Mapping)
                and str(pending.get("status") or "awaiting_user") == "awaiting_user")
    terminal = (value.get("cancelled") is True or value.get("error") is True or awaiting
                or value.get("role") == "assistant" and
                (value.get("session_end_complete") is True or value.get("answer_complete") is True))
    metadata = user.value.get("metadata") if user else {}
    if terminal and isinstance(metadata, Mapping) and metadata.get("retry") is True:
        committed = next((n for n in dialogue if n.id == committed_leaf_id), None)
        if committed is not None:
            return committed
    return leaf
