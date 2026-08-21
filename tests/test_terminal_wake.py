from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
def wake_env(tmp_path, monkeypatch):
    from cyrene import agent
    from cyrene.workbench import chat as chat_service
    from cyrene.workbench import chat_groups
    from cyrene.workbench import project_memory_prompt
    from cyrene.workbench import runtime
    from cyrene.workbench.chat_runs import ChatRunManager

    chats_path = tmp_path / "workbench_chats.json"
    chats_path.write_text(
        json.dumps(
            {
                "chats": [
                    {
                        "id": "chat_wake",
                        "projectId": "project_wake",
                        "title": "Wake test",
                        "status": "idle",
                        "messages": [{"id": "u1", "role": "user", "content": "run it"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    project = {
        "id": "project_wake",
        "workspacePath": str(tmp_path),
        "sessions": [],
    }

    monkeypatch.setattr(chat_service, "_CHATS_STORE", chats_path)
    monkeypatch.setattr(chat_service, "_STORE_DB_PATH", "")
    monkeypatch.setattr(chat_service, "_CONFIGURED_CHATS_STORE", None)
    monkeypatch.setattr(
        chat_service,
        "_CHAT_RUN_MANAGER",
        ChatRunManager(retention_seconds=0),
    )
    monkeypatch.setattr(
        chat_service,
        "_capture_workspace_changes_baseline",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )

    async def no_workspace_changes(**_kwargs):
        return None

    monkeypatch.setattr(
        chat_service,
        "_finalize_workspace_changes",
        no_workspace_changes,
    )
    monkeypatch.setattr(chat_service, "_session_state_messages", lambda _chat_id: [])
    monkeypatch.setattr(
        chat_service,
        "_extract_exchange_timeline",
        lambda *_args, **_kwargs: (
            [],
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            [],
        ),
    )
    monkeypatch.setattr(chat_service, "append_notification", lambda **_kwargs: {})
    monkeypatch.setattr(runtime, "_read_workbench_store", lambda: {"projects": [project]})
    monkeypatch.setattr(runtime, "_get_model", lambda: "test-model")
    monkeypatch.setattr(
        runtime,
        "_workbench_resolve_workspace_dir",
        lambda _project: str(tmp_path),
    )
    monkeypatch.setattr(chat_groups, "configure_store", lambda _db_path: None)

    async def reconcile_session(_chat_id):
        return None

    monkeypatch.setattr(chat_groups, "reconcile_session", reconcile_session)
    monkeypatch.setattr(project_memory_prompt, "build_main_agent_suffix", lambda _snapshot: "")
    monkeypatch.setattr(agent, "is_session_running", lambda _chat_id: False)

    return {
        "agent": agent,
        "chat_service": chat_service,
        "chats_path": chats_path,
    }


@pytest.mark.asyncio
async def test_terminal_wake_is_internal_and_requires_agent_to_read_terminal(
    wake_env,
    monkeypatch,
):
    captured = {}

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        kwargs["on_session_acquired"]()
        return "I inspected the terminal and continued."

    monkeypatch.setattr(wake_env["agent"], "run_agent", fake_run_agent)

    status = await wake_env["chat_service"].dispatch_shell_wake_run(
        {
            "wake_id": "wake_1",
            "terminal_id": "term_1",
            "shell_id": "term_1",
            "chat_id": "chat_wake",
            "prompt": "SECRET_TERMINAL_OUTPUT",
            "exit_status": "done",
            "exit_code": 0,
            "title": "build",
            "note": "verify the build",
        },
        bot=None,
        db_path="",
    )

    assert status == "started"
    run = wake_env["chat_service"]._CHAT_RUN_MANAGER.runs["chat_wake"]
    await asyncio.wait_for(run.done.wait(), timeout=2)

    assert captured["user_message"] == ""
    assert captured["public_user_message"] == ""
    assert captured["persist_user_message"] is False
    assert captured["assistant_message_meta"]["system_initiated"] is True
    assert "term_1" in captured["fixed_ephemeral_system"]
    assert "code.shell.read" in captured["fixed_ephemeral_system"]
    assert "SECRET_TERMINAL_OUTPUT" not in captured["fixed_ephemeral_system"]

    stored = json.loads(wake_env["chats_path"].read_text(encoding="utf-8"))
    messages = stored["chats"][0]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert all(message.get("content") != "SECRET_TERMINAL_OUTPUT" for message in messages)
    assert messages[-1]["wakeId"] == "wake_1"


@pytest.mark.asyncio
async def test_terminal_wake_startup_conflict_is_released_for_retry(
    wake_env,
    monkeypatch,
):
    from cyrene.agent.coordinator import SessionRunConflictError

    async def conflicting_run_agent(**_kwargs):
        raise SessionRunConflictError("chat_wake")

    monkeypatch.setattr(wake_env["agent"], "run_agent", conflicting_run_agent)

    status = await wake_env["chat_service"].dispatch_shell_wake_run(
        {
            "wake_id": "wake_conflict",
            "terminal_id": "term_conflict",
            "chat_id": "chat_wake",
            "prompt": "ignored output",
            "exit_status": "done",
            "exit_code": 0,
        },
        bot=None,
        db_path="",
    )

    assert status == "busy"
    run = wake_env["chat_service"]._CHAT_RUN_MANAGER.runs["chat_wake"]
    await asyncio.wait_for(run.done.wait(), timeout=2)
    stored = json.loads(wake_env["chats_path"].read_text(encoding="utf-8"))
    assert [message["role"] for message in stored["chats"][0]["messages"]] == ["user"]
