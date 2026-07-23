from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture
async def library_db(tmp_path, monkeypatch):
    from cyrene import db
    from cyrene import workbench_context

    db_path = str(tmp_path / "kb_project-a.db")
    await db.init_knowledge_db(db_path)

    async def resolve_current_project(_session_id):
        return db_path

    monkeypatch.setattr(
        workbench_context,
        "ensure_knowledge_db_for_session",
        resolve_current_project,
    )
    return db_path


@pytest.mark.asyncio
async def test_list_library_items_reports_real_project_metadata(library_db):
    from cyrene.knowledge import library
    from cyrene.tool_impl.list_library_items import _tool_list_library_items

    item = await library.create_item(
        library_db,
        {
            "title": "Project-scoped retrieval systems",
            "year": 2025,
            "venue": "Systems Journal",
            "doi": "10.1000/project-scope",
            "citekey": "scope2025",
            "creators": [
                {"first_name": "Lin", "last_name": "Chen", "creator_type": "author"}
            ],
        },
    )

    result = await _tool_list_library_items(
        {"query": "retrieval"}, None, -1, "ignored.db", None
    )

    assert "Project-scoped retrieval systems" in result
    assert "Lin Chen" in result
    assert f"paper_id={item['id']}" in result
    assert "10.1000/project-scope" in result


@pytest.mark.asyncio
async def test_search_library_returns_stable_paper_id(library_db):
    from cyrene.knowledge import library
    from cyrene.tool_impl.search_library import _tool_search_library

    item = await library.create_item(
        library_db,
        {
            "title": "Evidence-grounded agent workflows",
            "abstract": "A study of agents that retrieve project evidence before answering.",
            "tags": ["agents", "retrieval"],
            "reading_status": "reading",
        },
    )

    result = await _tool_search_library(
        {"query": "project evidence", "k": 5}, None, -1, "ignored.db", None
    )

    assert "Evidence-grounded agent workflows" in result
    assert f"paper_id={item['id']}" in result
    assert "Abstract:" in result


def test_library_tools_are_registered_as_read_only():
    from cyrene import registry_tools

    registry_tools._initialize_registry()

    assert "ListLibraryItems" in registry_tools.get_tool_names()
    assert "SearchLibrary" in registry_tools.get_tool_names()
    assert registry_tools.get_tool_execution_metadata("ListLibraryItems")["read_only"] is True
    assert registry_tools.get_tool_execution_metadata("SearchLibrary")["read_only"] is True
