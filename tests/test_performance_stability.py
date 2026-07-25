import asyncio
import json
import sqlite3
import threading


async def test_backup_export_runs_blocking_work_off_event_loop(monkeypatch, tmp_path):
    from cyrene import backup

    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def fake_export(*, include_db, target_path):
        worker_threads.append(threading.get_ident())
        return {"ok": True, "path": str(target_path), "include_db": include_db}

    monkeypatch.setattr(backup, "_export_backup_sync", fake_export)
    result = await backup.export_backup(include_db=False, target_path=tmp_path / "state.zip")

    assert result["ok"] is True
    assert worker_threads and worker_threads[0] != event_loop_thread


async def test_session_shutdown_awaits_owned_task_finalizers():
    from cyrene.agent import state
    from cyrene.agent.session import shutdown_session_tasks
    from cyrene.task_lifecycle import track_task

    ctx = state._ensure_session("lifecycle-test")
    started = asyncio.Event()
    finalized = asyncio.Event()

    async def worker():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalized.set()

    track_task(
        asyncio.create_task(worker()),
        ctx.pending_housekeeping,
        label="lifecycle test worker",
    )
    await started.wait()
    await shutdown_session_tasks()

    assert finalized.is_set()
    assert "lifecycle-test" not in state._sessions


def test_workbench_schema_cache_reinitializes_deleted_database(monkeypatch, tmp_path):
    from cyrene import workbench_store

    db_path = tmp_path / "workbench.db"
    cache_key = str(db_path.resolve())
    workbench_store._SCHEMA_READY.discard(cache_key)
    real_connect = sqlite3.connect
    calls = 0

    def counting_connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(workbench_store.sqlite3, "connect", counting_connect)
    workbench_store.ensure_schema(db_path)
    workbench_store.ensure_schema(db_path)
    assert calls == 1

    db_path.unlink()
    workbench_store.ensure_schema(db_path)
    assert calls == 2
    with real_connect(db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workbench_state'"
        ).fetchone()
    assert row is not None


def test_lightweight_project_lookup_skips_workspace_repairs(monkeypatch, tmp_path):
    from cyrene import workbench_runtime as routes

    store_path = tmp_path / "workbench_projects.json"
    store_path.write_text(
        json.dumps({"projects": [{"id": "project-fast", "dataKey": "fast"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "_CONFIGURED_WORKBENCH_STORE", None)
    monkeypatch.setattr(
        routes,
        "_workbench_ensure_invariants",
        lambda _payload: (_ for _ in ()).throw(AssertionError("heavy repair path ran")),
    )
    monkeypatch.setattr(
        routes,
        "_workbench_workspace_file_snapshot",
        lambda _root: (_ for _ in ()).throw(AssertionError("workspace scan ran")),
    )

    project = routes._workbench_find_project_lightweight("project-fast")

    assert project == {"id": "project-fast", "dataKey": "fast"}


def test_lightweight_project_store_read_skips_workspace_repairs(monkeypatch, tmp_path):
    from cyrene import workbench_runtime as routes

    payload = {
        "projects": [
            {
                "id": "project-fast",
                "dataKey": "fast",
                "sessions": [{"id": "session-fast", "title": "Fast"}],
            }
        ],
        "activeProjectId": "project-fast",
    }
    store_path = tmp_path / "workbench_projects.json"
    store_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "_CONFIGURED_WORKBENCH_STORE", None)
    monkeypatch.setattr(
        routes,
        "_workbench_ensure_invariants",
        lambda _payload: (_ for _ in ()).throw(AssertionError("heavy repair path ran")),
    )

    assert routes._read_workbench_store_lightweight() == payload


def test_web_app_uses_single_lifespan_manager(tmp_path):
    from webui.server import WebBot, create_app

    app = create_app(WebBot(), str(tmp_path / "cyrene.db"))

    assert app.router.on_startup == []
    assert app.router.on_shutdown == []
    assert app.state.goal_loop_manager is not None
