"""Tool implementation for browser_snapshot."""

from __future__ import annotations

import logging
from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized, run_context_value


logger = logging.getLogger(__name__)

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
                "resource_id": {
                    "type": "string",
                    "description": "Optional pinned topbar browser resource id. This is a strictly read-only snapshot when the browser belongs to another session.",
                },
            },
        },
    },
}


def _format_element(el: dict[str, Any], context: PluginContext) -> str:
    ref = str(el.get("ref") or "?")
    tag = str(el.get("tag") or "")
    role = str(el.get("role") or "")
    input_type = str(el.get("inputType") or "")
    label = str(el.get("text") or el.get("ariaLabel") or el.get("placeholder") or el.get("alt") or "").strip()
    href = str(el.get("href") or "").strip()
    selector = str(el.get("selector") or "").strip()
    rect = el.get("rect") if isinstance(el.get("rect"), dict) else {}
    bits = [f"[{ref}]", tag]
    if role:
        bits.append(f"role={role}")
    if input_type:
        bits.append(f"type={input_type}")
    if input_type == "file":
        accept = str(el.get("accept") or "") or plugin_localized(
            context, "(not declared)", "（未声明）"
        )
        bits.append(f"accept={accept}")
        bits.append(f"multiple={bool(el.get('multiple'))}")
    if label:
        bits.append(f"text={label!r}")
    if href:
        bits.append(f"href={href}")
    if selector:
        bits.append(f"selector={selector}")
    if rect:
        bits.append(f"box={rect.get('x', 0)},{rect.get('y', 0)},{rect.get('w', 0)}x{rect.get('h', 0)}")
    return " ".join(bits)


async def _tool_browser_snapshot(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import inspect_page

    try:
        max_elements = int(args.get("max_elements") or 80)
    except (TypeError, ValueError):
        max_elements = 80
    resource_id = str(args.get("resource_id") or "").strip()
    if resource_id:
        from cyrene.workbench.pinned_resources import browser_snapshot_target
        try:
            target = browser_snapshot_target(
                resource_id,
                str(run_context_value(context, "session_id") or ""),
            )
        except Exception:
            logger.debug("Pinned browser snapshot target rejected", exc_info=True)
            return plugin_localized(
                context,
                "Browser snapshot failed: the pinned browser resource is unavailable.",
                "浏览器快照失败：固定的浏览器资源不可用。",
            )
        result = await inspect_page(
            max_elements=max_elements,
            session_id=str(target.get("ownerSessionId") or ""),
            read_only=True,
        )
        if isinstance(result, dict):
            result["resource_id"] = resource_id
            result["read_only"] = bool(target.get("readOnly"))
    else:
        result = await inspect_page(max_elements=max_elements)
    if result.get("ok") is False:
        from .browser_output import browser_error_text

        return plugin_localized(
            context,
            "Browser snapshot failed: {error}",
            "浏览器快照失败：{error}",
            error=browser_error_text(
                result,
                context,
                "Unable to inspect the page.",
                "无法检查页面。",
            ),
        )
    parts = [
        plugin_localized(context, "Title: {title}", "标题：{title}", title=result.get("title", "—")),
        plugin_localized(context, "URL: {url}", "网址：{url}", url=result.get("url", "—")),
    ]
    if result.get("read_only"):
        parts.append(
            plugin_localized(
                context,
                "Access: read-only pinned browser snapshot; interactions are not permitted.",
                "访问权限：固定浏览器的只读快照；不允许交互。",
            )
        )
    snapshot_token = str(result.get("snapshot_token") or "").strip()
    if snapshot_token:
        parts.append(
            plugin_localized(
                context,
                "Snapshot credential: {token}\n"
                "Use this once as browser_navigate.snapshot_token only with reason=ui_unreachable. "
                "It expires after 2 minutes or any browser interaction/new snapshot.",
                "快照凭据：{token}\n"
                "仅当 reason=ui_unreachable 时，将其作为 browser_navigate.snapshot_token 使用一次。"
                "它会在 2 分钟后，或任何浏览器交互/新快照后过期。",
                token=snapshot_token,
            )
        )
    from .browser_output import page_observation_lines
    parts.extend(page_observation_lines(result, context))
    elements = result.get("elements") if isinstance(result.get("elements"), list) else []
    if not elements:
        parts.append(
            plugin_localized(
                context,
                "No visible actionable elements were found.",
                "未找到可见且可操作的元素。",
            )
        )
    else:
        parts.append(plugin_localized(context, "Visible elements:", "可见元素："))
        parts.extend(
            _format_element(el, context) for el in elements if isinstance(el, dict)
        )
    text = str(result.get("text") or "").strip()
    if text:
        parts.append(
            plugin_localized(
                context,
                "\nPage text preview:\n{preview}",
                "\n页面文本预览：\n{preview}",
                preview=text[:2000],
            )
        )
    return "\n".join(parts)


handler = _tool_browser_snapshot

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_snapshot"]
