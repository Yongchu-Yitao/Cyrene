import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_browser_tab_picker_floats_in_a_native_view_without_obscuring_the_page():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    main = (ROOT / "electron/main.js").read_text(encoding="utf-8")
    preload = (ROOT / "electron/preload.js").read_text(encoding="utf-8")
    package = (ROOT / "electron/package.json").read_text(encoding="utf-8")

    picker = chat.split("function setMaximizedBrowserPicker", 1)[1].split(
        "function selectMaximizedBrowserTab", 1
    )[0]
    split = chat.split("function WbcBrowserSplit(", 1)[1].split(
        "function WbcSubagentsSplitHost", 1
    )[0]

    assert "browserBridge.setTabPicker" in picker
    assert "workbench:browser-obscured" not in picker
    assert "screenshot" not in picker
    assert "bridge.setTabPicker" in split
    assert "workbench:browser-obscured" not in split
    assert "screenshot" not in split
    assert 'effectiveMode === "maximized" && !hasNativeTabPicker && maximizedPickerOpen' in chat
    assert "!hasNativeTabPicker && pickerOpen && <div" in split
    assert "const BROWSER_TAB_PICKER_HTML" in main
    native_view = main.split("  ensureTabPickerView() {", 1)[1].split(
        "pushTabPickerState()", 1
    )[0]
    assert "new WebContentsView" in native_view
    assert "parent.addChildView(view)" in main.split("syncTabPicker(", 1)[1]
    assert "this.syncTabPicker(win.contentView, true);" in main
    assert "setTabPicker: (info) => ipcRenderer.invoke('browser:set-tab-picker'" in preload
    assert '"browser-tab-picker-preload.js"' in package


def test_native_browser_tab_picker_has_motion_and_reduced_motion_support():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    main = (ROOT / "electron/main.js").read_text(encoding="utf-8")
    picker_html = main.split("const BROWSER_TAB_PICKER_HTML", 1)[1].split(
        "function normalizeBrowserSessionId", 1
    )[0]
    picker_bounds = main.split("tabPickerBounds()", 1)[1].split(
        "trackTabPickerWindow", 1
    )[0]

    assert "body.open #menu" in picker_html
    assert "animation: picker-in 220ms" in picker_html
    assert "animation: picker-out 150ms" in picker_html
    assert "transform: translate3d" in picker_html
    assert "prefers-reduced-motion: reduce" in picker_html
    assert "view.setBounds(bounds)" in main
    assert "const verticalLift = 60" in picker_bounds
    assert "surface.y - verticalLift" in picker_bounds
    assert "variant === 'maximized' ? 116 : 12" in picker_bounds
    assert "button:focus-visible { outline: none; }" in picker_html
    assert ".row:focus-within" in picker_html
    assert "var(--focus" not in picker_html
    assert 'focus: color("--wb-accent"' not in chat


def test_native_browser_tab_picker_title_clicks_are_debounced_in_both_hosts():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    maximized = chat.split("function WbcBrowserFloatingSurface(", 1)[1].split(
        "function wbcNavigationPreview", 1
    )[0]
    split = chat.split("function WbcBrowserSplit(", 1)[1].split(
        "function WbcSubagentsSplitHost", 1
    )[0]

    assert "WBC_BROWSER_TAB_PICKER_TOGGLE_DEBOUNCE_MS = 280" in chat
    assert "wbcBrowserTabPickerToggleIsDebounced" in chat
    assert "wbcBrowserTabPickerToggleIsDebounced(maximizedPickerToggleAtRef)" in maximized
    assert "wbcBrowserTabPickerToggleIsDebounced(browserPickerToggleAtRef)" in split


def test_native_browser_tab_picker_dismisses_and_syncs_actions_to_both_hosts():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    main = (ROOT / "electron/main.js").read_text(encoding="utf-8")
    picker_preload = (ROOT / "electron/browser-tab-picker-preload.js").read_text(
        encoding="utf-8"
    )
    picker_html = main.split("const BROWSER_TAB_PICKER_HTML", 1)[1].split(
        "function normalizeBrowserSessionId", 1
    )[0]
    split = chat.split("function WbcBrowserSplit(", 1)[1].split(
        "function WbcSubagentsSplitHost", 1
    )[0]

    assert "if (this.tabPickerState.visible) this.dismissTabPicker(true);" in main
    assert "this._tabPickerWindowBlurHandler" in main
    assert "String(input && input.key || '') === 'Escape'" in main
    assert "window.addEventListener('blur'" not in picker_html
    assert "browser-tab-picker:hidden-ready" in main
    assert "browser-tab-picker:action" in picker_preload
    assert "browser-tab-picker:hidden-ready" in picker_preload
    assert "browser-tab-picker:ready" in picker_preload
    assert "browserBridge.onTabPickerAction" in chat
    assert "bridge.onTabPickerAction" in split
    assert 'action.type === "select"' in split
    assert 'action.type === "close"' in split
    assert 'document.addEventListener("pointerdown", closeOnOutsidePointer);' in chat
    assert 'document.addEventListener("pointerdown", closeBrowserPicker);' in split
    assert 'window.addEventListener("keydown", closeBrowserPicker);' in split


def test_composer_command_and_permission_menus_close_on_outside_pointerdown():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    composer = chat.split("function WbcComposer(", 1)[1].split(
        "// Context picker popup", 1
    )[0]

    assert "var slashPickerRef = useWbcRef(null);" in composer
    assert "var modePickerRef = useWbcRef(null);" in composer
    assert 'className="wbc-pop-anchor" ref={slashPickerRef}' in composer
    assert 'className="wbc-pop-anchor" ref={modePickerRef}' in composer
    assert 'document.addEventListener("pointerdown", closeComposerMenu);' in composer
    assert "!slashPickerRef.current.contains(event.target)" in composer
    assert "!modePickerRef.current.contains(event.target)" in composer
    assert 'document.removeEventListener("pointerdown", closeComposerMenu);' in composer


def test_existing_item_action_menus_are_available_from_right_click():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    library = (ROOT / "src/webui/frontend/workbench-library.jsx").read_text(
        encoding="utf-8"
    )

    project_card = shell.split('className={"workbench-project-card"', 1)[1].split(
        "</div>", 1
    )[0]
    task_board_card = shell.split('className={"wb-board-card is-"', 1)[1].split(
        "</article>", 1
    )[0]
    task_rail_card = shell.split('className={"workbench-task-card"', 1)[1].split(
        "</div>", 1
    )[0]
    chat_card = chat.split('className={"wbc-chat-card"', 1)[1].split("</div>", 1)[0]
    for item in (project_card, task_board_card, task_rail_card, chat_card):
        assert "onContextMenu=" in item
        assert "event.preventDefault();" in item
        assert "event.stopPropagation();" in item

    row = library.split("function LibraryRow(props)", 1)[1].split(
        "function LibraryCard(props)", 1
    )[0]
    card = library.split("function LibraryCard(props)", 1)[1].split(
        "function StatePanel(props)", 1
    )[0]
    for item in (row, card):
        assert "onContextMenu:" in item
        assert "event.preventDefault();" in item
        assert "event.stopPropagation();" in item
        assert "props.onContextMenu(item, event)" in item
    assert "function openItemContextMenu(item, event)" in library
    assert "wb-lib-context-menu" in library
    assert 'document.querySelector(".workbench-shell")' in library
    assert "portalTheme: portalTheme" in library


def test_chat_page_blank_area_context_menu_reuses_quick_actions():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

    page = chat.split("function WorkbenchChatPage(", 1)[1].split(
        "function WbcRenameDialog(", 1
    )[0]
    overview = chat.split("function WbcOverviewTab(", 1)[1].split(
        "function wbcBlockLabel(", 1
    )[0]

    assert "function WbcQuickActionItems(" in chat
    page_root = page.split('className={"wbc-page"', 1)[1].split(">", 1)[0]
    assert "onContextMenu=" not in page_root
    assert "onConversationContextMenu={openPageContextMenu}" in page
    main = chat.split("function WbcMain(", 1)[1].split(
        "function WbcConversationNavigator(", 1
    )[0]
    assert "onContextMenu={onConversationContextMenu}" in main
    eligibility = chat.split("function wbcCanOpenPageContextMenu(", 1)[1].split(
        "function wbcPointInsideResourceShelf(", 1
    )[0]
    for broad_content_container in (".wbc-msg", ".wbc-question", ".wbc-trace"):
        assert broad_content_container not in eligibility
    for protected_control in ("button", "input", ".wbc-composer", ".wbc-browser-window"):
        assert protected_control in eligibility
    assert 'className="wb-item-context-menu wbc-page-context-menu"' in page
    assert "wbcCanOpenPageContextMenu(event)" in page
    assert "wbcPageContextMenuPlacement(event.clientX, event.clientY, nativeRect)" in page
    assert page.count("<WbcQuickActionItems") >= 1
    assert overview.count("<WbcQuickActionItems") == 0
    for action in (
        "workbenchChat.rename",
        "workbenchChat.toTask",
        "workbenchChat.compact",
        "workbenchChat.delete",
    ):
        assert action in chat.split("function WbcQuickActionItems(", 1)[1].split(
            "function WbcOverviewTab(", 1
        )[0]
    assert ".wbc-page-context-layer { z-index: 10150; }" in css
    assert ".wbc-page-context-layer .wb-item-context-menu { z-index: 10151; }" in css


def test_chat_quick_rename_uses_existing_dialog_instead_of_native_prompt():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    page = chat.split("function WorkbenchChatPage(", 1)[1].split(
        "function WbcRenameDialog(", 1
    )[0]
    overview = chat.split("function WbcOverviewTab(", 1)[1].split(
        "function wbcBlockLabel(", 1
    )[0]

    assert "function openQuickRename()" in page
    assert "setQuickRenameChat(activeChat);" in page
    assert "chat={quickRenameChat}" in page
    assert "onRename={handleRenameChat}" in page
    assert "window.prompt" not in overview


def test_chat_card_menu_can_convert_the_selected_chat_to_a_task():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    page = chat.split("function WorkbenchChatPage(", 1)[1].split(
        "function WbcRenameDialog(", 1
    )[0]
    rail = chat.split("function WbcRail(", 1)[1].split(
        "// Conversation main", 1
    )[0]

    assert "onToTask={handleToTask}" in page
    assert "toTaskBusy={toTaskBusy}" in page
    assert 'wbcT(toTaskBusy ? "workbenchChat.toTaskBusy" : "workbenchChat.toTask"' in rail
    assert "if (onToTask) onToTask(chat.id);" in rail
    assert 'typeof chatId === "string"' in page


def test_all_chat_action_menu_items_have_icons():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    rail = chat.split("function WbcRail(", 1)[1].split(
        "// Conversation main", 1
    )[0]
    card_menu = rail.split('className="wb-card-menu" role="menu"', 1)[1].split(
        "</div>", 1
    )[0]
    for icon in ("pin", "edit", "task", "trash"):
        assert f"WBC_ICONS.{icon}" in card_menu

    header = chat.split("function WbcHeader(", 1)[1].split(
        "// Conversation thread", 1
    )[0]
    overflow_menu = header.split('className="wbc-menu"', 1)[1].split(
        "</div>", 1
    )[0]
    for icon in ("edit", "task", "trash"):
        assert f"WBC_ICONS.{icon}" in overflow_menu

    quick_actions = chat.split("function WbcQuickActionItems(", 1)[1].split(
        "var WBC_SIDE_CARD_ORDER_PREFIX", 1
    )[0]
    for icon in ("edit", "task", "compact", "trash"):
        assert f"WBC_ICONS.{icon}" in quick_actions


def test_chat_page_context_menu_preserves_native_browser_surface():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    opener = chat.split("function openPageContextMenu(event)", 1)[1].split(
        "function handleDelete()", 1
    )[0]
    preview = chat.split("function onBrowserPreviewReady(event)", 1)[1].split(
        'window.addEventListener("workbench:browser-window-preview-ready"', 1
    )[0]

    assert 'document.querySelector(".wbc-browser-window .browser-native-host")' in opener
    assert "if (!placement.overlapsBrowser)" in opener
    assert 'wbcNotifyBrowserWindowInteraction(true, "context-menu"' in opener
    assert "detail.fallback" in preview
    assert "browserPreview: true" in preview
    assert 'wbcNotifyBrowserWindowInteraction(false, "context-menu"' in chat


def test_chat_page_context_menu_placement_avoids_browser_window():
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    helpers = "function wbcRectsOverlap(" + chat.split(
        "function wbcRectsOverlap(", 1
    )[1].split("function wbcCanOpenPageContextMenu", 1)[0]
    script = f"""
global.window = {{ innerWidth: 1200, innerHeight: 800 }};
eval({json.dumps(helpers)});
const browser = {{ left: 760, top: 120, right: 1160, bottom: 620 }};
const result = {{
  clear: wbcPageContextMenuPlacement(40, 40, browser),
  avoided: wbcPageContextMenuPlacement(900, 300, browser),
  cramped: wbcPageContextMenuPlacement(100, 100, {{
    left: 0, top: 0, right: 1200, bottom: 800
  }})
}};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["clear"] == {"left": 40, "top": 40, "overlapsBrowser": False}
    assert result["avoided"]["overlapsBrowser"] is False
    avoided = {
        "left": result["avoided"]["left"],
        "top": result["avoided"]["top"],
        "right": result["avoided"]["left"] + 220,
        "bottom": result["avoided"]["top"] + 166,
    }
    assert (
        avoided["right"] <= 760
        or avoided["left"] >= 1160
        or avoided["bottom"] <= 120
        or avoided["top"] >= 620
    )
    assert result["cramped"]["overlapsBrowser"] is True
    assert 8 <= result["cramped"]["left"] <= 972
    assert 8 <= result["cramped"]["top"] <= 626


def test_knowledge_context_menu_can_show_a_local_file_in_its_folder():
    library = (ROOT / "src/webui/frontend/workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    preload = (ROOT / "electron/preload.js").read_text(encoding="utf-8")
    main = (ROOT / "electron/main.js").read_text(encoding="utf-8")

    assert "function showLibraryItemInFolder(item)" in library
    assert "desktopBridge.showItemInFolder(filePath)" in library
    assert 'L("library.showInFolder", "Show in folder")' in library
    assert "showItemInFolder: (filePath)" in preload
    assert "ipcRenderer.invoke('shell:show-item-in-folder'" in preload
    assert "ipcMain.handle('shell:show-item-in-folder'" in main
    assert "fs.existsSync(resolved)" in main
    assert "shell.showItemInFolder(resolved)" in main


def test_library_card_star_precedes_aligned_selection_control():
    library = (ROOT / "src/webui/frontend/workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "src/webui/frontend/workbench-library.css").read_text(
        encoding="utf-8"
    )
    title_row = library.split(
        'h("div", { className: "wb-lib-card-title-row" }', 1
    )[1].split('h("p", { className: "wb-lib-card-description" }', 1)[0]

    assert title_row.index('className: "wb-lib-star"') < title_row.index(
        'className: "wb-lib-check wb-lib-card-check"'
    )
    star_rule = css.split(".wb-lib-star {", 1)[1].split("}", 1)[0]
    check_rule = css.split(".wb-lib-card-check {", 1)[1].split("}", 1)[0]
    for rule in (star_rule, check_rule):
        assert "width: 24px" in rule
        assert "height: 24px" in rule
        assert "place-items: center" in rule


def test_library_table_title_header_is_localized_and_aligned_to_filenames():
    library = (ROOT / "src/webui/frontend/workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "src/webui/frontend/workbench-library.css").read_text(
        encoding="utf-8"
    )
    i18n = (ROOT / "src/webui/frontend/workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    assert 'className: "wb-lib-title-head"' in library
    title_rule = css.split(".wb-lib-title-head {", 1)[1].split("}", 1)[0]
    assert "padding-left: 64px" in title_rule
    for key, english, chinese in (
        ("title", "Title", "标题"),
        ("author", "Author", "作者"),
        ("year", "Year", "年份"),
        ("source", "Source", "来源"),
        ("added", "Added", "添加时间"),
        ("tags", "Tags", "标签"),
    ):
        assert f'"library.column.{key}": "{english}"' in i18n
        assert f'"library.column.{key}": "{chinese}"' in i18n


def test_memory_items_expose_existing_actions_from_right_click():
    memory = (ROOT / "src/webui/frontend/workbench-memory.jsx").read_text(
        encoding="utf-8"
    )

    card = memory.split("function card(m)", 1)[1].split(
        "var learning =", 1
    )[0]
    assert "onContextMenu:" in card
    assert "openMemoryContextMenu(m, event)" in card
    assert "function openMemoryContextMenu(m, event)" in memory
    assert 't("memory.edit", "Edit memory")' in memory
    assert 't("memory.markStale", "Mark outdated")' in memory
    assert 't("memory.delete", "Delete memory")' in memory


def test_schedule_events_have_context_actions_in_every_calendar_view():
    schedule = (ROOT / "src/webui/frontend/workbench-schedule.jsx").read_text(
        encoding="utf-8"
    )

    assert schedule.count("props.onContextMenu(ev, e)") >= 4
    assert "function openScheduleContextMenu(ev, event)" in schedule
    assert 'T("schedule.edit")' in schedule
    assert 'T("schedule.enable")' in schedule
    assert 'T("schedule.pause")' in schedule
    assert 'T("schedule.delete")' in schedule
    for component in ("DayView", "WeekView", "MonthView"):
        call = f"React.createElement({component},"
        assert call in schedule
    assert "onContextMenu: openScheduleContextMenu" in schedule


def test_native_browser_tabs_support_reload_mute_and_close_from_right_click():
    browser = (
        ROOT / "src/webui/frontend/shared/browser/viewport.jsx"
    ).read_text(encoding="utf-8")
    i18n = (ROOT / "src/webui/frontend/workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    assert "onContextMenu={function (event) { openTabContextMenu(tab, event); }}" in browser
    assert "function openTabContextMenu(tab, event)" in browser
    assert "function runForTab(tab, action)" in browser
    assert 'bridge.reload(electronSessionId)' in browser
    assert 'bridge.setMuted({ sessionId: electronSessionId, muted: !tab.muted })' in browser
    assert 'bridge.closeTab({ sessionId: electronSessionId, tabId: tab.id })' in browser
    assert 'sendBounds(false);' in browser
    for key, english, chinese in (
        ("reload", "Reload", "重新加载"),
        ("mute", "Mute", "静音"),
        ("unmute", "Unmute", "取消静音"),
        ("close", "Close tab", "关闭标签页"),
    ):
        assert f'"browser.context.{key}": "{english}"' in i18n
        assert f'"browser.context.{key}": "{chinese}"' in i18n


def test_browser_tab_menu_uses_a_snapshot_before_hiding_native_content():
    browser = (
        ROOT / "src/webui/frontend/shared/browser/viewport.jsx"
    ).read_text(encoding="utf-8")

    opener = browser.split("function openTabContextMenu(tab, event)", 1)[1].split(
        "function runForTab", 1
    )[0]
    preview_load = browser.split("function onTabMenuPreviewLoad(event)", 1)[1].split(
        "function onTabMenuPreviewError", 1
    )[0]

    assert "bridge.screenshot({" in opener
    assert 'src: "data:image/png;base64," + result.pngBase64' in opener
    assert "setTabContextMenu(menu)" not in opener
    assert "imageNode.decode()" in preview_load
    assert "Promise.resolve(sendBounds(false))" in preview_load
    assert preview_load.index("sendBounds(false)") < preview_load.index(
        "setTabContextMenu(preview.menu)"
    )
    assert "interactionPreview || tabMenuPreview" in browser
    assert "onLoad={onTabMenuPreviewLoad}" in browser
