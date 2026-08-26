from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agent.plugin import PluginContext, PluginRegistry, PluginRuntime
from agent.plugin.native_tools import (
    CORE_PLUGIN_NAMES,
    LEGACY_HOST_CONTEXT_KEYS,
    MIGRATED_NATIVE_PLUGIN_NAMES,
    NATIVE_PLUGIN_PACK_ID,
    USER_STANDALONE_PLUGIN_NAMES,
    LegacyPluginHandler,
    NativePluginLoadError,
    create_native_plugin_pack,
    load_builtin_plugins,
    load_native_plugins,
    seed_builtin_plugin_directory,
)
from cyrene.tool_impl import NATIVE_TOOL_MODULES


def _run(coroutine):
    return asyncio.run(coroutine)


def _definition(name: str, *, required: tuple[str, ...] = ()) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Description for {name}",
            "parameters": {
                "type": "object",
                "properties": {item: {"type": "string"} for item in required},
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def _native_names_from_modules() -> set[str]:
    return {
        str(importlib.import_module(module_name).TOOL_DEF["function"]["name"])
        for module_name in NATIVE_TOOL_MODULES
    }


def test_every_declared_native_module_is_adapted_without_metadata_loss():
    plugins = load_native_plugins()
    by_name = {plugin.name: plugin for plugin in plugins}
    expected_names = _native_names_from_modules()

    assert len(plugins) == len(NATIVE_TOOL_MODULES) == len(expected_names)
    assert set(by_name) == expected_names
    assert MIGRATED_NATIVE_PLUGIN_NAMES <= expected_names

    for module_name in NATIVE_TOOL_MODULES:
        module = importlib.import_module(module_name)
        function = module.TOOL_DEF["function"]
        plugin = by_name[function["name"]]
        assert plugin.description == str(function.get("description") or "").strip()
        assert plugin.input_schema == function["parameters"]
        assert inspect.iscoroutinefunction(plugin.handler)
        adapter = plugin.handler.__self__
        assert isinstance(adapter, LegacyPluginHandler)
        assert adapter.module_name == module_name
        assert dict(adapter.metadata) == dict(
            getattr(module, "TOOL_METADATA", {})
        )

    assert by_name["CyreneAppStatus"].allow_parallel is True
    assert by_name["CyreneUISnapshot"].allow_parallel is False
    assert by_name["browser_request_takeover"].timeout_seconds == 900.0
    assert by_name["GenerateImage"].timeout_seconds == 420.0

    builtins = load_builtin_plugins()
    builtin_names = {plugin.name for plugin in builtins}
    from cyrene.tooling.catalog import get_tool_names

    legacy_catalog_names = set(get_tool_names())
    assert len(builtins) == len(builtin_names) == len(legacy_catalog_names) == 202
    assert builtin_names == legacy_catalog_names

    pack = create_native_plugin_pack()
    assert pack.id == NATIVE_PLUGIN_PACK_ID
    assert {plugin.name for plugin in pack.plugins} == (
        legacy_catalog_names - MIGRATED_NATIVE_PLUGIN_NAMES
    )


def test_adapter_handles_multi_tool_modules_and_rejects_incomplete_inventory(
    monkeypatch,
):
    first_name = "test_native_plugins.multi"
    first = ModuleType(first_name)

    def one(arguments, bot, chat_id, db_path, notify_state):
        return {
            "arguments": arguments,
            "host": [bot, chat_id, db_path, notify_state],
        }

    async def two(arguments, _bot, _chat_id, _db_path, _notify_state):
        return arguments["value"]

    first.TOOL_DEFS = [_definition("One"), _definition("Two", required=("value",))]
    first.TOOL_HANDLERS = {"One": one, "Two": two}
    first.TOOL_METADATA = {
        "One": {"requires_order": False, "resource_keys": ("one",)},
        "Two": {"timeout_seconds": 12},
    }
    monkeypatch.setitem(sys.modules, first_name, first)

    plugins = load_native_plugins((first_name,))
    assert [plugin.name for plugin in plugins] == ["One", "Two"]
    assert plugins[0].allow_parallel is True
    assert plugins[1].timeout_seconds == 12.0
    assert dict(plugins[0].handler.__self__.metadata) == first.TOOL_METADATA["One"]

    duplicate_name = "test_native_plugins.duplicate"
    duplicate = ModuleType(duplicate_name)
    duplicate.TOOL_DEF = _definition("One")
    duplicate.handler = one
    monkeypatch.setitem(sys.modules, duplicate_name, duplicate)

    missing_name = "test_native_plugins.does_not_exist"
    with pytest.raises(NativePluginLoadError) as raised:
        load_native_plugins((first_name, duplicate_name, missing_name))

    failures = {failure.module_name: failure.error for failure in raised.value.failures}
    assert "duplicate Plugin name 'One'" in failures[duplicate_name]
    assert "ModuleNotFoundError" in failures[missing_name]


def test_adapter_binds_explicit_run_context_and_resets_it(tmp_path, monkeypatch):
    module_name = "test_native_plugins.run_context"
    module = ModuleType(module_name)
    module.TOOL_DEF = _definition("ReadRunContext")

    async def read_context(_arguments, _bot, _chat_id, _db_path, _notify_state):
        from cyrene.agent.context import active_workspace_dir, current_run_context

        current = current_run_context()
        return {
            "session_id": current.session_id,
            "round_id": current.round_id,
            "permission_mode": current.permission_mode,
            "workspace": str(active_workspace_dir()),
        }

    module.handler = read_context
    monkeypatch.setitem(sys.modules, module_name, module)
    plugin = load_native_plugins((module_name,))[0]
    assert inspect.iscoroutinefunction(plugin.handler)

    from cyrene.agent.context import current_run_context

    before = current_run_context()

    async def scenario():
        return await plugin.handler(
            {},
            PluginContext(
                data={
                    "bot": None,
                    "chat_id": 0,
                    "db_path": "",
                    "notify_state": None,
                    "run_context": {
                        "session_id": "session-plugin",
                        "round_id": "round-plugin",
                        "permission_mode": "full_access",
                        "workspace_dir": tmp_path,
                        "workspace_enabled": True,
                    },
                }
            ),
        )

    assert _run(scenario()) == {
        "session_id": "session-plugin",
        "round_id": "round-plugin",
        "permission_mode": "full_access",
        "workspace": str(tmp_path),
    }
    assert current_run_context() == before


def test_seeded_builtin_plugins_use_toolbox_list_describe_invoke_chain(tmp_path):
    async def scenario():
        plugin_directory = tmp_path / "plugin_impl"
        seeded = seed_builtin_plugin_directory(plugin_directory)
        expected_builtin_names = {plugin.name for plugin in load_builtin_plugins()}
        expected_user_names = expected_builtin_names - CORE_PLUGIN_NAMES
        assert len(expected_builtin_names) == 202
        assert len(expected_user_names) == len(seeded.tool_files) == 199
        assert set(seeded.tool_files) == expected_user_names
        assert len(seeded.created) == 200
        assert seeded.existing == ()
        assert all(path.is_file() for path in seeded.tool_files.values())
        assert all(
            (
                "TOOL_DEF" in path.read_text(encoding="utf-8")
                or "input_schema=" in path.read_text(encoding="utf-8")
            )
            and (
                "async def handler" in path.read_text(encoding="utf-8")
                or f"async def {path.stem}" in path.read_text(encoding="utf-8")
            )
            for path in seeded.tool_files.values()
        )
        for name in USER_STANDALONE_PLUGIN_NAMES:
            source = seeded.tool_files[name].read_text(encoding="utf-8")
            assert "from agent.plugin import Plugin, PluginContext" in source
            assert "invoke_builtin_tool" not in source

        edited_path = seeded.tool_files["PluginAuthoringGuide"]
        edited_source = edited_path.read_text(encoding="utf-8") + "\n# user edit\n"
        edited_path.write_text(edited_source, encoding="utf-8")
        preserved = seed_builtin_plugin_directory(plugin_directory)
        assert preserved.created == ()
        assert edited_path.read_text(encoding="utf-8") == edited_source

        missing_path = seeded.tool_files["GitStatus"]
        missing_path.unlink()
        supplemented = seed_builtin_plugin_directory(plugin_directory)
        assert supplemented.created == (missing_path,)
        assert missing_path.is_file()

        registry = PluginRegistry()
        assert registry.load_directory(plugin_directory) == ()

        registered_names = {item.plugin.name for item in registry.list_plugins()}
        assert expected_builtin_names <= registered_names
        for name in expected_user_names:
            assert Path(registry.registered(name).source).is_relative_to(
                plugin_directory
            )
        for name in CORE_PLUGIN_NAMES:
            assert registry.registered(name).source == "core"

        pack_names = {
            plugin.name
            for plugin in registry.list_packs()
            if plugin.id == NATIVE_PLUGIN_PACK_ID
            for plugin in plugin.plugins
        }
        assert pack_names == expected_builtin_names - MIGRATED_NATIVE_PLUGIN_NAMES
        from cyrene.tooling.catalog import get_tool_names

        assert pack_names | MIGRATED_NATIVE_PLUGIN_NAMES == set(get_tool_names())
        for name in MIGRATED_NATIVE_PLUGIN_NAMES:
            assert registry.registered(name).pack_id != NATIVE_PLUGIN_PACK_ID

        runtime = PluginRuntime(registry)
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
            }
        )
        listing = await runtime.call("toolbox", {"operation": "list"}, context)
        assert listing.success is True
        listed_pack = next(
            pack
            for pack in listing.value["packs"]
            if pack["id"] == NATIVE_PLUGIN_PACK_ID
        )
        assert {item["name"] for item in listed_pack["tools"]} == pack_names
        assert {item["name"] for item in listing.value["standalone_tools"]} >= {
            "Edit",
            "Glob",
            "Grep",
        }
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
        assert description["pack"] == NATIVE_PLUGIN_PACK_ID
        assert description["input_schema"] == registry.resolve(
            "PluginAuthoringGuide"
        ).input_schema

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
        assert invoked.value["name"] == "PluginAuthoringGuide"
        assert json.loads(invoked.value["result"])["apiVersion"] == 1

        standalone = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "Glob",
                "arguments": {"pattern": "*.txt"},
            },
            context,
        )
        assert standalone.success is True
        assert standalone.value["result"] == "seeded.txt"

        missing_context = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "PluginAuthoringGuide",
                "arguments": {},
            },
            PluginContext(data={}),
        )
        assert missing_context.success is False
        assert "PluginContext.data is missing legacy host key(s)" in (
            missing_context.error
        )
        assert all(key in missing_context.error for key in LEGACY_HOST_CONTEXT_KEYS)

    _run(scenario())
