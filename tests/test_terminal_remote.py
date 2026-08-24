from __future__ import annotations

import asyncio
import shlex
import subprocess
from pathlib import Path

import pytest

from cyrene.terminal.client import TerminalDaemonClient
from cyrene.terminal.manager import TerminalManager, TerminalSession, _now_iso
from cyrene.terminal.remote import build_managed_ssh_launch
from cyrene.terminal.shell_integration import OscMetadataParser, prepare_shell_integration


def _session(tmp_path: Path, **overrides) -> TerminalSession:
    values = {
        "id": "term_remote",
        "project_id": "project-1",
        "title": "Remote",
        "cwd": str(tmp_path),
        "shell": "ssh",
        "argv": ["ssh", "example"],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "status": "running",
        "connection_kind": "ssh",
        "ssh_target": "example",
        "remote_cwd": "/home/user",
        "connection_status": "connected",
        "remote_connected": True,
    }
    values.update(overrides)
    return TerminalSession(**values)


def test_managed_ssh_launch_installs_integration_and_attaches_tmux() -> None:
    launch = build_managed_ssh_launch(
        target="deploy@example.com",
        remote_cwd="/srv/app",
        tmux_session="cyrene_app",
        ssh_executable="/usr/bin/ssh",
    )

    assert launch.argv[:2] == ["/usr/bin/ssh", "-tt"]
    assert launch.argv[-2] == "deploy@example.com"
    assert "shell-integration" in launch.argv[-1]
    assert "tmux new-session -A -s cyrene_app" in launch.argv[-1]
    assert "Lifecycle connected" in launch.argv[-1]
    assert len(launch.bundle_version) == 16
    command = shlex.split(launch.argv[-1])
    assert command[:3] == ["exec", "/bin/sh", "-c"]
    subprocess.run(
        ["/bin/sh", "-n", "-c", command[3]], check=True,
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("target", ["-oProxyCommand=x", "host name", ""])
def test_managed_ssh_launch_rejects_targets_that_can_become_options(target: str) -> None:
    with pytest.raises(ValueError):
        build_managed_ssh_launch(target=target, ssh_executable="ssh")


def test_shell_integration_uses_tmux_visible_pane_passthrough(tmp_path: Path) -> None:
    launch = prepare_shell_integration(
        shell="bash", argv=["/bin/bash"], env={}, runtime_dir=tmp_path,
    )
    source = Path(launch.env["CYRENE_SHELL_INTEGRATION_SCRIPT"]).read_text()

    assert "allow-passthrough on" in source
    assert "\\033Ptmux;" in source
    assert "allow-passthrough all" not in source
    subprocess.run(
        ["/bin/bash", "-n", str(launch.env["CYRENE_SHELL_INTEGRATION_SCRIPT"])],
        check=True, capture_output=True, text=True,
    )


def test_parser_reports_managed_remote_lifecycle_properties() -> None:
    parser = OscMetadataParser()
    data = (
        b"\x1b]133;P;Context=ssh\x07"
        b"\x1b]133;P;ProfileId=prod\x07"
        b"\x1b]133;P;Lifecycle=connected\x07"
    )

    events = parser.feed(data, start_seq=10)

    assert [(event["kind"], event["value"]) for event in events] == [
        ("context", "ssh"),
        ("profile", "prod"),
        ("lifecycle", "connected"),
    ]


def test_remote_osc7_updates_remote_cwd_without_touching_local_cwd(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)

    changed = TerminalManager._apply_osc_metadata(session, {
        "kind": "cwd",
        "value": "/tmp",
        "uri": "file://remote-host/tmp",
        "host": "remote-host",
    })

    assert changed is True
    assert session.cwd == str(tmp_path)
    assert session.remote_cwd == "/tmp"
    assert session.cwd_uri == "file://remote-host/tmp"


def test_unmanaged_remote_osc7_does_not_replace_local_cwd(tmp_path: Path) -> None:
    session = _session(
        tmp_path,
        connection_kind="local",
        ssh_target="",
        remote_cwd="",
        connection_status="local",
        remote_connected=False,
    )

    TerminalManager._apply_osc_metadata(session, {
        "kind": "cwd",
        "value": "/tmp",
        "uri": "file://remote-host/tmp",
        "host": "remote-host",
    })

    assert session.cwd == str(tmp_path)
    assert session.cwd_uri == "file://remote-host/tmp"


def test_remote_context_survives_manager_restart(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    manager = TerminalManager(state_dir=state_dir)
    session = _session(
        tmp_path,
        tmux_session="cyrene_app",
        disconnect_reason="transport_lost",
        reconnect_attempt=2,
        status="exited",
    )
    manager._sessions[session.id] = session
    manager._persist_session(session)
    manager.flush()
    manager.close_store()

    restored = TerminalManager(state_dir=state_dir)
    public = restored.get(session.id).public()

    assert public["connectionKind"] == "ssh"
    assert public["sshTarget"] == "example"
    assert public["remoteCwd"] == "/home/user"
    assert public["tmuxSession"] == "cyrene_app"
    assert public["disconnectReason"] == "transport_lost"
    assert public["reconnectAttempt"] == 2
    restored.close_store()


@pytest.mark.asyncio
async def test_transport_loss_reconnects_but_explicit_tmux_detach_does_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    manager = TerminalManager()
    reconnecting = _session(tmp_path)
    detached = _session(tmp_path, id="term_detached", tmux_session="work")
    manager._sessions[reconnecting.id] = reconnecting
    manager._sessions[detached.id] = detached
    monkeypatch.setattr(manager, "_schedule_remote_reconnect", lambda session: True)

    manager._mark_exited(reconnecting, 255)
    TerminalManager._apply_osc_metadata(detached, {
        "kind": "lifecycle", "value": "tmux_detached",
    })
    manager._mark_exited(detached, 0)

    assert reconnecting.status == "starting"
    assert reconnecting.connection_status == "reconnecting"
    assert reconnecting.disconnect_reason == "transport_lost"
    assert detached.status == "exited"
    assert detached.connection_status == "detached"
    assert detached.disconnect_reason == "tmux_detached"
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_connection_barrier_releases_only_after_remote_lifecycle(
    tmp_path: Path,
) -> None:
    manager = TerminalManager()
    session = _session(
        tmp_path,
        connection_status="connecting",
        remote_connected=False,
    )
    manager._sessions[session.id] = session

    waiting = asyncio.create_task(
        manager.wait_until_connected(session.id, timeout=1.0)
    )
    await asyncio.sleep(0)
    assert not waiting.done()

    TerminalManager._apply_osc_metadata(session, {
        "kind": "lifecycle", "value": "connected",
    })

    connected = await waiting
    assert connected["connectionStatus"] == "connected"


@pytest.mark.asyncio
async def test_agent_initial_command_waits_before_writing_to_managed_ssh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    client = TerminalDaemonClient(state_dir=tmp_path / "daemon")
    calls: list[tuple[str, dict]] = []
    ready = False

    monkeypatch.setattr(
        TerminalManager,
        "_resolve_cwd",
        classmethod(lambda cls, project_id, cwd="": tmp_path),
    )

    async def fake_request(action: str, **payload):
        nonlocal ready
        calls.append((action, payload))
        if action == "create":
            return {"terminal": {
                "id": "term_remote",
                "connectionKind": "ssh",
                "connectionStatus": "connecting",
            }}
        if action == "waitConnected":
            ready = True
            return {"terminal": {
                "id": "term_remote", "connectionStatus": "connected",
            }}
        if action == "input":
            assert ready is True
            return {"terminal": {"id": "term_remote"}}
        raise AssertionError(action)

    monkeypatch.setattr(client, "_request", fake_request)

    await client.create_agent_terminal(
        "project-1",
        owner_chat_id="chat-1",
        cwd=str(tmp_path),
        ssh_target="example",
        command="pwd",
    )

    assert [action for action, _payload in calls] == [
        "create", "waitConnected", "input",
    ]
    assert calls[-1][1]["data"] == "pwd\n"
