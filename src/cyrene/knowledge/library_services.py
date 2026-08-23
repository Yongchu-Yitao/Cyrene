"""Repository and application services for the project-scoped literature library."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

import aiosqlite

from cyrene.knowledge import bibliography, embeddings, ingest, library, retrieve, store, zotero
from cyrene.runtime.attachments import resolve_managed_attachment_path, safe_attachment_filename


logger = logging.getLogger(__name__)


class LibraryUpload(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self) -> bytes: ...
    async def seek(self, offset: int) -> None: ...


@dataclass(slots=True)
class LibraryRequestError(ValueError):
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class LibraryRawDownload:
    path: Path
    filename: str
    media_type: str


class LibraryRepository:
    """Persistence/query boundary for library projections."""

    async def raw_attachment(self, db_path: str, item_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(db_path, timeout=30) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT a.*,d.path AS document_path,d.content_type AS document_content_type
                   FROM library_attachments a LEFT JOIN kb_documents d ON d.id=a.kb_document_id
                   WHERE a.item_id=? ORDER BY
                     CASE WHEN lower(COALESCE(a.content_type,d.content_type,''))='application/pdf' THEN 0 ELSE 1 END,
                     a.created_at LIMIT 1""",
                (item_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def viewed_item_ids(
        self,
        db_path: str,
        *,
        attachment_url: str,
        file_name: str,
        uploads_dir: Path,
        exports_dir: Path,
    ) -> list[str]:
        await library.sync_knowledge_documents(db_path)
        parsed_path = unquote(urlparse(str(attachment_url or "")).path)
        raw_prefix, raw_suffix = "/api/workbench/library/items/", "/raw"
        if parsed_path.startswith(raw_prefix) and parsed_path.endswith(raw_suffix):
            item_id = parsed_path[len(raw_prefix):-len(raw_suffix)].strip("/")
            return [item_id] if item_id and await library.get_item(db_path, item_id) else []
        candidates = self._managed_view_paths(parsed_path, uploads_dir, exports_dir)
        async with aiosqlite.connect(db_path, timeout=30) as db:
            ids = await self._item_ids_by_paths(db, candidates)
            if ids:
                return ids
            normalized_name = Path(str(file_name or "")).name.strip()
            if not normalized_name:
                return []
            cursor = await db.execute(
                """SELECT DISTINCT a.item_id FROM library_attachments a
                   LEFT JOIN kb_documents d ON d.id=a.kb_document_id
                   WHERE lower(COALESCE(NULLIF(a.filename,''),NULLIF(d.name,''),''))=lower(?)""",
                (normalized_name,),
            )
            matches = [str(row[0]) for row in await cursor.fetchall() if row[0]]
            return matches if len(matches) == 1 else []

    @staticmethod
    def _managed_view_paths(parsed_path: str, uploads_dir: Path, exports_dir: Path) -> list[str]:
        safe_name = safe_attachment_filename(Path(parsed_path).name, fallback_stem="attachment")
        if parsed_path.startswith("/api/chat/upload/"):
            return [str((uploads_dir / safe_name).resolve())]
        if parsed_path.startswith("/api/chat/export/"):
            return [str((exports_dir / safe_name).resolve())]
        return []

    @staticmethod
    async def _item_ids_by_paths(db: aiosqlite.Connection, paths: list[str]) -> list[str]:
        if not paths:
            return []
        placeholders = ",".join("?" for _ in paths)
        cursor = await db.execute(
            f"""SELECT DISTINCT a.item_id FROM library_attachments a
                LEFT JOIN kb_documents d ON d.id=a.kb_document_id
                WHERE a.path IN ({placeholders}) OR d.path IN ({placeholders})""",
            [*paths, *paths],
        )
        return [str(row[0]) for row in await cursor.fetchall() if row[0]]

    async def indexed_text(self, db_path: str, item_id: str, limit: int = 100_000) -> tuple[str, bool]:
        async with aiosqlite.connect(db_path, timeout=30) as db:
            cursor = await db.execute(
                """SELECT c.content FROM library_attachments a JOIN kb_chunks c
                   ON c.document_id=a.kb_document_id WHERE a.item_id=?
                   ORDER BY a.created_at,c.ordinal""",
                (item_id,),
            )
            parts, length, truncated = [], 0, False
            while rows := await cursor.fetchmany(100):
                for row in rows:
                    value = str(row[0] or "")
                    remaining = limit - length
                    if remaining <= 0:
                        truncated = True
                        break
                    parts.append(value[:remaining])
                    length += min(len(value), remaining)
                    if len(value) > remaining:
                        truncated = True
                        break
                if truncated:
                    break
            return "\n\n".join(parts), truncated

    async def embedding_status(self, db_path: str, item_id: str) -> dict[str, Any]:
        model, dimensions = embeddings.current_identity()
        async with aiosqlite.connect(db_path, timeout=30) as db:
            cursor = await db.execute(
                """SELECT COUNT(c.id), SUM(CASE WHEN c.embedding IS NOT NULL THEN 1 ELSE 0 END),
                   SUM(CASE WHEN c.embedding IS NOT NULL AND c.embedding_model = ?
                       AND (? = 0 OR c.embedding_dim = ?) THEN 1 ELSE 0 END)
                   FROM (SELECT DISTINCT kb_document_id FROM library_attachments
                         WHERE item_id = ? AND kb_document_id IS NOT NULL) a
                   LEFT JOIN kb_chunks c ON c.document_id = a.kb_document_id""",
                (model, dimensions, dimensions, item_id),
            )
            row = await cursor.fetchone()
        total, embedded, compatible = (int((row or [0, 0, 0])[index] or 0) for index in range(3))
        state = "complete" if total and compatible == total else "partial" if compatible else "incompatible" if embedded else "none"
        return {"state": state, "total_chunks": total, "embedded_chunks": embedded, "compatible_chunks": compatible, "model": model, "dimensions": dimensions}

    async def document_item_map(self, db_path: str, document_ids: list[str]) -> dict[str, str]:
        if not document_ids:
            return {}
        placeholders = ",".join("?" for _ in document_ids)
        async with aiosqlite.connect(db_path, timeout=30) as db:
            cursor = await db.execute(
                f"SELECT kb_document_id,item_id FROM library_attachments WHERE kb_document_id IN ({placeholders})",
                document_ids,
            )
            return {str(row[0]): str(row[1]) for row in await cursor.fetchall()}


class LibraryApplicationService:
    def __init__(
        self,
        *,
        repository: LibraryRepository,
        ensure_db: Callable[[str], Any],
        resolve_workspace: Callable[[str], str],
        uploads_dir: Path,
        exports_dir: Path,
    ) -> None:
        self.repository = repository
        self._ensure_db = ensure_db
        self._resolve_workspace = resolve_workspace
        self._uploads_dir = uploads_dir
        self._exports_dir = exports_dir

    async def db(self, workspace: str) -> str:
        return await self._ensure_db(workspace)

    def resolve_workspace(self, workspace: str) -> str:
        return self._resolve_workspace(workspace)

    async def list_items(self, workspace: str, **filters: Any) -> dict[str, Any]:
        db_path = await self.db(workspace)
        items, total = await library.list_items(db_path, **filters)
        return {"items": items, "total": total, "workspace": self._resolve_workspace(workspace)}

    async def stats(self, workspace: str) -> dict[str, Any]:
        return await library.get_stats(await self.db(workspace))

    async def tags(self, workspace: str) -> list[dict[str, Any]]:
        return await library.list_tags(await self.db(workspace))

    async def collections(self, workspace: str) -> list[dict[str, Any]]:
        return await library.list_collections(await self.db(workspace))

    async def create_collection(self, workspace: str, body: dict[str, Any]) -> dict[str, Any]:
        return await library.create_collection(await self.db(workspace), body)

    async def update_collection(self, workspace: str, collection_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
        return await library.update_collection(await self.db(workspace), collection_id, body)

    async def delete_collection(self, workspace: str, collection_id: str) -> bool:
        return await library.delete_collection(await self.db(workspace), collection_id)

    async def create_item(self, workspace: str, body: dict[str, Any]) -> dict[str, Any]:
        return await library.create_item(await self.db(workspace), body)

    async def delete_items(self, workspace: str, item_ids: list[str], *, permanent: bool) -> int:
        return await library.delete_items(await self.db(workspace), item_ids, permanent=permanent)

    async def get_item(self, workspace: str, item_id: str) -> dict[str, Any] | None:
        db_path = await self.db(workspace)
        item = await library.get_item(db_path, item_id, include_deleted=True)
        if not item:
            return None
        indexed_text, truncated = await self.repository.indexed_text(db_path, item_id)
        item["indexed_text"] = indexed_text
        item["indexed_text_truncated"] = truncated
        item["embedding_status"] = await self.repository.embedding_status(db_path, item_id)
        return item

    async def update_item(self, workspace: str, item_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
        return await library.update_item(await self.db(workspace), item_id, body)

    async def mark_read(self, workspace: str, *, attachment_url: str, file_name: str) -> dict[str, Any]:
        db_path = await self.db(workspace)
        ids = await self.repository.viewed_item_ids(
            db_path, attachment_url=attachment_url, file_name=file_name,
            uploads_dir=self._uploads_dir, exports_dir=self._exports_dir,
        )
        updated = []
        for item_id in ids:
            item = await library.update_item(db_path, item_id, {"reading_status": "read"})
            if item:
                updated.append(item)
        return {"ok": True, "updated": len(updated), "items": updated}

    async def delete_item(self, workspace: str, item_id: str, *, permanent: bool) -> bool:
        return await library.delete_item(await self.db(workspace), item_id, permanent=permanent)

    async def restore_item(self, workspace: str, item_id: str) -> dict[str, Any] | None:
        return await library.restore_item(await self.db(workspace), item_id)

    async def create_note(self, workspace: str, item_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
        db_path = await self.db(workspace)
        if not await library.get_item(db_path, item_id):
            return None
        return await library.create_note(db_path, item_id, body)

    async def update_note(self, workspace: str, note_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
        return await library.update_note(await self.db(workspace), note_id, body)

    async def delete_note(self, workspace: str, note_id: str) -> bool:
        return await library.delete_note(await self.db(workspace), note_id)

    async def create_relation(self, workspace: str, body: dict[str, Any]) -> dict[str, Any]:
        return await library.create_relation(await self.db(workspace), body)

    async def delete_relation(self, workspace: str, relation_id: str) -> bool:
        return await library.delete_relation(await self.db(workspace), relation_id)

    async def search(self, workspace: str, query: str, limit: int) -> dict[str, Any]:
        if not query.strip():
            return {"items": [], "total": 0, "document_results": []}
        db_path = await self.db(workspace)
        items, total = await library.list_items(db_path, q=query, limit=max(1, min(limit, 100)))
        results = await retrieve.search_knowledge(db_path, query, k=max(1, min(limit, 50)))
        mapping = await self.repository.document_item_map(db_path, [str(item.get("document_id") or "") for item in results])
        for result in results:
            result["library_item_id"] = mapping.get(str(result.get("document_id") or ""))
        return {"items": items, "total": total, "document_results": [item for item in results if item.get("library_item_id")]}

    async def citation(self, workspace: str, item_id: str, style: str) -> dict[str, Any] | None:
        item = await library.get_item(await self.db(workspace), item_id)
        if not item:
            return None
        return {"citation": library.render_citation(item, style), "bibtex": library.render_bibtex(item), "citekey": item.get("citekey") or item.get("provider_item_key") or item["id"], "style": style}

    async def raw(self, workspace: str, item_id: str) -> LibraryRawDownload | None:
        db_path = await self.db(workspace)
        if not await library.get_item(db_path, item_id):
            return None
        attachment = await self.repository.raw_attachment(db_path, item_id)
        if not attachment:
            raise LibraryRequestError("no attachment", 404)
        stored_path = str(attachment.get("document_path") or "")
        path = Path(stored_path)
        if not path.is_file():
            path = resolve_managed_attachment_path(stored_path)
        if path is None or not path.is_file():
            raise LibraryRequestError("attachment unavailable", 404)
        media_type = str(attachment.get("document_content_type") or attachment.get("content_type") or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        return LibraryRawDownload(path, str(attachment.get("filename") or path.name), media_type)


class LibraryIngestService:
    def __init__(self, *, application: LibraryApplicationService, uploads_dir: Path) -> None:
        self.application = application
        self._uploads_dir = uploads_dir
        self._reembed_state: dict[str, dict[str, Any]] = {}

    async def import_records(self, workspace: str, body: dict[str, Any]) -> dict[str, Any]:
        items = body["items"]
        db_path = await self.application.db(workspace)
        zotero_items = [value for value in items if isinstance(value, dict) and (isinstance(value.get("data"), dict) or value.get("provider") == "zotero" or "itemType" in value)]
        normalized = [value for value in items if isinstance(value, dict) and value not in zotero_items]
        summary = await zotero.import_records(
            db_path, zotero_items,
            collections=body.get("collections") if isinstance(body.get("collections"), list) else [],
            library_id=str(body.get("provider_library_id") or "0"),
        )
        for value in normalized:
            summary["items"].append(await library.create_item(db_path, value))
            summary["created"] += 1
            summary["imported"] += 1
        return summary

    async def upload(self, workspace: str, files: list[LibraryUpload], item_id: str) -> list[dict[str, Any]]:
        db_path = await self.application.db(workspace)
        items = []
        for file in files:
            content = await file.read()
            try:
                parsed = bibliography.parse(file.filename or "", content)
            except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise LibraryRequestError(f"无法解析 {file.filename or 'bibliography'}: {exc}") from exc
            if parsed is not None:
                if item_id:
                    raise LibraryRequestError("item_id can only be used when uploading an attachment")
                if not parsed:
                    raise LibraryRequestError(f"{file.filename or 'bibliography'} 中没有可导入的文献")
                for payload in parsed:
                    items.append(await library.create_item(db_path, payload))
                continue
            await file.seek(0)
            items.append(await self._upload_one(db_path, workspace, file, item_id))
        return items

    async def _upload_one(self, db_path: str, workspace: str, file: LibraryUpload, item_id: str) -> dict[str, Any]:
        resolved = self.application.resolve_workspace(workspace)
        target_dir = self._uploads_dir / f"library_{resolved}"
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = safe_attachment_filename(file.filename or "knowledge-file", fallback_stem="knowledge-file")
        target = target_dir / f"{uuid.uuid4().hex}_{safe_name}"
        content = await file.read()
        target.write_bytes(content)
        digest = store.content_hash_bytes(content)
        content_type = str(file.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream")
        doc = await store.upsert_document_by_path(
            db_path, path=str(target.resolve()), source="library_upload", name=file.filename or safe_name,
            content_type=content_type, kind="pdf" if target.suffix.lower() == ".pdf" else "file",
            size=len(content), content_hash=digest,
        )
        if doc.get("path") and Path(str(doc["path"])).resolve() != target.resolve():
            target.unlink(missing_ok=True)
        if not item_id:
            item = await library.create_item(db_path, {"title": Path(file.filename or safe_name).stem.replace("_", " "), "item_type": "document"})
            item_id = item["id"]
        elif not await library.get_item(db_path, item_id):
            target.unlink(missing_ok=True)
            raise LibraryRequestError("knowledge item not found", 404)
        await library.add_attachment(db_path, item_id, {
            "kb_document_id": doc.get("id"), "title": file.filename or safe_name,
            "filename": file.filename or safe_name, "path": str(doc.get("path") or target.resolve()),
            "content_type": content_type, "link_mode": "imported_file", "content_hash": digest,
        })
        if doc.get("status") in {"pending", "error"}:
            asyncio.create_task(ingest.index_document(db_path, doc["id"]))
        return await library.get_item(db_path, item_id) or {}

    async def embedding_status(self, workspace: str) -> dict[str, Any]:
        db_path = await self.application.db(workspace)
        info = await store.get_corpus_embedding_info(db_path)
        model, dimensions = embeddings.current_identity()
        configured = embeddings.is_configured()
        coverage = await store.get_embedding_coverage(db_path, model, dimensions)
        return {**info, **coverage, "configured": configured, "retrieval_mode": "hybrid" if configured else "keyword", "model": model, "dimensions": dimensions, "mismatch": bool(configured and coverage["total_chunks"] and coverage["pending_vectors"]), "reembed": self._reembed_state.get(db_path, {"running": False})}

    async def reembed(self, workspace: str) -> dict[str, Any]:
        db_path = await self.application.db(workspace)
        current = self._reembed_state.get(db_path, {})
        if current.get("running"):
            return {"ok": True, "reembed": current}
        self._reembed_state[db_path] = {"running": True, "error": ""}
        asyncio.create_task(self._run_reembed(db_path, workspace))
        return {"ok": True, "reembed": {"running": True}}

    async def _run_reembed(self, db_path: str, workspace: str) -> None:
        try:
            result = await ingest.reembed_all(db_path)
            self._reembed_state[db_path] = {"running": False, **result, "error": ""}
        except Exception as exc:
            logger.exception("Library re-embedding failed for %s", workspace)
            self._reembed_state[db_path] = {"running": False, "error": str(exc)}


class ZoteroSyncService:
    def __init__(
        self,
        *,
        application: LibraryApplicationService,
        settings: Callable[[], dict[str, Any]],
    ) -> None:
        self.application = application
        self._settings = settings

    @staticmethod
    def client(config: dict[str, Any]) -> zotero.ZoteroLocalClient:
        return zotero.ZoteroLocalClient(str(config.get("base_url") or zotero.DEFAULT_BASE_URL))

    async def status(self, workspace: str) -> dict[str, Any]:
        db_path = await self.application.db(workspace)
        config = self._settings()
        status = await self.client(config).status()
        status["sync_sources"] = await library.get_sync_state(db_path)
        status["auto_sync"] = bool(config.get("auto_sync", False))
        status["copy_attachments"] = bool(config.get("copy_attachments", True))
        status["default_library_id"] = "0"
        status["default_library_type"] = "user"
        return status

    async def collections(self, library_id: str, library_type: str) -> dict[str, Any]:
        records, version = await self.client(self._settings()).collections(library_id, library_type)
        return {"collections": records, "library_version": version}

    async def sync(self, workspace: str, body: dict[str, Any]) -> dict[str, Any]:
        db_path = await self.application.db(workspace)
        config = self._settings()
        since = body.get("since")
        return await zotero.sync(
            db_path, self.client(config), library_id=str(body.get("library_id") or "0"),
            library_type=str(body.get("library_type") or "user"),
            collection_key=str(body.get("collection_key") or ""),
            since=int(since) if since not in (None, "") else None,
            copy_attachments=bool(config.get("copy_attachments", True)),
        )


__all__ = [
    "LibraryApplicationService", "LibraryIngestService", "LibraryRawDownload",
    "LibraryRepository", "LibraryRequestError", "ZoteroSyncService",
]
