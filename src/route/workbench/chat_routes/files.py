from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from cyrene.workbench.workspace_changes import (
    get_chat_file_change,
    list_chat_change_sets,
)
from route.errors import localized_error_response
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
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
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
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
        change = await asyncio.to_thread(
            get_chat_file_change,
            service.db_path,
            chat_id,
            change_set_id,
            file_path,
        )
        if change is None:
            return localized_error_response(
                "File change not found.",
                "未找到文件变更记录。",
                404,
                "file_change_not_found",
            )
        return {"change": change}

    @router.get("/api/workbench/chats/{chat_id}/files/{file_path:path}")
    async def api_workbench_chat_file(chat_id: str, file_path: str):
        """Preview/download a tracked agent file inside the chat's workspace."""
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
        normalized = str(file_path or "").strip().replace("\\", "/")
        tracked = next(
            (item for item in (chat.get("generatedFiles") or []) if isinstance(item, dict) and str(item.get("path") or "").replace("\\", "/") == normalized),
            None,
        )
        if not tracked:
            return localized_error_response(
                "File not found.", "未找到文件。", 404, "file_not_found"
            )
        R = _routes()
        project_store = await asyncio.to_thread(R.read_store)
        project = R.find_project(project_store, str(chat.get("projectId") or ""))
        if not project:
            return localized_error_response(
                "Project not found.", "未找到项目。", 404, "project_not_found"
            )
        try:
            workspace_dir = _resolve_chat_workspace_dir(chat, project, R.resolve_workspace_dir)
        except ValueError:
            logger.warning(
                "Invalid workspace configuration for chat %s",
                chat_id,
                exc_info=True,
            )
            return localized_error_response(
                "The workspace configuration is invalid.",
                "工作区配置无效。",
                400,
                "invalid_workspace",
            )
        root = Path(workspace_dir).expanduser().resolve()
        try:
            target = (root / normalized).resolve()
            target.relative_to(root)
        except (OSError, ValueError):
            return localized_error_response(
                "The file path is outside the workspace.",
                "文件路径位于工作区之外。",
                403,
                "file_outside_workspace",
            )
        if not target.is_file():
            return localized_error_response(
                "File not found.", "未找到文件。", 404, "file_not_found"
            )
        filename = Path(str(tracked.get("name") or target.name)).name or target.name
        media_type = str(tracked.get("content_type") or "").strip() or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return FileResponse(target, filename=filename, media_type=media_type)
