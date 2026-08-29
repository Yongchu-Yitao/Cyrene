from pathlib import Path

import pytest

from cyrene.workbench.artifacts.artifact_runtime import _workbench_artifact_download_target


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


def test_artifact_download_prefers_pinned_webui_exports_copy(monkeypatch, tmp_path):
    from cyrene.workbench.artifacts import artifact_runtime as runtime

    exports = tmp_path / "webui_exports"
    exports.mkdir()
    exported = exports / "report_a1b2c3d4e5.md"
    exported.write_text("# pinned copy", encoding="utf-8")
    monkeypatch.setattr(runtime, "_EXPORTS_DIR", exports)

    # Source file is gone — only the pinned webui_exports copy can serve it.
    session = {
        "artifacts": [{
            "id": "artifact_demo",
            "type": "file_change",
            "name": "report.md",
            "path": "report.md",
            "attachment": {
                "id": "report_a1b2c3d4e5.md",
                "name": "report.md",
                "url": "/api/chat/export/report_a1b2c3d4e5.md",
            },
        }]
    }
    _, resolved = _workbench_artifact_download_target(
        {"workspacePath": str(tmp_path)},
        session,
        "artifact_demo",
    )

    assert resolved == exported


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


def test_artifact_download_raises_when_no_copy_exists(monkeypatch, tmp_path):
    from cyrene.workbench.artifacts import artifact_runtime as runtime

    exports = tmp_path / "webui_exports"
    exports.mkdir()
    monkeypatch.setattr(runtime, "_EXPORTS_DIR", exports)

    with pytest.raises(FileNotFoundError):
        _workbench_artifact_download_target(
            {"workspacePath": str(tmp_path)},
            _session("deliverables/report.html"),
            "artifact_demo",
        )

def test_artifact_download_rebases_generated_project_workspace(
    monkeypatch, tmp_path
):
    from cyrene.workbench.artifacts import artifact_runtime as runtime

    app_workspace = tmp_path / "current" / "workspace"
    # Generated project workspaces live under .cyrene/projects.
    target = (
        app_workspace
        / ".cyrene"
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
            "workspace/.cyrene/projects/project_demo"
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
