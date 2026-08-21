from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_edit_task_partially_updates_only_the_selected_task(tmp_path):
    from cyrene.runtime import database
    from cyrene.tool_impl.task.edit_task import _tool_edit_task

    db_path = str(tmp_path / "cyrene.sqlite3")
    await database.init_db(db_path)
    selected_id = await database.create_task(
        db_path,
        -1,
        "old prompt",
        "interval",
        "3600",
        "2029-01-01T00:00:00+00:00",
    )
    untouched_id = await database.create_task(
        db_path,
        -1,
        "leave me alone",
        "cron",
        "0 9 * * *",
        "2029-01-01T01:00:00+00:00",
    )
    await database.update_task_status(db_path, selected_id, "paused")

    result = await _tool_edit_task(
        {
            "task_id": selected_id,
            "prompt": "new prompt",
            "schedule_type": "once",
            "schedule_value": "2030-01-02T03:04:05+00:00",
        },
        None,
        -1,
        db_path,
        None,
    )

    selected = await database.get_task(db_path, selected_id)
    untouched = await database.get_task(db_path, untouched_id)
    assert result.startswith(f"Task {selected_id} updated:")
    assert selected is not None
    assert selected["prompt"] == "new prompt"
    assert selected["schedule_type"] == "once"
    assert selected["schedule_value"] == "2030-01-02T03:04:05+00:00"
    assert selected["next_run"] == "2030-01-02T03:04:05+00:00"
    assert selected["status"] == "paused"
    assert untouched is not None
    assert untouched["prompt"] == "leave me alone"
    assert untouched["schedule_type"] == "cron"


@pytest.mark.asyncio
async def test_edit_task_rejects_invalid_schedule_without_mutating_task(tmp_path):
    from cyrene.runtime import database
    from cyrene.tool_impl.task.edit_task import _tool_edit_task

    db_path = str(tmp_path / "cyrene.sqlite3")
    await database.init_db(db_path)
    task_id = await database.create_task(
        db_path,
        -1,
        "original",
        "interval",
        "3600",
        "2029-01-01T00:00:00+00:00",
    )

    result = await _tool_edit_task(
        {
            "task_id": task_id,
            "prompt": "must not leak through",
            "schedule_value": "not-an-interval",
        },
        None,
        -1,
        db_path,
        None,
    )

    task = await database.get_task(db_path, task_id)
    assert result.startswith("Invalid schedule:")
    assert task is not None
    assert task["prompt"] == "original"
    assert task["schedule_value"] == "3600"


def test_edit_task_is_registered_in_the_task_tool_pack():
    from cyrene.tooling import catalog
    from cyrene.tooling.native_definitions import get_native_tool_def
    from cyrene.tooling.packs import CAPABILITY_BINDINGS

    assert get_native_tool_def("edit_task")["function"]["name"] == "edit_task"
    assert ("task.edit", "edit_task") in CAPABILITY_BINDINGS["task_tools"]
    assert "edit_task" in catalog.get_tool_names()

