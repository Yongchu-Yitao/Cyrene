"""FastAPI contract consumed by the existing Workbench knowledge frontend."""

from __future__ import annotations

from typing import Any, Awaitable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from cyrene.localization import localized

from .service import (
    KnowledgeService,
    WorkspaceNotFoundError,
    WorkspaceRequiredError,
)
from .zotero import ZoteroError


def _bool(value: str | bool | None) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).casefold() in {"1", "true", "yes", "on"}


def _json_error(code: str, english: str, chinese: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"error": localized(english, chinese), "code": code},
        status_code=status_code,
    )


def _not_found() -> JSONResponse:
    return _json_error(
        "knowledge_item_not_found",
        "The knowledge item was not found.",
        "未找到知识库条目。",
        404,
    )


async def _call(awaitable: Awaitable[Any]) -> Any:
    try:
        return await awaitable
    except WorkspaceRequiredError as exc:
        raise HTTPException(
            status_code=400,
            detail=localized("A workspace is required.", "必须指定工作区。"),
        ) from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=localized("The workspace was not found.", "未找到工作区。"),
        ) from exc
    except ValueError:
        return JSONResponse({
            "error": localized("The knowledge request is invalid.", "知识库请求无效。"),
            "code": "invalid_knowledge_request",
        }, status_code=400)
    except LookupError:
        return JSONResponse({
            "error": localized("The knowledge item was not found.", "未找到知识库条目。"),
            "code": "knowledge_item_not_found",
        }, status_code=404)
    except PermissionError:
        return JSONResponse({
            "error": localized("Access to the knowledge item was denied.", "无权访问该知识库条目。"),
            "code": "knowledge_access_denied",
        }, status_code=403)


def register_routes(parent: APIRouter, service: KnowledgeService) -> None:
    def require_workspace(workspace: str = "") -> None:
        try:
            service.resolve_workspace(workspace)
        except WorkspaceRequiredError as exc:
            raise HTTPException(
                status_code=400,
                detail=localized("A workspace is required.", "必须指定工作区。"),
            ) from exc
        except WorkspaceNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=localized("The workspace was not found.", "未找到工作区。"),
            ) from exc

    router = APIRouter(dependencies=[Depends(require_workspace)])

    @router.get("/api/workbench/library/items")
    async def list_items(
        workspace: str = "",
        q: str = "",
        collection: str = "",
        status: str = "",
        tag: str = "",
        item_type: str = "",
        file_type: str = "",
        year: int | None = None,
        starred: str | None = None,
        trash: bool = False,
        sort: str = "updated_at",
        order: str = "desc",
        limit: int = 200,
        offset: int = 0,
    ):
        return await service.items(
            workspace,
            q=q,
            collection=collection,
            status=status,
            tag=tag,
            item_type=item_type,
            file_type=file_type,
            year=year,
            starred=_bool(starred),
            trash=trash,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

    @router.post("/api/workbench/library/items")
    async def create_item(body: dict[str, Any], workspace: str = ""):
        return await _call(service.create_item(workspace, body))

    @router.post("/api/workbench/library/items/batch-delete")
    async def delete_items(body: dict[str, Any], workspace: str = ""):
        raw_ids = body.get("item_ids")
        if not isinstance(raw_ids, list):
            return _json_error(
                "invalid_item_ids",
                "Item IDs must be a list.",
                "条目 ID 必须是列表。",
                400,
            )
        item_ids = [value for value in dict.fromkeys(str(value or "").strip() for value in raw_ids) if value]
        if not item_ids:
            return _json_error(
                "item_ids_required",
                "At least one item ID is required.",
                "至少需要一个条目 ID。",
                400,
            )
        if len(item_ids) > 500:
            return _json_error(
                "too_many_item_ids",
                "At most 500 items can be deleted at once.",
                "一次最多可以删除 500 个条目。",
                400,
            )
        deleted = await service.delete_items(workspace, item_ids, permanent=bool(_bool(body.get("permanent"))))
        return {"ok": True, "deleted": deleted}

    @router.get("/api/workbench/library/items/{item_id}")
    async def get_item(item_id: str, workspace: str = ""):
        value = await service.get_item(workspace, item_id)
        return value or _not_found()

    @router.patch("/api/workbench/library/items/{item_id}")
    async def update_item(item_id: str, body: dict[str, Any], workspace: str = ""):
        value = await _call(service.update_item(workspace, item_id, body))
        if isinstance(value, JSONResponse):
            return value
        return value or _not_found()

    @router.delete("/api/workbench/library/items/{item_id}")
    async def delete_item(item_id: str, workspace: str = "", permanent: bool = False):
        return {"ok": await service.delete_item(workspace, item_id, permanent=bool(permanent))}

    @router.post("/api/workbench/library/items/{item_id}/restore")
    async def restore_item(item_id: str, workspace: str = ""):
        value = await service.restore_item(workspace, item_id)
        return value or _not_found()

    @router.post("/api/workbench/library/read")
    async def mark_read(body: dict[str, Any], workspace: str = ""):
        return await service.mark_read(
            workspace,
            attachment_url=str(body.get("attachment_url") or ""),
            file_name=str(body.get("file_name") or ""),
        )

    @router.get("/api/workbench/library/stats")
    async def stats(workspace: str = ""):
        return await service.stats(workspace)

    @router.get("/api/workbench/library/tags")
    async def tags(workspace: str = ""):
        return {"tags": await service.tags(workspace)}

    @router.get("/api/workbench/library/collections")
    async def collections(workspace: str = ""):
        return {"collections": await service.collections(workspace)}

    @router.post("/api/workbench/library/collections")
    async def create_collection(body: dict[str, Any], workspace: str = ""):
        return await _call(service.create_collection(workspace, body))

    @router.patch("/api/workbench/library/collections/{collection_id}")
    async def update_collection(collection_id: str, body: dict[str, Any], workspace: str = ""):
        value = await _call(service.update_collection(workspace, collection_id, body))
        if isinstance(value, JSONResponse):
            return value
        return value or _not_found()

    @router.delete("/api/workbench/library/collections/{collection_id}")
    async def delete_collection(collection_id: str, workspace: str = ""):
        return {"ok": await service.delete_collection(workspace, collection_id)}

    @router.post("/api/workbench/library/items/{item_id}/notes")
    async def create_note(item_id: str, body: dict[str, Any], workspace: str = ""):
        value = await service.create_note(workspace, item_id, body)
        return value or _not_found()

    @router.patch("/api/workbench/library/notes/{note_id}")
    async def update_note(note_id: str, body: dict[str, Any], workspace: str = ""):
        value = await service.update_note(workspace, note_id, body)
        return value or _not_found()

    @router.delete("/api/workbench/library/notes/{note_id}")
    async def delete_note(note_id: str, workspace: str = ""):
        return {"ok": await service.delete_note(workspace, note_id)}

    @router.post("/api/workbench/library/relations")
    async def create_relation(body: dict[str, Any], workspace: str = ""):
        return await _call(service.create_relation(workspace, body))

    @router.delete("/api/workbench/library/relations/{relation_id}")
    async def delete_relation(relation_id: str, workspace: str = ""):
        return {"ok": await service.delete_relation(workspace, relation_id)}

    @router.post("/api/workbench/library/import")
    async def import_records(body: dict[str, Any], workspace: str = ""):
        return await _call(service.import_records(workspace, body))

    @router.post("/api/workbench/library/upload")
    async def upload(
        workspace: str = "",
        item_id: str = "",
        files: list[UploadFile] = File(...),
    ):
        if not files:
            return _json_error(
                "files_required",
                "Upload at least one file.",
                "请至少上传一个文件。",
                400,
            )
        value = await _call(service.upload(workspace, files, item_id))
        return value if isinstance(value, JSONResponse) else {"items": value}

    @router.get("/api/workbench/library/search")
    async def search(workspace: str = "", q: str = "", k: int = 20):
        return await service.search(workspace, q, k)

    @router.get("/api/workbench/library/items/{item_id}/citation")
    async def citation(item_id: str, workspace: str = "", style: str = "ieee"):
        value = await service.citation(workspace, item_id, style)
        return value or _not_found()

    @router.get("/api/workbench/library/items/{item_id}/raw")
    async def raw(item_id: str, workspace: str = ""):
        value = await _call(service.raw(workspace, item_id))
        if isinstance(value, JSONResponse):
            return value
        if value is None:
            return _not_found()
        return FileResponse(
            str(value["path"]),
            media_type=str(value["media_type"]),
            filename=str(value["filename"]),
            content_disposition_type="inline",
        )

    @router.get("/api/workbench/library/items/{item_id}/attachments/{attachment_id}/raw")
    async def raw_attachment(
        item_id: str,
        attachment_id: str,
        workspace: str = "",
    ):
        value = await _call(service.raw(workspace, item_id, attachment_id))
        if isinstance(value, JSONResponse):
            return value
        if value is None:
            return _not_found()
        return FileResponse(
            str(value["path"]),
            media_type=str(value["media_type"]),
            filename=str(value["filename"]),
            content_disposition_type="inline",
        )

    @router.get("/api/workbench/library/embedding/status")
    async def embedding_status(workspace: str = ""):
        return await service.embedding_status(workspace)

    @router.post("/api/workbench/library/reembed")
    async def reembed(workspace: str = ""):
        return await service.reembed(workspace)

    @router.get("/api/workbench/library/zotero/status")
    async def zotero_status(workspace: str = ""):
        try:
            return await service.zotero_status(workspace)
        except ZoteroError as exc:
            return {
                "available": False,
                "error": str(exc),
                "code": "zotero_unavailable",
                "sync_sources": [],
            }

    @router.get("/api/workbench/library/zotero/collections")
    async def zotero_collections(workspace: str = "", library_id: str = "0", library_type: str = "user"):
        del workspace
        try:
            return await service.zotero_collections(library_id, library_type)
        except ZoteroError as exc:
            return JSONResponse({
                "error": str(exc),
                "code": "zotero_unavailable",
            }, status_code=503)

    @router.post("/api/workbench/library/zotero/sync")
    async def zotero_sync(body: dict[str, Any], workspace: str = ""):
        try:
            return await service.zotero_sync(workspace, body)
        except ZoteroError as exc:
            return JSONResponse({
                "error": str(exc),
                "code": "zotero_unavailable",
            }, status_code=503)

    parent.include_router(router)


__all__ = ["register_routes"]
