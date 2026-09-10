"""Regression coverage for lock duration, commit ownership and cancellation."""
import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from cyrene.observability import debug
from cyrene.platform import database
from cyrene.platform.persistence import analytics
from cyrene.plugins.builtin.cyrene_schedule.migrations import initialize_schedule_database
from cyrene.plugins.builtin.cyrene_schedule.repository import ScheduleRepository


async def wait_thread(event):
    assert await asyncio.to_thread(event.wait, 5), "worker did not reach checkpoint"


async def test_empty_schedule_does_not_wait_for_writer(tmp_path):
    path = str(tmp_path / "schedule.db")
    await initialize_schedule_database(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        assert await asyncio.wait_for(ScheduleRepository(path).claim_due(), 1) == []
    finally:
        connection.rollback()
        connection.close()


async def test_stats_cancel_after_commit_acknowledges_exact_batch(monkeypatch, tmp_path):
    path = str(tmp_path / "stats.db")
    await database.init_db(path)
    monkeypatch.setattr(debug, "DB_PATH", path)
    monkeypatch.setattr(debug, "_telemetry_pending", debug.deque())
    monkeypatch.setattr(debug, "_telemetry_batch_task", None)
    committed, release = threading.Event(), threading.Event()
    original = analytics._record_usage_stats_batch
    calls = []

    def write(*args):
        calls.append(1)
        original(*args)
        committed.set()
        assert release.wait(5)

    monkeypatch.setattr(analytics, "_record_usage_stats_batch", write)
    first = {"type": "tool_call", "timestamp": "2026-09-10T00:00:00Z", "tool": "Read"}
    debug._telemetry_pending.append(first)
    task = asyncio.create_task(debug._flush_telemetry_batch())
    other = None
    try:
        await wait_thread(committed)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # A second cancellation must not detach the writer.
        other = asyncio.create_task(debug._flush_telemetry_batch())
        await asyncio.sleep(0)
        later = dict(first)
        debug._telemetry_pending.append(later)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await other
        assert len(calls) == 1
        assert list(debug._telemetry_pending) == [later]
        await debug._flush_telemetry_batch()
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT SUM(tool_calls) FROM daily_stats").fetchone()[0] == 2
        assert not debug._telemetry_pending
    finally:
        release.set()
        await asyncio.gather(task, *([other] if other else []), return_exceptions=True)


async def test_stats_failure_rolls_back_all_counters(tmp_path):
    path = str(tmp_path / "rollback.db")
    await database.init_db(path)
    with sqlite3.connect(path) as conn:
        before = {table: conn.execute(f"SELECT * FROM {table}").fetchall()
                  for table in ("daily_stats", "daily_tool_stats", "token_usage")}
        conn.execute("CREATE TRIGGER fail_tokens BEFORE INSERT ON token_usage BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        await database.record_usage_stats_batch(
            path, tool_events=[("2026-09-10T00:00:00Z", "Read")],
            token_events=[{"model": "test", "total_tokens": 1}],
        )
    with sqlite3.connect(path) as conn:
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == rows


async def due_repository(tmp_path):
    path = str(tmp_path / "due.db")
    await initialize_schedule_database(path)
    repo = ScheduleRepository(path)
    await repo.create(
        chat_id=-1, prompt="test", schedule_type="interval", schedule_value="60",
        next_run=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )
    return repo


async def test_cancelled_claim_releases_committed_unconsumed_lease(monkeypatch, tmp_path):
    repo = await due_repository(tmp_path)
    committed, release = threading.Event(), threading.Event()
    original = repo._claim_due
    claimed = []

    def claim(**kwargs):
        result = original(**kwargs)
        claimed.extend(result)
        committed.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(repo, "_claim_due", claim)
    task = asyncio.create_task(repo.claim_due())
    try:
        await wait_thread(committed)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        with sqlite3.connect(repo.db_path) as conn:
            assert conn.execute("SELECT lease_token FROM scheduled_tasks").fetchone()[0] is None
            assert conn.execute("SELECT status FROM task_run_logs").fetchone()[0] == "interrupted"
        reclaimed = await repo.claim_due()
        assert len(reclaimed) == 1
        assert reclaimed[0].run_id == claimed[0].run_id
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_claim_failure_rolls_back_lease_and_log(tmp_path):
    repo = await due_repository(tmp_path)
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute("CREATE TRIGGER fail_log BEFORE INSERT ON task_run_logs BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        await repo.claim_due()
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("SELECT lease_token FROM scheduled_tasks").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM task_run_logs").fetchone()[0] == 0


async def test_stats_commit_does_not_need_event_loop_progress(monkeypatch, tmp_path):
    path = str(tmp_path / "progress.db")
    await database.init_db(path)
    started, continue_write, committed = (threading.Event() for _ in range(3))
    original_connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            result = super().execute(sql, *args, **kwargs)
            if sql.startswith("INSERT OR IGNORE INTO daily_stats"):
                started.set()
                assert continue_write.wait(5)
            return result

        def commit(self):
            super().commit()
            committed.set()

    def connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=Connection)

    monkeypatch.setattr(analytics.sqlite3, "connect", connect)
    task = asyncio.create_task(database.record_usage_stats_batch(
        path, tool_events=[("2026-09-10T00:00:00Z", "Read")],
    ))
    try:
        await wait_thread(started)
        continue_write.set()
        # Deliberately stop the event loop. The SQLite owner must still commit.
        assert committed.wait(2)
        await task
    finally:
        continue_write.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_schedule_revalidates_after_positive_precheck(monkeypatch, tmp_path):
    repo = await due_repository(tmp_path)
    original_connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "BEGIN IMMEDIATE":
                # Another connection pauses the task after the read precheck.
                other = original_connect(repo.db_path)
                try:
                    other.execute("UPDATE scheduled_tasks SET status='paused'")
                    other.commit()
                finally:
                    other.close()
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=Connection)

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert await repo.claim_due() == []
    with original_connect(repo.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_run_logs").fetchone()[0] == 0
