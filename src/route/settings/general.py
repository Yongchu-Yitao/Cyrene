"""Composition root for settings and onboarding HTTP adapters."""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter

from agent.plugin import active_plugin_application_host
from cyrene.runtime.data_reset import DataResetApplicationService
from cyrene.runtime.config_integration_service import (
    ConfigIntegrationApplicationService,
)
from cyrene.runtime.onboarding_context_service import OnboardingContextApplicationService
from cyrene.runtime.profile_data_service import ProfileDataApplicationService
from cyrene.workbench.presentation_service import PresentationQueryService
from route.settings.config_integrations import (
    register_config_read_routes,
    register_namespace_routes,
)
from route.settings.onboarding_context import register_onboarding_context_routes
from route.settings.profile_data import register_profile_data_routes
from route.settings.plugin_service import PluginSettingsApplicationService
from route.settings.plugins import register_plugin_settings_routes


async def _publish_settings_changed(
    namespace: str,
    revision: int | None,
    changed: list[str],
) -> None:
    from cyrene.observability import debug

    # Application Plugins observe generic setting changes through their
    # service ports. Core does not know which pack owns a changed capability.
    host = active_plugin_application_host()
    seen: set[int] = set()
    for service in host.active_services.values() if host is not None else ():
        if id(service) in seen:
            continue
        seen.add(id(service))
        callback = getattr(service, "settings_changed", None)
        if not callable(callback):
            continue
        result = callback(namespace, tuple(changed))
        if inspect.isawaitable(result):
            await result
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
        OnboardingContextApplicationService(),
    )
    register_plugin_settings_routes(
        router,
        PluginSettingsApplicationService(
            plugin_host.registry,
            _publish_settings_changed,
        ),
    )
    config_service = ConfigIntegrationApplicationService(
        presentation_queries,
        _publish_settings_changed,
    )
    register_config_read_routes(router, config_service)
    register_namespace_routes(router, config_service)
    register_profile_data_routes(
        router,
        ProfileDataApplicationService(
            db_path,
            presentation_queries,
            reset_service,
            _publish_settings_changed,
        ),
    )
