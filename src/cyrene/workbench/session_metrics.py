"""Pure formatting and accounting helpers for Workbench session views."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes:02d}:{seconds:02d}"


def status_progress(status: str) -> float:
    return {
        "running": 0.45,
        "resumed": 0.65,
        "waiting": 0.82,
        "done": 1.0,
        "timeout": 1.0,
    }.get(status, 0.5)


def short_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%H:%M:%S")
    except Exception:
        return "—"


def elapsed_since(value: str | None) -> str:
    if not value:
        return "—"
    try:
        timestamp = datetime.fromisoformat(value)
        seconds = (
            datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
        ).total_seconds()
        return format_duration(seconds)
    except Exception:
        return "—"


def safe_json_loads(value: str) -> dict[str, Any] | list[Any] | None:
    try:
        return json.loads(value)
    except Exception:
        return None


def summarize_text(value: str, limit: int = 96) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def tool_output_map(raw_messages: list[dict]) -> dict[str, str]:
    return {
        str(message["tool_call_id"]): str(message.get("content") or "")
        for message in raw_messages
        if message.get("role") == "tool" and message.get("tool_call_id")
    }


def tool_output_ids(raw_messages: list[dict]) -> set[str]:
    return {
        str(message["tool_call_id"])
        for message in raw_messages
        if message.get("role") == "tool" and message.get("tool_call_id")
    }


def tool_args_signature(value: Any) -> str:
    parsed = safe_json_loads(value) if isinstance(value, str) else value
    normalized = parsed if parsed is not None else value
    try:
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps(str(normalized), ensure_ascii=False)


def usage_totals(raw_messages: list[dict]) -> dict[str, int | None]:
    totals: dict[str, int | None] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "requests": 0,
    }
    found = False
    for message in raw_messages:
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        totals["requests"] = int(totals["requests"] or 0) + 1
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        ):
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] = int(totals[key] or 0) + value
                found = True
    if not found and not totals["requests"]:
        return {key: None for key in totals}
    if not totals["total_tokens"] and (
        totals["prompt_tokens"] or totals["completion_tokens"]
    ):
        totals["total_tokens"] = int(totals["prompt_tokens"] or 0) + int(
            totals["completion_tokens"] or 0
        )
    return totals


def last_request_context_tokens(raw_msgs: list[dict]) -> int | None:
    """Return token use for the most recent recorded LLM request."""
    for message in reversed(raw_msgs):
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        total = usage.get("total_tokens")
        if isinstance(total, int) and total > 0:
            return total
        prompt = usage.get("prompt_tokens")
        if isinstance(prompt, int) and prompt > 0:
            completion = usage.get("completion_tokens")
            return prompt + (completion if isinstance(completion, int) else 0)
    return None


def merge_usage_totals(
    *usage_items: dict[str, int | None],
) -> dict[str, int | None]:
    merged = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "requests": 0,
    }
    found = False
    for usage in usage_items:
        if not isinstance(usage, dict):
            continue
        for key in merged:
            value = usage.get(key)
            if isinstance(value, int):
                merged[key] += value
                found = True
    if not found:
        return {key: None for key in merged}
    if not merged["total_tokens"] and (
        merged["prompt_tokens"] or merged["completion_tokens"]
    ):
        merged["total_tokens"] = (
            merged["prompt_tokens"] + merged["completion_tokens"]
        )
    return merged


def format_tokens(usage: dict[str, int | None] | None) -> str:
    if not isinstance(usage, dict):
        return "—"
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    parts: list[str] = []
    if prompt_tokens is not None:
        parts.append(f"{format_token_count(prompt_tokens)} in")
    if completion_tokens is not None:
        parts.append(f"{format_token_count(completion_tokens)} out")
    if total_tokens is not None:
        parts.append(f"{format_token_count(total_tokens)} total")
    return " / ".join(parts) if parts else "—"


def format_token_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)
