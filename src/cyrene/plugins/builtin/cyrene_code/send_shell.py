"""Tool implementation for SendShell."""

from __future__ import annotations

import re
from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import (
    guard_shell_command_workspace_write,
    json_result,
    plugin_localized,
)

from .definitions import get_native_tool_def
from .services import terminal_service

TOOL_NAME = 'SendShell'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


_TERMINAL_KEYS = {
    "enter": "\r",
    "escape": "\x1b",
    "tab": "\t",
    "shift_tab": "\x1b[Z",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[H",
    "end": "\x1b[F",
    "insert": "\x1b[2~",
    "delete": "\x1b[3~",
    "page_up": "\x1b[5~",
    "page_down": "\x1b[6~",
    "backspace": "\x7f",
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
    "ctrl_space": "\x00",
}


def _terminal_key_sequence(key: str) -> str:
    normalized = str(key or "").strip().lower()
    if normalized in _TERMINAL_KEYS:
        return _TERMINAL_KEYS[normalized]
    if normalized.startswith("ctrl_") and len(normalized) == 6:
        letter = normalized[-1]
        if "a" <= letter <= "z":
            return chr(ord(letter) - ord("a") + 1)
    return ""


def _screen_accepts_sensitive_input(screen_text: str) -> bool:
    """Limit the sensitive-input bypass to a visible credential prompt."""
    lines = [line.strip() for line in str(screen_text or "").splitlines() if line.strip()]
    tail = "\n".join(lines[-4:])
    return bool(re.search(
        r"(?:password|passphrase|passcode|pin|密码|口令|验证码)"
        r"(?:\s+for\s+[^:\n]+)?\s*[:：]?\s*$",
        tail,
        re.IGNORECASE,
    ))


async def _tool_send_shell(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    import asyncio

    command = str(args.get("text", args.get("command", "")) or "")
    sensitive = bool(args.get("sensitive"))
    if not sensitive:
        try:
            guard_shell_command_workspace_write(command, context)
        except ValueError:
            raise ValueError(plugin_localized(
                context,
                "The command was blocked because its workspace write targets "
                "could not be verified safely.",
                "该命令已被阻止，因为无法安全确认其写入目标位于工作区内。",
            )) from None
    terminals = terminal_service(context)
    terminal = await terminals.resolve(
        context,
        terminal_id=str(args.get("shell_id") or ""),
        name=str(args.get("name") or ""),
    )
    if sensitive:
        before = await terminals.screen(str(terminal.get("id") or ""))
        if not _screen_accepts_sensitive_input(str(before.get("screenText") or "")):
            raise ValueError(
                plugin_localized(
                    context,
                    "sensitive=true is allowed only while the terminal visibly requests "
                    "a password, passphrase, passcode, PIN, or verification code.",
                    "仅当终端明确要求输入密码、口令、PIN 或验证码时，"
                    "才允许使用 sensitive=true。",
                )
            )
    key = str(args.get("key") or "").strip().lower()
    data = command + _terminal_key_sequence(key)
    if not data:
        raise ValueError(plugin_localized(
            context,
            "text or key is required.",
            "必须提供 text 或 key。",
        ))
    await terminals.animate(context, str(terminal.get("id") or ""), "input")
    snap = await terminals.input(str(terminal.get("id") or ""), data)
    await asyncio.sleep(0.12)
    snap = await terminals.screen(str(terminal.get("id") or ""))
    terminal_state = snap.get("terminal") or {}
    return json_result({
        "shell_id": terminal.get("id", ""),
        "terminal_id": terminal.get("id", ""),
        "status": terminal_state.get("status", ""),
        "screen_text": snap.get("screenText", ""),
        "cursor": snap.get("cursor", {}),
        "last_actor": terminal_state.get("lastActor", ""),
        "input_event_count": terminal_state.get("inputEventCount", 0),
    })


handler = _tool_send_shell

__all__ = [
    "TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_shell",
    "_screen_accepts_sensitive_input", "_terminal_key_sequence",
]
