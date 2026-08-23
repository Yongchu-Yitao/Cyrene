"""Knowledge-database schema initialization and FTS maintenance."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

KB_TABLES_SQL: str = """
CREATE TABLE IF NOT EXISTS kb_documents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    path          TEXT NOT NULL,
    content_hash  TEXT DEFAULT '',
    content_type  TEXT DEFAULT '',
    kind          TEXT DEFAULT 'file',
    size          INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'pending',
    source        TEXT DEFAULT 'upload',
    title         TEXT DEFAULT '',
    summary       TEXT DEFAULT '',
    tags          TEXT DEFAULT '[]',
    char_count    INTEGER DEFAULT 0,
    chunk_count   INTEGER DEFAULT 0,
    entity_id     TEXT,
    error         TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    indexed_at    TEXT,
    metadata      TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_kb_documents_status ON kb_documents(status);
CREATE INDEX IF NOT EXISTS idx_kb_documents_kind   ON kb_documents(kind);
CREATE INDEX IF NOT EXISTS idx_kb_documents_updated_at ON kb_documents(updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_documents_path ON kb_documents(path);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id              TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    content         TEXT NOT NULL,
    char_start      INTEGER DEFAULT 0,
    char_end        INTEGER DEFAULT 0,
    token_count     INTEGER DEFAULT 0,
    embedding       BLOB,
    embedding_dim   INTEGER DEFAULT 0,
    embedding_model TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_document ON kb_chunks(document_id);

CREATE TABLE IF NOT EXISTS kb_relations (
    id          TEXT PRIMARY KEY,
    src_id      TEXT NOT NULL,
    dst_id      TEXT NOT NULL,
    relation    TEXT DEFAULT 'related',
    weight      REAL DEFAULT 1.0,
    source      TEXT DEFAULT 'manual',
    created_at  TEXT NOT NULL,
    UNIQUE(src_id, dst_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_kb_relations_src ON kb_relations(src_id);
CREATE INDEX IF NOT EXISTS idx_kb_relations_dst ON kb_relations(dst_id);

-- Structured literature-library records live beside, but do not replace, the
-- generic knowledge documents.  Because this script is run against the
-- workspace-specific kb_<project>.db, every row below is project isolated by
-- construction and never needs a caller-supplied project discriminator.
CREATE TABLE IF NOT EXISTS library_items (
    id                    TEXT PRIMARY KEY,
    provider              TEXT NOT NULL DEFAULT 'cyrene',
    provider_library_id   TEXT NOT NULL DEFAULT '',
    provider_item_key     TEXT NOT NULL DEFAULT '',
    provider_version      INTEGER NOT NULL DEFAULT 0,
    item_type             TEXT NOT NULL DEFAULT 'document',
    title                 TEXT NOT NULL DEFAULT '',
    abstract              TEXT NOT NULL DEFAULT '',
    doi                   TEXT NOT NULL DEFAULT '',
    isbn                  TEXT NOT NULL DEFAULT '',
    url                   TEXT NOT NULL DEFAULT '',
    venue                 TEXT NOT NULL DEFAULT '',
    publisher             TEXT NOT NULL DEFAULT '',
    volume                TEXT NOT NULL DEFAULT '',
    issue                 TEXT NOT NULL DEFAULT '',
    pages                 TEXT NOT NULL DEFAULT '',
    language              TEXT NOT NULL DEFAULT '',
    year                  INTEGER,
    date_text             TEXT NOT NULL DEFAULT '',
    citekey               TEXT NOT NULL DEFAULT '',
    reading_status        TEXT NOT NULL DEFAULT 'unread',
    last_read_at          TEXT,
    starred               INTEGER NOT NULL DEFAULT 0,
    tags                  TEXT NOT NULL DEFAULT '[]',
    csl_json              TEXT NOT NULL DEFAULT '{}',
    raw_json              TEXT NOT NULL DEFAULT '{}',
    deleted_at            TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    synced_at             TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_library_items_provider_key
    ON library_items(provider, provider_library_id, provider_item_key)
    WHERE provider_item_key <> '';
CREATE INDEX IF NOT EXISTS idx_library_items_updated ON library_items(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_library_items_doi ON library_items(doi);
CREATE INDEX IF NOT EXISTS idx_library_items_status ON library_items(reading_status);

CREATE TABLE IF NOT EXISTS library_creators (
    id            TEXT PRIMARY KEY,
    item_id       TEXT NOT NULL,
    creator_type  TEXT NOT NULL DEFAULT 'author',
    first_name    TEXT NOT NULL DEFAULT '',
    last_name     TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL DEFAULT '',
    ordinal       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_library_creators_item ON library_creators(item_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_library_creators_name ON library_creators(last_name, first_name);

CREATE TABLE IF NOT EXISTS library_collections (
    id                    TEXT PRIMARY KEY,
    provider              TEXT NOT NULL DEFAULT 'cyrene',
    provider_library_id   TEXT NOT NULL DEFAULT '',
    provider_key          TEXT NOT NULL DEFAULT '',
    provider_version      INTEGER NOT NULL DEFAULT 0,
    name                  TEXT NOT NULL,
    parent_id             TEXT,
    sort_order            INTEGER NOT NULL DEFAULT 0,
    raw_json              TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_library_collections_provider_key
    ON library_collections(provider, provider_library_id, provider_key)
    WHERE provider_key <> '';
CREATE INDEX IF NOT EXISTS idx_library_collections_parent ON library_collections(parent_id);

CREATE TABLE IF NOT EXISTS library_collection_items (
    collection_id TEXT NOT NULL,
    item_id       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (collection_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_library_collection_items_item ON library_collection_items(item_id);

CREATE TABLE IF NOT EXISTS library_attachments (
    id                    TEXT PRIMARY KEY,
    item_id               TEXT NOT NULL,
    provider              TEXT NOT NULL DEFAULT 'cyrene',
    provider_library_id   TEXT NOT NULL DEFAULT '',
    provider_key          TEXT NOT NULL DEFAULT '',
    provider_version      INTEGER NOT NULL DEFAULT 0,
    kb_document_id        TEXT,
    title                 TEXT NOT NULL DEFAULT '',
    filename              TEXT NOT NULL DEFAULT '',
    path                  TEXT NOT NULL DEFAULT '',
    content_type          TEXT NOT NULL DEFAULT '',
    link_mode             TEXT NOT NULL DEFAULT '',
    content_hash          TEXT NOT NULL DEFAULT '',
    raw_json              TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_library_attachments_provider_key
    ON library_attachments(provider, provider_library_id, provider_key) WHERE provider_key <> '';
CREATE INDEX IF NOT EXISTS idx_library_attachments_item ON library_attachments(item_id);
CREATE INDEX IF NOT EXISTS idx_library_attachments_document ON library_attachments(kb_document_id);

CREATE TABLE IF NOT EXISTS library_notes (
    id                TEXT PRIMARY KEY,
    item_id           TEXT NOT NULL,
    provider          TEXT NOT NULL DEFAULT 'cyrene',
    provider_library_id TEXT NOT NULL DEFAULT '',
    provider_key      TEXT NOT NULL DEFAULT '',
    provider_version  INTEGER NOT NULL DEFAULT 0,
    title             TEXT NOT NULL DEFAULT '',
    content           TEXT NOT NULL DEFAULT '',
    author            TEXT NOT NULL DEFAULT '',
    raw_json          TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_library_notes_provider_key
    ON library_notes(provider, provider_library_id, provider_key) WHERE provider_key <> '';
CREATE INDEX IF NOT EXISTS idx_library_notes_item ON library_notes(item_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS library_annotations (
    id                TEXT PRIMARY KEY,
    item_id           TEXT NOT NULL,
    attachment_id     TEXT,
    provider          TEXT NOT NULL DEFAULT 'cyrene',
    provider_library_id TEXT NOT NULL DEFAULT '',
    provider_key      TEXT NOT NULL DEFAULT '',
    provider_version  INTEGER NOT NULL DEFAULT 0,
    annotation_type   TEXT NOT NULL DEFAULT 'highlight',
    page_label        TEXT NOT NULL DEFAULT '',
    quote             TEXT NOT NULL DEFAULT '',
    comment           TEXT NOT NULL DEFAULT '',
    color             TEXT NOT NULL DEFAULT '',
    position_json     TEXT NOT NULL DEFAULT '{}',
    raw_json          TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_library_annotations_provider_key
    ON library_annotations(provider, provider_library_id, provider_key) WHERE provider_key <> '';
CREATE INDEX IF NOT EXISTS idx_library_annotations_item ON library_annotations(item_id);

CREATE TABLE IF NOT EXISTS library_relations (
    id          TEXT PRIMARY KEY,
    src_item_id TEXT NOT NULL,
    dst_item_id TEXT NOT NULL,
    relation    TEXT NOT NULL DEFAULT 'related',
    source      TEXT NOT NULL DEFAULT 'manual',
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE(src_item_id, dst_item_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_library_relations_src ON library_relations(src_item_id);
CREATE INDEX IF NOT EXISTS idx_library_relations_dst ON library_relations(dst_item_id);

CREATE TABLE IF NOT EXISTS library_sync_sources (
    id                    TEXT PRIMARY KEY,
    provider              TEXT NOT NULL,
    provider_library_id   TEXT NOT NULL DEFAULT '',
    collection_key        TEXT NOT NULL DEFAULT '',
    name                  TEXT NOT NULL DEFAULT '',
    last_library_version  INTEGER NOT NULL DEFAULT 0,
    last_synced_at        TEXT,
    last_error            TEXT NOT NULL DEFAULT '',
    config_json           TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    UNIQUE(provider, provider_library_id, collection_key)
);

CREATE TABLE IF NOT EXISTS library_sync_tombstones (
    provider              TEXT NOT NULL,
    provider_library_id   TEXT NOT NULL DEFAULT '',
    object_type           TEXT NOT NULL,
    provider_key          TEXT NOT NULL,
    version               INTEGER NOT NULL DEFAULT 0,
    deleted_at            TEXT NOT NULL,
    PRIMARY KEY (provider, provider_library_id, object_type, provider_key)
);
"""

KB_FTS_SQL: str = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_fts USING fts5("
    "content, chunk_id UNINDEXED, document_id UNINDEXED, tokenize='trigram'"
    ");"
)

LIBRARY_FTS_SQL: str = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS library_items_fts USING fts5("
    "title, creators, abstract, doi, venue, tags, item_id UNINDEXED, tokenize='trigram'"
    ");"
)


async def init_knowledge_db(db_path: str) -> None:
    """Create knowledge base tables in a database file.

    Used for per-workspace knowledge base databases (kb_<workspace_id>.db).
    Safe to call multiple times — uses IF NOT EXISTS.
    """
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path)) as db:
        page_count = int((await (await db.execute("PRAGMA page_count")).fetchone())[0])
        if page_count == 0:
            await db.execute("PRAGMA auto_vacuum = INCREMENTAL")
        await db.executescript(KB_TABLES_SQL)
        await db.execute(KB_FTS_SQL)
        await db.execute(LIBRARY_FTS_SQL)
        # Migration: add content_hash column to existing tables
        try:
            await db.execute("ALTER TABLE kb_documents ADD COLUMN content_hash TEXT DEFAULT ''")
        except Exception:
            # Expected when the column already exists on upgraded databases
            logger.debug("KB migration: content_hash column already present", exc_info=True)
        try:
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_documents_content_hash "
                "ON kb_documents(content_hash) WHERE content_hash <> ''"
            )
        except Exception:
            logger.warning("Failed to create KB content_hash dedup index", exc_info=True)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_documents_updated_at "
            "ON kb_documents(updated_at DESC)"
        )
        await db.commit()
    _schedule_knowledge_fts_maintenance(str(path))


_KNOWLEDGE_FTS_MAINTENANCE_TASKS: dict[str, asyncio.Task[None]] = {}


def _schedule_knowledge_fts_maintenance(db_path: str) -> None:
    """Repair pathological FTS tombstone growth outside request latency."""
    path = str(Path(db_path).expanduser().resolve())
    existing = _KNOWLEDGE_FTS_MAINTENANCE_TASKS.get(path)
    if existing is not None and not existing.done():
        return

    async def maintain() -> None:
        await asyncio.sleep(1.0)
        try:
            async with aiosqlite.connect(path, timeout=30) as db:
                await db.execute("PRAGMA busy_timeout = 30000")
                cursor = await db.execute("SELECT COUNT(*) FROM kb_chunks_fts")
                live_rows = int((await cursor.fetchone())[0])
                cursor = await db.execute("SELECT COUNT(*) FROM kb_chunks_fts_data")
                data_blocks = int((await cursor.fetchone())[0])
                if data_blocks <= max(4096, live_rows * 32):
                    return
                logger.info(
                    "Optimizing bloated knowledge FTS index [rows=%s blocks=%s]",
                    live_rows,
                    data_blocks,
                )
                await db.execute(
                    "INSERT INTO kb_chunks_fts(kb_chunks_fts) VALUES('optimize')"
                )
                await db.execute("PRAGMA incremental_vacuum(2048)")
                await db.commit()
        except Exception:
            logger.warning("Knowledge FTS maintenance failed for %s", path, exc_info=True)
        finally:
            _KNOWLEDGE_FTS_MAINTENANCE_TASKS.pop(path, None)

    try:
        _KNOWLEDGE_FTS_MAINTENANCE_TASKS[path] = asyncio.create_task(maintain())
    except RuntimeError:
        return


