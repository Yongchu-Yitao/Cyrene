"""Editable persistence for Cyrene's durable entity Plugin.

Supports tracking and managing various entity types:
- task, project, decision, knowledge, relationship, event, resource, idea, problem, habit
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from cyrene.platform.sqlite_json import (
    deserialize_dict as _deserialize_dict,
    deserialize_list as _deserialize_list,
    serialize_dict as _serialize_dict,
    serialize_list as _serialize_list,
)

_UPDATABLE_FIELDS = frozenset(
    {
        "status",
        "priority",
        "content",
        "title",
        "effort",
        "due_date",
        "parent_id",
        "tags",
        "linked_ids",
        "people",
        "metadata",
    }
)

_SCHEMA_SQL = """
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
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_status ON entities(status);
CREATE INDEX IF NOT EXISTS idx_entities_due ON entities(due_date);
CREATE INDEX IF NOT EXISTS idx_entities_project_id ON entities(project_id);

CREATE TABLE IF NOT EXISTS entity_candidates (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT DEFAULT '',
    confidence      REAL NOT NULL,
    source_round_id TEXT,
    project_id      TEXT DEFAULT 'default',
    raw_text        TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_candidates_project_id
ON entity_candidates(project_id);

CREATE TABLE IF NOT EXISTS entity_type_confidence (
    type         TEXT PRIMARY KEY,
    adjustment   REAL DEFAULT 0.0,
    sample_count INTEGER DEFAULT 0,
    updated_at   TEXT NOT NULL
);
"""


async def _initialize_store(db_path: str) -> None:
    """Create every table and index owned by this Plugin pack."""

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(_SCHEMA_SQL)
        await db.commit()


def _now() -> str:
    """Return current time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


def _row_to_entity(row: aiosqlite.Row) -> dict:
    """Convert a database row to an entity dict with deserialized fields."""
    return {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "content": row["content"],
        "status": row["status"],
        "tags": _deserialize_list(row["tags"]),
        "priority": row["priority"],
        "effort": row["effort"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_referenced_at": row["last_referenced_at"],
        "due_date": row["due_date"],
        "parent_id": row["parent_id"],
        "linked_ids": _deserialize_list(row["linked_ids"]),
        "people": _deserialize_list(row["people"]),
        "source": row["source"],
        "source_round_id": row["source_round_id"],
        "confidence": row["confidence"],
        "metadata": _deserialize_dict(row["metadata"]),
        "project_id": row["project_id"],
    }


async def create_entity(
    db_path: str,
    *,
    type: str,
    title: str,
    content: str = "",
    status: str = "active",
    tags: list[str] | None = None,
    priority: str = "medium",
    effort: str | None = None,
    due_date: str | None = None,
    parent_id: str | None = None,
    linked_ids: list[str] | None = None,
    people: list[str] | None = None,
    source: str = "extracted",
    source_round_id: str | None = None,
    confidence: float = 1.0,
    metadata: dict | None = None,
    project_id: str = "default",
) -> dict:
    """Create a new entity and return it with all fields populated.

    ``project_id`` scopes the entity to a Workbench project so its deadline shows
    on that project's calendar (日程). Defaults to ``"default"`` for globally /
    auto-extracted entities.

    Reminder coordination belongs to :class:`EntityService`; this repository
    function only persists entity state.
    """
    entity_id = _new_id()
    now = _now()

    if metadata is None:
        metadata = {}

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO entities (
                id, type, title, content, status, tags, priority, effort,
                created_at, updated_at, last_referenced_at, due_date, parent_id,
                linked_ids, people, source, source_round_id, confidence, metadata,
                project_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                type,
                title,
                content,
                status,
                _serialize_list(tags),
                priority,
                effort,
                now,
                now,
                now,
                due_date,
                parent_id,
                _serialize_list(linked_ids),
                _serialize_list(people),
                source,
                source_round_id,
                confidence,
                _serialize_dict(metadata),
                "default" if project_id is None else str(project_id),
            ),
        )
        await db.commit()

        # Fetch and return the created entity
        cursor = await db.execute("SELECT * FROM entities WHERE id = ?", (entity_id,))
        row = await cursor.fetchone()
        return _row_to_entity(row) if row else {}


async def update_entity(db_path: str, entity_id: str, **fields) -> dict | None:
    """Update specified fields of an entity and return the updated entity."""
    if not fields:
        return await get_entity(db_path, entity_id)
    unsupported = sorted(set(fields) - _UPDATABLE_FIELDS)
    if unsupported:
        raise ValueError("unsupported entity field(s): " + ", ".join(unsupported))

    # Build the update query dynamically
    now = _now()
    set_clauses = ["updated_at = ?", "last_referenced_at = ?"]
    values: list[Any] = [now, now]

    for key, value in fields.items():
        if key == "tags":
            set_clauses.append("tags = ?")
            values.append(_serialize_list(value if isinstance(value, list) else []))
        elif key == "linked_ids":
            set_clauses.append("linked_ids = ?")
            values.append(_serialize_list(value if isinstance(value, list) else []))
        elif key == "people":
            set_clauses.append("people = ?")
            values.append(_serialize_list(value if isinstance(value, list) else []))
        elif key == "metadata":
            set_clauses.append("metadata = ?")
            values.append(_serialize_dict(value if isinstance(value, dict) else {}))
        elif key in ("status", "priority", "content", "title", "effort", "due_date", "parent_id"):
            set_clauses.append(f"{key} = ?")
            values.append(value)

    values.append(entity_id)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            f"UPDATE entities SET {', '.join(set_clauses)} WHERE id = ?",
            values,
        )
        await db.commit()

        # Fetch and return the updated entity
        cursor = await db.execute("SELECT * FROM entities WHERE id = ?", (entity_id,))
        row = await cursor.fetchone()
        return _row_to_entity(row) if row else None


async def delete_entity(db_path: str, entity_id: str, permanent: bool = False) -> bool:
    """Delete or archive an entity.

    If permanent=False (default), sets status to 'archived' (soft delete).
    If permanent=True, deletes the entity permanently.
    Reminder coordination belongs to :class:`EntityService`.
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("SELECT 1 FROM entities WHERE id = ?", (entity_id,))
        row = await cursor.fetchone()

        if row is None:
            return False

        if permanent:
            await db.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        else:
            await db.execute("UPDATE entities SET status = ? WHERE id = ?", ("archived", entity_id))

        await db.commit()
        return True


async def find_entities_by_title(
    db_path: str,
    title: str,
    *,
    type: str | None = None,
    project_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Find entities whose title matches exactly.

    This is intentionally an exact-title lookup.  Delete/update operations must
    never turn a fuzzy search result into an implicit destructive action.
    Archived rows are included so an operator can also clean up stale duplicates.
    """
    normalized_title = str(title or "").strip()
    if not normalized_title:
        return []

    query = "SELECT * FROM entities WHERE title = ?"
    params: list[Any] = [normalized_title]
    if type:
        query += " AND type = ?"
        params.append(type)
    if project_id is not None:
        query += " AND COALESCE(project_id, 'default') = ?"
        params.append(project_id)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_entity(row) for row in rows]


async def find_entities_by_id_prefix(
    db_path: str,
    id_prefix: str,
    *,
    project_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Find entities whose IDs start with ``id_prefix`` for concise resolution."""
    normalized_prefix = str(id_prefix or "").strip()
    if not normalized_prefix:
        return []

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM entities WHERE substr(id, 1, ?) = ?"
        params: list[Any] = [len(normalized_prefix), normalized_prefix]
        if project_id is not None:
            query += " AND COALESCE(project_id, 'default') = ?"
            params.append(project_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_entity(row) for row in rows]


async def get_entity(db_path: str, entity_id: str) -> dict | None:
    """Get a single entity by ID."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM entities WHERE id = ?", (entity_id,))
        row = await cursor.fetchone()
        return _row_to_entity(row) if row else None


async def list_entities(
    db_path: str,
    *,
    type: str | None = None,
    status: str | None = None,
    has_due_date: bool = False,
    project_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """List entities with optional filtering.

    Args:
        type: Filter by entity type (optional)
        status: Filter by status (default: None = all)
        has_due_date: If True, only return entities with due_date
        project_id: Scope to a Workbench project (optional; None = all projects)
        limit: Maximum number of results
    """
    query = "SELECT * FROM entities WHERE 1=1"
    params: list[Any] = []

    if type:
        query += " AND type = ?"
        params.append(type)

    if status:
        query += " AND status = ?"
        params.append(status)
    else:
        query += " AND status NOT IN ('archived', 'abandoned')"

    if has_due_date:
        query += " AND due_date IS NOT NULL"

    if project_id is not None:
        query += " AND COALESCE(project_id, 'default') = ?"
        params.append(project_id)

    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_entity(row) for row in rows]


async def query_entities(
    db_path: str,
    q: str = "",
    *,
    type: str | None = None,
    status: str | None = None,
    due_before: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Search entities by keyword and apply filters.

    Args:
        q: Search keyword (matches title and content)
        type: Filter by entity type (optional)
        status: Filter by status (None = exclude only archived/abandoned)
        due_before: Filter to entities with due_date before this time (ISO 8601)
        project_id: Scope to a Workbench project (optional; None = all projects)
        limit: Maximum number of results
    """
    query = "SELECT * FROM entities WHERE 1=1"
    params: list[Any] = []

    if q:
        query += " AND (title LIKE ? OR content LIKE ?)"
        search_pattern = f"%{q}%"
        params.extend([search_pattern, search_pattern])

    if type:
        query += " AND type = ?"
        params.append(type)

    if status:
        query += " AND status = ?"
        params.append(status)
    else:
        query += " AND status NOT IN ('archived', 'abandoned')"

    if due_before:
        query += " AND due_date IS NOT NULL AND due_date < ?"
        params.append(due_before)

    if project_id is not None:
        query += " AND COALESCE(project_id, 'default') = ?"
        params.append(project_id)

    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_entity(row) for row in rows]


async def add_candidate(
    db_path: str,
    *,
    type: str,
    title: str,
    content: str = "",
    confidence: float,
    source_round_id: str | None = None,
    project_id: str = "default",
    raw_text: str | None = None,
) -> str:
    """Add a candidate entity and return its ID."""
    candidate_id = _new_id()
    now = _now()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO entity_candidates (
                id, type, title, content, confidence, source_round_id,
                project_id, raw_text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                type,
                title,
                content,
                confidence,
                source_round_id,
                str(project_id or "default"),
                raw_text,
                now,
            ),
        )
        await db.commit()

    return candidate_id


async def list_candidates(
    db_path: str,
    limit: int = 50,
    *,
    project_id: str | None = None,
) -> list[dict]:
    """List all candidate entities."""
    query = "SELECT * FROM entity_candidates"
    params: list[Any] = []
    if project_id is not None:
        query += " WHERE COALESCE(project_id, 'default') = ?"
        params.append(project_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "type": row["type"],
                "title": row["title"],
                "content": row["content"],
                "confidence": row["confidence"],
                "source_round_id": row["source_round_id"],
                "project_id": row["project_id"],
                "raw_text": row["raw_text"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]


async def promote_candidate(db_path: str, candidate_id: str) -> dict | None:
    """Promote a candidate to a full entity."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM entity_candidates WHERE id = ?",
            (candidate_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        # Create the entity
        entity = await create_entity(
            db_path,
            type=row["type"],
            title=row["title"],
            content=row["content"],
            confidence=row["confidence"],
            source="extracted",
            source_round_id=row["source_round_id"],
            project_id=row["project_id"],
        )

        # Delete the candidate
        await db.execute("DELETE FROM entity_candidates WHERE id = ?", (candidate_id,))
        await db.commit()

        return entity


async def reject_candidate(db_path: str, candidate_id: str) -> bool:
    """Reject a candidate and lower the type confidence."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT type FROM entity_candidates WHERE id = ?",
            (candidate_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return False

        entity_type = row["type"]

        # Update type confidence: lower by 0.05
        await db.execute(
            """
            INSERT INTO entity_type_confidence (type, adjustment, sample_count, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(type) DO UPDATE SET
                adjustment = adjustment - 0.05,
                sample_count = sample_count + 1,
                updated_at = ?
            """,
            (entity_type, -0.05, 1, _now(), _now()),
        )

        # Delete the candidate
        await db.execute("DELETE FROM entity_candidates WHERE id = ?", (candidate_id,))
        await db.commit()

        return True


async def process_candidates(db_path: str) -> list[dict]:
    """Automatically promote candidates with confidence >= 0.8 to full entities.

    Returns the list of promoted entities.
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id FROM entity_candidates WHERE confidence >= 0.8 ORDER BY created_at ASC",
        )
        rows = await cursor.fetchall()

    promoted = []
    for row in rows:
        result = await promote_candidate(db_path, row["id"])
        if result:
            promoted.append(result)

    return promoted


async def has_similar_entity(
    db_path: str,
    type: str,
    title: str,
    *,
    project_id: str = "default",
) -> bool:
    """Check if a similar entity already exists (same type + overlapping title).

    Checks both the ``entities`` and ``entity_candidates`` tables.
    Uses substring matching so "买点菜" matches "记得买菜" etc.
    """
    search = f"%{title}%"
    async with aiosqlite.connect(db_path) as db:
        # Check entities
        cursor = await db.execute(
            "SELECT COUNT(*) FROM entities "
            "WHERE type = ? AND title LIKE ? "
            "AND COALESCE(project_id, 'default') = ?",
            (type, search, str(project_id or "default")),
        )
        row = await cursor.fetchone()
        if row and row[0] > 0:
            return True

        # Check candidates
        cursor = await db.execute(
            "SELECT COUNT(*) FROM entity_candidates "
            "WHERE type = ? AND title LIKE ? "
            "AND COALESCE(project_id, 'default') = ?",
            (type, search, str(project_id or "default")),
        )
        row = await cursor.fetchone()
        if row and row[0] > 0:
            return True

    return False


async def adjust_type_confidence(db_path: str, type: str, delta: float) -> None:
    """Adjust the confidence adjustment for a specific entity type."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO entity_type_confidence (type, adjustment, sample_count, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(type) DO UPDATE SET
                adjustment = adjustment + ?,
                sample_count = sample_count + 1,
                updated_at = ?
            """,
            (type, delta, 1, _now(), delta, _now()),
        )
        await db.commit()


class EntityRepository:
    """Bound repository used by services and non-Agent application surfaces."""

    def __init__(self, db_path: str) -> None:
        normalized = str(db_path or "").strip()
        if not normalized:
            raise ValueError("entity repository requires a database path")
        self.db_path = normalized
        self._ready = False

    async def ensure_ready(self) -> None:
        if self._ready:
            return
        await _initialize_store(self.db_path)
        self._ready = True

    async def create(self, **values: Any) -> dict:
        await self.ensure_ready()
        return await create_entity(self.db_path, **values)

    async def update(self, entity_id: str, **fields: Any) -> dict | None:
        await self.ensure_ready()
        return await update_entity(self.db_path, entity_id, **fields)

    async def delete(self, entity_id: str, *, permanent: bool = False) -> bool:
        await self.ensure_ready()
        return await delete_entity(self.db_path, entity_id, permanent=permanent)

    async def get(self, entity_id: str) -> dict | None:
        await self.ensure_ready()
        return await get_entity(self.db_path, entity_id)

    async def find_by_title(
        self,
        title: str,
        *,
        type: str | None = None,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        await self.ensure_ready()
        return await find_entities_by_title(
            self.db_path,
            title,
            type=type,
            project_id=project_id,
            limit=limit,
        )

    async def find_by_id_prefix(
        self,
        prefix: str,
        *,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        await self.ensure_ready()
        return await find_entities_by_id_prefix(
            self.db_path,
            prefix,
            project_id=project_id,
            limit=limit,
        )

    async def list(self, **filters: Any) -> list[dict]:
        await self.ensure_ready()
        return await list_entities(self.db_path, **filters)

    async def query(self, q: str = "", **filters: Any) -> list[dict]:
        await self.ensure_ready()
        return await query_entities(self.db_path, q, **filters)

    async def add_candidate(self, **values: Any) -> str:
        await self.ensure_ready()
        return await add_candidate(self.db_path, **values)

    async def list_candidates(
        self,
        *,
        limit: int = 50,
        project_id: str | None = None,
    ) -> list[dict]:
        await self.ensure_ready()
        return await list_candidates(
            self.db_path,
            limit=limit,
            project_id=project_id,
        )

    async def promote_candidate(self, candidate_id: str) -> dict | None:
        await self.ensure_ready()
        return await promote_candidate(self.db_path, candidate_id)

    async def reject_candidate(self, candidate_id: str) -> bool:
        await self.ensure_ready()
        return await reject_candidate(self.db_path, candidate_id)

    async def process_candidates(self) -> list[dict]:
        await self.ensure_ready()
        return await process_candidates(self.db_path)

    async def has_similar(
        self,
        type: str,
        title: str,
        *,
        project_id: str = "default",
    ) -> bool:
        await self.ensure_ready()
        return await has_similar_entity(
            self.db_path,
            type,
            title,
            project_id=project_id,
        )

    async def adjust_type_confidence(self, type: str, delta: float) -> None:
        await self.ensure_ready()
        await adjust_type_confidence(self.db_path, type, delta)


__all__ = ["EntityRepository"]
