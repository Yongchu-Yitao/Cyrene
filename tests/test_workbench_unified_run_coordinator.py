from __future__ import annotations

import asyncio
import json
import sqlite3
import threading


def _projects_payload(workspace, *, two_sessions: bool = False):
    def session(session_id: str):
        return {
            "id": session_id,
            "projectId": "project_1",
            "kind": "task",
            "title": f"Task {session_id}",
            "goal": "Exercise the task state machine",
            "status": "planning",
            "priority": "medium",
            "constraints": [],
            "planRevision": 1,
            "planDefinitionRevision": 1,
            "approvedPlanDefinitionRevision": 1,
            "plan": [
                {
                    "id": f"step_{session_id}",
                    "title": "One step",
                    "status": "running",
                    "order": 1,
                    "dependsOn": [],
                }
            ],
            "events": [],
            "runs": [],
            "artifacts": [],
            "acceptanceCriteria": [],
            "createdAt": "2026-08-21T00:00:00+00:00",
            "updatedAt": "2026-08-21T00:00:00+00:00",
        }

    sessions = [session("session_1")]
    if two_sessions:
        sessions.append(session("session_2"))
    return {
        "projects": [
            {
                "id": "project_1",
                "name": "Coordinator test",
                "workspacePath": str(workspace),
                "sessions": sessions,
                "createdAt": "2026-08-21T00:00:00+00:00",
                "updatedAt": "2026-08-21T00:00:00+00:00",
            }
        ],
        "activeProjectId": "project_1",
        "activeSessionId": "session_1",
    }


async def test_one_shared_coordinator_owns_chat_task_and_goal_loop(tmp_path, monkeypatch):
    from cyrene.runtime.run_coordinator import RunCoordinator
    from cyrene.workbench import chat as chat_service
    from cyrene.workbench import task_runs
    from cyrene.workbench.chat_runs import ChatRunManager
    from cyrene.workbench.goal_loop import GoalLoopManager

    db_path = str(tmp_path / "coordinator.sqlite3")
    monkeypatch.setattr(chat_service, "_record_chat_run_outcome", lambda *_a, **_k: None)
    manager = ChatRunManager(retention_seconds=0)
    manager.configure(db_path)
    coordinator = task_runs.coordinator_for(db_path)

    assert isinstance(coordinator, RunCoordinator)
    assert manager._coordinator is coordinator
    assert GoalLoopManager(db_path).coordinator is coordinator

    release_chat = asyncio.Event()

    async def runner(run):
        await release_chat.wait()
        run.outcome = {"kind": "reply"}

    chat_run, is_new = manager.start_or_get(
        "same_public_id",
        {"type": "ack", "clientRequestId": "chat-request"},
        runner,
        stream=False,
    )
    attached, attached_is_new = manager.start_or_get(
        "same_public_id",
        {"type": "ack"},
        runner,
        stream=False,
    )
    task_lease = coordinator.try_acquire(
        "task",
        "same_public_id",
        "task_run_1",
        bind_current_task=False,
    )

    assert is_new is True
    assert attached_is_new is False
    assert attached is chat_run
    assert task_lease is not None  # owner namespaces stay domain-specific
    assert coordinator.try_acquire(
        "task", "same_public_id", "task_run_2", bind_current_task=False
    ) is None

    goal_manager = GoalLoopManager(db_path)
    goal_manager.register_run("goal_run_conflict", "same_public_id")
    assert goal_manager.wake("goal_run_conflict") is False
    coordinator.finish(task_lease)
    release_chat.set()
    await asyncio.wait_for(chat_run.done.wait(), timeout=2)
    assert coordinator.get("conversation", "same_public_id") is None
    await manager.shutdown()


async def test_low_level_session_conflict_never_cancels_the_existing_run():
    from cyrene.agent.coordinator import (
        SessionRunConflictError,
        run_session_operation,
    )

    entered = asyncio.Event()
    release = asyncio.Event()
    cancelled = False

    async def first_operation():
        nonlocal cancelled
        entered.set()
        try:
            await release.wait()
            return "first-completed"
        except asyncio.CancelledError:
            cancelled = True
            raise

    first = asyncio.create_task(
        run_session_operation("conflict-session", first_operation)
    )
    await asyncio.wait_for(entered.wait(), timeout=1)

    try:
        await run_session_operation(
            "conflict-session",
            lambda: asyncio.sleep(0, result="second"),
        )
    except SessionRunConflictError as exc:
        assert exc.session_id == "conflict-session"
    else:
        raise AssertionError("the competing run should have been rejected")

    assert cancelled is False
    assert first.done() is False
    release.set()
    assert await asyncio.wait_for(first, timeout=1) == "first-completed"


async def test_session_operation_reports_only_after_lock_acquisition():
    from cyrene.agent.coordinator import run_session_operation

    acquired = []

    async def operation():
        assert acquired == ["acquired"]
        return "done"

    result = await run_session_operation(
        "acquisition-callback-session",
        operation,
        on_acquired=lambda: acquired.append("acquired"),
    )

    assert result == "done"
    assert acquired == ["acquired"]


def test_project_storage_migrates_to_one_row_per_task_and_updates_only_one(tmp_path):
    from cyrene.workbench.store import read_document, write_document

    db_path = tmp_path / "storage.sqlite3"
    export_path = tmp_path / "workbench_projects.json"
    payload = _projects_payload(tmp_path, two_sessions=True)
    write_document(
        db_path,
        "projects",
        payload,
        lambda: {"projects": []},
        export_path=export_path,
    )

    with sqlite3.connect(db_path) as conn:
        shell = json.loads(
            conn.execute(
                "SELECT payload_json FROM workbench_state WHERE key = 'projects'"
            ).fetchone()[0]
        )
        rows_before = dict(
            conn.execute(
                "SELECT session_id, updated_at FROM workbench_task_sessions"
            ).fetchall()
        )

    assert set(rows_before) == {"session_1", "session_2"}
    first_summary = shell["projects"][0]["sessions"][0]
    assert first_summary["isSummary"] is True
    assert "runs" not in first_summary
    assert "plan" not in first_summary

    hydrated = read_document(db_path, "projects", lambda: {"projects": []})
    first = next(
        item
        for item in hydrated["projects"][0]["sessions"]
        if item["id"] == "session_1"
    )
    first["runs"].append({"id": "run_1", "status": "completed"})
    first["updatedAt"] = "2026-08-21T01:00:00+00:00"
    write_document(
        db_path,
        "projects",
        hydrated,
        lambda: {"projects": []},
        export_path=export_path,
    )

    with sqlite3.connect(db_path) as conn:
        rows_after = dict(
            conn.execute(
                "SELECT session_id, updated_at FROM workbench_task_sessions"
            ).fetchall()
        )
    assert rows_after["session_1"] != rows_before["session_1"]
    assert rows_after["session_2"] == rows_before["session_2"]

    reloaded = read_document(db_path, "projects", lambda: {"projects": []})
    by_id = {
        item["id"]: item for item in reloaded["projects"][0]["sessions"]
    }
    assert by_id["session_1"]["runs"][0]["id"] == "run_1"
    assert by_id["session_2"]["runs"] == []
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert all(
        not item.get("isSummary")
        for item in exported["projects"][0]["sessions"]
    )


def test_normalized_project_read_does_not_request_a_write_lock(tmp_path):
    from cyrene.workbench.store import read_document, write_document

    db_path = tmp_path / "read-while-writing.sqlite3"
    payload = _projects_payload(tmp_path)
    write_document(db_path, "projects", payload, lambda: {"projects": []})

    writer = sqlite3.connect(db_path, timeout=0.05)
    writer.execute("PRAGMA busy_timeout = 50")
    writer.execute("BEGIN IMMEDIATE")
    try:
        hydrated = read_document(db_path, "projects", lambda: {"projects": []})
    finally:
        writer.rollback()
        writer.close()

    assert hydrated["projects"][0]["id"] == "project_1"


def test_task_chat_audits_before_agent_rejects_competitor_and_keeps_task_open(
    tmp_path,
    monkeypatch,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from route.registry import register_routes
    from cyrene.workbench import runtime as runtime

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store_path = data_dir / "workbench_projects.json"
    store_path.write_text(
        json.dumps(_projects_payload(tmp_path), ensure_ascii=False),
        encoding="utf-8",
    )
    db_path = str(tmp_path / "api.sqlite3")
    entered_agent = threading.Event()
    release_agent = threading.Event()
    cancelled = threading.Event()

    async def fake_agent_reply(*_args, **_kwargs):
        entered_agent.set()
        try:
            await asyncio.to_thread(release_agent.wait)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "A bounded task-chat reply"

    async def allow_budget(_session_id):
        return None

    async def no_archive(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime, "DATA_DIR", data_dir)
    monkeypatch.setattr(runtime, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(runtime, "_db_path", "")
    monkeypatch.setattr(runtime, "_CONFIGURED_WORKBENCH_STORE", None)
    monkeypatch.setattr(runtime, "_workbench_agent_reply", fake_agent_reply)
    monkeypatch.setattr(runtime, "_check_budget_gate", allow_budget)
    monkeypatch.setattr(runtime, "_workbench_archive_run_knowledge", no_archive)
    monkeypatch.setattr(runtime, "schedule_capture", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime, "append_notification", lambda **_kwargs: {})

    app = FastAPI()
    register_routes(app, bot=None, db_path=db_path)
    first_client = TestClient(app)
    second_client = TestClient(app)
    first_response = {}

    def send_first():
        first_response["response"] = first_client.post(
            "/api/task-sessions/session_1/chat",
            json={"message": "first", "clientRequestId": "request_1"},
        )

    thread = threading.Thread(target=send_first, daemon=True)
    thread.start()
    assert entered_agent.wait(timeout=3)

    running_payload = runtime._read_workbench_store()
    _, running_session = runtime._workbench_find_session(
        running_payload, "session_1"
    )
    assert running_session is not None
    assert running_session["activeRunId"]
    assert len(running_session["runs"]) == 1
    provisional = running_session["runs"][0]
    provisional_id = provisional["id"]
    assert provisional["status"] == "running"
    assert provisional["events"][0]["type"] == "RunAcceptedEvent"

    competing = second_client.post(
        "/api/task-sessions/session_1/chat",
        json={"message": "second", "clientRequestId": "request_2"},
    )
    assert competing.status_code == 409
    assert competing.json()["code"] == "task_run_in_progress"
    assert cancelled.is_set() is False
    assert thread.is_alive()

    release_agent.set()
    thread.join(timeout=5)
    assert thread.is_alive() is False
    response = first_response["response"]
    assert response.status_code == 200
    body = response.json()
    session = body["session"]
    run = body["run"]
    assert run["id"] == provisional_id
    assert session["status"] == "answered"
    assert session.get("activeRunId") is None
    assert len(session["runs"]) == 1
    assert session["runs"][0]["status"] == "completed"
    event_types = [item["type"] for item in session["runs"][0]["events"]]
    assert event_types[0] == "RunAcceptedEvent"
    assert "UserMessageEvent" in event_types
    assert "AgentResponseEvent" in event_types
    assert event_types[-1] == "RunCompletedEvent"
    assert cancelled.is_set() is False


def test_restart_reconciles_provisional_task_run_and_requeues_step(
    tmp_path,
    monkeypatch,
):
    from cyrene.workbench import runtime
    from cyrene.workbench.task_runs import (
        begin_task_run,
        recover_interrupted_task_runs,
    )
    from cyrene.workbench.store import write_document

    db_path = str(tmp_path / "recovery.sqlite3")
    store_path = tmp_path / "workbench_projects.json"
    payload = _projects_payload(tmp_path)
    write_document(
        db_path,
        "projects",
        payload,
        lambda: {"projects": []},
        export_path=store_path,
    )
    monkeypatch.setattr(runtime, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(runtime, "_db_path", db_path)
    monkeypatch.setattr(runtime, "_CONFIGURED_WORKBENCH_STORE", store_path)

    assert begin_task_run(
        "session_1",
        "run_crashed",
        request_id="request_crashed",
        run_type="execution",
        body={"input": "execute", "stepId": "step_session_1"},
    ) is True
    assert recover_interrupted_task_runs() == 1

    recovered = runtime._read_workbench_store()
    _, session = runtime._workbench_find_session(recovered, "session_1")
    assert session is not None
    run = next(item for item in session["runs"] if item["id"] == "run_crashed")
    assert run["status"] == "interrupted"
    assert run["terminationReason"] == "process_restarted"
    assert run["events"][-1]["type"] == "RunInterruptedEvent"
    assert session.get("activeRunId") is None
    assert session["plan"][0]["status"] == "pending"
