from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest

from agent.plugin.plugin_impl.cyrene_memory import structured as memory
from agent.plugin.plugin_impl.cyrene_memory.routes_structured import (
    register_workbench_memory_routes,
)


def test_workspace_memory_crud_preserves_history_and_stale_semantics(tmp_path, monkeypatch):
    original_db_path = memory._STORE_DB_PATH
    from cyrene.workbench.store import ensure_schema

    database = tmp_path / "memory.db"
    ensure_schema(database)
    memory.configure_store(str(database))
    try:
        app = FastAPI()
        router = APIRouter()
        register_workbench_memory_routes(router, memory.MemoryApplicationService())
        app.include_router(router)
        client = TestClient(app)

        created = client.post(
            "/api/workbench/memory?workspace=project_demo",
            json={
                "content": "Keep verified fixtures.",
                "category": "habit",
                "source": "manual",
                "confidence": "high",
                "tags": "tests, verified",
            },
        )
        assert created.status_code == 200
        memory_id = created.json()["id"]
        item = created.json()["memories"][0]
        assert item["id"] == memory_id
        assert item["tags"] == ["tests", "verified"]
        assert item["history"][0]["action"] == "created"

        updated = client.patch(
            f"/api/workbench/memory/{memory_id}?workspace=project_demo",
            json={"content": "Keep verified integration fixtures.", "stale": True},
        )
        assert updated.status_code == 200
        item = updated.json()["memories"][0]
        assert item["stale"] is True
        assert [event["action"] for event in item["history"]] == [
            "created",
            "stale",
            "edited",
        ]

        deleted = client.delete(
            f"/api/workbench/memory/{memory_id}?workspace=project_demo"
        )
        assert deleted.status_code == 200
        assert deleted.json()["memories"] == []
    finally:
        memory.configure_store(original_db_path)


def test_workspace_memory_application_maps_invalid_and_missing_mutations():
    class Repository(memory.MemoryRepository):
        def __init__(self):
            self.entries = []

        def load(self, _workspace):
            return self.entries

    service = memory.MemoryApplicationService(repository=Repository())
    with pytest.raises(memory.MemoryApplicationError) as create_error:
        service.create("project-a", memory.MemoryCreateDTO(content="  "))
    exc = create_error.value
    assert (str(exc), exc.status_code, exc.code) == (
        "Memory content is required",
        400,
        "memory_content_required",
    )

    with pytest.raises(memory.MemoryApplicationError) as delete_error:
        service.delete("project-a", "missing")
    exc = delete_error.value
    assert (str(exc), exc.status_code, exc.code) == (
        "Memory not found",
        404,
        "memory_not_found",
    )
