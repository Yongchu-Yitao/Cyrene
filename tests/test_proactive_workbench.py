import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock


def _write_chats(path, chats):
    path.write_text(json.dumps({"chats": chats}, ensure_ascii=False), encoding="utf-8")


def test_latest_workbench_user_activity_uses_user_timestamp(monkeypatch, tmp_path):
    from cyrene.runtime import scheduler

    chats_path = tmp_path / "workbench_chats.json"
    _write_chats(chats_path, [
        {
            "id": "chat_old",
            "projectId": "project_a",
            "title": "Old",
            "lastUserMessageAt": "2026-06-17T10:00:00+00:00",
            "updatedAt": "2026-06-18T12:00:00+00:00",
            "messages": [],
        },
        {
            "id": "chat_latest",
            "projectId": "project_b",
            "title": "Latest",
            "updatedAt": "2026-06-17T11:30:00+00:00",
            "messages": [
                {
                    "role": "user",
                    "content": "latest user turn",
                    "createdAt": "2026-06-17T11:00:00+00:00",
                },
                {
                    "role": "assistant",
                    "content": "reply",
                    "createdAt": "2026-06-17T11:30:00+00:00",
                },
            ],
        },
    ])
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)

    latest = scheduler._latest_workbench_user_activity()

    assert latest is not None
    assert latest["chat_id"] == "chat_latest"
    assert latest["project_id"] == "project_b"
    assert latest["timestamp"] == datetime(2026, 6, 17, 11, tzinfo=timezone.utc)


def test_silence_detection_includes_workbench_user_activity(monkeypatch, tmp_path):
    from cyrene.runtime import scheduler

    _write_chats(tmp_path / "workbench_chats.json", [
        {
            "id": "chat_1",
            "projectId": "project_1",
            "lastUserMessageAt": "2026-06-18T02:03:04+00:00",
            "messages": [],
        }
    ])
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scheduler, "CONVERSATIONS_DIR", conversations)
    monkeypatch.setattr(scheduler, "STATE_FILE", tmp_path / "missing-state.json")

    assert scheduler._last_user_message_time() == datetime(
        2026, 6, 18, 2, 3, 4, tzinfo=timezone.utc
    )


def test_mark_user_activity_resets_lottery(monkeypatch):
    from cyrene.runtime import scheduler
    from cyrene.workbench import chat as routes_workbench_chat

    reset = MagicMock()
    monkeypatch.setattr(scheduler, "reset_lottery", reset)
    chat = {}

    routes_workbench_chat._mark_user_activity(
        chat, "2026-06-18T02:03:04+00:00"
    )

    assert chat["lastUserMessageAt"] == "2026-06-18T02:03:04+00:00"
    assert chat["updatedAt"] == "2026-06-18T02:03:04+00:00"
    reset.assert_called_once_with()


async def test_proactive_skips_when_latest_workbench_chat_is_running(
    monkeypatch, tmp_path
):
    from cyrene.runtime import scheduler

    _write_chats(tmp_path / "workbench_chats.json", [
        {
            "id": "chat_busy",
            "projectId": "project_1",
            "lastUserMessageAt": "2026-06-17T02:03:04+00:00",
            "messages": [],
        }
    ])
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "is_session_running", lambda session_id: session_id == "chat_busy")
    run = AsyncMock()
    monkeypatch.setattr(scheduler, "run_heartbeat_agent", run)
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=0,
        cooldown_until=0.0,
        last_proactive_time=0.0,
        probability=0.5,
    )

    await scheduler._heartbeat_proactive_check(None, "db.sqlite3")

    run.assert_not_awaited()
    assert scheduler._LOTTERY_STATE["probability"] == 0.5


async def test_proactive_is_persisted_to_new_workbench_chat(
    monkeypatch, tmp_path
):
    from cyrene.observability import debug
    from cyrene.runtime import scheduler
    from cyrene.workbench import chat as routes_workbench_chat

    chats_path = tmp_path / "workbench_chats.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    projects_path = tmp_path / "workbench_projects.json"
    _write_chats(chats_path, [
        {
            "id": "chat_latest",
            "projectId": "project_1",
            "title": "Launch",
            "model": "test-model",
            "lastUserMessageAt": "2026-06-14T02:03:04+00:00",
            "messages": [],
        }
    ])
    projects_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "project_1",
                        "name": "Launch",
                        "workspacePath": str(workspace),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    events = []

    async def publish(event, **_kwargs):
        events.append(event)

    captured = {}

    async def run_agent(
        prompt,
        bot,
        chat_id,
        db_path,
        session_id="",
        on_reply=None,
        lang="",
        workspace_dir="",
    ):
        assert session_id.startswith("wbchat_")
        assert session_id != "chat_latest"
        assert on_reply is not None
        captured["session_id"] = session_id
        captured["lang"] = lang
        captured["workspace_dir"] = workspace_dir
        await on_reply("How did the launch go?")
        return "How did the launch go?"

    import cyrene.runtime.settings_store as settings_store
    monkeypatch.setattr(
        settings_store, "get",
        lambda key, default=None: "zh" if key == "app_language" else default,
    )
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)
    monkeypatch.setattr(routes_workbench_chat, "_CHATS_STORE", chats_path)
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_save_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "_silence_hours", lambda: 96.0)
    monkeypatch.setattr(scheduler, "_assemble_proactive_context", AsyncMock(return_value=""))
    monkeypatch.setattr(scheduler, "is_session_running", lambda _session_id: False)
    monkeypatch.setattr(scheduler, "run_heartbeat_agent", run_agent)
    monkeypatch.setattr(scheduler, "notify", AsyncMock())
    monkeypatch.setattr(scheduler, "append_notification", lambda **_kwargs: {})
    monkeypatch.setattr(debug, "publish_event", publish)
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=0,
        cooldown_until=0.0,
        last_proactive_time=0.0,
        probability=0.0,
    )

    await scheduler._heartbeat_proactive_check(None, "db.sqlite3")

    saved = json.loads(chats_path.read_text(encoding="utf-8"))
    assert len(saved["chats"]) == 2
    proactive_chat = saved["chats"][0]
    original_chat = saved["chats"][1]
    assert proactive_chat["id"] == captured["session_id"]
    assert proactive_chat["id"] != original_chat["id"]
    assert proactive_chat["projectId"] == "project_1"
    assert proactive_chat["sourceChatId"] == "chat_latest"
    assert proactive_chat["proactive"] is True
    assert original_chat["messages"] == []
    messages = proactive_chat["messages"]
    assert messages[-1]["content"] == "How did the launch go?"
    assert messages[-1]["proactive"] is True
    assert proactive_chat["updatedAt"] == messages[-1]["createdAt"]
    assert events[-1]["type"] == "workbench_proactive_message"
    assert events[-1]["chat_id"] == proactive_chat["id"]
    assert any(
        event.get("type") == "workbench_chat_changed"
        and event.get("chat_id") == proactive_chat["id"]
        for event in events
    )
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 1
    # The persisted UI language must be threaded into the proactive agent run.
    assert captured["lang"] == "zh"
    assert captured["workspace_dir"] == str(workspace)


async def test_proactive_public_persistence_strips_internal_awaiting_marker(
    monkeypatch, tmp_path
):
    from cyrene.observability import debug
    from cyrene.workbench import chat as routes_workbench_chat

    chats_path = tmp_path / "workbench_chats.json"
    _write_chats(chats_path, [{
        "id": "chat_existing",
        "projectId": "project_1",
        "title": "Existing",
        "messages": [],
    }])
    monkeypatch.setattr(routes_workbench_chat, "_CHATS_STORE", chats_path)
    monkeypatch.setattr(debug, "publish_event", AsyncMock())

    result = await routes_workbench_chat.create_proactive_chat(
        "project_1",
        "**[[cyrene.awaiting_user]]**",
        chat_id="wbchat_proactive_new",
    )

    assert result is None
    saved = json.loads(chats_path.read_text(encoding="utf-8"))
    assert len(saved["chats"]) == 1
    assert "[[cyrene.awaiting_user]]" not in chats_path.read_text(encoding="utf-8")


async def test_heartbeat_agent_does_not_preempt_busy_target_session():
    from cyrene.agent import coordinator
    from cyrene.agent import state

    ctx = state._ensure_session("chat_busy_proactive_test")
    await ctx.lock.acquire()
    try:
        result = await coordinator.run_heartbeat_agent(
            "hidden prompt",
            None,
            0,
            "db.sqlite3",
            session_id="chat_busy_proactive_test",
        )
    finally:
        ctx.lock.release()

    assert result == ""


async def test_proactive_lang_is_pinned_in_ephemeral_system(monkeypatch):
    from cyrene.agent import coordinator

    captured = {}

    async def fake_run_chat_agent(prompt, bot, chat_id, db_path, **kwargs):
        captured["ephemeral_system"] = kwargs.get("ephemeral_system", "")
        captured["behavior_user_message"] = kwargs.get("behavior_user_message")
        captured["behavior_system_initiated"] = kwargs.get("behavior_system_initiated")
        return ""

    monkeypatch.setattr(coordinator, "_run_chat_agent", fake_run_chat_agent)

    # An explicit language pins the reply; no soft "guess from past messages".
    await coordinator.run_heartbeat_agent(
        "hidden", None, 0, "db.sqlite3", session_id="lang_pin_zh", lang="zh",
    )
    assert "简体中文" in captured["ephemeral_system"]
    assert "based on their past messages" not in captured["ephemeral_system"]
    assert captured["behavior_user_message"] == "Scheduled proactive check-in"
    assert captured["behavior_system_initiated"] is True
    assert "DECISION RULE — autonomous work, not conversation" in captured["ephemeral_system"]
    assert "use tools and complete it now" in captured["ephemeral_system"]
    assert "Never greet the user, make small talk" in captured["ephemeral_system"]
    assert "This scheduler event is not user activity" in captured["ephemeral_system"]
    assert "Never imply the user just woke up" in captured["ephemeral_system"]

    # No persisted language falls back to inferring from past messages.
    await coordinator.run_heartbeat_agent(
        "hidden", None, 0, "db.sqlite3", session_id="lang_pin_none", lang="",
    )
    assert "based on their past messages" in captured["ephemeral_system"]


async def test_proactive_write_allows_only_new_files(monkeypatch, tmp_path):
    from cyrene.agent import state
    from cyrene.tool_impl.core.write import _tool_write
    import cyrene.runtime.settings_store as settings_store

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    existing = workspace / "existing.md"
    existing.write_text("keep", encoding="utf-8")

    monkeypatch.setattr(settings_store, "is_workspace_active", lambda: True)
    workspace_token = state._active_workspace_dir.set(str(workspace))
    meta_token = state._ui_round_assistant_meta.set({"system_initiated": True})
    try:
        result = await _tool_write(
            {"path": "notes/new.md", "content": "hello"},
            None,
            0,
            "db.sqlite3",
            None,
        )
        assert "Wrote" in result
        assert (workspace / "notes" / "new.md").read_text(encoding="utf-8") == "hello"

        result = await _tool_write(
            {"path": "existing.md", "content": "replace"},
            None,
            0,
            "db.sqlite3",
            None,
        )
        assert "may only create new files" in result
        assert existing.read_text(encoding="utf-8") == "keep"
    finally:
        state._ui_round_assistant_meta.reset(meta_token)
        state._active_workspace_dir.reset(workspace_token)


async def test_write_skill_md_reminds_agent_to_register_after_generation(monkeypatch, tmp_path):
    from cyrene.agent import state
    from cyrene.tool_impl.core.write import _tool_write
    import cyrene.runtime.settings_store as settings_store

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(settings_store, "is_workspace_active", lambda: True)
    workspace_token = state._active_workspace_dir.set(str(workspace))
    try:
        result = await _tool_write(
            {"path": "skills/demo/SKILL.md", "content": "# Demo"},
            None,
            0,
            "db.sqlite3",
            None,
        )
        assert "not registered yet" in result
        assert "skill.install" in result
        assert str(workspace / "skills" / "demo") in result
    finally:
        state._active_workspace_dir.reset(workspace_token)


async def test_proactive_rejects_edit_and_shell_file_mutations(monkeypatch):
    from cyrene.agent import state
    from cyrene.tooling.executor import _execute_tool

    meta_token = state._ui_round_assistant_meta.set({"system_initiated": True})
    try:
        edit_result = await _execute_tool(
            "Edit",
            {"path": "existing.md", "old_string": "a", "new_string": "b"},
            None,
            0,
            "db.sqlite3",
            None,
        )
        assert "Editing existing files is forbidden" in edit_result

        shell_result = await _execute_tool(
            "Bash",
            {"command": "echo hello > existing.md"},
            None,
            0,
            "db.sqlite3",
            None,
        )
        assert "cannot run shell commands that write" in shell_result
    finally:
        state._ui_round_assistant_meta.reset(meta_token)


def test_workbench_frontend_handles_proactive_sse():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "webui" / "frontend"
        / "workbench-chat.jsx"
    ).read_text(encoding="utf-8")

    assert 'event.type === "workbench_proactive_message"' in source
    assert "messages.concat([proactiveMessage])" in source
