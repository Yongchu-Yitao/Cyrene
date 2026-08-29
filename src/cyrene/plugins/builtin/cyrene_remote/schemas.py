"""Strict request/response contracts owned by the Remote Plugin."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RemoteSettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RemoteSettingsUpdate(RemoteSettingsModel):
    enabled: bool
    relay_url: str = Field(default="", max_length=500)
    device_name: str = Field(min_length=1, max_length=120)


class RemotePairingInvitationRequest(RemoteSettingsModel):
    capabilities: list[str] = Field(default_factory=list, max_length=50)
    project_scopes: list[str] = Field(default_factory=list, max_length=500)
    ttl_seconds: int = Field(default=120, ge=30, le=600)


class RemotePairingAcceptRequest(RemoteSettingsModel):
    invitation: str = Field(min_length=1, max_length=20_000)


class RemotePairingCompleteRequest(RemoteSettingsModel):
    response: str = Field(min_length=1, max_length=20_000)


class RemoteShortPairingConnectRequest(RemoteSettingsModel):
    address: str = Field(min_length=1, max_length=200)
    pairing_key: str = Field(min_length=1, max_length=32)


class RemotePeerGrantUpdate(RemoteSettingsModel):
    capabilities: list[str] = Field(default_factory=list, max_length=50)
    project_scopes: list[str] = Field(default_factory=list, max_length=500)


class RemoteAuditResponse(RemoteSettingsModel):
    events: list[dict[str, Any]]
