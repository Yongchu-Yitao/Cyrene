from __future__ import annotations

import asyncio
import threading


def test_startup_recovers_crashed_running_chat_and_clears_stale_question(monkeypatch):
    from webui import routes_workbench_chat as chat_mod
    from webui.workbench_chat_runs import ChatRunManager

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
    assert payload["chats"][1]["pendingQuestion"]["id"] == "valid"
    assert written == [payload]


async def test_chat_run_driver_error_always_publishes_terminal_event_and_wakes_waiters(
    monkeypatch,
):
    from webui import routes_workbench_chat as chat_mod
    from webui.workbench_chat_runs import ChatRunManager

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
    from webui.workbench_chat_runs import ChatRunManager

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


async def test_chat_run_storage_setup_runs_off_the_event_loop(monkeypatch, tmp_path):
    from cyrene.workbench_inbox import WorkbenchAgentInbox
    from webui.workbench_chat_runs import ChatRunManager

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
    from webui import routes_workbench_chat as chat_mod
    from webui.workbench_chat_runs import ChatRunManager

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

    monkeypatch.setattr(chat_mod, "_read_chats_store", locked_read)
    monkeypatch.setattr(
        chat_mod, "_CHAT_RUN_MANAGER", ChatRunManager(retention_seconds=0)
    )
    app = FastAPI()
    chat_mod.register_workbench_chat_routes(app, bot=None, db_path="")
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
