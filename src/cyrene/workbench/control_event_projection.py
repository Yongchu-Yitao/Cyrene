"""Public projection for Workbench run events shared by transport adapters."""

from __future__ import annotations

from typing import Any


PUBLIC_RUN_EVENT_TYPES = frozenset({
    "ack",
    "auto_review",
    "awaiting_user",
    "error",
    "guidance_received",
    "intermediate_message",
    "interrupted",
    "permission_decision",
    "phase_transition",
    "plan",
    "plan_progress",
    "reasoning_delta",
    "reasoning_done",
    "reasoning_start",
    "reply_delta",
    "reply_done",
    "reply_start",
    "run_finalizing",
    "saved",
    "subagent_update",
    "tool_call_finished",
    "tool_call_progress",
    "tool_call_started",
    "user_question",
    "user_question_answered",
})


def _pending_question(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        key: value[key]
        for key in (
            "id",
            "questionId",
            "kind",
            "questionKind",
            "text",
            "prompt",
            "question",
            "title",
            "options",
            "choices",
            "allowCustom",
            "allow_custom",
        )
        if key in value
    }
    return result or None


def _attachment(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    result = {
        key: item[key]
        for key in (
            "id",
            "name",
            "type",
            "mediaType",
            "content_type",
            "kind",
            "size",
            "width",
            "height",
        )
        if key in item
    }
    return result or None


def _intermediate_message(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        key: value[key]
        for key in (
            "id",
            "role",
            "content",
            "text",
            "kind",
            "status",
            "createdAt",
            "liveDedupeKey",
            "opensActivity",
        )
        if key in value
    }
    if isinstance(value.get("attachments"), list):
        result["attachments"] = [
            summary
            for item in value["attachments"]
            if (summary := _attachment(item)) is not None
        ]
    if isinstance(value.get("trace"), list):
        result["trace"] = value["trace"]
    return result or None


def public_run_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return the fixed public run-event DTO; omit unknown/internal events."""

    event_type = str(event.get("type") or "")
    if event_type not in PUBLIC_RUN_EVENT_TYPES:
        return None
    result: dict[str, Any] = {
        "type": event_type,
        "cursor": int(event.get("_seq") or 0),
        "run_id": str(event.get("runId") or ""),
    }
    for key in (
        "chatId", "status", "code", "delta", "response", "message",
        "phase", "provider", "tool_call_id", "tool", "label", "current",
        "total", "progress", "failed", "from", "to", "detail",
        "detail_key", "step", "note", "round_id", "approved", "operation",
        "path_hint", "rationale", "agent_id", "caller", "task", "mode",
        "outcome", "stop_reason", "result_preview", "created_at", "updated_at",
        "message_count",
    ):
        value = event.get(key)
        if key in event and (
            isinstance(value, (str, int, float, bool)) or value is None
        ):
            result[key] = value
    for key in ("args", "detail_params", "plan"):
        value = event.get(key)
        if isinstance(value, (dict, list)):
            result[key] = value
    if event_type == "awaiting_user":
        question = _pending_question(
            event.get("pending_question") or event.get("pendingQuestion")
        )
        if question is not None:
            result["pending_question"] = question
    if event_type == "intermediate_message":
        message = _intermediate_message(event.get("message"))
        if message is not None:
            result["message"] = message
    return result


__all__ = ["PUBLIC_RUN_EVENT_TYPES", "public_run_event"]
