from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.runtime.io import read_json_safe
from route.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


def register_context_routes(router: APIRouter, context: ChatRouteContext) -> None:
    service = context.service
    runtime = context.workbench_runtime
    _routes = context.runtime
    _project_data_key = context.project_data_key
    _resolve_library_file_payload = context.resolve_library_file_payload
    _public_pinned_resource = context.public_pinned_resource
    _CHAT_RUN_MANAGER = service.run_manager
    _agent_runtime_builtin = service.agent_runtime_builtin
    _chat_context_payload = service.chat_context_payload
    _context_segment_tokens = service.context_segment_tokens
    _find_chat = service.repository.find
    _read_chats_store = service.repository.read
    _utc_now_iso = service.utc_now_iso
    _workbench_subagent_payload = service.subagent_payload

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
        from cyrene.runtime.config_store import effective_ctx_limit_for_model

        if chat_id.startswith("legacy:"):
            _prefix, _project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not session_id:
                return JSONResponse({"error": "chat not found"}, status_code=404)
            model_name = str(getattr(config, "OPENAI_MODEL", "") or "")
            return await asyncio.to_thread(
                _chat_context_payload,
                session_id,
                model_name,
                ctx_limit=effective_ctx_limit_for_model(model_name),
            )
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        model_name = str(chat.get("model") or getattr(config, "OPENAI_MODEL", "") or "")
        # ``modelSelectionId`` is stable even when two connections expose the
        # same remote model name with different context windows.
        model_selection = str(chat.get("modelSelectionId") or model_name).strip()
        return await asyncio.to_thread(
            _chat_context_payload,
            chat_id,
            model_name,
            # The selected conversation model owns the context budget. Using
            # the process-global primary here made the overview stay stale
            # after a per-chat model switch.
            ctx_limit=effective_ctx_limit_for_model(model_selection),
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
        model_name = str(chat.get("model") or getattr(config, "OPENAI_MODEL", "") or "")
        result = await compact_session_if_needed(
            chat_id,
            # Explicit compaction must always have a usable budget even when an
            # OpenAI-compatible custom model has no family heuristic/configured
            # context size. 128K is the conservative default used by the core
            # chat models and is safer than passing 0 (which disables budgeting).
            ctx_limit=(effective_ctx_limit_for_model(model_name) or 128_000),
            force=True,
        )
        return {"ok": True, **result}

    @router.get("/api/workbench/chats/{chat_id}/context-blocks")
    async def api_workbench_chat_context_blocks(chat_id: str):
        """Context block composition using the same token math as the Overview gauge."""
        if chat_id.startswith("legacy:"):
            _, _project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not session_id:
                return JSONResponse({"error": "chat not found"}, status_code=404)
            state_id = session_id
        else:
            state_id = chat_id

        data = read_json_safe(runtime.session_state_file(state_id))
        data = data if isinstance(data, dict) else {}
        messages = data.get("messages")
        if not isinstance(messages, list):
            messages = []

        # ACP Agents own their private context and usually do not write
        # Cyrene's session state file.  The public transcript is still known,
        # so use it as an honest fallback instead of claiming that a non-empty
        # conversation has no context.  This remains explicitly marked as an
        # estimate; system prompts and any Agent-private memory are not guessed.
        composition_source = "agent_state"
        agent_context_detail_available = True
        agent_report: dict[str, Any] = {}
        if not chat_id.startswith("legacy:"):
            chats_payload = await asyncio.to_thread(_read_chats_store)
            chat = _find_chat(chats_payload, chat_id)
            if isinstance(chat, dict):
                agent_fields = _agent_runtime_builtin.chat_agent_fields(chat)
                agent = agent_fields.get("agent") if isinstance(agent_fields, dict) else {}
                installation_id = str((agent or {}).get("installationId") or "")
                if installation_id and installation_id != _agent_runtime_builtin.BUILTIN_INSTALLATION_ID:
                    stored_report = chat.get("agentContextReport")
                    agent_report = stored_report if isinstance(stored_report, dict) else {}
                    if agent_report:
                        composition_source = "agent_report"
                        agent_context_detail_available = bool(agent_report.get("segments"))
                    elif not messages:
                        transcript = chat.get("messages")
                        messages = transcript if isinstance(transcript, list) else []
                        composition_source = "public_transcript"
                        agent_context_detail_available = False
        seg = _context_segment_tokens(messages)
        msg_total = sum(seg.values())

        layers: list[dict[str, Any]] = []

        if composition_source == "agent_report":
            reported_segments = agent_report.get("segments") if isinstance(agent_report.get("segments"), list) else []
            segment_total = 0
            for index, item in enumerate(reported_segments[:32]):
                if not isinstance(item, dict):
                    continue
                tokens = max(0, int(item.get("tokens") or 0))
                if tokens <= 0:
                    continue
                segment_total += tokens
                layers.append(
                    {
                        "id": "agent_segment_" + str(index + 1),
                        "label": str(item.get("label") or item.get("key") or f"Segment {index + 1}"),
                        "sublabel": None,
                        "blocks": [],
                        "totalTokens": tokens,
                    }
                )
            reported_used = max(0, int(agent_report.get("used") or 0))
            if reported_used > segment_total:
                layers.append(
                    {
                        "id": "agent_other",
                        "label": "Other Agent context",
                        "sublabel": None,
                        "blocks": [],
                        "totalTokens": reported_used - segment_total,
                    }
                )
            if not layers and reported_used > 0:
                layers.append(
                    {
                        "id": "agent_reported",
                        "label": "Agent context",
                        "sublabel": None,
                        "blocks": [],
                        "totalTokens": reported_used,
                    }
                )

        # Layer 1: System Prefix — from separately-saved blocks (not in state.json)
        sys_blocks = data.get("system_context_blocks")
        if isinstance(sys_blocks, list) and sys_blocks:
            sys_tokens = sum(int(b.get("tokens_est", 0) or 0) for b in sys_blocks if isinstance(b, dict))
            layers.append(
                {
                    "id": "system_prefix",
                    "label": "System Prefix",
                    "sublabel": None,
                    "blocks": [dict(b) for b in sys_blocks if isinstance(b, dict)],
                    "totalTokens": sys_tokens,
                }
            )

        # Layer 2: Ephemeral — from saved text (not in state.json)
        ephemeral = data.get("ephemeral_context")
        if isinstance(ephemeral, str) and ephemeral.strip():
            tokens = runtime.approx_token_count(ephemeral)
            layers.append(
                {
                    "id": "ephemeral",
                    "label": "Ephemeral Tail",
                    "sublabel": None,
                    "blocks": [{"id": "ephemeral.run", "type": "ephemeral", "tokens_est": tokens, "chars": len(ephemeral)}],
                    "totalTokens": tokens,
                }
            )

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
            layers.append(
                {
                    "id": "messages",
                    "label": "Conversation Messages",
                    "sublabel": None,
                    "blocks": msg_blocks,
                    "totalTokens": msg_total,
                }
            )

        total = sum(layer["totalTokens"] for layer in layers)
        return {
            "layers": layers,
            "totalTokensEst": total,
            "messageTokens": msg_total,
            "compositionSource": composition_source,
            "agentContextDetailAvailable": agent_context_detail_available,
            "contextUsed": int(agent_report.get("used") or 0) if agent_report else 0,
            "contextLimit": int(agent_report.get("size") or 0) if agent_report else 0,
        }

    @router.get("/api/workbench/chats/{chat_id}/inbox")
    async def api_workbench_chat_inbox(chat_id: str):
        """Return only the current live inbox for this conversation."""
        started = time.monotonic()
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "legacy chat has no Workbench inbox"}, status_code=404)
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
        live = (
            run.inbox.live_snapshot()
            if run is not None
            else {
                "queueDepth": 0,
                "pendingGuidance": 0,
                "activeTasks": 0,
                "persistenceTasks": 0,
                "closed": True,
                "events": [],
                "tools": [],
            }
        )
        events = list(live.get("events") or [])
        tools = [dict(item) for item in list(live.get("tools") or []) if str(item.get("state") or "") in {"queued", "running", "ready"}]
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
            "active": bool(run is not None and run.status in {"running", "finishing"}),
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
