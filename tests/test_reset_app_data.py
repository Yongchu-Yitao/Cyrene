import asyncio

import pytest

from cyrene.knowledge import ingest
from webui import routes
from webui import routes_workbench_knowledge


@pytest.mark.asyncio
async def test_clear_knowledge_data_removes_workspace_databases_and_cache(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    store_dir.mkdir()

    knowledge_paths = [
        store_dir / "kb_default.db",
        store_dir / "kb_default.db-wal",
        store_dir / "kb_default.db-shm",
        store_dir / "kb_project_123.db",
        store_dir / "kb_project_123.db-journal",
    ]
    for path in knowledge_paths:
        path.write_bytes(b"knowledge")

    unrelated = store_dir / "cyrene.db"
    unrelated.write_bytes(b"main")

    cancelled = False

    async def fake_cancel_pending_tasks():
        nonlocal cancelled
        cancelled = True

    monkeypatch.setattr(ingest, "cancel_pending_tasks", fake_cancel_pending_tasks)
    routes_workbench_knowledge._kb_initialized.update(str(path) for path in knowledge_paths)

    await routes._clear_knowledge_data(store_dir)

    assert cancelled is True
    assert all(not path.exists() for path in knowledge_paths)
    assert unrelated.exists()
    assert routes_workbench_knowledge._kb_initialized == set()


@pytest.mark.asyncio
async def test_cancel_pending_knowledge_index_task(monkeypatch):
    started = asyncio.Event()

    async def blocked_index(_db_path: str, _doc_id: str):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ingest, "_index_document_inner", blocked_index)
    task = asyncio.create_task(ingest.index_document("unused.db", "doc"))
    await started.wait()

    await ingest.cancel_pending_tasks()

    assert task.cancelled()
    assert not ingest._ACTIVE_INDEX_TASKS
