from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT_UI = ROOT / "src/cyrene/workbench/webui/frontend/features/chat"


def test_file_and_terminal_rows_share_the_resource_row_component() -> None:
    resource_list = (CHAT_UI / "resource-list.jsx").read_text(encoding="utf-8")
    context_panel = (CHAT_UI / "context-panel.jsx").read_text(encoding="utf-8")
    split_pane = (CHAT_UI / "split-pane.jsx").read_text(encoding="utf-8")

    assert 'function WbcResourceListRow({' in resource_list
    assert 'className={"wbc-artifact-list-row"' in resource_list
    assert '<WbcResourceListRow' in context_panel
    assert '<WbcResourceListRow' in split_pane
    assert 'icon={WBC_ICONS.file}' in context_panel
    assert 'icon={WBC_ICONS.slash}' in split_pane


def test_terminal_row_keeps_status_without_its_old_parallel_layout() -> None:
    split_pane = (CHAT_UI / "split-pane.jsx").read_text(encoding="utf-8")
    viewer_css = (CHAT_UI / "viewer.css").read_text(encoding="utf-8")

    assert 'iconAdornment={<i className={running ? "running" : "exited"} />}' in split_pane
    assert 'aria-label={title + " — " + statusLabel}' in split_pane
    assert 'wbc-side-terminal-status' not in split_pane
    assert 'grid-template-columns: 8px minmax(0, 1fr) auto 16px' not in viewer_css
    assert '<time>{wbcFormatTime(terminal.updatedAt || terminal.createdAt)}</time>' not in split_pane
