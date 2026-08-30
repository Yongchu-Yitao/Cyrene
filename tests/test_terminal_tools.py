from __future__ import annotations

import asyncio
import base64
import json

import pytest


@pytest.mark.asyncio
async def test_ui_surface_request_runs_on_the_websocket_owner_loop() -> None:
    from cyrene.workbench.ui import ui_surface

    owner_loop = asyncio.get_running_loop()
    connection = None

    class FakeWebSocket:
        async def send_json(self, payload):
            assert asyncio.get_running_loop() is owner_loop
            ui_surface.receive(connection, {
                "type": "response",
                "requestId": payload["requestId"],
                "result": {"ok": True, "method": payload["method"]},
            })

    connection = await ui_surface.register("surface-cross-loop", FakeWebSocket())
    try:
        result = await asyncio.to_thread(
            lambda: asyncio.run(
                ui_surface.request(
                    "surface-cross-loop",
                    "terminal.show",
                    {"terminalId": "term-1"},
                    timeout=0.5,
                )
            )
        )
    finally:
        await ui_surface.unregister("surface-cross-loop", connection)

    assert result == {"ok": True, "method": "terminal.show"}


def test_requested_terminal_title_preserves_user_supplied_name() -> None:
    from cyrene.plugins.builtin.cyrene_code.services import requested_terminal_title

    assert requested_terminal_title("Explicit", "请新建一个名为 Ignored 的终端") == "Explicit"
    assert requested_terminal_title("", "请新建一个名为 E2E-Alpha-Dev 的持久终端") == "E2E-Alpha-Dev"
    assert requested_terminal_title("", 'Create a terminal named "API-Worker" in this project') == "API-Worker"
    assert requested_terminal_title("", 'Create a shell called "API Worker"') == "API Worker"
    assert requested_terminal_title("", "请新建一个持久终端") == ""


@pytest.mark.asyncio
async def test_visible_split_terminal_is_discoverable_without_conversation_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_code import services as terminals

    context = PluginContext(
        data={
            "run_context": {
                "project_id": "project-1",
                "session_id": "chat-1",
                "ui_instance_id": "surface-1",
            }
        },
    )

    async def fake_request(ui_instance_id, method, args, *, timeout):
        assert (ui_instance_id, method, args, timeout) == (
            "surface-1", "terminal.current", {}, 3.0,
        )
        return {
            "ok": True,
            "terminalId": "term_visible",
            "terminals": [{
                "terminalId": "term_visible",
                "title": "Terminal 1",
                "side": "left",
            }],
        }

    class FakeClient:
        async def list(self, project_id, **_kwargs):
            assert project_id == "project-1"
            return {"terminals": [{
                "id": "term_visible",
                "projectId": "project-1",
                "title": "Terminal 1",
                "ownerChatId": "another-chat",
                "createdBy": "user",
                "status": "running",
            }]}

    monkeypatch.setattr("cyrene.workbench.ui.ui_surface.request", fake_request)
    service = terminals.CyreneTerminalService()
    monkeypatch.setattr(service, "_client", lambda: FakeClient())

    visible = await service.list_visible(context)
    resolved = await service.resolve(context)

    assert visible[0]["id"] == "term_visible"
    assert visible[0]["visible"] is True
    assert visible[0]["visibleSide"] == "left"
    assert resolved["id"] == "term_visible"


@pytest.mark.asyncio
async def test_list_shells_includes_visible_unbound_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_code import list_shells
    from cyrene.plugins.builtin.cyrene_code.services import CyreneTerminalService

    async def no_bound_terminals(*, include_exited):
        assert include_exited is True
        return []

    async def one_visible_terminal():
        return [{
            "id": "term_visible",
            "title": "Terminal 1",
            "cwd": "/workspace",
            "status": "running",
            "visibleSide": "left",
        }]

    service = CyreneTerminalService()
    monkeypatch.setattr(service, "list_owned", lambda _context, *, include_exited: no_bound_terminals(include_exited=include_exited))
    monkeypatch.setattr(service, "list_visible", lambda _context: one_visible_terminal())
    context = PluginContext(services={"terminals": service})

    result = json.loads(
        await list_shells._tool_list_shells({}, context)
    )

    assert result == [{
        "shell_id": "term_visible",
        "title": "Terminal 1",
        "cwd": "/workspace",
        "status": "running",
        "exit_code": None,
        "wake_id": "",
        "created_by": "",
        "last_actor": "",
        "last_input_at": "",
        "input_event_count": 0,
        "bound_to_conversation": False,
        "visible_in_current_split": True,
        "visible_side": "left",
    }]


def test_terminal_input_tools_trigger_the_shared_control_animation() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = root / "src/cyrene/plugins/builtin/cyrene_code"
    send = (source / "send_shell.py").read_text(encoding="utf-8")
    interrupt = (source / "interrupt_shell.py").read_text(
        encoding="utf-8"
    )

    assert 'await terminals.animate(context, str(terminal.get("id") or ""), "input")' in send
    assert 'await terminals.animate(context, str(terminal.get("id") or ""), "interrupt")' in interrupt


@pytest.mark.asyncio
async def test_current_terminal_requires_user_choice_when_two_panes_are_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.plugins.builtin.cyrene_code import services as terminals

    async def fake_request(_ui_instance_id, method, _args, *, timeout):
        assert method == "terminal.current"
        assert timeout == 3.0
        return {
            "ok": False,
            "error": "multiple_terminals_visible",
            "terminals": [
                {"terminalId": "term_a", "title": "API"},
                {"terminalId": "term_b", "title": "Worker"},
            ],
        }

    monkeypatch.setattr("cyrene.workbench.ui.ui_surface.request", fake_request)
    service = terminals.CyreneTerminalService()
    with pytest.raises(ValueError, match="Provide a terminal name") as exc:
        await service._current_terminal_id("surface-1")
    assert "API (term_a)" in str(exc.value)
    assert "Worker (term_b)" in str(exc.value)


@pytest.mark.asyncio
async def test_terminal_control_animation_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_code import services as terminals

    calls = []

    async def fake_request(ui_instance_id, method, args, *, timeout):
        calls.append((ui_instance_id, method, dict(args), timeout))
        return {"ok": True, "highlighted": True}

    monkeypatch.setattr("cyrene.workbench.ui.ui_surface.request", fake_request)
    context = PluginContext(
        data={
            "run_context": {
                "project_id": "project-1",
                "session_id": "chat-1",
                "ui_instance_id": "surface-1",
            }
        },
    )
    service = terminals.CyreneTerminalService()
    assert await service.animate(context, "term_a", "input") is True

    assert calls == [
        (
            "surface-1",
            "terminal.control",
            {"terminalId": "term_a", "action": "input"},
            3.0,
        )
    ]


@pytest.mark.asyncio
async def test_sensitive_terminal_input_is_sent_without_command_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_code import send_shell
    from cyrene.plugins.builtin.cyrene_code.services import CyreneTerminalService

    sent = []

    async def fake_resolve_terminal(_context, **_kwargs):
        return {"id": "term_secret", "title": "SSH"}

    async def fake_animation(_context, _terminal_id, _action):
        return True

    class FakeClient:
        def __init__(self):
            self.screen_calls = 0

        async def input(self, terminal_id, data):
            sent.append((terminal_id, data))
            return {"terminal": {"status": "running"}}

        async def screen(self, terminal_id):
            assert terminal_id == "term_secret"
            self.screen_calls += 1
            if self.screen_calls == 1:
                return {
                    "terminal": {"status": "running"},
                    "screenText": "[sudo] password for syw:",
                }
            return {
                "terminal": {
                    "status": "running",
                    "lastActor": "agent",
                    "inputEventCount": 1,
                },
                "screenText": "connected",
                "cursor": {"x": 0, "y": 0, "visible": True},
            }

    service = CyreneTerminalService()
    fake_client = FakeClient()
    monkeypatch.setattr(service, "resolve", fake_resolve_terminal)
    monkeypatch.setattr(service, "animate", fake_animation)
    monkeypatch.setattr(service, "_client", lambda: fake_client)
    context = PluginContext(services={"terminals": service})

    result = json.loads(await send_shell._tool_send_shell(
        {
            "name": "SSH",
            "text": "literal-user-secret",
            "key": "enter",
            "sensitive": True,
        },
        context,
    ))

    assert sent == [("term_secret", "literal-user-secret\r")]
    assert result["status"] == "running"


def test_sensitive_terminal_input_requires_visible_credential_prompt() -> None:
    from cyrene.plugins.builtin.cyrene_code.send_shell import _screen_accepts_sensitive_input

    assert _screen_accepts_sensitive_input("[sudo] password for syw:") is True
    assert _screen_accepts_sensitive_input("Enter passphrase for key '/tmp/id':") is True
    assert _screen_accepts_sensitive_input("请输入密码：") is True
    assert _screen_accepts_sensitive_input("user@host workspace %") is False


@pytest.mark.asyncio
async def test_read_shell_explicitly_distinguishes_screen_and_scrollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_code import read_shell
    from cyrene.plugins.builtin.cyrene_code.services import CyreneTerminalService

    terminal = {"id": "term_a", "title": "API"}

    async def fake_resolve_terminal(_context, **_kwargs):
        return dict(terminal)

    class FakeClient:
        async def screen(self, terminal_id):
            assert terminal_id == "term_a"
            return {
                "terminal": {"status": "running", "lastActor": "user"},
                "rows": 24,
                "cols": 80,
                "cursor": {"x": 2, "y": 1, "visible": True},
                "screenText": "prompt\nready",
            }

        async def scrollback(self, terminal_id, *, cursor, max_bytes):
            assert terminal_id == "term_a"
            assert cursor == 10
            assert max_bytes == 64
            return {
                "terminal": {"status": "running", "lastActor": "agent"},
                "encoding": "base64",
                "data": base64.b64encode(b"before\x1b[31mRED\x1b[0m\r\nafter").decode(),
                "requestedStartSeq": 10,
                "startSeq": 10,
                "endSeq": 34,
                "oldestSeq": 4,
                "nextSeq": 50,
                "truncated": True,
                "truncatedBefore": True,
                "truncatedAfter": True,
            }

    fake_client = FakeClient()
    service = CyreneTerminalService()
    monkeypatch.setattr(service, "resolve", fake_resolve_terminal)
    monkeypatch.setattr(service, "_client", lambda: fake_client)
    context = PluginContext(services={"terminals": service})

    screen = json.loads(
        await read_shell._tool_read_shell({}, context)
    )
    assert screen["source"] == "screen"
    assert screen["text"] == screen["screen_text"] == "prompt\nready"
    assert screen["range"] == {
        "start_row": 0,
        "end_row": 1,
        "rendered_rows": 2,
        "terminal_rows": 24,
        "terminal_cols": 80,
    }
    assert screen["truncated"] is False

    scrollback = json.loads(
        await read_shell._tool_read_shell(
            {"view": "scrollback", "cursor": 10, "max_bytes": 64},
            context,
        )
    )
    assert scrollback["source"] == "scrollback"
    assert scrollback["text"] == scrollback["scrollback_text"] == "beforeRED\nafter"
    assert scrollback["range"] == {
        "requested_start_seq": 10,
        "start_seq": 10,
        "end_seq": 34,
        "oldest_seq": 4,
        "next_seq": 50,
    }
    assert scrollback["truncated"] is True
    assert scrollback["truncated_before"] is True
    assert scrollback["truncated_after"] is True


@pytest.mark.asyncio
async def test_read_shell_exposes_indexed_commands_and_selected_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_code import read_shell
    from cyrene.plugins.builtin.cyrene_code.services import CyreneTerminalService

    async def fake_resolve_terminal(_context, **_kwargs):
        return {"id": "term_remote", "title": "Remote"}

    class FakeClient:
        async def commands(self, terminal_id):
            assert terminal_id == "term_remote"
            return {"commands": [{
                "id": "cmd_1", "command": "uname -a", "exitCode": 0,
            }]}

        async def command_output(self, terminal_id, command_id):
            assert (terminal_id, command_id) == ("term_remote", "cmd_1")
            return {
                "command": {"id": "cmd_1", "command": "uname -a"},
                "text": "Linux remote\n",
            }

    service = CyreneTerminalService()
    monkeypatch.setattr(service, "resolve", fake_resolve_terminal)
    monkeypatch.setattr(service, "_client", lambda: FakeClient())
    context = PluginContext(services={"terminals": service})

    commands = json.loads(await read_shell._tool_read_shell(
        {"view": "commands"}, context,
    ))
    output = json.loads(await read_shell._tool_read_shell(
        {"view": "command_output", "command_id": "cmd_1"}, context,
    ))

    assert commands["commands"][0]["command"] == "uname -a"
    assert output["text"] == "Linux remote\n"
