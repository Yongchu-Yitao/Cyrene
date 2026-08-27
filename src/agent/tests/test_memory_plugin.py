from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agent.plugin import (
    PluginApplicationContext,
    PluginApplicationHost,
    PluginContext,
    PluginRegistry,
    PluginRuntime,
)
from agent.plugin.plugin_impl.cyrene_memory import plugin_pack
from agent.plugin.plugin_impl.cyrene_memory import application as memory_application


def run(coroutine):
    return asyncio.run(coroutine)


def test_memory_pack_completes_toolbox_list_describe_invoke_chain(tmp_path):
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test-memory")
    runtime = PluginRuntime(registry)
    context = PluginContext(workspace=tmp_path)

    listing = run(runtime.call("toolbox", {"operation": "list"}, context))
    assert "cyrene_memory" in listing.value["packs"]

    described = run(
        runtime.call(
            "toolbox",
            {"operation": "describe", "name": "cyrene_memory"},
            context,
        )
    )
    assert described.success is True
    assert len(described.value["plugins"]) == 9
    search_description = next(
        item
        for item in described.value["plugins"]
        if item["name"] == "search_project_memory"
    )
    assert search_description["pack"] == "cyrene_memory"
    assert search_description["input_schema"] == registry.resolve(
        "search_project_memory"
    ).input_schema

    invoked = run(
        runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "search_project_memory",
                "arguments": {"query": "verified"},
            },
            context,
        )
    )
    assert invoked.success is True
    result = json.loads(invoked.value["result"])
    assert result["status"] == "error"
    assert result["type"] == "not_found"


def test_seeded_user_directory_loads_complete_memory_pack(tmp_path):
    from agent.plugin.native_tools import seed_builtin_plugin_directory
    from cyrene.runtime.database import init_db

    seeded_root = tmp_path / "seeded"
    seed_builtin_plugin_directory(seeded_root)
    isolated_root = tmp_path / "memory-only"
    isolated_root.mkdir()
    shutil.copytree(
        seeded_root / "cyrene_memory",
        isolated_root / "cyrene_memory",
    )
    registry = PluginRegistry(include_core=False)

    assert registry.load_directory(isolated_root) == ()
    pack = next(pack for pack in registry.list_packs() if pack.id == "cyrene_memory")
    assert len(pack.plugins) == 9
    assert pack.setup is not None
    assert pack.application_setup is not None
    assert (isolated_root / "cyrene_memory" / "application.py").is_file()
    assert (isolated_root / "cyrene_memory" / "service.py").is_file()

    database = tmp_path / "seeded-memory.db"
    run(init_db(str(database)))
    app = FastAPI()
    host = PluginApplicationHost(
        app=app,
        registry=registry,
        bot=None,
        db_path=str(database),
        data_directory=tmp_path / "data",
        plugin_directory=isolated_root,
    )
    router = APIRouter()
    host.attach(router)
    assert host.service("memory") is not None
    assert "/api/workbench/memory" in {route.path for route in router.routes}


def test_memory_pack_mounts_session_context_through_hook(monkeypatch, tmp_path):
    from agent.demo import AgentTreeSession
    from agent.plugin import Plugin, PluginPack
    from agent.plugin.plugin_impl.cyrene_memory.service import MemoryService

    captured = []

    async def model(arguments, _context):
        captured.append(arguments["messages"])
        return {"content": "done", "tool_calls": [], "model": "fake"}

    monkeypatch.setattr(
        MemoryService,
        "context_block",
        lambda _self: "Project memory:\nUse the verified design.",
    )
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test-memory")
    registry.register_pack(
        PluginPack(
            "model",
            "test model",
            (
                Plugin(
                    "MiniMax",
                    "fake",
                    {"type": "object"},
                    model,
                    kind="model",
                ),
            ),
        ),
        source="test-model",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentTreeSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
    )

    session.submit("continue", run_id="run-memory")
    run(session.drain())

    nodes = session.snapshot()["nodes"]
    assert [node["value"].get("role") for node in nodes] == [
        "system",
        "user",
        "context",
        "assistant",
    ]
    mounted = nodes[2]["value"]
    assert mounted["context_kind"] == "plugin_session"
    assert mounted["context_source"] == "hook"
    assert mounted["metadata"] == {"source": "SessionStart"}
    assert "Project memory:\nUse the verified design." in captured[0][0]["content"]
    session.close()


def test_memory_plugin_session_end_owns_automatic_capture(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    from agent.context import ContextStoreRouter
    from agent.hook import HookEvent, SESSION_END
    from agent.plugin.plugin_impl.cyrene_memory.service import MemoryService

    captured = {}

    def persist(
        _self,
        messages,
        assistant_node_id,
        run_id,
        _anchor_value,
        _details,
    ):
        captured["snapshot"] = {
            "messages": messages,
            "assistant_node_id": assistant_node_id,
            "run_id": run_id,
        }
        return {"chatId": "chat-memory", "projectId": "project-memory"}

    async def capture(
        _self,
        user_text,
        assistant_text,
        messages,
        snapshot,
        *,
        evidence,
    ):
        captured["capture"] = {
            "user": user_text,
            "assistant": assistant_text,
            "messages": messages,
            "snapshot": snapshot,
            "evidence": evidence,
        }

    monkeypatch.setattr(MemoryService, "_persist_learning_snapshot", persist)
    monkeypatch.setattr(MemoryService, "_capture_and_learn", capture)

    with ContextStoreRouter(tmp_path / "context") as store:
        tree = store.create_tree(
            {"role": "system", "content": "system"},
            tree_id="chat-memory",
            root_id="root",
        )
        user = store.mount(
            tree.id,
            tree.root_id,
            {
                "role": "user",
                "content": "Remember this verified choice.",
                "run_id": "run-1",
            },
        )
        assistant = store.mount(
            tree.id,
            user.id,
            {
                "role": "assistant",
                "content": "I will use the Plugin boundary.",
                "run_id": "run-1",
            },
        )
        service = MemoryService(
            tmp_path,
            store,
            tree.id,
            {
                "session_id": tree.id,
                "project_id": "project-memory",
                "memory_archive_enabled": False,
                "memory_write_enabled": True,
            },
        )

        run(
            service.on_session_end(
                HookEvent(
                    SESSION_END,
                    tree.id,
                    datetime.now(timezone.utc),
                    payload={
                        "status": "completed",
                        "assistant_node_id": assistant.id,
                        "run_id": "run-1",
                        "assistant_text": "I will use the Plugin boundary.",
                    },
                    node_id=assistant.id,
                    is_root=True,
                )
            )
        )

    assert captured["snapshot"]["assistant_node_id"] == assistant.id
    assert captured["snapshot"]["run_id"] == "run-1"
    assert captured["capture"]["user"] == "Remember this verified choice."
    assert captured["capture"]["assistant"] == "I will use the Plugin boundary."
    assert captured["capture"]["snapshot"] == {
        "chatId": "chat-memory",
        "projectId": "project-memory",
    }


def test_project_learning_uses_exact_tool_call_node(monkeypatch, tmp_path):
    from agent.context import ContextStoreRouter
    from agent.plugin.plugin_impl.cyrene_memory.service import MemoryService
    from agent.plugin.plugin_impl.cyrene_memory import project_memory
    from cyrene.workbench.chat_repository import ChatRepository

    captured = {}

    def persist(chat_id, project_id, messages, **kwargs):
        captured.update(
            chat_id=chat_id,
            project_id=project_id,
            messages=messages,
            kwargs=kwargs,
        )
        return {
            "chatId": chat_id,
            "projectId": project_id,
            "messages": messages,
            "treeId": kwargs["tree_id"],
            "treeNodeId": kwargs["tree_node_id"],
            "snapshotSource": "context_tree_node",
        }

    monkeypatch.setattr(ChatRepository, "get", lambda _self, _chat_id: {"kind": "chat"})
    monkeypatch.setattr(project_memory, "persist_tree_context_snapshot", persist)
    monkeypatch.setattr(
        project_memory,
        "schedule_learning",
        lambda _project_id, snapshot, **_kwargs: {
            "status": "queued",
            "snapshot": snapshot,
        },
    )

    with ContextStoreRouter(tmp_path / "context") as store:
        tree = store.create_tree(
            {"role": "system", "content": "system"},
            tree_id="chat-memory",
            root_id="root",
        )
        user = store.mount(
            tree.id,
            tree.root_id,
            {"role": "user", "content": "remember this", "run_id": "run-1"},
        )
        context = store.mount(
            tree.id,
            user.id,
            {
                "role": "context",
                "content": "Project memory:\nPrior decision.",
                "context_kind": "plugin_session",
                "run_id": "run-1",
            },
        )
        anchor = store.mount(
            tree.id,
            context.id,
            {
                "role": "assistant",
                "content": "",
                "run_id": "run-1",
                "model": "fake-model",
                "model_identity": {"candidateId": "fake-candidate"},
                "tool_calls": [
                    {
                        "id": "learn-call",
                        "name": "trigger_project_memory_learning",
                        "arguments": {"reason": "durable evidence"},
                    }
                ],
            },
        )
        service = MemoryService(
            tmp_path,
            store,
            tree.id,
            {
                "session_id": tree.id,
                "project_id": "project-memory",
                "completed_turn_count": 1,
            },
        )

        result = service.trigger_project_learning(
            "durable evidence",
            node_id=anchor.id,
        )

    assert result["status"] == "queued"
    assert captured["kwargs"]["tree_id"] == tree.id
    assert captured["kwargs"]["tree_node_id"] == anchor.id
    assert captured["kwargs"]["round_id"] == "run-1"
    assert captured["kwargs"]["model"] == {
        "id": "fake-model",
        "candidateId": "fake-candidate",
    }
    assert [message["role"] for message in captured["messages"]] == [
        "system",
        "user",
    ]
    assert "Project memory:\nPrior decision." in captured["messages"][0]["content"]


def test_memory_application_setup_owns_routes_search_and_shutdown(monkeypatch, tmp_path):
    class FakeMemoryApplication:
        memory = object()
        project_memory = object()

        async def search_workbench(self, _query, _limit):
            return []

        async def shutdown(self):
            return None

        def startup(self):
            return None

    fake = FakeMemoryApplication()
    monkeypatch.setattr(
        memory_application.MemoryApplication,
        "create",
        classmethod(
            lambda _cls, _db_path, _data_directory, *, model_gateway: fake
        ),
    )

    app = FastAPI()
    router = APIRouter()
    services = {}
    frontend_modules = []
    search_providers = {}
    startup_handlers = []
    shutdown_handlers = []
    context = PluginApplicationContext(
        app=app,
        router=router,
        bot=None,
        db_path=str(tmp_path / "test.db"),
        data_directory=tmp_path,
        plugin_directory=tmp_path / "plugin_impl",
        services=services,
        frontend_modules=frontend_modules,
        search_providers=search_providers,
        startup_handlers=startup_handlers,
        shutdown_handlers=shutdown_handlers,
    )

    memory_application.setup_application(context)

    paths = {route.path for route in router.routes}
    assert "/api/workbench/memory" in paths
    assert "/api/projects/{project_id}/memory-prompt" in paths
    assert "/api/memory" in paths
    assert "/api/settings/soul" in paths
    assert "/api/search/conversations" in paths
    assert services == {"memory": fake}
    assert search_providers == {"memory": fake.search_workbench}
    assert frontend_modules == ["memory"]
    assert startup_handlers == [fake.startup]
    assert shutdown_handlers == [fake.shutdown]


def test_memory_pack_attaches_real_application_contributions(tmp_path):
    registry = PluginRegistry(include_core=False)
    registry.register_pack(plugin_pack, source="test-memory")
    app = FastAPI()
    host = PluginApplicationHost(
        app=app,
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "test.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )
    router = APIRouter()

    host.attach(router)

    assert host.attached_packs == ("cyrene_memory",)
    assert host.setup_failures == {}
    assert host.service("memory") is not None
    assert "memory" in host.search_providers
    assert host.frontend_modules == ["memory"]
    paths = {route.path for route in router.routes}
    assert "/api/memory" in paths
    assert "/api/workbench/memory" in paths
    assert "/api/projects/{project_id}/memory-prompt" in paths
    assert "/api/workbench/chats/{chat_id}/memory-learning" in paths
    assert "/api/settings/soul" in paths
    assert "/api/search/conversations" in paths


def test_memory_plugin_serves_frontend_contract_end_to_end(tmp_path, monkeypatch):
    from agent.plugin.plugin_impl.cyrene_memory import archive, soul
    from cyrene.runtime.database import init_db
    from cyrene.workbench.store import write_document

    database = tmp_path / "runtime.db"
    run(init_db(str(database)))
    write_document(
        database,
        "projects",
        {
            "projects": [
                {
                    "id": "project-1",
                    "name": "Memory Project",
                    "workspacePath": str(tmp_path / "workspace"),
                }
            ]
        },
        lambda: {"projects": []},
    )
    monkeypatch.setattr(soul, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(
        archive,
        "CONVERSATIONS_DIR",
        tmp_path / "workspace" / ".cyrene" / "conversations",
    )

    registry = PluginRegistry(include_core=False)
    registry.register_pack(plugin_pack, source="test-memory")
    app = FastAPI()
    host = PluginApplicationHost(
        app=app,
        registry=registry,
        bot=None,
        db_path=str(database),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )
    router = APIRouter()
    host.attach(router)
    app.include_router(router)
    run(host.startup())

    with TestClient(app) as client:
        empty = client.get("/api/workbench/memory?workspace=project-1")
        assert empty.status_code == 200, empty.text
        assert empty.json()["overview"]["total"] == 0

        created = client.post(
            "/api/workbench/memory?workspace=project-1",
            json={
                "content": "The Plugin API is connected to the Memory page.",
                "category": "project",
                "source": "manual",
                "tags": ["frontend"],
            },
        )
        assert created.status_code == 200
        memory_id = created.json()["id"]
        assert created.json()["overview"]["total"] == 1

        updated = client.patch(
            f"/api/workbench/memory/{memory_id}?workspace=project-1",
            json={"content": "The complete Plugin API is connected."},
        )
        assert updated.status_code == 200
        assert updated.json()["memories"][0]["content"] == (
            "The complete Plugin API is connected."
        )

        prompt = client.get("/api/projects/project-1/memory-prompt")
        assert prompt.status_code == 200
        changed_prompt = client.patch(
            "/api/projects/project-1/memory-prompt",
            json={"prompt": "Preserve verified project decisions."},
        )
        assert changed_prompt.status_code == 200
        assert changed_prompt.json()["current"]["prompt"] == (
            "Preserve verified project decisions."
        )

        service = host.service("memory")
        run(
            service.archive_exchange(
                "frontend contract needle",
                "verified",
                1,
                archive_session_id="session-1",
            )
        )
        searched = client.get("/api/search/conversations?q=needle")
        assert searched.status_code == 200
        assert searched.json()["results"]

        overview = client.get("/api/memory")
        assert overview.status_code == 200
        assert set(overview.json()) == {
            "soul",
            "short_term",
            "context_window",
            "archive",
        }

        updated_soul = client.put(
            "/api/settings/soul",
            json={"content": "# Plugin persona\n\n## SELF:IDENTITY\n- editable\n"},
        )
        assert updated_soul.status_code == 200
        assert client.get("/api/settings/soul").json()["content"].startswith(
            "# Plugin persona"
        )

        deleted = client.delete(
            f"/api/workbench/memory/{memory_id}?workspace=project-1"
        )
        assert deleted.status_code == 200
        assert deleted.json()["overview"]["total"] == 0

    run(host.shutdown())
    frontend = (
        Path(__file__).resolve().parents[2]
        / "webui"
        / "frontend"
        / "workbench-memory.jsx"
    ).read_text(encoding="utf-8")
    project_frontend = (
        Path(__file__).resolve().parents[2]
        / "webui"
        / "frontend"
        / "features"
        / "shell"
        / "support.jsx"
    ).read_text(encoding="utf-8")
    chat_frontend = (
        Path(__file__).resolve().parents[2]
        / "webui"
        / "frontend"
        / "features"
        / "chat"
        / "model-api.jsx"
    ).read_text(encoding="utf-8")
    settings_frontend = (
        Path(__file__).resolve().parents[2]
        / "webui"
        / "frontend"
        / "settings-overlay.jsx"
    ).read_text(encoding="utf-8")
    search_frontend = (
        Path(__file__).resolve().parents[2]
        / "webui"
        / "frontend"
        / "shared"
        / "search"
        / "overlay.jsx"
    ).read_text(encoding="utf-8")
    compiled_frontend = (
        Path(__file__).resolve().parents[2]
        / "webui"
        / "static"
        / "app"
        / "compiled"
        / "app.js"
    ).read_text(encoding="utf-8")
    assert "/api/workbench/memory" in frontend
    assert "/api/projects/" in project_frontend
    assert "/memory-prompt" in project_frontend
    assert "/memory-learning" in chat_frontend
    assert "/api/settings/soul" in settings_frontend
    assert "/api/search/conversations" in search_frontend
    for route_fragment in (
        "/api/workbench/memory",
        "/memory-prompt",
        "/memory-learning",
        "/api/settings/soul",
        "/api/search/conversations",
    ):
        assert route_fragment in compiled_frontend
    assert host.frontend_modules == ["memory"]


def test_memory_application_search_preserves_visibility_and_project_scope(
    monkeypatch,
    tmp_path,
):
    from cyrene.workbench import context as workbench_context
    from cyrene.workbench import store as workbench_store

    monkeypatch.setattr(
        workbench_context,
        "read_projects",
        lambda: [{"id": "project-1", "name": "Demo"}],
    )
    monkeypatch.setattr(
        workbench_store,
        "list_document_keys",
        lambda _db_path, prefix="": ["memory:project-1"],
    )
    monkeypatch.setattr(
        workbench_store,
        "read_document",
        lambda *_args, **_kwargs: [
            {
                "id": "memory-visible",
                "content": "User prefers verified fixtures",
                "category": "preference",
                "tags": ["tests"],
            },
            {
                "id": "memory-hidden",
                "content": "verified internal report",
                "category": "task_report",
            },
        ],
    )
    application = memory_application.MemoryApplication(
        "test.db", object(), object(), tmp_path
    )

    results = application._search("verified", 10)

    assert [item["id"] for item in results] == ["memory-visible"]
    assert results[0]["projectId"] == "project-1"
    assert results[0]["projectName"] == "Demo"


def test_memory_application_owns_workbench_injection_and_learning_policy(tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import structured
    from cyrene.workbench.store import ensure_schema

    database = tmp_path / "memory-policy.db"
    ensure_schema(database)
    structured.configure_store(str(database))
    application = memory_application.MemoryApplication(
        str(database), object(), object(), tmp_path / "data"
    )
    project = {"id": "project policy", "name": "Policy"}
    session = {"id": "session-1"}

    structured.add_agent_memory(
        "project_policy",
        "Use the verified Plugin boundary.",
        category="project",
    )
    fixed, volatile = application.compose_workbench_context(project, session)

    assert "verified Plugin boundary" in fixed
    assert volatile == ""
    assert session["_promptMemoryKey"] == "project_policy"

    structured.add_agent_memory(
        "project_policy",
        "A fact learned after this session started.",
        category="fact",
    )
    fixed_again, volatile_again = application.compose_workbench_context(
        project,
        session,
    )

    assert "learned after this session" not in fixed_again
    assert "learned after this session" in volatile_again
    assert application.store_reflection_insights(
        project,
        {
            "excluded_paths": ["keep memory policy in Workbench core"],
            "promising_directions": ["delegate the complete policy to the Plugin"],
        },
    ) == 2
    assert application.store_task_report(
        project,
        "任务：完成记忆插件迁移\n结果：前后端链路已验证。",
    ) is True
    stored = structured._load("project_policy")
    assert {item.get("category") for item in stored} >= {
        "reflection",
        "task_report",
    }
    assert "完成记忆插件迁移" in application.render_past_task_reports(project)


def test_memory_application_owns_workbench_lifecycle_operations(monkeypatch, tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import project_memory, structured

    calls: list[tuple] = []
    monkeypatch.setattr(
        project_memory,
        "current_snapshot",
        lambda project_id: {
            "prompt": f"memory:{project_id}",
            "modifiedAt": "now",
            "hash": "hash",
        },
    )
    monkeypatch.setattr(
        structured,
        "delete_workspace_memory",
        lambda workspace_id: calls.append(("workspace", workspace_id)),
    )

    async def cancel_project(project_id):
        calls.append(("cancel-project", project_id))

    async def cancel_chat(chat_id):
        calls.append(("cancel-chat", chat_id))

    monkeypatch.setattr(project_memory, "cancel_project_jobs", cancel_project)
    monkeypatch.setattr(project_memory, "cancel_chat_jobs", cancel_chat)
    monkeypatch.setattr(
        project_memory,
        "delete_project_memory",
        lambda project_id, chat_ids: calls.append(
            ("project", project_id, tuple(chat_ids))
        ),
    )
    monkeypatch.setattr(
        project_memory,
        "delete_chat_context",
        lambda chat_id: calls.append(("chat-context", chat_id)),
    )
    application = memory_application.MemoryApplication(
        "test.db", object(), object(), tmp_path
    )

    assert application.current_snapshot("project-1")["prompt"] == "memory:project-1"
    application.delete_workspace("workspace-1")
    run(application.cancel_project_jobs("project-1"))
    application.delete_project("project-1", ["chat-1", "chat-2"])
    run(application.delete_chat("chat-1"))

    assert calls == [
        ("workspace", "workspace-1"),
        ("cancel-project", "project-1"),
        ("project", "project-1", ("chat-1", "chat-2")),
        ("cancel-chat", "chat-1"),
        ("chat-context", "chat-1"),
    ]


def test_memory_application_owns_persona_reset(monkeypatch, tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import short_term

    soul_path = tmp_path / "workspace" / ".cyrene" / "SOUL.md"
    soul_path.parent.mkdir(parents=True)
    soul_path.write_text("old persona", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(
        memory_application.MemoryApplication,
        "soul_path",
        lambda _self: soul_path,
    )
    monkeypatch.setattr(
        memory_application.MemoryApplication,
        "ensure_soul",
        lambda _self: calls.append("ensure-soul"),
    )
    monkeypatch.setattr(
        short_term,
        "save_entries",
        lambda entries: calls.append(("short-term", list(entries))),
    )
    application = memory_application.MemoryApplication(
        "", object(), object(), tmp_path / "data"
    )

    application.reset_persona_memory()

    assert not soul_path.exists()
    assert calls == ["ensure-soul", ("short-term", [])]


def test_memory_application_owns_archive_storage_and_backup_contract(
    monkeypatch,
    tmp_path,
):
    from agent.plugin.plugin_impl.cyrene_memory import archive, soul

    conversations = tmp_path / "workspace" / ".cyrene" / "conversations"
    soul_path = tmp_path / "workspace" / ".cyrene" / "SOUL.md"
    monkeypatch.setattr(archive, "CONVERSATIONS_DIR", conversations)
    monkeypatch.setattr(soul, "WORKSPACE_DIR", tmp_path / "workspace")
    conversations.mkdir(parents=True)
    source = conversations / "2026-08-26.md"
    source.write_text(
        "# Conversations - 2026-08-26\n\n"
        "## 10:00:00 UTC\n"
        "<!-- archive_session_id: keep -->\n\n"
        "**User**: keep\n\n**Cyrene**: kept\n\n---\n\n"
        "## 11:00:00 UTC\n"
        "<!-- archive_session_id: remove -->\n\n"
        "**User**: remove\n\n**Cyrene**: removed\n\n---\n",
        encoding="utf-8",
    )
    application = memory_application.MemoryApplication(
        "", object(), object(), tmp_path / "data"
    )

    documents = application.list_archive_documents()
    assert [item["date"] for item in documents] == ["2026-08-26"]
    assert len(documents[0]["sections"]) == 2
    assert application.latest_archived_user_message_time().isoformat() == (
        "2026-08-26T11:00:00+00:00"
    )
    assert application.delete_archive_session("2026-08-26", "remove") is True
    assert [
        section["archive_session_id"]
        for section in application.read_archive_sections("2026-08-26")
    ] == ["keep"]
    with pytest.raises(ValueError):
        application.read_archive_sections("..")

    assert application.backup_sources() == {
        "files": ((soul_path, "workspace/SOUL.md"),),
        "directories": ((conversations, "workspace/conversations"),),
    }
    assert application.storage_paths() == {
        "memory": (
            tmp_path / "data" / "short_term.json",
            tmp_path / "data" / "memory_steward.json",
            soul_path,
        ),
        "conversations": (conversations,),
    }


def test_memory_application_owns_existing_data_detection(monkeypatch, tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import archive
    from cyrene.workbench.store import ensure_schema, write_document

    database = tmp_path / "memory-existing.db"
    ensure_schema(database)
    conversations = tmp_path / "conversations"
    monkeypatch.setattr(archive, "CONVERSATIONS_DIR", conversations)
    application = memory_application.MemoryApplication(
        str(database), object(), object(), tmp_path / "data"
    )

    assert application.has_existing_data() is False
    write_document(
        database,
        "memory:project-1",
        [{"id": "memory-1", "content": "persisted"}],
        list,
    )
    assert application.has_existing_data() is True
