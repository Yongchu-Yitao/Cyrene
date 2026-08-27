"""Management HTTP surface for the active editable Plugin registry."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agent.plugin import (
    PluginApplicationHost,
    PluginPack,
    PluginRegistry,
    PluginRegistryError,
    RegisteredPlugin,
)


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
    enabled = registry.plugin_enabled(plugin.name)
    return {
        "id": plugin.name,
        "name": plugin.name,
        "description": plugin.description,
        "kind": plugin.kind,
        "pack_id": registered.pack_id,
        "standalone": registered.pack_id is None,
        "configured_enabled": registry.plugin_configured_enabled(plugin.name),
        "effective_enabled": enabled,
        "locked": registry.plugin_locked(plugin.name),
        "model_visible": plugin.model_visible,
        "main_only": plugin.main_only,
        "source": source,
        "source_path": source_path,
    }


def _pack_value(
    registry: PluginRegistry,
    pack: PluginPack,
    registered_by_name: dict[str, RegisteredPlugin],
) -> dict[str, Any]:
    source_value = registry.pack_source(pack.id)
    source, source_path = _source_values(source_value)
    plugins = [
        _plugin_value(registry, registered_by_name[plugin.name])
        for plugin in pack.plugins
    ]
    configured_enabled = registry.pack_configured_enabled(pack.id)
    return {
        "id": pack.id,
        "name": pack.id,
        "description": pack.description,
        "configured_enabled": configured_enabled,
        "effective_enabled": any(
            plugin["effective_enabled"] for plugin in plugins
        ),
        "locked": registry.pack_locked(pack.id),
        "plugin_count": len(plugins),
        "tool_count": sum(plugin["kind"] == "tool" for plugin in plugins),
        "model_count": sum(plugin["kind"] == "model" for plugin in plugins),
        "plugins": plugins,
        "source": source,
        "source_path": source_path,
    }


def _failure_values(host: PluginApplicationHost) -> list[dict[str, str]]:
    def pack_source(pack_id: str) -> str:
        try:
            return _source_values(host.registry.pack_source(pack_id))[0]
        except PluginRegistryError:
            return "user"

    failures = [
        {
            "stage": "load",
            "path": str(failure.path),
            "pack_id": "",
            "source": "user",
            "error": failure.error,
        }
        for failure in host.load_failures
    ]
    failures.extend(
        {
            "stage": "application_setup",
            "path": "",
            "pack_id": pack_id,
            "source": pack_source(pack_id),
            "error": error,
        }
        for pack_id, error in sorted(host.setup_failures.items())
    )
    mcp_service = host.service("mcp")
    status = getattr(mcp_service, "status", None)
    if callable(status):
        failures.extend(
            {
                "stage": "mcp_connection",
                "path": str(item.get("name") or ""),
                "pack_id": str(item.get("pack_id") or ""),
                "source": "mcp",
                "error": str(item.get("error") or "MCP server is unavailable"),
            }
            for item in status()
            if str(item.get("status") or "") == "error"
            or str(item.get("error") or "")
        )
    return failures


def _directory_status(host: PluginApplicationHost) -> dict[str, Any]:
    root = host.plugin_directory
    exists = root.exists()
    is_directory = root.is_dir()
    entries: list[Path] = []
    error = ""
    if is_directory:
        try:
            entries = sorted(root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            error = str(exc)
    packs = [
        entry.name
        for entry in entries
        if not entry.name.startswith((".", "_"))
        and entry.is_dir()
        and (entry / "__init__.py").is_file()
    ]
    standalone = [
        entry.name
        for entry in entries
        if not entry.name.startswith((".", "_"))
        and entry.is_file()
        and entry.suffix == ".py"
    ]
    writable_target = root if exists else root.parent
    return {
        "path": str(root),
        "exists": exists,
        "is_directory": is_directory,
        "readable": bool(is_directory and os.access(root, os.R_OK)),
        "writable": bool(writable_target.exists() and os.access(writable_target, os.W_OK)),
        "status": "error" if error else "ready" if is_directory else "missing",
        "error": error,
        "auto_seed": True,
        "seed_manifest": str(root / ".upstream-hashes.json"),
        "seeded": (root / ".upstream-hashes.json").is_file(),
        "pack_directories": packs,
        "standalone_files": standalone,
    }


def plugin_registry_status(host: PluginApplicationHost) -> dict[str, Any]:
    registry = host.registry
    registered = registry.list_plugins()
    registered_by_name = {item.plugin.name: item for item in registered}
    packs = [
        _pack_value(registry, pack, registered_by_name)
        for pack in registry.list_packs()
    ]
    plugins = [_plugin_value(registry, item) for item in registered]
    failures = _failure_values(host)
    return {
        "ok": not failures,
        "directory": _directory_status(host),
        "packs": packs,
        "plugins": plugins,
        "standalone_plugins": [
            plugin for plugin in plugins if plugin["standalone"]
        ],
        "failures": failures,
        "attached_application_packs": list(host.attached_packs),
    }


def _seed_value(seed: Any) -> dict[str, Any]:
    return {
        "directory": str(seed.directory),
        "created": [str(path) for path in seed.created],
        "updated": [str(path) for path in seed.updated],
        "existing": [str(path) for path in seed.existing],
        "removed": [str(path) for path in seed.removed],
        "diagnostics": list(seed.diagnostics),
        "manifest": str(seed.manifest),
    }


def register_plugin_routes(
    router: APIRouter,
    host: PluginApplicationHost,
) -> None:
    @router.get("/api/plugins")
    async def api_list_plugins():
        return plugin_registry_status(host)

    @router.get("/api/plugins/failures")
    async def api_plugin_failures():
        return {"failures": _failure_values(host)}

    @router.get("/api/plugins/directory")
    async def api_plugin_directory():
        return _directory_status(host)

    @router.post("/api/plugins/reload")
    async def api_reload_plugins():
        try:
            seed, _failures = host.reload_user_plugins()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return {
            **plugin_registry_status(host),
            "reload": _seed_value(seed),
            # Routes and process services are attached once at startup. Tool and
            # model definitions are live immediately; application contribution
            # changes take effect after the process restarts.
            "application_restart_required": True,
        }


__all__ = ["plugin_registry_status", "register_plugin_routes"]
