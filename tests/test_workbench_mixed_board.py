from conftest import workbench_chat_source
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_mixed_board_keeps_user_placement_until_task_status_changes():
    source = (ROOT / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    helpers = source.split("var WB_TASK_BOARD_COLUMNS = [", 1)[1].split(
        "function wbChatBoardTone", 1
    )[0]
    script = f"""
var WB_TASK_BOARD_COLUMNS = [{helpers}
const cards = [
  {{key: "task:t1", kind: "task", item: {{id: "t1", status: "idle"}}}},
  {{key: "chat:c1", kind: "chat", item: {{id: "c1", status: "completed"}}}}
];
const saved = {{
  placements: {{
    "task:t1": {{column: "blocked", statusBasis: "idle"}},
    "chat:c1": {{column: "review", statusBasis: "user"}}
  }},
  columns: {{planning: [], executing: [], review: ["chat:c1"], completed: [], blocked: ["task:t1"]}}
}};
const stable = wbNormalizeMixedBoardLayout(saved, cards);
const changed = wbNormalizeMixedBoardLayout(stable, [
  {{key: "task:t1", kind: "task", item: {{id: "t1", status: "running"}}}},
  cards[1]
]);
process.stdout.write(JSON.stringify({{stable, changed}}));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    result = json.loads(completed.stdout)
    assert result["stable"]["placements"]["task:t1"]["column"] == "blocked"
    assert result["stable"]["placements"]["chat:c1"]["column"] == "review"
    assert result["changed"]["placements"]["task:t1"]["column"] == "executing"
    assert result["changed"]["placements"]["chat:c1"]["column"] == "review"


def test_work_menu_task_split_and_project_tools_expand_existing_surfaces_inline():
    workbench = (ROOT / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    chat = workbench_chat_source()
    css = (ROOT / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    dock = workbench.split("function WorkbenchSidebarDock", 1)[1].split(
        "var WB_TASK_BOARD_COLUMNS", 1
    )[0]
    assert 'id: "board"' in dock
    assert 'id: "work"' in dock
    assert "WorkbenchModuleAccount" not in dock

    assert 'var WBC_TASK_DRAG_MIME = "application/x-cyrene-task+json";' in chat
    assert 'openPaneContent("task", String(taskPayload.id), { side: "right" })' in chat
    assert 'if (activeChatIdRef.current) openChatSplit(droppedChatId);' in chat
    assert 'else openPaneContent("chat", droppedChatId, { side: "right" });' in chat
    assert 'var activeTaskWorkspaceRef = useWbcRef("");' in chat
    assert "function openTaskWorkspace(taskId)" in chat
    assert 'ownerChatId: projectPaneOwnerKey()' in chat
    assert 'var [railSelectionSuppressed, setRailSelectionSuppressed] = useWbcState(false);' in chat
    assert 'activeChatId={railSelectionSuppressed ? "" : activeChatId}' in chat
    assert 'activeTaskId={railSelectionSuppressed ? "" : activeTaskId}' in chat
    rail_mode_change = chat.split('onRailModeChange={function (mode) {', 1)[1].split(
        "        }}", 1
    )[0]
    assert "setRailSelectionSuppressed(true);" in rail_mode_change
    assert "setRailMode(mode);" in rail_mode_change
    assert "selectChat(" not in rail_mode_change
    assert "openTaskWorkspace(" not in rail_mode_change
    assert "restoreTerminalReplacement(" not in rail_mode_change
    assert 'activeTaskWorkspaceRef.current = "";' in chat
    assert 'if (!selectId && activeTaskWorkspaceRef.current) return list;' in chat
    assert 'card.kind === "task"' in chat
    assert 'className={"wbc-project-tools"' in chat
    assert '" has-expanded-project-tool"' in chat
    assert 'aria-controls="wbc-project-file-list"' in chat
    assert 'aria-controls="wbc-project-terminal-list"' in chat
    assert "setFileToolsExpanded" in chat
    assert "setTerminalToolsExpanded" in chat
    assert 'className="wbc-project-tool-inline-header is-file"' in chat
    assert 'className="wbc-project-tool-inline-header is-terminal"' in chat
    assert 'wbcT("rail.searchEverything", "Search chats, tasks, files, and terminals")' in chat
    assert 'className={"wbc-chat-list workbench-integrated-rail-body wbc-unified-search-results"' in chat
    assert '"/files?query=" + encodeURIComponent(search)' in chat
    assert 'placeholder={wbcT("rail.searchFiles"' not in chat
    assert 'placeholder={wbcT("terminal.search"' not in chat
    assert 'className="wbc-project-file-list"' in chat
    assert 'className={"wbc-project-terminal-list"' in chat
    assert "function handleProjectToolWheel(event)" in chat
    assert "pull.wheelDistance >= 72" in chat
    assert "function handleProjectToolTouchMove(event)" in chat
    assert "distanceY >= 56 && distanceY > distanceX" in chat
    assert "onWheel={handleProjectToolWheel}" in chat
    assert "onTouchMove={handleProjectToolTouchMove}" in chat
    assert 'className="wbc-project-tool-directory-control"' in chat
    assert 'filePath === "." ? (' in chat
    assert '>{WBC_ICONS.chevronLeft}</button>' in chat
    assert 'className={"workbench-project-files-path"' not in chat
    assert '.wbc-project-tool-inline-header > button.wbc-project-tool-directory-control' in css
    assert 'openToolRail("files")' not in chat
    assert 'openToolRail("terminal")' not in chat
    assert ".wbc-task-split," in css
    assert ".wbc-project-tools" in css
    assert ".wbc-project-tool-expand.is-expanded" in css
    assert "grid-template-rows: 0fr;" in css
    assert ".wbc-project-tools.has-expanded-tool" in css
    assert ".wbc-project-tools:not(.has-expanded-tool)" in css
    assert ".wbc-project-tool-expand + button" in css
    project_tool_icon = css.split(".wbc-project-tool-icon {", 1)[1].split("}", 1)[0]
    assert "var(--wb-muted) 10%" in project_tool_icon
    assert "var(--wb-accent)" not in project_tool_icon
    assert ".wbc-rail.has-expanded-project-tool > .wbc-chat-list" in css
    assert "flex: 0 0 0%;" in css
    assert "grid-template-rows: auto auto minmax(0, 1fr);" in css
    assert ".wbc-project-terminal-list .wbc-chat-card {" not in css
    assert "React.createElement(chatModule.Rail" in workbench
    assert "Rail: WbcProjectRail" in chat
    shared_rail_host = chat.split("function WbcProjectRail(props)", 1)[1].split(
        "// ---------------------------------------------------------------------------\n// Conversation main",
        1,
    )[0]
    assert "return <WbcRail" in shared_rail_host
    assert "terminals={terminals}" in shared_rail_host
    assert 'onRailModeChange: setProjectRailMode' in workbench
    assert 'onOpenFile: function (entry) { openProjectRailResource("file", entry); }' in workbench
    assert 'onOpenTerminal: function (terminalId) { openProjectRailResource("terminal", terminalId); }' in workbench
    task_visual = chat.split("function taskRailVisualState(task)", 1)[1].split(
        "function storeTaskOrder", 1
    )[0]
    assert 'var planning = raw === "planning";' in task_visual
    assert 'planning ? WBC_ICONS.planning : WBC_ICONS.file' in task_visual
    assert 'planning ? " status-planning"' in task_visual
    assert 'planning: <svg viewBox="0 0 24 24"' in chat
    assert ".wbc-rail .wbc-chat-card.status-planning .wbc-chat-row-icon {" in css


def test_persistent_dock_has_equal_visible_side_and_bottom_insets():
    css = (ROOT / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    dock_override = css.rsplit(".workbench-sidebar-dock.is-persistent {", 1)[1].split(
        "}", 1
    )[0]
    assert "bottom: 19px;" in dock_override


def test_task_workspace_reuses_right_panel_visibility_and_split_contracts():
    workbench = (ROOT / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    chat = workbench_chat_source()
    css = (ROOT / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    i18n = (ROOT / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    assert "TaskContextPanelComponent: RightContextPanel" in workbench
    assert 'isChat && !activeChatId && taskView === "detail" && store.activeSessionId' in workbench
    assert "onRightTab={openRightTab}" in workbench
    assert 'window.addEventListener("cyrene:open-task-context-panel"' in workbench
    assert 'className="wbc-task-pane-floating-panel"' in workbench
    assert "onToggleSide={function () { setFloatingPanelOpen(false); }}" in workbench

    assert "var projectTaskPanelCard = paneCardCount === 1" in chat
    assert 'className="wbc-task-context-panel"' in chat
    assert 'projectTaskPanelCard && !sideVisible\n          ? { "--wbc-side-track-width": "0px" }' in chat
    assert "splitDetailOpen && !projectPaneOnly && !singleColumnWorkspaceOpen && !projectTaskPanelCard" in chat
    assert "var WB_RIGHT_MIN = 280;" in workbench
    assert "Object.prototype.hasOwnProperty.call(taskRightTabs, projectTaskPanelId)" in chat
    assert "? taskRightTabs[projectTaskPanelId]" in chat
    assert "onToggleSide={onToggleSide}" in chat
    assert "split={!singlePane}" in chat
    assert 'openPanelLabel={wbcT("task.side.openDetailPanel", "Open task details")}' in chat
    assert i18n.count('"task.side.openDetailPanel"') == 2
    assert '"task.side.openDetailPanel": "打开任务详情"' in i18n

    assert ".wbc-page.wbc-project-pane-only.wbc-project-task-pane {" in css
    assert "--wbc-docked-side-open-width: var(--wb-right-w, 350px);" in css
    assert ".wbc-page > :is(.wbc-side, .wbc-task-context-panel) {" in css
    assert ".wbc-page.wbc-side-hidden > :is(.wbc-side, .wbc-task-context-panel) {" in css
    assert ".wbc-task-pane-floating-panel {" in css

    hidden_task_workspace = css.split(
        ".wbc-page.wbc-side-hidden.wbc-project-pane-only.wbc-project-task-pane > .wbc-pane-layout.single {",
        1,
    )[1].split("}", 1)[0]
    assert "grid-column: 2 / 4;" in hidden_task_workspace
    assert "padding-right: var(--wbc-card-gutter);" in hidden_task_workspace
