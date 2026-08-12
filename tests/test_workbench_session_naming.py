import asyncio

import pytest


@pytest.mark.asyncio
async def test_generate_session_title_uses_exact_candidate_without_truncation(monkeypatch):
    from cyrene.agent import model_service
    from cyrene.workbench.session_naming import generate_session_title

    captured = {}

    async def fake_call(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return {"content": "  修复登录超时问题。  "}

    monkeypatch.setattr(model_service, "call_agent_model", fake_call)

    message = "请帮我排查登录接口偶发超时" * 300
    candidate = {"id": "chosen", "model": "chosen-model"}
    title = await generate_session_title(message, limit=60, candidate=candidate)

    assert title == "修复登录超时问题"
    assert captured["messages"][-1]["content"] == message
    assert captured["kwargs"]["candidates"] == [candidate]
    assert captured["kwargs"]["max_tokens"] is None
    assert "response_format" not in captured["kwargs"]


def test_task_session_is_llm_named_only_once(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cyrene.runtime import database as db
    from cyrene.workbench import runtime, session_naming
    from route.registry import register_routes

    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_dir.mkdir()
    workspace.mkdir()
    store_path = data_dir / "workbench_projects.json"
    store_path.write_text(
        """{"projects":[{"id":"project_1","name":"Cyrene","workspacePath":"%s","sessions":[{"id":"session_1","projectId":"project_1","kind":"task","title":"新任务","goal":"","status":"idle","constraints":[],"plan":[],"events":[],"runs":[],"artifacts":[],"acceptanceCriteria":[]}]}],"activeProjectId":"project_1","activeSessionId":"session_1"}"""
        % str(workspace).replace("\\", "\\\\"),
        encoding="utf-8",
    )
    db_path = str(tmp_path / "test.db")
    asyncio.run(db.init_db(db_path))
    monkeypatch.setattr(runtime, "DATA_DIR", data_dir)
    monkeypatch.setattr(runtime, "_WORKBENCH_STORE", store_path)

    calls = []

    async def fake_name(message, *, limit=60, candidate=None):
        calls.append(message)
        assert candidate is not None
        return "实现单次 Session 命名"

    async def fake_classify(_text, _session):
        return "plan"

    async def fake_plan(_session, _project, feedback=""):
        return ([{"id": "step_1", "title": "实现", "status": "pending"}], [], True, "replace")

    monkeypatch.setattr(session_naming, "generate_session_title", fake_name)
    monkeypatch.setattr(runtime, "_workbench_classify_intent", fake_classify)
    monkeypatch.setattr(runtime, "_workbench_generate_plan_steps", fake_plan)

    app = FastAPI()
    register_routes(app, bot=None, db_path=db_path)
    client = TestClient(app)

    first = client.post(
        "/api/task-sessions/session_1/dispatch",
        json={"input": "把 session 的 LLM 命名加回来"},
    )
    second = client.post(
        "/api/task-sessions/session_1/dispatch",
        json={"input": "计划再补充一个步骤"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["session"]["title"] == "实现单次 Session 命名"
    assert second.json()["session"]["title"] == "实现单次 Session 命名"
    assert calls == ["把 session 的 LLM 命名加回来"]
