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


def test_artifact_download_prefers_pinned_webui_exports_copy(monkeypatch, tmp_path):
    from cyrene.workbench import runtime

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


def test_artifact_download_falls_back_to_exported_copy_when_source_missing(
    monkeypatch, tmp_path
):
    from cyrene.workbench import runtime

    exports = tmp_path / "webui_exports"
    exports.mkdir()
    exported = exports / "report_a1b2c3d4e5.html"
    exported.write_text("<h1>old deliverable</h1>", encoding="utf-8")
    monkeypatch.setattr(runtime, "_EXPORTS_DIR", exports)

    # No workspace source after a cross-machine restore and no pinned
    # attachment id — only the durable webui_exports copy survives.
    artifact, resolved = _workbench_artifact_download_target(
        {"workspacePath": str(tmp_path)},
        _session("deliverables/report.html"),
        "artifact_demo",
    )

    assert artifact["name"] == "report.html"
    assert resolved == exported


def test_artifact_download_raises_when_no_copy_exists(monkeypatch, tmp_path):
    from cyrene.workbench import runtime

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
    from cyrene.workbench import runtime

    app_workspace = tmp_path / "current" / "workspace"
    # Generated project workspaces now live under .cyrene/projects; a legacy
    # artifact path (deliverables/report.md) resolves relative to that root.
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


def test_workspace_root_rebases_legacy_project_without_source(monkeypatch, tmp_path):
    from cyrene.workbench import runtime

    app_workspace = tmp_path / "current" / "workspace"
    monkeypatch.setattr(runtime, "WORKSPACE_DIR", app_workspace)

    # Pre-workspacePathSource project: the stored path still points at the
    # pre-migration location workspace/projects/<id>.
    project = {
        "id": "project_demo",
        "dataKey": "project_demo",
        "workspacePath": str(app_workspace / "projects" / "project_demo"),
    }
    resolved = runtime._workbench_workspace_root(project)

    assert resolved == (
        app_workspace / ".cyrene" / "projects" / "project_demo"
    ).resolve()


def test_resolve_workspace_dir_rebases_without_recreating_legacy_dir(
    monkeypatch, tmp_path
):
    from cyrene.workbench import runtime

    app_workspace = tmp_path / "current" / "workspace"
    monkeypatch.setattr(runtime, "WORKSPACE_DIR", app_workspace)

    project = {
        "id": "project_demo",
        "dataKey": "project_demo",
        "workspacePath": str(app_workspace / "projects" / "project_demo"),
    }
    resolved = runtime._workbench_resolve_workspace_dir(project)
    expected = (app_workspace / ".cyrene" / "projects" / "project_demo").resolve()

    assert resolved == str(expected)
    assert expected.is_dir()
    # The agent must not recreate an empty directory at the old location.
    assert not (app_workspace / "projects" / "project_demo").exists()


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
