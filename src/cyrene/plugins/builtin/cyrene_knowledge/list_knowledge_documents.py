"""Native Plugin for listing project knowledge documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyrene.core.plugin import Plugin, PluginContext
from cyrene.plugins.native_runtime import plugin_localized

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
        return plugin_localized(
            context,
            "The knowledge base contains no documents matching the requested filters.",
            "知识库中没有符合当前筛选条件的文档。",
        )
    searchable = sum(int(document.get("chunk_count") or 0) > 0 for document in documents)
    lines = [plugin_localized(
        context,
        "Knowledge base files: {count} returned; {searchable} searchable and {unsearchable} without searchable text.",
        "知识库文件：返回 {count} 个；{searchable} 个可搜索，{unsearchable} 个暂无可搜索文本。",
        count=len(documents),
        searchable=searchable,
        unsearchable=len(documents) - searchable,
    )]
    for index, document in enumerate(documents, start=1):
        chunk_count = int(document.get("chunk_count") or 0)
        path = Path(str(document.get("path") or "")).expanduser()
        lines.append(
            plugin_localized(
                context,
                "[{index}] {name} (status={status}, chunks={chunks}, size={size}, {searchability}, id={document_id}, path={path})",
                "[{index}] {name}（状态={status}，分块={chunks}，大小={size}，{searchability}，id={document_id}，路径={path}）",
                index=index,
                name=document.get("name") or plugin_localized(context, "Untitled", "无标题"),
                status=document.get("status") or "unknown",
                chunks=chunk_count,
                size=int(document.get("size") or 0),
                searchability=plugin_localized(
                    context,
                    "searchable" if chunk_count else "not searchable",
                    "可搜索" if chunk_count else "不可搜索",
                ),
                document_id=document.get("id"),
                path=path,
            )
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
