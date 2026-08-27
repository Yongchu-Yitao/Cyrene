from pathlib import Path
import sqlite3
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.mark.asyncio
async def test_knowledge_store_creates_missing_parent_directory(tmp_path):
    from agent.plugin.plugin_impl.cyrene_knowledge.store import KnowledgeStore

    db_path = tmp_path / "fresh-profile" / "store" / "knowledge.db"

    store = KnowledgeStore(db_path.parent)

    assert store.db_path.is_file()


@pytest.mark.asyncio
async def test_knowledge_plugin_migrates_legacy_database_once(tmp_path):
    from agent.plugin.plugin_impl.cyrene_knowledge.service import create_knowledge_service

    legacy_store = tmp_path / "store"
    legacy_store.mkdir()
    source_file = tmp_path / "legacy-note.md"
    source_file.write_text("Legacy knowledge body", encoding="utf-8")
    legacy_db = legacy_store / "kb_default.db"
    with sqlite3.connect(legacy_db) as connection:
        connection.executescript(
            """
            CREATE TABLE kb_documents (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
                content_hash TEXT DEFAULT '', content_type TEXT DEFAULT '',
                kind TEXT DEFAULT 'file', size INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending', source TEXT DEFAULT 'upload',
                title TEXT DEFAULT '', summary TEXT DEFAULT '', tags TEXT DEFAULT '[]',
                char_count INTEGER DEFAULT 0, chunk_count INTEGER DEFAULT 0,
                entity_id TEXT, error TEXT DEFAULT '', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, indexed_at TEXT, metadata TEXT DEFAULT '{}'
            );
            CREATE TABLE kb_chunks (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                content TEXT NOT NULL, char_start INTEGER DEFAULT 0,
                char_end INTEGER DEFAULT 0, token_count INTEGER DEFAULT 0,
                embedding BLOB, embedding_dim INTEGER DEFAULT 0,
                embedding_model TEXT DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE TABLE kb_relations (
                id TEXT PRIMARY KEY, src_id TEXT NOT NULL, dst_id TEXT NOT NULL,
                relation TEXT DEFAULT 'related', weight REAL DEFAULT 1.0,
                source TEXT DEFAULT 'manual', created_at TEXT NOT NULL,
                UNIQUE(src_id, dst_id, relation)
            );
            CREATE TABLE library_items (
                id TEXT PRIMARY KEY, provider TEXT, provider_library_id TEXT,
                provider_item_key TEXT, provider_version INTEGER, item_type TEXT,
                title TEXT, tags TEXT, created_at TEXT, updated_at TEXT,
                last_read_at TEXT, deleted_at TEXT
            );
            CREATE TABLE library_attachments (
                id TEXT PRIMARY KEY, item_id TEXT, kb_document_id TEXT, title TEXT,
                filename TEXT, path TEXT, content_type TEXT, content_hash TEXT,
                provider TEXT, provider_library_id TEXT, provider_key TEXT,
                created_at TEXT, updated_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO kb_documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "doc-old",
                source_file.name,
                str(source_file),
                "",
                "text/markdown",
                "file",
                source_file.stat().st_size,
                "indexed",
                "upload",
                "Migrated note",
                "Old summary",
                '["legacy"]',
                21,
                1,
                None,
                "",
                "2025-01-01T00:00:00+00:00",
                "2025-01-02T00:00:00+00:00",
                "2025-01-02T00:00:00+00:00",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO kb_chunks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "chunk-old",
                "doc-old",
                0,
                "Legacy knowledge body",
                0,
                21,
                3,
                None,
                0,
                "",
                "2025-01-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO kb_documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "doc-paper",
                source_file.name,
                str(source_file),
                "paper-hash",
                "text/markdown",
                "file",
                source_file.stat().st_size,
                "indexed",
                "library",
                "Paper attachment",
                "",
                "[]",
                21,
                1,
                None,
                "",
                "2025-02-01T00:00:00+00:00",
                "2025-02-02T00:00:00+00:00",
                "2025-02-02T00:00:00+00:00",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO kb_chunks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "chunk-paper",
                "doc-paper",
                0,
                "Indexed paper body",
                0,
                18,
                3,
                None,
                0,
                "",
                "2025-02-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO library_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "paper-old",
                "zotero",
                "library-a",
                "ITEM1",
                7,
                "journalArticle",
                "Migrated paper",
                '["research"]',
                "2025-02-01T00:00:00+00:00",
                "2025-02-02T00:00:00+00:00",
                None,
                None,
            ),
        )
        connection.execute(
            "INSERT INTO library_attachments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "attachment-old",
                "paper-old",
                "doc-paper",
                "Paper attachment",
                source_file.name,
                str(source_file),
                "text/markdown",
                "paper-hash",
                "zotero",
                "library-a",
                "ATT1",
                "2025-02-01T00:00:00+00:00",
                "2025-02-02T00:00:00+00:00",
            ),
        )

    project_state = {
        "activeProjectId": "project-a",
        "projects": [{"id": "project-a", "dataKey": "default"}],
    }
    service = create_knowledge_service(
        tmp_path / "data" / "plugin_data" / "cyrene_knowledge",
        workspace_resolver=lambda workspace: workspace,
        zotero_settings=lambda: {},
        legacy_store_directory=legacy_store,
        project_state_provider=lambda: project_state,
    )

    await service.startup()
    first = await service.items("project-a")
    await service.startup()
    second = await service.items("project-a")

    assert first["total"] == second["total"] == 2
    migrated_note = next(item for item in first["items"] if item["title"] == "Migrated note")
    item = await service.get_item("project-a", migrated_note["id"])
    assert item is not None
    assert item["title"] == "Migrated note"
    assert item["tags"] == ["legacy"]
    assert item["indexed_text"] == "Legacy knowledge body"
    assert Path(item["attachments"][0]["path"]).read_text(encoding="utf-8") == "Legacy knowledge body"
    migrated_paper = next(item for item in first["items"] if item["title"] == "Migrated paper")
    paper = await service.get_item("project-a", migrated_paper["id"])
    assert paper is not None
    assert paper["tags"] == ["research"]
    assert paper["indexed_text"] == "Indexed paper body"


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
