"""Wire-size, durable replay and slow-consumer contracts for timeline v2."""
import asyncio
import json
import subprocess

import pytest

from cyrene.workbench.chat import chat_runs
from cyrene.workbench.chat.run_timeline import RunTimeline


def frontend_replay(patches):
    from conftest import frontend_module_source
    source = frontend_module_source("features/chat/runtime-timeline.jsx")
    helpers = "function wbcConfirmOptimisticMessage(" + source.split("function wbcConfirmOptimisticMessage(", 1)[1].split("export {", 1)[0]
    script = "eval(" + json.dumps(helpers) + "); let runtime = {}; const states = [];\n"
    script += "for (const patch of " + json.dumps(patches) + ") { runtime = wbcApplyTimeline(runtime, patch); states.push(runtime.timeline.messages); }\n"
    script += "process.stdout.write(JSON.stringify(states));"
    return json.loads(subprocess.run(["node"], input=script, capture_output=True, text=True, check=True).stdout)


def test_text_wire_size_scales_linearly_and_frontend_keeps_unicode_and_identity():
    timeline = RunTimeline("unicode")
    patches = [timeline.apply({"type": "reply_start"})]
    first_id = timeline.messages()[0]["id"]
    for _ in range(1000):
        patches.append(timeline.apply({"type": "reply_delta", "delta": "中文🙂e\u0301"}))
    first_half = sum(len(json.dumps(p).encode()) for p in patches[1:501])
    second_half = sum(len(json.dumps(p).encode()) for p in patches[501:])
    assert second_half < first_half * 1.1
    assert all(not p["messages"] for p in patches[1:])
    # Feed real server operations to the real frontend reducer, including a
    # repeated frame and a final corrected snapshot on the same identity.
    patches.append(patches[-1])
    patches.append(timeline.apply({"type": "reply_done", "response": "最终修订🙂"}))
    states = frontend_replay(patches)
    assert states[-2] == states[-3]
    assert states[-2][0]["content"] == "中文🙂e\u0301" * 1000
    assert states[-1] == timeline.messages()
    assert states[-1][0]["id"] == first_id


def test_reasoning_updates_do_not_repeat_tool_payload_and_restore_metadata():
    timeline = RunTimeline("reasoning")
    patches = [timeline.apply({"type": "tool.started", "toolCallId": "t", "args": {"body": "x" * 100000}})]
    patches.append(timeline.apply({"type": "reasoning_delta", "delta": "start"}))
    patches.append(timeline.apply({"type": "reasoning_done"}))
    patches.append(timeline.apply({"type": "tool.completed", "toolCallId": "t"}))
    patches.append(timeline.apply({"type": "reasoning_delta", "delta": "继续🙂"}))
    assert len(json.dumps(patches[-1])) < 1000
    assert "trace" not in json.dumps(patches[-1])
    states = frontend_replay(patches)
    assert states[-1] == timeline.messages()
    assert states[-1][0]["status"] == "running"
    assert "endedAt" not in states[-1][0]


@pytest.mark.asyncio
async def test_trimmed_durable_deltas_survive_retry_and_process_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_runs, "_MAX_BUFFER_EVENTS", 5)
    path = str(tmp_path / "replay.sqlite3")
    store = chat_runs.ChatRunEventStore(path)
    run = chat_runs.ChatRun("chat", {"type": "ack"}, max_buffer=100)
    store.create(run)
    await run.publish({"type": "reply_start"})
    for _ in range(20):
        await run.publish({"type": "reply_delta", "delta": "部分🙂"})
    batch = run.events[1:]
    store.append_many(run.run_id, batch)
    store.append_many(run.run_id, batch)  # cancelled flush committed already
    restarted = chat_runs.ChatRunEventStore(path)
    restored = restarted.load_by_run_id(run.run_id)
    assert restored.timeline.messages() == run.timeline.messages()
    assert len(restored.events) <= 5
    restarted.recover_interrupted()
    restored = restarted.load_by_run_id(run.run_id)
    assert restored.timeline.messages()[0]["content"] == "部分🙂" * 20
    assert restored.timeline.messages()[0]["status"] == "failed"
    manager = chat_runs.ChatRunManager()
    emitted = [json.loads(line) async for line in manager.stream(restored)]
    assert any(e["type"] == "timeline_snapshot" for e in emitted)
    states = frontend_replay([e["timeline"] for e in emitted if e.get("timeline")])
    assert states[-1] == restored.timeline.messages()
    assert emitted[-1]["code"] == "process_restarted"
    await run.inbox.close()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_slow_stream_uses_signal_and_snapshot_without_queued_events(monkeypatch):
    monkeypatch.setattr(chat_runs, "_MAX_BUFFER_BYTES", 4096)
    run = chat_runs.ChatRun("slow", {"type": "ack"}, max_buffer=100)
    manager = chat_runs.ChatRunManager()
    stream = manager.stream(run)
    first = json.loads(await anext(stream))
    for _ in range(1000):
        await run.publish({"type": "reply_delta", "delta": "x" * 32})
    assert len(run.subscribers) == 1
    assert isinstance(next(iter(run.subscribers)), asyncio.Event)
    assert run._buffer_bytes <= 4096
    await run.publish({"type": "reply_done", "response": "x" * 32000})
    await run.publish({"type": "saved"})
    run.done.set()
    events = [first] + [json.loads(line) async for line in stream]
    assert any(e["type"] == "timeline_snapshot" for e in events)
    assert events[-1]["type"] == "saved"
    states = frontend_replay([e["timeline"] for e in events])
    assert states[-1] == run.timeline.messages()
    assert not run.subscribers
    await run.inbox.close()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_fast_stream_and_retained_cursor_deliver_each_delta_once():
    run = chat_runs.ChatRun("fast", {"type": "ack"})
    manager = chat_runs.ChatRunManager()
    stream = manager.stream(run)
    await anext(stream)
    patches = []
    for text in ["a", "🙂", "中文"]:
        await run.publish({"type": "reply_delta", "delta": text})
        item = json.loads(await anext(stream))
        patches.append(item["timeline"])
    cursor = item["_seq"]
    await stream.aclose()
    await run.publish({"type": "reply_delta", "delta": "b"})
    run.done.set()
    resumed = [json.loads(line) async for line in manager.stream(run, cursor)]
    assert len(resumed) == 1
    patches.append(resumed[0]["timeline"])
    assert frontend_replay(patches)[-1] == run.timeline.messages()
    await run.inbox.close()
    await manager.shutdown()
