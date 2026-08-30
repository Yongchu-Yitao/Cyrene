"""GitHub repository project support Plugin."""

from pathlib import Path

from cyrene.core.plugin import ExtensionContribution, PluginPack
from cyrene.plugins import WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution


def detect(_workspace: Path, _current_path: str):
    # Review and Git diff are supplied by the shared workspace surface. This
    # project type owns GitHub-specific capability activation without adding a
    # misleading build/run action.
    return []


plugin_pack = PluginPack(
    id="cyrene_project_github",
    description="GitHub repository integration for workspace projects.",
    plugins=(),
    metadata={"default_enabled": False},
    contributions=(ExtensionContribution(WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution(
        id="github", title="GitHub", detect=detect,
        marker_files=(".git", ".github"), runtime_extensions=("cli:github-cli",),
    )),),
)

__all__ = ["detect", "plugin_pack"]
