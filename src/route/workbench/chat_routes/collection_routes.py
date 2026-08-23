from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.chat_events import publish_chat_changed
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


def _register_list_route(router: APIRouter, context: ChatRouteContext):
    service = context.service
    _project_data_key = context.project_data_key
    _legacy_chats = service.legacy_chats
    _prune_orphaned_fork_metadata = service.prune_orphaned_fork_metadata
    _public_chat_light = service.public_chat_light
    _read_chats_store = service.repository.read
    _read_chat_summaries_store = service.repository.read_summaries
    _write_chats_store = service.repository.write

    @router.get("/api/workbench/chats")
    async def api_workbench_list_chats(project: str = ""):
        started = time.monotonic()
        # SQLite busy waits and JSON decoding are synchronous. Keep them off the
        # uvicorn event loop so one contended read cannot freeze every Workbench
        # request (the client otherwise reaches its 30s timeout as a group).
        payload = await asyncio.to_thread(_read_chat_summaries_store)
        if _prune_orphaned_fork_metadata(payload):
            full_payload = await asyncio.to_thread(_read_chats_store)
            if _prune_orphaned_fork_metadata(full_payload):
                await asyncio.to_thread(_write_chats_store, full_payload)
                payload = await asyncio.to_thread(_read_chat_summaries_store)
        data_key = await asyncio.to_thread(_project_data_key, project) if project else ""
        chats = [
            _public_chat_light(chat)
            for chat in payload.get("chats", [])
            if str(chat.get("kind") or "chat") == "chat" and (not project or str(chat.get("projectId") or "") == project)
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

    return api_workbench_list_chats


def _register_quick_targets_route(router: APIRouter, context: ChatRouteContext) -> None:
    service = context.service
    _routes = context.runtime
    _CHAT_RUN_MANAGER = service.run_manager
    _chat_preview = service.chat_preview
    _read_chat_summaries_store = service.repository.read_summaries

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
        store = await asyncio.to_thread(R.read_store)
        projects = store.get("projects", []) or []
        # The default project is identified by its data key, not its name — the
        # name follows the workspace directory and need not be "Cyrene".
        default_project = next(
            (p for p in projects if R.project_data_key(p) == "default"),
            None,
        )
        if default_project is None and projects:
            default_project = projects[0]
        project_by_id = {str(p.get("id") or ""): p for p in projects}

        query = str(q or "").strip().lower()
        limit = max(1, min(int(limit or 40), 200))

        payload = await asyncio.to_thread(_read_chat_summaries_store)
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
                "dataKey": R.project_data_key(default_project),
                "workspacePath": str(default_project.get("workspacePath") or ""),
                "model": str(default_project.get("model") or ""),
            }
        return {"defaultProject": default_payload, "targets": targets}


def _register_create_route(router: APIRouter, context: ChatRouteContext):
    service = context.service
    _routes = context.runtime

    @router.post("/api/workbench/chats")
    async def api_workbench_create_chat(body_model: api_models.ChatCreateBody):
        started = time.monotonic()
        body = api_models.body_dict(body_model)
        project_id = str(body.get("project") or body.get("projectId") or "").strip()
        if not project_id:
            return JSONResponse({"error": "project is required"}, status_code=400)
        R = _routes()
        project = await asyncio.to_thread(R.find_project_lightweight, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)

        from cyrene.workbench.project_memory_prompt import current_snapshot

        memory_snapshot = await asyncio.to_thread(current_snapshot, project_id)

        requested_agent = body.get("agent") if isinstance(body.get("agent"), dict) else None
        requested_installation_id = str((requested_agent or {}).get("installationId") or "").strip()
        agent_snapshot = None
        model_access_snapshot = None
        capabilities_snapshot = None
        if requested_installation_id:
            from cyrene.agent_runtime.builtin import BUILTIN_INSTALLATION_ID

            if requested_installation_id == BUILTIN_INSTALLATION_ID:
                agent_snapshot = {"installationId": BUILTIN_INSTALLATION_ID}
                model_access_snapshot = body.get("modelAccess") if isinstance(body.get("modelAccess"), dict) else None
            else:
                from cyrene.extensions import agent_runtime as extension_agents

                installation = await asyncio.to_thread(
                    extension_agents.get_agent_installation,
                    requested_installation_id,
                )
                if installation is None:
                    return JSONResponse(
                        {
                            "error": "Agent installation not found",
                            "code": "dependency_missing",
                            "failureKind": "dependency_missing",
                        },
                        status_code=404,
                    )
                if not bool(installation.get("enabled", True)):
                    return JSONResponse(
                        {
                            "error": "Agent installation is disabled",
                            "code": "agent_disabled",
                            "failureKind": "agent_disabled",
                        },
                        status_code=409,
                    )
                # Identity, driver and capabilities are server-owned. Never
                # persist a client-authored snapshot for an external Agent.
                agent_snapshot = {
                    "installationId": installation.get("installation_id", ""),
                    "agentId": installation.get("agent_id", ""),
                    "displayName": installation.get("display_name", ""),
                    "version": installation.get("version", ""),
                    "driver": installation.get("driver", ""),
                    "protocolVersion": installation.get("protocol_version", 1),
                }
                model_access_snapshot = dict(installation.get("model_access") or {"mode": "cyrene_managed", "profileId": "primary"})
                capabilities_snapshot = dict(installation.get("capabilities") or {})

        def create_and_persist() -> dict[str, Any]:
            payload = service.repository.read()
            chat = service.create_chat(
                project_id,
                str(body.get("title") or ""),
                R.get_model(),
                project_memory_snapshot=memory_snapshot,
                agent=agent_snapshot,
                model_access=model_access_snapshot,
                capabilities=capabilities_snapshot,
                soul_active=body.get("soulActive"),
                workspace_active=body.get("workspaceActive"),
                reasoning_effort=str(body.get("reasoningEffort") or ""),
            )
            payload.setdefault("chats", []).insert(0, chat)
            service.repository.write(payload)
            return chat

        chat = await asyncio.to_thread(create_and_persist)
        await publish_chat_changed(str(chat.get("id") or ""), project_id, "created")
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 250:
            logger.warning(
                "Slow Workbench chat creation [project=%s duration_ms=%.1f]",
                project_id,
                elapsed_ms,
            )
        return {"ok": True, "chat": service.public_chat_full(chat)}

    return api_workbench_create_chat


def register_collection_routes(
    router: APIRouter,
    context: ChatRouteContext,
) -> dict[str, Any]:
    list_chats = _register_list_route(router, context)
    _register_quick_targets_route(router, context)
    create_chat = _register_create_route(router, context)
    return {"list_chats": list_chats, "create_chat": create_chat}
