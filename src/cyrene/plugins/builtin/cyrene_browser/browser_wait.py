"""Tool implementation for browser_wait."""

from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized

TOOL_NAME = "browser_wait"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Last-resort conditional wait for a specific selector, text, or URL after SPA rendering. "
            "Do not use it to delay for a fixed amount of time; prefer an immediate browser_snapshot "
            "or browser_network_log, and rely on the Workbench inbox to wake the agent for tool results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Optional CSS selector that must exist."},
                "text": {"type": "string", "description": "Optional page text that must appear."},
                "url_contains": {"type": "string", "description": "Optional substring that the current URL must contain."},
                "timeout_ms": {"type": "integer", "description": "Timeout in milliseconds. Default 5000, max 30000."},
            },
        },
    },
}


async def _tool_browser_wait(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import wait_for_page

    selector = str(args.get("selector") or "").strip()
    text = str(args.get("text") or "").strip()
    url_contains = str(args.get("url_contains") or "").strip()
    try:
        timeout_ms = int(args.get("timeout_ms") or 5000)
    except (TypeError, ValueError):
        timeout_ms = 5000
    if not selector and not text and not url_contains:
        return plugin_localized(
            context,
            "Wait failed: provide selector, text, or url_contains.",
            "等待失败：请提供 selector、text 或 url_contains。",
        )
    result = await wait_for_page(selector=selector, text=text, url_contains=url_contains, timeout_ms=timeout_ms)
    if result.get("ok"):
        return plugin_localized(
            context,
            "Wait condition met.\nURL: {url}\nTitle: {title}",
            "等待条件已满足。\n网址：{url}\n标题：{title}",
            url=result.get("url", "—"),
            title=result.get("title", "—"),
        )
    from .browser_output import browser_error_text

    return plugin_localized(
        context,
        "Wait failed: {error}",
        "等待失败：{error}",
        error=browser_error_text(
            result,
            context,
            "The wait condition was not met.",
            "等待条件未满足。",
        ),
    )


handler = _tool_browser_wait

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_wait"]
