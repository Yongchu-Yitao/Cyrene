from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_workbench_project_modules_restore_project_scoped_cache_before_refresh():
    memory = (ROOT / "src/webui/frontend/workbench-memory.jsx").read_text(
        encoding="utf-8"
    )
    schedule = (ROOT / "src/webui/frontend/workbench-schedule.jsx").read_text(
        encoding="utf-8"
    )
    knowledge = (ROOT / "src/webui/frontend/workbench-knowledge.jsx").read_text(
        encoding="utf-8"
    )

    assert "memoryPageCache.payloads[workspace]" in memory
    assert "setPayload(cachedPayload ? cachedPayload.value : null)" in memory
    assert "if (!active) return;" in memory

    assert "schedulePageCache.ranges[requestKey]" in schedule
    assert "setRawEvents(cached.value)" in schedule
    assert "if (!active) return;" in schedule

    assert "knowledgePageCache.lists[workspace]" in knowledge
    assert "setDocuments(cached ? cached.documents : [])" in knowledge
    assert "if (!active) return;" in knowledge

    for source in (memory, schedule, knowledge):
        assert "CACHE_TTL_MS" not in source
        assert 'window.addEventListener("focus", refreshSoon)' in source
        assert 'window.CyreneUI.require("events").subscribe(onRuntimeEvent)' in source


def test_workbench_module_routes_use_lightweight_canonical_project_lookup(monkeypatch):
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import knowledge as knowledge
    from route.workbench import schedule as schedule

    project = {"id": "project_fast", "dataKey": "schedule-fast"}
    monkeypatch.setattr(
        routes,
        "_workbench_find_project_lightweight",
        lambda project_id: project if project_id == project["id"] else None,
    )

    def fail_full_read():
        raise AssertionError("canonical project ids must not trigger the full project-store scan")

    monkeypatch.setattr(routes, "_read_workbench_store", fail_full_read)

    assert knowledge._resolve_workspace_id(project["id"]) == project["id"]
    assert schedule._resolve_workspace_id(project["id"]) == project["dataKey"]
