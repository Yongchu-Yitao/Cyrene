"""Plugin-owned SQLite repository for project knowledge and literature."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote
from uuid import uuid4

from .content import split_text


_ITEM_FIELDS = (
    "item_type",
    "title",
    "abstract",
    "doi",
    "isbn",
    "url",
    "venue",
    "publisher",
    "volume",
    "issue",
    "pages",
    "language",
    "year",
    "date_text",
    "citekey",
    "reading_status",
    "starred",
    "provider",
    "provider_library_id",
    "provider_item_key",
    "provider_version",
    "content",
)
_SORT_FIELDS = {
    "added_at": "created_at",
    "created_at": "created_at",
    "title": "title COLLATE NOCASE",
    "updated_at": "updated_at",
    "year": "COALESCE(year, 0)",
}
_SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    workspace TEXT NOT NULL,
    item_type TEXT NOT NULL DEFAULT 'document',
    title TEXT NOT NULL,
    abstract TEXT NOT NULL DEFAULT '',
    doi TEXT NOT NULL DEFAULT '',
    isbn TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    volume TEXT NOT NULL DEFAULT '',
    issue TEXT NOT NULL DEFAULT '',
    pages TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    year INTEGER,
    date_text TEXT NOT NULL DEFAULT '',
    citekey TEXT NOT NULL DEFAULT '',
    reading_status TEXT NOT NULL DEFAULT 'unread',
    starred INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL DEFAULT 'cyrene',
    provider_library_id TEXT NOT NULL DEFAULT '',
    provider_item_key TEXT NOT NULL DEFAULT '',
    provider_version INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_read_at TEXT,
    deleted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS item_provider_key
    ON items(workspace, provider, provider_library_id, provider_item_key)
    WHERE provider_item_key <> '';
CREATE INDEX IF NOT EXISTS item_workspace_updated ON items(workspace, updated_at DESC);

CREATE TABLE IF NOT EXISTS creators (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    creator_type TEXT NOT NULL DEFAULT 'author',
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS creator_item ON creators(item_id, ordinal);

CREATE TABLE IF NOT EXISTS tags (
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY(item_id, tag)
);

CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    workspace TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
    color TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'cyrene',
    provider_library_id TEXT NOT NULL DEFAULT '',
    provider_key TEXT NOT NULL DEFAULT '',
    provider_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS collection_provider_key
    ON collections(workspace, provider, provider_library_id, provider_key)
    WHERE provider_key <> '';

CREATE TABLE IF NOT EXISTS collection_items (
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY(collection_id, item_id)
);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    workspace TEXT NOT NULL,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    file_type TEXT NOT NULL DEFAULT 'other',
    size INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    indexed_text TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'cyrene',
    provider_library_id TEXT NOT NULL DEFAULT '',
    provider_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attachment_item ON attachments(item_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS attachment_provider_key
    ON attachments(item_id, provider, provider_library_id, provider_key)
    WHERE provider_key <> '';

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    workspace TEXT NOT NULL,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    attachment_id TEXT REFERENCES attachments(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    content TEXT NOT NULL,
    vector_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS chunk_workspace_item ON chunks(workspace, item_id, ordinal);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'cyrene',
    provider_library_id TEXT NOT NULL DEFAULT '',
    provider_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS note_provider_key
    ON notes(item_id, provider, provider_library_id, provider_key)
    WHERE provider_key <> '';

CREATE TABLE IF NOT EXISTS annotations (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    attachment_id TEXT REFERENCES attachments(id) ON DELETE CASCADE,
    annotation_type TEXT NOT NULL DEFAULT 'highlight',
    page_label TEXT NOT NULL DEFAULT '',
    quote TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'cyrene',
    provider_library_id TEXT NOT NULL DEFAULT '',
    provider_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS annotation_provider_key
    ON annotations(item_id, provider, provider_library_id, provider_key)
    WHERE provider_key <> '';

CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY,
    workspace TEXT NOT NULL,
    src_item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    dst_item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    relation TEXT NOT NULL DEFAULT 'related',
    source TEXT NOT NULL DEFAULT 'cyrene',
    created_at TEXT NOT NULL,
    UNIQUE(workspace, src_item_id, dst_item_id, relation)
);

CREATE TABLE IF NOT EXISTS sync_state (
    workspace TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_library_id TEXT NOT NULL,
    collection_key TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL DEFAULT '{}',
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace, provider, provider_library_id, collection_key)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _clean_tags(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[,;；\n]", str(value or ""))
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        label = _clean_string(item.get("tag") if isinstance(item, Mapping) else item)
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            result.append(label)
    return result


def _normalized_creators(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        value = re.split(r"[;；\n]", value)
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for raw in value:
        if isinstance(raw, str):
            item = {"name": raw}
        elif isinstance(raw, Mapping):
            item = dict(raw)
        else:
            continue
        normalized = {
            "creator_type": _clean_string(item.get("creator_type") or item.get("creatorType") or "author"),
            "first_name": _clean_string(item.get("first_name") or item.get("firstName")),
            "last_name": _clean_string(item.get("last_name") or item.get("lastName")),
            "name": _clean_string(item.get("name") or item.get("literal")),
        }
        if normalized["name"] or normalized["first_name"] or normalized["last_name"]:
            result.append(normalized)
    return result


def file_type(filename: str, content_type: str) -> str:
    name = str(filename or "").casefold()
    media = str(content_type or "").casefold().split(";", 1)[0]
    if media == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if media.startswith("image/") or re.search(r"\.(avif|bmp|gif|jpe?g|png|webp)$", name):
        return "image"
    if media.startswith("audio/") or re.search(r"\.(aac|flac|m4a|mp3|oga|ogg|wav|weba)$", name):
        return "audio"
    if media.startswith("video/") or re.search(r"\.(m4v|mov|mp4|ogv|webm)$", name):
        return "video"
    if re.search(r"spreadsheet|ms-excel|text/csv|tab-separated", media) or re.search(r"\.(csv|numbers|tsv|xls|xlsm|xlsx)$", name):
        return "spreadsheet"
    if re.search(r"powerpoint|presentation", media) or re.search(r"\.(key|odp|ppt|pptx)$", name):
        return "presentation"
    if media == "text/uri-list" or re.search(r"\.(link|url|webloc)$", name):
        return "link"
    if media.startswith("text/") or re.search(r"\.(doc|docx|html?|json|log|md|rtf|tex|txt|xml|ya?ml)$", name):
        return "document"
    return "other"


def vectorize(text: str, dimensions: int = 128) -> list[float]:
    tokens = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", str(text or "").casefold())
    values = [0.0] * dimensions
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        values[index] += -1.0 if digest[4] & 1 else 1.0
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values] if norm else values


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right)) if left and right else 0.0


class KnowledgeStore:
    """One new database shared by all workspaces and owned by the Plugin."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.db_path = self.root / "knowledge.sqlite3"
        self.files_root = self.root / "files"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_root.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_root.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            connection.executescript(_SCHEMA)

    @staticmethod
    def _workspace_directory_name(workspace: str) -> str:
        label = re.sub(r"[^A-Za-z0-9_.-]+", "_", workspace).strip("._")[:48] or "workspace"
        digest = hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:12]
        return f"{label}-{digest}"

    def workspace_files(self, workspace: str) -> Path:
        target = self.files_root / self._workspace_directory_name(workspace)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def managed_path(self, workspace: str, filename: str) -> Path:
        clean = re.sub(r"[^\w.()\- ]+", "_", Path(filename).name, flags=re.UNICODE).strip()
        clean = clean[:160] or "attachment"
        return self.workspace_files(workspace) / f"{uuid4().hex}_{clean}"

    @staticmethod
    def _item_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["starred"] = bool(item.get("starred"))
        item["added_at"] = item.get("created_at")
        return item

    def _hydrate(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        detail: bool,
    ) -> dict[str, Any]:
        item = self._item_from_row(row)
        item_id = str(item["id"])
        item["creators"] = [
            dict(value)
            for value in connection.execute(
                "SELECT creator_type,first_name,last_name,name FROM creators WHERE item_id=? ORDER BY ordinal",
                (item_id,),
            ).fetchall()
        ]
        item["tags"] = [
            str(value[0])
            for value in connection.execute(
                "SELECT tag FROM tags WHERE item_id=? ORDER BY tag COLLATE NOCASE",
                (item_id,),
            ).fetchall()
        ]
        item["collections"] = [
            dict(value)
            for value in connection.execute(
                "SELECT c.id,c.name,c.color FROM collections c JOIN collection_items ci ON ci.collection_id=c.id WHERE ci.item_id=? ORDER BY c.name COLLATE NOCASE",
                (item_id,),
            ).fetchall()
        ]
        attachments = [
            dict(value)
            for value in connection.execute(
                "SELECT id,filename,path,content_type,file_type,size,page_count,content_hash,created_at,updated_at FROM attachments WHERE item_id=? ORDER BY created_at",
                (item_id,),
            ).fetchall()
        ]
        for attachment in attachments:
            attachment["raw_url"] = (
                f"/api/workbench/library/items/{quote(item_id, safe='')}/attachments/{quote(str(attachment['id']), safe='')}/raw?workspace={quote(str(item['workspace']), safe='')}"
            )
        item["attachments"] = attachments
        item["attachment_count"] = len(attachments)
        primary = next(
            (value for value in attachments if value.get("content_type") == "application/pdf"),
            attachments[0] if attachments else None,
        )
        if primary:
            item["attachment_name"] = primary.get("filename") or ""
            item["filename"] = primary.get("filename") or ""
            item["attachment_size"] = int(primary.get("size") or 0)
            item["content_type"] = primary.get("content_type") or ""
        counts = connection.execute(
            "SELECT COUNT(*),SUM(CASE WHEN vector_json<>'[]' THEN 1 ELSE 0 END) FROM chunks WHERE item_id=?",
            (item_id,),
        ).fetchone()
        total_chunks = int(counts[0] or 0)
        compatible_chunks = int(counts[1] or 0)
        state = "none" if total_chunks == 0 else "complete" if compatible_chunks == total_chunks else "partial"
        item["embedding_status"] = {
            "state": state,
            "total_chunks": total_chunks,
            "compatible_chunks": compatible_chunks,
        }
        if detail:
            item["notes"] = [
                dict(value)
                for value in connection.execute(
                    "SELECT id,title,content,author,provider,created_at,updated_at FROM notes WHERE item_id=? ORDER BY updated_at DESC",
                    (item_id,),
                ).fetchall()
            ]
            item["annotations"] = [
                dict(value)
                for value in connection.execute(
                    "SELECT id,attachment_id,annotation_type,page_label,quote,comment,color,created_at,updated_at FROM annotations WHERE item_id=? ORDER BY created_at",
                    (item_id,),
                ).fetchall()
            ]
            relations = [
                dict(value)
                for value in connection.execute(
                    "SELECT r.id,r.src_item_id,r.dst_item_id,r.relation,r.source,r.created_at,"
                    "CASE WHEN r.src_item_id=? THEN dst.title ELSE src.title END AS title "
                    "FROM relations r JOIN items src ON src.id=r.src_item_id "
                    "JOIN items dst ON dst.id=r.dst_item_id "
                    "WHERE r.src_item_id=? OR r.dst_item_id=? ORDER BY r.created_at DESC",
                    (item_id, item_id, item_id),
                ).fetchall()
            ]
            for relation in relations:
                relation["relation_type"] = relation.get("relation") or "related"
                relation["type"] = relation["relation_type"]
            item["relations"] = relations
            texts = [
                str(value[0] or "")
                for value in connection.execute(
                    "SELECT indexed_text FROM attachments WHERE item_id=? ORDER BY created_at",
                    (item_id,),
                ).fetchall()
                if str(value[0] or "")
            ]
            item["indexed_text"] = "\n\n".join(texts) or str(item.get("content") or "")
        return item

    def _replace_creators(self, connection: sqlite3.Connection, item_id: str, creators: Any) -> None:
        connection.execute("DELETE FROM creators WHERE item_id=?", (item_id,))
        for ordinal, creator in enumerate(_normalized_creators(creators)):
            connection.execute(
                "INSERT INTO creators(id,item_id,ordinal,creator_type,first_name,last_name,name) VALUES(?,?,?,?,?,?,?)",
                (
                    new_id("creator"),
                    item_id,
                    ordinal,
                    creator["creator_type"],
                    creator["first_name"],
                    creator["last_name"],
                    creator["name"],
                ),
            )

    def _replace_tags(self, connection: sqlite3.Connection, item_id: str, tags: Any) -> None:
        connection.execute("DELETE FROM tags WHERE item_id=?", (item_id,))
        connection.executemany(
            "INSERT INTO tags(item_id,tag) VALUES(?,?)",
            [(item_id, value) for value in _clean_tags(tags)],
        )

    def _replace_collections(
        self,
        connection: sqlite3.Connection,
        workspace: str,
        item_id: str,
        collection_ids: Any,
    ) -> None:
        if not isinstance(collection_ids, list):
            collection_ids = []
        ids = list(dict.fromkeys(_clean_string(value) for value in collection_ids if _clean_string(value)))
        if ids:
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"SELECT id FROM collections WHERE workspace=? AND id IN ({placeholders})",
                (workspace, *ids),
            ).fetchall()
            ids = [str(row[0]) for row in rows]
        connection.execute("DELETE FROM collection_items WHERE item_id=?", (item_id,))
        now = utc_now()
        connection.executemany(
            "INSERT INTO collection_items(collection_id,item_id,created_at) VALUES(?,?,?)",
            [(collection_id, item_id, now) for collection_id in ids],
        )

    def _replace_item_chunks(
        self,
        connection: sqlite3.Connection,
        workspace: str,
        item_id: str,
        text: str,
    ) -> None:
        connection.execute("DELETE FROM chunks WHERE item_id=? AND attachment_id IS NULL", (item_id,))
        for ordinal, chunk in enumerate(split_text(text)):
            connection.execute(
                "INSERT INTO chunks(id,workspace,item_id,attachment_id,ordinal,content,vector_json) VALUES(?,?,?,?,?,?,?)",
                (
                    new_id("chunk"),
                    workspace,
                    item_id,
                    None,
                    ordinal,
                    chunk,
                    json.dumps(vectorize(chunk), separators=(",", ":")),
                ),
            )

    def create_item(self, workspace: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        title = _clean_string(payload.get("title"))
        if not title:
            raise ValueError("title is required")
        item_id = _clean_string(payload.get("id")) or new_id("paper")
        now = utc_now()
        values: dict[str, Any] = {
            "item_type": _clean_string(payload.get("item_type")) or "document",
            "title": title,
            "abstract": _clean_string(payload.get("abstract")),
            "doi": _clean_string(payload.get("doi")),
            "isbn": _clean_string(payload.get("isbn")),
            "url": _clean_string(payload.get("url")),
            "venue": _clean_string(payload.get("venue") or payload.get("publication_title")),
            "publisher": _clean_string(payload.get("publisher")),
            "volume": _clean_string(payload.get("volume")),
            "issue": _clean_string(payload.get("issue")),
            "pages": _clean_string(payload.get("pages")),
            "language": _clean_string(payload.get("language")),
            "year": int(payload["year"]) if str(payload.get("year") or "").isdigit() else None,
            "date_text": _clean_string(payload.get("date_text")),
            "citekey": _clean_string(payload.get("citekey")),
            "reading_status": _clean_string(payload.get("reading_status")) or "unread",
            "starred": int(bool(payload.get("starred"))),
            "provider": _clean_string(payload.get("provider")) or "cyrene",
            "provider_library_id": _clean_string(payload.get("provider_library_id")),
            "provider_item_key": _clean_string(payload.get("provider_item_key")),
            "provider_version": int(payload.get("provider_version") or 0),
            "content": str(payload.get("content") or ""),
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO items(id,workspace," + ",".join(_ITEM_FIELDS) + ",created_at,updated_at) VALUES(" + ",".join("?" for _ in range(len(_ITEM_FIELDS) + 4)) + ")",
                (item_id, workspace, *(values[field] for field in _ITEM_FIELDS), now, now),
            )
            self._replace_creators(connection, item_id, payload.get("creators") or payload.get("authors"))
            self._replace_tags(connection, item_id, payload.get("tags"))
            self._replace_collections(connection, workspace, item_id, payload.get("collection_ids"))
            self._replace_item_chunks(
                connection,
                workspace,
                item_id,
                "\n\n".join(value for value in (values["title"], values["abstract"], values["content"]) if value),
            )
            row = connection.execute("SELECT * FROM items WHERE workspace=? AND id=?", (workspace, item_id)).fetchone()
            return self._hydrate(connection, row, detail=True)

    def upsert_provider_item(self, workspace: str, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        provider = _clean_string(payload.get("provider"))
        library_id = _clean_string(payload.get("provider_library_id"))
        key = _clean_string(payload.get("provider_item_key"))
        if not provider or not key:
            return self.create_item(workspace, payload), True
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT id,deleted_at FROM items WHERE workspace=? AND provider=? AND provider_library_id=? AND provider_item_key=?",
                (workspace, provider, library_id, key),
            ).fetchone()
        if row:
            value = self.update_item(workspace, str(row[0]), payload)
            if value is None:
                raise RuntimeError("provider item disappeared while updating")
            if row["deleted_at"] and not bool(payload.get("_deleted")):
                value = self.restore_item(workspace, str(row[0])) or value
            return value, False
        return self.create_item(workspace, payload), True

    def get_item(self, workspace: str, item_id: str, *, detail: bool = True) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM items WHERE workspace=? AND id=?", (workspace, item_id)).fetchone()
            return self._hydrate(connection, row, detail=detail) if row else None

    def update_item(self, workspace: str, item_id: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            existing = connection.execute("SELECT * FROM items WHERE workspace=? AND id=?", (workspace, item_id)).fetchone()
            if not existing:
                return None
            assignments: list[str] = []
            values: list[Any] = []
            for field in _ITEM_FIELDS:
                if field not in payload:
                    continue
                value: Any = payload[field]
                if field == "starred":
                    value = int(bool(value))
                elif field in {"year", "provider_version"}:
                    value = int(value) if str(value or "").isdigit() else (None if field == "year" else 0)
                elif field != "content":
                    value = _clean_string(value)
                assignments.append(f"{field}=?")
                values.append(value)
            if assignments:
                if payload.get("reading_status") == "read":
                    assignments.append("last_read_at=?")
                    values.append(utc_now())
                assignments.append("updated_at=?")
                values.append(utc_now())
                connection.execute(
                    f"UPDATE items SET {','.join(assignments)} WHERE workspace=? AND id=?",
                    (*values, workspace, item_id),
                )
            if "creators" in payload or "authors" in payload:
                self._replace_creators(connection, item_id, payload.get("creators") or payload.get("authors"))
            if "tags" in payload:
                self._replace_tags(connection, item_id, payload.get("tags"))
            if "collection_ids" in payload:
                self._replace_collections(connection, workspace, item_id, payload.get("collection_ids"))
            if any(field in payload for field in ("title", "abstract", "content")):
                refreshed = connection.execute("SELECT title,abstract,content FROM items WHERE id=?", (item_id,)).fetchone()
                self._replace_item_chunks(
                    connection,
                    workspace,
                    item_id,
                    "\n\n".join(str(value or "") for value in refreshed if value),
                )
            row = connection.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return self._hydrate(connection, row, detail=True)

    def list_items(
        self,
        workspace: str,
        *,
        q: str = "",
        collection: str = "",
        status: str = "",
        tag: str = "",
        item_type: str = "",
        file_type_filter: str = "",
        year: int | None = None,
        starred: bool | None = None,
        trash: bool = False,
        sort: str = "updated_at",
        order: str = "desc",
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["i.workspace=?", "i.deleted_at IS NOT NULL" if trash else "i.deleted_at IS NULL"]
        values: list[Any] = [workspace]
        query = _clean_string(q)
        if query:
            needle = f"%{query.casefold()}%"
            where.append(
                "(LOWER(i.title) LIKE ? OR LOWER(i.abstract) LIKE ? OR LOWER(i.doi) LIKE ? "
                "OR LOWER(i.venue) LIKE ? OR EXISTS(SELECT 1 FROM creators c WHERE c.item_id=i.id "
                "AND LOWER(c.first_name||' '||c.last_name||' '||c.name) LIKE ?) "
                "OR EXISTS(SELECT 1 FROM attachments a WHERE a.item_id=i.id AND LOWER(a.indexed_text) LIKE ?))"
            )
            values.extend([needle] * 6)
        if collection == "__unclassified__":
            where.append("NOT EXISTS(SELECT 1 FROM collection_items ci WHERE ci.item_id=i.id)")
        elif collection:
            where.append("EXISTS(SELECT 1 FROM collection_items ci WHERE ci.item_id=i.id AND ci.collection_id=?)")
            values.append(collection)
        if status == "recent_added":
            where.append("i.created_at >= datetime('now','-30 days')")
        elif status == "recent_read":
            where.append("i.last_read_at IS NOT NULL")
        elif status:
            where.append("i.reading_status=?")
            values.append(status)
        if tag:
            where.append("EXISTS(SELECT 1 FROM tags t WHERE t.item_id=i.id AND t.tag=?)")
            values.append(tag)
        if item_type:
            where.append("i.item_type=?")
            values.append(item_type)
        if file_type_filter:
            where.append("EXISTS(SELECT 1 FROM attachments a WHERE a.item_id=i.id AND a.file_type=?)")
            values.append(file_type_filter)
        if year is not None:
            where.append("i.year=?")
            values.append(int(year))
        if starred is not None:
            where.append("i.starred=?")
            values.append(int(starred))
        clause = " AND ".join(where)
        sort_expression = _SORT_FIELDS.get(sort, _SORT_FIELDS["updated_at"])
        direction = "ASC" if str(order).casefold() == "asc" else "DESC"
        bounded_limit = max(1, min(int(limit or 200), 1000))
        bounded_offset = max(0, int(offset or 0))
        with self._lock, self._connect() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) FROM items i WHERE {clause}", values).fetchone()[0])
            rows = connection.execute(
                f"SELECT i.* FROM items i WHERE {clause} ORDER BY {sort_expression} {direction},i.id LIMIT ? OFFSET ?",
                (*values, bounded_limit, bounded_offset),
            ).fetchall()
            return {
                "items": [self._hydrate(connection, row, detail=False) for row in rows],
                "total": total,
            }

    def delete_items(self, workspace: str, item_ids: Iterable[str], *, permanent: bool) -> int:
        ids = list(dict.fromkeys(_clean_string(value) for value in item_ids if _clean_string(value)))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connect() as connection:
            if not permanent:
                cursor = connection.execute(
                    f"UPDATE items SET deleted_at=?,updated_at=? WHERE workspace=? AND id IN ({placeholders}) AND deleted_at IS NULL",
                    (utc_now(), utc_now(), workspace, *ids),
                )
                return int(cursor.rowcount)
            paths = [
                Path(str(row[0]))
                for row in connection.execute(
                    f"SELECT a.path FROM attachments a JOIN items i ON i.id=a.item_id WHERE i.workspace=? AND i.id IN ({placeholders})",
                    (workspace, *ids),
                ).fetchall()
            ]
            cursor = connection.execute(
                f"DELETE FROM items WHERE workspace=? AND id IN ({placeholders})",
                (workspace, *ids),
            )
            count = int(cursor.rowcount)
        for path in paths:
            try:
                if path.resolve().is_relative_to(self.files_root):
                    path.unlink(missing_ok=True)
            except (OSError, ValueError):
                continue
        return count

    def restore_item(self, workspace: str, item_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE items SET deleted_at=NULL,updated_at=? WHERE workspace=? AND id=?",
                (utc_now(), workspace, item_id),
            )
            if not cursor.rowcount:
                return None
            row = connection.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return self._hydrate(connection, row, detail=True)

    def create_collection(self, workspace: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        name = _clean_string(payload.get("name"))
        if not name:
            raise ValueError("name is required")
        collection_id = _clean_string(payload.get("id")) or new_id("collection")
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO collections(id,workspace,name,parent_id,color,provider,provider_library_id,"
                "provider_key,provider_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    collection_id,
                    workspace,
                    name,
                    _clean_string(payload.get("parent_id")) or None,
                    _clean_string(payload.get("color")),
                    _clean_string(payload.get("provider")) or "cyrene",
                    _clean_string(payload.get("provider_library_id")),
                    _clean_string(payload.get("provider_key")),
                    int(payload.get("provider_version") or 0),
                    now,
                    now,
                ),
            )
            return dict(connection.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone())

    def upsert_provider_collection(self, workspace: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        provider = _clean_string(payload.get("provider"))
        library_id = _clean_string(payload.get("provider_library_id"))
        key = _clean_string(payload.get("provider_key"))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM collections WHERE workspace=? AND provider=? AND provider_library_id=? AND provider_key=?",
                (workspace, provider, library_id, key),
            ).fetchone()
        if not row:
            return self.create_collection(workspace, payload)
        value = self.update_collection(workspace, str(row[0]), payload)
        if value is None:
            raise RuntimeError("provider collection disappeared while updating")
        return value

    def list_collections(self, workspace: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT c.*,COALESCE(SUM(CASE WHEN i.id IS NULL THEN 0 ELSE 1 END),0) "
                    "AS count "
                    "FROM collections c "
                    "LEFT JOIN collection_items ci ON ci.collection_id=c.id "
                    "LEFT JOIN items i ON i.id=ci.item_id AND i.deleted_at IS NULL "
                    "WHERE c.workspace=? GROUP BY c.id ORDER BY c.name COLLATE NOCASE",
                    (workspace,),
                ).fetchall()
            ]

    def update_collection(self, workspace: str, collection_id: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        allowed = ("name", "parent_id", "color", "provider_version")
        assignments, values = [], []
        for field in allowed:
            if field in payload:
                assignments.append(f"{field}=?")
                if field == "provider_version":
                    values.append(int(payload[field] or 0))
                elif field == "parent_id":
                    values.append(_clean_string(payload[field]) or None)
                else:
                    values.append(_clean_string(payload[field]))
        if not assignments:
            with self._lock, self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM collections WHERE workspace=? AND id=?",
                    (workspace, collection_id),
                ).fetchone()
                return dict(row) if row else None
        assignments.append("updated_at=?")
        values.append(utc_now())
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE collections SET {','.join(assignments)} WHERE workspace=? AND id=?",
                (*values, workspace, collection_id),
            )
            if not cursor.rowcount:
                return None
            return dict(connection.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone())

    def delete_collection(self, workspace: str, collection_id: str) -> bool:
        with self._lock, self._connect() as connection:
            return bool(
                connection.execute(
                    "DELETE FROM collections WHERE workspace=? AND id=?",
                    (workspace, collection_id),
                ).rowcount
            )

    def list_tags(self, workspace: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            return [
                {"name": str(row[0]), "tag": str(row[0]), "count": int(row[1])}
                for row in connection.execute(
                    "SELECT t.tag,COUNT(*) FROM tags t JOIN items i ON i.id=t.item_id "
                    "WHERE i.workspace=? AND i.deleted_at IS NULL GROUP BY t.tag "
                    "ORDER BY COUNT(*) DESC,t.tag COLLATE NOCASE",
                    (workspace,),
                ).fetchall()
            ]

    def stats(self, workspace: str) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN deleted_at IS NULL AND starred=1 THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN deleted_at IS NULL AND "
                "julianday(created_at)>=julianday('now','-30 days') THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN deleted_at IS NULL AND last_read_at IS NOT NULL "
                "THEN 1 ELSE 0 END) "
                "FROM items WHERE workspace=?",
                (workspace,),
            ).fetchone()
            unclassified = connection.execute(
                "SELECT COUNT(*) FROM items i WHERE i.workspace=? AND i.deleted_at IS NULL AND NOT EXISTS(SELECT 1 FROM collection_items ci WHERE ci.item_id=i.id)",
                (workspace,),
            ).fetchone()[0]
            return {
                "total": int(row[0] or 0),
                "starred": int(row[1] or 0),
                "trash": int(row[2] or 0),
                "recent_added": int(row[3] or 0),
                "recent_read": int(row[4] or 0),
                "unclassified": int(unclassified or 0),
            }

    def create_note(self, workspace: str, item_id: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        now = utc_now()
        note_id = _clean_string(payload.get("id")) or new_id("note")
        provider = _clean_string(payload.get("provider")) or "cyrene"
        library_id = _clean_string(payload.get("provider_library_id"))
        provider_key = _clean_string(payload.get("provider_key"))
        with self._lock, self._connect() as connection:
            if not connection.execute("SELECT 1 FROM items WHERE workspace=? AND id=?", (workspace, item_id)).fetchone():
                return None
            if provider_key:
                existing = connection.execute(
                    "SELECT id FROM notes WHERE item_id=? AND provider=? AND provider_library_id=? AND provider_key=?",
                    (item_id, provider, library_id, provider_key),
                ).fetchone()
                if existing:
                    note_id = str(existing[0])
                    connection.execute(
                        "UPDATE notes SET title=?,content=?,author=?,updated_at=? WHERE id=?",
                        (
                            _clean_string(payload.get("title")),
                            str(payload.get("content") or payload.get("text") or ""),
                            _clean_string(payload.get("author")),
                            now,
                            note_id,
                        ),
                    )
                    return dict(connection.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())
            connection.execute(
                "INSERT INTO notes(id,item_id,title,content,author,provider,provider_library_id,provider_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    note_id,
                    item_id,
                    _clean_string(payload.get("title")),
                    str(payload.get("content") or payload.get("text") or ""),
                    _clean_string(payload.get("author")),
                    provider,
                    library_id,
                    provider_key,
                    now,
                    now,
                ),
            )
            return dict(connection.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())

    def update_note(self, workspace: str, note_id: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        assignments, values = [], []
        for field in ("title", "content", "author"):
            if field in payload or (field == "content" and "text" in payload):
                assignments.append(f"{field}=?")
                values.append(str(payload.get("content") or payload.get("text") or "") if field == "content" else _clean_string(payload.get(field)))
        if not assignments:
            return None
        assignments.append("updated_at=?")
        values.append(utc_now())
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE notes SET {','.join(assignments)} WHERE id=? AND item_id IN(SELECT id FROM items WHERE workspace=?)",
                (*values, note_id, workspace),
            )
            if not cursor.rowcount:
                return None
            return dict(connection.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())

    def delete_note(self, workspace: str, note_id: str) -> bool:
        with self._lock, self._connect() as connection:
            return bool(
                connection.execute(
                    "DELETE FROM notes WHERE id=? AND item_id IN(SELECT id FROM items WHERE workspace=?)",
                    (note_id, workspace),
                ).rowcount
            )

    def create_relation(self, workspace: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        src = _clean_string(payload.get("src_item_id"))
        dst = _clean_string(payload.get("dst_item_id"))
        if not src or not dst or src == dst:
            raise ValueError("src_item_id and dst_item_id must be different items")
        relation = _clean_string(payload.get("relation")) or "related"
        now = utc_now()
        with self._lock, self._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM items WHERE workspace=? AND id IN (?,?)",
                (workspace, src, dst),
            ).fetchone()[0]
            if count != 2:
                raise LookupError("related item not found")
            existing = connection.execute(
                "SELECT * FROM relations WHERE workspace=? AND src_item_id=? AND dst_item_id=? AND relation=?",
                (workspace, src, dst, relation),
            ).fetchone()
            if existing:
                return dict(existing)
            relation_id = new_id("relation")
            connection.execute(
                "INSERT INTO relations(id,workspace,src_item_id,dst_item_id,relation,source,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    relation_id,
                    workspace,
                    src,
                    dst,
                    relation,
                    _clean_string(payload.get("source")) or "cyrene",
                    now,
                ),
            )
            return dict(connection.execute("SELECT * FROM relations WHERE id=?", (relation_id,)).fetchone())

    def delete_relation(self, workspace: str, relation_id: str) -> bool:
        with self._lock, self._connect() as connection:
            return bool(
                connection.execute(
                    "DELETE FROM relations WHERE workspace=? AND id=?",
                    (workspace, relation_id),
                ).rowcount
            )

    def add_attachment(
        self,
        workspace: str,
        item_id: str,
        *,
        filename: str,
        path: Path,
        content_type: str,
        indexed_text: str,
        page_count: int,
        provider: str = "cyrene",
        provider_library_id: str = "",
        provider_key: str = "",
    ) -> dict[str, Any]:
        resolved_path = path.expanduser().resolve()
        try:
            resolved_path.relative_to(self.files_root)
        except ValueError as exc:
            raise ValueError("attachment must be stored inside the Plugin data directory") from exc
        digest = hashlib.sha256(resolved_path.read_bytes()).hexdigest()
        now = utc_now()
        attachment_id = new_id("attachment")
        chunks = split_text(indexed_text)
        old_path: Path | None = None
        with self._lock, self._connect() as connection:
            if not connection.execute("SELECT 1 FROM items WHERE workspace=? AND id=?", (workspace, item_id)).fetchone():
                raise LookupError("item not found")
            existing = None
            if provider_key:
                existing = connection.execute(
                    "SELECT id,path FROM attachments WHERE item_id=? AND provider=? AND provider_library_id=? AND provider_key=?",
                    (item_id, provider, provider_library_id, provider_key),
                ).fetchone()
            values = (
                filename,
                str(resolved_path),
                content_type or "application/octet-stream",
                file_type(filename, content_type),
                int(resolved_path.stat().st_size),
                max(0, int(page_count or 0)),
                digest,
                indexed_text,
                provider,
                provider_library_id,
                provider_key,
            )
            if existing:
                attachment_id = str(existing["id"])
                old_path = Path(str(existing["path"])).expanduser()
                connection.execute(
                    "UPDATE attachments SET filename=?,path=?,content_type=?,file_type=?,size=?,"
                    "page_count=?,content_hash=?,indexed_text=?,provider=?,provider_library_id=?,"
                    "provider_key=?,updated_at=? WHERE id=?",
                    (*values, now, attachment_id),
                )
                connection.execute("DELETE FROM chunks WHERE attachment_id=?", (attachment_id,))
            else:
                connection.execute(
                    "INSERT INTO attachments(id,item_id,workspace,filename,path,content_type,file_type,"
                    "size,page_count,content_hash,indexed_text,provider,provider_library_id,provider_key,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (attachment_id, item_id, workspace, *values, now, now),
                )
            for ordinal, chunk in enumerate(chunks):
                connection.execute(
                    "INSERT INTO chunks(id,workspace,item_id,attachment_id,ordinal,content,vector_json) VALUES(?,?,?,?,?,?,?)",
                    (
                        new_id("chunk"),
                        workspace,
                        item_id,
                        attachment_id,
                        ordinal,
                        chunk,
                        json.dumps(vectorize(chunk), separators=(",", ":")),
                    ),
                )
            connection.execute("UPDATE items SET updated_at=? WHERE id=?", (now, item_id))
            result = dict(
                connection.execute(
                    "SELECT id,filename,path,content_type,file_type,size,page_count,content_hash,created_at,updated_at FROM attachments WHERE id=?",
                    (attachment_id,),
                ).fetchone()
            )
        if old_path and old_path.resolve() != resolved_path:
            try:
                old_path.resolve().relative_to(self.files_root)
                old_path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        return result

    def add_annotation(self, workspace: str, item_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        now = utc_now()
        annotation_id = _clean_string(payload.get("id")) or new_id("annotation")
        provider = _clean_string(payload.get("provider")) or "cyrene"
        library_id = _clean_string(payload.get("provider_library_id"))
        provider_key = _clean_string(payload.get("provider_key"))
        with self._lock, self._connect() as connection:
            if not connection.execute("SELECT 1 FROM items WHERE workspace=? AND id=?", (workspace, item_id)).fetchone():
                raise LookupError("item not found")
            if provider_key:
                existing = connection.execute(
                    "SELECT id FROM annotations WHERE item_id=? AND provider=? AND provider_library_id=? AND provider_key=?",
                    (item_id, provider, library_id, provider_key),
                ).fetchone()
                if existing:
                    annotation_id = str(existing[0])
                    connection.execute(
                        "UPDATE annotations SET attachment_id=?,annotation_type=?,page_label=?,quote=?,comment=?,color=?,updated_at=? WHERE id=?",
                        (
                            _clean_string(payload.get("attachment_id")) or None,
                            _clean_string(payload.get("annotation_type")) or "highlight",
                            _clean_string(payload.get("page_label")),
                            str(payload.get("quote") or ""),
                            str(payload.get("comment") or ""),
                            _clean_string(payload.get("color")),
                            now,
                            annotation_id,
                        ),
                    )
                    return dict(connection.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,)).fetchone())
            connection.execute(
                "INSERT INTO annotations(id,item_id,attachment_id,annotation_type,page_label,quote,"
                "comment,color,provider,provider_library_id,provider_key,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    annotation_id,
                    item_id,
                    _clean_string(payload.get("attachment_id")) or None,
                    _clean_string(payload.get("annotation_type")) or "highlight",
                    _clean_string(payload.get("page_label")),
                    str(payload.get("quote") or ""),
                    str(payload.get("comment") or ""),
                    _clean_string(payload.get("color")),
                    provider,
                    library_id,
                    provider_key,
                    now,
                    now,
                ),
            )
            return dict(connection.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,)).fetchone())

    def primary_attachment(
        self,
        workspace: str,
        item_id: str,
        attachment_id: str = "",
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            if attachment_id:
                row = connection.execute(
                    "SELECT a.* FROM attachments a JOIN items i ON i.id=a.item_id WHERE i.workspace=? AND i.id=? AND a.id=?",
                    (workspace, item_id, attachment_id),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT a.* FROM attachments a JOIN items i ON i.id=a.item_id "
                    "WHERE i.workspace=? AND i.id=? ORDER BY "
                    "CASE WHEN a.content_type='application/pdf' THEN 0 ELSE 1 END,a.created_at LIMIT 1",
                    (workspace, item_id),
                ).fetchone()
            return dict(row) if row else None

    def list_documents(self, workspace: str, *, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT a.*,i.title,(SELECT COUNT(*) FROM chunks c WHERE c.attachment_id=a.id) "
                "AS chunk_count FROM attachments a JOIN items i ON i.id=a.item_id "
                "WHERE a.workspace=? AND i.deleted_at IS NULL ORDER BY a.updated_at DESC LIMIT ?",
                (workspace, max(1, min(int(limit or 100), 500))),
            ).fetchall()
            documents = [
                {
                    "id": str(row["id"]),
                    "item_id": str(row["item_id"]),
                    "name": str(row["filename"] or row["title"]),
                    "path": str(row["path"]),
                    "status": "indexed" if int(row["chunk_count"] or 0) else "stored",
                    "chunk_count": int(row["chunk_count"] or 0),
                    "size": int(row["size"] or 0),
                    "content_type": str(row["content_type"] or ""),
                }
                for row in rows
            ]
        requested_status = _clean_string(status).casefold()
        if not requested_status:
            return documents
        return [document for document in documents if str(document["status"]).casefold() == requested_status]

    def search_chunks(self, workspace: str | None, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        needle = _clean_string(query)
        if not needle:
            return []
        tokens = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", needle.casefold())
        query_vector = vectorize(needle)
        sql = "SELECT c.*,i.title,a.filename FROM chunks c JOIN items i ON i.id=c.item_id LEFT JOIN attachments a ON a.id=c.attachment_id WHERE i.deleted_at IS NULL"
        values: tuple[Any, ...] = ()
        if workspace is not None:
            sql += " AND c.workspace=?"
            values = (workspace,)
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, values).fetchall()
        scored: list[tuple[float, float, sqlite3.Row]] = []
        for row in rows:
            content = str(row["content"] or "")
            folded = content.casefold()
            lexical = sum(folded.count(token) for token in tokens) / max(1, len(tokens))
            try:
                stored_vector = json.loads(str(row["vector_json"] or "[]"))
            except json.JSONDecodeError:
                stored_vector = []
            similarity = cosine(query_vector, stored_vector)
            score = lexical + similarity
            if score > 0:
                scored.append((score, similarity, row))
        scored.sort(key=lambda value: value[0], reverse=True)
        return [
            {
                "chunk_id": str(row["id"]),
                "document_id": str(row["attachment_id"] or row["item_id"]),
                "item_id": str(row["item_id"]),
                "workspace": str(row["workspace"]),
                "document_name": str(row["filename"] or row["title"]),
                "content": str(row["content"]),
                "score": score,
                "cosine_similarity": similarity,
                "mode": "hybrid",
            }
            for score, similarity, row in scored[: max(1, min(int(limit or 20), 200))]
        ]

    def search_library(
        self,
        workspace: str,
        query: str,
        *,
        limit: int,
        status: str = "",
        tag: str = "",
    ) -> list[dict[str, Any]]:
        metadata = self.list_items(workspace, q=query, status=status, tag=tag, limit=limit)["items"]
        evidence = self.search_chunks(workspace, query, limit=max(limit * 3, 12))
        by_item: dict[str, list[dict[str, Any]]] = {}
        for hit in evidence:
            by_item.setdefault(str(hit["item_id"]), []).append(hit)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in metadata:
            item_id = str(item["id"])
            result.append({"workspace": workspace, "item": item, "evidence": by_item.get(item_id, [])[:2]})
            seen.add(item_id)
        for item_id, hits in by_item.items():
            if item_id in seen or len(result) >= limit:
                continue
            item = self.get_item(workspace, item_id, detail=False)
            if not item or (status and item.get("reading_status") != status):
                continue
            if tag and tag not in item.get("tags", []):
                continue
            result.append({"workspace": workspace, "item": item, "evidence": hits[:2]})
        return result[:limit]

    def mark_read(self, workspace: str, item_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE items SET reading_status='read',last_read_at=?,updated_at=? WHERE workspace=? AND id=?",
                (utc_now(), utc_now(), workspace, item_id),
            )
            if not cursor.rowcount:
                return None
            row = connection.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return self._hydrate(connection, row, detail=False)

    def embedding_status(self, workspace: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*),SUM(CASE WHEN vector_json<>'[]' THEN 1 ELSE 0 END) FROM chunks WHERE workspace=?",
                (workspace,),
            ).fetchone()
            total = int(row[0] or 0)
            compatible = int(row[1] or 0)
            return {
                "configured": True,
                "provider": "plugin_local",
                "model": "cyrene-hash-v1",
                "dimensions": 128,
                "total_chunks": total,
                "compatible_chunks": compatible,
                "compatible_vectors": compatible,
                "pending_vectors": max(0, total - compatible),
                "state": "complete" if total and total == compatible else "none" if not total else "partial",
            }

    def reembed(self, workspace: str) -> int:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT id,content FROM chunks WHERE workspace=?", (workspace,)).fetchall()
            connection.executemany(
                "UPDATE chunks SET vector_json=? WHERE id=?",
                [(json.dumps(vectorize(str(row["content"])), separators=(",", ":")), str(row["id"])) for row in rows],
            )
            return len(rows)

    def get_sync_state(self, workspace: str, provider: str, library_id: str, collection_key: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sync_state WHERE workspace=? AND provider=? AND provider_library_id=? AND collection_key=?",
                (workspace, provider, library_id, collection_key),
            ).fetchone()
            if not row:
                return None
            value = dict(row)
            try:
                value["config"] = json.loads(value.pop("config_json"))
            except json.JSONDecodeError:
                value["config"] = {}
            return value

    def list_sync_states(self, workspace: str, provider: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sync_state WHERE workspace=? AND provider=? ORDER BY updated_at DESC",
                (workspace, provider),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            try:
                value["config"] = json.loads(value.pop("config_json"))
            except json.JSONDecodeError:
                value["config"] = {}
            result.append(value)
        return result

    def set_sync_state(
        self,
        workspace: str,
        provider: str,
        library_id: str,
        collection_key: str,
        *,
        version: int,
        config: Mapping[str, Any],
        error: str = "",
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO sync_state(workspace,provider,provider_library_id,collection_key,version,"
                "config_json,last_error,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(workspace,provider,provider_library_id,collection_key) DO UPDATE SET "
                "version=excluded.version,config_json=excluded.config_json,last_error=excluded.last_error,"
                "updated_at=excluded.updated_at",
                (
                    workspace,
                    provider,
                    library_id,
                    collection_key,
                    int(version or 0),
                    json.dumps(dict(config), ensure_ascii=False),
                    str(error or ""),
                    utc_now(),
                ),
            )

    def delete_workspace(self, workspace: str) -> None:
        directory = self.files_root / self._workspace_directory_name(workspace)
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM sync_state WHERE workspace=?", (workspace,))
            connection.execute("DELETE FROM collections WHERE workspace=?", (workspace,))
            connection.execute("DELETE FROM items WHERE workspace=?", (workspace,))
        shutil.rmtree(directory, ignore_errors=True)

    def reset(self) -> None:
        with self._lock:
            shutil.rmtree(self.root, ignore_errors=True)
            self.root.mkdir(parents=True, exist_ok=True)
            self.files_root.mkdir(parents=True, exist_ok=True)
            self.initialize()


__all__ = [
    "KnowledgeStore",
    "cosine",
    "file_type",
    "new_id",
    "utc_now",
    "vectorize",
]
