from __future__ import annotations

import asyncio
import json
import sqlite3


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
    from cyrene.workbench.tasks import task_runs
    from cyrene.workbench.chat.chat_runs import ChatRunManager
    from cyrene.workbench.goals.goal_loop import GoalLoopManager

    db_path = str(tmp_path / "coordinator.sqlite3")
    manager = ChatRunManager(retention_seconds=0)
    manager.configure(db_path)
    coordinator = task_runs.coordinator_for(db_path)

    assert isinstance(coordinator, RunCoordinator)
    assert manager._coordinator is coordinator
    assert GoalLoopManager(db_path, object()).coordinator is coordinator

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

    goal_manager = GoalLoopManager(db_path, object())
    goal_manager.register_run("goal_run_conflict", "same_public_id")
    assert goal_manager.wake("goal_run_conflict") is False
    coordinator.finish(task_lease)
    release_chat.set()
    await asyncio.wait_for(chat_run.done.wait(), timeout=2)
    assert coordinator.get("conversation", "same_public_id") is None
    await manager.shutdown()


def test_project_storage_uses_one_row_per_task_and_updates_only_one(tmp_path):
    from cyrene.workbench.persistence.store import read_document, write_document

    db_path = tmp_path / "storage.sqlite3"
    payload = _projects_payload(tmp_path, two_sessions=True)
    write_document(
        db_path,
        "projects",
        payload,
        lambda: {"projects": []},
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


def test_normalized_project_read_does_not_request_a_write_lock(tmp_path):
    from cyrene.workbench.persistence.store import read_document, write_document

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


async def test_restart_rebinds_provisional_task_run_and_resumes_same_run_id(
    tmp_path,
    monkeypatch,
):
    from cyrene.workbench.projects import project_repository
    from cyrene.workbench.tasks import task_runs
    from cyrene.workbench.tasks.task_runs import (
        begin_task_run,
        current_task_run_id,
        recover_interrupted_task_runs,
    )
    from cyrene.workbench.persistence.store import write_document

    db_path = str(tmp_path / "recovery.sqlite3")
    payload = _projects_payload(tmp_path)
    write_document(
        db_path,
        "projects",
        payload,
        lambda: {"projects": []},
    )
    project_repository._configure_workbench_store(db_path)

    assert begin_task_run(
        "session_1",
        "run_crashed",
        request_id="request_crashed",
        run_type="execution",
        body={"input": "execute", "stepId": "step_session_1"},
    ) is True

    entered = asyncio.Event()
    release = asyncio.Event()
    resumed = {}

    async def resume_run(session_id, run_type, body):
        resumed.update(
            session_id=session_id,
            run_type=run_type,
            body=body,
            run_id=current_task_run_id(),
        )
        entered.set()
        await release.wait()
        return {"ok": True}

    assert await recover_interrupted_task_runs(db_path, resume_run) == 1
    await asyncio.wait_for(entered.wait(), timeout=1)
    lease = task_runs.coordinator_for(db_path).get("task", "session_1")
    assert lease is not None
    recovery_task = lease.task
    assert recovery_task is not None
    assert resumed == {
        "session_id": "session_1",
        "run_type": "execution",
        "body": {"input": "execute", "stepId": "step_session_1"},
        "run_id": "run_crashed",
    }
    release.set()
    await asyncio.wait_for(recovery_task, timeout=1)

    recovered = project_repository._read_workbench_store()
    _, session = project_repository._workbench_find_session(
        recovered, "session_1"
    )
    assert session is not None
    run = next(item for item in session["runs"] if item["id"] == "run_crashed")
    assert run["status"] == "completed"
    assert run["resumeBody"] == {
        "input": "execute",
        "stepId": "step_session_1",
    }
    assert run["events"][-1]["type"] == "RunCompletedEvent"
    assert session.get("activeRunId") is None
    assert session["plan"][0]["status"] == "running"
