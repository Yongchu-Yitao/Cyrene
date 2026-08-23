from __future__ import annotations

import asyncio
import inspect
import json
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _custom_tools_enabled(monkeypatch):
    from cyrene.custom_tools.manager import CustomToolManager

    tracked_managers: set[CustomToolManager] = set()
    original_reload = CustomToolManager.reload
    original_start = CustomToolManager.start

    async def tracked_reload(self, *args, **kwargs):
        tracked_managers.add(self)
        return await original_reload(self, *args, **kwargs)

    async def tracked_start(self, *args, **kwargs):
        tracked_managers.add(self)
        return await original_start(self, *args, **kwargs)

    monkeypatch.setattr(CustomToolManager, "reload", tracked_reload)
    monkeypatch.setattr(CustomToolManager, "start", tracked_start)
    monkeypatch.setattr(
        "cyrene.custom_tools.manager._pack_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "cyrene.custom_tools.manager._package_switches",
        lambda: {},
    )
    monkeypatch.setattr(
        "cyrene.observability.debug.publish_event",
        AsyncMock(),
    )
    yield
    for manager in tracked_managers:
        manager.stop_sync()


def _tool_module(
    tool_name: str,
    label: str,
    *,
    metadata: dict | None = None,
) -> str:
    tool_def = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": f"custom {tool_name}",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    }
    metadata_source = (
        f"TOOL_METADATA = {metadata!r}\n"
        if metadata is not None
        else ""
    )
    return (
        f"TOOL_DEF = {tool_def!r}\n"
        f"{metadata_source}\n"
        "async def handler(arguments, *_args, **_kwargs):\n"
        f"    return {label!r} + ':' + str(arguments.get('value') or '')\n"
    )


def _relative_tool_module(tool_name: str) -> str:
    tool_def = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": f"custom {tool_name}",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    }
    return (
        "from ._shared import LABEL\n"
        f"TOOL_DEF = {tool_def!r}\n\n"
        "async def handler(arguments, *_args, **_kwargs):\n"
        "    return LABEL + ':' + str(arguments.get('value') or '')\n"
    )


def _host_echo_tool_module(tool_name: str) -> str:
    definition_source = _tool_module(tool_name, "unused").split(
        "async def handler",
        1,
    )[0]
    return (
        "import json\n"
        + definition_source
        + "async def handler(arguments, bot, chat_id, db_path, notify_state):\n"
        + "    return json.dumps({\n"
        + "        'implementation': 'custom',\n"
        + "        'arguments': arguments,\n"
        + "        'bot_matches': bot is not None,\n"
        + "        'chat_id': chat_id,\n"
        + "        'db_path': db_path,\n"
        + "        'notify_matches': notify_state is not None,\n"
        + "    })\n"
    )


def _write_tool(
    root,
    package_id: str,
    filename: str,
    tool_name: str,
    label: str,
    *,
    metadata: dict | None = None,
):
    package = root / package_id
    package.mkdir(parents=True, exist_ok=True)
    path = package / filename
    path.write_text(
        _tool_module(tool_name, label, metadata=metadata),
        encoding="utf-8",
    )
    return path


def _tool_identity(tool) -> tuple[str, str]:
    return str(tool.package_id), str(tool.name)


def _resolve(manager, package_id: str, tool_name: str):
    _package, tool = manager.resolve_tool(f"custom:{package_id}/{tool_name}")
    return tool


async def _call_loaded(tool, arguments: dict) -> str:
    handler = tool.handler
    assert inspect.iscoroutinefunction(handler)
    return await handler(arguments, None, 0, "", None)


def test_custom_tools_live_only_in_the_system_user_data_directory():
    from cyrene.custom_tools.manager import CUSTOM_TOOLS_ROOT, CustomToolManager
    from cyrene.runtime.paths import USER_DATA_DIR

    manager = CustomToolManager()

    assert CUSTOM_TOOLS_ROOT == USER_DATA_DIR / "custom-tools"
    assert manager.root == CUSTOM_TOOLS_ROOT


@pytest.mark.asyncio
async def test_reload_loads_multiple_packages_tools_and_optional_metadata(tmp_path):
    from cyrene.custom_tools.manager import CustomToolManager

    root = tmp_path / "custom-tools"
    _write_tool(
        root,
        "alpha",
        "echo.py",
        "Echo",
        "alpha-echo",
        metadata={
            "read_only": True,
            "resource_keys": ("custom:alpha",),
            "requires_order": False,
        },
    )
    _write_tool(root, "alpha", "second.py", "Second", "alpha-second")
    _write_tool(root, "beta", "other.py", "Other", "beta-other")

    manager = CustomToolManager(root)
    await manager.reload()

    tools = manager.get_tool_definitions()
    assert {_tool_identity(tool) for tool in tools} == {
        ("alpha", "Echo"),
        ("alpha", "Second"),
        ("beta", "Other"),
    }
    echo = _resolve(manager, "alpha", "Echo")
    assert echo.stable_name == "custom:alpha/Echo"
    assert echo.concrete_name.startswith("custom:alpha/Echo@")
    assert echo.capability_id == "custom.alpha.Echo"
    assert echo.metadata["read_only"] is True
    assert tuple(echo.metadata["resource_keys"]) == ("custom:alpha",)
    assert echo.metadata["requires_order"] is False
    assert await _call_loaded(echo, {"value": "hello"}) == "alpha-echo:hello"


@pytest.mark.asyncio
async def test_source_scan_runs_off_loop_and_reuses_unchanged_hashes(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools.manager import CustomToolManager

    root = tmp_path / "custom-tools"
    source = _write_tool(root, "cached", "tool.py", "Cached", "first")
    manager = CustomToolManager(root)
    main_thread = threading.get_ident()
    scan_threads: list[int] = []
    original_scan = manager._scan_source_tree

    def tracked_scan(previous_states):
        scan_threads.append(threading.get_ident())
        return original_scan(previous_states)

    monkeypatch.setattr(manager, "_scan_source_tree", tracked_scan)
    await manager.reload()

    reads: list[Path] = []
    original_read_bytes = Path.read_bytes

    def tracked_read_bytes(path):
        reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    await asyncio.to_thread(
        manager._scan_source_tree,
        dict(manager._source_states),
    )

    assert scan_threads[0] != main_thread
    assert source not in reads

    source.write_text(_tool_module("Cached", "second"), encoding="utf-8")
    await asyncio.to_thread(
        manager._scan_source_tree,
        dict(manager._source_states),
    )
    assert source in reads


@pytest.mark.asyncio
async def test_native_source_event_refreshes_only_affected_cache(
    tmp_path,
):
    from cyrene.custom_tools.manager import CustomToolManager

    root = tmp_path / "custom-tools"
    source = _write_tool(root, "native", "tool.py", "Native", "first")
    untouched = _write_tool(root, "untouched", "tool.py", "Untouched", "stable")
    manager = CustomToolManager(root)
    await manager.reload()
    manager._running = True
    untouched_state = manager._source_states[untouched]

    source.write_text(_tool_module("Native", "second"), encoding="utf-8")
    changed = await manager._apply_source_events({(2, str(source))})

    assert changed is True
    assert manager._source_states[untouched] is untouched_state
    assert await _call_loaded(
        _resolve(manager, "native", "Native"),
        {"value": "x"},
    ) == "second:x"


@pytest.mark.asyncio
async def test_disabled_custom_tool_pack_never_starts_source_watcher(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager

    root = tmp_path / "custom-tools"
    _write_tool(root, "disabled", "tool.py", "Disabled", "disabled")
    monkeypatch.setattr(manager_module, "_pack_enabled", lambda: False)
    manager = CustomToolManager(root)

    await manager.start()

    assert manager.running is True
    assert manager._watch_task is None


@pytest.mark.asyncio
async def test_disabling_custom_tool_pack_cancels_source_watcher(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager

    root = tmp_path / "custom-tools"
    _write_tool(root, "toggle", "tool.py", "Toggle", "toggle")
    pack_enabled = True
    monkeypatch.setattr(manager_module, "_pack_enabled", lambda: pack_enabled)
    manager = CustomToolManager(root)
    await manager.reload()
    manager._running = True
    watcher = asyncio.create_task(asyncio.Event().wait())
    manager._watch_task = watcher

    pack_enabled = False
    status = await manager.sync_pack_state()

    assert status["enabled"] is False
    assert manager._watch_task is None
    assert watcher.cancelled()


@pytest.mark.asyncio
async def test_bad_modules_are_reported_per_file_without_blocking_good_tools(tmp_path):
    from cyrene.custom_tools.manager import CustomToolManager

    root = tmp_path / "custom-tools"
    _write_tool(root, "mixed", "good.py", "Good", "good")
    package = root / "mixed"
    (package / "syntax_error.py").write_text(
        "def broken(:\n",
        encoding="utf-8",
    )
    (package / "sync_handler.py").write_text(
        _tool_module("Sync", "sync").replace(
            "async def handler",
            "def handler",
            1,
        ),
        encoding="utf-8",
    )
    (package / "missing_def.py").write_text(
        "async def handler(arguments, *_args):\n    return arguments\n",
        encoding="utf-8",
    )
    _write_tool(root, "mixed", "duplicate_a.py", "Duplicate", "first")
    _write_tool(root, "mixed", "duplicate_b.py", "Duplicate", "second")
    _write_tool(root, "healthy", "other.py", "Other", "other")

    manager = CustomToolManager(root)
    await manager.reload()

    assert {_tool_identity(tool) for tool in manager.get_tool_definitions()} == {
        ("healthy", "Other"),
        ("mixed", "Good"),
    }
    assert await _call_loaded(
        _resolve(manager, "mixed", "Good"),
        {"value": "still-loaded"},
    ) == "good:still-loaded"
    status_text = json.dumps(manager.status(), ensure_ascii=False, default=str)
    assert "syntax_error.py" in status_text
    assert "sync_handler.py" in status_text
    assert "missing_def.py" in status_text
    assert "duplicate_a.py" in status_text
    assert "duplicate_b.py" in status_text
    assert "DuplicateToolName" in status_text


@pytest.mark.asyncio
async def test_malformed_input_schema_is_isolated_before_catalog_exposure(
    tmp_path,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager
    from cyrene.tooling import catalog, wire

    root = tmp_path / "custom-tools"
    _write_tool(root, "healthy", "good.py", "Good", "good")
    package = root / "broken"
    package.mkdir(parents=True)
    (package / "read.py").write_text(
        _tool_module("Read", "must-not-load").replace(
            "'type': 'object'",
            "'type': 'array'",
            1,
        ),
        encoding="utf-8",
    )

    manager = CustomToolManager(root)
    previous = manager_module._manager
    manager_module._manager = manager
    try:
        await manager.reload()
        assert {_tool_identity(tool) for tool in manager.get_tool_definitions()} == {
            ("healthy", "Good"),
        }
        assert "parameters.type must be 'object'" in json.dumps(
            manager.list_errors(),
            ensure_ascii=False,
        )
        assert (
            catalog.get_effective_function_definitions()["Read"]["function"][
                "description"
            ]
            != "custom Read"
        )
    finally:
        manager_module._manager = previous
        wire.invalidate_wire_tool_cache()


@pytest.mark.asyncio
async def test_reload_replaces_modified_modules_and_removes_deleted_tools(tmp_path):
    from cyrene.custom_tools.manager import CustomToolManager

    root = tmp_path / "custom-tools"
    source = _write_tool(root, "mutable", "value.py", "Value", "version-a")
    manager = CustomToolManager(root)

    await manager.reload()
    first = _resolve(manager, "mutable", "Value")
    assert await _call_loaded(first, {"value": "x"}) == "version-a:x"

    # Keep the replacement the same size and reload immediately. Reloading
    # must consume current source bytes rather than stale import bytecode.
    source.write_text(
        _tool_module("Value", "version-b"),
        encoding="utf-8",
    )
    await manager.reload()
    second = _resolve(manager, "mutable", "Value")
    assert second is not first
    assert await _call_loaded(second, {"value": "x"}) == "version-b:x"

    source.unlink()
    await manager.reload()
    with pytest.raises(KeyError):
        _resolve(manager, "mutable", "Value")
    assert ("mutable", "Value") not in {
        _tool_identity(tool) for tool in manager.get_tool_definitions()
    }

    source.parent.rmdir()
    await manager.reload()
    assert manager.get_package("mutable") is None


@pytest.mark.asyncio
async def test_disabled_pack_does_not_import_or_expose_user_modules(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager

    root = tmp_path / "custom-tools"
    package = root / "disabled"
    package.mkdir(parents=True)
    imported_marker = tmp_path / "imported.txt"
    (package / "tool.py").write_text(
        (
            "from pathlib import Path\n"
            f"Path({str(imported_marker)!r}).write_text('imported', encoding='utf-8')\n"
            + _tool_module("DisabledTool", "disabled")
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(manager_module, "_pack_enabled", lambda: False)

    manager = CustomToolManager(root)
    await manager.reload()

    assert imported_marker.exists() is False
    assert manager.get_tool_definitions() == []
    assert manager.get_public_tool_defs() == []
    assert manager.list_errors() == []
    with pytest.raises(KeyError):
        manager.resolve_tool("custom:disabled/DisabledTool")
    status = manager.status()
    assert status["enabled"] is False
    assert status["tool_count"] == 0
    assert status["packages"][0]["status"] == "disabled"


@pytest.mark.asyncio
async def test_package_switches_are_independent_persistent_and_skip_imports(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager
    from cyrene.runtime import config_store, settings_service

    root = tmp_path / "custom-tools"
    imported_marker = tmp_path / "alpha-imported.txt"
    alpha = root / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "tool.py").write_text(
        (
            "from pathlib import Path\n"
            f"Path({str(imported_marker)!r}).write_text('imported', encoding='utf-8')\n"
            + _tool_module("AlphaTool", "alpha")
        ),
        encoding="utf-8",
    )
    beta_imports = tmp_path / "beta-imports.txt"
    beta_source = _write_tool(root, "beta", "tool.py", "BetaTool", "beta")
    beta_source.write_text(
        "from pathlib import Path\n"
        + f"_marker = Path({str(beta_imports)!r})\n"
        + "_marker.write_text(str(int(_marker.read_text()) + 1) if _marker.exists() else '1')\n"
        + beta_source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    switches = {"alpha": False}
    revision = [4]

    def read_switches():
        return dict(switches)

    def update_settings(
        namespace,
        changes,
        *,
        actor,
        expected_revision=None,
        **_kwargs,
    ):
        assert namespace == "runtime"
        assert actor == "ui"
        if expected_revision is not None and expected_revision != revision[0]:
            raise config_store.SettingsRevisionConflict(
                expected_revision,
                revision[0],
            )
        for key, enabled in changes["enabled_tool_packs"].items():
            assert key.startswith("custom_tools:")
            switches[key.split(":", 1)[1]] = enabled
        revision[0] += 1
        return {"revision": revision[0]}

    monkeypatch.setattr(manager_module, "_package_switches", read_switches)
    monkeypatch.setattr(settings_service, "update", update_settings)

    manager = CustomToolManager(root)
    await manager.reload()

    assert imported_marker.exists() is False
    assert {_tool_identity(tool) for tool in manager.get_tool_definitions()} == {
        ("beta", "BetaTool"),
    }
    packages = {item["id"]: item for item in manager.status()["packages"]}
    assert packages["alpha"]["configured_enabled"] is False
    assert packages["alpha"]["effective_enabled"] is False
    assert packages["alpha"]["source_count"] == 1
    assert packages["beta"]["configured_enabled"] is True
    assert packages["beta"]["effective_enabled"] is True

    enabled_status = await manager.set_package_enabled(
        "alpha",
        True,
        expected_revision=4,
    )
    assert enabled_status["settings_revision"] == 5
    assert imported_marker.read_text(encoding="utf-8") == "imported"
    assert {_tool_identity(tool) for tool in manager.get_tool_definitions()} == {
        ("alpha", "AlphaTool"),
        ("beta", "BetaTool"),
    }

    beta_identity = _resolve(manager, "beta", "BetaTool").concrete_name
    disabled_status = await manager.set_package_enabled("beta", False)
    assert disabled_status["settings_revision"] == 6
    disabled_beta = next(
        item for item in disabled_status["packages"] if item["id"] == "beta"
    )
    assert [tool["name"] for tool in disabled_beta["tools"]] == ["BetaTool"]
    assert disabled_beta["effective_enabled"] is False
    assert {_tool_identity(tool) for tool in manager.get_tool_definitions()} == {
        ("alpha", "AlphaTool"),
    }
    with pytest.raises(KeyError):
        manager.resolve_declared_tool(beta_identity)

    beta_import_count = beta_imports.read_text(encoding="utf-8")
    second_manager = CustomToolManager(root)
    await second_manager.reload()
    persisted = {item["id"]: item for item in second_manager.status()["packages"]}
    assert persisted["alpha"]["configured_enabled"] is True
    assert persisted["beta"]["configured_enabled"] is False
    assert persisted["beta"]["effective_enabled"] is False
    assert [tool["name"] for tool in persisted["beta"]["tools"]] == ["BetaTool"]
    assert beta_imports.read_text(encoding="utf-8") == beta_import_count

    with pytest.raises(KeyError):
        await manager.set_package_enabled("missing", False)
    with pytest.raises(TypeError):
        await manager.set_package_enabled("alpha", 1)

    alpha_identity = _resolve(manager, "alpha", "AlphaTool").concrete_name
    monkeypatch.setattr(
        manager,
        "_reload_locked",
        AsyncMock(side_effect=RuntimeError("reload failed")),
    )
    with pytest.raises(RuntimeError, match="reload failed"):
        await manager.set_package_enabled("alpha", False)
    assert manager.get_tool_definitions() == []
    with pytest.raises(KeyError):
        manager.resolve_declared_tool(alpha_identity)


def _patch_execution_dependencies(monkeypatch):
    from cyrene.observability import debug
    from cyrene.runtime import settings_store
    from cyrene.tooling import catalog, gateway, snapshot
    from cyrene.tooling import executor as executor_module

    monkeypatch.setattr(settings_store, "is_tool_pack_enabled", lambda _name: True)
    monkeypatch.setattr(catalog, "is_tool_pack_enabled", lambda _name: True)
    monkeypatch.setattr(gateway, "is_tool_pack_enabled", lambda _name: True)
    monkeypatch.setattr(snapshot, "is_tool_pack_enabled", lambda _name: True)
    monkeypatch.setattr(
        "cyrene.hooks.run_pre_tool_hooks",
        AsyncMock(side_effect=lambda _name, arguments: arguments),
    )
    monkeypatch.setattr("cyrene.hooks.run_post_tool_hooks", AsyncMock())
    monkeypatch.setattr(debug, "publish_event", AsyncMock())
    monkeypatch.setattr(
        executor_module,
        "_record_action_background",
        lambda *_args, **_kwargs: None,
    )


@pytest.mark.asyncio
async def test_relative_support_import_reload_rejects_frozen_old_revision(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager
    from cyrene.tooling import gateway, snapshot, wire

    _patch_execution_dependencies(monkeypatch)
    root = tmp_path / "custom-tools"
    package = root / "relative"
    package.mkdir(parents=True)
    shared = package / "_shared.py"
    shared.write_text("LABEL = 'relative-a'\n", encoding="utf-8")
    (package / "tool.py").write_text(
        _relative_tool_module("Relative"),
        encoding="utf-8",
    )
    manager = CustomToolManager(root)
    previous = manager_module._manager
    manager_module._manager = manager
    try:
        await manager.reload()
        first = _resolve(manager, "relative", "Relative")
        assert await _call_loaded(first, {"value": "x"}) == "relative-a:x"
        assert next(
            item for item in manager.list_files()
            if item["path"] == "relative/_shared.py"
        )["status"] == "support"

        frozen = snapshot.build_catalog_snapshot("main")
        frozen_identity = frozen.capabilities[
            "custom.relative.Relative"
        ].concrete_name
        assert frozen_identity == first.concrete_name

        shared.write_text("LABEL = 'relative-b'\n", encoding="utf-8")
        stale_before_reload = json.loads(await gateway.execute_wire_tool(
            "custom_tools",
            {
                "operation": "invoke",
                "capability_id": "custom.relative.Relative",
                "arguments": {"value": "must-not-run-after-save"},
            },
            None,
            0,
            "",
            None,
            catalog_snapshot=frozen,
        ))
        assert stale_before_reload["status"] == "error"
        assert frozen_identity in str(stale_before_reload["result"])
        assert "relative-a" not in json.dumps(
            stale_before_reload,
            ensure_ascii=False,
        )

        await manager.reload()
        second = _resolve(manager, "relative", "Relative")
        assert second.concrete_name != frozen_identity
        assert await _call_loaded(second, {"value": "x"}) == "relative-b:x"
        with pytest.raises(KeyError):
            manager.resolve_tool(frozen_identity)

        stale_result = json.loads(await gateway.execute_wire_tool(
            "custom_tools",
            {
                "operation": "invoke",
                "capability_id": "custom.relative.Relative",
                "arguments": {"value": "must-not-run-new-code"},
            },
            None,
            0,
            "",
            None,
            catalog_snapshot=frozen,
        ))
        assert stale_result["status"] == "error"
        assert frozen_identity in str(stale_result["result"])
        assert "relative-b" not in json.dumps(stale_result, ensure_ascii=False)
    finally:
        manager_module._manager = previous
        wire.invalidate_wire_tool_cache()


@pytest.mark.asyncio
async def test_unique_name_override_and_system_identity_execute_through_real_stack(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager
    from cyrene.tooling import catalog, gateway, snapshot, wire
    from cyrene.tooling.executor import _execute_tool

    _patch_execution_dependencies(monkeypatch)
    host_bot = object()
    host_notify = {"sent": False}

    async def native_read(arguments, bot, chat_id, db_path, notify_state):
        return json.dumps({
            "implementation": "system",
            "arguments": arguments,
            "bot_matches": bot is host_bot,
            "chat_id": chat_id,
            "db_path": db_path,
            "notify_matches": notify_state is host_notify,
        })

    monkeypatch.setitem(catalog.TOOL_HANDLERS, "Read", native_read)
    root = tmp_path / "custom-tools"
    custom_source = _write_tool(
        root,
        "override",
        "read.py",
        "Read",
        "custom-read",
    )
    custom_source.write_text(
        _host_echo_tool_module("Read"),
        encoding="utf-8",
    )
    manager = CustomToolManager(root)
    previous = manager_module._manager
    manager_module._manager = manager
    try:
        await manager.reload()

        effective = catalog.get_effective_function_definitions()
        assert effective["Read"]["function"]["description"] == "custom Read"
        frozen = snapshot.build_catalog_snapshot("main")
        custom_identity = frozen.capabilities["Read"].concrete_name
        assert custom_identity.startswith("custom:override/Read@")
        assert frozen.capabilities["Read"].source == "custom"
        assert frozen.capabilities["system.Read"].concrete_name == "system:Read"
        assert frozen.capabilities["system.Read"].source == "native"

        custom_call = gateway.resolve_wire_call(
            "Read",
            {"value": "hello"},
            catalog_snapshot=frozen,
        )
        assert custom_call.concrete_name == custom_identity
        custom_result = json.loads(await _execute_tool(
            custom_call.concrete_name,
            custom_call.concrete_arguments,
            host_bot,
            41,
            "host.db",
            host_notify,
        ))
        assert custom_result == {
            "implementation": "custom",
            "arguments": {"value": "hello"},
            "bot_matches": True,
            "chat_id": 41,
            "db_path": "host.db",
            "notify_matches": True,
        }

        system_call = gateway.resolve_wire_call(
            "system.Read",
            {"path": "file.txt"},
            catalog_snapshot=frozen,
        )
        assert system_call.concrete_name == "system:Read"
        system_result = json.loads(await _execute_tool(
            system_call.concrete_name,
            system_call.concrete_arguments,
            host_bot,
            41,
            "host.db",
            host_notify,
        ))
        assert system_result == {
            "implementation": "system",
            "arguments": {"path": "file.txt"},
            "bot_matches": True,
            "chat_id": 41,
            "db_path": "host.db",
            "notify_matches": True,
        }
    finally:
        manager_module._manager = previous
        wire.invalidate_wire_tool_cache()


@pytest.mark.asyncio
async def test_custom_tool_is_rechecked_after_hooks_before_handler_admission(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager
    from cyrene.tooling.executor import _execute_tool

    _patch_execution_dependencies(monkeypatch)
    root = tmp_path / "custom-tools"
    marker = tmp_path / "handler-ran.txt"
    source = _write_tool(root, "race", "tool.py", "RaceTool", "unused")
    definition_source = source.read_text(encoding="utf-8").split(
        "async def handler",
        1,
    )[0]
    source.write_text(
        "from pathlib import Path\n"
        + definition_source
        + "async def handler(arguments, *_args, **_kwargs):\n"
        + f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
        + "    return 'ran'\n",
        encoding="utf-8",
    )
    switches = {"race": True}
    monkeypatch.setattr(
        manager_module,
        "_package_switches",
        lambda: dict(switches),
    )

    async def disable_while_yielded(_name, arguments):
        switches["race"] = False
        return arguments

    monkeypatch.setattr(
        "cyrene.hooks.run_pre_tool_hooks",
        AsyncMock(side_effect=disable_while_yielded),
    )
    manager = CustomToolManager(root)
    previous = manager_module._manager
    manager_module._manager = manager
    try:
        await manager.reload()
        identity = _resolve(manager, "race", "RaceTool").concrete_name
        result = await _execute_tool(identity, {}, None, 0, "", None)
        assert result.startswith("Tool unavailable:")
        assert marker.exists() is False
    finally:
        manager_module._manager = previous


@pytest.mark.asyncio
async def test_ambiguous_custom_names_do_not_override_native_but_remain_qualified(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager
    from cyrene.tooling import catalog, gateway, snapshot, wire
    from cyrene.tooling.executor import _execute_tool

    _patch_execution_dependencies(monkeypatch)

    async def native_read(arguments, *_args):
        return "system:" + str(arguments.get("path") or "")

    monkeypatch.setitem(catalog.TOOL_HANDLERS, "Read", native_read)
    root = tmp_path / "custom-tools"
    _write_tool(root, "alpha", "read.py", "Read", "alpha")
    _write_tool(root, "beta", "read.py", "Read", "beta")
    manager = CustomToolManager(root)
    previous = manager_module._manager
    manager_module._manager = manager
    try:
        await manager.reload()

        with pytest.raises(ValueError, match="ambiguous"):
            manager.resolve_tool("Read")
        alpha = _resolve(manager, "alpha", "Read")
        beta = _resolve(manager, "beta", "Read")
        assert alpha.stable_name == "custom:alpha/Read"
        assert beta.stable_name == "custom:beta/Read"

        effective = catalog.get_effective_function_definitions()
        assert effective["Read"]["function"]["description"] != "custom Read"
        frozen = snapshot.build_catalog_snapshot("main")
        raw_call = gateway.resolve_wire_call(
            "Read",
            {"path": "native.txt"},
            catalog_snapshot=frozen,
        )
        assert raw_call.concrete_name == "Read"
        assert await _execute_tool(
            raw_call.concrete_name,
            raw_call.concrete_arguments,
            None,
            0,
            "",
            None,
        ) == "system:native.txt"

        for package_id in ("alpha", "beta"):
            capability_id = f"custom.{package_id}.Read"
            resolution = gateway.resolve_wire_call(
                "custom_tools",
                {
                    "operation": "invoke",
                    "capability_id": capability_id,
                    "arguments": {"value": "qualified"},
                },
                catalog_snapshot=frozen,
            )
            assert resolution.concrete_name.startswith(
                f"custom:{package_id}/Read@"
            )
            assert await _execute_tool(
                resolution.concrete_name,
                resolution.concrete_arguments,
                None,
                0,
                "",
                None,
            ) == f"{package_id}:qualified"
    finally:
        manager_module._manager = previous
        wire.invalidate_wire_tool_cache()


@pytest.mark.asyncio
async def test_system_identity_respects_the_original_native_pack_toggle(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager
    from cyrene.tooling import catalog

    root = tmp_path / "custom-tools"
    _write_tool(root, "override", "recall.py", "RecallMemory", "custom")
    manager = CustomToolManager(root)
    previous = manager_module._manager
    manager_module._manager = manager

    def enabled(wire_name):
        return wire_name != "memory_tools"

    monkeypatch.setattr(catalog, "is_tool_pack_enabled", enabled)
    try:
        await manager.reload()
        capability_ids = {
            capability.capability_id
            for capability in catalog.capabilities_for_pack(
                "custom_tools",
                include_disabled=True,
            )
        }
        assert "custom.override.RecallMemory" in capability_ids
        assert "system.RecallMemory" not in capability_ids
    finally:
        manager_module._manager = previous


@pytest.mark.asyncio
async def test_custom_name_does_not_hijack_raw_mcp_execution(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager
    from cyrene.tooling import catalog
    from cyrene.tooling.backends import mcp_manager
    from cyrene.tooling.executor import _execute_tool

    _patch_execution_dependencies(monkeypatch)
    monkeypatch.delitem(catalog.TOOL_HANDLERS, "Collision", raising=False)
    monkeypatch.setattr(
        "cyrene.tooling.runtime_api.request_scope_elevation",
        AsyncMock(return_value=None),
    )
    mcp_calls: list[tuple[str, dict]] = []

    class FakeMcpManager:
        @staticmethod
        def has_tool(name):
            return name == "Collision"

        @staticmethod
        async def execute_tool(name, arguments):
            mcp_calls.append((name, dict(arguments)))
            return "mcp:" + str(arguments.get("value") or "")

    monkeypatch.setattr(mcp_manager, "get_manager", lambda: FakeMcpManager())
    root = tmp_path / "custom-tools"
    _write_tool(root, "local", "collision.py", "Collision", "custom")
    manager = CustomToolManager(root)
    previous = manager_module._manager
    manager_module._manager = manager
    try:
        await manager.reload()
        custom = _resolve(manager, "local", "Collision")

        assert await _execute_tool(
            "Collision",
            {"value": "raw"},
            None,
            0,
            "",
            None,
        ) == "mcp:raw"
        assert mcp_calls == [("Collision", {"value": "raw"})]
        assert await _execute_tool(
            custom.concrete_name,
            {"value": "qualified"},
            None,
            0,
            "",
            None,
        ) == "custom:qualified"
        assert mcp_calls == [("Collision", {"value": "raw"})]
    finally:
        manager_module._manager = previous


@pytest.mark.asyncio
async def test_custom_tool_metadata_reaches_execution_scheduling(
    tmp_path,
    monkeypatch,
):
    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager
    from cyrene.runtime import settings_store
    from cyrene.tooling import catalog

    monkeypatch.setattr(settings_store, "is_tool_pack_enabled", lambda _name: True)
    monkeypatch.setattr(catalog, "is_tool_pack_enabled", lambda _name: True)
    root = tmp_path / "custom-tools"
    _write_tool(
        root,
        "metadata",
        "inspect.py",
        "InspectCustom",
        "metadata",
        metadata={
            "read_only": True,
            "resource_keys": ("fs:{path}",),
            "requires_order": False,
        },
    )
    manager = CustomToolManager(root)
    previous = manager_module._manager
    manager_module._manager = manager
    try:
        await manager.reload()
        metadata = catalog.get_tool_execution_metadata(
            "custom:metadata/InspectCustom",
            {"path": "sample.txt"},
        )
        assert metadata["read_only"] is True
        assert metadata["requires_order"] is False
        assert metadata["resource_keys"]
        assert metadata["resource_keys"][0].startswith("fs:")
    finally:
        manager_module._manager = previous


def test_status_reload_and_package_toggle_are_the_complete_http_contract(
    tmp_path,
    monkeypatch,
):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from cyrene.custom_tools import manager as manager_module
    from cyrene.custom_tools.manager import CustomToolManager
    from cyrene.runtime import config_store, settings_service
    from route import custom_tools as custom_tool_routes

    root = tmp_path / "custom-tools"
    _write_tool(root, "api", "first.py", "First", "first")
    (root / "api" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    manager = CustomToolManager(root)
    real_reload = manager.reload
    start_calls = []
    switches = {}
    revision = [10]

    monkeypatch.setattr(
        manager_module,
        "_package_switches",
        lambda: dict(switches),
    )

    def update_settings(
        _namespace,
        changes,
        *,
        actor,
        expected_revision=None,
        **_kwargs,
    ):
        assert actor == "ui"
        if expected_revision is not None and expected_revision != revision[0]:
            raise config_store.SettingsRevisionConflict(
                expected_revision,
                revision[0],
            )
        for key, enabled in changes["enabled_tool_packs"].items():
            switches[key.split(":", 1)[1]] = enabled
        revision[0] += 1
        return {"revision": revision[0]}

    monkeypatch.setattr(settings_service, "update", update_settings)

    async def start_without_watcher():
        start_calls.append("start")
        manager._running = True
        await real_reload(reason="startup")

    # Preserve the route's explicit start semantics without leaving a watcher
    # attached to TestClient's private event loop.
    monkeypatch.setattr(manager, "start", start_without_watcher)
    monkeypatch.setattr(
        custom_tool_routes,
        "get_custom_tool_manager",
        lambda: manager,
    )

    router = APIRouter()
    custom_tool_routes.register_custom_tool_routes(router, None, "")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    custom_routes = {
        (route.path, method)
        for route in router.routes
        if route.path.startswith("/api/custom-tools")
        for method in route.methods
    }
    assert custom_routes == {
        ("/api/custom-tools/status", "GET"),
        ("/api/custom-tools/reload", "POST"),
        ("/api/custom-tools/packages/{package_id}/enabled", "PUT"),
    }

    status_before_reload = client.get("/api/custom-tools/status")
    assert status_before_reload.status_code == 200
    assert status_before_reload.json()["running"] is False
    assert status_before_reload.json()["generation"] == 0
    assert status_before_reload.json()["package_count"] == 0
    assert status_before_reload.json()["tool_count"] == 0
    assert status_before_reload.json()["error_count"] == 0
    assert manager.get_tool_definitions() == []
    assert start_calls == []
    assert client.get("/api/custom-tools").status_code == 404
    assert client.get("/api/custom-tools/root").status_code == 404

    reloaded = client.post("/api/custom-tools/reload")
    assert reloaded.status_code == 200
    assert reloaded.json()["ok"] is True
    assert start_calls == ["start"]

    status = client.get("/api/custom-tools/status")
    assert status.status_code == 200
    assert status.json()["root"] == str(root)
    status_text = json.dumps(status.json(), ensure_ascii=False)
    assert "custom:api/First" in status_text
    assert "broken.py" in status_text

    _write_tool(root, "api", "second.py", "Second", "second")
    second_reload = client.post("/api/custom-tools/reload")
    assert second_reload.status_code == 200
    assert start_calls == ["start"]
    assert "custom:api/Second" in json.dumps(
        client.get("/api/custom-tools/status").json(),
        ensure_ascii=False,
    )

    disabled = client.put(
        "/api/custom-tools/packages/api/enabled",
        json={"enabled": False, "expected_revision": 10},
    )
    assert disabled.status_code == 200
    assert disabled.json()["settings_revision"] == 11
    package = disabled.json()["packages"][0]
    assert package["configured_enabled"] is False
    assert package["effective_enabled"] is False
    assert {tool["name"] for tool in package["tools"]} == {"First", "Second"}
    assert disabled.json()["tool_count"] == 0

    invalid = client.put(
        "/api/custom-tools/packages/api/enabled",
        json={"enabled": "false"},
    )
    assert invalid.status_code == 422
    extra = client.put(
        "/api/custom-tools/packages/api/enabled",
        json={"enabled": False, "unexpected": True},
    )
    assert extra.status_code == 422
    negative_revision = client.put(
        "/api/custom-tools/packages/api/enabled",
        json={"enabled": False, "expected_revision": -1},
    )
    assert negative_revision.status_code == 422
    unknown = client.put(
        "/api/custom-tools/packages/missing/enabled",
        json={"enabled": False},
    )
    assert unknown.status_code == 404
    conflict = client.put(
        "/api/custom-tools/packages/api/enabled",
        json={"enabled": True, "expected_revision": 10},
    )
    assert conflict.status_code == 409

    enabled = client.put(
        "/api/custom-tools/packages/api/enabled",
        json={"enabled": True, "expected_revision": 11},
    )
    assert enabled.status_code == 200
    assert enabled.json()["packages"][0]["effective_enabled"] is True
    assert enabled.json()["tool_count"] == 2


def test_no_dedicated_agent_management_tool_is_registered():
    from cyrene.agent.prompts import _MAIN_CUSTOM_TOOLS_PROMPT
    from cyrene.runtime.paths import USER_DATA_DIR
    from cyrene.tooling import catalog
    from cyrene.tooling.packs import CAPABILITY_BINDINGS

    assert str(USER_DATA_DIR / "custom-tools") in _MAIN_CUSTOM_TOOLS_PROMPT
    assert (
        "existing direct Read, Write, Edit, Glob, and Grep file tools"
        in _MAIN_CUSTOM_TOOLS_PROMPT
    )
    assert "ManageCustomTools" not in _MAIN_CUSTOM_TOOLS_PROMPT
    assert "ManageCustomTools" not in catalog.TOOL_HANDLERS
    assert all(
        (tool_def.get("function") or {}).get("name") != "ManageCustomTools"
        for tool_def in catalog.TOOL_DEFS
    )
    assert all(
        concrete_name != "ManageCustomTools"
        for bindings in CAPABILITY_BINDINGS.values()
        for _capability_id, concrete_name in bindings
    )
