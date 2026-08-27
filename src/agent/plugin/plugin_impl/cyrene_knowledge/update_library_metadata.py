"""Native Plugin for verified project-library metadata updates."""

from __future__ import annotations

from typing import Any

from agent.plugin import Plugin, PluginContext
from agent.plugin.native_runtime import plugin_localized

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
        raise ValueError(plugin_localized(
            context,
            "Metadata must be an object.",
            "元数据必须是对象。",
        ))
    updated, written, skipped = await knowledge_service(context).update_library_metadata(
        context,
        paper_id,
        metadata,
        overwrite=bool(arguments.get("overwrite")),
    )
    if not written:
        suffix = plugin_localized(
            context,
            " Existing fields preserved: {fields}.",
            " 已保留现有字段：{fields}。",
            fields=", ".join(skipped),
        ) if skipped else ""
        return plugin_localized(
            context,
            "No metadata was changed.",
            "未更改任何元数据。",
        ) + suffix
    result = plugin_localized(
        context,
        "Updated project-library paper {paper_id}. Written fields: {fields}.",
        "已更新项目文献库论文 {paper_id}。已写入字段：{fields}。",
        paper_id=paper_id,
        fields=", ".join(written),
    )
    if skipped:
        result += plugin_localized(
            context,
            " Preserved existing fields: {fields}.",
            " 已保留现有字段：{fields}。",
            fields=", ".join(skipped),
        )
    sources = [str(value).strip() for value in arguments.get("sources") or [] if str(value or "").strip()]
    if sources:
        result += plugin_localized(
            context,
            " Sources: {sources}",
            " 来源：{sources}",
            sources=", ".join(sources),
        )
    if updated:
        result += plugin_localized(
            context,
            "\nTitle: {title}",
            "\n标题：{title}",
            title=updated.get("title") or plugin_localized(context, "Untitled", "无标题"),
        )
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
