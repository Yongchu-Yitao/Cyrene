from pathlib import Path

import pytest


def test_chat_plan_persists_markdown_and_tracks_step_progress(monkeypatch, tmp_path):
    from cyrene.runtime.io import atomic_write_json
    from cyrene.workbench import chat as chat_routes

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
    from cyrene.tooling.catalog import AGENT_TOOL_GROUPS, get_tool_names

    assert "update_plan_progress" in get_tool_names()
    assert "update_plan_progress" in AGENT_TOOL_GROUPS["subagent_blocklist"]


def test_plan_mode_allows_discovery_before_entering_plan_flow():
    source = (Path(__file__).resolve().parent.parent / "src" / "cyrene" / "agent" / "coordinator.py").read_text(
        encoding="utf-8"
    )

    assert "Plan Mode Discovery" in source
    assert "pre-plan tool discovery" in source
    assert "run_plan_flow(" not in source


@pytest.mark.asyncio
async def test_generate_plan_includes_pre_plan_tool_history(monkeypatch):
    from cyrene.agent import planning

    seen = {}

    async def fake_call_llm(messages, tools=None, max_tokens=None, thinking=None):
        seen["content"] = "\n\n".join(str(m.get("content") or "") for m in messages)
        return {
            "tool_calls": [{
                "function": {
                    "name": "submit_plan",
                    "arguments": (
                        '{"title":"计划","steps":[{"title":"基于发现规划",'
                        '"tasks":["使用已读取的配置结论"]}]}'
                    ),
                }
            }]
        }

    monkeypatch.setattr("cyrene.agent.state._call_llm", fake_call_llm)
    plan = await planning.generate_plan(
        "优化配置",
        history=[
            {"role": "assistant", "tool_calls": [{"function": {"name": "Read"}}]},
            {"role": "tool", "content": "配置文件显示当前启用了 strict 模式"},
        ],
    )

    assert plan["steps"][0]["title"] == "基于发现规划"
    assert "生成计划前已收集到的上下文" in seen["content"]
    assert "strict 模式" in seen["content"]


@pytest.mark.asyncio
async def test_plan_confirmation_can_resume_with_auto_mode(monkeypatch):
    from cyrene.agent import coordinator, guidance, state
    from cyrene.workbench import chat as routes_workbench_chat

    seen = {}

    async def fake_run_chat_agent(user_message, bot, chat_id, db_path, **kwargs):
        seen.update(kwargs)
        return "done"

    async def fake_publish(_event):
        return None

    monkeypatch.setattr(coordinator, "_run_chat_agent", fake_run_chat_agent)
    monkeypatch.setattr(state, "_publish_runtime_event", fake_publish)
    monkeypatch.setattr(
        routes_workbench_chat,
        "activate_chat_plan",
        lambda _chat_id, plan: {**plan, "status": "active"},
    )
    token = state._current_session_id.set("chat_1")
    try:
        result = await guidance._handle_plan_confirmation_answer(
            round_id="round_1",
            pending={
                "meta": {
                    "kind": "plan_confirmation",
                    "plan": {"title": "计划", "steps": [{"title": "做事", "tasks": []}]},
                    "user_message": "完成任务",
                }
            },
            answer_text="同意并开始",
            client_request_id="req_1",
            context={"round_history": [], "persist_base_messages": [], "persist_insert_at": 0},
            permission_mode="auto",
        )
    finally:
        state._current_session_id.reset(token)

    assert result == "done"
    assert seen["permission_mode"] == "auto"
