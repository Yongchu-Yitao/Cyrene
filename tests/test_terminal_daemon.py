from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import sys
from pathlib import Path

import pytest

from cyrene.terminal.client import TerminalDaemonClient


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_terminal_daemon_survives_view_disconnect_until_explicit_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import cyrene.terminal.client as client_module

    state_dir = tmp_path / "daemon-state"
    monkeypatch.setattr(
        client_module.TerminalManager,
        "_resolve_cwd",
        classmethod(lambda cls, project_id, cwd="": tmp_path),
    )
    monkeypatch.setattr(client_module, "interactive_argv", lambda: ("sh", ["/bin/sh"]))
    client = TerminalDaemonClient(state_dir=state_dir)
    daemon_pid = 0
    try:
        created = await client.create("project-1")
        terminal = created["terminal"]
        info = json.loads((state_dir / "connection.json").read_text(encoding="utf-8"))
        daemon_pid = int(info["pid"])

        connection, first = await client.connect_terminal(terminal["id"], 0)
        assert first["type"] == "snapshot"
        await connection.send({
            "type": "input",
            "encoding": "base64",
            "data": base64.b64encode(b"printf 'DAEMON_STILL_RUNNING\\n'\n").decode(),
        })
        output = b""
        for _ in range(100):
            event = await asyncio.wait_for(connection.read(), timeout=2)
            if event.get("type") == "output":
                output += base64.b64decode(event["data"])
            if b"DAEMON_STILL_RUNNING" in output:
                break
        assert b"DAEMON_STILL_RUNNING" in output

        # This is the Electron/WebSocket close boundary: detaching the view
        # must not terminate the daemon-owned process.
        await connection.close()
        listed = await client.list("project-1")
        assert listed["terminals"][0]["id"] == terminal["id"]
        assert listed["terminals"][0]["status"] == "running"

        await client.rename(terminal["id"], "Detached shell")
        await client.update_layout("project-1", [terminal["id"]], [terminal["id"]])
        await client.activate("project-1", terminal["id"])
        restored = await client.list("project-1")
        assert restored["activeTerminalId"] == terminal["id"]
        assert restored["terminals"][0]["title"] == "Detached shell"
        assert restored["terminals"][0]["pinned"] is True

        reconnected, snapshot = await client.connect_terminal(terminal["id"], 0)
        assert snapshot["terminal"]["id"] == terminal["id"]
        replay = b""
        for _ in range(100):
            event = await asyncio.wait_for(reconnected.read(), timeout=2)
            if event.get("type") == "output":
                replay += base64.b64decode(event["data"])
            if b"DAEMON_STILL_RUNNING" in replay:
                break
        assert b"DAEMON_STILL_RUNNING" in replay
        await reconnected.close()

        await client.remove(terminal["id"])
        assert (await client.list("project-1"))["terminals"] == []
        assert not (state_dir / "scrollback" / f"{terminal['id']}.bin").exists()
    finally:
        if not daemon_pid:
            try:
                daemon_pid = int(json.loads(
                    (state_dir / "connection.json").read_text(encoding="utf-8")
                )["pid"])
            except (OSError, ValueError, KeyError):
                daemon_pid = 0
        if daemon_pid:
            try:
                os.kill(daemon_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            else:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(os.waitpid, daemon_pid, 0), timeout=3
                    )
                except TimeoutError:
                    os.kill(daemon_pid, signal.SIGKILL)
                    await asyncio.to_thread(os.waitpid, daemon_pid, 0)
