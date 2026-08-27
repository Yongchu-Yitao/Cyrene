"""Native Plugin for bibliographic and indexed-evidence search."""

from __future__ import annotations

from typing import Any

from agent.plugin import Plugin, PluginContext

from ._service import knowledge_service
from .definitions import get_native_tool_def, get_plugin_spec
from .service import creator_label

TOOL_NAME = "SearchLibrary"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("library:project", "knowledge:project"),
}


async def handler(arguments: dict[str, Any], context: PluginContext) -> str:
    query = str(arguments.get("query") or "").strip()
    results = await knowledge_service(context).search_library(
        context,
        query,
        limit=int(arguments.get("k") or 8),
        status=str(arguments.get("status") or ""),
        tag=str(arguments.get("tag") or ""),
    )
    if not results:
        return "No matching papers or indexed evidence were found in the project library."
    lines = [f"Found {len(results)} project-library paper(s) for: {query}"]
    for index, result in enumerate(results, start=1):
        item = result["item"]
        paper_id = str(item.get("id") or "")
        authors = creator_label(item.get("creators") or []) or "Unknown author"
        lines.append(
            f"\n[{index}] {item.get('title') or 'Untitled'}\n"
            f"paper_id={paper_id}; authors={authors}; year={item.get('year') or ''}; "
            f"venue={item.get('venue') or ''}; doi={item.get('doi') or ''}; "
            f"citekey={item.get('citekey') or ''}; "
            f"status={item.get('reading_status') or 'unread'}"
        )
        abstract = str(item.get("abstract") or "").strip()
        if abstract:
            lines.append(f"Abstract: {abstract[:500]}")
        for hit in result.get("evidence") or []:
            content = " ".join(str(hit.get("content") or "").split())
            if content:
                lines.append(f"Evidence ({hit.get('document_name') or 'attachment'}, mode={hit.get('mode') or 'search'}): {content[:500]}")
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
