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
                "Main agent only. Spawn an independent execution worker or a bounded "
                "discussion participant. Workers have separate ContextTrees and share "
                "only the session-scoped agent inbox."
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
                    "mode": {
                        "type": "string",
                        "enum": ["execution", "discussion"],
                        "description": "Worker mode; defaults to execution. Roles imply discussion.",
                    },
                    "success_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                        "description": "Concrete conditions that prove completion.",
                    },
                    "max_messages": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Optional per-agent discussion message cap.",
                    },
                    "discussion_id": {
                        "type": "string",
                        "description": "Stable shared discussion identifier; defaults to parent round.",
                    },
                    "use_secondary": {
                        "type": "boolean",
                        "description": "Route simple work to the configured secondary model.",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["moderator", "participant"],
                        "description": "Optional bounded-discussion role.",
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
            "name": "quit",
            "description": (
                "Subagent-only terminal protocol. First write the complete result in "
                "normal assistant content, then call quit alone."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "completion_status": {
                        "type": "string",
                        "enum": ["completed", "partial", "blocked"],
                    },
                    "criteria_evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "criterion": {"type": "string"},
                                "evidence": {"type": "string"},
                            },
                            "required": ["criterion", "evidence"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["completion_status"],
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
