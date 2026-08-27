"""Editable Cyrene image Plugin pack."""

from agent.plugin import PluginPack

from .generate_image import plugin as generate_image_plugin

plugin_pack = PluginPack(
    id="cyrene_image",
    description="Generate image assets and attach their results.",
    plugins=(generate_image_plugin,),
)

__all__ = ["plugin_pack"]
