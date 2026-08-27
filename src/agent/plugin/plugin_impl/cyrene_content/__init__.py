"""Editable Cyrene content-access Plugin pack."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from agent.plugin import (
    Plugin,
    PluginApplicationContext,
    PluginPack,
    PluginSetupContext,
    active_plugin_service,
)

from . import analyze_attachment, read_tool_result, web_fetch, web_search
from .search_service import get_search_service
from .tool_result_store import get_tool_result_store


def setup(context: PluginSetupContext) -> None:
    if context.services.get("web_search") is None:
        context.provide(
            "web_search",
            active_plugin_service("web_search") or get_search_service(),
            replace=True,
        )
    if context.services.get("tool_results") is None:
        context.provide(
            "tool_results",
            active_plugin_service("tool_results") or get_tool_result_store(),
            replace=True,
        )


def application_setup(context: PluginApplicationContext) -> None:
    search_service = get_search_service()
    context.provide("web_search", search_service)
    context.provide("tool_results", get_tool_result_store())
    context.on_startup(search_service.startup)
    context.on_shutdown(search_service.shutdown)


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    metadata: dict[str, Any] = dict(getattr(module, "TOOL_METADATA", {}))
    return Plugin(
        name=str(function["name"]),
        description=str(function.get("description") or ""),
        input_schema=dict(function.get("parameters") or {"type": "object", "properties": {}}),
        handler=module.handler,
        allow_parallel=bool(metadata.get("allow_parallel", not metadata.get("requires_order", True))),
        timeout_seconds=float(metadata.get("timeout_seconds", 180.0)),
        metadata=metadata,
    )


plugin_pack = PluginPack(
    id="cyrene_content",
    description="Attachment, paged-result, and web content access.",
    plugins=tuple(_plugin(module) for module in (
        read_tool_result,
        analyze_attachment,
        web_fetch,
        web_search,
    )),
    setup=setup,
    application_setup=application_setup,
)

__all__ = ["application_setup", "plugin_pack", "setup"]
