"""Globally enabled time snapshots at model boundaries."""
from cyrene.core.plugin import PluginPack
from .context import setup

plugin_pack = PluginPack(
    id="cyrene_time_context",
    description="Refresh the current time in the user's configured timezone every five minutes.",
    plugins=(),
    setup=setup,
    metadata={
        "default_enabled": True,
        "i18n": {
            "en": {"name": "Current time", "description": "Refresh time context every five minutes at model boundaries."},
            "zh": {"name": "当前时间", "description": "在模型调用前按五分钟间隔更新用户时区的时间上下文。"},
        },
    },
)
