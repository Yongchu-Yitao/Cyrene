"""Editable Cyrene extensions Plugin pack."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import Plugin, PluginPack

from . import list_environment, manage_extensions, search_environment


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    metadata: dict[str, Any] = dict(getattr(module, "TOOL_METADATA", {}))
    if str(function["name"]) == "ManageExtensions":
        metadata["main_only"] = True
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
    id="cyrene_extensions",
    description="Inspect and manage external integrations and runtime environments.",
    plugins=tuple(_plugin(module) for module in (
        list_environment,
        search_environment,
        manage_extensions,
    )),
    application_setup=application_setup,
    metadata={
        "i18n": {
            "en": {
                "name": "Extensions",
                "description": "Inspect and manage external integrations and runtime environments.",
            },
            "zh": {
                "name": "扩展",
                "description": "查看并管理外部集成和运行环境。",
            },
        },
    },
)

__all__ = ["application_setup", "plugin_pack"]
