"""Minimal localization used by the host-independent core."""

from __future__ import annotations

import locale
import os
from typing import Any


def normalize_language(value: Any) -> str:
    raw = str(value or "").strip().replace("_", "-").lower()
    if raw == "zh" or raw.startswith("zh-"):
        return "zh"
    if raw == "en" or raw.startswith("en-"):
        return "en"
    return ""


def system_language() -> str:
    candidates = (
        os.environ.get("LC_ALL"),
        os.environ.get("LC_MESSAGES"),
        os.environ.get("LANG"),
        locale.getlocale()[0],
    )
    return next((value for item in candidates if (value := normalize_language(item))), "en")


def localized(en: str, zh: str, *, language: Any = None, **values: Any) -> str:
    resolved = normalize_language(language) or system_language()
    return (zh if resolved == "zh" else en).format(**values)


__all__ = ["localized", "normalize_language", "system_language"]
