import asyncio
from datetime import datetime, timedelta, timezone

from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime
from cyrene.plugins.native_tools import seed_builtin_plugin_directory
from cyrene.platform.database import init_db
from cyrene.plugins.builtin.cyrene_schedule.service import ScheduleRuntimeService


def _runtime(tmp_path):
    db_path = str(tmp_path / "schedule.sqlite3")
    asyncio.run(init_db(db_path))
    plugin_directory = tmp_path / "plugin_impl"
    seed_builtin_plugin_directory(plugin_directory)
    registry = PluginRegistry()
    failures = registry.load_directory(plugin_directory)
    assert not [item for item in failures if item.path.name == "cyrene_schedule"]
    service = ScheduleRuntimeService(db_path, plugin_directory=plugin_directory)
    context = PluginContext(
        data={"source": "workbench", "project_id": "project_a", "session_id": ""},
        services={"schedules": service},
    )
    return PluginRuntime(registry), service, context


def test_toolbox_list_describe_invoke_and_hidden_tick(tmp_path):
    runtime, service, context = _runtime(tmp_path)

    async def scenario():
        listed = await runtime.call("toolbox", {"operation": "list"}, context)
        assert listed.success is True
        assert "cyrene_schedule" in listed.value["packs"]

        described = await runtime.call(
            "toolbox",
            {"operation": "describe", "name": "cyrene_schedule"},
            context,
        )
        assert described.success is True
        names = {item["name"] for item in described.value["plugins"]}
        assert "schedule.create" in names
        assert "schedule.occurrences" not in names
        assert "schedule.tick" not in names
        assert all(
            item["pack"] == "cyrene_schedule"
            for item in described.value["plugins"]
        )

        hidden = await runtime.call(
            "toolbox",
            {"operation": "describe", "name": "schedule.tick"},
            context,
        )
        assert hidden.success is False

        invoked = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "schedule.create",
                "arguments": {
                    "prompt": "Plugin chain check",
                    "schedule_type": "interval",
                    "schedule_value": "60",
                    "permission_mode": "full_access",
                },
            },
            context,
        )
        assert invoked.success is True
        assert invoked.value["pack"] == "cyrene_schedule"
        assert invoked.value["result"]["task"]["permission_mode"] == "workspace_only"
        tasks = await service.repository.list("project_a")
        assert [task.prompt for task in tasks] == ["Plugin chain check"]

        occurrences = await runtime.call(
            "schedule.occurrences",
            {
                "start": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                "end": (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat(),
            },
            context,
        )
        assert occurrences.success is True
        events = occurrences.value["events"]
        assert events
        assert {event["task_id"] for event in events} == {tasks[0].id}

    asyncio.run(scenario())


def test_claim_is_exclusive_stable_and_finalize_preserves_pause(tmp_path):
    _runtime_value, service, _context = _runtime(tmp_path)

    async def scenario():
        await service.ensure_ready()
        due = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        task_id = await service.repository.create(
            chat_id=-1,
            prompt="race check",
            schedule_type="interval",
            schedule_value="60",
            next_run=due,
            project_id="project_a",
        )
        left, right = await asyncio.gather(
            service.repository.claim_due(limit=1),
            service.repository.claim_due(limit=1),
        )
        claims = [*left, *right]
        assert len(claims) == 1
        claim = claims[0]

        assert await service.repository.release_claim(claim, reason="test recovery")
        reclaimed = (await service.repository.claim_due(limit=1))[0]
        assert reclaimed.run_id == claim.run_id

        assert await service.repository.update_status(
            task_id, "paused", project_id="project_a"
        )
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert await service.repository.finalize_claim(
            reclaimed,
            run_status="success",
            result="done",
            error=None,
            duration_ms=10,
            next_run=future,
            task_status="active",
        )
        task = await service.repository.get(task_id, "project_a")
        assert task is not None
        assert task.status == "paused"
        assert task.next_run == due
        assert task.last_result == "done"

    asyncio.run(scenario())


def test_hidden_tick_executes_message_and_records_terminal_run(tmp_path):
    runtime, service, context = _runtime(tmp_path)
    delivered = []

    async def deliver(task, text, *, run_id, error=False):
        delivered.append((task.id, text, run_id, error))
        return {"workbench": True}

    service.deliver = deliver

    async def scenario():
        await service.ensure_ready()
        task_id = await service.repository.create(
            chat_id=-1,
            prompt="Exact reminder text",
            schedule_type="once",
            schedule_value="2000-01-01T00:00:00+00:00",
            next_run="2000-01-01T00:00:00+00:00",
            project_id="project_a",
            action_type="message",
        )
        tick = await runtime.call("schedule.tick", {"limit": 10}, context)
        assert tick.success is True
        assert tick.value["claimed"] == 1
        assert delivered[0][1] == "Exact reminder text"

        task = await service.repository.get(task_id, "project_a")
        assert task is not None
        assert task.status == "completed"
        assert task.next_run is None
        runs = await service.repository.list_runs(
            task_id, project_id="project_a", limit=10
        )
        assert runs[0]["status"] == "success"
        assert runs[0]["run_id"] == delivered[0][2]

    asyncio.run(scenario())
