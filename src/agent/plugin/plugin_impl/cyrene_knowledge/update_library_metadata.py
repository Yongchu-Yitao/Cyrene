"""Native Plugin for verified project-library metadata updates."""

from __future__ import annotations

from typing import Any

from agent.plugin import Plugin, PluginContext

from ._service import knowledge_service
from .definitions import get_native_tool_def, get_plugin_spec

TOOL_NAME = "UpdateLibraryMetadata"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "read_only": False,
    "resource_keys": ("library:project",),
}


async def handler(arguments: dict[str, Any], context: PluginContext) -> str:
    paper_id = str(arguments.get("paper_id") or "").strip()
    metadata = arguments.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    updated, written, skipped = await knowledge_service(context).update_library_metadata(
        context,
        paper_id,
        metadata,
        overwrite=bool(arguments.get("overwrite")),
    )
    if not written:
        suffix = f" Existing fields preserved: {', '.join(skipped)}." if skipped else ""
        return "No metadata was changed." + suffix
    result = f"Updated project-library paper {paper_id}. Written fields: {', '.join(written)}."
    if skipped:
        result += f" Preserved existing fields: {', '.join(skipped)}."
    sources = [str(value).strip() for value in arguments.get("sources") or [] if str(value or "").strip()]
    if sources:
        result += " Sources: " + ", ".join(sources)
    if updated:
        result += f"\nTitle: {updated.get('title') or 'Untitled'}"
    return result


_spec = get_plugin_spec(TOOL_NAME)
plugin = Plugin(
    name=TOOL_NAME,
    description=_spec["description"],
    input_schema=_spec["input_schema"],
    handler=handler,
    allow_parallel=False,
    timeout_seconds=180,
    metadata=TOOL_METADATA,
)

__all__ = ["TOOL_DEF", "TOOL_METADATA", "TOOL_NAME", "handler", "plugin"]
