"""Core Agent Runtime data models.

Fields are snake_case internally with camelCase public aliases matching the
Workbench API convention (handoff §6/§10/§14).  Use ``to_public_dict()`` for
chat snapshots and API responses.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasGenerator, BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

AgentState = Literal[
    "ready",
    "not_started",
    "starting",
    "stopped",
    "error",
    "disabled",
    "unknown",
]

AgentAuthState = Literal[
    "not_configured",
    "authenticating",
    "connected",
    "failed",
    "expired",
    "unknown",
]

ModelAccessMode = Literal["cyrene_managed", "agent_managed"]


class _AgentRuntimeModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(alias=to_camel),
        populate_by_name=True,
        extra="ignore",
    )

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class ModelAccess(_AgentRuntimeModel):
    """Model source snapshot bound to a conversation (§10).

    ``cyrene_managed`` routes through the Cyrene Model Gateway; ``agent_managed``
    means the Agent uses its own provider/account configuration.  No credentials
    are ever stored here.
    """

    mode: ModelAccessMode = "cyrene_managed"
    profile_id: str = ""
    protocol: str = ""
    model: str = ""

    @field_validator("mode", mode="before")
    @classmethod
    def _coerce_mode(cls, value: Any) -> str:
        if isinstance(value, str) and value.strip().lower() in {"cyrene_managed", "agent_managed"}:
            return value.strip().lower()
        return "cyrene_managed"


class AgentBinding(_AgentRuntimeModel):
    """Persisted agent identity snapshot for one chat (§14).

    The chat stores the creation-time identity so history stays readable after
    an Agent is upgraded or uninstalled.  ``installation_id`` (never a bare
    product name) is the binding key; ``external_session_id`` is the Agent-side
    session id and does not replace the Cyrene chat id.
    """

    installation_id: str
    agent_id: str = ""
    display_name: str = ""
    version: str = ""
    driver: str = ""
    protocol_version: int = 1
    external_session_id: str = ""
    binding_locked: bool = False

    @property
    def is_builtin(self) -> bool:
        return self.installation_id == "agent_cyrene_builtin"


class AgentDescriptor(_AgentRuntimeModel):
    """Normalized Agent description served to the frontend (§6.1).

    The frontend never reads the raw Manifest; it consumes this normalized
    descriptor produced by inspect/probe/Profile/Manifest in priority order.
    """

    installation_id: str
    agent_id: str = ""
    display_name: str = ""
    version: str = ""
    driver: str = ""
    protocol_version: int = 1
    state: AgentState = "unknown"
    auth_state: AgentAuthState = "unknown"
    default_model_access: ModelAccessMode = "cyrene_managed"
    capabilities: dict[str, Any] = Field(default_factory=dict)

    @field_validator("state", "auth_state", mode="before")
    @classmethod
    def _coerce_state(cls, value: Any) -> str:
        raw = str(value or "").strip().lower()
        allowed = ("ready", "not_started", "starting", "stopped", "error", "disabled",
                   "unknown", "not_configured", "authenticating", "connected", "failed", "expired")
        return raw if raw in allowed else "unknown"

    @field_validator("default_model_access", mode="before")
    @classmethod
    def _coerce_default_model_access(cls, value: Any) -> str:
        if isinstance(value, str) and value.strip().lower() in {"cyrene_managed", "agent_managed"}:
            return value.strip().lower()
        return "cyrene_managed"

    @field_validator("protocol_version", mode="before")
    @classmethod
    def _coerce_protocol_version(cls, value: Any) -> int:
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0
