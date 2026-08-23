from __future__ import annotations
from conftest import frontend_module_source, workbench_chat_source, workbench_i18n_source

import asyncio
import json
import multiprocessing
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor


async def _wait_for_query(db_path, query, expected, *, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query).fetchall()
        if rows == expected:
            return rows
        if asyncio.get_running_loop().time() >= deadline:
            return rows
        await asyncio.sleep(0.01)


def _initialize_inbox_schema_process(db_path, barrier, results) -> None:
    """Use a process-local schema cache/lock to exercise SQLite serialization."""
    try:
        from cyrene.workbench.persistence.schema import ensure_schema

        barrier.wait(timeout=10)
        ensure_schema(db_path)
        results.put("")
    except BaseException as exc:
        results.put(f"{type(exc).__name__}: {exc}")


async def test_tool_result_returns_through_session_inbox_while_guidance_is_retained(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    db_path = tmp_path / "workbench.db"
    inbox = WorkbenchAgentInbox("chat_1", str(db_path))
    inbox.round_id = "round_1"
    release = asyncio.Event()

    async def slow_tool() -> str:
        await release.wait()
        return "tool output"

    inbox.submit_tool("call_1", "Read", slow_tool)
    waiter = asyncio.create_task(inbox.wait_for_tool_result("call_1"))
    guidance = await inbox.put_guidance("先检查后端", client_request_id="guide_1")
    await asyncio.sleep(0)
    assert not waiter.done()

    release.set()
    assert await asyncio.wait_for(waiter, timeout=1) == "tool output"
    retained = inbox.collect_guidance_nowait()
    assert [event["payload"]["text"] for event in retained] == ["先检查后端"]
    inbox.acknowledge(retained)

    expected_rows = [
        ("guidance", "completed", "chat_1", "round_1"),
        ("tool_result", "completed", "chat_1", "round_1"),
    ]
    rows = await _wait_for_query(
        db_path,
        "SELECT event_type, status, session_id, round_id "
        "FROM workbench_agent_inbox ORDER BY created_at",
        expected_rows,
    )
    assert rows == expected_rows
    assert guidance["event_id"].startswith("evt_")
    await inbox.close()


async def test_tool_result_is_delivered_when_persistence_fails(tmp_path, monkeypatch):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_persist_failure", str(tmp_path / "workbench.db"))
    original_persist = inbox._persist

    def fail_only_tool_results(event):
        if event.get("type") == "tool_result":
            return None
        return original_persist(event)

    monkeypatch.setattr(inbox, "_persist", fail_only_tool_results)

    async def completed_tool() -> str:
        return "completed before persistence failed"

    inbox.submit_tool("call_persist_failure", "RecallMemory", completed_tool)
    result = await asyncio.wait_for(
        inbox.wait_for_tool_result("call_persist_failure"), timeout=1
    )
    assert result == "completed before persistence failed"
    await inbox.close()


async def test_structured_tool_error_is_marked_as_error(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    db_path = tmp_path / "workbench.db"
    inbox = WorkbenchAgentInbox("chat_structured_error", str(db_path))

    async def failed_tool() -> str:
        return json.dumps({
            "status": "error",
            "error": {
                "type": "invalid_arguments",
                "message": "bad arguments",
            },
        })

    inbox.submit_tool("call_error", "browser_tools", failed_tool)
    result = await asyncio.wait_for(
        inbox.wait_for_tool_result("call_error"), timeout=1
    )
    assert json.loads(result)["status"] == "error"
    await inbox.close()
    with sqlite3.connect(db_path) as conn:
        payload_json = conn.execute(
            "SELECT payload_json FROM workbench_agent_inbox "
            "WHERE event_type='tool_result'"
        ).fetchone()[0]
    assert json.loads(payload_json)["is_error"] is True


async def test_live_snapshot_exposes_current_tool_state_and_result_content():
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_live_snapshot")
    started = asyncio.Event()
    release = asyncio.Event()

    async def tool() -> str:
        started.set()
        await release.wait()
        return "fresh result content"

    inbox.submit_tool("call_live", "ReadWorkspace", tool)
    await asyncio.wait_for(started.wait(), timeout=1)
    running = inbox.live_snapshot()
    assert running["activeTasks"] == 1
    assert running["tools"][0] == {
        "toolCallId": "call_live",
        "toolName": "ReadWorkspace",
        "state": "running",
        "updatedAt": running["tools"][0]["updatedAt"],
    }

    release.set()
    deadline = asyncio.get_running_loop().time() + 1
    while not inbox.live_snapshot()["events"]:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0)
    ready = inbox.live_snapshot()
    assert ready["tools"][0]["state"] == "ready"
    assert ready["events"][0]["toolCallId"] == "call_live"
    assert ready["events"][0]["preview"] == "fresh result content"

    assert await inbox.wait_for_tool_result("call_live") == "fresh result content"
    consumed = inbox.live_snapshot()
    assert consumed["tools"][0]["state"] == "consumed"
    assert consumed["events"] == []
    await inbox.close()


async def test_live_snapshot_keeps_tool_arguments_across_state_changes():
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_live_arguments")
    started = asyncio.Event()
    release = asyncio.Event()

    async def tool() -> str:
        started.set()
        await release.wait()
        return "search complete"

    arguments = {"query": "南京旅游资源", "limit": 5}
    inbox.submit_tool(
        "call_live_arguments",
        "WebSearch",
        tool,
        metadata={"arguments": arguments},
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    running = inbox.live_snapshot()["tools"][0]
    assert running["state"] == "running"
    assert running["arguments"] == arguments

    release.set()
    assert await inbox.wait_for_tool_result("call_live_arguments") == "search complete"
    consumed = inbox.live_snapshot()["tools"][0]
    assert consumed["state"] == "consumed"
    assert consumed["arguments"] == arguments
    await inbox.close()


async def test_tool_result_wakes_agent_before_blocked_persistence_finishes(
    tmp_path, monkeypatch
):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_blocked_persist", str(tmp_path / "workbench.db"))
    original_persist = inbox._persist
    persistence_started = threading.Event()
    release_persistence = threading.Event()

    def block_only_tool_results(event):
        if event.get("type") == "tool_result":
            persistence_started.set()
            release_persistence.wait()
        return original_persist(event)

    monkeypatch.setattr(inbox, "_persist", block_only_tool_results)

    async def completed_tool() -> str:
        return "result delivered before SQLite"

    inbox.submit_tool("call_blocked_persist", "RecallMemory", completed_tool)
    try:
        result = await asyncio.wait_for(
            inbox.wait_for_tool_result("call_blocked_persist"), timeout=1
        )
        assert result == "result delivered before SQLite"
        assert await asyncio.to_thread(persistence_started.wait, 1)
        assert not release_persistence.is_set()
    finally:
        release_persistence.set()
        await inbox.close()


async def test_guidance_is_not_delivered_before_blocked_persistence_finishes(
    tmp_path, monkeypatch
):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_guidance_persist", str(tmp_path / "workbench.db"))
    original_persist = inbox._persist
    persistence_started = threading.Event()
    release_persistence = threading.Event()

    def block_only_guidance(event):
        if event.get("type") == "guidance":
            persistence_started.set()
            release_persistence.wait()
        return original_persist(event)

    monkeypatch.setattr(inbox, "_persist", block_only_guidance)
    try:
        put_task = asyncio.create_task(
            inbox.put_guidance(
                "立即改变方向", client_request_id="guide_blocked_persist"
            )
        )
        assert await asyncio.to_thread(persistence_started.wait, 1)
        guidance = inbox.collect_guidance_nowait()
        assert guidance == []
        assert not put_task.done()
        assert not release_persistence.is_set()
        release_persistence.set()
        event = await asyncio.wait_for(put_task, timeout=1)
        guidance = inbox.collect_guidance_nowait()
        assert [item["payload"]["text"] for item in guidance] == ["立即改变方向"]
        assert event["event_id"] == guidance[0]["event_id"]
        inbox.acknowledge(guidance)
    finally:
        release_persistence.set()
        await inbox.close()


async def test_duplicate_durable_tool_result_is_still_delivered_in_memory(tmp_path, monkeypatch):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_duplicate_result", str(tmp_path / "workbench.db"))
    monkeypatch.setattr(inbox, "_persist", lambda _event: False)
    monkeypatch.setattr(inbox, "_existing_event_id", lambda _key: "existing_result")

    async def completed_tool() -> str:
        return "deliver duplicate"

    inbox.submit_tool("call_duplicate", "ListSkills", completed_tool)
    assert await asyncio.wait_for(
        inbox.wait_for_tool_result("call_duplicate"), timeout=1
    ) == "deliver duplicate"
    await inbox.close()


async def test_workbench_chat_run_installs_its_own_inbox_context(tmp_path):
    from cyrene.workbench.inbox import current_workbench_inbox
    from cyrene.workbench.chat_runs import ChatRunManager

    manager = ChatRunManager(retention_seconds=0)
    manager.configure(str(tmp_path / "workbench.db"))
    seen = {}

    async def runner(run):
        seen["inbox"] = current_workbench_inbox()
        seen["chat_id"] = run.inbox.session_id

    run, is_new = manager.start_or_get("chat_a", {"type": "ack"}, runner, stream=True)
    await asyncio.wait_for(run.done.wait(), timeout=1)
    assert is_new is True
    assert seen == {"inbox": run.inbox, "chat_id": "chat_a"}


async def test_guidance_dedupe_returns_original_event_without_second_delivery(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_dedupe", str(tmp_path / "workbench.db"))
    first = await inbox.put_guidance("first", client_request_id="same-request")
    duplicate = await inbox.put_guidance("first", client_request_id="same-request")
    assert duplicate["duplicate"] is True
    assert duplicate["event_id"] == first["event_id"]
    events = inbox.collect_guidance_nowait()
    assert len(events) == 1
    inbox.acknowledge(events)
    await inbox.close()


async def test_completed_guidance_dedupe_survives_process_restart(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    db_path = tmp_path / "workbench.db"
    first = WorkbenchAgentInbox("chat_restart_dedupe", str(db_path), run_id="run_1")
    original = await first.put_guidance("first", client_request_id="same-request")
    events = first.collect_guidance_nowait()
    first.acknowledge(events)
    await first.close()

    resumed = WorkbenchAgentInbox("chat_restart_dedupe", str(db_path), run_id="run_2")
    duplicate = await resumed.put_guidance("changed", client_request_id="same-request")
    assert duplicate["duplicate"] is True
    assert duplicate["event_id"] == original["event_id"]
    assert resumed.collect_guidance_nowait() == []
    await resumed.close()


async def test_guidance_persistence_failure_rejects_without_delivery(tmp_path, monkeypatch):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_guidance_failure", str(tmp_path / "workbench.db"))
    monkeypatch.setattr(inbox, "_persist", lambda _event: None)

    try:
        await inbox.put_guidance("must be durable", client_request_id="durable-1")
    except RuntimeError as exc:
        assert "persist" in str(exc).lower()
    else:
        raise AssertionError("non-durable guidance was accepted")
    assert inbox.collect_guidance_nowait() == []
    await inbox.close()


async def test_terminal_guidance_check_is_atomic_with_durable_admission(
    tmp_path, monkeypatch
):
    from cyrene.workbench.inbox import (
        GuidanceAdmissionClosed,
        WorkbenchAgentInbox,
    )

    inbox = WorkbenchAgentInbox("chat_atomic_terminal", str(tmp_path / "workbench.db"))
    persist_started = threading.Event()
    release_persist = threading.Event()
    original_persist = inbox._persist

    def blocked_persist(event):
        persist_started.set()
        release_persist.wait(2)
        return original_persist(event)

    monkeypatch.setattr(inbox, "_persist", blocked_persist)
    put_task = asyncio.create_task(
        inbox.put_guidance("最后一刻改变方向", client_request_id="atomic-guide")
    )
    assert await asyncio.to_thread(persist_started.wait, 1)

    terminal_task = asyncio.create_task(inbox.collect_guidance_or_seal())
    await asyncio.sleep(0)
    assert terminal_task.done() is False

    release_persist.set()
    accepted = await asyncio.wait_for(put_task, timeout=1)
    terminal_guidance = await asyncio.wait_for(terminal_task, timeout=1)
    assert [item["event_id"] for item in terminal_guidance] == [accepted["event_id"]]

    inbox.acknowledge(terminal_guidance)
    assert await inbox.collect_guidance_or_seal() == []
    try:
        await inbox.put_guidance("已经太迟", client_request_id="after-seal")
    except GuidanceAdmissionClosed:
        pass
    else:
        raise AssertionError("guidance was admitted after the terminal seal")
    await inbox.close()


async def test_claimed_guidance_is_recovered_after_run_restart(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    db_path = tmp_path / "workbench.db"
    first = WorkbenchAgentInbox("chat_recover", str(db_path))
    event = await first.put_guidance("恢复这条引导", client_request_id="recover-1")
    claimed = first.collect_guidance_nowait()
    assert claimed[0]["event_id"] == event["event_id"]
    # No close call: simulate a hard process stop, where graceful cleanup never ran.
    resumed = WorkbenchAgentInbox("chat_recover", str(db_path))
    recovered = resumed.collect_guidance_nowait()
    assert [item["event_id"] for item in recovered] == [event["event_id"]]
    resumed.acknowledge(recovered)
    await first.close()
    await resumed.close()


async def test_graceful_close_cancels_unconsumed_events_with_run_reason(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    db_path = tmp_path / "workbench.db"
    inbox = WorkbenchAgentInbox("chat_close", str(db_path), run_id="run_close")
    await inbox.put_guidance("还没有处理", client_request_id="close-guide")
    claimed = inbox.collect_guidance_nowait()
    assert len(claimed) == 1
    await inbox.put(
        "tool_result",
        {"tool_call_id": "call_orphan", "tool_name": "Read", "result": "done"},
        batch_id="batch_orphan",
        dedupe_key="tool-result:call_orphan",
    )

    await inbox.close(termination_reason="user_interrupted")

    expected_rows = [
        ("guidance", "cancelled", "run_close", "", "user_interrupted"),
        ("tool_result", "cancelled", "run_close", "batch_orphan", "user_interrupted"),
    ]
    rows = await _wait_for_query(
        db_path,
        "SELECT event_type, status, run_id, batch_id, termination_reason "
        "FROM workbench_agent_inbox ORDER BY created_at",
        expected_rows,
    )
    assert rows == expected_rows


async def test_tool_lifecycle_telemetry_records_batch_queue_and_durations(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    db_path = tmp_path / "workbench.db"
    inbox = WorkbenchAgentInbox("chat_trace", str(db_path), run_id="run_trace")

    async def tool() -> str:
        await asyncio.sleep(0)
        return "ok"

    batch_id = inbox.submit_tool_batch(
        [("call_trace", "Read", tool)], batch_id="batch_trace"
    )
    assert batch_id == "batch_trace"
    assert await inbox.wait_for_tool_result("call_trace") == "ok"
    await inbox.close(termination_reason="completed")

    query = (
        "SELECT event_type, run_id, batch_id, tool_call_id, tool_name, "
        "queue_length, duration_ms, termination_reason, tool_execution_ms, "
        "result_wait_ms, result_queue_delay_ms, tool_queue_wait_ms, agent_wait_ms "
        "FROM workbench_agent_run_events ORDER BY created_at, rowid"
    )
    deadline = asyncio.get_running_loop().time() + 1
    while True:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query).fetchall()
        if len(rows) >= 5 or asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.01)
    assert [row[0] for row in rows] == [
        "tool_submitted",
        "tool_started",
        "tool_result_queued",
        "tool_result_consumed",
        "run_terminated",
    ]
    assert all(row[1] == "run_trace" for row in rows)
    assert all(row[5] >= 0 for row in rows)
    assert rows[0][2:5] == ("batch_trace", "call_trace", "Read")
    assert rows[2][6] is not None
    assert rows[3][6] is not None
    assert rows[-1][7] == "completed"
    assert rows[2][8] is not None
    assert rows[3][9] is not None
    assert rows[3][10] is not None
    assert rows[1][11] is not None
    assert rows[3][12] is not None


async def test_inbox_telemetry_flushes_by_threshold_and_close(
    tmp_path,
    monkeypatch,
):
    from cyrene.workbench import inbox as inbox_module

    monkeypatch.setattr(inbox_module, "_TELEMETRY_BATCH_MAX", 3)
    monkeypatch.setattr(inbox_module, "_TELEMETRY_FLUSH_INTERVAL_SECONDS", 60.0)
    inbox = inbox_module.WorkbenchAgentInbox(
        "chat_batch_trace",
        str(tmp_path / "workbench.db"),
        run_id="run_batch_trace",
    )
    batches = []
    original_record_events = inbox._record_events

    def tracked_record_events(rows):
        batches.append([row[5] for row in rows])
        original_record_events(rows)

    monkeypatch.setattr(inbox, "_record_events", tracked_record_events)
    inbox._record_event_background("trace_one")
    inbox._record_event_background("trace_two")
    inbox._record_event_background("trace_three")

    deadline = asyncio.get_running_loop().time() + 1
    while (
        not batches
        or (
            inbox._telemetry_flush_task is not None
            and not inbox._telemetry_flush_task.done()
        )
    ):
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.01)

    assert batches == [["trace_one", "trace_two", "trace_three"]]
    inbox._record_event_background("trace_four")
    await inbox.close()

    assert batches == [
        ["trace_one", "trace_two", "trace_three"],
        ["trace_four", "run_terminated"],
    ]


async def test_inbox_telemetry_flushes_after_short_interval(tmp_path, monkeypatch):
    from cyrene.workbench import inbox as inbox_module

    monkeypatch.setattr(inbox_module, "_TELEMETRY_BATCH_MAX", 64)
    monkeypatch.setattr(inbox_module, "_TELEMETRY_FLUSH_INTERVAL_SECONDS", 0.01)
    db_path = tmp_path / "workbench.db"
    inbox = inbox_module.WorkbenchAgentInbox(
        "chat_timer_trace",
        str(db_path),
        run_id="run_timer_trace",
    )
    inbox._record_event_background("timer_trace")

    rows = await _wait_for_query(
        db_path,
        "SELECT event_type FROM workbench_agent_run_events ORDER BY rowid",
        [("timer_trace",)],
    )
    assert rows == [("timer_trace",)]
    await inbox.close()


def test_inbox_schema_migrates_existing_database_without_dropping_events(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    db_path = tmp_path / "workbench.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE workbench_agent_inbox (
                event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                round_id TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL,
                status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
                dedupe_key TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "INSERT INTO workbench_agent_inbox VALUES "
            "('old_event','other_chat','','tool_result','completed',0,'','{}','old','old')"
        )

    WorkbenchAgentInbox("new_chat", str(db_path), run_id="run_migrated")

    with sqlite3.connect(db_path) as conn:
        inbox_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(workbench_agent_inbox)")
        }
        trace_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(workbench_agent_run_events)")
        }
        old_event = conn.execute(
            "SELECT event_id, status FROM workbench_agent_inbox WHERE event_id='old_event'"
        ).fetchone()
    assert {"run_id", "batch_id", "termination_reason"} <= inbox_columns
    assert {
        "run_id", "batch_id", "queue_length", "tool_queue_wait_ms",
        "tool_execution_ms", "agent_wait_ms", "result_wait_ms",
        "result_queue_delay_ms", "termination_reason",
    } <= trace_columns
    assert old_event == ("old_event", "completed")


def test_inbox_schema_migration_is_serialized_across_concurrent_runs(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    db_path = tmp_path / "workbench.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE workbench_agent_inbox (
                event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                round_id TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL,
                status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
                dedupe_key TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT ''
            )
            """
        )

    concurrency = 24
    barrier = threading.Barrier(concurrency)

    def initialize(index: int):
        barrier.wait()
        return WorkbenchAgentInbox(
            f"concurrent_chat_{index}", str(db_path), run_id=f"run_{index}"
        )

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        inboxes = list(pool.map(initialize, range(concurrency)))

    assert len(inboxes) == concurrency
    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(workbench_agent_inbox)"
            )
        }
    assert {"run_id", "batch_id", "termination_reason"} <= columns


def test_inbox_schema_migration_is_atomic_across_processes(tmp_path):
    db_path = tmp_path / "workbench.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE workbench_agent_inbox (
                event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                round_id TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL,
                status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
                dedupe_key TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "INSERT INTO workbench_agent_inbox VALUES "
            "('old_event','chat','','tool_result','completed',0,'','{}','old','old')"
        )

    context = multiprocessing.get_context("spawn")
    concurrency = 8
    barrier = context.Barrier(concurrency)
    results = context.Queue()
    processes = [
        context.Process(
            target=_initialize_inbox_schema_process,
            args=(str(db_path), barrier, results),
        )
        for _ in range(concurrency)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert [process.exitcode for process in processes] == [0] * concurrency
    assert [results.get(timeout=2) for _ in processes] == [""] * concurrency
    with sqlite3.connect(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        inbox_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(workbench_agent_inbox)")
        }
        run_event_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(workbench_agent_run_events)")
        }
        indexes = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        old_event = conn.execute(
            "SELECT status FROM workbench_agent_inbox WHERE event_id = 'old_event'"
        ).fetchone()

    assert {"workbench_agent_inbox", "workbench_agent_run_events"} <= tables
    assert {"run_id", "batch_id", "termination_reason"} <= inbox_columns
    assert {
        "tool_queue_wait_ms",
        "tool_execution_ms",
        "agent_wait_ms",
        "result_wait_ms",
        "result_queue_delay_ms",
    } <= run_event_columns
    assert {
        "idx_workbench_agent_inbox_dedupe",
        "idx_workbench_agent_inbox_pending",
        "idx_workbench_agent_inbox_completed",
        "idx_workbench_agent_run_events_run",
        "idx_workbench_agent_run_events_created",
    } <= indexes
    assert old_event == ("completed",)


def test_chat_run_manager_initializes_inbox_schema_before_run_storage_attach(
    monkeypatch, tmp_path
):
    from cyrene.workbench.chat_runs import ChatRun, ChatRunManager
    from cyrene.workbench.persistence import schema

    db_path = tmp_path / "workbench.db"
    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(schema.sqlite3, "connect", traced_connect)

    manager = ChatRunManager(retention_seconds=0)
    manager.configure(str(db_path))

    with real_connect(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        inbox_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(workbench_agent_inbox)")
        }
    assert {
        "workbench_state",
        "workbench_chats",
        "workbench_agent_inbox",
        "workbench_agent_run_events",
        "workbench_chat_runs",
        "workbench_chat_run_events",
    } <= tables
    assert {"run_id", "batch_id", "termination_reason"} <= inbox_columns

    statements.clear()
    run = ChatRun("chat_preinitialized", {"type": "ack"})
    run.inbox.configure_storage(str(db_path))

    normalized = [statement.strip().upper() for statement in statements]
    assert any(
        statement.startswith("SELECT") and "WORKBENCH_AGENT_INBOX" in statement
        for statement in normalized
    )
    assert not any(
        statement.startswith(("CREATE ", "ALTER ", "DROP "))
        or statement.startswith("PRAGMA JOURNAL_MODE")
        for statement in normalized
    )


def test_inbox_operational_connection_does_not_reconfigure_journal_mode(
    monkeypatch, tmp_path
):
    from cyrene.workbench.inbox import WorkbenchAgentInbox
    from cyrene.workbench.persistence import schema

    db_path = tmp_path / "workbench.db"
    WorkbenchAgentInbox("chat_schema_ready", str(db_path))
    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(schema.sqlite3, "connect", traced_connect)
    inbox = WorkbenchAgentInbox("chat_operational", str(db_path))
    statements.clear()

    with inbox._connect() as conn:
        conn.execute("SELECT 1").fetchone()

    normalized = [statement.strip().upper() for statement in statements]
    assert "PRAGMA BUSY_TIMEOUT = 5000" in normalized
    assert not any(
        statement.startswith("PRAGMA JOURNAL_MODE") for statement in normalized
    )


def test_inbox_startup_prunes_only_expired_terminal_history(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox
    from cyrene.workbench.persistence.schema import SCHEMA_READY

    db_path = tmp_path / "workbench.db"
    WorkbenchAgentInbox("chat_1", str(db_path), run_id="run_1")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workbench_agent_inbox(
                event_id, session_id, run_id, event_type, status,
                payload_json, created_at, completed_at
            ) VALUES
                ('expired', 'chat_1', 'run_1', 'tool_result', 'completed',
                 '{}', '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00'),
                ('pending', 'chat_1', 'run_1', 'guidance', 'queued',
                 '{}', '2020-01-01T00:00:00+00:00', '')
            """
        )
        conn.execute(
            """
            INSERT INTO workbench_agent_run_events(
                event_id, session_id, run_id, event_type, created_at
            ) VALUES ('expired_trace', 'chat_1', 'run_1', 'tool_started',
                      '2020-01-01T00:00:00+00:00')
            """
        )

    # A process restart clears the per-path schema cache and runs the bounded
    # startup maintenance again. New runs in the same process do not repeat it.
    SCHEMA_READY.discard(str(db_path.resolve()))
    WorkbenchAgentInbox("chat_2", str(db_path), run_id="run_2")

    with sqlite3.connect(db_path) as conn:
        inbox_ids = {
            row[0] for row in conn.execute(
                "SELECT event_id FROM workbench_agent_inbox"
            ).fetchall()
        }
        trace_count = conn.execute(
            "SELECT COUNT(*) FROM workbench_agent_run_events"
        ).fetchone()[0]
    assert inbox_ids == {"pending"}
    assert trace_count == 0


async def test_guidance_skips_not_yet_started_tools_in_submitted_batch(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_batch", str(tmp_path / "workbench.db"))
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_ran = False

    async def first_tool() -> str:
        first_started.set()
        await release_first.wait()
        return "first result"

    async def second_tool() -> str:
        nonlocal second_ran
        second_ran = True
        return "second result"

    inbox.submit_tool_batch([
        ("call_1", "Read", first_tool),
        ("call_2", "Write", second_tool),
    ])
    await asyncio.wait_for(first_started.wait(), timeout=1)
    await inbox.put_guidance("不要执行第二步", client_request_id="stop-second")
    release_first.set()

    assert await asyncio.wait_for(inbox.wait_for_tool_result("call_1"), timeout=1) == "first result"
    second_result = await asyncio.wait_for(inbox.wait_for_tool_result("call_2"), timeout=1)
    assert second_result.startswith("Skipped before execution")
    assert second_ran is False
    guidance = inbox.collect_guidance_nowait()
    inbox.acknowledge(guidance)
    await inbox.close()


async def test_read_only_calls_on_same_resource_run_in_parallel(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_parallel_reads", str(tmp_path / "workbench.db"))
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()

    async def first() -> str:
        first_started.set()
        await release.wait()
        return "first"

    async def second() -> str:
        second_started.set()
        await release.wait()
        return "second"

    read_meta = {
        "read_only": True,
        "resource_keys": ("fs:/tmp/shared",),
        "requires_order": False,
    }
    inbox.submit_tool_batch([
        ("call_read_1", "Read", first, read_meta),
        ("call_read_2", "Read", second, read_meta),
    ])
    await asyncio.wait_for(
        asyncio.gather(first_started.wait(), second_started.wait()), timeout=1
    )
    release.set()
    assert await inbox.wait_for_tool_result("call_read_1") == "first"
    assert await inbox.wait_for_tool_result("call_read_2") == "second"
    await inbox.close()


def test_tool_registry_resolves_parallel_safety_metadata_from_arguments(tmp_path):
    from cyrene.tooling.catalog import TOOL_DEFS, TOOL_METADATA, get_tool_execution_metadata

    target = tmp_path / "nested" / "file.txt"
    read_meta = get_tool_execution_metadata("Read", {"path": str(target)})
    write_meta = get_tool_execution_metadata("Write", {"path": str(target)})
    browser_meta = get_tool_execution_metadata("browser_snapshot", {})
    unknown_meta = get_tool_execution_metadata("mcp_unknown", {})

    assert read_meta == {
        "read_only": True,
        "resource_keys": (f"fs:{target.resolve()}",),
        "requires_order": False,
    }
    assert write_meta == {
        "read_only": False,
        "resource_keys": (f"fs:{target.resolve()}",),
        "requires_order": False,
    }
    assert browser_meta["requires_order"] is True
    assert unknown_meta == {
        "read_only": False,
        "resource_keys": ("tool:mcp_unknown",),
        "requires_order": True,
    }
    registered_names = {
        str((tool_def.get("function") or {}).get("name") or "")
        for tool_def in TOOL_DEFS
    }
    assert registered_names <= set(TOOL_METADATA)
    assert all(
        {"read_only", "resource_keys", "requires_order"} <= set(TOOL_METADATA[name])
        for name in registered_names
    )


async def test_writes_to_same_resource_are_serial_but_distinct_resources_parallel(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_resource_conflict", str(tmp_path / "workbench.db"))
    first_started = asyncio.Event()
    conflicting_started = asyncio.Event()
    distinct_started = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> str:
        first_started.set()
        await release_first.wait()
        return "first"

    async def conflicting() -> str:
        conflicting_started.set()
        return "conflicting"

    async def distinct() -> str:
        distinct_started.set()
        return "distinct"

    def write_meta(resource: str) -> dict:
        return {
            "read_only": False,
            "resource_keys": (resource,),
            "requires_order": False,
        }

    inbox.submit_tool_batch([
        ("call_write_1", "Write", first, write_meta("fs:/tmp/a")),
        ("call_write_2", "Edit", conflicting, write_meta("fs:/tmp/a")),
        ("call_write_3", "Write", distinct, write_meta("fs:/tmp/b")),
    ])
    await asyncio.wait_for(
        asyncio.gather(first_started.wait(), distinct_started.wait()), timeout=1
    )
    assert conflicting_started.is_set() is False
    release_first.set()
    await asyncio.wait_for(conflicting_started.wait(), timeout=1)
    assert await inbox.wait_for_tool_result("call_write_1") == "first"
    assert await inbox.wait_for_tool_result("call_write_2") == "conflicting"
    assert await inbox.wait_for_tool_result("call_write_3") == "distinct"
    await inbox.close()


async def test_requires_order_call_is_a_batch_barrier(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_order_barrier", str(tmp_path / "workbench.db"))
    first_started = asyncio.Event()
    barrier_started = asyncio.Event()
    trailing_started = asyncio.Event()
    release_first = asyncio.Event()
    release_barrier = asyncio.Event()
    read_meta = {"read_only": True, "resource_keys": ("network:web",), "requires_order": False}
    barrier_meta = {"read_only": False, "resource_keys": ("chat:messages",), "requires_order": True}

    async def first() -> str:
        first_started.set()
        await release_first.wait()
        return "first"

    async def barrier() -> str:
        barrier_started.set()
        await release_barrier.wait()
        return "barrier"

    async def trailing() -> str:
        trailing_started.set()
        return "trailing"

    inbox.submit_tool_batch([
        ("call_before", "WebFetch", first, read_meta),
        ("call_barrier", "send_message", barrier, barrier_meta),
        ("call_after", "WebFetch", trailing, read_meta),
    ])
    await asyncio.wait_for(first_started.wait(), timeout=1)
    assert not barrier_started.is_set()
    assert not trailing_started.is_set()
    release_first.set()
    await asyncio.wait_for(barrier_started.wait(), timeout=1)
    assert not trailing_started.is_set()
    release_barrier.set()
    await asyncio.wait_for(trailing_started.wait(), timeout=1)
    assert await inbox.wait_for_tool_result("call_before") == "first"
    assert await inbox.wait_for_tool_result("call_barrier") == "barrier"
    assert await inbox.wait_for_tool_result("call_after") == "trailing"
    await inbox.close()


async def test_parallel_results_can_arrive_out_of_order_and_are_consumed_in_model_order(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_out_of_order", str(tmp_path / "workbench.db"))
    slow_started = asyncio.Event()
    fast_done = asyncio.Event()
    release_slow = asyncio.Event()
    read_meta = {"read_only": True, "resource_keys": ("network:web",), "requires_order": False}

    async def slow() -> str:
        slow_started.set()
        await release_slow.wait()
        return "slow"

    async def fast() -> str:
        fast_done.set()
        return "fast"

    inbox.submit_tool_batch([
        ("call_slow", "WebFetch", slow, read_meta),
        ("call_fast", "WebFetch", fast, read_meta),
    ])
    await asyncio.wait_for(asyncio.gather(slow_started.wait(), fast_done.wait()), timeout=1)
    slow_waiter = asyncio.create_task(inbox.wait_for_tool_result("call_slow"))
    await asyncio.sleep(0)
    assert not slow_waiter.done()
    release_slow.set()
    assert await asyncio.wait_for(slow_waiter, timeout=1) == "slow"
    assert await asyncio.wait_for(inbox.wait_for_tool_result("call_fast"), timeout=1) == "fast"
    await inbox.close()


async def test_live_inbox_prioritizes_guidance_over_already_queued_tool_result(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_priority", str(tmp_path / "workbench.db"))
    await inbox.put(
        "tool_result",
        {"tool_call_id": "call_1", "tool_name": "Read", "result": "ready"},
        dedupe_key="tool-result:call_1",
    )
    await inbox.put_guidance("优先处理我", client_request_id="priority-guide")

    guidance = inbox.collect_guidance_nowait()
    assert [event["payload"]["text"] for event in guidance] == ["优先处理我"]
    inbox.acknowledge(guidance)
    assert await asyncio.wait_for(inbox.wait_for_tool_result("call_1"), timeout=1) == "ready"
    await inbox.close()


async def test_acknowledging_one_guidance_does_not_clear_newer_guidance_signal(tmp_path):
    from cyrene.workbench.inbox import WorkbenchAgentInbox

    inbox = WorkbenchAgentInbox("chat_guidance_race", str(tmp_path / "workbench.db"))
    await inbox.put_guidance("first", client_request_id="race-1")
    first = inbox.collect_guidance_nowait()
    await inbox.put_guidance("second", client_request_id="race-2")
    inbox.acknowledge(first)

    ran = False

    async def should_not_run() -> str:
        nonlocal ran
        ran = True
        return "bad"

    inbox.submit_tool_batch([("call_race", "Write", should_not_run)])
    result = await asyncio.wait_for(inbox.wait_for_tool_result("call_race"), timeout=1)
    assert result.startswith("Skipped before execution")
    assert ran is False
    second = inbox.collect_guidance_nowait()
    assert [event["payload"]["text"] for event in second] == ["second"]
    inbox.acknowledge(second)
    await inbox.close()


async def test_workbench_guidance_endpoint_queues_into_live_chat(monkeypatch, tmp_path):
    import httpx
    from fastapi import FastAPI
    from cyrene.workbench import chat as chat_service
    from route.workbench.chat import register_workbench_chat_routes
    from cyrene.workbench.chat_runs import ChatRunManager

    db_path = tmp_path / "workbench.db"
    chats_path = tmp_path / "workbench_chats.json"
    chats_path.write_text(
        json.dumps({
            "chats": [{
                "id": "chat_live",
                "projectId": "project_1",
                "title": "Live",
                "status": "running",
                "messages": [],
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-01T00:00:00+00:00",
            }]
        }),
        encoding="utf-8",
    )
    manager = ChatRunManager(retention_seconds=0)
    monkeypatch.setattr(chat_service, "_CHATS_STORE", chats_path)
    monkeypatch.setattr(chat_service, "_CONFIGURED_CHATS_STORE", None)
    monkeypatch.setattr(chat_service, "_CHAT_RUN_MANAGER", manager)

    app = FastAPI()
    register_workbench_chat_routes(app, bot=None, db_path=str(db_path))
    release = asyncio.Event()

    async def runner(_run):
        await release.wait()

    run, _ = manager.start_or_get("chat_live", {"type": "ack"}, runner, stream=True)
    transport = httpx.ASGITransport(app=app)
    # A live run is authoritative and must not wait behind the durable chats
    # document read on every sidebar poll.
    def unexpected_store_read():
        raise AssertionError("active inbox poll should use the in-memory run")

    with monkeypatch.context() as active_patch:
        active_patch.setattr(chat_service, "_read_chats_store", unexpected_store_read)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            active_inbox = await client.get("/api/workbench/chats/chat_live/inbox")
    assert active_inbox.status_code == 200
    assert active_inbox.json()["active"] is True

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workbench/chats/chat_live/guidance",
            json={"message": "先做后端", "clientRequestId": "guide_http_1"},
        )
        inbox_response = await client.get(
            "/api/workbench/chats/chat_live/inbox"
        )
    assert response.status_code == 200
    assert response.json()["queued"] is True
    assert inbox_response.status_code == 200
    assert inbox_response.headers["cache-control"] == "no-store"
    inbox_payload = inbox_response.json()
    assert inbox_payload["active"] is True
    assert inbox_payload["observedAt"]
    assert inbox_payload["counts"]["queued"] == 1
    assert inbox_payload["live"]["events"][0]["type"] == "guidance"
    assert inbox_payload["events"][0]["type"] == "guidance"
    assert inbox_payload["events"][0]["preview"] == "先做后端"
    queued = run.inbox.collect_guidance_nowait()
    assert [event["payload"]["text"] for event in queued] == ["先做后端"]
    run.inbox.acknowledge(queued)
    stored = chat_service._read_chats_store()
    assert stored["chats"][0]["messages"][-1]["guidance"] is True
    assert stored["chats"][0]["messages"][-1]["content"] == "先做后端"
    assert any(event.get("type") == "guidance_received" for event in run.events)
    run.status = "finishing"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        late = await client.post(
            "/api/workbench/chats/chat_live/guidance",
            json={"message": "too late", "clientRequestId": "guide_http_2"},
        )
    assert late.status_code == 409
    release.set()
    await asyncio.wait_for(run.done.wait(), timeout=1)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        idle_inbox = await client.get("/api/workbench/chats/chat_live/inbox")
    assert idle_inbox.status_code == 200
    assert idle_inbox.json()["active"] is False
    assert idle_inbox.json()["runId"] == ""
    assert idle_inbox.json()["events"] == []
    assert idle_inbox.json()["tools"] == []


async def test_workbench_guidance_endpoint_maps_sealed_admission_to_follow_up(
    monkeypatch, tmp_path
):
    import httpx
    from fastapi import FastAPI
    from cyrene.workbench import chat as chat_service
    from route.workbench.chat import register_workbench_chat_routes
    from cyrene.workbench.chat_runs import ChatRunManager

    chats_path = tmp_path / "workbench_chats.json"
    chats_path.write_text(json.dumps({"chats": [{
        "id": "chat_sealed",
        "projectId": "project_1",
        "status": "running",
        "messages": [],
    }]}), encoding="utf-8")
    manager = ChatRunManager(retention_seconds=0)
    monkeypatch.setattr(chat_service, "_CHATS_STORE", chats_path)
    monkeypatch.setattr(chat_service, "_CONFIGURED_CHATS_STORE", None)
    monkeypatch.setattr(chat_service, "_CHAT_RUN_MANAGER", manager)
    app = FastAPI()
    register_workbench_chat_routes(
        app, bot=None, db_path=str(tmp_path / "workbench.db")
    )
    release = asyncio.Event()

    async def runner(_run):
        await release.wait()

    run, _ = manager.start_or_get("chat_sealed", {"type": "ack"}, runner)
    await run.ready.wait()
    assert await run.inbox.collect_guidance_or_seal() == []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response_task = asyncio.create_task(
            client.post(
                "/api/workbench/chats/chat_sealed/guidance",
                json={"message": "作为下一条继续", "clientRequestId": "sealed-guide"},
            )
        )
        await asyncio.sleep(0)
        assert response_task.done() is False
        release.set()
        response = await asyncio.wait_for(response_task, timeout=1)
    assert response.status_code == 409
    assert response.json()["code"] == "chat_not_running"
    await run.done.wait()


async def test_startup_reconciles_durable_guidance_missing_from_transcript(
    monkeypatch, tmp_path
):
    from cyrene.workbench.inbox import WorkbenchAgentInbox
    from cyrene.workbench import chat as chat_mod

    db_path = tmp_path / "workbench.db"
    chats_path = tmp_path / "workbench_chats.json"
    chats_path.write_text(
        json.dumps({
            "chats": [{
                "id": "chat_reconcile",
                "messages": [],
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-01T00:00:00+00:00",
            }]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(chat_mod, "_CHATS_STORE", chats_path)
    monkeypatch.setattr(chat_mod, "_CONFIGURED_CHATS_STORE", None)
    monkeypatch.setattr(chat_mod, "_STORE_DB_PATH", "")

    inbox = WorkbenchAgentInbox(
        "chat_reconcile", str(db_path), run_id="run_reconcile"
    )
    event = await inbox.put_guidance(
        "recover visible guidance",
        client_request_id="req_reconcile",
        public_message_id="msg_reconcile",
        public_created_at="2026-01-01T00:00:02+00:00",
    )

    assert chat_mod._reconcile_inbox_guidance_messages(str(db_path)) == 1
    assert chat_mod._reconcile_inbox_guidance_messages(str(db_path)) == 0
    stored = chat_mod._read_chats_store()
    assert stored["chats"][0]["messages"] == [{
        "id": "msg_reconcile",
        "role": "user",
        "content": "recover visible guidance",
        "createdAt": "2026-01-01T00:00:02+00:00",
        "guidance": True,
        "guidanceEventId": event["event_id"],
        "runId": "run_reconcile",
        "clientRequestId": "req_reconcile",
    }]
    await inbox.close()


async def test_main_agent_resumes_from_tool_result_and_applies_runtime_guidance(monkeypatch, tmp_path):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent import state as state_mod
    from cyrene.workbench.inbox import WorkbenchAgentInbox, _workbench_agent_inbox

    inbox = WorkbenchAgentInbox("chat_agent", str(tmp_path / "workbench.db"))
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    model_calls = []

    async def fake_llm(messages, tools=None, **_kwargs):
        model_calls.append(messages)
        if len(model_calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "phase1_use",
                    "type": "function",
                    "function": {"name": "use_tools", "arguments": '{"task":"inspect"}'},
                }],
            }
        if len(model_calls) == 2:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_read",
                    "type": "function",
                    "function": {"name": "Read", "arguments": '{"file_path":"x"}'},
                }],
            }
        assert any(
            "先只检查后端" in str(message.get("content") or "")
            for message in messages
        )
        assert any(
            message.get("role") == "tool"
            and message.get("tool_call_id") == "call_read"
            and message.get("content") == "read result"
            for message in messages
        )
        return {
            "role": "assistant",
            "content": "已按引导完成",
            "tool_calls": [{
                "id": "call_quit",
                "type": "function",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        }

    async def fake_tool(name, _args, _bot, _chat_id, _db_path, _notify):
        assert name == "Read"
        tool_started.set()
        await release_tool.wait()
        return "read result"

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_execute_tool", fake_tool)
    monkeypatch.setattr(agent_mod, "_append_session_message", noop)
    monkeypatch.setattr(agent_mod, "_save_session_messages", noop)
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", noop)

    round_token = state_mod._current_round_id.set("round_agent")
    inbox_token = _workbench_agent_inbox.set(inbox)
    try:
        task = asyncio.create_task(
            agent_mod._run_main_agent("inspect", [], None, 0, "db.sqlite3")
        )
        await asyncio.wait_for(tool_started.wait(), timeout=1)
        await inbox.put_guidance("先只检查后端", client_request_id="guide_agent")
        release_tool.set()
        assert await asyncio.wait_for(task, timeout=2) == "已按引导完成"
    finally:
        _workbench_agent_inbox.reset(inbox_token)
        state_mod._current_round_id.reset(round_token)
        await inbox.close()

    assert len(model_calls) == 3


async def test_promoted_phase1_tool_keeps_runtime_guidance_open(monkeypatch, tmp_path):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent import state as state_mod
    from cyrene.workbench.inbox import WorkbenchAgentInbox, _workbench_agent_inbox

    inbox = WorkbenchAgentInbox("chat_promoted", str(tmp_path / "workbench.db"))
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    model_calls = []

    async def fake_llm(messages, tools=None, **_kwargs):
        model_calls.append(messages)
        if len(model_calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_promoted_read",
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "arguments": '{"file_path":"x"}',
                    },
                }],
            }
        assert any(
            "只读取摘要" in str(message.get("content") or "")
            for message in messages
        )
        return {
            "role": "assistant",
            "content": "已读取摘要",
            "tool_calls": [{
                "id": "call_promoted_quit",
                "type": "function",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        }

    async def fake_tool(name, _args, _bot, _chat_id, _db_path, _notify):
        assert name == "Read"
        tool_started.set()
        await release_tool.wait()
        return "read result"

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_execute_tool", fake_tool)
    monkeypatch.setattr(agent_mod, "_append_session_message", noop)
    monkeypatch.setattr(agent_mod, "_save_session_messages", noop)
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", noop)

    round_token = state_mod._current_round_id.set("round_promoted")
    inbox_token = _workbench_agent_inbox.set(inbox)
    try:
        task = asyncio.create_task(
            agent_mod._run_main_agent("inspect", [], None, 0, "db.sqlite3")
        )
        await asyncio.wait_for(tool_started.wait(), timeout=1)
        await inbox.put_guidance("只读取摘要", client_request_id="guide_promoted")
        release_tool.set()
        assert await asyncio.wait_for(task, timeout=2) == "已读取摘要"
    finally:
        release_tool.set()
        _workbench_agent_inbox.reset(inbox_token)
        state_mod._current_round_id.reset(round_token)
        await inbox.close()

    assert len(model_calls) == 2


async def test_main_agent_quit_waits_for_already_submitted_tool(monkeypatch, tmp_path):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent import state as state_mod
    from cyrene.workbench.inbox import WorkbenchAgentInbox, _workbench_agent_inbox

    inbox = WorkbenchAgentInbox("chat_quit_wait", str(tmp_path / "workbench.db"))
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    quit_generated = asyncio.Event()

    async def existing_tool():
        tool_started.set()
        await release_tool.wait()
        return "existing result"

    inbox.submit_tool("existing_call", "Read", existing_tool)
    await asyncio.wait_for(tool_started.wait(), timeout=1)

    async def fake_llm(_messages, tools=None, **_kwargs):
        quit_generated.set()
        return {
            "role": "assistant",
            "content": "在途工具已经完成，当前任务现已结束。",
            "tool_calls": [{
                "id": "quit_now",
                "type": "function",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        }

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_append_session_message", noop)
    monkeypatch.setattr(agent_mod, "_save_session_messages", noop)

    round_token = state_mod._current_round_id.set("round_quit_wait")
    inbox_token = _workbench_agent_inbox.set(inbox)
    try:
        task = asyncio.create_task(
            agent_mod._run_main_agent("结束", [], None, 0, "db.sqlite3")
        )
        await asyncio.wait_for(quit_generated.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not task.done()
        release_tool.set()
        assert await asyncio.wait_for(task, timeout=2) == "在途工具已经完成，当前任务现已结束。"
    finally:
        _workbench_agent_inbox.reset(inbox_token)
        state_mod._current_round_id.reset(round_token)
        await inbox.close()


async def test_main_agent_runs_independent_read_calls_in_parallel(monkeypatch, tmp_path):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent import state as state_mod
    from cyrene.workbench.inbox import WorkbenchAgentInbox, _workbench_agent_inbox

    inbox = WorkbenchAgentInbox("chat_agent_parallel", str(tmp_path / "workbench.db"))
    started = {"a": asyncio.Event(), "b": asyncio.Event()}
    release = asyncio.Event()
    model_calls = 0

    async def fake_llm(messages, tools=None, **_kwargs):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return {
                "role": "assistant", "content": "",
                "tool_calls": [{
                    "id": "phase1_use", "type": "function",
                    "function": {"name": "use_tools", "arguments": '{"task":"inspect"}'},
                }],
            }
        if model_calls == 2:
            return {
                "role": "assistant", "content": "",
                "tool_calls": [
                    {
                        "id": "call_a", "type": "function",
                        "function": {"name": "Read", "arguments": '{"path":"a.txt"}'},
                    },
                    {
                        "id": "call_b", "type": "function",
                        "function": {"name": "Read", "arguments": '{"path":"b.txt"}'},
                    },
                ],
            }
        assert any(message.get("tool_call_id") == "call_a" for message in messages)
        assert any(message.get("tool_call_id") == "call_b" for message in messages)
        return {
            "role": "assistant", "content": "parallel reads completed successfully",
            "tool_calls": [{
                "id": "call_quit", "type": "function",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        }

    async def fake_tool(_name, args, _bot, _chat_id, _db_path, _notify):
        key = str(args["path"])[0]
        started[key].set()
        await release.wait()
        return key

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_execute_tool", fake_tool)
    monkeypatch.setattr(agent_mod, "_append_session_message", noop)
    monkeypatch.setattr(agent_mod, "_save_session_messages", noop)
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", noop)

    round_token = state_mod._current_round_id.set("round_parallel")
    inbox_token = _workbench_agent_inbox.set(inbox)
    try:
        task = asyncio.create_task(
            agent_mod._run_main_agent("inspect", [], None, 0, "db.sqlite3")
        )
        await asyncio.wait_for(
            asyncio.gather(started["a"].wait(), started["b"].wait()), timeout=1
        )
        release.set()
        assert await asyncio.wait_for(task, timeout=2) == "parallel reads completed successfully"
    finally:
        _workbench_agent_inbox.reset(inbox_token)
        state_mod._current_round_id.reset(round_token)
        await inbox.close()

    assert model_calls == 3


async def test_main_agent_applies_guidance_sent_while_model_call_is_in_flight(monkeypatch, tmp_path):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent import state as state_mod
    from cyrene.workbench.inbox import WorkbenchAgentInbox, _workbench_agent_inbox

    inbox = WorkbenchAgentInbox("chat_model_guidance", str(tmp_path / "workbench.db"))
    model_started = asyncio.Event()
    release_model = asyncio.Event()
    first_model_cancelled = asyncio.Event()
    model_calls = []

    async def fake_llm(messages, tools=None, **_kwargs):
        model_calls.append(messages)
        if len(model_calls) == 1:
            model_started.set()
            try:
                await release_model.wait()
                return {
                    "role": "assistant", "content": "旧答案",
                    "tool_calls": [{
                        "id": "quit_old", "type": "function",
                        "function": {"name": "quit", "arguments": "{}"},
                    }],
                }
            except asyncio.CancelledError:
                first_model_cancelled.set()
                raise
        assert any(
            "改成新答案" in str(message.get("content") or "")
            for message in messages
        )
        return {
            "role": "assistant", "content": "新答案",
            "tool_calls": [{
                "id": "quit_new", "type": "function",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        }

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_append_session_message", noop)
    monkeypatch.setattr(agent_mod, "_save_session_messages", noop)
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", noop)

    round_token = state_mod._current_round_id.set("round_model_guidance")
    inbox_token = _workbench_agent_inbox.set(inbox)
    try:
        task = asyncio.create_task(
            agent_mod._run_main_agent("回答问题", [], None, 0, "db.sqlite3")
        )
        await asyncio.wait_for(model_started.wait(), timeout=5)
        await inbox.put_guidance("改成新答案", client_request_id="guide_during_model")
        assert await asyncio.wait_for(task, timeout=2) == "新答案"
        assert first_model_cancelled.is_set()
    finally:
        release_model.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        _workbench_agent_inbox.reset(inbox_token)
        state_mod._current_round_id.reset(round_token)
        await inbox.close()

    assert len(model_calls) == 2


async def test_main_agent_keeps_wrap_reply_and_continues_with_late_guidance(monkeypatch, tmp_path):
    from cyrene.agent import agent as agent_mod
    from cyrene.agent import state as state_mod
    from cyrene.workbench.inbox import WorkbenchAgentInbox, _workbench_agent_inbox

    inbox = WorkbenchAgentInbox("chat_wrap_guidance", str(tmp_path / "workbench.db"))
    wrap_started = asyncio.Event()
    release_wrap = asyncio.Event()
    model_calls = []
    saved = []
    wrap_calls = 0

    async def fake_llm(messages, tools=None, **_kwargs):
        model_calls.append([dict(message) for message in messages])
        if len(model_calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "phase1_use",
                    "type": "function",
                    "function": {"name": "use_tools", "arguments": '{"task":"inspect"}'},
                }],
            }
        if len(model_calls) == 2:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "quit_old",
                    "type": "function",
                    "function": {"name": "quit", "arguments": "{}"},
                }],
            }
        assert any(
            "继续处理新要求" in str(message.get("content") or "")
            for message in messages
        )
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "quit_new",
                "type": "function",
                "function": {"name": "quit", "arguments": "{}"},
            }],
        }

    async def fake_final_reply(_messages, max_tokens=None):
        nonlocal wrap_calls
        wrap_calls += 1
        if wrap_calls == 1:
            wrap_started.set()
            await release_wrap.wait()
            return "已经生成的旧回复"
        return "按新指令完成的回复"

    async def fake_save(messages, **_kwargs):
        saved.append([dict(message) for message in messages])

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_mod, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_mod, "_final_user_reply_from_history", fake_final_reply)
    monkeypatch.setattr(agent_mod, "_append_session_message", noop)
    monkeypatch.setattr(agent_mod, "_save_session_messages", fake_save)
    monkeypatch.setattr(agent_mod, "_publish_runtime_event", noop)

    async def stream_noop(_event):
        return None

    round_token = state_mod._current_round_id.set("round_wrap_guidance")
    writer_token = state_mod._reply_stream_writer.set(stream_noop)
    inbox_token = _workbench_agent_inbox.set(inbox)
    try:
        task = asyncio.create_task(
            agent_mod._run_main_agent("先完成原任务", [], None, 0, "db.sqlite3")
        )
        await asyncio.wait_for(wrap_started.wait(), timeout=1)
        guidance = await inbox.put_guidance(
            "继续处理新要求", client_request_id="guide_during_wrap"
        )
        release_wrap.set()
        assert await asyncio.wait_for(task, timeout=2) == "按新指令完成的回复"
    finally:
        _workbench_agent_inbox.reset(inbox_token)
        state_mod._reply_stream_writer.reset(writer_token)
        state_mod._current_round_id.reset(round_token)
        await inbox.close()

    assert wrap_calls == 2
    assert len(model_calls) == 3
    assert saved
    final_messages = saved[-1]
    old_reply = next(
        message for message in final_messages
        if message.get("content") == "已经生成的旧回复"
    )
    assert old_reply["intermediate_reply"] is True
    assert any(
        message.get("runtime_guidance")
        and "继续处理新要求" in str(message.get("content") or "")
        for message in final_messages
    )
    assert final_messages[-1]["content"] == "按新指令完成的回复"

    with inbox._connect() as conn:
        status = conn.execute(
            "SELECT status FROM workbench_agent_inbox WHERE event_id = ?",
            (guidance["event_id"],),
        ).fetchone()[0]
    assert status == "completed"


async def test_chat_run_interrupt_cleans_pending_inbox_with_run_id(tmp_path):
    from cyrene.workbench.chat_runs import ChatRunManager

    db_path = tmp_path / "workbench.db"
    manager = ChatRunManager(retention_seconds=0)
    manager.configure(str(db_path))
    ready = asyncio.Event()

    async def runner(run):
        await run.inbox.put_guidance("中断前引导", client_request_id="interrupt-guide")
        run.inbox.collect_guidance_nowait()
        await run.inbox.put(
            "tool_result",
            {"tool_call_id": "call_interrupt", "tool_name": "Read", "result": "ready"},
            batch_id="batch_interrupt",
            dedupe_key="tool-result:call_interrupt",
        )
        ready.set()
        await asyncio.Event().wait()

    run, _ = manager.start_or_get("chat_interrupt", {"type": "ack"}, runner)
    await asyncio.wait_for(ready.wait(), timeout=1)
    assert manager.interrupt("chat_interrupt") is True
    await asyncio.wait_for(run.done.wait(), timeout=1)

    expected_rows = [
        ("cancelled", run.run_id, "user_interrupted"),
        ("cancelled", run.run_id, "user_interrupted"),
    ]
    deadline = asyncio.get_running_loop().time() + 1
    while True:
        with sqlite3.connect(db_path) as conn:
            pending_rows = conn.execute(
                "SELECT status, run_id, termination_reason FROM workbench_agent_inbox "
                "ORDER BY created_at"
            ).fetchall()
            terminal = conn.execute(
                "SELECT run_id, termination_reason FROM workbench_agent_run_events "
                "WHERE event_type='run_terminated'"
            ).fetchone()
        if (
            pending_rows == expected_rows
            and terminal == (run.run_id, "user_interrupted")
        ) or asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.01)
    assert pending_rows == expected_rows
    assert terminal == (run.run_id, "user_interrupted")


def test_workbench_composer_switches_stop_button_to_guidance_when_typed():
    composer = frontend_module_source("features/chat/composer.jsx")
    actions = frontend_module_source("features/chat/chat-action-controller.jsx")
    model_api = frontend_module_source("features/chat/model-api.jsx")
    runtime = frontend_module_source("features/chat/file-resources.jsx")
    split_pane = frontend_module_source("features/chat/split-pane.jsx")

    assert "var hasRuntimeGuidance = running && !!draft.trim();" in composer
    assert "running && !hasRuntimeGuidance ? onInterrupt : submit" in composer
    assert 'wbcT("workbenchChat.sendGuidance", "Send guidance")' in composer
    assert "context.model.sendGuidance(chatId, text, requestId)" in actions
    assert "timeout: 0" in model_api.split("function sendGuidance", 1)[1].split(
        "function answerChat", 1
    )[0]
    assert 'id: "guidance_pending_" + requestId' in actions
    assert "guidance: true, optimistic: true" in actions
    assert "wbcMergeChronologicalMessages(previous.messages || [], [optimistic])" in actions
    assert 'error.code === "chat_not_running"' in actions
    assert "context.runtimeEngine.deferSend(chatId, { message: text }, context.model)" in actions
    assert "terminal event wakes the deferred send" in runtime
    assert "if (!runtimes[chatId])" in runtime.split("function deferSend", 1)[1].split(
        "function setHooks", 1
    )[0]
    split = split_pane.split("function WbcChatSplit(", 1)[1].split(
        "function WbcSideAgentSplitResizer", 1
    )[0]
    side_agent = split_pane.split("function WbcSideAgentTab(", 1)[1].split(
        "function WbcSideAgentsPanel", 1
    )[0]
    assert "function guide(message)" in split
    assert "onGuidance={guide}" in split
    assert "setRunning(false);" not in split.split("function stop()", 1)[1].split(
        "function guide", 1
    )[0]
    assert "function guide(message)" in side_agent
    assert "onGuidance={guide}" in side_agent
    assert "disabled={compact && running}" not in composer


def test_runtime_guidance_marker_is_not_sent_as_an_upstream_message_field():
    from cyrene.call_llm import _strip_internal_fields

    assert _strip_internal_fields({
        "role": "user",
        "content": "guide",
        "runtime_guidance": True,
    }) == {"role": "user", "content": "guide"}


async def test_interrupt_waits_for_workbench_run_cleanup_before_acknowledging(
    monkeypatch,
):
    import asyncio
    from types import SimpleNamespace

    from cyrene.workbench import global_chat_service
    from cyrene.workbench.global_chat_service import GlobalChatApplicationService
    from cyrene.workbench.subagent_messaging_service import SubagentMessagingService

    done = asyncio.Event()
    calls: list[str] = []

    class RunManager:
        def get(self, session_id: str):
            calls.append(f"get:{session_id}")
            return SimpleNamespace(done=done)

        def interrupt(self, session_id: str):
            calls.append(f"manager:{session_id}")
            asyncio.get_running_loop().call_soon(done.set)
            return True

    monkeypatch.setattr(global_chat_service, "get_chat_run_manager", RunManager)
    monkeypatch.setattr(
        global_chat_service.agent,
        "interrupt_active_run",
        lambda session_id="": calls.append(f"agent:{session_id}") or False,
    )

    def settle(session_id: str):
        assert done.is_set()
        calls.append(f"settle:{session_id}")

    monkeypatch.setattr(global_chat_service, "settle_chat_running_status", settle)
    service = GlobalChatApplicationService(
        "",
        bot=None,
        subagents=SubagentMessagingService(None, ""),
        reset_agent_lottery=lambda: None,
    )

    result = await service.interrupt("chat_1")

    assert result == {"ok": True, "interrupted": True}
    assert calls == [
        "get:chat_1",
        "agent:chat_1",
        "manager:chat_1",
        "settle:chat_1",
    ]


def test_main_prompt_prefers_inbox_wakeup_over_fixed_time_waits():
    from cyrene.agent.prompts import _MAIN_AGENT_PROMPT
    from cyrene.tooling.guidance import PACK_USAGE_GUIDANCE

    assert "single stable `toolbox` gateway" in _MAIN_AGENT_PROMPT
    assert "event-driven completion" in PACK_USAGE_GUIDANCE["browser_tools"]
    assert "wake_on_exit=true" in PACK_USAGE_GUIDANCE["code_tools"]
    assert "Never use Bash `sleep`" not in _MAIN_AGENT_PROMPT


def test_learned_skills_require_explicit_successful_inspection_without_auto_router():
    from pathlib import Path
    from cyrene.agent.prompts import _MAIN_AGENT_PROMPT
    from cyrene.tooling.guidance import PACK_USAGE_GUIDANCE
    from cyrene.tooling.packs import CAPABILITY_BINDINGS

    source = Path("src/cyrene/agent/agent.py").read_text(encoding="utf-8")
    assert "try_route_and_execute_skill" not in source
    assert "single stable `toolbox` gateway" in _MAIN_AGENT_PROMPT
    assert "skill.get_learned" not in _MAIN_AGENT_PROMPT
    skill_ids = {item[0] for item in CAPABILITY_BINDINGS["skill_tools"]}
    assert {"skill.get_learned", "skill.run_learned"} <= skill_ids
    guidance = PACK_USAGE_GUIDANCE["skill_tools"]
    assert "Inspect a matching learned skill before running it" in guidance
    assert "Never invent skill names" in guidance


def test_subagent_monitoring_has_no_fixed_two_second_completion_sleep():
    from pathlib import Path

    agent_source = Path("src/cyrene/agent/agent.py").read_text(encoding="utf-8")
    guidance_source = Path("src/cyrene/agent/guidance.py").read_text(encoding="utf-8")
    assert "await asyncio.sleep(2)" not in agent_source
    assert "await asyncio.sleep(2)" not in guidance_source


def test_workbench_has_localized_model_fallback_progress_message():

    source = workbench_i18n_source()
    assert '"phase.modelFallback": "Primary model unavailable' in source
    assert '"phase.modelFallback": "主模型不可用，正在切换备用模型' in source


def test_workbench_has_actionable_codex_failure_alerts():

    i18n = workbench_i18n_source()
    chat = workbench_chat_source()
    for key in (
        "phase.codexQuotaExhausted",
        "phase.codexAuthenticationExpired",
        "phase.codexModelUnavailable",
    ):
        assert i18n.count(f'"{key}"') == 2
    assert "if (event.alert && workbenchServices.feedback().showToast)" in chat
    assert "failed: !!event.failed" in chat
