"""Tool implementation for browser_wait."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "browser_wait"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Wait for the current browser page to satisfy a condition after SPA navigation or async rendering.",
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


async def _tool_browser_wait(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import wait_for_page

    selector = str(args.get("selector") or "").strip()
    text = str(args.get("text") or "").strip()
    url_contains = str(args.get("url_contains") or "").strip()
    try:
        timeout_ms = int(args.get("timeout_ms") or 5000)
    except (TypeError, ValueError):
        timeout_ms = 5000
    if not selector and not text and not url_contains:
        return "Wait failed: provide selector, text, or url_contains."
    result = await wait_for_page(selector=selector, text=text, url_contains=url_contains, timeout_ms=timeout_ms)
    if result.get("ok"):
        return f"Wait condition met.\nURL: {result.get('url', '—')}\nTitle: {result.get('title', '—')}"
    return f"Wait failed: {result.get('error', 'unknown error')}"


handler = _tool_browser_wait

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_wait"]
