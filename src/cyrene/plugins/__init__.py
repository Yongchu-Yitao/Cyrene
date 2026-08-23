"""Trusted project plugin host.

Plugins are intentionally separate from Custom Tools and the Extension Center.
Cyrene owns package discovery, per-project enablement, process lifetime and the
UI/RPC bridge; the plugin owns everything behind those seams.
"""

from cyrene.plugins.manager import PluginManager, get_plugin_manager

__all__ = ["PluginManager", "get_plugin_manager"]
