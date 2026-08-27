from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent.plugin import PluginContext, PluginRegistry, PluginRuntime
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
        services={"web_search": FakeSearchService()},
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
        "WebSearch",
        "read_tool_result",
    }

    searched = run(
        runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "WebSearch",
                "arguments": {
                    "query": "plugin protocol",
                    "detail": "preview",
                    "max_results": 3,
                },
            },
            context,
        )
    )
    assert searched.success is True
    assert searched.value["result"] == "search evidence"

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

    wrong_session = PluginContext(data={"session_id": "chat-two"})
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
    assert "another session" in rejected.value["result"]
