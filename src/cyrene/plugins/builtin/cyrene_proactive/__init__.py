"""Personal assistant follow-up preference."""

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import PluginPack
from .revisit import default_daily_frequency, draw_tool


def application_setup(context: PluginApplicationContext) -> None:
    from cyrene.platform.settings_service import (
        PluginSettingsContribution,
        plugin_setting_spec,
    )

    context.provide(
        "proactive_settings",
        PluginSettingsContribution(specs=(
            plugin_setting_spec("agent_proactive", "boolean", True, tab="agents", apply_mode="next_run"),
            plugin_setting_spec("agent_proactive_daily_frequency", "number", default_daily_frequency(),
                                tab="agents", minimum=0.09, maximum=64, apply_mode="next_run"),
        )),
    )
    context.expose_frontend("proactive")


plugin_pack = PluginPack(
    id="cyrene_proactive",
    description="Personal assistant proactive preference.",
    plugins=(draw_tool,),
    application_setup=application_setup,
    metadata={
        "i18n": {
            "en": {
                "name": "Assistant follow-up",
                "description": "Manage the personal assistant proactive preference.",
            },
            "zh": {
                "name": "助理主动跟进",
                "description": "管理个人助理的主动跟进偏好。",
            },
        }
    },
)


__all__ = ["application_setup", "plugin_pack"]
