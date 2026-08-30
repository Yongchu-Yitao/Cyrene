"""Defensive JSON field codecs shared by SQLite repositories."""

from __future__ import annotations

import json
from typing import Any


def serialize_list(items: list[Any] | None) -> str:
    return json.dumps(items or [])


def serialize_dict(d: dict[str, Any] | None) -> str:
    return json.dumps(d or {})


def deserialize_list(s: str | None) -> list[Any]:
    if not s:
        return []
    try:
        decoded = json.loads(s)
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def deserialize_dict(s: str | None) -> dict[str, Any]:
    if not s:
        return {}
    try:
        decoded = json.loads(s)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


__all__ = [
    "deserialize_dict",
    "deserialize_list",
    "serialize_dict",
    "serialize_list",
]
