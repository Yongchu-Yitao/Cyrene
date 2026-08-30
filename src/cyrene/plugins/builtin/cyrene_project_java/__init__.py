"""Java, Maven, and Gradle project support Plugin."""

from pathlib import Path

from cyrene.core.plugin import ExtensionContribution, PluginPack
from cyrene.plugins import WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution
from cyrene.plugins.project_types import nearest_scope, relative_scope, scope_id, workspace_action


def detect(workspace: Path, current_path: str):
    scope = nearest_scope(workspace, current_path, "pom.xml", "build.gradle", "build.gradle.kts")
    if scope is None:
        return []
    cwd = relative_scope(workspace, scope)
    suffix = scope_id(cwd)
    if (scope / "pom.xml").exists():
        program = "./mvnw" if (scope / "mvnw").is_file() else "mvn"
        return [
            workspace_action(f"java.build.{suffix}", "Build Maven project", "build", program, ["package"], cwd=cwd),
            workspace_action(f"java.test.{suffix}", "Test Maven project", "test", program, ["test"], cwd=cwd),
        ]
    program = "./gradlew" if (scope / "gradlew").is_file() else "gradle"
    return [
        workspace_action(f"java.build.{suffix}", "Build Gradle project", "build", program, ["build"], cwd=cwd),
        workspace_action(f"java.test.{suffix}", "Test Gradle project", "test", program, ["test"], cwd=cwd),
    ]


plugin_pack = PluginPack(
    id="cyrene_project_java",
    description="Java project detection and Maven or Gradle workspace actions.",
    plugins=(),
    metadata={"default_enabled": False},
    contributions=(ExtensionContribution(WORKSPACE_PROJECT_TYPE, WorkspaceProjectTypeContribution(
        id="java", title="Java", detect=detect,
        marker_files=("pom.xml", "build.gradle", "build.gradle.kts"),
        runtime_extensions=("toolchain:java",),
    )),),
)

__all__ = ["detect", "plugin_pack"]
