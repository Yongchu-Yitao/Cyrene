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

    assert "openSessionMenu(event, item, activity, false)" in shell
    assert "onTogglePinnedSession" in shell
    assert "onRemoveSessionTab" in shell
    assert "onLoadSessionResources" in shell
    assert "bridge.screenshot({" in shell
    assert 'className="workbench-session-browser-preview"' in shell
    assert 'className="workbench-session-resource-chevron"' in shell
    assert "function WorkbenchSessionMenuFileName" in shell
    assert "contentWidth - node.clientWidth" in shell
    assert "wb-session-file-name-scroll" in css
    assert 'onOpenSessionResource(item, { type: "browser" })' in shell
    assert 'onOpenSessionResource(item, { type: "file", file: file })' in shell
    assert "workbench.sessionMenu.copyTitle" in shell
    assert "workbench-session-primary-actions" in shell
    assert "workbench-session-utility-actions" in shell
    assert 'className="open-session"' in shell
    assert ".workbench-session-primary-actions.has-runtime-control" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert ".workbench-session-utility-actions" in css
    assert "workbench-session-primary-actions:not(.has-runtime-control)" in css
    assert "justify-content: flex-start" in css
    utility_css = css.rsplit(".workbench-session-utility-actions {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in utility_css
    assert "margin-top: 8px" in utility_css
    action_button_css = css.split(
        ".workbench-account-menu.workbench-session-context-menu .workbench-session-primary-actions > button,",
        1,
    )[1].split("}", 1)[0]
    assert "font-weight: 700" in action_button_css
    utility_button_css = css.rsplit(
        ".workbench-account-menu.workbench-session-context-menu .workbench-session-utility-actions > button {",
        1,
    )[1].split("}", 1)[0]
    assert "min-height: 40px" in utility_button_css
    assert "color: var(--wb-text)" in utility_button_css
    danger_utility_css = css.split(
        ".workbench-session-utility-actions > button.danger {", 1
    )[1].split("}", 1)[0]
    assert "grid-column: auto" in danger_utility_css
    assert 'className="workbench-session-resource-section"' in shell
    session_menu = shell.split('className="workbench-account-menu workbench-session-menu workbench-session-context-menu"', 1)[1].split("var overflowMenuPortal", 1)[0]
    assert 'className="workbench-session-menu-separator"' not in session_menu
    hidden_scrollbar_css = css.split(
        ".workbench-session-context-menu.workbench-session-menu {", 1
    )[1].split("}", 1)[0]
    assert "scrollbar-width: none" in hidden_scrollbar_css
    assert ".workbench-session-context-menu.workbench-session-menu::-webkit-scrollbar" in css
    assert 'className="workbench-account-menu workbench-session-menu workbench-session-context-menu"' in shell
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


def test_session_activity_view_model_prioritizes_attention_and_preserves_active_tab():
    source = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    helper = source.split("function wbVisibleSessionTabs", 1)[1].split(
        "\nfunction wbRememberOpenedSessionKey", 1
    )[0]
    script = (
        "function wbVisibleSessionTabs"
        + helper
        + """
const items = [
  {kind:"task", id:"t1", source:{status:"idle"}},
  {kind:"chat", id:"c1", source:{runStatus:"running"}},
  {kind:"task", id:"t2", source:{status:"waiting_for_user", pendingQuestion:{id:"q1"}}},
  {kind:"task", id:"t3", source:{status:"running", planStepCount:5, planCompletedCount:2, planCurrentIndex:3}},
  {kind:"task", id:"t4", source:{status:"planning"}},
  {kind:"chat", id:"c2", source:{status:"idle", messageCount:4}}
];
const layout = wbVisibleSessionTabs(items, "task:t3", 3);
process.stdout.write(JSON.stringify({
  visible: layout.visible.map((item) => item.id),
  overflow: layout.overflow.map((item) => item.id),
  attention: wbSessionActivityPhase(items[2], null, null),
  running: wbSessionActivitySnapshot(items[3], null, null, null),
  staticPlanning: wbSessionActivitySnapshot(items[4], null, null, null),
  livePlanning: wbSessionActivitySnapshot(items[4], null, {active:true}, null),
  settledChat: wbSessionActivitySnapshot(items[5], null, {
    active:true,
    activity:{kind:"reasoning", label:"phase2", detail:"deepseek-v4-flash"}
  }, null),
  finishedTool: wbSessionActivitySnapshot(items[3], null, {
    active:false,
    activity:{kind:"tool", label:"browser.navigate", detail:"completed"}
  }, null),
  liveTool: wbSessionActivitySnapshot(items[3], null, {
    active:true,
    activity:{kind:"tool", label:"browser.navigate", detail:"running"}
  }, null),
  runtimeWinsToolFailure: wbSessionActivitySnapshot(items[5], {
    progress:[], activities:[]
  }, {
    status:"failed",
    active:false,
    activity:{kind:"tool", label:"shell", failed:true}
  }, null)
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

    assert result["visible"] == ["t1", "c1", "t3"]
    assert result["overflow"] == ["t2", "t4", "c2"]
    assert result["attention"] == {
        "phase": "attention",
        "reason": "input",
        "active": False,
    }
    assert result["running"]["phase"] == "running"
    assert result["running"]["progress"] == {
        "current": 3,
        "completed": 2,
        "total": 5,
        "title": "",
        "action": "",
    }
    assert result["running"]["capabilities"]["canPause"] is True
    assert result["running"]["isLive"] is True
    assert result["staticPlanning"]["phase"] == "planning"
    assert result["staticPlanning"]["isLive"] is False
    assert result["livePlanning"]["isLive"] is True
    assert result["settledChat"]["phase"] == "completed"
    assert result["settledChat"]["isLive"] is False
    assert result["settledChat"]["activity"] is None
    assert result["finishedTool"]["activity"] is None
    assert result["liveTool"]["activity"]["label"] == "browser.navigate"
    assert result["runtimeWinsToolFailure"]["phase"] == "running"
    assert result["runtimeWinsToolFailure"]["isLive"] is True


def test_topbar_activity_controls_hover_preview_and_overflow_are_separate():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    css = (ROOT / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

    assert 'className="workbench-session-tab-more"' in shell
    assert "openSessionMenu(event, item, activity, true)" in shell
    assert "onClick={function () { if (onOpenSession) onOpenSession(item); }}" in shell
    assert "scheduleSessionPreview(event, item, activity, false)" in shell
    assert "scheduleSessionPreview(event, item, activity, true)" in shell
    assert 'morphUntil: state.phase === "completed"' in shell
    assert "setActivityClock(Date.now())" in shell
    assert "replaceTitleForMorph" in shell
    assert 'id="workbench-session-activity-preview"' in shell
    assert 'className={"workbench-session-overflow-button "' in shell
    assert 'className="workbench-session-overflow-stack"' in shell
    assert 'className="workbench-session-overflow-count"' in shell
    assert 'className="workbench-session-overflow-chevron"' in shell
    assert '<span>… {overflowTabs.length}</span>' not in shell
    assert "overflowMenuPortal" in shell
    assert "wbSessionActivityRank" in shell
    assert "wbSplitOverflowSessions" in shell
    assert 't("workbench.sessionOverflow.title", "All conversations")' in shell
    assert 't("workbench.sessionOverflow.other", "Other sessions")' in shell
    assert 't("workbench.sessionOverflow.exceptions", "Exceptions")' in shell
    assert 'className="workbench-session-overflow-divider"' in shell
    assert shell.count('className="workbench-session-overflow-group-items"') == 2
    assert "has-regular" in shell
    assert "has-exceptions" in shell
    assert '" split-scroll"' in shell
    assert ".workbench-session-overflow-menu.split-scroll" in css
    assert ".workbench-session-overflow-group-items" in css
    group_items_css = css.split(".workbench-session-overflow-group-items {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto" in group_items_css
    assert "overscroll-behavior: contain" in group_items_css
    exceptional_css = css.split(
        ".workbench-session-overflow-list.has-regular.has-exceptions .workbench-session-overflow-group.exceptional {",
        1,
    )[1].split("}", 1)[0]
    assert "min-height: 92px" in exceptional_css
    assert "max-height: min(170px, 42%)" in exceptional_css
    assert ".workbench-session-tab-more" in css
    inactive_tab_css = css.split(
        ".workbench-session-tab-group .workbench-session-tab {", 1
    )[1].split("}", 1)[0]
    assert "padding-right: 6px" in inactive_tab_css
    reserved_action_css = css.split(
        ".workbench-session-tab-group.active .workbench-session-tab,", 1
    )[1].split("}", 1)[0]
    assert ".workbench-session-tab-group:hover .workbench-session-tab" in reserved_action_css
    assert "padding-right: 28px" in reserved_action_css
    assert ".workbench-session-activity-preview" in css
    assert ".workbench-session-overflow-menu" in css
    assert "wb-session-running-pulse" in css
    assert ".workbench-session-status-dot.planning.is-live" in css
    assert "planning: state.isLive" in shell
    assert 't("workbench.sessionStatus.planningStage", "Planning stage")' in shell
    assert 't("workbench.sessionStatus.step", {' in shell
    assert '}, "Step {current}/{total}")' in shell
    assert "`llm_call` is emitted as a completed accounting event" in shell
    activity_reducer = shell.split("function onActivityEvent(data)", 1)[1].split(
        'return window.CyreneUI.require("events").subscribe(onActivityEvent)', 1
    )[0]
    assert 'next.status = "failed"' not in activity_reducer
    assert "Tool failure is local to this call" in activity_reducer
    reduced_motion = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".workbench-session-status-dot" in reduced_motion
    assert "animation: none" in reduced_motion


def test_overflow_sessions_group_exceptions_last_and_sort_each_group_by_time():
    source = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    helper = source.split("function wbOverflowSessionTime", 1)[1].split(
        "\nfunction wbRememberOpenedSessionKey", 1
    )[0]
    script = (
        "function wbOverflowSessionTime"
        + helper
        + """
const sourceItems = [
  {id: "attention-old", title: "Needs input", updatedAt: "2026-08-01T10:00:00Z", activity: {phase: "attention"}},
  {id: "planning-old", title: "Planning", updatedAt: "2026-08-02T10:00:00Z", activity: {phase: "planning"}},
  {id: "failed-new", title: "Failed", updatedAt: "2026-08-04T10:00:00Z", activity: {phase: "failed"}},
  {id: "completed-new", title: "Completed", updatedAt: "2026-08-05T10:00:00Z", activity: {phase: "completed"}},
  {id: "running-middle", title: "Running", updatedAt: "2026-08-03T10:00:00Z", activity: {phase: "running"}}
];
const groups = wbSplitOverflowSessions(sourceItems);
process.stdout.write(JSON.stringify({
  regular: groups.regular.map(item => item.id),
  exceptional: groups.exceptional.map(item => item.id),
  original: sourceItems.map(item => item.id)
}));
"""
    )
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True, cwd=ROOT
    )
    result = json.loads(completed.stdout)

    assert result["regular"] == ["completed-new", "running-middle", "planning-old"]
    assert result["exceptional"] == ["failed-new", "attention-old"]
    assert result["original"] == [
        "attention-old",
        "planning-old",
        "failed-new",
        "completed-new",
        "running-middle",
    ]


def test_overflow_menu_keeps_internal_scroll_open_and_has_no_accent_outline():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    css = (ROOT / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

    assert "function handleScroll(event)" in shell
    assert 'target.closest(\n        ".workbench-session-overflow-menu, .workbench-session-menu"' in shell
    assert 'window.addEventListener("scroll", handleScroll, true)' in shell
    assert 'window.removeEventListener("scroll", handleScroll, true)' in shell
    focus_rule = css.split(".workbench-session-overflow-button:focus,", 1)[1].split("}", 1)[0]
    assert "outline: none" in focus_rule
    assert "box-shadow: none" in focus_rule


def test_topbar_overlay_captures_browser_frame_before_hiding_native_view():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    viewport = (
        ROOT / "src/webui/frontend/shared/browser/viewport.jsx"
    ).read_text(encoding="utf-8")

    coordinator = shell.split("function wbSetBrowserOverlayObscured", 1)[1].split(
        "// Other classic-script bundles", 1
    )[0]
    assert "preview: true" in coordinator
    assert "onCaptureStarted" in coordinator
    assert "onReady: hideNativeAfterPreview" in coordinator
    assert coordinator.index('window.dispatchEvent(new CustomEvent("workbench:browser-obscured"') < coordinator.index(
        "if (!captureStarted) hideNativeAfterPreview()"
    )
    assert "setNativeObscured(false).finally" in coordinator
    assert 'typeof detail.onCaptureStarted === "function"' in viewport
    assert "detail.onCaptureStarted()" in viewport
    assert "setOverlayPreview(preview)" in viewport
    assert "restoreNativeAfterPreview()" in viewport


def test_overflow_count_uses_the_i18n_parameter_position():
    i18n = (ROOT / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")
    helper = i18n.split("function workbenchInterpolate", 1)[1].split(
        "\nfunction workbenchToolName", 1
    )[0]
    script = (
        'var WORKBENCH_TRANSLATIONS={zh:{"workbench.sessionOverflow.count":"另有 {count} 个"},en:{}};'
        'var workbenchI18nLang="zh";'
        "function workbenchInterpolate"
        + helper
        + 'process.stdout.write(workbenchT("workbench.sessionOverflow.count", {count: 8}, "{count} more"));'
    )
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert completed.stdout == "另有 8 个"


def test_task_summary_exposes_compact_plan_progress_for_the_topbar():
    from cyrene.workbench.runtime import _workbench_session_summary

    summary = _workbench_session_summary(
        {
            "id": "task-1",
            "projectId": "project-1",
            "status": "running",
            "plan": [
                {"title": "Done", "status": "completed"},
                {"title": "Inspect loader", "status": "running", "currentAction": "Reading files"},
                {"title": "Verify", "status": "pending"},
            ],
        }
    )

    assert summary["planStepCount"] == 3
    assert summary["planCompletedCount"] == 1
    assert summary["planCurrentIndex"] == 2
    assert summary["planCurrentTitle"] == "Inspect loader"
    assert summary["planCurrentAction"] == "Reading files"


def test_chat_summary_exposes_live_run_status_without_stale_running_state():
    from cyrene.workbench.chat import _public_chat_light

    summary = _public_chat_light(
        {
            "id": "topbar-status-test-chat",
            "projectId": "project-1",
            "status": "running",
            "messages": [],
        }
    )

    assert summary["status"] == "running"
    assert summary["runStatus"] == "idle"


def test_chat_summary_preserves_failed_cancelled_and_awaiting_run_outcomes():
    from cyrene.workbench.chat import _public_chat_light

    base = {
        "projectId": "project-1",
        "status": "idle",
        "messages": [{"role": "user", "content": "hello"}],
    }
    failed = _public_chat_light({
        **base,
        "id": "topbar-last-failed-chat",
        "lastRun": {"status": "error", "outcome": "error"},
    })
    cancelled = _public_chat_light({
        **base,
        "id": "topbar-last-cancelled-chat",
        "lastRun": {"status": "cancelled", "terminationReason": "user_interrupted"},
    })
    awaiting = _public_chat_light({
        **base,
        "id": "topbar-last-awaiting-chat",
        "lastRun": {"status": "done", "outcome": "awaiting"},
    })

    assert failed["runStatus"] == "failed"
    assert cancelled["runStatus"] == "cancelled"
    assert awaiting["runStatus"] == "awaiting_user"


def test_chat_run_outcome_projection_is_persisted_for_list_and_topbar(monkeypatch):
    from cyrene.workbench import chat as chat_mod

    payload = {
        "chats": [{
            "id": "chat-outcome-projection",
            "status": "running",
            "updatedAt": "2026-08-08T09:00:00+00:00",
            "messages": [],
        }]
    }
    written = []
    monkeypatch.setattr(chat_mod, "_read_chats_store", lambda: payload)
    monkeypatch.setattr(chat_mod, "_write_chats_store", lambda value: written.append(value))

    chat_mod._record_chat_run_outcome(
        "chat-outcome-projection",
        run_id="run-1",
        status="error",
        termination_reason="driver_error",
        outcome_kind="error",
        created_at="2026-08-08T10:00:00+00:00",
    )

    chat = payload["chats"][0]
    assert chat["status"] == "idle"
    assert chat["lastRun"]["id"] == "run-1"
    assert chat["lastRun"]["status"] == "error"
    assert chat["lastRun"]["outcome"] == "error"
    assert written == [payload]


def test_session_activity_reducer_tracks_parallel_work_and_resets_between_runs():
    source = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    status_helpers = source.split("function wbActivityStatusIsActive", 1)[1].split(
        "\nfunction wbSessionActivityPhase", 1
    )[0]
    helper = source.split("function wbArgsPreview", 1)[1].split(
        "\nfunction wbActorLabel", 1
    )[0]
    script = (
        "function wbActivityStatusIsActive"
        + status_helpers
        + "function wbArgsPreview"
        + helper
        + """
let state = {};
state = wbReduceSessionActivity(state, {type:"tool_call_started", session_id:"c1", runId:"r1", tool_call_id:"a", tool:"shell", timestamp:"2026-08-08T10:00:00Z"});
state = wbReduceSessionActivity(state, {type:"tool_call_started", session_id:"c1", runId:"r1", tool_call_id:"b", tool:"browser", timestamp:"2026-08-08T10:00:01Z"});
state = wbReduceSessionActivity(state, {type:"tool_call_finished", session_id:"c1", runId:"r1", tool_call_id:"a", tool:"shell", timestamp:"2026-08-08T10:00:02Z"});
const afterOneFinished = {active:state.active, tools:Object.keys(state.activeTools)};
state = wbReduceSessionActivity(state, {type:"subagent_update", session_id:"c1", runId:"r1", agent_id:"research", status:"running", task:"Search"});
state = wbReduceSessionActivity(state, {type:"tool_call_finished", session_id:"c1", runId:"r1", tool_call_id:"b", tool:"browser"});
const subagentSurvives = state.active;
state = wbReduceSessionActivity(state, {type:"session_update", session_id:"c1", runId:"r1", status:"error"});
const terminal = {active:state.active, tools:Object.keys(state.activeTools), activity:state.activity, agent:state.agents.research.status};
state = wbReduceSessionActivity(state, {type:"session_update", session_id:"c1", runId:"r2", status:"running"});
const nextRun = {active:state.active, activity:state.activity, agents:Object.keys(state.agents), status:state.status};
process.stdout.write(JSON.stringify({afterOneFinished, subagentSurvives, terminal, nextRun}));
"""
    )
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True, cwd=ROOT
    )
    result = json.loads(completed.stdout)

    assert result["afterOneFinished"] == {"active": True, "tools": ["b"]}
    assert result["subagentSurvives"] is True
    assert result["terminal"] == {
        "active": False,
        "tools": [],
        "activity": None,
        "agent": "done",
    }
    assert result["nextRun"] == {
        "active": True,
        "activity": None,
        "agents": [],
        "status": "running",
    }


def test_session_activity_uses_newer_durable_status_and_maps_terminal_variants():
    source = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    terminal_helper = source.split("function wbActivityStatusIsTerminal", 1)[1].split(
        "\nfunction wbSessionActivityPhase", 1
    )[0]
    helper = source.split("function wbSessionActivityPhase", 1)[1].split(
        "\nfunction wbLatestRuntimeActivity", 1
    )[0]
    script = (
        "function wbActivityStatusIsTerminal"
        + terminal_helper
        + "function wbSessionActivityPhase"
        + helper
        + """
const completed = wbSessionActivityPhase({kind:"chat", source:{runStatus:"completed", updatedAt:"2026-08-08T10:00:02Z", messageCount:2}}, null, {status:"failed", statusAt:Date.parse("2026-08-08T10:00:01Z"), lastEventAt:Date.parse("2026-08-08T10:00:01Z"), active:true, activeTools:{old:{label:"shell"}}});
const sameRunLateEvent = wbSessionActivityPhase({kind:"chat", source:{runStatus:"completed", updatedAt:"2026-08-08T10:00:02Z", lastRun:{id:"r1"}, messageCount:2}}, null, {runKey:"r1", status:"running", statusAt:Date.parse("2026-08-08T10:00:03Z"), lastEventAt:Date.parse("2026-08-08T10:00:03Z"), active:true, activeTools:{old:{label:"shell"}}});
const newerRun = wbSessionActivityPhase({kind:"chat", source:{runStatus:"completed", updatedAt:"2026-08-08T10:00:02Z", lastRun:{id:"r1"}, messageCount:2}}, null, {runKey:"r2", status:"running", statusAt:Date.parse("2026-08-08T10:00:03Z"), lastEventAt:Date.parse("2026-08-08T10:00:03Z"), active:true, activeTools:{next:{label:"shell"}}});
const cancelled = wbSessionActivityPhase({kind:"chat", source:{runStatus:"cancelled"}}, null, null);
const awaiting = wbSessionActivityPhase({kind:"chat", source:{runStatus:"awaiting_user"}}, null, null);
process.stdout.write(JSON.stringify({completed, sameRunLateEvent, newerRun, cancelled, awaiting}));
"""
    )
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True, cwd=ROOT
    )
    result = json.loads(completed.stdout)

    assert result["completed"]["phase"] == "completed"
    assert result["sameRunLateEvent"]["phase"] == "completed"
    assert result["sameRunLateEvent"]["active"] is False
    assert result["newerRun"]["phase"] == "running"
    assert result["newerRun"]["active"] is True
    assert result["cancelled"]["phase"] == "cancelled"
    assert result["awaiting"] == {
        "phase": "attention",
        "reason": "input",
        "active": False,
    }


def test_chat_runtime_broadcasts_terminal_lifecycle_to_topbar():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(encoding="utf-8")

    assert 'new CustomEvent("cyrene:wbc-chat-lifecycle"' in chat
    assert 'publishLifecycle(chatId, "completed", event)' in chat
    assert 'publishLifecycle(chatId, "awaiting_user", event)' in chat
    assert 'publishLifecycle(chatId, "cancelled", event)' in chat
    assert 'window.addEventListener("cyrene:wbc-chat-lifecycle", onChatLifecycle)' in shell
    assert 'setInterval(refreshLiveChats, 2500)' in shell
    assert 'reloadRecentChats(store.projects || [])' in shell
    assert 'chatRuntimeEngine.subscribeSummary' in shell


def test_tab_menus_use_cyrene_context_menu_surface():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    css = (ROOT / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

    assert "workbench-session-context-menu" in shell
    assert ".workbench-session-context-menu" in css
    context_css = css.split(".workbench-session-context-menu,", 1)[1].split("}", 1)[0]
    assert "border-radius: 14px" in context_css
    assert '"--wb-card-bg-strong"' in shell
    assert "...sessionMenu.portalTheme" in shell
    assert "...overflowMenu.portalTheme" in shell
    assert "...resourceMenu.portalTheme" in shell
    assert "background: var(--wb-card-bg-strong, var(--wb-card-bg" in context_css
    assert "sessionMenuCurrentActivity" in shell
    assert '["ArrowDown", "ArrowUp", "Home", "End"]' in shell
    assert "items[nextIndex].focus()" in shell


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
