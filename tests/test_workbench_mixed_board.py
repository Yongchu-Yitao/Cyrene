from conftest import (
    frontend_module_source,
    workbench_chat_source,
    workbench_shell_source,
    workbench_style_source,
)
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_conversation_board_starts_empty_and_only_tracks_explicit_placements():
    source = workbench_shell_source()
    helpers = source.split("var WB_CONVERSATION_BOARD_COLUMNS = [", 1)[1].split(
        "function wbReadConversationDrag", 1
    )[0]
    script = f"""
var WB_CONVERSATION_BOARD_COLUMNS = [{helpers}
const empty = wbNormalizeConversationBoardLayout(null);
const added = wbPlaceConversationBoardCard(empty, "chat-1", "review", "", "after");
const moved = wbPlaceConversationBoardCard(added, "chat-1", "completed", "", "after");
const removed = wbRemoveConversationBoardCard(moved, "chat-1");
process.stdout.write(JSON.stringify({{empty, added, moved, removed}}));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    result = json.loads(completed.stdout)
    assert result["empty"]["placements"] == {}
    assert all(not ids for ids in result["empty"]["columns"].values())
    assert result["added"]["columns"]["review"] == ["chat-1"]
    assert result["moved"]["columns"]["review"] == []
    assert result["moved"]["columns"]["completed"] == ["chat-1"]
    assert result["removed"]["placements"] == {}


def test_persistent_dock_has_equal_visible_side_and_bottom_insets():
    css = workbench_style_source()

    dock_override = css.rsplit(".workbench-sidebar-dock.is-persistent {", 1)[1].split(
        "}", 1
    )[0]
    assert "bottom: 19px;" in dock_override


def test_board_rail_open_actions_activate_work_before_opening_content():
    shell = workbench_shell_source()
    chat = workbench_chat_source()

    assert 'onActivateWorkspace: function () { context.navigation.openPage("work"); }' in shell
    assert "workspaceContent, onActivateWorkspace, newChatRequestId" in chat
    assert "function activateWorkspace()" in chat
    assert "if (onActivateWorkspace) onActivateWorkspace();" in chat

    rail = chat.split("<WbcRail", 1)[1].split("/>\n      {workspaceContent", 1)[0]
    assert "onSelect={function (chatId) { activateWorkspace(); return selectChat(chatId); }}" in rail
    assert "onCreate={function () { activateWorkspace(); return handleCreateChat(); }}" in rail
    assert "onOpenFile={function (entry) { activateWorkspace(); return openProjectFile(entry); }}" in rail
    assert "onOpenTerminal={function (terminalId, side) { activateWorkspace(); return openTerminal(terminalId, side); }}" in rail
    assert "onCreateTerminal={function () { activateWorkspace(); return createTerminal(); }}" in rail
    plugin_open = rail.split("onOpenPluginView={function", 1)[1].split("onOpenSplit={function", 1)[0]
    assert plugin_open.index("activateWorkspace();") < plugin_open.index('openPaneContent("plugin-view"')
    assert 'activateWorkspace();\n          return openPaneContent("chat"' in rail


def test_conversation_board_uses_previous_card_geometry_without_top_hint():
    board = frontend_module_source("features/chat/conversation-board.jsx")
    css = frontend_module_source("features/chat/chat.css")

    assert '<p>{t("conversationBoard.subtitle")}</p>' not in board
    columns = css.split(
        ".wbc-page.wbc-external-workspace .wb-board-columns {", 1
    )[1].split("}", 1)[0]
    assert "min-width: calc(1748px + var(--wb-conversation-board-rail-reserve)" in columns
    assert "grid-template-columns: repeat(5, minmax(340px, 1fr));" in columns
    assert "padding-left: calc(var(--wb-conversation-board-rail-reserve)" in columns
    card = css.split(
        ".wbc-page.wbc-external-workspace .wb-board-card {", 1
    )[1].split("}", 1)[0]
    assert "width: 100%;" in card
    assert "height: calc(140px * var(--wb-ui-font-scale, 1));" in card

    host = css.split(
        ".wbc-page.wbc-external-workspace > .wbc-external-workspace-host {", 1
    )[1].split("}", 1)[0]
    assert "grid-column: 1 / 5;" in host
    assert "z-index: 0;" in host
    board_canvas = css.split(
        ".wbc-page.wbc-external-workspace .workbench-conversation-board {", 1
    )[1].split("}", 1)[0]
    assert "padding-top: calc(var(--wbc-card-top-inset) + 24px);" in board_canvas
    rail = css.split(
        ".wbc-page.wbc-external-workspace > .wbc-rail {", 1
    )[1].split("}", 1)[0]
    assert "z-index: 30;" in rail
    board_surface = css.split(
        ".wbc-page.wbc-external-workspace {", 1
    )[1].split("}", 1)[0]
    assert "--wb-floating-rail-bg:" not in board_surface
    assert "--wbc-panel-surface:" not in board_surface
    assert 'html[data-theme="dark"] .wbc-page.wbc-external-workspace {' not in css
    glass = css.split(
        ".workbench-grid.integrated-sidebars .wbc-page.wbc-external-workspace > .wbc-rail.workbench-integrated-rail {", 1
    )[1].split("}", 1)[0]
    assert "background: var(--wb-composer-surface, color-mix(in srgb, #fff 76%, transparent));" in glass
    assert "backdrop-filter: var(--wbc-composer-glass-filter);" in glass
