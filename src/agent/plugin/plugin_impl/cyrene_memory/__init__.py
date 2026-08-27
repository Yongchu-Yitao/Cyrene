"""Editable memory Plugin pack with Agent and application integrations."""

from agent.plugin import (
    PluginApplicationContext,
    PluginPack,
    merge_plugin_pack_metadata,
)

from .list_memories import plugin as list_memories_plugin
from .read_group_sessions import plugin as read_group_sessions_plugin
from .recall_conversation import plugin as recall_conversation_plugin
from .recall_memory import plugin as recall_memory_plugin
from .retire_project_memory import plugin as retire_project_memory_plugin
from .retire_short_term_memory import plugin as retire_short_term_memory_plugin
from .save_project_memory import plugin as save_project_memory_plugin
from .search_project_memory import plugin as search_project_memory_plugin
from .service import setup_memory
from .trigger_project_memory_learning import (
    plugin as trigger_project_memory_learning_plugin,
)


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)

plugin_pack = PluginPack(
    id="cyrene_memory",
    description="Inject, learn, recall, save, search, and retire memories.",
    plugins=(
        list_memories_plugin,
        recall_memory_plugin,
        recall_conversation_plugin,
        read_group_sessions_plugin,
        retire_short_term_memory_plugin,
        search_project_memory_plugin,
        save_project_memory_plugin,
        retire_project_memory_plugin,
        trigger_project_memory_learning_plugin,
    ),
    setup=setup_memory,
    application_setup=application_setup,
)
plugin_pack = merge_plugin_pack_metadata(
    plugin_pack,
    {
        name: {"main_only": True}
        for name in (
            "ReadChatGroupSessions",
            "retire_short_term_memory",
            "save_project_memory",
            "retire_project_memory",
            "trigger_project_memory_learning",
        )
    },
)
if len(plugin_pack.plugins) != 9:
    raise RuntimeError("memory pack must contain exactly 9 Plugins")

__all__ = ["application_setup", "plugin_pack"]
