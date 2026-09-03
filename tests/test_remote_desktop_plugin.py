from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from cyrene.plugins.contributions import validate_workbench_contributions
from cyrene.plugins.builtin.cyrene_remote.services import (
    RemoteControlApplicationService,
)
from cyrene.plugins.builtin.cyrene_remote_desktop import plugin_pack
from cyrene.plugins.builtin.cyrene_remote_desktop.contracts import (
    DEFAULT_DESKTOP_CAPABILITIES,
    REMOTE_DESKTOP_PROTOCOL_VERSION,
    DisplayDescriptor,
)
from cyrene.plugins.builtin.cyrene_remote_desktop.providers import (
    FreeRdpProvider,
    _configured_rdp_port,
    _freerdp_connection_arguments,
    _normalize_rdp_connect_error,
    _parse_rdp_port,
    _rdp_listener_state,
)
from cyrene.plugins.builtin.cyrene_remote_desktop.service import (
    CredentialBroker,
    RemoteDesktopError,
    RemoteDesktopService,
)


def run(awaitable):
    return asyncio.run(awaitable)


class _PeerStore:
    def __init__(
        self,
        *,
        received: tuple[str, ...] = (),
        granted: tuple[str, ...] = (),
    ) -> None:
        self.peer = {
            "device_id": "device-one",
            "display_name": "Desk One",
            "received_capabilities": list(received),
            "granted_capabilities": list(granted),
            "revoked_at": "",
        }

    def get_peer(self, device_id: str) -> dict[str, Any] | None:
        return dict(self.peer) if device_id == "device-one" else None

    def list_peers(self) -> list[dict[str, Any]]:
        return [dict(self.peer)]


class _RemoteService:
    def __init__(self, store: _PeerStore, peer_transport: Any = None) -> None:
        self.store = store
        self.peer_transport = peer_transport


def _service(
    tmp_path: Path,
    *,
    received: tuple[str, ...] = (),
    granted: tuple[str, ...] = (),
    peer_transport: Any = None,
) -> RemoteDesktopService:
    remote = _RemoteService(
        _PeerStore(received=received, granted=granted),
        peer_transport=peer_transport,
    )
    return RemoteDesktopService(
        str(tmp_path / "remote-desktop.sqlite3"),
        tmp_path / "data",
        remote_service=remote,
    )


def _connected_session(service: RemoteDesktopService, *, secure: bool = False) -> str:
    session_id = "rds_" + "1" * 32
    service.store.create_session(
        {
            "session_id": session_id,
            "device_id": "device-one",
            "device_name": "Desk One",
            "mode": "current_desktop",
            "state": "connected",
            "remote_session_id": "rdh_" + "2" * 32,
            "secure_surface": secure,
        }
    )
    return session_id


def _offer_payload(*, mode: str = "current_desktop") -> dict[str, Any]:
    return {
        "protocol_version": REMOTE_DESKTOP_PROTOCOL_VERSION,
        "mode": mode,
        "offer": {"type": "offer", "sdp": "v=0\r\n"},
        "quality_mode": "auto",
    }


def test_remote_desktop_pack_declares_only_v1_view_tools_and_valid_contributions():
    validate_workbench_contributions(plugin_pack)

    assert plugin_pack.metadata["default_enabled"] is False
    assert plugin_pack.metadata["requires_plugin_packs"] == ("cyrene_remote",)
    assert {plugin.name for plugin in plugin_pack.plugins} == {
        "ListRemoteDesktopSessions",
        "InspectRemoteDesktop",
    }
    assert all(plugin.metadata["main_only"] is True for plugin in plugin_pack.plugins)
    assert "desktop:screen_view_agent" in DEFAULT_DESKTOP_CAPABILITIES
    assert "desktop:input_agent" not in DEFAULT_DESKTOP_CAPABILITIES
    assert "desktop:audio_agent" not in DEFAULT_DESKTOP_CAPABILITIES
    assert "desktop:clipboard_agent" not in DEFAULT_DESKTOP_CAPABILITIES

    tool = plugin_pack.metadata["project_tools"][0]
    pane_menu = {item["id"]: item for item in tool["pane_menu"]}
    information = pane_menu["information"]
    mode = pane_menu["connection_mode"]
    quality = pane_menu["quality"]
    assert tool["rail_section"] == "project_tools"
    assert tool["icon_name"] == "remoteDesktop"
    assert tool["pane_owner_scope"] == "project"
    assert tool["click_behavior"] == "replace_workspace"
    assert tool["restore_layout"] is True
    assert information["placement"] == "root"
    assert [field["state_key"] for field in information["fields"]][-3:] == [
        "latency_ms",
        "quality_mode",
        "clipboard_status",
    ]
    latency = next(
        field for field in information["fields"]
        if field["state_key"] == "latency_ms"
    )
    assert latency["label"] == "Latency"
    assert latency["i18n"]["zh"]["label"] == "延迟"
    assert latency["suffix"] == " ms"
    assert pane_menu["file_transfer"]["frontend_action"] == "file_transfer"
    assert pane_menu["switch_display"]["frontend_action"] == "switch_display"
    assert pane_menu["microphone"]["frontend_action"] == "toggle_microphone"
    assert mode["state_key"] == "preferred_mode"
    assert mode["presentation"] == "slider"
    assert mode["available_values_state_key"] == "modes"
    assert mode["reload_view"] is True
    assert mode["context_arguments"] == {"device_id": "device_id"}
    assert mode.get("requires_session") is not True
    assert [item["value"] for item in mode["options"]] == [
        "current_desktop",
        "remote_login",
    ]
    assert quality["context_arguments"] == {"device_id": "device_id"}
    assert quality.get("requires_session") is not True
    assert quality["presentation"] == "slider"
    assert [item["value"] for item in quality["options"]] == [
        "auto",
        "smooth",
        "balanced",
        "clear",
    ]


def test_remote_desktop_uses_the_injected_remote_peer_transport(tmp_path: Path):
    class Gateway:
        connected = True

        def __init__(self) -> None:
            self.commands: list[str] = []

        async def request(self, *_args: Any, **values: Any) -> dict[str, Any]:
            self.commands.append(str(values.get("command") or ""))
            return {
                "ok": True,
                "providers": [
                    {
                        "id": "freerdp",
                        "status": "supported",
                        "modes": ["remote_login"],
                    }
                ],
            }

    gateway = Gateway()
    remote = RemoteControlApplicationService(
        _PeerStore(
            received=("desktop:session_connect", "desktop:remote_login")
        ),
        projection=object(),
        runtime=type("Runtime", (), {"gateway": gateway})(),
    )
    remote.store.peer["last_seen_at"] = datetime.now(timezone.utc).isoformat()
    service = RemoteDesktopService(
        str(tmp_path / "remote-desktop.sqlite3"),
        tmp_path / "data",
        remote_service=remote,
    )

    cards = run(service.cards())
    prepared = run(service.prepare("device-one"))

    assert cards["cards"][0]["online"] is True
    assert cards["cards"][0]["icon_name"] == "remoteDevice"
    assert prepared["remote_probe"]["providers"][0]["id"] == "freerdp"
    assert gateway.commands == ["desktop.probe"]


def test_remote_desktop_mode_setting_disconnects_and_updates_preference(
    tmp_path: Path,
):
    class Gateway:
        connected = True

        def __init__(self) -> None:
            self.commands: list[str] = []

        async def request(self, *_args: Any, **values: Any) -> dict[str, Any]:
            self.commands.append(str(values.get("command") or ""))
            return {"ok": True}

    gateway = Gateway()
    service = _service(
        tmp_path,
        received=("desktop:remote_login",),
        peer_transport=gateway,
    )
    session_id = _connected_session(service)

    result = run(service.set_mode(session_id, "remote_login"))

    assert result["session"]["session_id"] == ""
    assert result["session"]["preferred_mode"] == "remote_login"
    assert service.store.preference("device-one")["preferred_mode"] == "remote_login"
    assert service.store.get_session(session_id)["state"] == "disconnected"
    assert gateway.commands == ["desktop.disconnect"]


def test_remote_desktop_mode_setting_recovers_a_failed_session(tmp_path: Path):
    service = _service(tmp_path, received=("desktop:current_session",))
    session_id = _connected_session(service)
    service.store.update_session(
        session_id,
        state="failed",
        remote_session_id="",
        last_error_code="rdp_authentication_failed",
    )

    result = run(service.set_mode(session_id, "current_desktop"))

    assert result["session"]["preferred_mode"] == "current_desktop"
    assert service.store.preference("device-one")["preferred_mode"] == "current_desktop"
    assert service.store.get_session(session_id)["state"] == "disconnected"


def test_remote_desktop_device_settings_work_without_an_active_session(
    tmp_path: Path,
):
    service = _service(
        tmp_path,
        received=(
            "desktop:session_connect",
            "desktop:current_session",
            "desktop:remote_login",
        ),
    )

    prepared = run(service.prepare("default"))
    mode = run(
        service.set_mode("", "remote_login", device_id="default")
    )
    quality = run(
        service.set_quality("", "clear")
    )

    assert prepared["device_id"] == "device-one"
    assert mode["session"]["preferred_mode"] == "remote_login"
    assert quality["session"]["quality_mode"] == "clear"
    preference = service.store.preference("device-one")
    assert preference["preferred_mode"] == "remote_login"
    assert preference["quality_mode"] == "clear"


def test_remote_desktop_frontend_and_electron_host_are_packaged():
    root = Path(__file__).resolve().parent.parent
    rail = (
        root
        / "src/cyrene/workbench/webui/frontend/features/chat/rail.jsx"
    ).read_text(encoding="utf-8")
    pane = (
        root
        / "src/cyrene/workbench/webui/frontend/features/chat/split-pane.jsx"
    ).read_text(encoding="utf-8")
    styles = (
        root
        / "src/cyrene/workbench/webui/frontend/features/chat/chat.css"
    ).read_text(encoding="utf-8")
    context_styles = (
        root
        / "src/cyrene/workbench/webui/frontend/features/chat/context.css"
    ).read_text(encoding="utf-8")
    plugin_ui = (
        root
        / "src/cyrene/plugins/builtin/cyrene_remote_desktop/frontend/remote-desktop.js"
    ).read_text(encoding="utf-8")
    plugin_html = (
        root
        / "src/cyrene/plugins/builtin/cyrene_remote_desktop/frontend/index.html"
    ).read_text(encoding="utf-8")
    plugin_css = (
        root
        / "src/cyrene/plugins/builtin/cyrene_remote_desktop/frontend/styles.css"
    ).read_text(encoding="utf-8")
    plugin_styles = (
        root
        / "src/cyrene/plugins/builtin/cyrene_remote_desktop/frontend/styles.css"
    ).read_text(encoding="utf-8")
    plugin_host = (
        root
        / "src/cyrene/workbench/webui/frontend/platform/plugins.jsx"
    ).read_text(encoding="utf-8")
    application = (
        root
        / "src/cyrene/plugins/builtin/cyrene_remote_desktop/application.py"
    ).read_text(encoding="utf-8")
    page = (
        root
        / "src/cyrene/workbench/webui/frontend/features/chat/page.jsx"
    ).read_text(encoding="utf-8")
    pane_drop = (
        root
        / "src/cyrene/workbench/webui/frontend/features/chat/pane-drop-controller.jsx"
    ).read_text(encoding="utf-8")
    providers = (
        root
        / "src/cyrene/plugins/builtin/cyrene_remote_desktop/providers.py"
    ).read_text(encoding="utf-8")
    electron_host = (root / "electron/remote-desktop.js").read_text(encoding="utf-8")
    electron_main = (root / "electron/main.js").read_text(encoding="utf-8")
    media_host = (root / "electron/remote-desktop-host.js").read_text(encoding="utf-8")
    rdp_sidecar = (root / "electron/remote-desktop-rdp-sidecar.js").read_text(encoding="utf-8")
    app_use = (root / "electron/app-use.js").read_text(encoding="utf-8")
    app_use_macos = (root / "electron/app-use-macos.jxa").read_text(encoding="utf-8")
    app_use_windows = (root / "electron/app-use-windows.ps1").read_text(encoding="utf-8-sig")
    input_coordinates = (root / "electron/remote-desktop-coordinates.js").read_text(encoding="utf-8")
    package = json.loads((root / "electron/package.json").read_text(encoding="utf-8"))
    packaged_files = set(package["build"]["files"])

    assert 'var clickBehavior = String(tool.click_behavior || "")' in rail
    assert 'replaceWorkspace: clickBehavior === "replace_workspace" || !clickBehavior' in rail
    assert "paneMenu: Array.isArray(tool && tool.pane_menu)" in rail
    assert 'role="menuitemradio"' in pane
    assert "wbc-resource-observation-dot" in pane
    assert ".wbc-pane-card.is-resource-observed::before" in styles
    assert "animation: wbc-agent-chat-flow" in styles
    assert "wbc-resource-observation-shimmer" not in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert "timeoutMs," in plugin_ui
    assert "Number(message.timeoutMs)" in plugin_host
    assert "timeout: requestTimeout" in plugin_host
    assert "request_approval" not in electron_host
    assert "MutterRemoteDesktopInput" in electron_host
    assert "NotifyPointerMotionRelative" in electron_host
    assert "NotifyKeyboardKeysym" in electron_host
    assert "operation: 'set_viewport'" in electron_host
    assert "new RTCPeerConnection" in plugin_ui
    assert "activePeer.addTransceiver('video', { direction: 'recvonly' })" in plugin_ui
    assert "await waitForConnectedVideo(peer, 15000)" in plugin_ui
    assert "video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA" in plugin_ui
    assert "new ResizeObserver" in plugin_ui
    assert "activePeer.getStats()" in plugin_ui
    assert "currentRoundTripTime" in plugin_ui
    assert "latency_ms: latencyMs" in plugin_ui
    assert "type: 'viewport'" in plugin_ui
    assert "currentVideoConstraints" in media_host
    assert "autoTransmissionProfiles" in media_host
    assert "qualityLimitationReason" in media_host
    assert "availableOutgoingBitrate" in media_host
    assert "maintain-framerate" in media_host
    assert "startFreeRdp" in rdp_sidecar
    assert "+dynamic-resolution" in rdp_sidecar
    assert "rdp_authentication_failed" in rdp_sidecar
    assert "remoteDesktop.credentials.request') return 185_000" in plugin_ui
    assert "Acknowledge ipcRenderer.invoke" in electron_host
    assert "remote-desktop-rdp-sidecar.js" in packaged_files
    assert "app.commandLine.appendSwitch('ozone-platform', 'wayland')" in electron_main
    assert "app.commandLine.appendSwitch('ozone-platform', 'x11')" not in electron_main
    assert "app.commandLine.appendSwitch('ozone-platform', 'x11')" in (
        root / "electron" / "remote-desktop-rdp-sidecar.js"
    ).read_text(encoding="utf-8")
    assert "remote-audio" in plugin_ui
    assert "getUserMedia({ audio: true" in plugin_ui
    assert "remoteDesktop.display.select" in plugin_ui
    assert "remoteDesktop.clipboard.files.upload.begin" in plugin_ui
    assert "remoteDesktop.clipboard.files.upload.chunk" in plugin_ui
    assert "remoteDesktop.session.reconnect" in plugin_ui
    assert "remoteDesktop.security.get" in plugin_ui
    assert "securityPollRunning" in plugin_ui
    assert "securityFailureCount < 3" in plugin_ui
    assert "remote_peer_identity_mismatch" in plugin_ui
    assert "securityTimer = window.setTimeout" in plugin_ui
    assert "securityTimer = window.setInterval" not in plugin_ui
    assert 'code: String(error && error.code || "")' in plugin_host
    assert "window.setTimeout(connect, 0)" in plugin_ui
    assert "autoConnectStarted" in plugin_ui
    assert "remoteDesktop.mode.set" in application
    assert "pluginViewRevision" in page
    assert "activateProjectPaneWorkspace" in page
    assert "projectOwnedPlugin" in pane_drop
    assert "wbc-side-split-grip-settings" in styles
    assert "wbc-side-split-grip-information" in pane
    plugin_view_branch = page.split('} else if (card.kind === "plugin-view") {', 1)[1].split(
        "    } else {", 1
    )[0]
    assert "pluginPaneState.paneMenu" in plugin_view_branch
    assert "<WbcSplitGripBar" in plugin_view_branch
    assert "menuContributions={pluginPaneMenu}" in plugin_view_branch
    split_menu_css = styles.split(".wbc-side-split-grip-menu {", 1)[1].split("}", 1)[0]
    split_menu_list_css = styles.split(".wbc-side-split-grip-accordion {", 1)[1].split("}", 1)[0]
    assert "position: fixed;" in split_menu_css
    assert "height: max-content;" in split_menu_css
    assert "align-self: start;" in split_menu_css
    assert "height: max-content;" in split_menu_list_css
    assert 'rootRef.current.closest(".workbench-grid")' in pane
    assert 'propertyName.indexOf("--wb-") !== 0' in pane
    assert 'rootRef.current.closest(".wbc-pane-card, .wbc-side-card")' in pane
    assert 'portalTheme["--wbc-split-grip-surface"] = cardStyle.backgroundColor' in pane
    assert 'style={Object.assign({}, menuPosition.portalTheme' in pane
    split_menu_surface_css = styles.split(
        ".wbc-panel-accordion-surface.wbc-side-split-grip-menu {", 1
    )[1].split("}", 1)[0]
    assert "border: 1px solid var(--wbc-split-grip-border-color);" in split_menu_surface_css
    assert "background: var(--wbc-split-grip-surface," in split_menu_surface_css
    split_menu_item_css = styles.split(
        ".wbc-side-split-grip-menu .wbc-side-accordion-item {", 1
    )[1].split("}", 1)[0]
    assert "border-bottom: 1px solid var(--wbc-split-grip-divider-color);" in split_menu_item_css
    assert "--wbc-split-grip-divider-color: rgba(23, 28, 34, .055);" in split_menu_surface_css
    dark_split_menu_surface_css = styles.split(
        'html[data-theme="dark"] .wbc-panel-accordion-surface.wbc-side-split-grip-menu {', 1
    )[1].split("}", 1)[0]
    assert "--wbc-split-grip-divider-color: rgba(255, 255, 255, .03);" in dark_split_menu_surface_css
    split_menu_animation_css = styles.split(
        ".wbc-side-split-grip-expanded-body {", 1
    )[1].split("}", 1)[0]
    assert "height 190ms cubic-bezier(.2, .8, .2, 1)" in split_menu_animation_css
    assert 'className={"wbc-side-split-grip-expanded-body" + (expanded ? " open" : "")}' in pane
    assert "window.setTimeout(function () { setRendered(false); }, 190)" in pane
    slider_css = styles.split(
        '.wbc-side-split-grip-setting-slider input[type="range"] {', 1
    )[1].split("}", 1)[0]
    assert "var(--wbc-split-grip-slider-track)" in slider_css
    split_menu_body_css = styles.split(
        ".wbc-side-split-grip-expanded-content {", 1
    )[1].split("}", 1)[0]
    assert "padding: 0 16px 12px;" in split_menu_body_css
    assert 'window.ReactDOM.createPortal((' in pane
    assert 'typeof document !== "undefined" ? document.body : null' in pane
    assert "menuBody={true}" in pane
    collapse_css = context_styles.split(".wbc-side-collapse {", 1)[1].split("}", 1)[0]
    assert "interpolate-size: allow-keywords;" in collapse_css
    assert "pluginViewCommand" in page
    assert "cyrene:plugin-view-interaction" in pane
    assert "contextValue = card.payload[stateKey]" in page
    assert "type: 'interaction'" in plugin_ui
    assert '<div class="toolbar-actions" hidden' in plugin_html
    assert 'id="popover-scrim"' in plugin_html
    assert 'id="file-menu-close"' in plugin_html
    assert 'aria-labelledby="file-menu-title"' in plugin_html
    assert '<footer class="statusbar">' not in plugin_html
    assert 'id="status-copy"' not in plugin_html
    assert "justify-content: center" in plugin_css
    assert "grid-template-rows: minmax(0, 1fr)" in plugin_css
    assert ".toolbar { display: none; }" in plugin_css
    assert "top: 12px" in plugin_css
    assert "left: 50%" in plugin_css
    assert ".file-popover { right:" not in plugin_css
    assert "function closePopovers(options)" in plugin_ui
    assert "togglePopover(fileMenu, fileMenuClose)" in plugin_ui
    assert "popoverScrim.addEventListener('pointerdown'" in plugin_ui
    assert 'data-input-enabled="true"' not in plugin_css
    assert "#remote-video { z-index: 1; object-fit: contain; cursor: none; }" in plugin_css
    assert '[data-state="connected"] .stage { cursor: none; }' in plugin_css
    assert "const retryDelays = [0, 100, 250, 500]" in electron_host
    assert "Failed to load the active-session indicator after retries" in electron_host
    assert "constraints.cursor = 'always'" in media_host
    assert "supported.cursor" not in media_host
    assert "playoutDelayHint = 0" in plugin_ui
    assert "jitterBufferTarget = 0" in plugin_ui
    assert "videoBackdrop.srcObject = null" in plugin_ui
    assert "videoBackdrop.srcObject = remoteVideoStream" not in plugin_ui
    assert "auto: { width: { ideal: 1600 }, height: { ideal: 900 }, frameRate: { ideal: 30, max: 30 } }" in media_host
    assert "auto: { maxBitrate: 8_000_000, maxFramerate: 30 }" in media_host
    assert "Math.min(1, maxWidth / requestedWidth, maxHeight / requestedHeight)" in media_host
    assert "NotifyPointerMotionAbsolute(this.streamPath, x, y)" in electron_host
    assert "this.screenCastSession.RecordVirtual" in electron_host
    assert "pipewiresrc" in electron_host
    assert "max-framerate=${dimensions.frameRate}/1" in electron_host
    assert "native_capture: record.nativeCapture" in electron_host
    assert "canvas.captureStream" in media_host
    assert "releaseNativeSurface" in media_host
    assert "['button_down', 'right_click', 'double_click'].includes(action)" not in electron_host
    assert "#remote-video { z-index: 1; object-fit: contain" in plugin_css
    assert ".remote-video-backdrop" in plugin_css
    assert 'id="remote-video-backdrop"' in plugin_html
    assert "viewport: currentViewportSize()" in plugin_ui
    assert "maxBitrate: 42_000_000" in media_host
    assert "maintain-framerate" in media_host
    assert "const scale = Math.min(rect.width / video.videoWidth" in plugin_ui
    assert "localX / renderedWidth" in plugin_ui
    assert 'id="connect-button"' not in plugin_html
    assert "remoteDesktopGlobalInput" in app_use
    assert "globalWindowsInput" in electron_host
    perform_action = app_use_windows.split("function Perform-Action($Payload) {", 1)[1]
    assert perform_action.index("if ($capability -eq 'key_sequence')") < perform_action.index("$root = Get-Root")
    assert 'class="mode-picker"' not in plugin_html
    assert "--bg: transparent" in plugin_styles
    assert ".stage { position: relative; min-height: 0; overflow: hidden; outline: none; background: var(--bg); }" in plugin_styles
    assert "background: radial-gradient" not in plugin_styles
    assert "backdrop-filter" not in plugin_styles
    assert ':root[data-theme="light"]' in plugin_styles
    assert "color: var(--text)" in plugin_styles
    assert 'type: "theme"' in plugin_host
    assert "themeObserver.observe" in plugin_host
    assert "applyTheme(context.theme)" in plugin_ui
    assert '"local_bind": {"host": "127.0.0.1", "port": 0}' in providers
    assert "rdp_port_occupied_by_other_service" in providers
    assert "showIndicator(args)" in electron_host
    assert "consumeForcedDisconnects()" in electron_host
    assert "desktop_wayland_input_bridge_unavailable" in electron_host
    assert "securityEpoch" in electron_host
    assert "remoteDesktopInput(appSession, capability, parameters)" in electron_host
    assert "focus_policy: 'never'" in electron_host
    assert "activePointerId !== event.pointerId" in plugin_ui
    assert "async remoteDesktopInput(" in app_use
    assert "{ name: 'pointer_event'" not in app_use
    assert "'pointer_event'" in app_use_macos
    assert "'pointer_event'" in app_use_windows
    assert "SetProcessDPIAware" in app_use_windows
    assert "SetPhysicalCursorPos" in app_use_windows
    assert "dipToScreenPoint" in input_coordinates
    assert "dipToScreenRect" in input_coordinates
    assert "microphoneEnabled" in media_host
    assert "transceiver.direction" in media_host
    assert "xdotool" in package["build"]["deb"]["depends"]
    assert "xdotool" in package["build"]["rpm"]["depends"]
    assert {
        "remote-desktop.js",
        "remote-desktop-coordinates.js",
        "remote-desktop-preload.js",
        "remote-desktop-host.html",
        "remote-desktop-host.js",
        "remote-desktop-indicator-preload.js",
        "remote-desktop-indicator.html",
        "remote-desktop-indicator.css",
        "remote-desktop-indicator.js",
        "remote-desktop-credential-preload.js",
        "remote-desktop-credential.html",
        "remote-desktop-credential.js",
    } <= packaged_files


def test_rdp_listener_probe_distinguishes_protocol_from_an_occupied_port():
    def probe(response: bytes) -> str:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = int(listener.getsockname()[1])

        def serve() -> None:
            try:
                connection, _address = listener.accept()
                with connection:
                    connection.recv(64)
                    connection.sendall(response)
            finally:
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            return _rdp_listener_state(port)
        finally:
            thread.join(timeout=2)

    assert probe(bytes.fromhex("030000130ed000001234000200080003000000")) == "rdp"
    assert probe(b"HTTP/1.1 400 Bad Request\r\n\r\n") == "non_rdp"


def test_rdp_probe_selects_the_backend_that_is_actually_listening(
    monkeypatch: pytest.MonkeyPatch,
):
    async def targets(_system: str) -> list[tuple[str, int, str, str]]:
        return [
            ("gnome-remote-desktop", 3389, "gnome_settings", "closed"),
            ("xrdp", 3391, "xrdp_config", "rdp"),
        ]

    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_remote_desktop.providers.platform.system",
        lambda: "Linux",
    )
    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_remote_desktop.providers._rdp_backend_candidates",
        lambda _system: ("gnome-remote-desktop", "xrdp"),
    )
    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_remote_desktop.providers._probe_rdp_targets",
        targets,
    )
    provider = FreeRdpProvider()
    provider.binary = sys.executable

    descriptor = run(provider.probe())

    assert descriptor.status == "supported"
    assert descriptor.modes == ("remote_login",)
    assert descriptor.display_server == "xrdp"
    assert {"clipboard_text", "clipboard_image", "clipboard_file"}.issubset(
        descriptor.capabilities
    )


def test_freerdp_connection_arguments_include_initial_controller_viewport():
    arguments = _freerdp_connection_arguments(
        session_id="rdp-one",
        rdp_port=3389,
        rdp_port_source="gnome_settings",
        offer={"type": "offer", "sdp": "test"},
        display_id="rdp-display-1",
        quality_mode="clear",
        ice_servers=[],
        permissions={"input": True},
        credentials={"username": "user", "password": "secret"},
        viewport={"width": 1512, "height": 949, "device_pixel_ratio": 2},
    )

    assert arguments["viewport"] == {
        "width": 1512,
        "height": 949,
        "device_pixel_ratio": 2,
    }
    assert arguments["rdp"]["dynamic_resolution"] is True


def test_freerdp_sidecar_reports_failure_before_destroying_display():
    root = Path(__file__).resolve().parent.parent
    sidecar = (root / "electron/remote-desktop-rdp-sidecar.js").read_text(
        encoding="utf-8"
    )

    assert "stop({ terminateRuntime: false })" in sidecar
    assert "Destroying Xvfb here can terminate Electron" in sidecar


def test_freerdp_development_launcher_uses_linux_electron_flags():
    root = Path(__file__).resolve().parent.parent
    source = (
        root
        / "src/cyrene/plugins/builtin/cyrene_remote_desktop/freerdp_dev_sidecar.py"
    ).read_text(encoding="utf-8")

    assert '"--no-sandbox"' in source
    assert '"--disable-dev-shm-usage"' in source
    assert '"--disable-gpu"' in source


def test_linux_development_freerdp_bridge_reuses_electron_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    electron = tmp_path / "electron"
    electron.write_text("runtime", encoding="utf-8")
    resources = tmp_path / "resources"
    resources.mkdir()
    (resources / "remote-desktop-rdp-sidecar.js").write_text("sidecar", encoding="utf-8")
    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_remote_desktop.providers.platform.system",
        lambda: "Linux",
    )
    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_remote_desktop.providers.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"xfreerdp3", "Xvfb", "xdotool"} else None,
    )
    monkeypatch.setenv("CYRENE_ELECTRON_DEV", "1")
    monkeypatch.setenv("CYRENE_ELECTRON_PATH", str(electron))
    monkeypatch.setenv("CYRENE_ELECTRON_RESOURCES_DIR", str(resources))
    monkeypatch.delenv("CYRENE_FREERDP_SIDECAR", raising=False)

    provider = FreeRdpProvider()

    assert provider.binary == ""
    assert provider._sidecar_command()[0] == sys.executable
    assert provider._sidecar_command()[1].endswith("freerdp_dev_sidecar.py")


def test_turn_shared_secret_issues_time_limited_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CYRENE_TURN_URL", "turns:turn.example.test:5349")
    monkeypatch.setenv("CYRENE_TURN_SHARED_SECRET", "turn-secret")
    monkeypatch.setenv("CYRENE_TURN_TTL_SECONDS", "300")
    monkeypatch.delenv("CYRENE_TURN_USERNAME", raising=False)
    monkeypatch.delenv("CYRENE_TURN_CREDENTIAL", raising=False)

    server = RemoteDesktopService._ice_servers("session-one")[-1]
    username = str(server["username"])
    expected = base64.b64encode(
        hmac.new(b"turn-secret", username.encode(), hashlib.sha1).digest()
    ).decode("ascii")

    assert server["urls"] == ["turns:turn.example.test:5349"]
    assert username.split(":", 1)[0].isdecimal()
    assert server["credential"] == expected


def test_network_status_reports_when_turn_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    for name in (
        "CYRENE_STUN_URL",
        "CYRENE_TURN_URL",
        "CYRENE_TURN_SHARED_SECRET",
        "CYRENE_TURN_USERNAME",
        "CYRENE_TURN_CREDENTIAL",
    ):
        monkeypatch.delenv(name, raising=False)

    status = RemoteDesktopService._network_status("session-one")

    assert status["ice_servers"] == []
    assert status["relay_ready"] is False
    assert {item["code"] for item in status["diagnostics"]} == {
        "turn_not_configured",
        "ice_discovery_not_configured",
    }


def test_rdp_port_resolution_uses_system_configuration_and_validates_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = tmp_path / "xrdp.ini"
    config.write_text(
        "[Globals]\nport=tcp://.:3391 127.0.0.1:3392\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CYRENE_XRDP_CONFIG", str(config))
    monkeypatch.delenv("CYRENE_RDP_PORT", raising=False)

    assert _parse_rdp_port("tcp://.:3391") == 3391
    assert _configured_rdp_port("linux") == (3391, "xrdp_config")

    monkeypatch.setenv("CYRENE_RDP_PORT", "3395")
    assert _configured_rdp_port("linux") == (3395, "environment")

    monkeypatch.setenv("CYRENE_RDP_PORT", "not-a-port")
    with pytest.raises(ValueError, match="rdp_port_invalid"):
        _configured_rdp_port("linux")

    occupied = _normalize_rdp_connect_error(
        {"ok": False, "code": "rdp_protocol_mismatch"},
        3395,
    )
    assert occupied["code"] == "rdp_port_occupied_by_other_service"
    assert occupied["rdp_port"] == 3395

    dynamic_bind = _normalize_rdp_connect_error(
        {"ok": False, "code": "address_in_use"},
        3395,
    )
    assert dynamic_bind["code"] == "rdp_local_port_allocation_failed"
    assert dynamic_bind["retryable"] is True


def test_credential_broker_is_one_shot_and_zeroes_discarded_values():
    broker = CredentialBroker()
    handle = broker.put(
        {"username": "alice", "domain": "LAB", "password": "secret"}
    )
    assert broker.take(handle) == {
        "username": "alice",
        "domain": "LAB",
        "password": "secret",
    }
    with pytest.raises(RemoteDesktopError) as reused:
        broker.take(handle)
    assert reused.value.code == "desktop_credential_handle_expired"

    discarded = broker.put({"username": "bob", "password": "erase-me"})
    raw_password = broker._values[discarded][1]["password"]
    broker.discard(discarded)
    assert raw_password == bytearray(len(raw_password))


def test_layout_grants_reject_agent_self_authorization_and_stale_revisions(tmp_path: Path):
    service = _service(
        tmp_path,
        received=("desktop:screen_view_agent",),
    )
    session_id = _connected_session(service)
    desktop_card = {
        "card_id": "desktop-card",
        "kind": "plugin-view",
        "pack_id": "cyrene_remote_desktop",
        "session_id": session_id,
    }

    denied = run(
        service.project_layout(
            {
                "pane_layout_id": "layout-one",
                "projection_scope_id": "project-one",
                "revision": 1,
                "origin": "agent_ui_action",
                "cards": [
                    {
                        "card_id": "chat-card",
                        "kind": "chat",
                        "chat_id": "chat-one",
                        "meta": {"origin": "agent"},
                    },
                    desktop_card,
                ],
            }
        )
    )
    assert denied["grant_count"] == 0
    assert service.store.is_authorized("chat-one", session_id) is False

    granted = run(
        service.project_layout(
            {
                "pane_layout_id": "layout-one",
                "projection_scope_id": "project-one",
                "revision": 2,
                "origin": "user_pointer",
                "cards": [
                    {
                        "card_id": "chat-card",
                        "kind": "chat",
                        "chat_id": "chat-one",
                        "meta": {"origin": "agent", "claimedByUser": True},
                    },
                    desktop_card,
                ],
            }
        )
    )
    assert granted["grant_count"] == 1
    assert [item["session_id"] for item in service.authorized_sessions("chat-one")] == [
        session_id
    ]

    switched = run(
        service.project_layout(
            {
                "pane_layout_id": "layout-two",
                "projection_scope_id": "project-one",
                "revision": 3,
                "origin": "user_pointer",
                "cards": [],
            }
        )
    )
    assert switched["grant_count"] == 0
    assert service.authorized_sessions("chat-one") == []

    with pytest.raises(RemoteDesktopError) as stale:
        run(
            service.project_layout(
                {
                    "pane_layout_id": "layout-one",
                    "projection_scope_id": "project-one",
                    "revision": 1,
                    "origin": "system_restore",
                    "cards": [],
                }
            )
        )
    assert stale.value.code == "desktop_layout_revision_stale"

    service.store.update_session(session_id, state="disconnected")
    assert service.authorized_sessions("chat-one") == []


def test_secure_surface_blocks_agent_snapshot_before_frame_request(tmp_path: Path):
    service = _service(
        tmp_path,
        received=("desktop:screen_view_agent",),
    )
    session_id = _connected_session(service, secure=True)
    service.store.replace_layout_grants(
        "layout-secure",
        1,
        [
            {
                "session_id": session_id,
                "chat_id": "chat-one",
                "origin": "user_pointer",
                "granted": True,
            }
        ],
    )

    with pytest.raises(RemoteDesktopError) as blocked:
        run(
            service.request_agent_snapshot(
                session_id,
                "chat-one",
                reason="Inspect the visible error",
                region=None,
            )
        )
    assert blocked.value.code == "desktop_secure_surface_masked"
    assert service._observations == {}


def test_agent_snapshot_is_discarded_when_security_epoch_changes(tmp_path: Path):
    service = _service(
        tmp_path,
        received=("desktop:screen_view_agent",),
    )
    session_id = _connected_session(service)
    service.store.replace_layout_grants(
        "layout-secure-race",
        1,
        [
            {
                "session_id": session_id,
                "chat_id": "chat-one",
                "origin": "user_pointer",
                "granted": True,
            }
        ],
    )

    class Gateway:
        def __init__(self) -> None:
            self.epochs = iter((7, 8))

        async def request(self, *_args: Any, **values: Any) -> dict[str, Any]:
            assert values["command"] == "desktop.security.get"
            return {
                "ok": True,
                "secure_surface": False,
                "security_epoch": next(self.epochs),
            }

    gateway_instance = Gateway()
    service.remote_service.peer_transport = gateway_instance

    async def scenario() -> None:
        task = asyncio.create_task(
            service.request_agent_snapshot(
                session_id,
                "chat-one",
                reason="Inspect the visible error",
                region=None,
            )
        )
        for _attempt in range(100):
            if service._observations:
                break
            await asyncio.sleep(0)
        assert service._observations
        observation_id = next(iter(service._observations))
        image = Image.new("RGB", (2, 2), "white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        await service.submit_observation_frame(observation_id, output.getvalue())
        with pytest.raises(RemoteDesktopError) as blocked:
            await task
        assert blocked.value.code == "desktop_secure_surface_masked"

    run(scenario())
    assert service._snapshots == {}
    assert list(service.snapshot_directory.glob("*.png")) == []


def test_local_clipboard_files_are_staged_and_forwarded_in_chunks(tmp_path: Path):
    service = _service(
        tmp_path,
        received=("desktop:clipboard_file_user",),
    )
    session_id = _connected_session(service)
    calls: list[tuple[str, dict[str, Any]]] = []

    class Gateway:
        async def request(self, *_args: Any, **values: Any) -> dict[str, Any]:
            command = str(values["command"])
            payload = dict(values["payload"])
            calls.append((command, payload))
            if command == "desktop.clipboard.file.upload.begin":
                return {"ok": True, "offset": 0}
            if command == "desktop.clipboard.file.upload.chunk":
                raw = base64.b64decode(payload["content_base64"], validate=True)
                assert hashlib.sha256(raw).hexdigest() == payload["chunk_sha256"]
                return {"ok": True, "next_offset": payload["offset"] + len(raw)}
            return {"ok": True}

    gateway = Gateway()
    service.remote_service.peer_transport = gateway
    begun = service.begin_local_clipboard_files(
        session_id,
        [{"relative_path": "folder/report.txt", "size": 6}],
    )
    upload_id = str(begun["upload_id"])
    first = b"abc"
    second = b"123"
    assert service.append_local_clipboard_file(
        upload_id,
        "folder/report.txt",
        0,
        first,
        hashlib.sha256(first).hexdigest(),
    )["next_offset"] == 3
    assert service.append_local_clipboard_file(
        upload_id,
        "folder/report.txt",
        3,
        second,
        hashlib.sha256(second).hexdigest(),
    )["next_offset"] == 6

    result = run(service.commit_local_clipboard_files(upload_id))

    assert result == {"ok": True, "count": 1, "bytes": 6}
    assert upload_id not in service._local_clipboard_uploads
    assert any(command == "desktop.clipboard.file.apply" for command, _ in calls)


def test_host_rechecks_mode_capability_before_selecting_provider(tmp_path: Path):
    service = _service(
        tmp_path,
        granted=(
            "desktop:session_connect",
            "desktop:screen_view_user",
            "desktop:current_session",
        ),
    )

    result = run(
        service.handle_remote_command(
            "device-one",
            "desktop.negotiate",
            _offer_payload(mode="remote_login"),
        )
    )
    assert result["ok"] is False
    assert result["code"] == "desktop_capability_denied"
    assert service._host_sessions == {}


def test_builtin_host_connects_pregranted_peer_and_shows_persistent_indicator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class Provider:
        id = "electron-current-desktop"

        def __init__(self) -> None:
            self.negotiations = 0
            self.disconnections: list[str] = []

        async def negotiate(self, _session_id: str, **_values: Any) -> dict[str, Any]:
            self.negotiations += 1
            return {
                "ok": True,
                "answer": {"type": "answer", "sdp": "v=0\r\n"},
                "transport_kind": "p2p",
            }

        async def list_displays(self, _session_id: str) -> list[DisplayDescriptor]:
            return [DisplayDescriptor("display-one", "Primary", 1920, 1080, primary=True)]

        async def disconnect(self, session_id: str) -> None:
            self.disconnections.append(session_id)

    class Providers:
        def __init__(self, provider: Provider) -> None:
            self.provider = provider

        async def select(self, _mode: str) -> Provider:
            return self.provider

        def by_id(self, provider_id: str) -> Provider:
            if provider_id != self.provider.id:
                raise KeyError(provider_id)
            return self.provider

    service = _service(
        tmp_path,
        granted=(
            "desktop:session_connect",
            "desktop:screen_view_user",
            "desktop:current_session",
        ),
    )
    provider = Provider()
    service.providers = Providers(provider)  # type: ignore[assignment]
    calls: list[tuple[str, dict[str, Any]]] = []

    async def electron_rpc(
        method: str,
        arguments: dict[str, Any] | None = None,
        **_values: Any,
    ) -> dict[str, Any]:
        calls.append((method, dict(arguments or {})))
        return {"ok": True}

    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_remote_desktop.service.electron_desktop_rpc",
        electron_rpc,
    )

    connected = run(
        service.handle_remote_command(
            "device-one", "desktop.negotiate", _offer_payload()
        )
    )
    assert connected["ok"] is True
    assert connected["permissions"]["input"] is False
    assert provider.negotiations == 1
    assert all(method != "request_approval" for method, _args in calls)
    assert any(method == "show_indicator" for method, _args in calls)
    assert all(
        args.get("can_control") is False
        for method, args in calls
        if method == "show_indicator"
    )

    unauthorized_display = run(
        service.handle_remote_command(
            "device-one",
            "desktop.display.select",
            {
                "session_id": connected["remote_session_id"],
                "display_id": "display-one",
            },
        )
    )
    assert unauthorized_display["code"] == "desktop_capability_denied"


def test_host_reserves_single_controller_slot_before_provider_await(tmp_path: Path):
    class BlockingProvider:
        id = "blocking-provider"

        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def negotiate(self, session_id: str, **_values: Any) -> dict[str, Any]:
            self.entered.set()
            await self.release.wait()
            return {
                "ok": True,
                "answer": {"type": "answer", "sdp": "v=0\r\n"},
                "transport_kind": "p2p",
            }

        async def list_displays(self, _session_id: str) -> list[DisplayDescriptor]:
            return [
                DisplayDescriptor(
                    "display-one", "Primary display", 1920, 1080, primary=True
                )
            ]

        async def disconnect(self, _session_id: str) -> None:
            return None

    class ProviderManager:
        def __init__(self, provider: BlockingProvider) -> None:
            self.provider = provider

        async def select(self, _mode: str) -> BlockingProvider:
            return self.provider

        def by_id(self, provider_id: str) -> BlockingProvider:
            if provider_id != self.provider.id:
                raise KeyError(provider_id)
            return self.provider

    async def scenario() -> None:
        service = _service(
            tmp_path,
            granted=(
                "desktop:session_connect",
                "desktop:screen_view_user",
                "desktop:current_session",
            ),
        )
        provider = BlockingProvider()
        service.providers = ProviderManager(provider)  # type: ignore[assignment]
        first_task = asyncio.create_task(
            service.handle_remote_command(
                "device-one", "desktop.negotiate", _offer_payload()
            )
        )
        await asyncio.wait_for(provider.entered.wait(), timeout=1)
        second = await service.handle_remote_command(
            "device-one", "desktop.negotiate", _offer_payload()
        )
        assert second["ok"] is False
        assert second["code"] == "desktop_controller_busy"
        provider.release.set()
        first = await asyncio.wait_for(first_task, timeout=1)
        assert first["ok"] is True
        assert len(service._host_sessions) == 1

    run(scenario())
