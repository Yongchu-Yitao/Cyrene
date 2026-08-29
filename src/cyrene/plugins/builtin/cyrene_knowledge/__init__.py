"""Editable knowledge Plugin pack with Agent and application integrations."""

from __future__ import annotations

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import (
    PluginPack,
    PluginSetupContext,
)

from .list_knowledge_documents import plugin as list_knowledge_documents
from .list_library_items import plugin as list_library_items
from .search_knowledge import plugin as search_knowledge
from .search_library import plugin as search_library
from .update_library_metadata import plugin as update_library_metadata


def setup(context: PluginSetupContext) -> None:
    from .service import create_knowledge_service

    if context.services.get("knowledge") is None:
        context.provide(
            "knowledge",
            create_knowledge_service(
                context.data_directory / "plugin_data" / "cyrene_knowledge",
                legacy_store_directory=context.data_directory.parent / "store",
            ),
        )


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)


plugin_pack = PluginPack(
    id="cyrene_knowledge",
    description="Search, manage, index, and present project knowledge and library documents.",
    plugins=(
        list_knowledge_documents,
        search_knowledge,
        list_library_items,
        search_library,
        update_library_metadata,
    ),
    setup=setup,
    application_setup=application_setup,
)

__all__ = ["application_setup", "plugin_pack", "setup"]
