"""Editable input schemas for the ContextTree-backed subagent tools."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_TOOL_DEFS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": (
                "Main agent only. Create an independent AgentSession with a new "
                "ContextTree, copy the main agent's initial root context, and append "
                "the supplied task as its first instruction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Unique identifier for the subagent.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Instruction from the main agent.",
                    },
                },
                "required": ["agent_id", "task"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_agent_message",
            "description": (
                "Send a message to the main agent or another subagent through the "
                "session-scoped inbox."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Target agent ID, or 'main'.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Message content.",
                    },
                },
                "required": ["to", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "broadcast_agent_message",
            "description": "Send one message to every other agent in the current session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Message content.",
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_round",
            "description": "Main agent only. List this session's subagents and status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "round_id": {
                        "type": "string",
                        "description": "Optional run ID filter.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
)

_TOOL_DEFS_BY_NAME = {
    str(item["function"]["name"]): item
    for item in _TOOL_DEFS
}


def get_native_tool_def(name: str) -> dict[str, Any]:
    """Return a pack-local copy of one declared tool schema."""

    target = str(name)
    try:
        definition = _TOOL_DEFS_BY_NAME[target]
    except KeyError as exc:
        raise KeyError(f"unknown local tool definition: {target}") from exc
    return deepcopy(definition)


__all__ = ["get_native_tool_def"]
