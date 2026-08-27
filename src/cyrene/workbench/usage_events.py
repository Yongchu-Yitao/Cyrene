"""Bridge Plugin/Workbench events into durable usage accounting."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

logger = logging.getLogger(__name__)

_ACCOUNTING_EVENT_TYPES = frozenset({
    "llm_call",
    "tool_call",
    "auto_review",
    "permission_decision",
    "destructive_confirmation",
    "external_upload_confirmation",
    "self_configuration_confirmation",
    "host_lifecycle_confirmation",
})


async def publish_usage_event(
    event: Mapping[str, Any],
    *,
    session_id: str = "",
) -> None:
    """Publish one accounting event without coupling Plugins to persistence.

    Model Plugins already emit canonical ``llm_call`` events. Workbench tool
    lifecycle events use the public ``tool.started`` envelope, which is
    projected once into the stable analytics ``tool_call`` shape here.
    """

    event_type = str(event.get("type") or "")
    accounting_event: dict[str, Any]
    if event_type in _ACCOUNTING_EVENT_TYPES:
        accounting_event = dict(event)
    elif event_type == "tool.started":
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        tool = str(payload.get("name") or payload.get("tool") or "").strip()
        if not tool:
            return
        accounting_event = {
            "type": "tool_call",
            "timestamp": str(event.get("timestamp") or ""),
            "round_id": str(event.get("runId") or event.get("run_id") or ""),
            "session_id": str(
                event.get("sessionId")
                or event.get("session_id")
                or session_id
                or ""
            ),
            "caller": "main_agent",
            "tool": tool,
        }
    else:
        return

    from cyrene.observability import debug

    try:
        await debug.publish_event(
            accounting_event,
            session_id=str(
                accounting_event.get("session_id") or session_id or ""
            ),
        )
    except Exception:
        logger.debug("Usage event publication failed", exc_info=True)


__all__ = ["publish_usage_event"]
