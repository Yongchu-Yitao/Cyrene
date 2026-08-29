"""Editable, required Plugin pack for composer-attached context."""

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import PluginPack

from .context_mount import setup_composer_context
from .mount_plugin import plugin as mount_plugin


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)


plugin_pack = PluginPack(
    id="cyrene_composer_context",
    description=(
        "Validate and mount context explicitly selected in the message composer."
    ),
    plugins=(mount_plugin,),
    setup=setup_composer_context,
    application_setup=application_setup,
    metadata={
        "required": True,
        "i18n": {
            "en": {
                "name": "Composer context",
                "description": (
                    "Validate and mount workspaces, Soul, Skills, MCP servers, "
                    "Plugin packs, and remote devices selected in the composer."
                ),
            },
            "zh": {
                "name": "输入框上下文",
                "description": (
                    "统一校验并挂载输入框中选择的工作区、灵魂、技能、MCP "
                    "服务器、插件包和远程设备。"
                ),
            },
        },
    },
)


__all__ = [
    "application_setup",
    "mount_plugin",
    "plugin_pack",
    "setup_composer_context",
]
