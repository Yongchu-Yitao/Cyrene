"""Composable Plugin execution for Cyrene's state-based agent kernel."""

from .batch_catcher import PluginBatchCatcher
from .batch_runner import PluginBatchRunner
from .activation import PluginActivationSnapshot, PluginActivationState
from .customization import PluginCustomizationState
from .application import (
    PluginApplicationHost,
    active_plugin_application_host,
    active_plugin_service,
    set_active_plugin_application_host,
)
from .core_impl import (
    PERMISSION_PLUGIN_ID,
    PermissionDecision,
    PermissionReviewPlugin,
    TOOLBOX_PLUGIN_NAME,
)
from .model_gateway import PluginModelGateway, ensure_model_router
from .plugin import (
    Plugin,
    PluginApplicationContext,
    PluginApplicationSetupHandler,
    PluginCall,
    PluginCallResult,
    PluginContext,
    PluginFrontendHandler,
    PluginHandler,
    PluginLifecycleHandler,
    PluginPack,
    PluginSetupContext,
    PluginSetupHandler,
    PluginSearchHandler,
    merge_plugin_pack_metadata,
)
from .registry import (
    PluginLoadFailure,
    PluginNotFoundError,
    PluginRegistry,
    PluginRegistryError,
    PluginUnavailableError,
    RegisteredPlugin,
    default_plugin_impl_directory,
)
from .runtime import PluginRuntime, PreparedPluginCall
from .session_state import (
    PLUGIN_SESSION_STATE_KEY,
    plugin_child_context_ids,
    plugin_public_session_snapshot,
    plugin_session_state,
    with_plugin_session_state,
    without_plugin_session_state,
)
from .validation import PluginInputValidationError, PluginSchemaError

__all__ = [
    "PERMISSION_PLUGIN_ID",
    "PermissionDecision",
    "PermissionReviewPlugin",
    "PluginActivationSnapshot",
    "PluginActivationState",
    "Plugin",
    "PluginApplicationContext",
    "PluginApplicationHost",
    "PluginApplicationSetupHandler",
    "PluginBatchCatcher",
    "PluginBatchRunner",
    "PluginCall",
    "PluginCallResult",
    "PluginCustomizationState",
    "PluginContext",
    "PluginFrontendHandler",
    "PluginHandler",
    "PluginLifecycleHandler",
    "PluginInputValidationError",
    "PluginLoadFailure",
    "PluginModelGateway",
    "PluginNotFoundError",
    "PluginPack",
    "PluginSetupContext",
    "PluginSetupHandler",
    "PluginSearchHandler",
    "PluginRegistry",
    "PluginRegistryError",
    "PluginUnavailableError",
    "PluginRuntime",
    "PluginSchemaError",
    "PLUGIN_SESSION_STATE_KEY",
    "PreparedPluginCall",
    "RegisteredPlugin",
    "TOOLBOX_PLUGIN_NAME",
    "active_plugin_application_host",
    "active_plugin_service",
    "default_plugin_impl_directory",
    "ensure_model_router",
    "set_active_plugin_application_host",
    "merge_plugin_pack_metadata",
    "plugin_child_context_ids",
    "plugin_public_session_snapshot",
    "plugin_session_state",
    "with_plugin_session_state",
    "without_plugin_session_state",
]
