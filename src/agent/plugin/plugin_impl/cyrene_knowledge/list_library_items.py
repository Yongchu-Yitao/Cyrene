"""Native Plugin for listing project-library items."""

from __future__ import annotations

from typing import Any

from agent.plugin import Plugin, PluginContext

from ._service import knowledge_service
from .definitions import get_native_tool_def, get_plugin_spec
from .service import creator_label

TOOL_NAME = "ListLibraryItems"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("library:project",),
}


async def handler(arguments: dict[str, Any], context: PluginContext) -> str:
    result = await knowledge_service(context).list_library_items(
        context,
        query=str(arguments.get("query") or ""),
        status=str(arguments.get("status") or ""),
        collection_id=str(arguments.get("collection_id") or ""),
        tag=str(arguments.get("tag") or ""),
        limit=int(arguments.get("limit") or 50),
    )
    items = list(result.get("items") or [])
    if not items:
        return "The current project literature library contains no matching items."
    lines = [f"Project literature library: {len(items)} returned of {int(result.get('total') or 0)} matching item(s)."]
    for index, item in enumerate(items, start=1):
        authors = creator_label(item.get("creators") or []) or "Unknown author"
        lines.append(
            f"[{index}] {item.get('title') or 'Untitled'} | authors={authors} | "
            f"year={item.get('year') or ''} | venue={item.get('venue') or ''} | "
            f"doi={item.get('doi') or ''} | citekey={item.get('citekey') or ''} | "
            f"status={item.get('reading_status') or 'unread'} | paper_id={item.get('id')}"
        )
    return "\n".join(lines)


_spec = get_plugin_spec(TOOL_NAME)
plugin = Plugin(
    name=TOOL_NAME,
    description=_spec["description"],
    input_schema=_spec["input_schema"],
    handler=handler,
    allow_parallel=True,
    timeout_seconds=180,
    metadata=TOOL_METADATA,
)

__all__ = ["TOOL_DEF", "TOOL_METADATA", "TOOL_NAME", "handler", "plugin"]
