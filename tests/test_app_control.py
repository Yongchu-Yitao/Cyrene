from __future__ import annotations

import json
from pathlib import Path

import pytest


def _bind_local_delegation(user_request: str, *, session_id: str = "origin"):
    from cyrene.agent import state
    from cyrene.agent.context import bind_run_context

    binding = bind_run_context(
        agent_id="main",
        caller="main_agent",
        client_request_id="request-1",
        round_id="round-1",
        session_id=session_id,
        conversation_source="desktop_local",
        user_request_text=user_request,
    )
    receipt_token = state._explicit_delegation_receipts.set(set())
    batch_token = state._explicit_delegation_batches.set({})
    return binding, (receipt_token, batch_token)


def _reset_local_delegation(state, tokens):
    receipt_token, batch_token = tokens
    state._explicit_delegation_batches.reset(batch_token)
    state._explicit_delegation_receipts.reset(receipt_token)


async def _approved_delegation():
    return True, "用户明确要求执行该精确操作。"


def test_operation_and_ui_action_manifests_are_classified():
    from cyrene.workbench.app_operations import OPERATION_BY_ID, validate_manifest
    from cyrene.workbench.ui_actions import validate_ui_action_ledger

    assert validate_manifest() == ()
    assert validate_ui_action_ledger() == ()
    assert OPERATION_BY_ID["cyrene.session.message"].risk == "R2"
    assert OPERATION_BY_ID["cyrene.session.message"].exposure == "internal_service"
    assert OPERATION_BY_ID["cyrene.project.manage"].exposure == "internal_service"
    assert OPERATION_BY_ID["cyrene.approval.answer"].risk == "R3"
    assert OPERATION_BY_ID["cyrene.approval.answer"].exposure == "ui_surface"
    assert OPERATION_BY_ID["cyrene.approval.unprompted_self_answer"].risk == "R4"
    assert "cyrene.settings.model" not in OPERATION_BY_ID


def test_background_business_controls_are_internal_only():
    from cyrene.tooling.catalog import capabilities_for_pack, get_active_tool_defs_for_actor
    from cyrene.tooling.packs import INTERNAL_ONLY_CONCRETE_TOOL_NAMES

    exposed = [
        item.capability_id
        for item in capabilities_for_pack("cyrene_tools", include_disabled=True)
    ]
    assert exposed == [
        "cyrene.app.status",
        "cyrene.app.window",
        "cyrene.ui.snapshot",
        "cyrene.ui.inspect",
        "cyrene.ui.click",
        "cyrene.ui.double_click",
        "cyrene.ui.type",
        "cyrene.ui.scroll",
        "cyrene.ui.drag",
        "cyrene.settings.describe",
        "cyrene.settings.read",
        "cyrene.settings.update",
    ]
    active_names = {
        item["function"]["name"]
        for item in get_active_tool_defs_for_actor("main")
    }
    assert active_names.isdisjoint(INTERNAL_ONLY_CONCRETE_TOOL_NAMES)


def test_current_tree_exposes_project_switch_chat_search_and_shared_pip_maximize_handler():
    root = Path(__file__).resolve().parents[1]
    workbench = (root / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    chat = (root / "src/webui/frontend/workbench-chat.jsx").read_text(encoding="utf-8")
    model = (root / "src/webui/frontend/workbench-model.jsx").read_text(encoding="utf-8")
    welcome = (root / "src/webui/frontend/workbench-welcome.jsx").read_text(encoding="utf-8")
    ui_surface = (root / "src/webui/frontend/platform/ui-surface.jsx").read_text(encoding="utf-8")
    electron = (root / "electron/main.js").read_text(encoding="utf-8")

    assert 'node_id: "project_switcher"' in workbench
    assert 'scope: "project_menu"' in workbench
    assert 'return onSelectProject && onSelectProject(projectId);' in workbench
    assert 'node_id: "chat_search_input"' in chat
    assert 'set_value: function (input) { setQuery(String(input.value || "")); }' in chat
    assert 'node_id: "new_chat"' in chat
    registration_block = chat.split('node_id: "new_chat"', 1)[0].rsplit("useWbcEffect(function ()", 1)[-1]
    assert "if (!collapsed)" not in registration_block
    assert 'node_id: "chat_list"' in chat
    assert 'node_id: "chat_composer_submit"' in chat
    assert 'node_id: "task_composer_submit"' in workbench
    assert 'get_element: function () { return sendButtonRef.current; }' in workbench
    assert 'target_scope: "task"' in workbench
    composer_source = chat.split("function WbcComposer", 1)[1]
    user_message_source = chat.split("function WbcUserMessage", 1)[1].split("function WbcAssistantMessage", 1)[0]
    assert "var sendButtonRef = useWbcRef(null);" in composer_source
    assert "sendButtonRef" not in user_message_source
    assert 'var submitRisk = submitMode === "interrupt" ? "R1" : "R2";' in chat
    assert 'get_element: function () { return sendButtonRef.current; }' in chat
    assert 'submit_exposed: !sendDisabled' in chat
    assert 'submit_exposed: false' not in chat
    assert 'wbcViewportChatIds(chatListRef.current)' in chat
    assert 'filtered.slice(0, 80)' not in chat
    assert 'action_id: "page_previous"' in chat
    assert 'action_id: "page_next"' in chat
    assert 'data-cyrene-node-id="chat_context_menu"' in chat
    assert 'requires_capability: "cyrene.chat.manage"' not in chat
    assert 'setRenameChat(menuChat);' in chat
    assert 'return onDelete && onDelete(menuChatId);' in chat
    assert 'data-cyrene-risk="R2"' in chat
    assert '[role="alertdialog"][aria-modal="true"]' in (
        root / "src/webui/frontend/platform/ui-surface.jsx"
    ).read_text(encoding="utf-8")
    assert 'onDoubleClick={effectiveMode === "pip" ? maximizeBrowserWindow : undefined}' in chat
    assert 'gesture_aliases: ["double_press", "maximize_button"]' in chat
    assert 'maximize: maximizeBrowserWindow' in chat
    assert model.count("uiInstanceId: currentUiInstanceId()") == 4
    assert 'clientRequestId: options.clientRequestId || newClientRequestId("task_dispatch")' in model
    assert 'data-cyrene-node-id="onboarding"' in welcome
    assert 'data-cyrene-node-id="onboarding_base_url"' in welcome
    assert 'data-cyrene-node-id="onboarding_model"' in welcome
    assert 'aria-pressed={llmSource === "custom"}' in welcome
    assert 'aria-pressed={llmSource === "codex"}' in welcome
    assert 'domNodeIds.set(element, explicitNodeId)' in ui_surface
    assert 'return explicitNodeId;' in ui_surface
    assert '"disabled", "checked", "pressed", "selected"' in ui_surface
    assert 'pressed: element.getAttribute("aria-pressed") == null' in ui_surface
    assert "isDesktopOnboardingTree(candidate)" in electron
    assert "runDesktopOnboardingSmokeTest(window, uiInstanceId, tree)" in electron
    assert "'onboarding custom model endpoint after scroll'" in electron
    assert "'onboarding model field after scroll'" in electron


def test_window_control_schema_requires_argument_bound_idempotency_key():
    from cyrene.tool_impl.application.window import TOOL_DEF

    function = TOOL_DEF["function"]
    assert function["parameters"]["required"] == ["action", "idempotency_key"]
    assert function["parameters"]["properties"]["idempotency_key"]["minLength"] == 1


@pytest.mark.asyncio
async def test_settings_describe_reports_its_own_operation_id(monkeypatch):
    from cyrene.tool_impl.application import settings_describe

    monkeypatch.setattr(
        settings_describe,
        "describe",
        lambda _namespace: {"revision": 4, "settings": [], "controls": []},
    )
    result = json.loads(await settings_describe.handler({}, None, 0, "", None))

    assert result["status"] == "success"
    assert result["operation_id"] == "cyrene.settings.describe"


def test_tool_output_global_cap_is_configurable_and_unlimited_at_zero(monkeypatch):
    from cyrene import config
    from cyrene.model_runtime.messages import truncate

    text = "x" * 20_000
    monkeypatch.setattr(config, "MAX_TOOL_OUTPUT_CHARS", 0)
    assert truncate(text) == text
    monkeypatch.setattr(config, "MAX_TOOL_OUTPUT_CHARS", 10)
    assert truncate(text).startswith("x" * 10 + "\n...[truncated ")


@pytest.mark.asyncio
async def test_desktop_conversation_source_requires_host_verified_owned_surface(monkeypatch):
    from cyrene.runtime import host_bridge

    async def electron_surface(_method, _args):
        return {"ok": True, "hostKind": "electron", "surfaceAvailable": True}

    monkeypatch.setattr(host_bridge, "call_host", electron_surface)
    assert await host_bridge.resolve_conversation_source("surface-1") == "desktop_local"

    async def unverified_surface(_method, _args):
        return {"ok": True, "hostKind": "electron", "surfaceAvailable": False}

    monkeypatch.setattr(host_bridge, "call_host", unverified_surface)
    assert await host_bridge.resolve_conversation_source("surface-1") == "webui"
    assert await host_bridge.resolve_conversation_source("") == "webui"


def test_settings_patch_is_atomic_revisioned_and_self_pack_is_protected(monkeypatch, tmp_path):
    from cyrene.runtime import config_store, settings_service

    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config_store, "_ENCRYPTED_PATH", tmp_path / "config.enc")
    monkeypatch.setattr(config_store, "_KEY_PATH", tmp_path / ".config_key")
    monkeypatch.setattr(config_store, "_LEGACY_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(config_store, "_LEGACY_SETTINGS_PATH", tmp_path / "web_settings.json")
    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_migrated", False)
    monkeypatch.setattr(config_store, "_fernet", None)

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
            "runtime", {"enabled_tool_packs": {"cyrene_tools": False}},
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


def test_non_model_settings_registry_covers_every_tab_and_shortcuts_are_versioned(monkeypatch, tmp_path):
    from cyrene.runtime import config_store, settings_service

    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config_store, "_ENCRYPTED_PATH", tmp_path / "config.enc")
    monkeypatch.setattr(config_store, "_KEY_PATH", tmp_path / ".config_key")
    monkeypatch.setattr(config_store, "_LEGACY_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(config_store, "_LEGACY_SETTINGS_PATH", tmp_path / "web_settings.json")
    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_migrated", False)
    monkeypatch.setattr(config_store, "_fernet", None)

    schema = settings_service.describe()
    assert schema["excluded_tabs"] == ["models"]
    assert set(schema["covered_tabs"]) == set(settings_service.NON_MODEL_SETTINGS_TABS)
    assert {item["tab"] for item in schema["controls"]} == set(settings_service.NON_MODEL_SETTINGS_TABS)
    assert all(item["tab"] in settings_service.NON_MODEL_SETTINGS_TABS for item in schema["settings"])
    assert "models" not in {item["tab"] for item in schema["settings"] + schema["controls"]}
    serialized_schema = json.dumps(schema, sort_keys=True)
    assert "cyrene.data.manage" not in serialized_schema
    assert "cyrene.update.manage" not in serialized_schema
    assert set(schema["shortcut_defaults"]) == set(settings_service.SHORTCUT_DEFAULTS)
    assert len(schema["shortcut_defaults"]) == 15
    assert settings_service.describe("shortcuts")["covered_tabs"] == ["shortcuts"]

    user_saved = settings_service.update(
        "shortcuts",
        {"shortcut_bindings": {"new-chat": ["mod", "U"]}},
        actor="ui",
        expected_revision=0,
    )
    assert user_saved["revision"] == 1

    # A process-style cache reload must read the encrypted value back from disk.
    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_fernet", None)
    assert settings_service.read_public("shortcuts")["values"]["shortcut_bindings"] == {
        "new-chat": ["mod", "U"],
    }

    agent_saved = settings_service.update(
        "shortcuts",
        {"shortcut_bindings": {"search": ["mod", "J"]}},
        actor="agent",
        expected_revision=1,
        approved_risks=frozenset({"R2"}),
    )
    assert agent_saved["revision"] == 2
    assert settings_service.read_public("shortcuts")["values"]["shortcut_bindings"] == {
        "search": ["mod", "J"],
        "new-chat": ["mod", "U"],
    }

    # A stale Agent write is rejected and cannot erase the user's saved value.
    with pytest.raises(config_store.SettingsRevisionConflict):
        settings_service.update(
            "shortcuts",
            {"shortcut_bindings": {"settings": ["mod", "S"]}},
            actor="agent",
            expected_revision=1,
            approved_risks=frozenset({"R2"}),
        )
    assert settings_service.read_public("shortcuts")["values"]["shortcut_bindings"]["new-chat"] == ["mod", "U"]

    reset_one = settings_service.update(
        "shortcuts",
        {"shortcut_bindings": {"search": None}},
        actor="agent",
        expected_revision=2,
        approved_risks=frozenset({"R2"}),
    )
    assert reset_one["revision"] == 3
    assert settings_service.read_public("shortcuts")["values"]["shortcut_bindings"] == {
        "new-chat": ["mod", "U"],
    }

    with pytest.raises(settings_service.SettingsValidationError, match="conflicts"):
        settings_service.update(
            "shortcuts",
            {"shortcut_bindings": {"search": ["mod", "J"], "new-chat": ["mod", "J"]}},
            actor="ui",
            expected_revision=3,
        )


def test_lifecycle_records_revalidation_and_reconciles_only_host_accepted_actions(monkeypatch, tmp_path):
    from cyrene.agent.context import bind_run_context
    from cyrene.runtime import host_actions

    monkeypatch.setattr(host_actions, "_STATE_PATH", tmp_path / "actions.json")
    binding = bind_run_context(
        session_id="session-1", round_id="round-1",
        client_request_id="request-1",
    )
    try:
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
    finally:
        binding.reset()

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

    from cyrene.runtime import host_actions, updater
    from cyrene.workbench import runtime as workbench_runtime

    monkeypatch.setattr(host_actions, "_STATE_PATH", tmp_path / "actions.json")
    monkeypatch.setattr(updater, "get_download_progress", lambda: {
        "done": True,
        "verified": True,
        "actual_sha256": "a" * 64,
        "downloaded": 12,
        "total": 12,
    })
    launched = MagicMock(return_value=(True, "", "", 200))
    monkeypatch.setattr(workbench_runtime, "_launch_update_restart", launched)
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
async def test_explicit_local_delegation_is_exact_and_single_use(monkeypatch):
    from cyrene.agent import auto_review
    from cyrene.agent import state
    from cyrene.tooling.policy import approvals
    from cyrene.workbench.app_control import authorize

    quote = "现在重启 Cyrene"
    binding, token = _bind_local_delegation(quote)
    requested = []

    async def fallback(**_kwargs):
        requested.append(True)
        return "approval-required"

    async def approve_delegation(**_kwargs):
        return True, "用户明确要求执行该精确操作。"

    monkeypatch.setattr(auto_review, "review_user_delegation", approve_delegation)
    monkeypatch.setattr(approvals, "request_host_lifecycle_confirmation", fallback)
    # app_control imported the policy function directly.
    monkeypatch.setattr("cyrene.workbench.app_control.request_host_lifecycle_confirmation", fallback)
    try:
        first = await authorize(
            "cyrene.app.lifecycle",
            {"action": "restart_app"},
            reason="User requested restart.",
            delegation_quote=quote,
        )
        second = await authorize(
            "cyrene.app.lifecycle",
            {"action": "restart_backend"},
            reason="User requested another restart.",
            delegation_quote=quote,
        )
    finally:
        _reset_local_delegation(state, token)
        binding.reset()

    assert first is None
    assert second == "approval-required"
    assert requested == [True]


@pytest.mark.asyncio
async def test_local_round_without_client_request_id_reviews_full_user_request(monkeypatch):
    from cyrene.agent import auto_review, state
    from cyrene.agent.context import bind_run_context
    from cyrene.workbench.app_control import authorize

    user_request = "帮我新建一个对话，然后在新的对话里面搜索看一下野生小熊猫的攻略。"
    reviews = []
    fallbacks = []

    async def approve_delegation(**kwargs):
        reviews.append(kwargs)
        return True, "用户明确要求提交新对话中的消息。"

    async def fallback(**_kwargs):
        fallbacks.append(True)
        return "approval-required"

    monkeypatch.setattr(auto_review, "review_user_delegation", approve_delegation)
    monkeypatch.setattr(
        "cyrene.workbench.app_control.request_self_configuration_confirmation",
        fallback,
    )
    binding = bind_run_context(
        agent_id="main",
        caller="main_agent",
        client_request_id="",
        round_id="round-local-no-request-id",
        session_id="origin",
        conversation_source="desktop_local",
        user_request_text=user_request,
    )
    receipt_token = state._explicit_delegation_receipts.set(set())
    batch_token = state._explicit_delegation_batches.set({})
    try:
        result = await authorize(
            "cyrene.ui.click.r2",
            {
                "snapshot_id": "tree-current",
                "revision": 7,
                "node_id": "chat_composer_submit",
                "action_id": "submit",
            },
            reason="Submit the message requested by the user.",
        )
    finally:
        state._explicit_delegation_batches.reset(batch_token)
        state._explicit_delegation_receipts.reset(receipt_token)
        binding.reset()

    assert result is None
    assert fallbacks == []
    assert len(reviews) == 1
    assert reviews[0]["delegation_quote"] == user_request


@pytest.mark.asyncio
async def test_explicit_local_delegation_batch_is_reviewed_once_and_consumed_in_order(monkeypatch):
    from cyrene.agent import auto_review, state
    from cyrene.workbench.app_control import authorize

    quote = "先重启后端，然后退出 Cyrene"
    binding, token = _bind_local_delegation(quote)
    reviews = []
    fallbacks = []
    operations = [
        {
            "operation_id": "cyrene.app.lifecycle",
            "arguments": {"action": "restart_backend"},
        },
        {
            "operation_id": "cyrene.app.lifecycle",
            "arguments": {"action": "quit"},
        },
    ]

    async def approve_batch(**kwargs):
        reviews.append(kwargs)
        return True, "用户明确要求按顺序执行这两项操作。"

    async def fallback(**_kwargs):
        fallbacks.append(True)
        return "approval-required"

    monkeypatch.setattr(auto_review, "review_user_delegation", approve_batch)
    monkeypatch.setattr(
        "cyrene.workbench.app_control.request_host_lifecycle_confirmation",
        fallback,
    )
    try:
        first = await authorize(
            "cyrene.app.lifecycle",
            {"action": "restart_backend"},
            reason="Perform the first requested action.",
            delegation_quote=quote,
            delegation_operations=operations,
        )
        second = await authorize(
            "cyrene.app.lifecycle",
            {"action": "quit"},
            reason="Perform the second requested action.",
            delegation_quote=quote,
            delegation_operations=operations,
        )
        exhausted = await authorize(
            "cyrene.app.lifecycle",
            {"action": "restart_backend"},
            reason="Attempt to reuse the consumed batch.",
            delegation_quote=quote,
            delegation_operations=operations,
        )
    finally:
        _reset_local_delegation(state, token)
        binding.reset()

    assert first is None
    assert second is None
    assert exhausted == "approval-required"
    assert len(reviews) == 1
    reviewed_operations = json.loads(reviews[0]["operations_json"])
    assert reviewed_operations == operations
    assert fallbacks == [True]


@pytest.mark.asyncio
async def test_policy_statement_and_forwarded_text_are_not_delegation(monkeypatch):
    from cyrene.agent import auto_review
    from cyrene.agent import state
    from cyrene.agent.context import bind_run_context
    from cyrene.workbench.app_control import authorize

    policy = "如果用户要求，也可以由 agent 来确认并重启"

    async def fallback(**_kwargs):
        return "approval-required"

    reviews = []

    async def review_delegation(**kwargs):
        reviews.append(kwargs)
        return False, "这是产品规则讨论，不是当前行动指令。"

    monkeypatch.setattr(auto_review, "review_user_delegation", review_delegation)
    monkeypatch.setattr("cyrene.workbench.app_control.request_host_lifecycle_confirmation", fallback)
    binding, token = _bind_local_delegation(policy)
    try:
        policy_result = await authorize(
            "cyrene.app.lifecycle", {"action": "restart_app"},
            reason="Policy discussion.", delegation_quote=policy,
        )
    finally:
        _reset_local_delegation(state, token)
        binding.reset()

    forwarded = bind_run_context(
        agent_id="main", caller="main_agent", client_request_id="request-2",
        round_id="round-2", session_id="target",
        conversation_source="agent_session",
        user_request_text="请你代我确认并重启 Cyrene",
    )
    forwarded_token = state._explicit_delegation_receipts.set(set())
    forwarded_batch_token = state._explicit_delegation_batches.set({})
    try:
        forwarded_result = await authorize(
            "cyrene.app.lifecycle", {"action": "restart_app"},
            reason="Forwarded agent text.", delegation_quote="请你代我确认并重启 Cyrene",
        )
    finally:
        state._explicit_delegation_batches.reset(forwarded_batch_token)
        state._explicit_delegation_receipts.reset(forwarded_token)
        forwarded.reset()

    assert policy_result == "approval-required"
    assert "local desktop approval" in forwarded_result
    assert len(reviews) == 1
    assert reviews[0]["delegation_quote"] == policy


@pytest.mark.asyncio
async def test_session_message_target_is_bound_to_current_tree(monkeypatch, tmp_path):
    from cyrene.agent import auto_review
    from cyrene.agent import state
    from cyrene.tool_impl.application import session_message
    from cyrene.workbench import app_control

    quote = "请你代我向另一个任务发送这段文字"
    binding, token = _bind_local_delegation(quote)
    monkeypatch.setattr(app_control, "_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_control, "_IDEMPOTENCY_PATH", tmp_path / "idempotency.json")
    calls = []

    async def fake_host(method, args):
        calls.append((method, dict(args)))
        if method == "ui.snapshot.current":
            return {
                "ok": True,
                "snapshot_id": "tree-current",
                "revision": 7,
                "root": {
                    "node_id": "root",
                    "children": [{
                        "node_id": "task_composer_input",
                        "role": "textbox",
                        "state": {
                            "session_id": "target-task",
                            "session_kind": "task",
                            "draft_empty": True,
                            "submit_exposed": False,
                        },
                        "actions": [
                            {"action_id": "set_value", "risk": "R1"},
                            {"action_id": "clear_value", "risk": "R1"},
                        ],
                        "children": [],
                    }],
                },
            }
        if args["action_id"] == "set_value":
            return {"ok": True, "revision": 8}
        return {"ok": True, "revision": 9}

    async def fake_dispatch(kind, session_id, message, *, origin_session_id):
        assert (kind, session_id, message, origin_session_id) == (
            "task", "target-task", "hello", "origin",
        )
        return {"status": "started", "run_id": "run-target"}

    monkeypatch.setattr(session_message, "call_host", fake_host)
    monkeypatch.setattr(session_message.app_services, "dispatch_session_message", fake_dispatch)
    monkeypatch.setattr(
        auto_review,
        "review_user_delegation",
        lambda **_kwargs: _approved_delegation(),
    )
    try:
        result = json.loads(await session_message.handler({
            "snapshot_id": "tree-current",
            "revision": 7,
            "node_id": "task_composer_input",
            "message": "hello",
            "reason": "Send the requested instruction.",
            "idempotency_key": "send-1",
            "delegation_quote": quote,
        }, None, 0, "", None))
    finally:
        _reset_local_delegation(state, token)
        binding.reset()

    assert result["status"] == "success"
    assert result["effects"][0] == {
        "target_session_id": "target-task",
        "target_session_kind": "task",
        "run_id": "run-target",
        "delivery_status": "started",
        "draft_cleared": True,
    }
    assert [item[1].get("action_id") for item in calls[1:]] == ["set_value", "clear_value"]
    assert calls[-1][1]["revision"] == 8


@pytest.mark.asyncio
async def test_session_message_cannot_submit_calling_session(monkeypatch):
    from cyrene.agent import state
    from cyrene.tool_impl.application import session_message

    binding, token = _bind_local_delegation("请你代我发送", session_id="same")

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
    try:
        result = json.loads(await session_message.handler({
            "snapshot_id": "tree-current", "revision": 3,
            "node_id": "chat_composer_input", "message": "hello",
            "reason": "Prepare own draft.", "idempotency_key": "self-1",
        }, None, 0, "", None))
    finally:
        _reset_local_delegation(state, token)
        binding.reset()

    assert result["error_code"] == "self_session_submit_forbidden"


@pytest.mark.asyncio
async def test_cyrene_gateway_reaches_current_surface_broker_end_to_end(
    monkeypatch, tmp_path,
):
    from cyrene.agent.context import bind_run_context
    from cyrene.tooling import execute_wire_tool
    from cyrene.workbench import app_control, ui_surface

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
    binding = bind_run_context(
        agent_id="main",
        caller="main_agent",
        session_id="origin",
        round_id="round-e2e",
        client_request_id="request-e2e",
        conversation_source="webui",
        ui_instance_id="surface-e2e",
    )
    try:
        discovered = json.loads(await execute_wire_tool(
            "cyrene_tools",
            {"operation": "discover", "query": "current interface tree"},
            None, 0, "", None,
        ))
        assert any(
            item["id"] == "cyrene.ui.snapshot"
            for item in discovered["capabilities"]
        )
        described = json.loads(await execute_wire_tool(
            "cyrene_tools",
            {"operation": "describe", "capability_ids": [
                "cyrene.ui.snapshot", "cyrene.ui.inspect", "cyrene.ui.click",
            ]},
            None, 0, "", None,
        ))
        assert [item["id"] for item in described["capabilities"]] == [
            "cyrene.ui.snapshot", "cyrene.ui.inspect", "cyrene.ui.click",
        ]

        read_outer = json.loads(await execute_wire_tool(
            "cyrene_tools",
            {
                "operation": "invoke",
                "capability_id": "cyrene.ui.snapshot",
                "arguments": {"max_depth": 12},
            },
            None, 0, "", None,
        ))
        read_result = read_outer["result"]
        assert read_result["snapshot"]["snapshot_id"] == "tree-e2e"

        act_outer = json.loads(await execute_wire_tool(
            "cyrene_tools",
            {
                "operation": "invoke",
                "capability_id": "cyrene.ui.click",
                "arguments": {
                    "snapshot_id": "tree-e2e",
                    "revision": 4,
                    "node_id": "navigation_chat",
                    "action_id": "open",
                    "reason": "Open the user-visible Chat surface.",
                    "idempotency_key": "gateway-e2e-open-chat",
                },
            },
            None, 0, "", None,
        ))
        act_result = act_outer["result"]
        assert act_result["status"] == "success"
        assert act_result["revision"] == 5
    finally:
        binding.reset()
        await ui_surface.unregister("surface-e2e", socket.connection)


@pytest.mark.asyncio
async def test_ui_inspect_uses_target_node_lease_across_unrelated_revision_changes(
    monkeypatch,
):
    from cyrene.agent.context import bind_run_context
    from cyrene.tool_impl.application import _ui_snapshot, ui_inspect

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
    binding = bind_run_context(
        agent_id="main", caller="main_agent", session_id="origin",
        round_id="round-inspect", client_request_id="request-inspect",
        conversation_source="desktop_local", ui_instance_id="surface-inspect",
    )
    try:
        result = json.loads(await ui_inspect.handler({
            "snapshot_id": "tree-inspect",
            "revision": 4,
            "node_id": "chat_composer_input",
            "include": ["interactive", "text"],
            "max_depth": 3,
        }, None, 0, "", None))
    finally:
        binding.reset()

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

    from cyrene.agent.context import bind_run_context
    from cyrene.tool_impl.application import _ui_action
    from cyrene.workbench import app_control

    module = import_module(f"cyrene.tool_impl.application.{module_name}")
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
    binding = bind_run_context(
        agent_id="main", caller="main_agent", session_id="origin",
        round_id=f"round-{module_name}", client_request_id=f"request-{module_name}",
        conversation_source="desktop_local", ui_instance_id="surface-main",
    )
    try:
        result = json.loads(await module.handler({
            "snapshot_id": f"tree-{module_name}",
            "revision": 3,
            "node_id": "target",
            "action_id": "perform",
            "input": {},
            "reason": "Perform the requested visible action.",
            "idempotency_key": f"cursor-{module_name}",
        }, None, 0, "", None))
    finally:
        binding.reset()

    assert result["status"] == "success"
    assert operation_family in result["operation_id"]
    execute_args = next(
        args for method, args in calls
        if method == "ui.gesture.execute_current"
    )
    assert execute_args["_agent_cursor_mode"] == "target"


@pytest.mark.asyncio
async def test_ui_snapshot_exposes_visible_and_calling_session_mismatch(monkeypatch):
    from cyrene.agent.context import bind_run_context
    from cyrene.tool_impl.application import _ui_snapshot, ui_snapshot

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
    binding = bind_run_context(
        agent_id="main", caller="main_agent", session_id="calling-chat",
        round_id="round-mismatch", client_request_id="request-mismatch",
        conversation_source="desktop_local", ui_instance_id="surface-main",
    )
    try:
        result = json.loads(await ui_snapshot.handler({}, None, 0, "", None))
    finally:
        binding.reset()

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
    from cyrene.agent.context import bind_run_context
    from cyrene.tool_impl.application import _ui_action, ui_click
    from cyrene.workbench import app_control

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
    binding = bind_run_context(
        agent_id="main", caller="main_agent", session_id="origin",
        round_id="round-retry", client_request_id="request-retry",
        conversation_source="webui", ui_instance_id="surface-retry",
    )
    args = {
        "snapshot_id": "tree-retry", "revision": 2,
        "node_id": "open_search", "action_id": "open",
        "reason": "Open search.", "idempotency_key": "retry-open-search",
    }
    try:
        first = json.loads(await ui_click.handler(args, None, 0, "", None))

        async def should_not_call_host(_method, _args):
            raise AssertionError("an exact idempotent retry must not touch the changed surface")

        monkeypatch.setattr(_ui_action, "call_host", should_not_call_host)
        second = json.loads(await ui_click.handler(args, None, 0, "", None))
    finally:
        binding.reset()

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
    from cyrene.agent.context import bind_run_context
    from cyrene.tool_impl.application import _ui_action, ui_click
    from cyrene.workbench import app_control

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
    binding = bind_run_context(
        agent_id="main", caller="main_agent", session_id="origin",
        round_id="round-compatible", client_request_id="request-compatible",
        conversation_source="desktop_local", ui_instance_id="surface-compatible",
    )
    try:
        result = json.loads(await ui_click.handler({
            "snapshot_id": "tree-compatible",
            "revision": 7,
            "node_id": "new_chat",
            "action_id": "invoke",
            "reason": "Create the requested visible chat.",
            "idempotency_key": "compatible-new-chat",
        }, None, 0, "", None))
    finally:
        binding.reset()

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
    from cyrene.agent.context import bind_run_context
    from cyrene.tool_impl.application import _ui_action, ui_double_click
    from cyrene.workbench import app_control

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
    binding = bind_run_context(
        agent_id="main", caller="main_agent", session_id="origin",
        round_id="round-double-click", client_request_id="request-double-click",
        conversation_source="desktop_local", ui_instance_id="surface-double-click",
    )
    base_args = {
        "snapshot_id": "tree-browser",
        "revision": 4,
        "node_id": "browser_window_titlebar",
        "action_id": "maximize",
        "reason": "Double-click the Browser titlebar to maximize it.",
    }
    try:
        success = json.loads(await ui_double_click.handler({
            **base_args,
            "idempotency_key": "double-click-browser-maximize",
        }, None, 0, "", None))
        declared_aliases[:] = ["press", "keyboard"]
        rejected = json.loads(await ui_double_click.handler({
            **base_args,
            "idempotency_key": "double-click-ordinary-button",
        }, None, 0, "", None))
    finally:
        binding.reset()

    assert success["status"] == "success"
    assert success["operation_id"] == "cyrene.ui.double_click"
    assert rejected["status"] == "error"
    assert rejected["error_code"] == "gesture_not_available"
    assert [method for method, _args in calls].count("ui.gesture.execute_current") == 1
    assert next(args for method, args in calls if method == "ui.gesture.execute_current")["_agent_cursor_mode"] == "click"


@pytest.mark.asyncio
async def test_desktop_settings_describe_uses_electron_cas_revision(monkeypatch):
    from cyrene.tool_impl.application import settings_describe

    async def fake_host(method, args):
        assert (method, args) == ("desktop.settings.get", {})
        return {"ok": True, "settings": {
            "settingsRevision": 17,
            "quickChatEnabled": False,
        }}

    monkeypatch.setattr(settings_describe, "call_host", fake_host)
    result = json.loads(await settings_describe.handler(
        {"namespace": "desktop"}, None, 0, "", None,
    ))
    assert result["revision"] == 17
    assert result["schema"]["revision"] == 17


@pytest.mark.asyncio
async def test_lifecycle_finalization_is_scoped_to_origin_request(
    monkeypatch, tmp_path,
):
    from cyrene.agent.context import bind_run_context
    from cyrene.runtime import host_actions

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
        binding = bind_run_context(
            session_id="same-session", round_id=f"round-{run_id}",
            client_request_id=run_id,
        )
        try:
            host_actions.schedule_action(
                "restart_backend",
                idempotency_key=key,
                parameter_hash=digest,
                expected_app_version="0.7.9",
            )
        finally:
            binding.reset()

    await host_actions.finalize_origin(
        "same-session", "", origin_run_id="request-a",
    )
    pending = host_actions.list_actions()
    assert executed == ["request-a"]
    assert {item["origin_run_id"]: item["status"] for item in pending} == {
        "request-a": "queued",
        "request-b": "waiting_for_run_finalization",
    }
