from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter

from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


def _composer_context_service():
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("composer_context")
    if service is None:
        raise RuntimeError(
            "Required Plugin application service is unavailable: composer_context"
        )
    return service


def _extensions_service():
    from cyrene.core.plugin import application_plugin_service

    return application_plugin_service("extensions")


def _register_list_route(router: APIRouter, context: ChatRouteContext):
    service = context.service
    _prune_orphaned_fork_metadata = service.prune_orphaned_fork_metadata
    _public_chats_light = service.public_chats_light
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
        source_chats = [
            chat
            for chat in payload.get("chats", [])
            if str(chat.get("kind") or "chat") == "chat" and (not project or str(chat.get("projectId") or "") == project)
        ]
        chats = _public_chats_light(source_chats)
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

        ``running`` reflects the authoritative in-flight run registry (not the
        persisted status, which can be stale after a crash).
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
            return localized_error_response(
                "A project is required.", "请选择项目。", 400, "project_required"
            )
        R = _routes()
        project = await asyncio.to_thread(R.find_project_lightweight, project_id)
        if not project:
            return localized_error_response(
                "Project not found.", "未找到项目。", 404, "project_not_found"
            )

        memory_snapshot = await context.project_memory_snapshot(project_id)

        composer_context = _composer_context_service()
        context_activations = composer_context.normalize(
            body.get("contextActivations")
        )

        requested_agent = body.get("agent") if isinstance(body.get("agent"), dict) else None
        requested_installation_id = str((requested_agent or {}).get("installationId") or "").strip()
        from cyrene.agent_runtime.builtin import BUILTIN_INSTALLATION_ID

        agent_snapshot = None
        model_access_snapshot = None
        capabilities_snapshot = None
        if requested_installation_id:
            if requested_installation_id == BUILTIN_INSTALLATION_ID:
                agent_snapshot = {"installationId": BUILTIN_INSTALLATION_ID}
                model_access_snapshot = body.get("modelAccess") if isinstance(body.get("modelAccess"), dict) else None
            else:
                extensions = _extensions_service()
                resolver = getattr(extensions, "get_agent_installation", None)
                installation = (
                    await asyncio.to_thread(resolver, requested_installation_id)
                    if callable(resolver)
                    else None
                )
                if installation is None:
                    return localized_error_response(
                        "Agent installation not found.",
                        "未找到 Agent 安装。",
                        404,
                        "dependency_missing",
                        failureKind="dependency_missing",
                    )
                if not bool(installation.get("enabled", True)):
                    return localized_error_response(
                        "The Agent installation is disabled.",
                        "该 Agent 安装已停用。",
                        409,
                        "agent_disabled",
                        failureKind="agent_disabled",
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

        if (
            requested_installation_id
            and requested_installation_id != BUILTIN_INSTALLATION_ID
            and any(context_activations.values())
        ):
            return localized_error_response(
                "Composer context capabilities require the built-in Cyrene Agent.",
                "编辑器上下文能力需要使用 Cyrene 内置 Agent。",
                400,
                "builtin_agent_required",
            )

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
        chat["contextActivations"] = context_activations
        chat["remoteDeviceIds"] = list(body.get("remoteDeviceIds") or ())
        try:
            workspace_dir = service.resolve_chat_workspace_dir(
                chat,
                project,
                R.resolve_workspace_dir,
            )
            resolved_input = service.resolve_composer_input_context(
                chat,
                workspace_dir,
                strict=True,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Invalid composer input context: %s", exc)
            invalid = isinstance(exc, ValueError)
            return localized_error_response(
                (
                    "The context configuration is invalid."
                    if invalid
                    else "The selected input context is unavailable."
                ),
                "上下文配置无效。" if invalid else "所选输入框上下文当前不可用。",
                400 if invalid else 503,
                (
                    "invalid_context_configuration"
                    if invalid
                    else "composer_context_unavailable"
                ),
            )
        chat["soulActive"] = bool(resolved_input["soulActive"])
        chat["workspaceActive"] = bool(resolved_input["workspaceActive"])
        chat["remoteDeviceIds"] = list(resolved_input["remoteDeviceIds"])
        chat["contextActivations"] = dict(resolved_input["contextActivations"])

        def create_and_persist() -> dict[str, Any]:
            payload = service.repository.read()
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
