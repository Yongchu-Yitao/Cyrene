import json
import subprocess
from pathlib import Path


def test_global_search_times_out_and_ignores_stale_requests():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "shared" / "search" / "overlay.jsx").read_text(
        encoding="utf-8"
    )

    assert "SEARCH_REQUEST_TIMEOUT_MS = 10000" in source
    assert "requestSeqRef.current !== requestId" in source
    assert "controller.__cyreneTimedOut = true" in source
    assert "function shouldIgnoreSearchResponse" in source
    assert 'if (controller.__cyreneTimedOut) setStatus("error")' in source
    assert 'e.name === "AbortError" && !controller.__cyreneTimedOut' in source


def test_new_workbench_chat_reuses_create_response_without_refetching():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    shell = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )

    assert 'var skipNextHydrationChatIdRef = useWbcRef("");' in source
    assert "skipNextHydrationChatIdRef.current = chat.id;" in source
    assert "skipNextHydrationChatIdRef.current === activeChatId" in source
    assert "newChatRequestId: newChatRequestId" in shell
    assert "handledNewChatRequestIdRef" in source
    assert "handleCreateChat();" in source


def test_chat_sidebar_card_order_helpers_normalize_and_move_cards():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    helper_source = "function wbcNormalizeSideCardOrder(" + source.split(
        "function wbcNormalizeSideCardOrder(", 1
    )[1].split("function wbcLoadSideCardOrder", 1)[0]
    helper_source += "function wbcMoveSideCard(" + source.split(
        "function wbcMoveSideCard(", 1
    )[1].split("function WbcSortableCardStack", 1)[0]
    script = f"""
eval({json.dumps(helper_source)});
const defaults = ["summary", "session", "context", "actions"];
const result = {{
  normalized: wbcNormalizeSideCardOrder(defaults, ["context", "missing", "context", "summary"]),
  before: wbcMoveSideCard(defaults, "actions", "session", "before"),
  after: wbcMoveSideCard(defaults, "summary", "context", "after"),
  unchanged: wbcMoveSideCard(defaults, "summary", "summary", "before")
}};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result["normalized"] == ["context", "summary", "session", "actions"]
    assert result["before"] == ["summary", "actions", "session", "context"]
    assert result["after"] == ["session", "context", "summary", "actions"]
    assert result["unchanged"] == ["summary", "session", "context", "actions"]


def test_chat_rail_order_helpers_keep_new_chats_first_and_move_existing_chats():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    helper_source = "function wbcNormalizeChatOrder(" + source.split(
        "function wbcNormalizeChatOrder(", 1
    )[1].split("function wbcLoadChatOrder", 1)[0]
    helper_source += "function wbcMoveChatOrder(" + source.split(
        "function wbcMoveChatOrder(", 1
    )[1].split("function WbcRail", 1)[0]
    script = f"""
eval({json.dumps(helper_source)});
const defaults = ["new", "alpha", "beta", "gamma"];
const result = {{
  normalized: wbcNormalizeChatOrder(defaults, ["beta", "missing", "beta", "alpha"]),
  before: wbcMoveChatOrder(defaults, "gamma", "alpha", "before"),
  after: wbcMoveChatOrder(defaults, "new", "beta", "after"),
  unchanged: wbcMoveChatOrder(defaults, "beta", "beta", "before"),
  groupBeforeChat: wbcMoveChatOrderBlock(
    ["alpha", "beta", "single", "gamma", "delta", "last"],
    ["gamma", "delta"],
    ["single"],
    "before"
  ),
  groupAfterGroup: wbcMoveChatOrderBlock(
    ["alpha", "single", "gamma", "beta", "delta", "last"],
    ["alpha", "beta"],
    ["gamma", "delta"],
    "after"
  ),
  groupToEnd: wbcMoveChatOrderBlock(
    ["alpha", "beta", "single", "last"],
    ["alpha", "beta"],
    [],
    "after"
  )
}};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result["normalized"] == ["new", "gamma", "beta", "alpha"]
    assert result["before"] == ["new", "gamma", "alpha", "beta"]
    assert result["after"] == ["alpha", "beta", "new", "gamma"]
    assert result["unchanged"] == ["new", "alpha", "beta", "gamma"]
    assert result["groupBeforeChat"] == ["alpha", "beta", "gamma", "delta", "single", "last"]
    assert result["groupAfterGroup"] == ["single", "gamma", "delta", "alpha", "beta", "last"]
    assert result["groupToEnd"] == ["single", "last", "alpha", "beta"]


def test_chat_rail_group_helpers_create_extend_and_normalize_groups():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    helper_source = "function wbcNormalizeChatGroups(" + source.split(
        "function wbcNormalizeChatGroups(", 1
    )[1].split("function WbcRail", 1)[0]
    script = f"""
function wbcT(_key, fallback) {{ return fallback; }}
eval({json.dumps(helper_source)});
const created = wbcCreateChatGroup([], "beta", "alpha", "group_one");
const extended = wbcCreateChatGroup(created, "gamma", "alpha", "unused");
const moved = wbcCreateChatGroup(
  [
    {{ id: "old", title: "Old", chatIds: ["alpha", "beta"] }},
    {{ id: "target", title: "Target", chatIds: ["gamma", "delta"] }}
  ],
  "beta",
  "gamma",
  "unused"
);
const removedFromThree = wbcRemoveChatFromGroups(
  [{{ id: "three", title: "Three", chatIds: ["alpha", "beta", "gamma"] }}],
  "beta"
);
const dissolvedAtOne = wbcRemoveChatFromGroups(
  [{{ id: "two", title: "Two", chatIds: ["alpha", "beta"] }}],
  "beta"
);
const normalized = wbcNormalizeChatGroups(
  [
    {{ id: "invalid", title: "Invalid", chatIds: ["alpha", "missing"] }},
    {{ id: "kept", title: "Kept", summary: "Saved summary", titleLocked: true, metadataLang: "en", metadataChatIds: "alpha|beta", chatIds: ["alpha", "beta", "beta"] }},
    {{ id: "duplicate", title: "Duplicate", chatIds: ["beta", "gamma"] }}
  ],
  ["alpha", "beta", "gamma"]
);
process.stdout.write(JSON.stringify({{ created, extended, moved, removedFromThree, dissolvedAtOne, normalized }}));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result["created"] == [
        {
            "id": "group_one",
            "title": "New chat group",
            "summary": "",
            "titleLocked": False,
            "metadataLang": "",
            "metadataChatIds": "",
            "chatIds": ["alpha", "beta"],
        }
    ]
    assert result["extended"][0]["chatIds"] == ["alpha", "beta", "gamma"]
    assert result["moved"] == [
        {"id": "target", "title": "Target", "chatIds": ["gamma", "delta", "beta"]}
    ]
    assert result["removedFromThree"] == [
        {"id": "three", "title": "Three", "chatIds": ["alpha", "gamma"]}
    ]
    assert result["dissolvedAtOne"] == []
    assert result["normalized"] == [
        {
            "id": "kept",
            "title": "Kept",
            "summary": "Saved summary",
            "titleLocked": True,
            "metadataLang": "en",
            "metadataChatIds": "alpha|beta",
            "chatIds": ["alpha", "beta"],
        }
    ]


def test_workbench_chat_group_drop_uses_one_enclosing_frame_without_stacking():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    rail = source.split("function WbcRail(", 1)[1].split(
        "// Conversation main (column 3)", 1
    )[0]
    assert "WBC_CHAT_GROUPS_PREFIX" in source
    assert "function wbcCreateChatGroup(" in source
    assert "function wbcRemoveChatFromGroups(" in source
    assert 'mode: "group"' in rail
    assert "sourceGroupId" in rail
    assert "commitUngroupDrop(dragState.movingId)" in rail
    assert "function updateDragState(next)" in rail
    assert 'dragState.mode === "group"' in rail
    assert 'className="wbc-chat-group wbc-chat-group-preview drop-ready"' in rail
    assert 'wbcT("workbenchChat.releaseToGroup", "Release to create a chat group")' in rail
    assert 'wbcT("workbenchChat.releaseToExistingGroup", "Release to add to this chat group")' in rail
    assert 'className={"wbc-chat-group-content" + (isCollapsed ? " collapsed" : " expanded")}' in rail
    assert 'inert={isCollapsed ? "" : undefined}' in rail
    assert 'className="wbc-chat-group-content-inner"' in rail
    assert 'className="wbc-chat-group-children"' in rail
    assert "function openGroupMenu(event)" in rail
    assert "function toggleGroupMenu(event)" in rail
    assert "onContextMenu={openGroupMenu}" in rail
    assert "onClick={toggleGroupMenu}" in rail
    assert "setMenuId(groupMenuId)" in rail
    assert rail.count('wbcT("workbenchChat.groupRename", "Rename group")') == 1
    assert rail.count('wbcT("workbenchChat.groupDissolve", "Dissolve group")') == 1
    assert ".wbc-chat-group {" in styles
    assert ".wbc-chat-group.drop-ready {" in styles
    assert ".wbc-chat-group-drop-hint {" in styles
    assert "grid-template-rows: 1fr;" in styles
    assert "grid-template-rows: 0fr;" in styles
    assert ".wbc-chat-group-chevron.expanded svg" in styles
    assert "WBC_CHAT_GROUP_DRAG_MIME" in source
    assert "function wbcMoveChatOrderBlock(" in source
    assert 'dragKind: "group"' in rail
    assert 'mode: "group-reorder"' in rail
    assert 'wbcSetChatGroupDrag(event, group, projectId)' in rail
    assert 'draggable="true"' in rail.split("function renderGroupFrame", 1)[1]
    assert "commitGroupOrder(order" in rail
    assert ".wbc-chat-group.dragging {" in styles
    assert '{group.chatIds.length + (groupDropReady ? 1 : 0)}' in rail
    assert '<span className="wbc-chat-group-icon" aria-hidden="true">2</span>' in rail
    assert 'wbcT("workbenchChat.groupCount", "{count} chats"' not in rail
    assert ".wbc-chat-group-chevron:focus-visible" in styles
    assert "font-variant-numeric: tabular-nums;" in styles
    assert "WorkbenchChatModel.generateChatGroupMetadata" in rail
    assert "if (!groupBackendReady) return;" in rail
    assert "projectId: projectId" in rail
    assert "signature: signature" in rail
    assert "groupBackendWriteRef.current.chain.catch" in rail
    assert "var persistedGroup = result.group;" in rail
    assert "title: String(persistedGroup.title || candidate.title)" in rail
    assert 'type: "metadata"' not in rail.split("function refreshChatGroupMetadata", 1)[1].split("function commitGroupDrop", 1)[0]
    assert "titleLocked: true" in rail
    assert "metadataChatIds: signature" in rail
    assert "group.metadataLang !== groupMetadataLang" in rail
    assert "<WbcHoverMarquee text={group.title}" in rail
    assert "group.summary || (groupMetadataPending[group.id]" in rail
    assert "@keyframes wbc-hover-marquee" in styles
    summary_css = styles.split(".wbc-chat-group-summary {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in summary_css
    assert "align-items: center;" in summary_css
    assert "justify-content: flex-start;" in summary_css
    assert "padding: 2px 3px;" in summary_css
    assert "padding-left: 31px;" not in summary_css
    assert ".wbc-chat-group:not(.collapsed) .wbc-chat-group-summary" not in styles
    group_css = styles.split(".wbc-chat-group {", 1)[1].split("}", 1)[0]
    assert "var(--wb-active-bg) 24%" in group_css
    child_active_css = styles.split(
        ".wbc-chat-group .wbc-chat-group-child.active,", 1
    )[1].split("}", 1)[0]
    assert "var(--wb-accent) 12%" in child_active_css
    assert "border-color:" in child_active_css
    assert "inset 3px 0 0" not in child_active_css
    assert "stack" not in styles.split(".wbc-chat-group {", 1)[1].split("/* ---- main column ---- */", 1)[0].lower()


def test_overflowing_chat_card_and_topbar_tab_text_scrolls_on_hover():
    root = Path(__file__).resolve().parent.parent
    chat_source = (
        root / "src" / "webui" / "frontend" / "workbench-chat.jsx"
    ).read_text(encoding="utf-8")
    shell_source = (
        root / "src" / "webui" / "frontend" / "workbench.jsx"
    ).read_text(encoding="utf-8")
    styles = (
        root / "src" / "webui" / "frontend" / "workbench.css"
    ).read_text(encoding="utf-8")

    assert "function WbcHoverMarquee(" in chat_source
    assert '<WbcHoverMarquee text={chat.title || wbcT(' in chat_source
    assert '<WbcHoverMarquee text={chat.preview || wbcT(' in chat_source
    assert '<WbcHoverMarquee text={item.title} className="workbench-session-tab-title" />' in shell_source
    assert 'metrics.overflow ? " overflow" : ""' in chat_source
    assert ".wbc-hover-marquee.overflow:hover .wbc-hover-marquee-track" in styles
    assert "animation: wbc-hover-marquee" in styles
    assert "animation-timing-function: cubic-bezier(.45, 0, 1, 1);" in styles
    assert "infinite alternate" not in styles.split("@keyframes wbc-hover-marquee", 1)[0].rsplit(".wbc-hover-marquee", 1)[-1]
    marquee_keyframes = styles.split("@keyframes wbc-hover-marquee", 1)[1].split("@media", 1)[0]
    assert "88%, 100%" in marquee_keyframes
    assert "56%" not in marquee_keyframes
    assert "prefers-reduced-motion: reduce" in styles


def test_chat_sidebar_context_is_flat_and_overview_is_integrated():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert 'className="wbc-overview-compact"' in source
    assert 'className="workbench-side-section wbc-overview-session"' in source
    assert '<WbcContextUsage data={liveData} compact={true} />' in source
    overview_source = source.split("function WbcOverviewTab(", 1)[1].split(
        "function wbcBlockLabel(", 1
    )[0]
    assert '<WbcOverviewUsage usage={usage} />' in overview_source
    assert "WbcQuickActionItems" not in overview_source
    assert "wbc-overview-actions" not in overview_source
    context_source = source.split("function WbcContextTab(", 1)[1].split(
        "function WbcArtifactsTab(", 1
    )[0]
    assert 'className="wbc-context-sections"' in context_source
    assert '<WbcContextBlockList chat={chat} running={!!runtime} compact={false} />' in context_source
    assert 'className: "wbc-context-detail"' in source
    assert '<WbcInboxCard chat={chat} running={!!runtime} hideTitle={true} />' in context_source
    assert "usedToolPackages.length === 0" in context_source
    assert "workbenchChat.noUsedToolPackages" in context_source
    assert 'className="workbench-side-section wbc-context-stats"' in context_source
    assert "WbcSortableCardStack" not in context_source
    context_css = styles.split(".wbc-context-sections {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column;" in context_css


def test_workbench_chat_cards_are_borderless_and_compact_overview_is_flat():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    chat_card_css = styles.split(".wbc-chat-card {", 1)[1].split("}", 1)[0]
    compact_overview_css = styles.split(
        ".wbc-overview-compact > .workbench-side-section {", 1
    )[1].split("}", 1)[0]
    active_chat_css = styles.split(
        ".wbc-chat-card.active,\n.wbc-chat-card.menu-open,", 1
    )[1].split("}", 1)[0]
    focus_chat_css = styles.split(
        ".wbc-chat-card:focus,\n.wbc-chat-card:focus-visible {", 1
    )[1].split("}", 1)[0]
    page_css = styles.split(".wbc-page {", 1)[1].split("}", 1)[0]

    assert "border: 0;" in chat_card_css
    assert "box-shadow: var(--wbc-control-shadow);" in chat_card_css
    assert (
        ".workbench-side-section {\n"
        "  border: 0;\n"
        "  padding: 12px;\n"
        "  background: var(--wb-card-bg);\n"
        "  box-shadow: var(--wbc-control-shadow);"
    ) in styles
    assert "border: 0;" in compact_overview_css
    assert "background: transparent;" in compact_overview_css
    assert "box-shadow: none;" in compact_overview_css
    assert "0 1px 2px rgba(15, 23, 42, 0.028)" in page_css
    assert "0 5px 14px rgba(15, 23, 42, 0.02)" in page_css
    assert "border-color:" not in active_chat_css
    assert "outline: 0;" in focus_chat_css


def test_workbench_chat_inputs_are_borderless():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    search_css = styles.split(".wbc-search input {", 1)[1].split("}", 1)[0]
    search_focus_css = styles.split(".wbc-search input:focus {", 1)[1].split("}", 1)[0]
    composer_css = styles.split(".wbc-composer-box {", 1)[1].split("}", 1)[0]
    assert "border: 0;" in search_css
    assert "box-shadow: var(--wbc-control-shadow);" in search_css
    assert "border-color:" not in search_focus_css
    assert "border: 0;" in composer_css
    assert "box-shadow: var(--wbc-control-shadow);" in composer_css
    assert ".wbc-composer-box:focus-within {" not in styles


def test_main_chat_composer_uses_a_glass_dock_without_changing_the_input_card():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    stage_css = styles.split(".wbc-thread-stage {", 1)[1].split("}", 1)[0]
    dock_css = styles.split(".wbc-main > .wbc-composer {", 1)[1].split("}", 1)[0]
    page_glass_css = styles.split(".wbc-page::before {", 1)[1].split("}", 1)[0]
    input_css = styles.split(".wbc-composer-box {", 1)[1].split("}", 1)[0]
    scroll_css = styles.split(".wbc-scroll-to-bottom {", 1)[1].split("}", 1)[0]

    assert "--wbc-thread-inset-bottom: calc(198px * var(--wb-ui-font-scale, 1));" in stage_css
    assert "position: absolute;" in dock_css
    assert "inset: auto 0 0;" in dock_css
    assert "background:" not in dock_css
    assert "background: color-mix(in srgb, var(--wb-topbar-bg) 58%, transparent);" in page_glass_css
    assert "backdrop-filter: blur(32px) saturate(170%) contrast(102%);" in page_glass_css
    assert "linear-gradient(to top, #000 0%, #000 82%, transparent 100%)" in page_glass_css
    assert ".wbc-main > .wbc-composer::before {" not in styles
    assert "background: var(--wb-card-bg);" in input_css
    assert "border-radius: 14px;" in input_css
    assert "padding: 10px 12px 8px;" in input_css
    assert "bottom: calc(var(--wbc-thread-inset-bottom) - 12px);" in scroll_css


def test_hidden_chat_sidebar_slightly_widens_and_centers_the_conversation_lane():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    page_css = styles.split(".wbc-page {", 1)[1].split("}", 1)[0]
    hidden_css = styles.split(".wbc-page.wbc-side-hidden {", 1)[1].split("}", 1)[0]
    stage_css = styles.split(".wbc-thread-stage {", 1)[1].split("}", 1)[0]
    dock_css = styles.split(".wbc-main > .wbc-composer {", 1)[1].split("}", 1)[0]

    assert "--wbc-reclaimed-side-width: 0px;" in page_css
    assert "--wbc-conversation-shift: 0px;" in page_css
    assert "--wbc-side-track-width: var(--wb-right-w, 350px);" in page_css
    assert "grid-template-columns: var(--wbc-rail-width) minmax(var(--wbc-main-min-width), 1fr) var(--wbc-side-track-width);" in page_css
    assert "transition: grid-template-columns 500ms cubic-bezier(.22, 1.16, .36, 1);" in page_css
    assert "--wbc-collapsed-lane-growth: clamp(64px, 5vw, 96px);" in page_css
    assert "--wbc-side-track-width: 0px;" in hidden_css
    assert "--wbc-reclaimed-side-width: calc(var(--wb-right-w, 350px) - var(--wbc-collapsed-lane-growth));" in hidden_css
    assert "--wbc-conversation-shift: calc(var(--wbc-reclaimed-side-width) / 2);" in hidden_css
    for lane_css in (stage_css, dock_css):
        assert "width: calc(100% - var(--wbc-reclaimed-side-width));" in lane_css
    assert "left: var(--wbc-conversation-shift);" in stage_css
    assert "transition: width 420ms cubic-bezier(.22, 1.24, .36, 1), left 420ms cubic-bezier(.22, 1.24, .36, 1);" in stage_css
    assert "transform: translateX(var(--wbc-conversation-shift));" in dock_css
    assert "transition: width 420ms cubic-bezier(.22, 1.24, .36, 1), transform 420ms cubic-bezier(.22, 1.24, .36, 1);" in dock_css

    compact_css = styles.split("@media (max-width: 980px) {", 1)[1].split("}", 3)
    assert any("--wbc-reclaimed-side-width: 0px;" in block for block in compact_css)
    hidden_side_css = styles.split(".wbc-page.wbc-side-hidden .wbc-side {", 1)[1].split("}", 1)[0]
    assert "display: none;" not in hidden_side_css
    assert "opacity: 0;" in hidden_side_css
    assert "visibility: hidden;" in hidden_side_css
    assert "@media (prefers-reduced-motion: reduce)" in styles


def test_workbench_chat_rails_use_hidden_scrollbars():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    chat_list_css = styles.split(".wbc-chat-list {", 1)[1].split("}", 1)[0]
    chat_scrollbar_css = styles.split(
        ".wbc-chat-list::-webkit-scrollbar {", 1
    )[1].split("}", 1)[0]
    side_body_css = styles.split(".wbc-side-body {", 1)[1].split("}", 1)[0]
    side_scrollbar_css = styles.split(
        ".wbc-side-body::-webkit-scrollbar {", 1
    )[1].split("}", 1)[0]
    thread_css = styles.split(".wbc-thread {", 1)[1].split("}", 1)[0]
    thread_scrollbar_css = styles.split(
        ".wbc-thread::-webkit-scrollbar {", 1
    )[1].split("}", 1)[0]

    assert "overflow-y: auto;" in chat_list_css
    assert "scrollbar-width: none;" in chat_list_css
    assert "width: 0;" in chat_scrollbar_css
    assert "height: 0;" in chat_scrollbar_css
    assert "overflow-y: auto;" in side_body_css
    assert "scrollbar-width: none;" in side_body_css
    assert "width: 0;" in side_scrollbar_css
    assert "height: 0;" in side_scrollbar_css
    assert "overflow-y: auto;" in thread_css
    assert "scrollbar-width: none;" in thread_css
    assert "width: 0;" in thread_scrollbar_css
    assert "height: 0;" in thread_scrollbar_css


def test_global_topbar_is_frosted_and_conversation_header_panel_is_removed():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    topbar_css = styles.split(".workbench-topbar {", 1)[1].split("}", 1)[0]
    thread_stage_css = styles.split(".wbc-thread-stage {", 1)[1].split("}", 1)[0]
    main = chat.split("function WbcMain(", 1)[1].split("function WbcHeader(", 1)[0]

    assert "background: color-mix(in srgb, var(--wb-topbar-bg) 58%, transparent);" in topbar_css
    assert "backdrop-filter: blur(32px) saturate(170%) contrast(102%);" in topbar_css
    assert "border-bottom: 1px solid color-mix(in srgb, var(--wb-line) 64%, transparent);" in topbar_css
    assert '<WbcHeader' not in main
    assert 'className="wbc-header"' not in main
    assert 'className="wbc-top-glass"' not in chat
    assert "position: fixed;" in topbar_css
    assert "inset: 0 0 auto;" in topbar_css
    assert "--wbc-thread-inset-top: 76px;" in thread_stage_css


def test_workbench_chat_rail_keeps_its_own_fixed_header_surface():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    rail_css = styles.split(".wbc-rail {", 1)[1].split("}", 1)[0]
    rail_glass_css = styles.split(".wbc-rail-glass {", 1)[1].split("}", 1)[0]
    rail_glass_surface_css = styles.split(".wbc-rail-glass::before {", 1)[1].split("}", 1)[0]
    rail_toolbar_css = styles.split(".wbc-rail-toolbar {", 1)[1].split("}", 1)[0]
    new_chat_css = styles.split(".wbc-new-chat-btn {", 1)[1].split("}", 1)[0]
    search_input_css = styles.split(".wbc-search input {", 1)[1].split("}", 1)[0]
    chat_list_css = styles.split(".wbc-chat-list {", 1)[1].split("}", 1)[0]

    assert 'className="wbc-rail-glass"' in source
    assert 'className="wbc-top-glass"' not in source
    assert "--wbc-rail-overlay-height: calc(48px * var(--wb-ui-font-scale, 1));" in rail_css
    assert "--wbc-rail-content-inset: calc(var(--wbc-rail-overlay-height) + 8px);" in rail_css
    assert "padding: 8px 12px;" in rail_glass_css
    page_css = styles.split(".wbc-page {", 1)[1].split("}", 1)[0]
    page_glass_css = styles.split(".wbc-page::before {", 1)[1].split("}", 1)[0]
    assert "background: transparent;" in rail_css
    assert "padding: 0 12px;" in rail_css
    assert "padding: 0 12px 14px;" not in rail_css
    assert "z-index: 21;" in rail_css
    assert "--wbc-shared-glass-rail-width: calc(var(--wbc-rail-width) + 26px);" in page_css
    assert "--wbc-shared-glass-rail-top-inset:" in page_css
    assert "background: color-mix(in srgb, var(--wb-topbar-bg) 58%, transparent);" in page_glass_css
    assert "backdrop-filter: blur(32px) saturate(170%) contrast(102%);" in page_glass_css
    assert "100% var(--wbc-shared-glass-height);" in page_glass_css
    assert "mask-position: left bottom, left bottom;" in page_glass_css
    assert "calc(100% - var(--wbc-shared-glass-rail-width))" not in page_glass_css
    assert "mask-composite: add;" in page_glass_css
    assert "isolation: isolate;" in rail_css
    assert "background: transparent;" in rail_glass_css
    assert "background: color-mix(in srgb, var(--wb-topbar-bg) 58%, transparent);" in rail_glass_surface_css
    assert "backdrop-filter: blur(32px) saturate(170%) contrast(102%);" in rail_glass_surface_css
    assert "mask-image: linear-gradient(to right" in rail_glass_surface_css
    assert ".wbc-rail::before {" not in styles
    assert "display: flex;" in rail_toolbar_css
    assert "align-items: center;" in rail_toolbar_css
    assert "gap: 8px;" in rail_toolbar_css
    assert "width: 32px;" in new_chat_css
    assert "height: 32px;" in new_chat_css
    assert 'className="workbench-icon-btn wbc-new-chat-btn"' in source
    assert 'aria-label={wbcT("workbenchChat.newChat"' in source
    rail_markup = source.split('<div className="wbc-rail-glass">', 1)[1].split(
        "{menuId &&", 1
    )[0]
    assert 'workbenchChat.railTitle' not in rail_markup
    assert '<span>{wbcT("workbenchChat.newChat"' not in rail_markup
    assert "height: 32px;" in search_input_css
    assert "border-right: 0;" in rail_css
    assert ".wbc-rail::after {" not in styles
    assert "position: relative;" in chat_list_css
    assert "z-index: 21;" in chat_list_css
    assert "padding: calc(58px + var(--wbc-rail-content-inset)) 0 8px;" in chat_list_css


def test_collapsed_right_sidebar_restore_control_lives_in_the_global_topbar():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    topbar = source.split("function WorkbenchTopbar(", 1)[1].split(
        "function WorkbenchNotificationCenter(", 1
    )[0]
    assert 'className="workbench-icon-btn"' in topbar
    assert 'data-chat-side-show="true"' in topbar
    assert 'new CustomEvent("workbench:show-chat-side")' in topbar
    assert 'window.addEventListener("workbench:chat-side-visibility"' in topbar
    assert 'window.addEventListener("workbench:show-chat-side"' in chat
    assert 'new CustomEvent("workbench:chat-side-visibility"' in chat

    assert ".workbench-chat-side-show {" not in styles


def test_workbench_chat_sidebar_is_a_top_aligned_floating_accordion():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    right_panel_styles = styles.split("/* ---- right panel ---- */", 1)[1]
    side_css = right_panel_styles.split(".wbc-side {", 1)[1].split("}", 1)[0]
    card_css = styles.split(".wbc-side-card {", 1)[1].split("}", 1)[0]
    accordion_css = styles.split(".wbc-side-accordion {", 1)[1].split("}", 1)[0]
    side_body_css = styles.split(".wbc-side-body {", 1)[1].split("}", 1)[0]
    flush_css = styles.split(".wbc-side-body.flush {", 1)[1].split("}", 1)[0]

    assert 'className="wbc-side-card"' in source
    assert 'className="wbc-side-accordion"' in source
    assert 'className="wbc-side-accordion-trigger"' in source
    assert "aria-expanded={expanded}" in source
    assert 'var [sideTab, setSideTab] = useWbcState("");' in source
    assert 'var activeTab = tabs.some(function (item) { return item.id === tab; }) ? tab : "";' in source
    assert 'onTabChange(expanded ? "" : item.id)' in source
    assert "padding: 70px 12px 12px;" in side_css
    assert "background: transparent;" in side_css
    assert "border-radius: 18px;" in card_css
    assert "backdrop-filter: blur(18px) saturate(112%);" in card_css
    assert "overflow-y: auto;" in accordion_css
    assert "max-height: min(620px, calc(100vh - 250px));" in side_body_css
    assert "padding: 4px 16px 12px;" in side_body_css
    assert "padding: 0;" in flush_css


def test_workbench_chat_sidebar_expanded_lists_share_a_responsive_content_system():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    trigger_css = styles.split(".wbc-side-accordion-trigger {", 1)[1].split("}", 1)[0]
    card_css = styles.split(".wbc-side-card {", 1)[1].split("}", 1)[0]
    file_row_css = styles.split(".wbc-side-body .wbc-file-row {", 1)[1].split("}", 1)[0]
    compact_container = styles.split("@container wbc-side-card (max-width: 320px) {", 1)[1]

    assert "width: 100%;" in trigger_css
    assert "calc(100% + 24px)" not in trigger_css
    assert "container: wbc-side-card / inline-size;" in card_css
    assert "min-height: 42px;" in file_row_css
    assert "border-radius: 0;" in file_row_css
    assert ".wbc-side-body .wbc-file-open" in compact_container
    assert ".wbc-side-body .wbc-branch-kind" in compact_container


def test_workbench_chat_sidebar_tabs_use_panel_specific_svg_icons():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    icon_source = source.split("var WBC_SIDE_TAB_ICONS = {", 1)[1].split("\n};", 1)[0]
    for tab_id in [
        "overview", "plan", "subagents", "context", "artifacts", "changes",
        "branches", "viewer", "map", "browser", '"side-agents"',
    ]:
        assert f"{tab_id}: <svg" in icon_source
    assert 'strokeWidth="1.7"' in icon_source
    assert "WBC_SIDE_TAB_ICONS[item.id]" in source


def test_workbench_chat_sidebar_resizes_from_the_card_edge_without_a_guide_line():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    shell = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    side = chat.split("function WbcSide(", 1)[1].split("function WbcChangesTab", 1)[0]
    card_start = side.index('<div className="wbc-side-card">')
    resizer = 'React.createElement(window.CyreneUI.require("shell").ColResizer, { cardEdge: true })'
    assert resizer in side
    assert card_start < side.index(resizer)
    assert "function WbColResizer({ cardEdge })" in shell
    assert 'className={"wb-col-resizer" + (cardEdge ? " card-edge" : "")}' in shell
    assert "if (cardEdge) return;" in shell
    card_edge_css = styles.split(
        ".wbc-side-card > .wb-col-resizer.card-edge {", 1
    )[1].split("}", 1)[0]
    assert "top: 0;" in card_edge_css
    assert "bottom: 0;" in card_edge_css
    assert "left: 0;" in card_edge_css
    assert "width: 10px;" in card_edge_css
    assert ".wbc-side-card > .wb-col-resizer.card-edge::after" in styles
    assert "content: none;" in styles.split(
        ".wbc-side-card > .wb-col-resizer.card-edge::after", 1
    )[1].split("}", 1)[0]


def test_workbench_chat_sidebar_keeps_only_overview_and_context_unconditional():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    tabs = source.split("  var tabs = [", 1)[1].split("  var activeTab =", 1)[0]
    assert tabs.count('id: "overview"') == 1
    assert tabs.count('id: "context"') == 1
    assert 'if (pendingPlan) tabs.push({ id: "plan"' in tabs
    assert 'if (hasSubagents) tabs.push({ id: "subagents"' in tabs
    assert 'if (hasArtifacts) tabs.push({ id: "artifacts"' in tabs
    assert "if (hasWorkspaceChanges)" in tabs
    assert 'if (hasBranches) tabs.push({ id: "branches"' in tabs
    assert 'if (viewerFile) tabs.push({ id: "viewer"' in tabs
    assert 'if (hasMap) tabs.push({ id: "map"' in tabs
    assert 'if (hasBrowser) tabs.push({ id: "browser"' in tabs
    assert "if (sideAgents && sideAgents.length)" in tabs
    assert "sideAgentsLoading" not in tabs


def test_workbench_clears_stale_side_questions_before_loading_another_chat():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    loading_effect = source.split("var cancelled = false;", 1)[1].split(
        "return function () { cancelled = true; };", 1
    )[0]
    assert "setSideAgents([]);\n    setSideAgentsLoading(true);" in loading_effect


def test_workbench_side_question_panel_renders_only_the_question_list():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    panel = source.split("function WbcSideAgentsPanel", 1)[1].split(
        "function WbcSideAccordionBody", 1
    )[0]
    assert 'className="wbc-side-agent-list"' in panel
    assert "items.map(function (agent, index)" in panel
    assert "<WbcSideAgentTab" not in panel
    assert 'activeTab === "changes";' in source
    assert 'activeTab === "changes" || activeTab === "side-agents"' not in source
    list_css = styles.split(".wbc-side-agent-list {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column;" in list_css
    assert "gap: 6px;" in list_css


def test_workbench_side_question_opens_the_existing_conversation_ui_in_a_split():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    select_handler = source.split("function selectSideAgent", 1)[1].split(
        "function closeSideAgentSplit", 1
    )[0]
    split_component = source.split("function WbcSideAgentSplit({", 1)[1].split(
        "function WbcSideAgentTab", 1
    )[0]
    panel = source.split("function WbcSideAgentsPanel", 1)[1].split(
        "function WbcSideAccordionBody", 1
    )[0]

    assert "setSideAgentSplitByChat" in select_handler
    assert "function WbcSideAgentSplitHost" in source
    assert "function WbcSideAgentSplitResizer" in source
    assert "setTimeout(function () { setLastChildren(null); }, 540)" in source
    assert "requestAnimationFrame(function ()" in source
    assert '(entered ? " open" : "")' in source
    assert '<WbcSideAgentTab agent={agent}' in split_component
    assert '<aside className="wbc-side-agent-split"' in split_component
    assert 'className="wbc-side-agent-split-picker"' in split_component
    assert '<WbcSplitPickerMenu open={pickerOpen}' in split_component
    assert "items.map(function (item, index)" in split_component
    assert "if (onSelect) onSelect(item.id);" in split_component
    assert "<WbcSideAgentTab" not in panel
    assert '(splitDetailOpen ? " side-agent-split-open" : "")' in source
    assert "activeSideAgentId={splitSideAgentId}" in source
    assert "position: absolute;" in styles.split(".wbc-side-agent-split-motion {", 1)[1].split("}", 1)[0]
    assert ".wbc-page.side-agent-split-open" not in styles
    split_motion_css = styles.split(".wbc-side-agent-split-motion {", 1)[1].split("}", 1)[0]
    assert "width: var(--wbc-side-track-width);" in split_motion_css
    assert "transform 500ms cubic-bezier(.22, 1.16, .36, 1);" in split_motion_css
    assert "transform: translateX(100%);" in styles
    assert ".wbc-side-agent-split-motion.open" in styles
    assert 'localStorage.setItem("wbc-side-agent-split-width"' in source
    assert "function wbcClampSideSplitWidth" in source
    assert "var mainMin = Math.min(440, Math.max(380, viewport * 0.36));" in source
    assert 'window.addEventListener("resize", keepSplitWithinViewport);' in source
    assert 'style={splitDetailOpen ? { "--wbc-side-track-width": sideAgentSplitWidth + "px" } : undefined}' in source
    assert ".wbc-side-agent-split-resizer" in styles
    assert "body.wbc-resizing-side-agent .wbc-page" in styles
    split_head_css = styles.split(".wbc-side-agent-split-head {", 1)[1].split("}", 1)[0]
    assert "border-radius: 14px;" in split_head_css
    assert "margin: 12px 12px 8px;" in split_head_css
    assert "z-index: 1001;" in split_head_css
    split_css = styles.split(".wbc-side-agent-split {", 1)[1].split("}", 1)[0]
    assert "padding-top: 58px;" in split_css
    assert "--wbc-main-min-width: clamp(380px, 36vw, 440px);" in styles


def test_workbench_artifacts_use_the_shared_resizable_split_preview():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    artifact_tab = source.split("function WbcArtifactsTab", 1)[1].split(
        "window.CyreneUI.chat", 1
    )[0]
    artifact_split = source.split("function WbcArtifactSplit({", 1)[1].split(
        "function WbcSideAgentSplitHost", 1
    )[0]
    select_handler = source.split("function selectArtifact", 1)[1].split(
        "function closeSideAgentSplit", 1
    )[0]

    assert 'workbenchChat.filesAndArtifacts' not in artifact_tab
    assert 'className="wbc-artifact-list"' in artifact_tab
    assert 'className="wbc-artifact-list-row"' in artifact_tab
    assert "if (onSelectArtifact) onSelectArtifact(file);" in artifact_tab
    assert "setArtifactSplitByChat" in select_handler
    assert "setSideAgentSplitByChat" in select_handler
    assert "function WbcArtifactSplitHost" in source
    assert '<WbcSideAgentSplitResizer width={width} onResize={onResize} />' in source
    assert 'className="wbc-side-agent-split wbc-artifact-split"' in artifact_split
    assert 'className="wbc-side-agent-split-picker"' in artifact_split
    assert "files.map(function (item, index)" in artifact_split
    assert '<WbcViewerTab file={file} onViewed={onViewed} hideHeader={true}' in artifact_split
    assert 'className="wbc-artifact-split-actions"' in artifact_split
    assert 'className="wbc-side-agent-split-action"' in artifact_split
    assert "splitViewer || splitMap || splitBrowserTabId || splitSubagents" in source
    assert ".wbc-artifact-list-row" in styles
    assert ".wbc-artifact-split-viewer" in styles


def test_workbench_changes_panel_is_list_only_and_opens_shared_diff_split():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    changes_tab = source.split("function WbcChangesTab", 1)[1].split(
        "function WbcSubagentsTab", 1
    )[0]
    change_split = source.split("function WbcChangeSplit({", 1)[1].split(
        "function WbcSideAgentSplitHost", 1
    )[0]

    assert 'className="wbc-changes-files"' in changes_tab
    assert "if (onSelectChange) onSelectChange({ chatId: chatId" in changes_tab
    assert 'className="wbc-change-diff"' not in changes_tab
    assert "WorkbenchChatModel.getChangeDiff" not in changes_tab
    assert "function WbcChangeSplitHost" in source
    assert 'className="wbc-side-agent-split wbc-change-split"' in change_split
    assert "WorkbenchChatModel.getChangeDiff" in change_split
    assert 'className="wbc-change-split-diff wbc-change-diff"' in change_split
    assert "files.map(function (item)" in change_split
    assert ".wbc-change-split-diff" in styles


def test_workbench_resource_tabs_use_lists_and_shared_splits_while_branches_expand_inline():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    browser = (root / "src" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    side = source.split("function WbcSide({", 1)[1].split("function wbcChangeTypeLabel", 1)[0]
    assert 'activeTab === "viewer" && <WbcViewerList' in side
    assert 'activeTab === "map" && <WbcMapList' in side
    assert '<WbcBrowserList browserState={browserPanelState}' in side
    assert 'var opensSplit = item.id === "subagents" || item.id === "browser";' in side
    assert 'if (item.id === "subagents" && onOpenSubagents) onOpenSubagents();' in side
    assert 'activeTab === "branches" && <WbcBranchTab' in side
    assert "function WbcMapSplitHost" in source
    assert "L.circleMarker(pos" in source
    assert "new ResizeObserver(invalidate)" in source
    assert 'setTimeout(invalidate, 560)' in source
    assert "function wbcRenderMapMarkdown" in source
    assert "var noteHtml = wbcRenderMapMarkdown(note)" in source
    assert 'body.innerHTML = noteHtml;' in source
    assert 'marker.bindPopup(popup, { maxWidth: 340, minWidth: 210 });' in source
    assert "function WbcBrowserSplitHost" in source
    assert "function WbcSubagentsSplitHost" in source
    assert source.count('className="wbc-resource-split-picker-wrap"') >= 2
    assert source.count("<WbcSplitPickerMenu open={pickerOpen}") >= 3
    assert 'selectResourceSplit("map", item)' in source
    assert 'selectResourceSplit("browser", tabId)' in source
    assert 'selectResourceSplit("subagents", true)' in source
    assert "desiredTabId" in browser
    assert "bridge.activateTab" in browser
    assert ".wbc-resource-list-row" in styles
    assert ".wbc-resource-split-body" in styles
    assert ".wbc-resource-picker-menu" in styles
    assert ".wbc-map-popup-markdown" in styles
    assert "@keyframes wbc-split-menu-in" in styles
    assert "@keyframes wbc-split-menu-out" in styles
    assert "animation: wbc-split-menu-in 340ms" in styles
    assert "animation: wbc-split-menu-out 240ms" in styles
    assert "translate3d(0, -12px, 0) scale(.985)" in styles
    assert "function WbcSplitPickerMenu" in source
    resource_picker_css = styles.split(".wbc-resource-picker-menu {", 1)[1].split("}", 1)[0]
    assert "position: relative;" in resource_picker_css


def test_workbench_empty_composer_does_not_expand_from_parent_scroll_height():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    sync_height = source.split("function syncHeight()", 1)[1].split(
        "function submit()", 1
    )[0]
    assert 'if (!String(draftRef.current || ""))' in sync_height
    assert 'ta.style.height = compact ? "32px" : "44px";' in sync_height


def test_memory_toolbars_use_the_same_frosted_glass_without_overlay_spacing():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    main_css = styles.split(".wb-mem-main {", 1)[1].split("}", 1)[0]
    toolbar_css = styles.split(".wb-mem-toolbar {", 1)[1].split("}", 1)[0]
    toolbar_glass_css = styles.split(
        ".wb-mem-toolbar::before {", 1
    )[1].split("}", 1)[0]
    scroll_css = styles.split(".wb-mem-scroll {", 1)[1].split("}", 1)[0]
    detail_styles = styles.split("/* ── detail panel ── */", 1)[1]
    detail_css = detail_styles.split(".wb-mem-detail {", 1)[1].split("}", 1)[0]
    tabs_css = detail_styles.split(".wb-mem-detail-tabs {", 1)[1].split("}", 1)[0]
    tabs_glass_css = detail_styles.split(
        ".wb-mem-detail-tabs::before {", 1
    )[1].split("}", 1)[0]
    detail_scroll_css = detail_styles.split(
        ".wb-mem-detail-scroll {", 1
    )[1].split("}", 1)[0]

    assert "--wb-mem-toolbar-overlay-height: 66px;" in main_css
    assert "position: relative;" in main_css
    assert "position: absolute;" in toolbar_css
    assert "z-index: 20;" in toolbar_css
    assert "isolation: isolate;" in toolbar_css
    assert "var(--wb-main-bg, var(--wb-surface)) 66%" in toolbar_glass_css
    assert "var(--wb-main-bg, var(--wb-surface)) 56%" in toolbar_glass_css
    assert "var(--wb-main-bg, var(--wb-surface)) 32%" in toolbar_glass_css
    assert "backdrop-filter: blur(46px) saturate(165%) contrast(103%);" in toolbar_glass_css
    assert "mask-image: linear-gradient(" in toolbar_glass_css
    assert "padding: calc(var(--wb-mem-toolbar-overlay-height) + 4px) 22px 14px;" in scroll_css

    assert "--wb-mem-detail-tabs-overlay-height: 42px;" in detail_css
    assert "position: relative;" in detail_css
    assert "position: absolute;" in tabs_css
    assert "z-index: 20;" in tabs_css
    assert "isolation: isolate;" in tabs_css
    assert "border-bottom: 0;" in tabs_css
    assert "var(--wb-surface) 66%" in tabs_glass_css
    assert "var(--wb-surface) 56%" in tabs_glass_css
    assert "var(--wb-surface) 32%" in tabs_glass_css
    assert "backdrop-filter: blur(46px) saturate(165%) contrast(103%);" in tabs_glass_css
    assert "mask-image: linear-gradient(" in tabs_glass_css
    assert "padding: calc(var(--wb-mem-detail-tabs-overlay-height) + 16px) 16px 16px;" in detail_scroll_css


def test_memory_page_hides_all_scrollbars_without_disabling_scroll():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    scrollbar_scope = styles.split(
        "/* Keep every memory surface scrollable without exposing scrollbar chrome. */",
        1,
    )[1]

    assert ".wb-mem-page * {" in scrollbar_scope
    assert "scrollbar-width: none;" in scrollbar_scope
    assert "-ms-overflow-style: none;" in scrollbar_scope
    assert ".wb-mem-page *::-webkit-scrollbar {" in scrollbar_scope
    assert "display: none;" in scrollbar_scope
    assert "width: 0;" in scrollbar_scope
    assert "height: 0;" in scrollbar_scope
    assert ".wb-mem-scroll {" in styles
    assert "overflow-y: auto;" in styles


def test_notification_items_navigate_to_their_precise_context():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    chat_source = (
        root / "src" / "webui" / "frontend" / "workbench-chat.jsx"
    ).read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert "function wbNotificationNavigationTarget(item)" in source
    assert 'type: "chat", chatId: meta.chatId' in source
    assert 'type: "task", sessionId: meta.sessionId' in source
    assert 'type: "schedule"' in source
    assert 'type: "knowledge", docId: meta.documentId || meta.docId' in source
    assert 'setSettingsTab("about")' in source
    assert "navigateFromSearch(target);" in source
    assert "onOpenNotification={navigateFromNotification}" in source
    assert 'className="workbench-notif-item-jump"' in source
    assert 'targetProjectId === String(projectIdRef.current || "")' in chat_source
    assert "refreshChats(targetId);" in chat_source
    assert ".workbench-notif-item:focus-visible" in styles


def test_workbench_chat_interrupt_waits_for_server_and_uses_live_status_everywhere():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    runtime_interrupt = source.split(
        "  function interrupt(chatId, model) {", 1
    )[1].split("\n  function deferSend", 1)[0]
    side_status = source.split("function WbcOverviewTab", 1)[1].split(
        "function wbcBlockLabel", 1
    )[0]

    assert "Promise.resolve(request)" in runtime_interrupt
    assert ".finally(function () {" in runtime_interrupt
    assert "abort(chatId);" in runtime_interrupt
    assert 'fire("onInterrupted", chatId);' in source
    assert 'return { ...prev, status: "idle" };' in source
    assert "runtime ?" in side_status
    assert 'className={"wbc-overview-status" + (runtime ? " live" : "")}' in side_status
    assert 'chat.status === "running"' not in side_status


def test_workbench_chat_restores_project_cache_before_background_refresh():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    assert "function wbcChatCache()" in source
    assert "chatCache.lists[requestedProjectId]" in source
    assert "setLoading(!cachedList);" in source
    assert "setActiveChat(cachedChat);" in source
    assert "setChatLoading(!cachedChat);" in source


def test_remote_chat_change_refreshes_the_open_transcript_as_well_as_the_rail():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    event_block = source.split('if (event.type === "workbench_chat_changed") {', 1)[1].split(
        'if (event.type === "workspace_changes")', 1
    )[0]
    assert "remoteChangedChatIdsRef.current.add(changedChatId || \"*\")" in event_block
    assert 'refreshChats("");' in event_block
    assert 'changedChatIds.has(openChatId)' in event_block
    assert "setLoadRevision(function (value) { return value + 1; });" in event_block


def test_remote_chat_refresh_and_notification_navigation_use_the_latest_project():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    page_setup = source.split("function WorkbenchChatPage", 1)[1].split(
        "  function refreshChats", 1
    )[0]
    refresh = source.split("  function refreshChats(selectId) {", 1)[1].split(
        "\n  // Initial load + project switch.", 1
    )[0]
    navigation = source.split("  function applyPendingChatSelection() {", 1)[1].split(
        "\n  useWbcEffect(function () {", 1
    )[0]
    remote_events = source.split(
        'if (event.type === "workbench_chat_changed") {', 1
    )[1].split('if (event.type === "workspace_changes")', 1)[0]

    assert "projectIdRef.current = projectId;" in page_setup
    assert 'var requestedProjectId = String(projectIdRef.current || "");' in refresh
    assert "var requestedProjectId = projectId;" not in refresh
    assert "refreshChats(targetId);" in navigation
    assert 'refreshChats("");' in remote_events


def test_workbench_chat_has_long_conversation_navigation_and_bottom_return():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert "function WbcConversationNavigator" in source
    assert 'data-wbc-nav-item={nav ? "true" : undefined}' in source
    assert 'className="wbc-conversation-nav"' in source
    assert "scrollToConversationBottom" in source
    assert 'className="wbc-scroll-to-bottom"' in source
    assert 'navigation={msg.role === "user" ? wbcUserMessageNavigationMeta(msg) : null}' in source
    assert "visible: markers.length > 5" in source
    assert 'className="wbc-conversation-nav-trigger"' in source
    assert 'className="wbc-conversation-nav-panel"' in source
    assert 'className="wbc-conversation-nav-list"' in source
    assert "hoveredIndex" not in source
    assert "var contentPreview = wbcNavigationPreview(msg.content || \"\");" in source
    assert "var attachmentPreview = attachmentTypes.slice(0, 2).join(\" · \");" in source
    assert "contentPreview ? prefix + \": \" + preview : preview" in source
    assert '"workbenchChat.attachmentType.image": "图片"' in (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    assert ".wbc-conversation-nav" in styles
    assert ".wbc-conversation-nav:hover .wbc-conversation-nav-panel" in styles
    assert ".wbc-conversation-nav-list" in styles
    assert ".wbc-scroll-to-bottom" in styles
    nav_css = styles.split(".wbc-conversation-nav {", 1)[1].split("}", 1)[0]
    panel_css = styles.split(".wbc-conversation-nav-panel {", 1)[1].split("}", 1)[0]
    assert "top: calc(50% - 48px);" in nav_css
    assert "right: 4px;" in nav_css
    assert "left: auto;" in nav_css
    assert "right: 0;" in panel_css
    assert "left: auto;" in panel_css
    assert "transform-origin: right center;" in panel_css


def test_maximized_browser_has_compact_agent_chat_with_transient_status():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert 'effectiveMode === "maximized" && !hasNativeChatOverlay && (' in source
    assert 'browserWindowMode === "maximized" ? " browser-window-maximized" : ""' in source
    released_stage_css = styles.split(
        ".wbc-thread-stage.browser-window-maximized {", 1
    )[1].split("}", 1)[0]
    assert "width: 100%;" in released_stage_css
    assert "transition: none;" in released_stage_css
    assert "will-change: auto;" in released_stage_css
    assert 'className="wbc-browser-fullscreen-chat"' in source
    assert "fullscreenStatusRequested" in source
    assert "fullscreenFinalReply" in source
    assert "latestAssistantReplyText" in source
    assert "fullscreenReplyBaselineRef" in source
    assert "}, 5000);" in source
    assert "wbcBrowserFullscreenStatusText(runtime)" in source
    assert "setChatOverlay" in source
    assert "onChatOverlayAction" in source
    assert "hasNativeChatOverlay" in source
    assert 'document.querySelector(".workbench-shell") || document.documentElement' in source
    assert 'attributeFilter: ["data-theme", "style"]' in source
    assert 'window.addEventListener("cyrene-tweak-accent-change", refreshOverlayTheme)' in source
    assert "chatOverlayThemeRevision" in source
    assert ".wbc-browser-fullscreen-composer" in styles
    composer = styles.split(".wbc-browser-fullscreen-composer {", 1)[1].split("}", 1)[0]
    focused_composer = styles.split(".wbc-browser-fullscreen-composer:focus-within {", 1)[1].split("}", 1)[0]
    assert "box-shadow: none;" in composer
    assert "box-shadow: none;" in focused_composer
    assert "padding-bottom: 58px" not in styles


def test_electron_browser_chat_overlay_floats_above_native_page():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    overlay_preload = (root / "electron" / "browser-chat-overlay-preload.js").read_text(
        encoding="utf-8"
    )
    package = (root / "electron" / "package.json").read_text(encoding="utf-8")

    assert "ensureChatOverlayView()" in main
    assert "parent.addChildView(view)" in main
    assert "container && container.contentView ? container.contentView : container" in main
    assert "syncChatOverlay(this.ownerWindow()?.contentView || null" in main
    assert "syncChatOverlay(win.contentView" in main
    assert "Failed to attach browser chat overlay" in main
    assert "const bottomOffset = 56" in main
    assert "this.bounds.height - height - bottomOffset" in main
    assert "browser:set-chat-overlay" in main
    assert "browser-chat-overlay:action" in main
    assert "form:focus-within { border-color: var(--accent, #6d5dfc); box-shadow: none; }" in main
    assert "statusComplete" in main
    assert "setChatOverlay:" in preload
    assert "onChatOverlayAction:" in preload
    assert "contextBridge.exposeInMainWorld('browserChatOverlay'" in overlay_preload
    assert '"browser-chat-overlay-preload.js"' in package


def _run_workbench_model_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    runtime_path = root / "src" / "webui" / "frontend" / "platform" / "runtime.jsx"
    model_path = root / "src" / "webui" / "frontend" / "workbench-model.jsx"
    script = f"""
const fs = require("fs");
global.window = {{}};
eval(fs.readFileSync({json.dumps(str(runtime_path))}, "utf8"));
eval(fs.readFileSync({json.dumps(str(model_path))}, "utf8"));
window.WorkbenchModel = window.CyreneUI.require("model");
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _run_workbench_i18n_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    runtime_path = root / "src" / "webui" / "frontend" / "platform" / "runtime.jsx"
    i18n_path = root / "src" / "webui" / "frontend" / "workbench-i18n.jsx"
    script = f"""
const fs = require("fs");
global.window = {{}};
global.localStorage = {{ getItem: () => "", setItem: () => {{}} }};
global.navigator = {{ language: "zh-CN" }};
global.document = {{ documentElement: {{ dataset: {{}} }} }};
global.React = {{ useState: () => [0, () => {{}}], useEffect: () => {{}} }};
eval(fs.readFileSync({json.dumps(str(runtime_path))}, "utf8"));
eval(fs.readFileSync({json.dumps(str(i18n_path))}, "utf8"));
window.WorkbenchI18n = window.CyreneUI.require("i18n");
window.WorkbenchI18n.setLang("zh");
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _run_workbench_trace_i18n_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    runtime_path = root / "src" / "webui" / "frontend" / "platform" / "runtime.jsx"
    i18n_path = root / "src" / "webui" / "frontend" / "workbench-i18n.jsx"
    chat_source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    helper_source = "function wbcT(" + chat_source.split(
        "function wbcT(", 1
    )[1].split("function wbcThinkingPhrases", 1)[0]
    script = f"""
const fs = require("fs");
global.window = {{}};
global.localStorage = {{ getItem: () => "", setItem: () => {{}} }};
global.navigator = {{ language: "zh-CN" }};
global.document = {{ documentElement: {{ dataset: {{}} }} }};
global.React = {{ useState: () => [0, () => {{}}], useEffect: () => {{}} }};
eval(fs.readFileSync({json.dumps(str(runtime_path))}, "utf8"));
eval(fs.readFileSync({json.dumps(str(i18n_path))}, "utf8"));
eval({json.dumps(helper_source)});
window.WorkbenchI18n = window.CyreneUI.require("i18n");
window.WorkbenchI18n.setLang("zh");
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _run_workbench_runtime_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    args_preview_source = "function wbcToolArgsPreview(" + source.split(
        "function wbcToolArgsPreview(", 1
    )[1].split("function wbcThinkingPhrases", 1)[0]
    runtime_source = source.split(
        "var WorkbenchChatRuntimes = (function () {", 1
    )[1].split("// Page", 1)[0]
    runtime_source = "var WorkbenchChatRuntimes = (function () {" + runtime_source
    script = f"""
global.window = {{
  CyreneUI: {{
    require: () => ({{
      subscribe: (handler) => {{ global.__wbcSseHandler = handler; return () => {{}}; }}
    }})
  }}
}};
function wbcT(_key, fallback) {{ return fallback; }}
function wbcSubagentStatusText(status) {{ return String(status || ""); }}
eval({json.dumps(args_preview_source)});
eval({json.dumps(runtime_source)});
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _run_workbench_timeline_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    timeline_source = "function wbcConfirmOptimisticMessage(" + source.split(
        "function wbcConfirmOptimisticMessage(", 1
    )[1].split("function wbcCurrentModel(", 1)[0]
    script = f"""
eval({json.dumps(timeline_source)});
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def test_workbench_confirmed_user_turn_keeps_live_timeline_anchor():
    result = _run_workbench_timeline_js(
        """
(() => {
  const startedAt = Date.parse("2026-07-15T09:27:52.100Z");
  const optimistic = {
    id: "pending_user",
    role: "user",
    content: "你好",
    createdAt: new Date(startedAt).toISOString(),
    optimistic: true
  };
  const confirmed = wbcConfirmOptimisticMessage(optimistic, {
    id: "saved_user",
    role: "user",
    content: "你好",
    createdAt: "2026-07-15T09:27:52.180000+00:00"
  });
  const runtime = wbcRuntimeTimelineMessages({
    chatId: "chat_1",
    startedAt,
    activities: []
  });
  const merged = wbcMergeChronologicalMessages([confirmed], runtime);
  return {
    ids: merged.map(item => item.id),
    createdAt: confirmed.createdAt,
    serverCreatedAt: confirmed.serverCreatedAt
  };
})()
"""
    )

    assert result == {
        "ids": ["saved_user", "runtime_heartbeat_chat_1", "runtime_activity_1"],
        "createdAt": "2026-07-15T09:27:52.100Z",
        "serverCreatedAt": "2026-07-15T09:27:52.180000+00:00",
    }


def test_workbench_timeline_compares_real_instants_not_timestamp_strings():
    result = _run_workbench_timeline_js(
        """
wbcMergeChronologicalMessages(
  [{ id: "first", createdAt: "2026-01-01T01:00:00+01:00" }],
  [{ id: "second", createdAt: "2026-01-01T00:00:00.500Z" }]
).map(item => item.id)
"""
    )

    assert result == ["first", "second"]


def test_workbench_finalizing_runtime_closes_live_tool_activity():
    result = _run_workbench_timeline_js(
        """
(() => {
  const runtime = {
    chatId: "chat_1",
    startedAt: 1000,
    replying: true,
    progress: [{ kind: "tool", status: "running", toolCallId: "root_tool" }],
    activities: [{
      id: "activity_1",
      startedAt: 1100,
      reasoningActive: true,
      progress: [{ kind: "tool", status: "running", toolCallId: "activity_tool" }]
    }]
  };
  const finalized = wbcFinalizeRuntime(runtime);
  const timeline = wbcRuntimeTimelineMessages(finalized);
  return {
    finalizing: finalized.finalizing,
    replying: finalized.replying,
    rootStatus: finalized.progress[0].status,
    activityStatus: finalized.activities[0].progress[0].status,
    timelineClosed: finalized.activities[0].timelineClosed,
    heartbeatFinalizing: timeline[0].runtimeFinalizing,
    activityActive: timeline[1].runtimeActivityActive
  };
})()
"""
    )

    assert result == {
        "finalizing": True,
        "replying": False,
        "rootStatus": "completed",
        "activityStatus": "completed",
        "timelineClosed": True,
        "heartbeatFinalizing": True,
        "activityActive": False,
    }


def test_workbench_terminal_tool_event_preserves_resolved_identity():
    result = _run_workbench_timeline_js(
        """
wbcMergeToolLifecycleEntry(
  {
    kind: "tool",
    toolCallId: "call_1",
    text: "memory.project.search",
    preview: "resolved query",
    status: "completed",
    failed: false
  },
  {
    kind: "tool",
    toolCallId: "call_1",
    text: "memory_tools",
    preview: "invoke, memory.project.search",
    status: "completed",
    failed: false
  },
  true
)
"""
    )

    assert result["text"] == "memory.project.search"
    assert result["preview"] == "resolved query"
    assert result["status"] == "completed"
    assert result["failed"] is False


def test_workbench_plan_revision_guard_only_blocks_unresolved_started_steps():
    result = _run_workbench_model_js(
        """
[
  window.WorkbenchModel.hasUnresolvedStartedSteps([
    { status: "completed" },
    { status: "skipped" }
  ]),
  window.WorkbenchModel.hasUnresolvedStartedSteps([
    { status: "completed" },
    { status: "failed" },
    { status: "pending" }
  ]),
  window.WorkbenchModel.hasUnresolvedStartedSteps([
    { status: "pending" },
    { status: "pending" }
  ])
]
"""
    )

    assert result == [False, True, False]


def test_workbench_dependency_helpers_preserve_visible_order_and_block_unmet_steps():
    result = _run_workbench_model_js(
        """
(() => {
  const plan = [
    { id: "a", title: "A", status: "completed", dependsOn: [] },
    { id: "b", title: "B", status: "pending", dependsOn: ["a"] },
    { id: "c", title: "C", status: "pending", dependsOn: ["b"] }
  ];
  const invalid = [plan[1], plan[0], plan[2]];
  return {
    valid: window.WorkbenchModel.validatePlanGraph(plan),
    invalid: window.WorkbenchModel.validatePlanGraph(invalid),
    next: window.WorkbenchModel.findNextRunnableStep(plan).id,
    unmetC: window.WorkbenchModel.unmetDependencyIds(plan, plan[2]),
    marked: window.WorkbenchModel.markStepById(plan, "b", "running", "go").map(s => s.status)
  };
})()
"""
    )

    assert result["valid"] == {"valid": True}
    assert result["invalid"]["code"] == "dependency_order"
    assert result["next"] == "b"
    assert result["unmetC"] == ["b"]
    assert result["marked"] == ["completed", "running", "pending"]


def test_workbench_plan_ui_uses_step_ids_and_operation_endpoint():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "webui" / "frontend" / "workbench-model.jsx").read_text(encoding="utf-8")

    assert "function markStepById" in model
    assert '"/plan"' in model
    assert "model.markStepById(basePlan, stepId" in source
    assert "controller.reorderSteps" in source
    assert "dependsOn" in source
    assert "function requirePlan(baseSession)" in source
    assert "firstUnresolvedStepIndex" not in source


def test_workbench_keeps_live_subagent_logs_across_silent_refreshes():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")

    assert 'data.type === "subagent_update"' in source
    assert 'session_id = str(entry.get("session_id") or "")' in (
        root / "src" / "cyrene" / "subagent.py"
    ).read_text(encoding="utf-8")
    assert "event.live && event.id" in source
    assert "data.message" in source


def test_workbench_uses_light_project_payload_and_lazy_session_detail():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "webui" / "frontend" / "workbench-model.jsx").read_text(encoding="utf-8")

    assert 'apiJson("/api/projects?detail=summary")' in model
    assert "function fetchSession(sessionId)" in model
    assert '"/api/task-sessions/" + encodeURIComponent(sessionId)' in model
    assert "mergeSessionPayload(prev, payload)" in source
    assert "if (session.isSummary) fetchAndMergeSession(session.id)" in source
    assert "if (nextSession && nextSession.isSummary) fetchAndMergeSession(nextSessionId)" not in source
    assert "seq !== sessionLoadSeqRef.current" in source


def test_workbench_auto_welcome_waits_for_backend_and_skips_existing_content():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    data_store = (root / "src" / "webui" / "frontend" / "platform" / "data-store.jsx").read_text(
        encoding="utf-8"
    )

    assert "function wbProjectStoreHasUserContent(store)" in source
    assert "autoWelcomePendingRef.current = true" in source
    assert "Promise.resolve(dataStore.ready)" in source
    assert "!!onboardingState.hasExistingData" in source
    assert "|| wbProjectStoreHasUserContent(next)" in source
    assert 'current == null ? "welcome" : current' in source
    assert 'hasExistingData: false' in data_store

    helper_source = "function wbProjectStoreHasUserContent(" + source.split(
        "function wbProjectStoreHasUserContent(", 1
    )[1].split("function wbRememberWelcomeHandled", 1)[0]
    script = f"""
eval({json.dumps(helper_source)});
const blankDefault = {{projects: [{{
  dataKey: "default",
  sessions: [{{title: "新任务", goal: "", plan: [], events: [], runs: [], artifacts: []}}]
}}]}};
const explicitProject = {{projects: [{{dataKey: "project_123", sessions: []}}]}};
const usedLegacyProject = {{projects: [{{
  dataKey: "default",
  sessions: [{{title: "Research", goal: "Find sources"}}]
}}]}};
process.stdout.write(JSON.stringify({{
  blankDefault: wbProjectStoreHasUserContent(blankDefault),
  explicitProject: wbProjectStoreHasUserContent(explicitProject),
  usedLegacyProject: wbProjectStoreHasUserContent(usedLegacyProject)
}}));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)
    assert result == {
        "blankDefault": False,
        "explicitProject": True,
        "usedLegacyProject": True,
    }


def test_workbench_module_pages_are_kept_alive_without_hidden_file_drop():
    root = Path(__file__).resolve().parent.parent
    shell = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    library = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")

    assert "mountedPages" in shell
    assert "var WorkbenchStableSurface = React.memo(" in shell
    assert "return !prev.active && !next.active;" in shell
    assert "<WorkbenchStableSurface active={isChat}>" in shell
    assert "<WorkbenchStableSurface active={isKnowledge}>" in shell
    assert "<WorkbenchStableSurface active={isSchedule}>" in shell
    assert "<WorkbenchStableSurface active={isMemory}>" in shell
    assert "<WorkbenchStableSurface active={!isModulePage}>" in shell
    assert "active={!isModulePage}" in shell
    assert "var taskDropEnabled = !!(active && project && session && session.kind !== \"init\")" in shell
    assert "function WorkbenchChatPage({ active, project" in chat
    assert "!!(isActive && project)" in chat
    assert "function WorkbenchLibraryPage(props)" in library
    assert "props.active !== false" in library


def test_workbench_task_controller_uses_current_session_from_returned_store():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    controller = source.split("function useTaskController", 1)[1].split("function TaskPlanList", 1)[0]

    assert "function sessionFromStore" in controller
    assert "sessions[j] && sessions[j].id === sid" in controller
    assert "return ctrl.executeAll({ baseSession: sessionFromStore(store, session) })" in controller
    assert "(store && store.activeSession) || current" not in controller
    assert "(patched && patched.activeSession) || baseSession" not in controller
    assert "(next && next.activeSession) || baseSession" not in controller
    assert "(nextStore && nextStore.activeSession) || currentSession" not in controller


def test_workbench_memory_skill_learning_selects_tool_chains():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    routes = (root / "src" / "route" / "learning.py").read_text(encoding="utf-8")
    pattern = (
        root / "src" / "cyrene" / "learning" / "facade.py"
    ).read_text(encoding="utf-8")
    prompts = (root / "src" / "cyrene" / "agent" / "prompts.py").read_text(encoding="utf-8")

    assert "selectedLearningChainId" in source
    assert "selectedLearningSessionId" in source
    assert "learningSessions(snap.chains)" in source
    assert "tool_chains" in source
    assert "onSelectChain(chain.id)" in source
    assert "onSelectSession" in source
    assert "memRenderMarkdown" in source
    assert "dangerouslySetInnerHTML" in source
    assert "toolIcon(step)" in source
    assert "toolDisplayName(step)" in source
    assert "toolParamsText(step)" in source
    assert "detailScreenshot(chain)" in source
    assert "detailFiles(chain)" in source
    assert "className: \"wb-replay-learn\"" not in source
    assert "Cyrene Browser" not in source
    assert "回放速度" not in source
    assert "工具链 Replay" not in source
    assert "wb-replay-learn" not in styles
    assert "wb-replay-timeline" not in styles
    assert "wb-replay-logo" not in styles
    assert "memory.learning.detailsTitle" in source
    assert "memory.learning.agentAnswer" in source
    assert "memory.learning.sessionSelect" in source
    assert "/api/learning/process" in source
    assert "/api/patterns" not in source
    assert "grid-template-columns: 34px 42px minmax(0, 1fr) 22px" in styles
    assert ".wb-detail-shot" in styles
    assert ".wb-detail-files" in styles
    assert "memory.learning.detailsTitle" in i18n
    assert "memory.learning.sessionSelect" in i18n
    assert "memory.learning.review.parameterize" not in i18n
    assert "memory.learning.processedNote" in i18n
    learning_source = source[source.index("function learningSnapshot"):source.index("// ── main page")]
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in learning_source)
    # Memory records use the compatibility workspace/dataKey, while learning
    # sessions must always be requested with the canonical project id.
    assert 'var learningProject = (project && project.id) || workspace;' in source
    assert '"/api/evolution?project=" + encodeURIComponent(learningProject)' in source
    assert '"?project=" + encodeURIComponent(learningProject)' in source
    assert "_learning_enrich_tool_chains" in routes
    assert "_learning_is_known_media_path" in routes
    assert "/api/tool-chain-media" in routes
    assert "/api/scripts" not in routes
    assert "ListScripts" not in pattern
    assert "RunScript" not in pattern
    assert "LearnSkill" not in pattern
    assert "call `LearnSkill`" not in prompts


def test_workbench_chat_overview_i18n_has_zh_labels():
    result = _run_workbench_i18n_js(
        """
[
  window.WorkbenchI18n.t("chat.side.overview"),
  window.WorkbenchI18n.t("chat.runSummary"),
  window.WorkbenchI18n.t("workbenchChat.sessionInfo"),
  window.WorkbenchI18n.t("workbenchChat.statusLabel"),
  window.WorkbenchI18n.t("workbenchChat.messageCount"),
  window.WorkbenchI18n.t("workbenchChat.model"),
  window.WorkbenchI18n.t("chat.runId"),
  window.WorkbenchI18n.t("workbenchChat.createdAt"),
  window.WorkbenchI18n.t("workbenchChat.quickActions")
]
"""
    )

    assert result == [
        "概览",
        "运行摘要",
        "会话信息",
        "状态",
        "消息数",
        "模型",
        "会话 ID",
        "创建时间",
        "快捷操作",
    ]


def test_workbench_chat_supports_parallel_conversation_runtimes():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    assert "var WorkbenchChatRuntimes = (function () {" in source
    assert "var runtimes = {};" in source
    assert "var aborts = {};" in source
    assert 'window.CyreneUI.chat = window.CyreneUI.register("chat", {' in source
    assert "Runtimes: WorkbenchChatRuntimes" in source
    assert "var runtimeEngine = WorkbenchChatRuntimes;" in source
    assert "runtimeEngine.subscribe(function (snap) { setRuntimes(snap); })" in source
    assert "runtimeEngine.start(chatId, preparedInput, model)" in source
    assert "var activeRuntime = runtimes[activeChatId] || null;" in source
    assert "if (!chatId || runtimes[chatId]) return null;" in source
    assert "otherRunning" not in source
    assert "workbenchChat.lockedByOther" not in source
    assert "workbenchChat.lockedByOther" not in i18n


def test_workbench_chat_renders_new_user_turn_before_live_thinking_card():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    quick_source = (
        root / "src" / "webui" / "frontend" / "workbench-quick-chat.jsx"
    ).read_text(encoding="utf-8")

    start_block = source.split("function start(chatId, input, model)", 1)[1].split(
        "function reconnect(chatId, model)", 1
    )[0]
    ack_block = source.split("onAck: function (event) {", 1)[1].split(
        "onReplyStart:", 1
    )[0]

    assert 'id: optimisticId' in start_block
    assert 'role: "user"' in start_block
    assert "attachments: Array.isArray(input.attachments)" in start_block
    assert start_block.index('fire("onUserMessage"') < start_block.index("update(chatId")
    assert "optimisticUserMessageId" in start_block
    assert 'fire("onUserMessageConfirmed"' in ack_block
    assert "optimisticId" in ack_block
    assert "onUserMessageConfirmed: function" in source
    assert "onUserMessageConfirmed: function" in quick_source
    assert "quickChatConfirmUserMessage" in quick_source

    result = _run_workbench_runtime_js(
        """
(() => {
  const events = [];
  const userMessages = [];
  const confirmations = [];
  let handlers = null;
  WorkbenchChatRuntimes.setHooks({
    onUserMessage: (_chatId, message) => {
      events.push("user");
      userMessages.push(message);
    },
    onUserMessageConfirmed: (_chatId, confirmation) => {
      events.push("confirmed");
      confirmations.push(confirmation);
    }
  });
  WorkbenchChatRuntimes.subscribe(() => events.push("runtime"));
  WorkbenchChatRuntimes.start(
    "chat-1",
    { message: "hello", attachments: [{ id: "file-1" }] },
    {
      sendMessage: (_chatId, _input, nextHandlers) => {
        handlers = nextHandlers;
        return new Promise(() => {});
      }
    }
  );
  const beforeAck = events.slice();
  handlers.onAck({
    userMessage: { id: "msg-1", role: "user", content: "hello" }
  });
  return {
    beforeAck,
    optimistic: userMessages[0],
    confirmation: confirmations[0]
  };
})()
"""
    )

    assert result["beforeAck"] == ["user", "runtime"]
    assert result["optimistic"]["content"] == "hello"
    assert result["optimistic"]["attachments"] == [{"id": "file-1"}]
    assert result["optimistic"]["optimistic"] is True
    assert result["confirmation"]["optimisticId"] == result["optimistic"]["id"]
    assert result["confirmation"]["userMessage"]["id"] == "msg-1"


def test_workbench_chat_opens_bounded_browser_window_from_live_browser_events():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    assert "browserActiveByChat" in source
    assert 'event.type === "browser_frame" || event.type === "browser_takeover_request"' in source
    assert "(!browserEventChatId || browserEventChatId === String(activeChatIdRef.current))" in source
    assert "setBrowserActiveByChat(function (prev)" in source
    assert "(browserState && browserState.active) || browserMarkedActive" in source
    browser_event_block = source.split("var browserEventChatId", 1)[1].split(
        "// Live tool/phase/subagent progress", 1
    )[0]
    assert "setBrowserWindowModeByChat" in browser_event_block
    assert 'setSideTab("browser")' not in browser_event_block
    assert 'browserWindowModeByChat[activeChatId] || "pip"' in source
    assert 'effectiveMode === "minimized"' in source
    assert 'effectiveMode !== "maximized"' in source
    assert "wbc-browser-resize-handle" in source
    assert ".wbc-thread-stage" in styles
    assert '<div className="wbc-browser-movement-region">' in source
    movement_region_styles = styles.split(".wbc-browser-movement-region {", 1)[1].split("}", 1)[0]
    assert "position: absolute;" in movement_region_styles
    assert "top: var(--wbc-thread-inset-top);" in movement_region_styles
    assert "right: calc(12px - var(--wbc-side-track-width));" in movement_region_styles
    assert "bottom: calc(34px * var(--wb-ui-font-scale, 1));" in movement_region_styles
    assert "left: var(--wbc-thread-inset-inline);" in movement_region_styles
    assert "pointer-events: none;" in movement_region_styles
    thread_styles = styles.split(".wbc-thread {", 1)[1].split("}", 1)[0]
    assert "padding: var(--wbc-thread-inset-top) var(--wbc-thread-inset-inline) var(--wbc-thread-inset-bottom);" in thread_styles
    assert ".wbc-browser-window.maximized" in styles
    assert ".wbc-browser-restore-float" in styles
    pip_styles = styles.split(".wbc-browser-window.pip {", 1)[1].split("}", 1)[0]
    assert "width: min(calc(var(--wbc-side-track-width)" in pip_styles
    assert "height: min(240px" in pip_styles
    floating_native_styles = styles.split(".wbc-browser-window.pip .browser-view.native,", 1)[1].split("}", 1)[0]
    assert ".wbc-browser-window.maximized .browser-view.native" in floating_native_styles
    assert "--browser-resize-gutter: 0px;" in floating_native_styles
    assert ".wbc-browser-window.pip .browser-tabs-strip," in styles
    assert ".wbc-browser-window.pip .browser-nav-bar" in styles
    assert "WBC_ICONS.windowMaximize" in source
    assert "WBC_ICONS.windowMinimize" in source
    minimized_surface = source.split('effectiveMode === "minimized"', 1)[1].split("var inlineStyle", 1)[0]
    assert 'Array.isArray(displayBrowserState.tabs) && displayBrowserState.tabs.length === 0' in source
    assert 'hasNoBrowserTabs && (effectiveMode === "pip" || effectiveMode === "minimized")' in source
    assert "wbc-browser-title-pill" not in minimized_surface
    assert "WBC_ICONS.windowMaximize" not in minimized_surface
    assert "WBC_ICONS.windowRestore" not in minimized_surface
    assert "wbc-browser-restore-favicon" in minimized_surface
    assert "displayBrowserFavicon" in minimized_surface
    assert "beginMinimizedDrag" in minimized_surface
    assert 'draggable="true"' not in minimized_surface
    assert 'ref={minimizedRef}' in minimized_surface
    assert 'onError={function (event) { event.currentTarget.hidden = true; }}' in minimized_surface
    assert 'className="fallback"' in minimized_surface
    assert "wbc-material-icon close-fullscreen" in source
    assert 'close-fullscreen-rounded.svg' in styles
    assert "height: 58px;" in styles
    assert "wbc-browser-title-pill" in source
    assert "wbc-browser-restore-icon" not in source
    assert "browser-status-dot running" not in source.split("function WbcBrowserFloatingSurface", 1)[1].split("function WbcMain", 1)[0]


def test_browser_floating_surfaces_use_pointer_shelf_hit_testing_and_favicon_state():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    workbench = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    assert "wc.on('page-favicon-updated'" in main
    assert "favicon: String(tab.favicon || '')" in main
    assert "favicon: ''" in main
    assert "function wbcPointInsideResourceShelf(clientX, clientY)" in source
    assert 'document.querySelector(".workbench-resource-shelf")' in source
    assert "updateResourceShelfTarget(interaction, event.clientX, event.clientY);" in source
    assert "pinBrowserFromPointerInteraction(interaction);" in source
    assert 'window.dispatchEvent(new CustomEvent("cyrene:resource-shelf-drag-state"' in source
    assert 'window.addEventListener("cyrene:resource-shelf-drag-state"' in workbench
    assert 'className="wbc-browser-title-pill"' in source
    title_pill = source.split('className="wbc-browser-title-pill"', 1)[1].split("</span>", 1)[0]
    assert "onPointerDown" not in title_pill
    assert "onDragStart" not in title_pill
    assert ".wbc-browser-restore-float.dragging" in styles
    assert ".wbc-browser-restore-favicon img" in styles
    assert "function commitFloatingFrame(" in source
    assert "function commitMinimizedFrame(" in source
    assert "ensureMinimizedDragGhost(interaction);" in source
    assert 'ghost.classList.add("dragging", "wbc-browser-drag-ghost")' in source
    assert 'stage.querySelector(".wbc-browser-restore-float")' in source
    assert ".wbc-browser-restore-float.wbc-browser-drag-ghost" in styles
    assert "position: fixed;" in styles.split(
        ".wbc-browser-restore-float.wbc-browser-drag-ghost {", 1
    )[1].split("}", 1)[0]


def test_electron_browser_bounds_follow_floating_window_with_frame_coalescing():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    set_bounds_block = main.split("  setBounds(info = {}) {", 1)[1].split("\n  setObscured(", 1)[0]
    assert "this.syncAttachedView();" in set_bounds_block
    assert "}, 32);" in set_bounds_block
    assert "}, 50);" not in set_bounds_block
    commit_block = source.split("  function commitFrame(next, area) {", 1)[1].split("\n  function stopInteraction", 1)[0]
    assert 'node.style.left = clamped.x + "px"' in commit_block
    assert "wbcNotifyBrowserLayoutChanged();" in commit_block
    move_block = source.split("  function onPointerMove(event) {", 1)[1].split("\n  function beginInteraction", 1)[0]
    begin_block = source.split("  function beginInteraction(event, kind, direction) {", 1)[1].split("\n  useWbcEffect", 1)[0]
    stop_block = source.split("  function stopInteraction() {", 1)[1].split("\n  function onPointerMove", 1)[0]
    assert "(dx * dx) + (dy * dy) < 9" in move_block
    assert "interaction.started = true;" in move_block
    assert "wbcNotifyBrowserWindowInteraction(true, interaction.kind" in move_block
    assert "started: false" in begin_block
    assert "wbcNotifyBrowserWindowInteraction(true, kind" not in begin_block
    assert "function finalizeInteraction(interaction)" in source
    assert "if (!interaction.previewReady) return;" in stop_block
    assert "interaction.pointerReleased = true;" in stop_block
    assert "if (interaction.pointerReleased) finalizeInteraction(interaction);" in stop_block
    assert "}, 750);" in move_block
    assert "if (interaction.cancelled) return;" in move_block

    browser_view = (root / "src" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")
    assert "lastBoundsRef" in browser_view
    assert "if (lastBoundsRef.current === signature) return Promise.resolve(true);" in browser_view
    assert "workbench:browser-window-interaction" in browser_view
    assert "browser-native-preview" in browser_view
    assert "topInset" not in browser_view
    assert "--browser-preview-top-inset" not in styles
    assert "inset: 0" in styles
    assert "bridge.screenshot" in browser_view
    assert "finishWindowInteraction" in browser_view
    assert "transition: true" in browser_view
    assert "interactionPreviewMountedRef" in browser_view
    preview_commit_block = browser_view.split(
        "function onInteractionPreviewLoad(event) {", 1
    )[1].split("\n  function onInteractionPreviewError", 1)[0]
    assert 'typeof imageNode.decode === "function"' in preview_commit_block
    assert preview_commit_block.count("requestAnimationFrame(function () {") == 2
    assert "interactionPreviewMountedRef.current = true;" in preview_commit_block
    assert "Promise.resolve(sendBounds(false))" in preview_commit_block
    assert 'workbench:browser-window-preview-ready' in preview_commit_block
    assert "if (!hidden)" in preview_commit_block
    assert "onLoad={onInteractionPreviewLoad}" in browser_view
    assert "onError={onInteractionPreviewError}" in browser_view
    assert "React.useLayoutEffect(function ()" not in browser_view
    interaction_block = browser_view.split(
        "function onBrowserWindowInteraction(event) {", 1
    )[1].split(
        'window.addEventListener("workbench:browser-window-interaction"', 1
    )[0]
    assert "sendBounds(false);" not in interaction_block
    assert 'if (String(detail.kind || "") !== "mode")' not in interaction_block
    assert "windowInteractionRef.current = true;" in interaction_block
    assert "if (!interactionPreviewMountedRef.current)" in interaction_block
    assert 'detail: { sessionId: electronSessionId, fallback: true }' in interaction_block
    assert "function commitInteractionDelta(interaction, dx, dy)" in source
    assert "function onBrowserWindowPreviewReady(event)" in source
    assert "if (detail.fallback) {" in source
    assert "interaction.cancelled = true;" in source
    assert "if (!interaction.previewReady) return;" in move_block
    assert "previewReady: false" in begin_block
    assert 'window.addEventListener("workbench:browser-window-preview-ready"' in begin_block
    assert 'window.removeEventListener("workbench:browser-window-preview-ready"' in stop_block
    assert 'wbcNotifyBrowserWindowInteraction(true, "mode", browserSessionId, {' in source
    assert 'wbcNotifyBrowserWindowInteraction(false, "mode", browserSessionId);' in source
    mode_transition_block = source.split(
        "  function runModeTransition(action, targetMode) {", 1
    )[1].split("\n  function measuredFloatingFrame", 1)[0]
    assert 'window.addEventListener("workbench:browser-transition-target-ready"' in mode_transition_block
    assert "applyModeAfterPreview();" in mode_transition_block
    assert mode_transition_block.index(
        'window.addEventListener("workbench:browser-transition-target-ready"'
    ) < mode_transition_block.index(
        'wbcNotifyBrowserWindowInteraction(true, "mode", browserSessionId, {'
    )
    assert "setTimeout(applyModeAfterPreview, 1800)" in mode_transition_block
    assert "function measureBrowserSurfaceForMode(targetMode)" in source
    assert 'var measurementHost = targetMode === "maximized" ? document.body : host;' in source
    assert "measurementHost.appendChild(clone);" in source
    assert 'clone.querySelector(".browser-native-surface")' in source
    assert "targetBounds: measureBrowserSurfaceForMode" in mode_transition_block
    assert "highResolution: false" in browser_view
    assert "targetWidth: 0" in browser_view
    assert "modeTargetPreviewRef.current = {" in browser_view
    assert 'workbench:browser-transition-commit-preview' in browser_view
    assert "window.ReactDOM.flushSync(commitModeAndPreview)" in mode_transition_block
    assert "function prepareModeTargetFrame(previewToken)" in browser_view
    assert 'transition: "prepare"' in browser_view
    assert 'phase: "target"' in browser_view
    assert 'workbench:browser-transition-target-ready' in browser_view
    assert "function commitPreparedModeTransition(token)" in browser_view
    assert 'transition: "commit"' in browser_view
    sync_view_block = main.split("  syncAttachedView() {", 1)[1].split("\n  setBounds(", 1)[0]
    set_bounds_index = sync_view_block.index("active.view.setBounds(targetBounds)")
    attach_index = sync_view_block.index("win.contentView.addChildView(active.view)")
    assert set_bounds_index < attach_index
    assert "active.view.setVisible(false)" in sync_view_block
    assert "active.view.setVisible(true)" in sync_view_block
    assert "active.view.setBorderRadius(targetCornerRadius)" in sync_view_block
    assert "this.borderRadius = Math.max(0, Math.min(24" in main
    assert "async pageViewportMatches(view, bounds)" in main
    assert "'({ width: window.innerWidth, height: window.innerHeight })'" in main
    assert "async settlePageViewport(view, bounds, forcePulse = false)" in main
    settle_viewport_block = main.split(
        "  async settlePageViewport(view, bounds, forcePulse = false) {", 1
    )[1].split("\n  applyPageFrameStyle(", 1)[0]
    assert "width: target.width > 9 ? target.width - 1 : target.width" in settle_viewport_block
    assert "return this.waitForPageViewport(view, target, 6);" in settle_viewport_block
    prepare_transition_block = main.split(
        "  async prepareBoundsTransition() {", 1
    )[1].split("\n  async commitBoundsTransition()", 1)[0]
    commit_transition_block = main.split(
        "  async commitBoundsTransition() {", 1
    )[1].split("\n  async settleBoundsTransition()", 1)[0]
    settle_transition_block = main.split(
        "  async settleBoundsTransition() {", 1
    )[1].split("\n  setBounds(", 1)[0]
    assert "const stagingBounds = {" in prepare_transition_block
    assert "active.view.setBounds(stagingBounds)" in prepare_transition_block
    assert "const viewportReady = await this.settlePageViewport(active.view, stagingBounds);" in prepare_transition_block
    assert "{ fast: true }" in prepare_transition_block
    assert "debug.sendCommand('Page.captureScreenshot'" in prepare_transition_block
    assert "active.view.setBounds(targetBounds)" in prepare_transition_block
    assert "const targetImage = await Promise.race" in prepare_transition_block
    assert "targetImage.getSize()" in prepare_transition_block
    assert "targetPngBase64 = targetImage.toPNG().toString('base64');" in prepare_transition_block
    assert "pngBase64: targetPngBase64" in prepare_transition_block
    assert "widthTolerance = Math.ceil((PAGE_CSS_MAX_FIT_WIDTH * 1.2) - PAGE_CSS_TARGET_WIDTH);" in main
    assert "this._pageZoomTokenByContents = new Map();" in main
    assert "this._pageZoomTokenByContents.get(contentsId) === zoomToken" in main
    assert "request = request * (PAGE_CSS_TARGET_WIDTH / innerW)" not in main
    assert "await this.settlePageViewport(active.view, targetBounds, true)" in commit_transition_block
    assert "prepared = await this.prepareBoundsTransition();" in settle_transition_block
    assert "return this.commitBoundsTransition();" in settle_transition_block
    assert "info.transition === 'prepare'" in set_bounds_block
    assert "info.transition === 'commit'" in set_bounds_block
    assert "Page.captureScreenshot" in main
    assert "cssVisualViewport" in main
    assert "const surfaceRef = React.useRef(null);" in browser_view
    assert 'const pipWindow = node.closest(".wbc-browser-window.pip")' in browser_view
    assert "const borderRadius = 0;" in browser_view
    assert "const node = surfaceRef.current;" in browser_view
    assert "contentInset" not in browser_view
    assert "x: rect.left" in browser_view
    assert "width: Math.max(0, rect.width)" in browser_view
    assert "const pageCornerRadius = pipWindow ? 8 : 0;" in browser_view
    assert "pageCornerRadius: pageCornerRadius" in browser_view
    assert "pageCornerColor" not in browser_view
    assert "data-cyrene-page-top-cover" not in main
    assert "data-cyrene-pip-root-scrollbars" in main
    assert "html::-webkit-scrollbar, body::-webkit-scrollbar" in main
    assert "if (scrollbarStyle) scrollbarStyle.remove()" in main
    assert "result.y -= cornerRadius" not in main
    assert "result.height += cornerRadius" not in main
    assert "this.applyPageFrameStyle(active.view, targetCornerRadius)" in main
    assert "this.applyPageFrameStyle(view, undefined, true)" in main
    assert "topMask" not in browser_view
    assert "topMask" not in main
    assert "borderRadius: borderRadius" in browser_view
    assert "this.bounds.height - this.bottomCornerInset" not in main
    assert "topCover" not in browser_view
    pip_bar_block = styles.split(".wbc-browser-window.pip .wbc-browser-window-bar {", 1)[1].split("\n}", 1)[0]
    assert "height: 46px" in pip_bar_block
    assert "padding: 0 9px 0 14px" in pip_bar_block
    assert "border-bottom-color:" in pip_bar_block
    assert ".wbc-browser-window.pip .wbc-browser-window-bar > *" not in styles
    assert ".wbc-browser-window.pip .browser-native-surface" in styles
    assert "className=\"browser-native-surface\"" in browser_view
    assert "border-radius: 11px" in styles
    pip_host_rule = styles.split(
        ".wbc-browser-window.pip .browser-native-host {", 2
    )[2].split("}", 1)[0]
    assert "--browser-content-inset: 3px;" in pip_host_rule
    assert "background: var(--wb-surface);" in pip_host_rule
    assert "border:" not in pip_host_rule
    assert "box-shadow:" not in pip_host_rule
    pip_surface_rule = styles.split(
        ".wbc-browser-window.pip .browser-native-surface {", 1
    )[1].split("}", 1)[0]
    assert "inset: var(--browser-content-inset);" in pip_surface_rule
    assert "border-radius: 8px;" in pip_surface_rule
    assert "background: var(--wb-surface);" in pip_surface_rule
    assert "width:" not in pip_surface_rule
    assert "height:" not in pip_surface_rule
    assert "topCover" not in main
    assert "this.repaintView(active)" in sync_view_block
    assert "wc.invalidate()" in main
    assert "settleBoundsTransition" in main
    assert "active.view.webContents.capturePage()" in main


def test_workbench_reload_restores_native_browser_after_beforeunload_guard():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    reset_block = source.split(
        "// A reload hides the native view from beforeunload", 1
    )[1].split("// Any renderer overlay", 1)[0]
    assert "wbBrowserOverlayCount = 0;" in reset_block
    assert "wbSetBrowserOverlayObscured(0);" in reset_block
    assert reset_block.index("wbBrowserOverlayCount = 0;") < reset_block.index(
        "wbSetBrowserOverlayObscured(0);"
    )


def _run_browser_avoidance_plan(*args):
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    function_source = "function wbcBrowserAvoidancePlan" + source.split(
        "function wbcBrowserAvoidancePlan", 1
    )[1].split("\nfunction wbcNotifyBrowserLayoutChanged", 1)[0]
    script = f"""
{function_source}
const result = wbcBrowserAvoidancePlan(...{json.dumps(list(args))});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_browser_avoidance_plan_uses_the_wider_readable_lane():
    assert _run_browser_avoidance_plan(100, 800, 650, 200, 14) == {
        "side": "left",
        "start": 0,
        "end": 264,
    }
    assert _run_browser_avoidance_plan(100, 800, 150, 200, 14) == {
        "side": "right",
        "start": 264,
        "end": 0,
    }


def test_browser_avoidance_plan_declines_centered_or_too_narrow_layouts():
    assert _run_browser_avoidance_plan(100, 800, 400, 200, 14) is None
    assert _run_browser_avoidance_plan(100, 800, 430, 350, 14) is None
    assert _run_browser_avoidance_plan(100, 800, 920, 200, 14) is None


def test_workbench_chat_reflows_only_entries_intersecting_the_browser_pip():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert 'data-wbc-thread-item="true"' in source
    assert 'stage.querySelector(".wbc-browser-window.pip")' in source
    assert 'window.addEventListener("workbench:browser-layout", scheduleBrowserAvoidance)' in source
    assert "var scheduleStickyViewportRestore = useWbcCallback(function () {" in source
    assert source.count("scheduleStickyViewportRestore();") >= 6
    assert "new MutationObserver(function ()" in source
    assert "for (var pass = 0; pass < 5; pass++)" in source
    assert 'item.offsetTop + item.offsetHeight <= contentTop' not in source
    assert 'candidate.offsetTop + candidate.offsetHeight <= contentTop' in source
    assert 'item.style.setProperty("--wbc-browser-avoid-start"' in source
    assert 'item.style.setProperty("--wbc-browser-avoid-end"' in source
    assert "if (!preserveViewport) return;" in source
    on_scroll_block = source.split("  function onScroll() {", 1)[1].split("\n  useWbcEffect", 1)[0]
    assert "scheduleBrowserAvoidance();" in on_scroll_block
    assert "scheduleBrowserAvoidance(false);" not in on_scroll_block
    assert "avoidanceScrollingRef.current = true;" in on_scroll_block
    assert "}, 120);" in on_scroll_block
    schedule_block = source.split(
        "var scheduleBrowserAvoidance = useWbcCallback(function () {", 1
    )[1].split("// Track whether the user is reading scrollback", 1)[0]
    assert "if (avoidanceScrollingRef.current) return;" in schedule_block
    assert "applyBrowserAvoidance(true);" in schedule_block
    assert "applyBrowserAvoidance(false);" not in schedule_block
    sticky_restore_block = source.split(
        "var scheduleStickyViewportRestore = useWbcCallback(function () {", 1
    )[1].split("var applyBrowserAvoidance", 1)[0]
    assert "if (!stickRef.current || stickyRestoreRafRef.current) return;" in sticky_restore_block
    assert "stickyRestoreRafRef.current = requestAnimationFrame(function () {" in sticky_restore_block
    assert "thread.scrollTop = thread.scrollHeight;" in sticky_restore_block
    assert "if (!thread || !stickRef.current) return;" in sticky_restore_block
    thread_item_styles = styles.split(".wbc-thread-item {", 1)[1].split("}", 1)[0]
    assert "padding-inline-start: var(--wbc-browser-avoid-start, 0px);" in thread_item_styles
    assert "padding-inline-end: var(--wbc-browser-avoid-end, 0px);" in thread_item_styles
    assert ".wbc-thread-item > .wbc-msg.user" in styles
    assert ".wbc-thread-item > .wbc-msg.assistant" in styles


def test_active_browser_tab_uses_standard_text_color():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    active_tab_styles = styles.split(".browser-tab.active {", 1)[1].split("}", 1)[0]
    assert "color: var(--wb-text, var(--text));" in active_tab_styles
    assert "color: var(--wb-accent, var(--accent));" not in active_tab_styles


def test_electron_browser_video_fullscreen_is_platform_aware_and_shared_with_ui():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    browser_view = (root / "src" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    create_view = main.split("  createView() {", 1)[1].split("\n  setContext(", 1)[0]
    assert "disableHtmlFullscreenWindowResize: true" in create_view
    assert "wc.on('enter-html-full-screen'" in create_view
    assert "this.enterVideoFullscreen(view)" in create_view
    assert "wc.on('leave-html-full-screen'" in create_view
    assert "this.finishVideoFullscreen(view)" in create_view

    enter_fullscreen = main.split("  async enterVideoFullscreen(view) {", 1)[1].split("\n  finishVideoFullscreen(", 1)[0]
    assert "external: isMac" in enter_fullscreen
    assert "if (isMac)" in enter_fullscreen
    assert "const videoWindow = new BrowserWindow" in enter_fullscreen
    assert "videoWindow.setFullScreen(true)" in enter_fullscreen
    assert "else if ((isWindows || isLinux) && mainWindow && !mainWindow.isDestroyed())" in enter_fullscreen
    assert "mainWindow.setFullScreen(true)" in enter_fullscreen
    assert "this._mainFullscreenLeaveHandler" in enter_fullscreen
    assert "this.requestVideoFullscreenExit()" in enter_fullscreen

    finish_fullscreen = main.split("  finishVideoFullscreen(view) {", 1)[1].split("\n  createView()", 1)[0]
    assert "!this._mainWindowWasFullScreen" in finish_fullscreen
    assert "mainWindow.setFullScreen(false)" in finish_fullscreen
    assert "mainWindow.removeListener('leave-full-screen', this._mainFullscreenLeaveHandler)" in finish_fullscreen

    sync_view = main.split("  syncAttachedView() {", 1)[1].split("\n  async settleBoundsTransition", 1)[0]
    assert "const fullscreenTab = this.fullscreenTab()" in sync_view
    assert "const targetBounds = fullscreenActive ? this.fullscreenBounds(win)" in sync_view
    assert "this.pageViewBounds(" in sync_view
    assert "win.contentView.addChildView(active.view)" in sync_view
    assert "videoFullscreen:" in main
    assert "platform: process.platform" in main

    assert 'className="browser-video-fullscreen-overlay"' in browser_view
    assert "已在全屏播放" in browser_view
    assert "视频正在独立的全屏窗口中播放" in browser_view
    assert ".browser-video-fullscreen-overlay" in styles

    session_guards = main.split("function installBrowserSessionGuards(", 1)[1].split("\nclass BrowserTabManager", 1)[0]
    assert "permission === 'fullscreen'" in session_guards
    assert "browserSession.setPermissionCheckHandler" in session_guards
    assert "browserSession.setPermissionRequestHandler" in session_guards
    assert "callback(browserPermissionAllowed(permission))" in session_guards


def test_electron_browser_tab_attaches_before_navigation_and_survives_media_load_errors():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    create_tab = main.split("  async createTab(", 1)[1].split("\n  activateTab(", 1)[0]

    attach_index = create_tab.index("this.syncAttachedView()")
    load_index = create_tab.index("await view.webContents.loadURL(tab.url)")
    assert attach_index < load_index
    assert "tab.lastLoadError = String" in create_tab
    assert "Browser tab navigation reported an error" in create_tab
    assert "return tab" in create_tab


def test_workbench_browser_window_frame_stays_inside_chat_region():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    helper_source = "function wbcClampBrowserWindowFrame(" + source.split(
        "function wbcClampBrowserWindowFrame(", 1
    )[1].split("function wbcNotifyBrowserLayoutChanged", 1)[0]
    script = f"""
eval({json.dumps(helper_source)});
const result = [
  wbcClampBrowserWindowFrame({{ x: 900, y: 500, width: 400, height: 300 }}, 1000, 600, 240, 180),
  wbcClampBrowserWindowFrame({{ x: 0, y: 0, width: 1000, height: 600 }}, 1000, 600, 240, 180),
  wbcClampBrowserWindowFrame({{ x: -20, y: -30, width: 80, height: 90 }}, 300, 220, 240, 180)
];
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == [
        {"x": 600, "y": 300, "width": 400, "height": 300},
        {"x": 0, "y": 0, "width": 1000, "height": 600},
        {"x": 0, "y": 0, "width": 240, "height": 180},
    ]


def test_workbench_chat_tracks_actual_model_from_live_llm_events():
    result = _run_workbench_runtime_js(
        """
(() => {
  const model = { sendMessage: () => new Promise(() => {}) };
  WorkbenchChatRuntimes.start("chat_model", { message: "hello" }, model);
  global.__wbcSseHandler({
    type: "llm_call",
    status: "started",
    session_id: "chat_model",
    model: "mimo-v2.5"
  });
  return WorkbenchChatRuntimes.snapshot().chat_model.activeModel;
})()
"""
    )

    assert result == "mimo-v2.5"


def test_workbench_chat_groups_reasoning_and_tools_until_visible_message():
    result = _run_workbench_runtime_js(
        """
(() => {
  const model = {
    sendMessage: (_chatId, _input, handlers) => {
      global.__wbcStreamHandlers = handlers;
      return new Promise(() => {});
    }
  };
  WorkbenchChatRuntimes.start("chat_activities", { message: "hello" }, model);

  global.__wbcSseHandler({
    type: "llm_call",
    status: "started",
    event_id: "llm_1_started",
    session_id: "chat_activities",
    model: "mimo-v2.5"
  });
  global.__wbcStreamHandlers.onReasoningStart();
  global.__wbcStreamHandlers.onReasoningDelta("first reasoning");
  global.__wbcStreamHandlers.onReasoningDone("first reasoning");
  global.__wbcSseHandler({
    type: "llm_call",
    status: "completed",
    event_id: "llm_1",
    session_id: "chat_activities",
    model: "mimo-v2.5",
    response: { reasoning_content: "first reasoning" }
  });
  global.__wbcSseHandler({
    type: "tool_call",
    session_id: "chat_activities",
    tool: "read_file",
    args: { path: "a.md" }
  });

  global.__wbcSseHandler({
    type: "llm_call",
    status: "started",
    event_id: "llm_2_started",
    session_id: "chat_activities",
    model: "mimo-v2.5"
  });
  global.__wbcStreamHandlers.onReasoningStart();
  global.__wbcStreamHandlers.onReasoningDelta("second reasoning");
  global.__wbcStreamHandlers.onReasoningDone("second reasoning");
  global.__wbcSseHandler({
    type: "llm_call",
    status: "completed",
    event_id: "llm_2",
    session_id: "chat_activities",
    model: "mimo-v2.5",
    response: { reasoning_content: "second reasoning" }
  });
  global.__wbcSseHandler({
    type: "tool_call",
    session_id: "chat_activities",
    tool: "list_skills",
    args: {}
  });
  global.__wbcSseHandler({
    type: "tool_call",
    session_id: "chat_activities",
    tool: "read_file",
    args: { path: "b.md" }
  });

  const runtime = WorkbenchChatRuntimes.snapshot().chat_activities;
  return runtime.activities.map(activity => ({
    id: activity.id,
    reasoning: activity.reasoning,
    tools: activity.progress.map(entry => entry.text)
  }));
})()
"""
    )

    assert result == [{
        "id": "activity_1",
        "reasoning": "first reasoning\n\nsecond reasoning",
        "tools": ["read_file", "list_skills", "read_file"],
    }]


def test_workbench_chat_dedupes_cross_connection_llm_event_race():
    result = _run_workbench_runtime_js(
        """
(() => {
  const model = {
    sendMessage: (_chatId, _input, handlers) => {
      global.__wbcStreamHandlers = handlers;
      return new Promise(() => {});
    }
  };
  WorkbenchChatRuntimes.start("chat_race", { message: "hello" }, model);
  global.__wbcSseHandler({
    type: "llm_call",
    status: "started",
    event_id: "race_started",
    session_id: "chat_race",
    model: "mimo-v2.5"
  });
  global.__wbcSseHandler({
    type: "llm_call",
    status: "completed",
    event_id: "race_completed",
    session_id: "chat_race",
    model: "mimo-v2.5",
    response: { reasoning_content: "late reasoning" }
  });
  // The direct response stream can be delivered after the SSE completion even
  // though the server emitted its chunks first. It must reuse the same card.
  global.__wbcStreamHandlers.onReasoningStart();
  global.__wbcStreamHandlers.onReasoningDelta("late reasoning");
  global.__wbcStreamHandlers.onReasoningDone("late reasoning");
  const activities = WorkbenchChatRuntimes.snapshot().chat_race.activities;
  return { count: activities.length, reasoning: activities[0].reasoning };
})()
"""
    )

    assert result == {"count": 1, "reasoning": "late reasoning"}


def test_workbench_chat_visible_message_closes_activity_group():
    result = _run_workbench_runtime_js(
        """
(() => {
  const model = {
    sendMessage: (_chatId, _input, handlers) => {
      global.__wbcStreamHandlers = handlers;
      return new Promise(() => {});
    }
  };
  WorkbenchChatRuntimes.start("chat_continuous", { message: "hello" }, model);

  function runReasoningCall(number, reasoning) {
    global.__wbcSseHandler({
      type: "llm_call",
      status: "started",
      event_id: `continuous_${number}_started`,
      session_id: "chat_continuous",
      model: "mimo-v2.5"
    });
    global.__wbcStreamHandlers.onReasoningStart();
    global.__wbcStreamHandlers.onReasoningDelta(reasoning);
    global.__wbcStreamHandlers.onReasoningDone(reasoning);
    global.__wbcSseHandler({
      type: "llm_call",
      status: "completed",
      event_id: `continuous_${number}_completed`,
      session_id: "chat_continuous",
      model: "mimo-v2.5",
      response: { reasoning_content: reasoning }
    });
  }

  runReasoningCall(1, "first thought");
  runReasoningCall(2, "second thought");
  global.__wbcSseHandler({
    type: "tool_call",
    session_id: "chat_continuous",
    tool: "read_file",
    args: { path: "boundary.md" }
  });
  global.__wbcStreamHandlers.onIntermediateMessage({
    message: {
      id: "mid_1",
      role: "assistant",
      content: "中途回复",
      createdAt: "2026-01-01T00:00:02Z"
    }
  });
  runReasoningCall(3, "thought after tool");

  return WorkbenchChatRuntimes.snapshot().chat_continuous.activities.map(activity => ({
    reasoning: activity.reasoning,
    tools: activity.progress.map(entry => entry.text)
  }));
})()
"""
    )

    assert result == [
        {
            "reasoning": "first thought\n\nsecond thought",
            "tools": ["read_file"],
        },
        {
            "reasoning": "thought after tool",
            "tools": [],
        },
    ]


def test_workbench_chat_tool_preamble_splits_current_llm_reasoning_after_message():
    result = _run_workbench_runtime_js(
        """
(() => {
  const model = {
    sendMessage: (_chatId, _input, handlers) => {
      global.__wbcStreamHandlers = handlers;
      return new Promise(() => {});
    }
  };
  WorkbenchChatRuntimes.start("chat_preamble", { message: "send it" }, model);

  function runReasoningCall(number, reasoning) {
    global.__wbcSseHandler({
      type: "llm_call", status: "started", event_id: `preamble_${number}_started`,
      session_id: "chat_preamble", model: "mimo-v2.5"
    });
    global.__wbcStreamHandlers.onReasoningStart();
    global.__wbcStreamHandlers.onReasoningDelta(reasoning);
    global.__wbcStreamHandlers.onReasoningDone(reasoning);
    global.__wbcSseHandler({
      type: "llm_call", status: "completed", event_id: `preamble_${number}_completed`,
      session_id: "chat_preamble", model: "mimo-v2.5",
      response: { reasoning_content: reasoning }
    });
  }

  runReasoningCall(1, "check the file");
  global.__wbcSseHandler({
    type: "tool_call", session_id: "chat_preamble", tool: "Bash",
    args: { command: "ls photo.jpg" }
  });
  runReasoningCall(2, "send the existing file");
  global.__wbcStreamHandlers.onIntermediateMessage({
    message: {
      id: "mid_preamble",
      role: "assistant",
      content: "找到了，我发给你。",
      createdAt: new Date().toISOString(),
      opensActivity: true
    }
  });
  global.__wbcSseHandler({
    type: "tool_call", session_id: "chat_preamble", tool: "send_file",
    args: { path: "photo.jpg" }
  });

  const runtime = WorkbenchChatRuntimes.snapshot().chat_preamble;
  return {
    activities: runtime.activities.map(activity => ({
      reasoning: activity.reasoning,
      tools: activity.progress.map(entry => entry.text),
      closed: !!activity.timelineClosed
    })),
    segments: runtime.segments.map(segment => segment.message.content)
  };
})()
"""
    )

    assert result == {
        "activities": [
            {
                "reasoning": "check the file",
                "tools": ["Bash"],
                "closed": True,
            },
            {
                "reasoning": "send the existing file",
                "tools": ["send_file"],
                "closed": False,
            },
        ],
        "segments": ["找到了，我发给你。"],
    }


def test_workbench_chat_merges_tool_only_calls_without_visible_boundary():
    result = _run_workbench_runtime_js(
        """
(() => {
  const model = {
    sendMessage: (_chatId, _input, handlers) => {
      global.__wbcStreamHandlers = handlers;
      return new Promise(() => {});
    }
  };
  WorkbenchChatRuntimes.start("chat_tools", { message: "hello" }, model);
  global.__wbcSseHandler({
    type: "llm_call", status: "started", event_id: "s1",
    session_id: "chat_tools", model: "mimo-v2.5"
  });
  global.__wbcSseHandler({
    type: "llm_call", status: "completed", event_id: "c1",
    session_id: "chat_tools", model: "mimo-v2.5", response: {}
  });
  global.__wbcSseHandler({
    type: "tool_call", session_id: "chat_tools", tool: "read_file", args: { path: "a" }
  });
  global.__wbcSseHandler({
    type: "llm_call", status: "started", event_id: "s2",
    session_id: "chat_tools", model: "mimo-v2.5"
  });
  global.__wbcSseHandler({
    type: "llm_call", status: "completed", event_id: "c2",
    session_id: "chat_tools", model: "mimo-v2.5", response: {}
  });
  global.__wbcSseHandler({
    type: "tool_call", session_id: "chat_tools", tool: "list_skills", args: {}
  });
  const activities = WorkbenchChatRuntimes.snapshot().chat_tools.activities;
  return activities.map(activity => activity.progress.map(entry => entry.text));
})()
"""
    )

    assert result == [["read_file", "list_skills"]]


def test_workbench_chat_model_label_and_context_usage_use_live_data():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    composer = source.split("function WbcComposer(", 1)[1].split(
        "// Context picker popup", 1
    )[0]
    overview = source.split("function WbcOverviewTab", 1)[1].split(
        "function wbcBlockLabel", 1
    )[0]
    usage = source.split("function WbcContextUsage", 1)[1].split(
        "function WbcQuickActionItems", 1
    )[0]

    assert "var modelName = wbcCurrentModel(chat, project, runtime, null);" in composer
    assert "var liveData = useWbcLiveChatMetrics(chat, !!runtime);" in overview
    assert "wbcCurrentModel(chat, null, runtime, liveData)" in overview
    assert "var usage = (liveData && liveData.usage) || chat.usage || {};" in overview
    assert '<WbcOverviewUsage usage={usage} />' in overview
    assert '<WbcContextUsage data={liveData} compact={true} />' in overview
    assert '{!compact && (' not in usage
    assert 'className={"wbc-ctx-bar level-" + fillLevel}' in usage
    assert 'className="wbc-ctx-splitbar"' in usage
    assert 'className="wbc-ctx-split-label"' in usage
    assert 'workbenchChat.ctx.compactAt' in usage
    assert 'wbcT("chat.runSummary"' not in overview
    assert "WbcQuickActionItems" not in overview


def test_workbench_chat_delete_detaches_local_fork_markers():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    handler = source.split("function handleDeleteChat(chatId)", 1)[1].split("function handleToTask", 1)[0]

    assert "function detachDeletedForkSource(item)" in handler
    assert "delete cleaned.forkedFromChatId" in handler
    assert "delete cleaned.forkedAtMessageId" in handler
    assert "delete cleaned.forkMessage" in handler
    assert ".map(detachDeletedForkSource)" in handler
    assert "setActiveChat(function (prev) { return detachDeletedForkSource(prev); })" in handler
    assert handler.index("setChats(function (prev)") < handler.index("model.deleteChat(chatId)")
    assert "next.splice(Math.min(Math.max(deletedIndex, 0), next.length), 0, deletedItem)" in handler


def test_workbench_chat_card_menu_can_rename_the_target_chat():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    rail = source.split("function WbcRail(", 1)[1].split(
        "// Conversation main (column 3)", 1
    )[0]
    rename_dialog = source.split("function WbcRenameDialog", 1)[1].split(
        "function WbcRail(", 1
    )[0]

    assert "onRename={handleRenameChat}" in source
    assert 'wbcT("workbenchChat.rename", "Rename chat")' in rail
    assert 'role="dialog"' in rename_dialog
    assert "maxLength={60}" in rename_dialog
    assert "setRenameChat(chat)" in rail
    assert "window.prompt(" not in rail
    assert "onRename(chat.id, nextTitle)" in rename_dialog
    assert "prev && prev.id === chat.id" in source
    assert '(menuId ? " menu-active" : "")' in rail
    menu_active_css = styles.split(".wbc-chat-list.menu-active {", 1)[1].split("}", 1)[0]
    assert "z-index: 200;" in menu_active_css
    assert "pointer-events: none;" in menu_active_css
    assert ".wbc-chat-list.menu-active .wbc-chat-card.menu-open" in styles
    assert "pointer-events: auto;" in styles.split(
        ".wbc-chat-list.menu-active .wbc-chat-card.menu-open", 1
    )[1].split("}", 1)[0]


def test_workbench_chat_card_menu_can_pin_and_sort_conversations():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    shell = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    rail = source.split("function WbcRail(", 1)[1].split(
        "// Conversation main (column 3)", 1
    )[0]

    assert "function wbcOrderChatsByPinned(chats, pinnedChatIds)" in source
    assert "return leftPinned ? -1 : 1" in source
    assert "pinnedChatIds={pinnedChatIds}" in source
    assert "onTogglePinned={onTogglePinnedChat}" in source
    assert 'wbcT("workbenchChat.pin", "Pin chat")' in rail
    assert 'wbcT("workbenchChat.unpin", "Unpin chat")' in rail
    assert 'className="wbc-chat-card-pin"' in rail
    assert "onTogglePinnedChat: function (chat, pinned)" in shell
    assert 'togglePinnedSession({ id: chat.id, kind: "chat" }, pinned)' in shell


def test_workbench_chat_cards_reorder_and_open_when_dropped_on_conversation():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )
    rail = source.split("function WbcRail(", 1)[1].split(
        "// Conversation main (column 3)", 1
    )[0]
    main = source.split("function WbcMain(", 1)[1].split(
        "function WbcAgentMessage", 1
    )[0]

    assert 'var WBC_CHAT_DRAG_MIME = "application/x-cyrene-chat+json";' in source
    assert "function wbcSetChatDrag(event, chat)" in source
    assert "function wbcReadChatDrag(event)" in source
    assert "WBC_CHAT_ORDER_PREFIX" in rail
    assert "localStorage.setItem(WBC_CHAT_ORDER_PREFIX" in rail
    assert 'draggable="true"' in rail
    assert "wbcMoveChatOrder(order, dragState.movingId" in rail
    assert "event.dataTransfer.setDragImage(" in rail
    assert "event.altKey" in rail
    assert "onOpenDroppedChat={function (chatId)" in source
    assert "onDrop={handleChatDrop}" in main
    assert 'className="wbc-chat-open-drop-hint"' in main
    assert ".wbc-chat-card.dragging" in styles
    assert ".wbc-main.chat-drop-active" in styles
    drop_border_css = styles.split(
        ".wbc-main.chat-drop-active::after {", 1
    )[1].split("}", 1)[0]
    assert "inset: 0;" in drop_border_css
    assert "z-index: 65;" in drop_border_css
    assert "border: 2px solid" in drop_border_css
    assert i18n.count('"workbenchChat.dragChat"') == 2
    assert i18n.count('"workbenchChat.chatMoved"') == 2
    assert i18n.count('"workbenchChat.dropToOpen"') == 2


def test_workbench_chat_rename_dialog_uses_compact_vertical_spacing():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    body = styles.split(".wbc-rename-body {", 1)[1].split("}", 1)[0]
    foot = styles.rsplit(".wbc-rename-foot {", 1)[1].split("}", 1)[0]

    assert "gap: 8px;" in body
    assert "padding: 16px 18px 8px;" in body
    assert "padding: 12px 18px;" in foot


def test_workbench_branch_tree_uses_compact_git_history_layout():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    branch = source.split("function WbcBranchTab", 1)[1].split(
        "// ---------------------------------------------------------------------------\n// Right context panel", 1
    )[0]

    assert "wbc-branch-hint" not in branch
    assert '"--wbc-branch-rail": (maxDepth * 14 + 30) + "px"' in branch
    assert "CURVE_W = 14, CURVE_H = 24" in source
    assert 'grid-template-columns: 42px minmax(0, 1fr) max-content' in styles
    assert "height: 56px" in styles.split(".wbc-branch-button", 1)[1].split("}", 1)[0]
    card_styles = styles.split(".wbc-branch-card", 1)[1].split("}", 1)[0]
    assert "height: 44px" in card_styles
    assert "border: 1px solid" in card_styles
    assert ".wbc-branch-line.main-lane" in styles
    assert ".wbc-branch-line.fork-lane" in styles
    assert "border-top-right-radius: 14px 24px" in styles
    assert "-webkit-line-clamp" not in styles.split(".wbc-branch-text", 1)[1].split("}", 1)[0]


def test_workbench_chat_switches_stop_to_guidance_while_running():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(
        encoding="utf-8"
    )
    composer = source.split("function WbcComposer(", 1)[1].split(
        "// Context picker popup", 1
    )[0]

    textarea = composer.split("<textarea", 1)[1].split("/>", 1)[0]
    keydown = composer.split("function onKeyDown(event) {", 1)[1].split(
        "function pickFiles()", 1
    )[0]

    assert "disabled={running}" not in textarea
    assert "if (running) return;" not in keydown
    assert "var hasRuntimeGuidance = running && !!draft.trim();" in composer
    assert "running && !hasRuntimeGuidance ? onInterrupt : submit" in composer
    assert "if (running) { onInterrupt(); return; }" not in composer
    assert "输入内容以引导正在运行的 Agent" in (
        root / "src" / "webui" / "frontend" / "workbench-i18n.jsx"
    ).read_text(encoding="utf-8")
    assert "workbench-chat.js?v=0.7.0b12" in index
    assert "workbench-i18n.js?v=0.7.0b12" in index


def test_workbench_guidance_is_optimistic_and_completed_tools_do_not_spin():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    guidance_model = source.split("function sendGuidance", 1)[1].split(
        "function answerChat", 1
    )[0]
    guidance_handler = source.split("function handleGuidance", 1)[1].split(
        "function handleAnswer", 1
    )[0]
    trace_card = source.split("function WbcTraceCard", 1)[1].split(
        "function WbcAssistantMessage", 1
    )[0]

    assert "timeout: 0" in guidance_model
    assert 'id: "guidance_pending_" + clientRequestId' in guidance_handler
    assert "optimistic: true" in guidance_handler
    assert "response.userMessage" in guidance_handler
    assert "item.clientRequestId" in guidance_handler
    assert 'status: (toolStarted || toolProgress) ? "running" : "completed"' in source
    assert 'event.type === "tool_call_progress"' in source
    assert 'className="wbc-transfer-progress"' in trace_card
    assert 'entry.status === "running"' in trace_card


def test_workbench_tool_start_is_rendered_then_completed_in_place():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    runtime = source.split("function onSseEvent(event)", 1)[1].split(
        'window.CyreneUI.require("events").subscribe(onSseEvent)', 1
    )[0]
    activity_card = source.split("function WbcLiveActivityCard", 1)[1].split(
        "function WbcLiveMessage", 1
    )[0]
    assert (
        'event.type === "tool_call_started" || event.type === "tool_call" || '
        'event.type === "tool_call_finished"'
    ) in runtime
    assert 'toolCallId: String(event.tool_call_id || "")' in runtime
    assert 'status: (toolStarted || toolProgress) ? "running" : "completed"' in runtime
    assert 'progressCurrent: toolProgress ?' in runtime
    assert "wbcMergeToolLifecycleEntry(item, entry, terminalToolEvent)" in runtime
    assert "progress: mergeToolProgress(activity && activity.progress)" in runtime
    assert "matchedToolCall" in runtime
    assert 'entry.toolCallId || i' in source
    assert 'entry.kind === "tool" && entry.status === "running"' in activity_card
    assert "hasRunningTools && !hasReplyText" in activity_card
    assert 'type === "run_finalizing" && handlers.onFinalizing' in source
    assert "wbcFinalizeRuntime(cur)" in source


def test_workbench_marks_run_finalizing_before_workspace_save():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "route" / "workbench" / "chat.py").read_text(
        encoding="utf-8"
    )
    run_streaming = source.split("async def run_streaming", 1)[1].split(
        "run, _is_new", 1
    )[0]
    normal_completion = run_streaming.split("if not run.saw_reply_events:", 1)[1]

    reply_done = normal_completion.index('"type": "reply_done"')
    finalizing = normal_completion.index('"type": "run_finalizing"')
    workspace_finalize = normal_completion.index("await _finalize_workspace_changes(")
    saved = normal_completion.index('"type": "saved"')

    assert reply_done < finalizing < workspace_finalize < saved


def test_workbench_assistant_footer_formats_persisted_processing_duration():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    helper = "function wbcFormatProcessingDuration(" + source.split(
        "function wbcFormatProcessingDuration(", 1
    )[1].split("function wbcConfirmOptimisticMessage", 1)[0]
    script = f"""
{helper}
const values = [undefined, -1, 0, 500, 1000, 61000, 3661000];
process.stdout.write(JSON.stringify(values.map(wbcFormatProcessingDuration)));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    assert json.loads(completed.stdout) == [
        "",
        "",
        "<0.1s",
        "0.5s",
        "1s",
        "1m 1s",
        "1h 1m",
    ]

    footer = source.split('<div className="wbc-msg-foot">', 1)[1].split(
        "</div>", 1
    )[0]
    assert footer.index("wbcFormatTime(msg.createdAt)") < footer.index(
        "processingDuration"
    )
    assert footer.index("processingDuration") < footer.index("total_tokens")


def test_workbench_terminal_reply_snapshot_is_authoritative_after_streamed_calls():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "route" / "workbench" / "chat.py").read_text(
        encoding="utf-8"
    )
    run_streaming = source.split("async def run_streaming", 1)[1].split(
        "run, _is_new", 1
    )[0]
    normal_completion = run_streaming.split("if not run.saw_reply_events:", 1)[1]
    fallback_body, after_fallback = normal_completion.split(
        "# A streamed model call can finish", 1
    )

    assert '"type": "reply_delta"' in fallback_body
    assert '"type": "reply_done"' not in fallback_body
    assert 'await run.publish({"type": "reply_done", "response": reply})' in after_fallback


def test_workbench_pip_reflow_does_not_compete_with_scroll_anchor():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    main = source.split("function WbcMain", 1)[1].split(
        "function WbcQuestionPrompt", 1
    )[0]
    thread_rule = styles.split(".wbc-thread {", 1)[1].split("}", 1)[0]

    assert "avoidanceApplyingRef.current = true;" in main
    assert main.count("if (avoidanceApplyingRef.current) return;") == 2
    assert "avoidanceApplyingRef.current = false;" in main
    assert "overflow-anchor: none;" in thread_rule
    assert "finalizing={!!msg.runtimeFinalizing}" in main
    assert 'wbcT("workbenchChat.finalizing", "Reply complete · saving results…")' in source


def test_workbench_permission_prompt_renders_every_scoped_option():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    prompt = source.split("function WbcQuestionPrompt", 1)[1].split(
        "function WbcErrorNotice", 1
    )[0]

    assert '(options.length ? options : ["在本次会话同意", "同意一次", "拒绝"]).map' in prompt
    assert "onAnswer(pq.id, opt)" in prompt
    assert "options[options.length - 1]" not in prompt.split(") : (", 1)[0]


def test_workbench_context_tab_has_live_session_inbox_card():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    css = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    context_tab = source.split("function WbcContextTab", 1)[1].split(
        "function WbcArtifactsTab", 1
    )[0]
    live_hook = source.split("function useWbcLiveInbox", 1)[1].split(
        "function wbcInboxStatus", 1
    )[0]
    inbox_card = source.split("function WbcInboxCard", 1)[1].split(
        "function WbcContextTab", 1
    )[0]
    inbox_call = '<WbcInboxCard chat={chat} running={!!runtime} hideTitle={true} />'
    assert inbox_call in context_tab
    assert context_tab.index(inbox_call) > context_tab.index(
        'workbenchChat.conversationContext'
    )
    assert context_tab.index(inbox_call) < context_tab.index(
        'workbenchChat.usedToolPackages'
    )
    assert '"/inbox"' in source
    assert 'cache: "no-store"' in live_hook
    assert "timer = setTimeout(load, delay)" in live_hook
    assert "(payload && payload.active) || activeHint ? 1000 : 5000" in live_hook
    assert 'requestOptions.signal = requestController.signal' in live_hook
    assert "requestController.abort()" in live_hook
    assert "if (!cancelled) {" in live_hook
    assert "setInterval(load" not in live_hook
    assert "wbcInboxSnapshotCache = new Map()" in source
    assert "wbcCacheInbox(chatId, payload)" in live_hook
    assert "loading: !nextData" in live_hook
    assert "[chatId, retryRevision, activeHint]" in live_hook
    assert "chat.updatedAt" not in live_hook
    assert 'workbenchChat.inbox.queue' in inbox_card
    assert 'workbenchChat.inbox.queueEmpty' in inbox_card
    assert 'className={"wbc-inbox-queue-count"' in inbox_card
    assert "queueDepth === 0 ? (" in inbox_card
    assert 'queueDepth === null ? "—" : queueDepth' in inbox_card
    inbox_head_css = css.split("\n.wbc-inbox-head {", 1)[1].split("}", 1)[0]
    assert "justify-content: flex-start;" in inbox_head_css
    assert "align-items: baseline;" in inbox_head_css
    assert 'className="wbc-side-empty"' in inbox_card
    assert 'className="wbc-inbox-summary"' not in inbox_card
    assert "liveView.error ? (" in inbox_card
    assert ") : feed.length === 0 ? (" in inbox_card
    assert 'workbenchChat.inbox.live' not in inbox_card
    assert 'wbc-inbox-live' not in inbox_card
    assert "wbc-inbox-run-row" not in inbox_card
    assert 'workbenchChat.inbox.guidancePending' not in inbox_card
    assert 'workbenchChat.inbox.activeTools' not in inbox_card
    assert 'wbcT("toolName." + item.toolName, item.toolName)' in source
    assert "wbcInboxArgumentPreview(tool.arguments)" in inbox_card
    assert "item.toolCallId && <code" not in inbox_card
    assert 'aria-live="polite"' in source
    assert ".wbc-inbox-card" in css
    inbox_card_css = css.split(".wbc-inbox-card", 1)[1].split(
        ".wbc-inbox-head,", 1
    )[0]
    inbox_meta_css = css.split(".wbc-inbox-event-meta {", 2)[2].split("}", 1)[0]
    assert "padding-bottom: 10px" in inbox_card_css
    assert "justify-content: flex-end" in inbox_meta_css
    assert ".wbc-inbox-event-meta code" not in css


def test_tool_package_settings_are_scoped_and_context_shows_agent_disclosure():
    root = Path(__file__).resolve().parents[1]
    overlay = (
        root / "src" / "webui" / "frontend" / "settings-overlay.jsx"
    ).read_text(encoding="utf-8")
    chat = (
        root / "src" / "webui" / "frontend" / "workbench-chat.jsx"
    ).read_text(encoding="utf-8")
    i18n = (
        root / "src" / "webui" / "frontend" / "workbench-i18n.jsx"
    ).read_text(encoding="utf-8")
    css = (
        root / "src" / "webui" / "frontend" / "workbench.css"
    ).read_text(encoding="utf-8")
    classic_settings = root / "src" / "webui" / "static" / "app" / "settings.jsx"

    capabilities = overlay.split("function CapabilitiesPanel", 1)[1].split(
        "function DataPanel", 1
    )[0]
    context_tab = chat.split("function WbcContextTab", 1)[1].split(
        "function WbcArtifactsTab", 1
    )[0]
    assert 'group.kind === "package"' in capabilities
    assert 'FieldRow(' in capabilities
    assert 'saveToolGroup(group.id, !packageEnabled)' in capabilities
    assert 't("toolName." + group.wire_name)' in capabilities
    assert 't("toolPackageDesc." + group.id)' in capabilities
    assert "toggleTool(" not in capabilities
    assert "toolList.map" not in capabilities
    assert "saveBrowserTools" not in overlay

    disclosure = chat.split("function wbcUsedToolPackages", 1)[1].split(
        "function WbcContextTab", 1
    )[0]
    assert 'fetch("/api/settings/tools"' not in context_tab
    assert '"cyrene-tool-packages-change"' not in context_tab
    assert "wbcUsedToolPackages(chat, runtime)" in context_tab
    assert "message.tools" in disclosure
    assert "runtime.activities" in disclosure
    assert "runtime.segments" in disclosure
    assert "WBC_PROGRESSIVE_TOOL_PACKAGES.has(name)" in disclosure
    assert "workbenchChat.usedToolPackages" in context_tab
    assert "usedToolPackages.length === 0" in context_tab
    assert "workbenchChat.noUsedToolPackages" in context_tab
    assert 'className="wbc-side-empty"' in context_tab
    assert ".workbench-shell .wbc-side-empty p" in css
    assert "toolPackage.enabled" not in context_tab
    assert "workbenchChat.injectedContext" not in context_tab
    assert "settings.soulMd" not in context_tab
    assert "workspacePathLabel" not in context_tab

    for package_id in (
        "code_tools",
        "browser_tools",
        "desktop_tools",
        "memory_tools",
        "knowledge_tools",
        "task_tools",
        "entity_tools",
        "map_tools",
        "subagent_tools",
        "delivery_tools",
        "skill_tools",
        "remote_tools",
        "integration_tools",
    ):
        assert i18n.count(f'"toolPackageDesc.{package_id}"') == 2
        assert i18n.count(f'"toolName.{package_id}"') == 2

    assert not classic_settings.exists()
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert '"workbenchChat.inbox.title": "Session inbox"' in i18n
    assert '"workbenchChat.inbox.title": "Agent 收件箱"' in i18n
    assert '"workbenchChat.usedToolPackages": "Used tool packages"' in i18n
    assert '"workbenchChat.usedToolPackages": "已使用的工具包"' in i18n
    assert '"workbenchChat.inbox.live"' not in i18n


def test_workbench_inbox_cleanup_aborts_and_ignores_a_late_response():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    hook_source = source.split("var WBC_INBOX_CACHE_LIMIT", 1)[1].split(
        "function wbcInboxStatus", 1
    )[0]
    hook_source = "var WBC_INBOX_CACHE_LIMIT" + hook_source
    script = f"""
let cleanup = null;
let resolveInbox = null;
let capturedSignal = null;
function useWbcState(initial) {{
  let value = typeof initial === "function" ? initial() : initial;
  return [value, function (update) {{
    value = typeof update === "function" ? update(value) : update;
  }}];
}}
function useWbcEffect(effect) {{ cleanup = effect(); }}
function wbcErrorText(error) {{ return String(error); }}
global.window = {{
  CyreneUI: {{
    require: function (name) {{
      if (name !== "chat") throw new Error("unexpected service " + name);
      return {{
        Model: {{
          getInbox: function (_chatId, options) {{
            capturedSignal = options.signal;
            return new Promise(function (resolve) {{ resolveInbox = resolve; }});
          }}
        }}
      }};
    }}
  }}
}};
var WorkbenchChatModel = window.CyreneUI.require("chat").Model;
eval({json.dumps(hook_source)});
useWbcLiveInbox({{ id: "chat_race" }}, false);
cleanup();
resolveInbox({{ active: true, counts: {{}}, events: [], tools: [] }});
setTimeout(function () {{
  process.stdout.write(JSON.stringify({{
    aborted: capturedSignal.aborted,
    cacheSize: wbcInboxSnapshotCache.size
  }}));
}}, 0);
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True, timeout=2
    )

    assert json.loads(completed.stdout) == {"aborted": True, "cacheSize": 0}


def test_workbench_chat_does_not_render_previous_transcript_during_switch():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    load_effect = source.split("// Load the full transcript when the selection changes.", 1)[1].split(
        "// Viewer / content tabs belong to one conversation", 1
    )[0]

    assert load_effect.index("setActiveChat(null)") < load_effect.index("if (!activeChatId)")
    assert "new AbortController()" in load_effect
    assert "controller.abort()" in load_effect
    assert "Promise.all" not in load_effect
    assert "model.getChat(activeChatId, requestOptions)" in load_effect
    assert 'model.getSubagents(activeChatId, "", requestOptions)' in load_effect
    assert load_effect.index("setActiveChat(chat)") < load_effect.index("setSubagentData(payload)")
    assert 'String(activeChat.id || "") === String(activeChatId || "")' in source
    assert "chat={visibleChat}" in source
    assert "chat={visibleChat || selectedChatSummary}" in source
    assert "var conversationLoading = loading || chatLoading;" in source
    assert "loading={conversationLoading}" in source


def test_workbench_chat_loading_keeps_lightweight_overview_visible():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    assert "var selectedChatSummary = chats.find" in source
    assert "chatSummary={selectedChatSummary}" in source
    assert "chatDetailed={!!visibleChat}" in source
    assert "loading && !chat" in source
    assert "messages.length === 0 && !runtime && !loading && !error" in source
    assert '"workbenchChat.loadingConversation": "加载对话中..."' in i18n
    assert '"workbenchChat.error.transcriptPrefix": "对话详情：{error}"' in i18n


def test_workbench_chat_loading_is_centered_in_the_rail():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert '"wbc-chat-list" + (loading ? " is-loading" : "")' in source
    assert 'className="workbench-muted wbc-rail-loading" role="status"' in source
    assert "!loading && railItems.map" in source
    loading_styles = styles.split(".wbc-chat-list.is-loading {", 1)[1].split("}", 1)[0]
    assert "align-items: center;" in loading_styles
    assert "justify-content: center;" in loading_styles


def test_workbench_chat_plan_confirmation_can_continue_in_auto_mode():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    assert "function answerChat(chatId, questionId, answerText, options)" in source
    assert 'mode: options.mode || undefined' in source
    assert 'kind === "plan_confirmation"' in source
    assert "isPlanConfirmation && options.length > 0 ?" in source
    assert 'onAnswer(pq.id, options[0], "auto")' in source
    plan_branch = source.split("isPlanConfirmation && options.length > 0 ?", 1)[1].split(
        ") : options.length > 0 && (", 1
    )[0]
    assert "options.map" not in plan_branch
    assert "workbenchChat.approveAuto" in i18n


def test_workbench_permission_mode_is_preserved_across_secondary_entry_points():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    assert 'mode: input.mode || "default"' in source
    assert "preparedInput.mode = wbcNormalizePermissionMode(" in source
    assert "activeChat.permissionMode" in source
    assert "var answerMode = wbcNormalizePermissionMode(" in source
    assert "{ mode: answerMode }" in source
    assert "var replayMode = wbcNormalizePermissionMode(" in source
    assert "{ retry: true, forkReplay: true, mode: replayMode }" in source
    assert 'mode: "auto", command: ""' not in source


def test_workbench_surfaces_permission_reviews_and_describes_auto_accurately():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    assert 'event.type === "auto_review" || event.type === "permission_decision"' in source
    assert 'kind: "permission"' in source
    assert '"workbenchChat.mode.auto.desc": "Review permission requests automatically"' in i18n
    assert '"workbenchChat.mode.auto.desc": "自动审核权限请求"' in i18n


def test_workbench_attachment_preview_falls_back_without_overflowing():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert "failedImagePreviews" in source
    assert "onError={function () {" in source
    assert "showImagePreview" in source
    assert "function WbcMessageAttachment({ file, onOpenFile })" in source
    message_attachment = source.split(
        "function WbcMessageAttachment({ file, onOpenFile })", 1
    )[1].split("function WbcUserMessage(", 1)[0]
    assert "onError={function () { setImageFailed(true); }}" in message_attachment
    assert 'className="wbc-inline-image"' in message_attachment
    assert 'className="wbc-inline-image-preview"' in message_attachment
    assert 'className="wbc-inline-image-footer"' in message_attachment
    assert 'className="wbc-inline-image-actions"' in message_attachment
    assert 'draggable="true"' in message_attachment
    assert "wbcStartFileDrag(event, file)" in message_attachment
    assert 'draggable="false"' in message_attachment
    assert "wbcCanOpenExternally(file)" in message_attachment
    assert "WBC_ICONS.openExternal" in message_attachment
    assert 'className: "wbc-inline-image-action"' in message_attachment
    assert source.count("<WbcMessageAttachment key=") == 3
    assert 'window.CyreneUI.require("library").FileVisual' in source
    assert 'className="wbc-attach-file"' in message_attachment
    assert 'wbcT("workbenchChat.openPreview", "Open preview")' in message_attachment
    assert 'className={"wbc-msg-attachments" + (msg.content ? " after-copy" : "")}' in source
    assert 'className={"wbc-attach-card" + (showImagePreview ? " image" : " file")}' in source
    assert ".wbc-attach-file-open" in styles
    assert ".wbc-inline-image-preview img" in styles
    assert ".wbc-inline-image-actions .wbc-inline-image-action" in styles
    inline_image_rule = styles.split(".wbc-inline-image {", 1)[1].split("}", 1)[0]
    assert "width: min(280px, 100%);" in inline_image_rule
    preview_rule = styles.split(".wbc-inline-image-preview {", 1)[1].split("}", 1)[0]
    assert "aspect-ratio: 1;" in preview_rule
    preview_image_rule = styles.split(".wbc-inline-image-preview img {", 1)[1].split("}", 1)[0]
    assert "object-fit: cover;" in preview_image_rule
    assert "border-radius: 11px;" in preview_image_rule
    assert "border: 0;" in inline_image_rule
    assert "background: transparent;" in inline_image_rule
    actions_rule = styles.split(".wbc-inline-image-actions {", 1)[1].split("}", 1)[0]
    footer_rule = styles.split(".wbc-inline-image-footer {", 1)[1].split("}", 1)[0]
    assert "min-height: 34px;" in footer_rule
    assert "background: var(--wb-card-bg);" in footer_rule
    assert "box-shadow: var(--wbc-control-shadow);" in footer_rule
    assert "flex: 0 0 auto;" in actions_rule
    action_rule = styles.split(
        ".wbc-inline-image-actions .wbc-inline-image-action {", 1
    )[1].split("}", 1)[0]
    assert "display: inline-flex;" in action_rule
    assert "align-items: center;" in action_rule
    assert "justify-content: center;" in action_rule
    assert "box-sizing: border-box;" in action_rule
    assert "width: 28px !important;" in action_rule
    assert "height: 28px !important;" in action_rule
    assert "padding: 0 !important;" in action_rule
    assert ".wbc-attach-card.file" in styles
    image_rule = styles.split(".wbc-attach-card.image {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden;" in image_rule


def test_workbench_agent_images_render_inline_with_viewer_and_file_actions():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    agent_files = source.split("function WbcAgentFiles(", 1)[1].split(
        "function WbcTraceCard(", 1
    )[0]
    assert 'wbcFileViewKind(file) === "image" && file.url' in agent_files
    assert "<WbcMessageAttachment" in agent_files
    assert "wbcStartFileDrag(event, file)" in agent_files


def test_workbench_execution_card_restores_green_surface():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    trace_rule = styles.split(".wbc-trace {", 1)[1].split("}", 1)[0]
    dark_trace_rule = styles.split(
        'html[data-theme="dark"] .wbc-trace {', 1
    )[1].split("}", 1)[0]
    assert "var(--wb-green) 6%" in trace_rule
    assert "var(--wb-green) 10%" in dark_trace_rule
    assert (
        "border: 1px solid color-mix(in srgb, var(--wb-green) 26%, transparent);"
        in trace_rule
    )
    assert "box-shadow:" not in trace_rule


def test_workbench_chat_splits_live_tools_around_intermediate_messages():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    append_block = source.split("function appendIntermediate(chatId, message)", 1)[1].split(
        "function streamHandlers(chatId)", 1
    )[0]

    assert 'type === "intermediate_message"' in source
    assert "function appendIntermediate(chatId, message)" in source
    assert "message.liveDedupeKey" in append_block
    assert "messageKey === segmentKey" in append_block
    assert "existingIndex >= 0" in append_block
    assert "segments: segments.concat" in source
    assert "progress: Array.isArray(message.trace) ? message.trace" in source
    assert "wbcRuntimeSegmentMessages(runtime)" in source
    assert "wbcMergeChronologicalMessages(durableMessages" in source
    assert "<WbcAssistantMessage" in source
    assert "event.assistantMessages" in source
    assert 'event.type === "assistant_message" && event.intermediate && event.message' in source


def test_workbench_chat_retry_truncates_only_after_durable_terminal_event():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    ack_block = source.split("onAck: function (event) {", 1)[1].split(
        "onReplyStart:", 1
    )[0]
    saved_block = source.split("onSaved: function (event) {", 1)[1].split(
        "onAwaitingUser:", 1
    )[0]
    awaiting_block = source.split("onAwaitingUser: function (event) {", 1)[1].split(
        "onError:", 1
    )[0]

    assert "if (event.retry) return;" in ack_block
    assert 'fire("onRetryTruncate"' not in ack_block
    assert 'fire("onRetryTruncate"' in saved_block
    assert 'fire("onRetryTruncate"' in awaiting_block


def test_workbench_chat_error_retry_replays_failed_message_instead_of_reloading():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    runtime_error = source.split(
        'onError: function (chatId, err) {', 1
    )[1].split("onSettled:", 1)[0]
    main_props = source.split("<WbcMain", 1)[1].split("/>", 1)[0]

    assert 'setErrorKind("message");' in runtime_error
    assert 'onRetry={errorKind === "message" ? handleRetryMessage : retryLoad}' in main_props
    assert 'errorKind={errorKind}' in main_props
    assert '<WbcErrorNotice message={error} kind={errorKind} onRetry={onRetry} />' in source
    assert 'wbcT("workbenchChat.error.messageTitle", "Message processing failed")' in source
    assert 'wbcT("workbenchChat.error.messageBody"' in source


def test_workbench_chat_errors_keep_i18n_metadata_and_localize_known_codes():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    api = (root / "src" / "webui" / "frontend" / "platform" / "api.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    assert 'streamError.code = event.code || event.failure_kind || ""' in source
    assert 'streamError.detailKey = event.detail_key || event.detailKey || ""' in source
    assert 'error.detailKey = (payload && (payload.detail_key || payload.detailKey)) || ""' in api
    assert 'WORKBENCH_ERROR_I18N_KEYS' in source
    assert 'quota_exhausted: "workbenchChat.error.quotaExhausted"' in source
    assert 'quota_exhausted: "workbenchChat.error.quotaExhausted"' in api
    assert 'if (/^codex\\s+quota\\s+is\\s+exhausted\\b/i.test(raw))' in source
    for expected in (
        '"workbenchChat.error.quotaExhausted": "Codex quota is exhausted.',
        '"workbenchChat.error.quotaExhausted": "Codex 额度已耗尽，',
        '"workbenchChat.error.processRestarted": "Cyrene restarted',
        '"workbenchChat.error.processRestarted": "Cyrene 在消息完成前重启了',
    ):
        assert expected in i18n


def test_workbench_uses_the_library_as_the_only_knowledge_page():
    root = Path(__file__).resolve().parent.parent
    shell = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'window.CyreneUI.require("library").Page' in shell
    assert 'window.CyreneUI.require("knowledge").Page' not in shell
    assert "compiled/workbench-knowledge.js" not in index
    assert not (root / "src" / "webui" / "frontend" / "workbench-knowledge.jsx").exists()


def test_workbench_chat_plan_tab_uses_durable_plan_and_live_step_events():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    assert "function wbcActivePlan(chat)" in source
    assert "var active = chat && chat.activePlan;" in source
    assert 'event.type === "plan_progress" || event.type === "plan"' in source
    assert 'className={"wbc-plan-step " + status}' in source
    assert "wbcPlanStepStatusText(status)" in source


def test_workbench_chat_tool_trace_preserves_i18n_metadata():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    live_message = chat.split("function WbcLiveMessage(", 1)[1].split(
        "var WBC_DRAFT_PREFIX", 1
    )[0]
    segment_adapter = chat.split("function wbcRuntimeSegmentMessages(", 1)[1].split(
        "function wbcSubagentStatusText", 1
    )[0]
    assert "if (!runtime.text) return null;" in live_message
    assert "function wbcRuntimeTimelineMessages(runtime)" in chat
    assert "function wbcTraceDedupeKey(trace)" in chat
    assert "activityTraceKeys.has(messageTraceKey)" in chat
    assert "runtimeActivity: activity" in chat
    assert "trace: hasLiveActivities ? []" in segment_adapter
    assert "Array.isArray(segment.progress) ? segment.progress" in segment_adapter
    assert "return { tool: entry.text, preview: entry.preview };" not in live_message
    assert 'wbcT(entry.detailKey, toolKey, entry.detailParams)' in chat
    assert '"update_plan_progress"].indexOf(toolName)' in chat
    assert '"toolName.retire_project_memory": "Retire project memory"' in i18n
    assert '"toolName.retire_project_memory": "停用项目记忆"' in i18n
    assert '"workbenchChat.thinkingPhrases":' in i18n
    assert "WBC_THINKING_PHRASES" not in chat
    assert "var heartbeatI18n = useWorkbenchI18n();" in chat
    assert "}, [heartbeatLang]);" in chat


def test_workbench_live_trace_keeps_each_llm_activity_independent():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    css = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    assert 'type === "reasoning_start" && handlers.onReasoningStart' in chat
    assert 'type === "reasoning_delta" && handlers.onReasoningDelta' in chat
    assert 'type === "reasoning_done" && handlers.onReasoningDone' in chat
    assert 'reasoning: String(activity.reasoning || "") + delta' in chat
    assert "function WbcLiveActivityCard({ activity, active, hasReplyText })" in chat
    activity_card = chat.split("function WbcLiveActivityCard", 1)[1].split(
        "function WbcLiveMessage", 1
    )[0]
    live_message = chat.split("function WbcLiveMessage", 1)[1].split(
        "var WBC_DRAFT_PREFIX", 1
    )[0]
    assert "useWbcState(false)" in activity_card
    assert "useWbcState(0)" in activity_card
    assert 'setLockedHeight(cardRef.current.getBoundingClientRect().height)' in chat
    assert 'setLockedHeight(0);' in chat
    assert 'else if (showReasoning)' in activity_card
    assert "lockedTraceCount" not in activity_card
    assert 'style={lockedHeight ? { height: lockedHeight + "px" } : null}' in chat
    assert 'setShowReasoning(function (visible) { return !visible; });' in chat
    assert 'var hasReasoning = !!String(item.reasoning || "").trim();' in activity_card
    assert "function wbcPhase1ProgressDetail(entries)" in chat
    assert 'var isCodexProvider = String(item.provider || "") === "codex_oauth";' in activity_card
    assert "var hasExpandableDetail = !isCodexProvider" in activity_card
    assert 'if (!hasExpandableDetail) return;' in activity_card
    assert 'onToggle={hasExpandableDetail ? toggleReasoning : null}' in activity_card
    assert 'showReasoning={hasExpandableDetail && showReasoning}' in activity_card
    assert 'lockedHeight={hasExpandableDetail ? lockedHeight : 0}' in activity_card
    assert 'provider: String(event.provider || activity.provider || "")' in chat
    assert 'detail.scrollTop = active ? detail.scrollHeight : 0;' in activity_card
    assert "if (!msg.runtimeActivityActive && activityEntries.length === 0) return null;" in chat
    assert "wbcRuntimeSegmentMessages(runtime).concat(wbcRuntimeTimelineMessages(runtime))" in chat
    assert "if (msg.runtimeActivity || msg.activityCard)" in chat
    assert "activity={activity}" in chat
    assert "reasoning={phase1Detail}" in activity_card
    assert "useWbcState(false)" not in live_message
    assert "useWbcState(0)" not in live_message
    assert "trace: hasLiveActivities ? []" in chat
    assert 'activityRunning ? <span className="wb-spinner small" aria-hidden="true" /> : null' in chat
    assert 'className="wbc-thinking-detail-text" ref={reasoningRef}' in chat
    assert ".wbc-trace.live.wbc-trace-locked" in css
    assert ".wbc-trace.wbc-trace-locked .wbc-trace-view" in css
    thread_child_css = css.split(".wbc-thread > * {", 1)[1].split("}", 1)[0]
    assert "flex-shrink: 0;" in thread_child_css
    trace_view_css = css.split(".wbc-trace-view {", 1)[1].split("}", 1)[0]
    assert "height: 100%;" not in trace_view_css
    detail_css = css.split(".wbc-thinking-detail {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden;" in detail_css
    detail_text_css = css.split(".wbc-thinking-detail-text {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in detail_text_css
    assert "margin-right: -8px;" in detail_text_css
    assert "padding-right: 8px;" in detail_text_css
    assert "查看思考详情" in i18n


def test_codex_reasoning_effort_updates_the_primary_candidate_without_stale_state():
    root = Path(__file__).resolve().parent.parent
    settings = (
        root / "src" / "webui" / "frontend" / "settings-overlay.jsx"
    ).read_text(encoding="utf-8")

    assert "setCodexCandidate(normalizeModel({" in settings
    assert "selectedEffort != null ? selectedEffort : codexEffort" in settings
    assert "setCodexPrimaryCandidate(codexModel, value);" in settings
    assert "return [candidate].concat(rest);" not in settings


def test_workbench_deepseek_reasoning_effort_matches_provider_capabilities():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert 'if (!efforts.length && wbcIsDeepSeekModel(model)) efforts = ["high", "max"];' in chat
    assert 'else if (["xhigh", "max"].indexOf(effort) >= 0) effort = "max";' in chat
    assert "setReasoningEffort(wbcReasoningEffortForModel(" in chat
    model_menu_css = styles.split(".wbc-model-menu {", 1)[1].split("}", 1)[0]
    assert "width: min(260px, calc(100vw - 32px));" in model_menu_css
    model_row_css = styles.split(
        ".wbc-popmenu > .wbc-model-menu-row {", 1
    )[1].split("}", 1)[0]
    assert "grid-template-columns: max-content minmax(0, 1fr) 18px;" in model_row_css
    model_name_css = styles.split(".wbc-model-button-name {", 1)[1].split("}", 1)[0]
    assert "font-size: 11px;" in model_name_css
    model_effort_css = styles.split(".wbc-model-button-effort {", 1)[1].split("}", 1)[0]
    assert "font-size: 11px;" in model_effort_css
    assert 'className="wbc-model-menu-value wbc-model-menu-model-name"' in chat
    menu_model_name_css = styles.split(
        ".wbc-model-menu-model-name {", 1
    )[1].split("}", 1)[0]
    assert "font-size: 11px;" in menu_model_name_css
    assert "white-space: nowrap;" in menu_model_name_css
    assert "overflow-wrap:" not in menu_model_name_css
    assert "text-overflow: clip;" in menu_model_name_css


def test_workbench_chat_context_and_browser_trace_have_dynamic_i18n_labels():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    # Dynamic context block and tool IDs must resolve through the same
    # translation table as the surrounding labels instead of leaking raw IDs.
    assert 'var key = "workbenchChat.ctxBlock." + id;' in chat
    assert 'wbcT("toolName." + toolKey, toolKey)' in chat
    assert '"workbenchChat.ctxBlock.skills.learned": "Learned skills"' in i18n
    assert '"workbenchChat.ctxBlock.skills.learned": "已学习技能"' in i18n
    assert '"toolName.browser_user_events": "User browser operations"' in i18n
    assert '"toolName.browser_user_events": "用户浏览器操作"' in i18n
    assert '"toolName.browser_upload_files": "Upload files"' in i18n
    assert '"toolName.browser_upload_files": "上传文件"' in i18n


def test_progressive_capability_ids_resolve_to_existing_tool_name_i18n():
    from cyrene.tooling.native_definitions import get_native_tool_defs
    from cyrene.tooling.packs import CAPABILITY_BINDINGS

    root = Path(__file__).resolve().parent.parent
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    # Runtime traces intentionally publish model-facing IDs such as
    # browser.navigate. Every native progressive capability must map back to
    # the existing localized concrete-tool label instead of leaking that ID.
    for bindings in CAPABILITY_BINDINGS.values():
        for capability_id, concrete_name in bindings:
            assert f'"{capability_id}": "{concrete_name}"' in i18n
            assert i18n.count(f'"toolName.{concrete_name}"') == 2
    for tool_def in get_native_tool_defs():
        tool_name = tool_def["function"]["name"]
        assert i18n.count(f'"toolName.{tool_name}"') == 2

    assert 'var alias = WORKBENCH_TOOL_NAME_ALIASES[toolName];' in i18n
    assert 'resolvedKey = "toolName." + alias;' in i18n
    assert '"browser.navigate": "browser_navigate"' in i18n
    assert '"toolName.browser_navigate": "Navigate"' in i18n
    assert '"toolName.browser_navigate": "浏览器导航"' in i18n


def test_tool_i18n_fallbacks_do_not_leak_internal_keys_after_classic_removal():
    result = _run_workbench_trace_i18n_js(
        """
({
  unknownTool: window.WorkbenchI18n.t("toolName.custom_mcp_tool", "custom_mcp_tool"),
  unknownParam: window.WorkbenchI18n.t("memory.learning.toolParam.custom_arg", "custom_arg"),
  planProgress: window.WorkbenchI18n.toolName("update_plan_progress", "zh"),
  browserSubmit: window.WorkbenchI18n.toolName("browser.user.submit", "zh"),
  browserNavigateEn: window.WorkbenchI18n.toolName("browser.navigate", "en"),
  showSidebar: window.WorkbenchI18n.t("workbenchChat.showSidebar"),
  hideSidebar: window.WorkbenchI18n.t("workbenchChat.hideSidebar"),
  download: window.WorkbenchI18n.t("workbenchChat.download")
})
"""
    )

    assert result == {
        "unknownTool": "custom_mcp_tool",
        "unknownParam": "custom_arg",
        "planProgress": "更新计划进度",
        "browserSubmit": "用户提交表单",
        "browserNavigateEn": "Navigate",
        "showSidebar": "显示侧边栏",
        "hideSidebar": "隐藏侧边栏",
        "download": "下载",
    }

    root = Path(__file__).resolve().parent.parent
    classic_root = root / "src" / "webui" / "static" / "app"
    assert not (classic_root / "chat.jsx").exists()
    assert not (classic_root / "chat-surface.jsx").exists()
    assert not (classic_root / "evolution.jsx").exists()


def test_workbench_tool_trace_preview_localizes_protocol_values_only():
    result = _run_workbench_trace_i18n_js(
        """
[
  wbcToolPreviewText("discover"),
  wbcToolPreviewText("invoke, memory.project.search"),
  wbcToolPreviewText("待办 任务 task pending")
]
"""
    )

    assert result == [
        "发现能力",
        "调用能力, 搜索项目记忆",
        "待办 任务 task pending",
    ]


def test_workbench_tool_trace_preview_serializes_nested_arguments():
    result = _run_workbench_trace_i18n_js(
        """
(() => {
  const preview = wbcToolArgsPreview({
    operation: "invoke",
    capability_id: "browser.click_text",
    arguments: { text: "继续", exact: true }
  });
  return {
    preview,
    localized: wbcToolPreviewText(preview),
    leakedObjectTag: preview.includes("[object Object]")
  };
})()
"""
    )

    assert result == {
        "preview": 'invoke, browser.click_text, {"text":"继续","exact":true}',
        "localized": '调用能力, 点击文本, {"text":"继续","exact":true}',
        "leakedObjectTag": False,
    }


def test_workbench_phase_events_publish_translation_keys():
    root = Path(__file__).resolve().parent.parent
    planning = (root / "src" / "cyrene" / "agent" / "planning.py").read_text(encoding="utf-8")
    guidance = (root / "src" / "cyrene" / "agent" / "guidance.py").read_text(encoding="utf-8")
    reflection = (root / "src" / "cyrene" / "agent" / "deep_reflection.py").read_text(encoding="utf-8")
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    assert '"detail_key": "phase.planning"' in planning
    assert '"detail_key": "phase.applyingGuidanceToSubagents"' in guidance
    assert '"detail_params": {"count": len(snapshot)}' in guidance
    assert '"detail_key": "phase.guidedRoundContinuation"' in guidance
    assert '"detail_key": "phase.guidanceExecution"' in guidance
    assert '"detail_key": "phase.deepReflection"' in reflection
    assert '"phase.useToolsAttachments": "Phase 1 decided to use tools. Task: Analyze uploaded attachments"' in i18n
    assert '"phase.useToolsAttachments": "阶段一决定使用工具。任务：分析上传的附件"' in i18n


def test_workbench_chat_last_user_message_has_retry_action():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    main = source.split("function WbcMain(", 1)[1].split(
        "function WbcQuestionPrompt(", 1
    )[0]
    user_message = source.split("function WbcUserMessage(", 1)[1].split(
        "function WbcAgentFiles(", 1
    )[0]

    assert 'var lastUserId = "";' in main
    assert 'String(msg.id || "") === lastUserId' in main
    assert "onRetryMessage={canRetryUser ? onRetryMessage : null}" in main
    assert "function WbcUserMessage({ msg, onOpenFile, onEditMessage, canEdit, onRetryMessage })" in source
    assert "onClick={onRetryMessage}" in user_message
    assert "WBC_ICONS.retry" in user_message
    assert 'wbcT("workbenchChat.retryUserMessage", "Retry message")' in user_message
    assert '"workbenchChat.retryUserMessage": "重试消息"' in i18n


def test_workbench_chat_uses_explicit_run_reconnect_without_resubmitting_message():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    assert 'function reconnectRun(chatId, handlers, signal)' in source
    assert '"/run-stream"' in source
    assert 'function reconnect(chatId, model)' in source
    assert 'runtimeEngine.reconnect(activeChat.id, model)' in source
    assert 'activeChat.status === "running"' in source


def test_workbench_copy_uses_electron_clipboard_bridge():
    root = Path(__file__).resolve().parent.parent
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert "clipboard, contextBridge, ipcRenderer" in preload
    assert "writeClipboardText: (text) =>" in preload
    assert "clipboard.writeText(" in preload
    assert 'typeof window.cyrene.writeClipboardText === "function"' in chat
    assert "window.cyrene.writeClipboardText(text);" in chat
    assert "await navigator.clipboard.writeText(text);" in chat
    assert 'console.error("Failed to copy workbench message:", e);' in chat


def test_code_blocks_use_declared_language_and_resilient_clipboard_actions():
    root = Path(__file__).resolve().parent.parent
    highlight = (
        root
        / "src"
        / "webui"
        / "frontend"
        / "shared"
        / "markdown"
        / "highlight.jsx"
    ).read_text(encoding="utf-8")
    actions = (
        root / "src" / "webui" / "frontend" / "shared" / "markdown" / "actions.jsx"
    ).read_text(encoding="utf-8")
    styles = (
        root / "src" / "webui" / "frontend" / "shared" / "markdown" / "highlight.css"
    ).read_text(encoding="utf-8")

    assert 'language = "text";' in highlight
    assert "hljs.highlightAuto(code)" not in highlight
    assert 'typeof window.cyrene.writeClipboardText === "function"' in actions
    assert 'navigator.clipboard && typeof navigator.clipboard.writeText === "function"' in actions
    assert 'document.execCommand("copy")' in actions
    assert "padding-top: 52px;" in styles
    assert "top: 0;" in styles
    assert "bottom: 0;" not in styles.split(".code-block-actions", 1)[1].split("}", 1)[0]


def test_workbench_side_viewer_keeps_html_sandboxed_and_uses_pdfjs_text_layer():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")

    assert 'split(";", 1)[0].trim().toLowerCase()' in source
    assert 'ext === "ppt"' not in source
    assert 'ext === "doc"' not in source
    assert 'wbcFileViewKind(file) !== "html"' in source
    assert 'function wbcHtmlPreviewDocument(source, sourceUrl)' in source
    assert '<base href="' in source
    assert 'sandbox="allow-scripts"' in source
    assert 'srcDoc={htmlPreview}' in source
    assert 'pdf.installCopyFix(container, viewer)' in source
    assert 'pdf.installSelectionSanitizer(container, viewer, eventBus)' in source
    assert 'selectionSanitizer.abort();' in source
    assert '"/api/workbench/library/read?workspace="' in source
    assert '<WbcViewerList files={viewerItems} selectedFile={viewerFile} onSelect={onSelectViewer} />' in source
    assert 'selectResourceSplit("viewer", wbcArtifactFileKey(file))' in source
    assert 'onLoad={confirmViewed}' in source
    assert 'return <WbcPdfJsViewer file={file} url={url} onViewed={confirmViewed} />;' in source
    assert '.wbc-viewer .pdfViewer .textLayer' not in styles
    assert "width: 100%;" in styles
    assert "height: 100%;" in styles
    assert r"/\.html?$/i.test(target.pathname)" in main


def test_workbench_acceptance_button_calls_agent_endpoint():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "webui" / "frontend" / "workbench-model.jsx").read_text(encoding="utf-8")

    assert 'window.CyreneUI.require("model").generateAcceptance(session.id)' in source
    assert '"/acceptance/generate"' in model


def test_workbench_artifact_rows_download_registered_files():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "webui" / "frontend" / "workbench-model.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    routes = (root / "src" / "route" / "workbench" / "task_sessions.py").read_text(encoding="utf-8")

    assert "WorkbenchModel.ensureArtifacts(session)" in source
    assert 'className="workbench-artifact-row wb-artifact-download"' in source
    assert 'download={artifact.name || true}' in source
    assert '"/artifacts/" + encodeURIComponent(artifact.id) + "/download"' in source
    assert "artifact.type !== \"file_change\"" in model
    assert 'name: "task-summary.md"' not in model
    assert ".wb-artifact-download:hover" in styles
    assert '@router.get("/api/task-sessions/{session_id}/artifacts/{artifact_id}/download")' in routes
    assert "_workbench_artifact_download_target(project, session, artifact_id)" in routes


def test_workbench_right_tabs_do_not_shrink_for_long_run_logs():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    tabs_rule = styles.split(".workbench-right-tabs {", 1)[1].split("}", 1)[0]
    body_rule = styles.split(".workbench-right-body {", 1)[1].split("}", 1)[0]
    compact_tabs = styles.split("@container (max-width: 320px) {", 1)[1].split("}", 2)

    assert "flex: 0 0 48px;" in tabs_rule
    assert "flex: 1 1 auto;" in body_rule
    assert "container-type: inline-size;" in styles
    assert "gap: 2px;" in compact_tabs[0]
    assert "padding-inline: 8px;" in compact_tabs[0]
    assert "padding-inline: 2px;" in compact_tabs[1]
    assert "font-size: calc(12px * var(--wb-ui-font-scale, 1));" in compact_tabs[1]
    assert "workbench.css?v=0.7.0b12" in index


def test_workbench_collapsed_rail_keeps_labels_horizontal_during_expansion():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    nav_rule = styles.split("\n.workbench-nav-button {", 1)[1].split("}", 1)[0]
    nav_label_rule = styles.split(".workbench-nav-button > span:last-child {", 1)[1].split("}", 1)[0]
    global_nav_rule = styles.split("\n.workbench-global-nav {", 1)[1].split("}", 1)[0]
    account_rule = styles.split("\n.workbench-account {", 1)[1].split("}", 1)[0]
    account_meta_rule = styles.rsplit(".workbench-account-meta {", 1)[1].split("}", 1)[0]

    assert ".workbench-project-rail:focus-within" in styles
    assert ":not(:hover):not(:focus-within)" in styles
    assert "height: 39px;" in nav_rule
    assert "grid-auto-rows: 39px;" in global_nav_rule
    assert "white-space: nowrap;" in nav_label_rule
    assert "height: 63px;" in account_rule
    assert "grid-template-rows: 36px;" in account_rule
    assert "height: 36px;" in account_meta_rule
    assert "workbench.css?v=0.7.0b12" in index


def test_workbench_collapsed_rail_icons_stay_left_anchored_while_closing():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    collapsed_prefix = (
        ".workbench-grid.rail-collapsed "
        ".workbench-project-rail:not(:hover):not(:focus-within) "
    )
    project_list_rule = styles.split(collapsed_prefix + ".workbench-project-list {", 1)[1].split("}", 1)[0]
    project_card_rule = styles.split(collapsed_prefix + ".workbench-project-card {", 1)[1].split("}", 1)[0]
    nav_rule = styles.split(collapsed_prefix + ".workbench-nav-button {", 1)[1].split("}", 1)[0]
    account_rule = styles.split(collapsed_prefix + ".workbench-account {", 1)[1].split("}", 1)[0]
    head_actions_rule = styles.split(collapsed_prefix + ".workbench-rail-head-actions {", 1)[1].split("}", 1)[0]

    # These offsets are relative to the rail's left edge, so entering the
    # non-hover state cannot center icons against the still-animating width.
    assert "align-items: flex-start;" in project_list_rule
    assert "margin-left: 10px;" in project_card_rule
    assert "margin: 0 0 0 10px;" in nav_rule
    assert "justify-content: flex-start;" in account_rule
    assert "padding: 13px 0 13px 14px;" in account_rule
    assert "margin-left: 0;" in head_actions_rule


def test_workbench_narrow_window_forces_project_rail_into_stable_icon_strip():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    title_rule = styles.split("\n.wb-rail-title {", 1)[1].split("}", 1)[0]
    actions_rule = styles.split("\n.workbench-rail-head-actions {", 1)[1].split("}", 1)[0]
    assert "position: absolute;" in title_rule
    assert "left: 39px;" in title_rule
    assert "transform: translate(-50%, -50%);" in title_rule
    assert "margin-left: auto;" in actions_rule
    compact = styles.split("@media (max-width: 1040px)", 1)[1].split("/* ── Light-mode", 1)[0]
    assert "--wb-rail-w: 64px;" in compact
    assert "--wb-rail-w-open: 250px;" in compact
    assert ".workbench-add-btn > span:last-child" in compact
    assert ".workbench-project-menu-btn" in compact
    assert "width: 44px;" in compact
    assert "overflow-x: hidden;" in compact
    assert ".workbench-global-nav" in compact
    assert "display: grid;" in compact
    assert ".workbench-project-rail:hover" in compact
    assert "width: var(--wb-rail-w-open);" in compact
    assert "box-shadow: 18px 0 50px" in compact
    hover_head = compact.split(".workbench-project-rail:hover .workbench-rail-head", 1)[1].split("}", 1)[0]
    assert "justify-content: space-between;" in hover_head
    assert "padding: 0 12px;" in hover_head
    compact_actions = compact.split(".workbench-project-rail:not(:hover):not(:focus-within) .workbench-rail-head-actions", 1)[1].split("}", 1)[0]
    assert "margin-left: 0;" in compact_actions


def test_workbench_wechat_channel_uses_qr_login_instead_of_token_input():
    root = Path(__file__).resolve().parent.parent
    settings = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")
    translations = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "function WeChatConnectionPanel" in settings
    assert 'fetch("/api/wechat/status")' in settings
    assert 'fetch("/api/wechat/qr-login"' in settings
    assert 'fetch("/api/wechat/poll-login"' in settings
    assert 'fetch("/api/wechat/start"' in settings
    assert 'fetch("/api/wechat/stop"' in settings
    assert "result.qrcode_image || result.qrcode_img" in settings
    assert "WECHAT_BOT_TOKEN" not in settings
    assert '"settings.wechatScanConnect": "扫描二维码连接"' in translations
    assert ".wb-wechat-qr-overlay" in styles
    assert "settings-overlay.js?v=0.7.0b12" in index


def test_linux_desktop_uses_native_frame_and_directory_picker():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    create = (root / "src" / "webui" / "frontend" / "workbench-create.jsx").read_text(encoding="utf-8")
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert "const isLinux = process.platform === 'linux';" in main
    assert "const useInsetTitleBar = isMac;" in main
    assert "ipcMain.handle('dialog:pick-directory'" in main
    assert "properties: ['openDirectory', 'createDirectory']" in main
    assert "if (process.platform !== 'linux') return Promise.resolve(null);" in preload
    assert "ipcRenderer.invoke('dialog:pick-directory')" in preload
    assert 'window.cyrene.platform === "linux"' in create
    assert "await window.cyrene.pickDirectory()" in create
    assert 'window.cyrene.platform === "linux"' in chat
    assert "window.cyrene.pickDirectory().then(function (data)" in chat


def test_electron_browser_panel_uses_native_browser_bridge():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    view = (root / "src" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")

    assert "WebContentsView" in main
    assert "class BrowserTabManager" in main
    assert "CYRENE_ELECTRON_RPC_PORT" in main
    assert "ipcMain.handle('browser:set-bounds'" in main
    assert "setAudioMuted" in main
    assert "isCurrentlyAudible" in main
    assert "browser_tab_new" in (root / "src" / "cyrene" / "tooling" / "catalog.py").read_text(encoding="utf-8")
    assert "browser: {" in preload
    assert "ipcRenderer.invoke('browser:navigate'" in preload
    assert "ipcRenderer.invoke('browser:set-context'" in preload
    assert "window.cyrene && window.cyrene.browser" in view
    assert "ElectronBrowserViewportPanel" in view
    assert "bridge.setBounds" in view
    assert "bridge.setContext" in view
    assert "bridge.setMuted" in view
    assert "browser_user_events" in (root / "src" / "cyrene" / "tooling" / "catalog.py").read_text(encoding="utf-8")


def test_native_browser_yields_to_model_confirm_and_topbar_overlays():
    root = Path(__file__).resolve().parent.parent
    workbench = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    feedback = (
        root / "src" / "webui" / "frontend" / "shared" / "feedback" / "service.jsx"
    ).read_text(encoding="utf-8")

    assert 'window.CyreneUI.register("browser-overlays"' in workbench
    assert "if (!sessionMenu && !resourceMenu) return undefined;" in workbench
    assert "if (!modelOpen) return undefined;" in workbench
    assert 'window.CyreneUI.require("browser-overlays")' in chat
    assert 'platform.require("browser-overlays")' in feedback
    assert "overlays.adjust(1);" in feedback
    assert "return function () { overlays.adjust(-1); };" in feedback


def test_electron_browser_type_uses_react_compatible_native_setter():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    browser_input = (root / "electron" / "browser-input.js").read_text(encoding="utf-8")
    package = (root / "electron" / "package.json").read_text(encoding="utf-8")
    playwright_browser = (root / "src" / "cyrene" / "browser.py").read_text(encoding="utf-8")

    assert "buildBrowserTypeTargetScript" in main
    assert "runPageOperation('set-native')" in main
    assert "prototypeSetter.call(element, desired);" in browser_input
    assert "element.value = desired" not in browser_input
    assert "await waitForControlledRender();" in browser_input
    assert "runPageOperation('prepare-trusted')" in main
    assert "wc.focus();" in main
    assert "await wc.insertText(desiredText);" in main
    assert "runPageOperation('verify')" in main
    assert "wc.sendInputEvent({ type: 'keyDown', keyCode: 'Enter' });" in main
    assert "browser-input.js" in package
    assert "clean(el.value)" in main
    assert "clean(el.value)" in playwright_browser
    assert "inputType === 'password' ? '' : clean(el.value)" in main
    assert "inputType === 'password' ? '' : clean(el.value)" in playwright_browser


def test_electron_browser_tabs_are_per_session_while_login_state_is_shared():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    browser = (root / "src" / "cyrene" / "browser.py").read_text(encoding="utf-8")
    chat_routes = (root / "src" / "route" / "workbench" / "chat.py").read_text(encoding="utf-8")
    view = (root / "src" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert "const browserTabManagers = new Map();" in main
    assert "new BrowserTabManager(normalized)" in main
    assert "this.partition = BROWSER_PARTITION;" in main
    assert "partition: this.partition" in main
    assert "sessionId: this.sessionId" in main
    assert "this.sessionId !== activeBrowserSessionId" in main
    assert "closeBrowserSession" in main
    assert "manager.closeAll()" in main
    assert "payload.sessionId" in main
    assert '"sessionId": session_id' in browser
    assert "getState: (sessionId)" in preload
    assert "bridge.getState(electronSessionId)" in view
    assert 'String(next.sessionId || "") === electronSessionId' in view
    assert "bridge.getState(chatId)" in chat
    assert "Array.isArray(next.tabs)" in view
    assert "await close_electron_browser_session(chat_id)" in chat_routes


def test_electron_browser_user_events_are_recorded_for_learning():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    routes = (root / "src" / "route" / "agent" / "browser.py").read_text(encoding="utf-8")
    view = (root / "src" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")

    assert "BROWSER_USER_EVENT_CONSOLE_PREFIX" in main
    assert "installUserEventCapture" in main
    assert "handleCapturedUserEvent" in main
    assert "postBackendJson('/api/browser/user-event'" in main
    assert "recordUserEvent('navigate'" in main
    assert "browser:set-context" in main
    assert '"/api/browser/user-event"' in routes
    assert "record_browser_user_event" in routes
    # Browser telemetry is persisted here; completed agent turns own the
    # learning barrier so an event cannot race an incomplete tool chain.
    assert "process_unprocessed_turns" not in routes
    assert "bridge.setContext({ sessionId: electronSessionId, roundId: rid })" in view


def test_electron_browser_panel_does_not_restore_closed_tabs_from_stale_state():
    root = Path(__file__).resolve().parent.parent
    view = (root / "src" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")
    panel = view.split("function ElectronBrowserViewportPanel", 1)[1].split("function ScreencastBrowserViewportPanel", 1)[0]

    assert 'const nextUrl = (active && active.url) || "";' in panel
    assert "browserState && browserState.url" not in panel
    assert "browserState && browserState.active" not in panel
    assert "if (!tabs.length" not in panel


def test_workbench_chat_directory_picker_falls_back_on_macos_and_lists_default_workspace():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    assert 'window.cyrene.platform === "linux"' in chat
    assert 'fetch("/api/context/pick-directory", { method: "POST" })' in chat
    assert "defaultWorkspacePath={projectWorkspacePath || wsDir}" in chat
    assert "if (defaultWorkspacePath) workspaceOptions.push" in chat
    assert 'wbcT("workbenchChat.defaultWorkspace", "Default workspace")' in chat
    assert '"workbenchChat.defaultWorkspace": "Default workspace"' in i18n
    assert '"workbenchChat.defaultWorkspace": "默认 workspace"' in i18n


def test_workbench_chat_workspace_chip_follows_project_until_user_overrides_it():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    # Both POSIX and Windows workspace paths render only their final directory
    # name in the chip (for example, the default Windows path renders
    # "workspace" instead of the full C:\Users\...\workspace path).
    assert 'function wbcWorkspaceDisplayName(path)' in chat
    assert 'replace(/[\\\\/]+$/, "")' in chat
    assert 'normalized.split(/[\\\\/]/).filter(Boolean).pop()' in chat
    assert "name: wbcWorkspaceDisplayName(wsDir)" in chat
    assert "var name = wbcWorkspaceDisplayName(p);" in chat

    # The workspace override helpers take an optional draft namespace (default
    # "" for the main chat; the quick-chat window passes one) — the call sites
    # thread it through.
    assert "return wbcLoadWorkspaceOverride(workspaceContextKey, draftNs);" in chat
    assert 'var WBC_WORKSPACE_PREFIX = "cyrene-wbc-workspace-";' in chat
    assert "function wbcWorkspaceContextKey(chatId, projectId)" in chat
    assert "var workspaceContextKey = wbcWorkspaceContextKey(chatId, projectId);" in chat
    assert "wbcSaveWorkspaceOverride(prevKey, currentOverride, draftNs);" in chat
    assert "var nextOverride = wbcLoadWorkspaceOverride(workspaceContextKey, draftNs);" in chat
    assert 'window.dispatchEvent(new CustomEvent("cyrene:wbc-chat-created"' in chat
    assert 'window.addEventListener("cyrene:wbc-chat-created", onChatCreated);' in chat
    assert "wbcSaveWorkspaceOverride(nextKey, workspaceOverrideRef.current, draftNs);" in chat
    assert 'var projectWorkspacePath = (project && project.workspacePath) || "";' in chat
    assert (
        "var wsDir = workspaceOverride || projectWorkspacePath || "
        "(contextState && contextState.workspace_dir) || \"\";"
    ) in chat
    assert "}, [projectId, projectWorkspacePath]);" in chat
    assert (
        'setWorkspaceOverride(selectedPath && selectedPath !== '
        'projectWorkspacePath ? selectedPath : "");'
    ) in chat


def test_workbench_context_picker_contains_long_workspace_paths():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    picker_rule = styles.rsplit(".wbc-ctx-picker {", 1)[1].split("}", 1)[0]
    chip_row_rule = styles.split(".wbc-context-chips {", 1)[1].split("}", 1)[0]
    picker_anchor_rule = styles.split(
        ".wbc-context-chips > .wbc-pop-anchor {", 1
    )[1].split("}", 1)[0]
    text_rule = styles.rsplit(
        ".wbc-ctx-picker .wbc-popmenu-label,\n.wbc-ctx-picker .wbc-popmenu-desc {",
        1,
    )[1].split("}", 1)[0]

    assert "position: relative;" in chip_row_rule
    assert "position: static;" in picker_anchor_rule
    assert "width: min(300px, 100%);" in picker_rule
    assert "max-width: 100%;" in picker_rule
    assert "min-width: min(220px, 100%);" in styles
    assert "overflow-x: hidden;" in picker_rule
    assert "min-width: 0;" in styles
    assert "text-overflow: ellipsis;" in text_rule
    assert "white-space: nowrap;" in text_rule
    assert 'className="wbc-popmenu-desc" title={p}' in chat
    assert "workbench-chat.js?v=0.7.0b12" in index


def test_workbench_follow_up_uses_context_endpoint_without_native_prompt():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "webui" / "frontend" / "workbench-model.jsx").read_text(encoding="utf-8")
    routes = (root / "src" / "route" / "workbench" / "projects.py").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    assert 'window.prompt("后续任务标题"' not in source
    assert "model.createFollowUp(sid, options)" in source
    assert '"/follow-up"' in model
    assert '"/api/task-sessions/{session_id}/follow-up"' in routes
    assert 'session["parentSessionId"] = session_id' in routes
    assert "followUpContext" in routes
    assert "workbench-model.js?v=0.7.0b12" in index
    assert "workbench.js?v=0.7.0b12" in index


def test_workbench_regenerate_plan_failure_preserves_current_plan():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    regenerate_block = source.split("regeneratePlan: function ()", 1)[1].split("approvePlan: function ()", 1)[0]

    assert "plan: Array.isArray(session.plan) ? session.plan : []" in regenerate_block
    assert "acceptanceCriteria: Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : []" in regenerate_block
    assert "model.buildPlanSteps" not in regenerate_block


def test_workbench_plan_conflict_does_not_apply_client_fallback():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    api = (root / "src" / "webui" / "frontend" / "platform" / "api.jsx").read_text(encoding="utf-8")

    assert 'err.code === "stale_plan_revision"' in source
    assert "rethrowPlanConflict(err);" in source
    assert "error.code = (payload && payload.code)" in api


def test_workbench_api_timeout_covers_response_body_consumption():
    root = Path(__file__).resolve().parent.parent
    api = (root / "src" / "webui" / "frontend" / "platform" / "api.jsx").read_text(encoding="utf-8")

    assert "Keep the deadline active until" in api
    assert "resp.__workbenchRequestDone = done" in api
    assert "resp.__workbenchNormalizeAbort = normalizeAbort" in api
    assert 'err.name === "AbortError" || err.isTimeout' in api


def test_workbench_api_json_times_out_when_body_stalls_after_headers():
    root = Path(__file__).resolve().parent.parent
    api_path = root / "src" / "webui" / "frontend" / "platform" / "api.jsx"
    script = f"""
const fs = require("fs");
global.window = {{
  CyreneUI: {{
    register: function (_name, service) {{ return service; }},
    require: function (name) {{
      if (name === "i18n") {{
        return {{ t: function (_key, _params, fallback) {{ return fallback; }} }};
      }}
      throw new Error("unexpected service " + name);
    }}
  }}
}};
global.fetch = function (_url, init) {{
  return Promise.resolve({{
    ok: true,
    status: 200,
    body: {{}},
    json: function () {{
      return new Promise(function (_resolve, reject) {{
        init.signal.addEventListener("abort", function () {{
          const err = new Error("aborted");
          err.name = "AbortError";
          reject(err);
        }});
      }});
    }}
  }});
}};
eval(fs.readFileSync({json.dumps(str(api_path))}, "utf8"));
window.CyreneUI.api.json("/slow-body", {{ timeout: 10, toast: false }}).then(
  function () {{ process.stdout.write("unexpected success"); process.exit(1); }},
  function (err) {{ process.stdout.write(JSON.stringify({{ name: err.name, isTimeout: err.isTimeout }})); }}
);
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True, timeout=2
    )

    assert json.loads(completed.stdout) == {"name": "TimeoutError", "isTimeout": True}


def test_workbench_init_plan_failure_shows_details_and_restart():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-create.jsx").read_text(encoding="utf-8")

    assert "function InitPlanError" in source
    assert 'className="wb-init-plan-error"' in source
    assert "error.attempts" in source
    assert "onRestart={complete}" in source
    assert 'T("init.restart")' in source
    assert "!planReady && !planError" in source

    result = _run_workbench_i18n_js(
        """
[
  window.WorkbenchI18n.t("init.planError.title"),
  window.WorkbenchI18n.t("init.planError.summary", { count: 5 }),
  window.WorkbenchI18n.t("init.restart")
]
"""
    )
    assert result == [
        "计划生成失败",
        "连续尝试 5 次后仍未生成计划，系统没有创建兜底计划。",
        "重新开始",
    ]


def test_workbench_init_answer_updates_do_not_set_parent_state_inside_local_updater():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-create.jsx").read_text(encoding="utf-8")
    answer_block = source.split("function setAnswer(qid, value)", 1)[1].split("function regenerate()", 1)[0]

    assert "answersRef.current = nextAnswers;" in answer_block
    assert "setAnswers(nextAnswers);" in answer_block
    assert "persist(nextAnswers);" in answer_block
    assert "setAnswers(function" not in answer_block


def test_workbench_model_settings_preserve_form_on_failed_response():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")
    save_block = source.split("function saveModels()", 1)[1].split("function saveTools()", 1)[0]

    assert "async function readSettingsResponse(response)" in source
    assert "if (!response.ok)" in source
    assert "fetch(\"/api/settings/models\").then(readSettingsResponse)" in source
    assert "}).then(readSettingsResponse).then(function (p)" in save_block
    assert "p.custom_models || norm" in save_block
    assert "p.vision_models || p.vision_candidates || vNorm" in save_block
    assert "settings-overlay.js?v=0.7.0b12" in index


def test_workbench_chat_subagent_page_is_independent_and_localized():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    classic_chat = root / "src" / "webui" / "static" / "app" / "chat.jsx"

    assert 'id: "subagents"' in source
    assert "function WbcSubagentsTab" in source
    assert '"/subagents" + query' in source
    assert "AgentGroupChat" not in source
    assert ".wbc-subagent-page" in styles
    assert ".agent-chat-" not in styles.split("/* Workbench-only subagent page.", 1)[1].split("/* 计划 tab", 1)[0]
    assert not classic_chat.exists()

    result = _run_workbench_i18n_js(
        """
[
  window.WorkbenchI18n.t("workbenchChat.subagents"),
  window.WorkbenchI18n.t("workbenchChat.subagent.title"),
  window.WorkbenchI18n.t("workbenchChat.subagent.status.running"),
  window.WorkbenchI18n.t("workbenchChat.subagent.result")
]
"""
    )
    assert result == ["子代理", "子代理执行", "执行中", "执行结果"]


def test_workbench_chat_quick_actions_include_manual_context_compaction():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert 'function compactChat(chatId)' in source
    assert '"/compact"' in source
    assert 'wbcT(compactBusy ? "workbenchChat.compactBusy" : "workbenchChat.compact"' in source
    assert "activeRunning || compactBusy" not in source
    assert "disabled={compactBusy} onClick={run(onCompact)}" in source
    assert 'payload.reason === "running"' in source
    assert 'payload.reason === "awaiting_user"' in source
    assert 'payload.reason === "no_tool_activity"' in source
    assert 'payload.reason === "distilling"' in source

    result = _run_workbench_i18n_js(
        """
[
  window.WorkbenchI18n.t("workbenchChat.compact"),
  window.WorkbenchI18n.t("workbenchChat.compactBusy"),
  window.WorkbenchI18n.t("workbenchChat.compactRunning"),
  window.WorkbenchI18n.t("workbenchChat.compactAwaitingUser"),
  window.WorkbenchI18n.t("workbenchChat.compactNoTools"),
  window.WorkbenchI18n.t("workbenchChat.compactDistilling")
]
"""
    )
    assert result == [
        "压缩对话",
        "正在压缩…",
        "Agent 正在工作，请在任务完成后再压缩。",
        "请先回答 Agent 的问题，再压缩对话。",
        "当前对话没有工具调用，无需主动压缩。",
        "后台正在蒸馏上下文，请稍后再试。",
    ]


def test_workbench_chat_exposes_browser_live_view_and_takeover():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert 'event.type === "browser_frame" || event.type === "browser_takeover_request"' in source
    browser_switch_block = source.split('event.type === "browser_frame" || event.type === "browser_takeover_request"', 1)[1].split('// Live tool/phase/subagent progress', 1)[0]
    assert 'setSideTab("browser")' not in browser_switch_block
    assert "setBrowserWindowModeByChat" in browser_switch_block
    assert "runtimeEngine.isRunning" not in browser_switch_block
    assert 'id: "browser", label: wbcT("chat.side.browser", "Browser")' in source
    assert 'window.CyreneUI.require("browser").ViewportPanel' in source
    assert "function WbcBrowserSplit(" in source
    assert "onTakeoverComplete: onTakeoverComplete" in source
    assert "desiredTabId: active.id || tabId" in source
    assert "handleAnswer(pending.id" in source


def test_warning_toast_has_no_colored_left_accent():
    root = Path(__file__).resolve().parent.parent
    css = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    assert ".workbench-toast.is-warning { border-left: 1px solid var(--wb-line); }" in css
    assert ".workbench-toast.is-warning { border-left-color: var(--wb-amber); }" not in css


def test_workbench_subagent_payload_recovers_chat_scoped_snapshot(monkeypatch):
    from cyrene import subagent
    from cyrene.workbench import chat as routes_workbench_chat

    messages = [
        {"role": "user", "round_id": "round_1", "content": "Compare two approaches"},
        {
            "role": "assistant",
            "round_id": "round_1",
            "tool_calls": [{
                "id": "spawn_1",
                "function": {
                    "name": "spawn_subagent",
                    "arguments": json.dumps({"agent_id": "alpha", "task": "Review approach A"}),
                },
            }],
        },
        {
            "role": "assistant",
            "round_id": "round_1",
            "subagent_flow_snapshot": {
                "round_id": "round_1",
                "agents": {
                    "alpha": {
                        "task": "Review approach A",
                        "status": "done",
                        "result": "Approach A is simpler.",
                        "messages": [],
                        "round_id": "round_1",
                    },
                },
                "comm_messages": [],
            },
        },
    ]
    monkeypatch.setattr(routes_workbench_chat, "_session_state_messages", lambda _chat_id: messages)
    monkeypatch.setattr(subagent, "_registry", {})

    payload = routes_workbench_chat._workbench_subagent_payload("wbchat_one")

    assert payload["activeRoundId"] == "round_1"
    assert payload["rounds"][0]["title"] == "Compare two approaches"
    assert payload["agents"][0]["id"] == "alpha"
    assert payload["agents"][0]["result"] == "Approach A is simpler."
    assert payload["messages"][0]["type"] == "result"


def _run_workbench_shortcuts_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    runtime_path = root / "src" / "webui" / "frontend" / "platform" / "runtime.jsx"
    shortcuts_path = root / "src" / "webui" / "frontend" / "workbench-shortcuts.jsx"
    script = f"""
    const fs = require("fs");
    const store = {{}};
    global.window = {{
        navigator: {{ userAgent: "Mozilla/5.0 (Windows NT 10.0)" }},
        dispatchEvent: () => {{}},
        Event: function (n) {{ this.type = n; }},
    }};
    global.localStorage = {{
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => {{ store[k] = String(v); }},
        removeItem: (k) => {{ delete store[k]; }},
    }};
    eval(fs.readFileSync({json.dumps(str(runtime_path))}, "utf8"));
    eval(fs.readFileSync({json.dumps(str(shortcuts_path))}, "utf8"));
    const result = ({expression});
    process.stdout.write(JSON.stringify(result));
    """
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def test_workbench_shortcuts_module_exposes_actions_and_platform_aware_mod():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-shortcuts.jsx").read_text(encoding="utf-8")

    assert 'window.CyreneUI.shortcuts = window.CyreneUI.register("shortcuts"' in source
    assert "isMacPlatform" in source
    assert '"mod"' in source
    # Composer Enter-to-send is one of the default bindings so the setting panel
    # can show and rebind it.
    assert '"composer-send"' in source
    assert '"Enter"' in source

    ids = _run_workbench_shortcuts_js(
        'window.CyreneUI.require("shortcuts").list().map(function (i) { return i.id; })'
    )
    assert "search" in ids
    assert "new-chat" in ids
    assert "new-task" in ids
    assert "composer-send" in ids
    assert "composer-newline" in ids
    assert "switch-session-1" in ids
    assert "switch-session-2" in ids
    assert "switch-session-3" in ids
    assert "next-session" in ids
    assert "previous-session" in ids
    assert "close-session-tab" in ids


def test_workbench_shortcut_labels_use_tab_terminology_in_both_locales():
    root = Path(__file__).resolve().parent.parent
    translations = (
        root / "src" / "webui" / "frontend" / "workbench-i18n.jsx"
    ).read_text(encoding="utf-8")

    for expected in (
        '"shortcut.action.switchSession1": "Open topbar tab 1"',
        '"shortcut.action.nextSession": "Next topbar tab"',
        '"shortcut.action.previousSession": "Previous topbar tab"',
        '"shortcut.action.closeSessionTab": "Remove current tab"',
        '"shortcut.action.switchSession1": "打开顶栏标签页 1"',
        '"shortcut.action.nextSession": "下一个顶栏标签页"',
        '"shortcut.action.previousSession": "上一个顶栏标签页"',
        '"shortcut.action.closeSessionTab": "移除当前标签页"',
    ):
        assert expected in translations

    for removed in (
        "Open topbar session",
        "recent session tab",
        "topbar session",
        "current session tab",
        "打开顶栏 Session",
        "最近 Session Tab",
        "顶栏 Session",
        "当前 Session",
    ):
        assert removed not in translations


def test_workbench_shortcuts_matches_mod_k_on_windows_user_agent():
    # The "mod" token resolves to Ctrl on Windows/Linux user agents. A Cmd+K
    # event (metaKey) on a Windows UA should also match search, because "mod"
    # matches meta OR ctrl so Mac keyboards work everywhere; a plain "k"
    # should not match.
    result = _run_workbench_shortcuts_js(
        "{"
        ' ctrlK: window.CyreneUI.require("shortcuts").matches({ key: "k", metaKey: false, ctrlKey: true, shiftKey: false, altKey: false }, "search"),'
        ' cmdK: window.CyreneUI.require("shortcuts").matches({ key: "k", metaKey: true, ctrlKey: false, shiftKey: false, altKey: false }, "search"),'
        ' plainK: window.CyreneUI.require("shortcuts").matches({ key: "k", metaKey: false, ctrlKey: false, shiftKey: false, altKey: false }, "search"),'
        ' enter: window.CyreneUI.require("shortcuts").matches({ key: "Enter", metaKey: false, ctrlKey: false, shiftKey: false, altKey: false }, "composer-send"),'
        ' shiftEnter: window.CyreneUI.require("shortcuts").matches({ key: "Enter", metaKey: false, ctrlKey: false, shiftKey: true, altKey: false }, "composer-send"),'
        ' shiftEnterNewline: window.CyreneUI.require("shortcuts").matches({ key: "Enter", metaKey: false, ctrlKey: false, shiftKey: true, altKey: false }, "composer-newline")'
        "}"
    )
    assert result == {
        "ctrlK": True,
        "cmdK": True,  # mod matches meta OR ctrl so Mac keyboards work everywhere
        "plainK": False,
        "enter": True,
        "shiftEnter": False,
        "shiftEnterNewline": True,
    }


def test_workbench_shortcuts_persist_and_reset_custom_binding():
    result = _run_workbench_shortcuts_js(
        "(function () {"
        ' var sc = window.CyreneUI.require("shortcuts");'
        " var before = sc.describe('search').join('+');"
        " sc.set('search', ['mod', 'P']);"
        " var after = sc.describe('search').join('+');"
        " sc.reset('search');"
        " var reset = sc.describe('search').join('+');"
        " var isCustom = sc.isCustom('search');"
        " return { before: before, after: after, reset: reset, isCustom: isCustom };"
        "})()"
    )
    assert result == {
        "before": "mod+K",
        "after": "mod+P",
        "reset": "mod+K",
        "isCustom": False,
    }


def test_workbench_shortcuts_capture_event_converts_ctrl_to_mod_on_windows():
    # On Windows/Linux, pressing Ctrl+K should capture as ["mod", "K"] so the
    # binding stays portable when the user later opens the app on a Mac.
    result = _run_workbench_shortcuts_js(
        "{"
        ' ctrlK: window.CyreneUI.require("shortcuts").captureEvent({ key: "k", metaKey: false, ctrlKey: true, shiftKey: false, altKey: false }),'
        ' shiftEnter: window.CyreneUI.require("shortcuts").captureEvent({ key: "Enter", metaKey: false, ctrlKey: false, shiftKey: true, altKey: false }),'
        ' escape: window.CyreneUI.require("shortcuts").captureEvent({ key: "Escape", metaKey: false, ctrlKey: false, shiftKey: false, altKey: false }),'
        ' pureMod: window.CyreneUI.require("shortcuts").captureEvent({ key: "Control", metaKey: false, ctrlKey: true, shiftKey: false, altKey: false })'
        "}"
    )
    assert result["ctrlK"] == {"cancelled": False, "keys": ["mod", "K"]}
    assert result["shiftEnter"] == {"cancelled": False, "keys": ["shift", "Enter"]}
    assert result["escape"] == {"cancelled": True, "keys": []}
    assert result["pureMod"] == {"cancelled": False, "keys": []}


def test_workbench_task_composer_uses_enter_to_send_via_shortcut_module():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")

    # The old Cmd/Ctrl+Enter to send behavior is replaced by the shortcut module
    # so Enter sends directly (matching the chat composer).
    composer_block = source.split("function TaskComposer(", 1)[1].split("function composerPlaceholder", 1)[0]
    assert 'sc.matches(event, "composer-send")' in composer_block
    assert "event.metaKey || event.ctrlKey" not in composer_block.split("function onKeyDown")[1].split("}")[0]


def test_workbench_task_composer_includes_model_and_reasoning_picker():
    root = Path(__file__).resolve().parent.parent
    workbench = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "webui" / "frontend" / "workbench-model.jsx").read_text(encoding="utf-8")

    composer = workbench.split("function TaskComposer(", 1)[1].split(
        "function ComposerDisclaimer", 1
    )[0]
    assert "wbc-model-button" in composer
    assert 'setModelPanel("models")' in composer
    assert 'setModelPanel("effort")' in composer
    assert "onSelectedModelIdChange(id)" in composer
    assert "onReasoningEffortChange(effort)" in composer
    assert "model: options.model || undefined" in model
    assert 'reasoningEffort: options.reasoningEffort || ""' in model
    task_work_area = workbench.split("function TaskWorkArea(", 1)[1].split(
        "function TaskComposer(", 1
    )[0]
    assert "applyInitialModels(options);" in task_work_area
    assert task_work_area.index("applyInitialModels(options);") < task_work_area.index(
        "return catalogRequest.then"
    )


def test_workbench_model_picker_compacts_without_overlapping_send_button():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    composer_rule = styles.split(".wbc-composer-box {", 1)[1].split("}", 1)[0]
    anchor_rule = styles.split(".wbc-model-anchor {", 1)[1].split("}", 1)[0]
    button_rule = styles.split(".wbc-model-button {", 1)[1].split("}", 1)[0]
    compact_rule = styles.split(
        "@container wbc-composer (max-width: 420px) {", 1
    )[1].split(".wbc-pop-anchor", 1)[0]

    assert "container-name: wbc-composer;" in composer_rule
    assert "container-type: inline-size;" in composer_rule
    assert "flex: 0 1 auto;" in anchor_rule
    assert "min-width: 0;" in anchor_rule
    assert "max-width: 100%;" in button_rule
    assert 'className="wbc-model-button-icon" aria-hidden="true"' in (
        root / "src" / "webui" / "frontend" / "workbench-chat.jsx"
    ).read_text(encoding="utf-8")
    assert 'className="wbc-model-button-icon" aria-hidden="true"' in (
        root / "src" / "webui" / "frontend" / "workbench.jsx"
    ).read_text(encoding="utf-8")
    assert ".wbc-model-button-icon" in compact_rule
    assert "display: inline-flex;" in compact_rule
    assert ".wbc-model-button-name" in compact_rule
    assert ".wbc-model-button-effort" in compact_rule
    assert "display: none;" in compact_rule


def test_workbench_file_drop_routes_files_to_task_chat_and_knowledge():
    root = Path(__file__).resolve().parent.parent
    workbench = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    library = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    # The shared document-level target prevents Chromium's default file
    # navigation and forwards the real DataTransfer FileList.
    drop_hook = workbench.split("function useWorkbenchFileDrop", 1)[1].split(
        "function WorkbenchFileDropOverlay", 1
    )[0]
    assert 'types.indexOf("Files")' in drop_hook
    assert 'document.addEventListener("dragover"' in drop_hook
    assert 'document.addEventListener("drop"' in drop_hook
    assert "event.preventDefault()" in drop_hook
    assert "event.dataTransfer.files" in drop_hook

    # Task and chat route a drop from the whole module to their existing upload
    # pipelines, which append the uploaded files to the composer attachment row.
    assert 'new CustomEvent("cyrene:add-task-attachments"' in workbench
    assert 'window.addEventListener("cyrene:add-task-attachments"' in workbench
    assert "model.uploadAttachments(files)" in workbench
    assert 'new CustomEvent("cyrene:add-chat-attachments"' in chat
    assert 'window.addEventListener("cyrene:add-chat-attachments"' in chat
    assert "model.uploadFiles(files)" in chat

    # The canonical library page keeps file ingestion on its existing upload path.
    assert "function handleFiles(files)" in library
    assert 'type: "file", multiple: true' in library
    assert "client.upload(files)" in library
    assert ".wb-file-drop-overlay" in styles


def test_workbench_file_drop_hook_prevents_navigation_and_delivers_files():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    hook_source = "function useWorkbenchFileDrop" + source.split(
        "function useWorkbenchFileDrop", 1
    )[1].split("function WorkbenchFileDropOverlay", 1)[0]
    script = f"""
const documentListeners = {{}};
const windowListeners = {{}};
const stateChanges = [];
let cleanup = null;
global.document = {{
  addEventListener: (name, fn) => {{ documentListeners[name] = fn; }},
  removeEventListener: (name) => {{ delete documentListeners[name]; }}
}};
global.window = {{
  addEventListener: (name, fn) => {{ windowListeners[name] = fn; }},
  removeEventListener: (name) => {{ delete windowListeners[name]; }}
}};
global.React = {{
  useState: (value) => [value, (next) => stateChanges.push(next)],
  useRef: (value) => ({{ current: value }}),
  useEffect: (fn) => {{ cleanup = fn(); }}
}};
eval({json.dumps(hook_source)});
let delivered = [];
useWorkbenchFileDrop((files) => {{ delivered = Array.from(files).map((file) => file.name); }}, true);
let prevented = 0;
const transfer = {{ types: ["Files"], files: [{{ name: "alpha.txt" }}, {{ name: "beta.pdf" }}], dropEffect: "none" }};
const event = {{ dataTransfer: transfer, preventDefault: () => {{ prevented += 1; }} }};
documentListeners.dragenter(event);
documentListeners.dragover(event);
documentListeners.drop(event);
if (cleanup) cleanup();
process.stdout.write(JSON.stringify({{
  delivered,
  prevented,
  dropEffect: transfer.dropEffect,
  stateChanges,
  listenersAfterCleanup: Object.keys(documentListeners)
}}));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result["delivered"] == ["alpha.txt", "beta.pdf"]
    assert result["prevented"] == 3
    assert result["dropEffect"] == "copy"
    assert result["stateChanges"] == [True, True, False]
    assert result["listenersAfterCleanup"] == []


def test_workbench_settings_overlay_has_shortcuts_tab_and_panel():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")
    translations = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    assert '{ id: "shortcuts", labelKey: "settings.shortcuts" }' in source
    assert "function ShortcutsPanel" in source
    assert "React.createElement(ShortcutsPanel" in source
    assert 'window.CyreneUI.require("shortcuts")' in source
    assert "captureEvent" in source
    # The panel groups bindings and offers a reset-all action.
    assert "settings.shortcutGroupGlobal" in source
    assert "settings.resetShortcuts" in source
    # i18n keys for both languages
    assert '"settings.shortcuts": "Shortcuts"' in translations
    assert '"settings.shortcuts": "快捷键"' in translations
    assert '"shortcut.action.search"' in translations
    assert '"shortcut.action.composerSend"' in translations
    # Styles for the panel
    assert ".wb-shortcuts-panel" in styles
    assert ".wb-shortcut-row" in styles
    assert ".wb-shortcut-capture" in styles
    # The new module is loaded before the panels that consume it
    assert "compiled/workbench-shortcuts.js?v=0.7.0b12" in index


def test_workbench_about_related_actions_only_click_right_button():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    related_block = source.split('React.createElement("section", { className: "wb-about-related-card" }', 1)[1].split(
        "changelogOpen && React.createElement", 1
    )[0]

    assert 'React.createElement("div", { key: item.title, className: "wb-about-related-row" }' in related_block
    assert 'React.createElement("button", { type: "button", className: "wb-about-related-action", onClick: item.onClick }' in related_block
    assert 'React.createElement("a", { className: "wb-about-related-action", href: item.href, target: "_blank", rel: "noopener noreferrer" }' in related_block
    assert 'className: "wb-about-related-row", onClick: item.onClick' not in related_block
    assert 'className: "wb-about-related-row", href: item.href' not in related_block

    related_row_rule = styles.split(".wb-about-related-row {", 1)[1].split("}", 1)[0]
    assert "cursor: pointer" not in related_row_rule
    assert ".wb-about-related-row:hover" not in styles
    assert ".wb-about-related-action:hover" in styles
    assert ".wb-about-related-action:focus-visible" in styles

    final_action_rule = styles.rsplit(".workbench-shell .settings-overlay .wb-about-related-action {", 1)[1].split("}", 1)[0]
    assert "min-height: calc(30px * var(--wb-ui-density-scale, 1)) !important" in final_action_rule
    assert "font-family: var(--wb-font) !important" in final_action_rule
    assert "font-size: calc(13px * var(--wb-ui-font-scale, 1)) !important" in final_action_rule
    assert "font-weight: 600 !important" in final_action_rule
    assert "line-height: 1 !important" in final_action_rule

    assert "--wb-settings-panel-height: min(540px, calc(100vh - 48px))" in styles
    assert styles.count("height: var(--wb-settings-panel-height);") == 3


def test_remote_settings_keeps_compatibility_on_and_persists_package_checkboxes():
    root = Path(__file__).resolve().parent.parent
    source = (
        root / "src" / "webui" / "frontend" / "settings-overlay.jsx"
    ).read_text(encoding="utf-8")
    i18n = (
        root / "src" / "webui" / "frontend" / "workbench-i18n.jsx"
    ).read_text(encoding="utf-8")
    styles = (
        root / "src" / "webui" / "frontend" / "workbench.css"
    ).read_text(encoding="utf-8")

    remote_panel = source.split("function RemotePanel(p) {", 1)[1].split(
        "function RemotePeerCard", 1
    )[0]
    assert "FieldRow(" in remote_panel
    assert "Toggle(!!remote.enabled" in remote_panel
    assert "remote-status-card" not in remote_panel
    assert ".remote-status-card" not in styles
    assert "remoteCapabilityLabel" not in source
    assert "remoteCompatibilityCapabilities" not in remote_panel
    assert 't("settings.remoteCompatibilityAlwaysOn")' in source
    assert 'className: "remote-option-list remote-tool-package-options"' in source
    assert "toggleInviteToolPack(item.wire_name)" in remote_panel
    assert "inviteToolPacksRef.current = next" in remote_panel
    assert "default_tool_packs: next," in remote_panel
    assert "default_tool_packs: nextRemote.default_tool_packs || []" in remote_panel
    assert "remoteRequiredCapabilities(" in source
    assert "remoteToolPackGrants(" in source
    assert "remoteTransportDetail(t, transport)" in remote_panel
    assert "transport.port_fallback" in source
    assert i18n.count('"settings.remoteTransportAlternatePort"') == 2
    assert 'var [pairingMode, setPairingMode] = useStateSt("share")' in remote_panel
    assert 'className: "wb-seg remote-pairing-tabs"' in remote_panel
    assert "inviteDefaultsInitializedRef.current = true" in remote_panel
    assert "payload.remote_tool_packages || []" in remote_panel
    assert "payload.projects || []" in remote_panel
    assert 'React.createElement("details", { className: "remote-pairing-settings" }' in remote_panel
    assert 'React.createElement("summary", null' in remote_panel
    assert 'className: "remote-pairing-settings-chevron"' in remote_panel
    assert "ExternalChevron()" in remote_panel
    assert ".remote-pairing-settings[open] summary" in styles
    assert ".remote-pairing-settings[open] .remote-pairing-settings-chevron" in styles
    assert ".remote-pairing-settings summary:focus {" in styles
    assert ".remote-pairing-settings summary:focus-visible {" in styles
    assert 'className: "remote-pairing-columns"' not in remote_panel
    assert 'className: "remote-pairing-card"' not in remote_panel
    assert ".remote-pairing-columns" not in styles
    assert ".remote-pairing-card" not in styles
    assert i18n.count('"settings.remotePairModeShare"') == 2
    assert i18n.count('"settings.remotePairModeControl"') == 2
    assert i18n.count('"settings.remotePairCapabilities"') == 2
    assert i18n.count('"settings.remoteShareSettings"') == 2
    assert i18n.count('"settings.remoteShareSettingsHint"') == 2
    assert '"settings.remoteAllowController": "允许其他设备控制 Cyrene"' in i18n
    assert '"settings.remoteAllowControllerHint": "在共享设置中修改允许远程调用的工具或项目。"' in i18n
    assert 'fetch("/api/remote/pairing/short-key"' in remote_panel
    assert 'fetch("/api/remote/pairing/connect"' in remote_panel
    assert 'error.code === "remote_pairing_peer_update_required"' in remote_panel
    assert i18n.count('"settings.remotePeerUpdateRequired"') == 2
    assert "function persistSettings(nextRemote, version)" in remote_panel
    assert "function updateRemoteSettings(nextRemote, immediate)" in remote_panel
    assert "}, 600);" in remote_panel
    assert "onBlur: flushRemoteSettings" in remote_panel
    assert "onClick: saveSettings" not in remote_panel
    assert 't("settings.saveApply")' not in remote_panel
    assert 'placeholder: "192.168.1.20:37841"' in remote_panel
    assert 'placeholder: "ABCDE-23456"' in remote_panel
    assert 'className: "remote-direct-offer"' in remote_panel
    assert 'window.cyrene.writeClipboardText(value)' in remote_panel
    assert '"aria-label": t("settings.remoteCopyPairingKey")' in remote_panel
    assert "remoteEventLabel(t, event.event_type)" in remote_panel
    assert "remoteOutcomeLabel(t, event.outcome)" in remote_panel
    assert "remoteEventTime(event.created_at)" in remote_panel
    assert 'window.CyreneUI.require("feedback")' in remote_panel
    assert 'className: "remote-notice"' not in remote_panel
    assert ".remote-notice {" not in styles
    assert "justify-content: center;" in styles
    assert i18n.count('"settings.remoteAudit": "Connection events"') == 1
    assert i18n.count('"settings.remoteAudit": "连接事件"') == 1
    assert i18n.count('"settings.remoteEvent.remote_gateway_started"') == 2
    assert i18n.count('"settings.remoteOutcome.online"') == 2
    assert "incomingInvitation" not in remote_panel
    assert "incomingResponse" not in remote_panel
    assert 't("settings.remoteRelayUrl")' not in remote_panel
    assert 'placeholder: "wss://relay.example/v1"' not in remote_panel
    assert "peer.lan_address" in source
    assert ".wb-textarea {" in styles
    assert "height: 68px;" in styles
    assert i18n.count('"settings.remotePairingKey"') == 2
    assert i18n.count('"settings.remoteDeviceAddress"') == 2

    assert i18n.count('"settings.remoteCompatibilityAlwaysOn"') == 2

    for status in (
        "Configured",
        "Connected",
        "Connecting",
        "Disabled",
        "Error",
        "ErrorDetail",
        "Unknown",
    ):
        assert i18n.count(f'"settings.remoteTransport{status}"') == 2


def test_workbench_about_panel_reads_app_version_from_registered_data_store():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")

    update_section = source.split("function UpdateSection({ t, config }) {", 1)[1].split(
        "function SettingsVersionIcon", 1
    )[0]

    assert 'var dataState = window.CyreneUI.require("data").state;' in update_section
    assert "dataState.appVersion" in update_section


def test_workbench_settings_dynamic_lists_have_stable_react_keys():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")

    shortcuts_panel = source.split("function ShortcutsPanel(p) {", 1)[1].split(
        "function BudgetPanel", 1
    )[0]
    model_card = source.split("function ModelCard(children, key) {", 1)[1].split(
        "function ModelField", 1
    )[0]

    assert "React.createElement(React.Fragment, { key: groupKey }" in shortcuts_panel
    assert 'React.createElement("div", { className: "wb-shortcut-row", key: item.id }' in shortcuts_panel
    assert 'React.createElement("div", { className: "wb-model-card", key: key }, ...children)' in model_card


def test_workbench_help_center_lists_shortcuts_from_module_with_customize_link():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")

    # Help center reads the binding list from the registered shortcuts service instead of
    # hardcoding the keys array, so customizations surface there too.
    help_block = source.split("function WorkbenchHelpCenter", 1)[1].split("function WorkbenchEditProjectModal", 1)[0]
    assert 'window.CyreneUI.require("shortcuts")' in help_block
    assert "shortcutList" in help_block
    assert "help.customizeShortcuts" in help_block
    # The old hardcoded list is gone.
    assert '{ id: "search", label: t("help.shortcut.search"), keys: ["mod", "K"] }' not in help_block


def test_workbench_global_shortcut_handler_wired_in_workbench_app():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")

    app_block = source.split("function WorkbenchApp", 1)[1].split("function WorkbenchTopbar", 1)[0]
    # A keydown listener dispatches the global shortcuts.
    assert 'addEventListener("keydown"' in app_block
    assert 'sc.matches(event, "search")' in app_block
    assert 'sc.matches(event, "new-chat")' in app_block
    assert 'sc.matches(event, "new-task")' in app_block
    new_chat_block = app_block.split('sc.matches(event, "new-chat")', 1)[1].split(
        'sc.matches(event, "new-task")', 1
    )[0]
    assert "createChat();" in new_chat_block
    assert 'setFullPage("chat");' not in new_chat_block
    assert '"new-chat":       function () { acts.createChat(); }' in app_block
    assert '"new-chat":       function () { acts.createSession(); }' not in app_block
    assert 'sc.matches(event, "settings")' in app_block
    assert 'sc.matches(event, "toggle-sidebar")' in app_block
    assert 'sc.matches(event, "switch-project")' in app_block


def test_workbench_memory_cite_tab_renders_actual_citations_not_placeholder():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")

    # The old placeholder text is gone.
    assert "引用记录会在 Agent 引用此记忆时自动记录" not in source
    # The Cite tab now renders citations from the memory's citations list.
    assert "m.citations" in source
    assert "wb-mem-cite-list" in source
    assert "wb-mem-cite-row" in source


def test_workbench_memory_history_tab_renders_events_not_hardcoded():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")

    # The old hardcoded two-row history is gone — isolate the historyBody block.
    history_block = source.split("var historyBody", 1)[1].split("return h(\"aside\"", 1)[0]
    assert '"最后更新"' not in history_block
    assert '"创建记忆"' not in history_block
    # The History tab now renders from m.history.
    assert "m.history" in source
    assert "historyEvents" in source
    assert "action_label" in source


def test_workbench_memory_detail_wraps_long_content_without_horizontal_overflow():
    root = Path(__file__).resolve().parent.parent
    css = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    detail_block = css.split("\n.wb-mem-detail {", 1)[1].split("}", 1)[0]
    scroll_block = css.split("\n.wb-mem-detail-scroll {", 1)[1].split("}", 1)[0]
    hero_text_block = css.split("\n.wb-mem-detail-hero p {", 1)[1].split("}", 1)[0]
    content_block = css.split("\n.wb-mem-content-full {", 1)[1].split("}", 1)[0]
    citation_block = css.split("\n.wb-mem-cite-snippet {", 1)[1].split("}", 1)[0]
    footer_button_block = css.split("\n.wb-mem-detail-foot .wb-btn {", 1)[1].split("}", 1)[0]

    assert "overflow: hidden;" in detail_block
    assert "overflow-x: hidden;" in scroll_block
    assert "overflow-wrap: anywhere;" in hero_text_block
    assert "overflow-wrap: anywhere;" in content_block
    assert "overflow-wrap: anywhere;" in citation_block
    assert "white-space: normal;" in footer_button_block


def test_workbench_skill_learning_uses_actionable_candidate_status_only():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    translations = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    assert 'activeCandidate ? h("div", { className: "wb-learning-review-pill "' in source
    assert "candidateNextStepText(activeCandidate, t)" in source
    assert 'activePanel === "learning" ? null : rail' in source
    assert 'onExit: function () { setActivePanel(""); }' in source
    assert "不是可复用的多工具流程" not in translations
    assert '"memory.learning.noRepeatYet": "尚未发现重复"' in translations


def test_workbench_skill_learning_has_small_screen_progressive_disclosure():
    root = Path(__file__).resolve().parent.parent
    css = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    compact_three_column = css.split("@media (min-width: 761px) and (max-width: 980px)", 1)[1].split("@media", 1)[0]
    assert ".wb-mem-page.learning-active > .wb-mem-detail" in compact_three_column
    assert "display: flex;" in compact_three_column
    assert "grid-template-columns: 220px minmax(280px, 1fr);" in compact_three_column
    narrow_block = css.split("@media (max-width: 760px)", 1)[1].split("@media", 1)[0]
    assert ".wb-mem-page.learning-active > .wb-mem-detail { display: none; }" in narrow_block
    assert "@media (max-width: 1500px)" not in css
    assert "@media (max-width: 1080px)" in css
    assert "@media (max-width: 760px)" in css
    assert "grid-template-rows: minmax(220px, 38%) minmax(0, 1fr);" in css


def test_workbench_skill_learning_remains_operable_in_short_windows():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    css = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    translations = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    sidebar_block = css.split(".wb-learning-session-list {", 1)[1].split("}", 1)[0]
    sessions_block = css.split(".wb-learning-side-section.sessions {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in sidebar_block
    assert "scrollbar-width: none;" in sidebar_block
    assert ".wb-learning-session-list::-webkit-scrollbar" in css
    assert "flex: 1 0 200px;" in sessions_block
    assert "min-height: 200px;" in sessions_block
    assert "@media (max-height: 760px)" in css

    assert "translatedToolParamName(item.key, t)" in source
    assert '"memory.learning.toolParam.payload": "Payload"' in translations
    assert '"memory.learning.toolParam.payload": "操作数据"' in translations
    assert '"memory.learning.toolParam.target": "目标元素"' in translations


def test_workbench_memory_related_uses_tag_and_content_matching_not_category_only():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")

    # The old simple category-only filter is gone — the filter line that used
    # category as the sole match criterion no longer exists.
    assert "m.id !== selected.id && m.category === selected.category" not in source
    # The new scoring uses shared tags and content word overlap.
    assert "selTags" in source
    assert "selWords" in source
    assert "score" in source
    # Category is now just one mild scoring signal, not a hard filter.
    related_block = source.split("var related = useMemo", 1)[1].split("var related", 1)[0].split("function applyPayload", 1)[0]
    assert "score += 1" in related_block  # category match adds 1
    assert "score += 3" in related_block  # shared tag adds 3


def test_workbench_library_groups_items_with_collections_and_tags():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")

    assert "library.myCollections" in source
    assert "library.tagCloud" in source
    assert 'scope.type === "collection"' in source
    assert 'scope.type === "tag"' in source


def test_workbench_library_tags_are_editable_inline():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")

    assert "function TagsWorkspace" in source
    assert "wb-lib-tag-editor" in source
    assert "props.onUpdate({ tags: next })" in source
    assert "client.update(selectedId, value)" in source


def test_workbench_library_content_tab_renders_markdown():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")
    renderer = (root / "src" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx").read_text(encoding="utf-8")

    assert "renderMarkdownHtml" in source
    assert 'window.CyreneUI.require("markdown").render' in source
    assert "root.marked.parse(source)" in renderer
    assert "root.DOMPurify.sanitize" in renderer
    assert "dangerouslySetInnerHTML" in source
    assert "wb-lib-markdown" in source


def test_markdown_bare_url_stops_at_cjk_punctuation():
    root = Path(__file__).resolve().parent.parent
    renderer_path = root / "src" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx"
    marked_path = root / "src" / "webui" / "static" / "app" / "marked.min.js"
    script = f"""
const fs = require("fs");
const vm = require("vm");
const marked = require({json.dumps(str(marked_path))});
const services = {{}};
const window = {{
  marked,
  DOMPurify: {{ sanitize: (html) => html }},
  CyreneUI: {{
    register: (name, service) => (services[name] = service),
  }},
}};
vm.runInNewContext(fs.readFileSync({json.dumps(str(renderer_path))}, "utf8"), {{ window }});
const source = "B 站首页（www.bilibili.com），顶部导航已加载。";
process.stdout.write(JSON.stringify({{
  bare: services.markdown.renderRich(source),
  explicit: services.markdown.renderRich("[示例](https://example.com/a，b)"),
}}));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert '<a href="http://www.bilibili.com">www.bilibili.com</a>），顶部导航已加载。' in result["bare"]
    assert "%EF%BC%89" not in result["bare"]
    assert '<a href="https://example.com/a%EF%BC%8Cb">示例</a>' in result["explicit"]


def test_workbench_library_list_uses_explicit_pagination():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")

    assert "var PAGE_SIZE = 120" in source
    assert "function loadMore()" in source
    assert "data.items.length < data.total" in source
    assert "library.loadMore" in source


def test_packaged_electron_preserves_explicit_runtime_path_overrides():
    root = Path(__file__).resolve().parent.parent
    source = (root / "electron" / "main.js").read_text(encoding="utf-8")

    assert "process.env.CYRENE_USER_DATA_DIR || getCyreneUserDataDir()" in source
    assert "process.env.CYRENE_CACHE_DIR || getCyreneCacheDir()" in source
    assert "process.env.CYRENE_TEMP_DIR || getCyreneTempDir()" in source


def test_workbench_composers_upload_files_pasted_from_clipboard():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    task = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")

    for source in (chat, task):
        assert "onPaste={onPaste}" in source
        assert "clipboard.files" in source
        assert "clipboard.items" in source
        assert 'item.kind === "file" ? item.getAsFile() : null' in source
        assert "if (!files.length) return; // Preserve the browser's normal text paste." in source
        assert "event.preventDefault();" in source
        assert "addFiles(files);" in source


def test_account_menu_codex_quota_requires_primary_oauth_and_login():
    root = Path(__file__).resolve().parent.parent
    shell = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    model = (root / "src" / "webui" / "frontend" / "workbench-model.jsx").read_text(
        encoding="utf-8"
    )
    settings = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(
        encoding="utf-8"
    )

    assert 'primary.provider !== "codex_oauth"' in shell
    assert "quotaPayload.connected === true" in shell
    assert "codexQuotaState.primary && codexQuotaState.connected" in shell
    assert 'fetch("/api/settings/openai-oauth/limits")' in shell
    assert "WorkbenchModel.codexQuotaWindows(quotaPayload.limits)" in shell
    assert "WorkbenchModel.readCodexQuotaCache()" in shell
    assert "WorkbenchModel.writeCodexQuotaCache(quotaPayload)" in shell
    assert "WorkbenchModel.codexPlanLabel(quotaPayload.account, quotaPayload.limits)" in shell

    # The settings panel and account menu share one duration-based parser.
    assert "function codexQuotaWindows(limits)" in model
    assert "function codexPlanLabel(account, limits)" in model
    assert 'if (normalized === "prolite") return "pro 5x"' in model
    assert 'if (normalized === "pro") return "pro 20x"' in model
    assert 'durationMins === 300' in model
    assert 'durationMins >= 10080' in model
    assert "codexQuotaModel.codexQuotaWindows(codexQuota.limits)" in settings
    assert 't("settings.codexQuotaPlan"' in settings


def test_phase1_stream_is_rendered_as_a_distinct_execution_card():
    root = Path(__file__).resolve().parent.parent
    source = (
        root / "src" / "webui" / "frontend" / "workbench-chat.jsx"
    ).read_text(encoding="utf-8")
    styles = (
        root / "src" / "webui" / "frontend" / "workbench.css"
    ).read_text(encoding="utf-8")
    translations = (
        root / "src" / "webui" / "frontend" / "workbench-i18n.jsx"
    ).read_text(encoding="utf-8")

    assert 'String(item.llmPhase || "") === "phase1"' in source
    assert 'kind: "phase1"' in source
    assert 'wbcT("workbenchChat.phase1Card"' in source
    assert "String(last.llmPhase) === eventPhase" in source
    assert '"workbenchChat.phase1Card": "正在理解指令"' in translations
    assert '"workbenchChat.phase1Understood": "已理解用户需求"' in translations
    assert "border-radius: 16px" in styles
