"""Tool implementation for StartShell."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import (
    guard_shell_command_workspace_write,
    json_result,
    plugin_localized,
    resolve_tool_path,
    run_context_value,
)

from .definitions import get_native_tool_def
from .services import requested_terminal_title, terminal_service

TOOL_NAME = 'StartShell'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_start_shell(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    cwd_arg = str(args.get("cwd", ".") or ".")
    try:
        cwd_path = resolve_tool_path(cwd_arg, context)
    except (RuntimeError, ValueError):
        raise ValueError(plugin_localized(
            context,
            "The requested working directory is not available in this workspace.",
            "请求的工作目录在当前工作区中不可用。",
        )) from None
    cwd = str(cwd_path)
    command = str(args.get("command", "") or "")
    ssh_target = str(args.get("ssh_target", "") or "").strip()
    if command:
        try:
            guard_shell_command_workspace_write(command, context)
        except ValueError:
            raise ValueError(plugin_localized(
                context,
                "The command was blocked because its workspace write targets "
                "could not be verified safely.",
                "该命令已被阻止，因为无法安全确认其写入目标位于工作区内。",
            )) from None
    wake_on_exit = bool(args.get("wake_on_exit", False))
    wake_note = str(args.get("wake_note", "") or "")
    created = await terminal_service(context).create(
        context,
        command=command,
        cwd=cwd,
        title=requested_terminal_title(
            str(args.get("title", "") or ""),
            str(run_context_value(context, "user_request_text") or ""),
        ),
        wake_on_exit=wake_on_exit,
        wake_note=wake_note,
        owner_tool_call_id=str(
            run_context_value(context, "client_request_id")
            or run_context_value(context, "round_id")
            or ""
        ),
        ssh_target=ssh_target,
        remote_cwd=str(args.get("remote_cwd", "") or ""),
        tmux_session=str(args.get("tmux_session", "") or ""),
    )
    snap = dict(created.get("terminal") or {})
    result = {
        "shell_id": snap.get("id", ""),
        "terminal_id": snap.get("id", ""),
        "status": snap.get("status", ""),
        "cwd": (
            snap.get("remoteCwd", "") or snap.get("cwd", ".")
            if snap.get("connectionKind") == "ssh"
            else snap.get("cwd", ".")
        ),
        "title": snap.get("title") or plugin_localized(
            context,
            "Terminal",
            "终端",
        ),
        "owner_chat_id": snap.get("ownerChatId", ""),
        "wake_on_exit": bool(snap.get("wakeId")),
        "wake_id": snap.get("wakeId", ""),
        "execution_mode": snap.get("launchMode", "interactive"),
        "connection_kind": snap.get("connectionKind", "local"),
        "connection_status": snap.get("connectionStatus", "local"),
        "ssh_target": snap.get("sshTarget", ""),
        "remote_cwd": snap.get("remoteCwd", ""),
        "tmux_session": snap.get("tmuxSession", ""),
        "shown": False,
    }
    if result["wake_on_exit"]:
        if result["execution_mode"] == "one_shot":
            result["wake_hint"] = plugin_localized(
                context,
                "The command is running as a one-shot background job. Do not wait or poll. "
                "Finish this turn; an internal wake will remind you to read this terminal "
                "with code.shell.read when it completes.",
                "该命令正在作为一次性后台任务运行。请勿等待或轮询。结束当前轮次即可；"
                "任务完成后，内部唤醒会提醒你使用 code.shell.read 读取此终端。",
            )
        else:
            result["wake_hint"] = plugin_localized(
                context,
                "The terminal is running in the background. Do not wait or poll. "
                "It remains in the conversation terminal list and wakes this chat only "
                "when its process exits.",
                "终端正在后台运行。请勿等待或轮询。它会保留在当前会话的终端列表中，"
                "并仅在进程退出时唤醒此聊天。",
            )
    return json_result(result)


handler = _tool_start_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_start_shell"]
