"""Strict HTTP schemas for the Remote Desktop Plugin."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebRtcDescription(StrictModel):
    type: Literal["offer", "answer"]
    sdp: str = Field(min_length=1, max_length=2_000_000)


class SessionCreateRequest(StrictModel):
    device_id: str = Field(min_length=1, max_length=200)
    mode: Literal["current_desktop", "remote_login"] = "current_desktop"
    offer: WebRtcDescription
    display_id: str = Field(default="", max_length=200)
    quality_mode: Literal["auto", "smooth", "balanced", "clear"] = "auto"
    pane_card_id: str = Field(default="", max_length=240)
    pane_layout_id: str = Field(default="", max_length=240)
    credential_handle: str = Field(default="", max_length=160)


class SessionReconnectRequest(StrictModel):
    offer: WebRtcDescription


class DisplaySelectRequest(StrictModel):
    display_id: str = Field(min_length=1, max_length=200)


class QualityRequest(StrictModel):
    quality_mode: Literal["auto", "smooth", "balanced", "clear"]


class MicrophoneRequest(StrictModel):
    enabled: bool


class LayoutCard(StrictModel):
    card_id: str = Field(max_length=240)
    kind: Literal["chat", "plugin-view", "file", "terminal", "other"]
    chat_id: str = Field(default="", max_length=200)
    pack_id: str = Field(default="", max_length=120)
    view_id: str = Field(default="", max_length=120)
    instance_id: str = Field(default="", max_length=200)
    device_id: str = Field(default="", max_length=200)
    session_id: str = Field(default="", max_length=100)
    meta: dict[str, Any] = Field(default_factory=dict)


class LayoutProjectionRequest(StrictModel):
    pane_layout_id: str = Field(min_length=1, max_length=240)
    projection_scope_id: str = Field(default="", max_length=240)
    revision: int = Field(ge=1)
    origin: Literal[
        "user_pointer", "user_keyboard", "restored_user_layout",
        "agent_ui_action", "system_restore",
    ] = "user_pointer"
    cards: list[LayoutCard] = Field(max_length=4)


__all__ = [
    "DisplaySelectRequest",
    "LayoutProjectionRequest",
    "MicrophoneRequest",
    "QualityRequest",
    "SessionCreateRequest",
    "SessionReconnectRequest",
]
