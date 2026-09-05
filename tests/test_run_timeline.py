"""Behavioral contracts for the shared live/durable transcript."""
import asyncio
import json
import subprocess

import pytest

from cyrene.workbench.chat.run_timeline import RunTimeline
from cyrene.workbench.chat.chat_runs import ChatRun


def event(kind, second=0, **values):
    return {"type": kind, "timestamp": f"2026-09-05T10:00:{second:02d}+00:00", **values}


def test_consecutive_identical_replies_have_distinct_stable_identities():
    timeline = RunTimeline("run")
    for source in ("a", "b"):
        timeline.apply(event("reply_start", sourceId=source))
        timeline.apply(event("reply_delta", sourceId=source, delta="same"))
        timeline.apply(event("reply_done", sourceId=source, response="same"))
    records = timeline.messages()
    assert [r["content"] for r in records] == ["same", "same"]
    assert records[0]["id"] != records[1]["id"]
    assert records[0]["intermediate"] is True
    assert not records[1].get("intermediate")
    timeline.apply(event("reply_done", sourceId="b", response="corrected"))
    assert [r["content"] for r in timeline.messages()] == ["same", "corrected"]


def test_duplicate_delta_is_idempotent():
    timeline = RunTimeline("run")
    delta = event("reply_delta", delta="a", sourceId="a", eventId="delta-1")
    timeline.apply(delta)
    timeline.apply(delta)
    assert timeline.messages()[0]["content"] == "a"


def test_prose_splits_activity_and_late_tool_completion_stays_in_original_card():
    timeline = RunTimeline("run")
    timeline.apply(event("reasoning_delta", sourceId="a", delta="thought"))
    timeline.apply(event("reasoning_done", 1, sourceId="a", response="thought"))
    timeline.apply(event("reply_done", 2, sourceId="a", response="checking"))
    timeline.apply(event("tool.started", 3, sourceId="a", toolCallId="t1", name="read"))
    timeline.apply(event("tool.started", 4, sourceId="a", toolCallId="t2", name="search"))
    timeline.apply(event("reply_done", 5, sourceId="b", response="next"))
    timeline.apply(event("tool.started", 6, sourceId="b", toolCallId="t3", name="write"))
    ids = [r["id"] for r in timeline.messages()]
    timeline.apply(event("tool.completed", 9, sourceId="a", toolCallId="t1", status="failed"))
    records = timeline.messages()
    assert [r["id"] for r in records] == ids
    assert [r["content"] for r in records] == ["", "checking", "", "next", ""]
    assert [t["toolCallId"] for t in records[2]["trace"]] == ["t1", "t2"]
    assert records[2]["trace"][0]["failed"]
    assert records[2]["status"] == "running"
    timeline.apply(event("tool.completed", 12, toolCallId="t2", status="completed"))
    assert timeline.messages()[2]["endedAt"] == event("", 12)["timestamp"]


def test_failure_retains_partial_content_and_settles_activity():
    timeline = RunTimeline("run")
    timeline.apply(event("reply_delta", delta="partial"))
    timeline.apply(event("tool.started", toolCallId="t", name="read"))
    timeline.apply(event("error", 10))
    assert timeline.messages()[0]["content"] == "partial"
    assert all(record["status"] == "failed" for record in timeline.messages())
    assert timeline.messages()[1]["trace"][0]["status"] == "failed"


def test_live_records_are_the_terminal_records_and_are_checkpointed():
    async def scenario():
        checkpoints = []
        run = ChatRun("chat", {"type": "ack"}, persist_live_message=lambda _, m: checkpoints.append(m))
        await run.publish(event("reply_start", sourceId="a"))
        await run.publish(event("reply_delta", sourceId="a", delta="first"))
        await run.publish(event("reply_done", 1, sourceId="a", response="first"))
        await run.publish(event("reply_done", 2, sourceId="b", response="second"))
        await run.publish(event("run.completed", 3))
        live = {}
        for emitted in run.events:
            for message in emitted.get("timeline", {}).get("messages", []):
                live[message["id"]] = message
        assert list(live.values()) == run.terminal_timeline_messages([])
        assert [r["content"] for r in run.terminal_timeline_messages([])] == ["first", "second"]
        assert checkpoints[-1]["status"] == "completed"
        restored = ChatRun.restore(run_id=run.run_id, chat_id="chat", status="completed", created_at=run.created_at,
                                  termination_reason="", outcome_kind="reply", last_seq=run.seq,
                                  events=run.events, completed=True)
        assert restored.timeline.messages() == run.timeline.messages()
    asyncio.run(scenario())


def test_frontend_projection_replaces_continuation_atomically_and_keeps_history():
    from conftest import frontend_module_source
    source = frontend_module_source("features/chat/runtime-timeline.jsx")
    helpers = "function wbcConfirmOptimisticMessage(" + source.split("function wbcConfirmOptimisticMessage(", 1)[1].split("export {", 1)[0]
    script = f"""
eval({json.dumps(helpers)});
let runtime = wbcApplyTimeline({{}}, {{version:1, runId:'r', revision:1, status:'running', messages:[
  {{id:'m', role:'assistant', content:'first', status:'completed', timelineOrder:1, timelineRevision:1, createdAt:'2026-01-01'}}
]}});
const waiting = wbcProjectTranscript([], runtime);
runtime = wbcApplyTimeline(runtime, {{version:1, runId:'r', revision:2, status:'running', messages:[
  {{id:'a', role:'assistant', content:'', activityCard:true, status:'running', timelineOrder:2, createdAt:'2026-01-02'}}
]}});
const active = wbcProjectTranscript([], runtime);
const stale = wbcApplyTimeline(runtime, {{version:1, runId:'r', revision:1, messages:[]}});
process.stdout.write(JSON.stringify({{waiting:waiting.map(m=>m.id), active:active.map(m=>m.id), stale:stale===runtime}}));
"""
    result = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert result == {"waiting": ["m", "r:continuation"], "active": ["m", "a"], "stale": True}


def test_group_duration_is_its_own_wall_clock_and_failure_does_not_remove_cards():
    from conftest import frontend_module_source
    source = frontend_module_source("features/chat/messages.jsx")
    helpers = "var WBC_ACTIVITY_GROUP_MIN_ITEMS" + source.split("var WBC_ACTIVITY_GROUP_MIN_ITEMS", 1)[1].split("// Read-only tool names", 1)[0]
    script = f"""
eval({json.dumps(helpers)});
const activities = [0,1,2].map(i => ({{id:'a'+i, timelineVersion:1, activityCard:true,
  status:i===1?'failed':'completed', createdAt:'2026-01-01T00:00:0'+i+'Z',
  startedAt:'2026-01-01T00:00:0'+i+'Z', endedAt:'2026-01-01T00:00:05Z', trace:[]}}));
const grouped = wbcGroupConsecutiveActivityMessages(activities.concat([{{id:'m',content:'reply',processingDurationMs:90000}}]), null);
process.stdout.write(JSON.stringify({{count:grouped[0].activities.length,duration:grouped[0].durationMs,active:grouped[0].active,next:grouped[1].id}}));
"""
    result = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert result == {"count": 3, "duration": 5000, "active": False, "next": "m"}


def test_disclosure_survives_completion_grouping_and_remount():
    from conftest import frontend_module_source
    source = frontend_module_source("features/chat/messages.jsx")
    helper = source[source.index("var wbcDisclosureListeners"):source.index("function wbcLocalizedToolName")]
    script = f"""
const storage = new Map();
const localStorage = {{getItem:k=>storage.has(k)?storage.get(k):null,setItem:(k,v)=>storage.set(k,v)}};
const window = {{addEventListener(){{}},removeEventListener(){{}}}};
function useWbcState(initial) {{ return [typeof initial==='function'?initial():initial, ()=>{{}}]; }}
function useWbcEffect(effect) {{ effect(); }}
eval({json.dumps(helper)});
wbcUseDisclosure('card')[1](true);
const inherited = wbcUseDisclosure('group',['card'])[0];
wbcUseDisclosure('group',['card'])[1](false);
const closed = wbcUseDisclosure('group',['card'])[0];
wbcUseDisclosure('group',['card'])[1](true);
process.stdout.write(JSON.stringify({{inherited,closed,remount:wbcUseDisclosure('group',['card'])[0],child:wbcUseDisclosure('card')[0]}}));
"""
    result = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert result == {"inherited": True, "closed": False, "remount": True, "child": True}


def test_terminal_failure_transfers_visible_records_before_clearing_runtime():
    from test_workbench_frontend_logic import _run_workbench_runtime_js
    result = _run_workbench_runtime_js("""
(() => {
  let captured = [];
  let handlers;
  WorkbenchChatRuntimes.setHooks({onAssistantSaved: (_id, messages) => { captured = messages; }});
  WorkbenchChatRuntimes.start('failed', {message:'hello'}, {sendMessage: (_id,_input,h) => {
    handlers = h; return new Promise(() => {});
  }});
  handlers.onTimeline({version:1,runId:'r',revision:1,status:'failed',messages:[
    {id:'partial',role:'assistant',content:'retained',status:'failed',timelineVersion:1,timelineOrder:1}
  ]});
  handlers.onError(new Error('provider failed'));
  return {content:captured[0].content, status:captured[0].status, cleared:!WorkbenchChatRuntimes.get('failed')};
})()
""")
    assert result == {"content": "retained", "status": "failed", "cleared": True}


@pytest.mark.parametrize("source_id", ["generation-a", ""])
@pytest.mark.parametrize("protocol", ["legacy", "versioned"])
def test_guidance_during_reply_preserves_stream_identity_and_intermediate_marker(source_id, protocol):
    timeline = RunTimeline("guided-reply")
    delivered = RunTimeline(timeline.run_id)
    events = []
    names = ("reply_start", "reply_delta", "reply_done") if protocol == "legacy" else ("message.started", "message.delta", "message.completed")

    def publish(kind, second, **payload):
        item = event(kind, second, **({"payload": payload} if protocol == "versioned" else payload))
        events.append(item)
        patch = timeline.apply(item)
        delivered.ingest(patch)
        assert delivered.records == timeline.records
        return patch

    source = {"sourceId": source_id} if source_id else {}
    publish(names[0], 0, **source)
    publish(names[1], 1, delta="先检查", **source)
    original = timeline.messages()[0]
    publish("guidance_received", 2, userMessage={
        "id": "guide", "role": "user", "content": "只检查，不修改",
        "createdAt": event("", 2)["timestamp"],
    })
    assert timeline.messages()[0]["status"] == "running"
    publish(names[1], 3, delta="配置。", **source)
    publish(names[2], 4, response="先检查配置。", **source)
    assert [record["content"] for record in timeline.messages()] == ["先检查配置。", "只检查，不修改"]
    assert timeline.messages()[0]["id"] == original["id"]
    assert timeline.messages()[0]["createdAt"] == original["createdAt"]
    assert timeline.messages()[0]["status"] == "completed"
    next_source = {"sourceId": "generation-b"} if source_id else {}
    publish(names[0], 5, **next_source)
    assert timeline.messages()[0]["intermediate"] is True
    publish(names[2], 6, response="收到，只检查。", **next_source)
    assert [record["content"] for record in timeline.messages()] == ["先检查配置。", "只检查，不修改", "收到，只检查。"]
    assert all(record["status"] == "completed" for record in timeline.messages())
    replay = RunTimeline(timeline.run_id)
    for item in events:
        replay.apply(item)
    assert replay.messages() == timeline.messages()


@pytest.mark.parametrize("source_id", ["generation-a", ""])
@pytest.mark.parametrize("protocol", ["legacy", "versioned"])
def test_guidance_keeps_reasoning_owner_when_new_tool_opens_another_card(source_id, protocol):
    timeline = RunTimeline("guided-reasoning")
    delivered = RunTimeline(timeline.run_id)
    names = ("reasoning_start", "reasoning_delta", "reasoning_done") if protocol == "legacy" else ("reasoning.started", "reasoning.delta", "reasoning.completed")

    def publish(kind, second, **payload):
        item = event(kind, second, **({"payload": payload} if protocol == "versioned" else payload))
        patch = timeline.apply(item)
        delivered.ingest(patch)
        assert delivered.records == timeline.records
        return patch

    source = {"sourceId": source_id} if source_id else {}
    publish(names[0], 0, **source)
    publish(names[1], 1, delta="检查", **source)
    original_id = timeline.messages()[0]["id"]
    publish("tool.started", 2, toolCallId="old-tool", name="Read", **source)
    publish("guidance_received", 3, userMessage={"id": "guide", "role": "user", "content": "不要修改"})
    publish("tool.started", 4, toolCallId="new-tool", name="Search", **source)
    new_card_id = timeline.tools["new-tool"]
    assert new_card_id != original_id
    publish(names[1], 5, delta="完成", **source)
    publish(names[2], 6, response="检查完成", **source)
    assert len(timeline.messages()) == 3
    assert timeline.records[original_id]["reasoning"] == "检查完成"
    assert timeline.records[original_id]["reasoningActive"] is False
    assert timeline.records[original_id]["status"] == "running"  # old tool still owns this card
    publish("tool.completed", 7, toolCallId="old-tool", status="completed", **source)
    assert timeline.records[original_id]["status"] == "completed"
    assert timeline.records[original_id]["endedAt"] == event("", 7)["timestamp"]
    assert timeline.records[new_card_id]["status"] == "running"
    publish("tool.completed", 8, toolCallId="new-tool", status="completed", **source)
    assert not any(record["status"] == "running" for record in timeline.messages())
    publish(names[0], 9, **source)
    if source_id:
        assert timeline.reasoning_id == new_card_id
    else:
        assert timeline.reasoning_id != original_id
    assert timeline.records[original_id]["status"] == "completed"


def test_guidance_without_new_activity_settles_original_reasoning_and_shows_continuation():
    from conftest import frontend_module_source

    timeline = RunTimeline("guided-wait")
    patches = []
    for item in [
        event("reasoning_delta", 0, sourceId="a", delta="检查"),
        event("guidance_received", 1, userMessage={"id": "guide", "role": "user", "content": "不要修改"}),
        event("reasoning_done", 2, sourceId="a", response="检查完成"),
    ]:
        patches.append(timeline.apply(item))
    assert len(timeline.messages()) == 2
    assert timeline.messages()[0]["status"] == "completed"
    source = frontend_module_source("features/chat/runtime-timeline.jsx")
    helpers = "function wbcConfirmOptimisticMessage(" + source.split("function wbcConfirmOptimisticMessage(", 1)[1].split("export {", 1)[0]
    script = f"""
eval({json.dumps(helpers)});
let runtime = {{}};
const states = {json.dumps(patches)}.map(patch => {{
  runtime = wbcApplyTimeline(runtime, patch);
  return wbcProjectTranscript([], runtime).map(message => message.id);
}});
process.stdout.write(JSON.stringify(states));
"""
    states = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    activity_id = timeline.messages()[0]["id"]
    assert states == [[activity_id], [activity_id, "guide"], [activity_id, "guide", "guided-wait:continuation"]]
