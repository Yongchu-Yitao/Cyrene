import json
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("PIL", MagicMock())
sys.modules["PIL"].Image = MagicMock()
sys.modules.setdefault("pypdf", MagicMock())

from cyrene import agent
import cyrene.agent.state as agent_state
from cyrene.agent import session as agent_session
from cyrene.call_llm import _message_token_estimate


def test_dedupe_messages_by_id_keeps_latest_occurrence_in_original_position() -> None:
    messages = [
        {"role": "user", "message_id": "msg_1", "content": "hello"},
        {"role": "assistant", "message_id": "msg_2", "content": "world"},
        {"role": "user", "message_id": "msg_1", "content": "hello updated", "round_title": "latest"},
    ]

    deduped = agent._dedupe_messages_by_id(messages)

    assert deduped == [
        {"role": "user", "message_id": "msg_1", "content": "hello updated", "round_title": "latest"},
        {"role": "assistant", "message_id": "msg_2", "content": "world"},
    ]


def test_user_forced_compaction_folds_entire_conversation_below_automatic_threshold() -> None:
    messages = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": ("context line %d " % index) * 8,
        }
        for index in range(12)
    ]
    total = sum(_message_token_estimate(message) for message in messages)
    ctx_limit = total * 2

    automatic = agent_session._compact_messages_for_storage(
        messages,
        ctx_limit=ctx_limit,
    )
    forced = agent_session._compact_messages_for_storage(
        messages,
        ctx_limit=ctx_limit,
        force=True,
    )

    assert total < int(ctx_limit * agent_session._COMPACT_TRIGGER_RATIO)
    assert automatic is messages
    assert len(forced) == 1
    assert forced[0]["compacted_block"] is True
    assert "context line 0" in forced[0]["content"]
    assert "context line 11" in forced[0]["content"]


def test_user_forced_compaction_merges_existing_blocks_and_live_messages() -> None:
    messages = [
        {
            "role": "system",
            "content": "[Compacted earlier context]\nOld durable decision",
            "compacted_block": True,
            "message_id": "msg_old_block",
        },
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "new answer"},
    ]

    compacted = agent_session._compact_messages_for_storage(
        messages,
        ctx_limit=100_000,
        force=True,
    )

    assert len(compacted) == 1
    assert compacted[0]["compacted_block"] is True
    assert compacted[0]["content"].count("[Compacted earlier context]") == 1
    assert "Old durable decision" in compacted[0]["content"]
    assert "User: new question" in compacted[0]["content"]
    assert "new answer" in compacted[0]["content"]


def test_user_forced_compaction_works_without_known_context_window() -> None:
    messages = [
        {"role": "user", "content": "short question"},
        {"role": "assistant", "content": "short answer"},
    ]

    compacted = agent_session._compact_messages_for_storage(
        messages,
        ctx_limit=0,
        force=True,
    )

    assert len(compacted) == 1
    assert compacted[0]["compacted_block"] is True
    assert "short question" in compacted[0]["content"]
    assert "short answer" in compacted[0]["content"]


def test_user_forced_compaction_replaces_tool_only_history_with_marker() -> None:
    messages = [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "large tool result " * 30,
        },
    ]

    compacted = agent_session._compact_messages_for_storage(
        messages,
        ctx_limit=100_000,
        force=True,
    )

    assert len(compacted) == 1
    assert compacted[0]["compacted_block"] is True
    assert "only tool results" in compacted[0]["content"]


@pytest.mark.asyncio
async def test_user_forced_compaction_rejects_running_session(monkeypatch) -> None:
    lock = asyncio.Lock()
    await lock.acquire()
    ctx = SimpleNamespace(lock=lock, session_state_lock=asyncio.Lock(), pending_distill_task=None)
    monkeypatch.setattr(agent_session, "_ensure_session", lambda session_id="": ctx)

    result = await agent_session.compact_session_if_needed("chat-test", force=True)

    assert result == {"compacted": False, "reason": "running"}
    lock.release()


@pytest.mark.asyncio
async def test_user_forced_compaction_rejects_pending_question(monkeypatch) -> None:
    ctx = SimpleNamespace(lock=asyncio.Lock(), session_state_lock=asyncio.Lock(), pending_distill_task=None)
    monkeypatch.setattr(agent_session, "_ensure_session", lambda session_id="": ctx)
    monkeypatch.setattr(
        agent_session,
        "_load_session_state",
        lambda: {
            "pending_question": {"id": "question_1"},
            "messages": [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "Read", "arguments": "{}"}}],
                },
            ],
        },
    )

    result = await agent_session.compact_session_if_needed("chat-test", force=True)

    assert result == {"compacted": False, "reason": "awaiting_user"}


@pytest.mark.asyncio
async def test_user_forced_compaction_requires_tool_activity(monkeypatch) -> None:
    ctx = SimpleNamespace(lock=asyncio.Lock(), session_state_lock=asyncio.Lock(), pending_distill_task=None)
    monkeypatch.setattr(agent_session, "_ensure_session", lambda session_id="": ctx)
    monkeypatch.setattr(
        agent_session,
        "_load_session_state",
        lambda: {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        },
    )

    result = await agent_session.compact_session_if_needed("chat-test", force=True)

    assert result == {"compacted": False, "reason": "no_tool_activity"}


@pytest.mark.asyncio
async def test_user_forced_compaction_rejects_active_distillation(monkeypatch) -> None:
    pending = asyncio.get_running_loop().create_future()
    ctx = SimpleNamespace(
        lock=asyncio.Lock(),
        session_state_lock=asyncio.Lock(),
        pending_distill_task=pending,
    )
    monkeypatch.setattr(agent_session, "_ensure_session", lambda session_id="": ctx)

    result = await agent_session.compact_session_if_needed("chat-test", force=True)

    assert result == {"compacted": False, "reason": "distilling"}
    pending.cancel()


def test_unknown_context_window_does_not_trim_message_count() -> None:
    messages = [
        {"role": "user", "content": f"message {index}"}
        for index in range(80)
    ]

    compacted = agent_session._compact_messages_for_storage(messages, ctx_limit=0)

    assert compacted is messages
    assert len(compacted) == 80


def test_compaction_drops_tool_only_old_prefix() -> None:
    messages = [
        {
            "role": "tool",
            "tool_call_id": f"call_{index}",
            "content": "large tool result " * 30,
        }
        for index in range(4)
    ]
    messages.append({"role": "user", "content": "keep this recent message"})

    compacted = agent_session._compact_messages_for_storage(messages, ctx_limit=200)

    assert compacted == [{"role": "user", "content": "keep this recent message"}]


@pytest.mark.asyncio
async def test_memory_compression_uses_latest_message_window(monkeypatch) -> None:
    captured_prompt = ""

    async def fake_call_llm(messages, tools=None, **kwargs):
        nonlocal captured_prompt
        captured_prompt = messages[-1]["content"]
        return {"content": ""}

    monkeypatch.setattr(agent_session, "_call_llm", fake_call_llm)
    messages = [
        {"role": "user", "content": f"marker-{index}"}
        for index in range(60)
    ]

    await agent_session._compress_old_messages(messages)

    assert "marker-40" in captured_prompt
    assert "marker-59" in captured_prompt
    assert "marker-39" not in captured_prompt
    assert "marker-0" not in captured_prompt


@pytest.mark.asyncio
async def test_memory_compression_skips_workbench_sessions(monkeypatch) -> None:
    from cyrene import workbench_context

    async def unexpected_call(*args, **kwargs):
        raise AssertionError("Workbench sessions must use project memory capture")

    monkeypatch.setattr(agent_session, "_call_llm", unexpected_call)
    monkeypatch.setattr(
        workbench_context,
        "resolve_workbench_project_id_for_session",
        lambda session_id: "project-test" if session_id == "chat-test" else None,
    )

    await agent_session._compress_old_messages(
        [{"role": "user", "content": "remember this project detail"}],
        session_id="chat-test",
    )


@pytest.mark.asyncio
async def test_save_session_messages_does_not_regress_final_reply(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    data_dir = tmp_path

    old_state_file = agent_state.STATE_FILE
    old_data_dir = agent_state.DATA_DIR
    old_base = agent._persist_base_messages.get()
    old_merge_live = agent._persist_merge_live_state.get()
    old_prefix = agent._persist_history_prefix_len.get()
    old_insert = agent._persist_insert_at.get()
    old_round_id = agent._current_round_id.get()
    try:
        agent_state.STATE_FILE = state_file
        agent_state.DATA_DIR = data_dir

        existing = [
            {
                "role": "assistant",
                "message_id": "msg_prev_assistant",
                "content": "done",
                "round_id": "round_prev",
            },
            {
                "role": "user",
                "message_id": "msg_current_user",
                "content": "why no reply",
                "round_id": "round_now",
            },
            {
                "role": "assistant",
                "message_id": "msg_final_assistant",
                "content": "reply restored",
                "round_id": "round_now",
            },
        ]
        state_file.write_text(
            json.dumps({"archive_session_id": "session_test", "messages": existing}, ensure_ascii=False),
            encoding="utf-8",
        )

        history = [existing[0]]
        stale_messages = [
            history[0],
            {
                "role": "user",
                "message_id": "msg_current_user",
                "content": "why no reply",
                "round_id": "round_now",
            },
        ]

        agent._persist_base_messages.set(None)
        agent._persist_merge_live_state.set(True)
        agent._persist_history_prefix_len.set(len(history))
        agent._persist_insert_at.set(len(history))
        agent._current_round_id.set("round_now")

        await agent._save_session_messages(stale_messages)

        saved = json.loads(state_file.read_text(encoding="utf-8"))
        saved_messages = saved["messages"]
        assert [msg["message_id"] for msg in saved_messages] == [
            "msg_prev_assistant",
            "msg_current_user",
            "msg_final_assistant",
        ]
    finally:
        agent_state.STATE_FILE = old_state_file
        agent_state.DATA_DIR = old_data_dir
        agent._persist_base_messages.set(old_base)
        agent._persist_merge_live_state.set(old_merge_live)
        agent._persist_history_prefix_len.set(old_prefix)
        agent._persist_insert_at.set(old_insert)
        agent._current_round_id.set(old_round_id)


@pytest.mark.asyncio
async def test_save_session_messages_with_persist_base_preserves_concurrent_messages(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    data_dir = tmp_path

    old_state_file = agent_state.STATE_FILE
    old_data_dir = agent_state.DATA_DIR
    old_base = agent._persist_base_messages.get()
    old_merge_live = agent._persist_merge_live_state.get()
    old_prefix = agent._persist_history_prefix_len.get()
    old_insert = agent._persist_insert_at.get()
    try:
        agent_state.STATE_FILE = state_file
        agent_state.DATA_DIR = data_dir

        base_messages = [
            {"role": "user", "message_id": "u1", "content": "first"},
            {"role": "assistant", "message_id": "a1", "content": "reply"},
        ]
        current_messages = [
            *base_messages,
            {"role": "user", "message_id": "g1", "content": "queued guidance", "queued_guidance_id": "guide_1"},
        ]
        state_file.write_text(
            json.dumps({"archive_session_id": "session_test", "messages": current_messages}, ensure_ascii=False),
            encoding="utf-8",
        )

        incoming = [
            *base_messages,
            {"role": "user", "message_id": "u2", "content": "new question"},
            {"role": "assistant", "message_id": "a2", "content": "new answer"},
        ]

        agent._persist_base_messages.set(base_messages)
        agent._persist_merge_live_state.set(False)
        agent._persist_history_prefix_len.set(len(base_messages))
        agent._persist_insert_at.set(len(base_messages))

        await agent._save_session_messages(incoming)

        saved = json.loads(state_file.read_text(encoding="utf-8"))["messages"]
        assert [msg["message_id"] for msg in saved] == ["u1", "a1", "g1", "u2", "a2"]
    finally:
        agent_state.STATE_FILE = old_state_file
        agent_state.DATA_DIR = old_data_dir
        agent._persist_base_messages.set(old_base)
        agent._persist_merge_live_state.set(old_merge_live)
        agent._persist_history_prefix_len.set(old_prefix)
        agent._persist_insert_at.set(old_insert)


@pytest.mark.asyncio
async def test_save_session_messages_with_persist_base_keeps_question_answer_after_transient_system(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    data_dir = tmp_path

    old_state_file = agent_state.STATE_FILE
    old_data_dir = agent_state.DATA_DIR
    old_base = agent._persist_base_messages.get()
    old_merge_live = agent._persist_merge_live_state.get()
    old_prefix = agent._persist_history_prefix_len.get()
    old_insert = agent._persist_insert_at.get()
    try:
        agent_state.STATE_FILE = state_file
        agent_state.DATA_DIR = data_dir

        base_messages = [
            {"role": "user", "message_id": "u1", "content": "start", "round_id": "round_q"},
            {
                "role": "assistant",
                "message_id": "q1",
                "content": "Need permission?",
                "round_id": "round_q",
                "question_prompt": True,
                "question_id": "question_1",
            },
        ]
        state_file.write_text(
            json.dumps({"archive_session_id": "session_test", "messages": base_messages}, ensure_ascii=False),
            encoding="utf-8",
        )

        incoming = [
            *base_messages,
            {"role": "user", "message_id": "u2", "content": "allow once", "round_id": "round_q"},
            {"role": "assistant", "message_id": "a2", "content": "continuing", "round_id": "round_q"},
        ]

        agent._persist_base_messages.set(base_messages)
        agent._persist_merge_live_state.set(False)
        agent._persist_history_prefix_len.set(len(base_messages) + 1)
        agent._persist_insert_at.set(len(base_messages))

        await agent._save_session_messages(incoming)

        saved = json.loads(state_file.read_text(encoding="utf-8"))["messages"]
        assert [msg["message_id"] for msg in saved] == ["u1", "q1", "u2", "a2"]
    finally:
        agent_state.STATE_FILE = old_state_file
        agent_state.DATA_DIR = old_data_dir
        agent._persist_base_messages.set(old_base)
        agent._persist_merge_live_state.set(old_merge_live)
        agent._persist_history_prefix_len.set(old_prefix)
        agent._persist_insert_at.set(old_insert)


def test_get_session_labels_persists_generated_archive_session_id(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    data_dir = tmp_path

    old_state_file = agent_state.STATE_FILE
    old_data_dir = agent_state.DATA_DIR
    try:
        agent_state.STATE_FILE = state_file
        agent_state.DATA_DIR = data_dir
        state_file.write_text(json.dumps({"messages": []}, ensure_ascii=False), encoding="utf-8")

        labels_first = agent.get_session_labels()
        labels_second = agent.get_session_labels()
        saved_state = json.loads(state_file.read_text(encoding="utf-8"))

        assert labels_first["archive_session_id"]
        assert labels_first["archive_session_id"] == labels_second["archive_session_id"]
        assert saved_state["archive_session_id"] == labels_first["archive_session_id"]
    finally:
        agent_state.STATE_FILE = old_state_file
        agent_state.DATA_DIR = old_data_dir
