"""Tool implementation for browser_scroll."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

TOOL_NAME = "browser_scroll"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Scroll the page or a nested scrollable area under the target. Use when content is outside the visible area (e.g. load more results, see comments, or scroll a modal/sidebar).",
        "parameters": {
            "type": "object",
            "properties": {
                "delta_y": {"type": "integer", "description": "Pixels to scroll vertically. Positive=down, negative=up. Default 500 (about half a viewport)."},
                "ref": {"type": "string", "description": "Optional element ref from browser_snapshot. Scroll the nearest scrollable area under this element."},
                "x": {"type": "integer", "description": "Optional viewport x coordinate for the scroll gesture."},
                "y": {"type": "integer", "description": "Optional viewport y coordinate for the scroll gesture."},
            },
        },
    },
}


async def _tool_browser_scroll(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import scroll_page

    raw = args.get("delta_y")
    if raw is None:
        delta_y = 500
    else:
        try:
            delta_y = int(raw)
        except (ValueError, TypeError):
            return plugin_localized(
                context,
                "Scroll failed: delta_y must be an integer.",
                "滚动失败：delta_y 必须是整数。",
            )
    def _optional_int(name: str) -> int | None:
        value = args.get(name)
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            raise ValueError(name) from None

    try:
        x = _optional_int("x")
        y = _optional_int("y")
    except ValueError as exc:
        return plugin_localized(
            context,
            "Scroll failed: {field} must be an integer.",
            "滚动失败：{field} 必须是整数。",
            field=str(exc),
        )
    ref = str(args.get("ref") or "").strip()
    result = await scroll_page(delta_y=delta_y, x=x, y=y, ref=ref)
    if result.get("ok") is False:
        from .browser_output import browser_error_text

        return plugin_localized(
            context,
            "Scroll failed: {error}",
            "滚动失败：{error}",
            error=browser_error_text(
                result,
                context,
                "The browser page could not be scrolled.",
                "无法滚动浏览器页面。",
            ),
        )
    if delta_y == 0:
        return plugin_localized(
            context,
            "Scroll position unchanged (delta_y=0).",
            "滚动位置未变化（delta_y=0）。",
        )
    if result.get("moved") is False:
        return plugin_localized(
            context,
            "Scroll had no effect. The targeted area may already be at its boundary; choose a ref or coordinates inside another scrollable area.",
            "滚动未生效。目标区域可能已到达边界；请选择另一个可滚动区域内的 ref 或坐标。",
        )
    actual = result.get("actualDeltaY")
    target = result.get("target") if isinstance(result.get("target"), dict) else {}
    target_name = str(
        target.get("id")
        or (f"e{target.get('ref')}" if target.get("ref") else "")
        or target.get("tag")
        or plugin_localized(context, "targeted area", "目标区域")
    )
    if isinstance(actual, (int, float)) and actual != 0:
        return plugin_localized(
            context,
            "Scrolled {target} by {pixels}px.",
            "已将 {target} 滚动 {pixels}px。",
            target=target_name,
            pixels=round(actual),
        )
    if delta_y > 0:
        return plugin_localized(
            context,
            "Scrolled down at ({x}, {y}).",
            "已在坐标（{x}, {y}）向下滚动。",
            x=result.get("x"),
            y=result.get("y"),
        )
    return plugin_localized(
        context,
        "Scrolled up at ({x}, {y}).",
        "已在坐标（{x}, {y}）向上滚动。",
        x=result.get("x"),
        y=result.get("y"),
    )


handler = _tool_browser_scroll

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_scroll"]
