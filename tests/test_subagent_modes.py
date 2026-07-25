"""Regression coverage for execution/discussion subagent runtime modes."""

from __future__ import annotations

import json

import pytest


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _patch_runtime(monkeypatch, responses, *, tool_result="ok"):
    from cyrene.agent import state as agent_state
    from cyrene import subagent
    import cyrene.tooling as tooling

    calls = []
    response_iter = iter(responses)

    async def fake_call_llm(messages, tools=None, max_tokens=None, **kwargs):
        calls.append({
            "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
            "tools": json.loads(json.dumps(tools, ensure_ascii=False)),
        })
        return next(response_iter)

    async def fake_execute(name, args, *_args, **_kwargs):
        if callable(tool_result):
            return tool_result(name, args)
        return tool_result

    async def fake_wait(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(agent_state, "_call_llm", fake_call_llm)
    monkeypatch.setattr(tooling, "execute_wire_tool", fake_execute)
    monkeypatch.setattr(subagent, "wait_for_others", fake_wait)
    return calls


def _patch_limits(monkeypatch, **overrides):
    from cyrene.runtime import settings_store

    original_get = settings_store.get

    def fake_get(name, default=None):
        if name in overrides:
            return overrides[name]
        return original_get(name, default)

    monkeypatch.setattr(settings_store, "get", fake_get)


@pytest.mark.asyncio
async def test_execution_mode_is_not_bounded_by_main_agent_round_limit(monkeypatch):
    from cyrene import subagent
    from cyrene.agent import state as agent_state

    responses = [
        {"content": "", "tool_calls": [_tool_call("r1", "Read", {"path": "a.txt"})]},
        {"content": "", "tool_calls": [_tool_call("r2", "Read", {"path": "b.txt"})]},
        {
            "content": "",
            "tool_calls": [_tool_call("q1", "quit", {
                "reply": "execution complete",
                "completion_status": "completed",
                "criteria_evidence": [{
                    "criterion": "Both files inspected",
                    "evidence": "Read results were returned for a.txt and b.txt.",
                }],
            })],
        },
    ]
    calls = _patch_runtime(
        monkeypatch,
        responses,
        tool_result=lambda _name, args: f"contents:{args.get('path')}",
    )
    monkeypatch.setattr(agent_state, "_get_max_tool_rounds", lambda: 1)

    await subagent.clear()
    await subagent.register(
        "worker",
        "read both files",
        mode="execution",
        success_criteria=["Both files inspected"],
    )
    result = await subagent._run_subagent(
        "worker",
        "read both files",
        None,
        0,
        "db.sqlite3",
    )

    snapshot = await subagent.get_snapshot()
    assert result == "execution complete"
    assert len(calls) == 3
    assert snapshot["worker"]["status"] == "done"
    assert snapshot["worker"]["mode"] == "execution"
    assert snapshot["worker"]["metrics"]["tool_calls"] == 2


@pytest.mark.asyncio
async def test_execution_checkpoint_rechecks_success_without_stopping(monkeypatch):
    from cyrene import subagent

    _patch_limits(
        monkeypatch,
        subagent_execution_no_progress_turns=3,
        subagent_execution_max_tool_calls=200,
        subagent_execution_max_wall_seconds=1800,
        subagent_execution_checkpoint_calls=2,
    )
    responses = [
        {"content": "", "tool_calls": [_tool_call("r1", "Read", {"path": "a.txt"})]},
        {"content": "", "tool_calls": [_tool_call("r2", "Read", {"path": "b.txt"})]},
        {"content": "", "tool_calls": [_tool_call("r3", "Read", {"path": "c.txt"})]},
        {"content": "", "tool_calls": [_tool_call("q1", "quit", {"reply": "done"})]},
    ]
    calls = _patch_runtime(
        monkeypatch,
        responses,
        tool_result=lambda _name, args: f"contents:{args.get('path')}",
    )

    await subagent.clear()
    await subagent.register("worker", "inspect three files", mode="execution")
    result = await subagent._run_subagent(
        "worker",
        "inspect three files",
        None,
        0,
        "db.sqlite3",
    )

    assert result == "done"
    assert any(
        str(message.get("content") or "").startswith("[Execution Checkpoint]")
        for message in calls[2]["messages"]
    )


@pytest.mark.asyncio
async def test_execution_mode_stops_after_repeated_no_progress(monkeypatch):
    from cyrene import subagent

    _patch_limits(
        monkeypatch,
        subagent_execution_no_progress_turns=2,
        subagent_execution_max_tool_calls=200,
        subagent_execution_max_wall_seconds=1800,
        subagent_execution_checkpoint_calls=20,
    )
    repeated_read = {"path": "unchanged.txt"}
    responses = [
        {"content": "", "tool_calls": [_tool_call("r1", "Read", repeated_read)]},
        {"content": "", "tool_calls": [_tool_call("r2", "Read", repeated_read)]},
        {"content": "", "tool_calls": [_tool_call("r3", "Read", repeated_read)]},
        {
            "content": "",
            "tool_calls": [_tool_call("q1", "quit", {"reply": "partial evidence retained"})],
        },
    ]
    calls = _patch_runtime(monkeypatch, responses, tool_result="unchanged contents")

    await subagent.clear()
    await subagent.register("worker", "inspect file", mode="execution")
    result = await subagent._run_subagent(
        "worker",
        "inspect file",
        None,
        0,
        "db.sqlite3",
    )

    snapshot = await subagent.get_snapshot()
    assert len(calls) == 4
    assert result == "partial evidence retained"
    assert snapshot["worker"]["status"] == "incomplete"
    assert snapshot["worker"]["outcome"] == "partial"
    assert snapshot["worker"]["stop_reason"] == "execution_no_progress"
    assert snapshot["worker"]["metrics"]["no_progress_turns"] == 2
    assert [item["function"]["name"] for item in calls[-1]["tools"]] == ["quit"]


@pytest.mark.asyncio
async def test_execution_absolute_safety_fuse_is_resource_exhausted(monkeypatch):
    from cyrene import subagent

    _patch_limits(
        monkeypatch,
        subagent_execution_no_progress_turns=3,
        subagent_execution_max_tool_calls=1,
        subagent_execution_max_wall_seconds=1800,
        subagent_execution_checkpoint_calls=20,
    )
    responses = [
        {"content": "", "tool_calls": [_tool_call("r1", "Read", {"path": "a.txt"})]},
        {
            "content": "",
            "tool_calls": [_tool_call("q1", "quit", {"reply": "safety summary"})],
        },
    ]
    calls = _patch_runtime(monkeypatch, responses, tool_result="new evidence")

    await subagent.clear()
    await subagent.register("worker", "inspect files", mode="execution")
    result = await subagent._run_subagent(
        "worker",
        "inspect files",
        None,
        0,
        "db.sqlite3",
    )

    snapshot = await subagent.get_snapshot()
    assert result == "safety summary"
    assert snapshot["worker"]["status"] == "incomplete"
    assert snapshot["worker"]["outcome"] == "resource_exhausted"
    assert snapshot["worker"]["stop_reason"] == "execution_tool_call_safety_limit"
    assert [item["function"]["name"] for item in calls[-1]["tools"]] == ["quit"]


@pytest.mark.asyncio
async def test_discussion_mode_enforces_message_limit_and_then_summarizes(monkeypatch):
    from cyrene import subagent

    _patch_limits(
        monkeypatch,
        subagent_discussion_max_rounds=5,
        subagent_discussion_max_messages_per_agent=1,
        subagent_discussion_max_total_messages=20,
        subagent_discussion_max_message_chars=2000,
        subagent_discussion_max_wall_seconds=600,
        subagent_discussion_max_tool_calls=50,
        subagent_discussion_no_new_info_rounds=2,
    )
    send_point = {
        "operation": "invoke",
        "capability_id": "subagent.send_message",
        "arguments": {"to": "bob", "content": "The API boundary should own retries."},
    }
    responses = [
        {
            "content": "",
            "tool_calls": [_tool_call("m1", "subagent_tools", send_point)],
        },
        {
            "content": "",
            "tool_calls": [_tool_call("q1", "quit", {"reply": "Final discussion position."})],
        },
    ]
    calls = _patch_runtime(monkeypatch, responses, tool_result="Message sent to bob.")

    await subagent.clear()
    await subagent.register(
        "alice",
        "discuss retry ownership",
        round_id="round_discussion",
        role="participant",
        mode="discussion",
    )
    await subagent.register(
        "bob",
        "discuss retry ownership",
        round_id="round_discussion",
        role="participant",
        mode="discussion",
    )
    result = await subagent._run_subagent(
        "alice",
        "discuss retry ownership",
        None,
        0,
        "db.sqlite3",
        role="participant",
        mode="discussion",
    )

    snapshot = await subagent.get_snapshot(round_id="round_discussion")
    assert result.startswith("Final discussion position.")
    assert "[to bob]" in result
    assert len(calls) == 2
    assert snapshot["alice"]["status"] == "done"
    assert snapshot["alice"]["stop_reason"] == "discussion_message_limit_per_agent"
    assert snapshot["alice"]["metrics"]["discussion_messages"] == 1
    assert any(
        "[活跃子 agent]" in str(message.get("content") or "")
        for message in calls[0]["messages"]
    )
    assert [item["function"]["name"] for item in calls[-1]["tools"]] == ["quit"]


@pytest.mark.asyncio
async def test_discussion_message_length_is_separate_from_message_count(monkeypatch):
    from cyrene import subagent

    _patch_limits(
        monkeypatch,
        subagent_discussion_max_rounds=5,
        subagent_discussion_max_messages_per_agent=4,
        subagent_discussion_max_total_messages=20,
        subagent_discussion_max_message_chars=100,
        subagent_discussion_max_wall_seconds=600,
        subagent_discussion_max_tool_calls=50,
        subagent_discussion_no_new_info_rounds=2,
    )
    too_long = {
        "operation": "invoke",
        "capability_id": "subagent.send_message",
        "arguments": {"to": "bob", "content": "x" * 101},
    }
    short_enough = {
        "operation": "invoke",
        "capability_id": "subagent.send_message",
        "arguments": {"to": "bob", "content": "short"},
    }
    responses = [
        {"content": "", "tool_calls": [_tool_call("m1", "subagent_tools", too_long)]},
        {"content": "", "tool_calls": [_tool_call("m2", "subagent_tools", short_enough)]},
        {"content": "", "tool_calls": [_tool_call("q1", "quit", {"reply": "done"})]},
    ]
    _patch_runtime(monkeypatch, responses, tool_result="Message sent to bob.")

    await subagent.clear()
    await subagent.register(
        "alice",
        "discuss",
        round_id="round_length",
        role="participant",
        mode="discussion",
    )
    result = await subagent._run_subagent(
        "alice",
        "discuss",
        None,
        0,
        "db.sqlite3",
        role="participant",
        mode="discussion",
    )

    snapshot = await subagent.get_snapshot(round_id="round_length")
    assert result.startswith("done")
    assert snapshot["alice"]["metrics"]["discussion_messages"] == 1
    tool_results = [
        message["content"]
        for message in await subagent.get_raw_messages("alice")
        if message.get("role") == "tool"
    ]
    assert any("discussion_message_too_long" in result for result in tool_results)


def test_spawn_subagent_schema_exposes_mode_and_success_criteria():
    from cyrene.tooling.native_definitions import get_native_tool_def

    properties = get_native_tool_def("spawn_subagent")["function"]["parameters"]["properties"]
    assert properties["mode"]["enum"] == ["execution", "discussion"]
    assert properties["success_criteria"]["items"]["type"] == "string"
    assert properties["max_messages"]["minimum"] == 1
    assert properties["discussion_id"]["type"] == "string"


@pytest.mark.asyncio
async def test_execution_worker_cannot_bypass_discussion_budget(monkeypatch):
    from cyrene import subagent

    communication = {
        "operation": "invoke",
        "capability_id": "subagent.send_message",
        "arguments": {"to": "peer", "content": "should not be delivered"},
    }
    responses = [
        {"content": "", "tool_calls": [_tool_call("m1", "subagent_tools", communication)]},
        {"content": "", "tool_calls": [_tool_call("q1", "quit", {"reply": "reported to parent"})]},
    ]
    executed = []

    def tool_result(name, args):
        executed.append((name, args))
        return "Message sent to peer."

    _patch_runtime(monkeypatch, responses, tool_result=tool_result)
    await subagent.clear()
    await subagent.register("worker", "independent work", mode="execution")

    result = await subagent._run_subagent(
        "worker", "independent work", None, 0, "db.sqlite3"
    )

    assert result == "reported to parent"
    assert executed == []
    raw = await subagent.get_raw_messages("worker")
    assert any(
        "communication_requires_discussion_mode" in str(message.get("content") or "")
        for message in raw
        if message.get("role") == "tool"
    )
    assert "[to peer]" not in result


@pytest.mark.asyncio
async def test_terminal_quit_pairs_every_tool_call_in_mixed_batch(monkeypatch):
    from cyrene import subagent

    responses = [{
        "content": "",
        "tool_calls": [
            _tool_call("read1", "Read", {"path": "should-not-run.txt"}),
            _tool_call("quit1", "quit", {"reply": "done"}),
        ],
    }]
    executed = []
    _patch_runtime(
        monkeypatch,
        responses,
        tool_result=lambda name, args: executed.append((name, args)) or "unexpected",
    )
    await subagent.clear()
    await subagent.register("worker", "finish", mode="execution")

    assert await subagent._run_subagent(
        "worker", "finish", None, 0, "db.sqlite3"
    ) == "done"
    assert executed == []
    tool_results = [
        message
        for message in await subagent.get_raw_messages("worker")
        if message.get("role") == "tool"
    ]
    assert {message["tool_call_id"] for message in tool_results} == {
        "read1",
        "quit1",
    }


@pytest.mark.asyncio
async def test_duplicate_active_agent_id_is_rejected_without_overwrite():
    from cyrene import subagent

    await subagent.clear()
    assert await subagent.register("same", "first", session_id="session-a")
    assert not await subagent.register("same", "second", session_id="session-b")
    snapshot = await subagent.get_snapshot()
    assert snapshot["same"]["task"] == "first"


@pytest.mark.asyncio
async def test_reactivation_renews_execution_lease_but_keeps_lifetime_metrics():
    from cyrene import subagent

    await subagent.clear()
    await subagent.register("worker", "first lease", mode="execution")
    await subagent._update_metrics(
        "worker",
        tool_calls=87,
        lease_tool_calls=200,
        estimated_cost_usd=2.5,
        lease_estimated_cost_usd=1.25,
        no_progress_turns=3,
    )
    await subagent.mark_incomplete(
        "worker",
        "partial",
        reason="execution_tool_call_safety_limit",
        outcome="resource_exhausted",
    )

    assert await subagent.reactivate("worker")
    metrics = (await subagent.get_snapshot())["worker"]["metrics"]
    assert metrics["tool_calls"] == 87
    assert metrics["estimated_cost_usd"] == 2.5
    assert metrics["lease_tool_calls"] == 0
    assert metrics["lease_estimated_cost_usd"] == 0.0
    assert metrics["no_progress_turns"] == 0


@pytest.mark.asyncio
async def test_cancel_and_timeout_have_explicit_outcomes():
    from cyrene import subagent

    await subagent.clear()
    await subagent.register("cancelled", "task", round_id="r1")
    await subagent.cancel_subagent_tasks("r1")
    cancelled = (await subagent.get_snapshot())["cancelled"]
    assert cancelled["outcome"] == "cancelled"
    assert cancelled["stop_reason"] == "user_cancelled"

    await subagent.register("timed", "task", round_id="r2")
    await subagent.timeout_subagents(["timed"], reason="deadline")
    timed = (await subagent.get_snapshot())["timed"]
    assert timed["status"] == "timeout"
    assert timed["outcome"] == "resource_exhausted"
    assert timed["stop_reason"] == "parent_monitor_deadline"


@pytest.mark.asyncio
async def test_model_message_override_cannot_raise_admin_discussion_cap(monkeypatch):
    from cyrene import subagent

    _patch_limits(monkeypatch, subagent_discussion_max_messages_per_agent=1)
    responses = [
        {"content": "", "tool_calls": [_tool_call("m1", "subagent_tools", {
            "operation": "invoke",
            "capability_id": "subagent.send_message",
            "arguments": {"to": "bob", "content": "one point"},
        })]},
        {"content": "", "tool_calls": [_tool_call("q1", "quit", {"reply": "summary"})]},
    ]
    calls = _patch_runtime(monkeypatch, responses, tool_result="Message sent to bob.")
    await subagent.clear()
    await subagent.register(
        "alice",
        "discuss",
        round_id="r",
        discussion_id="d",
        role="participant",
        discussion_max_messages=10,
    )
    await subagent.register(
        "bob",
        "discuss",
        round_id="r",
        discussion_id="d",
        role="participant",
    )

    await subagent._run_subagent(
        "alice", "discuss", None, 0, "db.sqlite3", role="participant"
    )
    assert len(calls) == 2
    assert (await subagent.get_snapshot())["alice"]["metrics"]["discussion_messages"] == 1


@pytest.mark.asyncio
async def test_discussion_state_is_shared_by_discussion_id_and_isolated_between_them():
    from cyrene import subagent

    await subagent.clear()
    for agent_id, discussion_id, session_id in (
        ("mod-a", "discussion-a", "session-a"),
        ("peer-a", "discussion-a", "session-a"),
        ("mod-b", "discussion-b", "session-a"),
        ("cross-session", "discussion-a", "session-b"),
    ):
        await subagent.register(
            agent_id,
            "topic",
            round_id="same-parent-round",
            discussion_id=discussion_id,
            session_id=session_id,
            role="moderator" if agent_id.startswith("mod") else "participant",
        )

    assert await subagent._claim_discussion_message_slot(
        "mod-a", max_per_agent=4, max_total=20
    ) == (True, "")
    await subagent._record_discussion_delivery("mod-a", "new argument")
    state_a = await subagent._get_discussion_state("peer-a")
    state_b = await subagent._get_discussion_state("mod-b")
    assert state_a["rounds"] == 1
    assert state_a["messages_total"] == 1
    assert state_b["rounds"] == 0
    assert state_b["messages_total"] == 0
    assert await subagent.list_discussion_peer_ids("mod-a") == ["peer-a"]


@pytest.mark.asyncio
async def test_missing_success_evidence_requires_correction_before_completion(monkeypatch):
    from cyrene import subagent

    responses = [
        {"content": "", "tool_calls": [
            _tool_call("read1", "Read", {"path": "artifact.txt"}),
            _tool_call("quit1", "quit", {
                "reply": "premature",
                "completion_status": "completed",
                "criteria_evidence": [],
            }),
        ]},
        {"content": "", "tool_calls": [_tool_call("quit2", "quit", {
            "reply": "verified",
            "completion_status": "completed",
            "criteria_evidence": [{
                "criterion": "Artifact verified",
                "evidence": "artifact.txt exists and validation passed",
            }],
        })]},
    ]
    executed = []
    _patch_runtime(
        monkeypatch,
        responses,
        tool_result=lambda name, args: executed.append((name, args)) or "unexpected",
    )
    await subagent.clear()
    await subagent.register(
        "worker",
        "produce artifact",
        mode="execution",
        success_criteria=["Artifact verified"],
    )

    result = await subagent._run_subagent(
        "worker", "produce artifact", None, 0, "db.sqlite3"
    )
    assert result == "verified"
    assert executed == []
    raw = await subagent.get_raw_messages("worker")
    first_batch_results = {
        message["tool_call_id"]: message["content"]
        for message in raw
        if message.get("role") == "tool"
        and message.get("tool_call_id") in {"read1", "quit1"}
    }
    assert set(first_batch_results) == {"read1", "quit1"}
    assert "completion_evidence_missing" in first_batch_results["quit1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("completion_status", ["partial", "blocked"])
async def test_execution_worker_can_report_honest_non_success_outcome(
    monkeypatch,
    completion_status,
):
    from cyrene import subagent

    _patch_runtime(monkeypatch, [{
        "content": "",
        "tool_calls": [_tool_call("quit1", "quit", {
            "reply": "The dependency is unavailable.",
            "completion_status": completion_status,
        })],
    }])
    await subagent.clear()
    await subagent.register(
        "worker",
        "produce artifact",
        mode="execution",
        success_criteria=["Artifact verified"],
    )

    await subagent._run_subagent(
        "worker", "produce artifact", None, 0, "db.sqlite3"
    )
    snapshot = (await subagent.get_snapshot())["worker"]
    assert snapshot["status"] == "incomplete"
    assert snapshot["outcome"] == completion_status
    assert snapshot["stop_reason"] == f"subagent_reported_{completion_status}"


def test_execution_context_compaction_preserves_contract():
    from cyrene import subagent

    messages = [
        {"role": "system", "content": "TASK CONTRACT: keep this"},
        *[
            {"role": "user", "content": f"old evidence {index} " + ("x" * 300)}
            for index in range(20)
        ],
        {"role": "user", "content": "recent acceptance evidence"},
    ]
    compacted, before, after, changed = subagent._compact_subagent_context(
        messages,
        max_context_tokens=500,
    )
    assert changed
    assert compacted[0] == messages[0]
    assert any("recent acceptance evidence" in str(item.get("content") or "") for item in compacted)
    assert after < before


@pytest.mark.asyncio
async def test_execution_cost_fuse_requests_finalization(monkeypatch):
    from cyrene import subagent

    _patch_limits(
        monkeypatch,
        subagent_execution_max_cost_usd=0.001,
        subagent_execution_max_tool_calls=200,
        subagent_execution_max_wall_seconds=1800,
        subagent_execution_no_progress_turns=3,
    )
    responses = [
        {
            "model": "gpt-5.5",
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 10,
                "total_tokens": 1010,
            },
            "content": "",
            "tool_calls": [_tool_call("r1", "Read", {"path": "a.txt"})],
        },
        {"content": "", "tool_calls": [_tool_call("q1", "quit", {"reply": "cost summary"})]},
    ]
    calls = _patch_runtime(monkeypatch, responses, tool_result="new evidence")
    await subagent.clear()
    await subagent.register("worker", "inspect", mode="execution")

    assert await subagent._run_subagent(
        "worker", "inspect", None, 0, "db.sqlite3"
    ) == "cost summary"
    snapshot = (await subagent.get_snapshot())["worker"]
    assert snapshot["outcome"] == "resource_exhausted"
    assert snapshot["stop_reason"] == "execution_cost_safety_limit"
    assert snapshot["metrics"]["estimated_cost_usd"] > 0.001
    assert [item["function"]["name"] for item in calls[-1]["tools"]] == ["quit"]
