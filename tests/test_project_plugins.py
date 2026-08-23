from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
import httpx

from cyrene.plugins.manager import PluginError, PluginManager
from route.plugins import register_plugin_routes


def _write_plugin(root: Path, *, chat_provider: bool = False) -> Path:
    root.mkdir(parents=True)
    contributions = [
        {"point": "cyrene.view", "id": "main", "title": "Demo"},
        {
            "point": "cyrene.projectTool",
            "id": "demo-tool",
            "title": "Demo",
            "view": "main",
        },
    ]
    manifest = {
        "apiVersion": 1,
        "id": "com.example.demo",
        "name": "Demo Plugin",
        "version": "1.2.3",
        "backend": {"type": "python", "entry": "plugin.py"},
        "frontend": {"mode": "iframe", "entry": "ui/index.html"},
        "contributes": contributions,
    }
    root.joinpath("plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    root.joinpath("ui").mkdir()
    root.joinpath("ui/index.html").write_text("<h1>Demo</h1>", encoding="utf-8")
    provider_registration = ""
    if chat_provider:
        provider_registration = """
    context.register("cyrene.chatProvider", {
        "id": "demo-chat",
        "models": [{"id": "demo-model", "capabilities": ["chat", "tools"]}],
        "complete": complete,
    })
"""
    root.joinpath("plugin.py").write_text(
        """
async def echo(args):
    return {"echo": args, "project": CONTEXT.project_id}

async def complete(request):
    return {
        "message": {"role": "assistant", "content": "plugin:" + request["model"]},
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }

def activate(context):
    global CONTEXT
    CONTEXT = context
    context.register_method("demo.echo", echo)
"""
        + provider_registration,
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_project_plugin_lifecycle_contributions_rpc_and_delete_policy(tmp_path):
    source = _write_plugin(tmp_path / "source")
    manager = PluginManager(tmp_path / "runtime")

    installed = await manager.install(source)
    assert installed["plugin"]["id"] == "com.example.demo"

    enabled = await manager.set_enabled("com.example.demo", "project-1", True)
    assert enabled["enabled"] is True
    contributions = await manager.contributions("project-1")
    assert {(item["point"], item["id"]) for item in contributions} == {
        ("cyrene.view", "main"),
        ("cyrene.projectTool", "demo-tool"),
    }
    view = next(item for item in contributions if item["point"] == "cyrene.view")
    assert view["entry"] == "ui/index.html"
    assert manager.asset_path(
        "com.example.demo", "project-1", "ui/index.html"
    ).read_text(encoding="utf-8") == "<h1>Demo</h1>"

    result = await manager.call(
        "com.example.demo", "project-1", "demo.echo", {"value": 7}
    )
    assert result == {"echo": {"value": 7}, "project": "project-1"}

    await manager.set_enabled("com.example.demo", "project-1", False)
    assert await manager.contributions("project-1") == []
    with pytest.raises(PluginError, match="disabled"):
        await manager.call("com.example.demo", "project-1", "demo.echo", {})

    data_path = manager.data_dir / "com.example.demo"
    assert data_path.is_dir()
    deleted = await manager.delete("com.example.demo")
    assert deleted["dataDeleted"] is False
    assert data_path.is_dir()
    assert not manager.package_dir("com.example.demo").exists()
    await manager.close()


@pytest.mark.asyncio
async def test_chat_provider_contribution_materializes_and_completes(tmp_path, monkeypatch):
    import cyrene.plugins.manager as manager_module
    from cyrene.model_runtime.client import call_llm
    from cyrene.plugins.integrations import (
        chat_model_candidates,
        complete_chat_candidate,
    )

    source = _write_plugin(tmp_path / "source", chat_provider=True)
    manager = PluginManager(tmp_path / "runtime")
    monkeypatch.setattr(manager_module, "_MANAGER", manager)
    await manager.install(source)
    await manager.set_enabled("com.example.demo", "project-2", True)

    candidates = await chat_model_candidates("project-2")
    assert [item["id"] for item in candidates] == [
        "plugin:com.example.demo:demo-chat:demo-model"
    ]
    assert candidates[0]["provider"] == "cyrene_plugin"

    response = await complete_chat_candidate(
        candidates[0],
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        max_tokens=20,
        stream=True,
        thinking="auto",
        response_format=None,
        caller="test",
        phase="reply",
        session_id="chat-1",
        timeout=10.0,
    )
    assert response["content"] == "plugin:demo-model"
    assert response["usage"] == {"prompt_tokens": 2, "completion_tokens": 1}

    stream_events = []

    async def capture(event):
        stream_events.append(event)

    response = await call_llm(
        [{"role": "user", "content": "hello"}],
        candidates=candidates,
        stream=True,
        stream_callback=capture,
        publish_events=False,
        record_usage=False,
        record_latency=False,
        timeout=10.0,
    )
    assert response["content"] == "plugin:demo-model"
    assert response["model"] == "demo-model"
    assert [event["type"] for event in stream_events] == [
        "reply_start",
        "reply_delta",
        "reply_done",
    ]
    await manager.close()


def test_plugin_manifest_rejects_package_escape(tmp_path):
    package = tmp_path / "plugin"
    package.mkdir()
    package.joinpath("plugin.json").write_text(
        json.dumps({
            "id": "com.example.escape",
            "backend": {"type": "python", "entry": "../outside.py"},
        }),
        encoding="utf-8",
    )
    tmp_path.joinpath("outside.py").write_text("pass", encoding="utf-8")

    from cyrene.plugins.manifest import PluginManifestError, load_manifest

    with pytest.raises(PluginManifestError, match="inside the plugin package"):
        load_manifest(package)


@pytest.mark.asyncio
async def test_project_plugin_http_surface_enforces_project_toggle(tmp_path):
    source = _write_plugin(tmp_path / "source")
    manager = PluginManager(tmp_path / "runtime")
    app = FastAPI()
    router = APIRouter()
    register_plugin_routes(router, manager)
    app.include_router(router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        installed = await client.post(
            "/api/plugins/install", json={"path": str(source)}
        )
        assert installed.status_code == 200

        disabled_asset = await client.get(
            "/api/plugins/com.example.demo/projects/project-3/assets/ui/index.html"
        )
        assert disabled_asset.status_code == 404

        enabled = await client.post(
            "/api/plugins/com.example.demo/enabled",
            json={"projectId": "project-3", "enabled": True},
        )
        assert enabled.json()["enabled"] is True

        contributions = await client.get(
            "/api/plugins/contributions",
            params={"project_id": "project-3", "point": "cyrene.projectTool"},
        )
        assert [item["id"] for item in contributions.json()["contributions"]] == [
            "demo-tool"
        ]
        asset = await client.get(
            "/api/plugins/com.example.demo/projects/project-3/assets/ui/index.html"
        )
        assert asset.status_code == 200
        assert asset.text == "<h1>Demo</h1>"
        assert "connect-src 'none'" in asset.headers["content-security-policy"]

        disabled = await client.post(
            "/api/plugins/com.example.demo/enabled",
            json={"projectId": "project-3", "enabled": False},
        )
        assert disabled.json()["enabled"] is False
        hidden = await client.get(
            "/api/plugins/contributions", params={"project_id": "project-3"}
        )
        assert hidden.json()["contributions"] == []

    await manager.close()
