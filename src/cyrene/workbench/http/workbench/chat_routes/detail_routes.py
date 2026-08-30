from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from typing import Any

from fastapi import APIRouter

from cyrene.plugins.builtin.cyrene_control.state import (
    read_plan_file,
    write_plan_file,
)
from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


def _normalize_plan_context_files(value: Any) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for raw in (value[:100] if isinstance(value, list) else ()):
        item = {"source": "workspace", "path": raw} if isinstance(raw, str) else raw
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "workspace")
        if source != "workspace":
            raise ValueError("invalid_plan")
        path = str(item.get("path") or "").strip()[:4_000]
        if path:
            files.append({
                "source": source,
                "path": path,
                "name": str(item.get("name") or "").strip()[:500],
            })
    return files


def _normalize_active_plan(value: Any, current: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("invalid_plan")
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ValueError("invalid_plan") from None
    if len(encoded) > 250_000:
        raise ValueError("plan_too_large")
    plan = copy.deepcopy(value)
    plan_id = str(plan.get("planId") or "").strip()
    title = str(plan.get("title") or "").strip()
    steps = plan.get("steps")
    if not plan_id or not title or not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError("invalid_plan")
    if len(title) > 500:
        raise ValueError("plan_too_large")
    current_plan = current if isinstance(current, dict) else {}
    current_id = str(current_plan.get("planId") or "").strip()
    if current_id and current_id != plan_id:
        raise ValueError("plan_changed")
    current_steps = {
        str(step.get("id") or ""): step
        for step in current_plan.get("steps") or ()
        if isinstance(step, dict) and str(step.get("id") or "")
    }
    plan_started = any(
        str(step.get("status") or "pending") != "pending"
        or step.get("startedAt")
        or step.get("completedAt")
        or step.get("progressEvents")
        or step.get("toolCalls")
        for step in current_steps.values()
    )
    normalized_steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            raise ValueError("invalid_plan")
        step = copy.deepcopy(raw)
        step_id = str(step.get("id") or f"step_{index}").strip()
        step_title = str(step.get("title") or step.get("content") or "").strip()
        if not step_id or step_id in seen or not step_title or len(step_title) > 500:
            raise ValueError("invalid_plan")
        seen.add(step_id)
        step["id"] = step_id
        step["title"] = step_title
        step["description"] = str(step.get("description") or "")[:20_000]
        dependencies: list[str] = []
        for item in (
            (step.get("dependsOn") or ())[:100]
            if isinstance(step.get("dependsOn") or (), list)
            else ()
        ):
            dependency = str(item or "").strip()
            if dependency and dependency not in dependencies:
                dependencies.append(dependency)
        step["dependsOn"] = dependencies
        step["command"] = str(step.get("command") or "")[:50_000]
        step["promptOverride"] = str(step.get("promptOverride") or "")[:50_000]
        step["note"] = str(step.get("note") or "")[:20_000]
        step["contextFiles"] = _normalize_plan_context_files(step.get("contextFiles"))
        step = {
            key: step[key]
            for key in (
                "id",
                "title",
                "description",
                "dependsOn",
                "command",
                "promptOverride",
                "note",
                "contextFiles",
            )
        }
        previous = current_steps.get(step_id)
        if previous and str(previous.get("status") or "pending") != "pending":
            step = copy.deepcopy(previous)
        else:
            step["status"] = str((previous or {}).get("status") or "pending")
        normalized_steps.append(step)
    if plan_started and list(current_steps) != [step["id"] for step in normalized_steps]:
        raise ValueError("plan_started")
    positions = {step["id"]: index for index, step in enumerate(normalized_steps)}
    if any(
        dependency not in positions or positions[dependency] >= index
        for index, step in enumerate(normalized_steps)
        for dependency in step.get("dependsOn") or ()
    ):
        raise ValueError("invalid_plan_order")
    plan["planId"] = plan_id
    plan["title"] = title
    plan["steps"] = normalized_steps
    return plan


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

    async def latest_file_plan(chat_id: str, chat: dict[str, Any]) -> dict[str, Any] | None:
        project = await asyncio.to_thread(
            _routes().find_project_lightweight,
            str(chat.get("projectId") or ""),
        )
        if not project:
            return None
        workspace_dir = service.resolve_chat_workspace_dir(
            chat,
            project,
            _routes().resolve_workspace_dir,
        )
        stored = await asyncio.to_thread(
            read_plan_file,
            workspace_dir,
            chat_id,
            expected_plan_id=str((chat.get("activePlan") or {}).get("planId") or ""),
        )
        if stored is None:
            return None
        try:
            return _normalize_active_plan(stored, stored)
        except ValueError:
            logger.warning("Ignoring invalid conversation plan file for %s", chat.get("id"))
            return None

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
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
        if "generatedFiles" not in chat:
            await asyncio.to_thread(_sync_chat_generated_files, chat_id)
            chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
            if not chat:
                return localized_error_response(
                    "Chat not found.", "未找到对话。", 404, "chat_not_found"
                )

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

    @router.get("/api/workbench/chats/{chat_id}/plan")
    async def api_workbench_get_chat_plan(chat_id: str):
        chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
        if not chat:
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
        plan = await latest_file_plan(chat_id, chat)
        if plan is None and isinstance(chat.get("activePlan"), dict):
            plan = copy.deepcopy(chat["activePlan"])
        return {"plan": plan}

    return api_workbench_get_chat


async def _apply_agent_binding(chat: dict[str, Any], body: dict[str, Any], default_model: str):
    if chat.get("messages"):
        return localized_error_response(
            "The Agent binding can only be changed in an empty chat.",
            "只能在空对话中更改 Agent 绑定。",
            409,
            "agent_binding_locked",
        )
    requested = body.get("agent") if isinstance(body.get("agent"), dict) else {}
    installation_id = str(requested.get("installationId") or "").strip()
    from cyrene.agents.builtin import BUILTIN_INSTALLATION_ID, normalize_agent_fields

    if not installation_id or installation_id == BUILTIN_INSTALLATION_ID:
        fields = normalize_agent_fields(
            {"installationId": BUILTIN_INSTALLATION_ID},
            body.get("modelAccess") if isinstance(body.get("modelAccess"), dict) else None,
            default_model=default_model,
        )
    else:
        extensions = _extensions_service()
        resolver = getattr(extensions, "get_agent_installation", None)
        installation = (
            await asyncio.to_thread(resolver, installation_id)
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
        chat["contextActivations"] = _composer_context_service().normalize(
            chat.get("contextActivations")
        )
    else:
        chat.pop("contextActivations", None)
    chat.pop("agentConfigOptions", None)
    chat.pop("agentConfigValues", None)
    chat.pop("modelSelectionId", None)
    return None


def _apply_agent_config_values(chat: dict[str, Any], values: Any):
    from cyrene.agents.builtin import normalize_agent_binding

    if normalize_agent_binding(chat.get("agent")).is_builtin:
        return localized_error_response(
            "Built-in chats do not use Agent configuration options.",
            "内置 Agent 对话不使用 Agent 配置选项。",
            400,
            "agent_config_not_supported",
        )
    if not isinstance(values, dict):
        return localized_error_response(
            "agentConfigValues must be an object.",
            "agentConfigValues 必须是对象。",
            400,
            "invalid_agent_config_values",
        )
    allowed = {str(option.get("id") or ""): option for option in chat.get("agentConfigOptions") or [] if isinstance(option, dict) and option.get("id")}
    normalized_values: dict[str, Any] = {}
    for config_id, value in values.items():
        config_id = str(config_id or "")[:200]
        option = allowed.get(config_id)
        if option is None:
            return localized_error_response(
                "Agent configuration option not found.",
                "未找到 Agent 配置选项。",
                400,
                "agent_config_option_not_found",
            )
        if option.get("type") == "boolean":
            normalized_values[config_id] = bool(value)
        else:
            valid_values = {str(item.get("value") or "") for item in option.get("options") or [] if isinstance(item, dict)}
            value = str(value or "")[:500]
            if value not in valid_values:
                return localized_error_response(
                    "The Agent configuration value is invalid.",
                    "Agent 配置值无效。",
                    400,
                    "invalid_agent_config_value",
                )
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
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("model_configuration")
    candidates = service.selectable_model_candidates() if service is not None else []
    selected = next(
        (item for item in candidates if selected_key in {str(item.get("id") or ""), str(item.get("model") or ""), str(item.get("name") or "")}),
        None,
    )
    chat["modelSelectionId"] = selected_key
    chat["model"] = str((selected or {}).get("model") or (selected or {}).get("name") or selected_key)
    chat.pop("lastModel", None)


def _apply_workspace_preferences(
    chat: dict[str, Any],
    body: dict[str, Any],
    *,
    chat_id: str,
    service: Any,
) -> Any | None:
    if "workspaceOverride" in body:
        try:
            override = service.normalize_workspace_override(body.get("workspaceOverride"))
        except ValueError:
            logger.warning(
                "Invalid workspace override for chat %s",
                chat_id,
                exc_info=True,
            )
            return localized_error_response(
                "The workspace override is invalid.",
                "工作区覆盖路径无效。",
                400,
                "invalid_workspace_override",
            )
        if override:
            chat["workspaceOverride"] = override
        else:
            chat.pop("workspaceOverride", None)
    if "workspaceSurface" not in body:
        return None
    try:
        workspace_surface = service.normalize_workspace_surface(
            body.get("workspaceSurface"),
            chat_id=chat_id,
            project_id=str(chat.get("projectId") or ""),
        )
    except ValueError:
        return localized_error_response(
            "The workspace surface is invalid.",
            "工作区分屏状态无效。",
            400,
            "invalid_workspace_surface",
        )
    if workspace_surface is None:
        chat.pop("workspaceSurface", None)
    else:
        chat["workspaceSurface"] = workspace_surface
    return None


def _register_update_route(router: APIRouter, context: ChatRouteContext):
    service = context.service
    _routes = context.runtime
    _public_chat_full = service.public_chat_full
    _get_workbench_chat = service.repository.get
    _utc_now_iso = service.utc_now_iso
    _write_chat_store = service.repository.write_one

    @router.patch("/api/workbench/chats/{chat_id}")
    async def api_workbench_update_chat(chat_id: str, body_model: api_models.ChatUpdateBody):
        body = api_models.body_dict(body_model)
        input_context_changed = any(
            key in body
            for key in (
                "soulActive",
                "workspaceActive",
                "workspaceOverride",
                "remoteDeviceIds",
                "contextActivations",
            )
        )
        chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
        if not chat:
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
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
        workspace_error = _apply_workspace_preferences(
            chat,
            body,
            chat_id=chat_id,
            service=service,
        )
        if workspace_error is not None:
            return workspace_error
        if "contextActivations" in body:
            chat["contextActivations"] = _composer_context_service().normalize(
                body.get("contextActivations")
            )
        if "remoteDeviceIds" in body:
            chat["remoteDeviceIds"] = list(body.get("remoteDeviceIds") or ())
        from cyrene.agents.builtin import normalize_agent_binding

        if (
            not normalize_agent_binding(chat.get("agent")).is_builtin
            and any((chat.get("contextActivations") or {}).values())
        ):
            return localized_error_response(
                "Composer context capabilities require the built-in Cyrene Agent.",
                "编辑器上下文能力需要使用 Cyrene 内置 Agent。",
                400,
                "builtin_agent_required",
            )
        if input_context_changed:
            project = await asyncio.to_thread(
                R.find_project_lightweight,
                str(chat.get("projectId") or ""),
            )
            if not project:
                return localized_error_response(
                    "Project not found.", "未找到项目。", 404, "project_not_found"
                )
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
                logger.warning(
                    "Composer input context update failed for chat %s: %s",
                    chat_id,
                    exc,
                )
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
            chat["workspaceActive"] = bool(
                resolved_input["workspaceActive"]
            )
            chat["remoteDeviceIds"] = list(
                resolved_input["remoteDeviceIds"]
            )
            chat["contextActivations"] = dict(
                resolved_input["contextActivations"]
            )
        if "activePlan" in body:
            project = await asyncio.to_thread(
                R.find_project_lightweight,
                str(chat.get("projectId") or ""),
            )
            if not project:
                return localized_error_response(
                    "Project not found.", "未找到项目。", 404, "project_not_found"
                )
            workspace_dir = service.resolve_chat_workspace_dir(
                chat,
                project,
                R.resolve_workspace_dir,
            )
            file_plan = await asyncio.to_thread(
                read_plan_file,
                workspace_dir,
                chat_id,
                expected_plan_id=str((chat.get("activePlan") or {}).get("planId") or ""),
            )
            try:
                active_plan = _normalize_active_plan(
                    body.get("activePlan"),
                    file_plan or chat.get("activePlan"),
                )
            except ValueError as exc:
                code = str(exc)
                conflict = code in {"plan_changed", "plan_started"}
                messages = {
                    "plan_changed": (
                        "The active plan changed. Reload it before editing.",
                        "当前计划已变化，请重新加载后再编辑。",
                    ),
                    "plan_started": (
                        "The plan can no longer be edited after execution starts.",
                        "计划开始执行后不能再编辑结构。",
                    ),
                    "plan_too_large": (
                        "The plan is too large.",
                        "计划内容过大。",
                    ),
                    "invalid_plan_order": (
                        "A step cannot appear before one of its prerequisites.",
                        "步骤不能排在它的前置步骤之前。",
                    ),
                }
                english, chinese = messages.get(
                    code,
                    ("The plan is invalid.", "计划数据无效。"),
                )
                return localized_error_response(
                    english,
                    chinese,
                    409 if conflict else 400,
                    code,
                )
            try:
                active_plan = await asyncio.to_thread(
                    write_plan_file,
                    workspace_dir,
                    chat_id,
                    active_plan,
                )
            except (OSError, ValueError):
                logger.warning("Conversation plan file write failed for %s", chat_id, exc_info=True)
                return localized_error_response(
                    "The conversation plan file could not be saved.",
                    "无法保存对话计划文件。",
                    500,
                    "plan_file_unavailable",
                )
            persisted = await asyncio.to_thread(
                context.conversation_context.agent_states.write_plugin_session_state,
                chat_id,
                "cyrene_control",
                {
                    "schema_version": 1,
                    "plan": active_plan,
                    "public_snapshot": {"activePlan": active_plan},
                },
            )
            if not persisted:
                return localized_error_response(
                    "The conversation plan context is unavailable.",
                    "当前对话的计划上下文不可用。",
                    409,
                    "plan_context_unavailable",
                )
            chat["activePlan"] = active_plan
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
