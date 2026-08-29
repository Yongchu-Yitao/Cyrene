"""Plugin-owned per-turn context for the visible Workbench split."""

from cyrene.core.plugin import PluginPack

from .context import setup_split_context


plugin_pack = PluginPack(
    id="cyrene_split_context",
    description=(
        "Mount a bounded description of the current conversation's visible "
        "Workbench split when each user turn starts."
    ),
    plugins=(),
    setup=setup_split_context,
    metadata={
        "default_enabled": True,
        "i18n": {
            "en": {
                "name": "Visible split context",
                "description": (
                    "Tell the Agent which panes are currently visible beside "
                    "the active conversation."
                ),
            },
            "zh": {
                "name": "当前分屏上下文",
                "description": "每轮自动告诉 Agent 当前对话旁边可见的分屏。",
            },
        },
    },
)


__all__ = ["plugin_pack", "setup_split_context"]
