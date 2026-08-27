"""Native Plugin for bibliographic and indexed-evidence search."""

from __future__ import annotations

from typing import Any

from agent.plugin import Plugin, PluginContext
from agent.plugin.native_runtime import plugin_localized, plugin_localized_plural

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
        return plugin_localized(
            context,
            "No matching papers or indexed evidence were found in the project library.",
            "项目文献库中没有匹配的论文或索引证据。",
        )
    lines = [plugin_localized_plural(
        context,
        "Found {count} project-library paper for: {query}",
        "Found {count} project-library papers for: {query}",
        "在项目文献库中找到 {count} 篇与“{query}”相关的论文",
        count=len(results),
        query=query,
    )]
    for index, result in enumerate(results, start=1):
        item = result["item"]
        paper_id = str(item.get("id") or "")
        authors = creator_label(item.get("creators") or []) or plugin_localized(
            context, "Unknown author", "未知作者"
        )
        lines.append(
            plugin_localized(
                context,
                "\n[{index}] {title}\npaper_id={paper_id}; authors={authors}; year={year}; venue={venue}; doi={doi}; ",
                "\n[{index}] {title}\npaper_id={paper_id}; 作者={authors}; 年份={year}; 来源={venue}; doi={doi}; ",
                index=index,
                title=item.get("title") or plugin_localized(context, "Untitled", "无标题"),
                paper_id=paper_id,
                authors=authors,
                year=item.get("year") or "",
                venue=item.get("venue") or "",
                doi=item.get("doi") or "",
            )
            + f"citekey={item.get('citekey') or ''}; "
            + plugin_localized(
                context,
                "status={status}",
                "状态={status}",
                status=item.get("reading_status") or "unread",
            )
        )
        abstract = str(item.get("abstract") or "").strip()
        if abstract:
            lines.append(plugin_localized(
                context,
                "Abstract: {abstract}",
                "摘要：{abstract}",
                abstract=abstract[:500],
            ))
        for hit in result.get("evidence") or []:
            content = " ".join(str(hit.get("content") or "").split())
            if content:
                lines.append(plugin_localized(
                    context,
                    "Evidence ({document}, mode={mode}): {content}",
                    "证据（{document}，模式={mode}）：{content}",
                    document=hit.get("document_name") or plugin_localized(context, "attachment", "附件"),
                    mode=hit.get("mode") or "search",
                    content=content[:500],
                ))
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
