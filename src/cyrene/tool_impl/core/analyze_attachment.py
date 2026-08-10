"""Tool implementation for AnalyzeAttachment."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    json_result,
    request_read_elevation,
    resolve_tool_path,
    analyze_attachment,
)

TOOL_NAME = 'AnalyzeAttachment'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_analyze_attachment(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    try:
        path = resolve_tool_path(str(args["path"]))
    except ValueError:
        elev = await request_read_elevation(
            tool_name="AnalyzeAttachment",
            path_hint=str(args.get("path", "")),
            reason="Agent 想要分析此文件内容。",
        )
        if elev is not None:
            return elev
        path = resolve_tool_path(str(args["path"]))
    prompt = str(args.get("prompt", "") or "")
    force_refresh = bool(args.get("force_refresh", False))
    try:
        result = await analyze_attachment(str(path), prompt=prompt, force_refresh=force_refresh)
    except FileNotFoundError:
        return json_result({
            "error": "attachment_unavailable",
            "path": str(path),
            "message": "The uploaded attachment is no longer available. Ask the user to upload it again.",
            "action": "stop_attachment_analysis",
            "search_elsewhere": False,
        })
    return json_result(result)


handler = _tool_analyze_attachment

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_analyze_attachment"]
