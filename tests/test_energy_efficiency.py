"""Regression coverage for background energy-efficiency fixes."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock


def test_electron_background_renderers_are_throttled():
    source = (
        Path(__file__).resolve().parents[1] / "electron" / "main.js"
    ).read_text(encoding="utf-8")

    assert "backgroundThrottling: false" not in source
    assert source.count("backgroundThrottling: true") == 3


async def test_scheduler_uses_independent_maintenance_cadences(
    tmp_path,
    monkeypatch,
):
    from cyrene.runtime import scheduler
    from cyrene.workbench import chat as routes_workbench_chat
    from cyrene.workbench import notifications as workbench_notifications

    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_get_heartbeat_interval", lambda: 1800)
    monkeypatch.setattr(scheduler, "SCHEDULER_INTERVAL", 60)
    monkeypatch.setattr(scheduler, "PATTERN_DETECTION_INTERVAL", 600)
    monkeypatch.setattr(scheduler, "STEWARD_INTERVAL", 3600)
    monkeypatch.setattr(scheduler, "_workbench_db_path", "")
    monkeypatch.setattr(routes_workbench_chat, "configure_store", lambda _path: None)
    monkeypatch.setattr(workbench_notifications, "configure_store", lambda _path: None)

    instance = scheduler.setup_scheduler(None, str(tmp_path / "cyrene.runtime.database"))
    jobs = {job.id: job for job in instance.get_jobs()}

    assert set(jobs) == {
        "scheduled_tasks",
        "behavior_learning",
        "proactive_heartbeat",
        "steward",
        "short_term_cleanup",
    }
    assert jobs["scheduled_tasks"].trigger.interval.total_seconds() == 60
    assert jobs["behavior_learning"].trigger.interval.total_seconds() == 600
    assert jobs["proactive_heartbeat"].trigger.interval.total_seconds() == 1800
    assert jobs["steward"].trigger.interval.total_seconds() == 3600
    assert jobs["short_term_cleanup"].trigger.interval.total_seconds() == 86400


async def test_due_task_poll_does_not_run_heavy_maintenance(monkeypatch):
    from cyrene.runtime import scheduler

    due_tasks = AsyncMock()
    proactive = AsyncMock()
    steward = AsyncMock()
    learning = AsyncMock()
    monkeypatch.setattr(scheduler, "_check_and_execute_tasks", due_tasks)
    monkeypatch.setattr(scheduler, "_heartbeat_proactive_check", proactive)
    monkeypatch.setattr(scheduler, "_run_steward_if_needed", steward)
    monkeypatch.setattr(scheduler, "_behavior_learning_tick", learning)

    await scheduler._scheduled_task_tick(None, "cyrene.runtime.database")

    due_tasks.assert_awaited_once_with(None, "cyrene.runtime.database")
    proactive.assert_not_awaited()
    steward.assert_not_awaited()
    learning.assert_not_awaited()


async def test_behavior_learning_kicks_are_coalesced(monkeypatch):
    from cyrene.learning import engine as behavior_learning
    from cyrene.agent import coordinator

    process = AsyncMock()
    monkeypatch.setattr(behavior_learning, "process_unprocessed_turns", process)
    monkeypatch.setattr(coordinator, "PATTERN_DETECTION_INTERVAL", 1)

    await coordinator._kick_behavior_learning_processing()
    first_task = coordinator._DEFERRED_BEHAVIOR_TASK
    await coordinator._kick_behavior_learning_processing()

    assert coordinator._DEFERRED_BEHAVIOR_TASK is first_task
    process.assert_not_awaited()
    first_task.cancel()
    await coordinator.shutdown_background_tasks()


async def test_single_tool_turn_skips_learning_llm(tmp_path, monkeypatch):
    from cyrene.learning import engine as learning

    await learning.init(tmp_path, tmp_path)
    calls: list[str] = []

    async def capture(prompt: str, *, caller: str = "behavior_learning"):
        calls.append(f"{caller}:{prompt[:40]}")
        return {"purpose": "不应生成"}

    monkeypatch.setattr(learning, "_call_llm_json", capture)
    context = await learning.begin_turn(
        session_id="single-tool-session",
        round_id="single-tool-round",
        user_message="查询上海天气",
        history=[],
    )
    await learning.record_action(
        "search_web",
        {"query": "上海天气"},
        "main_agent",
        "single-tool-round",
        10,
        result="晴",
        success=True,
    )
    await learning.complete_turn(
        turn_id=context["turn_id"],
        assistant_response="上海今天晴。",
    )
    learning.clear_turn_context(context)

    result = await learning.process_unprocessed_turns(force=True)

    assert result["processed_turns"] == 1
    assert calls == []
    async with learning._conn() as conn:
        row = await (
            await conn.execute(
                "SELECT purpose FROM behavior_turn_tool_chains WHERE turn_id = ?",
                (context["turn_id"],),
            )
        ).fetchone()
    assert row["purpose"] == ""


async def test_llm_usage_and_latency_can_share_one_batch(tmp_path):
    from cyrene.runtime import database as db

    db_path = tmp_path / "telemetry.db"
    await db.init_db(str(db_path))
    await db.record_llm_telemetry_batch(
        str(db_path),
        token_events=[{
            "model": "test-model",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "duration_ms": 500,
            "caller": "test",
        }],
        latency_events=[{
            "call_id": "llm-test",
            "model": "test-model",
            "endpoint": "https://example.test/v1/chat/completions",
            "outcome": "success",
            "request_ms": 500,
            "total_call_ms": 505,
        }],
    )

    with sqlite3.connect(db_path) as conn:
        usage_count = conn.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0]
        latency_count = conn.execute(
            "SELECT COUNT(*) FROM llm_latency_events"
        ).fetchone()[0]

    assert usage_count == 1
    assert latency_count == 1
