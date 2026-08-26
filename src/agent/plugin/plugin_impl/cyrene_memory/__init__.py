"""Editable Cyrene memory Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_memory",
    description="Recall, save, search, and retire memories.",
    native_module_names=(
        "list_memories", "recall_memory", "recall_conversation",
        "read_group_sessions", "retire_short_term_memory",
        "search_project_memory", "save_project_memory",
        "retire_project_memory", "trigger_project_memory_learning",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 9:
    raise RuntimeError("memory pack must contain exactly 9 Plugins")

__all__ = ["plugin_pack"]
