from __future__ import annotations

import asyncio
import copy
import json
from unittest.mock import AsyncMock


def test_historical_final_reply_exports_delegate_to_live_reply_module():
    from cyrene import agent
    from cyrene.agent import replies

    assert agent._final_user_reply_from_history is replies._final_user_reply_from_history
    assert agent._final_plain_reply_from_history is replies._final_plain_reply_from_history
    assert agent._final_reply_from_history is replies._final_reply_from_history


async def test_guidance_historical_model_patch_seam_still_delegates(monkeypatch):
    from cyrene.agent import guidance

    calls = []

    async def fake_call(messages, tools=None, max_tokens=None):
        calls.append((messages, tools, max_tokens))
        return {"role": "assistant", "content": "patched reply"}

    monkeypatch.setattr(guidance, "_call_llm", fake_call)
    monkeypatch.setattr(guidance, "_streaming_reply_requested", lambda: False)

    result = await guidance._final_reply_from_history([
        {"role": "user", "content": "hello"},
    ])

    assert result == "patched reply"
    assert len(calls) == 1

def test_phase1_protocol_normalization_keeps_policy_out_of_orchestrator():
    from cyrene.agent.loop_protocol import normalize_phase1_decision

    response = {
        "content": "",
        "tool_calls": [
            {
                "id": "run",
                "function": {
                    "name": "Read",
                    "arguments": '{"path":"README.md"}',
                },
            },
            {
                "id": "ask",
                "function": {
                    "name": "ask_user",
                    "arguments": '{"text":"Which file?"}',
                },
            },
        ],
    }

    decision = normalize_phase1_decision(
        response,
        allowed_tool_names={"use_tools", "ask_user", "quit"},
        wire_tool_names={"use_tools", "ask_user", "quit", "Read"},
        can_promote_tools=True,
        system_initiated=False,
    )

    assert [call["function"]["name"] for call in decision.tool_calls] == [
        "ask_user"
    ]
    assert decision.concrete_calls == ()
    assert decision.ask_user_call is not None
    assert decision.enters_execution is False


async def test_phase1_plain_text_requires_explicit_quit_after_one_control_repair(monkeypatch):
    from cyrene.agent import agent as agent_mod

    model_calls = []
    saved = []

    responses = iter([
        {"role": "assistant", "content": "draft", "tool_calls": []},
        {
            "role": "assistant",
            "content": "direct answer",
            "tool_calls": [{
                "id": "decision-quit",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        },
    ])

    async def fake_llm(messages, tools=None, **kwargs):
        assert kwargs.get("tool_choice") == "required"
        model_calls.append(messages)
        return next(responses)

    async def fake_save(messages, **_kwargs):
        saved.append(messages)

    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_append_session_message", AsyncMock())
    monkeypatch.setattr(agent_mod, "_save_session_messages", fake_save)
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", AsyncMock())

    result = await agent_mod._run_main_agent(
        "answer directly",
        [],
        None,
        0,
        "db.sqlite3",
        system_prompt="system",
    )

    assert result == "direct answer"
    assert len(model_calls) == 2
    assert saved[-1][-1]["content"] == "direct answer"
    assert "tool_calls" not in saved[-1][-1]


async def test_phase2_plain_text_requires_explicit_quit_after_one_control_repair(monkeypatch):
    from cyrene.agent import agent as agent_mod

    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "handoff",
                "function": {
                    "name": "use_tools",
                    "arguments": '{"execution_brief":"inspect"}',
                },
            }],
        },
        {"role": "assistant", "content": "still working", "tool_calls": []},
        {
            "role": "assistant",
            "content": "execution answer",
            "tool_calls": [{
                "id": "execution-quit",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        },
    ])
    model_calls = []

    async def fake_llm(messages, tools=None, **_kwargs):
        model_calls.append(messages)
        return next(responses)

    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_append_session_message", AsyncMock())
    monkeypatch.setattr(agent_mod, "_save_session_messages", AsyncMock())
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", AsyncMock())

    result = await agent_mod._run_main_agent(
        "inspect",
        [],
        None,
        0,
        "db.sqlite3",
        system_prompt="system",
    )

    assert result == "execution answer"
    assert len(model_calls) == 3


async def test_direct_main_loop_call_keeps_legacy_shared_compatibility(monkeypatch):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent.transcript_policy import TranscriptPolicy

    calls = []
    executed = []
    resets = []
    lease_overrides = []
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "legacy-read",
                "function": {
                    "name": "Read",
                    "arguments": '{"path":"README.md"}',
                },
            }],
        },
        {
            "role": "assistant",
            "content": "Read complete.",
            "tool_calls": [{
                "id": "legacy-quit",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        },
    ])

    async def fake_llm(messages, tools=None, **_kwargs):
        calls.append((messages, tools))
        return next(responses)

    async def fake_execute(name, arguments, *args, **kwargs):
        executed.append((name, arguments))
        return "README contents"

    def fake_activate_run_model_lease(*, transcript_policy_override=None):
        lease_overrides.append(transcript_policy_override)
        return "lease-token"

    monkeypatch.setattr(agent_mod, "has_active_run_model_lease", lambda: False)
    monkeypatch.setattr(agent_mod, "activate_run_model_lease", fake_activate_run_model_lease)
    monkeypatch.setattr(agent_mod, "reset_run_model_lease", resets.append)
    monkeypatch.setattr(
        agent_mod,
        "current_run_transcript_policy",
        lambda: TranscriptPolicy.LEGACY_SHARED,
    )
    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_execute_tool", fake_execute)
    monkeypatch.setattr(agent_mod, "_append_session_message", AsyncMock())
    monkeypatch.setattr(agent_mod, "_save_session_messages", AsyncMock())
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", AsyncMock())

    result = await agent_mod._run_main_agent(
        "Read the README",
        [],
        None,
        0,
        "db.sqlite3",
        system_prompt="LEGACY_SHARED_MARKER",
        resume_lane="execution",
    )

    assert result == "Read complete."
    assert executed == [("Read", {"path": "README.md"})]
    assert lease_overrides == [TranscriptPolicy.LEGACY_SHARED]
    assert resets == ["lease-token"]
    assert calls[0][1] is calls[1][1]
    assert "LEGACY_SHARED_MARKER" in calls[0][0][0]["content"]
    assert {tool["function"]["name"] for tool in calls[0][1] or []} > {
        "use_tools", "ask_user", "quit"
    }


async def test_phase1_terminal_guidance_redecides_without_forcing_execution(
    monkeypatch,
):
    from cyrene.agent import agent as agent_mod

    guidance = {
        "event_id": "guide-1",
        "payload": {"text": "make it shorter"},
    }

    class FakeInbox:
        round_id = ""

        def __init__(self):
            self.boundary_checks = 0
            self.acknowledged = []

        def collect_guidance_nowait(self):
            return []

        async def wait_for_guidance(self):
            await asyncio.Future()

        async def collect_guidance_or_seal(self):
            self.boundary_checks += 1
            return [guidance] if self.boundary_checks == 1 else []

        async def wait_for_active_tools(self):
            return None

        def acknowledge(self, events):
            self.acknowledged.extend(events)

    fake_inbox = FakeInbox()
    model_calls = []
    events = []

    async def fake_llm(messages, tools=None, **_kwargs):
        model_calls.append(messages)
        if len(model_calls) == 1:
            return {
                "role": "assistant",
                "content": "long answer",
                "tool_calls": [{
                    "id": "old-quit",
                    "function": {"name": "quit", "arguments": "{}"},
                }],
            }
        assert any(
            "make it shorter" in str(message.get("content") or "")
            for message in messages
        )
        return {
            "role": "assistant",
            "content": "short answer",
            "tool_calls": [{
                "id": "new-quit",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        }

    async def capture_event(event):
        events.append(event)

    monkeypatch.setattr(agent_mod, "current_workbench_inbox", lambda: fake_inbox)
    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_append_session_message", AsyncMock())
    monkeypatch.setattr(agent_mod, "_save_session_messages", AsyncMock())
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", capture_event)

    result = await agent_mod._run_main_agent(
        "explain",
        [],
        None,
        0,
        "db.sqlite3",
        system_prompt="system",
    )

    assert result == "short answer"
    assert len(model_calls) == 2
    assert fake_inbox.acknowledged == [guidance]
    assert not any(event.get("to") == "phase2_execution" for event in events)
    deferred_tool_result = next(
        message
        for message in model_calls[1]
        if message.get("tool_call_id") == "old-quit"
    )
    assert deferred_tool_result["hidden_from_ui"] is True


def test_lane_deltas_only_carry_public_conversation_since_last_boundary():
    from cyrene.agent.loop_protocol import (
        decision_conversation_delta,
        execution_conversation_delta,
        public_assistant_artifact_refs,
    )

    decision_messages = [
        {"role": "user", "content": "already synchronized", "lane_refs": ["decision"]},
        {"role": "tool", "content": "outcome", "record_kind": "execution_outcome"},
        {"role": "user", "content": "first missed turn", "lane_refs": ["decision"]},
        {"role": "assistant", "content": "first answer", "lane_refs": ["decision"]},
        {"role": "assistant", "content": "legacy shared and already visible to execution"},
        {"role": "user", "content": "old shared guidance", "runtime_guidance": True},
        {"role": "user", "content": "second missed turn", "lane_refs": ["decision"]},
        {
            "role": "user",
            "content": "live decision guidance",
            "runtime_guidance": True,
            "message_id": "live-guidance",
        },
        {
            "role": "assistant",
            "content": "private decision",
            "lane_refs": ["decision"],
            "hidden_from_ui": True,
        },
        {
            "role": "user",
            "content": "current request",
            "message_id": "current",
            "lane_refs": ["decision"],
        },
    ]
    assert decision_conversation_delta(
        decision_messages,
        current_user_message_id="current",
        runtime_guidance_message_ids=["live-guidance"],
    ) == [
        {"type": "conversation_message", "role": "user", "content": "first missed turn"},
        {"type": "conversation_message", "role": "assistant", "content": "first answer"},
        {"type": "conversation_message", "role": "user", "content": "second missed turn"},
        {"type": "conversation_message", "role": "user", "content": "live decision guidance"},
    ]

    execution_messages = [
        {"role": "user", "content": "handoff", "record_kind": "execution_handoff"},
        {
            "role": "assistant",
            "content": "Which format?",
            "record_kind": "pending_question",
            "lane_refs": ["execution"],
        },
        {"role": "user", "content": "Markdown", "record_kind": "conversation", "lane_refs": ["execution"]},
        {"role": "tool", "content": "large private evidence", "lane_refs": ["execution"]},
        {
            "role": "assistant",
            "content": "final answer",
            "lane_refs": ["execution"],
            "attachments": [{
                "id": "report",
                "name": "report.pdf",
                "url": "/api/report.pdf",
                "private_token": "must-not-copy",
            }],
        },
        {
            "role": "user",
            "content": "Also keep it short",
            "runtime_guidance": True,
            "lane_refs": ["execution"],
        },
    ]
    assert execution_conversation_delta(execution_messages) == [
        {"type": "conversation_message", "role": "assistant", "content": "Which format?"},
        {"type": "conversation_message", "role": "user", "content": "Markdown"},
        {"type": "conversation_message", "role": "user", "content": "Also keep it short"},
    ]
    assert public_assistant_artifact_refs(execution_messages) == [{
        "id": "report",
        "name": "report.pdf",
        "url": "/api/report.pdf",
    }]


def test_side_conversation_delta_uses_only_frozen_public_sections():
    from cyrene.agent.loop_protocol import side_conversation_delta

    wrapped = (
        "private coordinator instruction\n"
        "<main_conversation>User: public parent\nAssistant: public reply</main_conversation>\n"
        "<selected_quote>public selected quote</selected_quote>\n"
        "private suffix"
    )
    assert side_conversation_delta(wrapped) == [{
        "type": "side_conversation_snapshot",
        "parent_public_transcript": "User: public parent\nAssistant: public reply",
        "selected_quote": "public selected quote",
    }]
    assert side_conversation_delta([{"type": "text", "text": wrapped}]) == [
        {
            "type": "side_conversation_snapshot",
            "parent_public_transcript": "User: public parent\nAssistant: public reply",
            "selected_quote": "public selected quote",
        }
    ]
    assert side_conversation_delta("private coordinator instruction only") == []


async def test_dual_lane_handoff_keeps_decision_light_and_execution_specialized(
    monkeypatch,
):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent.transcript_policy import TranscriptPolicy

    wrapped_request = (
        "private coordinator instruction\n"
        "<main_conversation>User: parent context</main_conversation>\n"
        "<selected_quote>selected public quote</selected_quote>"
    )
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "route-1",
                "function": {
                    "name": "use_tools",
                    "arguments": '{"execution_brief":"Inspect then update"}',
                },
            }],
        },
        {
            "role": "assistant",
            "content": "Updated the file.",
            "tool_calls": [{
                "id": "quit-1",
                "function": {
                    "name": "quit",
                    "arguments": json.dumps({
                        "state_summary": "The requested file was updated.",
                        "artifacts": [{"name": "result.md"}],
                        "unresolved": [],
                    }),
                },
            }],
        },
    ])
    model_calls = []
    lane_records = []
    saved_records = []

    async def fake_llm(messages, tools=None, **_kwargs):
        model_calls.append((messages, tools))
        return next(responses)

    async def capture_lane_record(message, session_id=None):
        lane_records.append(message)

    async def capture_save(messages, **_kwargs):
        saved_records.append(messages)

    monkeypatch.setattr(
        agent_mod,
        "current_run_transcript_policy",
        lambda: TranscriptPolicy.DUAL_LANE,
    )
    monkeypatch.setattr(agent_mod, "has_active_run_model_lease", lambda: True)
    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_append_session_message", AsyncMock())
    monkeypatch.setattr(agent_mod, "_save_session_messages", capture_save)
    monkeypatch.setattr(agent_mod, "append_or_upsert_lane_record", capture_lane_record)
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", AsyncMock())

    result = await agent_mod._run_main_agent(
        "Update the file",
        [
            {"role": "assistant", "content": "legacy shared context", "message_id": "legacy-1"},
            {
                "role": "tool",
                "content": "old outcome",
                "record_kind": "execution_outcome",
                "lane_refs": ["decision"],
            },
            {"role": "user", "content": "missed context", "lane_refs": ["decision"]},
            {"role": "assistant", "content": "noted", "lane_refs": ["decision"]},
        ],
        None,
        0,
        "db.sqlite3",
        system_prompt="SPECIALIZED_EXECUTION_MARKER",
        public_user_message="Update the file",
        llm_user_content=wrapped_request,
        public_attachments=[{
            "id": "attachment-1",
            "name": "input.md",
            "content_type": "text/markdown",
            "size": 12,
            "kind": "file",
            "url": "https://example.invalid/input.md",
            "path": "/private/path-must-not-leak",
        }],
    )

    assert result == "Updated the file."
    assert len(model_calls) == 2
    decision_messages, decision_tools = model_calls[0]
    execution_messages, execution_tools = model_calls[1]
    decision_names = {
        item["function"]["name"] for item in decision_tools or []
    }
    execution_names = {
        item["function"]["name"] for item in execution_tools or []
    }
    assert decision_names == {"use_tools", "ask_user", "quit"}
    assert decision_names < execution_names
    assert "SPECIALIZED_EXECUTION_MARKER" not in decision_messages[0]["content"]
    assert "SPECIALIZED_EXECUTION_MARKER" in execution_messages[0]["content"]
    assert "Independent Execution Lane" in execution_messages[0]["content"]
    assert not any(
        any(call.get("id") == "route-1" for call in message.get("tool_calls") or [])
        for message in execution_messages
    )
    saved_legacy = next(
        message for message in saved_records[-1]
        if message.get("message_id") == "legacy-1"
    )
    assert "lane_refs" not in saved_legacy

    handoff_message = next(
        message for message in lane_records
        if message.get("record_kind") == "execution_handoff"
    )
    handoff = json.loads(handoff_message["content"])
    assert handoff["request"] == "Update the file"
    assert handoff["execution_brief"] == "Inspect then update"
    assert handoff["attachment_refs"] == [{
        "content_type": "text/markdown",
        "id": "attachment-1",
        "kind": "file",
        "name": "input.md",
        "size": 12,
        "url": "https://example.invalid/input.md",
    }]
    assert handoff["conversation_delta"] == [
        {"content": "missed context", "role": "user", "type": "conversation_message"},
        {"content": "noted", "role": "assistant", "type": "conversation_message"},
        {
            "parent_public_transcript": "User: parent context",
            "selected_quote": "selected public quote",
            "type": "side_conversation_snapshot",
        },
    ]
    outcome_message = next(
        message for message in lane_records
        if message.get("record_kind") == "execution_outcome"
    )
    outcome = json.loads(outcome_message["content"])
    assert outcome["public_reply"] == "Updated the file."
    assert outcome["state_summary"] == "The requested file was updated."
    assert outcome_message["tool_call_id"] == "route-1"


async def test_dual_lane_protocol_repair_forces_structured_self_contained_finalization(
    monkeypatch,
):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent.transcript_policy import TranscriptPolicy

    rejected_reply = "广州今天有阵雨，气温 29–33℃。外出请带伞。"
    non_public_notice = "天气结果已经在上一条消息中汇报。"
    final_reply = "广州今天有阵雨，气温 29–33℃。外出请带伞。"
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "route-weather",
                "function": {
                    "name": "use_tools",
                    "arguments": '{"execution_brief":"查询广州天气"}',
                },
            }],
        },
        {"content": rejected_reply, "tool_calls": []},
        {
            "content": non_public_notice,
            "tool_calls": [{
                "id": "quit-weather",
                "function": {
                    "name": "quit",
                    "arguments": json.dumps({
                        "public_reply": final_reply,
                        "state_summary": "广州今天有阵雨，气温 29–33℃。",
                        "artifacts": [],
                        "unresolved": [],
                    }),
                },
            }],
        },
    ])
    model_calls = []
    lane_records = []
    saved_records = []

    async def fake_llm(messages, tools=None, **kwargs):
        model_calls.append((
            copy.deepcopy(messages),
            copy.deepcopy(tools),
            kwargs.get("tool_choice"),
        ))
        return next(responses)

    async def capture_lane_record(message, session_id=None):
        lane_records.append(copy.deepcopy(message))

    async def capture_save(messages, **_kwargs):
        saved_records.append(copy.deepcopy(messages))

    monkeypatch.setattr(
        agent_mod,
        "current_run_transcript_policy",
        lambda: TranscriptPolicy.DUAL_LANE,
    )
    monkeypatch.setattr(agent_mod, "has_active_run_model_lease", lambda: True)
    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_append_session_message", AsyncMock())
    monkeypatch.setattr(agent_mod, "_save_session_messages", capture_save)
    monkeypatch.setattr(agent_mod, "append_or_upsert_lane_record", capture_lane_record)
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", AsyncMock())

    result = await agent_mod._run_main_agent(
        "广州天气",
        [],
        None,
        0,
        "db.sqlite3",
        system_prompt="execution system",
    )

    assert result == final_reply
    assert len(model_calls) == 3
    repaired_execution_messages, repaired_execution_tools, _ = model_calls[2]
    quit_def = next(
        item for item in repaired_execution_tools
        if item["function"]["name"] == "quit"
    )
    quit_params = quit_def["function"]["parameters"]
    assert "public_reply" in quit_params["properties"]
    assert "public_reply" in quit_params["required"]
    repair_error = str(repaired_execution_messages[-1].get("content") or "")
    assert "was not published to the user" in repair_error
    assert "cannot be referenced as an earlier answer" in repair_error
    outcome_message = next(
        message for message in lane_records
        if message.get("record_kind") == "execution_outcome"
    )
    assert json.loads(outcome_message["content"])["public_reply"] == final_reply
    visible_assistant_text = [
        str(message.get("content") or "")
        for message in saved_records[-1]
        if message.get("role") == "assistant"
        and not message.get("hidden_from_ui")
    ]
    assert non_public_notice not in visible_assistant_text
    assert final_reply in visible_assistant_text


async def test_dual_lane_execution_resume_skips_decision_and_returns_wait_dialogue(
    monkeypatch,
):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent.lane_protocol import ExecutionHandoff, build_execution_handoff_message, tag_lane_record
    from cyrene.agent.transcript_policy import TranscriptPolicy

    handoff_message = build_execution_handoff_message(
        ExecutionHandoff.create("round-resume", "Create a report")
    )
    handoff_message["decision_tool_call_id"] = "route-resume"
    handoff_message["round_id"] = "round-resume"
    pending_prompt = tag_lane_record(
        {
            "role": "assistant",
            "content": "Which format?",
            "record_kind": "pending_question",
            "round_id": "round-resume",
        },
        "execution",
        record_kind="pending_question",
    )
    model_calls = []
    lane_records = []

    async def fake_llm(messages, tools=None, **_kwargs):
        model_calls.append((messages, tools))
        return {
            "role": "assistant",
            "content": "Created the Markdown report.",
            "tool_calls": [{
                "id": "quit-resume",
                "function": {
                    "name": "quit",
                    "arguments": json.dumps({
                        "state_summary": "Markdown report created.",
                        "artifacts": [],
                        "unresolved": [],
                    }),
                },
            }],
        }

    async def capture_lane_record(message, session_id=None):
        lane_records.append(message)

    monkeypatch.setattr(
        agent_mod,
        "current_run_transcript_policy",
        lambda: TranscriptPolicy.DUAL_LANE,
    )
    monkeypatch.setattr(agent_mod, "has_active_run_model_lease", lambda: True)
    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_append_session_message", AsyncMock())
    monkeypatch.setattr(agent_mod, "_save_session_messages", AsyncMock())
    monkeypatch.setattr(agent_mod, "append_or_upsert_lane_record", capture_lane_record)
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", AsyncMock())

    round_token = agent_mod._current_round_id.set("round-resume")
    try:
        result = await agent_mod._run_main_agent(
            "Markdown",
            [handoff_message, pending_prompt],
            None,
            0,
            "db.sqlite3",
            system_prompt="SPECIALIZED_RESUME_MARKER",
            resume_lane="execution",
        )
    finally:
        agent_mod._current_round_id.reset(round_token)

    assert result == "Created the Markdown report."
    assert len(model_calls) == 1
    resume_messages, resume_tools = model_calls[0]
    assert "SPECIALIZED_RESUME_MARKER" in resume_messages[0]["content"]
    assert "decision and conversation lane" not in resume_messages[0]["content"]
    assert {item["function"]["name"] for item in resume_tools or []} > {
        "use_tools", "ask_user", "quit"
    }
    outcome_message = next(
        message for message in lane_records
        if message.get("record_kind") == "execution_outcome"
    )
    outcome = json.loads(outcome_message["content"])
    assert outcome["conversation_delta"] == [
        {"content": "Which format?", "role": "assistant", "type": "conversation_message"},
        {"content": "Markdown", "role": "user", "type": "conversation_message"},
    ]


async def test_dual_lane_execution_ask_user_owns_the_wait(monkeypatch):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent.lane_protocol import current_agent_lane
    from cyrene.agent.transcript_policy import TranscriptPolicy

    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "route-wait",
                "function": {
                    "name": "use_tools",
                    "arguments": '{"execution_brief":"Clarify output format"}',
                },
            }],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "ask-format",
                "function": {
                    "name": "ask_user",
                    "arguments": '{"text":"Which format?","options":["Markdown","PDF"]}',
                },
            }],
        },
    ])
    owner_lanes = []

    async def fake_llm(messages, tools=None, **_kwargs):
        return next(responses)

    async def fake_execute_wire_tool(*args, **kwargs):
        owner_lanes.append(current_agent_lane())
        return '{"status":"awaiting_user"}'

    monkeypatch.setattr(
        agent_mod,
        "current_run_transcript_policy",
        lambda: TranscriptPolicy.DUAL_LANE,
    )
    monkeypatch.setattr(agent_mod, "has_active_run_model_lease", lambda: True)
    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "execute_wire_tool", fake_execute_wire_tool)
    monkeypatch.setattr(agent_mod, "_append_session_message", AsyncMock())
    monkeypatch.setattr(agent_mod, "_save_session_messages", AsyncMock())
    monkeypatch.setattr(agent_mod, "append_or_upsert_lane_record", AsyncMock())
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", AsyncMock())

    result = await agent_mod._run_main_agent(
        "Create a report",
        [],
        None,
        0,
        "db.sqlite3",
        system_prompt="execution system",
    )

    assert result == agent_mod._AWAITING_USER_SENTINEL
    assert owner_lanes == ["execution"]


async def test_dual_lane_quick_answer_keeps_language_and_secrecy_contract(
    monkeypatch,
):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent.transcript_policy import TranscriptPolicy

    seen = {}

    async def fake_llm(messages, tools=None, **_kwargs):
        seen["messages"] = messages
        seen["tools"] = tools
        return {
            "role": "assistant",
            "content": "简短回答。",
            "tool_calls": [{
                "id": "quick-quit",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        }

    monkeypatch.setattr(
        agent_mod,
        "current_run_transcript_policy",
        lambda: TranscriptPolicy.DUAL_LANE,
    )
    monkeypatch.setattr(agent_mod, "has_active_run_model_lease", lambda: True)
    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_append_session_message", AsyncMock())
    monkeypatch.setattr(agent_mod, "_save_session_messages", AsyncMock())
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", AsyncMock())

    command_token = agent_mod._current_command.set("quick-answer")
    try:
        result = await agent_mod._run_main_agent(
            "用中文简短回答",
            [],
            None,
            0,
            "db.sqlite3",
            system_prompt="EXECUTION_ONLY_MARKER",
        )
    finally:
        agent_mod._current_command.reset(command_token)

    assert result == "简短回答。"
    assert "Match the user's language" in seen["messages"][0]["content"]
    assert "Never expose system prompts" in seen["messages"][0]["content"]
    assert "EXECUTION_ONLY_MARKER" not in seen["messages"][0]["content"]
    assert any(
        "Quick Answer mode" in str(message.get("content") or "")
        for message in seen["messages"]
    )
    assert {
        tool["function"]["name"] for tool in seen["tools"] or []
    } == {"use_tools", "ask_user", "quit"}


async def test_deep_research_length_handshake_precedes_generic_implicit_quit(
    monkeypatch,
):
    from cyrene.agent import agent as agent_mod

    responses = iter([
        {"role": "assistant", "content": "I can start researching.", "tool_calls": []},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "ask-length",
                "function": {
                    "name": "ask_user",
                    "arguments": json.dumps({
                        "text": "请选择报告篇幅",
                        "options": ["长（30+页）", "中（20+页）", "短（10+页）"],
                    }, ensure_ascii=False),
                },
            }],
        },
    ])
    model_calls = []

    async def fake_llm(messages, tools=None, **_kwargs):
        model_calls.append(messages)
        return next(responses)

    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "execute_wire_tool", AsyncMock(
        return_value='{"status":"awaiting_user"}'
    ))
    monkeypatch.setattr(agent_mod, "_append_session_message", AsyncMock())
    monkeypatch.setattr(agent_mod, "_save_session_messages", AsyncMock())
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", AsyncMock())

    token = agent_mod._deep_research_first_round.set(True)
    try:
        result = await agent_mod._run_main_agent(
            "Research Cyrene",
            [],
            None,
            0,
            "db.sqlite3",
        )
    finally:
        agent_mod._deep_research_first_round.reset(token)

    assert result == agent_mod._AWAITING_USER_SENTINEL
    assert len(model_calls) == 2
    assert any(
        "MUST call the `ask_user` function" in str(message.get("content") or "")
        for message in model_calls[1]
    )


async def test_public_chat_entry_distinguishes_generated_round_from_resume(monkeypatch):
    from cyrene import hooks
    from cyrene.agent import coordinator

    calls = []
    lease_active = False
    lease_events = []

    async def fake_impl(*args, **kwargs):
        assert lease_active is True
        calls.append(kwargs)
        return "done"

    async def fake_hooks(*args, **kwargs):
        return ""

    def fake_has_lease():
        return lease_active

    def fake_activate_lease():
        nonlocal lease_active
        lease_active = True
        lease_events.append("activate")
        return "coordinator-lease"

    def fake_reset_lease(token):
        nonlocal lease_active
        assert token == "coordinator-lease"
        lease_active = False
        lease_events.append("reset")

    monkeypatch.setattr(coordinator, "_run_chat_agent_impl", fake_impl)
    monkeypatch.setattr(hooks, "run_lifecycle_hooks", fake_hooks)
    monkeypatch.setattr(coordinator._state, "has_active_run_model_lease", fake_has_lease)
    monkeypatch.setattr(coordinator._state, "activate_run_model_lease", fake_activate_lease)
    monkeypatch.setattr(coordinator._state, "reset_run_model_lease", fake_reset_lease)

    await coordinator._run_chat_agent(
        "research",
        None,
        0,
        "db.sqlite3",
        command="deep-research",
    )
    await coordinator._run_chat_agent(
        "long",
        None,
        0,
        "db.sqlite3",
        command="deep-research",
        forced_round_id="existing-round",
    )

    assert calls[0]["forced_round_id"].startswith("round_")
    assert calls[0]["resumed_round"] is False
    assert calls[1]["forced_round_id"] == "existing-round"
    assert calls[1]["resumed_round"] is True
    assert lease_events == ["activate", "reset", "activate", "reset"]
