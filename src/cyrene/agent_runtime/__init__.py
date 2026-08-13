"""Cyrene unified Agent Runtime domain.

Backend foundation for external Agent bindings and capabilities (phase 1).
Routes and UI depend on this domain layer instead of branching on external
product names.  The built-in Cyrene Agent remains fully backward compatible:
whenever agent fields are absent, normalization falls back to the built-in
Agent descriptor/binding.
"""

from __future__ import annotations

from cyrene.agent_runtime.builtin import (
    BUILTIN_AGENT_CAPABILITIES,
    BUILTIN_AGENT_ID,
    BUILTIN_AGENT_VERSION,
    BUILTIN_DISPLAY_NAME,
    BUILTIN_DRIVER,
    BUILTIN_INSTALLATION_ID,
    BUILTIN_PROTOCOL_VERSION,
    BuiltinAgentDriver,
    builtin_binding,
    builtin_descriptor,
    builtin_driver,
    chat_agent_fields,
    default_model_access,
    normalize_agent_binding,
    normalize_agent_fields,
    normalize_model_access,
)
from cyrene.agent_runtime.capabilities import (
    CAPABILITY_STATES,
    KNOWN_CAPABILITY_GROUPS,
    CapabilityState,
    capability_available,
    capability_state,
    is_capability_available,
    is_capability_supported,
    merge_capabilities,
    normalize_capabilities,
    normalize_capability_state,
    with_conservative_defaults,
)
from cyrene.agent_runtime.driver import (
    AgentConnection,
    AgentDriver,
    AgentStartRequest,
    DriverInfo,
    DriverRegistry,
    default_registry,
    driver_names,
    get_driver,
    register_driver,
)
from cyrene.agent_runtime.errors import (
    FAILURE_KINDS,
    FailureKind,
    AgentRuntimeError,
    failure_kind,
    is_failure_kind,
)
from cyrene.agent_runtime.events import (
    CORE_EVENT_TYPES,
    event_envelope,
    normalize_builtin_event,
    sanitize_event_payload,
)
from cyrene.agent_runtime.models import (
    AgentAuthState,
    AgentBinding,
    AgentDescriptor,
    AgentState,
    ModelAccess,
    ModelAccessMode,
)
from cyrene.agent_runtime.acp_events import (
    AcpEventMapper,
    is_terminal_run_event,
    redact_secrets,
)
from cyrene.agent_runtime.acp_protocol import (
    ACP_METHOD_INITIALIZE,
    ACP_METHOD_SESSION_CANCEL,
    ACP_METHOD_SESSION_LOAD,
    ACP_METHOD_SESSION_NEW,
    ACP_METHOD_SESSION_PROMPT,
    ACP_METHOD_SESSION_SET_CONFIG_OPTION,
    ACP_NOTIFICATIONS,
    ACP_PROTOCOL_VERSION,
    JsonRpcError,
)
from cyrene.agent_runtime.acp_transport import (
    AcpStdioTransport,
    AcpTransportError,
    build_safe_env,
)
from cyrene.agent_runtime.process_manager import (
    ACP_STDIO_DRIVER,
    AcpProcessManager,
    get_process_manager,
)
from cyrene.agent_runtime.runtime_service import (
    AcpConnection,
    AcpRuntimeService,
    AcpStdioDriver,
    EnvModelBinder,
    ModelBinding,
    default_model_binder,
    discover_external_agent_config_options,
    get_acp_runtime_service,
    run_external_agent_turn,
    respond_to_external_agent_request,
)

register_driver(
    BUILTIN_DRIVER,
    builtin_driver,
    protocol_version=BUILTIN_PROTOCOL_VERSION,
    description="Built-in Cyrene Agent (descriptor/probe only; legacy run path in phase 1)",
)

register_driver(
    ACP_STDIO_DRIVER,
    AcpStdioDriver,
    protocol_version=ACP_PROTOCOL_VERSION,
    description="ACP stdio JSON-RPC subprocess driver for external agents "
    "(OpenCode ACP, Codex ACP, Pi ACP)",
)

__all__ = [
    "ACP_METHOD_INITIALIZE",
    "ACP_METHOD_SESSION_CANCEL",
    "ACP_METHOD_SESSION_LOAD",
    "ACP_METHOD_SESSION_NEW",
    "ACP_METHOD_SESSION_PROMPT",
    "ACP_METHOD_SESSION_SET_CONFIG_OPTION",
    "ACP_NOTIFICATIONS",
    "ACP_PROTOCOL_VERSION",
    "ACP_STDIO_DRIVER",
    "AcpConnection",
    "AcpEventMapper",
    "AcpProcessManager",
    "AcpRuntimeService",
    "AcpStdioDriver",
    "AcpStdioTransport",
    "AcpTransportError",
    "AgentAuthState",
    "AgentBinding",
    "AgentConnection",
    "AgentDescriptor",
    "AgentDriver",
    "AgentRuntimeError",
    "AgentStartRequest",
    "AgentState",
    "BUILTIN_AGENT_CAPABILITIES",
    "BUILTIN_AGENT_ID",
    "BUILTIN_AGENT_VERSION",
    "BUILTIN_DISPLAY_NAME",
    "BUILTIN_DRIVER",
    "BUILTIN_INSTALLATION_ID",
    "BUILTIN_PROTOCOL_VERSION",
    "BuiltinAgentDriver",
    "CAPABILITY_STATES",
    "CORE_EVENT_TYPES",
    "CapabilityState",
    "DriverInfo",
    "DriverRegistry",
    "EnvModelBinder",
    "FAILURE_KINDS",
    "FailureKind",
    "JsonRpcError",
    "KNOWN_CAPABILITY_GROUPS",
    "ModelAccess",
    "ModelAccessMode",
    "ModelBinding",
    "builtin_binding",
    "builtin_descriptor",
    "builtin_driver",
    "build_safe_env",
    "capability_available",
    "capability_state",
    "chat_agent_fields",
    "default_model_access",
    "default_registry",
    "default_model_binder",
    "discover_external_agent_config_options",
    "driver_names",
    "event_envelope",
    "failure_kind",
    "get_driver",
    "get_acp_runtime_service",
    "get_process_manager",
    "is_capability_available",
    "is_capability_supported",
    "is_failure_kind",
    "is_terminal_run_event",
    "merge_capabilities",
    "normalize_agent_binding",
    "normalize_agent_fields",
    "normalize_builtin_event",
    "normalize_capabilities",
    "normalize_capability_state",
    "normalize_model_access",
    "redact_secrets",
    "register_driver",
    "run_external_agent_turn",
    "respond_to_external_agent_request",
    "sanitize_event_payload",
    "with_conservative_defaults",
]
