"""Editable Cyrene renderer Plugin pack."""

from cyrene.core.plugin import PluginPack

from .load_contract import plugin as load_contract_plugin

plugin_pack = PluginPack(
    id="cyrene_renderer",
    description="Load Workbench renderer contracts.",
    plugins=(load_contract_plugin,),
)

__all__ = ["plugin_pack"]
