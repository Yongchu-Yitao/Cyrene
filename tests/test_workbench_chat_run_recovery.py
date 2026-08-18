from __future__ import annotations

import asyncio
import sqlite3
import threading


def test_startup_recovers_crashed_running_chat_and_clears_stale_question(monkeypatch):
    from cyrene.workbench import chat as chat_mod
    from cyrene.workbench.chat_runs import ChatRunManager

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
    written = []
    monkeypatch.setattr(chat_mod, "_read_chats_store", lambda: payload)
    monkeypatch.setattr(chat_mod, "_write_chats_store", lambda value: written.append(value))

    ChatRunManager().startup()

    assert payload["chats"][0]["status"] == "idle"
    assert "pendingQuestion" not in payload["chats"][0]
    assert payload["chats"][0]["lastRun"]["status"] == "error"
    assert payload["chats"][0]["lastRun"]["terminationReason"] == "process_restarted"
    assert payload["chats"][1]["pendingQuestion"]["id"] == "valid"
    assert written == [payload]


async def test_chat_run_driver_error_always_publishes_terminal_event_and_wakes_waiters(
    monkeypatch,
):
    from cyrene.workbench import chat as chat_mod
    from cyrene.workbench.chat_runs import ChatRunManager

    monkeypatch.setattr(chat_mod, "_settle_chat_running_status", lambda _chat_id: None)
    manager = ChatRunManager(retention_seconds=0)

    async def runner(_run):
        raise RuntimeError("driver exploded")

    run, is_new = manager.start_or_get(
        "chat_driver_error", {"type": "ack"}, runner, stream=True
    )

    async def broken_close(**_kwargs):
        raise RuntimeError("cleanup also failed")

    monkeypatch.setattr(run.inbox, "close", broken_close)
    await asyncio.wait_for(run.done.wait(), timeout=1)

    assert is_new is True
    assert run.status == "error"
    assert run.outcome["kind"] == "error"
    assert any(event.get("type") == "error" for event in run.events)


async def test_finished_run_remains_replayable_during_retention_window():
    from cyrene.workbench.chat_runs import ChatRunManager

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
    from cyrene.workbench.chat_runs import ChatRunManager

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
    from cyrene.workbench.chat_runs import ChatRunManager

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
    from cyrene.workbench.chat_runs import ChatRun, ChatRunEventStore

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


async def test_stream_deltas_are_batched_into_one_sqlite_transaction(
    monkeypatch,
    tmp_path,
):
    from cyrene.workbench.chat_runs import ChatRun, ChatRunEventStore

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

    assert batch_sizes == [33]
    restored = store.load_by_run_id(run.run_id)
    assert restored is not None
    assert [event["type"] for event in restored.events] == [
        "ack",
        *(["reply_delta"] * 32),
        "reply_done",
    ]


def test_startup_marks_unfinished_durable_run_as_process_restarted(tmp_path):
    from cyrene.workbench.chat_runs import ChatRun, ChatRunEventStore, ChatRunManager

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
    from cyrene.workbench.inbox import WorkbenchAgentInbox
    from cyrene.workbench.chat_runs import ChatRunManager

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


async def test_chat_detail_load_does_not_block_other_event_loop_work(monkeypatch):
    import time

    import httpx
    from fastapi import FastAPI
    from route.workbench import chat as chat_routes
    from cyrene.workbench.chat_runs import ChatRunManager

    started = threading.Event()
    release = threading.Event()

    def locked_read():
        started.set()
        release.wait(timeout=1)
        return {
            "chats": [{
                "id": "chat_locked",
                "projectId": "project_1",
                "title": "Locked",
                "status": "idle",
                "messages": [],
            }]
        }

    monkeypatch.setattr(chat_routes, "_read_chats_store", locked_read)
    monkeypatch.setattr(
        chat_routes, "_CHAT_RUN_MANAGER", ChatRunManager(retention_seconds=0)
    )
    app = FastAPI()
    chat_routes.register_workbench_chat_routes(app, bot=None, db_path="")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        before = time.monotonic()
        request = asyncio.create_task(client.get("/api/workbench/chats/chat_locked"))
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.sleep(0.03)
        elapsed = time.monotonic() - before

        assert elapsed < 0.2
        assert not request.done()
        release.set()
        response = await asyncio.wait_for(request, timeout=1)

    assert response.status_code == 200
    assert response.json()["chat"]["id"] == "chat_locked"
