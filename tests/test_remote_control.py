"""Device trust, encrypted gateway, and desktop remote-settings tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import sqlite3
import sys
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.core.plugin import PluginContext

from cyrene.plugins.builtin.cyrene_remote.control import (
    BASE_REMOTE_CAPABILITIES,
    DEFAULT_REMOTE_CAPABILITIES,
    InMemoryRemoteRelay,
    RemoteControlStore,
    RemoteEnvelopeCodec,
    RemoteGateway,
    RemoteIdentityStore,
    WebSocketRemoteRelay,
)
from cyrene.plugins.builtin.cyrene_remote.commands import (
    RemoteCommandExecutor,
    RemoteControlRuntime,
    _chat_detail,
    public_remote_event,
)
from cyrene.plugins.builtin.cyrene_remote.relay import CyreneRelayServer
from cyrene.plugins.builtin.cyrene_remote.pairing import (
    DirectPairingServer,
    connect_by_address,
    normalize_pairing_address,
)
from cyrene.plugins.builtin.cyrene_remote.harness import handler as remote_harness
from cyrene.plugins.builtin.cyrene_remote.application import register_remote_routes


def _register_remote_test_routes(router, app, db_path, *, projects=None):
    async def list_projects():
        return list(projects or [])

    return register_remote_routes(
        router,
        app,
        db_path,
        bot=None,
        chat=SimpleNamespace(),
        projects=SimpleNamespace(list_projects=list_projects),
        tasks=SimpleNamespace(),
        goals=SimpleNamespace(),
    )


def test_remote_identity_uses_owner_only_local_file(tmp_path):
    identity_path = tmp_path / "device.remote-identity"
    store = RemoteIdentityStore(
        str(tmp_path / "remote.sqlite3"),
        fallback_path=identity_path,
    )

    first = store.get_or_create()
    reloaded = RemoteIdentityStore(
        str(tmp_path / "remote.sqlite3"),
        fallback_path=identity_path,
    ).get_or_create()

    assert identity_path.stat().st_mode & 0o777 == 0o600
    assert reloaded.device_id == first.device_id

    encoded = identity_path.read_text(encoding="utf-8").strip()
    payload = json.loads(
        base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    )
    assert payload["version"] == 1


def test_remote_identity_accepts_protocol_v2_version_tag(tmp_path):
    identity_path = tmp_path / "device.remote-identity"
    db_path = str(tmp_path / "remote.sqlite3")
    first = RemoteIdentityStore(db_path, fallback_path=identity_path).get_or_create()
    encoded = identity_path.read_text(encoding="utf-8").strip()
    payload = json.loads(
        base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    )
    payload["version"] = 2
    identity_path.write_text(
        base64.urlsafe_b64encode(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("="),
        encoding="utf-8",
    )

    reloaded = RemoteIdentityStore(
        db_path,
        fallback_path=identity_path,
    ).get_or_create()

    assert reloaded.device_id == first.device_id


@pytest.fixture
def paired_stores(monkeypatch, tmp_path):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    target = RemoteControlStore(str(tmp_path / "target.sqlite3"))
    controller = RemoteControlStore(str(tmp_path / "controller.sqlite3"))
    target.update_settings(
        enabled=True,
        relay_url="ws://127.0.0.1:9876",
        device_name="Target Cyrene",
    )
    controller.update_settings(
        enabled=True,
        relay_url="ws://127.0.0.1:9876",
        device_name="Controller Cyrene",
    )
    invitation = target.create_pairing_invitation(
        capabilities=list(DEFAULT_REMOTE_CAPABILITIES),
        project_scopes=["project_1"],
    )
    accepted = controller.accept_pairing_invitation(invitation["invitation"])
    completed = target.complete_pairing_response(accepted["response"])
    return {
        "target": target,
        "controller": controller,
        "target_peer": completed,
        "controller_peer": accepted["peer"],
        "pairing_response": accepted["response"],
    }


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("100.64.0.1", "100.64.0.1:37841"),
        ("100.96.8.4:41234", "100.96.8.4:41234"),
        ("100.127.255.254", "100.127.255.254:37841"),
    ],
)
def test_pairing_address_allows_tailscale_network(address, expected):
    assert normalize_pairing_address(address) == expected


@pytest.mark.parametrize("address", ["100.63.255.255", "100.128.0.1", "8.8.8.8"])
def test_pairing_address_does_not_expand_tailscale_allowlist(address):
    with pytest.raises(ValueError, match="local-network"):
        normalize_pairing_address(address)


def test_pairing_creates_directional_grants_and_single_use_invitation(
    paired_stores,
):
    target = paired_stores["target"]
    controller = paired_stores["controller"]
    target_peer = target.get_peer(controller.identity.device_id)
    controller_peer = controller.get_peer(target.identity.device_id)

    assert target_peer is not None
    assert target_peer["granted_capabilities"] == sorted(
        DEFAULT_REMOTE_CAPABILITIES
    )
    assert target_peer["granted_project_scopes"] == ["project_1"]
    assert controller_peer is not None
    assert controller_peer["received_capabilities"] == sorted(
        DEFAULT_REMOTE_CAPABILITIES
    )
    assert controller_peer["received_project_scopes"] == ["project_1"]
    assert target.identity.device_id.startswith("dev_")
    assert target.identity.device_id != controller.identity.device_id
    with pytest.raises(ValueError, match="already used"):
        target.complete_pairing_response(
            paired_stores["pairing_response"]
        )


def test_default_pairing_grant_completes_remote_agent_approval_loop(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    target = RemoteControlStore(str(tmp_path / "target-default.sqlite3"))
    controller = RemoteControlStore(str(tmp_path / "controller-default.sqlite3"))
    target.update_settings(
        enabled=True,
        relay_url="",
        device_name="Target",
    )
    invitation = target.create_pairing_invitation(
        project_scopes=["project_1"],
    )
    accepted = controller.accept_pairing_invitation(invitation["invitation"])
    target.complete_pairing_response(accepted["response"])

    peer = controller.get_peer(target.identity.device_id)
    assert peer is not None
    assert "approval:respond" in DEFAULT_REMOTE_CAPABILITIES
    assert "approval:respond" in peer["received_capabilities"]


def test_remote_plugin_pack_grants_are_valid_but_remote_pack_is_not(monkeypatch, tmp_path):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    store = RemoteControlStore(str(tmp_path / "tool-pack-grants.sqlite3"))
    controller = RemoteControlStore(str(tmp_path / "tool-pack-controller.sqlite3"))
    store.update_settings(enabled=True, relay_url="", device_name="Target")

    invitation = store.create_pairing_invitation(
        capabilities=[
            *DEFAULT_REMOTE_CAPABILITIES,
            "pluginpack:cyrene_desktop",
            "pluginpack:cyrene_extensions",
        ],
        project_scopes=["project_1"],
    )

    accepted = controller.accept_pairing_invitation(invitation["invitation"])
    assert accepted["peer"]["received_capabilities"] == sorted([
        *DEFAULT_REMOTE_CAPABILITIES,
        "pluginpack:cyrene_desktop",
        "pluginpack:cyrene_extensions",
    ])
    with pytest.raises(ValueError, match="unsupported remote capabilities"):
        store.create_pairing_invitation(
            capabilities=["pluginpack:cyrene_remote"],
            project_scopes=["project_1"],
        )


def test_runtime_database_write_lock_does_not_block_remote_command(
    paired_stores,
):
    async def scenario():
        target = paired_stores["target"]
        controller = paired_stores["controller"]
        relay = InMemoryRemoteRelay()
        received = []

        async def target_handler(peer_id, command, payload, project_id):
            received.append((peer_id, command, payload, project_id))
            return {"ok": True, "chat": {"id": "chat_remote"}}

        async def controller_handler(*_args):
            return {"ok": True}

        target_gateway = RemoteGateway(target, relay, target_handler)
        controller_gateway = RemoteGateway(
            controller,
            relay,
            controller_handler,
        )
        await target_gateway.start()
        await controller_gateway.start()
        runtime_lock = sqlite3.connect(controller.db_path)
        runtime_lock.execute(
            "CREATE TABLE IF NOT EXISTS active_chat_writer(value TEXT)"
        )
        runtime_lock.commit()
        runtime_lock.execute("BEGIN IMMEDIATE")
        runtime_lock.execute(
            "INSERT INTO active_chat_writer(value) VALUES ('streaming')"
        )
        try:
            result = await controller_gateway.request(
                target.identity.device_id,
                command="chats.send",
                project_id="project_1",
                payload={"message": "continue remotely"},
                idempotency_key="runtime_lock_isolated_1",
                timeout=3,
            )
        finally:
            runtime_lock.rollback()
            runtime_lock.close()
            await controller_gateway.stop()
            await target_gateway.stop()

        assert result == {"ok": True, "chat": {"id": "chat_remote"}}
        assert received == [
            (
                controller.identity.device_id,
                "chats.send",
                {"message": "continue remotely"},
                "project_1",
            )
        ]

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_ip_and_short_key_pairing_completes_both_sides(monkeypatch, tmp_path):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    target_db = str(tmp_path / "direct-target.sqlite3")
    controller_db = str(tmp_path / "direct-controller.sqlite3")
    target = RemoteControlStore(target_db)
    controller = RemoteControlStore(controller_db)
    for store, name in ((target, "Target"), (controller, "Controller")):
        store.update_settings(
            enabled=True,
            relay_url="ws://127.0.0.1:9876",
            device_name=name,
        )
    offer = target.create_short_pairing_invitation(
        capabilities=["chat:read", "chat:send"],
        project_scopes=["project_1"],
    )
    assert len(offer["pairing_key"]) == 11

    target_server = DirectPairingServer(target, host="127.0.0.1", port=0)
    controller_server = DirectPairingServer(controller, host="127.0.0.1", port=0)
    await target_server.start()
    await controller_server.start()
    target_port = target_server._server.sockets[0].getsockname()[1]
    controller_port = controller_server._server.sockets[0].getsockname()[1]
    try:
        result = await connect_by_address(
            controller,
            address=f"127.0.0.1:{target_port}",
            pairing_key=offer["pairing_key"].lower(),
            listener_port=controller_port,
        )

        assert result["peer"]["device_id"] == target.identity.device_id
        controller_peer = controller.get_peer(target.identity.device_id)
        target_peer = target.get_peer(controller.identity.device_id)
        assert controller_peer["received_capabilities"] == sorted(
            BASE_REMOTE_CAPABILITIES
        )
        assert controller_peer["lan_address"] == f"127.0.0.1:{target_port}"
        assert target_peer["granted_project_scopes"] == ["project_1"]
        assert target_peer["lan_address"] == f"127.0.0.1:{controller_port}"
        with pytest.raises(ValueError, match="invalid or expired"):
            target.claim_short_pairing_invitation(
                offer["pairing_key"], source="127.0.0.1"
            )

        controlled_actions = []

        async def target_handler(sender, command, payload, project_id):
            controlled_actions.append(
                (sender, command, payload, project_id)
            )
            return {
                "ok": True,
                "remote_device": "Target",
                "echo": payload.get("message"),
            }

        async def controller_handler(sender, command, payload, project_id):
            return {"ok": True}

        target_gateway = RemoteGateway(target, target_server, target_handler)
        controller_gateway = RemoteGateway(
            controller, controller_server, controller_handler
        )
        await target_gateway.start()
        await controller_gateway.start()
        try:
            response = await controller_gateway.request(
                target.identity.device_id,
                command="chats.send",
                project_id="project_1",
                payload={"message": "execute on the controlled Cyrene"},
                idempotency_key="idem_lan_control_1",
                timeout=5,
            )
        finally:
            await controller_gateway.stop()
            await target_gateway.stop()
        assert response == {
            "ok": True,
            "remote_device": "Target",
            "echo": "execute on the controlled Cyrene",
        }
        assert controlled_actions == [
            (
                controller.identity.device_id,
                "chats.send",
                {"message": "execute on the controlled Cyrene"},
                "project_1",
            )
        ]
    finally:
        await controller_server.stop()
        await target_server.stop()

    reopened_target = RemoteControlStore(target_db)
    reopened_controller = RemoteControlStore(controller_db)
    persisted_controller_peer = reopened_controller.get_peer(
        target.identity.device_id
    )
    persisted_target_peer = reopened_target.get_peer(
        controller.identity.device_id
    )
    assert persisted_controller_peer is not None
    assert persisted_controller_peer["lan_address"] == (
        f"127.0.0.1:{target_port}"
    )
    assert persisted_controller_peer["received_capabilities"] == sorted(
        BASE_REMOTE_CAPABILITIES
    )
    assert persisted_target_peer is not None
    assert persisted_target_peer["lan_address"] == (
        f"127.0.0.1:{controller_port}"
    )
    assert persisted_target_peer["granted_project_scopes"] == ["project_1"]


@pytest.mark.asyncio
async def test_mobile_request_response_transport_needs_no_controller_listener(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    target = RemoteControlStore(str(tmp_path / "mobile-target.sqlite3"))
    controller = RemoteControlStore(
        str(tmp_path / "mobile-controller.sqlite3")
    )
    for store, name in ((target, "Desktop"), (controller, "Android")):
        store.update_settings(
            enabled=True,
            relay_url="ws://127.0.0.1:9876",
            device_name=name,
        )
    offer = target.create_short_pairing_invitation(
        capabilities=["projects:list_shared"],
        project_scopes=["project_mobile"],
    )
    server = DirectPairingServer(target, host="127.0.0.1", port=0)

    async def handler(peer_id, command, payload, project_id):
        assert peer_id == controller.identity.device_id
        assert command == "projects.list"
        assert payload == {}
        assert project_id == ""
        return {
            "ok": True,
            "projects": [{"id": "project_mobile", "name": "Mobile"}],
        }

    gateway = RemoteGateway(target, server, handler)
    await server.start()
    await gateway.start()
    port = server._server.sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            claim = await client.post(
                f"http://127.0.0.1:{port}/v1/pairing/claim",
                json={"pairing_key": offer["pairing_key"]},
            )
            accepted = controller.accept_pairing_invitation(
                claim.json()["invitation"]
            )
            completed = await client.post(
                f"http://127.0.0.1:{port}/v1/pairing/complete",
                json={
                    "response": accepted["response"],
                    "transport_mode": "request_response",
                    "client_features": ["inline_response_v1"],
                },
            )
            assert completed.status_code == 200
            assert completed.json()["transport_mode"] == "request_response"
            assert (
                target.get_peer(controller.identity.device_id)["lan_address"]
                == ""
            )

            peer = controller.get_peer(target.identity.device_id)
            request_id = "request_mobile_inline_1"
            envelope = RemoteEnvelopeCodec.encode(
                identity=controller.identity,
                peer=peer,
                kind="command",
                payload={
                    "request_id": request_id,
                    "command": "projects.list",
                    "project_id": "",
                    "idempotency_key": "",
                    "payload": {},
                },
            )
            response = await client.post(
                f"http://127.0.0.1:{port}/v1/control/request",
                json={"envelope": envelope},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["accepted"] is True
            kind, payload = RemoteEnvelopeCodec.decode(
                identity=controller.identity,
                peer=peer,
                envelope=body["envelope"],
                mark_nonce=controller.mark_nonce,
            )
            assert kind == "response"
            assert payload["request_id"] == request_id
            assert payload["result"] == {
                "ok": True,
                "projects": [
                    {"id": "project_mobile", "name": "Mobile"}
                ],
            }
    finally:
        await gateway.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_runtime_falls_back_when_preferred_lan_port_is_occupied(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    blocker = None
    preferred = 0

    async def discard(
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        writer.close()
        await writer.wait_closed()

    for candidate in range(37841, 37940):
        try:
            blocker = await asyncio.start_server(
                discard,
                "127.0.0.1",
                candidate,
            )
            preferred = candidate
            break
        except OSError:
            continue
    assert blocker is not None

    store = RemoteControlStore(str(tmp_path / "runtime.sqlite3"))
    store.update_settings(
        enabled=True,
        relay_url="",
        device_name="Fallback target",
    )
    store.update_listen_port(preferred)

    async def executor(*_args):
        return {"ok": True}

    runtime = RemoteControlRuntime(
        db_path=store.db_path,
        store=store,
        executor=executor,
        lan_host="127.0.0.1",
    )
    try:
        await runtime.start()
        assert runtime.gateway is not None
        assert runtime.lan_port != preferred
        assert runtime.status()["port_fallback"] is True
        assert store.get_settings()["listen_port"] == runtime.lan_port
    finally:
        await runtime.stop()
        blocker.close()
        await blocker.wait_closed()


@pytest.mark.asyncio
async def test_direct_delivery_discovers_and_persists_fallback_port(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    target = RemoteControlStore(str(tmp_path / "target.sqlite3"))
    controller = RemoteControlStore(str(tmp_path / "controller.sqlite3"))
    target.update_settings(enabled=True, relay_url="", device_name="Target")
    controller.update_settings(
        enabled=True,
        relay_url="",
        device_name="Controller",
    )

    async def unrelated_http_server(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        await reader.read(64 * 1024)
        writer.write(
            b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n"
            b"Connection: close\r\n\r\n"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    blocker = None
    preferred = 0
    for candidate in range(37841, 37940):
        try:
            blocker = await asyncio.start_server(
                unrelated_http_server,
                "127.0.0.1",
                candidate,
            )
            preferred = candidate
            break
        except OSError:
            continue
    assert blocker is not None

    offer = target.create_short_pairing_invitation(
        capabilities=["chat:read"],
        project_scopes=["project_1"],
    )
    target_server = DirectPairingServer(
        target,
        host="127.0.0.1",
        port=preferred,
    )
    controller_server = DirectPairingServer(
        controller,
        host="127.0.0.1",
        port=0,
    )
    await target_server.start()
    await controller_server.start()
    try:
        await connect_by_address(
            controller,
            address=f"127.0.0.1:{target_server.port}",
            pairing_key=offer["pairing_key"],
            listener_port=controller_server.port,
        )
        controller.update_peer_lan_address(
            target.identity.device_id,
            f"127.0.0.1:{preferred}",
        )

        async def handler(*_args):
            return {"ok": True}

        target_gateway = RemoteGateway(target, target_server, handler)
        controller_gateway = RemoteGateway(
            controller,
            controller_server,
            handler,
        )
        await target_gateway.start()
        await controller_gateway.start()
        try:
            result = await controller_gateway.request(
                target.identity.device_id,
                command="chats.read",
                project_id="project_1",
                payload={},
                idempotency_key="fallback-discovery",
                timeout=5,
            )
        finally:
            await controller_gateway.stop()
            await target_gateway.stop()

        assert result == {"ok": True}
        peer = controller.get_peer(target.identity.device_id)
        assert peer is not None
        assert peer["lan_address"] == f"127.0.0.1:{target_server.port}"
        assert target_server.port != preferred
    finally:
        await controller_server.stop()
        await target_server.stop()
        blocker.close()
        await blocker.wait_closed()


@pytest.mark.asyncio
async def test_encrypted_grant_sync_updates_peer_listener_port(paired_stores):
    controller = paired_stores["controller"]
    target = paired_stores["target"]
    controller.update_peer_lan_address(
        target.identity.device_id,
        "100.100.8.4:37841",
    )

    async def handler(*_args):
        return {"ok": True}

    gateway = RemoteGateway(controller, InMemoryRemoteRelay(), handler)
    target_peer = target.get_peer(controller.identity.device_id)
    assert target_peer is not None
    envelope = RemoteEnvelopeCodec.encode(
        identity=target.identity,
        peer=target_peer,
        kind="grant_update",
        payload={
            "capabilities": target_peer["granted_capabilities"],
            "project_scopes": target_peer["granted_project_scopes"],
            "listener_port": 37857,
        },
    )
    await gateway._receive(envelope)

    refreshed = controller.get_peer(target.identity.device_id)
    assert refreshed is not None
    assert refreshed["lan_address"] == "100.100.8.4:37857"


def test_pairing_invitation_signature_rejects_grant_tampering(
    monkeypatch,
    tmp_path,
):
    import base64
    import json

    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    target = RemoteControlStore(str(tmp_path / "signed-target.sqlite3"))
    controller = RemoteControlStore(str(tmp_path / "signed-controller.sqlite3"))
    target.update_settings(
        enabled=True,
        relay_url="ws://127.0.0.1:9876",
        device_name="Target",
    )
    invitation = target.create_pairing_invitation(
        capabilities=["chat:read"],
        project_scopes=["project_1"],
    )
    encoded = invitation["invitation"]
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    payload = json.loads(raw.decode("utf-8"))
    payload["granted_capabilities"] = ["task:dispatch"]
    tampered_raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    tampered = base64.urlsafe_b64encode(tampered_raw).decode("ascii").rstrip("=")

    with pytest.raises(ValueError, match="signature mismatch"):
        controller.accept_pairing_invitation(tampered)


def test_peer_grant_scope_and_revocation_are_enforced(paired_stores):
    target = paired_stores["target"]
    controller = paired_stores["controller"]
    controller_id = controller.identity.device_id

    assert target.authorize_inbound(
        controller_id, "chats.send", "project_1"
    ) == (True, "")
    assert target.authorize_inbound(
        controller_id, "chats.send", "project_2"
    ) == (False, "project_scope_denied")
    assert target.authorize_inbound(
        controller_id, "tasks.dispatch", "project_1"
    ) == (True, "")
    assert target.authorize_inbound(
        controller_id, "chats.read", ""
    ) == (False, "project_scope_required")

    updated = target.update_peer_grant(
        controller_id,
        capabilities=["task:read"],
        project_scopes=["project_2"],
    )
    assert updated["granted_capabilities"] == sorted(
        BASE_REMOTE_CAPABILITIES
    )
    assert target.authorize_inbound(
        controller_id, "tasks.read", "project_2"
    ) == (True, "")
    target.update_settings(
        enabled=False,
        relay_url="ws://127.0.0.1:9876",
        device_name="Target Cyrene",
    )
    assert target.authorize_inbound(
        controller_id, "tasks.read", "project_2"
    ) == (False, "remote_access_disabled")
    target.update_settings(
        enabled=True,
        relay_url="ws://127.0.0.1:9876",
        device_name="Target Cyrene",
    )
    assert target.revoke_peer(controller_id) is True
    assert target.authorize_inbound(
        controller_id, "tasks.read", "project_2"
    ) == (False, "peer_not_trusted")


def test_remote_shell_requires_live_code_plugin_even_with_stale_grant(
    paired_stores,
    monkeypatch,
):
    target = paired_stores["target"]
    controller = paired_stores["controller"]
    controller_id = controller.identity.device_id
    target.update_peer_grant(
        controller_id,
        capabilities=["pluginpack:cyrene_code"],
        project_scopes=["project_1"],
    )
    target.update_peer_received_grant(
        controller_id,
        capabilities=["pluginpack:cyrene_code"],
        project_scopes=["project_1"],
    )
    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_remote.control._remote_shell_plugin_available",
        lambda: True,
    )
    shell_commands = (
        "shell.open",
        "shell.read",
        "shell.write",
        "shell.interrupt",
        "shell.close",
    )
    for command in shell_commands:
        assert target.authorize_inbound(
            controller_id, command, "project_1"
        ) == (True, "")
        assert target.authorize_outbound(
            controller_id, command, "project_1"
        ) == (True, "")

    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_remote.control._remote_shell_plugin_available",
        lambda: False,
    )
    for command in shell_commands:
        assert target.authorize_inbound(
            controller_id, command, "project_1"
        ) == (False, "plugin_pack_unavailable")
        assert target.authorize_outbound(
            controller_id, command, "project_1"
        ) == (False, "plugin_pack_unavailable")

    async def scenario():
        from cyrene.localization import localized

        monkeypatch.setattr(
            "cyrene.plugins.builtin.cyrene_remote.commands.application_plugin_service",
            lambda _name: None,
        )
        executor = RemoteCommandExecutor(store=target, db_path=target.db_path)
        for command in shell_commands:
            result = await executor(controller_id, command, {}, "project_1")
            assert result["ok"] is False
            assert result["code"] == "remote_plugin_unavailable"
            assert result["error"] == localized(
                "The remote Plugin is unavailable.",
                "远程插件不可用。",
            )

    asyncio.run(scenario())


def test_remote_executor_filters_projects_and_public_run_events(
    paired_stores,
    monkeypatch,
):
    async def scenario():
        target = paired_stores["target"]
        controller = paired_stores["controller"]
        async def list_projects():
            return [
                {"id": "project_1", "name": "Shared"},
                {"id": "project_private", "name": "Private"},
            ]
        done = asyncio.Event()
        done.set()
        run = SimpleNamespace(
            run_id="run_shared",
            chat_id="chat_shared",
            status="done",
            created_at="2026-07-27T00:00:00+00:00",
            termination_reason="completed",
            done=done,
            events=[
                {
                    "_seq": 1,
                    "runId": "run_shared",
                    "type": "ack",
                    "chatId": "chat_shared",
                    "workspacePath": "/private/workspace",
                },
                {
                    "_seq": 2,
                    "runId": "run_shared",
                    "type": "reasoning_delta",
                    "delta": "private chain",
                },
                {
                    "_seq": 3,
                    "runId": "run_shared",
                    "type": "workspace_changes",
                    "changes": [{"path": "/private/secret.txt"}],
                },
                {
                    "_seq": 4,
                    "runId": "run_shared",
                    "type": "reply_done",
                    "response": "public answer",
                    "debug": {"path": "/private/debug"},
                },
                {
                    "_seq": 5,
                    "runId": "run_shared",
                    "type": "awaiting_user",
                    "pendingQuestion": {
                        "id": "question_1",
                        "text": "Allow once?",
                        "options": ["Allow", "Deny"],
                        "allowCustom": False,
                        "debug": {"path": "/private/debug"},
                    },
                },
            ],
        )

        async def get_chat(_chat_id):
            return {
                "chat": {
                    "id": "chat_shared",
                    "projectId": "project_1",
                    "messages": [],
                }
            }

        run_manager = SimpleNamespace(
            get_replayable_by_run_id=lambda _run_id: run
        )
        executor = RemoteCommandExecutor(
            store=target,
            chat=SimpleNamespace(get=get_chat, run_manager=run_manager),
            projects=SimpleNamespace(list_projects=list_projects),
        )

        projects = await executor(
            controller.identity.device_id,
            "projects.list",
            {},
            "",
        )
        events = await executor(
            controller.identity.device_id,
            "runs.events",
            {"run_id": "run_shared"},
            "project_1",
        )
        wrong_project = await executor(
            controller.identity.device_id,
            "runs.read",
            {"run_id": "run_shared"},
            "project_private",
        )

        assert projects["projects"] == [
            {
                "id": "project_1",
                "name": "Shared",
                "status": "active",
                "updated_at": "",
            }
        ]
        assert [event["type"] for event in events["events"]] == [
            "ack",
            "reasoning_delta",
            "reply_done",
            "awaiting_user",
        ]
        assert events["events"][0] == {
            "type": "ack",
            "cursor": 1,
            "run_id": "run_shared",
            "chatId": "chat_shared",
        }
        assert events["events"][1]["delta"] == "private chain"
        assert events["events"][2]["response"] == "public answer"
        assert "debug" not in events["events"][2]
        assert events["events"][3]["pending_question"] == {
            "id": "question_1",
            "text": "Allow once?",
            "options": ["Allow", "Deny"],
            "allowCustom": False,
        }
        assert events["next_cursor"] == 5
        assert wrong_project["code"] == "remote_project_mismatch"

    asyncio.run(scenario())


def test_remote_command_sanitizes_task_data_and_rejects_elevated_modes(
    paired_stores,
):
    async def scenario():
        target = paired_stores["target"]
        controller = paired_stores["controller"]
        task = {
            "id": "task_1",
            "projectId": "project_1",
            "title": "Shared task",
            "status": "idle",
            "plan": [
                {
                    "id": "step_1",
                    "title": "Safe title",
                    "status": "pending",
                    "workspacePath": "/private/workspace",
                    "debug": {"secret": True},
                }
            ],
            "pendingQuestion": {
                "id": "question_1",
                "text": "Allow this operation?",
                "options": [
                    {"id": "allow_once", "label": "Allow once"},
                    {"id": "deny", "label": "Deny"},
                ],
                "allowCustom": True,
                "debug": {"path": "/private/debug"},
            },
        }
        calls = {"chat_send": 0, "task_dispatch": 0}
        modes = {}

        async def list_tasks(_project_id):
            return {"sessions": [task]}

        async def get_task(_task_id):
            return {"session": task}

        async def dispatch_task(_task_id, body):
            calls["task_dispatch"] += 1
            modes["task"] = body["mode"]
            return {"session": task}

        async def get_chat(_chat_id):
            return {
                "chat": {
                    "id": "chat_1",
                    "projectId": "project_1",
                    "messages": [],
                    "pendingQuestion": task["pendingQuestion"],
                }
            }

        async def send_chat_detached(_chat_id, body, **_kwargs):
            calls["chat_send"] += 1
            modes["chat"] = body["mode"]
            return {"run_id": "run_1"}

        executor = RemoteCommandExecutor(
            store=target,
            chat=SimpleNamespace(
                get=get_chat,
                send=send_chat_detached,
                run_manager=SimpleNamespace(get=lambda _chat_id: None),
            ),
            projects=SimpleNamespace(list_tasks=list_tasks),
            tasks=SimpleNamespace(get=get_task, dispatch=dispatch_task),
        )

        listed = await executor(
            controller.identity.device_id,
            "tasks.list",
            {},
            "project_1",
        )
        assert listed["tasks"][0]["plan"] == [
            {
                "id": "step_1",
                "title": "Safe title",
                "status": "pending",
            }
        ]
        assert listed["tasks"][0]["pending_question"] == {
            "id": "question_1",
            "text": "Allow this operation?",
            "options": [
                {"id": "allow_once", "label": "Allow once"},
                {"id": "deny", "label": "Deny"},
            ],
            "allowCustom": True,
        }
        chat_detail = await executor(
            controller.identity.device_id,
            "chats.read",
            {"chat_id": "chat_1"},
            "project_1",
        )
        assert chat_detail["chat"]["pending_question"] == listed["tasks"][0]["pending_question"]

        await executor(
            controller.identity.device_id,
            "chats.send",
            {
                "chat_id": "chat_1",
                "message": "Run everything",
                "permission_mode": "full_access",
            },
            "project_1",
        )
        await executor(
            controller.identity.device_id,
            "tasks.dispatch",
            {
                "task_id": "task_1",
                "message": "Run everything",
                "permission_mode": "full_access",
            },
            "project_1",
        )
        assert calls == {"chat_send": 1, "task_dispatch": 1}
        assert modes == {"chat": "full_access", "task": "full_access"}

        await executor(
            controller.identity.device_id,
            "chats.send",
            {
                "chat_id": "chat_1",
                "message": "Review permissions automatically",
                "permission_mode": "auto",
            },
            "project_1",
        )
        await executor(
            controller.identity.device_id,
            "tasks.dispatch",
            {
                "task_id": "task_1",
                "message": "Review permissions automatically",
                "permission_mode": "auto",
            },
            "project_1",
        )
        assert calls == {"chat_send": 2, "task_dispatch": 2}
        assert modes == {"chat": "auto", "task": "auto"}

    asyncio.run(scenario())


def test_remote_harness_filters_by_granted_plugin_pack_and_uses_bound_context(
    paired_stores,
    monkeypatch,
    tmp_path,
):
    async def scenario():
        target = paired_stores["target"]
        controller = paired_stores["controller"]
        target.update_peer_grant(
            controller.identity.device_id,
            capabilities=["pluginpack:cyrene_desktop"],
            project_scopes=["project_1"],
        )
        monkeypatch.setattr(
            "cyrene.plugins.builtin.cyrene_remote.commands._remote_project",
            lambda project_id: {
                "id": project_id,
                "workspacePath": str(tmp_path),
            },
        )
        monkeypatch.setattr(
            "cyrene.plugins.builtin.cyrene_remote.commands._remote_project_workspace",
            lambda _project: str(tmp_path),
        )
        observed = {"calls": []}

        class PluginRuntime:
            async def call(self, name, arguments, context, *, call_id=""):
                observed["calls"].append((name, dict(arguments), call_id))
                observed.update({
                    "name": name,
                    "arguments": arguments,
                    "context": context,
                    "call_id": call_id,
                })
                if arguments.get("operation") == "list":
                    return SimpleNamespace(
                        success=True,
                        error="",
                        value={
                            "operation": "list",
                            "packs": ["cyrene_desktop"],
                            "standalone_tools": [],
                        },
                    )
                if arguments.get("operation") == "describe":
                    return SimpleNamespace(
                        success=True,
                        error="",
                        value={
                            "operation": "describe",
                            "plugins": [{
                                "name": "app_use",
                                "description": "Desktop control",
                                "input_schema": {"type": "object"},
                                "pack": "cyrene_desktop",
                            }],
                        },
                    )
                return SimpleNamespace(
                    success=True,
                    error="",
                    value={
                        "operation": "invoke",
                        "name": "app_use",
                        "pack": "cyrene_desktop",
                        "result": {
                            "status": "success",
                            "result": "remote desktop inspected",
                        },
                    },
                )

        host = SimpleNamespace(
            registry=SimpleNamespace(
                list_plugins=lambda: [
                    SimpleNamespace(
                        plugin=SimpleNamespace(name="app_use"),
                        pack_id="cyrene_desktop",
                    )
                ]
            ),
            runtime=PluginRuntime(),
            services={},
        )
        monkeypatch.setattr(
            "cyrene.plugins.builtin.cyrene_remote.commands.application_plugin_scope",
            lambda: host,
        )
        executor = RemoteCommandExecutor(
            store=target,
            db_path=target.db_path,
        )

        denied = await executor(
            controller.identity.device_id,
            "harness.list",
            {"plugin_pack": "cyrene_code"},
            "project_1",
        )
        discovered = await executor(
            controller.identity.device_id,
            "harness.list",
            {"plugin_pack": "cyrene_desktop", "query": "desktop"},
            "project_1",
        )
        invocation_arguments = {
            "device_id": target.identity.device_id,
            "project_id": "project_1",
            "plugin_pack": "cyrene_desktop",
            "operation": "invoke",
            "capability_id": "app_use",
            "arguments": {"operation": "list_targets"},
        }
        authorization_hash = hashlib.sha256(
            json.dumps(
                invocation_arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        invoked = await executor(
            controller.identity.device_id,
            "harness.invoke",
            {
                "plugin_pack": "cyrene_desktop",
                "capability_id": "app_use",
                "arguments": {"operation": "list_targets"},
                "call_id": "remote-call-1",
                "authorization": {
                    "version": 1,
                    "approved": True,
                    "permission_mode": "auto",
                    "arguments_sha256": authorization_hash,
                },
            },
            "project_1",
        )
        missing_receipt = await executor(
            controller.identity.device_id,
            "harness.invoke",
            {
                "plugin_pack": "cyrene_desktop",
                "capability_id": "app_use",
                "arguments": {"operation": "list_targets"},
            },
            "project_1",
        )
        manual_file_tunnel = await executor(
            controller.identity.device_id,
            "harness.invoke",
            {
                "plugin_pack": "cyrene_desktop",
                "capability_id": "desktop.shell",
                "arguments": {
                    "command": "base64 -d > payload.bin " + ("A" * 300),
                },
            },
            "project_1",
        )

        assert denied["code"] == "remote_plugin_pack_denied"
        assert discovered["result"]["plugins"][0]["name"] == "app_use"
        assert [call[1]["operation"] for call in observed["calls"][:2]] == [
            "list",
            "describe",
        ]
        assert missing_receipt["code"] == "remote_authorization_invalid"
        assert manual_file_tunnel["code"] == "remote_file_channel_required"
        assert invoked["ok"] is True
        assert observed["name"] == "toolbox"
        assert observed["arguments"]["name"] == "app_use"
        run_context = observed["context"].data["run_context"]
        assert run_context["caller"] == "remote_harness"
        assert run_context["permission_mode"] == "auto"
        assert run_context["temporary_full_access"] is False
        assert run_context["bounded_remote_authorization"] is True
        assert invoked["authorization"]["scope"] == "single_invocation"

    asyncio.run(scenario())


def test_remote_shell_is_direct_project_scoped_and_device_owned(
    paired_stores,
    monkeypatch,
    tmp_path,
):
    async def scenario():
        target = paired_stores["target"]
        controller = paired_stores["controller"]
        target.update_peer_grant(
            controller.identity.device_id,
            capabilities=["pluginpack:cyrene_code"],
            project_scopes=["project_1"],
        )
        monkeypatch.setattr(
            "cyrene.plugins.builtin.cyrene_remote.commands._remote_project",
            lambda project_id: {
                "id": project_id,
                "name": "Authorized project",
                "workspacePath": str(tmp_path),
            },
        )
        monkeypatch.setattr(
            "cyrene.plugins.builtin.cyrene_remote.commands._remote_project_workspace",
            lambda _project: str(tmp_path),
        )
        observed = {}

        class FakeTerminalClient:
            def __init__(self):
                self.terminal = {
                    "id": "shell_mobile_1", "status": "running",
                    "cwd": str(tmp_path), "exitCode": None, "nextSeq": 1,
                }
                self.screen_text = "[terminal started]"

            async def create(self, project_id, **kwargs):
                observed["create"] = (project_id, kwargs)
                return {"terminal": dict(self.terminal)}

            async def screen(self, terminal_id):
                return {"terminal": dict(self.terminal), "screenText": self.screen_text}

            async def input(self, terminal_id, data, *, actor="agent"):
                observed["input"] = (terminal_id, data, actor)
                self.terminal["nextSeq"] = 3
                self.screen_text = f"$ {data}\n{tmp_path}"
                return await self.screen(terminal_id)

            async def interrupt(self, terminal_id):
                observed["interrupt"] = terminal_id
                return await self.screen(terminal_id)

            async def remove(self, terminal_id):
                self.terminal["status"] = "closed"
                return {"terminal": dict(self.terminal), "deleted": True}

        fake_client = FakeTerminalClient()
        monkeypatch.setattr(
            "cyrene.plugins.builtin.cyrene_remote.commands.application_plugin_service",
            lambda name: fake_client if name == "remote_shell" else None,
        )
        executor = RemoteCommandExecutor(
            store=target,
            db_path=target.db_path,
        )

        opened = await executor(
            controller.identity.device_id,
            "shell.open",
            {},
            "project_1",
        )
        written = await executor(
            controller.identity.device_id,
            "shell.write",
            {
                "shell_id": opened["shell_id"],
                "input": "pwd",
                "cursor": opened["next_cursor"],
            },
            "project_1",
        )

        assert observed["create"] == (
            "project_1",
            {"cwd": str(tmp_path), "title": "Mobile Shell · Authorized project"},
        )
        assert observed["input"] == ("shell_mobile_1", "pwd", "user")
        assert written["lines"][0]["text"] == f"$ pwd\n{tmp_path}"
        interrupted = await executor(
            controller.identity.device_id,
            "shell.interrupt",
            {
                "shell_id": opened["shell_id"],
                "cursor": written["next_cursor"],
            },
            "project_1",
        )
        assert interrupted["status"] == "running"
        assert observed["interrupt"] == "shell_mobile_1"
        with pytest.raises(ValueError, match="unavailable"):
            await executor(
                "another_phone",
                "shell.read",
                {"shell_id": opened["shell_id"]},
                "project_1",
            )

    asyncio.run(scenario())


def test_remote_harness_sends_list_and_exact_authorized_invoke(monkeypatch):
    async def scenario():
        class AllowPermission:
            async def request_dynamic_permission(self, **_kwargs):
                return None

        device = {
            "device_id": "device_target",
            "received_capabilities": [
                "pluginpack:cyrene_desktop",
                "workspace_file:metadata",
            ],
        }
        commands = []
        monkeypatch.setattr(
            "cyrene.plugins.builtin.cyrene_remote.harness.resolve_selected_remote_device",
            lambda *_args, **_kwargs: ({}, device),
        )

        async def send(args, *_rest, **_kwargs):
            commands.append(args)
            return {"ok": True, "result": {"status": "success"}}
        monkeypatch.setattr(
            "cyrene.plugins.builtin.cyrene_remote.harness.request_remote_command",
            send,
        )

        context = PluginContext(
            data={
                "db_path": "runtime.sqlite3",
                "remote_device_ids": ["device_target"],
                "run_context": {
                    "session_id": "chat_local",
                    "permission_mode": "auto",
                },
            },
            services={"permission": AllowPermission()},
        )
        discovered = json.loads(await remote_harness(
            {
                "project_id": "project_1",
                "plugin_pack": "cyrene_desktop",
                "operation": "list",
                "query": "desktop",
            },
            context,
        ))
        invoked = json.loads(await remote_harness(
            {
                "project_id": "project_1",
                "plugin_pack": "cyrene_desktop",
                "operation": "invoke",
                "capability_id": "desktop.use",
                "arguments": {"operation": "list_targets"},
                "reason": "Inspect the selected remote desktop",
            },
            context,
        ))

        assert discovered["ok"] is True
        assert invoked["ok"] is True
        assert [item["command"] for item in commands] == [
            "harness.list",
            "harness.invoke",
        ]
        assert [item["payload"]["plugin_pack"] for item in commands] == [
            "cyrene_desktop",
            "cyrene_desktop",
        ]
        assert commands[1]["payload"]["authorization"]["approved"] is True
        assert commands[1]["payload"]["authorization"]["destructive_approved"] is False

    asyncio.run(scenario())


def test_remote_permission_classification_matches_v0713_boundaries(tmp_path):
    from cyrene.plugins.builtin.cyrene_remote.permission import (
        remote_permission_request,
    )

    context = PluginContext(workspace=tmp_path)
    cancel, cancel_destructive = remote_permission_request(
        "RemoteCyreneJobs",
        {"operation": "cancel", "job_id": "job_1"},
        context,
        device_id="device_1",
        project_id="project_1",
    )
    delete, delete_destructive = remote_permission_request(
        "RemoteCyreneFiles",
        {"operation": "delete_tree", "remote_path": "build"},
        context,
        device_id="device_1",
        project_id="project_1",
    )
    safe_invoke, safe_destructive = remote_permission_request(
        "RemoteHarness",
        {
            "operation": "invoke",
            "capability_id": "desktop.use",
            "arguments": {"operation": "list_targets"},
        },
        context,
        device_id="device_1",
        project_id="project_1",
    )

    assert cancel["kind"] == "remote_job_control"
    assert cancel_destructive is False
    assert delete["kind"] == "destructive_confirmation"
    assert delete_destructive is True
    assert safe_invoke["kind"] == "remote_harness_invoke"
    assert safe_destructive is False


def test_remote_command_reads_only_attachment_referenced_by_shared_chat(
    paired_stores,
    monkeypatch,
    tmp_path,
):
    async def scenario():
        from cyrene import config as cyrene_config
        from cyrene.runtime import attachments as managed_attachments
        from cyrene.plugins.builtin.cyrene_remote import (
            commands as remote_commands_module,
        )

        # Several legacy test modules install PIL stubs during collection.
        # Reuse the real modules captured by test_app_use (or import them
        # normally when this file runs alone), then re-register the codecs
        # needed by this integration test.
        holder = next(
            (
                module
                for module in tuple(sys.modules.values())
                if isinstance(module, ModuleType)
                and "_REAL_PIL" in vars(module)
                and "_REAL_PIL_IMAGE" in vars(module)
            ),
            None,
        )
        if holder is None:
            real_pil = importlib.import_module("PIL")
            real_image = importlib.import_module("PIL.Image")
        else:
            real_pil = holder._REAL_PIL
            real_image = holder._REAL_PIL_IMAGE
        monkeypatch.setitem(sys.modules, "PIL", real_pil)
        monkeypatch.setitem(sys.modules, "PIL.Image", real_image)
        monkeypatch.setattr(real_pil, "Image", real_image)
        real_image_ops = importlib.reload(
            importlib.import_module("PIL.ImageOps")
        )
        for plugin_name in ("PIL.PngImagePlugin", "PIL.WebPImagePlugin"):
            importlib.reload(importlib.import_module(plugin_name))
        real_image.init()
        monkeypatch.setattr(remote_commands_module, "Image", real_image)
        monkeypatch.setattr(
            remote_commands_module,
            "ImageOps",
            real_image_ops,
        )

        data_dir = tmp_path / "data"
        exports = tmp_path / "exports"
        uploads = tmp_path / "uploads"
        exports.mkdir()
        uploads.mkdir()
        monkeypatch.setattr(managed_attachments, "EXPORTS_DIR", exports)
        monkeypatch.setattr(managed_attachments, "UPLOADS_DIR", uploads)
        screenshot = exports / "desktop.png"
        screenshot.write_bytes(b"remote-screenshot")
        preview = exports / "preview.png"
        real_image.new(
            "RGB",
            (2400, 1200),
            (72, 118, 180),
        ).save(preview)
        monkeypatch.setattr(cyrene_config, "DATA_DIR", data_dir)
        external = tmp_path / "outside-managed-roots.bin"
        external.write_bytes(b"x" * (10 * 1024 * 1024 + 17))

        async def get_chat(_chat_id):
            return {
                "chat": {
                    "id": "chat_1",
                    "projectId": "project_1",
                    "messages": [
                        {
                            "id": "message_1",
                            "role": "assistant",
                            "content": "Screenshot attached.",
                            "attachments": [
                                {
                                    "id": "desktop.png",
                                    "name": "Desktop screenshot",
                                    "content_type": "image/png",
                                    "kind": "image",
                                    "size": len(b"remote-screenshot"),
                                    "url": "/api/chat/export/desktop.png",
                                },
                                {
                                    "id": "preview.png",
                                    "name": "Generated preview",
                                    "content_type": "image/png",
                                    "kind": "image",
                                    "size": preview.stat().st_size,
                                    "width": 2400,
                                    "height": 1200,
                                    "url": "/api/chat/export/preview.png",
                                },
                                {
                                    "id": "large-result",
                                    "name": "Large result",
                                    "content_type": "application/octet-stream",
                                    "kind": "file",
                                    "size": external.stat().st_size,
                                    "path": str(external),
                                }
                            ],
                        }
                    ],
                }
            }

        executor = RemoteCommandExecutor(
            store=paired_stores["target"],
            chat=SimpleNamespace(get=get_chat),
        )
        result = await executor(
            paired_stores["controller"].identity.device_id,
            "attachments.read",
            {
                "chat_id": "chat_1",
                "attachment_id": "desktop.png",
            },
            "project_1",
        )
        missing = await executor(
            paired_stores["controller"].identity.device_id,
            "attachments.read",
            {
                "chat_id": "chat_1",
                "attachment_id": "secret.txt",
            },
            "project_1",
        )
        thumbnail = await executor(
            paired_stores["controller"].identity.device_id,
            "attachments.read",
            {
                "chat_id": "chat_1",
                "attachment_id": "preview.png",
                "variant": "thumbnail",
            },
            "project_1",
        )
        large_first = await executor(
            paired_stores["controller"].identity.device_id,
            "attachments.read",
            {
                "chat_id": "chat_1",
                "attachment_id": "large-result",
                "offset": 0,
                "limit": 1024 * 1024,
            },
            "project_1",
        )
        large_last = await executor(
            paired_stores["controller"].identity.device_id,
            "attachments.read",
            {
                "chat_id": "chat_1",
                "attachment_id": "large-result",
                "offset": 10 * 1024 * 1024,
                "limit": 1024 * 1024,
            },
            "project_1",
        )

        assert result["ok"] is True
        assert result["media_type"] == "image/png"
        assert result["attachment"]["kind"] == "image"
        assert result["content_base64"] == "cmVtb3RlLXNjcmVlbnNob3Q="
        assert missing["code"] == "attachment_not_found"
        assert thumbnail["variant"] == "thumbnail"
        assert thumbnail["media_type"] == "image/webp"
        assert thumbnail["original_size"] == preview.stat().st_size
        assert thumbnail["width"] == 960
        assert thumbnail["height"] == 480
        with real_image.open(
            BytesIO(base64.b64decode(thumbnail["content_base64"]))
        ) as image:
            assert image.format == "WEBP"
            assert image.size == (960, 480)
        assert large_first["chunk_size"] == 1024 * 1024
        assert large_first["eof"] is False
        assert large_last["chunk_size"] == 17
        assert large_last["eof"] is True
        assert large_last["size"] == 10 * 1024 * 1024 + 17

    asyncio.run(scenario())


def test_remote_status_assembles_chunks_into_local_attachment(
    monkeypatch,
    tmp_path,
):
    async def scenario():
        from cyrene import config as cyrene_config
        from cyrene.runtime import attachments as managed_attachments
        from cyrene.plugins.builtin.cyrene_remote import status as remote_status

        data_dir = tmp_path / "data"
        exports = data_dir / "exports"
        source = b"a" * (1024 * 1024 + 31)
        monkeypatch.setattr(cyrene_config, "DATA_DIR", data_dir)
        monkeypatch.setattr(managed_attachments, "EXPORTS_DIR", exports)

        async def fake_request(args, _context):
            payload = dict(args.get("payload") or {})
            offset = int(payload.get("offset") or 0)
            limit = int(payload.get("limit") or 1)
            chunk = source[offset : offset + limit]
            next_offset = offset + len(chunk)
            return {
                "ok": True,
                "filename": "large-result.bin",
                "media_type": "application/octet-stream",
                "size": len(source),
                "offset": offset,
                "next_offset": next_offset,
                "eof": next_offset >= len(source),
                "content_base64": __import__("base64").b64encode(chunk).decode(
                    "ascii"
                ),
            }

        monkeypatch.setattr(
            remote_status,
            "request_remote_command",
            fake_request,
        )
        async def no_progress(**_kwargs):
            return None
        monkeypatch.setattr(remote_status, "publish_tool_progress", no_progress)
        raw = await remote_status.handler(
            {
                "command": "attachments.read",
                "project_id": "project_1",
                "payload": {
                    "chat_id": "chat_1",
                    "attachment_id": "large-result",
                },
            },
            PluginContext(data={"db_path": str(tmp_path / "runtime.sqlite3")}),
        )
        result = json.loads(raw)
        assert result["ok"] is True
        assert result["downloaded"] is True
        assert result["size"] == len(source)
        assert "content_base64" not in result
        local_path = Path(result["attachment"]["path"])
        assert local_path.read_bytes() == source

    asyncio.run(scenario())


def test_encrypted_gateway_executes_typed_command_and_dedupes(paired_stores):
    async def scenario():
        target = paired_stores["target"]
        controller = paired_stores["controller"]
        relay = InMemoryRemoteRelay()
        calls = []

        async def target_handler(peer_id, command, payload, project_id):
            calls.append((peer_id, command, payload, project_id))
            return {"ok": True, "message": payload["message"]}

        async def controller_handler(peer_id, command, payload, project_id):
            raise AssertionError("controller must not receive a command")

        target_gateway = RemoteGateway(target, relay, target_handler)
        controller_gateway = RemoteGateway(controller, relay, controller_handler)
        await target_gateway.start()
        await controller_gateway.start()
        try:
            first = await controller_gateway.request(
                target.identity.device_id,
                command="chats.send",
                project_id="project_1",
                idempotency_key="send_1",
                payload={"message": "run remotely"},
            )
            duplicate = await controller_gateway.request(
                target.identity.device_id,
                command="chats.send",
                project_id="project_1",
                idempotency_key="send_1",
                payload={"message": "run remotely"},
            )
        finally:
            await controller_gateway.stop()
            await target_gateway.stop()

        assert first == {"ok": True, "message": "run remotely"}
        assert duplicate == {
            "ok": True,
            "message": "run remotely",
            "duplicate": True,
        }
        assert len(calls) == 1

    asyncio.run(scenario())


def test_command_idempotency_reserves_inflight_work_atomically(paired_stores):
    target = paired_stores["target"]
    controller_id = paired_stores["controller"].identity.device_id
    payload = {
        "command": "chats.send",
        "project_id": "project_1",
        "payload": {"message": "once"},
    }

    assert target.claim_dedupe(controller_id, "atomic_1", payload) == (
        "execute",
        None,
    )
    assert target.claim_dedupe(controller_id, "atomic_1", payload) == (
        "in_progress",
        None,
    )
    target.store_dedupe_result(
        controller_id,
        "atomic_1",
        payload,
        {"ok": True},
    )
    assert target.claim_dedupe(controller_id, "atomic_1", payload) == (
        "duplicate",
        {"ok": True},
    )
    with pytest.raises(ValueError, match="different payload"):
        target.claim_dedupe(
            controller_id,
            "atomic_1",
            {**payload, "payload": {"message": "different"}},
        )


def test_encrypted_grant_updates_and_revocation_propagate(paired_stores):
    async def scenario():
        target = paired_stores["target"]
        controller = paired_stores["controller"]
        relay = InMemoryRemoteRelay()

        async def unused_handler(*_args):
            return {"ok": True}

        target_gateway = RemoteGateway(target, relay, unused_handler)
        controller_gateway = RemoteGateway(controller, relay, unused_handler)
        await target_gateway.start()
        await controller_gateway.start()
        try:
            target.update_peer_grant(
                controller.identity.device_id,
                capabilities=["task:read"],
                project_scopes=["project_2"],
            )
            await target_gateway.notify_grant_update(
                controller.identity.device_id
            )
            synchronized = controller.get_peer(target.identity.device_id)
            assert synchronized is not None
            assert synchronized["received_capabilities"] == sorted(
                BASE_REMOTE_CAPABILITIES
            )
            assert synchronized["received_project_scopes"] == ["project_2"]

            await target_gateway.notify_revocation(
                controller.identity.device_id
            )
            target.revoke_peer(controller.identity.device_id)
            assert controller.get_peer(target.identity.device_id) is None
        finally:
            await controller_gateway.stop()
            await target_gateway.stop()

    asyncio.run(scenario())


def test_envelope_rejects_tampering_and_replay(paired_stores):
    target = paired_stores["target"]
    controller = paired_stores["controller"]
    target_peer = target.get_peer(controller.identity.device_id)
    controller_peer = controller.get_peer(target.identity.device_id)
    assert target_peer is not None and controller_peer is not None

    envelope = RemoteEnvelopeCodec.encode(
        identity=controller.identity,
        peer=controller_peer,
        kind="command",
        payload={"command": "projects.list"},
    )
    kind, payload = RemoteEnvelopeCodec.decode(
        identity=target.identity,
        peer=target_peer,
        envelope=envelope,
        mark_nonce=target.mark_nonce,
    )
    assert kind == "command"
    assert payload == {"command": "projects.list"}

    with pytest.raises(ValueError, match="replay"):
        RemoteEnvelopeCodec.decode(
            identity=target.identity,
            peer=target_peer,
            envelope=envelope,
            mark_nonce=target.mark_nonce,
        )

    tampered = RemoteEnvelopeCodec.encode(
        identity=controller.identity,
        peer=controller_peer,
        kind="command",
        payload={"command": "projects.list"},
    )
    tampered["ciphertext"] = tampered["ciphertext"][:-2] + "aa"
    with pytest.raises(ValueError, match="signature"):
        RemoteEnvelopeCodec.decode(
            identity=target.identity,
            peer=target_peer,
            envelope=tampered,
            mark_nonce=target.mark_nonce,
        )


def test_remote_settings_api_manages_identity_pairing_grants_and_audit(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    app = FastAPI()
    router = APIRouter()
    _register_remote_test_routes(
        router,
        app,
        str(tmp_path / "remote-api.sqlite3"),
        projects=[{"id": "project_1", "name": "Remote Project"}],
    )
    app.include_router(router)

    with TestClient(app) as client:
        initial = client.get("/api/remote/settings")
        assert initial.status_code == 200
        assert initial.json()["enabled"] is False
        assert initial.json()["identity"]["device_id"].startswith("dev_")
        assert initial.json()["projects"] == [
            {"id": "project_1", "name": "Remote Project"}
        ]

        updated = client.put(
            "/api/remote/settings",
            json={
                "enabled": True,
                "relay_url": "ws://127.0.0.1:9876",
                "device_name": "Living Room Mac",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["device_name"] == "Living Room Mac"

        invitation = client.post(
            "/api/remote/pairing/invitations",
            json={
                "capabilities": ["projects:list_shared", "chat:read"],
                "project_scopes": ["project_1"],
                "ttl_seconds": 60,
            },
        )
        assert invitation.status_code == 201
        assert invitation.json()["pairing_id"].startswith("pair_")

        audit = client.get("/api/remote/audit")
        assert audit.status_code == 200
        event_types = [event["event_type"] for event in audit.json()["events"]]
        assert "remote_settings_updated" in event_types
        assert "pairing_invitation_created" in event_types


def test_remote_device_catalog_projects_only_trusted_controller_grants(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    controller_db = str(tmp_path / "controller-context.sqlite3")
    target_db = str(tmp_path / "target-context.sqlite3")
    target = RemoteControlStore(target_db)
    controller = RemoteControlStore(controller_db)
    target.update_settings(
        enabled=True,
        relay_url="ws://127.0.0.1:9876",
        device_name="Target",
    )
    invitation = target.create_pairing_invitation(
        capabilities=["projects:list_shared", "chat:read"],
        project_scopes=["project_1"],
    )
    accepted = controller.accept_pairing_invitation(invitation["invitation"])
    target.complete_pairing_response(accepted["response"])

    app = FastAPI()
    router = APIRouter()
    _register_remote_test_routes(router, app, controller_db)
    app.include_router(router)
    with TestClient(app) as client:
        catalog = client.get("/api/remote/context-devices")
        assert catalog.status_code == 200
        assert catalog.json()["revision"] >= 1
        assert catalog.json()["devices"][0]["device_id"] == target.identity.device_id
        assert catalog.json()["devices"][0]["state"] == "ready"
        assert catalog.json()["devices"][0]["eligible"] is True

        removed_context_route = client.put(
            "/api/workbench/chats/chat_1/remote-context",
            json={"device_ids": [target.identity.device_id]},
        )
        assert removed_context_route.status_code == 404


def test_real_websocket_relay_connects_two_encrypted_gateways(paired_stores):
    async def scenario():
        from websockets.asyncio.server import serve

        relay_server = CyreneRelayServer()
        async with serve(relay_server.handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            relay_url = f"ws://127.0.0.1:{port}"
            target = paired_stores["target"]
            controller = paired_stores["controller"]
            target_relay = WebSocketRemoteRelay(relay_url)
            controller_relay = WebSocketRemoteRelay(relay_url)
            received = []

            async def target_handler(peer_id, command, payload, project_id):
                received.append((peer_id, command, payload, project_id))
                return {"ok": True, "projects": [{"id": "project_1"}]}

            async def controller_handler(peer_id, command, payload, project_id):
                raise AssertionError("controller must not receive a command")

            target_gateway = RemoteGateway(
                target,
                target_relay,
                target_handler,
            )
            controller_gateway = RemoteGateway(
                controller,
                controller_relay,
                controller_handler,
            )
            await target_gateway.start()
            await controller_gateway.start()
            try:
                await target_relay.wait_connected(timeout=3)
                await controller_relay.wait_connected(timeout=3)
                result = await controller_gateway.request(
                    target.identity.device_id,
                    command="projects.list",
                    idempotency_key="projects_1",
                    payload={},
                    timeout=3,
                )
            finally:
                await controller_gateway.stop()
                await target_gateway.stop()

        assert result == {
            "ok": True,
            "projects": [{"id": "project_1"}],
        }
        assert received == [
            (
                controller.identity.device_id,
                "projects.list",
                {},
                "",
            )
        ]

    asyncio.run(scenario())


def test_websocket_relay_rejects_unsigned_device_registration():
    async def scenario():
        import json

        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve
        from websockets.exceptions import ConnectionClosed

        relay_server = CyreneRelayServer()
        async with serve(relay_server.handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(
                f"ws://127.0.0.1:{port}",
                proxy=None,
            ) as connection:
                await connection.send(
                    json.dumps(
                        {
                            "type": "register",
                            "protocol_version": 2,
                            "device_id": "dev_unsigned",
                        }
                    )
                )
                with pytest.raises(ConnectionClosed):
                    await connection.recv()
                assert connection.close_code == 1008

    asyncio.run(scenario())


def test_websocket_relay_reports_offline_recipient_without_request_timeout(
    paired_stores,
):
    async def scenario():
        from websockets.asyncio.server import serve

        relay_server = CyreneRelayServer()
        async with serve(relay_server.handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            controller = paired_stores["controller"]

            async def unused_handler(*_args):
                return {"ok": True}

            gateway = RemoteGateway(
                controller,
                WebSocketRemoteRelay(f"ws://127.0.0.1:{port}"),
                unused_handler,
            )
            await gateway.start()
            try:
                await gateway.relay.wait_connected(timeout=3)
                with pytest.raises(ConnectionError, match="offline"):
                    await gateway.request(
                        paired_stores["target"].identity.device_id,
                        command="projects.list",
                        idempotency_key="offline_1",
                        payload={},
                        timeout=10,
                    )
            finally:
                await gateway.stop()

    asyncio.run(scenario())


def test_websocket_relay_enforces_per_connection_message_rate(
    paired_stores,
):
    async def scenario():
        import base64
        import json
        import os
        import time

        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve
        from websockets.exceptions import ConnectionClosed

        identity = paired_stores["controller"].identity
        relay_server = CyreneRelayServer(max_messages_per_second=1)
        async with serve(relay_server.handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(
                f"ws://127.0.0.1:{port}",
                proxy=None,
            ) as connection:
                registration = {
                    "type": "register",
                    "protocol_version": 2,
                    "device_id": identity.device_id,
                    "signing_public_key": identity.signing_public_key,
                    "timestamp": int(time.time()),
                    "nonce": base64.urlsafe_b64encode(
                        os.urandom(18)
                    ).decode("ascii").rstrip("="),
                }
                canonical = json.dumps(
                    registration,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                registration["signature"] = base64.urlsafe_b64encode(
                    identity.signing_private_key.sign(canonical)
                ).decode("ascii").rstrip("=")
                await connection.send(json.dumps(registration))
                acknowledged = json.loads(await connection.recv())
                assert acknowledged["type"] == "registered"

                await connection.send("{}")
                await connection.send("{}")
                with pytest.raises(ConnectionClosed):
                    await connection.recv()
                assert connection.close_code == 1013

    asyncio.run(scenario())


def test_remote_relay_requires_tls_except_on_localhost():
    with pytest.raises(ValueError, match="wss"):
        WebSocketRemoteRelay("ws://relay.example.test/v1")
    WebSocketRemoteRelay("wss://relay.example.test/v1")
    WebSocketRemoteRelay("ws://127.0.0.1:9876")


def test_mobile_model_copy_exports_api_key_but_never_codex_oauth(monkeypatch):
    from cyrene.plugins.builtin.cyrene_remote import commands as commands_module

    graph = {
        "version": 10,
        "connections": [
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "adapter": "openai",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "mobile-copy-key",
                "options": {"provider_preset": "deepseek"},
            },
            {
                "id": "codex",
                "name": "Codex",
                "adapter": "codex_oauth",
                "base_url": "codex://oauth",
                "api_key": "",
                "options": {"provider_preset": "codex_oauth"},
            },
        ],
        "profiles": [],
        "routes": {
            "primary": [],
            "secondary": [],
            "vision": [],
            "embedding": [],
        },
    }
    service = SimpleNamespace(get_model_configuration=lambda: dict(graph))
    host = SimpleNamespace(
        service=lambda name: service if name == "model_configuration" else None
    )
    monkeypatch.setattr(commands_module, "application_plugin_scope", lambda: host)

    copied = RemoteCommandExecutor._settings_models_copy({})["models"]

    connections = {item["id"]: item for item in copied["connections"]}
    assert connections["deepseek"]["api_key"] == "mobile-copy-key"
    assert connections["codex"]["api_key"] == ""

    with pytest.raises(ValueError, match="does not accept fields"):
        RemoteCommandExecutor._settings_models_copy({"unexpected": True})


def test_mobile_chat_contract_preserves_workbench_activity_timeline():
    detail = _chat_detail({
        "id": "chat_1",
        "messages": [{
            "id": "activity_1",
            "role": "assistant",
            "content": "",
            "createdAt": "2026-08-02T08:00:00Z",
            "activityCard": True,
            "intermediate": True,
            "reasoning": "Inspecting the repository",
            "trace": [{"toolCallId": "call_1", "text": "read_file"}],
        }],
    })

    message = detail["messages"][0]
    assert message["activityCard"] is True
    assert message["intermediate"] is True
    assert message["reasoning"] == "Inspecting the repository"
    assert message["trace"][0]["toolCallId"] == "call_1"


def test_mobile_run_contract_exposes_workbench_reasoning_and_tool_lifecycle():
    reasoning = public_remote_event({
        "_seq": 3,
        "type": "reasoning_delta",
        "delta": "Checking",
        "phase": "phase2",
        "provider": "openai",
    })
    tool = public_remote_event({
        "_seq": 4,
        "type": "tool_call_started",
        "tool_call_id": "call_1",
        "tool": "read_file",
        "args": {"path": "/workspace/app.py"},
    })

    assert reasoning == {
        "type": "reasoning_delta",
        "cursor": 3,
        "run_id": "",
        "delta": "Checking",
        "phase": "phase2",
        "provider": "openai",
    }
    assert tool["tool_call_id"] == "call_1"
    assert tool["tool"] == "read_file"
    assert tool["args"] == {"path": "/workspace/app.py"}
