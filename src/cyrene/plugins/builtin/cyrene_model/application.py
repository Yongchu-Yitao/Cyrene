"""Application contribution owned by the editable model Provider pack."""

from __future__ import annotations

from pathlib import Path

from cyrene.plugins.context import PluginApplicationContext
from cyrene.plugins.model_catalog import application_model_runtime
from .configuration import (
    candidate_for_profile,
    candidates_for_route,
    connection_with_secret,
    get_model_configuration,
    normalize_model_configuration,
    public_model_configuration,
    save_model_configuration,
    selectable_model_candidates,
    validate_active_route_provider_families,
)
from .probe import ModelProbeService
from .routes import ModelConfigurationApplicationService, register_model_configuration_routes
from .oauth import register_oauth_routes


class ModelConfigurationService(ModelConfigurationApplicationService):
    """Process-level model configuration port exposed by the model pack.

    Core code consumes this object through ``application_plugin_service``; all
    provider-specific configuration and persistence remains in this pack.
    """

    get_model_configuration = staticmethod(get_model_configuration)
    normalize_model_configuration = staticmethod(normalize_model_configuration)
    public_model_configuration = staticmethod(public_model_configuration)
    save_model_configuration = staticmethod(save_model_configuration)
    candidate_for_profile = staticmethod(candidate_for_profile)
    candidates_for_route = staticmethod(candidates_for_route)
    selectable_model_candidates = staticmethod(selectable_model_candidates)
    connection_with_secret = staticmethod(connection_with_secret)
    validate_active_route_provider_families = staticmethod(
        validate_active_route_provider_families
    )

    @staticmethod
    def storage_paths() -> dict[str, tuple[Path, ...]]:
        """Claim OAuth/Codex CLI cache roots for storage accounting."""

        from cyrene.config import CACHE_DIR

        return {"codex_cli": (CACHE_DIR / "codex_cli",)}

    @staticmethod
    def oauth_provider():
        """Return the managed Codex OAuth adapter for model-owned callers."""

        from cyrene.model_runtime.codex_provider import get_codex_provider

        return get_codex_provider()

    @staticmethod
    def oauth_base_url() -> str:
        return "codex://oauth"

    @staticmethod
    async def prepare_data_reset() -> dict[str, bool]:
        """Close the OAuth provider before the host clears disposable roots."""

        try:
            from cyrene.model_runtime.codex_provider import get_codex_provider

            await get_codex_provider().close()
        except (ImportError, RuntimeError, OSError):
            return {"codex_cli": False}
        return {"codex_cli": True}


def setup_application(context: PluginApplicationContext) -> None:
    if context.registry is None:
        raise RuntimeError("cyrene_model requires the active Plugin registry")

    gateway = context.services.get("model")
    runtime = getattr(gateway, "runtime", None) or application_model_runtime(
        context.registry
    )
    settings = ModelConfigurationService(context.registry, runtime)
    probe = ModelProbeService(context.registry, runtime)
    register_model_configuration_routes(context.router, settings)
    register_oauth_routes(context.router)
    context.provide("model_configuration", settings)
    context.provide("model_probe", probe)
    from cyrene.runtime.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
        plugin_setting_spec,
    )

    context.provide(
        "model_settings_schema",
        PluginSettingsContribution(
            specs=(plugin_setting_spec("codex_budget_enabled", "boolean", True, tab="budget"),),
            controls=(
                SettingControlSpec("models.connections", "models", "current_ui", "cyrene.ui.inspect", "R2"),
                SettingControlSpec("models.credentials", "models", "current_ui", "cyrene.ui.type", "R3", secret=True),
                SettingControlSpec("models.profiles", "models", "current_ui", "cyrene.ui.inspect", "R2"),
                SettingControlSpec("models.routes", "models", "current_ui", "cyrene.ui.inspect", "R2", "next_run"),
                SettingControlSpec("models.oauth", "models", "user_ceremony", "cyrene.oauth", "R3"),
            ),
        ),
    )

    async def close_oauth_provider() -> None:
        await settings.oauth_provider().close()

    context.on_shutdown(close_oauth_provider)
    context.expose_frontend("model")


__all__ = ["ModelConfigurationService", "setup_application"]
