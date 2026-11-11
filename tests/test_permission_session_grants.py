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
                session.answer(pending["id"], {"允许这次": "allow_once", "本次会话内总是允许": "allow_session", "拒绝": "deny"}[answer])
                asyncio.run(session.drain())
        else:
            session.answer(pending["id"], {"允许这次": "allow_once", "本次会话内总是允许": "allow_session", "拒绝": "deny"}[answer])
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


@pytest.mark.parametrize("command", [
    "rm -f .cyrene/scratch/check.mjs .cyrene/scratch/screenshot.png",
    "cd '{scratch}' && rm -f check.mjs screenshot.png",
    "rm -rf .cyrene/scratch/temporary-preview",
])
def test_scratch_cleanup_routes_to_automatic_review(tmp_path, command):
    from datetime import datetime, timezone
    from cyrene.core.hook import HookEvent, PRE_TOOL_USE
    from test_task_contexts import make_session

    scratch = tmp_path / ".cyrene" / "scratch"
    scratch.mkdir(parents=True)
    command = command.format(scratch=scratch)
    request = bash_boundary({"command": command}, PluginContext(workspace=tmp_path))
    assert request["kind"] == "scratch_cleanup"
    assert request["requires_human"] is False
    assert command in request["reason"]
    session = make_session(tmp_path)
    try:
        session._plugin_context_data["run_context"] = {"permission_mode": "auto"}
        requirement = session._permission_requirement(HookEvent(
            PRE_TOOL_USE, session.tree.id, datetime.now(timezone.utc),
            payload={"tool": {"name": "Bash", "arguments": {"command": command}}, "permission": request},
        ))
        assert requirement.action == "review"
    finally:
        session.close()


@pytest.mark.parametrize("command", [
    "rm -f .cyrene/scratch/check.mjs index.html",
    "rm -rf .cyrene/scratch",
    "rm -f .cyrene/scratch/../../index.html",
    "cd .cyrene/scratch && rm -f ~/important.txt",
    "cd .cyrene/scratch && rm -f *.png",
    "cd .cyrene/scratch && rm -f check.mjs; rm index.html",
    "rm -f .cyrene/scratch/link/important.txt",
])
def test_unbounded_or_deliverable_deletions_still_require_confirmation(tmp_path, command):
    scratch = tmp_path / ".cyrene" / "scratch"
    scratch.mkdir(parents=True)
    outside = tmp_path / "user-files"
    outside.mkdir()
    (scratch / "link").symlink_to(outside, target_is_directory=True)
    request = bash_boundary({"command": command}, PluginContext(workspace=tmp_path))
    assert request["kind"] == "destructive_confirmation"
    assert request["requires_human"] is True


@pytest.mark.parametrize("style", ["absolute_cd", "relative", "absolute"])
def test_nested_project_scratch_cleanup_matches_installed_task_layout(tmp_path, style):
    workspace = tmp_path / "Application Support" / "Cyrene" / "workspace"
    scratch = workspace / "split-bill" / ".cyrene" / "scratch"
    scratch.mkdir(parents=True)
    commands = {
        "absolute_cd": f'cd "{scratch}" && rm -f capture-final.mjs final-numbers.mjs desktop-1280.png',
        "relative": "rm -f split-bill/.cyrene/scratch/capture-final.mjs",
        "absolute": f'rm -f "{scratch}/capture-final.mjs"',
    }
    request = bash_boundary({"command": commands[style]}, PluginContext(workspace=workspace))
    assert request["kind"] == "scratch_cleanup"
    assert request["requires_human"] is False
    assert all(str(scratch) in path for path in request["paths"])


@pytest.mark.parametrize("escape", ["symlink_project", "outside_workspace", "scratch_root", "mixed_deliverable"])
def test_nested_scratch_cleanup_keeps_ownership_boundary(tmp_path, escape):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    (outside / ".cyrene" / "scratch").mkdir(parents=True)
    (workspace / "linked-project").symlink_to(outside, target_is_directory=True)
    commands = {
        "symlink_project": "rm -f linked-project/.cyrene/scratch/test.mjs",
        "outside_workspace": f'rm -f "{outside}/.cyrene/scratch/test.mjs"',
        "scratch_root": "rm -rf split-bill/.cyrene/scratch",
        "mixed_deliverable": "rm -f split-bill/.cyrene/scratch/test.mjs split-bill/index.html",
    }
    request = bash_boundary({"command": commands[escape]}, PluginContext(workspace=workspace))
    assert request["requires_human"] is True


def test_task_human_permission_is_blocked_without_pausing_or_deleting(tmp_path):
    from test_task_contexts import make_session

    target = tmp_path / "deliverable.txt"
    target.write_text("keep")
    calls = []

    async def model(arguments, context):
        calls.append(arguments)
        if len(calls) == 1:
            return {"content": "", "tool_calls": [{"id": "cleanup", "name": "Bash",
                    "arguments": {"command": "rm deliverable.txt"}}]}
        return {"content": "Kept the file and completed without optional cleanup.", "tool_calls": []}

    session = make_session(tmp_path, model)
    session._plugin_context_data["assistant_owner_session_id"] = "assistant_global"
    session._plugin_context_data["run_context"] = {"permission_mode": "auto"}
    try:
        session.submit("Finish the task", run_id="task-denial")
        asyncio.run(session.drain())
        assert target.read_text() == "keep"
        assert session.pending_output() is None
        assert session.final_output("task-denial")["content"].startswith("Kept the file")
        assert len(calls) == 2
        assert "Task permission request denied by default" in str(calls[1])
    finally:
        session.close()


@pytest.mark.parametrize("task,kind,expected", [
    (True, "destructive_confirmation", "deny"),
    (False, "destructive_confirmation", "confirm"),
    (True, "scratch_cleanup", "review"),
])
def test_task_denial_preserves_auto_review_and_parent_confirmation(tmp_path, task, kind, expected):
    from datetime import datetime, timezone
    from cyrene.core.hook import HookEvent, PRE_TOOL_USE
    from test_task_contexts import make_session

    session = make_session(tmp_path)
    session._plugin_context_data["assistant_owner_session_id"] = "assistant_global" if task else ""
    session._plugin_context_data["run_context"] = {"permission_mode": "auto"}
    try:
        result = session._permission_requirement(HookEvent(
            PRE_TOOL_USE, session.tree.id, datetime.now(timezone.utc),
            payload={"tool": {"name": "Bash", "arguments": {"command": "rm example"}},
                     "permission": {"kind": kind, "operation": "cleanup", "reason": "test"}},
        ))
        assert result.action == expected
    finally:
        session.close()


@pytest.mark.parametrize('answer', ['不同意', '始终拒绝', 'allow_once', 'deny'])
def test_untyped_legacy_options_cannot_grant(answer):
    grants = PermissionGrants(threading.RLock())
    grants.record_answer({'permission': {'fingerprint': 'call'},
                          'options': [{'id': 'allow_once', 'label': answer}]}, answer, None, None)
    assert not grants.consume('call')


def test_explicit_decision_survives_codec_and_does_not_infer_scope_from_label():
    from cyrene.core.plugin.result_codec import question_options
    grants = PermissionGrants(threading.RLock())
    pending = {'permission': {'fingerprint': 'call'}, 'options': question_options([
        {'id': 'once', 'label': '本次会话 始终', 'decision': 'allow', 'scope': 'once'},
        {'id': 'deny', 'label': '不同意', 'decision': 'deny', 'scope': 'none'},
    ])}
    for answer in ('不同意', 'deny', '本次会话 始终', 'unknown'):
        grants.record_answer(pending, answer, None, None)
        assert not grants.consume('call')
    grants.record_answer(pending, 'once', None, None)
    assert grants.consume('call')
    assert not grants.consume('call')
    assert not grants.session


def test_single_use_rejects_session_choice_even_if_stored_pending_is_malformed():
    grants = PermissionGrants(threading.RLock())
    grants.record_answer({'permission': {'fingerprint': 'call', 'single_use': True},
                          'options': [{'id': 'session', 'label': 'approve', 'decision': 'allow', 'scope': 'session'}]},
                         'session', None, None)
    assert not grants.consume('call')


def test_question_builder_rejects_untyped_custom_options():
    from cyrene.core.plugin.permission_grants import permission_question
    question = permission_question({'options': ['不同意']}, tool_name='Bash', operation='delete',
        kind='destructive_confirmation', requires_human=True, fingerprint='call',
        session_fingerprint='', round_id='', client_request_id='')
    assert question['options'] == [{'id': 'deny', 'label': '拒绝', 'decision': 'deny', 'scope': 'none'}]
