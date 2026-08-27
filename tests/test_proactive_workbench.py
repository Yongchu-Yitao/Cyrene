import json
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def _write_chats(path, chats):
    path.write_text(json.dumps({"chats": chats}, ensure_ascii=False), encoding="utf-8")


def test_latest_workbench_user_activity_uses_user_timestamp(monkeypatch, tmp_path):
    from agent.plugin.plugin_impl.cyrene_proactive import service as scheduler

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
    monkeypatch.setattr(
        scheduler,
        "ChatRepository",
        lambda _db: type("JsonRepository", (), {
            "read": lambda self: json.loads(chats_path.read_text(encoding="utf-8")),
        })(),
    )
    monkeypatch.setattr(scheduler, "_workbench_db_path", str(chats_path))

    latest = scheduler._latest_workbench_user_activity()

    assert latest is not None
    assert latest["chat_id"] == "chat_latest"
    assert latest["project_id"] == "project_b"
    assert latest["timestamp"] == datetime(2026, 6, 17, 11, tzinfo=timezone.utc)


def test_silence_detection_includes_workbench_user_activity(monkeypatch, tmp_path):
    from agent.plugin.plugin_impl.cyrene_proactive import service as scheduler

    _write_chats(tmp_path / "workbench_chats.json", [
        {
            "id": "chat_1",
            "projectId": "project_1",
            "lastUserMessageAt": "2026-06-18T02:03:04+00:00",
            "messages": [],
        }
    ])
    monkeypatch.setattr(
        scheduler,
        "ChatRepository",
        lambda _db: type("JsonRepository", (), {
            "read": lambda self: json.loads((tmp_path / "workbench_chats.json").read_text(encoding="utf-8")),
        })(),
    )
    monkeypatch.setattr(scheduler, "_workbench_db_path", str(tmp_path / "workbench_chats.json"))
    monkeypatch.setattr(
        scheduler,
        "_memory_service",
        lambda: SimpleNamespace(latest_archived_user_message_time=lambda: None),
    )
    assert scheduler._last_user_message_time() == datetime(
        2026, 6, 18, 2, 3, 4, tzinfo=timezone.utc
    )


async def test_proactive_skips_when_latest_workbench_chat_is_running(
    monkeypatch, tmp_path
):
    from agent.plugin.plugin_impl.cyrene_proactive import service as scheduler

    _write_chats(tmp_path / "workbench_chats.json", [
        {
            "id": "chat_busy",
            "projectId": "project_1",
            "lastUserMessageAt": "2026-06-17T02:03:04+00:00",
            "messages": [],
        }
    ])
    monkeypatch.setattr(
        scheduler,
        "ChatRepository",
        lambda _db: type("JsonRepository", (), {
            "read": lambda self: json.loads((tmp_path / "workbench_chats.json").read_text(encoding="utf-8")),
        })(),
    )
    monkeypatch.setattr(scheduler, "_workbench_db_path", str(tmp_path / "workbench_chats.json"))
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(
        scheduler,
        "_is_workbench_conversation_running",
        lambda _db_path, session_id: session_id == "chat_busy",
    )
    run = AsyncMock()
    monkeypatch.setattr(scheduler, "_run_plugin_proactive_turn", run)
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
    from agent.plugin.plugin_impl.cyrene_proactive import service as scheduler

    db_path = str(tmp_path / "runtime.sqlite3")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from cyrene.workbench.chat_repository import ChatRepository

    repository = ChatRepository(db_path)
    repository.write({"chats": [
        {
            "id": "chat_latest",
            "projectId": "project_1",
            "title": "Launch",
            "model": "test-model",
            "lastUserMessageAt": "2026-06-14T02:03:04+00:00",
            "messages": [],
        }
    ]})
    events = []

    async def publish(event, **_kwargs):
        events.append(event)

    captured = {}

    async def run_agent(prompt, **kwargs):
        session_id = kwargs.get("session_id", "")
        assert session_id.startswith("wbchat_")
        assert session_id != "chat_latest"
        captured["session_id"] = session_id
        captured["lang"] = kwargs.get("lang", "")
        captured["workspace_dir"] = kwargs.get("workspace_dir", "")
        return SimpleNamespace(
            text="How did the launch go?",
            model="test-model",
            pending_question=None,
        )

    import cyrene.runtime.settings_store as settings_store
    monkeypatch.setattr(
        settings_store, "get",
        lambda key, default=None: "zh" if key == "app_language" else default,
    )
    import cyrene.workbench.chat_application as chat_application
    monkeypatch.setattr(
        chat_application,
        "_composer_context_service",
        lambda: SimpleNamespace(
            default_input_context=lambda: {
                "soulActive": False,
                "workspaceActive": True,
            },
            normalize=lambda value: value if isinstance(value, dict) else {},
        ),
    )
    monkeypatch.setattr(scheduler, "_workbench_db_path", db_path)
    monkeypatch.setattr(
        scheduler,
        "_workbench_workspace_dir_for_project",
        lambda _project_id: str(workspace),
    )
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_save_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "_silence_hours", lambda: 96.0)
    monkeypatch.setattr(
        scheduler,
        "_is_workbench_conversation_running",
        lambda _db_path, _session_id: False,
    )
    monkeypatch.setattr(scheduler, "_run_plugin_proactive_turn", run_agent)
    monkeypatch.setattr(scheduler, "notify", AsyncMock())
    monkeypatch.setattr(scheduler, "append_notification", lambda **_kwargs: {})
    monkeypatch.setattr(debug, "publish_event", publish)
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=0,
        cooldown_until=0.0,
        last_proactive_time=0.0,
        probability=0.0,
    )

    await scheduler._heartbeat_proactive_check(
        None,
        db_path,
    )

    saved = repository.read()
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
    # ChatRepository persistence and the dedicated proactive debug event are
    # the observable contracts; chat-change SSE publication has its own event
    # channel and is not folded into the debug stream.
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 1
    # The persisted UI language must be threaded into the proactive agent run.
    assert captured["lang"] == "zh"
    assert captured["workspace_dir"] == str(workspace)


def test_workbench_frontend_handles_proactive_sse():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/webui/frontend/features/chat/live-event-controller.jsx"
    ).read_text(encoding="utf-8")

    assert 'event.type === "workbench_proactive_message"' in source
    assert "wbcApplyProactiveMessage(context, event)" in source
    assert "messages.some(function (item) { return item.id === message.id; })" in source
    assert "messages: messages.concat([message])" in source
