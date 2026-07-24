"""Tool implementation for StartShell."""

from __future__ import annotations

from typing import Any

from cyrene import tool_legacy as _legacy
from cyrene.tool_legacy import (
    _classify_destructive_shell_command,
    _command_is_file_deletion,
    _guard_shell_command_workspace_write,
    _is_dangerous_subshell,
    _json_result,
    _request_delete_confirmation,
    _request_destructive_confirmation,
    _request_read_elevation,
    _request_scope_elevation,
    _request_write_elevation,
    _resolve_tool_path,
    _start_shell_session,
    json,
)

TOOL_NAME = 'StartShell'
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_start_shell(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.agent.state import _current_round_id, _current_session_id

    cwd_arg = str(args.get("cwd", ".") or ".")
    try:
        cwd_path = _resolve_tool_path(cwd_arg)
    except ValueError:
        elev = await _request_read_elevation(
            tool_name="StartShell",
            path_hint=cwd_arg,
            reason="Agent 想要在 workspace 之外的目录启动 shell。",
        )
        if elev is not None:
            return elev
        cwd_path = _resolve_tool_path(cwd_arg)
    cwd = str(cwd_path)
    from cyrene.agent.state import _temporary_full_access
    command = str(args.get("command", "") or "")
    _full_access = _temporary_full_access.get()
    if command:
        if not _full_access and _is_dangerous_subshell(command):
            elev = await _request_scope_elevation(
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
            _guard_shell_command_workspace_write(command)
        except ValueError:
            elev = await _request_write_elevation(tool_name="StartShell", path_hint=cwd, reason=command[:240])
            if elev is not None:
                return elev
        destructive = _classify_destructive_shell_command(command)
        if destructive is not None:
            delete_result = await _request_destructive_confirmation(
                tool_name="StartShell",
                operation=destructive["operation"],
                detail=destructive["detail"],
                destructive_kind=destructive["kind"],
            )
            if delete_result is not None:
                return delete_result
        elif _command_is_file_deletion(command):
            delete_result = await _request_delete_confirmation(tool_name="StartShell", command=command)
            if delete_result is not None:
                return delete_result
    wake_on_exit = bool(args.get("wake_on_exit", False))
    wake_note = str(args.get("wake_note", "") or "")
    session_id = str(_current_session_id.get() or "").strip()
    snap = await _start_shell_session(
        command=command,
        cwd=cwd,
        title=str(args.get("title", "") or ""),
        round_id=_current_round_id.get(),
        wake_on_exit=wake_on_exit,
        wake_chat_id=session_id if wake_on_exit else "",
        wake_note=wake_note,
    )
    result = {
        "shell_id": snap.get("id", ""),
        "status": snap.get("status", ""),
        "cwd": snap.get("cwd", "."),
        "title": snap.get("title", "independent shell"),
        "wake_on_exit": bool(snap.get("wakeOnExit")),
        "wake_id": snap.get("wakeId", ""),
        "wake_chat_id": snap.get("wakeChatId", ""),
    }
    if wake_on_exit and not result["wake_on_exit"]:
        result["wake_error"] = (
            "wake_on_exit requested but no Workbench session_id is bound; "
            "shell started without an exit wake."
        )
    elif result["wake_on_exit"]:
        result["wake_hint"] = (
            "Shell is running in the background. Do not wait or poll. "
            "Quit this turn; you will be woken with the terminal output when it exits."
        )
    return _json_result(result)


handler = _tool_start_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_start_shell"]
