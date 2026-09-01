"""Platform Provider implementations and deterministic capability probing."""

from __future__ import annotations

import asyncio
import configparser
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from typing import Any

from .contracts import (
    DesktopMode,
    DisplayDescriptor,
    ProviderDescriptor,
    QualityMode,
    RemoteDesktopProvider,
)
from .electron_bridge import electron_desktop_available, electron_desktop_rpc


def _command_version(command: str, *args: str) -> str:
    path = shutil.which(command)
    if not path:
        return ""
    try:
        result = subprocess.run(
            [path, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=3,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "installed"
    return str(result.stdout or "installed").splitlines()[0][:160]


_DEFAULT_RDP_PORT = 3389
_RDP_PORT_ENV = "CYRENE_RDP_PORT"
_RDP_NEGOTIATION_REQUEST = bytes.fromhex(
    # TPKT + X.224 Connection Request + RDP_NEG_REQ.  Ask for TLS, CredSSP,
    # and CredSSP-with-early-user-auth; both an RDP_NEG_RSP and an
    # RDP_NEG_FAILURE prove that the listener is actually speaking RDP.
    "030000130ee00000000000010008000b000000"
)


def _parse_rdp_port(value: Any) -> int | None:
    """Return the first TCP port from a Windows/xrdp-style port value."""

    raw = str(value or "").strip()
    if not raw:
        return None
    for token in re.split(r"[\s,]+", raw):
        candidate = token.strip()
        if not candidate:
            continue
        if candidate.isdecimal():
            port = int(candidate)
        else:
            match = re.search(r":(\d+)$", candidate)
            if match is None:
                continue
            port = int(match.group(1))
        if 1 <= port <= 65535:
            return port
    return None


def _windows_rdp_port() -> int | None:
    try:
        import winreg  # type: ignore[import-not-found]

        path = r"SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            value, _value_type = winreg.QueryValueEx(key, "PortNumber")
        return _parse_rdp_port(value)
    except (ImportError, OSError, ValueError):
        return None


def _xrdp_port(config_path: str = "/etc/xrdp/xrdp.ini") -> int | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        with open(config_path, "r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, configparser.Error):
        return None
    globals_section = next(
        (section for section in parser.sections() if section.casefold() == "globals"),
        "",
    )
    if not globals_section:
        return None
    return _parse_rdp_port(parser.get(globals_section, "port", fallback=""))


def _gnome_rdp_port() -> int | None:
    gsettings = shutil.which("gsettings")
    if not gsettings:
        return None
    try:
        result = subprocess.run(
            [
                gsettings,
                "get",
                "org.gnome.desktop.remote-desktop.rdp",
                "port",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_rdp_port(result.stdout)


def _configured_rdp_port(system: str, rdp_backend: str = "") -> tuple[int, str]:
    configured = str(os.environ.get(_RDP_PORT_ENV) or "").strip()
    if configured:
        port = _parse_rdp_port(configured)
        if port is None or str(port) != configured:
            raise ValueError("rdp_port_invalid")
        return port, "environment"
    if system == "windows":
        port = _windows_rdp_port()
        return (port, "windows_registry") if port is not None else (_DEFAULT_RDP_PORT, "default")
    if system == "linux":
        if rdp_backend == "gnome-remote-desktop":
            port = _gnome_rdp_port()
            if port is not None:
                return port, "gnome_settings"
        config_path = str(os.environ.get("CYRENE_XRDP_CONFIG") or "/etc/xrdp/xrdp.ini")
        port = _xrdp_port(config_path)
        return (port, "xrdp_config") if port is not None else (_DEFAULT_RDP_PORT, "default")
    return _DEFAULT_RDP_PORT, "default"


def _rdp_listener_state(port: int) -> str:
    """Return ``rdp``, ``non_rdp``, or ``closed`` for a loopback listener."""

    try:
        connection = socket.create_connection(("127.0.0.1", int(port)), timeout=0.65)
    except OSError:
        return "closed"
    try:
        with connection:
            connection.settimeout(0.85)
            connection.sendall(_RDP_NEGOTIATION_REQUEST)
            response = bytearray()
            while len(response) < 4:
                chunk = connection.recv(4 - len(response))
                if not chunk:
                    break
                response.extend(chunk)
            expected = (
                min(64, int.from_bytes(response[2:4], "big"))
                if len(response) == 4 and response[0:2] == b"\x03\x00"
                else 11
            )
            while len(response) < expected:
                chunk = connection.recv(expected - len(response))
                if not chunk:
                    break
                response.extend(chunk)
    except OSError:
        return "non_rdp"
    if (
        len(response) >= 11
        and response[0:2] == b"\x03\x00"
        and int.from_bytes(response[2:4], "big") >= 11
        and response[5] == 0xD0
    ):
        # Older servers can omit RDP_NEG_RSP and immediately continue with the
        # selected legacy security protocol.  A valid X.224 CC is sufficient.
        return "rdp"
    return "non_rdp"


def _loopback_port_is_open(port: int) -> bool:
    return _rdp_listener_state(port) != "closed"


def _gnome_remote_desktop_available() -> bool:
    candidates = (
        shutil.which("gnome-remote-desktop-daemon") or "",
        "/usr/libexec/gnome-remote-desktop-daemon",
        "/usr/lib/gnome-remote-desktop-daemon",
    )
    return any(candidate and os.path.isfile(candidate) for candidate in candidates)


def _xrdp_available() -> bool:
    candidate = shutil.which("xrdp") or "/usr/sbin/xrdp"
    return bool(candidate and os.path.isfile(candidate))


def _rdp_backend_candidates(system: str) -> tuple[str, ...]:
    if system == "windows":
        return ("windows-rdp-service",)
    if system != "linux":
        return ()
    result: list[str] = []
    if _gnome_remote_desktop_available():
        result.append("gnome-remote-desktop")
    if _xrdp_available():
        result.append("xrdp")
    return tuple(result)


async def _probe_rdp_targets(
    system: str,
) -> list[tuple[str, int, str, str]]:
    """Probe every configured RDP backend instead of trusting install order."""

    result: list[tuple[str, int, str, str]] = []
    for backend in _rdp_backend_candidates(system):
        port, source = _configured_rdp_port(system, backend)
        state = await asyncio.to_thread(_rdp_listener_state, port)
        result.append((backend, port, source, state))
    return result


def _normalize_rdp_connect_error(result: dict[str, Any], port: int) -> dict[str, Any]:
    if result.get("ok") is not False:
        return result
    code = str(result.get("code") or "")
    if code in {
        "address_in_use", "bind_failed", "local_port_in_use",
        "rdp_local_port_in_use",
    }:
        return {
            **result,
            "code": "rdp_local_port_allocation_failed",
            "error": "The Sidecar could not allocate a local ephemeral port. Retry the connection.",
            "retryable": True,
        }
    if code in {
        "rdp_protocol_mismatch", "rdp_service_mismatch",
        "rdp_port_occupied", "not_an_rdp_service",
    }:
        return {
            **result,
            "code": "rdp_port_occupied_by_other_service",
            "error": f"Port {port} is occupied, but the listener is not a usable RDP service.",
            "rdp_port": int(port),
        }
    return result


def _electron_display_descriptors(result: dict[str, Any]) -> tuple[DisplayDescriptor, ...]:
    return tuple(
        DisplayDescriptor(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or item.get("id") or "Display"),
            width=max(1, int(item.get("width") or 1)),
            height=max(1, int(item.get("height") or 1)),
            scale=max(0.1, float(item.get("scale") or 1)),
            rotation=int(item.get("rotation") or 0),
            primary=bool(item.get("primary")),
            kind=str(item.get("kind") or "physical"),
        )
        for item in result.get("displays") or ()
        if isinstance(item, dict) and str(item.get("id") or "")
    )


def _electron_probe_diagnostics(
    result: dict[str, Any], required_denied: list[str]
) -> list[dict[str, Any]]:
    diagnostics = [
        {
            "code": f"permission_{key}_required",
            "severity": "warning",
            "message": f"The {key} permission is required for full remote desktop control.",
        }
        for key in required_denied
    ]
    if str(result.get("display_server") or "").lower() == "wayland" and "accessibility" in required_denied:
        diagnostics.append(
            {
                "code": "wayland_input_bridge_unavailable",
                "severity": "warning",
                "message": "Wayland screen sharing is available, but input control requires the desktop RemoteDesktop/libei bridge or an RDP login session.",
            }
        )
    if not result.get("system_audio"):
        diagnostics.append(
            {
                "code": "system_audio_backend_unavailable",
                "severity": "warning",
                "message": "System audio capture is unavailable; install or enable the platform PipeWire/PulseAudio capture backend.",
            }
        )
    if not result.get("microphone_injection"):
        diagnostics.append(
            {
                "code": "microphone_injection_unavailable",
                "severity": "info",
                "message": "Current-desktop microphone return requires a configured virtual-audio input; ordinary speaker playback is not advertised as a microphone.",
            }
        )
    return diagnostics


class ElectronCurrentDesktopProvider:
    id = "electron-current-desktop"

    async def probe(self) -> ProviderDescriptor:
        if not electron_desktop_available():
            return ProviderDescriptor(
                id=self.id,
                version="1",
                status="unsupported",
                modes=(),
                diagnostics=(
                    {
                        "code": "desktop_host_unavailable",
                        "severity": "error",
                        "message": "Start Cyrene in the desktop app to share the current desktop.",
                    },
                ),
            )
        result = await electron_desktop_rpc("probe", timeout=10)
        if result.get("ok") is False:
            return ProviderDescriptor(
                id=self.id,
                version="1",
                status="degraded",
                modes=("current_desktop",),
                diagnostics=(
                    {
                        "code": str(result.get("code") or "desktop_probe_failed"),
                        "severity": "error",
                        "message": str(result.get("error") or "Desktop capture is unavailable."),
                    },
                ),
            )
        displays = _electron_display_descriptors(result)
        permissions = result.get("permissions") if isinstance(result.get("permissions"), dict) else {}
        required_denied = [
            key
            for key in ("screen", "accessibility")
            if str(permissions.get(key) or "granted") not in {"granted", "not_required"}
        ]
        diagnostics = _electron_probe_diagnostics(result, required_denied)
        capabilities = [
            "video", "multi_monitor", "clipboard_text",
            "clipboard_image", "clipboard_file", "webrtc",
        ]
        if "accessibility" not in required_denied:
            capabilities.append("input")
        if result.get("system_audio"):
            capabilities.append("system_audio")
        if result.get("microphone"):
            capabilities.append("microphone")
        return ProviderDescriptor(
            id=self.id,
            version=str(result.get("version") or "1"),
            status="degraded" if required_denied else "supported",
            modes=("current_desktop",),
            displays=displays,
            capabilities=tuple(capabilities),
            diagnostics=tuple(diagnostics),
            display_server=str(result.get("display_server") or ""),
            audio_backend=str(result.get("audio_backend") or ""),
            secure_surface=bool(result.get("secure_surface")),
        )

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
        viewport: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if mode != "current_desktop":
            return {
                "ok": False,
                "code": "desktop_mode_unsupported",
                "error": "The current-desktop Provider cannot create a remote login session.",
            }
        return await electron_desktop_rpc(
            "negotiate",
            {
                "session_id": session_id,
                "offer": offer,
                "display_id": display_id,
                "quality_mode": quality_mode,
                "ice_servers": ice_servers,
                "permissions": dict(permissions or {}),
                "viewport": dict(viewport or {}),
            },
            timeout=60,
        )

    async def disconnect(self, session_id: str) -> None:
        await electron_desktop_rpc("disconnect", {"session_id": session_id}, timeout=10)

    async def list_displays(self, session_id: str) -> list[DisplayDescriptor]:
        result = await electron_desktop_rpc("displays", {"session_id": session_id}, timeout=10)
        return [
            DisplayDescriptor(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or "Display"),
                width=max(1, int(item.get("width") or 1)),
                height=max(1, int(item.get("height") or 1)),
                scale=max(0.1, float(item.get("scale") or 1)),
                rotation=int(item.get("rotation") or 0),
                primary=bool(item.get("primary")),
                kind=str(item.get("kind") or "physical"),
            )
            for item in result.get("displays") or ()
            if isinstance(item, dict) and str(item.get("id") or "")
        ]

    async def select_display(self, session_id: str, display_id: str) -> None:
        result = await electron_desktop_rpc(
            "select_display", {"session_id": session_id, "display_id": display_id}
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_display_select_failed"))

    async def set_quality(self, session_id: str, mode: QualityMode) -> None:
        result = await electron_desktop_rpc(
            "set_quality", {"session_id": session_id, "quality_mode": mode}
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_quality_failed"))

    async def set_microphone(self, session_id: str, enabled: bool) -> None:
        # The controller owns microphone capture.  The controlled Provider only
        # records the state so it can stop accepting the upstream track on mute.
        result = await electron_desktop_rpc(
            "set_microphone", {"session_id": session_id, "enabled": bool(enabled)}
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_microphone_failed"))

    async def security_state(self, session_id: str) -> dict[str, Any]:
        result = await electron_desktop_rpc(
            "security_state", {"session_id": session_id}, timeout=5
        )
        if result.get("ok") is False:
            raise RuntimeError(
                str(result.get("code") or "desktop_security_state_unavailable")
            )
        return {
            "secure_surface": bool(result.get("secure_surface")),
            "security_epoch": max(0, int(result.get("security_epoch") or 0)),
        }

    async def apply_clipboard_image(self, session_id: str, path: str) -> None:
        result = await electron_desktop_rpc(
            "write_clipboard_image", {"session_id": session_id, "path": str(path)}
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_clipboard_image_failed"))

    async def export_clipboard_image(
        self, session_id: str, offer_id: str, path: str
    ) -> dict[str, Any]:
        result = await electron_desktop_rpc(
            "export_clipboard_image",
            {"session_id": session_id, "offer_id": offer_id, "path": str(path)},
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_clipboard_offer_not_found"))
        return result

    async def acknowledge_clipboard_image(self, session_id: str, offer_id: str) -> None:
        await electron_desktop_rpc(
            "ack_clipboard_image",
            {"session_id": session_id, "offer_id": offer_id},
        )

    async def apply_clipboard_files(self, session_id: str, paths: list[str]) -> None:
        result = await electron_desktop_rpc(
            "write_clipboard_files",
            {"session_id": session_id, "paths": [str(item) for item in paths]},
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_clipboard_file_failed"))

    async def export_clipboard_files(
        self, session_id: str, offer_id: str, path: str
    ) -> dict[str, Any]:
        result = await electron_desktop_rpc(
            "export_clipboard_files",
            {"session_id": session_id, "offer_id": offer_id, "path": str(path)},
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_clipboard_offer_not_found"))
        return result

    async def acknowledge_clipboard_files(self, session_id: str, offer_id: str) -> None:
        await electron_desktop_rpc(
            "ack_clipboard_files",
            {"session_id": session_id, "offer_id": offer_id},
        )


async def _select_rdp_target(
    system: str,
) -> tuple[tuple[str, int, str, str] | None, dict[str, Any] | None]:
    try:
        targets = await _probe_rdp_targets(system)
    except ValueError:
        return None, {
            "ok": False,
            "code": "rdp_port_invalid",
            "error": "CYRENE_RDP_PORT must be a decimal TCP port between 1 and 65535.",
        }
    selected = next((item for item in targets if item[3] == "rdp"), None)
    if selected is None:
        selected = next(
            (item for item in targets if item[3] == "non_rdp"),
            targets[0] if targets else None,
        )
    if selected is None:
        return None, {
            "ok": False,
            "code": "rdp_service_missing",
            "error": "No supported local RDP service is installed.",
        }
    _backend, port, source, listener_state = selected
    if listener_state == "non_rdp":
        return None, {
            "ok": False,
            "code": "rdp_port_occupied_by_other_service",
            "error": f"Port {port} is occupied, but the listener is not a usable RDP service.",
            "rdp_port": port,
            "rdp_port_source": source,
        }
    if listener_state != "rdp":
        return None, {
            "ok": False,
            "code": "rdp_service_not_listening",
            "error": f"No local RDP service is listening on port {port}.",
            "rdp_port": port,
            "rdp_port_source": source,
        }
    return selected, None


def _freerdp_connection_arguments(
    *,
    session_id: str,
    rdp_port: int,
    rdp_port_source: str,
    offer: dict[str, Any],
    display_id: str,
    quality_mode: QualityMode,
    ice_servers: list[dict[str, Any]],
    permissions: dict[str, bool] | None,
    credentials: dict[str, str],
    viewport: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_viewport = viewport if isinstance(viewport, dict) else {}
    pixel_ratio = max(0.5, min(2.0, float(raw_viewport.get("device_pixel_ratio") or 1)))
    width = max(320, min(3840, round(float(raw_viewport.get("width") or 1920))))
    height = max(240, min(2160, round(float(raw_viewport.get("height") or 1080))))
    return {
        "session_id": session_id,
        "target": {"host": "127.0.0.1", "port": rdp_port, "source": rdp_port_source},
        "local_bind": {"host": "127.0.0.1", "port": 0},
        "offer": offer,
        "display_id": display_id,
        "quality_mode": quality_mode,
        "ice_servers": ice_servers,
        "permissions": dict(permissions or {}),
        "viewport": {
            "width": width,
            "height": height,
            "device_pixel_ratio": pixel_ratio,
        },
        "credentials": {
            "username": str(credentials.get("username") or ""),
            "domain": str(credentials.get("domain") or ""),
            "password": str(credentials.get("password") or ""),
        },
        "rdp": {
            "tls": True,
            "nla": True,
            "verify_certificate": True,
            "audio_output": True,
            "audio_input": True,
            "dynamic_resolution": True,
            "drive_redirection": False,
        },
    }


class FreeRdpProvider:
    """Probe and launch contract for the signed FreeRDP controller sidecar.

    Cyrene distributions may bundle ``cyrene-freerdp-sidecar``.  Development
    checkouts also accept the executable on PATH.  It speaks the same JSON RPC
    contract as the Electron capture host and never exposes a public RDP port.
    """

    id = "freerdp-sidecar"

    def __init__(self) -> None:
        configured = str(os.environ.get("CYRENE_FREERDP_SIDECAR") or "").strip()
        bundled_root = str(os.environ.get("CYRENE_INSTALL_RESOURCES_DIR") or "").strip()
        bundled_name = "cyrene-freerdp-sidecar.exe" if platform.system().lower() == "windows" else "cyrene-freerdp-sidecar"
        bundled_candidates = (
            os.path.join(bundled_root, "remote-desktop", bundled_name),
            os.path.join(bundled_root, "x64-sidecars", bundled_name),
        ) if bundled_root else ()
        self.binary = next(
            (
                candidate
                for candidate in (
                    configured,
                    *bundled_candidates,
                    shutil.which("cyrene-freerdp-sidecar") or "",
                )
                if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK)
            ),
            "",
        )
        electron_path = str(os.environ.get("CYRENE_ELECTRON_PATH") or "").strip()
        electron_root = str(os.environ.get("CYRENE_ELECTRON_RESOURCES_DIR") or "").strip()
        launcher = os.path.join(os.path.dirname(__file__), "freerdp_dev_sidecar.py")
        media_host = os.path.join(electron_root, "remote-desktop-rdp-sidecar.js") if electron_root else ""
        self._development_launcher = (
            launcher
            if platform.system().lower() == "linux"
            and os.environ.get("CYRENE_ELECTRON_DEV") == "1"
            and os.path.isfile(electron_path)
            and os.path.isfile(media_host)
            and os.path.isfile(launcher)
            and bool(shutil.which("xfreerdp3") or shutil.which("xfreerdp"))
            and bool(shutil.which("Xvfb"))
            and bool(shutil.which("xdotool"))
            else ""
        )
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _sidecar_command(self) -> tuple[str, ...]:
        if self.binary:
            return (self.binary,)
        if self._development_launcher:
            return (sys.executable, self._development_launcher)
        return ()

    async def _request(
        self,
        session_id: str,
        method: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 30,
    ) -> dict[str, Any]:
        process = self._processes.get(str(session_id))
        if process is None or process.returncode is not None or process.stdin is None or process.stdout is None:
            raise RuntimeError("freerdp_sidecar_session_unavailable")
        lock = self._locks.setdefault(str(session_id), asyncio.Lock())
        async with lock:
            message = json.dumps(
                {"version": 1, "method": str(method), "args": dict(arguments)},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            process.stdin.write(message)
            await process.stdin.drain()
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise RuntimeError("freerdp_sidecar_timeout") from exc
        if not raw:
            raise RuntimeError("freerdp_sidecar_stopped")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("freerdp_sidecar_invalid_result") from exc
        if not isinstance(result, dict):
            raise RuntimeError("freerdp_sidecar_invalid_result")
        return result

    async def _probe_remote_login_service(
        self, system: str
    ) -> tuple[str, int, str, list[dict[str, Any]]]:
        diagnostics: list[dict[str, Any]] = []
        rdp_backend = ""
        rdp_port = 0
        listener_state = "closed"
        targets: list[tuple[str, int, str, str]] = []
        candidates = _rdp_backend_candidates(system)
        if system in {"windows", "linux"}:
            if not candidates:
                diagnostics.append(
                    {
                        "code": "rdp_service_missing",
                        "severity": "error",
                        "message": (
                            "Enable Windows Remote Desktop on the controlled host."
                            if system == "windows"
                            else "Install GNOME Remote Desktop or xrdp/xorgxrdp on the controlled Linux host."
                        ),
                    }
                )
        else:
            diagnostics.append(
                {
                    "code": "remote_login_not_supported_on_macos",
                    "severity": "info",
                    "message": "macOS current-desktop control uses ScreenCaptureKit; RDP remote login is unavailable.",
                }
            )
        if candidates:
            try:
                targets = await _probe_rdp_targets(system)
            except ValueError:
                diagnostics.append(
                    {
                        "code": "rdp_port_invalid",
                        "severity": "error",
                        "message": "CYRENE_RDP_PORT must be a decimal TCP port between 1 and 65535.",
                    }
                )
            selected = next((item for item in targets if item[3] == "rdp"), None)
            if selected is None and targets:
                selected = next((item for item in targets if item[3] == "non_rdp"), targets[0])
            if selected is not None:
                rdp_backend, rdp_port, _rdp_port_source, listener_state = selected
            valid_listener_present = any(item[3] == "rdp" for item in targets)
            for backend, port, source, state in targets:
                diagnostics.append(
                    {
                        "code": (
                            "rdp_listener_detected"
                            if state == "rdp"
                            else "rdp_port_occupied_by_other_service"
                            if state == "non_rdp"
                            else "rdp_service_not_listening"
                        ),
                        "severity": (
                            "info" if state == "rdp" or valid_listener_present else "error"
                        ),
                        "message": (
                            f"A valid {backend} listener is present on port {port} ({source})."
                            if state == "rdp"
                            else f"Port {port} for {backend} ({source}) is occupied by a service that did not complete an RDP X.224 handshake."
                            if state == "non_rdp"
                            else f"No {backend} service is listening on port {port} ({source})."
                        ),
                    }
                )
        return rdp_backend, rdp_port, listener_state, diagnostics

    async def probe(self) -> ProviderDescriptor:
        system = platform.system().lower()
        rdp_backend, rdp_port, listener_state, diagnostics = (
            await self._probe_remote_login_service(system)
        )
        command = self._sidecar_command()
        if not command:
            diagnostics.append(
                {
                    "code": "freerdp_sidecar_missing",
                    "severity": "error",
                    "message": "The signed Cyrene FreeRDP sidecar is not installed.",
                }
            )
        supported = bool(command and rdp_backend and rdp_port and listener_state == "rdp")
        if self._development_launcher:
            diagnostics.append(
                {
                    "code": "freerdp_development_bridge",
                    "severity": "info",
                    "message": "The built-in Linux FreeRDP development bridge is available.",
                }
            )
        return ProviderDescriptor(
            id=self.id,
            version=(
                _command_version(self.binary, "--version")
                if self.binary
                else _command_version(shutil.which("xfreerdp3") or "xfreerdp", "/version")
                if command
                else ""
            ),
            status="supported" if supported else "unsupported",
            modes=("remote_login",) if supported else (),
            capabilities=(
                "video", "input", "system_audio", "microphone", "multi_monitor",
                "clipboard_text", "clipboard_image", "clipboard_file",
                "rdp_tls", "rdp_nla", "rdp_dynamic_resolution", "webrtc",
            ) if supported else (),
            diagnostics=tuple(diagnostics),
            display_server=rdp_backend,
        )

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
        viewport: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Credential material reaches the sidecar only through its inherited
        # stdin pipe; it never enters argv, environment variables, or files.
        command = self._sidecar_command()
        if mode != "remote_login" or not command:
            return {
                "ok": False,
                "code": "freerdp_sidecar_missing",
                "error": "The FreeRDP sidecar is unavailable.",
            }
        if not credentials or not str(credentials.get("username") or "") or not str(credentials.get("password") or ""):
            return {
                "ok": False,
                "code": "desktop_credentials_required",
                "error": "Remote login requires one-time credentials from the secure host dialog.",
                "credential_request": True,
            }
        selected, target_error = await _select_rdp_target(platform.system().lower())
        if target_error is not None:
            return target_error
        assert selected is not None
        _rdp_backend, rdp_port, rdp_port_source, _listener_state = selected
        process = await asyncio.create_subprocess_exec(
            *command,
            "--stdio-json",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=2_500_000,
        )
        self._processes[str(session_id)] = process
        self._locks[str(session_id)] = asyncio.Lock()
        try:
            result = await self._request(
                session_id,
                "connect",
                _freerdp_connection_arguments(
                    session_id=session_id,
                    rdp_port=rdp_port,
                    rdp_port_source=rdp_port_source,
                    offer=offer,
                    display_id=display_id,
                    quality_mode=quality_mode,
                    ice_servers=ice_servers,
                    permissions=permissions,
                    credentials=credentials,
                    viewport=viewport,
                ),
                timeout=75,
            )
        finally:
            # Strings cannot be zeroed by Python, so keep their lifetime to
            # this call and drop the only mapping reference immediately.
            for key in tuple(credentials):
                credentials[key] = ""
            credentials.clear()
        if result.get("ok") is False:
            result = _normalize_rdp_connect_error(result, rdp_port)
            await self.disconnect(session_id)
        return result

    async def disconnect(self, session_id: str) -> None:
        process = self._processes.get(str(session_id))
        if process is not None and process.returncode is None:
            try:
                await self._request(session_id, "disconnect", {}, timeout=5)
            except Exception:
                pass
        process = self._processes.pop(str(session_id), None)
        self._locks.pop(str(session_id), None)
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()

    def session_alive(self, session_id: str) -> bool:
        process = self._processes.get(str(session_id))
        return bool(process is not None and process.returncode is None)

    async def list_displays(self, session_id: str) -> list[DisplayDescriptor]:
        result = await self._request(session_id, "displays", {}, timeout=10)
        raw_displays = result.get("displays") if isinstance(result.get("displays"), list) else []
        displays = [
            DisplayDescriptor(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or "RDP display"),
                width=max(1, int(item.get("width") or 1)),
                height=max(1, int(item.get("height") or 1)),
                scale=max(0.1, float(item.get("scale") or 1)),
                rotation=int(item.get("rotation") or 0),
                primary=bool(item.get("primary")),
                kind="virtual",
            )
            for item in raw_displays
            if isinstance(item, dict) and str(item.get("id") or "")
        ]
        return displays or [DisplayDescriptor("rdp-display-1", "RDP display", 1920, 1080, primary=True, kind="virtual")]

    async def select_display(self, session_id: str, display_id: str) -> None:
        result = await self._request(session_id, "select_display", {"display_id": display_id})
        if result.get("ok") is False:
            raise ValueError(str(result.get("code") or "desktop_display_not_found"))

    async def set_quality(self, session_id: str, mode: QualityMode) -> None:
        result = await self._request(session_id, "set_quality", {"quality_mode": mode})
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_quality_failed"))

    async def set_microphone(self, session_id: str, enabled: bool) -> None:
        result = await self._request(session_id, "set_microphone", {"enabled": bool(enabled)})
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_microphone_failed"))

    async def security_state(self, session_id: str) -> dict[str, Any]:
        result = await self._request(session_id, "security_state", {}, timeout=5)
        if result.get("ok") is False:
            raise RuntimeError(
                str(result.get("code") or "desktop_security_state_unavailable")
            )
        return {
            "secure_surface": bool(result.get("secure_surface")),
            "security_epoch": max(0, int(result.get("security_epoch") or 0)),
        }

    async def apply_clipboard_image(self, session_id: str, path: str) -> None:
        result = await self._request(
            session_id,
            "write_clipboard_image",
            {"path": str(path)},
            timeout=15,
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_clipboard_image_failed"))

    async def export_clipboard_image(
        self, session_id: str, offer_id: str, path: str
    ) -> dict[str, Any]:
        result = await self._request(
            session_id,
            "export_clipboard_image",
            {"offer_id": offer_id, "path": str(path)},
            timeout=15,
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_clipboard_offer_not_found"))
        return result

    async def acknowledge_clipboard_image(self, session_id: str, offer_id: str) -> None:
        await self._request(
            session_id,
            "ack_clipboard_image",
            {"offer_id": offer_id},
            timeout=10,
        )

    async def apply_clipboard_files(self, session_id: str, paths: list[str]) -> None:
        result = await self._request(
            session_id,
            "write_clipboard_files",
            {"paths": [str(item) for item in paths]},
            timeout=15,
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_clipboard_file_failed"))

    async def export_clipboard_files(
        self, session_id: str, offer_id: str, path: str
    ) -> dict[str, Any]:
        result = await self._request(
            session_id,
            "export_clipboard_files",
            {"offer_id": offer_id, "path": str(path)},
            timeout=30,
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("code") or "desktop_clipboard_offer_not_found"))
        return result

    async def acknowledge_clipboard_files(self, session_id: str, offer_id: str) -> None:
        await self._request(
            session_id,
            "ack_clipboard_files",
            {"offer_id": offer_id},
            timeout=10,
        )


class ProviderManager:
    def __init__(self) -> None:
        self.providers: tuple[RemoteDesktopProvider, ...] = (
            ElectronCurrentDesktopProvider(),
            FreeRdpProvider(),
        )

    async def probe(self) -> list[ProviderDescriptor]:
        return list(await asyncio.gather(*(provider.probe() for provider in self.providers)))

    async def select(self, mode: DesktopMode) -> RemoteDesktopProvider:
        provider, _descriptor = await self.select_with_descriptor(mode)
        return provider

    async def select_with_descriptor(
        self, mode: DesktopMode
    ) -> tuple[RemoteDesktopProvider, ProviderDescriptor]:
        descriptors = await self.probe()
        for provider, descriptor in zip(self.providers, descriptors):
            if mode in descriptor.modes and descriptor.status in {"supported", "degraded"}:
                return provider, descriptor
        code = "desktop_provider_unavailable"
        if mode == "remote_login":
            rdp_descriptor = next(
                (item for item in descriptors if item.id == "freerdp-sidecar"),
                None,
            )
            error = next(
                (
                    item
                    for item in (rdp_descriptor.diagnostics if rdp_descriptor else ())
                    if str(item.get("severity") or "") == "error"
                ),
                None,
            )
            code = str(error.get("code") or "freerdp_sidecar_missing") if error else "freerdp_sidecar_missing"
        raise RuntimeError(code)

    def by_id(self, provider_id: str) -> RemoteDesktopProvider:
        provider = next((item for item in self.providers if item.id == provider_id), None)
        if provider is None:
            raise KeyError(provider_id)
        return provider


__all__ = [
    "ElectronCurrentDesktopProvider",
    "FreeRdpProvider",
    "ProviderManager",
]
