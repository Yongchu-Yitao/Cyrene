from conftest import (
    frontend_module_source,
    workbench_chat_source,
    workbench_i18n_source,
    workbench_shell_source,
    workbench_style_source,
)
import json
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent.parent


def test_browser_tab_picker_floats_in_a_native_view_without_obscuring_the_page():
    chat = workbench_chat_source()
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
        "  pushTabPickerState() {", 1
    )[0]
    assert "new WebContentsView" in native_view
    assert "parent.addChildView(view)" in main.split("syncTabPicker(", 1)[1]
    assert "this.syncTabPicker(win.contentView, true);" in main
    assert "setTabPicker: (info) => ipcRenderer.invoke('browser:set-tab-picker'" in preload
    assert '"browser-tab-picker-preload.js"' in package


def test_native_browser_tab_picker_has_motion_and_reduced_motion_support():
    chat = workbench_chat_source()
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


def test_native_browser_tab_picker_has_flat_chrome_without_visible_scrollbars():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    build = (ROOT / "src" / "webui" / "build-jsx.mjs").read_text(encoding="utf-8")
    embedded_picker = main.split("const BROWSER_TAB_PICKER_HTML", 1)[1].split(
        "function normalizeBrowserSessionId", 1
    )[0]

    assert "box-shadow: none" in embedded_picker
    assert "scrollbar-width: none" in embedded_picker
    assert "#menu::-webkit-scrollbar" in embedded_picker
    assert "overflow-y: auto" in embedded_picker
    assert "['BROWSER_TAB_PICKER_HTML', 'browser-tab-picker.html']" in build

    native_view = main.split("  ensureTabPickerView() {", 1)[1].split(
        "  pushTabPickerState() {", 1
    )[0]
    assert "BROWSER_TAB_PICKER_FLAT_CHROME_CSS" in native_view
    assert "await view.webContents.insertCSS" in native_view
    assert "&style=flat-chrome-1" in native_view

    picker_bounds = main.split("tabPickerBounds()", 1)[1].split(
        "trackTabPickerWindow", 1
    )[0]
    assert "variant === 'split' ? 'split' : 'maximized'" in main
    assert "variant === 'maximized' ? 116 : 12" in picker_bounds


def test_native_browser_tab_picker_title_clicks_are_debounced_in_both_hosts():
    chat = workbench_chat_source()
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
    chat = workbench_chat_source()
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
    assert "options.bridge.onTabPickerAction" in chat
    assert "bridge.onTabPickerAction" in split
    assert 'action.type === "select"' in split
    assert 'action.type === "close"' in split
    assert 'document.addEventListener("pointerdown", closeOnOutsidePointer);' in chat
    assert 'document.addEventListener("pointerdown", closeBrowserPicker);' in split
    assert 'window.addEventListener("keydown", closeBrowserPicker);' in split


def test_composer_tools_menu_closes_on_outside_pointerdown():
    chat = workbench_chat_source()
    composer = chat.split("function WbcComposer(", 1)[1].split(
        "// Context picker popup", 1
    )[0]

    assert "var toolsPickerRef = useWbcRef(null);" in composer
    assert 'className="wbc-pop-anchor wbc-tools-anchor" ref={toolsPickerRef}' in composer
    assert 'document.addEventListener("pointerdown", closeToolsMenu);' in composer
    assert "!toolsPickerRef.current.contains(event.target)" in composer
    assert 'document.removeEventListener("pointerdown", closeToolsMenu);' in composer
    assert 'setModelPanel("permission")' in composer


def test_existing_item_action_menus_are_available_from_right_click():
    shell = workbench_shell_source()
    chat = workbench_chat_source()
    library = (ROOT / "src/webui/frontend/workbench-library.jsx").read_text(
        encoding="utf-8"
    )

    # Project cards were consolidated into the top-bar switcher. Its current
    # entry exposes the project actions through a visible per-project button;
    # cards that still support context menus retain their right-click contract.
    project_entry = shell.split(
        'className={"workbench-top-project-row"', 1
    )[1].split("</div>", 1)[0]
    assert 'className="workbench-top-project-more"' in project_entry
    assert "event.stopPropagation();" in project_entry
    assert "setProjectActionId(actionsOpen ? \"\" : project.id);" in project_entry
    assert 'className="workbench-top-project-actions" role="menu"' in project_entry
    task_board_card = shell.split('className={"wb-board-card is-"', 1)[1].split(
        "</article>", 1
    )[0]
    task_rail_card = chat.split('className={"wbc-chat-card wbc-task-card"', 1)[1].split(
        "</div>", 1
    )[0]
    chat_card = chat.split('className={"wbc-chat-card"', 1)[1].split("</div>", 1)[0]
    for item in (task_board_card, task_rail_card, chat_card):
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
    chat = workbench_chat_source()
    css = workbench_style_source()

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
        "function wbcQuestionOptionValue(", 1
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
        "workbenchChat.generateMemory",
        "workbenchChat.delete",
    ):
        assert action in chat.split("function WbcQuickActionItems(", 1)[1].split(
            "function WbcOverviewTab(", 1
        )[0]
    assert ".wbc-page-context-layer { z-index: 10150; }" in css
    assert ".wbc-page-context-layer .wb-item-context-menu { z-index: 10151; }" in css


def test_chat_quick_rename_uses_existing_dialog_instead_of_native_prompt():
    chat = workbench_chat_source()
    rename_controller = frontend_module_source("features/chat/chat-action-controller.jsx")
    page = chat.split("function WorkbenchChatPage(", 1)[1].split(
        "function WbcRenameDialog(", 1
    )[0]
    overview = chat.split("function WbcOverviewTab(", 1)[1].split(
        "function wbcBlockLabel(", 1
    )[0]

    assert "function openQuickRename()" in page
    assert "context.setQuickRenameChat(context.activeChat);" in rename_controller
    assert "chat={quickRenameChat}" in page
    assert "onRename={handleRenameChat}" in page
    assert "window.prompt" not in overview


def test_chat_card_menu_can_convert_the_selected_chat_to_a_task():
    chat = workbench_chat_source()
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
    chat = workbench_chat_source()

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
    for icon in ("edit", "task", "compact", "spark", "trash"):
        assert f"WBC_ICONS.{icon}" in quick_actions

    assert quick_actions.index("workbenchChat.compact") < quick_actions.index(
        "workbenchChat.generateMemory"
    ) < quick_actions.index("workbenchChat.delete")


def test_floating_menus_and_modals_share_the_conversation_surface_tokens():
    styles = workbench_style_source()
    library_styles = (ROOT / "src/webui/frontend/workbench-library.css").read_text(
        encoding="utf-8"
    )

    shell_tokens = styles.split(".workbench-shell {", 1)[1].split("}", 1)[0]
    for token in (
        "--wb-flyout-border:",
        "--wb-flyout-bg:",
        "--wb-flyout-radius:",
        "--wb-flyout-shadow:",
        "--wb-modal-border:",
        "--wb-modal-bg:",
        "--wb-modal-radius:",
        "--wb-modal-shadow:",
        "--wb-modal-scrim-bg:",
        "--wb-modal-scrim-filter:",
        "--wb-field-border:",
    ):
        assert token in shell_tokens

    for selector in (
        ".wb-card-menu {",
        ".wb-mem-menu {",
        ".wb-item-context-menu {",
        ".wbc-menu {",
        ".wbc-popmenu {",
    ):
        block = styles.split(selector, 1)[1].split("}", 1)[0]
        assert "var(--wb-flyout-border" in block
        assert "var(--wb-flyout-bg" in block
        assert "var(--wb-flyout-radius" in block
        assert "var(--wb-flyout-shadow" in block

    for selector in (
        ".workbench-project-edit-modal {",
        ".workbench-confirm-modal {",
        ".wbc-rename-dialog {",
        ".wb-mem-modal {",
        ".wb-create-modal {",
    ):
        block = styles.split(selector, 1)[1].split("}", 1)[0]
        assert "var(--wb-modal-border" in block
        assert "var(--wb-modal-bg" in block
        assert "var(--wb-modal-radius" in block
        assert "var(--wb-modal-shadow" in block

    for selector in (".wb-lib-dropdown {", ".wb-lib-context-menu {"):
        block = library_styles.split(selector, 1)[1].split("}", 1)[0]
        assert "var(--wb-flyout-border" in block
        assert "var(--wb-flyout-bg" in block
        assert "var(--wb-flyout-radius" in block
        assert "var(--wb-flyout-shadow" in block

    library_modal = library_styles.split(".wb-lib-modal {", 1)[1].split("}", 1)[0]
    assert "var(--wb-modal-border" in library_modal
    assert "var(--wb-modal-bg" in library_modal
    assert "var(--wb-modal-radius" in library_modal
    assert "var(--wb-modal-shadow" in library_modal


def test_remaining_live_flyouts_use_shared_tokens_and_legacy_picker_css_is_removed():
    styles = workbench_style_source()

    for selector in (".wb-accent-popover {", ".wbc-model-menu {", ".wbq-picker {"):
        block = styles.split(selector, 1)[1].split("}", 1)[0]
        assert "var(--wb-flyout" in block

    accent_arrow = styles.split(".wb-accent-popover::before {", 1)[1].split("}", 1)[0]
    assert "var(--wb-flyout-border)" in accent_arrow
    assert "var(--wb-flyout-bg)" in accent_arrow
    assert 'html[data-theme="light"] .wbc-model-menu' not in styles
    assert 'html[data-theme="dark"] .wbc-model-menu' not in styles

    # The retired conversation-rail project picker has no runtime component;
    # keep only the still-live new-chat control from that old selector family.
    for retired in (
        ".wbc-nav-card.picker-open",
        ".wbc-project-trigger",
        ".wbc-project-picker",
        ".wbc-project-avatar",
        ".wbc-project-active-check",
    ):
        assert retired not in styles
    assert ".wbc-project-new-chat" in styles


def test_composer_popmenus_hide_scrollbar_chrome_without_disabling_scrolling():
    styles = workbench_style_source()

    for selector in (".wb-popmenu {", ".wbc-popmenu {"):
        block = styles.split(selector, 1)[1].split("}", 1)[0]
        assert "overflow-y: auto;" in block
        assert "scrollbar-width: none;" in block
        assert "-ms-overflow-style: none;" in block

    webkit_rule = styles.split(
        ".wb-popmenu::-webkit-scrollbar,\n.wbc-popmenu::-webkit-scrollbar {", 1
    )[1].split("}", 1)[0]
    assert "display: none;" in webkit_rule
    assert "width: 0;" in webkit_rule
    assert "height: 0;" in webkit_rule


def test_memory_and_library_flyouts_expose_accessible_state():
    memory = (ROOT / "src/webui/frontend/workbench-memory.jsx").read_text(
        encoding="utf-8"
    )
    library = (ROOT / "src/webui/frontend/workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    quick_chat = (ROOT / "src/webui/frontend/workbench-quick-chat.jsx").read_text(
        encoding="utf-8"
    )

    assert '"aria-modal": "true", "aria-labelledby": titleId' in memory
    assert 'role: "radiogroup", "aria-label": label' in memory
    assert '"aria-haspopup": "menu"' in memory
    assert 'role: "menuitemradio", "aria-checked": value === o.id' in memory
    assert 'htmlFor: "wb-memory-content"' in memory
    assert 'htmlFor: "wb-memory-tags"' in memory

    assert 'role: "dialog", "aria-modal": "true", "aria-labelledby": "wb-lib-add-title"' in library
    assert 'role: "dialog", "aria-modal": "true", "aria-labelledby": "wb-lib-collection-title"' in library
    assert 'role="dialog" aria-label={quickChatText("选择对话", "Choose conversation")}' in quick_chat
    assert 'role="group" aria-label={quickChatText("对话列表", "Conversation list")}' in quick_chat
    assert "aria-pressed={!selectedChatId}" in quick_chat
    assert "aria-pressed={on}" in quick_chat


def test_chat_page_context_menu_preserves_native_browser_surface():
    chat = workbench_chat_source()
    opener = frontend_module_source("features/chat/page-context-menu.jsx")
    preview = chat.split("function onBrowserPreviewReady(event)", 1)[1].split(
        'window.addEventListener("workbench:browser-window-preview-ready"', 1
    )[0]

    assert 'document.querySelector(".wbc-browser-window .browser-native-host")' in opener
    assert "if (!placement.overlapsBrowser)" in opener
    assert 'wbcNotifyBrowserWindowInteraction(true, "context-menu"' in opener
    assert "detail.fallback" in preview
    assert "browserPreview: true" in preview


def test_project_memory_editor_and_manual_chat_trigger_are_wired_end_to_end():
    support = frontend_module_source("features/shell/support.jsx")
    topbar = frontend_module_source("features/shell/topbar.jsx")
    shell_composition = frontend_module_source("features/shell/shell-composition.jsx")
    model_api = frontend_module_source("features/chat/model-api.jsx")
    chat_page = frontend_module_source("features/chat/page.jsx")
    context_panel = frontend_module_source("features/chat/context-panel.jsx")
    error_mapping = frontend_module_source("features/chat/errors.jsx")
    messages = frontend_module_source("features/chat/messages.jsx")
    memory_page = (ROOT / "src/webui/frontend/workbench-memory.jsx").read_text(encoding="utf-8")
    memory_css = frontend_module_source("features/memory/memory.css")
    css = workbench_style_source()
    i18n = workbench_i18n_source()

    assert "function WorkbenchProjectMemoryModal(" in support
    modal = support.split("function WorkbenchProjectMemoryModal(", 1)[1].split(
        "function WorkbenchSidebarCollapseControl", 1
    )[0]
    assert '["prompt", "memories", "history"]' not in modal
    assert '"/memory-prompt?include_memories=false"' in modal
    assert 'method: "PATCH"' in modal
    assert '"/memory-prompt/restore"' in modal
    assert "baseModifiedAt" in modal
    assert "WorkbenchProjectMemoryItem" not in support
    assert "selectedModifiedAt" in modal
    assert 't("projectMemory.versionSelector")' in modal
    assert "readOnly={!!selectedVersion}" in modal

    top_actions = topbar.split('className="workbench-top-project-actions"', 1)[1].split(
        "</div>", 1
    )[0]
    assert top_actions.index('t("rail.editProject")') < top_actions.index(
        't("rail.editMemory")'
    ) < top_actions.index('t("rail.deleteProject")')
    assert "onEditMemory={dialogs.setEditMemoryProject}" in shell_composition
    assert "onEditProjectMemory: context.dialogs.editActiveProjectMemory" in shell_composition
    assert "props.onEditProjectMemory" in memory_page
    assert "props.onEditProjectMemory && !props.sidebarCollapsed" in memory_page
    assert 'className: "wb-mem-project-memory-btn"' in memory_page
    assert ".wb-mem-rail.is-collapsed .wb-mem-project-memory-btn" in memory_css
    assert 't("memory.editProjectMemory"' in memory_page
    assert ".workbench-project-memory-scrim" in css
    assert "width: min(900px" in css
    assert 'className="workbench-project-memory-head-version"' in modal
    assert 'className="workbench-project-memory-overview"' in modal
    assert ".workbench-project-memory-head-version" in css
    assert ".workbench-project-memory-toolbar" not in css
    assert ".workbench-project-memory-tabs" not in css

    assert "function generateMemory(chatId, lang)" in model_api
    assert '"/memory-learning"' in model_api
    assert 'body: JSON.stringify({ lang: lang === "zh" ? "zh" : "en" })' in model_api
    assert "model.generateMemory(activeChat.id, memoryLanguage)" in chat_page
    assert "onGenerateMemory={handleGenerateMemory}" in chat_page
    assert "onClick={run(onGenerateMemory)}" in context_panel
    for text in (
        '"rail.editMemory": "Edit memory"',
        '"rail.editMemory": "编辑记忆"',
        '"workbenchChat.generateMemory": "Generate memory"',
        '"workbenchChat.generateMemory": "生成记忆"',
        '"workbenchChat.error.memoryTitle": "Could not generate memory"',
        '"workbenchChat.error.memoryTitle": "无法生成记忆"',
        '"workbenchChat.error.memoryContextUnavailable": "This conversation has no recoverable model context, so project memory cannot be generated."',
        '"workbenchChat.error.memoryContextUnavailable": "这段对话没有可恢复的模型上下文，无法生成项目记忆。"',
        '"memory.editProjectMemory": "Edit project memory"',
        '"memory.editProjectMemory": "编辑项目记忆"',
    ):
        assert text in i18n
    assert 'setErrorKind("memory")' in chat_page
    assert 'kind === "memory"' in messages
    assert 'no_completed_context: "workbenchChat.error.memoryContextUnavailable"' in error_mapping
    assert 'wbcNotifyBrowserWindowInteraction(false, "context-menu"' in chat_page

    from agent.plugin.plugin_impl.cyrene_memory.routes_project import (
        register_project_memory_routes,
    )

    class MemoryService:
        calls = []

        async def learn_from_chat(self, chat_id, *, language=""):
            self.calls.append((chat_id, language))
            return {"status": "queued", "job": {"id": "job_1"}}

    service = MemoryService()
    app = FastAPI()
    register_project_memory_routes(app, service)
    response = TestClient(app).post(
        "/api/workbench/chats/chat_1/memory-learning",
        json={"lang": "zh"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "queued", "job": {"id": "job_1"}}
    assert service.calls == [("chat_1", "zh")]


def test_chat_page_context_menu_placement_avoids_browser_window():
    chat = workbench_chat_source()
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
        "bottom": result["avoided"]["top"] + 206,
    }
    assert (
        avoided["right"] <= 760
        or avoided["left"] >= 1160
        or avoided["bottom"] <= 120
        or avoided["top"] >= 620
    )
    assert result["cramped"]["overlapsBrowser"] is True
    assert 8 <= result["cramped"]["left"] <= 972
    assert 8 <= result["cramped"]["top"] <= 586


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
    i18n = workbench_i18n_source()

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
    i18n = workbench_i18n_source()

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
