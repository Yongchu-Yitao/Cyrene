import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Patch missing deps before any cyrene import
try:
    _pil_missing = importlib.util.find_spec("PIL") is None
except ValueError:
    _pil_missing = "PIL" not in sys.modules
if _pil_missing:
    sys.modules.setdefault("PIL", MagicMock())
    sys.modules["PIL"].Image = MagicMock()
try:
    _pypdf_missing = importlib.util.find_spec("pypdf") is None
except ValueError:
    _pypdf_missing = "pypdf" not in sys.modules
if _pypdf_missing:
    sys.modules.setdefault("pypdf", MagicMock())


def _patch_call_llm(monkeypatch, fake):
    """Patch _call_llm in all sub-modules that import it at module level."""
    from cyrene.agent import (
        agent as _a,
        coordinator as _c,
        guidance as _g,
        replies as _r,
        session as _se,
        state as _s,
    )
    for _mod in (_s, _a, _c, _g, _r, _se):
        if hasattr(_mod, '_call_llm'):
            monkeypatch.setattr(_mod, '_call_llm', fake)


def _patch_call_llm_stream(monkeypatch, fake):
    from cyrene.agent import guidance as _g, replies as _r
    for _mod in (_g, _r):
        monkeypatch.setattr(_mod, '_call_llm_stream', fake)


def _patch_save_session(monkeypatch, fake):
    """Patch _save_session_messages in all sub-modules that import it."""
    from cyrene.agent import agent as _a, session as _se
    for _mod in (_a, _se):
        if hasattr(_mod, '_save_session_messages'):
            monkeypatch.setattr(_mod, '_save_session_messages', fake)


def _patch_append_session(monkeypatch, fake):
    from cyrene.agent import agent as _a, session as _se
    for _mod in (_a, _se):
        if hasattr(_mod, '_append_session_message'):
            monkeypatch.setattr(_mod, '_append_session_message', fake)


def _patch_execute_tool(monkeypatch, fake):
    """Patch _execute_tool in all sub-modules that import it."""
    from cyrene.agent import agent as _a, coordinator as _c
    for _mod in (_a, _c):
        if hasattr(_mod, '_execute_tool'):
            monkeypatch.setattr(_mod, '_execute_tool', fake)


def _patch_state_file(monkeypatch, path):
    from cyrene.agent import state as _s
    monkeypatch.setattr(_s, 'STATE_FILE', path)


def _patch_data_dir(monkeypatch, path):
    from cyrene.agent import state as _s
    monkeypatch.setattr(_s, 'DATA_DIR', path)


def _patch_runtime_context(monkeypatch, *, get_context=None, get_memory_context=None):
    from cyrene import agent
    from cyrene.agent import coordinator as _c
    if get_context is not None:
        monkeypatch.setattr(agent, "get_context", get_context)
        monkeypatch.setattr(_c, "get_context", get_context)
    if get_memory_context is not None:
        monkeypatch.setattr(agent, "get_memory_context", get_memory_context)
        monkeypatch.setattr(_c, "get_memory_context", get_memory_context)


async def test_execution_agent_returns_quit_text(monkeypatch):
    from cyrene import agent

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        return {
            "content": "scheduled task completed",
            "tool_calls": [{"id": "q1", "function": {"name": "quit", "arguments": "{}"}}],
        }

    _patch_call_llm(monkeypatch, fake_call_llm)
    result = await agent._run_execution_agent("do something", None, 0, "db.sqlite3")
    assert result == "scheduled task completed"


def test_get_memory_context_includes_short_term_by_default(tmp_path, monkeypatch):
    from cyrene import memory
    from cyrene.runtime import settings_store
    from cyrene.runtime.memory import short_term

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "user prefers concise replies",
            "type": "preference",
            "first_seen": "2026-05-18",
            "last_mentioned": "2026-05-19",
            "mention_count": 2,
            "emotional_valence": 0,
        }
    ])
    monkeypatch.setattr(settings_store, "is_soul_active", lambda: True)
    monkeypatch.setattr(memory, "read_shallow_memory", lambda: "## SELF:IDENTITY\n- test memory")
    context = memory.get_memory_context()

    assert "SELF:IDENTITY" in context
    assert "Short-term cross-session memory" in context
    assert "user prefers concise replies" in context


def test_get_memory_context_can_skip_short_term(tmp_path, monkeypatch):
    from cyrene import memory
    from cyrene.runtime import settings_store
    from cyrene.runtime.memory import short_term

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "user likes jasmine tea",
            "type": "fact",
            "first_seen": "2026-05-18",
            "last_mentioned": "2026-05-19",
            "mention_count": 1,
            "emotional_valence": 0,
        }
    ])
    monkeypatch.setattr(settings_store, "is_soul_active", lambda: True)
    monkeypatch.setattr(memory, "read_shallow_memory", lambda: "## SELF:BELIEFS\n- test belief")
    context = memory.get_memory_context(include_short_term=False)

    assert "SELF:BELIEFS" in context
    assert "Short-term cross-session memory" not in context
    assert "user likes jasmine tea" not in context


def test_agent_module_reexports_memory_helpers():
    from cyrene import agent, memory
    from cyrene.runtime.memory import short_term

    assert agent.get_context is short_term.get_context
    assert agent.get_memory_context is memory.get_memory_context


async def test_execute_tool_awaits_event_publish(monkeypatch):
    from cyrene.tooling import executor as tools

    seen = {"published": False}

    async def fake_handler(arguments, bot, chat_id, db_path, notify_state):
        return "ok"

    async def fake_publish_event(event, **kwargs):
        seen["published"] = True
        seen["event"] = event

    monkeypatch.setitem(tools.TOOL_HANDLERS, "__test_tool__", fake_handler)

    from cyrene.observability import debug

    monkeypatch.setattr(debug, "publish_event", fake_publish_event)
    result = await tools._execute_tool("__test_tool__", {}, None, 0, "db.sqlite3", None)

    assert result == "ok"
    assert seen["published"] is True
    assert seen["event"]["type"] == "tool_call"

    tools.TOOL_HANDLERS.pop("__test_tool__", None)


async def test_execute_tool_completion_carries_active_tool_call_id(monkeypatch):
    from cyrene.observability import debug
    from cyrene.tooling import executor as tool_executor

    published = []

    async def fake_handler(arguments, bot, chat_id, db_path, notify_state):
        return "ok"

    async def fake_publish_event(event, **kwargs):
        published.append(event)

    monkeypatch.setitem(tool_executor.TOOL_HANDLERS, "__identified_tool__", fake_handler)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)
    token = tool_executor._active_tool_call_id.set("call_live_1")
    try:
        result = await tool_executor._execute_tool(
            "__identified_tool__", {}, None, 0, "db.sqlite3", None
        )
    finally:
        tool_executor._active_tool_call_id.reset(token)

    assert result == "ok"
    assert published[-1]["type"] == "tool_call"
    assert published[-1]["tool_call_id"] == "call_live_1"


async def test_main_agent_publishes_tool_start_with_identity_and_redacted_args(monkeypatch):
    from cyrene.agent import agent as agent_core

    publish = AsyncMock()
    monkeypatch.setattr(agent_core, "_publish_runtime_event", publish)

    await agent_core._publish_tool_call_started(
        "call_start_1", "WebSearch", {"query": "Nanjing travel"}
    )

    event = publish.await_args.args[0]
    assert event["type"] == "tool_call_started"
    assert event["tool_call_id"] == "call_start_1"
    assert event["tool"] == "WebSearch"
    assert event["args"] == {"query": "Nanjing travel"}
    assert event["timestamp"]


@pytest.mark.parametrize(
    ("arguments", "expected_status"),
    [
        ({"operation": "discover", "query": "memory"}, "completed"),
        ({"operation": "invalid"}, "failed"),
    ],
)
async def test_progressive_wire_call_always_publishes_terminal_lifecycle(
    monkeypatch, arguments, expected_status
):
    from cyrene.agent import agent as agent_core

    published = []

    async def capture(event):
        published.append(event)

    monkeypatch.setattr(agent_core, "_publish_runtime_event", capture)

    result = await agent_core._execute_tool_for_call(
        "call_gateway_1",
        "memory_tools",
        arguments,
        None,
        0,
        "",
    )

    assert result
    assert [event["type"] for event in published] == [
        "tool_call_started",
        "tool_call_finished",
    ]
    assert all(event["tool_call_id"] == "call_gateway_1" for event in published)
    assert all(event["tool"] == "memory_tools" for event in published)
    assert all(event["args"] == arguments for event in published)
    assert all(event["timestamp"] for event in published)
    assert published[-1]["status"] == expected_status
    assert published[-1]["failed"] is (expected_status == "failed")


async def test_ordered_tool_batch_publishes_each_start_at_real_execution_time(
    monkeypatch, tmp_path
):
    from cyrene.agent import agent as agent_core
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    events = []

    async def capture(event):
        events.append((str(event.get("type") or ""), str(event.get("tool") or "")))

    async def execute(name, _args, _bot, _chat_id, _db_path, _notify_state):
        if name == "send_message":
            events.append(("intermediate_message", name))
        return "ok"

    monkeypatch.setattr(agent_core, "_publish_runtime_event", capture)
    monkeypatch.setattr(agent_core, "_execute_tool", execute)

    inbox = WorkbenchAgentInbox(
        "chat_real_tool_order",
        str(tmp_path / "workbench.db"),
        run_id="run_real_tool_order",
    )
    try:
        inbox.submit_tool_batch([
            (
                "call_send",
                "send_message",
                lambda: agent_core._execute_tool_for_call(
                    "call_send", "send_message", {"text": "我先查一下。"}, None, 0, ""
                ),
                agent_core._inbox_tool_metadata(
                    "send_message", {"text": "我先查一下。"}
                ),
            ),
            (
                "call_search",
                "WebSearch",
                lambda: agent_core._execute_tool_for_call(
                    "call_search", "WebSearch", {"query": "天气"}, None, 0, ""
                ),
                agent_core._inbox_tool_metadata("WebSearch", {"query": "天气"}),
            ),
        ], batch_id="batch_real_tool_order")
        assert await inbox.wait_for_tool_result("call_send") == "ok"
        assert await inbox.wait_for_tool_result("call_search") == "ok"
    finally:
        await inbox.close()

    assert events == [
        ("tool_call_started", "send_message"),
        ("intermediate_message", "send_message"),
        ("tool_call_finished", "send_message"),
        ("tool_call_started", "WebSearch"),
        ("tool_call_finished", "WebSearch"),
    ]


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (RuntimeError("boom"), "failed"),
        (asyncio.CancelledError(), "cancelled"),
    ],
)
async def test_tool_call_terminal_lifecycle_covers_errors_and_cancellation(
    monkeypatch, failure, expected_status
):
    from cyrene.agent import agent as agent_core

    published = []

    async def fail(*_args, **_kwargs):
        raise failure

    async def capture(event):
        published.append(event)

    monkeypatch.setattr(agent_core, "_execute_tool", fail)
    monkeypatch.setattr(agent_core, "_publish_runtime_event", capture)

    with pytest.raises(type(failure)):
        await agent_core._execute_tool_for_call(
            "call_failure_1",
            "memory_tools",
            {"operation": "discover"},
            None,
            0,
            "",
        )

    assert published[-1]["type"] == "tool_call_finished"
    assert published[-1]["status"] == expected_status
    assert published[-1]["failed"] is (expected_status == "failed")


def test_main_agent_inbox_metadata_includes_visible_tool_arguments():
    from cyrene.agent import agent as agent_core

    metadata = agent_core._inbox_tool_metadata(
        "WebSearch", {"query": "Nanjing travel", "limit": 5}
    )

    assert metadata["arguments"] == {"query": "Nanjing travel", "limit": 5}


async def test_execute_tool_timeout_becomes_a_structured_tool_result(monkeypatch):
    from cyrene.observability import debug
    from cyrene.tooling import executor as tool_executor

    async def never_returns(*_args, **_kwargs):
        await asyncio.Event().wait()

    async def fake_publish_event(*_args, **_kwargs):
        return None

    monkeypatch.setitem(tool_executor.TOOL_HANDLERS, "__timeout_tool__", never_returns)
    monkeypatch.setattr(tool_executor, "_tool_timeout_seconds", lambda *_args: 0.01)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    result = await tool_executor._execute_tool(
        "__timeout_tool__", {}, None, 0, "", None
    )

    assert result == "Tool failed: __timeout_tool__ timed out after 0.01 seconds."


def test_filesystem_tools_offload_blocking_io_and_bound_scans():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src" / "cyrene" / "tool_impl" / "core"
    read_source = (root / "read.py").read_text(encoding="utf-8")
    write_source = (root / "write.py").read_text(encoding="utf-8")
    edit_source = (root / "edit.py").read_text(encoding="utf-8")
    glob_source = (root / "glob.py").read_text(encoding="utf-8")
    grep_source = (root / "grep.py").read_text(encoding="utf-8")

    assert "await asyncio.to_thread(path.read_text" in read_source
    assert "await asyncio.to_thread(write_file)" in write_source
    assert "await asyncio.to_thread(edit_file)" in edit_source
    assert "await asyncio.to_thread(scan)" in glob_source
    assert "await asyncio.to_thread(scan)" in grep_source
    assert "_MAX_CANDIDATES" in glob_source
    assert "_MAX_CANDIDATES" in grep_source
    assert "_MAX_FILE_BYTES" in grep_source


async def test_tool_loop_continues_until_completion_and_persists_final_message(tmp_path, monkeypatch):
    from cyrene.agent import agent as _agent_core
    from cyrene.agent import state as _agent_state

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"_session_epoch": _agent_state._session_epoch, "messages": []}), encoding="utf-8")
    _patch_state_file(monkeypatch, state_file)
    _patch_data_dir(monkeypatch, tmp_path)
    calls = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        if tools is None:
            return {"content": "final answer from gathered tool results"}
        calls.append(tools)
        if len(calls) == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "phase1",
                    "function": {"name": "use_tools", "arguments": "{\"task\":\"check\"}"},
                }],
            }
        if len(calls) <= 17:
            return {
                "content": "",
                "tool_calls": [{
                    "id": f"tool{len(calls)}",
                    "function": {"name": "WebSearch", "arguments": "{\"query\":\"x\"}"},
                }],
            }
        return {"content": "final answer from gathered tool results"}

    async def fake_execute_tool(*args, **kwargs):
        return "tool result"

    _patch_call_llm(monkeypatch, fake_call_llm)
    monkeypatch.setattr(_agent_core, "_execute_tool", fake_execute_tool)
    result = await _agent_core._run_main_agent(
        "check",
        [],
        None,
        0,
        "db.sqlite3",
        system_prompt="test system",
        client_request_id="req_limit",
    )

    assert result == "final answer from gathered tool results"
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["messages"][-1]["role"] == "assistant"
    assert saved["messages"][-1]["content"] == result
    assert saved["messages"][-1]["client_request_id"] == "req_limit"
    assert len(calls) == 18


@pytest.mark.parametrize(
    ("public_message", "expected_key", "expected_params"),
    [
        ("分析 Cyrene 的能耗", "phase.useTools", {"task": "分析 Cyrene 的能耗"}),
        ("", "phase.useToolsAttachments", None),
    ],
)
async def test_use_tools_phase_hides_internal_attachment_prompt(
    monkeypatch, public_message, expected_key, expected_params
):
    from cyrene.agent import agent as _agent_core

    internal_message = (
        public_message
        + "\n[Uploaded attachments]\n"
        + "The user uploaded the following files into the local workspace-accessible runtime data directory."
    )
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "phase1",
                "function": {"name": "use_tools", "arguments": '{"task":"inspect"}'},
            }],
        },
        {
            "content": "Attachment inspection completed.",
            "tool_calls": [{
                "id": "quit1",
                "function": {
                    "name": "quit",
                    "arguments": "{}",
                },
            }],
        },
    ])
    events = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        return next(responses)

    async def fake_publish_runtime_event(event):
        events.append(dict(event))

    _patch_call_llm(monkeypatch, fake_call_llm)
    _patch_append_session(monkeypatch, AsyncMock())
    _patch_save_session(monkeypatch, AsyncMock())
    monkeypatch.setattr(_agent_core, "_publish_runtime_event", fake_publish_runtime_event)

    await _agent_core._run_main_agent(
        internal_message,
        [],
        None,
        0,
        "db.sqlite3",
        public_user_message=public_message,
        public_attachments=[{"name": "energy.png"}],
    )

    phase_event = next(
        event
        for event in events
        if event.get("from") == "phase1_decision"
        and event.get("to") == "phase2_execution"
    )
    assert phase_event["detail_key"] == expected_key
    if expected_params is None:
        assert "detail_params" not in phase_event
    else:
        assert phase_event["detail_params"] == expected_params
    assert "[Uploaded attachments]" not in phase_event["detail"]
    assert "The user uploaded" not in phase_event["detail"]


def test_merge_live_block_preserves_distinct_empty_tool_call_assistants():
    from cyrene.agent.session import _merge_live_block

    existing = [
        {
            "role": "assistant",
            "content": "",
            "round_id": "round_a",
            "message_id": "assistant_1",
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": "WebSearch", "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "round_id": "round_a",
            "message_id": "tool_1",
            "tool_call_id": "call_1",
            "content": "first result",
        },
    ]
    incoming = [
        {
            "role": "assistant",
            "content": "",
            "round_id": "round_a",
            "message_id": "assistant_2",
            "tool_calls": [{
                "id": "call_2",
                "function": {"name": "browser_navigate", "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "round_id": "round_a",
            "message_id": "tool_2",
            "tool_call_id": "call_2",
            "content": "second result",
        },
    ]

    merged = _merge_live_block(existing, incoming)

    assistant_ids = [
        message["message_id"]
        for message in merged
        if message.get("role") == "assistant"
    ]
    assert assistant_ids == ["assistant_1", "assistant_2"]
    assert [message.get("tool_call_id") for message in merged if message.get("role") == "tool"] == ["call_1", "call_2"]


async def test_subagent_cannot_send_user_visible_message(monkeypatch):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.tool_impl.delivery import send_message as tools

    called = {"append": False}

    async def fake_append_system_message(*args, **kwargs):
        called["append"] = True
        return {}

    monkeypatch.setattr(_agent_session, "append_system_message", fake_append_system_message)

    token = agent._current_agent_id.set("agent_worker")
    try:
        result = await tools._tool_send_user_message({"text": "hello from subagent"}, None, 0, "db.sqlite3", None)
    finally:
        agent._current_agent_id.reset(token)

    assert "Only the main agent can send a user-visible WebUI message" in result
    assert called["append"] is False


def test_subagent_tool_defs_hide_main_only_tools():
    from cyrene.tooling import catalog as tools

    main_defs = {item["function"]["name"] for item in tools.get_active_tool_defs_for_actor("main")}
    sub_defs = {item["function"]["name"] for item in tools.get_active_tool_defs_for_actor("subagent")}

    assert "send_message" in main_defs
    assert "spawn_subagent" in main_defs
    assert "send_message" not in sub_defs
    assert "spawn_subagent" not in sub_defs
    assert "ask_user" not in sub_defs
    assert "send_agent_message" in sub_defs


async def test_recall_memory_tool_returns_recent_short_term_entries(tmp_path):
    from cyrene.runtime.memory import short_term
    from cyrene.tool_impl.memory import recall_memory as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "user prefers concise replies",
            "type": "preference",
            "first_seen": "2026-05-18",
            "last_mentioned": "2026-05-20",
            "mention_count": 1,
            "emotional_valence": 0,
        },
        {
            "content": "user prefers detailed reports",
            "type": "preference",
            "first_seen": "2026-05-17",
            "last_mentioned": "2026-05-19",
            "mention_count": 2,
            "emotional_valence": 0,
        },
        {
            "content": "user uses macOS",
            "type": "fact",
            "first_seen": "2026-05-16",
            "last_mentioned": "2026-05-21",
            "mention_count": 1,
            "emotional_valence": 0,
        },
    ])

    result = await tools._tool_recall_memory(
        {"query": "prefers", "type": "preference", "limit": 2},
        None,
        0,
        "db.sqlite3",
        None,
    )
    payload = json.loads(result)

    assert [item["content"] for item in payload["memories"]] == [
        "user prefers concise replies",
        "user prefers detailed reports",
    ]
    assert all(item["memory_id"].startswith("stm_") for item in payload["memories"])
    assert "matches" not in payload
    assert "soul_memory" not in payload


async def test_list_memories_reports_total_and_supports_filters_and_pagination(
    tmp_path,
):
    from cyrene.runtime.memory import short_term
    from cyrene.tool_impl.memory import list_memories as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "user prefers concise replies",
            "type": "preference",
            "first_seen": "2026-05-18",
            "last_mentioned": "2026-05-20",
            "mention_count": 1,
        },
        {
            "content": "user uses macOS",
            "type": "fact",
            "first_seen": "2026-05-16",
            "last_mentioned": "2026-05-21",
            "mention_count": 2,
        },
        {
            "content": "superseded preference",
            "type": "preference",
            "first_seen": "2026-05-15",
            "last_mentioned": "2026-05-19",
            "mention_count": 1,
            "stale": True,
            "retired_at": "2026-05-22T10:00:00+08:00",
            "retire_reason": "corrected",
        },
    ])

    result = await tools._tool_list_memories(
        {"status": "all", "type": "preference", "limit": 1, "offset": 1},
        None,
        0,
        "db.sqlite3",
        None,
    )
    payload = json.loads(result)

    assert payload["total"] == 2
    assert payload["returned"] == 1
    assert payload["has_more"] is False
    assert payload["memories"][0]["status"] == "retired"
    assert payload["memories"][0]["retire_reason"] == "corrected"


async def test_list_memories_defaults_to_all_active_memories(tmp_path):
    from cyrene.runtime.memory import short_term
    from cyrene.tool_impl.memory import list_memories as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": f"memory {index}",
            "type": "fact",
            "first_seen": "2026-05-18",
            "last_mentioned": f"2026-05-{index + 1:02d}",
        }
        for index in range(3)
    ] + [{
        "content": "retired memory",
        "type": "fact",
        "first_seen": "2026-05-01",
        "last_mentioned": "2026-05-01",
        "stale": True,
    }])

    result = await tools._tool_list_memories(
        {},
        None,
        0,
        "db.sqlite3",
        None,
    )
    payload = json.loads(result)

    assert payload["status"] == "active"
    assert payload["total"] == 3
    assert payload["total_by_scope"] == {"short_term": 3, "project": 0}
    assert payload["returned"] == 3
    assert payload["has_more"] is False


async def test_retire_short_term_memory_tool_marks_entry_stale(tmp_path):
    from cyrene.runtime.memory import short_term
    from cyrene.tool_impl.memory import recall_memory
    from cyrene.tool_impl.memory import retire_short_term_memory as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "user incorrectly prefers verbose replies",
            "type": "preference",
            "first_seen": "2026-05-18",
            "last_mentioned": "2026-05-20",
            "mention_count": 1,
            "emotional_valence": 0,
        },
        {
            "content": "user uses macOS",
            "type": "fact",
            "first_seen": "2026-05-16",
            "last_mentioned": "2026-05-21",
            "mention_count": 1,
            "emotional_valence": 0,
        },
    ])
    memory_id = short_term.entry_id(short_term.load_entries()[0])

    result = await tools._tool_retire_short_term_memory(
        {"memory_id": memory_id, "reason": "user corrected this"},
        None,
        0,
        "db.sqlite3",
        None,
    )
    payload = json.loads(result)

    assert payload["status"] == "success"
    assert payload["changed"] is True
    entries = short_term.load_entries()
    assert entries[0]["id"] == memory_id
    assert entries[0]["stale"] is True
    assert entries[0]["retire_reason"] == "user corrected this"
    assert "user incorrectly prefers verbose replies" not in short_term.get_context()

    recall_result = await recall_memory._tool_recall_memory(
        {"query": "verbose", "limit": 10},
        None,
        0,
        "db.sqlite3",
        None,
    )
    recall_payload = json.loads(recall_result)
    assert recall_payload["available_matches"] == 0


async def test_recall_memory_tool_uses_or_for_multiple_terms(tmp_path):
    from cyrene.runtime.memory import short_term
    from cyrene.tool_impl.memory import recall_memory as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "用户本人照片可用于身份识别",
            "type": "fact",
            "first_seen": "2026-06-20",
            "last_mentioned": "2026-06-21",
            "mention_count": 1,
            "emotional_valence": 0,
        },
    ])

    result = await tools._tool_recall_memory(
        {"query": "照片 人物 头像 识别", "limit": 10},
        None,
        0,
        "db.sqlite3",
        None,
    )
    payload = json.loads(result)

    assert payload["available_matches"] == 1
    assert payload["memories"][0]["content"] == "用户本人照片可用于身份识别"


async def test_recall_memory_tool_bounds_large_results(tmp_path):
    from cyrene.runtime.memory import short_term
    from cyrene.tool_impl.memory import recall_memory as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": f"memory-{index}-" + ("x" * 10_000),
            "type": "fact",
            "first_seen": "2026-05-18",
            "last_mentioned": f"2026-05-{20 - index:02d}",
            "mention_count": 1,
            "emotional_valence": 0,
        }
        for index in range(20)
    ])

    result = await tools._tool_recall_memory(
        {"limit": 20},
        None,
        0,
        "db.sqlite3",
        None,
    )
    payload = json.loads(result)

    assert payload["truncated"] is True
    assert len(result) < 10_000
    assert all(len(item["content"]) <= 801 for item in payload["memories"])
    assert all(item["content_truncated"] is True for item in payload["memories"])


async def test_recall_conversation_tool_returns_archived_matches(tmp_path, monkeypatch):
    from cyrene.runtime.memory import conversations
    from cyrene.tool_impl.memory import recall_conversation as tools

    conversations_dir = tmp_path / "conversations"
    conversations_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(conversations, "CONVERSATIONS_DIR", conversations_dir)

    (conversations_dir / "2026-05-19.md").write_text(
        "# Conversations - 2026-05-19\n\n"
        "<!-- session_title: 第一场 -->\n\n"
        "## 09:00:00 UTC\n\n"
        "<!-- archive_session_id: session_alpha -->\n"
        "<!-- session_title: 第一场 -->\n"
        "<!-- round_id: round_1 -->\n"
        "<!-- round_title: 设计角色 -->\n\n"
        "**User**: 先聊角色设定\n\n"
        "**Ape**: 角色偏冷静理性。\n\n"
        "---\n\n"
        "## 10:00:00 UTC\n\n"
        "<!-- archive_session_id: session_beta -->\n"
        "<!-- session_title: 第二场 -->\n"
        "<!-- round_id: round_2 -->\n"
        "<!-- round_title: 偏好总结 -->\n\n"
        "**User**: 记住我偏好简洁回答\n\n"
        "**Ape**: 已记录你偏好简洁回答。\n\n"
        "---\n",
        encoding="utf-8",
    )

    result = await tools._tool_recall_conversation(
        {"session_id": "archive_2026-05-19_session_beta", "limit": 2},
        None,
        0,
        "db.sqlite3",
        None,
    )
    payload = json.loads(result)

    assert payload["matches"][0]["archive_session_id"] == "session_beta"
    assert payload["matches"][0]["session_title"] == "第二场"
    assert payload["matches"][0]["assistant"] == "已记录你偏好简洁回答。"
    assert "memories" not in payload


async def test_recall_conversation_tool_searches_active_workbench_workspace(tmp_path):
    from cyrene.runtime.memory import conversations
    from cyrene.tool_impl.memory import recall_conversation as tools
    from cyrene.agent import state as agent_state

    workspace = tmp_path / "project"
    other_workspace = tmp_path / "other"
    conversations.archive_session_exchange(
        "wbchat_alpha",
        "我们讨论 photo identification skill 的安装",
        "已安装全局 skill。",
        workspace_dir=workspace,
        session_title="技能安装",
    )
    conversations.archive_session_exchange(
        "wbchat_beta",
        "photo identification skill 后续清理",
        "需要检查实体和项目记忆。",
        workspace_dir=workspace,
        session_title="清理讨论",
    )
    conversations.archive_session_exchange(
        "wbchat_other",
        "photo identification skill 在另一个 workspace",
        "不应被当前 workspace 搜到。",
        workspace_dir=other_workspace,
        session_title="其他项目",
    )

    token = agent_state._active_workspace_dir.set(str(workspace))
    try:
        result = await tools._tool_recall_conversation(
            {"query": "photo identification", "limit": 10},
            None,
            0,
            "db.sqlite3",
            None,
        )
    finally:
        agent_state._active_workspace_dir.reset(token)

    payload = json.loads(result)
    assert payload["scope"] == "workbench_workspace"
    assert {item["session_id"] for item in payload["matches"]} == {
        "wbchat_alpha",
        "wbchat_beta",
    }
    assert all(item["source"] == "workbench_workspace" for item in payload["matches"])
    assert all(str(workspace) in item["source_file"] for item in payload["matches"])


async def test_run_chat_agent_avoids_duplicate_short_term_memory_in_system_prompt(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.agent import agent as _agent_core

    seen: dict[str, Any] = {}

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    _patch_runtime_context(monkeypatch, get_context=lambda max_chars=5000: "[Previous context:]\n- remembers tea")

    def fake_get_memory_context(include_short_term: bool = True):
        seen["include_short_term"] = include_short_term
        return "## Memory Context\n- stable trait"

    async def fake_run_main_agent(user_message, history, bot, chat_id, db_path, system_prompt="", client_request_id="", persist_user_message=True, lang="", **kwargs):
        seen["history"] = history
        seen["system_prompt"] = system_prompt
        return "ok"

    _patch_runtime_context(monkeypatch, get_memory_context=fake_get_memory_context)
    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)

    result = await agent._run_chat_agent("hello", None, 0, "db.sqlite3")

    assert result == "ok"
    assert seen["include_short_term"] is False
    assert seen["history"][0]["content"].startswith("[Restored context]")
    assert "stable trait" in seen["system_prompt"]


async def test_workbench_renderer_trigger_is_a_small_stable_system_extension(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import agent as _agent_core
    from cyrene.agent import session as _agent_session
    from cyrene.agent import state as _agent_state

    seen: dict[str, Any] = {}
    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    _patch_runtime_context(
        monkeypatch,
        get_context=lambda max_chars=5000: "",
        get_memory_context=lambda include_short_term=True: "",
    )

    async def fake_run_main_agent(
        user_message, history, bot, chat_id, db_path, system_prompt="", **kwargs
    ):
        seen["system_prompt"] = system_prompt
        return "ok"

    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)
    token = _agent_state.response_capabilities.set(
        frozenset({"interactive_blocks"})
    )
    try:
        assert await agent._run_chat_agent(
            "hello", None, 0, "db.sqlite3"
        ) == "ok"
    finally:
        _agent_state.response_capabilities.reset(token)

    prompt = seen["system_prompt"]
    assert "call `LoadRendererContract`" in prompt
    assert ":::chart line" not in prompt
    assert "action_id:" not in prompt


async def test_run_chat_agent_keeps_global_short_term_out_of_workbench_sessions(monkeypatch, tmp_path):
    """Per-session workbench runs must not inherit the default session's
    short-term context (regression: fresh task sessions answered stale topics)."""
    from cyrene import agent
    from cyrene.agent import state as _agent_state
    from cyrene.agent import session as _agent_session
    from cyrene.agent import agent as _agent_core

    seen: dict[str, Any] = {}

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    _patch_runtime_context(monkeypatch, get_context=lambda max_chars=5000: "[Previous context:]\n- stale paper topic")

    def fake_get_memory_context(include_short_term: bool = True):
        seen["include_short_term"] = include_short_term
        return "## Memory Context\n- stable trait"

    async def fake_run_main_agent(user_message, history, bot, chat_id, db_path, system_prompt="", client_request_id="", persist_user_message=True, lang="", **kwargs):
        seen["history"] = history
        seen["system_prompt"] = system_prompt
        return "ok"

    _patch_runtime_context(monkeypatch, get_memory_context=fake_get_memory_context)
    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)

    token = _agent_state._current_session_id.set("wbchat_test123")
    try:
        result = await agent._run_chat_agent("hello", None, 0, "db.sqlite3")
    finally:
        _agent_state._current_session_id.reset(token)

    assert result == "ok"
    assert seen["include_short_term"] is False
    assert not any(
        "[Restored context]" in str(message.get("content") or "")
        for message in seen["history"]
        if isinstance(message, dict)
    )
    assert "stale paper topic" not in seen["system_prompt"]
    assert "stable trait" in seen["system_prompt"]


async def test_run_chat_agent_does_not_schedule_hidden_session_naming(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.learning import engine as behavior_learning
    from cyrene.agent import agent as _agent_core
    from cyrene.agent import session as _agent_session

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    _patch_runtime_context(monkeypatch, get_context=lambda max_chars=5000: "")

    async def fake_run_main_agent(user_message, history, bot, chat_id, db_path, system_prompt="", **kwargs):
        return "ok"

    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)
    hidden_namer = AsyncMock()
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", hidden_namer)
    monkeypatch.setattr(behavior_learning, "begin_turn", AsyncMock(return_value=None))

    result = await asyncio.wait_for(agent._run_chat_agent("hello", None, 0, "db.sqlite3"), timeout=0.1)

    assert result == "ok"
    hidden_namer.assert_not_awaited()


async def test_call_llm_falls_back_to_next_model_candidate(monkeypatch):
    from cyrene import call_llm as cll

    attempts: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, Any], request: httpx.Request):
            self.status_code = status_code
            self._payload = payload
            self.request = request

        def json(self):
            return self._payload

        def raise_for_status(self):
            raise httpx.HTTPStatusError("upstream failure", request=self.request, response=httpx.Response(self.status_code, request=self.request))

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, json=None, headers=None):
            attempts.append((str(json.get("model") or ""), endpoint))
            request = httpx.Request("POST", endpoint)
            if json.get("model") == "primary-model":
                return FakeResponse(503, {}, request)
            return FakeResponse(
                200,
                {
                    "choices": [{"message": {"role": "assistant", "content": "fallback ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
                request,
            )

    monkeypatch.setattr(
        cll,
        "get_models",
        lambda: [
            {"id": "candidate-1", "model": "primary-model", "base_url": "https://primary.example/v1", "api_key": "primary-key"},
            {"id": "candidate-2", "model": "fallback-model", "base_url": "https://fallback.example/v1", "api_key": "fallback-key"},
        ],
    )
    monkeypatch.setattr(cll.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setenv("OPENAI_MODEL", "primary-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://primary.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "primary-key")

    message = await cll.call_llm([{"role": "user", "content": "hello"}], max_tokens=32)

    assert message["content"] == "fallback ok"
    assert attempts == [
        ("primary-model", "https://primary.example/v1/chat/completions"),
        ("primary-model", "https://primary.example/v1/chat/completions"),
        ("primary-model", "https://primary.example/v1/chat/completions"),
        ("fallback-model", "https://fallback.example/v1/chat/completions"),
    ]


async def test_call_llm_stream_falls_back_to_next_model_candidate(monkeypatch):
    from cyrene import call_llm as cll

    attempts: list[tuple[str, str]] = []
    emitted: list[dict[str, Any]] = []

    class FakeStreamResponse:
        def __init__(self, status_code: int, lines: list[str], request: httpx.Request):
            self.status_code = status_code
            self._lines = lines
            self.request = request

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        def raise_for_status(self):
            raise httpx.HTTPStatusError("upstream failure", request=self.request, response=httpx.Response(self.status_code, request=self.request))

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, endpoint, json=None, headers=None):
            attempts.append((str(json.get("model") or ""), endpoint))
            request = httpx.Request(method, endpoint)
            if json.get("model") == "primary-model":
                return FakeStreamResponse(503, [], request)
            return FakeStreamResponse(
                200,
                [
                    'data: {"choices":[{"delta":{"content":"hello "}}]}',
                    'data: {"choices":[{"delta":{"content":"world"}}],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}',
                    "data: [DONE]",
                ],
                request,
            )

    monkeypatch.setattr(
        cll,
        "get_models",
        lambda: [
            {"id": "candidate-1", "model": "primary-model", "base_url": "https://primary.example/v1", "api_key": "primary-key"},
            {"id": "candidate-2", "model": "fallback-model", "base_url": "https://fallback.example/v1", "api_key": "fallback-key"},
        ],
    )
    monkeypatch.setattr(cll.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setenv("OPENAI_MODEL", "primary-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://primary.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "primary-key")

    async def _capture(event):
        emitted.append(event)

    message = await cll.call_llm(
        [{"role": "user", "content": "hello"}],
        max_tokens=32,
        stream=True,
        stream_callback=_capture,
    )

    assert message["content"] == "hello world"
    assert message["usage"]["total_tokens"] == 3
    assert attempts == [
        ("primary-model", "https://primary.example/v1/chat/completions"),
        ("primary-model", "https://primary.example/v1/chat/completions"),
        ("primary-model", "https://primary.example/v1/chat/completions"),
        ("fallback-model", "https://fallback.example/v1/chat/completions"),
    ]
    assert emitted[0]["type"] == "reply_start"
    assert emitted[-1]["type"] == "reply_done"


def test_normalize_dsml_tool_calls_converts_textual_fallback():
    from cyrene import call_llm as cll

    message = {
        "role": "assistant",
        "content": (
            '<｜｜DSML｜｜tool_calls>\n'
            '<｜｜DSML｜｜invoke name="WebSearch">\n'
            '<｜｜DSML｜｜parameter name="query" string="true">AoA prediction</｜｜DSML｜｜parameter>\n'
            '</｜｜DSML｜｜invoke>\n'
            '<｜｜DSML｜｜invoke name="quit"/>\n'
            '</｜｜DSML｜｜tool_calls>'
        ),
    }
    tools = [
        {"type": "function", "function": {"name": "WebSearch"}},
        {"type": "function", "function": {"name": "quit"}},
    ]

    normalized = cll._normalize_dsml_tool_calls(message, tools)

    assert normalized["content"] == ""
    assert [call["function"]["name"] for call in normalized["tool_calls"]] == ["WebSearch", "quit"]
    assert json.loads(normalized["tool_calls"][0]["function"]["arguments"]) == {"query": "AoA prediction"}
    assert json.loads(normalized["tool_calls"][1]["function"]["arguments"]) == {}


def test_normalize_dsml_tool_calls_rejects_unknown_tools():
    from cyrene import call_llm as cll

    message = {
        "role": "assistant",
        "content": (
            '<｜｜DSML｜｜tool_calls>'
            '<｜｜DSML｜｜invoke name="UnknownTool"/>'
            '</｜｜DSML｜｜tool_calls>'
        ),
    }

    assert cll._normalize_dsml_tool_calls(message, [{"type": "function", "function": {"name": "WebSearch"}}]) == message


async def test_final_reply_retries_visible_dsml_tool_markup(monkeypatch):
    from cyrene.agent import guidance, replies

    calls: list[list[dict]] = []

    async def fake_call_llm(messages, tools=None, max_tokens=None):
        calls.append(messages)
        if len(calls) == 1:
            return {
                "role": "assistant",
                "content": (
                    '<｜｜DSML｜｜tool_calls>\n'
                    '<｜｜DSML｜｜invoke name="WebSearch">\n'
                    '<｜｜DSML｜｜parameter name="query" string="true">Hong Kong to Prague direct flight Cathay Pacific 2026</｜｜DSML｜｜parameter>\n'
                    '</｜｜DSML｜｜invoke>\n'
                    '</｜｜DSML｜｜tool_calls>'
                ),
            }
        return {"role": "assistant", "content": "目前没有确认的国泰香港到布拉格直飞结果。"}

    monkeypatch.setattr(replies, "_call_llm", fake_call_llm)

    text = await guidance._final_reply_from_history([
        {"role": "user", "content": "查香港到布拉格机票"},
        {"role": "tool", "tool_call_id": "w1", "content": "Search returned no direct-flight result."},
    ])

    assert text == "目前没有确认的国泰香港到布拉格直飞结果。"
    assert len(calls) == 2
    assert "textual tool-call markup" in calls[1][-1]["content"]


def test_retry_safe_guide_round_id_drops_completed_round_target():
    from cyrene.workbench import runtime as routes

    assert routes._retry_safe_guide_round_id("round_old", retry=True) == ""
    assert routes._retry_safe_guide_round_id(" round_live ", retry=False) == "round_live"


async def test_call_llm_secondary_concurrency_counter(monkeypatch):
    from cyrene import call_llm as cll

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "secondary ok"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, json=None, headers=None):
            return FakeResponse()

    monkeypatch.setattr(cll.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(cll, "_secondary_in_flight", 0)

    message = await cll.call_llm(
        [{"role": "user", "content": "hello"}],
        candidates=[{
            "id": "secondary",
            "model": "secondary-model",
            "api_key": "",
            "endpoints": ["https://secondary.example/v1/chat/completions"],
            "max_concurrency": 1,
        }],
        publish_events=False,
        record_usage=False,
    )

    assert message["content"] == "secondary ok"
    assert cll._secondary_in_flight == 0


async def test_run_vision_chat_uses_vision_candidates_after_primary_failure(monkeypatch):
    from cyrene.runtime import attachments as att
    from cyrene import call_llm as cll

    attempts: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, Any], request: httpx.Request):
            self.status_code = status_code
            self._payload = payload
            self.request = request

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code < 400:
                return
            raise httpx.HTTPStatusError("vision unsupported", request=self.request, response=httpx.Response(self.status_code, request=self.request))

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, json=None, headers=None):
            attempts.append((str(json.get("model") or ""), endpoint))
            request = httpx.Request("POST", endpoint)
            if json.get("model") == "primary-model":
                return FakeResponse(400, {}, request)
            return FakeResponse(
                200,
                {"choices": [{"message": {"content": "vision fallback ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
                request,
            )

    monkeypatch.setattr(
        cll,
        "get_models",
        lambda: [{"id": "candidate-1", "model": "primary-model", "base_url": "https://primary.example/v1", "api_key": "primary-key"}],
    )
    monkeypatch.setattr(
        cll,
        "get_vision_models",
        lambda: [{"id": "vision-1", "model": "vision-model", "base_url": "https://vision.example/v1", "api_key": "vision-key"}],
    )
    monkeypatch.setattr(cll.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setenv("OPENAI_MODEL", "primary-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://primary.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "primary-key")

    payload = await att.run_vision_chat(
        [{"type": "text", "text": "describe"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}],
        content_prompt="describe",
    )

    assert payload["vision_text"] == "vision fallback ok"
    assert payload["vision_model"] == "vision-model"
    assert attempts == [
        ("vision-model", "https://vision.example/v1/chat/completions"),
    ]


async def test_chat_with_uploaded_images_falls_back_to_vision_model(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"fake-image")

    request = httpx.Request("POST", "https://primary.example/v1/chat/completions")
    response = httpx.Response(400, request=request)

    async def fake_call_llm(messages, tools=None, max_tokens=None):
        raise httpx.HTTPStatusError("image unsupported", request=request, response=response)

    async def fake_run_vision_chat(content, content_prompt=""):
        return {"vision_text": "vision route ok"}

    monkeypatch.setattr(routes, "_call_llm", fake_call_llm)
    monkeypatch.setattr(routes, "run_vision_chat", fake_run_vision_chat)
    monkeypatch.setattr(routes, "format_httpx_error", lambda exc: "image unsupported")

    result = await routes._chat_with_uploaded_images(
        "",
        [{"path": str(image_path), "content_type": "image/png"}],
    )

    assert result == "vision route ok"


async def test_save_session_messages_emits_session_update(tmp_path, monkeypatch):
    from cyrene import agent
    from cyrene.observability import debug

    seen = []

    async def fake_publish_event(event, **kwargs):
        seen.append({**event, **kwargs})

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    await agent._save_session_messages([
        {"role": "user", "content": "hi", "round_id": "round_1"},
        {"role": "assistant", "content": "hello", "round_id": "round_1"},
    ])

    assert seen
    assert seen[-1]["type"] == "session_update"
    assert seen[-1]["message_count"] == 2
    assert seen[-1]["last_role"] == "assistant"
    assert seen[-1]["round_id"] == "round_1"


async def test_proactive_round_hides_internal_prompt_and_initial_detail(tmp_path, monkeypatch):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.observability import debug

    events = []

    async def fake_publish_event(event):
        events.append(dict(event))

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        return {
            "content": "最近你之前提到的那件事怎么样了？如果你想，我可以继续帮你拆一下。",
            "tool_calls": [],
        }

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_memory_context", lambda include_short_term=True: "")
    _patch_call_llm(monkeypatch, fake_call_llm)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    result = await agent._run_chat_agent(
        "internal proactive instruction",
        None,
        0,
        "db.sqlite3",
        persist_user_message=False,
        public_prompt="",
        refresh_labels=False,
        hide_initial_detail=True,
        assistant_message_meta={"proactive": True, "system_initiated": True},
    )

    assert "如果你想" in result

    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    messages = saved["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == result
    assert messages[0]["proactive"] is True
    assert messages[0]["system_initiated"] is True

    phase_events = [event for event in events if event.get("type") == "phase_transition"]
    assert phase_events
    assert phase_events[0]["from"] == "phase1_decision"
    assert phase_events[0]["to"] == "chat_only"
    assert "detail" not in phase_events[0]


def test_build_live_flow_round_skips_input_for_system_initiated_messages():
    from cyrene.workbench import runtime as routes

    raw_msgs = [
        {
            "role": "assistant",
            "content": "最近你之前提到的项目推进得怎么样了？",
            "round_id": "round_1",
            "message_id": "msg_1",
            "system_initiated": True,
            "proactive": True,
        }
    ]
    messages = routes._convert_messages(raw_msgs)
    nodes, edges, _bottom = routes._build_live_flow_round(
        prefix="r1_",
        raw_msgs=raw_msgs,
        messages=messages,
        subagents=[],
        registry={},
        recent_events=[{"type": "phase_transition", "to": "chat_only"}],
        y_offset=0,
        round_id="round_1",
    )

    assert not any(node["kind"] == "input" for node in nodes)
    assert any(node["kind"] == "main" for node in nodes)
    assert any(node["kind"] == "output" for node in nodes)
    assert not any(edge.get("from") == "r1_n_user" for edge in edges)


async def test_heartbeat_proactive_check_uses_main_agent_loop(monkeypatch):
    from cyrene.runtime import scheduler

    seen = {}

    monkeypatch.setattr(scheduler, "OWNER_ID", 7)
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_save_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "_silence_hours", lambda: 96.0)
    monkeypatch.setattr(scheduler, "_latest_workbench_user_activity", lambda: None)
    monkeypatch.setattr(scheduler, "notify", AsyncMock())
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=0, cooldown_until=0.0, last_proactive_time=0.0, probability=0.0,
    )

    async def fake_context(_db_path=""):
        return "## Recent memories about the user\n- user is preparing a launch"

    async def fake_run_heartbeat_agent(prompt, bot, chat_id, db_path, lang=""):
        seen["prompt"] = prompt
        seen["chat_id"] = chat_id
        seen["db_path"] = db_path
        return "user-facing proactive message"

    monkeypatch.setattr(scheduler, "_assemble_proactive_context", fake_context)
    monkeypatch.setattr(scheduler, "run_heartbeat_agent", fake_run_heartbeat_agent)

    await scheduler._heartbeat_proactive_check(bot=None, db_path="db.sqlite3")

    assert seen["chat_id"] == 7
    assert seen["db_path"] == "db.sqlite3"
    assert "scheduler-initiated proactive check-in" in seen["prompt"]
    assert "Recent memories about the user" in seen["prompt"]
    assert "autonomous work cycle, not a social check-in" in seen["prompt"]
    assert "use tools and complete the work now" in seen["prompt"]
    assert "Never claim or imply that the user just woke up" in seen["prompt"]
    assert "Trigger: system scheduler; no new user activity" in seen["prompt"]
    assert "Do not send a greeting, check-in, small talk" in seen["prompt"]
    # A delivered message advances the unanswered streak by exactly one.
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 1
    assert scheduler._LOTTERY_STATE["last_proactive_time"] > 0


async def test_heartbeat_proactive_check_stays_silent_when_agent_skips(monkeypatch):
    from cyrene.runtime import scheduler

    seen = {"notified": False}

    monkeypatch.setattr(scheduler, "OWNER_ID", 7)
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_save_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "_silence_hours", lambda: 96.0)
    monkeypatch.setattr(scheduler, "_latest_workbench_user_activity", lambda: None)
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=0, cooldown_until=0.0, last_proactive_time=0.0, probability=0.0,
    )

    async def fake_context(_db_path):
        return "## Recent conversation\n- user already closed the loop"

    async def fake_run_heartbeat_agent(prompt, bot, chat_id, db_path, lang=""):
        seen["prompt"] = prompt
        return ""

    async def fake_notify(*args, **kwargs):
        seen["notified"] = True

    monkeypatch.setattr(scheduler, "_assemble_proactive_context", fake_context)
    monkeypatch.setattr(scheduler, "run_heartbeat_agent", fake_run_heartbeat_agent)
    monkeypatch.setattr(scheduler, "notify", fake_notify)

    await scheduler._heartbeat_proactive_check(bot=None, db_path="db.sqlite3")

    # Agent returned no text -> nothing is delivered and the unanswered streak
    # does not advance.
    assert seen["notified"] is False
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 0
    assert "scheduler-initiated proactive check-in" in seen["prompt"]
    # A work cycle with nothing material to do must bow out silently instead
    # of manufacturing a social check-in.
    assert "quit" in seen["prompt"].lower()
    assert "If there is no useful safe action or no material result" in seen["prompt"]


async def test_proactive_single_ignored_message_does_not_snowball_into_cooldown(monkeypatch):
    """Regression: the unanswered streak must track delivered messages, not
    heartbeat ticks. One ignored message followed by silent ticks must NOT
    accumulate into the cooldown threshold."""
    import time

    from cyrene.runtime import scheduler

    monkeypatch.setattr(scheduler, "OWNER_ID", 7)
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_save_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "_silence_hours", lambda: 96.0)
    monkeypatch.setattr(scheduler, "_latest_workbench_user_activity", lambda: None)
    monkeypatch.setattr(scheduler, "notify", AsyncMock())
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=0, cooldown_until=0.0, last_proactive_time=0.0, probability=0.0,
    )

    async def fake_context(_db_path=""):
        return ""

    # Deliver exactly one message on the first tick; stay silent ever after.
    calls = {"n": 0}

    async def fake_run_heartbeat_agent(prompt, bot, chat_id, db_path, lang=""):
        calls["n"] += 1
        return "hey, how did the launch go?" if calls["n"] == 1 else ""

    monkeypatch.setattr(scheduler, "_assemble_proactive_context", fake_context)
    monkeypatch.setattr(scheduler, "run_heartbeat_agent", fake_run_heartbeat_agent)

    # The user never replies (reset_lottery is never called) across many ticks.
    for _ in range(6):
        await scheduler._heartbeat_proactive_check(bot=None, db_path="db.sqlite3")

    # Only the single delivery counts; no multi-day cooldown is armed.
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 1
    assert scheduler._LOTTERY_STATE["cooldown_until"] == 0.0
    assert scheduler._LOTTERY_STATE["cooldown_until"] <= time.time()


async def test_proactive_cooldown_arms_when_streak_reaches_threshold(monkeypatch):
    """Once ``_PROACTIVE_COOLDOWN_THRESHOLD`` delivered messages go unanswered,
    the next check arms the cooldown instead of sending again."""
    import time

    from cyrene.runtime import scheduler

    sent = {"count": 0}

    monkeypatch.setattr(scheduler, "OWNER_ID", 7)
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_save_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "_silence_hours", lambda: 96.0)
    monkeypatch.setattr(scheduler, "_latest_workbench_user_activity", lambda: None)
    monkeypatch.setattr(scheduler, "notify", AsyncMock())
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=scheduler._PROACTIVE_COOLDOWN_THRESHOLD,
        cooldown_until=0.0, last_proactive_time=0.0, probability=0.0,
    )

    async def fake_context(_db_path=""):
        return ""

    async def fake_run_heartbeat_agent(prompt, bot, chat_id, db_path, lang=""):
        sent["count"] += 1
        return "hi"

    monkeypatch.setattr(scheduler, "_assemble_proactive_context", fake_context)
    monkeypatch.setattr(scheduler, "run_heartbeat_agent", fake_run_heartbeat_agent)

    await scheduler._heartbeat_proactive_check(bot=None, db_path="db.sqlite3")

    assert sent["count"] == 0
    assert scheduler._LOTTERY_STATE["cooldown_until"] > time.time()
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 0

    # A user message clears the cooldown so the agent can speak again.
    scheduler.reset_lottery()
    assert scheduler._LOTTERY_STATE["cooldown_until"] == 0.0
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 0


async def test_system_initiated_silent_quit_yields_no_message(tmp_path, monkeypatch):
    """A proactive round where the agent quits with nothing to say must return
    an empty string so the scheduler delivers nothing — never a filler 'Done.'."""
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.observability import debug

    async def fake_publish_event(event):
        return None

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        # Decision phase (and any later synthesis) silently quits, no text.
        return {
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "quit", "arguments": "{}"}},
            ],
        }

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_memory_context", lambda include_short_term=True: "")
    _patch_call_llm(monkeypatch, fake_call_llm)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    result = await agent._run_chat_agent(
        "internal proactive instruction",
        None,
        0,
        "db.sqlite3",
        persist_user_message=False,
        public_prompt="",
        refresh_labels=False,
        hide_initial_detail=True,
        assistant_message_meta={"proactive": True, "system_initiated": True},
    )

    assert result == ""


async def test_system_initiated_round_cannot_use_ask_user(tmp_path, monkeypatch):
    """Proactive rounds keep the fixed wire schema but must not execute ask_user."""
    from cyrene import agent
    from cyrene.agent import agent as _agent_core
    from cyrene.agent import session as _agent_session
    from cyrene.observability import debug

    calls = []
    executed = []

    async def fake_publish_event(event):
        return None

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        tool_names = {
            str(tool.get("function", {}).get("name") or "")
            for tool in (tools or [])
        }
        calls.append(tool_names)
        assert "ask_user" in tool_names
        if len(calls) == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "use_1",
                    "function": {
                        "name": "use_tools",
                        "arguments": json.dumps({"task": "hidden proactive instruction"}),
                    },
                }],
            }
        if len(calls) == 2:
            # Simulate a model attempting a visible but runtime-forbidden tool.
            return {
                "content": "",
                "tool_calls": [{
                    "id": "ask_1",
                    "function": {
                        "name": "ask_user",
                        "arguments": json.dumps({"text": "How are you?"}),
                    },
                }],
            }
        return {
            "content": "Just checking in.",
            "tool_calls": [],
        }

    async def fake_execute_tool(name, arguments, bot, chat_id, db_path, notify_state):
        executed.append(name)
        return "ok"

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_memory_context", lambda include_short_term=True: "")
    monkeypatch.setattr(_agent_core, "_call_llm", fake_call_llm)
    monkeypatch.setattr(_agent_core, "_execute_tool", fake_execute_tool)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    result = await agent._run_chat_agent(
        "hidden proactive instruction",
        None,
        0,
        "db.sqlite3",
        persist_user_message=False,
        public_prompt="",
        refresh_labels=False,
        hide_initial_detail=True,
        assistant_message_meta={"proactive": True, "system_initiated": True},
    )

    assert result == "Just checking in."
    assert "ask_user" not in executed
    assert len(calls) == 3


async def test_tool_executor_rejects_ask_user_for_system_initiated_round(monkeypatch):
    from cyrene.tooling import executor as tool_executor
    from cyrene.agent import state as _agent_state

    handler = AsyncMock(return_value="should not run")
    monkeypatch.setitem(tool_executor.TOOL_HANDLERS, "ask_user", handler)
    token = _agent_state._ui_round_assistant_meta.set({
        "proactive": True,
        "system_initiated": True,
    })
    try:
        result = await tool_executor._execute_tool(
            "ask_user",
            {"text": "How are you?"},
            None,
            0,
            "db.sqlite3",
            None,
        )
    finally:
        _agent_state._ui_round_assistant_meta.reset(token)

    assert result.startswith("Tool unavailable:")
    handler.assert_not_awaited()


async def test_system_initiated_elevation_never_creates_pending_question(
    tmp_path, monkeypatch
):
    from cyrene.agent import context as agent_context
    from cyrene.agent import session as agent_session
    from cyrene.tooling.runtime_support import _request_scope_elevation

    state_file = tmp_path / "state.json"
    _patch_state_file(monkeypatch, state_file)
    _patch_data_dir(monkeypatch, tmp_path)

    with agent_context.bind_run_context(
        round_id="round_proactive_permission",
        session_id="wbchat_proactive_permission",
        permission_mode="default",
        assistant_meta={"proactive": True, "system_initiated": True},
    ):
        result = await _request_scope_elevation(
            tool_name="Bash",
            path_hint="",
            operation="执行本地进程或 Shell 命令",
            reason="python3 -c 'print(1)'",
            permission_kind="process_execution",
        )

    assert result.startswith("Tool unavailable:")
    assert agent_session.get_pending_question() == {}


async def test_permission_elevation_uses_scoped_choices_without_custom_answer(
    tmp_path, monkeypatch
):
    from cyrene.agent import context as agent_context
    from cyrene.agent import session as agent_session
    from cyrene.tooling.runtime_support import _request_scope_elevation

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)

    with agent_context.bind_run_context(
        round_id="round_permission_choices",
        session_id="wbchat_permission_choices",
        permission_mode="default",
    ):
        result = await _request_scope_elevation(
            tool_name="Bash",
            path_hint="",
            operation="执行本地进程或 Shell 命令",
            permission_kind="process_execution",
            options=["旧的工具自定义选项", "拒绝"],
        )
        pending = agent_session.get_pending_question()
    assert json.loads(result)["status"] == "awaiting_user"
    assert [option["label"] for option in pending["options"]] == [
        "在本次会话同意",
        "同意一次",
        "拒绝",
    ]
    assert pending["allow_custom"] is False


async def test_heartbeat_agent_suppresses_awaiting_user_sentinel(monkeypatch):
    from cyrene.agent import coordinator

    async def fake_run_chat_agent(*args, **kwargs):
        return coordinator._state._AWAITING_USER_SENTINEL

    on_reply = AsyncMock()
    monkeypatch.setattr(coordinator, "_run_chat_agent", fake_run_chat_agent)

    result = await coordinator.run_heartbeat_agent(
        "internal proactive instruction",
        None,
        0,
        "db.sqlite3",
        session_id="wbchat_sentinel_regression",
        on_reply=on_reply,
    )

    assert result == ""
    on_reply.assert_not_awaited()


async def test_heartbeat_agent_strips_decorated_awaiting_user_sentinel(monkeypatch):
    from cyrene.agent import coordinator

    async def fake_run_chat_agent(*args, **kwargs):
        return "**[[cyrene.awaiting_user]]**"

    on_reply = AsyncMock()
    monkeypatch.setattr(coordinator, "_run_chat_agent", fake_run_chat_agent)

    result = await coordinator.run_heartbeat_agent(
        "internal proactive instruction",
        None,
        0,
        "db.sqlite3",
        session_id="wbchat_decorated_sentinel_regression",
        on_reply=on_reply,
    )

    assert result == ""
    on_reply.assert_not_awaited()


def test_assistant_text_ignores_reasoning_when_tool_calls_present():
    """Regression: a turn that emits tool_calls (e.g. ``quit``) with empty
    content must NOT surface ``reasoning_content`` as user-facing text — that
    leaked the model's chain-of-thought into proactive messages. Pure-text
    turns (no tool_calls) still fall back to reasoning for Qwen-style models."""
    from cyrene.model_runtime.messages import _assistant_text

    quit_turn = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "The user hasn't replied yet... Let me just quit.",
        "tool_calls": [{"id": "c1", "function": {"name": "quit", "arguments": "{}"}}],
    }
    assert _assistant_text(quit_turn) == ""

    # No tool_calls: the reasoning fallback is still honored (Qwen-style models).
    plain_turn = {"role": "assistant", "content": "", "reasoning_content": "final answer"}
    assert _assistant_text(plain_turn) == "final answer"

    # Real content always wins, even alongside tool_calls.
    spoke_turn = {
        "role": "assistant",
        "content": "scheduled task completed",
        "reasoning_content": "scratch",
        "tool_calls": [{"id": "c2", "function": {"name": "quit", "arguments": "{}"}}],
    }
    assert _assistant_text(spoke_turn) == "scheduled task completed"


async def test_system_initiated_quit_does_not_leak_reasoning(tmp_path, monkeypatch):
    """A proactive round where the agent quits with only ``reasoning_content``
    (its internal deliberation) and no ``content`` must stay silent — the
    reasoning must never be delivered to the user as the proactive message."""
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.observability import debug

    async def fake_publish_event(event):
        return None

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        return {
            "content": "",
            "reasoning_content": (
                "The user hasn't replied to my last proactive check-in yet. "
                "Reaching out again would feel pushy. Let me just quit."
            ),
            "tool_calls": [
                {"id": "c1", "function": {"name": "quit", "arguments": "{}"}},
            ],
        }

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_memory_context", lambda include_short_term=True: "")
    _patch_call_llm(monkeypatch, fake_call_llm)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    result = await agent._run_chat_agent(
        "internal proactive instruction",
        None,
        0,
        "db.sqlite3",
        persist_user_message=False,
        public_prompt="",
        refresh_labels=False,
        hide_initial_detail=True,
        assistant_message_meta={"proactive": True, "system_initiated": True},
    )

    assert result == ""


def test_last_user_time_prefers_archive_over_state_mtime(tmp_path, monkeypatch):
    """Silence detection must read the real user-turn timestamp from the
    conversation archive, not state.json's mtime. The agent rewrites state.json
    on its own (proactive replies, steward, ...), so a fresh mtime would
    otherwise mask genuine user silence and suppress the >72h reach-out."""
    from datetime import datetime, timezone

    from cyrene.runtime import scheduler

    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    # The user actually last spoke on 2026-06-02 at 09:00 UTC (recorded once,
    # per turn, in the archive).
    (conv_dir / "2026-06-02.md").write_text(
        "# 2026-06-02\n\n## 09:00:00 UTC\n\n**User**: morning!\n\n**Cyrene**: hi\n",
        encoding="utf-8",
    )
    # state.json was just rewritten by the agent — its last message is a
    # proactive reply and its mtime is "now". That must NOT count as user activity.
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"messages": [
            {"role": "user", "content": "morning!"},
            {"role": "assistant", "content": "checking in", "proactive": True},
        ]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(scheduler, "CONVERSATIONS_DIR", conv_dir)
    monkeypatch.setattr(scheduler, "STATE_FILE", state_file)
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)

    result = scheduler._last_user_message_time()

    assert result == datetime(2026, 6, 2, 9, 0, 0, tzinfo=timezone.utc)


def test_last_user_time_mtime_fallback_requires_user_spoke_last(tmp_path, monkeypatch):
    """Before anything is archived, fall back to state.json mtime only when the
    most recent message is the user's; otherwise report unknown (None) so we
    never treat one of the agent's own writes as user activity."""
    import os
    from datetime import datetime, timezone

    from cyrene.runtime import scheduler

    conv_dir = tmp_path / "conversations"  # deliberately not created
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(scheduler, "CONVERSATIONS_DIR", conv_dir)
    monkeypatch.setattr(scheduler, "STATE_FILE", state_file)
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)

    # (a) User spoke last → mtime is a valid proxy.
    state_file.write_text(
        json.dumps({"messages": [
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "you there?"},
        ]}),
        encoding="utf-8",
    )
    pinned = datetime(2026, 6, 4, 8, 0, 0, tzinfo=timezone.utc).timestamp()
    os.utime(state_file, (pinned, pinned))
    result = scheduler._last_user_message_time()
    assert result is not None
    assert abs(result.timestamp() - pinned) < 1.0

    # (b) Agent spoke last (proactive) → mtime is the agent's write, not the
    #     user's, so it must be ignored.
    state_file.write_text(
        json.dumps({"messages": [
            {"role": "user", "content": "you there?"},
            {"role": "assistant", "content": "yes!", "proactive": True},
        ]}),
        encoding="utf-8",
    )
    assert scheduler._last_user_message_time() is None


async def test_execute_task_fallback_persists_webui_reminder(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.observability import debug
    from cyrene.runtime import scheduler

    seen = []

    async def fake_publish_event(event):
        seen.append(event)

    async def fake_run_task_agent(prompt, bot, chat_id, db_path, notify_state=None):
        return "task finished without explicit message"

    async def fake_log_task_run(*args, **kwargs):
        return None

    async def fake_update_task_after_run(*args, **kwargs):
        return None

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(scheduler, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)
    monkeypatch.setattr(scheduler, "run_task_agent", fake_run_task_agent)
    monkeypatch.setattr(scheduler, "notify", AsyncMock())
    monkeypatch.setattr(scheduler.db, "log_task_run", fake_log_task_run)
    monkeypatch.setattr(scheduler.db, "update_task_after_run", fake_update_task_after_run)

    agent.STATE_FILE.write_text(json.dumps({"messages": []}, ensure_ascii=False), encoding="utf-8")

    await scheduler._execute_task(
        {
            "id": "task_1",
            "chat_id": 7,
            "prompt": "提醒我喝水",
            "schedule_type": "once",
            "schedule_value": "2026-05-20T10:18:00+00:00",
        },
        bot=None,
        db_path="db.sqlite3",
    )

    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert saved[-1]["content"] == "Result: task finished without explicit message"
    assert saved[-1]["system_initiated"] is True
    assert saved[-1]["scheduled"] is True
    assert any(event.get("type") == "assistant_message" and event.get("scheduled") is True for event in seen)


def test_format_httpx_error_includes_request_response_and_cause():
    import httpx
    from cyrene import agent

    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(502, request=request, text='{"error":"upstream exploded"}')
    cause = ConnectionError("socket closed")
    exc = httpx.HTTPStatusError("Bad Gateway", request=request, response=response)
    exc.__cause__ = cause

    detail = agent.format_httpx_error(exc)

    assert "HTTPStatusError" in detail
    assert "request=POST https://example.test/v1/chat/completions" in detail
    assert "status=502" in detail
    assert 'body={"error":"upstream exploded"}' in detail
    assert "cause=ConnectionError: socket closed" in detail


async def test_send_agent_message_redirects_main_alias():
    from cyrene.tool_impl.subagent import send_agent_message as tools

    result = await tools._tool_send_agent_message(
        {"to": "danny", "content": "final answer"},
        None,
        0,
        "db.sqlite3",
        None,
    )

    assert "main-agent inbox is reserved for user guidance" in result
    assert "quit response" in result


async def test_send_agent_message_rejects_cross_round_target():
    from cyrene import agent
    from cyrene import subagent
    from cyrene.tool_impl.subagent import send_agent_message as tools

    await subagent.clear()
    await subagent.register("alice", "task A", round_id="round_old")
    round_token = agent._current_round_id.set("round_new")
    try:
        result = await tools._tool_send_agent_message(
            {"to": "alice", "content": "status?"},
            None,
            0,
            "db.sqlite3",
            None,
        )
    finally:
        agent._current_round_id.reset(round_token)

    assert "current round" in result
    assert "round_new" in result


async def test_send_message_tool_persists_intermediate_reply(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import state as _agent_state
    from cyrene.observability import debug
    from cyrene.tool_impl.delivery import send_message as tools

    seen = []

    async def fake_publish_event(event):
        seen.append(event)

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    agent.STATE_FILE.write_text(json.dumps({
        "messages": [
            {"role": "user", "content": "do the work", "round_id": "round_1", "client_request_id": "req_1"},
        ]
    }, ensure_ascii=False), encoding="utf-8")

    round_token = agent._current_round_id.set("round_1")
    request_token = agent._current_client_request_id.set("req_1")
    pending_token = agent._pending_intermediate_user_replies.set([])
    sender_token = agent._current_agent_id.set("main")
    destructive_token = _agent_state._destructive_confirmation_allow_all.set(True)
    streamed = []

    async def collect_stream_event(event):
        streamed.append(event)

    stream_token = agent._reply_stream_writer.set(collect_stream_event)
    try:
        result = await tools._tool_send_user_message(
            {"text": "先给你一个中途结论：方向是对的，我继续细化。"},
            None,
            0,
            "db.sqlite3",
            None,
        )
    finally:
        agent._reply_stream_writer.reset(stream_token)
        _agent_state._destructive_confirmation_allow_all.reset(destructive_token)
        agent._current_agent_id.reset(sender_token)
        agent._pending_intermediate_user_replies.reset(pending_token)
        agent._current_client_request_id.reset(request_token)
        agent._current_round_id.reset(round_token)

    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert result == "Mid-run message sent to the user."
    assert saved[-1]["role"] == "assistant"
    assert saved[-1]["content"].startswith("先给你一个中途结论")
    assert saved[-1]["round_id"] == "round_1"
    assert saved[-1]["client_request_id"] == "req_1"
    assert saved[-1]["intermediate_reply"] is True
    assert any(event.get("type") == "assistant_message" and event.get("intermediate") is True for event in seen)
    assert streamed == [{
        "type": "intermediate_message",
        "message": {
            "id": saved[-1]["message_id"],
            "role": "assistant",
            "content": "先给你一个中途结论：方向是对的，我继续细化。",
            "createdAt": saved[-1]["created_at"],
            "intermediate": True,
            "roundId": "round_1",
        },
    }]


async def test_send_message_tool_from_scheduler_persists_system_message(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.observability import debug
    from cyrene.tool_impl.delivery import send_message as tools

    seen = []

    async def fake_publish_event(event):
        seen.append(event)

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    agent.STATE_FILE.write_text(json.dumps({"messages": []}, ensure_ascii=False), encoding="utf-8")

    sender_token = agent._current_agent_id.set("scheduler")
    try:
        notify_state = {"sent": False}
        result = await tools._tool_send_user_message(
            {"text": "这是调度任务消息"},
            None,
            0,
            "db.sqlite3",
            notify_state,
        )
    finally:
        agent._current_agent_id.reset(sender_token)

    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert result == "Scheduled message sent to the user."
    assert notify_state["sent"] is True
    assert saved[-1]["role"] == "assistant"
    assert saved[-1]["content"] == "这是调度任务消息"
    assert saved[-1]["system_initiated"] is True
    assert saved[-1]["scheduled"] is True
    assert any(event.get("type") == "assistant_message" and event.get("scheduled") is True for event in seen)


async def test_schedule_task_once_normalizes_naive_local_time_to_utc(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from cyrene.tool_impl.task import schedule_task as tools
    from cyrene.runtime import schedule_spec

    seen = {}
    local_timezone = timezone(timedelta(hours=8))

    async def fake_create_task(db_path, chat_id, prompt, schedule_type, schedule_value, next_run, permission_mode="workspace_only", project_id="default", schedule_timezone="UTC"):
        seen["db_path"] = db_path
        seen["chat_id"] = chat_id
        seen["prompt"] = prompt
        seen["schedule_type"] = schedule_type
        seen["schedule_value"] = schedule_value
        seen["next_run"] = next_run
        seen["permission_mode"] = permission_mode
        seen["project_id"] = project_id
        seen["schedule_timezone"] = schedule_timezone
        return "task_local"

    class _FakeLocalNow(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls(2026, 5, 20, 19, 33, 35, tzinfo=local_timezone)
            return cls(2026, 5, 20, 11, 33, 35, tzinfo=tz)

    monkeypatch.setattr(tools.db, "create_task", fake_create_task)
    monkeypatch.setattr(tools, "datetime", _FakeLocalNow)
    monkeypatch.setattr(schedule_spec, "_local_tzinfo", lambda: local_timezone)

    result = await tools._tool_schedule_task(
        {
            "prompt": "send_message(\"2分钟到了\")",
            "schedule_type": "once",
            "schedule_value": "2026-05-20T19:35:35",
        },
        None,
        -1,
        "db.sqlite3",
        None,
    )

    assert result == "Task task_local scheduled. Next run: 2026-05-20T11:35:35+00:00 权限模式：workspace_only"
    assert seen["schedule_value"] == "2026-05-20T11:35:35+00:00"
    assert seen["next_run"] == "2026-05-20T11:35:35+00:00"
    assert seen["permission_mode"] == "workspace_only"
    assert seen["project_id"] == "default"
    assert seen["schedule_timezone"] == "UTC"


async def test_schedule_task_uses_workbench_project_scope(monkeypatch, tmp_path):
    import json

    from cyrene.tool_impl.task import schedule_task as tools
    from cyrene.agent import state as agent_state
    import cyrene.workbench.context as workbench_context

    seen = {}

    projects_store = tmp_path / "workbench_projects.json"
    projects_store.write_text(json.dumps({
        "projects": [
            {
                "id": "proj_demo",
                "dataKey": "proj_demo_scope",
                "sessions": [{"id": "task_session_1"}],
            }
        ]
    }), encoding="utf-8")

    chats_store = tmp_path / "workbench_chats.json"
    chats_store.write_text(json.dumps({
        "chats": [
            {"id": "wbchat_1", "projectId": "proj_demo"}
        ]
    }), encoding="utf-8")

    async def fake_create_task(db_path, chat_id, prompt, schedule_type, schedule_value, next_run, permission_mode="workspace_only", project_id="default", schedule_timezone="UTC"):
        seen["project_id"] = project_id
        seen["schedule_timezone"] = schedule_timezone
        return "task_scope"

    monkeypatch.setattr(workbench_context, "_WORKBENCH_STORE", projects_store)
    monkeypatch.setattr(workbench_context, "_WORKBENCH_CHATS_STORE", chats_store)
    monkeypatch.setattr(tools.db, "create_task", fake_create_task)

    token = agent_state._current_session_id.set("wbchat_1")
    try:
        result = await tools._tool_schedule_task(
            {
                "prompt": "send_message(\"scope\")",
                "schedule_type": "interval",
                "schedule_value": "3600",
            },
            None,
            -1,
            "db.sqlite3",
            None,
        )
    finally:
        agent_state._current_session_id.reset(token)

    assert result.startswith("Task task_scope scheduled.")
    assert seen["project_id"] == "proj_demo_scope"


async def test_ask_user_tool_persists_pending_question(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.observability import debug
    from cyrene.tool_impl.control import ask_user as tools

    seen = []

    async def fake_publish_event(event):
        seen.append(event)

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    agent.STATE_FILE.write_text(json.dumps({
        "messages": [
            {"role": "user", "content": "帮我订行程", "round_id": "round_1", "round_title": "订行程"},
        ]
    }, ensure_ascii=False), encoding="utf-8")

    round_token = agent._current_round_id.set("round_1")
    request_token = agent._current_client_request_id.set("req_ask_1")
    sender_token = agent._current_agent_id.set("main")
    try:
        result = await tools._tool_ask_user(
            {"text": "你想去北京还是上海？", "options": ["北京", "上海"]},
            None,
            0,
            "db.sqlite3",
            None,
        )
    finally:
        agent._current_agent_id.reset(sender_token)
        agent._current_client_request_id.reset(request_token)
        agent._current_round_id.reset(round_token)

    payload = json.loads(result)
    state = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))
    pending = state["pending_question"]
    saved = state["messages"]

    assert payload["status"] == "awaiting_user"
    assert pending["text"] == "你想去北京还是上海？"
    assert pending["round_id"] == "round_1"
    assert pending["client_request_id"] == "req_ask_1"
    assert [item["label"] for item in pending["options"]] == ["北京", "上海"]
    assert saved[-1]["role"] == "assistant"
    assert saved[-1]["content"] == "你想去北京还是上海？"
    assert saved[-1]["question_prompt"] is True
    assert saved[-1]["question_id"] == pending["id"]
    assert any(event.get("type") == "user_question" and event.get("question_id") == pending["id"] for event in seen)


async def test_permission_pending_question_does_not_persist_chat_message(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.observability import debug

    seen = []

    async def fake_publish_event(event):
        seen.append(event)

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    agent.STATE_FILE.write_text(json.dumps({
        "messages": [
            {"role": "user", "content": "写到外部路径", "round_id": "round_1", "message_id": "u1"},
        ]
    }, ensure_ascii=False), encoding="utf-8")

    question = await agent._upsert_pending_question({
        "text": "申请写入 workspace 外部路径",
        "round_id": "round_1",
        "client_request_id": "req_perm_1",
        "options": ["仅这次允许", "保持仅限 workspace"],
        "meta": {
            "kind": "write_permission_request",
            "tool_name": "Write",
            "path_hint": "/tmp/outside.txt",
            "operation": "写入/删除操作",
        },
    })

    state = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))

    assert [msg["content"] for msg in state["messages"]] == ["写到外部路径"]
    assert state["pending_question"]["id"] == question["id"]
    assert state["pending_question"]["hidden_from_chat"] is True
    assert state["pending_question"]["hide_answer_in_chat"] is True
    assert "message_id" not in state["pending_question"]
    assert any(event.get("type") == "user_question" and event.get("question_id") == question["id"] for event in seen)


async def test_ask_user_wait_state_does_not_persist_assistant_trace(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import session as _agent_session

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        names = {item.get("function", {}).get("name") for item in (tools or [])}
        if "use_tools" in names:
            return {
                "content": "我应该先问清楚。",
                "tool_calls": [
                    {
                        "id": "ask_1",
                        "function": {
                            "name": "ask_user",
                            "arguments": json.dumps({
                                "text": "你更想看攻略还是代码？",
                                "options": ["攻略", "代码"],
                            }, ensure_ascii=False),
                        },
                    }
                ],
            }
        raise AssertionError("Unexpected heavy tool loop")

    async def fake_execute_tool(name, arguments, bot, chat_id, db_path, notify_state):
        assert name == "ask_user"
        await agent._upsert_pending_question({
            "text": str(arguments.get("text", "")),
            "options": list(arguments.get("options", [])),
            "round_id": agent._current_round_id.get(),
            "client_request_id": agent._current_client_request_id.get(),
        })
        return json.dumps({
            "status": "awaiting_user",
            "question_id": "question_fake",
            "option_count": 2,
        }, ensure_ascii=False)

    _patch_call_llm(monkeypatch, fake_call_llm)
    _patch_execute_tool(monkeypatch, fake_execute_tool)

    result = await agent._run_chat_agent("帮我继续", None, 0, "db.sqlite3", client_request_id="req_wait")
    state = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))
    messages = state["messages"]

    assert result == agent._AWAITING_USER_SENTINEL
    assert [msg["role"] for msg in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "帮我继续"
    assert messages[1]["question_prompt"] is True
    assert messages[1]["content"] == "你更想看攻略还是代码？"
    assert "tool_calls" not in messages[1]
    assert state["pending_question"]["text"] == "你更想看攻略还是代码？"


async def test_answer_pending_question_resumes_same_round(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import coordinator as _agent_coordinator

    seen = {}

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    agent.STATE_FILE.write_text(json.dumps({
        "messages": [
            {"role": "user", "content": "做一个旅游计划", "round_id": "round_1", "message_id": "u1"},
            {"role": "assistant", "content": "你更偏向城市还是自然？", "round_id": "round_1", "question_prompt": True, "question_id": "question_1", "message_id": "a1"},
            {"role": "user", "content": "别的轮次", "round_id": "round_2", "message_id": "u2"},
        ],
        "pending_question": {
            "id": "question_1",
            "text": "你更偏向城市还是自然？",
            "round_id": "round_1",
            "round_title": "旅游计划",
            "client_request_id": "req_ask_1",
            "allow_custom": True,
            "options": [{"id": "option_1", "label": "城市"}, {"id": "option_2", "label": "自然"}],
            "asked_at": "2026-05-19T03:00:00+00:00",
            "meta": {"command": "deep-research"},
        },
    }, ensure_ascii=False), encoding="utf-8")

    async def fake_run_chat_agent(
        user_message,
        bot,
        chat_id,
        db_path,
        ephemeral_system="",
        forced_round_id="",
        history_override=None,
        persist_base_messages=None,
        persist_insert_at=None,
        client_request_id="",
        persist_user_message=True,
        public_prompt=None,
        refresh_labels=True,
        hide_initial_detail=False,
        assistant_message_meta=None,
        lang="",
        command="",
        permission_mode="default",
    ):
        from cyrene.agent.context import current_user_request_text
        from cyrene.agent import state as agent_state

        seen["user_message"] = user_message
        seen["authorization_user_request"] = current_user_request_text()
        seen["delegation_receipts_ready"] = agent_state._explicit_delegation_receipts.get() == set()
        seen["delegation_batches_ready"] = agent_state._explicit_delegation_batches.get() == {}
        seen["ephemeral_system"] = ephemeral_system
        seen["forced_round_id"] = forced_round_id
        seen["history_override"] = history_override
        seen["persist_base_messages"] = persist_base_messages
        seen["persist_insert_at"] = persist_insert_at
        seen["client_request_id"] = client_request_id
        seen["persist_user_message"] = persist_user_message
        seen["command"] = command
        seen["permission_mode"] = permission_mode
        return "继续完成后的最终答案"

    monkeypatch.setattr(_agent_coordinator, "_run_chat_agent", fake_run_chat_agent)

    result = await agent.answer_pending_question(
        "question_1",
        "我更偏向城市",
        None,
        0,
        "db.sqlite3",
        client_request_id="req_answer_1",
    )

    state = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))

    assert result == "继续完成后的最终答案"
    assert "pending_question" not in state
    assert seen["user_message"] == "我更偏向城市"
    assert seen["authorization_user_request"] == (
        "做一个旅游计划\n\n用户随后澄清：我更偏向城市"
    )
    assert "你更偏向城市还是自然" not in seen["authorization_user_request"]
    assert seen["delegation_receipts_ready"] is True
    assert seen["delegation_batches_ready"] is True
    assert "answers your earlier clarification question" in seen["ephemeral_system"]
    assert seen["forced_round_id"] == "round_1"
    assert [msg["content"] for msg in seen["history_override"]] == ["做一个旅游计划", "你更偏向城市还是自然？"]
    assert [msg["content"] for msg in seen["persist_base_messages"]] == ["做一个旅游计划", "你更偏向城市还是自然？", "别的轮次"]
    assert seen["persist_insert_at"] == 2
    assert seen["client_request_id"] == "req_answer_1"
    assert seen["persist_user_message"] is True
    assert seen["command"] == "deep-research"


async def test_clarification_resume_can_auto_review_original_cyrene_ui_request(
    monkeypatch, tmp_path
):
    from cyrene import agent
    from cyrene.agent import auto_review
    from cyrene.agent import coordinator as _agent_coordinator
    from cyrene.agent.context import bind_run_context
    from cyrene.workbench.app_control import authorize

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    original = "帮我新建一个对话，然后在新的对话里面搜索野生小熊猫攻略。"
    agent.STATE_FILE.write_text(json.dumps({
        "messages": [
            {"role": "user", "content": original, "round_id": "round_ui", "message_id": "u1"},
            {
                "role": "assistant",
                "content": "你指的是哪个应用？",
                "round_id": "round_ui",
                "question_prompt": True,
                "question_id": "question_ui",
                "message_id": "a1",
            },
        ],
        "pending_question": {
            "id": "question_ui",
            "text": "你指的是哪个应用？",
            "round_id": "round_ui",
            "allow_custom": True,
            "options": [],
            "asked_at": "2026-08-11T00:00:00+00:00",
            "meta": {},
        },
    }, ensure_ascii=False), encoding="utf-8")

    reviews = []
    fallbacks = []

    async def approve_delegation(**kwargs):
        reviews.append(kwargs)
        return True, "原始请求与澄清共同明确授权提交搜索。"

    async def fallback(**_kwargs):
        fallbacks.append(True)
        return "approval-required"

    async def fake_run_chat_agent(
        user_message,
        bot,
        chat_id,
        db_path,
        forced_round_id="",
        **_kwargs,
    ):
        binding = bind_run_context(
            agent_id="main",
            caller="main_agent",
            round_id=forced_round_id,
            session_id="wbchat_ui",
            conversation_source="desktop_local",
        )
        try:
            result = await authorize(
                "cyrene.ui.click.r2",
                {
                    "snapshot_id": "tree_ui",
                    "revision": 4,
                    "node_id": "chat_composer_submit",
                    "action_id": "submit",
                },
                reason="提交已输入的新对话搜索请求",
                delegation_quote=original,
            )
        finally:
            binding.reset()
        return "continued" if result is None else result

    monkeypatch.setattr(auto_review, "review_user_delegation", approve_delegation)
    monkeypatch.setattr(
        "cyrene.workbench.app_control.request_self_configuration_confirmation",
        fallback,
    )
    monkeypatch.setattr(_agent_coordinator, "_run_chat_agent", fake_run_chat_agent)
    outer = bind_run_context(
        conversation_source="desktop_local",
        user_request_text="在 Cyrene 应用里新建对话",
    )
    try:
        result = await agent.answer_pending_question(
            "question_ui",
            "在 Cyrene 应用里新建对话",
            None,
            0,
            "db.sqlite3",
            permission_mode="auto",
        )
    finally:
        outer.reset()

    assert result == "continued"
    assert fallbacks == []
    assert len(reviews) == 1
    assert reviews[0]["delegation_quote"] == original
    assert reviews[0]["user_request"] == (
        original + "\n\n用户随后澄清：在 Cyrene 应用里新建对话"
    )


async def test_workbench_pending_answer_resume_is_session_owned_and_interruptible(
    monkeypatch,
):
    from cyrene.agent.coordinator import (
        interrupt_active_run,
        is_session_running,
    )
    from cyrene.workbench import runtime as workbench_runtime

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_answer_pending_question(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(
        workbench_runtime,
        "answer_pending_question",
        fake_answer_pending_question,
    )

    task = asyncio.create_task(workbench_runtime._workbench_answer_pending(
        "chat_answer_interrupt",
        "question_1",
        "继续",
        "",
    ))
    await asyncio.wait_for(started.wait(), timeout=1)

    assert is_session_running("chat_answer_interrupt") is True
    assert interrupt_active_run("chat_answer_interrupt") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert is_session_running("chat_answer_interrupt") is False


def test_pending_permission_public_shape_keeps_only_localizable_meta():
    from cyrene.workbench.session_view import build_pending_question

    result = build_pending_question({
        "id": "question_ui",
        "text": "legacy text",
        "meta": {
            "kind": "self_configuration_confirmation",
            "tool_name": "cyrene.ui.click",
            "operation": "cyrene.ui.click.r2",
            "path_hint": "cyrene-setting:argument-hash",
            "reason": "提交搜索请求",
            "secret_internal_plan": "must not leak",
        },
        "options": ["允许这一次", "拒绝"],
    })

    assert result is not None
    assert result["meta"] == {
        "kind": "self_configuration_confirmation",
        "tool_name": "cyrene.ui.click",
        "operation": "cyrene.ui.click.r2",
        "path_hint": "cyrene-setting:argument-hash",
        "reason": "提交搜索请求",
    }


async def test_answer_permission_question_is_hidden_from_context(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import coordinator as _agent_coordinator
    from cyrene.agent.state import _temporary_full_access

    seen = {}

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    agent.STATE_FILE.write_text(json.dumps({
        "messages": [
            {"role": "user", "content": "写到外部路径", "round_id": "round_1", "message_id": "u1"},
            {"role": "user", "content": "别的轮次", "round_id": "round_2", "message_id": "u2"},
        ],
        "pending_question": {
            "id": "question_perm_1",
            "text": "申请写入 workspace 外部路径",
            "round_id": "round_1",
            "client_request_id": "req_perm_1",
            "allow_custom": False,
            "options": [{"id": "option_1", "label": "在本次会话同意"}, {"id": "option_2", "label": "同意一次"}, {"id": "option_3", "label": "拒绝"}],
            "asked_at": "2026-05-19T03:00:00+00:00",
            "hidden_from_chat": True,
            "hide_answer_in_chat": True,
            "meta": {
                "kind": "write_permission_request",
                "tool_name": "Write",
                "path_hint": "/tmp/outside.txt",
                "operation": "写入/删除操作",
                "reason": "需要写测试文件",
            },
        },
    }, ensure_ascii=False), encoding="utf-8")

    async def fake_run_chat_agent(
        user_message,
        bot,
        chat_id,
        db_path,
        ephemeral_system="",
        forced_round_id="",
        history_override=None,
        persist_base_messages=None,
        persist_insert_at=None,
        client_request_id="",
        persist_user_message=True,
        public_prompt=None,
        refresh_labels=True,
        hide_initial_detail=False,
        assistant_message_meta=None,
        lang="",
        command="",
        permission_mode="default",
    ):
        seen["user_message"] = user_message
        seen["ephemeral_system"] = ephemeral_system
        seen["forced_round_id"] = forced_round_id
        seen["history_override"] = history_override
        seen["persist_base_messages"] = persist_base_messages
        seen["persist_insert_at"] = persist_insert_at
        seen["client_request_id"] = client_request_id
        seen["persist_user_message"] = persist_user_message
        seen["public_prompt"] = public_prompt
        seen["permission_mode"] = permission_mode
        return "继续完成后的最终答案"

    monkeypatch.setattr(_agent_coordinator, "_run_chat_agent", fake_run_chat_agent)

    try:
        result = await agent.answer_pending_question(
            "question_perm_1",
            "同意一次",
            None,
            0,
            "db.sqlite3",
            client_request_id="req_answer_perm_1",
            permission_mode="auto",
        )
    finally:
        _temporary_full_access.set(False)

    state = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))

    assert result == "继续完成后的最终答案"
    assert "pending_question" not in state
    assert seen["user_message"] == "[Internal permission decision received. Continue the same round using the system instruction above.]"
    assert "同意一次" not in seen["user_message"]
    assert "granted this exact write/delete permission request once" in seen["ephemeral_system"]
    assert "Permission kind: write_permission_request" in seen["ephemeral_system"]
    assert "Tool: Write" in seen["ephemeral_system"]
    assert seen["forced_round_id"] == "round_1"
    assert [msg["content"] for msg in seen["history_override"]] == ["写到外部路径"]
    assert [msg["content"] for msg in seen["persist_base_messages"]] == ["写到外部路径", "别的轮次"]
    assert seen["persist_insert_at"] == 2
    assert seen["client_request_id"] == "req_answer_perm_1"
    assert seen["persist_user_message"] is False
    assert seen["public_prompt"] == "写到外部路径"
    assert seen["permission_mode"] == "auto"


async def test_bash_destructive_command_requires_confirmation_in_full_access(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import state as agent_state
    from cyrene.tool_impl.core import bash as bash_tool

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "victim").mkdir()
    agent.STATE_FILE.write_text(json.dumps({
        "messages": [{"role": "user", "content": "清理目录", "round_id": "round_1"}],
    }, ensure_ascii=False), encoding="utf-8")

    round_token = agent_state._current_round_id.set("round_1")
    workspace_token = agent_state._active_workspace_dir.set(str(workspace))
    full_token = agent_state._temporary_full_access.set(True)
    mode_token = agent_state._permission_mode.set("full_access")
    try:
        result = await bash_tool._tool_bash(
            {"command": "rm -rf victim", "timeout_ms": 1000},
            None,
            0,
            "",
            {},
        )
    finally:
        agent_state._permission_mode.reset(mode_token)
        agent_state._temporary_full_access.reset(full_token)
        agent_state._active_workspace_dir.reset(workspace_token)
        agent_state._current_round_id.reset(round_token)

    payload = json.loads(result)
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))

    assert payload["status"] == "awaiting_user"
    assert payload["permission"] == "destructive_confirmation"
    assert saved["pending_question"]["hidden_from_chat"] is True
    assert saved["pending_question"]["meta"]["kind"] == "destructive_confirmation"
    assert saved["pending_question"]["meta"]["destructive_kind"] == "file_delete"
    assert (workspace / "victim").exists()


def test_review_clone_refresh_is_not_classified_as_destructive(monkeypatch, tmp_path):
    from cyrene.tooling import runtime_support

    workspace = tmp_path / "workspace"
    workspace.joinpath("scratch").mkdir(parents=True)
    monkeypatch.setattr(runtime_support, "WORKSPACE_DIR", workspace)
    command = "rm -rf scratch/demo-review && git clone --depth 1 https://example.com/demo.git scratch/demo-review"
    assert runtime_support._classify_destructive_shell_command(command) is None
    assert runtime_support._classify_destructive_shell_command("rm -rf scratch/demo") is not None
    assert runtime_support._classify_destructive_shell_command("rm -rf ../demo-review && git clone https://example.com/demo.git ../demo-review") is not None


async def test_send_wechat_file_does_not_prompt_in_full_access(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import state as agent_state
    from cyrene.tool_impl.delivery import send_wechat_file as wechat_tool

    class FakeWechatBot:
        def __init__(self):
            self.sent = []

        async def send_file(self, chat_id, filepath, filename):
            self.sent.append((chat_id, filepath, filename))
            return True

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "Desktop"
    outside.mkdir()
    target = outside / "rfi.pdf"
    target.write_bytes(b"%PDF-1.4\n")
    agent.STATE_FILE.write_text(json.dumps({
        "messages": [{"role": "user", "content": "把 rfi.pdf 发我", "round_id": "round_1"}],
    }, ensure_ascii=False), encoding="utf-8")

    bot = FakeWechatBot()
    round_token = agent_state._current_round_id.set("round_1")
    workspace_token = agent_state._active_workspace_dir.set(str(workspace))
    mode_token = agent_state._permission_mode.set("full_access")
    full_token = agent_state._temporary_full_access.set(False)
    try:
        result = await wechat_tool._tool_send_wechat_file(
            {"path": str(target), "name": "rfi.pdf", "text": "你桌面上的 rfi.pdf"},
            bot,
            123,
            "",
            {},
        )
    finally:
        agent_state._temporary_full_access.reset(full_token)
        agent_state._permission_mode.reset(mode_token)
        agent_state._active_workspace_dir.reset(workspace_token)
        agent_state._current_round_id.reset(round_token)

    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))
    assert result == "File sent via WeChat: rfi.pdf"
    assert bot.sent == [("123", str(target), "rfi.pdf")]
    assert "pending_question" not in saved


async def test_send_telegram_does_not_prompt_in_full_access(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import state as agent_state
    from cyrene.tool_impl.delivery import send_telegram as telegram_tool

    class FakeTelegramBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    agent.STATE_FILE.write_text(json.dumps({
        "messages": [{"role": "user", "content": "发 Telegram 通知", "round_id": "round_1"}],
    }, ensure_ascii=False), encoding="utf-8")

    bot = FakeTelegramBot()
    round_token = agent_state._current_round_id.set("round_1")
    mode_token = agent_state._permission_mode.set("full_access")
    full_token = agent_state._temporary_full_access.set(False)
    try:
        result = await telegram_tool._tool_send_message(
            {"text": "任务完成"},
            bot,
            456,
            "",
            {},
        )
    finally:
        agent_state._temporary_full_access.reset(full_token)
        agent_state._permission_mode.reset(mode_token)
        agent_state._current_round_id.reset(round_token)

    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))
    assert result == "Message sent."
    assert bot.sent == [(456, "任务完成")]
    assert "pending_question" not in saved


async def test_start_shell_allows_external_cwd_in_full_access(monkeypatch, tmp_path):
    from cyrene.agent import state as agent_state
    from cyrene.tool_impl.code import start_shell as start_shell_tool

    outside = tmp_path / "outside"
    outside.mkdir()
    seen = {}

    async def fake_start_shell_session(
        command,
        cwd,
        title,
        round_id,
        wake_on_exit=False,
        wake_chat_id="",
        wake_note="",
    ):
        seen.update({
            "command": command,
            "cwd": cwd,
            "title": title,
            "round_id": round_id,
            "wake_on_exit": wake_on_exit,
            "wake_chat_id": wake_chat_id,
            "wake_note": wake_note,
        })
        return {"id": "shell_1", "status": "running", "cwd": cwd, "title": title}

    monkeypatch.setattr(start_shell_tool, "_start_shell_session", fake_start_shell_session)

    round_token = agent_state._current_round_id.set("round_1")
    mode_token = agent_state._permission_mode.set("full_access")
    full_token = agent_state._temporary_full_access.set(True)
    try:
        result = await start_shell_tool._tool_start_shell(
            {"cwd": str(outside), "command": "", "title": "external"},
            None,
            0,
            "",
            {},
        )
    finally:
        agent_state._temporary_full_access.reset(full_token)
        agent_state._permission_mode.reset(mode_token)
        agent_state._current_round_id.reset(round_token)

    payload = json.loads(result)
    assert payload["status"] == "running"
    assert payload["cwd"] == str(outside)
    assert seen["cwd"] == str(outside)
    assert seen["wake_on_exit"] is False


async def test_send_wechat_file_uses_auto_review_without_prompt(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import auto_review
    from cyrene.agent import state as agent_state
    from cyrene.tool_impl.delivery import send_wechat_file as wechat_tool

    class FakeWechatBot:
        async def send_file(self, chat_id, filepath, filename):
            return True

    seen = {}

    async def fake_review_elevation(**kwargs):
        seen.update(kwargs)
        return True, "文件发送符合用户请求。"

    monkeypatch.setattr(auto_review, "review_elevation", fake_review_elevation)
    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "rfi.pdf"
    target.write_bytes(b"%PDF-1.4\n")
    agent.STATE_FILE.write_text(json.dumps({
        "messages": [{"role": "user", "content": "把 rfi.pdf 发我", "round_id": "round_1"}],
    }, ensure_ascii=False), encoding="utf-8")

    round_token = agent_state._current_round_id.set("round_1")
    workspace_token = agent_state._active_workspace_dir.set(str(workspace))
    mode_token = agent_state._permission_mode.set("auto")
    full_token = agent_state._temporary_full_access.set(False)
    try:
        result = await wechat_tool._tool_send_wechat_file(
            {"path": str(target), "name": "rfi.pdf", "text": "你桌面上的 rfi.pdf"},
            FakeWechatBot(),
            123,
            "",
            {},
        )
        full_access_after_review = agent_state._temporary_full_access.get()
    finally:
        agent_state._temporary_full_access.reset(full_token)
        agent_state._permission_mode.reset(mode_token)
        agent_state._active_workspace_dir.reset(workspace_token)
        agent_state._current_round_id.reset(round_token)

    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))
    assert result == "File sent via WeChat: rfi.pdf"
    assert seen["tool_name"] == "send_wechat_file"
    assert seen["operation"] == "外发 WeChat 文件"
    assert full_access_after_review is False
    assert "pending_question" not in saved


async def test_analyze_attachment_retries_external_path_after_auto_approval(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import auto_review
    from cyrene.agent import state as agent_state
    from cyrene.tool_impl.core import analyze_attachment as analyze_tool

    outside = tmp_path / "outside.txt"
    outside.write_text("hello", encoding="utf-8")
    seen = {}

    async def fake_review_elevation(**kwargs):
        seen["review"] = kwargs
        return True, "读取符合用户请求。"

    async def fake_analyze_attachment(path, prompt="", force_refresh=False):
        seen["path"] = path
        seen["prompt"] = prompt
        seen["force_refresh"] = force_refresh
        return {"ok": True, "text": "hello"}

    monkeypatch.setattr(auto_review, "review_elevation", fake_review_elevation)
    monkeypatch.setattr(analyze_tool, "analyze_attachment", fake_analyze_attachment)
    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    agent.STATE_FILE.write_text(json.dumps({
        "messages": [{"role": "user", "content": "分析外部文件", "round_id": "round_1"}],
    }, ensure_ascii=False), encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    round_token = agent_state._current_round_id.set("round_1")
    workspace_token = agent_state._active_workspace_dir.set(str(workspace))
    mode_token = agent_state._permission_mode.set("auto")
    full_token = agent_state._temporary_full_access.set(False)
    try:
        result = await analyze_tool._tool_analyze_attachment(
            {"path": str(outside), "prompt": "summarize"},
            None,
            0,
            "",
            {},
        )
        full_access_after_review = agent_state._temporary_full_access.get()
    finally:
        agent_state._temporary_full_access.reset(full_token)
        agent_state._permission_mode.reset(mode_token)
        agent_state._active_workspace_dir.reset(workspace_token)
        agent_state._current_round_id.reset(round_token)

    payload = json.loads(result)
    assert payload == {"ok": True, "text": "hello"}
    assert seen["review"]["tool_name"] == "AnalyzeAttachment"
    assert seen["path"] == str(outside)
    assert seen["prompt"] == "summarize"
    assert full_access_after_review is False


async def test_destructive_confirmation_answer_remembers_single_operation(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import coordinator as _agent_coordinator
    from cyrene.agent import state as agent_state
    from cyrene.tooling.runtime_support import _destructive_operation_fingerprint

    seen = {}
    fingerprint = _destructive_operation_fingerprint(
        tool_name="Bash",
        operation="文件删除操作",
        detail="命令：rm -rf victim",
        destructive_kind="file_delete",
    )

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    agent.STATE_FILE.write_text(json.dumps({
        "messages": [{"role": "user", "content": "清理目录", "round_id": "round_1"}],
        "pending_question": {
            "id": "question_destructive_1",
            "text": "确认删除",
            "round_id": "round_1",
            "client_request_id": "req_destructive_1",
            "options": [{"id": "option_1", "label": "允许这次"}, {"id": "option_2", "label": "拒绝"}],
            "asked_at": "2026-06-29T00:00:00+00:00",
            "hidden_from_chat": True,
            "hide_answer_in_chat": True,
            "meta": {
                "kind": "destructive_confirmation",
                "tool_name": "Bash",
                "operation": "文件删除操作",
                "reason": "命令：rm -rf victim",
                "fingerprint": fingerprint,
            },
        },
    }, ensure_ascii=False), encoding="utf-8")

    async def fake_run_chat_agent(*args, **kwargs):
        seen["ephemeral_system"] = kwargs.get("ephemeral_system", "")
        seen["fingerprints"] = agent_state._destructive_confirmation_fingerprints.get()
        seen["full_access"] = agent_state._temporary_full_access.get()
        return "继续执行"

    monkeypatch.setattr(_agent_coordinator, "_run_chat_agent", fake_run_chat_agent)

    original_fingerprints = agent_state._destructive_confirmation_fingerprints.set(frozenset())
    original_allow_all = agent_state._destructive_confirmation_allow_all.set(False)
    try:
        result = await agent.answer_pending_question(
            "question_destructive_1",
            "允许这次",
            None,
            0,
            "db.sqlite3",
            client_request_id="req_answer_destructive_1",
        )
    finally:
        agent_state._destructive_confirmation_allow_all.reset(original_allow_all)
        agent_state._destructive_confirmation_fingerprints.reset(original_fingerprints)

    assert result == "继续执行"
    assert fingerprint in seen["fingerprints"]
    assert seen["full_access"] is False
    assert "confirmed the destructive" in seen["ephemeral_system"]


async def test_send_message_posts_intermediate_reply_without_permission(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import state as agent_state
    from cyrene.tool_impl.delivery import send_message as send_message_tool

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    agent.STATE_FILE.write_text(json.dumps({
        "messages": [{"role": "user", "content": "发个进度", "round_id": "round_1"}],
    }, ensure_ascii=False), encoding="utf-8")

    round_token = agent_state._current_round_id.set("round_1")
    agent_token = agent_state._current_agent_id.set("main")
    request_token = agent_state._current_client_request_id.set("req_1")
    try:
        result = await send_message_tool._tool_send_user_message(
            {"text": "我正在处理"},
            None,
            0,
            "",
            {},
        )
    finally:
        agent_state._current_client_request_id.reset(request_token)
        agent_state._current_agent_id.reset(agent_token)
        agent_state._current_round_id.reset(round_token)

    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))

    assert result == "Mid-run message sent to the user."
    assert "pending_question" not in saved
    assert saved["messages"][-1]["role"] == "assistant"
    assert saved["messages"][-1]["content"] == "我正在处理"
    assert saved["messages"][-1]["intermediate_reply"] is True
    assert saved["messages"][-1]["client_request_id"] == "req_1"


def test_build_current_session_exposes_pending_question(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(routes, "_SERVER_STARTED_AT", 0)
    monkeypatch.setattr(routes, "get_live_rounds", lambda: [])
    monkeypatch.setattr(routes, "list_live_shells", lambda include_exited=False: [])

    routes.STATE_FILE.write_text(json.dumps({
        "session_title": "当前会话",
        "messages": [
            {"role": "user", "content": "帮我订机票", "round_id": "round_1", "message_id": "u1"},
            {"role": "assistant", "content": "你是要单程还是往返？", "round_id": "round_1", "question_prompt": True, "question_id": "question_1", "message_id": "a1"},
        ],
        "pending_question": {
            "id": "question_1",
            "text": "你是要单程还是往返？",
            "round_id": "round_1",
            "round_title": "订机票",
            "client_request_id": "req_ask_1",
            "allow_custom": True,
            "options": [{"id": "option_1", "label": "单程"}, {"id": "option_2", "label": "往返"}],
            "asked_at": "2026-05-19T03:00:00+00:00",
        },
    }, ensure_ascii=False), encoding="utf-8")

    session = routes._build_current_session()

    assert session["status"] == "queued"
    assert session["pendingQuestion"]["id"] == "question_1"
    assert session["pendingQuestion"]["text"] == "你是要单程还是往返？"
    assert [item["label"] for item in session["pendingQuestion"]["options"]] == ["单程", "往返"]
    assert session["chat"]["messages"][-1]["questionPrompt"] is True


def test_reply_stream_chunks_reconstructs_original_text():
    from cyrene.workbench import runtime as routes

    text = "第一段先说重点。\n\n第二段补充更多细节，而且这一段稍微长一点，方便验证分块逻辑。"
    chunks = routes._reply_stream_chunks(text, target_chars=12)

    assert chunks
    assert len(chunks) > 1
    assert "".join(chunks) == text


async def test_stream_reply_payload_emits_ndjson_events():
    from cyrene.workbench import runtime as routes

    response = await routes._stream_reply_payload("你好，世界")
    body = b""
    async for chunk in response.body_iterator:
        body += chunk.encode("utf-8") if isinstance(chunk, str) else chunk

    events = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]

    assert events[0]["type"] == "reply_start"
    assert any(event["type"] == "reply_delta" for event in events)
    assert events[-1] == {"type": "reply_done", "response": "你好，世界"}


async def test_intermediate_agent_calls_stream_only_reasoning_to_workbench(monkeypatch):
    from cyrene import call_llm as cll
    from cyrene.agent import state as agent_state

    captured = []

    async def fake_call_llm(messages, **kwargs):
        assert kwargs["stream"] is True
        callback = kwargs["stream_callback"]
        await callback({"type": "reasoning_start"})
        await callback({"type": "reasoning_delta", "delta": "先检查上下文"})
        await callback({"type": "reasoning_done", "response": "先检查上下文"})
        await callback({"type": "reply_start"})
        await callback({"type": "reply_delta", "delta": "internal tool preamble"})
        return {"role": "assistant", "content": "internal tool preamble"}

    async def collect(event):
        captured.append(event)

    monkeypatch.setattr(cll, "call_llm", fake_call_llm)
    token = agent_state._reply_stream_writer.set(collect)
    try:
        result = await agent_state._call_llm(
            [{"role": "user", "content": "inspect"}],
            tools=[{"type": "function", "function": {"name": "Read"}}],
        )
    finally:
        agent_state._reply_stream_writer.reset(token)

    assert result["content"] == "internal tool preamble"
    assert [event["type"] for event in captured] == [
        "reasoning_start",
        "reasoning_delta",
        "reasoning_done",
    ]


async def test_upstream_stream_emits_reasoning_deltas_before_reply():
    from cyrene.call_llm import _handle_stream

    events = []

    class FakeResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"reasoning_content":"先分析"}}]}'
            yield 'data: {"choices":[{"delta":{"reasoning_content":"，再回答","content":"完成"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def stream(self, *args, **kwargs):
            return FakeResponse()

    async def capture(event):
        events.append(event)

    message = await _handle_stream(
        FakeClient(),
        "https://example.test/v1/chat/completions",
        {"messages": []},
        {},
        capture,
    )

    assert message["reasoning_content"] == "先分析，再回答"
    assert message["content"] == "完成"
    assert [event["type"] for event in events] == [
        "reasoning_start",
        "reasoning_delta",
        "reasoning_delta",
        "reply_start",
        "reply_delta",
        "reasoning_done",
        "reply_done",
    ]


async def test_run_main_agent_chat_only_streams_final_reply(monkeypatch):
    from cyrene import agent
    from cyrene.agent import state as _agent_state

    saved = {}
    streamed = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        return {"content": "internal draft"}

    fake_call_llm_stream = AsyncMock()

    async def fake_save_session_messages(messages, **_kwargs):
        saved["messages"] = list(messages)

    _patch_call_llm(monkeypatch, fake_call_llm)
    _patch_call_llm_stream(monkeypatch, fake_call_llm_stream)
    _patch_save_session(monkeypatch, fake_save_session_messages)
    _patch_append_session(monkeypatch, AsyncMock())
    monkeypatch.setattr(_agent_state, "_publish_runtime_event", AsyncMock())

    async def collect(event):
        streamed.append(event)

    token = agent._reply_stream_writer.set(collect)
    round_token = agent._current_round_id.set("round_stream")
    try:
        result = await agent._run_main_agent(
            "直接聊聊天",
            [],
            None,
            0,
            "db.sqlite3",
            system_prompt="system",
            client_request_id="req_stream",
        )
    finally:
        agent._current_round_id.reset(round_token)
        agent._reply_stream_writer.reset(token)

    assert result == "internal draft"
    assert streamed == [
        {"type": "reply_start"},
        {"type": "reply_delta", "delta": "internal draft"},
        {"type": "reply_done", "response": "internal draft"},
    ]
    fake_call_llm_stream.assert_not_awaited()
    assert saved["messages"][-1]["content"] == "internal draft"
    assert saved["messages"][-1]["client_request_id"] == "req_stream"


async def test_streaming_phase2_delivers_valid_assistant_content_on_quit(
    tmp_path, monkeypatch
):
    """Normal assistant content on a quit turn is delivered without rebuilding."""
    from cyrene.agent import agent as _agent_core
    from cyrene.agent import state as _agent_state

    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"_session_epoch": _agent_state._session_epoch, "messages": []}),
        encoding="utf-8",
    )
    _patch_state_file(monkeypatch, state_file)
    _patch_data_dir(monkeypatch, tmp_path)

    calls = []
    saved = {}
    streamed = []
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "phase1",
                "function": {
                    "name": "use_tools",
                    "arguments": json.dumps({"task": "inspect"}),
                },
            }],
        },
        {
            "content": "已经完成检查。",
            "tool_calls": [{
                "id": "quit1",
                "function": {
                    "name": "quit",
                    "arguments": "{}",
                },
            }],
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 20,
                "total_tokens": 1220,
            },
        },
    ])

    async def fake_call_llm(messages, tools=None, **_kwargs):
        calls.append(json.dumps(tools, sort_keys=True))
        return next(responses)

    async def fake_save(messages, **_kwargs):
        saved["messages"] = list(messages)

    fake_stream = AsyncMock()
    _patch_call_llm(monkeypatch, fake_call_llm)
    _patch_call_llm_stream(monkeypatch, fake_stream)
    _patch_save_session(monkeypatch, fake_save)
    _patch_append_session(monkeypatch, AsyncMock())
    monkeypatch.setattr(_agent_core, "_publish_runtime_event", AsyncMock())

    async def collect(event):
        streamed.append(event)

    writer_token = _agent_state._reply_stream_writer.set(collect)
    try:
        result = await _agent_core._run_main_agent(
            "inspect", [], None, 0, "db.sqlite3",
            client_request_id="req_direct_quit",
        )
    finally:
        _agent_state._reply_stream_writer.reset(writer_token)

    assert result == "已经完成检查。"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    fake_stream.assert_not_awaited()
    assert streamed == [
        {"type": "reply_start"},
        {"type": "reply_delta", "delta": "已经完成检查。"},
        {"type": "reply_done", "response": "已经完成检查。"},
    ]
    assert saved["messages"][-1]["content"] == "已经完成检查。"
    assert "tool_calls" not in saved["messages"][-1]
    assert saved["messages"][-1]["usage"]["prompt_tokens"] == 1200


async def test_streaming_phase2_dsml_assistant_content_uses_no_tool_wrapup(
    tmp_path, monkeypatch
):
    """Tool markup on a quit turn is repaired without reopening tools."""
    from cyrene.agent import agent as _agent_core
    from cyrene.agent import state as _agent_state

    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"_session_epoch": _agent_state._session_epoch, "messages": []}),
        encoding="utf-8",
    )
    _patch_state_file(monkeypatch, state_file)
    _patch_data_dir(monkeypatch, tmp_path)

    dsml = (
        '<｜｜DSML｜｜tool_calls>'
        '<｜｜DSML｜｜invoke name="WebSearch"/>'
        '</｜｜DSML｜｜tool_calls>'
    )
    calls = []
    wrap_tools = []
    saved = {}
    streamed = []
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "phase1",
                "function": {
                    "name": "use_tools",
                    "arguments": json.dumps({"task": "inspect"}),
                },
            }],
        },
        {
            "content": dsml,
            "tool_calls": [{
                "id": "quit1",
                "function": {
                    "name": "quit",
                    "arguments": "{}",
                },
            }],
        },
    ])

    async def fake_call_llm(messages, tools=None, **_kwargs):
        calls.append(json.dumps(tools, sort_keys=True))
        return next(responses)

    async def fake_call_llm_stream(_messages, max_tokens=None, tools=None):
        wrap_tools.append(json.dumps(tools, sort_keys=True))
        await _agent_core._emit_reply_stream_event({"type": "reply_start"})
        await _agent_core._emit_reply_stream_event({
            "type": "reply_delta", "delta": "安全的最终答复。",
        })
        await _agent_core._emit_reply_stream_event({
            "type": "reply_done", "response": "安全的最终答复。",
        })
        return {
            "role": "assistant",
            "content": "安全的最终答复。",
            "usage": {
                "prompt_tokens": 1300,
                "completion_tokens": 20,
                "total_tokens": 1320,
            },
        }

    async def fake_save(messages, **_kwargs):
        saved["messages"] = list(messages)

    _patch_call_llm(monkeypatch, fake_call_llm)
    _patch_call_llm_stream(monkeypatch, fake_call_llm_stream)
    _patch_save_session(monkeypatch, fake_save)
    _patch_append_session(monkeypatch, AsyncMock())
    monkeypatch.setattr(_agent_core, "_publish_runtime_event", AsyncMock())

    async def collect(event):
        streamed.append(event)

    writer_token = _agent_state._reply_stream_writer.set(collect)
    try:
        result = await _agent_core._run_main_agent(
            "inspect", [], None, 0, "db.sqlite3",
        )
    finally:
        _agent_state._reply_stream_writer.reset(writer_token)

    assert result == "安全的最终答复。"
    assert len(wrap_tools) == 1
    assert wrap_tools[0] == "null"
    assert calls[0] == calls[1]
    assert all("DSML" not in str(event) for event in streamed)
    assert all(
        "DSML" not in str(message.get("content") or "")
        for message in saved["messages"]
    )


async def test_quit_wrap_up_never_reenters_tool_loop(tmp_path, monkeypatch):
    """Once quit is observed, recovery is no-tool and cannot revive execution."""
    from cyrene.agent import agent as _agent_core
    from cyrene.agent import state as _agent_state

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"_session_epoch": _agent_state._session_epoch, "messages": []}), encoding="utf-8")
    _patch_state_file(monkeypatch, state_file)
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_core, "_publish_runtime_event", AsyncMock())

    saved = {}
    executed = []
    streamed = []
    stream_calls = {"n": 0}

    llm_calls = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        llm_calls.append(tools)
        if len(llm_calls) == 1:  # decision phase → route into execution
            return {"content": "", "tool_calls": [{"id": "d1", "function": {"name": "use_tools", "arguments": "{\"task\":\"看 github 实现\"}"}}]}
        # A malformed terminal batch must not execute the sibling tool.
        return {
            "content": "",
            "tool_calls": [
                {"id": "wf1", "function": {"name": "WebFetch", "arguments": "{\"url\":\"https://example.com/x\"}"}},
                {"id": "q1", "function": {"name": "quit", "arguments": "{}"}},
            ],
        }

    async def fake_call_llm_stream(messages, max_tokens=32000, tools=None):
        stream_calls["n"] += 1
        assert tools is None
        await _agent_core._emit_reply_stream_event({"type": "reply_start"})
        await _agent_core._emit_reply_stream_event({"type": "reply_delta", "delta": "已对比完成"})
        await _agent_core._emit_reply_stream_event({"type": "reply_done", "response": "已对比完成"})
        return {"role": "assistant", "content": "已对比完成"}

    async def fake_execute_tool(name, args, *rest, **kw):
        executed.append(name)
        return f"result of {name}"

    async def fake_save(messages, **_kwargs):
        saved["messages"] = list(messages)

    _patch_call_llm(monkeypatch, fake_call_llm)
    _patch_call_llm_stream(monkeypatch, fake_call_llm_stream)
    _patch_save_session(monkeypatch, fake_save)
    _patch_append_session(monkeypatch, AsyncMock())
    monkeypatch.setattr(_agent_core, "_execute_tool", fake_execute_tool)
    async def collect(event):
        streamed.append(event)

    writer_token = _agent_state._reply_stream_writer.set(collect)
    round_token = _agent_core._current_round_id.set("round_reenter")
    try:
        result = await _agent_core._run_main_agent(
            "你确定吗，看 github 链接",
            [], None, 0, "db.sqlite3",
            system_prompt="system", client_request_id="req_reenter",
        )
    finally:
        _agent_core._current_round_id.reset(round_token)
        _agent_state._reply_stream_writer.reset(writer_token)

    assert result == "已对比完成"
    assert executed == []
    assert stream_calls["n"] == 1
    deltas = "".join(e.get("delta", "") for e in streamed if e["type"] == "reply_delta")
    assert "DSML" not in deltas
    assert "已对比完成" in deltas
    assert saved["messages"][-1]["content"] == "已对比完成"


async def test_stream_agent_reply_forwards_live_events_before_completion(monkeypatch):
    from cyrene import agent
    from cyrene.workbench import runtime as routes

    seen = {"archived": None}

    async def fake_archive_exchange(*args, **kwargs):
        seen["archived"] = (args, kwargs)

    async def fake_run():
        writer = agent._reply_stream_writer.get()
        assert writer is not None
        await writer({"type": "reply_start"})
        await writer({"type": "reply_delta", "delta": "先到"})
        await asyncio.sleep(0)
        await writer({"type": "reply_done", "response": "先到后完"})
        return "先到后完"

    monkeypatch.setattr(routes, "archive_exchange", fake_archive_exchange)
    monkeypatch.setattr(routes, "get_session_labels", lambda: {
        "session_title": "session",
        "round_title": "round",
        "round_id": "round_1",
        "archive_session_id": "session_1",
    })

    response = routes._stream_agent_reply(fake_run, "用户消息")
    body = b""
    async for chunk in response.body_iterator:
        body += chunk.encode("utf-8") if isinstance(chunk, str) else chunk

    events = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]

    assert [event["type"] for event in events] == ["reply_start", "reply_delta", "reply_done"]
    assert seen["archived"] is not None


def test_flush_intermediate_replies_keeps_messages_for_later_saves():
    from cyrene import agent

    base_messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task", "message_id": "u1"},
    ]
    pending = [{
        "role": "assistant",
        "content": "working on it",
        "message_id": "a_mid",
        "intermediate_reply": True,
    }]
    token = agent._pending_intermediate_user_replies.set(pending)
    try:
        agent._flush_intermediate_user_replies(base_messages)
    finally:
        agent._pending_intermediate_user_replies.reset(token)

    assert base_messages[-1]["message_id"] == "a_mid"
    assert base_messages[-1]["intermediate_reply"] is True


async def test_query_round_tool_reports_live_round(monkeypatch, tmp_path):
    from cyrene import subagent
    from cyrene.tool_impl.subagent import query_round as tools

    # Isolate from the ambient data/state.json: the live-round view merges the
    # session state file with the subagent registry, and a real round_1 in the
    # repo state file would shadow the registry entry this test registers.
    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)

    await subagent.clear()
    await subagent.register("alice", "research topic", round_id="round_1")

    result = await tools._tool_query_round({"round_id": "round_1"}, None, 0, "db.sqlite3", None)

    assert "round_1" in result
    assert "research topic" in result


async def test_queue_round_guidance_drains_main_inbox_without_subagents(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import coordinator as _agent_coordinator
    from cyrene.agent import guidance as _agent_guidance
    from cyrene.observability import debug
    from cyrene.runtime import inbox
    import cyrene.runtime.memory.conversations as conversations

    ack_text = "收到这条引导了，我会按新的方向继续这一轮。"

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
    agent.STATE_FILE.write_text(
        json.dumps({
            "session_title": "session label",
            "messages": [
                {"role": "user", "content": "round one question", "round_id": "round_1", "round_title": "round one"},
                {"role": "assistant", "content": "round one reply", "round_id": "round_1", "round_title": "round one"},
                {"role": "user", "content": "other round question", "round_id": "round_2", "round_title": "round two"},
                {"role": "assistant", "content": "other round reply", "round_id": "round_2", "round_title": "round two"},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    seen = {}
    monkeypatch.setattr(
        _agent_guidance,
        "get_live_rounds",
        lambda: [{"id": "round_1", "status": "running", "title": "round one", "pendingGuidance": 0, "runningSubagents": 0, "subagentCount": 0}],
    )

    async def fake_run_chat_agent(
        user_message,
        bot,
        chat_id,
        db_path,
        ephemeral_system="",
        forced_round_id="",
        history_override=None,
        persist_base_messages=None,
        persist_insert_at=None,
        client_request_id="",
        persist_user_message=True,
        public_prompt=None,
        refresh_labels=True,
        hide_initial_detail=False,
        assistant_message_meta=None,
        lang="",
    ):
        seen["user_message"] = user_message
        seen["ephemeral_system"] = ephemeral_system
        seen["forced_round_id"] = forced_round_id
        seen["history_override"] = history_override
        seen["persist_base_messages"] = persist_base_messages
        seen["persist_insert_at"] = persist_insert_at
        seen["client_request_id"] = client_request_id
        seen["persist_user_message"] = persist_user_message
        seen["assistant_message_meta"] = assistant_message_meta
        return "guided reply"

    async def fake_archive_exchange(user_message, assistant_response, chat_id, session_title="", round_title="", round_id="", archive_session_id=""):
        seen["archived"] = (user_message, assistant_response, session_title, round_title, round_id)

    monkeypatch.setattr(_agent_coordinator, "_run_chat_agent", fake_run_chat_agent)
    monkeypatch.setattr(_agent_guidance, "_generate_guidance_ack", AsyncMock(return_value=ack_text))
    monkeypatch.setattr(conversations, "archive_exchange", fake_archive_exchange)
    events = []
    monkeypatch.setattr(debug, "publish_event", lambda event: events.append(event) or asyncio.sleep(0))

    item = await agent.queue_round_guidance("round_1", "please continue with logistics", None, 0, "db.sqlite3", client_request_id="req_1")
    await asyncio.sleep(0.05)
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert item["target_round_id"] == "round_1"
    assert seen["user_message"] == "please continue with logistics"
    assert "main-agent inbox" in seen["ephemeral_system"]
    assert seen["forced_round_id"] == "round_1"
    assert [msg["content"] for msg in seen["history_override"]] == ["round one question", "round one reply"]
    assert [msg["content"] for msg in seen["persist_base_messages"]] == [
        "round one question",
        "round one reply",
        "other round question",
        "other round reply",
        "please continue with logistics",
        ack_text,
    ]
    assert seen["persist_insert_at"] == 6
    assert seen["client_request_id"] == "req_1"
    assert seen["persist_user_message"] is False
    assert seen["assistant_message_meta"] == {"in_reply_to_guidance_id": item["id"]}
    assert seen["archived"][0] == "please continue with logistics"
    assert seen["archived"][2:] == ("session label", "round one", "round_1")
    assert saved[4]["content"] == "please continue with logistics"
    assert saved[4]["queued_guidance_id"] == item["id"]
    assert saved[5]["content"] == ack_text
    assert saved[5]["guidance_ack_for_guidance_id"] == item["id"]
    assert inbox.get_unread_count(agent._MAIN_INBOX_AGENT_ID) == 0
    assert any(
        event.get("type") == "guidance_acknowledged"
        and event.get("client_request_id") == "req_1"
        and event.get("ack_text") == ack_text
        for event in events
    )


async def test_queue_round_guidance_persists_user_message_immediately(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import guidance as _agent_guidance
    from cyrene import subagent
    from cyrene.runtime import inbox

    await subagent.clear()
    await subagent.register("alice", "research topic", round_id="round_1")

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
    agent.STATE_FILE.write_text(
        json.dumps({
            "messages": [
                {"role": "user", "content": "round one question", "round_id": "round_1", "round_title": "round one"},
                {"role": "assistant", "content": "round one reply", "round_id": "round_1", "round_title": "round one"},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(_agent_guidance, "_ensure_main_inbox_worker", lambda *_args, **_kwargs: None)

    item = await agent.queue_round_guidance("round_1", "queued follow-up", None, 0, "db.sqlite3", client_request_id="req_queued")
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert saved[-1]["role"] == "user"
    assert saved[-1]["content"] == "queued follow-up"
    assert saved[-1]["round_id"] == "round_1"
    assert saved[-1]["round_title"] == "round one"
    assert saved[-1]["client_request_id"] == "req_queued"
    assert saved[-1]["queued_guidance_id"] == item["id"]
    assert item["id"].startswith("msg_")
    assert inbox.get_unread_count(agent._MAIN_INBOX_AGENT_ID) == 1


async def test_main_inbox_guidance_relays_to_subagents_and_inserts_reply(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import coordinator as _agent_coordinator
    from cyrene.agent import guidance as _agent_guidance
    from cyrene.observability import debug
    from cyrene.runtime import inbox
    from cyrene import subagent
    import cyrene.runtime.memory.conversations as conversations

    ack_text = "收到，我先把这一轮的结论按你这条要求展开。"

    await subagent.clear()
    await subagent.register("alice", "research topic", round_id="round_1")

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
    agent.STATE_FILE.write_text(
        json.dumps({
            "session_title": "session label",
            "messages": [
                {"role": "user", "content": "round one question", "round_id": "round_1", "round_title": "round one"},
                {"role": "assistant", "content": "round one reply", "round_id": "round_1", "round_title": "round one"},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    seen = {}

    async def fake_fan_out(round_id, content, bot, chat_id, db_path):
        seen["fanout"] = (round_id, content)
        return ["alice"]

    async def fake_wait(round_id, bot, chat_id, db_path):
        seen["wait"] = round_id
        return False, "[alice] task: research topic\nstatus: done\nresult:\nDetailed finding"

    async def fake_summary_subagent(round_id, parent_task="", guidance="", round_history=None):
        seen["synth"] = (parent_task, round_id, guidance, [m["content"] for m in (round_history or [])])
        return "expanded reply"

    async def fake_flow_snapshot(_round_id):
        return {}

    async def fake_archive_exchange(user_message, assistant_response, chat_id, session_title="", round_title="", round_id="", archive_session_id=""):
        seen["archived"] = (user_message, assistant_response, session_title, round_title, round_id)

    async def fail_run_chat_agent(*_args, **_kwargs):
        raise AssertionError("_run_chat_agent should not run when the round already has subagents")

    monkeypatch.setattr(_agent_guidance, "fan_out_guidance_to_subagents", fake_fan_out)
    monkeypatch.setattr(_agent_guidance, "_wait_for_subagent_round", fake_wait)
    monkeypatch.setattr(subagent, "run_summary_subagent", fake_summary_subagent)
    monkeypatch.setattr(subagent, "build_flow_snapshot", fake_flow_snapshot)
    monkeypatch.setattr(_agent_coordinator, "_run_chat_agent", fail_run_chat_agent)
    monkeypatch.setattr(_agent_guidance, "_generate_guidance_ack", AsyncMock(return_value=ack_text))
    monkeypatch.setattr(conversations, "archive_exchange", fake_archive_exchange)
    events = []
    monkeypatch.setattr(debug, "publish_event", lambda event: events.append(event) or asyncio.sleep(0))

    item = await agent.queue_round_guidance("round_1", "please expand section B", None, 0, "db.sqlite3", client_request_id="req_sub")
    await asyncio.sleep(0.05)
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert item["target_round_id"] == "round_1"
    assert seen["fanout"] == ("round_1", "please expand section B")
    assert seen["wait"] == "round_1"
    assert seen["synth"][0] == "round one question"
    assert seen["synth"][1] == "round_1"
    assert seen["synth"][2] == "please expand section B"
    assert saved[-3]["content"] == "please expand section B"
    assert saved[-3]["queued_guidance_id"] == item["id"]
    assert saved[-2]["content"] == ack_text
    assert saved[-2]["guidance_ack_for_guidance_id"] == item["id"]
    assert saved[-1]["content"] == "expanded reply"
    assert saved[-1]["client_request_id"] == "req_sub"
    assert saved[-1]["in_reply_to_guidance_id"] == item["id"]
    assert seen["archived"] == ("please expand section B", "expanded reply", "session label", "round one", "round_1")
    assert any(
        event.get("type") == "guidance_acknowledged"
        and event.get("client_request_id") == "req_sub"
        and event.get("ack_text") == ack_text
        for event in events
    )
    assert any(
        event.get("type") == "chat_message" and event.get("client_request_id") == "req_sub"
        for event in events
    )


async def test_main_inbox_guidance_failure_inserts_error_reply(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import coordinator as _agent_coordinator
    from cyrene.agent import guidance as _agent_guidance
    from cyrene.observability import debug
    from cyrene.runtime import inbox
    import cyrene.runtime.memory.conversations as conversations

    ack_text = "收到，我会按这个补充要求继续处理。"

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
    agent.STATE_FILE.write_text(
        json.dumps({
            "session_title": "session label",
            "messages": [
                {"role": "user", "content": "round one question", "round_id": "round_1", "round_title": "round one"},
                {"role": "assistant", "content": "round one reply", "round_id": "round_1", "round_title": "round one"},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        _agent_guidance,
        "get_live_rounds",
        lambda: [{"id": "round_1", "status": "running", "title": "round one", "pendingGuidance": 0, "runningSubagents": 0, "subagentCount": 0}],
    )

    seen = {}

    async def boom_run_chat_agent(*_args, **_kwargs):
        raise RuntimeError("boom")

    async def fake_archive_exchange(user_message, assistant_response, chat_id, session_title="", round_title="", round_id="", archive_session_id=""):
        seen["archived"] = (user_message, assistant_response, session_title, round_title, round_id)

    monkeypatch.setattr(_agent_coordinator, "_run_chat_agent", boom_run_chat_agent)
    monkeypatch.setattr(_agent_guidance, "_generate_guidance_ack", AsyncMock(return_value=ack_text))
    monkeypatch.setattr(conversations, "archive_exchange", fake_archive_exchange)
    events = []
    monkeypatch.setattr(debug, "publish_event", lambda event: events.append(event) or asyncio.sleep(0))

    item = await agent.queue_round_guidance("round_1", "please retry with details", None, 0, "db.sqlite3", client_request_id="req_fail")
    await asyncio.sleep(0.05)
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert saved[-3]["content"] == "please retry with details"
    assert saved[-3]["queued_guidance_id"] == item["id"]
    assert saved[-2]["content"] == ack_text
    assert saved[-2]["guidance_ack_for_guidance_id"] == item["id"]
    assert saved[-1]["role"] == "assistant"
    assert "Guidance could not be applied because an internal error occurred" in saved[-1]["content"]
    assert saved[-1]["client_request_id"] == "req_fail"
    assert saved[-1]["in_reply_to_guidance_id"] == item["id"]
    assert seen["archived"][0] == "please retry with details"
    assert seen["archived"][2:] == ("session label", "round one", "round_1")
    assert any(
        event.get("type") == "guidance_acknowledged"
        and event.get("client_request_id") == "req_fail"
        and event.get("ack_text") == ack_text
        for event in events
    )
    assert any(
        event.get("type") == "chat_message" and event.get("client_request_id") == "req_fail"
        for event in events
    )


async def test_main_inbox_guidance_continuation_keeps_ack_before_final_reply(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.learning import engine as behavior_learning
    from cyrene.agent import session as _agent_session
    from cyrene.agent import guidance as _agent_guidance
    from cyrene.observability import debug
    from cyrene.runtime import inbox
    import cyrene.runtime.memory.conversations as conversations

    ack_text = "明白，我按你的新要求重做这一轮的回复。"

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_context", lambda max_chars=5000: "")
    monkeypatch.setattr(agent, "get_memory_context", lambda: "")
    agent.STATE_FILE.write_text(
        json.dumps({
            "session_title": "session label",
            "messages": [
                {"role": "user", "content": "round one question", "round_id": "round_1", "round_title": "round one"},
                {"role": "assistant", "content": "round one reply", "round_id": "round_1", "round_title": "round one"},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        _agent_guidance,
        "get_live_rounds",
        lambda: [{"id": "round_1", "status": "running", "title": "round one", "pendingGuidance": 0, "runningSubagents": 0, "subagentCount": 0}],
    )

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        return {
            "content": "adjusted final reply",
            "reasoning_content": "guided reasoning",
            "tool_calls": [],
        }

    async def fake_archive_exchange(user_message, assistant_response, chat_id, session_title="", round_title="", round_id="", archive_session_id=""):
        return None

    _patch_call_llm(monkeypatch, fake_call_llm)
    monkeypatch.setattr(_agent_guidance, "_generate_guidance_ack", AsyncMock(return_value=ack_text))
    monkeypatch.setattr(behavior_learning, "begin_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(conversations, "archive_exchange", fake_archive_exchange)
    events = []
    monkeypatch.setattr(debug, "publish_event", lambda event: events.append(event) or asyncio.sleep(0))

    item = await agent.queue_round_guidance("round_1", "please adjust the answer", None, 0, "db.sqlite3", client_request_id="req_guided")
    await asyncio.sleep(0.05)
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    guided_users = [msg for msg in saved if msg.get("role") == "user" and msg.get("client_request_id") == "req_guided"]
    ack_index = next(i for i, msg in enumerate(saved) if msg.get("guidance_ack_for_guidance_id") == item["id"])
    reply_index = next(i for i, msg in enumerate(saved) if msg.get("role") == "assistant" and msg.get("client_request_id") == "req_guided")

    assert len(guided_users) == 1
    assert guided_users[0]["content"] == "please adjust the answer"
    assert guided_users[0]["queued_guidance_id"] == item["id"]
    assert saved[ack_index]["content"] == ack_text
    assert ack_index == 3
    assert saved[reply_index]["content"] == "adjusted final reply"
    assert reply_index == 4
    assert saved[reply_index]["reasoning_content"] == "guided reasoning"
    assert saved[reply_index]["round_id"] == "round_1"
    assert saved[reply_index]["in_reply_to_guidance_id"] == item["id"]
    assert any(
        event.get("type") == "guidance_acknowledged"
        and event.get("client_request_id") == "req_guided"
        and event.get("ack_text") == ack_text
        for event in events
    )
    assert any(
        event.get("type") == "chat_message"
        and event.get("client_request_id") == "req_guided"
        for event in events
    )


async def test_run_chat_agent_persists_client_request_ids(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.agent import agent as _agent_core
    from cyrene.observability import debug

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_context", lambda max_chars=5000: "")
    monkeypatch.setattr(agent, "get_memory_context", lambda: "")
    events = []
    monkeypatch.setattr(debug, "publish_event", lambda event: events.append(event) or asyncio.sleep(0))

    async def fake_run_main_agent(user_message, history, bot, chat_id, db_path, system_prompt="", client_request_id="", persist_user_message=True, lang="", **kwargs):
        round_id = agent._current_round_id.get()
        await agent._save_session_messages([
            *history,
            {"role": "user", "content": user_message, "round_id": round_id, "client_request_id": client_request_id},
            {"role": "assistant", "content": "raw reply", "round_id": round_id, "client_request_id": client_request_id},
        ])
        return "raw reply"

    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)

    result = await agent._run_chat_agent("current request", None, 0, "db.sqlite3", client_request_id="req_live")
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert result == "raw reply"
    assert saved[-2]["client_request_id"] == "req_live"
    assert saved[-1]["client_request_id"] == "req_live"
    assert saved[-2]["message_id"].startswith("msg_")
    assert saved[-1]["message_id"].startswith("msg_")
    assert any(
        event.get("type") == "chat_message" and event.get("client_request_id") == "req_live"
        for event in events
    )


async def test_run_chat_agent_history_override_preserves_other_rounds(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.agent import agent as _agent_core

    base_messages = [
        {"role": "user", "content": "round one question", "round_id": "round_1"},
        {"role": "assistant", "content": "round one reply", "round_id": "round_1"},
        {"role": "user", "content": "other round question", "round_id": "round_2"},
        {"role": "assistant", "content": "other round reply", "round_id": "round_2"},
    ]

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_context", lambda max_chars=5000: "")
    agent.STATE_FILE.write_text(json.dumps({"messages": base_messages}, ensure_ascii=False), encoding="utf-8")

    async def fake_run_main_agent(user_message, history, bot, chat_id, db_path, system_prompt="", client_request_id="", persist_user_message=True, lang="", **kwargs):
        round_id = agent._current_round_id.get()
        await agent._save_session_messages([
            *history,
            {"role": "user", "content": user_message, "round_id": round_id},
            {"role": "assistant", "content": "raw reply", "round_id": round_id},
        ])
        return "raw reply"

    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)

    result = await agent._run_chat_agent(
        "guided follow-up",
        None,
        0,
        "db.sqlite3",
        forced_round_id="round_1",
        history_override=base_messages[:2],
    )
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert result == "raw reply"
    assert [msg["content"] for msg in saved] == [
        "round one question",
        "round one reply",
        "other round question",
        "other round reply",
        "guided follow-up",
        "raw reply",
    ]


async def test_run_chat_agent_persist_insert_at_keeps_later_queued_messages_in_place(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.agent import agent as _agent_core

    base_messages = [
        {"role": "user", "content": "round one question", "round_id": "round_1"},
        {"role": "assistant", "content": "round one reply", "round_id": "round_1"},
        {"role": "user", "content": "later queued guidance", "round_id": "round_1", "queued_guidance_id": "guide_2"},
    ]

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_context", lambda max_chars=5000: "")
    agent.STATE_FILE.write_text(json.dumps({"messages": base_messages}, ensure_ascii=False), encoding="utf-8")

    async def fake_run_main_agent(user_message, history, bot, chat_id, db_path, system_prompt="", client_request_id="", persist_user_message=True, public_user_message=None, public_attachments=None, lang="", **kwargs):
        round_id = agent._current_round_id.get()
        await agent._save_session_messages([
            *history,
            {"role": "user", "content": user_message, "round_id": round_id},
            {"role": "assistant", "content": "reply to current guidance", "round_id": round_id},
        ])
        return "reply to current guidance"

    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)

    result = await agent._run_chat_agent(
        "current queued guidance",
        None,
        0,
        "db.sqlite3",
        forced_round_id="round_1",
        history_override=base_messages[:2],
        persist_base_messages=base_messages,
        persist_insert_at=2,
    )
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert result == "reply to current guidance"
    assert [msg["content"] for msg in saved] == [
        "round one question",
        "round one reply",
        "current queued guidance",
        "reply to current guidance",
        "later queued guidance",
    ]


async def test_run_chat_agent_live_merge_preserves_concurrent_guidance(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.agent import agent as _agent_core

    base_messages = [
        {"role": "user", "content": "previous question", "round_id": "round_0"},
        {"role": "assistant", "content": "previous reply", "round_id": "round_0"},
    ]

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_context", lambda max_chars=5000: "")
    agent.STATE_FILE.write_text(json.dumps({"messages": base_messages}, ensure_ascii=False), encoding="utf-8")

    async def fake_run_main_agent(user_message, history, bot, chat_id, db_path, system_prompt="", client_request_id="", persist_user_message=True, public_user_message=None, public_attachments=None, lang="", **kwargs):
        await agent._append_session_message({
            "role": "user",
            "content": "queued guidance",
            "round_id": "round_2",
            "queued_guidance_id": "guide_1",
        })
        round_id = agent._current_round_id.get()
        await agent._save_session_messages([
            *history,
            {"role": "user", "content": user_message, "round_id": round_id},
            {"role": "assistant", "content": "raw reply", "round_id": round_id},
        ])
        return "raw reply"

    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)

    result = await agent._run_chat_agent("current request", None, 0, "db.sqlite3", forced_round_id="round_1")
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert result == "raw reply"
    assert [msg["content"] for msg in saved] == [
        "previous question",
        "previous reply",
        "current request",
        "raw reply",
        "queued guidance",
    ]
    assert saved[-1]["queued_guidance_id"] == "guide_1"


async def test_save_session_messages_replaces_live_round_block_without_duplication(monkeypatch, tmp_path):
    from cyrene import agent

    base_messages = [
        {"role": "user", "content": "previous question", "round_id": "round_0"},
        {"role": "assistant", "content": "previous reply", "round_id": "round_0"},
    ]

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    agent.STATE_FILE.write_text(json.dumps({"messages": base_messages}, ensure_ascii=False), encoding="utf-8")

    round_token = agent._current_round_id.set("round_1")
    base_token = agent._persist_base_messages.set(None)
    merge_token = agent._persist_merge_live_state.set(True)
    prefix_token = agent._persist_history_prefix_len.set(len(base_messages))
    insert_token = agent._persist_insert_at.set(len(base_messages))
    try:
        await agent._append_session_message({
            "role": "user",
            "content": "current request",
            "round_id": "round_1",
        })
        await agent._append_session_message({
            "role": "user",
            "content": "queued guidance",
            "round_id": "round_2",
            "queued_guidance_id": "guide_1",
        })
        await agent._save_session_messages([
            *base_messages,
            {"role": "user", "content": "current request", "round_id": "round_1"},
            {"role": "assistant", "content": "raw reply", "round_id": "round_1"},
        ])
    finally:
        agent._persist_insert_at.reset(insert_token)
        agent._persist_history_prefix_len.reset(prefix_token)
        agent._persist_merge_live_state.reset(merge_token)
        agent._persist_base_messages.reset(base_token)
        agent._current_round_id.reset(round_token)

    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert [msg["content"] for msg in saved] == [
        "previous question",
        "previous reply",
        "current request",
        "raw reply",
        "queued guidance",
    ]
    assert saved[-1]["queued_guidance_id"] == "guide_1"


async def test_run_chat_agent_history_override_visible_reply_update_does_not_duplicate_messages(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.agent import agent as _agent_core

    base_messages = [
        {"role": "user", "content": "round one question", "round_id": "round_1"},
        {"role": "assistant", "content": "round one reply", "round_id": "round_1"},
        {"role": "user", "content": "other round question", "round_id": "round_2"},
        {"role": "assistant", "content": "other round reply", "round_id": "round_2"},
    ]

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_context", lambda max_chars=5000: "")
    agent.STATE_FILE.write_text(json.dumps({"messages": base_messages}, ensure_ascii=False), encoding="utf-8")

    async def fake_run_main_agent(user_message, history, bot, chat_id, db_path, system_prompt="", client_request_id="", persist_user_message=True, lang="", **kwargs):
        round_id = agent._current_round_id.get()
        await agent._save_session_messages([
            *history,
            {"role": "user", "content": user_message, "round_id": round_id},
            {"role": "assistant", "content": "raw reply", "round_id": round_id},
        ])
        return "raw reply"

    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)

    result = await agent._run_chat_agent(
        "guided follow-up",
        None,
        0,
        "db.sqlite3",
        forced_round_id="round_1",
        history_override=base_messages[:2],
    )
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert result == "raw reply"
    assert [msg["content"] for msg in saved] == [
        "round one question",
        "round one reply",
        "other round question",
        "other round reply",
        "guided follow-up",
        "raw reply",
    ]


def test_inbox_send_message_is_serialized():
    from cyrene.runtime import inbox
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        inbox.INBOX_DIR = Path(tmp) / "inbox"

        async def send_and_read():
            await asyncio.gather(*[
                inbox.send_message(f"sender_{i}", "receiver", "chat", f"payload_{i}")
                for i in range(20)
            ])
            return await inbox.read_messages("receiver", mark_read=False)

        messages = asyncio.run(send_and_read())
        ids = [m["message_id"] for m in messages]
        assert len(messages) == 20
        assert len(set(ids)) == 20
        assert inbox.get_unread_count("receiver") == 20


async def test_subagent_registry_emits_update_events(monkeypatch):
    from cyrene.observability import debug
    from cyrene import subagent

    seen = []

    async def fake_publish_event(event, **kwargs):
        seen.append({**event, **kwargs})

    monkeypatch.setattr(debug, "publish_event", fake_publish_event)
    await subagent.clear()
    await subagent.register("alice", "review ssh", round_id="round_live", session_id="session_live")
    await subagent.save_messages("alice", [{"role": "assistant", "content": "checking"}])
    await subagent.set_waiting("alice", result="draft ready")
    await subagent.set_resumed("alice")
    await subagent.mark_done("alice", result="done")

    event_types = [event["type"] for event in seen]
    statuses = [event.get("status") for event in seen if event.get("type") == "subagent_update"]

    assert "subagent_update" in event_types
    assert statuses[0] == "running"
    assert "waiting" in statuses
    assert "resumed" in statuses
    assert statuses[-1] == "done"
    assert seen[-1]["round_id"] == "round_live"
    assert seen[-1]["session_id"] == "session_live"
    assert seen[-1]["message_count"] == 1


async def test_run_subagent_persists_quit_tool_messages_before_resume(monkeypatch):
    from cyrene import subagent

    llm_inputs = []
    responses = iter([
        {
            "content": "initial finding",
            "tool_calls": [{"id": "q1", "function": {"name": "quit", "arguments": "{}"}}],
        },
        {
            "content": "final finding",
            "tool_calls": [{"id": "q2", "function": {"name": "quit", "arguments": "{}"}}],
        },
    ])
    wait_results = iter([
        "[from host_moderator] (chat) 请补充一条后勤建议",
        "",
    ])

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        snapshot = json.loads(json.dumps(messages, ensure_ascii=False))
        llm_inputs.append(snapshot)
        assert max_tokens is None
        if len(llm_inputs) == 2:
            assert any(
                msg.get("role") == "tool" and msg.get("tool_call_id") == "q1"
                for msg in snapshot
            ), "Resumed subagent history must include the prior quit tool response"
        return next(responses)

    async def fake_wait_for_others(agent_id, inbox_check_func, mark_read_func=None, max_wait=600, result=""):
        return next(wait_results)

    _patch_call_llm(monkeypatch, fake_call_llm)
    monkeypatch.setattr(subagent, "wait_for_others", fake_wait_for_others)

    await subagent.clear()
    await subagent.register("alice", "research topic")

    result = await subagent._run_subagent("alice", "research topic", None, 0, "db.sqlite3")
    raw = await subagent.get_raw_messages("alice")

    assert result == "final finding"
    assert any(msg.get("role") == "tool" and msg.get("tool_call_id") == "q1" for msg in raw)
    assert any(msg.get("role") == "tool" and msg.get("tool_call_id") == "q2" for msg in raw)


def test_live_flow_contains_tool_nodes_and_comm_edges(tmp_path, monkeypatch):
    from cyrene.observability import debug
    from cyrene.runtime import inbox
    from cyrene.workbench import runtime as routes

    inbox.INBOX_DIR = tmp_path / "inbox"
    asyncio.run(inbox.send_message("alice", "bob", "chat", "Discuss firewall baselines"))

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [
        {
            "type": "tool_call",
            "caller": "main_agent",
            "tool": "spawn_subagent",
            "args": {"agent_id": "alice"},
            "result_preview": "spawned",
        }
    ])

    raw_msgs = [
        {"role": "user", "content": "do work"},
        {"role": "assistant", "content": "", "reasoning_content": "thinking", "usage": {"prompt_tokens": 120, "completion_tokens": 40}, "tool_calls": [
            {"id": "t1", "function": {"name": "spawn_subagent", "arguments": '{"agent_id":"alice"}'}}
        ]},
        {"role": "tool", "tool_call_id": "t1", "content": "spawned"},
        {"role": "assistant", "content": "final answer", "usage": {"prompt_tokens": 30, "completion_tokens": 12}},
    ]
    ui_msgs = routes._convert_messages(raw_msgs)
    subagents = [{
        "id": "alice",
        "name": "alice",
        "status": "running",
        "task": "task A",
        "tokens": 2,
        "elapsed": "00:01",
        "progress": 0.45,
        "result": "",
        "messageCount": 2,
        "createdAt": "12:00:00",
        "updatedAt": "12:00:01",
    }, {
        "id": "bob",
        "name": "bob",
        "status": "queued",
        "task": "task B",
        "tokens": 1,
        "elapsed": "00:01",
        "progress": 0.82,
        "result": "",
        "messageCount": 1,
        "createdAt": "12:00:00",
        "updatedAt": "12:00:01",
    }]
    registry = {
        "alice": {"messages": [{"role": "assistant", "content": "a", "usage": {"prompt_tokens": 18, "completion_tokens": 7}}], "result": "", "status": "running"},
        "bob": {"messages": [], "result": "", "status": "waiting"},
    }

    monkeypatch.setattr(routes, "DATA_DIR", tmp_path)
    flow = routes._build_live_flow(raw_msgs, ui_msgs, subagents, registry)

    tool_nodes = [node for node in flow["nodes"] if node["kind"] == "tool"]
    comm_edges = [edge for edge in flow["edges"] if edge.get("kind") == "comm"]
    output_nodes = [node for node in flow["nodes"] if node["kind"] == "output"]

    assert any(node["title"] == "spawn_subagent" for node in tool_nodes)
    assert any(edge["message"]["body"] == "Discuss firewall baselines" for edge in comm_edges)
    assert output_nodes and output_nodes[0]["detail"]["content"] == "final answer"
    main_node = next(node for node in flow["nodes"] if node["id"] == "n_main")
    alice_node = next(node for node in flow["nodes"] if node["title"] == "subagent · alice")
    assert main_node["detail"]["tokensIn"] == 150
    assert main_node["detail"]["tokensOut"] == 52
    assert alice_node["detail"]["tokensIn"] == 18
    assert alice_node["detail"]["tokensOut"] == 7


def test_live_flow_marks_empty_tool_outputs_done(monkeypatch):
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [])
    raw_msgs = [
        {"role": "user", "content": "run command"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "bash", "arguments": json.dumps({"cmd": "true"})}},
        ]},
        {"role": "tool", "tool_call_id": "t1", "content": ""},
        {"role": "assistant", "content": "done"},
    ]

    flow = routes._build_live_flow(raw_msgs, routes._convert_messages(raw_msgs), [], {})
    tool = next(node for node in flow["nodes"] if node["kind"] == "tool")

    assert tool["status"] == "done"
    assert tool["detail"]["output"] == "Completed with no captured output."


def test_live_flow_marks_tool_without_captured_output_done_after_followup(monkeypatch):
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [])
    raw_msgs = [
        {"role": "user", "content": "research"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "search", "arguments": json.dumps({"query": "alpha"})}},
        ]},
        {"role": "assistant", "content": "summary"},
    ]

    flow = routes._build_live_flow(raw_msgs, routes._convert_messages(raw_msgs), [], {})
    tool = next(node for node in flow["nodes"] if node["kind"] == "tool")

    assert tool["status"] == "done"
    assert "no tool output was captured" in tool["detail"]["output"]


def test_live_flow_marks_recent_overlay_tools_done(monkeypatch):
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [
        {
            "type": "tool_call",
            "caller": "main_agent",
            "tool": "web_search",
            "args": {"query": "latest"},
            "result_preview": "search complete",
            "round_id": "round_live",
        }
    ])
    raw_msgs = [
        {"role": "user", "content": "check latest", "round_id": "round_live"},
        {"role": "assistant", "content": "working", "round_id": "round_live"},
    ]

    flow = routes._build_live_flow(raw_msgs, routes._convert_messages(raw_msgs), [], {})
    tool = next(node for node in flow["nodes"] if node["kind"] == "tool")
    tool_edge = next(edge for edge in flow["edges"] if edge["to"] == tool["id"])

    assert tool["title"] == "web_search"
    assert tool["status"] == "done"
    assert tool_edge.get("kind") is None


def test_build_current_session_uses_live_shell_snapshots(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(routes, "DATA_DIR", tmp_path)
    routes.STATE_FILE.write_text(
        json.dumps({
            "messages": [
                {"role": "user", "content": "run server", "round_id": "round_1"},
                {"role": "assistant", "content": "", "round_id": "round_1", "tool_calls": [
                    {"id": "bash_1", "function": {"name": "Bash", "arguments": json.dumps({"command": "python -m http.server"})}},
                ]},
                {"role": "tool", "tool_call_id": "bash_1", "content": "started", "round_id": "round_1"},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "list_live_shells", lambda include_exited=False: [{
        "id": "shell_live",
        "title": "dev server",
        "cwd": ".",
        "pid": 1234,
        "status": "running",
        "elapsed": "00:12",
        "updatedAt": "12:00:00",
        "lines": [{"kind": "meta", "text": "[shell started]"}],
    }])

    session = routes._build_current_session()

    assert session["shells"][0]["id"] == "shell_live"
    assert len(session["shells"]) == 1


def test_build_current_session_done_event_clears_recent_activity(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    routes.STATE_FILE.write_text(
        '{"messages":[{"role":"user","content":"hello"},{"role":"assistant","content":"done"}]}',
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [
        {"type": "tool_call", "caller": "main_agent", "timestamp": (now - timedelta(seconds=2)).isoformat()},
        {"type": "session_update", "status": "done", "timestamp": now.isoformat()},
        {"type": "llm_call", "caller": "behavior_learning", "timestamp": now.isoformat()},
        {"type": "llm_call", "caller": "compactor", "timestamp": now.isoformat()},
    ])

    session = routes._build_current_session()

    assert session["status"] == "done"


async def test_compress_old_messages_labels_llm_as_compactor(monkeypatch):
    from cyrene.agent import state as _agent_state
    from cyrene.agent import session as _agent_session

    callers = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        callers.append(_agent_state._caller_type.get())
        return {"content": ""}

    monkeypatch.setattr(_agent_session, "_call_llm", fake_call_llm)

    await _agent_session._compress_old_messages([
        {"role": "user", "content": "remember this"},
        {"role": "assistant", "content": "noted"},
    ])

    assert callers == ["compactor"]


def test_build_current_session_ignores_completed_llm_accounting_after_done(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    routes.STATE_FILE.write_text(
        '{"messages":[{"role":"user","content":"hello"},{"role":"assistant","content":"done"}]}',
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [
        {"type": "session_update", "status": "done", "timestamp": (now - timedelta(seconds=2)).isoformat()},
        {"type": "llm_call", "caller": "main_agent", "timestamp": now.isoformat()},
    ])

    session = routes._build_current_session()

    assert session["status"] == "done"


def test_build_sessions_includes_today_archive_when_live_session_exists(tmp_path, monkeypatch):
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "CONVERSATIONS_DIR", tmp_path / "conversations")
    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    routes.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    today = routes.datetime.now().astimezone().strftime("%Y-%m-%d")
    (routes.CONVERSATIONS_DIR / f"{today}.md").write_text(
        "# Conversations\n\n## 08:00:00 UTC\n\n**User**: hi\n\n**Ape**: archived\n\n---\n",
        encoding="utf-8",
    )
    routes.STATE_FILE.write_text(
        '{"messages":[{"role":"user","content":"live hi"},{"role":"assistant","content":"live reply"}]}',
        encoding="utf-8",
    )

    sessions = routes._build_sessions()
    ids = [session["id"] for session in sessions]

    assert ids[0] == "run_live"
    assert f"archive_{today}_legacy_{today}" in ids


def test_build_sessions_skips_archive_copy_of_current_live_session(tmp_path, monkeypatch):
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "CONVERSATIONS_DIR", tmp_path / "conversations")
    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    routes.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    today = routes.datetime.now().astimezone().strftime("%Y-%m-%d")
    (routes.CONVERSATIONS_DIR / f"{today}.md").write_text(
        "# Conversations\n\n"
        "## 08:00:00 UTC\n\n"
        "<!-- archive_session_id: session_live -->\n"
        "<!-- session_title: 当前会话 -->\n\n"
        "**User**: live hi\n\n"
        "**Ape**: archived live reply\n\n"
        "---\n\n"
        "## 09:00:00 UTC\n\n"
        "<!-- archive_session_id: session_other -->\n"
        "<!-- session_title: 另一场会话 -->\n\n"
        "**User**: other hi\n\n"
        "**Ape**: other reply\n\n"
        "---\n",
        encoding="utf-8",
    )
    routes.STATE_FILE.write_text(
        '{"archive_session_id":"session_live","messages":[{"role":"user","content":"live hi"},{"role":"assistant","content":"live reply"}]}',
        encoding="utf-8",
    )

    sessions = routes._build_sessions()
    ids = [session["id"] for session in sessions]

    assert ids[0] == "run_live"
    assert f"archive_{today}_session_live" not in ids
    assert f"archive_{today}_session_other" in ids


def test_build_current_session_recovers_subagents_from_state_and_inbox(tmp_path, monkeypatch):
    from cyrene.runtime import inbox
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")

    routes.STATE_FILE.write_text(
        json.dumps({
            "messages": [
                {"role": "user", "content": "start", "round_id": "round_live"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call_1", "function": {
                        "name": "spawn_subagent",
                        "arguments": json.dumps({"agent_id": "alice", "task": "review firewall"})
                    }}
                ], "round_id": "round_live"},
                {"role": "tool", "tool_call_id": "call_1", "content": "spawned", "round_id": "round_live"},
                {"role": "assistant", "content": "done", "round_id": "round_live"},
            ]
        }),
        encoding="utf-8",
    )
    asyncio.run(inbox.send_message("alice", "bob", "chat", "Use ufw and fail2ban", round_id="round_live"))

    session = routes._build_current_session()
    subagents = {item["name"]: item for item in session["subagents"]}
    subagent_names = set(subagents)
    flow_titles = {node["title"] for node in session["flow"]["nodes"] if node["kind"] == "subagent"}
    comm_edges = [edge for edge in session["flow"]["edges"] if edge.get("kind") == "comm"]

    assert {"alice", "bob"}.issubset(subagent_names)
    assert session["currentRoundId"] == "round_live"
    assert subagents["alice"]["roundId"] == "round_live"
    assert subagents["bob"]["roundId"] == "round_live"
    assert "subagent · alice" in flow_titles
    assert "subagent · bob" in flow_titles
    assert any(edge["message"]["body"] == "Use ufw and fail2ban" for edge in comm_edges)


async def test_clear_session_id_removes_live_flow_residue(tmp_path, monkeypatch):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.runtime import inbox
    from cyrene import subagent
    from cyrene.workbench import runtime as routes

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(_agent_session, "_compress_old_messages", AsyncMock())

    agent.STATE_FILE.write_text(
        json.dumps({
            "messages": [
                {"role": "user", "content": "start", "round_id": "round_live"},
                {"role": "assistant", "content": "", "round_id": "round_live", "tool_calls": [
                    {"id": "call_1", "function": {
                        "name": "spawn_subagent",
                        "arguments": json.dumps({"agent_id": "alice", "task": "review firewall"})
                    }}
                ]},
                {"role": "tool", "tool_call_id": "call_1", "content": "spawned", "round_id": "round_live"},
                {"role": "assistant", "content": "waiting", "round_id": "round_live"},
            ]
        }),
        encoding="utf-8",
    )
    await subagent.register("alice", "review firewall", round_id="round_live")
    await inbox.send_message("alice", "bob", "chat", "Use ufw and fail2ban", round_id="round_live")

    before = routes._build_current_session()
    assert before["flow"]["nodes"]

    await agent.clear_session_id()

    after = routes._build_current_session()
    assert after["title"] == "new session"
    assert after["subagents"] == []
    assert after["flow"]["nodes"] == []
    assert after["flow"]["edges"] == []


def test_build_user_reads_local_username(monkeypatch):
    from cyrene.workbench import runtime as routes

    monkeypatch.setenv("USER", "localtester")
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    monkeypatch.setattr(routes.getpass, "getuser", lambda: "ignored-user")

    user = routes._build_user()

    assert user["name"] == "localtester"
    assert user["handle"] == "localtester"
    assert user["initials"] == "L"


def test_live_flow_staggers_subagents_when_tool_stacks_are_tall(monkeypatch):
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [])
    raw_msgs = [
        {"role": "user", "content": "plan"},
        {"role": "assistant", "content": "final"},
    ]
    ui_msgs = routes._convert_messages(raw_msgs)
    subagents = [
        {
            "id": "alpha",
            "name": "alpha",
            "status": "done",
            "task": "task alpha",
            "tokens": 0,
            "elapsed": "00:01",
            "progress": 1.0,
            "result": "",
            "messageCount": 0,
            "createdAt": "12:00:00",
            "updatedAt": "12:00:01",
        },
        {
            "id": "beta",
            "name": "beta",
            "status": "done",
            "task": "task beta",
            "tokens": 0,
            "elapsed": "00:01",
            "progress": 1.0,
            "result": "",
            "messageCount": 0,
            "createdAt": "12:00:02",
            "updatedAt": "12:00:03",
        },
    ]
    registry = {
        "alpha": {
            "messages": [
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "a1", "function": {"name": "search", "arguments": "{}"}},
                    {"id": "a2", "function": {"name": "bash", "arguments": "{}"}},
                    {"id": "a3", "function": {"name": "read", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "a1", "content": "ok"},
                {"role": "tool", "tool_call_id": "a2", "content": "ok"},
                {"role": "tool", "tool_call_id": "a3", "content": "ok"},
            ],
            "result": "",
            "status": "done",
        },
        "beta": {
            "messages": [],
            "result": "",
            "status": "done",
        },
    }

    flow = routes._build_live_flow(raw_msgs, ui_msgs, subagents, registry)
    alpha = next(node for node in flow["nodes"] if node["title"] == "subagent · alpha")
    beta = next(node for node in flow["nodes"] if node["title"] == "subagent · beta")

    assert beta["y"] - alpha["y"] >= 300


def test_live_flow_stacks_multiple_rounds_and_keeps_comm_edges(tmp_path, monkeypatch):
    from cyrene.runtime import inbox
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [])
    monkeypatch.setattr(routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
    asyncio.run(inbox.send_message("alice", "bob", "chat", "Round one message"))

    raw_msgs = [
        {"role": "user", "content": "round one"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "alice", "task": "task a"})}},
            {"id": "call_2", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "bob", "task": "task b"})}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "spawned"},
        {"role": "tool", "tool_call_id": "call_2", "content": "spawned"},
        {"role": "assistant", "content": "round one done"},
        {"role": "user", "content": "round two"},
        {"role": "assistant", "content": "round two done"},
    ]

    flow = routes._build_live_flow(raw_msgs, routes._convert_messages(raw_msgs), [], {})

    round0_main = next(node for node in flow["nodes"] if node["id"] == "r0_n_main")
    round1_main = next(node for node in flow["nodes"] if node["id"] == "r1_n_main")
    comm_edges = [edge for edge in flow["edges"] if edge.get("kind") == "comm"]

    assert round1_main["y"] > round0_main["y"]
    assert any(edge["message"]["body"] == "Round one message" for edge in comm_edges)


def test_live_flow_prunes_extra_user_only_tail_rounds(monkeypatch):
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [])
    raw_msgs = [
        {"role": "user", "content": "round one", "round_id": "round_1"},
        {"role": "assistant", "content": "done one", "round_id": "round_1"},
        {"role": "user", "content": "round two pending", "round_id": "round_2"},
        {"role": "user", "content": "round three pending", "round_id": "round_3"},
    ]

    flow = routes._build_live_flow(raw_msgs, routes._convert_messages(raw_msgs), [], {})
    input_ids = [node["id"] for node in flow["nodes"] if node["kind"] == "input"]

    assert input_ids == ["r0_n_user", "r1_n_user"]
    assert all(not node["id"].startswith("r2_") for node in flow["nodes"])


def test_live_flow_keeps_guided_continuation_inside_same_round(monkeypatch):
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [])
    raw_msgs = [
        {"role": "user", "content": "original task", "round_id": "round_1", "round_title": "round one"},
        {"role": "assistant", "content": "first reply", "round_id": "round_1", "round_title": "round one"},
        {"role": "user", "content": "please adjust the answer", "round_id": "round_1", "round_title": "round one", "queued_guidance_id": "guide_1"},
        {"role": "assistant", "content": "已接受引导。我会按这条新要求调整当前这一轮的工作，并在完成后给你更新。", "round_id": "round_1", "round_title": "round one", "guidance_ack_for_guidance_id": "guide_1"},
        {"role": "assistant", "content": "adjusted final reply", "round_id": "round_1", "round_title": "round one", "in_reply_to_guidance_id": "guide_1"},
    ]

    flow = routes._build_live_flow(raw_msgs, routes._convert_messages(raw_msgs), [], {})
    input_nodes = [node for node in flow["nodes"] if node["kind"] == "input"]
    main_nodes = [node for node in flow["nodes"] if node["kind"] == "main"]
    output_nodes = [node for node in flow["nodes"] if node["kind"] == "output"]

    assert len(input_nodes) == 1
    assert len(main_nodes) == 1
    assert len(output_nodes) == 1
    assert input_nodes[0]["title"] == "round one"
    assert output_nodes[0]["detail"]["content"] == "adjusted final reply"


def test_live_flow_attaches_live_registry_to_latest_substantive_round(monkeypatch):
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [])
    raw_msgs = [
        {"role": "user", "content": "start debate", "round_id": "round_a"},
        {"role": "assistant", "content": "", "round_id": "round_a", "tool_calls": [
            {"id": "call_1", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "alice", "task": "debate"})}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "spawned", "round_id": "round_a"},
        {"role": "assistant", "content": "working", "round_id": "round_a"},
        {"role": "user", "content": "hello", "round_id": "round_b"},
    ]
    registry = {
        "alice": {
            "task": "debate",
            "status": "running",
            "result": "",
            "messages": [{"role": "assistant", "content": "still working"}],
            "created_at": "2026-05-16T04:00:00+00:00",
            "updated_at": "2026-05-16T04:00:10+00:00",
            "round_id": "round_a",
        }
    }
    subagents = [{
        "id": "alice",
        "name": "alice",
        "status": "running",
        "task": "debate",
        "tokens": 1,
        "elapsed": "00:01",
        "progress": 0.45,
        "result": "",
        "messageCount": 1,
        "createdAt": "12:00:00",
        "updatedAt": "12:00:10",
    }]

    flow = routes._build_live_flow(raw_msgs, routes._convert_messages(raw_msgs), subagents, registry)
    alice = next(node for node in flow["nodes"] if node["title"] == "subagent · alice")

    assert alice["id"].startswith("r0_")
    assert all(not node["title"] == "subagent · alice" or node["id"].startswith("r0_") for node in flow["nodes"])


def test_live_flow_filters_comm_edges_by_round_id(tmp_path, monkeypatch):
    from cyrene.runtime import inbox
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [])
    monkeypatch.setattr(routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
    asyncio.run(inbox.send_message("alice", "bob", "chat", "Old round", round_id="round_old"))
    asyncio.run(inbox.send_message("alice", "bob", "chat", "New round", round_id="round_new"))

    raw_msgs = [
        {"role": "user", "content": "old", "round_id": "round_old"},
        {"role": "assistant", "content": "", "round_id": "round_old", "tool_calls": [
            {"id": "old_1", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "alice", "task": "a"})}},
            {"id": "old_2", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "bob", "task": "b"})}},
        ]},
        {"role": "assistant", "content": "done old", "round_id": "round_old"},
        {"role": "user", "content": "new", "round_id": "round_new"},
        {"role": "assistant", "content": "", "round_id": "round_new", "tool_calls": [
            {"id": "new_1", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "alice", "task": "a"})}},
            {"id": "new_2", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "bob", "task": "b"})}},
        ]},
        {"role": "assistant", "content": "done new", "round_id": "round_new"},
    ]

    flow = routes._build_live_flow(raw_msgs, routes._convert_messages(raw_msgs), [], {})
    old_edges = [edge for edge in flow["edges"] if edge.get("kind") == "comm" and edge["from"].startswith("r0_")]
    new_edges = [edge for edge in flow["edges"] if edge.get("kind") == "comm" and edge["from"].startswith("r1_")]

    assert any(edge["message"]["body"] == "Old round" for edge in old_edges)
    assert all(edge["message"]["body"] != "New round" for edge in old_edges)
    assert any(edge["message"]["body"] == "New round" for edge in new_edges)


def test_live_flow_filters_recent_events_to_current_round(monkeypatch):
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [
        {
            "type": "tool_call",
            "caller": "main_agent",
            "tool": "old_search",
            "args": {"query": "old"},
            "result_preview": "old result",
            "round_id": "round_old",
        },
        {
            "type": "tool_call",
            "caller": "main_agent",
            "tool": "new_search",
            "args": {"query": "new"},
            "result_preview": "new result",
            "round_id": "round_new",
        },
    ])
    raw_msgs = [
        {"role": "user", "content": "old", "round_id": "round_old"},
        {"role": "assistant", "content": "done old", "round_id": "round_old"},
        {"role": "user", "content": "new", "round_id": "round_new"},
        {"role": "assistant", "content": "working new", "round_id": "round_new"},
    ]

    flow = routes._build_live_flow(raw_msgs, routes._convert_messages(raw_msgs), [], {})
    round1_tools = [
        node["title"]
        for node in flow["nodes"]
        if node["kind"] == "tool" and node["id"].startswith("r1_")
    ]

    assert "new_search" in round1_tools
    assert "old_search" not in round1_tools


def test_live_flow_does_not_merge_stale_subagent_card_into_new_round(monkeypatch):
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [])
    raw_msgs = [
        {"role": "user", "content": "old", "round_id": "round_old"},
        {"role": "assistant", "content": "", "round_id": "round_old", "tool_calls": [
            {"id": "old_1", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "alice", "task": "old task"})}},
        ]},
        {"role": "tool", "tool_call_id": "old_1", "content": "spawned", "round_id": "round_old"},
        {"role": "assistant", "content": "done old", "round_id": "round_old"},
        {"role": "user", "content": "new", "round_id": "round_new"},
        {"role": "assistant", "content": "", "round_id": "round_new", "tool_calls": [
            {"id": "new_1", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "alice", "task": "new task"})}},
        ]},
        {"role": "tool", "tool_call_id": "new_1", "content": "spawned", "round_id": "round_new"},
        {"role": "assistant", "content": "working new", "round_id": "round_new"},
    ]
    subagents = [{
        "id": "alice",
        "name": "alice",
        "status": "done",
        "task": "old task",
        "roundId": "round_old",
        "tokens": 1,
        "elapsed": "00:02",
        "progress": 1.0,
        "result": "old result",
        "messageCount": 1,
        "createdAt": "12:00:00",
        "updatedAt": "12:00:01",
    }]
    registry = {
        "alice": {
            "task": "old task",
            "status": "done",
            "result": "old result",
            "messages": [{"role": "assistant", "content": "old result"}],
            "round_id": "round_old",
        }
    }

    flow = routes._build_live_flow(raw_msgs, routes._convert_messages(raw_msgs), subagents, registry)
    new_alice = next(
        node for node in flow["nodes"]
        if node["title"] == "subagent · alice" and node["id"].startswith("r1_")
    )

    assert new_alice["detail"]["task"] == "new task"
    assert new_alice["detail"]["result"] == ""


def test_infer_subagent_entries_prefers_latest_spawn_round_over_stale_registry():
    from cyrene.workbench import runtime as routes

    raw_msgs = [
        {"role": "user", "content": "old", "round_id": "round_old"},
        {"role": "assistant", "content": "", "round_id": "round_old", "tool_calls": [
            {"id": "old_1", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "alice", "task": "old task"})}},
        ]},
        {"role": "user", "content": "new", "round_id": "round_new"},
        {"role": "assistant", "content": "", "round_id": "round_new", "tool_calls": [
            {"id": "new_1", "function": {"name": "spawn_subagent", "arguments": json.dumps({"agent_id": "alice", "task": "new task"})}},
        ]},
    ]
    registry = {
        "alice": {
            "task": "old task",
            "status": "done",
            "result": "old result",
            "messages": [{"role": "assistant", "content": "old result"}],
            "round_id": "round_old",
            "created_at": "2026-05-16T04:00:00+00:00",
            "updated_at": "2026-05-16T04:00:10+00:00",
        }
    }

    entries = routes._infer_subagent_entries(raw_msgs, registry)

    assert entries["alice"]["task"] == "new task"
    assert entries["alice"]["round_id"] == "round_new"
    assert entries["alice"]["status"] == "running"
    assert entries["alice"]["result"] == ""
    assert entries["alice"]["messages"] == []


def test_live_flow_uses_registry_when_state_is_empty(monkeypatch):
    from cyrene.observability import debug
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(debug, "get_recent_events", lambda limit=200: [
        {"type": "phase_transition", "detail": "Live subagents spawned"},
    ])
    registry = {
        "alice": {
            "task": "discuss architecture",
            "status": "running",
            "result": "",
            "messages": [{"role": "assistant", "content": "working"}],
            "created_at": "2026-05-15T09:00:00+00:00",
            "updated_at": "2026-05-15T09:00:10+00:00",
            "round_id": "round_live",
        }
    }
    subagents = [{
        "id": "alice",
        "name": "alice",
        "status": "running",
        "task": "discuss architecture",
        "tokens": 1,
        "elapsed": "00:01",
        "progress": 0.45,
        "result": "",
        "messageCount": 1,
        "createdAt": "17:00:00",
        "updatedAt": "17:00:10",
    }]

    flow = routes._build_live_flow([], [], subagents, registry)

    assert any(node["kind"] == "main" for node in flow["nodes"])
    assert any(node["title"] == "subagent · alice" for node in flow["nodes"])


def test_convert_messages_keeps_assistant_entries_with_thinking_or_tools():
    from cyrene.workbench import runtime as routes

    raw_msgs = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "", "reasoning_content": "thinking"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "spawn_subagent", "arguments": "{}"}}
        ]},
    ]

    ui_msgs = routes._convert_messages(raw_msgs)

    assert len(ui_msgs) == 2
    assert ui_msgs[1]["role"] == "agent"
    assert ui_msgs[1]["thinking"] == "thinking"
    assert ui_msgs[1]["tools"][0]["name"] == "spawn_subagent"


def test_convert_messages_merges_adjacent_trace_only_assistant_entries():
    from cyrene.workbench import runtime as routes

    raw_msgs = [
        {"role": "assistant", "content": "", "reasoning_content": "first pass", "round_id": "round_1"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "search", "arguments": "{}"}}
        ], "round_id": "round_1"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t2", "function": {"name": "fetch", "arguments": "{}"}}
        ], "round_id": "round_1"},
    ]

    ui_msgs = routes._convert_messages(raw_msgs)

    assert len(ui_msgs) == 1
    assert ui_msgs[0]["thinking"] == "first pass"
    assert [tool["name"] for tool in ui_msgs[0]["tools"]] == ["search", "fetch"]


def test_convert_messages_merges_adjacent_trace_only_entries_with_same_client_request_id():
    from cyrene.workbench import runtime as routes

    raw_msgs = [
        {"role": "assistant", "content": "", "reasoning_content": "first", "round_id": "round_1", "client_request_id": "req_1"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "search", "arguments": "{}"}}
        ], "round_id": "round_1", "client_request_id": "req_1"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t2", "function": {"name": "fetch", "arguments": "{}"}}
        ], "round_id": "round_1", "client_request_id": "req_1"},
    ]

    ui_msgs = routes._convert_messages(raw_msgs)

    assert len(ui_msgs) == 1
    assert ui_msgs[0]["clientRequestId"] == "req_1"
    assert ui_msgs[0]["thinking"] == "first"
    assert [tool["name"] for tool in ui_msgs[0]["tools"]] == ["search", "fetch"]


def test_convert_messages_merges_trace_only_assistant_into_following_body_reply():
    from cyrene.workbench import runtime as routes

    raw_msgs = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "thinking before tool",
            "tool_calls": [{"id": "t1", "function": {"name": "search", "arguments": "{}"}}],
            "round_id": "round_1",
        },
        {
            "role": "assistant",
            "content": "final answer",
            "reasoning_content": "thinking after tool",
            "round_id": "round_1",
            "client_request_id": "req_1",
        },
    ]

    ui_msgs = routes._convert_messages(raw_msgs)

    assert len(ui_msgs) == 1
    assert ui_msgs[0]["body"] == "final answer"
    assert ui_msgs[0]["clientRequestId"] == "req_1"
    assert ui_msgs[0]["thinking"] == "thinking before tool\n\nthinking after tool"
    assert [tool["name"] for tool in ui_msgs[0]["tools"]] == ["search"]


def test_convert_messages_collapses_consecutive_duplicate_user_messages():
    from cyrene.workbench import runtime as routes

    raw_msgs = [
        {"role": "user", "content": "介绍你自己和你能做的事", "round_id": "round_1", "client_request_id": "req_1"},
        {"role": "user", "content": "介绍你自己和你能做的事", "round_id": "round_2", "client_request_id": "req_2"},
        {"role": "assistant", "content": "ok", "round_id": "round_2", "client_request_id": "req_2"},
    ]

    ui_msgs = routes._convert_messages(raw_msgs)

    assert len(ui_msgs) == 2
    assert ui_msgs[0]["role"] == "user"
    assert ui_msgs[0]["clientRequestId"] == "req_2"
    assert ui_msgs[1]["role"] == "agent"


def test_convert_messages_dedupes_repeated_message_ids_even_when_not_adjacent():
    from cyrene.workbench import runtime as routes

    raw_msgs = [
        {"role": "user", "content": "same prompt", "message_id": "u1", "round_id": "round_1", "client_request_id": "req_1"},
        {"role": "assistant", "content": "reply", "message_id": "a1", "round_id": "round_1", "client_request_id": "req_1"},
        {"role": "user", "content": "same prompt", "message_id": "u1", "round_id": "round_1", "client_request_id": "req_1"},
    ]

    ui_msgs = routes._convert_messages(raw_msgs)

    assert len(ui_msgs) == 2
    assert [msg["messageId"] for msg in ui_msgs] == ["u1", "a1"]


def test_convert_messages_collapses_repeated_user_bodies_within_one_user_block():
    from cyrene.workbench import runtime as routes

    raw_msgs = [
        {"role": "user", "content": "check", "message_id": "u1"},
        {"role": "user", "content": "现在先看看多伦多的天气", "message_id": "u2"},
        {"role": "user", "content": "check", "message_id": "u3"},
        {"role": "user", "content": "现在先看看多伦多的天气", "message_id": "u4"},
        {"role": "assistant", "content": "ok", "message_id": "a1"},
    ]

    ui_msgs = routes._convert_messages(raw_msgs)

    assert len(ui_msgs) == 3
    assert [msg["messageId"] for msg in ui_msgs] == ["u3", "u4", "a1"]
    assert [msg["body"] for msg in ui_msgs[:2]] == ["check", "现在先看看多伦多的天气"]


def test_convert_messages_marks_intermediate_replies():
    from cyrene.workbench import runtime as routes

    raw_msgs = [
        {
            "role": "assistant",
            "content": "先汇报一个阶段性结论",
            "round_id": "round_1",
            "client_request_id": "req_1",
            "message_id": "a_mid",
            "intermediate_reply": True,
        },
        {
            "role": "assistant",
            "content": "最终答复",
            "round_id": "round_1",
            "client_request_id": "req_1",
            "message_id": "a_final",
        },
    ]

    ui_msgs = routes._convert_messages(raw_msgs)

    assert ui_msgs[0]["messageId"] == "a_mid"
    assert ui_msgs[0]["intermediateReply"] is True
    assert ui_msgs[1]["messageId"] == "a_final"
    assert "intermediateReply" not in ui_msgs[1]


async def test_run_chat_agent_returns_main_agent_text_directly(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.agent import agent as _agent_core

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_context", lambda max_chars=5000: "")

    async def fake_run_main_agent(user_message, history, bot, chat_id, db_path, system_prompt="", client_request_id="", persist_user_message=True, lang="", **kwargs):
        round_id = agent._current_round_id.get()
        await agent._save_session_messages([
            {"role": "user", "content": user_message, "round_id": round_id},
            {"role": "assistant", "content": "raw final", "round_id": round_id},
        ])
        return "raw final"

    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)

    result = await agent._run_chat_agent("hi", None, 0, "db.sqlite3")
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert result == "raw final"
    assert saved[-1]["role"] == "assistant"
    assert saved[-1]["content"] == "raw final"


async def test_run_chat_agent_returns_main_text_when_internal_trace_has_no_final_message(monkeypatch, tmp_path):
    from cyrene import agent
    from cyrene.agent import session as _agent_session
    from cyrene.agent import agent as _agent_core

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(_agent_session, "_refresh_session_labels", AsyncMock())
    monkeypatch.setattr(agent, "get_context", lambda max_chars=5000: "")

    async def fake_run_main_agent(user_message, history, bot, chat_id, db_path, system_prompt="", client_request_id="", persist_user_message=True, lang="", **kwargs):
        round_id = agent._current_round_id.get()
        await agent._save_session_messages([
            {"role": "user", "content": user_message, "round_id": round_id},
            {
                "role": "assistant",
                "content": "",
                "round_id": round_id,
                "tool_calls": [{"id": "s1", "function": {"name": "spawn_subagent", "arguments": "{}"}}],
            },
        ])
        return "[Sub-agents are still working in the background. You can continue the conversation.]"

    monkeypatch.setattr(_agent_core, "_run_main_agent", fake_run_main_agent)

    result = await agent._run_chat_agent("keep going", None, 0, "db.sqlite3")
    saved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))["messages"]

    assert result.startswith("[Sub-agents are still working in the background.")
    assert saved[-1]["role"] == "assistant"
    assert saved[-1]["content"] == ""
    assert "tool_calls" in saved[-1]


async def test_tool_bash_returns_early_when_interrupted(monkeypatch):
    from cyrene.agent import state as _agent_state
    from cyrene.tool_impl.core import bash as tools

    interrupt_event = asyncio.Event()
    monkeypatch.setattr(_agent_state, "_interrupt_event", interrupt_event)

    task = asyncio.create_task(tools._tool_bash(
        {"command": "sleep 30", "timeout_ms": 60000},
        None,
        0,
        "db.sqlite3",
        None,
    ))
    await asyncio.sleep(0.2)
    interrupt_event.set()

    payload = json.loads(await task)

    assert payload["exit_code"] == -1
    assert "interrupted" in payload["stderr"].lower()


async def test_interrupt_active_run_clears_after_locked_run_finishes():
    from cyrene import agent

    agent._interrupt_event.clear()
    locked = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock():
        async with agent._agent_lock:
            locked.set()
            await release.wait()

    task = asyncio.create_task(hold_lock())
    await locked.wait()

    assert agent.interrupt_active_run() is True
    assert agent._interrupt_event.is_set() is True

    release.set()
    await task
    await asyncio.sleep(0.1)

    assert agent._interrupt_event.is_set() is False


async def test_interrupt_active_run_cancels_active_session_task(monkeypatch):
    from cyrene import agent
    from cyrene.agent import coordinator as _agent_coordinator
    from cyrene.agent import state as _agent_state

    session_id = "interrupt_active_task_test"
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_run_chat_agent(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(_agent_coordinator, "_run_chat_agent", fake_run_chat_agent)

    task = asyncio.create_task(agent.run_agent("hi", None, 0, "db.sqlite3", session_id=session_id))
    await started.wait()

    assert agent.is_session_running(session_id) is True
    assert agent.interrupt_active_run(session_id=session_id) is True

    await asyncio.wait_for(cancelled.wait(), timeout=1)
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("run_agent task should have been cancelled")

    await asyncio.sleep(0.1)
    ctx = _agent_state._ensure_session(session_id)
    assert ctx.active_task is None
    assert agent.is_session_running(session_id) is False


async def test_run_agent_clears_stale_interrupt_before_starting(monkeypatch):
    from cyrene import agent
    from cyrene.agent import coordinator as _agent_coordinator

    seen = {}

    async def fake_run_chat_agent(user_message, bot, chat_id, db_path, **kwargs):
        seen["interrupt_before_start"] = agent._interrupt_event.is_set()
        return "ok"

    monkeypatch.setattr(_agent_coordinator, "_run_chat_agent", fake_run_chat_agent)
    agent._interrupt_event.set()

    result = await agent.run_agent("hi", None, 0, "db.sqlite3")

    assert result == "ok"
    assert seen["interrupt_before_start"] is False


async def test_run_main_agent_summarizes_and_cancels_subagents_when_monitoring_is_interrupted(monkeypatch):
    # New behavior (主代理活动检测): when the user interrupts while the main agent
    # is monitoring subagents, the agent cancels the running subagents immediately
    # and proceeds to the summary phase (it no longer returns an early "still working
    # in the background" notice — that path was removed).
    from cyrene import agent
    from cyrene.agent import state as _agent_state
    from cyrene.runtime import inbox
    from cyrene import subagent

    # The monitoring loop waits on the module-level ``_interrupt_event`` (a from-import
    # of the shared state object), so we signal that *same* shared event rather than
    # monkeypatching a fresh one onto the state module — a replacement object would
    # never reach agent.py's binding. The autouse conftest fixture clears this event
    # before each test, so using it is isolated.

    responses = iter([
        {
            "content": "",
            "tool_calls": [{"id": "u1", "function": {"name": "use_tools", "arguments": '{"task":"check"}'}}],
        },
        {
            "content": "",
            "tool_calls": [{"id": "s1", "function": {"name": "spawn_subagent", "arguments": '{"agent_id":"alice","task":"research"}'}}],
        },
    ])
    saved = []
    cancelled = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        return next(responses)

    async def fake_execute_tool(name, args, bot, chat_id, db_path, notify_state):
        return "spawned"

    async def fake_save(messages, **_kwargs):
        saved.append(messages)

    async def fake_snapshot(round_id=None):
        return {"alice": {"status": "running", "task": "research"}}

    async def fake_cancel_subagent_tasks(round_id=None):
        cancelled.append(round_id)

    async def fake_summary(**kwargs):
        return "summary done"

    async def fake_flow_snapshot(round_id=None):
        return {}

    _patch_call_llm(monkeypatch, fake_call_llm)
    _patch_execute_tool(monkeypatch, fake_execute_tool)
    _patch_save_session(monkeypatch, fake_save)
    monkeypatch.setattr(subagent, "get_snapshot", fake_snapshot)
    monkeypatch.setattr(subagent, "cancel_subagent_tasks", fake_cancel_subagent_tasks)
    monkeypatch.setattr(subagent, "run_summary_subagent", fake_summary)
    monkeypatch.setattr(subagent, "build_flow_snapshot", fake_flow_snapshot)
    monkeypatch.setattr(subagent, "clear", lambda round_id=None: asyncio.sleep(0))
    monkeypatch.setattr(subagent, "get_raw_messages", lambda aid: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(subagent, "reactivate", lambda aid: asyncio.sleep(0, result=False))
    monkeypatch.setattr(inbox, "get_unread_count", lambda aid: 0)

    task = asyncio.create_task(agent._run_main_agent("check", [], None, 0, "db.sqlite3"))
    await asyncio.sleep(0.1)
    _agent_state._interrupt_event.set()
    result = await task

    # Interrupt -> running subagents cancelled, session persisted, summary returned.
    assert result == "summary done"
    assert cancelled, "interrupt should cancel running subagents"
    assert saved


async def test_run_main_agent_retries_invalid_phase1_tool_and_returns_model_explanation(monkeypatch):
    from cyrene import agent
    from cyrene.agent import state as _agent_state

    calls = []
    responses = iter([
        {
            "content": "好的，先看天气。",
            "tool_calls": [
                {"id": "w1", "function": {"name": "UnavailableTool", "arguments": '{"query":"Toronto weather today"}'}},
            ],
        },
        {
            "content": "当前阶段没有合适工具，请改用 use_tools 进入完整工具阶段。",
            "tool_calls": [],
        },
    ])
    saved = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000):
        calls.append(tools)
        return next(responses)

    async def fake_save(messages, **_kwargs):
        saved.append(messages)

    _patch_call_llm(monkeypatch, fake_call_llm)
    _patch_save_session(monkeypatch, fake_save)

    result = await agent._run_main_agent("现在先看看多伦多的天气", [], None, 0, "db.sqlite3")

    assert result == "当前阶段没有合适工具，请改用 use_tools 进入完整工具阶段。"
    assert calls[0] is calls[1]
    tool_names = {item.get("function", {}).get("name") for item in calls[0]}
    assert {"use_tools", "ask_user", "quit", "WebSearch"} <= tool_names
    assert calls[0] is not _agent_state._LIGHT_TOOL_DEFS
    assert saved


async def test_refresh_session_labels_is_a_compatibility_noop(monkeypatch, tmp_path):
    from cyrene import agent

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)

    await agent._save_session_messages([
        {"role": "user", "content": "讨论加密货币辩论结构", "round_id": "round_1"},
        {"role": "assistant", "content": "ok", "round_id": "round_1"},
    ])

    hidden_namer = AsyncMock()
    _patch_call_llm(monkeypatch, hidden_namer)

    await agent._refresh_session_labels("讨论加密货币辩论结构", "round_1")
    state = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))
    labels = agent.get_session_labels()

    hidden_namer.assert_not_awaited()
    assert "session_title" not in state
    assert all(
        "round_title" not in msg
        for msg in state["messages"]
        if msg.get("round_id") == "round_1"
    )
    assert labels["round_title"] == ""
    assert labels["session_title"] == ""

    await agent._save_session_messages(state["messages"])
    preserved = json.loads(agent.STATE_FILE.read_text(encoding="utf-8"))
    assert "session_title" not in preserved


def test_build_current_session_uses_saved_session_and_round_titles(tmp_path, monkeypatch):
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(routes, "DATA_DIR", tmp_path)

    routes.STATE_FILE.write_text(
        json.dumps({
            "session_title": "加密货币多代理讨论",
            "messages": [
                {"role": "user", "content": "讨论加密货币辩论结构", "round_id": "round_1", "round_title": "加密货币辩论"},
                {"role": "assistant", "content": "ok", "round_id": "round_1", "round_title": "加密货币辩论"},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    session = routes._build_current_session()
    user_node = next(node for node in session["flow"]["nodes"] if node["kind"] == "input")

    assert session["title"] == "加密货币多代理讨论"
    assert session["currentRoundId"] == "round_1"
    assert session["currentRoundTitle"] == "加密货币辩论"
    assert user_node["title"] == "加密货币辩论"


def test_build_current_session_uses_latest_round_id_for_chat_sidebar(tmp_path, monkeypatch):
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(routes, "DATA_DIR", tmp_path)

    routes.STATE_FILE.write_text(
        json.dumps({
            "messages": [
                {"role": "user", "content": "first round", "round_id": "round_1", "round_title": "第一轮"},
                {"role": "assistant", "content": "done", "round_id": "round_1", "round_title": "第一轮"},
                {"role": "user", "content": "second round", "round_id": "round_2", "round_title": "第二轮"},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    session = routes._build_current_session()

    assert session["currentRoundId"] == "round_2"
    assert session["currentRoundTitle"] == "第二轮"


def test_build_archive_sessions_reads_titles_and_splits_rounds(tmp_path, monkeypatch):
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "CONVERSATIONS_DIR", tmp_path / "conversations")
    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    routes.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    routes.STATE_FILE.write_text('{"messages":[]}', encoding="utf-8")

    date_str = "2026-05-15"
    (routes.CONVERSATIONS_DIR / f"{date_str}.md").write_text(
        "# Conversations - 2026-05-15\n\n"
        "<!-- session_title: 加密货币多代理讨论 -->\n\n"
        "## 08:00:00 UTC\n\n"
        "<!-- round_id: round_a -->\n"
        "<!-- round_title: 设计辩论角色 -->\n\n"
        "**User**: 先设计角色\n\n"
        "**Ape**: 好\n\n"
        "---\n\n"
        "## 08:05:00 UTC\n\n"
        "<!-- round_id: round_b -->\n"
        "<!-- round_title: 让双方开始辩论 -->\n\n"
        "**User**: 现在开始辩论\n\n"
        "**Ape**: 开始\n\n"
        "---\n",
        encoding="utf-8",
    )

    sessions = routes._build_archive_sessions()
    flow = sessions[0]["flow"]
    input_titles = [node["title"] for node in flow["nodes"] if node["kind"] == "input"]

    assert sessions[0]["title"] == "加密货币多代理讨论"
    assert input_titles == ["设计辩论角色", "让双方开始辩论"]


def test_build_archive_sessions_splits_multiple_same_day_sessions_by_archive_session_id(tmp_path, monkeypatch):
    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(routes, "CONVERSATIONS_DIR", tmp_path / "conversations")
    monkeypatch.setattr(routes, "STATE_FILE", tmp_path / "state.json")
    routes.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    routes.STATE_FILE.write_text('{"messages":[]}', encoding="utf-8")

    date_str = "2026-05-15"
    (routes.CONVERSATIONS_DIR / f"{date_str}.md").write_text(
        "# Conversations - 2026-05-15\n\n"
        "## 08:00:00 UTC\n\n"
        "<!-- archive_session_id: session_alpha -->\n"
        "<!-- session_title: 第一场 -->\n"
        "<!-- round_id: round_a -->\n"
        "<!-- round_title: 设计角色 -->\n\n"
        "**User**: 第一场开始\n\n"
        "**Ape**: 好\n\n"
        "---\n\n"
        "## 08:05:00 UTC\n\n"
        "<!-- archive_session_id: session_alpha -->\n"
        "<!-- session_title: 第一场 -->\n"
        "<!-- round_id: round_b -->\n"
        "<!-- round_title: 继续讨论 -->\n\n"
        "**User**: 第一场继续\n\n"
        "**Ape**: 继续\n\n"
        "---\n\n"
        "## 09:00:00 UTC\n\n"
        "<!-- archive_session_id: session_beta -->\n"
        "<!-- session_title: 第二场 -->\n"
        "<!-- round_id: round_c -->\n"
        "<!-- round_title: 新话题 -->\n\n"
        "**User**: 第二场开始\n\n"
        "**Ape**: 开始\n\n"
        "---\n",
        encoding="utf-8",
    )

    sessions = routes._build_archive_sessions()

    assert [session["title"] for session in sessions] == ["第二场", "第一场"]
    assert sessions[0]["id"] == "archive_2026-05-15_session_beta"
    assert sessions[0]["chat"]["messages"][0]["body"] == "第二场开始"
    assert [node["title"] for node in sessions[1]["flow"]["nodes"] if node["kind"] == "input"] == ["设计角色", "继续讨论"]


async def _drain_ndjson(response):
    """Iterate a StreamingResponse body and parse each NDJSON line."""
    parsed = []
    async for chunk in response.body_iterator:
        text = chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            line = line.strip()
            if line:
                parsed.append(json.loads(line))
    return parsed


async def test_stream_agent_reply_surfaces_model_failure_instead_of_done(monkeypatch):
    """Issue #7: a failing agent run (model timeout/5xx/network) must surface an
    `error` event and publish session status `error` — never get swallowed into a
    silent `done` (the symptom users hit as "模型失败只回 done")."""
    from cyrene.workbench import runtime as routes
    from cyrene.observability import debug

    events = []

    async def fake_publish_event(event):
        events.append(event)

    monkeypatch.setattr(debug, "publish_event", fake_publish_event)

    async def failing_run():
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        raise httpx.ConnectError("upstream unreachable", request=request)

    response = routes._stream_agent_reply(lambda: failing_run(), "hello")
    lines = await _drain_ndjson(response)

    error_lines = [ln for ln in lines if ln.get("type") == "error"]
    assert error_lines, f"expected an error event, got {lines}"
    assert error_lines[0]["error"] == "model_call_failed"
    assert error_lines[0]["message"]
    assert not any(ln.get("type") == "reply_done" for ln in lines)

    statuses = [e.get("status") for e in events if e.get("type") == "session_update"]
    assert "error" in statuses, statuses
    assert "done" not in statuses, statuses


async def test_stream_agent_reply_still_publishes_done_on_success(monkeypatch):
    """Regression guard for the issue-#7 finally gating: a successful run must
    still stream the reply and publish session status `done` (and no `error`)."""
    from cyrene.workbench import runtime as routes
    from cyrene.observability import debug

    events = []

    async def fake_publish_event(event):
        events.append(event)

    async def fake_archive_exchange(*args, **kwargs):
        return None

    monkeypatch.setattr(debug, "publish_event", fake_publish_event)
    monkeypatch.setattr(routes, "archive_exchange", fake_archive_exchange)
    monkeypatch.setattr(routes, "get_session_labels", lambda *a, **k: {})

    async def ok_run():
        return "hi there"

    response = routes._stream_agent_reply(lambda: ok_run(), "hello")
    lines = await _drain_ndjson(response)

    done_lines = [ln for ln in lines if ln.get("type") == "reply_done"]
    assert done_lines and done_lines[0]["response"] == "hi there"
    assert not any(ln.get("type") == "error" for ln in lines)

    statuses = [e.get("status") for e in events if e.get("type") == "session_update"]
    assert "done" in statuses, statuses
    assert "error" not in statuses, statuses


# ---------------------------------------------------------------------------
# Issue #38 — Credential isolation between model providers
# ---------------------------------------------------------------------------


def test_normalized_candidate_same_provider_inherits_key():
    """Same base_url → api_key inherited from active provider."""
    from cyrene.call_llm import _normalized_candidate, DEFAULT_OPENAI_BASE_URL

    result = _normalized_candidate(
        {"model": "some-model"},
        active_model="primary",
        active_base_url=DEFAULT_OPENAI_BASE_URL,
        active_api_key="primary-secret",
    )
    assert result["api_key"] == "primary-secret"


def test_normalized_candidate_cross_provider_no_key_not_inherited():
    """Different base_url without explicit api_key → empty string, not inherited."""
    from cyrene.call_llm import _normalized_candidate, DEFAULT_OPENAI_BASE_URL

    result = _normalized_candidate(
        {"model": "other-model", "base_url": "https://other-provider.example/v1"},
        active_model="primary",
        active_base_url=DEFAULT_OPENAI_BASE_URL,
        active_api_key="primary-secret",
    )
    assert result["api_key"] == ""


def test_normalized_candidate_cross_provider_explicit_key_used():
    """Different base_url with explicit api_key → that key is used, not the active one."""
    from cyrene.call_llm import _normalized_candidate, DEFAULT_OPENAI_BASE_URL

    result = _normalized_candidate(
        {"model": "other-model", "base_url": "https://other-provider.example/v1", "api_key": "other-secret"},
        active_model="primary",
        active_base_url=DEFAULT_OPENAI_BASE_URL,
        active_api_key="primary-secret",
    )
    assert result["api_key"] == "other-secret"


def test_resolve_secondary_candidates_cross_provider_no_key_not_inherited(monkeypatch):
    """Secondary model on different base_url without api_key must not inherit OPENAI_API_KEY."""
    from cyrene import call_llm
    from cyrene.call_llm import DEFAULT_OPENAI_BASE_URL

    monkeypatch.setattr(call_llm, "get_secondary_model", lambda: {
        "model": "secondary-model",
        "base_url": "https://secondary-provider.example/v1",
    })
    monkeypatch.setenv("OPENAI_API_KEY", "primary-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)

    candidates = call_llm._resolve_secondary_candidates()
    assert candidates
    assert candidates[0]["api_key"] == ""


def test_resolve_secondary_candidates_same_provider_inherits_key(monkeypatch):
    """Secondary model on same base_url without api_key inherits OPENAI_API_KEY."""
    from cyrene import call_llm
    from cyrene.call_llm import DEFAULT_OPENAI_BASE_URL

    monkeypatch.setattr(call_llm, "get_secondary_model", lambda: {
        "model": "secondary-model",
        "base_url": DEFAULT_OPENAI_BASE_URL,
    })
    monkeypatch.setenv("OPENAI_API_KEY", "primary-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)

    candidates = call_llm._resolve_secondary_candidates()
    assert candidates
    assert candidates[0]["api_key"] == "primary-secret"


def test_resolve_vision_candidates_cross_provider_no_key_not_inherited(monkeypatch):
    """Vision-only model on different base_url without api_key must not inherit OPENAI_API_KEY."""
    from cyrene import call_llm
    from cyrene.call_llm import DEFAULT_OPENAI_BASE_URL

    monkeypatch.setattr(call_llm, "get_models", lambda: [])
    monkeypatch.setattr(call_llm, "get_vision_models", lambda: [
        {"model": "vision-model", "base_url": "https://vision-provider.example/v1"},
    ])
    monkeypatch.setenv("OPENAI_API_KEY", "primary-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)
    monkeypatch.setenv("OPENAI_MODEL", "primary-model")

    candidates = call_llm._resolve_vision_candidates()
    vision = next((c for c in candidates if c.get("model") == "vision-model"), None)
    assert vision is not None
    assert vision["api_key"] == ""


async def test_streamed_chat_only_final_reply_persists_usage(monkeypatch, tmp_path):
    """Chat-only streaming should persist the usage from the single phase-1 call."""
    from cyrene import agent
    from cyrene.agent import state as _agent_state

    _patch_state_file(monkeypatch, tmp_path / "state.json")
    _patch_data_dir(monkeypatch, tmp_path)

    async def fake_phase1(messages, tools=None, max_tokens=32000, **kwargs):
        return {"content": "plain phase1 text", "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}

    fake_stream = AsyncMock()

    saved: dict[str, Any] = {}

    async def fake_save(messages, **_kwargs):
        saved["messages"] = messages

    _patch_call_llm(monkeypatch, fake_phase1)
    _patch_call_llm_stream(monkeypatch, fake_stream)
    _patch_save_session(monkeypatch, fake_save)
    _patch_append_session(monkeypatch, AsyncMock())

    async def noop_writer(event):
        return None

    token = _agent_state._reply_stream_writer.set(noop_writer)
    try:
        result = await agent._run_main_agent("hello", [], None, 0, "db.sqlite3")
    finally:
        _agent_state._reply_stream_writer.reset(token)

    assert result == "plain phase1 text"
    fake_stream.assert_not_awaited()
    final_entries = [
        message for message in saved["messages"]
        if message.get("role") == "assistant" and message.get("content") == "plain phase1 text"
    ]
    assert final_entries, "streamed final reply should be persisted"
    assert final_entries[-1].get("usage", {}).get("total_tokens") == 12


def test_recent_main_agent_activity_ignores_completed_accounting_events_and_terminal_phases():
    from datetime import datetime, timedelta, timezone

    from cyrene.workbench.session_view import has_recent_main_agent_activity

    now = datetime.now(timezone.utc)

    def stamp(offset):
        return (now + timedelta(seconds=offset)).isoformat()

    assert has_recent_main_agent_activity([
        {"type": "session_update", "status": "running", "timestamp": stamp(-4)},
        {"type": "llm_call", "caller": "main_agent", "status": "completed", "timestamp": stamp(-3)},
        {"type": "phase_transition", "to": "done", "timestamp": stamp(-2)},
    ], now) is False

    assert has_recent_main_agent_activity([
        {"type": "tool_call_started", "caller": "main_agent", "tool_call_id": "a", "timestamp": stamp(-4)},
        {"type": "tool_call_started", "caller": "main_agent", "tool_call_id": "b", "timestamp": stamp(-3)},
        {"type": "tool_call_finished", "caller": "main_agent", "tool_call_id": "a", "timestamp": stamp(-2)},
    ], now) is True
