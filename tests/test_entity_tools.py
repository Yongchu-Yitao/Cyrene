import aiosqlite
import pytest


async def _invoke(runtime, context, name, arguments):
    result = await runtime.call(
        "toolbox",
        {"operation": "invoke", "name": name, "arguments": arguments},
        context,
    )
    assert result.success is True, result.error
    assert result.value["name"] == name
    return result.value["result"]


@pytest.mark.asyncio
async def test_seeded_entity_pack_owns_session_and_application_backend(tmp_path):
    import inspect
    from pathlib import Path

    from fastapi import APIRouter, FastAPI

    from cyrene.plugins import PluginApplicationContext
    from cyrene.core.plugin import (
        PluginContext,
        PluginRegistry,
        PluginRuntime,
        PluginSetupContext,
    )
    from cyrene.plugins.native_tools import seed_builtin_plugin_directory

    db_path = str(tmp_path / "entities.db")
    plugin_directory = tmp_path / "plugin_impl"
    seed_builtin_plugin_directory(plugin_directory)
    registry = PluginRegistry()
    assert registry.load_directory(plugin_directory) == ()
    pack = next(item for item in registry.list_packs() if item.id == "cyrene_entity")

    session_services = {}
    assert pack.setup is not None
    pack.setup(
        PluginSetupContext(
            data_directory=tmp_path / "agent-data",
            plugin_directory=plugin_directory,
            workspace=tmp_path / "workspace",
            tree=None,
            tree_id="session-1",
            root_id="root",
            hooks=None,
            data={
                "db_path": db_path,
                "chat_id": 9,
                "session_id": "session-1",
            },
            services=session_services,
        )
    )
    session_service = session_services["entities"]
    session_source = Path(inspect.getsourcefile(type(session_service)) or "").resolve()
    assert session_source.is_relative_to(plugin_directory.resolve())

    runtime = PluginRuntime(registry)
    tracked = await _invoke(
        runtime,
        PluginContext(
            data={"project_id": "project-a"},
            services=session_services,
        ),
        "entity.track",
        {"type": "fact", "title": "用户目录后端"},
    )
    assert tracked["entity"]["project_id"] == "project-a"
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('entities', 'entity_candidates', 'entity_type_confidence')"
        )
        assert {row[0] for row in await cursor.fetchall()} == {
            "entities",
            "entity_candidates",
            "entity_type_confidence",
        }

    application_services = {}
    router = APIRouter()
    pack = next(item for item in registry.list_packs() if item.id == "cyrene_entity")
    assert pack.application_setup is not None
    pack.application_setup(
        PluginApplicationContext(
            app=FastAPI(),
            router=router,
            bot=None,
            db_path=db_path,
            data_directory=tmp_path / "app-data",
            plugin_directory=plugin_directory,
            services=application_services,
            frontend_modules=[],
            search_providers={},
            startup_handlers=[],
            shutdown_handlers=[],
        )
    )
    application_service = application_services["entities"]
    application_source = Path(
        inspect.getsourcefile(type(application_service)) or ""
    ).resolve()
    assert application_source.is_relative_to(plugin_directory.resolve())
    assert "/api/entities" in {route.path for route in router.routes}


@pytest.mark.asyncio
async def test_entity_pack_mounts_attention_context_only_for_proactive_runs(tmp_path):
    from cyrene.core.context import ContextStoreRouter
    from cyrene.core.plugin import PluginSetupContext
    from cyrene.plugins.builtin.cyrene_entity import setup

    class Entities:
        async def query(self, **_filters):
            return [{"title": "Publish release"}]

        async def list(self, **_filters):
            return [{
                "title": "Choose rollout strategy",
                "type": "decision",
                "last_referenced_at": "2000-01-01T00:00:00+00:00",
                "metadata": {},
            }]

    store = ContextStoreRouter(tmp_path / "context")
    tree = store.create_tree(tree_id="proactive", root_id="root")
    hooks = store.hooks_for(tree.id)
    setup(PluginSetupContext(
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugins",
        workspace=tmp_path,
        tree=store,
        tree_id=tree.id,
        root_id=tree.root_id,
        hooks=hooks,
        data={},
        services={"entities": Entities()},
    ))

    assert await hooks.turn_start({"metadata": {}}) == ""
    mounted = await hooks.turn_start({"metadata": {"proactive": True}})
    assert "Publish release" in mounted
    assert "Choose rollout strategy" in mounted
    store.close()


@pytest.mark.asyncio
async def test_entity_pack_follows_toolbox_chain_and_shares_service_data(tmp_path):
    from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime
    from cyrene.plugins.builtin.cyrene_entity import plugin_pack
    from cyrene.runtime.database import init_db
    from cyrene.plugins.builtin.cyrene_entity.service import EntityService

    db_path = str(tmp_path / "entities.db")
    await init_db(db_path)
    service = EntityService(db_path, reminder_chat_id=7, origin_session_id="session-1")
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    runtime = PluginRuntime(registry)
    context = PluginContext(
        data={"project_id": "project-a", "run_id": "round-1"},
        services={"entities": service},
    )

    listing = await runtime.call("toolbox", {"operation": "list"}, context)
    assert listing.success is True
    assert "cyrene_entity" in listing.value["packs"]

    described = await runtime.call(
        "toolbox",
        {"operation": "describe", "name": "cyrene_entity"},
        context,
    )
    assert [tool["name"] for tool in described.value["plugins"]] == [
        "entity.track",
        "entity.update",
        "entity.list",
        "entity.query",
        "entity.delete",
    ]
    assert described.success is True
    assert all(item["pack"] == "cyrene_entity" for item in described.value["plugins"])
    track_schema = described.value["plugins"][0]["input_schema"]
    assert track_schema["required"] == ["type", "title"]
    assert track_schema["additionalProperties"] is False

    duplicate_a = await service.create(
        type="fact",
        title="用户本人照片识别",
        content="第一条内容",
        project_id="project-a",
    )
    duplicate_b = await service.create(
        type="fact",
        title="用户本人照片识别",
        content="第二条内容",
        project_id="project-a",
    )
    other_project = await service.create(
        type="fact",
        title="用户本人照片识别",
        content="另一个项目的内容",
        project_id="project-b",
    )

    tracked = await _invoke(
        runtime,
        context,
        "entity.track",
        {"type": "fact", "title": "用户形象识别", "content": "完整 ID 返回"},
    )
    assert tracked["ok"] is True
    assert len(tracked["entity"]["id"]) == 36
    assert tracked["entity"]["project_id"] == "project-a"

    listed = await _invoke(runtime, context, "entity.list", {})
    listed_ids = {entity["id"] for entity in listed["entities"]}
    assert {duplicate_a["id"], duplicate_b["id"], tracked["entity"]["id"]} <= listed_ids
    assert other_project["id"] not in listed_ids

    queried = await _invoke(
        runtime,
        context,
        "entity.query",
        {"q": "用户本人照片识别"},
    )
    assert {entity["content"] for entity in queried["entities"]} == {
        "第一条内容",
        "第二条内容",
    }

    ambiguous = await _invoke(
        runtime,
        context,
        "entity.delete",
        {"title": "用户本人照片识别", "permanent": True},
    )
    assert ambiguous["ok"] is False
    assert ambiguous["error"] == "ambiguous"
    assert {entity["id"] for entity in ambiguous["matches"]} == {
        duplicate_a["id"],
        duplicate_b["id"],
    }

    updated = await _invoke(
        runtime,
        context,
        "entity.update",
        {"id": tracked["entity"]["id"][:8], "field": "priority", "value": "high"},
    )
    assert updated["ok"] is True
    assert updated["entity"]["priority"] == "high"

    deleted = await _invoke(
        runtime,
        context,
        "entity.delete",
        {"id": duplicate_a["id"][:8], "permanent": True},
    )
    assert deleted["ok"] is True
    assert await service.get(duplicate_a["id"]) is None


@pytest.mark.asyncio
async def test_entity_service_coordinates_reminders_and_candidate_scope(tmp_path):
    from cyrene.runtime.database import init_db
    from cyrene.plugins.builtin.cyrene_entity.service import EntityService
    from cyrene.plugins.builtin.cyrene_schedule.service import ScheduleRuntimeService

    db_path = str(tmp_path / "entities.db")
    await init_db(db_path)
    schedules = ScheduleRuntimeService(db_path)
    await schedules.ensure_ready()
    service = EntityService(
        db_path,
        reminder_chat_id=42,
        origin_session_id="session-2",
        reminders=schedules,
    )

    entity = await service.create(
        type="task",
        title="发布新版本",
        source="explicit",
        due_date="2030-01-02T03:04:05+00:00",
        project_id="project-scope",
    )
    task_id = entity["metadata"]["reminder_task_id"]
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        )
        reminder = await cursor.fetchone()
    assert reminder is not None
    assert reminder["chat_id"] == 42
    assert reminder["project_id"] == "project-scope"
    assert reminder["origin_session_id"] == "session-2"

    updated = await service.update(entity["id"], due_date="2030-02-03T04:05:06+00:00")
    assert updated is not None
    reminder = await schedules.reminder(task_id)
    assert reminder is not None
    assert reminder.next_run == "2030-02-03T04:05:06+00:00"

    assert await service.delete(entity["id"]) is True
    reminder = await schedules.reminder(task_id)
    assert reminder is not None
    assert reminder.status == "cancelled"

    candidate_id = await service.add_candidate(
        type="project",
        title="候选项目",
        content="只属于当前项目",
        confidence=0.9,
        project_id="project-scope",
    )
    assert await service.list_candidates(project_id="other-project") == []
    candidates = await service.list_candidates(project_id="project-scope")
    assert [candidate["id"] for candidate in candidates] == [candidate_id]

    promoted = await service.process_candidates()
    assert len(promoted) == 1
    assert promoted[0]["project_id"] == "project-scope"

    class FailingScheduler:
        async def create_reminder(self, **_values):
            raise RuntimeError("scheduler unavailable")

    failing_service = EntityService(db_path, reminders=FailingScheduler())
    with pytest.raises(RuntimeError, match="scheduler unavailable"):
        await failing_service.create(
            type="task",
            title="不能留下半条记录",
            source="explicit",
            due_date="2031-01-01T00:00:00+00:00",
            project_id="project-scope",
        )
    assert await failing_service.query("不能留下半条记录") == []


@pytest.mark.asyncio
async def test_entity_reminder_rejects_when_schedule_plugin_is_unavailable(
    tmp_path,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_entity.service import EntityService

    monkeypatch.setattr("cyrene.core.plugin.application_plugin_service", lambda _name: None)
    service = EntityService(str(tmp_path / "entities.db"))
    await service.startup()

    with pytest.raises(RuntimeError, match="unavailable or disabled"):
        await service.create(
            type="task",
            title="没有 Schedule 插件的提醒",
            source="explicit",
            due_date="2031-01-01T00:00:00+00:00",
        )
    assert await service.query("没有 Schedule 插件的提醒") == []
