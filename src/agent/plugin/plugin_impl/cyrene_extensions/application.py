"""Plugin Center application contribution owned by the environment pack."""

from __future__ import annotations

from agent.plugin import PluginApplicationContext
from agent.plugin.plugin_impl.cyrene_extensions.extension_plugin_center import register_plugin_center_extension_routes
from agent.plugin.plugin_impl.cyrene_extensions.extension_service import get_extension_service

from .agent_model_gateway_routes import register_agent_model_gateway_routes
from .agent_routes import register_agent_routes


def setup_plugin_center(context: PluginApplicationContext) -> None:
    service = get_extension_service()
    context.provide("extensions", service)
    register_plugin_center_extension_routes(
        context.router,
        owner_pack="cyrene_extensions",
        service=service,
    )


def setup_application(context: PluginApplicationContext) -> None:
    setup_plugin_center(context)
    register_agent_routes(context.router, context.bot, context.db_path)
    register_agent_model_gateway_routes(context.router)
    context.expose_frontend("extensions")
    context.expose_frontend("agents")
    from cyrene.runtime.settings_service import (
        PluginSettingsContribution,
        plugin_setting_spec,
    )

    context.provide(
        "extension_settings",
        PluginSettingsContribution(specs=(
            plugin_setting_spec(
                "external_agent_proxy_enabled",
                "boolean",
                False,
                tab="general",
                apply_mode="next_run",
            ),
            plugin_setting_spec(
                "external_agent_proxy_url",
                "string",
                "",
                tab="general",
                apply_mode="next_run",
            ),
            plugin_setting_spec(
                "external_agent_proxy_port",
                "integer",
                7897,
                tab="general",
                minimum=1,
                maximum=65535,
                apply_mode="next_run",
            ),
            plugin_setting_spec(
                "proxy_extensions_enabled",
                "boolean",
                False,
                tab="general",
                apply_mode="next_run",
            ),
        )),
    )


__all__ = ["setup_application", "setup_plugin_center"]
