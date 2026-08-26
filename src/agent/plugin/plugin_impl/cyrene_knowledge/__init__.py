"""Editable Cyrene knowledge Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_knowledge",
    description="Search and manage project knowledge and library documents.",
    native_module_names=(
        "list_knowledge_documents", "search_knowledge",
        "list_library_items", "search_library", "update_library_metadata",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 5:
    raise RuntimeError("knowledge pack must contain exactly 5 Plugins")

__all__ = ["plugin_pack"]
