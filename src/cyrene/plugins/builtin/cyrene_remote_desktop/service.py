"""Consent, provider, and encrypted-command services for Remote Desktop."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from cyrene.core.plugin import application_plugin_service


VIEW_CAPABILITY = "remote_desktop:view"
CONTROL_CAPABILITY = "remote_desktop:control"
LOGIN_CAPABILITY = "remote_desktop:login"
REMOTE_DESKTOP_CAPABILITIES = frozenset({
    VIEW_CAPABILITY,
    CONTROL_CAPABILITY,
    LOGIN_CAPABILITY,
})

_MAX_FRAME_EDGE = 1440
_MAX_TEXT_LENGTH = 4096
_MAX_SESSION_SECONDS = 15 * 60
_LOGIN_KEYS = frozenset({
    "Enter", "Tab", "Escape", "Backspace", "Delete", "Home", "End",
    "PageUp", "PageDown", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
})


class RemoteDesktopError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> float:
    return time.monotonic()


def _clean_permissions(values: Any) -> frozenset[str]:
    allowed = {"view", "control", "login"}
    result = {str(item or "").strip() for item in (values or [])}
    if not result or not result <= allowed:
        raise RemoteDesktopError("invalid_permissions", "permissions must contain view, control, or login")
    if "control" in result or "login" in result:
        result.add("view")
    return frozenset(result)


def _public_target(target: Any) -> dict[str, Any]:
    value = dict(target or {})
    return {
        key: value.get(key)
        for key in (
            "target_id", "targetId", "app_name", "appName", "title", "platform",
            "foreground", "minimized", "bounds",
        )
        if value.get(key) is not None
    }


@dataclass(slots=True)
class ConsentLease:
    peer_device_id: str
    permissions: frozenset[str]
    expires_at: float

    def public(self) -> dict[str, Any]:
        return {
            "peer_device_id": self.peer_device_id,
            "permissions": sorted(self.permissions),
            "remaining_seconds": max(0, round(self.expires_at - _now())),
        }


@dataclass(slots=True)
class DesktopSession:
    session_id: str
    peer_device_id: str
    provider: str
    provider_session_id: str
    mode: str
    target: dict[str, Any]
    expires_at: float
    mapping: dict[str, Any] = field(default_factory=dict)


class ElectronWindowProvider:
    id = "user_session"

    @staticmethod
    def available() -> bool:
        return bool(
            str(os.environ.get("CYRENE_ELECTRON_RPC_PORT") or "").strip()
            and str(os.environ.get("CYRENE_ELECTRON_RPC_TOKEN") or "").strip()
        )

    async def _rpc(self, method: str, args: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
        port = str(os.environ.get("CYRENE_ELECTRON_RPC_PORT") or "").strip()
        token = str(os.environ.get("CYRENE_ELECTRON_RPC_TOKEN") or "").strip()
        if not port or not token:
            raise RemoteDesktopError("desktop_host_unavailable", "Cyrene Electron desktop host is unavailable")
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                response = await client.post(
                    f"http://127.0.0.1:{port}/app/rpc",
                    headers={"X-Cyrene-Token": token},
                    json={"method": method, "args": args},
                )
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RemoteDesktopError("desktop_host_error", "Desktop host request failed") from exc
        if not isinstance(result, dict):
            raise RemoteDesktopError("desktop_host_error", "Desktop host returned an invalid response")
        if result.get("status") == "error":
            raise RemoteDesktopError(str(result.get("type") or "provider_error"), str(result.get("message") or "Desktop provider failed"))
        return result

    async def targets(self) -> list[dict[str, Any]]:
        result = await self._rpc("list_targets", {})
        return [_public_target(item) for item in result.get("targets") or []]

    async def open(self, target_id: str, mode: str) -> tuple[str, dict[str, Any]]:
        if mode == "login":
            raise RemoteDesktopError("login_provider_required", "System login requires the privileged companion provider")
        result = await self._rpc("connect", {
            "target_id": str(target_id or ""),
            "parameters": {"mode": "visual", "focus_policy": "when_required"},
        })
        return str(result.get("session_id") or ""), _public_target(result.get("target"))

    async def frame(self, session_id: str) -> dict[str, Any]:
        return await self._rpc("call", {
            "session_id": session_id,
            "capability": "visual_describe",
            "parameters": {},
        })

    async def input(self, session_id: str, event: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
        kind = str(event.get("type") or "")
        logical_width = float(mapping.get("logical_width") or 0)
        logical_height = float(mapping.get("logical_height") or 0)

        def point() -> tuple[float, float]:
            if logical_width <= 0 or logical_height <= 0:
                raise RemoteDesktopError("frame_required", "A fresh frame is required before pointer input")
            try:
                x = float(event.get("x"))
                y = float(event.get("y"))
            except (TypeError, ValueError) as exc:
                raise RemoteDesktopError("invalid_input", "Pointer coordinates are invalid") from exc
            if not 0 <= x <= 1 or not 0 <= y <= 1:
                raise RemoteDesktopError("invalid_input", "Pointer coordinates must be normalized")
            return x * logical_width, y * logical_height

        capability = ""
        parameters: dict[str, Any] = {"allow_foreground_input": True}
        if kind in {"click", "double_click", "right_click"}:
            capability = {"click": "click_at", "double_click": "double_click", "right_click": "right_click"}[kind]
            parameters.update(zip(("x", "y"), point()))
            parameters["coordinate_space"] = "window"
        elif kind == "scroll":
            capability = "scroll_at"
            parameters.update(zip(("x", "y"), point()))
            parameters.update({
                "coordinate_space": "window",
                "direction": str(event.get("direction") or "down"),
                "amount": max(1, min(int(event.get("amount") or 30), 240)),
            })
        elif kind == "shortcut":
            keys = [str(item) for item in event.get("keys") or [] if str(item)]
            if not keys or len(keys) > 8:
                raise RemoteDesktopError("invalid_input", "Shortcut keys are invalid")
            capability = "key_sequence"
            parameters["steps"] = [{"type": "shortcut", "keys": keys}]
        elif kind == "key":
            key = str(event.get("key") or "")
            if not key or len(key) > 40:
                raise RemoteDesktopError("invalid_input", "Key is invalid")
            capability = "key_sequence"
            parameters["steps"] = [{"type": "key", "key": key}]
        elif kind == "text":
            text = str(event.get("text") or "")
            if not text or len(text) > _MAX_TEXT_LENGTH:
                raise RemoteDesktopError("invalid_input", "Text input is empty or too long")
            capability = "key_sequence"
            parameters["steps"] = [{"type": "text", "text": text}]
        else:
            raise RemoteDesktopError("invalid_input", "Unsupported input event")
        return await self._rpc("call", {
            "session_id": session_id,
            "capability": capability,
            "parameters": parameters,
        })

    async def close(self, session_id: str) -> None:
        await self._rpc("disconnect", {"session_id": session_id})


class CompanionProvider:
    """Adapter for an optional privileged, loopback-only login-screen helper."""

    id = "system_login"

    def __init__(self) -> None:
        self.url = str(os.environ.get("CYRENE_REMOTE_DESKTOP_COMPANION_URL") or "").strip().rstrip("/")
        self.token = str(os.environ.get("CYRENE_REMOTE_DESKTOP_COMPANION_TOKEN") or "").strip()

    def available(self) -> bool:
        parsed = urlparse(self.url)
        return bool(
            self.token
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )

    async def _rpc(self, method: str, args: dict[str, Any]) -> dict[str, Any]:
        if not self.available():
            raise RemoteDesktopError("login_provider_unavailable", "The privileged login companion is not configured")
        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                response = await client.post(
                    self.url + "/rpc",
                    headers={"X-Cyrene-Remote-Desktop-Token": self.token},
                    json={"method": method, "args": args},
                )
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RemoteDesktopError("login_provider_error", "The privileged login companion failed") from exc
        if not isinstance(result, dict):
            raise RemoteDesktopError("login_provider_error", "The privileged login companion returned an invalid response")
        if result.get("ok") is False:
            raise RemoteDesktopError(str(result.get("code") or "login_provider_error"), "The privileged login companion rejected the request")
        return result

    async def targets(self) -> list[dict[str, Any]]:
        result = await self._rpc("targets", {})
        return [_public_target(item) for item in result.get("targets") or []]

    async def open(self, target_id: str, mode: str) -> tuple[str, dict[str, Any]]:
        if mode != "login":
            raise RemoteDesktopError("invalid_mode", "The login provider only accepts login sessions")
        result = await self._rpc("login.begin", {"target_id": target_id})
        return str(result.get("session_id") or ""), _public_target(result.get("target"))

    async def frame(self, session_id: str) -> dict[str, Any]:
        return await self._rpc("frame", {"session_id": session_id})

    async def input(self, session_id: str, event: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
        if str(event.get("type") or "") == "text":
            raise RemoteDesktopError("credential_input_blocked", "Text and passwords are never relayed to the system login provider")
        return await self._rpc("input", {"session_id": session_id, "event": event})

    async def close(self, session_id: str) -> None:
        await self._rpc("close", {"session_id": session_id})


class RemoteDesktopService:
    def __init__(self) -> None:
        self.providers = {
            "user_session": ElectronWindowProvider(),
            "system_login": CompanionProvider(),
        }
        self.leases: dict[str, ConsentLease] = {}
        self.sessions: dict[str, DesktopSession] = {}
        self._lock = asyncio.Lock()

    def _remote(self) -> Any:
        remote = application_plugin_service("remote")
        if remote is None or getattr(remote, "runtime", None) is None:
            raise RemoteDesktopError("remote_unavailable", "Cyrene Remote is unavailable")
        return remote

    def _purge(self) -> None:
        now = _now()
        self.leases = {key: value for key, value in self.leases.items() if value.expires_at > now}
        expired = [item for item in self.sessions.values() if item.expires_at <= now]
        self.sessions = {key: value for key, value in self.sessions.items() if value.expires_at > now}
        for session in expired:
            try:
                asyncio.get_running_loop().create_task(
                    self.providers[session.provider].close(session.provider_session_id)
                )
            except RuntimeError:
                pass

    def _provider_status(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "user_session",
                "available": self.providers["user_session"].available(),
                "modes": ["view", "control"],
                "scope": "current_user_session",
            },
            {
                "id": "system_login",
                "available": self.providers["system_login"].available(),
                "modes": ["login"],
                "scope": "lock_and_login_screen",
                "requires": "CYRENE_REMOTE_DESKTOP_COMPANION_URL and CYRENE_REMOTE_DESKTOP_COMPANION_TOKEN",
            },
        ]

    async def local_status(self) -> dict[str, Any]:
        self._purge()
        remote = self._remote()
        peers = await asyncio.to_thread(remote.store.list_peers)
        return {
            "ok": True,
            "device_id": remote.store.identity.device_id,
            "peers": [{
                "device_id": item.get("device_id"),
                "display_name": item.get("display_name"),
                "granted_capabilities": item.get("granted_capabilities") or [],
                "received_capabilities": item.get("received_capabilities") or [],
            } for item in peers],
            "leases": [item.public() for item in self.leases.values()],
            "providers": self._provider_status(),
        }

    async def approve(self, peer_device_id: str, permissions: Any, ttl_seconds: int) -> dict[str, Any]:
        remote = self._remote()
        peer = await asyncio.to_thread(remote.store.get_peer, str(peer_device_id or ""))
        if peer is None:
            raise RemoteDesktopError("peer_not_trusted", "The selected device is not paired")
        ttl = max(30, min(int(ttl_seconds or 600), _MAX_SESSION_SECONDS))
        approved = _clean_permissions(permissions)
        lease = ConsentLease(str(peer_device_id), approved, _now() + ttl)
        capability_by_permission = {
            "view": VIEW_CAPABILITY,
            "control": CONTROL_CAPABILITY,
            "login": LOGIN_CAPABILITY,
        }
        # The local button is also the explicit persistent peer-grant ceremony.
        # The short lease below remains mandatory, so this does not create
        # unattended desktop access after it expires or after a restart.
        granted = set(peer.get("granted_capabilities") or [])
        granted.update(capability_by_permission[item] for item in approved)
        await remote.service.update_grant(str(peer_device_id), {
            "capabilities": sorted(granted),
            "project_scopes": list(peer.get("granted_project_scopes") or []),
        })
        async with self._lock:
            self.leases[lease.peer_device_id] = lease
        remote.store.audit(
            "remote_desktop_consent_granted",
            peer_device_id=lease.peer_device_id,
            outcome="granted",
            detail={"permissions": sorted(lease.permissions), "ttl_seconds": ttl},
        )
        return {"ok": True, "lease": lease.public()}

    async def revoke(self, peer_device_id: str) -> dict[str, Any]:
        peer_device_id = str(peer_device_id or "")
        async with self._lock:
            self.leases.pop(peer_device_id, None)
            closing = [item for item in self.sessions.values() if item.peer_device_id == peer_device_id]
            for item in closing:
                self.sessions.pop(item.session_id, None)
        for item in closing:
            try:
                await self.providers[item.provider].close(item.provider_session_id)
            except Exception:
                pass
        self._remote().store.audit(
            "remote_desktop_consent_revoked",
            peer_device_id=peer_device_id,
            outcome="revoked",
        )
        return {"ok": True, "closed_sessions": len(closing)}

    def _require_lease(self, peer_device_id: str, permission: str) -> ConsentLease:
        self._purge()
        lease = self.leases.get(peer_device_id)
        if lease is None or permission not in lease.permissions:
            raise RemoteDesktopError("remote_target_approval_required", "The target device has not granted a current consent lease")
        return lease

    async def _targets(self, peer_device_id: str, provider_id: str) -> dict[str, Any]:
        self._require_lease(peer_device_id, "view")
        provider = self.providers.get(provider_id)
        if provider is None or not provider.available():
            raise RemoteDesktopError("provider_unavailable", "The requested desktop provider is unavailable")
        return {"ok": True, "provider": provider_id, "targets": await provider.targets()}

    async def _open(self, peer_device_id: str, provider_id: str, target_id: str, mode: str) -> dict[str, Any]:
        if mode not in {"view", "control", "login"}:
            raise RemoteDesktopError("invalid_mode", "Desktop mode must be view, control, or login")
        permission = "login" if mode == "login" else mode
        lease = self._require_lease(peer_device_id, permission)
        provider = self.providers.get(provider_id)
        if provider is None or not provider.available():
            raise RemoteDesktopError("provider_unavailable", "The requested desktop provider is unavailable")
        provider_session_id, target = await provider.open(target_id, mode)
        if not provider_session_id:
            raise RemoteDesktopError("provider_error", "Desktop provider did not create a session")
        session = DesktopSession(
            session_id="rd_" + secrets.token_urlsafe(24),
            peer_device_id=peer_device_id,
            provider=provider_id,
            provider_session_id=provider_session_id,
            mode=mode,
            target=target,
            expires_at=min(lease.expires_at, _now() + _MAX_SESSION_SECONDS),
        )
        async with self._lock:
            self.sessions[session.session_id] = session
        return {
            "ok": True,
            "session_id": session.session_id,
            "mode": mode,
            "provider": provider_id,
            "target": target,
            "remaining_seconds": round(session.expires_at - _now()),
        }

    def _session(self, peer_device_id: str, session_id: str, *, input_required: bool = False) -> DesktopSession:
        self._purge()
        session = self.sessions.get(str(session_id or ""))
        if session is None or session.peer_device_id != peer_device_id:
            raise RemoteDesktopError("desktop_session_not_found", "The desktop session is unavailable or expired")
        permission = "login" if session.mode == "login" else ("control" if input_required else "view")
        self._require_lease(peer_device_id, permission)
        if input_required and session.mode == "view":
            raise RemoteDesktopError("remote_permission_denied", "This is a view-only session")
        return session

    @staticmethod
    def _encode_frame(result: dict[str, Any]) -> tuple[str, str, int, int]:
        encoded = str(result.get("image_base64") or result.get("data") or "")
        if not encoded:
            raise RemoteDesktopError("frame_unavailable", "The desktop provider returned no frame")
        try:
            image = Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))).convert("RGB")
            image.thumbnail((_MAX_FRAME_EDGE, _MAX_FRAME_EDGE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=72, optimize=True)
        except Exception as exc:
            raise RemoteDesktopError("invalid_frame", "The desktop provider returned an invalid frame") from exc
        return base64.b64encode(output.getvalue()).decode("ascii"), "image/jpeg", image.width, image.height

    async def _frame(self, peer_device_id: str, session_id: str) -> dict[str, Any]:
        session = self._session(peer_device_id, session_id)
        result = await self.providers[session.provider].frame(session.provider_session_id)
        data, mime_type, width, height = await asyncio.to_thread(self._encode_frame, result)
        mapping = dict(result.get("coordinate_mapping") or {})
        if not mapping:
            mapping = {
                "logical_width": result.get("width") or width,
                "logical_height": result.get("height") or height,
            }
        session.mapping = mapping
        return {
            "ok": True,
            "session_id": session.session_id,
            "mime_type": mime_type,
            "image_base64": data,
            "width": width,
            "height": height,
            "remaining_seconds": round(session.expires_at - _now()),
        }

    @staticmethod
    def _sanitize_input(event: dict[str, Any], *, login: bool) -> dict[str, Any]:
        kind = str(event.get("type") or "")
        if kind in {"click", "double_click", "right_click", "scroll"}:
            try:
                x, y = float(event.get("x")), float(event.get("y"))
            except (TypeError, ValueError) as exc:
                raise RemoteDesktopError("invalid_input", "Pointer coordinates are invalid") from exc
            if not 0 <= x <= 1 or not 0 <= y <= 1:
                raise RemoteDesktopError("invalid_input", "Pointer coordinates must be normalized")
            clean: dict[str, Any] = {"type": kind, "x": x, "y": y}
            if kind == "scroll":
                direction = str(event.get("direction") or "down")
                if direction not in {"up", "down", "left", "right"}:
                    raise RemoteDesktopError("invalid_input", "Scroll direction is invalid")
                clean.update({
                    "direction": direction,
                    "amount": max(1, min(int(event.get("amount") or 30), 240)),
                })
            return clean
        if kind == "text":
            if login:
                raise RemoteDesktopError("credential_input_blocked", "Passwords and text are never relayed to the system login screen")
            text = str(event.get("text") or "")
            if not text or len(text) > _MAX_TEXT_LENGTH:
                raise RemoteDesktopError("invalid_input", "Text input is empty or too long")
            return {"type": "text", "text": text}
        if kind == "key":
            key = str(event.get("key") or "")
            if not key or len(key) > 40:
                raise RemoteDesktopError("invalid_input", "Key is invalid")
            if login and key not in _LOGIN_KEYS:
                raise RemoteDesktopError("credential_input_blocked", "Printable keys are not relayed to the system login screen")
            return {"type": "key", "key": key}
        if kind == "shortcut":
            if login:
                raise RemoteDesktopError("credential_input_blocked", "Keyboard shortcuts are not relayed to the system login screen")
            keys = [str(item) for item in event.get("keys") or [] if str(item)]
            if not keys or len(keys) > 8 or any(len(item) > 40 for item in keys):
                raise RemoteDesktopError("invalid_input", "Shortcut keys are invalid")
            return {"type": "shortcut", "keys": keys}
        raise RemoteDesktopError("invalid_input", "Unsupported input event")

    async def _input(
        self,
        peer_device_id: str,
        session_id: str,
        event: dict[str, Any],
        *,
        login_channel: bool,
    ) -> dict[str, Any]:
        session = self._session(peer_device_id, session_id, input_required=True)
        if login_channel != (session.mode == "login"):
            raise RemoteDesktopError("remote_permission_denied", "The input channel does not match the desktop session mode")
        clean_event = self._sanitize_input(dict(event or {}), login=login_channel)
        result = await self.providers[session.provider].input(session.provider_session_id, clean_event, session.mapping)
        return {"ok": True, "status": result.get("status", "success")}

    async def _close(self, peer_device_id: str, session_id: str) -> dict[str, Any]:
        session = self._session(peer_device_id, session_id)
        async with self._lock:
            self.sessions.pop(session.session_id, None)
        await self.providers[session.provider].close(session.provider_session_id)
        return {"ok": True}

    async def handle_remote(self, peer_device_id: str, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if command == "desktop.status":
                self._require_lease(peer_device_id, "view")
                return {"ok": True, "providers": self._provider_status()}
            if command == "desktop.targets":
                return await self._targets(peer_device_id, str(payload.get("provider") or "user_session"))
            if command.startswith("desktop.session.open_"):
                mode = command.removeprefix("desktop.session.open_")
                provider = str(payload.get("provider") or ("system_login" if mode == "login" else "user_session"))
                return await self._open(peer_device_id, provider, str(payload.get("target_id") or ""), mode)
            if command == "desktop.frame.read":
                return await self._frame(peer_device_id, str(payload.get("session_id") or ""))
            if command in {"desktop.input.send", "desktop.login.input"}:
                return await self._input(
                    peer_device_id,
                    str(payload.get("session_id") or ""),
                    dict(payload.get("event") or {}),
                    login_channel=command == "desktop.login.input",
                )
            if command == "desktop.session.close":
                return await self._close(peer_device_id, str(payload.get("session_id") or ""))
            raise RemoteDesktopError("remote_command_unsupported", "Unsupported remote desktop command")
        except RemoteDesktopError as exc:
            return {"ok": False, "code": exc.code, "error": str(exc)}

    async def controller_request(self, arguments: Any) -> dict[str, Any]:
        args = dict(arguments or {})
        operation = str(args.get("operation") or "status")
        if operation == "status":
            return await self.local_status()
        if operation == "approve":
            return await self.approve(str(args.get("peer_device_id") or ""), args.get("permissions"), int(args.get("ttl_seconds") or 600))
        if operation == "revoke":
            return await self.revoke(str(args.get("peer_device_id") or ""))
        device_id = str(args.get("device_id") or "")
        if not device_id:
            raise RemoteDesktopError("device_required", "Select a paired device")
        remote = self._remote()
        gateway = remote.runtime.gateway
        command = {
            "targets": "desktop.targets",
            "open_view": "desktop.session.open_view",
            "open_control": "desktop.session.open_control",
            "open_login": "desktop.session.open_login",
            "frame": "desktop.frame.read",
            "input": "desktop.input.send",
            "login_input": "desktop.login.input",
            "close": "desktop.session.close",
        }.get(operation)
        if command is None:
            raise RemoteDesktopError("invalid_operation", "Unsupported Remote Desktop operation")
        payload = {key: value for key, value in args.items() if key not in {"operation", "device_id"}}
        idempotency_key = ""
        if operation in {"open_view", "open_control", "open_login", "input", "login_input", "close"}:
            idempotency_key = "rd_" + secrets.token_hex(16)
        try:
            return await gateway.request(
                device_id,
                command=command,
                payload=payload,
                idempotency_key=idempotency_key,
                timeout=25,
            )
        except (ConnectionError, PermissionError, RuntimeError, TimeoutError) as exc:
            raise RemoteDesktopError("remote_request_failed", str(exc)) from exc

    async def shutdown(self) -> None:
        sessions = list(self.sessions.values())
        self.sessions.clear()
        self.leases.clear()
        for session in sessions:
            try:
                await self.providers[session.provider].close(session.provider_session_id)
            except Exception:
                pass


__all__ = [
    "CONTROL_CAPABILITY",
    "LOGIN_CAPABILITY",
    "REMOTE_DESKTOP_CAPABILITIES",
    "RemoteDesktopError",
    "RemoteDesktopService",
    "VIEW_CAPABILITY",
]
