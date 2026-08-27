from __future__ import annotations

import asyncio
import copy

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.workbench.chat_events import publish_chat_changed
from route.workbench.chat_routes.context import ChatRouteContext


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
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "object body required"}, status_code=400)
        message_ids = body.get("messageIds")
        traces = body.get("traces")
        if not isinstance(message_ids, list) or not isinstance(traces, list):
            return JSONResponse({"error": "messageIds and traces arrays required"}, status_code=400)
        if not message_ids or len(message_ids) != len(traces) or len(message_ids) > 100:
            return JSONResponse(
                {"error": "messageIds and traces must be non-empty, equal-length arrays (≤100)"},
                status_code=400,
            )
        sanitized = await asyncio.to_thread(_sanitize_durable_traces, traces)
        chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
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
            return JSONResponse({"error": "chat not found"}, status_code=404)
        base_chat = copy.deepcopy(chat)
        from cyrene.agent_runtime.builtin import normalize_agent_binding

        if normalize_agent_binding(chat.get("agent")).is_builtin:
            return {"configOptions": [], "values": {}}
        project_store = await asyncio.to_thread(R.read_store)
        project = R.find_project(project_store, str(chat.get("projectId") or ""))
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        try:
            workspace_dir = _resolve_chat_workspace_dir(chat, project, R.resolve_workspace_dir)
            from cyrene.agent_runtime import discover_external_agent_config_options

            options = await discover_external_agent_config_options(chat=chat, workspace_path=workspace_dir)
        except Exception as exc:
            kind = str(getattr(exc, "kind", "") or "agent_config_unavailable")
            return JSONResponse({"error": str(exc), "code": kind}, status_code=409)
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
