from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from cyrene.core import AgentSession, AgentSessionEvent
from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry
from cyrene.workbench.chat.chat_runs import ChatRun
from cyrene.workbench.chat.run_timeline import RunTimeline
from cyrene.workbench.core_adapter.bridge import WorkbenchSessionBridge, _PublisherBinding, _SessionEventStream


def event(sequence, kind, run_id="attempt-1", tree_id="chat", **data):
    return AgentSessionEvent(
        sequence=sequence, type=kind, tree_id=tree_id, run_id=run_id,
        node_id=f"node-{sequence}", time=datetime.now(timezone.utc), data=data,
    )


@pytest.mark.asyncio
async def test_operation_owns_continuation_events_and_keeps_transport_run_identity():
    chat_run = ChatRun("chat", {"type": "ack"})
    session = SimpleNamespace(tree=SimpleNamespace(id="chat"), session_driver=None)
    stream = _SessionEventStream()
    # Restore may already emit before the async Workbench operation binds.
    stream.receive(event(1, "assistant.reasoning.started", sourceId="thought-1"))
    stream.receive(event(2, "assistant.reasoning.delta", sourceId="thought-1", delta="Checking the input"))
    binding = _PublisherBinding(session, chat_run.publish, run_id="attempt-1",
                                replay=False, event_stream=stream)
    stream.receive(event(3, "assistant.reasoning.done", sourceId="thought-1", response="Checking the input"))
    # Continuation/reflection has a different execution identity in the same
    # exclusively owned session; a child/foreign tree is not part of this feed.
    stream.receive(event(4, "assistant.tool_calls", run_id="reflection-2",
                         tool_calls=[{"id": "read-1", "name": "Read", "arguments": {}}]))
    stream.receive(event(5, "tool.completed", run_id="reflection-2",
                         call_id="read-1", name="Read", success=True, value="ok"))
    stream.receive(event(6, "assistant.tool_calls", tree_id="other-chat",
                         tool_calls=[{"id": "foreign", "name": "Bash"}]))
    await binding.close()
    records = chat_run.timeline.messages()
    assert any(m.get("reasoning") == "Checking the input" for m in records)
    traces = [entry for m in records for entry in m.get("trace", [])]
    assert [entry["toolCallId"] for entry in traces] == ["read-1"]
    assert traces[0]["status"] == "completed"
    assert {e["runId"] for e in chat_run.events} == {chat_run.run_id}
    assert {e.get("executionRunId") for e in chat_run.events[1:]} == {"attempt-1", "reflection-2"}
    replay = RunTimeline(chat_run.run_id)
    for published in chat_run.events:
        if "timeline" in published:
            replay.ingest(published["timeline"])
    assert replay.messages() == records
    count = len(chat_run.events)
    stream.receive(event(7, "assistant.reasoning.delta", delta="after close"))
    stream.close()
    assert len(chat_run.events) == count


@pytest.mark.asyncio
async def test_bridge_captures_output_during_session_open(monkeypatch, tmp_path):
    original_restore = AgentSession._restore

    def restore(session):
        original_restore(session)
        session._emit_event("assistant.reasoning.delta", run_id="attempt-1",
                            data={"sourceId": "opening-thought", "delta": "Restoring execution"})

    monkeypatch.setattr(AgentSession, "_restore", restore)

    async def model(_arguments, _context):
        return {"content": "done", "tool_calls": []}

    registry = PluginRegistry()
    registry.register_pack(PluginPack("model", "model", (
        Plugin("MiniMax", "model", {"type": "object"}, model, kind="model"),
    )), source="test")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    bridge = WorkbenchSessionBridge.open(
        tmp_path / "state", tmp_path / "workspace", plugins,
        registry=registry, load_plugins=False, model_plugin="MiniMax", chat_id="chat",
    )
    published = []
    try:
        result = await bridge.submit_result("continue", run_id="attempt-1", publish=published.append)
        assert result.text == "done"
        assert [e.get("delta") for e in published if e["type"] == "reasoning_delta"] == ["Restoring execution"]
        assert any(e["type"] == "reply_done" for e in published)
    finally:
        bridge.close()


@pytest.mark.asyncio
async def test_pending_and_durable_replay_do_not_import_previous_runs():
    received = []
    stream = _SessionEventStream()
    stream.receive(event(1, "assistant.reasoning.delta", run_id="old", delta="old"))
    current = event(2, "assistant.reasoning.delta", delta="current")
    stream.receive(current)
    session = SimpleNamespace(
        tree=SimpleNamespace(id="chat"), session_driver=None,
        events=lambda: [event(1, "assistant.reasoning.delta", run_id="old", delta="old"), current],
    )
    binding = _PublisherBinding(session, received.append, run_id="attempt-1",
                                replay=True, event_stream=stream)
    with pytest.raises(RuntimeError, match="already owned"):
        _PublisherBinding(session, received.append, run_id="other",
                          replay=False, event_stream=stream)
    await binding.close()
    assert [e["delta"] for e in received] == ["current"]
    stream.close()
