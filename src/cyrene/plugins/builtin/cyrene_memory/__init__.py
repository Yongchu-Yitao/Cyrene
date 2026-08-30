"""Editable memory Plugin pack with Agent and application integrations."""

from typing import Any

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import (
    Plugin,
    PluginContext,
    PluginPack,
    merge_plugin_pack_metadata,
)
from cyrene.platform import config_store

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


# Memory owns its maintenance policy. Keep model-backed stewardship at a
# one-hour minimum even when an older installation stored a smaller value.
STEWARD_INTERVAL = max(
    3600,
    int(config_store.get_env("STEWARD_INTERVAL", "3600") or "3600"),
)


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)


async def _steward_tick(
    _arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    """Run one due memory-steward pass through the active application service."""

    service = context.services.get("memory")
    if service is None:
        raise RuntimeError("PluginContext.services['memory'] is unavailable")
    from cyrene.plugins.background import maintenance_lock

    async with maintenance_lock():
        ran = await service.run_steward_if_needed(interval=STEWARD_INTERVAL)
    return {"ok": True, "ran": bool(ran)}


async def _short_term_cleanup_tick(
    _arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    """Remove expired short-term memories through the active service."""

    service = context.services.get("memory")
    if service is None:
        raise RuntimeError("PluginContext.services['memory'] is unavailable")
    from cyrene.plugins.background import maintenance_lock

    async with maintenance_lock():
        service.clear_old_short_term(days=7)
    return {"ok": True}


_EMPTY_INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

memory_steward_job = Plugin(
    name="memory.steward.tick",
    description="Run one due memory-steward pass. Hidden from models.",
    input_schema=_EMPTY_INPUT_SCHEMA,
    handler=_steward_tick,
    timeout_seconds=600.0,
    metadata={
        "model_visible": False,
        "i18n": {
            "en": {
                "name": "Memory steward",
                "description": "Consolidate recent conversations into durable memory.",
            },
            "zh": {
                "name": "记忆管家",
                "description": "将近期对话整理为持久记忆。",
            },
        },
        "background_job": {
            "id": "steward",
            "interval_seconds": max(1, int(STEWARD_INTERVAL)),
            "coalesce": True,
            "max_instances": 1,
            "run_on_start": False,
        },
    },
)

short_term_cleanup_job = Plugin(
    name="memory.short_term.cleanup",
    description="Remove expired short-term memories. Hidden from models.",
    input_schema=_EMPTY_INPUT_SCHEMA,
    handler=_short_term_cleanup_tick,
    metadata={
        "model_visible": False,
        "i18n": {
            "en": {
                "name": "Short-term memory cleanup",
                "description": "Remove short-term memories older than seven days.",
            },
            "zh": {
                "name": "短期记忆清理",
                "description": "清理超过七天的短期记忆。",
            },
        },
        "background_job": {
            "id": "short_term_cleanup",
            "interval_seconds": 86400,
            "coalesce": True,
            "max_instances": 1,
            "run_on_start": False,
        },
    },
)

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
        memory_steward_job,
        short_term_cleanup_job,
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
if len(plugin_pack.plugins) != 11:
    raise RuntimeError("memory pack must contain exactly 11 Plugins")

__all__ = ["application_setup", "plugin_pack"]
