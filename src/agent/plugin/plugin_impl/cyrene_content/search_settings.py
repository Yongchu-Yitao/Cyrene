"""Search-provider settings owned by the editable content Plugin pack."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent.plugin import (
    PluginApplicationContext,
    PluginNotFoundError,
    PluginRegistry,
    active_plugin_application_host,
)
from cyrene.runtime import config_store
from fastapi import Request
from fastapi.responses import JSONResponse

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

SettingsChangedPublisher = Callable[
    [str, int | None, list[str]],
    Awaitable[None],
]


class SearchSettingsError(ValueError):
    """Invalid search-provider configuration."""


class SearchSettingsApplicationError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int,
        revision: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.revision = revision


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
    order.extend(
        provider for provider in _default_order() if provider not in order
    )
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


def _active_registry() -> PluginRegistry | None:
    host = active_plugin_application_host()
    return host.registry if host is not None else None


def _plugin_enabled(
    canonical_name: str,
    registry: PluginRegistry | None = None,
    *,
    configured: bool = False,
) -> bool:
    active_registry = registry or _active_registry()
    if active_registry is None:
        # Isolated provider tests do not build an application host. Actual
        # application calls always receive the owning pack's registry.
        return True
    try:
        if configured:
            return active_registry.plugin_configured_enabled(canonical_name)
        return active_registry.plugin_enabled(canonical_name)
    except Exception:
        return False


def runtime_settings(
    canonical_name: str = "WebSearch",
    *,
    registry: PluginRegistry | None = None,
) -> SearchRuntimeSettings:
    raw = _stored_search()
    order = _ordered_provider_ids(raw)
    enabled_map = _enabled_provider_map(raw)
    return SearchRuntimeSettings(
        enabled=_plugin_enabled(canonical_name, registry),
        providers=tuple(
            provider for provider in order if enabled_map[provider]
        ),
    )


def provider_api_key(provider: str) -> str:
    env_key = PROVIDER_API_KEY_ENV.get(str(provider))
    return (
        str(config_store.get_env(env_key, "") or "").strip()
        if env_key
        else ""
    )


def public_settings(
    canonical_name: str,
    registry: PluginRegistry,
) -> dict[str, Any]:
    raw = _stored_search()
    enabled_map = _enabled_provider_map(raw)
    order = _ordered_provider_ids(raw)
    return {
        "revision": config_store.get_settings_revision(),
        "enabled": _plugin_enabled(
            canonical_name,
            registry,
            configured=True,
        ),
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
            raise SearchSettingsError(
                f"invalid or duplicate search provider: {provider}"
            )
        is_enabled = row.get("enabled")
        if not isinstance(is_enabled, bool):
            raise SearchSettingsError(
                f"{provider}.enabled must be a boolean"
            )
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
                raise SearchSettingsError(
                    f"{provider} API key is too long"
                )
            if api_key:
                env_updates[env_key] = api_key
    if set(order) != set(PROVIDER_IDS):
        missing = ", ".join(
            provider for provider in PROVIDER_IDS if provider not in order
        )
        raise SearchSettingsError(f"missing search providers: {missing}")
    return order, enabled, env_updates


def update_settings(
    body: Any,
    *,
    canonical_name: str,
    registry: PluginRegistry,
) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise SearchSettingsError("search settings must be an object")
    master_enabled = body.get("enabled")
    if not isinstance(master_enabled, bool):
        raise SearchSettingsError("enabled must be a boolean")
    order, enabled_map, env_updates = _normalized_provider_rows(
        body.get("providers")
    )
    if master_enabled and not any(enabled_map.values()):
        raise SearchSettingsError("enable at least one search provider")

    # Resolve the editable display name back to the stable contribution
    # identity, then persist only that identity in generic Plugin activation.
    canonical = registry.registered_by_canonical(
        canonical_name
    ).plugin.canonical_name
    current_plugins = config_store.get_enabled_plugins()
    current_plugins[canonical] = master_enabled
    revision, settings = config_store.update_settings_and_env_atomic(
        {
            "search": {
                "provider_order": order,
                "provider_enabled": enabled_map,
            },
            "enabled_plugins": current_plugins,
        },
        env_updates,
        expected_revision=body.get("expected_revision"),
    )
    activation = registry.activation.snapshot()
    registry.configure_activation(
        plugins=dict(settings.get("enabled_plugins") or {}),
        packs=activation.packs,
    )
    return {
        "ok": True,
        "revision": revision,
        **public_settings(canonical, registry),
    }


class SearchSettingsApplicationService:
    def __init__(
        self,
        registry: PluginRegistry,
        canonical_name: str,
        publish_settings_changed: SettingsChangedPublisher,
    ) -> None:
        self._registry = registry
        self._canonical_name = canonical_name
        self._publish_settings_changed = publish_settings_changed

    def get_settings(self) -> dict[str, Any]:
        return public_settings(self._canonical_name, self._registry)

    async def update_settings(self, body: Any) -> dict[str, Any]:
        try:
            result = update_settings(
                body,
                canonical_name=self._canonical_name,
                registry=self._registry,
            )
        except config_store.SettingsRevisionConflict as exc:
            raise SearchSettingsApplicationError(
                str(exc), 409, exc.actual
            ) from exc
        except SearchSettingsError as exc:
            raise SearchSettingsApplicationError(str(exc), 400) from exc
        await self._publish_settings_changed(
            "search",
            result["revision"],
            ["search", "enabled_plugins"],
        )
        return result


async def _publish_settings_changed(
    namespace: str,
    revision: int | None,
    changed: list[str],
) -> None:
    from cyrene.observability import debug

    await debug.publish_event({
        "type": "settings_changed",
        "namespace": namespace,
        "revision": revision,
        "changed": list(changed),
    })


def install_search_settings(
    context: PluginApplicationContext,
    *,
    canonical_name: str,
) -> bool:
    registry = context.registry
    if not isinstance(registry, PluginRegistry):
        raise RuntimeError(
            "cyrene_content requires the Plugin application registry"
        )
    try:
        registry.registered_by_canonical(canonical_name)
    except PluginNotFoundError:
        return False
    service = SearchSettingsApplicationService(
        registry,
        canonical_name,
        _publish_settings_changed,
    )

    @context.router.get("/api/settings/search")
    async def api_get_search_settings():
        return service.get_settings()

    @context.router.put("/api/settings/search")
    async def api_update_search_settings(request: Request):
        try:
            return await service.update_settings(await request.json())
        except SearchSettingsApplicationError as exc:
            payload: dict[str, Any] = {"error": str(exc)}
            if exc.revision is not None:
                payload["revision"] = exc.revision
            return JSONResponse(payload, status_code=exc.status_code)
    return True


__all__ = [
    "DEFAULT_PROVIDER_ENABLED",
    "PROVIDER_API_KEY_ENV",
    "PROVIDER_IDS",
    "SearchRuntimeSettings",
    "SearchSettingsApplicationError",
    "SearchSettingsApplicationService",
    "SearchSettingsError",
    "install_search_settings",
    "provider_api_key",
    "public_settings",
    "runtime_settings",
    "update_settings",
]
