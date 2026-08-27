"""Tool implementation for browser_type_ref."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext

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


async def _tool_browser_type_ref(args: dict[str, Any], _context: PluginContext) -> str:
    from cyrene.browser import type_ref

    ref = str(args.get("ref") or "").strip()
    text = str(args.get("text") or "")
    submit = bool(args.get("submit", False))
    if not ref:
        return "Type failed: no element ref provided."
    result = await type_ref(ref, text, submit=submit)
    if result.get("ok"):
        return f"Typed into {ref}.\nURL: {result.get('url', '—')}\nTitle: {result.get('title', '—')}"
    return f"Type failed: {result.get('error', 'unknown error')}"


handler = _tool_browser_type_ref

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_type_ref"]
