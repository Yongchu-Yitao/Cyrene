from __future__ import annotations

import json

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory
from cyrene.workbench.persistence import store
from cyrene.workbench.chat.conversation_context_service import AgentContextRepository
from cyrene.workbench.sessions.session_presentation import WorkbenchSessionPresentation
from cyrene.workbench.http.agent.sessions import register_session_routes


def _empty_store():
    return {"chats": []}


def _seed_chat(db_path):
    store.write_chat_bundle(
        db_path,
        {
            "chats": [
                {
                    "id": "chat_one",
                    "projectId": "project_one",
                    "kind": "chat",
                    "title": "Plugin migration",
                    "status": "idle",
                    "model": "test-model",
                    "createdAt": "2026-08-26T10:00:00+00:00",
                    "updatedAt": "2026-08-26T10:01:00+00:00",
                    "messages": [
                        {
                            "id": "user_one",
                            "role": "user",
                            "content": "Inspect the Plugin runtime",
                            "createdAt": "2026-08-26T10:00:00+00:00",
                        },
                        {
                            "id": "assistant_one",
                            "role": "assistant",
                            "content": "The runtime is ready.",
                            "createdAt": "2026-08-26T10:01:00+00:00",
                            "usage": {
                                "prompt_tokens": 20,
                                "completion_tokens": 10,
                                "total_tokens": 30,
                            },
                            "processingDurationMs": 100,
                        },
                    ],
                }
            ]
        },
        _empty_store,
    )


def _seed_context(db_path):
    context_directory = workbench_agent_data_directory(str(db_path)) / "context"
    with ContextStoreRouter(context_directory) as router:
        tree = router.create_tree(
            {
                "role": "system",
                "content": "Agent instructions",
                "_plugin_session_state": {
                    "cyrene_subagent": {
                        "child_context_ids": [],
                        "public_snapshot": {
                            "subagents": {
                                "worker_one": {
                                    "task": "Inspect code",
                                    "status": "done",
                                    "result": "ready",
                                    "round_id": "run_one",
                                },
                            },
                        },
                    }
                },
            },
            tree_id="chat_one",
            root_id="root",
        )
        user = router.mount(
            tree.id,
            tree.root_id,
            {"role": "user", "content": "Inspect", "run_id": "run_one"},
            node_id="user",
        )
        call = router.mount(
            tree.id,
            user.id,
            {
                "role": "assistant",
                "content": "",
                "run_id": "run_one",
                "model": "test-model",
                "tool_calls": [
                    {
                        "id": "call_one",
                        "name": "toolbox",
                        "arguments": {
                            "operation": "invoke",
                            "name": "Glob",
                        },
                    }
                ],
            },
            node_id="call",
        )
        result = router.mount(
            tree.id,
            call.id,
            {
                "role": "tool_results",
                "run_id": "run_one",
                "results": [
                    {
                        "call_id": "call_one",
                        "name": "toolbox",
                        "success": True,
                        "value": {
                            "operation": "invoke",
                            "pack": "cyrene_code",
                            "name": "Glob",
                        },
                    }
                ],
            },
            node_id="result",
        )
        router.mount(
            tree.id,
            result.id,
            {
                "role": "assistant",
                "content": "done",
                "run_id": "run_one",
                "model": "test-model",
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
                "session_end_complete": True,
            },
            node_id="final",
        )


def test_sessions_project_sqlite_chat_and_context_tree(tmp_path):
    db_path = tmp_path / "cyrene.sqlite3"
    _seed_chat(db_path)
    _seed_context(db_path)

    sessions = WorkbenchSessionPresentation(db_path).list()

    assert len(sessions) == 1
    session = sessions[0]
    assert session["id"] == "chat_one"
    assert session["messageCount"] == 2
    assert session["summary"]["total_tokens"] == 30
    assert session["summary"]["toolCalls"] == 1
    assert session["usedPluginPacks"] == ["cyrene_code"]
    assert session["subagents"][0]["id"] == "worker_one"
    assert session["contextTree"]["treeId"] == "chat_one"


def test_agent_context_batch_reads_only_indexed_trees_with_one_router(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "cyrene.sqlite3"
    _seed_context(db_path)
    context_directory = workbench_agent_data_directory(str(db_path)) / "context"
    counts = {"routers": 0, "trees": []}
    original_init = ContextStoreRouter.__init__
    original_get_tree = ContextStoreRouter.get_tree

    def counted_init(self, *args, **kwargs):
        counts["routers"] += 1
        original_init(self, *args, **kwargs)

    def counted_get_tree(self, tree_id):
        counts["trees"].append(tree_id)
        return original_get_tree(self, tree_id)

    monkeypatch.setattr(ContextStoreRouter, "__init__", counted_init)
    monkeypatch.setattr(ContextStoreRouter, "get_tree", counted_get_tree)

    states = AgentContextRepository(context_directory).read_many(
        ("missing_one", "chat_one", "missing_two")
    )

    assert list(states) == ["chat_one"]
    assert states["chat_one"]["checkpoint"]["status"] == "completed"
    assert counts == {"routers": 1, "trees": ["chat_one"]}


def test_export_merges_context_tree_activity_and_clear_removes_context(tmp_path):
    db_path = tmp_path / "cyrene.sqlite3"
    _seed_chat(db_path)
    _seed_context(db_path)
    presentation = WorkbenchSessionPresentation(db_path)

    exported = presentation.export("chat_one", "json")
    payload = json.loads(exported.content)

    assert exported.filename.endswith(".json")
    assert any(
        message.get("activityCard") is True
        for message in payload["chat"]["messages"]
    )

    cleared, deleted_archives = presentation.clear("chat_one")
    assert cleared["messageCount"] == 0
    assert deleted_archives == 0
    assert store.read_chat(db_path, "chat_one", _empty_store)["messages"] == []
    context_directory = workbench_agent_data_directory(str(db_path)) / "context"
    with ContextStoreRouter(context_directory) as router:
        with pytest.raises(TreeNotFoundError):
            router.get_tree("chat_one")


def test_workbench_session_routes_use_explicit_repository_endpoints(tmp_path):
    db_path = tmp_path / "cyrene.sqlite3"
    _seed_chat(db_path)
    app = FastAPI()
    router = APIRouter()
    register_session_routes(router, None, str(db_path))
    app.include_router(router)
    client = TestClient(app)

    listed = client.get("/api/workbench/sessions")
    exported = client.get(
        "/api/workbench/sessions/chat_one/export",
        params={"format": "markdown"},
    )

    assert listed.status_code == 200
    assert listed.json()["sessions"][0]["id"] == "chat_one"
    assert exported.status_code == 200
    assert "# Plugin migration" in exported.text
    assert client.get("/api/sessions").status_code == 404
