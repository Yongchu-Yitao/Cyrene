"""Plugin Center routes owned by the native MCP application pack."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from agent.plugin import PluginApplicationContext
from agent.plugin.plugin_impl.cyrene_extensions.extension_plugin_center import register_plugin_center_routes
from agent.plugin.plugin_impl.cyrene_extensions.extension_service import application_extension_service
from cyrene.localization import localized

from .service import MCPPluginService, MCPServerNotFoundError


async def _json_configuration(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise ValueError(localized(
            "MCP server configuration must be an object.",
            "MCP 服务器配置必须是对象。",
        ))
    return dict(payload)


def _register_configuration_route(
    context: PluginApplicationContext,
    service: MCPPluginService,
) -> None:
    @context.router.put(
        "/api/plugin-center/mcp/{extension_id:path}/configuration"
    )
    async def api_update_mcp_configuration(
        extension_id: str,
        request: Request,
    ):
        try:
            configuration = await _json_configuration(request)
            result = await service.update_configuration(
                extension_id,
                configuration,
            )
        except MCPServerNotFoundError as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "mcp_server_not_found",
                    "error": localized("MCP server was not found.", "未找到 MCP 服务器。"),
                },
                status_code=404,
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
        return {"ok": True, **result}


def setup_plugin_center(context: PluginApplicationContext) -> None:
    service = application_extension_service(context)
    if service is None:
        return
    register_plugin_center_routes(
        context.router,
        kind="mcp",
        owner_pack="cyrene_mcp",
        service=service,
    )
    mcp_service = context.services.get("mcp")
    if mcp_service is not None:
        _register_configuration_route(context, mcp_service)


__all__ = ["setup_plugin_center"]
