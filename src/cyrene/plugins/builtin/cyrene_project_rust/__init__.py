"""Rust project support Plugin."""

from pathlib import Path

from cyrene.core.plugin import ExtensionContribution, PluginPack
from cyrene.plugins import WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution
from cyrene.plugins.project_types import nearest_scope, relative_scope, scope_id, workspace_action


def detect(workspace: Path, current_path: str):
    scope = nearest_scope(workspace, current_path, "Cargo.toml")
    if scope is None:
        return []
    cwd = relative_scope(workspace, scope)
    suffix = scope_id(cwd)
    return [
        workspace_action(f"rust.run.{suffix}", "Run Rust project", "run", "cargo", ["run"], cwd=cwd),
        workspace_action(f"rust.build.{suffix}", "Build Rust project", "build", "cargo", ["build"], cwd=cwd),
        workspace_action(f"rust.test.{suffix}", "Test Rust project", "test", "cargo", ["test"], cwd=cwd),
    ]


plugin_pack = PluginPack(
    id="cyrene_project_rust",
    description="Rust project detection and workspace actions.",
    plugins=(),
    metadata={"default_enabled": False},
    contributions=(ExtensionContribution(WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution(
        id="rust", title="Rust", detect=detect, marker_files=("Cargo.toml",),
        runtime_extensions=("toolchain:rust",),
    )),),
)

__all__ = ["detect", "plugin_pack"]
