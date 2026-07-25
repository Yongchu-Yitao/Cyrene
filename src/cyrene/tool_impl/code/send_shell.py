"""Tool implementation for SendShell."""

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
    request_scope_elevation,
    request_write_elevation,
    send_shell_session,
)

TOOL_NAME = 'SendShell'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_shell(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.agent.context import has_temporary_full_access
    command = str(args.get("command", ""))
    _full_access = has_temporary_full_access()
    if not _full_access and is_dangerous_subshell(command):
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
    snap = await send_shell_session(
        str(args.get("shell_id", "")),
        command,
        wait_ms=int(args.get("wait_ms", 700) or 700),
    )
    return json_result({
        "shell_id": snap.get("id", ""),
        "status": snap.get("status", ""),
        "elapsed": snap.get("elapsed", "—"),
        "lines": snap.get("lines", [])[-20:],
    })


handler = _tool_send_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_shell"]
