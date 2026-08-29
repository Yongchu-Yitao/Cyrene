"""Native Plugin for listing project-library items."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import Plugin, PluginContext
from cyrene.plugins.native_runtime import plugin_localized, plugin_localized_plural

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
        return plugin_localized(
            context,
            "The current project literature library contains no matching items.",
            "当前项目文献库中没有匹配项。",
        )
    lines = [plugin_localized_plural(
        context,
        "Project literature library: returned {count} of {total} matching item.",
        "Project literature library: returned {count} of {total} matching items.",
        "项目文献库：返回 {count}/{total} 个匹配项。",
        count=len(items),
        total=int(result.get("total") or 0),
    )]
    for index, item in enumerate(items, start=1):
        authors = creator_label(item.get("creators") or []) or plugin_localized(
            context, "Unknown author", "未知作者"
        )
        lines.append(
            plugin_localized(
                context,
                "[{index}] {title} | authors={authors} | year={year} | venue={venue} | ",
                "[{index}] {title} | 作者={authors} | 年份={year} | 来源={venue} | ",
                index=index,
                title=item.get("title") or plugin_localized(context, "Untitled", "无标题"),
                authors=authors,
                year=item.get("year") or "",
                venue=item.get("venue") or "",
            )
            + f"doi={item.get('doi') or ''} | citekey={item.get('citekey') or ''} | "
            + plugin_localized(
                context,
                "status={status} | paper_id={paper_id}",
                "状态={status} | paper_id={paper_id}",
                status=item.get("reading_status") or "unread",
                paper_id=item.get("id"),
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
