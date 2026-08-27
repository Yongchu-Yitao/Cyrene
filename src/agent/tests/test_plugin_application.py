from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI

from agent.plugin import (
    PluginApplicationHost,
    PluginContext,
    PluginPack,
    PluginRegistry,
    PluginRuntime,
)
from cyrene.workbench import presentation_runtime
from cyrene.workbench.presentation_service import PresentationQueryService


def _host(tmp_path: Path, registry: PluginRegistry) -> PluginApplicationHost:
    return PluginApplicationHost(
        app=FastAPI(),
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "app.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )


def test_application_setup_commits_routes_services_lifecycle_and_search(
    tmp_path,
    monkeypatch,
):
    events: list[str] = []

    async def search(query: str, limit: int) -> list[dict[str, Any]]:
        return [{"id": "one", "type": "demo", "title": query, "limit": limit}]

    def application_setup(context) -> None:
        @context.router.get("/plugin-demo")
        async def plugin_demo():
            return {"ok": True}

        context.provide("demo", object())
        context.provide_search("demo", search)
        context.expose_frontend("demo")
        context.on_startup(lambda: events.append("startup"))
        context.on_shutdown(lambda: events.append("shutdown"))

    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(
            id="demo",
            description="demo",
            plugins=(),
            application_setup=application_setup,
        ),
        source="test",
    )
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)

    assert host.attached_packs == ("demo",)
    assert host.service("demo") is not None
    assert host.frontend_modules == ["demo"]
    assert {route.path for route in router.routes} == {"/plugin-demo"}

    async def core_search(query, types, limit, db_path):
        assert query == "needle"
        assert types == set()
        assert limit == 5
        assert db_path == str((tmp_path / "app.db").resolve())
        return {}

    async def ui_data(_timezone, _db_path):
        return {"ok": True}

    monkeypatch.setattr(presentation_runtime, "_search_workbench_items", core_search)
    monkeypatch.setattr(presentation_runtime, "_build_ui_data", ui_data)
    queries = PresentationQueryService(
        db_path=tmp_path / "app.db",
        frontend_modules=host.frontend_modules,
        search_providers=host.search_providers,
    )
    assert queries.search_types == frozenset({"project", "task", "chat", "demo"})

    async def scenario() -> None:
        assert await queries.search_workbench("needle", {"demo"}, 5) == {
            "demo": [{"id": "one", "type": "demo", "title": "needle", "limit": 5}]
        }
        assert await queries.ui_data() == {"ok": True, "pluginModules": ["demo"]}
        await host.startup()
        await host.shutdown()

    asyncio.run(scenario())
    assert events == ["startup", "shutdown"]


def test_knowledge_pack_owns_tools_routes_search_and_frontend(tmp_path):
    from agent.plugin.plugin_impl.cyrene_knowledge import plugin_pack

    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)

    assert host.attached_packs == ("cyrene_knowledge",)
    assert host.service("knowledge") is not None
    assert "knowledge" in host.search_providers
    assert host.frontend_modules == ["knowledge"]
    route_paths = {route.path for route in router.routes}
    assert "/api/workbench/library/items" in route_paths
    assert "/api/workbench/library/search" in route_paths

    class FakeKnowledge:
        async def list_documents(self, context, *, status, limit):
            assert context.data == {"session_id": "chat_one"}
            assert status == "indexed"
            assert limit == 3
            return [{
                "id": "doc_one",
                "name": "Design.md",
                "path": str(tmp_path / "Design.md"),
                "status": "indexed",
                "chunk_count": 2,
                "size": 12,
            }]

    context = PluginContext(
        data={"session_id": "chat_one"},
        services={"knowledge": FakeKnowledge()},
    )
    runtime = PluginRuntime(registry)

    async def scenario() -> None:
        listing = await runtime.call("toolbox", {"operation": "list"}, context)
        assert listing.success is True
        assert "cyrene_knowledge" in listing.value["packs"]
        described = await runtime.call(
            "toolbox",
            {"operation": "describe", "name": "cyrene_knowledge"},
            context,
        )
        assert described.success is True
        assert {tool["name"] for tool in described.value["plugins"]} == {
            "ListKnowledgeDocuments",
            "SearchKnowledge",
            "ListLibraryItems",
            "SearchLibrary",
            "UpdateLibraryMetadata",
        }
        assert all(
            tool["pack"] == "cyrene_knowledge"
            for tool in described.value["plugins"]
        )
        invoked = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "ListKnowledgeDocuments",
                "arguments": {"status": "indexed", "limit": 3},
            },
            context,
        )
        assert invoked.success is True
        assert "Design.md" in invoked.value["result"]
        assert "doc_one" in invoked.value["result"]

    asyncio.run(scenario())


def test_host_without_knowledge_pack_has_no_knowledge_contributions(tmp_path):
    host = _host(tmp_path, PluginRegistry(include_core=False))
    router = APIRouter()
    host.attach(router)

    assert host.service("knowledge") is None
    assert "knowledge" not in host.search_providers
    assert "knowledge" not in host.frontend_modules
    assert not any("/library" in route.path for route in router.routes)
