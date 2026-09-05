"""Shared projection rules for lifecycle-owned context nodes."""

from __future__ import annotations

import json
from copy import deepcopy
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


def project_model_messages(
    path: Sequence[Any],
    *,
    observation_services: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    """Project the effective model history, honoring every context replacement.

    Optional services materialize transient observations for the live model call;
    durable consumers share the same projection without storing binary content.
    """

    messages: list[dict[str, Any]] = []
    current_run_id = next(
        (
            str(node.value.get("run_id") or "")
            for node in reversed(path)
            if isinstance(node.value, Mapping)
            and node.value.get("role") == "user"
        ),
        "",
    )
    active_context_ids = selected_context_node_ids(path, current_run_id)
    root_value = (
        path[0].value
        if path and isinstance(path[0].value, Mapping)
        else {}
    )
    base_system_content = str(root_value.get("content") or "")
    for node in path:
        value = node.value if isinstance(node.value, Mapping) else {}
        role = str(value.get("role") or "")
        if role in {"context_compaction", "context_reflection"}:
            compacted = value.get("messages")
            if isinstance(compacted, list) and all(
                isinstance(message, Mapping) for message in compacted
            ):
                messages = [deepcopy(dict(message)) for message in compacted]
                if str(value.get("run_id") or "") != current_run_id:
                    system = next(
                        (
                            message
                            for message in messages
                            if str(message.get("role") or "") == "system"
                            and message.get("compacted_block") is not True
                        ),
                        None,
                    )
                    if system is None and base_system_content:
                        messages.insert(
                            0,
                            {
                                "role": "system",
                                "content": base_system_content,
                            },
                        )
                    elif system is not None:
                        system["content"] = base_system_content
            continue
        if role in {"system", "user"}:
            content = str(value.get("content") or "")
            messages.append({"role": role, "content": content})
        elif role == "context":
            if node.id not in active_context_ids:
                continue
            content = str(value.get("content") or "").strip()
            if not content:
                continue
            project_context_message(messages, value)
        elif role == "assistant":
            message: dict[str, Any] = {
                "role": "assistant",
                "content": str(value.get("content") or ""),
            }
            calls = value.get("tool_calls")
            if isinstance(calls, list) and calls:
                message["tool_calls"] = [
                    {
                        "id": str(call.get("id") or ""),
                        "type": "function",
                        "function": {
                            "name": str(call.get("name") or ""),
                            "arguments": json.dumps(
                                call.get("arguments") or {},
                                ensure_ascii=False,
                                default=str,
                            ),
                        },
                    }
                    for call in calls
                    if isinstance(call, Mapping)
                ]
                reasoning_details = value.get("reasoning_details")
                if isinstance(reasoning_details, list) and reasoning_details:
                    message["reasoning_details"] = deepcopy(reasoning_details)
            messages.append(message)
        elif role == "tool_results":
            results = value.get("results")
            observations: list[dict[str, Any]] = []
            for result in results if isinstance(results, list) else ():
                if not isinstance(result, Mapping):
                    continue
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(result.get("call_id") or ""),
                        "name": str(result.get("name") or ""),
                        "content": json.dumps(
                            {
                                "success": bool(result.get("success")),
                                "value": result.get("value"),
                                "error": str(result.get("error") or ""),
                                **(
                                    {"failure": result.get("failure")}
                                    if isinstance(result.get("failure"), Mapping)
                                    else {}
                                ),
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                )
                observation = None
                # Application services may contribute a managed multimodal
                # result adapter. The first service that recognizes this
                # tool value owns materialization. This keeps binary pixels
                # out of the durable Plugin result while allowing MCP and
                # user-authorized live resources to share the model path.
                for observation_service in observation_services:
                    builder = getattr(
                        observation_service,
                        "build_observation_content",
                        None,
                    )
                    if not callable(builder):
                        continue
                    observation = builder(
                        result.get("value"),
                        tool_name=str(result.get("name") or ""),
                    )
                    if not observation:
                        continue
                    materialize = getattr(
                        observation_service,
                        "materialize_content_block",
                        None,
                    )
                    if callable(materialize):
                        observation = [materialize(block) for block in observation]
                    break
                if observation:
                    observations.append(
                        {
                            "role": "user",
                            "content": observation,
                            "ephemeral_model_observation": True,
                        }
                    )
            messages.extend(observations)
    return messages


__all__ = [
    "project_model_messages",
    "context_is_turn",
    "project_context_message",
    "selected_context_node_ids",
]
