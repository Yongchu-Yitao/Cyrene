"""Read browser events captured by behavior learning."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized, run_context_value

TOOL_NAME = "browser_user_events"
TOOL_METADATA = {"main_only": True}
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "List recent user-driven operations in the embedded browser for "
            "the current chat session, including clicks, text input, scrolling, "
            "and navigation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum recent operations. Default 20, max 100.",
                },
                "round_id": {
                    "type": "string",
                    "description": "Optional round id; omit for the whole chat.",
                },
            },
        },
    },
}

def _event_label(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or "event")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    target = event.get("target") if isinstance(event.get("target"), dict) else {}
    bits = [f"{event.get('created_at')}: browser.user.{kind}"]
    for label, value in (
        ("purpose", event.get("purpose")),
        ("action", event.get("action_summary")),
        ("object", event.get("object_summary")),
        ("title", event.get("title")),
        ("url", event.get("url")),
    ):
        if value:
            bits.append(f"{label}={value!r}")
    compact_target = {
        key: value for key, value in target.items() if value not in ("", None)
    }
    compact_payload = {
        key: value for key, value in payload.items() if value not in ("", None)
    }
    if compact_target:
        bits.append(f"target={compact_target}")
    if compact_payload:
        bits.append(f"payload={compact_payload}")
    return " | ".join(bits)


async def _tool_browser_user_events(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    learning = context.services.get("skills")
    if learning is None:
        raise RuntimeError("Skills Plugin application service is unavailable")
    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    session_id = str(run_context_value(context, "session_id") or "").strip()
    round_id = str(args.get("round_id") or "").strip()
    if round_id == "current":
        round_id = str(run_context_value(context, "round_id") or "").strip()
    events = await learning.list_recent_browser_user_events(
        session_id=session_id,
        round_id=round_id,
        limit=limit,
    )
    if not events and round_id:
        events = await learning.list_recent_browser_user_events(
            session_id=session_id,
            round_id="",
            limit=limit,
        )
    if not events:
        return plugin_localized(
            context,
            "No recent user browser operations were recorded for the current chat session.",
            "当前对话会话中没有记录到最近的用户浏览器操作。",
        )
    return plugin_localized(
        context,
        "Recent user browser operations:",
        "最近的用户浏览器操作：",
    ) + "\n" + "\n".join(
        _event_label(event) for event in events
    )


handler = _tool_browser_user_events

__all__ = ["TOOL_DEF", "TOOL_METADATA", "TOOL_NAME", "handler"]
