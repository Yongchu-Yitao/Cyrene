from pathlib import Path
import json
import subprocess


ROOT = Path(__file__).resolve().parent.parent


def test_topbar_uses_three_recent_task_and_conversation_tabs():
    source = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")

    assert (
        "function wbRecentSessionTabs(projects, chatsByProject, recentOpenedKeys, pinnedKeys, hiddenKeys, limit)"
        in source
    )
    assert 'kind: "task"' in source
    assert 'kind: "chat"' in source
    assert "right.updatedAt.localeCompare(left.updatedAt)" in source
    assert "recentOpenedSessionKeys" in source
    assert 'localStorage.setItem("wb-recent-opened-sessions"' in source
    assert 'rememberOpenedSession("chat", activeChatId)' in source
    assert 'rememberOpenedSession("task", store.activeSessionId)' in source
    assert 'className={"workbench-session-tab" + (isActive ? " active" : "")}' in source
    assert 'navigateFromSearch({ type: "chat"' in source
    assert 'navigateFromSearch({ type: "task"' in source
    assert 'className="workbench-crumbs"' not in source


def test_recent_session_merger_orders_mixed_items_and_caps_at_three():
    source = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    helper = source.split("function wbRecentSessionTabs", 1)[1].split(
        "\nfunction WorkbenchSessionMenuFileName", 1
    )[0]
    script = (
        "function wbRecentSessionTabs"
        + helper
        + """
const projects = [
  {id: "p1", name: "One", sessions: [
    {id: "t1", title: "Older task", updatedAt: "2026-07-01T00:00:00Z"},
    {id: "t2", title: "Newest task", updatedAt: "2026-07-04T00:00:00Z"}
  ]},
  {id: "p2", name: "Two", sessions: [
    {id: "t3", title: "Middle task", updatedAt: "2026-07-02T00:00:00Z"}
  ]}
];
const chats = {
  p1: [{id: "c1", title: "Recent chat", updatedAt: "2026-07-03T00:00:00Z"}],
  p2: [{id: "c2", title: "Old chat", updatedAt: "2026-06-01T00:00:00Z"}]
};
const opened = ["task:t3", "chat:c1", "task:t2"];
const pinned = ["chat:c1"];
const hidden = ["task:t2"];
process.stdout.write(JSON.stringify(wbRecentSessionTabs(projects, chats, opened, pinned, hidden, 3)));
"""
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    items = json.loads(result.stdout)

    assert [(item["kind"], item["id"]) for item in items] == [
        ("chat", "c1"),
        ("task", "t3"),
        ("task", "t1"),
    ]
    assert items[0]["pinned"] is True


def test_pinned_sessions_are_not_dropped_by_the_recent_tab_limit():
    source = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    helper = source.split("function wbRecentSessionTabs", 1)[1].split(
        "\nfunction WorkbenchSessionMenuFileName", 1
    )[0]
    script = (
        "function wbRecentSessionTabs"
        + helper
        + """
const projects = [{id: "p1", sessions: []}];
const chats = {p1: [
  {id: "c1", title: "One"}, {id: "c2", title: "Two"},
  {id: "c3", title: "Three"}, {id: "c4", title: "Four"}
]};
process.stdout.write(JSON.stringify(wbRecentSessionTabs(
  projects, chats, [], ["chat:c1", "chat:c2", "chat:c3", "chat:c4"], [], 3
)));
"""
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    items = json.loads(result.stdout)

    assert [item["id"] for item in items] == ["c1", "c2", "c3", "c4"]
    assert all(item["pinned"] is True for item in items)


def test_existing_topbar_session_keeps_order_until_an_unshown_session_is_opened():
    source = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    helper = source.split("function wbRememberOpenedSessionKey", 1)[1].split(
        "\nfunction wbDeliverResourceToChat", 1
    )[0]
    script = (
        "function wbRememberOpenedSessionKey"
        + helper
        + """
const initial = ["task:t1", "chat:c1", "task:t2"];
const visible = initial.slice();
const existing = wbRememberOpenedSessionKey(initial, visible, "chat:c1", 20);
const newlyOpened = wbRememberOpenedSessionKey(existing, visible, "task:t3", 20);
const fallbackSnapshot = wbRememberOpenedSessionKey(
  ["task:t1"],
  ["task:t1", "chat:c1", "task:t2"],
  "chat:c1",
  20
);
process.stdout.write(JSON.stringify({
  existing,
  existingUsesSameArray: existing === initial,
  newlyOpened,
  fallbackSnapshot
}));
"""
    )
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    result = json.loads(completed.stdout)

    assert result["existing"] == ["task:t1", "chat:c1", "task:t2"]
    assert result["existingUsesSameArray"] is True
    assert result["newlyOpened"][:4] == [
        "task:t3",
        "task:t1",
        "chat:c1",
        "task:t2",
    ]
    assert result["fallbackSnapshot"] == ["task:t1", "chat:c1", "task:t2"]


def test_recent_conversation_lists_stay_in_sync_with_chat_page():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    assert "reloadRecentChats(store.projects || [])" in shell
    assert "onChatsChange: function (projectId, chats)" in shell
    assert "if (onChatsChange && projectId) onChatsChange(projectId, chats)" in chat


def test_session_tabs_remain_interactive_inside_the_draggable_titlebar():
    css = (ROOT / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

    assert ".workbench-session-tabs {" in css
    tabstrip = css.split(".workbench-session-tabs {", 1)[1].split("}", 1)[0]
    tab = css.split(".workbench-session-tab {", 1)[1].split("}", 1)[0]
    assert "-webkit-app-region: no-drag" not in tabstrip
    assert '.workbench-topbar button,' in css
    assert '-webkit-app-region: no-drag' in css.split(
        '.workbench-topbar button,', 1
    )[1].split("}", 1)[0]
    assert "border: 1px solid" in tab
    assert "flex: 0 1 136px" in tab
    assert "padding: 0 6px" in tab
    assert "gap: 4px" in tab
    assert ".workbench-session-tab.active" in css
    assert ".workbench-session-tab.active::after" not in css
    assert "html[data-density=\"compact\"] .workbench-session-tab" in css


def test_active_session_tab_stretches_with_an_accessible_transition():
    css = (ROOT / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

    tab = css.split(".workbench-session-tab {", 1)[1].split("}", 1)[0]
    active = css.split(".workbench-session-tab.active {", 1)[1].split("}", 1)[0]
    reduced_motion = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]

    assert "flex-basis 240ms cubic-bezier(0.22, 1, 0.36, 1)" in tab
    assert "width: clamp(136px, 15vw, 220px)" in active
    assert "flex-basis: 220px" in active
    assert ".workbench-session-tab" in reduced_motion
    assert "transition: none" in reduced_motion


def test_session_tab_context_menu_supports_pinning_resources_and_removal():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

    assert "onContextMenu={function (event) { openSessionMenu(event, item); }}" in shell
    assert "onTogglePinnedSession" in shell
    assert "onRemoveSessionTab" in shell
    assert "onLoadSessionResources" in shell
    assert "bridge.screenshot({" in shell
    assert 'className="workbench-session-browser-preview"' in shell
    assert "<img src={sessionMenu.resources.browser.previewUrl}" in shell
    assert "function WorkbenchSessionMenuFileName" in shell
    assert "contentWidth - node.clientWidth" in shell
    assert "wb-session-file-name-scroll" in css
    assert 'onOpenSessionResource(item, { type: "browser" })' in shell
    assert 'onOpenSessionResource(item, { type: "file", file: file })' in shell
    assert "workbench.sessionMenu.copyTitle" in shell
    assert 'className="workbench-account-menu workbench-session-menu"' in shell
    assert 'className="workbench-session-menu-portal"' in shell
    assert "portalTheme[name] = computedTheme.getPropertyValue(name)" in shell
    assert "workbench-session-menu" in css
    topbar = shell.split("function WorkbenchTopbar", 1)[1].split(
        "function WorkbenchNotificationCenter", 1
    )[0]
    assert "wbSetBrowserOverlayObscured(1)" in topbar
    assert "wbSetBrowserOverlayObscured(-1)" in topbar
    assert "pendingTopbarResourceRef" in chat
    assert 'resource.type === "browser"' in chat
    assert 'resource.type === "file"' in chat


def test_browser_can_be_copied_to_another_conversation_by_drop():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    copy_helper = shell.split("function wbCopyBrowserToChat", 1)[1].split(
        "\nfunction WorkbenchSessionMenuFileName", 1
    )[0]
    assert "target === owner" in copy_helper
    assert "bridge.createTab({" in copy_helper
    assert "sessionId: target" in copy_helper
    assert "url: url" in copy_helper
    assert "activate: true" in copy_helper
    assert '"cyrene:browser-copied-to-chat"' in copy_helper
    assert 'resource.kind === "browser"' in shell
    assert "copyBrowserToConversation(item.id, resource)" in shell
    assert '"cyrene:copy-browser-to-chat"' in shell
    assert 'draggable={resource.kind === "browser" ? "true" : undefined}' in shell
    assert "function wbcConversationTabAtPoint" in chat
    assert '.workbench-session-tab[data-session-kind="chat"]' in chat
    assert '"cyrene:copy-browser-to-chat"' in chat
    assert '"cyrene:browser-copied-to-chat"' in chat
    assert "{ [targetChatId]: true }" in chat
    assert '{ [targetChatId]: "pip" }' in chat


def test_browser_copy_helper_creates_target_session_tab_without_reusing_owner():
    source = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    helper = source.split("function wbCopyBrowserToChat", 1)[1].split(
        "\nfunction WorkbenchSessionMenuFileName", 1
    )[0]
    script = (
        "const calls = [];"
        "global.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };"
        "global.window = {"
        " cyrene: { browser: { createTab: (info) => { calls.push(info); return Promise.resolve({ok:true}); } } },"
        " dispatchEvent: (event) => calls.push({event:event.type, detail:event.detail})"
        "};"
        "function wbCopyBrowserToChat"
        + helper
        + """
(async function () {
  const copied = await wbCopyBrowserToChat("target-chat", {
    kind: "browser",
    ownerSessionId: "source-chat",
    url: "https://example.com/path",
    title: "Example"
  });
  const sameOwner = await wbCopyBrowserToChat("source-chat", {
    kind: "browser",
    ownerSessionId: "source-chat",
    url: "https://example.com/path"
  });
  process.stdout.write(JSON.stringify({copied, sameOwner, calls}));
})().catch(function (error) { console.error(error); process.exit(1); });
"""
    )
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    result = json.loads(completed.stdout)
    assert result["copied"] is True
    assert result["sameOwner"] is False
    assert result["calls"][0] == {
        "sessionId": "target-chat",
        "url": "https://example.com/path",
        "activate": True,
    }
    assert result["calls"][1]["event"] == "cyrene:browser-copied-to-chat"
    assert result["calls"][1]["detail"]["targetChatId"] == "target-chat"


def test_topbar_sessions_and_resources_have_keyboard_control():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    shortcuts = (ROOT / "src/webui/frontend/workbench-shortcuts.jsx").read_text(
        encoding="utf-8"
    )

    assert 'data-workbench-topbar-item="session"' in shell
    assert 'data-workbench-topbar-item="resource"' in shell
    assert 'data-session-kind={item.kind}' in shell
    assert 'data-session-id={item.id}' in shell
    assert '["ArrowLeft", "ArrowRight", "Home", "End"]' in shell
    assert 'key === "Delete" || key === "Backspace"' in shell
    assert 'root.querySelectorAll("[data-workbench-topbar-item]")' in shell
    assert '"switch-session-1"' in shell
    assert '"next-session"' in shell
    assert '"previous-session"' in shell
    assert '"close-session-tab"' in shell
    assert 'keys: ["mod", "shift", "1"]' in shortcuts
    assert 'keys: ["mod", "1"]' in shortcuts
    assert 'keys: ["mod", "2"]' in shortcuts
    assert 'keys: ["mod", "3"]' in shortcuts
    assert 'keys: ["ctrl", "Tab"]' in shortcuts
    assert 'keys: ["ctrl", "shift", "Tab"]' in shortcuts
    assert 'keys: ["mod", "W"]' in shortcuts


def test_empty_resource_shelf_uses_a_right_aligned_pin_hint():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    css = (ROOT / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

    empty_hint = shell.split('className="workbench-resource-shelf-empty"', 1)[1].split(
        "</span>", 1
    )[0]
    empty_hint_css = css.split(".workbench-resource-shelf-empty {", 1)[1].split(
        "}", 1
    )[0]

    assert 'viewBox="0 0 24 24"' in empty_hint
    assert '<path d="M12 17v5" />' in empty_hint
    assert '<path d="M5 17h14" />' in empty_hint
    assert "margin-left: auto" in empty_hint_css
