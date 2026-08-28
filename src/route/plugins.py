"""Management HTTP surface for the active editable Plugin registry."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from agent.hook import configure_hook_action_provider, configure_hook_override_provider
from agent.plugin import (
    PluginApplicationHost,
    PluginPack,
    PluginRegistryError,
    RegisteredPlugin,
)
from agent.plugin.registry import PluginNotFoundError
from agent.workbench.hook_listing import (
    runtime_hook_listing,
    runtime_hook_action,
    runtime_hook_override,
    update_runtime_hook,
)
from cyrene.localization import localized
from route.errors import localized_error_response

logger = logging.getLogger(__name__)


def _source_values(source: str) -> tuple[str, str | None]:
    if source == "core":
        return "core", None
    if source.startswith("mcp:"):
        return "mcp", source.removeprefix("mcp:")
    return "user", source


def _seeded_contribution_names(directory: Path) -> frozenset[str]:
    """Return top-level sources owned by Cyrene's editable seed manifest."""

    manifest = directory / ".upstream-hashes.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return frozenset()
    files = payload.get("files") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(files, dict)
    ):
        return frozenset()
    return frozenset(
        parts[0]
        for relative in files
        if isinstance(relative, str)
        and (parts := Path(relative).parts)
        and parts[0] not in {".", ".."}
    )


def _user_created_source(source: str, seeded: frozenset[str]) -> bool:
    kind, source_path = _source_values(source)
    return bool(
        kind == "user"
        and source_path
        and Path(source_path).name not in seeded
    )


def _plugin_value(
    host: PluginApplicationHost,
    registered: RegisteredPlugin,
    seeded: frozenset[str],
) -> dict[str, Any]:
    registry = host.registry
    plugin = registered.plugin
    source, source_path = _source_values(registered.source)
    enabled = registry.plugin_enabled(plugin.name)
    customization = registry.customizations.get(plugin.canonical_name)
    pack_id = registered.pack_id
    operational = (
        enabled and host.pack_operational(pack_id)
        if pack_id is not None
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
            enabled and host.pack_running(pack_id)
            if pack_id is not None
            else False
        ),
        "startup_error": (
            host.startup_failures.get(pack_id, "") if pack_id is not None else ""
        ),
        "restart_required": (
            host.pack_restart_required(pack_id) if pack_id is not None else False
        ),
        "locked": registry.plugin_locked(plugin.name),
        "model_visible": plugin.model_visible,
        "agent_exposure": (
            "direct" if source == "core" and plugin.kind == "tool"
            else plugin.agent_exposure
        ),
        "customized": bool(customization),
        "customized_name": "name" in customization,
        "customized_description": "description" in customization,
        "i18n": dict(plugin.metadata.get("i18n", {})),
        "main_only": plugin.main_only,
        "source": source,
        "source_path": source_path,
        "user_created": _user_created_source(registered.source, seeded),
    }


def _pack_value(
    host: PluginApplicationHost,
    pack: PluginPack,
    registered_by_name: dict[str, RegisteredPlugin],
    seeded: frozenset[str],
) -> dict[str, Any]:
    registry = host.registry
    source_value = registry.pack_source(pack.id)
    source, source_path = _source_values(source_value)
    plugins = [
        _plugin_value(host, registered_by_name[plugin.name], seeded)
        for plugin in pack.plugins
    ]
    configured_enabled = registry.pack_configured_enabled(pack.id)
    return {
        "id": pack.id,
        "name": pack.id,
        "description": pack.description,
        "i18n": dict(pack.metadata.get("i18n", {})),
        "configured_enabled": configured_enabled,
        "effective_enabled": (
            any(plugin["effective_enabled"] for plugin in plugins)
            if plugins
            else configured_enabled
        ),
        "operational": host.pack_operational(pack.id),
        "running": host.pack_running(pack.id),
        "startup_error": host.startup_failures.get(pack.id, ""),
        "restart_required": host.pack_restart_required(pack.id),
        "locked": registry.pack_locked(pack.id),
        "enabled_count": sum(
            plugin["effective_enabled"] for plugin in plugins
        ),
        "plugin_count": len(plugins),
        "tool_count": sum(plugin["kind"] == "tool" for plugin in plugins),
        "model_count": sum(plugin["kind"] == "model" for plugin in plugins),
        "plugins": plugins,
        "source": source,
        "source_path": source_path,
        "user_created": _user_created_source(source_value, seeded),
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
    failures.extend(
        {
            "stage": "application_startup",
            "path": "",
            "pack_id": pack_id,
            "source": pack_source(pack_id),
            "error": error,
        }
        for pack_id, error in sorted(host.startup_failures.items())
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
        except OSError:
            logger.warning("Could not read Plugin directory %s", root, exc_info=True)
            error = localized(
                "The Plugin directory could not be read.",
                "无法读取插件目录。",
            )
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
    seeded = _seeded_contribution_names(host.plugin_directory)
    registered = registry.list_plugins()
    registered_by_name = {item.plugin.name: item for item in registered}
    packs = [
        _pack_value(host, pack, registered_by_name, seeded)
        for pack in registry.list_packs()
    ]
    plugins = [_plugin_value(host, item, seeded) for item in registered]
    failures = _failure_values(host)
    frontend = host.frontend_contributions()
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
        "restart_required_packs": list(host.restart_required_packs),
        "application_restart_required": bool(host.restart_required_packs),
        "frontend_views": frontend["views"],
        "project_tools": frontend["project_tools"],
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
    configure_hook_override_provider(runtime_hook_override)
    configure_hook_action_provider(runtime_hook_action)

    @router.get("/api/plugins")
    async def api_list_plugins():
        return plugin_registry_status(host)

    @router.get("/api/hooks")
    async def api_list_hooks():
        cli_service = host.service("cli")
        custom_listing = getattr(cli_service, "hook_listing", None)
        if callable(custom_listing):
            payload = custom_listing()
        else:
            from agent.plugin.plugin_impl.cyrene_cli.hooks import (
                CliHookService,
                public_hook,
                public_proposal,
            )

            stored_hooks = CliHookService()
            payload = {
                "hooks": [public_hook(item) for item in stored_hooks.list()],
                "proposals": [
                    public_proposal(item) for item in stored_hooks.proposals()
                ],
                "configuration_results": stored_hooks.configuration_results(),
            }
        return {
            **payload,
            "system_hooks": runtime_hook_listing(host.db_path),
            "custom_available": callable(custom_listing),
        }

    @router.put("/api/hooks/system/{hook_id}")
    async def api_update_system_hook(hook_id: str, request: Request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            return update_runtime_hook(host.db_path, hook_id, payload)
        except LookupError:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "system_hook_not_found",
                    "error": localized(
                        "The system automatic trigger was not found.",
                        "未找到该系统自动触发。",
                    ),
                },
                status_code=404,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Invalid system Hook update: %s", exc)
            return JSONResponse(
                {
                    "ok": False,
                    "code": "system_hook_update_invalid",
                    "error": localized(
                        "The system automatic trigger configuration is invalid.",
                        "系统自动触发配置无效。",
                    ),
                },
                status_code=400,
            )

    @router.get("/api/plugins/failures")
    async def api_plugin_failures():
        return {"failures": _failure_values(host)}

    @router.get("/api/plugins/directory")
    async def api_plugin_directory():
        return _directory_status(host)

    @router.get("/api/plugins/packs/{pack_id}/assets/{asset_path:path}")
    async def api_plugin_frontend_asset(pack_id: str, asset_path: str):
        try:
            target = host.frontend_asset_path(pack_id, asset_path)
        except (OSError, PluginRegistryError, ValueError):
            return localized_error_response(
                "Plugin view asset not found.",
                "未找到插件视图资源。",
                404,
                "plugin_view_asset_not_found",
            )
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(
            target,
            media_type=media_type,
            headers={
                "Cache-Control": "no-cache",
                "Content-Security-Policy": (
                    "default-src 'self' data: blob:; "
                    "script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: blob: https:; "
                    "media-src 'self' data: blob: https:; "
                    "connect-src 'none'"
                ),
            },
        )

    @router.post("/api/plugins/packs/{pack_id}/call")
    async def api_call_plugin_frontend(pack_id: str, request: Request):
        try:
            body = await request.json()
            method = str(body.get("method") or "").strip()
            if not method:
                raise ValueError("method is required")
            result = await host.call_frontend_method(
                pack_id,
                method,
                body.get("args"),
                project_id=str(body.get("project_id") or ""),
            )
        except (OSError, PluginRegistryError, TypeError, ValueError):
            logger.warning("Plugin frontend call failed: %s", pack_id, exc_info=True)
            return localized_error_response(
                "The Plugin view request failed.",
                "插件视图请求失败。",
                400,
                "plugin_view_call_failed",
            )
        return {"ok": True, "result": result}

    @router.post("/api/plugins/reload")
    async def api_reload_plugins():
        try:
            seed, _failures = await host.reload_user_plugins()
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.exception("Plugin reload failed")
            return localized_error_response(
                "Plugins could not be reloaded.",
                "无法重新加载插件。",
                500,
                "plugin_reload_failed",
            )
        return {
            **plugin_registry_status(host),
            "reload": _seed_value(seed),
            # Routes and process services are attached once at startup. Tool and
            # model definitions are live immediately; application contribution
            # changes take effect after the process restarts.
            "application_restart_required": bool(host.restart_required_packs),
        }

    @router.delete("/api/plugins/packs/{pack_id}")
    async def api_delete_plugin_pack(pack_id: str):
        """Delete one user pack and persist a canonical seed tombstone first."""

        try:
            source = host.registry.pack_source(pack_id)
            source_path = Path(source)
            if source == "core" or source_path.parent != host.plugin_directory:
                raise PluginRegistryError(
                    f"Plugin pack is not a managed user pack: {pack_id}"
                )
            from agent.plugin.native_tools import mark_builtin_plugin_deleted

            mark_builtin_plugin_deleted(host.plugin_directory, source_path.name)
            if source_path.is_dir() and not source_path.is_symlink():
                shutil.rmtree(source_path)
            else:
                source_path.unlink()
            seed, _failures = await host.reload_user_plugins()
        except PluginNotFoundError:
            return localized_error_response(
                "Plugin pack not found.",
                "未找到插件包。",
                404,
                "plugin_pack_not_found",
            )
        except (OSError, PluginRegistryError, RuntimeError, TypeError, ValueError):
            logger.warning("Plugin pack deletion failed: %s", pack_id, exc_info=True)
            return localized_error_response(
                "The Plugin pack could not be removed.",
                "无法移除该插件包。",
                400,
                "plugin_pack_remove_failed",
            )
        return {
            **plugin_registry_status(host),
            "reload": _seed_value(seed),
            "application_restart_required": bool(host.restart_required_packs),
        }

    @router.patch("/api/plugins/tools/{canonical_name}")
    async def api_update_plugin_tool(
        canonical_name: str,
        body: dict[str, Any],
    ):
        allowed = {"name", "description", "agent_exposure"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            return localized_error_response(
                "Unknown tool setting: {settings}.",
                "未知的工具设置：{settings}。",
                400,
                "unknown_tool_setting",
                settings=", ".join(unknown),
            )
        try:
            registered = host.registry.customize_tool(canonical_name, body)
            if registered is None:
                raise RuntimeError("Tool was unexpectedly deleted")
            from cyrene.runtime import settings_store

            settings_store.set_(
                "plugin_tool_customizations",
                host.registry.customizations.snapshot(),
            )
        except PluginNotFoundError:
            return localized_error_response(
                "Tool Plugin not found.",
                "未找到工具插件。",
                404,
                "tool_plugin_not_found",
            )
        except (PluginRegistryError, TypeError, ValueError, RuntimeError):
            logger.warning("Plugin tool update failed: %s", canonical_name, exc_info=True)
            return localized_error_response(
                "The tool Plugin could not be updated.",
                "无法更新该工具插件。",
                400,
                "tool_plugin_update_failed",
            )
        return plugin_registry_status(host)

    @router.delete("/api/plugins/tools/{canonical_name}")
    async def api_delete_plugin_tool(canonical_name: str):
        try:
            host.registry.customize_tool(canonical_name, {"deleted": True})
            from cyrene.runtime import settings_store

            settings_store.set_(
                "plugin_tool_customizations",
                host.registry.customizations.snapshot(),
            )
        except PluginNotFoundError:
            return localized_error_response(
                "Tool Plugin not found.",
                "未找到工具插件。",
                404,
                "tool_plugin_not_found",
            )
        except (PluginRegistryError, TypeError, ValueError):
            logger.warning("Plugin tool deletion failed: %s", canonical_name, exc_info=True)
            return localized_error_response(
                "The tool Plugin could not be removed.",
                "无法移除该工具插件。",
                400,
                "tool_plugin_remove_failed",
            )
        return plugin_registry_status(host)


__all__ = ["plugin_registry_status", "register_plugin_routes"]
