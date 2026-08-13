from __future__ import annotations

import pytest

from cyrene.tooling.backends import app_semantic


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


def test_all_seven_external_semantic_tools_are_registered():
    from cyrene.tool_impl import NATIVE_TOOL_MODULES
    from cyrene.tooling.packs import CAPABILITY_BINDINGS

    modules = {name.rsplit(".", 1)[-1] for name in NATIVE_TOOL_MODULES}
    assert {
        "app_ui_snapshot", "app_ui_inspect", "app_ui_click", "app_ui_double_click",
        "app_ui_type", "app_ui_scroll", "app_ui_drag",
    } <= modules
    concrete = {tool for _, tool in CAPABILITY_BINDINGS["desktop_tools"]}
    assert {
        "AppUISnapshot", "AppUIInspect", "AppUIClick", "AppUIDoubleClick",
        "AppUIType", "AppUIScroll", "AppUIDrag",
    } <= concrete
