"""Shared guidance payload parsing, wakeup policy and transcript provenance."""

from __future__ import annotations

import json
from typing import Any


def interrupting_guidance(payload: dict[str, Any]) -> bool:
    # Status notifications wait for a normal processing boundary. Explicit
    # task updates and peer messages still steer the running model.
    return not (payload.get("agent_originated") and payload.get("agent_message_kind") == "task_notification")


def guidance_interrupt(event: dict[str, Any] | None) -> bool:
    event = event or {}
    return (bool(event["interrupt"]) if event.get("type") == "guidance_recovered"
            else interrupting_guidance(event.get("payload") or {}))


def inbox_payload(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def guidance_message_provenance(record: dict[str, Any]) -> dict[str, Any]:
    provenance = ({"agentOriginated": True,
                   "originSessionId": str(record.get("originSessionId") or ""),
                   "agentMessageKind": str(record.get("agentMessageKind") or "")}
                  if record.get("agentOriginated") else {})
    if record.get("attachments"):
        provenance["attachments"] = record["attachments"]
    return provenance
