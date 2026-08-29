"""Regression coverage for background energy-efficiency fixes."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


def test_electron_background_renderers_are_throttled():
    source = (
        Path(__file__).resolve().parents[1] / "electron" / "main.js"
    ).read_text(encoding="utf-8")

    assert "backgroundThrottling: false" not in source
    # Main renderer, browser page, native tab picker, and browser chat overlay
    # all retain Chromium's background throttling.
    assert source.count("backgroundThrottling: true") == 4
    assert "browser-tab-picker-preload.js" in source


async def test_scheduler_uses_independent_maintenance_cadences(
    tmp_path,
    monkeypatch,
):
    from cyrene.plugins import background
    from cyrene.workbench.application import notifications as workbench_notifications

    monkeypatch.setenv("CYRENE_PLUGIN_IMPL_DIR", str(tmp_path / "plugin_impl"))
    monkeypatch.setenv("SCHEDULER_INTERVAL", "60")
    monkeypatch.setattr(workbench_notifications, "configure_store", lambda _path: None)

    instance = background.setup_background_plugin_scheduler(
        str(tmp_path / "cyrene.runtime.database")
    )
    jobs = {job.id: job for job in instance.get_jobs()}

    assert set(jobs) == {
        "scheduled_tasks",
        "plugin_registry_sync",
        "behavior_learning",
        "proactive_heartbeat",
        "steward",
        "short_term_cleanup",
    }
    assert jobs["scheduled_tasks"].trigger.interval.total_seconds() == 60
    assert jobs["behavior_learning"].trigger.interval.total_seconds() == 600
    assert jobs["proactive_heartbeat"].trigger.interval.total_seconds() == 1800
    assert jobs["proactive_heartbeat"].next_run_time is not None
    assert jobs["steward"].trigger.interval.total_seconds() == 3600
    assert jobs["short_term_cleanup"].trigger.interval.total_seconds() == 86400


async def test_due_task_job_invokes_hidden_plugin_not_maintenance(monkeypatch, tmp_path):
    from cyrene.plugins import background
    from cyrene.workbench.application import notifications as workbench_notifications

    monkeypatch.setenv("CYRENE_PLUGIN_IMPL_DIR", str(tmp_path / "plugin_impl"))
    monkeypatch.setattr(workbench_notifications, "configure_store", lambda _path: None)
    scheduler = background.setup_background_plugin_scheduler(
        str(tmp_path / "runtime.sqlite3")
    )
    scheduled_job = scheduler.get_job("scheduled_tasks")
    assert scheduled_job is not None
    host = scheduled_job.func.__self__
    plugin_call = AsyncMock(
        return_value=SimpleNamespace(success=True, error="")
    )
    monkeypatch.setattr(background.PluginRuntime, "call", plugin_call)

    binding = host._installed["scheduled_tasks"]
    await host._invoke(
        "schedule.tick",
        {},
        "scheduled_tasks",
        binding.pack_id,
        binding.source,
        binding.handler_identity,
    )

    plugin_call.assert_awaited_once()
    assert plugin_call.await_args.args[0] == "schedule.tick"


async def test_disabled_background_packs_are_not_scheduled_or_invoked(
    monkeypatch, tmp_path
):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from cyrene.plugins.background import BackgroundPluginHost

    monkeypatch.setenv("CYRENE_PLUGIN_IMPL_DIR", str(tmp_path / "plugin_impl"))
    scheduler = AsyncIOScheduler()
    host = BackgroundPluginHost(scheduler)
    host.registry.configure_activation(
        plugins={},
        packs={
            "cyrene_schedule": False,
            "cyrene_proactive": False,
            "cyrene_skills": False,
            "cyrene_memory": False,
        },
    )
    host.attach()

    jobs = {job.id for job in scheduler.get_jobs()}
    assert "scheduled_tasks" not in jobs
    assert "proactive_heartbeat" not in jobs
    assert "behavior_learning" not in jobs
    assert "steward" not in jobs
    assert "short_term_cleanup" not in jobs
    assert "plugin_registry_sync" in jobs

    plugin_call = AsyncMock()
    monkeypatch.setattr("cyrene.plugins.background.PluginRuntime.call", plugin_call)
    for plugin_name, job_id in (
        ("schedule.tick", "scheduled_tasks"),
        ("proactive.heartbeat", "proactive_heartbeat"),
        ("skills.learning.tick", "behavior_learning"),
        ("memory.steward.tick", "steward"),
        ("memory.short_term.cleanup", "short_term_cleanup"),
    ):
        await host._invoke(plugin_name, {}, job_id, None, "disabled", 0)
    plugin_call.assert_not_awaited()


def test_steward_reads_recent_workbench_session_archives(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.cyrene_memory import steward
    from cyrene.workbench.sessions import context as workbench_context

    conversations = tmp_path / "conversations"
    conversations.mkdir()
    recent = conversations / "wbchat_recent.md"
    recent.write_text(
        "# Conversation wbchat_recent\n\n"
        "## 2026-07-28 12:00:00 CST\n\n"
        "**User**: 我决定下周发布新版本。\n",
        encoding="utf-8",
    )
    old = conversations / "wbchat_old.md"
    old.write_text("old conversation", encoding="utf-8")
    old_timestamp = time.time() - 7200
    old.touch()
    import os
    os.utime(old, (old_timestamp, old_timestamp))

    memory_service = SimpleNamespace(
        conversations_directory=conversations,
        session_conversations_directory=lambda workspace_path: (
            Path(workspace_path) / "conversations"
        ),
    )
    monkeypatch.setattr(
        workbench_context,
        "read_projects",
        lambda: [],
    )

    text = steward.recent_workbench_conversations(
        memory_service,
        time.time() - 3600,
    )

    assert "wbchat_recent.md" in text
    assert "project_id=default" in text
    assert "下周发布新版本" in text
    assert "wbchat_old.md" not in text


def test_steward_normalizes_soul_commands_and_excludes_entities():
    from cyrene.plugins.builtin.cyrene_memory import steward

    result = steward.normalize_soul_commands(
        "APPEND MEMORY:HIGH_IMPACT :: exact fact\n"
        "APPEND: PATTERN:USER — legacy preference\n"
        "MERGE RELATIONSHIP:USER: old|||new\n"
        'ENTITY project_id="project_1" type="task" title="Ship" '
        'confidence="0.9" content="Release it"\n'
        "ERASE: 无\n"
    )

    assert result.splitlines() == [
        "APPEND MEMORY:HIGH_IMPACT :: exact fact",
        "APPEND PATTERN:USER :: legacy preference",
        "MERGE RELATIONSHIP:USER :: old|||new",
    ]


async def test_steward_processes_workbench_archive_without_owner_id(
    tmp_path, monkeypatch
):
    import cyrene.core.plugin as plugin_runtime
    from cyrene.plugins.builtin.cyrene_memory import steward

    model_runner = AsyncMock(return_value="SKIP")
    monkeypatch.setattr(steward, "has_daily_conversation", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        steward,
        "recent_workbench_conversations",
        lambda *_args, **_kwargs: "Workbench conversation text",
    )
    memory_service = SimpleNamespace(
        data_directory=tmp_path,
        conversations_directory=tmp_path / "conversations",
        recent_conversations=AsyncMock(return_value=""),
    )
    soul_service = SimpleNamespace(
        read=lambda: "# Independent Soul",
        apply_update=lambda _commands: [],
    )
    monkeypatch.setattr(plugin_runtime, "application_plugin_service", lambda _name: None)

    ran = await steward.run_steward_if_needed(
        memory_service,
        interval=3600,
        now=100_000,
        model_runner=model_runner,
        soul_application=soul_service,
    )

    assert ran is True
    model_runner.assert_awaited_once_with(
        "Workbench conversation text",
        "# Independent Soul",
        None,
    )
    assert (tmp_path / "memory_steward.json").exists()


async def test_steward_background_plugin_delegates_to_memory_service():
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin import cyrene_memory

    run_steward = AsyncMock(return_value=True)
    service = SimpleNamespace(run_steward_if_needed=run_steward)

    result = await cyrene_memory._steward_tick(
        {}, PluginContext(services={"memory": service})
    )

    assert result == {"ok": True, "ran": True}
    run_steward.assert_awaited_once_with(interval=cyrene_memory.STEWARD_INTERVAL)




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
