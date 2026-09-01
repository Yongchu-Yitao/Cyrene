"""Host-neutral helpers derived from an active :class:`PluginContext`.

The core runtime only knows explicit values and services carried by the
invocation.  Product adapters may provide richer fallbacks, but core never
reaches into the Workbench application singleton.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from typing import Any

from .execution import current_plugin_execution, require_plugin_execution
from .plugin import PluginContext


def current_plugin_context() -> PluginContext:
    return require_plugin_execution().context


def plugin_service(name: str) -> Any | None:
    return current_plugin_context().services.get(str(name or "").strip())


def run_context_data(context: PluginContext) -> Mapping[str, Any]:
    value = context.data.get("run_context")
    return value if isinstance(value, Mapping) else {}


def run_context_value(
    context: PluginContext,
    name: str,
    default: Any = "",
) -> Any:
    key = str(name or "").strip()
    if key in context.data:
        return context.data[key]
    return run_context_data(context).get(key, default)


def plugin_language(context: PluginContext | None = None) -> str:
    execution = current_plugin_execution()
    active = context or (execution.context if execution is not None else None)
    if active is None:
        return "en"
    raw = str(
        active.data.get("language")
        or run_context_value(active, "language", "")
        or run_context_value(active, "app_language", "")
        or "en"
    ).strip().replace("_", "-").lower()
    return "zh" if raw == "zh" or raw.startswith("zh-") else "en"


def plugin_localized(
    context: PluginContext | None,
    en: str,
    zh: str,
    **values: Any,
) -> str:
    template = zh if plugin_language(context) == "zh" else en
    return template.format(**values)


def plugin_localized_plural(
    context: PluginContext | None,
    en_one: str,
    en_other: str,
    zh: str,
    *,
    count: int | float,
    **values: Any,
) -> str:
    if plugin_language(context) == "zh":
        template = zh
    else:
        template = en_one if count == 1 else en_other
    return template.format(count=count, **values)


async def publish_runtime_event(
    context: PluginContext,
    event: Mapping[str, Any],
) -> bool:
    writer = (
        context.services.get("runtime_events")
        or run_context_data(context).get("runtime_event_writer")
        or context.data.get("runtime_event_writer")
    )
    if not callable(writer):
        return False
    result = writer(dict(event))
    if inspect.isawaitable(result):
        await result
    return True


def json_result(payload: Any) -> str:
    return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


__all__ = [
    "current_plugin_context",
    "json_result",
    "plugin_language",
    "plugin_localized",
    "plugin_localized_plural",
    "plugin_service",
    "publish_runtime_event",
    "run_context_data",
    "run_context_value",
]
