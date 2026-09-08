"""Required per-turn user-language context Plugin pack."""

from cyrene.core.plugin import PluginPack

from .context import setup_user_language


plugin_pack = PluginPack(
    id="cyrene_user_language",
    description="Mount the user's current language preference for Agent replies.",
    plugins=(),
    setup=setup_user_language,
    metadata={
        "required": True,
        "i18n": {
            "en": {
                "name": "User language",
                "description": "Mount the user's current language preference for Agent replies.",
            },
            "zh": {
                "name": "用户语言",
                "description": "每轮自动挂载用户的语言设置，让 Agent 使用相应语言回复。",
            },
        },
    },
)


__all__ = ["plugin_pack"]
