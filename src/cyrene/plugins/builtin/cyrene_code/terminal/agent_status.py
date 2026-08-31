"""Coding-agent identity and lifecycle event normalization.

Native CLI hooks are the only source of lifecycle truth.  Cyrene does not infer
working or waiting from process activity, CPU use, terminal text, or timers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


AGENT_STATES = frozenset({
    "idle", "working", "waiting", "completed", "failed", "interrupted",
})


@dataclass(frozen=True, slots=True)
class AgentAdapter:
    id: str
    label: str


# Harness names, not model providers. Using a MiniMax model from Claude Code
# still makes the lifecycle owner Claude Code; MiniMax's ``mmx`` utility is not
# itself a coding-agent harness.
AGENT_ADAPTERS: tuple[AgentAdapter, ...] = (
    AgentAdapter("claude", "Claude Code"),
    AgentAdapter("codex", "Codex CLI"),
    AgentAdapter("gemini", "Gemini CLI"),
    AgentAdapter("opencode", "OpenCode"),
    AgentAdapter("kimi", "Kimi Code"),
    AgentAdapter("minimax", "MiniMax Code"),
    AgentAdapter("aider", "Aider"),
    AgentAdapter("qwen", "Qwen Code"),
    AgentAdapter("copilot", "GitHub Copilot CLI"),
    AgentAdapter("goose", "Goose"),
    AgentAdapter("amp", "Amp"),
)


def adapter_by_id(agent_id: str) -> AgentAdapter | None:
    normalized = str(agent_id or "").strip().casefold().replace("_", "-")
    aliases = {
        "claude-code": "claude",
        "codex-cli": "codex",
        "gemini-cli": "gemini",
        "kimi-code": "kimi",
        "kimi-code-cli": "kimi",
        "kimi-code-client": "kimi",
        "minimax-code": "minimax",
        "qwen-code": "qwen",
        "github-copilot": "copilot",
    }
    normalized = aliases.get(normalized, normalized)
    return next((adapter for adapter in AGENT_ADAPTERS if adapter.id == normalized), None)


def _event_names(event: str, payload: dict[str, Any]) -> Iterable[str]:
    yield str(event or "")
    for key in (
        "hook_event_name", "event", "event_name", "type", "notification_type",
        "reason", "status",
    ):
        value = payload.get(key)
        if value is not None:
            yield str(value)


def normalize_agent_event(
    agent_id: str, event: str, payload: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return ``(state, canonical_event)`` for vendor lifecycle payloads."""
    del agent_id  # Event aliases are intentionally shared across adapters.
    body = dict(payload or {})
    names = []
    for name in _event_names(event, body):
        if not str(name or "").strip():
            continue
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name))
        names.append(re.sub(r"[^a-z0-9]+", "_", snake.casefold()).strip("_"))
    states = {
        "working": {
            "working", "turn_started", "user_prompt_submit", "pre_tool_use",
            "post_tool_use", "permission_result", "before_agent", "before_tool",
            "after_tool", "before_model", "after_model", "resume",
        },
        "waiting": {
            "waiting", "permission_request", "approval_request",
            "approval_required", "waiting_for_user", "input_required",
        },
        "completed": {
            "completed", "stop", "after_agent", "turn_completed",
            "task_completed", "session_end",
        },
        "failed": {
            "failed", "stop_failure", "tool_failure", "session_error",
            "turn_error", "error",
        },
        "interrupted": {"interrupted", "interrupt", "cancelled", "canceled", "aborted"},
        "idle": {"idle", "ready", "session_start"},
    }
    for name in names:
        for state, events in states.items():
            if name in events:
                return state, names[0]
    raise ValueError("unsupported coding-agent lifecycle event")


__all__ = [
    "AGENT_ADAPTERS", "AGENT_STATES", "AgentAdapter", "adapter_by_id",
    "normalize_agent_event",
]
