"""Process-level attachment for the editable knowledge Plugin pack."""

from __future__ import annotations

from agent.plugin import PluginApplicationContext

from .service import create_knowledge_service


def setup_application(context: PluginApplicationContext) -> None:
    from .routes import register_routes

    service = create_knowledge_service(
        context.data_directory / "plugin_data" / "cyrene_knowledge",
        legacy_store_directory=context.data_directory.parent / "store",
    )
    register_routes(context.router, service)
    context.provide("knowledge", service)
    context.provide_search("knowledge", service.search_workbench)
    context.expose_frontend("knowledge")
    context.on_startup(service.startup)
    context.on_shutdown(service.shutdown)


__all__ = ["setup_application"]
