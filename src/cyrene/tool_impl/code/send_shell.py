"""Tool implementation for SendShell."""

from __future__ import annotations

import re
from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    classify_destructive_shell_command,
    command_is_file_deletion,
    guard_shell_command_workspace_write,
    is_dangerous_subshell,
    json_result,
    request_delete_confirmation,
    request_destructive_confirmation,
    request_scope_elevation,
    request_write_elevation,
)

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


async def _tool_send_shell(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    import asyncio
    from cyrene.terminal.client import get_terminal_daemon_client
    from cyrene.tooling.backends.terminals import animate_terminal_control, resolve_terminal

    from cyrene.agent.context import has_temporary_full_access
    command = str(args.get("text", args.get("command", "")) or "")
    sensitive = bool(args.get("sensitive"))
    _full_access = has_temporary_full_access()
    if not sensitive and not _full_access and is_dangerous_subshell(command):
        elev = await request_scope_elevation(
            tool_name="SendShell",
            path_hint="",
            operation="包含命令替换的 Shell 操作",
            reason=f"命令包含 $() 或反引号，其展开路径无法静态验证。\n命令：{command[:240]}",
            permission_kind="subshell_elevation",
            options=["允许执行", "拒绝"],
            scope_hint="",
        )
        if elev is not None:
            return elev
    if not sensitive:
        try:
            guard_shell_command_workspace_write(command)
        except ValueError:
            elev = await request_write_elevation(tool_name="SendShell", path_hint="", reason=command[:240])
            if elev is not None:
                return elev
        destructive = classify_destructive_shell_command(command)
        if destructive is not None:
            delete_result = await request_destructive_confirmation(
                tool_name="SendShell",
                operation=destructive["operation"],
                detail=destructive["detail"],
                destructive_kind=destructive["kind"],
            )
            if delete_result is not None:
                return delete_result
        elif command_is_file_deletion(command):
            delete_result = await request_delete_confirmation(tool_name="SendShell", command=command)
            if delete_result is not None:
                return delete_result
    terminal = await resolve_terminal(
        terminal_id=str(args.get("shell_id") or ""),
        name=str(args.get("name") or ""),
        access="write",
    )
    client = get_terminal_daemon_client()
    if sensitive:
        before = await client.screen(str(terminal.get("id") or ""))
        if not _screen_accepts_sensitive_input(str(before.get("screenText") or "")):
            raise ValueError(
                "sensitive=true is allowed only while the terminal visibly requests "
                "a password, passphrase, passcode, PIN, or verification code."
            )
    key = str(args.get("key") or "").strip().lower()
    data = command + _terminal_key_sequence(key)
    if not data:
        raise ValueError("text or key is required")
    await animate_terminal_control(str(terminal.get("id") or ""), "input")
    snap = await client.input(str(terminal.get("id") or ""), data)
    await asyncio.sleep(0.12)
    snap = await client.screen(str(terminal.get("id") or ""))
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
