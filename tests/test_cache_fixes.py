"""Verify cache-hit improvements don't break any behavior."""
import asyncio
import json
from unittest.mock import AsyncMock

from cyrene.agent import state as _agent_state
from cyrene.agent import agent as _agent_core


def test_normalized_usage_reads_provider_prompt_cache_fields():
    from cyrene.call_llm import _normalized_usage

    openai_usage = _normalized_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
        [],
        {},
    )
    anthropic_usage = _normalized_usage(
        {
            "input_tokens": 120,
            "output_tokens": 12,
            "cache_read_input_tokens": 90,
            "cache_creation_input_tokens": 30,
        },
        [],
        {},
    )

    assert openai_usage["prompt_cache_hit_tokens"] == 80
    assert openai_usage["prompt_cache_miss_tokens"] == 20
    assert anthropic_usage["prompt_tokens"] == 120
    assert anthropic_usage["completion_tokens"] == 12
    assert anthropic_usage["prompt_cache_hit_tokens"] == 90
    assert anthropic_usage["prompt_cache_miss_tokens"] == 30


async def test_run_model_lease_pins_candidates_across_calls():
    """Model settings changed mid-run are deferred until the next run."""
    from cyrene.model_runtime import client as model_client

    generation = {"model": "deepseek-before"}
    captured_candidates = []

    def fake_resolve(model_type):
        return [{
            "id": model_type,
            "provider": "openai_compatible",
            "model": generation["model"],
            "base_url": "https://api.deepseek.com",
            "api_key": "secret",
            "endpoints": ["https://api.deepseek.com/chat/completions"],
        }]

    async def fake_unified_call(messages, **kwargs):
        captured_candidates.append(kwargs.get("candidates"))
        return {"role": "assistant", "content": "ok"}

    _orig_resolve = _patch(model_client, "_resolve_candidates", fake_resolve)
    _orig_prioritize = _patch(
        model_client,
        "_prioritize_last_success",
        lambda candidates, model_type, session_id: candidates,
    )
    _orig_call = _patch(model_client, "call_llm", fake_unified_call)
    lease_token = _agent_state.activate_run_model_lease()
    try:
        generation["model"] = "deepseek-after"
        await _agent_state._call_llm([{"role": "user", "content": "one"}])
        await _agent_state._call_llm([{"role": "user", "content": "two"}])
    finally:
        _agent_state.reset_run_model_lease(lease_token)
        _patch(model_client, "call_llm", _orig_call)
        _patch(model_client, "_prioritize_last_success", _orig_prioritize)
        _patch(model_client, "_resolve_candidates", _orig_resolve)

    assert [items[0]["model"] for items in captured_candidates] == [
        "deepseek-before",
        "deepseek-before",
    ]


async def test_run_model_lease_child_task_pins_successful_candidate_and_endpoint():
    """A model call running in a child task must update the shared run lease."""
    captured_candidates = []
    responses = iter([
        {
            "role": "assistant",
            "content": "phase one",
            "_candidate_identity": {
                "candidateId": "backup",
                "provider": "openai_compatible",
                "model": "backup-model",
                "endpoint": "https://backup.example/v1/second",
            },
        },
        {"role": "assistant", "content": "phase two"},
    ])

    async def fake_unified_call(messages, **kwargs):
        captured_candidates.append(kwargs["candidates"])
        assert kwargs["candidate_lease"] is not None
        return next(responses)

    lease = _agent_state.RunModelLease(
        "lease-test",
        {
            "primary": (
                {
                    "id": "primary",
                    "provider": "openai_compatible",
                    "model": "primary-model",
                    "endpoints": ["https://primary.example/v1"],
                },
                {
                    "id": "backup",
                    "provider": "openai_compatible",
                    "model": "backup-model",
                    "endpoints": [
                        "https://backup.example/v1/first",
                        "https://backup.example/v1/second",
                    ],
                },
            ),
        },
    )
    lease_token = _agent_state._run_model_lease.set(lease)
    from cyrene.model_runtime import client as model_client
    original_call = _patch(model_client, "call_llm", fake_unified_call)
    try:
        await asyncio.create_task(
            _agent_state._call_llm([{"role": "user", "content": "one"}])
        )
        await _agent_state._call_llm([{"role": "user", "content": "two"}])
    finally:
        _patch(model_client, "call_llm", original_call)
        _agent_state._run_model_lease.reset(lease_token)

    second_call = captured_candidates[1]
    assert second_call[0]["id"] == "backup"
    assert second_call[0]["endpoints"][0] == "https://backup.example/v1/second"


def test_run_model_lease_reports_strict_prefix_and_invalidation_reasons():
    lease = _agent_state.RunModelLease("lease-fingerprint", {"primary": ()})
    identity = {
        "candidateId": "primary",
        "provider": "openai_compatible",
        "model": "model",
        "endpoint": "https://model.example/v1",
        "reasoningEffort": "high",
    }

    first = lease.observe_request(
        "primary",
        identity=identity,
        message_fingerprints=["system", "user", "decision"],
        tools_fingerprint="tools-a",
        payload_fingerprint="payload-a",
    )
    second = lease.observe_request(
        "primary",
        identity=identity,
        message_fingerprints=["system", "user", "decision", "assistant", "tool"],
        tools_fingerprint="tools-a",
        payload_fingerprint="payload-b",
    )
    invalidated = lease.observe_request(
        "primary",
        identity={**identity, "endpoint": "https://other.example/v1"},
        message_fingerprints=["changed"],
        tools_fingerprint="tools-b",
        payload_fingerprint="payload-c",
    )

    assert first["cache_prefix_status"] == "first_request"
    assert second["cache_prefix_status"] == "strict_prefix_reuse"
    assert second["cache_prefix_message_count"] == 3
    assert invalidated["cache_prefix_status"] == "invalidated"
    assert set(invalidated["cache_invalidation_reason"].split(",")) == {
        "endpoint_changed",
        "tools_changed",
        "message_prefix_changed",
    }


def test_run_model_lease_can_partition_diagnostics_without_changing_default_scope():
    lease = _agent_state.RunModelLease("lease-scopes", {"primary": ()})
    identity = {
        "candidateId": "primary",
        "provider": "openai_compatible",
        "model": "model",
        "endpoint": "https://model.example/v1",
        "reasoningEffort": "high",
    }
    request = {
        "identity": identity,
        "message_fingerprints": ["system", "user"],
        "tools_fingerprint": "tools-a",
        "payload_fingerprint": "payload-a",
    }

    default_first = lease.observe_request("primary", **request)
    decision_first = lease.observe_request(
        "primary", **request, cache_scope="decision"
    )
    execution_first = lease.observe_request(
        "primary", **request, cache_scope="execution"
    )
    decision_retry = lease.observe_request(
        "primary", **request, cache_scope="decision"
    )

    assert default_first["cache_prefix_status"] == "first_request"
    assert decision_first["cache_prefix_status"] == "first_request"
    assert execution_first["cache_prefix_status"] == "first_request"
    assert decision_retry["cache_prefix_status"] == "identical_retry"


def test_run_model_lease_reports_lane_cache_epoch_rotation():
    lease = _agent_state.RunModelLease("lease-epoch", {"primary": ()})
    identity = {
        "candidateId": "primary",
        "provider": "openai_compatible",
        "model": "model",
        "endpoint": "https://model.example/v1",
        "reasoningEffort": "high",
        "cacheRouteKey": "decision-epoch-0",
    }
    request = {
        "message_fingerprints": ["system", "user"],
        "tools_fingerprint": "tools-a",
        "payload_fingerprint": "payload-a",
        "cache_scope": "decision",
    }

    lease.observe_request("primary", identity=identity, **request)
    rotated = lease.observe_request(
        "primary",
        identity={**identity, "cacheRouteKey": "decision-epoch-1"},
        **request,
    )

    assert rotated["cache_prefix_status"] == "invalidated"
    assert rotated["cache_invalidation_reason"] == "cache_epoch_changed"


def test_workbench_new_session_memory_moves_to_volatile_tail(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import memory as memory

    monkeypatch.setattr(memory, "STORE_DIR", tmp_path)
    monkeypatch.setattr(memory, "_STORE_DB_PATH", "")
    monkeypatch.setattr(memory, "_CONFIGURED_STORE_DIR", None)
    monkeypatch.setattr(memory, "_resolve_workspace_id", lambda workspace_id: str(workspace_id))

    project = {"id": "project-test", "name": "Test"}
    session = {"id": "session-test", "title": "测试任务"}
    memory.add_agent_memory("project-test", "初始项目事实：需要使用 SQLite。", category="fact")

    fixed_first = routes._workbench_compose_ephemeral_system(project, session)
    volatile_first = routes._workbench_compose_volatile_ephemeral_system(project, session)

    assert "初始项目事实" in fixed_first
    assert volatile_first == ""

    memory.add_agent_memory("project-test", "新增项目事实：本 session 内刚发现需要保留。", category="fact")
    fixed_second = routes._workbench_compose_ephemeral_system(project, session)
    volatile_second = routes._workbench_compose_volatile_ephemeral_system(project, session)

    assert "初始项目事实" in fixed_second
    assert "新增项目事实" not in fixed_second
    assert "新增项目事实" in volatile_second
    assert "本 session 新增项目记忆" in volatile_second

    empty_first_session = {"id": "session-empty"}
    empty_project = {"id": "project-empty", "name": "Empty"}
    assert "空 session 后新增事实" not in routes._workbench_compose_ephemeral_system(empty_project, empty_first_session)
    memory.add_agent_memory("project-empty", "空 session 后新增事实：只能放在尾部。", category="fact")
    assert "空 session 后新增事实" not in routes._workbench_compose_ephemeral_system(empty_project, empty_first_session)
    assert "空 session 后新增事实" in routes._workbench_compose_volatile_ephemeral_system(empty_project, empty_first_session)


def _patch(obj, attr, replacement):
    """Simple patch helper."""
    original = getattr(obj, attr)
    setattr(obj, attr, replacement)
    return original


async def test_phase1_retry_with_unified_system_prompt():
    """Phase 1 retry still works with DECISION as separate user message."""
    from cyrene import agent
    from cyrene.agent.prompts import (
        _MAIN_AGENT_PROMPT_TEMPLATE,
        prompt_for_enabled_tool_packs,
    )

    calls = []
    responses = iter([
        {
            "content": "ok, checking weather.",
            "tool_calls": [
                {"id": "w1", "function": {"name": "UnavailableTool", "arguments": json.dumps({"query": "Toronto weather"})}},
            ],
        },
        {
            "content": "No suitable tool. Use use_tools to enter full tool phase.",
            "tool_calls": [{
                "id": "q1",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        calls.append((messages, tools))
        return next(responses)

    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_save = _patch(_agent_core, "_save_session_messages", AsyncMock())
    try:
        await agent._run_main_agent("check Toronto weather", [], None, 0, "db.sqlite3")
    finally:
        _patch(_agent_core, "_call_llm", _orig_llm)
        _patch(_agent_core, "_save_session_messages", _orig_save)

    # Phase 1 and its retry must use the exact prompt bundle selected by the
    # current tool-pack settings.
    expected_main_prompt = prompt_for_enabled_tool_packs(
        _MAIN_AGENT_PROMPT_TEMPLATE
    )
    phase1_msgs, _ = calls[0]
    assert phase1_msgs[0]["content"] == expected_main_prompt
    # DECISION is last user message
    assert phase1_msgs[-1]["role"] == "user"
    assert phase1_msgs[-1]["content"] == agent._PHASE1_DECISION_PROMPT
    # Retry uses same system prompt
    retry_msgs, _ = calls[1]
    assert retry_msgs[0]["content"] == expected_main_prompt
    # Retry includes DECISION
    assert any(
        m.get("content") == agent._PHASE1_DECISION_PROMPT
        for m in retry_msgs if m["role"] == "user"
    )
    print("PASS: test_phase1_retry_with_unified_system_prompt")


async def test_phase1_concrete_tool_is_promoted_without_correction_call():
    """A valid concrete decision executes immediately in the Phase-2 loop."""
    from cyrene import agent

    llm_inputs = []
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "r1",
                "function": {
                    "name": "Read",
                    "arguments": json.dumps({"path": "notes.txt"}),
                },
            }],
        },
        {
            "content": "The file contains the requested notes.",
            "tool_calls": [{
                "id": "q1",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        llm_inputs.append([dict(message) for message in messages])
        return next(responses)

    execute = AsyncMock(return_value="file contents")
    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_exec = _patch(_agent_core, "_execute_tool", execute)
    _orig_save = _patch(_agent_core, "_save_session_messages", AsyncMock())
    try:
        result = await agent._run_main_agent(
            "read notes.txt", [], None, 0, "db.sqlite3"
        )
    finally:
        _patch(_agent_core, "_call_llm", _orig_llm)
        _patch(_agent_core, "_execute_tool", _orig_exec)
        _patch(_agent_core, "_save_session_messages", _orig_save)

    assert result == "The file contains the requested notes."
    assert len(llm_inputs) == 2
    execute.assert_awaited_once()
    assert execute.await_args.args[:2] == ("Read", {"path": "notes.txt"})
    phase1_messages, phase2_messages = llm_inputs
    assert phase2_messages[:len(phase1_messages)] == phase1_messages
    appended = phase2_messages[len(phase1_messages):]
    assert appended[0]["role"] == "assistant"
    assert appended[0]["tool_calls"][0]["id"] == "r1"
    assert appended[1]["role"] == "tool"
    assert appended[1]["tool_call_id"] == "r1"


async def test_phase1_multiple_concrete_tools_share_one_promoted_batch():
    """Every concrete call is resolved before the next model turn."""
    from cyrene import agent

    llm_inputs = []
    responses = iter([
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "r1",
                    "function": {
                        "name": "Read",
                        "arguments": json.dumps({"path": "one.txt"}),
                    },
                },
                {
                    "id": "r2",
                    "function": {
                        "name": "Read",
                        "arguments": json.dumps({"path": "two.txt"}),
                    },
                },
            ],
        },
        {
            "content": "Both files were inspected.",
            "tool_calls": [{
                "id": "q1",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        llm_inputs.append([dict(message) for message in messages])
        return next(responses)

    execute = AsyncMock(side_effect=["one", "two"])
    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_exec = _patch(_agent_core, "_execute_tool", execute)
    _orig_save = _patch(_agent_core, "_save_session_messages", AsyncMock())
    try:
        result = await agent._run_main_agent(
            "read both files", [], None, 0, "db.sqlite3"
        )
    finally:
        _patch(_agent_core, "_call_llm", _orig_llm)
        _patch(_agent_core, "_execute_tool", _orig_exec)
        _patch(_agent_core, "_save_session_messages", _orig_save)

    assert result == "Both files were inspected."
    assert execute.await_count == 2
    appended = llm_inputs[1][len(llm_inputs[0]):]
    assert [message["role"] for message in appended] == [
        "assistant", "tool", "tool",
    ]
    assert [message["tool_call_id"] for message in appended[1:]] == ["r1", "r2"]


async def test_phase1_ask_user_wins_over_promoted_concrete_tool():
    """Clarification remains terminal for the turn when calls are mixed."""
    from cyrene import agent

    llm_calls = 0

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        nonlocal llm_calls
        llm_calls += 1
        return {
            "content": "I need one detail first.",
            "tool_calls": [
                {
                    "id": "a1",
                    "function": {
                        "name": "ask_user",
                        "arguments": json.dumps({"text": "Which file?"}),
                    },
                },
                {
                    "id": "r1",
                    "function": {
                        "name": "Read",
                        "arguments": json.dumps({"path": "guessed.txt"}),
                    },
                },
            ],
        }

    execute = AsyncMock(return_value="must not run")
    ask = AsyncMock(return_value='{"status":"awaiting_user"}')
    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_exec = _patch(_agent_core, "_execute_tool", execute)
    _orig_wire = _patch(_agent_core, "execute_wire_tool", ask)
    try:
        result = await agent._run_main_agent(
            "read the file", [], None, 0, "db.sqlite3"
        )
    finally:
        _patch(_agent_core, "_call_llm", _orig_llm)
        _patch(_agent_core, "_execute_tool", _orig_exec)
        _patch(_agent_core, "execute_wire_tool", _orig_wire)

    assert result == agent._AWAITING_USER_SENTINEL
    assert llm_calls == 1
    execute.assert_not_awaited()
    ask.assert_awaited_once()
    assert ask.await_args.args[0] == "ask_user"


async def test_quick_answer_does_not_promote_concrete_phase1_tool():
    """Quick Answer keeps its no-execution contract and requests correction."""
    from cyrene import agent

    llm_inputs = []
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "r1",
                "function": {
                    "name": "Read",
                    "arguments": json.dumps({"path": "notes.txt"}),
                },
            }],
        },
        {
            "content": "I cannot execute tools in Quick Answer mode.",
            "tool_calls": [{
                "id": "q1",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        llm_inputs.append([dict(message) for message in messages])
        return next(responses)

    execute = AsyncMock(return_value="must not run")
    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_exec = _patch(_agent_core, "_execute_tool", execute)
    _orig_save = _patch(_agent_core, "_save_session_messages", AsyncMock())
    command_token = _agent_state._current_command.set("quick-answer")
    try:
        result = await agent._run_main_agent(
            "answer briefly", [], None, 0, "db.sqlite3"
        )
    finally:
        _agent_state._current_command.reset(command_token)
        _patch(_agent_core, "_call_llm", _orig_llm)
        _patch(_agent_core, "_execute_tool", _orig_exec)
        _patch(_agent_core, "_save_session_messages", _orig_save)

    assert result == "I cannot execute tools in Quick Answer mode."
    assert len(llm_inputs) == 2
    assert "Quick Answer mode does not allow execution tools" in llm_inputs[1][-1]["content"]
    execute.assert_not_awaited()


async def test_phase2_prefix_matches_phase1():
    """Phase 2 prefix is identical to Phase 1 for cache hits."""
    from cyrene import agent

    llm_inputs = []
    llm_tools = []
    phase1_done = False
    phase1_responses = iter([
        {
            "content": "",
            "tool_calls": [{"id": "u1", "function": {"name": "use_tools", "arguments": json.dumps({"task": "test"})}}],
        },
    ])
    phase2_responses = iter([
        {
            "content": "Task is done. All work completed.",
            "tool_calls": [{"id": "q1", "function": {"name": "quit", "arguments": "{}"}}],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        nonlocal phase1_done
        llm_inputs.append([dict(message) for message in messages])
        llm_tools.append(tools)
        if not phase1_done:
            phase1_done = True
            return next(phase1_responses)
        return next(phase2_responses)

    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_save = _patch(_agent_core, "_save_session_messages", AsyncMock())
    try:
        result = await agent._run_main_agent("test task", [], None, 0, "db.sqlite3")
    finally:
        _patch(_agent_core, "_call_llm", _orig_llm)
        _patch(_agent_core, "_save_session_messages", _orig_save)

    assert "Task is done" in result
    phase1_messages, phase2_messages = llm_inputs
    assert phase2_messages[:len(phase1_messages)] == phase1_messages
    assert llm_tools[1] == llm_tools[0]
    appended = phase2_messages[len(phase1_messages):]
    assert appended[0]["role"] == "assistant"
    assert appended[0]["tool_calls"][0]["id"] == "u1"
    assert appended[1]["role"] == "tool"
    assert appended[1]["tool_call_id"] == "u1"
    print("PASS: test_phase2_prefix_matches_phase1")


async def test_phase1_execution_brief_is_handed_to_phase2():
    """Phase 2 receives the concise plan produced by the decision phase."""
    from cyrene import agent

    llm_inputs = []
    brief = (
        "Objective: update the prompt. Steps: inspect the current contract, "
        "edit the relevant prompt, then run focused tests. Validation: prompt "
        "contract assertions and regression tests pass."
    )
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "u1",
                "function": {
                    "name": "use_tools",
                    "arguments": json.dumps({
                        # Simulate a stale provider response from the old schema;
                        # the Phase-2 protocol must remove this duplicate.
                        "task": "improve the prompt",
                        "execution_brief": brief,
                    }),
                },
            }],
        },
        {
            "content": "The prompt was updated and the focused checks passed.",
            "tool_calls": [{
                "id": "q1",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        llm_inputs.append([dict(message) for message in messages])
        return next(responses)

    save_mock = AsyncMock()
    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_save = _patch(_agent_core, "_save_session_messages", save_mock)
    try:
        result = await agent._run_main_agent(
            "improve the prompt", [], None, 0, "db.sqlite3"
        )
    finally:
        _patch(_agent_core, "_call_llm", _orig_llm)
        _patch(_agent_core, "_save_session_messages", _orig_save)

    assert result == "The prompt was updated and the focused checks passed."
    phase2_messages = llm_inputs[1]
    handoff = next(
        message
        for message in phase2_messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert handoff["tool_calls"][0]["id"] == "u1"
    arguments = json.loads(handoff["tool_calls"][0]["function"]["arguments"])
    assert arguments["execution_brief"] == brief
    assert "task" not in arguments
    tool_result = phase2_messages[phase2_messages.index(handoff) + 1]
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "u1"
    saved_messages = save_mock.await_args.args[0]
    assert all(
        not any(
            str(call.get("function", {}).get("name") or "") == "use_tools"
            for call in message.get("tool_calls") or []
        )
        for message in saved_messages
    )


async def test_volatile_context_is_versioned_inside_strict_prefix():
    """Volatile context stays in place instead of moving to each new tail."""
    from cyrene import agent

    llm_inputs = []
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "u1",
                "function": {"name": "use_tools", "arguments": json.dumps({"task": "inspect"})},
            }],
        },
        {
            "content": "Inspection completed successfully.",
            "tool_calls": [{
                "id": "q1",
                "type": "function",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        llm_inputs.append([dict(message) for message in messages])
        return next(responses)

    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_save = _patch(_agent_core, "_save_session_messages", AsyncMock())
    try:
        await agent._run_main_agent(
            "inspect",
            [],
            None,
            0,
            "db.sqlite3",
            ephemeral_system="VOLATILE_V1",
        )
    finally:
        _patch(_agent_core, "_call_llm", _orig_llm)
        _patch(_agent_core, "_save_session_messages", _orig_save)

    phase1_messages, phase2_messages = llm_inputs
    assert phase2_messages[:len(phase1_messages)] == phase1_messages
    volatile = [m for m in phase1_messages if m.get("content") == "VOLATILE_V1"]
    assert len(volatile) == 1
    assert volatile[0]["role"] == "system"


async def test_first_round_phase1_uses_full_wire_tools():
    """First non-deep-research decision call uses the full wire tool set."""
    from cyrene import agent

    calls = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        calls.append((messages, tools))
        return {
            "content": "direct answer",
            "tool_calls": [{"id": "q1", "function": {"name": "quit", "arguments": "{}"}}],
        }

    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_save = _patch(_agent_core, "_save_session_messages", AsyncMock())
    try:
        result = await agent._run_main_agent("hi", [], None, 0, "db.sqlite3")
    finally:
        _patch(_agent_core, "_call_llm", _orig_llm)
        _patch(_agent_core, "_save_session_messages", _orig_save)

    assert result == "direct answer"
    tool_names = [t["function"]["name"] for t in calls[0][1]]
    assert "use_tools" in tool_names
    assert "Read" in tool_names


async def test_fixed_ephemeral_stays_before_user_across_tool_rounds():
    """Run-fixed ephemeral context is part of the append-only prompt prefix."""
    from cyrene import agent

    llm_inputs = []
    responses = iter([
        {
            "content": "",
            "tool_calls": [{"id": "u1", "function": {"name": "use_tools", "arguments": json.dumps({"task": "inspect"})}}],
        },
        {
            "content": "",
            "tool_calls": [{"id": "r1", "function": {"name": "Read", "arguments": json.dumps({"path": "a.txt"})}}],
        },
        {
            "content": "final answer",
            "tool_calls": [{"id": "q1", "function": {"name": "quit", "arguments": "{}"}}],
        },
    ])
    saved_messages = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        llm_inputs.append([{"role": m["role"], "content": m.get("content", "")} for m in messages])
        return next(responses)

    async def fake_save(messages, **kwargs):
        saved_messages.append(messages)

    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_core_exec = _patch(_agent_core, "_execute_tool", AsyncMock(return_value="file content"))
    _orig_save = _patch(_agent_core, "_save_session_messages", fake_save)
    try:
        result = await agent._run_main_agent(
            "inspect",
            [],
            None,
            0,
            "db.sqlite3",
            fixed_ephemeral_system="FIXED_CONTEXT",
        )
    finally:
        _patch(_agent_core, "_call_llm", _orig_llm)
        _patch(_agent_core, "_execute_tool", _orig_core_exec)
        _patch(_agent_core, "_save_session_messages", _orig_save)

    assert result == "final answer"
    effective_main_prompt = llm_inputs[0][0]["content"]
    phase2_first = llm_inputs[1]
    phase2_second = llm_inputs[2]
    assert [m["content"] for m in phase2_first[:3]] == [
        effective_main_prompt,
        "FIXED_CONTEXT",
        "inspect",
    ]
    assert [m["content"] for m in phase2_second[:3]] == [
        effective_main_prompt,
        "FIXED_CONTEXT",
        "inspect",
    ]
    assert phase2_second[:len(phase2_first)] == phase2_first
    assert phase2_second[len(phase2_first)]["role"] == "assistant"
    assert phase2_second[len(phase2_first) + 1]["role"] == "tool"
    assert saved_messages
    assert all(m.get("content") != "FIXED_CONTEXT" for m in saved_messages[-1])


async def test_dual_lane_dynamic_context_follows_cached_history():
    """Run-specific tails do not invalidate the lane history before them."""
    from cyrene import agent
    from cyrene.agent.transcript_policy import ProviderFamily, TranscriptPolicy

    llm_inputs = []
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "u1",
                "function": {
                    "name": "use_tools",
                    "arguments": json.dumps({"execution_brief": "inspect"}),
                },
            }],
        },
        {
            "content": "inspection complete",
            "tool_calls": [{
                "id": "q1",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        llm_inputs.append([dict(message) for message in messages])
        return next(responses)

    history = [
        {"role": "user", "content": "older request", "message_id": "old-user"},
        {
            "role": "assistant",
            "content": "older answer",
            "message_id": "old-assistant",
        },
    ]
    lease = _agent_state.RunModelLease(
        "dual-prefix-test",
        {"primary": ()},
        provider_family=ProviderFamily.OPENAI_COMPATIBLE,
        transcript_policy=TranscriptPolicy.DUAL_LANE,
    )
    lease_token = _agent_state._run_model_lease.set(lease)
    _orig_llm = _patch(_agent_core, "_call_llm", fake_call_llm)
    _orig_save = _patch(_agent_core, "_save_session_messages", AsyncMock())
    _orig_append = _patch(_agent_core, "_append_session_message", AsyncMock())
    _orig_lane_append = _patch(
        _agent_core,
        "append_or_upsert_lane_record",
        AsyncMock(),
    )
    try:
        result = await agent._run_main_agent(
            "current request",
            history,
            None,
            0,
            "db.sqlite3",
            fixed_ephemeral_system="FIXED_CONTEXT",
        )
    finally:
        _patch(_agent_core, "append_or_upsert_lane_record", _orig_lane_append)
        _patch(_agent_core, "_append_session_message", _orig_append)
        _patch(_agent_core, "_save_session_messages", _orig_save)
        _patch(_agent_core, "_call_llm", _orig_llm)
        _agent_state._run_model_lease.reset(lease_token)

    assert result == "inspection complete"
    decision_messages, execution_messages = llm_inputs
    assert [message.get("content") for message in decision_messages[:4]] == [
        decision_messages[0]["content"],
        "older request",
        "older answer",
        "FIXED_CONTEXT",
    ]
    assert [message.get("content") for message in execution_messages[:4]] == [
        execution_messages[0]["content"],
        "older request",
        "older answer",
        "FIXED_CONTEXT",
    ]


async def test_subagent_stable_system_prompt():
    """Subagent keeps messages[0] stable across rounds."""
    from cyrene import subagent
    from cyrene.runtime import inbox
    import cyrene.tooling as tools

    llm_inputs = []
    responses = iter([
        {
            "content": "finding 1",
            "tool_calls": [{"id": "t1", "function": {"name": "Read", "arguments": json.dumps({"path": "test.txt"})}}],
        },
        {
            "content": "finding 2",
            "tool_calls": [{"id": "q1", "function": {"name": "quit", "arguments": "{}"}}],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        saved = [{"role": m["role"], "content": str(m.get("content", ""))[:200]} for m in messages]
        llm_inputs.append(saved)
        assert max_tokens is None
        return next(responses)

    async def fake_wait(agent_id, inbox_check_func, mark_read_func=None, max_wait=600, result=""):
        return ""

    _orig_llm = _patch(_agent_state, "_call_llm", fake_call_llm)
    _orig_exec = _patch(tools, "execute_wire_tool", AsyncMock(return_value="file content"))
    _orig_wait = _patch(subagent, "wait_for_others", fake_wait)
    _orig_save = _patch(subagent, "save_messages", AsyncMock())
    _orig_run = _patch(subagent, "set_running", AsyncMock())
    _orig_pub = _patch(subagent, "_publish_registry_event", AsyncMock())
    _orig_ctx = _patch(subagent, "get_context", AsyncMock(
        return_value="[活跃子 agent]\n  alice: test [工作中]"
    ))
    _orig_inbox_ctx = _patch(inbox, "get_inbox_context", lambda aid, session_id="": "")
    _orig_mark = _patch(inbox, "mark_all_read", AsyncMock())
    try:
        await subagent._run_subagent("test_agent", "test task", None, 0, "db.sqlite3")
    finally:
        _patch(_agent_state, "_call_llm", _orig_llm)
        _patch(tools, "execute_wire_tool", _orig_exec)
        _patch(subagent, "wait_for_others", _orig_wait)
        _patch(subagent, "save_messages", _orig_save)
        _patch(subagent, "set_running", _orig_run)
        _patch(subagent, "_publish_registry_event", _orig_pub)
        _patch(subagent, "get_context", _orig_ctx)
        _patch(inbox, "get_inbox_context", _orig_inbox_ctx)
        _patch(inbox, "mark_all_read", _orig_mark)

    for i, msgs in enumerate(llm_inputs):
        assert msgs[0]["role"] == "system", f"Call {i}: messages[0] should be system"
        assert "[活跃子 agent]" not in msgs[0]["content"], (
            f"Call {i}: system prompt leaked registry context: {msgs[0]['content'][:100]}"
        )
        assert "[收件箱]" not in msgs[0]["content"], (
            f"Call {i}: system prompt leaked inbox context"
        )

    call1 = llm_inputs[0]
    registry_msgs = [m for m in call1 if m["role"] == "user" and "[活跃子 agent]" in m.get("content", "")]
    assert registry_msgs == [], (
        "Execution subagents are independent workers and should not receive "
        "discussion registry context."
    )

    print("PASS: test_subagent_stable_system_prompt")


async def test_subagent_empty_quit_exits_without_feedback_retry():
    """Subagent no longer retries empty quit with validator feedback."""
    from cyrene import subagent
    from cyrene.runtime import inbox
    import cyrene.tooling as tools

    llm_inputs = []
    responses = iter([
        {
            "content": "Done.",
            "tool_calls": [{"id": "q1", "function": {"name": "quit", "arguments": "{}"}}],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        saved = [{"role": m["role"], "content": str(m.get("content", ""))[:200]} for m in messages]
        llm_inputs.append(saved)
        return next(responses)

    async def fake_wait(agent_id, inbox_check_func, mark_read_func=None, max_wait=600, result=""):
        return ""

    _orig_llm = _patch(_agent_state, "_call_llm", fake_call_llm)
    _orig_exec = _patch(tools, "execute_wire_tool", AsyncMock(return_value="ok"))
    _orig_wait = _patch(subagent, "wait_for_others", fake_wait)
    _orig_save = _patch(subagent, "save_messages", AsyncMock())
    _orig_run = _patch(subagent, "set_running", AsyncMock())
    _orig_pub = _patch(subagent, "_publish_registry_event", AsyncMock())
    _orig_ctx = _patch(subagent, "get_context", AsyncMock(return_value=""))
    _orig_resume = _patch(subagent, "set_resumed", AsyncMock())
    _orig_inbox_ctx = _patch(inbox, "get_inbox_context", lambda aid, session_id="": "")
    _orig_mark = _patch(inbox, "mark_all_read", AsyncMock())
    _orig_send = _patch(inbox, "send_message", AsyncMock())
    try:
        result = await subagent._run_subagent("test_agent", "test task", None, 0, "db.sqlite3")
    finally:
        _patch(_agent_state, "_call_llm", _orig_llm)
        _patch(tools, "execute_wire_tool", _orig_exec)
        _patch(subagent, "wait_for_others", _orig_wait)
        _patch(subagent, "save_messages", _orig_save)
        _patch(subagent, "set_running", _orig_run)
        _patch(subagent, "_publish_registry_event", _orig_pub)
        _patch(subagent, "get_context", _orig_ctx)
        _patch(subagent, "set_resumed", _orig_resume)
        _patch(inbox, "get_inbox_context", _orig_inbox_ctx)
        _patch(inbox, "mark_all_read", _orig_mark)
        _patch(inbox, "send_message", _orig_send)

    assert result == "Done."
    assert len(llm_inputs) == 1
    print("PASS: test_subagent_empty_quit_exits_without_feedback_retry")


async def test_subagent_resume_preserves_old_context_as_append_only_history():
    """Resumed subagent never rewrites context that reached an earlier request."""
    from cyrene import subagent
    from cyrene.runtime import inbox
    import cyrene.tooling as tools

    old_messages = [
        {"role": "system", "content": "You are a sub-agent..."},
        {"role": "user", "content": "original task"},
        {"role": "user", "content": "[活跃子 agent]\n  alice: task [工作中]"},  # old context
        {"role": "assistant", "content": "done"},
    ]

    llm_inputs = []
    responses = iter([
        {
            "content": "resumed finding",
            "tool_calls": [{"id": "q1", "function": {"name": "quit", "arguments": "{}"}}],
        },
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        saved = [{"role": m["role"], "content": str(m.get("content", ""))[:200]} for m in messages]
        llm_inputs.append(saved)
        return next(responses)

    async def fake_wait(agent_id, inbox_check_func, mark_read_func=None, max_wait=600, result=""):
        return ""

    _orig_llm = _patch(_agent_state, "_call_llm", fake_call_llm)
    _orig_exec = _patch(tools, "execute_wire_tool", AsyncMock(return_value="ok"))
    _orig_wait = _patch(subagent, "wait_for_others", fake_wait)
    _orig_save = _patch(subagent, "save_messages", AsyncMock())
    _orig_run = _patch(subagent, "set_running", AsyncMock())
    _orig_pub = _patch(subagent, "_publish_registry_event", AsyncMock())
    _orig_ctx = _patch(subagent, "get_context", AsyncMock(
        return_value="[活跃子 agent]\n  bob: new task [工作中]"
    ))
    _orig_inbox_ctx = _patch(inbox, "get_inbox_context", lambda aid, session_id="": "")
    _orig_mark = _patch(inbox, "mark_all_read", AsyncMock())
    try:
        await subagent._run_subagent(
            "test_agent", "task", None, 0, "db.sqlite3",
            resume_messages=old_messages,
        )
    finally:
        _patch(_agent_state, "_call_llm", _orig_llm)
        _patch(tools, "execute_wire_tool", _orig_exec)
        _patch(subagent, "wait_for_others", _orig_wait)
        _patch(subagent, "save_messages", _orig_save)
        _patch(subagent, "set_running", _orig_run)
        _patch(subagent, "_publish_registry_event", _orig_pub)
        _patch(subagent, "get_context", _orig_ctx)
        _patch(inbox, "get_inbox_context", _orig_inbox_ctx)
        _patch(inbox, "mark_all_read", _orig_mark)

    call_msgs = llm_inputs[0]
    context_msgs = [m for m in call_msgs if "[活跃子 agent]" in str(m.get("content", ""))]
    assert context_msgs == [{
        "role": "user",
        "content": "[活跃子 agent]\n  alice: task [工作中]",
    }], "Previously observed context must remain in the append-only prefix."

    print("PASS: test_subagent_resume_preserves_old_context_as_append_only_history")


async def main():
    await test_phase1_retry_with_unified_system_prompt()
    await test_phase2_prefix_matches_phase1()
    await test_first_round_phase1_uses_full_wire_tools()
    await test_fixed_ephemeral_stays_before_user_across_tool_rounds()
    await test_subagent_stable_system_prompt()
    await test_subagent_empty_quit_exits_without_feedback_retry()
    await test_subagent_resume_preserves_old_context_as_append_only_history()
    print("\nAll 5 cache-fix verification tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
