"""Device trust, encrypted gateway, and desktop remote-settings tests."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.runtime.remote_control import (
    DEFAULT_REMOTE_CAPABILITIES,
    InMemoryRemoteRelay,
    RemoteControlStore,
    RemoteEnvelopeCodec,
    RemoteGateway,
    WebSocketRemoteRelay,
    register_remote_gateway,
    unregister_remote_gateway,
)
from cyrene.runtime.remote_commands import RemoteCommandExecutor
from cyrene.runtime.remote_relay import CyreneRelayServer
from cyrene.runtime.remote_pairing import (
    DirectPairingServer,
    connect_by_address,
    normalize_pairing_address,
)
from cyrene.tool_impl.remote.list_devices import handler as list_remote_devices
from cyrene.tool_impl.remote.run import handler as run_remote_cyrene
from cyrene.tool_impl.remote.status import handler as remote_cyrene_status
from route.remote import register_remote_routes


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
        capabilities=["projects:list_shared", "chat:read", "chat:send"],
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
    assert target_peer["granted_capabilities"] == [
        "chat:read",
        "chat:send",
        "projects:list_shared",
    ]
    assert target_peer["granted_project_scopes"] == ["project_1"]
    assert controller_peer is not None
    assert controller_peer["received_capabilities"] == [
        "chat:read",
        "chat:send",
        "projects:list_shared",
    ]
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


def test_remote_store_migrates_out_of_runtime_database(monkeypatch, tmp_path):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    logical_path = tmp_path / "legacy-runtime.sqlite3"
    original = RemoteControlStore(str(logical_path))
    original.update_settings(
        enabled=True,
        relay_url="",
        device_name="Migrated device",
    )
    with sqlite3.connect(original.remote_db_path) as source:
        with sqlite3.connect(logical_path) as legacy:
            source.backup(legacy)
    for suffix in ("", "-wal", "-shm"):
        path = tmp_path / f"legacy-runtime.sqlite3.remote-control{suffix}"
        path.unlink(missing_ok=True)

    migrated = RemoteControlStore(str(logical_path))

    assert migrated.remote_db_path != migrated.db_path
    assert migrated.get_settings()["device_name"] == "Migrated device"
    with sqlite3.connect(migrated.remote_db_path) as conn:
        marker = conn.execute(
            """
            SELECT migration_id FROM remote_store_migrations
            WHERE migration_id = 'split_remote_control_store_v1'
            """
        ).fetchone()
    assert marker == ("split_remote_control_store_v1",)


def test_legacy_default_grants_gain_approval_response_only_when_untouched(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    target_path = str(tmp_path / "target-upgrade.sqlite3")
    controller_path = str(tmp_path / "controller-upgrade.sqlite3")
    target = RemoteControlStore(target_path)
    controller = RemoteControlStore(controller_path)
    target.update_settings(enabled=True, relay_url="", device_name="Target")
    invitation = target.create_pairing_invitation(
        project_scopes=["project_1"],
    )
    accepted = controller.accept_pairing_invitation(invitation["invitation"])
    target.complete_pairing_response(accepted["response"])
    legacy_caps = sorted(
        capability
        for capability in DEFAULT_REMOTE_CAPABILITIES
        if capability != "approval:respond"
    )
    with sqlite3.connect(controller.remote_db_path) as conn:
        conn.execute(
            """
            UPDATE remote_peers
            SET received_capabilities_json = ?
            WHERE device_id = ?
            """,
            (json.dumps(legacy_caps), target.identity.device_id),
        )

    reopened = RemoteControlStore(controller_path)
    peer = reopened.get_peer(target.identity.device_id)

    assert peer is not None
    assert "approval:respond" in peer["received_capabilities"]


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
        assert controller_peer["received_capabilities"] == ["chat:read", "chat:send"]
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
    assert persisted_controller_peer["received_capabilities"] == [
        "chat:read",
        "chat:send",
    ]
    assert persisted_target_peer is not None
    assert persisted_target_peer["lan_address"] == (
        f"127.0.0.1:{controller_port}"
    )
    assert persisted_target_peer["granted_project_scopes"] == ["project_1"]


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
    ) == (False, "capability_denied")
    assert target.authorize_inbound(
        controller_id, "chats.read", ""
    ) == (False, "project_scope_required")

    updated = target.update_peer_grant(
        controller_id,
        capabilities=["task:read"],
        project_scopes=["project_2"],
    )
    assert updated["granted_capabilities"] == ["task:read"]
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


def test_remote_executor_filters_projects_and_public_run_events(
    paired_stores,
    monkeypatch,
):
    async def scenario():
        target = paired_stores["target"]
        controller = paired_stores["controller"]
        monkeypatch.setattr(
            "cyrene.workbench.runtime._read_workbench_store_lightweight",
            lambda: {
                "projects": [
                    {"id": "project_1", "name": "Shared"},
                    {"id": "project_private", "name": "Private"},
                ]
            },
        )
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
            chat_adapter={"get_chat": get_chat, "run_manager": run_manager},
            project_adapter={},
            task_adapter={},
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
            "reply_done",
        ]
        assert events["events"][0] == {
            "type": "ack",
            "cursor": 1,
            "run_id": "run_shared",
            "chatId": "chat_shared",
        }
        assert events["events"][1]["response"] == "public answer"
        assert "debug" not in events["events"][1]
        assert events["next_cursor"] == 4
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
                "prompt": "Continue?",
                "debug": {"path": "/private/debug"},
            },
        }
        calls = {"chat_send": 0, "task_dispatch": 0}

        async def list_tasks(_project_id):
            return {"sessions": [task]}

        async def get_task(_task_id):
            return {"session": task}

        async def dispatch_task(_task_id, _body):
            calls["task_dispatch"] += 1
            return {"session": task}

        async def get_chat(_chat_id):
            return {
                "chat": {
                    "id": "chat_1",
                    "projectId": "project_1",
                    "messages": [],
                }
            }

        async def send_chat_detached(*_args, **_kwargs):
            calls["chat_send"] += 1
            return {"run_id": "run_1"}

        executor = RemoteCommandExecutor(
            store=target,
            chat_adapter={
                "get_chat": get_chat,
                "send_chat_detached": send_chat_detached,
            },
            project_adapter={"list_tasks": list_tasks},
            task_adapter={
                "get_task": get_task,
                "dispatch_task": dispatch_task,
            },
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
            "prompt": "Continue?",
        }

        with pytest.raises(ValueError, match="permission_mode"):
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
        with pytest.raises(ValueError, match="permission_mode"):
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
        assert calls == {"chat_send": 0, "task_dispatch": 0}

    asyncio.run(scenario())


def test_remote_command_reads_only_attachment_referenced_by_shared_chat(
    paired_stores,
    monkeypatch,
    tmp_path,
):
    async def scenario():
        from cyrene.runtime import attachments as managed_attachments

        exports = tmp_path / "exports"
        uploads = tmp_path / "uploads"
        exports.mkdir()
        uploads.mkdir()
        monkeypatch.setattr(managed_attachments, "EXPORTS_DIR", exports)
        monkeypatch.setattr(managed_attachments, "UPLOADS_DIR", uploads)
        screenshot = exports / "desktop.png"
        screenshot.write_bytes(b"remote-screenshot")
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
            chat_adapter={"get_chat": get_chat},
            project_adapter={},
            task_adapter={},
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
        from cyrene.tool_impl.remote import status as remote_status

        data_dir = tmp_path / "data"
        exports = data_dir / "exports"
        source = b"a" * (1024 * 1024 + 31)
        monkeypatch.setattr(cyrene_config, "DATA_DIR", data_dir)
        monkeypatch.setattr(managed_attachments, "EXPORTS_DIR", exports)

        async def fake_request(args, _db_path, *, fallback_chat_id):
            del fallback_chat_id
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
        raw = await remote_status.handler(
            {
                "command": "attachments.read",
                "project_id": "project_1",
                "payload": {
                    "chat_id": "chat_1",
                    "attachment_id": "large-result",
                },
            },
            None,
            1,
            str(tmp_path / "runtime.sqlite3"),
            None,
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
            assert synchronized["received_capabilities"] == ["task:read"]
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
    from cyrene.workbench import runtime as workbench_runtime

    monkeypatch.setattr(
        workbench_runtime,
        "_read_workbench_store_lightweight",
        lambda: {"projects": [{"id": "project_1", "name": "Remote Project"}]},
    )
    app = FastAPI()
    router = APIRouter()
    register_remote_routes(router, app, str(tmp_path / "remote-api.sqlite3"))
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


def test_remote_context_accepts_only_trusted_controller_grants(
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

    chats = {
        "chats": [
            {
                "id": "chat_1",
                "projectId": "local_project",
                "remoteDeviceIds": [],
            }
        ]
    }
    from cyrene.workbench import chat as chat_service

    monkeypatch.setattr(chat_service, "_read_chats_store", lambda: chats)
    monkeypatch.setattr(chat_service, "_write_chats_store", lambda payload: None)

    app = FastAPI()
    router = APIRouter()
    register_remote_routes(router, app, controller_db)
    app.include_router(router)
    with TestClient(app) as client:
        selected = client.put(
            "/api/workbench/chats/chat_1/remote-context",
            json={"device_ids": [target.identity.device_id]},
        )
        assert selected.status_code == 200
        assert selected.json()["device_ids"] == [target.identity.device_id]
        assert chats["chats"][0]["remoteDeviceIds"] == [
            target.identity.device_id
        ]

        rejected = client.put(
            "/api/workbench/chats/chat_1/remote-context",
            json={"device_ids": ["dev_unknown"]},
        )
        assert rejected.status_code == 400
        assert rejected.json()["code"] == "remote_context_device_invalid"


def test_agent_status_tool_only_controls_device_selected_in_chat(
    paired_stores,
    monkeypatch,
):
    async def scenario():
        target = paired_stores["target"]
        controller = paired_stores["controller"]
        relay = InMemoryRemoteRelay()

        async def target_handler(peer_id, command, payload, project_id):
            assert peer_id == controller.identity.device_id
            return {
                "ok": True,
                "command": command,
                "project_id": project_id,
                "payload": payload,
            }

        async def controller_handler(peer_id, command, payload, project_id):
            raise AssertionError("controller must not receive a command")

        target_gateway = RemoteGateway(target, relay, target_handler)
        controller_gateway = RemoteGateway(
            controller,
            relay,
            controller_handler,
        )
        chats = {
            "chats": [
                {
                    "id": "chat_local",
                    "projectId": "local_project",
                    "remoteDeviceIds": [target.identity.device_id],
                }
            ]
        }
        from cyrene.workbench import chat as chat_service

        monkeypatch.setattr(chat_service, "_read_chats_store", lambda: chats)
        await target_gateway.start()
        await controller_gateway.start()
        register_remote_gateway(controller.db_path, controller_gateway)
        try:
            listed = await list_remote_devices(
                {},
                None,
                "chat_local",
                controller.db_path,
                None,
            )
            status = await remote_cyrene_status(
                {
                    "device_id": target.identity.device_id,
                    "command": "projects.list",
                    "payload": {},
                },
                None,
                "chat_local",
                controller.db_path,
                None,
            )
            denied = await remote_cyrene_status(
                {
                    "device_id": "dev_not_selected",
                    "command": "projects.list",
                    "payload": {},
                },
                None,
                "chat_local",
                controller.db_path,
                None,
            )
        finally:
            unregister_remote_gateway(
                controller.db_path,
                controller_gateway,
            )
            await controller_gateway.stop()
            await target_gateway.stop()

        assert target.identity.device_id in listed
        assert '"command": "projects.list"' in status
        assert "未添加到当前对话上下文" in denied

    asyncio.run(scenario())


def test_run_remote_cyrene_creates_chat_and_starts_remote_agent(
    monkeypatch,
    tmp_path,
):
    async def scenario():
        monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
        target = RemoteControlStore(str(tmp_path / "run-target.sqlite3"))
        controller = RemoteControlStore(
            str(tmp_path / "run-controller.sqlite3")
        )
        target.update_settings(enabled=True, relay_url="", device_name="Target")
        controller.update_settings(
            enabled=True,
            relay_url="",
            device_name="Controller",
        )
        invitation = target.create_pairing_invitation(
            capabilities=["chat:create", "chat:send"],
            project_scopes=["project_1"],
        )
        accepted = controller.accept_pairing_invitation(
            invitation["invitation"]
        )
        target.complete_pairing_response(accepted["response"])
        relay = InMemoryRemoteRelay()
        received = []
        remote_chat = {
            "id": "chat_remote_1",
            "projectId": "project_1",
            "title": "Inspect remote desktop",
            "messages": [],
        }

        async def create_chat(body):
            received.append(("create", body.project, body.title))
            return {"ok": True, "chat": dict(remote_chat)}

        async def get_chat(chat_id):
            assert chat_id == remote_chat["id"]
            return {"ok": True, "chat": dict(remote_chat)}

        async def send_chat_detached(chat_id, body, *, detached):
            received.append(("send", chat_id, dict(body), detached))
            return {
                "run_id": "run_remote_1",
                "chat_id": chat_id,
                "status": "running",
                "created_at": "2026-07-27T08:00:00+00:00",
                "event_cursor": 0,
            }

        target_executor = RemoteCommandExecutor(
            store=target,
            chat_adapter={
                "create_chat": create_chat,
                "get_chat": get_chat,
                "send_chat_detached": send_chat_detached,
            },
            project_adapter={},
            task_adapter={},
        )

        async def controller_handler(*_args):
            return {"ok": True}

        chats = {
            "chats": [
                {
                    "id": "chat_local",
                    "projectId": "project_local",
                    "remoteDeviceIds": [target.identity.device_id],
                }
            ]
        }
        from cyrene.workbench import chat as chat_service

        monkeypatch.setattr(chat_service, "_read_chats_store", lambda: chats)

        async def allow_remote_action(**_kwargs):
            return None

        monkeypatch.setattr(
            "cyrene.tool_impl.remote.run.request_scope_elevation",
            allow_remote_action,
        )
        target_gateway = RemoteGateway(target, relay, target_executor)
        controller_gateway = RemoteGateway(
            controller,
            relay,
            controller_handler,
        )
        await target_gateway.start()
        await controller_gateway.start()
        register_remote_gateway(controller.db_path, controller_gateway)
        try:
            raw = await run_remote_cyrene(
                {
                    "device_id": target.identity.device_id,
                    "project_id": "project_1",
                    "title": "Inspect remote desktop",
                    "message": "Use your local tools to inspect the desktop.",
                    "permission_mode": "default",
                    "language": "en",
                    "idempotency_key": "remote_run_test_1",
                    "reason": "User requested remote work",
                },
                None,
                "chat_local",
                controller.db_path,
                None,
            )
        finally:
            unregister_remote_gateway(
                controller.db_path,
                controller_gateway,
            )
            await controller_gateway.stop()
            await target_gateway.stop()

        result = json.loads(raw)
        assert result["ok"] is True
        assert result["chat"]["id"] == "chat_remote_1"
        assert result["run_id"] == "run_remote_1"
        assert received[0] == (
            "create",
            "project_1",
            "Inspect remote desktop",
        )
        assert received[1][0:2] == ("send", "chat_remote_1")
        assert received[1][2] == {
            "message": "Use your local tools to inspect the desktop.",
            "mode": "default",
            "lang": "en",
            "stream": True,
        }
        assert received[1][3] is True

    asyncio.run(scenario())


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
                            "protocol_version": 1,
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
                    "protocol_version": 1,
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
