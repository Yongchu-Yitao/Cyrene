"""FastAPI adapters for workspace-scoped Workbench conversations."""

# Service symbols are bound below so the adapter stays thin while preserving
# the established endpoint implementation during the package migration.
# ruff: noqa: F821

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from cyrene.workbench import chat as _service
from cyrene.workbench.workspace_changes import (
    delete_chat_change_sets,
    get_chat_file_change,
    list_chat_change_sets,
)
from route import schemas as api_models

globals().update({
    name: value
    for name, value in vars(_service).items()
    if not name.startswith("__")
})

_DETACHED_ANSWER_TASKS: set[asyncio.Task[Any]] = set()
_SESSION_TITLE_TASKS: set[asyncio.Task[Any]] = set()


def _finish_detached_answer_task(task: asyncio.Task[Any]) -> None:
    _DETACHED_ANSWER_TASKS.discard(task)
    if not task.cancelled():
        task.exception()


def _track_session_title_task(task: asyncio.Task[Any]) -> None:
    _SESSION_TITLE_TASKS.add(task)

    def done(completed: asyncio.Task[Any]) -> None:
        _SESSION_TITLE_TASKS.discard(completed)
        try:
            completed.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Failed to inspect Workbench session naming task")

    task.add_done_callback(done)


def register_workbench_chat_routes(
    router: APIRouter, bot: Any, db_path: str
) -> dict[str, Any]:
    configure_store(db_path)
    _CHAT_RUN_MANAGER.configure(db_path)
    from cyrene.workbench import pinned_resources
    from cyrene.workbench import chat_groups
    pinned_resources.configure(db_path)
    chat_groups.configure_store(db_path)

    from cyrene.runtime.shell_wake import get_shell_wake_service

    async def _shell_wake_dispatcher(wake: dict[str, Any]) -> str:
        return await dispatch_shell_wake_run(wake, bot=bot, db_path=db_path)

    get_shell_wake_service().configure(
        dispatcher=_shell_wake_dispatcher,
        is_busy=lambda chat_id: _CHAT_RUN_MANAGER.get(str(chat_id)) is not None,
    )

    # Heavyweight helpers (store access, attachments, agent entrypoints) live in
    # Workbench runtime; import lazily at call time to avoid a circular import.

    def _routes():
        from cyrene.workbench import runtime as legacy_routes
        return legacy_routes

    def _project_data_key(project_id: str) -> str:
        R = _routes()
        project = R._workbench_find_project_lightweight(project_id)
        return R._workbench_project_data_key(project) if project else project_id

    async def _resolve_library_file_payload(raw: dict[str, Any]) -> dict[str, Any]:
        """Resolve a dragged knowledge item to its linked managed file."""
        body = dict(raw or {})
        nested = body.get("file") if isinstance(body.get("file"), dict) else {}
        source_kind = str(body.get("sourceKind") or nested.get("sourceKind") or "")
        item_id = str(body.get("libraryItemId") or nested.get("libraryItemId") or "")
        workspace = str(
            body.get("ownerProjectId") or nested.get("ownerProjectId") or ""
        )
        if source_kind != "library" or not item_id or not workspace:
            return body
        try:
            from pathlib import Path
            from cyrene.knowledge import library as knowledge_library
            from cyrene.runtime.attachments import resolve_managed_attachment_path
            from cyrene.workbench.knowledge import _ensure_kb_db
            from route.workbench.library import _find_raw_attachment

            kb_path = await _ensure_kb_db(workspace)
            if not await knowledge_library.get_item(kb_path, item_id):
                return body
            attachment = await _find_raw_attachment(kb_path, item_id)
            if not attachment:
                return body
            stored_path = str(
                attachment.get("document_path") or attachment.get("path") or ""
            )
            path = Path(stored_path)
            if not path.is_file():
                path = resolve_managed_attachment_path(stored_path)
            if path is None or not path.is_file():
                return body
            name = str(attachment.get("filename") or path.name)
            content_type = str(
                attachment.get("document_content_type")
                or attachment.get("content_type")
                or body.get("content_type")
                or "application/octet-stream"
            )
            resolved_file = {
                **nested,
                "id": str(nested.get("id") or f"library:{workspace}:{item_id}"),
                "name": name,
                "path": str(path.resolve()),
                "url": str(body.get("url") or nested.get("url") or ""),
                "content_type": content_type,
                "size": int(path.stat().st_size),
                "kind": str(nested.get("kind") or "file"),
                "sourceKind": "library",
                "libraryItemId": item_id,
                "ownerProjectId": workspace,
            }
            return {
                **body,
                "name": name,
                "title": str(body.get("title") or name),
                "path": str(path.resolve()),
                "content_type": content_type,
                "size": int(path.stat().st_size),
                "sourceKind": "library",
                "libraryItemId": item_id,
                "file": resolved_file,
            }
        except Exception:
            logger.exception(
                "Failed to resolve dragged library item %s in %s",
                item_id,
                workspace,
            )
            return body

    def _public_pinned_resource(item: dict[str, Any]) -> dict[str, Any]:
        public = dict(item)
        public.pop("path", None)
        nested = public.get("file")
        if isinstance(nested, dict):
            public["file"] = {key: value for key, value in nested.items() if key != "path"}
        return public

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
                from pathlib import Path
                from urllib.parse import unquote, urlparse
                from cyrene.runtime.attachments import EXPORTS_DIR, UPLOADS_DIR
                parsed = unquote(urlparse(str(body.get("url") or "")).path)
                roots = (
                    ("/api/chat/upload/", UPLOADS_DIR),
                    ("/api/chat/export/", EXPORTS_DIR),
                )
                for prefix, root in roots:
                    if parsed.startswith(prefix):
                        candidate = (root / Path(parsed[len(prefix):]).name).resolve()
                        if candidate.exists() and candidate.is_file():
                            body["path"] = str(candidate)
                        break
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

    @router.get("/api/workbench/chats")
    async def api_workbench_list_chats(project: str = ""):
        started = time.monotonic()
        # SQLite busy waits and JSON decoding are synchronous. Keep them off the
        # uvicorn event loop so one contended read cannot freeze every Workbench
        # request (the client otherwise reaches its 30s timeout as a group).
        payload = await asyncio.to_thread(_read_chats_store)
        if _prune_orphaned_fork_metadata(payload):
            await asyncio.to_thread(_write_chats_store, payload)
        data_key = await asyncio.to_thread(_project_data_key, project) if project else ""
        chats = [
            _public_chat_light(chat)
            for chat in payload.get("chats", [])
            if str(chat.get("kind") or "chat") == "chat"
            and (not project or str(chat.get("projectId") or "") == project)
        ]
        if project and data_key == "default":
            legacy = await asyncio.to_thread(_legacy_chats, project)
            legacy.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
            chats.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
            chats = chats + legacy
        else:
            chats.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 1000:
            logger.warning("Slow Workbench chat list load [project=%s duration_ms=%.1f]", project, elapsed_ms)
        return {"chats": chats}

    @router.get("/api/workbench/quick-chat/targets")
    async def api_workbench_quick_chat_targets(q: str = "", limit: int = 40):
        """Send targets for the quick-chat window: writable modern chats across
        every project plus the resolved default project (where an unselected
        quick chat starts a new conversation).

        Legacy sessions are read-only and live outside the chats store, so they
        never appear here. ``running`` reflects the authoritative in-flight run
        registry (not the persisted status, which can be stale after a crash).
        """
        R = _routes()
        store = await asyncio.to_thread(R._read_workbench_store)
        projects = store.get("projects", []) or []
        # The default project is identified by its data key, not its name — the
        # name follows the workspace directory and need not be "Cyrene".
        default_project = next(
            (p for p in projects if R._workbench_project_data_key(p) == "default"),
            None,
        )
        if default_project is None and projects:
            default_project = projects[0]
        project_by_id = {str(p.get("id") or ""): p for p in projects}

        query = str(q or "").strip().lower()
        limit = max(1, min(int(limit or 40), 200))

        payload = await asyncio.to_thread(_read_chats_store)
        targets: list[dict[str, Any]] = []
        for chat in payload.get("chats", []):
            if str(chat.get("kind") or "chat") != "chat":
                continue
            chat_id = str(chat.get("id") or "")
            if not chat_id:
                continue
            project_id = str(chat.get("projectId") or "")
            project = project_by_id.get(project_id) or {}
            project_name = str(project.get("name") or "")
            title = str(chat.get("title") or "")
            preview = _chat_preview(chat)
            if query and query not in " ".join([title, project_name, preview]).lower():
                continue
            targets.append(
                {
                    "chatId": chat_id,
                    "title": title,
                    "projectId": project_id,
                    "projectName": project_name,
                    "workspacePath": str(project.get("workspacePath") or ""),
                    "model": str(chat.get("model") or project.get("model") or ""),
                    "preview": preview,
                    "updatedAt": str(chat.get("updatedAt") or ""),
                    "running": _CHAT_RUN_MANAGER.get(chat_id) is not None,
                    "writable": True,
                }
            )
        targets.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        targets = targets[:limit]

        default_payload = None
        if default_project is not None:
            default_payload = {
                "id": str(default_project.get("id") or ""),
                "name": str(default_project.get("name") or ""),
                "dataKey": R._workbench_project_data_key(default_project),
                "workspacePath": str(default_project.get("workspacePath") or ""),
                "model": str(default_project.get("model") or ""),
            }
        return {"defaultProject": default_payload, "targets": targets}

    @router.post("/api/workbench/chats")
    async def api_workbench_create_chat(body_model: api_models.ChatCreateBody):
        started = time.monotonic()
        body = api_models.body_dict(body_model)
        project_id = str(body.get("project") or body.get("projectId") or "").strip()
        if not project_id:
            return JSONResponse({"error": "project is required"}, status_code=400)
        R = _routes()
        project = await asyncio.to_thread(R._workbench_find_project_lightweight, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)

        from cyrene.workbench.project_memory_prompt import current_snapshot
        memory_snapshot = await asyncio.to_thread(current_snapshot, project_id)

        def create_and_persist() -> dict[str, Any]:
            payload = _read_chats_store()
            chat = _new_chat(
                project_id,
                str(body.get("title") or ""),
                R._get_model(),
                project_memory_snapshot=memory_snapshot,
            )
            payload.setdefault("chats", []).insert(0, chat)
            _write_chats_store(payload)
            return chat

        chat = await asyncio.to_thread(create_and_persist)
        from cyrene.observability import debug
        await debug.publish_event({
            "type": "workbench_chat_changed",
            "change": "created",
            "session_id": str(chat.get("id") or ""),
            "chat_id": str(chat.get("id") or ""),
            "project_id": project_id,
        }, session_id=str(chat.get("id") or ""))
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 250:
            logger.warning(
                "Slow Workbench chat creation [project=%s duration_ms=%.1f]",
                project_id,
                elapsed_ms,
            )
        return {"ok": True, "chat": _public_chat_full(chat)}

    @router.get("/api/workbench/chats/{chat_id}/side-agents")
    async def api_workbench_list_side_agents(chat_id: str):
        if chat_id.startswith("legacy:"):
            return {"agents": []}
        payload = await asyncio.to_thread(_read_chats_store)
        parent = _find_chat(payload, chat_id)
        if not parent:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        agents = [
            _public_chat_full(item)
            for item in payload.get("chats", [])
            if str(item.get("kind") or "") == "side-agent"
            and str(item.get("parentChatId") or "") == chat_id
        ]
        agents.sort(key=lambda item: str(item.get("createdAt") or ""))
        return {"agents": agents}

    @router.post("/api/workbench/chats/{chat_id}/side-agents")
    async def api_workbench_create_side_agent(
        chat_id: str, body_model: api_models.SideAgentCreateBody
    ):
        if chat_id.startswith("legacy:"):
            return JSONResponse(
                {"error": "legacy chats cannot create side agents"},
                status_code=403,
            )
        body = api_models.body_dict(body_model)
        quote = str(body.get("quote") or "").strip()
        if not quote:
            return JSONResponse({"error": "quote is required"}, status_code=400)

        def create_and_persist() -> dict[str, Any] | None:
            payload = _read_chats_store()
            parent = _find_chat(payload, chat_id)
            if not parent:
                return None
            compact_quote = re.sub(r"\s+", " ", quote)
            title = str(body.get("title") or "").strip() or compact_quote[:28]
            agent = _new_chat(
                str(parent.get("projectId") or ""),
                title or "侧边提问",
                str(parent.get("model") or ""),
                project_memory_snapshot=(
                    dict(parent.get("projectMemorySnapshot") or {})
                    if isinstance(parent.get("projectMemorySnapshot"), dict)
                    else None
                ),
            )
            agent["kind"] = "side-agent"
            agent["parentChatId"] = chat_id
            agent["sourceQuote"] = quote[:12_000]
            if parent.get("workspaceOverride"):
                agent["workspaceOverride"] = str(parent["workspaceOverride"])
            payload.setdefault("chats", []).insert(0, agent)
            _write_chats_store(payload)
            return agent

        agent = await asyncio.to_thread(create_and_persist)
        if not agent:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        return {"ok": True, "agent": _public_chat_full(agent)}

    @router.get("/api/workbench/chats/{chat_id}")
    async def api_workbench_get_chat(chat_id: str):
        started = time.monotonic()
        if chat_id.startswith("legacy:"):
            _prefix, project_id, _session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            data_key = await asyncio.to_thread(_project_data_key, project_id) if project_id else ""
            if not project_id or data_key != "default":
                return JSONResponse({"error": "chat not found"}, status_code=404)
            legacy = await asyncio.to_thread(_legacy_chats, project_id, full_id=chat_id)
            if not legacy:
                return JSONResponse({"error": "chat not found"}, status_code=404)
            elapsed_ms = (time.monotonic() - started) * 1000
            if elapsed_ms >= 1000:
                logger.warning(
                    "Slow legacy Workbench chat detail load [chat_id=%s duration_ms=%.1f]",
                    chat_id,
                    elapsed_ms,
                )
            return {"chat": legacy[0]}
        payload = await asyncio.to_thread(_read_chats_store)
        if _prune_orphaned_fork_metadata(payload):
            await asyncio.to_thread(_write_chats_store, payload)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        if "generatedFiles" not in chat:
            await asyncio.to_thread(_sync_chat_generated_files, chat_id)
            payload = await asyncio.to_thread(_read_chats_store)
            chat = _find_chat(payload, chat_id)
            if not chat:
                return JSONResponse({"error": "chat not found"}, status_code=404)
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 1000:
            logger.warning("Slow Workbench chat detail load [chat_id=%s duration_ms=%.1f]", chat_id, elapsed_ms)
        return {"chat": _public_chat_full(chat)}

    @router.get("/api/workbench/chats/{chat_id}/changes")
    async def api_workbench_chat_changes(chat_id: str):
        """Return durable run-scoped workspace changes without consulting Git."""
        if chat_id.startswith("legacy:"):
            return {"changeSets": [], "fileCount": 0, "additions": 0, "deletions": 0}
        payload = await asyncio.to_thread(_read_chats_store)
        if not _find_chat(payload, chat_id):
            return JSONResponse({"error": "chat not found"}, status_code=404)
        change_sets = await asyncio.to_thread(
            list_chat_change_sets, _service._STORE_DB_PATH, chat_id
        )
        return {
            "changeSets": change_sets,
            "fileCount": sum(int(item.get("fileCount") or 0) for item in change_sets),
            "additions": sum(int(item.get("additions") or 0) for item in change_sets),
            "deletions": sum(int(item.get("deletions") or 0) for item in change_sets),
        }

    @router.get("/api/workbench/chats/{chat_id}/changes/{change_set_id}/files/{file_path:path}")
    async def api_workbench_chat_change_diff(
        chat_id: str, change_set_id: str, file_path: str
    ):
        """Return the immutable diff recorded for one file in one agent run."""
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "file change not found"}, status_code=404)
        payload = await asyncio.to_thread(_read_chats_store)
        if not _find_chat(payload, chat_id):
            return JSONResponse({"error": "chat not found"}, status_code=404)
        change = await asyncio.to_thread(
            get_chat_file_change,
            _service._STORE_DB_PATH,
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
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "file not found"}, status_code=404)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        normalized = str(file_path or "").strip().replace("\\", "/")
        tracked = next(
            (
                item for item in (chat.get("generatedFiles") or [])
                if isinstance(item, dict)
                and str(item.get("path") or "").replace("\\", "/") == normalized
            ),
            None,
        )
        if not tracked:
            return JSONResponse({"error": "file not found"}, status_code=404)
        R = _routes()
        project_store = await asyncio.to_thread(R._read_workbench_store)
        project = R._workbench_find_project(
            project_store, str(chat.get("projectId") or "")
        )
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        try:
            workspace_dir = _resolve_chat_workspace_dir(
                chat, project, R._workbench_resolve_workspace_dir
            )
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
        media_type = (
            str(tracked.get("content_type") or "").strip()
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        return FileResponse(target, filename=filename, media_type=media_type)

    @router.get("/api/workbench/chats/{chat_id}/subagents")
    async def api_workbench_chat_subagents(chat_id: str, round_id: str = ""):
        if chat_id.startswith("legacy:"):
            return {"rounds": [], "activeRoundId": "", "agents": [], "messages": []}
        payload = await asyncio.to_thread(_read_chats_store)
        if not _find_chat(payload, chat_id):
            return JSONResponse({"error": "chat not found"}, status_code=404)
        return await asyncio.to_thread(_workbench_subagent_payload, chat_id, round_id)

    @router.get("/api/workbench/chats/{chat_id}/context")
    async def api_workbench_chat_context(chat_id: str):
        """Live context-window gauge + composition for the overview panel."""
        from cyrene import config
        from cyrene.runtime.config_store import get_current_ctx_limit

        # Keep the gauge denominator identical to automatic persistence
        # compaction. The latest assistant call may have used a fallback model,
        # but that runtime metadata must not move the displayed 60% threshold.
        compactor_ctx_limit = get_current_ctx_limit()

        if chat_id.startswith("legacy:"):
            _prefix, _project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not session_id:
                return JSONResponse({"error": "chat not found"}, status_code=404)
            model_name = str(getattr(config, "OPENAI_MODEL", "") or "")
            return await asyncio.to_thread(
                _chat_context_payload,
                session_id,
                model_name,
                ctx_limit=compactor_ctx_limit,
            )
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        model_name = str(chat.get("model") or getattr(config, "OPENAI_MODEL", "") or "")
        return await asyncio.to_thread(
            _chat_context_payload,
            chat_id,
            model_name,
            ctx_limit=compactor_ctx_limit,
        )

    @router.post("/api/workbench/chats/{chat_id}/compact")
    async def api_workbench_chat_compact(chat_id: str):
        """Let the user explicitly run the normal session compaction flow."""
        from cyrene import config
        from cyrene.agent import compact_session_if_needed
        from cyrene.runtime.config_store import effective_ctx_limit_for_model

        if chat_id.startswith("legacy:"):
            return JSONResponse(
                {"error": "legacy chat context is read-only"},
                status_code=403,
            )
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        model_name = str(
            chat.get("model") or getattr(config, "OPENAI_MODEL", "") or ""
        )
        result = await compact_session_if_needed(
            chat_id,
            # Explicit compaction must always have a usable budget even when an
            # OpenAI-compatible custom model has no family heuristic/configured
            # context size. 128K is the conservative default used by the core
            # chat models and is safer than passing 0 (which disables budgeting).
            ctx_limit=(
                effective_ctx_limit_for_model(model_name)
                or 128_000
            ),
            force=True,
        )
        return {"ok": True, **result}

    @router.get("/api/workbench/chats/{chat_id}/context-blocks")
    async def api_workbench_chat_context_blocks(chat_id: str):
        """Context block composition using the same token math as the Overview gauge."""
        from cyrene.agent.state import _session_state_file
        from cyrene.call_llm import _approx_token_count

        if chat_id.startswith("legacy:"):
            _, _project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not session_id:
                return JSONResponse({"error": "chat not found"}, status_code=404)
            state_id = session_id
        else:
            state_id = chat_id

        data = read_json_safe(_session_state_file(state_id))
        if not isinstance(data, dict):
            return {"layers": [], "totalTokensEst": 0}

        messages = data.get("messages")
        if not isinstance(messages, list):
            messages = []
        seg = _context_segment_tokens(messages)
        msg_total = sum(seg.values())

        layers: list[dict[str, Any]] = []

        # Layer 1: System Prefix — from separately-saved blocks (not in state.json)
        sys_blocks = data.get("system_context_blocks")
        if isinstance(sys_blocks, list) and sys_blocks:
            sys_tokens = sum(int(b.get("tokens_est", 0) or 0) for b in sys_blocks if isinstance(b, dict))
            layers.append({
                "id": "system_prefix",
                "label": "System Prefix",
                "sublabel": None,
                "blocks": [dict(b) for b in sys_blocks if isinstance(b, dict)],
                "totalTokens": sys_tokens,
            })

        # Layer 2: Ephemeral — from saved text (not in state.json)
        ephemeral = data.get("ephemeral_context")
        if isinstance(ephemeral, str) and ephemeral.strip():
            tokens = _approx_token_count(ephemeral)
            layers.append({
                "id": "ephemeral",
                "label": "Ephemeral Tail",
                "sublabel": None,
                "blocks": [{"id": "ephemeral.run", "type": "ephemeral", "tokens_est": tokens, "chars": len(ephemeral)}],
                "totalTokens": tokens,
            })

        # Layer 3: Messages — same segments as the Overview gauge
        msg_seg_order = [
            ("compacted", "Compacted"),
            ("system", "System"),
            ("user", "User"),
            ("assistant", "Assistant"),
            ("tool", "Tool"),
        ]
        msg_blocks = []
        for key, label in msg_seg_order:
            t = int(seg.get(key, 0) or 0)
            if t > 0:
                msg_blocks.append({"id": "segment." + key, "type": key, "tokens_est": t, "source": "", "reason": ""})
        if msg_blocks:
            layers.append({
                "id": "messages",
                "label": "Conversation Messages",
                "sublabel": None,
                "blocks": msg_blocks,
                "totalTokens": msg_total,
            })

        total = sum(layer["totalTokens"] for layer in layers)
        return {"layers": layers, "totalTokensEst": total, "messageTokens": msg_total}

    @router.get("/api/workbench/chats/{chat_id}/inbox")
    async def api_workbench_chat_inbox(chat_id: str):
        """Return only the current live inbox for this conversation."""
        started = time.monotonic()
        if chat_id.startswith("legacy:"):
            return JSONResponse(
                {"error": "legacy chat has no Workbench inbox"}, status_code=404
            )
        # A mounted Context tab polls this endpoint throughout a run. The run
        # registry is authoritative for that hot path, so do not queue a full
        # chats-document SQLite read merely to re-validate an already running
        # conversation. Idle/unknown ids still use the durable store for the
        # existing 404 contract. Re-check after the await because a run may
        # start while validation is in progress.
        run = _CHAT_RUN_MANAGER.get(chat_id)
        if run is None:
            payload = await asyncio.to_thread(_read_chats_store)
            if not _find_chat(payload, chat_id):
                return JSONResponse({"error": "chat not found"}, status_code=404)
            run = _CHAT_RUN_MANAGER.get(chat_id)
        live = run.inbox.live_snapshot() if run is not None else {
            "queueDepth": 0,
            "pendingGuidance": 0,
            "activeTasks": 0,
            "persistenceTasks": 0,
            "closed": True,
            "events": [],
            "tools": [],
        }
        events = list(live.get("events") or [])
        tools = [
            dict(item)
            for item in list(live.get("tools") or [])
            if str(item.get("state") or "") in {"queued", "running", "ready"}
        ]
        counts = {
            "queued": sum(1 for item in events if item.get("status") == "queued"),
            "claimed": sum(1 for item in events if item.get("status") == "claimed"),
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "total": len(events),
        }
        timestamps = [str(item.get("createdAt") or "") for item in events]
        timestamps.extend(str(item.get("updatedAt") or "") for item in tools)
        snapshot = {
            "sessionId": chat_id,
            "runId": str(run.run_id if run is not None else ""),
            "active": bool(
                run is not None and run.status in {"running", "finishing"}
            ),
            "runStatus": str(run.status if run is not None else "idle"),
            "counts": counts,
            "events": events,
            "tools": tools,
            "updatedAt": max((stamp for stamp in timestamps if stamp), default=""),
            "observedAt": _utc_now_iso(),
            "live": live,
        }
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 1000:
            logger.warning(
                "Slow Workbench inbox snapshot [chat_id=%s active=%s duration_ms=%.1f]",
                chat_id,
                run is not None,
                elapsed_ms,
            )
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})

    @router.get("/api/workbench/chats/{chat_id}/run-stream")
    async def api_workbench_chat_run_stream(chat_id: str, cursor: int = 0):
        """Reconnect to an existing streamed run without submitting a message."""
        replay_lookup = getattr(
            _CHAT_RUN_MANAGER, "get_replayable", _CHAT_RUN_MANAGER.get
        )
        run = replay_lookup(chat_id)
        if run is None:
            await asyncio.to_thread(_settle_chat_running_status, chat_id)
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_run_not_found"},
                status_code=404,
            )
        return StreamingResponse(
            _CHAT_RUN_MANAGER.stream(run, cursor=max(0, int(cursor or 0))),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/api/workbench/chats/{chat_id}/guidance")
    async def api_workbench_chat_guidance(
        chat_id: str, body_model: api_models.ChatGuidanceBody
    ):
        """Steer the currently running Workbench conversation.

        Guidance is queued in the run-scoped inbox.  A tool waiter consumes it
        immediately; otherwise the agent picks it up at the next model/tool
        boundary.  It never starts a second conversation run.
        """
        body = api_models.body_dict(body_model)
        message = str(body.get("message") or "").strip()
        client_request_id = str(body.get("clientRequestId") or "").strip()
        if not message:
            return JSONResponse(
                {"error": "guidance message is empty", "code": "guidance_empty"},
                status_code=422,
            )
        run = _CHAT_RUN_MANAGER.get(chat_id)
        if run is None or run.status != "running":
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_not_running"},
                status_code=409,
            )
        # Durable inbox setup happens off the HTTP event loop. Guidance must
        # wait for it before accepting an event, otherwise a just-started run
        # can race schema initialization.
        await run.ready.wait()
        if run.status != "running":
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_not_running"},
                status_code=409,
            )
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)

        now = _utc_now_iso()
        public_message_id = _short_id("msg")
        try:
            event = await run.inbox.put_guidance(
                message,
                client_request_id=client_request_id,
                public_message_id=public_message_id,
                public_created_at=now,
            )
        except RuntimeError:
            logger.exception("Failed to persist guidance for chat %s", chat_id)
            return JSONResponse(
                {
                    "error": "guidance could not be saved; please retry",
                    "code": "guidance_persistence_failed",
                },
                status_code=503,
            )
        if event.get("duplicate"):
            return {
                "queued": True, "duplicate": True, "eventId": event["event_id"],
                "runId": run.run_id,
            }

        user_entry = {
            "id": public_message_id,
            "role": "user",
            "content": message,
            "createdAt": now,
            "guidance": True,
            "guidanceEventId": event["event_id"],
            "runId": run.run_id,
        }
        if client_request_id:
            user_entry["clientRequestId"] = client_request_id
        chat.setdefault("messages", []).append(user_entry)
        chat["updatedAt"] = now
        await asyncio.to_thread(_write_chats_store, payload)
        await run.publish({
            "type": "guidance_received",
            "eventId": event["event_id"],
            "runId": run.run_id,
            "userMessage": _public_message(user_entry),
            "message": "Guidance queued for the running agent.",
        })
        return {
            "queued": True,
            "eventId": event["event_id"],
            "runId": run.run_id,
            "userMessage": _public_message(user_entry),
        }

    @router.patch("/api/workbench/chats/{chat_id}")
    async def api_workbench_update_chat(
        chat_id: str, body_model: api_models.ChatUpdateBody
    ):
        body = api_models.body_dict(body_model)
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "legacy chat metadata is read-only"}, status_code=403)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        if "title" in body:
            chat["title"] = str(body.get("title") or "").strip()[:60] or chat.get("title")
            chat["titleLocked"] = True
        chat["updatedAt"] = _utc_now_iso()
        await asyncio.to_thread(_write_chats_store, payload)
        return {"ok": True, "chat": _public_chat_full(chat)}

    @router.get("/api/workbench/chat-groups")
    async def api_workbench_chat_groups(project: str = ""):
        project_id = str(project or "").strip()
        if not project_id:
            return JSONResponse({"error": "project is required"}, status_code=400)
        if not await asyncio.to_thread(_routes()._workbench_find_project_lightweight, project_id):
            return JSONResponse({"error": "project not found"}, status_code=404)
        return await asyncio.to_thread(chat_groups.get_project_groups, project_id)

    async def _replace_chat_groups(body: dict[str, Any]):
        project_id = str(body.get("projectId") or "").strip()
        if not await asyncio.to_thread(_routes()._workbench_find_project_lightweight, project_id):
            return JSONResponse({"error": "project not found"}, status_code=404)
        try:
            return await chat_groups.replace_project_groups(
                project_id,
                body.get("groups") if isinstance(body.get("groups"), list) else [],
                base_groups=(
                    body.get("baseGroups")
                    if isinstance(body.get("baseGroups"), list)
                    else None
                ),
                mutation_intent=(
                    body.get("intent") if isinstance(body.get("intent"), dict) else None
                ),
                mark_migrated=True,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            logger.exception("Failed to persist chat groups for project %s", project_id)
            return JSONResponse({"error": "chat group persistence failed"}, status_code=500)

    @router.put("/api/workbench/chat-groups")
    async def api_workbench_replace_chat_groups(
        body_model: api_models.ChatGroupsReplaceBody,
    ):
        return await _replace_chat_groups(api_models.body_dict(body_model))

    @router.post("/api/workbench/chat-groups/migrate")
    async def api_workbench_migrate_chat_groups(
        body_model: api_models.ChatGroupsReplaceBody,
    ):
        """Idempotently import the legacy browser-owned projection."""
        body = api_models.body_dict(body_model)
        project_id = str(body.get("projectId") or "").strip()
        existing = await asyncio.to_thread(chat_groups.get_project_groups, project_id)
        if not existing.get("migrationRequired"):
            return existing
        return await _replace_chat_groups(body)

    @router.post("/api/workbench/chat-groups/metadata")
    async def api_workbench_chat_group_metadata(
        body_model: api_models.ChatGroupMetadataBody,
    ):
        body = api_models.body_dict(body_model)
        project_id = str(body.get("projectId") or "").strip()
        group_id = str(body.get("groupId") or "").strip()
        signature = str(body.get("signature") or "")
        metadata_context = None
        if project_id:
            try:
                metadata_context = await asyncio.to_thread(
                    chat_groups.get_group_metadata_context,
                    project_id,
                    group_id,
                    signature=signature,
                )
            except LookupError as exc:
                return JSONResponse({"error": str(exc)}, status_code=404)
            except RuntimeError as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
        try:
            metadata = await _service.generate_chat_group_metadata(
                (
                    metadata_context["members"]
                    if metadata_context
                    else body.get("members") if isinstance(body.get("members"), list) else []
                ),
                lang=str(body.get("lang") or ""),
                title_locked=(
                    bool(metadata_context["group"].get("titleLocked"))
                    if metadata_context
                    else bool(body.get("titleLocked"))
                ),
                current_title=(
                    str(metadata_context["group"].get("title") or "")
                    if metadata_context
                    else str(body.get("currentTitle") or "")
                ),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            logger.exception("Failed to generate chat group metadata")
            return JSONResponse(
                {"error": "chat group metadata generation failed"},
                status_code=502,
            )
        persisted_group = None
        if metadata_context:
            try:
                persisted = await chat_groups.update_group_metadata(
                    project_id,
                    group_id,
                    signature=metadata_context["signature"],
                    metadata=metadata,
                )
            except (LookupError, RuntimeError) as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
            persisted_group = next(
                (
                    item
                    for item in persisted.get("groups", [])
                    if str(item.get("id") or "") == group_id
                ),
                None,
            )
        return {
            "ok": True,
            "groupId": group_id,
            "metadata": metadata,
            "group": persisted_group,
        }

    @router.delete("/api/workbench/chats/{chat_id}")
    async def api_workbench_delete_chat(chat_id: str):
        from cyrene.agent import clear_session_id, interrupt_active_run
        if chat_id.startswith("legacy:"):
            _prefix, project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not project_id or not session_id or _project_data_key(project_id) != "default":
                return JSONResponse({"error": "chat not found"}, status_code=404)
            payload, status_code = await _routes()._delete_chat_session(session_id)
            if status_code != 200:
                return JSONResponse(payload, status_code=status_code)
            try:
                from cyrene.browser import close_electron_browser_session

                await close_electron_browser_session(session_id)
            except Exception:
                logger.exception("Failed to close Electron browser for chat %s", session_id)
            return {"ok": True}
        payload = await asyncio.to_thread(_read_chats_store)
        chats = payload.get("chats", [])
        removed_root = next(
            (chat for chat in chats if str(chat.get("id") or "") == chat_id),
            None,
        )
        if removed_root is None:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        removed_project_id = str(removed_root.get("projectId") or "")
        removed_chat_ids = {
            chat_id,
            *[
                str(chat.get("id") or "")
                for chat in chats
                if str(chat.get("kind") or "") == "side-agent"
                and str(chat.get("parentChatId") or "") == chat_id
            ],
        }
        next_chats = [
            chat
            for chat in chats
            if str(chat.get("id") or "") not in removed_chat_ids
        ]
        for chat in next_chats:
            if str(chat.get("forkedFromChatId") or "") == chat_id:
                _clear_fork_metadata(chat)
        try:
            # Revoke group authority before deleting the chat record. If this
            # fails, leave the chat intact rather than persisting a stale group.
            await chat_groups.remove_chat(chat_id, removed_project_id)
        except Exception:
            logger.exception("Failed to remove deleted chat %s from chat groups", chat_id)
            return JSONResponse(
                {"error": "chat group membership could not be revoked"},
                status_code=503,
            )
        payload["chats"] = next_chats
        await asyncio.to_thread(_write_chats_store, payload)
        for removed_chat_id in removed_chat_ids:
            try:
                await asyncio.to_thread(
                    delete_chat_change_sets,
                    _service._STORE_DB_PATH,
                    removed_chat_id,
                )
            except Exception:
                logger.exception(
                    "Failed to delete workspace change history for chat %s",
                    removed_chat_id,
                )
            try:
                _CHAT_RUN_MANAGER.interrupt(removed_chat_id)
                interrupt_active_run(session_id=removed_chat_id)
                await clear_session_id(session_id=removed_chat_id)
            except Exception:
                logger.exception(
                    "Failed to clear agent state for chat %s",
                    removed_chat_id,
                )
            try:
                from cyrene.browser import close_electron_browser_session

                if removed_chat_id == chat_id:
                    await close_electron_browser_session(chat_id)
                else:
                    await close_electron_browser_session(removed_chat_id)
            except Exception:
                logger.exception(
                    "Failed to close Electron browser for chat %s",
                    removed_chat_id,
                )
            try:
                from cyrene.workbench.project_memory_prompt import (
                    cancel_chat_jobs,
                    delete_chat_context,
                )

                await cancel_chat_jobs(removed_chat_id)
                await asyncio.to_thread(delete_chat_context, removed_chat_id)
            except Exception:
                logger.exception(
                    "Failed to delete project-memory context for chat %s",
                    removed_chat_id,
                )
        return {"ok": True}

    async def _workbench_chat_send_impl(
        chat_id: str,
        body: dict[str, Any],
        *,
        detached: bool = False,
    ):
        processing_started_at = time.monotonic()
        from cyrene.agent import run_agent
        from cyrene.agent.state import PERMISSION_MODES, _attachment_paths_by_name

        message = str(body.get("message") or "").strip()
        client_request_id = str(body.get("clientRequestId") or "").strip()
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        if attachments:
            attachments = [
                await _resolve_library_file_payload(item)
                if isinstance(item, dict) else item
                for item in attachments
            ]
        command = str(body.get("command") or "").strip()
        wants_stream = bool(body.get("stream"))
        retry = bool(body.get("retry"))
        fork_replay = bool(body.get("forkReplay"))
        requested_mode = str(body.get("mode") or "").strip().lower()
        requested_model = str(body.get("model") or "").strip()
        requested_effort = str(body.get("reasoningEffort") or "").strip().lower()
        lang = str(body.get("lang") or "").strip().lower()
        # Persist the UI language so server-side flows (the proactive scheduler)
        # can reply in the same language even with no HTTP request to read.
        if lang in {"en", "zh"}:
            try:
                from cyrene.runtime.settings_store import get as _get_setting, set_ as _set_setting
                if str(_get_setting("app_language", "") or "") != lang:
                    _set_setting("app_language", lang)
            except Exception:
                pass

        R = _routes()
        normalized = R._workbench_normalize_attachments(attachments)
        public_attachments = [R.build_public_attachment_payload(item) for item in normalized]
        if not retry and not message and not normalized:
            return JSONResponse({"error": "message is required"}, status_code=400)

        # ── Budget gate ──
        from cyrene.workbench.runtime import _check_budget_gate as _chat_budget_gate
        _bgt = await _chat_budget_gate(chat_id)
        if _bgt:
            return JSONResponse(_bgt, status_code=403)

        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        is_side_agent = str(chat.get("kind") or "") == "side-agent"
        completed_turn_count_before = _completed_turn_count(chat)
        parent_chat = (
            _find_chat(payload, str(chat.get("parentChatId") or ""))
            if is_side_agent
            else None
        )
        parent_transcript = _side_agent_parent_transcript(parent_chat)
        stored_mode = str(chat.get("permissionMode") or "").strip().lower()
        if requested_mode:
            mode = requested_mode if requested_mode in PERMISSION_MODES else "default"
        else:
            mode = stored_mode if stored_mode in PERMISSION_MODES else "default"
        chat["permissionMode"] = mode
        project_id = str(chat.get("projectId") or "")
        project_store = await asyncio.to_thread(R._read_workbench_store)
        project = R._workbench_find_project(project_store, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        if "workspaceOverride" in body:
            try:
                requested_workspace = _normalize_workspace_override(
                    body.get("workspaceOverride")
                )
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            if requested_workspace:
                chat["workspaceOverride"] = requested_workspace
            else:
                chat.pop("workspaceOverride", None)
        try:
            workspace_dir = _resolve_chat_workspace_dir(
                chat, project, R._workbench_resolve_workspace_dir
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        selected_candidate = None
        recovered_stale_selection = False
        selected_key = requested_model or str(chat.get("modelSelectionId") or "").strip()
        if selected_key:
            from cyrene.runtime.settings_store import get_models

            configured_models = get_models() or []
            selected_candidate = next(
                (
                    candidate
                    for candidate in configured_models
                    if selected_key
                    in {
                        str(candidate.get("id") or "").strip(),
                        str(candidate.get("model") or "").strip(),
                        str(candidate.get("name") or "").strip(),
                    }
                ),
                None,
            )
            if selected_candidate is None:
                if requested_model:
                    return JSONResponse({"error": "configured model not found"}, status_code=400)
                # The active model source can change while a conversation is
                # idle (for example, Codex quota is exhausted and the user
                # switches back to a custom DeepSeek model).  In that case the
                # chat still carries the old source-specific selection id,
                # which is no longer present in the active candidate list.
                # A retry has no explicit model field, so recover by selecting
                # the current configured primary instead of rejecting the run
                # with a misleading "configured model not found" response.
                selected_candidate = configured_models[0] if configured_models else None
                if selected_candidate is not None:
                    recovered_stale_selection = True
                    selected_key = str(
                        selected_candidate.get("id")
                        or selected_candidate.get("model")
                        or selected_candidate.get("name")
                        or ""
                    ).strip()
        if selected_candidate is not None:
            from cyrene.model_runtime.client import set_session_model_preference

            selected_model_name = str(
                selected_candidate.get("model")
                or selected_candidate.get("name")
                or selected_key
            ).strip()
            selected_model_id = str(selected_candidate.get("id") or selected_key).strip()
            selected_effort = requested_effort or str(
                (
                    selected_candidate.get("reasoning_effort")
                    if recovered_stale_selection
                    else chat.get("reasoningEffort")
                )
                or selected_candidate.get("reasoning_effort")
                or ""
            ).strip().lower()
            set_session_model_preference(
                chat_id,
                selected_candidate,
                selected_effort,
            )
            chat["modelSelectionId"] = selected_model_id
            chat["model"] = selected_model_name
            chat["reasoningEffort"] = selected_effort

        existing_run = _CHAT_RUN_MANAGER.get(chat_id)
        if existing_run is not None:
            return JSONResponse(
                {"error": "chat already has a running reply", "code": "chat_run_in_progress"},
                status_code=409,
            )

        now = _utc_now_iso()
        messages = chat.setdefault("messages", [])
        should_generate_title = False
        user_entry: dict[str, Any]
        truncate_after_id = ""
        retry_replaced_message_ids: set[str] = set()
        retry_state_backup: tuple[Any, bytes | None] | None = None
        if retry:
            # Regenerate the last exchange transactionally. Keep the public
            # transcript intact until the replacement reply has been persisted;
            # otherwise a failed retry permanently deletes the previous answer.
            last_user_index = -1
            for index in range(len(messages) - 1, -1, -1):
                if messages[index].get("role") == "user":
                    last_user_index = index
                    break
            if last_user_index < 0:
                return JSONResponse({"error": "nothing to retry"}, status_code=400)
            user_entry = messages[last_user_index]
            truncate_after_id = str(user_entry.get("id") or "")
            retry_replaced_message_ids = {
                str(item.get("id") or "")
                for item in messages[last_user_index + 1:]
                if isinstance(item, dict) and str(item.get("id") or "")
            }
            message = str(user_entry.get("content") or "").strip()
            command = ""
            normalized = R._workbench_normalize_attachments(user_entry.get("agentAttachments") or [])
            public_attachments = user_entry.get("attachments") if isinstance(user_entry.get("attachments"), list) else []
            # A fork already truncated the raw state at the edit boundary; only
            # a plain retry needs to drop the last exchange from the state here.
            if not fork_replay:
                from cyrene.agent.state import _session_state_file

                state_path = _session_state_file(chat_id)
                previous_state = await asyncio.to_thread(
                    lambda: state_path.read_bytes() if state_path.exists() else None
                )
                retry_state_backup = (state_path, previous_state)
                await asyncio.to_thread(_truncate_state_for_retry, chat_id)
        else:
            user_entry = {
                "id": _short_id("msg"),
                "role": "user",
                "content": message,
                "createdAt": now,
            }
            if client_request_id:
                user_entry["clientRequestId"] = client_request_id
            if public_attachments:
                user_entry["attachments"] = public_attachments
                # Keep the normalized (path-bearing) attachments privately so a
                # later retry can rebuild the agent prompt + read-guard map.
                user_entry["agentAttachments"] = normalized
            is_first_message = not any(m.get("role") == "user" for m in messages)
            messages.append(user_entry)
            if is_first_message and chat.get("title") in ("", "新对话", None) and message:
                chat["title"] = message.replace("\n", " ")[:24]
            if (
                is_first_message
                and bool(message)
                and not bool(chat.get("titleLocked"))
                and not chat.get("titleNamingStatus")
            ):
                should_generate_title = True
                chat["titleNamingStatus"] = "pending"
                chat["titleNamingStartedAt"] = now
        if not is_side_agent:
            try:
                # Retry truncation can remove a membership event that followed
                # the regenerated exchange, so reconcile only after that cut.
                await chat_groups.reconcile_session(chat_id)
            except Exception:
                logger.exception("Failed to reconcile chat-group context for %s", chat_id)
                if retry_state_backup is not None:
                    state_path, previous_state = retry_state_backup
                    if previous_state is None:
                        await asyncio.to_thread(state_path.unlink, missing_ok=True)
                    else:
                        await asyncio.to_thread(state_path.write_bytes, previous_state)
                return JSONResponse(
                    {"error": "chat group context could not be prepared"},
                    status_code=503,
                )
        chat["status"] = "running"
        if selected_candidate is None:
            chat["model"] = R._get_model()
        _mark_user_activity(chat, now)
        await asyncio.to_thread(_write_chats_store, payload)

        async def _name_session_once() -> None:
            if not should_generate_title:
                return
            from cyrene.workbench.session_naming import generate_session_title

            try:
                generated_title = await generate_session_title(message, limit=60)
            except Exception:
                logger.warning("Workbench session naming failed for %s", chat_id, exc_info=True)
                generated_title = ""

            def persist_title() -> bool:
                fresh = _read_chats_store()
                fresh_chat = _find_chat(fresh, chat_id)
                if not fresh_chat or fresh_chat.get("titleNamingStatus") != "pending":
                    return False
                if generated_title and not bool(fresh_chat.get("titleLocked")):
                    fresh_chat["title"] = generated_title
                    fresh_chat["titleNamingStatus"] = "generated"
                    fresh_chat["titleGeneratedAt"] = _utc_now_iso()
                else:
                    fresh_chat["titleNamingStatus"] = (
                        "locked" if bool(fresh_chat.get("titleLocked")) else "failed"
                    )
                _write_chats_store(fresh)
                return bool(generated_title) and not bool(fresh_chat.get("titleLocked"))

            changed = await asyncio.to_thread(persist_title)
            if changed:
                from cyrene.observability import debug

                await debug.publish_event({
                    "type": "workbench_chat_changed",
                    "change": "renamed",
                    "session_id": chat_id,
                    "chat_id": chat_id,
                    "project_id": project_id,
                }, session_id=chat_id)

        if should_generate_title:
            _track_session_title_task(asyncio.create_task(_name_session_once()))

        agent_message = message
        if is_side_agent:
            source_quote = str(chat.get("sourceQuote") or "").strip()
            agent_message = (
                "你是主对话旁的独立 Side Agent。以下 main_conversation 是提问"
                "发生时主对话的完整公开内容；结合全部对话理解问题，并把"
                " selected_quote 作为用户当前关注的重点。不要假装上下文中未提供"
                "的事实。\n\n<main_conversation>\n"
                + (parent_transcript or "(empty)")
                + "\n</main_conversation>\n\n<selected_quote>\n"
                + (source_quote or "(none)")
                + "\n</selected_quote>\n\n用户问题：\n"
                + message
            )
        if normalized:
            agent_message = (agent_message or "[Attachment upload]") + R._attachment_prompt_block(normalized)
            # Auto-allow uploaded files for tool read guards (same as /api/chat).
            att_map: dict[str, str] = {}
            for item in normalized:
                full_path = str(item.get("path") or "").strip()
                if not full_path:
                    continue
                from pathlib import Path as _Path
                uuid_name = _Path(full_path).name
                att_map[uuid_name] = full_path
                parts = uuid_name.split("_", 1)
                if len(parts) == 2:
                    att_map[parts[1]] = full_path
            _attachment_paths_by_name.set(att_map)

        # Capture IDs of messages already in state before this exchange, so
        # _extract_exchange_segments can identify new messages by ID rather
        # than by positional index (which would break after session compaction).
        state_ids_before: set[str] = set()
        for m in await asyncio.to_thread(_session_state_messages, chat_id):
            mid = str(m.get("message_id") or m.get("id") or "").strip()
            if mid:
                state_ids_before.add(mid)

        async def _run() -> str:
            from cyrene.workbench.project_memory_prompt import build_main_agent_suffix

            return await run_agent(
                user_message=agent_message,
                bot=bot,
                chat_id=R._CHAT_ID,
                db_path=db_path,
                session_id=chat_id,
                permission_mode=mode,
                command=command,
                public_user_message=message or None,
                public_attachments=public_attachments or None,
                workspace_dir=workspace_dir,
                final_system_extra=build_main_agent_suffix(
                    chat.get("projectMemorySnapshot")
                    if isinstance(chat.get("projectMemorySnapshot"), dict)
                    else None,
                    include_trigger=not is_side_agent,
                ),
                response_capabilities=("interactive_blocks",),
            )

        def _finalize(reply_text: str) -> dict[str, Any]:
            """Persist mid-run messages plus the final assistant reply in order."""
            state_messages = _session_state_messages(chat_id)
            timeline_entries, usage, files = _extract_exchange_timeline(
                state_messages, state_ids_before
            )
            fresh = _read_chats_store()
            fresh_chat = _find_chat(fresh, chat_id)
            if not fresh_chat:
                return {}
            _commit_retry_cut(fresh_chat)
            configured_model = str(fresh_chat.get("model") or "")
            model_name = _last_exchange_model(state_messages, state_ids_before) or configured_model
            for entry in timeline_entries:
                entry.setdefault("model", model_name)
            assistant_entry: dict[str, Any] = {
                "id": _short_id("msg"),
                "role": "assistant",
                "content": str(reply_text or ""),
                "createdAt": _utc_now_iso(),
                "model": model_name,
                "processingDurationMs": max(
                    0, int(round((time.monotonic() - processing_started_at) * 1000))
                ),
            }
            if any(usage.values()):
                assistant_entry["usage"] = usage
            if files:
                assistant_entry["attachments"] = files
            fresh_chat["lastModel"] = model_name
            saved_messages = [*timeline_entries, assistant_entry]
            _merge_chat_messages_chronologically(fresh_chat, saved_messages)
            completed_turn_count = _next_completed_turn_count(
                {"completedTurnCount": completed_turn_count_before},
                retry=retry,
                command=command,
                is_side_agent=is_side_agent,
            )
            fresh_chat["completedTurnCount"] = completed_turn_count
            fresh_chat["status"] = "idle"
            fresh_chat.pop("pendingQuestion", None)
            fresh_chat["updatedAt"] = assistant_entry["createdAt"]
            _write_chats_store(fresh)
            # Persist this exchange to the workspace's per-session conversation
            # file so the conversation survives outside the JSON store and the
            # agent can read its own history by id. Best-effort; never block reply.
            try:
                archive_session_exchange(
                    chat_id,
                    message,
                    str(reply_text or ""),
                    workspace_dir=workspace_dir,
                    session_title=str(fresh_chat.get("title") or ""),
                )
            except Exception:
                logger.exception("Failed to archive workbench conversation %s", chat_id)
            if not command and not retry and not is_side_agent:
                append_notification(
                    title="Agent 回复完成",
                    body=f"Agent 在「{fresh_chat.get('title') or '新对话'}」中回复了你。",
                    tab="mention",
                    project_ref=project_id,
                    source="workbench_chat_reply",
                    source_label="对话",
                    link_label=str(fresh_chat.get("title") or ""),
                    meta={"chatId": chat_id},
                )
            return {
                "assistantMessage": assistant_entry,
                "assistantMessages": saved_messages,
                "completedTurnCount": completed_turn_count,
            }

        async def _finalize_async(reply_text: str) -> dict[str, Any]:
            finalized = await asyncio.to_thread(_finalize, reply_text)
            if finalized and not is_side_agent:
                from cyrene.workbench.project_memory_prompt import (
                    completed_context_snapshot,
                    schedule_learning,
                    should_auto_trigger,
                )

                turn_count = int(finalized.get("completedTurnCount") or 0)
                snapshot = await asyncio.to_thread(
                    completed_context_snapshot,
                    chat_id,
                    project_id,
                    completed_turn_count=turn_count,
                    final_assistant_text=str(reply_text or ""),
                )
                if (
                    snapshot
                    and not command
                    and not retry
                    and should_auto_trigger(turn_count)
                ):
                    schedule_learning(
                        project_id,
                        snapshot,
                        source="conversation_auto",
                        reason=f"completed_turn_{turn_count}",
                    )
            return finalized

        def _restore_retry_state() -> None:
            if retry_state_backup is None:
                return
            state_path, previous = retry_state_backup
            try:
                if previous is None:
                    state_path.unlink(missing_ok=True)
                else:
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    state_path.write_bytes(previous)
            except Exception:
                logger.exception("Failed to restore retry state for %s", chat_id)

        def _commit_retry_cut(target_chat: dict[str, Any]) -> None:
            if not retry or not truncate_after_id:
                return
            # Delete only the stale tail captured when retry began. Guidance or
            # proactive entries added during the new run must survive.
            _remove_retry_replaced_messages(
                target_chat, truncate_after_id, retry_replaced_message_ids
            )

        def _settle_status() -> None:
            fresh = _read_chats_store()
            fresh_chat = _find_chat(fresh, chat_id)
            if fresh_chat and fresh_chat.get("status") == "running":
                fresh_chat["status"] = "idle"
                _write_chats_store(fresh)

        def _stash_chat_pending(pending: dict[str, Any] | None) -> list[dict[str, Any]]:
            """Persist a paused run's pending question on the chat record so the
            transcript shows an answer prompt (not the raw awaiting-user sentinel)."""
            fresh = _read_chats_store()
            fresh_chat = _find_chat(fresh, chat_id)
            if not fresh_chat:
                return []
            saved_messages: list[dict[str, Any]] = []
            fresh_chat["status"] = "idle"
            if pending:
                fresh_chat["pendingQuestion"] = pending
                state_messages = _session_state_messages(chat_id)
                timeline_entries, usage, files = _extract_exchange_timeline(
                    state_messages,
                    state_ids_before,
                    include_open_tool_preamble=True,
                )
                model_name = (
                    _last_exchange_model(state_messages, state_ids_before)
                    or str(fresh_chat.get("model") or "")
                )
                for entry in timeline_entries:
                    entry.setdefault("model", model_name)
                question_entry = _pending_question_message(
                    pending,
                    usage=usage,
                    files=files,
                    model=model_name,
                )
                saved_messages = [*timeline_entries, question_entry]
                fresh_chat["lastModel"] = model_name
                _merge_chat_messages_chronologically(
                    fresh_chat, saved_messages
                )
            else:
                fresh_chat.pop("pendingQuestion", None)
            fresh_chat["updatedAt"] = _utc_now_iso()
            _write_chats_store(fresh)
            return [_public_message(item) for item in saved_messages]

        async def run_non_streaming(run: ChatRun) -> None:
            changes_before = await _capture_workspace_changes_baseline(
                workspace_dir, run.run_id
            )
            try:
                reply = await _run()
            except asyncio.CancelledError:
                await _finalize_workspace_changes(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=changes_before,
                    status="cancelled",
                    run=run,
                )
                await asyncio.to_thread(_restore_retry_state)
                raise
            except Exception as exc:
                logger.exception("Workbench chat run failed for %s", chat_id)
                await _finalize_workspace_changes(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=changes_before,
                    status="error",
                    run=run,
                )
                await asyncio.to_thread(_restore_retry_state)
                await asyncio.to_thread(_settle_status)
                from cyrene.observability import debug
                await debug.publish_event({
                    "type": "workbench_chat_changed",
                    "change": "settled",
                    "session_id": chat_id,
                    "chat_id": chat_id,
                    "project_id": project_id,
                }, session_id=chat_id)
                run.outcome = {"kind": "error", "exc": exc}
                return
            run.status = "finishing"
            if reply == R._AWAITING_USER_SENTINEL:
                await _finalize_workspace_changes(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=changes_before,
                    status="awaiting_user",
                    run=run,
                )
                if retry:
                    def commit_retry() -> None:
                        fresh = _read_chats_store()
                        fresh_chat = _find_chat(fresh, chat_id)
                        if fresh_chat:
                            _commit_retry_cut(fresh_chat)
                            _write_chats_store(fresh)
                    await asyncio.to_thread(commit_retry)
                pending = await asyncio.to_thread(R._workbench_pending_question_for, chat_id)
                awaiting_messages = await asyncio.to_thread(_stash_chat_pending, pending)
                run.outcome = {"kind": "awaiting", "pending": pending}
                run.outcome["assistantMessages"] = awaiting_messages
                return
            await _finalize_workspace_changes(
                chat_id=chat_id,
                run_id=run.run_id,
                workspace_dir=workspace_dir,
                before=changes_before,
                status="completed",
                run=run,
            )
            finalized = await _finalize_async(reply)
            run.outcome = {
                "kind": "reply",
                "payload": finalized,
            }

        if not wants_stream:
            run, is_new = _CHAT_RUN_MANAGER.start_or_get(
                chat_id,
                {"type": "ack", "chatId": chat_id},
                run_non_streaming,
                stream=False,
            )
            if not is_new:
                return JSONResponse(
                    {"error": "chat already has a running reply", "code": "chat_run_in_progress"},
                    status_code=409,
                )
            await run.done.wait()
            outcome = run.outcome or {}
            kind = str(outcome.get("kind") or "")
            if kind == "error":
                exc = outcome.get("exc")
                if not isinstance(exc, Exception):
                    exc = RuntimeError("agent run failed")
                message = _workbench_chat_run_error_message(exc, lang)
                error = message if isinstance(exc, httpx.TransportError) else "agent run failed"
                return JSONResponse(
                    {
                        "error": error,
                        "detail": str(exc),
                        **_workbench_chat_error_metadata(exc),
                    },
                    status_code=502,
                )
            if kind == "awaiting":
                pending = outcome.get("pending")
                return {
                    "ok": True,
                    "awaitingUser": True,
                    "pendingQuestion": pending,
                    "assistantMessages": outcome.get("assistantMessages") or [],
                    "userMessage": _public_message(user_entry),
                    "retry": retry,
                    "retryReplacedMessageIds": sorted(retry_replaced_message_ids),
                }
            finalized = outcome.get("payload")
            if not isinstance(finalized, dict):
                finalized = {}
            return {
                "ok": True,
                "userMessage": _public_message(user_entry),
                "assistantMessage": finalized.get("assistantMessage") or {},
                "assistantMessages": finalized.get("assistantMessages") or [],
                "retry": retry,
            }

        ack: dict[str, Any] = {"type": "ack", "chatId": chat_id}
        if retry:
            ack["retry"] = True
            ack["truncateAfterMessageId"] = truncate_after_id
        else:
            ack["userMessage"] = _public_message(user_entry)

        async def run_streaming(run: ChatRun) -> None:
            changes_before = await _capture_workspace_changes_baseline(
                workspace_dir, run.run_id
            )
            live_segments_stop = asyncio.Event()
            live_segments_task = asyncio.create_task(
                _publish_live_exchange_segments_loop(run, chat_id, state_ids_before, live_segments_stop)
            )
            try:
                try:
                    reply = await _run()
                except asyncio.CancelledError:
                    await _finalize_workspace_changes(
                        chat_id=chat_id,
                        run_id=run.run_id,
                        workspace_dir=workspace_dir,
                        before=changes_before,
                        status="cancelled",
                        run=run,
                    )
                    await asyncio.to_thread(_restore_retry_state)
                    raise
                except Exception as exc:
                    logger.exception("Workbench chat streaming run failed for %s", chat_id)
                    await _finalize_workspace_changes(
                        chat_id=chat_id,
                        run_id=run.run_id,
                        workspace_dir=workspace_dir,
                        before=changes_before,
                        status="error",
                        run=run,
                    )
                    await asyncio.to_thread(_restore_retry_state)
                    await asyncio.to_thread(_settle_status)
                    run.outcome = {"kind": "error", "exc": exc}
                    await run.publish({
                        "type": "error",
                        "error": "model_call_failed",
                        "message": _workbench_chat_run_error_message(exc, lang),
                        **_workbench_chat_error_metadata(exc),
                    })
                    return
                # The agent has returned and can no longer absorb new guidance.
                # Keep the run available for stream finalization/replay, but make
                # the guidance endpoint reject this narrow terminal window.
                run.status = "finishing"
                live_segments_stop.set()
                await live_segments_task
                if reply == R._AWAITING_USER_SENTINEL:
                    await _finalize_workspace_changes(
                        chat_id=chat_id,
                        run_id=run.run_id,
                        workspace_dir=workspace_dir,
                        before=changes_before,
                        status="awaiting_user",
                        run=run,
                    )
                    # Run paused for a permission / clarification answer — surface
                    # the question instead of streaming the sentinel as a reply.
                    if retry:
                        def commit_stream_retry() -> None:
                            fresh = _read_chats_store()
                            fresh_chat = _find_chat(fresh, chat_id)
                            if fresh_chat:
                                _commit_retry_cut(fresh_chat)
                                _write_chats_store(fresh)
                        await asyncio.to_thread(commit_stream_retry)
                    pending = await asyncio.to_thread(R._workbench_pending_question_for, chat_id)
                    awaiting_messages = await asyncio.to_thread(_stash_chat_pending, pending)
                    run.outcome = {"kind": "awaiting", "pending": pending}
                    await run.publish({
                        "type": "awaiting_user",
                        "pending_question": pending,
                        "assistantMessages": awaiting_messages,
                        "retry": retry,
                        "retryReplacedMessageIds": sorted(retry_replaced_message_ids),
                        "truncateAfterMessageId": truncate_after_id,
                    })
                    return
                if not run.saw_reply_events:
                    await run.publish({"type": "reply_start"})
                    for chunk in R._reply_stream_chunks(reply):
                        await run.publish({"type": "reply_delta", "delta": chunk})
                # A streamed model call can finish before the agent reopens the
                # tool channel, so its reply_done is not necessarily the text
                # that _finalize_async will persist. Publish one authoritative
                # terminal snapshot from the agent coroutine's return value.
                # The client replaces (rather than appends) on reply_done, which
                # also makes this harmless when the last model call already
                # streamed exactly the same text.
                await run.publish({"type": "reply_done", "response": reply})
                # The agent coroutine has returned and only durable finalization
                # remains. The UI can stop tool animations without pretending the
                # transcript and workspace change set are already saved.
                await run.publish({
                    "type": "run_finalizing",
                    "chatId": chat_id,
                    "runId": run.run_id,
                })
                await _finalize_workspace_changes(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=changes_before,
                    status="completed",
                    run=run,
                )
                finalized = await _finalize_async(reply)
                saved_event = {
                    "type": "saved",
                    **finalized,
                    "retry": retry,
                    "retryReplacedMessageIds": sorted(retry_replaced_message_ids),
                    "truncateAfterMessageId": truncate_after_id,
                }
                run.outcome = {"kind": "reply", "payload": saved_event}
                await run.publish(saved_event)
            finally:
                if not live_segments_stop.is_set():
                    live_segments_stop.set()
                    try:
                        await live_segments_task
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.debug("Workbench chat live segment publisher failed for %s", chat_id, exc_info=True)
                await asyncio.to_thread(_settle_status)
                from cyrene.observability import debug
                await debug.publish_event({
                    "type": "workbench_chat_changed",
                    "change": "settled",
                    "session_id": chat_id,
                    "chat_id": chat_id,
                    "project_id": project_id,
                }, session_id=chat_id)

        run, is_new = _CHAT_RUN_MANAGER.start_or_get(
            chat_id,
            ack,
            run_streaming,
            stream=True,
        )
        if is_new:
            from cyrene.observability import debug
            await debug.publish_event({
                "type": "workbench_chat_changed",
                "change": "running",
                "session_id": chat_id,
                "chat_id": chat_id,
                "project_id": project_id,
            }, session_id=chat_id)
        if detached:
            if not is_new:
                return JSONResponse(
                    {
                        "error": "chat already has a running reply",
                        "code": "chat_run_in_progress",
                    },
                    status_code=409,
                )
            return JSONResponse(
                {
                    "run_id": run.run_id,
                    "chat_id": chat_id,
                    "status": run.status,
                    "created_at": run.created_at,
                    "event_cursor": 0,
                },
                status_code=202,
            )
        return StreamingResponse(
            _CHAT_RUN_MANAGER.stream(run),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/api/workbench/chats/{chat_id}/messages")
    async def api_workbench_chat_send(
        chat_id: str, body_model: api_models.ChatMessageBody
    ):
        return await _workbench_chat_send_impl(
            chat_id,
            api_models.body_dict(body_model),
        )

    @router.post("/api/workbench/chats/{chat_id}/actions")
    async def api_workbench_chat_action(
        chat_id: str, body_model: api_models.ChatActionBody
    ):
        """Handle a `:::button` click (block_actions protocol).

        ``mode: "model"`` buttons land here. The source message is updated in
        place (chat.update semantics: the clicked block flips to
        ``disabled: true`` so one click is consumed exactly once), then the
        event is routed through the normal send pipeline as a user turn so
        the agent can answer semantically and the reply is appended.
        """
        body = api_models.body_dict(body_model)
        action_id = str(body.get("actionId") or "").strip()
        value = str(body.get("value") or "")
        message_id = str(body.get("messageId") or "").strip()
        if not action_id or not message_id:
            return JSONResponse(
                {"error": "actionId and messageId are required"}, status_code=400
            )
        # Mirrors the frontend spec validation: the event router is an attack
        # surface, so action ids stay whitelisted and bounded.
        if not re.fullmatch(r"[a-z0-9_]+", action_id) or len(action_id) > 32:
            return JSONResponse({"error": "invalid action_id"}, status_code=400)
        if len(value) > 256:
            return JSONResponse({"error": "value too long"}, status_code=400)
        if chat_id.startswith("legacy:"):
            return JSONResponse(
                {"error": "legacy chats cannot run actions"}, status_code=403
            )

        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        messages = chat.get("messages") if isinstance(chat.get("messages"), list) else []
        target = next(
            (
                entry
                for entry in messages
                if str(entry.get("id") or "") == message_id
            ),
            None,
        )
        if target is None:
            return JSONResponse({"error": "message not found"}, status_code=404)
        if str(target.get("role") or "") != "assistant":
            return JSONResponse(
                {"error": "actions target assistant messages"}, status_code=400
            )

        content = str(target.get("content") or "")
        if not has_button_block(content, action_id):
            return JSONResponse(
                {"error": "action not found in message"}, status_code=404
            )
        updated_content, label = disable_button_block(content, action_id)
        if updated_content is None:
            # The block is already disabled: a duplicate click. Reject so the
            # event stays idempotent regardless of client retries.
            return JSONResponse(
                {"error": "action already handled", "code": "action_duplicate"},
                status_code=409,
            )
        target["content"] = updated_content
        chat["updatedAt"] = _utc_now_iso()
        await asyncio.to_thread(_write_chats_store, payload)

        label_text = label or action_id
        if value:
            label_text = f"{label_text} ({action_id}: {value})"
        # Route through the full send pipeline: budget gate, permission mode,
        # model selection and run/finalize are all handled there.
        return await _workbench_chat_send_impl(chat_id, {
            "message": f"[按钮操作] {label_text}",
            "stream": False,
        })

    @router.post("/api/workbench/chats/{chat_id}/fork")
    async def api_workbench_chat_fork(
        chat_id: str, body_model: api_models.ChatForkBody
    ):
        """Fork a conversation at an edited user message.

        Creates a new chat with the prefix transcript (everything before the
        edited user message) plus a NEW user entry bearing the edited content
        and the original attachments. The source chat is preserved untouched.
        The agent's raw state is copied from the source session and truncated at
        the same user-message boundary so the forked chat can replay the edit
        through a normal send (``{ retry: true, forkReplay: true }``) without
        re-truncating. The agent is NOT run here.
        """
        from cyrene.agent.state import _session_state_file

        body = api_models.body_dict(body_model)
        message_id = str(body.get("messageId") or "").strip()
        new_content = str(body.get("content") or "").strip()
        if not message_id:
            return JSONResponse({"error": "messageId is required"}, status_code=400)
        if not new_content:
            return JSONResponse({"error": "content is required"}, status_code=400)
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "legacy chats cannot be forked"}, status_code=403)

        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        messages = chat.get("messages") if isinstance(chat.get("messages"), list) else []
        if not messages:
            return JSONResponse({"error": "chat has no messages"}, status_code=400)

        edit_index = -1
        for index, entry in enumerate(messages):
            if str(entry.get("id") or "") == message_id:
                edit_index = index
                break
        if edit_index < 0:
            return JSONResponse({"error": "message not found"}, status_code=404)
        if str(messages[edit_index].get("role") or "") != "user":
            return JSONResponse({"error": "can only edit user messages"}, status_code=400)

        # User-message ordinal (1-indexed) of the edited turn — this is the
        # boundary at which the raw state will be truncated.
        user_ordinal = sum(
            1 for entry in messages[:edit_index + 1]
            if str(entry.get("role") or "") == "user"
        )

        R = _routes()
        project_id = str(chat.get("projectId") or "")
        now = _utc_now_iso()
        new_chat = _new_chat(
            project_id,
            str(chat.get("title") or ""),
            str(chat.get("model") or R._get_model()),
            project_memory_snapshot=(
                dict(chat.get("projectMemorySnapshot") or {})
                if isinstance(chat.get("projectMemorySnapshot"), dict)
                else None
            ),
        )
        new_chat["forkedFromChatId"] = chat_id
        new_chat["forkedAtMessageId"] = message_id
        if chat.get("workspaceOverride"):
            new_chat["workspaceOverride"] = str(chat["workspaceOverride"])
        # Immutable divergence snippet — the edited prompt that started this
        # branch. Captured here so the branch tree never has to diff transcripts.
        new_chat["forkMessage"] = new_content.replace("\n", " ").strip()[:80]

        # Prefix transcript: everything before the edited user message.
        # Strip usage from copied messages so the branch doesn't inherit the
        # parent's accumulated token counts in the overview sidebar.
        prefix = []
        for entry in messages[:edit_index]:
            copied = dict(entry)
            copied.pop("usage", None)
            prefix.append(copied)
        # New user entry bearing the edited text + original attachments.
        orig = messages[edit_index]
        edited_entry: dict[str, Any] = {
            "id": _short_id("msg"),
            "role": "user",
            "content": new_content,
            "createdAt": now,
        }
        if isinstance(orig.get("attachments"), list) and orig["attachments"]:
            edited_entry["attachments"] = orig["attachments"]
            # Preserve the private path-bearing attachments so the replay send
            # can rebuild the agent prompt + read-guard map (same as :1132-1136).
            if orig.get("agentAttachments"):
                edited_entry["agentAttachments"] = orig["agentAttachments"]
        new_chat["messages"] = prefix + [edited_entry]
        new_chat["completedTurnCount"] = _completed_turn_count({"messages": prefix})
        new_chat["updatedAt"] = now

        payload.setdefault("chats", []).insert(0, new_chat)
        await asyncio.to_thread(_write_chats_store, payload)

        # Seed the forked session's raw state from the source, truncated at the
        # same user-message boundary so the replay send appends the edited turn.
        new_chat_id = str(new_chat.get("id") or "")
        src_state = _session_state_file(chat_id)
        new_state = _session_state_file(new_chat_id)
        def seed_fork_state() -> None:
            new_state.parent.mkdir(parents=True, exist_ok=True)
            try:
                if src_state.exists():
                    shutil.copyfile(src_state, new_state)
                    truncated = _truncate_state_file_at_user_ordinal(new_state, user_ordinal)
                    if not truncated:
                        logger.warning(
                            "Fork state truncation missed user ordinal %d for %s (source %s) — "
                            "state may have been compacted; replay will use the existing prefix.",
                            user_ordinal, new_chat_id, chat_id,
                        )
                else:
                    atomic_write_json(new_state, {"messages": []})
            except Exception:
                logger.exception("Failed to seed fork state for %s", new_chat_id)

        await asyncio.to_thread(seed_fork_state)

        return {"ok": True, "chat": _public_chat_full(new_chat)}

    @router.post("/api/workbench/chats/{chat_id}/to-task")
    async def api_workbench_chat_to_task(
        chat_id: str, body_model: api_models.ChatToTaskBody
    ):
        """Promote a conversation into a task session of its project (开始执行)."""
        body = api_models.body_dict(body_model)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        R = _routes()
        store = await asyncio.to_thread(R._read_workbench_store)
        project = R._workbench_find_project(store, str(chat.get("projectId") or ""))
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        # Fallback signal when synthesis is unavailable: the last user message.
        last_user = ""
        for message in reversed(chat.get("messages") or []):
            if message.get("role") == "user" and str(message.get("content") or "").strip():
                last_user = str(message["content"]).strip()
                break

        # Synthesize a task brief from the WHOLE conversation unless the caller
        # passed explicit overrides for both title and goal.
        override_title = str(body.get("title") or "").strip()
        override_goal = str(body.get("goal") or "").strip()
        brief: dict[str, Any] = {}
        if not (override_title and override_goal):
            synthesized = await _summarize_chat_to_brief(chat, project)
            if isinstance(synthesized, dict):
                brief = synthesized

        from_synthesis = bool(brief)
        title = (override_title or str(brief.get("title") or "").strip()
                 or str(chat.get("title") or "").strip() or "新任务")[:80] or "新任务"
        goal = (override_goal or str(brief.get("goal") or "").strip() or last_user or title).strip()
        constraints = _coerce_brief_constraints(brief.get("constraints"))
        acceptance = _coerce_brief_acceptance(brief.get("acceptanceCriteria"))

        session = R._workbench_new_session(project.get("id"), title, goal)
        if constraints:
            session["constraints"] = constraints
        if acceptance:
            session["acceptanceCriteria"] = acceptance
        session["sourceChatId"] = chat_id
        session["events"] = [{
            "id": _short_id("event"),
            "type": "CreatedFromChat",
            "createdAt": _utc_now_iso(),
            "body": (
                f"由对话「{chat.get('title') or '新对话'}」综合整理而来（已通读完整对话）。"
                if from_synthesis else
                f"由对话「{chat.get('title') or '新对话'}」创建。"
            ),
            "chatId": chat_id,
        }]
        project.setdefault("sessions", []).insert(0, session)
        project["updatedAt"] = session["createdAt"]
        store["activeProjectId"] = project.get("id")
        store["activeSessionId"] = session["id"]
        await asyncio.to_thread(R._write_workbench_store, store)

        # Keep the original conversation and link it to the task, so it's clearly
        # preserved (never consumed) and reachable from both sides.
        chat["convertedSessionId"] = session["id"]
        chat["convertedTaskTitle"] = title
        chat["convertedAt"] = session["createdAt"]
        await asyncio.to_thread(_write_chats_store, payload)
        await asyncio.to_thread(
            append_notification,
            title="对话已转为任务",
            body=f"对话「{chat.get('title') or '新对话'}」已创建任务「{title}」。",
            tab="comment",
            project_ref=project.get("id"),
            source="chat_to_task",
            source_label="任务",
            link_label=title,
            meta={"chatId": chat_id, "sessionId": session["id"]},
        )
        return {"ok": True, "session": session, **store}

    @router.post("/api/workbench/chats/{chat_id}/answer")
    async def api_workbench_chat_answer(
        chat_id: str, body_model: api_models.AnswerBody
    ):
        """Answer a paused chat run's permission / clarification question and
        resume the SAME round. Returns the continued reply (appended as an
        assistant message) or a follow-up question. Session-scoped to this chat."""
        body = api_models.body_dict(body_model)
        if bool(body.get("stream")):
            async def event_stream():
                from cyrene.agent.context import bind_run_context

                queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
                saw_reply_events = False
                subscriber_active = True

                async def publish(event: dict[str, Any]) -> None:
                    if subscriber_active:
                        await queue.put(dict(event))

                next_body = api_models.AnswerBody(**{**body, "stream": False})
                binding = bind_run_context(
                    reply_stream_writer=publish,
                    runtime_event_writer=publish,
                )
                try:
                    task = asyncio.create_task(
                        api_workbench_chat_answer(chat_id, next_body)
                    )
                    _DETACHED_ANSWER_TASKS.add(task)
                    task.add_done_callback(_finish_detached_answer_task)
                finally:
                    binding.reset()

                try:
                    while True:
                        if task.done() and queue.empty():
                            break
                        try:
                            event = await asyncio.wait_for(queue.get(), timeout=0.1)
                        except asyncio.TimeoutError:
                            continue
                        if str(event.get("type") or "").startswith("reply_"):
                            saw_reply_events = True
                        yield json.dumps(event, ensure_ascii=False) + "\n"

                    response = await task
                    if isinstance(response, JSONResponse):
                        try:
                            error_payload = json.loads(bytes(response.body).decode("utf-8"))
                        except Exception:
                            error_payload = {}
                        yield json.dumps({
                            "type": "error",
                            "error": str(error_payload.get("error") or "answer_failed"),
                            "message": str(
                                error_payload.get("detail")
                                or error_payload.get("error")
                                or "Failed to resume the conversation."
                            ),
                        }, ensure_ascii=False) + "\n"
                        return

                    if not isinstance(response, dict):
                        yield json.dumps({
                            "type": "error",
                            "error": "invalid_answer_response",
                            "message": "Invalid answer response from the daemon.",
                        }, ensure_ascii=False) + "\n"
                        return
                    if bool(response.get("awaitingUser")):
                        yield json.dumps({
                            "type": "awaiting_user",
                            "pending_question": response.get("pendingQuestion"),
                        }, ensure_ascii=False) + "\n"
                        return
                    assistant = response.get("assistantMessage")
                    reply = (
                        str(assistant.get("content") or "")
                        if isinstance(assistant, dict)
                        else ""
                    )
                    if not saw_reply_events:
                        yield json.dumps({"type": "reply_start"}, ensure_ascii=False) + "\n"
                        if reply:
                            yield json.dumps({
                                "type": "reply_delta",
                                "delta": reply,
                            }, ensure_ascii=False) + "\n"
                    # Always publish one authoritative terminal snapshot. The
                    # renderer replaces/settles its accumulated text from this.
                    yield json.dumps({
                        "type": "reply_done",
                        "response": reply,
                    }, ensure_ascii=False) + "\n"
                    yield json.dumps({
                        "type": "saved",
                        "assistantMessage": assistant or {},
                        "assistantMessages": response.get("assistantMessages") or [],
                    }, ensure_ascii=False) + "\n"
                finally:
                    # The continuation owns persistence for the resumed round.
                    # Detaching a terminal subscriber must not cancel that work.
                    subscriber_active = False

            return StreamingResponse(
                event_stream(),
                media_type="application/x-ndjson",
                headers={"Cache-Control": "no-cache"},
            )

        question_id = str(body.get("question_id") or "").strip()
        answer_text = str(body.get("answer") or body.get("selected_option") or "").strip()
        processing_started_at = time.monotonic()
        from cyrene.agent.state import PERMISSION_MODES
        requested_mode = str(body.get("mode") or "").strip().lower()
        if not question_id or not answer_text:
            return JSONResponse({"error": "question_id and answer are required"}, status_code=400)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        is_side_agent = str(chat.get("kind") or "") == "side-agent"
        stored_mode = str(chat.get("permissionMode") or "").strip().lower()
        if requested_mode:
            mode = requested_mode if requested_mode in PERMISSION_MODES else "default"
        else:
            mode = stored_mode if stored_mode in PERMISSION_MODES else "default"
        chat["permissionMode"] = mode
        pending = chat.get("pendingQuestion") if isinstance(chat.get("pendingQuestion"), dict) else None
        if not pending or str(pending.get("id") or "") != question_id:
            return JSONResponse({"error": "no matching pending question"}, status_code=409)

        R = _routes()
        project_id = str(chat.get("projectId") or "")
        project_store = await asyncio.to_thread(R._read_workbench_store)
        project = R._workbench_find_project(project_store, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        try:
            workspace_dir = _resolve_chat_workspace_dir(
                chat, project, R._workbench_resolve_workspace_dir
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        now = _utc_now_iso()
        answer_entry: dict[str, Any] = {
            "id": _short_id("msg"),
            "role": "user",
            "content": answer_text,
            "createdAt": now,
            "answerToQuestionId": question_id,
        }
        _merge_chat_messages_chronologically(chat, [answer_entry])
        _mark_user_activity(chat, now)
        await asyncio.to_thread(_write_chats_store, payload)
        state_ids_before_resume: set[str] = set()
        for m in await asyncio.to_thread(_session_state_messages, chat_id):
            mid = str(m.get("message_id") or m.get("id") or "").strip()
            if mid:
                state_ids_before_resume.add(mid)
        resume_run_id = f"resume_{uuid.uuid4().hex}"
        changes_before = await _capture_workspace_changes_baseline(
            workspace_dir, resume_run_id
        )
        try:
            if mode == "default":
                reply = await R._workbench_answer_pending(
                    chat_id, question_id, answer_text, workspace_dir,
                )
            else:
                reply = await R._workbench_answer_pending(
                    chat_id, question_id, answer_text, workspace_dir,
                    permission_mode=mode,
                )
        except asyncio.CancelledError:
            await _finalize_workspace_changes(
                chat_id=chat_id,
                run_id=resume_run_id,
                workspace_dir=workspace_dir,
                before=changes_before,
                status="cancelled",
            )
            raise
        except Exception as exc:
            await _finalize_workspace_changes(
                chat_id=chat_id,
                run_id=resume_run_id,
                workspace_dir=workspace_dir,
                before=changes_before,
                status="error",
            )
            logger.exception("Workbench chat answer-resume failed for %s", chat_id)
            return JSONResponse(
                {
                    "error": "answer resume failed",
                    "detail": str(exc),
                    **_workbench_chat_error_metadata(exc),
                },
                status_code=502,
            )

        await _finalize_workspace_changes(
            chat_id=chat_id,
            run_id=resume_run_id,
            workspace_dir=workspace_dir,
            before=changes_before,
            status="awaiting_user" if reply == R._AWAITING_USER_SENTINEL else "completed",
        )

        if reply == R._AWAITING_USER_SENTINEL:
            new_pending = await asyncio.to_thread(R._workbench_pending_question_for, chat_id)

            resume_state_messages = await asyncio.to_thread(_session_state_messages, chat_id)

            def extract_pending() -> tuple[list[dict[str, Any]], dict[str, Any], list[Any]]:
                return _extract_exchange_timeline(
                    resume_state_messages,
                    state_ids_before_resume,
                    include_open_tool_preamble=True,
                )

            timeline_entries, usage, files = await asyncio.to_thread(extract_pending)
            pending_model = (
                _last_exchange_model(resume_state_messages, state_ids_before_resume)
                or str(chat.get("model") or "")
            )
            for entry in timeline_entries:
                entry.setdefault("model", pending_model)
            additions = [
                *timeline_entries,
                *([
                    _pending_question_message(
                        new_pending,
                        usage=usage,
                        files=files,
                        model=pending_model,
                    )
                ] if new_pending else []),
            ]
            await asyncio.to_thread(
                _stash_chat_pending_for, chat_id, new_pending, additions=additions
            )
            await asyncio.to_thread(
                _record_chat_run_outcome,
                chat_id,
                run_id=resume_run_id,
                status="done",
                termination_reason="awaiting_user",
                outcome_kind="awaiting",
                created_at=now,
            )
            return {
                "ok": True,
                "awaitingUser": True,
                "pendingQuestion": new_pending,
                "userMessage": _public_message(answer_entry),
            }

        answer_state_messages = await asyncio.to_thread(_session_state_messages, chat_id)

        def extract_answer() -> tuple[list[dict[str, Any]], dict[str, Any], list[Any]]:
            return _extract_exchange_timeline(
                answer_state_messages, state_ids_before_resume
            )

        timeline_entries, usage, files = await asyncio.to_thread(extract_answer)
        fresh = await asyncio.to_thread(_read_chats_store)
        fresh_chat = _find_chat(fresh, chat_id)
        if not fresh_chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        model_name = (
            _last_exchange_model(answer_state_messages, state_ids_before_resume)
            or str(fresh_chat.get("model") or "")
        )
        for entry in timeline_entries:
            entry.setdefault("model", model_name)
        assistant_entry: dict[str, Any] = {
            "id": _short_id("msg"),
            "role": "assistant",
            "content": str(reply or ""),
            "createdAt": _utc_now_iso(),
            "model": model_name,
            "processingDurationMs": max(
                0, int(round((time.monotonic() - processing_started_at) * 1000))
            ),
        }
        if any(usage.values()):
            assistant_entry["usage"] = usage
        if files:
            assistant_entry["attachments"] = files
        saved_messages = [*timeline_entries, assistant_entry]
        _merge_chat_messages_chronologically(fresh_chat, saved_messages)
        completed_turn_count = _next_completed_turn_count(
            fresh_chat,
            is_side_agent=is_side_agent,
        )
        fresh_chat["completedTurnCount"] = completed_turn_count
        fresh_chat["lastModel"] = model_name
        fresh_chat["status"] = "idle"
        fresh_chat.pop("pendingQuestion", None)
        fresh_chat["updatedAt"] = assistant_entry["createdAt"]
        await asyncio.to_thread(_write_chats_store, fresh)
        await asyncio.to_thread(complete_chat_plan, chat_id)
        # Answer-resume runs do not pass through ChatRunManager, whose normal
        # finalizer projects the terminal outcome into ``lastRun``.  Record the
        # resumed reply explicitly so the lightweight conversation list cannot
        # fall back to the original paused run's stale ``awaiting`` outcome.
        await asyncio.to_thread(
            _record_chat_run_outcome,
            chat_id,
            run_id=resume_run_id,
            status="done",
            termination_reason="completed",
            outcome_kind="reply",
            created_at=now,
        )
        try:
            await asyncio.to_thread(
                archive_session_exchange,
                chat_id,
                answer_text,
                str(reply or ""),
                workspace_dir=workspace_dir,
                session_title=str(fresh_chat.get("title") or ""),
            )
        except Exception:
            logger.exception("Failed to archive workbench conversation %s", chat_id)
        if project_id and not is_side_agent:
            from cyrene.workbench.project_memory_prompt import (
                completed_context_snapshot,
                schedule_learning,
                should_auto_trigger,
            )

            snapshot = await asyncio.to_thread(
                completed_context_snapshot,
                chat_id,
                project_id,
                completed_turn_count=completed_turn_count,
                final_assistant_text=str(reply or ""),
            )
            if snapshot and should_auto_trigger(completed_turn_count):
                schedule_learning(
                    project_id,
                    snapshot,
                    source="conversation_auto",
                    reason=f"completed_turn_{completed_turn_count}",
                )
        return {
            "ok": True,
            "awaitingUser": False,
            "userMessage": _public_message(answer_entry),
            "assistantMessage": _public_message(assistant_entry),
            "assistantMessages": [_public_message(item) for item in saved_messages],
        }

    return {
        "list_chats": api_workbench_list_chats,
        "create_chat": api_workbench_create_chat,
        "update_chat": api_workbench_update_chat,
        "delete_chat": api_workbench_delete_chat,
        "get_chat": api_workbench_get_chat,
        "send_chat_detached": _workbench_chat_send_impl,
        "guide_chat": api_workbench_chat_guidance,
        "answer_chat": api_workbench_chat_answer,
        "run_manager": _CHAT_RUN_MANAGER,
    }


__all__ = ["register_workbench_chat_routes"]
