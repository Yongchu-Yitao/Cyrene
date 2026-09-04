"""Tool implementation for AnalyzeAttachment."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext

from .definitions import get_native_tool_def
from cyrene.plugins.native_runtime import json_result, plugin_localized, resolve_tool_path
from cyrene.platform.attachments import analyze_attachment

TOOL_NAME = 'AnalyzeAttachment'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_analyze_attachment(args: dict[str, Any], context: PluginContext) -> str:
    path = resolve_tool_path(str(args["path"]))
    prompt = str(args.get("prompt", "") or "")
    force_refresh = bool(args.get("force_refresh", False))
    try:
        result = await analyze_attachment(
            str(path),
            prompt=prompt,
            force_refresh=force_refresh,
            context=context,
        )
    except FileNotFoundError:
        return json_result({
            "error": "attachment_unavailable",
            "path": str(path),
            "message": plugin_localized(
                context,
                "The uploaded attachment is no longer available. Ask the user to upload it again.",
                "上传的附件已不可用，请用户重新上传。",
            ),
            "action": "stop_attachment_analysis",
            "search_elsewhere": False,
        })
    return json_result(result)


handler = _tool_analyze_attachment

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_analyze_attachment"]
