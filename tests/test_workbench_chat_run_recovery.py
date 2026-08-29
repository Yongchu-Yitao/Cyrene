from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace


def test_startup_recovers_crashed_running_chat_and_clears_stale_question(tmp_path):
    from cyrene.runtime.database import init_db
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
            publish_live_segments=None,
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
    stopped = asyncio.Event()
    stopped.set()
    live_task = asyncio.create_task(asyncio.sleep(0))
    await service._settle_stream(request, reply_run, stopped, live_task)
    assert settle_calls == []

    error_run = ChatRun("chat_projection", {"type": "ack"})
    error_run.outcome = {"kind": "error", "exc": RuntimeError("failed")}
    live_task = asyncio.create_task(asyncio.sleep(0))
    await service._settle_stream(request, error_run, stopped, live_task)
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
    assert rows == [(1, "text"), (7, "blob"), (8, "blob"), (9, "blob"), (10, "blob")]
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


async def test_durable_event_lock_cannot_block_or_fail_live_reply(monkeypatch, tmp_path):
    from cyrene.workbench.chat.chat_runs import ChatRun, ChatRunEventStore

    store = ChatRunEventStore(str(tmp_path / "locked-events.sqlite3"))
    run = ChatRun("chat_locked", {"type": "ack", "chatId": "chat_locked"})
    await run.configure_event_store(store)
    queue = asyncio.Queue()
    run.subscribers.add(queue)

    def locked_append_many(_run_id, _events):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "append_many", locked_append_many)

    await asyncio.wait_for(
        run.publish({"type": "reply_done", "response": "completed"}),
        timeout=0.2,
    )
    projected = await asyncio.wait_for(queue.get(), timeout=0.2)
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
