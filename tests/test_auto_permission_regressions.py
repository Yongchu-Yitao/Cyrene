import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def _ignore_event(*_args, **_kwargs):
    return None


async def test_auto_review_rejects_string_false(monkeypatch):
    from cyrene.agent import auto_review, state

    async def malformed_review(*_args, **_kwargs):
        return {
            "tool_calls": [{
                "function": {
                    "name": "decide",
                    "arguments": '{"approve":"false","rationale":"deny"}',
                }
            }]
        }

    monkeypatch.setattr(state, "_call_llm", malformed_review)
    approved, rationale = await auto_review.review_elevation(
        tool_name="Read",
        operation="读取操作",
        path_hint="/tmp/example",
    )

    assert approved is False
    assert "无效裁决格式" in rationale
    schema = auto_review._REVIEW_TOOL_DEFS[0]["function"]
    assert schema["strict"] is True
    assert schema["parameters"]["additionalProperties"] is False


async def test_delegation_review_uses_semantics_with_cache_stable_system_prompt(monkeypatch):
    from cyrene.agent import auto_review, state

    calls = []

    async def approve(messages, **kwargs):
        calls.append((messages, kwargs))
        return {
            "tool_calls": [{
                "function": {
                    "name": "decide",
                    "arguments": '{"approve":true,"rationale":"用户直接要求创建对话"}',
                }
            }]
        }

    monkeypatch.setattr(state, "_call_llm", approve)
    approved, rationale = await auto_review.review_user_delegation(
        user_request="你新建一个对话，查明天深圳天气",
        delegation_quote="你新建一个对话",
        operations_json='[{"operation_id":"cyrene.chat.manage","arguments":{"action":"create"}}]',
        reason="Create the requested chat.",
    )

    assert approved is True
    assert rationale == "用户直接要求创建对话"
    messages, kwargs = calls[0]
    assert "不要求出现‘代我’、‘帮我’等固定措辞" in messages[0]["content"]
    assert "你新建一个对话" not in messages[0]["content"]
    assert "你新建一个对话" in messages[1]["content"]
    assert kwargs["secondary"] is True
    assert kwargs["thinking"] == "disabled"


async def test_auto_approval_is_bound_to_one_exact_path(monkeypatch, tmp_path):
    from cyrene.agent import auto_review, state
    from cyrene.observability import debug
    from cyrene.tooling import runtime_support

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    reviews = []

    async def approve(**kwargs):
        reviews.append(kwargs)
        return True, "符合请求"

    monkeypatch.setattr(auto_review, "review_elevation", approve)
    monkeypatch.setattr(debug, "publish_event", _ignore_event)
    round_token = state._current_round_id.set("round_exact_path")
    mode_token = state._permission_mode.set("auto")
    workspace_token = state._active_workspace_dir.set(str(workspace))
    full_token = state._temporary_full_access.set(False)
    path_grant_token = state._scoped_path_access_grants.set(None)
    try:
        result = await runtime_support._request_read_elevation(
            tool_name="Read",
            path_hint=str(first),
            reason="test",
        )
        assert result is None
        assert runtime_support._resolve_tool_path(str(first)) == first.resolve()
        with pytest.raises(ValueError):
            runtime_support._resolve_tool_path(str(second))
        assert state._temporary_full_access.get() is False

        delivery = await runtime_support._request_external_delivery_confirmation(
            tool_name="send_message",
            operation="外发消息",
            detail="second operation",
        )
        assert delivery is None
    finally:
        state._scoped_path_access_grants.reset(path_grant_token)
        state._temporary_full_access.reset(full_token)
        state._active_workspace_dir.reset(workspace_token)
        state._permission_mode.reset(mode_token)
        state._current_round_id.reset(round_token)

    assert [item["tool_name"] for item in reviews] == ["Read", "send_message"]


async def test_extension_changes_reach_reviewer_even_in_full_access(monkeypatch):
    from cyrene.agent import auto_review, state
    from cyrene.observability import debug
    from cyrene.tooling import runtime_support

    reviews = []

    async def approve(**kwargs):
        reviews.append(kwargs)
        return True, "extension install approved"

    monkeypatch.setattr(auto_review, "review_elevation", approve)
    monkeypatch.setattr(debug, "publish_event", _ignore_event)
    round_token = state._current_round_id.set("round_extension_review")
    mode_token = state._permission_mode.set("full_access")
    try:
        result = await runtime_support._request_scope_elevation(
            tool_name="ManageExtensions",
            path_hint="extension:toolchain:node:exact",
            operation="安装扩展 toolchain:node",
            reason="User asked the agent to install Node.",
            permission_kind="extension_change",
        )
    finally:
        state._permission_mode.reset(mode_token)
        state._current_round_id.reset(round_token)

    assert result is None
    assert reviews[0]["tool_name"] == "ManageExtensions"


async def test_exact_grant_is_shared_and_consumed_once_across_tool_tasks():
    from cyrene.agent import context, state

    token = state._permission_elevation_grants.set(None)
    try:
        context.grant_permission_elevation("exact")

        async def consume():
            await asyncio.sleep(0)
            return context.consume_permission_elevation("exact")

        results = await asyncio.gather(
            asyncio.create_task(consume()),
            asyncio.create_task(consume()),
        )
    finally:
        state._permission_elevation_grants.reset(token)

    assert sorted(results) == [False, True]


def test_new_exact_approval_labels_are_recognized():
    from cyrene.agent.guidance import _permission_answer_granted

    assert _permission_answer_granted("允许执行这一次") is True
    assert _permission_answer_granted("允许调用这一次") is True


def test_permission_fingerprint_binds_command_or_external_arguments():
    from cyrene.agent.context import permission_elevation_fingerprint

    common = {
        "tool_name": "external_tool",
        "permission_kind": "external_tool_execution",
        "path_hint": "",
        "operation": "调用外部 MCP/集成工具",
    }
    first = permission_elevation_fingerprint(
        **common,
        reason='外部工具参数：{"target":"a"}',
    )
    second = permission_elevation_fingerprint(
        **common,
        reason='外部工具参数：{"target":"b"}',
    )

    assert first != second


def test_self_configuration_fingerprint_ignores_retry_reason_paraphrase():
    from cyrene.agent.context import permission_elevation_fingerprint

    common = {
        "tool_name": "cyrene.ui.click",
        "permission_kind": "self_configuration_confirmation",
        "path_hint": "cyrene-setting:exact-operation-hash",
        "operation": "cyrene.ui.click.r2",
    }
    first = permission_elevation_fingerprint(
        **common,
        reason="提交用户要求的搜索请求",
    )
    second = permission_elevation_fingerprint(
        **common,
        reason="发送已经输入的新对话搜索请求",
    )

    assert first == second


async def test_default_mode_blocks_unknown_mcp_until_exact_approval(
    monkeypatch, tmp_path
):
    from cyrene.agent import state
    from cyrene.observability import debug
    from cyrene.tooling import executor
    from cyrene.tooling.backends import mcp_manager

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "messages": [{
            "role": "user",
            "content": "调用外部工具",
            "round_id": "round_mcp",
        }]
    }), encoding="utf-8")
    monkeypatch.setattr(state, "STATE_FILE", state_file)
    monkeypatch.setattr(state, "DATA_DIR", tmp_path)
    monkeypatch.setattr(debug, "publish_event", _ignore_event)

    class FakeManager:
        def __init__(self):
            self.calls = []

        async def execute_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return "executed"

        def has_tool(self, name):
            return name == "external_side_effect"

    manager = FakeManager()
    monkeypatch.setattr(mcp_manager, "get_manager", lambda: manager)
    round_token = state._current_round_id.set("round_mcp")
    mode_token = state._permission_mode.set("default")
    try:
        result = await executor._execute_tool(
            "external_side_effect",
            {"target": "outside"},
            None,
            0,
            "",
            None,
        )
    finally:
        state._permission_mode.reset(mode_token)
        state._current_round_id.reset(round_token)

    payload = json.loads(result)
    assert payload["status"] == "awaiting_user"
    assert payload["permission"] == "external_tool_execution"
    assert manager.calls == []


async def test_auto_mode_reviews_unbounded_shell_but_skips_workspace_read_only_commands(
    monkeypatch, tmp_path
):
    from cyrene.agent import auto_review, state
    from cyrene.observability import debug
    from cyrene.tooling import executor

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reviews = []

    async def deny(**kwargs):
        reviews.append(kwargs)
        return False, "不允许读取系统文件"

    monkeypatch.setattr(auto_review, "review_elevation", deny)
    monkeypatch.setattr(debug, "publish_event", _ignore_event)
    workspace_token = state._active_workspace_dir.set(str(workspace))
    round_token = state._current_round_id.set("round_shell")
    mode_token = state._permission_mode.set("auto")
    try:
        denied = await executor._execute_tool(
            "Bash",
            {"command": "head -n 1 /etc/hosts"},
            None,
            0,
            "",
            None,
        )
        allowed = await executor._execute_tool(
            "Bash",
            {"command": "printf safe"},
            None,
            0,
            "",
            None,
        )
    finally:
        state._permission_mode.reset(mode_token)
        state._current_round_id.reset(round_token)
        state._active_workspace_dir.reset(workspace_token)

    assert "拒绝" in denied
    assert reviews[0]["tool_name"] == "Bash"
    assert reviews[0]["operation"] == "执行本地进程或 Shell 命令"
    assert "safe" in allowed
    assert len(reviews) == 1


async def test_send_shell_uses_terminal_context_for_unified_auto_review(
    monkeypatch, tmp_path
):
    from cyrene.agent import auto_review, state
    from cyrene.observability import debug
    from cyrene.tooling import catalog, executor

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reviews = []

    async def approve(**kwargs):
        reviews.append(kwargs)
        return True, "用户要求在该终端安装 Git"

    async def fake_send_shell(arguments, *_args):
        return json.dumps({"sent": arguments["text"]})

    monkeypatch.setattr(auto_review, "review_elevation", approve)
    monkeypatch.setattr(debug, "publish_event", _ignore_event)
    monkeypatch.setitem(catalog.TOOL_HANDLERS, "SendShell", fake_send_shell)
    workspace_token = state._active_workspace_dir.set(str(workspace))
    round_token = state._current_round_id.set("round_terminal_review")
    mode_token = state._permission_mode.set("auto")
    try:
        result = await executor._execute_tool(
            "SendShell",
            {
                "name": "Terminal 5",
                "text": "sudo apt install git",
                "key": "enter",
            },
            None,
            0,
            "",
            None,
        )
    finally:
        state._permission_mode.reset(mode_token)
        state._current_round_id.reset(round_token)
        state._active_workspace_dir.reset(workspace_token)

    assert json.loads(result)["sent"] == "sudo apt install git"
    assert len(reviews) == 1
    assert reviews[0]["tool_name"] == "SendShell"
    assert reviews[0]["operation"] == "向已授权共享终端发送可执行输入"
    assert reviews[0]["path_hint"] == "terminal:Terminal 5"
    assert "目标终端：Terminal 5" in reviews[0]["reason"]
    assert "输入：sudo apt install git" in reviews[0]["reason"]
    assert "按键：enter" in reviews[0]["reason"]


async def test_start_shell_and_sensitive_terminal_input_skip_process_review(
    monkeypatch, tmp_path
):
    from cyrene.agent import auto_review, state
    from cyrene.observability import debug
    from cyrene.tooling import catalog, executor

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reviews = []
    calls = []

    async def deny(**kwargs):
        reviews.append(kwargs)
        return False, "should not be called"

    async def fake_handler(arguments, *_args):
        calls.append(dict(arguments))
        return "executed"

    monkeypatch.setattr(auto_review, "review_elevation", deny)
    monkeypatch.setattr(debug, "publish_event", _ignore_event)
    monkeypatch.setitem(catalog.TOOL_HANDLERS, "StartShell", fake_handler)
    monkeypatch.setitem(catalog.TOOL_HANDLERS, "SendShell", fake_handler)
    workspace_token = state._active_workspace_dir.set(str(workspace))
    round_token = state._current_round_id.set("round_terminal_no_review")
    mode_token = state._permission_mode.set("auto")
    try:
        started = await executor._execute_tool(
            "StartShell",
            {"cwd": ".", "command": "python -m worker"},
            None,
            0,
            "",
            None,
        )
        password_sent = await executor._execute_tool(
            "SendShell",
            {
                "name": "Terminal 5",
                "text": "user-provided-password",
                "key": "enter",
                "sensitive": True,
            },
            None,
            0,
            "",
            None,
        )
    finally:
        state._permission_mode.reset(mode_token)
        state._current_round_id.reset(round_token)
        state._active_workspace_dir.reset(workspace_token)

    assert started == "executed"
    assert password_sent == "executed"
    assert len(calls) == 2
    assert reviews == []


def test_sensitive_terminal_input_and_password_assignments_are_redacted():
    from cyrene.runtime.secret_redaction import redact_text, redact_value

    redacted = redact_value({
        "name": "Terminal 5",
        "text": "user-provided-password",
        "key": "enter",
        "sensitive": True,
    })
    assert redacted["text"] == "[REDACTED_SENSITIVE_INPUT]"
    assert "user-provided-password" not in json.dumps(redacted)
    assert redact_text("密码是 user-provided-password") == (
        "密码是 [REDACTED_PASSWORD]"
    )


def test_sensitive_terminal_tool_call_is_redacted_before_session_storage():
    from cyrene.agent.agent import _redact_sensitive_tool_calls_for_storage

    message = {
        "role": "assistant",
        "tool_calls": [{
            "id": "call_secret",
            "function": {
                "name": "SendShell",
                "arguments": json.dumps({
                    "name": "Terminal 5",
                    "text": "literal-user-secret",
                    "key": "enter",
                    "sensitive": True,
                }),
            },
        }],
    }
    safe = _redact_sensitive_tool_calls_for_storage(message)
    safe_arguments = json.loads(
        safe["tool_calls"][0]["function"]["arguments"]
    )

    assert safe_arguments["text"] == "[REDACTED_SENSITIVE_INPUT]"
    assert safe_arguments["key"] == "enter"
    assert "literal-user-secret" not in json.dumps(safe)
    assert "literal-user-secret" in message["tool_calls"][0]["function"]["arguments"]


def test_shell_review_classifier_distinguishes_read_only_git_from_mutating_commands(
    tmp_path
):
    from cyrene.agent import state
    from cyrene.tooling.executor import _shell_command_needs_explicit_review

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_token = state._active_workspace_dir.set(str(workspace))
    try:
        assert _shell_command_needs_explicit_review({
            "command": "cd repo && git log --oneline -5 && rg -n skill src 2>/dev/null",
        }) is False
        assert _shell_command_needs_explicit_review({
            "command": "git clone https://example.com/repo.git scratch/repo",
        }) is True
        assert _shell_command_needs_explicit_review({
            "command": "sed -i 's/old/new/' src/example.py",
        }) is True
        assert _shell_command_needs_explicit_review({
            "command": "printf changed > src/example.py",
        }) is True
        assert _shell_command_needs_explicit_review({
            "command": "cd ../../outside && cat secret.txt",
        }) is True
        assert _shell_command_needs_explicit_review({
            "command": "git branch -D obsolete",
        }) is True
    finally:
        state._active_workspace_dir.reset(workspace_token)


async def test_permission_decision_is_persisted(tmp_path):
    import aiosqlite

    from cyrene.runtime import database

    db_path = tmp_path / "cyrene.db"
    await database.init_db(str(db_path))
    await database.record_permission_decision(str(db_path), {
        "event_id": "evt_permission_1",
        "timestamp": "2026-07-26T12:00:00+00:00",
        "type": "auto_review",
        "session_id": "chat_1",
        "round_id": "round_1",
        "source": "auto_reviewer",
        "tool_name": "Read",
        "operation": "读取操作",
        "permission_kind": "read_elevation",
        "path_hint": "/tmp/example",
        "approved": False,
        "rationale": "outside scope",
        "fingerprint": "abc",
    })

    async with aiosqlite.connect(db_path) as db:
        row = await (
            await db.execute(
                "SELECT session_id, approved, fingerprint FROM permission_decisions "
                "WHERE id='evt_permission_1'"
            )
        ).fetchone()

    assert row == ("chat_1", 0, "abc")
