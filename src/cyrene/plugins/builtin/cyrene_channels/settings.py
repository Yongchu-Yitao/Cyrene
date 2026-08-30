"""Credential policy owned by the messaging-channels Plugin pack."""

from __future__ import annotations

import os
from typing import Any

from cyrene.localization import localized
from cyrene.platform import config_store


_EDITABLE_ENV_KEYS: dict[str, dict[str, Any]] = {
    "TELEGRAM_BOT_TOKEN": {"masked": True},
    "WECHAT_BOT_TOKEN": {"masked": True},
}
_INTERNAL_ENV_KEYS = frozenset({"WECHAT_OWNER_ID"})


def get_env(key: str, default: str = "") -> str:
    return config_store.get_env(key, default)


def telegram_owner_id() -> int | None:
    raw = str(os.environ.get("OWNER_ID") or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def editable_env_keys() -> dict[str, dict[str, Any]]:
    labels = {
        "TELEGRAM_BOT_TOKEN": localized("Telegram token", "Telegram 令牌"),
        "WECHAT_BOT_TOKEN": localized("WeChat token", "微信令牌"),
    }
    return {
        key: {**meta, "label": labels[key]}
        for key, meta in _EDITABLE_ENV_KEYS.items()
    }


def write_env_keys(updates: dict[str, str]) -> bool:
    allowed = set(_EDITABLE_ENV_KEYS) | set(_INTERNAL_ENV_KEYS)
    normalized = {
        str(key): str(value or "").strip().strip("\"'")
        for key, value in updates.items()
        if str(key) in allowed
    }
    if normalized:
        config_store.set_env_many(normalized)
    return True


__all__ = [
    "editable_env_keys",
    "get_env",
    "telegram_owner_id",
    "write_env_keys",
]
