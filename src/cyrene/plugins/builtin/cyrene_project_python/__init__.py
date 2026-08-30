"""Python project support Plugin."""

from __future__ import annotations

from pathlib import Path

from cyrene.core.plugin import ExtensionContribution, PluginPack
from cyrene.plugins import WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution
from cyrene.plugins.project_types import (
    nearest_scope,
    preferred_program,
    relative_scope,
    scope_id,
    workspace_action,
)


def detect(workspace: Path, current_path: str):
    scope = nearest_scope(workspace, current_path, "pyproject.toml", "pytest.ini", "setup.py")
    actions = []
    if scope is not None:
        cwd = relative_scope(workspace, scope)
        program, prefix = (
            (preferred_program("uv"), ["run"])
            if (scope / "uv.lock").exists()
            else (preferred_program("python3", "python", "py"), ["-m"])
        )
        actions.append(workspace_action(
            f"python.test.{scope_id(cwd)}", "Run Python tests", "test",
            program, [*prefix, "pytest"], cwd=cwd,
        ))
    path = str(current_path or "").replace("\\", "/")
    target = (workspace / path).resolve()
    if path.endswith(".py") and target.is_file() and target.is_relative_to(workspace.resolve()):
        relative = target.relative_to(workspace.resolve()).as_posix()
        actions.append(workspace_action(
            f"python.file.{scope_id(relative)}", "Run current Python file", "run",
            preferred_program("python3", "python", "py"), [relative],
        ))
    return actions


plugin_pack = PluginPack(
    id="cyrene_project_python",
    description="Python project detection and workspace actions.",
    plugins=(),
    metadata={"default_enabled": False},
    contributions=(ExtensionContribution(
        WORKSPACE_PROJECT_TYPE,
        WorkspaceProjectTypeContribution(
            id="python",
            title="Python",
            detect=detect,
            marker_files=("pyproject.toml", "pytest.ini", "setup.py"),
            runtime_extensions=("toolchain:python", "toolchain:uv"),
        ),
    ),
    ),
)

__all__ = ["detect", "plugin_pack"]
