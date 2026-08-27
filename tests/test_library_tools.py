from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.mark.asyncio
async def test_knowledge_store_creates_missing_parent_directory(tmp_path):
    from agent.plugin.plugin_impl.cyrene_knowledge.store import KnowledgeStore

    db_path = tmp_path / "fresh-profile" / "store" / "knowledge.db"

    store = KnowledgeStore(db_path.parent)

    assert store.db_path.is_file()


@pytest.fixture
async def library_plugin(tmp_path):
    from agent.plugin import PluginContext
    from agent.plugin.plugin_impl.cyrene_knowledge.service import create_knowledge_service

    service = create_knowledge_service(
        tmp_path / "knowledge",
        workspace_resolver=lambda workspace: workspace,
        zotero_settings=lambda: {},
    )
    await service.startup()
    context = PluginContext(
        data={"project_id": "project-a"},
        services={"knowledge": service},
    )
    try:
        yield service, context
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_list_library_items_reports_real_project_metadata(library_plugin):
    from agent.plugin.plugin_impl.cyrene_knowledge.list_library_items import handler

    service, context = library_plugin
    item = await service.create_item(
        "project-a",
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

    result = await handler({"query": "retrieval"}, context)

    assert "Project-scoped retrieval systems" in result
    assert "Lin Chen" in result
    assert f"paper_id={item['id']}" in result
    assert "10.1000/project-scope" in result


@pytest.mark.asyncio
async def test_search_library_returns_stable_paper_id(library_plugin):
    from agent.plugin.plugin_impl.cyrene_knowledge.search_library import handler

    service, context = library_plugin
    item = await service.create_item(
        "project-a",
        {
            "title": "Evidence-grounded agent workflows",
            "abstract": "A study of agents that retrieve project evidence before answering.",
            "tags": ["agents", "retrieval"],
            "reading_status": "reading",
        },
    )

    result = await handler({"query": "project evidence", "k": 5}, context)

    assert "Evidence-grounded agent workflows" in result
    assert f"paper_id={item['id']}" in result
    assert "Abstract:" in result


def test_library_tools_are_registered_as_read_only():
    from agent.plugin.plugin_impl.cyrene_knowledge import plugin_pack

    plugins = {plugin.name: plugin for plugin in plugin_pack.plugins}
    assert plugins["ListLibraryItems"].metadata["read_only"] is True
    assert plugins["SearchLibrary"].metadata["read_only"] is True
    assert plugins["UpdateLibraryMetadata"].metadata["read_only"] is False


@pytest.mark.asyncio
async def test_update_library_metadata_fills_only_missing_fields(library_plugin):
    from agent.plugin.plugin_impl.cyrene_knowledge import update_library_metadata as tool

    service, context = library_plugin
    item = await service.create_item(
        "project-a",
        {
            "title": "paper.pdf",
            "venue": "User-maintained venue",
            "item_type": "document",
        },
    )

    result = await tool.handler(
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
        context,
    )

    updated = await service.get_item("project-a", item["id"])
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
async def test_update_library_metadata_can_correct_verified_existing_fields(library_plugin):
    from agent.plugin.plugin_impl.cyrene_knowledge.update_library_metadata import handler

    service, context = library_plugin
    item = await service.create_item(
        "project-a",
        {"title": "Incorrect title", "year": 2020},
    )
    result = await handler(
        {
            "paper_id": item["id"],
            "metadata": {"title": "Correct title", "year": 2024},
            "overwrite": True,
            "sources": ["https://publisher.example/paper"],
        },
        context,
    )

    updated = await service.get_item("project-a", item["id"])
    assert updated["title"] == "Correct title"
    assert updated["year"] == 2024
    assert "Sources: https://publisher.example/paper" in result
