"""Durable Plugin result codecs and user-question value normalization."""
from __future__ import annotations
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from .plugin import PluginCallResult, PluginFailure


def json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def decoded_plugin_value(value: Any) -> Any:
    """Decode JSON-shaped Plugin output without changing ordinary strings."""

    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def question_options(raw: Any) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for index, item in enumerate(raw if isinstance(raw, list) else (), start=1):
        if isinstance(item, Mapping):
            label = next(
                (
                    str(item.get(key) or "").strip()
                    for key in ("label", "text", "value", "title", "name")
                    if str(item.get(key) or "").strip()
                ),
                "",
            )
            option_id = str(item.get("id") or "").strip()
        else:
            label = str(item or "").strip()
            option_id = ""
        if not label:
            continue
        options.append(
            {
                "id": option_id or f"option_{index}",
                "label": label,
            }
        )
        if len(options) >= 6:
            break
    return options


def stored_result(result: PluginCallResult) -> dict[str, Any]:
    return {
        "call_id": result.call_id,
        "name": result.name,
        "success": result.success,
        "value": json_value(result.value),
        "error": result.error,
        "time": result.time.isoformat(),
        **(
            {"failure": result.failure.as_dict()}
            if result.failure is not None
            else {}
        ),
    }


def restored_result(raw: Mapping[str, Any]) -> PluginCallResult:
    raw_failure = raw.get("failure")
    return PluginCallResult(
        str(raw.get("call_id") or ""),
        str(raw.get("name") or ""),
        bool(raw.get("success")),
        raw.get("value"),
        str(raw.get("error") or ""),
        datetime.fromisoformat(str(raw.get("time"))),
        (
            PluginFailure.from_dict(raw_failure)
            if isinstance(raw_failure, Mapping)
            else None
        ),
    )

