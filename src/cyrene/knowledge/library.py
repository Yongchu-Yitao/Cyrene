"""Structured literature library stored in a project knowledge database.

The caller supplies the already-resolved ``kb_<project>.db`` path.  This keeps
project isolation at the storage boundary while allowing a literature item to
reference an ordinary ``kb_document`` for extraction and vector retrieval.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import aiosqlite


ITEM_FIELDS = {
    "item_type", "title", "abstract", "doi", "isbn", "url", "venue",
    "publisher", "volume", "issue", "pages", "language", "year",
    "date_text", "citekey", "reading_status", "starred", "tags", "csl_json",
    "last_read_at",
}
READING_STATUSES = {"unread", "reading", "read", "archived"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    if not value:
        return default
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError):
        return default


def _json(value: Any, default: Any) -> str:
    if not isinstance(value, type(default)):
        value = default
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clean_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if isinstance(raw, dict):
            raw = raw.get("tag") or raw.get("name")
        tag = str(raw or "").strip()
        folded = tag.casefold()
        if tag and folded not in seen:
            result.append(tag)
            seen.add(folded)
    return result


def _source_abstract(metadata: Any) -> str:
    """Return only an abstract explicitly supplied by source metadata.

    ``kb_documents.summary`` is an indexing preview generated from extracted
    content (including image descriptions), so it must not be presented as a
    bibliographic abstract. Accept only fields whose source semantics are
    explicitly "abstract".
    """
    if not isinstance(metadata, dict):
        return ""
    candidates = [metadata]
    for key in ("bibliographic", "csl_json", "zotero"):
        nested = metadata.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        for key in ("abstract", "abstractNote"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _row_item(row: aiosqlite.Row) -> dict[str, Any]:
    result = dict(row)
    result["starred"] = bool(result.get("starred"))
    result["tags"] = _loads(result.get("tags"), [])
    result["csl_json"] = _loads(result.get("csl_json"), {})
    result["raw_json"] = _loads(result.get("raw_json"), {})
    return result


def _row_json(row: aiosqlite.Row, *fields: str) -> dict[str, Any]:
    result = dict(row)
    for field in fields:
        result[field] = _loads(result.get(field), {})
    return result


async def _fetch_children(
    db: aiosqlite.Connection,
    table: str,
    item_id: str,
    *,
    order: str = "created_at ASC",
) -> list[dict[str, Any]]:
    allowed = {
        "library_creators", "library_attachments", "library_notes",
        "library_annotations",
    }
    if table not in allowed:
        raise ValueError("unsupported child table")
    if table == "library_attachments":
        cursor = await db.execute(
            f"""SELECT a.*,COALESCE(d.size,0) AS size,d.status AS index_status,
                d.chunk_count,d.indexed_at,d.metadata AS document_metadata
                FROM library_attachments a LEFT JOIN kb_documents d ON d.id=a.kb_document_id
                WHERE a.item_id=? ORDER BY a.{order}""",
            (item_id,),
        )
    else:
        cursor = await db.execute(
            f"SELECT * FROM {table} WHERE item_id = ? ORDER BY {order}", (item_id,)
        )
    rows = await cursor.fetchall()
    json_fields = {
        "library_attachments": ("raw_json",),
        "library_notes": ("raw_json",),
        "library_annotations": ("position_json", "raw_json"),
    }.get(table, ())
    result = [_row_json(row, *json_fields) for row in rows]
    if table == "library_attachments":
        for attachment in result:
            document_metadata = _loads(attachment.pop("document_metadata", None), {})
            raw_json = attachment.get("raw_json") or {}
            page_count = (
                document_metadata.get("page_count") or raw_json.get("page_count")
            )
            try:
                page_count = int(page_count or 0)
            except (TypeError, ValueError):
                page_count = 0
            if page_count <= 0 and (
                str(attachment.get("content_type") or "").lower() == "application/pdf"
                or Path(
                    str(
                        attachment.get("filename")
                        or attachment.get("path")
                        or ""
                    )
                ).suffix.lower()
                == ".pdf"
            ):
                path = Path(str(attachment.get("path") or ""))
                if not path.is_file():
                    from cyrene.runtime.attachments import resolve_managed_attachment_path

                    relocated = resolve_managed_attachment_path(str(path))
                    if relocated is not None:
                        path = relocated
                if path.is_file():
                    try:
                        from pypdf import PdfReader

                        page_count = len(PdfReader(str(path)).pages)
                        document_metadata["page_count"] = page_count
                        if attachment.get("kb_document_id"):
                            await db.execute(
                                "UPDATE kb_documents SET metadata=? WHERE id=?",
                                (_json(document_metadata, {}), attachment["kb_document_id"]),
                            )
                            await db.commit()
                    except Exception:
                        page_count = 0
            if page_count > 0:
                attachment["page_count"] = page_count
    return result


async def _hydrate(db: aiosqlite.Connection, item: dict[str, Any]) -> dict[str, Any]:
    item_id = item["id"]
    item["creators"] = await _fetch_children(
        db, "library_creators", item_id, order="ordinal ASC"
    )
    cursor = await db.execute(
        """
        SELECT c.* FROM library_collections c
        JOIN library_collection_items ci ON ci.collection_id = c.id
        WHERE ci.item_id = ? ORDER BY c.sort_order ASC, c.name COLLATE NOCASE ASC
        """,
        (item_id,),
    )
    item["collections"] = [
        _row_json(row, "raw_json") for row in await cursor.fetchall()
    ]
    item["attachments"] = await _fetch_children(db, "library_attachments", item_id)
    item["notes"] = await _fetch_children(
        db, "library_notes", item_id, order="updated_at DESC"
    )
    item["annotations"] = await _fetch_children(
        db, "library_annotations", item_id, order="created_at ASC"
    )
    cursor = await db.execute(
        """
        SELECT r.*, CASE WHEN r.src_item_id = ? THEN r.dst_item_id ELSE r.src_item_id END AS other_item_id,
               i.title AS other_title,i.title AS title,r.relation AS relation_type
        FROM library_relations r
        LEFT JOIN library_items i ON i.id = CASE WHEN r.src_item_id = ? THEN r.dst_item_id ELSE r.src_item_id END
        WHERE r.src_item_id = ? OR r.dst_item_id = ? ORDER BY r.created_at DESC
        """,
        (item_id, item_id, item_id, item_id),
    )
    item["relations"] = [dict(row) for row in await cursor.fetchall()]
    item["attachment_count"] = len(item["attachments"])
    item["note_count"] = len(item["notes"])
    item["annotation_count"] = len(item["annotations"])
    return item


def _creator_text(creators: Iterable[dict[str, Any]]) -> str:
    return " ".join(
        str(c.get("name") or " ".join(
            part for part in (str(c.get("first_name") or "").strip(), str(c.get("last_name") or "").strip()) if part
        )).strip()
        for c in creators
    ).strip()


async def sync_knowledge_documents(db_path: str) -> int:
    """Expose legacy/current ``kb_documents`` in the structured library view.

    The bridge creates metadata rows and attachments in the same project DB; it
    does not copy files or move records across projects. Documents already
    linked to a structured literature item keep their identity. Their abstract
    is reconciled with explicit source metadata so generated indexing previews
    are never exposed as bibliographic abstracts.
    """
    created = 0
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        # List/stats/tags are loaded in parallel by the UI. Serializing this
        # small migration avoids duplicate bridge rows on a project's first
        # visit while still letting later knowledge uploads appear immediately.
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """SELECT DISTINCT i.id,i.abstract,d.metadata,d.summary
               FROM library_items i
               JOIN library_attachments a ON a.item_id=i.id
               JOIN kb_documents d ON d.id=a.kb_document_id
               WHERE i.provider='knowledge'"""
        )
        for linked in await cursor.fetchall():
            source_abstract = _source_abstract(_loads(linked["metadata"], {}))
            current = str(linked["abstract"] or "")
            # An abstract explicitly supplied by source metadata is
            # authoritative and overrides the local value.
            if source_abstract and current != source_abstract:
                await db.execute(
                    "UPDATE library_items SET abstract=? WHERE id=?",
                    (source_abstract, linked["id"]),
                )
                await _refresh_fts(db, str(linked["id"]))
            # Pre-bridge versions copied the generated indexing preview into
            # abstract; that pollution is repaired to ''. A user/agent edit
            # never equals the generated summary verbatim, so it is preserved
            # when the source metadata has no abstract of its own.
            elif not source_abstract and current and current == str(linked["summary"] or ""):
                await db.execute(
                    "UPDATE library_items SET abstract=? WHERE id=?",
                    ("", linked["id"]),
                )
                await _refresh_fts(db, str(linked["id"]))

        cursor = await db.execute(
            """SELECT d.* FROM kb_documents d
               WHERE NOT EXISTS (
                 SELECT 1 FROM library_attachments a
                 WHERE a.kb_document_id=d.id
               )
               ORDER BY d.created_at ASC"""
        )
        documents = await cursor.fetchall()
        for document in documents:
            document_id = str(document["id"])
            cursor = await db.execute(
                """SELECT id FROM library_items
                   WHERE provider='knowledge' AND provider_library_id=''
                     AND provider_item_key=?""",
                (document_id,),
            )
            existing = await cursor.fetchone()
            item_id = str(existing["id"]) if existing else _id()
            metadata = _loads(document["metadata"], {})
            source_abstract = _source_abstract(metadata)
            tags = _clean_tags(_loads(document["tags"], []))
            created_at = str(document["created_at"] or _now())
            updated_at = str(document["updated_at"] or created_at)
            if not existing:
                await db.execute(
                    """INSERT INTO library_items (
                        id,provider,provider_library_id,provider_item_key,provider_version,
                        item_type,title,abstract,doi,isbn,url,venue,publisher,volume,issue,pages,
                        language,year,date_text,citekey,reading_status,last_read_at,starred,tags,
                        csl_json,raw_json,created_at,updated_at,synced_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item_id, "knowledge", "", document_id, 0,
                        str(metadata.get("item_type") or "document"),
                        str(document["title"] or document["name"] or "未命名文档"),
                        source_abstract, "", "", "", "", "", "", "", "",
                        str(metadata.get("language") or ""), None, "", "", "unread", None, 0,
                        _json(tags, []), "{}", _json(
                            {
                                "kb_document_id": document_id,
                                "source": str(document["source"] or ""),
                                "metadata": metadata,
                            },
                            {},
                        ),
                        created_at, updated_at, document["indexed_at"],
                    ),
                )
                created += 1
            else:
                if source_abstract:
                    await db.execute(
                        "UPDATE library_items SET abstract=? WHERE id=?",
                        (source_abstract, item_id),
                    )
            await db.execute(
                """INSERT INTO library_attachments (
                    id,item_id,provider,provider_library_id,provider_key,provider_version,
                    kb_document_id,title,filename,path,content_type,link_mode,content_hash,
                    raw_json,created_at,updated_at
                ) SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                  WHERE NOT EXISTS (
                    SELECT 1 FROM library_attachments WHERE kb_document_id=?
                  )""",
                (
                    _id(), item_id, "knowledge", "", document_id, 0, document_id,
                    str(document["title"] or document["name"] or ""),
                    str(document["name"] or ""), str(document["path"] or ""),
                    str(document["content_type"] or ""), "knowledge_document",
                    str(document["content_hash"] or ""), _json(metadata, {}),
                    created_at, updated_at, document_id,
                ),
            )
            await _refresh_fts(db, item_id)
        await db.commit()
    return created


async def _refresh_fts(db: aiosqlite.Connection, item_id: str) -> None:
    cursor = await db.execute("SELECT * FROM library_items WHERE id = ?", (item_id,))
    row = await cursor.fetchone()
    await db.execute("DELETE FROM library_items_fts WHERE item_id = ?", (item_id,))
    if not row or row["deleted_at"]:
        return
    cursor = await db.execute(
        "SELECT * FROM library_creators WHERE item_id = ? ORDER BY ordinal", (item_id,)
    )
    creators = [dict(value) for value in await cursor.fetchall()]
    await db.execute(
        """INSERT INTO library_items_fts
           (title, creators, abstract, doi, venue, tags, item_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            row["title"], _creator_text(creators), row["abstract"], row["doi"],
            row["venue"], " ".join(_loads(row["tags"], [])), item_id,
        ),
    )


def _normalized_creators(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    creators = []
    for ordinal, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        creators.append({
            "creator_type": str(raw.get("creator_type") or raw.get("creatorType") or "author"),
            "first_name": str(raw.get("first_name") or raw.get("firstName") or ""),
            "last_name": str(raw.get("last_name") or raw.get("lastName") or ""),
            "name": str(raw.get("name") or ""),
            "ordinal": ordinal,
        })
    return creators


async def _replace_creators(
    db: aiosqlite.Connection, item_id: str, creators: Any
) -> None:
    await db.execute("DELETE FROM library_creators WHERE item_id = ?", (item_id,))
    now = _now()
    for creator in _normalized_creators(creators):
        await db.execute(
            """INSERT INTO library_creators
               (id,item_id,creator_type,first_name,last_name,name,ordinal,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                _id(), item_id, creator["creator_type"], creator["first_name"],
                creator["last_name"], creator["name"], creator["ordinal"], now,
            ),
        )


async def _replace_collections(
    db: aiosqlite.Connection, item_id: str, collection_ids: Any
) -> None:
    if not isinstance(collection_ids, list):
        return
    await db.execute("DELETE FROM library_collection_items WHERE item_id = ?", (item_id,))
    now = _now()
    for collection_id in dict.fromkeys(str(value) for value in collection_ids if value):
        await db.execute(
            "INSERT OR IGNORE INTO library_collection_items(collection_id,item_id,created_at) VALUES(?,?,?)",
            (collection_id, item_id, now),
        )


async def create_item(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    item_id = _id()
    now = _now()
    values = {key: payload.get(key) for key in ITEM_FIELDS}
    status = str(values.get("reading_status") or "unread")
    if status not in READING_STATUSES:
        status = "unread"
    year = values.get("year")
    try:
        year = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO library_items (
                id,provider,provider_library_id,provider_item_key,provider_version,
                item_type,title,abstract,doi,isbn,url,venue,publisher,volume,issue,pages,
                language,year,date_text,citekey,reading_status,last_read_at,starred,tags,csl_json,
                raw_json,created_at,updated_at,synced_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item_id, str(payload.get("provider") or "cyrene"),
                str(payload.get("provider_library_id") or ""),
                str(payload.get("provider_item_key") or ""),
                int(payload.get("provider_version") or 0),
                str(values.get("item_type") or "document"),
                str(values.get("title") or ""), str(values.get("abstract") or ""),
                str(values.get("doi") or ""), str(values.get("isbn") or ""),
                str(values.get("url") or ""), str(values.get("venue") or ""),
                str(values.get("publisher") or ""), str(values.get("volume") or ""),
                str(values.get("issue") or ""), str(values.get("pages") or ""),
                str(values.get("language") or ""), year,
                str(values.get("date_text") or ""), str(values.get("citekey") or ""),
                status, values.get("last_read_at") or (now if status == "read" else None),
                int(bool(values.get("starred"))), _json(_clean_tags(values.get("tags")), []),
                _json(values.get("csl_json"), {}), _json(payload.get("raw_json"), {}),
                now, now, payload.get("synced_at"),
            ),
        )
        await _replace_creators(db, item_id, payload.get("creators"))
        await _replace_collections(db, item_id, payload.get("collection_ids"))
        await _refresh_fts(db, item_id)
        await db.commit()
    return await get_item(db_path, item_id) or {}


async def get_item(
    db_path: str, item_id: str, *, include_deleted: bool = False
) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        sql = "SELECT * FROM library_items WHERE id = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        cursor = await db.execute(sql, (item_id,))
        row = await cursor.fetchone()
        return await _hydrate(db, _row_item(row)) if row else None


async def get_item_by_provider_key(
    db_path: str, provider: str, library_id: str, key: str
) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM library_items WHERE provider=? AND provider_library_id=?
               AND provider_item_key=?""",
            (provider, library_id, key),
        )
        row = await cursor.fetchone()
        return await _hydrate(db, _row_item(row)) if row else None


async def update_item(
    db_path: str, item_id: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    fields: dict[str, Any] = {}
    for key in ITEM_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if key == "tags":
            value = _json(_clean_tags(value), [])
        elif key == "csl_json":
            value = _json(value, {})
        elif key == "starred":
            value = int(bool(value))
        elif key == "reading_status":
            value = str(value or "unread")
            if value not in READING_STATUSES:
                raise ValueError("invalid reading_status")
            if value == "read" and "last_read_at" not in payload:
                fields["last_read_at"] = _now()
        elif key == "year":
            try:
                value = int(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                value = None
        elif value is None:
            value = ""
        fields[key] = value
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        if fields:
            fields["updated_at"] = _now()
            columns = ", ".join(f"{key} = ?" for key in fields)
            cursor = await db.execute(
                f"UPDATE library_items SET {columns} WHERE id = ?",
                [*fields.values(), item_id],
            )
            if cursor.rowcount == 0:
                return None
        else:
            cursor = await db.execute("SELECT 1 FROM library_items WHERE id = ?", (item_id,))
            if not await cursor.fetchone():
                return None
        if "creators" in payload:
            await _replace_creators(db, item_id, payload.get("creators"))
        if "collection_ids" in payload:
            await _replace_collections(db, item_id, payload.get("collection_ids"))
        await _refresh_fts(db, item_id)
        await db.commit()
    return await get_item(db_path, item_id, include_deleted=True)


async def list_items(
    db_path: str,
    *,
    q: str = "",
    collection: str = "",
    status: str = "",
    tag: str = "",
    item_type: str = "",
    file_type: str = "",
    year: int | str | None = None,
    starred: bool | None = None,
    trash: bool = False,
    sort: str = "updated_at",
    order: str = "desc",
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    await sync_knowledge_documents(db_path)
    where = ["i.deleted_at IS NOT NULL" if trash else "i.deleted_at IS NULL"]
    params: list[Any] = []
    if q.strip():
        pattern = f"%{q.strip()}%"
        where.append("""(i.title LIKE ? OR i.abstract LIKE ? OR i.doi LIKE ? OR i.venue LIKE ?
            OR EXISTS (SELECT 1 FROM library_creators cr WHERE cr.item_id=i.id
                AND (cr.name LIKE ? OR cr.first_name LIKE ? OR cr.last_name LIKE ?))
            OR EXISTS (SELECT 1 FROM library_notes n WHERE n.item_id=i.id
                AND (n.title LIKE ? OR n.content LIKE ?))
            OR EXISTS (SELECT 1 FROM library_annotations a WHERE a.item_id=i.id
                AND (a.quote LIKE ? OR a.comment LIKE ?)))""")
        params.extend([pattern] * 11)
    if collection == "__unclassified__":
        where.append("NOT EXISTS (SELECT 1 FROM library_collection_items ci WHERE ci.item_id=i.id)")
    elif collection:
        where.append("EXISTS (SELECT 1 FROM library_collection_items ci WHERE ci.item_id=i.id AND ci.collection_id=?)")
        params.append(collection)
    if status == "recent_added":
        where.append("i.created_at>=?")
        params.append((datetime.now(timezone.utc) - timedelta(days=30)).isoformat())
    elif status == "recent_read":
        where.append("i.last_read_at>=?")
        params.append((datetime.now(timezone.utc) - timedelta(days=30)).isoformat())
    elif status:
        where.append("i.reading_status=?")
        params.append(status)
    if tag:
        where.append("i.tags LIKE ?")
        params.append(f'%"{tag}"%')
    if item_type:
        where.append("i.item_type=?")
        params.append(item_type)
    if file_type:
        attachment_prefix = """EXISTS (
            SELECT 1 FROM library_attachments a
            WHERE a.item_id=i.id AND """
        attachment_suffix = ")"
        file_type_sql = {
            "pdf": """(
                lower(a.content_type)='application/pdf'
                OR lower(a.filename) GLOB '*.pdf'
            )""",
            "image": """(
                lower(a.content_type) LIKE 'image/%'
                OR lower(a.filename) GLOB '*.avif'
                OR lower(a.filename) GLOB '*.bmp'
                OR lower(a.filename) GLOB '*.gif'
                OR lower(a.filename) GLOB '*.jpeg'
                OR lower(a.filename) GLOB '*.jpg'
                OR lower(a.filename) GLOB '*.png'
                OR lower(a.filename) GLOB '*.webp'
            )""",
            "audio": """(
                lower(a.content_type) LIKE 'audio/%'
                OR lower(a.filename) GLOB '*.aac'
                OR lower(a.filename) GLOB '*.flac'
                OR lower(a.filename) GLOB '*.m4a'
                OR lower(a.filename) GLOB '*.mp3'
                OR lower(a.filename) GLOB '*.oga'
                OR lower(a.filename) GLOB '*.ogg'
                OR lower(a.filename) GLOB '*.wav'
                OR lower(a.filename) GLOB '*.weba'
            )""",
            "video": """(
                lower(a.content_type) LIKE 'video/%'
                OR lower(a.filename) GLOB '*.m4v'
                OR lower(a.filename) GLOB '*.mov'
                OR lower(a.filename) GLOB '*.mp4'
                OR lower(a.filename) GLOB '*.ogv'
                OR lower(a.filename) GLOB '*.webm'
            )""",
            "spreadsheet": """(
                lower(a.content_type) IN ('text/csv','text/tab-separated-values')
                OR lower(a.content_type) LIKE '%spreadsheet%'
                OR lower(a.content_type) LIKE '%ms-excel%'
                OR lower(a.filename) GLOB '*.csv'
                OR lower(a.filename) GLOB '*.numbers'
                OR lower(a.filename) GLOB '*.tsv'
                OR lower(a.filename) GLOB '*.xls'
                OR lower(a.filename) GLOB '*.xlsm'
                OR lower(a.filename) GLOB '*.xlsx'
            )""",
            "presentation": """(
                lower(a.content_type) LIKE '%powerpoint%'
                OR lower(a.content_type) LIKE '%presentation%'
                OR lower(a.filename) GLOB '*.key'
                OR lower(a.filename) GLOB '*.odp'
                OR lower(a.filename) GLOB '*.ppt'
                OR lower(a.filename) GLOB '*.pptx'
            )""",
            "document": """(
                (
                    lower(a.content_type) LIKE 'text/%'
                    AND lower(a.content_type) NOT IN (
                        'text/csv','text/tab-separated-values','text/uri-list'
                    )
                )
                OR lower(a.content_type) LIKE '%msword%'
                OR lower(a.content_type) LIKE '%wordprocessing%'
                OR lower(a.content_type) LIKE '%opendocument.text%'
                OR lower(a.content_type) LIKE '%rtf%'
                OR lower(a.filename) GLOB '*.doc'
                OR lower(a.filename) GLOB '*.docx'
                OR lower(a.filename) GLOB '*.html'
                OR lower(a.filename) GLOB '*.htm'
                OR lower(a.filename) GLOB '*.json'
                OR lower(a.filename) GLOB '*.log'
                OR lower(a.filename) GLOB '*.md'
                OR lower(a.filename) GLOB '*.rtf'
                OR lower(a.filename) GLOB '*.txt'
                OR lower(a.filename) GLOB '*.xml'
                OR lower(a.filename) GLOB '*.yaml'
                OR lower(a.filename) GLOB '*.yml'
            )""",
            "link": """(
                lower(a.content_type)='text/uri-list'
                OR lower(a.filename) GLOB '*.link'
                OR lower(a.filename) GLOB '*.url'
                OR lower(a.filename) GLOB '*.webloc'
            )""",
        }
        condition = file_type_sql.get(file_type)
        if file_type == "link":
            where.append(
                f"(i.item_type='webpage' OR {attachment_prefix}{condition}{attachment_suffix})"
            )
        elif file_type == "document":
            where.append(
                f"""(
                    (i.item_type='document' AND NOT EXISTS (
                        SELECT 1 FROM library_attachments a WHERE a.item_id=i.id
                    ))
                    OR {attachment_prefix}{condition}{attachment_suffix}
                )"""
            )
        elif condition:
            where.append(attachment_prefix + condition + attachment_suffix)
        elif file_type == "other":
            recognized = " OR ".join(
                f"({value})" for value in file_type_sql.values()
            )
            where.append(
                f"""(
                    EXISTS (
                        SELECT 1 FROM library_attachments a WHERE a.item_id=i.id
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM library_attachments a
                        WHERE a.item_id=i.id AND ({recognized})
                    )
                    AND i.item_type<>'webpage'
                )"""
            )
    if year not in (None, ""):
        try:
            normalized_year = int(year)
        except (TypeError, ValueError):
            normalized_year = 0
        if normalized_year:
            where.append("i.year=?")
            params.append(normalized_year)
    if starred is not None:
        where.append("i.starred=?")
        params.append(int(starred))
    sort_column = {
        "title": "i.title COLLATE NOCASE", "year": "i.year",
        "created_at": "i.created_at", "updated_at": "i.updated_at",
        "added": "i.created_at", "author": "first_creator",
    }.get(sort, "i.updated_at")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    where_sql = " AND ".join(where)
    select_sql = """SELECT i.*, COALESCE((SELECT NULLIF(cr.name,'') FROM library_creators cr
        WHERE cr.item_id=i.id ORDER BY cr.ordinal LIMIT 1),
        (SELECT cr.last_name FROM library_creators cr WHERE cr.item_id=i.id ORDER BY cr.ordinal LIMIT 1), '') AS first_creator,
        (SELECT COUNT(*) FROM library_attachments a WHERE a.item_id=i.id) AS attachment_count,
        (SELECT COUNT(*) FROM library_notes n WHERE n.item_id=i.id) AS note_count,
        (SELECT a.filename FROM library_attachments a WHERE a.item_id=i.id ORDER BY a.created_at LIMIT 1) AS attachment_name,
        (SELECT a.content_type FROM library_attachments a WHERE a.item_id=i.id ORDER BY a.created_at LIMIT 1) AS content_type,
        (SELECT d.size FROM library_attachments a JOIN kb_documents d ON d.id=a.kb_document_id
            WHERE a.item_id=i.id ORDER BY a.created_at LIMIT 1) AS attachment_size
        FROM library_items i"""
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT COUNT(*) FROM library_items i WHERE {where_sql}", params
        )
        total = int((await cursor.fetchone())[0])
        sql = f"{select_sql} WHERE {where_sql} ORDER BY {sort_column} {direction}, i.id ASC"
        query_params = list(params)
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            query_params.extend([min(limit, 1000), max(offset, 0)])
        cursor = await db.execute(sql, query_params)
        items = []
        for row in await cursor.fetchall():
            item = _row_item(row)
            item["creators"] = await _fetch_children(db, "library_creators", item["id"], order="ordinal ASC")
            items.append(item)
        return items, total


async def delete_items(
    db_path: str, item_ids: Iterable[str], *, permanent: bool = False
) -> int:
    normalized_ids = list(
        dict.fromkeys(str(item_id or "").strip() for item_id in item_ids)
    )
    normalized_ids = [item_id for item_id in normalized_ids if item_id]
    if not normalized_ids:
        return 0

    placeholders = ",".join("?" for _ in normalized_ids)
    async with aiosqlite.connect(db_path, timeout=30) as db:
        if permanent:
            # Remember the corpus rows these items link before the attachment
            # rows (which hold the link) are removed below.
            cursor = await db.execute(
                f"""SELECT DISTINCT kb_document_id FROM library_attachments
                    WHERE item_id IN ({placeholders})
                      AND kb_document_id IS NOT NULL AND kb_document_id<>''""",
                normalized_ids,
            )
            linked_doc_ids = [str(row[0]) for row in await cursor.fetchall()]
            orphan_docs: list[dict[str, Any]] = []
            for table in (
                "library_creators", "library_collection_items", "library_attachments",
                "library_notes", "library_annotations",
            ):
                await db.execute(
                    f"DELETE FROM {table} WHERE item_id IN ({placeholders})",
                    normalized_ids,
                )
            await db.execute(
                f"""DELETE FROM library_relations
                    WHERE src_item_id IN ({placeholders})
                       OR dst_item_id IN ({placeholders})""",
                normalized_ids + normalized_ids,
            )
            cursor = await db.execute(
                f"DELETE FROM library_items WHERE id IN ({placeholders})",
                normalized_ids,
            )
            deleted_count = max(cursor.rowcount, 0)
            # A permanently deleted item must not resurface through the
            # knowledge bridge, which re-imports kb_documents that have no
            # attachment link. Drop corpus rows orphaned by this deletion
            # (chunks, FTS, relations) plus the managed file, so neither the
            # bridge nor the catalog rescan can resurrect it.
            if linked_doc_ids:
                doc_placeholders = ",".join("?" for _ in linked_doc_ids)
                cursor = await db.execute(
                    f"""SELECT DISTINCT kb_document_id FROM library_attachments
                        WHERE kb_document_id IN ({doc_placeholders})""",
                    linked_doc_ids,
                )
                kept_doc_ids = {str(row[0]) for row in await cursor.fetchall()}
                orphan_doc_ids = [
                    value for value in linked_doc_ids if value not in kept_doc_ids
                ]
                if orphan_doc_ids:
                    orphan_placeholders = ",".join("?" for _ in orphan_doc_ids)
                    await db.execute(
                        f"DELETE FROM kb_chunks_fts WHERE document_id IN ({orphan_placeholders})",
                        orphan_doc_ids,
                    )
                    await db.execute(
                        f"DELETE FROM kb_chunks WHERE document_id IN ({orphan_placeholders})",
                        orphan_doc_ids,
                    )
                    await db.execute(
                        f"""DELETE FROM kb_relations
                            WHERE src_id IN ({orphan_placeholders})
                               OR dst_id IN ({orphan_placeholders})""",
                        orphan_doc_ids + orphan_doc_ids,
                    )
                    cursor = await db.execute(
                        f"SELECT id, path FROM kb_documents WHERE id IN ({orphan_placeholders})",
                        orphan_doc_ids,
                    )
                    orphan_docs = [{"id": row[0], "path": row[1]} for row in await cursor.fetchall()]
                    await db.execute(
                        f"DELETE FROM kb_documents WHERE id IN ({orphan_placeholders})",
                        orphan_doc_ids,
                    )
        else:
            now = _now()
            cursor = await db.execute(
                f"""UPDATE library_items SET deleted_at=?, updated_at=?
                    WHERE id IN ({placeholders}) AND deleted_at IS NULL""",
                [now, now] + normalized_ids,
            )
        await db.execute(
            f"DELETE FROM library_items_fts WHERE item_id IN ({placeholders})",
            normalized_ids,
        )
        await db.commit()
        if permanent:
            # Only managed files are removed: a path outside the upload/export
            # dirs may be the user's own file (e.g. a linked Zotero storage
            # file) and must not be touched by a library-side delete.
            from cyrene.runtime.attachments import resolve_managed_attachment_path
            for doc in orphan_docs:
                file_path = resolve_managed_attachment_path(str(doc.get("path") or ""))
                if file_path is not None:
                    try:
                        file_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            return deleted_count
        return max(cursor.rowcount, 0)


async def delete_item(db_path: str, item_id: str, *, permanent: bool = False) -> bool:
    return await delete_items(db_path, [item_id], permanent=permanent) > 0


async def restore_item(db_path: str, item_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "UPDATE library_items SET deleted_at=NULL,updated_at=? WHERE id=?",
            (_now(), item_id),
        )
        if cursor.rowcount == 0:
            return None
        await _refresh_fts(db, item_id)
        await db.commit()
    return await get_item(db_path, item_id)


async def create_collection(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    collection_id = str(payload.get("id") or _id())
    now = _now()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO library_collections
               (id,provider,provider_library_id,provider_key,provider_version,name,parent_id,
                sort_order,raw_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                collection_id, str(payload.get("provider") or "cyrene"),
                str(payload.get("provider_library_id") or ""), str(payload.get("provider_key") or ""),
                int(payload.get("provider_version") or 0), str(payload.get("name") or "未命名收藏夹"),
                payload.get("parent_id"), int(payload.get("sort_order") or 0),
                _json(payload.get("raw_json"), {}), now, now,
            ),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM library_collections WHERE id=?", (collection_id,))
        row = await cursor.fetchone()
        return _row_json(row, "raw_json")


async def upsert_collection(
    db_path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    provider = str(payload.get("provider") or "zotero")
    library_id = str(payload.get("provider_library_id") or "0")
    key = str(payload.get("provider_key") or "")
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id FROM library_collections WHERE provider=? AND provider_library_id=? AND provider_key=?",
            (provider, library_id, key),
        )
        row = await cursor.fetchone()
    if not row:
        return await create_collection(db_path, payload)
    collection_id = row["id"]
    now = _now()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """UPDATE library_collections SET provider_version=?,name=?,parent_id=?,
               sort_order=?,raw_json=?,updated_at=? WHERE id=?""",
            (
                int(payload.get("provider_version") or 0), str(payload.get("name") or "未命名收藏夹"),
                payload.get("parent_id"), int(payload.get("sort_order") or 0),
                _json(payload.get("raw_json"), {}), now, collection_id,
            ),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM library_collections WHERE id=?", (collection_id,))
        return _row_json(await cursor.fetchone(), "raw_json")


async def list_collections(db_path: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT c.*, COUNT(i.id) AS count FROM library_collections c
               LEFT JOIN library_collection_items ci ON ci.collection_id=c.id
               LEFT JOIN library_items i ON i.id=ci.item_id AND i.deleted_at IS NULL
               GROUP BY c.id ORDER BY c.sort_order, c.name COLLATE NOCASE"""
        )
        return [_row_json(row, "raw_json") for row in await cursor.fetchall()]


async def update_collection(
    db_path: str, collection_id: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    fields = {key: payload[key] for key in ("name", "parent_id", "sort_order") if key in payload}
    if not fields:
        return next((value for value in await list_collections(db_path) if value["id"] == collection_id), None)
    fields["updated_at"] = _now()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        cursor = await db.execute(
            f"UPDATE library_collections SET {', '.join(key + '=?' for key in fields)} WHERE id=?",
            [*fields.values(), collection_id],
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return next((value for value in await list_collections(db_path) if value["id"] == collection_id), None)


async def delete_collection(db_path: str, collection_id: str) -> bool:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        await db.execute("DELETE FROM library_collection_items WHERE collection_id=?", (collection_id,))
        await db.execute("UPDATE library_collections SET parent_id=NULL WHERE parent_id=?", (collection_id,))
        cursor = await db.execute("DELETE FROM library_collections WHERE id=?", (collection_id,))
        await db.commit()
        return cursor.rowcount > 0


async def create_note(db_path: str, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    note_id = _id()
    now = _now()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO library_notes
               (id,item_id,provider,provider_library_id,provider_key,provider_version,title,content,author,raw_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                note_id,item_id,str(payload.get("provider") or "cyrene"),str(payload.get("provider_library_id") or ""),
                str(payload.get("provider_key") or ""),
                int(payload.get("provider_version") or 0),str(payload.get("title") or ""),
                str(payload.get("content") or ""),str(payload.get("author") or ""),
                _json(payload.get("raw_json"), {}),now,now,
            ),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM library_notes WHERE id=?", (note_id,))
        return _row_json(await cursor.fetchone(), "raw_json")


async def update_note(db_path: str, note_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    fields = {key: str(payload[key] or "") for key in ("title", "content", "author") if key in payload}
    fields["updated_at"] = _now()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"UPDATE library_notes SET {', '.join(key + '=?' for key in fields)} WHERE id=?",
            [*fields.values(), note_id],
        )
        if cursor.rowcount == 0:
            return None
        await db.commit()
        cursor = await db.execute("SELECT * FROM library_notes WHERE id=?", (note_id,))
        return _row_json(await cursor.fetchone(), "raw_json")


async def delete_note(db_path: str, note_id: str) -> bool:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        cursor = await db.execute("DELETE FROM library_notes WHERE id=?", (note_id,))
        await db.commit()
        return cursor.rowcount > 0


async def add_attachment(db_path: str, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    attachment_id = str(payload.get("id") or _id())
    now = _now()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO library_attachments
               (id,item_id,provider,provider_library_id,provider_key,provider_version,kb_document_id,title,filename,path,
                content_type,link_mode,content_hash,raw_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                attachment_id,item_id,str(payload.get("provider") or "cyrene"),str(payload.get("provider_library_id") or ""),
                str(payload.get("provider_key") or ""),
                int(payload.get("provider_version") or 0),payload.get("kb_document_id"),str(payload.get("title") or ""),
                str(payload.get("filename") or ""),str(payload.get("path") or ""),
                str(payload.get("content_type") or ""),str(payload.get("link_mode") or ""),
                str(payload.get("content_hash") or ""),_json(payload.get("raw_json"), {}),now,now,
            ),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM library_attachments WHERE id=?", (attachment_id,))
        return _row_json(await cursor.fetchone(), "raw_json")


async def upsert_attachment(db_path: str, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    provider = str(payload.get("provider") or "zotero")
    library_id = str(payload.get("provider_library_id") or "")
    key = str(payload.get("provider_key") or "")
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id FROM library_attachments WHERE provider=? AND provider_library_id=? AND provider_key=?",
            (provider, library_id, key),
        )
        row = await cursor.fetchone()
    if not row:
        return await add_attachment(db_path, item_id, payload)
    attachment_id = row["id"]
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """UPDATE library_attachments SET item_id=?,provider_version=?,kb_document_id=COALESCE(?,kb_document_id),
               title=?,filename=?,path=?,content_type=?,link_mode=?,content_hash=?,raw_json=?,updated_at=? WHERE id=?""",
            (
                item_id,int(payload.get("provider_version") or 0),payload.get("kb_document_id"),
                str(payload.get("title") or ""),str(payload.get("filename") or ""),str(payload.get("path") or ""),
                str(payload.get("content_type") or ""),str(payload.get("link_mode") or ""),
                str(payload.get("content_hash") or ""),_json(payload.get("raw_json"), {}),_now(),attachment_id,
            ),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM library_attachments WHERE id=?", (attachment_id,))
        return _row_json(await cursor.fetchone(), "raw_json")


async def add_annotation(db_path: str, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    annotation_id = _id()
    now = _now()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO library_annotations
               (id,item_id,attachment_id,provider,provider_library_id,provider_key,provider_version,annotation_type,
                page_label,quote,comment,color,position_json,raw_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                annotation_id,item_id,payload.get("attachment_id"),str(payload.get("provider") or "cyrene"),
                str(payload.get("provider_library_id") or ""),str(payload.get("provider_key") or ""),
                int(payload.get("provider_version") or 0),
                str(payload.get("annotation_type") or "highlight"),str(payload.get("page_label") or ""),
                str(payload.get("quote") or ""),str(payload.get("comment") or ""),str(payload.get("color") or ""),
                _json(payload.get("position_json"), {}),_json(payload.get("raw_json"), {}),now,now,
            ),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM library_annotations WHERE id=?", (annotation_id,))
        return _row_json(await cursor.fetchone(), "position_json", "raw_json")


async def create_relation(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    relation_id = _id()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO library_relations(id,src_item_id,dst_item_id,relation,source,note,created_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(src_item_id,dst_item_id,relation) DO UPDATE SET source=excluded.source,note=excluded.note""",
            (
                relation_id,str(payload.get("src_item_id") or ""),str(payload.get("dst_item_id") or ""),
                str(payload.get("relation") or "related"),str(payload.get("source") or "manual"),
                str(payload.get("note") or ""),_now(),
            ),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT * FROM library_relations WHERE src_item_id=? AND dst_item_id=? AND relation=?",
            (payload.get("src_item_id"),payload.get("dst_item_id"),str(payload.get("relation") or "related")),
        )
        return dict(await cursor.fetchone())


async def delete_relation(db_path: str, relation_id: str) -> bool:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        cursor = await db.execute("DELETE FROM library_relations WHERE id=?", (relation_id,))
        await db.commit()
        return cursor.rowcount > 0


async def list_tags(db_path: str) -> list[dict[str, Any]]:
    await sync_knowledge_documents(db_path)
    async with aiosqlite.connect(db_path, timeout=30) as db:
        cursor = await db.execute("SELECT tags FROM library_items WHERE deleted_at IS NULL")
        counts: dict[str, int] = {}
        spelling: dict[str, str] = {}
        for row in await cursor.fetchall():
            for tag in _loads(row[0], []):
                folded = str(tag).casefold()
                spelling.setdefault(folded, str(tag))
                counts[folded] = counts.get(folded, 0) + 1
        return [
            {"name": spelling[key], "count": count}
            for key, count in sorted(counts.items(), key=lambda value: (-value[1], spelling[value[0]].casefold()))
        ]


async def get_stats(db_path: str) -> dict[str, Any]:
    await sync_knowledge_documents(db_path)
    recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        async def scalar(sql: str, params: tuple[Any, ...] = ()) -> int:
            cursor = await db.execute(sql, params)
            row = await cursor.fetchone()
            return int(row[0] or 0) if row else 0
        total = await scalar("SELECT COUNT(*) FROM library_items WHERE deleted_at IS NULL")
        cursor = await db.execute(
            "SELECT reading_status,COUNT(*) FROM library_items WHERE deleted_at IS NULL GROUP BY reading_status"
        )
        statuses = {str(row[0]): int(row[1]) for row in await cursor.fetchall()}
        return {
            "total": total,
            "unclassified": await scalar(
                """SELECT COUNT(*) FROM library_items i WHERE i.deleted_at IS NULL AND NOT EXISTS
                   (SELECT 1 FROM library_collection_items ci WHERE ci.item_id=i.id)"""
            ),
            "recent_added": await scalar(
                "SELECT COUNT(*) FROM library_items WHERE deleted_at IS NULL AND created_at>=?", (recent,)
            ),
            "recent_read": await scalar(
                "SELECT COUNT(*) FROM library_items WHERE deleted_at IS NULL AND last_read_at>=?", (recent,)
            ),
            "starred": await scalar("SELECT COUNT(*) FROM library_items WHERE deleted_at IS NULL AND starred=1"),
            "trash": await scalar("SELECT COUNT(*) FROM library_items WHERE deleted_at IS NOT NULL"),
            "statuses": statuses,
            "attachments": await scalar("SELECT COUNT(*) FROM library_attachments"),
            "notes": await scalar("SELECT COUNT(*) FROM library_notes"),
            "annotations": await scalar("SELECT COUNT(*) FROM library_annotations"),
        }


async def set_sync_state(
    db_path: str,
    *,
    provider: str,
    library_id: str,
    collection_key: str,
    name: str = "",
    version: int = 0,
    error: str = "",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO library_sync_sources
               (id,provider,provider_library_id,collection_key,name,last_library_version,last_synced_at,
                last_error,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(provider,provider_library_id,collection_key) DO UPDATE SET
                name=excluded.name,last_library_version=excluded.last_library_version,
                last_synced_at=excluded.last_synced_at,last_error=excluded.last_error,
                config_json=excluded.config_json,updated_at=excluded.updated_at""",
            (_id(),provider,library_id,collection_key,name,version,now,error,_json(config, {}),now,now),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT * FROM library_sync_sources WHERE provider=? AND provider_library_id=? AND collection_key=?",
            (provider, library_id, collection_key),
        )
        return _row_json(await cursor.fetchone(), "config_json")


async def get_sync_state(db_path: str, provider: str = "zotero") -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM library_sync_sources WHERE provider=? ORDER BY updated_at DESC", (provider,)
        )
        return [_row_json(row, "config_json") for row in await cursor.fetchall()]


def zotero_to_item(raw: dict[str, Any], library_id: str = "0") -> dict[str, Any]:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    date_text = str(data.get("date") or "")
    year_match = re.search(r"(?<!\d)(1[5-9]\d{2}|20\d{2}|21\d{2})(?!\d)", date_text)
    creators = _normalized_creators(data.get("creators"))
    title = str(data.get("title") or data.get("shortTitle") or "")
    venue = str(
        data.get("publicationTitle") or data.get("conferenceName")
        or data.get("proceedingsTitle") or data.get("university") or ""
    )
    item = {
        "provider": "zotero",
        # Keep the requested API library identifier (``0`` is Zotero Local
        # API's stable current-user alias).  Mixing in response.library.id
        # would make parent items disagree with their child/sync records.
        "provider_library_id": str(library_id),
        "provider_item_key": str(raw.get("key") or data.get("key") or ""),
        "provider_version": int(raw.get("version") or data.get("version") or 0),
        "item_type": str(data.get("itemType") or "journalArticle"),
        "title": title,
        "abstract": str(data.get("abstractNote") or ""),
        "doi": str(data.get("DOI") or ""),
        "isbn": str(data.get("ISBN") or ""),
        "url": str(data.get("url") or ""),
        "venue": venue,
        "publisher": str(data.get("publisher") or ""),
        "volume": str(data.get("volume") or ""),
        "issue": str(data.get("issue") or ""),
        "pages": str(data.get("pages") or ""),
        "language": str(data.get("language") or ""),
        "year": int(year_match.group(1)) if year_match else None,
        "date_text": date_text,
        "citekey": str(data.get("citationKey") or ""),
        "tags": _clean_tags(data.get("tags")),
        "creators": creators,
        "raw_json": raw,
        "synced_at": _now(),
    }
    item["csl_json"] = {
        "id": item["citekey"] or item["provider_item_key"],
        "type": "article-journal" if item["item_type"] == "journalArticle" else item["item_type"],
        "title": title,
        "author": [
            {"given": creator["first_name"], "family": creator["last_name"], "literal": creator["name"]}
            for creator in creators
        ],
        "issued": {"date-parts": [[item["year"]]]} if item["year"] else {},
        "container-title": venue,
        "volume": item["volume"], "issue": item["issue"], "page": item["pages"],
        "DOI": item["doi"], "URL": item["url"],
    }
    return item


async def upsert_zotero_item(
    db_path: str, raw: dict[str, Any], library_id: str = "0"
) -> tuple[dict[str, Any], bool]:
    payload = zotero_to_item(raw, library_id)
    existing = await get_item_by_provider_key(
        db_path, "zotero", payload["provider_library_id"], payload["provider_item_key"]
    )
    if not existing:
        return await create_item(db_path, payload), True
    if existing.get("deleted_at"):
        # The user trashed this item; a Zotero re-sync must not resurrect it.
        return existing, False
    editable = {key: payload[key] for key in ITEM_FIELDS if key in payload}
    editable["creators"] = payload["creators"]
    # An empty source value must not clobber a non-empty local value: abstract
    # and tags (among others) may have been set by the user in the editor while
    # Zotero reports them as empty. Non-empty source values stay authoritative.
    for key, value in list(editable.items()):
        if key == "creators":
            empty = not value and bool(existing.get("creators"))
        else:
            empty = value in (None, "", [], {}) and str(existing.get(key) or "")
        if empty:
            del editable[key]
    async with aiosqlite.connect(db_path, timeout=30) as db:
        await db.execute(
            """UPDATE library_items SET provider_version=?,raw_json=?,synced_at=?
               WHERE id=?""",
            (payload["provider_version"],_json(raw, {}),_now(),existing["id"]),
        )
        await db.commit()
    return await update_item(db_path, existing["id"], editable) or existing, False


def render_citation(item: dict[str, Any], style: str = "ieee") -> str:
    creators = item.get("creators") or []
    names = []
    for creator in creators:
        first = str(creator.get("first_name") or "").strip()
        last = str(creator.get("last_name") or "").strip()
        literal = str(creator.get("name") or "").strip()
        names.append(literal or " ".join(part for part in (first[:1] + "." if first else "", last) if part))
    author_text = ", ".join(names) or "Unknown author"
    title = str(item.get("title") or "Untitled")
    venue = str(item.get("venue") or "")
    year = str(item.get("year") or item.get("date_text") or "n.d.")
    doi = str(item.get("doi") or "")
    if style.lower() in {"apa", "apa7"}:
        return f"{author_text} ({year}). {title}. {venue}." + (f" https://doi.org/{doi}" if doi else "")
    parts = [f'{author_text}, “{title},”']
    if venue:
        parts.append(f" {venue},")
    parts.append(f" {year}.")
    if doi:
        parts.append(f" doi: {doi}.")
    return "".join(parts)


def render_bibtex(item: dict[str, Any]) -> str:
    """Render a portable BibTeX entry from a hydrated library item."""
    item_type = str(item.get("item_type") or "")
    entry_type = {
        "journalArticle": "article",
        "conferencePaper": "inproceedings",
        "book": "book",
        "bookSection": "incollection",
        "thesis": "phdthesis",
        "report": "techreport",
    }.get(item_type, "misc")
    raw_key = str(
        item.get("citekey") or item.get("provider_item_key") or item.get("id") or "cyrene"
    )
    citekey = re.sub(r"[^0-9A-Za-z_.:+-]+", "-", raw_key).strip("-") or "cyrene"

    def escaped(value: Any) -> str:
        return (
            str(value or "")
            .replace("\\", r"\textbackslash{}")
            .replace("{", r"\{")
            .replace("}", r"\}")
            .strip()
        )

    creators = []
    for creator in item.get("creators") or []:
        literal = str(creator.get("name") or "").strip()
        first = str(creator.get("first_name") or "").strip()
        last = str(creator.get("last_name") or "").strip()
        name = literal or " ".join(part for part in (first, last) if part)
        if name:
            creators.append(name)

    venue_field = "journal" if entry_type == "article" else (
        "booktitle" if entry_type in {"inproceedings", "incollection"} else "howpublished"
    )
    fields = [
        ("title", item.get("title")),
        ("author", " and ".join(creators)),
        ("year", item.get("year") or item.get("date_text")),
        (venue_field, item.get("venue")),
        ("publisher", item.get("publisher")),
        ("volume", item.get("volume")),
        ("number", item.get("issue")),
        ("pages", item.get("pages")),
        ("doi", item.get("doi")),
        ("isbn", item.get("isbn")),
        ("url", item.get("url")),
        ("language", item.get("language")),
        ("abstract", item.get("abstract")),
    ]
    lines = [
        f"  {name} = {{{escaped(value)}}}"
        for name, value in fields
        if str(value or "").strip()
    ]
    return f"@{entry_type}{{{citekey},\n" + ",\n".join(lines) + "\n}"


__all__ = [
    "sync_knowledge_documents", "create_item", "get_item", "get_item_by_provider_key",
    "update_item", "list_items",
    "delete_item", "delete_items", "restore_item", "create_collection", "upsert_collection", "list_collections",
    "update_collection", "delete_collection", "create_note", "update_note", "delete_note",
    "add_attachment", "upsert_attachment", "add_annotation", "create_relation", "delete_relation",
    "list_tags", "get_stats", "set_sync_state", "get_sync_state", "zotero_to_item",
    "upsert_zotero_item", "render_citation", "render_bibtex",
]
