"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import json
import sys
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime
from cyrene.plugins import native_tools
from cyrene.plugins.native_tools import (
    CORE_PLUGIN_NAMES,
    USER_STANDALONE_PLUGIN_NAMES,
    mark_builtin_plugin_deleted,
    seed_builtin_plugin_directory,
)


TOOL_PACK_IDS = frozenset(
    {
        "cyrene_application",
        "cyrene_browser",
        "cyrene_code",
        "cyrene_cli",
        "cyrene_content",
        "cyrene_control",
        "cyrene_delivery",
        "cyrene_desktop",
        "cyrene_entity",
        "cyrene_extensions",
        "cyrene_image",
        "cyrene_knowledge",
        "cyrene_map",
        "cyrene_media",
        "cyrene_memory",
        "cyrene_office",
        "cyrene_remote",
        "cyrene_renderer",
        "cyrene_schedule",
        "cyrene_skills",
        "cyrene_subagent",
        "cyrene_task",
    }
)
MODEL_VISIBLE_PACK_IDS = TOOL_PACK_IDS - {"cyrene_image"}
CONTEXT_PACK_IDS = frozenset({
    "cyrene_composer_context",
    "cyrene_context",
    "cyrene_soul",
    "cyrene_system_prompt",
})


def _run(coroutine):
    return asyncio.run(coroutine)


def _sha(content: bytes) -> str:
    return sha256(content).hexdigest()


def test_seeded_canonical_plugins_complete_toolbox_chain(tmp_path):
    async def scenario():
        plugin_directory = tmp_path / "plugin_impl"

        seeded = seed_builtin_plugin_directory(plugin_directory)

        assert seeded.directory == plugin_directory.resolve()
        assert seeded.manifest == plugin_directory / ".upstream-hashes.json"
        assert seeded.manifest.is_file()
        assert not (plugin_directory / "__init__.py").exists()
        assert not tuple(plugin_directory.glob(".*.cyrene-seed-*"))

        for source_path in plugin_directory.rglob("*.py"):
            source_text = source_path.read_text(encoding="utf-8")
            assert "invoke_builtin_tool" not in source_text
            assert "from cyrene.tool_impl" not in source_text
            assert "import cyrene.tool_impl" not in source_text

        registry = PluginRegistry()
        assert registry.load_directory(plugin_directory) == ()

        user_packs = {
            pack.id: pack
            for pack in registry.list_packs()
            if registry.pack_source(pack.id) != "core"
        }
        assert TOOL_PACK_IDS <= set(user_packs)
        assert CONTEXT_PACK_IDS <= set(user_packs)
        assert registry.pack_locked("cyrene_context") is True
        assert registry.pack_locked("cyrene_composer_context") is True
        assert registry.pack_locked("cyrene_system_prompt") is True
        business_names = {
            plugin.name
            for pack_id in TOOL_PACK_IDS
            for plugin in user_packs[pack_id].plugins
        }
        assert all(
            plugin.kind == "tool"
            for pack_id in TOOL_PACK_IDS
            for plugin in user_packs[pack_id].plugins
        )
        assert all(
            plugin.kind == ("model" if pack_id == "cyrene_model" else "tool")
            for pack_id, pack in user_packs.items()
            if pack_id not in CONTEXT_PACK_IDS
            for plugin in pack.plugins
        )
        assert not business_names & CORE_PLUGIN_NAMES
        assert not business_names & USER_STANDALONE_PLUGIN_NAMES

        standalone_names = {
            item.plugin.name
            for item in registry.list_plugins()
            if item.pack_id is None
            and item.plugin.kind == "tool"
            and item.source != "core"
        }
        assert standalone_names == USER_STANDALONE_PLUGIN_NAMES
        plugin_entity_names = {
            "entity.track",
            "entity.update",
            "entity.list",
            "entity.query",
            "entity.delete",
        }
        registered_tool_names = (
            business_names | standalone_names | CORE_PLUGIN_NAMES
        )
        assert plugin_entity_names <= registered_tool_names

        for name in business_names | standalone_names:
            registered = registry.registered(name)
            assert Path(registered.source).is_relative_to(plugin_directory)

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "seeded.txt").write_text("seeded", encoding="utf-8")
        context = PluginContext(
            workspace=workspace,
            data={
                "bot": None,
                "chat_id": 0,
                "db_path": "",
                "notify_state": None,
                "run_context": {
                    "workspace_dir": workspace,
                    "workspace_enabled": True,
                    "permission_mode": "full_access",
                },
            },
        )
        runtime = PluginRuntime(registry)

        listing = await runtime.call("toolbox", {"operation": "list"}, context)
        assert listing.success is True
        assert set(listing.value["packs"]) == MODEL_VISIBLE_PACK_IDS | {
            "cyrene_plugin_development"
        }
        assert set(listing.value["standalone_tools"]) == USER_STANDALONE_PLUGIN_NAMES

        globbed = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "Glob",
                "arguments": {"pattern": "*.txt"},
            },
            context,
        )
        assert globbed.success is True
        assert globbed.value["result"] == "seeded.txt"

    _run(scenario())


def test_hash_manifest_updates_defaults_without_overwriting_user_edits(
    tmp_path,
    monkeypatch,
):
    plugin_directory = tmp_path / "plugin_impl"
    first = MappingProxyType(
        {
            "cyrene_application/__init__.py": b"pack-v1\n",
            "cyrene_application/tool.py": b"tool-v1\n",
            "cyrene_application/helper.py": b"helper-v1\n",
            "edit.py": b"edit-v1\n",
        }
    )
    current = {"value": first}
    monkeypatch.setattr(
        native_tools,
        "_collect_canonical_files",
        lambda: current["value"],
    )

    initial = seed_builtin_plugin_directory(plugin_directory)
    pack_init = plugin_directory / "cyrene_application" / "__init__.py"
    tool = plugin_directory / "cyrene_application" / "tool.py"
    helper = plugin_directory / "cyrene_application" / "helper.py"
    edit = plugin_directory / "edit.py"
    assert set(initial.created) == {pack_init, tool, helper, edit}
    assert initial.updated == ()
    assert initial.existing == ()

    tool.write_bytes(b"user-tool\n")
    edit.write_bytes(b"user-edit\n")
    helper.unlink()
    current["value"] = MappingProxyType(
        {
            "cyrene_application/__init__.py": b"pack-v2\n",
            "cyrene_application/tool.py": b"tool-v2\n",
            "cyrene_application/helper.py": b"helper-v2\n",
            "edit.py": b"edit-v2\n",
            "grep.py": b"grep-v1\n",
        }
    )
    upgraded = seed_builtin_plugin_directory(plugin_directory)
    grep = plugin_directory / "grep.py"

    assert set(upgraded.created) == {helper, grep}
    assert upgraded.updated == (pack_init,)
    assert set(upgraded.existing) == {tool, edit}
    assert pack_init.read_bytes() == b"pack-v2\n"
    assert tool.read_bytes() == b"user-tool\n"
    assert helper.read_bytes() == b"helper-v2\n"
    assert edit.read_bytes() == b"user-edit\n"

    manifest = json.loads(upgraded.manifest.read_text(encoding="utf-8"))
    assert manifest == {
        "version": 1,
        "files": {
            "cyrene_application/__init__.py": _sha(b"pack-v2\n"),
            "cyrene_application/helper.py": _sha(b"helper-v2\n"),
            "cyrene_application/tool.py": _sha(b"tool-v1\n"),
            "edit.py": _sha(b"edit-v1\n"),
            "grep.py": _sha(b"grep-v1\n"),
        },
    }

    tool.write_bytes(b"tool-v1\n")
    current["value"] = MappingProxyType(
        {
            **dict(current["value"]),
            "cyrene_application/tool.py": b"tool-v3\n",
        }
    )
    reverted = seed_builtin_plugin_directory(plugin_directory)

    assert reverted.updated == (tool,)
    assert tool.read_bytes() == b"tool-v3\n"


def test_hash_manifest_retires_only_unmodified_obsolete_defaults(tmp_path, monkeypatch):
    plugin_directory = tmp_path / "plugin_impl"
    current = {
        "value": MappingProxyType(
            {
                "cyrene_entity/__init__.py": b"pack-v1\n",
                "cyrene_entity/_runtime.py": b"runtime-v1\n",
                "cyrene_entity/store.py": b"store-v1\n",
                "old.py": b"old-v1\n",
            }
        )
    }
    monkeypatch.setattr(
        native_tools,
        "_collect_canonical_files",
        lambda: current["value"],
    )
    seed_builtin_plugin_directory(plugin_directory)

    runtime = plugin_directory / "cyrene_entity" / "_runtime.py"
    store = plugin_directory / "cyrene_entity" / "store.py"
    old = plugin_directory / "old.py"
    old.write_bytes(b"user-old\n")
    current["value"] = MappingProxyType(
        {"cyrene_entity/__init__.py": b"pack-v2\n"}
    )

    upgraded = seed_builtin_plugin_directory(plugin_directory)

    assert set(upgraded.removed) == {runtime, store}
    assert runtime.exists() is False
    assert store.exists() is False
    assert old.read_bytes() == b"user-old\n"
    manifest = json.loads(upgraded.manifest.read_text(encoding="utf-8"))
    assert manifest["files"] == {
        "cyrene_entity/__init__.py": _sha(b"pack-v2\n"),
        "old.py": _sha(b"old-v1\n"),
    }

    edited_directory = tmp_path / "edited_plugin_impl"
    current["value"] = MappingProxyType(
        {
            "cyrene_entity/__init__.py": b"pack-v1\n",
            "cyrene_entity/tool.py": b"tool-v1\n",
            "cyrene_entity/store.py": b"store-v1\n",
        }
    )
    seed_builtin_plugin_directory(edited_directory)
    edited_tool = edited_directory / "cyrene_entity" / "tool.py"
    edited_store = edited_directory / "cyrene_entity" / "store.py"
    edited_tool.write_bytes(b"user-tool\n")
    current["value"] = MappingProxyType(
        {
            "cyrene_entity/__init__.py": b"pack-v2\n",
            "cyrene_entity/tool.py": b"tool-v2\n",
        }
    )

    preserved = seed_builtin_plugin_directory(edited_directory)

    assert edited_store in preserved.existing
    assert edited_store.read_bytes() == b"store-v1\n"


def test_hash_manifest_preserves_user_edits_across_canonical_file_rename(
    tmp_path,
    monkeypatch,
):
    plugin_directory = tmp_path / "plugin_impl"
    current = {
        "value": MappingProxyType(
            {
                "cyrene_system_prompt/__init__.py": b"pack-v1\n",
                "cyrene_system_prompt/prompt.py": b"prompt-v1\n",
            }
        )
    }
    monkeypatch.setattr(
        native_tools,
        "_collect_canonical_files",
        lambda: current["value"],
    )
    seed_builtin_plugin_directory(plugin_directory)

    old_prompt = plugin_directory / "cyrene_system_prompt" / "prompt.py"
    new_prompt = plugin_directory / "cyrene_system_prompt" / "system_prompt.py"
    old_prompt.write_bytes(b"user-prompt\n")
    current["value"] = MappingProxyType(
        {
            "cyrene_system_prompt/__init__.py": b"pack-v2\n",
            "cyrene_system_prompt/system_prompt.py": b"prompt-v2\n",
        }
    )

    seed_builtin_plugin_directory(plugin_directory)

    assert old_prompt.exists() is False
    assert new_prompt.read_bytes() == b"user-prompt\n"
    manifest = json.loads(
        (plugin_directory / ".upstream-hashes.json").read_text(encoding="utf-8")
    )
    assert manifest["files"]["cyrene_system_prompt/system_prompt.py"] == _sha(
        b"prompt-v1\n"
    )
    assert "cyrene_system_prompt/prompt.py" not in manifest["files"]


def test_unmanaged_pack_and_standalone_collisions_are_never_merged(
    tmp_path,
    monkeypatch,
):
    plugin_directory = tmp_path / "plugin_impl"
    canonical = MappingProxyType(
        {
            "cyrene_application/__init__.py": b"canonical-pack\n",
            "cyrene_application/new_tool.py": b"canonical-tool\n",
            "cyrene_browser/__init__.py": b"browser-pack\n",
            "cyrene_browser/new_tool.py": b"browser-tool\n",
            "edit.py": b"canonical-edit\n",
        }
    )
    monkeypatch.setattr(native_tools, "_collect_canonical_files", lambda: canonical)

    unmanaged_pack = plugin_directory / "cyrene_application"
    unmanaged_pack.mkdir(parents=True)
    unmanaged_initializer = unmanaged_pack / "__init__.py"
    unmanaged_initializer.write_bytes(b"user-pack\n")
    outside = tmp_path / "outside-browser"
    outside.mkdir()
    browser_link = plugin_directory / "cyrene_browser"
    browser_link.symlink_to(outside, target_is_directory=True)
    edit = plugin_directory / "edit.py"
    edit.write_bytes(b"canonical-edit\n")

    seeded = seed_builtin_plugin_directory(plugin_directory)

    assert seeded.created == ()
    assert seeded.updated == ()
    assert set(seeded.existing) == {unmanaged_pack, browser_link, edit}
    assert unmanaged_initializer.read_bytes() == b"user-pack\n"
    assert not (unmanaged_pack / "new_tool.py").exists()
    assert not (outside / "new_tool.py").exists()
    assert edit.read_bytes() == b"canonical-edit\n"
    assert len(seeded.diagnostics) == 3
    assert all("preserved" in diagnostic for diagnostic in seeded.diagnostics)
    manifest = json.loads(seeded.manifest.read_text(encoding="utf-8"))
    assert manifest == {"version": 1, "files": {}}


def test_explicit_canonical_pack_tombstone_prevents_reseed(tmp_path, monkeypatch):
    plugin_directory = tmp_path / "plugin_impl"
    canonical = MappingProxyType(
        {
            "cyrene_demo/__init__.py": b"pack\n",
            "cyrene_demo/tool.py": b"tool\n",
            "edit.py": b"edit\n",
        }
    )
    monkeypatch.setattr(native_tools, "_collect_canonical_files", lambda: canonical)
    seed_builtin_plugin_directory(plugin_directory)
    pack = plugin_directory / "cyrene_demo"

    assert mark_builtin_plugin_deleted(plugin_directory, "cyrene_demo") is True
    for child in pack.iterdir():
        child.unlink()
    pack.rmdir()
    reseeded = seed_builtin_plugin_directory(plugin_directory)

    assert pack.exists() is False
    assert not any(path.is_relative_to(pack) for path in reseeded.created)
    manifest = json.loads(reseeded.manifest.read_text(encoding="utf-8"))
    assert manifest["deleted"] == ["cyrene_demo"]
    assert all(not key.startswith("cyrene_demo/") for key in manifest["files"])


def test_frozen_build_reads_the_packaged_canonical_tree(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    canonical = (
        bundle
        / "builtin_plugin_sources"
        / "cyrene" / "plugins" / "builtin"
    )
    pack = canonical / "cyrene_application"
    pack.mkdir(parents=True)
    schedule_pack = canonical / "cyrene_schedule"
    schedule_pack.mkdir(parents=True)
    (canonical / "__init__.py").write_bytes(b"not-seeded\n")
    (pack / "__init__.py").write_bytes(b"pack\n")
    for name in ("__init__.py", "application.py", "schedule_spec.py", "tools.py"):
        (schedule_pack / name).write_bytes(name.encode("utf-8"))
    for name in ("edit.py", "glob.py", "grep.py"):
        (canonical / name).write_bytes(name.encode("utf-8"))

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    files = native_tools._collect_canonical_files()

    assert files == {
        "cyrene_application/__init__.py": b"pack\n",
        "cyrene_schedule/__init__.py": b"__init__.py",
        "cyrene_schedule/application.py": b"application.py",
        "cyrene_schedule/schedule_spec.py": b"schedule_spec.py",
        "cyrene_schedule/tools.py": b"tools.py",
        "edit.py": b"edit.py",
        "glob.py": b"glob.py",
        "grep.py": b"grep.py",
    }
