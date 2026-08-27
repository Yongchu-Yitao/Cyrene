"""Tool implementation for browser_click_at."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

TOOL_NAME = "browser_click_at"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Click the current browser page at viewport coordinates. Use only when snapshot refs/selectors are insufficient and a screenshot provides reliable coordinates.",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Viewport x coordinate."},
                "y": {"type": "integer", "description": "Viewport y coordinate."},
            },
            "required": ["x", "y"],
        },
    },
}


async def _tool_browser_click_at(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import click_at

    try:
        x = int(args.get("x"))
        y = int(args.get("y"))
    except (TypeError, ValueError):
        return plugin_localized(
            context,
            "Click failed: invalid coordinates.",
            "点击失败：坐标无效。",
        )
    result = await click_at(x, y)
    if result.get("ok"):
        from .browser_output import page_observation_lines

        parts = [
            plugin_localized(context, "Clicked at {x},{y}.", "已点击坐标 {x},{y}。", x=x, y=y),
            plugin_localized(context, "URL: {url}", "网址：{url}", url=result.get("url", "—")),
            plugin_localized(context, "Title: {title}", "标题：{title}", title=result.get("title", "—")),
        ]
        parts.extend(page_observation_lines(result, context))
        return "\n".join(parts)
    from .browser_output import browser_error_text, file_chooser_instruction
    chooser = file_chooser_instruction(result, context)
    if chooser:
        return chooser
    return plugin_localized(
        context,
        "Click failed: {error}",
        "点击失败：{error}",
        error=browser_error_text(
            result,
            context,
            "The browser page could not be clicked at those coordinates.",
            "无法在这些坐标点击浏览器页面。",
        ),
    )


handler = _tool_browser_click_at

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_click_at"]
