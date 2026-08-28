"""Editable Cyrene messaging-channel application pack."""

from __future__ import annotations

from agent.plugin import PluginApplicationContext, PluginPack

from .runtime_plugin import plugin as runtime_plugin


def run_telegram() -> None:
    """CLI launcher kept inside the editable channel pack."""

    from .telegram import run_telegram as launch

    launch()


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)


plugin_pack = PluginPack(
    id="cyrene_channels",
    description="Configure and run Telegram and WeChat messaging channels.",
    plugins=(runtime_plugin,),
    application_setup=application_setup,
    metadata={
        "runtime_launchers": {"telegram": run_telegram},
        "i18n": {
            "en": {
                "name": "Messaging channels",
                "description": "Configure and run Telegram and WeChat messaging channels.",
            },
            "zh": {
                "name": "消息渠道",
                "description": "配置并运行 Telegram 与微信消息渠道。",
            },
        },
    },
)


__all__ = ["application_setup", "plugin_pack", "run_telegram", "runtime_plugin"]
