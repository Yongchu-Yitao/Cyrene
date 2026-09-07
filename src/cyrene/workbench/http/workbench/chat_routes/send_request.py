"""Parsed request metadata, separate from mutable turn/rollback state."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SendOrigin:
    client_request_id: str
    client_send_epoch_ms: float
    ui_instance_id: str
    conversation_source: str
    agent_originated: bool
    origin_session_id: str

    @classmethod
    def parse(cls, body: dict[str, Any]) -> SendOrigin:
        client_request_id = str(body.get("clientRequestId") or "").strip()
        try:
            client_send_epoch_ms = float(body.get("clientSendEpochMs") or 0.0)
        except (TypeError, ValueError, OverflowError):
            client_send_epoch_ms = 0.0
        return cls(
            client_request_id=client_request_id,
            client_send_epoch_ms=client_send_epoch_ms,
            ui_instance_id=str(body.get("uiInstanceId") or "").strip(),
            conversation_source=str(body.get("conversationSource") or "").strip(),
            agent_originated=body.get("agentOriginated") is True,
            origin_session_id=str(body.get("sourceSessionId") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class SendOptions:
    requested_context_activations: Any
    wants_stream: bool
    retry: bool
    fork_replay: bool
    requested_mode: str
    requested_model: str
    requested_effort: str
    lang: str
    voice_command: bool

    @classmethod
    def parse(cls, body: dict[str, Any]) -> SendOptions:
        return cls(
            requested_context_activations=body.get("contextActivations") if "contextActivations" in body else None,
            wants_stream=bool(body.get("stream")),
            retry=bool(body.get("retry")),
            fork_replay=bool(body.get("forkReplay")),
            requested_mode=str(body.get("mode") or "").strip().lower(),
            requested_model=str(body.get("model") or "").strip(),
            requested_effort=str(body.get("reasoningEffort") or "").strip().lower(),
            lang=str(body.get("lang") or "").strip().lower(),
            voice_command=body.get("voiceCommand") is True,
        )
