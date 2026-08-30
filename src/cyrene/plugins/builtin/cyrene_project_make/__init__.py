"""Make project support Plugin."""

import re
from pathlib import Path

from cyrene.core.plugin import ExtensionContribution, PluginPack
from cyrene.plugins import WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution
from cyrene.plugins.project_types import nearest_scope, relative_scope, scope_id, workspace_action


def detect(workspace: Path, current_path: str):
    scope = nearest_scope(workspace, current_path, "Makefile", "makefile")
    if scope is None:
        return []
    makefile = scope / ("Makefile" if (scope / "Makefile").exists() else "makefile")
    try:
        text = makefile.read_text(encoding="utf-8")
    except OSError:
        return []
    targets = {
        match.group(1)
        for match in re.finditer(r"^([A-Za-z0-9_.-]+)\s*:(?![=])", text, re.MULTILINE)
    }
    cwd = relative_scope(workspace, scope)
    suffix = scope_id(cwd)
    result = []
    for target, kind, label, zh_label in (
        ("build", "build", "Build with Make", "使用 Make 构建"),
        ("test", "test", "Test with Make", "使用 Make 测试"),
        ("run", "run", "Run with Make", "使用 Make 运行"),
    ):
        if target in targets:
            result.append(workspace_action(
                f"make.{target}.{suffix}", label, kind, "make", [target],
                cwd=cwd, long_running=target == "run",
                i18n={"zh": {"label": zh_label}},
            ))
    return result


plugin_pack = PluginPack(
    id="cyrene_project_make",
    description="Makefile project detection and workspace actions.",
    plugins=(),
    contributions=(ExtensionContribution(WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution(
        id="make", title="Make", detect=detect, marker_files=("Makefile", "makefile"),
    )),),
)

__all__ = ["detect", "plugin_pack"]
