"""Slash-command parsing for agent entrypoints."""

from __future__ import annotations

from typing import Any

DEEP_REFLECT_COMMAND_ID = "deep-reflect"
DEEP_REFLECT_SLASHES = ("/deep-reflect", "/深度反思")
BUILTIN_COMMAND_IDS = (
    "quick-answer",
    "deep-research",
    DEEP_REFLECT_COMMAND_ID,
    "help-me-decide",
    "learning-plan",
    "daily-review",
    "deep-compare",
    "terminal",
)


def parse_slash_command(
    text: str,
    *,
    allowed_commands: tuple[str, ...] | list[str] | set[str] = BUILTIN_COMMAND_IDS,
) -> dict[str, Any]:
    """Parse an exact ``/command`` prefix and preserve its public text."""

    source = str(text or "").strip()
    allowed = {str(item or "").strip() for item in allowed_commands}
    aliases = {"深度反思": DEEP_REFLECT_COMMAND_ID}
    if not source.startswith("/"):
        return {"matched": False, "command": "", "arguments": "", "public_text": source}
    parts = source[1:].split(None, 1)
    head = parts[0] if parts else ""
    tail = parts[1] if len(parts) > 1 else ""
    command = aliases.get(head, head)
    if command not in allowed:
        return {"matched": False, "command": "", "arguments": "", "public_text": source}
    return {
        "matched": True,
        "command": command,
        "arguments": tail.strip(),
        "public_text": source,
    }


def parse_slash_invocation(text: str) -> dict[str, Any]:
    """Parse slash syntax without deciding whether the command is registered."""

    source = str(text or "").strip()
    if not source.startswith("/"):
        return {"matched": False, "command": "", "arguments": "", "public_text": source}
    parts = source[1:].split(None, 1)
    command = parts[0] if parts else ""
    if not command:
        return {"matched": False, "command": "", "arguments": "", "public_text": source}
    return {
        "matched": True,
        "command": command,
        "arguments": parts[1].strip() if len(parts) > 1 else "",
        "public_text": source,
    }


def parse_deep_reflect_command(text: str) -> dict[str, Any]:
    """Parse a deep-reflect slash command.

    Returns a small dict rather than a dataclass so call sites can pass the
    result through JSON-oriented code without extra conversion.
    """
    parsed = parse_slash_command(text, allowed_commands=(DEEP_REFLECT_COMMAND_ID,))
    return {
        "matched": bool(parsed["matched"]),
        "command": str(parsed["command"]),
        "focus": str(parsed["arguments"]),
        "public_text": str(parsed["public_text"]),
    }


def is_deep_reflect_command(command: str, text: str = "") -> bool:
    return str(command or "").strip() == DEEP_REFLECT_COMMAND_ID or bool(parse_deep_reflect_command(text).get("matched"))


def parse_deep_reflect_focus(text: str) -> str:
    return str(parse_deep_reflect_command(text).get("focus") or "").strip()
