"""Canonical request-usage fields for durable Workbench chat messages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)


def normalized_usage(raw: Any) -> dict[str, int]:
    usage = raw if isinstance(raw, Mapping) else {}
    result = {key: 0 for key in USAGE_KEYS}
    for key in USAGE_KEYS:
        try:
            result[key] = max(0, int(usage.get(key) or 0))
        except (TypeError, ValueError, OverflowError):
            pass
    if not result["total_tokens"]:
        result["total_tokens"] = (
            result["prompt_tokens"] + result["completion_tokens"]
        )
    return result


def latest_request_usage(messages: Sequence[Any]) -> dict[str, int]:
    """Return the explicitly recorded latest request, never cumulative usage."""

    for message in reversed(messages):
        if not isinstance(message, Mapping):
            continue
        latest = message.get("latestRequestUsage")
        if isinstance(latest, Mapping):
            return normalized_usage(latest)
        if isinstance(message.get("usage"), Mapping):
            # This is a usage-bearing legacy message.  Looking further back
            # would expose a previous request as though it belonged to this one.
            break
    return normalized_usage({})


def runtime_usage_message_fields(
    usage: Mapping[str, Any] | None,
    latest_request: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Project one runtime result into its two distinct durable usage fields."""

    fields: dict[str, dict[str, Any]] = {}
    cumulative = dict(usage or {})
    if any(cumulative.values()):
        fields["usage"] = cumulative
    latest = dict(latest_request or {})
    if any(latest.values()):
        fields["latestRequestUsage"] = latest
    return fields


__all__ = [
    "USAGE_KEYS",
    "latest_request_usage",
    "normalized_usage",
    "runtime_usage_message_fields",
]
