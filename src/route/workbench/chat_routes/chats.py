from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from typing import Any

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from cyrene.runtime.io import atomic_write_json
from cyrene.workbench import chat_groups, pinned_resources
from cyrene.workbench.notifications import append_notification
from cyrene.workbench.workspace_changes import (
    delete_chat_change_sets,
)
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


def register_chat_routes(
    router: APIRouter,
    context: ChatRouteContext,
    *,
    send_chat_detached,
) -> dict[str, Any]:
    service = context.service
    runtime = context.workbench_runtime
    _routes = context.runtime
    _project_data_key = context.project_data_key
    _resolve_library_file_payload = context.resolve_library_file_payload
    _public_pinned_resource = context.public_pinned_resource
    _CHAT_RUN_MANAGER = service.run_manager
    _chat_preview = service.chat_preview
    _chat_soul_active = service.chat_soul_active
    _chat_workspace_active = service.chat_workspace_active
    _clear_fork_metadata = service.clear_fork_metadata
    _coerce_brief_acceptance = service.coerce_brief_acceptance
    _coerce_brief_constraints = service.coerce_brief_constraints
    _completed_turn_count = service.completed_turn_count
    _find_chat = service.repository.find
    _legacy_chats = service.legacy_chats
    _new_chat = service.create_chat
    _normalize_workspace_override = service.normalize_workspace_override
    _prune_orphaned_fork_metadata = service.prune_orphaned_fork_metadata
    _public_chat_full = service.public_chat_full
    _public_chat_light = service.public_chat_light
    _read_chats_store = service.repository.read
    _resolve_chat_workspace_dir = service.resolve_chat_workspace_dir
    _sanitize_durable_traces = service.sanitize_durable_traces
    _short_id = service.short_id
    _summarize_chat_to_brief = service.summarize_chat_to_brief
    _sync_chat_generated_files = service.sync_chat_generated_files
    _truncate_state_file_at_user_ordinal = service.truncate_state_file_at_user_ordinal
    _utc_now_iso = service.utc_now_iso
    _write_chats_store = service.repository.write
    terminate_chat_agents = service.terminate_chat_agents

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
                        candidate = (root / Path(parsed[len(prefix) :]).name).resolve()
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
                "dataKey": R.project_data_key(default_project),
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
            payload = _read_chats_store()
            chat = _new_chat(
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
            _write_chats_store(payload)
            return chat

        chat = await asyncio.to_thread(create_and_persist)
        from cyrene.observability import debug

        await debug.publish_event(
            {
                "type": "workbench_chat_changed",
                "change": "created",
                "session_id": str(chat.get("id") or ""),
                "chat_id": str(chat.get("id") or ""),
                "project_id": project_id,
            },
            session_id=str(chat.get("id") or ""),
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 250:
            logger.warning(
                "Slow Workbench chat creation [project=%s duration_ms=%.1f]",
                project_id,
                elapsed_ms,
            )
        return {"ok": True, "chat": _public_chat_full(chat)}

    @router.post("/api/workbench/voice-command")
    async def api_workbench_voice_command(
        audio: UploadFile,
        lang: str = Form(""),
        ui_instance_id: str = Form(""),
    ):
        """Transcribe first, then silently create and dispatch a default-project chat.

        Keeping ASR and chat creation in one backend operation guarantees that
        empty/silence-only captures never leave an orphan conversation behind.
        """
        from cyrene.voice import engine as voice_engine

        try:
            voice_status = await asyncio.to_thread(voice_engine.status)
            if not voice_status.get("asr_ready") or not voice_status.get("tts_ready"):
                return JSONResponse(
                    {"error": "voice models are not ready", "created": False},
                    status_code=409,
                )
            audio_payload = await audio.read(voice_engine.MAX_AUDIO_BYTES + 1)
            if len(audio_payload) > voice_engine.MAX_AUDIO_BYTES:
                raise ValueError("audio file is too large")
            transcript = await asyncio.to_thread(voice_engine.transcribe, audio_payload)
        except (ValueError, RuntimeError, OSError) as exc:
            return JSONResponse(
                {"error": str(exc), "created": False},
                status_code=409 if isinstance(exc, RuntimeError) else 400,
            )

        text = str((transcript or {}).get("text") or "").strip()
        if not text or bool((transcript or {}).get("silence_only")):
            return {"ok": True, "created": False, "text": ""}

        R = _routes()
        store = await asyncio.to_thread(R.read_store)
        projects = store.get("projects", []) or []
        default_project = next(
            (project for project in projects if R.project_data_key(project) == "default"),
            None,
        )
        if default_project is None and projects:
            default_project = projects[0]
        project_id = str((default_project or {}).get("id") or "")
        if not project_id:
            return JSONResponse(
                {"error": "default project not found", "created": False},
                status_code=404,
            )

        from cyrene.workbench.project_memory_prompt import current_snapshot

        memory_snapshot = await asyncio.to_thread(current_snapshot, project_id)

        def create_and_persist() -> dict[str, Any]:
            payload = _read_chats_store()
            chat = _new_chat(
                project_id,
                "",
                R.get_model(),
                project_memory_snapshot=memory_snapshot,
            )
            chat["permissionMode"] = "auto"
            payload.setdefault("chats", []).insert(0, chat)
            _write_chats_store(payload)
            return chat

        chat = await asyncio.to_thread(create_and_persist)
        chat_id = str(chat.get("id") or "")
        from cyrene.observability import debug

        await debug.publish_event(
            {
                "type": "workbench_chat_changed",
                "change": "created",
                "session_id": chat_id,
                "chat_id": chat_id,
                "project_id": project_id,
            },
            session_id=chat_id,
        )

        dispatch = await send_chat_detached(
            chat_id,
            {
                "message": text,
                "mode": "auto",
                "lang": lang if lang in {"en", "zh"} else "",
                "stream": True,
                "uiInstanceId": str(ui_instance_id or ""),
                "voiceCommand": True,
            },
            detached=True,
        )
        if isinstance(dispatch, JSONResponse):
            try:
                dispatch_payload = json.loads(bytes(dispatch.body).decode("utf-8"))
            except Exception:
                dispatch_payload = {"error": "voice command dispatch failed"}
            if not 200 <= dispatch.status_code < 300:
                return JSONResponse(
                    {
                        **dispatch_payload,
                        "created": True,
                        "chat_id": chat_id,
                        "text": text,
                    },
                    status_code=dispatch.status_code,
                )
            return {
                "ok": True,
                "created": True,
                "text": text,
                **dispatch_payload,
            }
        return {"ok": True, "created": True, "text": text, "chat_id": chat_id}

    @router.get("/api/workbench/chats/{chat_id}/side-agents")
    async def api_workbench_list_side_agents(chat_id: str):
        if chat_id.startswith("legacy:"):
            return {"agents": []}
        payload = await asyncio.to_thread(_read_chats_store)
        parent = _find_chat(payload, chat_id)
        if not parent:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        agents = [_public_chat_full(item) for item in payload.get("chats", []) if str(item.get("kind") or "") == "side-agent" and str(item.get("parentChatId") or "") == chat_id]
        agents.sort(key=lambda item: str(item.get("createdAt") or ""))
        return {"agents": agents}

    @router.post("/api/workbench/chats/{chat_id}/side-agents")
    async def api_workbench_create_side_agent(chat_id: str, body_model: api_models.SideAgentCreateBody):
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
                project_memory_snapshot=(dict(parent.get("projectMemorySnapshot") or {}) if isinstance(parent.get("projectMemorySnapshot"), dict) else None),
            )
            agent["kind"] = "side-agent"
            agent["parentChatId"] = chat_id
            agent["sourceQuote"] = quote[:12_000]
            if parent.get("workspaceOverride"):
                agent["workspaceOverride"] = str(parent["workspaceOverride"])
            agent["soulActive"] = _chat_soul_active(parent)
            agent["workspaceActive"] = _chat_workspace_active(parent)
            if parent.get("reasoningEffort"):
                agent["reasoningEffort"] = str(parent["reasoningEffort"])
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

    @router.patch("/api/workbench/chats/{chat_id}")
    async def api_workbench_update_chat(chat_id: str, body_model: api_models.ChatUpdateBody):
        body = api_models.body_dict(body_model)
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "legacy chat metadata is read-only"}, status_code=403)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        R = _routes()
        if "title" in body:
            chat["title"] = str(body.get("title") or "").strip()[:60] or chat.get("title")
            chat["titleLocked"] = True
        if "agent" in body:
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
                    default_model=R.get_model(),
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
            chat.pop("agentConfigOptions", None)
            chat.pop("agentConfigValues", None)
            chat.pop("modelSelectionId", None)
        if "agentConfigValues" in body:
            from cyrene.agent_runtime.builtin import normalize_agent_binding

            if normalize_agent_binding(chat.get("agent")).is_builtin:
                return JSONResponse({"error": "Built-in chats do not use Agent config options"}, status_code=400)
            values = body.get("agentConfigValues")
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
        if "model" in body:
            selected_key = str(body.get("model") or "").strip()
            if selected_key:
                from cyrene.runtime.model_configuration import selectable_model_candidates

                from cyrene.runtime.settings_store import get_models

                selected = next(
                    (
                        item
                        for item in selectable_model_candidates(legacy_candidates=get_models() or [])
                        if selected_key
                        in {
                            str(item.get("id") or ""),
                            str(item.get("model") or ""),
                            str(item.get("name") or ""),
                        }
                    ),
                    None,
                )
                chat["modelSelectionId"] = selected_key
                chat["model"] = str((selected or {}).get("model") or (selected or {}).get("name") or selected_key)
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
        chat["updatedAt"] = _utc_now_iso()
        await asyncio.to_thread(_write_chats_store, payload)
        from cyrene.observability import debug

        await debug.publish_event(
            {
                "type": "workbench_chat_changed",
                "change": "updated",
                "session_id": chat_id,
                "chat_id": chat_id,
                "project_id": str(chat.get("projectId") or ""),
            },
            session_id=chat_id,
        )
        return {"ok": True, "chat": _public_chat_full(chat)}

    @router.patch("/api/workbench/chats/{chat_id}/trace")
    async def api_workbench_patch_chat_trace(request: Request, chat_id: str):
        """Persist the client-assembled live trace onto the saved activity cards.

        The runtime trace is built from SSE tool events; the backend's own
        transcript extraction can lose mid-run calls (compaction/retry) and
        drops runtime status fields, so the completed conversation would not
        match what ran live. The client uploads its authoritative trace per
        saved activity-card message id; this endpoint stores it sanitized.
        """
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "legacy chat transcript is read-only"}, status_code=403)
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
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
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
            await asyncio.to_thread(_write_chats_store, payload)
        return {"ok": True, "updated": updated}

    @router.get("/api/workbench/chats/{chat_id}/agent-config-options")
    async def api_workbench_agent_config_options(chat_id: str):
        R = _routes()
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
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
        await asyncio.to_thread(_write_chats_store, payload)
        return {"configOptions": options, "values": values}

    @router.get("/api/workbench/chat-groups")
    async def api_workbench_chat_groups(project: str = ""):
        project_id = str(project or "").strip()
        if not project_id:
            return JSONResponse({"error": "project is required"}, status_code=400)
        if not await asyncio.to_thread(_routes().find_project_lightweight, project_id):
            return JSONResponse({"error": "project not found"}, status_code=404)
        return await asyncio.to_thread(chat_groups.get_project_groups, project_id)

    async def _replace_chat_groups(body: dict[str, Any]):
        project_id = str(body.get("projectId") or "").strip()
        if not await asyncio.to_thread(_routes().find_project_lightweight, project_id):
            return JSONResponse({"error": "project not found"}, status_code=404)
        try:
            return await chat_groups.replace_project_groups(
                project_id,
                body.get("groups") if isinstance(body.get("groups"), list) else [],
                base_groups=(body.get("baseGroups") if isinstance(body.get("baseGroups"), list) else None),
                mutation_intent=(body.get("intent") if isinstance(body.get("intent"), dict) else None),
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
            metadata = await service.generate_chat_group_metadata(
                (metadata_context["members"] if metadata_context else body.get("members") if isinstance(body.get("members"), list) else []),
                lang=str(body.get("lang") or ""),
                title_locked=(bool(metadata_context["group"].get("titleLocked")) if metadata_context else bool(body.get("titleLocked"))),
                current_title=(str(metadata_context["group"].get("title") or "") if metadata_context else str(body.get("currentTitle") or "")),
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
                (item for item in persisted.get("groups", []) if str(item.get("id") or "") == group_id),
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
        if chat_id.startswith("legacy:"):
            _prefix, project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not project_id or not session_id or _project_data_key(project_id) != "default":
                return JSONResponse({"error": "chat not found"}, status_code=404)
            payload, status_code = await _routes().delete_chat_session(session_id)
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
            *[str(chat.get("id") or "") for chat in chats if str(chat.get("kind") or "") == "side-agent" and str(chat.get("parentChatId") or "") == chat_id],
        }
        try:
            await terminate_chat_agents(removed_chat_ids)
        except Exception:
            logger.exception("Failed to terminate agents for deleted chat %s", chat_id)
            return JSONResponse(
                {"error": "chat agents could not be terminated"},
                status_code=503,
            )
        next_chats = [chat for chat in chats if str(chat.get("id") or "") not in removed_chat_ids]
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
            from cyrene.agent_runtime.model_gateway import revoke_model_gateway_scope

            revoke_model_gateway_scope(chat_id=removed_chat_id)
            try:
                await asyncio.to_thread(
                    delete_chat_change_sets,
                    service.db_path,
                    removed_chat_id,
                )
            except Exception:
                logger.exception(
                    "Failed to delete workspace change history for chat %s",
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

    @router.post("/api/workbench/chats/{chat_id}/fork")
    async def api_workbench_chat_fork(chat_id: str, body_model: api_models.ChatForkBody):
        """Fork a conversation at an edited user message.

        Creates a new chat with the prefix transcript (everything before the
        edited user message) plus a NEW user entry bearing the edited content
        and the original attachments. The source chat is preserved untouched.
        The agent's raw state is copied from the source session and truncated at
        the same user-message boundary so the forked chat can replay the edit
        through a normal send (``{ retry: true, forkReplay: true }``) without
        re-truncating. The agent is NOT run here.
        """
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
        from cyrene.agent_runtime.builtin import normalize_agent_binding

        if not normalize_agent_binding(chat.get("agent") if isinstance(chat.get("agent"), dict) else None).is_builtin:
            return JSONResponse(
                {
                    "error": "This Agent does not support conversation forks",
                    "code": "capability_missing",
                    "failureKind": "capability_missing",
                },
                status_code=409,
            )
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
        user_ordinal = sum(1 for entry in messages[: edit_index + 1] if str(entry.get("role") or "") == "user")

        R = _routes()
        project_id = str(chat.get("projectId") or "")
        now = _utc_now_iso()
        new_chat = _new_chat(
            project_id,
            str(chat.get("title") or ""),
            str(chat.get("model") or R.get_model()),
            project_memory_snapshot=(dict(chat.get("projectMemorySnapshot") or {}) if isinstance(chat.get("projectMemorySnapshot"), dict) else None),
        )
        new_chat["forkedFromChatId"] = chat_id
        new_chat["forkedAtMessageId"] = message_id
        if chat.get("workspaceOverride"):
            new_chat["workspaceOverride"] = str(chat["workspaceOverride"])
        new_chat["soulActive"] = _chat_soul_active(chat)
        new_chat["workspaceActive"] = _chat_workspace_active(chat)
        if chat.get("reasoningEffort"):
            new_chat["reasoningEffort"] = str(chat["reasoningEffort"])
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
        src_state = runtime.session_state_file(chat_id)
        new_state = runtime.session_state_file(new_chat_id)

        def seed_fork_state() -> None:
            new_state.parent.mkdir(parents=True, exist_ok=True)
            try:
                if src_state.exists():
                    shutil.copyfile(src_state, new_state)
                    truncated = _truncate_state_file_at_user_ordinal(new_state, user_ordinal)
                    if not truncated:
                        logger.warning(
                            "Fork state truncation missed user ordinal %d for %s (source %s) — state may have been compacted; replay will use the existing prefix.",
                            user_ordinal,
                            new_chat_id,
                            chat_id,
                        )
                else:
                    atomic_write_json(new_state, {"messages": []})
            except Exception:
                logger.exception("Failed to seed fork state for %s", new_chat_id)

        await asyncio.to_thread(seed_fork_state)

        return {"ok": True, "chat": _public_chat_full(new_chat)}

    @router.post("/api/workbench/chats/{chat_id}/to-task")
    async def api_workbench_chat_to_task(chat_id: str, body_model: api_models.ChatToTaskBody):
        """Promote a conversation into a task session of its project (开始执行)."""
        body = api_models.body_dict(body_model)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        R = _routes()
        store = await asyncio.to_thread(R.read_store)
        project = R.find_project(store, str(chat.get("projectId") or ""))
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
        title = (override_title or str(brief.get("title") or "").strip() or str(chat.get("title") or "").strip() or "新任务")[:80] or "新任务"
        goal = (override_goal or str(brief.get("goal") or "").strip() or last_user or title).strip()
        constraints = _coerce_brief_constraints(brief.get("constraints"))
        acceptance = _coerce_brief_acceptance(brief.get("acceptanceCriteria"))

        session = R.new_session(project.get("id"), title, goal)
        if constraints:
            session["constraints"] = constraints
        if acceptance:
            session["acceptanceCriteria"] = acceptance
        session["sourceChatId"] = chat_id
        session["events"] = [
            {
                "id": _short_id("event"),
                "type": "CreatedFromChat",
                "createdAt": _utc_now_iso(),
                "body": (f"由对话「{chat.get('title') or '新对话'}」综合整理而来（已通读完整对话）。" if from_synthesis else f"由对话「{chat.get('title') or '新对话'}」创建。"),
                "chatId": chat_id,
            }
        ]
        project.setdefault("sessions", []).insert(0, session)
        project["updatedAt"] = session["createdAt"]
        store["activeProjectId"] = project.get("id")
        store["activeSessionId"] = session["id"]
        await asyncio.to_thread(R.write_store, store)

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

    return {
        "list_chats": api_workbench_list_chats,
        "create_chat": api_workbench_create_chat,
        "update_chat": api_workbench_update_chat,
        "delete_chat": api_workbench_delete_chat,
        "get_chat": api_workbench_get_chat,
    }
