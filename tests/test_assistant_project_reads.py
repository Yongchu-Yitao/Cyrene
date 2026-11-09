"""Assistant task reads aggregate projects without changing normal/write scopes."""
import json
from io import BytesIO
from starlette.datastructures import UploadFile, Headers

import pytest

from cyrene.core.plugin import PluginContext
from cyrene.plugins.project_read_scope import reads_all_projects
from cyrene.plugins.builtin.cyrene_knowledge.service import KnowledgeService, WorkspaceNotFoundError
from cyrene.plugins.builtin.cyrene_memory import structured
from cyrene.plugins.builtin.cyrene_memory.list_memories import handler as list_memories
from cyrene.plugins.builtin.cyrene_memory.search_project_memory import handler as search_memories
from cyrene.workbench.projects.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID


PROJECTS = [{"id": "one", "name": "First"}, {"id": "two", "name": "Second"}]
TASK_DATA = {"project_id": ASSISTANT_SCOPE_ID, "assistant_owner_session_id": ASSISTANT_ID}


@pytest.fixture
def projects(monkeypatch):
    from cyrene.workbench.sessions import context
    monkeypatch.setattr(context, "read_projects", lambda: PROJECTS)


def test_scope_requires_host_owner_and_assistant_scope():
    assert reads_all_projects(TASK_DATA)
    assert not reads_all_projects({"project_id": ASSISTANT_SCOPE_ID})
    assert not reads_all_projects({**TASK_DATA, "project_id": "one"})
    assert not reads_all_projects({**TASK_DATA, "assistant_owner_session_id": "other"})


@pytest.mark.asyncio
async def test_memory_search_inventory_and_pagination(projects, tmp_path, monkeypatch):
    from cyrene.workbench.persistence.store import ensure_schema
    database = tmp_path / "memory.db"
    ensure_schema(database)
    monkeypatch.setattr(structured, "_STORE_DB_PATH", str(database))
    for project in PROJECTS:
        structured._save(project["id"], [
            {"id": project["id"], "content": "shared telescope decision", "category": "fact"},
            {"id": project["id"] + "-old", "content": "shared retired fact", "category": "fact", "stale": True},
        ])
    context = PluginContext(data=TASK_DATA)
    result = json.loads(await search_memories({"query": "telescope"}, context))
    assert {m["project_id"] for m in result["memories"]} == {"one", "two"}
    assert {m["project_name"] for m in result["memories"]} == {"First", "Second"}
    limited = json.loads(await search_memories({"query": "shared", "limit": 1}, context))
    assert len(limited["memories"]) == 1
    retired = json.loads(await search_memories({"query": "retired", "include_stale": True}, context))
    assert len(retired["memories"]) == 2
    listing = json.loads(await list_memories({"scope": "project", "limit": 1, "offset": 1}, context))
    assert listing["total"] == 2
    assert listing["returned"] == 1
    assert listing["memories"][0]["project_id"] in {"one", "two"}
    normal = PluginContext(data={"project_id": "one"})
    result = json.loads(await search_memories({"query": "telescope"}, normal))
    assert [m["id"] for m in result["memories"]] == ["one"]
    assert "project_id" not in result["memories"][0]
    listing = json.loads(await list_memories({"scope": "project"}, normal))
    assert listing["total"] == 1


@pytest.mark.asyncio
async def test_knowledge_reads_sources_limits_and_write_scope(projects, tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path / "knowledge")
    monkeypatch.setattr(service, "_start_embedding_refresh", lambda *_: None)
    async def no_embedding(_):
        return None, "", 0
    monkeypatch.setattr(service, "_query_embedding", no_embedding)
    for project in PROJECTS:
        # Upload exercises both the document inventory and indexed passage search.
        await service.upload(project["id"], [UploadFile(BytesIO(b"telescope evidence"), filename=project["id"] + ".txt", headers=Headers({"content-type": "text/plain"}))])
    context = PluginContext(data=TASK_DATA)
    hits = await service.search_knowledge(context, "telescope", limit=2)
    assert {hit["project_id"] for hit in hits} == {"one", "two"}
    assert len(await service.search_knowledge(context, "telescope", limit=1)) == 1
    documents = await service.list_documents(context)
    assert {item["project_name"] for item in documents} == {"First", "Second"}
    papers = await service.search_library(context, "telescope")
    assert {item["project_id"] for item in papers} == {"one", "two"}
    listing = await service.list_library_items(context, limit=1)
    assert listing["total"] == 2 and len(listing["items"]) == 1
    assert not (await service.list_documents(context, status="pending"))
    normal = PluginContext(data={"project_id": "one"})
    assert {hit["workspace"] for hit in await service.search_knowledge(normal, "telescope")} == {"one"}
    assert len(await service.list_documents(normal)) == 1
    assert len(await service.search_library(normal, "telescope")) == 1
    assert (await service.list_library_items(normal))["total"] == 1
    with pytest.raises(WorkspaceNotFoundError):
        await service.update_library_metadata(context, papers[0]["item"]["id"], {"title": "changed"})
    assert context.data == TASK_DATA
    from cyrene.plugins.builtin.cyrene_knowledge import search_knowledge, search_library, list_knowledge_documents, list_library_items
    tool_context = PluginContext(data=TASK_DATA, services={"knowledge": service})
    for module in [search_knowledge, search_library, list_knowledge_documents, list_library_items]:
        text = await module.handler({"query": "telescope"}, tool_context)
        assert "project_id=one; project_name=First" in text
        assert "project_id=two; project_name=Second" in text
    await service.shutdown()


@pytest.mark.asyncio
async def test_empty_project_catalog(tmp_path, monkeypatch):
    from cyrene.workbench.sessions import context as wb_context
    monkeypatch.setattr(wb_context, "read_projects", lambda: [])
    service = KnowledgeService(tmp_path)
    context = PluginContext(data=TASK_DATA)
    assert await service.search_knowledge(context, "anything") == []
    assert await service.search_library(context, "anything") == []
    assert await service.list_documents(context) == []
    assert await service.list_library_items(context) == {"items": [], "total": 0}
    assert json.loads(await search_memories({"query": "anything"}, context))["memories"] == []
    assert json.loads(await list_memories({"scope": "project"}, context))["total"] == 0
