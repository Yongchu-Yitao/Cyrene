"""Open workspace HTML in an isolated, interactive desktop browser tab."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import (
    json_result,
    plugin_localized,
    resolve_workspace_path,
    workspace_root,
)

TOOL_NAME = "browser_open_file"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Open a local .html/.htm file inside the active workspace in the embedded "
            "Electron browser. Uses an isolated preview tab, replacing the previous local "
            "preview. Relative CSS, JS, images and other web assets in the HTML's directory "
            "and subdirectories are supported. External network resources, parent directories, "
            "hidden files and popups are blocked; bundle dependencies locally. Use existing "
            "browser_snapshot, browser_screenshot and browser click/type tools to inspect "
            "and test the result. Call again after editing to refresh the preview. "
            "Requires the desktop app; do not use browser_navigate with file:// or localhost."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative or absolute HTML file path."},
            },
            "required": ["path"],
        },
    },
}


async def handler(args: dict[str, Any], context: PluginContext) -> str:
    from . import runtime

    raw = str(args.get("path") or "").strip()
    try:
        if not raw:
            raise ValueError("Missing path")
        path = resolve_workspace_path(raw, context)
        if path.suffix.lower() not in {".html", ".htm"} or not path.is_file():
            raise ValueError("Not an HTML file")
        if any(part.startswith(".") for part in path.relative_to(workspace_root(context)).parts):
            raise ValueError("Hidden file")
    except (ValueError, OSError, RuntimeError):
        return json_result({
            "ok": False,
            "error": plugin_localized(
                context,
                "Provide an existing .html or .htm file inside the active workspace, outside hidden directories.",
                "请提供当前工作区内已存在的 .html 或 .htm 文件，且不能位于隐藏目录中。",
            ),
        })
    return json_result(await runtime.open_local_file(str(path), str(workspace_root(context))))
