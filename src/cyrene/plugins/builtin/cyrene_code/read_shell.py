"""Tool implementation for ReadShell."""

from __future__ import annotations

import base64
import re
from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import json_result, plugin_localized

from .definitions import get_native_tool_def
from .services import terminal_service

TOOL_NAME = "ReadShell"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"read_only": True}

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|P.*?\x1b\\|[@-_])",
    re.DOTALL,
)


def _plain_scrollback_text(data: bytes) -> str:
    text = bytes(data).decode("utf-8", errors="replace")
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    while "\x08" in text:
        text = re.sub(r"[^\n]\x08", "", text).replace("\x08", "")
    return text


async def _tool_read_shell(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    terminals = terminal_service(context)
    terminal = await terminals.resolve(
        context,
        terminal_id=str(args.get("shell_id") or ""),
        name=str(args.get("name") or ""),
    )
    view = str(args.get("view") or "screen").strip().lower()
    if view not in {"screen", "scrollback", "commands", "command_output"}:
        raise ValueError(
            plugin_localized(
                context,
                "view must be 'screen', 'scrollback', 'commands', or 'command_output'.",
                "view 必须是 'screen'、'scrollback'、'commands' 或 'command_output'。",
            )
        )
    terminal_id = str(terminal.get("id") or "")
    if view == "commands":
        payload = await terminals.commands(terminal_id)
        return json_result({
            "shell_id": terminal_id,
            "terminal_id": terminal_id,
            "title": terminal.get("title") or plugin_localized(
                context,
                "Terminal",
                "终端",
            ),
            "source": "commands",
            "commands": list(payload.get("commands") or []),
        })
    if view == "command_output":
        command_id = str(args.get("command_id") or "").strip()
        if not command_id:
            raise ValueError(plugin_localized(
                context,
                "command_id is required for view=command_output.",
                "当 view=command_output 时必须提供 command_id。",
            ))
        payload = await terminals.command_output(terminal_id, command_id)
        return json_result({
            "shell_id": terminal_id,
            "terminal_id": terminal_id,
            "title": terminal.get("title") or plugin_localized(
                context,
                "Terminal",
                "终端",
            ),
            "source": "command_output",
            "command": payload.get("command") or {},
            "text": str(payload.get("text") or ""),
        })
    if view == "scrollback":
        requested_cursor = args.get("cursor")
        snap = await terminals.scrollback(
            terminal_id,
            cursor=(int(requested_cursor) if requested_cursor is not None else None),
            max_bytes=int(args.get("max_bytes") or 64 * 1024),
        )
        terminal_state = snap.get("terminal") or {}
        data = base64.b64decode(str(snap.get("data") or ""), validate=True)
        text = _plain_scrollback_text(data)
        return json_result({
            "shell_id": terminal.get("id", ""),
            "terminal_id": terminal.get("id", ""),
            "title": terminal.get("title") or plugin_localized(
                context,
                "Terminal",
                "终端",
            ),
            "status": terminal_state.get("status", ""),
            "source": "scrollback",
            "range": {
                "requested_start_seq": snap.get("requestedStartSeq"),
                "start_seq": snap.get("startSeq"),
                "end_seq": snap.get("endSeq"),
                "oldest_seq": snap.get("oldestSeq"),
                "next_seq": snap.get("nextSeq"),
            },
            "truncated": bool(snap.get("truncated")),
            "truncated_before": bool(snap.get("truncatedBefore")),
            "truncated_after": bool(snap.get("truncatedAfter")),
            "text": text,
            "scrollback_text": text,
            "byte_count": len(data),
            "last_actor": terminal_state.get("lastActor", ""),
            "last_input_at": terminal_state.get("lastInputAt", ""),
            "input_event_count": terminal_state.get("inputEventCount", 0),
            "connection_kind": terminal_state.get("connectionKind", "local"),
            "connection_status": terminal_state.get("connectionStatus", "local"),
            "ssh_target": terminal_state.get("sshTarget", ""),
            "remote_cwd": terminal_state.get("remoteCwd", ""),
            "tmux_session": terminal_state.get("tmuxSession", ""),
        })

    snap = await terminals.screen(terminal_id)
    terminal_state = snap.get("terminal") or {}
    screen_text = snap.get("screenText", "")
    rendered_lines = str(screen_text).splitlines()
    return json_result({
        "shell_id": terminal.get("id", ""),
        "terminal_id": terminal.get("id", ""),
        "title": terminal.get("title") or plugin_localized(
            context,
            "Terminal",
            "终端",
        ),
        "status": terminal_state.get("status", ""),
        "rows": snap.get("rows"),
        "cols": snap.get("cols"),
        "cursor": snap.get("cursor", {}),
        "source": "screen",
        "range": {
            "start_row": 0,
            "end_row": max(0, len(rendered_lines) - 1) if rendered_lines else None,
            "rendered_rows": len(rendered_lines),
            "terminal_rows": snap.get("rows"),
            "terminal_cols": snap.get("cols"),
        },
        "truncated": False,
        "text": screen_text,
        "screen_text": screen_text,
        "last_actor": terminal_state.get("lastActor", ""),
        "last_input_at": terminal_state.get("lastInputAt", ""),
        "input_event_count": terminal_state.get("inputEventCount", 0),
        "connection_kind": terminal_state.get("connectionKind", "local"),
        "connection_status": terminal_state.get("connectionStatus", "local"),
        "ssh_target": terminal_state.get("sshTarget", ""),
        "remote_cwd": terminal_state.get("remoteCwd", ""),
        "tmux_session": terminal_state.get("tmuxSession", ""),
    })


handler = _tool_read_shell

__all__ = [
    "TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_plain_scrollback_text",
    "_tool_read_shell",
]
