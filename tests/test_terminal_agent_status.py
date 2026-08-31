from __future__ import annotations

from cyrene.plugins.builtin.cyrene_code.terminal.agent_status import (
    adapter_by_id,
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
    }
    assert public["unread"] is False
    restored.close_store()


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
