"""Editable voice application Plugin pack."""

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import PluginPack


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)


plugin_pack = PluginPack(
    id="cyrene_voice",
    description="Provide speech recognition, synthesis, and voice settings.",
    plugins=(),
    application_setup=application_setup,
    metadata={
        "i18n": {
            "en": {
                "name": "Voice",
                "description": "Provide speech recognition, synthesis, and voice settings.",
            },
            "zh": {
                "name": "语音",
                "description": "提供语音识别、语音合成与声音设置。",
            },
        }
    },
)


__all__ = ["application_setup", "plugin_pack"]
