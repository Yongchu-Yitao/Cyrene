"""Shared projection rules for lifecycle-owned context nodes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def context_is_turn(value: Mapping[str, Any]) -> bool:
    """Return whether a context node belongs to one user-turn suffix."""

    lifecycle = str(value.get("context_lifecycle") or "").strip().lower()
    if lifecycle:
        return lifecycle == "turn"
    source = str(value.get("context_source") or "")
    kind = str(value.get("context_kind") or "")
    return (
        source == "TurnStart"
        or kind.startswith("turn_")
    )


def selected_context_node_ids(
    nodes: Sequence[Any],
    current_run_id: str,
) -> set[str]:
    """Select current stable mounts plus every historical turn's own suffix."""

    current_stable_by_kind: dict[str, str] = {}
    turn_by_run_kind: dict[tuple[str, str], str] = {}
    for node in nodes:
        value = getattr(node, "value", None)
        if not isinstance(value, Mapping) or value.get("role") != "context":
            continue
        node_id = str(getattr(node, "id", "") or "")
        run_id = str(value.get("run_id") or "")
        kind = str(value.get("context_kind") or node_id)
        if context_is_turn(value):
            turn_by_run_kind[(run_id, kind)] = node_id
        elif run_id == str(current_run_id or ""):
            current_stable_by_kind[kind] = node_id
    return {
        *current_stable_by_kind.values(),
        *turn_by_run_kind.values(),
    }


def project_context_message(
    messages: list[dict[str, Any]],
    value: Mapping[str, Any],
) -> None:
    """Project stable context to system and turn context to its preceding user."""

    content = str(value.get("content") or "").strip()
    if not content:
        return
    target_role = "user" if context_is_turn(value) else "system"
    target = next(
        (
            message
            for message in reversed(messages)
            if str(message.get("role") or "") == target_role
        ),
        None,
    )
    if target is None:
        message = {"role": target_role, "content": content}
        if target_role == "system":
            messages.insert(0, message)
        else:
            messages.append(message)
        return
    current = str(target.get("content") or "").strip()
    target["content"] = "\n\n".join(
        part for part in (current, content) if part
    )


__all__ = [
    "context_is_turn",
    "project_context_message",
    "selected_context_node_ids",
]
