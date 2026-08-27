"""Built-in Cyrene Agent descriptor and external-driver binding normalization.

The built-in entry describes the native Plugin/ContextTree Agent.  External
ACP Agents use drivers; the native Agent deliberately has no subprocess driver.
"""

from __future__ import annotations

from typing import Any

from cyrene.agent_runtime.capabilities import normalize_capabilities
from cyrene.agent_runtime.errors import AgentRuntimeError
from cyrene.agent_runtime.models import AgentBinding, AgentDescriptor, ModelAccess
from cyrene.localization import localized

BUILTIN_AGENT_ID = "cyrene"
BUILTIN_INSTALLATION_ID = "agent_cyrene_builtin"
BUILTIN_DISPLAY_NAME = "Cyrene"
BUILTIN_DRIVER = "cyrene_builtin"
BUILTIN_PROTOCOL_VERSION = 1
BUILTIN_AGENT_VERSION = "1.0.0"

# The built-in agent keeps the full Workbench surface it already offers today;
# external agents start from their probed/declared capabilities instead.
BUILTIN_AGENT_CAPABILITIES: dict[str, Any] = {
    "session": {"load": "supported", "fork": "supported", "close": "supported"},
    "input": {
        "text": "supported",
        "image": "supported",
        "file": "supported",
        "audio": "supported",
    },
    "output": {
        "streaming": "supported",
        "reasoning": "supported",
        "toolLifecycle": "supported",
        "artifacts": "supported",
        "diff": "supported",
    },
    "interaction": {
        "permission": "supported",
        "elicitation": "supported",
        "steer": "supported",
        "cancel": "supported",
    },
    "model": {
        "agentManaged": "unsupported",
        "cyreneManaged": [],
        "switchDuringSession": "supported",
        "reasoningEffort": "supported",
    },
}


def builtin_binding() -> AgentBinding:
    return AgentBinding(
        installation_id=BUILTIN_INSTALLATION_ID,
        agent_id=BUILTIN_AGENT_ID,
        display_name=BUILTIN_DISPLAY_NAME,
        version=BUILTIN_AGENT_VERSION,
        driver=BUILTIN_DRIVER,
        protocol_version=BUILTIN_PROTOCOL_VERSION,
    )


def builtin_descriptor() -> AgentDescriptor:
    return AgentDescriptor(
        installation_id=BUILTIN_INSTALLATION_ID,
        agent_id=BUILTIN_AGENT_ID,
        display_name=BUILTIN_DISPLAY_NAME,
        version=BUILTIN_AGENT_VERSION,
        driver=BUILTIN_DRIVER,
        protocol_version=BUILTIN_PROTOCOL_VERSION,
        state="ready",
        auth_state="connected",
        default_model_access="cyrene_managed",
        capabilities=normalize_capabilities(BUILTIN_AGENT_CAPABILITIES),
    )


def default_model_access(*, model: str = "") -> ModelAccess:
    return ModelAccess(mode="cyrene_managed", model=str(model or "").strip())


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            return value
    return None


def normalize_agent_binding(raw: dict[str, Any] | None) -> AgentBinding:
    """Normalize a raw chat/request binding; absent/legacy → built-in Agent."""
    if not isinstance(raw, dict):
        return builtin_binding()
    installation_id = str(_first(raw, "installationId", "installation_id") or "").strip()
    if not installation_id or installation_id == BUILTIN_INSTALLATION_ID:
        return builtin_binding()
    agent_id = str(_first(raw, "agentId", "agent_id") or "").strip() or installation_id
    return AgentBinding(
        installation_id=installation_id,
        agent_id=agent_id,
        display_name=str(_first(raw, "displayName", "display_name") or "").strip() or agent_id,
        version=str(_first(raw, "version") or "").strip(),
        driver=str(_first(raw, "driver") or "").strip(),
        protocol_version=_as_int(_first(raw, "protocolVersion", "protocol_version")),
        external_session_id=str(_first(raw, "externalSessionId", "external_session_id") or "").strip(),
        binding_locked=_as_bool(_first(raw, "bindingLocked", "binding_locked")),
    )


def normalize_model_access(
    raw: dict[str, Any] | None,
    *,
    default_model: str = "",
) -> ModelAccess:
    """Normalize a raw modelAccess snapshot; absent/legacy → cyrene-managed."""
    if not isinstance(raw, dict):
        return default_model_access(model=default_model)
    return ModelAccess(
        mode=raw.get("mode") or "cyrene_managed",
        profile_id=str(_first(raw, "profileId", "profile_id") or "").strip(),
        protocol=str(_first(raw, "protocol") or "").strip(),
        model=str(_first(raw, "model") or default_model or "").strip(),
    )


def normalize_agent_fields(
    agent_raw: dict[str, Any] | None,
    model_access_raw: dict[str, Any] | None,
    *,
    default_model: str = "",
    capabilities_raw: dict[str, Any] | None = None,
    capabilities_revision: int | None = None,
) -> dict[str, Any]:
    """Return the camelCase chat-storage block (handoff §14).

    ``agent`` / ``modelAccess`` / ``capabilities`` / ``capabilitiesRevision``
    are normalized together so a chat always has a coherent snapshot.  External
    agents may start with ``capabilities: {}`` until a probe fills them.
    """
    binding = normalize_agent_binding(agent_raw)
    model_access = normalize_model_access(model_access_raw, default_model=default_model)
    if binding.is_builtin:
        capabilities = normalize_capabilities(BUILTIN_AGENT_CAPABILITIES)
    else:
        capabilities = normalize_capabilities(capabilities_raw)
    if (
        not isinstance(capabilities_revision, int)
        or isinstance(capabilities_revision, bool)
        or capabilities_revision < 0
    ):
        capabilities_revision = 1
    return {
        "agent": binding.to_public_dict(),
        "modelAccess": model_access.to_public_dict(),
        "capabilities": capabilities,
        "capabilitiesRevision": capabilities_revision,
    }


def chat_agent_fields(chat: dict[str, Any]) -> dict[str, Any]:
    """Normalize the agent block of one stored chat for public snapshots.

    Read-only normalization: legacy chats without agent fields surface the
    built-in Cyrene Agent without rewriting the store.
    """
    if not isinstance(chat, dict):
        chat = {}
    stored_agent = chat.get("agent") if isinstance(chat.get("agent"), dict) else None
    stored_access = chat.get("modelAccess") if isinstance(chat.get("modelAccess"), dict) else None
    stored_caps = chat.get("capabilities") if isinstance(chat.get("capabilities"), dict) else None
    stored_revision = chat.get("capabilitiesRevision")
    return normalize_agent_fields(
        stored_agent,
        stored_access,
        default_model=str(chat.get("model") or ""),
        capabilities_raw=stored_caps,
        capabilities_revision=(
            stored_revision
            if isinstance(stored_revision, int)
            and not isinstance(stored_revision, bool)
            and stored_revision >= 0
            else None
        ),
    )


class BuiltinAgentDriver:
    """Descriptor/probe-only entry for the in-process Plugin Agent."""

    async def inspect(self, installation: dict[str, Any] | None = None) -> AgentDescriptor:
        return builtin_descriptor()

    async def connect(self, request: Any) -> Any:
        raise AgentRuntimeError(
            kind="capability_missing",
            message=localized(
                "the built-in Cyrene Agent runs in-process through the Plugin runtime; no external driver connection applies",
                "内置 Cyrene 智能体通过插件运行时在进程内运行，不适用外部驱动连接",
            ),
        )


def builtin_driver() -> BuiltinAgentDriver:
    """Driver factory registered under ``BUILTIN_DRIVER`` in the registry."""
    return BuiltinAgentDriver()
