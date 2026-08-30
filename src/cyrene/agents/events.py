"""Unified Agent Event envelope foundation."""

from __future__ import annotations

import uuid
from typing import Any

CORE_EVENT_TYPES = frozenset({
    "run.started",
    "run.awaiting_input",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "message.started",
    "message.delta",
    "message.completed",
    "notification.created",
    "reasoning.started",
    "reasoning.delta",
    "reasoning.completed",
    "tool.started",
    "tool.updated",
    "tool.completed",
    "permission.requested",
    "permission.resolved",
    "elicitation.requested",
    "elicitation.resolved",
    "artifact.created",
    "artifact.updated",
    "usage.updated",
    "session.updated",
})

_SECRET_KEY_PATTERNS = (
    "token",
    "api_key",
    "api-key",
    "apikey",
    "authorization",
    "secret",
    "password",
    "credential",
    "cookie",
    "oauth",
)

def _sanitize_event_value(value: Any) -> Any:
    """Recursively remove credential-shaped keys from driver-owned data."""
    if isinstance(value, dict):
        return {
            str(key): _sanitize_event_value(item)
            for key, item in value.items()
            if not _is_secret_event_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_event_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _is_secret_event_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    compact = normalized.replace("_", "")
    # Token accounting fields such as totalTokens/inputTokens are telemetry,
    # not credentials. Redact credential-shaped names, not every occurrence of
    # the substring "token".
    if compact.endswith("tokens") or compact in {
        "tokencount", "totaltokens", "inputtokens", "outputtokens",
        "prompttokens", "completiontokens",
    }:
        return False
    return normalized in _SECRET_KEY_PATTERNS or compact in {
        "accesstoken", "refreshtoken", "authtoken", "bearertoken",
        "apikey", "xapikey", "authorization", "clientsecret", "password",
        "credential", "credentials", "cookie", "oauth",
    }

def sanitize_event_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Recursively redact credential-shaped fields from event data.

    Long-lived model keys, Agent tokens and Authorization headers must never
    reach chat JSON, event streams, or frontend state (handoff §19.3).
    """
    if not isinstance(payload, dict):
        return {}
    return _sanitize_event_value(payload)


def event_envelope(
    *,
    type: str,
    payload: dict[str, Any] | None = None,
    event_id: str = "",
    timestamp: str = "",
    agent_id: str = "",
    installation_id: str = "",
    chat_id: str = "",
    run_id: str = "",
    session_id: str = "",
    actor_id: str = "primary",
    parent_run_id: str | None = None,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one normalized event envelope (handoff §12.1)."""
    return {
        "schemaVersion": 1,
        "eventId": event_id or f"evt_{uuid.uuid4().hex[:12]}",
        "timestamp": timestamp,
        "agentId": agent_id,
        "installationId": installation_id,
        "chatId": chat_id,
        "runId": run_id,
        "sessionId": session_id,
        "actorId": actor_id,
        "parentRunId": parent_run_id,
        "type": type,
        "payload": sanitize_event_payload(payload),
        "extensions": sanitize_event_payload(extensions),
    }
