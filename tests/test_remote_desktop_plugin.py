"""Focused tests for the Remote Desktop Plugin security boundary."""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from cyrene.plugins.builtin.cyrene_remote.control import (
    DEFAULT_REMOTE_CAPABILITIES,
    REMOTE_CAPABILITIES,
)
from cyrene.plugins.builtin.cyrene_remote import control as remote_control
from cyrene.plugins.builtin.cyrene_remote_desktop import service as desktop_module
from cyrene.plugins.builtin.cyrene_remote_desktop.service import (
    CONTROL_CAPABILITY,
    LOGIN_CAPABILITY,
    RemoteDesktopService,
    VIEW_CAPABILITY,
)


class FakeStore:
    def __init__(self) -> None:
        self.identity = SimpleNamespace(device_id="target-device")
        self.peer = {
            "device_id": "controller-device",
            "display_name": "Controller",
            "granted_capabilities": ["chat:read"],
            "received_capabilities": [],
            "granted_project_scopes": ["project-1"],
        }
        self.audit_events = []

    def get_peer(self, device_id):
        return dict(self.peer) if device_id == self.peer["device_id"] else None

    def list_peers(self):
        return [dict(self.peer)]

    def audit(self, event_type, **values):
        self.audit_events.append((event_type, values))


class FakeRemoteApplication:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    async def update_grant(self, device_id, values):
        assert device_id == self.store.peer["device_id"]
        self.store.peer["granted_capabilities"] = list(values["capabilities"])
        self.store.peer["granted_project_scopes"] = list(values["project_scopes"])
        return {"peer": dict(self.store.peer)}


class FakeProvider:
    id = "user_session"

    def __init__(self) -> None:
        self.inputs = []
        self.closed = []

    @staticmethod
    def available():
        return True

    async def targets(self):
        return [{"target_id": "window-1", "app_name": "Editor", "title": "Document"}]

    async def open(self, target_id, mode):
        assert target_id == "window-1"
        return "provider-session", {"target_id": target_id, "title": "Document"}

    async def frame(self, session_id):
        assert session_id == "provider-session"
        image = Image.new("RGB", (100, 50), "#15996f")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return {
            "image_base64": base64.b64encode(output.getvalue()).decode("ascii"),
            "coordinate_mapping": {"logical_width": 200, "logical_height": 100},
        }

    async def input(self, session_id, event, mapping):
        self.inputs.append((session_id, event, mapping))
        return {"status": "success"}

    async def close(self, session_id):
        self.closed.append(session_id)


def fake_remote():
    store = FakeStore()
    application = FakeRemoteApplication(store)
    return SimpleNamespace(
        store=store,
        service=application,
        runtime=SimpleNamespace(gateway=object()),
    )


def test_sensitive_capabilities_are_supported_but_not_default():
    assert {VIEW_CAPABILITY, CONTROL_CAPABILITY, LOGIN_CAPABILITY} <= REMOTE_CAPABILITIES
    assert not {VIEW_CAPABILITY, CONTROL_CAPABILITY, LOGIN_CAPABILITY} & set(DEFAULT_REMOTE_CAPABILITIES)
    assert remote_control._COMMAND_CAPABILITIES["desktop.session.open_view"] == VIEW_CAPABILITY
    assert remote_control._COMMAND_CAPABILITIES["desktop.session.open_control"] == CONTROL_CAPABILITY
    assert remote_control._COMMAND_CAPABILITIES["desktop.session.open_login"] == LOGIN_CAPABILITY
    assert not set(remote_control._COMMAND_CAPABILITIES).intersection(
        remote_control._PROJECT_SCOPED_COMMANDS
    ).intersection({"desktop.status", "desktop.targets", "desktop.frame.read"})


@pytest.mark.asyncio
async def test_target_consent_gates_frames_and_control(monkeypatch):
    remote = fake_remote()
    monkeypatch.setattr(desktop_module, "application_plugin_service", lambda name: remote if name == "remote" else None)
    service = RemoteDesktopService()
    provider = FakeProvider()
    service.providers["user_session"] = provider

    denied = await service.handle_remote("controller-device", "desktop.targets", {"provider": "user_session"})
    assert denied["code"] == "remote_target_approval_required"

    approved = await service.approve("controller-device", ["control"], 120)
    assert approved["lease"]["permissions"] == ["control", "view"]
    assert {VIEW_CAPABILITY, CONTROL_CAPABILITY} <= set(remote.store.peer["granted_capabilities"])

    targets = await service.handle_remote("controller-device", "desktop.targets", {"provider": "user_session"})
    assert targets["targets"][0]["target_id"] == "window-1"
    opened = await service.handle_remote("controller-device", "desktop.session.open_control", {
        "provider": "user_session",
        "target_id": "window-1",
    })
    frame = await service.handle_remote("controller-device", "desktop.frame.read", {
        "session_id": opened["session_id"],
    })
    assert frame["mime_type"] == "image/jpeg"
    assert (frame["width"], frame["height"]) == (100, 50)
    Image.open(io.BytesIO(base64.b64decode(frame["image_base64"]))).verify()

    sent = await service.handle_remote("controller-device", "desktop.input.send", {
        "session_id": opened["session_id"],
        "event": {"type": "click", "x": 0.25, "y": 0.5},
    })
    assert sent == {"ok": True, "status": "success"}
    assert provider.inputs[0][2] == {"logical_width": 200, "logical_height": 100}

    revoked = await service.revoke("controller-device")
    assert revoked["closed_sessions"] == 1
    assert provider.closed == ["provider-session"]


@pytest.mark.asyncio
async def test_system_login_never_relays_text_credentials(monkeypatch):
    remote = fake_remote()
    monkeypatch.setattr(desktop_module, "application_plugin_service", lambda name: remote if name == "remote" else None)
    service = RemoteDesktopService()
    provider = FakeProvider()
    provider.id = "system_login"
    service.providers["system_login"] = provider

    await service.approve("controller-device", ["login"], 120)
    opened = await service.handle_remote("controller-device", "desktop.session.open_login", {
        "provider": "system_login",
        "target_id": "window-1",
    })
    blocked = await service.handle_remote("controller-device", "desktop.login.input", {
        "session_id": opened["session_id"],
        "event": {"type": "text", "text": "do-not-relay"},
    })
    assert blocked["code"] == "credential_input_blocked"
    assert provider.inputs == []

    printable_key = await service.handle_remote("controller-device", "desktop.login.input", {
        "session_id": opened["session_id"],
        "event": {"type": "key", "key": "A"},
    })
    assert printable_key["code"] == "credential_input_blocked"
    assert provider.inputs == []


@pytest.mark.asyncio
async def test_electron_pointer_input_uses_latest_frame_mapping(monkeypatch):
    provider = desktop_module.ElectronWindowProvider()
    calls = []

    async def rpc(method, args, timeout=20.0):
        calls.append((method, args, timeout))
        return {"status": "success"}

    monkeypatch.setattr(provider, "_rpc", rpc)
    await provider.input(
        "provider-session",
        {"type": "click", "x": 0.25, "y": 0.5},
        {"logical_width": 200, "logical_height": 100},
    )
    assert calls[0][0] == "call"
    assert calls[0][1]["capability"] == "click_at"
    assert calls[0][1]["parameters"]["x"] == 50
    assert calls[0][1]["parameters"]["y"] == 50
