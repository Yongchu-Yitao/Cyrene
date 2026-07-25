"""Pure message identity, merge, and parsing helpers."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4


def ensure_message_identity(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for message in messages:
        if not isinstance(message, dict):
            continue
        if not str(message.get("message_id", "")).strip():
            message["message_id"] = f"msg_{uuid4().hex}"
    return messages


def dedupe_messages_by_id(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep message order while preferring the latest version for each id."""
    deduped: list[dict[str, Any]] = []
    seen_index: dict[str, int] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_id = str(message.get("message_id", "")).strip()
        if message_id and message_id in seen_index:
            deduped[seen_index[message_id]] = message
            continue
        if message_id:
            seen_index[message_id] = len(deduped)
        deduped.append(message)
    return deduped


def merge_message_sequence(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge persisted sequences without regressing newer entries."""
    incoming_by_id = {
        str(message.get("message_id", "")).strip(): message
        for message in incoming
        if isinstance(message, dict)
        and str(message.get("message_id", "")).strip()
    }

    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for message in existing:
        if not isinstance(message, dict):
            continue
        message_id = str(message.get("message_id", "")).strip()
        if message_id and message_id in incoming_by_id:
            merged.append(incoming_by_id[message_id])
            seen_ids.add(message_id)
            continue
        merged.append(message)
        if message_id:
            seen_ids.add(message_id)

    for message in incoming:
        if not isinstance(message, dict):
            continue
        message_id = str(message.get("message_id", "")).strip()
        if message_id and message_id in seen_ids:
            continue
        merged.append(message)
        if message_id:
            seen_ids.add(message_id)

    return dedupe_messages_by_id(merged)


def message_suffix_after_persisted_prefix(
    messages: list[dict[str, Any]],
    base_messages: list[dict[str, Any]],
    fallback_prefix_len: int,
) -> list[dict[str, Any]]:
    """Return newly produced messages after the persisted history prefix."""
    base_ids = {
        str(message.get("message_id", "")).strip()
        for message in base_messages
        if isinstance(message, dict)
        and str(message.get("message_id", "")).strip()
    }
    if base_ids:
        index = 0
        while index < len(messages):
            message = messages[index]
            message_id = (
                str(message.get("message_id", "")).strip()
                if isinstance(message, dict)
                else ""
            )
            if not message_id or message_id not in base_ids:
                break
            index += 1
        if index > 0:
            return messages[index:]

    prefix_len = max(0, min(fallback_prefix_len, len(messages)))
    return messages[prefix_len:]


def is_replaceable_live_message(entry: dict[str, Any], round_id: str) -> bool:
    """Return whether a persisted message belongs to the active live run."""
    if not round_id:
        return False
    if str(entry.get("round_id", "")).strip() != round_id:
        return False
    return not str(entry.get("queued_guidance_id", "")).strip()


def fallback_label(text: str, limit: int = 48) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip().strip(
        "[](){}<>\"'`，。！？；：,.;!?"
    )
    return compact[:limit] or "Untitled"


def extract_json_object(text: str) -> dict[str, Any]:
    source = str(text or "").strip()
    if not source:
        return {}
    try:
        data = json.loads(source)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", source, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
