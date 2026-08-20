from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import signal
import sys
from pathlib import Path

import pytest

from cyrene.terminal.client import TerminalDaemonClient, TerminalRequestError


@pytest.mark.asyncio
async def test_terminal_daemon_requests_have_a_hard_timeout() -> None:
    client = TerminalDaemonClient()

    async def never_responds(_action, _payload):
        await asyncio.Event().wait()
        return {}

    client._request_once = never_responds  # type: ignore[method-assign]
    with pytest.raises(TerminalRequestError) as exc:
        await client._request("screen", request_timeout=0.01)
    assert exc.value.code == "daemon_timeout"


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_terminal_daemon_survives_view_disconnect_until_explicit_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import cyrene.terminal.manager as manager_module

    state_dir = tmp_path / "daemon-state"
    monkeypatch.setattr(
        manager_module.TerminalManager,
        "_resolve_cwd",
        classmethod(lambda cls, project_id, cwd="": tmp_path),
    )
    monkeypatch.setattr(
        "cyrene.tooling.backends.shell_runtime.interactive_argv",
        lambda: ("sh", ["/bin/sh"]),
    )
    client = TerminalDaemonClient(state_dir=state_dir)
    daemon_pid = 0
    try:
        created = await client.create("project-1")
        terminal = created["terminal"]
        info = json.loads((state_dir / "connection.json").read_text(encoding="utf-8"))
        daemon_pid = int(info["pid"])

        connection, first = await client.connect_terminal(terminal["id"], 0)
        assert first["type"] == "snapshot"
        while True:
            replay_complete = await asyncio.wait_for(connection.read(), timeout=2)
            if replay_complete.get("type") == "replay_complete":
                break
        assert replay_complete["type"] == "replay_complete"
        assert replay_complete["nextSeq"] == first["terminal"]["nextSeq"]
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

        scrollback = await client.scrollback(
            terminal["id"], cursor=0, max_bytes=64 * 1024
        )
        assert b"DAEMON_STILL_RUNNING" in base64.b64decode(scrollback["data"])
        assert scrollback["startSeq"] == scrollback["oldestSeq"]
        assert scrollback["endSeq"] == scrollback["nextSeq"]
        assert scrollback["truncated"] is False

        # This is the Electron/WebSocket close boundary: detaching the view
        # must not terminate the daemon-owned process.
        await connection.close()
        listed = await client.list("project-1")
        assert listed["terminals"][0]["id"] == terminal["id"]
        assert listed["terminals"][0]["status"] == "running"
        history = await client.input_history(terminal["id"])
        assert [(event["actor"], event["accepted"]) for event in history["events"]] == [
            ("user", True)
        ]

        await client.rename(terminal["id"], "Detached shell")
        second = (await client.create("project-1", title="Second shell"))["terminal"]
        await client.update_layout(
            "project-1",
            [second["id"], terminal["id"]],
            [second["id"], terminal["id"]],
        )
        await client.activate("project-1", terminal["id"])
        restored = await client.list("project-1")
        assert restored["activeTerminalId"] == terminal["id"]
        assert [item["id"] for item in restored["terminals"]] == [
            second["id"], terminal["id"]
        ]
        detached = next(
            item for item in restored["terminals"] if item["id"] == terminal["id"]
        )
        assert detached["title"] == "Detached shell"
        assert detached["cwd"] == str(tmp_path)
        assert detached["pinned"] is True

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
        await client.remove(second["id"])
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


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_exited_terminal_input_does_not_drop_daemon_subscription(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import cyrene.terminal.manager as manager_module

    state_dir = tmp_path / "daemon-state"
    monkeypatch.setattr(
        manager_module.TerminalManager,
        "_resolve_cwd",
        classmethod(lambda cls, project_id, cwd="": tmp_path),
    )
    monkeypatch.setattr(
        "cyrene.tooling.backends.shell_runtime.interactive_argv",
        lambda: ("sh", ["/bin/sh", "-c", "exit 0"]),
    )
    client = TerminalDaemonClient(state_dir=state_dir)
    daemon_pid = 0
    try:
        terminal = (await client.create("project-1"))["terminal"]
        info = json.loads((state_dir / "connection.json").read_text(encoding="utf-8"))
        daemon_pid = int(info["pid"])
        for _ in range(100):
            await asyncio.sleep(0.01)
            current = (await client.list("project-1"))["terminals"][0]
            if current["status"] == "exited":
                break

        connection, snapshot = await client.connect_terminal(terminal["id"], 0)
        assert snapshot["terminal"]["status"] == "exited"
        for _ in range(2):
            await connection.send({"type": "input", "data": "ignored"})
            while True:
                event = await asyncio.wait_for(connection.read(), timeout=2)
                if event.get("type") == "error":
                    break
            assert event["code"] == "terminal_not_running"
            assert event["terminal"]["status"] == "exited"
        await connection.close()
        await client.remove(terminal["id"])
    finally:
        if daemon_pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(daemon_pid, signal.SIGTERM)
            with contextlib.suppress(TimeoutError, ChildProcessError):
                await asyncio.wait_for(
                    asyncio.to_thread(os.waitpid, daemon_pid, 0), timeout=3
                )


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY behavior")
async def test_terminal_daemon_shutdown_closes_views_and_recovers_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import cyrene.terminal.manager as manager_module

    state_dir = tmp_path / "daemon-state"
    monkeypatch.setattr(
        manager_module.TerminalManager,
        "_resolve_cwd",
        classmethod(lambda cls, project_id, cwd="": tmp_path),
    )
    monkeypatch.setattr(
        "cyrene.tooling.backends.shell_runtime.interactive_argv",
        lambda: ("sh", ["/bin/sh"]),
    )
    client = TerminalDaemonClient(state_dir=state_dir)
    terminal = (await client.create("project-1"))["terminal"]
    info = json.loads((state_dir / "connection.json").read_text(encoding="utf-8"))
    daemon_pid = int(info["pid"])
    connection, _ = await client.connect_terminal(terminal["id"], 0)
    os.kill(daemon_pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(
            asyncio.to_thread(os.waitpid, daemon_pid, 0), timeout=3
        )
        disconnected = False
        for _ in range(20):
            try:
                await asyncio.wait_for(connection.read(), timeout=2)
            except ConnectionError as exc:
                assert "daemon disconnected" in str(exc)
                disconnected = True
                break
        assert disconnected is True

        listed = await client.list("project-1")
        recovered = listed["terminals"][0]
        assert recovered["id"] == terminal["id"]
        assert recovered["status"] == "running"
        assert recovered["recoveryReason"] == "daemon_restart"
        assert recovered["recoveredAt"]
        assert recovered["recoveryCount"] == 1

        daemon_pid = int(json.loads(
            (state_dir / "connection.json").read_text(encoding="utf-8")
        )["pid"])
        reconnected, snapshot = await client.connect_terminal(terminal["id"], 0)
        assert snapshot["terminal"]["status"] == "running"
        await reconnected.send({
            "type": "input",
            "encoding": "base64",
            "data": base64.b64encode(b"printf 'RECOVERED_DAEMON_OK\\n'\n").decode(),
        })
        replay = b""
        for _ in range(100):
            event = await asyncio.wait_for(reconnected.read(), timeout=2)
            if event.get("type") == "output":
                replay += base64.b64decode(event["data"])
            if b"RECOVERED_DAEMON_OK" in replay:
                break
        assert b"RECOVERED_DAEMON_OK" in replay
        await reconnected.close()
        await client.remove(terminal["id"])
    finally:
        await connection.close()
        with contextlib.suppress(ProcessLookupError):
            os.kill(daemon_pid, signal.SIGTERM)
        with contextlib.suppress(TimeoutError, ChildProcessError):
            await asyncio.wait_for(
                asyncio.to_thread(os.waitpid, daemon_pid, 0), timeout=3
            )


def test_terminal_frontend_exposes_recovery_controls_and_input_cursor() -> None:
    frontend = Path(__file__).parents[1] / "src/webui/frontend"
    source = (frontend / "terminal/entry.jsx").read_text(encoding="utf-8")
    chat_source = (frontend / "workbench-chat.jsx").read_text(encoding="utf-8")
    feedback = (frontend / "shared/feedback/service.jsx").read_text(encoding="utf-8")
    styles = (frontend / "workbench.css").read_text(encoding="utf-8")
    terminal_styles = (frontend / "terminal/terminal.css").read_text(encoding="utf-8")

    assert 'cursorBlink: true' in source
    assert 'cursorStyle: "bar"' in source
    assert 'cursorWidth: 2' in source
    assert 'TERMINAL_LINE_HEIGHT = 1.14' in source
    assert 'lineHeight: TERMINAL_LINE_HEIGHT' in source
    assert 'cursorInactiveStyle: "none"' in source
    assert 'TERMINAL_CURSOR_HEIGHT_RATIO = 0.74' in source
    assert 'window.matchMedia("(prefers-reduced-motion: reduce)")' in source
    assert 'smoothScrollDuration: 0' in source
    assert 'customCursor.className = "wbc-terminal-input-cursor"' in source
    assert 'terminal.element.classList.contains("focus")' in source
    assert 'core.coreService.isCursorHidden' in source
    assert 'cursorMove = terminal.onCursorMove(scheduleInputCursorUpdate)' in source
    assert 'bufferChange = terminal.buffer.onBufferChange(handleBufferChange)' in source
    assert 'buffer.type === "alternate"' in source
    assert 'tailSpacer.classList.add("is-alternate-buffer")' in source
    assert 'tailSpacer.classList.remove("is-alternate-buffer")' in source
    assert 'terminal.buffer.active.type !== "normal"' in source
    assert 'terminal.scrollToBottom();' in source
    assert 'interactionEnd = Math.max(0, host.scrollHeight - host.clientHeight)' in source
    assert 'top: interactionEnd' in source
    assert 'bufferChange.dispose()' in source
    assert '.wbc-terminal-input-cursor.is-visible {' in terminal_styles
    assert '.wbc-terminal-host .xterm-cursor-layer {' in terminal_styles
    assert '.wbc-terminal-host .xterm-cursor.xterm-cursor-bar {' in terminal_styles
    assert 'box-shadow: none !important;' in terminal_styles
    assert 'terminal.write("\\u001b[?25h")' in source
    assert 'message.type === "replay_complete"' in source
    assert 'tailSpacer.className = "wbc-terminal-tail-spacer"' in source
    assert 'pendingUserInputMarker = createInteractionMarker()' in source
    assert 'replaceLastInteractionMarker(pendingUserInputMarker || createInteractionMarker())' in source
    assert 'trackAgentInputBoundary(message.terminal, message.type)' in source
    assert 'String(terminalState.lastActor || "") === "agent"' in source
    assert 'lastInteractionMarker && !lastInteractionMarker.isDisposed' in source
    assert 'interactionLines <= terminal.rows' in source
    assert 'TERMINAL_TAIL_COMPACT_LINES = 1' in source
    assert 'markerViewportRow + 1 - TERMINAL_TAIL_COMPACT_LINES' in source
    assert 'tailBaseOverflow = Math.max(0, host.scrollHeight - host.clientHeight)' in source
    assert 'Math.max(0, desiredScrollTop - tailBaseOverflow)' in source
    assert 'lastInteractionFits && !interactionFits && host.scrollTop > 0' in source
    assert 'behavior: reduceMotionQuery && reduceMotionQuery.matches ? "auto" : "smooth"' in source
    assert 'activeBuffer.type === "normal"' in source
    assert 'screenHeight / terminal.rows' in source
    assert 'function scrollHostToTail()' not in source
    assert 'followHostTail' not in source
    assert 'terminal.scrollToBottom();\n        updateTailSpacer();' in source
    assert 'terminal.write(bytes, function () {\n          updateTailSpacer();' in source
    assert 'scrollbar-width: none;' in terminal_styles
    assert '.wbc-terminal-host::-webkit-scrollbar {' in terminal_styles
    assert 'overflow-x: hidden;' in terminal_styles
    assert 'scroll-behavior: smooth;' not in terminal_styles
    assert 'transition: height 180ms cubic-bezier(0.22, 1, 0.36, 1);' in terminal_styles
    assert '.wbc-terminal-tail-spacer.is-alternate-buffer {' in terminal_styles
    assert 'activeBuffer.viewportY >= activeBuffer.baseY' in source
    assert 'host.addEventListener("wheel", handleTailWheel' in source
    assert 'TerminalClient.restart(terminalId)' in source
    assert 'Math.min(15000, 400 * Math.pow(2' in source
    assert 'window.addEventListener("online", handleOnline)' in source
    assert 'window.CyreneUI.require("feedback")' in source
    assert 'showTerminalRecoveryToast(message.terminal)' in source
    assert 'showTerminalExitToast(message.terminal, restartTerminal)' in source
    assert '"终端已退出：" + terminalExitMessage(terminal)' in source
    assert 'actionLabel: recoverable ? "重新启动" : ""' in source
    assert 'else if (connection === "exited") {' not in source
    assert 'className="workbench-toast-action"' in feedback
    assert 'typeof opts.onAction === "function"' in feedback
    assert 'toast.key === toastKey' in feedback
    assert '.workbench-toast-action {' in styles
    assert 'key: "terminal-status:" + String(terminalId || "")' in source
    assert 'notice.kind === "loading" ? "info" : notice.kind' in source
    assert 'feedback.dismissToast(statusToastRef.current)' in source
    assert 'className={"wbc-terminal-notice " + notice.kind}' not in source
    assert '.wbc-terminal-notice {' not in terminal_styles
    assert 'actionLabel = notice.reconnect' in source
    assert '"重新启动"' in source
    assert 'var [railMode, setRailMode] = useWbcState("chat")' in chat_source
    assert 'setRailMode("terminal");\n    replaceWithTerminal(pending.terminalId' in chat_source
    assert 'railMode={railMode}' in chat_source


def test_agent_terminal_show_uses_split_and_replaces_one_existing_pane() -> None:
    source = (
        Path(__file__).parents[1]
        / "src/webui/frontend/workbench-chat.jsx"
    ).read_text(encoding="utf-8")

    assert "function showAgentTerminal(terminalId, preferredSide)" in source
    assert "if (count <= 1)" in source
    assert "next[targetSide] = [card]" in source
    assert "next[replaceSide][replaceIndex] = card" in source
    assert 'mode: "split"' in source
