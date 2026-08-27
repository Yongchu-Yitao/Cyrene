"""Application service for Plugin Registry activation settings."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import logging
from typing import Any

from fastapi.responses import JSONResponse

from agent.plugin import (
    PluginPack,
    PluginRegistry,
    PluginRegistryError,
    RegisteredPlugin,
)
from cyrene.localization import localized
from cyrene.runtime import config_store, settings_service, settings_store
from route.errors import localized_error_response

SettingsChangedPublisher = Callable[[str, int | None, list[str]], Awaitable[None]]
logger = logging.getLogger(__name__)


def _source_values(source: str) -> tuple[str, str | None]:
    if source == "core":
        return "core", None
    if source.startswith("mcp:"):
        return "mcp", source.removeprefix("mcp:")
    return "user", source


def _plugin_value(
    registry: PluginRegistry,
    registered: RegisteredPlugin,
    host: Any | None = None,
) -> dict[str, Any]:
    plugin = registered.plugin
    source, source_path = _source_values(registered.source)
    enabled = registry.plugin_enabled(plugin.name)
    pack_id = registered.pack_id
    operational = (
        enabled and bool(host.pack_operational(pack_id))
        if host is not None and pack_id is not None
        else enabled
    )
    return {
        "id": plugin.canonical_name,
        "name": plugin.name,
        "canonical_name": plugin.canonical_name,
        "description": plugin.description,
        "kind": plugin.kind,
        "pack_id": pack_id,
        "standalone": pack_id is None,
        "configured_enabled": registry.plugin_configured_enabled(plugin.name),
        "effective_enabled": enabled,
        "operational": operational,
        "running": (
            enabled and bool(host.pack_running(pack_id))
            if host is not None and pack_id is not None
            else False
        ),
        "startup_error": _startup_error(host, pack_id),
        "restart_required": (
            bool(host.pack_restart_required(pack_id))
            if host is not None and pack_id is not None
            else False
        ),
        "locked": registry.plugin_locked(plugin.name),
        "model_visible": plugin.model_visible,
        "main_only": plugin.main_only,
        "source": source,
        "source_path": source_path,
    }


def _pack_value(
    registry: PluginRegistry,
    pack: PluginPack,
    host: Any | None = None,
) -> dict[str, Any]:
    source, source_path = _source_values(registry.pack_source(pack.id))
    plugin_names = [plugin.canonical_name for plugin in pack.plugins]
    enabled_count = sum(
        registry.plugin_enabled(name) for name in plugin_names
    )
    effective = (
        enabled_count > 0
        if plugin_names
        else registry.pack_configured_enabled(pack.id)
    )
    return {
        "id": pack.id,
        "name": pack.id,
        "description": pack.description,
        "plugins": plugin_names,
        "configured_enabled": registry.pack_configured_enabled(pack.id),
        "effective_enabled": effective,
        "operational": (
            bool(host.pack_operational(pack.id)) if host is not None else effective
        ),
        "running": bool(host.pack_running(pack.id)) if host is not None else False,
        "startup_error": _startup_error(host, pack.id),
        "restart_required": (
            bool(host.pack_restart_required(pack.id)) if host is not None else False
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

    from agent.plugin import active_plugin_application_host

    active_host = active_plugin_application_host()
    host = (
        active_host
        if active_host is not None and active_host.registry is registry
        else None
    )

    plugins = [
        _plugin_value(registry, registered, host)
        for registered in registry.list_plugins()
    ]
    packs = [_pack_value(registry, pack, host) for pack in registry.list_packs()]
    return {
        "plugins": plugins,
        "packs": packs,
        "standalone_plugins": [
            plugin for plugin in plugins if plugin["standalone"]
        ],
    }


def _startup_error(host: Any | None, pack_id: str | None) -> str:
    if host is None or pack_id is None or not host.startup_failures.get(pack_id):
        return ""
    return localized("Plugin failed to start.", "插件启动失败。")


def _bad_request(
    en: str,
    zh: str,
    code: str,
    **details: Any,
) -> JSONResponse:
    return localized_error_response(en, zh, 400, code, **details)


def _activation_update_error(
    body: Any,
    registry: PluginRegistry,
) -> JSONResponse | None:
    if not isinstance(body, Mapping):
        return _bad_request(
            "request body must be an object",
            "请求体必须是对象。",
            "invalid_plugin_activation",
        )
    plugins = body.get("plugins")
    packs = body.get("packs")
    if not (isinstance(plugins, Mapping) and plugins) and not (
        isinstance(packs, Mapping) and packs
    ):
        return _bad_request(
            "plugins or packs must be a non-empty object",
            "plugins 或 packs 必须是非空对象。",
            "empty_plugin_activation",
        )
    if plugins is not None and not isinstance(plugins, Mapping):
        return _bad_request(
            "plugins must be an object",
            "plugins 必须是对象。",
            "invalid_plugin_values",
        )
    if packs is not None and not isinstance(packs, Mapping):
        return _bad_request(
            "packs must be an object",
            "packs 必须是对象。",
            "invalid_plugin_pack_values",
        )

    invalid_plugins = [
        str(name)
        for name, enabled in (plugins or {}).items()
        if not isinstance(enabled, bool)
    ]
    if invalid_plugins:
        names = ", ".join(sorted(invalid_plugins))
        return _bad_request(
            "Plugin values must be booleans: " + names,
            "插件开关值必须是布尔值：" + names,
            "invalid_plugin_values",
            plugins=sorted(invalid_plugins),
        )
    invalid_packs = [
        str(name)
        for name, enabled in (packs or {}).items()
        if not isinstance(enabled, bool)
    ]
    if invalid_packs:
        names = ", ".join(sorted(invalid_packs))
        return _bad_request(
            "Plugin pack values must be booleans: " + names,
            "插件包开关值必须是布尔值：" + names,
            "invalid_plugin_pack_values",
            packs=sorted(invalid_packs),
        )

    registered_plugins = {
        item.plugin.canonical_name for item in registry.list_plugins()
    }
    registered_packs = {pack.id for pack in registry.list_packs()}
    unknown_plugins = sorted(set(map(str, plugins or {})) - registered_plugins)
    if unknown_plugins:
        names = ", ".join(unknown_plugins)
        return _bad_request(
            "unknown Plugin(s): " + names,
            "未知插件：" + names,
            "unknown_plugins",
            plugins=unknown_plugins,
        )
    unknown_packs = sorted(set(map(str, packs or {})) - registered_packs)
    if unknown_packs:
        names = ", ".join(unknown_packs)
        return _bad_request(
            "unknown Plugin pack(s): " + names,
            "未知插件包：" + names,
            "unknown_plugin_packs",
            packs=unknown_packs,
        )

    locked_plugins = sorted(
        str(name)
        for name in (plugins or {})
        if registry.plugin_locked(str(name))
    )
    if locked_plugins:
        names = ", ".join(locked_plugins)
        return _bad_request(
            "locked Plugin(s) cannot be changed: " + names,
            "无法更改已锁定的插件：" + names,
            "locked_plugins",
            plugins=locked_plugins,
        )
    locked_packs = sorted(
        str(name)
        for name in (packs or {})
        if registry.pack_locked(str(name))
    )
    if locked_packs:
        names = ", ".join(locked_packs)
        return _bad_request(
            "locked Plugin pack(s) cannot be changed: " + names,
            "无法更改已锁定的插件包：" + names,
            "locked_plugin_packs",
            packs=locked_packs,
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
            from agent.plugin import active_plugin_application_host

            host = active_plugin_application_host()
            if host is not None and host.registry is self._registry:
                await host.reconcile_activation()
        except config_store.SettingsRevisionConflict as exc:
            return localized_error_response(
                "Plugin settings were changed by another client.",
                "插件设置已被其他客户端更改。",
                409,
                "settings_revision_conflict",
                revision=exc.actual,
            )
        except (settings_service.SettingsServiceError, PluginRegistryError) as exc:
            logger.info("Invalid Plugin activation update", exc_info=True)
            return _bad_request(
                "Plugin settings are invalid.",
                "插件设置无效。",
                "invalid_plugin_activation",
            )

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
