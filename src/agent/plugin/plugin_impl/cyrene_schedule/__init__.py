"""Editable scheduled-task Plugin pack."""

from agent.plugin import PluginApplicationContext, PluginPack

from .tools import plugins


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)

plugin_pack = PluginPack(
    id="cyrene_schedule",
    description="Create, manage, execute, and inspect durable scheduled tasks.",
    plugins=plugins,
    application_setup=application_setup,
    metadata={
        "i18n": {
            "en": {
                "name": "Schedules",
                "description": "Create, manage, execute, and inspect durable scheduled tasks.",
            },
            "zh": {
                "name": "定时任务",
                "description": "创建、管理、执行并检查持久化定时任务。",
            },
        }
    },
)

__all__ = ["application_setup", "plugin_pack"]
