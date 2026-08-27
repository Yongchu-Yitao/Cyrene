from __future__ import annotations

from typing import Any, Collection

from agent.plugin import PluginContext

from ._ui_action import execute_action


def gesture_tool_def(name: str, description: str) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "snapshot_id": {"type": "string", "maxLength": 160},
                "revision": {"type": "integer", "minimum": 1},
                "node_id": {"type": "string", "maxLength": 160},
                "action_id": {"type": "string", "maxLength": 100},
                "input": {"type": "object"},
                "reason": {"type": "string", "maxLength": 500},
                "idempotency_key": {"type": "string", "maxLength": 160},
            },
            "required": ["snapshot_id", "revision", "node_id", "action_id", "reason", "idempotency_key"],
            "additionalProperties": False,
        },
    }}


async def run_gesture(
    operation_id: str,
    allowed_kinds: Collection[str],
    args: dict[str, Any],
    context: PluginContext,
    *,
    required_gesture_aliases: Collection[str] | None = None,
) -> str:
    return await execute_action(
        args,
        context,
        operation_family=operation_id,
        allowed_kinds=frozenset(allowed_kinds),
        required_gesture_aliases=(
            frozenset(required_gesture_aliases)
            if required_gesture_aliases is not None
            else None
        ),
    )


__all__ = ["gesture_tool_def", "run_gesture"]
