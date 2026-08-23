from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_workbench_project_modules_restore_project_scoped_cache_before_refresh():
    memory = (ROOT / "src/webui/frontend/workbench-memory.jsx").read_text(
        encoding="utf-8"
    )
    schedule = (ROOT / "src/webui/frontend/workbench-schedule.jsx").read_text(
        encoding="utf-8"
    )
    library = (ROOT / "src/webui/frontend/workbench-library.jsx").read_text(
        encoding="utf-8"
    )

    assert "memoryPageCache.payloads[workspace]" in memory
    assert "setPayload(cachedPayload ? cachedPayload.value : null)" in memory
    assert "if (!active) return;" in memory

    assert "schedulePageCache.ranges[requestKey]" in schedule
    assert "setRawEvents(cached.value)" in schedule
    assert "if (!active) return;" in schedule

    assert "function WorkbenchLibraryPage(props)" in library
    assert "props.active !== false" in library
    assert not (ROOT / "src/webui/frontend/workbench-knowledge.jsx").exists()

    for source in (memory, schedule):
        assert "CACHE_TTL_MS" not in source
        assert 'window.addEventListener("focus", refreshSoon)' in source
        assert "workbenchServices.events().subscribe(onRuntimeEvent)" in source


def test_workbench_module_routes_use_lightweight_canonical_project_lookup(monkeypatch):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import context as workbench_context
    from cyrene.knowledge import workspace as knowledge_workspace
    from cyrene.workbench.schedule_repository import WorkspaceProjectResolver

    project = {"id": "project_fast", "dataKey": "schedule-fast"}
    monkeypatch.setattr(
        routes,
        "_workbench_find_project_lightweight",
        lambda project_id: project if project_id == project["id"] else None,
    )
    monkeypatch.setattr(workbench_context, "read_projects", lambda: [project])

    def fail_full_read():
        raise AssertionError("canonical project ids must not trigger the full project-store scan")

    monkeypatch.setattr(routes, "_read_workbench_store", fail_full_read)

    assert knowledge_workspace.resolve_workspace_id(project["id"]) == project["id"]
    resolver = WorkspaceProjectResolver(
        find_project_lightweight=routes._workbench_find_project_lightweight,
        read_projects=workbench_context.read_projects,
    )
    assert resolver.resolve(project["id"]) == project["dataKey"]
