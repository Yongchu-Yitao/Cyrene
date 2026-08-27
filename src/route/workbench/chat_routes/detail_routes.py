from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.chat_events import publish_chat_changed
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


def _merge_context_activity_messages(
    chat: dict[str, Any],
    activity_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add ContextTree-derived activity cards without duplicating persisted ones."""

    result = dict(chat)
    messages = [
        dict(message)
        for message in chat.get("messages") or []
        if isinstance(message, dict)
    ]
    known_ids = {
        str(message.get("id") or "")
        for message in messages
        if str(message.get("id") or "")
    }
    for activity in activity_messages:
        activity_id = str(activity.get("id") or "")
        if not activity_id or activity_id in known_ids:
            continue
        known_ids.add(activity_id)
        messages.append(dict(activity))
    messages.sort(
        key=lambda message: (
            not bool(str(message.get("createdAt") or "")),
            str(message.get("createdAt") or ""),
        )
    )
    result["messages"] = messages
    return result


def _register_get_route(router: APIRouter, context: ChatRouteContext):
    service = context.service
    _routes = context.runtime
    _prewarm_workspace_changes = service.prewarm_workspace_changes
    _prune_orphaned_fork_metadata = service.prune_orphaned_fork_metadata
    _public_chat_full = service.public_chat_full
    _read_chats_store = service.repository.read
    _read_chat_summaries_store = service.repository.read_summaries
    _get_workbench_chat = service.repository.get
    _resolve_chat_workspace_dir = service.resolve_chat_workspace_dir
    _sync_chat_generated_files = service.sync_chat_generated_files
    _write_chats_store = service.repository.write

    @router.get("/api/workbench/chats/{chat_id}")
    async def api_workbench_get_chat(chat_id: str):
        started = time.monotonic()
        summary_payload = await asyncio.to_thread(_read_chat_summaries_store)
        if _prune_orphaned_fork_metadata(summary_payload):
            full_payload = await asyncio.to_thread(_read_chats_store)
            if _prune_orphaned_fork_metadata(full_payload):
                await asyncio.to_thread(_write_chats_store, full_payload)
        chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        if "generatedFiles" not in chat:
            await asyncio.to_thread(_sync_chat_generated_files, chat_id)
            chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
            if not chat:
                return JSONResponse({"error": "chat not found"}, status_code=404)

        async def prewarm_opened_workspace() -> None:
            try:
                R = _routes()
                project_store = await asyncio.to_thread(R.read_store)
                project = R.find_project(project_store, str(chat.get("projectId") or ""))
                if project:
                    workspace_dir = _resolve_chat_workspace_dir(chat, project, R.resolve_workspace_dir)
                    _prewarm_workspace_changes(workspace_dir)
            except Exception:
                logger.debug(
                    "Workbench workspace snapshot prewarm skipped for %s",
                    chat_id,
                    exc_info=True,
                )

        asyncio.create_task(prewarm_opened_workspace())
        public_chat = _public_chat_full(chat)
        try:
            activity_messages = await context.conversation_context.activity_messages(
                chat_id
            )
        except Exception:
            logger.debug(
                "ContextTree activity-history projection skipped for %s",
                chat_id,
                exc_info=True,
            )
        else:
            public_chat = _merge_context_activity_messages(
                public_chat,
                activity_messages,
            )
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 1000:
            logger.warning("Slow Workbench chat detail load [chat_id=%s duration_ms=%.1f]", chat_id, elapsed_ms)
        return {"chat": public_chat}

    return api_workbench_get_chat


async def _apply_agent_binding(chat: dict[str, Any], body: dict[str, Any], default_model: str):
    if chat.get("messages"):
        return JSONResponse(
            {"error": "Agent binding can only change on an empty chat", "code": "agent_binding_locked"},
            status_code=409,
        )
    requested = body.get("agent") if isinstance(body.get("agent"), dict) else {}
    installation_id = str(requested.get("installationId") or "").strip()
    from cyrene.agent_runtime.builtin import BUILTIN_INSTALLATION_ID, normalize_agent_fields

    if not installation_id or installation_id == BUILTIN_INSTALLATION_ID:
        fields = normalize_agent_fields(
            {"installationId": BUILTIN_INSTALLATION_ID},
            body.get("modelAccess") if isinstance(body.get("modelAccess"), dict) else None,
            default_model=default_model,
        )
    else:
        from cyrene.extensions import agent_runtime as extension_agents

        installation = await asyncio.to_thread(extension_agents.get_agent_installation, installation_id)
        if installation is None:
            return JSONResponse({"error": "Agent installation not found", "code": "dependency_missing"}, status_code=404)
        if not bool(installation.get("enabled", True)):
            return JSONResponse({"error": "Agent installation is disabled", "code": "agent_disabled"}, status_code=409)
        fields = normalize_agent_fields(
            {
                "installationId": installation.get("installation_id", ""),
                "agentId": installation.get("agent_id", ""),
                "displayName": installation.get("display_name", ""),
                "version": installation.get("version", ""),
                "driver": installation.get("driver", ""),
                "protocolVersion": installation.get("protocol_version", 1),
            },
            dict(installation.get("model_access") or {"mode": "cyrene_managed", "profileId": "primary"}),
            capabilities_raw=dict(installation.get("capabilities") or {}),
        )
    chat.update(fields)
    if not installation_id or installation_id == BUILTIN_INSTALLATION_ID:
        from cyrene.workbench.composer_context import normalize_context_activations

        chat["contextActivations"] = normalize_context_activations(
            chat.get("contextActivations")
        )
    else:
        chat.pop("contextActivations", None)
    chat.pop("agentConfigOptions", None)
    chat.pop("agentConfigValues", None)
    chat.pop("modelSelectionId", None)
    return None


def _apply_agent_config_values(chat: dict[str, Any], values: Any):
    from cyrene.agent_runtime.builtin import normalize_agent_binding

    if normalize_agent_binding(chat.get("agent")).is_builtin:
        return JSONResponse({"error": "Built-in chats do not use Agent config options"}, status_code=400)
    if not isinstance(values, dict):
        return JSONResponse({"error": "agentConfigValues must be an object"}, status_code=400)
    allowed = {str(option.get("id") or ""): option for option in chat.get("agentConfigOptions") or [] if isinstance(option, dict) and option.get("id")}
    normalized_values: dict[str, Any] = {}
    for config_id, value in values.items():
        config_id = str(config_id or "")[:200]
        option = allowed.get(config_id)
        if option is None:
            return JSONResponse({"error": "Agent config option not found"}, status_code=400)
        if option.get("type") == "boolean":
            normalized_values[config_id] = bool(value)
        else:
            valid_values = {str(item.get("value") or "") for item in option.get("options") or [] if isinstance(item, dict)}
            value = str(value or "")[:500]
            if value not in valid_values:
                return JSONResponse({"error": "Agent config option value is invalid"}, status_code=400)
            normalized_values[config_id] = value
    chat.setdefault("agentConfigValues", {}).update(normalized_values)
    for config_id, value in normalized_values.items():
        option = allowed.get(config_id) or {}
        if str(option.get("category") or "") != "model" and config_id.lower() != "model":
            continue
        selected = next(
            (item for item in option.get("options") or [] if isinstance(item, dict) and str(item.get("value") or "") == str(value)),
            None,
        )
        chat["modelSelectionId"] = str(value)
        chat["model"] = str((selected or {}).get("name") or value)
        chat.pop("lastModel", None)
    return None


def _apply_model_selection(chat: dict[str, Any], selected_key: str) -> None:
    if not selected_key:
        return
    from cyrene.runtime.model_configuration import selectable_model_candidates

    selected = next(
        (item for item in selectable_model_candidates() if selected_key in {str(item.get("id") or ""), str(item.get("model") or ""), str(item.get("name") or "")}),
        None,
    )
    chat["modelSelectionId"] = selected_key
    chat["model"] = str((selected or {}).get("model") or (selected or {}).get("name") or selected_key)
    chat.pop("lastModel", None)


def _register_update_route(router: APIRouter, context: ChatRouteContext):
    service = context.service
    _routes = context.runtime
    _normalize_workspace_override = service.normalize_workspace_override
    _public_chat_full = service.public_chat_full
    _get_workbench_chat = service.repository.get
    _utc_now_iso = service.utc_now_iso
    _write_chat_store = service.repository.write_one

    @router.patch("/api/workbench/chats/{chat_id}")
    async def api_workbench_update_chat(chat_id: str, body_model: api_models.ChatUpdateBody):
        body = api_models.body_dict(body_model)
        chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        base_chat = copy.deepcopy(chat)
        R = _routes()
        if "title" in body:
            chat["title"] = str(body.get("title") or "").strip()[:60] or chat.get("title")
            chat["titleLocked"] = True
        if "agent" in body:
            error = await _apply_agent_binding(chat, body, R.get_model())
            if error is not None:
                return error
        if "agentConfigValues" in body:
            error = _apply_agent_config_values(chat, body.get("agentConfigValues"))
            if error is not None:
                return error
        if "model" in body:
            selected_key = str(body.get("model") or "").strip()
            _apply_model_selection(chat, selected_key)
        if "reasoningEffort" in body:
            effort = str(body.get("reasoningEffort") or "").strip().lower()
            if effort:
                chat["reasoningEffort"] = effort
            else:
                chat.pop("reasoningEffort", None)
        if "soulActive" in body:
            chat["soulActive"] = bool(body.get("soulActive"))
        if "workspaceActive" in body:
            chat["workspaceActive"] = bool(body.get("workspaceActive"))
        if "workspaceOverride" in body:
            try:
                override = _normalize_workspace_override(body.get("workspaceOverride"))
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            if override:
                chat["workspaceOverride"] = override
            else:
                chat.pop("workspaceOverride", None)
        if "contextActivations" in body:
            from cyrene.workbench.composer_context import validate_context_activations

            try:
                chat["contextActivations"] = validate_context_activations(
                    body.get("contextActivations")
                )
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
        from cyrene.agent_runtime.builtin import normalize_agent_binding

        if (
            not normalize_agent_binding(chat.get("agent")).is_builtin
            and any((chat.get("contextActivations") or {}).values())
        ):
            return JSONResponse(
                {"error": "Composer context capabilities require the built-in Cyrene Agent"},
                status_code=400,
            )
        chat["updatedAt"] = _utc_now_iso()
        await asyncio.to_thread(_write_chat_store, chat, base_chat=base_chat)
        await publish_chat_changed(
            chat_id,
            str(chat.get("projectId") or ""),
            "updated",
        )
        return {"ok": True, "chat": _public_chat_full(chat)}

    return api_workbench_update_chat


def register_detail_routes(
    router: APIRouter,
    context: ChatRouteContext,
) -> dict[str, Any]:
    get_chat = _register_get_route(router, context)
    update_chat = _register_update_route(router, context)
    return {"get_chat": get_chat, "update_chat": update_chat}
