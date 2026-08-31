"""Stable contracts shared by the Remote Desktop Plugin runtime."""

from __future__ import annotations

import platform as platform_module
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol


REMOTE_DESKTOP_PROTOCOL_VERSION = 1

DESKTOP_CAPABILITIES = frozenset(
    {
        "desktop:session_connect",
        "desktop:current_session",
        "desktop:remote_login",
        "desktop:screen_view_user",
        "desktop:screen_view_agent",
        "desktop:input_user",
        "desktop:input_agent",
        "desktop:display_list",
        "desktop:display_select_user",
        "desktop:display_select_agent",
        "desktop:audio_output_user",
        "desktop:audio_input_user",
        "desktop:audio_agent",
        "desktop:clipboard_text_user",
        "desktop:clipboard_image_user",
        "desktop:clipboard_file_user",
        "desktop:clipboard_agent",
    }
)

# Agent-input/audio/clipboard capabilities are protocol reservations only.  They
# are deliberately absent from this default and are not consumed by V1 code.
DEFAULT_DESKTOP_CAPABILITIES = (
    "desktop:session_connect",
    "desktop:current_session",
    "desktop:remote_login",
    "desktop:screen_view_user",
    "desktop:screen_view_agent",
    "desktop:input_user",
    "desktop:display_list",
    "desktop:display_select_user",
    "desktop:audio_output_user",
    "desktop:audio_input_user",
    "desktop:clipboard_text_user",
    "desktop:clipboard_image_user",
    "desktop:clipboard_file_user",
)

QualityMode = Literal["auto", "smooth", "balanced", "clear"]
DesktopMode = Literal["current_desktop", "remote_login"]
SessionState = Literal[
    "idle",
    "probing",
    "needs_component",
    "needs_permission",
    "ready",
    "waiting_credentials",
    "gathering_ice",
    "connecting_direct",
    "connecting_turn",
    "authenticating_rdp",
    "starting_native_session",
    "connected",
    "reconnecting",
    "failed",
    "disconnected",
    "reconnect_required",
]


@dataclass(frozen=True, slots=True)
class DisplayDescriptor:
    id: str
    name: str
    width: int
    height: int
    scale: float = 1.0
    rotation: int = 0
    primary: bool = False
    kind: str = "physical"

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    id: str
    version: str
    status: Literal["supported", "degraded", "unsupported"]
    platform: str = field(default_factory=platform_module.system)
    architecture: str = field(default_factory=platform_module.machine)
    modes: tuple[DesktopMode, ...] = ()
    displays: tuple[DisplayDescriptor, ...] = ()
    capabilities: tuple[str, ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()
    display_server: str = ""
    audio_backend: str = ""
    secure_surface: bool = False

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["displays"] = [item.public() for item in self.displays]
        return value


@dataclass(frozen=True, slots=True)
class SnapshotRegion:
    x: int
    y: int
    width: int
    height: int

    def public(self) -> dict[str, int]:
        return asdict(self)


class RemoteDesktopProvider(Protocol):
    id: str

    async def probe(self) -> ProviderDescriptor: ...

    async def negotiate(
        self,
        session_id: str,
        *,
        mode: DesktopMode,
        offer: dict[str, Any],
        display_id: str,
        quality_mode: QualityMode,
        ice_servers: list[dict[str, Any]],
        credentials: dict[str, str] | None = None,
        permissions: dict[str, bool] | None = None,
    ) -> dict[str, Any]: ...

    async def disconnect(self, session_id: str) -> None: ...

    async def list_displays(self, session_id: str) -> list[DisplayDescriptor]: ...

    async def select_display(self, session_id: str, display_id: str) -> None: ...

    async def set_quality(self, session_id: str, mode: QualityMode) -> None: ...

    async def set_microphone(self, session_id: str, enabled: bool) -> None: ...

    async def security_state(self, session_id: str) -> dict[str, Any]: ...

    async def apply_clipboard_image(self, session_id: str, path: str) -> None: ...

    async def export_clipboard_image(
        self, session_id: str, offer_id: str, path: str
    ) -> dict[str, Any]: ...

    async def acknowledge_clipboard_image(
        self, session_id: str, offer_id: str
    ) -> None: ...

    async def apply_clipboard_files(
        self, session_id: str, paths: list[str]
    ) -> None: ...

    async def export_clipboard_files(
        self, session_id: str, offer_id: str, path: str
    ) -> dict[str, Any]: ...

    async def acknowledge_clipboard_files(
        self, session_id: str, offer_id: str
    ) -> None: ...


QUALITY_MODES: tuple[QualityMode, ...] = (
    "auto",
    "smooth",
    "balanced",
    "clear",
)


__all__ = [
    "DEFAULT_DESKTOP_CAPABILITIES",
    "DESKTOP_CAPABILITIES",
    "DesktopMode",
    "DisplayDescriptor",
    "ProviderDescriptor",
    "QUALITY_MODES",
    "QualityMode",
    "REMOTE_DESKTOP_PROTOCOL_VERSION",
    "RemoteDesktopProvider",
    "SessionState",
    "SnapshotRegion",
]
