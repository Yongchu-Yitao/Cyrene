from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys

import pytest

from agent.plugin import PluginContext
from cyrene.runtime.remote_control import DEFAULT_REMOTE_CAPABILITIES, RemoteControlStore
from cyrene.runtime.remote_commands import RemoteCommandExecutor
from cyrene.runtime.remote_workspace import RemoteJobManager, RemoteWorkspaceFiles


@pytest.fixture
def paired_stores(monkeypatch, tmp_path):
    monkeypatch.setenv("CYRENE_REMOTE_KEYRING", "0")
    target = RemoteControlStore(str(tmp_path / "target.sqlite3"))
    controller = RemoteControlStore(str(tmp_path / "controller.sqlite3"))
    target.update_settings(enabled=True, relay_url="", device_name="Target")
    invitation = target.create_pairing_invitation(
        capabilities=list(DEFAULT_REMOTE_CAPABILITIES),
        project_scopes=["project_1"],
    )
    accepted = controller.accept_pairing_invitation(invitation["invitation"])
    target.complete_pairing_response(accepted["response"])
    return {"target": target, "controller": controller}


def _bind_workspace(monkeypatch, workspace):
    monkeypatch.setattr(
        "cyrene.runtime.remote_workspace.find_workbench_project_lightweight",
        lambda project_id: {"id": project_id, "workspacePath": str(workspace)},
    )
    monkeypatch.setattr(
        "cyrene.runtime.remote_workspace.resolve_project_workspace_dir",
        lambda _project: str(workspace),
    )


def test_remote_workspace_upload_resume_hash_atomic_commit_and_scope(
    paired_stores,
    monkeypatch,
    tmp_path,
):
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _bind_workspace(monkeypatch, workspace)
        target = paired_stores["target"]
        controller_id = paired_stores["controller"].identity.device_id
        files = RemoteWorkspaceFiles(target)
        content = (b"Cyrene remote file channel\n" * 30_000) + b"done"
        digest = hashlib.sha256(content).hexdigest()

        begin = await files.execute(
            controller_id,
            "files.upload.begin",
            "project_1",
            {
                "transfer_id": "transfer_test",
                "path": "nested/payload.bin",
                "size": len(content),
                "sha256": digest,
                "conflict_policy": "fail",
            },
        )
        assert begin["offset"] == 0

        first = content[:400_000]
        written = await files.execute(
            controller_id,
            "files.upload.chunk",
            "project_1",
            {
                "transfer_id": "transfer_test",
                "offset": 0,
                "chunk_sha256": hashlib.sha256(first).hexdigest(),
                "content_base64": base64.b64encode(first).decode(),
            },
        )
        assert written["next_offset"] == len(first)
        resumed = await files.execute(
            controller_id,
            "files.upload.begin",
            "project_1",
            {
                "transfer_id": "transfer_test",
                "path": "nested/payload.bin",
                "size": len(content),
                "sha256": digest,
                "conflict_policy": "fail",
            },
        )
        assert resumed == {
            "ok": True,
            "transfer_id": "transfer_test",
            "offset": len(first),
            "chunk_bytes": 512 * 1024,
            "resumed": True,
            "state": "active",
        }

        rest = content[len(first):]
        await files.execute(
            controller_id,
            "files.upload.chunk",
            "project_1",
            {
                "transfer_id": "transfer_test",
                "offset": len(first),
                "chunk_sha256": hashlib.sha256(rest).hexdigest(),
                "content_base64": base64.b64encode(rest).decode(),
            },
        )
        committed = await files.execute(
            controller_id,
            "files.upload.commit",
            "project_1",
            {"transfer_id": "transfer_test"},
        )
        assert committed["sha256"] == digest
        assert (workspace / "nested" / "payload.bin").read_bytes() == content

        downloaded = bytearray()
        offset = 0
        while True:
            chunk = await files.execute(
                controller_id,
                "files.download",
                "project_1",
                {"path": "nested/payload.bin", "offset": offset, "limit": 128_000},
            )
            raw = base64.b64decode(chunk["content_base64"])
            assert hashlib.sha256(raw).hexdigest() == chunk["chunk_sha256"]
            downloaded.extend(raw)
            offset = chunk["next_offset"]
            if chunk["eof"]:
                assert chunk["sha256"] == ""
                break
        assert bytes(downloaded) == content

        explicit_hash = await files.execute(
            controller_id,
            "files.download",
            "project_1",
            {"path": "nested/payload.bin", "offset": 0, "limit": 1, "include_hash": True},
        )
        assert explicit_hash["sha256"] == digest

        with pytest.raises(ValueError, match="project-relative|escapes"):
            await files.execute(
                controller_id,
                "files.stat",
                "project_1",
                {"path": "../outside.txt"},
            )

        outside = tmp_path / "device-wide.txt"
        outside.write_text("full access")
        with pytest.raises(ValueError, match="absolute remote paths require"):
            await files.execute(
                controller_id,
                "files.stat",
                "project_1",
                {"path": str(outside)},
            )
        absolute = await files.execute(
            controller_id,
            "files.stat",
            "project_1",
            {"path": str(outside), "include_hash": True},
            allow_outside=True,
        )
        assert absolute["entry"]["path"] == str(outside)
        assert absolute["entry"]["sha256"] == hashlib.sha256(b"full access").hexdigest()

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_remote_download_accumulates_digest_without_final_file_rescan(
    monkeypatch,
    tmp_path,
):
    from agent.plugin.plugin_impl.cyrene_remote import files as remote_files_tool

    content = b"streamed remote payload"
    expected = hashlib.sha256(content).hexdigest()
    requests: list[tuple[str, dict]] = []

    async def fake_request(
        _args,
        _context,
        *,
        command,
        payload,
        key=None,
    ):
        del key
        requests.append((command, dict(payload)))
        if command == "files.stat":
            return {
                "ok": True,
                "entry": {"kind": "file", "size": len(content), "sha256": expected},
            }
        offset = int(payload["offset"])
        chunk = content[offset:offset + 7]
        next_offset = offset + len(chunk)
        return {
            "ok": True,
            "offset": offset,
            "next_offset": next_offset,
            "size": len(content),
            "eof": next_offset >= len(content),
            "sha256": "",
            "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
            "content_base64": base64.b64encode(chunk).decode(),
        }

    async def no_progress(**_kwargs):
        return None

    async def forbidden_hash(_path):
        pytest.fail("completed remote downloads must use the streaming digest")

    monkeypatch.setattr(remote_files_tool, "DATA_DIR", tmp_path)
    monkeypatch.setattr(remote_files_tool, "_request", fake_request)
    monkeypatch.setattr(remote_files_tool, "publish_tool_progress", no_progress)
    monkeypatch.setattr(remote_files_tool, "_hash_file", forbidden_hash)
    monkeypatch.setattr(
        remote_files_tool,
        "register_generated_attachment",
        lambda path, display_name=None: {"path": path, "name": display_name},
    )

    result = await remote_files_tool._download(
        {"device_id": "device", "project_id": "project", "remote_path": "payload.bin"},
        PluginContext(),
    )

    assert result["sha256"] == expected
    assert requests[0] == (
        "files.stat",
        {"path": "payload.bin", "include_hash": True},
    )
    assert all("include_hash" not in payload for command, payload in requests if command == "files.download")


@pytest.mark.asyncio
async def test_remote_sync_upload_reuses_stable_manifest_digest(monkeypatch, tmp_path):
    from agent.plugin.plugin_impl.cyrene_remote import files as remote_files_tool

    source = tmp_path / "stable.bin"
    source.write_bytes(b"stable manifest bytes")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    identity = remote_files_tool._file_identity(source)
    commands: list[str] = []

    async def fake_request(
        _args,
        _context,
        *,
        command,
        payload,
        key=None,
    ):
        del key
        commands.append(command)
        if command == "files.upload.begin":
            assert payload["sha256"] == expected
            return {"ok": True, "offset": 0}
        if command == "files.upload.chunk":
            return {
                "ok": True,
                "offset": payload["offset"],
                "next_offset": payload["offset"] + len(base64.b64decode(payload["content_base64"])),
            }
        return {"ok": True, "sha256": expected}

    async def no_progress(**_kwargs):
        return None

    async def forbidden_hash(_path):
        pytest.fail("stable sync manifests must not be hashed again before upload")

    monkeypatch.setattr(remote_files_tool, "_request", fake_request)
    monkeypatch.setattr(remote_files_tool, "publish_tool_progress", no_progress)
    monkeypatch.setattr(remote_files_tool, "_hash_file", forbidden_hash)

    result = await remote_files_tool._upload_file(
        {"device_id": "device", "project_id": "project"},
        PluginContext(),
        source,
        "stable.bin",
        conflict_policy="overwrite",
        known_sha256=expected,
        known_identity=identity,
    )

    assert result["sha256"] == expected
    assert commands == ["files.upload.begin", "files.upload.chunk", "files.upload.commit"]


def test_remote_directory_sync_diff_and_delete_are_typed(
    paired_stores,
    monkeypatch,
    tmp_path,
):
    async def scenario():
        workspace = tmp_path / "workspace"
        (workspace / "dataset").mkdir(parents=True)
        (workspace / "dataset" / "old.txt").write_text("old")
        (workspace / "dataset" / "same.txt").write_text("same")
        _bind_workspace(monkeypatch, workspace)
        files = RemoteWorkspaceFiles(paired_stores["target"])
        controller_id = paired_stores["controller"].identity.device_id
        same_hash = hashlib.sha256(b"same").hexdigest()
        diff = await files.execute(
            controller_id,
            "files.sync.prepare",
            "project_1",
            {
                "path": "dataset",
                "entries": [
                    {"path": "dataset/same.txt", "kind": "file", "sha256": same_hash},
                    {"path": "dataset/new.txt", "kind": "file", "sha256": hashlib.sha256(b"new").hexdigest()},
                ],
            },
        )
        assert diff["upload"] == ["dataset/new.txt"]
        assert diff["delete"] == ["dataset/old.txt"]
        applied = await files.execute(
            controller_id,
            "files.sync.apply",
            "project_1",
            {
                "sync_id": diff["sync_id"],
                "delete": diff["delete"],
                "delete_extraneous": True,
            },
        )
        assert applied["deleted"] == ["dataset/old.txt"]
        assert not (workspace / "dataset" / "old.txt").exists()

    asyncio.run(scenario())


def test_absolute_paths_require_exact_controller_receipt(
    paired_stores,
    monkeypatch,
    tmp_path,
):
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("device scope")
        _bind_workspace(monkeypatch, workspace)
        target = paired_stores["target"]
        executor = RemoteCommandExecutor(
            store=target,
        )
        payload = {"path": str(outside), "include_hash": True}
        arguments = {
            "device_id": target.identity.device_id,
            "project_id": "project_1",
            "command": "files.stat",
            "payload": payload,
        }
        digest = hashlib.sha256(
            json.dumps(
                arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        authorized = {
            **payload,
            "_authorization": {
                "version": 1,
                "approved": True,
                "permission_mode": "full_access",
                "scope": "single_operation",
                "outside_workspace": True,
                "arguments_sha256": digest,
            },
        }
        result = await executor(
            paired_stores["controller"].identity.device_id,
            "files.stat",
            authorized,
            "project_1",
        )
        assert result["entry"]["path"] == str(outside)

        tampered = {
            **authorized,
            "path": str(tmp_path / "different.txt"),
            "_authorization": dict(authorized["_authorization"]),
        }
        with pytest.raises(PermissionError, match="exact controller authorization"):
            await executor(
                paired_stores["controller"].identity.device_id,
                "files.stat",
                tampered,
                "project_1",
            )

    asyncio.run(scenario())


def test_remote_job_lifecycle_logs_artifacts_and_completion_event(
    paired_stores,
    monkeypatch,
    tmp_path,
):
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _bind_workspace(monkeypatch, workspace)
        events = []
        jobs = RemoteJobManager(paired_stores["target"])

        async def send_event(peer_id, event):
            events.append((peer_id, event))

        jobs.set_event_sender(send_event)
        controller_id = paired_stores["controller"].identity.device_id
        started = await jobs.execute(
            controller_id,
            "jobs.start",
            "project_1",
            {
                "job_id": "job_test",
                "origin_chat_id": "chat_local",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; print('training complete'); Path('result.txt').write_text('ok')",
                ],
                "artifact_paths": ["result.txt"],
            },
        )
        assert started["status"] in {"running", "completed"}
        waited = await jobs.execute(
            controller_id,
            "jobs.wait",
            "project_1",
            {"job_id": "job_test", "timeout_seconds": 10, "cursor": 0},
        )
        assert waited["status"] == "completed"
        assert waited["exit_code"] == 0
        assert "training complete" in waited["output"]
        artifacts = await jobs.execute(
            controller_id,
            "jobs.artifacts",
            "project_1",
            {"job_id": "job_test"},
        )
        assert artifacts["artifacts"][0]["path"] == "result.txt"
        assert events[0][1] == {
            "type": "remote_job_update",
            "session_id": "chat_local",
            "project_id": "project_1",
            "job_id": "job_test",
            "status": "completed",
            "exit_code": 0,
        }

    asyncio.run(scenario())
