from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from cyrene.workbench.workspace_changes import (
    get_chat_file_change,
    list_chat_change_sets,
)
from route.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


def register_file_routes(router: APIRouter, context: ChatRouteContext) -> None:
    service = context.service
    _routes = context.runtime
    _project_data_key = context.project_data_key
    _resolve_library_file_payload = context.resolve_library_file_payload
    _public_pinned_resource = context.public_pinned_resource
    _find_chat = service.repository.find
    _read_chats_store = service.repository.read
    _resolve_chat_workspace_dir = service.resolve_chat_workspace_dir

    @router.get("/api/workbench/chats/{chat_id}/changes")
    async def api_workbench_chat_changes(chat_id: str):
        """Return durable run-scoped workspace changes without consulting Git."""
        payload = await asyncio.to_thread(_read_chats_store)
        if not _find_chat(payload, chat_id):
            return JSONResponse({"error": "chat not found"}, status_code=404)
        change_sets = await asyncio.to_thread(list_chat_change_sets, service.db_path, chat_id)
        return {
            "changeSets": change_sets,
            "fileCount": sum(int(item.get("fileCount") or 0) for item in change_sets),
            "additions": sum(int(item.get("additions") or 0) for item in change_sets),
            "deletions": sum(int(item.get("deletions") or 0) for item in change_sets),
        }

    @router.get("/api/workbench/chats/{chat_id}/changes/{change_set_id}/files/{file_path:path}")
    async def api_workbench_chat_change_diff(chat_id: str, change_set_id: str, file_path: str):
        """Return the immutable diff recorded for one file in one agent run."""
        payload = await asyncio.to_thread(_read_chats_store)
        if not _find_chat(payload, chat_id):
            return JSONResponse({"error": "chat not found"}, status_code=404)
        change = await asyncio.to_thread(
            get_chat_file_change,
            service.db_path,
            chat_id,
            change_set_id,
            file_path,
        )
        if change is None:
            return JSONResponse({"error": "file change not found"}, status_code=404)
        return {"change": change}

    @router.get("/api/workbench/chats/{chat_id}/files/{file_path:path}")
    async def api_workbench_chat_file(chat_id: str, file_path: str):
        """Preview/download a tracked agent file inside the chat's workspace."""
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        normalized = str(file_path or "").strip().replace("\\", "/")
        tracked = next(
            (item for item in (chat.get("generatedFiles") or []) if isinstance(item, dict) and str(item.get("path") or "").replace("\\", "/") == normalized),
            None,
        )
        if not tracked:
            return JSONResponse({"error": "file not found"}, status_code=404)
        R = _routes()
        project_store = await asyncio.to_thread(R.read_store)
        project = R.find_project(project_store, str(chat.get("projectId") or ""))
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        try:
            workspace_dir = _resolve_chat_workspace_dir(chat, project, R.resolve_workspace_dir)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        root = Path(workspace_dir).expanduser().resolve()
        try:
            target = (root / normalized).resolve()
            target.relative_to(root)
        except (OSError, ValueError):
            return JSONResponse({"error": "file path is outside workspace"}, status_code=403)
        if not target.is_file():
            return JSONResponse({"error": "file not found"}, status_code=404)
        filename = Path(str(tracked.get("name") or target.name)).name or target.name
        media_type = str(tracked.get("content_type") or "").strip() or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return FileResponse(target, filename=filename, media_type=media_type)
