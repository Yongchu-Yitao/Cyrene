from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest

from cyrene.terminal.manager import TerminalManager


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_terminal_manager_keeps_a_resizable_replayable_pty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "cyrene.workbench.app_services.read_project",
        lambda project_id: {"id": project_id, "workspacePath": str(tmp_path)},
    )
    monkeypatch.setattr(
        "cyrene.tooling.backends.shell_runtime.interactive_argv",
        lambda: ("sh", ["/bin/sh"]),
    )
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "0")
    monkeypatch.setenv("CLICOLOR", "0")
    monkeypatch.setenv("CLICOLOR_FORCE", "0")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("LC_CTYPE", "C")
    monkeypatch.setenv("LC_ALL", "C")
    manager = TerminalManager(output_limit=64 * 1024)

    terminal = await manager.create("project-1", cols=82, rows=24)
    assert terminal["title"] == "Terminal 1"
    assert terminal["status"] == "running"
    assert terminal["cwd"] == str(tmp_path)

    await manager.resize(terminal["id"], 96, 31)
    await manager.write(terminal["id"], "printf 'CYRENE_PTY_OK\\n'\n")
    await manager.write_bytes(terminal["id"], b"printf 'RAW_INPUT_OK\\n'\n")
    await manager.write(
        terminal["id"],
        "printf 'COLOR_ENV=%s|%s|%s|%s|%s|%s|%s|%s|%s\\n' "
        '"$TERM" "$COLORTERM" "$TERM_PROGRAM" "$CLICOLOR" '
        '"${NO_COLOR-unset}" "${FORCE_COLOR-unset}" '
        '"$LANG" "$LC_CTYPE" "${LC_ALL-unset}"\n',
    )

    output = b""
    for _ in range(50):
        await asyncio.sleep(0.02)
        chunks = manager.replay(terminal["id"], 0)
        output = b"".join(base64.b64decode(chunk["data"]) for chunk in chunks)
        if (
            b"CYRENE_PTY_OK" in output
            and b"RAW_INPUT_OK" in output
            and b"COLOR_ENV=xterm-256color|truecolor|Cyrene|1|unset|unset|C.UTF-8|C.UTF-8|unset" in output
        ):
            break

    assert b"CYRENE_PTY_OK" in output
    assert b"RAW_INPUT_OK" in output
    assert b"COLOR_ENV=xterm-256color|truecolor|Cyrene|1|unset|unset|C.UTF-8|C.UTF-8|unset" in output
    full_scrollback = manager.scrollback_snapshot(
        terminal["id"], cursor=0, max_bytes=64 * 1024
    )
    assert b"CYRENE_PTY_OK" in base64.b64decode(full_scrollback["data"])
    assert full_scrollback["startSeq"] == full_scrollback["oldestSeq"]
    assert full_scrollback["endSeq"] == full_scrollback["nextSeq"]
    assert full_scrollback["truncated"] is False

    tail_scrollback = manager.scrollback_snapshot(terminal["id"], max_bytes=32)
    assert len(base64.b64decode(tail_scrollback["data"])) <= 32
    assert tail_scrollback["endSeq"] == tail_scrollback["nextSeq"]
    assert tail_scrollback["truncatedBefore"] is True
    assert tail_scrollback["truncatedAfter"] is False
    renamed = manager.rename(terminal["id"], "Build shell")
    assert renamed["title"] == "Build shell"
    assert renamed["cols"] == 96
    assert renamed["rows"] == 31
    assert [item["id"] for item in manager.list("project-1")] == [terminal["id"]]

    await manager.close(terminal["id"], remove=True)
    assert manager.list("project-1") == []


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_terminal_metadata_and_scrollback_survive_manager_restart(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    manager = TerminalManager(output_limit=64 * 1024, state_dir=state_dir)
    terminal = await manager.create_resolved(
        "project-1", cwd=str(tmp_path), shell="sh", argv=["/bin/sh"]
    )
    await manager.write(terminal["id"], "printf 'PERSISTED_SCROLLBACK\\n'; exit\n")
    for _ in range(100):
        await asyncio.sleep(0.02)
        if manager.get(terminal["id"]).status == "exited":
            break

    manager.rename(terminal["id"], "Persistent shell")
    manager.update_layout("project-1", [terminal["id"]], [terminal["id"]])
    manager.set_active("project-1", terminal["id"])
    history_before = manager.input_history(terminal["id"])
    assert history_before[-1]["actor"] == "agent"
    assert history_before[-1]["accepted"] is True

    restored = TerminalManager(output_limit=64 * 1024, state_dir=state_dir)
    listed = restored.list("project-1")
    assert listed[0]["title"] == "Persistent shell"
    assert listed[0]["pinned"] is True
    assert restored.active_terminal_id("project-1") == terminal["id"]
    output = b"".join(
        base64.b64decode(chunk["data"])
        for chunk in restored.replay(terminal["id"], 0)
    )
    assert b"PERSISTED_SCROLLBACK" in output
    assert restored.input_history(terminal["id"]) == history_before
    assert listed[0]["lastActor"] == "agent"
    assert listed[0]["inputEventCount"] == 1

    await manager.close(terminal["id"], remove=True)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_interrupted_interactive_shell_is_restored_without_rerunning_one_shots(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    manager = TerminalManager(output_limit=64 * 1024, state_dir=state_dir)
    interactive = await manager.create_resolved(
        "project-1", cwd=str(tmp_path), shell="sh", argv=["/bin/sh"]
    )
    one_shot = await manager.create_resolved(
        "project-1",
        cwd=str(tmp_path),
        shell="sh",
        argv=["/bin/sh", "-c", "exit 0"],
        launch_mode="one_shot",
    )
    for _ in range(100):
        await asyncio.sleep(0.01)
        if manager.get(one_shot["id"]).status == "exited":
            break

    await manager.close(interactive["id"])
    assert manager._db is not None
    manager._db.execute(
        "UPDATE terminal_sessions SET status='running', exit_code=NULL WHERE id=?",
        (interactive["id"],),
    )
    manager._db.commit()

    restored = TerminalManager(
        output_limit=64 * 1024,
        state_dir=state_dir,
        startup_reason="app_upgrade",
    )
    recovered = await restored.restore_interrupted_sessions()
    assert [item["id"] for item in recovered] == [interactive["id"]]
    recovered_terminal = restored.get(interactive["id"]).public()
    assert recovered_terminal["status"] == "running"
    assert recovered_terminal["recoveryReason"] == "app_upgrade"
    assert recovered_terminal["recoveredAt"]
    assert recovered_terminal["recoveryCount"] == 1
    assert recovered_terminal["recoverable"] is False
    assert restored.get(one_shot["id"]).status == "exited"
    replay = b"".join(
        base64.b64decode(chunk["data"])
        for chunk in restored.replay(interactive["id"], 0)
    )
    assert b"Cyrene restored this shell after an application upgrade" in replay
    assert b"\x1b[?25h" in replay

    await restored.close(interactive["id"], remove=True)
    await restored.close(one_shot["id"], remove=True)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_exited_pty_restarts_under_same_terminal_id_with_scrollback(
    tmp_path: Path,
) -> None:
    manager = TerminalManager(output_limit=64 * 1024, state_dir=tmp_path / "state")
    terminal = await manager.create_resolved(
        "project-1",
        cwd=str(tmp_path),
        shell="sh",
        argv=["/bin/sh"],
        title="Recoverable shell",
    )
    await manager.write(
        terminal["id"], "printf 'BEFORE_PTY_EXIT\\n'; exit 7\n", actor="user"
    )
    for _ in range(100):
        await asyncio.sleep(0.02)
        if manager.get(terminal["id"]).status == "exited":
            break

    exited = manager.get(terminal["id"]).public()
    assert exited["status"] == "exited"
    assert exited["exitCode"] == 7
    assert exited["exitReason"] == "process_exit"
    assert exited["exitAt"]
    assert exited["recoverable"] is True

    restarted = await manager.restart(terminal["id"])
    assert restarted["id"] == terminal["id"]
    assert restarted["title"] == "Recoverable shell"
    assert restarted["cwd"] == str(tmp_path)
    assert restarted["status"] == "running"
    assert restarted["recoveryReason"] == "pty_restart"
    assert restarted["recoveredAt"]
    assert restarted["recoveryCount"] == 1

    await manager.write(
        terminal["id"], "printf 'AFTER_PTY_RESTART\\n'\n", actor="user"
    )
    replay = b""
    for _ in range(100):
        await asyncio.sleep(0.02)
        replay = b"".join(
            base64.b64decode(chunk["data"])
            for chunk in manager.replay(terminal["id"], 0)
        )
        if b"AFTER_PTY_RESTART" in replay:
            break
    assert b"BEFORE_PTY_EXIT" in replay
    assert b"Cyrene restarted this terminal after its PTY exited" in replay
    assert b"\x1b[?25h" in replay
    assert b"AFTER_PTY_RESTART" in replay

    await manager.close(terminal["id"], remove=True)


@pytest.mark.asyncio
async def test_one_shot_terminal_cannot_be_restarted(tmp_path: Path) -> None:
    manager = TerminalManager(output_limit=64 * 1024)
    terminal = await manager.create_resolved(
        "project-1",
        cwd=str(tmp_path),
        shell="python",
        argv=[sys.executable, "-c", "pass"],
        launch_mode="one_shot",
    )
    for _ in range(100):
        await asyncio.sleep(0.01)
        if manager.get(terminal["id"]).status == "exited":
            break
    with pytest.raises(ValueError, match="one-shot terminals cannot be restarted"):
        await manager.restart(terminal["id"])
    await manager.close(terminal["id"], remove=True)


def test_terminal_cwd_cannot_escape_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "cyrene.workbench.app_services.read_project",
        lambda project_id: {"id": project_id, "workspacePath": str(workspace)},
    )
    manager = TerminalManager()

    with pytest.raises(ValueError, match="inside the project workspace"):
        manager._resolve_cwd("project-1", "../outside")


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_agent_terminal_vt_screen_and_wake_are_durable_and_exactly_once(
    tmp_path: Path,
) -> None:
    manager = TerminalManager(output_limit=64 * 1024, state_dir=tmp_path / "state")
    terminal = await manager.create_resolved(
        "project-1",
        cwd=str(tmp_path),
        shell="sh",
        argv=["/bin/sh", "-c", "printf '\\033[31mWAKE_OK\\033[0m\\n'"],
        owner_chat_id="chat-1",
        created_by="agent",
        launch_mode="one_shot",
        wake_on_exit=True,
        wake_note="verify result",
    )
    for _ in range(100):
        await asyncio.sleep(0.02)
        if manager.get(terminal["id"]).status == "exited":
            break

    listed = manager.list("project-1", owner_chat_id="chat-1")
    assert [item["id"] for item in listed] == [terminal["id"]]
    assert listed[0]["createdBy"] == "agent"
    assert "WAKE_OK" in manager.screen_snapshot(terminal["id"])["screenText"]

    claimed = manager.claim_wake("test-consumer", 30)
    assert claimed is not None
    assert claimed["terminal_id"] == terminal["id"]
    assert "verify result" in claimed["prompt"]
    settled = manager.settle_wake(
        claimed["wake_id"], claimed["lease_token"], "delivered"
    )
    assert settled["status"] == "delivered"
    assert manager.claim_wake("second-consumer", 30) is None

    await manager.close(terminal["id"], remove=True)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_user_terminal_input_temporarily_has_priority(tmp_path: Path) -> None:
    from cyrene.terminal.manager import TerminalInputBusyError

    manager = TerminalManager(
        output_limit=64 * 1024, user_input_priority_seconds=0.08
    )
    terminal = await manager.create_resolved(
        "project-1", cwd=str(tmp_path), shell="sh", argv=["/bin/sh"]
    )
    await manager.write(terminal["id"], "printf USER\\n\n", actor="user")
    with pytest.raises(TerminalInputBusyError, match="user input has priority") as busy:
        await manager.write(terminal["id"], "printf AGENT\\n\n", actor="agent")
    assert busy.value.retry_after_ms > 0
    history = manager.input_history(terminal["id"])
    assert [(item["actor"], item["accepted"], item["reason"]) for item in history] == [
        ("user", True, ""),
        ("agent", False, "user_priority"),
    ]
    await asyncio.sleep(0.09)
    await manager.write(terminal["id"], "printf AGENT\\n\n", actor="agent")
    assert manager.get(terminal["id"]).public()["lastActor"] == "agent"
    assert manager.get(terminal["id"]).public()["inputEventCount"] == 3
    await manager.close(terminal["id"], remove=True)


def test_agent_terminal_key_sequences_cover_interactive_tui_controls() -> None:
    from cyrene.tool_impl.code.send_shell import _terminal_key_sequence

    assert _terminal_key_sequence("escape") == "\x1b"
    assert _terminal_key_sequence("up") == "\x1b[A"
    assert _terminal_key_sequence("page_down") == "\x1b[6~"
    assert _terminal_key_sequence("shift_tab") == "\x1b[Z"
    assert _terminal_key_sequence("f12") == "\x1b[24~"
    assert _terminal_key_sequence("ctrl_a") == "\x01"
    assert _terminal_key_sequence("ctrl_z") == "\x1a"
    assert _terminal_key_sequence("unknown") == ""


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_terminal_passes_raw_tui_keys_and_sgr_mouse_bytes(tmp_path: Path) -> None:
    manager = TerminalManager(output_limit=64 * 1024)
    script = (
        "import os,tty; tty.setraw(0); "
        "os.write(1,b'RAW_READY\\n'); "
        "data=os.read(0,64); os.write(1, data.hex().encode()+b'\\n')"
    )
    terminal = await manager.create_resolved(
        "project-1",
        cwd=str(tmp_path),
        shell="python",
        argv=[sys.executable, "-c", script],
    )
    for _ in range(100):
        await asyncio.sleep(0.01)
        ready = b"".join(
            base64.b64decode(chunk["data"])
            for chunk in manager.replay(terminal["id"], 0)
        )
        if b"RAW_READY" in ready:
            break
    assert b"RAW_READY" in ready
    payload = b"\x1b[A\x1b\x03\x1b[<0;10;5M"
    await manager.write_bytes(
        terminal["id"], payload, binary=True, actor="user"
    )
    for _ in range(100):
        await asyncio.sleep(0.02)
        if manager.get(terminal["id"]).status == "exited":
            break
    replay = b"".join(
        base64.b64decode(chunk["data"])
        for chunk in manager.replay(terminal["id"], 0)
    )
    assert payload.hex().encode() in replay
    history = manager.input_history(terminal["id"])
    assert len(history) == 1
    assert history[0]["actor"] == "user"
    assert history[0]["kind"] == "binary"
    assert history[0]["byteCount"] == len(payload)
    assert history[0]["accepted"] is True
    await manager.close(terminal["id"], remove=True)
