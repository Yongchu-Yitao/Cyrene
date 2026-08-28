"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, FastAPI

from agent.plugin import (
    PluginApplicationHost,
    PluginContext,
    PluginRegistry,
    PluginRuntime,
)
from agent.plugin.plugin_impl.cyrene_content import plugin_pack
from agent.plugin.plugin_impl.cyrene_content import tool_result_store, web_search


def run(coroutine):
    return asyncio.run(coroutine)


def test_content_pack_has_no_legacy_tooling_dependency():
    root = Path(web_search.__file__).parent
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.glob("*.py"))
    )
    assert "cyrene.tooling" not in sources
    assert "cyrene.agent.context" not in sources
    assert "from route.pdf" not in sources
    assert "from route.search" not in sources


def test_disabled_content_pack_mounts_no_routes_or_process_services(tmp_path):
    registry = PluginRegistry(include_core=False)
    registry.register_pack(plugin_pack, source="test-content")
    registry.configure_activation(plugins={}, packs={"cyrene_content": False})
    host = PluginApplicationHost(
        app=FastAPI(),
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "app.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )
    router = APIRouter()

    host.attach(router)

    assert host.attached_packs == ()
    assert host.service("content") is None
    assert host.service("web_search") is None
    assert not {
        "/pdf/viewer",
        "/api/pdf/context-plan",
        "/api/pdf/analyze",
        "/api/workbench/search",
        "/api/settings/search",
    }.intersection(route.path for route in router.routes)


def test_content_pack_toolbox_search_and_result_read_chain(tmp_path, monkeypatch):
    class FakeSearchService:
        async def search(self, topic: str, **options: object) -> str:
            assert topic == "plugin protocol"
            assert options["detail"] == "preview"
            assert options["max_results"] == 3
            assert options["session_id"] == "chat-one"
            return "search evidence"

    monkeypatch.setattr(tool_result_store, "_RESULT_ROOT", tmp_path / "results")
    content_ref = tool_result_store.store_tool_result(
        "alpha\nbeta\ngamma",
        session_id="chat-one",
    )

    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test-content")
    runtime = PluginRuntime(registry)
    context = PluginContext(
        workspace=tmp_path,
        tree_id="chat-one",
        data={"session_id": "chat-one", "run_id": "run-one"},
        services={
            "content": object(),
            "web_search": FakeSearchService(),
            "tool_results": tool_result_store.get_tool_result_store(),
        },
    )

    listing = run(runtime.call("toolbox", {"operation": "list"}, context))
    assert "cyrene_content" in listing.value["packs"]

    described = run(
        runtime.call(
            "toolbox",
            {"operation": "describe", "name": "cyrene_content"},
            context,
        )
    )
    assert {item["name"] for item in described.value["plugins"]} == {
        "AnalyzeAttachment",
        "WebFetch",
        "read_tool_result",
    }

    searched = run(
        runtime.call(
            "WebSearch",
            {
                "query": "plugin protocol",
                "detail": "preview",
                "max_results": 3,
            },
            context,
        )
    )
    assert searched.success is True
    assert searched.value == "search evidence"

    unavailable = run(
        runtime.call(
            "WebSearch",
            {"query": "must fail closed", "detail": "preview"},
            PluginContext(data={"session_id": "chat-one"}),
        )
    )
    assert unavailable.success is False
    assert unavailable.error in {"Plugin execution failed.", "插件执行失败。"}

    read = run(
        runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "read_tool_result",
                "arguments": {
                    "content_ref": content_ref,
                    "offset": 6,
                    "limit": 4,
                },
            },
            context,
        )
    )
    page = json.loads(read.value["result"])
    assert page["content"] == "beta"
    assert page["has_more"] is True

    wrong_session = PluginContext(
        data={"session_id": "chat-two"},
        services={
            "content": object(),
            "tool_results": tool_result_store.get_tool_result_store(),
        },
    )
    rejected = run(
        runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "read_tool_result",
                "arguments": {"content_ref": content_ref},
            },
            wrong_session,
        )
    )
    assert rejected.value["result"] in {
        "Tool failed: the result reference or paging arguments are invalid.",
        "工具失败：结果引用或分页参数无效。",
    }
