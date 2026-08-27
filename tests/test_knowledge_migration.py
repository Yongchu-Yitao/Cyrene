"""Ownership and compatibility checks for the Knowledge Plugin store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent.plugin.plugin_impl.cyrene_knowledge.service import create_knowledge_service


def _legacy_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE kb_documents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                content_hash TEXT DEFAULT '',
                content_type TEXT DEFAULT '',
                kind TEXT DEFAULT 'file',
                size INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ready',
                source TEXT DEFAULT 'upload',
                title TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                char_count INTEGER DEFAULT 0,
                chunk_count INTEGER DEFAULT 0,
                entity_id TEXT,
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                indexed_at TEXT,
                metadata TEXT DEFAULT '{}'
            );
            CREATE TABLE kb_chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                content TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO kb_documents(id,name,path,content_hash,content_type,kind,size,title,summary,tags,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "old-document",
                "legacy.txt",
                "",
                "legacy-content-hash",
                "text/plain",
                "file",
                14,
                "Legacy title",
                "Legacy summary",
                '["migration"]',
                "2025-01-01T00:00:00+00:00",
                "2025-01-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO kb_chunks(id,document_id,ordinal,content) VALUES(?,?,?,?)",
            ("old-chunk", "old-document", 0, "preserved legacy evidence"),
        )


@pytest.mark.asyncio
async def test_application_startup_migrates_real_store_once_into_plugin_data(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    plugin_root = data_root / "plugin_data" / "cyrene_knowledge"
    legacy_root = tmp_path / "store"
    legacy_db = legacy_root / "kb_project-data.db"
    _legacy_database(legacy_db)

    service = create_knowledge_service(
        plugin_root,
        legacy_store_directory=legacy_root,
        project_state_provider=lambda: {
            "activeProjectId": "project-one",
            "projects": [{"id": "project-one", "dataKey": "project-data"}],
        },
        initialize_store=False,
    )

    await service.startup()
    await service.startup()

    assert service.store.root == plugin_root.resolve()
    assert service.store.db_path.is_file()
    with sqlite3.connect(service.store.db_path) as connection:
        assert connection.execute(
            "SELECT workspace,title FROM items"
        ).fetchall() == [("project-one", "Legacy title")]
        assert connection.execute(
            "SELECT content FROM chunks"
        ).fetchall() == [("preserved legacy evidence",)]
        assert connection.execute(
            "SELECT source_path,workspace,migration_version FROM legacy_imports"
        ).fetchall() == [(str(legacy_db.resolve()), "project-one", 1)]
