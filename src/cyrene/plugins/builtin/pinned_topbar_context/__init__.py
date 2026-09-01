"""Plugin-owned per-turn context for globally pinned Workbench resources."""

from cyrene.core.plugin import PluginPack

from .context import setup_context


plugin_pack = PluginPack(
    id="pinned_topbar_context",
    description=(
        "Mount the latest globally pinned Workbench resources when each user "
        "message starts a turn."
    ),
    plugins=(),
    setup=setup_context,
    metadata={
        "default_enabled": True,
        "i18n": {
            "en": {
                "name": "Pinned topbar context",
                "description": (
                    "Mount the latest globally pinned Workbench resources "
                    "with every new user turn."
                ),
            },
            "zh": {
                "name": "顶部固定资源上下文",
                "description": "每次用户发送新消息时挂载最新的全局固定资源。",
            },
        },
    },
)


__all__ = ["plugin_pack", "setup_context"]
