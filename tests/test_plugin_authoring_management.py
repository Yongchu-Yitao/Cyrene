from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.plugin import Plugin, PluginContext, PluginPack, PluginRegistry


class _FakeHost:
    def __init__(self, plugin_directory: Path) -> None:
        self.plugin_directory = plugin_directory
        self.db_path = str(plugin_directory / "state.sqlite3")
        self.reloads = 0

    async def reload_user_plugins(self):
        self.reloads += 1
        return SimpleNamespace(created=(), updated=()), ()


@pytest.mark.asyncio
async def test_plugin_source_manager_requires_user_review_for_seeded_source(
    tmp_path,
    monkeypatch,
) -> None:
    from agent.plugin.plugin_impl.cyrene_plugin_development import tools

    plugin_root = tmp_path / "plugin_impl"
    source = plugin_root / "cyrene_seeded" / "tool.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    (plugin_root / ".upstream-hashes.json").write_text(
        json.dumps({"version": 1, "files": {"cyrene_seeded/tool.py": "old"}}),
        encoding="utf-8",
    )
    host = _FakeHost(plugin_root)
    monkeypatch.setattr(tools, "active_plugin_application_host", lambda: host)
    context = PluginContext(workspace=tmp_path, data={"language": "zh"})

    inspected = json.loads(await tools.manage_plugin_source(
        {"action": "read", "path": "cyrene_seeded/tool.py"},
        context,
    ))
    blocked = json.loads(await tools.manage_plugin_source(
        {
            "action": "write",
            "path": "cyrene_seeded/tool.py",
            "expected_sha256": inspected["sha256"],
            "content": "value = 2\n",
        },
        context,
    ))

    assert inspected["system"] is True
    assert blocked["code"] == "user_confirmation_required"
    assert blocked["requires_user_review"] is True
    assert source.read_text(encoding="utf-8") == "value = 1\n"
    assert host.reloads == 0


@pytest.mark.asyncio
async def test_plugin_source_manager_updates_user_source_with_revision_guard(
    tmp_path,
    monkeypatch,
) -> None:
    from agent.plugin.plugin_impl.cyrene_plugin_development import tools

    plugin_root = tmp_path / "plugin_impl"
    source = plugin_root / "my_plugin" / "tool.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    host = _FakeHost(plugin_root)
    monkeypatch.setattr(tools, "active_plugin_application_host", lambda: host)
    context = PluginContext(workspace=tmp_path)
    inspected = json.loads(await tools.manage_plugin_source(
        {"action": "read", "path": "my_plugin/tool.py"}, context
    ))

    changed = json.loads(await tools.manage_plugin_source(
        {
            "action": "write",
            "path": "my_plugin/tool.py",
            "expected_sha256": inspected["sha256"],
            "content": "value = 2\n",
        },
        context,
    ))

    assert changed["ok"] is True
    assert changed["system"] is False
    assert source.read_text(encoding="utf-8") == "value = 2\n"
    assert host.reloads == 1


@pytest.mark.asyncio
async def test_hook_manager_exposes_user_mutation_and_blocks_unconfirmed_system_change(
    tmp_path,
    monkeypatch,
) -> None:
    from agent.plugin.plugin_impl.cyrene_plugin_development import tools

    class Hooks:
        values: dict[str, dict] = {}

        def list(self):
            return list(self.values.values())

        def get(self, hook_id):
            return self.values.get(hook_id)

        def save(self, value, *, actor="user"):
            hook = {
                "id": str(value.get("id") or "created-hook"),
                "name": str(value.get("name") or "Created Hook"),
                "description": "",
                "event": str(value["event"]),
                "matcher": str(value.get("matcher") or "*"),
                "enabled": bool(value.get("enabled", True)),
                "priority": 100,
                "failure_policy": "open",
                "timeout_seconds": 10,
                "runner": dict(value["runner"]),
                "created_at": "2026-08-28T00:00:00+00:00",
                "updated_at": "2026-08-28T00:00:00+00:00",
            }
            self.values[hook["id"]] = hook
            return hook

        def delete(self, hook_id, *, actor="user"):
            return self.values.pop(hook_id, None) is not None

        def set_enabled(self, hook_id, enabled, *, actor="user"):
            self.values[hook_id]["enabled"] = enabled
            return self.values[hook_id]

    host = _FakeHost(tmp_path / "plugin_impl")
    monkeypatch.setattr(tools, "active_plugin_application_host", lambda: host)
    current_system_hook = {
        "id": "core-permission-review",
        "event": "PreToolUse",
        "plugin_id": "core.permission",
        "enabled": True,
        "root_only": False,
        "matcher": "*",
        "failure_policy": "open",
        "config": {},
        "created_at": "2026-08-28T00:00:00+00:00",
        "action": {"type": "plugin"},
    }
    from agent.workbench import hook_listing as hook_listing_module
    monkeypatch.setattr(
        hook_listing_module,
        "runtime_hook_listing",
        lambda _db: [dict(current_system_hook)],
    )
    applied = []
    monkeypatch.setattr(
        hook_listing_module,
        "update_runtime_hook",
        lambda _db, hook_id, payload: applied.append((hook_id, payload)) or {
            "ok": True,
            "hook": {**current_system_hook, "event": payload.get("new_event", current_system_hook["event"])},
        },
    )
    hooks = Hooks()
    context = PluginContext(
        workspace=tmp_path,
        services={"cli": SimpleNamespace(hooks=hooks)},
    )
    created = json.loads(await tools.manage_hooks(
        {
            "action": "create",
            "scope": "user",
            "hook": {
                "name": "Run formatter",
                "event": "PostToolUse",
                "runner": {"type": "command", "executable": "/usr/bin/true", "args": []},
            },
        },
        context,
    ))
    blocked = json.loads(await tools.manage_hooks(
        {
            "action": "update", "scope": "system",
            "hook_id": "core-permission-review",
            "event": "PreToolUse", "plugin_id": "core.permission",
            "hook": {"new_event": "PostToolUse"},
        },
        context,
    ))
    disabled = json.loads(await tools.manage_hooks(
        {"action": "disable", "scope": "user", "hook_id": "created-hook"},
        context,
    ))
    confirmed = json.loads(await tools.manage_hooks(
        {
            "action": "update", "scope": "system",
            "hook_id": "core-permission-review",
            "event": "PreToolUse", "plugin_id": "core.permission",
            "hook": {"new_event": "PostToolUse"},
            "user_confirmed": True,
            "confirmation_token": blocked["confirmation_token"],
        },
        context,
    ))
    second_preview = json.loads(await tools.manage_hooks(
        {
            "action": "update", "scope": "system",
            "hook_id": "core-permission-review",
            "event": "PreToolUse", "plugin_id": "core.permission",
            "hook": {"new_event": "PostToolUse"},
        },
        context,
    ))
    mismatched = json.loads(await tools.manage_hooks(
        {
            "action": "update", "scope": "system",
            "hook_id": "core-permission-review",
            "event": "PreToolUse", "plugin_id": "core.permission",
            "hook": {"new_event": "TurnStart"},
            "user_confirmed": True,
            "confirmation_token": second_preview["confirmation_token"],
        },
        context,
    ))

    assert created["ok"] is True
    assert created["hook"]["event"] == "PostToolUse"
    assert blocked["code"] == "user_confirmation_required"
    assert blocked["target_kind"] == "system_hook"
    assert blocked["preview"]["current"]["event"] == "PreToolUse"
    assert blocked["preview"]["proposed"]["event"] == "PostToolUse"
    assert "-  \"event\": \"PreToolUse\"" in blocked["preview"]["diff"]
    assert disabled["hook"]["enabled"] is False
    assert confirmed["ok"] is True
    assert applied[0][1]["new_event"] == "PostToolUse"
    assert mismatched["code"] == "system_hook_confirmation_invalid"
    assert len(applied) == 1


@pytest.mark.asyncio
async def test_hook_manager_reuses_background_generation_service(
    tmp_path,
    monkeypatch,
) -> None:
    from agent.plugin.plugin_impl.cyrene_plugin_development import tools

    host = _FakeHost(tmp_path / "plugin_impl")
    monkeypatch.setattr(tools, "active_plugin_application_host", lambda: host)

    class HookGenerationService:
        hooks = SimpleNamespace(list=lambda: [])

        def request_hook_generation(self, payload):
            return {"ok": True, "status": "configuring", "hook": dict(payload)}

    result = json.loads(await tools.manage_hooks(
        {
            "action": "generate",
            "scope": "user",
            "hook": {
                "name": "Record failures",
                "event": "PostToolUse",
                "action_instruction": "Write failures to a local log.",
            },
        },
        PluginContext(
            workspace=tmp_path,
            services={"cli": HookGenerationService()},
        ),
    ))

    assert result["ok"] is True
    assert result["status"] == "configuring"
    assert result["hook"]["action_instruction"] == "Write failures to a local log."


@pytest.mark.asyncio
async def test_plugin_manager_lists_switches_and_deletes_installed_packs(
    tmp_path,
    monkeypatch,
) -> None:
    from agent.plugin.plugin_impl.cyrene_plugin_development import tools
    from cyrene.runtime import settings_store

    async def run(_arguments, _context):
        return {"ok": True}

    plugin_root = tmp_path / "plugin_impl"
    pack_root = plugin_root / "user_pack"
    pack_root.mkdir(parents=True)
    (pack_root / "__init__.py").write_text("# managed pack\n", encoding="utf-8")
    member = Plugin(
        name="UserPackTool",
        description="tool",
        input_schema={"type": "object"},
        handler=run,
    )
    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(id="user_pack", description="pack", plugins=(member,)),
        source=str(pack_root),
    )

    class Host:
        def __init__(self):
            self.plugin_directory = plugin_root
            self.db_path = str(tmp_path / "state.sqlite3")
            self.registry = registry
            self.restart_required_packs = ()
            self.reconciles = 0

        async def reconcile_activation(self):
            self.reconciles += 1

        async def reload_user_plugins(self):
            return SimpleNamespace(created=(), updated=()), ()

    host = Host()
    monkeypatch.setattr(tools, "active_plugin_application_host", lambda: host)
    monkeypatch.setattr(settings_store, "save_enabled_plugins", lambda _value: None)
    monkeypatch.setattr(settings_store, "save_enabled_plugin_packs", lambda _value: None)
    context = PluginContext(workspace=tmp_path)

    listing = json.loads(await tools.manage_plugins({"action": "list"}, context))
    disabled = json.loads(await tools.manage_plugins(
        {"action": "disable", "kind": "pack", "id": "user_pack"}, context
    ))
    deleted = json.loads(await tools.manage_plugins(
        {"action": "delete", "kind": "pack", "id": "user_pack"}, context
    ))

    assert listing["packs"][0]["id"] == "user_pack"
    assert disabled["ok"] is True
    assert registry.pack_enabled("user_pack") is False
    assert host.reconciles == 1
    assert deleted["ok"] is True
    assert not pack_root.exists()


def test_plugin_development_pack_exposes_reviewed_source_and_hook_managers() -> None:
    from agent.plugin.plugin_impl.cyrene_plugin_development import plugin_pack

    by_name = {plugin.name: plugin for plugin in plugin_pack.plugins}
    assert {"PluginManager", "PluginSourceManager", "HookManager"} <= set(by_name)
    assert by_name["PluginManager"].main_only is True
    assert by_name["PluginSourceManager"].metadata["read_only"] is False
    assert by_name["PluginSourceManager"].main_only is True
    assert by_name["HookManager"].metadata["read_only"] is False
    assert by_name["HookManager"].main_only is True
