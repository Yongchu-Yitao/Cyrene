"""Message utilities: identity, dedup, merge, intermediate replies, round helpers.

Depends on ``state`` (for ContextVars) but not on ``session``, ``guidance``,
or ``coordinator``.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any

from cyrene.agent.context import (
    append_pending_intermediate_reply,
    current_assistant_meta,
    current_session_state_lock,
    emit_reply_stream_event as _emit_reply_stream_event,
    publish_runtime_event as _publish_runtime_event,
    take_pending_intermediate_replies,
)
from cyrene.agent.message_utils import (
    dedupe_messages_by_id as _dedupe_messages_by_id,
    ensure_message_identity as _ensure_message_identity,
    extract_json_object as _extract_json_object,
    fallback_label as _fallback_label,
    is_replaceable_live_message as _is_replaceable_live_message,
    merge_message_sequence as _merge_message_sequence,
    message_suffix_after_persisted_prefix as _message_suffix_after_persisted_prefix,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intermediate replies
# ---------------------------------------------------------------------------

def _flush_intermediate_user_replies(messages: list[dict[str, Any]]) -> None:
    pending = take_pending_intermediate_replies()
    if not pending:
        return
    existing_ids = {str(m.get("message_id", "")).strip() for m in messages if isinstance(m, dict)}
    for entry in pending:
        _ensure_message_identity([entry])
        mid = str(entry.get("message_id", "")).strip()
        if mid and mid in existing_ids:
            continue
        messages.append(dict(entry))
        if mid:
            existing_ids.add(mid)


async def _insert_intermediate_user_reply(
    content: str,
    round_id: str,
    client_request_id: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat()
    assistant_entry: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "round_id": round_id,
        "intermediate_reply": True,
        "created_at": created_at,
    }
    if attachments:
        assistant_entry["attachments"] = [dict(item) for item in attachments if isinstance(item, dict)]
    if client_request_id:
        assistant_entry["client_request_id"] = client_request_id

    from cyrene.agent.session import get_session_labels

    labels = get_session_labels(round_id)
    if labels.get("round_title"):
        assistant_entry["round_title"] = labels["round_title"]

    _ensure_message_identity([assistant_entry])

    append_pending_intermediate_reply(assistant_entry)

    from cyrene.agent.session import _load_session_state, _write_session_messages_locked

    async with current_session_state_lock():
        state = _load_session_state()
        existing = state.get("messages", [])
        full_messages = list(existing) if isinstance(existing, list) else []
        full_messages.append(dict(assistant_entry))
        _ensure_message_identity(full_messages)
        await _write_session_messages_locked(state, full_messages)

    public_message: dict[str, Any] = {
        "id": assistant_entry.get("message_id", ""),
        "role": "assistant",
        "content": content,
        "createdAt": created_at,
        "intermediate": True,
    }
    if attachments:
        public_message["attachments"] = [dict(item) for item in attachments if isinstance(item, dict)]
    await _emit_reply_stream_event({
        "type": "intermediate_message",
        "message": public_message,
    })
    await _publish_runtime_event({
        "type": "assistant_message",
        "round_id": round_id,
        "client_request_id": client_request_id,
        "intermediate": True,
        "message_id": assistant_entry.get("message_id", ""),
        "message": public_message,
    })
    return assistant_entry


async def insert_intermediate_user_reply(
    content: str,
    round_id: str,
    client_request_id: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Public delivery boundary for user-visible intermediate replies."""
    return await _insert_intermediate_user_reply(
        content,
        round_id,
        client_request_id,
        attachments,
    )


# ---------------------------------------------------------------------------
# Entry builders
# ---------------------------------------------------------------------------

def _assistant_entry_from_response(response: dict[str, Any], round_id: str, include_tool_calls: bool = True) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "role": "assistant",
        "content": response.get("content") or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if response.get("reasoning_content"):
        entry["reasoning_content"] = response["reasoning_content"]
    if include_tool_calls and response.get("tool_calls"):
        entry["tool_calls"] = response["tool_calls"]
    if response.get("usage"):
        entry["usage"] = response["usage"]
    if round_id:
        entry["round_id"] = round_id
    extra_meta = current_assistant_meta()
    if extra_meta:
        entry.update(extra_meta)
    return entry


def _apply_assistant_meta(entry: dict[str, Any]) -> dict[str, Any]:
    entry.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    extra_meta = current_assistant_meta()
    if extra_meta:
        entry.update(extra_meta)
    return entry


def _tool_result_requests_user_input(result: str) -> bool:
    payload = _extract_json_object(result)
    return str(payload.get("status", "")).strip() == "awaiting_user"


# ---------------------------------------------------------------------------
# Round timestamp helpers
# ---------------------------------------------------------------------------

def _round_epoch_ms(round_id: str) -> int | None:
    match = re.fullmatch(r"round_(\d+)", str(round_id or "").strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _round_started_iso(round_id: str) -> str | None:
    epoch_ms = _round_epoch_ms(round_id)
    if epoch_ms is None:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _is_placeholder_reply(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "", "done", "done.", "finished", "finished.",
        "ok", "ok.", "okay", "okay.",
        "完成", "完成。", "已完成", "已完成。",
    }


def _round_title_from_entry(entry: dict[str, Any]) -> str:
    return (
        str(entry.get("title", "")).strip()
        or _fallback_label(entry.get("last_user") or entry.get("prompt") or entry.get("id"), limit=40)
    )
