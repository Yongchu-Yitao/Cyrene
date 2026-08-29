"""Loopback-only Zotero Local API adapter owned by the knowledge Plugin."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

import httpx

from cyrene.localization import localized


class ZoteroError(RuntimeError):
    pass


class ZoteroClient:
    def __init__(self, base_url: str, *, timeout: float = 12.0) -> None:
        normalized = str(base_url or "http://127.0.0.1:23119/api").rstrip("/")
        parsed = urlsplit(normalized)
        hostname = str(parsed.hostname or "").casefold()
        loopback = hostname == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                loopback = False
        if parsed.scheme not in {"http", "https"} or not loopback:
            raise ZoteroError(localized(
                "Zotero Local API must use a loopback URL.",
                "Zotero Local API 必须使用本机回环地址。",
            ))
        self.base_url = normalized
        self.timeout = timeout

    @staticmethod
    def prefix(library_id: str, library_type: str) -> str:
        normalized_type = str(library_type or "user").casefold()
        normalized_id = str(library_id or "0").strip()
        if normalized_type not in {"user", "group"}:
            raise ZoteroError(localized(
                "The Zotero library type must be user or group.",
                "Zotero 文献库类型必须是 user 或 group。",
            ))
        if not normalized_id.isdigit():
            raise ZoteroError(localized(
                "The Zotero library ID must be numeric.",
                "Zotero 文献库 ID 必须是数字。",
            ))
        return f"groups/{normalized_id}" if normalized_type == "group" else f"users/{normalized_id}"

    async def request(self, path: str, params: Mapping[str, Any] | None = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/{path.lstrip('/')}",
                    params=dict(params or {}),
                    headers={"Zotero-API-Version": "3"},
                )
                response.raise_for_status()
                return response
        except httpx.ConnectError as exc:
            raise ZoteroError(localized(
                "Could not connect to Zotero. Start Zotero and enable its Local API in advanced settings.",
                "无法连接 Zotero。请启动 Zotero，并在高级设置中启用本地 API。",
            )) from exc
        except httpx.TimeoutException as exc:
            raise ZoteroError(localized(
                "The Zotero Local API request timed out.",
                "连接 Zotero Local API 超时。",
            )) from exc
        except httpx.HTTPStatusError as exc:
            raise ZoteroError(localized(
                "Zotero Local API returned HTTP {status}.",
                "Zotero Local API 返回 HTTP {status}。",
                status=exc.response.status_code,
            )) from exc
        except httpx.RequestError as exc:
            raise ZoteroError(localized(
                "The Zotero Local API request failed.",
                "Zotero Local API 请求失败。",
            )) from exc

    async def status(self) -> dict[str, Any]:
        response = await self.request("users/0/items", {"limit": 1, "format": "json"})
        return {
            "available": True,
            "base_url": self.base_url,
            "library_version": int(response.headers.get("Last-Modified-Version") or 0),
            "total_items": int(response.headers.get("Total-Results") or 0),
        }

    async def fetch_all(self, path: str, params: Mapping[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
        result: list[dict[str, Any]] = []
        start = 0
        version = 0
        while True:
            response = await self.request(
                path,
                {**dict(params or {}), "format": "json", "limit": 100, "start": start},
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ZoteroError(localized(
                    "Zotero Local API returned invalid JSON.",
                    "Zotero Local API 返回了无效 JSON。",
                )) from exc
            if not isinstance(payload, list):
                raise ZoteroError(localized(
                    "Zotero Local API returned unrecognized data.",
                    "Zotero Local API 返回了无法识别的数据。",
                ))
            page = [value for value in payload if isinstance(value, dict)]
            result.extend(page)
            version = max(version, int(response.headers.get("Last-Modified-Version") or 0))
            total_header = response.headers.get("Total-Results")
            total = int(total_header) if str(total_header or "").isdigit() else None
            start += len(page)
            if not page or len(page) < 100 or (total is not None and start >= total):
                return result, version

    async def collections(self, library_id: str, library_type: str) -> tuple[list[dict[str, Any]], int]:
        return await self.fetch_all(f"{self.prefix(library_id, library_type)}/collections")

    async def items(
        self,
        library_id: str,
        library_type: str,
        collection_key: str,
    ) -> tuple[list[dict[str, Any]], int]:
        prefix = self.prefix(library_id, library_type)
        if collection_key and not re.fullmatch(r"[A-Za-z0-9]+", collection_key):
            raise ZoteroError(localized(
                "The Zotero collection key contains invalid characters.",
                "Zotero 集合键包含无效字符。",
            ))
        path = f"{prefix}/collections/{collection_key}/items" if collection_key else f"{prefix}/items"
        return await self.fetch_all(path, {"include": "data", "includeTrashed": 1})


def _data(raw: Mapping[str, Any]) -> dict[str, Any]:
    return dict(raw.get("data")) if isinstance(raw.get("data"), Mapping) else dict(raw)


def _key(raw: Mapping[str, Any]) -> str:
    data = _data(raw)
    return str(raw.get("key") or data.get("key") or "")


def _item_payload(raw: Mapping[str, Any], library_id: str) -> dict[str, Any]:
    data = _data(raw)
    tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    return {
        "item_type": str(data.get("itemType") or "document"),
        "title": str(data.get("title") or "Untitled Zotero item"),
        "abstract": str(data.get("abstractNote") or ""),
        "doi": str(data.get("DOI") or ""),
        "isbn": str(data.get("ISBN") or ""),
        "url": str(data.get("url") or ""),
        "venue": str(data.get("publicationTitle") or data.get("conferenceName") or data.get("university") or ""),
        "publisher": str(data.get("publisher") or ""),
        "volume": str(data.get("volume") or ""),
        "issue": str(data.get("issue") or ""),
        "pages": str(data.get("pages") or ""),
        "language": str(data.get("language") or ""),
        "year": next(
            (int(part) for part in str(data.get("date") or "").replace("/", "-").split("-") if part.isdigit() and len(part) == 4),
            None,
        ),
        "date_text": str(data.get("date") or ""),
        "citekey": str(data.get("citationKey") or ""),
        "creators": list(data.get("creators") or []),
        "tags": [str(tag.get("tag") or "") if isinstance(tag, Mapping) else str(tag) for tag in tags],
        "provider": "zotero",
        "provider_library_id": library_id,
        "provider_item_key": _key(raw),
        "provider_version": int(raw.get("version") or data.get("version") or 0),
        "starred": bool(data.get("extra") and "starred" in str(data.get("extra")).casefold()),
    }


async def sync_zotero(
    service: Any,
    workspace: str,
    *,
    base_url: str,
    library_id: str,
    library_type: str,
    collection_key: str,
    copy_attachments: bool,
) -> dict[str, Any]:
    """Import the current Zotero snapshot into the new Plugin database."""

    client = ZoteroClient(base_url)
    collections, collection_version = await client.collections(library_id, library_type)
    records, item_version = await client.items(library_id, library_type, collection_key)

    collection_ids: dict[str, str] = {}
    for raw in collections:
        data = _data(raw)
        value = await asyncio.to_thread(
            service.store.upsert_provider_collection,
            workspace,
            {
                "name": str(data.get("name") or "Untitled collection"),
                "provider": "zotero",
                "provider_library_id": library_id,
                "provider_key": _key(raw),
                "provider_version": int(raw.get("version") or data.get("version") or 0),
            },
        )
        collection_ids[_key(raw)] = str(value["id"])
    for raw in collections:
        data = _data(raw)
        parent_key = str(data.get("parentCollection") or "")
        if not parent_key or parent_key not in collection_ids:
            continue
        await asyncio.to_thread(
            service.store.update_collection,
            workspace,
            collection_ids[_key(raw)],
            {"parent_id": collection_ids[parent_key]},
        )

    parents: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    for raw in records:
        data = _data(raw)
        item_type = str(data.get("itemType") or "")
        if item_type == "attachment":
            attachments.append(raw)
        elif item_type == "note":
            notes.append(raw)
        elif item_type == "annotation":
            annotations.append(raw)
        elif not data.get("parentItem"):
            parents.append(raw)

    created = updated = deleted = skipped = 0
    errors: list[dict[str, str]] = []
    parent_ids: dict[str, str] = {}
    imported_items: list[dict[str, Any]] = []
    for raw in parents:
        data = _data(raw)
        payload = _item_payload(raw, library_id)
        payload["_deleted"] = bool(data.get("deleted"))
        payload["collection_ids"] = [collection_ids[key] for key in data.get("collections") or [] if key in collection_ids]
        try:
            item, was_created = await asyncio.to_thread(service.store.upsert_provider_item, workspace, payload)
            parent_ids[_key(raw)] = str(item["id"])
            imported_items.append(item)
            created += int(was_created)
            updated += int(not was_created)
            if data.get("deleted"):
                deleted += await asyncio.to_thread(
                    service.store.delete_items,
                    workspace,
                    [str(item["id"])],
                    permanent=False,
                )
        except Exception as exc:
            skipped += 1
            errors.append({"key": _key(raw), "error": str(exc)})

    for raw in notes:
        data = _data(raw)
        parent_id = parent_ids.get(str(data.get("parentItem") or ""))
        if not parent_id:
            skipped += 1
            continue
        try:
            await asyncio.to_thread(
                service.store.create_note,
                workspace,
                parent_id,
                {
                    "title": "Zotero Note",
                    "content": str(data.get("note") or ""),
                    "provider": "zotero",
                    "provider_library_id": library_id,
                    "provider_key": _key(raw),
                },
            )
        except Exception as exc:
            skipped += 1
            errors.append({"key": _key(raw), "error": str(exc)})

    attachment_ids: dict[str, str] = {}
    attachment_parent_ids: dict[str, str] = {}
    for raw in attachments:
        data = _data(raw)
        parent_id = parent_ids.get(str(data.get("parentItem") or ""))
        if not parent_id:
            skipped += 1
            continue
        attachment_key = _key(raw)
        attachment_parent_ids[attachment_key] = parent_id
        if not copy_attachments:
            continue
        try:
            raw_path = str(data.get("path") or data.get("localPath") or "")
            if raw_path.casefold().startswith("file://"):
                raw_path = unquote(urlsplit(raw_path).path)
            source = Path(raw_path).expanduser()
            if not source.is_absolute() or not source.is_file():
                continue
            content = await asyncio.to_thread(source.read_bytes)
            item = await service._write_upload(
                workspace,
                str(data.get("filename") or source.name),
                content,
                str(data.get("contentType") or "application/octet-stream"),
                item_id=parent_id,
                provider="zotero",
                provider_library_id=library_id,
                provider_key=attachment_key,
            )
            hydrated_attachments = list(item.get("attachments") or [])
            if hydrated_attachments:
                matching = next(
                    (attachment for attachment in hydrated_attachments if str(attachment.get("filename") or "") == str(data.get("filename") or source.name)),
                    hydrated_attachments[-1],
                )
                attachment_ids[attachment_key] = str(matching["id"])
        except Exception as exc:
            skipped += 1
            errors.append({"key": _key(raw), "error": str(exc)})

    for raw in annotations:
        data = _data(raw)
        attachment_key = str(data.get("parentItem") or "")
        parent_id = attachment_parent_ids.get(attachment_key)
        if not parent_id:
            skipped += 1
            continue
        try:
            await asyncio.to_thread(
                service.store.add_annotation,
                workspace,
                parent_id,
                {
                    "attachment_id": attachment_ids.get(attachment_key),
                    "annotation_type": str(data.get("annotationType") or "highlight"),
                    "page_label": str(data.get("annotationPageLabel") or ""),
                    "quote": str(data.get("annotationText") or ""),
                    "comment": str(data.get("annotationComment") or ""),
                    "color": str(data.get("annotationColor") or ""),
                    "provider": "zotero",
                    "provider_library_id": library_id,
                    "provider_key": _key(raw),
                },
            )
        except Exception as exc:
            skipped += 1
            errors.append({"key": _key(raw), "error": str(exc)})

    version = max(collection_version, item_version)
    await asyncio.to_thread(
        service.store.set_sync_state,
        workspace,
        "zotero",
        library_id,
        collection_key,
        version=version,
        config={"library_type": library_type},
    )
    return {
        "imported": created + updated,
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
        "items": imported_items,
        "library_version": version,
        "collection_key": collection_key,
    }


__all__ = ["ZoteroClient", "ZoteroError", "sync_zotero"]
