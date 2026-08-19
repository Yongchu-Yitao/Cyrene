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
    import cyrene.terminal.manager as terminal_module

    monkeypatch.setattr(
        terminal_module,
        "read_project",
        lambda project_id: {"id": project_id, "workspacePath": str(tmp_path)},
    )
    monkeypatch.setattr(
        terminal_module,
        "interactive_argv",
        lambda: ("sh", ["/bin/sh"]),
    )
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "0")
    monkeypatch.setenv("CLICOLOR", "0")
    monkeypatch.setenv("CLICOLOR_FORCE", "0")
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
        "printf 'COLOR_ENV=%s|%s|%s|%s|%s|%s\\n' "
        '"$TERM" "$COLORTERM" "$TERM_PROGRAM" "$CLICOLOR" '
        '"${NO_COLOR-unset}" "${FORCE_COLOR-unset}"\n',
    )

    output = b""
    for _ in range(50):
        await asyncio.sleep(0.02)
        chunks = manager.replay(terminal["id"], 0)
        output = b"".join(base64.b64decode(chunk["data"]) for chunk in chunks)
        if (
            b"CYRENE_PTY_OK" in output
            and b"RAW_INPUT_OK" in output
            and b"COLOR_ENV=xterm-256color|truecolor|Cyrene|1|unset|unset" in output
        ):
            break

    assert b"CYRENE_PTY_OK" in output
    assert b"RAW_INPUT_OK" in output
    assert b"COLOR_ENV=xterm-256color|truecolor|Cyrene|1|unset|unset" in output
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

    await manager.close(terminal["id"], remove=True)


def test_terminal_cwd_cannot_escape_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import cyrene.terminal.manager as terminal_module

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        terminal_module,
        "read_project",
        lambda project_id: {"id": project_id, "workspacePath": str(workspace)},
    )
    manager = TerminalManager()

    with pytest.raises(ValueError, match="inside the project workspace"):
        manager._resolve_cwd("project-1", "../outside")
