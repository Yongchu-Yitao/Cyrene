import sys
import os
import asyncio
import subprocess
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from route.registry import register_routes


def test_workbench_init_task_plan_normalizes_llm_payload():
    from cyrene.workbench.runtime import _workbench_coerce_init_task_plan

    fallback = [{"title": "fallback", "goal": "fallback", "priority": "medium"}]
    plan = _workbench_coerce_init_task_plan(
        {
            "tasks": [
                {
                    "title": "  明确 MVP 范围  ",
                    "goal": "确定首版目标",
                    "priority": "urgent",
                    "constraints": ["  只做 Web 端  ", ""],
                    "acceptanceCriteria": ["范围已确认", ""],
                },
                {"description": "补齐登录注册流程"},
                {"title": ""},
            ]
        },
        fallback,
    )

    assert len(plan) == 2
    assert plan[0]["title"] == "明确 MVP 范围"
    assert plan[0]["priority"] == "medium"
    assert plan[0]["constraints"] == ["只做 Web 端"]
    assert plan[0]["acceptanceCriteria"] == ["范围已确认"]
    assert plan[1]["title"] == "补齐登录注册流程"


def test_workbench_init_tool_creates_task_sessions_from_major_plan():
    from cyrene.workbench.runtime import _workbench_create_sessions_from_init_plan

    project = {"id": "project_1", "sessions": [{"id": "init_1", "kind": "init"}]}
    created = _workbench_create_sessions_from_init_plan(
        project,
        [
            {
                "title": "明确范围",
                "goal": "整理需求边界",
                "priority": "high",
                "constraints": ["范围限制：不做移动端"],
                "acceptanceCriteria": ["需求边界已确认"],
            },
            {"title": "实现核心功能", "goal": "交付 MVP", "priority": "medium"},
        ],
        "2026-06-11T00:00:00+00:00",
    )

    assert [session["title"] for session in created] == ["明确范围", "实现核心功能"]
    assert project["sessions"][0]["title"] == "明确范围"
    assert project["sessions"][1]["title"] == "实现核心功能"
    assert project["sessions"][2]["id"] == "init_1"
    assert created[0]["kind"] == "task"
    assert created[0]["priority"] == "high"
    assert created[0]["constraints"] == ["范围限制：不做移动端"]
    assert created[0]["acceptanceCriteria"][0]["text"] == "需求边界已确认"
    assert created[0]["events"][0]["type"] == "CreatedFromInitPlan"


def test_workbench_follow_up_seed_uses_current_task_state():
    from cyrene.workbench.runtime import _workbench_follow_up_seed

    seed = _workbench_follow_up_seed({
        "title": "修复登录流程",
        "goal": "让用户可以稳定登录",
        "status": "failed",
        "priority": "high",
        "summary": {"text": "接口已完成，浏览器回归仍失败"},
        "constraints": ["不要修改认证协议"],
        "plan": [
            {"title": "实现登录接口", "status": "completed"},
            {"title": "修复浏览器回归", "status": "failed"},
        ],
        "acceptanceCriteria": [
            {"text": "接口测试通过", "status": "passed"},
            {"text": "浏览器登录成功", "status": "failed"},
        ],
        "reflection": {"packet": {"next_step": "检查登录页事件处理"}},
    })

    assert seed["title"] == "修复登录流程 · 后续"
    assert seed["priority"] == "high"
    assert seed["constraints"] == ["不要修改认证协议"]
    assert "来源任务当前状态：失败" in seed["goal"]
    assert "尚未解决的步骤：修复浏览器回归" in seed["goal"]
    assert "尚未满足的验收项：浏览器登录成功" in seed["goal"]
    assert "反思建议的下一步：检查登录页事件处理" in seed["goal"]
    assert seed["unresolvedAcceptance"] == ["浏览器登录成功"]


def test_workbench_follow_up_seed_keeps_explicit_request_with_source_context():
    from cyrene.workbench.runtime import _workbench_follow_up_seed

    seed = _workbench_follow_up_seed(
        {
            "title": "整理发布说明",
            "goal": "输出版本发布说明",
            "status": "completed",
            "constraints": [],
        },
        requested_title="补充英文版本",
        requested_goal="另外制作一份英文发布说明",
    )

    assert seed["title"] == "补充英文版本"
    assert "本次后续要求：另外制作一份英文发布说明" in seed["goal"]
    assert "来源任务目标：输出版本发布说明" in seed["goal"]
    assert "来源任务当前状态：已完成" in seed["goal"]


def test_workbench_follow_up_endpoint_creates_linked_session(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cyrene.workbench import runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    source_session = {
        "id": "session_source",
        "projectId": "project_1",
        "kind": "task",
        "title": "修复登录流程",
        "goal": "让用户可以稳定登录",
        "status": "failed",
        "priority": "high",
        "constraints": ["不要修改认证协议"],
        "plan": [{"id": "step_1", "title": "修复浏览器回归", "status": "failed"}],
        "acceptanceCriteria": [{"id": "accept_1", "text": "浏览器登录成功", "status": "failed"}],
        "events": [],
        "runs": [],
        "artifacts": [],
        "agentReply": "登录接口完成，但页面点击没有响应。",
        "summary": "浏览器回归仍失败",
        "createdAt": "2026-06-19T00:00:00+00:00",
        "updatedAt": "2026-06-19T00:00:00+00:00",
    }
    store_path.write_text(json.dumps({
        "projects": [{
            "id": "project_1",
            "name": "Cyrene",
            "sessions": [source_session],
            "createdAt": "2026-06-19T00:00:00+00:00",
            "updatedAt": "2026-06-19T00:00:00+00:00",
        }],
        "activeProjectId": "project_1",
        "activeSessionId": "session_source",
    }), encoding="utf-8")

    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "append_notification", lambda **_kwargs: {})

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    response = TestClient(app).post(
        "/api/task-sessions/session_source/follow-up",
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    created = payload["session"]
    assert created["title"] == "修复登录流程 · 后续"
    assert created["parentSessionId"] == "session_source"
    assert created["priority"] == "high"
    assert created["constraints"] == ["不要修改认证协议"]
    assert created["acceptanceCriteria"][0]["text"] == "浏览器登录成功"
    assert created["events"][0]["type"] == "CreatedAsFollowUp"
    assert payload["activeSessionId"] == created["id"]
    assert payload["projects"][0]["sessions"][0]["id"] == created["id"]


def test_workbench_plan_revision_preserves_existing_steps_when_feedback_is_supplemental():
    from cyrene.workbench.runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [
        _workbench_new_plan_step("读取项目上下文", "理解当前实现", 1, "task_1"),
        _workbench_new_plan_step("执行验证", "运行相关检查", 2, "task_1"),
    ]
    generated = [
        _workbench_new_plan_step("使用 torch 环境执行验证", "通过 conda run -n torch 运行检查", 1, "task_1"),
    ]

    merged = _workbench_reconcile_revised_plan(existing, generated, "你可以用 conda 环境 torch")

    assert [step["title"] for step in merged] == ["读取项目上下文", "执行验证", "使用 torch 环境执行验证"]
    assert merged[0]["id"] == existing[0]["id"]
    assert merged[2]["status"] == "pending"


def test_workbench_plan_graph_rejects_cycles_missing_dependencies_and_invalid_order():
    from cyrene.workbench.runtime import _workbench_validate_plan_graph

    valid, _, code = _workbench_validate_plan_graph([
        {"id": "a", "title": "A", "dependsOn": []},
        {"id": "b", "title": "B", "dependsOn": ["a"]},
    ])
    assert valid is True
    assert code == ""

    valid, _, code = _workbench_validate_plan_graph([
        {"id": "a", "title": "A", "dependsOn": ["missing"]},
    ])
    assert valid is False
    assert code == "missing_dependency"

    valid, _, code = _workbench_validate_plan_graph([
        {"id": "b", "title": "B", "dependsOn": ["a"]},
        {"id": "a", "title": "A", "dependsOn": []},
    ])
    assert valid is False
    assert code == "dependency_order"

    valid, _, code = _workbench_validate_plan_graph([
        {"id": "a", "title": "A", "dependsOn": ["b"]},
        {"id": "b", "title": "B", "dependsOn": ["a"]},
    ], require_dependency_order=False)
    assert valid is False
    assert code == "dependency_cycle"


def test_workbench_plan_coercion_resolves_dependency_indexes():
    from cyrene.workbench.runtime import _workbench_coerce_plan_steps

    steps = _workbench_coerce_plan_steps(
        {
            "steps": [
                {"title": "读取上下文", "dependsOnStepIndexes": []},
                {"title": "实现功能", "dependsOnStepIndexes": [1]},
                {"title": "运行测试", "dependsOnStepIndexes": [1, 2, 9]},
            ]
        },
        {"id": "task_1"},
    )

    assert steps[0]["dependsOn"] == []
    assert steps[1]["dependsOn"] == [steps[0]["id"]]
    assert steps[2]["dependsOn"] == [steps[0]["id"], steps[1]["id"]]


def test_workbench_plan_mutation_endpoint_validates_revision_dependencies_and_started_state(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cyrene.workbench import runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    store_path.write_text(json.dumps({
        "projects": [{
            "id": "project_1",
            "name": "Cyrene",
            "sessions": [{
                "id": "session_1",
                "projectId": "project_1",
                "kind": "task",
                "title": "依赖计划",
                "goal": "验证依赖计划",
                "status": "planning",
                "priority": "medium",
                "constraints": [],
                "planRevision": 8,
                "planDefinitionRevision": 3,
                "approvedPlanDefinitionRevision": 3,
                "plan": [
                    {"id": "step_a", "title": "A", "status": "pending", "order": 1, "dependsOn": []},
                    {"id": "step_b", "title": "B", "status": "pending", "order": 2, "dependsOn": ["step_a"]},
                ],
                "events": [],
                "runs": [],
                "artifacts": [],
                "acceptanceCriteria": [],
                "createdAt": "2026-06-19T00:00:00+00:00",
                "updatedAt": "2026-06-19T00:00:00+00:00",
            }],
            "createdAt": "2026-06-19T00:00:00+00:00",
            "updatedAt": "2026-06-19T00:00:00+00:00",
        }],
        "activeProjectId": "project_1",
        "activeSessionId": "session_1",
    }), encoding="utf-8")

    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "append_notification", lambda **_kwargs: {})
    monkeypatch.setattr(routes, "is_session_running", lambda _session_id: False)
    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    client = TestClient(app)

    stale = client.patch("/api/task-sessions/session_1/plan", json={
        "operation": "reorder",
        "basePlanRevision": 2,
        "orderedStepIds": ["step_a", "step_b"],
    })
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_plan_revision"

    invalid = client.patch("/api/task-sessions/session_1/plan", json={
        "operation": "reorder",
        "basePlanRevision": 3,
        "orderedStepIds": ["step_b", "step_a"],
    })
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "dependency_order"

    has_dependents = client.patch("/api/task-sessions/session_1/plan", json={
        "operation": "delete",
        "basePlanRevision": 3,
        "stepId": "step_a",
    })
    assert has_dependents.status_code == 409
    assert has_dependents.json()["code"] == "step_has_dependents"

    updated = client.patch("/api/task-sessions/session_1/plan", json={
        "operation": "update",
        "basePlanRevision": 3,
        "stepId": "step_b",
        "fields": {"title": "B2", "description": "changed", "dependsOn": ["step_a"]},
    })
    assert updated.status_code == 200
    session = updated.json()["session"]
    assert session["plan"][1]["title"] == "B2"
    assert session["planDefinitionRevision"] == 4
    assert session["approvedPlanDefinitionRevision"] is None

    stored = routes._read_workbench_store()
    stored["projects"][0]["sessions"][0]["plan"][0]["status"] = "completed"
    routes._write_workbench_store(stored)
    deleted = client.patch("/api/task-sessions/session_1/plan", json={
        "operation": "delete",
        "basePlanRevision": 4,
        "stepId": "step_b",
    })
    assert deleted.status_code == 200
    deleted_session = deleted.json()["session"]
    assert [step["id"] for step in deleted_session["plan"]] == ["step_a"]
    assert deleted_session["planDefinitionRevision"] == 5

    locked = client.patch("/api/task-sessions/session_1/plan", json={
        "operation": "delete",
        "basePlanRevision": 5,
        "stepId": "step_a",
    })
    assert locked.status_code == 409
    assert locked.json()["code"] == "step_started"


def test_workbench_step_run_rejects_unmet_dependencies_before_agent_call(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cyrene.workbench import runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    store_path.write_text(json.dumps({
        "projects": [{
            "id": "project_1",
            "name": "Cyrene",
            "workspacePath": str(tmp_path),
            "sessions": [{
                "id": "session_1",
                "projectId": "project_1",
                "kind": "task",
                "title": "依赖执行",
                "goal": "验证执行守卫",
                "status": "running",
                "priority": "medium",
                "constraints": [],
                "planRevision": 2,
                "planDefinitionRevision": 1,
                "approvedPlanDefinitionRevision": 1,
                "plan": [
                    {"id": "step_a", "title": "A", "status": "pending", "order": 1, "dependsOn": []},
                    {"id": "step_b", "title": "B", "status": "running", "order": 2, "dependsOn": ["step_a"]},
                ],
                "events": [],
                "runs": [],
                "artifacts": [],
                "acceptanceCriteria": [],
                "createdAt": "2026-06-19T00:00:00+00:00",
                "updatedAt": "2026-06-19T00:00:00+00:00",
            }],
            "createdAt": "2026-06-19T00:00:00+00:00",
            "updatedAt": "2026-06-19T00:00:00+00:00",
        }],
        "activeProjectId": "project_1",
        "activeSessionId": "session_1",
    }), encoding="utf-8")
    called = False

    async def fake_reply(*_args, **_kwargs):
        nonlocal called
        called = True
        return "should not run"

    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "_workbench_agent_reply", fake_reply)
    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))

    client = TestClient(app)
    stale = client.post("/api/task-sessions/session_1/runs", json={
        "input": "run A",
        "stepId": "step_a",
        "stepTitle": "A",
        "action": "spawn_subagent",
        "planDefinitionRevision": 0,
    })
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_plan_revision"

    stored = routes._read_workbench_store()
    stored["projects"][0]["sessions"][0]["approvedPlanDefinitionRevision"] = None
    routes._write_workbench_store(stored)
    unapproved = client.post("/api/task-sessions/session_1/runs", json={
        "input": "run A",
        "stepId": "step_a",
        "stepTitle": "A",
        "action": "spawn_subagent",
        "planDefinitionRevision": 1,
    })
    assert unapproved.status_code == 409
    assert unapproved.json()["code"] == "plan_not_approved"

    stored["projects"][0]["sessions"][0]["approvedPlanDefinitionRevision"] = 1
    routes._write_workbench_store(stored)
    response = client.post("/api/task-sessions/session_1/runs", json={
        "input": "run B",
        "stepId": "step_b",
        "stepTitle": "B",
        "action": "spawn_subagent",
        "planDefinitionRevision": 1,
    })

    assert response.status_code == 409
    assert response.json()["code"] == "unmet_dependencies"
    assert called is False


def test_workbench_step_run_commits_completion_and_real_timestamps(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cyrene.workbench import runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    store_path.write_text(json.dumps({
        "projects": [{
            "id": "project_1",
            "name": "Cyrene",
            "workspacePath": str(tmp_path),
            "sessions": [{
                "id": "session_1",
                "projectId": "project_1",
                "kind": "task",
                "title": "原子完成",
                "goal": "完成一个步骤",
                "status": "running",
                "planRevision": 2,
                "planDefinitionRevision": 1,
                "approvedPlanDefinitionRevision": 1,
                "plan": [{
                    "id": "step_a", "title": "A", "status": "running",
                    "order": 1, "dependsOn": [],
                    "startedAt": "2026-06-19T00:00:00+00:00",
                }],
                "events": [], "runs": [], "artifacts": [],
                "acceptanceCriteria": [],
                "createdAt": "2026-06-19T00:00:00+00:00",
                "updatedAt": "2026-06-19T00:00:00+00:00",
            }],
            "createdAt": "2026-06-19T00:00:00+00:00",
            "updatedAt": "2026-06-19T00:00:00+00:00",
        }],
        "activeProjectId": "project_1",
        "activeSessionId": "session_1",
    }), encoding="utf-8")

    async def fake_reply(*_args, **_kwargs):
        await asyncio.sleep(0.01)
        return "步骤已经真正执行完成"

    async def no_outcome(*_args, **_kwargs):
        return None

    async def no_archive(*_args, **_kwargs):
        return None

    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "_workbench_agent_reply", fake_reply)
    monkeypatch.setattr(routes, "_workbench_generate_step_outcome", no_outcome)
    monkeypatch.setattr(routes, "_workbench_archive_run_knowledge", no_archive)
    monkeypatch.setattr(routes, "schedule_capture", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "append_notification", lambda **_kwargs: {})

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    response = TestClient(app).post("/api/task-sessions/session_1/runs", json={
        "input": "执行 A",
        "stepId": "step_a",
        "stepTitle": "A",
        "action": "spawn_subagent",
        "planDefinitionRevision": 1,
    })

    assert response.status_code == 200
    session = response.json()["session"]
    run = response.json()["run"]
    assert session["plan"][0]["status"] == "completed"
    assert session["status"] == "review"
    assert run["status"] == "completed"
    assert run["endedAt"] > run["startedAt"]
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert stored["projects"][0]["sessions"][0]["plan"][0]["status"] == "completed"


def test_workbench_step_run_persists_agent_failure(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cyrene.workbench import runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    store_path.write_text(json.dumps({
        "projects": [{
            "id": "project_1", "name": "Cyrene", "workspacePath": str(tmp_path),
            "sessions": [{
                "id": "session_1", "projectId": "project_1", "kind": "task",
                "title": "失败落库", "goal": "执行步骤", "status": "running",
                "planRevision": 1, "planDefinitionRevision": 1,
                "approvedPlanDefinitionRevision": 1,
                "plan": [{"id": "step_a", "title": "A", "status": "running", "order": 1, "dependsOn": []}],
                "events": [], "runs": [], "artifacts": [], "acceptanceCriteria": [],
                "createdAt": "2026-06-19T00:00:00+00:00", "updatedAt": "2026-06-19T00:00:00+00:00",
            }],
            "createdAt": "2026-06-19T00:00:00+00:00", "updatedAt": "2026-06-19T00:00:00+00:00",
        }],
        "activeProjectId": "project_1", "activeSessionId": "session_1",
    }), encoding="utf-8")

    async def failed_reply(*_args, **_kwargs):
        raise routes._WorkbenchAgentRunError("workbench_agent_run_failed", "Agent 执行失败：boom")

    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "_workbench_agent_reply", failed_reply)
    monkeypatch.setattr(routes, "append_notification", lambda **_kwargs: {})

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    response = TestClient(app).post("/api/task-sessions/session_1/runs", json={
        "input": "执行 A", "stepId": "step_a", "action": "spawn_subagent",
        "planDefinitionRevision": 1,
    })

    assert response.status_code == 502
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    session = stored["projects"][0]["sessions"][0]
    assert session["status"] == "failed"
    assert session["plan"][0]["status"] == "failed"
    assert session["runs"][0]["status"] == "failed"
    assert session["runs"][0]["error"] == "Agent 执行失败：boom"


def test_workbench_plan_revision_drops_only_invalid_dependency_edges():
    from cyrene.workbench.runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [
        _workbench_new_plan_step("A", "", 1, "task_1"),
        _workbench_new_plan_step("B", "", 2, "task_1"),
        _workbench_new_plan_step("C", "", 3, "task_1"),
    ]
    existing[1]["dependsOn"] = [existing[0]["id"]]
    existing[2]["dependsOn"] = [existing[1]["id"]]
    generated = [
        _workbench_new_plan_step("B", "move first", 1, "task_1"),
        _workbench_new_plan_step("A", "move second", 2, "task_1"),
        _workbench_new_plan_step("C", "keep last", 3, "task_1"),
    ]
    generated[0]["sourceStepId"] = existing[1]["id"]
    generated[1]["sourceStepId"] = existing[0]["id"]
    generated[2]["sourceStepId"] = existing[2]["id"]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "把 B 移到 A 前面", operation="revise"
    )

    assert [step["title"] for step in merged] == ["B", "A", "C"]
    assert merged[0]["dependsOn"] == []
    assert merged[2]["dependsOn"] == [existing[1]["id"]]


def test_workbench_permission_denial_returns_plan_step_to_pending(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cyrene.workbench import runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    store_path.write_text(json.dumps({
        "projects": [{
            "id": "project_1",
            "name": "Cyrene",
            "workspacePath": str(tmp_path),
            "sessions": [{
                "id": "session_1",
                "projectId": "project_1",
                "kind": "task",
                "title": "权限处理",
                "goal": "验证权限拒绝",
                "status": "waiting_for_user",
                "priority": "medium",
                "constraints": [],
                "planRevision": 2,
                "planDefinitionRevision": 1,
                "approvedPlanDefinitionRevision": 1,
                "plan": [{
                    "id": "step_a",
                    "title": "执行写入",
                    "status": "running",
                    "order": 1,
                    "dependsOn": [],
                    "startedAt": "2026-06-19T00:00:00+00:00",
                }],
                "pendingQuestion": {
                    "id": "question_1",
                    "kind": "write_permission_request",
                    "options": [],
                },
                "pendingPlanStep": {"stepId": "step_a", "continueAll": True},
                "events": [],
                "runs": [],
                "artifacts": [],
                "acceptanceCriteria": [],
                "createdAt": "2026-06-19T00:00:00+00:00",
                "updatedAt": "2026-06-19T00:00:00+00:00",
            }],
            "createdAt": "2026-06-19T00:00:00+00:00",
            "updatedAt": "2026-06-19T00:00:00+00:00",
        }],
        "activeProjectId": "project_1",
        "activeSessionId": "session_1",
    }), encoding="utf-8")

    async def fake_answer(*_args, **_kwargs):
        return "已拒绝权限请求。"

    async def fake_archive(*_args, **_kwargs):
        return None

    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "_workbench_answer_pending", fake_answer)
    monkeypatch.setattr(
        routes,
        "_workbench_apply_pending",
        lambda *_args: ("已拒绝权限请求。", False),
    )
    monkeypatch.setattr(routes, "_workbench_archive_run_knowledge", fake_archive)
    monkeypatch.setattr(routes, "schedule_capture", lambda *_args, **_kwargs: None)

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    response = TestClient(app).post(
        "/api/task-sessions/session_1/answer",
        json={"question_id": "question_1", "answer": "拒绝"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["continuePlanExecution"] is False
    assert payload["session"]["status"] == "paused"
    assert payload["session"]["plan"][0]["status"] == "pending"
    assert payload["session"]["plan"][0]["startedAt"] is None
    assert "pendingPlanStep" not in payload["session"]


def test_workbench_plan_revision_allows_explicit_replacement():
    from cyrene.workbench.runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [_workbench_new_plan_step("旧计划", "", 1, "task_1")]
    generated = [_workbench_new_plan_step("新计划", "", 1, "task_1")]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "重新规划，替换原计划", operation="replace"
    )

    assert [step["title"] for step in merged] == ["新计划"]


def test_workbench_agent_selected_replacement_does_not_append_old_plan():
    from cyrene.workbench.runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [_workbench_new_plan_step("旧计划", "", 1, "task_1")]
    generated = [_workbench_new_plan_step("完全不同的新计划", "", 1, "task_1")]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "换一种做法", operation="replace"
    )

    assert [step["title"] for step in merged] == ["完全不同的新计划"]


def test_workbench_revision_preserves_matching_step_identity_and_progress():
    from cyrene.workbench.runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [
        _workbench_new_plan_step("实现接口", "旧描述", 1, "task_1"),
        _workbench_new_plan_step("运行测试", "旧描述", 2, "task_1"),
    ]
    existing[0]["status"] = "completed"
    existing[0]["progressEvents"] = [{"body": "done"}]
    generated = [
        _workbench_new_plan_step("实现接口", "更新描述", 1, "task_1"),
        _workbench_new_plan_step("运行测试", "更新描述", 2, "task_1"),
        _workbench_new_plan_step("发布版本", "新增步骤", 3, "task_1"),
    ]
    generated[0]["sourceStepId"] = existing[0]["id"]
    generated[1]["sourceStepId"] = existing[1]["id"]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "增加发布步骤", operation="revise"
    )

    assert merged[0]["id"] == existing[0]["id"]
    assert merged[0]["status"] == "completed"
    assert merged[0]["progressEvents"] == [{"body": "done"}]
    assert merged[0]["description"] == "更新描述"
    assert merged[2]["title"] == "发布版本"


def test_workbench_repeated_partial_revisions_cannot_grow_plan_unbounded():
    from cyrene.workbench.runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [
        _workbench_new_plan_step(f"旧步骤 {index}", "", index, "task_1")
        for index in range(1, 13)
    ]
    generated = [_workbench_new_plan_step("额外步骤", "", 1, "task_1")]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "再补充一步", operation="revise"
    )

    assert len(merged) == 12


def test_workbench_acceptance_normalizes_agent_payload_and_resets_status():
    from cyrene.workbench.runtime import _workbench_coerce_acceptance_criteria

    criteria = _workbench_coerce_acceptance_criteria(
        {
            "acceptanceCriteria": [
                "登录接口返回有效会话",
                {"text": "登录失败时显示明确错误"},
                "",
            ]
        },
        [],
    )

    assert [item["text"] for item in criteria] == [
        "登录接口返回有效会话",
        "登录失败时显示明确错误",
    ]
    assert all(item["status"] == "pending" for item in criteria)


def test_workbench_json_parser_skips_stray_braces_before_valid_object():
    from cyrene.workbench.runtime import _workbench_parse_json_object

    parsed = _workbench_parse_json_object(
        '说明里有一个无效片段 {not json}，最终结果是 '
        '{"results": [{"id": "a1", "passed": true}], "reason": "ok"} 后续文字'
    )

    assert parsed["results"][0]["id"] == "a1"
    assert parsed["reason"] == "ok"


def test_workbench_json_parser_does_not_accept_nested_object_from_malformed_outer_json():
    from cyrene.workbench.runtime import _workbench_parse_json_object

    parsed = _workbench_parse_json_object(
        '{"results": [{"id": "a1", "passed": true}], "reason": "ok",}'
    )

    assert parsed is None


async def test_workbench_explore_agent_repairs_malformed_json_once(monkeypatch):
    from cyrene.workbench import runtime as routes

    responses = [
        {"content": '结果如下：{"ok": true,}', "tool_calls": []},
        {"content": '{"ok": true}', "tool_calls": []},
    ]

    async def fake_llm(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(routes, "_call_llm", fake_llm)

    result = await routes._workbench_run_explore_agent(None, "return json")

    assert result == {"ok": True}
    assert responses == []


async def test_workbench_empty_workspace_init_form_uses_project_description(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    captured = {}

    async def fake_llm(messages, tools=None, **kwargs):
        captured["prompt"] = messages[-1]["content"]
        captured["tools"] = tools
        captured["response_format"] = kwargs.get("response_format")
        return {
            "content": json.dumps({
                "greeting": "我们先把 AI 学习路线项目拆清楚。",
                "sections": [{
                    "id": "audience",
                    "title": "学习对象",
                    "questions": [{
                        "id": "student_level",
                        "type": "textarea",
                        "label": "高中生目前的编程和数学基础分别是什么？",
                        "placeholder": "例如：会 Python 基础，线性代数刚入门",
                    }],
                }],
            }, ensure_ascii=False),
            "tool_calls": [],
        }

    monkeypatch.setattr(routes, "_call_llm", fake_llm)

    result = await routes._workbench_generate_init_form(
        {
            "id": "project_1",
            "name": "AI 学习路线",
            "description": "给高中生设计一套 AI 学习路线，并输出阶段计划。",
            "template": "ai",
            "workspacePath": str(tmp_path),
        },
        lang="zh",
    )

    assert result is not None
    assert result["generated"] is True
    assert result["sections"][0]["questions"][0]["label"] == "高中生目前的编程和数学基础分别是什么？"
    assert captured["tools"] is None
    assert captured["response_format"] == routes._WORKBENCH_JSON_RESPONSE_FORMAT
    assert "项目描述：给高中生设计一套 AI 学习路线" in captured["prompt"]
    assert "不能探索文件" in captured["prompt"]
    assert "不要只套用通用模板" in captured["prompt"]


async def test_workbench_non_empty_workspace_init_form_prioritizes_description(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    (tmp_path / "README.md").write_text("# Existing App\n", encoding="utf-8")
    captured = {}

    async def fake_agent(workspace_root, prompt, **kwargs):
        captured["workspace_root"] = workspace_root
        captured["prompt"] = prompt
        return {
            "greeting": "我会结合描述和现有文件来初始化项目。",
            "sections": [{
                "id": "scope",
                "title": "范围澄清",
                "questions": [{
                    "id": "priority",
                    "type": "textarea",
                    "label": "健康数据仪表盘首版最需要服务哪类用户？",
                }],
            }],
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)

    result = await routes._workbench_generate_init_form(
        {
            "id": "project_1",
            "name": "健康数据仪表盘",
            "description": "面向医生的健康数据可视化仪表盘，重点展示慢病趋势。",
            "template": "product",
            "workspacePath": str(tmp_path),
        },
        lang="zh",
    )

    assert result is not None
    assert result["sections"][0]["questions"][0]["label"] == "健康数据仪表盘首版最需要服务哪类用户？"
    assert captured["workspace_root"] == tmp_path.resolve()
    assert "项目描述：面向医生的健康数据可视化仪表盘" in captured["prompt"]
    assert "最高优先级需求信号" in captured["prompt"]
    assert "不要只围绕代码结构提问" in captured["prompt"]


async def test_workbench_blank_project_existing_workspace_prompt_keeps_files_unconfirmed(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    (tmp_path / "script.txt").write_text("完整剧本\n", encoding="utf-8")
    captured = {}

    async def fake_agent(workspace_root, prompt, **kwargs):
        captured["workspace_root"] = workspace_root
        captured["prompt"] = prompt
        return {
            "greeting": "我看到工作区里有一些已有文件，我们先确认它们和新项目的关系。",
            "sections": [{
                "id": "workspace_relationship",
                "title": "已有文件关系",
                "questions": [{
                    "id": "file_relationship",
                    "type": "single",
                    "label": "这些已有文件和新项目是什么关系？",
                    "options": ["复用/导入", "仅作参考", "忽略", "另建空目录"],
                }],
            }],
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)

    result = await routes._workbench_generate_init_form(
        {
            "id": "project_1",
            "name": "新项目",
            "description": "",
            "template": "blank",
            "workspacePath": str(tmp_path),
            "workspacePathSource": "user",
        },
        lang="zh",
    )

    assert result is not None
    assert result["sections"][0]["id"] == "workspace_relationship"
    assert captured["workspace_root"] == tmp_path.resolve()
    assert "不等于用户确认这些文件就是本项目" in captured["prompt"]
    assert "空白项目" in captured["prompt"]
    assert "待确认线索" in captured["prompt"]
    assert "第一组问题必须先确认已有文件与新项目的关系" in captured["prompt"]
    assert "不能说“这是一个围绕某某的项目”" in captured["prompt"]


async def test_workbench_plan_revision_reuses_thread_without_tools(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    revision = routes._workbench_workspace_revision(tmp_path)
    previous_messages = [
        {"role": "system", "content": routes._WORKBENCH_PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": "initial task"},
        {
            "role": "assistant",
            "content": (
                '{"goal":"完成登录","revisionMode":"revise","steps":'
                '[{"sourceStepId":null,"title":"实现登录","description":"修改 app.py",'
                '"dependsOnStepIndexes":[]}],"acceptanceCriteria":["登录可用"]}'
            ),
        },
    ]
    session = {
        "id": "task_1",
        "title": "实现登录",
        "goal": "完成登录",
        "constraints": [],
        "plan": [{"id": "step_1", "title": "实现登录", "description": "修改 app.py", "status": "pending"}],
        "acceptanceCriteria": [],
        "planningThread": {
            "id": "planning_1",
            "contractVersion": routes._WORKBENCH_PLANNER_CONTRACT_VERSION,
            "messages": previous_messages,
            "observationCache": {},
            "inspectedResources": {},
            "metrics": [],
            "workspaceRevision": revision,
        },
    }
    captured = {}

    async def fake_llm(messages, tools=None, **kwargs):
        captured["messages"] = [dict(message) for message in messages]
        captured["tools"] = tools
        return {
            "content": (
                '{"goal":"完成登录","revisionMode":"revise","steps":'
                '[{"sourceStepId":"step_1","title":"实现登录","description":"补充错误处理",'
                '"dependsOnStepIndexes":[]}],"acceptanceCriteria":["登录可用"]}'
            ),
            "tool_calls": [],
            "usage": {"prompt_tokens": 100, "prompt_cache_hit_tokens": 80},
        }

    monkeypatch.setattr(routes, "_call_llm", fake_llm)

    _steps, _acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session,
        {"workspacePath": str(tmp_path)},
        feedback="把第一步描述详细一点",
        requested_operation="revise",
    )

    assert from_llm is True
    assert operation == "revise"
    assert captured["tools"] is None
    assert captured["messages"][:3] == previous_messages
    assert captured["messages"][-1]["role"] == "user"
    assert session["planningThread"]["lastToolBundleVersion"] == routes._WORKBENCH_PLANNER_NO_TOOLS_VERSION
    assert session["planningThread"]["metrics"][-1]["cachedTokens"] == 80


async def test_workbench_plan_revision_explores_after_workspace_change(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    target = tmp_path / "app.py"
    target.write_text("print('old')\n", encoding="utf-8")
    old_revision = routes._workbench_workspace_revision(tmp_path)
    target.write_text("print('new version')\n", encoding="utf-8")
    session = {
        "id": "task_1",
        "title": "实现登录",
        "goal": "完成登录",
        "constraints": [],
        "plan": [{"id": "step_1", "title": "实现登录", "status": "pending"}],
        "acceptanceCriteria": [],
        "planningThread": {
            "id": "planning_1",
            "contractVersion": routes._WORKBENCH_PLANNER_CONTRACT_VERSION,
            "messages": [
                {"role": "system", "content": routes._WORKBENCH_PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": "initial"},
                {"role": "assistant", "content": "{}"},
            ],
            "observationCache": {},
            "inspectedResources": {},
            "metrics": [],
            "workspaceRevision": old_revision,
        },
    }
    captured = {}

    async def fake_llm(messages, tools=None, **kwargs):
        captured["tools"] = tools
        return {
            "content": (
                '{"goal":"完成登录","revisionMode":"revise","steps":'
                '[{"sourceStepId":"step_1","title":"实现登录","description":"按新代码调整",'
                '"dependsOnStepIndexes":[]}],"acceptanceCriteria":["登录可用"]}'
            ),
            "tool_calls": [],
        }

    monkeypatch.setattr(routes, "_call_llm", fake_llm)

    await routes._workbench_generate_plan_steps(
        session,
        {"workspacePath": str(tmp_path)},
        feedback="调整计划",
        requested_operation="revise",
    )

    assert captured["tools"] == routes._WORKBENCH_EXPLORE_TOOLS


async def test_workbench_read_file_observation_cache_and_runtime_dedup(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    target = tmp_path / "notes.txt"
    target.write_text("0123456789", encoding="utf-8")
    tc = {
        "id": "call_1",
        "function": {
            "name": "read_file",
            "arguments": '{"path":"notes.txt","offset":2,"limit":4}',
        },
    }
    observation_cache = {}
    inspected = {}
    metrics = {}
    revision = routes._workbench_workspace_revision(tmp_path)

    first_runtime = {}
    first = await routes._workbench_exec_explore_tool(
        tc,
        tmp_path,
        observation_cache=observation_cache,
        runtime_cache=first_runtime,
        metrics=metrics,
        workspace_revision=revision,
        inspected_resources=inspected,
    )
    duplicate = await routes._workbench_exec_explore_tool(
        tc,
        tmp_path,
        observation_cache=observation_cache,
        runtime_cache=first_runtime,
        metrics=metrics,
        workspace_revision=revision,
        inspected_resources=inspected,
    )

    assert first == "2345\n\n...(truncated; next offset=6)"
    assert duplicate == first
    assert metrics["duplicateCallsBlocked"] == 1

    def fail_read_text(*args, **kwargs):
        raise AssertionError("cached file range must not be read again")

    monkeypatch.setattr(Path, "read_text", fail_read_text)
    second = await routes._workbench_exec_explore_tool(
        tc,
        tmp_path,
        observation_cache=observation_cache,
        runtime_cache={},
        metrics=metrics,
        workspace_revision=revision,
        inspected_resources=inspected,
    )

    assert second == first
    assert metrics["workspaceCacheHits"] == 1


async def test_workbench_plan_agent_returns_fresh_acceptance_criteria(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    async def fake_agent(*args, **kwargs):
        return {
            "steps": [
                {"title": "实现登录接口", "description": "修改认证模块"},
                {"title": "验证登录流程", "description": "运行认证测试"},
            ],
            "acceptanceCriteria": [
                "有效账号可以登录并获得会话",
                "认证测试全部通过",
            ],
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "实现登录",
        "goal": "完成账号登录功能",
        "constraints": [],
        "plan": [],
        "acceptanceCriteria": [{"id": "old", "text": "旧标准", "status": "passed"}],
    }
    project = {"workspacePath": str(tmp_path)}

    steps, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(session, project)

    assert from_llm is True
    assert operation == "create"
    assert [step["title"] for step in steps] == ["实现登录接口", "验证登录流程"]
    assert [item["text"] for item in acceptance] == [
        "有效账号可以登录并获得会话",
        "认证测试全部通过",
    ]
    assert all(item["status"] == "pending" for item in acceptance)


async def test_workbench_acceptance_agent_uses_current_plan(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    captured = {}

    async def fake_agent(workspace_root, prompt, **kwargs):
        captured["prompt"] = prompt
        return {"acceptanceCriteria": ["认证模块测试通过"]}

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "实现登录",
        "goal": "完成账号登录功能",
        "constraints": ["仅支持邮箱登录"],
        "plan": [{"title": "实现认证模块", "description": "修改 auth.py"}],
    }
    project = {"workspacePath": str(tmp_path)}

    acceptance, from_llm = await routes._workbench_generate_acceptance_criteria(session, project)

    assert from_llm is True
    assert acceptance[0]["text"] == "认证模块测试通过"
    assert "完成账号登录功能" in captured["prompt"]
    assert "实现认证模块" in captured["prompt"]
    assert "仅支持邮箱登录" in captured["prompt"]


async def test_workbench_failed_plan_revision_preserves_existing_acceptance(monkeypatch):
    from cyrene.workbench import runtime as routes

    async def failed_agent(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", failed_agent)
    old_criteria = [{
        "id": "accept_existing",
        "text": "人工确认的验收标准",
        "status": "passed",
        "evidence": "tests passed",
    }]
    session = {
        "id": "task_1",
        "title": "实现登录",
        "goal": "完成账号登录功能",
        "constraints": [],
        "plan": [{"id": "step_1", "title": "实现登录", "status": "pending"}],
        "acceptanceCriteria": old_criteria,
    }

    steps, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session, {"workspacePath": ""}, feedback="补充登录测试"
    )

    assert from_llm is False
    assert operation == "revise"
    assert steps == session["plan"]
    assert acceptance == old_criteria


async def test_workbench_reconciled_plan_does_not_launch_second_acceptance_agent(monkeypatch):
    from cyrene.workbench import runtime as routes

    prompts = []

    async def fake_agent(workspace_root, prompt, **kwargs):
        prompts.append(prompt)
        return {
            "revisionMode": "revise",
            "steps": [{"title": "新增测试", "description": "补充测试"}],
            "acceptanceCriteria": ["新增测试通过"],
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "实现功能",
        "goal": "完成核心功能",
        "constraints": [],
        "plan": [
            {"id": "step_1", "title": "实现功能", "description": "完成实现", "status": "pending"},
            {"id": "step_2", "title": "构建产物", "description": "生成包", "status": "pending"},
        ],
        "acceptanceCriteria": [],
    }

    steps, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session, {"workspacePath": ""}, feedback="补充测试"
    )

    assert from_llm is True
    assert operation == "revise"
    assert [step["title"] for step in steps] == ["实现功能", "构建产物", "新增测试"]
    assert [item["text"] for item in acceptance] == ["新增测试通过"]
    assert len(prompts) == 1


async def test_workbench_plan_revision_updates_goal_used_by_goal_loop(monkeypatch):
    from cyrene.workbench import runtime as routes

    async def fake_agent(*args, **kwargs):
        return {
            "goal": "完成短信登录功能，并覆盖短信验证码测试",
            "revisionMode": "replace",
            "steps": [
                {"title": "实现短信登录", "description": "接入短信验证码"},
                {"title": "验证短信登录", "description": "运行短信登录测试"},
            ],
            "acceptanceCriteria": ["短信登录测试通过"],
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "实现登录",
        "goal": "完成账号密码登录功能",
        "constraints": [],
        "plan": [{"id": "step_1", "title": "实现密码登录", "status": "pending"}],
        "acceptanceCriteria": [],
    }

    steps, _acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session,
        {"workspacePath": ""},
        feedback="改为短信验证码登录，并补充相关测试",
    )

    assert from_llm is True
    assert operation == "replace"
    assert [step["title"] for step in steps] == ["实现短信登录", "验证短信登录"]
    assert session["goal"] == "完成短信登录功能，并覆盖短信验证码测试"


async def test_workbench_plan_agent_can_choose_full_replacement(monkeypatch):
    from cyrene.workbench import runtime as routes

    async def fake_agent(*args, **kwargs):
        return {
            "revisionMode": "replace",
            "steps": [
                {"sourceStepId": None, "title": "设计全新方案", "description": "重新设计"},
                {"sourceStepId": None, "title": "实现全新方案", "description": "重新实现"},
            ],
            "acceptanceCriteria": ["全新方案可运行"],
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "旧任务",
        "goal": "完成任务",
        "constraints": [],
        "plan": [
            {"id": "step_old", "title": "旧步骤", "description": "", "status": "completed"},
        ],
        "acceptanceCriteria": [],
    }

    steps, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session,
        {"workspacePath": ""},
        feedback="请生成完全不同的计划",
    )

    assert from_llm is True
    assert operation == "replace"
    assert [step["title"] for step in steps] == ["设计全新方案", "实现全新方案"]
    assert all(step["id"] != "step_old" for step in steps)
    assert [item["text"] for item in acceptance] == ["全新方案可运行"]


async def test_workbench_explicit_regeneration_hides_old_plan(monkeypatch):
    from cyrene.workbench import runtime as routes

    prompts = []
    async def fake_agent(_workspace_root, prompt, **_kwargs):
        prompts.append(prompt)
        return {
            "revisionMode": "replace",
            "steps": [
                {"title": "建立对照实验", "description": "先验证关键假设"},
                {"title": "实现替代架构", "description": "采用不同技术路径"},
            ],
            "acceptanceCriteria": ["替代方案通过验证"],
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "优化模型",
        "goal": "提高模型准确率",
        "constraints": [],
        "plan": [
            {"id": "step_1", "title": "读取日志", "description": "分析结果", "status": "pending"},
            {"id": "step_2", "title": "修改模型", "description": "调整架构", "status": "pending"},
        ],
        "acceptanceCriteria": [],
    }

    steps, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session,
        {"workspacePath": ""},
        feedback="请基于当前任务目标生成一份全新的执行计划，不保留原计划步骤。",
        requested_operation="replace",
    )

    assert from_llm is True
    assert operation == "replace"
    assert [step["title"] for step in steps] == ["建立对照实验", "实现替代架构"]
    assert [item["text"] for item in acceptance] == ["替代方案通过验证"]
    assert len(prompts) == 1
    assert "当前已有执行计划" not in prompts[0]
    # Regeneration still re-decomposes from the goal; the exploration policy and
    # JSON schema now live in the planner system prompt, so the per-call message
    # only carries the regeneration directive plus the bundle-aware tool directive
    # (empty workspace here -> no-tools bundle).
    assert "重新生成" in prompts[0]
    assert "至少一半步骤" in prompts[0]
    assert "本次不提供工作区探索工具" in prompts[0]


async def test_workbench_plan_prompt_leaves_workspace_exploration_to_agent(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    # Non-empty workspace so the no-tools decision comes from the task being
    # explicitly project-independent, not merely from an empty workspace.
    (tmp_path / "app.py").write_text("print('x')\n", encoding="utf-8")
    captured = {}

    async def fake_agent(workspace_root, prompt, **kwargs):
        captured["workspace_root"] = workspace_root
        captured["prompt"] = prompt
        captured["tool_bundle_version"] = kwargs.get("tool_bundle_version")
        return {
            "steps": [
                {"title": "明确目标", "description": "梳理计划目标"},
                {"title": "安排阶段", "description": "制定阶段性行动"},
                {"title": "复盘调整", "description": "根据结果调整计划"},
            ],
            "acceptanceCriteria": ["计划覆盖目标、行动和复盘"],
        }

    async def fake_routing(*_args, **_kwargs):
        return {
            "workspaceRelationship": "independent",
            "needsWorkspaceRefresh": False,
            "revisionMode": "revise",
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    monkeypatch.setattr(routes, "_workbench_classify_plan_routing", fake_routing)
    session = {
        "id": "task_non_file_plan",
        "title": "制定学习计划",
        "goal": "制定一个与当前本地项目无关的三个月学习计划",
        "constraints": [],
        "plan": [],
        "acceptanceCriteria": [],
    }

    await routes._workbench_generate_plan_steps(
        session,
        {"workspacePath": str(tmp_path)},
    )

    # An explicitly project-independent task gets the no-tools bundle, and the
    # per-call prompt must not advertise exploration tools it cannot use (M1).
    assert captured["tool_bundle_version"] == routes._WORKBENCH_PLANNER_NO_TOOLS_VERSION
    prompt = captured["prompt"]
    assert "本次不提供工作区探索工具" in prompt
    assert "你可以使用" not in prompt
    assert "list_directory" not in prompt


async def test_workbench_auto_start_acceptance_uses_derived_goal(monkeypatch):
    from cyrene.workbench import runtime as routes

    calls = 0

    async def fake_agent(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "goal": "修复认证模块的登录回归",
                "title": "修复登录回归",
                "steps": [{"title": "修复认证逻辑", "description": "修改认证模块"}],
            }
        return None

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "新任务",
        "goal": "",
        "constraints": [],
        "plan": [],
        "acceptanceCriteria": [],
    }

    _, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session, {"workspacePath": ""}, auto_start=True
    )

    assert from_llm is True
    assert operation == "create"
    assert session["goal"] == "修复认证模块的登录回归"
    assert any("修复认证模块的登录回归" in item["text"] for item in acceptance)


async def test_workbench_verifier_uses_clean_agent_context(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    captured = {}

    async def fake_agent(workspace_root, prompt, **kwargs):
        captured["workspace_root"] = workspace_root
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "results": [{"id": "accept_1", "passed": True, "evidence": "测试通过"}],
            "recommend_reflection": False,
            "reason": "全部通过",
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_session_1",
        "title": "实现登录",
        "goal": "完成账号登录",
        "acceptanceCriteria": [
            {"id": "accept_1", "text": "登录测试通过", "status": "pending"}
        ],
    }

    verdict = await routes._workbench_verify_acceptance(
        session, {"workspacePath": str(tmp_path)}
    )

    assert verdict["results"][0]["passed"] is True
    assert captured["kwargs"]["session_id"] == "task_session_1"
    assert captured["kwargs"]["clean_context"] is True
    assert captured["kwargs"]["raise_on_failure"] is True
    assert "上下文完全独立" in captured["prompt"]
    assert "不得依赖任务执行 Agent 的对话" in captured["prompt"]


async def test_workbench_verifier_rejects_missing_criteria_results(monkeypatch, tmp_path):
    import pytest
    from cyrene.workbench import runtime as routes

    calls = {"n": 0}

    async def fake_agent(*args, **kwargs):
        calls["n"] += 1
        return {
            "results": [{"id": "accept_1", "passed": True, "evidence": "ok"}],
            "recommend_reflection": False,
            "reason": "partial",
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    # Keep the retry backoff instant so the test does not actually sleep.
    monkeypatch.setattr(routes, "_WORKBENCH_VERIFY_RETRY_BASE_DELAY", 0.0)
    session = {
        "id": "task_session_1",
        "title": "实现登录",
        "acceptanceCriteria": [
            {"id": "accept_1", "text": "接口完成", "status": "pending"},
            {"id": "accept_2", "text": "测试通过", "status": "pending"},
        ],
    }

    # A schema-shaped failure (missing criterion) is retryable, so it exhausts
    # the attempt budget before finally surfacing the error.
    with pytest.raises(routes._WorkbenchGenerationError, match="遗漏了 1 条"):
        await routes._workbench_verify_acceptance(
            session, {"workspacePath": str(tmp_path)}
        )
    assert calls["n"] == routes._WORKBENCH_VERIFY_MAX_ATTEMPTS


async def test_workbench_verifier_retries_transient_then_succeeds(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    calls = {"n": 0}

    async def flaky_agent(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # First reply comes back empty — a transient glitch the loop retries.
            raise routes._WorkbenchGenerationError("empty_response", "模型返回了空响应。")
        return {
            "results": [{"id": "accept_1", "passed": True, "evidence": "测试通过"}],
            "recommend_reflection": False,
            "reason": "全部通过",
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", flaky_agent)
    monkeypatch.setattr(routes, "_WORKBENCH_VERIFY_RETRY_BASE_DELAY", 0.0)
    session = {
        "id": "task_session_1",
        "title": "实现登录",
        "acceptanceCriteria": [
            {"id": "accept_1", "text": "登录测试通过", "status": "pending"}
        ],
    }

    verdict = await routes._workbench_verify_acceptance(
        session, {"workspacePath": str(tmp_path)}
    )

    assert calls["n"] == 2
    assert verdict["results"][0]["passed"] is True


async def test_workbench_verifier_does_not_retry_auth_failure(monkeypatch, tmp_path):
    import pytest
    from cyrene.workbench import runtime as routes

    calls = {"n": 0}

    async def auth_failing_agent(*args, **kwargs):
        calls["n"] += 1
        raise routes._WorkbenchGenerationError("authentication", "模型服务鉴权失败（HTTP 401）。")

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", auth_failing_agent)
    monkeypatch.setattr(routes, "_WORKBENCH_VERIFY_RETRY_BASE_DELAY", 0.0)
    session = {
        "id": "task_session_1",
        "title": "实现登录",
        "acceptanceCriteria": [
            {"id": "accept_1", "text": "登录测试通过", "status": "pending"}
        ],
    }

    # Auth/config errors won't fix themselves on retry, so they bail immediately.
    with pytest.raises(routes._WorkbenchGenerationError, match="鉴权失败"):
        await routes._workbench_verify_acceptance(
            session, {"workspacePath": str(tmp_path)}
        )
    assert calls["n"] == 1


async def test_workbench_clean_explore_agent_clears_inherited_session(monkeypatch):
    from cyrene.agent.state import _current_session_id
    from cyrene.workbench import runtime as routes

    seen_session_ids = []

    async def fake_llm(*args, **kwargs):
        seen_session_ids.append(_current_session_id.get())
        return {"content": '{"ok": true}', "tool_calls": []}

    monkeypatch.setattr(routes, "_call_llm", fake_llm)
    outer_token = _current_session_id.set("dirty_execution_session")
    try:
        result = await routes._workbench_run_explore_agent(
            None,
            "return json",
            session_id="task_session_1",
            clean_context=True,
        )
        assert result == {"ok": True}
        assert seen_session_ids == [""]
        assert _current_session_id.get() == "dirty_execution_session"
    finally:
        _current_session_id.reset(outer_token)


def test_workbench_file_changes_from_write_and_edit_events(tmp_path):
    from cyrene.workbench.runtime import _workbench_file_changes_from_tool_event

    write_changes = _workbench_file_changes_from_tool_event(
        {"tool": "Write", "args": {"path": str(tmp_path / "notes.md")}, "result": ""},
        tmp_path,
    )
    edit_changes = _workbench_file_changes_from_tool_event(
        {"tool": "Edit", "args": {"path": str(tmp_path / "src/app.py")}, "result": ""},
        tmp_path,
    )

    assert write_changes[0]["path"] == "notes.md"
    assert write_changes[0]["status"] == "created/updated"
    assert edit_changes[0]["path"] == "src/app.py"
    assert edit_changes[0]["status"] == "modified"


def test_workbench_file_changes_parse_tool_result_fallback(tmp_path):
    from cyrene.workbench.runtime import _workbench_file_changes_from_tool_event

    changes = _workbench_file_changes_from_tool_event(
        {"tool": "custom_write", "args": {}, "result": f"Wrote {tmp_path / 'out.txt'}"},
        tmp_path,
    )

    assert changes[0]["path"] == "out.txt"
    assert changes[0]["status"] == "created/updated"


def test_workbench_file_changes_reject_paths_outside_workspace(tmp_path):
    from cyrene.workbench.runtime import _workbench_file_changes_from_tool_event

    outside = tmp_path.parent / "outside.md"
    absolute = _workbench_file_changes_from_tool_event(
        {"tool": "Write", "args": {"path": str(outside)}, "result": ""},
        tmp_path,
    )
    traversal = _workbench_file_changes_from_tool_event(
        {"tool": "Write", "args": {"path": "../outside.md"}, "result": ""},
        tmp_path,
    )

    assert absolute == []
    assert traversal == []


def test_workbench_file_changes_reject_cyrene_managed_run_state(tmp_path):
    from cyrene.workbench.runtime import (
        _workbench_file_changes_from_tool_event,
        _workbench_git_status_delta,
        _workbench_workspace_file_snapshot,
        _workbench_workspace_text_snapshot,
    )

    cyrene_root = tmp_path / ".cyrene"
    (cyrene_root / "conversations").mkdir(parents=True)
    (cyrene_root / "conversations" / "wbchat_1.md").write_text("chat\n", encoding="utf-8")
    (cyrene_root / "plan").mkdir()
    (cyrene_root / "plan" / "plan_1.md").write_text("plan\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    assert _workbench_file_changes_from_tool_event(
        {"tool": "Write", "args": {"path": ".cyrene/conversations/wbchat_1.md"}, "result": ""},
        tmp_path,
    ) == []
    assert _workbench_git_status_delta(
        {},
        {".cyrene/conversations/wbchat_1.md": "??", ".cyrene/plan/plan_1.md": "??", "app.py": "??"},
        tmp_path,
    )[0]["path"] == "app.py"
    # The snapshot walks skip dot directories, so .cyrene is invisible.
    assert set(_workbench_workspace_file_snapshot(tmp_path)) == {"app.py"}
    assert set(_workbench_workspace_text_snapshot(tmp_path)) == {"app.py"}


def test_workbench_git_status_snapshot_is_scoped_to_nested_workspace(tmp_path):
    from cyrene.workbench.runtime import _workbench_git_status_snapshot

    repo = tmp_path / "repo"
    workspace = repo / "workspace"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "outside.txt").write_text("before\n", encoding="utf-8")
    (workspace / "inside.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "outside.txt", "workspace/inside.txt"], cwd=repo, check=True, capture_output=True)
    (repo / "outside.txt").write_text("after\n", encoding="utf-8")
    (workspace / "inside.txt").write_text("after\n", encoding="utf-8")

    assert _workbench_git_status_snapshot(workspace) == {"inside.txt": "AM"}


def test_workbench_git_status_delta_and_step_related_files():
    from cyrene.workbench.runtime import _workbench_apply_step_file_changes, _workbench_git_status_delta

    changes = _workbench_git_status_delta({"old.py": " M"}, {"old.py": " M", "new.py": "??", "app.py": " M"})
    assert [(item["path"], item["status"]) for item in changes] == [("new.py", "created"), ("app.py", "modified")]

    session = {"plan": [{"id": "s1", "relatedFiles": [{"path": "old.py", "status": "modified"}]}]}
    _workbench_apply_step_file_changes(session, "s1", changes)
    assert [item["path"] for item in session["plan"][0]["relatedFiles"]] == ["old.py", "new.py", "app.py"]


def test_workbench_workspace_snapshot_detects_named_shell_output(tmp_path):
    from cyrene.workbench.runtime import (
        _workbench_workspace_file_snapshot,
        _workbench_workspace_snapshot_delta,
        _workbench_workspace_text_snapshot,
    )

    before = _workbench_workspace_file_snapshot(tmp_path)
    before_text = _workbench_workspace_text_snapshot(tmp_path)
    output = tmp_path / "exports" / "report.pdf"
    output.parent.mkdir()
    output.write_bytes(b"%PDF-1.7\n")
    scratch = tmp_path / "scratch.tmp"
    scratch.write_text("temporary", encoding="utf-8")
    after = _workbench_workspace_file_snapshot(tmp_path)
    after_text = _workbench_workspace_text_snapshot(tmp_path)

    changes = _workbench_workspace_snapshot_delta(
        before,
        after,
        "已生成 exports/report.pdf，可直接交付。",
        before_text=before_text,
        after_text=after_text,
    )
    by_path = {item["path"]: item for item in changes}
    assert by_path["exports/report.pdf"]["status"] == "produced"
    assert by_path["exports/report.pdf"]["source"] == "workspace_output"
    assert by_path["scratch.tmp"]["status"] == "created"
    assert by_path["scratch.tmp"]["source"] == "workspace"
    assert "+temporary" in by_path["scratch.tmp"]["diff"]


def test_workbench_workspace_snapshot_delta_records_text_diffs_without_git(tmp_path):
    from cyrene.workbench.runtime import (
        _workbench_merge_file_changes,
        _workbench_recorded_diff_for_path,
        _workbench_workspace_file_snapshot,
        _workbench_workspace_snapshot_delta,
        _workbench_workspace_text_snapshot,
    )

    existing = tmp_path / "notes.md"
    existing.write_text("old line\nkeep\n", encoding="utf-8")
    removed = tmp_path / "old.txt"
    removed.write_text("remove me\n", encoding="utf-8")
    before = _workbench_workspace_file_snapshot(tmp_path)
    before_text = _workbench_workspace_text_snapshot(tmp_path)

    existing.write_text("new line\nkeep\n", encoding="utf-8")
    created = tmp_path / "created.md"
    created.write_text("# Created\n", encoding="utf-8")
    removed.unlink()
    after = _workbench_workspace_file_snapshot(tmp_path)
    after_text = _workbench_workspace_text_snapshot(tmp_path)

    changes = _workbench_workspace_snapshot_delta(
        before,
        after,
        "",
        before_text=before_text,
        after_text=after_text,
    )
    by_path = {item["path"]: item for item in changes}
    assert by_path["notes.md"]["status"] == "modified"
    assert "-old line" in by_path["notes.md"]["diff"]
    assert "+new line" in by_path["notes.md"]["diff"]
    assert by_path["created.md"]["status"] == "created"
    assert "--- /dev/null" in by_path["created.md"]["diff"]
    assert "+# Created" in by_path["created.md"]["diff"]
    assert by_path["old.txt"]["status"] == "deleted"
    assert "-remove me" in by_path["old.txt"]["diff"]
    assert "+++ /dev/null" in by_path["old.txt"]["diff"]

    merged = _workbench_merge_file_changes([
        {"path": "notes.md", "status": "modified", "source": "Edit"},
        by_path["notes.md"],
    ])
    assert merged[0]["source"] == "Edit"
    assert "+new line" in merged[0]["diff"]

    recorded = _workbench_recorded_diff_for_path({"runs": [{"fileChanges": merged}]}, "notes.md", tmp_path)
    assert recorded is not None
    assert recorded["source"] == "workspace_snapshot"
    assert "+new line" in recorded["diff"]


def test_workbench_recorded_diff_blocks_misleading_current_snapshot_fallback(tmp_path):
    from cyrene.workbench.runtime import (
        _workbench_git_diff_for_path,
        _workbench_recorded_diff_for_path,
        _workbench_workspace_file_snapshot,
        _workbench_workspace_snapshot_delta,
        _workbench_workspace_text_snapshot,
    )

    target = tmp_path / "same.md"
    target.write_text("same content\n", encoding="utf-8")
    before = _workbench_workspace_file_snapshot(tmp_path)
    before_text = _workbench_workspace_text_snapshot(tmp_path)
    stat = target.stat()
    os.utime(target, (stat.st_atime + 10, stat.st_mtime + 10))
    after = _workbench_workspace_file_snapshot(tmp_path)
    after_text = _workbench_workspace_text_snapshot(tmp_path)

    changes = _workbench_workspace_snapshot_delta(
        before,
        after,
        "",
        before_text=before_text,
        after_text=after_text,
    )
    assert changes[0]["path"] == "same.md"
    assert changes[0]["diffUnavailableReason"] == "no_text_difference"
    assert "diff" not in changes[0]

    recorded = _workbench_recorded_diff_for_path({"runs": [{"fileChanges": changes}]}, "same.md", tmp_path)
    assert recorded == {
        "path": "same.md",
        "diff": "",
        "has_changes": False,
        "source": "no_text_difference",
        "reason": "no_text_difference",
    }

    fallback = asyncio.run(_workbench_git_diff_for_path(tmp_path, "same.md"))
    assert fallback["source"] == "snapshot"
    assert "+same content" in fallback["diff"]


import pytest


@pytest.mark.asyncio
async def test_workbench_git_diff_for_tracked_and_untracked_files(tmp_path):
    from cyrene.workbench.runtime import _workbench_git_diff_for_path

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    tracked = tmp_path / "app.py"
    tracked.write_text("print('old')\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True)
    tracked.write_text("print('new')\n", encoding="utf-8")

    tracked_diff = await _workbench_git_diff_for_path(tmp_path, "app.py")
    assert tracked_diff["path"] == "app.py"
    assert "-print('old')" in tracked_diff["diff"]
    assert "+print('new')" in tracked_diff["diff"]

    untracked = tmp_path / "notes.md"
    untracked.write_text("# Notes\n", encoding="utf-8")
    untracked_diff = await _workbench_git_diff_for_path(tmp_path, "notes.md")
    assert untracked_diff["path"] == "notes.md"
    assert "--- /dev/null" in untracked_diff["diff"]
    assert "+++ b/notes.md" in untracked_diff["diff"]
    assert "+# Notes" in untracked_diff["diff"]

    staged = tmp_path / "staged.md"
    staged.write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "staged.md"], cwd=tmp_path, check=True, capture_output=True)
    staged_diff = await _workbench_git_diff_for_path(tmp_path, "staged.md")
    assert staged_diff["source"] == "git"
    assert "+++ b/staged.md" in staged_diff["diff"]
    assert "+staged" in staged_diff["diff"]


@pytest.mark.asyncio
async def test_workbench_file_diff_falls_back_to_current_text_snapshot(tmp_path):
    from cyrene.workbench.runtime import _workbench_git_diff_for_path

    report = tmp_path / "report.tex"
    report.write_text("\\section{Result}\n", encoding="utf-8")

    no_git_diff = await _workbench_git_diff_for_path(tmp_path, "report.tex")
    assert no_git_diff["source"] == "snapshot"
    assert no_git_diff["has_changes"] is True
    assert "--- /dev/null" in no_git_diff["diff"]
    assert "+++ b/report.tex" in no_git_diff["diff"]
    assert "+\\section{Result}" in no_git_diff["diff"]

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "report.tex"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    clean_diff = await _workbench_git_diff_for_path(tmp_path, "report.tex")
    assert clean_diff["source"] == "snapshot"
    assert "+\\section{Result}" in clean_diff["diff"]


@pytest.mark.asyncio
async def test_workbench_git_diff_rejects_paths_outside_workspace(tmp_path):
    from cyrene.workbench.runtime import _workbench_git_diff_for_path

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError):
        await _workbench_git_diff_for_path(tmp_path, outside)


@pytest.mark.asyncio
async def test_workbench_init_task_plan_reports_llm_success(monkeypatch):
    from cyrene.workbench import runtime as R

    async def fake_call_llm(messages, tools=None, max_tokens=None, secondary=False, thinking="auto"):
        return {"content": '{"tasks": [{"title": "拆解需求", "goal": "明确范围", "priority": "high"}]}'}

    monkeypatch.setattr(R, "_call_llm", fake_call_llm)
    plan, from_llm, error = await R._workbench_generate_init_task_plan(
        {"id": "p1", "name": "Demo", "template": "blank"}, {"answers": {}},
    )
    assert from_llm is True
    assert error is None
    assert plan[0]["title"] == "拆解需求"


@pytest.mark.asyncio
async def test_workbench_init_task_plan_retries_five_times_without_fallback(monkeypatch):
    from cyrene.workbench import runtime as R

    calls = 0

    async def failing_call_llm(messages, tools=None, max_tokens=None, secondary=False, thinking="auto"):
        nonlocal calls
        calls += 1
        raise RuntimeError("model down")

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(R, "_call_llm", failing_call_llm)
    monkeypatch.setattr(R.asyncio, "sleep", no_wait)
    plan, from_llm, error = await R._workbench_generate_init_task_plan(
        {"id": "p1", "name": "Demo", "template": "blank"},
        {"answers": {"goal": "做一个 CLI 工具"}},
    )
    assert calls == 5
    assert from_llm is False
    assert plan is None
    assert error["code"] == "init_plan_generation_failed"
    assert error["attemptCount"] == 5
    assert len(error["attempts"]) == 5
    assert all("model down" in attempt["message"] for attempt in error["attempts"])


@pytest.mark.asyncio
async def test_workbench_init_task_plan_can_recover_on_fifth_attempt(monkeypatch):
    from cyrene.workbench import runtime as R

    calls = 0

    async def flaky_call_llm(messages, tools=None, max_tokens=None, secondary=False, thinking="auto"):
        nonlocal calls
        calls += 1
        if calls < 5:
            raise RuntimeError(f"temporary failure {calls}")
        return {"content": '{"tasks": [{"title": "最终成功", "goal": "完成规划"}]}'}

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(R, "_call_llm", flaky_call_llm)
    monkeypatch.setattr(R.asyncio, "sleep", no_wait)
    plan, from_llm, error = await R._workbench_generate_init_task_plan(
        {"id": "p1", "name": "Demo", "template": "blank"},
        {"answers": {"goal": "做一个 CLI 工具"}},
    )

    assert calls == 5
    assert from_llm is True
    assert error is None
    assert plan[0]["title"] == "最终成功"


def test_workbench_generation_error_redacts_credentials():
    from cyrene.workbench.runtime import _workbench_generation_error

    error = _workbench_generation_error(
        RuntimeError("Bearer secret-token sk-abcdefghijkl api_key=private-value")
    )

    assert "secret-token" not in error.message
    assert "abcdefghijkl" not in error.message
    assert "private-value" not in error.message
    assert "<redacted>" in error.message


def test_workbench_promote_file_artifacts_promotes_and_dedups():
    from cyrene.workbench.runtime import _workbench_promote_file_artifacts

    session = {"artifacts": [
        {"id": "a1", "type": "task_brief", "name": "task-brief.md", "status": "draft"},
        {"id": "a2", "type": "knowledge_document", "name": "archived.md", "status": "indexed"},
    ]}
    changes = [
        {"path": "report.md", "status": "produced", "source": "workspace_output"},
        {"path": "existing.py", "status": "modified", "source": "Edit"},
        {"path": "inferred.txt", "status": "created", "source": "git"},
        {"path": "export.pdf", "status": "produced", "source": "send_file"},
        {"path": "shell.pdf", "status": "produced", "source": "workspace_output"},
        {"path": "report.md", "status": "created/updated", "source": "Write"},
    ]
    added = _workbench_promote_file_artifacts(session, changes, "2026-06-14T00:00:00Z")

    assert added == 3
    file_arts = [a for a in session["artifacts"] if a["type"] == "file_change"]
    by_name = {a["name"]: a for a in file_arts}
    assert set(by_name) == {"report.md", "export.pdf", "shell.pdf"}
    assert by_name["report.md"]["status"] == "ready"
    assert by_name["export.pdf"]["status"] == "ready"
    assert by_name["shell.pdf"]["status"] == "ready"
    assert by_name["report.md"]["source"] == "workspace_output"
    assert all(a["type"] == "file_change" for a in session["artifacts"])

    # idempotent: re-running adds nothing
    assert _workbench_promote_file_artifacts(session, changes, "2026-06-14T01:00:00Z") == 0


def test_workbench_promote_file_artifacts_pins_attachment_copy(tmp_path):
    from cyrene.workbench.runtime import _workbench_promote_file_artifacts

    (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
    session = {"artifacts": []}
    changes = [{
        "path": "report.md",
        "status": "produced",
        "source": "workspace_output",
        "diff": "--- /dev/null\n+++ b/report.md\n@@ -0,0 +1 @@\n+# Report\n",
        "diffSource": "workspace_snapshot",
        # send_file's tool event pins the durable webui_exports copy. The
        # public attachment payload carries an id (the exported filename) but
        # no path — mirror the real build_public_attachment_payload shape.
        "attachment": {
            "id": "report_f1a2b3c4d5.md",
            "name": "report.md",
            "content_type": "text/markdown",
            "size": 11,
            "kind": "file",
            "url": "/api/chat/export/report_f1a2b3c4d5.md",
        },
    }]

    assert _workbench_promote_file_artifacts(session, changes, "2026-06-14T00:00:00Z") == 1
    artifact = session["artifacts"][0]
    # No deliverables/ dir anymore: the artifact keeps the Agent-verified
    # relative path and pins the durable webui_exports copy for download.
    assert artifact["path"] == "report.md"
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# Report\n"
    assert artifact["attachment"]["id"] == "report_f1a2b3c4d5.md"
    assert artifact["attachment"].get("path") is None
    # Diff headers keep the source path since no copy is made.
    assert "+++ b/report.md" in artifact["diff"]
    assert "deliverables" not in artifact["diff"]


def test_workbench_promote_file_artifacts_attachment_flows_from_tool_event():
    """send_file's real tool-event result pins the attachment on the change."""
    from cyrene.workbench.runtime import _workbench_file_changes_from_tool_event

    changes = _workbench_file_changes_from_tool_event(
        {
            "tool": "send_file",
            "args": {"path": "report.md"},
            "result": '{"status": "sent", "attachment": {"id": "report_abc123.md", "name": "report.md", "url": "/api/chat/export/report_abc123.md"}}',
        },
        None,
    )

    assert len(changes) == 1
    assert changes[0]["path"] == "report.md"
    assert changes[0]["status"] == "produced"
    assert changes[0]["attachment"]["id"] == "report_abc123.md"


def test_workbench_promote_file_artifacts_no_attachment_keeps_workspace_path(tmp_path):
    from cyrene.workbench.runtime import _workbench_promote_file_artifacts

    (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
    session = {"artifacts": []}
    changes = [{
        "path": "report.md",
        "status": "produced",
        "source": "workspace_output",
        "diff": "--- /dev/null\n+++ b/report.md\n@@ -0,0 +1 @@\n+# Report\n",
        "diffSource": "workspace_snapshot",
    }]

    assert _workbench_promote_file_artifacts(session, changes, "2026-06-14T00:00:00Z") == 1
    artifact = session["artifacts"][0]
    assert artifact["path"] == "report.md"
    assert "attachment" not in artifact


def test_workbench_final_artifact_file_changes_use_declared_artifacts_only():
    from cyrene.workbench.runtime import _workbench_final_artifact_file_changes

    session = {
        "runs": [{"fileChanges": [
            {"path": "analysis.py", "status": "created", "source": "Write"},
            {"path": "report.tex", "status": "created", "source": "Write"},
        ]}],
        "artifacts": [{
            "id": "artifact_pdf",
            "type": "file_change",
            "name": "report.pdf",
            "path": "deliverables/report.pdf",
            "status": "ready",
            "source": "send_file",
        }],
    }

    changes = _workbench_final_artifact_file_changes(session)

    assert changes == [{
        "path": "deliverables/report.pdf",
        "status": "produced",
        "source": "send_file",
    }]


def test_workbench_backfill_file_artifacts_from_runs_and_steps():
    from cyrene.workbench.runtime import _workbench_backfill_file_artifacts

    session = {
        "artifacts": [{"id": "a1", "type": "task_brief", "name": "task-brief.md", "status": "draft"}],
        "runs": [{"fileChanges": [
            {"path": "report.md", "status": "created/updated", "source": "Write"},
            {"path": "inferred.txt", "status": "created", "source": "git"},
        ]}],
        "plan": [{"relatedFiles": [
            {"path": "existing.py", "status": "modified", "source": "Edit"},
            {"path": "report.md", "status": "modified", "source": "git"},
        ]}],
    }
    added = _workbench_backfill_file_artifacts(session, "2026-06-14T00:00:00Z")

    assert added == 0
    names = {a["name"] for a in session["artifacts"] if a["type"] == "file_change"}
    assert names == set()
    # idempotent
    assert _workbench_backfill_file_artifacts(session, "2026-06-14T00:00:00Z") == 0


def test_workbench_backfill_does_not_restore_missing_produced_artifact(tmp_path):
    from cyrene.workbench.runtime import _workbench_backfill_file_artifacts

    session = {
        "runs": [{
            "fileChanges": [{
                "path": "missing.txt",
                "status": "produced",
                "source": "send_file",
            }],
        }],
        "plan": [],
        "artifacts": [],
    }

    assert _workbench_backfill_file_artifacts(
        session,
        "2026-06-14T00:00:00Z",
        tmp_path,
    ) == 0
    assert session["artifacts"] == []


def test_workbench_prunes_non_file_and_duplicate_artifacts():
    from cyrene.workbench.runtime import _workbench_prune_non_file_artifacts

    session = {"artifacts": [
        {"id": "brief", "type": "task_brief", "name": "task-brief.md"},
        {"id": "file-1", "type": "file_change", "name": "report.md", "path": "out/report.md"},
        {"id": "file-2", "type": "file_change", "name": "report.md", "path": "out/report.md"},
        {"id": "test", "type": "file_change", "name": "test_render.md", "path": "test_render.md"},
        {"id": "knowledge", "type": "knowledge_document", "name": "archived.md"},
    ]}

    assert _workbench_prune_non_file_artifacts(session) is True
    assert session["artifacts"] == [
        {"id": "file-1", "type": "file_change", "name": "report.md", "path": "out/report.md"},
        {"id": "test", "type": "file_change", "name": "test_render.md", "path": "test_render.md"},
    ]


@pytest.mark.asyncio
async def test_workbench_backfills_reported_historical_output(monkeypatch, tmp_path):
    from cyrene.workbench.runtime import _workbench_backfill_referenced_file_artifacts
    from cyrene.workbench import runtime

    output = tmp_path / "exports" / "final.pdf"
    output.parent.mkdir()
    output.write_bytes(b"%PDF-1.7\n")
    source = tmp_path / "source.md"
    source.write_text("# source", encoding="utf-8")
    session = {
        "artifacts": [],
        "runs": [{
            "agentResponse": (
                "PDF 已成功生成，可直接交付。"
                f"文件路径：`{output}`。输入源文件为 `{source}`。"
            ),
        }],
    }

    async def fake_call_llm(messages, **_kwargs):
        assert "不要根据‘生成’‘保存’等单个词机械判断" in messages[-1]["content"]
        return {"content": '{"deliverablePaths":["exports/final.pdf"]}'}

    monkeypatch.setattr(runtime, "_call_llm", fake_call_llm)

    added = await _workbench_backfill_referenced_file_artifacts(
        {"workspacePath": str(tmp_path)},
        session,
        "2026-06-21T00:00:00Z",
    )

    assert added == 1
    # No deliverables/ copy: the artifact keeps the source-relative path.
    assert session["artifacts"][0]["path"] == "exports/final.pdf"
    assert session["legacyArtifactModelMigrationVersion"] == 1
    assert (tmp_path / "exports" / "final.pdf").read_bytes() == b"%PDF-1.7\n"


def test_workbench_prunes_parent_repo_git_files_and_artifacts(tmp_path):
    from cyrene.workbench.runtime import _workbench_prune_invalid_file_records

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.md").write_text("ok", encoding="utf-8")
    bad = {"path": "src/webui/index.html", "status": "modified", "source": "git"}
    good = {"path": "report.md", "status": "created/updated", "source": "Write"}
    session = {
        "plan": [{"relatedFiles": [good, bad]}],
        "runs": [{"fileChanges": [good, bad], "events": [{"fileChanges": [bad]}]}],
        "events": [{"fileChanges": [bad]}],
        "artifacts": [
            {"type": "file_change", "name": "report.md", "path": "report.md"},
            {"type": "file_change", "name": "index.html", "path": "src/webui/index.html"},
        ],
    }

    changed = _workbench_prune_invalid_file_records(
        {"workspacePath": str(workspace)},
        session,
    )

    assert changed is True
    assert [item["path"] for item in session["plan"][0]["relatedFiles"]] == ["report.md"]
    assert [item["path"] for item in session["runs"][0]["fileChanges"]] == ["report.md"]
    assert session["runs"][0]["events"][0]["fileChanges"] == []
    assert session["events"][0]["fileChanges"] == []
    assert session["artifacts"] == []


def test_workbench_task_plan_tool_helper_scopes_mutation_to_current_session(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    store_path.write_text(json.dumps({
        "projects": [{
            "id": "project_1",
            "name": "Cyrene",
            "sessions": [
                {
                    "id": "session_a",
                    "projectId": "project_1",
                    "kind": "task",
                    "title": "任务 A",
                    "goal": "只更新 A",
                    "status": "running",
                    "planRevision": 1,
                    "planDefinitionRevision": 1,
                    "approvedPlanDefinitionRevision": 1,
                    "plan": [
                        {"id": "step_a1", "title": "A1", "status": "pending", "order": 1, "dependsOn": []},
                    ],
                    "events": [],
                    "runs": [],
                    "artifacts": [],
                    "acceptanceCriteria": [],
                    "createdAt": "2026-06-19T00:00:00+00:00",
                    "updatedAt": "2026-06-19T00:00:00+00:00",
                },
                {
                    "id": "session_b",
                    "projectId": "project_1",
                    "kind": "task",
                    "title": "任务 B",
                    "goal": "不应被更新",
                    "status": "planning",
                    "planRevision": 7,
                    "planDefinitionRevision": 7,
                    "approvedPlanDefinitionRevision": 7,
                    "plan": [
                        {"id": "step_b1", "title": "B1", "status": "pending", "order": 1, "dependsOn": []},
                    ],
                    "events": [],
                    "runs": [],
                    "artifacts": [],
                    "acceptanceCriteria": [],
                    "createdAt": "2026-06-19T00:00:00+00:00",
                    "updatedAt": "2026-06-19T00:00:00+00:00",
                },
            ],
            "createdAt": "2026-06-19T00:00:00+00:00",
            "updatedAt": "2026-06-19T00:00:00+00:00",
        }],
        "activeProjectId": "project_1",
        "activeSessionId": "session_b",
    }), encoding="utf-8")

    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "_db_path", "")
    monkeypatch.setattr(routes, "_CONFIGURED_WORKBENCH_STORE", None)

    result = routes.update_task_plan_for_session(
        "session_a",
        "add",
        step={"title": "A2", "description": "追加到 A 的计划"},
        reason="用户补充了 A 的范围",
    )

    assert result["ok"] is True
    stored = routes._read_workbench_store()
    project = stored["projects"][0]
    session_a = next(session for session in project["sessions"] if session["id"] == "session_a")
    session_b = next(session for session in project["sessions"] if session["id"] == "session_b")
    assert [step["title"] for step in session_a["plan"]] == ["A1", "A2"]
    assert session_a["planDefinitionRevision"] == 2
    assert [step["title"] for step in session_b["plan"]] == ["B1"]
    assert session_b["planDefinitionRevision"] == 7
    assert stored["activeSessionId"] == "session_a"

    added_step_id = session_a["plan"][1]["id"]
    result = routes.update_task_plan_for_session(
        "session_a",
        "delete",
        step_id=added_step_id,
        reason="用户取消了追加步骤",
    )

    assert result["ok"] is True
    stored = routes._read_workbench_store()
    project = stored["projects"][0]
    session_a = next(session for session in project["sessions"] if session["id"] == "session_a")
    session_b = next(session for session in project["sessions"] if session["id"] == "session_b")
    assert [step["title"] for step in session_a["plan"]] == ["A1"]
    assert session_a["planDefinitionRevision"] == 3
    assert [step["title"] for step in session_b["plan"]] == ["B1"]
    assert session_b["planDefinitionRevision"] == 7


def test_update_task_plan_is_registered_main_only():
    from cyrene.tooling import catalog as registry_tools

    registry_tools._initialize_registry()

    assert "update_task_plan" in registry_tools.get_tool_names()
    assert registry_tools.is_tool_allowed_for_actor("update_task_plan", "main") is True
    assert registry_tools.is_tool_allowed_for_actor("update_task_plan", "subagent") is False
