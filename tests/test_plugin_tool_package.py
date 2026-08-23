import json
from unittest.mock import AsyncMock

import pytest


def test_plugin_pack_is_progressive_complete_and_main_only():
    from cyrene.tooling import catalog
    from cyrene.tooling.guidance import pack_guidance

    capabilities = catalog.capabilities_for_pack("plugin_tools", actor="main")
    assert {item.capability_id for item in capabilities} == {
        "plugin.authoring.guide",
        "plugin.scaffold",
        "plugin.validate",
        "plugin.list",
        "plugin.install",
        "plugin.enable",
        "plugin.disable",
        "plugin.reload",
        "plugin.contributions",
        "plugin.call",
        "plugin.logs",
        "plugin.delete",
    }
    assert catalog.capabilities_for_pack("plugin_tools", actor="subagent") == []
    guidance = pack_guidance("plugin_tools")
    assert "plugin.authoring.guide" in guidance
    assert "unified review" in guidance
    assert "owns all models, runtimes" in guidance


@pytest.mark.asyncio
async def test_guide_and_scaffold_supply_a_valid_editable_package(tmp_path):
    from cyrene.agent.context import bind_run_context
    from cyrene.tool_impl import plugins as plugin_tools

    guide = json.loads(await plugin_tools._guide({}))
    assert guide["ok"] is True
    assert "cyrene.chatProvider" in guide["guide"]
    assert "cyrene.ocrProvider" in guide["guide"]
    assert "central review Agent" in guide["guide"]

    with bind_run_context(workspace_dir=tmp_path, temporary_full_access=False):
        scaffold = json.loads(await plugin_tools._scaffold({
            "path": "plugins/usage",
            "plugin_id": "com.example.usage",
            "name": "Usage",
        }))
        validated = json.loads(await plugin_tools._validate({"path": "plugins/usage"}))

    assert scaffold["ok"] is True
    assert scaffold["validation"]["installable"] is True
    assert validated["ok"] is True
    assert (tmp_path / "plugins/usage/plugin.json").is_file()
    assert (tmp_path / "plugins/usage/plugin.py").is_file()
    assert (tmp_path / "plugins/usage/ui/index.html").is_file()
    assert "cyrene.projectTool" in (tmp_path / "plugins/usage/plugin.py").read_text()


def test_validation_rejects_broken_python_and_static_view_reference(tmp_path):
    from cyrene.tool_impl.plugins import _validate_path

    root = tmp_path / "broken"
    (root / "ui").mkdir(parents=True)
    (root / "ui/index.html").write_text("ok")
    (root / "plugin.py").write_text("def broken(:\n")
    (root / "plugin.json").write_text(json.dumps({
        "apiVersion": 1,
        "id": "broken.plugin",
        "backend": {"type": "python", "entry": "plugin.py"},
        "frontend": {"mode": "iframe", "entry": "ui/index.html"},
        "contributes": [{
            "point": "cyrene.projectTool",
            "id": "tool",
            "view": "missing",
        }],
    }))

    result = _validate_path(root)

    assert result["ok"] is False
    assert any("backend syntax/read error" in error for error in result["errors"])
    assert any("references missing static cyrene.view" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_mutating_lifecycle_and_rpc_share_central_plugin_review(monkeypatch):
    from cyrene.tool_impl import plugins as plugin_tools

    review = AsyncMock(return_value="reviewed")
    monkeypatch.setattr(plugin_tools, "request_scope_elevation", review)

    assert await plugin_tools._review("install", "example", {"path": "example"}) == "reviewed"
    call = review.await_args.kwargs
    assert call["permission_kind"] == "plugin_change"
    assert call["path_hint"].startswith("plugin:example:")
    assert "install" in call["operation"]


@pytest.mark.asyncio
async def test_plugin_review_uses_auto_reviewer_without_pending_question(monkeypatch):
    from cyrene.agent import auto_review
    from cyrene.agent.context import bind_run_context
    from cyrene.tool_impl import plugins as plugin_tools

    review = AsyncMock(return_value=(True, "bounded plugin operation is appropriate"))
    monkeypatch.setattr(auto_review, "review_elevation", review)

    with bind_run_context(
        agent_id="main",
        round_id="round-plugin-review",
        permission_mode="auto",
    ):
        result = await plugin_tools._review(
            "enable",
            "example.plugin@project-a",
            {"plugin_id": "example.plugin", "project_id": "project-a"},
        )

    assert result is None
    assert review.await_count == 1
    assert review.await_args.kwargs["tool_name"] == "PluginDeveloper"
    assert review.await_args.kwargs["path_hint"].startswith("plugin:example.plugin@project-a:")


@pytest.mark.asyncio
async def test_install_enable_call_logs_and_delete_form_a_closed_loop(tmp_path, monkeypatch):
    from cyrene.agent.context import bind_run_context
    from cyrene.plugins.manager import PluginManager
    from cyrene.tool_impl import plugins as plugin_tools

    manager = PluginManager(tmp_path / "installed")
    monkeypatch.setattr(plugin_tools, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(plugin_tools, "_review", AsyncMock(return_value=None))

    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.json").write_text(json.dumps({
        "apiVersion": 1,
        "id": "loop.plugin",
        "backend": {"type": "python", "entry": "plugin.py"},
    }))
    (source / "plugin.py").write_text(
        "def activate(context):\n"
        "    context.register_method('ping', lambda value: {'pong': value})\n"
        "    context.register('cyrene.command', {'id': 'ping', 'title': 'Ping'})\n"
    )

    with bind_run_context(workspace_dir=tmp_path):
        installed = json.loads(await plugin_tools._install({"path": "source"}))
    enabled = json.loads(await plugin_tools._enable({"plugin_id": "loop.plugin", "project_id": "project-a"}))
    contributions = json.loads(await plugin_tools._contributions({"project_id": "project-a"}))
    called = json.loads(await plugin_tools._call({
        "plugin_id": "loop.plugin",
        "project_id": "project-a",
        "method": "ping",
        "arguments": {"value": 7},
    }))
    logs = json.loads(await plugin_tools._logs({"plugin_id": "loop.plugin", "project_id": "project-a"}))
    disabled = json.loads(await plugin_tools._disable({"plugin_id": "loop.plugin", "project_id": "project-a"}))
    deleted = json.loads(await plugin_tools._delete({"plugin_id": "loop.plugin"}))

    assert installed["ok"] is True
    assert enabled["enabled"] is True
    assert contributions["contributions"][0]["point"] == "cyrene.command"
    assert called["result"] == {"pong": {"value": 7}}
    assert isinstance(logs["lines"], list)
    assert disabled["enabled"] is False
    assert deleted == {"ok": True, "pluginId": "loop.plugin", "dataDeleted": False}
    await manager.close()
