"""Fixed Read Plugin."""

from __future__ import annotations

import asyncio
import mimetypes
from typing import Any

from ..context import plugin_localized
from ..plugin import Plugin, PluginContext, PluginExecutionError, PluginFailure
from .permission_boundaries import path_boundary, resolved_path


_resolve_path = resolved_path


def read_permission_boundary(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any] | None:
    return path_boundary(
        arguments.get("path"),
        context,
        kind="read_elevation",
        operation="读取操作",
    )


async def read(arguments: dict[str, Any], context: PluginContext) -> str:
    path = _resolve_path(arguments.get("path"), context)
    try:
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        if "\x00" in content:
            raise UnicodeError("File contains NUL bytes")
    except UnicodeError as exc:
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        message = plugin_localized(
            context,
            "Read only supports UTF-8 text. This file could not be read as UTF-8 text "
            "(inferred type: {media_type}). For images, PDFs, or other attachments, "
            "use AnalyzeAttachment with the same path; use toolbox to discover or "
            "activate it if needed. For text in another encoding, decode it using "
            "the correct encoding. Retrying Read or re-uploading the same file will "
            "not fix this format mismatch.",
            "Read 仅支持 UTF-8 文本，无法将此文件作为 UTF-8 文本读取"
            "（推测类型：{media_type}）。图片、PDF 等附件请使用 AnalyzeAttachment，"
            "传入相同 path；如工具未加载，请通过 toolbox 查找或激活。"
            "其他编码的文本请使用正确编码解码。重试 Read 或重新上传相同文件"
            "无法解决此格式不匹配问题。",
            media_type=media_type,
        )
        raise PluginExecutionError(PluginFailure(
            error_code="read_unsupported_format",
            message=message,
            details={"path": str(path), "inferred_media_type": media_type,
                     "suggested_tool": "AnalyzeAttachment"},
        )) from exc
    start_line = arguments.get("start_line")
    end_line = arguments.get("end_line")
    if start_line is None and end_line is None:
        return content

    start = int(start_line or 1)
    end = int(end_line) if end_line is not None else None
    if end is not None and end < start:
        raise ValueError("end_line must be greater than or equal to start_line")
    return "".join(content.splitlines(keepends=True)[start - 1:end])


READ_PLUGIN = Plugin(
    name="Read",
    description=(
        "Read a UTF-8 text file, optionally selecting a 1-based inclusive "
        "line range. Does not read images, PDFs, or binary files; use "
        "AnalyzeAttachment for those (discover it through toolbox if needed)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "First line to return (1-based, inclusive).",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Last line to return (1-based, inclusive).",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    handler=read,
    metadata={
        "read_only": True,
        "resource_effects": ({
            "argument_path": ("path",),
            "kind": "file",
            "access": "read",
            "phase": "both",
        },),
    },
    permission_boundary=read_permission_boundary,
    allow_parallel=True,
    timeout_seconds=30.0,
)


__all__ = ["READ_PLUGIN", "read", "read_permission_boundary"]
