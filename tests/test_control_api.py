"""Contract and behavior tests for the versioned desktop-local Control API."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyrene.runtime import database as runtime_database
from cyrene.runtime.io import atomic_write_json
from cyrene.workbench.chat_runs import ChatRunManager
from route.registry import register_routes


@pytest.fixture
def control_env(monkeypatch, tmp_path):
    from cyrene import config as cyrene_config
    from cyrene.agent import state as agent_state
    from cyrene.runtime import attachments as managed_attachments
    from cyrene.workbench import chat as chat_service
    from cyrene.workbench import runtime as workbench_runtime

    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    data_dir.mkdir()
    store_dir.mkdir()
    workspace_dir.mkdir()
    db_path = str(store_dir / "control.sqlite3")
    asyncio.run(runtime_database.init_db(db_path))

    monkeypatch.setattr(cyrene_config, "DATA_DIR", data_dir)
    monkeypatch.setattr(cyrene_config, "STORE_DIR", store_dir)
    monkeypatch.setattr(cyrene_config, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(workbench_runtime, "DATA_DIR", data_dir)
    monkeypatch.setattr(workbench_runtime, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(chat_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(agent_state, "_DATA_DIR", data_dir)
    monkeypatch.setattr(agent_state, "DATA_DIR", data_dir)
    monkeypatch.setattr(
        managed_attachments,
        "UPLOADS_DIR",
        data_dir / "webui_uploads",
    )
    monkeypatch.setattr(
        managed_attachments,
        "EXPORTS_DIR",
        data_dir / "webui_exports",
    )
    agent_state._sessions.clear()

    chat_service._CHATS_STORE = data_dir / "workbench_chats.json"
    workbench_runtime._WORKBENCH_STORE = data_dir / "workbench_projects.json"
    manager = ChatRunManager(retention_seconds=30)
    monkeypatch.setattr(chat_service, "_CHAT_RUN_MANAGER", manager)

    async def fake_task_agent_reply(*_args, **_kwargs):
        return "step completed"

    async def fake_archive_task_knowledge(*_args, **_kwargs):
        return None

    # Install deterministic task-execution ports before route composition.
    # The production routes capture explicit service dependencies at startup.
    monkeypatch.setattr(
        workbench_runtime,
        "_workbench_agent_reply",
        fake_task_agent_reply,
    )
    monkeypatch.setattr(
        workbench_runtime,
        "_workbench_archive_run_knowledge",
        fake_archive_task_knowledge,
    )

    atomic_write_json(
        workbench_runtime._WORKBENCH_STORE,
        {
            "projects": [
                {
                    "id": "project_1",
                    "name": "Control Project",
                    "dataKey": "project_1",
                    "description": "Control API fixture",
                    "workspacePath": str(workspace_dir),
                    "status": "active",
                    "model": "test-model",
                    "createdAt": "2026-07-27T00:00:00+00:00",
                    "updatedAt": "2026-07-27T01:00:00+00:00",
                    "sessions": [
                        {
                            "id": "task_1",
                            "projectId": "project_1",
                            "kind": "task",
                            "title": "Existing task",
                            "status": "idle",
                            "createdAt": "2026-07-27T00:00:00+00:00",
                            "updatedAt": "2026-07-27T00:00:00+00:00",
                        }
                    ],
                }
            ],
            "activeProjectId": "project_1",
            "activeSessionId": "task_1",
        },
    )
    atomic_write_json(
        chat_service._CHATS_STORE,
        {
            "chats": [
                {
                    "id": "chat_1",
                    "projectId": "project_1",
                    "kind": "chat",
                    "title": "Existing chat",
                    "status": "idle",
                    "model": "test-model",
                    "permissionMode": "auto",
                    "createdAt": "2026-07-27T00:00:00+00:00",
                    "updatedAt": "2026-07-27T01:00:00+00:00",
                    "messages": [
                        {
                            "id": "message_1",
                            "role": "user",
                            "content": "hello",
                            "createdAt": "2026-07-27T00:30:00+00:00",
                        }
                    ],
                }
            ]
        },
    )

    app = FastAPI()
    register_routes(app, bot=None, db_path=db_path)
    with TestClient(app) as client:
        yield {
            "client": client,
            "manager": manager,
            "db_path": db_path,
            "data_dir": data_dir,
            "workspace_dir": workspace_dir,
        }


def test_control_capabilities_disclose_remote_gateway_and_remaining_limits(
    control_env,
):
    response = control_env["client"].get("/v1/control/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["api_version"] == "v1"
    assert payload["auth_boundary"] == "desktop_local"
    assert payload["remote_transport_available"] is True
    assert payload["durable_run_events"] is True
    assert "chats.send" in payload["operations"]
    assert "attachments.read" in payload["operations"]
    features = {item["name"]: item["available"] for item in payload["features"]}
    assert features == {
        "chat_runs": True,
        "durable_run_events": True,
        "remote_gateway": True,
        "remote_desktop": False,
    }


def test_workbench_chat_detail_does_not_hydrate_sibling_transcripts(
    control_env,
    monkeypatch,
):
    from cyrene.workbench import chat as chat_service

    def unexpected_full_store_read():
        raise AssertionError("single-chat detail must not read the full chat store")

    monkeypatch.setattr(chat_service, "_read_chats_store", unexpected_full_store_read)
    response = control_env["client"].get("/api/workbench/chats/chat_1")

    assert response.status_code == 200
    assert response.json()["chat"]["id"] == "chat_1"


def test_control_projects_are_summaries_without_local_paths_or_credentials(
    control_env,
):
    response = control_env["client"].get("/v1/control/projects")

    assert response.status_code == 200
    assert response.json() == {
        "projects": [
            {
                "id": "project_1",
                "name": "Control Project",
                "status": "active",
                "updated_at": "2026-07-27T01:00:00+00:00",
                "task_count": 1,
            }
        ]
    }
    assert "workspacePath" not in response.text
    assert "model" not in response.text


def test_control_task_contract_lists_creates_reads_cancels_and_lists_artifacts(
    control_env,
):
    client = control_env["client"]

    listed = client.get(
        "/v1/control/tasks",
        params={"project_id": "project_1"},
    )
    assert listed.status_code == 200
    assert listed.json()["tasks"][0]["id"] == "task_1"

    created = client.post(
        "/v1/control/tasks",
        json={
            "project_id": "project_1",
            "title": "Remote task",
            "goal": "Validate the remote task contract",
            "priority": "high",
        },
    )
    assert created.status_code == 201
    task = created.json()["task"]
    assert task["project_id"] == "project_1"
    assert task["title"] == "Remote task"
    assert task["priority"] == "high"

    fetched = client.get(f"/v1/control/tasks/{task['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["task"]["goal"] == (
        "Validate the remote task contract"
    )

    artifacts = client.get(
        f"/v1/control/tasks/{task['id']}/artifacts"
    )
    assert artifacts.status_code == 200
    assert artifacts.json() == {"artifacts": []}

    cancelled = client.post(f"/v1/control/tasks/{task['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["action"] == "cancel"
    assert cancelled.json()["task"]["status"] == "cancelled"


def test_control_task_plan_can_be_approved_and_run_step_by_step(
    control_env,
):
    from cyrene.workbench import runtime as workbench_runtime

    payload = workbench_runtime._read_workbench_store()
    _project, task = workbench_runtime._workbench_find_session(
        payload,
        "task_1",
    )
    assert task is not None
    task["goal"] = "Execute the shared plan"
    task["status"] = "planning"
    task["planDefinitionRevision"] = 1
    task["approvedPlanDefinitionRevision"] = None
    task["plan"] = [
        {
            "id": "step_1",
            "title": "Inspect and finish",
            "description": "Complete the requested work.",
            "status": "pending",
            "dependsOn": [],
        }
    ]
    workbench_runtime._write_workbench_store(payload)

    stale = control_env["client"].post(
        "/v1/control/tasks/task_1/plan/approve",
        json={"plan_definition_revision": 0},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_plan_revision"

    approved = control_env["client"].post(
        "/v1/control/tasks/task_1/plan/approve",
        json={"plan_definition_revision": 1},
    )
    assert approved.status_code == 200
    assert approved.json()["task"]["status"] == "waiting_for_approval"

    executed = control_env["client"].post(
        "/v1/control/tasks/task_1/steps/step_1/runs",
        json={
            "message": "Execute step 1",
            "plan_definition_revision": 1,
            "permission_mode": "auto",
        },
    )
    assert executed.status_code == 200
    result = executed.json()["task"]
    assert result["status"] == "review"
    assert result["plan"][0]["status"] == "completed"


def test_control_chat_contract_lists_creates_and_reads(control_env):
    client = control_env["client"]

    listed = client.get(
        "/v1/control/chats",
        params={"project_id": "project_1"},
    )
    assert listed.status_code == 200
    assert listed.json()["chats"][0] == {
        "id": "chat_1",
        "project_id": "project_1",
        "title": "Existing chat",
        "status": "idle",
        "created_at": "2026-07-27T00:00:00+00:00",
        "updated_at": "2026-07-27T01:00:00+00:00",
        "message_count": 1,
        "running": False,
        "awaiting_user": False,
    }

    created = client.post(
        "/v1/control/chats",
        json={"project_id": "project_1", "title": "Remote work"},
    )
    assert created.status_code == 201
    chat_id = created.json()["chat"]["id"]
    assert created.json()["chat"]["project_id"] == "project_1"
    assert created.json()["chat"]["title"] == "Remote work"

    detail = client.get(f"/v1/control/chats/{chat_id}")
    assert detail.status_code == 200
    assert detail.json()["chat"]["messages"] == []


def test_control_chat_attachment_contract_downloads_referenced_file(
    control_env,
):
    from cyrene.runtime import attachments as managed_attachments
    from cyrene.workbench import chat as chat_service

    managed_attachments.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = managed_attachments.EXPORTS_DIR / "remote-result.txt"
    target.write_bytes(b"complete remote result")
    payload = chat_service._read_chats_store()
    chat = chat_service._find_chat(payload, "chat_1")
    assert chat is not None
    chat["messages"][0]["attachments"] = [
        {
            "id": target.name,
            "name": "result.txt",
            "path": str(target),
            "content_type": "text/plain",
            "kind": "code",
            "size": target.stat().st_size,
            "url": f"/api/chat/export/{target.name}",
        }
    ]
    chat_service._write_chats_store(payload)

    detail = control_env["client"].get("/v1/control/chats/chat_1")
    assert detail.status_code == 200
    attachment = detail.json()["chat"]["messages"][0]["attachments"][0]
    assert attachment["download_url"] == (
        "/v1/control/chats/chat_1/attachments/remote-result.txt"
    )

    downloaded = control_env["client"].get(attachment["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content == b"complete remote result"


def test_control_task_approval_requires_matching_pending_question(control_env):
    response = control_env["client"].post(
        "/v1/control/tasks/task_1/approvals/question_missing/responses",
        json={"answer": "Allow once", "permission_mode": "default"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "approval_not_pending"


def test_control_requests_reject_unknown_fields(control_env):
    response = control_env["client"].post(
        "/v1/control/chats",
        json={
            "project_id": "project_1",
            "title": "No ambient authority",
            "workspace_path": "/tmp/escape",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_control_message_starts_detached_run_and_replays_public_events(
    control_env, monkeypatch,
):
    from cyrene import agent
    from cyrene.agent import state as agent_state

    observed = {}

    async def fake_run_agent(**kwargs):
        observed.update(kwargs)
        writer = agent_state._reply_stream_writer.get()
        assert writer is not None
        await writer({"type": "reasoning_delta", "delta": "private chain"})
        return "finished remotely"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)
    response = control_env["client"].post(
        "/v1/control/chats/chat_1/messages",
        json={
            "message": "inspect the project",
            "permission_mode": "default",
            "language": "en",
        },
    )

    assert response.status_code == 202
    accepted = response.json()
    assert accepted["run_id"].startswith("run_")
    assert accepted["chat_id"] == "chat_1"

    deadline = time.monotonic() + 2
    run_payload = {}
    while time.monotonic() < deadline:
        current = control_env["client"].get(
            f"/v1/control/runs/{accepted['run_id']}"
        )
        assert current.status_code == 200
        run_payload = current.json()
        if run_payload["completed"]:
            break
        time.sleep(0.01)

    assert run_payload["completed"] is True
    assert run_payload["outcome"] == "reply"
    assert observed["permission_mode"] == "default"
    assert observed["workspace_dir"] == str(control_env["workspace_dir"].resolve())

    events = control_env["client"].get(
        f"/v1/control/runs/{accepted['run_id']}/events",
        params={"after": 0},
    )
    assert events.status_code == 200
    payload = events.json()
    event_types = [event["type"] for event in payload["events"]]
    assert "ack" in event_types
    assert "reply_done" in event_types
    assert "saved" in event_types
    assert "reasoning_delta" not in event_types
    assert "private chain" not in events.text
    assert payload["completed"] is True

    replay = control_env["client"].get(
        f"/v1/control/runs/{accepted['run_id']}/events",
        params={"after": payload["next_cursor"]},
    )
    assert replay.status_code == 200
    assert replay.json()["events"] == []


def test_control_interrupt_targets_run_id_not_arbitrary_chat(control_env):
    from cyrene.workbench.chat_runs import ChatRun

    manager = control_env["manager"]
    run = ChatRun("chat_1", {"type": "ack", "chatId": "chat_1"})
    manager.runs["chat_1"] = run

    missing = control_env["client"].post(
        "/v1/control/runs/run_missing/interrupt"
    )
    assert missing.status_code == 409
    assert missing.json()["code"] == "control_run_not_active"

    response = control_env["client"].post(
        f"/v1/control/runs/{run.run_id}/interrupt"
    )
    assert response.status_code == 200
    assert response.json() == {
        "interrupted": True,
        "run_id": run.run_id,
        "status": "cancelled",
    }


def test_control_guidance_is_run_scoped_and_idempotent(control_env):
    from cyrene.workbench.chat_runs import ChatRun

    manager = control_env["manager"]
    run = ChatRun(
        "chat_1",
        {"type": "ack", "chatId": "chat_1"},
        db_path=control_env["db_path"],
    )
    run.ready.set()
    manager.runs["chat_1"] = run

    first = control_env["client"].post(
        f"/v1/control/runs/{run.run_id}/guidance",
        json={"message": "focus on the contract", "request_id": "guide_1"},
    )
    duplicate = control_env["client"].post(
        f"/v1/control/runs/{run.run_id}/guidance",
        json={"message": "focus on the contract", "request_id": "guide_1"},
    )

    assert first.status_code == 200
    assert first.json()["queued"] is True
    assert first.json()["duplicate"] is False
    assert first.json()["run_id"] == run.run_id
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        **first.json(),
        "duplicate": True,
    }


def test_control_event_cursor_reports_evicted_buffer_gap(control_env):
    from cyrene.workbench.chat_runs import ChatRun

    manager = control_env["manager"]
    run = ChatRun(
        "chat_1",
        {"type": "ack", "chatId": "chat_1"},
        max_buffer=4,
    )
    for index in range(5):
        asyncio.run(
            run.publish({"type": "reply_delta", "delta": str(index)})
        )
    manager.runs["chat_1"] = run

    response = control_env["client"].get(
        f"/v1/control/runs/{run.run_id}/events",
        params={"after": 0},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [event["cursor"] for event in payload["events"]] == [1, 4, 5, 6]
    assert payload["truncated"] is True


def test_control_openapi_is_explicitly_versioned_and_typed(control_env):
    schema = control_env["client"].app.openapi()
    paths = {
        path: item
        for path, item in schema["paths"].items()
        if path.startswith("/v1/control/")
    }

    assert set(paths) == {
        "/v1/control/capabilities",
        "/v1/control/projects",
        "/v1/control/chats",
        "/v1/control/chats/{chat_id}",
        "/v1/control/chats/{chat_id}/messages",
        "/v1/control/chats/{chat_id}/attachments/{attachment_id}",
        "/v1/control/runs/{run_id}",
        "/v1/control/runs/{run_id}/events",
        "/v1/control/runs/{run_id}/guidance",
        "/v1/control/runs/{run_id}/interrupt",
        "/v1/control/tasks",
        "/v1/control/tasks/{task_id}",
        "/v1/control/tasks/{task_id}/dispatch",
        "/v1/control/tasks/{task_id}/plan/approve",
        "/v1/control/tasks/{task_id}/steps/{step_id}/runs",
        "/v1/control/tasks/{task_id}/pause",
        "/v1/control/tasks/{task_id}/resume",
        "/v1/control/tasks/{task_id}/cancel",
        "/v1/control/chats/{chat_id}/approvals/{question_id}/responses",
        "/v1/control/tasks/{task_id}/approvals/{question_id}/responses",
        "/v1/control/tasks/{task_id}/artifacts",
        "/v1/control/tasks/{task_id}/artifacts/{artifact_id}",
    }
    send = paths["/v1/control/chats/{chat_id}/messages"]["post"]
    assert send["operationId"] == "control_v1_send_chat_message"
    assert "202" in send["responses"]
    assert send["tags"] == ["Control"]
