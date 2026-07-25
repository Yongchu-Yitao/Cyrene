"""Project-isolated literature-library API for Workbench."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import aiosqlite
from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from cyrene.attachments import EXPORTS_DIR, UPLOADS_DIR, safe_attachment_filename
from cyrene.knowledge import bibliography, ingest, library, retrieve, store, zotero
from route.errors import error_response
from cyrene.workbench_knowledge_service import _ensure_kb_db, _resolve_workspace_id


logger = logging.getLogger(__name__)


def _zotero_config() -> dict[str, Any]:
    from cyrene.integration_settings import get_zotero_settings

    return get_zotero_settings()


def _zotero_client(config: dict[str, Any] | None = None) -> zotero.ZoteroLocalClient:
    settings = config or _zotero_config()
    return zotero.ZoteroLocalClient(str(settings.get("base_url") or zotero.DEFAULT_BASE_URL))


def _bool_param(value: str | bool | None) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


async def _find_raw_attachment(db_path: str, item_id: str) -> dict[str, Any] | None:
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


async def _viewed_item_ids(
    db_path: str, *, attachment_url: str = "", file_name: str = ""
) -> list[str]:
    await library.sync_knowledge_documents(db_path)
    parsed_path = unquote(urlparse(str(attachment_url or "")).path)
    raw_prefix = "/api/workbench/library/items/"
    raw_suffix = "/raw"
    if parsed_path.startswith(raw_prefix) and parsed_path.endswith(raw_suffix):
        item_id = parsed_path[len(raw_prefix):-len(raw_suffix)].strip("/")
        return [item_id] if item_id and await library.get_item(db_path, item_id) else []

    candidate_paths: list[str] = []
    safe_name = safe_attachment_filename(
        Path(parsed_path).name, fallback_stem="attachment"
    )
    if parsed_path.startswith("/api/chat/upload/"):
        candidate_paths.append(str((UPLOADS_DIR / safe_name).resolve()))
    elif parsed_path.startswith("/api/chat/export/"):
        candidate_paths.append(str((EXPORTS_DIR / safe_name).resolve()))

    async with aiosqlite.connect(db_path, timeout=30) as db:
        if candidate_paths:
            placeholders = ",".join("?" for _ in candidate_paths)
            cursor = await db.execute(
                f"""SELECT DISTINCT a.item_id FROM library_attachments a
                    LEFT JOIN kb_documents d ON d.id=a.kb_document_id
                    WHERE a.path IN ({placeholders}) OR d.path IN ({placeholders})""",
                [*candidate_paths, *candidate_paths],
            )
            ids = [str(row[0]) for row in await cursor.fetchall() if row[0]]
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
        ids = [str(row[0]) for row in await cursor.fetchall() if row[0]]
        return ids if len(ids) == 1 else []


async def _indexed_item_text(
    db_path: str, item_id: str, limit: int = 100_000
) -> tuple[str, bool]:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        cursor = await db.execute(
            """SELECT c.content FROM library_attachments a
               JOIN kb_chunks c ON c.document_id=a.kb_document_id
               WHERE a.item_id=? ORDER BY a.created_at,c.ordinal""",
            (item_id,),
        )
        parts: list[str] = []
        length = 0
        truncated = False
        while True:
            rows = await cursor.fetchmany(100)
            if not rows:
                break
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


async def _upload_one(
    db_path: str, workspace: str, file: UploadFile, item_id: str = ""
) -> dict[str, Any]:
    resolved = _resolve_workspace_id(workspace)
    target_dir = UPLOADS_DIR / f"library_{resolved}"
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = safe_attachment_filename(
        file.filename or "knowledge-file", fallback_stem="knowledge-file"
    )
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
        item = await library.create_item(db_path, {
            "title": Path(file.filename or safe_name).stem.replace("_", " "),
            "item_type": "document",
        })
        item_id = item["id"]
    elif not await library.get_item(db_path, item_id):
        target.unlink(missing_ok=True)
        raise ValueError("knowledge item not found")
    await library.add_attachment(db_path, item_id, {
        "kb_document_id": doc.get("id"), "title": file.filename or safe_name,
        "filename": file.filename or safe_name, "path": str(doc.get("path") or target.resolve()),
        "content_type": content_type, "link_mode": "imported_file", "content_hash": digest,
    })
    if doc.get("status") in {"pending", "error"}:
        asyncio.create_task(ingest.index_document(db_path, doc["id"]))
    return await library.get_item(db_path, item_id) or {}


def register_workbench_library_routes(router: APIRouter) -> None:
    @router.get("/api/workbench/library/items")
    async def wb_library_items(
        workspace: str = "", q: str = "", collection: str = "", status: str = "",
        tag: str = "", item_type: str = "", file_type: str = "",
        year: int | None = None, starred: str | None = None,
        trash: bool = False, sort: str = "updated_at", order: str = "desc",
        limit: int = 200, offset: int = 0,
    ):
        try:
            db_path = await _ensure_kb_db(workspace)
            items, total = await library.list_items(
                db_path, q=q, collection=collection, status=status, tag=tag,
                item_type=item_type, file_type=file_type, year=year,
                starred=_bool_param(starred), trash=trash,
                sort=sort, order=order, limit=limit, offset=offset,
            )
            return {"items": items, "total": total, "workspace": _resolve_workspace_id(workspace)}
        except Exception:
            logger.exception("Failed to list literature for %s", workspace)
            return error_response("List failed", 500, "library_list_failed")

    @router.get("/api/workbench/library/stats")
    async def wb_library_stats(workspace: str = ""):
        try:
            return await library.get_stats(await _ensure_kb_db(workspace))
        except Exception:
            logger.exception("Failed to load literature stats for %s", workspace)
            return error_response("Stats failed", 500, "library_stats_failed")

    @router.get("/api/workbench/library/tags")
    async def wb_library_tags(workspace: str = ""):
        try:
            return {"tags": await library.list_tags(await _ensure_kb_db(workspace))}
        except Exception:
            logger.exception("Failed to list literature tags for %s", workspace)
            return error_response("Tags failed", 500, "library_tags_failed")

    @router.get("/api/workbench/library/collections")
    async def wb_library_collections(workspace: str = ""):
        try:
            return {"collections": await library.list_collections(await _ensure_kb_db(workspace))}
        except Exception:
            logger.exception("Failed to list literature collections for %s", workspace)
            return error_response("Collections failed", 500, "library_collections_failed")

    @router.post("/api/workbench/library/collections")
    async def wb_create_library_collection(body: dict[str, Any], workspace: str = ""):
        if not str(body.get("name") or "").strip():
            return JSONResponse({"error": "name is required"}, status_code=400)
        try:
            return await library.create_collection(await _ensure_kb_db(workspace), body)
        except Exception:
            logger.exception("Failed to create literature collection")
            return error_response("Create failed", 500, "library_collection_create_failed")

    @router.patch("/api/workbench/library/collections/{collection_id}")
    async def wb_update_library_collection(
        collection_id: str, body: dict[str, Any], workspace: str = ""
    ):
        try:
            value = await library.update_collection(await _ensure_kb_db(workspace), collection_id, body)
            return value or JSONResponse({"error": "not found"}, status_code=404)
        except Exception:
            logger.exception("Failed to update literature collection %s", collection_id)
            return error_response("Update failed", 500, "library_collection_update_failed")

    @router.delete("/api/workbench/library/collections/{collection_id}")
    async def wb_delete_library_collection(collection_id: str, workspace: str = ""):
        try:
            return {"ok": await library.delete_collection(await _ensure_kb_db(workspace), collection_id)}
        except Exception:
            logger.exception("Failed to delete literature collection %s", collection_id)
            return error_response("Delete failed", 500, "library_collection_delete_failed")

    @router.post("/api/workbench/library/items")
    async def wb_create_library_item(body: dict[str, Any], workspace: str = ""):
        try:
            return await library.create_item(await _ensure_kb_db(workspace), body)
        except Exception:
            logger.exception("Failed to create literature item")
            return error_response("Create failed", 500, "library_create_failed")

    @router.post("/api/workbench/library/items/batch-delete")
    async def wb_delete_library_items(body: dict[str, Any], workspace: str = ""):
        raw_item_ids = body.get("item_ids")
        if not isinstance(raw_item_ids, list):
            return JSONResponse({"error": "item_ids must be a list"}, status_code=400)
        item_ids = list(
            dict.fromkeys(str(item_id or "").strip() for item_id in raw_item_ids)
        )
        item_ids = [item_id for item_id in item_ids if item_id]
        if not item_ids:
            return JSONResponse({"error": "item_ids is required"}, status_code=400)
        if len(item_ids) > 500:
            return JSONResponse(
                {"error": "at most 500 items can be deleted at once"},
                status_code=400,
            )
        permanent = bool(_bool_param(body.get("permanent")))
        try:
            deleted = await library.delete_items(
                await _ensure_kb_db(workspace),
                item_ids,
                permanent=permanent,
            )
            return {"ok": True, "deleted": deleted}
        except Exception:
            logger.exception("Failed to delete %s literature items", len(item_ids))
            return error_response("Delete failed", 500, "library_delete_failed")

    @router.get("/api/workbench/library/items/{item_id}")
    async def wb_get_library_item(item_id: str, workspace: str = ""):
        try:
            db_path = await _ensure_kb_db(workspace)
            item = await library.get_item(db_path, item_id, include_deleted=True)
            if not item:
                return JSONResponse({"error": "not found"}, status_code=404)
            indexed_text, truncated = await _indexed_item_text(db_path, item_id)
            item["indexed_text"] = indexed_text
            item["indexed_text_truncated"] = truncated
            return item
        except Exception:
            logger.exception("Failed to load literature item %s", item_id)
            return error_response("Get failed", 500, "library_get_failed")

    @router.patch("/api/workbench/library/items/{item_id}")
    async def wb_update_library_item(
        item_id: str, body: dict[str, Any], workspace: str = ""
    ):
        try:
            item = await library.update_item(await _ensure_kb_db(workspace), item_id, body)
            return item or JSONResponse({"error": "not found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            logger.exception("Failed to update literature item %s", item_id)
            return error_response("Update failed", 500, "library_update_failed")

    @router.post("/api/workbench/library/read")
    async def wb_library_mark_read(body: dict[str, Any], workspace: str = ""):
        try:
            db_path = await _ensure_kb_db(workspace)
            item_ids = await _viewed_item_ids(
                db_path,
                attachment_url=str(body.get("attachment_url") or ""),
                file_name=str(body.get("file_name") or ""),
            )
            updated = []
            for item_id in item_ids:
                item = await library.update_item(
                    db_path, item_id, {"reading_status": "read"}
                )
                if item:
                    updated.append(item)
            return {"ok": True, "updated": len(updated), "items": updated}
        except Exception:
            logger.exception("Failed recording literature read event")
            return error_response(
                "Read event failed", 500, "library_read_event_failed"
            )

    @router.delete("/api/workbench/library/items/{item_id}")
    async def wb_delete_library_item(
        item_id: str, workspace: str = "", permanent: bool = False
    ):
        try:
            return {"ok": await library.delete_item(await _ensure_kb_db(workspace), item_id, permanent=permanent)}
        except Exception:
            logger.exception("Failed to delete literature item %s", item_id)
            return error_response("Delete failed", 500, "library_delete_failed")

    @router.post("/api/workbench/library/items/{item_id}/restore")
    async def wb_restore_library_item(item_id: str, workspace: str = ""):
        try:
            item = await library.restore_item(await _ensure_kb_db(workspace), item_id)
            return item or JSONResponse({"error": "not found"}, status_code=404)
        except Exception:
            logger.exception("Failed to restore literature item %s", item_id)
            return error_response("Restore failed", 500, "library_restore_failed")

    @router.post("/api/workbench/library/items/{item_id}/notes")
    async def wb_create_library_note(
        item_id: str, body: dict[str, Any], workspace: str = ""
    ):
        try:
            db_path = await _ensure_kb_db(workspace)
            if not await library.get_item(db_path, item_id):
                return JSONResponse({"error": "item not found"}, status_code=404)
            return await library.create_note(db_path, item_id, body)
        except Exception:
            logger.exception("Failed to create note for %s", item_id)
            return error_response("Create note failed", 500, "library_note_create_failed")

    @router.patch("/api/workbench/library/notes/{note_id}")
    async def wb_update_library_note(
        note_id: str, body: dict[str, Any], workspace: str = ""
    ):
        try:
            note = await library.update_note(await _ensure_kb_db(workspace), note_id, body)
            return note or JSONResponse({"error": "not found"}, status_code=404)
        except Exception:
            logger.exception("Failed to update literature note %s", note_id)
            return error_response("Update note failed", 500, "library_note_update_failed")

    @router.delete("/api/workbench/library/notes/{note_id}")
    async def wb_delete_library_note(note_id: str, workspace: str = ""):
        try:
            return {"ok": await library.delete_note(await _ensure_kb_db(workspace), note_id)}
        except Exception:
            logger.exception("Failed to delete literature note %s", note_id)
            return error_response("Delete note failed", 500, "library_note_delete_failed")

    @router.post("/api/workbench/library/relations")
    async def wb_create_library_relation(body: dict[str, Any], workspace: str = ""):
        if not body.get("src_item_id") or not body.get("dst_item_id"):
            return JSONResponse({"error": "src_item_id and dst_item_id are required"}, status_code=400)
        try:
            return await library.create_relation(await _ensure_kb_db(workspace), body)
        except Exception:
            logger.exception("Failed to create literature relation")
            return error_response("Create relation failed", 500, "library_relation_create_failed")

    @router.delete("/api/workbench/library/relations/{relation_id}")
    async def wb_delete_library_relation(relation_id: str, workspace: str = ""):
        try:
            return {"ok": await library.delete_relation(await _ensure_kb_db(workspace), relation_id)}
        except Exception:
            logger.exception("Failed to delete literature relation %s", relation_id)
            return error_response("Delete relation failed", 500, "library_relation_delete_failed")

    @router.post("/api/workbench/library/import")
    async def wb_import_library(body: dict[str, Any], workspace: str = ""):
        items = body.get("items")
        if not isinstance(items, list):
            return JSONResponse({"error": "items must be a list"}, status_code=400)
        try:
            db_path = await _ensure_kb_db(workspace)
            zotero_items = [
                value for value in items if isinstance(value, dict) and (
                    isinstance(value.get("data"), dict) or value.get("provider") == "zotero"
                    or "itemType" in value
                )
            ]
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
        except Exception:
            logger.exception("Failed to import literature")
            return error_response("Import failed", 500, "library_import_failed")

    @router.post("/api/workbench/library/upload")
    async def wb_upload_library(
        files: list[UploadFile], workspace: str = "", item_id: str = ""
    ):
        if not files:
            return JSONResponse({"error": "no files uploaded"}, status_code=400)
        try:
            db_path = await _ensure_kb_db(workspace)
            items: list[dict[str, Any]] = []
            for file in files:
                content = await file.read()
                try:
                    parsed = bibliography.parse(file.filename or "", content)
                except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    return JSONResponse(
                        {"error": f"无法解析 {file.filename or 'bibliography'}: {exc}"}, status_code=400
                    )
                if parsed is not None:
                    if item_id:
                        return JSONResponse(
                            {"error": "item_id can only be used when uploading an attachment"},
                            status_code=400,
                        )
                    if not parsed:
                        return JSONResponse(
                            {"error": f"{file.filename or 'bibliography'} 中没有可导入的文献"},
                            status_code=400,
                        )
                    for payload in parsed:
                        items.append(await library.create_item(db_path, payload))
                    continue
                await file.seek(0)
                items.append(await _upload_one(db_path, workspace, file, item_id))
            return {"items": items}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except Exception:
            logger.exception("Failed to upload literature")
            return error_response("Upload failed", 500, "library_upload_failed")

    @router.get("/api/workbench/library/search")
    async def wb_search_library(workspace: str = "", q: str = "", k: int = 20):
        try:
            if not q.strip():
                return {"items": [], "total": 0, "document_results": []}
            db_path = await _ensure_kb_db(workspace)
            items, total = await library.list_items(db_path, q=q, limit=max(1, min(k, 100)))
            document_results = await retrieve.search_knowledge(db_path, q, k=max(1, min(k, 50)))
            if document_results:
                document_ids = [str(value.get("document_id") or "") for value in document_results]
                placeholders = ",".join("?" for _ in document_ids)
                async with aiosqlite.connect(db_path, timeout=30) as db:
                    cursor = await db.execute(
                        f"SELECT kb_document_id,item_id FROM library_attachments WHERE kb_document_id IN ({placeholders})",
                        document_ids,
                    )
                    item_by_doc = {str(row[0]): str(row[1]) for row in await cursor.fetchall()}
                for result in document_results:
                    result["library_item_id"] = item_by_doc.get(str(result.get("document_id") or ""))
                document_results = [value for value in document_results if value.get("library_item_id")]
            return {"items": items, "total": total, "document_results": document_results}
        except Exception:
            logger.exception("Failed to search literature")
            return error_response("Search failed", 500, "library_search_failed")

    @router.get("/api/workbench/library/items/{item_id}/citation")
    async def wb_library_citation(item_id: str, workspace: str = "", style: str = "ieee"):
        try:
            item = await library.get_item(await _ensure_kb_db(workspace), item_id)
            if not item:
                return JSONResponse({"error": "not found"}, status_code=404)
            return {
                "citation": library.render_citation(item, style),
                "bibtex": library.render_bibtex(item),
                "citekey": item.get("citekey") or item.get("provider_item_key") or item["id"],
                "style": style,
            }
        except Exception:
            logger.exception("Failed to cite literature item %s", item_id)
            return error_response("Citation failed", 500, "library_citation_failed")

    @router.get("/api/workbench/library/items/{item_id}/raw")
    async def wb_library_raw(item_id: str, workspace: str = ""):
        try:
            db_path = await _ensure_kb_db(workspace)
            if not await library.get_item(db_path, item_id):
                return JSONResponse({"error": "not found"}, status_code=404)
            attachment = await _find_raw_attachment(db_path, item_id)
            if not attachment:
                return JSONResponse({"error": "no attachment"}, status_code=404)
            # Raw access is only allowed through a project knowledge-document
            # link.  Never serve a path supplied as untrusted import metadata.
            path = Path(str(attachment.get("document_path") or ""))
            if not path.is_file():
                return JSONResponse({"error": "attachment unavailable"}, status_code=404)
            return FileResponse(
                str(path), media_type=str(
                    attachment.get("document_content_type") or attachment.get("content_type")
                    or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
                ), filename=str(attachment.get("filename") or path.name),
                content_disposition_type="inline",
            )
        except Exception:
            logger.exception("Failed raw access for literature item %s", item_id)
            return error_response("Raw access failed", 500, "library_raw_failed")

    @router.get("/api/workbench/library/zotero/status")
    async def wb_zotero_status(workspace: str = ""):
        try:
            db_path = await _ensure_kb_db(workspace)
            config = _zotero_config()
            status = await _zotero_client(config).status()
            status["sync_sources"] = await library.get_sync_state(db_path)
            status["auto_sync"] = bool(config.get("auto_sync", False))
            status["copy_attachments"] = bool(config.get("copy_attachments", True))
            status["default_library_id"] = "0"
            status["default_library_type"] = "user"
            return status
        except zotero.ZoteroLocalError as exc:
            return {"available": False, "error": str(exc), "sync_sources": []}
        except Exception:
            logger.exception("Failed to probe Zotero Local API")
            return error_response("Zotero probe failed", 500, "zotero_status_failed")

    @router.get("/api/workbench/library/zotero/collections")
    async def wb_zotero_collections(
        workspace: str = "", library_id: str = "0", library_type: str = "user"
    ):
        del workspace  # retained for a consistent frontend contract
        try:
            records, version = await _zotero_client().collections(library_id, library_type)
            return {"collections": records, "library_version": version}
        except zotero.ZoteroLocalError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        except Exception:
            logger.exception("Failed to list Zotero collections")
            return error_response("Zotero collections failed", 500, "zotero_collections_failed")

    @router.post("/api/workbench/library/zotero/sync")
    async def wb_zotero_sync(body: dict[str, Any], workspace: str = ""):
        try:
            db_path = await _ensure_kb_db(workspace)
            config = _zotero_config()
            since = body.get("since")
            return await zotero.sync(
                db_path, _zotero_client(config),
                library_id=str(body.get("library_id") or "0"),
                library_type=str(body.get("library_type") or "user"),
                collection_key=str(body.get("collection_key") or ""),
                since=int(since) if since not in (None, "") else None,
                copy_attachments=bool(config.get("copy_attachments", True)),
            )
        except zotero.ZoteroLocalError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        except Exception:
            logger.exception("Failed to sync Zotero")
            return error_response("Zotero sync failed", 500, "zotero_sync_failed")


__all__ = ["register_workbench_library_routes"]
