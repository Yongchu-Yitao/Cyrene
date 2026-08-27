"""Editable MCP application Plugin and dynamic external-tool projection."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from cyrene.localization import localized

from agent.plugin import (
    PluginApplicationContext,
    PluginPack,
    PluginSetupContext,
    active_plugin_service,
)

from .service import MCPPluginService


def _registry(services):
    model = services.get("model")
    registry = getattr(model, "registry", None)
    if registry is None:
        raise RuntimeError("cyrene_mcp requires the native Plugin model service")
    return registry


def setup(context: PluginSetupContext) -> None:
    # Session setup never creates a second MCP runtime. The process-level
    # application contribution is the sole owner of service lifecycle.
    service = context.services.get("mcp") or active_plugin_service("mcp")
    if service is None:
        return
    service.attach_registry(_registry(context.services))
    if context.services.get("mcp") is None:
        context.provide("mcp", service)


def application_setup(context: PluginApplicationContext) -> None:
    service = MCPPluginService(data_directory=context.data_directory)
    service.attach_registry(_registry(context.services), authoritative=True)
    context.provide("mcp", service)
    from cyrene.runtime.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
    )

    context.provide(
        "mcp_settings",
        PluginSettingsContribution(controls=(
            SettingControlSpec("mcp.servers", "plugin-registry", "existing_capability", "cyrene_mcp", "R2", "immediate"),
        )),
    )
    context.on_startup(service.startup)
    context.on_shutdown(service.shutdown)
    context.expose_frontend("mcp")

    @context.router.get("/api/settings/mcp")
    async def api_get_mcp_servers() -> dict[str, Any]:
        return {
            "servers": service.status(),
            "configs": service.configs(redacted=True),
        }

    @context.router.put("/api/settings/mcp")
    async def api_update_mcp_servers(request: Request):
        payload = await request.json()
        servers = payload.get("servers", []) if isinstance(payload, dict) else []
        try:
            status = await service.replace_configs(
                servers,
                merge_redacted=True,
            )
        except ValueError as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "invalid_mcp_configuration",
                    "error": localized(
                        "MCP server configuration is invalid.",
                        "MCP 服务器配置无效。",
                    ),
                },
                status_code=400,
            )
        return {
            "ok": True,
            "servers": status,
            "configs": service.configs(redacted=True),
        }

    from .application import setup_plugin_center

    setup_plugin_center(context)


# Each configured server is registered dynamically as its own ``mcp.<server>``
# pack.  This static application pack only contributes lifecycle and services,
# so it deliberately has no directly executable Plugins.
plugin_pack = PluginPack(
    id="cyrene_mcp",
    description="Connect configured MCP servers and expose each server as a Plugin pack.",
    metadata={
        "i18n": {
            "en": {
                "name": "MCP integrations",
                "description": "Connect configured MCP servers and expose their tools.",
            },
            "zh": {
                "name": "MCP 集成",
                "description": "连接已配置的 MCP 服务器并提供其中的工具。",
            },
        }
    },
    plugins=(),
    setup=setup,
    application_setup=application_setup,
)


__all__ = ["application_setup", "plugin_pack", "setup"]
