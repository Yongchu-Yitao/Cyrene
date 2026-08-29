"""Editable Cyrene map Plugin pack."""

from cyrene.core.plugin import PluginPack, PluginSetupContext

from .application import setup_application
from .service import MapService, map_database
from .tools import connect_pins_plugin, pin_location_plugin


def setup_session(context: PluginSetupContext) -> None:
    service = context.services.get("maps")
    if service is None:
        service = MapService(map_database(context.data_directory))
        context.provide("maps", service)
    initializer = getattr(service, "initialize", None)
    if callable(initializer):
        initializer()


plugin_pack = PluginPack(
    id="cyrene_map",
    description="Search places and calculate map routes.",
    plugins=(pin_location_plugin, connect_pins_plugin),
    setup=setup_session,
    application_setup=setup_application,
)

__all__ = ["plugin_pack"]
