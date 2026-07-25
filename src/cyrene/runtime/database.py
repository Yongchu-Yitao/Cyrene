"""Database operations for scheduled tasks and persisted daily analytics.

Note: Message history is stored in conversations/ folder (not in DB).
The DB is used for structured data that needs querying and stable aggregates.
"""

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    project_id TEXT DEFAULT 'default',
    prompt TEXT NOT NULL,
    schedule_type TEXT NOT NULL,
    schedule_value TEXT NOT NULL,
    next_run TEXT,
    last_run TEXT,
    last_result TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    permission_mode TEXT DEFAULT 'workspace_only'
);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_status ON scheduled_tasks(status);

CREATE TABLE IF NOT EXISTS task_run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    run_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_task_run_logs_task_id ON task_run_logs(task_id);

CREATE TABLE IF NOT EXISTS goal_loop_drafts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    base_plan_revision INTEGER NOT NULL,
    goal TEXT NOT NULL,
    goal_changed INTEGER NOT NULL DEFAULT 0,
    plan_json TEXT NOT NULL,
    acceptance_json TEXT NOT NULL,
    limits_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_loop_drafts_session ON goal_loop_drafts(session_id);
CREATE INDEX IF NOT EXISTS idx_goal_loop_drafts_expires ON goal_loop_drafts(expires_at);

CREATE TABLE IF NOT EXISTS goal_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    phase TEXT NOT NULL DEFAULT 'executing',
    plan_definition_revision INTEGER NOT NULL,
    current_step_id TEXT,
    permission_mode TEXT NOT NULL DEFAULT 'auto',
    reflection_mode TEXT NOT NULL DEFAULT 'proactive',
    max_active_seconds INTEGER NOT NULL,
    max_repair_rounds INTEGER NOT NULL,
    active_seconds REAL NOT NULL DEFAULT 0,
    active_started_at TEXT,
    repair_round INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until TEXT,
    stop_reason TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_runs_status ON goal_runs(status);
CREATE INDEX IF NOT EXISTS idx_goal_runs_lease ON goal_runs(lease_until);

CREATE TABLE IF NOT EXISTS goal_run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    step_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES goal_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_goal_run_events_run ON goal_run_events(run_id);

CREATE TABLE IF NOT EXISTS daily_stats (
    day TEXT PRIMARY KEY,
    llm_requests INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    archive_entries INTEGER NOT NULL DEFAULT 0,
    memory_new INTEGER NOT NULL DEFAULT 0,
    memory_mentions INTEGER NOT NULL DEFAULT 0,
    emotion_sum REAL NOT NULL DEFAULT 0,
    emotion_count INTEGER NOT NULL DEFAULT 0,
    activity_00_04 INTEGER NOT NULL DEFAULT 0,
    activity_04_08 INTEGER NOT NULL DEFAULT 0,
    activity_08_12 INTEGER NOT NULL DEFAULT 0,
    activity_12_16 INTEGER NOT NULL DEFAULT 0,
    activity_16_20 INTEGER NOT NULL DEFAULT 0,
    activity_20_24 INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_model_stats (
    day TEXT NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, model)
);

CREATE TABLE IF NOT EXISTS daily_topic_terms (
    day TEXT NOT NULL,
    term TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, term)
);
CREATE INDEX IF NOT EXISTS idx_daily_topic_terms_day ON daily_topic_terms(day);

CREATE TABLE IF NOT EXISTS daily_tool_stats (
    day TEXT NOT NULL,
    tool TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, tool)
);
CREATE INDEX IF NOT EXISTS idx_daily_tool_stats_day ON daily_tool_stats(day);

CREATE TABLE IF NOT EXISTS analytics_backfills (
    source TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    model TEXT NOT NULL,
    round_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    caller TEXT NOT NULL DEFAULT 'main',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_token_usage_created_at ON token_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_model ON token_usage(model);
CREATE INDEX IF NOT EXISTS idx_token_usage_round_id ON token_usage(round_id);

CREATE TABLE IF NOT EXISTS llm_latency_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    round_id TEXT NOT NULL DEFAULT '',
    caller TEXT NOT NULL DEFAULT '',
    phase TEXT NOT NULL DEFAULT '',
    model_type TEXT NOT NULL DEFAULT 'primary',
    candidate_id TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    endpoint TEXT NOT NULL DEFAULT '',
    candidate_rank INTEGER NOT NULL DEFAULT 0,
    endpoint_rank INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 1,
    outcome TEXT NOT NULL,
    status_code INTEGER NOT NULL DEFAULT 0,
    error_type TEXT NOT NULL DEFAULT '',
    queue_wait_ms REAL NOT NULL DEFAULT 0,
    pre_attempt_wait_ms REAL NOT NULL DEFAULT 0,
    request_ms REAL NOT NULL DEFAULT 0,
    response_headers_ms REAL,
    ttft_ms REAL,
    first_token_after_headers_ms REAL,
    generation_ms REAL,
    retry_backoff_ms REAL NOT NULL DEFAULT 0,
    total_call_ms REAL NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens_per_second REAL,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    client_pool_reused INTEGER NOT NULL DEFAULT 0,
    connection_pool_key TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_llm_latency_created_at ON llm_latency_events(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_latency_call_id ON llm_latency_events(call_id);
CREATE INDEX IF NOT EXISTS idx_llm_latency_endpoint ON llm_latency_events(endpoint);

CREATE TABLE IF NOT EXISTS entities (
    id                  TEXT PRIMARY KEY,
    type                TEXT NOT NULL,
    title               TEXT NOT NULL,
    content             TEXT DEFAULT '',
    status              TEXT DEFAULT 'active',
    tags                TEXT DEFAULT '[]',
    priority            TEXT DEFAULT 'medium',
    effort              TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    last_referenced_at  TEXT NOT NULL,
    due_date            TEXT,
    parent_id           TEXT REFERENCES entities(id),
    linked_ids          TEXT DEFAULT '[]',
    people              TEXT DEFAULT '[]',
    source              TEXT DEFAULT 'extracted',
    source_round_id     TEXT,
    confidence          REAL DEFAULT 1.0,
    metadata            TEXT DEFAULT '{}',
    project_id          TEXT DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_entities_type   ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_status ON entities(status);
CREATE INDEX IF NOT EXISTS idx_entities_due    ON entities(due_date);
-- idx_entities_project_id is created in init_db() AFTER the ALTER migration, so
-- it also lands on pre-existing DBs whose CREATE TABLE above was a no-op.

CREATE TABLE IF NOT EXISTS entity_candidates (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT DEFAULT '',
    confidence      REAL NOT NULL,
    source_round_id TEXT,
    raw_text        TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_type_confidence (
    type         TEXT PRIMARY KEY,
    adjustment   REAL DEFAULT 0.0,
    sample_count INTEGER DEFAULT 0,
    updated_at   TEXT NOT NULL
);

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

CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_fts USING fts5(
    content, chunk_id UNINDEXED, document_id UNINDEXED, tokenize='trigram'
);

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

CREATE TABLE IF NOT EXISTS workbench_state (
    key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_TOPIC_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[a-z][a-z0-9_-]{2,}")
_TOPIC_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "have", "about",
    "there", "would", "could", "should", "into", "your", "their", "them",
    "they", "what", "when", "where", "which", "while", "were", "been",
    "user", "assistant", "reply", "response", "just", "like", "than",
    "then", "also", "some", "more", "very", "much", "really",
    "一个", "这个", "那个", "我们", "你们", "他们", "以及", "因为", "所以", "就是",
}


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        # WAL: readers and the writer no longer block each other, and the
        # rollback-journal SHARED→EXCLUSIVE deadlock (seen as "database is
        # locked" when the goal loop and tool/chat writes overlap) disappears.
        # journal_mode is persisted in the DB header, so this one call applies
        # to every later connection that opens this database.
        await db.execute("PRAGMA journal_mode = WAL")
        await db.executescript(_CREATE_TABLES)
        # Migration: add permission_mode column to existing tables
        try:
            await db.execute("ALTER TABLE scheduled_tasks ADD COLUMN permission_mode TEXT DEFAULT 'workspace_only'")
        except Exception:
            pass  # Column already exists
        try:
            await db.execute("ALTER TABLE scheduled_tasks ADD COLUMN project_id TEXT DEFAULT 'default'")
        except Exception:
            pass  # Column already exists
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project_id ON scheduled_tasks(project_id)"
        )
        # Migration: scope entities to a Workbench project (calendar 日程 view).
        try:
            await db.execute("ALTER TABLE entities ADD COLUMN project_id TEXT DEFAULT 'default'")
        except Exception:
            pass  # Column already exists
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_entities_project_id ON entities(project_id)"
        )
        try:
            await db.execute("ALTER TABLE kb_documents ADD COLUMN content_hash TEXT DEFAULT ''")
        except Exception:
            pass  # Column already exists
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_documents_content_hash "
            "ON kb_documents(content_hash) WHERE content_hash <> ''"
        )
        await db.commit()
    await _maybe_backfill_analytics(db_path)


# Knowledge base tables SQL (used to initialize per-workspace KB databases)
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
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(KB_TABLES_SQL)
        await db.execute(KB_FTS_SQL)
        await db.execute(LIBRARY_FTS_SQL)
        # Migration: add content_hash column to existing tables
        try:
            await db.execute("ALTER TABLE kb_documents ADD COLUMN content_hash TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_documents_content_hash "
                "ON kb_documents(content_hash) WHERE content_hash <> ''"
            )
        except Exception:
            pass
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_documents_updated_at "
            "ON kb_documents(updated_at DESC)"
        )
        await db.commit()


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo or timezone.utc


def _normalize_day(day: str | None = None, timestamp: str | None = None) -> str:
    if day:
        return str(day).strip()[:10]
    if timestamp:
        raw = str(timestamp).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(_local_tzinfo()).strftime("%Y-%m-%d")
        except Exception:
            return raw[:10]
    return datetime.now(_local_tzinfo()).strftime("%Y-%m-%d")


def _activity_column(hour: int) -> str:
    if hour < 4:
        return "activity_00_04"
    if hour < 8:
        return "activity_04_08"
    if hour < 12:
        return "activity_08_12"
    if hour < 16:
        return "activity_12_16"
    if hour < 20:
        return "activity_16_20"
    return "activity_20_24"


def bump_activity_sync(db_path: str, timestamp: str | None = None) -> None:
    """Increment the correct daily activity bucket for the given timestamp.

    Synchronous counterpart used by Workbench's per-session archiving, which
    runs synchronously so callers are not forced to be async.
    """
    ts = str(timestamp or datetime.now(_local_tzinfo()).isoformat())
    day = _normalize_day(timestamp=ts)
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hour = int(dt.astimezone(_local_tzinfo()).strftime("%H"))
    except Exception:
        hour = 0
    activity_col = _activity_column(hour)
    with sqlite3.connect(db_path, timeout=30) as db:
        db.execute("PRAGMA busy_timeout = 30000")
        db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
        db.execute(
            f"UPDATE daily_stats SET {activity_col} = {activity_col} + 1 WHERE day = ?",
            (day,),
        )
        db.commit()


def _extract_topic_terms(text: str, limit: int = 12) -> list[str]:
    source = str(text or "").lower()
    if not source:
        return []
    results: list[str] = []
    seen: set[str] = set()
    for token in _TOPIC_RE.findall(source):
        if token in _TOPIC_STOPWORDS:
            continue
        if token.isascii() and len(token) < 4:
            continue
        if token in seen:
            continue
        seen.add(token)
        results.append(token)
        if len(results) >= limit:
            break
    return results


def extract_topic_terms(text: str, limit: int = 12) -> list[str]:
    """Public deterministic topic extraction for repository consumers."""
    return _extract_topic_terms(text, limit)


def _ensure_day_row_sync(db: sqlite3.Connection, day: str) -> None:
    db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))


def record_memory_touch_sync(db_path: str, *, day: str | None = None, emotional_valence: float = 0, is_new: bool = False) -> None:
    target_day = _normalize_day(day=day)
    with sqlite3.connect(db_path) as db:
        _ensure_day_row_sync(db, target_day)
        db.execute(
            """
            UPDATE daily_stats
            SET memory_mentions = memory_mentions + 1,
                memory_new = memory_new + ?,
                emotion_sum = emotion_sum + ?,
                emotion_count = emotion_count + 1
            WHERE day = ?
            """,
            (1 if is_new else 0, float(emotional_valence or 0), target_day),
        )
        db.commit()


async def record_runtime_usage(db_path: str, timestamp: str, usage: dict | None = None) -> None:
    day = _normalize_day(timestamp=timestamp)
    usage = usage if isinstance(usage, dict) else {}
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
        await db.execute(
            """
            UPDATE daily_stats
            SET llm_requests = llm_requests + 1,
                prompt_tokens = prompt_tokens + ?,
                completion_tokens = completion_tokens + ?,
                total_tokens = total_tokens + ?,
                cache_hit_tokens = cache_hit_tokens + ?,
                cache_miss_tokens = cache_miss_tokens + ?
            WHERE day = ?
            """,
            (
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                int(usage.get("total_tokens") or 0),
                int(usage.get("prompt_cache_hit_tokens") or 0),
                int(usage.get("prompt_cache_miss_tokens") or 0),
                day,
            ),
        )
        await db.commit()


async def record_model_usage(db_path: str, timestamp: str, model: str, usage: dict | None = None) -> None:
    if not model:
        return
    day = _normalize_day(timestamp=timestamp)
    model = model.strip()
    usage = usage if isinstance(usage, dict) else {}
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO daily_model_stats (day, model) VALUES (?, ?)",
            (day, model),
        )
        await db.execute(
            """
            UPDATE daily_model_stats
            SET requests = requests + 1,
                prompt_tokens = prompt_tokens + ?,
                completion_tokens = completion_tokens + ?
            WHERE day = ? AND model = ?
            """,
            (
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                day,
                model,
            ),
        )
        await db.commit()


async def get_model_stats_range(db_path: str, day_from: str, day_to: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT day, model, requests, prompt_tokens, completion_tokens FROM daily_model_stats WHERE day >= ? AND day <= ? ORDER BY day ASC, model ASC",
            (day_from, day_to),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def record_tool_call(db_path: str, timestamp: str, tool_name: str = "") -> None:
    day = _normalize_day(timestamp=timestamp)
    tool = str(tool_name or "").strip()
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
        await db.execute(
            "UPDATE daily_stats SET tool_calls = tool_calls + 1 WHERE day = ?",
            (day,),
        )
        if tool:
            await db.execute(
                """
                INSERT INTO daily_tool_stats (day, tool, count) VALUES (?, ?, 1)
                ON CONFLICT(day, tool) DO UPDATE SET count = count + 1
                """,
                (day, tool),
            )
        await db.commit()


def _canonical_tool_for_stats(tool_name: str) -> str:
    """Return the stable feature key used by profile usage stats."""
    raw = str(tool_name or "").strip()
    if not raw:
        return ""

    compact = re.sub(r"\s+", "", raw).lower()
    localized_aliases = {
        "浏览器": "browser",
        "浏览器操作": "browser",
        "用户浏览器操作": "browser",
        "网络搜索": "web_search",
        "联网搜索": "web_search",
        "网页抓取": "web_fetch",
        "获取网页": "web_fetch",
        "终端": "bash",
        "执行命令": "bash",
    }
    if compact in localized_aliases:
        return localized_aliases[compact]

    snake = re.sub(r"[\s.\-]+", "_", raw)
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", snake)
    snake = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", snake)
    snake = re.sub(r"_+", "_", snake).strip("_").lower()
    if not snake:
        return ""
    if snake == "browser" or snake.startswith("browser_"):
        return "browser"

    aliases = {
        "websearch": "web_search",
        "web_search": "web_search",
        "webfetch": "web_fetch",
        "web_fetch": "web_fetch",
        "fetch_url": "web_fetch",
        "bash": "bash",
        "run_shell": "bash",
        "run_command": "bash",
        "start_shell": "bash",
        "send_shell": "bash",
        "read": "read_file",
        "read_file": "read_file",
        "write": "write_file",
        "write_file": "write_file",
        "edit": "edit_file",
        "edit_file": "edit_file",
        "recallmemory": "recall_memory",
        "recall_memory": "recall_memory",
        "recallconversation": "recall_conversation",
        "recall_conversation": "recall_conversation",
        "listknowledgedocuments": "list_knowledge_documents",
        "list_knowledge_documents": "list_knowledge_documents",
        "searchknowledge": "search_knowledge",
        "search_knowledge": "search_knowledge",
    }
    return aliases.get(snake, snake)


async def get_tool_counts_range(db_path: str, day_from: str, day_to: str, limit: int = 5) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT tool, SUM(count) AS count
            FROM daily_tool_stats
            WHERE day >= ? AND day <= ?
            GROUP BY tool
            """,
            (day_from, day_to),
        )
        rows = await cursor.fetchall()
        merged: dict[str, int] = {}
        for row in rows:
            tool = _canonical_tool_for_stats(str(row["tool"] or ""))
            if not tool:
                continue
            merged[tool] = merged.get(tool, 0) + int(row["count"] or 0)
        top = sorted(merged.items(), key=lambda item: (-item[1], item[0]))[: int(limit)]
        return [{"tool": tool, "count": count} for tool, count in top]


async def record_archive_exchange(
    db_path: str,
    *,
    timestamp: str,
    user_message: str,
    assistant_response: str,
) -> None:
    day = _normalize_day(timestamp=timestamp)
    try:
        dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hour = int(dt.astimezone(_local_tzinfo()).strftime("%H"))
    except Exception:
        hour = 0
    activity_col = _activity_column(hour)
    topic_terms = _extract_topic_terms(" ".join([user_message or "", assistant_response or ""]))
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
        await db.execute(
            f"""
            UPDATE daily_stats
            SET archive_entries = archive_entries + 1,
                {activity_col} = {activity_col} + 1
            WHERE day = ?
            """,
            (day,),
        )
        for term in topic_terms:
            await db.execute(
                """
                INSERT INTO daily_topic_terms (day, term, count)
                VALUES (?, ?, 1)
                ON CONFLICT(day, term) DO UPDATE SET count = count + 1
                """,
                (day, term),
            )
        await db.commit()


async def get_daily_stats_range(db_path: str, day_from: str, day_to: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM daily_stats WHERE day >= ? AND day <= ? ORDER BY day ASC",
            (day_from, day_to),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_topic_counts_range(db_path: str, day_from: str, day_to: str, limit: int = 18) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT term, SUM(count) AS count
            FROM daily_topic_terms
            WHERE day >= ? AND day <= ?
            GROUP BY term
            ORDER BY count DESC, term ASC
            LIMIT ?
            """,
            (day_from, day_to, int(limit)),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def count_stat_days(db_path: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM daily_stats WHERE archive_entries > 0")
        row = await cursor.fetchone()
        return int(row[0] or 0) if row else 0


# ---------------------------------------------------------------------------
# Token usage tracking
# ---------------------------------------------------------------------------

def _estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> float:
    from cyrene.model_runtime.pricing import effective_price, estimate_cost

    pricing = effective_price(model)
    cost = estimate_cost(
        pricing,
        prompt_tokens,
        completion_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
    )
    return round(cost, 6)


async def record_token_usage(
    db_path: str,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    duration_ms: int = 0,
    round_id: str = "",
    session_id: str = "",
    caller: str = "main",
) -> None:
    await record_llm_telemetry_batch(
        db_path,
        token_events=[{
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cache_hit_tokens": cache_hit_tokens,
            "cache_miss_tokens": cache_miss_tokens,
            "duration_ms": duration_ms,
            "round_id": round_id,
            "session_id": session_id,
            "caller": caller,
        }],
    )


async def _ensure_llm_latency_table(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_latency_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL,
            created_at TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '',
            round_id TEXT NOT NULL DEFAULT '', caller TEXT NOT NULL DEFAULT '',
            phase TEXT NOT NULL DEFAULT '', model_type TEXT NOT NULL DEFAULT 'primary',
            candidate_id TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
            endpoint TEXT NOT NULL DEFAULT '', candidate_rank INTEGER NOT NULL DEFAULT 0,
            endpoint_rank INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL DEFAULT 1,
            outcome TEXT NOT NULL, status_code INTEGER NOT NULL DEFAULT 0,
            error_type TEXT NOT NULL DEFAULT '', queue_wait_ms REAL NOT NULL DEFAULT 0,
            pre_attempt_wait_ms REAL NOT NULL DEFAULT 0,
            request_ms REAL NOT NULL DEFAULT 0, response_headers_ms REAL,
            ttft_ms REAL, first_token_after_headers_ms REAL, generation_ms REAL,
            retry_backoff_ms REAL NOT NULL DEFAULT 0, total_call_ms REAL NOT NULL DEFAULT 0,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens_per_second REAL, fallback_used INTEGER NOT NULL DEFAULT 0,
            client_pool_reused INTEGER NOT NULL DEFAULT 0,
            connection_pool_key TEXT NOT NULL DEFAULT ''
        )
        """
    )
    cursor = await db.execute("PRAGMA table_info(llm_latency_events)")
    columns = {str(row[1]) for row in await cursor.fetchall()}
    migrations = {
        "pre_attempt_wait_ms": "REAL NOT NULL DEFAULT 0",
        "response_headers_ms": "REAL",
        "first_token_after_headers_ms": "REAL",
        "client_pool_reused": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in migrations.items():
        if name not in columns:
            await db.execute(
                f"ALTER TABLE llm_latency_events ADD COLUMN {name} {definition}"
            )


def _token_usage_row(event: dict, now: str) -> tuple:
    model = str(event.get("model") or "")
    prompt_tokens = int(event.get("prompt_tokens") or 0)
    completion_tokens = int(event.get("completion_tokens") or 0)
    cache_hit_tokens = int(event.get("cache_hit_tokens") or 0)
    cache_miss_tokens = int(event.get("cache_miss_tokens") or 0)
    cost = _estimate_cost(
        model,
        prompt_tokens,
        completion_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
    )
    return (
        str(event.get("created_at") or now),
        model,
        str(event.get("round_id") or ""),
        str(event.get("session_id") or ""),
        str(event.get("caller") or "main"),
        prompt_tokens,
        completion_tokens,
        int(event.get("total_tokens") or 0),
        cache_hit_tokens,
        cache_miss_tokens,
        int(event.get("duration_ms") or 0),
        cost,
    )


def _llm_latency_row(event: dict, now: str) -> tuple:
    return (
        str(event.get("call_id") or ""),
        str(event.get("created_at") or now),
        str(event.get("session_id") or ""),
        str(event.get("round_id") or ""),
        str(event.get("caller") or ""),
        str(event.get("phase") or ""),
        str(event.get("model_type") or "primary"),
        str(event.get("candidate_id") or ""),
        str(event.get("model") or ""),
        str(event.get("endpoint") or ""),
        int(event.get("candidate_rank") or 0),
        int(event.get("endpoint_rank") or 0),
        int(event.get("attempt") or 1),
        str(event.get("outcome") or "unknown"),
        int(event.get("status_code") or 0),
        str(event.get("error_type") or ""),
        float(event.get("queue_wait_ms") or 0),
        float(event.get("pre_attempt_wait_ms") or event.get("queue_wait_ms") or 0),
        float(event.get("request_ms") or 0),
        event.get("response_headers_ms"),
        event.get("ttft_ms"),
        event.get("first_token_after_headers_ms"),
        event.get("generation_ms"),
        float(event.get("retry_backoff_ms") or 0),
        float(event.get("total_call_ms") or 0),
        int(event.get("prompt_tokens") or 0),
        int(event.get("completion_tokens") or 0),
        event.get("output_tokens_per_second"),
        1 if event.get("fallback_used") else 0,
        1 if event.get("client_pool_reused") else 0,
        str(event.get("connection_pool_key") or ""),
    )


async def record_llm_telemetry_batch(
    db_path: str,
    *,
    token_events: list[dict] | tuple[dict, ...] = (),
    latency_events: list[dict] | tuple[dict, ...] = (),
) -> None:
    """Persist usage and latency events with one connection and one commit."""
    if not token_events and not latency_events:
        return
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        if latency_events:
            await _ensure_llm_latency_table(db)
        if token_events:
            await db.executemany(
                """INSERT INTO token_usage
                   (created_at, model, round_id, session_id, caller,
                    prompt_tokens, completion_tokens, total_tokens,
                    cache_hit_tokens, cache_miss_tokens, duration_ms, estimated_cost)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [_token_usage_row(event, now) for event in token_events],
            )
        if latency_events:
            await db.executemany(
                """
                INSERT INTO llm_latency_events
                (call_id, created_at, session_id, round_id, caller, phase, model_type,
                 candidate_id, model, endpoint, candidate_rank, endpoint_rank, attempt,
                 outcome, status_code, error_type, queue_wait_ms, pre_attempt_wait_ms,
                 request_ms, response_headers_ms, ttft_ms, first_token_after_headers_ms,
                 generation_ms, retry_backoff_ms, total_call_ms, prompt_tokens,
                 completion_tokens, output_tokens_per_second, fallback_used,
                 client_pool_reused, connection_pool_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_llm_latency_row(event, now) for event in latency_events],
            )
        await db.commit()


async def record_llm_latency(
    db_path: str,
    **event,
) -> None:
    """Persist one endpoint attempt with optimization-oriented latency spans."""
    await record_llm_telemetry_batch(db_path, latency_events=[event])


async def get_token_usage_stats(
    db_path: str,
    *,
    days: int = 7,
    model: str = "",
) -> dict:
    """Return aggregated token usage stats.

    Returns::
        {"total": {"requests": N, "prompt_tokens": N, ...},
         "by_model": [{"model": "...", "requests": N, ...}],
         "by_day": [{"day": "...", "requests": N, ...}],
         "total_cost": N}
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Totals
        cursor = await db.execute(
            """SELECT COUNT(*) AS requests,
                      COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                      COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens,
                      COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,
                      COALESCE(SUM(estimated_cost), 0) AS total_cost
               FROM token_usage WHERE created_at >= ?""",
            (since,),
        )
        total_row = await cursor.fetchone()

        # By model
        model_filter = " AND model = ?" if model else ""
        model_params = (since, model) if model else (since,)
        cursor = await db.execute(
            f"""SELECT model,
                       COUNT(*) AS requests,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                       COALESCE(SUM(estimated_cost), 0) AS cost
                FROM token_usage WHERE created_at >= ?{model_filter}
                GROUP BY model ORDER BY cost DESC""",
            model_params,
        )
        by_model = [dict(r) for r in await cursor.fetchall()]

        # By day
        cursor = await db.execute(
            f"""SELECT DATE(created_at) AS day,
                       COUNT(*) AS requests,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(estimated_cost), 0) AS cost
                FROM token_usage WHERE created_at >= ?{model_filter}
                GROUP BY day ORDER BY day ASC""",
            model_params,
        )
        by_day = [dict(r) for r in await cursor.fetchall()]

    total = dict(total_row) if total_row else {}
    return {
        "total": {
            "requests": total.get("requests", 0),
            "prompt_tokens": total.get("prompt_tokens", 0),
            "completion_tokens": total.get("completion_tokens", 0),
            "total_tokens": total.get("total_tokens", 0),
            "cache_hit_tokens": total.get("cache_hit_tokens", 0),
            "total_cost": round(float(total.get("total_cost", 0)), 6),
        },
        "by_model": by_model,
        "by_day": by_day,
    }


async def _backfill_runtime_logs(db_path: str) -> None:
    from cyrene.runtime.paths import DATA_DIR

    if not DATA_DIR.exists():
        return
    async with aiosqlite.connect(db_path) as db:
        for log_path in sorted(DATA_DIR.glob("debug_*.jsonl")):
            try:
                for line in log_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    timestamp = str(entry.get("timestamp") or "").strip()
                    if not timestamp:
                        continue
                    day = _normalize_day(timestamp=timestamp)
                    await db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
                    if entry.get("type") == "llm_call":
                        usage = entry.get("usage")
                        if not isinstance(usage, dict):
                            response = entry.get("response")
                            usage = response.get("usage") if isinstance(response, dict) else {}
                        usage = usage if isinstance(usage, dict) else {}
                        await db.execute(
                            """
                            UPDATE daily_stats
                            SET llm_requests = llm_requests + 1,
                                prompt_tokens = prompt_tokens + ?,
                                completion_tokens = completion_tokens + ?,
                                total_tokens = total_tokens + ?,
                                cache_hit_tokens = cache_hit_tokens + ?,
                                cache_miss_tokens = cache_miss_tokens + ?
                            WHERE day = ?
                            """,
                            (
                                int(usage.get("prompt_tokens") or 0),
                                int(usage.get("completion_tokens") or 0),
                                int(usage.get("total_tokens") or 0),
                                int(usage.get("prompt_cache_hit_tokens") or 0),
                                int(usage.get("prompt_cache_miss_tokens") or 0),
                                day,
                            ),
                        )
                    elif entry.get("type") == "tool_call":
                        await db.execute(
                            "UPDATE daily_stats SET tool_calls = tool_calls + 1 WHERE day = ?",
                            (day,),
                        )
            except Exception:
                continue
        await db.execute(
            "INSERT OR REPLACE INTO analytics_backfills (source, completed_at) VALUES (?, ?)",
            ("runtime_logs_v1", datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def _backfill_conversation_archives(db_path: str) -> None:
    from cyrene.runtime.memory.archive_format import parse_archive_sections
    from cyrene.runtime.paths import WORKSPACE_DIR

    conversations_dir = WORKSPACE_DIR / "conversations"
    if not conversations_dir.exists():
        return
    async with aiosqlite.connect(db_path) as db:
        for filepath in sorted(conversations_dir.glob("*.md")):
            date_str = filepath.stem
            try:
                sections = parse_archive_sections(
                    filepath.read_text(encoding="utf-8"),
                    date_str,
                )
            except Exception:
                continue
            for section in sections:
                day = str(section.get("date") or date_str).strip()[:10]
                await db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
                stamp = str(section.get("timestamp") or "").strip()
                try:
                    hour = int(stamp[:2])
                except Exception:
                    hour = 0
                activity_col = _activity_column(hour)
                await db.execute(
                    f"""
                    UPDATE daily_stats
                    SET archive_entries = archive_entries + 1,
                        {activity_col} = {activity_col} + 1
                    WHERE day = ?
                    """,
                    (day,),
                )
                topic_terms = _extract_topic_terms(" ".join([
                    str(section.get("user_body") or ""),
                    str(section.get("assistant_body") or ""),
                ]))
                for term in topic_terms:
                    await db.execute(
                        """
                        INSERT INTO daily_topic_terms (day, term, count)
                        VALUES (?, ?, 1)
                        ON CONFLICT(day, term) DO UPDATE SET count = count + 1
                        """,
                        (day, term),
                    )
        await db.execute(
            "INSERT OR REPLACE INTO analytics_backfills (source, completed_at) VALUES (?, ?)",
            ("conversation_archives_v1", datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def _maybe_backfill_analytics(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT source FROM analytics_backfills")
        rows = await cursor.fetchall()
        completed = {str(row["source"]) for row in rows}
    if "runtime_logs_v1" not in completed:
        await _backfill_runtime_logs(db_path)
    if "conversation_archives_v1" not in completed:
        await _backfill_conversation_archives(db_path)


# --- Task CRUD ---

async def create_task(db_path: str, chat_id: int, prompt: str, schedule_type: str, schedule_value: str, next_run: str, permission_mode: str = "workspace_only", project_id: str = "default") -> str:
    task_id = uuid.uuid4().hex[:8]
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO scheduled_tasks (id, chat_id, project_id, prompt, schedule_type, schedule_value, next_run, created_at, permission_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, chat_id, project_id or "default", prompt, schedule_type, schedule_value, next_run, datetime.now(timezone.utc).isoformat(), permission_mode),
        )
        await db.commit()
    return task_id


async def get_all_tasks(db_path: str, project_id: str | None = None) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if project_id is None:
            cursor = await db.execute("SELECT * FROM scheduled_tasks")
        else:
            cursor = await db.execute(
                "SELECT * FROM scheduled_tasks WHERE COALESCE(project_id, 'default') = ?",
                (project_id or "default",),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_due_tasks(db_path: str) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM scheduled_tasks WHERE status = 'active' AND next_run <= ?",
            (now,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def update_task_status(db_path: str, task_id: str, status: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "UPDATE scheduled_tasks SET status = ? WHERE id = ?",
            (status, task_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_task(db_path: str, task_id: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        await db.commit()
        return cursor.rowcount > 0


async def update_task_after_run(db_path: str, task_id: str, last_result: str, next_run: str | None, status: str = "active") -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE scheduled_tasks SET last_run = ?, last_result = ?, next_run = ?, status = ? WHERE id = ?",
            (now, last_result, next_run, status, task_id),
        )
        await db.commit()


async def log_task_run(db_path: str, task_id: str, duration_ms: int, status: str, result: str | None = None, error: str | None = None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO task_run_logs (task_id, run_at, duration_ms, status, result, error) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, datetime.now(timezone.utc).isoformat(), duration_ms, status, result, error),
        )
        await db.commit()


async def get_task_time_totals(db_path: str) -> dict:
    """Aggregate agent work time across scheduled-task runs and goal-loop runs.

    ``task_run_logs.duration_ms`` is already milliseconds; ``goal_runs.active_seconds``
    is seconds and gets scaled up. Returns total/longest in ms plus a run count.
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(duration_ms), 0), COALESCE(MAX(duration_ms), 0), COUNT(*) FROM task_run_logs",
        )
        task_total, task_longest, task_runs = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT COALESCE(SUM(active_seconds), 0), COALESCE(MAX(active_seconds), 0), COUNT(*) FROM goal_runs",
        )
        goal_total_s, goal_longest_s, goal_runs = await cursor.fetchone()
    goal_total_ms = int(round(float(goal_total_s or 0) * 1000))
    goal_longest_ms = int(round(float(goal_longest_s or 0) * 1000))
    return {
        "total_ms": int(task_total or 0) + goal_total_ms,
        "longest_ms": max(int(task_longest or 0), goal_longest_ms),
        "runs": int(task_runs or 0) + int(goal_runs or 0),
    }
