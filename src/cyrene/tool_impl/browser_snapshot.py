"""Tool implementation for browser_snapshot."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "browser_snapshot"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Inspect the current browser page and return visible actionable elements with refs, text, hrefs, selectors, and bounding boxes. Use this before clicking complex SPA pages instead of guessing CSS selectors.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_elements": {"type": "integer", "description": "Maximum number of visible elements to return. Default 80, max 200."},
            },
        },
    },
}


def _format_element(el: dict[str, Any]) -> str:
    ref = str(el.get("ref") or "?")
    tag = str(el.get("tag") or "")
    role = str(el.get("role") or "")
    label = str(el.get("text") or el.get("ariaLabel") or el.get("placeholder") or el.get("alt") or "").strip()
    href = str(el.get("href") or "").strip()
    selector = str(el.get("selector") or "").strip()
    rect = el.get("rect") if isinstance(el.get("rect"), dict) else {}
    bits = [f"[{ref}]", tag]
    if role:
        bits.append(f"role={role}")
    if label:
        bits.append(f"text={label!r}")
    if href:
        bits.append(f"href={href}")
    if selector:
        bits.append(f"selector={selector}")
    if rect:
        bits.append(f"box={rect.get('x', 0)},{rect.get('y', 0)},{rect.get('w', 0)}x{rect.get('h', 0)}")
    return " ".join(bits)


async def _tool_browser_snapshot(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import inspect_page

    try:
        max_elements = int(args.get("max_elements") or 80)
    except (TypeError, ValueError):
        max_elements = 80
    result = await inspect_page(max_elements=max_elements)
    if result.get("ok") is False:
        return f"Browser snapshot failed: {result.get('error', 'unknown error')}"
    parts = [
        f"Title: {result.get('title', '—')}",
        f"URL: {result.get('url', '—')}",
    ]
    elements = result.get("elements") if isinstance(result.get("elements"), list) else []
    if not elements:
        parts.append("No visible actionable elements found.")
    else:
        parts.append("Visible elements:")
        parts.extend(_format_element(el) for el in elements if isinstance(el, dict))
    text = str(result.get("text") or "").strip()
    if text:
        parts.append("\nPage text preview:\n" + text[:2000])
    return "\n".join(parts)


handler = _tool_browser_snapshot

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_snapshot"]
