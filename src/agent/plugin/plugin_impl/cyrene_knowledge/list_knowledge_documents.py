"""Native Plugin for listing project knowledge documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.plugin import Plugin, PluginContext

from ._service import knowledge_service
from .definitions import get_native_tool_def, get_plugin_spec

TOOL_NAME = "ListKnowledgeDocuments"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("knowledge:project",),
}


async def handler(arguments: dict[str, Any], context: PluginContext) -> str:
    documents = await knowledge_service(context).list_documents(
        context,
        status=str(arguments.get("status") or ""),
        limit=int(arguments.get("limit") or 100),
    )
    if not documents:
        return "The knowledge base contains no documents matching the requested filters."
    searchable = sum(int(document.get("chunk_count") or 0) > 0 for document in documents)
    lines = [f"Knowledge base files: {len(documents)} returned; {searchable} searchable and {len(documents) - searchable} without searchable text."]
    for index, document in enumerate(documents, start=1):
        chunk_count = int(document.get("chunk_count") or 0)
        path = Path(str(document.get("path") or "")).expanduser()
        lines.append(
            f"[{index}] {document.get('name') or 'Untitled'} "
            f"(status={document.get('status') or 'unknown'}, chunks={chunk_count}, "
            f"size={int(document.get('size') or 0)}, "
            f"{'searchable' if chunk_count else 'not searchable'}, "
            f"id={document.get('id')}, path={path})"
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
