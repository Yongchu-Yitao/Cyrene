"""Shared accessors for the runtime-provided subagent service."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.core.plugin.execution import current_plugin_execution


def current_agent_id(context: PluginContext) -> str:
    """Return the identity assigned to the current Agent execution context."""

    return str(context.data.get("agent_id") or "main").strip() or "main"


def subagent_manager(context: PluginContext) -> Any:
    """Return the session-owned subagent manager exposed to Plugins."""

    manager = context.services.get("subagents")
    if manager is None:
        raise RuntimeError("PluginContext.services['subagents'] is unavailable")
    return manager


def current_effect_key() -> str:
    """Return the durable outer tool-call ID used to deduplicate side effects."""

    execution = current_plugin_execution()
    return str(execution.call.id) if execution is not None else ""


def result_text(result: Mapping[str, Any]) -> str:
    """Encode manager results as the string value expected by tool callers."""

    if not isinstance(result, Mapping):
        raise TypeError("subagent manager methods must return a mapping")
    return json.dumps(dict(result), ensure_ascii=False, default=str)


__all__ = [
    "current_agent_id",
    "current_effect_key",
    "result_text",
    "subagent_manager",
]
