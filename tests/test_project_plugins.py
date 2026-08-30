from __future__ import annotations

from types import SimpleNamespace

from cyrene.core.plugin import PluginPack
from cyrene.plugins.builtin.cyrene_project_javascript import (
    plugin_pack as javascript_pack,
)
from cyrene.plugins.builtin.cyrene_project_python import plugin_pack as python_pack
from cyrene.plugins.contributions import (
    WORKSPACE_PROJECT_TYPE,
    validate_workbench_contributions,
    workspace_project_types,
)


class FakeActivation:
    def __init__(self, registry):
        self.registry = registry

    def snapshot(self):
        return SimpleNamespace(plugins={}, packs=dict(self.registry.enabled))


class FakeRegistry:
    def __init__(self, packs=(), enabled=None, refresh_pack: PluginPack | None = None):
        self.packs = {pack.id: pack for pack in packs}
        self.enabled = {pack_id: True for pack_id in self.packs}
        self.enabled.update(enabled or {})
        self.refresh_pack = refresh_pack
        self.activation = FakeActivation(self)
        self.refreshes = 0

    def list_packs(self):
        return tuple(self.packs.values())

    def pack_enabled(self, pack_id):
        if pack_id not in self.packs:
            raise KeyError(pack_id)
        return self.enabled.get(pack_id, True)

    def set_pack_enabled(self, pack_id, enabled):
        if pack_id not in self.packs:
            raise KeyError(pack_id)
        self.enabled[pack_id] = bool(enabled)

    def refresh_directory(self, _directory):
        self.refreshes += 1
        if self.refresh_pack is not None:
            self.packs[self.refresh_pack.id] = self.refresh_pack
            self.enabled.setdefault(self.refresh_pack.id, True)
        return ()


def test_project_types_are_independent_validated_plugin_contributions():
    for pack in (python_pack, javascript_pack):
        validate_workbench_contributions(pack)
        project_types = workspace_project_types(pack)
        assert len(project_types) == 1
        assert pack.extensions.values(WORKSPACE_PROJECT_TYPE) == project_types
        assert project_types[0].runtime_extensions
        assert pack.plugins == ()
        assert pack.metadata["default_enabled"] is False


def test_extension_dependency_installs_and_enables_missing_project_plugin(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions import project_plugin_dependencies as links

    registry = FakeRegistry(refresh_pack=python_pack)
    host = SimpleNamespace(
        registry=registry,
        plugin_directory=tmp_path / "plugins",
        load_failures=(),
    )
    restored = []
    saved = {}
    monkeypatch.setattr(links, "application_plugin_scope", lambda: host)
    monkeypatch.setattr(links, "restore_builtin_plugin", lambda directory, pack_id: restored.append((directory, pack_id)))
    monkeypatch.setattr(links.settings_store, "get", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr(links.settings_store, "set_", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(links.settings_store, "save_enabled_plugins", lambda value: saved.__setitem__("plugins", value))
    monkeypatch.setattr(links.settings_store, "save_enabled_plugin_packs", lambda value: saved.__setitem__("packs", value))

    statuses = links.ensure_project_plugins("toolchain", "python", force_enable=False)

    assert restored == [(host.plugin_directory, "cyrene_project_python")]
    assert registry.refreshes == 1
    assert statuses == [{
        "packId": "cyrene_project_python",
        "installed": True,
        "installedNow": True,
        "enabled": True,
        "enabledNow": False,
    }]
    assert saved["extension_project_plugin_links"] == {
        "toolchain:python": ["cyrene_project_python"]
    }


def test_passive_reconcile_preserves_manual_disable_but_explicit_enable_restores_it(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions import project_plugin_dependencies as links

    registry = FakeRegistry(
        packs=(javascript_pack,),
        enabled={javascript_pack.id: False},
    )
    host = SimpleNamespace(registry=registry, plugin_directory=tmp_path, load_failures=())
    saved = {
        "extension_project_plugin_links": {
            "toolchain:node": [javascript_pack.id],
        }
    }
    monkeypatch.setattr(links, "application_plugin_scope", lambda: host)
    monkeypatch.setattr(links.settings_store, "get", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr(links.settings_store, "set_", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(links.settings_store, "save_enabled_plugins", lambda value: saved.__setitem__("plugins", value))
    monkeypatch.setattr(links.settings_store, "save_enabled_plugin_packs", lambda value: saved.__setitem__("packs", value))

    passive = links.ensure_project_plugins("toolchain", "node", force_enable=False)
    assert passive[0]["enabled"] is False

    explicit = links.ensure_project_plugins("toolchain", "node", force_enable=True)
    assert explicit[0]["enabled"] is True
    assert explicit[0]["enabledNow"] is True
    assert saved["packs"][javascript_pack.id] is True
