from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.workbench import pinned_resources
from route.workbench.chat_routes.context import ChatRouteContext


def _resolve_pinned_file_path(body: dict[str, Any]) -> None:
    from pathlib import Path
    from urllib.parse import unquote, urlparse

    from cyrene.runtime.attachments import EXPORTS_DIR, UPLOADS_DIR

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
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "object body required"}, status_code=400)
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
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, "resource": _public_pinned_resource(item)}

    @router.delete("/api/workbench/pinned-resources/{resource_id}")
    async def api_workbench_unpin_resource(resource_id: str):
        removed = await asyncio.to_thread(pinned_resources.remove_resource, resource_id)
        if not removed:
            return JSONResponse({"error": "resource not found"}, status_code=404)
        return {"ok": True}
