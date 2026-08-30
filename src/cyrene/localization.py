"""Small runtime localization helpers shared by non-UI Cyrene surfaces.

The React application owns its larger message catalog.  Python services still
produce user-visible notifications, channel replies, browser headers, and
Office/runtime metadata, so they need one authoritative language resolver.

Resolution order is deliberately consistent everywhere:

1. an explicit caller override;
2. the persisted ``app_language`` runtime setting;
3. the operating-system locale;
4. English as the portable fallback.
"""

from __future__ import annotations

import locale
import os
from typing import Any

SUPPORTED_LANGUAGES = frozenset({"en", "zh"})


def normalize_language(value: Any) -> str:
    """Return Cyrene's canonical ``en``/``zh`` code, or ``""`` if unknown."""
    raw = str(value or "").strip().replace("_", "-").lower()
    if raw == "zh" or raw.startswith("zh-"):
        return "zh"
    if raw == "en" or raw.startswith("en-"):
        return "en"
    return ""


def system_language() -> str:
    """Resolve the host language without depending on mutable app settings."""
    candidates = [
        os.environ.get("LC_ALL"),
        os.environ.get("LC_MESSAGES"),
        os.environ.get("LANG"),
    ]
    try:
        candidates.append(locale.getlocale()[0])
    except (TypeError, ValueError):
        pass
    for candidate in candidates:
        normalized = normalize_language(candidate)
        if normalized:
            return normalized
    return "en"


def app_language(explicit: Any = None) -> str:
    """Return the effective runtime language using the shared precedence."""
    normalized = normalize_language(explicit)
    if normalized:
        return normalized
    try:
        from cyrene.platform.settings_store import get as get_setting

        normalized = normalize_language(get_setting("app_language", ""))
    except Exception:
        normalized = ""
    return normalized or system_language()


def locale_tag(language: Any = None) -> str:
    return "zh-CN" if app_language(language) == "zh" else "en-US"


def accept_language(language: Any = None) -> str:
    return (
        "zh-CN,zh;q=0.9,en;q=0.8"
        if app_language(language) == "zh"
        else "en-US,en;q=0.9,zh-CN;q=0.6,zh;q=0.5"
    )


def localized(en: str, zh: str, *, language: Any = None, **values: Any) -> str:
    """Select and interpolate one compact bilingual runtime message."""
    template = zh if app_language(language) == "zh" else en
    return template.format(**values)


def localized_plural(
    en_one: str,
    en_other: str,
    zh: str,
    *,
    count: int | float,
    language: Any = None,
    **values: Any,
) -> str:
    """Render an English singular/plural pair or a count-neutral Chinese form."""
    resolved_language = app_language(language)
    template = zh if resolved_language == "zh" else (en_one if count == 1 else en_other)
    return template.format(count=count, **values)


__all__ = [
    "SUPPORTED_LANGUAGES",
    "accept_language",
    "app_language",
    "locale_tag",
    "localized",
    "localized_plural",
    "normalize_language",
    "system_language",
]
