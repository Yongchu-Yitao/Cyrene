"""Tool implementation for browser_user_events."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "browser_user_events"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "List recent user-driven operations in the embedded browser for the current chat session, "
            "including clicks, text input, scrolling, and navigation. Use this only when you need to "
            "understand what the user just did in the browser before continuing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recent browser user operations to return. Default 20, max 100.",
                },
                "round_id": {
                    "type": "string",
                    "description": "Optional round id. Omit to read recent operations across the current chat session.",
                },
            },
        },
    },
}

# i18n: translations for tool output strings
_OUTPUT_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "no_events": "No recent user browser operations were recorded for the current chat session.",
        "header": "Recent user browser operations:",
    },
    "zh": {
        "no_events": "当前对话会话中没有记录到最近的用户浏览器操作。",
        "header": "最近的用户浏览器操作：",
    },
}


def _t(key: str) -> str:
    """Return translated output string for the current language."""
    try:
        from cyrene.runtime.config_store import get_setting
        lang = str(get_setting("app_language", "") or "").strip().lower()
        lang = lang if lang in ("en", "zh") else "en"
    except Exception:
        lang = "en"
    texts = _OUTPUT_TEXTS.get(lang, _OUTPUT_TEXTS["en"])
    return texts.get(key, key)


def _event_label(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or "event")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    target = event.get("target") if isinstance(event.get("target"), dict) else {}
    url = str(event.get("url") or "")
    title = str(event.get("title") or "")
    bits = [f"{event.get('created_at')}: browser.user.{kind}"]
    purpose = str(event.get("purpose") or "")
    action_summary = str(event.get("action_summary") or "")
    object_summary = str(event.get("object_summary") or "")
    if purpose:
        bits.append(f"purpose={purpose!r}")
    if action_summary:
        bits.append(f"action={action_summary!r}")
    if object_summary:
        bits.append(f"object={object_summary!r}")
    if title:
        bits.append(f"title={title!r}")
    if url:
        bits.append(f"url={url}")
    if target:
        compact_target = {k: v for k, v in target.items() if v not in ("", None)}
        if compact_target:
            bits.append(f"target={compact_target}")
    if payload:
        compact_payload = {k: v for k, v in payload.items() if v not in ("", None)}
        if compact_payload:
            bits.append(f"payload={compact_payload}")
    return " | ".join(bits)


async def _tool_browser_user_events(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    from cyrene.learning import engine as behavior_learning
    from cyrene.agent.context import get_current_round_id, get_current_session_id

    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    session_id = str(get_current_session_id() or "").strip()
    round_id = str(args.get("round_id") or "").strip()
    if round_id == "current":
        round_id = str(get_current_round_id() or "").strip()
    events = await behavior_learning.list_recent_browser_user_events(
        session_id=session_id,
        round_id=round_id,
        limit=limit,
    )
    if not events and round_id:
        events = await behavior_learning.list_recent_browser_user_events(
            session_id=session_id,
            round_id="",
            limit=limit,
        )
    if not events:
        return _t("no_events")
    return _t("header") + "\n" + "\n".join(_event_label(event) for event in events)


handler = _tool_browser_user_events

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_user_events"]
