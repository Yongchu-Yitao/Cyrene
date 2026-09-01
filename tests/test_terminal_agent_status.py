from __future__ import annotations

from cyrene.plugins.builtin.cyrene_code.terminal.agent_status import (
    AGENT_ADAPTERS,
    adapter_for_command,
    adapter_by_id,
    command_title,
    normalize_agent_event,
)
from cyrene.plugins.builtin.cyrene_code.terminal.manager import (
    TerminalManager,
    TerminalSession,
)


def _session() -> TerminalSession:
    return TerminalSession(
        id="term_agent",
        project_id="project",
        title="Terminal 1",
        cwd="/tmp",
        shell="zsh",
        argv=["zsh", "-i"],
        created_at="2026-08-31T00:00:00+00:00",
        updated_at="2026-08-31T00:00:00+00:00",
        status="running",
    )


def test_agent_adapters_normalize_native_hook_identities() -> None:
    assert adapter_by_id("claude-code").id == "claude"
    assert adapter_by_id("kimi_code_cli").id == "kimi"
    assert adapter_by_id("gemini-cli").id == "gemini"
    assert adapter_by_id("codex-cli").id == "codex"
    assert adapter_by_id("mmx") is None


def test_all_declared_agents_are_detected_from_shell_commands() -> None:
    commands = {
        "claude": "claude --resume",
        "codex": "env OPENAI_LOG=1 codex",
        "gemini": "npx @google/gemini-cli",
        "opencode": "opencode",
        "kimi": "kimi-cli",
        "minimax": "minimax-code",
        "aider": "python -m aider",
        "qwen": "qwen-code",
        "copilot": "gh copilot suggest",
        "goose": "goose session",
        "amp": "amp",
    }
    assert {adapter.id for adapter in AGENT_ADAPTERS} == set(commands)
    assert {
        agent_id: adapter_for_command(command).id
        for agent_id, command in commands.items()
    } == {agent_id: agent_id for agent_id in commands}
    assert command_title("sudo -u runner env FOO=1 codex --full-auto") == "codex"


def test_vendor_hook_events_normalize_to_shared_lifecycle() -> None:
    assert normalize_agent_event("kimi", "TurnStarted", {})[0] == "working"
    assert normalize_agent_event("claude", "PermissionRequest", {})[0] == "waiting"
    assert normalize_agent_event("codex", "Stop", {})[0] == "completed"
    assert normalize_agent_event("kimi", "StopFailure", {})[0] == "failed"
    assert normalize_agent_event("gemini", "Interrupt", {})[0] == "interrupted"


def test_agent_state_and_unread_are_orthogonal_and_persisted(tmp_path) -> None:
    state_dir = tmp_path / "state"
    manager = TerminalManager(state_dir=state_dir)
    session = _session()
    manager._sessions[session.id] = session
    manager._persist_session(session)

    reported = manager.agent_event(
        session.id,
        "kimi",
        "PermissionRequest",
        {"hook_event_name": "PermissionRequest", "session_id": "abc"},
    )
    assert reported["agentState"] == "waiting"
    assert reported["agentActive"] is True
    assert reported["unread"] is True

    read = manager.mark_read(session.id)
    assert read["agentState"] == "waiting"
    assert read["unread"] is False
    manager.close_store()

    restored = TerminalManager(state_dir=state_dir)
    public = restored.get(session.id).public()
    assert public["agent"] == {
        "id": "kimi",
        "label": "Kimi Code",
        "state": "waiting",
        "event": "permission_request",
        "updatedAt": public["agentUpdatedAt"],
        "active": False,
        "sessionEndedAt": public["agentSessionEndedAt"],
    }
    assert public["agentSessionEndedAt"]
    assert public["unread"] is False
    restored.close_store()


def test_agent_session_end_clears_active_identity_without_losing_history() -> None:
    manager = TerminalManager()
    session = _session()
    manager._sessions[session.id] = session
    manager.agent_event(session.id, "claude", "SessionStart", {})

    ended = manager.agent_event(session.id, "claude", "SessionEnd", {})

    assert ended["agentId"] == "claude"
    assert ended["agentState"] == "completed"
    assert ended["agentEvent"] == "session_end"
    assert ended["agentActive"] is False
    assert ended["agentSessionEndedAt"]


def test_output_unread_can_be_acknowledged_without_changing_agent_state() -> None:
    manager = TerminalManager()
    session = _session()
    manager._sessions[session.id] = session
    manager.agent_event(session.id, "claude", "TurnStarted", {})
    manager._append_output(session, b"working\r\n")

    assert session.public()["unread"] is True
    manager.mark_read(session.id)
    assert session.public()["agentState"] == "working"
    assert session.public()["unread"] is False
