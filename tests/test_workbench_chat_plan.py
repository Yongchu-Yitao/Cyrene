from pathlib import Path


def test_chat_plan_persists_markdown_and_tracks_step_progress(monkeypatch, tmp_path):
    from cyrene.io_utils import atomic_write_json
    from webui import routes_workbench_chat as chat_routes

    store_path = tmp_path / "workbench_chats.json"
    atomic_write_json(store_path, {
        "chats": [{
            "id": "chat_1",
            "projectId": "project_1",
            "kind": "chat",
            "title": "Plan test",
            "status": "idle",
            "messages": [],
        }]
    })
    monkeypatch.setattr(chat_routes, "_CHATS_STORE", store_path)
    monkeypatch.setattr(chat_routes, "_STORE_DB_PATH", "")
    monkeypatch.setattr(chat_routes, "_CONFIGURED_CHATS_STORE", None)

    plan = chat_routes.persist_chat_plan(
        "chat_1",
        {
            "title": "实现计划",
            "summary": "逐步完成",
            "steps": [
                {"title": "检查", "tasks": ["读取代码"]},
                {"title": "实现", "tasks": ["修改代码"]},
            ],
        },
        round_id="round_1",
        workspace_dir=tmp_path / "workspace",
    )
    markdown_path = Path(plan["markdownPath"])
    assert markdown_path.parent.name == "plan"
    assert markdown_path.exists()
    assert "# 实现计划" in markdown_path.read_text(encoding="utf-8")

    active = chat_routes.activate_chat_plan("chat_1", plan)
    assert active["status"] == "active"
    assert active["steps"][0]["status"] == "in_progress"

    progressed = chat_routes.update_chat_plan_progress(
        "chat_1", 1, "completed", "代码已读取"
    )
    assert progressed["steps"][0]["status"] == "completed"
    assert progressed["steps"][0]["note"] == "代码已读取"

    progressed = chat_routes.update_chat_plan_progress("chat_1", 2, "in_progress")
    assert progressed["steps"][1]["status"] == "in_progress"
    assert "[~] 2. 实现" in markdown_path.read_text(encoding="utf-8")

    chat_routes.complete_chat_plan("chat_1")
    stored = chat_routes._read_chats_store()["chats"][0]
    assert stored["activePlan"]["status"] == "completed"
    assert "activePlan" not in chat_routes._public_chat_full(stored)


def test_workbench_plan_progress_tool_is_main_only():
    from cyrene.registry_tools import AGENT_TOOL_GROUPS, get_tool_names

    assert "update_plan_progress" in get_tool_names()
    assert "update_plan_progress" in AGENT_TOOL_GROUPS["subagent_blocklist"]
