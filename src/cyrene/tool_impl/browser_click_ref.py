"""Tool implementation for browser_click_ref."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "browser_click_ref"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Click an element returned by browser_snapshot using its ref (for example e12). Prefer this over CSS selectors on SPA pages with dynamic classes.",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref from browser_snapshot, such as e12."},
            },
            "required": ["ref"],
        },
    },
}


async def _tool_browser_click_ref(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import click_ref

    ref = str(args.get("ref") or "").strip()
    if not ref:
        return "Click failed: no element ref provided."
    result = await click_ref(ref)
    if result.get("ok"):
        return f"Clicked {ref}.\nURL: {result.get('url', '—')}\nTitle: {result.get('title', '—')}"
    return f"Click failed: {result.get('error', 'unknown error')}"


handler = _tool_browser_click_ref

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_click_ref"]
