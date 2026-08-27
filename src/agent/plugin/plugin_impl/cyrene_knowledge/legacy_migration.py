"""One-way import of pre-Plugin knowledge databases.

The old backend stored one ``kb_<dataKey>.db`` file per Workbench project.
This module is intentionally self-contained inside the editable knowledge
Plugin: core only supplies the Plugin data directory and current project state.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from .store import KnowledgeStore, file_type, utc_now, vectorize

logger = logging.getLogger(__name__)

MIGRATION_VERSION = 1


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return list(connection.execute(f'SELECT * FROM "{table}"').fetchall())


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stable_id(kind: str, workspace: str, identity: str) -> str:
    digest = hashlib.sha256(
        f"{kind}\0{workspace}\0{identity}".encode("utf-8")
    ).hexdigest()[:32]
    return f"legacy_{kind}_{digest}"


def _safe_data_key(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", _text(value)).strip("._")
    return cleaned or "default"


def _workspace_map(project_state: Mapping[str, Any]) -> tuple[dict[str, str], str]:
    projects = project_state.get("projects")
    projects = projects if isinstance(projects, list) else []
    mapping: dict[str, str] = {}
    first = ""
    for raw in projects:
        if not isinstance(raw, Mapping):
            continue
        project_id = _text(raw.get("id"))
        if not project_id:
            continue
        first = first or project_id
        mapping[project_id] = project_id
        mapping[_safe_data_key(raw.get("dataKey") or project_id)] = project_id
    active = _text(project_state.get("activeProjectId"))
    if active not in mapping.values():
        active = first
    return mapping, active


def _source_workspace(path: Path, mapping: Mapping[str, str], fallback: str) -> str:
    key = path.stem[3:] if path.stem.startswith("kb_") else path.stem
    return _text(mapping.get(key)) or fallback


def _managed_legacy_path(
    store: KnowledgeStore,
    workspace: str,
    source: str,
    identity: str,
) -> tuple[Path, int, str]:
    source_path = Path(source).expanduser()
    filename = source_path.name or "attachment"
    clean = re.sub(r"[^\w.()\- ]+", "_", filename, flags=re.UNICODE).strip()
    clean = clean[:160] or "attachment"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    target = store.workspace_files(workspace) / f"legacy_{digest}_{clean}"
    size = 0
    content_hash = ""
    if source_path.is_file():
        try:
            size = int(source_path.stat().st_size)
            content_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if not target.is_file() or target.stat().st_size != size:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target)
        except OSError:
            logger.warning("Could not copy legacy knowledge attachment %s", source_path)
    return target, size, content_hash


def _insert_chunks(
    target: sqlite3.Connection,
    *,
    workspace: str,
    item_id: str,
    attachment_id: str | None,
    chunks: Iterable[sqlite3.Row],
    identity_prefix: str,
) -> int:
    count = 0
    for ordinal, row in enumerate(chunks):
        content = str(row["content"] or "")
        if not content:
            continue
        source_ordinal = int(row["ordinal"] or ordinal)
        chunk_id = _stable_id(
            "chunk", workspace, f"{identity_prefix}:{source_ordinal}"
        )
        target.execute(
            "INSERT OR IGNORE INTO chunks(id,workspace,item_id,attachment_id,ordinal,content,vector_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                chunk_id,
                workspace,
                item_id,
                attachment_id,
                source_ordinal,
                content,
                json.dumps(vectorize(content), separators=(",", ":")),
            ),
        )
        count += 1
    return count


def _existing_provider_item(
    target: sqlite3.Connection,
    workspace: str,
    provider: str,
    library_id: str,
    provider_key: str,
) -> str:
    if not provider_key:
        return ""
    row = target.execute(
        "SELECT id FROM items WHERE workspace=? AND provider=? AND provider_library_id=? AND provider_item_key=?",
        (workspace, provider, library_id, provider_key),
    ).fetchone()
    return _text(row[0]) if row else ""


def _insert_item(
    target: sqlite3.Connection,
    *,
    workspace: str,
    item_id: str,
    payload: Mapping[str, Any],
    created_at: str,
    updated_at: str,
    last_read_at: str = "",
    deleted_at: str = "",
) -> str:
    provider = _text(payload.get("provider")) or "cyrene"
    library_id = _text(payload.get("provider_library_id"))
    provider_key = _text(payload.get("provider_item_key"))
    existing = _existing_provider_item(
        target, workspace, provider, library_id, provider_key
    )
    if existing:
        return existing
    target.execute(
        "INSERT OR IGNORE INTO items(id,workspace,item_type,title,abstract,doi,isbn,url,venue,publisher,"
        "volume,issue,pages,language,year,date_text,citekey,reading_status,starred,provider,"
        "provider_library_id,provider_item_key,provider_version,content,created_at,updated_at,last_read_at,deleted_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            item_id,
            workspace,
            _text(payload.get("item_type")) or "document",
            _text(payload.get("title")) or "Untitled",
            _text(payload.get("abstract")),
            _text(payload.get("doi")),
            _text(payload.get("isbn")),
            _text(payload.get("url")),
            _text(payload.get("venue")),
            _text(payload.get("publisher")),
            _text(payload.get("volume")),
            _text(payload.get("issue")),
            _text(payload.get("pages")),
            _text(payload.get("language")),
            payload.get("year") if str(payload.get("year") or "").isdigit() else None,
            _text(payload.get("date_text")),
            _text(payload.get("citekey")),
            _text(payload.get("reading_status")) or "unread",
            int(bool(payload.get("starred"))),
            provider,
            library_id,
            provider_key,
            int(payload.get("provider_version") or 0),
            str(payload.get("content") or ""),
            created_at or utc_now(),
            updated_at or created_at or utc_now(),
            last_read_at or None,
            deleted_at or None,
        ),
    )
    return item_id


def migrate_legacy_knowledge(
    store: KnowledgeStore,
    legacy_store_directory: str | Path,
    project_state: Mapping[str, Any],
) -> dict[str, int]:
    """Import all legacy knowledge databases into ``store`` once per version."""

    source_root = Path(legacy_store_directory).expanduser().resolve()
    mapping, fallback = _workspace_map(project_state)
    report = {"sources": 0, "items": 0, "attachments": 0, "chunks": 0}
    if not source_root.is_dir() or not fallback:
        return report

    sources: list[tuple[Path, str, int, int]] = []
    with store._lock, store._connect() as target:
        for path in sorted(source_root.glob("kb_*.db")):
            if not path.is_file():
                continue
            workspace = _source_workspace(path, mapping, fallback)
            stat = path.stat()
            prior = target.execute(
                "SELECT source_size,source_mtime_ns FROM legacy_imports "
                "WHERE source_path=? AND workspace=? AND migration_version=?",
                (str(path), workspace, MIGRATION_VERSION),
            ).fetchone()
            if prior and int(prior[0]) == stat.st_size and int(prior[1]) == stat.st_mtime_ns:
                continue
            sources.append((path, workspace, stat.st_size, stat.st_mtime_ns))

        document_owners: dict[tuple[str, str], str] = {}
        item_owners: dict[tuple[str, str], str] = {}
        attachment_ids: dict[tuple[str, str], str] = {}

        # Literature records first, so their backing kb_documents do not become
        # duplicate standalone items in the second pass.
        for path, workspace, _size, _mtime in sources:
            with sqlite3.connect(path) as source:
                source.row_factory = sqlite3.Row
                tables = _tables(source)
                if "library_items" not in tables:
                    continue

                collection_map: dict[str, str] = {}
                if "library_collections" in tables:
                    for row in _rows(source, "library_collections"):
                        old_id = _text(row["id"])
                        collection_id = _stable_id("collection", workspace, old_id)
                        collection_map[old_id] = collection_id
                        target.execute(
                            "INSERT OR IGNORE INTO collections(id,workspace,name,parent_id,color,provider,provider_library_id,provider_key,provider_version,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                collection_id,
                                workspace,
                                _text(row["name"]) or "Untitled",
                                None,
                                "",
                                _text(row["provider"]) or "cyrene",
                                _text(row["provider_library_id"]),
                                _text(row["provider_key"]),
                                int(row["provider_version"] or 0),
                                _text(row["created_at"]) or utc_now(),
                                _text(row["updated_at"]) or utc_now(),
                            ),
                        )
                    for row in _rows(source, "library_collections"):
                        parent = collection_map.get(_text(row["parent_id"]))
                        if parent:
                            target.execute(
                                "UPDATE collections SET parent_id=? WHERE id=?",
                                (parent, collection_map[_text(row["id"])]),
                            )

                for row in _rows(source, "library_items"):
                    old_id = _text(row["id"])
                    item_id = _stable_id("item", workspace, old_id)
                    item_id = _insert_item(
                        target,
                        workspace=workspace,
                        item_id=item_id,
                        payload=dict(row),
                        created_at=_text(row["created_at"]),
                        updated_at=_text(row["updated_at"]),
                        last_read_at=_text(row["last_read_at"]),
                        deleted_at=_text(row["deleted_at"]),
                    )
                    item_owners[(str(path), old_id)] = item_id
                    report["items"] += 1
                    for tag in _json_list(row["tags"]):
                        label = _text(tag.get("tag") if isinstance(tag, Mapping) else tag)
                        if label:
                            target.execute(
                                "INSERT OR IGNORE INTO tags(item_id,tag) VALUES(?,?)",
                                (item_id, label),
                            )

                if "library_creators" in tables:
                    for row in _rows(source, "library_creators"):
                        item_id = item_owners.get((str(path), _text(row["item_id"])))
                        if not item_id:
                            continue
                        creator_id = _stable_id("creator", workspace, _text(row["id"]))
                        target.execute(
                            "INSERT OR IGNORE INTO creators(id,item_id,ordinal,creator_type,first_name,last_name,name) VALUES(?,?,?,?,?,?,?)",
                            (
                                creator_id,
                                item_id,
                                int(row["ordinal"] or 0),
                                _text(row["creator_type"]) or "author",
                                _text(row["first_name"]),
                                _text(row["last_name"]),
                                _text(row["name"]),
                            ),
                        )
                if "library_collection_items" in tables:
                    for row in _rows(source, "library_collection_items"):
                        item_id = item_owners.get((str(path), _text(row["item_id"])))
                        collection_id = collection_map.get(_text(row["collection_id"]))
                        if item_id and collection_id:
                            target.execute(
                                "INSERT OR IGNORE INTO collection_items(collection_id,item_id,created_at) VALUES(?,?,?)",
                                (collection_id, item_id, _text(row["created_at"]) or utc_now()),
                            )

                chunks_by_document: dict[str, list[sqlite3.Row]] = {}
                if "kb_chunks" in tables:
                    for chunk in _rows(source, "kb_chunks"):
                        chunks_by_document.setdefault(_text(chunk["document_id"]), []).append(chunk)
                if "library_attachments" in tables:
                    for row in _rows(source, "library_attachments"):
                        item_id = item_owners.get((str(path), _text(row["item_id"])))
                        if not item_id:
                            continue
                        old_attachment_id = _text(row["id"])
                        attachment_id = _stable_id("attachment", workspace, old_attachment_id)
                        attachment_ids[(str(path), old_attachment_id)] = attachment_id
                        document_id = _text(row["kb_document_id"])
                        if document_id:
                            document_owners[(str(path), document_id)] = item_id
                        filename = _text(row["filename"] or row["title"]) or "attachment"
                        identity = f"{workspace}:{old_attachment_id}"
                        managed, size, digest = _managed_legacy_path(
                            store, workspace, _text(row["path"]), identity
                        )
                        text = "\n\n".join(
                            str(chunk["content"] or "")
                            for chunk in chunks_by_document.get(document_id, [])
                            if str(chunk["content"] or "")
                        )
                        content_type = _text(row["content_type"]) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
                        target.execute(
                            "INSERT OR IGNORE INTO attachments(id,item_id,workspace,filename,path,content_type,file_type,size,page_count,content_hash,indexed_text,provider,provider_library_id,provider_key,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                attachment_id,
                                item_id,
                                workspace,
                                filename,
                                str(managed),
                                content_type,
                                file_type(filename, content_type),
                                size,
                                0,
                                digest or _text(row["content_hash"]),
                                text,
                                _text(row["provider"]) or "cyrene",
                                _text(row["provider_library_id"]),
                                _text(row["provider_key"]),
                                _text(row["created_at"]) or utc_now(),
                                _text(row["updated_at"]) or utc_now(),
                            ),
                        )
                        report["attachments"] += 1
                        report["chunks"] += _insert_chunks(
                            target,
                            workspace=workspace,
                            item_id=item_id,
                            attachment_id=attachment_id,
                            chunks=chunks_by_document.get(document_id, []),
                            identity_prefix=f"attachment:{old_attachment_id}",
                        )

                if "library_notes" in tables:
                    for row in _rows(source, "library_notes"):
                        item_id = item_owners.get((str(path), _text(row["item_id"])))
                        if not item_id:
                            continue
                        target.execute(
                            "INSERT OR IGNORE INTO notes(id,item_id,title,content,author,provider,provider_library_id,provider_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (
                                _stable_id("note", workspace, _text(row["id"])),
                                item_id,
                                _text(row["title"]),
                                str(row["content"] or ""),
                                _text(row["author"]),
                                _text(row["provider"]) or "cyrene",
                                _text(row["provider_library_id"]),
                                _text(row["provider_key"]),
                                _text(row["created_at"]) or utc_now(),
                                _text(row["updated_at"]) or utc_now(),
                            ),
                        )
                if "library_annotations" in tables:
                    for row in _rows(source, "library_annotations"):
                        item_id = item_owners.get((str(path), _text(row["item_id"])))
                        if not item_id:
                            continue
                        target.execute(
                            "INSERT OR IGNORE INTO annotations(id,item_id,attachment_id,annotation_type,page_label,quote,comment,color,provider,provider_library_id,provider_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                _stable_id("annotation", workspace, _text(row["id"])),
                                item_id,
                                attachment_ids.get((str(path), _text(row["attachment_id"]))),
                                _text(row["annotation_type"]) or "highlight",
                                _text(row["page_label"]),
                                str(row["quote"] or ""),
                                str(row["comment"] or ""),
                                _text(row["color"]),
                                _text(row["provider"]) or "cyrene",
                                _text(row["provider_library_id"]),
                                _text(row["provider_key"]),
                                _text(row["created_at"]) or utc_now(),
                                _text(row["updated_at"]) or utc_now(),
                            ),
                        )

        # Generic knowledge documents, excluding those already owned by a
        # migrated literature attachment. Content hashes collapse copies left
        # behind by earlier default -> project database migrations.
        for path, workspace, _size, _mtime in sources:
            with sqlite3.connect(path) as source:
                source.row_factory = sqlite3.Row
                tables = _tables(source)
                if "kb_documents" not in tables:
                    continue
                chunks_by_document: dict[str, list[sqlite3.Row]] = {}
                if "kb_chunks" in tables:
                    for chunk in _rows(source, "kb_chunks"):
                        chunks_by_document.setdefault(_text(chunk["document_id"]), []).append(chunk)
                for row in _rows(source, "kb_documents"):
                    old_id = _text(row["id"])
                    key = (str(path), old_id)
                    if key in document_owners:
                        continue
                    digest = _text(row["content_hash"])
                    existing = None
                    if digest:
                        existing = target.execute(
                            "SELECT item_id FROM attachments WHERE workspace=? AND content_hash=? LIMIT 1",
                            (workspace, digest),
                        ).fetchone()
                    if existing:
                        document_owners[key] = _text(existing[0])
                        continue
                    identity = f"hash:{digest}" if digest else f"document:{old_id}"
                    item_id = _stable_id("document", workspace, identity)
                    chunks = chunks_by_document.get(old_id, [])
                    content = "\n\n".join(
                        str(chunk["content"] or "") for chunk in chunks if str(chunk["content"] or "")
                    )
                    title = _text(row["title"] or row["name"]) or Path(_text(row["path"])).stem or "Untitled"
                    item_id = _insert_item(
                        target,
                        workspace=workspace,
                        item_id=item_id,
                        payload={
                            "item_type": _text(row["kind"]) or "document",
                            "title": title,
                            "abstract": _text(row["summary"]),
                            "provider": "cyrene-legacy",
                            "provider_library_id": workspace,
                            "provider_item_key": identity,
                            "content": "" if _text(row["path"]) else content,
                        },
                        created_at=_text(row["created_at"]),
                        updated_at=_text(row["updated_at"]),
                    )
                    document_owners[key] = item_id
                    report["items"] += 1
                    for tag in _json_list(row["tags"]):
                        label = _text(tag.get("tag") if isinstance(tag, Mapping) else tag)
                        if label:
                            target.execute(
                                "INSERT OR IGNORE INTO tags(item_id,tag) VALUES(?,?)",
                                (item_id, label),
                            )
                    attachment_id: str | None = None
                    source_path = _text(row["path"])
                    if source_path:
                        attachment_id = _stable_id("attachment", workspace, identity)
                        managed, actual_size, actual_digest = _managed_legacy_path(
                            store, workspace, source_path, identity
                        )
                        filename = _text(row["name"]) or Path(source_path).name or "attachment"
                        content_type = _text(row["content_type"]) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
                        target.execute(
                            "INSERT OR IGNORE INTO attachments(id,item_id,workspace,filename,path,content_type,file_type,size,page_count,content_hash,indexed_text,provider,provider_library_id,provider_key,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                attachment_id,
                                item_id,
                                workspace,
                                filename,
                                str(managed),
                                content_type,
                                file_type(filename, content_type),
                                actual_size or int(row["size"] or 0),
                                0,
                                actual_digest or digest,
                                content,
                                "cyrene-legacy",
                                workspace,
                                identity,
                                _text(row["created_at"]) or utc_now(),
                                _text(row["updated_at"]) or utc_now(),
                            ),
                        )
                        report["attachments"] += 1
                    report["chunks"] += _insert_chunks(
                        target,
                        workspace=workspace,
                        item_id=item_id,
                        attachment_id=attachment_id,
                        chunks=chunks,
                        identity_prefix=identity,
                    )

        # Preserve both generic document relations and structured item relations.
        for path, workspace, size, mtime_ns in sources:
            with sqlite3.connect(path) as source:
                source.row_factory = sqlite3.Row
                tables = _tables(source)
                if "kb_relations" in tables:
                    for row in _rows(source, "kb_relations"):
                        src = document_owners.get((str(path), _text(row["src_id"])))
                        dst = document_owners.get((str(path), _text(row["dst_id"])))
                        if src and dst and src != dst:
                            target.execute(
                                "INSERT OR IGNORE INTO relations(id,workspace,src_item_id,dst_item_id,relation,source,created_at) VALUES(?,?,?,?,?,?,?)",
                                (
                                    _stable_id("relation", workspace, _text(row["id"])),
                                    workspace,
                                    src,
                                    dst,
                                    _text(row["relation"]) or "related",
                                    _text(row["source"]) or "cyrene-legacy",
                                    _text(row["created_at"]) or utc_now(),
                                ),
                            )
                if "library_relations" in tables:
                    for row in _rows(source, "library_relations"):
                        src = item_owners.get((str(path), _text(row["src_item_id"])))
                        dst = item_owners.get((str(path), _text(row["dst_item_id"])))
                        if src and dst and src != dst:
                            target.execute(
                                "INSERT OR IGNORE INTO relations(id,workspace,src_item_id,dst_item_id,relation,source,created_at) VALUES(?,?,?,?,?,?,?)",
                                (
                                    _stable_id("relation", workspace, _text(row["id"])),
                                    workspace,
                                    src,
                                    dst,
                                    _text(row["relation"]) or "related",
                                    _text(row["source"]) or "cyrene-legacy",
                                    _text(row["created_at"]) or utc_now(),
                                ),
                            )
                target.execute(
                    "INSERT INTO legacy_imports(source_path,workspace,migration_version,source_size,source_mtime_ns,imported_at,report_json) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_path,workspace,migration_version) DO UPDATE SET "
                    "source_size=excluded.source_size,source_mtime_ns=excluded.source_mtime_ns,"
                    "imported_at=excluded.imported_at,report_json=excluded.report_json",
                    (
                        str(path),
                        workspace,
                        MIGRATION_VERSION,
                        size,
                        mtime_ns,
                        utc_now(),
                        json.dumps(report, separators=(",", ":")),
                    ),
                )
                report["sources"] += 1

    if report["sources"]:
        logger.info("Imported legacy knowledge databases: %s", report)
    return report


__all__ = ["MIGRATION_VERSION", "migrate_legacy_knowledge"]
