from copy import deepcopy
from pathlib import Path

import pytest

from cyrene.workbench.projects import project_repository, project_runtime


@pytest.mark.parametrize("workspace", [
    "/workspace/.cyrene-desktop/workspace",
    "/Users/test/Cyrene/workspace",
    "/Users/test/custom-install/workspace",
])
def test_default_project_has_consistent_name_without_changing_path(monkeypatch, workspace):
    monkeypatch.setattr(project_runtime, "WORKSPACE_DIR", Path(workspace))
    monkeypatch.setattr(project_runtime, "_get_model", lambda: "test-model")
    project = project_runtime._workbench_default_project()["projects"][0]
    assert project["name"] == "Cyrene"
    assert project["workspacePath"] == workspace


def test_legacy_android_label_is_normalized_on_read_without_changing_identity(monkeypatch):
    monkeypatch.setattr(project_runtime, "_get_model", lambda: "test-model")
    project = project_runtime._workbench_default_project()["projects"][0]
    project.update(name=".cyrene-desktop", workspacePath="/workspace/.cyrene-desktop/workspace")
    before = deepcopy(project)
    payload = {"projects": [project], "activeProjectId": project["id"]}
    monkeypatch.setattr(project_repository, "read_document", lambda *args: deepcopy(payload))
    result = project_repository.read_workbench_store()
    repaired = result["projects"][0]
    assert repaired["name"] == "Cyrene"
    assert result["activeProjectId"] == before["id"]
    for key, value in before.items():
        if key != "name":
            assert repaired[key] == value
    assert not project_runtime._workbench_normalize_default_project_name(repaired)


@pytest.mark.parametrize("changes", [
    {"name": "我的项目"},
    {"workspacePath": "/home/test/.cyrene-desktop/workspace"},
    {"workspacePathSource": "generated"},
])
def test_custom_projects_are_not_renamed(changes):
    project = {
        "name": ".cyrene-desktop",
        "workspacePath": "/workspace/.cyrene-desktop/workspace",
        "workspacePathSource": "user",
        **changes,
    }
    before = deepcopy(project)
    assert not project_runtime._workbench_normalize_default_project_name(project)
    assert project == before
