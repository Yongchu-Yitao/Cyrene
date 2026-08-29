from __future__ import annotations

import pytest

from cyrene.plugins.builtin.cyrene_desktop import _app_semantic_backend as app_semantic


@pytest.fixture(autouse=True)
def clear_semantic_sessions(tmp_path):
    app_semantic._SESSIONS.clear()
    original = app_semantic._IDEMPOTENCY_PATH
    app_semantic._IDEMPOTENCY_PATH = tmp_path / "app_semantic_idempotency.json"
    yield
    app_semantic._SESSIONS.clear()
    app_semantic._IDEMPOTENCY_PATH = original


@pytest.mark.asyncio
async def test_semantic_session_snapshot_action_lease_and_idempotency(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_rpc(operation, arguments=None, **_kwargs):
        arguments = arguments or {}
        calls.append((operation, arguments))
        if operation == "connect":
            assert arguments["parameters"]["mode"] == "semantic"
            assert arguments["parameters"]["focus_policy"] == "never"
            return {
                "status": "success", "session_id": "app_session_test",
                "target": {"app_name": "Demo", "platform": "linux"},
                "semantic_profile": {"status": "available"},
            }
        if operation == "call" and arguments.get("capability") == "snapshot":
            return {
                "status": "success", "snapshot_revision": 7,
                "semantic_profile": {"status": "available"},
                "nodes": [{"ref": "e1", "role": "button", "name": "Save", "actions": ["press"]}],
            }
        if operation == "call" and arguments.get("capability") == "press":
            return {"status": "success", "verification": {"nodes": [{"ref": "e1", "role": "status", "name": "Saved", "actions": []}]}}
        raise AssertionError((operation, arguments))

    monkeypatch.setattr(app_semantic, "electron_app_rpc", fake_rpc)
    connected = await app_semantic.execute_snapshot({"operation": "connect", "target_id": "target_1"})
    snapshot = await app_semantic.execute_snapshot({"operation": "snapshot", "session_id": connected["session_id"]})
    node = snapshot["nodes"][0]
    action = node["actions"][0]
    request = {
        "session_id": connected["session_id"], "snapshot_id": snapshot["snapshot_id"],
        "revision": snapshot["revision"], "node_id": node["node_id"], "action_id": action["action_id"],
        "reason": "Save the document", "idempotency_key": "save-once-123",
    }
    first = await app_semantic.execute_action("click", request)
    second = await app_semantic.execute_action("click", request)
    assert first["status"] == "success"
    assert first["effect_verified"] is True
    assert second["idempotent_replay"] is True
    assert sum(args.get("capability") == "press" for op, args in calls if op == "call") == 1


@pytest.mark.asyncio
async def test_semantic_action_rejects_mixed_or_stale_lease(monkeypatch):
    async def fake_rpc(operation, arguments=None, **_kwargs):
        if operation == "connect":
            return {"status": "success", "session_id": "app_session_test", "target": {}, "semantic_profile": {}}
        return {
            "status": "success", "snapshot_revision": 2,
            "nodes": [{"ref": "e1", "role": "button", "actions": ["press"]}],
        }

    monkeypatch.setattr(app_semantic, "electron_app_rpc", fake_rpc)
    await app_semantic.execute_snapshot({"operation": "connect", "target_id": "target_1"})
    snapshot = await app_semantic.execute_snapshot({"operation": "snapshot", "session_id": "app_session_test"})
    node = snapshot["nodes"][0]
    result = await app_semantic.execute_action("click", {
        "session_id": "app_session_test", "snapshot_id": snapshot["snapshot_id"], "revision": 3,
        "node_id": node["node_id"], "action_id": node["actions"][0]["action_id"],
        "reason": "test conflict", "idempotency_key": "conflict-123",
    })
    assert result["type"] == "revision_conflict"


@pytest.mark.asyncio
async def test_snapshot_missing_session_id_is_invalid_arguments_not_stale_session(monkeypatch):
    async def unexpected_rpc(*_args, **_kwargs):
        raise AssertionError("missing session_id must fail before RPC")

    monkeypatch.setattr(app_semantic, "electron_app_rpc", unexpected_rpc)
    result = await app_semantic.execute_snapshot({"operation": "snapshot"})
    assert result == {
        "status": "error",
        "type": "invalid_arguments",
        "message": "session_id is required for AppUISnapshot operation=snapshot.",
        "missing_arguments": ["session_id"],
        "next_valid_actions": ["retry_with_session_id"],
    }


@pytest.mark.asyncio
async def test_inspect_forwards_supported_depth_limits(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_rpc(operation, arguments=None, **_kwargs):
        arguments = arguments or {}
        calls.append((operation, arguments))
        if operation == "connect":
            return {
                "status": "success", "session_id": "app_session_inspect", "target": {},
                "semantic_profile": {"status": "available"},
            }
        if operation == "call" and arguments.get("capability") == "snapshot":
            return {
                "status": "success", "snapshot_revision": 1,
                "nodes": [{"ref": "e1", "role": "Group", "name": "Editor", "actions": []}],
            }
        if operation == "call" and arguments.get("capability") == "inspect":
            return {
                "status": "success", "snapshot_revision": 2,
                "nodes": [{"ref": "e1/e1", "role": "Button", "name": "Run", "actions": ["press"]}],
            }
        raise AssertionError((operation, arguments))

    monkeypatch.setattr(app_semantic, "electron_app_rpc", fake_rpc)
    await app_semantic.execute_snapshot({"operation": "connect", "target_id": "target_1"})
    snapshot = await app_semantic.execute_snapshot({"operation": "snapshot", "session_id": "app_session_inspect"})
    result = await app_semantic.execute_inspect({
        "session_id": "app_session_inspect", "snapshot_id": snapshot["snapshot_id"],
        "revision": snapshot["revision"], "node_id": snapshot["nodes"][0]["node_id"],
        "max_nodes": 60, "max_depth": 6,
    })
    inspect_call = next(args for operation, args in calls if operation == "call" and args.get("capability") == "inspect")
    assert inspect_call["parameters"] == {"ref": "e1", "max_nodes": 60, "max_depth": 6}
    assert result["nodes"][0]["name"] == "Run"


@pytest.mark.asyncio
async def test_public_semantic_nodes_expose_expandability(monkeypatch):
    async def fake_rpc(operation, arguments=None, **_kwargs):
        if operation == "connect":
            return {"status": "success", "session_id": "expandable", "target": {}, "semantic_profile": {}}
        return {
            "status": "success", "snapshot_revision": 1,
            "nodes": [{"ref": "e1", "role": "Group", "name": "Toolbar", "childCount": 4, "actions": []}],
        }

    monkeypatch.setattr(app_semantic, "electron_app_rpc", fake_rpc)
    await app_semantic.execute_snapshot({"operation": "connect", "target_id": "target_1"})
    snapshot = await app_semantic.execute_snapshot({"operation": "snapshot", "session_id": "expandable"})
    assert snapshot["nodes"][0]["child_count"] == 4
    assert snapshot["nodes"][0]["expandable"] is True


def test_app_ui_snapshot_schema_exposes_find():
    from cyrene.plugins.builtin.cyrene_desktop.app_ui_snapshot import TOOL_DEF

    properties = TOOL_DEF["function"]["parameters"]["properties"]
    assert properties["operation"]["enum"] == [
        "list_targets", "connect", "snapshot", "reprobe", "find", "status", "disconnect",
    ]
    assert "contains" in properties
    assert "max_results" in properties
    assert "enabled" in properties
    assert "find" in TOOL_DEF["function"]["description"]


@pytest.mark.asyncio
async def test_find_forwards_parameters_and_leases_matched_nodes(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_rpc(operation, arguments=None, **_kwargs):
        arguments = arguments or {}
        calls.append((operation, arguments))
        if operation == "connect":
            return {
                "status": "success", "session_id": "app_session_find", "target": {},
                "semantic_profile": {"status": "available"},
            }
        if operation == "call" and arguments.get("capability") == "find":
            return {
                "status": "success", "snapshot_revision": 4,
                "semantic_profile": {"status": "available"},
                "nodes": [{"ref": "e7", "role": "link", "name": "Cyrene-0.7.10-win-arm64.exe", "actions": ["press"]}],
            }
        if operation == "call" and arguments.get("capability") == "press":
            return {"status": "success", "verification": {"nodes": [{"ref": "e7", "role": "link", "actions": []}]}}
        raise AssertionError((operation, arguments))

    monkeypatch.setattr(app_semantic, "electron_app_rpc", fake_rpc)
    await app_semantic.execute_snapshot({"operation": "connect", "target_id": "target_1"})
    found = await app_semantic.execute_snapshot({
        "operation": "find", "session_id": "app_session_find",
        "contains": "arm64", "max_results": 20,
    })
    find_call = next(args for operation, args in calls if operation == "call" and args.get("capability") == "find")
    assert find_call["parameters"] == {"contains": "arm64", "max_results": 20}
    node = found["nodes"][0]
    assert node["name"] == "Cyrene-0.7.10-win-arm64.exe"
    clicked = await app_semantic.execute_action("click", {
        "session_id": "app_session_find", "snapshot_id": found["snapshot_id"],
        "revision": found["revision"], "node_id": node["node_id"], "action_id": node["actions"][0]["action_id"],
        "reason": "download the installer", "idempotency_key": "find-click-123",
    })
    assert clicked["status"] == "success"
    assert clicked["effect_verified"] is True


@pytest.mark.asyncio
async def test_semantic_connect_filters_unreachable_manifest_capabilities(monkeypatch):
    async def fake_rpc(operation, arguments=None, **_kwargs):
        assert operation == "connect"
        return {
            "status": "success", "session_id": "app_session_filter", "target": {},
            "semantic_profile": {"status": "available"},
            "capabilities": [
                {"name": "snapshot"}, {"name": "inspect"}, {"name": "find"},
                {"name": "press"}, {"name": "set_value"}, {"name": "type_text"},
                {"name": "scroll"}, {"name": "semantic_double_click"}, {"name": "semantic_drag"},
                {"name": "wait"},
            ],
        }

    monkeypatch.setattr(app_semantic, "electron_app_rpc", fake_rpc)
    result = await app_semantic.execute_snapshot({"operation": "connect", "target_id": "target-1"})
    names = [item["name"] for item in result["capabilities"]]
    assert "wait" not in names
    assert set(names) == {
        "snapshot", "inspect", "find", "press", "set_value",
        "type_text", "scroll", "semantic_double_click", "semantic_drag",
    }


def test_all_seven_external_semantic_tools_are_registered():
    from cyrene.plugins.builtin.cyrene_desktop import plugin_pack

    concrete = {plugin.name for plugin in plugin_pack.plugins}
    assert {
        "AppUISnapshot", "AppUIInspect", "AppUIClick", "AppUIDoubleClick",
        "AppUIType", "AppUIScroll", "AppUIDrag",
    } <= concrete


@pytest.mark.asyncio
async def test_unavailable_semantic_provider_returns_explicit_visual_handoff(monkeypatch):
    async def fake_rpc(operation, arguments=None, **_kwargs):
        assert operation == "connect"
        assert arguments["parameters"]["mode"] == "semantic"
        return {
            "status": "success", "session_id": "semantic-unavailable",
            "target": {"app_name": "Canvas", "platform": "darwin", "bounds": {"x": 1, "y": 2}},
            "semantic_profile": {"status": "unavailable", "reason": "container_only_tree"},
            "capabilities": [{"name": "snapshot"}],
        }

    monkeypatch.setattr(app_semantic, "electron_app_rpc", fake_rpc)
    result = await app_semantic.execute_snapshot({"operation": "connect", "target_id": "target-1"})
    assert result["alternate_scheme"] == {"tool": "app_use", "operation": "list_targets", "mode": "visual"}
    assert result["next_valid_actions"] == ["disconnect", "switch:visual"]
    assert "bounds" not in result["target"]


@pytest.mark.asyncio
async def test_linux_semantic_failure_does_not_offer_unsupported_visual_scheme(monkeypatch):
    async def fake_rpc(operation, arguments=None, **_kwargs):
        assert operation == "connect"
        return {
            "status": "success", "session_id": "linux-semantic-unavailable",
            "target": {"app_name": "Canvas", "platform": "linux"},
            "semantic_profile": {"status": "unavailable", "reason": "container_only_tree"},
        }

    monkeypatch.setattr(app_semantic, "electron_app_rpc", fake_rpc)
    result = await app_semantic.execute_snapshot({"operation": "connect", "target_id": "target-1"})
    assert "alternate_scheme" not in result
    assert result["next_valid_actions"] == ["disconnect"]


@pytest.mark.asyncio
async def test_partial_generic_snapshot_exposes_coverage_and_immediate_visual_handoff(monkeypatch):
    async def fake_rpc(operation, arguments=None, **_kwargs):
        if operation == "connect":
            return {
                "status": "success", "session_id": "semantic-partial",
                "target": {"app_name": "Electron App", "platform": "darwin"},
                "semantic_profile": {"status": "available"},
            }
        assert operation == "call"
        return {
            "status": "success", "snapshot_revision": 3,
            "semantic_profile": {"status": "partial", "reason": "generic_or_unlabeled_actions"},
            "semantic_coverage": {
                "grade": "partial", "generic_or_unlabeled_actionable_nodes": 2,
                "visual_recommended": True,
            },
            "nodes": [{"ref": "e1", "role": "Group", "description": "组", "actions": ["press"]}],
        }

    monkeypatch.setattr(app_semantic, "electron_app_rpc", fake_rpc)
    connected = await app_semantic.execute_snapshot({"operation": "connect", "target_id": "target-1"})
    result = await app_semantic.execute_snapshot({"operation": "snapshot", "session_id": connected["session_id"]})
    assert result["semantic_coverage"]["generic_or_unlabeled_actionable_nodes"] == 2
    assert result["visual_recommended"] is True
    assert result["alternate_scheme"] == {"tool": "app_use", "operation": "list_targets", "mode": "visual"}
    assert result["next_valid_actions"] == ["disconnect", "switch:visual"]
