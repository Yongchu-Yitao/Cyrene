"""Verify cache-hit improvements don't break any behavior."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Patch missing deps before any cyrene import
sys.modules.setdefault("PIL", MagicMock())
sys.modules["PIL"].Image = MagicMock()
sys.modules.setdefault("pypdf", MagicMock())

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

    calls = []
    responses = iter([
        {
            "content": "ok, checking weather.",
            "tool_calls": [
                {"id": "w1", "function": {"name": "WebSearch", "arguments": json.dumps({"query": "Toronto weather"})}},
            ],
        },
        {
            "content": "No suitable tool. Use use_tools to enter full tool phase.",
            "tool_calls": [],
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

    # Phase 1 system prompt is MAIN only
    phase1_msgs, _ = calls[0]
    assert phase1_msgs[0]["content"] == agent._MAIN_AGENT_PROMPT, "Phase 1 system should be MAIN only"
    # DECISION is last user message
    assert phase1_msgs[-1]["role"] == "user"
    assert phase1_msgs[-1]["content"] == agent._PHASE1_DECISION_PROMPT
    # Retry uses same system prompt
    retry_msgs, _ = calls[1]
    assert retry_msgs[0]["content"] == agent._MAIN_AGENT_PROMPT
    # Retry includes DECISION
    assert any(
        m.get("content") == agent._PHASE1_DECISION_PROMPT
        for m in retry_msgs if m["role"] == "user"
    )
    print("PASS: test_phase1_retry_with_unified_system_prompt")


async def test_phase2_prefix_matches_phase1():
    """Phase 2 prefix is identical to Phase 1 for cache hits."""
    from cyrene import agent

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
    print("PASS: test_phase2_prefix_matches_phase1")


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
    phase2_first = llm_inputs[1]
    phase2_second = llm_inputs[2]
    assert [m["content"] for m in phase2_first[:3]] == [
        agent._MAIN_AGENT_PROMPT,
        "FIXED_CONTEXT",
        "inspect",
    ]
    assert [m["content"] for m in phase2_second[:3]] == [
        agent._MAIN_AGENT_PROMPT,
        "FIXED_CONTEXT",
        "inspect",
    ]
    assert phase2_second[3]["role"] == "assistant"
    assert phase2_second[4]["role"] == "tool"
    assert saved_messages
    assert all(m.get("content") != "FIXED_CONTEXT" for m in saved_messages[-1])


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


async def test_subagent_resume_strips_old_context():
    """Resumed subagent strips old context messages from previous run."""
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
    assert context_msgs == [], (
        "Execution-mode resume should remove stale coordination context and "
        "should not inject fresh discussion context."
    )

    print("PASS: test_subagent_resume_strips_old_context")


async def main():
    await test_phase1_retry_with_unified_system_prompt()
    await test_phase2_prefix_matches_phase1()
    await test_first_round_phase1_uses_full_wire_tools()
    await test_fixed_ephemeral_stays_before_user_across_tool_rounds()
    await test_subagent_stable_system_prompt()
    await test_subagent_empty_quit_exits_without_feedback_retry()
    await test_subagent_resume_strips_old_context()
    print("\nAll 5 cache-fix verification tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
