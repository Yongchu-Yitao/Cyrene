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
)

__all__ = ["application_setup", "plugin_pack"]
