"""Validated search-provider settings and encrypted credential storage."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from cyrene.runtime import config_store

PROVIDER_IDS = ("simplexng", "deepseek", "tavily", "brave")
PROVIDER_API_KEY_ENV = {
    "tavily": "TAVILY_API_KEY",
    "brave": "BRAVE_SEARCH_API_KEY",
}
DEFAULT_PROVIDER_ENABLED = {
    "simplexng": True,
    "deepseek": True,
    "tavily": False,
    "brave": False,
}


class SearchSettingsError(ValueError):
    """Invalid search-provider configuration."""


@dataclass(frozen=True, slots=True)
class SearchRuntimeSettings:
    enabled: bool
    providers: tuple[str, ...]


def _default_order() -> tuple[str, ...]:
    primary = (
        ("deepseek", "simplexng")
        if sys.platform == "win32"
        else ("simplexng", "deepseek")
    )
    return (*primary, "tavily", "brave")


def _stored_search() -> dict[str, Any]:
    value = config_store.get_setting("search", {})
    return dict(value) if isinstance(value, dict) else {}


def _ordered_provider_ids(raw: dict[str, Any]) -> list[str]:
    saved_order = raw.get("provider_order")
    order = [
        str(provider)
        for provider in (saved_order if isinstance(saved_order, list) else [])
        if str(provider) in PROVIDER_IDS
    ]
    order.extend(provider for provider in _default_order() if provider not in order)
    return order


def _enabled_provider_map(raw: dict[str, Any]) -> dict[str, bool]:
    enabled = dict(DEFAULT_PROVIDER_ENABLED)
    saved = raw.get("provider_enabled")
    if isinstance(saved, dict):
        enabled.update({
            provider: value
            for provider, value in saved.items()
            if provider in PROVIDER_IDS and isinstance(value, bool)
        })
    return enabled


def runtime_settings() -> SearchRuntimeSettings:
    raw = _stored_search()
    order = _ordered_provider_ids(raw)
    enabled_map = _enabled_provider_map(raw)
    return SearchRuntimeSettings(
        enabled=config_store.is_tool_enabled("WebSearch"),
        providers=tuple(provider for provider in order if enabled_map[provider]),
    )


def provider_api_key(provider: str) -> str:
    env_key = PROVIDER_API_KEY_ENV.get(str(provider))
    return str(config_store.get_env(env_key, "") or "").strip() if env_key else ""


def public_settings() -> dict[str, Any]:
    raw = _stored_search()
    enabled_map = _enabled_provider_map(raw)
    order = _ordered_provider_ids(raw)
    return {
        "revision": config_store.get_settings_revision(),
        "enabled": config_store.is_tool_enabled("WebSearch"),
        "providers": [
            {
                "id": provider,
                "enabled": bool(enabled_map.get(provider, False)),
                "requires_api_key": provider in PROVIDER_API_KEY_ENV,
                "api_key_configured": (
                    bool(provider_api_key(provider))
                    if provider in PROVIDER_API_KEY_ENV
                    else True
                ),
            }
            for provider in order
        ],
    }


def _normalized_provider_rows(
    rows: Any,
) -> tuple[list[str], dict[str, bool], dict[str, str]]:
    if not isinstance(rows, list) or not rows:
        raise SearchSettingsError("providers must be a non-empty list")
    order: list[str] = []
    enabled: dict[str, bool] = {}
    env_updates: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise SearchSettingsError("each provider must be an object")
        provider = str(row.get("id") or "").strip().lower()
        if provider not in PROVIDER_IDS or provider in order:
            raise SearchSettingsError(f"invalid or duplicate search provider: {provider}")
        is_enabled = row.get("enabled")
        if not isinstance(is_enabled, bool):
            raise SearchSettingsError(f"{provider}.enabled must be a boolean")
        order.append(provider)
        enabled[provider] = is_enabled
        env_key = PROVIDER_API_KEY_ENV.get(provider)
        if not env_key:
            continue
        if row.get("clear_api_key") is True:
            env_updates[env_key] = ""
        elif "api_key" in row:
            api_key = str(row.get("api_key") or "").strip()
            if len(api_key) > 1000:
                raise SearchSettingsError(f"{provider} API key is too long")
            if api_key:
                env_updates[env_key] = api_key
    if set(order) != set(PROVIDER_IDS):
        missing = ", ".join(provider for provider in PROVIDER_IDS if provider not in order)
        raise SearchSettingsError(f"missing search providers: {missing}")
    return order, enabled, env_updates


def update_settings(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise SearchSettingsError("search settings must be an object")
    master_enabled = body.get("enabled")
    if not isinstance(master_enabled, bool):
        raise SearchSettingsError("enabled must be a boolean")
    order, enabled_map, env_updates = _normalized_provider_rows(body.get("providers"))
    if master_enabled and not any(enabled_map.values()):
        raise SearchSettingsError("enable at least one search provider")
    current_tools = config_store.get_enabled_tools()
    current_tools["WebSearch"] = master_enabled
    revision, _settings = config_store.update_settings_and_env_atomic(
        {
            "search": {
                "provider_order": order,
                "provider_enabled": enabled_map,
            },
            "enabled_tools": current_tools,
        },
        env_updates,
        expected_revision=body.get("expected_revision"),
    )
    return {"ok": True, "revision": revision, **public_settings()}


__all__ = [
    "PROVIDER_IDS",
    "SearchRuntimeSettings",
    "SearchSettingsError",
    "provider_api_key",
    "public_settings",
    "runtime_settings",
    "update_settings",
]
