"""Go project support Plugin."""

from pathlib import Path

from cyrene.core.plugin import ExtensionContribution, PluginPack
from cyrene.plugins import WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution
from cyrene.plugins.project_types import nearest_scope, relative_scope, scope_id, workspace_action


def detect(workspace: Path, current_path: str):
    scope = nearest_scope(workspace, current_path, "go.mod", "go.work")
    if scope is None:
        return []
    cwd = relative_scope(workspace, scope)
    suffix = scope_id(cwd)
    return [
        workspace_action(f"go.run.{suffix}", "Run Go project", "run", "go", ["run", "."], cwd=cwd, i18n={"zh": {"label": "运行 Go 项目"}}),
        workspace_action(f"go.build.{suffix}", "Build Go project", "build", "go", ["build", "./..."], cwd=cwd, i18n={"zh": {"label": "构建 Go 项目"}}),
        workspace_action(f"go.test.{suffix}", "Test Go project", "test", "go", ["test", "./..."], cwd=cwd, i18n={"zh": {"label": "测试 Go 项目"}}),
    ]


plugin_pack = PluginPack(
    id="cyrene_project_go",
    description="Go project detection and workspace actions.",
    plugins=(),
    metadata={"default_enabled": False},
    contributions=(ExtensionContribution(WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution(
        id="go", title="Go", detect=detect, marker_files=("go.mod", "go.work"),
        runtime_extensions=("toolchain:go",),
    )),),
)

__all__ = ["detect", "plugin_pack"]
