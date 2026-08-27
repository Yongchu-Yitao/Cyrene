"""Process-level attachment for the editable knowledge Plugin pack."""

from __future__ import annotations

from agent.plugin import PluginApplicationContext

from .service import create_knowledge_service


def setup_application(context: PluginApplicationContext) -> None:
    from .routes import register_routes
    from .settings_routes import register_settings_routes

    service = create_knowledge_service(
        context.data_directory / "plugin_data" / "cyrene_knowledge",
        legacy_store_directory=context.data_directory.parent / "store",
        initialize_store=False,
    )
    register_routes(context.router, service)

    register_settings_routes(context.router, service)
    context.provide("knowledge", service)
    context.provide_search("knowledge", service.search_workbench)
    context.expose_frontend("knowledge")
    from cyrene.runtime.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
    )

    context.provide(
        "knowledge_settings",
        PluginSettingsContribution(controls=(
            SettingControlSpec("integrations.zotero", "integrations", "current_ui", "cyrene.ui.inspect", "R2"),
            SettingControlSpec("integrations.zotero_test", "integrations", "current_ui", "cyrene.ui.inspect", "R2"),
            SettingControlSpec("integrations.zotero_import", "integrations", "current_ui", "cyrene.ui.inspect", "R2"),
        )),
    )
    context.on_startup(service.startup)
    context.on_shutdown(service.shutdown)


__all__ = ["setup_application"]
