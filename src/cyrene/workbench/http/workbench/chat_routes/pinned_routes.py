from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request

from cyrene.workbench.chat import pinned_resources
from cyrene.workbench.http.errors import localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


def _resolve_pinned_file_path(body: dict[str, Any]) -> None:
    from pathlib import Path
    from urllib.parse import unquote, urlparse

    from cyrene.platform.attachments import EXPORTS_DIR, UPLOADS_DIR

    parsed = unquote(urlparse(str(body.get("url") or "")).path)
    roots = (
        ("/api/workbench/uploads/", UPLOADS_DIR),
        ("/api/workbench/exports/", EXPORTS_DIR),
    )
    for prefix, root in roots:
        if not parsed.startswith(prefix):
            continue
        candidate = (root / Path(parsed[len(prefix) :]).name).resolve()
        if candidate.exists() and candidate.is_file():
            body["path"] = str(candidate)
        return


def register_pinned_routes(router: APIRouter, context: ChatRouteContext) -> None:
    _resolve_library_file_payload = context.resolve_library_file_payload
    _public_pinned_resource = context.public_pinned_resource

    @router.get("/api/workbench/pinned-resources")
    async def api_workbench_pinned_resources():
        items = await asyncio.to_thread(pinned_resources.list_resources)
        return {"resources": [_public_pinned_resource(item) for item in items]}

    @router.post("/api/workbench/pinned-resources")
    async def api_workbench_pin_resource(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return localized_error_response(
                "The request body is not valid JSON.",
                "请求正文不是有效的 JSON。",
                400,
                "invalid_json",
            )
        if not isinstance(body, dict):
            return localized_error_response(
                "The request body must be an object.",
                "请求正文必须是对象。",
                400,
                "object_body_required",
            )
        if str(body.get("kind") or "") == "file":
            body = await _resolve_library_file_payload(body)
            file_payload = body.get("file") if isinstance(body.get("file"), dict) else {}
            for key in ("name", "path", "url", "content_type", "size"):
                if not body.get(key) and file_payload.get(key) is not None:
                    body[key] = file_payload.get(key)
            if not body.get("path"):
                _resolve_pinned_file_path(body)
        try:
            item = await asyncio.to_thread(pinned_resources.upsert_resource, body)
        except ValueError:
            logger.warning("Invalid pinned-resource request", exc_info=True)
            return localized_error_response(
                "The pinned resource is invalid.",
                "置顶资源无效。",
                400,
                "invalid_pinned_resource",
            )
        return {"ok": True, "resource": _public_pinned_resource(item)}

    @router.delete("/api/workbench/pinned-resources/{resource_id}")
    async def api_workbench_unpin_resource(resource_id: str):
        removed = await asyncio.to_thread(pinned_resources.remove_resource, resource_id)
        if not removed:
            return localized_error_response(
                "Resource not found.", "未找到资源。", 404, "resource_not_found"
            )
        return {"ok": True}
