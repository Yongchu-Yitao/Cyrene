"""Projection of durable conversation checkpoints and recovery guidance."""
from collections.abc import Mapping, Sequence
from typing import Any
from .bridge import WorkbenchPendingQuestion


def _cancellation_details(value):
    reason = str(value.get("cancel_reason") or "unknown")
    kind, recovery = {
        "user_cancelled": ("user_stop", "wait_for_user"),
        "user_interrupted": ("user_stop", "wait_for_user"),
        "shutdown_timeout": ("app_shutdown", "resume_existing_task"),
        "process_restarted": ("runtime_interrupted", "resume_existing_task"),
        "runtime_retry_exhausted": ("runtime_interrupted", "inspect_before_retry"),
        "chat_deleted": ("user_stop", "do_not_resume"),
    }.get(reason, ("unknown", "inspect_before_retry"))
    return dict(cancel_reason=reason, interruption_kind=kind, recovery_action=recovery)


def _checkpoint_run_id(nodes, leaf):
    by_id = {node.id: node for node in nodes}
    current = leaf
    run_id = ""
    while current is not None:
        value = current.value if isinstance(current.value, Mapping) else {}
        run_id = str(value.get("run_id") or "")
        if run_id:
            break
        current = by_id.get(str(current.parent_id or ""))
    return run_id


def context_checkpoint_from_nodes(nodes: Sequence[Any], committed_leaf_id: str = "") -> dict[str, Any] | None:
    """Project a durable run checkpoint from an already-loaded tree snapshot."""

    dialogue = [
        node
        for node in nodes
        if isinstance(node.value, Mapping)
        and node.value.get("role")
        in {
            "system",
            "user",
            "context",
            "context_compaction",
            "context_reflection",
            "assistant",
            "tool_results",
        }
    ]
    if not dialogue:
        return None
    from cyrene.core.context.paths import select_context_leaf
    leaf = select_context_leaf(nodes, committed_leaf_id)
    run_id = _checkpoint_run_id(nodes, leaf)
    value = leaf.value if isinstance(leaf.value, Mapping) else {}
    pending = value.get("pending_question")
    if (
        value.get("role") == "tool_results"
        and value.get("trigger_model") is False
        and isinstance(pending, Mapping)
        and str(pending.get("status") or "awaiting_user") == "awaiting_user"
    ):
        question = WorkbenchPendingQuestion.from_mapping(pending)
        return {
            "status": "awaiting_user",
            "run_id": run_id,
            "node_id": leaf.id,
            "pending_question": question,
            "active_plan": question.plan,
        }
    if value.get("cancelled") is True:
        status = "cancelled"
    elif value.get("error") is True:
        status = "failed"
    elif value.get("role") == "assistant" and (
        value.get("session_end_complete") is True or value.get("answer_complete") is True
    ):
        status = "completed"
    elif value.get("role") in {"context_compaction", "context_reflection"} and value.get("resume_model") is not True:
        status = "completed"
    else:
        status = "running"
    result = {
        "status": status,
        "run_id": run_id,
        "node_id": leaf.id,
    }
    if status == "cancelled":
        result.update(_cancellation_details(value))
    return result

