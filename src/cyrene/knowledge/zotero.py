"""Read-only Zotero Desktop Local API client and project-library importer."""

from __future__ import annotations

import asyncio
import ipaddress
import mimetypes
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import aiosqlite
import httpx

from cyrene.knowledge import ingest, library, store


DEFAULT_BASE_URL = "http://127.0.0.1:23119/api"
CHILD_TYPES = {"attachment", "note", "annotation"}


class ZoteroLocalError(RuntimeError):
    """A friendly failure from an unavailable or incompatible local Zotero."""


class ZoteroLocalClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        parsed = urlsplit(normalized)
        hostname = str(parsed.hostname or "").lower()
        loopback = hostname == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                loopback = False
        if parsed.scheme not in {"http", "https"} or not loopback:
            raise ValueError("Zotero Local API must use a loopback URL")
        self.base_url = normalized
        self.timeout = timeout
        self.transport = transport

    @staticmethod
    def _prefix(library_id: str = "0", library_type: str = "user") -> str:
        if library_type == "group":
            return f"groups/{library_id}"
        return f"users/{library_id or '0'}"

    async def _request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.get(
                    f"{self.base_url}/{path.lstrip('/')}", params=params,
                    headers={"Zotero-API-Version": "3"},
                )
                response.raise_for_status()
                return response
        except httpx.ConnectError as exc:
            raise ZoteroLocalError(
                "无法连接 Zotero。请启动 Zotero，并在高级设置中启用本地 API。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ZoteroLocalError("连接 Zotero Local API 超时。") from exc
        except httpx.HTTPStatusError as exc:
            raise ZoteroLocalError(
                f"Zotero Local API 返回 HTTP {exc.response.status_code}。"
            ) from exc

    async def status(self) -> dict[str, Any]:
        response = await self._request("users/0/items", {"limit": 1, "format": "json"})
        return {
            "available": True,
            "base_url": self.base_url,
            "library_version": int(response.headers.get("Last-Modified-Version") or 0),
            "total_items": int(response.headers.get("Total-Results") or 0),
        }

    async def fetch_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        page_size: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        start = 0
        results: list[dict[str, Any]] = []
        version = 0
        while True:
            page_params = {**(params or {}), "format": "json", "limit": page_size, "start": start}
            response = await self._request(path, page_params)
            version = max(version, int(response.headers.get("Last-Modified-Version") or 0))
            payload = response.json()
            if not isinstance(payload, list):
                raise ZoteroLocalError("Zotero Local API 返回了无法识别的数据。")
            page = [value for value in payload if isinstance(value, dict)]
            results.extend(page)
            total_header = response.headers.get("Total-Results")
            total = int(total_header) if total_header and total_header.isdigit() else None
            start += len(page)
            if not page or len(page) < page_size or (total is not None and start >= total):
                break
        return results, version

    async def collections(
        self, library_id: str = "0", library_type: str = "user", since: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        params = {"since": since} if since > 0 else {}
        return await self.fetch_all(f"{self._prefix(library_id, library_type)}/collections", params)

    async def items(
        self,
        library_id: str = "0",
        library_type: str = "user",
        *,
        collection_key: str = "",
        since: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        prefix = self._prefix(library_id, library_type)
        path = f"{prefix}/collections/{collection_key}/items" if collection_key else f"{prefix}/items"
        params = {"include": "data", "includeTrashed": 1}
        if since > 0:
            params["since"] = since
        records, version = await self.fetch_all(path, params)
        if not collection_key:
            return records, version

        # Collection item lists contain top-level items; fetch their child
        # attachments/notes/annotations so a scoped import is still complete.
        children: list[dict[str, Any]] = []
        for record in records:
            data = record.get("data") if isinstance(record.get("data"), dict) else record
            if data.get("parentItem") or data.get("itemType") in CHILD_TYPES:
                continue
            key = str(record.get("key") or data.get("key") or "")
            if not key:
                continue
            page, child_version = await self.fetch_all(f"{prefix}/items/{key}/children", {"include": "data"})
            children.extend(page)
            version = max(version, child_version)
        by_key: dict[str, dict[str, Any]] = {}
        for value in [*records, *children]:
            data = value.get("data") if isinstance(value.get("data"), dict) else value
            key = str(value.get("key") or data.get("key") or "")
            if key:
                by_key[key] = value
        return list(by_key.values()), version

    async def attachment_path(
        self,
        key: str,
        library_id: str = "0",
        library_type: str = "user",
    ) -> Path | None:
        """Resolve an attachment via Zotero's Local-API-only file URL route."""
        response = await self._request(
            f"{self._prefix(library_id, library_type)}/items/{key}/file/view/url"
        )
        raw = response.text.strip().strip('"')
        parsed = urlsplit(raw)
        if parsed.scheme != "file":
            return None
        path = Path(unquote(parsed.path))
        return path if path.is_file() else None

    async def deleted(
        self, library_id: str = "0", library_type: str = "user", since: int = 0
    ) -> tuple[dict[str, list[str]], int]:
        response = await self._request(
            f"{self._prefix(library_id, library_type)}/deleted", {"since": max(0, since)}
        )
        payload = response.json()
        if not isinstance(payload, dict):
            payload = {}
        result = {
            key: [str(value) for value in values]
            for key, values in payload.items()
            if isinstance(values, list)
        }
        return result, int(response.headers.get("Last-Modified-Version") or 0)


def _zotero_data(raw: dict[str, Any]) -> dict[str, Any]:
    return raw.get("data") if isinstance(raw.get("data"), dict) else raw


def _zotero_key(raw: dict[str, Any]) -> str:
    data = _zotero_data(raw)
    return str(raw.get("key") or data.get("key") or "")


async def _collection_map(
    db_path: str, library_id: str
) -> dict[str, str]:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        cursor = await db.execute(
            """SELECT provider_key,id FROM library_collections
               WHERE provider='zotero' AND provider_library_id=?""",
            (library_id,),
        )
        return {str(row[0]): str(row[1]) for row in await cursor.fetchall()}


async def _provider_item_map(db_path: str, library_id: str) -> dict[str, str]:
    async with aiosqlite.connect(db_path, timeout=30) as db:
        cursor = await db.execute(
            """SELECT provider_item_key,id FROM library_items
               WHERE provider='zotero' AND provider_library_id=?""",
            (library_id,),
        )
        return {str(row[0]): str(row[1]) for row in await cursor.fetchall()}


async def _replace_zotero_memberships(
    db_path: str, item_id: str, collection_keys: list[str], mapping: dict[str, str],
    library_id: str,
) -> None:
    ids = [mapping[key] for key in collection_keys if key in mapping]
    now = library._now()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        # Keep Cyrene-managed collection membership, replace Zotero-managed membership.
        await db.execute(
            """DELETE FROM library_collection_items WHERE item_id=? AND collection_id IN
               (SELECT id FROM library_collections WHERE provider='zotero' AND provider_library_id=?)""",
            (item_id, library_id),
        )
        for collection_id in ids:
            await db.execute(
                "INSERT OR IGNORE INTO library_collection_items(collection_id,item_id,created_at) VALUES(?,?,?)",
                (collection_id, item_id, now),
            )
        await db.commit()


async def _upsert_note(
    db_path: str, item_id: str, raw: dict[str, Any], library_id: str
) -> None:
    data = _zotero_data(raw)
    key = _zotero_key(raw)
    async with aiosqlite.connect(db_path, timeout=30) as db:
        cursor = await db.execute(
            """SELECT id FROM library_notes WHERE provider='zotero'
               AND provider_library_id=? AND provider_key=?""", (library_id, key)
        )
        row = await cursor.fetchone()
    payload = {
        "provider": "zotero", "provider_library_id": library_id, "provider_key": key,
        "provider_version": int(raw.get("version") or data.get("version") or 0),
        "title": str(data.get("title") or "Zotero Note"), "content": str(data.get("note") or ""),
        "raw_json": raw,
    }
    if not row:
        await library.create_note(db_path, item_id, payload)
        return
    async with aiosqlite.connect(db_path, timeout=30) as db:
        await db.execute(
            """UPDATE library_notes SET item_id=?,provider_version=?,title=?,content=?,raw_json=?,updated_at=?
               WHERE id=?""",
            (item_id,payload["provider_version"],payload["title"],payload["content"],library._json(raw, {}),library._now(),row[0]),
        )
        await db.commit()


async def _upsert_annotation(
    db_path: str,
    item_id: str,
    attachment_id: str | None,
    raw: dict[str, Any],
    library_id: str,
) -> None:
    data = _zotero_data(raw)
    key = _zotero_key(raw)
    async with aiosqlite.connect(db_path, timeout=30) as db:
        cursor = await db.execute(
            """SELECT id FROM library_annotations WHERE provider='zotero'
               AND provider_library_id=? AND provider_key=?""", (library_id, key)
        )
        row = await cursor.fetchone()
    payload = {
        "attachment_id": attachment_id, "provider": "zotero",
        "provider_library_id": library_id, "provider_key": key,
        "provider_version": int(raw.get("version") or data.get("version") or 0),
        "annotation_type": str(data.get("annotationType") or "highlight"),
        "page_label": str(data.get("annotationPageLabel") or ""),
        "quote": str(data.get("annotationText") or ""),
        "comment": str(data.get("annotationComment") or ""),
        "color": str(data.get("annotationColor") or ""),
        "position_json": data.get("annotationPosition") if isinstance(data.get("annotationPosition"), dict) else {},
        "raw_json": raw,
    }
    if not row:
        await library.add_annotation(db_path, item_id, payload)
        return
    async with aiosqlite.connect(db_path, timeout=30) as db:
        await db.execute(
            """UPDATE library_annotations SET item_id=?,attachment_id=?,provider_version=?,annotation_type=?,
               page_label=?,quote=?,comment=?,color=?,position_json=?,raw_json=?,updated_at=? WHERE id=?""",
            (
                item_id,attachment_id,payload["provider_version"],payload["annotation_type"],payload["page_label"],
                payload["quote"],payload["comment"],payload["color"],library._json(payload["position_json"], {}),
                library._json(raw, {}),library._now(),row[0],
            ),
        )
        await db.commit()


async def import_records(
    db_path: str,
    items: list[dict[str, Any]],
    *,
    collections: list[dict[str, Any]] | None = None,
    library_id: str = "0",
    library_type: str = "user",
    client: ZoteroLocalClient | None = None,
    copy_attachments: bool = True,
) -> dict[str, Any]:
    """Import Zotero JSON, preserving provider keys for idempotent updates."""
    collections = collections or []
    # Upsert collection shells first, then resolve parent ids in a second pass.
    for raw in collections:
        data = _zotero_data(raw)
        await library.upsert_collection(db_path, {
            "provider": "zotero", "provider_library_id": library_id,
            "provider_key": _zotero_key(raw),
            "provider_version": int(raw.get("version") or data.get("version") or 0),
            "name": str(data.get("name") or "未命名收藏夹"), "raw_json": raw,
        })
    collection_ids = await _collection_map(db_path, library_id)
    for raw in collections:
        data = _zotero_data(raw)
        key = _zotero_key(raw)
        parent_id = collection_ids.get(str(data.get("parentCollection") or ""))
        if key in collection_ids:
            await library.update_collection(db_path, collection_ids[key], {"parent_id": parent_id})

    top_level = []
    children = []
    for raw in items:
        data = _zotero_data(raw)
        if data.get("parentItem") or data.get("itemType") in CHILD_TYPES:
            children.append(raw)
        else:
            top_level.append(raw)
    created = updated = skipped = 0
    errors: list[dict[str, str]] = []
    imported_items: list[dict[str, Any]] = []
    for raw in top_level:
        try:
            item, was_created = await library.upsert_zotero_item(db_path, raw, library_id)
            imported_items.append(item)
            created += int(was_created)
            updated += int(not was_created)
            data = _zotero_data(raw)
            await _replace_zotero_memberships(
                db_path, item["id"], [str(value) for value in data.get("collections") or []],
                collection_ids, library_id,
            )
            if _zotero_data(raw).get("deleted"):
                await library.delete_item(db_path, item["id"])
        except Exception as exc:
            skipped += 1
            errors.append({"key": _zotero_key(raw), "error": str(exc)})

    item_ids = await _provider_item_map(db_path, library_id)
    for raw in top_level:
        data = _zotero_data(raw)
        src_id = item_ids.get(_zotero_key(raw))
        relations = data.get("relations") if isinstance(data.get("relations"), dict) else {}
        if not src_id:
            continue
        for relation_name, raw_targets in relations.items():
            targets = raw_targets if isinstance(raw_targets, list) else [raw_targets]
            for raw_target in targets:
                target_key = str(raw_target or "").rstrip("/").rsplit("/", 1)[-1]
                dst_id = item_ids.get(target_key)
                if dst_id and dst_id != src_id:
                    await library.create_relation(db_path, {
                        "src_item_id": src_id, "dst_item_id": dst_id,
                        "relation": str(relation_name or "related"), "source": "zotero",
                    })
    attachment_by_key: dict[str, dict[str, Any]] = {}
    pending_annotations: list[dict[str, Any]] = []
    for raw in children:
        data = _zotero_data(raw)
        child_type = str(data.get("itemType") or "")
        parent_key = str(data.get("parentItem") or "")
        if child_type == "annotation":
            pending_annotations.append(raw)
            continue
        item_id = item_ids.get(parent_key)
        if not item_id:
            # Notes can occasionally be standalone; they are intentionally not
            # turned into fake bibliographic items.
            skipped += 1
            continue
        if child_type == "note":
            await _upsert_note(db_path, item_id, raw, library_id)
            continue
        if child_type != "attachment":
            continue
        path = str(data.get("path") or "")
        document_id = None
        content_hash = ""
        # Only a live Local API sync may turn Zotero attachment metadata into a
        # filesystem read.  JSON imported through the public API remains inert,
        # preventing a crafted payload from exposing an arbitrary local path.
        local_path = (
            Path(path).expanduser()
            if client and path and not path.startswith("attachments:")
            else None
        )
        if (not local_path or not local_path.is_absolute() or not local_path.is_file()) and client:
            try:
                local_path = await client.attachment_path(
                    _zotero_key(raw), library_id=library_id, library_type=library_type
                )
            except ZoteroLocalError as exc:
                errors.append({"key": _zotero_key(raw), "error": str(exc)})
        if local_path and local_path.is_absolute() and local_path.is_file() and copy_attachments:
            from cyrene.attachments import UPLOADS_DIR, safe_attachment_filename

            target_dir = UPLOADS_DIR / "zotero" / Path(db_path).stem
            target_dir.mkdir(parents=True, exist_ok=True)
            target_name = safe_attachment_filename(
                str(data.get("filename") or local_path.name), fallback_stem="zotero_attachment"
            )
            target = target_dir / f"{_zotero_key(raw)}_{target_name}"
            if not target.is_file() or store.content_hash_file(target) != store.content_hash_file(local_path):
                shutil.copy2(local_path, target)
            local_path = target
        if local_path and local_path.is_absolute() and local_path.is_file():
            content_hash = store.content_hash_file(local_path)
            doc = await store.upsert_document_by_path(
                db_path, path=str(local_path.resolve()), name=str(data.get("filename") or local_path.name),
                source="zotero", kind="pdf" if local_path.suffix.lower() == ".pdf" else "file",
                content_type=str(data.get("contentType") or mimetypes.guess_type(str(local_path))[0] or ""),
                size=local_path.stat().st_size, content_hash=content_hash,
                metadata={"zotero_item_key": _zotero_key(raw), "zotero_parent_key": parent_key},
            )
            document_id = doc.get("id")
            if doc.get("status") in {"pending", "error"}:
                asyncio.create_task(ingest.index_document(db_path, doc["id"]))
        attachment = await library.upsert_attachment(db_path, item_id, {
            "provider": "zotero", "provider_library_id": library_id,
            "provider_key": _zotero_key(raw),
            "provider_version": int(raw.get("version") or data.get("version") or 0),
            "kb_document_id": document_id, "title": str(data.get("title") or ""),
            "filename": str(data.get("filename") or ""), "path": path,
            "content_type": str(data.get("contentType") or ""),
            "link_mode": str(data.get("linkMode") or ""), "content_hash": content_hash,
            "raw_json": raw,
        })
        attachment_by_key[_zotero_key(raw)] = attachment

    # An annotation's parent is an attachment, not the bibliographic item.
    for raw in pending_annotations:
        data = _zotero_data(raw)
        attachment = attachment_by_key.get(str(data.get("parentItem") or ""))
        if not attachment:
            async with aiosqlite.connect(db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """SELECT * FROM library_attachments WHERE provider='zotero'
                       AND provider_library_id=? AND provider_key=?""",
                    (library_id, str(data.get("parentItem") or "")),
                )
                row = await cursor.fetchone()
                attachment = dict(row) if row else None
        if attachment:
            await _upsert_annotation(
                db_path, attachment["item_id"], attachment["id"], raw, library_id
            )
        else:
            skipped += 1

    return {
        "imported": created + updated,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "items": imported_items,
    }


async def _apply_deleted(
    db_path: str, library_id: str, deleted: dict[str, list[str]], version: int
) -> int:
    item_keys = deleted.get("items", [])
    collection_keys = deleted.get("collections", [])
    if not item_keys and not collection_keys:
        return 0
    now = library._now()
    async with aiosqlite.connect(db_path, timeout=30) as db:
        count = 0
        for key in item_keys:
            cursor = await db.execute(
                """UPDATE library_items SET deleted_at=?,updated_at=?
                   WHERE provider='zotero' AND provider_library_id=? AND provider_item_key=?""",
                (now, now, library_id, key),
            )
            count += cursor.rowcount
            cursor = await db.execute(
                """SELECT id FROM library_attachments WHERE provider='zotero'
                   AND provider_library_id=? AND provider_key=?""",
                (library_id, key),
            )
            attachment_row = await cursor.fetchone()
            if attachment_row:
                await db.execute(
                    "DELETE FROM library_annotations WHERE attachment_id=?", (attachment_row[0],)
                )
            for table in ("library_attachments", "library_notes", "library_annotations"):
                cursor = await db.execute(
                    f"""DELETE FROM {table} WHERE provider='zotero'
                        AND provider_library_id=? AND provider_key=?""",
                    (library_id, key),
                )
                count += cursor.rowcount
            await db.execute(
                """INSERT INTO library_sync_tombstones
                   (provider,provider_library_id,object_type,provider_key,version,deleted_at)
                   VALUES('zotero',?,'item',?,?,?)
                   ON CONFLICT(provider,provider_library_id,object_type,provider_key)
                   DO UPDATE SET version=excluded.version,deleted_at=excluded.deleted_at""",
                (library_id, key, version, now),
            )
            await db.execute(
                "DELETE FROM library_items_fts WHERE item_id IN (SELECT id FROM library_items WHERE provider='zotero' AND provider_library_id=? AND provider_item_key=?)",
                (library_id, key),
            )
        for key in collection_keys:
            cursor = await db.execute(
                """SELECT id FROM library_collections WHERE provider='zotero'
                   AND provider_library_id=? AND provider_key=?""",
                (library_id, key),
            )
            row = await cursor.fetchone()
            if row:
                collection_id = str(row[0])
                await db.execute(
                    "DELETE FROM library_collection_items WHERE collection_id=?",
                    (collection_id,),
                )
                await db.execute(
                    "UPDATE library_collections SET parent_id=NULL WHERE parent_id=?",
                    (collection_id,),
                )
                cursor = await db.execute(
                    "DELETE FROM library_collections WHERE id=?", (collection_id,)
                )
                count += cursor.rowcount
            await db.execute(
                """INSERT INTO library_sync_tombstones
                   (provider,provider_library_id,object_type,provider_key,version,deleted_at)
                   VALUES('zotero',?,'collection',?,?,?)
                   ON CONFLICT(provider,provider_library_id,object_type,provider_key)
                   DO UPDATE SET version=excluded.version,deleted_at=excluded.deleted_at""",
                (library_id, key, version, now),
            )
        await db.commit()
        return count


async def sync(
    db_path: str,
    client: ZoteroLocalClient,
    *,
    library_id: str = "0",
    library_type: str = "user",
    collection_key: str = "",
    since: int | None = None,
    copy_attachments: bool = True,
) -> dict[str, Any]:
    if since is None:
        states = await library.get_sync_state(db_path)
        matching = next(
            (
                value for value in states
                if value["provider_library_id"] == library_id and value["collection_key"] == collection_key
            ),
            None,
        )
        since = int(matching.get("last_library_version") or 0) if matching else 0
    try:
        collections, collection_version = await client.collections(library_id, library_type, since)
        records, item_version = await client.items(
            library_id, library_type, collection_key=collection_key, since=since
        )
        deleted, deleted_version = await client.deleted(library_id, library_type, since)
        summary = await import_records(
            db_path, records, collections=collections, library_id=library_id,
            library_type=library_type, client=client,
            copy_attachments=copy_attachments,
        )
        version = max(collection_version, item_version, deleted_version, since)
        summary["deleted"] = await _apply_deleted(db_path, library_id, deleted, version)
        summary["library_version"] = version
        summary["collection_key"] = collection_key
        await library.set_sync_state(
            db_path, provider="zotero", library_id=library_id,
            collection_key=collection_key, version=version,
            config={"library_type": library_type},
        )
        return summary
    except Exception as exc:
        await library.set_sync_state(
            db_path, provider="zotero", library_id=library_id,
            collection_key=collection_key, version=int(since or 0), error=str(exc),
            config={"library_type": library_type},
        )
        raise


__all__ = [
    "DEFAULT_BASE_URL", "ZoteroLocalError", "ZoteroLocalClient", "import_records", "sync",
]
