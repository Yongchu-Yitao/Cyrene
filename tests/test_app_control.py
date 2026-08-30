from __future__ import annotations
from conftest import workbench_chat_source, workbench_shell_source

import json
from pathlib import Path

import pytest


def _plugin_context(
    *,
    session_id: str = "origin",
    round_id: str = "round-1",
    client_request_id: str = "request-1",
    conversation_source: str = "desktop_local",
    ui_instance_id: str = "surface-main",
    permission_user_request: str = "",
    permission_service=None,
):
    from cyrene.core.plugin import PluginContext

    return PluginContext(
        data={
            "run_context": {
                "agent_id": "main",
                "caller": "main_agent",
                "client_request_id": client_request_id,
                "round_id": round_id,
                "session_id": session_id,
                "conversation_source": conversation_source,
                "ui_instance_id": ui_instance_id,
                "permission_user_request": permission_user_request,
            }
        },
        services=(
            {"permission": permission_service}
            if permission_service is not None
            else {}
        ),
    )


def _bind_plugin_context(context, *, name: str = "test_plugin"):
    from cyrene.core.plugin import PluginCall
    from cyrene.core.plugin.execution import bind_plugin_execution

    return bind_plugin_execution(
        object(),
        PluginCall(name=name, arguments={}),
        context,
    )


def test_operation_and_ui_action_manifests_are_classified():
    from cyrene.workbench.application.app_operations import OPERATION_BY_ID, validate_manifest
    from cyrene.workbench.ui.ui_actions import validate_ui_action_ledger

    assert validate_manifest() == ()
    assert validate_ui_action_ledger() == ()
    assert OPERATION_BY_ID["cyrene.session.message"].risk == "R2"
    assert OPERATION_BY_ID["cyrene.session.message"].exposure == "internal_service"
    assert OPERATION_BY_ID["cyrene.project.manage"].exposure == "internal_service"
    assert OPERATION_BY_ID["cyrene.approval.answer"].risk == "R3"
    assert OPERATION_BY_ID["cyrene.approval.answer"].exposure == "ui_surface"
    assert OPERATION_BY_ID["cyrene.approval.unprompted_self_answer"].risk == "R4"
    assert OPERATION_BY_ID["cyrene.permission.elevate"].exposure == "forbidden"


def test_agent_model_secret_input_is_redacted_from_self_control_audit_payloads():
    from cyrene.workbench.application.app_control import _redact

    payload = {
        "node_id": "model_api_key",
        "input": {"secret_value": "sk-private-model-key"},
    }
    redacted = _redact(payload)

    assert redacted["input"]["secret_value"] == "[REDACTED]"
    assert "sk-private-model-key" not in json.dumps(redacted)


def test_background_business_controls_are_internal_only():
    from cyrene.plugins.builtin.cyrene_application import plugin_pack

    plugins = {plugin.name: plugin for plugin in plugin_pack.plugins}
    assert {
        "CyreneSessionMessage",
        "CyreneProjectControl",
        "CyreneChatControl",
        "CyreneDataControl",
        "CyreneUpdateControl",
        "CyreneLifecycleControl",
    } == {
        name
        for name, plugin in plugins.items()
        if plugin.metadata.get("model_visible") is False
    }


@pytest.mark.asyncio
async def test_explicit_r2_delegation_bypasses_duplicate_human_prompt(monkeypatch):
    from cyrene.workbench.application.app_control import authorize

    class PermissionService:
        prompted = False

        def explicit_delegation_status(self, **_kwargs):
            return "missing"

        def consume_explicit_delegation(self, *, approve_new=False, **_kwargs):
            return 1 if approve_new else 0

        def request_permission(self, **_kwargs):
            self.prompted = True
            return {"status": "awaiting_user"}

    async def approve(**_kwargs):
        return True, "明确授权。"

    monkeypatch.setattr(
        "cyrene.plugins.permission_review.review_user_delegation",
        approve,
    )
    service = PermissionService()
    context = _plugin_context(
        permission_user_request="启用指定的扩展包",
        permission_service=service,
    )
    with _bind_plugin_context(context):
        result = await authorize(
            "cyrene.settings.global",
            {"enabled_plugin_packs": ["demo"]},
            reason="启用用户指定的扩展包",
            delegation_quote="启用指定的扩展包",
        )

    assert result is None
    assert service.prompted is False


@pytest.mark.asyncio
async def test_rejected_r2_delegation_falls_back_to_exact_human_confirmation(monkeypatch):
    from cyrene.workbench.application.app_control import authorize

    class PermissionService:
        def explicit_delegation_status(self, **_kwargs):
            return "missing"

        def consume_explicit_delegation(self, **_kwargs):
            return 0

        def request_permission(self, **kwargs):
            request = kwargs["request"]
            return {
                "status": "awaiting_user",
                "kind": request["kind"],
                "permission": request,
            }

    async def reject(**_kwargs):
        return False, "用户请求不够明确。"

    monkeypatch.setattr(
        "cyrene.plugins.permission_review.review_user_delegation",
        reject,
    )
    context = _plugin_context(
        permission_user_request="看看插件设置",
        permission_service=PermissionService(),
    )
    with _bind_plugin_context(context):
        result = await authorize(
            "cyrene.settings.global",
            {"enabled_plugin_packs": ["demo"]},
            reason="更改插件设置",
            delegation_quote="看看插件设置",
        )

    payload = json.loads(result)
    assert payload["status"] == "awaiting_user"
    assert payload["kind"] == "self_configuration_confirmation"


def test_window_control_schema_requires_argument_bound_idempotency_key():
    from cyrene.plugins.builtin.cyrene_application.window import TOOL_DEF

    function = TOOL_DEF["function"]
    assert function["parameters"]["required"] == ["action", "idempotency_key"]
    assert function["parameters"]["properties"]["idempotency_key"]["minLength"] == 1


@pytest.mark.asyncio
async def test_settings_describe_reports_its_own_operation_id(monkeypatch):
    from cyrene.plugins.builtin.cyrene_application import settings_describe

    monkeypatch.setattr(
        settings_describe,
        "describe",
        lambda _namespace: {"revision": 4, "settings": [], "controls": []},
    )
    result = json.loads(await settings_describe.handler({}, _plugin_context()))

    assert result["status"] == "success"
    assert result["operation_id"] == "cyrene.settings.describe"


def test_tool_output_global_cap_is_configurable_and_unlimited_at_zero(monkeypatch):
    from cyrene import config
    from cyrene.model.messages import truncate

    text = "x" * 20_000
    monkeypatch.setattr(config, "MAX_TOOL_OUTPUT_CHARS", 0)
    assert truncate(text) == text
    monkeypatch.setattr(config, "MAX_TOOL_OUTPUT_CHARS", 10)
    assert truncate(text).startswith("x" * 10 + "\n...[truncated ")


@pytest.mark.asyncio
async def test_desktop_conversation_source_requires_host_verified_owned_surface(monkeypatch):
    from cyrene.platform import host_bridge

    async def electron_surface(_method, _args, **_kwargs):
        return {"ok": True, "hostKind": "electron", "surfaceAvailable": True}

    monkeypatch.setattr(host_bridge, "call_host", electron_surface)
    assert await host_bridge.resolve_conversation_source("surface-1") == "desktop_local"

    async def unverified_surface(_method, _args, **_kwargs):
        return {"ok": True, "hostKind": "electron", "surfaceAvailable": False}

    monkeypatch.setattr(host_bridge, "call_host", unverified_surface)
    assert await host_bridge.resolve_conversation_source("surface-1") == "webui"
    assert await host_bridge.resolve_conversation_source("") == "webui"


def test_settings_patch_is_atomic_revisioned_and_self_pack_is_protected(monkeypatch, tmp_path):
    from cyrene.platform import config_store, settings_service

    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config_store, "_ENCRYPTED_PATH", tmp_path / "config.enc")
    monkeypatch.setattr(config_store, "_KEY_PATH", tmp_path / ".config_key")
    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_fernet", None)
    monkeypatch.setattr(config_store, "_initialized", False)

    first = settings_service.update(
        "runtime",
        {"app_language": "zh", "timezone": "Asia/Shanghai"},
        actor="ui",
        expected_revision=0,
    )
    assert first["revision"] == 1
    before = config_store.get_all_settings()

    with pytest.raises(settings_service.SettingsValidationError):
        settings_service.update(
            "runtime",
            {"app_language": "en", "timezone": "Not/AZone"},
            actor="ui",
            expected_revision=1,
        )
    assert config_store.get_all_settings() == before
    assert config_store.get_settings_revision() == 1

    with pytest.raises(config_store.SettingsRevisionConflict):
        settings_service.update(
            "runtime", {"app_language": "en"},
            actor="ui", expected_revision=0,
        )
    with pytest.raises(settings_service.SettingsForbiddenError):
        settings_service.validate_changes(
            "runtime", {"enabled_plugin_packs": {"cyrene_application": False}},
            actor="agent", approved_risks=frozenset({"R2"}),
        )

    user_saved = settings_service.update(
        "appearance", {"theme": "dark"}, actor="ui", expected_revision=1,
    )
    assert user_saved["revision"] == 2
    agent_saved = settings_service.update(
        "runtime", {"app_language": "en"}, actor="agent", expected_revision=2,
    )
    assert agent_saved["revision"] == 3
    assert settings_service.read_public("appearance")["values"]["theme"] == "dark"
    assert settings_service.read_public("runtime")["values"]["timezone"] == "Asia/Shanghai"

    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_fernet", None)
    assert settings_service.read_public("appearance")["values"]["theme"] == "dark"
    assert settings_service.read_public("runtime")["values"]["app_language"] == "en"


def test_lifecycle_records_revalidation_and_reconciles_only_host_accepted_actions(monkeypatch, tmp_path):
    from cyrene.platform import host_actions

    monkeypatch.setattr(host_actions, "_STATE_PATH", tmp_path / "actions.json")
    context = _plugin_context(
        session_id="session-1",
        round_id="round-1",
        client_request_id="request-1",
    )
    with _bind_plugin_context(context):
        first = host_actions.schedule_action(
            "restart_app",
            idempotency_key="restart-1",
            parameter_hash="a" * 64,
            expected_app_version="0.7.9",
            approval_receipt="delegation_receipt",
        )
        second = host_actions.schedule_action(
            "restart_backend",
            idempotency_key="restart-2",
            parameter_hash="b" * 64,
            expected_app_version="0.7.9",
        )

    assert first["origin_run_id"] == "request-1"
    assert first["approval_fingerprint"] == "delegation_receipt"
    assert first["required_host_kind"] == "electron"
    host_actions._settle(first["action_id"], "executing", "accepted")
    host_actions._settle(second["action_id"], "queued", "")
    host_actions.reconcile_startup()
    terminal = {item["action_id"]: item for item in host_actions.list_actions(include_terminal=True)}
    assert terminal[first["action_id"]]["status"] == "completed"
    assert terminal[second["action_id"]]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_update_install_uses_host_prepare_launch_commit_order(monkeypatch, tmp_path):
    from unittest.mock import MagicMock

    from cyrene.platform import host_actions, update_install, updater

    monkeypatch.setattr(host_actions, "_STATE_PATH", tmp_path / "actions.json")
    monkeypatch.setattr(updater, "get_download_progress", lambda: {
        "done": True,
        "verified": True,
        "actual_sha256": "a" * 64,
        "downloaded": 12,
        "total": 12,
    })
    launched = MagicMock(return_value=(True, "", "", 200))
    monkeypatch.setattr(update_install, "launch_update_restart", launched)
    calls = []

    async def fake_host(method, args=None):
        calls.append((method, dict(args or {})))
        if method == "host.status":
            return {"ok": True, "hostKind": "electron", "appVersion": "0.7.9"}
        return {"ok": True, "summary": str((args or {}).get("phase") or "")}

    monkeypatch.setattr(host_actions, "call_host", fake_host)
    await host_actions._execute({
        "action_id": "host_action_" + "b" * 32,
        "action": "update_install",
        "parameter_hash": "c" * 64,
        "expected_app_version": "0.7.9",
        "required_host_kind": "electron",
        "revalidation": {"sha256": "a" * 64, "size": 12},
    })

    launched.assert_called_once()
    assert [item[1].get("phase") for item in calls[1:]] == ["prepare", "commit"]










@pytest.mark.asyncio
async def test_session_message_cannot_submit_calling_session(monkeypatch):
    from cyrene.plugins.builtin.cyrene_application import session_message

    context = _plugin_context(session_id="same")

    async def fake_host(_method, _args):
        return {
            "ok": True,
            "snapshot_id": "tree-current",
            "revision": 3,
            "root": {
                "node_id": "root",
                "children": [{
                    "node_id": "chat_composer_input",
                    "role": "textbox",
                    "state": {
                        "session_id": "same", "session_kind": "chat",
                        "draft_empty": True, "submit_exposed": False,
                    },
                    "actions": [
                        {"action_id": "set_value"}, {"action_id": "clear_value"},
                    ],
                    "children": [],
                }],
            },
        }

    monkeypatch.setattr(session_message, "call_host", fake_host)
    result = json.loads(await session_message.handler({
        "snapshot_id": "tree-current", "revision": 3,
        "node_id": "chat_composer_input", "message": "hello",
        "reason": "Prepare own draft.", "idempotency_key": "self-1",
    }, context))

    assert result["error_code"] == "self_session_submit_forbidden"


@pytest.mark.asyncio
async def test_application_plugins_reach_current_surface_broker_end_to_end(
    monkeypatch, tmp_path,
):
    from cyrene.plugins.builtin.cyrene_application import ui_click, ui_snapshot
    from cyrene.workbench.application import app_control
    from cyrene.workbench.ui import ui_surface

    monkeypatch.delenv("CYRENE_ELECTRON_RPC_PORT", raising=False)
    monkeypatch.delenv("CYRENE_ELECTRON_RPC_TOKEN", raising=False)
    monkeypatch.setattr(app_control, "_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_control, "_IDEMPOTENCY_PATH", tmp_path / "idempotency.json")

    class FakeSurfaceSocket:
        connection = None

        async def send_json(self, payload):
            if payload["method"] == "snapshot":
                result = {
                    "ok": True,
                    "snapshot_id": "tree-e2e",
                    "revision": 4,
                    "surface": {"kind": "main", "scope": "main"},
                    "root": {
                        "node_id": "root",
                        "children": [{
                            "node_id": "navigation_chat",
                            "role": "navigation_item",
                            "actions": [{
                                "action_id": "open",
                                "kind": "invoke",
                                "risk": "R1",
                            }],
                            "children": [],
                        }],
                    },
                }
            else:
                assert payload["method"] == "act"
                assert payload["args"]["node_id"] == "navigation_chat"
                result = {"ok": True, "revision": 5, "result": {"opened": "chat"}}
            ui_surface.receive(self.connection, {
                "requestId": payload["requestId"],
                "result": result,
            })

    socket = FakeSurfaceSocket()
    socket.connection = await ui_surface.register("surface-e2e", socket)
    context = _plugin_context(
        session_id="origin",
        round_id="round-e2e",
        client_request_id="request-e2e",
        conversation_source="webui",
        ui_instance_id="surface-e2e",
    )
    try:
        with _bind_plugin_context(context, name="CyreneUIClick"):
            read_result = json.loads(await ui_snapshot.handler(
                {"max_depth": 12},
                context,
            ))
            assert read_result["snapshot"]["snapshot_id"] == "tree-e2e"

            act_result = json.loads(await ui_click.handler(
                {
                    "snapshot_id": "tree-e2e",
                    "revision": 4,
                    "node_id": "navigation_chat",
                    "action_id": "open",
                    "reason": "Open the user-visible Chat surface.",
                    "idempotency_key": "gateway-e2e-open-chat",
                },
                context,
            ))
            assert act_result["status"] == "success"
            assert act_result["revision"] == 5
    finally:
        await ui_surface.unregister("surface-e2e", socket.connection)


@pytest.mark.asyncio
async def test_ui_inspect_uses_target_node_lease_across_unrelated_revision_changes(
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_application import _ui_snapshot, ui_inspect

    calls = []

    async def fake_host(method, args):
        calls.append((method, dict(args)))
        assert method == "ui.snapshot.current"
        assert args["snapshot_id"] == "tree-inspect"
        assert args["revision"] == 4
        assert args["parent_node_id"] == "chat_composer_input"
        assert args["allow_compatible_node"] is True
        assert args["_agent_cursor_mode"] == "inspect"
        return {
            "ok": True,
            "snapshot_id": "tree-inspect",
            "revision": 9,
            "requested_revision_compatible": True,
            "root": {
                "node_id": "chat_composer_input",
                "role": "textbox",
                "name": "Message Cyrene...",
                "actions": [{"action_id": "set_value", "kind": "set_value"}],
                "children": [],
            },
        }

    monkeypatch.setattr(_ui_snapshot, "call_host", fake_host)
    context = _plugin_context(
        round_id="round-inspect",
        client_request_id="request-inspect",
        ui_instance_id="surface-inspect",
    )
    result = json.loads(await ui_inspect.handler({
        "snapshot_id": "tree-inspect",
        "revision": 4,
        "node_id": "chat_composer_input",
        "include": ["interactive", "text"],
        "max_depth": 3,
    }, context))

    assert result["status"] == "success"
    assert result["revision"] == 9
    assert result["snapshot"]["requested_revision_compatible"] is True
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "operation_family", "action_kind"),
    [
        ("ui_type", "cyrene.ui.type", "set_value"),
        ("ui_scroll", "cyrene.ui.scroll", "scroll"),
    ],
)
async def test_non_pointer_ui_actions_still_move_cursor_to_their_target(
    monkeypatch, tmp_path, module_name, operation_family, action_kind,
):
    from importlib import import_module

    from cyrene.plugins.builtin.cyrene_application import _ui_action
    from cyrene.workbench.application import app_control

    module = import_module(f"cyrene.plugins.builtin.cyrene_application.{module_name}")
    monkeypatch.setattr(app_control, "_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_control, "_IDEMPOTENCY_PATH", tmp_path / "idempotency.json")
    calls = []

    async def fake_host(method, args):
        calls.append((method, dict(args)))
        if method == "ui.snapshot.current":
            return {
                "ok": True,
                "snapshot_id": f"tree-{module_name}",
                "revision": 3,
                "root": {
                    "node_id": "target",
                    "actions": [{
                        "action_id": "perform",
                        "kind": action_kind,
                        "risk": "R1",
                    }],
                    "children": [],
                },
            }
        return {"ok": True, "revision": 4}

    monkeypatch.setattr(_ui_action, "call_host", fake_host)
    context = _plugin_context(
        round_id=f"round-{module_name}",
        client_request_id=f"request-{module_name}",
    )
    with _bind_plugin_context(context, name=module.TOOL_NAME):
        result = json.loads(await module.handler({
            "snapshot_id": f"tree-{module_name}",
            "revision": 3,
            "node_id": "target",
            "action_id": "perform",
            "input": {},
            "reason": "Perform the requested visible action.",
            "idempotency_key": f"cursor-{module_name}",
        }, context))

    assert result["status"] == "success"
    assert operation_family in result["operation_id"]
    execute_args = next(
        args for method, args in calls
        if method == "ui.gesture.execute_current"
    )
    assert execute_args["_agent_cursor_mode"] == "target"


@pytest.mark.asyncio
async def test_ui_snapshot_exposes_visible_and_calling_session_mismatch(monkeypatch):
    from cyrene.plugins.builtin.cyrene_application import _ui_snapshot, ui_snapshot

    async def fake_host(method, _args):
        assert method == "ui.snapshot.current"
        return {
            "ok": True,
            "snapshot_id": "tree-other-session",
            "revision": 5,
            "surface": {
                "kind": "main",
                "scope": "main",
                "visible_session_id": "visible-chat",
                "visible_session_kind": "chat",
            },
            "root": {"node_id": "root", "children": []},
        }

    monkeypatch.setattr(_ui_snapshot, "call_host", fake_host)
    context = _plugin_context(
        session_id="calling-chat",
        round_id="round-mismatch",
        client_request_id="request-mismatch",
    )
    result = json.loads(await ui_snapshot.handler({}, context))

    assert result["snapshot"]["surface"] == {
        "kind": "main",
        "scope": "main",
        "visible_session_id": "visible-chat",
        "visible_session_kind": "chat",
        "calling_session_id": "calling-chat",
        "session_relation": "different",
    }


@pytest.mark.asyncio
async def test_ui_action_exact_retry_replays_before_reading_new_tree(
    monkeypatch, tmp_path,
):
    from cyrene.plugins.builtin.cyrene_application import _ui_action, ui_click
    from cyrene.workbench.application import app_control

    monkeypatch.setattr(app_control, "_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_control, "_IDEMPOTENCY_PATH", tmp_path / "idempotency.json")
    calls = []

    async def fake_host(method, args):
        calls.append((method, dict(args)))
        if method == "ui.snapshot.current":
            return {
                "ok": True, "snapshot_id": "tree-retry", "revision": 2,
                "root": {"node_id": "root", "children": [{
                    "node_id": "open_search", "actions": [{
                        "action_id": "open", "kind": "invoke", "risk": "R1",
                    }], "children": [],
                }]},
            }
        return {"ok": True, "revision": 3}

    monkeypatch.setattr(_ui_action, "call_host", fake_host)
    context = _plugin_context(
        round_id="round-retry",
        client_request_id="request-retry",
        conversation_source="webui",
        ui_instance_id="surface-retry",
    )
    args = {
        "snapshot_id": "tree-retry", "revision": 2,
        "node_id": "open_search", "action_id": "open",
        "reason": "Open search.", "idempotency_key": "retry-open-search",
    }
    with _bind_plugin_context(context, name="CyreneUIClick"):
        first = json.loads(await ui_click.handler(args, context))

        async def should_not_call_host(_method, _args):
            raise AssertionError("an exact idempotent retry must not touch the changed surface")

        monkeypatch.setattr(_ui_action, "call_host", should_not_call_host)
        second = json.loads(await ui_click.handler(args, context))

    assert first["status"] == "success"
    assert second == first
    assert [method for method, _args in calls] == [
        "ui.snapshot.current", "ui.gesture.execute_current",
    ]
    assert calls[1][1]["_agent_cursor_mode"] == "click"


@pytest.mark.asyncio
async def test_ui_action_accepts_unchanged_node_lease_across_global_revision(
    monkeypatch, tmp_path,
):
    from cyrene.plugins.builtin.cyrene_application import _ui_action, ui_click
    from cyrene.workbench.application import app_control

    monkeypatch.setattr(app_control, "_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_control, "_IDEMPOTENCY_PATH", tmp_path / "idempotency.json")
    calls = []

    async def fake_host(method, args):
        calls.append((method, dict(args)))
        if method == "ui.snapshot.current":
            assert args["snapshot_id"] == "tree-compatible"
            assert args["revision"] == 7
            assert args["action_id"] == "invoke"
            assert args["allow_compatible_action"] is True
            return {
                "ok": True,
                "snapshot_id": "tree-compatible",
                "revision": 12,
                "requested_revision_compatible": True,
                "root": {
                    "node_id": "new_chat",
                    "actions": [{
                        "action_id": "invoke", "kind": "invoke", "risk": "R1",
                    }],
                    "children": [],
                },
            }
        return {"ok": True, "revision": 13}

    monkeypatch.setattr(_ui_action, "call_host", fake_host)
    context = _plugin_context(
        round_id="round-compatible",
        client_request_id="request-compatible",
        ui_instance_id="surface-compatible",
    )
    with _bind_plugin_context(context, name="CyreneUIClick"):
        result = json.loads(await ui_click.handler({
            "snapshot_id": "tree-compatible",
            "revision": 7,
            "node_id": "new_chat",
            "action_id": "invoke",
            "reason": "Create the requested visible chat.",
            "idempotency_key": "compatible-new-chat",
        }, context))

    assert result["status"] == "success"
    assert result["revision"] == 13
    assert [method for method, _args in calls] == [
        "ui.snapshot.current", "ui.gesture.execute_current",
    ]
    assert calls[1][1]["_agent_cursor_mode"] == "click"


@pytest.mark.asyncio
async def test_ui_double_click_requires_declared_double_press_and_maximizes_browser(
    monkeypatch, tmp_path,
):
    from cyrene.plugins.builtin.cyrene_application import _ui_action, ui_double_click
    from cyrene.workbench.application import app_control

    monkeypatch.setattr(app_control, "_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_control, "_IDEMPOTENCY_PATH", tmp_path / "idempotency.json")
    declared_aliases = ["double_press", "maximize_button"]
    calls = []

    async def fake_host(method, args):
        calls.append((method, dict(args)))
        if method == "ui.snapshot.current":
            return {
                "ok": True,
                "snapshot_id": "tree-browser",
                "revision": 4,
                "requested_revision_compatible": True,
                "root": {
                    "node_id": "browser_window_titlebar",
                    "actions": [{
                        "action_id": "maximize",
                        "kind": "invoke",
                        "risk": "R1",
                        "gesture_aliases": declared_aliases,
                    }],
                    "children": [],
                },
            }
        return {"ok": True, "revision": 5, "result": {"mode": "maximized"}}

    monkeypatch.setattr(_ui_action, "call_host", fake_host)
    context = _plugin_context(
        round_id="round-double-click",
        client_request_id="request-double-click",
        ui_instance_id="surface-double-click",
    )
    base_args = {
        "snapshot_id": "tree-browser",
        "revision": 4,
        "node_id": "browser_window_titlebar",
        "action_id": "maximize",
        "reason": "Double-click the Browser titlebar to maximize it.",
    }
    with _bind_plugin_context(context, name="CyreneUIDoubleClick"):
        success = json.loads(await ui_double_click.handler({
            **base_args,
            "idempotency_key": "double-click-browser-maximize",
        }, context))
        declared_aliases[:] = ["press", "keyboard"]
        rejected = json.loads(await ui_double_click.handler({
            **base_args,
            "idempotency_key": "double-click-ordinary-button",
        }, context))

    assert success["status"] == "success"
    assert success["operation_id"] == "cyrene.ui.double_click"
    assert rejected["status"] == "error"
    assert rejected["error_code"] == "gesture_not_available"
    assert [method for method, _args in calls].count("ui.gesture.execute_current") == 1
    assert next(args for method, args in calls if method == "ui.gesture.execute_current")["_agent_cursor_mode"] == "click"


@pytest.mark.asyncio
async def test_desktop_settings_describe_uses_electron_cas_revision(monkeypatch):
    from cyrene.plugins.builtin.cyrene_application import settings_describe

    async def fake_host(method, args):
        assert (method, args) == ("desktop.settings.get", {})
        return {"ok": True, "settings": {
            "settingsRevision": 17,
            "quickChatEnabled": False,
        }}

    monkeypatch.setattr(settings_describe, "call_host", fake_host)
    result = json.loads(await settings_describe.handler(
        {"namespace": "desktop"}, _plugin_context(),
    ))
    assert result["revision"] == 17
    assert result["schema"]["revision"] == 17


@pytest.mark.asyncio
async def test_lifecycle_finalization_is_scoped_to_origin_request(
    monkeypatch, tmp_path,
):
    from cyrene.platform import host_actions

    monkeypatch.setattr(host_actions, "_STATE_PATH", tmp_path / "actions.json")
    executed = []

    async def fake_execute(item):
        executed.append(item["origin_run_id"])

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(host_actions, "_execute", fake_execute)
    monkeypatch.setattr(host_actions.asyncio, "sleep", no_sleep)
    for run_id, key, digest in (
        ("request-a", "lifecycle-a", "a" * 64),
        ("request-b", "lifecycle-b", "b" * 64),
    ):
        context = _plugin_context(
            session_id="same-session",
            round_id=f"round-{run_id}",
            client_request_id=run_id,
        )
        with _bind_plugin_context(context):
            host_actions.schedule_action(
                "restart_backend",
                idempotency_key=key,
                parameter_hash=digest,
                expected_app_version="0.7.9",
            )

    await host_actions.finalize_origin(
        "same-session", "", origin_run_id="request-a",
    )
    pending = host_actions.list_actions()
    assert executed == ["request-a"]
    assert {item["origin_run_id"]: item["status"] for item in pending} == {
        "request-a": "queued",
        "request-b": "waiting_for_run_finalization",
    }
