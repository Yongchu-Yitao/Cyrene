"""HTTP and asset bridge for trusted project plugins."""

from __future__ import annotations

from fastapi import APIRouter

from cyrene.plugins.manager import PluginManager
from route.plugin_routes.management import register_plugin_management_routes
from route.plugin_routes.runtime import register_plugin_runtime_routes
from route.plugin_routes.transport import register_plugin_transport_routes


def register_plugin_routes(router: APIRouter, manager: PluginManager) -> None:
    register_plugin_management_routes(router, manager)
    register_plugin_runtime_routes(router, manager)
    register_plugin_transport_routes(router, manager)


__all__ = ["register_plugin_routes"]
