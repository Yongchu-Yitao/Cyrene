"""Editable model implementations."""

from agent.plugin import PluginPack

from .minimax import MINIMAX_PLUGIN

plugin_pack = PluginPack(
    id="model",
    description="Model calling components.",
    plugins=(MINIMAX_PLUGIN,),
)

__all__ = ["plugin_pack"]
