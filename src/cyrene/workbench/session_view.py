"""Pure normalization helpers for Workbench session presentation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any


def build_pending_question(raw_pending: Any) -> dict[str, Any] | None:
    if not isinstance(raw_pending, dict):
        return None
    question_id = str(raw_pending.get("id", "")).strip()
    text = str(raw_pending.get("text", "")).strip()
    if not question_id or not text:
        return None
    options_out = []
    raw_options = raw_pending.get("options", [])
    if isinstance(raw_options, list):
        for item in raw_options:
            if isinstance(item, dict):
                option_id = str(item.get("id", "")).strip()
                label = str(item.get("label", "")).strip()
            else:
                option_id = ""
                label = str(item or "").strip()
            if not label:
                continue
            options_out.append(
                {
                    "id": option_id or f"option_{len(options_out) + 1}",
                    "label": label,
                }
            )
    meta = raw_pending.get("meta") if isinstance(raw_pending.get("meta"), dict) else {}
    result = {
        "id": question_id,
        "text": text,
        "askedAt": str(raw_pending.get("asked_at", "")).strip(),
        "roundId": str(raw_pending.get("round_id", "")).strip(),
        "roundTitle": str(raw_pending.get("round_title", "")).strip(),
        "clientRequestId": str(raw_pending.get("client_request_id", "")).strip(),
        "allowCustom": bool(raw_pending.get("allow_custom", True)),
        "hideAnswerInChat": bool(raw_pending.get("hide_answer_in_chat")),
        "kind": str(meta.get("kind", "")).strip(),
        "options": options_out,
    }
    if result["kind"] in {
        "scope_elevation",
        "write_permission_request",
        "read_elevation",
        "subshell_elevation",
        "external_delivery_request",
        "external_upload_confirmation",
        "delete_confirmation",
        "destructive_confirmation",
        "self_configuration_confirmation",
        "host_lifecycle_confirmation",
        "task_permission_request",
        "git_commit",
    }:
        result["meta"] = {
            key: str(meta.get(key) or "")
            for key in ("kind", "tool_name", "operation", "path_hint", "reason")
        }
    return result


def has_recent_main_agent_activity(
    recent: list[dict],
    now_ts: datetime,
) -> bool:
    """Return whether recent runtime events indicate an unfinished main-agent run."""
    cutoff_ts = now_ts - timedelta(seconds=30)
    lifecycle_active = False
    phase_active = False
    active_tools: set[str] = set()
    for event in recent:
        try:
            event_ts = datetime.fromisoformat(str(event.get("timestamp") or ""))
            if event_ts <= cutoff_ts:
                continue
        except (ValueError, TypeError):
            continue

        event_type = str(event.get("type") or "")
        if event_type == "session_update":
            status = str(event.get("status") or "").lower()
            if status:
                lifecycle_active = status in {"running", "planning", "finishing"}
                if not lifecycle_active:
                    phase_active = False
                    active_tools.clear()
            continue
        if event_type == "phase_transition":
            target = str(event.get("to") or "").lower()
            phase_active = not bool(
                re.search(r"done|complete|finish|idle|cancel|error|fail", target)
            )
            if not phase_active:
                lifecycle_active = False
                active_tools.clear()
            continue
        if str(event.get("caller") or "") != "main_agent":
            continue
        if event_type == "llm_call":
            # This is a completed accounting event. Reasoning-start/finish is
            # tracked by the attached run stream, so it must not resurrect a
            # session after a terminal transition.
            continue
        tool_id = str(
            event.get("tool_call_id")
            or event.get("toolCallId")
            or f"{event.get('caller', '')}:{event.get('tool', '')}"
        )
        if event_type in {"tool_call_started", "tool_call_progress"}:
            active_tools.add(tool_id)
        elif event_type in {"tool_call_finished", "tool_call"}:
            active_tools.discard(tool_id)
    return lifecycle_active or phase_active or bool(active_tools)


def _is_trace_only_agent_message(msg: dict[str, Any]) -> bool:
    return (
        msg.get("role") == "agent"
        and not str(msg.get("body", "")).strip()
        and (bool(msg.get("thinking")) or bool(msg.get("tools")))
    )


def _ui_tool_message_key(msg: dict) -> tuple | None:
    if not isinstance(msg, dict):
        return None
    role = str(msg.get("role", "")).strip()
    round_id = str(msg.get("roundId", "")).strip()
    if role == "agent":
        tools = msg.get("tools") if isinstance(msg.get("tools"), list) else []
        tool_ids = tuple(
            str(tool.get("toolCallId", "")).strip()
            for tool in tools
            if isinstance(tool, dict) and str(tool.get("toolCallId", "")).strip()
        )
        if tool_ids and len(tool_ids) == len(tools):
            return ("agent_tools", round_id, tool_ids)
    return None


def dedupe_repeated_messages(messages: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen_ids: set[tuple[str, str]] = set()
    seen_tool_keys: dict[tuple, int] = {}
    for msg in messages:
        message_id = str(msg.get("messageId", "")).strip() or str(
            msg.get("id", "")
        ).strip()
        tool_key = _ui_tool_message_key(msg)
        if tool_key and tool_key in seen_tool_keys:
            deduped[seen_tool_keys[tool_key]] = msg
            continue
        if tool_key:
            seen_tool_keys[tool_key] = len(deduped)
        if message_id:
            dedupe_key = (str(msg.get("role", "")).strip(), message_id)
            if dedupe_key in seen_ids:
                continue
            seen_ids.add(dedupe_key)
        deduped.append(msg)
    return deduped


def merge_adjacent_trace_only_messages(messages: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for msg in messages:
        if not merged:
            merged.append(msg)
            continue
        prev = merged[-1]
        prev_request_id = str(prev.get("clientRequestId", "")).strip()
        next_request_id = str(msg.get("clientRequestId", "")).strip()
        compatible_request = (
            not prev_request_id
            or not next_request_id
            or prev_request_id == next_request_id
        )
        compatible_round = str(prev.get("roundId", "")).strip() == str(
            msg.get("roundId", "")
        ).strip()
        compatible_guidance = (
            not str(prev.get("queuedGuidanceId", "")).strip()
            and not str(msg.get("queuedGuidanceId", "")).strip()
            and not str(prev.get("guidanceAckForGuidanceId", "")).strip()
            and not str(msg.get("guidanceAckForGuidanceId", "")).strip()
            and not str(prev.get("inReplyToGuidanceId", "")).strip()
            and not str(msg.get("inReplyToGuidanceId", "")).strip()
        )
        if (
            _is_trace_only_agent_message(prev)
            and _is_trace_only_agent_message(msg)
            and compatible_round
            and compatible_request
            and compatible_guidance
        ):
            prev_thinking = str(prev.get("thinking", "")).strip()
            next_thinking = str(msg.get("thinking", "")).strip()
            if next_thinking:
                if prev_thinking and next_thinking != prev_thinking:
                    prev["thinking"] = prev_thinking + "\n\n" + next_thinking
                elif not prev_thinking:
                    prev["thinking"] = next_thinking
            prev_tools = list(prev.get("tools") or [])
            next_tools = list(msg.get("tools") or [])
            if next_tools:
                prev["tools"] = prev_tools + next_tools
            continue
        if (
            _is_trace_only_agent_message(prev)
            and msg.get("role") == "agent"
            and compatible_round
            and compatible_request
            and compatible_guidance
            and (
                str(msg.get("body", "")).strip()
                or str(msg.get("thinking", "")).strip()
                or bool(msg.get("tools"))
            )
        ):
            merged_msg = dict(msg)
            prev_thinking = str(prev.get("thinking", "")).strip()
            next_thinking = str(merged_msg.get("thinking", "")).strip()
            if prev_thinking:
                if next_thinking and next_thinking != prev_thinking:
                    merged_msg["thinking"] = prev_thinking + "\n\n" + next_thinking
                elif not next_thinking:
                    merged_msg["thinking"] = prev_thinking
            prev_tools = list(prev.get("tools") or [])
            next_tools = list(merged_msg.get("tools") or [])
            if prev_tools or next_tools:
                merged_msg["tools"] = prev_tools + next_tools
            if (
                not str(merged_msg.get("clientRequestId", "")).strip()
                and prev_request_id
            ):
                merged_msg["clientRequestId"] = prev_request_id
            merged[-1] = merged_msg
            continue
        merged.append(msg)
    return merged


def collapse_duplicate_user_messages(messages: list[dict]) -> list[dict]:
    collapsed: list[dict] = []
    index = 0
    while index < len(messages):
        msg = messages[index]
        if msg.get("role") != "user":
            collapsed.append(msg)
            index += 1
            continue

        block_end = index
        while (
            block_end < len(messages)
            and messages[block_end].get("role") == "user"
        ):
            block_end += 1

        block = messages[index:block_end]
        seen_bodies: set[str] = set()
        kept_reversed: list[dict] = []
        for block_msg in reversed(block):
            body = str(block_msg.get("body", "")).strip()
            if body and body in seen_bodies:
                continue
            if body:
                seen_bodies.add(body)
            kept_reversed.append(block_msg)
        collapsed.extend(reversed(kept_reversed))
        index = block_end
    return collapsed


def count_tool_calls(raw_msgs: list[dict]) -> int:
    count = sum(len(message.get("tool_calls") or []) for message in raw_msgs)
    if count == 0:
        count = sum(1 for message in raw_msgs if message.get("role") == "tool")
    return count


def session_started_at(raw_messages: list[dict], default: float) -> float:
    for message in raw_messages:
        round_id = str(message.get("round_id", "")).strip()
        match = re.fullmatch(r"round_(\d+)", round_id)
        if match:
            return int(match.group(1)) / 1000.0
    return default


def split_raw_rounds(raw_msgs: list[dict]) -> list[list[dict]]:
    rounds: list[list[dict]] = []
    current: list[dict] = []
    current_key = ""
    anonymous_round_index = 0
    for message in raw_msgs:
        round_id = str(message.get("round_id", "")).strip()
        if round_id:
            next_key = f"round:{round_id}"
        elif message.get("role") == "user":
            anonymous_round_index += 1
            next_key = f"anon:{anonymous_round_index}"
        else:
            next_key = current_key or f"anon:{max(anonymous_round_index, 1)}"

        if current and next_key != current_key:
            rounds.append(current)
            current = []

        if not current:
            current_key = next_key
            current = [message]
            continue

        current.append(message)
    if current:
        rounds.append(current)
    return rounds


def _round_has_activity(raw_msgs: list[dict]) -> bool:
    return any(str(message.get("role", "")) != "user" for message in raw_msgs)


def prune_flow_rounds(
    rounds: list[list[dict]],
) -> tuple[list[list[dict]], int]:
    """Keep substantive rounds plus the latest pending user-only round."""
    if not rounds:
        return [], -1

    substantive_indices = [
        index
        for index, raw_round in enumerate(rounds)
        if _round_has_activity(raw_round)
    ]
    if not substantive_indices:
        return [rounds[-1]], 0

    keep_indices = set(substantive_indices)
    latest_substantive = substantive_indices[-1]
    tail_pending = [
        index
        for index in range(latest_substantive + 1, len(rounds))
        if not _round_has_activity(rounds[index])
    ]
    if tail_pending:
        keep_indices.add(tail_pending[-1])

    pruned: list[list[dict]] = []
    index_map: dict[int, int] = {}
    for original_index, raw_round in enumerate(rounds):
        if original_index not in keep_indices:
            continue
        index_map[original_index] = len(pruned)
        pruned.append(raw_round)

    return pruned, index_map[latest_substantive]
