from pathlib import Path

import pytest

from cyrene.workbench.runtime import (
    _workbench_artifact_download_target,
    _workbench_compose_static_system,
)


def _session(path: str, artifact_type: str = "file_change") -> dict:
    return {
        "artifacts": [
            {
                "id": "artifact_demo",
                "type": artifact_type,
                "name": Path(path).name,
                "path": path,
            }
        ]
    }


def test_artifact_download_target_resolves_registered_workspace_file(tmp_path):
    target = tmp_path / "deliverables" / "report.md"
    target.parent.mkdir()
    target.write_text("# report", encoding="utf-8")

    artifact, resolved = _workbench_artifact_download_target(
        {"workspacePath": str(tmp_path)},
        _session("deliverables/report.md"),
        "artifact_demo",
    )

    assert artifact["name"] == "report.md"
    assert resolved == target


def test_artifact_download_rebases_generated_project_workspace(
    monkeypatch, tmp_path
):
    from cyrene.workbench import runtime

    app_workspace = tmp_path / "current" / "workspace"
    target = (
        app_workspace
        / "projects"
        / "project_demo"
        / "deliverables"
        / "report.md"
    )
    target.parent.mkdir(parents=True)
    target.write_text("# restored report", encoding="utf-8")
    monkeypatch.setattr(runtime, "WORKSPACE_DIR", app_workspace)

    project = {
        "id": "project_demo",
        "dataKey": "project_demo",
        "workspacePathSource": "generated",
        "workspacePath": (
            "/Users/old/Library/Application Support/Cyrene/"
            "workspace/projects/project_demo"
        ),
    }
    _, resolved = runtime._workbench_artifact_download_target(
        project,
        _session("deliverables/report.md"),
        "artifact_demo",
    )

    assert resolved == target


def test_artifact_download_target_rejects_paths_outside_workspace(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the workspace"):
        _workbench_artifact_download_target(
            {"workspacePath": str(tmp_path)},
            _session(str(outside)),
            "artifact_demo",
        )


def test_artifact_download_target_rejects_missing_or_non_file_artifacts(tmp_path):
    with pytest.raises(LookupError, match="artifact not found"):
        _workbench_artifact_download_target(
            {"workspacePath": str(tmp_path)},
            _session("missing.md"),
            "artifact_other",
        )

    with pytest.raises(ValueError, match="not a downloadable file"):
        _workbench_artifact_download_target(
            {"workspacePath": str(tmp_path)},
            _session("summary.md", artifact_type="summary"),
            "artifact_demo",
        )

    with pytest.raises(FileNotFoundError, match="artifact file not found"):
        _workbench_artifact_download_target(
            {"workspacePath": str(tmp_path)},
            _session("missing.md"),
            "artifact_demo",
        )


def test_static_system_tells_agent_to_declare_shell_artifacts():
    # Files produced only through Bash/shell are caught as a weak git diff and do
    # not auto-promote to the downloadable 产物 panel. The per-run system block
    # must instruct the agent to declare such deliverables via send_file, or a
    # shell-produced file silently never reaches the user. This stable rule
    # belongs in the cacheable static system block.
    text = _workbench_compose_static_system(
        {"id": "proj_demo", "name": "Demo"},
        {"id": "sess_demo", "goal": "produce a report"},
    )

    assert "产物" in text
    assert "send_file" in text
    assert "Bash" in text
