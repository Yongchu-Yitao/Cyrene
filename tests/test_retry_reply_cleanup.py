from cyrene.workbench.chat.run_timeline import RunTimeline
from test_workbench_frontend_logic import _run_workbench_timeline_js


def test_retry_removes_only_failed_reply_when_replacement_text_arrives():
    timeline = RunTimeline("run")
    replay = RunTimeline("run")

    def emit(kind, **data):
        patch = timeline.apply({"type": kind, **data})
        replay.ingest(patch)
        assert replay.messages() == timeline.messages()
        return patch

    emit("reply_done", sourceId="earlier", response="keep earlier reply")
    emit("tool.started", sourceId="earlier", toolCallId="tool", name="Read")
    emit("tool.completed", sourceId="earlier", toolCallId="tool", name="Read")
    emit("reply_delta", sourceId="failed", delta="partial")
    old_id = timeline.reply_id
    emit("reply_start", sourceId="failed", reset=True)
    emit("reply_start", sourceId="retry")
    assert timeline.records[old_id]["content"] == "partial"
    emit("reply_delta", sourceId="retry", delta="")
    assert old_id in timeline.records
    patch = emit("reply_delta", sourceId="retry", delta="new reply")
    assert old_id not in timeline.records
    assert patch["removedMessageIds"] == [old_id]
    assert [m["content"] for m in timeline.messages() if m["content"]] == ["keep earlier reply", "new reply"]
    assert any(m.get("trace") for m in timeline.messages())
    emit("reply_delta", sourceId="failed", delta="late old text")
    assert old_id not in timeline.records
    restored = RunTimeline("run")
    restored.ingest(timeline.snapshot())
    assert restored.messages() == timeline.messages()
    assert restored.removed_message_ids == {old_id}


def test_frontend_removes_retried_reply_from_live_and_stale_durable_history():
    result = _run_workbench_timeline_js('''
(() => {
  const old = {id:"old", role:"assistant", content:"partial", timelineOrder:1};
  const fresh = {id:"new", role:"assistant", content:"recovered", timelineOrder:2};
  let runtime = wbcApplyTimeline({}, {version:2, runId:"run", revision:1, status:"running", messages:[old]});
  runtime = wbcApplyTimeline(runtime, {version:2, runId:"run", revision:2, status:"completed", messages:[fresh], removedMessageIds:["old"]});
  return {live:runtime.timeline.messages, transcript:wbcProjectTranscript([old], runtime)};
})()
''')
    assert [m["content"] for m in result["live"]] == ["recovered"]
    assert [m["content"] for m in result["transcript"]] == ["recovered"]


def test_checkpoint_deletes_previously_saved_partial_reply(tmp_path):
    from cyrene.workbench.chat.chat_runs import ChatRunManager
    from cyrene.workbench.chat.chat_repository import ChatRepository

    path = str(tmp_path / "chat.sqlite3")
    manager = ChatRunManager()
    manager.configure(path)
    repository = ChatRepository(path)
    repository.write({"chats": [{"id": "chat", "messages": [
        {"id": "earlier", "content": "keep"}, {"id": "partial", "content": "remove"},
    ]}]})
    manager._persist_live_public_message("chat", {"id": "partial", "timelineRemoved": True})
    chat = repository.read()["chats"][0]
    assert [m["id"] for m in chat["messages"]] == ["earlier"]
