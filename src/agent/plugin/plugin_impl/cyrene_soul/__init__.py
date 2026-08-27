"""SOUL.md context-mounting Plugin pack."""

from agent.plugin import PluginPack

from .service import setup_soul


plugin_pack = PluginPack(
    id="cyrene_soul",
    description="Mount the enabled SOUL.md persona directly below the system prompt.",
    plugins=(),
    setup=setup_soul,
    metadata={
        "i18n": {
            "en": {
                "name": "Soul",
                "description": "Mount the enabled SOUL.md persona directly below the system prompt.",
            },
            "zh": {
                "name": "灵魂",
                "description": "启用后，将 SOUL.md 人格挂载到系统提示词正下方。",
            },
        }
    },
)

__all__ = ["plugin_pack"]
