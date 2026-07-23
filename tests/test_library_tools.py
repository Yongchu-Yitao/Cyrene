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
    assert "UpdateLibraryMetadata" in registry_tools.get_tool_names()
    assert (
        registry_tools.get_tool_execution_metadata("UpdateLibraryMetadata")["read_only"]
        is False
    )


@pytest.mark.asyncio
async def test_update_library_metadata_fills_only_missing_fields(library_db):
    from cyrene.knowledge import library
    from cyrene.tool_impl import update_library_metadata as tool

    item = await library.create_item(
        library_db,
        {
            "title": "paper.pdf",
            "venue": "User-maintained venue",
            "item_type": "document",
        },
    )

    result = await tool._tool_update_library_metadata(
        {
            "paper_id": item["id"],
            "metadata": {
                "title": "Reliable Paper Title",
                "authors": ["Ada Lovelace"],
                "venue": "Searched venue",
                "doi": "10.1000/reliable",
                "abstract": "Evidence-based metadata.",
                "year": 2025,
                "item_type": "journalArticle",
            },
            "sources": ["https://doi.org/10.1000/reliable"],
        },
        None,
        -1,
        "ignored.db",
        None,
    )

    updated = await library.get_item(library_db, item["id"])
    assert updated["title"] == "paper.pdf"
    assert updated["venue"] == "User-maintained venue"
    assert updated["doi"] == "10.1000/reliable"
    assert updated["abstract"] == "Evidence-based metadata."
    assert updated["year"] == 2025
    assert updated["item_type"] == "document"
    assert updated["creators"][0]["name"] == "Ada Lovelace"
    assert "Written fields:" in result
    assert "Preserved existing fields: item_type, title, venue." in result
    assert "Sources: https://doi.org/10.1000/reliable" in result


@pytest.mark.asyncio
async def test_update_library_metadata_can_correct_verified_existing_fields(library_db):
    from cyrene.knowledge import library
    from cyrene.tool_impl.update_library_metadata import _tool_update_library_metadata

    item = await library.create_item(
        library_db,
        {"title": "Incorrect title", "year": 2020},
    )
    result = await _tool_update_library_metadata(
        {
            "paper_id": item["id"],
            "metadata": {"title": "Correct title", "year": 2024},
            "overwrite": True,
            "sources": ["https://publisher.example/paper"],
        },
        None,
        -1,
        "ignored.db",
        None,
    )

    updated = await library.get_item(library_db, item["id"])
    assert updated["title"] == "Correct title"
    assert updated["year"] == 2024
    assert "Sources: https://publisher.example/paper" in result
