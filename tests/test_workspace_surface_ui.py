from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_SURFACE = (
    ROOT
    / "src"
    / "cyrene"
    / "workbench"
    / "webui"
    / "frontend"
    / "features"
    / "chat"
    / "workspace-surface.jsx"
)
WORKSPACE_STYLES = WORKSPACE_SURFACE.with_name("workspace.css")
WORKBENCH_STYLES = WORKSPACE_SURFACE.parents[2] / "workbench.css"
RUNTIME_WORKBENCH_STYLES = (
    WORKSPACE_SURFACE.parents[3] / "static" / "app" / "workbench.css"
)
FEEDBACK_SERVICE = WORKSPACE_SURFACE.parents[2] / "shared" / "feedback" / "service.jsx"
PROJECT_FILES = WORKSPACE_SURFACE.with_name("project-files.jsx")
PROJECT_RAIL = WORKSPACE_SURFACE.with_name("rail.jsx")


def test_workspace_surface_reserves_the_host_grip_and_uses_compact_chrome() -> None:
    source = WORKSPACE_SURFACE.read_text(encoding="utf-8")
    styles = WORKSPACE_STYLES.read_text(encoding="utf-8")

    assert 'className="wbc-workspace-chrome"' in source
    assert 'className="wbc-workspace-action-picker"' in source
    grip_rule = styles.split(
        ".wbc-pane-card > .wbc-workspace-surface {", 1
    )[1].split("}", 1)[0]
    assert "padding-top: 34px" in grip_rule
    assert "grid-template-rows: auto minmax(0, 1fr) auto" in styles


def test_workspace_toolbar_uses_one_run_action_without_a_restart_button() -> None:
    source = WORKSPACE_SURFACE.read_text(encoding="utf-8")

    assert 'wbcT("workspace.run", "Run")' in source
    assert 'wbcT("workspace.restart", "Restart")' not in source
    assert 'executionCommand("restart")' not in source


def test_workspace_errors_use_the_shared_bottom_right_toast() -> None:
    source = WORKSPACE_SURFACE.read_text(encoding="utf-8")
    workspace_styles = WORKSPACE_STYLES.read_text(encoding="utf-8")
    workbench_styles = WORKBENCH_STYLES.read_text(encoding="utf-8")
    runtime_workbench_styles = RUNTIME_WORKBENCH_STYLES.read_text(encoding="utf-8")
    feedback_service = FEEDBACK_SERVICE.read_text(encoding="utf-8")
    toast_host = workbench_styles.split(".workbench-toast-host {", 1)[1].split("}", 1)[0]

    assert 'feedback.showToast(message, "error", { key: feedbackKey })' in source
    assert 'className="wbc-workspace-error"' not in source
    assert ".wbc-workspace-error" not in workspace_styles
    assert runtime_workbench_styles == workbench_styles
    assert workbench_styles.count(".workbench-toast-host {") == 1
    assert feedback_service.count('className="workbench-toast-host"') == 1
    assert "right: max(18px, env(safe-area-inset-right))" in toast_host
    assert "bottom: max(18px, env(safe-area-inset-bottom))" in toast_host
    assert "left:" not in toast_host


def test_workspace_sections_share_the_pane_background_instead_of_color_bands() -> None:
    styles = WORKSPACE_STYLES.read_text(encoding="utf-8")

    for selector in (
        ".wbc-workspace-surface {",
        ".wbc-workspace-chrome {",
        ".wbc-workspace-review {",
        ".wbc-workspace-review-overview {",
    ):
        rule = styles.split(selector, 1)[1].split("}", 1)[0]
        assert "background: transparent" in rule


def test_workspace_tabs_only_exist_when_their_content_is_available() -> None:
    source = WORKSPACE_SURFACE.read_text(encoding="utf-8")

    assert 'if (selectedFile) tabs.push({ id: "editor"' in source
    assert 'if (execution && execution.terminalId) tabs.push({ id: "terminal"' in source
    assert 'if (diagnostics.length) tabs.push({ id: "problems"' in source
    assert 'if (hasReview) tabs.push({ id: "review"' in source
    assert 'if (hasPreview) tabs.push({ id: "preview"' in source
    assert 'tabs.push({ id: "files"' in source
    assert 'tabs.some(function (item) { return item.id === tab })' in source
    assert 'if (tab !== visibleTab) setTab(visibleTab)' in source
    assert '{visibleTab === "preview" ? <WorkspacePreview' in source
    assert source.index('className="wbc-workspace-content"') < source.index('className="wbc-workspace-tabs"')


def test_workspace_review_reuses_the_existing_change_diff_viewer() -> None:
    source = WORKSPACE_SURFACE.read_text(encoding="utf-8")
    styles = WORKSPACE_STYLES.read_text(encoding="utf-8")

    assert "function workspaceGitDiffFiles" in source
    assert "function WorkspaceDiff" in source
    assert "workbenchServices.diff().Panel" in source
    assert 'className="wbc-change-split-diff wbc-change-diff wbc-workspace-diff-view"' in source
    assert 'hideHeader: true, hideHunkHeaders: true' in source
    assert "function workspaceDiffRows" not in source
    assert "git.hasChanges ? \"●\"" not in source
    assert "<pre><code>{diff}</code></pre>" not in source
    assert ".wbc-workspace-diff-row" not in styles
    assert ".wbc-workspace-diff-code" not in styles
    diff_rule = styles.split(".wbc-workspace-diff-view {", 1)[1].split("}", 1)[0]
    assert "border: 0" in diff_rule
    assert "border-radius: 0" in diff_rule


def test_workspace_and_project_rail_share_the_project_file_row() -> None:
    workspace = WORKSPACE_SURFACE.read_text(encoding="utf-8")
    rail = PROJECT_RAIL.read_text(encoding="utf-8")
    project_files = PROJECT_FILES.read_text(encoding="utf-8")

    assert 'import { WbcProjectFileHeader, WbcProjectFileRow, useWbcProjectFiles } from "./project-files.jsx"' in workspace
    assert 'import { WbcProjectFileHeader, WbcProjectFileRow, useWbcProjectFiles } from "./project-files.jsx"' in rail
    assert "<WbcProjectFileHeader" in workspace
    assert "<WbcProjectFileHeader" in rail
    assert "function WbcProjectFileHeader" in project_files
    assert "<WbcProjectFileRow" in workspace
    assert "<WbcProjectFileRow" in rail
    assert "wbcProjectFileVisual(entry)" in project_files
    assert 'className="workbench-project-file-icon"' in project_files
    assert 'className="workbench-project-file-chevron"' in project_files
    assert ".wbc-project-file-header.is-workspace" in WORKSPACE_STYLES.read_text(encoding="utf-8")


def test_workspace_and_project_rail_share_automatic_file_refresh() -> None:
    workspace = WORKSPACE_SURFACE.read_text(encoding="utf-8")
    rail = PROJECT_RAIL.read_text(encoding="utf-8")
    project_files = PROJECT_FILES.read_text(encoding="utf-8")

    assert "useWbcProjectFiles({" in workspace
    assert "useWbcProjectFiles({" in rail
    assert 'window.addEventListener("cyrene:workspace-file-changed"' in project_files
    workspace_files = workspace.split("function WorkspaceFiles", 1)[1].split("function WorkspaceProblems", 1)[0]
    assert 'aria-label={wbcT("common.refresh", "Refresh")}' not in workspace_files


def test_workspace_review_uses_one_source_picker_and_opens_the_diff_directly() -> None:
    source = WORKSPACE_SURFACE.read_text(encoding="utf-8")
    styles = WORKSPACE_STYLES.read_text(encoding="utf-8")
    tab_rule = styles.split(".wbc-workspace-tabs button {", 1)[1].split("}", 1)[0]
    active_rule = styles.split(".wbc-workspace-tabs button.active {", 1)[1].split("}", 1)[0]

    assert 'className="wbc-workspace-review-source"' in source
    assert 'className="wbc-workspace-review-file"' in source
    assert '<header className="wbc-workspace-review-overview">' in source
    assert '<footer className="wbc-workspace-review-file">' in source
    assert source.index('<header className="wbc-workspace-review-overview">') < source.index('className="wbc-workspace-review-body"')
    assert source.index('<WorkspaceDiff diff={diff}') < source.index('<footer className="wbc-workspace-review-file">')
    assert "wbc-workspace-review-switch" not in source
    assert "WorkspaceGitSummary" not in source
    assert "openSnapshot" not in source
    assert "sourceFiles.length > 1" in source
    assert "<WorkspaceDiff diff={diff}" in source
    assert "border-radius: 10px" in tab_rule
    assert "var(--wb-accent)" in active_rule
    assert "background:" in active_rule
    assert "wbc-workspace-review-overview" in styles
    assert "wbc-workspace-review-file-stats" in styles
