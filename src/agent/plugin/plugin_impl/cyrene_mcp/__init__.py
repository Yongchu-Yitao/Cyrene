"""Editable bridge that projects configured MCP servers as Plugin packs."""

from __future__ import annotations

from agent.plugin import PluginApplicationContext, PluginPack, PluginSetupContext
from agent.plugin.mcp_service import get_mcp_service


def _registry(services):
    model = services.get("model")
    registry = getattr(model, "registry", None)
    if registry is None:
        raise RuntimeError("cyrene_mcp requires the native Plugin model service")
    return registry


def setup(context: PluginSetupContext) -> None:
    service = get_mcp_service()
    service.attach_registry(_registry(context.services))
    if context.services.get("mcp") is None:
        context.provide("mcp", service)


def application_setup(context: PluginApplicationContext) -> None:
    service = get_mcp_service()
    service.attach_registry(_registry(context.services))
    context.provide("mcp", service)
    context.on_startup(service.startup)
    context.on_shutdown(service.shutdown)


# Each configured server is registered dynamically as its own ``mcp.<server>``
# pack.  This static application pack only contributes lifecycle and services,
# so it deliberately has no directly executable Plugins.
plugin_pack = PluginPack(
    id="cyrene_mcp",
    description="Connect configured MCP servers and expose each server as a Plugin pack.",
    plugins=(),
    setup=setup,
    application_setup=application_setup,
)


__all__ = ["application_setup", "plugin_pack", "setup"]
