"""Host-independent Plugin definitions, registry, and execution runtime."""

from .activation import PluginActivationSnapshot, PluginActivationState
from .batch_catcher import PluginBatchCatcher
from .batch_runner import PluginBatchRunner
from .core_impl import (
    PERMISSION_PLUGIN_ID,
    PermissionDecision,
    PermissionReviewPlugin,
    TOOLBOX_PLUGIN_NAME,
)
from .customization import PluginCustomizationState
from .context import (
    current_plugin_context,
    json_result,
    plugin_language,
    plugin_localized,
    plugin_localized_plural,
    plugin_service,
    publish_runtime_event,
    run_context_data,
    run_context_value,
)
from .extensions import (
    APPLICATION_SETUP,
    RUN_SERVICE,
    SESSION_SETUP,
    ExtensionContribution,
    ExtensionPoint,
    ExtensionRegistry,
    PluginScope,
)
from .model import RuntimeModelGateway
from .plugin import (
    Plugin,
    PluginApplicationSetupHandler,
    PluginCall,
    PluginCallResult,
    PluginContext,
    PluginHandler,
    PluginPack,
    PermissionBoundaryProvider,
    PluginSetupContext,
    PluginSetupHandler,
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
from .resource_effects import (
    PluginResourceEffect,
    RESOURCE_REVEAL_ARGUMENT,
    RESOURCE_REVEAL_DESCRIPTION,
    ResourceAccess,
    ResourceEffectPhase,
    ResourceKind,
    normalize_resource_effects,
    resource_effect_input_schema,
    resolve_resource_effect_values,
    split_resource_reveal,
    workspace_resource_locations,
)
from .scopes import (
    ApplicationPluginScope,
    application_plugin_scope,
    application_plugin_service,
    set_application_plugin_scope,
)
from .session_state import (
    PLUGIN_SESSION_STATE_KEY,
    plugin_child_context_ids,
    plugin_public_session_snapshot,
    plugin_session_state,
    with_plugin_session_state,
    without_plugin_session_state,
)
from .validation import (
    PluginArgumentNormalization,
    PluginArgumentRepair,
    PluginInputValidationError,
    PluginSchemaError,
    normalize_plugin_arguments,
    validate_plugin_arguments,
)

__all__ = [name for name in globals() if not name.startswith("_")]
