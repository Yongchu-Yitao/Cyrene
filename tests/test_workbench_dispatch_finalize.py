"""Workbench dispatch — the `finalize` (completion / handoff) intent.

When the user signals the task is done ("任务完成了 / 可以验收了"), the composer must
NOT re-plan: it should summarize the existing deliverables into the reply card and
move the task to `review`, preserving the plan, its revision, and any artifacts.
"""
import json
import asyncio

from route.registry import register_routes


def test_classify_intent_maps_done_to_finalize(monkeypatch):
    from cyrene import workbench_runtime as routes

    async def fake_call_llm(*_args, **_kwargs):
        return {"content": '{"kind":"done"}'}

    monkeypatch.setattr(routes, "_call_llm", fake_call_llm)
    session = {"goal": "导出攻略为 PDF", "plan": [{"id": "s1", "title": "转换", "status": "completed"}]}
    kind = asyncio.run(routes._workbench_classify_intent("任务完成了，把成果给我", session))
    assert kind == "finalize"


def _seed_store(store_path, workspace):
    store_path.write_text(json.dumps({
        "projects": [{
            "id": "project_1",
            "name": "Cyrene",
            "workspacePath": str(workspace),
            "sessions": [{
                "id": "session_1",
                "projectId": "project_1",
                "kind": "task",
                "title": "导出攻略 PDF",
                "goal": "将 guide.md 导出为高质量 PDF 并交付",
                "status": "planning",
                "priority": "medium",
                "constraints": [],
                "planRevision": 4,
                "planDefinitionRevision": 2,
                "approvedPlanDefinitionRevision": None,
                "plan": [
                    {"id": "step_a", "title": "生成 PDF", "status": "completed", "order": 1, "dependsOn": []},
                    {"id": "step_b", "title": "验证质量", "status": "pending", "order": 2, "dependsOn": ["step_a"]},
                ],
                "events": [],
                "runs": [],
                "artifacts": [{
                    "id": "artifact_1",
                    "type": "file_change",
                    "name": "guide.pdf",
                    "path": "guide.pdf",
                    "status": "ready",
                    "createdAt": "2026-06-20T00:00:00+00:00",
                    "summary": "guide.pdf",
                    "source": "workspace_output",
                }],
                "acceptanceCriteria": [{"id": "ac1", "text": "PDF 可打开", "status": "pending"}],
                "createdAt": "2026-06-20T00:00:00+00:00",
                "updatedAt": "2026-06-20T00:00:00+00:00",
            }],
            "createdAt": "2026-06-20T00:00:00+00:00",
            "updatedAt": "2026-06-20T00:00:00+00:00",
        }],
        "activeProjectId": "project_1",
        "activeSessionId": "session_1",
    }), encoding="utf-8")


def test_dispatch_finalize_summarizes_without_replanning(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cyrene import workbench_runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    _seed_store(store_path, tmp_path)

    summary = "任务已完成。\n- 已生成 guide.pdf（589 行，含全部章节）\n- 预算章节核验通过"
    finalize_directive_seen = {}

    async def fake_classify(_text, _session):
        return "finalize"

    async def fake_reply(user_input, session, constraints, **kwargs):
        finalize_directive_seen["ephemeral"] = kwargs.get("ephemeral_system", "")
        return summary

    async def no_archive(*_args, **_kwargs):
        return None

    def boom_plan(*_args, **_kwargs):  # plan generation must never run on finalize
        raise AssertionError("finalize must not generate or revise a plan")

    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "_workbench_classify_intent", fake_classify)
    monkeypatch.setattr(routes, "_workbench_agent_reply", fake_reply)
    monkeypatch.setattr(routes, "_workbench_generate_plan_steps", boom_plan)
    monkeypatch.setattr(routes, "_workbench_archive_run_knowledge", no_archive)
    monkeypatch.setattr(routes, "schedule_capture", lambda *_a, **_k: None)
    monkeypatch.setattr(routes, "append_notification", lambda **_kwargs: {})

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    client = TestClient(app)

    resp = client.post("/api/task-sessions/session_1/dispatch", json={"input": "任务完成了，给我成果"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["replyKind"] == "finalize"

    session = body["session"]
    # Lands in review (待验收), reply carries the deliverable summary.
    assert session["status"] == "review"
    assert session["agentReply"] == summary
    # Plan + its revision are untouched; the existing artifact is preserved.
    assert session["planRevision"] == 4
    assert [s["id"] for s in session["plan"]] == ["step_a", "step_b"]
    assert [a["name"] for a in session["artifacts"]] == ["guide.pdf"]
    # The completion directive was injected so the agent hands off, not re-works.
    assert "收尾交付" in finalize_directive_seen["ephemeral"]
    assert "guide.pdf" in finalize_directive_seen["ephemeral"]


def test_dispatch_answer_uses_task_reply_mode_and_reply_card(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cyrene import workbench_runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    _seed_store(store_path, tmp_path)

    seen = {}
    answer = "可以，当前计划里 PDF 已生成，剩下的是验证质量。"

    async def fake_classify(_text, _session):
        return "answer"

    async def fake_reply(user_input, session, constraints, **kwargs):
        seen["command"] = kwargs.get("command", "")
        seen["ephemeral"] = kwargs.get("ephemeral_system", "")
        return answer

    async def no_archive(*_args, **_kwargs):
        return None

    def boom_plan(*_args, **_kwargs):
        raise AssertionError("answer must not generate or revise a plan")

    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "_workbench_classify_intent", fake_classify)
    monkeypatch.setattr(routes, "_workbench_agent_reply", fake_reply)
    monkeypatch.setattr(routes, "_workbench_generate_plan_steps", boom_plan)
    monkeypatch.setattr(routes, "_workbench_archive_run_knowledge", no_archive)
    monkeypatch.setattr(routes, "schedule_capture", lambda *_a, **_k: None)
    monkeypatch.setattr(routes, "append_notification", lambda **_kwargs: {})

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    client = TestClient(app)

    resp = client.post("/api/task-sessions/session_1/dispatch", json={"input": "现在进展到哪一步了？"})
    assert resp.status_code == 200
    body = resp.json()
    session = body["session"]

    assert body["replyKind"] == "answer"
    assert session["status"] == "answered"
    assert session["agentReply"] == answer
    assert session["planRevision"] == 4
    assert seen["command"] == "workbench-task-reply"
    assert "本轮任务对话回复模式" in seen["ephemeral"]


def test_dispatch_acceptance_repair_does_not_return_500(monkeypatch, tmp_path):
    """A failed verification can be repaired through the normal dispatch path."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cyrene import workbench_runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    _seed_store(store_path, tmp_path)
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    session = payload["projects"][0]["sessions"][0]
    session["status"] = "failed"
    session["verifyReason"] = "PDF 缺少目录。"
    session["acceptanceCriteria"][0].update({"status": "failed", "evidence": "未找到目录页。"})
    store_path.write_text(json.dumps(payload), encoding="utf-8")

    seen = {}

    async def fake_reply(_user_input, _session, _constraints, **kwargs):
        seen["ephemeral"] = kwargs.get("ephemeral_system", "")
        return "已补充目录并重新生成 PDF。"

    async def no_archive(*_args, **_kwargs):
        return None

    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "_workbench_agent_reply", fake_reply)
    monkeypatch.setattr(routes, "_workbench_archive_run_knowledge", no_archive)
    monkeypatch.setattr(routes, "schedule_capture", lambda *_a, **_k: None)
    monkeypatch.setattr(routes, "append_notification", lambda **_kwargs: {})

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    client = TestClient(app)

    resp = client.post(
        "/api/task-sessions/session_1/dispatch",
        json={"input": "按验收结果继续修复", "command": "workbench-task-repair"},
    )
    assert resp.status_code == 200
    assert resp.json()["replyKind"] == "repair"
    assert "验收未完全通过" in seen["ephemeral"]
    assert "PDF 缺少目录" in seen["ephemeral"]
