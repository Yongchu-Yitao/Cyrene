"""Tool implementation for browser_click_text."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "browser_click_text"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Click the first visible element whose text or accessible label matches the provided text.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Visible text or accessible label to click."},
                "exact": {"type": "boolean", "description": "Require an exact normalized text match. Default false."},
            },
            "required": ["text"],
        },
    },
}


async def _tool_browser_click_text(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import click_text

    text = str(args.get("text") or "").strip()
    exact = bool(args.get("exact", False))
    if not text:
        return "Click failed: no text provided."
    result = await click_text(text, exact=exact)
    if result.get("ok"):
        return f"Clicked text {text!r}.\nURL: {result.get('url', '—')}\nTitle: {result.get('title', '—')}"
    return f"Click failed: {result.get('error', 'unknown error')}"


handler = _tool_browser_click_text

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_click_text"]
