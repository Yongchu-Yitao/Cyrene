"""Tool implementation for Edit."""

from __future__ import annotations

import asyncio
from typing import Any

from cyrene import tool_legacy as _legacy
from cyrene.tool_legacy import (
    _request_write_elevation,
    _resolve_workspace_write_target,
)

TOOL_NAME = 'Edit'
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


def _is_system_initiated_round() -> bool:
    try:
        from cyrene.agent.state import _ui_round_assistant_meta

        meta = _ui_round_assistant_meta.get()
        return isinstance(meta, dict) and bool(meta.get("system_initiated"))
    except Exception:
        return False


async def _tool_edit(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    if _is_system_initiated_round():
        return (
            "Tool unavailable: proactive system-initiated rounds may only do "
            "incremental file work. Editing existing files is forbidden."
        )
    from cyrene.settings_store import is_workspace_active
    if not is_workspace_active():
        return "Workspace access is disabled. Ask the user to add workspace via '+ add context' in the chat input, or set a workspace directory in Settings."
    try:
        path = _resolve_workspace_write_target(str(args["path"]))
    except ValueError:
        elev = await _request_write_elevation(tool_name="Edit", path_hint=str(args.get("path", "")))
        if elev is not None:
            return elev
        # 已放行（完全访问 / 审核 agent 批准）：full-access 已置位，重新解析即成功
        path = _resolve_workspace_write_target(str(args["path"]))
    old_string = str(args["old_string"])
    new_string = str(args["new_string"])
    replace_all = bool(args.get("replace_all", False))

    def edit_file() -> int:
        content = path.read_text(encoding="utf-8")
        occurrences = content.count(old_string)
        if occurrences == 0:
            raise ValueError("old_string not found")
        if occurrences > 1 and not replace_all:
            raise ValueError("old_string matched multiple times; set replace_all=true")
        updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        path.write_text(updated, encoding="utf-8")
        return occurrences

    occurrences = await asyncio.to_thread(edit_file)
    replaced = occurrences if replace_all else 1
    return f"Edited {path}. Replacements: {replaced}"


handler = _tool_edit

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_edit"]
