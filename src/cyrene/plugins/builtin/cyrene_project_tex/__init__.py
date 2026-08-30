"""TeX document project support Plugin."""

from __future__ import annotations

from pathlib import Path

from cyrene.core.plugin import ExtensionContribution, PluginPack
from cyrene.plugins import WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution
from cyrene.plugins.project_types import scope_id, workspace_action


def detect(workspace: Path, current_path: str):
    path = str(current_path or "").replace("\\", "/")
    target = (workspace / path).resolve()
    if not path.endswith(".tex") or not target.is_file() or not target.is_relative_to(workspace.resolve()):
        return []
    relative = target.relative_to(workspace.resolve()).as_posix()
    return [workspace_action(
        f"tex.build.{scope_id(relative)}", "Build document", "build",
        "latexmk", ["-pdf", "-interaction=nonstopmode", relative],
        artifacts=[str(Path(relative).with_suffix(".pdf"))],
    )]


plugin_pack = PluginPack(
    id="cyrene_project_tex",
    description="TeX project detection and PDF build actions.",
    plugins=(),
    metadata={"default_enabled": False},
    contributions=(ExtensionContribution(
        WORKSPACE_PROJECT_TYPE,
        WorkspaceProjectTypeContribution(
            id="tex",
            title="TeX",
            detect=detect,
            marker_files=("latexmkrc",),
            runtime_extensions=("toolchain:tex",),
        ),
    ),
    ),
)

__all__ = ["detect", "plugin_pack"]
