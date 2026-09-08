"""Select recovery actions without writing nodes, publishing or scheduling work.

Queries are lazy to preserve the original branch precedence and store reads.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


class RunTerminatedError(RuntimeError):
    """A new input attempted to reuse a failed or cancelled run."""


def terminal_run_ancestor(leaf, nodes):
    """A late descendant cannot supersede its run's failure/cancellation.

    Stop at a different run so an explicit new turn remains authoritative.
    Successful answers are not barriers: child coordination can continue them.
    """
    by_id = {node.id: node for node in nodes}
    current = leaf
    run_id = ""
    while current is not None:
        value = current.value if isinstance(current.value, Mapping) else {}
        current_run = str(value.get("run_id") or "")
        if current_run:
            if run_id and current_run != run_id:
                break
            run_id = current_run
        if run_id and (value.get("cancelled") is True or value.get("error") is True):
            return current
        current = by_id.get(current.parent_id)
    return leaf


@dataclass(frozen=True, slots=True)
class RestoreDecision:
    action: str
    pending: dict[str, Any] | None = None


def select_restore_action(
    value: Mapping[str, Any], *, pending: Callable[[], dict[str, Any] | None],
    has_assistant: Callable[[], bool], has_tool_result: Callable[[], bool],
    has_context_provider: Callable[[], bool],
) -> RestoreDecision:
    if value.get("cancelled") is True:
        return RestoreDecision("cancelled")
    question = pending()
    if question is not None:
        return RestoreDecision("awaiting_user", question)
    role = value.get("role")
    if role in {"context_compaction", "context_reflection"}:
        # Reflection restores the request before querying existing transitions.
        # Leave that query in the rewrite executor to preserve failure ordering.
        return RestoreDecision("rewrite")
    if role == "assistant" and value.get("tool_calls"):
        return RestoreDecision("tools_complete" if has_tool_result() else "resume_tools")
    return _select_unfinished_action(value, has_assistant, has_context_provider)


def _select_unfinished_action(value, has_assistant, has_context_provider):
    role = value.get("role")
    if role == "context" and value.get("trigger_model") is False:
        return RestoreDecision("resume_context_source")
    if role == "user" and value.get("trigger_model") is False and has_context_provider():
        return RestoreDecision("resume_user_context")
    if (
        role == "assistant" and not value.get("tool_calls")
        and value.get("error") is not True
        and value.get("cancelled") is not True
        and value.get("session_end_complete") is not True
    ):
        return RestoreDecision("resume_session_end")
    if value.get("trigger_model") is True and not has_assistant():
        return RestoreDecision("resume_model")
    return RestoreDecision("idle")
