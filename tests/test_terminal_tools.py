from __future__ import annotations

import base64
import json

import pytest


def test_requested_terminal_title_preserves_user_supplied_name() -> None:
    from cyrene.tooling.backends.terminals import requested_terminal_title

    assert requested_terminal_title("Explicit", "请新建一个名为 Ignored 的终端") == "Explicit"
    assert requested_terminal_title("", "请新建一个名为 E2E-Alpha-Dev 的持久终端") == "E2E-Alpha-Dev"
    assert requested_terminal_title("", 'Create a terminal named "API-Worker" in this project') == "API-Worker"
    assert requested_terminal_title("", 'Create a shell called "API Worker"') == "API Worker"
    assert requested_terminal_title("", "请新建一个持久终端") == ""


def test_terminal_input_tools_trigger_the_shared_control_animation() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    send = (root / "src/cyrene/tool_impl/code/send_shell.py").read_text(encoding="utf-8")
    interrupt = (root / "src/cyrene/tool_impl/code/interrupt_shell.py").read_text(
        encoding="utf-8"
    )

    assert 'await animate_terminal_control(str(terminal.get("id") or ""), "input")' in send
    assert 'await animate_terminal_control(str(terminal.get("id") or ""), "interrupt")' in interrupt


@pytest.mark.asyncio
async def test_current_terminal_requires_user_choice_when_two_panes_are_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.tooling.backends import terminals

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

    monkeypatch.setattr("cyrene.workbench.ui_surface.request", fake_request)
    with pytest.raises(ValueError, match="Ask the user which terminal") as exc:
        await terminals._surface_current_terminal("surface-1")
    assert "API (term_a)" in str(exc.value)
    assert "Worker (term_b)" in str(exc.value)


@pytest.mark.asyncio
async def test_terminal_control_animation_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.agent.context import bind_run_context
    from cyrene.tooling.backends import terminals

    calls = []

    async def fake_request(ui_instance_id, method, args, *, timeout):
        calls.append((ui_instance_id, method, dict(args), timeout))
        return {"ok": True, "highlighted": True}

    monkeypatch.setattr("cyrene.workbench.ui_surface.request", fake_request)
    binding = bind_run_context(
        agent_id="main",
        caller="main_agent",
        session_id="chat-1",
        ui_instance_id="surface-1",
    )
    try:
        assert await terminals.animate_terminal_control("term_a", "input") is True
    finally:
        binding.reset()

    assert calls == [
        (
            "surface-1",
            "terminal.control",
            {"terminalId": "term_a", "action": "input"},
            3.0,
        )
    ]


@pytest.mark.asyncio
async def test_read_shell_explicitly_distinguishes_screen_and_scrollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.tool_impl.code import read_shell

    terminal = {"id": "term_a", "title": "API"}

    async def fake_resolve_terminal(**_kwargs):
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
    monkeypatch.setattr(
        "cyrene.tooling.backends.terminals.resolve_terminal", fake_resolve_terminal
    )
    monkeypatch.setattr(
        "cyrene.terminal.client.get_terminal_daemon_client", lambda: fake_client
    )

    screen = json.loads(
        await read_shell._tool_read_shell({}, None, 0, "", None)
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
            None,
            0,
            "",
            None,
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
