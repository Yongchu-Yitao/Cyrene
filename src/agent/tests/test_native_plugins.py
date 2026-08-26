from __future__ import annotations

import asyncio
import inspect
import json
import sys
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

from agent.plugin import PluginContext, PluginRegistry, PluginRuntime
from agent.plugin import native_tools
from agent.plugin.native_tools import (
    CORE_PLUGIN_NAMES,
    USER_STANDALONE_PLUGIN_NAMES,
    seed_builtin_plugin_directory,
)


BUSINESS_PACK_IDS = frozenset(
    {
        "cyrene_application",
        "cyrene_browser",
        "cyrene_code",
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
        "cyrene_plugins",
        "cyrene_remote",
        "cyrene_renderer",
        "cyrene_skills",
        "cyrene_subagent",
        "cyrene_task",
    }
)


def _run(coroutine):
    return asyncio.run(coroutine)


def _sha(content: bytes) -> str:
    return sha256(content).hexdigest()


def test_seeded_canonical_plugins_complete_toolbox_chain(tmp_path):
    async def scenario():
        plugin_directory = tmp_path / "plugin_impl"
        legacy_pack = plugin_directory / "cyrene_tools"
        legacy_pack.mkdir(parents=True)
        legacy_initializer = legacy_pack / "__init__.py"
        legacy_initializer.write_bytes(native_tools._FIRST_GENERATION_PACK_INITIALIZER)
        obsolete_shim = legacy_pack / "tool_obsolete.py"
        obsolete_shim.write_text(
            "raise AssertionError('canonical packs imported an obsolete shim')\n",
            encoding="utf-8",
        )

        seeded = seed_builtin_plugin_directory(plugin_directory)

        assert seeded.directory == plugin_directory.resolve()
        assert seeded.manifest == plugin_directory / ".upstream-hashes.json"
        assert seeded.manifest.is_file()
        assert not legacy_pack.exists()
        assert seeded.legacy_backups == (
            plugin_directory / ".cyrene_tools-legacy",
        )
        backup = seeded.legacy_backups[0]
        assert (backup / "__init__.py").read_bytes() == (
            native_tools._FIRST_GENERATION_PACK_INITIALIZER
        )
        assert (backup / obsolete_shim.name).is_file()
        assert "child-file edits could not be verified" in " ".join(
            seeded.diagnostics
        )
        assert not (plugin_directory / "__init__.py").exists()
        assert not tuple(plugin_directory.glob(".*.cyrene-seed-*"))

        # Prove the user-owned business source is the code that executes.  A
        # packaged handler fallback would ignore this edit and fail the test.
        guide_source = plugin_directory / "cyrene_plugins" / "tools.py"
        original_guide = (
            '    return _result({"ok": True, "apiVersion": 1, '
            '"guide": AUTHORING_GUIDE})'
        )
        sentinel = "sentinel-from-user-plugin-impl"
        source = guide_source.read_text(encoding="utf-8")
        assert source.count(original_guide) == 1
        guide_source.write_text(
            source.replace(original_guide, f'    return "{sentinel}"'),
            encoding="utf-8",
        )

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
        assert BUSINESS_PACK_IDS <= set(user_packs)
        business_names = {
            plugin.name
            for pack_id in BUSINESS_PACK_IDS
            for plugin in user_packs[pack_id].plugins
        }
        assert len(business_names) == 196
        assert all(
            plugin.kind == "tool"
            for pack_id in BUSINESS_PACK_IDS
            for plugin in user_packs[pack_id].plugins
        )
        assert all(
            plugin.kind == "model"
            for pack_id, pack in user_packs.items()
            if pack_id not in BUSINESS_PACK_IDS
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
        assert len(business_names | standalone_names | CORE_PLUGIN_NAMES) == 202
        from cyrene.tooling.catalog import get_tool_names

        assert business_names | standalone_names | CORE_PLUGIN_NAMES == set(
            get_tool_names()
        )

        for name in business_names | standalone_names:
            registered = registry.registered(name)
            assert Path(registered.source).is_relative_to(plugin_directory)

        guide = registry.resolve("PluginAuthoringGuide")
        adapter = getattr(guide.handler, "__self__")
        implementation = getattr(adapter, "implementation")
        implementation_source = Path(
            inspect.getsourcefile(implementation) or ""
        ).resolve()
        adapter_source = Path(
            inspect.getsourcefile(type(adapter)) or ""
        ).resolve()
        assert implementation_source == guide_source.resolve()
        assert adapter_source.is_relative_to(plugin_directory)

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
        assert {item["id"] for item in listing.value["packs"]} == BUSINESS_PACK_IDS
        listed_names = {
            tool["name"]
            for pack in listing.value["packs"]
            for tool in pack["tools"]
        }
        assert listed_names == business_names
        assert {
            item["name"] for item in listing.value["standalone_tools"]
        } == USER_STANDALONE_PLUGIN_NAMES

        described = await runtime.call(
            "toolbox",
            {"operation": "describe", "name": "PluginAuthoringGuide"},
            context,
        )
        assert described.success is True
        description = described.value["plugins"][0]
        assert description["name"] == "PluginAuthoringGuide"
        assert description["pack"] == "cyrene_plugins"
        assert description["input_schema"] == guide.input_schema

        invoked = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "PluginAuthoringGuide",
                "arguments": {},
            },
            context,
        )
        assert invoked.success is True
        assert invoked.value == {
            "operation": "invoke",
            "name": "PluginAuthoringGuide",
            "result": sentinel,
        }

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


def test_modified_aggregate_pack_is_backed_up_without_losing_edits(
    tmp_path,
    monkeypatch,
):
    plugin_directory = tmp_path / "plugin_impl"
    canonical = MappingProxyType(
        {
            "cyrene_application/__init__.py": b"canonical-pack\n",
            "edit.py": b"canonical-edit\n",
        }
    )
    monkeypatch.setattr(native_tools, "_collect_canonical_files", lambda: canonical)
    legacy = plugin_directory / "cyrene_tools"
    legacy.mkdir(parents=True)
    (legacy / "__init__.py").write_bytes(b"user-initializer\n")
    (legacy / "tool_custom.py").write_bytes(b"user-tool\n")

    seeded = seed_builtin_plugin_directory(plugin_directory)

    assert not legacy.exists()
    assert seeded.legacy_backups == (
        plugin_directory / ".cyrene_tools-legacy",
    )
    backup = seeded.legacy_backups[0]
    assert (backup / "__init__.py").read_bytes() == b"user-initializer\n"
    assert (backup / "tool_custom.py").read_bytes() == b"user-tool\n"
    assert "initializer modification detected" in " ".join(seeded.diagnostics)


def test_only_verified_legacy_model_pack_is_migrated(tmp_path, monkeypatch):
    canonical = MappingProxyType(
        {
            "cyrene_model/__init__.py": b"canonical-model\n",
            "cyrene_model/provider.py": b"provider\n",
            "edit.py": b"canonical-edit\n",
        }
    )
    monkeypatch.setattr(native_tools, "_collect_canonical_files", lambda: canonical)
    known_initializer = b"legacy-model\n"
    known_provider = b"legacy-provider\n"
    monkeypatch.setattr(
        native_tools,
        "_LEGACY_MODEL_DEFAULT_HASHES",
        MappingProxyType(
            {
                "model/__init__.py": _sha(known_initializer),
                "model/minimax.py": _sha(known_provider),
            }
        ),
    )

    verified_root = tmp_path / "verified" / "plugin_impl"
    verified_model = verified_root / "model"
    verified_model.mkdir(parents=True)
    (verified_model / "__init__.py").write_bytes(known_initializer)
    (verified_model / "minimax.py").write_bytes(known_provider)

    migrated = seed_builtin_plugin_directory(verified_root)

    assert not verified_model.exists()
    assert migrated.legacy_backups == (verified_root / ".model-legacy",)
    assert (verified_root / ".model-legacy" / "minimax.py").read_bytes() == (
        known_provider
    )
    assert (verified_root / "cyrene_model" / "provider.py").is_file()

    unmanaged_root = tmp_path / "unmanaged" / "plugin_impl"
    unmanaged_model = unmanaged_root / "model"
    unmanaged_model.mkdir(parents=True)
    (unmanaged_model / "__init__.py").write_bytes(b"user-model\n")

    preserved = seed_builtin_plugin_directory(unmanaged_root)

    assert unmanaged_model.is_dir()
    assert not (unmanaged_root / "cyrene_model").exists()
    assert preserved.legacy_backups == ()
    assert "skipped cyrene_model" in " ".join(preserved.diagnostics)


def test_frozen_build_reads_the_packaged_canonical_tree(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    canonical = (
        bundle
        / "builtin_plugin_sources"
        / "agent"
        / "plugin"
        / "plugin_impl"
    )
    pack = canonical / "cyrene_application"
    pack.mkdir(parents=True)
    (canonical / "__init__.py").write_bytes(b"not-seeded\n")
    (pack / "__init__.py").write_bytes(b"pack\n")
    for name in ("edit.py", "glob.py", "grep.py"):
        (canonical / name).write_bytes(name.encode("utf-8"))

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    files = native_tools._collect_canonical_files()

    assert files == {
        "cyrene_application/__init__.py": b"pack\n",
        "edit.py": b"edit.py",
        "glob.py": b"glob.py",
        "grep.py": b"grep.py",
    }
