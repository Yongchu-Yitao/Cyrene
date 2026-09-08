"""Cross-entry contracts for plugin management and source identity."""
import json
import os

import pytest
from fastapi import FastAPI

from cyrene.core.plugin import Plugin, PluginContext, PluginPack, PluginRegistry
from cyrene.core.plugin.registry import _python_source_signature
from cyrene.core.plugin.session_plugins import SessionPlugins
from cyrene.platform import config_store, settings_store
from cyrene.plugins.application import PluginApplicationHost
from cyrene.plugins.builtin.cyrene_plugin_development import tools
from cyrene.plugins.management import delete_source
from cyrene.workbench.http.plugins import plugin_registry_status
from cyrene.workbench.http.settings.plugin_service import (
    PluginSettingsApplicationService, get_plugin_settings,
)

pytestmark = pytest.mark.usefixtures("isolated_plugin_settings")


@pytest.fixture
def host(tmp_path, monkeypatch):
    registry = PluginRegistry(include_core=False)
    for name in ("first", "second"):
        registry.register_pack(PluginPack(
            id=name, description=name,
            plugins=(Plugin(name=name + "Tool", description=name,
                            input_schema={"type": "object"}, handler=lambda a, c: "ok"),),
        ), source="test-" + name)
    value = PluginApplicationHost(
        app=FastAPI(), registry=registry, bot=None, db_path=str(tmp_path / "state.db"),
        data_directory=tmp_path / "data", plugin_directory=tmp_path / "installed",
    )
    monkeypatch.setattr(tools, "application_plugin_scope", lambda: value)
    return value


@pytest.mark.asyncio
async def test_agent_and_settings_merge_updates_and_notify_once(host, monkeypatch):
    from cyrene.observability import debug

    events = []
    async def publish(event):
        events.append(event)
    monkeypatch.setattr(debug, "publish_event", publish)
    ctx = PluginContext()
    result = json.loads(await tools.manage_plugins(
        {"action": "disable", "kind": "pack", "id": "first"}, ctx))
    assert result["ok"], result
    revision = config_store.get_settings_revision()
    from cyrene.platform.settings_service import publish_settings_changed
    service = PluginSettingsApplicationService(host.registry, publish_settings_changed)
    response = await service.update_activation({"packs": {"second": False}, "expected_revision": revision})
    assert response["ok"]
    assert settings_store.get_enabled_plugin_packs() == {"first": False, "second": False}
    assert not host.registry.pack_enabled("first")
    assert not host.registry.pack_enabled("second")
    assert len([e for e in events if e["type"] == "settings_changed"]) == 2
    conflict = await service.update_activation({"packs": {"first": True}, "expected_revision": revision})
    assert conflict.status_code == 409
    assert not host.registry.pack_enabled("first")


@pytest.mark.asyncio
async def test_failed_persistence_does_not_change_registry(host, monkeypatch):
    def fail(_config):
        raise OSError("disk unavailable")
    monkeypatch.setattr(config_store, "_persist", fail)
    result = json.loads(await tools.manage_plugins(
        {"action": "disable", "kind": "pack", "id": "first"}, PluginContext()))
    assert not result["ok"]
    assert host.registry.pack_enabled("first")
    assert settings_store.get_enabled_plugin_packs() == {}


@pytest.mark.asyncio
async def test_pack_switch_is_consistent_when_all_member_tools_are_disabled(host):
    host.registry.set_plugin_enabled("firstTool", False)
    agent = json.loads(await tools.manage_plugins({"action": "list"}, PluginContext()))
    values = (agent, get_plugin_settings(host.registry), plugin_registry_status(host))
    for value in values:
        pack = next(p for p in value["packs"] if p["id"] == "first")
        assert pack["effective_enabled"] is True
        assert pack["enabled_count"] == 0
    assert next(p for p in agent["plugins"] if p["name"] == "firstTool")["enabled"] is False


def test_all_lifecycles_use_content_identity(tmp_path):
    source = tmp_path / "sample"
    source.mkdir()
    path = source / "__init__.py"
    path.write_text("value = 1\n")
    pack = PluginPack(id="sample", description="sample", plugins=(), setup=lambda context: None)
    def fingerprints():
        return (_python_source_signature(source),
                PluginApplicationHost._source_generation(str(source)),
                SessionPlugins._pack_setup_fingerprint(pack, str(source)))
    before = fingerprints()
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    assert fingerprints() == before
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "ignored.py").write_text("ignored = True")
    assert fingerprints() == before
    path.write_text("value = 2\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    after = fingerprints()
    assert all(old != new for old, new in zip(before, after))


@pytest.mark.asyncio
async def test_shared_delete_rejects_outside_symlink(host, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.py").write_text("keep = True")
    host.plugin_directory.mkdir(exist_ok=True)
    link = host.plugin_directory / "escape"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="managed"):
        await delete_source(host, link)
    assert (outside / "keep.py").exists()
