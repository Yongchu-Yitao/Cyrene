from __future__ import annotations

import asyncio
import copy
import logging

from fastapi import APIRouter, Request

from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.http.errors import localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


def _register_trace_route(router: APIRouter, context: ChatRouteContext) -> None:
    service = context.service
    _get_workbench_chat = service.repository.get
    _sanitize_durable_traces = service.sanitize_durable_traces
    _utc_now_iso = service.utc_now_iso
    _write_chat_store = service.repository.write_one

    @router.patch("/api/workbench/chats/{chat_id}/trace")
    async def api_workbench_patch_chat_trace(request: Request, chat_id: str):
        """Persist the client-assembled live trace onto the saved activity cards.

        The runtime trace is built from SSE tool events; the backend's own
        transcript extraction can lose mid-run calls (compaction/retry) and
        drops runtime status fields, so the completed conversation would not
        match what ran live. The client uploads its authoritative trace per
        saved activity-card message id; this endpoint stores it sanitized.
        """
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
        message_ids = body.get("messageIds")
        traces = body.get("traces")
        if not isinstance(message_ids, list) or not isinstance(traces, list):
            return localized_error_response(
                "messageIds and traces must both be arrays.",
                "messageIds 和 traces 都必须是数组。",
                400,
                "trace_arrays_required",
            )
        if not message_ids or len(message_ids) != len(traces) or len(message_ids) > 100:
            return localized_error_response(
                "messageIds and traces must be non-empty arrays of equal length, with at most 100 items.",
                "messageIds 和 traces 必须是长度相同的非空数组，且最多包含 100 项。",
                400,
                "invalid_trace_batch",
            )
        sanitized = await asyncio.to_thread(_sanitize_durable_traces, traces)
        chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
        if not chat:
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
        base_chat = copy.deepcopy(chat)
        by_id = {str(message.get("id") or ""): message for message in chat.get("messages") or [] if isinstance(message, dict) and str(message.get("id") or "")}
        updated = 0
        for message_id, trace in zip(message_ids, sanitized):
            target = by_id.get(str(message_id or ""))
            if not isinstance(target, dict) or not target.get("activityCard"):
                continue
            target["trace"] = trace
            updated += 1
        if updated:
            chat["updatedAt"] = _utc_now_iso()
            await asyncio.to_thread(_write_chat_store, chat, base_chat=base_chat)
            await publish_chat_changed(
                chat_id,
                str(chat.get("projectId") or ""),
                "trace_updated",
            )
        return {"ok": True, "updated": updated}


def _register_agent_options_route(router: APIRouter, context: ChatRouteContext) -> None:
    service = context.service
    _routes = context.runtime
    _get_workbench_chat = service.repository.get
    _resolve_chat_workspace_dir = service.resolve_chat_workspace_dir
    _utc_now_iso = service.utc_now_iso
    _write_chat_store = service.repository.write_one

    @router.get("/api/workbench/chats/{chat_id}/agent-config-options")
    async def api_workbench_agent_config_options(chat_id: str):
        R = _routes()
        chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
        if not chat:
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
        base_chat = copy.deepcopy(chat)
        from cyrene.agents.builtin import normalize_agent_binding

        if normalize_agent_binding(chat.get("agent")).is_builtin:
            return {"configOptions": [], "values": {}}
        project_store = await asyncio.to_thread(R.read_store)
        project = R.find_project(project_store, str(chat.get("projectId") or ""))
        if not project:
            return localized_error_response(
                "Project not found.", "未找到项目。", 404, "project_not_found"
            )
        try:
            workspace_dir = _resolve_chat_workspace_dir(chat, project, R.resolve_workspace_dir)
            from cyrene.agents import discover_external_agent_config_options

            options = await discover_external_agent_config_options(chat=chat, workspace_path=workspace_dir)
        except Exception as exc:
            kind = str(getattr(exc, "kind", "") or "agent_config_unavailable")
            logger.warning(
                "Agent configuration discovery failed for chat %s",
                chat_id,
                exc_info=True,
            )
            return localized_error_response(
                "Agent configuration is unavailable.",
                "Agent 配置不可用。",
                409,
                kind,
                failureKind=kind,
            )
        chat["agentConfigOptions"] = options
        values = dict(chat.get("agentConfigValues") or {})
        for option in options:
            option_id = str(option.get("id") or "")
            current_value = option.get("currentValue")
            if option.get("type") == "select":
                valid_values = {str(item.get("value") or "") for item in option.get("options") or [] if isinstance(item, dict)}
                if str(values.get(option_id, "")) not in valid_values:
                    values[option_id] = current_value
            else:
                values.setdefault(option_id, current_value)
        chat["agentConfigValues"] = values
        chat["updatedAt"] = _utc_now_iso()
        await asyncio.to_thread(_write_chat_store, chat, base_chat=base_chat)
        await publish_chat_changed(
            chat_id,
            str(chat.get("projectId") or ""),
            "agent_config_updated",
        )
        return {"configOptions": options, "values": values}


def register_agent_config_routes(router: APIRouter, context: ChatRouteContext) -> None:
    _register_trace_route(router, context)
    _register_agent_options_route(router, context)
