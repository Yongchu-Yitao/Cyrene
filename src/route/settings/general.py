"""Composition root for settings and onboarding HTTP adapters."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from cyrene.config import SOUL_PATH, WORKSPACE_DIR
from cyrene.runtime.data_reset import DataResetApplicationService
from cyrene.runtime.config_integration_service import (
    ConfigIntegrationApplicationService,
)
from cyrene.runtime.model_probe_service import ModelProbeService
from cyrene.runtime.onboarding_context_service import (
    OnboardingContextApplicationService,
    ProjectResolver,
    SoulRepository,
)
from cyrene.runtime.profile_data_service import ProfileDataApplicationService
from cyrene.runtime.search_settings_service import SearchSettingsApplicationService
from cyrene.workbench.context import read_project_state
from cyrene.workbench.presentation_service import PresentationQueryService
from route.settings.config_integrations import register_config_integration_routes
from route.settings.model_service import ModelSettingsApplicationService
from route.settings.models import register_model_routes
from route.settings.oauth import register_oauth_routes
from route.settings.onboarding_context import register_onboarding_context_routes
from route.settings.profile_data import register_profile_data_routes
from route.settings.search import register_search_settings_routes
from route.settings.tool_service import ToolSettingsApplicationService
from route.settings.tools import register_tool_routes


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
        from cyrene.config import SEARXNG_HOST, SEARXNG_PORT
        from cyrene.tooling.backends.searxng_manager import get_manager

        manager = get_manager()
        if manager.is_running:
            await asyncio.to_thread(manager.stop)
            await asyncio.to_thread(manager.start, int(SEARXNG_PORT), SEARXNG_HOST)
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
    """Compose settings adapters in their historical registration order."""
    del bot
    reset_service = data_reset_service or DataResetApplicationService(db_path)
    presentation_queries = queries or PresentationQueryService()

    register_onboarding_context_routes(
        router,
        OnboardingContextApplicationService(
            SoulRepository(SOUL_PATH),
            ProjectResolver(read_project_state, WORKSPACE_DIR),
        ),
    )
    register_oauth_routes(router)
    register_model_routes(router, ModelSettingsApplicationService(ModelProbeService()))
    register_tool_routes(router, ToolSettingsApplicationService(_publish_settings_changed))
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
