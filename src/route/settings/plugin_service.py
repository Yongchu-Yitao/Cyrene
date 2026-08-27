"""Application service for Plugin Registry activation settings."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi.responses import JSONResponse

from agent.plugin import (
    PluginPack,
    PluginRegistry,
    PluginRegistryError,
    RegisteredPlugin,
)
from cyrene.runtime import config_store, settings_service, settings_store

SettingsChangedPublisher = Callable[[str, int | None, list[str]], Awaitable[None]]


def _source_values(source: str) -> tuple[str, str | None]:
    if source == "core":
        return "core", None
    if source.startswith("mcp:"):
        return "mcp", source.removeprefix("mcp:")
    return "user", source


def _plugin_value(
    registry: PluginRegistry,
    registered: RegisteredPlugin,
) -> dict[str, Any]:
    plugin = registered.plugin
    source, source_path = _source_values(registered.source)
    return {
        "id": plugin.canonical_name,
        "name": plugin.name,
        "canonical_name": plugin.canonical_name,
        "description": plugin.description,
        "kind": plugin.kind,
        "pack_id": registered.pack_id,
        "standalone": registered.pack_id is None,
        "configured_enabled": registry.plugin_configured_enabled(plugin.name),
        "effective_enabled": registry.plugin_enabled(plugin.name),
        "locked": registry.plugin_locked(plugin.name),
        "model_visible": plugin.model_visible,
        "main_only": plugin.main_only,
        "source": source,
        "source_path": source_path,
    }


def _pack_value(registry: PluginRegistry, pack: PluginPack) -> dict[str, Any]:
    source, source_path = _source_values(registry.pack_source(pack.id))
    plugin_names = [plugin.canonical_name for plugin in pack.plugins]
    enabled_count = sum(
        registry.plugin_enabled(name) for name in plugin_names
    )
    return {
        "id": pack.id,
        "name": pack.id,
        "description": pack.description,
        "plugins": plugin_names,
        "configured_enabled": registry.pack_configured_enabled(pack.id),
        "effective_enabled": (
            enabled_count > 0 if plugin_names
            else registry.pack_configured_enabled(pack.id)
        ),
        "enabled_count": enabled_count,
        "plugin_count": len(plugin_names),
        "tool_count": sum(plugin.kind == "tool" for plugin in pack.plugins),
        "model_count": sum(plugin.kind == "model" for plugin in pack.plugins),
        "locked": registry.pack_locked(pack.id),
        "source": source,
        "source_path": source_path,
    }


def get_plugin_settings(registry: PluginRegistry) -> dict[str, Any]:
    """Return only contributions registered in the active Plugin framework."""

    plugins = [
        _plugin_value(registry, registered)
        for registered in registry.list_plugins()
    ]
    packs = [_pack_value(registry, pack) for pack in registry.list_packs()]
    return {
        "plugins": plugins,
        "packs": packs,
        "standalone_plugins": [
            plugin for plugin in plugins if plugin["standalone"]
        ],
    }


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


def _activation_update_error(
    body: Any,
    registry: PluginRegistry,
) -> JSONResponse | None:
    if not isinstance(body, Mapping):
        return _bad_request("request body must be an object")
    plugins = body.get("plugins")
    packs = body.get("packs")
    if not (isinstance(plugins, Mapping) and plugins) and not (
        isinstance(packs, Mapping) and packs
    ):
        return _bad_request("plugins or packs must be a non-empty object")
    if plugins is not None and not isinstance(plugins, Mapping):
        return _bad_request("plugins must be an object")
    if packs is not None and not isinstance(packs, Mapping):
        return _bad_request("packs must be an object")

    invalid_plugins = [
        str(name)
        for name, enabled in (plugins or {}).items()
        if not isinstance(enabled, bool)
    ]
    if invalid_plugins:
        return _bad_request(
            "Plugin values must be booleans: "
            + ", ".join(sorted(invalid_plugins))
        )
    invalid_packs = [
        str(name)
        for name, enabled in (packs or {}).items()
        if not isinstance(enabled, bool)
    ]
    if invalid_packs:
        return _bad_request(
            "Plugin pack values must be booleans: "
            + ", ".join(sorted(invalid_packs))
        )

    registered_plugins = {
        item.plugin.canonical_name for item in registry.list_plugins()
    }
    registered_packs = {pack.id for pack in registry.list_packs()}
    unknown_plugins = sorted(set(map(str, plugins or {})) - registered_plugins)
    if unknown_plugins:
        return _bad_request(
            "unknown Plugin(s): " + ", ".join(unknown_plugins)
        )
    unknown_packs = sorted(set(map(str, packs or {})) - registered_packs)
    if unknown_packs:
        return _bad_request(
            "unknown Plugin pack(s): " + ", ".join(unknown_packs)
        )

    locked_plugins = sorted(
        str(name)
        for name in (plugins or {})
        if registry.plugin_locked(str(name))
    )
    if locked_plugins:
        return _bad_request(
            "locked Plugin(s) cannot be changed: "
            + ", ".join(locked_plugins)
        )
    locked_packs = sorted(
        str(name)
        for name in (packs or {})
        if registry.pack_locked(str(name))
    )
    if locked_packs:
        return _bad_request(
            "locked Plugin pack(s) cannot be changed: "
            + ", ".join(locked_packs)
        )
    return None


class PluginSettingsApplicationService:
    def __init__(
        self,
        registry: PluginRegistry,
        publish_settings_changed: SettingsChangedPublisher,
    ) -> None:
        self._registry = registry
        self._publish_settings_changed = publish_settings_changed

    def get_settings(self) -> dict[str, Any]:
        return get_plugin_settings(self._registry)

    async def update_activation(
        self,
        body: dict[str, Any],
    ) -> dict[str, Any] | JSONResponse:
        error = _activation_update_error(body, self._registry)
        if error is not None:
            return error
        plugin_updates = {
            str(name): enabled
            for name, enabled in (body.get("plugins") or {}).items()
        }
        pack_updates = {
            str(name): enabled
            for name, enabled in (body.get("packs") or {}).items()
        }
        changes: dict[str, Any] = {}
        if plugin_updates:
            changes["enabled_plugins"] = plugin_updates
        if pack_updates:
            changes["enabled_plugin_packs"] = pack_updates
        try:
            result = settings_service.update(
                "runtime",
                changes,
                actor="ui",
                expected_revision=body.get("expected_revision"),
            )
            self._registry.configure_activation(
                plugins=settings_store.get_enabled_plugins(),
                packs=settings_store.get_enabled_plugin_packs(),
            )
        except config_store.SettingsRevisionConflict as exc:
            return JSONResponse(
                {"error": str(exc), "revision": exc.actual},
                status_code=409,
            )
        except (settings_service.SettingsServiceError, PluginRegistryError) as exc:
            return _bad_request(str(exc))

        await self._publish_settings_changed(
            "runtime",
            result["revision"],
            list(changes),
        )
        return {
            "ok": True,
            "revision": result["revision"],
            **get_plugin_settings(self._registry),
        }


__all__ = ["PluginSettingsApplicationService", "get_plugin_settings"]
