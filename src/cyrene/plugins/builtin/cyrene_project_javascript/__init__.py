"""JavaScript, TypeScript, Node.js, Bun, and Deno project support Plugin."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from cyrene.core.plugin import ExtensionContribution, PluginPack
from cyrene.plugins import WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution
from cyrene.plugins.project_types import nearest_scope, relative_scope, scope_id, workspace_action


def _package_actions(workspace: Path, current_path: str):
    scope = nearest_scope(workspace, current_path, "package.json")
    if scope is None:
        return []
    try:
        package = json.loads((scope / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    scripts = package.get("scripts") if isinstance(package, Mapping) else {}
    scripts = scripts if isinstance(scripts, Mapping) else {}
    cwd = relative_scope(workspace, scope)
    if (scope / "bun.lock").exists() or (scope / "bun.lockb").exists():
        manager = "bun"
    elif (scope / "pnpm-lock.yaml").exists():
        manager = "pnpm"
    elif (scope / "yarn.lock").exists():
        manager = "yarn"
    else:
        manager = "npm"
    result = []
    run_added = False
    for name, kind, label, zh_label, long_running in (
        ("dev", "run", "Start development server", "启动开发服务器", True),
        ("start", "run", "Start application", "启动应用", True),
        ("build", "build", "Build project", "构建项目", False),
        ("test", "test", "Run tests", "运行测试", False),
        ("preview", "preview", "Preview build", "预览构建", True),
    ):
        if name not in scripts or (kind == "run" and run_added):
            continue
        args = ["run", name] if manager in {"npm", "bun"} else [name]
        result.append(workspace_action(
            f"javascript.{name}.{scope_id(cwd)}", label, kind, manager, args,
            cwd=cwd, long_running=long_running,
            i18n={"zh": {"label": zh_label}},
        ))
        run_added = run_added or kind == "run"
    return result


def _deno_actions(workspace: Path, current_path: str):
    scope = nearest_scope(workspace, current_path, "deno.json", "deno.jsonc")
    if scope is None:
        return []
    config_path = scope / ("deno.json" if (scope / "deno.json").exists() else "deno.jsonc")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tasks = config.get("tasks") if isinstance(config, Mapping) else {}
    tasks = tasks if isinstance(tasks, Mapping) else {}
    cwd = relative_scope(workspace, scope)
    result = []
    for name, kind, label, zh_label, long_running in (
        ("dev", "run", "Start Deno development task", "启动 Deno 开发任务", True),
        ("start", "run", "Start Deno application", "启动 Deno 应用", True),
        ("build", "build", "Build Deno project", "构建 Deno 项目", False),
        ("test", "test", "Test Deno project", "测试 Deno 项目", False),
    ):
        if name in tasks:
            result.append(workspace_action(
                f"deno.{name}.{scope_id(cwd)}", label, kind,
                "deno", ["task", name], cwd=cwd, long_running=long_running,
                i18n={"zh": {"label": zh_label}},
            ))
    return result


def detect(workspace: Path, current_path: str):
    return [*_package_actions(workspace, current_path), *_deno_actions(workspace, current_path)]


plugin_pack = PluginPack(
    id="cyrene_project_javascript",
    description="JavaScript and TypeScript project detection and workspace actions.",
    plugins=(),
    metadata={"default_enabled": False},
    contributions=(ExtensionContribution(
        WORKSPACE_PROJECT_TYPE,
        WorkspaceProjectTypeContribution(
            id="javascript",
            title="JavaScript / TypeScript",
            detect=detect,
            marker_files=("package.json", "deno.json", "deno.jsonc"),
            runtime_extensions=("toolchain:node", "toolchain:bun", "toolchain:deno"),
        ),
    ),
    ),
)

__all__ = ["detect", "plugin_pack"]
