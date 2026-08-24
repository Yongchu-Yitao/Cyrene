import json
from types import SimpleNamespace

import pytest

from cyrene.agent import session as agent_session
from cyrene.agent import state as agent_state
from cyrene.agent.lane_protocol import (
    ExecutionHandoff,
    ExecutionOutcome,
    advance_lane_epoch_in_state,
    bind_agent_lane,
    build_execution_handoff_message,
    build_execution_outcome_message,
    current_agent_lane,
    lane_cache_epoch_id,
    lane_epochs_from_state,
    project_lane_history,
    tag_lane_record,
)
from cyrene.model_runtime.transcript_policy import (
    ProviderFamily,
    TranscriptPolicy,
)
from cyrene.workbench.chat import (
    _extract_exchange_timeline,
    _public_messages,
    _side_agent_parent_transcript,
)
from cyrene.workbench.presentation_runtime import _convert_messages


def test_lane_events_have_fixed_field_order_and_stable_json() -> None:
    handoff = ExecutionHandoff.create(
        "turn-1",
        "原始请求",
        "x" * 400,
        hard_constraints=[{"source": "user", "value": "no network"}],
        attachment_refs=[{"z": 1, "a": 2}],
    )
    same_handoff = ExecutionHandoff.create(
        "turn-1",
        "原始请求",
        "x" * 400,
        hard_constraints=[{"value": "no network", "source": "user"}],
        attachment_refs=[{"a": 2, "z": 1}],
    )
    assert list(handoff.to_dict()) == [
        "type", "version", "event_id", "turn_id", "attempt", "request",
        "execution_brief", "hard_constraints", "attachment_refs",
        "conversation_delta",
    ]
    assert len(handoff.execution_brief) == 300
    assert handoff.stable_json() == same_handoff.stable_json()
    assert "timestamp" not in handoff.stable_json()

    outcome = ExecutionOutcome.create(
        "turn-1",
        "completed",
        public_reply="done",
        artifacts=[{"path": "report.md"}],
    )
    assert list(outcome.to_dict()) == [
        "type", "version", "event_id", "turn_id", "attempt", "status",
        "public_reply", "state_summary", "artifacts", "unresolved",
        "conversation_delta",
    ]
    assert outcome.stable_json() == ExecutionOutcome.create(
        "turn-1", "completed", public_reply="done",
        artifacts=[{"path": "report.md"}],
    ).stable_json()


def test_agent_lane_binding_is_run_local_and_resets() -> None:
    assert current_agent_lane() == "decision"
    with bind_agent_lane("execution"):
        assert current_agent_lane() == "execution"
    assert current_agent_lane() == "decision"


def test_lane_epochs_are_stable_independent_and_legacy_compatible() -> None:
    legacy_state = {"messages": []}
    assert lane_epochs_from_state(legacy_state) == {
        "decision": 0,
        "execution": 0,
    }
    initial_decision = lane_cache_epoch_id(legacy_state, "decision")
    initial_execution = lane_cache_epoch_id(legacy_state, "execution")
    assert initial_decision == lane_cache_epoch_id(legacy_state, "decision")
    assert initial_decision != initial_execution
    advance_lane_epoch_in_state(legacy_state, "decision")

    assert legacy_state["lane_epochs"] == {"decision": 1, "execution": 0}
    assert lane_cache_epoch_id(legacy_state, "decision") != initial_decision
    assert lane_cache_epoch_id(legacy_state, "execution") == initial_execution


def test_run_cache_scope_uses_only_the_selected_lane_epoch() -> None:
    session_id = "lane-cache-scope"
    ctx = agent_state._ensure_session(session_id)
    ctx.cache_session_epoch = 0
    ctx.lane_epochs = {"decision": 2, "execution": 0}
    session_token = agent_state._current_session_id.set(session_id)
    lease_token = agent_state._run_model_lease.set(SimpleNamespace(
        provider_family=ProviderFamily.OPENAI_COMPATIBLE,
        transcript_policy=TranscriptPolicy.DUAL_LANE,
    ))
    try:
        assert agent_state.current_run_cache_scope("decision") == "decision"
        assert agent_state.current_run_cache_epoch("decision").endswith(
            "lane-v1:decision:s0:e2"
        )
        assert agent_state.current_run_cache_epoch("execution").endswith(
            "lane-v1:execution:s0:e0"
        )
    finally:
        agent_state._run_model_lease.reset(lease_token)
        agent_state._current_session_id.reset(session_token)
        agent_state._sessions.pop(session_id, None)


def test_lane_projection_isolates_transcripts_and_keeps_safe_legacy_context() -> None:
    handoff_message = build_execution_handoff_message(
        ExecutionHandoff.create("turn-1", "inspect")
    )
    outcome_message = build_execution_outcome_message(
        ExecutionOutcome.create("turn-1", "completed", public_reply="done"),
        tool_call_id="use-tools-1",
    )
    messages = [
        {"role": "user", "content": "legacy visible request", "message_id": "legacy"},
        {
            "role": "assistant",
            "content": "legacy UI question",
            "message_id": "legacy-question",
            "question_prompt": True,
        },
        tag_lane_record(
            {"role": "assistant", "content": "decision trace", "message_id": "d1"},
            "decision",
        ),
        handoff_message,
        tag_lane_record(
            {
                "role": "assistant",
                "content": "execution trace",
                "message_id": "e1",
                "tool_calls": [{"id": "call-1", "function": {"name": "Read", "arguments": "{}"}}],
            },
            "execution",
        ),
        outcome_message,
    ]

    decision = project_lane_history(messages, "decision")
    execution = project_lane_history(messages, "execution")
    assert [message["message_id"] for message in decision] == [
        "legacy", "d1", outcome_message["message_id"],
    ]
    assert [message["message_id"] for message in execution] == [
        "legacy", handoff_message["message_id"], "e1",
    ]
    assert all(message.get("message_id") != "e1" for message in decision)
    assert all(message.get("message_id") != "d1" for message in execution)


def test_legacy_codex_projection_is_unchanged() -> None:
    legacy = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "pick one", "question_prompt": True},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "Read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]
    assert project_lane_history(legacy, "decision") == legacy
    assert project_lane_history(legacy, "execution") == legacy


def test_hidden_protocol_records_are_never_projected_to_workbench() -> None:
    hidden = tag_lane_record(
        {
            "role": "assistant",
            "content": "private protocol",
            "message_id": "private-1",
            "reasoning_content": "private reasoning",
        },
        "execution",
        record_kind="execution_handoff",
        hidden_from_ui=True,
    )
    visible = {"role": "assistant", "content": "public reply", "message_id": "public-1"}

    assert [message.get("body") for message in _convert_messages([hidden, visible])] == [
        "public reply"
    ]
    assert _public_messages([hidden, visible]) == [visible]
    assert _extract_exchange_timeline([hidden], set())[0] == []
    assert _side_agent_parent_transcript({"messages": [hidden, visible]}) == (
        "[1. assistant]\npublic reply"
    )


@pytest.mark.asyncio
async def test_lane_upsert_and_full_save_preserve_other_lane(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "lane-session.json"
    session_id = "lane-session"
    monkeypatch.setattr(agent_session, "_session_state_file", lambda _session_id: state_path)

    decision_record = build_execution_outcome_message(
        ExecutionOutcome.create("turn-save", "completed", public_reply="done"),
        tool_call_id="use-tools-save",
    )
    execution_record = build_execution_handoff_message(
        ExecutionHandoff.create("turn-save", "inspect"),
    )
    await agent_session.append_or_upsert_lane_record(
        decision_record,
        session_id=session_id,
    )
    await agent_session.append_or_upsert_lane_record(
        execution_record,
        session_id=session_id,
    )

    token = agent_state._current_session_id.set(session_id)
    try:
        await agent_session._save_session_messages([
            decision_record,
        ])
    finally:
        agent_state._current_session_id.reset(token)

    saved = json.loads(state_path.read_text(encoding="utf-8"))["messages"]
    assert {message["message_id"] for message in saved} == {
        decision_record["message_id"], execution_record["message_id"],
    }
    saved_handoff = next(
        message for message in saved
        if message["message_id"] == execution_record["message_id"]
    )
    assert saved_handoff["record_kind"] == "execution_handoff"
    assert saved_handoff["hidden_from_ui"] is True
    assert all("chat_group_context_event" not in message for message in saved)


@pytest.mark.asyncio
async def test_lane_projection_save_preserves_canonical_chronology(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "lane-order.json"
    session_id = "lane-order"
    monkeypatch.setattr(agent_session, "_session_state_file", lambda _session_id: state_path)

    def record(event_id: str, lane: str) -> dict:
        return tag_lane_record(
            {
                "role": "assistant",
                "content": event_id,
                "event_id": event_id,
                "message_id": f"msg_{event_id}",
            },
            lane,
            persist_model_record=True,
        )

    decision_a = record("a", "decision")
    execution_b = record("b", "execution")
    decision_c = record("c", "decision")
    execution_d = record("d", "execution")
    decision_e = record("e", "decision")
    for entry in (decision_a, execution_b, decision_c, execution_d):
        await agent_session.append_or_upsert_lane_record(
            entry,
            session_id=session_id,
        )

    token = agent_state._current_session_id.set(session_id)
    try:
        # A decision projection omits B/D and appends E.  Saving it must merge
        # the execution records back where they occurred, before the new tail.
        await agent_session._save_session_messages([
            decision_a,
            decision_c,
            decision_e,
        ])
    finally:
        agent_state._current_session_id.reset(token)

    saved = json.loads(state_path.read_text(encoding="utf-8"))["messages"]
    assert [message["event_id"] for message in saved] == ["a", "b", "c", "d", "e"]


def test_lane_records_compact_by_lane_without_permanent_pins() -> None:
    messages = []
    for index in range(8):
        messages.extend([
            tag_lane_record(
                {
                    "role": "assistant",
                    "content": (f"DECISION_ONLY_{index} " * 24).strip(),
                    "message_id": f"msg_decision_{index}",
                },
                "decision",
                persist_model_record=True,
            ),
            tag_lane_record(
                {
                    "role": "assistant",
                    "content": (f"EXECUTION_ONLY_{index} " * 24).strip(),
                    "message_id": f"msg_execution_{index}",
                },
                "execution",
                persist_model_record=True,
            ),
        ])

    compacted_lanes = set()
    compacted = agent_session._compact_preserving_lane_records(
        messages,
        ctx_limit=320,
        compacted_lanes=compacted_lanes,
    )

    assert len(compacted) < len(messages)
    assert compacted_lanes == {"decision", "execution"}
    decision = project_lane_history(compacted, "decision")
    execution = project_lane_history(compacted, "execution")
    decision_blocks = [
        message for message in decision if message.get("record_kind") == "lane_compacted"
    ]
    execution_blocks = [
        message for message in execution if message.get("record_kind") == "lane_compacted"
    ]
    assert len(decision_blocks) == 1
    assert len(execution_blocks) == 1
    assert "EXECUTION_ONLY" not in decision_blocks[0]["content"]
    assert "DECISION_ONLY" not in execution_blocks[0]["content"]
    assert any(message.get("message_id") == "msg_decision_7" for message in decision)
    assert any(message.get("message_id") == "msg_execution_7" for message in execution)
    assert all("chat_group_context_event" not in message for message in compacted)


def test_powerpoint_receipt_is_created_only_at_lane_compaction_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from cyrene.agent.deep_reflection import project_history_for_llm
    from cyrene.tooling import result_store

    monkeypatch.setattr(result_store, "_RESULT_ROOT", tmp_path / "tool-results")
    messages = [
        tag_lane_record(
            {
                "role": "assistant",
                "content": "inspect",
                "message_id": "ppt_call",
                "tool_calls": [{
                    "id": "ppt-inspect",
                    "function": {
                        "name": "PowerPointInspect",
                        "arguments": '{"operation":"list_slides"}',
                    },
                }],
            },
            "execution",
        ),
        tag_lane_record(
            {
                "role": "tool",
                "tool_call_id": "ppt-inspect",
                "content": '{"status":"success","revision":7}',
                "message_id": "ppt_result",
            },
            "execution",
        ),
        tag_lane_record(
            {
                "role": "assistant",
                "content": "inspection complete",
                "message_id": "ppt_final",
            },
            "execution",
        ),
    ]

    assert project_history_for_llm(messages) == messages
    compacted_lanes = set()
    compacted = agent_session._compact_preserving_lane_records(
        messages,
        force=True,
        compacted_lanes=compacted_lanes,
    )

    assert compacted_lanes == {"execution"}
    receipt = next(
        message for message in compacted
        if message.get("powerpoint_episode_receipt")
    )
    assert receipt["record_kind"] == "lane_compacted"
    assert json.loads(receipt["content"])["type"] == (
        "powerpoint_tool_episode_receipt"
    )
    assert compacted[-1]["message_id"] == "ppt_final"
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == (
        '{"operation":"list_slides"}'
    )


@pytest.mark.asyncio
async def test_lane_upsert_supports_default_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "default-lane.json"
    monkeypatch.setattr(agent_session, "_session_state_file", lambda _session_id: state_path)
    record = tag_lane_record(
        {
            "role": "assistant",
            "content": "default session",
            "event_id": "default-event",
            "message_id": "msg_default_event",
        },
        "decision",
    )

    token = agent_state._current_session_id.set("")
    try:
        await agent_session.append_or_upsert_lane_record(record)
    finally:
        agent_state._current_session_id.reset(token)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    saved = state["messages"]
    assert [message["message_id"] for message in saved] == ["msg_default_event"]
    assert "lane_epochs" not in state


@pytest.mark.asyncio
async def test_lane_cache_epoch_advance_is_atomic_and_lane_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "lane-epoch.json"
    session_id = "lane-epoch"
    monkeypatch.setattr(agent_session, "_session_state_file", lambda _session_id: state_path)
    state_path.write_text(json.dumps({
        "_session_epoch": 3,
        "messages": [],
        "lane_epochs": {"decision": 4, "execution": 7},
    }), encoding="utf-8")
    execution_before = agent_session.get_lane_cache_epoch(
        "execution",
        session_id=session_id,
    )

    decision_epoch = await agent_session.advance_lane_cache_epoch(
        "decision",
        session_id=session_id,
    )

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["_session_epoch"] == 3
    assert saved["lane_epochs"] == {"decision": 5, "execution": 7}
    assert decision_epoch.endswith("lane-v1:decision:s3:e5")
    assert agent_session.get_lane_cache_epoch(
        "execution",
        session_id=session_id,
    ) == execution_before
    agent_state._sessions.pop(session_id, None)


@pytest.mark.asyncio
async def test_lane_compaction_advances_only_the_compacted_lane_epoch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "lane-compaction-epoch.json"
    session_id = "lane-compaction-epoch"
    monkeypatch.setattr(agent_session, "_session_state_file", lambda _session_id: state_path)
    messages = [
        tag_lane_record(
            {
                "role": "assistant",
                "content": (f"DECISION_{index} " * 24).strip(),
                "message_id": f"decision_{index}",
                **({
                    "tool_calls": [{
                        "id": "decision-tool",
                        "function": {"name": "use_tools", "arguments": "{}"},
                    }],
                } if index == 0 else {}),
            },
            "decision",
        )
        for index in range(8)
    ]
    state_path.write_text(json.dumps({"messages": messages}), encoding="utf-8")

    async def ignore_event(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(agent_session.debug, "publish_event", ignore_event)
    monkeypatch.setattr(agent_session, "_schedule_compaction_distill", lambda *_args: None)
    result = await agent_session.compact_session_if_needed(
        session_id,
        ctx_limit=320,
        force=True,
    )

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert result["compacted"] is True
    assert saved["lane_epochs"] == {"decision": 1, "execution": 0}
    assert any(
        message.get("record_kind") == "lane_compacted"
        for message in saved["messages"]
    )
    agent_state._sessions.pop(session_id, None)


@pytest.mark.asyncio
async def test_pending_question_ui_projection_is_not_model_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "pending-lane.json"
    session_id = "pending-lane"
    monkeypatch.setattr(agent_session, "_session_state_file", lambda _session_id: state_path)
    state_path.write_text(json.dumps({
        "messages": [{
            "role": "assistant",
            "content": "legacy prompt",
            "message_id": "legacy-question-message",
            "question_id": "question-1",
            "question_prompt": True,
        }],
    }), encoding="utf-8")

    async def ignore_event(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(agent_session, "_publish_runtime_event", ignore_event)
    monkeypatch.setattr(agent_session.debug, "publish_event", ignore_event)
    token = agent_state._current_session_id.set(session_id)
    lease_token = agent_state._run_model_lease.set(SimpleNamespace(
        provider_family=ProviderFamily.OPENAI_COMPATIBLE,
        transcript_policy=TranscriptPolicy.DUAL_LANE,
    ))
    try:
        question = await agent_session._upsert_pending_question({
            "id": "question-1",
            "text": "Which format?",
            "owner_lane": "execution",
        })
    finally:
        agent_state._run_model_lease.reset(lease_token)
        agent_state._current_session_id.reset(token)

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    prompt = saved["messages"][0]
    assert question["message_id"] == "legacy-question-message"
    assert prompt["message_id"] == question["message_id"]
    assert prompt["record_kind"] == "pending_question"
    assert prompt["persist_model_record"] is False
    assert project_lane_history(saved["messages"], "execution") == []
    agent_state._sessions.pop(session_id, None)


@pytest.mark.asyncio
async def test_pending_question_keeps_legacy_codex_model_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "pending-legacy.json"
    session_id = "pending-legacy"
    monkeypatch.setattr(
        agent_session,
        "_session_state_file",
        lambda _session_id: state_path,
    )

    async def ignore_event(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(agent_session, "_publish_runtime_event", ignore_event)
    monkeypatch.setattr(agent_session.debug, "publish_event", ignore_event)
    session_token = agent_state._current_session_id.set(session_id)
    lease_token = agent_state._run_model_lease.set(SimpleNamespace(
        provider_family=ProviderFamily.CODEX,
        transcript_policy=TranscriptPolicy.LEGACY_SHARED,
    ))
    try:
        await agent_session._upsert_pending_question({
            "id": "question-legacy",
            "text": "Which format?",
            "owner_lane": "decision",
        })
    finally:
        agent_state._run_model_lease.reset(lease_token)
        agent_state._current_session_id.reset(session_token)

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["messages"][0]["persist_model_record"] is True
    assert project_lane_history(saved["messages"], "decision") == saved["messages"]
    agent_state._sessions.pop(session_id, None)


def test_pending_question_owner_lane_defaults_to_decision() -> None:
    assert agent_session._normalize_pending_question({
        "id": "q1", "text": "continue?",
    })["owner_lane"] == "decision"
    assert agent_session._normalize_pending_question({
        "id": "q2", "text": "continue?", "owner_lane": "execution",
    })["owner_lane"] == "execution"
    assert agent_session._normalize_pending_question({
        "id": "q3", "text": "continue?", "owner_lane": "invalid",
    })["owner_lane"] == "decision"


@pytest.mark.asyncio
async def test_live_pending_question_uses_trusted_current_lane(monkeypatch) -> None:
    captured = []

    async def fake_upsert(payload):
        captured.append(payload)
        return payload

    monkeypatch.setattr(agent_session, "_upsert_pending_question", fake_upsert)
    with bind_agent_lane("execution"):
        result = await agent_session.upsert_pending_question({
            "id": "q-live", "text": "continue?",
        })

    assert result["owner_lane"] == "execution"
    assert captured == [result]
