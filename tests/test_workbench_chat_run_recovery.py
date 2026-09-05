from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from types import SimpleNamespace


def test_startup_recovers_crashed_running_chat_and_clears_stale_question(tmp_path):
    from cyrene.platform.database import init_db
    from cyrene.workbench.chat.chat_repository import ChatRepository
    from cyrene.workbench.chat.chat_runs import ChatRunManager

    payload = {
        "chats": [
            {
                "id": "chat_crashed",
                "status": "running",
                "pendingQuestion": {"id": "stale", "text": "旧问题"},
            },
            {
                "id": "chat_waiting",
                "status": "idle",
                "pendingQuestion": {"id": "valid", "text": "仍待回答"},
            },
        ]
    }
    database = tmp_path / "runtime.db"
    asyncio.run(init_db(str(database)))
    repository = ChatRepository(str(database))
    repository.write(payload)
    manager = ChatRunManager()
    manager.configure(str(database))
    manager.startup()
    payload = repository.read()

    assert payload["chats"][0]["status"] == "idle"
    assert "pendingQuestion" not in payload["chats"][0]
    assert payload["chats"][0]["lastRun"]["status"] == "error"
    assert payload["chats"][0]["lastRun"]["terminationReason"] == "process_restarted"
    assert payload["chats"][1]["pendingQuestion"]["id"] == "valid"


async def test_stream_status_projection_is_skipped_after_reply_and_nonfatal_after_error():
    from cyrene.workbench.chat.chat_run_lifecycle_service import (
        ChatRunLifecycleApplicationService,
        ChatRunLifecycleDependencies,
    )
    from cyrene.workbench.chat.chat_runs import ChatRun

    published = []

    async def publish_chat_changed(*args, **kwargs):
        published.append((args, kwargs))

    service = ChatRunLifecycleApplicationService(
        ChatRunLifecycleDependencies(
            run_manager=None,
            capture_workspace_baseline=None,
            finalize_workspace_changes=None,
            schedule_workspace_finalize=None,
            publish_chat_changed=publish_chat_changed,
            load_chat_summary=lambda chat_id: {"id": chat_id},
            public_message=lambda value: value,
            error_message=lambda exc, _lang: str(exc),
            error_metadata=lambda _exc: {},
        )
    )
    settle_calls = []

    def locked_settle():
        settle_calls.append("called")
        raise sqlite3.OperationalError("database is locked")

    request = SimpleNamespace(
        chat_id="chat_projection",
        project_id="project_projection",
        settle_status=locked_settle,
    )

    reply_run = ChatRun("chat_projection", {"type": "ack"})
    reply_run.outcome = {
        "kind": "reply",
        "payload": {"chatSummary": {"id": "chat_projection"}},
    }
    await service._settle_stream(request, reply_run)
    assert settle_calls == []

    error_run = ChatRun("chat_projection", {"type": "ack"})
    error_run.outcome = {"kind": "error", "exc": RuntimeError("failed")}
    await service._settle_stream(request, error_run)
    assert settle_calls == ["called"]
    assert len(published) == 2


async def test_finished_run_remains_replayable_during_retention_window():
    from cyrene.workbench.chat.chat_runs import ChatRunManager

    manager = ChatRunManager(retention_seconds=45)

    async def runner(run):
        run.outcome = {"kind": "reply"}
        await run.publish({"type": "saved", "assistantMessage": {"content": "done"}})

    run, _ = manager.start_or_get(
        "chat_replay_finished", {"type": "ack"}, runner, stream=False
    )
    await asyncio.wait_for(run.done.wait(), timeout=1)

    assert manager.get("chat_replay_finished") is None
    assert manager.get_replayable("chat_replay_finished") is run
    replayed = [line async for line in manager.stream(run)]
    assert any('"type": "saved"' in line for line in replayed)
    await manager.shutdown()


async def test_deleted_chat_run_is_cancelled_awaited_and_forgotten(tmp_path):
    from cyrene.workbench.chat.chat_runs import ChatRunManager

    manager = ChatRunManager(retention_seconds=45)
    manager.configure(str(tmp_path / "deleted-run.sqlite3"))
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def runner(_run):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    run, _ = manager.start_or_get(
        "chat_deleted", {"type": "ack", "chatId": "chat_deleted"}, runner
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    assert await manager.terminate("chat_deleted") is True
    assert stopped.is_set()
    assert run.task is not None and run.task.done()
    assert manager.get("chat_deleted") is None
    assert manager.get_replayable("chat_deleted") is None


async def test_finished_run_events_reload_from_sqlite_after_memory_cleanup(
    tmp_path,
):
    from cyrene.workbench.chat.chat_runs import ChatRunManager

    db_path = str(tmp_path / "durable-runs.sqlite3")
    manager = ChatRunManager(retention_seconds=0)
    manager.configure(db_path)

    async def runner(run):
        await run.publish({"type": "reply_done", "response": "durable reply"})
        run.outcome = {"kind": "reply"}

    run, _ = manager.start_or_get(
        "chat_durable",
        {"type": "ack", "chatId": "chat_durable"},
        runner,
        stream=False,
    )
    await asyncio.wait_for(run.done.wait(), timeout=2)

    restarted = ChatRunManager(retention_seconds=0)
    restarted.configure(db_path)
    restored = restarted.get_replayable_by_run_id(run.run_id)

    assert restored is not None
    assert restored.done.is_set()
    assert restored.status == "done"
    assert restored.outcome == {"kind": "reply"}
    assert [event["type"] for event in restored.events] == [
        "ack",
        "reply_done",
    ]
    assert restored.events[-1]["response"] == "durable reply"


def test_corrupt_durable_event_is_dropped_without_breaking_replay(tmp_path):
    from cyrene.workbench.chat.chat_runs import ChatRun, ChatRunEventStore

    db_path = str(tmp_path / "corrupt-durable-event.sqlite3")
    store = ChatRunEventStore(db_path)
    run = ChatRun("chat_corrupt", {"type": "ack", "chatId": "chat_corrupt"})
    store.create(run)
    store.append(run.run_id, {"_seq": 2, "type": "reply_done", "response": "ok"})
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE workbench_chat_run_events SET event_json = ? WHERE run_id = ? AND seq = 2",
            ("{not-json", run.run_id),
        )

    restored = store.load_by_run_id(run.run_id)

    assert restored is not None
    assert [event["type"] for event in restored.events] == ["ack"]


def test_durable_events_are_compressed_and_trimmed_like_live_buffer(monkeypatch, tmp_path):
    import sqlite3
    from cyrene.workbench.chat import chat_runs

    monkeypatch.setattr(chat_runs, "_MAX_BUFFER_EVENTS", 5)
    db_path = str(tmp_path / "compact-durable-events.sqlite3")
    store = chat_runs.ChatRunEventStore(db_path)
    run = chat_runs.ChatRun("chat_compact", {"type": "ack"})
    store.create(run)
    store.append_many(
        run.run_id,
        [
            {"_seq": seq, "type": "reply_delta", "delta": "x" * 2_000}
            for seq in range(2, 11)
        ],
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT seq, typeof(event_json) FROM workbench_chat_run_events "
            "WHERE run_id=? ORDER BY seq",
            (run.run_id,),
        ).fetchall()
    assert rows == [(1, "blob"), (7, "blob"), (8, "blob"), (9, "blob"), (10, "blob")]
    restored = store.load_by_run_id(run.run_id)
    assert restored is not None
    assert [event["_seq"] for event in restored.events] == [1, 7, 8, 9, 10]
    assert restored.events[-1]["delta"] == "x" * 2_000


async def test_stream_deltas_are_batched_into_one_sqlite_transaction(
    monkeypatch,
    tmp_path,
):
    from cyrene.workbench.chat.chat_runs import ChatRun, ChatRunEventStore

    store = ChatRunEventStore(str(tmp_path / "batched-events.sqlite3"))
    run = ChatRun("chat_batched", {"type": "ack", "chatId": "chat_batched"})
    await run.configure_event_store(store)
    batch_sizes = []
    original_append_many = store.append_many

    def tracked_append_many(run_id, events):
        batch_sizes.append(len(events))
        return original_append_many(run_id, events)

    monkeypatch.setattr(store, "append_many", tracked_append_many)
    for index in range(32):
        await run.publish({"type": "reply_delta", "delta": str(index)})

    # In-memory fanout remains immediate while SQLite work waits for a bounded
    # batch or a terminal event.
    assert len(run.events) == 33
    assert batch_sizes == []

    await run.publish({"type": "reply_done", "response": "done"})
    await run.flush_event_store()

    assert batch_sizes == [33]
    restored = store.load_by_run_id(run.run_id)
    assert restored is not None
    assert [event["type"] for event in restored.events] == [
        "ack",
        *(["reply_delta"] * 32),
        "reply_done",
    ]


async def test_visible_tool_start_seals_streamed_reply_before_tool_event():
    from cyrene.workbench.chat.chat_runs import ChatRun

    checkpointed = []
    run = ChatRun(
        "chat_ordered_preamble",
        {"type": "ack", "chatId": "chat_ordered_preamble"},
        persist_live_message=lambda chat_id, message: checkpointed.append(
            (chat_id, dict(message))
        ),
    )

    await run.publish({
        "type": "reply_delta",
        "delta": "好，我先",
        "timestamp": "2026-08-29T06:00:00+00:00",
    })
    await run.publish({
        "type": "reply_done",
        "response": "好，我先检查代码。",
        "timestamp": "2026-08-29T06:00:01+00:00",
    })
    await run.publish({
        "type": "tool.started",
        "timestamp": "2026-08-29T06:00:02+00:00",
        "payload": {"toolCallId": "call-read", "name": "Read"},
    })
    await run.publish({
        "type": "tool.started",
        "timestamp": "2026-08-29T06:00:03+00:00",
        "payload": {"toolCallId": "call-search", "name": "Search"},
    })

    assert [event["type"] for event in run.events] == [
        "ack", "reply_delta", "reply_done", "tool.started", "tool.started",
    ]
    records = run.terminal_timeline_messages([])
    assert records[0]["content"] == "好，我先检查代码。"
    assert records[0]["createdAt"] == "2026-08-29T06:00:00+00:00"
    assert records[1]["activityCard"] is True
    assert len(records[1]["trace"]) == 2
    assert checkpointed[0][1]["id"] == records[0]["id"]


async def test_core_message_delta_uses_same_tool_boundary_order():
    from cyrene.workbench.chat.chat_runs import ChatRun

    run = ChatRun("chat_core_order", {"type": "ack"})
    await run.publish({
        "type": "message.delta",
        "timestamp": "2026-08-29T06:10:00+00:00",
        "payload": {"delta": "Let me inspect that."},
    })
    await run.publish({
        "type": "message.completed",
        "timestamp": "2026-08-29T06:10:01+00:00",
        "payload": {"response": "Let me inspect that."},
    })
    await run.publish({
        "type": "tool.started",
        "timestamp": "2026-08-29T06:10:02+00:00",
        "payload": {"toolCallId": "call-shell", "name": "Bash"},
    })

    records = run.terminal_timeline_messages([])
    assert records[0]["content"] == "Let me inspect that."
    assert records[1]["trace"][0]["toolCallId"] == "call-shell"


async def test_explicit_intermediate_message_consumes_matching_stream_buffer():
    from cyrene.workbench.chat.chat_runs import ChatRun

    run = ChatRun("chat_explicit_intermediate", {"type": "ack"})
    await run.publish({"type": "reply_delta", "delta": "正在检查。"})
    await run.publish({
        "type": "tool.started",
        "payload": {"toolCallId": "call-message", "name": "send_message"},
    })
    explicit = {
        "id": "assistant_explicit",
        "role": "assistant",
        "content": "正在检查。",
        "createdAt": "2026-08-29T06:20:00+00:00",
        "intermediate": True,
        "roundId": run.run_id,
    }
    await run.publish({"type": "intermediate_message", "message": explicit})
    await run.publish({
        "type": "tool.started",
        "payload": {"toolCallId": "call-shell", "name": "Bash"},
    })

    intermediates = [
        event for event in run.events if event["type"] == "intermediate_message"
    ]
    assert [event["message"]["id"] for event in intermediates] == [
        "assistant_explicit"
    ]
    assert [event["type"] for event in run.events[-2:]] == [
        "intermediate_message",
        "tool.started",
    ]


async def test_terminal_timeline_uses_run_event_when_live_checkpoint_failed():
    from cyrene.workbench.chat.chat_runs import ChatRun

    def fail_checkpoint(_chat_id, _message):
        raise OSError("chat store is temporarily unavailable")

    run = ChatRun(
        "chat_checkpoint_failure",
        {"type": "ack"},
        persist_live_message=fail_checkpoint,
    )
    message = {
        "id": "assistant_still_durable_at_terminal",
        "role": "assistant",
        "content": "这条中间回复必须保留。",
        "createdAt": "2026-08-29T06:30:00+00:00",
        "intermediate": True,
        "roundId": run.run_id,
    }

    await run.publish({"type": "intermediate_message", "message": message})

    record = run.terminal_timeline_messages([])[0]
    assert record["id"] == message["id"]
    assert record["content"] == message["content"]
    assert record["timelineVersion"] == 1


async def test_terminal_failure_checkpoints_completed_stream_before_error():
    from cyrene.workbench.chat.chat_runs import ChatRun

    checkpointed = []
    run = ChatRun(
        "chat_failed_after_reply",
        {"type": "ack", "chatId": "chat_failed_after_reply"},
        persist_live_message=lambda chat_id, message: checkpointed.append(
            (chat_id, dict(message))
        ),
    )

    await run.publish({
        "type": "reply_delta",
        "delta": "已生成的正文。",
        "timestamp": "2026-09-03T11:29:18+00:00",
    })
    await run.publish({
        "type": "reply_done",
        "response": "已生成的完整正文。",
        "timestamp": "2026-09-03T11:29:19+00:00",
    })
    await run.publish({
        "type": "run.failed",
        "payload": {"code": "model_response_invalid"},
    })

    assert [event["type"] for event in run.events] == [
        "ack", "reply_delta", "reply_done", "run.failed",
    ]
    recovered = run.terminal_timeline_messages([])[0]
    assert recovered["content"] == "已生成的完整正文。"
    assert recovered["createdAt"] == "2026-09-03T11:29:18+00:00"
    assert checkpointed == [("chat_failed_after_reply", recovered)]
    await run.publish({"type": "error", "code": "agent_run_failed"})
    assert run.terminal_timeline_messages([]) == [recovered]


async def test_durable_event_lock_cannot_block_or_fail_live_reply(monkeypatch, tmp_path):
    from cyrene.workbench.chat.chat_runs import ChatRun, ChatRunEventStore

    store = ChatRunEventStore(str(tmp_path / "locked-events.sqlite3"))
    run = ChatRun("chat_locked", {"type": "ack", "chatId": "chat_locked"})
    await run.configure_event_store(store)
    changed = asyncio.Event()
    run.subscribers.add(changed)

    def locked_append_many(_run_id, _events):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "append_many", locked_append_many)

    await asyncio.wait_for(
        run.publish({"type": "reply_done", "response": "completed"}),
        timeout=0.2,
    )
    await asyncio.wait_for(changed.wait(), timeout=0.2)
    projected = run.events[-1]
    assert projected["type"] == "reply_done"
    assert projected["response"] == "completed"

    flush_task = run._event_store_flush_task
    assert flush_task is not None
    await asyncio.gather(flush_task, return_exceptions=True)
    assert [event["type"] for event in run._event_store_pending] == ["reply_done"]


def test_startup_marks_unfinished_durable_run_as_process_restarted(tmp_path):
    from cyrene.workbench.chat.chat_runs import ChatRun, ChatRunEventStore, ChatRunManager

    db_path = str(tmp_path / "crashed-run.sqlite3")
    store = ChatRunEventStore(db_path)
    run = ChatRun("chat_crashed", {"type": "ack", "chatId": "chat_crashed"})
    store.create(run)

    manager = ChatRunManager(retention_seconds=0)
    manager.configure(db_path)
    manager.startup()
    restored = manager.get_replayable_by_run_id(run.run_id)

    assert restored is not None
    assert restored.status == "error"
    assert restored.termination_reason == "process_restarted"
    assert restored.events[-1]["code"] == "process_restarted"
    assert restored.events[-1]["_seq"] == 2


async def test_chat_run_storage_setup_runs_off_the_event_loop(monkeypatch, tmp_path):
    from cyrene.workbench.application.inbox import WorkbenchAgentInbox
    from cyrene.workbench.chat.chat_runs import ChatRunManager

    started = threading.Event()
    release = threading.Event()
    original = WorkbenchAgentInbox.configure_storage

    def blocked_setup(self, db_path):
        started.set()
        release.wait(timeout=2)
        return original(self, db_path)

    monkeypatch.setattr(WorkbenchAgentInbox, "configure_storage", blocked_setup)
    manager = ChatRunManager(retention_seconds=0)
    manager.configure(str(tmp_path / "workbench.db"))
    ran = asyncio.Event()

    async def runner(_run):
        ran.set()

    run, _ = manager.start_or_get("chat_nonblocking_init", {"type": "ack"}, runner)
    assert await asyncio.to_thread(started.wait, 1)

    # This tick would never execute if schema setup were still synchronous on
    # the server's sole asyncio loop.
    ticked = False

    def mark_tick():
        nonlocal ticked
        ticked = True

    asyncio.get_running_loop().call_soon(mark_tick)
    await asyncio.sleep(0)
    assert ticked is True
    assert not ran.is_set()

    release.set()
    await asyncio.wait_for(run.done.wait(), timeout=2)
    assert ran.is_set()


async def test_chat_run_records_server_stages_and_first_stream_delta_once():
    from cyrene.workbench.chat.chat_runs import ChatRun

    run = ChatRun(
        "chat_timing",
        {
            "type": "ack",
            "clientRequestId": "request_1",
            "_timingEnabled": True,
            "_latencyStartedMonotonic": time.monotonic(),
            "timing": [
                {"stage": "server_received", "serverElapsedMs": 0.0},
                {"stage": "ack", "serverElapsedMs": 0.1},
            ],
        },
    )
    await run.mark_timing("snapshot_complete")
    await run.publish({"type": "reasoning_delta", "delta": "thinking"})
    await run.publish({"type": "reply_delta", "delta": "answer"})

    assert "_latencyStartedMonotonic" not in run.events[0]
    stages = [
        event.get("stage")
        for event in run.events
        if event.get("type") == "chat_timing"
    ]
    assert stages == ["snapshot_complete", "first_delta"]
    first_delta = next(event for event in run.events if event.get("stage") == "first_delta")
    assert first_delta["clientRequestId"] == "request_1"
    assert first_delta["serverElapsedMs"] >= 0
