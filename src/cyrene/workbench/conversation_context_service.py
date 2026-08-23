"""Application queries for Workbench conversation context and live inboxes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from cyrene.runtime.io import read_json_safe


logger = logging.getLogger(__name__)


class ConversationNotFoundError(LookupError):
    pass


class SessionStateRepository:
    """Own reads of agent session-state documents."""

    def __init__(self, state_file: Callable[[str], Path]) -> None:
        self._state_file = state_file

    def read(self, session_id: str) -> dict[str, Any]:
        value = read_json_safe(self._state_file(session_id))
        return value if isinstance(value, dict) else {}

    def read_map(self, session_id: str) -> dict[str, Any]:
        """Read map state while preserving the legacy invalid-JSON failure."""
        path = self._state_file(session_id)
        if not path.exists():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}


def _empty_live_inbox() -> dict[str, Any]:
    return {
        "queueDepth": 0,
        "pendingGuidance": 0,
        "activeTasks": 0,
        "persistenceTasks": 0,
        "closed": True,
        "events": [],
        "tools": [],
    }


def _agent_report_layers(report: dict[str, Any]) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = []
    raw_segments = report.get("segments")
    segments = raw_segments if isinstance(raw_segments, list) else []
    segment_total = 0
    for index, item in enumerate(segments[:32]):
        if not isinstance(item, dict):
            continue
        tokens = max(0, int(item.get("tokens") or 0))
        if tokens <= 0:
            continue
        segment_total += tokens
        layers.append({
            "id": "agent_segment_" + str(index + 1),
            "label": str(item.get("label") or item.get("key") or f"Segment {index + 1}"),
            "sublabel": None,
            "blocks": [],
            "totalTokens": tokens,
        })
    reported_used = max(0, int(report.get("used") or 0))
    if reported_used > segment_total:
        layers.append({
            "id": "agent_other",
            "label": "Other Agent context",
            "sublabel": None,
            "blocks": [],
            "totalTokens": reported_used - segment_total,
        })
    if not layers and reported_used > 0:
        layers.append({
            "id": "agent_reported",
            "label": "Agent context",
            "sublabel": None,
            "blocks": [],
            "totalTokens": reported_used,
        })
    return layers


def _system_layer(data: dict[str, Any]) -> dict[str, Any] | None:
    raw_blocks = data.get("system_context_blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        return None
    blocks = [dict(item) for item in raw_blocks if isinstance(item, dict)]
    return {
        "id": "system_prefix",
        "label": "System Prefix",
        "sublabel": None,
        "blocks": blocks,
        "totalTokens": sum(int(item.get("tokens_est", 0) or 0) for item in blocks),
    }


def _message_layer(
    segments: dict[str, int],
    total: int,
) -> dict[str, Any] | None:
    blocks = []
    for key in ("compacted", "system", "user", "assistant", "tool"):
        tokens = int(segments.get(key, 0) or 0)
        if tokens > 0:
            blocks.append({
                "id": "segment." + key,
                "type": key,
                "tokens_est": tokens,
                "source": "",
                "reason": "",
            })
    if not blocks:
        return None
    return {
        "id": "messages",
        "label": "Conversation Messages",
        "sublabel": None,
        "blocks": blocks,
        "totalTokens": total,
    }


class ConversationContextQueryService:
    """Own durable context lookups, fallback selection, and token projection."""

    def __init__(
        self,
        *,
        states: SessionStateRepository,
        chats: Any,
        agent_runtime: Any,
        context_payload: Callable[..., dict[str, Any]],
        context_segments: Callable[[list[Any]], dict[str, int]],
        subagent_payload: Callable[..., dict[str, Any]],
        compact_session: Callable[..., Awaitable[dict[str, Any]]],
        default_model: Callable[[], str],
        context_limit: Callable[[str], int],
        approx_token_count: Callable[[str], int],
    ) -> None:
        self.states = states
        self.chats = chats
        self.agent_runtime = agent_runtime
        self.context_payload = context_payload
        self.context_segments = context_segments
        self.subagent_payload = subagent_payload
        self.compact_session = compact_session
        self.default_model = default_model
        self.context_limit = context_limit
        self.approx_token_count = approx_token_count

    async def _chat(self, chat_id: str) -> dict[str, Any]:
        payload = await asyncio.to_thread(self.chats.read)
        chat = self.chats.find(payload, chat_id)
        if not isinstance(chat, dict):
            raise ConversationNotFoundError("chat not found")
        return chat

    async def subagents(self, chat_id: str, round_id: str) -> dict[str, Any]:
        await self._chat(chat_id)
        return await asyncio.to_thread(self.subagent_payload, chat_id, round_id)

    async def summary(
        self,
        chat_id: str,
        *,
        legacy_session_id: str = "",
    ) -> dict[str, Any]:
        if legacy_session_id:
            model_name = self.default_model()
            return await asyncio.to_thread(
                self.context_payload,
                legacy_session_id,
                model_name,
                ctx_limit=self.context_limit(model_name),
            )
        chat = await self._chat(chat_id)
        configured = str(chat.get("model") or self.default_model() or "")
        model_name = str(chat.get("lastModel") or configured)
        selection = str(chat.get("modelSelectionId") or configured).strip()
        return await asyncio.to_thread(
            self.context_payload,
            chat_id,
            model_name,
            ctx_limit=self.context_limit(selection),
        )

    async def compact(self, chat_id: str) -> dict[str, Any]:
        chat = await self._chat(chat_id)
        model_name = str(chat.get("model") or self.default_model() or "")
        result = await self.compact_session(
            chat_id,
            ctx_limit=(self.context_limit(model_name) or 128_000),
            force=True,
        )
        return {"ok": True, **result}

    async def blocks(
        self,
        chat_id: str,
        state_id: str,
        *,
        legacy: bool,
    ) -> dict[str, Any]:
        data = await asyncio.to_thread(self.states.read, state_id)
        raw_messages = data.get("messages")
        messages = raw_messages if isinstance(raw_messages, list) else []
        source = "agent_state"
        detail_available = True
        report: dict[str, Any] = {}
        if not legacy:
            messages, source, detail_available, report = await self._context_source(
                chat_id,
                messages,
            )
        segments = self.context_segments(messages)
        message_total = sum(segments.values())
        layers = _agent_report_layers(report) if source == "agent_report" else []
        system_layer = _system_layer(data)
        if system_layer is not None:
            layers.append(system_layer)
        ephemeral = data.get("ephemeral_context")
        if isinstance(ephemeral, str) and ephemeral.strip():
            tokens = self.approx_token_count(ephemeral)
            layers.append({
                "id": "ephemeral",
                "label": "Ephemeral Tail",
                "sublabel": None,
                "blocks": [{
                    "id": "ephemeral.run",
                    "type": "ephemeral",
                    "tokens_est": tokens,
                    "chars": len(ephemeral),
                }],
                "totalTokens": tokens,
            })
        message_layer = _message_layer(segments, message_total)
        if message_layer is not None:
            layers.append(message_layer)
        return self._blocks_payload(
            layers,
            message_total,
            source,
            detail_available,
            report,
        )

    async def _context_source(
        self,
        chat_id: str,
        messages: list[Any],
    ) -> tuple[list[Any], str, bool, dict[str, Any]]:
        payload = await asyncio.to_thread(self.chats.read)
        chat = self.chats.find(payload, chat_id)
        if not isinstance(chat, dict):
            return messages, "agent_state", True, {}
        fields = self.agent_runtime.chat_agent_fields(chat)
        agent = fields.get("agent") if isinstance(fields, dict) else {}
        installation_id = str((agent or {}).get("installationId") or "")
        if not installation_id or installation_id == self.agent_runtime.BUILTIN_INSTALLATION_ID:
            return messages, "agent_state", True, {}
        stored_report = chat.get("agentContextReport")
        report = stored_report if isinstance(stored_report, dict) else {}
        if report:
            return messages, "agent_report", bool(report.get("segments")), report
        if messages:
            return messages, "agent_state", True, {}
        transcript = chat.get("messages")
        public_messages = transcript if isinstance(transcript, list) else []
        return public_messages, "public_transcript", False, {}

    @staticmethod
    def _blocks_payload(
        layers: list[dict[str, Any]],
        message_total: int,
        source: str,
        detail_available: bool,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "layers": layers,
            "totalTokensEst": sum(layer["totalTokens"] for layer in layers),
            "messageTokens": message_total,
            "compositionSource": source,
            "agentContextDetailAvailable": detail_available,
            "contextUsed": int(report.get("used") or 0) if report else 0,
            "contextLimit": int(report.get("size") or 0) if report else 0,
        }


class ConversationInboxQueryService:
    """Own hot run-registry lookup and durable fallback for inbox snapshots."""

    def __init__(
        self,
        *,
        chats: Any,
        run_manager: Any,
        utc_now: Callable[[], str],
    ) -> None:
        self.chats = chats
        self.run_manager = run_manager
        self.utc_now = utc_now

    async def snapshot(self, chat_id: str) -> dict[str, Any]:
        started = time.monotonic()
        run = self.run_manager.get(chat_id)
        if run is None:
            payload = await asyncio.to_thread(self.chats.read)
            if not self.chats.find(payload, chat_id):
                raise ConversationNotFoundError("chat not found")
            run = self.run_manager.get(chat_id)
        live = run.inbox.live_snapshot() if run is not None else _empty_live_inbox()
        events = list(live.get("events") or [])
        tools = [
            dict(item)
            for item in list(live.get("tools") or [])
            if str(item.get("state") or "") in {"queued", "running", "ready"}
        ]
        snapshot = self._snapshot_payload(chat_id, run, live, events, tools)
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 1000:
            logger.warning(
                "Slow Workbench inbox snapshot [chat_id=%s active=%s duration_ms=%.1f]",
                chat_id,
                run is not None,
                elapsed_ms,
            )
        return snapshot

    def _snapshot_payload(
        self,
        chat_id: str,
        run: Any,
        live: dict[str, Any],
        events: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        timestamps = [str(item.get("createdAt") or "") for item in events]
        timestamps.extend(str(item.get("updatedAt") or "") for item in tools)
        return {
            "sessionId": chat_id,
            "runId": str(run.run_id if run is not None else ""),
            "active": bool(run is not None and run.status in {"running", "finishing"}),
            "runStatus": str(run.status if run is not None else "idle"),
            "counts": {
                "queued": sum(1 for item in events if item.get("status") == "queued"),
                "claimed": sum(1 for item in events if item.get("status") == "claimed"),
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
                "total": len(events),
            },
            "events": events,
            "tools": tools,
            "updatedAt": max((stamp for stamp in timestamps if stamp), default=""),
            "observedAt": self.utc_now(),
            "live": live,
        }


__all__ = [
    "ConversationContextQueryService",
    "ConversationInboxQueryService",
    "ConversationNotFoundError",
    "SessionStateRepository",
]
