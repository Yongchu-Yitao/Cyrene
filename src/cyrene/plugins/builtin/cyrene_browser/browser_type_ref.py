"""Tool implementation for browser_type_ref."""

from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized

TOOL_NAME = "browser_type_ref"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Type text into an editable element returned by browser_snapshot using its ref.",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref from browser_snapshot, such as e3."},
                "text": {"type": "string", "description": "Text to type."},
                "submit": {"type": "boolean", "description": "Press Enter after typing. Default false."},
            },
            "required": ["ref", "text"],
        },
    },
}


async def _tool_browser_type_ref(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import type_ref

    ref = str(args.get("ref") or "").strip()
    text = str(args.get("text") or "")
    submit = bool(args.get("submit", False))
    if not ref:
        return plugin_localized(
            context,
            "Typing failed: no element ref was provided.",
            "输入失败：未提供元素 ref。",
        )
    result = await type_ref(ref, text, submit=submit)
    if result.get("ok"):
        return plugin_localized(
            context,
            "Typed into {ref}.\nURL: {url}\nTitle: {title}",
            "已在 {ref} 中输入文本。\n网址：{url}\n标题：{title}",
            ref=ref,
            url=result.get("url", "—"),
            title=result.get("title", "—"),
        )
    from .browser_output import browser_error_text

    return plugin_localized(
        context,
        "Typing failed: {error}",
        "输入失败：{error}",
        error=browser_error_text(
            result,
            context,
            "The browser could not enter text in that element.",
            "浏览器无法在该元素中输入文本。",
        ),
    )


handler = _tool_browser_type_ref

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_type_ref"]
