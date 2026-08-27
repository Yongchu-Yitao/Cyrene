from __future__ import annotations

import pytest

from agent.plugin import PluginContext, PluginRegistry, PluginRuntime
from agent.plugin.plugin_impl.cyrene_schedule import plugin_pack
from cyrene.runtime.database import init_db
from agent.plugin.plugin_impl.cyrene_schedule.service import ScheduleRuntimeService


@pytest.mark.asyncio
async def test_schedule_edit_is_atomic_and_project_scoped(tmp_path):
    db_path = str(tmp_path / "cyrene.sqlite3")
    await init_db(db_path)
    service = ScheduleRuntimeService(db_path)
    await service.ensure_ready()
    selected_id = await service.repository.create(
        chat_id=-1,
        prompt="old prompt",
        schedule_type="interval",
        schedule_value="3600",
        next_run="2029-01-01T00:00:00+00:00",
        project_id="project_a",
    )
    untouched_id = await service.repository.create(
        chat_id=-1,
        prompt="leave me alone",
        schedule_type="cron",
        schedule_value="0 9 * * *",
        next_run="2029-01-01T01:00:00+00:00",
        project_id="project_b",
    )
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    runtime = PluginRuntime(registry)
    context = PluginContext(
        data={"source": "workbench", "project_id": "project_a"},
        services={"schedules": service},
    )

    invalid = await runtime.call(
        "schedule.edit",
        {
            "task_id": selected_id,
            "prompt": "must not leak through",
            "schedule_value": "not-an-interval",
        },
        context,
    )
    assert invalid.success is False
    selected = await service.repository.get(selected_id, "project_a")
    assert selected is not None
    assert selected.prompt == "old prompt"

    changed = await runtime.call(
        "schedule.edit",
        {
            "task_id": selected_id,
            "prompt": "new prompt",
            "schedule_type": "once",
            "schedule_value": "2030-01-02T03:04:05+00:00",
        },
        context,
    )
    assert changed.success is True
    selected = await service.repository.get(selected_id, "project_a")
    untouched = await service.repository.get(untouched_id, "project_b")
    assert selected is not None and selected.prompt == "new prompt"
    assert selected.schedule_type == "once"
    assert selected.next_run == "2030-01-02T03:04:05+00:00"
    assert untouched is not None and untouched.prompt == "leave me alone"

    outside_scope = await runtime.call(
        "schedule.edit",
        {"task_id": untouched_id, "prompt": "forbidden"},
        context,
    )
    assert outside_scope.success is False
