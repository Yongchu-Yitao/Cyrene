"""Conversation approvals cover sequential deletions without becoming global."""

import asyncio
import threading
from unittest.mock import patch

import pytest

from cyrene.core.plugin import Plugin, PluginContext, PluginPack, PluginRegistry
from cyrene.core.plugin.core_impl.permission_boundaries import bash_boundary
from cyrene.core.plugin.permission_grants import PermissionGrants
from cyrene.core.session import AgentSession
from cyrene.plugins.builtin.cyrene_remote.permission import remote_permission_request


@pytest.mark.parametrize("answer", ["允许这次", "本次会话内总是允许", "拒绝"])
def test_sequential_deletions_respect_once_or_session_and_restore(tmp_path, answer):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("first.txt", "second.txt"):
        (workspace / name).write_text("temporary test file")
    commands = iter(["rm first.txt", "rm first.txt", "rm second.txt"])
    calls = 0

    async def model(_arguments, _context):
        nonlocal calls
        command = next(commands, None)
        calls += 1
        if command is None:
            return {"content": "done", "tool_calls": []}
        return {"content": "", "tool_calls": [{
            "id": f"delete-{calls}", "name": "Bash",
            "arguments": {"command": command},
        }]}

    registry = PluginRegistry()
    registry.register_pack(PluginPack("model", "model", (
        Plugin("MiniMax", "fake", {"type": "object"}, model, kind="model"),
    )), source="test")
    plugin_directory = tmp_path / "plugins"
    plugin_directory.mkdir()

    def open_session(tree_id="deletions"):
        return AgentSession(
            tmp_path / "data", workspace, plugin_directory,
            registry=registry, tree_id=tree_id,
        )

    session = open_session()
    try:
        session.submit("Delete the temporary files", run_id="delete-run")
        asyncio.run(session.drain())
        pending = session.pending_output()
        assert pending["kind"] == "destructive_confirmation"
        assert (workspace / "first.txt").exists()
        if answer == "本次会话内总是允许":
            with patch.object(session, "_permission_fingerprint", side_effect=AssertionError(
                "Session-approved deletions must not serialize or hash the full invocation"
            )):
                session.answer(pending["id"], answer)
                asyncio.run(session.drain())
        else:
            session.answer(pending["id"], answer)
            asyncio.run(session.drain())
        if answer == "拒绝":
            assert (workspace / "first.txt").exists()
            assert (workspace / "second.txt").exists()
            assert session.pending_output()["kind"] == "destructive_confirmation"
            return
        assert not (workspace / "first.txt").exists()
        if answer == "允许这次":
            assert (workspace / "second.txt").exists()
            assert session.pending_output()["kind"] == "destructive_confirmation"
            return
        assert not (workspace / "second.txt").exists()
        assert session.pending_output() is None
        assert session.final_output("delete-run")["content"] == "done"
    finally:
        session.close()

    def request(session, command, **overrides):
        arguments = {"command": command}
        boundary = bash_boundary(arguments, PluginContext(workspace=workspace))
        return session.request_permission(
            tool_name="Bash", arguments=arguments,
            request={**boundary, **overrides},
        )

    restored = open_session()
    try:
        with patch.object(restored, "_permission_fingerprint", side_effect=AssertionError(
            "Restored session grants must also skip exact fingerprints"
        )):
            assert request(restored, "rm third.txt") is None
        assert request(restored, "git reset --hard")["status"] == "awaiting_user"
        assert request(restored, "rm fourth.txt", single_use=True)["status"] == "awaiting_user"
    finally:
        restored.close()
    separate = open_session("another-conversation")
    try:
        assert request(separate, "rm third.txt")["status"] == "awaiting_user"
    finally:
        separate.close()


def test_remote_delete_scope_changes_with_device_project_and_operation(tmp_path):
    context = PluginContext(workspace=tmp_path)

    def request(path, operation="delete", device="one", project="project"):
        return remote_permission_request(
            "RemoteCyreneFiles", {"operation": operation, "remote_path": path},
            context, device_id=device, project_id=project,
        )[0]

    grants = PermissionGrants(threading.RLock())
    original = request("first.txt")
    scope = grants.session_fingerprint("RemoteCyreneFiles", original)
    assert scope
    grants.session.add(scope)
    next_delete = request("second", operation="delete_tree")
    assert grants.consume(
        grants.fingerprint("RemoteCyreneFiles", {"remote_path": "second"}, next_delete),
        grants.session_fingerprint("RemoteCyreneFiles", next_delete),
    )
    for other in (
        request("third", device="two"), request("third", project="another"),
        request("third", operation="sync"),
    ):
        assert not grants.consume("different-call", grants.session_fingerprint("RemoteCyreneFiles", other))
    assert not grants.consume("different-tool", grants.session_fingerprint("OtherTool", original))


def test_remote_harness_delete_scope_keeps_capability_and_operation_separate(tmp_path):
    context = PluginContext(workspace=tmp_path)

    def scope(command, capability="shell.run"):
        request, destructive = remote_permission_request(
            "RemoteHarness", {
                "operation": "invoke", "capability_id": capability,
                "arguments": {"command": command},
            }, context, device_id="device", project_id="project",
        )
        assert destructive
        return PermissionGrants.session_fingerprint("RemoteHarness", request)

    assert scope("rm first.txt") == scope("rm second.txt")
    assert scope("rm first.txt") != scope("git reset --hard")
    assert scope("rm first.txt") != scope("rm first.txt", "other.run")


def test_legacy_exact_session_grants_do_not_expand_and_single_use_has_no_scope(tmp_path):
    context = PluginContext(workspace=tmp_path)
    grants = PermissionGrants(threading.RLock())
    first = {"command": "rm first.txt"}
    second = {"command": "rm second.txt"}
    request = bash_boundary(first, context)
    grants.session.add(grants.fingerprint("Bash", first, request))
    assert grants.consume(grants.fingerprint("Bash", first, request))
    assert not grants.consume(
        grants.fingerprint("Bash", second, bash_boundary(second, context)),
        grants.session_fingerprint("Bash", request),
    )
    assert grants.session_fingerprint("Bash", {**request, "single_use": True}) == ""
    assert grants.session_fingerprint("Write", {"kind": "write_permission_request"}) == ""
    different_workspace = bash_boundary(first, PluginContext(workspace=tmp_path / "other"))
    assert grants.session_fingerprint("Bash", request) != grants.session_fingerprint("Bash", different_workspace)
