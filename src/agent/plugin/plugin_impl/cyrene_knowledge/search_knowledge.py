"""Native Plugin for hybrid project-knowledge search."""

from __future__ import annotations

from typing import Any

from agent.plugin import Plugin, PluginContext

from ._service import knowledge_service
from .definitions import get_native_tool_def, get_plugin_spec

TOOL_NAME = "SearchKnowledge"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("knowledge:project",),
}


async def handler(arguments: dict[str, Any], context: PluginContext) -> str:
    query = str(arguments.get("query") or "").strip()
    results = await knowledge_service(context).search_knowledge(
        context,
        query,
        limit=int(arguments.get("k") or 6),
    )
    if not results:
        return "No matching documents found in the knowledge base."
    lines = [f"Found {len(results)} matching passage(s) from the knowledge base:"]
    for index, result in enumerate(results, start=1):
        similarity = result.get("cosine_similarity")
        similarity_text = f"; cosine_similarity={float(similarity):.6f}" if similarity is not None else ""
        content = str(result.get("content") or "").strip()[:500]
        lines.append(f"[{index}] {result.get('document_name') or 'Unknown'}{similarity_text}\n{content}")
    return "\n\n".join(lines)


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
