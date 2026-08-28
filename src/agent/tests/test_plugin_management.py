from __future__ import annotations

import asyncio

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agent.plugin import (
    Plugin,
    PluginApplicationHost,
    PluginCustomizationState,
    PluginPack,
    PluginRegistry,
    PluginSetupContext,
)
from agent.context import ContextStoreRouter
from agent.hook import SESSION_START
from agent.plugin.plugin_impl.cyrene_soul.service import setup_soul
from agent.plugin.plugin_impl.cyrene_context.service import setup_runtime_context
from cyrene.runtime import settings_store
from route.plugins import register_plugin_routes


def test_tool_management_api_edits_exposure_and_persistently_deletes(
    tmp_path,
    monkeypatch,
):
    saved: list[dict] = []
    monkeypatch.setattr(
        settings_store,
        "set_",
        lambda key, value: saved.append({"key": key, "value": value}),
    )
    registry = PluginRegistry(customizations=PluginCustomizationState())
    registry.register_pack(
        PluginPack(
            id="demo",
            description="Demo tools",
            plugins=(Plugin(
                name="DemoTool",
                description="Original description",
                input_schema={"type": "object", "properties": {}},
                handler=lambda _arguments, _context: "ok",
            ),),
        ),
        source="user-test",
    )
    app = FastAPI()
    host = PluginApplicationHost(
        app=app,
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "app.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )
    router = APIRouter()
    register_plugin_routes(router, host)
    app.include_router(router)

    with TestClient(app) as client:
        changed = client.patch(
            "/api/plugins/tools/DemoTool",
            json={
                "name": "RenamedDemo",
                "description": "Description shown to Agent",
                "agent_exposure": "direct",
            },
        )
        assert changed.status_code == 200
        tool = next(
            item for item in changed.json()["plugins"]
            if item["id"] == "DemoTool"
        )
        assert tool["name"] == "RenamedDemo"
        assert tool["description"] == "Description shown to Agent"
        assert tool["agent_exposure"] == "direct"
        assert tool["customized_name"] is True

        deleted = client.delete("/api/plugins/tools/DemoTool")
        assert deleted.status_code == 200
        assert all(
            item["id"] != "DemoTool"
            for item in deleted.json()["plugins"]
        )

    assert saved[-1]["key"] == "plugin_tool_customizations"
    assert saved[-1]["value"]["DemoTool"]["deleted"] is True


def test_soul_plugin_mounts_before_other_session_context(tmp_path):
    class SoulService:
        @staticmethod
        def persona_context():
            return "## SELF:IDENTITY\n- Cyrene"

    store = ContextStoreRouter(tmp_path / "context")
    tree = store.create_tree(tree_id="chat", root_id="root")
    hooks = store.hooks_for(tree.id)
    hooks.register(SESSION_START, lambda _event: {"context": "memory"})
    setup_soul(PluginSetupContext(
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugins",
        workspace=tmp_path,
        tree=store,
        tree_id=tree.id,
        root_id=tree.root_id,
        hooks=hooks,
        data={"soul_enabled": True, "language": "en"},
        services={"soul": SoulService()},
    ))

    assert asyncio.run(hooks.session_start()) == (
        "## Persona memory\n## SELF:IDENTITY\n- Cyrene\n\nmemory"
    )
    store.close()


def test_enabled_soul_hook_fails_closed_without_its_application_service(tmp_path):
    store = ContextStoreRouter(tmp_path / "context")
    tree = store.create_tree(tree_id="chat-missing-soul", root_id="root")
    hooks = store.hooks_for(tree.id)
    setup_soul(PluginSetupContext(
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugins",
        workspace=tmp_path,
        tree=store,
        tree_id=tree.id,
        root_id=tree.root_id,
        hooks=hooks,
        data={"soul_enabled": True},
        services={},
    ))

    with pytest.raises(RuntimeError, match="soul service is unavailable"):
        asyncio.run(hooks.session_start())
    store.close()


def test_enabled_soul_hook_fails_closed_when_persona_read_fails(tmp_path):
    class BrokenSoulService:
        @staticmethod
        def persona_context():
            raise OSError("SOUL.md is unreadable")

    store = ContextStoreRouter(tmp_path / "context")
    tree = store.create_tree(tree_id="chat-unreadable-soul", root_id="root")
    hooks = store.hooks_for(tree.id)
    setup_soul(PluginSetupContext(
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugins",
        workspace=tmp_path,
        tree=store,
        tree_id=tree.id,
        root_id=tree.root_id,
        hooks=hooks,
        data={"soul_enabled": True},
        services={"soul": BrokenSoulService()},
    ))

    with pytest.raises(RuntimeError, match="SOUL.md is unreadable"):
        asyncio.run(hooks.session_start())
    store.close()


def test_runtime_context_plugin_mounts_only_the_durable_turn_value(tmp_path):
    store = ContextStoreRouter(tmp_path / "context")
    tree = store.create_tree(tree_id="chat-context", root_id="root")
    hooks = store.hooks_for(tree.id)
    setup_runtime_context(PluginSetupContext(
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugins",
        workspace=tmp_path,
        tree=store,
        tree_id=tree.id,
        root_id=tree.root_id,
        hooks=hooks,
        data={},
        services={},
    ))

    assert {hook.id for hook in hooks.list()} == {"cyrene-context-turn-start"}
    assert asyncio.run(hooks.turn_start({
        "metadata": {"ephemeral_context": "current turn context"},
    })) == "current turn context"
    assert asyncio.run(hooks.turn_start({"metadata": {}})) == ""
    store.close()
