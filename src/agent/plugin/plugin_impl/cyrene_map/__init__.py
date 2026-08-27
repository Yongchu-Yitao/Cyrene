"""Editable Cyrene map Plugin pack."""

from agent.plugin import PluginPack, PluginSetupContext

from .application import setup_application
from .service import MapService, map_database
from .tools import connect_pins_plugin, pin_location_plugin


def setup_session(context: PluginSetupContext) -> None:
    if "maps" not in context.services:
        context.provide("maps", MapService(map_database(context.data_directory)))


plugin_pack = PluginPack(
    id="cyrene_map",
    description="Search places and calculate map routes.",
    plugins=(pin_location_plugin, connect_pins_plugin),
    setup=setup_session,
    application_setup=setup_application,
)

__all__ = ["plugin_pack"]
