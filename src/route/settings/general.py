"""Composition root for settings and onboarding HTTP adapters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agent.plugin import active_plugin_application_host
from cyrene.config import WORKSPACE_DIR
from cyrene.runtime.data_reset import DataResetApplicationService
from cyrene.runtime.config_integration_service import (
    ConfigIntegrationApplicationService,
)
from cyrene.runtime.onboarding_context_service import (
    OnboardingContextApplicationService,
    ProjectResolver,
)
from cyrene.runtime.profile_data_service import ProfileDataApplicationService
from cyrene.runtime.search_settings_service import SearchSettingsApplicationService
from cyrene.workbench.context import read_project_state
from cyrene.workbench.presentation_service import PresentationQueryService
from route.settings.config_integrations import register_config_integration_routes
from route.settings.oauth import register_oauth_routes
from route.settings.onboarding_context import register_onboarding_context_routes
from route.settings.profile_data import register_profile_data_routes
from route.settings.search import register_search_settings_routes
from route.settings.plugin_service import PluginSettingsApplicationService
from route.settings.plugins import register_plugin_settings_routes


async def _publish_settings_changed(
    namespace: str,
    revision: int | None,
    changed: list[str],
) -> None:
    from cyrene.observability import debug

    proxy_runtime_keys = {
        "external_agent_proxy_enabled",
        "external_agent_proxy_url",
        "external_agent_proxy_port",
        "proxy_search_enabled",
    }
    if {
        "external_agent_proxy_enabled",
        "external_agent_proxy_url",
        "external_agent_proxy_port",
    }.intersection(changed):
        # ACP transports are process-scoped and retain their spawn env. Recycle
        # them so the next turn applies the new setting without a full restart.
        from cyrene.agent_runtime.process_manager import get_process_manager

        await get_process_manager().close_all()
    if proxy_runtime_keys.intersection(changed):
        # SimpleXNG owns its outgoing environment and generated settings for
        # the lifetime of the child process. Restart an already-running local
        # instance so the saved search scope takes effect immediately.
        from agent.plugin import active_plugin_service
        from cyrene.config import SEARXNG_HOST, SEARXNG_PORT

        service = active_plugin_service("web_search")
        manager = getattr(service, "manager", None)
        restart = getattr(service, "restart", None)
        if manager is not None and manager.is_running and callable(restart):
            await restart(int(SEARXNG_PORT), str(SEARXNG_HOST))
    await debug.publish_event({
        "type": "settings_changed",
        "namespace": namespace,
        "revision": revision,
        "changed": list(changed),
    })


def register_settings_routes(
    router: APIRouter,
    bot: Any,
    db_path: str,
    *,
    data_reset_service: DataResetApplicationService | None = None,
    queries: PresentationQueryService | None = None,
) -> None:
    """Compose the active settings adapters."""
    del bot
    reset_service = data_reset_service or DataResetApplicationService(db_path)
    presentation_queries = queries or PresentationQueryService()
    plugin_host = active_plugin_application_host()
    if plugin_host is None:
        raise RuntimeError("Plugin application host is required by the settings interface")

    register_onboarding_context_routes(
        router,
        OnboardingContextApplicationService(
            ProjectResolver(read_project_state, WORKSPACE_DIR),
        ),
    )
    register_oauth_routes(router)
    register_plugin_settings_routes(
        router,
        PluginSettingsApplicationService(
            plugin_host.registry,
            _publish_settings_changed,
        ),
    )
    register_config_integration_routes(
        router,
        ConfigIntegrationApplicationService(
            presentation_queries,
            _publish_settings_changed,
        ),
    )
    register_profile_data_routes(
        router,
        ProfileDataApplicationService(
            db_path,
            presentation_queries,
            reset_service,
            _publish_settings_changed,
        ),
    )
    register_search_settings_routes(
        router,
        SearchSettingsApplicationService(_publish_settings_changed),
    )
