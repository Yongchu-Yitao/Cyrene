"""Coding-agent identity and lifecycle event normalization.

Shell command boundaries provide a common active/ended lifecycle for every
supported harness. Native CLI hooks may refine that state (for example,
permission waiting). Cyrene never guesses lifecycle from arbitrary command
output, CPU activity, or timers.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import PurePath
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


_COMMAND_AGENT_IDS = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "opencode": "opencode",
    "kimi": "kimi",
    "kimi-cli": "kimi",
    "kimi-code": "kimi",
    "minimax": "minimax",
    "minimax-code": "minimax",
    "aider": "aider",
    "qwen": "qwen",
    "qwen-code": "qwen",
    "copilot": "copilot",
    "goose": "goose",
    "amp": "amp",
}
_COMMAND_WRAPPERS = frozenset({"command", "exec", "env", "nohup", "nice", "sudo"})
_RUN_WRAPPERS = frozenset({"poetry", "pipenv", "uv"})
_WRAPPER_OPTIONS_WITH_VALUE = {
    "env": frozenset({"-C", "--chdir", "-S", "--split-string", "-u", "--unset"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "sudo": frozenset({
        "-C", "--close-from", "-D", "--chdir", "-g", "--group",
        "-h", "--host", "-p", "--prompt", "-R", "--chroot",
        "-r", "--role", "-t", "--type", "-u", "--user",
    }),
}


def _command_name(value: str) -> str:
    normalized = str(value or "").replace("\\", "/")
    return PurePath(normalized).name.casefold().removesuffix(".exe")


def command_tokens(command: str) -> list[str]:
    """Return the effective command tokens without common process wrappers."""
    try:
        tokens = shlex.split(str(command or ""), posix=True)
    except ValueError:
        tokens = str(command or "").strip().split()
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens.pop(0)
    while tokens:
        name = _command_name(tokens[0])
        if name in _RUN_WRAPPERS and len(tokens) > 1 and tokens[1] == "run":
            tokens = tokens[2:]
            continue
        if name not in _COMMAND_WRAPPERS:
            break
        tokens = tokens[1:]
        while tokens:
            token = tokens[0]
            if token == "--":
                tokens.pop(0)
                break
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
                tokens.pop(0)
                continue
            if not token.startswith("-"):
                break
            option = tokens.pop(0).split("=", 1)[0]
            if option in _WRAPPER_OPTIONS_WITH_VALUE.get(name, ()) and tokens:
                tokens.pop(0)
    return tokens


def command_title(command: str) -> str:
    """Return a short, argument-free title for the latest shell command."""
    tokens = command_tokens(command)
    if not tokens:
        return ""
    name = _command_name(tokens[0])
    if name == "npx" and len(tokens) > 1:
        return _command_name(tokens[1])[:60]
    if name == "gh" and len(tokens) > 1 and tokens[1].casefold() == "copilot":
        return "gh copilot"
    if name in {"python", "python3", "py"} and len(tokens) > 2 and tokens[1] == "-m":
        return _command_name(tokens[2])[:60]
    return name[:60]


def adapter_for_command(command: str) -> AgentAdapter | None:
    """Identify a supported coding-agent harness from a shell command."""
    tokens = command_tokens(command)
    if not tokens:
        return None
    name = _command_name(tokens[0])
    if name == "npx" and len(tokens) > 1:
        package = tokens[1].casefold()
        package_aliases = {
            "@anthropic-ai/claude-code": "claude",
            "@openai/codex": "codex",
            "@google/gemini-cli": "gemini",
            "opencode-ai": "opencode",
        }
        return adapter_by_id(package_aliases.get(package, ""))
    if name == "gh" and len(tokens) > 1 and tokens[1].casefold() == "copilot":
        return adapter_by_id("copilot")
    if name in {"python", "python3", "py"} and len(tokens) > 2 and tokens[1] == "-m":
        name = _command_name(tokens[2])
    return adapter_by_id(_COMMAND_AGENT_IDS.get(name, name))


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
    "adapter_for_command", "command_title", "command_tokens",
    "normalize_agent_event",
]
