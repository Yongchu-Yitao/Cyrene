"""Editable Cyrene content-access Plugin pack."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import (
    Plugin,
    PluginPack,
    PluginSetupContext,
)

from . import analyze_attachment, read_tool_result, web_fetch, web_search
from .search_service import get_search_service
from .tool_result_store import get_tool_result_store


def setup(context: PluginSetupContext) -> None:
    missing = [
        name
        for name in ("content", "web_search", "tool_results")
        if context.services.get(name) is None
    ]
    if missing:
        raise RuntimeError(
            "cyrene_content application services are unavailable: "
            + ", ".join(missing)
        )


def application_setup(context: PluginApplicationContext) -> None:
    from cyrene.core.plugin import application_plugin_scope
    from cyrene.workbench.artifacts.presentation_service import PresentationQueryService

    from .pdf_routes import register_pdf_routes
    from .routes import register_search_routes
    from .search_settings import install_search_settings

    search_service = get_search_service()
    context.provide("content", search_service)
    context.provide("web_search", search_service)
    context.provide("tool_results", get_tool_result_store())
    context.on_startup(search_service.startup)
    context.on_shutdown(search_service.shutdown)
    register_pdf_routes(context.router)
    register_search_routes(
        context.router,
        PresentationQueryService(
            db_path=context.db_path,
            plugin_host=application_plugin_scope(),
        ),
    )
    context.expose_frontend("content")
    if install_search_settings(context, canonical_name=web_search.TOOL_NAME):
        from cyrene.runtime.settings_service import (
            PluginSettingsContribution,
            SettingControlSpec,
            plugin_setting_spec,
        )

        context.provide(
            "search_settings_schema",
            PluginSettingsContribution(
                specs=(
                    plugin_setting_spec(
                        "proxy_search_enabled", "boolean", False,
                        tab="general", apply_mode="next_run",
                    ),
                ),
                controls=(
                    SettingControlSpec(
                        "search.providers", "search", "current_ui",
                        "cyrene.ui.inspect", "R3", secret=True,
                    ),
                ),
            ),
        )
        context.expose_frontend("search")


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
