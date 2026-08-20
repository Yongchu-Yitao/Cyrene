"""Tool implementation for StartShell."""

from __future__ import annotations

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
    request_read_elevation,
    request_scope_elevation,
    request_write_elevation,
    resolve_tool_path,
)

TOOL_NAME = 'StartShell'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_start_shell(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.tooling.backends.terminals import (
        agent_creation_scope,
        requested_terminal_title,
    )
    from cyrene.terminal.client import get_terminal_daemon_client

    context, project_id, session_id = agent_creation_scope()

    cwd_arg = str(args.get("cwd", ".") or ".")
    try:
        cwd_path = resolve_tool_path(cwd_arg)
    except ValueError:
        elev = await request_read_elevation(
            tool_name="StartShell",
            path_hint=cwd_arg,
            reason="Agent 想要在 workspace 之外的目录启动 shell。",
        )
        if elev is not None:
            return elev
        cwd_path = resolve_tool_path(cwd_arg)
    cwd = str(cwd_path)
    from cyrene.agent.context import has_temporary_full_access
    command = str(args.get("command", "") or "")
    _full_access = has_temporary_full_access()
    if command:
        if not _full_access and is_dangerous_subshell(command):
            elev = await request_scope_elevation(
                tool_name="StartShell",
                path_hint="",
                operation="包含命令替换的 Shell 操作",
                reason=f"命令包含 $() 或反引号，其展开路径无法静态验证。\n命令：{command[:240]}",
                permission_kind="subshell_elevation",
                options=["允许执行", "拒绝"],
                scope_hint="",
            )
            if elev is not None:
                return elev
        try:
            guard_shell_command_workspace_write(command)
        except ValueError:
            elev = await request_write_elevation(tool_name="StartShell", path_hint=cwd, reason=command[:240])
            if elev is not None:
                return elev
        destructive = classify_destructive_shell_command(command)
        if destructive is not None:
            delete_result = await request_destructive_confirmation(
                tool_name="StartShell",
                operation=destructive["operation"],
                detail=destructive["detail"],
                destructive_kind=destructive["kind"],
            )
            if delete_result is not None:
                return delete_result
        elif command_is_file_deletion(command):
            delete_result = await request_delete_confirmation(tool_name="StartShell", command=command)
            if delete_result is not None:
                return delete_result
    wake_on_exit = bool(args.get("wake_on_exit", False))
    wake_note = str(args.get("wake_note", "") or "")
    created = await get_terminal_daemon_client().create_agent_terminal(
        project_id,
        owner_chat_id=session_id,
        command=command,
        cwd=cwd,
        title=requested_terminal_title(
            str(args.get("title", "") or ""), context.user_request_text,
        ),
        wake_on_exit=wake_on_exit,
        wake_note=wake_note,
        owner_tool_call_id=str(context.client_request_id or context.round_id or ""),
    )
    snap = dict(created.get("terminal") or {})
    result = {
        "shell_id": snap.get("id", ""),
        "terminal_id": snap.get("id", ""),
        "status": snap.get("status", ""),
        "cwd": snap.get("cwd", "."),
        "title": snap.get("title", "Terminal"),
        "owner_chat_id": snap.get("ownerChatId", ""),
        "wake_on_exit": bool(snap.get("wakeId")),
        "wake_id": snap.get("wakeId", ""),
        "execution_mode": snap.get("launchMode", "interactive"),
        "shown": False,
    }
    if result["wake_on_exit"]:
        if result["execution_mode"] == "one_shot":
            result["wake_hint"] = (
                "The command is running as a one-shot background job. Do not wait or poll. "
                "Quit this turn; you will be woken with the terminal output when it completes."
            )
        else:
            result["wake_hint"] = (
                "The terminal is running in the background. Do not wait or poll. "
                "It remains in the conversation terminal list and wakes this chat only when its process exits."
            )
    return json_result(result)


handler = _tool_start_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_start_shell"]
