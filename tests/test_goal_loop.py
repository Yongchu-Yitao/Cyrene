import asyncio
import json
import sqlite3

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from route.registry import register_routes


def _store(tmp_path, *, status="planning", revision=3):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    store_path = data_dir / "workbench_projects.json"
    store_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "project_1",
                        "name": "Cyrene",
                        "workspacePath": str(tmp_path),
                        "sessions": [
                            {
                                "id": "session_1",
                                "projectId": "project_1",
                                "kind": "task",
                                "title": "实现登录",
                                "goal": "完成账号登录功能",
                                "status": status,
                                "priority": "medium",
                                "constraints": [],
                                "planRevision": 4,
                                "planDefinitionRevision": revision,
                                "approvedPlanDefinitionRevision": None,
                                "plan": [
                                    {
                                        "id": "step_1",
                                        "title": "实现登录接口",
                                        "description": "修改认证模块",
                                        "status": "pending",
                                        "order": 1,
                                        "dependsOn": [],
                                    }
                                ],
                                "events": [],
                                "runs": [],
                                "artifacts": [],
                                "acceptanceCriteria": [
                                    {"id": "accept_1", "text": "认证测试通过", "status": "pending"}
                                ],
                                "createdAt": "2026-06-19T00:00:00+00:00",
                                "updatedAt": "2026-06-19T00:00:00+00:00",
                            }
                        ],
                        "createdAt": "2026-06-19T00:00:00+00:00",
                        "updatedAt": "2026-06-19T00:00:00+00:00",
                    }
                ],
                "activeProjectId": "project_1",
                "activeSessionId": "session_1",
            }
        ),
        encoding="utf-8",
    )
    return data_dir, store_path


def _app(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import goal_loop as goal_loop

    data_dir, store_path = _store(tmp_path)
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "append_notification", lambda **_kwargs: {})
    monkeypatch.setattr(goal_loop, "append_notification", lambda **_kwargs: {})
    monkeypatch.setattr(goal_loop.GoalLoopManager, "wake", lambda self, run_id: None)
    app = FastAPI()
    register_routes(app, bot=None, db_path=db_path)
    return app, db_path, store_path


def test_goal_loop_preview_and_start_without_changing_goal(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    async def unexpected_generation(*_args, **_kwargs):
        raise AssertionError("existing user acceptance criteria must be preserved")

    monkeypatch.setattr(routes, "_workbench_generate_acceptance_criteria", unexpected_generation)
    app, db_path, store_path = _app(monkeypatch, tmp_path)
    client = TestClient(app)

    preview = client.post(
        "/api/task-sessions/session_1/goal-loop/preview",
        json={
            "goal": "完成账号登录功能",
            "maxRuntimeHours": 2,
            "maxRepairRounds": 3,
            "permissionMode": "auto",
            "reflectionMode": "proactive",
            "basePlanDefinitionRevision": 3,
        },
    )
    assert preview.status_code == 200
    draft = preview.json()
    assert draft["goalChanged"] is False
    assert draft["plan"][0]["id"] == "step_1"
    assert draft["acceptanceCriteria"][0]["id"] == "accept_1"
    assert draft["acceptanceCriteria"][0]["text"] == "认证测试通过"
    assert draft["planSource"] == "fallback"

    started = client.post(
        "/api/task-sessions/session_1/goal-loop/start",
        json={"draftId": draft["draftId"]},
    )
    assert started.status_code == 200
    session = started.json()["session"]
    assert session["status"] == "running"
    assert session["goalLoop"]["status"] == "running"
    assert session["goalLoop"]["reflectionMode"] == "proactive"
    assert session["acceptanceCriteria"][0]["status"] == "pending"

    stored = json.loads(store_path.read_text(encoding="utf-8"))
    stored_session = stored["projects"][0]["sessions"][0]
    assert stored_session["approvedPlanDefinitionRevision"] == 3

    async def read_run():
        from cyrene.workbench.goal_loop import _get_run_by_session
        return await _get_run_by_session(db_path, "session_1")

    run = asyncio.run(read_run())
    assert run["max_active_seconds"] == 7200
    assert run["max_repair_rounds"] == 3


async def test_goal_loop_concurrent_start_returns_conflict_not_server_error(monkeypatch, tmp_path):
    """A double click or retried HTTP request must create exactly one run."""
    app, db_path, _store_path = _app(monkeypatch, tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preview = await client.post(
            "/api/task-sessions/session_1/goal-loop/preview",
            json={
                "goal": "完成账号登录功能",
                "maxRuntimeHours": 2,
                "maxRepairRounds": 3,
                "permissionMode": "auto",
                "reflectionMode": "proactive",
                "basePlanDefinitionRevision": 3,
            },
        )
        assert preview.status_code == 200
        draft_id = preview.json()["draftId"]
        first, second = await asyncio.gather(
            client.post(
                "/api/task-sessions/session_1/goal-loop/start",
                json={"draftId": draft_id},
            ),
            client.post(
                "/api/task-sessions/session_1/goal-loop/start",
                json={"draftId": draft_id},
            ),
        )

    assert sorted((first.status_code, second.status_code)) == [200, 409]
    conflict = first if first.status_code == 409 else second
    assert conflict.json()["code"] == "goal_loop_exists"

    from cyrene.workbench import goal_loop as goal_loop

    rows = await goal_loop._fetch_all(db_path, "SELECT * FROM goal_runs WHERE session_id = ?", ("session_1",))
    assert len(rows) == 1


async def test_goal_loop_start_rolls_back_run_when_projection_write_fails(monkeypatch, tmp_path):
    from cyrene.workbench import goal_loop as goal_loop

    app, db_path, _store_path = _app(monkeypatch, tmp_path)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preview = await client.post(
            "/api/task-sessions/session_1/goal-loop/preview",
            json={
                "goal": "完成账号登录功能",
                "maxRuntimeHours": 2,
                "maxRepairRounds": 3,
                "permissionMode": "auto",
                "reflectionMode": "proactive",
                "basePlanDefinitionRevision": 3,
            },
        )
        draft_id = preview.json()["draftId"]
        monkeypatch.setattr(
            goal_loop.WorkbenchGoalLoopTransaction,
            "write_session",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(OSError, match="disk full"):
            await client.post(
                "/api/task-sessions/session_1/goal-loop/start",
                json={"draftId": draft_id},
            )

    assert await goal_loop._get_run_by_session(db_path, "session_1") is None


async def test_goal_loop_startup_recovers_hard_crash_state_and_stale_lease(monkeypatch, tmp_path):
    """Persisted running state is the exact state left by SIGKILL/power loss."""
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import goal_loop as goal_loop

    data_dir, store_path = _store(tmp_path, status="running")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    await goal_loop._ensure_schema(db_path)
    payload = routes._read_workbench_store()
    payload["projects"][0]["sessions"][0]["plan"][0].update(
        status="running",
        startedAt="2026-07-11T00:00:00+00:00",
        currentAction="旧进程正在执行",
    )
    routes._write_workbench_store(payload)
    now = goal_loop._utc_iso()
    await goal_loop._execute(
        db_path,
        """
        INSERT INTO goal_runs
        (id, session_id, project_id, objective, status, phase,
         plan_definition_revision, permission_mode, reflection_mode,
         max_active_seconds, max_repair_rounds, active_seconds,
         active_started_at, repair_round, lease_owner, lease_until,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, 'running', 'executing', ?, 'auto', 'standard',
                3600, 2, 12, ?, 0, 'dead-process', ?, ?, ?)
        """,
        ("run_crashed", "session_1", "project_1", "完成账号登录功能", 3, now, now, now, now),
    )

    manager = goal_loop.GoalLoopManager(db_path)
    awakened = []
    monkeypatch.setattr(manager, "wake", awakened.append)
    await manager.startup()

    recovered = await goal_loop._get_run_by_id(db_path, "run_crashed")
    assert recovered["status"] == "running"
    assert recovered["phase"] == "recovering"
    assert recovered["lease_owner"] is None
    assert recovered["lease_until"] is None
    assert recovered["active_started_at"]
    assert awakened == ["run_crashed"]
    stored = routes._read_workbench_store()
    session = stored["projects"][0]["sessions"][0]
    step = session["plan"][0]
    assert session["status"] == "running"
    assert session["goalLoop"]["phase"] == "recovering"
    assert step["status"] == "pending"
    assert step["startedAt"] is None
    assert "currentAction" not in step


async def test_goal_loop_graceful_restart_pauses_then_recovers(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import goal_loop as goal_loop

    data_dir, store_path = _store(tmp_path, status="running")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    await goal_loop._ensure_schema(db_path)
    now = goal_loop._utc_iso()
    await goal_loop._execute(
        db_path,
        """
        INSERT INTO goal_runs
        (id, session_id, project_id, objective, status, phase,
         plan_definition_revision, permission_mode, reflection_mode,
         max_active_seconds, max_repair_rounds, active_seconds,
         active_started_at, repair_round, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'running', 'executing', ?, 'auto', 'standard',
                3600, 2, 0, ?, 0, ?, ?)
        """,
        ("run_restart", "session_1", "project_1", "完成账号登录功能", 3, now, now, now),
    )

    old_manager = goal_loop.GoalLoopManager(db_path)
    await old_manager.shutdown()
    paused = await goal_loop._get_run_by_id(db_path, "run_restart")
    assert paused["status"] == "paused"
    assert paused["stop_reason"] == "server_shutdown"

    new_manager = goal_loop.GoalLoopManager(db_path)
    awakened = []
    monkeypatch.setattr(new_manager, "wake", awakened.append)
    await new_manager.startup()
    resumed = await goal_loop._get_run_by_id(db_path, "run_restart")
    assert resumed["status"] == "running"
    assert resumed["phase"] == "recovering"
    assert resumed["stop_reason"] is None
    assert awakened == ["run_restart"]


def test_goal_loop_changed_goal_regenerates_plan_and_requires_full_access_confirmation(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    async def fake_plan(session, project, feedback="", requested_operation="auto", **_kwargs):
        assert session["goal"] == "改为实现短信登录"
        assert requested_operation == "replace"
        return (
            [{"id": "new_step", "title": "实现短信登录", "status": "pending", "order": 1, "dependsOn": []}],
            [{"id": "new_accept", "text": "短信登录测试通过", "status": "pending"}],
            True,
            "replace",
        )

    monkeypatch.setattr(routes, "_workbench_generate_plan_steps", fake_plan)
    app, _db_path, _store_path = _app(monkeypatch, tmp_path)
    client = TestClient(app)

    denied = client.post(
        "/api/task-sessions/session_1/goal-loop/preview",
        json={
            "goal": "改为实现短信登录",
            "maxRuntimeHours": 1,
            "maxRepairRounds": 2,
            "permissionMode": "full_access",
            "reflectionMode": "frequent",
            "fullAccessConfirmed": False,
            "basePlanDefinitionRevision": 3,
        },
    )
    assert denied.status_code == 400

    preview = client.post(
        "/api/task-sessions/session_1/goal-loop/preview",
        json={
            "goal": "改为实现短信登录",
            "maxRuntimeHours": 1,
            "maxRepairRounds": 2,
            "permissionMode": "full_access",
            "reflectionMode": "frequent",
            "fullAccessConfirmed": True,
            "basePlanDefinitionRevision": 3,
        },
    )
    assert preview.status_code == 200
    assert preview.json()["goalChanged"] is True
    assert preview.json()["plan"][0]["id"] == "new_step"


def test_acceptance_patch_auto_completes_task_when_all_criteria_pass(monkeypatch, tmp_path):
    app, _db_path, store_path = _app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.patch(
        "/api/task-sessions/session_1",
        json={
            "acceptanceCriteria": [
                {"id": "accept_1", "text": "认证测试通过", "status": "passed"},
                {"id": "accept_2", "text": "回归测试通过", "status": "passed"},
            ]
        },
    )

    assert response.status_code == 200
    session = response.json()["session"]
    assert session["status"] == "completed"
    assert [event["type"] for event in session["events"]] == ["TaskCompleted"]
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert stored["projects"][0]["sessions"][0]["status"] == "completed"

    # Replaying the same update is idempotent and does not duplicate the event.
    replay = client.patch(
        "/api/task-sessions/session_1",
        json={
            "acceptanceCriteria": [
                {"id": "accept_1", "text": "认证测试通过", "status": "passed"},
                {"id": "accept_2", "text": "回归测试通过", "status": "passed"},
            ]
        },
    )
    assert replay.status_code == 200
    assert len(replay.json()["session"]["events"]) == 1


def test_independent_verification_auto_completes_task(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    async def fake_verify(_session, _project):
        return {
            "results": [{"id": "accept_1", "passed": True, "evidence": "测试通过"}],
            "reason": "全部通过",
        }

    monkeypatch.setattr(routes, "_workbench_verify_acceptance", fake_verify)
    app, _db_path, _store_path = _app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post("/api/task-sessions/session_1/verify", json={})

    assert response.status_code == 200
    session = response.json()["session"]
    assert session["status"] == "completed"
    assert session["acceptanceCriteria"][0]["status"] == "passed"
    assert any(event["type"] == "TaskCompleted" for event in session["events"])


def test_goal_loop_preview_returns_service_unavailable_before_generation_when_storage_is_busy(
    monkeypatch,
    tmp_path,
):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench.goal_loop_repository import SqliteGoalLoopRepository

    generation_called = False

    async def fake_plan(*_args, **_kwargs):
        nonlocal generation_called
        generation_called = True
        raise AssertionError("planning must not run while draft storage is busy")

    async def locked_delete(_repository, _expires_before):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(routes, "_workbench_generate_plan_steps", fake_plan)
    monkeypatch.setattr(SqliteGoalLoopRepository, "delete_expired_drafts", locked_delete)
    app, _db_path, _store_path = _app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/task-sessions/session_1/goal-loop/preview",
        json={
            "goal": "改为实现短信登录",
            "maxRuntimeHours": 1,
            "maxRepairRounds": 2,
            "permissionMode": "auto",
            "reflectionMode": "proactive",
            "basePlanDefinitionRevision": 3,
        },
    )

    assert response.status_code == 503
    assert response.json()["code"] == "goal_loop_storage_busy"
    assert generation_called is False


async def test_goal_loop_runner_completes_after_independent_verification(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import goal_loop as goal_loop

    data_dir, store_path = _store(tmp_path)
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(goal_loop, "append_notification", lambda **_kwargs: {})
    monkeypatch.setattr(routes, "_workbench_git_status_snapshot", lambda _root: {})
    monkeypatch.setattr(routes, "_workbench_git_status_delta", lambda *_args: [])
    monkeypatch.setattr(routes, "_collect_run_activity_events", lambda *_args: [])

    async def fake_agent(*_args, **_kwargs):
        return "已完成登录接口"

    async def fake_step_verify(*_args, **_kwargs):
        return {"passed": True, "evidence": "认证模块已更新", "retry_guidance": ""}

    async def fake_goal_verify(session, project):
        return {
            "results": [{"id": "accept_1", "passed": True, "evidence": "认证测试通过"}],
            "recommend_reflection": False,
            "reason": "全部通过",
        }

    monkeypatch.setattr(routes, "_workbench_agent_reply", fake_agent)
    monkeypatch.setattr(goal_loop, "_verify_step", fake_step_verify)
    monkeypatch.setattr(routes, "_workbench_verify_acceptance", fake_goal_verify)

    await goal_loop._ensure_schema(db_path)
    now = goal_loop._utc_iso()
    await goal_loop._execute(
        db_path,
        """
        INSERT INTO goal_runs
        (id, session_id, project_id, objective, status, phase,
         plan_definition_revision, permission_mode, reflection_mode,
         max_active_seconds, max_repair_rounds, active_seconds,
         active_started_at, repair_round, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'running', 'executing', ?, 'auto', 'standard', 3600, 2, 0, ?, 0, ?, ?)
        """,
        ("run_1", "session_1", "project_1", "完成账号登录功能", 3, now, now, now),
    )
    manager = goal_loop.GoalLoopManager(db_path)
    await manager._run("run_1")

    run = await goal_loop._get_run_by_id(db_path, "run_1")
    assert run["status"] == "completed"
    assert run["stop_reason"] == "acceptance_passed"
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    session = stored["projects"][0]["sessions"][0]
    assert session["status"] == "completed"
    assert session["plan"][0]["status"] == "completed"
    assert session["acceptanceCriteria"][0]["status"] == "passed"


@pytest.mark.parametrize(
    ("failure_kind", "expected_reason"),
    [
        ("budget", "budget_monthly_exhausted"),
        ("verification", "step_verification_unavailable"),
    ],
)
async def test_goal_loop_pauses_when_execution_infrastructure_is_unavailable(
    monkeypatch, tmp_path, failure_kind, expected_reason
):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import goal_loop as goal_loop

    data_dir, store_path = _store(tmp_path)
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(goal_loop, "append_notification", lambda **_kwargs: {})
    monkeypatch.setattr(routes, "_workbench_git_status_snapshot", lambda _root: {})
    monkeypatch.setattr(routes, "_workbench_git_status_delta", lambda *_args: [])
    monkeypatch.setattr(routes, "_collect_run_activity_events", lambda *_args: [])

    async def fake_agent(*_args, **_kwargs):
        if failure_kind == "budget":
            raise routes._WorkbenchAgentRunError(
                "budget_monthly_exhausted",
                "Monthly budget exhausted",
                status_code=403,
            )
        return "已完成登录接口"

    async def fake_step_verify(*_args, **_kwargs):
        if failure_kind == "verification":
            raise RuntimeError("步骤独立验收暂时不可用")
        raise AssertionError("budget block must stop before verification")

    monkeypatch.setattr(routes, "_workbench_agent_reply", fake_agent)
    monkeypatch.setattr(goal_loop, "_verify_step", fake_step_verify)

    await goal_loop._ensure_schema(db_path)
    now = goal_loop._utc_iso()
    await goal_loop._execute(
        db_path,
        """
        INSERT INTO goal_runs
        (id, session_id, project_id, objective, status, phase,
         plan_definition_revision, permission_mode, reflection_mode,
         max_active_seconds, max_repair_rounds, active_seconds,
         active_started_at, repair_round, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'running', 'executing', ?, 'auto', 'standard', 3600, 2, 0, ?, 0, ?, ?)
        """,
        ("run_1", "session_1", "project_1", "完成账号登录功能", 3, now, now, now),
    )

    manager = goal_loop.GoalLoopManager(db_path)
    await manager._run("run_1")

    run = await goal_loop._get_run_by_id(db_path, "run_1")
    assert run["status"] == "paused"
    assert run["stop_reason"] == expected_reason
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    session = stored["projects"][0]["sessions"][0]
    assert session["status"] == "paused"
    assert session["goalLoop"]["status"] == "paused"


def test_goal_loop_skipped_prerequisite_blocks_dependent_step():
    from cyrene.workbench import goal_loop

    plan = [
        {"id": "step_a", "status": "skipped", "dependsOn": []},
        {"id": "step_b", "status": "pending", "dependsOn": ["step_a"]},
    ]

    assert goal_loop._dependencies_satisfied(plan, plan[1]) is False
    assert goal_loop._next_step(plan) is None


async def test_goal_loop_runner_blocks_after_repeated_step_verification_failure(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import goal_loop as goal_loop

    data_dir, store_path = _store(tmp_path)
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(goal_loop, "append_notification", lambda **_kwargs: {})
    monkeypatch.setattr(routes, "_workbench_git_status_snapshot", lambda _root: {})
    monkeypatch.setattr(routes, "_workbench_git_status_delta", lambda *_args: [])
    monkeypatch.setattr(routes, "_collect_run_activity_events", lambda *_args: [])

    verify_calls = {"n": 0}

    async def fake_agent(*_args, **_kwargs):
        return "尝试修改认证模块"

    async def always_fail_step(*_args, **_kwargs):
        verify_calls["n"] += 1
        return {"passed": False, "evidence": "认证接口仍然缺失", "retry_guidance": "需要真正实现接口"}

    async def fake_reflect(*_args, **_kwargs):
        return {"summary": "stuck"}

    # The whole-goal verifier must never decide completion when steps never pass.
    async def fail_goal_verify(session, project):
        raise AssertionError("goal verification should not run while a step is stuck")

    monkeypatch.setattr(routes, "_workbench_agent_reply", fake_agent)
    monkeypatch.setattr(goal_loop, "_verify_step", always_fail_step)
    monkeypatch.setattr(goal_loop, "_reflect", fake_reflect)
    monkeypatch.setattr(routes, "_workbench_verify_acceptance", fail_goal_verify)

    await goal_loop._ensure_schema(db_path)
    now = goal_loop._utc_iso()
    await goal_loop._execute(
        db_path,
        """
        INSERT INTO goal_runs
        (id, session_id, project_id, objective, status, phase,
         plan_definition_revision, permission_mode, reflection_mode,
         max_active_seconds, max_repair_rounds, active_seconds,
         active_started_at, repair_round, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'running', 'executing', ?, 'auto', 'standard', 3600, 2, 0, ?, 0, ?, ?)
        """,
        ("run_1", "session_1", "project_1", "完成账号登录功能", 3, now, now, now),
    )
    manager = goal_loop.GoalLoopManager(db_path)
    await manager._run("run_1")

    run = await goal_loop._get_run_by_id(db_path, "run_1")
    assert run["status"] == "blocked"
    assert run["stop_reason"] == "step_stuck"
    # The step retries up to the cap, then blocks — it must not retry forever.
    assert verify_calls["n"] == goal_loop._STEP_FAILURE_CAP
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    session = stored["projects"][0]["sessions"][0]
    assert session["status"] == "blocked"
    assert session["plan"][0]["status"] == "pending"
    assert session["plan"][0]["goalLoopAttempts"] == goal_loop._STEP_FAILURE_CAP


async def test_resume_after_answer_does_not_re_execute_the_answered_step(monkeypatch, tmp_path):
    """Answering a goal-loop clarification must resume the loop WITHOUT resetting
    the answered step back to pending — otherwise the runner re-executes the same
    step and the agent re-asks the same question."""
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import goal_loop as goal_loop

    data_dir, store_path = _store(tmp_path, status="running")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)

    # The answer endpoint has already marked the step complete and cleared
    # pendingPlanStep before resume_after_answer runs.
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    session = payload["projects"][0]["sessions"][0]
    session["plan"][0]["status"] = "completed"
    session.pop("pendingPlanStep", None)
    store_path.write_text(json.dumps(payload), encoding="utf-8")

    await goal_loop._ensure_schema(db_path)
    now = goal_loop._utc_iso()
    await goal_loop._execute(
        db_path,
        """
        INSERT INTO goal_runs
        (id, session_id, project_id, objective, status, phase,
         plan_definition_revision, permission_mode, reflection_mode,
         max_active_seconds, max_repair_rounds, active_seconds,
         repair_round, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'waiting_for_user', 'waiting_for_user', ?, 'auto', 'standard', 3600, 2, 0, 0, ?, ?)
        """,
        ("run_1", "session_1", "project_1", "完成账号登录功能", 3, now, now),
    )

    # No manager registered → wake() is skipped, so the worker never runs here.
    await goal_loop.resume_after_answer(db_path, "session_1")

    run = await goal_loop._get_run_by_id(db_path, "run_1")
    assert run["status"] == "running"
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    step = stored["projects"][0]["sessions"][0]["plan"][0]
    assert step["status"] == "completed"  # NOT reset to pending
    assert stored["projects"][0]["sessions"][0]["status"] == "running"


async def test_resume_after_answer_pauses_on_permission_denied(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import goal_loop as goal_loop

    data_dir, store_path = _store(tmp_path, status="running")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)

    await goal_loop._ensure_schema(db_path)
    now = goal_loop._utc_iso()
    await goal_loop._execute(
        db_path,
        """
        INSERT INTO goal_runs
        (id, session_id, project_id, objective, status, phase,
         plan_definition_revision, permission_mode, reflection_mode,
         max_active_seconds, max_repair_rounds, active_seconds,
         repair_round, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'waiting_for_user', 'waiting_for_user', ?, 'auto', 'standard', 3600, 2, 0, 0, ?, ?)
        """,
        ("run_1", "session_1", "project_1", "完成账号登录功能", 3, now, now),
    )

    await goal_loop.resume_after_answer(db_path, "session_1", permission_denied=True)

    run = await goal_loop._get_run_by_id(db_path, "run_1")
    assert run["status"] == "paused"
    assert run["stop_reason"] == "permission_denied"
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    session = stored["projects"][0]["sessions"][0]
    assert session["status"] == "paused"
    assert session["goalLoop"]["status"] == "paused"


async def test_begin_async_answer_tags_step_and_resumes_run(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import goal_loop as goal_loop

    data_dir, store_path = _store(tmp_path, status="waiting_for_user")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)

    # A goal-loop step is waiting on a clarification question.
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    session = payload["projects"][0]["sessions"][0]
    session["plan"][0]["status"] = "running"
    session["pendingQuestion"] = {"id": "q1", "text": "用纯色方案吗？", "options": ["同意", "再想想"]}
    session["pendingPlanStep"] = {"stepId": "step_1", "goalLoop": True}
    store_path.write_text(json.dumps(payload), encoding="utf-8")

    await goal_loop._ensure_schema(db_path)
    now = goal_loop._utc_iso()
    await goal_loop._execute(
        db_path,
        """
        INSERT INTO goal_runs
        (id, session_id, project_id, objective, status, phase,
         plan_definition_revision, permission_mode, reflection_mode,
         max_active_seconds, max_repair_rounds, active_seconds,
         repair_round, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'waiting_for_user', 'waiting_for_user', ?, 'auto', 'standard', 3600, 2, 0, 0, ?, ?)
        """,
        ("run_1", "session_1", "project_1", "完成账号登录功能", 3, now, now),
    )

    # No manager registered → wake() is skipped, so the worker never runs here.
    took = await goal_loop.begin_async_answer(db_path, "session_1", "q1", "同意")
    assert took is True

    run = await goal_loop._get_run_by_id(db_path, "run_1")
    assert run["status"] == "running"
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    session = stored["projects"][0]["sessions"][0]
    assert session.get("pendingQuestion") in (None, {})  # UI card cleared
    assert "pendingPlanStep" not in session or not session["pendingPlanStep"]
    marker = session["plan"][0].get("goalLoopResumeAnswer")
    assert marker == {"questionId": "q1", "answer": "同意"}

    # A run that is NOT waiting is declined (caller falls back to sync path).
    await goal_loop._update_run(db_path, "run_1", status="running")
    assert await goal_loop.begin_async_answer(db_path, "session_1", "q1", "同意") is False


async def test_goal_loop_worker_resumes_via_answer_pending_and_completes(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import goal_loop as goal_loop

    data_dir, store_path = _store(tmp_path, status="running")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(goal_loop, "append_notification", lambda **_kwargs: {})
    monkeypatch.setattr(routes, "_workbench_git_status_snapshot", lambda _root: {})
    monkeypatch.setattr(routes, "_workbench_git_status_delta", lambda *_args: [])
    monkeypatch.setattr(routes, "_collect_run_activity_events", lambda *_args: [])

    # The step carries an async-answer tag → must resume via answer_pending.
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    payload["projects"][0]["sessions"][0]["plan"][0]["goalLoopResumeAnswer"] = {
        "questionId": "q1", "answer": "用纯色方案",
    }
    payload["projects"][0]["sessions"][0]["plan"][0]["status"] = "running"
    store_path.write_text(json.dumps(payload), encoding="utf-8")

    calls = {"answer": 0, "fresh": 0}

    async def fake_answer_pending(session_id, question_id, answer_text, workspace_dir, permission_mode="default"):
        calls["answer"] += 1
        assert question_id == "q1"
        assert answer_text == "用纯色方案"
        # The run's permission mode must carry into the resumed slice, not revert.
        assert permission_mode == "auto"
        return "已根据你的回复用纯色方案完成此步"

    async def fake_fresh(*_args, **_kwargs):
        calls["fresh"] += 1
        return "fresh execution (should not happen)"

    async def fake_step_verify(*_args, **_kwargs):
        return {"passed": True, "evidence": "已完成", "retry_guidance": ""}

    async def fake_goal_verify(session, project):
        return {"results": [{"id": "accept_1", "passed": True, "evidence": "ok"}], "reason": "ok"}

    monkeypatch.setattr(routes, "_workbench_answer_pending", fake_answer_pending)
    monkeypatch.setattr(routes, "_workbench_agent_reply", fake_fresh)
    monkeypatch.setattr(goal_loop, "_verify_step", fake_step_verify)
    monkeypatch.setattr(routes, "_workbench_verify_acceptance", fake_goal_verify)

    await goal_loop._ensure_schema(db_path)
    now = goal_loop._utc_iso()
    await goal_loop._execute(
        db_path,
        """
        INSERT INTO goal_runs
        (id, session_id, project_id, objective, status, phase,
         plan_definition_revision, permission_mode, reflection_mode,
         max_active_seconds, max_repair_rounds, active_seconds,
         active_started_at, repair_round, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'running', 'executing', ?, 'auto', 'standard', 3600, 2, 0, ?, 0, ?, ?)
        """,
        ("run_1", "session_1", "project_1", "完成账号登录功能", 3, now, now, now),
    )
    manager = goal_loop.GoalLoopManager(db_path)
    await manager._run("run_1")

    assert calls["answer"] == 1   # resumed via answer_pending
    assert calls["fresh"] == 0    # did NOT re-execute from scratch
    run = await goal_loop._get_run_by_id(db_path, "run_1")
    assert run["status"] == "completed"
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    step = stored["projects"][0]["sessions"][0]["plan"][0]
    assert step["status"] == "completed"
    assert "goalLoopResumeAnswer" not in step  # one-shot tag consumed
