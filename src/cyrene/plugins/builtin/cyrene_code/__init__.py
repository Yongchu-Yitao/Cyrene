"""Editable Cyrene code Plugin pack."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import ModuleType
from typing import Any

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import ExtensionContribution, Plugin, PluginPack
from cyrene.plugins import (
    WORKBENCH_SURFACE,
    WORKSPACE_ACTION,
    WORKSPACE_FILE_TYPE,
    WorkbenchSurfaceContribution,
    WorkbenchSurfaceRenderer,
    WorkspaceFileTypeContribution,
    WorkspaceActionContribution,
)

from . import (
    analysis,
    delete_shell,
    git,
    indexer,
    interrupt_shell,
    list_shells,
    read_shell,
    send_shell,
    show_shell,
    start_shell,
    workspace_action,
)
from .services import setup, setup_application


def application_setup(context: PluginApplicationContext) -> None:
    setup_application(context)


def _plugin(
    definition: Mapping[str, Any],
    handler: Callable[..., Any],
    metadata: Mapping[str, Any] | None = None,
    permission_boundary: Callable[..., Any] | None = None,
) -> Plugin:
    function = definition["function"]
    if not isinstance(function, Mapping):
        raise TypeError("code Plugin definition must contain a function mapping")
    values = dict(metadata or {})
    resource_effects = {
        "IndexCodebase": ({
            "argument_path": ("path",), "kind": "directory",
            "access": "scan", "phase": "both",
        },),
        "GetFileSymbols": ({
            "argument_path": ("path",), "kind": "file",
            "access": "scan", "phase": "both",
        },),
        "CodeReview": ({
            "argument_path": ("path",), "kind": "file",
            "access": "scan", "phase": "both",
        },),
    }.get(str(function["name"]))
    if resource_effects:
        values["resource_effects"] = resource_effects
    return Plugin(
        name=str(function["name"]),
        description=str(function.get("description") or ""),
        input_schema=dict(
            function.get("parameters")
            or {"type": "object", "properties": {}}
        ),
        handler=handler,
        allow_parallel=bool(
            values.get("allow_parallel", not values.get("requires_order", True))
        ),
        timeout_seconds=float(values.get("timeout_seconds", 180.0)),
        metadata=values,
        permission_boundary=permission_boundary,
    )


def _module_plugin(module: ModuleType) -> Plugin:
    metadata = dict(getattr(module, "TOOL_METADATA", {}))
    if str(module.TOOL_DEF["function"]["name"]) == "StartShell":
        metadata["main_only"] = True
    return _plugin(
        module.TOOL_DEF,
        module.handler,
        metadata,
        getattr(module, "permission_boundary", None),
    )


_shell_modules = (
    start_shell,
    send_shell,
    list_shells,
    read_shell,
    interrupt_shell,
    show_shell,
    delete_shell,
    workspace_action,
)
_declarations = (
    *analysis.PLUGIN_DECLARATIONS,
    *git.PLUGIN_DECLARATIONS,
    *indexer.PLUGIN_DECLARATIONS,
)

plugin_pack = PluginPack(
    id="cyrene_code",
    description="Shell sessions, code analysis, Git, and workspace indexing.",
    plugins=tuple(_module_plugin(module) for module in _shell_modules)
    + tuple(_plugin(definition, handler) for definition, handler in _declarations),
    setup=setup,
    application_setup=application_setup,
    metadata={
        "workbench_entries": (
            {
                "id": "files",
                "title": "Files",
                "description": "Browse files in the active Cyrene project.",
                "i18n": {
                    "zh": {
                        "title": "文件",
                        "description": "浏览当前 Cyrene 项目中的文件。",
                    }
                },
            },
            {
                "id": "terminal",
                "title": "Terminal",
                "description": "Open and manage project terminal sessions.",
                "i18n": {
                    "zh": {
                        "title": "终端",
                        "description": "打开和管理项目终端会话。",
                    }
                },
            },
        ),
    },
    contributions=(
        ExtensionContribution(WORKBENCH_SURFACE, WorkbenchSurfaceContribution(
            id="file-editor",
            title="Workspace",
            i18n={"zh": {"title": "工作区"}},
            renderer=WorkbenchSurfaceRenderer(kind="native", id="workspace-composite"),
            accepted_activities=("read", "write", "scan", "execute", "build", "run", "test", "preview"),
            resource_kinds=("file", "execution", "endpoint", "artifact"),
            lifetime="sticky",
            preferred_side="right",
        )),
        ExtensionContribution(WORKBENCH_SURFACE, WorkbenchSurfaceContribution(
            id="directory-tree",
            title="File structure",
            i18n={"zh": {"title": "文件结构"}},
            renderer=WorkbenchSurfaceRenderer(kind="native", id="workspace-directory"),
            accepted_activities=("scan",),
            resource_kinds=("directory",),
            lifetime="run",
            preferred_side="right",
        )),
        ExtensionContribution(WORKSPACE_FILE_TYPE, WorkspaceFileTypeContribution(
            id="source-code",
            extensions=(
                ".c", ".cc", ".cpp", ".css", ".go", ".h", ".hpp", ".java",
                ".js", ".jsx", ".kt", ".py", ".rs", ".scss", ".sh", ".swift",
                ".svelte", ".ts", ".tsx", ".vue",
            ),
            editable=True,
            default_surface="cyrene_code/file-editor",
        )),
        ExtensionContribution(WORKSPACE_ACTION, WorkspaceActionContribution(
            id="build",
            kind="build",
            method="workspace_execution.start",
            title="Build",
            i18n={"zh": {"title": "构建"}},
            marker_files=("package.json", "pyproject.toml", "Makefile"),
            outputs=("diagnostics", "artifact", "terminal"),
            default_surface="cyrene_code/file-editor",
        )),
        ExtensionContribution(WORKSPACE_ACTION, WorkspaceActionContribution(
            id="run",
            kind="run",
            method="workspace_execution.start",
            title="Run",
            i18n={"zh": {"title": "运行"}},
            marker_files=("package.json", "pyproject.toml", "Makefile"),
            outputs=("diagnostics", "endpoint", "terminal"),
            default_surface="cyrene_code/file-editor",
        )),
        ExtensionContribution(WORKSPACE_ACTION, WorkspaceActionContribution(
            id="test",
            kind="test",
            method="workspace_execution.start",
            title="Test",
            i18n={"zh": {"title": "测试"}},
            marker_files=("package.json", "pyproject.toml", "Makefile"),
            outputs=("diagnostics", "artifact", "terminal"),
            default_surface="cyrene_code/file-editor",
        )),
        ExtensionContribution(WORKSPACE_ACTION, WorkspaceActionContribution(
            id="preview",
            kind="preview",
            method="workspace_execution.start",
            title="Preview",
            i18n={"zh": {"title": "预览"}},
            extensions=(".html", ".pdf"),
            outputs=("artifact", "endpoint", "terminal"),
            default_surface="cyrene_code/file-editor",
        )),
        ExtensionContribution(WORKSPACE_FILE_TYPE, WorkspaceFileTypeContribution(
            id="documents",
            extensions=(".md", ".mdx", ".rst", ".tex", ".txt"),
            editable=True,
            default_surface="cyrene_code/file-editor",
        )),
        ExtensionContribution(WORKSPACE_FILE_TYPE, WorkspaceFileTypeContribution(
            id="structured-text",
            extensions=(".env", ".html", ".ini", ".json", ".toml", ".xml", ".yaml", ".yml"),
            editable=True,
            default_surface="cyrene_code/file-editor",
        )),
        ExtensionContribution(WORKSPACE_FILE_TYPE, WorkspaceFileTypeContribution(
            id="images",
            extensions=(".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp"),
            editable=False,
            default_surface="cyrene_code/file-editor",
        )),
        ExtensionContribution(WORKSPACE_FILE_TYPE, WorkspaceFileTypeContribution(
            id="pdf",
            extensions=(".pdf",),
            mime_types=("application/pdf",),
            editable=False,
            default_surface="cyrene_code/file-editor",
        )),
    ),
)
if len(plugin_pack.plugins) != 20:
    raise RuntimeError("code pack must contain exactly 20 Plugins")

__all__ = ["application_setup", "plugin_pack"]
