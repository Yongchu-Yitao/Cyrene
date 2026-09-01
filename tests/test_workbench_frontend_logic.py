from conftest import (
    frontend_module_source,
    workbench_chat_route_source,
    workbench_chat_source,
    workbench_i18n_source,
    workbench_runtime_source,
    workbench_settings_source,
    workbench_shell_source,
    workbench_style_source,
)
import asyncio
import json
import re
import subprocess
from pathlib import Path


def test_external_agent_frontend_consumes_unified_dynamic_events_and_viewers():
    source = workbench_chat_source()
    route_source = workbench_chat_route_source()
    settings = workbench_settings_source()
    i18n = workbench_i18n_source()

    for handler in (
        "onArtifactEvent", "onUsageUpdated", "onSessionUpdated",
        "onPermissionResolved", "onElicitationResolved",
    ):
        assert handler in source
    assert "wbcElicitationFields" in source
    assert "__agentForm: true" in source
    assert "session.configOptions" in source
    assert "capabilitySource" in source
    assert 'if (value === "agent_defined") return "agent_defined";' in source
    assert '<audio className="wbc-viewer-media"' in source
    assert '<video className="wbc-viewer-media"' in source
    assert "function wbcDurableTracePayload" in source
    assert "onUnknownAgentEvent" in source
    assert "wbcToolPresentationKind" in source
    assert "progress.slice(-40)" in source
    assert 'var failed = !!entry.failed || ["failed", "error", "failure", "expired", "cancelled"].indexOf(entryStatus) >= 0;' in source
    assert 'entry["preview"] = str(summary)' in route_source
    assert "matching = [" in route_source
    assert "open_indices = [" in route_source
    assert 'entry["preview"] = preview' in route_source
    assert 'entry["reasoningOffset"] = len("".join(self.projection.reasoning_parts))' in route_source
    assert "if projection.trace or projection.reasoning_parts:" in route_source
    assert 'del self.projection.trace[:-40]' in route_source
    assert '"trace": projection.trace[-40:]' in route_source
    assert 'status in {"failed", "error", "failure", "expired", "cancelled"}' in route_source
    assert 'PluginRegistryPanel' in settings
    assert '"workbenchChat.attachmentType.audio": "音频"' in i18n


def test_model_picker_commits_only_the_latest_server_confirmed_selection():
    composer = frontend_module_source("features/chat/composer.jsx")

    assert "var modelSelectionRequestRef = useWbcRef(0);" in composer
    assert "if (modelSelectionRequestRef.current !== selectionRequest) return;" in composer
    assert "setSelectedModelId(confirmedId);" in composer
    assert "setReasoningEffort(confirmedEffort);" in composer
    persisted_branch = composer.split(
        "if (chatId) {\n                                model.updateChatPreferences", 1
    )[1].split("} else {", 1)[0]
    assert "setSelectedModelId(id);" not in persisted_branch


def test_context_panel_has_only_the_new_agent_tree_projection():
    source = workbench_chat_source()
    i18n = workbench_i18n_source()
    assert 'data.compositionSource === "agent_report"' not in source
    assert 'data.compositionSource === "public_transcript"' not in source
    assert "workbenchChat.ctxBlocks.agentReportDetailed" not in source
    assert 'className: "wbc-context-source-info"' not in source
    assert 'className: "wbc-context-source-popover"' not in source
    assert "workbenchChat.ctxBlocks.externalEstimate" not in i18n
    assert 'className: "wbc-ctx-layer-row"' in source
    assert "workbenchChat.ctxBlocks.layer.agent_other" not in i18n


def test_external_agent_context_hides_cyrene_only_inbox_and_plugin_packs():
    source = workbench_chat_source()
    assert 'var externalAgent = !!wbcChatAgent(chat) && !wbcIsBuiltinAgent(wbcChatAgent(chat));' in source
    assert '!externalAgent && <WbcInboxCard' in source
    assert '!externalAgent && <section className="workbench-side-section" aria-label={wbcT("workbenchChat.usedPluginPacks"' in source


def test_system_default_model_description_is_localized_without_rewriting_custom_copy():
    source = workbench_chat_source()
    i18n = workbench_i18n_source()
    friendly = "function wbcFriendlyModelName(" + source.split("function wbcFriendlyModelName(", 1)[1].split("function wbcLocalizedModelDescription", 1)[0]
    localized = "function wbcLocalizedModelDescription(" + source.split("function wbcLocalizedModelDescription(", 1)[1].split("function wbcNormalizePermissionMode", 1)[0]
    script = f'''\nfunction wbcT(key, fallback, params) {{\n  if (key === "workbenchChat.modelProviderDefault") return params.provider + " 默认配置";\n  return fallback;\n}}\n{friendly}\n{localized}\nprocess.stdout.write(JSON.stringify([\n  wbcLocalizedModelDescription({{model:"deepseek-v4-flash", desc:"DeepSeek default"}}),\n  wbcLocalizedModelDescription({{model:"deepseek-v4-flash", desc:"团队默认模型"}})\n]));\n'''
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == ["DeepSeek 默认配置", "团队默认模型"]
    assert '"workbenchChat.modelProviderDefault": "{provider} 默认配置"' in i18n


def test_chat_files_merge_message_attachments_and_nested_agent_output():
    source = workbench_chat_source()
    helper_source = "function wbcChatArtifactFiles(" + source.split(
        "function wbcChatArtifactFiles(", 1
    )[1].split("function wbcChatDeliveredArtifacts", 1)[0]
    script = f"""
function wbcEditableChatFileResource(chat, file) {{ return file; }}
eval({json.dumps(helper_source)});
const files = wbcChatArtifactFiles({{
  id: "chat/one",
  messages: [{{ role: "user", attachments: [{{ id: "upload", name: "brief.txt", url: "/uploads/brief" }}] }}],
  files: [{{ id: "made", name: "result.md", path: "other folder/result.md", source: "agent" }}]
}});
process.stdout.write(JSON.stringify(files));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    files = json.loads(completed.stdout)

    assert [item["file"]["name"] for item in files] == ["brief.txt", "result.md"]
    assert files[1]["file"]["url"] == "/api/workbench/chats/chat%2Fone/files/other%20folder/result.md"


def test_chat_artifacts_only_include_assistant_deliveries():
    source = workbench_chat_source()
    helper_source = "function wbcChatDeliveredArtifacts(" + source.split(
        "function wbcChatDeliveredArtifacts(", 1
    )[1].split("function wbcEditableChatFileResource", 1)[0]
    script = f"""
function wbcEditableChatFileResource(chat, file) {{ return file; }}
eval({json.dumps(helper_source)});
const files = wbcChatDeliveredArtifacts({{
  messages: [
    {{ role: "user", attachments: [{{ id: "upload", name: "brief.txt" }}] }},
    {{ role: "assistant", attachments: [{{ id: "result", name: "result.pdf", url: "/api/chat/export/result.pdf" }}] }},
    {{ role: "assistant", attachments: [{{ id: "result", name: "result.pdf", url: "/api/chat/export/result.pdf" }}] }}
  ],
  files: [{{ id: "workspace", name: "notes.md", source: "agent" }}]
}});
process.stdout.write(JSON.stringify(files));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    files = json.loads(completed.stdout)

    assert [item["file"]["name"] for item in files] == ["result.pdf"]


def test_conversation_viewer_is_chat_scoped_and_reuses_project_file_editor_resources():
    root = Path(__file__).resolve().parent.parent
    rail_model = (root / "src/cyrene/workbench/webui/frontend/features/chat/rail-model.jsx").read_text(encoding="utf-8")
    resource_splits = (root / "src/cyrene/workbench/webui/frontend/features/chat/resource-splits.jsx").read_text(encoding="utf-8")
    project_resource = "function wbcProjectFileResource(" + rail_model.split(
        "function wbcProjectFileResource(", 1
    )[1].split("\n\nexport {", 1)[0]
    editable_resource = "function wbcEditableChatFileResource(" + resource_splits.split(
        "function wbcEditableChatFileResource(", 1
    )[1].split("function wbcViewerFileFromItems", 1)[0]
    viewer_match = "function wbcViewerFileFromItems(" + resource_splits.split(
        "function wbcViewerFileFromItems(", 1
    )[1].split("function wbcArtifactFileKey", 1)[0]
    artifact_key = "function wbcArtifactFileKey(" + resource_splits.split(
        "function wbcArtifactFileKey(", 1
    )[1].split("var WBC_PROJECT_FILE_DRAFTS", 1)[0]
    edit_url = "function wbcProjectFileEditUrl(" + resource_splits.split(
        "function wbcProjectFileEditUrl(", 1
    )[1].split("function wbcCanEditProjectTextFile", 1)[0]
    script = f"""
function wbcFileViewKind() {{ return "markdown"; }}
eval({json.dumps(project_resource + editable_resource + viewer_match + artifact_key + edit_url)});
const generated = {{ id: "made", name: "notes.md", path: "docs/notes.md", source: "agent" }};
const editable = wbcEditableChatFileResource({{ projectId: "project/one" }}, generated);
const unrelated = wbcViewerFileFromItems(
  {{ name: "other.md", path: "other.md", source: "project", projectId: "project/one" }},
  [{{ file: editable }}]
);
const selected = wbcViewerFileFromItems(generated, [{{ file: editable }}]);
process.stdout.write(JSON.stringify({{
  editable,
  editUrl: wbcProjectFileEditUrl(editable),
  unrelated,
  selected
}}));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result["editable"]["source"] == "project"
    assert result["editable"]["projectId"] == "project/one"
    assert result["editable"]["id"] == "made"
    assert result["editable"]["url"] == "/api/projects/project%2Fone/files/content/docs/notes.md"
    assert result["editUrl"] == "/api/projects/project%2Fone/files/edit/docs/notes.md"
    assert result["unrelated"] is None
    assert result["selected"]["source"] == "project"

    source = workbench_chat_source()
    side_panel = source.split("function WbcSide({", 1)[1].split(
        "function WbcOverviewTab", 1
    )[0]
    assert "var chatViewerFile = wbcViewerFileFromItems(viewerFile, viewerItems);" in side_panel
    assert 'if (chatViewerFile) tabs.push({ id: "viewer"' in side_panel
    open_pane = frontend_module_source("features/chat/pane-layout-controller.jsx").split(
        "function wbcOpenPaneContent(", 1
    )[1].split(
        "\nfunction wbcUpdatePaneCard", 1
    )[0]
    assert 'payload = wbcEditableChatFileResource({ projectId: context.projectId }, payload);' in open_pane


def test_chat_file_type_labels_do_not_treat_text_formats_as_word_documents():
    source = workbench_chat_source()
    constants = "var WBC_CODE_EXTS" + source.split("var WBC_CODE_EXTS", 1)[1].split(
        "function wbcFileViewKind", 1
    )[0]
    helpers = "function wbcFileViewKind" + source.split(
        "function wbcFileViewKind", 1
    )[1].split("function wbcAttachmentVisual(file)", 1)[0]
    script = f"""
const window = {{ CyreneUI: {{ require() {{
  return {{ FileVisual: {{ visualKind(file) {{
    const name = String(file.name || file.filename || '').toLowerCase();
    if (/\\.(doc|docx)$/.test(name)) return 'doc';
    if (/\\.(csv|tsv|xlsx)$/.test(name)) return 'sheet';
    return 'file';
  }} }} }};
}} }} }};
const workbenchServices = {{ library: () => window.CyreneUI.require("library") }};
eval({json.dumps(constants + helpers)});
const files = [
  {{ name: 'notes.md', content_type: 'text/plain' }},
  {{ name: 'ci.yml', content_type: 'text/yaml' }},
  {{ name: 'output.log', content_type: 'text/plain' }},
  {{ name: 'report.docx', content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }},
  {{ filename: 'fallback.md', content_type: 'text/plain' }}
];
process.stdout.write(JSON.stringify(files.map(wbcAttachmentVisualKind)));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    assert json.loads(completed.stdout) == ["markdown", "code", "note", "doc", "markdown"]


def test_file_view_kind_recognizes_media_extensions_without_mime_metadata():
    source = workbench_chat_source()
    constants = "var WBC_CODE_EXTS" + source.split("var WBC_CODE_EXTS", 1)[1].split(
        "function wbcFileViewKind", 1
    )[0]
    helper = "function wbcFileViewKind" + source.split(
        "function wbcFileViewKind", 1
    )[1].split("function wbcAttachmentVisualKind", 1)[0]
    script = f"""
eval({json.dumps(constants + helper)});
const files = [
  {{ name: 'sorting_viz_preview.png' }},
  {{ path: 'images/photo.JPG' }},
  {{ filename: 'cover.avif' }},
  {{ contentType: 'image/jpeg' }},
  {{ name: 'generated.MP4', content_type: 'application/octet-stream' }},
  {{ url: '/api/chat/export/voice.opus?download=1', content_type: 'application/octet-stream' }}
];
process.stdout.write(JSON.stringify(files.map(wbcFileViewKind)));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    assert json.loads(completed.stdout) == [
        "image",
        "image",
        "image",
        "image",
        "video",
        "audio",
    ]


def test_office_files_use_lazy_browser_renderers_with_resource_limits():
    root = Path(__file__).resolve().parent.parent
    source = workbench_chat_source()
    build_script = (root / "src" / "cyrene" / "workbench" / "webui" / "build-jsx.mjs").read_text(
        encoding="utf-8"
    )
    package = json.loads(
        (root / "src" / "cyrene" / "workbench" / "webui" / "package.json").read_text(encoding="utf-8")
    )

    assert 'return "docx";' in source
    assert 'return "pptx";' in source
    assert 'return <WbcOfficeViewer key={url} file={file}' in source
    assert 'renderAltChunks: false' in source
    assert 'zipLimits: renderer.RECOMMENDED_ZIP_LIMITS' in source
    assert 'fitMode: "contain"' in source
    assert 'scrollContainer: container' in source
    assert 'lazySlides: true' in source
    assert 'lazyMedia: true' in source
    assert 'var fitScale = Math.min(1, availableWidth / pageWidth);' in source
    assert 'page.style.zoom = String(fitScale);' in source
    assert 'renderTarget.className = "wbc-office-content";' in source
    assert 'contentRef.current.style.transform = "scale(" + (next / 100) + ")";' in source
    assert 'Math.max(100, Math.min(300, Number(nextZoom) || 100))' in source
    assert 'viewerRef.current.setZoom(next)' not in source
    assert 'viewerRef.current.destroy()' in source
    assert 'wbcValidateOfficeArchive(buffer)' in source
    assert 'script.src = "/static/app/office/"' in source
    assert "office-docx.js" in build_script
    assert "office-pptx.js" in build_script
    assert package["dependencies"]["docx-preview"] == "0.4.0"
    assert package["dependencies"]["@aiden0z/pptx-renderer"] == "1.2.4"


def test_chat_file_visual_uses_the_precise_attachment_kind_for_icon_and_tone():
    source = workbench_chat_source()
    visual = source.split("function wbcAttachmentVisual(file)", 1)[1].split(
        "function wbcAttachmentTypeLabel", 1
    )[0]

    assert "var kind = wbcAttachmentVisualKind(file);" in visual
    assert "shared.toneForKind(kind)" in visual
    assert "shared.iconForKind(kind)" in visual


def test_library_file_visual_exposes_kind_based_rendering_without_reclassification():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    visual = source.split("var LibraryFileVisual =", 1)[1].split(
        "function cardDescription", 1
    )[0]

    assert "toneForKind: function (kind)" in visual
    assert "iconForKind: function (kind)" in visual
    assert "toneForKind(LibraryFileVisual.visualKind(item))" in visual
    assert "iconForKind(LibraryFileVisual.visualKind(item))" in visual


def test_conversation_panel_has_separate_files_and_artifacts_rows():
    source = workbench_chat_source()

    assert 'tabs.push({ id: "files", label: wbcT("workbenchChat.files", "Files") });' in source
    assert 'if (artifactItems.length) tabs.push({ id: "artifacts", label: wbcT("workbenchChat.artifacts", "Artifacts") });' in source
    assert 'files: hasFiles ? String(fileItems.length) : ""' in source
    assert 'artifacts: artifactItems.length ? String(artifactItems.length) : ""' in source


def test_global_search_times_out_and_ignores_stale_requests():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "search" / "overlay.jsx").read_text(
        encoding="utf-8"
    )

    assert "SEARCH_REQUEST_TIMEOUT_MS = 10000" in source
    assert "requestSeqRef.current !== requestId" in source
    assert "controller.__cyreneTimedOut = true" in source
    assert "function shouldIgnoreSearchResponse" in source
    assert 'if (controller.__cyreneTimedOut) setStatus("error")' in source
    assert 'e.name === "AbortError" && !controller.__cyreneTimedOut' in source


def test_new_workbench_chat_reuses_create_response_without_refetching():
    source = workbench_chat_source()
    shell = workbench_shell_source()

    assert 'var skipNextHydrationChatIdRef = useWbcRef("");' in source
    assert "skipNextHydrationChatIdRef.current = chat.id;" in source
    assert "skipNextHydrationChatIdRef.current === activeChatId" in source
    assert "newChatRequestId: newChatRequestId" in shell
    assert "handledNewChatRequestIdRef" in source
    assert "handleCreateChat();" in source


def test_chat_sidebar_card_order_helpers_normalize_and_move_cards():
    source = workbench_chat_source()
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
    source = workbench_chat_source()
    helper_source = "function wbcNormalizeChatOrder(" + source.split(
        "function wbcNormalizeChatOrder(", 1
    )[1].split("function wbcLoadChatOrder", 1)[0]
    helper_source += "function wbcMoveChatOrder(" + source.split(
        "function wbcMoveChatOrder(", 1
    )[1].split("function wbcNormalizeChatGroups", 1)[0]
    script = f"""
eval({json.dumps(helper_source)});
const defaults = ["new", "alpha", "beta", "gamma"];
const result = {{
  normalized: wbcNormalizeChatOrder(defaults, ["beta", "missing", "beta", "alpha"]),
  before: wbcMoveChatOrder(defaults, "gamma", "alpha", "before"),
  after: wbcMoveChatOrder(defaults, "new", "beta", "after"),
  unchanged: wbcMoveChatOrder(defaults, "beta", "beta", "before")
}};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result["normalized"] == ["new", "gamma", "beta", "alpha"]
    assert result["before"] == ["new", "gamma", "alpha", "beta"]
    assert result["after"] == ["alpha", "beta", "new", "gamma"]
    assert result["unchanged"] == ["new", "alpha", "beta", "gamma"]


def test_chat_rail_group_helpers_create_extend_and_normalize_groups():
    source = workbench_chat_source()
    helper_source = 'var WBC_CHAT_GROUPS_PREFIX = "cyrene-workbench-chat-groups-v1:";\n'
    helper_source += "function wbcNormalizeChatGroups(" + source.split(
        "function wbcNormalizeChatGroups(", 1
    )[1].split("function wbcConversationTrackRawStatus", 1)[0]
    script = f"""
function wbcT(_key, fallback) {{ return fallback; }}
const localStorage = {{
  getItem: function (key) {{
    return key === "cyrene-workbench-chat-groups-v1:project_1"
      ? JSON.stringify([{{ id: "cached", title: "Cached", chatIds: ["alpha", "beta"] }}])
      : null;
  }}
}};
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
const loaded = wbcLoadChatGroups("project_1", ["alpha", "beta"]);
process.stdout.write(JSON.stringify({{ created, extended, moved, removedFromThree, dissolvedAtOne, normalized, loaded }}));
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
    assert result["loaded"] == [
        {
            "id": "cached",
            "title": "Cached",
            "summary": "",
            "titleLocked": False,
            "metadataLang": "",
            "metadataChatIds": "",
            "chatIds": ["alpha", "beta"],
        }
    ]


def test_workbench_chat_group_drop_uses_one_enclosing_frame_without_stacking():
    source = workbench_chat_source()
    styles = workbench_style_source()

    rail = source.split("function WbcRail(", 1)[1].split(
        "// Conversation main (column 3)", 1
    )[0]
    drop_controller = frontend_module_source("features/chat/rail-drop-controller.jsx")
    assert "WBC_CHAT_ORDER_PREFIX" in source
    assert "WBC_CHAT_GROUPS_PREFIX" in source
    assert "wbcLoadChatGroups(projectId, defaultOrder)" in rail
    assert "WorkbenchChatModel.migrateChatGroups" in rail
    assert "Keep the last-known browser cache for offline startup" in rail
    assert "localStorage.setItem(" in rail
    assert "function wbcCreateChatGroup(" in source
    assert "function wbcRemoveChatFromGroups(" in source
    assert 'mode: "group"' in rail
    assert "sourceGroupId" in rail
    assert "commitUngroupDrop(dragState.movingId)" in rail
    assert "var updateDragState = dropController.update" in rail
    assert "function update(next)" in drop_controller
    assert "update: update" in drop_controller
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
    assert "function revealRailMenu(actions)" in rail
    assert "revealExpandedGroup" not in rail
    assert "function chatRailVisualState(chat)" in rail
    assert 'var rawStatus = String(chat.runStatus || chat.status || "").trim().toLowerCase();' in rail
    assert '!!chat.awaitingUser || !!chat.pendingQuestion' in rail
    assert 'tone: failed ? " status-failed"' in rail
    assert 'attention ? WBC_ICONS.alert' in rail
    assert 'failed ? WBC_ICONS.errorCircle' in rail
    assert 'completed ? WBC_ICONS.check' in rail
    assert 'running ? WBC_ICONS.running : WBC_ICONS.file' in rail
    assert 'running: <span className="wb-spinner wbc-chat-running-spinner"' in source
    assert ".wbc-chat-running-spinner {" in styles
    assert "animation-duration: 0.7s;" in styles
    assert "var chatStatusLabel = visualState.label" in rail
    assert 'wbcT("status.failed", "Failed")' in rail
    assert '!chatStatusLabel && (' in rail
    assert '{chatStatusLabel && (' in rail
    assert 'className="wbc-chat-card-status"' in rail
    assert "+ chatStatusTone}" in rail
    assert 'menu.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" })' in rail
    assert 'visibleGroupRailItems.length ? " has-groups" : ""' in rail
    assert 'className="wbc-chat-list-primary"' in rail
    assert "function wbcCycleTopbarSessionTab(direction)" in source
    assert "function wbcHandleHorizontalWheelGesture(event, gesture, onCycle)" in source
    assert "Math.abs(deltaX) <= Math.abs(deltaY) * 1.15" in source
    assert "Math.abs(gesture.delta) < 44" in source
    assert "gesture.lockedUntil = now + 420" in source
    assert "gesture.waitingForIdle = true" in source
    assert "idleFor < 180 || now < gesture.lockedUntil" in source
    assert "lastEventAt: 0" in source
    assert "waitingForIdle: false" in source
    page = source.split("function WorkbenchChatPage", 1)[1].split(
        "function WbcRenameDialog", 1
    )[0]
    assert "var horizontalSessionWheelRef = useWbcRef({" in page
    assert "horizontalSessionWheelGesture={horizontalSessionWheelRef.current}" in page
    assert "function handleConversationHorizontalWheel(event)" in source
    assert 'onWheel={handleConversationHorizontalWheel}' in source
    assert "horizontalSessionWheelGesture," in source
    assert "wbcCycleTopbarSessionTab" in source
    assert 'className="wbc-chat-list-group-region"' in rail
    assert rail.index('className="wbc-chat-list-primary"') < rail.index('className="wbc-chat-list-group-region"')
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
    assert ".wbc-chat-group-content.expanded:has(.wbc-chat-card.menu-open) > .wbc-chat-group-content-inner {" in styles
    assert ".wbc-rail .wbc-chat-group:has(.wbc-chat-card.menu-open) {" in styles
    assert ".wbc-chat-list.has-groups {" in styles
    assert ".wbc-rail .wbc-chat-card.status-attention .wbc-chat-card-top b" in styles
    assert ".wbc-rail .wbc-chat-card.status-failed .wbc-chat-card-top b" in styles
    assert ".wbc-rail .wbc-chat-card.status-completed .wbc-chat-row-icon" in styles
    assert ".wbc-chat-card:hover .wbc-chat-card-status," in styles
    assert ".wbc-chat-card.status-attention .wbc-chat-card-status {" in styles
    assert ".wbc-chat-card.status-failed .wbc-chat-card-status {" in styles
    group_region_css = styles.split(".wbc-chat-list-group-region {", 1)[1].split("}", 1)[0]
    recent_primary_css = styles.split(".wbc-chat-list-primary {", 1)[1].split("}", 1)[0]
    assert "width: 100%;" in recent_primary_css
    assert "max-width: 100%;" in recent_primary_css
    assert "overflow-x: hidden;" in recent_primary_css
    assert "overscroll-behavior-x: none;" in recent_primary_css
    assert "min-height: 108px;" in group_region_css
    assert "max-height: min(44%, 320px);" in group_region_css
    assert "overflow-y: auto;" in group_region_css
    expanded_group_region_css = styles.split(
        ".wbc-chat-list-group-region:has(.wbc-chat-group.expanded) {", 1
    )[1].split("}", 1)[0]
    assert "flex-shrink: 0;" in expanded_group_region_css
    assert "max-height:" not in expanded_group_region_css
    assert "padding-top: 11px;" in group_region_css
    assert "calc(100% - 16px) 1px no-repeat" in group_region_css
    assert group_region_css.count("linear-gradient(") == 1
    assert "var(--wb-floating-rail-bg)" not in group_region_css
    assert ".wbc-chat-list.has-groups.menu-active," in styles
    assert ".wbc-chat-list-group-region:has(.menu-open)" in styles
    assert "WBC_CHAT_GROUP_DRAG_MIME" in source
    assert "function wbcMoveChatOrderBlock(" in source
    assert 'dragKind: "group"' in rail
    assert 'mode: "group-reorder"' in rail
    assert 'wbcSetChatGroupDrag(event, group, projectId)' in rail
    assert 'draggable="true"' in rail.split("function renderGroupFrame", 1)[1]
    assert "commitGroupOrder(order" in rail
    assert ".wbc-chat-group.dragging {" in styles
    assert rail.count('{group.chatIds.length + (groupDropReady ? 1 : 0)}') == 1
    assert '<span className="wbc-chat-group-leading-chevron expanded" aria-hidden="true">{WBC_ICONS.chevronRight}</span>' in rail
    assert '<span className="wbc-chat-group-count">{group.chatIds.length + (groupDropReady ? 1 : 0)}</span>' in rail
    assert '<span className="wbc-chat-group-count">2</span>' in rail
    assert 'className="wbc-chat-group-icon"' not in rail
    group_children_css = styles.split(".wbc-rail .wbc-chat-group-children {", 1)[1].split("}", 1)[0]
    assert "margin-left: 0;" in group_children_css
    assert "padding: 1px 6px;" in group_children_css
    assert "border-left: 0;" in group_children_css
    drop_clone = rail.split("function renderDropClone", 1)[1].split(
        "function renderChatCard", 1
    )[0]
    assert '<span className="wbc-chat-row-icon" aria-hidden="true">{visualState.icon}</span>' in drop_clone
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
    rail_group_css = styles.split(".wbc-rail .wbc-chat-group {", 1)[1].split("}", 1)[0]
    rail_group_head_css = styles.split(".wbc-rail .wbc-chat-group-head {", 1)[1].split("}", 1)[0]
    rail_children_css = styles.split(".wbc-rail .wbc-chat-group-children {", 1)[1].split("}", 1)[0]
    rail_child_hover_css = styles.split(
        ".wbc-rail .wbc-chat-group .wbc-chat-group-child:hover,", 1
    )[1].split("}", 1)[0]
    rail_collapsed_group_css = styles.split(".wbc-rail .wbc-chat-group.collapsed {", 1)[1].split("}", 1)[0]
    assert "padding: 5px 0;" in rail_group_css
    assert "var(--wb-card-bg) 42%" in rail_group_css
    assert "gap: 0;" in rail_collapsed_group_css
    assert "padding: 0 10px 0 8px;" in rail_group_head_css
    assert ".wbc-rail .wbc-chat-group.expanded .wbc-chat-group-head:hover {" in styles
    assert ".wbc-rail .wbc-chat-group-head:hover {" not in styles
    assert "margin-left: 0;" in rail_children_css
    assert "padding: 1px 6px;" in rail_children_css
    assert "border-left: 0;" in rail_children_css
    assert "transform: none;" in rail_child_hover_css


def test_overflowing_chat_card_and_topbar_tab_text_scrolls_on_hover():
    chat_source = workbench_chat_source()
    shell_source = workbench_shell_source()
    styles = workbench_style_source()

    assert "function WbcHoverMarquee(" in chat_source
    assert '<WbcHoverMarquee text={chat.title || wbcT(' in chat_source
    assert '<WbcHoverMarquee text={chat.preview || wbcT(' in chat_source
    assert '<WbcHoverMarquee text={item.title} className="workbench-session-tab-title" />' in shell_source
    assert '<WbcHoverMarquee text={visibleStatusText}' in shell_source
    assert 'className="workbench-session-tab-title workbench-session-tab-status-copy" auto={true}' in shell_source
    assert 'metrics.overflow ? " overflow" : ""' in chat_source
    assert 'auto ? " auto" : ""' in chat_source
    assert ".wbc-hover-marquee.overflow:hover .wbc-hover-marquee-track" in styles
    assert ".wbc-hover-marquee.overflow.auto .wbc-hover-marquee-track" in styles
    assert "animation: wbc-hover-marquee" in styles
    assert "animation-timing-function: cubic-bezier(.45, 0, 1, 1);" in styles
    assert "infinite alternate" not in styles.split("@keyframes wbc-hover-marquee", 1)[0].rsplit(".wbc-hover-marquee", 1)[-1]
    marquee_keyframes = styles.split("@keyframes wbc-hover-marquee", 1)[1].split("@media", 1)[0]
    assert "88%, 100%" in marquee_keyframes
    assert "56%" not in marquee_keyframes
    assert "prefers-reduced-motion: reduce" in styles


def test_remote_desktop_topbar_tab_uses_a_dedicated_monitor_icon():
    source = frontend_module_source("features/shell/topbar.jsx")

    icon = source.split("function WorkbenchTabKindIcon", 1)[1].split(
        "function WorkbenchSessionMenuFileName", 1
    )[0]
    assert "payload.packId || payload.pack_id" in icon
    assert 'pluginPackId === "cyrene_remote_desktop"' in icon
    assert '<rect x="1.5" y="2.1" width="13" height="9.2" rx="1.8"/>' in icon
    assert '<path d="M5 14h6M8 11.3V14"/>' in icon
    assert source.count("<WorkbenchTabKindIcon item={item} />") == 2
    assert "<WorkbenchTabKindIcon item={sessionMenuCurrentItem} />" in source


def test_remote_desktop_rail_uses_dedicated_vector_icons():
    icons = frontend_module_source("features/chat/icons.jsx")
    rail = frontend_module_source("features/chat/rail.jsx")

    assert "remoteDesktop: <svg" in icons
    assert 'd="m9 7.5 6.2 5.3-3 .6-1.4 2.7Z"' in icons
    assert "remoteDevice: <svg" in icons
    assert 'd="M8 20.5h8M12 16.5v4"' in icons
    assert 'M11.95 13.2' not in icons
    assert "item.icon_name || item.iconName" in rail
    assert "itemIconName && WBC_ICONS[itemIconName]" in rail


def test_remote_desktop_cards_reuse_the_terminal_resource_card_style():
    rail = frontend_module_source("features/chat/rail.jsx")
    styles = workbench_style_source()

    terminal_card = rail.split("function renderTerminalCard(terminal)", 1)[1].split(
        "function renderTerminalSection", 1
    )[0]
    plugin_cards = rail.split("function renderPluginCollectionItems", 1)[1].split(
        "function renderIntegratedPluginTool", 1
    )[0]
    assert "wbc-project-resource-card" in terminal_card
    assert "wbc-project-resource-card" in plugin_cards
    assert rail.count("wbc-plugin-tool-collection-items wbc-project-resource-list") == 2
    assert "wbc-project-terminal-list wbc-project-resource-list" in rail
    assert '<span className="wbc-chat-card-preview"><WbcHoverMarquee text={itemSubtitle} /></span>' in plugin_cards
    assert ".wbc-project-resource-list .wbc-project-resource-card {" in styles
    assert ".wbc-project-terminal-list .wbc-terminal-card {" not in styles
    online = styles.split(
        ".wbc-plugin-collection-card.is-online .wbc-plugin-collection-status {", 1
    )[1].split("}", 1)[0]
    assert "var(--wb-green)" in online


def test_chat_sidebar_context_is_flat_and_overview_is_integrated():
    source = workbench_chat_source()
    styles = workbench_style_source()

    assert 'className="wbc-overview-compact"' in source
    assert 'className="workbench-side-section wbc-overview-session"' in source
    assert '<WbcContextUsage data={liveData} compact={true} />' in source
    overview_source = source.split("function WbcOverviewTab(", 1)[1].split(
        "function wbcBlockLabel(", 1
    )[0]
    assert '<WbcOverviewUsage usage={usage} latestUsage={chat.latestUsage} />' in overview_source
    assert "WbcQuickActionItems" not in overview_source
    assert "wbc-overview-actions" not in overview_source
    context_source = source.split("function WbcContextTab(", 1)[1].split(
        "function WbcArtifactsTab(", 1
    )[0]
    assert 'className="wbc-context-sections"' in context_source
    assert '<WbcContextBlockList data={contextBlocks} compact={false} />' in context_source
    assert 'className: "wbc-context-detail"' in source
    assert '<WbcInboxCard liveView={inboxView} hideTitle={true} />' in context_source
    assert "usedPluginPacks.length === 0" in context_source
    assert "workbenchChat.noUsedPluginPacks" in context_source
    plugin_pack_heading = context_source.index('className="wbc-context-empty-head wbc-plugin-pack-head"')
    plugin_pack_condition = context_source.index("usedPluginPacks.length === 0 ? (")
    assert plugin_pack_heading < plugin_pack_condition
    assert 'className="wbc-plugin-pack-list"' in context_source
    assert "wbcLocalizedUsedPluginName(packId, pluginSnapshot)" in context_source
    assert "PluginFrontendService.subscribe(setPluginSnapshot)" in context_source
    assert 'pluginLocalizedField(descriptor, "name")' in source
    assert 'className="workbench-side-section wbc-context-stats"' in context_source
    assert 'className="wbc-context-facts"' in context_source
    assert "WbcSortableCardStack" not in context_source
    context_css = styles.split(".wbc-context-sections {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column;" in context_css
    assert ".wbc-plugin-pack-list {" in styles
    assert ".wbc-context-facts {" in styles
    context_facts_css = styles.split(".wbc-context-facts {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: minmax(0, 1fr);" in context_facts_css
    assert "border: 0;" in context_facts_css
    assert "background: transparent;" in context_facts_css
    plugin_pack_row_css = styles.split(
        ".wbc-side-body .wbc-plugin-pack-list .wbc-plugin-pack-row {",
        1,
    )[1].split("}", 1)[0]
    assert "display: flex;" in plugin_pack_row_css
    assert "justify-content: space-between;" in plugin_pack_row_css


def test_chat_overview_cache_rate_only_uses_latest_request():
    source = workbench_chat_source()
    usage = source.split("function WbcOverviewUsage(", 1)[1].split(
        "function WbcQuickActionItems(", 1
    )[0]

    assert 'wbcCacheRateLabel(latestHit, latestMiss)' in usage
    assert 'wbcT("workbenchChat.cacheHitRate", "Cache hit rate")' in usage
    assert 'cacheHitRateLatest' not in usage
    assert 'cacheHitRateLatestTotal' not in usage
    assert 'totalRateLabel' not in usage
    assert 'latestRateLabel + " / "' not in usage
    assert '<div className="wbc-overview-token-grid">' in usage


def test_builtin_agent_in_overview_uses_plain_text_without_builtin_suffix():
    source = workbench_chat_source()
    overview = source.split("function WbcOverviewTab(", 1)[1].split(
        "function wbcBlockLabel(", 1
    )[0]
    builtin = overview.split("{overviewHasAgent && overviewIsBuiltin && (", 1)[1].split(
        "          )}\n          {!overviewExternal", 1
    )[0]

    assert "<b>{overviewAgentName}</b>" in builtin
    assert "wbc-overview-agent-name" not in builtin
    assert "agentBuiltinSuffix" not in builtin


def test_workbench_chat_cards_are_borderless_and_compact_overview_is_flat():
    styles = workbench_style_source()

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
    dark_page_css = styles.split(
        'html[data-theme="dark"] .wbc-page {', 1
    )[1].split("}", 1)[0]
    rail_hover_css = styles.split(
        ".wbc-rail .wbc-chat-card:hover,", 1
    )[1].split("}", 1)[0]
    rail_active_hover_css = styles.split(
        ".wbc-rail .wbc-chat-card.active:hover {", 1
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
    assert "--wbc-panel-surface: #17181c;" in dark_page_css
    assert "--wb-floating-rail-bg: var(--wbc-panel-surface);" in dark_page_css
    assert "var(--wb-accent) 7%" in rail_hover_css
    assert "var(--wb-active-bg) 92%" in rail_active_hover_css


def test_workbench_chat_search_and_custom_background_composer_stay_distinct():
    styles = workbench_style_source()

    search_css = styles.split(".wbc-search input {", 1)[1].split("}", 1)[0]
    search_focus_css = styles.split(".wbc-search input:focus {", 1)[1].split("}", 1)[0]
    shell_css = styles.split(".workbench-shell {", 1)[1].split("}", 1)[0]
    composer_css = styles.split(".wbc-composer-box {", 1)[1].split("}", 1)[0]
    assert "border: 1px solid" in search_css
    assert "box-shadow: none;" in search_css
    assert "border-color:" in search_focus_css
    assert "--wbc-composer-glass-border: 1px solid color-mix(in srgb, var(--wb-line-2) 64%, transparent);" in composer_css
    assert "--wbc-composer-glass-background: color-mix(in srgb, var(--wb-card-bg) 72%, transparent);" in composer_css
    assert "--wbc-composer-glass-filter: blur(18px) saturate(120%) contrast(102%);" in shell_css
    assert "border: var(--wbc-composer-glass-border);" in composer_css
    assert "background: var(--wbc-composer-glass-background);" in composer_css
    assert "backdrop-filter: var(--wbc-composer-glass-filter);" in composer_css
    assert ".wbc-composer-box:focus-within {" not in styles


def test_main_chat_composer_uses_a_solid_canvas_and_readable_input_card():
    styles = workbench_style_source()
    source = workbench_chat_source()

    stage_css = styles.split("\n.wbc-thread-stage {", 1)[1].split("}", 1)[0]
    dock_css = styles.split("\n.wbc-main > .wbc-composer {", 1)[1].split("}", 1)[0]
    page_css = styles.split(".wbc-page {", 1)[1].split("}", 1)[0]
    topbar_css = styles.split(".workbench-topbar {", 1)[1].split("}", 1)[0]
    input_css = styles.split(".wbc-composer-box {", 1)[1].split("}", 1)[0]
    scroll_css = styles.split(".wbc-scroll-to-bottom {", 1)[1].split("}", 1)[0]
    scroll_hover_css = styles.split(".wbc-scroll-to-bottom:hover {", 1)[1].split(
        "}", 1
    )[0]

    assert (
        "--wbc-thread-inset-bottom: calc(var(--wbc-composer-reserve-height) + "
        "34px * var(--wb-ui-font-scale, 1));"
    ) in stage_css
    assert "position: absolute;" in dock_css
    assert "inset: auto 0 0;" in dock_css
    assert "background:" not in dock_css
    assert "background: var(--wb-main-bg);" in page_css
    assert "background: var(--wb-main-bg);" in topbar_css
    assert "-webkit-backdrop-filter: none;" in topbar_css
    assert "backdrop-filter: none;" in topbar_css
    assert ".wbc-page::before {" not in styles
    assert ".wbc-page::after {" not in styles
    assert ".wbc-glass-junction {" not in styles
    assert 'className="wbc-glass-junction"' not in source
    assert ".wbc-main > .wbc-composer::before {" not in styles
    assert "background: var(--wbc-composer-glass-background);" in input_css
    assert "border: var(--wbc-composer-glass-border);" in input_css
    assert "backdrop-filter: var(--wbc-composer-glass-filter);" in input_css
    assert "border-radius: 14px;" in input_css
    assert "padding: 10px 12px 8px;" in input_css
    assert "bottom: calc(100% + 8px);" in scroll_css
    assert "border: var(--wbc-composer-glass-border);" in scroll_css
    assert "background: var(--wbc-composer-glass-background);" in scroll_css
    assert "box-shadow: var(--wbc-composer-glass-shadow);" in scroll_css
    assert "backdrop-filter: var(--wbc-composer-glass-filter);" in scroll_css
    assert "transition: color 120ms ease, transform 120ms ease;" in scroll_css
    assert "color: var(--wb-text);" in scroll_hover_css
    assert "var(--wb-accent)" not in scroll_hover_css
    assert "border-color:" not in scroll_hover_css
    assert "background:" not in scroll_hover_css
    assert "position: relative;" in input_css
    assert "topOverlay={showScrollToBottom ? (" in source
    assert "{topOverlay}" in source


def test_split_chat_composers_align_with_the_floating_workspace_rail():
    styles = workbench_style_source()

    rail_css = styles.split(
        ".workbench-grid.integrated-sidebars .wbc-rail.workbench-integrated-rail {", 1
    )[1].split("}", 1)[0]
    composer_css = styles.split(
        ".workbench-grid.integrated-sidebars.is-chat .wbc-composer {", 1
    )[1].split("}", 1)[0]
    split_main_composer_css = styles.split(
        ".workbench-shell .wbc-page.side-agent-split-open > .wbc-main > .wbc-composer {",
        1,
    )[1].split("}", 1)[0]
    pane_split_main_composer_css = styles.split(
        ".workbench-shell .wbc-pane-layout.split .wbc-pane-card .wbc-main > .wbc-composer {",
        1,
    )[1].split("}", 1)[0]
    main_composer_css = styles.split(
        ".workbench-shell .wbc-main > .wbc-composer {", 1
    )[1].split("}", 1)[0]

    assert "width:" not in rail_css
    assert "max-width:" not in rail_css
    assert "justify-self:" not in rail_css
    assert (
        "margin: var(--wbc-card-top-inset) 4px "
        "var(--wbc-card-gutter) var(--wbc-card-gutter);" in rail_css
    )
    assert "padding-bottom: 12px;" in composer_css
    for scoped_composer_css in (
        main_composer_css,
        split_main_composer_css,
        pane_split_main_composer_css,
    ):
        assert "var(--wbc-composer-top-inset)" in scoped_composer_css
        assert scoped_composer_css.count("var(--wbc-composer-edge-inset)") == 2
    assert (
        'html[data-density="compact"] .workbench-shell '
        ".workbench-composer.wbc-composer"
    ) not in styles


def test_hidden_chat_sidebar_slightly_widens_and_centers_the_conversation_lane():
    styles = workbench_style_source()

    page_css = styles.split(".wbc-page {", 1)[1].split("}", 1)[0]
    hidden_css = styles.split(".wbc-page.wbc-side-hidden {", 1)[1].split("}", 1)[0]
    stage_css = styles.split("\n.wbc-thread-stage {", 1)[1].split("}", 1)[0]
    dock_css = styles.split("\n.wbc-main > .wbc-composer {", 1)[1].split("}", 1)[0]

    assert "--wbc-reclaimed-side-width: 0px;" in page_css
    assert "--wbc-conversation-shift: 0px;" in page_css
    assert "--wbc-docked-side-open-width: var(--wb-right-w, 350px);" in page_css
    assert "--wbc-side-track-width: var(--wbc-docked-side-open-width);" in page_css
    assert "grid-template-columns: var(--wbc-rail-width) 0px minmax(var(--wbc-main-min-width), 1fr) var(--wbc-side-track-width);" in page_css
    assert "transition: grid-template-columns 500ms cubic-bezier(.22, 1.16, .36, 1);" in page_css
    assert "--wbc-collapsed-lane-growth: clamp(144px, 12vw, 208px);" in page_css
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
    hidden_side_css = styles.split(
        ".wbc-page.wbc-side-hidden > :is(.wbc-side) {",
        1,
    )[1].split("}", 1)[0]
    assert "display: none;" not in hidden_side_css
    assert "opacity: 0;" in hidden_side_css
    assert "visibility: hidden;" in hidden_side_css
    assert "@media (prefers-reduced-motion: reduce)" in styles


def test_workbench_chat_rails_use_hidden_scrollbars():
    styles = workbench_style_source()

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


def test_global_topbar_is_solid_and_conversation_header_panel_is_removed():
    styles = workbench_style_source()
    chat = workbench_chat_source()
    topbar_css = styles.split(".workbench-topbar {", 1)[1].split("}", 1)[0]
    thread_stage_css = styles.split("\n.wbc-thread-stage {", 1)[1].split("}", 1)[0]
    main = chat.split("function WbcMain(", 1)[1].split("function WbcHeader(", 1)[0]

    assert "background: var(--wb-main-bg);" in topbar_css
    assert "-webkit-backdrop-filter: none;" in topbar_css
    assert "backdrop-filter: none;" in topbar_css
    assert "border-bottom: 0;" in topbar_css
    assert '<WbcHeader' not in main
    assert 'className="wbc-header"' not in main
    assert 'className="wbc-top-glass"' not in chat
    assert "position: fixed;" in topbar_css
    assert "inset: 0 0 auto;" in topbar_css
    assert "--wbc-thread-inset-top: 76px;" in thread_stage_css


def test_workbench_chat_rail_uses_the_shared_physical_card_and_fixed_header():
    styles = workbench_style_source()
    source = workbench_chat_source()

    rail_css = styles.split("\n.wbc-rail {", 1)[1].split("}", 1)[0]
    rail_glass_css = styles.split(".wbc-rail-glass {", 1)[1].split("}", 1)[0]
    rail_glass_surface_css = styles.split(".wbc-rail-glass::before {", 1)[1].split("}", 1)[0]
    nav_head_css = styles.split(".wbc-nav-card-head {", 1)[1].split("}", 1)[0]
    new_chat_css = styles.split(".wbc-project-new-chat {", 1)[1].split("}", 1)[0]
    search_input_css = styles.split(".wbc-search input {", 1)[1].split("}", 1)[0]
    shared_search_head_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail-search-head {", 1
    )[1].split("}", 1)[0]
    collapse_control_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-sidebar-collapse-control {", 1
    )[1].split("}", 1)[0]
    chat_list_css = styles.split(".wbc-chat-list {", 1)[1].split("}", 1)[0]

    assert 'className="wbc-rail-glass"' in source
    assert 'className="wbc-top-glass"' not in source
    assert "--wbc-rail-overlay-height: 56px;" in rail_css
    assert "--wbc-rail-content-inset: calc(var(--wbc-rail-overlay-height) + 6px);" in rail_css
    assert "inset: var(--wbc-card-top-inset) 8px auto;" in rail_glass_css
    assert "padding: 0;" in rail_glass_css
    assert "background: transparent;" in rail_css
    assert "padding: 0 12px;" in rail_css
    assert "padding: 0 12px 14px;" not in rail_css
    assert "z-index: 21;" in rail_css
    assert ".wbc-page::before {" not in styles
    assert ".wbc-page::after {" not in styles
    assert ".wbc-glass-junction {" not in styles
    assert "isolation: isolate;" in rail_css
    assert "background: transparent;" in rail_glass_css
    assert "content: none;" in rail_glass_surface_css
    assert 'className={"wbc-rail workbench-integrated-rail"' in source
    assert '+ (collapsed ? " is-collapsed" : "")' in source
    assert '+ (renderedRailMotionPhase ? (" is-status-" + renderedRailMotionPhase) : "")' in source
    shared_chat_rail_css = styles.split(
        ".workbench-grid.integrated-sidebars .wbc-rail.workbench-integrated-rail {", 1
    )[1].split("}", 1)[0]
    assert "height: calc(100% - var(--wbc-card-top-inset) - var(--wbc-card-gutter));" in shared_chat_rail_css
    assert "max-height: calc(100% - var(--wbc-card-top-inset) - var(--wbc-card-gutter));" in shared_chat_rail_css
    assert "width:" not in shared_chat_rail_css
    assert "max-width:" not in shared_chat_rail_css
    assert "justify-self:" not in shared_chat_rail_css
    assert (
        "margin: var(--wbc-card-top-inset) 4px "
        "var(--wbc-card-gutter) var(--wbc-card-gutter);" in shared_chat_rail_css
    )
    assert "padding: 0;" in shared_chat_rail_css
    shared_chat_surface_css = styles.split(
        ".workbench-grid.integrated-sidebars .wbc-rail.workbench-integrated-rail::before {", 1
    )[1].split("}", 1)[0]
    assert "content: none;" in shared_chat_surface_css
    shared_chat_glass_css = styles.split(
        ".workbench-grid.integrated-sidebars .wbc-rail.workbench-integrated-rail .wbc-rail-glass {", 1
    )[1].split("}", 1)[0]
    assert "position: relative;" in shared_chat_glass_css
    assert "inset: auto;" in shared_chat_glass_css
    assert "height: 56px;" in shared_chat_glass_css
    assert "display: flex;" in nav_head_css
    assert "align-items: center;" in nav_head_css
    assert "gap: 6px;" in nav_head_css
    assert "--wb-rail-toolbar-control-size: 32px;" in shared_search_head_css
    assert "width: var(--wb-rail-toolbar-control-size, 32px);" in new_chat_css
    assert "height: var(--wb-rail-toolbar-control-size, 32px);" in new_chat_css
    assert "width: var(--wb-rail-toolbar-control-size, 32px);" in collapse_control_css
    assert "height: var(--wb-rail-toolbar-control-size, 32px);" in collapse_control_css
    assert ".workbench-rail-mode-toggle" not in styles
    assert "flex: 1 1 auto;" in styles.split(".wbc-search {", 1)[1].split("}", 1)[0]
    assert 'className="workbench-rail-mode-toggle"' not in source
    assert "function toggleRailMode()" not in source
    assert "workRailMode" not in source
    assert "onRailModeChange" not in source
    assert 'className="wbc-project-new-chat"' in source
    assert 'className="wbc-chat-nav-title"' not in source
    assert 'className="wbc-rail-filter"' not in source
    assert 'className="wbc-rail-toolbar"' not in source
    assert 'className="wbc-module-nav"' not in source
    assert 'aria-label={wbcT("workbenchChat.newChat"' in source
    rail_markup = source.split('<div className="wbc-rail-glass">', 1)[1].split(
        "{menuId &&", 1
    )[0]
    assert rail_markup.index('data-cyrene-node-id="new_chat"') < rail_markup.index(
        'data-cyrene-node-id="chat_search_input"'
    )
    assert 'workbenchChat.railTitle' not in rail_markup
    assert '<span>{wbcT("workbenchChat.newChat"' not in rail_markup
    assert "height: var(--wb-rail-toolbar-control-size, 32px);" in search_input_css
    assert "border-right: 0;" in rail_css
    assert ".wbc-rail::after {" not in styles
    assert "position: relative;" in chat_list_css
    assert "z-index: 21;" in chat_list_css
    assert "padding: calc(var(--wbc-card-top-inset) + var(--wbc-rail-content-inset)) 10px 18px;" in chat_list_css


def test_collapsed_right_sidebar_restore_control_lives_in_the_global_topbar():
    chat = workbench_chat_source()
    source = workbench_shell_source()
    styles = workbench_style_source()

    topbar = source.split("function WorkbenchTopbar(", 1)[1].split(
        "function WorkbenchNotificationCenter(", 1
    )[0]
    assert 'className="workbench-icon-btn"' in topbar
    assert 'data-cyrene-node-id="open_settings"' not in topbar
    assert 'data-chat-side-show="true"' in topbar
    assert 'new CustomEvent("workbench:show-chat-side")' in topbar
    assert 'window.addEventListener("workbench:chat-side-visibility"' in topbar
    assert 'window.addEventListener("workbench:show-chat-side"' in chat
    assert 'new CustomEvent("workbench:chat-side-visibility"' in chat

    assert ".workbench-chat-side-show {" not in styles


def test_workbench_chat_sidebar_is_a_top_aligned_floating_accordion():
    styles = workbench_style_source()
    source = workbench_chat_source()

    right_panel_styles = styles.split("/* ---- right panel ---- */", 1)[1]
    side_css = right_panel_styles.split(".wbc-side {", 1)[1].split("}", 1)[0]
    card_css = styles.split("\n.wbc-side-card {", 1)[1].split("}", 1)[0]
    surface_css = styles.split(".wbc-panel-accordion-surface {", 1)[1].split("}", 1)[0]
    accordion_css = styles.split(".wbc-side-accordion {", 1)[1].split("}", 1)[0]
    side_body_css = styles.split(".wbc-side-body {", 1)[1].split("}", 1)[0]
    flush_css = styles.split(".wbc-side-body.flush {", 1)[1].split("}", 1)[0]

    assert '<WbcPanelAccordionSurface className="wbc-side-card">' in source
    assert '<WbcPanelAccordionList className="wbc-side-accordion" dataTour="chat_sidebar">' in source
    assert 'className={"wbc-side-accordion-trigger"' in source
    assert 'aria-expanded={expandable ? (expanded ? "true" : "false") : undefined}' in source
    assert 'var [sideTab, setSideTab] = useWbcState("");' in source
    assert 'var activeTab = tabs.some(function (item) { return item.id === tab; }) ? tab : "";' in source
    assert 'onTabChange(expanded ? "" : item.id)' in source
    assert "padding: var(--wbc-card-top-inset) var(--wbc-card-gutter) var(--wbc-card-gutter);" in side_css
    assert "background: transparent;" in side_css
    assert "border-radius: 18px;" in surface_css
    assert "backdrop-filter: blur(18px) saturate(112%);" in surface_css
    assert "overflow-y: auto;" in accordion_css
    assert "max-height: min(620px, calc(100vh - 250px));" in side_body_css
    assert "padding: 4px 16px 12px;" in side_body_css
    assert "padding: 0;" in flush_css


def test_workbench_chat_primary_cards_share_an_opaque_dark_surface():
    styles = workbench_style_source()

    page_css = styles.split(".wbc-page {", 1)[1].split("}", 1)[0]
    dark_page_css = styles.split('html[data-theme="dark"] .wbc-page {', 1)[1].split("}", 1)[0]
    rail_card_css = styles.split(".wbc-rail::before {", 1)[1].split("}", 1)[0]
    pane_card_css = styles.split(".wbc-pane-card {", 1)[1].split("}", 1)[0]
    side_card_css = styles.split("\n.wbc-side-card {", 1)[1].split("}", 1)[0]
    panel_surface_css = styles.split(".wbc-panel-accordion-surface {", 1)[1].split("}", 1)[0]
    dark_side_card_css = styles.split(
        'html[data-theme="dark"] .wbc-page .wbc-panel-accordion-surface {', 1
    )[1].split("}", 1)[0]

    assert "--wbc-panel-surface: var(--wb-floating-rail-bg);" in page_css
    assert "--wbc-panel-surface: #17181c;" in dark_page_css
    assert "background: var(--wbc-panel-surface);" in rail_card_css
    assert "background: var(--wbc-panel-surface);" in pane_card_css
    assert "background: var(--wbc-panel-surface);" in panel_surface_css
    assert "background: var(--wbc-panel-surface);" in dark_side_card_css
    assert styles.index('html[data-theme="dark"] .wbc-page .wbc-panel-accordion-surface {') > styles.index(
        'html[data-theme="dark"] :is(.wbc-composer-box, .wbc-side-card) {'
    )


def test_workbench_chat_sidebar_expanded_lists_share_a_responsive_content_system():
    styles = workbench_style_source()

    trigger_css = styles.split(".wbc-side-accordion-trigger {", 1)[1].split("}", 1)[0]
    card_css = styles.split("\n.wbc-side-card {", 1)[1].split("}", 1)[0]
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
    source = workbench_chat_source()

    icon_source = source.split("var WBC_SIDE_TAB_ICONS = {", 1)[1].split("\n};", 1)[0]
    for tab_id in [
        "overview", "plan", "subagents", "context", "artifacts", "changes",
        "branches", "viewer", "map", "browser", '"side-agents"',
    ]:
        assert f"{tab_id}: <svg" in icon_source
    assert 'strokeWidth="1.7"' in icon_source
    assert "WBC_SIDE_TAB_ICONS[item.id]" in source


def test_right_panel_maximum_uses_only_the_visible_layout_width():
    source = frontend_module_source("features/layout/right-panel-resizer.jsx")
    helper = "function wbVisibleLayoutWidth" + source.split(
        "function wbVisibleLayoutWidth", 1
    )[1].split("// Largest the right panel", 1)[0]
    script = f"""
global.window = {{ innerWidth: 1200 }};
eval({json.dumps(helper)});
function layout(left, right) {{
  return {{ getBoundingClientRect: () => ({{ left, right, width: right - left }}) }};
}}
process.stdout.write(JSON.stringify([
  wbVisibleLayoutWidth(layout(64, 1200)),
  wbVisibleLayoutWidth(layout(64, 1500)),
  wbVisibleLayoutWidth(layout(-80, 1150)),
  wbVisibleLayoutWidth(layout(1300, 1500)),
]));
"""
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == [1136, 1136, 1150, 0]


def test_right_panel_maximum_reserves_the_collapsed_rail_grid_track():
    source = frontend_module_source("features/layout/right-panel-resizer.jsx")
    helpers = "var WB_RIGHT_MIN" + source.split("var WB_RIGHT_MIN", 1)[1].split(
        "function wbApplyStoredRightWidth", 1
    )[0]
    script = f"""
global.window = {{
  innerWidth: 1814,
  getComputedStyle: () => ({{ getPropertyValue: () => "380px" }}),
}};
eval({json.dumps(helpers)});
const classList = (name) => ({{ contains: (candidate) => candidate === name }});
const rail = {{
  classList: classList("wbc-rail"),
  getBoundingClientRect: () => ({{ left: 12, right: 60, width: 48 }}),
}};
const pane = {{
  classList: classList("wbc-pane-layout"),
  getBoundingClientRect: () => ({{ left: 64, right: 1238, width: 1174 }}),
}};
const layout = {{
  children: [rail, pane],
  classList: classList("wbc-page"),
  getBoundingClientRect: () => ({{ left: 0, right: 1814, width: 1814 }}),
}};
const panel = {{ closest: (selector) => selector === ".wbc-page" ? layout : null }};
process.stdout.write(String(wbRightDynamicMax(panel)));
"""
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    assert result.stdout == "1370"


def test_workbench_chat_single_card_uses_independent_hidden_gutter_resize_handles():
    chat = workbench_chat_source()
    shell = workbench_shell_source()
    styles = workbench_style_source()

    side = chat.split("function WbcSide(", 1)[1].split("function WbcChangesTab", 1)[0]
    browser = chat.split("function WbcBrowserFloatingSurface", 1)[1].split("function WbcMain", 1)[0]
    card_start = side.index('<div className="wbc-side-card">')
    resizer = 'trackGutter: true'
    assert resizer in side
    assert card_start < side.index(resizer)
    assert resizer in browser
    assert 'surfaceId: "conversation"' in side
    assert 'surfaceId: "browser"' in browser
    assert "cardEdge: true" not in side
    assert "widthResizable={!floating && paneCardCount === 1}" in chat
    assert "function WbColResizer({ cardEdge, trackGutter, surfaceId })" in shell
    assert 'page.querySelector(":scope > .wbc-side")' in shell
    dynamic_max = shell.split("function wbRightDynamicMax(panel)", 1)[1].split(
        "function wbApplyStoredRightWidth", 1
    )[0]
    assert 'layout.classList.contains("wbc-page")' in dynamic_max
    assert 'child.classList.contains("wbc-rail")' in dynamic_max
    assert 'child.classList.contains("wbc-pane-layout")' in dynamic_max
    assert "pane.getBoundingClientRect().left - layoutRect.left" in dynamic_max
    assert 'getPropertyValue("--wbc-main-min-width")' in dynamic_max
    assert "var avail = wbVisibleLayoutWidth(layout);" in dynamic_max
    assert 'node.querySelector(".wbc-page > .wbc-side")' in shell
    assert '(trackGutter ? " track-gutter" : "")' in shell
    assert "if (cardEdge || trackGutter) return;" in shell
    gutter_css = styles.split(
        ".wbc-side-card > .wb-col-resizer.track-gutter,", 1
    )[1].split("}", 1)[0]
    assert ".wbc-browser-window.pip > .wb-col-resizer.track-gutter" in gutter_css
    assert "top: 0;" in gutter_css
    assert "bottom: 0;" in gutter_css
    assert "left: -12px;" in gutter_css
    assert "width: 12px;" in gutter_css
    handle_visual_css = styles.split(
        ".wbc-side-card > .wb-col-resizer.track-gutter::after,", 1
    )[1].split("}", 1)[0]
    assert "opacity: 0;" in handle_visual_css
    assert "height: 72px;" in handle_visual_css
    assert ".wb-col-resizer.track-gutter:hover::after" in styles
    assert ".wb-col-resizer.track-gutter:not(.is-resizing)::after" in styles
    assert "body.wb-col-resizing .wbc-page {\n  transition: none;\n}" in styles
    page_css = styles.split(".wbc-page {", 1)[1].split("}", 1)[0]
    rail_card_css = styles.split(".wbc-rail::before {", 1)[1].split("}", 1)[0]
    pane_layout_css = styles.split("\n.wbc-pane-layout {", 1)[1].split("}", 1)[0]
    side_css = styles.split("/* ---- right panel ---- */", 1)[1].split(
        ".wbc-side {", 1
    )[1].split("}", 1)[0]
    assert "--wbc-card-gutter: 12px;" in page_css
    assert "--wbc-topbar-clearance: 58px;" in page_css
    assert "--wbc-topbar-tab-bottom: 45px;" in page_css
    assert "--wbc-card-top-inset: max(" in page_css
    assert "var(--wbc-topbar-clearance)," in page_css
    assert "calc(var(--wbc-topbar-tab-bottom) + var(--wbc-card-gutter))" in page_css
    compact_page_css = styles.split('html[data-density="compact"] .wbc-page {', 1)[1].split("}", 1)[0]
    assert "--wbc-topbar-clearance: 50px;" in compact_page_css
    assert "--wbc-topbar-tab-bottom: 39px;" in compact_page_css
    assert "inset: var(--wbc-card-top-inset) var(--wbc-card-gutter) var(--wbc-card-gutter);" in rail_card_css
    assert (
        "padding: var(--wbc-card-top-inset) 0 var(--wbc-card-gutter) "
        "calc(var(--wbc-card-gutter) - 4px);" in pane_layout_css
    )
    assert "padding: var(--wbc-card-top-inset) var(--wbc-card-gutter) var(--wbc-card-gutter);" in side_css


def test_workbench_deleted_chat_closes_every_split_reference():
    source = workbench_chat_source()
    close_deleted = frontend_module_source("features/chat/pane-layout-controller.jsx").split(
        "function wbcCloseDeletedChatSplits(context, chatId) {", 1
    )[1].split(
        "\nfunction wbcMovePaneCardOtherSide", 1
    )[0]
    assert "context.setPaneLayoutsByChat(function (current)" in close_deleted
    assert 'card.kind === "chat" && String(card.payload || "") === deletedChatId' in close_deleted
    assert "if (!left.length && right.length)" in close_deleted
    assert "context.setResourceSplitByChat(function (current)" in close_deleted
    assert 'resource.type === "chat" && String(resource.payload || "") === deletedChatId' in close_deleted
    assert "delete context.paneLayoutRestoreRef.current[cardId]" in close_deleted
    delete_success = source.split("model.deleteChat(chatId).then(function () {", 1)[1].split(
        "}).catch(function (err)", 1
    )[0]
    assert "closeDeletedChatSplits(chatId);" in delete_success


def test_workbench_pane_drag_ghost_preserves_current_viewport_and_handle_hotspot():
    source = workbench_chat_source()
    pane_drag = frontend_module_source("features/chat/pane-card-drag-controller.jsx")
    styles = workbench_style_source()
    assert "var conversationViewport = wbcCaptureConversationViewport(card);" in pane_drag
    assert "var clonedCard = wbcClonePaneWithLiveState(card);" in pane_drag
    assert "var preview = clonedCard.clone;" in pane_drag
    assert 'preview.classList.add("wbc-pane-card-drag-surface")' in pane_drag
    assert 'ghost.className = "wbc-pane-card-drag-ghost";' in pane_drag
    assert "clonedCard.restoreViewport();" in pane_drag
    assert "wbcRestoreConversationViewport(preview, conversationViewport);" in pane_drag
    assert "var handleGrabX" in pane_drag
    assert "var handleGrabY" in pane_drag
    assert "Number(handle.dataset.wbcDragClientX)" in pane_drag
    assert "Number(handle.dataset.wbcDragHandleX)" in pane_drag
    assert "wbcMovePaneCardGhost(session, event);" in pane_drag
    assert "session.grabX = (session.handleRect.left - session.cardRect.left) + handleGrabX;" in pane_drag
    assert "session.grabY = (session.handleRect.top - session.cardRect.top) + handleGrabY;" in pane_drag
    assert 'session.ghost.style.transform = "translate3d("' in pane_drag
    assert 'session.ghost.classList.add("releasing")' in pane_drag
    assert "function wbcRetirePaneCardGhost(session)" in pane_drag
    assert 'session.ghost.style.visibility = "hidden";' in pane_drag
    assert "typeof session.ghost.animate === \"function\"" in pane_drag
    assert "Promise.resolve(fade.finished)" in pane_drag
    assert "window.requestIdleCallback(detachGhost, { timeout: 300 });" in pane_drag
    assert 'wbcBuildRailCardDragPreview(railCard, "wbc-pane-card-rail-drag-card")' in pane_drag
    assert 'event.dataTransfer.dropEffect = "move";' in pane_drag
    assert 'event.preventDefault();' in pane_drag
    assert "bridge.updateDrag" in pane_drag
    assert 'session.ghost.classList.toggle("rail-card", overRail);' in pane_drag
    assert 'session.railCard.classList.toggle("dragging", overRail);' in pane_drag
    assert "wbcPaneDragPointIn(session.railCard" in pane_drag
    assert 'session.railCard.classList.toggle("wbc-split-return-target", overMatchingCard);' in pane_drag
    assert 'session.railPreview.style.left = (session.grabX - railGrabX) + "px";' in pane_drag
    assert 'ghost.style.width = (overRail ? railPreviewWidth' not in pane_drag
    assert "function wbcFinishPaneCardGhost(session, event)" in pane_drag
    assert "if (droppedOnMatchingCard && session.draggedChatId)" in pane_drag
    assert "session.context.placeExistingPaneCard(" in pane_drag
    assert "wbcHideNativeDragImage(transfer);" not in pane_drag
    assert "event.dataTransfer.setDragImage(" not in pane_drag
    assert ".wbc-pane-card-drag-ghost" in styles
    assert "will-change: transform, opacity;" in styles
    ghost_css = styles.split(".wbc-pane-card-drag-ghost {", 1)[1].split("}", 1)[0]
    assert "opacity 72ms cubic-bezier(.4, 0, 1, 1)" in ghost_css
    assert ".wbc-pane-card-drag-ghost.rail-card" in styles
    assert ".wbc-pane-card-rail-drag-card" in styles
    assert ".wbc-pane-card-drag-surface" in styles
    ghost_composer_css = styles.split(
        ".wbc-pane-card-drag-surface .wbc-main > .wbc-composer {", 1
    )[1].split("}", 1)[0]
    assert "var(--wbc-composer-top-inset)" in ghost_composer_css
    assert ghost_composer_css.count("var(--wbc-composer-edge-inset)") == 2
    assert "!important" in ghost_composer_css
    assert ".wbc-rail .wbc-chat-card.wbc-split-return-target" in styles
    grip = source.split("function WbcSplitGripBar(", 1)[1].split("function WbcSplitPickerMenu", 1)[0]
    assert "function captureDragPointer(event)" in grip
    assert "onPointerDown={captureDragPointer}" in grip
    capture = grip.split("function captureDragPointer(event)", 1)[1].split(
        "function trackDragPointer", 1
    )[0]
    tracking = grip.split("function trackDragPointer(event)", 1)[1].split(
        "function releaseDragPointer", 1
    )[0]
    assert "onSplitPointerDown" not in capture
    assert "!drag.moved" in tracking
    assert "onSplitDragStart(event, dragSource)" in tracking
    assert "onSplitPointerDown(event, dragSource)" not in tracking
    pane_drop = frontend_module_source("features/chat/pane-drop-controller.jsx")
    assert pane_drop.index("paneCardDragImageCleanupRef.current()") < pane_drop.index("updatePaneLayout(function (current)")


def test_workbench_chat_sidebar_keeps_only_overview_and_context_unconditional():
    source = workbench_chat_source()

    tabs = source.split("  var tabs = [", 1)[1].split("  var activeTab =", 1)[0]
    assert tabs.count('id: "overview"') == 1
    assert tabs.count('id: "context"') == 1
    assert 'if (pendingPlan) tabs.push({ id: "plan"' in tabs
    assert 'if (hasSubagents) tabs.push({ id: "subagents"' in tabs
    assert 'if (hasFiles) tabs.push({ id: "files"' in tabs
    assert 'if (artifactItems.length) tabs.push({ id: "artifacts"' in tabs
    assert "if (hasWorkspaceChanges)" in tabs
    assert 'if (hasBranches) tabs.push({ id: "branches"' in tabs
    assert 'if (chatViewerFile) tabs.push({ id: "viewer"' in tabs
    assert 'if (hasMap) tabs.push({ id: "map"' in tabs
    assert 'if (hasBrowser) tabs.push({ id: "browser"' in tabs
    assert "if (sideAgents && sideAgents.length)" in tabs
    assert "sideAgentsLoading" not in tabs


def test_workbench_clears_stale_side_questions_before_loading_another_chat():
    source = workbench_chat_source()

    loading_effect = source.split("var cancelled = false;", 1)[1].split(
        "return function () { cancelled = true; };", 1
    )[0]
    assert "setSideAgents([]);\n    setSideAgentsLoading(true);" in loading_effect


def test_workbench_side_question_panel_renders_only_the_question_list():
    source = workbench_chat_source()
    styles = workbench_style_source()

    panel = source.split("function WbcSideAgentsPanel", 1)[1].split(
        "function WbcSideAccordionBody", 1
    )[0]
    assert 'className="wbc-side-agent-list"' in panel
    assert "items.map(function (agent, index)" in panel
    assert "<WbcSideAgentTab" not in panel
    assert "var flush = false;" in source
    assert 'activeTab === "changes" || activeTab === "side-agents"' not in source
    list_css = styles.split(".wbc-side-agent-list {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column;" in list_css
    assert "gap: 6px;" in list_css


def test_rail_chat_drop_replaces_the_canonical_active_conversation_by_selection():
    source = workbench_chat_source()
    helper = "function wbcStablePaneValue" + source.split(
        "function wbcStablePaneValue", 1
    )[1].split("function wbcSetSplitDrag", 1)[0]
    script = f"""
global.localStorage = {{ getItem: () => null }};
eval({json.dumps(helper)});
const base = wbcDefaultPaneLayout("main");
const canonicalMain = wbcPaneCardLocation(base, "chat:main");
const existingBase = wbcDefaultPaneLayout("existing-chat");
const canonicalExisting = wbcPaneCardLocation(existingBase, "chat:existing-chat");
const duplicateMain = wbcPaneCard("chat", "main", {{ freshInstance: true, ownerChatId: "main" }});
process.stdout.write(JSON.stringify([
  wbcChatDropReplacesActiveConversation(canonicalMain, "replace", "chat-b", "main"),
  wbcChatDropReplacesActiveConversation(canonicalExisting, "replace", "chat-b", "existing-chat"),
  wbcChatDropReplacesActiveConversation(canonicalMain, "bottom", "chat-b", "main"),
  wbcChatDropReplacesActiveConversation({{ card: duplicateMain }}, "replace", "chat-b", "main"),
]));
"""
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == [True, True, False, False]

    pane_drop = frontend_module_source("features/chat/pane-drop-controller.jsx")
    replacement_branch = pane_drop.split("if (dropped.droppedChatSelection) {", 1)[1].split(
        "  context.updatePaneLayout(function (current)", 1
    )[0]
    assert "wbcChatDropReplacesActiveConversation(" in pane_drop
    assert "context.selectChat(dropped.droppedChatSelection);" in replacement_branch
    assert "updatePaneLayout" not in replacement_branch
    assert "setSideVisible(false)" not in replacement_branch


def test_pane_drop_commits_the_visible_replace_or_split_action():
    pane_drop = frontend_module_source("features/chat/pane-drop-controller.jsx")
    helper = "function wbcCommittedPaneDropEdge" + pane_drop.split(
        "function wbcCommittedPaneDropEdge", 1
    )[1].split("function wbcDroppedPaneCard", 1)[0]
    script = f"""
eval({json.dumps(helper)});
const visibleReplace = {{ paneDropTarget: {{ cardId: "chat:main", edge: "replace" }} }};
const visibleRight = {{ paneDropTarget: {{ cardId: "chat:main", edge: "right" }} }};
const stalePreview = {{ paneDropTarget: {{ cardId: "chat:other", edge: "replace" }} }};
process.stdout.write(JSON.stringify([
  wbcCommittedPaneDropEdge(visibleReplace, "chat:main", "left"),
  wbcCommittedPaneDropEdge(visibleRight, "chat:main", "replace"),
  wbcCommittedPaneDropEdge(stalePreview, "chat:main", "left"),
]));
"""
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == ["replace", "right", "left"]

    page = workbench_chat_source()
    context_surface = page.split(
        "function WbcPaneContextTrackDropSurface", 1
    )[1].split("function WbcPaneFiveWayDropSurface", 1)[0]
    assert "paneDropTarget: paneDropTarget" in page
    assert 'className="wbc-pane-context-drop-sensor replace"' in context_surface
    assert context_surface.count('onDropOver(event, card.id, "replace", dropKey)') == 2
    assert 'onDrop(event, card.id, "replace")' in context_surface
    assert 'wbcT("workbenchChat.dropConversationReplace"' in context_surface
    assert "var committedEdge = wbcCommittedPaneDropEdge(context, targetCardId, edge);" in pane_drop
    assert "effectiveEdge = (layout[target.side] || []).length >= 2 ? \"replace\" : committedEdge" in pane_drop


def test_pane_grip_drag_uses_rendered_position_key_for_drop_feedback():
    source = workbench_chat_source()
    drag_controller = frontend_module_source("features/chat/pane-card-drag-controller.jsx")

    hit_testing = drag_controller.split("function wbcPaneDragTargetAt(session, clientX, clientY) {", 1)[1].split(
        "function wbcPaneDragPointIn", 1
    )[0]
    frame = source.split("function WbcPaneCardFrame(", 1)[1].split(
        "function WbcPaneRowResizer", 1
    )[0]

    assert 'data-pane-drop-key={dropKey || card.id}' in frame
    assert 'dropKey: String(targetCard.dataset.paneDropKey || targetId)' in hit_testing
    assert 'String(dropTarget.dropKey || "") === String(dropKey || "")' in frame
    assert 'side + ":" + index' in source


def test_agent_can_open_move_close_and_resize_semantic_split_panes():
    page = frontend_module_source("features/chat/page.jsx")
    rail = frontend_module_source("features/chat/rail.jsx")
    split = frontend_module_source("features/chat/split-pane.jsx")
    semantic = frontend_module_source("features/chat/pane-semantic-controller.jsx")
    layout = frontend_module_source("features/chat/pane-layout-controller.jsx")
    drag_layout = frontend_module_source("features/chat/drag-layout.jsx")

    assert 'action_id: "open_split", kind: "move"' in rail
    assert 'onOpenSplit(chatId, { side: side })' in rail
    assert '<WbcPaneSemanticController' in page
    assert 'semanticNodeId={wbcPaneSemanticNodeId(card.id)}' in page
    assert 'onClosePane={closePaneCardWithConfirmation}' in page
    assert 'node_id: "pane_workspace"' in semantic
    assert 'action_id: "open_chat", kind: "move"' in semantic
    assert 'action_id: "open_terminal", kind: "move"' in semantic
    assert 'action_id: "move", kind: "move"' in semantic
    assert 'action_id: "swap", kind: "move"' in semantic
    assert 'action_id: "close", kind: "invoke"' in semantic
    assert 'data-pane-semantic-node-id={semanticNodeId || ""}' in split
    assert 'node_id: "pane_column_separator"' in split
    assert 'node_id: "pane_row_separator_" + normalizedSide' in split
    assert split.count('action_id: "set_value", kind: "adjust"') >= 2
    assert split.count('action_id: "adjust", kind: "adjust"') >= 2

    location_helper = "function wbcPaneCardLocation" + drag_layout.split(
        "function wbcPaneCardLocation", 1
    )[1].split("function wbcPlacePaneCard", 1)[0]
    move_helper = "function wbcMovePaneCardLayout" + layout.split(
        "function wbcMovePaneCardLayout", 1
    )[1].split("function wbcMovePaneCard(context", 1)[0]
    swap_helper = "function wbcSwapPaneCardsLayout" + layout.split(
        "function wbcSwapPaneCardsLayout", 1
    )[1].split("function wbcSwapPaneCards(context", 1)[0]
    script = f"""
eval({json.dumps(location_helper + move_helper + swap_helper)});
const a = {{ id: "a" }}, b = {{ id: "b" }}, c = {{ id: "c" }};
const base = {{ left: [a, b], right: [c], leftRatio: 0.4, rightRatio: 0.6 }};
const reordered = wbcMovePaneCardLayout(base, "b", {{ side: "left", position: "top" }});
const moved = wbcMovePaneCardLayout(base, "a", {{ side: "right", position: "bottom" }});
const swapped = wbcSwapPaneCardsLayout(base, "a", "c");
let fullError = "";
try {{ wbcMovePaneCardLayout(moved, "b", {{ side: "right", position: "top" }}); }} catch (error) {{ fullError = error.message; }}
process.stdout.write(JSON.stringify({{
  reordered: [reordered.left.map(item => item.id), reordered.right.map(item => item.id)],
  moved: [moved.left.map(item => item.id), moved.right.map(item => item.id)],
  swapped: [swapped.left.map(item => item.id), swapped.right.map(item => item.id)],
  ratios: [moved.leftRatio, moved.rightRatio],
  fullError,
}}));
"""
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == {
        "reordered": [["b", "a"], ["c"]],
        "moved": [["b"], ["c", "a"]],
        "swapped": [["c", "b"], ["a"]],
        "ratios": [0.4, 0.6],
        "fullError": "target pane column is full",
    }


def test_workbench_side_question_opens_the_existing_conversation_ui_in_a_split():
    source = workbench_chat_source()
    pane = source.split("function renderPaneCard", 1)[1].split(
        "function renderPaneColumn", 1
    )[0]
    side_question = pane.split('card.kind === "side-agent"', 1)[1].split(
        "} else {", 1
    )[0]
    assert "<WbcSideAgentSplit" in side_question
    assert "<WbcSplitGripBar" in side_question
    assert "onOpenConversationPanel" in side_question
    assert 'setSideTab("side-agents")' in side_question
    assert "<WbcChatSplit" not in side_question


def test_each_conversation_split_grip_closes_its_own_conversation():
    source = frontend_module_source("features/chat/page.jsx")
    pane = source.split("function renderPaneCard", 1)[1].split(
        "function renderPaneColumn", 1
    )[0]
    assert "var close = function () { return closePaneCardWithConfirmation(card); };" in pane
    assert "function closePaneCardWithConfirmation(card, ownerChatId)" in source
    assert "var move = function () { movePaneCardOtherSide(card.id); };" in pane
    assert "onClose={close}" in pane
    assert "onToggleSide={move}" in pane


def test_floating_conversation_panel_resource_split_replaces_right_and_restores_previous_split():
    source = workbench_chat_source()
    pane_controller = frontend_module_source("features/chat/pane-layout-controller.jsx")
    opener = pane_controller.split("function wbcOpenPaneContent", 1)[1].split(
        "function wbcUpdatePaneCard", 1
    )[0]
    panel = source.split("function renderConversationPanel", 1)[1].split(
        "function openContentFromPaneCard", 1
    )[0]
    closer = pane_controller.split("function wbcClosePaneCard", 1)[1].split(
        "function wbcCloseDeletedChatSplits", 1
    )[0]
    assert "paneLayoutRestoreRef.current[card.id] = layout" in opener
    assert "wbcPromotePaneSourceLayout(layout, source, card)" in opener
    assert "restore: !!floating" in panel
    assert "promoteSourceLeft: true" in panel
    assert "wbcUpdatePaneLayout(context, restore, ownerChatId)" in closer


def test_opening_content_from_a_vertical_split_preserves_both_existing_panes():
    pane_controller = frontend_module_source("features/chat/pane-layout-controller.jsx")
    helper = "function wbcPromotePaneSourceLayout" + pane_controller.split(
        "function wbcPromotePaneSourceLayout", 1
    )[1].split("function wbcOpenPaneContent", 1)[0]
    script = f"""
eval({json.dumps(helper)});
const top = {{ id: "top" }};
const bottom = {{ id: "bottom" }};
const existingRight = {{ id: "existing-right" }};
const opened = {{ id: "opened" }};
const vertical = wbcPromotePaneSourceLayout(
  {{ left: [top, bottom], right: [], leftRatio: 0.4, rightRatio: 0.5 }},
  {{ side: "left", index: 0, card: top }},
  opened
);
const occupied = wbcPromotePaneSourceLayout(
  {{ left: [top], right: [existingRight], leftRatio: 0.4, rightRatio: 0.5 }},
  {{ side: "left", index: 0, card: top }},
  opened
);
process.stdout.write(JSON.stringify({{
  vertical: [vertical.left.map(card => card.id), vertical.right.map(card => card.id)],
  occupied: [occupied.left.map(card => card.id), occupied.right.map(card => card.id)],
}}));
"""
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == {
        "vertical": [["top", "bottom"], ["opened"]],
        "occupied": [["top"], ["opened"]],
    }


def test_workbench_message_viewer_action_opens_the_file_split_directly():
    open_viewer = frontend_module_source("features/chat/page-resource-controller.jsx").split(
        "function wbcOpenViewer(context, file, preferredSide) {", 1
    )[1].split(
        "function wbcOpenProjectFile", 1
    )[0]
    assert "setViewerFile(file);" in open_viewer
    assert 'selectResourceSplit("viewer", wbcArtifactFileKey(file), true);' in open_viewer
    assert 'openPaneContent("file", file' in open_viewer
    assert 'side: preferredSide === "left" ? "left" : "right"' in open_viewer


def test_workbench_split_grip_opens_a_centered_floating_conversation_panel():
    source = workbench_chat_source()
    styles = workbench_style_source()

    grip_bar = source.split("function WbcSplitGripBar", 1)[1].split(
        "function WbcSideAgentSplit", 1
    )[0]
    assert "workbenchServices.browserOverlays()" in grip_bar
    assert "overlays.adjust(1);" in grip_bar
    assert "overlays.adjust(-1);" in grip_bar
    assert "}, [menuOpen]);" in grip_bar
    assert "WBC_ICONS.sidebar" in grip_bar
    assert 'wbcT("workbenchChat.detailPanel.openConversationPanel"' in grip_bar
    assert "if (onOpenConversationPanel) onOpenConversationPanel();" in grip_bar
    assert "if (onClose) onClose();" not in grip_bar.split("function openConversationPanel", 1)[1].split("return (", 1)[0]
    assert "floatingConversationPanelOpen" in source
    assert "renderConversationPanel(true)" in source
    pane_card = source.split("  function renderPaneCard(", 1)[1].split(
        "  function renderPaneColumn(", 1
    )[0]
    pane_layout = source.split('<div\n        className={"wbc-pane-layout"', 1)[1].split(
        "      </div>", 1
    )[0]
    # The float must be owned by the active conversation card. Centering a
    # sibling against the full two-column workspace shifts it into the gutter.
    assert "isActiveConversation && floatingConversationPanelOpen" in pane_card
    assert 'className="wbc-pane-floating-conversation-panel"' in pane_card
    assert 'className="wbc-pane-floating-conversation-panel"' not in pane_layout
    assert 'floating ? " wbc-side-floating" : ""' in source
    assert "floating ? WBC_ICONS.x : WBC_ICONS.chevronsRight" in source
    assert 'wbcT("workbenchChat.closeFloatingConversationPanel"' in source

    assert "function WbcPanelAccordionSurface(" in source
    assert "function WbcPanelAccordionList(" in source
    assert "function WbcPanelAccordionSection(" in source
    assert "function WbcSplitGripAccordionBody(" in source
    assert "window.setTimeout(function () { setRendered(false); }, 190)" in source
    assert 'className={"wbc-side-split-grip-expanded-body" + (expanded ? " open" : "")}' in source
    assert 'aria-hidden={expanded ? "false" : "true"}' in source
    assert 'className={"wbc-side-accordion-item"' in source
    assert 'className={"wbc-side-accordion-trigger"' in source
    assert 'className="wbc-side-accordion-chevron"' in source
    assert "<WbcSideAccordionBody" in source
    assert 'className={"wbc-side-split-grip-menu"' in grip_bar
    assert 'wbc-side-split-grip-menu wbc-side-card' not in grip_bar
    assert '<WbcPanelAccordionList className="wbc-side-split-grip-accordion">' in grip_bar
    assert '<WbcPanelAccordionList className="wbc-side-accordion"' not in grip_bar
    assert 'window.ReactDOM.createPortal((' in grip_bar
    assert 'typeof document !== "undefined" ? document.body : null' in grip_bar
    assert 'surfaceRef={menuRef}' in grip_bar
    assert 'insideMenu = menuRef.current && menuRef.current.contains(event.target)' in grip_bar
    assert 'rootRef.current.closest(".workbench-grid")' in grip_bar
    assert 'propertyName.indexOf("--wb-") !== 0' in grip_bar
    assert 'rootRef.current.closest(".wbc-pane-card, .wbc-side-card")' in grip_bar
    assert 'portalTheme["--wbc-split-grip-surface"] = cardStyle.backgroundColor' in grip_bar
    assert 'style={Object.assign({}, menuPosition.portalTheme' in grip_bar
    split_menu_css = styles.split(".wbc-side-split-grip-menu {", 1)[1].split("}", 1)[0]
    assert "position: fixed;" in split_menu_css
    assert "top: var(--wbc-split-grip-menu-top);" in split_menu_css
    assert "left: var(--wbc-split-grip-menu-left);" in split_menu_css
    assert "transform: translateX(-50%);" in split_menu_css
    assert "right:" not in split_menu_css
    assert "height: max-content;" in split_menu_css
    assert "align-self: start;" in split_menu_css
    assert "max-height:" not in split_menu_css
    assert "display: block;" in split_menu_css
    assert "animation: wbc-floating-side-panel-in 170ms cubic-bezier(.2, .8, .2, 1) both;" in split_menu_css
    assert "--wbc-panel-surface:" not in split_menu_css
    split_menu_surface_css = styles.split(
        ".wbc-panel-accordion-surface.wbc-side-split-grip-menu {", 1
    )[1].split("}", 1)[0]
    assert "--wbc-split-grip-border-color: #d2dae2;" in split_menu_surface_css
    assert "border: 1px solid var(--wbc-split-grip-border-color);" in split_menu_surface_css
    assert "border-radius: var(--wbc-split-grip-radius, 18px);" in split_menu_surface_css
    assert "background: var(--wbc-split-grip-surface," in split_menu_surface_css
    assert "box-shadow: var(--wbc-split-grip-shadow," in split_menu_surface_css
    dark_split_menu_surface_css = styles.split(
        'html[data-theme="dark"] .wbc-panel-accordion-surface.wbc-side-split-grip-menu {', 1
    )[1].split("}", 1)[0]
    assert "--wbc-split-grip-border-color: #41434d;" in dark_split_menu_surface_css
    split_menu_item_css = styles.split(
        ".wbc-side-split-grip-menu .wbc-side-accordion-item {", 1
    )[1].split("}", 1)[0]
    assert "border-bottom: 1px solid var(--wbc-split-grip-divider-color);" in split_menu_item_css
    assert "--wbc-split-grip-divider-color: rgba(23, 28, 34, .08);" in split_menu_surface_css
    assert "--wbc-split-grip-divider-color: rgba(255, 255, 255, .055);" in dark_split_menu_surface_css
    split_menu_animation_css = styles.split(
        ".wbc-side-split-grip-expanded-body {", 1
    )[1].split("}", 1)[0]
    assert "interpolate-size: allow-keywords;" in split_menu_animation_css
    assert "height: 0;" in split_menu_animation_css
    assert "height 190ms cubic-bezier(.2, .8, .2, 1)" in split_menu_animation_css
    split_menu_animation_open_css = styles.split(
        ".wbc-side-split-grip-expanded-body.open {", 1
    )[1].split("}", 1)[0]
    assert "height: auto;" in split_menu_animation_open_css
    assert "opacity: 1;" in split_menu_animation_open_css
    slider_css = styles.split(
        '.wbc-side-split-grip-setting-slider input[type="range"] {', 1
    )[1].split("}", 1)[0]
    assert "display: block;" in slider_css
    assert "var(--wbc-split-grip-slider-track)" in slider_css
    split_accordion_css = styles.split(".wbc-side-split-grip-accordion {", 1)[1].split("}", 1)[0]
    assert "max-height: min(620px, calc(100vh - var(--wbc-split-grip-menu-top) - 12px));" in split_accordion_css
    assert "height: max-content;" in split_accordion_css
    split_menu_body_css = styles.split(
        ".wbc-side-split-grip-expanded-content {", 1
    )[1].split("}", 1)[0]
    assert "padding: 0 16px 12px;" in split_menu_body_css
    assert "box-sizing: border-box;" in split_menu_body_css
    assert "menuBody={true}" in grip_bar
    assert 'bodyClass="wbc-side-split-grip-accordion-body"' not in grip_bar
    split_menu_information_css = styles.split(
        ".wbc-side-split-grip-information {", 1
    )[1].split("}", 1)[0]
    assert "padding: 3px 0 0;" in split_menu_information_css
    panel_accordion_css = styles.split(".wbc-panel-accordion-list {", 1)[1].split("}", 1)[0]
    surface_css = styles.split(".wbc-panel-accordion-surface {", 1)[1].split("}", 1)[0]
    assert "flex:" not in panel_accordion_css
    assert "height:" not in panel_accordion_css
    assert ".wbc-panel-accordion-surface {" in styles
    assert '<WbcPanelAccordionSurface className="wbc-side-card">' in source
    accordion_item_css = styles.split(".wbc-side-accordion-item {", 1)[1].split("}", 1)[0]
    assert "border-bottom: 1px solid" in accordion_item_css
    assert ".wbc-side-collapse.open" in styles
    collapse_css = styles.split("\n.wbc-side-collapse {", 1)[1].split("}", 1)[0]
    open_collapse_css = styles.split("\n.wbc-side-collapse.open {", 1)[1].split("}", 1)[0]
    assert "height: 0;" in collapse_css
    assert "height: auto;" in open_collapse_css
    assert "grid-template-rows:" not in collapse_css
    assert "interpolate-size: allow-keywords;" in collapse_css
    assert "interpolate-size:" not in surface_css
    accordion_body = source.split("function WbcSideAccordionBody(", 1)[1].split(
        "function WbcConversationTerminalList", 1
    )[0]
    assert "ResizeObserver" not in accordion_body
    assert "--wbc-side-collapse-height" not in accordion_body

    floating_css = styles.split(
        ".wbc-split-main-grip > .wbc-side-floating {", 1
    )[1].split("}", 1)[0]
    assert "top: calc(100% + 4px);" in floating_css
    assert "left: 50%;" in floating_css
    assert "transform: translateX(-50%);" in floating_css
    assert "width: min(var(--wb-right-w, 350px), calc(100% - 24px));" in floating_css
    # The floating panel floats over live transcript content, so its card must
    # stay opaque — matching the docked panel instead of showing the
    # conversation through it.
    floating_card_css = styles.split(
        ".wbc-split-main-grip > .wbc-side-floating .wbc-side-card {", 1
    )[1].split("}", 1)[0]
    assert "background: var(--wb-card-bg);" in floating_card_css
    opaque_float_css = styles.split(
        "html :is(\n  .wbc-pane-floating-conversation-panel,", 1
    )[1].split("}", 1)[0]
    assert ".wbc-split-chat-panel," in opaque_float_css
    assert ".wbc-split-main-grip > .wbc-side-floating" in opaque_float_css
    assert "background: var(--wb-card-bg-strong);" in opaque_float_css
    assert "backdrop-filter: none;" in opaque_float_css


def test_workbench_artifacts_use_the_shared_resizable_split_preview():
    source = workbench_chat_source()
    resource_list = frontend_module_source("features/chat/resource-list.jsx")
    split_controller = frontend_module_source("features/chat/split-selection-controller.jsx")
    styles = workbench_style_source()

    artifact_tab = source.split("function WbcArtifactsTab", 1)[1].split(
        "window.CyreneUI.chat", 1
    )[0]
    artifact_split = source.split("function WbcArtifactSplit({", 1)[1].split(
        "function WbcSideAgentSplitHost", 1
    )[0]
    select_handler = split_controller.split("function wbcSelectArtifact", 1)[1].split(
        "function wbcSelectChange", 1
    )[0]

    assert 'workbenchChat.filesAndArtifacts' not in artifact_tab
    assert 'className="wbc-artifact-list"' in artifact_tab
    assert "<WbcResourceListRow" in artifact_tab
    assert 'className={"wbc-artifact-list-row"' in resource_list
    assert "if (onSelectArtifact) onSelectArtifact(file);" in artifact_tab
    assert "context.setArtifactSplitByChat" in select_handler
    assert 'wbcClearOtherSplits(context, chatId, "artifact")' in select_handler
    assert "function WbcArtifactSplitHost" in source
    assert "<WbcSideAgentSplitResizer width={width} onResize={onResize} splitSide={splitSide} />" in source
    assert "<WbcSideSplitGrip" not in source
    assert 'className="wbc-side-agent-split wbc-artifact-split"' in artifact_split
    assert 'className="wbc-side-agent-split-picker"' in artifact_split
    assert "files.map(function (item, index)" in artifact_split
    assert "<WbcViewerTab" in artifact_split
    assert "file={file}" in artifact_split
    assert "onViewed={onViewed}" in artifact_split
    assert "hideHeader={true}" in artifact_split
    assert 'className="wbc-artifact-split-actions"' in artifact_split
    assert 'className="wbc-side-agent-split-action"' in artifact_split
    assert "var splitDetailOpen = paneCardCount > 1;" in source
    assert ".wbc-artifact-list-row" in styles
    assert ".wbc-artifact-split-viewer" in styles


def test_workbench_viewer_split_grip_has_symmetric_vertical_spacing():
    styles = workbench_style_source()

    pane_split = styles.split(
        "\n.wbc-pane-card > .wbc-side-agent-split {"
    )[-1].split("}", 1)[0]
    grip = styles.split(
        ".wbc-pane-card-grip,", 1
    )[1].split("}", 1)[0]
    viewer_header_spacing = styles.split(
        ".wbc-pane-card > .wbc-artifact-split > .wbc-side-agent-split-head {", 1
    )[1].split("}", 1)[0]
    browser_header_spacing = styles.split(
        ".wbc-pane-card > .wbc-browser-split > .wbc-resource-split-picker-wrap > .wbc-side-agent-split-head {",
        1,
    )[1].split("}", 1)[0]

    assert "padding-top: 34px;" in pane_split
    assert "height: 34px;" in grip
    assert "margin-top: 0;" in viewer_header_spacing
    assert "margin-top: 0;" in browser_header_spacing


def test_project_file_rows_drag_to_viewer_split_and_topbar_resource_shelf():
    source = workbench_chat_source()
    styles = workbench_style_source()

    project_resource = frontend_module_source("features/chat/rail-model.jsx")
    project_rows = frontend_module_source("features/chat/project-files.jsx")
    project_files = frontend_module_source("features/chat/project-files.jsx")
    project_browser = source.split("function WbcRail", 1)[1].split(
        "function WbcBrowserFloatingSurface", 1
    )[0]
    split_drop = frontend_module_source("features/chat/page-drop-controller.jsx")

    assert 'source: "project"' in project_resource
    assert '"/api/projects/" + encodeURIComponent(projectId) + "/files/content/"' in project_resource
    assert 'draggable={projectFile ? "true" : undefined}' in project_rows
    assert "wbcStartFileDrag(event, projectFile)" in project_rows
    assert "wbcSetResourceDrag(event, wbcFileDragPayload(" in source
    assert "path: file && file.path" in source
    assert "source: file && file.source" in source
    assert "wbcHasResourceDrag(event)" in split_drop
    assert 'resource.kind !== "file"' in split_drop
    assert "setSplitSideDirect(side);" in split_drop
    assert "openViewer(resource.file" in split_drop
    assert "function wbcResourceSplitDropGeometry(pageRef)" in split_drop
    assert "var chatSideRect = wbcChatSideZoneRect();" in split_drop
    assert "event.clientX < geometry.rightLeft ? \"left\" : \"right\"" in split_drop
    assert 'className="wbc-resource-file-drop-zones"' in source
    assert 'className="wbc-chat-side-drop-hint" role="status"' in source
    assert 'event.target.closest(".wbc-pane-card")' in split_drop
    assert ".wbc-resource-file-drop-zones" in styles
    resource_zones = styles.split(".wbc-resource-file-drop-zones {", 1)[1].split("}", 1)[0]
    resource_zone = styles.split(".wbc-resource-file-drop-zone {", 1)[1].split("}", 1)[0]
    resource_active = styles.split(".wbc-resource-file-drop-zone.active {", 1)[1].split("}", 1)[0]
    resource_hint = styles.split(
        ".wbc-resource-file-drop-zone .wbc-chat-side-drop-hint {", 1
    )[1].split("}", 1)[0]
    assert "left: 0;" in resource_zones
    assert "display: grid;" not in resource_zones
    assert "position: absolute;" in resource_zone
    assert "border: 2px solid transparent;" in resource_zone
    assert "border-color: color-mix(in srgb, var(--wb-accent) 72%, transparent);" in resource_active
    assert "cubic-bezier(.22, 1.16, .36, 1)" in resource_hint
    assert 'return { projectId: String(projectId || ""), path: "." };' in project_browser
    assert "fileLocation.projectId === currentFileProjectId" in project_browser
    assert 'setQuery("");' in project_browser
    assert 'var key = normalizedProjectId + ":" + normalizedPath' in project_files
    assert 'if (snapshot.key !== key) return { entries: []' in project_files


def test_project_files_open_in_a_project_scoped_pane_without_an_active_chat():
    source = workbench_chat_source()
    styles = workbench_style_source()

    pane_helpers = frontend_module_source("features/chat/pane-layout-controller.jsx")
    open_viewer = frontend_module_source("features/chat/page-resource-controller.jsx")

    assert 'return context.projectId ? "project:" + String(context.projectId) : "";' in pane_helpers
    assert "chatId || context.activeChatIdRef.current || wbcProjectPaneOwnerKey(context)" in pane_helpers
    assert "wbcNormalizePaneLayout(context.paneLayoutsByChat[ownerId], ownerChatId)" in pane_helpers
    assert 'if (!ownerId || !type) return null;' in pane_helpers
    assert 'openPaneContent("file", file' in open_viewer
    assert 'paneOnlyCard.kind !== "chat"' in source
    assert "showNewConversationWorkspace: !activeChatId && paneCardCount === 0" in source
    assert '(projectPaneOnly ? " wbc-project-pane-only" : "")' in source
    assert "!projectPaneOnly && !floatingConversationPanelOpen && !splitDetailOpen" in source
    assert 'className="wbc-pane-column left wbc-new-conversation-column"' in source
    assert 'renderPaneCard({ id: "new-conversation", kind: "chat", payload: "", ownerChatId: "" }, "left", 1)' in source
    pane_card_renderer = source.split("  function renderPaneCard(", 1)[1].split(
        "\n  function renderPaneColumn", 1
    )[0]
    assert 'var isNewConversation = card.kind === "chat"' in pane_card_renderer
    assert 'String(card.id || "") === "new-conversation"' in pane_card_renderer
    assert "var isActiveConversation = isNewConversation || (" in pane_card_renderer
    assert pane_card_renderer.index("if (isActiveConversation && singlePane)") < pane_card_renderer.index(
        '} else if (card.kind === "chat")'
    )
    split_pane = frontend_module_source("features/chat/split-pane.jsx")
    empty_id_guard = split_pane.split("function wbcRefreshSplitChat(options)", 1)[1].split(
        "return WorkbenchChatModel.getChat", 1
    )[0]
    assert "if (!requestedId)" in empty_id_guard
    assert "setLoading(false);" in empty_id_guard
    project_only_styles = styles.split(".wbc-page.wbc-project-pane-only {", 1)[1].split(
        "}", 1
    )[0]
    assert "--wbc-side-track-width: 0px;" in project_only_styles
    assert "minmax(0, 1fr)" in project_only_styles
    assert ".wbc-page.wbc-project-pane-only > .wbc-pane-layout.single" in styles


def test_project_text_files_use_codemirror_with_live_markdown_and_conflict_controls():
    root = Path(__file__).resolve().parent.parent
    source = workbench_chat_source()
    editor = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "code" / "editor.jsx").read_text(
        encoding="utf-8"
    )
    renderer = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx"
    ).read_text(encoding="utf-8")
    styles = workbench_style_source()
    index = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "index.html").read_text(
        encoding="utf-8"
    )
    package = json.loads((root / "src" / "cyrene" / "workbench" / "webui" / "package.json").read_text(encoding="utf-8"))

    assert "@codemirror/view" in package["dependencies"]
    assert "@codemirror/lang-markdown" in package["dependencies"]
    assert "turndown" in package["dependencies"]
    assert "turndown-plugin-gfm" in package["dependencies"]
    assert 'import { Compartment, EditorState } from "@codemirror/state";' in editor
    assert "root.CyreneCodeMirror = Object.freeze({" in editor
    assert "Editor: Editor," in editor
    assert 'key: "Mod-s"' in editor
    assert '<script type="module" src="compiled/app.js?v=0.9.0-beta4">' in index
    assert 'import "../code/editor.jsx"' in (
        root / "src/cyrene/workbench/webui/frontend/entry/app.jsx"
    ).read_text(encoding="utf-8")
    assert 'function wbcProjectFileEditUrl(file)' in source
    assert 'expectedVersion: editorVersionRef.current' in source
    assert 'force: !!force' in source
    assert 'error.status === 409' in source
    assert 'window.setTimeout(function () { saveEditor(false); }, 650)' in source
    assert 'className="wbc-text-editor-toolbar"' not in source
    assert "function WbcMarkdownRenderedEditor" in source
    assert 'contentEditable="true"' in source
    assert "markdownFromElement" in editor
    assert 'turndown.addRule("cyreneInteractiveBlock"' in editor
    assert "data-wbc-source" in renderer
    assert 'markdownMode === "rendered"' in source
    assert 'window.addEventListener("beforeunload", warnBeforeUnload)' in source
    assert ".wbc-codemirror-host > .cm-editor" in styles
    assert ".wbc-markdown-rendered-editor" in styles
    editor_surface = styles.split(".wbc-text-editor-surface {", 1)[1].split("}", 1)[0]
    editor_host = styles.split(".wbc-codemirror-host {\n  width", 1)[1].split("}", 1)[0]
    editor_scroller = styles.split(".wbc-codemirror-host .cm-scroller {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in editor_surface
    assert "height: 100%;" in editor_host
    assert "overflow: auto;" in editor_scroller


def test_workbench_changes_panel_is_list_only_and_opens_shared_diff_split():
    source = workbench_chat_source()
    styles = workbench_style_source()

    changes_tab = source.split("function WbcChangesTab", 1)[1].split(
        "function WbcSubagentsTab", 1
    )[0]
    change_split = source.split("function WbcChangeSplit({", 1)[1].split(
        "function WbcSideAgentSplitHost", 1
    )[0]

    assert 'className="wbc-resource-list wbc-changes-files"' in changes_tab
    assert 'className={"wbc-resource-list-row wbc-change-file " + item.changeType}' in changes_tab
    assert "if (onSelectChange) onSelectChange({ chatId: chatId" in changes_tab
    assert 'className="wbc-change-diff"' not in changes_tab
    assert "WorkbenchChatModel.getChangeDiff" not in changes_tab
    assert "function WbcChangeSplitHost" in source
    assert 'className="wbc-side-agent-split wbc-change-split"' in change_split
    assert "WorkbenchChatModel.getChangeDiff" in change_split
    assert 'className="wbc-change-split-diff wbc-change-diff"' in change_split
    assert "files.map(function (item)" in change_split
    assert ".wbc-change-split-diff" in styles
    change_diff_styles = styles.split(".wbc-change-diff {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in change_diff_styles
    assert "flex-direction: column;" in change_diff_styles
    assert "min-height: 0;" in change_diff_styles
    diff_content_styles = styles.split(".diff-viewer-content {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in diff_content_styles


def test_workbench_resource_tabs_use_lists_and_shared_splits_while_branches_expand_inline():
    root = Path(__file__).resolve().parent.parent
    source = workbench_chat_source()
    browser = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(
        encoding="utf-8"
    )
    styles = workbench_style_source()

    side = source.split("function WbcSide({", 1)[1].split("function wbcChangeTypeLabel", 1)[0]
    assert 'activeTab === "viewer" && <WbcViewerList' in side
    assert 'activeTab === "map" && mapAvailable !== false && <WbcMapList' in side
    assert '<WbcBrowserList browserState={browserPanelState}' in side
    assert 'var opensSplit = item.id === "subagents" || item.id === "browser" || item.id === "workspace";' in side
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
    assert 'onSelect={function (next) { selectResourceSplit("map", next); }}' in source
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


def test_workbench_chat_composer_uses_native_content_sizing_without_forced_layout():
    source = workbench_chat_source()
    styles = workbench_style_source()

    composer = source.split("function WbcComposer({", 1)[1].split(
        "function wbcClearComposerDraft", 1
    )[0]
    textarea_css = styles.split(
        ".wbc-composer-box textarea.wbc-composer-textarea {", 1
    )[1].split("}", 1)[0]
    empty_textarea_css = styles.split(
        ".wbc-composer-box textarea.wbc-composer-textarea:placeholder-shown {", 1
    )[1].split("}", 1)[0]

    assert "function syncHeight()" not in composer
    assert "scrollHeight" not in composer
    assert 'CSS.supports("field-sizing", "content")' in source
    assert "wbcSyncLegacyComposerHeight(taRef.current, draft, compact);" in composer
    assert 'className="wbc-composer-textarea"' in composer
    assert "field-sizing: content;" in textarea_css
    assert "min-height: 44px;" in textarea_css
    assert "max-height: 180px;" in textarea_css
    assert "overflow-y: auto;" in textarea_css
    assert "field-sizing: fixed;" in empty_textarea_css
    assert "height: 44px;" in empty_textarea_css


def test_workbench_chat_composer_batches_draft_storage_and_reserve_height_updates():
    source = workbench_chat_source()

    assert "var WBC_DRAFT_SAVE_DELAY_MS = 300;" in source
    assert "pendingDraftSaveRef.current = { id: chatId, text: draft, ns: draftNs };" in source
    assert "window.setTimeout(flushPendingDraftSave, WBC_DRAFT_SAVE_DELAY_MS)" in source
    assert 'window.addEventListener("pagehide", flushPendingDraftSave);' in source
    assert "var lastHeight = 0;" in source
    assert "if (height <= 0 || height === lastHeight) return;" in source
    assert "new ResizeObserver(scheduleComposerReserveHeight)" in source


def test_workbench_chat_composer_themes_share_one_optimized_glass_pipeline():
    styles = workbench_style_source()

    shell = styles.split("\n.workbench-shell {", 1)[1].split("}", 1)[0]
    base = styles.split("\n.wbc-composer-box {", 1)[1].split("}", 1)[0]
    light = styles.split('html[data-theme="light"] .wbc-composer-box {', 1)[1].split(
        "}", 1
    )[0]
    dark = styles.split(
        'html[data-theme="dark"] :is(.wbc-composer-box, .wbc-side-card) {', 1
    )[1].split("}", 1)[0]
    dark_composer = styles.split(
        'html[data-theme="dark"] .wbc-composer-box {', 1
    )[1].split("}", 1)[0]
    dark_palette = styles.split(
        'html[data-theme="dark"] .workbench-shell {', 1
    )[1].split("}", 1)[0]
    performance = styles.split(
        'html[data-performance-mode="on"] *,', 1
    )[1].split("}", 1)[0]

    assert "--wbc-composer-glass-filter: blur(18px) saturate(120%) contrast(102%);" in shell
    assert "backdrop-filter: var(--wbc-composer-glass-filter);" in base
    assert "var(--wbc-composer-glass-drop-shadow)" in base
    assert "var(--wbc-composer-glass-top-highlight)" in base
    assert "var(--wbc-composer-glass-inner-edge)" in base
    assert "#fff 76%" in light
    assert "#1a1a1a 76%" in dark_palette
    assert "rgba(15, 23, 42, .12)" in light
    assert "rgba(0, 0, 0, .42)" in dark
    assert "#fff 14%" in dark
    assert "#fff 4%" in dark
    assert "--wbc-composer-glass-background: var(--wb-composer-surface);" in dark_composer
    assert "--wb-composer-surface: color-mix(in srgb, #18191d 76%, transparent);" in dark_palette
    assert "backdrop-filter" not in light
    assert "backdrop-filter" not in dark
    assert "backdrop-filter: none !important;" in performance
    assert "box-shadow: none !important;" in performance


def test_memory_detail_uses_shared_floating_card_and_animated_accordion():
    root = Path(__file__).resolve().parent.parent
    styles = workbench_style_source()
    memory_source = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx"
    ).read_text(encoding="utf-8")
    library_source = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx"
    ).read_text(encoding="utf-8")
    library_styles = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.css"
    ).read_text(encoding="utf-8")
    schedule_styles = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "features" / "schedule" / "schedule.css"
    ).read_text(encoding="utf-8")

    card_css = styles.split(".wb-floating-detail-card {", 1)[1].split("}", 1)[0]
    shell_css = styles.split(".wb-floating-detail-shell {", 1)[1].split("}", 1)[0]
    focus_trigger_css = styles.split(
        ".wb-detail-accordion-trigger:focus {", 1
    )[1].split("}", 1)[0]
    delete_css = styles.split(".wb-detail-card-delete {", 1)[1].split("}", 1)[0]
    empty_css = styles.split(
        ".wb-floating-detail-card .wb-detail-empty-state {", 1
    )[1].split("}", 1)[0]
    empty_text_css = styles.split(
        ".wb-floating-detail-card .wb-detail-empty-state > p {", 1
    )[1].split("}", 1)[0]
    panel_css = styles.split(".wb-detail-accordion-panel {", 1)[1].split("}", 1)[0]
    open_css = styles.split(".wb-detail-accordion-panel.open {", 1)[1].split("}", 1)[0]
    list_css = styles.split(".wb-detail-accordion-list {", 1)[1].split("}", 1)[0]
    panel_inner_css = styles.split(
        ".wb-detail-accordion-panel-inner {", 1
    )[1].split("}", 1)[0]
    trigger_css = styles.split(".wb-detail-accordion-trigger {", 1)[1].split("}", 1)[0]
    detail_css = styles.split(".wb-mem-detail {", 1)[1].split("}", 1)[0]
    shared_width_css = styles.split(".wb-mem-page,", 1)[1].split("}", 1)[0]

    assert "border-radius: 18px;" in card_css
    assert "backdrop-filter: blur(18px) saturate(112%);" in card_css
    assert "height: auto;" in card_css
    shared_detail_padding = (
        "padding: var(--wb-floating-card-top-gap) 12px "
        "var(--wb-floating-card-bottom-gap);"
    )
    assert shared_detail_padding in shell_css
    assert (
        "padding: var(--wb-floating-card-top-gap) 12px "
        "var(--wb-floating-card-bottom-gap) 4px;" in detail_css
    )
    assert ".wb-lib-right:is(.wb-floating-detail-shell) { padding-left: 4px; }" in library_styles
    assert "--wb-floating-detail-width: calc(var(--wb-right-w, 350px) - 8px);" in shared_width_css
    assert "@media (max-width: 1320px)" in styles
    assert "--wb-floating-detail-width: calc(var(--wb-right-w, 280px) - 8px);" in styles
    assert "flex: 0 0 var(--wb-floating-detail-width);" in detail_css
    assert "flex: 1 1 0%;" in styles.split(".wb-mem-main {", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 0%;" in library_styles.split(".wb-lib-main {", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 0%;" in schedule_styles.split(".wb-sched-main {", 1)[1].split("}", 1)[0]
    assert "flex: 0 0 calc(var(--wb-right-w, 350px) - 8px);" in schedule_styles
    assert "flex-basis: calc(var(--wb-right-w, 280px) - 8px);" in schedule_styles
    assert "padding-left: 4px;" in schedule_styles
    assert (
        ".wb-detail-accordion-trigger.active { background: transparent; }"
        in styles
    )
    assert "outline: none;" in focus_trigger_css
    assert "color: var(--wb-red);" in delete_css
    assert 'className: "wb-detail-card-delete"' in memory_source
    assert 'className: "wb-detail-card-delete wb-lib-right-delete"' in library_source
    memory_header = memory_source.split(
        'className: "wb-detail-accordion-head wb-mem-detail-nav-head"', 1
    )[1].split('className: "wb-detail-accordion-list"', 1)[0]
    library_header = library_source.split(
        'className: "wb-detail-accordion-head wb-lib-right-tabs-head"', 1
    )[1].split('className: "wb-detail-accordion-list wb-lib-right-tab-list"', 1)[0]
    assert 'className: "wb-detail-card-delete"' in memory_header
    assert 'className: "wb-detail-card-delete wb-lib-right-delete"' in library_header
    assert 'className: "wb-detail-empty-state wb-mem-detail-ph"' in memory_source
    assert 'className: "wb-detail-empty-state wb-lib-right-placeholder"' in library_source
    assert "justify-content: center;" in empty_css
    assert "gap: 14px;" in empty_css
    assert "font-size: calc(13px * var(--wb-ui-font-scale, 1));" in empty_text_css
    assert "font-weight: 620;" in empty_text_css
    assert "grid-template-rows: 0fr;" in panel_css
    assert "grid-template-rows 180ms cubic-bezier(.2, .8, .2, 1)" in panel_css
    assert "overflow: hidden;" in list_css
    assert "flex: 1 1 auto;" in open_css
    assert "min-height: 0;" in open_css
    assert "grid-template-rows: 1fr;" in open_css
    assert "border-bottom: 1px solid" in open_css
    assert (
        ".wb-detail-accordion-panel.open + .wb-detail-accordion-trigger"
        in styles
    )
    assert "display: flex;" in panel_inner_css
    assert "flex-direction: column;" in panel_inner_css
    assert "font-size: calc(12px * var(--wb-ui-font-scale, 1));" in trigger_css
    assert "ICON.detail(17)" in memory_source
    assert "ICON.history(17)" in memory_source
    assert 'className: "wb-workbench-filterbar wb-lib-commandbar"' in library_source
    assert 'className: "wb-lib-toolbar"' in library_source
    commandbar_css = library_styles.split(".wb-lib-commandbar {", 1)[1].split("}", 1)[0]
    assert "height: 112px;" in commandbar_css
    assert "grid-template-columns: minmax(160px, 360px) minmax(0, 1fr) auto;" in commandbar_css
    assert "grid-template-rows: 42px 48px;" in commandbar_css
    assert ".wb-lib-commandbar > .wb-lib-main-head," in library_styles
    assert ".wb-lib-commandbar > .wb-lib-toolbar { display: contents; }" in library_styles
    assert ".wb-lib-commandbar .wb-lib-search input { font-size: calc(13px * var(--wb-ui-font-scale, 1)); }" in library_styles
    assert 'className: "wb-workbench-filter-tool wb-lib-tool active"' in library_source
    assert 'html[data-theme="dark"] .wb-workbench-filterbar::before {' in styles
    assert ".wb-lib-commandbar::before {" in styles
    assert 'html[data-theme="dark"] .wb-lib-commandbar::before {' in styles
    dark_filterbar_css = styles.split(
        'html[data-theme="dark"] .wb-workbench-filterbar::before {', 1
    )[1].split("}", 1)[0]
    library_glass_css = styles.split(".wb-lib-commandbar::before {", 1)[1].split(
        "}", 1
    )[0]
    dark_library_glass_css = styles.split(
        'html[data-theme="dark"] .wb-lib-commandbar::before {', 1
    )[1].split("}", 1)[0]
    assert "inset: 0 0 -10px;" in dark_filterbar_css
    assert "border-radius: 0;" in dark_filterbar_css
    assert "var(--wb-main-bg, var(--wb-surface)) 96%" in dark_filterbar_css
    assert "blur(34px) saturate(128%)" in dark_filterbar_css
    assert "#000 84%" in dark_filterbar_css
    assert "transparent 100%" in dark_filterbar_css
    assert "inset: 0 0 -6px;" in library_glass_css
    assert "border-radius: 0;" in library_glass_css
    assert "mask-image: linear-gradient(to bottom" in library_glass_css
    assert "radial-gradient" not in library_glass_css
    assert "var(--wb-card-bg)" not in dark_library_glass_css
    assert "inset: 0 0 -10px;" in dark_library_glass_css
    assert "var(--wb-main-bg, var(--wb-surface)) 96%" in dark_library_glass_css
    assert "blur(40px) saturate(138%)" in dark_library_glass_css
    assert ".wb-lib-search," in styles
    assert ".wb-lib-view-toggle," in styles


def test_memory_page_hides_all_scrollbars_without_disabling_scroll():
    styles = workbench_style_source()

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


def test_memory_first_card_clears_the_dark_glass_toolbar():
    styles = workbench_style_source()

    memory_main = styles.split(".wb-mem-main {", 1)[1].split("}", 1)[0]
    dark_memory_main = styles.split(
        'html[data-theme="dark"] .wb-mem-main {', 1
    )[1].split("}", 1)[0]
    memory_scroll = styles.split(".wb-mem-scroll {", 1)[1].split("}", 1)[0]

    assert "--wb-mem-toolbar-overlay-height: 66px;" in memory_main
    assert "--wb-mem-toolbar-clearance: 4px;" in memory_main
    assert "--wb-mem-toolbar-clearance: 12px;" in dark_memory_main
    assert "var(--wb-mem-toolbar-clearance)" in memory_scroll


def test_memory_category_svg_keeps_all_shapes_for_visual_centering():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src/cyrene/workbench/webui/frontend/workbench-memory.jsx").read_text(
        encoding="utf-8"
    )
    icons = source.split("  function svg(", 1)[1].split("  var CATS =", 1)[0]
    script = f"""
const React = {{ createElement: (type, props, ...children) => ({{ type, props, children }}) }};
var h = React.createElement;
eval("function svg(" + {json.dumps(icons)});
const fact = ICON.fact(17);
process.stdout.write(JSON.stringify(fact.children.map(child => child.type)));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )

    assert json.loads(completed.stdout) == ["circle", "path"]


def test_library_workspace_tabs_are_integrated_into_the_floating_right_inspector():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.css").read_text(
        encoding="utf-8"
    )

    right_panel = source.split("function RightPanel(props)", 1)[1].split(
        "function LibraryPdfPreview(props)", 1
    )[0]
    page = source.split("function WorkbenchLibraryPage(props)", 1)[1]

    assert 'selectedItem && h(ItemWorkspace' not in page
    assert 'className: "wb-lib-results"' in page
    assert '{ id: "notes", label: L("library.notes", "Notes")' in right_panel
    assert '{ id: "tags", label: L("library.tags", "Tags")' in right_panel
    assert '{ id: "attachments", label: L("library.attachments", "Attachments")' in right_panel
    assert '{ id: "citation", label: L("library.citation", "Citation")' in right_panel
    assert 'tabId === "notes" && h(NotesWorkspace' in right_panel
    assert 'tabId === "citation" && h(CitationWorkspace' in right_panel
    assert 'window.localStorage.getItem("cyrene.library.viewMode")' in page
    assert 'window.localStorage.setItem("cyrene.library.viewMode", view)' in page
    columns = frontend_module_source("features/knowledge/library-columns.jsx")
    persisted_view_handler = columns.split("function selectLibraryView(nextView, setView)", 1)[1].split(
        "export {", 1
    )[0]
    assert 'window.localStorage.setItem("cyrene.library.viewMode", nextView)' in persisted_view_handler
    assert persisted_view_handler.index("localStorage.setItem") < persisted_view_handler.index(
        "setView(nextView)"
    )
    assert 'onClick: function () { selectLibraryView("table", setView); }' in page
    assert 'onClick: function () { selectLibraryView("grid", setView); }' in page
    card_grid_css = styles.split(".wb-lib-card-grid {", 1)[1].split("}", 1)[0]
    card_css = styles.split(".wb-lib-card {", 1)[1].split("}", 1)[0]
    card_body_css = styles.split(".wb-lib-card-body {", 1)[1].split("}", 1)[0]
    card_foot_css = styles.split(".wb-lib-card-foot {", 1)[1].split("}", 1)[0]
    assert "repeat(auto-fill, minmax(240px, 1fr))" in card_grid_css
    assert "height: 168px;" in card_css
    assert "overflow: hidden;" in card_css
    assert "height: 100%;" in card_body_css
    assert "overflow: hidden;" in card_body_css
    assert "flex: 0 0 16px;" in card_foot_css
    compact_css = styles.split("@media (max-width: 760px) {", 1)[1].split(
        "@media (prefers-reduced-motion: reduce)", 1
    )[0]
    assert ".wb-lib-card-grid { grid-template-columns: 1fr; }" not in compact_css


def test_workbench_chat_interrupt_waits_for_server_and_uses_live_status_everywhere():
    source = workbench_chat_source()
    runtime_hooks = frontend_module_source("features/chat/runtime-page-hooks.jsx")

    runtime_interrupt = source.split(
        "  function interrupt(chatId, model) {", 1
    )[1].split("\n  function deferSend", 1)[0]
    side_status = source.split("function WbcOverviewTab", 1)[1].split(
        "function wbcBlockLabel", 1
    )[0]

    assert "Promise.resolve(model.interrupt(chatId))" in runtime_interrupt
    assert ".then(function (result)" in runtime_interrupt
    assert ".finally(function () {" not in runtime_interrupt
    assert "abort(chatId);" in runtime_interrupt
    assert 'publishLifecycle(chatId, "cancelled", {});' in runtime_interrupt
    assert "clear(chatId);" in runtime_interrupt
    assert 'fire("onInterrupted", chatId);' in runtime_interrupt
    assert 'fire("onInterrupted", chatId);' in source
    assert 'return !previous || previous.id !== chatId ? previous : { ...previous, status: "idle" };' in runtime_hooks
    assert "runtime ?" in side_status
    assert 'className={"wbc-overview-status" + (runtime ? " live" : "")}' in side_status
    assert 'chat.status === "running"' not in side_status


def test_workbench_chat_interrupt_settles_runtime_during_reconnect_gap():
    result = _run_workbench_runtime_js(
        """
(async () => {
  let interrupted = 0;
  const model = {
    sendMessage: () => Promise.resolve(),
    reconnectRun: () => new Promise(() => {}),
    interrupt: () => Promise.resolve({ ok: true })
  };
  WorkbenchChatRuntimes.setHooks({
    onInterrupted: () => { interrupted += 1; }
  });
  await WorkbenchChatRuntimes.start("chat-stop", { message: "hello" }, model);
  const before = WorkbenchChatRuntimes.get("chat-stop");
  await WorkbenchChatRuntimes.interrupt("chat-stop", model);
  return {
    wasReconnecting: !!(before && before.reconnecting),
    runningAfterInterrupt: WorkbenchChatRuntimes.isRunning("chat-stop"),
    interrupted
  };
})()
"""
    )

    assert result == {
        "wasReconnecting": True,
        "runningAfterInterrupt": False,
        "interrupted": 1,
    }


def test_workbench_chat_restores_project_cache_before_background_refresh():
    source = workbench_chat_source()

    assert "function wbcChatCache()" in source
    assert "chatCache.lists[requestedProjectId]" in source
    assert "setLoading(!cachedList);" in source
    assert "setActiveChat(cachedChat);" in source
    assert "setChatLoading(!cachedChat);" in source


def test_remote_chat_change_projects_summary_without_refreshing_the_open_transcript():
    source = workbench_chat_source()
    live_events = frontend_module_source("features/chat/live-event-controller.jsx")

    event_block = live_events.split('if (event.type === "workbench_chat_changed") {', 1)[1].split(
        'if (event.type === "workspace_changes")', 1
    )[0]
    summary_branch, fallback_branch = event_block.split(
        "if (context.applyChatSummaryEvent(event)) return;", 1
    )
    assert "applyChatSummaryEvent" not in summary_branch
    assert "wbcScheduleRemoteChatRefresh(context, event);" in fallback_branch
    refresh_branch = live_events.split("function wbcScheduleRemoteChatRefresh", 1)[1].split(
        "function wbcApplyProactiveMessage", 1
    )[0]
    assert "remoteChangedChatIdsRef.current.add(changedChatId || \"*\")" in refresh_branch
    assert 'context.refreshChats("");' in refresh_branch
    assert 'changedChatIds.has(openChatId)' in refresh_branch
    assert "context.setLoadRevision(function (value) { return value + 1; });" in refresh_branch

    projection = source.split("  function applyChatSummaryEvent(event) {", 1)[1].split(
        "\n  // Initial load + project switch.", 1
    )[0]
    assert "chatCache.details[chatId] = mergeProjection(cached)" in projection
    assert "setActiveChat(function (current)" in projection
    assert "setChats(function (current)" in projection
    assert "beginChatListRequest" in projection
    assert "event.userMessage" in projection
    assert "event.assistantMessages" in projection
    assert "wbcMergeChronologicalMessages" in projection
    assert "model.getChat" not in projection
    assert "refreshChats" not in projection


def test_remote_chat_refresh_and_notification_navigation_use_the_latest_project():
    source = workbench_chat_source()
    live_events = frontend_module_source("features/chat/live-event-controller.jsx")

    page_setup = source.split("function WorkbenchChatPage", 1)[1].split(
        "  function refreshChats", 1
    )[0]
    refresh = source.split("  function refreshChats(selectId) {", 1)[1].split(
        "\n  // Initial load + project switch.", 1
    )[0]
    navigation = source.split("  function applyPendingChatSelection() {", 1)[1].split(
        "\n  useWbcEffect(function () {", 1
    )[0]
    remote_events = live_events.split(
        'if (event.type === "workbench_chat_changed") {', 1
    )[1].split('if (event.type === "workspace_changes")', 1)[0]
    remote_refresh = live_events.split("function wbcScheduleRemoteChatRefresh", 1)[1].split(
        "function wbcApplyProactiveMessage", 1
    )[0]

    assert "projectIdRef.current = projectId;" in page_setup
    assert 'var requestedProjectId = String(projectIdRef.current || "");' in refresh
    assert "var requestedProjectId = projectId;" not in refresh
    assert "refreshChats(targetId);" in navigation
    assert "if (context.applyChatSummaryEvent(event)) return;" in remote_events
    assert 'context.refreshChats("");' in remote_refresh


def test_background_chat_completion_updates_detail_cache_before_runtime_is_cleared():
    source = workbench_chat_source()
    hooks = frontend_module_source("features/chat/runtime-page-hooks.jsx")

    saved_hook = hooks.split(
        "function wbcRuntimeAssistantSaved(context, chatId, assistantMessages, terminalEvent) {", 1
    )[1].split("function wbcRuntimeAgentArtifact", 1)[0]
    resync_hook = hooks.split("function wbcRuntimeResync(context, chatId) {", 1)[1].split(
        "function wbcRuntimePageHooks", 1
    )[0]

    assert "var terminalSummary = terminalEvent && terminalEvent.chatSummary;" in saved_hook
    assert "function mergeTerminal(chat)" in saved_hook
    assert "context.chatCache.details[chatId] = mergeTerminal(cachedChat);" in saved_hook
    assert 'wbcSettleChatListItem(projected, "completed", terminalEvent)' in saved_hook
    assert "beginChatListRequest(currentProjectId)" in saved_hook
    assert "onSettled: function" not in source
    assert "context.isCurrentChatHydration(chatId, hydrationSequence)" in resync_hook
    assert "context.chatCache.details[chatId] = wbcPreserveLiveTimelineAnchors(cachedChat, chat, runtime);" in resync_hook


def test_saved_assistant_messages_merge_reasoning_into_stale_background_chat():
    source = workbench_chat_source()
    merge_source = "function wbcMergeChronologicalMessages(" + source.split(
        "function wbcMergeChronologicalMessages(", 1
    )[1].split("function wbcRuntimeSegmentMessages", 1)[0]
    confirm_source = "function wbcConfirmOptimisticMessage(" + source.split(
        "function wbcConfirmOptimisticMessage(", 1
    )[1].split("function wbcPreserveLiveTimelineAnchors", 1)[0]
    script = f"""
eval({json.dumps(confirm_source)});
eval({json.dumps(merge_source)});
const stale = {{
  id: "background",
  status: "running",
  messages: [{{ id: "user_1", role: "user", content: "question", createdAt: "2026-01-01T00:00:00Z" }}]
}};
const saved = [{{
  id: "assistant_1",
  role: "assistant",
  content: "answer",
  thinking: "reasoning trace",
  createdAt: "2026-01-01T00:00:01Z"
}}];
const first = wbcMergeSavedAssistantMessages(stale, saved);
const second = wbcMergeSavedAssistantMessages(first, saved);
process.stdout.write(JSON.stringify({{
  status: second.status,
  count: second.messages.length,
  thinking: second.messages[1].thinking
}}));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )

    assert json.loads(completed.stdout) == {
        "status": "idle",
        "count": 2,
        "thinking": "reasoning trace",
    }

    refresh = source.split("  function refreshChats(selectId) {", 1)[1].split(
        "\n  // Initial load + project switch.", 1
    )[0]
    create = frontend_module_source("features/chat/chat-action-controller.jsx").split(
        "function wbcHandleCreateChat(context) {", 1
    )[1].split("function wbcHandleRenameChat", 1)[0]
    assert "var selectionAtRequest = String(activeChatIdRef.current || \"\");" in refresh
    assert "var requestSequence = beginChatListRequest(requestedProjectId);" in refresh
    assert "isCurrentChatListRequest(requestedProjectId, requestSequence)" in refresh
    assert "wbcResolveRefreshedChatSelection(" in refresh
    assert "if (targetId !== null) selectChat(targetId);" in refresh
    assert "selectChat(chat.id);" in create


def test_workbench_chat_has_long_conversation_navigation_and_bottom_return():
    source = workbench_chat_source()
    styles = workbench_style_source()

    assert "function WbcConversationNavigator" in source
    assert 'data-wbc-nav-item={nav ? "true" : undefined}' in source
    assert 'className="wbc-conversation-nav"' in source
    assert "scrollToConversationBottom" in source
    assert 'className="wbc-scroll-to-bottom"' in source
    assert 'navigation={msg.role === "user" ? wbcUserMessageNavigationMeta(msg) : null}' in source
    assert "var visible = markers.length > 5;" in source
    assert 'className="wbc-conversation-nav-trigger"' in source
    assert 'className="wbc-conversation-nav-panel"' in source
    assert 'className="wbc-conversation-nav-list"' in source
    navigator = source.split("class WbcConversationNavigatorObserver", 1)[1].split(
        "export { WbcConversationNavigator }", 1
    )[0]
    assert "this.itemsDirty = true;" in navigator
    assert "if (this.itemsDirty) this.refreshItems();" in navigator
    assert "this.centers[index] = this.items[index].offsetTop + this.items[index].offsetHeight / 2;" in navigator
    assert "if (current.visible === visible && current.active === active" in navigator
    assert "hoveredIndex" not in source
    assert "var contentPreview = wbcNavigationPreview(msg.content || \"\");" in source
    assert "var attachmentPreview = attachmentTypes.slice(0, 2).join(\" · \");" in source
    assert "contentPreview ? prefix + \": \" + preview : preview" in source
    assert '"workbenchChat.attachmentType.image": "图片"' in workbench_i18n_source()
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
    source = workbench_chat_source()
    styles = workbench_style_source()

    assert 'effectiveMode === "maximized" && !hasNativeChatOverlay && (' in source
    assert 'browserWindowMode === "maximized" ? " browser-window-maximized" : ""' in source
    released_stage_css = styles.split(
        ".wbc-thread-stage.browser-window-maximized {", 1
    )[1].split("}", 1)[0]
    assert "width: 100%;" in released_stage_css
    assert "transition: none;" in released_stage_css
    assert "will-change: auto;" in released_stage_css
    assert 'className="wbc-browser-fullscreen-chat"' in source
    fullscreen_chat = source.split(
        "function useWbcFullscreenBrowserChat", 1
    )[1].split("function useWbcConversationRuntime", 1)[0]
    reply_effects = source.split(
        "function useWbcFullscreenReplyEffects", 1
    )[1].split("function wbcNativeChatOverlayColors", 1)[0]
    native_overlay_effects = source.split(
        "function useWbcNativeChatOverlayEffects", 1
    )[1].split("function useWbcFullscreenBrowserChat", 1)[0]
    assert "fullscreenStatusRequested" in fullscreen_chat
    assert "fullscreenFinalReply" in fullscreen_chat
    assert "latestAssistantReplyText" in fullscreen_chat
    # The baseline now belongs to the fullscreen-chat hook. Verify the reply
    # identity contract instead of freezing its former parent-local name.
    assert 'var replyBaselineRef = useWbcRef("");' in fullscreen_chat
    assert 'replyBaselineRef.current = String(latestAssistantReplyId || "");' in fullscreen_chat
    assert "replyId !== options.replyBaselineRef.current" in reply_effects
    assert "}, 5000);" in reply_effects
    assert "wbcBrowserFullscreenStatusText(runtime)" in source
    assert "setChatOverlay" in source
    assert "onChatOverlayAction" in source
    assert "hasNativeChatOverlay" in source
    assert 'document.querySelector(".workbench-shell") || document.documentElement' in source
    assert 'attributeFilter: ["data-theme", "style"]' in source
    assert 'window.addEventListener("cyrene-tweak-accent-change", refreshTheme)' in native_overlay_effects
    assert "var [themeRevision, setThemeRevision] = useWbcState(0);" in fullscreen_chat
    assert "options.setThemeRevision(function (value) { return value + 1; });" in native_overlay_effects
    assert "options.completedReply, options.themeRevision]" in native_overlay_effects
    assert ".wbc-browser-fullscreen-composer" in styles
    composer = styles.split(".wbc-browser-fullscreen-composer {", 1)[1].split("}", 1)[0]
    focused_composer = styles.split(".wbc-browser-fullscreen-composer:focus-within {", 1)[1].split("}", 1)[0]
    assert "box-shadow: 0 3px 12px rgba(9, 17, 30, 0.04);" in composer
    assert "box-shadow: 0 0 0 2px color-mix(in srgb, var(--wb-accent) 7%, transparent);" in focused_composer
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
    assert "const parent = this.ownerWindow()?.contentView || null" in main
    assert "this.syncChatOverlay(parent, true);" in main
    assert "this.syncChatOverlay(win.contentView, true);" in main
    assert "Failed to attach browser chat overlay" in main
    assert "const bottomOffset = 56" in main
    assert "this.bounds.height - height - bottomOffset" in main
    assert "browser:set-chat-overlay" in main
    assert "browser-chat-overlay:action" in main
    assert "form:focus-within { border-color: color-mix(in srgb, var(--accent, #6d5dfc) 36%, var(--line, #d8dce4)); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent, #6d5dfc) 7%, transparent); }" in main
    assert "statusComplete" in main
    assert "setChatOverlay:" in preload
    assert "onChatOverlayAction:" in preload
    assert "contextBridge.exposeInMainWorld('browserChatOverlay'" in overlay_preload
    assert '"browser-chat-overlay-preload.js"' in package


def _run_workbench_model_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    runtime_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "platform" / "runtime.jsx"
    model_source = re.sub(
        r"^(?:import|export)\s+.*$",
        "",
        (root / "src/cyrene/workbench/webui/frontend/workbench-model.jsx").read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    script = f"""
const fs = require("fs");
global.window = {{}};
eval(fs.readFileSync({json.dumps(str(runtime_path))}, "utf8"));
var workbenchServices = new Proxy({{}}, {{
  get: (_target, name) => () => window.CyreneUI.require(String(name))
}});
eval({json.dumps(model_source)});
window.WorkbenchModel = window.CyreneUI.require("model");
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _run_workbench_i18n_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    runtime_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "platform" / "runtime.jsx"
    i18n_source = re.sub(
        r"^import\s+.*$",
        "",
        workbench_i18n_source(),
        flags=re.MULTILINE,
    )
    i18n_source = re.sub(r"^export\s+", "", i18n_source, flags=re.MULTILINE)
    script = f"""
const fs = require("fs");
global.window = {{}};
global.localStorage = {{ getItem: () => "", setItem: () => {{}} }};
global.navigator = {{ language: "zh-CN" }};
global.document = {{ documentElement: {{ dataset: {{}} }} }};
global.React = {{ useState: () => [0, () => {{}}], useEffect: () => {{}} }};
eval(fs.readFileSync({json.dumps(str(runtime_path))}, "utf8"));
eval({json.dumps(i18n_source)});
window.WorkbenchI18n = window.CyreneUI.require("i18n");
window.WorkbenchI18n.setLang("zh");
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-"], input=script, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def _run_workbench_trace_i18n_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    runtime_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "platform" / "runtime.jsx"
    i18n_source = re.sub(
        r"^import\s+.*$",
        "",
        workbench_i18n_source(),
        flags=re.MULTILINE,
    )
    i18n_source = re.sub(r"^export\s+", "", i18n_source, flags=re.MULTILINE)
    core_source = frontend_module_source("features/chat/core.jsx")
    presentation = frontend_module_source("features/chat/presentation.jsx")
    helper_source = "function wbcT(" + core_source.split(
        "function wbcT(", 1
    )[1].split("export {", 1)[0]
    helper_source += "\nfunction wbcFormatToolParameter(" + presentation.split(
        "function wbcFormatToolParameter(", 1
    )[1].split("function wbcThinkingPhrases", 1)[0]
    script = f"""
const fs = require("fs");
global.window = {{}};
global.localStorage = {{ getItem: () => "", setItem: () => {{}} }};
global.navigator = {{ language: "zh-CN" }};
global.document = {{ documentElement: {{ dataset: {{}} }} }};
global.React = {{ useState: () => [0, () => {{}}], useEffect: () => {{}} }};
eval(fs.readFileSync({json.dumps(str(runtime_path))}, "utf8"));
eval({json.dumps(i18n_source)});
var workbenchServices = {{ i18n: () => window.CyreneUI.require("i18n") }};
eval({json.dumps(helper_source)});
window.WorkbenchI18n = window.CyreneUI.require("i18n");
window.WorkbenchI18n.setLang("zh");
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-"], input=script, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def _run_workbench_runtime_js(expression: str):
    agent_events = frontend_module_source("features/chat/agent-events.jsx")
    presentation = frontend_module_source("features/chat/presentation.jsx")
    timeline = frontend_module_source("features/chat/runtime-timeline.jsx")
    runtime = frontend_module_source("features/chat/file-resources.jsx")
    tool_display_name_source = "function wbcAgentToolDisplayName(" + agent_events.split(
        "function wbcAgentToolDisplayName(", 1
    )[1].split("function wbcAgentToolPayload", 1)[0]
    args_preview_source = "function wbcFormatToolParameter(" + presentation.split(
        "function wbcFormatToolParameter(", 1
    )[1].split("function wbcThinkingPhrases", 1)[0]
    timeline_source = "function wbcConfirmOptimisticMessage(" + timeline.split(
        "function wbcConfirmOptimisticMessage(", 1
    )[1].split("export {", 1)[0]
    runtime_source = "function wbcRuntimeToolEvent(" + runtime.split(
        "function wbcRuntimeToolEvent(", 1
    )[1].split("// Page", 1)[0]
    script = f"""
global.window = {{
  CyreneUI: {{
    require: () => ({{
      subscribe: (handler) => {{ global.__wbcSseHandler = handler; return () => {{}}; }}
    }})
  }}
}};
var workbenchServices = {{
  events: () => window.CyreneUI.require("events")
}};
function wbcT(_key, fallback) {{ return fallback; }}
function wbcSubagentStatusText(status) {{ return String(status || ""); }}
eval({json.dumps(tool_display_name_source)});
eval({json.dumps(args_preview_source)});
eval({json.dumps(timeline_source)});
eval({json.dumps(runtime_source)});
const result = ({expression});
Promise.resolve(result).then(function (value) {{
  process.stdout.write(JSON.stringify(value));
}}).catch(function (error) {{
  console.error(error && error.stack || error);
  process.exitCode = 1;
}});
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _run_workbench_timeline_js(expression: str):
    source = frontend_module_source("features/chat/runtime-timeline.jsx")
    timeline_source = "function wbcConfirmOptimisticMessage(" + source.split(
        "function wbcConfirmOptimisticMessage(", 1
    )[1].split("export {", 1)[0]
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


def test_workbench_hydration_keeps_live_user_turn_before_runtime_placeholder():
    result = _run_workbench_timeline_js(
        """
(() => {
  const startedAt = Date.parse("2026-07-15T09:27:52.100Z");
  const previous = {
    messages: [{
      id: "pending_user",
      role: "user",
      content: "你好",
      createdAt: new Date(startedAt).toISOString(),
      optimistic: true,
      clientRequestId: "send_1"
    }]
  };
  const hydrated = {
    messages: [{
      id: "saved_user",
      role: "user",
      content: "你好",
      createdAt: "2026-07-15T09:27:56.000000+00:00",
      clientRequestId: "send_1"
    }]
  };
  const runtime = {
    chatId: "chat_1",
    startedAt,
    activities: [],
    clientRequestId: "send_1",
    userMessages: previous.messages
  };
  const reconciled = wbcPreserveLiveTimelineAnchors(previous, hydrated, runtime);
  const merged = wbcMergeChronologicalMessages(
    reconciled.messages,
    wbcRuntimeTimelineMessages(runtime)
  );
  return {
    ids: merged.map(item => item.id),
    createdAt: reconciled.messages[0].createdAt,
    serverCreatedAt: reconciled.messages[0].serverCreatedAt
  };
})()
"""
    )

    assert result == {
        "ids": ["saved_user", "runtime_heartbeat_chat_1", "runtime_activity_1"],
        "createdAt": "2026-07-15T09:27:52.100Z",
        "serverCreatedAt": "2026-07-15T09:27:56.000000+00:00",
    }


def test_workbench_hydration_deduplicates_pending_question_answer_after_navigation():
    result = _run_workbench_timeline_js(
        """
(() => {
  const startedAt = Date.parse("2026-07-15T09:27:52.100Z");
  const optimisticAnswer = {
    id: "answer_pending_1",
    role: "user",
    content: "继续",
    createdAt: new Date(startedAt).toISOString(),
    answerToQuestionId: "question_1",
    optimistic: true
  };
  const hydrated = {
    messages: [{
      id: "msg_saved_answer",
      role: "user",
      content: "继续",
      createdAt: "2026-07-15T09:27:52.180000+00:00",
      answerToQuestionId: "question_1"
    }]
  };
  const runtime = {
    chatId: "chat_1",
    startedAt,
    activities: [],
    userMessages: [optimisticAnswer]
  };
  const reconciled = wbcPreserveLiveTimelineAnchors(
    { messages: [optimisticAnswer] },
    hydrated,
    runtime
  );
  return {
    ids: reconciled.messages.map(item => item.id),
    answerCount: reconciled.messages.filter(item => (
      item.answerToQuestionId === "question_1"
    )).length,
    optimistic: reconciled.messages[0].optimistic,
    createdAt: reconciled.messages[0].createdAt,
    serverCreatedAt: reconciled.messages[0].serverCreatedAt
  };
})()
"""
    )

    assert result == {
        "ids": ["msg_saved_answer"],
        "answerCount": 1,
        "optimistic": False,
        "createdAt": "2026-07-15T09:27:52.100Z",
        "serverCreatedAt": "2026-07-15T09:27:52.180000+00:00",
    }


def test_workbench_live_event_deduplicates_pending_question_answer_before_hydration():
    result = _run_workbench_timeline_js(
        """
(() => {
  const optimisticAnswer = {
    id: "answer_pending_1",
    role: "user",
    content: "红熊猫动物",
    createdAt: "2026-07-15T09:27:52.100Z",
    answerToQuestionId: "question_1",
    optimistic: true
  };
  const savedAnswer = {
    id: "msg_saved_answer",
    role: "user",
    content: "红熊猫动物",
    createdAt: "2026-07-15T09:27:52.180000+00:00",
    answerToQuestionId: "question_1"
  };
  const merged = wbcMergeChronologicalMessages([optimisticAnswer], [savedAnswer]);
  return {
    ids: merged.map(item => item.id),
    answerCount: merged.filter(item => item.answerToQuestionId === "question_1").length,
    optimistic: merged[0].optimistic,
    createdAt: merged[0].createdAt,
    serverCreatedAt: merged[0].serverCreatedAt
  };
})()
"""
    )

    assert result == {
        "ids": ["msg_saved_answer"],
        "answerCount": 1,
        "optimistic": False,
        "createdAt": "2026-07-15T09:27:52.100Z",
        "serverCreatedAt": "2026-07-15T09:27:52.180000+00:00",
    }


def test_workbench_hydration_cannot_remove_the_live_user_turn():
    result = _run_workbench_timeline_js(
        """
(() => {
  const startedAt = Date.parse("2026-07-15T09:27:52.100Z");
  const liveUser = {
    id: "pending_user",
    role: "user",
    content: "你好",
    createdAt: new Date(startedAt).toISOString(),
    optimistic: true,
    clientRequestId: "send_1"
  };
  const runtime = {
    chatId: "chat_1",
    startedAt,
    activities: [],
    clientRequestId: "send_1",
    userMessages: [liveUser]
  };
  const reconciled = wbcPreserveLiveTimelineAnchors(
    { messages: [liveUser] },
    { messages: [] },
    runtime
  );
  const merged = wbcMergeChronologicalMessages(
    reconciled.messages,
    wbcRuntimeTimelineMessages(runtime)
  );
  return {
    ids: merged.map(item => item.id),
    content: merged[0].content,
    optimistic: merged[0].optimistic
  };
})()
"""
    )

    assert result == {
        "ids": ["pending_user", "runtime_heartbeat_chat_1", "runtime_activity_1"],
        "content": "你好",
        "optimistic": True,
    }

    source = workbench_chat_source()
    hooks = frontend_module_source("features/chat/runtime-page-hooks.jsx")
    selection_hydration = source.split(
        "var hydrationSequence = beginChatHydration(activeChatId);", 1
    )[1].split("model.getSubagents", 1)[0]
    resync_hydration = hooks.split(
        "function wbcRuntimeResync(context, chatId) {", 1
    )[1].split("function wbcRuntimePageHooks", 1)[0]
    assert "isCurrentChatHydration(activeChatId, hydrationSequence)" in selection_hydration
    assert "runtimeEngine.get(activeChatId)" in selection_hydration
    assert "var hydrationSequence = context.beginChatHydration(chatId);" in resync_hydration
    assert "if (!context.isCurrentChatHydration(chatId, hydrationSequence)) return;" in resync_hydration
    assert "wbcPreserveLiveTimelineAnchors" in resync_hydration
    assert "var runtime = context.runtimeEngine.get(chatId);" in resync_hydration


def test_workbench_retry_hydration_cannot_restore_removed_model_output():
    result = _run_workbench_timeline_js(
        """
(() => {
  const hydrated = {
    messages: [
      { id: "user-retry", role: "user", content: "do it" },
      { id: "old-activity", role: "assistant", content: "" },
      { id: "old-reply", role: "assistant", content: "old answer" }
    ]
  };
  const runtime = {
    chatId: "chat-retry",
    retry: true,
    retryTruncateAfterMessageId: "user-retry",
    userMessages: []
  };
  const reconciled = wbcPreserveLiveTimelineAnchors(
    { messages: [{ id: "user-retry", role: "user", content: "do it" }] },
    hydrated,
    runtime
  );
  return reconciled.messages.map(item => item.id);
})()
"""
    )

    assert result == ["user-retry"]

    source = workbench_chat_source()
    load_effect = source.split("// Load the full transcript when the selection changes.", 1)[1].split(
        "// Viewer / content tabs belong to one conversation", 1
    )[0]
    cache_adoption = load_effect.split(
        "var cachedChat = activeChatId ?", 1
    )[1].split("var hydrationSequence", 1)[0]
    assert cache_adoption.index("wbcPreserveLiveTimelineAnchors(") < cache_adoption.index(
        "setActiveChat(cachedChat)"
    )
    assert "runtimeEngine.get(activeChatId)" in cache_adoption


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


def test_workbench_reused_tool_call_id_starts_a_distinct_occurrence():
    result = _run_workbench_timeline_js(
        """
(() => {
  const completed = [{
    kind: "tool",
    toolCallId: "search_1",
    text: "web_search",
    preview: "first query",
    status: "completed"
  }];
  const secondStart = {
    kind: "tool",
    toolCallId: "search_1",
    text: "web_search",
    preview: "second query",
    status: "running"
  };
  const startMerge = wbcMergeToolOccurrence(completed, secondStart, false);
  const withSecond = startMerge.items.concat([secondStart]);
  const secondDone = { ...secondStart, status: "completed" };
  const completionMerge = wbcMergeToolOccurrence(withSecond, secondDone, true);
  return {
    startMatched: startMerge.matched,
    completionMatched: completionMerge.matched,
    count: completionMerge.items.length,
    previews: completionMerge.items.map(item => item.preview),
    statuses: completionMerge.items.map(item => item.status)
  };
})()
"""
    )

    assert result == {
        "startMatched": False,
        "completionMatched": True,
        "count": 2,
        "previews": ["first query", "second query"],
        "statuses": ["completed", "completed"],
    }


def test_workbench_keeps_live_subagent_logs_across_silent_refreshes():
    root = Path(__file__).resolve().parent.parent
    source = frontend_module_source("features/session/activity.jsx")

    assert 'data.type === "subagent_update"' in source
    assert 'plugin_context_data.get("session_id")' in (
        root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_subagent" / "manager.py"
    ).read_text(encoding="utf-8")
    assert "if (!events.some(function (item) { return item && item.id === event.id; }))" in source
    assert "events.push(event);" in source
    assert "data.message" in source


def test_data_refresh_cancels_superseded_requests_and_is_event_driven():
    root = Path(__file__).resolve().parent.parent
    source = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "platform" / "data-store.jsx"
    ).read_text(encoding="utf-8")

    for name, endpoint in (
        ("sessions", "/api/workbench/sessions"),
        ("status", "/api/status"),
        ("dashboard", "/api/dashboard?tz="),
    ):
        assert f"if (__{name}RequestController) __{name}RequestController.abort();" in source
        assert f"__{name}RequestController = controller;" in source
        assert f"__{name}RequestController === controller" in source
        request = source.split(f'fetch("{endpoint}', 1)[1].split(";", 1)[0]
        assert "signal: controller.signal" in request

    # Sequence guards reject stale responses. Runtime refreshes are driven by
    # SSE; there are no delayed safety retries or periodic fallback fetches.
    assert "seq !== __sessionsRequestSeq" in source
    assert "seq !== __statusRequestSeq" in source
    assert "seq !== __dashboardRequestSeq" in source
    assert "scheduleRealtimeRefresh();" in source
    # Conversation summaries are projected by their dedicated consumers; they
    # must not fan out into unrelated sessions/status/dashboard readbacks.
    assert '"workbench_chat_changed"' not in source
    assert "__refreshSafetyTimer" not in source
    assert "refreshFallbackData" not in source


def test_workbench_module_pages_are_kept_alive_without_hidden_file_drop():
    root = Path(__file__).resolve().parent.parent
    shell = workbench_shell_source()
    chat = workbench_chat_source()
    library = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")

    assert "mountedPages" in shell
    assert "var WorkbenchStableSurface = React.memo(" in shell
    assert "return !prev.active && !next.active;" in shell
    assert "<WorkbenchStableSurface active={presentation.isChat || presentation.isBoard}>" in shell
    assert '<WorkbenchStableSurface active={p.isKnowledge} enterMotion={true}>' in shell
    assert '<WorkbenchStableSurface active={p.isSchedule} enterMotion={true}>' in shell
    assert '<WorkbenchStableSurface active={p.isMemory} enterMotion={true}>' in shell
    assert "workspaceContent: sharedWorkspace" in shell
    assert "WorkbenchBoardModuleSurface" not in shell
    assert "function WorkbenchChatPage({ active, project" in chat
    assert "}, !!(isActive && project));" in chat
    assert "!!(isActive && project)" in chat
    assert "function WorkbenchLibraryPage(props)" in library
    assert "props.active !== false" in library


def test_workbench_memory_skill_learning_selects_tool_chains(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    styles = workbench_style_source()
    i18n = workbench_i18n_source()
    pattern = (
        root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_skills" / "application_service.py"
    ).read_text(encoding="utf-8")

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
    from cyrene.plugins.builtin.cyrene_skills import orchestrator as learning
    from cyrene.plugins.builtin.cyrene_skills.application_service import (
        LearningApplicationService,
        MediaRepository,
        ProjectResolver,
        ToolChainProjection,
    )

    image_path = tmp_path / "data" / "behavior-media" / "turn_1" / "capture.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    captured = {}

    async def list_tool_chains(project_ids, limit):
        captured.update(project_ids=project_ids, limit=limit)
        return [{
            "id": "chain_1",
            "summary": {"total_steps": 1},
            "chain": [{
                "tool": "browser.screenshot",
                "output_summary": json.dumps({"path": str(image_path)}),
            }],
        }]

    async def status():
        return {"phase": "evolve", "state": "ready"}

    monkeypatch.setattr(learning, "list_tool_chains", list_tool_chains)
    media = MediaRepository(tmp_path / "data")
    service = LearningApplicationService(
        ProjectResolver(lambda value: "project_1" if value == "workspace-key" else None),
        media,
        ToolChainProjection(media),
        status,
    )
    payload = asyncio.run(service.chains("workspace-key", 80))

    assert captured == {"project_ids": ["project_1", "workspace-key"], "limit": 80}
    assert payload["tool_chains"][0]["id"] == "chain_1"
    screenshot = payload["tool_chains"][0]["screenshots"][0]
    assert screenshot["path"] == str(image_path)
    assert screenshot["url"].startswith("/api/tool-chain-media?path=")
    assert "ListScripts" not in pattern
    assert "RunScript" not in pattern
    assert "LearnSkill" not in pattern


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
    source = workbench_chat_source()
    i18n = workbench_i18n_source()

    assert "var WorkbenchChatRuntimes = (function () {" in source
    assert "var runtimes = {};" in source
    assert "var aborts = {};" in source
    assert 'window.CyreneUI.chat = window.CyreneUI.register("chat", chatService)' in source
    assert "Runtimes: WorkbenchChatRuntimes" in source
    assert "var runtimeEngine = WorkbenchChatRuntimes;" in source
    assert "subscribeSummary: subscribeSummary" in source
    page = source.split("function WorkbenchChatPage(", 1)[1].split(
        "// Conversation rail (column 2)", 1
    )[0]
    runtime_hook = source.split("function useWbcConversationRuntime(", 1)[1].split(
        "function useWbcConversationProjection(", 1
    )[0]
    main = source.split("function WbcMain(", 1)[1].split(
        "function wbcQuestionOptionValue(", 1
    )[0]
    assert "runtimeEngine.subscribeSummary(applyRuntimeSnapshot)" in page
    assert "runtimeEngine.subscribe(applyRuntimeSnapshot)" not in page
    assert "runtimeEngine.subscribe(applyRuntimeSnapshot)" in runtime_hook
    assert "var running = !!runtime;" in main
    main_props = page.split("<WbcMain", 1)[1].split("/>", 1)[0]
    assert "runtimeEngine={runtimeEngine}" in main_props
    assert "runtime={activeRuntime}" not in main_props
    assert "running={activeRunning}" not in main_props
    assert "runtimeEngine.get(activeChatId)" in source
    assert "wbcSameRuntimePresence(current, nextPresence)" in source
    assert "runtimeEngine.start(chatId, preparedInput, model)" in source
    assert "runningChatIds={runningChatIds}" in source
    assert "if (!chatId || runtimes[chatId]) return null;" in source
    assert "otherRunning" not in source
    assert "workbenchChat.lockedByOther" not in source
    assert "workbenchChat.lockedByOther" not in i18n


def test_workbench_runtime_summary_ignores_token_only_updates():
    result = _run_workbench_runtime_js(
        """
(() => {
  let handlers = null;
  let summaries = 0;
  WorkbenchChatRuntimes.subscribeSummary(() => { summaries += 1; });
  WorkbenchChatRuntimes.start("chat-1", { message: "hello" }, {
    sendMessage: (_chatId, _input, nextHandlers) => {
      handlers = nextHandlers;
      return new Promise(() => {});
    }
  });
  const afterStart = summaries;
  handlers.onReplyDelta("one");
  handlers.onReplyDelta("two");
  handlers.onReplyDone("onetwo");
  const afterReply = summaries;
  WorkbenchChatRuntimes.clear("chat-1");
  return { afterStart, afterReply, afterClear: summaries };
})()
"""
    )

    assert result == {"afterStart": 1, "afterReply": 2, "afterClear": 3}


def test_workbench_retry_boundary_survives_page_unmount_and_reconnect_ack():
    result = _run_workbench_runtime_js(
        """
(() => {
  let handlers = null;
  WorkbenchChatRuntimes.start(
    "chat-retry",
    { retry: true, retryTruncateAfterMessageId: "user-local" },
    {
      sendMessage: (_chatId, _input, nextHandlers) => {
        handlers = nextHandlers;
        return new Promise(() => {});
      }
    }
  );
  const beforeUnmount = WorkbenchChatRuntimes.get("chat-retry").retryTruncateAfterMessageId;
  WorkbenchChatRuntimes.setHooks(null);
  const afterUnmount = WorkbenchChatRuntimes.get("chat-retry").retryTruncateAfterMessageId;
  handlers.onAck({
    retry: true,
    truncateAfterMessageId: "user-server"
  });
  const afterAck = WorkbenchChatRuntimes.get("chat-retry").retryTruncateAfterMessageId;
  WorkbenchChatRuntimes.clear("chat-retry");
  return { beforeUnmount, afterUnmount, afterAck };
})()
"""
    )

    assert result == {
        "beforeUnmount": "user-local",
        "afterUnmount": "user-local",
        "afterAck": "user-server",
    }


def test_workbench_retry_boundary_removes_only_the_retried_model_output():
    result = _run_workbench_timeline_js(
        """
(() => {
  const messages = [
    { id: "user-before", role: "user" },
    { id: "assistant-before", role: "assistant" },
    { id: "user-retry", role: "user" },
    { id: "old-rehydrated-reasoning", role: "assistant" },
    { id: "old-rehydrated-reply", role: "assistant" },
    { id: "user-later", role: "user" },
    { id: "assistant-later", role: "assistant" }
  ];
  const truncated = wbcTruncateMessagesAfterUser(messages, "user-retry");
  const alreadyTruncated = [
    { id: "user-before", role: "user" },
    { id: "assistant-before", role: "assistant" },
    { id: "user-retry", role: "user" }
  ];
  return {
    ids: truncated.map(item => item.id),
    preservesIdentity: wbcTruncateMessagesAfterUser(
      alreadyTruncated,
      "user-retry"
    ) === alreadyTruncated
  };
})()
"""
    )

    assert result == {
        "ids": [
            "user-before",
            "assistant-before",
            "user-retry",
            "user-later",
            "assistant-later",
        ],
        "preservesIdentity": True,
    }


def test_workbench_chat_renders_new_user_turn_before_live_thinking_card():
    root = Path(__file__).resolve().parent.parent
    source = workbench_chat_source()
    quick_source = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-quick-chat.jsx"
    ).read_text(encoding="utf-8")
    runtime_source = frontend_module_source("features/chat/file-resources.jsx")

    start_block = source.split("function start(chatId, input, model)", 1)[1].split(
        "function reconnect(chatId, model, preserveRuntime)", 1
    )[0]
    ack_block = source.split("onAck: function (event) {", 1)[1].split(
        "onReplyStart:", 1
    )[0]

    assert 'id: optimisticId' in start_block
    assert 'role: "user"' in start_block
    assert "attachments: Array.isArray(input.attachments)" in start_block
    assert start_block.index('fire("onUserMessage"') < start_block.index("update(chatId")
    assert "optimisticUserMessageId" in start_block
    assert "retryTruncateAfterMessageId: retryTruncateAfterMessageId" in start_block
    assert "retrySuppressedMessageIds" not in start_block
    assert "wbcHandleRuntimeAck(event" in ack_block
    assert 'context.fire("onUserMessageConfirmed"' in runtime_source
    assert "optimisticId" in runtime_source
    assert 'context.fire("onRetryTruncate", context.chatId, retryTruncateAfterMessageId);' in runtime_source
    assert "retryReplacedMessageIds" not in ack_block
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
    userMessage: {
      id: "msg-1",
      role: "user",
      content: "hello",
      createdAt: "2026-07-15T09:27:56.000Z"
    }
  });
  return {
    beforeAck,
    optimistic: userMessages[0],
    confirmation: confirmations[0],
    runtimeUser: WorkbenchChatRuntimes.get("chat-1").userMessages[0]
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
    assert result["runtimeUser"]["id"] == "msg-1"
    assert result["runtimeUser"]["createdAt"] == result["optimistic"]["createdAt"]
    assert result["runtimeUser"]["serverCreatedAt"] == "2026-07-15T09:27:56.000Z"


def test_workbench_chat_opens_bounded_browser_window_from_live_browser_events():
    source = workbench_chat_source()
    live_events = frontend_module_source("features/chat/live-event-controller.jsx")
    styles = workbench_style_source()

    assert "browserActiveByChat" in source
    assert 'event.type === "browser_frame" || event.type === "browser_takeover_request"' in live_events
    assert "browserEventChatId !== String(context.activeChatIdRef.current)" in live_events
    assert "setBrowserActiveByChat(function (prev)" in live_events
    assert "(browserState && browserState.active) || browserMarkedActive" in source
    browser_event_block = live_events.split("function wbcApplyBrowserEvent", 1)[1].split(
        "function wbcHandleLiveEvent", 1
    )[0]
    assert "setBrowserWindowModeByChat" in browser_event_block
    assert 'setSideTab("browser")' not in browser_event_block
    assert 'browserWindowModeByChat[activeChatId] || "pip"' in source
    surface = source.split("function WbcBrowserFloatingSurface", 1)[1].split("function WbcMain", 1)[0]
    assert 'var effectiveMode = mode === "minimized" ? "pip" : (mode || "pip");' in surface
    assert "onMinimize" not in surface
    assert 'beginInteraction(event, "drag", "")' in surface
    assert '["n", "s"].map(function (direction)' in surface
    assert 'className={"wbc-browser-resize-handle " + direction}' in surface
    assert 'next.x = start.x + dx;' not in surface
    assert "style={inlineStyle}" not in surface
    assert ".wbc-thread-stage" in styles
    assert '<div className="wbc-browser-movement-region">' in source
    movement_region_styles = styles.split(".wbc-browser-movement-region {", 1)[1].split("}", 1)[0]
    assert "position: absolute;" in movement_region_styles
    assert "top: var(--wbc-thread-inset-top);" in movement_region_styles
    assert "right: var(--wbc-thread-inset-inline);" in movement_region_styles
    assert "bottom: calc(34px * var(--wb-ui-font-scale, 1));" in movement_region_styles
    assert "left: var(--wbc-thread-inset-inline);" in movement_region_styles
    assert "pointer-events: none;" in movement_region_styles
    thread_styles = styles.split(".wbc-thread {", 1)[1].split("}", 1)[0]
    assert "padding: var(--wbc-thread-inset-top) var(--wbc-thread-inset-inline) var(--wbc-thread-inset-bottom);" in thread_styles
    assert ".wbc-browser-window.maximized" in styles
    assert ".wbc-browser-restore-float" in styles
    pip_styles = styles.split(".wbc-browser-window.pip {", 1)[1].split("}", 1)[0]
    assert "max(240px, calc(var(--wbc-side-track-width) - (2 * var(--wbc-card-gutter))))" in pip_styles
    assert "height: min(240px" in pip_styles
    assert ".wbc-pane-card-chat:has(.wbc-browser-window.pip)" in styles
    assert ".wbc-pane-layout.single:has(.wbc-browser-window.pip)" in styles
    assert "right: calc(var(--wbc-card-gutter) - var(--wbc-side-track-width));" in styles
    assert "bottom: calc(-34px * var(--wb-ui-font-scale, 1));" in styles
    assert "setBrowserSuppressedForSide(\n      !!(sidePanelTabExpanded && browserVisible" in source
    assert 'querySelector(":scope > .wbc-side .wbc-side-card")' in source
    assert "class WbcFloatingBrowserAlignment" in source
    assert "align(forceNativeSync, immediateNativeSync)" in source
    assert 'sideRect.left - floatingRect.left' in source
    assert 'paneRect.bottom + userOffsetY - floatingRect.bottom' in source
    assert 'source: "pip-context-panel-alignment"' in source
    assert 'event.detail.source === "pip-context-panel-alignment"' in source
    alignment = source.split("class WbcFloatingBrowserAlignment", 1)[1].split(
        "function useWbcFloatingBrowserAlignment", 1
    )[0]
    assert "publishNativeBounds(deferUntilMounted) {" in alignment
    assert "this.dispatchNativeBounds();" in alignment
    assert "this.nativeBoundsCommitRaf = requestAnimationFrame" in alignment
    assert "if (geometryChanged || forceNativeSync) this.publishNativeBounds(!immediateNativeSync);" in alignment
    assert '--wbc-browser-pip-aligned-width' in source
    assert '--wbc-browser-pip-aligned-height' in source
    assert 'var maximumHeight = Math.max(0, Math.floor(paneRect.bottom - sideRect.bottom - 12));' in source
    assert 'var alignedHeight = Math.min(Math.round(alignedWidth * 3 / 4), maximumHeight);' in source
    assert 'var boundedOffsetY = Math.min(0, Math.max(minimumOffsetY, userOffsetY));' in source
    assert 'window.addEventListener("workbench:right-resize", this.onRightResize);' in alignment
    assert "aspect-ratio: 4 / 3;" in styles
    assert '--wbc-browser-pip-align-x' in styles
    assert '--wbc-browser-pip-align-y' in styles
    assert '--wbc-browser-pip-user-height' in styles
    assert '--wbc-browser-pip-user-offset-y' in styles
    floating_native_styles = styles.split(".wbc-browser-window.pip .browser-view.native,", 1)[1].split("}", 1)[0]
    assert ".wbc-browser-window.maximized .browser-view.native" in floating_native_styles
    assert "--browser-resize-gutter: 0px;" in floating_native_styles
    assert ".wbc-browser-window.pip .browser-tabs-strip," in styles
    assert ".wbc-browser-window.pip .browser-nav-bar" in styles
    assert "WBC_ICONS.windowMaximize" in surface
    assert "WBC_ICONS.windowMinimize" not in surface
    assert 'Array.isArray(displayBrowserState.tabs) && displayBrowserState.tabs.length === 0' in source
    assert 'hasNoBrowserTabs && effectiveMode === "pip"' in source
    assert 'action_id: "set_frame"' not in surface
    assert 'action_id: "maximize"' in surface
    assert 'modeTransition.run(onRestore, "pip")' in surface
    assert "{WBC_ICONS.x}" in source
    assert 'close-fullscreen-rounded.svg' in styles
    assert "height: 58px;" in styles
    assert "wbc-browser-title-pill" in source
    assert "wbc-browser-restore-icon" not in source
    assert "browser-status-dot running" not in source.split("function WbcBrowserFloatingSurface", 1)[1].split("function WbcMain", 1)[0]


def test_browser_floating_surfaces_use_pointer_shelf_hit_testing_and_favicon_state():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    source = workbench_chat_source()
    workbench = workbench_shell_source()
    styles = workbench_style_source()

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
    source = workbench_chat_source()
    styles = workbench_style_source()

    set_bounds_block = main.split("  setBounds(info = {}) {", 1)[1].split("\n  setObscured(", 1)[0]
    assert "this.syncAttachedView();" in set_bounds_block
    assert "}, 32);" in set_bounds_block
    assert "}, 50);" not in set_bounds_block
    commit_block = source.split(
        "  function commitFrame(next, area, anchorFrame, anchorOffset, customizeHeight) {", 1
    )[1].split("\n  function stopInteraction", 1)[0]
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

    browser_view = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")
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
    assert 'wbcNotifyBrowserWindowInteraction(true, "mode", this.browserSessionId, {' in source
    assert 'wbcNotifyBrowserWindowInteraction(false, "mode", this.browserSessionId);' in source
    mode_transition_block = source.split(
        "  runModeTransition(action, targetMode) {", 1
    )[1].split("\n}\n\nfunction useWbcBrowserModeTransition", 1)[0]
    assert 'window.addEventListener("workbench:browser-transition-target-ready"' in mode_transition_block
    assert "applyModeAfterPreview();" in mode_transition_block
    assert mode_transition_block.index(
        'window.addEventListener("workbench:browser-transition-target-ready"'
    ) < mode_transition_block.index(
        'wbcNotifyBrowserWindowInteraction(true, "mode", this.browserSessionId, {'
    )
    assert "setTimeout(applyModeAfterPreview, 1800)" in mode_transition_block
    assert "function wbcMeasureBrowserSurfaceForMode(shellRef, targetMode)" in source
    assert 'var measurementHost = targetMode === "maximized" ? document.body : host;' in source
    assert "measurementHost.appendChild(clone);" in source
    assert 'clone.querySelector(".browser-native-surface")' in source
    assert "targetBounds: wbcMeasureBrowserSurfaceForMode" in mode_transition_block
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
    assert "overscroll-behavior-y: auto !important;" in main
    assert "await this.settlePageViewport(active.view, targetBounds, true)" in commit_transition_block
    assert "prepared = await this.prepareBoundsTransition();" in settle_transition_block
    assert "return this.commitBoundsTransition();" in settle_transition_block
    assert "info.transition === 'prepare'" in set_bounds_block
    assert "info.transition === 'commit'" in set_bounds_block
    assert "Page.captureScreenshot" in main
    assert "cssVisualViewport" in main
    assert "const surfaceRef = React.useRef(null);" in browser_view
    assert 'const browserWindow = node.closest(".wbc-browser-window")' in browser_view
    assert "const borderRadius = 0;" in browser_view
    assert "const node = surfaceRef.current;" in browser_view
    assert "contentInset" not in browser_view
    assert "x: rect.left" in browser_view
    assert "width: Math.max(0, rect.width)" in browser_view
    assert 'browserWindow.classList.contains("pip")' in browser_view
    assert 'browserWindow.classList.contains("maximized") ? 12 : 0' in browser_view
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
    assert "z-index: 31;" in pip_bar_block
    assert "background: var(--wb-card-bg-strong);" in pip_bar_block
    assert "padding: 0 9px 0 14px" in pip_bar_block
    assert "border-bottom-color:" in pip_bar_block
    pip_head_block = styles.split(".wbc-browser-pip-head-wrap {", 1)[1].split("\n}", 1)[0]
    assert "min-height: 46px;" in pip_head_block
    assert "visibility: visible;" in pip_head_block
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
    source = frontend_module_source("shared/browser/overlays.jsx")
    reset_block = source.split("function wbResetBrowserOverlayObscured()", 1)[1].split("\n}", 1)[0]
    assert "wbBrowserOverlayCount = 0;" in reset_block
    assert "wbSetBrowserOverlayObscured(0);" in reset_block
    assert reset_block.index("wbBrowserOverlayCount = 0;") < reset_block.index(
        "wbSetBrowserOverlayObscured(0);"
    )


def _run_browser_avoidance_plan(*args):
    source = workbench_chat_source()
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


def _run_conversation_stick_sequence(steps):
    source = workbench_chat_source()
    function_source = "function wbcShouldStickToConversationBottom" + source.split(
        "function wbcShouldStickToConversationBottom", 1
    )[1].split("\nfunction wbcSelectionTextRect", 1)[0]
    script = f"""
{function_source}
let sticking = true;
let previous = 900;
const results = [];
for (const step of {json.dumps(steps)}) {{
  sticking = wbcShouldStickToConversationBottom(
    sticking, previous, step.scrollTop, 1000, 100
  );
  previous = step.scrollTop;
  results.push(sticking);
}}
process.stdout.write(JSON.stringify(results));
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


def test_workbench_chat_upward_scroll_immediately_releases_sticky_bottom():
    assert _run_conversation_stick_sequence([
        {"scrollTop": 870},
        {"scrollTop": 870},
        {"scrollTop": 897},
        {"scrollTop": 900},
    ]) == [False, False, False, True]


def test_browser_avoidance_plan_declines_centered_or_too_narrow_layouts():
    assert _run_browser_avoidance_plan(100, 800, 400, 200, 14) is None
    assert _run_browser_avoidance_plan(100, 800, 430, 350, 14) is None
    assert _run_browser_avoidance_plan(100, 800, 920, 200, 14) is None


def test_workbench_chat_reflows_only_entries_intersecting_the_browser_pip():
    source = workbench_chat_source()
    styles = workbench_style_source()

    assert 'data-wbc-thread-item="true"' in source
    assert 'stage.querySelector(".wbc-browser-window.pip")' in source
    assert 'window.addEventListener("workbench:browser-layout", scheduleBrowserAvoidance)' in source
    assert "var scheduleStickyViewportRestore = useWbcCallback(function () {" in source
    assert source.count("scheduleStickyViewportRestore();") >= 6
    assert "new MutationObserver(function ()" in source
    assert "mutationObserver.observe(thread, { childList: true });" in source
    assert "mutationObserver.observe(thread, { childList: true, subtree: true, characterData: true });" not in source
    assert "for (var pass = 0; pass < 5; pass++)" in source
    assert 'item.offsetTop + item.offsetHeight <= contentTop' not in source
    assert 'candidate.offsetTop + candidate.offsetHeight <= contentTop' in source
    assert 'item.style.setProperty("--wbc-browser-avoid-start"' in source
    assert 'item.style.setProperty("--wbc-browser-avoid-end"' in source
    assert "if (!preserveViewport) return;" in source
    on_scroll_block = source.split("  function onScroll() {", 1)[1].split("\n  useWbcEffect", 1)[0]
    assert "scheduleBrowserAvoidance();" in on_scroll_block
    assert "scheduleStickyViewportRestore();" not in on_scroll_block
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
    styles = workbench_style_source()

    active_tab_styles = styles.split("\n.browser-tab.active {", 1)[1].split("}", 1)[0]
    assert "color: var(--wb-text, var(--text));" in active_tab_styles
    assert "color: var(--wb-accent, var(--accent));" not in active_tab_styles


def test_electron_browser_video_fullscreen_is_platform_aware_and_shared_with_ui():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    browser_view = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")
    styles = workbench_style_source()

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
    assert 'browserLabel("browser.fullscreen.active", "Playing in full screen")' in browser_view
    assert 'browserLabel("browser.fullscreen.external", "The video is playing in a separate full-screen window")' in browser_view
    assert ".browser-video-fullscreen-overlay" in styles

    session_guards = main.split("function installBrowserSessionGuards(", 1)[1].split("\nclass BrowserTabManager", 1)[0]
    assert "permission === 'fullscreen'" in session_guards
    assert "browserSession.setPermissionCheckHandler" in session_guards
    assert "browserSession.setPermissionRequestHandler" in session_guards
    assert "permission !== 'pointerLock'" in session_guards
    assert "promptForPointerLock(webContents, details)" in session_guards
    assert "dialog.showMessageBox(pointerLockPromptParent(webContents)" in session_guards
    assert "defaultId: 1" in session_guards
    assert "cancelId: 1" in session_guards
    assert "result.response === 0" in session_guards
    assert "pendingPointerLockPrompts" in session_guards
    assert "allowedPointerLockOrigins.has(origin)" in session_guards
    assert "if (allowed) allowedPointerLockOrigins.add(origin)" in session_guards
    assert "Press Esc at any time to release it." in main
    assert "你可随时按 Esc 退出" in main


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
    source = workbench_chat_source()
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


def test_browser_window_docks_above_composer_without_changing_its_normal_frame():
    source = workbench_chat_source()
    helper_source = "function wbcBrowserComposerDockFrame(" + source.split(
        "function wbcBrowserComposerDockFrame(", 1
    )[1].split("\nfunction wbcKeepBrowserWindowClearOfComposer", 1)[0]
    script = f"""
{helper_source}
const original = {{ x: 640, y: 430, width: 340, height: 240 }};
const area = {{ left: 100, top: 80 }};
const overlappingComposer = {{ left: 120, right: 1080, top: 610 }};
const narrowComposer = {{ left: 120, right: 620, top: 610 }};
const docked = wbcBrowserComposerDockFrame(original, area, overlappingComposer, 10, 180);
const untouched = wbcBrowserComposerDockFrame(original, area, narrowComposer, 10, 180);
process.stdout.write(JSON.stringify({{ original, docked, untouched }}));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)
    assert result["docked"] == {"x": 640, "y": 280, "width": 340, "height": 240}
    assert result["untouched"] == result["original"]
    assert result["original"] == {"x": 640, "y": 430, "width": 340, "height": 240}
    assert "composerDocked={!sideVisible}" in source
    assert "preComposerDockFrameRef.current = frameRef.current || measuredFrame();" in source
    assert "if (committed && !composerDockedRef.current)" in source
    assert "composerDockedRef.current = false;" in source
    assert "}, 520);" in source


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


def test_workbench_chat_tracks_fallback_model_from_live_phase_event():
    result = _run_workbench_runtime_js(
        """
(() => {
  const model = { sendMessage: () => new Promise(() => {}) };
  WorkbenchChatRuntimes.start("chat_model", { message: "hello" }, model);
  global.__wbcSseHandler({
    type: "phase_transition",
    session_id: "chat_model",
    from: "primary_model",
    to: "fallback_model",
    detail: "switching",
    detail_params: { fallbackModel: "fallback-model" }
  });
  return WorkbenchChatRuntimes.snapshot().chat_model.activeModel;
})()
"""
    )

    assert result == "fallback-model"


def test_workbench_chat_splits_reasoning_and_tools_into_distinct_cards():
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

    assert result == [
        {
            "id": "activity_1",
            "reasoning": "first reasoning",
            "tools": [],
        },
        {
            "id": "activity_2",
            "reasoning": "",
            "tools": ["read_file"],
        },
        {
            "id": "activity_3",
            "reasoning": "second reasoning",
            "tools": [],
        },
        {
            "id": "activity_4",
            "reasoning": "",
            "tools": ["list_skills", "read_file"],
        },
    ]


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
            "tools": [],
        },
        {
            "reasoning": "",
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
  // The live state scanner may discover the visible preamble only after a
  // tool from this LLM call has started. It still belongs below the preamble.
  global.__wbcSseHandler({
    type: "tool_call", session_id: "chat_preamble", tool: "search",
    args: { query: "photo metadata" }
  });
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
                "tools": [],
                "closed": True,
            },
            {
                "reasoning": "",
                "tools": ["Bash"],
                "closed": False,
            },
            {
                "reasoning": "send the existing file",
                "tools": [],
                "closed": True,
            },
            {
                "reasoning": "",
                "tools": ["search", "send_file"],
                "closed": False,
            },
        ],
        "segments": ["找到了，我发给你。"],
    }


def test_workbench_chat_llm_boundaries_separate_tool_only_groups():
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

    assert result == [["read_file"], ["list_skills"]]


def test_workbench_chat_model_label_and_context_usage_use_live_data():
    source = workbench_chat_source()
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
    assert "runtime && runtime.activeModel" in overview
    assert "liveData && liveData.model" in overview
    assert "wbcCurrentModel(chat, null, runtime, liveData)" not in overview
    assert "var usage = Object.assign({}, (liveData && liveData.usage) || {}, runtimeUsage);" in overview
    assert "chat.usage" not in overview
    assert '<WbcOverviewUsage usage={usage} latestUsage={chat.latestUsage} />' in overview
    assert '<WbcContextUsage data={liveData} compact={true} />' in overview
    assert '{!compact && (' not in usage
    assert 'className={"wbc-ctx-bar level-" + fillLevel}' in usage
    assert 'className="wbc-ctx-splitbar"' in usage
    assert 'className="wbc-ctx-split-label"' in usage
    assert 'workbenchChat.ctx.compactAt' in usage
    assert 'wbcT("chat.runSummary"' not in overview
    assert "WbcQuickActionItems" not in overview


def test_workbench_chat_delete_detaches_local_fork_markers():
    source = workbench_chat_source()
    handler = source.split("function handleDeleteChat(chatId)", 1)[1].split("function handleCompact", 1)[0]

    assert "function detachDeletedForkSource(item)" in handler
    assert "delete cleaned.forkedFromChatId" in handler
    assert "delete cleaned.forkedAtMessageId" in handler
    assert "delete cleaned.forkMessage" in handler
    assert ".map(detachDeletedForkSource)" in handler
    assert "setActiveChat(function (prev) { return detachDeletedForkSource(prev); })" in handler
    assert handler.index("setChats(function (prev)") < handler.index("model.deleteChat(chatId)")
    assert handler.index("WbcVoice.stop()") < handler.index("model.deleteChat(chatId)")
    assert "next.splice(Math.min(Math.max(deletedIndex, 0), next.length), 0, deletedItem)" in handler


def test_workbench_chat_card_menu_can_rename_the_target_chat():
    source = workbench_chat_source()
    styles = workbench_style_source()
    rail = source.split("function WbcRail(", 1)[1].split(
        "// Conversation main (column 3)", 1
    )[0]
    rename_dialog = source.split("function WbcRenameDialog", 1)[1].split(
        "function WbcRail(", 1
    )[0]
    rename_controller = frontend_module_source("features/chat/chat-action-controller.jsx")

    assert "onRename={handleRenameChat}" in source
    assert 'wbcT("workbenchChat.rename", "Rename chat")' in rail
    assert 'role="dialog"' in rename_dialog
    assert "maxLength={60}" in rename_dialog
    assert "window.ReactDOM.createPortal(" in rename_dialog
    assert 'document.querySelector(".workbench-shell") || document.body' in rename_dialog
    assert "setRenameChat(chat)" in rail
    assert "onDoubleClick={function (event)" in rail
    assert 'event.target.closest("button, a, input, [role=\'menuitem\'], .wbc-fork-marker")' in rail
    assert rail.index("onDoubleClick={function (event)") < rail.index("onContextMenu={function (event)")
    assert "window.prompt(" not in rail
    assert "onRename(chat.id, nextTitle)" in rename_dialog
    assert "previous && previous.id === chat.id" in rename_controller
    assert '(menuId ? " menu-active" : "")' in rail
    menu_active_css = styles.split(".wbc-chat-list.menu-active {", 1)[1].split("}", 1)[0]
    assert "z-index: 200;" in menu_active_css
    assert "pointer-events: none;" in menu_active_css
    assert ".wbc-chat-list.menu-active .wbc-chat-card.menu-open" in styles
    assert "pointer-events: auto;" in styles.split(
        ".wbc-chat-list.menu-active .wbc-chat-card.menu-open", 1
    )[1].split("}", 1)[0]


def test_project_terminal_menu_is_above_the_outside_click_scrim():
    source = workbench_chat_source()
    styles = workbench_style_source()

    project_tools = source.split('<section\n          ref={projectToolsRef}', 1)[1].split(
        'aria-label={wbcT("rail.projectTools"', 1
    )[0]
    assert 'String(menuId).indexOf("terminal:") === 0 ? " menu-active" : ""' in project_tools

    project_tools_menu_css = styles.split(
        ".wbc-project-tools.menu-active {", 1
    )[1].split("}", 1)[0]
    assert "z-index: 200;" in project_tools_menu_css
    assert "pointer-events: none;" in project_tools_menu_css
    assert "pointer-events: auto;" in styles.split(
        ".wbc-project-terminal-list.menu-active .wbc-terminal-card.menu-open", 1
    )[1].split("}", 1)[0]


def test_project_tool_view_persists_per_project_and_restores_terminal_list():
    source = frontend_module_source("features/chat/rail.jsx")
    helpers = source.split(
        'var WBC_PROJECT_TOOL_VIEW_STORAGE_PREFIX = "wbc-project-tool-view:";', 1
    )[1].split("function WbcTerminalStatusIcon(", 1)[0]
    helpers = (
        'var WBC_PROJECT_TOOL_VIEW_STORAGE_PREFIX = "wbc-project-tool-view:";'
        + helpers
    )
    script = f"""
const values = new Map();
global.localStorage = {{
  getItem: key => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: key => values.delete(key),
}};
eval({json.dumps(helpers)});
wbcWriteProjectToolView("project-a", "terminal");
wbcWriteProjectToolView("project-b", "file");
const before = [wbcReadProjectToolView("project-a"), wbcReadProjectToolView("project-b")];
wbcWriteProjectToolView("project-a", "");
process.stdout.write(JSON.stringify({{
  before,
  after: wbcReadProjectToolView("project-a"),
  storedNone: wbcHasStoredProjectToolView("project-a"),
  invalid: wbcNormalizeProjectToolView("chat"),
}}));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    assert json.loads(completed.stdout) == {
        "before": ["terminal", "file"],
        "after": "",
        "storedNone": True,
        "invalid": "",
    }
    assert "return wbcReadProjectToolView(projectId);" in source
    project_reset = source.split("var visibleFiles", 1)[1].split("useWbcEffect(function () {", 1)[1].split(
        "}, [projectId]);", 1
    )[0]
    assert "setProjectToolViewState(wbcReadProjectToolView(projectId));" in project_reset
    assert "setFileToolsExpanded(false);" not in project_reset
    assert "setTerminalToolsExpanded(false);" not in project_reset
    assert "var activeTerminalBelongsToProject" in source
    assert 'String(terminal && terminal.projectId || "") === String(projectId)' in source
    assert "!activeTerminalBelongsToProject || wbcHasStoredProjectToolView(projectId)" in source


def test_project_terminal_cards_have_independent_agent_lifecycle_and_unread_semantics():
    source = frontend_module_source("features/chat/rail.jsx")
    terminal_controller = frontend_module_source("features/chat/terminal-controller.jsx")
    styles = workbench_style_source()
    terminal_state = source.split("function terminalRailVisualState(terminal)", 1)[1].split(
        "function renderTerminalCard", 1
    )[0]
    terminal_card = source.split("function renderTerminalCard(terminal)", 1)[1].split(
        "function renderTerminalSection", 1
    )[0]

    for state in ("working", "waiting", "completed", "idle", "failed", "interrupted"):
        assert f'{state}:' in terminal_state
        assert f'status-agent-{state}' in terminal_state
    assert 'status-terminal-failed' in terminal_state
    assert 'status-terminal-normal' in terminal_state
    assert "var visualState = terminalRailVisualState(terminal);" in terminal_card
    assert 'var unread = Boolean(terminal && terminal.unread);' in terminal_card
    assert 'var agentActive = Boolean(terminal && terminal.agentActive || agent && agent.active);' in terminal_state
    assert 'var agentVisual = agentActive ? agentStates[agentState] : null;' in terminal_state
    assert 'terminal && terminal.agentActive && terminal.agentState' in terminal_card
    assert 'wbc-terminal-unread-dot' in terminal_card
    assert "+ visualState.tone}" in terminal_card

    terminal_source = frontend_module_source("terminal/entry.jsx")
    assert 'socket.send(JSON.stringify({ type: "read" }))' in terminal_source
    assert 'TerminalClient.markRead(terminalId)' in terminal_source
    normal_terminal_css = styles.split(
        ".wbc-rail .wbc-terminal-card.status-terminal-normal .wbc-chat-row-icon {", 1
    )[1].split("}", 1)[0]
    idle_agent_css = styles.split(
        ".wbc-rail .wbc-terminal-card.status-agent-idle .wbc-chat-row-icon {", 1
    )[1].split("}", 1)[0]
    assert "color: var(--wb-green);" in normal_terminal_css
    assert "color: var(--wb-green);" in idle_agent_css
    assert "function wbcSubscribeTerminalRefresh(projectId, refreshTerminals)" in terminal_controller
    assert "window.requestAnimationFrame" in terminal_controller
    assert 'event.type !== "terminal_list_changed"' in terminal_controller
    assert "return wbcSubscribeTerminalRefresh(projectId, refreshTerminals);" in source
    assert "window.setInterval" not in source.split("function WbcProjectRail(props)", 1)[1].split(
        "// ---------------------------------------------------------------------------\n// Conversation main", 1
    )[0]
    assert "function WbcTerminalStatusIcon({ stateKey, icon })" in source
    assert 'className={"wbc-terminal-status-glyph is-leaving " + leaving.stateKey}' in source
    assert '<WbcTerminalStatusIcon stateKey={visualState.tone} icon={visualState.icon} />' in terminal_card
    assert "animation: wbc-terminal-icon-enter 240ms" in styles
    assert "animation: wbc-terminal-icon-leave 180ms" in styles
    assert "animation: wbc-terminal-agent-working 1.05s ease-in-out infinite;" in styles
    assert "animation: wbc-terminal-agent-completed-settle 320ms" in styles
    assert '@media (prefers-reduced-motion: reduce)' in styles
    assert ".wbc-terminal-status-glyph.is-leaving { display: none; }" in styles


def test_finished_one_shot_terminals_are_hidden_from_the_project_rail():
    source = frontend_module_source("features/chat/rail.jsx")
    helper = "function wbcTerminalVisibleInRail(" + source.split(
        "function wbcTerminalVisibleInRail(", 1
    )[1].split("function WbcTerminalStatusIcon(", 1)[0]
    script = f"""
eval({json.dumps(helper)});
process.stdout.write(JSON.stringify([
  wbcTerminalVisibleInRail({{launchMode: "one_shot", status: "starting"}}),
  wbcTerminalVisibleInRail({{launchMode: "one_shot", status: "running"}}),
  wbcTerminalVisibleInRail({{launchMode: "one_shot", status: "exited"}}),
  wbcTerminalVisibleInRail({{launchMode: "interactive", status: "exited"}}),
  wbcTerminalVisibleInRail({{status: "exited"}})
]));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )

    assert json.loads(completed.stdout) == [True, True, False, True, True]
    assert ".filter(wbcTerminalVisibleInRail).slice().sort" in source
    assert "&& wbcTerminalVisibleInRail(terminal);" in source


def test_workbench_chat_card_menu_can_pin_and_sort_conversations():
    source = workbench_chat_source()
    shell = workbench_shell_source()
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
    assert "onToggleChat: function (chat, pinned)" in shell
    assert 'togglePinnedSession({ id: chat.id, kind: "chat" }, pinned)' in shell


def test_plugin_tools_share_conversation_cards_and_split_drop_pipeline():
    rail = frontend_module_source("features/chat/rail.jsx")
    drag_layout = frontend_module_source("features/chat/drag-layout.jsx")
    page = frontend_module_source("features/chat/page.jsx")
    pane_drop = frontend_module_source("features/chat/pane-drop-controller.jsx")
    pane_layout = frontend_module_source("features/chat/pane-layout-controller.jsx")
    styles = workbench_style_source()
    i18n = workbench_i18n_source()

    assert 'var WBC_PLUGIN_VIEW_DRAG_MIME = "application/x-cyrene-plugin-view+json";' in drag_layout
    assert "function wbcSetPluginViewDrag(event, payload)" in drag_layout
    assert "function wbcHasPluginViewDrag(event)" in drag_layout
    assert "function wbcReadPluginViewDrag(event)" in drag_layout
    assert 'kind: "plugin-view"' in drag_layout
    assert 'className={"wbc-chat-card wbc-project-plugin-tool"' in rail
    assert 'draggable={context.disabled ? undefined : "true"}' in rail
    assert 'draggable={itemDisabled ? undefined : "true"}' in rail
    assert "wbcSetPluginViewDrag(event, payload);" in rail
    assert "prepareRailDragImage(event.currentTarget, event.dataTransfer" in rail
    assert 'host.classList.add("wbc-plugin-tool-drag-image")' in rail
    assert "pluginSuppressClickRef.current === context.toolKey" in rail
    plugin_section = rail.split('className="wbc-project-tools wbc-plugin-project-tools"', 1)[1].split("</section>", 1)[0]
    assert "wbcHasPluginViewDrag(event)" in plugin_section
    assert "event.preventDefault();" in plugin_section
    assert 'event.dataTransfer.dropEffect = "move";' in plugin_section
    assert 'setPluginDragId("");' in plugin_section
    assert 'role="button"' in rail
    assert 'event.key !== "Enter" && event.key !== " "' in rail
    assert 'var clickBehavior = String(tool.click_behavior || "");' in rail
    assert 'replaceWorkspace: clickBehavior === "replace_workspace" || !clickBehavior' in rail
    assert 'restore: tool.restore_layout !== false' in rail
    assert 'ownerScope: String(tool.pane_owner_scope || "chat")' in rail
    assert 'paneOwnerScope: String(tool && tool.pane_owner_scope || "chat")' in rail
    assert 'openOptions: wbcPluginToolOpenOptions(tool)' in rail
    assert "onOpenPluginView(payload, context.openOptions);" in rail
    assert "onOpenPluginView(context.payload, context.openOptions);" in rail
    assert 'var projectSectionPluginTools = pluginTools.filter(function (tool)' in rail
    assert '=== "project_tools"' in rail
    assert "{projectSectionPluginTools.map(renderIntegratedPluginTool)}" in rail
    assert "{pluginSectionTools.map(renderAuxiliaryPluginTool)}" in rail
    assert 'className="wbc-project-tool-inline-header is-plugin"' in rail
    assert 'className={"wbc-project-tool-expand wbc-plugin-tool-collection-expand"' in rail
    assert 'setChatDragKind("plugin-view")' in page
    assert "wbcHasPluginViewDrag(event)" in page
    assert "activateProjectPaneWorkspace" in page
    assert "projectOwnedPlugin" in pane_drop
    assert 'openPaneContent("plugin-view"' in page
    assert 'context.paneContentCard(\n        "plugin-view"' in pane_drop
    assert "var existingPlugin = wbcPaneCardLocation(layout, pluginCard.id);" in pane_drop
    assert 'normalizedType === "plugin-view" ? baseCard.id : ""' in pane_layout
    assert "freshInstance: true" in pane_layout
    assert ".wbc-plugin-project-tools > .wbc-project-plugin-tool" in styles
    assert ".wbc-plugin-tool-collection-items > .wbc-project-plugin-tool" in styles
    assert ".wbc-project-tools.has-expanded-tool > .wbc-project-tool-expand:not(.is-expanded)" in styles
    assert ".wbc-native-chat-drag-image.wbc-plugin-tool-drag-image" in styles
    assert ".wbc-project-plugin-tool.dragging" not in styles
    assert i18n.count('"workbenchChat.dragPluginView"') == 2
    assert i18n.count('"workbenchChat.dropPluginViewToOpenSide"') == 2


def test_dynamic_surfaces_reuse_plugin_snapshot_and_protect_user_panes():
    plugins = frontend_module_source("platform/plugins.jsx")
    surfaces = frontend_module_source("features/chat/dynamic-surfaces.jsx")
    broker = frontend_module_source("features/chat/dynamic-surface-broker.mjs")
    page = frontend_module_source("features/chat/page.jsx")
    detached = frontend_module_source("features/chat/context-panel.jsx")
    pane_drag = frontend_module_source("features/chat/pane-card-drag-controller.jsx")
    pane_restore = frontend_module_source("features/chat/pane-detachment.jsx")
    root = Path(__file__).resolve().parents[1]
    electron = (root / "electron" / "main.js").read_text(encoding="utf-8")

    assert "workbenchSurfaces" in plugins
    assert "workspaceFileTypes" in plugins
    assert "workspaceActions" in plugins
    assert "pluginSnapshotSurface(state, surfaceId)" in plugins
    assert "pluginSnapshotFileTypeFor(state, path, mime)" in plugins
    assert "pluginSnapshotActionsFor(state, resource)" in plugins
    assert 'window.CyreneUI.register("dynamicSurfaces"' in surfaces
    assert "function WbcSurfaceHost" in surfaces
    assert 'renderer.kind === "plugin_view"' in surfaces
    assert '"resource-summary": WbcResourceSummarySurface' in surfaces
    assert 'card.kind !== "surface" || meta.origin !== "agent"' in broker
    assert "meta.claimedByUser === true || meta.pinned === true" in broker
    assert 'outcome: SURFACE_OUTCOMES.DEFERRED' in broker
    assert "wbcProjectFileDraftKey" in page
    assert "surfaceSuppressionRef" in page
    assert "wbcRevealSurface(previous" in page
    assert 'card.kind === "surface"' in page
    assert "<WbcSurfaceHost" in page
    assert 'kind === "surface"' in detached
    assert "<WbcSurfaceHost" in detached
    assert "meta: pane.meta" in pane_drag
    assert "meta: descriptor.meta" in pane_restore
    assert "const meta = sourceMeta ?" in electron
    assert "claimedByUser: sourceMeta.claimedByUser === true" in electron


def test_workbench_chat_rename_dialog_uses_compact_vertical_spacing():
    styles = workbench_style_source()
    body = styles.split(".wbc-rename-body {", 1)[1].split("}", 1)[0]
    foot = styles.rsplit(".wbc-rename-foot {", 1)[1].split("}", 1)[0]

    assert "gap: 8px;" in body
    assert "padding: 16px 18px 8px;" in body
    assert "padding: 12px 18px;" in foot


def test_workbench_branch_tree_uses_compact_git_history_layout():
    source = workbench_chat_source()
    styles = workbench_style_source()
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
    source = workbench_chat_source()
    index = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "index.html").read_text(
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
    assert "输入内容以引导正在运行的 Agent" in workbench_i18n_source()
    assert '<script type="module" src="compiled/app.js?v=0.9.0-beta4"></script>' in index


def test_workbench_guidance_is_optimistic_and_completed_tools_do_not_spin():
    source = workbench_chat_source()
    action_controller = frontend_module_source("features/chat/chat-action-controller.jsx")
    guidance_model = source.split("function sendGuidance", 1)[1].split(
        "function answerChat", 1
    )[0]
    guidance_handler = action_controller.split("function wbcHandleGuidance", 1)[1].split(
        "function wbcAnswerLiveAgentRequest", 1
    )[0]
    trace_card = source.split("function WbcTraceCard", 1)[1].split(
        "function WbcAssistantMessage", 1
    )[0]

    assert "timeout: 0" in guidance_model
    assert 'id: "guidance_pending_" + requestId' in guidance_handler
    assert "clientRequestId: requestId" in guidance_handler
    assert "optimistic: true" in guidance_handler
    assert "response.userMessage" in guidance_handler
    assert "item.clientRequestId" in guidance_handler
    assert 'status: (toolStarted || toolProgress) ? "running" : "completed"' in source
    assert 'event.type === "tool_call_progress"' in source
    assert 'className="wbc-transfer-progress"' in trace_card
    assert 'entryStatus === "running"' in trace_card


def test_workbench_tool_start_is_rendered_then_completed_in_place():
    source = workbench_chat_source()

    runtime = source.split("function wbcRuntimeToolEvent(event, eventAt)", 1)[1].split(
        'workbenchServices.events().subscribe(onSseEvent)', 1
    )[0]
    stream = source.split("function consumeEventStream(", 1)[1].split(
        "function streamChat", 1
    )[0]
    tool_payload = source.split("function wbcAgentToolPayload(event)", 1)[1].split(
        "function wbcAgentPermissionPayload", 1
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
    assert "wbcMergeToolOccurrence(items, entry, terminalToolEvent)" in runtime
    assert "progress: mergeToolProgress(activity && activity.progress)" in runtime
    assert "matchedToolCall" in runtime
    assert '(entry.toolCallId || "trace") + ":" + i' in source
    assert 'entry.kind === "tool" && entry.status === "running"' in activity_card
    assert "hasRunningTools && !hasReplyText" in activity_card
    assert 'type === "run_finalizing" && handlers.onFinalizing' in source
    assert "wbcFinalizeRuntime(cur)" in source
    assert 'type === "tool_call_started" && handlers.onToolStarted' in stream
    assert 'type === "tool_call_finished" && handlers.onToolCompleted' in stream
    assert "createdAt: Number.isFinite(parsedAt) ? parsedAt : Date.now()" in tool_payload
    assert "appendActivity(closeActivityTimeline(latest), { createdAt: eventAt })" in runtime
    assert "errorMessage" in runtime
    assert 'presentation: failed && error ? { kind: "error" } : undefined' in runtime


def test_workbench_marks_run_finalizing_before_workspace_save():
    source = workbench_runtime_source()
    completion = source.split("async def _finish_stream_reply", 1)[1].split(
        "async def _settle_stream", 1
    )[0]

    reply_done = completion.index('"type": "reply_done"')
    finalizing = completion.index('"type": "run_finalizing"')
    durable_reply = completion.index("await request.finalize_reply(reply)")
    saved = completion.index('"type": "saved"')
    publish_saved = completion.index("await run.publish(saved_event)")

    assert reply_done < finalizing < durable_reply < saved < publish_saved


def test_workbench_assistant_footer_formats_persisted_processing_duration():
    source = workbench_chat_source()
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
    assert footer.index("processingDuration") < footer.index("usageTokenCount")
    assert footer.index("usageTokenCount") < footer.index("outputTokenSpeed")


def test_workbench_assistant_footer_uses_step_total_and_formats_speed():
    source = frontend_module_source("features/chat/messages.jsx")
    token_helper = "function wbcAssistantUsageTokenCount" + source.split(
        "function wbcAssistantUsageTokenCount", 1
    )[1].split("function wbcFormatOutputTokenSpeed", 1)[0]
    speed_helper = "function wbcFormatOutputTokenSpeed" + source.split(
        "function wbcFormatOutputTokenSpeed", 1
    )[1].split("function WbcAssistantMessage", 1)[0]
    script = f"""
{token_helper}
{speed_helper}
const result = {{
  tokens: [
    wbcAssistantUsageTokenCount({{completion_tokens: 12, total_tokens: 30}}),
    wbcAssistantUsageTokenCount({{output_tokens: 14, total_tokens: 31}}),
    wbcAssistantUsageTokenCount({{total_tokens: 32}}),
    wbcAssistantUsageTokenCount({{completion_tokens: 0, total_tokens: 33}}),
    wbcAssistantUsageTokenCount({{prompt_tokens: 7, completion_tokens: 8}}),
    wbcAssistantUsageTokenCount({{completion_tokens: 12}}),
    wbcAssistantUsageTokenCount({{output_tokens: 14}}),
    wbcAssistantUsageTokenCount({{}}),
    wbcAssistantUsageTokenCount(null),
  ],
  speeds: [
    wbcFormatOutputTokenSpeed(42.56),
    wbcFormatOutputTokenSpeed(42),
    wbcFormatOutputTokenSpeed(0),
    wbcFormatOutputTokenSpeed(undefined),
    wbcFormatOutputTokenSpeed(Infinity),
    wbcFormatOutputTokenSpeed(0.04),
  ],
}};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )

    assert json.loads(completed.stdout) == {
        "tokens": [30, 31, 32, 33, 15, 12, 14, None, None],
        "speeds": ["42.6 tok/s", "42 tok/s", "", "", "", "<0.1 tok/s"],
    }
    footer = source.split('<div className="wbc-msg-foot">', 1)[1].split(
        "</div>", 1
    )[0]
    assert "wbcCompactNumber(usageTokenCount)" in footer
    assert "msg.usage.total_tokens" not in footer
    assert 'className="wbc-msg-output-speed"' in footer


def test_workbench_terminal_reply_snapshot_is_authoritative_after_streamed_calls():
    source = workbench_runtime_source()
    completion = source.split("async def _finish_stream_reply", 1)[1].split(
        "async def _settle_stream", 1
    )[0]
    fallback_body, after_fallback = completion.split(
        "if not request.is_external_agent:", 1
    )

    assert '"type": "reply_delta"' in fallback_body
    assert '"type": "reply_done"' not in fallback_body
    assert 'await run.publish({"type": "reply_done", "response": reply})' in after_fallback


def test_workbench_pip_reflow_does_not_compete_with_scroll_anchor():
    source = workbench_chat_source()
    styles = workbench_style_source()
    main = source.split("function WbcMain", 1)[1].split(
        "function WbcQuestionPrompt", 1
    )[0]
    thread_rule = styles.split(".wbc-thread {", 1)[1].split("}", 1)[0]

    assert "avoidanceApplyingRef.current = true;" in main
    assert main.count("if (avoidanceApplyingRef.current) return;") == 2
    assert "avoidanceApplyingRef.current = false;" in main
    assert "overflow-anchor: none;" in thread_rule
    assert "runtimeFinalizing: !!runtime.finalizing" in source
    assert "!runtime.finalizing && index === activities.length - 1" in source
    assert 'wbcT("workbenchChat.finalizing", "Reply complete · saving results…")' in source


def test_workbench_permission_prompt_renders_every_scoped_option():
    source = workbench_chat_source()
    prompt = source.split("function WbcQuestionPrompt", 1)[1].split(
        "function WbcErrorNotice", 1
    )[0]

    assert "options.length ? options.map" in prompt
    assert "wbc-question-protocol-error" in prompt
    assert "onAnswer(pq.id, wbcQuestionOptionValue(opt))" in prompt
    assert "options[options.length - 1]" not in prompt.split(") : (", 1)[0]

    styles = workbench_style_source()
    group_css = styles.split(".wbc-question-group {", 1)[1].split("}", 1)[0]
    question_css = styles.split(".wbc-question {", 1)[1].split("}", 1)[0]
    text_css = styles.split(".wbc-question-text {", 1)[1].split("}", 1)[0]
    option_css = styles.split(".wbc-question-opt {", 1)[1].split("}", 1)[0]
    assert "width: 100%;" in group_css
    assert "min-width: 0;" in group_css
    assert "max-width: 100%;" in question_css
    assert "overflow-wrap: anywhere;" in text_css
    assert "word-break: break-word;" in text_css
    assert "max-width: 100%;" in option_css
    assert "white-space: normal;" in option_css


def test_collapsed_chat_rail_maps_actionable_conversation_states():
    source = workbench_chat_source()
    styles = workbench_style_source()
    i18n = workbench_i18n_source()

    rail = source.split("function WbcRail(", 1)[1].split(
        "// Conversation main (column 3)", 1
    )[0]
    assert "function wbcConversationTrackState(" in source
    assert 'kind: "running"' in source
    assert 'kind: "attention"' in source
    assert 'kind: "result"' in source
    assert 'kind: "failed"' in source
    assert "previous[chatId].running" in rail
    assert "current[chatId].completed" in rail
    assert 'chatId !== String(activeChatId || "")' in rail
    assert 'role="navigation"' in rail
    assert 'className="wbc-conversation-status-track"' in rail
    assert "onMouseEnter={function () { openStatusPreview(chat.id); }}" in rail
    assert "onMouseMove={function () { if (!previewOpen) openStatusPreview(chat.id); }}" in rail
    status_marker = rail.split('className={"wbc-conversation-status-marker', 1)[1].split(">", 1)[0]
    assert "title={title}" not in status_marker
    assert "aria-label={title}" in status_marker
    assert "onSelect(chat.id);" in rail
    assert "onToggleCollapsed" not in rail.split('className="wbc-conversation-status-track"', 1)[1]
    assert "answerFromStatusPreview(chat" in rail
    assert "runtimeEngine.get(chat.id)" in rail

    assert ".wbc-conversation-status-track" in styles
    assert ".wbc-conversation-status-preview" in styles
    raised_preview_rail_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail:has(.wbc-conversation-status-preview) {",
        1,
    )[1].split("}", 1)[0]
    assert "z-index: 50;" in raised_preview_rail_css
    assert "z-index: 64;" in styles.split(".workbench-sidebar-dock {", 1)[1].split("}", 1)[0]
    preview_css = styles.split(".wbc-conversation-status-preview {", 1)[1].split("}", 1)[0]
    assert "background: var(--wb-card-bg-strong, var(--wb-card-bg));" in preview_css
    assert "transparent" not in preview_css.split("background:", 1)[1].split(";", 1)[0]
    assert "backdrop-filter" not in preview_css
    assert "bottom: 270px;" in styles
    assert "pointer-events: auto;" in styles
    assert "width: 32px;" in styles
    assert '"workbenchChat.track.running"' in i18n
    assert '"workbenchChat.track.attention"' in i18n
    assert '"workbenchChat.track.result"' in i18n
    assert '"workbenchChat.track.failed"' in i18n


def test_collapsed_chat_rail_status_positions_preserve_order_and_bounds():
    root = Path(__file__).resolve().parents[1]
    source = workbench_chat_source()
    helper = source.split("function wbcConversationTrackPositions", 1)[1].split(
        "\nfunction wbcConversationTrackRuntimeText", 1
    )[0]
    script = (
        "function wbcConversationTrackPositions"
        + helper
        + """
const positioned = wbcConversationTrackPositions([
  {index: 0, id: "a"},
  {index: 1, id: "b"},
  {index: 2, id: "c"},
  {index: 9, id: "d"}
], 10);
process.stdout.write(JSON.stringify(positioned));
"""
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=root,
    )
    positioned = json.loads(result.stdout)
    positions = [item["position"] for item in positioned]
    assert positions == sorted(positions)
    assert positions[0] >= 6
    assert positions[-1] <= 94
    assert all(right > left for left, right in zip(positions, positions[1:]))

    measured_script = (
        "function wbcConversationTrackPositions"
        + helper
        + """
const positioned = wbcConversationTrackPositions([
  {index: 5, chat: {id: "attention"}}
], 10, {attention: {position: 110, expandedX: 29, trackHeight: 300}});
process.stdout.write(JSON.stringify(positioned));
"""
    )
    measured_result = subprocess.run(
        ["node", "-e", measured_script],
        check=True,
        capture_output=True,
        text=True,
        cwd=root,
    )
    measured_item = json.loads(measured_result.stdout)[0]
    assert measured_item["position"] == 94
    assert measured_item["expandedPosition"] == 110
    assert measured_item["expandedX"] == 29
    assert measured_item["collapseY"] == -48
    assert measured_item["measured"] is True


def test_collapsed_chat_rail_measures_expanded_row_centres_for_spatial_mapping():
    source = workbench_chat_source()
    rail = source.split("function WbcRail(", 1)[1].split(
        "// Conversation main (column 3)", 1
    )[0]

    assert "trackGeometryByChatId" in rail
    assert "useWbcLayoutEffect" in rail
    assert 'rail.querySelectorAll(".wbc-chat-card[data-chat-id]")' in rail
    assert "rect.top + (rect.height / 2) - trackRect.top" in rail
    assert "trackHeight: trackRect.height" in rail
    assert "!collapsed && !renderedRailMotionPhase && iconRect" in rail
    assert "iconRect.left + (iconRect.width / 2) - trackRect.left" in rail
    assert "measuredExpandedX != null" in rail
    assert 'rail.addEventListener("scroll", measure, true)' in rail
    assert "new ResizeObserver(measure)" in rail
    assert "orderedChats.length, trackGeometryByChatId" in rail
    assert "trackMeasuredExpandedRef" in rail
    measurement = rail.split("useWbcLayoutEffect(function () {", 1)[1].split(
        "}, [collapsed, projectId, visibleTrackLayoutKey, renderedRailMotionPhase, railDragActive]);",
        1,
    )[0]
    assert "if (renderedRailMotionPhase || railDragActive) return undefined" in measurement
    assert "if (collapsed && trackMeasuredExpandedRef.current) return undefined" in measurement
    assert "if (!collapsed) trackMeasuredExpandedRef.current = true" in measurement
    assert "renderedRailMotionPhase, railDragActive" in rail
    assert "!railDragActive && statusTrackItems.map" in rail
    assert 'item.state.kind === "result"' in rail
    assert ': WBC_ICONS.running;' in rail
    assert "!dragState\n      && chatTrackState" in rail
    assert "railDragWasActiveRef.current = true" in rail
    assert "setTrackGeometryByChatId({});" in rail

    drop_clone = rail.split("function renderDropClone", 1)[1].split(
        "function renderChatCard", 1
    )[0]
    assert "chatRailVisualState(chat)" in drop_clone
    assert "{visualState.icon}" in drop_clone
    assert "{WBC_ICONS.file}" not in drop_clone

    styles = workbench_style_source()
    base_track = styles.split(".wbc-conversation-status-track {", 1)[1].split("}", 1)[0]
    collapsed_track = styles.split(
        ".workbench-grid.integrated-sidebars .wbc-rail.workbench-integrated-rail.is-collapsed .wbc-conversation-status-track {",
        1,
    )[1].split("}", 1)[0]
    assert "visibility: hidden" not in base_track
    assert "--wbc-track-blue: #4f83e8" in base_track
    assert "top: 62px" in base_track
    assert "bottom: 270px" in base_track
    assert "clip-path" not in base_track
    assert "overflow: hidden" not in base_track
    assert "visibility" not in collapsed_track

    anchor_css = styles.split(".wbc-conversation-status-anchor {", 1)[1].split("}", 1)[0]
    collapsed_anchor_css = styles.split(
        ".workbench-grid.integrated-sidebars .wbc-rail.workbench-integrated-rail.is-collapsed .wbc-conversation-status-anchor {",
        1,
    )[1].split("}", 1)[0]
    unmeasured_collapsed_anchor_css = styles.split(
        ".workbench-grid.integrated-sidebars .wbc-rail.workbench-integrated-rail.is-collapsed .wbc-conversation-status-anchor:not(.is-measured) {",
        1,
    )[1].split("}", 1)[0]
    assert "left: var(--wbc-track-expanded-x, 29px)" in anchor_css
    assert "top: var(--wbc-track-expanded-position, var(--wbc-track-position))" in anchor_css
    assert "transition:" not in anchor_css
    assert "top 360ms" not in anchor_css
    assert "left: var(--wbc-track-collapsed-x)" in collapsed_anchor_css
    assert "transition:" not in collapsed_anchor_css
    assert "top:" not in collapsed_anchor_css
    assert "--wbc-track-collapsed-x: 23px" in base_track
    assert "wbc-status-enable-interaction" in collapsed_anchor_css
    assert "animation: none" in unmeasured_collapsed_anchor_css
    assert "pointer-events: none" in unmeasured_collapsed_anchor_css
    assert ".wbc-conversation-status-glyph" in styles
    assert ".wbc-conversation-status-dot" in styles
    expanding_css = styles.split(
        ".wbc-rail.is-status-expanding .wbc-conversation-status-anchor {", 1
    )[1].split("}", 1)[0]
    collapsing_css = styles.split(
        ".wbc-rail.is-status-collapsing .wbc-conversation-status-anchor {", 1
    )[1].split("}", 1)[0]
    assert "left var(--wb-sidebar-motion-duration) var(--wb-sidebar-motion-ease)" in expanding_css
    assert "transform var(--wb-sidebar-motion-duration) var(--wb-sidebar-motion-ease)" in expanding_css
    assert "left 360ms var(--wb-sidebar-motion-ease) 80ms" in collapsing_css
    assert "transform 360ms var(--wb-sidebar-motion-ease) 80ms" in collapsing_css
    assert "is-status-expanding .wbc-conversation-status-glyph" in styles
    assert "opacity 180ms ease 420ms" in styles
    assert "is-status-collapsing .wbc-conversation-status-glyph" in styles
    assert "opacity 180ms ease" in styles
    assert "railMotionCollapsedRef.current !== !!collapsed" in rail
    assert 'collapsed ? "collapsing" : "expanding"' in rail
    assert '}, 630);' in rail
    reduced_motion_css = styles.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "transition-delay: 0ms !important" in reduced_motion_css
    assert "wbc-status-enable-interaction 240ms" in collapsed_anchor_css
    assert ".wbc-chat-card.track-marker-ready .wbc-chat-row-icon" in styles
    marker_css = styles.split(".wbc-conversation-status-marker {", 1)[1].split("}", 1)[0]
    assert "opacity: 0" in marker_css
    assert "is-collapsed .wbc-conversation-status-marker" in styles
    settled_row_icon_css = styles.split(
        ".wbc-chat-card.track-marker-ready .wbc-chat-row-icon {", 1
    )[1].split("}", 1)[0]
    assert "opacity: 1" in settled_row_icon_css
    assert "is-status-expanding .wbc-chat-card.track-marker-ready .wbc-chat-row-icon" in styles
    assert "is-status-collapsing .wbc-chat-card.track-marker-ready .wbc-chat-row-icon" in styles
    assert "@keyframes wbc-status-enable-interaction" in styles

    collapsed_list_css = styles.split(
        ".workbench-grid.integrated-sidebars .wbc-rail.workbench-integrated-rail.is-collapsed .wbc-chat-list {",
        1,
    )[1].split("}", 1)[0]
    assert "width: calc(var(--wb-rail-w-open) - 18px)" in collapsed_list_css
    assert "align-self: flex-start" in collapsed_list_css


def test_status_preview_answers_the_target_background_chat_without_opening_it():
    source = workbench_chat_source()
    answer = frontend_module_source("features/chat/chat-action-controller.jsx")
    page_answer_bridge = source.split("function answerQuestionForChat", 1)[1].split(
        "// Regenerate the last assistant reply", 1
    )[0]
    preview = source.split("function WbcConversationStatusPreview", 1)[1].split(
        "function WbcRail", 1
    )[0]

    assert "model.answerChat(chatId, questionId, optionText" in answer
    assert "runtimeEngine.update(chatId" in answer
    assert "setChats(function (previous)" in answer
    assert "chatCache.details[chatId]" in answer
    assert "return answerQuestionForChat(chatId" in page_answer_bridge
    assert "isPermissionQuestionKind(kind)" in preview
    assert "pending.allowCustom" in preview
    assert "onAnswer(pending.id, text, resumeMode)" in preview
    assert "wbcPermissionOptionLabel(option, index, options.length)" in preview
    assert "var actionOptions = options.length" in preview


def test_composer_model_flyout_lists_agent_row_before_model_row():
    source = workbench_chat_source()
    root_panel = source.split('{modelPanel === "root" && (', 1)[1].split(
        '{modelPanel === "agents" && agentPickerEnabled && (', 1
    )[0]

    agent_marker = 'wbcT("workbenchChat.agent", "Agent")'
    model_marker = 'wbcT("workbenchChat.model", "Model")'
    assert "agentPickerEnabled && (" in root_panel
    assert agent_marker in root_panel
    assert model_marker in root_panel
    assert root_panel.index(agent_marker) < root_panel.index(model_marker)


def test_permission_buttons_submit_original_option_id():
    source = workbench_chat_source()
    option_value = source.split("function wbcQuestionOptionValue(", 1)[1].split(
        "function wbcIsLiveAgentRequest", 1
    )[0]
    answer = frontend_module_source("features/chat/chat-action-controller.jsx")
    question_buttons = source.split("function WbcQuestionPrompt", 1)[1].split(
        "function WbcErrorNotice", 1
    )[0]

    assert "option.optionId" in option_value
    assert "option.optionId" in option_value.split("option.label", 1)[0]
    assert 'onAnswer(pq.id, wbcQuestionOptionValue(opt)' in question_buttons
    assert '{ type: "option", optionId: String(optionText || "") }' in answer


def test_quick_chat_inherits_agent_binding_without_picker():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-quick-chat.jsx").read_text(
        encoding="utf-8"
    )
    snapshot_block = source.split(
        "var [selectedChatSnapshot, setSelectedChatSnapshot] = useQuickChatState(null);", 1
    )[1].split("function refetchTargets(", 1)[0]

    assert "model.getChat(selected.chatId)" in snapshot_block
    assert "agent: snapshot && snapshot.agent ? snapshot.agent : undefined" in snapshot_block
    assert "modelAccess: snapshot && snapshot.modelAccess ? snapshot.modelAccess : undefined" in snapshot_block
    assert "capabilities: snapshot && snapshot.capabilities ? snapshot.capabilities : undefined" in snapshot_block
    assert "capabilitiesRevision: snapshot ? snapshot.capabilitiesRevision : undefined" in snapshot_block

    composer_call = source.split("React.createElement(chatService.Composer, {", 1)[1].split(
        "})", 1
    )[0]
    assert "chat: composerChat" in composer_call
    assert "onDraftAgentChange" not in composer_call


def test_event_id_dedupe_is_bounded():
    source = workbench_chat_source()
    stream = source.split("function consumeEventStream(", 1)[1].split(
        "function sendMessage(chatId, input, handlers, signal)", 1
    )[0]

    assert "var WBC_EVENT_ID_DEDUPE_LIMIT = 4096;" in stream
    assert "function rememberEventId(eventId)" in stream
    assert "seenEventIds.has(eventId)" in stream
    assert "seenEventOrder.shift()" in stream
    assert "seenEventIds.delete(oldest)" in stream
    assert "if (!rememberEventId(eventId)) return;" in stream


def test_workbench_split_chat_renders_and_answers_pending_question():
    source = workbench_chat_source()
    split_chat = source.split("function WbcChatSplit({", 1)[1].split(
        "function WbcSideAgentSplitResizer", 1
    )[0]

    assert "function answerPendingQuestion(questionId, optionText, resumeMode)" in split_chat
    assert "var current = chatIdRef.current;" in split_chat
    assert "WorkbenchChatModel.answerChat(current, questionId, answer" in split_chat
    assert "message.questionPrompt" in split_chat
    assert "chat.pendingQuestion" in split_chat
    assert "<WbcQuestionPrompt" in split_chat
    assert "onAnswer={answerPendingQuestion}" in split_chat


def test_workbench_split_chat_adopts_retry_boundary_as_transcript_state():
    source = workbench_chat_source()
    split_pane = frontend_module_source("features/chat/split-pane.jsx")
    split_chat = source.split("function WbcChatSplit({", 1)[1].split(
        "function WbcSideAgentSplitResizer", 1
    )[0]
    refresh = split_pane.split("function wbcRefreshSplitChat(options)", 1)[1].split(
        "function WbcChatSplit({", 1
    )[0]
    runtime_effect = split_chat.split(
        "function applyRuntime(snapshot)", 1
    )[1].split("useWbcLayoutEffect(function ()", 1)[0]

    assert "var requestSequence = ++options.refreshSequenceRef.current;" in refresh
    assert "options.refreshSequenceRef.current !== requestSequence" in refresh
    assert "options.runtimeEngine.get(requestedId)" in refresh
    assert refresh.index("wbcPreserveLiveTimelineAnchors(") < refresh.index("options.setChat(reconciled)")
    assert "if (next && next.retryTruncateAfterMessageId)" in runtime_effect
    assert "setChat(function (current)" in runtime_effect
    assert "wbcPreserveLiveTimelineAnchors(current, current, next)" in runtime_effect
    projection = split_chat.split("var messages = wbcReconcileLiveUserMessages(", 1)[1].split(
        "var displayMessages", 1
    )[0]
    assert "retryTruncateAfterMessageId" not in projection


def test_permission_prompt_localizes_capability_ids_and_hides_internal_fingerprint():
    root = Path(__file__).resolve().parents[1]
    chat = workbench_chat_source()
    i18n = workbench_i18n_source()
    model = (root / "src/cyrene/workbench/webui/frontend/workbench-model.jsx").read_text(encoding="utf-8")
    runtime = workbench_runtime_source()

    assert "function wbcPermissionQuestionText(pending)" in chat
    assert "i18n.permissionQuestionText(pending, i18n.getLang())" in chat
    assert "function workbenchPermissionQuestionText(pending, lang)" in i18n
    assert '/^cyrene-(?:setting|lifecycle):/' in i18n
    assert '"skill.install": "InstallSkill"' in i18n
    assert 'wbcPermissionOptionLabel(opt, i, options.length)' in chat
    assert '? wbcPermissionQuestionText(pending)' in chat
    assert 'value: wbcQuestionOptionValue(option)' in chat
    assert 'raw = raw.replace(/\\.r[23]$/, "");' in i18n
    assert 'toolName = toolName.replace(/\\.r[23]$/, "");' in i18n
    assert '"cyrene.ui.click": "CyreneUIClick"' in i18n
    assert '"cyrene.ui.double_click": "CyreneUIDoubleClick"' in i18n
    assert '"toolName.CyreneUIDoubleClick": "双击界面组件"' in i18n
    for kind in (
        "external_delivery_request",
        "external_upload_confirmation",
        "destructive_confirmation",
        "self_configuration_confirmation",
        "host_lifecycle_confirmation",
    ):
        assert f"{kind}: true" in model
        assert f'"{kind}"' in runtime


def test_agent_chat_flow_glint_is_semantic_transient_and_reduced_motion_safe():
    root = Path(__file__).resolve().parents[1]
    chat = workbench_chat_source()
    css = workbench_style_source()
    i18n = workbench_i18n_source()
    surface = (root / "src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx").read_text(encoding="utf-8")

    assert 'WBC_AGENT_CHAT_FLOW_EVENT = "cyrene:agent-chat-flow"' in chat
    assert "WBC_AGENT_CHAT_FLOW_STATE = Object.create(null)" in chat
    assert "function wbcAgentChatFlowSnapshot(chatId)" in chat
    assert 'wbcNotifyAgentChatFlow("created", chat.id)' in chat
    assert 'wbcNotifyAgentChatFlow("typing", chatId)' not in chat
    assert "WBC_AGENT_CHAT_FLOW_TTLS = { created: 4200, typing: 3200 }" in chat
    assert "applyFlow(wbcAgentChatFlowSnapshot(chatId))" in chat
    assert "get_highlight_element: function () { return composerBoxRef.current; }" in chat
    assert 'data-agent-flow={agentFlow || undefined}' in chat
    assert ".wbc-rail .wbc-chat-card.agent-flow::after" in css
    assert ".wbc-composer-box.agent-flow::before" in css
    assert "@keyframes wbc-agent-chat-flow" in css
    assert "@keyframes wbc-agent-composer-flow" in css
    assert "wbc-agent-chat-flow 3.2s" in css
    assert "wbc-agent-composer-flow 3.2s" in css
    assert "function showAgentControlHighlight(element, nodeId, actionId)" in surface
    assert "entryHighlightElement(item), nodeId, actionId" in surface
    assert "settleAgentControlHighlight(controlHighlightSequence)" in surface
    assert "AGENT_CONTROL_FLOW_CYCLE_MS = 3200" in surface
    assert "prefers-reduced-motion:reduce" in surface
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert '"workbenchChat.agentFlow.typing": "Agent 正在输入消息"' in i18n


def test_agent_terminal_control_reuses_surface_glint_and_visible_terminal_is_unambiguous():
    root = Path(__file__).resolve().parents[1]
    chat = workbench_chat_source()
    terminal = (root / "src/cyrene/workbench/webui/frontend/terminal/entry.jsx").read_text(encoding="utf-8")
    surface = (root / "src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx").read_text(encoding="utf-8")

    assert 'error: "multiple_terminals_visible", terminals: visible' in chat
    assert 'terminals: visible,' in chat
    assert 'side: rect.left + rect.width / 2 <= window.innerWidth / 2 ? "left" : "right"' in chat
    assert 'visible.sort(function (a, b)' in chat
    assert 'data-terminal-id={String(terminalId || "")}' in terminal
    control = surface.split('if (method === "terminal.control")', 1)[1].split(
        'return { ok: false, error: "unsupported_surface_method" }', 1
    )[0]
    assert "showAgentControlHighlight(" in control
    assert "settleAgentControlHighlight(controlSequence)" in control
    assert "showAgentCursor(terminalPoint" in control


def test_workbench_context_tab_has_durable_agent_inbox_card():
    source = workbench_chat_source()
    css = workbench_style_source()
    context_tab = source.split("function WbcContextTab", 1)[1].split(
        "function WbcArtifactsTab", 1
    )[0]
    live_hook = source.split("function useWbcLiveInbox", 1)[1].split(
        "function wbcInboxStatus", 1
    )[0]
    inbox_card = source.split("function WbcInboxCard", 1)[1].split(
        "function WbcContextTab", 1
    )[0]
    inbox_call = '<WbcInboxCard liveView={inboxView} hideTitle={true} />'
    assert inbox_call in context_tab
    assert context_tab.index(inbox_call) > context_tab.index(
        'workbenchChat.conversationContext'
    )
    assert context_tab.index(inbox_call) < context_tab.index(
        'workbenchChat.usedPluginPacks'
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
    assert 'className="wbc-context-empty-label"' in inbox_card
    assert "hideTitle && feed.length === 0" not in inbox_card
    assert "queueDepth === 0 && !historyTruncated ? (" in inbox_card
    assert "data.eventsTruncated || data.historyWindowTruncated" in inbox_card
    assert "workbenchChat.inbox.historyTruncated" in inbox_card
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
    assert "wbcInboxArgumentPreview" not in inbox_card
    assert "tool_activity" not in inbox_card
    assert 'className="wbc-inbox-event-preview"' in inbox_card
    assert 'aria-live="polite"' in source
    assert ".wbc-inbox-card" in css
    inbox_card_css = css.split(".wbc-inbox-card", 1)[1].split(
        ".wbc-inbox-head,", 1
    )[0]
    inbox_meta_css = css.split(".wbc-inbox-event-meta {", 2)[2].split("}", 1)[0]
    inbox_meta_time_css = css.split(".wbc-inbox-event-meta time {", 1)[1].split("}", 1)[0]
    assert "padding-bottom: 10px" in inbox_card_css
    assert "justify-content: space-between" in inbox_meta_css
    assert "white-space: nowrap" in inbox_meta_time_css
    assert inbox_card.index('className="wbc-inbox-event-preview"') > inbox_card.index('className="wbc-inbox-event-meta"')
    assert ".wbc-inbox-event-meta code" not in css


def test_new_context_projection_drives_used_plugin_pack_list():
    source = workbench_chat_source()
    helper = "function wbcUsedPluginPacks" + source.split(
        "function wbcUsedPluginPacks", 1
    )[1].split("function WbcContextTab", 1)[0]
    script = f"""
eval({json.dumps(helper)});
process.stdout.write(JSON.stringify(wbcUsedPluginPacks({{
  usedPluginPacks: ["cyrene_subagent", "", "cyrene_code", "cyrene_subagent"],
  usedStandalonePlugins: ["CustomLint", "cyrene_code"]
}})));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == [
        "cyrene_subagent",
        "cyrene_code",
        "CustomLint",
    ]


def test_plugin_center_and_hooks_are_registered_in_settings():
    root = Path(__file__).resolve().parents[1]
    capabilities = frontend_module_source("features/settings/capabilities.jsx")
    overlay = frontend_module_source("settings-overlay.jsx")
    settings_index = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "settings-index.jsx"
    ).read_text(encoding="utf-8")
    app_entry = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "entry" / "app.jsx").read_text(
        encoding="utf-8"
    )
    assert 'plugins: "plugin-registry"' in overlay
    assert "settings.voiceCapability" in capabilities
    assert '{ id: "plugin-registry", labelKey: "settings.pluginRegistry", icon: "package" }' in overlay
    assert '{ id: "plugin-registry", labelKey: "settings.pluginRegistry" }' in settings_index
    assert '{ id: "hooks", labelKey: "settings.hooks", icon: "webhook" }' in overlay
    assert '{ id: "hooks", labelKey: "settings.hooks" }' in settings_index
    assert 'tab === "hooks" && React.createElement(HooksPanel' in overlay
    assert 'tab: "hooks", labelKey: "settings.hooks"' in settings_index

    assert 'import "../settings-overlay.jsx"' in app_entry
    assert 'import "../shared/settings-index.jsx"' in app_entry


def test_plugin_center_combines_runtime_status_with_plugin_owned_intake_apis():
    overlay = frontend_module_source("settings-overlay.jsx")
    plugin_settings = frontend_module_source("features/settings/custom-plugins.jsx")
    plugin_intake = frontend_module_source("features/settings/plugin-center-add.jsx")
    plugin_catalog = frontend_module_source("features/settings/plugin-center-catalog.jsx")
    plugin_cli_hooks = frontend_module_source("features/settings/plugin-center-cli-hooks.jsx")
    plugin_mcp = frontend_module_source("features/settings/plugin-center-mcp.jsx")
    plugin_service = frontend_module_source("platform/plugins.jsx")

    assert '{ id: "plugin-registry", labelKey: "settings.pluginRegistry", icon: "package" }' in overlay
    assert 'tab === "plugin-registry" && React.createElement(PluginRegistryPanel' in overlay
    assert 'api.json("/api/plugins", { toast: false })' in plugin_service
    assert 'api.json("/api/plugins/reload", {' in plugin_service
    assert '"/api/plugins/tools/"' in plugin_service
    assert 'method: remove ? "DELETE" : "PATCH"' in plugin_service
    assert "standalone_plugins" in plugin_service
    assert "attached_application_packs" in plugin_service
    assert "application_restart_required" in plugin_service
    assert "EventSource" not in plugin_service
    assert "/contributions" not in plugin_service
    assert '"/api/plugins/packs/" + encodeURIComponent(packId) + "/call"' in plugin_service
    assert 'settingsFetch("/api/plugins/activation", {' in plugin_settings
    assert 'import { PluginCenterAddButton, PluginCenterPage } from "./plugin-center-add.jsx"' in plugin_settings
    assert plugin_settings.index("React.createElement(PluginCenterAddButton") < plugin_settings.index('t("settings.pluginReload",')
    assert "if (showCenter)" in plugin_settings
    assert "React.createElement(PluginCenterPage" in plugin_settings
    assert 'onClose: function () { setShowCenter(false) }' in plugin_settings
    assert "wb-plugin-center-page-shell" in plugin_settings
    assert 't("settings.pluginCreateInNewChatHint", "To create a Plugin, start a new chat.")' in plugin_settings
    for kind in ("recommended", "skill", "mcp", "cli", "toolchain", "agent"):
        assert kind in plugin_intake
    assert 'selected === "recommended" ? "/api/plugin-center/overview"' in plugin_intake
    assert '"/api/plugin-center/" + encodeURIComponent(kind) + "/install"' in plugin_intake
    assert 'body: JSON.stringify({ extension_id: id, request: request })' in plugin_intake
    assert '"/api/plugin-center/skill/inspect"' in plugin_intake
    assert '"/api/plugin-center/skill/upload"' in plugin_intake
    assert '"/api/plugin-center/skill/import"' in plugin_intake
    assert '"/api/plugin-center/sources"' in frontend_module_source("features/settings/plugin-center-admin.jsx")
    assert '"/api/plugin-center/mcp/" + encodeURIComponent(editor.name) + "/configuration"' in plugin_mcp
    assert 'modules.indexOf("cli") >= 0' in plugin_intake
    assert 'selected === "cli" ? "cyrene_cli"' in plugin_intake
    assert '"/api/hooks"' in plugin_cli_hooks
    assert '"/api/plugin-center/cli/hooks"' in plugin_cli_hooks
    assert '"/api/plugin-center/cli/hooks/proposals/"' in plugin_cli_hooks
    assert 'function HooksPanel(props)' in plugin_cli_hooks
    assert 'className="wb-hook-filter"' in plugin_cli_hooks
    assert 'settings.hookCount' in plugin_cli_hooks
    assert 'settings.hookEnabled' in plugin_cli_hooks
    assert 'payload.system_hooks' in plugin_cli_hooks
    assert 'settings.systemHooks' in plugin_cli_hooks
    assert 'settings.userHooks' in plugin_cli_hooks
    assert 'onEdit={edit}' in plugin_cli_hooks
    assert 'function SystemHookEditor(props)' in plugin_cli_hooks
    assert 'var SYSTEM_EVENTS' in plugin_cli_hooks
    assert 'SYSTEM_EVENTS.map' in plugin_cli_hooks
    assert 'settings.systemHookWhen' in plugin_cli_hooks
    assert 'settings.systemHookThen' in plugin_cli_hooks
    assert 'action: action' in plugin_cli_hooks
    assert 'settings.systemHookWarningBody' in plugin_cli_hooks
    assert '"/api/hooks/system/"' in plugin_cli_hooks
    assert 'acknowledge_risk: true' in plugin_cli_hooks
    assert 'function HookRequestEditor(props)' in plugin_cli_hooks
    assert 'function AgentHookEditor(props)' in plugin_cli_hooks
    assert 'function ToolMatcherSelect(props)' in plugin_cli_hooks
    assert 'payload.tools' in plugin_cli_hooks
    assert 'settings.hookToolAll' in plugin_cli_hooks
    assert 'matcher: isToolEvent(draft.event) ? draft.matcher.trim() || "*" : "*"' in plugin_cli_hooks
    assert '"/api/plugin-center/cli/hooks/generate"' in plugin_cli_hooks
    assert '"/regenerate"' in plugin_cli_hooks
    assert 'settings.hookActionInstruction' in plugin_cli_hooks
    assert 'settings.hookSaveAndRegenerate' in plugin_cli_hooks
    assert 'settings.hookPriorityRange' in plugin_cli_hooks
    assert 'window.setInterval' in plugin_cli_hooks
    assert 'manageable={customAvailable}' in plugin_cli_hooks
    assert 'aria-expanded={expanded ? "true" : "false"}' in plugin_cli_hooks
    assert 'className="wb-hook-card-actions"' in plugin_cli_hooks
    assert plugin_cli_hooks.index('wb-hook-card-edit"') < plugin_cli_hooks.index('className="wb-hook-chevron-button"')
    assert '"/api/plugin-center/cli/" + encodeURIComponent(id) + "/configure-hook"' in plugin_intake
    assert "wb-plugin-center-page" in plugin_intake
    assert "McpToolDetails" in plugin_catalog
    assert "AgentTab" in plugin_intake
    assert '"mcp-providers": "plugin-registry"' in overlay
    assert '"setting-plugin-packs": "setting-plugin-registry"' in overlay
    assert '"setting-standalone-plugins": "setting-plugin-registry"' in overlay
    assert "McpProvidersPanel" not in overlay
    assert 'agent_exposure: item.agent_exposure === "direct" ? "discoverable" : "direct"' in plugin_settings
    assert 'settings.pluginToolAgentDescription' in plugin_settings
    assert 'settings.pluginToolDelete' in plugin_settings
    assert 'item.locked === true' in plugin_settings
    assert 'kind === "model"' in plugin_settings
    assert 'item.source === "core"' in plugin_settings
    assert 'model_visible === true' not in plugin_settings
    assert "registry.failures" in plugin_settings
    assert "registry.packs" in plugin_settings
    assert "registry.standalonePlugins" in plugin_settings
    assert "projectId" not in plugin_settings
    for removed_api in (
        "/api/plugins/install",
        "/api/plugins/contributions",
        "/api/plugins/events",
        "/enabled",
    ):
        assert removed_api not in plugin_service + plugin_settings


def test_workbench_inbox_cleanup_aborts_and_ignores_a_late_response():
    source = workbench_chat_source()
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
    source = workbench_chat_source()
    load_effect = source.split("// Load the full transcript when the selection changes.", 1)[1].split(
        "// Viewer / content tabs belong to one conversation", 1
    )[0]

    assert load_effect.index("setActiveChat(null)") < load_effect.index("if (!activeChatId)")
    assert "new AbortController()" in load_effect
    assert "controller.abort()" in load_effect
    assert "Promise.all" not in load_effect
    assert "model.getChat(activeChatId, requestOptions)" in load_effect
    assert 'model.getSubagents(activeChatId, "", requestOptions)' in load_effect
    assert load_effect.index("wbcPreserveLiveTimelineAnchors(") < load_effect.index("setSubagentData(payload)")
    assert 'String(activeChat.id || "") === String(activeChatId || "")' in source
    assert "chat={visibleChat}" in source
    assert "chat={visibleChat || selectedChatSummary}" in source
    assert "var conversationLoading = loading || chatLoading;" in source
    assert "loading={conversationLoading}" in source


def test_workbench_chat_loading_keeps_lightweight_overview_visible():
    source = workbench_chat_source()
    i18n = workbench_i18n_source()

    assert "var selectedChatSummary = chats.find" in source
    assert "chatSummary={selectedChatSummary}" in source
    assert "chatDetailed={!!visibleChat}" in source
    assert "loading && !chat" in source
    assert "messages.length === 0 && !runtime && !loading && !error" in source
    assert '"workbenchChat.loadingConversation": "加载对话中..."' in i18n
    assert '"workbenchChat.error.transcriptPrefix": "对话详情：{error}"' in i18n


def test_workbench_chat_loading_is_centered_in_the_rail():
    source = workbench_chat_source()
    styles = workbench_style_source()

    assert '"wbc-chat-list workbench-integrated-rail-body" + (loading ? " is-loading" : "")' in source
    assert 'className="workbench-muted wbc-rail-loading" role="status"' in source
    assert '!loading && renderRailSection("recent"' in source
    assert "!loading && visibleGroupRailItems.length > 0" in source
    assert 'renderRailSection("groups"' in source
    loading_styles = styles.split(".wbc-chat-list.is-loading {", 1)[1].split("}", 1)[0]
    assert "align-items: center;" in loading_styles
    assert "justify-content: center;" in loading_styles


def test_unified_search_only_shows_loading_status_while_results_are_pending():
    source = frontend_module_source("features/chat/rail.jsx")
    input_markup = source.split('data-cyrene-node-id="chat_search_input"', 1)[1].split(
        "/>\n", 1
    )[0]
    unified_results = source.split("{unifiedSearchActive ? (", 1)[1].split(
        ") : (", 1
    )[0]

    assert "setGlobalFilesLoading(codeAvailable && Boolean(nextQuery.trim()))" in input_markup
    assert "{globalFilesLoading ? (" in unified_results
    loading_branch, settled_branch = unified_results.split(") : <>", 1)
    assert 'className="workbench-muted wbc-rail-loading" role="status"' in loading_branch
    assert "wbc-unified-search-section is-chat" not in loading_branch
    assert "wbc-unified-search-section is-file" not in loading_branch
    assert "wbc-unified-search-section is-chat" in settled_branch
    assert "wbc-unified-search-section is-file" in settled_branch


def test_every_workspace_sidebar_card_can_swipe_between_module_tabs():
    source = workbench_shell_source()
    navigation = frontend_module_source("features/shell/navigation-controller.jsx")

    grid_markup = source.split('className={"workbench-grid integrated-sidebars"', 1)[1]
    assert "var handleSidebarModuleWheel = navigationActions.onModuleWheel" in source
    sidebar_wheel = navigation.split("function onModuleWheel(event) {", 1)[1].split(
        "return { openPage:", 1
    )[0]
    assert 'target.closest(".workbench-integrated-rail, .workbench-sidebar-dock.is-persistent")' in sidebar_wheel
    assert 'var moduleOrder = Array.isArray(enabledModules) && enabledModules.length' in navigation
    assert '["schedule", "board", "work", "knowledge", "memory"]' in navigation
    assert "Math.abs(deltaX) <= Math.abs(deltaY) * 1.15" in sidebar_wheel
    assert "Math.abs(gesture.delta) < 44" in sidebar_wheel
    assert "gesture.lockedUntil = now + 420" in sidebar_wheel
    assert "openPage(moduleOrder[nextIndex])" in sidebar_wheel
    assert "onWheel={handleSidebarModuleWheel}" in grid_markup


def test_active_conversation_rail_and_board_use_their_current_menu_dismissal_surfaces():
    chat = workbench_chat_source()
    board_source = frontend_module_source("features/chat/conversation-board.jsx")

    conversation_rail = chat.split("function WbcRail(", 1)[1].split(
        "function WbcProjectRail(", 1
    )[0]
    board = board_source.split("function ConversationBoard(", 1)[1].split(
        "function ConversationBoardCard(", 1
    )[0]
    assert '{menuId && <div className="wb-card-menu-scrim"' in conversation_rail
    assert 'className="wb-card-menu" role="menu"' in conversation_rail
    assert 'document.addEventListener("pointerdown", closeRailMenuOnOutside, true);' in conversation_rail
    assert 'target.closest(".wb-card-menu") || target.closest(".wb-card-menu-btn")' in conversation_rail
    assert 'document.removeEventListener("pointerdown", closeRailMenuOnOutside, true);' in conversation_rail
    assert 'document.addEventListener("pointerdown", closeOnOutside, true);' in board
    assert 'target.closest(".wb-card-menu")' in board
    assert 'className="wb-card-menu-scrim"' not in board


def test_board_new_button_only_creates_a_conversation():
    source = workbench_shell_source()
    styles = workbench_style_source()
    board_source = frontend_module_source("features/chat/conversation-board.jsx")
    board = board_source.split("function ConversationBoard(", 1)[1].split(
        "function ConversationBoardCard(", 1
    )[0]

    assert 'className="wb-board-new-btn" onClick={onCreateChat}' in board
    assert 't("conversationBoard.newConversation")' in board
    assert 'aria-haspopup="menu"' not in board
    assert 'onCreateChat={context.actions.createChat}' in source
    assert ".wb-board-new-btn" in styles


def test_board_search_only_filters_manually_added_conversations():
    styles = workbench_style_source()
    translations = workbench_i18n_source()
    board_source = frontend_module_source("features/chat/conversation-board.jsx")
    board = board_source.split("function ConversationBoard(", 1)[1].split("function ConversationBoardCard(", 1)[0]

    assert 'var [query, setQuery] = useWorkbenchState("");' in board
    assert 'var orderedIds = layout.columns' in board
    assert 'orderedIds.map(function (chatId) { return chatMap.get(chatId); })' in board
    assert 'wbHasConversationDrag(event)' in board
    assert 'className="wb-board-search"' in board
    assert 'type="search"' in board
    assert 'placeholder={t("conversationBoard.searchPlaceholder")}' in board
    assert 't("conversationBoard.noSearchResults")' in board
    assert board.index('className="wb-board-new-btn"') < board.index('className="wb-board-search"')
    assert '.wb-board-search {' in styles
    assert '.wb-board-search:focus-within {' in styles
    search_icon_css = styles.split('.wb-board-search > span {', 1)[1].split('}', 1)[0]
    assert 'align-items: center;' in search_icon_css
    assert 'justify-content: center;' in search_icon_css
    assert 'line-height: 0;' in search_icon_css
    assert '"conversationBoard.searchPlaceholder": "Search board conversations"' in translations
    assert '"conversationBoard.searchPlaceholder": "搜索看板中的对话"' in translations


def test_board_chat_navigation_uses_the_work_module_gate():
    navigation = frontend_module_source("features/shell/shell-navigation.jsx")
    navigation = "\n".join(
        line for line in navigation.splitlines() if not line.startswith("import ")
    ).rsplit("\nexport {", 1)[0]
    script = """
const opened = [];
const workbenchServices = {
  navigation: () => ({setPending: () => {}, clearPending: () => {}}),
};
const setTimeout = () => 0;
""" + navigation + """
const project = {id: "project-1"};
wbNavigateFromSearch({
  store: {projects: [project], activeProject: project, activeProjectId: project.id},
  enabledModules: ["schedule", "board", "work", "knowledge", "memory"],
  setFullPage: (page) => opened.push(page),
  getSelectProject: () => () => {},
}, {type: "chat", projectId: project.id, chatId: "chat-1"});
process.stdout.write(JSON.stringify(opened));
"""

    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == ["chat"]


def test_module_target_navigation_is_consumed_across_mount_and_slow_load():
    root = Path(__file__).resolve().parent.parent
    library = (root / "src/cyrene/workbench/webui/frontend/workbench-library.jsx").read_text(encoding="utf-8")
    memory = (root / "src/cyrene/workbench/webui/frontend/workbench-memory.jsx").read_text(encoding="utf-8")
    pending = (root / "src/cyrene/workbench/webui/frontend/shared/runtime/pending-module-selection.jsx").read_text(encoding="utf-8")

    assert 'if (!pending || pending.type !== "knowledge") return false;' in pending
    assert 'var documentId = String(pending.docId || pending.id || "");' in pending
    assert 'var directId = React.useRef("");' in pending
    assert "pendingKnowledge.isDirect(currentId)" in library
    assert 'window.addEventListener("cyrene:workbench-navigate", onNavigate);' in pending
    assert "apply();" in pending
    assert "createPendingMemorySelection" in memory
    assert "pendingMemorySelection.capture();" in memory
    assert "load({ background: !!cached }).then(applyPendingMemorySelection);" in memory
    assert "var id = pendingId || capture(pending);" in pending


def test_conversation_board_cards_share_the_standard_card_background():
    styles = frontend_module_source("features/chat/settings-surfaces.css")

    board_card_css = styles.split(".wb-board-card {", 1)[1].split("}", 1)[0]
    assert "background: var(--wb-card-bg);" in board_card_css
    assert ".wb-board-card.is-chat {" not in styles


def test_board_cards_use_a_consistent_compact_height():
    styles = frontend_module_source("features/chat/settings-surfaces.css")

    board_card_css = styles.split(".wb-board-card {", 1)[1].split("}", 1)[0]
    title_css = styles.split(".wb-board-card-title b {", 1)[1].split("}", 1)[0]
    meta_css = styles.split(".wb-board-card-meta {", 1)[1].split("}", 1)[0]

    assert "height: calc(140px * var(--wb-ui-font-scale, 1));" in board_card_css
    assert "box-sizing: border-box;" in board_card_css
    assert "text-overflow: ellipsis;" in title_css
    assert "white-space: nowrap;" in title_css
    assert "margin-top: auto;" in meta_css
    assert "flex-wrap: nowrap;" in meta_css


def test_non_chat_floating_cards_share_the_chat_topbar_baseline():
    root = Path(__file__).resolve().parent.parent
    styles = workbench_style_source()
    library_styles = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.css"
    ).read_text(encoding="utf-8")

    shell_css = styles.split(".workbench-shell {", 1)[1].split("}", 1)[0]
    grid_css = styles.split(".workbench-grid {", 1)[1].split("}", 1)[0]
    chat_grid_css = styles.split(".workbench-grid.is-chat {", 1)[1].split("}", 1)[0]
    rail_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail {", 1
    )[1].split("}", 1)[0]
    detail_shell_css = styles.split(".wb-floating-detail-shell {", 1)[1].split("}", 1)[0]
    library_detail_css = library_styles.split(".wb-lib-right {", 1)[1].split("}", 1)[0]
    compact_shell_css = styles.split(
        'html[data-density="compact"] .workbench-shell,', 1
    )[1].split("}", 1)[0]

    assert "--wb-topbar-height: 58px;" in shell_css
    assert "--wb-floating-card-top-gap: 0px;" in shell_css
    assert "--wb-floating-card-bottom-gap: 12px;" in shell_css
    assert "padding-top: var(--wb-topbar-height);" in shell_css
    assert "height: calc(100vh - var(--wb-topbar-height));" in grid_css
    assert "margin-top: calc(0px - var(--wb-topbar-height));" in chat_grid_css
    assert (
        "height: calc(100% - var(--wb-floating-card-top-gap) - "
        "var(--wb-floating-card-bottom-gap));" in rail_css
    )
    assert (
        "margin: var(--wb-floating-card-top-gap) 4px "
        "var(--wb-floating-card-bottom-gap) 12px;" in rail_css
    )
    expected_detail_padding = (
        "padding: var(--wb-floating-card-top-gap) 12px "
        "var(--wb-floating-card-bottom-gap);"
    )
    assert expected_detail_padding in detail_shell_css
    assert expected_detail_padding in library_detail_css
    assert "--wb-topbar-height: 50px;" in compact_shell_css
    assert "--wb-floating-card-top-gap: 1px;" in compact_shell_css


def test_chat_empty_rail_state_centers_vertically():
    styles = workbench_style_source()
    chat_source = frontend_module_source("features/chat/rail.jsx")

    assert '!loading && visibleRailItemCount === 0 ? " is-empty" : ""' in chat_source
    chat_empty_css = styles.split(
        ".wbc-chat-list.is-empty .wbc-chat-list-primary {", 1
    )[1].split("}", 1)[0]
    assert "display: flex;" in chat_empty_css
    assert "align-items: center;" in chat_empty_css
    assert "justify-content: center;" in chat_empty_css


def test_workbench_uses_one_live_chat_page_rail_for_work_and_board():
    root = Path(__file__).resolve().parent.parent
    source = workbench_shell_source()
    chat = workbench_chat_source()
    styles = workbench_style_source()
    chat_index = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "features" / "chat" / "index.jsx"
    ).read_text(
        encoding="utf-8"
    )

    assert "workspaceContent: sharedWorkspace" in source
    assert "function WorkbenchProjectRail(" not in source
    assert "<WorkbenchStableSurface active={presentation.isChat || presentation.isBoard}>" in source
    assert 'workspaceContent ? <div className="wbc-external-workspace-host"' in chat
    assert '.wbc-page.wbc-external-workspace > :not(.wbc-rail):not(.wbc-external-workspace-host) {' in styles
    external_host = styles.split(".wbc-page > .wbc-external-workspace-host {", 1)[1].split("}", 1)[0]
    assert "grid-column: 3 / 5;" in external_host
    assert "function WbcRail(" in chat
    assert "function WbcProjectRail(props)" in chat
    project_rail = chat.split("function WbcProjectRail(props)", 1)[1].split(
        "// ---------------------------------------------------------------------------\n// Conversation main",
        1,
    )[0]
    assert "return <WbcRail" in project_rail
    assert "Rail: WbcProjectRail" in chat_index


def test_budget_limits_use_existing_toast_and_inline_error_surfaces():
    root = Path(__file__).resolve().parent.parent
    chat = workbench_chat_source()
    events = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "platform" / "events.jsx").read_text(
        encoding="utf-8"
    )
    runtime_hooks = frontend_module_source("features/chat/runtime-page-hooks.jsx")

    assert 'String(err.code || "").startsWith("budget_")' in chat
    assert 'showToast(wbcErrorText(err), "error")' in chat
    assert 'event.type === "budget_warning"' in chat
    assert 'showToast(wbcErrorText(warningError), "warning")' in chat
    assert 'var budgetError = String(error && error.code || "").startsWith("budget_");' in runtime_hooks
    assert '"budget_warning"' in events


def test_workbench_keeps_one_persistent_module_dock_across_workspace_switches():
    root = Path(__file__).resolve().parent.parent
    source = workbench_shell_source()
    chat = workbench_chat_source()
    library = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    schedule = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-schedule.jsx").read_text(
        encoding="utf-8"
    )
    memory = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(
        encoding="utf-8"
    )
    styles = workbench_style_source()

    grid_markup = source.split('className={"workbench-grid integrated-sidebars"', 1)[1]
    assert "<WorkbenchModuleRail" not in grid_markup
    assert "function WorkbenchSidebarDock(" in source
    module_dock = source.split("function WorkbenchSidebarDock(", 1)[1].split(
        "var WB_CONVERSATION_BOARD_COLUMNS", 1
    )[0]
    for module_id in ("work", "board", "knowledge", "schedule", "memory"):
        assert f'id: "{module_id}"' in module_dock
    dock_item_positions = [
        module_dock.index(f'id: "{module_id}"')
        for module_id in ("schedule", "board", "work", "knowledge", "memory")
    ]
    assert dock_item_positions == sorted(dock_item_positions)
    assert "WorkbenchModuleAccount" not in module_dock
    assert "function renderSidebarDockSlot()" in source
    assert 'return <div className="workbench-sidebar-dock-slot" aria-hidden="true" />;' in source
    assert "var [railCollapsed, setRailCollapsed] = useWorkbenchState" in source
    navigation = frontend_module_source("features/shell/navigation-controller.jsx")
    assert "var toggleWorkspaceSidebar = navigationActions.toggleSidebar" in source
    assert "function toggleSidebar()" in navigation
    assert 'localStorage.setItem("wb-rail-collapsed", next ? "1" : "0")' in navigation
    assert '(railCollapsed ? " rail-collapsed" : "")' in source
    assert 'ref={wbApplyStoredRightWidth}' in source
    assert grid_markup.count("<WorkbenchSidebarDock") == 1
    assert "persistent={true}" in grid_markup
    assert "collapsed={railCollapsed}" in grid_markup
    assert "moduleDock: presentation.isChat || presentation.isBoard ? context.navigation.renderDockSlot() : null" in source
    assert "moduleDock: p.isKnowledge ? navigation.renderDockSlot() : null" in source
    assert "moduleDock: p.isSchedule ? navigation.renderDockSlot() : null" in source
    assert "moduleDock: p.isMemory ? navigation.renderDockSlot() : null" in source
    assert "function WorkbenchProjectRail(" not in source
    assert "navCollapsed: context.navigation.railCollapsed" in source
    assert "sidebarCollapsed: navigation.railCollapsed" in source
    assert "collapsed={railCollapsed}" in source
    assert 'className="wbc-module-nav"' not in chat
    assert "{moduleDock}" in chat
    assert "props.moduleDock" in library
    assert "props.moduleDock" in schedule
    assert "props.moduleDock" in memory
    assert ".workbench-grid.integrated-sidebars {" in styles
    assert ".workbench-sidebar-dock-nav {" in styles
    assert ".workbench-sidebar-dock-slot {" in styles
    assert ".workbench-sidebar-dock.is-persistent {" in styles
    persistent_dock_css = styles.split(
        ".workbench-sidebar-dock.is-persistent {", 1
    )[1].split("}", 1)[0]
    assert "position: absolute;" in persistent_dock_css
    assert "left: 27px;" in persistent_dock_css
    assert "width: calc(var(--wb-rail-w) - 46px);" in persistent_dock_css
    assert (
        ".workbench-grid.integrated-sidebars.is-chat > "
        ".workbench-sidebar-dock.is-persistent {" not in styles
    )
    assert ".workbench-sidebar-dock::before {" in styles
    dock_separator = styles.split(".workbench-sidebar-dock::before {", 1)[1].split("}", 1)[0]
    assert "content: none;" in dock_separator
    assert ".workbench-integrated-rail {" in styles
    integrated_grid_css = styles.split(".workbench-grid.integrated-sidebars {", 1)[1].split("}", 1)[0]
    integrated_rail_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail {", 1
    )[1].split("}", 1)[0]
    assert "--wb-rail-w: 300px;" in integrated_grid_css
    assert "--wb-sidebar-dock-height: 120px;" in integrated_grid_css
    assert "--wb-sidebar-motion-duration: 440ms;" in integrated_grid_css
    assert "--wb-sidebar-motion-ease: cubic-bezier(.22, 1.14, .36, 1);" in integrated_grid_css
    assert "--wb-floating-rail-border:" in integrated_grid_css
    assert "--wb-floating-rail-radius: 18px;" in integrated_grid_css
    assert "--wb-floating-rail-bg:" in integrated_grid_css
    assert "--wb-floating-rail-bg: var(--wb-composer-surface-color);" in integrated_grid_css
    assert "--wb-floating-rail-shadow:" in integrated_grid_css
    assert "0 5px 14px rgba(15, 23, 42, .02)" in integrated_grid_css
    agent_notification_css = styles.split(".wbc-agent-notification {", 1)[1].split("}", 1)[0]
    assert "border: 1px solid" in agent_notification_css
    assert "border-left:" not in agent_notification_css
    assert "--wb-floating-control-border:" in integrated_grid_css
    assert "--wb-floating-control-radius: 12px;" in integrated_grid_css
    assert "--wb-floating-control-bg:" in integrated_grid_css
    assert "--wb-floating-control-shadow:" in integrated_grid_css
    assert "flex: 0 0 calc(var(--wb-rail-w) - 16px);" in integrated_rail_css
    assert "width: calc(var(--wb-rail-w) - 16px);" in integrated_rail_css
    assert (
        "margin: var(--wb-floating-card-top-gap) 4px "
        "var(--wb-floating-card-bottom-gap) 12px;" in integrated_rail_css
    )
    assert "border: var(--wb-floating-rail-border);" in integrated_rail_css
    assert "border-radius: var(--wb-floating-rail-radius);" in integrated_rail_css
    assert "background: var(--wb-floating-rail-bg);" in integrated_rail_css
    assert "box-shadow: var(--wb-floating-rail-shadow);" in integrated_rail_css
    assert ".workbench-integrated-rail:is(:hover, :focus-within)" in styles
    shared_glass_css = styles.split(
        "/* One shared glass material for every integrated module rail.", 1
    )[1].split("}", 1)[0]
    assert "box-shadow: var(--wb-floating-rail-shadow);" in shared_glass_css
    assert "0 18px 44px" not in shared_glass_css
    assert "color-mix(in srgb, var(--wb-floating-rail-tint) 65%, transparent)" not in integrated_grid_css
    assert ".workbench-grid.integrated-sidebars.rail-collapsed {" in styles
    collapsed_grid_css = styles.split(
        ".workbench-grid.integrated-sidebars.rail-collapsed {", 1
    )[1].split("}", 1)[0]
    assert "--wb-rail-w: 64px;" in collapsed_grid_css
    assert ".workbench-grid.integrated-sidebars .workbench-integrated-rail.is-collapsed {" in styles
    collapsed_rail_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail.is-collapsed {", 1
    )[1].split("}", 1)[0]
    assert "flex-basis: 48px;" in collapsed_rail_css
    assert "width: 48px;" in collapsed_rail_css
    assert "@keyframes wb-sidebar-dock-stack-in" not in styles
    assert "@keyframes wb-sidebar-dock-row-in" not in styles
    assert ".workbench-grid.integrated-sidebars .workbench-integrated-rail-head {" in styles
    shared_head_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail-head {", 1
    )[1].split("}", 1)[0]
    assert "flex: 0 0 56px;" in shared_head_css
    assert "height: 56px;" in shared_head_css
    assert "min-height: 56px;" in shared_head_css
    assert "padding: 0 12px;" in shared_head_css
    assert ".workbench-integrated-rail-body {" in styles
    shared_body_css = styles.split(".workbench-integrated-rail-body {", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 auto;" in shared_body_css
    assert "overflow-y: auto;" in shared_body_css
    assert "workbench-integrated-rail-head workbench-integrated-rail-search-head" in chat
    assert "wbc-chat-list workbench-integrated-rail-body" in chat
    assert "wb-lib-sidebar-head workbench-integrated-rail-head" in library
    assert "wb-lib-side-scroll workbench-integrated-rail-body" in library
    assert "wb-sched-rail-head workbench-integrated-rail-head" in schedule
    assert "wb-sched-rail-scroll workbench-integrated-rail-body" in schedule
    assert "wb-mem-rail-head workbench-integrated-rail-head" in memory
    assert "wb-mem-rail-scroll workbench-integrated-rail-body" in memory
    dock_css = styles.split(".workbench-sidebar-dock {", 1)[1].split("}", 1)[0]
    dock_nav_css = styles.split(".workbench-sidebar-dock-nav {", 1)[1].split("}", 1)[0]
    dock_button_css = styles.split(".workbench-sidebar-dock-nav button {", 1)[1].split("}", 1)[0]
    dock_hover_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-sidebar-dock-nav button:hover {",
        1,
    )[1].split("}", 1)[0]
    dock_active_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-sidebar-dock-nav button.active {",
        1,
    )[1].split("}", 1)[0]
    assert "grid-template-rows: 56px 46px;" in dock_css
    assert "height: var(--wb-sidebar-dock-height);" in dock_css
    assert "min-height: var(--wb-sidebar-dock-height);" in dock_css
    assert "margin: auto 14px 12px;" in dock_css
    assert "height: 56px;" in dock_nav_css
    assert "grid-row: 1;" in dock_nav_css
    assert "--wb-dock-nav-bg: color-mix(in srgb, var(--wb-card-bg) 78%, var(--wb-control-bg));" in dock_nav_css
    assert "border: 1px solid color-mix(in srgb, var(--wb-line-2) 58%, transparent);" in dock_nav_css
    assert "border-radius: 15px;" in dock_nav_css
    assert "background: var(--wb-dock-nav-bg);" in dock_nav_css
    assert "height: 48px;" in dock_button_css
    assert ".workbench-grid.integrated-sidebars .workbench-sidebar-dock-nav button {" in styles
    assert "color: var(--wb-faint);" in dock_button_css
    assert "transform: translateY(-1px);" in dock_hover_css
    assert "var(--wb-dock-nav-bg)" in dock_hover_css
    assert "background: var(--wb-active-bg);" in dock_active_css
    assert "color: var(--wb-accent-strong);" in dock_active_css
    dock_icon_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-sidebar-dock-nav button svg {",
        1,
    )[1].split("}", 1)[0]
    dock_label_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-sidebar-dock-nav button b {",
        1,
    )[1].split("}", 1)[0]
    assert "width: 18px;" in dock_icon_css
    assert "stroke-width: 2;" in dock_icon_css
    assert "font-size: calc(10px * var(--wb-ui-font-scale, 1));" in dock_label_css
    assert "font-weight: 680;" in dock_label_css
    assert "grid-template-rows: 48px;" in dock_nav_css
    assert "grid-template-columns var(--wb-sidebar-motion-duration) var(--wb-sidebar-motion-ease)" in dock_nav_css
    collapsed_dock_nav_css = styles.split(
        ".workbench-grid.integrated-sidebars > .workbench-sidebar-dock.is-persistent.is-collapsed .workbench-sidebar-dock-nav {",
        1,
    )[1].split("}", 1)[0]
    assert "grid-template-columns: repeat(5, minmax(0, 0fr));" in collapsed_dock_nav_css
    assert "grid-template-rows: 36px;" in collapsed_dock_nav_css
    for index, y in enumerate((0, 38, 76, 114, 152), start=1):
        row_rule = styles.split(
            f".workbench-grid.integrated-sidebars > .workbench-sidebar-dock.is-persistent.is-collapsed .workbench-sidebar-dock-nav button:nth-child({index}) {{",
            1,
        )[1].split("}", 1)[0]
        assert f"transform: translateY({y}px);" in row_rule
    project_menu_styles = styles.split(".workbench-top-project-menu {", 1)[1].split("}", 1)[0]
    assert "background: var(--wb-flyout-bg);" in project_menu_styles
    assert "backdrop-filter: none;" in project_menu_styles
    assert ".workbench-top-project-menu-list:has(.workbench-top-project-row.menu-open) {" in styles
    project_row_active_styles = styles.split(
        ".workbench-top-project-row.active {", 1
    )[1].split("}", 1)[0]
    project_row_hover_styles = styles.split(
        ".workbench-top-project-row:hover,", 1
    )[1].split("}", 1)[0]
    assert "background: color-mix(in srgb, var(--wb-accent) 8%, transparent);" in project_row_active_styles
    assert "background: color-mix(in srgb, var(--wb-accent) 10%, transparent);" in project_row_hover_styles
    assert "box-shadow:" not in project_row_active_styles
    assert ".workbench-top-project-row:nth-last-child(-n + 2) .workbench-top-project-actions {" in styles
    project_actions_markup = source.split('className="workbench-top-project-actions"', 1)[1].split("</div>", 1)[0]
    assert project_actions_markup.count("<svg") == 3
    assert '<span>{t("rail.editProject")}</span>' in project_actions_markup
    assert '<span>{t("rail.editMemory")}</span>' in project_actions_markup
    assert '<span>{t("rail.deleteProject")}</span>' in project_actions_markup
    assert project_actions_markup.index('t("rail.editProject")') < project_actions_markup.index(
        't("rail.editMemory")'
    ) < project_actions_markup.index('t("rail.deleteProject")')
    active_chat_css = styles.split(
        ".wbc-rail .wbc-chat-card.active,",
        1,
    )[1].split("}", 1)[0]
    assert "background: var(--wb-active-bg);" in active_chat_css
    assert "color: var(--wb-accent-strong);" in active_chat_css
    project_name_styles = styles.split(".workbench-top-project-copy b {", 1)[1].split("}", 1)[0]
    project_path_styles = styles.rsplit(".workbench-top-project-copy small {", 1)[1].split("}", 1)[0]
    assert "font-size: calc(14px * var(--wb-ui-font-scale, 1));" in project_name_styles
    assert "font-size: calc(11.5px * var(--wb-ui-font-scale, 1));" in project_path_styles
    project_switcher_styles = styles.split(".workbench-project-switcher-btn {", 1)[1].split("}", 1)[0]
    project_chevron_styles = styles.split(".workbench-project-switcher-chevron {", 1)[1].split("}", 1)[0]
    assert "width: 150px;" in project_switcher_styles
    assert "flex: 0 1 150px;" in project_switcher_styles
    assert "margin-left: auto;" in project_chevron_styles
    assert 'html[data-platform="darwin"] .workbench-project-switcher-btn {' in styles
    assert "margin-left: -3px;" in styles
    search_head_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail-search-head {",
        1,
    )[1].split("}", 1)[0]
    assert "padding-inline: 12px;" in search_head_css
    assert ".wb-board-tool-btn," in styles
    assert ".wb-mem-searchbox," in styles
    assert ".wb-sched-viewseg," in styles
    assert "border: var(--wb-floating-control-border);" in styles
    assert "background: var(--wb-floating-control-bg);" in styles
    assert ".workbench-grid.integrated-sidebars .wb-sched-viewseg button.on {" in styles
    integrated_grid = styles.split(".workbench-grid.integrated-sidebars.is-chat,", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: minmax(0, 1fr);" in integrated_grid


def test_chat_rail_show_all_expands_recent_items():
    source = workbench_chat_source()

    assert 'var [showAllRecent, setShowAllRecent] = useWbcState(false);' in source
    assert "showAllRecent\n    ? recentRailItems\n    : recentRailItems.slice(0, recentOverviewLimit)" in source
    assert "setShowAllRecent(true);" in source


def test_primary_workspace_pages_replay_the_conversation_style_enter_motion():
    source = workbench_shell_source()
    styles = workbench_style_source()

    assert "function WorkbenchStableSurface({ active, enterMotion, children })" in source
    assert source.count("enterMotion={true}") == 3
    assert '<WorkbenchStableSurface active={presentation.isChat || presentation.isBoard}>' in source
    assert '<WorkbenchStableSurface active={p.isKnowledge} enterMotion={true}>' in source
    assert '<WorkbenchStableSurface active={p.isSchedule} enterMotion={true}>' in source
    assert '<WorkbenchStableSurface active={p.isMemory} enterMotion={true}>' in source
    assert "WorkbenchBoardModuleSurface" not in source
    enter_css = styles.split(
        ".workbench-stable-surface.has-page-enter-motion.is-active :is(", 1
    )[1].split("}", 1)[0]
    for primary_surface in (
        ".wb-sched-main",
        ".wb-lib-main",
        ".wb-mem-main",
        ".workbench-conversation-board",
        ".workbench-main",
    ):
        assert primary_surface in enter_css
    assert "wbc-pane-card-settle 360ms cubic-bezier(.22, 1.16, .36, 1)" in enter_css
    assert "backface-visibility: hidden;" in enter_css
    assert "@keyframes wb-workbench-page-enter" not in styles
    assert (
        ".workbench-grid.integrated-sidebars .workbench-sidebar-dock-nav,\n"
        "  .workbench-stable-surface.has-page-enter-motion.is-active :is("
    ) in styles
    reduced_motion = styles.split(
        ".workbench-stable-surface.has-page-enter-motion.is-active :is(\n"
        "    .wb-sched-main,", 1
    )[1]
    assert "animation: none !important;" in reduced_motion


def test_knowledge_sidebar_is_persistent_at_compact_desktop_widths():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.css").read_text(
        encoding="utf-8"
    )

    assert 'className: "wb-lib-sidebar workbench-integrated-rail" + (props.sidebarCollapsed ? " is-collapsed" : "")' in source
    assert "props.moduleDock" in source
    assert "sidebarOpenState" not in source
    assert 'className: "wb-lib-side-close"' not in source
    assert 'className: "wb-lib-sidebar-toggle"' not in source
    compact_styles = styles.split("@media (max-width: 1180px) {", 1)[1].split("}", 1)[0]
    assert "position: absolute" not in compact_styles
    assert "translateX" not in compact_styles


def test_workbench_chat_plan_confirmation_can_continue_in_auto_mode():
    source = workbench_chat_source()
    i18n = workbench_i18n_source()

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
    source = workbench_chat_source()

    assert 'mode: input.mode || "default"' in source
    assert "preparedInput.mode = wbcNormalizePermissionMode(" in source
    assert "activeChat.permissionMode" in source
    assert "var answerMode = wbcNormalizePermissionMode(" in source
    assert "{ mode: answerMode }" in source
    assert "var replayMode = wbcNormalizePermissionMode(" in source
    assert "{ retry: true, forkReplay: true, mode: replayMode }" in source
    assert 'mode: "auto", command: ""' not in source


def test_workbench_surfaces_permission_reviews_and_describes_auto_accurately():
    source = workbench_chat_source()
    agent_events = frontend_module_source("features/chat/agent-events.jsx")
    i18n = workbench_i18n_source()

    assert 'event.type === "auto_review" || event.type === "permission_decision"' in source
    assert 'kind: "permission"' in source
    assert '"permission.reviewed": { handler: "onPermissionReviewed"' in agent_events
    assert "function wbcAgentPermissionReviewPayload(event)" in agent_events
    assert "function applyPermissionReviewEvent(chatId, event)" in source
    assert "onPermissionReviewed: function (event)" in source
    assert '"workbenchChat.mode.auto.desc": "Review permission requests automatically"' in i18n
    assert '"workbenchChat.mode.auto.desc": "自动审核权限请求"' in i18n


def test_workbench_attachment_preview_falls_back_without_overflowing():
    source = workbench_chat_source()
    styles = workbench_style_source()

    assert "failedImagePreviews" in source
    assert "onError={function () {" in source
    assert "showImagePreview" in source
    assert "function WbcMessageAttachment({ file, onOpenFile })" in source
    assert 'var bubbleClassName = "wbc-bubble" + (hasInlineImage ? " with-inline-image" : "");' in source
    message_attachment = source.split(
        "function WbcMessageAttachment({ file, onOpenFile })", 1
    )[1].split("function WbcUserMessage(", 1)[0]
    assert "onError={function () { setImageFailed(true); }}" in message_attachment
    assert 'className="wbc-inline-image"' in message_attachment
    assert 'className="wbc-inline-image-preview"' in message_attachment
    assert 'className="wbc-inline-image-footer"' in message_attachment
    assert 'className="wbc-inline-image-actions"' in message_attachment
    assert 'className={"wbc-inline-media " + viewKind}' in message_attachment
    assert (
        '<video src={file.url} controls preload="metadata" playsInline draggable="false" />'
        in message_attachment
    )
    assert (
        '<audio src={file.url} controls preload="metadata" draggable="false" />'
        in message_attachment
    )
    assert 'className="wbc-inline-media-footer"' in message_attachment
    assert 'draggable="true"' in message_attachment
    assert "wbcStartFileDrag(event, file)" in message_attachment
    assert 'draggable="false"' in message_attachment
    assert "wbcCanOpenExternally(file)" in message_attachment
    assert "WBC_ICONS.openExternal" in message_attachment
    assert 'className: "wbc-inline-image-action"' in message_attachment
    assert source.count("<WbcMessageAttachment key=") == 4
    assert "workbenchServices.library().FileVisual" in source
    assert 'className="wbc-attach-file"' in message_attachment
    assert 'wbcT("workbenchChat.openPreview", "Open preview")' in message_attachment
    assert 'className={"wbc-msg-attachments" + (msg.content ? " after-copy" : "")}' in source
    assert 'className={"wbc-attach-card" + (showImagePreview ? " image" : " file")}' in source
    assert ".wbc-attach-file-open" in styles
    assert ".wbc-inline-image-preview img" in styles
    message_row_rule = styles.split(".wbc-msg-row {", 1)[1].split("}", 1)[0]
    assert "justify-content: flex-end;" in message_row_rule
    image_bubble_rule = styles.split(
        ".wbc-msg.user .wbc-bubble.with-inline-image {", 1
    )[1].split("}", 1)[0]
    assert "width: min(calc(280px + 28px), 100%);" in image_bubble_rule
    assert "max-width: 100%;" in image_bubble_rule
    assert ".wbc-inline-image-actions .wbc-inline-image-action" in styles
    assert ".wbc-inline-media > video" in styles
    assert ".wbc-inline-media > audio" in styles
    assert "max-height: min(58vh, 520px);" in styles
    assert "width: calc(100% - 16px);" in styles
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


def test_workbench_agent_media_and_references_reuse_inline_attachment_renderer():
    source = workbench_chat_source()
    i18n = workbench_i18n_source()

    agent_files = source.split("function WbcAgentFiles(", 1)[1].split(
        "function WbcMediaReferences(", 1
    )[0]
    references = source.split("function WbcMediaReferences(", 1)[1].split(
        "function WbcLiveAgentArtifacts(", 1
    )[0]
    assistant = source.split("function WbcAssistantMessage(", 1)[1].split(
        "function WbcHeartbeat(", 1
    )[0]
    assert (
        '["image", "video", "audio"].indexOf(wbcFileViewKind(file)) !== -1 && file.url'
        in agent_files
    )
    assert "<WbcMessageAttachment" in agent_files
    assert "wbcStartFileDrag(event, file)" in agent_files
    assert 'className="wbc-media-references"' in references
    assert 'wbcT("workbenchChat.mediaReferences", "References")' in references
    assert "<WbcMessageAttachment" in references
    assert "Array.isArray(msg.referenceAttachments)" in assistant
    assert "Array.isArray(msg.reference_attachments)" in assistant
    assert "<WbcMediaReferences files={referenceAttachments}" in assistant
    assert "<WbcAgentFiles files={msg.attachments}" in assistant
    assert '"workbenchChat.mediaReferences": "References"' in i18n
    assert '"workbenchChat.mediaReferences": "参考素材"' in i18n

    styles = workbench_style_source()
    assert ".wbc-media-references" in styles
    assert ".wbc-media-references-list .wbc-inline-image" in styles
    assert ".wbc-media-references-list .wbc-inline-media.video" in styles
    assert ".wbc-media-references-list .wbc-inline-media.audio" in styles


def test_file_resource_drag_temporarily_disables_native_topbar_drag_region():
    source = workbench_chat_source()
    messages = frontend_module_source("features/chat/messages.jsx")
    file_resources = frontend_module_source("features/chat/file-resources.jsx")
    styles = workbench_style_source()

    assert 'root.classList.add("wbc-resource-drag-active")' in source
    assert 'root.classList.remove("wbc-resource-drag-active")' in source
    assert 'document.addEventListener("dragend", clearResourceDragRegion, true)' in source
    assert 'document.addEventListener("drop", clearResourceDragRegion, true)' in source
    region_rule = styles.split(
        "html.wbc-resource-drag-active .workbench-topbar {", 1
    )[1].split("}", 1)[0]
    assert "-webkit-app-region: no-drag;" in region_rule
    assert "function wbcStartFilePointerDrag(event, file)" in file_resources
    assert "wbcPointInsideResourceShelf(clientX, clientY)" in file_resources
    assert 'new CustomEvent("cyrene:pin-topbar-resource", { detail: payload })' in file_resources
    assert "source.setPointerCapture(pointerId)" in file_resources
    assert "function wbcCopyFileDragAppearance(sourceNode, cloneNode)" in file_resources
    assert "wbcCopyFileDragAppearance(source, ghost)" in file_resources
    assert 'ghost.style.height = rect.height + "px"' in file_resources
    assert "wbcStartFilePointerDrag(event, file)" in messages
    assert 'draggable={wbcUsesFilePointerDrag() ? undefined : "true"}' in messages
    assert ".wbc-file-pointer-drag-ghost" in styles
    ghost_rule = styles.split(".wbc-file-pointer-drag-ghost {", 1)[1].split("}", 1)[0]
    assert "opacity:" not in ghost_rule
    assert "box-shadow:" not in ghost_rule


def test_workbench_execution_card_uses_collapsible_activity_summary():
    source = workbench_chat_source()
    styles = workbench_style_source()

    trace_rule = styles.split(".wbc-trace {", 1)[1].split("}", 1)[0]
    summary_rule = styles.split(".wbc-trace-summary {", 1)[1].split("}", 1)[0]
    dark_trace_rule = styles.split(
        'html[data-theme="dark"] .wbc-trace {', 1
    )[1].split("}", 1)[0]
    assert "border: 0;" in trace_rule
    assert "background: transparent;" in trace_rule
    assert "display: flex;" in trace_rule
    assert "flex-direction: column;" in trace_rule
    assert "background: transparent;" in dark_trace_rule
    assert "box-shadow:" not in trace_rule
    assert "min-height: 34px;" in summary_rule
    assert "width: fit-content;" in summary_rule
    assert "order: 1;" in summary_rule
    assert 'font-family: var(--mono, "IBM Plex Mono", ui-monospace, monospace);' in styles
    chevron_rule = styles.rsplit("\n.wbc-trace-summary-chevron {", 1)[1].split("}", 1)[0]
    assert "margin-left: 1px;" in chevron_rule
    assert 'className="wbc-trace-summary"' in source
    assert 'new CustomEvent("workbench:trace-disclosure"' in source
    assert "detail: { anchor: event.currentTarget, expanding: nextExpanded }" in source
    assert 'thread.addEventListener("workbench:trace-disclosure", preserveTraceDisclosureAnchor);' in source
    assert "stickRef.current = false;" in source
    assert "thread.scrollTop += anchor.getBoundingClientRect().top - anchorTop;" in source
    assert "function wbcTraceActionKind(entry)" in source
    assert "function wbcTraceActionIcon(entry)" in source
    assert 'if (kind === "search") return WBC_ICONS.search;' in source
    assert 'if (kind === "command") return WBC_ICONS.slash;' in source
    assert 'if (kind === "browser") return WBC_ICONS.browser;' in source
    icon_block = source.split("function wbcTraceActionIcon(entry)", 1)[1].split(
        "function wbcTraceCollapsedSummary", 1
    )[0]
    mapped_icons = re.findall(r"return WBC_ICONS\.([A-Za-z0-9_]+)", icon_block)
    assert len(mapped_icons) == 25
    assert len(mapped_icons) == len(set(mapped_icons))
    assert 'className="wbc-trace-entry-icon" aria-hidden="true">{WBC_ICONS.brain}' in source
    assert '{failed ? <span className="wbc-trace-entry-icon" aria-hidden="true">{wbcTraceActionIcon(entry)}</span> : null}' in source
    assert "isRunning ? wbcTraceActionIcon(entry) : WBC_ICONS.check" in source
    reasoning_icon_rule = styles.split(
        ".wbc-trace-timeline-reasoning > .wbc-trace-entry-icon {", 1
    )[1].split("}", 1)[0]
    assert "height: calc(18.17px * var(--wb-ui-font-scale, 1));" in reasoning_icon_rule
    assert "align-items: center;" in reasoning_icon_rule
    assert "function wbcTraceCollapsedSummary(entries, fallback)" in source
    assert "icon: WBC_ICONS.brain" in source
    assert 'entry && entry.kind === "phase1"' in source
    assert "if (phase1Entry) return" in source
    assert 'wbcT("workbenchChat.phase1Understood", "Understood the request")' in source
    assert "function wbcNormalizeReasoningText(text)" in source
    assert "function wbcTraceTimelineItems(entries, reasoning)" in source
    assert 'wbcT("workbenchChat.thinkingProcess", "Thinking process")' in source
    assert "reasoningOffset: String(activity.reasoning || \"\").length" in source
    assert source.count("&& !(Array.isArray(last.progress) && last.progress.length)") == 2
    assert source.count("var activityHasReasoning = !!String(latestActivity && latestActivity.reasoning") == 3
    assert source.count("appendActivity(closeActivityTimeline(latest), { createdAt: eventAt })") == 3
    assert 'className="wbc-trace-list wbc-trace-timeline"' in source
    assert "var seen = new Set();" in source
    assert "seen.has(key)" in source

    i18n = workbench_i18n_source()
    assert '"workbenchChat.thinkingProcess": "Thinking process"' in i18n
    assert '"workbenchChat.thinkingProcess": "思考过程"' in i18n

    styles = workbench_style_source()
    trace_mark = styles.split(".wbc-trace-mark {", 1)[1].split("}", 1)[0]
    trace_mark_icon = styles.split(".wbc-trace-mark > svg {", 1)[1].split("}", 1)[0]
    assert "font-size: calc(11.5px * var(--wb-ui-font-scale, 1));" in trace_mark
    assert "height: calc(1.5 * 11.5px * var(--wb-ui-font-scale, 1));" in trace_mark
    assert "width: 1em;" in trace_mark_icon
    assert "height: 1em;" in trace_mark_icon
    assert "var collapsedSummary = wbcTraceCollapsedSummary(entries, label);" in source
    assert "summaryItems.map" not in source
    assert "wbcTraceCollapsedLabel" not in source
    assert "traceAction.repeated" not in source
    assert 'className="wbc-trace-details"' in source
    assert 'className={"wbc-trace-collapse" + (expanded ? " open" : "")}' in source
    assert 'className="wbc-trace-collapse-inner"' in source
    assert "aria-hidden={!expanded}" in source
    assert 'aria-expanded={hasDetails ? expanded : undefined}' in source
    collapse_rule = styles.split(".wbc-trace-collapse {", 1)[1].split("}", 1)[0]
    collapse_open_rule = styles.split(".wbc-trace-collapse.open {", 1)[1].split("}", 1)[0]
    assert "grid-template-rows: 0fr;" in collapse_rule
    assert "opacity: 0;" in collapse_rule
    assert "grid-template-rows 200ms" in collapse_rule
    assert "grid-template-rows: 1fr;" in collapse_open_rule
    assert "opacity: 1;" in collapse_open_rule
    detail_rule = styles.split(".wbc-trace-details {", 1)[1].split("}", 1)[0]
    assert "border: 0;" in detail_rule
    assert "background: transparent;" in detail_rule
    assert 'className="wbc-trace-summary-preview"' not in source
    assert "wbc-trace-summary-preview" not in styles
    assert "var previewText = wbcToolPreviewText(entry.preview);" in source
    assert "{previewText ? <small>{wbcParenthesize(previewText)}</small> : null}" in source
    assert ".wbc-trace-details .wbc-trace-mark" not in styles
    assert 'className="wbc-trace-mark"' in source
    assert "编辑了文件" in workbench_i18n_source()
    i18n = workbench_i18n_source()
    assert '"workbenchChat.traceAction.usedSkill": "使用了技能工具"' in i18n
    assert '"workbenchChat.traceAction.usedTool": "使用了{tool}"' in i18n
    assert '"workbenchChat.traceAction.conjunction": "并"' in i18n
    assert '"workbenchChat.traceAction.executed"' not in i18n


def test_workbench_trace_summary_adds_a_verb_before_named_application_tools():
    source = workbench_chat_source()
    helpers = "function wbcTraceNormalizeName(" + source.split(
        "function wbcTraceNormalizeName(", 1
    )[1].split("function wbcNormalizeReasoningText", 1)[0]
    script = f"""
const WBC_ICONS = new Proxy({{}}, {{ get: (_target, name) => String(name) }});
const messages = {{
  "workbenchChat.traceAction.browsed": "操作了浏览器",
  "workbenchChat.traceAction.usedTool": "使用了{{tool}}",
  "workbenchChat.traceAction.conjunction": "并",
  "workbenchChat.traceAction.listSeparator": "、",
  "toolName.browser_navigate": "浏览器导航",
  "toolName.cyrene_tools": "Cyrene 应用工具"
}};
function wbcT(key, fallback, params) {{
  let value = messages[key] || fallback || key;
  Object.entries(params || {{}}).forEach(([name, replacement]) => {{
    value = value.split("{{" + name + "}}").join(String(replacement));
  }});
  return value;
}}
function wbcLocalizedToolName(toolName) {{
  const raw = String(toolName || "").trim();
  return wbcT("toolName." + raw, raw);
}}
eval({json.dumps(helpers)});
const summary = wbcTraceCollapsedSummary([
  {{ kind: "tool", tool: "browser_navigate" }},
  {{ kind: "tool", tool: "cyrene_tools" }}
]);
process.stdout.write(summary.label);
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )

    assert completed.stdout == "操作了浏览器并使用了Cyrene 应用工具"


def test_workbench_trace_timeline_removes_blank_lines_and_interleaves_tools():
    source = workbench_chat_source()
    timeline_source = "function wbcNormalizeReasoningText(" + source.split(
        "function wbcNormalizeReasoningText(", 1
    )[1].split("function WbcTraceCard", 1)[0]
    script = f"""
eval({json.dumps(timeline_source)});
const reasoning = "before\\n\\nmiddle\\n\\nafter";
const result = wbcTraceTimelineItems([
  {{ kind: "phase1", text: "Understood", preview: reasoning }},
  {{ toolCallId: "search_1", reasoningOffset: 8 }},
  {{ toolCallId: "search_2", reasoningOffset: 16 }}
], reasoning).map(item => item.kind === "reasoning"
  ? [item.kind, item.text]
  : [item.kind, item.entry.toolCallId]);
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )

    assert json.loads(completed.stdout) == [
        ["reasoning", "before"],
        ["trace", "search_1"],
        ["reasoning", "middle"],
        ["trace", "search_2"],
        ["reasoning", "after"],
    ]


def test_workbench_chat_splits_live_tools_around_intermediate_messages():
    source = workbench_chat_source()
    append_block = source.split("function appendIntermediate(chatId, message)", 1)[1].split(
        "function streamHandlers(chatId, generation)", 1
    )[0]

    assert 'type === "intermediate_message"' in source
    assert "function appendIntermediate(chatId, message)" in source
    assert "function intermediateMessageDedupeKeys(message)" in source
    assert "intermediateMessagesMatch(segmentMsg, message)" in append_block
    assert 'keys.push("content:" + normalizedContent)' in source
    assert "existingIndex >= 0" in append_block
    assert "segments: segments.concat" in source
    assert "progress: Array.isArray(message.trace) ? message.trace" in source
    assert "wbcRuntimeSegmentMessages(runtime)" in source
    assert "wbcMergeChronologicalMessages(durableMessages" in source
    assert "<WbcAssistantMessage" in source
    assert "event.assistantMessages" in source
    assert 'event.type === "assistant_message" && event.intermediate && event.message' in source


def test_workbench_intermediate_message_dedupe_matches_explicit_and_content_keys():
    source = frontend_module_source("features/chat/file-resources.jsx")
    helpers = "function intermediateMessageDedupeKeys" + source.split(
        "function intermediateMessageDedupeKeys", 1
    )[1].split("function appendIntermediate", 1)[0]
    script = f"""
eval({json.dumps(helpers)});
const text = "我先确认工作区结构，再开始处理。";
const result = {{
  explicitVsContent: intermediateMessagesMatch(
    {{ id: "direct", content: text }},
    {{ id: "preamble", content: text, liveDedupeKey: "msg_sem_example" }}
  ),
  differentContent: intermediateMessagesMatch(
    {{ id: "direct", content: text }},
    {{ id: "preamble", content: "另一条消息", liveDedupeKey: "msg_sem_example" }}
  ),
  attachmentMessages: intermediateMessagesMatch(
    {{ id: "file-1", content: text, attachments: [{{ name: "one.txt" }}] }},
    {{ id: "file-2", content: text, attachments: [{{ name: "two.txt" }}] }}
  )
}};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )

    assert json.loads(completed.stdout) == {
        "explicitVsContent": True,
        "differentContent": False,
        "attachmentMessages": False,
    }


def test_workbench_chat_retry_clears_model_output_before_start_and_reconciles_terminal_event():
    source = workbench_chat_source()
    action_controller = frontend_module_source("features/chat/chat-action-controller.jsx")
    runtime_hooks = frontend_module_source("features/chat/runtime-page-hooks.jsx")

    selection_helper = source.split("function wbcRetryTurnSelection(chat, messageId) {", 1)[1].split(
        "function wbcClearModelOutputForRetry", 1
    )[0]
    clear_helper = source.split("function wbcClearModelOutputForRetry(chat, messageId) {", 1)[1].split(
        "function wbcPreserveLiveTimelineAnchors", 1
    )[0]
    retry_block = action_controller.split("function wbcHandleRetryMessage(context, messageId) {", 1)[1].split(
        "function wbcHandleEditMessage", 1
    )[0]
    saved_block = source.split("onSaved: function (event) {", 1)[1].split(
        "onAwaitingUser:", 1
    )[0]
    awaiting_block = source.split("onAwaitingUser: function (event) {", 1)[1].split(
        "onError:", 1
    )[0]

    assert 'messages[i].role === "user"' in selection_helper
    assert 'messages[nextIndex].role === "user"' in selection_helper
    assert "messages.slice(userIndex + 1, endIndex)" in selection_helper
    assert "truncateAfterMessageId" in selection_helper
    assert "chat.messages.slice(0, selection.userIndex + 1).concat(chat.messages.slice(selection.endIndex))" in clear_helper
    assert "wbcClearModelOutputForRetry(cached, targetMessageId)" in retry_block
    assert "wbcClearModelOutputForRetry(previous, targetMessageId)" in retry_block
    assert "context.beginChatHydration(chatId);" in retry_block
    assert retry_block.index("context.beginChatHydration(chatId);") < retry_block.index("setRetryClearingMessageIds")
    assert "setRetryClearingMessageIds(selection.outputIds);" in retry_block
    assert "context.retryClearCommitRef.current = startRetryAfterClear;" in retry_block
    assert "setTimeout(startRetryAfterClear" not in retry_block
    assert 'event.animationName === "wbc-retry-output-clear"' in source
    assert "onRetryClearAnimationEnd();" in source
    assert "context.runtimeEngine.start(chatId, {" in retry_block
    assert "retry: true" in retry_block
    assert "mode: retryMode" in retry_block
    assert "retryTruncateAfterMessageId: selection.truncateAfterMessageId" in retry_block
    assert "retrySuppressed" not in retry_block
    assert 'className={retryClearing ? "retry-clearing" : ""}' in source
    projection = source.split("function useWbcConversationProjection(", 1)[1].split(
        "function wbcRenderHistoryMessage", 1
    )[0]
    assert "retryTruncateAfterMessageId" not in projection
    assert "retrySuppressed" not in projection
    retry_truncate = runtime_hooks.split("function wbcRuntimeRetryTruncate(context, chatId, truncateAfterMessageId) {", 1)[1].split(
        "function wbcRuntimeReplyStream", 1
    )[0]
    assistant_saved = runtime_hooks.split("function wbcRuntimeAssistantSaved(context, chatId, assistantMessages, terminalEvent) {", 1)[1].split(
        "function wbcRuntimeAgentArtifact", 1
    )[0]
    awaiting_user = runtime_hooks.split("function wbcRuntimeAwaitingUser(context, chatId, pendingQuestion) {", 1)[1].split(
        "function wbcRuntimeInterrupted", 1
    )[0]
    assert "wbcTruncateMessagesAfterUser" in retry_truncate
    assert "context.beginChatHydration(chatId);" in retry_truncate
    assert "replacedIds" not in retry_truncate
    assert "retrySuppressed" not in retry_truncate
    assert "context.beginChatHydration(chatId);" in assistant_saved
    assert "context.beginChatHydration(chatId);" in awaiting_user
    styles = workbench_style_source()
    clear_animation = styles.split(".wbc-thread-item.retry-clearing {", 1)[1].split("}", 1)[0]
    assert "animation: wbc-retry-output-clear 180ms" in clear_animation
    assert "grid-template-rows: 1fr" in clear_animation
    assert "will-change: grid-template-rows, margin-block-end, opacity, transform" in clear_animation
    clear_keyframes = styles.split("@keyframes wbc-retry-output-clear {", 1)[1].split(
        "@media (prefers-reduced-motion: reduce)", 1
    )[0]
    assert "grid-template-rows: 0fr" in clear_keyframes
    assert "margin-block-end: -16px" in clear_keyframes
    assert "filter:" not in clear_keyframes
    assert "clip-path:" not in clear_keyframes
    assert 'fire("onRetryTruncate"' in saved_block
    assert 'fire("onRetryTruncate"' in awaiting_block


def test_workbench_chat_error_retry_replays_failed_message_instead_of_reloading():
    source = workbench_chat_source()

    runtime_error = frontend_module_source("features/chat/runtime-page-hooks.jsx").split(
        "function wbcRuntimeError(context, chatId, error, failureState) {", 1
    )[1].split("function wbcRuntimeResync", 1)[0]
    main_props = source.split("<WbcMain", 1)[1].split("/>", 1)[0]

    assert 'context.setErrorKind("message");' in runtime_error
    assert 'onRetry={errorKind === "message" ? handleRetryMessage : (errorKind === "memory" ? handleGenerateMemory : retryLoad)}' in main_props
    assert 'errorKind={errorKind}' in main_props
    assert '<WbcErrorNotice message={error} kind={errorKind} onRetry={onRetry} />' in source
    assert 'wbcT("workbenchChat.error.messageTitle", "Message processing failed")' in source
    assert 'wbcT("workbenchChat.error.messageBody"' in source


def test_workbench_chat_waits_for_terminal_failure_and_retains_it_until_retry():
    source = workbench_chat_source()

    stream_failure = source.split("function failRun(chatId, err, generation) {", 1)[1].split(
        "function appendActivity", 1
    )[0]
    stream_owner = source.split("function ownStream(chatId, generation, streamPromise, ac, model) {", 1)[1].split(
        "// Begin a streamed send", 1
    )[0]
    page_error = frontend_module_source("features/chat/runtime-page-hooks.jsx").split(
        "function wbcRuntimeError(context, chatId, error, failureState) {", 1
    )[1].split("function wbcRuntimeResync", 1)[0]
    selection_load = source.split(
        "// Load the full transcript when the selection changes.", 1
    )[1].split("model.getChat(activeChatId", 1)[0]
    start_run = source.split("function start(chatId, input, model) {", 1)[1].split(
        "function reconnect", 1
    )[0]

    assert 'fire("onError", chatId, err, { terminal: false })' in stream_owner
    assert "failRun(chatId, err);" not in stream_owner
    assert 'publishLifecycle(chatId, "failed", err || {})' in stream_failure
    assert 'fire("onError", chatId, failures[chatId], { terminal: true })' in stream_failure
    assert "if (terminal)" in page_error
    assert 'runStatus: "failed"' in page_error
    assert 'wbcSettleChatListItem(chat, "failed", error)' in page_error
    assert "if (terminal && String(context.activeChatIdRef.current" in page_error
    assert "runtimeEngine.getFailure(activeChatId)" in selection_load
    assert "clearFailure(chatId);" in start_run


def test_late_terminal_event_from_completed_stream_cannot_fail_next_turn():
    result = _run_workbench_runtime_js(
        """
(() => {
  let firstHandlers = null;
  let secondHandlers = null;
  let terminalErrors = 0;
  const pending = new Promise(() => {});
  WorkbenchChatRuntimes.setHooks({
    onError: (_chatId, _error, state) => {
      if (state && state.terminal) terminalErrors += 1;
    }
  });
  WorkbenchChatRuntimes.start("chat-race", {
    message: "first", clientRequestId: "first"
  }, {
    sendMessage: (_chatId, _input, handlers) => {
      firstHandlers = handlers;
      return pending;
    }
  });
  // ``saved`` clears the visible runtime before the HTTP stream itself closes.
  WorkbenchChatRuntimes.clear("chat-race");
  WorkbenchChatRuntimes.start("chat-race", {
    message: "second", clientRequestId: "second"
  }, {
    sendMessage: (_chatId, _input, handlers) => {
      secondHandlers = handlers;
      return pending;
    }
  });
  firstHandlers.onReplyDelta("stale reply");
  firstHandlers.onError(new Error("stale driver failure"));
  const current = WorkbenchChatRuntimes.get("chat-race");
  return {
    terminalErrors,
    currentRequest: current && current.clientRequestId,
    currentText: current && current.text,
    hasFailure: !!WorkbenchChatRuntimes.getFailure("chat-race"),
    secondReady: !!secondHandlers
  };
})()
"""
    )

    assert result == {
        "terminalErrors": 0,
        "currentRequest": "second",
        "currentText": "",
        "hasFailure": False,
        "secondReady": True,
    }


def test_workbench_chat_errors_keep_i18n_metadata_and_localize_known_codes():
    root = Path(__file__).resolve().parent.parent
    source = workbench_chat_source()
    api = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "platform" / "api.jsx").read_text(
        encoding="utf-8"
    )
    i18n = workbench_i18n_source()

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
        '"workbenchChat.error.driverFailed": "The agent run stopped unexpectedly.',
        '"workbenchChat.error.driverFailed": "Agent 运行意外停止，',
    ):
        assert expected in i18n


def test_workbench_uses_the_library_as_the_only_knowledge_page():
    root = Path(__file__).resolve().parent.parent
    shell = workbench_shell_source()
    index = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "index.html").read_text(
        encoding="utf-8"
    )

    assert "workbenchServices.library().Page" in shell
    assert 'window.CyreneUI.require("knowledge").Page' not in shell
    assert "compiled/workbench-knowledge.js" not in index
    assert not (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-knowledge.jsx").exists()


def test_workbench_chat_plan_tab_uses_durable_plan_and_live_step_events():
    source = workbench_chat_source()
    live_events = frontend_module_source("features/chat/live-event-controller.jsx")
    split_pane = frontend_module_source("features/chat/split-pane.jsx")
    conversation_plan = frontend_module_source("features/plan/conversation-plan-timeline.jsx")

    assert "function wbcActivePlan(chat)" in source
    assert "var active = chat && chat.activePlan;" in source
    assert 'event.type !== "plan_progress" && event.type !== "plan"' in live_events
    assert "<ConversationPlanTimeline" in split_pane
    assert "<PlanTimeline" in conversation_plan
    assert 'body: JSON.stringify({ activePlan: nextPlan })' in conversation_plan


def test_workbench_chat_tool_trace_preserves_i18n_metadata():
    chat = workbench_chat_source()
    i18n = workbench_i18n_source()

    live_message = chat.split("function WbcLiveMessage(", 1)[1].split(
        "var WBC_DRAFT_PREFIX", 1
    )[0]
    assistant_message = chat.split("function WbcAssistantMessage(", 1)[1].split(
        "var WBC_HEARTBEAT_STALL_MS", 1
    )[0]
    segment_adapter = chat.split("function wbcRuntimeSegmentMessages(", 1)[1].split(
        "function wbcSubagentStatusText", 1
    )[0]
    assert 'liveRuntime={runtime}' in live_message
    assert "<WbcLiveAgentArtifacts files={liveRuntime.artifacts}" in assistant_message
    assert "function wbcRuntimeTimelineMessages(runtime, options)" in chat
    assert "function wbcTraceDedupeKey(trace)" in chat
    assert "activityTraceKeys.has(messageTraceKey)" in chat
    assert "runtimeActivity: activity" in chat
    assert "trace: hasLiveActivities ? []" in segment_adapter
    assert "Array.isArray(segment.progress) ? segment.progress" in segment_adapter
    assert "return { tool: entry.text, preview: entry.preview };" not in assistant_message
    assert 'wbcT(entry.detailKey, toolKey, entry.detailParams)' in chat
    assert '"update_plan_progress"].indexOf(toolName)' in chat
    assert '"toolName.retire_project_memory": "Retire project memory"' in i18n
    assert '"toolName.retire_project_memory": "停用项目记忆"' in i18n
    assert '"workbenchChat.thinkingPhrases":' in i18n
    assert "WBC_THINKING_PHRASES" not in chat
    assert "var heartbeatI18n = useWorkbenchI18n();" in chat
    assert "}, [heartbeatLang]);" in chat


def test_workbench_live_trace_keeps_each_llm_activity_independent():
    chat = workbench_chat_source()
    css = workbench_style_source()
    assert 'type === "reasoning_start" && handlers.onReasoningStart' in chat
    assert 'type === "reasoning_delta" && handlers.onReasoningDelta' in chat
    assert 'type === "reasoning_done" && handlers.onReasoningDone' in chat
    assert 'reasoning: String(activity.reasoning || "") + delta' in chat
    assert "function WbcLiveActivityCard({ activity, active, hasReplyText, live })" in chat
    activity_card = chat.split("function WbcLiveActivityCard", 1)[1].split(
        "function WbcActivityGroup", 1
    )[0]
    live_message = chat.split("function WbcLiveMessage", 1)[1].split(
        "var WBC_DRAFT_PREFIX", 1
    )[0]
    trace_card = chat.split("function WbcTraceCard", 1)[1].split(
        "function WbcAssistantMessage", 1
    )[0]
    assert "useWbcState(false)" not in activity_card
    assert "useWbcState(0)" not in activity_card
    assert "setLockedHeight" not in activity_card
    assert "lockedTraceCount" not in activity_card
    assert 'var hasReasoning = !!String(item.reasoning || "").trim();' in activity_card
    assert "function wbcPhase1ProgressDetail(entries)" in chat
    assert 'var isCodexProvider = String(item.provider || "") === "codex_oauth";' in activity_card
    assert 'var visibleReasoning = !isCodexProvider && (hasReasoning || isPhase1) ? phase1Detail : "";' in activity_card
    assert "hasExpandableDetail" not in activity_card
    assert "toggleReasoning" not in activity_card
    assert 'provider: String(event.provider || activity.provider || "")' in chat
    assert '!String(activity.reasoning || "").trim()' in chat
    assert "!msg.runtimeActivityActive" in chat
    assert "activityEntries.length === 0" in chat
    assert "wbcRuntimeSegmentMessages(runtime).concat(wbcRuntimeTimelineMessages(runtime, { showReasoningPlaceholder }))" in chat
    assert "if (wbcIsActivityMessage(msg))" in chat
    assert 'if (String(message.content || "").trim()) return false;' in chat
    assert 'if (reasoning || trace.length > 0) return true;' in chat
    assistant_message = chat.split("function WbcAssistantMessage", 1)[1].split(
        "var WBC_HEARTBEAT_STALL_MS", 1
    )[0]
    assert "var activityView = live ? null : wbcActivityMessageView(msg);" in assistant_message
    assert "activity={activityView.activity}" in assistant_message
    assert "if (!activityView.visible) return null;" in assistant_message
    history_renderer = chat.split("function wbcRenderHistoryMessage", 1)[1].split(
        "function WbcConversationTimeline", 1
    )[0]
    runtime_transcript = chat.split("function WbcRuntimeTranscript", 1)[1].split(
        "function WbcLiveMessage", 1
    )[0]
    assert "if (msg.runtimeHeartbeat) return null;" in history_renderer
    assert "if (item.runtimeHeartbeat)" in runtime_transcript
    assert "return null;" in runtime_transcript
    assert "<WbcHeartbeat startedAt=" not in chat
    assert "activity={activity}" in chat
    assert "reasoning={visibleReasoning}" in activity_card
    assert "useWbcState(false)" not in live_message
    assert "useWbcState(0)" not in live_message
    assert "trace: hasLiveActivities ? []" in chat
    assert 'summaryRunning ? <span className="wb-spinner small" aria-hidden="true" /> : null' in trace_card
    assert "isRunning ? wbcTraceActionIcon(entry) : WBC_ICONS.check" in trace_card
    assert 'isRunning ? <span className="wb-spinner small" /> : WBC_ICONS.check' not in trace_card
    assert 'var hasRunningEntries = live && entries.some(function (entry)' in trace_card
    assert 'var summaryRunning = hasRunningEntries || (activityRunning && entries.length === 0);' in trace_card
    assert 'var isRunning = live && entryStatus === "running";' in trace_card
    assert 'aria-busy={summaryRunning ? "true" : undefined}' in trace_card
    assert '.wbc-trace.live[aria-busy="true"] .wbc-trace-summary b' in css
    assert "background-clip: text;" in css
    assert "background-color: var(--wbc-trace-flow-base);" in css
    assert "background-size: 45% 100%;" in css
    assert "background-repeat: no-repeat;" in css
    assert "-webkit-text-fill-color: transparent;" in css
    assert "@keyframes wbc-trace-text-flow" in css
    assert "@keyframes wbc-trace-icon-flow" in css
    assert "wbc-trace-text-flow 2.4s linear infinite;" in css
    assert "wbc-trace-icon-flow 2.4s linear infinite;" in css
    assert "wbc-trace-text-flow 2.4s ease-in-out infinite alternate" not in css
    assert "wbc-trace-icon-flow 2.4s ease-in-out infinite alternate" not in css
    assert 'className="wbc-trace-label"' in trace_card
    assert ".wbc-trace-list li.active .wbc-trace-label" in css
    assert ".wbc-trace-list li.active .wbc-trace-mark" in css
    assert "#fff 50%" in css
    assert "from { background-position: -80% 0; }" in css
    assert "to { background-position: 180% 0; }" in css
    assert 'live={!!msg.runtimeActivity}' in chat
    assert 'live={true}' in live_message
    assert 'className="wbc-thinking-detail-text"' in trace_card
    assert "wbc-trace-reasoning-toggle" not in trace_card
    assert "wbc-trace-reasoning-toggle" not in css
    assert "wbc-trace-locked" not in css
    thread_child_css = css.split(".wbc-thread > * {", 1)[1].split("}", 1)[0]
    assert "flex-shrink: 0;" in thread_child_css
    trace_view_css = css.split(".wbc-trace-view {", 1)[1].split("}", 1)[0]
    assert "height: 100%;" not in trace_view_css
    detail_css = css.split("\n.wbc-thinking-detail {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden;" in detail_css
    detail_text_css = css.split("\n.wbc-thinking-detail-text {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in detail_text_css
    assert "margin-right: -8px;" in detail_text_css
    assert "padding-right: 8px;" in detail_text_css
    expanded_detail_text_css = css.split(
        ".wbc-trace-details .wbc-thinking-detail-text {", 1
    )[1].split("}", 1)[0]
    assert "overflow: visible;" in expanded_detail_text_css
    assert "white-space: pre-wrap;" in expanded_detail_text_css


def test_workbench_groups_three_or_more_consecutive_activity_messages():
    source = workbench_chat_source()
    grouping_source = "var WBC_ACTIVITY_GROUP_MIN_ITEMS" + source.split(
        "var WBC_ACTIVITY_GROUP_MIN_ITEMS", 1
    )[1].split("// Read-only tool names", 1)[0]
    script = f"""
eval({json.dumps(grouping_source)});
const durable = [
  {{ id: "a1", activityCard: true, createdAt: "2026-08-18T10:00:00Z", trace: [{{ kind: "tool", status: "completed" }}] }},
  {{ id: "a2", activityCard: true, createdAt: "2026-08-18T10:00:01Z", trace: [{{ kind: "tool", status: "completed" }}] }},
  {{ id: "a3", activityCard: true, createdAt: "2026-08-18T10:00:02Z", trace: [{{ kind: "tool", status: "completed" }}] }},
  {{ id: "reply", role: "assistant", content: "done", processingDurationMs: 4200 }}
];
const shortRun = wbcGroupConsecutiveActivityMessages(durable.slice(0, 2).concat(durable[3]), null);
const completed = wbcGroupConsecutiveActivityMessages(durable, null);
const live = wbcGroupConsecutiveActivityMessages([
  {{ id: "l1", runtimeActivity: {{ progress: [{{ kind: "tool", status: "running" }}] }} }},
  {{ id: "l2", runtimeActivity: {{ progress: [{{ kind: "tool", status: "completed" }}] }} }},
  {{ id: "l3", runtimeActivity: {{ reasoning: "thinking", progress: [] }} }}
], {{ startedAt: 1000, lastEventAt: 5000 }});
const historicalReasoning = wbcGroupConsecutiveActivityMessages([
  {{ id: "h1", content: "", reasoning: "historical thought", trace: [] }},
  {{ id: "h2", content: "", reasoning: "historical thought", trace: [] }},
  {{ id: "only-command", content: "", trace: [{{ kind: "tool", status: "completed" }}] }},
  {{ id: "reply-2", role: "assistant", content: "done" }}
], null);
process.stdout.write(JSON.stringify({{
  shortTypes: shortRun.map(item => !!item.activityGroup),
  completed: {{
    grouped: completed[0].activityGroup,
    ids: completed[0].activities.map(item => item.id),
    active: completed[0].active,
    durationMs: completed[0].durationMs,
    nextId: completed[1].id
  }},
  live: {{ grouped: live[0].activityGroup, active: live[0].active, durationMs: live[0].durationMs }},
  historicalReasoning: historicalReasoning.map(item => ({{
    id: item.id,
    grouped: !!item.activityGroup,
    activityIds: item.activityGroup ? item.activities.map(activity => activity.id) : []
  }}))
}}));
"""
    completed_process = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    result = json.loads(completed_process.stdout)
    assert result == {
        "shortTypes": [False, False, False],
        "completed": {
            "grouped": True,
            "ids": ["a1", "a2", "a3"],
            "active": False,
            "durationMs": 4200,
            "nextId": "reply",
        },
        "live": {"grouped": True, "active": True, "durationMs": None},
        "historicalReasoning": [
            {
                "id": "activity-group:h1",
                "grouped": True,
                "activityIds": ["h1", "h2", "only-command"],
            },
            {"id": "reply-2", "grouped": False, "activityIds": []},
        ],
    }


def test_workbench_activity_group_has_live_and_completed_disclosure_states():
    chat = workbench_chat_source()
    split = frontend_module_source("features/chat/split-pane.jsx")
    css = workbench_style_source()
    i18n = workbench_i18n_source()
    group_component = chat.split("function WbcActivityGroup", 1)[1].split(
        "var WBC_LIVE_MARKDOWN_INTERVAL_MS", 1
    )[0]
    assert "wbcGroupConsecutiveActivityMessages(messages, runtime)" in chat
    assert "displayMessages.map(function (msg)" in chat
    assert "var renderedHistory = useWbcMemo(function ()" in chat
    assert "}, [chatMessages, runtimeUserMessages, runtimeReplyRenderKey]);" in chat
    assert "runtimeHasReplyText" in chat
    assert "{renderedTimeline}" in chat
    assert "if (msg.activityGroup)" in chat
    assert "wbcGroupConsecutiveActivityMessages(messages, streamRuntime)" in split
    assert "displayMessages.map(function (message)" in split
    assert "if (message.activityGroup)" in split
    assert "<WbcActivityGroup group={message} />" in split
    assert 'className="wbc-activity-group-summary"' in group_component
    assert "aria-expanded={expanded}" in group_component
    assert 'aria-busy={active ? "true" : undefined}' in group_component
    assert "if (wasActiveRef.current && !active) setExpanded(false);" in group_component
    assert 'workbenchChat.activityGroup.completedDuration' in group_component
    trace_card = chat.split("function WbcTraceCard", 1)[1].split(
        "function WbcAssistantMessage", 1
    )[0]
    assert 'if (!entries.length && !reasoningText.trim() && !live) return null;' in trace_card
    assert 'visible: active || entries.length > 0 || hasReasoning' in chat
    assert '"workbenchChat.activityGroup.running.command": "正在运行命令"' in i18n
    assert '"workbenchChat.activityGroup.running.reply": "正在生成回复"' in i18n
    assert '"workbenchChat.activityGroup.completedDuration": "已处理（{duration}）"' in i18n
    assert ".wbc-activity-group-collapse.open" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert '.wbc-activity-group.active[aria-busy="true"] .wbc-activity-group-summary b' in css
    assert '.wbc-activity-group.active[aria-busy="true"] .wbc-activity-group-state' in css
    assert "animation: wbc-trace-text-flow 2.4s linear infinite;" in css
    assert "animation: wbc-trace-icon-flow 2.4s linear infinite;" in css
    group_list_css = css.split(".wbc-activity-group-list {", 1)[1].split("}", 1)[0]
    assert "border-left" not in group_list_css
    assert "margin: 3px 0 2px 22px;" in group_list_css


def test_codex_reasoning_effort_updates_the_primary_candidate_without_stale_state():
    root = Path(__file__).resolve().parent.parent
    settings = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "settings-model-configuration.jsx").read_text(encoding="utf-8")
    shared = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "features" / "settings" / "shared.jsx").read_text(encoding="utf-8")

    assert "reasoning_effort: String(profile.reasoning_effort || \"\").trim()" in settings
    assert "codexModelReasoningEfforts" in shared
    assert "return [candidate].concat(rest);" not in settings


def test_workbench_deepseek_reasoning_effort_matches_provider_capabilities():
    chat = workbench_chat_source()
    styles = workbench_style_source()

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
    assert 'className="wbc-model-menu-value">{modelName}</span>' in chat
    assert ".wbc-model-menu-model-name" not in styles


def test_workbench_chat_context_and_browser_trace_have_dynamic_i18n_labels():
    chat = workbench_chat_source()
    i18n = workbench_i18n_source()

    # Dynamic context block and tool IDs must resolve through the same
    # translation table as the surrounding labels instead of leaking raw IDs.
    assert 'var key = "workbenchChat.ctxBlock." + id;' in chat
    assert "if (label && label !== key) return label;" in chat
    assert "if (isToolEntry) return wbcLocalizedToolName(toolKey);" in chat
    assert "function wbcLocalizedToolName(toolName)" in chat
    assert '"workbenchChat.ctxBlock.system.root": "Agent instructions"' in i18n
    assert '"workbenchChat.ctxBlock.context.plugin_session": "插件会话上下文"' in i18n
    assert '"workbenchChat.ctxBlock.context.persona": "人格"' in i18n
    assert '"workbenchChat.ctxBlock.context.memory": "记忆"' in i18n
    assert '"workbenchChat.ctxBlock.context.learned_skills": "已学习技能"' in i18n
    assert '"toolName.browser_user_events": "User browser operations"' in i18n
    assert '"toolName.browser_user_events": "用户浏览器操作"' in i18n
    assert '"toolName.browser_upload_files": "Upload files"' in i18n
    assert '"toolName.browser_upload_files": "上传文件"' in i18n


def test_tool_i18n_fallbacks_do_not_leak_internal_keys_after_classic_removal():
    result = _run_workbench_trace_i18n_js(
        """
({
  unknownTool: window.WorkbenchI18n.t("toolName.custom_mcp_tool", "custom_mcp_tool"),
  unknownParam: window.WorkbenchI18n.t("memory.learning.toolParam.custom_arg", "custom_arg"),
  planProgress: window.WorkbenchI18n.toolName("update_plan_progress", "zh"),
  browserSubmit: window.WorkbenchI18n.toolName("browser.user.submit", "zh"),
  browserNavigateEn: window.WorkbenchI18n.toolName("browser.navigate", "en"),
  askUserZh: window.WorkbenchI18n.toolName("Ask User", "zh"),
  askUserEn: window.WorkbenchI18n.toolName("Ask User", "en"),
  globZh: window.WorkbenchI18n.toolName("Glob", "zh"),
  browserToolsZh: window.WorkbenchI18n.toolName("browser_tools", "zh"),
  skillToolsZh: window.WorkbenchI18n.toolName("skill_tools", "zh"),
  codeToolsEn: window.WorkbenchI18n.toolName("code_tools", "en"),
  cyreneToolsZh: window.WorkbenchI18n.toolName("cyrene_tools", "zh"),
  appSnapshot: window.WorkbenchI18n.toolName("AppUISnapshot", "zh"),
  powerPointShapesZh: window.WorkbenchI18n.toolName("PowerPointListShapes", "zh"),
  powerPointShapesEn: window.WorkbenchI18n.toolName("PowerPointListShapes", "en"),
  powerPointCapabilityZh: window.WorkbenchI18n.toolName("ppt.list_shapes", "zh"),
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
        "askUserZh": "询问用户",
        "askUserEn": "Ask user",
        "globZh": "查找文件",
        "browserToolsZh": "浏览器工具",
        "skillToolsZh": "技能工具",
        "codeToolsEn": "Code tools",
        "cyreneToolsZh": "Cyrene 应用工具",
        "appSnapshot": "快照应用界面",
        "powerPointShapesZh": "列出页面元素",
        "powerPointShapesEn": "List slide elements",
        "powerPointCapabilityZh": "列出页面元素",
        "showSidebar": "显示侧边栏",
        "hideSidebar": "隐藏侧边栏",
        "download": "下载",
    }

    root = Path(__file__).resolve().parent.parent
    classic_root = root / "src" / "cyrene" / "workbench" / "webui" / "static" / "app"
    assert not (classic_root / "chat.jsx").exists()
    assert not (classic_root / "chat-surface.jsx").exists()
    assert not (classic_root / "evolution.jsx").exists()


def test_profile_top_tools_use_shared_tool_name_i18n():
    root = Path(__file__).resolve().parent.parent
    profile = (root / "src/cyrene/workbench/webui/frontend/workbench-profile.jsx").read_text(
        encoding="utf-8"
    )
    i18n = workbench_i18n_source()

    assert 'workbenchServices.i18n().toolName(tool, lang)' in profile
    assert '"Ask User": "ask_user"' in i18n
    assert i18n.count('"toolName.ask_user"') == 2
    assert i18n.count('"toolName.Glob"') == 2


def test_settings_storage_keeps_last_snapshot_across_tab_remounts():
    overlay = workbench_settings_source()
    i18n = workbench_i18n_source()
    storage = overlay.split("var DATA_PANEL_STORAGE_TTL_MS", 1)[1].split(
        "function resetData()", 1
    )[0]

    assert 'var DATA_PANEL_STORAGE_CACHE_KEY = "cyrene.settings.storageSnapshot.v1";' in storage
    assert "function isDataPanelStorageSnapshot(payload)" in storage
    assert "function readDataPanelStorageCache()" in storage
    assert "localStorage.getItem(DATA_PANEL_STORAGE_CACHE_KEY)" in storage
    assert "localStorage.setItem(DATA_PANEL_STORAGE_CACHE_KEY" in storage
    assert "var persistedDataPanelStorage = readDataPanelStorageCache();" in storage
    assert "persistedDataPanelStorage ? persistedDataPanelStorage.payload : null" in storage
    assert "var dataPanelStorageRequest = null;" in storage
    assert "if (cacheIsFresh) return Promise.resolve(dataPanelStorageCache);" in storage
    assert "if (dataPanelStorageRequest) return dataPanelStorageRequest;" in storage
    assert "dataPanelStorageCache = payload;" in storage
    assert "persistDataPanelStorageCache(payload, dataPanelStorageCachedAt);" in storage
    assert "useStateSt(dataPanelStorageCache)" in storage
    assert "if (mounted && !dataPanelStorageCache) setStorageError" in storage
    assert "return function () { mounted = false; };" in storage
    assert 'settingsFetch("/api/settings/storage")' in storage
    assert 'extensions: "settings.storageExtensions"' in overlay
    assert 'media: "settings.storageMedia"' in overlay
    assert 'extensions: "#7c3aed"' in overlay
    assert 'media: "#fb7185"' in overlay
    assert i18n.count('"settings.storageExtensions"') == 2
    assert i18n.count('"settings.storageMedia"') == 2

    settings_page = overlay.split("function SettingsPage(", 1)[1].split(
        "// ── Remote Control Panel", 1
    )[0]
    assert "requestDataPanelStorage().catch(function () {});" in settings_page


def test_session_export_uses_balanced_responsive_action_layout():
    overlay = workbench_settings_source()
    styles = workbench_style_source()
    i18n = workbench_i18n_source()
    session_export = overlay.split("// Session export", 1)[1].split(
        "// ── About Panel", 1
    )[0]

    assert 'className: "wb-export-footer"' in session_export
    assert 'className: "wb-export-format"' in session_export
    assert 'role: "radiogroup"' in session_export
    assert '"aria-checked": exportFmt === "markdown"' in session_export
    assert 'className: "wb-section-block wb-session-export-block"' in session_export
    assert "var sessionDate = s.updatedAt || s.createdAt;" in session_export
    assert ".settings-overlay .wb-export-session-option {\n  min-height: 44px;" in styles
    assert "@media (max-width: 700px)" in styles
    assert i18n.count('"settings.sessionExportFormat"') == 2


def test_session_export_button_has_distinct_enabled_and_disabled_states():
    overlay = workbench_settings_source()
    styles = workbench_style_source()
    session_export = overlay.split("// Session export", 1)[1].split(
        "// ── About Panel", 1
    )[0]

    assert "function SessionExportIcon()" in overlay
    assert "React.createElement(SessionExportIcon)" in session_export
    assert 'className: "wb-btn primary wb-export-submit"' in session_export
    assert ".wb-export-submit {\n  min-width: 104px;\n  min-height: 44px;" in styles
    assert ".wb-btn.primary.wb-export-submit:not(:disabled)" in styles
    assert ".wb-btn.primary.wb-export-submit:disabled" in styles
    disabled = styles.split(
        ".wb-btn.primary.wb-export-submit:disabled {", 1
    )[1].split("}", 1)[0]
    assert "opacity: 1;" in disabled
    assert "background: color-mix(in srgb, var(--wb-control-bg)" in disabled
    assert "color: var(--wb-faint);" in disabled
    assert "@media (prefers-reduced-motion: reduce)" in styles


def test_workbench_tool_trace_preview_localizes_protocol_values_only():
    result = _run_workbench_trace_i18n_js(
        """
[
  wbcToolPreviewText("discover"),
  wbcToolPreviewText("invoke, memory.project.search"),
  wbcToolPreviewText("list_targets"),
  wbcToolPreviewText("call, visual_describe"),
  wbcToolPreviewText("待办 任务 task pending")
]
"""
    )

    assert result == [
        "发现能力",
        "调用能力, 搜索项目记忆",
        "发现应用",
        "执行应用操作, 查看应用截图",
        "待办 任务 task pending",
    ]


def test_workbench_tool_names_localize_toolbox_operations_and_delegated_tools():
    result = _run_workbench_trace_i18n_js(
        """
[
  window.WorkbenchI18n.toolName("toolbox.list", "zh"),
  window.WorkbenchI18n.toolName("toolbox.describe", "zh"),
  window.WorkbenchI18n.toolName("toolbox.browser_snapshot", "zh")
]
"""
    )

    assert result == ["查看可用工具", "查看工具说明", "检查页面"]


def test_workbench_live_tool_events_expose_the_deferred_operation_name():
    source = frontend_module_source("features/chat/agent-events.jsx")
    helper = "function wbcAgentToolDisplayName(" + source.split(
        "function wbcAgentToolDisplayName(", 1
    )[1].split("function wbcAgentToolPayload", 1)[0]
    script = f"""
eval({json.dumps(helper)});
process.stdout.write(JSON.stringify([
  wbcAgentToolDisplayName("toolbox", {{ operation: "list" }}),
  wbcAgentToolDisplayName("toolbox", {{ operation: "describe", name: "Read" }}),
  wbcAgentToolDisplayName("toolbox", {{ operation: "invoke", name: "browser_snapshot" }}),
  wbcAgentToolDisplayName("Read", {{ path: "README.md" }})
]));
"""
    completed = subprocess.run(
        ["node", "-"], input=script, check=True, capture_output=True, text=True
    )

    assert json.loads(completed.stdout) == [
        "toolbox.list",
        "toolbox.describe",
        "browser_snapshot",
        "Read",
    ]


def test_workbench_tool_validation_preview_is_fully_localized():
    result = _run_workbench_trace_i18n_js(
        """
wbcToolPreviewText(
  "插件参数无效： Invalid arguments for Plugin 'toolbox' at arguments.operation: "
  + "'browser_snapshot' is not one of ['list', 'describe', 'invoke']"
)
"""
    )

    assert result == (
        "参数 operation 无效：“检查页面” 不是有效选项，"
        "可选值：“列出可用工具”、“查看能力”、“调用能力”"
    )


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
        "preview": "invoke, browser.click_text, text: 继续, exact: true",
        "localized": "调用能力, 点击文本, text: 继续, exact: true",
        "leakedObjectTag": False,
    }


def test_workbench_tool_trace_preview_removes_python_object_braces():
    result = _run_workbench_trace_i18n_js(
        """
wbcToolPreviewText("invoke, skill_tools, {'name': '播放首页推荐首个视频', 'auto': True}")
"""
    )

    assert result == "调用能力, 技能工具, name: 播放首页推荐首个视频, auto: True"


def test_workbench_chat_last_user_message_has_retry_action():
    source = workbench_chat_source()
    i18n = workbench_i18n_source()

    projection = source.split("function useWbcConversationProjection(", 1)[1].split(
        "function wbcRenderHistoryMessage(", 1
    )[0]
    history = source.split("function wbcRenderHistoryMessage(", 1)[1].split(
        "function useWbcComposerReserveHeight(", 1
    )[0]
    user_message = source.split("function WbcUserMessage(", 1)[1].split(
        "function WbcAgentFiles(", 1
    )[0]

    assert 'var user = "";' in projection
    assert 'lastUserId: lastMessageIds.user' in projection
    assert 'String(msg.id || "") === context.lastUserId' in history
    assert "onRetryMessage={canRetryUser && context.onRetryMessage ? context.retryHistoryMessage : null}" in history
    assert "function WbcUserMessage({ msg, onOpenFile, onEditMessage, canEdit, onRetryMessage })" in source
    assert "onClick={function () { onRetryMessage(msg.id); }}" in user_message
    assert "WBC_ICONS.retry" in user_message
    assert 'wbcT("workbenchChat.retryUserMessage", "Retry message")' in user_message
    assert '"workbenchChat.retryUserMessage": "重试消息"' in i18n


def test_workbench_chat_uses_explicit_run_reconnect_without_resubmitting_message():
    source = workbench_chat_source()

    assert 'function reconnectRun(chatId, handlers, signal, cursor)' in source
    assert '"/run-stream"' in source
    assert '"?cursor=" + encodeURIComponent(eventCursor)' in source
    assert 'function reconnect(chatId, model, preserveRuntime)' in source
    assert 'runtimeEngine.reconnect(activeChat.id, model)' in source
    assert 'activeChat.status === "running"' in source


def test_workbench_chat_reconnect_keeps_live_timeline_and_resumes_from_cursor():
    source = workbench_chat_source()
    stream_owner = source.split(
        "function ownStream(chatId, generation, streamPromise, ac, model) {", 1
    )[1].split("// Begin a streamed send", 1)[0]

    assert "scheduleReconnect(chatId, model);" in stream_owner
    assert "update(chatId, null);" not in stream_owner
    assert "onEventCursor: function (cursor)" in source

    result = _run_workbench_runtime_js(
        """
(() => {
  let handlers = null;
  let reconnectCursor = -1;
  const pending = new Promise(() => {});
  const model = {
    sendMessage: (_chatId, _input, nextHandlers) => {
      handlers = nextHandlers;
      return pending;
    },
    reconnectRun: (_chatId, _handlers, _signal, cursor) => {
      reconnectCursor = cursor;
      return pending;
    }
  };
  WorkbenchChatRuntimes.start("chat-1", { message: "hello" }, model);
  handlers.onEventCursor(41);
  handlers.onReasoningDone("reasoning that must stay");
  handlers.onToolStarted({ toolCallId: "tool-1", name: "Read", status: "running" });
  const before = WorkbenchChatRuntimes.get("chat-1");
  WorkbenchChatRuntimes.reconnect("chat-1", model, true);
  const after = WorkbenchChatRuntimes.get("chat-1");
  const result = {
    reconnectCursor,
    eventCursor: after.eventCursor,
    reasoning: after.activities[0].reasoning,
    toolCallId: after.activities[1].progress[0].toolCallId,
    activityCountBefore: before.activities.length,
    activityCountAfter: after.activities.length,
    reconnecting: after.reconnecting
  };
  WorkbenchChatRuntimes.clear("chat-1");
  return result;
})()
"""
    )

    assert result == {
        "reconnectCursor": 41,
        "eventCursor": 41,
        "reasoning": "reasoning that must stay",
        "toolCallId": "tool-1",
        "activityCountBefore": 2,
        "activityCountAfter": 2,
        "reconnecting": True,
    }


def test_workbench_copy_uses_electron_clipboard_bridge():
    root = Path(__file__).resolve().parent.parent
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    plugins = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend"
        / "platform" / "plugins.jsx"
    ).read_text(encoding="utf-8")
    chat = workbench_chat_source()

    assert "const { contextBridge, ipcRenderer }" in preload
    assert "writeClipboardText: (text) =>" in preload
    assert "'clipboard:write-text'" in preload
    assert "ipcRenderer.invoke('clipboard:read-text')" in preload
    assert "ipcMain.handle('clipboard:write-text'" in main
    assert "ipcMain.handle('clipboard:read-text'" in main
    assert "Promise.resolve(hostResult)" in plugins
    assert "clipboard.readText()" not in preload
    assert 'typeof window.cyrene.writeClipboardText === "function"' in chat
    assert "window.cyrene.writeClipboardText(text);" in chat
    assert "await navigator.clipboard.writeText(text);" in chat
    assert 'console.error("Failed to copy workbench message:", e);' in chat


def test_workbench_shell_sets_a_restrictive_electron_csp():
    root = Path(__file__).resolve().parent.parent
    shell = (
        root / "src" / "cyrene" / "workbench" / "http" / "system" / "shell.py"
    ).read_text(encoding="utf-8")

    assert '"Content-Security-Policy": _WORKBENCH_CSP' in shell
    assert "object-src 'none'" in shell
    assert "frame-ancestors 'none'" in shell
    assert "worker-src 'self' blob:" in shell
    assert "unsafe-eval" not in shell


def test_code_blocks_use_declared_language_and_resilient_clipboard_actions():
    root = Path(__file__).resolve().parent.parent
    highlight = (
        root
        / "src" / "cyrene" / "workbench" / "webui"
        / "frontend"
        / "shared"
        / "markdown"
        / "highlight.jsx"
    ).read_text(encoding="utf-8")
    actions = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "actions.jsx"
    ).read_text(encoding="utf-8")
    styles = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "highlight.css"
    ).read_text(encoding="utf-8")

    assert 'language = "text";' in highlight
    assert "hljs.highlightAuto(code)" not in highlight
    assert 'typeof window.cyrene.writeClipboardText === "function"' in actions
    assert 'navigator.clipboard && typeof navigator.clipboard.writeText === "function"' in actions
    assert 'document.execCommand("copy")' in actions
    assert 'translate("workbenchChat.codeBlock.copy", "Copy")' in actions
    assert 'translate("workbenchChat.codeBlock.edit", "Edit")' in actions
    assert 'window.addEventListener("cyrene:i18n-changed", localizeActions)' in actions
    assert 'if (!codeElement || codeElement.tagName !== "CODE") return;' in actions
    assert "padding-top: 52px;" in styles
    assert "top: 0;" in styles
    assert "bottom: 0;" not in styles.split(".code-block-actions", 1)[1].split("}", 1)[0]


def test_tool_error_previews_do_not_render_as_code_blocks_or_show_a_left_rule():
    root = Path(__file__).resolve().parent.parent
    messages = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "features" / "chat" / "messages.jsx"
    ).read_text(encoding="utf-8")
    styles = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "features" / "chat" / "conversation.css"
    ).read_text(encoding="utf-8")
    error_rule = styles.split(".wbc-tool-presentation-body.error", 1)[1].split("}", 1)[0]

    assert '<pre className={"wbc-tool-presentation-body " + presentationKind}' in messages
    assert "border-inline-start" not in error_rule


def test_workbench_side_viewer_keeps_html_sandboxed_and_uses_pdfjs_text_layer():
    root = Path(__file__).resolve().parent.parent
    source = workbench_chat_source()
    styles = workbench_style_source()
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")

    assert 'split(";", 1)[0].trim().toLowerCase()' in source
    assert 'ext === "ppt"' not in source
    assert 'ext === "doc"' not in source
    assert 'wbcFileViewKind(file) !== "html"' in source
    assert 'function wbcHtmlPreviewDocument(source, sourceUrl)' in source
    assert '<base href="' in source
    assert 'data-cyrene-html-preview-bootstrap="storage"' in source
    assert 'installMemoryFallback("localStorage")' in source
    assert 'installMemoryFallback("sessionStorage")' in source
    assert 'sandbox="allow-scripts"' in source
    assert 'srcDoc={htmlPreview}' in source
    assert 'pdf.installCopyFix(container, viewer)' in source
    assert 'pdf.installSelectionSanitizer(container, viewer, eventBus)' in source
    assert 'selectionSanitizer.abort();' in source
    assert '"/api/workbench/library/read?workspace="' in source
    assert '<WbcViewerList files={viewerItems} selectedFile={chatViewerFile} onSelect={onSelectViewer} />' in source
    assert 'selectResourceSplit("viewer", wbcArtifactFileKey(file))' in source
    assert 'onLoad={confirmViewed}' in source
    assert 'onError={function () { setFailed(true); }}' in source
    assert 'function handleMarkdownLink(event)' in source
    assert 'href.charAt(0) !== "#"' in source
    assert 'target.scrollIntoView({ behavior: "smooth", block: "start" });' in source
    assert 'onClick={handleMarkdownLink}' in source
    assert 'return <WbcPdfJsViewer file={file} url={url} onViewed={confirmViewed} />;' in source
    assert "viewer.currentScaleValue = 'page-width';" in source
    assert 'fitScaleRef.current = viewer.currentScale;' in source
    assert 'applyPdfGestureScale(v.currentScale / 1.15);' in source
    assert 'Math.max(fitScaleRef.current, Math.min(5, nextScale))' in source
    assert 'eventBus.on(\'pagesinit\', onPagesInit);' in source
    assert 'new ResizeObserver(onContainerResize)' in source
    assert 'Math.abs(measuredWidth - lastFitWidth) < 1' in source
    assert 'function onSplitResizeEnd() { fitViewerWidth(true); }' in source
    assert '.wbc-viewer .pdfViewer .textLayer' not in styles
    viewer_pre_rule = styles.split('.wbc-viewer-pre {', 1)[1].split('}', 1)[0]
    assert 'color: #c9d1d9;' in viewer_pre_rule
    assert 'color-scheme: dark;' in viewer_pre_rule
    assert 'background: #0d1117;' in viewer_pre_rule
    viewer_code_rule = styles.split('.wbc-viewer-pre code.hljs {', 1)[1].split('}', 1)[0]
    assert 'color: #c9d1d9;' in viewer_code_rule
    assert 'background: transparent;' in viewer_code_rule
    assert '.wbc-viewer-pre .hljs-keyword,' in styles
    assert '.wbc-viewer-pre .hljs-title.function_ { color: #d2a8ff; }' in styles
    assert '.wbc-viewer-pre .hljs-string { color: #a5d6ff; }' in styles
    assert '.wbc-viewer-pre .hljs-comment,' in styles
    assert '.wbc-viewer-pre .hljs-built_in,' in styles
    assert "width: 100%;" in styles
    assert "height: 100%;" in styles
    viewer_image_rule = styles.split('.wbc-viewer-img {', 1)[1].split('}', 1)[0]
    assert 'object-fit: contain;' in viewer_image_rule
    assert '.wbc-artifact-split .wbc-viewer-head:has(.wbc-viewer-zoom)' in styles
    assert 'applyOfficeZoom(zoomRef.current + 25)' in source
    assert 'applyImageZoom(imageZoomRef.current + 25)' in source
    assert 'Math.max(100, Math.min(400, Number(nextZoom) || 100))' in source
    assert 'style={{ width: imageZoom + "%", height: imageZoom + "%" }}' in source
    assert 'function handleImageWheel(event)' in source
    assert 'function handleOfficeWheel(event)' in source
    assert 'function handlePdfWheel(event)' in source
    assert 'function wbcZoomAnchorRestorer(container, oldScale, clientX, clientY)' in source
    assert 'rect.width / 2' in source
    assert 'contentX * safeNewScale - anchorX' in source
    assert 'event.clientX, event.clientY' in source
    assert '(event.touches[0].clientX + event.touches[1].clientX) / 2' in source
    assert 'addEventListener("wheel", handleImageWheel, { passive: false })' in source
    assert 'addEventListener("wheel", handleOfficeWheel, { passive: false })' in source
    assert 'addEventListener("wheel", handlePdfWheel, { passive: false })' in source
    assert 'addEventListener("touchmove", handleImageTouchMove, { passive: false })' in source
    assert 'addEventListener("touchmove", handleOfficeTouchMove, { passive: false })' in source
    assert 'addEventListener("touchmove", handlePdfTouchMove, { passive: false })' in source
    pptx_surface_rule = styles.split(
        '.wbc-office-viewer.is-pptx .wbc-office-render-surface {', 1
    )[1].split('}', 1)[0]
    assert 'overflow-x: hidden;' in pptx_surface_rule
    assert 'background: #d9dde4;' in pptx_surface_rule
    assert 'color-scheme: light;' in pptx_surface_rule
    assert r"/\.html?$/i.test(target.pathname)" in main


def test_html_preview_storage_fallback_runs_before_game_scripts_without_weakening_sandbox():
    source = workbench_chat_source()
    helper_source = "var WBC_HTML_PREVIEW_STORAGE_BOOTSTRAP" + source.split(
        "var WBC_HTML_PREVIEW_STORAGE_BOOTSTRAP", 1
    )[1].split("// Map tools mark", 1)[0]
    script = f"""
global.window = {{ location: {{ href: "http://127.0.0.1:3000/" }} }};
Object.defineProperty(window, "localStorage", {{
  configurable: true,
  get: function () {{ throw new Error("opaque origin"); }}
}});
Object.defineProperty(window, "sessionStorage", {{
  configurable: true,
  get: function () {{ throw new Error("opaque origin"); }}
}});
eval({json.dumps(helper_source)});
const sourceHtml = '<!doctype html><html><head><title>Game</title></head><body><script>window.gameScore = localStorage.getItem("score");<\\/script></body></html>';
const preview = wbcHtmlPreviewDocument(sourceHtml, "/api/files/snake.html");
const bootstrapStart = preview.indexOf('<script data-cyrene-html-preview-bootstrap="storage">');
const bootstrapBodyStart = preview.indexOf('>', bootstrapStart) + 1;
const bootstrapEnd = preview.indexOf('</script>', bootstrapBodyStart);
eval(preview.slice(bootstrapBodyStart, bootstrapEnd));
window.localStorage.setItem("score", 7);
window.sessionStorage.setItem("paused", false);
process.stdout.write(JSON.stringify({{
  bootstrapBeforeGame: bootstrapStart < preview.indexOf("window.gameScore"),
  localValue: window.localStorage.getItem("score"),
  localLength: window.localStorage.length,
  missing: window.localStorage.getItem("missing"),
  firstKey: window.localStorage.key(0),
  sessionValue: window.sessionStorage.getItem("paused"),
  storagesAreIsolated: window.localStorage.getItem("paused") === null,
  hasBase: preview.includes('<base href="http://127.0.0.1:3000/api/files/snake.html">')
}}));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result == {
        "bootstrapBeforeGame": True,
        "localValue": "7",
        "localLength": 1,
        "missing": None,
        "firstKey": "score",
        "sessionValue": "false",
        "storagesAreIsolated": True,
        "hasBase": True,
    }


def test_workbench_collapsed_rail_keeps_labels_horizontal_during_expansion():
    root = Path(__file__).resolve().parent.parent
    styles = workbench_style_source()
    index = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

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
    assert "workbench.css?v=0.9.0-beta4" in index


def test_workbench_collapsed_rail_icons_stay_left_anchored_while_closing():
    styles = workbench_style_source()

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


def test_workbench_wechat_channel_uses_qr_login_instead_of_token_input():
    root = Path(__file__).resolve().parent.parent
    settings = workbench_settings_source()
    translations = workbench_i18n_source()
    styles = workbench_style_source()
    index = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "function WeChatConnectionPanel" in settings
    assert 'settingsFetch("/api/wechat/status")' in settings
    assert 'settingsFetch("/api/wechat/qr-login"' in settings
    assert 'settingsFetch("/api/wechat/poll-login"' in settings
    assert 'settingsFetch("/api/wechat/start"' in settings
    assert 'settingsFetch("/api/wechat/stop"' in settings
    assert "result.qrcode_image || result.qrcode_img" in settings
    assert "WECHAT_BOT_TOKEN" not in settings
    assert '"settings.wechatScanConnect": "扫描二维码连接"' in translations
    assert ".wb-wechat-qr-overlay" in styles
    assert '<script type="module" src="compiled/app.js?v=0.9.0-beta4"></script>' in index


def test_desktop_uses_cross_platform_native_directory_picker():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    create = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-create.jsx").read_text(encoding="utf-8")
    chat = workbench_chat_source()

    assert "const isLinux = process.platform === 'linux';" in main
    assert "const useInsetTitleBar = isMac;" in main
    assert "ipcMain.handle('dialog:pick-directory'" in main
    assert "properties: ['openDirectory', 'createDirectory']" in main
    picker_handler = main.split("ipcMain.handle('dialog:pick-directory'", 1)[1].split(
        "ipcMain.handle('dialog:pick-extension-path'", 1
    )[0]
    assert "isLinux" not in picker_handler
    assert "directoryPickerUnsupported" not in main
    assert "process.platform !== 'linux'" not in preload
    assert "ipcRenderer.invoke('dialog:pick-directory')" in preload
    assert 'window.cyrene && typeof window.cyrene.pickDirectory === "function"' in create
    assert "await window.cyrene.pickDirectory()" in create
    assert 'fetch("/api/context/pick-directory", { method: "POST" })' in create
    assert 'window.cyrene.platform === "linux"' not in chat
    assert "window.cyrene.pickDirectory().then(function (data)" in chat
    assert 'toastError(err, wbcT("workbenchChat.pickDirFailed"' in chat


def test_topbar_theme_toggle_persists_to_the_appearance_namespace():
    root = Path(__file__).resolve().parent.parent
    bootstrap = (root / "src/cyrene/workbench/webui/frontend/entry/bootstrap.jsx").read_text(encoding="utf-8")

    assert "function persistWorkbenchTheme(mode)" in bootstrap
    assert 'fetch("/api/settings/namespaces/appearance"' in bootstrap
    assert "var changes = { theme: mode };" in bootstrap
    assert "if (!values.appearance_migrated)" in bootstrap
    assert "changes.appearance_migrated = true;" in bootstrap
    assert "expected_revision: payload.revision" in bootstrap
    assert "if (response.status === 409 && !retry)" in bootstrap
    toggle = bootstrap.split("function toggleWorkbenchTheme()", 1)[1].split("var WorkbenchApp", 1)[0]
    assert "writeWorkbenchThemeLocal(next);" in toggle
    assert "persistWorkbenchTheme(next);" in toggle


def test_split_opening_keeps_elastic_motion_off_layout_sizing():
    styles = workbench_style_source()

    pane_layout = styles.split("\n.wbc-pane-layout {", 1)[1].split("}", 1)[0]
    pane_single = styles.split(".wbc-pane-layout.single {", 1)[1].split("}", 1)[0]
    right_card = styles.split(
        ".wbc-pane-layout.split > .wbc-pane-column.right > .wbc-pane-card {", 1
    )[1].split("}", 1)[0]
    right_keyframes = styles.split("@keyframes wbc-pane-card-settle-from-right {", 1)[1].split("}", 3)[0:3]

    assert "grid-template-columns 380ms cubic-bezier(.22, 1, .36, 1)" in pane_layout
    assert "grid-template-columns: minmax(0, 1fr) 0px 0px" in pane_single
    assert "wbc-pane-card-settle-from-right 400ms cubic-bezier(.22, 1.16, .36, 1)" in right_card
    assert all("scale(" not in block for block in right_keyframes)


def test_backup_actions_use_native_file_pickers_and_comfortable_density_only(
    monkeypatch,
    tmp_path,
):
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    settings = workbench_settings_source()
    data_panel = frontend_module_source("features/settings/data.jsx")
    bootstrap = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "entry" / "bootstrap.jsx").read_text(encoding="utf-8")
    index = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "ipcMain.handle('dialog:pick-backup-save-path'" in main
    assert "ipcMain.handle('dialog:pick-backup-file'" in main
    assert "dialog.showSaveDialog" in main
    assert "extensions: ['zip']" in main
    assert "ipcRenderer.invoke('dialog:pick-backup-save-path'" in preload
    assert "ipcRenderer.invoke('dialog:pick-backup-file'" in preload
    assert "await bridge.pickBackupSavePath" in data_panel
    assert "await bridge.pickBackupFile" in data_panel
    assert 'settingsFetch("/api/backup/export"' in data_panel
    assert 'settingsFetch("/api/backup/restore"' in data_panel
    assert 'JSON.stringify({ path: selection.path })' in data_panel
    assert 't("settings.backupRestoreBtn")' in data_panel
    assert 't("settings.backupHint")' in data_panel
    assert 'var [exportSids, setExportSids] = useStateSt([])' in settings
    assert 'settingsFetch("/api/workbench/chats")' not in settings
    assert "(dataState.sessions || []).filter" in data_panel
    assert "exportSessions.map(function (s)" in data_panel
    assert 'className: "wb-export-session-list"' in data_panel
    assert 'type: "checkbox", checked: selected' in data_panel
    assert 't("settings.sessionExportHint")' in data_panel
    assert 'exportSids.forEach(function (sessionId)' in data_panel
    assert '"/api/workbench/sessions/" + encodeURIComponent(sessionId) + "/export?format="' in data_panel
    assert '"/api/workbench/sessions/" + encodeURIComponent(session.id) + "/clear"' in data_panel

    from cyrene.platform import backup as backup_runtime
    from cyrene.workbench.persistence import store as workbench_store
    from cyrene.workbench.sessions.session_presentation import WorkbenchSessionPresentation

    captured = {}

    async def export_backup(*, include_db=True, target_path=None):
        captured.update(include_db=include_db, target_path=target_path)
        return {"ok": True, "path": str(target_path)}

    monkeypatch.setattr(backup_runtime, "export_backup", export_backup)
    selected_backup = tmp_path / "selected-backup.zip"
    backup_result = asyncio.run(
        backup_runtime.BackupRepository(tmp_path).export(str(selected_backup))
    )
    assert backup_result == {"ok": True, "path": str(selected_backup)}
    assert captured == {"include_db": True, "target_path": str(selected_backup)}

    chat = {
        "id": "chat_1",
        "title": "Exported chat",
        "createdAt": "2026-08-23T01:00:00Z",
        "updatedAt": "2026-08-23T01:01:00Z",
        "messages": [{"role": "user", "content": "hello"}],
    }
    session_db = tmp_path / "sessions.sqlite3"
    workbench_store.write_chat_bundle(
        session_db,
        {"chats": [chat]},
        lambda: {"chats": []},
    )
    exported = WorkbenchSessionPresentation(session_db).export("chat_1", "json")
    exported_payload = json.loads(exported.content)
    assert exported.media_type == "application/json; charset=utf-8"
    assert exported_payload["chat"]["id"] == "chat_1"
    assert exported_payload["chat"]["title"] == "Exported chat"
    assert exported_payload["chat"]["messages"] == [
        {"role": "user", "content": "hello"}
    ]

    assert 'document.documentElement.dataset.density = "cozy"' in settings
    assert 'localStorage.removeItem("cyrene-tweak-density")' in settings
    assert 'document.documentElement.dataset.density = "cozy"' in bootstrap
    assert 'document.documentElement.dataset.density = "cozy"' in index
    assert 'FieldRow(t("settings.density")' not in settings


def test_electron_browser_panel_uses_native_browser_bridge():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    view = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")

    assert "WebContentsView" in main
    assert "class BrowserTabManager" in main
    assert "CYRENE_ELECTRON_RPC_PORT" in main
    assert "ipcMain.handle('browser:set-bounds'" in main
    assert "setAudioMuted" in main
    assert "isCurrentlyAudible" in main
    browser_pack = (
        root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_browser" / "__init__.py"
    ).read_text(encoding="utf-8")
    skills_pack = (
        root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_skills" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "browser_tab_new" in browser_pack
    assert "browser: {" in preload
    assert "ipcRenderer.invoke('browser:navigate'" in preload
    assert "ipcRenderer.invoke('browser:set-context'" in preload
    assert "window.cyrene && window.cyrene.browser" in view
    assert "ElectronBrowserViewportPanel" in view
    assert "bridge.setBounds" in view
    assert "bridge.setContext" in view
    assert "bridge.setMuted" in view
    assert "browser_user_events" not in browser_pack
    assert "browser_user_events" in skills_pack


def test_native_browser_yields_to_model_confirm_and_topbar_overlays():
    root = Path(__file__).resolve().parent.parent
    workbench = workbench_shell_source()
    chat = workbench_chat_source()
    feedback = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "feedback" / "service.jsx"
    ).read_text(encoding="utf-8")

    assert 'window.CyreneUI.register("browser-overlays"' in workbench
    assert "if (!sessionMenu && !resourceMenu)" in workbench
    assert "if (!overflowMenu && !hoverPreview) return undefined;" in workbench
    assert "if (!modelOpen) return undefined;" in chat
    assert "workbenchServices.browserOverlays()" in chat
    assert 'platform.require("browser-overlays")' in feedback
    assert "overlays.adjust(1);" in feedback
    assert "return function () { overlays.adjust(-1); };" in feedback


def test_electron_browser_type_uses_react_compatible_native_setter():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    browser_input = (root / "electron" / "browser-input.js").read_text(encoding="utf-8")
    package = (root / "electron" / "package.json").read_text(encoding="utf-8")
    playwright_browser = (
        root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_browser" / "runtime.py"
    ).read_text(encoding="utf-8")

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
    browser = (
        root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_browser" / "runtime.py"
    ).read_text(encoding="utf-8")
    chat_routes = workbench_chat_route_source()
    view = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")
    chat = workbench_chat_source()

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
    assert 'application_plugin_service("browser")' in chat_routes
    assert "if browser_service is not None:" in chat_routes
    assert "await browser_service.close_session(removed_chat_id)" in chat_routes


def test_browser_snapshot_filters_non_interactable_page_nodes():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    browser = (
        root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_browser" / "runtime.py"
    ).read_text(encoding="utf-8")

    for source in (main, browser):
        assert "el.closest('[hidden],[inert],[aria-hidden=\"true\"]')" in source
        assert "el.checkVisibility" in source
        assert "style.contentVisibility === 'hidden'" in source
        assert "Number(style.opacity) <= 0.001" in source
        assert "document.elementsFromPoint(x, y)" in source
        assert "right <= left || bottom <= top" in source
        assert "visible: true" in source
        assert "interactive," in source
        assert "disabled," in source
        assert "new Set(out.map((item) => item.text)" in source


async def test_electron_browser_user_events_are_recorded_for_learning(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import live_service

    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    view = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")
    recorded = []

    async def record_browser_user_event(**event):
        recorded.append(event)

    class LearningService:
        pass

    LearningService.record_browser_user_event = staticmethod(
        record_browser_user_event
    )

    monkeypatch.setattr(
        live_service,
        "_active_learning_service",
        lambda: LearningService(),
    )

    assert "BROWSER_USER_EVENT_CONSOLE_PREFIX" in main
    assert "installUserEventCapture" in main
    assert "handleCapturedUserEvent" in main
    assert "postBackendJson('/api/browser/user-event'" in main
    assert "recordUserEvent('navigate'" in main
    assert "browser:set-context" in main
    assert "bridge.setContext({ sessionId: electronSessionId, roundId: rid })" in view

    result = await live_service.BrowserLiveApplicationService().record_user_event({
        "sessionId": "session_1",
        "roundId": "round_1",
        "eventKind": "navigate",
        "payload": {"source": "address_bar"},
        "browserUrl": "https://example.test/page",
        "browserTitle": "Example",
        "target": {"selector": "body"},
    })

    assert result == {"ok": True}
    assert recorded == [{
        "session_id": "session_1",
        "round_id": "round_1",
        "event_kind": "navigate",
        "payload": {"source": "address_bar"},
        "browser_url": "https://example.test/page",
        "browser_title": "Example",
        "target": {"selector": "body"},
    }]


def test_electron_browser_panel_does_not_restore_closed_tabs_from_stale_state():
    root = Path(__file__).resolve().parent.parent
    view = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")
    panel = view.split("function ElectronBrowserViewportPanel", 1)[1].split("function ScreencastBrowserViewportPanel", 1)[0]

    assert 'const nextUrl = (active && active.url) || "";' in panel
    assert "browserState && browserState.url" not in panel
    assert "browserState && browserState.active" not in panel
    assert "if (!tabs.length" not in panel


def test_workbench_chat_directory_picker_supports_desktop_and_browser_modes():
    chat = workbench_chat_source()
    i18n = workbench_i18n_source()

    assert 'window.cyrene.platform === "linux"' not in chat
    assert 'typeof window.cyrene.pickDirectory === "function"' in chat
    assert 'fetch("/api/context/pick-directory", { method: "POST" })' in chat
    assert "[wsDir, projectWorkspacePath].concat(wsHistory).forEach" in chat
    assert "workspaceOptions.push({ path: normalized, isDefault: normalized === projectWorkspacePath })" in chat
    assert 'wbcT("workbenchChat.defaultWorkspace", "Default workspace")' in chat
    assert '"workbenchChat.defaultWorkspace": "Default workspace"' in i18n
    assert '"workbenchChat.defaultWorkspace": "默认 workspace"' in i18n


def test_workbench_chat_workspace_chip_follows_project_until_user_overrides_it():
    chat = workbench_chat_source()

    # Both POSIX and Windows workspace paths render only their final directory
    # name in the chip (for example, the default Windows path renders
    # "workspace" instead of the full C:\Users\...\workspace path).
    assert 'function wbcWorkspaceDisplayName(path)' in chat
    assert 'replace(/[\\\\/]+$/, "")' in chat
    assert 'normalized.split(/[\\\\/]/).filter(Boolean).pop()' in chat
    assert "wbcWorkspaceDisplayName(option.path)" in chat
    assert "option.isDefault ? wbcT" in chat

    # The workspace override helpers take an optional draft namespace (default
    # "" for the main chat; the quick-chat window passes one) — the call sites
    # thread it through.
    assert 'return String(chat && chat.workspaceOverride || "").trim()' in chat
    assert "|| wbcLoadWorkspaceOverride(workspaceContextKey, draftNs);" in chat
    assert 'var WBC_WORKSPACE_PREFIX = "cyrene-wbc-workspace-";' in chat
    assert "function wbcWorkspaceContextKey(chatId, projectId)" in chat
    assert "var workspaceContextKey = wbcWorkspaceContextKey(chatId, projectId);" in chat
    assert "wbcSaveWorkspaceOverride(prevKey, currentOverride, draftNs);" in chat
    assert 'String(chat && chat.workspaceOverride || "").trim()' in chat
    assert 'window.dispatchEvent(new CustomEvent("cyrene:wbc-chat-created"' in chat
    assert 'window.addEventListener("cyrene:wbc-chat-created", onChatCreated);' not in chat
    assert "wbcSaveWorkspaceOverride(workspaceContextKey, workspaceOverride, draftNs);" in chat
    assert 'var projectWorkspacePath = (project && project.workspacePath) || "";' in chat
    assert (
        "var wsDir = workspaceOverride || projectWorkspacePath || "
        "(contextState && contextState.workspace_dir) || \"\";"
    ) in chat
    assert "}, [projectId, projectWorkspacePath, composerContextAvailable, contextStateRevision]);" in chat
    assert (
        'setWorkspaceOverride(selectedPath && selectedPath !== '
        'projectWorkspacePath ? selectedPath : "");'
    ) in chat
    assert "workspaceOverride: selectedPath && selectedPath !== projectWorkspacePath" in chat
    assert 'body.workspaceOverride = input.workspaceOverride || "";' in chat
    assert "model.updateChatPreferences(chatId, { soulActive: next })" in chat
    assert "model.updateChatPreferences(chatId, { workspaceActive: false })" in chat
    assert 'body.soulActive = !!input.soulActive;' in chat
    assert 'body.workspaceActive = !!input.workspaceActive;' in chat
    assert '"/api/context/remove-soul"' not in chat
    assert '"/api/context/remove-workspace"' not in chat
    assert "var composerChat = chat || chatSummary || null;" in chat
    assert 'key={"main-composer:" + String(composerChat && composerChat.id || "new")}' in chat
    assert "chat={composerChat}" in chat
    assert "Array.isArray(chat.remoteDeviceIds) ? chat.remoteDeviceIds.slice() : []" in chat
    assert 'setReasoningEffort(String(chat && chat.reasoningEffort || "").trim().toLowerCase());' in chat
    assert "var personaOn = soulAvailable && soulActive !== false;" in chat
    assert "var workspaceOn = workspaceAvailable && workspaceActive !== false;" in chat


def test_workbench_tools_menu_combines_content_commands_and_long_workspace_paths():
    root = Path(__file__).resolve().parent.parent
    chat = workbench_chat_source()
    styles = workbench_style_source()
    runtime_hooks = frontend_module_source("features/chat/runtime-page-hooks.jsx")
    file_resources = frontend_module_source("features/chat/file-resources.jsx")
    index = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    tools_rule = styles.split(".wbc-tools-menu {", 1)[1].split("}", 1)[0]
    content_rule = styles.split(".wbc-tools-content-list {", 1)[1].split("}", 1)[0]
    command_rule = styles.split(".wbc-tools-command-grid {", 1)[1].split("}", 1)[0]
    command_section_rule = styles.split(".wbc-tools-commands {", 1)[1].split("}", 1)[0]

    assert "width: min(320px, calc(100cqw - 58px));" in tools_rule
    assert "max-height: min(390px, calc(100vh - 190px));" in tools_rule
    assert "overflow-y: auto;" in tools_rule
    assert "max-height:" not in content_rule
    assert "overflow-y:" not in content_rule
    assert "max-height:" not in command_rule
    assert "overflow-y:" not in command_rule
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in command_rule
    assert "border-top:" not in command_section_rule
    assert "margin-top: 2px;" in command_section_rule
    assert ".wbc-tools-command:not(:disabled):hover" in styles
    assert "min-width: 0;" in styles
    assert 'wbcT("workbenchChat.contentItems", "Content")' in chat
    assert 'className="wbc-tools-content-list"' in chat
    assert 'wbcT("workbenchChat.chooseDirectory", "Choose directory…")' in chat
    assert "remoteDevices.map(function (device)" in chat
    assert "setToolsPanel" not in chat
    assert "function WbcCtxPicker" not in chat
    assert 'className="wbc-tools-command-grid"' in chat
    assert 'role="combobox"' in chat
    assert 'event.key === "ArrowDown" || event.key === "ArrowUp"' in chat
    assert "wbcParseSlashCommandText(text, slashPool)" in chat
    assert '"/api/context/state"' in chat
    assert '"/api/workbench/context-capabilities"' not in chat
    assert '"/api/workbench/slash-commands?project_id="' in chat
    assert "slashCommandCatalog.length ? slashCommandCatalog : WBC_COMMANDS" in chat
    assert 'item.group === "skill"' in chat
    assert 'item.group === "mcp"' in chat
    assert 'item.group === "toolPackage"' not in chat
    assert 'item.group === "customTool"' in chat
    assert 'item.group === "plugin"' in chat
    assert "payload.contextActivations = submittedContextActivations" in chat
    assert "submittedDescriptor && submittedDescriptor.activation" in chat
    assert 'key: "mcpServers"' in chat
    assert 'key: "skills"' in chat
    assert 'key: "pluginPacks"' in chat
    assert 'key: "customTools"' not in chat
    assert ".wbc-tools-context-options" in styles
    command_clear_guard = 'session.updateKind === "available_commands_update" || session.commands.length'
    assert command_clear_guard in runtime_hooks
    assert command_clear_guard in file_resources
    assert 'className={"wbc-composer-icon wbc-tools-trigger"' in chat
    assert 'enabledContentCount > 0 ? " has-content" : ""' in chat
    assert 'className="wbc-tools-trigger-count"' in chat
    assert ".wbc-tools-trigger.has-content {" in styles
    assert "border-radius: 999px;" in styles.split(".wbc-tools-trigger.has-content {", 1)[1].split("}", 1)[0]
    assert 'className={"wbc-send"' in chat
    assert ".wbc-send span" not in styles
    assert "transform: none;" in styles
    assert '<script type="module" src="compiled/app.js?v=0.9.0-beta4"></script>' in index


def test_workbench_api_timeout_covers_response_body_consumption():
    root = Path(__file__).resolve().parent.parent
    api = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "platform" / "api.jsx").read_text(encoding="utf-8")

    assert "Keep the deadline active until" in api
    assert "resp.__workbenchRequestDone = done" in api
    assert "resp.__workbenchNormalizeAbort = normalizeAbort" in api
    assert 'err.name === "AbortError" || err.isTimeout' in api


def test_workbench_api_json_times_out_when_body_stalls_after_headers():
    root = Path(__file__).resolve().parent.parent
    api_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "platform" / "api.jsx"
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


def test_workbench_model_settings_preserve_form_on_failed_response():
    root = Path(__file__).resolve().parent.parent
    source = workbench_settings_source()
    index = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "async function readSettingsResponse(response)" in source
    assert "if (!response.ok)" in source
    assert 'requestJson("/api/settings/model-config")' in source
    assert "store.setConfig(snapshot);" in source
    assert '<script type="module" src="compiled/app.js?v=0.9.0-beta4"></script>' in index


def test_workbench_chat_subagent_page_is_independent_and_localized():
    root = Path(__file__).resolve().parent.parent
    source = workbench_chat_source()
    styles = workbench_style_source()
    classic_chat = root / "src" / "cyrene" / "workbench" / "webui" / "static" / "app" / "chat.jsx"

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
    source = workbench_chat_source()

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
        "Agent 正在工作，请等待当前运行完成后再压缩。",
        "请先回答 Agent 的问题，再压缩对话。",
        "当前对话没有工具调用，无需主动压缩。",
        "后台正在蒸馏上下文，请稍后再试。",
    ]


def test_workbench_chat_exposes_browser_live_view_and_takeover():
    source = workbench_chat_source()
    live_events = frontend_module_source("features/chat/live-event-controller.jsx")

    assert 'event.type === "browser_frame" || event.type === "browser_takeover_request"' in live_events
    browser_switch_block = live_events.split("function wbcApplyBrowserEvent", 1)[1].split("function wbcHandleLiveEvent", 1)[0]
    assert 'setSideTab("browser")' not in browser_switch_block
    assert "setBrowserWindowModeByChat" in browser_switch_block
    assert "runtimeEngine.isRunning" not in browser_switch_block
    assert 'id: "browser", label: wbcT("chat.side.browser", "Browser")' in source
    assert "workbenchServices.browser().ViewportPanel" in source
    assert "function WbcBrowserSplit(" in source
    assert "onTakeoverComplete: onTakeoverComplete" in source
    browser_split = source.split("function WbcBrowserSplit(", 1)[1].split("function WbcSubagentsSplitHost", 1)[0]
    assert "desiredTabId" not in browser_split
    assert "bridge.activateTab({ sessionId: browserSessionId, tabId: tab.id })" in browser_split
    assert "zoomEnabled: true" in browser_split
    assert "resizeEdgeHintEnabled: true" in browser_split
    assert "zoomEnabled: false" not in browser_split
    assert "WBC_ICONS.windowMaximize" in browser_split
    assert "WBC_ICONS.windowRestore" in browser_split
    assert 'className={"wbc-side-agent-split wbc-browser-split" + (maximized ? " maximized" : "")}' in browser_split
    assert 'window.ReactDOM.createPortal(browserSplit, workbenchPortalRoot)' in browser_split
    assert "answerPendingQuestion(questionId, optionText, resumeMode)" in source


def test_warning_toast_has_no_colored_left_accent():
    css = workbench_style_source()

    assert ".workbench-toast.is-warning { border-left: 1px solid var(--wb-line); }" in css
    assert ".workbench-toast.is-warning { border-left-color: var(--wb-amber); }" not in css


def test_agent_error_notice_keeps_its_content_inside_a_uniform_border():
    css = workbench_style_source()

    error_card_css = css.split(".wbc-error-card {", 1)[1].split("}", 1)[0]
    agent_error_css = css.split(".wbc-error-card.is-agent-error {", 1)[1].split("}", 1)[0]
    agent_error_tones_css = css.split(
        ".wbc-error-card.is-agent-error.is-network {", 1
    )[1].split(".wbc-error-icon {", 1)[0]

    assert "flex: 0 0 auto;" in error_card_css
    assert "border-left:" not in agent_error_css
    assert "border-left-color:" not in agent_error_tones_css


def _run_workbench_shortcuts_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    runtime_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "platform" / "runtime.jsx"
    shortcuts_source = re.sub(
        r"^(?:import|export)\s+.*$",
        "",
        (root / "src/cyrene/workbench/webui/frontend/workbench-shortcuts.jsx").read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
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
    var workbenchServices = new Proxy({{}}, {{
      get: (_target, name) => () => window.CyreneUI.require(String(name))
    }});
    eval({json.dumps(shortcuts_source)});
    const result = ({expression});
    process.stdout.write(JSON.stringify(result));
    """
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def test_workbench_shortcuts_module_exposes_actions_and_platform_aware_mod():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-shortcuts.jsx").read_text(encoding="utf-8")

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
    assert "composer-send" in ids
    assert "composer-newline" in ids
    assert "switch-session-1" in ids
    assert "switch-session-2" in ids
    assert "switch-session-3" in ids
    assert "next-session" in ids
    assert "previous-session" in ids
    assert "close-session-tab" in ids


def test_empty_project_creates_a_default_conversation():
    source = workbench_chat_source()
    refresh = source.split("function refreshChats(selectId) {", 1)[1].split(
        "function applyChatSummaryEvent", 1
    )[0]

    assert "if (!list.length)" in refresh
    assert "return model.createChat(requestedProjectId)" in refresh
    assert "setChats(initial);" in refresh
    assert "selectChat(chat.id);" in refresh


def test_workbench_shortcut_labels_use_tab_terminology_in_both_locales():
    translations = workbench_i18n_source()

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


def test_settings_models_use_the_shared_settings_typography():
    styles = workbench_style_source()

    typography = styles.split(
        "/* Models is a Settings page, not a code editor.", 1
    )[1].split(".workbench-shell .wb-text-size-sample", 1)[0]
    assert ".settings-overlay .wb-models-panel" in typography
    assert "font-family: var(--wb-font) !important;" in typography
    assert "font-size: calc(14px * var(--wb-ui-font-scale)) !important;" in typography
    assert "font-size: calc(12px * var(--wb-ui-font-scale)) !important;" in typography


def test_project_memory_failed_status_shows_attempt_details():
    source = frontend_module_source("features/shell/support.jsx")
    styles = workbench_style_source()

    assert 'learningPhase === "failed"' in source
    assert "wbProjectMemoryDate(learningStatus.updatedAt)" in source
    assert "learningStatus.model && learningStatus.model.model" in source
    assert "learningStatus.error ||" in source
    assert ".workbench-project-memory-learning-status small {" in styles


def test_pending_question_disables_chat_composer_controls():
    chat = workbench_chat_source()
    chat_composer = chat.split("function WbcComposer(", 1)[1].split(
        "function wbcClearComposerDraft", 1
    )[0]
    chat_attachments = frontend_module_source("features/chat/composer-attachments.jsx")

    assert "var awaitingAnswer = !!(chat && chat.pendingQuestion && chat.pendingQuestion.id);" in chat_composer
    assert "if (awaitingAnswer) return;" in chat_composer
    assert "var sendDisabled = awaitingAnswer ||" in chat_composer
    assert "disabled={awaitingAnswer || !capText" in chat_composer
    assert "disabled={uploading || running || awaitingAnswer}" in chat_composer
    assert "disabled={awaitingAnswer || voicePhase" in chat_composer
    assert "if (awaitingAnswer) return\n      var detail" in chat_attachments



def test_workbench_model_picker_compacts_without_overlapping_send_button():
    styles = workbench_style_source()

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
    assert 'className="wbc-model-button-icon" aria-hidden="true"' in workbench_chat_source()
    assert ".wbc-model-button-icon" in compact_rule
    assert "display: inline-flex;" in compact_rule
    assert ".wbc-model-button-name" in compact_rule
    assert ".wbc-model-button-effort" in compact_rule
    assert "display: none;" in compact_rule


def test_workbench_file_drop_routes_files_to_chat_and_knowledge():
    root = Path(__file__).resolve().parent.parent
    workbench = workbench_shell_source()
    chat = workbench_chat_source()
    library = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")
    styles = workbench_style_source()

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

    # Chat routes a drop from the whole module to its existing upload pipeline,
    # which appends the uploaded files to the composer attachment row.
    assert 'new CustomEvent("cyrene:add-chat-attachments"' in chat
    assert 'window.addEventListener("cyrene:add-chat-attachments"' in chat
    assert "model.uploadFiles(files)" in chat

    # The canonical library page keeps file ingestion on its existing upload path.
    assert "function handleFiles(files)" in library
    assert 'type: "file", multiple: true' in library
    assert "client.upload(files)" in library
    assert ".wb-file-drop-overlay" in styles


def test_workbench_file_drop_hook_prevents_navigation_and_delivers_files():
    source = workbench_shell_source()
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


def test_workbench_settings_page_has_shortcuts_tab():
    root = Path(__file__).resolve().parent.parent
    source = workbench_settings_source()
    settings_entry = (root / "src/cyrene/workbench/webui/frontend/settings-overlay.jsx").read_text(encoding="utf-8")
    settings_index = (root / "src/cyrene/workbench/webui/frontend/features/settings/index.jsx").read_text(encoding="utf-8")
    translations = workbench_i18n_source()
    styles = workbench_style_source()

    assert '{ id: "shortcuts", labelKey: "settings.shortcuts", icon: "keyboard" }' in source
    assert "function ShortcutsPanel" in source
    assert "React.createElement(ShortcutsPanel" in source
    assert "function SettingsPage(" in source
    assert 'parent_id: "settings_page"' in source
    assert 'role: "region"' in source
    assert 'workbenchServices.shortcuts()' in source
    assert "captureEvent" in source
    # The panel groups bindings and offers a reset-all action.
    assert "settings.shortcutGroupGlobal" in source
    assert "settings.resetShortcuts" in source
    # i18n keys for both languages
    assert '"settings.shortcuts": "Keyboard shortcuts"' in translations
    assert '"settings.shortcuts": "键盘快捷键"' in translations
    assert '"shortcut.action.search"' in translations
    assert '"shortcut.action.composerSend"' in translations
    # Styles for the panel
    assert ".wb-shortcuts-panel" in styles
    assert ".wb-shortcut-row" in styles
    assert ".wb-shortcut-capture" in styles
    # Architecture guard: Settings consumes the domain barrel and the barrel
    # exposes the real shortcuts panel; app-entry loading is covered by the
    # executable ESM bundle contract.
    assert 'from "./features/settings/index.jsx"' in settings_entry
    assert 'export { ShortcutsPanel } from "./shortcuts.jsx"' in settings_index


def test_workbench_about_hero_owns_update_action_and_download_progress():
    source = workbench_settings_source()
    styles = workbench_style_source()

    about_block = source.split("function UpdateSection({ t, config }) {", 1)[1].split(
        "// ── Skills Panel", 1
    )[0]
    hero_index = about_block.index('className: "wb-about-product-card"')
    action_index = about_block.index('className: "wb-btn primary wb-about-check-btn"')
    update_index = about_block.index('className: "wb-about-update-card"')

    assert hero_index < action_index < update_index
    assert 'className: "wb-about-hero-progress"' in about_block
    assert '"--wb-about-download-progress": heroProgress + "%"' in about_block
    assert "var heroProgress = downloaded\n    ? 100" in about_block
    assert about_block.index("var heroProgress = downloaded") < about_block.index("progressTotal > 0")
    assert 'className: "wb-about-update-footer"' not in about_block
    assert 'className: "wb-about-related-card"' in about_block
    assert "var relatedLinks = [" in about_block

    progress_rule = styles.split(".wb-about-hero-progress {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in progress_rule
    assert "inset: 0 auto 0 0" in progress_rule
    assert "width: var(--wb-about-download-progress)" in progress_rule
    assert ".wb-about-product-card.is-downloading .wb-about-hero-progress" in styles
    assert ".wb-about-related-row" in styles

    changelog_modal_rule = styles.split(".wb-changelog-modal {", 1)[1].split("}", 1)[0]
    assert "height: min(460px, calc(100vh - 48px));" in changelog_modal_rule
    assert "--wb-settings-panel-height" not in styles


def test_extension_center_uses_fixed_settings_geometry_and_localized_catalog_copy():
    source = workbench_settings_source()
    translations = workbench_i18n_source()
    styles = workbench_style_source()

    assert '.settings-overlay-panel:has([data-settings-active-tab="extensions"])' not in styles
    assert 'React.createElement(PluginCenterPage' in source
    assert 'className="wb-extension-expand-button"' in source
    assert source.index('className="wb-extension-actions"') < source.index('className="wb-extension-expand-button"')
    chevron_css = styles.split(".wb-extension-chevron {", 1)[1].split("}", 1)[0]
    assert "width: 18px;" in chevron_css
    assert "height: 18px;" in chevron_css
    assert "display: grid;" in chevron_css
    assert "place-items: center;" in chevron_css
    assert "line-height: 0;" in chevron_css
    assert ".wb-extension-chevron > svg { display: block; }" in styles
    assert 'className="wb-extension-source-sections"' in source
    assert 't("settings.extensionMcpRegistryHint",' in source
    assert ".wb-extension-source-modal" in styles
    assert 'className="wb-btn wb-plugin-center-back"' in source
    page_css = styles.split(
        ".wb-plugin-center-popover.wb-plugin-center-page {", 1
    )[1].split("}", 1)[0]
    assert "position: static;" in page_css
    assert "width: 100%;" in page_css
    assert "max-height: none;" in page_css
    assert "box-shadow: none;" in page_css
    assert ".settings-overlay .settings-panel.wb-plugin-center-page-shell" in styles
    assert 'role={c.props.inline ? "region" : "dialog"}' in source

    settings_content_css = styles.split("/* Content area */", 1)[1].split(
        ".settings-overlay-content {", 1
    )[1].split("}", 1)[0]
    assert "overflow-y: auto;" in settings_content_css

    for extension_id in ("python", "uv", "tex", "node", "github-cli", "bun"):
        assert translations.count(f'"settings.extensionCatalog.{extension_id}.name"') == 2
        assert translations.count(f'"settings.extensionCatalog.{extension_id}.description"') == 2
    assert translations.count('"settings.extensionSource.system"') == 2
    assert translations.count('"settings.extensionHealthValue.healthy"') == 2


def test_remote_settings_keeps_compatibility_on_and_persists_package_checkboxes():
    source = workbench_settings_source()
    i18n = workbench_i18n_source()
    styles = workbench_style_source()

    remote_panel = source.split("function RemotePanel(p) {", 1)[1].split(
        "function RemotePeerCard", 1
    )[0]
    assert "FieldRow(" in remote_panel
    assert "Toggle(!!remote.enabled" in remote_panel
    assert "remote-status-card" not in remote_panel
    assert ".remote-status-card" not in styles
    assert "remoteCapabilityLabel" not in source
    assert "remoteCompatibilityCapabilities" not in remote_panel
    assert 'Toggle(!!remote.enabled' in remote_panel
    assert 'className: "remote-option"' in source
    assert "toggleList(project.id, setInviteProjects)" in remote_panel
    assert "project_scopes: inviteProjects" in remote_panel
    assert "remoteTransportDetail(" in source
    assert "remoteEventLabel(" in source
    assert "remoteTransportDetail(t, transport)" in remote_panel
    assert "transport.port_fallback" in source
    assert i18n.count('"settings.remoteTransportAlternatePort"') == 2
    assert 'var [pairingMode, setPairingMode] = useStateSt("share")' in remote_panel
    assert 'className: "wb-seg remote-pairing-tabs"' in remote_panel
    assert "inviteDefaultsInitializedRef.current = true" in remote_panel
    assert "(payload.projects || []).map(function (project)" in remote_panel
    assert "payload.projects || []" in remote_panel
    assert 'className: "remote-pairing-layout"' in remote_panel
    assert 'className: "remote-pairing-group"' in remote_panel
    assert 'className: "remote-project-choices"' in remote_panel
    assert ".remote-project-choices" in styles
    assert 'className: "remote-pairing-columns"' not in remote_panel
    assert 'className: "remote-pairing-card"' not in remote_panel
    assert ".remote-pairing-columns" not in styles
    assert ".remote-pairing-card" not in styles
    assert i18n.count('"settings.remotePairModeShare"') == 2
    assert i18n.count('"settings.remotePairModeControl"') == 2
    assert i18n.count('"settings.remotePairCapabilities"') == 2
    assert i18n.count('"settings.remoteSharedProjects"') == 2
    assert '"settings.remoteAllowController"' in i18n
    assert '"settings.remoteAllowControllerHint"' in i18n
    assert 'settingsFetch("/api/remote/pairing/short-key"' in remote_panel
    assert 'settingsFetch("/api/remote/pairing/connect"' in remote_panel
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
    assert 'workbenchServices.feedback()' in remote_panel
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
    assert i18n.count('"settings.remotePairingKey"') == 2
    assert i18n.count('"settings.remoteDeviceAddress"') == 2

    assert i18n.count('"settings.remoteCompatibilityAlwaysOn"') == 0

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
    source = workbench_settings_source()

    update_section = source.split("function UpdateSection({ t, config }) {", 1)[1].split(
        "\nexport { AboutPanel }", 1
    )[0]

    assert 'var dataState = workbenchServices.data().state;' in update_section
    assert "dataState.appVersion" in update_section


def test_workbench_settings_dynamic_lists_have_stable_react_keys():
    source = workbench_settings_source()

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
    source = workbench_shell_source()

    # Help center reads the binding list from the registered shortcuts service instead of
    # hardcoding the keys array, so customizations surface there too.
    help_block = source.split("function WorkbenchHelpCenter", 1)[1].split("function WorkbenchEditProjectModal", 1)[0]
    assert "workbenchServices.shortcuts()" in help_block
    assert "shortcutList" in help_block
    assert "help.customizeShortcuts" in help_block
    # The old hardcoded list is gone.
    assert '{ id: "search", label: t("help.shortcut.search"), keys: ["mod", "K"] }' not in help_block


def test_workbench_memory_cite_tab_renders_actual_citations_not_placeholder():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")

    # The old placeholder text is gone.
    assert "引用记录会在 Agent 引用此记忆时自动记录" not in source
    # The Cite tab now renders citations from the memory's citations list.
    assert "m.citations" in source
    assert "wb-mem-cite-list" in source
    assert "wb-mem-cite-row" in source


def test_workbench_memory_history_tab_renders_events_not_hardcoded():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")

    # The old hardcoded two-row history is gone — isolate the historyBody block.
    history_block = source.split("var historyBody", 1)[1].split("return h(\"aside\"", 1)[0]
    assert '"最后更新"' not in history_block
    assert '"创建记忆"' not in history_block
    # The History tab now renders from m.history.
    assert "m.history" in source
    assert "historyEvents" in source
    assert "historyActionLabel(ev.action, t)" in source
    assert "ev.action_label" not in source


def test_workbench_memory_combines_overview_into_source_card():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    css = workbench_style_source()

    rail_block = source.split("// ── category rail ──", 1)[1].split("// ── memory card list ──", 1)[0]
    assert rail_block.count('h("div", { className: "wb-mem-card" }') == 1
    assert 't("memory.overview", "Memory overview")' not in rail_block
    assert 'h(Donut, { segments: sources, total: overview.total || 0, t: t })' in rail_block
    assert 'className: "wb-mem-source-overview"' in rail_block
    assert rail_block.index('className: "wb-mem-source-legend"') < rail_block.index('className: "wb-mem-source-overview"')
    assert ".wb-mem-source-overview {" in css
    source_body_css = css.split(".wb-mem-source-body {", 1)[1].split("}", 1)[0]
    source_overview_css = css.split(".wb-mem-source-overview {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: 78px minmax(0, 1fr);" in source_body_css
    assert "grid-column: 1 / -1;" in source_overview_css
    assert "border-top: 1px solid var(--wb-line);" in source_overview_css


def test_workbench_memory_detail_wraps_long_content_without_horizontal_overflow():
    css = workbench_style_source()

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


def test_workbench_schedule_timeline_items_do_not_use_left_accent_bar():
    css = workbench_style_source()

    block = css.split("\n.wb-sched-block {", 1)[1].split("}", 1)[0]
    assert "border-left" not in block
    assert "border: none;" in block
    assert "padding: 3px 10px;" in block


def test_workbench_memory_list_contains_long_content_and_uses_neutral_selection():
    css = workbench_style_source()

    scroll_block = css.split("\n.wb-mem-scroll {", 1)[1].split("}", 1)[0]
    item_block = css.split("\n.wb-mem-item {", 1)[1].split("}", 1)[0]
    active_block = css.split("\n.wb-mem-item.active {", 1)[1].split("}", 1)[0]
    candidate_active_block = css.split("\n.wb-mem-item.wb-learning-candidate-card.active,", 1)[1].split("}", 1)[0]
    chip_block = css.split("\n.wb-mem-item-tags .wb-mem-chip {", 1)[1].split("}", 1)[0]
    chain_active_block = css.split("\n.wb-learning-chain-card.active {", 1)[1].split("}", 1)[0]

    assert "overflow-x: hidden;" in scroll_block
    assert "box-sizing: border-box;" in item_block
    assert "min-width: 0;" in item_block
    assert "max-width: 100%;" in chip_block
    assert "white-space: normal;" in chip_block
    assert "overflow-wrap: anywhere;" in chip_block
    assert "var(--wb-accent)" not in active_block
    assert "var(--wb-active" not in active_block
    assert ".wb-mem-item.wb-learning-candidate-card.active:hover" in css
    assert "var(--wb-accent)" not in candidate_active_block
    assert "color-mix(in srgb, var(--wb-text) 18%, var(--wb-line))" in candidate_active_block
    assert "0 2px 6px" in candidate_active_block
    assert "#f23491" not in chain_active_block
    assert "var(--wb-line-2)" in chain_active_block


def test_workbench_skill_learning_uses_actionable_candidate_status_only():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    translations = workbench_i18n_source()

    assert 'chainCandidate ? h("div", { className: "wb-learning-review-pill "' in source
    assert "candidateNextStepText(chainCandidate, t)" in source
    assert '\n      rail,\n      main,' in source
    assert 'onExit: function () { setActivePanel(""); }' in source
    assert "不是可复用的多工具流程" not in translations
    assert '"memory.learning.noRepeatYet": "尚未发现重复"' in translations


def test_workbench_skill_learning_is_gated_by_active_skills_plugin():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")

    page = source.split("function WorkbenchMemoryPage", 1)[1]
    assert 'pluginModules.indexOf("skills") >= 0' in page
    assert 'var learningActive = skillsEnabled && activePanel === "learning";' in page
    assert 'if (!skillsEnabled) return Promise.resolve(null);' in page
    assert 'skillsEnabled && h("button"' in page
    assert 'var main = learningActive ? h(SkillLearningMain' in page
    assert 'learningActive ? h(SkillLearningPanel' in page


def test_code_and_terminal_frontend_stop_when_code_plugin_marker_is_absent():
    page = frontend_module_source("features/chat/page.jsx")
    rail = frontend_module_source("features/chat/rail.jsx")
    terminal = frontend_module_source("terminal/entry.jsx")
    diff = frontend_module_source("shared/diff/viewer.jsx")
    editor = frontend_module_source("code/editor.jsx")

    assert 'var codeAvailable = pluginModules.indexOf("code") >= 0;' in page
    assert "useWbcTerminalCatalog(projectId, codeAvailable)" in page
    assert "if (!codeAvailable || !projectId || !isActive) return undefined;" in page
    assert "codeAvailable={codeAvailable}" in page
    assert "if (!codeAvailable || !projectId)" in rail
    assert '{codeAvailable ? <>' in rail
    assert '(codeAvailable || projectSectionPluginTools.length) && railMode === "chat" && !collapsed' in rail
    assert 'var codeAvailable = pluginModules.indexOf("code") >= 0;' in terminal
    assert "if (!codeAvailable || !host || !terminalId) return undefined;" in terminal
    assert "}, [terminalId, codeAvailable]);" in terminal
    assert "if (!codeAvailable) return null;" in terminal
    assert "if (!codeAvailable)" in diff
    assert "[props.diff, props.left, props.right, props.mode, codeAvailable]" in diff
    assert "if (!codeAvailable || !hostRef.current) return undefined;" in editor


def test_shared_diff_viewer_uses_the_review_layout_for_every_consumer():
    root = Path(__file__).resolve().parent.parent
    diff = frontend_module_source("shared/diff/viewer.jsx")
    workspace = frontend_module_source("features/chat/workspace-surface.jsx")
    resource_splits = frontend_module_source("features/chat/resource-splits.jsx")
    styles = (
        root
        / "src"
        / "cyrene"
        / "workbench"
        / "webui"
        / "frontend"
        / "features"
        / "chat"
        / "context.css"
    ).read_text(encoding="utf-8")

    assert "function compactHunkLines" in diff
    assert 'className: "diff-context-fold"' in diff
    assert 'className: "diff-viewer-stats"' in diff
    assert 'setDiffText(props.diff || "");' in diff
    assert 'line === "\\\\ No newline at end of file"' in diff
    assert 'diffT("chat.diff.noFinalNewline", "No newline at end of file")' in diff
    assert "workbenchServices.diff().Panel" in workspace
    assert "workbenchServices.diff().Panel" in resource_splits
    assert ".diff-viewer-panel {" in styles
    assert ".diff-context-fold {" in styles
    assert ".diff-line-meta {" in styles
    assert 'React.createElement("span", { className: "diff-ln" }, line.type === "del" ? line.leftNum : line.rightNum)' in diff
    assert "diff-ln-left" not in diff
    assert "diff-ln-right" not in diff
    assert '"--diff-line-number-width": "calc(" + lineNumberDigits + "ch + 14px)"' in diff
    assert "grid-template-columns: 4px var(--diff-line-number-width, 26px) minmax(max-content, 1fr);" in styles
    assert "overflow-x: hidden;" in styles
    assert "overflow-y: auto;" in styles
    assert "background: var(--wb-green);" in styles
    assert "background: repeating-linear-gradient(" in styles
    assert ".diff-line-add .diff-ln {" in styles
    assert ".diff-line-del .diff-ln {" in styles
    assert "if (!review.payload) return <WorkspaceEmpty" in workspace
    assert "if (review.loading || !review.payload)" not in workspace
    assert "disabled={review.loading}" in workspace
    assert ".wbc-change-diff .diff-viewer-panel" not in styles


def test_chat_agent_picker_is_gated_by_agents_plugin_marker():
    page = frontend_module_source("features/chat/page.jsx")
    composer = frontend_module_source("features/chat/composer.jsx")
    catalog = frontend_module_source("features/chat/composer-model-state.jsx")

    assert 'var agentsAvailable = pluginModules.indexOf("agents") >= 0;' in page
    assert "onDraftAgentChange={agentsAvailable ? handleDraftAgentChange : null}" in page
    assert "wbcSaveDraftAgentBinding(projectId, null);" in page
    assert 'var agentsAvailable = pluginModules.indexOf("agents") >= 0;' in composer
    assert 'var agentPickerEnabled = agentsAvailable && typeof onDraftAgentChange === "function";' in composer
    disabled_branch = catalog.split("if (!enabled) {", 1)[1].split("}", 1)[0]
    assert "setOptions([])" in disabled_branch
    assert "WorkbenchChatModel.listAgents()" in catalog


def test_workbench_skill_learning_moves_full_content_into_right_inspector():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")

    panel = source.split("function SkillLearningPanel", 1)[1].split("function SkillLearningMain", 1)[0]
    main = source.split("function SkillLearningMain", 1)[1].split("// ── main page", 1)[0]

    # The centre surface is now a memory-style list rather than a second,
    # duplicated detail column.
    assert 'className: "wb-mem-scroll wb-learning-list-scroll"' in main
    assert 'className: "wb-mem-item wb-learning-list-item"' in main
    assert 'className: "wb-learning-detail"' not in main
    # Every information group that used to live in that middle column remains
    # available in the right inspector alongside its existing media/analysis.
    assert 't("memory.learning.topic"' in panel
    assert 't("memory.learning.learningState"' in panel
    assert 't("memory.learning.stepTitle"' in panel
    assert "translatedToolParamName(item.key, t)" in panel
    assert 't("memory.learning.agentAnswer"' in panel
    assert 't("memory.learning.behaviorAnalysis"' in panel
    assert 't("memory.learning.duplicateCheck"' in panel
    assert 't("memory.learning.nextStep"' in panel
    assert 'examples.map(function (example, index)' in panel
    assert "activeSession.chains.slice" not in main
    assert "learnedSkills.slice" not in main


def test_workbench_skill_delete_matches_memory_detail_header_action():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    css = workbench_style_source()

    panel = source.split("function SkillLearningPanel", 1)[1].split("function SkillLearningMain", 1)[0]
    header = panel.split('className: "wb-detail-accordion-head wb-mem-detail-nav-head"', 1)[1].split(
        'className: "wb-detail-accordion-list"', 1
    )[0]
    assert 'className: "wb-detail-card-delete"' in header
    assert "onClick: deleteLearnedSkill" in header
    assert 'disabled: learning.busy === "delete"' in header
    assert 'className: "wb-detail-card-primary"' not in panel
    assert 't("memory.learning.learnAsSkill"' not in header
    assert 'learning.runAction("learn"' not in header
    assert 'className: "wb-btn danger"' not in panel
    disabled_rule = css.split(".wb-detail-card-delete:disabled {", 1)[1].split("}", 1)[0]
    assert "opacity: .45" in disabled_rule
    assert "display: none" not in disabled_rule


def test_workbench_skill_candidates_open_complete_keyboard_accessible_details():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    i18n = workbench_i18n_source()
    css = workbench_style_source()

    panel = source.split("function SkillLearningPanel", 1)[1].split("function SkillLearningMain", 1)[0]
    main = source.split("function SkillLearningMain", 1)[1].split("// ── main page", 1)[0]
    assert 'className: "wb-learning-candidate-detail-hit"' in main
    assert '"aria-pressed": detailKind === "candidate"' in main
    assert "onSelectCandidate(candidate.id)" in main
    assert 'setSelectedLearningDetailKind("candidate")' in source
    assert "selectedLearningCandidateId" in source
    assert 'if (detailKind === "candidate")' in panel
    for detail_key in (
        "candidateDetails",
        "candidateOverview",
        "occurrences",
        "parameters",
        "riskLevel",
        "relatedRounds",
        "parameterizedScript",
    ):
        assert f'memory.learning.{detail_key}' in panel
    assert "JSON.stringify(candidateScript, null, 2)" in panel
    assert '"memory.learning.candidateDetails": "候选技能详情"' in i18n

    candidate_card = css.split(".wb-mem-item.wb-learning-candidate-card {", 1)[1].split("}", 1)[0]
    assert "display: block;" in candidate_card
    assert "padding: 0;" in candidate_card
    candidate_actions = css.split(".wb-learning-candidate-card .wb-learning-candidate-actions {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in candidate_actions
    assert "align-items: center;" in candidate_actions
    assert "flex-wrap: wrap;" in candidate_actions


def test_workbench_skill_learning_keeps_right_inspector_on_compact_widths():
    css = workbench_style_source()

    learning_responsive = css.split("@media (min-width: 761px) and (max-width: 980px)", 1)[1]
    compact = learning_responsive.split("@media", 1)[0]
    narrow = learning_responsive.split("@media (max-width: 760px)", 1)[1].split("@media", 1)[0]
    assert ".wb-mem-page.learning-active > .wb-mem-detail" in compact
    assert "display: flex;" in compact
    assert "width: 360px;" in compact
    assert ".wb-mem-page.learning-active > .wb-mem-detail" in narrow
    narrow_detail = narrow.split(".wb-mem-page.learning-active > .wb-mem-detail", 1)[1].split("}", 1)[0]
    assert "display: flex;" in narrow_detail
    assert "display: none;" not in narrow_detail
    assert "@media (max-width: 1500px)" not in css
    assert "@media (max-width: 1080px)" in css
    assert "@media (max-width: 760px)" in css


def test_workbench_skill_steps_adapt_to_narrow_inspector_without_omitting_content():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    css = workbench_style_source()

    assert 'className: "wb-learning-step-content"' in source
    assert 'h("code", { title: String(item.value) }, item.value)' in source
    inspector = css.split(".wb-mem-replay-panel {", 1)[1].split("}", 1)[0]
    assert "container: wb-learning-inspector / inline-size;" in inspector
    compact = css.split("@container wb-learning-inspector (max-width: 520px) {", 1)[1].split(".wb-detail-shot {", 1)[0]
    assert ".wb-mem-replay-panel .wb-learning-step" in compact
    assert '"number icon title status"' in compact
    assert '"details details details details"' in compact
    assert ".wb-learning-step-content > .wb-learning-step-params" in compact
    assert "grid-area: details;" in compact
    assert "display: contents;" in compact
    assert "white-space: normal;" in compact
    assert "overflow-wrap: anywhere;" in compact


def test_workbench_behavior_analysis_stays_compact_in_narrow_inspector():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    css = workbench_style_source()

    assert 'className: "wb-replay-duplicates wb-learning-duplicate-status"' in source
    metrics = css.split(".wb-replay-metrics {", 1)[1].split("}", 1)[0]
    assert "repeat(3, minmax(0, 1fr))" in metrics
    assert "repeat(4" not in metrics
    duplicate_status = css.split(".wb-learning-duplicate-status {", 1)[1].split("}", 1)[0]
    assert "justify-content: space-between;" in duplicate_status
    assert "flex-wrap: wrap;" in duplicate_status
    narrow = css.split("@container wb-learning-inspector (max-width: 380px) {", 1)[1]
    assert "grid-template-columns: 1fr;" in narrow


def test_workbench_skill_learning_i18n_covers_visible_labels_and_tool_parameters():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    en_catalog = frontend_module_source("shared/i18n/catalog-en.jsx")
    zh_catalog = frontend_module_source("shared/i18n/catalog-zh.jsx")

    learning = source.split("function SkillLearningPanel", 1)[1].split("// ── main page", 1)[0]
    used_keys = set(re.findall(r't\("(memory\.learning\.[^"]+)"', learning))
    en_keys = set(re.findall(r'"(memory\.learning\.[^"]+)"\s*:', en_catalog))
    zh_keys = set(re.findall(r'"(memory\.learning\.[^"]+)"\s*:', zh_catalog))
    assert used_keys <= en_keys
    assert used_keys <= zh_keys
    assert en_keys == zh_keys

    assert 'raw.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase()' in source
    labels = _run_workbench_i18n_js(
        "({ "
        'namespaceEn: window.WorkbenchI18n.tForLang("memory.learning.toolParam.namespace", "en"), '
        'namespaceZh: window.WorkbenchI18n.tForLang("memory.learning.toolParam.namespace", "zh"), '
        'filePathEn: window.WorkbenchI18n.tForLang("memory.learning.toolParam.file_path", "en"), '
        'filePathZh: window.WorkbenchI18n.tForLang("memory.learning.toolParam.file_path", "zh")'
        " })"
    )
    assert labels == {
        "namespaceEn": "Namespace",
        "namespaceZh": "命名空间",
        "filePathEn": "File path",
        "filePathZh": "文件路径",
    }


def test_workbench_skill_learning_remains_operable_in_short_windows():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    css = workbench_style_source()
    translations = workbench_i18n_source()

    scroll_block = css.split(".wb-mem-scroll {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in scroll_block
    assert 'className: "wb-mem-scroll wb-learning-list-scroll"' in source
    assert ".wb-learning-list-scroll" in css
    assert ".wb-mem-replay-panel .wb-mem-detail-scroll" in css
    assert "@media (max-height: 760px)" in css

    assert "translatedToolParamName(item.key, t)" in source
    assert '"memory.learning.toolParam.payload": "Payload"' in translations
    assert '"memory.learning.toolParam.payload": "操作数据"' in translations
    assert '"memory.learning.toolParam.target": "目标元素"' in translations


def test_workbench_memory_related_uses_tag_and_content_matching_not_category_only():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")

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
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")

    assert "library.myCollections" in source
    assert "library.tagCloud" in source
    assert 'scope.type === "collection"' in source
    assert 'scope.type === "tag"' in source


def test_workbench_library_tags_are_editable_inline():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")

    assert "function TagsWorkspace" in source
    assert "wb-lib-tag-editor" in source
    assert "props.onUpdate({ tags: next })" in source
    assert "client.update(selectedId, value)" in source


def test_workbench_library_content_tab_renders_markdown():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")
    renderer = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx").read_text(encoding="utf-8")

    assert "renderMarkdownHtml" in source
    assert "workbenchServices.markdown().render" in source
    assert "root.marked.parse(source)" in renderer
    assert "root.DOMPurify.sanitize" in renderer
    assert "dangerouslySetInnerHTML" in source
    assert "wb-lib-markdown" in source


def test_markdown_bare_url_stops_at_cjk_punctuation():
    root = Path(__file__).resolve().parent.parent
    renderer_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx"
    marked_path = root / "src" / "cyrene" / "workbench" / "webui" / "static" / "app" / "marked.min.js"
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


def test_markdown_temperature_ranges_do_not_form_cross_line_strikethrough():
    root = Path(__file__).resolve().parent.parent
    renderer_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx"
    marked_path = root / "src" / "cyrene" / "workbench" / "webui" / "static" / "app" / "marked.min.js"
    script = f"""
const fs = require("fs");
const vm = require("vm");
const marked = require({json.dumps(str(marked_path))});
const services = {{}};
const window = {{
  marked,
  DOMPurify: {{ sanitize: (html) => html }},
  CyreneUI: {{ register: (name, service) => (services[name] = service) }},
}};
vm.runInNewContext(fs.readFileSync({json.dumps(str(renderer_path))}, "utf8"), {{ window }});
const weather = [
  "**8月22日（周六）**：大雨转中雨，26~30℃",
  "**8月23日（周日）**：小雨转多云，25~32℃",
].join("\\n");
process.stdout.write(JSON.stringify({{
  weather: services.markdown.renderRich(weather),
  strike: services.markdown.renderRich("保留 ~~删除线~~ 语法"),
}}));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert "26~30℃" in result["weather"]
    assert "25~32℃" in result["weather"]
    assert "<del>" not in result["weather"]
    assert "<del>删除线</del>" in result["strike"]


def test_markdown_interactive_blocks_render_only_after_streaming_finishes():
    root = Path(__file__).resolve().parent.parent
    renderer_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx"
    marked_path = root / "src" / "cyrene" / "workbench" / "webui" / "static" / "app" / "marked.min.js"
    script = f"""
const fs = require("fs");
const vm = require("vm");
const marked = require({json.dumps(str(marked_path))});
const services = {{}};
const window = {{
  marked,
  DOMPurify: {{ sanitize: (html) => html }},
  CyreneUI: {{ register: (name, service) => (services[name] = service) }},
}};
vm.runInNewContext(fs.readFileSync({json.dumps(str(renderer_path))}, "utf8"), {{ window }});
const source = [
  "Visible introduction.",
  "",
  ":::details **Analysis**",
  "Detailed **Markdown**.",
  ":::",
  "",
  ":::card Performance comparison",
  "| Option | Time |",
  "| --- | --- |",
  "| A | 12ms |",
  ":::",
  "",
  "Visible conclusion.",
].join("\\n");
process.stdout.write(JSON.stringify({{
  finalHtml: services.markdown.renderRich(source),
  streamingHtml: services.markdown.renderRich(source, {{ interactive: false }}),
}}));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert '<details class="wbc-fold" data-wbc-source="' in result["finalHtml"]
    assert '<summary><strong>Analysis</strong></summary>' in result["finalHtml"]
    assert '<div class="wbc-fold-body"><p>Detailed <strong>Markdown</strong>.</p>' in result["finalHtml"]
    assert '<div class="wbc-card" data-wbc-source="' in result["finalHtml"]
    assert '<div class="wbc-card-title">Performance comparison</div>' in result["finalHtml"]
    assert "<table>" in result["finalHtml"]
    assert "Visible introduction." in result["streamingHtml"]
    assert "Visible conclusion." in result["streamingHtml"]
    assert "Detailed" not in result["streamingHtml"]
    assert "Performance comparison" not in result["streamingHtml"]
    assert "wbc-fold" not in result["streamingHtml"]
    assert "wbc-card" not in result["streamingHtml"]


def test_markdown_streaming_strip_preserves_directives_inside_code_fences():
    root = Path(__file__).resolve().parent.parent
    renderer_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx"
    marked_path = root / "src" / "cyrene" / "workbench" / "webui" / "static" / "app" / "marked.min.js"
    script = f"""
const fs = require("fs");
const vm = require("vm");
const marked = require({json.dumps(str(marked_path))});
const services = {{}};
const window = {{
  marked,
  DOMPurify: {{ sanitize: (html) => html }},
  CyreneUI: {{ register: (name, service) => (services[name] = service) }},
}};
vm.runInNewContext(fs.readFileSync({json.dumps(str(renderer_path))}, "utf8"), {{ window }});
const fenced = ["```text", ":::details literal", "inside code", ":::", "```", "Visible."].join("\\n");
const unfinished = ["Before.", ":::card Pending", "hidden so far"].join("\\n");
const embeddedFence = [
  "Before.",
  ":::details Complete",
  "```text",
  ":::",
  "```",
  "still hidden",
  ":::",
  "After.",
].join("\\n");
process.stdout.write(JSON.stringify({{
  fenced: services.markdown.renderRich(fenced, {{ interactive: false }}),
  unfinished: services.markdown.renderRich(unfinished, {{ interactive: false }}),
  embeddedFence: services.markdown.renderRich(embeddedFence, {{ interactive: false }}),
  finalEmbeddedFence: services.markdown.renderRich(embeddedFence),
}}));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert ":::details literal" in result["fenced"]
    assert "inside code" in result["fenced"]
    assert "Visible." in result["fenced"]
    assert "Before." in result["unfinished"]
    assert "Pending" not in result["unfinished"]
    assert "hidden so far" not in result["unfinished"]
    assert "Before." in result["embeddedFence"]
    assert "After." in result["embeddedFence"]
    assert "still hidden" not in result["embeddedFence"]
    assert '<details class="wbc-fold" data-wbc-source="' in result["finalEmbeddedFence"]
    assert '<summary>Complete</summary>' in result["finalEmbeddedFence"]
    assert "still hidden" in result["finalEmbeddedFence"]
    assert ":::" in result["finalEmbeddedFence"]
    assert "After." in result["finalEmbeddedFence"]


def test_workbench_live_reply_disables_interactive_markdown_until_done():
    root = Path(__file__).resolve().parent.parent
    chat = workbench_chat_source()
    styles = workbench_style_source()
    contract = (root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_renderer" / "load_contract.py").read_text(encoding="utf-8")

    assistant_message = chat.split("function WbcAssistantMessage", 1)[1].split("var WBC_HEARTBEAT_STALL_MS", 1)[0]
    assert "WBC_LIVE_MARKDOWN_INTERVAL_MS = 120" in chat
    assert "!live || !!liveRuntime.streamDone" in assistant_message
    assert "wbcRenderMarkdown(renderedText, { interactive: false })" in assistant_message
    assert "wbcRenderMarkdown(renderedText)" in assistant_message
    assert "return flush ? source : renderedText;" in chat
    assert ".wbc-fold > summary:focus-visible" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert ":::details Title" in contract
    assert ":::card Title" in contract


def test_workbench_live_reply_preserves_message_identity_when_saved():
    runtime = frontend_module_source("features/chat/file-resources.jsx")
    conversation = frontend_module_source("features/chat/conversation.jsx")
    binder = "function wbcBindSavedReplyRenderKey" + runtime.split(
        "function wbcBindSavedReplyRenderKey", 1
    )[1].split("// ---------------------------------------------------------------------------", 1)[0]
    script = f"""
eval({json.dumps(binder)});
const source = [
  {{ id: "activity", role: "assistant", content: "", trace: [{{ kind: "tool" }}] }},
  {{ id: "intermediate", role: "assistant", content: "progress", intermediate: true }},
  {{ id: "final", role: "assistant", content: "done" }}
];
const bound = wbcBindSavedReplyRenderKey(source, "reply_stream_request-1");
process.stdout.write(JSON.stringify({{ source, bound }}));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    result = json.loads(completed.stdout)

    assert "replyRenderKey" not in result["source"][2]
    assert result["bound"][0]["id"] == "activity"
    assert "replyRenderKey" not in result["bound"][1]
    assert result["bound"][2]["replyRenderKey"] == "reply_stream_request-1"
    assert 'replyRenderKey: replyRenderKey' in runtime
    assert 'runtimes[chatId] && runtimes[chatId].replyRenderKey' in runtime
    assert 'var renderKey = String(msg && (msg.replyRenderKey || msg.id) || "");' in conversation
    assert '<WbcThreadItem key={String(runtime.replyRenderKey)}>' in conversation
    live_timeline = conversation.split(
        "function wbcRenderConversationTimeline(", 1
    )[1].split("function WbcMain(", 1)[0]
    assert "<WbcAssistantMessage" in live_timeline
    assert "<WbcLiveMessage" not in live_timeline
    assert "{renderedTimeline}" in conversation
    assert "{renderedHistory}" not in conversation.split(
        'className="wbc-thread"', 1
    )[1].split("<WbcConversationNavigator", 1)[0]


def test_workbench_agent_transport_notice_has_structured_event_and_durable_bubble():
    root = Path(__file__).resolve().parent.parent
    chat = workbench_chat_source()
    styles = workbench_style_source()
    events = (root / "src" / "cyrene" / "agents" / "events.py").read_text(encoding="utf-8")
    route = workbench_chat_route_source()

    assert '"notification.created"' in events
    assert '"notification.created": { handler: "onNotification"' in chat
    assert "function WbcAgentNotification" in chat
    assert "msg.runtimeNotification || msg.notificationCard" in chat
    assert '"notificationCard": True' in route
    assert ".wbc-agent-notification" in styles
    assert 'role="status" aria-live="polite"' in chat


def test_custom_model_connection_protocol_does_not_select_a_brand_icon():
    source = (Path(__file__).resolve().parent.parent / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "settings-model-configuration.jsx").read_text(encoding="utf-8")

    start = source.index("function connectionProviderIcon(connection)")
    end = source.index("function connectionProviderMark(connection)", start)
    resolver = source[start:end]
    assert "provider_preset" in resolver
    assert "presetIcons[preset]" in resolver
    assert 'isLocalConnection(connection) ? "onnx"' in resolver
    assert "connectionAdapter(connection)" not in resolver


def test_quick_chat_renders_live_and_durable_agent_notifications():
    root = Path(__file__).resolve().parent.parent
    chat = workbench_chat_source()
    quick = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-quick-chat.jsx").read_text(encoding="utf-8")

    assert "AgentNotification: WbcAgentNotification" in chat
    assert "RuntimeTranscript: WbcRuntimeTranscript" in chat
    assert "m.notificationCard" in quick
    assert "chatService.AgentNotification" in quick
    assert "chatService.RuntimeTranscript" in quick
    assert "runtime && runtime.notifications && runtime.notifications.length" in quick


def test_compact_chat_surfaces_share_capability_driven_agent_runtime_ui():
    root = Path(__file__).resolve().parent.parent
    chat = workbench_chat_source()
    quick = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-quick-chat.jsx").read_text(encoding="utf-8")

    assert "function wbcReduceDetachedRuntime" in chat
    assert chat.count("<WbcRuntimeTranscript runtime=") >= 2
    assert "onToolStarted:" in chat
    assert "onArtifactEvent:" in chat
    assert "WorkbenchChatModel.answerAgentRequest(current, questionId, response)" in chat
    assert "WorkbenchChatModel.answerAgentRequest(current.id, questionId, response)" in chat
    assert "QuestionPrompt: WbcQuestionPrompt" in chat
    assert "pendingAgentRequest" in quick
    assert "model.answerAgentRequest(chatId, questionId, response)" in quick
    assert "chatService.QuestionPrompt" in quick


def test_workbench_library_list_uses_explicit_pagination():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")

    assert "var PAGE_SIZE = 120" in source
    assert "function loadMore()" in source
    assert "data.items.length < data.total" in source
    assert "library.loadMore" in source


def test_workbench_library_table_header_context_menu_controls_visible_columns():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")
    columns = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "features" / "knowledge" / "library-columns.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.css").read_text(encoding="utf-8")
    english = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "i18n" / "catalog-en.jsx").read_text(encoding="utf-8")
    chinese = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "i18n" / "catalog-zh.jsx").read_text(encoding="utf-8")

    assert 'onContextMenu: function (event)' in columns.split("function LibraryTableHead", 1)[1].split("function useLibraryColumns", 1)[0]
    assert "props.onColumnContextMenu(event)" in columns
    assert 'window.localStorage.getItem("cyrene.library.tableColumns")' in columns
    assert 'window.localStorage.setItem("cyrene.library.tableColumns"' in columns
    assert 'role: "menuitemcheckbox"' in columns
    assert "libraryTableGridTemplate(visible)" in source
    assert "visibleColumns: visibleColumns" in source
    assert "cursor: context-menu;" in styles
    assert ".wb-lib-column-menu" in styles
    assert '"library.displayedColumns": "Displayed columns"' in english
    assert '"library.displayedColumns": "显示的列"' in chinese


def test_workbench_library_table_columns_fill_space_without_wrapping_dates():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "features" / "knowledge" / "library-columns.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.css").read_text(encoding="utf-8")

    widths = source.split("var LIBRARY_TABLE_COLUMN_WIDTHS = {", 1)[1].split("};", 1)[0]
    assert 'title: "minmax(0, 4fr)"' in widths
    assert 'added: "minmax(118px, .85fr)"' in widths
    assert 'tags: "minmax(0, .9fr)"' in widths
    assert ".wb-lib-table-grid > * { min-width: 0; }" in styles
    assert ".wb-lib-table-head > span, .wb-lib-row > span" in styles
    assert "text-overflow: ellipsis; white-space: nowrap;" in styles


def test_workbench_library_does_not_merge_stale_detail_when_switching_items():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-library.jsx").read_text(
        encoding="utf-8"
    )

    assert (
        "detail && String(detail.id) === String(selectedId) ? detail : null"
        in source
    )
    select = source.split("function select(id)", 1)[1].split("function replaceItem", 1)[0]
    assert 'setDetail(null);' in select
    assert 'setSelectedId(String(id));' in select
    assert select.index("setDetail(null)") < select.index("setSelectedId(String(id))")


def test_packaged_electron_preserves_explicit_runtime_path_overrides():
    root = Path(__file__).resolve().parent.parent
    source = (root / "electron" / "main.js").read_text(encoding="utf-8")

    assert "process.env.CYRENE_USER_DATA_DIR || getCyreneUserDataDir()" in source
    assert "process.env.CYRENE_CACHE_DIR || getCyreneCacheDir()" in source
    assert "process.env.CYRENE_TEMP_DIR || getCyreneTempDir()" in source


def test_workbench_composer_uploads_files_pasted_from_clipboard():
    chat = "\n".join((
        frontend_module_source("features/chat/composer-attachments.jsx"),
        frontend_module_source("features/chat/composer.jsx"),
    ))

    assert "onPaste={onPaste}" in chat
    assert "clipboard.files" in chat
    assert "clipboard.items" in chat
    assert 'item.kind === "file" ? item.getAsFile() : null' in chat
    assert "if (!files.length) return" in chat
    assert "event.preventDefault();" in chat
    assert "addFiles(files)" in chat


def test_settings_codex_quota_uses_the_shared_duration_parser():
    root = Path(__file__).resolve().parent.parent
    model = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-model.jsx").read_text(
        encoding="utf-8"
    )
    settings = workbench_settings_source()

    assert "function codexQuotaWindows(limits)" in model
    assert "function codexPlanLabel(account, limits)" in model
    assert 'if (normalized === "prolite") return "pro 5x"' in model
    assert 'if (normalized === "pro") return "pro 20x"' in model
    assert 'durationMins === 300' in model
    assert 'durationMins >= 10080' in model
    assert "codexQuotaModel.codexQuotaWindows(codexQuota.limits)" in settings
    assert 't("settings.codexQuotaPlan"' in settings


def test_external_agent_probe_and_install_help_are_actionable():
    settings = frontend_module_source("features/settings/plugin-center-agents.jsx")
    i18n = workbench_i18n_source()

    assert 'if (onChanged) onChanged(next)' in settings
    agent_settings = frontend_module_source("features/settings/plugin-center-agents.jsx")
    assert 'stateLabel("Runtime"' in agent_settings
    assert 'source: { type: "inline", manifest: parsed }' in settings
    assert 'request("/api/plugin-center/agent/install-proposals"' in settings
    assert 'install-proposals/" + encodeURIComponent(id) + "/confirm' in settings
    assert 't("settings.agentProposalConfirmInstall"' in settings
    assert '"settings.agentRuntime.pendingTransport": "Not tested"' in i18n
    assert '"settings.agentRuntime.pendingTransport": "尚未测试"' in i18n
    assert i18n.count('"settings.agentProposalSubmitTitle"') == 2


def test_agent_picker_rebinds_empty_chat_and_confirms_new_nonempty_chat():
    source = workbench_chat_source()
    styles = workbench_style_source()

    assert "function handleSwitchAgent(binding)" in source
    assert 'model.createChatWithBinding(projectId, "", binding)' in source
    assert "model.updateChatAgent(activeChat.id, binding)" in source
    assert 'wbcT("workbenchChat.agentNewChatTitle"' in source
    assert "onSwitchAgent={agentsAvailable ? handleSwitchAgent : null}" in source
    agent_panel = source.split('{modelPanel === "agents"', 1)[1].split('{modelPanel === "models"', 1)[0]
    assert "locked: false" in agent_panel
    assert 'canPick: availability.state === "available"' in agent_panel
    assert "function pickAgentBinding(binding)" in source
    assert "if (chat && chat.id && onSwitchAgent) onSwitchAgent(binding);" in source
    assert agent_panel.count("pickAgentBinding(binding);") == 2
    assert "if (agentBindingLocked && onSwitchAgent)" not in agent_panel
    assert 'className="wbc-agent-menu-dot"' in source
    assert ".wbc-agent-menu-row:focus-visible" in styles
    assert "border: 0;" in styles.split(".wbc-agent-menu-row {", 1)[1].split("}", 1)[0]


def test_agent_owned_model_picker_uses_acp_config_options():
    source = workbench_chat_source()
    i18n = workbench_i18n_source()

    assert '"/agent-config-options"' in source
    assert "agentModelConfig.options" in source
    assert "model.updateAgentConfigValues" in source
    assert '<span className="wbc-model-button-name">{modelName}</span>' in source
    assert 'wbcT("workbenchChat.modelSource.agentManaged"' not in source.split('className="wbc-model-button-name"', 1)[1].split("</button>", 1)[0]
    assert '"workbenchChat.agentModelUnavailable": "此 Agent 未提供可选模型。"' in i18n
    assert 'String(option.category || "") === "thought_level"' in source
    assert 'var effectiveReasoningEffort = agentManagedModels ? agentReasoningValue : reasoningEffort;' in source
    assert 'reasoningEffort: agentManagedModels ? "" : reasoningEffort' in source
    assert 'model.updateAgentConfigValues(chatId, { [agentReasoningConfig.id]: effort })' in source
    assert 'model.updateChatPreferences(chatId, { reasoningEffort: effort })' in source


def test_external_agent_usage_is_shared_with_the_reply_finalizer():
    route = workbench_chat_route_source()
    services = workbench_runtime_source()

    assert "external = ExternalTurnProjection()" in route
    assert "projection=self.external" in route
    assert "projection: ExternalTurnProjection" in services
    assert "effective_usage.update(request.projection.usage)" in services


def test_agent_diagnostics_note_is_localized_from_a_stable_code():
    root = Path(__file__).resolve().parent.parent
    settings = workbench_settings_source()
    i18n = workbench_i18n_source()
    runtime = (root / "src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py").read_text(encoding="utf-8")

    assert 'diagnostics.noteCode || diagnostics.note_code || diagnostics.reason' in settings
    assert 't("settings.agentDiagnosticsStartsOnDemand"' in settings
    assert 'diagnostics.note || ""' not in settings
    assert '"noteCode": "starts_on_demand"' in runtime
    assert "ACP stdio 会在需要时启动；诊断信息不会暴露进程环境变量或凭据。" in i18n


def test_agent_settings_show_composer_usability_and_localized_reasons():
    settings = frontend_module_source("features/settings/plugin-center-agents.jsx")
    i18n = workbench_i18n_source()

    assert "function agentUsability(agent, t)" in settings
    assert 't("settings.agentComposerAvailability"' in settings
    assert 't("settings.agentUsability.available"' in settings
    assert 'runtime === "crashed" || runtime === "error"' in settings
    assert 'auth === "failed" || auth === "expired"' in settings
    assert i18n.count('"settings.agentUsability.available"') == 2
    assert '"settings.agentUsability.available": "可在 Composer 中使用"' in i18n


def test_agent_network_errors_render_full_actionable_details():
    source = workbench_chat_source()
    styles = workbench_style_source()
    i18n = workbench_i18n_source()

    assert "function wbcAgentErrorPresentation(detail, failureKind)" in source
    assert "invalid peer certificate" in source
    assert 'className="wbc-error-detail"' in source
    assert 'wbcT("workbenchChat.error.copyDetail"' in source
    assert "white-space: pre-wrap;" in styles.split(".wbc-error-detail {", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: anywhere;" in styles.split(".wbc-error-detail {", 1)[1].split("}", 1)[0]
    assert i18n.count('"workbenchChat.error.tlsTitle"') == 2
    assert i18n.count('"workbenchChat.error.copyDetail"') == 2


def test_phase1_stream_is_rendered_as_a_distinct_execution_card():
    source = workbench_chat_source()
    styles = workbench_style_source()
    translations = workbench_i18n_source()

    assert 'String(item.llmPhase || "") === "phase1"' in source
    assert 'kind: "phase1"' in source
    assert 'wbcT("workbenchChat.phase1Card"' in source
    assert "String(last.llmPhase) === eventPhase" in source
    assert '"workbenchChat.phase1Card": "正在理解指令"' in translations
    assert '"workbenchChat.phase1Understood": "已理解用户需求"' in translations
    assert "border-radius: 16px" in styles


# ---------------------------------------------------------------------------
# :::chart declarative interactive blocks
# ---------------------------------------------------------------------------

def _run_chart_services_js(expression: str):
    """Run JS against the chart spec + mount modules (no marked/DOM needed)."""
    root = Path(__file__).resolve().parent.parent
    spec_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "chart" / "spec.jsx"
    mount_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "chart" / "mount.jsx"
    script = f"""
const fs = require("fs");
const services = {{}};
const window = {{
  CyreneUI: {{ register: (name, service) => (services[name] = service) }},
  document: undefined,
}};
eval(fs.readFileSync({json.dumps(str(spec_path))}, "utf8"));
eval(fs.readFileSync({json.dumps(str(mount_path))}, "utf8"));
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _run_chart_render_js(expression: str):
    """Run JS against the real marked + chart spec + renderer modules."""
    root = Path(__file__).resolve().parent.parent
    renderer_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx"
    spec_path = root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "chart" / "spec.jsx"
    marked_path = root / "src" / "cyrene" / "workbench" / "webui" / "static" / "app" / "marked.min.js"
    script = f"""
const fs = require("fs");
const marked = require({json.dumps(str(marked_path))});
const services = {{}};
const window = {{
  marked,
  DOMPurify: {{ sanitize: (html) => html }},
  CyreneUI: {{ register: (name, service) => (services[name] = service) }},
}};
eval(fs.readFileSync({json.dumps(str(spec_path))}, "utf8"));
eval(fs.readFileSync({json.dumps(str(renderer_path))}, "utf8"));
window.CyreneUI.markdown = services.markdown;
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


_CHART_SAMPLE = [
    "Visible intro.",
    "",
    ":::chart line",
    "x: [-4,-3,-2,-1,0,1,2,3,4]",
    'y-binds: "a*x*x + b*x + c"',
    "controls:",
    "  - param: a",
    "    range: [-5, 5]",
    "    step: 0.1",
    "    default: 1",
    "  - param: b",
    "    range: [-10, 10]",
    "    step: 0.5",
    "    default: 0",
    "  - param: c",
    "    range: [-10, 10]",
    "    step: 0.5",
    "    default: -4",
    "options:",
    "  title: y = a·x² + b·x + c",
    ":::",  # noqa: E501
    "",
    "Visible conclusion.",
]


def test_markdown_chart_block_renders_payload_and_streaming_strips_it():
    result = _run_chart_render_js(
        "(() => {\n"
        "  const src = " + json.dumps("\n".join(_CHART_SAMPLE)) + ";\n"
        "  const finalHtml = services.markdown.renderRich(src);\n"
        "  const streamingHtml = services.markdown.renderRich(src, { interactive: false });\n"
        "  const match = finalHtml.match(/data-wbc-chart=\"([^\"]*)\"/);\n"
        "  const payload = match ? JSON.parse(match[1].replace(/&quot;/g, '\"')) : null;\n"
        "  return {\n"
        "    finalHtml,\n"
        "    streamingHtml,\n"
        "    payload,\n"
        "    hasSpecFallback: finalHtml.indexOf('<pre class=\"wbc-chart-spec\">') >= 0,\n"
        "  };\n"
        "})()"
    )

    assert 'class="wbc-chart"' in result["finalHtml"]
    assert result["hasSpecFallback"]
    assert result["payload"]["type"] == "line"
    assert result["payload"]["x"] == [-4, -3, -2, -1, 0, 1, 2, 3, 4]
    assert result["payload"]["y-binds"] == "a*x*x + b*x + c"
    assert result["payload"]["controls"][0] == {"param": "a", "range": [-5, 5], "step": 0.1, "default": 1}
    assert result["payload"]["options"]["title"] == "y = a·x² + b·x + c"
    # The interactive block is hidden while streaming; prose survives.
    assert "Visible intro." in result["streamingHtml"]
    assert "Visible conclusion." in result["streamingHtml"]
    assert "wbc-chart" not in result["streamingHtml"]
    assert "a*x*x" not in result["streamingHtml"]


def test_markdown_chart_invalid_spec_falls_back_to_spec_text():
    result = _run_chart_render_js(
        "(() => {\n"
        "  const src = ['Bad chart:', '', ':::chart pie', 'x: [1,2,3]', 'y: [4,5,6]', ':::'].join('\\n');\n"
        "  const html = services.markdown.renderRich(src);\n"
        "  return { html };\n"
        "})()"
    )
    assert 'class="wbc-chart wbc-chart-error"' in result["html"]
    assert 'data-wbc-chart-error="unsupported chart type: pie"' in result["html"]
    assert '<pre class="wbc-chart-spec">x: [1,2,3]' in result["html"]


def test_chart_spec_validation_rejects_injection_and_bad_shapes():
    result = _run_chart_services_js(
        "(() => {\n"
        "  const chartSpec = services['chart-spec'];\n"
        "  const good = 'x: [1,2,3]\\ny: [4,5,6]\\n';\n"
        "  function outcome(body, type) {\n"
        "    try { chartSpec.buildPayload(body, type || 'line'); return true; }\n"
        "    catch (e) { return false; }\n"
        "  }\n"
        "  const controlA = 'controls:\\n  - param: a\\n    range: [0, 1]\\n    step: 0.1\\n    default: 0\\n';\n"
        "  return {\n"
        "    good: outcome(good),\n"
        "    unknown_type: outcome(good, 'pie'),\n"
        "    html_in_binds: outcome('x: [1,2]\\ny-binds: \\\"a + <script>alert(1)</script>\\\"\\n' + controlA),\n"
        "    eval_in_binds: outcome('x: [1,2]\\ny-binds: \\\"eval(1)\\\"\\n' + controlA),\n"
        "    unbound_variable: outcome('x: [1,2]\\ny-binds: \\\"a + b\\\"\\n' + controlA),\n"
        "    x_y_mismatch: outcome('x: [1,2,3]\\ny: [4]\\n'),\n"
        "    missing_y: outcome('x: [1,2,3]\\n'),\n"
        "    bad_range: outcome('x: [1,2]\\ny: [3,4]\\ncontrols:\\n  - param: a\\n    range: [5, 5]\\n    step: 0.1\\n    default: 1\\n'),\n"
        "    reserved_x: outcome('x: [1,2]\\ny: [3,4]\\ncontrols:\\n  - param: x\\n    range: [0, 1]\\n    step: 0.1\\n    default: 0\\n'),\n"
        "  };\n"
        "})()"
    )
    assert result["good"] is True
    assert result["unknown_type"] is False
    assert result["html_in_binds"] is False
    assert result["eval_in_binds"] is False
    assert result["unbound_variable"] is False
    assert result["x_y_mismatch"] is False
    assert result["missing_y"] is False
    assert result["bad_range"] is False
    assert result["reserved_x"] is False


def test_chart_spec_payload_size_is_capped():
    result = _run_chart_services_js(
        "(() => {\n"
        "  const chartSpec = services['chart-spec'];\n"
        "  const big = Array.from({ length: 4000 }, (_, i) => i);\n"
        "  const body = 'x: [' + big.join(',') + ']\\ny: [' + big.join(',') + ']\\n';\n"
        "  try { chartSpec.buildPayload(body, 'line'); return { accepted: true }; }\n"
        "  catch (e) { return { accepted: false, message: e.message }; }\n"
        "})()"
    )
    assert result["accepted"] is False
    assert "32 KB" in result["message"]


def test_chart_binds_evaluator_is_whitelisted_arithmetic():
    result = _run_chart_services_js(
        "(() => {\n"
        "  const { compileExpr } = services['chart-spec'];\n"
        "  const quad = compileExpr('a*x*x + b*x + c');\n"
        "  const outcomes = {\n"
        "    quad: [0, 1, 2, 3, 4].map(x => quad.evaluate({ a: 1, b: 0, c: -4, x })),\n"
        "    parentheses: compileExpr('(a+b)*(c-d)').evaluate({ a: 1, b: 2, c: 5, d: 3 }),\n"
        "    negation: compileExpr('-x*x + 2').evaluate({ x: 3 }),\n"
        "    division: compileExpr('(x+1)/2').evaluate({ x: 3 }),\n"
        "    decimal: compileExpr('x*0.5').evaluate({ x: 3 }),\n"
        "  };\n"
        "  const expressions = [\n"
        "    \"eval('x')\", 'x ** 2', 'x < 2', 'a && b', 'Math.max(x)',\n"
        "    'x + 1 = 2', 'a ? b : c', 'import os', 'x; alert(1)',\n"
        "  ];\n"
        "  const rejections = {};\n"
        "  expressions.forEach(expr => {\n"
        "    try { compileExpr(expr); rejections[expr] = false; }\n"
        "    catch (e) { rejections[expr] = true; }\n"
        "  });\n"
        "  return { outcomes, rejections };\n"
        "})()"
    )
    assert result["outcomes"]["quad"] == [-4, -3, 0, 5, 12]
    assert result["outcomes"]["parentheses"] == 6
    assert result["outcomes"]["negation"] == -7
    assert result["outcomes"]["division"] == 2
    assert result["outcomes"]["decimal"] == 1.5
    assert all(result["rejections"].values())


def test_chart_mount_builds_option_and_recomputes_series():
    result = _run_chart_services_js(
        "(() => {\n"
        "  const chart = services['chart'];\n"
        "  const payload = {\n"
        "    type: 'line',\n"
        "    x: [0, 1, 2],\n"
        "    'y-binds': 'a*x + 1',\n"
        "    controls: [{ param: 'a', range: [0, 2], step: 1, default: 2 }],\n"
        "    options: { title: 'T', grid: true },\n"
        "  };\n"
        "  const optionA3 = chart.buildOption(payload, { a: 3 });\n"
        "  const optionA2 = chart.buildOption(payload, { a: 2 });\n"
        "  const scatter = chart.buildOption({ type: 'scatter', x: [0, 1], y: [5, 6] }, {});\n"
        "  return {\n"
        "    withA3: optionA3.series[0].data,\n"
        "    withA2: optionA2.series[0].data,\n"
        "    type: optionA3.series[0].type,\n"
        "    title: optionA3.title.text,\n"
        "    scatterData: scatter.series[0].data,\n"
        "    scatterType: scatter.series[0].type,\n"
        "    xAxis: optionA3.xAxis.data,\n"
        "  };\n"
        "})()"
    )
    assert result["withA3"] == [1, 4, 7]
    assert result["withA2"] == [1, 3, 5]
    assert result["type"] == "line"
    assert result["title"] == "T"
    assert result["scatterData"] == [[0, 5], [1, 6]]
    assert result["scatterType"] == "scatter"
    assert result["xAxis"] == [0, 1, 2]


def test_workbench_assistant_message_mounts_charts_and_contract_teaches_chart():
    root = Path(__file__).resolve().parent.parent
    chat = workbench_chat_source()
    styles = workbench_style_source()
    contract = (root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_renderer" / "load_contract.py").read_text(encoding="utf-8")
    index_html = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")
    build_script = (root / "src" / "cyrene" / "workbench" / "webui" / "build-jsx.mjs").read_text(encoding="utf-8")

    assistant_message = chat.split("function WbcAssistantMessage", 1)[1].split("function WbcHeartbeat", 1)[0]
    assert 'window.CyreneUI.chart' in assistant_message
    assert 'chartService.mount(bodyRef.current, {' in assistant_message
    assert 'messageId: String(msg && msg.id || "")' in assistant_message
    assert 'chatId: String(chatId || ""),' in assistant_message
    assert 'chartService.dispose(bodyRef.current)' in assistant_message
    assert 'ref={bodyRef}' in chat
    assert ".wbc-chart-canvas" in styles
    assert ".wbc-chart-controls" in styles
    assert ".wbc-chart-spec" in styles
    assert ":::chart line" in contract
    assert "y-binds" in contract
    assert '<script type="module" src="compiled/app.js?v=0.9.0-beta4">' in index_html
    entry_html = (root / "src/cyrene/workbench/webui/frontend/entry/app.jsx").read_text(encoding="utf-8")
    assert 'import "../shared/chart/spec.jsx"' in entry_html
    assert 'import "../shared/chart/mount.jsx"' in entry_html
    assert "echarts.min.js" in index_html
    assert "node_modules/echarts/dist/echarts.min.js" in build_script


def test_markdown_button_block_renders_payload_with_readable_fallback():
    result = _run_chart_render_js(
        "(() => {\n"
        "  const src = [\n"
        "    'Intro.',\n"
        "    '',\n"
        "    ':::button',\n"
        "    'label: 开始翻译',\n"
        "    'action_id: translate_start',\n"
        "    'style: primary',\n"
        "    'mode: local',\n"
        "    'value: zh->en',\n"
        "    ':::',\n"
        "    '',\n"
        "    'Outro.',\n"
        "  ].join('\\n');\n"
        "  const finalHtml = services.markdown.renderRich(src);\n"
        "  const streamingHtml = services.markdown.renderRich(src, { interactive: false });\n"
        "  const match = finalHtml.match(/data-wbc-button=\"([^\"]*)\"/);\n"
        "  const payload = match ? JSON.parse(match[1].replace(/&quot;/g, '\"').replace(/&gt;/g, '>').replace(/&lt;/g, '<')) : null;\n"
        "  return { finalHtml, streamingHtml, payload };\n"
        "})()"
    )
    assert 'class="wbc-button"' in result["finalHtml"]
    # The fallback line is readable: "[按钮: 开始翻译]".
    assert "[Button: 开始翻译]" in result["finalHtml"]
    assert result["payload"]["action_id"] == "translate_start"
    assert result["payload"]["label"] == "开始翻译"
    assert result["payload"]["style"] == "primary"
    assert result["payload"]["mode"] == "local"
    assert result["payload"]["value"] == "zh->en"
    assert result["payload"]["disabled"] is False
    # Streaming keeps prose but hides the interactive block.
    assert "Intro." in result["streamingHtml"]
    assert "Outro." in result["streamingHtml"]
    assert "wbc-button" not in result["streamingHtml"]


def test_markdown_button_label_is_escaped_in_fallback():
    result = _run_chart_render_js(
        "(() => {\n"
        "  const src = [\n"
        "    ':::button',\n"
        "    'label: <img src=x onerror=alert(1)>',\n"
        "    'action_id: poke',\n"
        "    ':::',\n"
        "  ].join('\\n');\n"
        "  return { html: services.markdown.renderRich(src) };\n"
        "})()"
    )
    assert "<img" not in result["html"]
    assert "&lt;img" in result["html"]
    assert "onerror=alert" in result["html"]  # present only as escaped text


def test_markdown_button_invalid_spec_falls_back_to_raw_spec():
    result = _run_chart_render_js(
        "(() => {\n"
        "  const bad = [':::button', 'label: X', 'action_id: UPPER_CASE', ':::'].join('\\n');\n"
        "  const missing = [':::button', 'action_id: ok_id', ':::'].join('\\n');\n"
        "  return {\n"
        "    bad: services.markdown.renderRich(bad),\n"
        "    missing: services.markdown.renderRich(missing),\n"
        "  };\n"
        "})()"
    )
    assert 'class="wbc-button wbc-button-error"' in result["bad"]
    assert 'data-wbc-button-error="action_id must match [a-z0-9_]+"' in result["bad"]
    assert '<pre class="wbc-button-spec">label: X' in result["bad"]
    assert 'data-wbc-button-error="button requires a non-empty label"' in result["missing"]


def test_button_spec_validation_rejects_bad_fields():
    result = _run_chart_services_js(
        "(() => {\n"
        "  const chartSpec = services['chart-spec'];\n"
        "  function outcome(body) {\n"
        "    try { chartSpec.buildButtonPayload(body); return true; }\n"
        "    catch (e) { return false; }\n"
        "  }\n"
        "  const base = 'label: Go\\naction_id: go\\n';\n"
        "  return {\n"
        "    good: outcome(base),\n"
        "    long_action_id: outcome('label: Go\\naction_id: ' + 'a'.repeat(33) + '\\n'),\n"
        "    bad_style: outcome(base + 'style: giant\\n'),\n"
        "    bad_mode: outcome(base + 'mode: remote\\n'),\n"
        "    long_value: outcome(base + 'value: ' + 'v'.repeat(257) + '\\n'),\n"
        "    bad_disabled: outcome(base + 'disabled: yes\\n'),\n"
        "    empty_label: outcome('action_id: go\\n'),\n"
        "  };\n"
        "})()"
    )
    assert result["good"] is True
    assert result["long_action_id"] is False
    assert result["bad_style"] is False
    assert result["bad_mode"] is False
    assert result["long_value"] is False
    assert result["bad_disabled"] is False
    assert result["empty_label"] is False


def test_button_click_protocol_payload_shape_and_event_ids():
    result = _run_chart_services_js(
        "(() => {\n"
        "  const chart = services['chart'];\n"
        "  const ids = [chart.eventId(), chart.eventId(), chart.eventId()];\n"
        "  const unique = new Set(ids).size === ids.length;\n"
        "  return { ids, unique };\n"
        "})()"
    )
    assert result["unique"] is True


def test_workbench_button_wiring_and_protocol_surface():
    root = Path(__file__).resolve().parent.parent
    chat = workbench_chat_source()
    mount = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "chart" / "mount.jsx").read_text(encoding="utf-8")
    renderer = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx").read_text(encoding="utf-8")
    styles = workbench_style_source()
    contract = (root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_renderer" / "load_contract.py").read_text(encoding="utf-8")

    assert 'chatId: String(chatId || "")' in chat
    assert 'messageId: String(msg && msg.id || "")' in chat
    assert 'type: "block_actions"' in mount
    assert 'action_id: spec.action_id' in mount
    assert 'block_id: blockId' in mount
    assert 'event_id: eventId()' in mount
    assert 'message_id: (context && context.messageId) || ""' in mount
    assert 'new root.CustomEvent("wbc:block-action"' in mount
    assert 'fired = true;' in mount
    assert "(?:details|card|chart|button|actions|grid)" in renderer
    assert 'buildButtonPayload' in renderer
    assert ":::button" in contract
    assert "action_id" in contract
    assert ".wbc-button-btn" in styles
    assert ".wbc-button-btn--primary" in styles
    assert ".wbc-button-btn--danger" in styles
    assert ".wbc-button-triggered" in styles


def test_markdown_actions_container_renders_button_row_and_streaming_strips():
    result = _run_chart_render_js(
        "(() => {\n"
        "  const src = [\n"
        "    'Intro.',\n"
        "    '',\n"
        "    ':::actions',\n"
        "    '  :::button',\n"
        "    '  label: 开始翻译',\n"
        "    '  action_id: translate_start',\n"
        "    '  style: primary',\n"
        "    '  mode: model',\n"
        "    '  value: zh->en',\n"
        "    '  :::',\n"
        "    '  :::button',\n"
        "    '  label: 清空输入',\n"
        "    '  action_id: input_clear',\n"
        "    '  mode: local',\n"
        "    '  :::',\n"
        "    ':::',\n"
        "    '',\n"
        "    'Outro.',\n"
        "  ].join('\\n');\n"
        "  const finalHtml = services.markdown.renderRich(src);\n"
        "  const streamingHtml = services.markdown.renderRich(src, { interactive: false });\n"
        "  return { finalHtml, streamingHtml };\n"
        "})()"
    )
    assert '<div class="wbc-actions" data-wbc-source="' in result["finalHtml"]
    assert result["finalHtml"].count('data-wbc-button="') == 2
    assert 'data-wbc-button-action_id="translate_start"' not in result["finalHtml"]
    assert "Intro." in result["streamingHtml"]
    assert "Outro." in result["streamingHtml"]
    assert "wbc-actions" not in result["streamingHtml"]
    assert "translate_start" not in result["streamingHtml"]


def test_markdown_grid_container_renders_columns_with_card_and_chart():
    result = _run_chart_render_js(
        "(() => {\n"
        "  const src = [\n"
        "    ':::grid cols: 2',\n"
        "    '  :::card 输入区',\n"
        "    '  源文本',\n"
        "    '  :::',\n"
        "    '  :::chart line',\n"
        "    '  x: [1,2,3]',\n"
        "    '  y: [4,5,6]',\n"
        "    '  :::',\n"
        "    ':::',\n"
        "  ].join('\\n');\n"
        "  return { html: services.markdown.renderRich(src) };\n"
        "})()"
    )
    assert 'class="wbc-grid"' in result["html"]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in result["html"]
    assert 'class="wbc-card"' in result["html"]
    assert 'data-wbc-chart="' in result["html"]


def test_markdown_containers_reject_invalid_nesting_and_depth():
    result = _run_chart_render_js(
        "(() => {\n"
        "  const actionWithCard = [':::actions', '  :::card 错', 'x', '  :::', ':::'].join('\\n');\n"
        "  const gridWithButton = [':::grid', '  :::button', '  label: X', '  action_id: x', '  :::', ':::'].join('\\n');\n"
        "  const containerInContainer = [':::actions', '  :::actions', '  :::', ':::'].join('\\n');\n"
        "  const emptyActions = [':::actions', ':::'].join('\\n');\n"
        "  const plain = ['Before.', '', ':::actions', '  :::button', '  label: A', '  action_id: a', '  :::', ':::', '', 'After.'].join('\\n');\n"
        "  return {\n"
        "    actionWithCard: services.markdown.renderRich(actionWithCard),\n"
        "    gridWithButton: services.markdown.renderRich(gridWithButton),\n"
        "    containerInContainer: services.markdown.renderRich(containerInContainer),\n"
        "    emptyActions: services.markdown.renderRich(emptyActions),\n"
        "    plain: services.markdown.renderRich(plain),\n"
        "  };\n"
        "})()"
    )
    assert 'class="wbc-actions wbc-actions-error"' in result["actionWithCard"]
    assert 'class="wbc-grid wbc-grid-error"' in result["gridWithButton"]
    assert 'class="wbc-actions wbc-actions-error"' in result["containerInContainer"]
    assert 'class="wbc-actions wbc-actions-error"' in result["emptyActions"]
    assert '<div class="wbc-actions" data-wbc-source="' in result["plain"]
    assert "Before." in result["plain"] and "After." in result["plain"]


def test_workbench_actions_grid_wiring_and_contract_rules():
    root = Path(__file__).resolve().parent.parent
    renderer = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx").read_text(encoding="utf-8")
    styles = workbench_style_source()
    contract = (root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_renderer" / "load_contract.py").read_text(encoding="utf-8")

    assert "findClosingLine" in renderer
    assert "depth" in renderer
    assert "collectChildTypes" in renderer
    assert '"actions"' in renderer
    assert '"grid"' in renderer
    assert 'token.blockType === "actions"' in renderer
    assert 'token.blockType === "grid"' in renderer
    assert ".wbc-actions" in styles
    assert ".wbc-grid" in styles
    assert "@media (max-width: 720px)" in styles
    assert ":::actions" in contract
    assert ":::grid cols: 2" in contract
    assert "cannot contain containers" in contract


def test_workbench_button_model_mode_forwards_to_runtime_endpoint():
    root = Path(__file__).resolve().parent.parent
    mount = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "chart" / "mount.jsx").read_text(encoding="utf-8")
    chat_route = workbench_chat_route_source()
    service = (root / "src" / "cyrene" / "workbench" / "chat" / "chat_application.py").read_text(encoding="utf-8")
    schemas = (root / "src" / "cyrene" / "workbench" / "http" / "schemas.py").read_text(encoding="utf-8")
    wbc = workbench_chat_source()

    assert 'spec.mode === "model"' in mount
    assert '/actions"' in mount
    assert 'actionId: spec.action_id' in mount
    assert 'chatId: String(chatId || "")' in wbc
    assert 'chatId={String(context.chat && context.chat.id || "")}' in wbc
    assert "@router.post(\"/api/workbench/chats/{chat_id}/actions\")" in chat_route or '@router.post("/api/workbench/chats/{chat_id}/actions")' in chat_route
    assert "action_duplicate" in chat_route
    assert "disable_button_block" in chat_route
    assert "def disable_button_block" in service
    assert "def has_button_block" in service
    assert "class ChatActionBody" in schemas
    assert "actionId" in schemas


def test_unsupported_file_viewer_uses_centered_accessible_empty_state():
    chat = workbench_chat_source()
    styles = workbench_style_source()
    i18n = workbench_i18n_source()

    unsupported = chat.split('className="wbc-viewer-unsupported"', 1)[1].split("</div>\n    );", 1)[0]
    assert 'role="status"' in chat
    assert "<WbcFileVisual file={file}" in unsupported
    assert "WBC_ICONS.openExternal" in unsupported
    assert 'className="wb-btn tonal wbc-viewer-unsupported-action"' in unsupported
    assert ".wbc-viewer-unsupported {" in styles
    assert "place-items: center" in styles
    assert "flex: 1 1 auto" in styles
    assert ".wbc-viewer-unsupported-action:focus-visible" in styles
    assert i18n.count('"workbenchChat.viewerUnsupportedHint"') == 2


def test_settings_controls_share_memory_floating_material():
    styles = workbench_style_source()
    source = workbench_settings_source()

    assert "--wb-settings-control-bg: color-mix(in srgb, var(--wb-card-bg) 88%, var(--wb-surface))" in styles
    assert "--wb-settings-control-radius: 12px" in styles
    assert "html[data-theme] .settings-overlay :where(.wb-input, .wb-select, .wb-textarea, .wb-btn)" in styles
    assert "border: var(--wb-settings-control-border)" in styles
    assert "background: var(--wb-settings-control-bg)" in styles
    assert ".settings-overlay .wb-seg" in styles
    assert ".settings-overlay .wb-toggle" in styles
    assert "html[data-theme] .settings-overlay .wb-btn.primary" in styles
    assert "background: color-mix(in srgb, var(--wb-accent) 26%, var(--wb-settings-control-bg))" in styles
    assert "html[data-theme] .settings-overlay .wb-btn.primary {\n  border-color:" in styles
    assert "background: color-mix(in srgb, var(--wb-accent) 26%, var(--wb-settings-control-bg));\n  color: var(--wb-text)" in styles
    assert "border-color: color-mix(in srgb, var(--wb-accent) 62%, var(--wb-line-2))" in styles
    assert ".workbench-integrated-rail-primary-action," in styles
    assert ".wb-btn.primary:not(.danger)" in styles
    assert "background: color-mix(in srgb, var(--wb-accent) 26%, var(--wb-floating-control-bg))" in styles
    assert ".settings-overlay .wb-path-display" in styles
    assert "width: min(460px, 48vw)" in styles
    assert ".settings-overlay .wb-export-format .wb-seg" in styles
    assert "function escapeHtml(" not in source
    assert ".remote-pairing-result" not in styles
    assert ".remote-bundle" not in styles
    assert ".wb-accent-custom-button" not in styles
    assert ".wb-skill-detail-card" not in styles
    assert ".settings-overlay .wb-export-session-select" not in styles


def test_board_scroll_canvas_reaches_behind_floating_rail_gutter():
    source = frontend_module_source("features/chat/conversation-board.jsx")
    styles = workbench_style_source()

    columns_rule = styles.split(
        ".workbench-grid.integrated-sidebars.is-conversation-board .wb-board-columns {", 1
    )[1].split("}", 1)[0]
    scroll_rule = styles.split(
        ".workbench-grid.integrated-sidebars.is-conversation-board .wb-board-scroll {", 1
    )[1].split("}", 1)[0]
    floating_rail_rule = styles.rsplit(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail {", 1
    )[1].split("}", 1)[0]

    assert "--wb-conversation-board-canvas-gutter: 22px;" in styles
    assert 'html[data-theme="dark"] .workbench-grid.integrated-sidebars.is-conversation-board {' in styles
    assert "--wb-floating-rail-tint: #24323f;" in styles
    assert "padding-left: calc(var(--wb-conversation-board-rail-reserve) + var(--wb-conversation-board-canvas-gutter));" in columns_rule
    assert "margin-inline: calc(0px - var(--wb-conversation-board-canvas-gutter));" in scroll_rule
    assert "background: var(--wb-floating-rail-bg);" in floating_rail_rule
    assert "opacity:" not in floating_rail_rule
    assert "function handleBoardWheel(event)" in source
    assert 'target.closest(".wb-board-column-body")' in source
    assert "columnBody.scrollTop < maxColumnTop - 1" in source
    assert "viewport.scrollWidth - viewport.clientWidth" in source
    assert "viewport.scrollLeft = nextLeft;" in source
    assert 'className="wb-board-scroll" onWheel={handleBoardWheel}' in source


def test_conversation_status_preview_controls_share_floating_material():
    styles = workbench_style_source()

    option_rule = styles.split(
        ".wbc-conversation-status-preview-option {", 1
    )[1].split("}", 1)[0]
    reply_rule = styles.split(
        ".wbc-conversation-status-preview-reply {", 1
    )[1].split("}", 1)[0]
    input_rule = styles.split(
        ".wbc-conversation-status-preview-reply input {", 1
    )[1].split("}", 1)[0]
    send_rule = styles.split(
        ".wbc-conversation-status-preview-reply button {", 1
    )[1].split("}", 1)[0]

    assert "min-height: 38px;" in option_rule
    assert "var(--wb-floating-control-border" in option_rule
    assert "var(--wb-floating-control-bg" in option_rule
    assert "var(--wb-floating-control-shadow" in option_rule
    assert "grid-template-columns: minmax(0, 1fr) 38px;" in reply_rule
    assert "height: 38px;" in input_rule
    assert "border-radius: var(--wb-floating-control-radius, 12px);" in input_rule
    assert "width: 38px;" in send_rule
    assert "height: 38px;" in send_rule
    assert "color: var(--wb-text);" in send_rule
    assert "color-mix(in srgb, var(--wb-accent) 26%" in send_rule


def test_performance_mode_disables_renderer_effects_and_is_boot_persistent():
    frontend = Path("src/cyrene/workbench/webui/frontend")
    overlay = workbench_settings_source()
    workbench = workbench_shell_source()
    css = workbench_style_source()
    index = (frontend / "index.html").read_text(encoding="utf-8")

    assert 'JSON.stringify({ performance_mode: next })' in overlay
    assert 'settings.performanceMode' in overlay
    appearance = overlay.split("function AppearancePanel(p)", 1)[1].split("// ── Capabilities Panel ──", 1)[0]
    general = (frontend / "features" / "settings" / "general.jsx").read_text(encoding="utf-8")
    assert 'settings.performanceMode' in appearance
    assert 'settings.performanceMode' not in general
    assert 'dataset.performanceMode = next ? "on" : "off"' in workbench
    assert 'localStorage.getItem("cyrene-performance-mode") === "1"' in index
    assert 'html[data-performance-mode="on"] *' in css
    assert "backdrop-filter: none !important" in css
    assert "box-shadow: none !important" in css
    assert "animation: none !important" in css
    assert "transition: none !important" in css


def _run_workbench_durable_trace_js(expression: str):
    source = frontend_module_source("features/chat/runtime-timeline.jsx")
    durable_source = "var WBC_DURABLE_TRACE_FIELDS" + source.split(
        "var WBC_DURABLE_TRACE_FIELDS", 1
    )[1].split("export {", 1)[0]
    script = f"""
eval({json.dumps(durable_source)});
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def test_workbench_durable_trace_reuses_client_assembled_activities():
    result = _run_workbench_durable_trace_js(
        """
(() => {
  const runtime = {
    activities: [
      {
        id: "activity_1",
        progress: [
          { kind: "tool", toolCallId: "c1", text: "Bash", preview: "run", status: "running", failed: false, input: { secret: "x" } },
          { kind: "tool", toolCallId: "c2", text: "Read", preview: "a.md", status: "completed", failed: false },
        ],
      },
      {
        id: "activity_2",
        progress: [
          { kind: "tool", toolCallId: "c3", text: "WebFetch", preview: "", status: "completed", failed: true },
        ],
      },
    ],
  };
  const saved = [
    { id: "activity_reasoning_x", activityCard: true, trace: [] },
    { id: "msg1", activityCard: true, trace: [{ tool: "old" }] },
    { id: "msg2", activityCard: true, trace: [{ tool: "old" }] },
  ];
  return wbcDurableTracePayload("chat1", runtime, saved);
})()
"""
    )
    assert result["messageIds"] == ["msg1", "msg2"]
    assert len(result["traces"]) == 2
    first = result["traces"][0]
    assert len(first) == 2
    assert first[0]["status"] == "completed"  # straggler running entry settled
    assert first[0]["toolCallId"] == "c1"
    assert first[0]["text"] == "Bash"
    assert "input" not in first[0]  # payload fields scrubbed
    assert "secret" not in json.dumps(result)
    assert result["traces"][1][0]["failed"] is True


def test_workbench_durable_trace_skips_when_boundaries_diverge():
    result = _run_workbench_durable_trace_js(
        """
(() => {
  const withTools = [
    { id: "a", progress: [{ kind: "tool", toolCallId: "c1", text: "Bash" }] },
    { id: "b", progress: [{ kind: "tool", toolCallId: "c2", text: "Read" }] },
  ];
  const noTools = [{ id: "c", progress: [] }];
  const savedOne = [{ id: "m1", activityCard: true, trace: [{ tool: "old" }] }];
  return {
    mismatch: wbcDurableTracePayload("chat", { activities: withTools }, savedOne),
    noActivity: wbcDurableTracePayload("chat", { activities: noTools }, savedOne),
    noSaved: wbcDurableTracePayload("chat", { activities: withTools }, []),
    emptyRuntime: wbcDurableTracePayload("chat", null, savedOne),
  };
})()
"""
    )
    assert result == {
        "mismatch": None,
        "noActivity": None,
        "noSaved": None,
        "emptyRuntime": None,
    }


def test_media_settings_hide_comfyui_and_use_progressive_autosave_ui():
    media = frontend_module_source("features/settings/media.jsx")
    settings_index = frontend_module_source("shared/settings-index.jsx")
    settings_overlay = workbench_settings_source()
    build = Path("src/cyrene/workbench/webui/build-jsx.mjs").read_text(encoding="utf-8")
    controls = (Path("src/cyrene/workbench/webui/frontend/features/settings/controls.css")).read_text(
        encoding="utf-8"
    )
    zh = Path("src/cyrene/workbench/webui/frontend/shared/i18n/catalog-zh.jsx").read_text(encoding="utf-8")

    visible_order = media.split("var MEDIA_PROVIDER_ORDER =", 1)[1].split(";", 1)[0]
    visible_defaults = media.split("var MEDIA_KIND_PROVIDERS =", 1)[1].split(";", 1)[0]
    panel = media.split("function MediaPanel(p)", 1)[1].split("export {", 1)[0]

    assert "comfyui" not in visible_order.lower()
    assert "comfyui" not in visible_defaults.lower()
    assert 'return provider === "comfyui" ? provider' in media
    assert 'id: "setting-media-comfyui"' not in settings_index
    assert 'className: "wb-media-provider"' in media
    assert 'className: "wb-media-advanced"' in media
    assert 'className: "wb-media-runtime"' in media
    assert '{ id: "media", labelKey: "settings.mediaGeneration", icon: "photo-video" }' in settings_overlay
    assert "'photo-video.svg'" in build
    assert 'icon: "sparkles"' not in settings_overlay
    assert 'kind: "image"' in media
    assert 'kind: "video"' in media
    assert 'kind: "music"' in media
    assert "function MediaModelSelect(p)" in media
    assert "function MediaDisclosure(p)" in media
    assert '"aria-expanded": open' in media
    assert 'className: p.bodyClassName + (open ? " open" : "")' in media
    assert 'React.createElement("select"' in media
    assert 'value: MEDIA_CUSTOM_MODEL_VALUE' in media
    assert '"/api/settings/media/providers/" + encodeURIComponent(id) + "/models"' in media
    assert 'p.loadProviderModels(p.id, false)' in media
    assert "function scheduleMediaSave(queue, delay)" in media
    assert "scheduleMediaSave(store.saveQueue, immediate ? 0 : 600)" in media
    assert "failMediaSave(queue, error, version)" in media
    assert "if (saveQueue.available && saveQueue.dirty) persistMediaSave(saveQueue, true)" in media
    assert "expectedRevision" in media
    assert "onClick: media.save" not in panel
    assert 't("settings.mediaSave")' not in panel
    assert panel.index("MediaDefaultsSection(") < panel.index('id: "setting-media-providers"')
    assert panel.index('id: "setting-media-providers"') < panel.index("MediaRuntimeSection(")
    assert ".wb-media-default-grid" in controls
    assert ".wb-media-provider-summary:focus-visible" in controls
    advanced_rule = controls.split(".wb-media-advanced {", 1)[1].split("}", 1)[0]
    assert "margin-top: 0;" in advanced_rule
    assert "border-top: 0;" in advanced_rule
    assert "border-top: 1px" not in advanced_rule
    assert '"settings.mediaFlowDeliverHint": "完成后，图片、视频或音乐会自动加入发起生成的对话。"' in zh
    assert "wake_agent" not in zh.split('"settings.mediaGeneration"', 1)[1].split(
        '"settings.capabilitiesSubtitle"', 1
    )[0]
    runtime_summary = controls.split(
        ".wb-media-advanced-summary,\n.wb-media-runtime-summary {", 1
    )[1].split("}", 1)[0]
    assert (
        "grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr) 10px;"
        in runtime_summary
    )
    assert ".wb-media-runtime-summary::after" in controls
    assert "grid-template-rows: 0fr;" in controls
    assert "grid-template-rows: 1fr;" in controls
    assert "grid-template-rows 0.22s ease-out" in controls
    assert ".wb-media-provider-body-content" in controls
    assert ".wb-media-advanced-body-content" in controls
    assert "@media (prefers-reduced-motion: reduce)" in controls


def test_media_model_dropdown_filters_kinds_and_preserves_custom_selection():
    media = frontend_module_source("features/settings/media.jsx")
    helper = "function mediaModelEntries" + media.split(
        "function mediaModelEntries", 1
    )[1].split("function mediaModelOption", 1)[0]
    catalog = {
        "models": [
            {
                "id": "image-recommended",
                "label": "Image Recommended",
                "kinds": ["image"],
                "recommended": True,
                "source": "catalog",
                "available": True,
            },
            {
                "id": "video-only",
                "kinds": ["video"],
                "source": "live",
                "verified": True,
            },
        ]
    }
    script = f"""
eval({json.dumps(helper)});
process.stdout.write(JSON.stringify(mediaModelEntries(
  {json.dumps(catalog)},
  "image",
  "custom-image-model"
)));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    entries = json.loads(completed.stdout)

    assert [entry["id"] for entry in entries] == [
        "custom-image-model",
        "image-recommended",
    ]
    assert entries[0]["source"] == "configured"
    assert entries[1]["accountAvailable"] is True
    assert entries[1]["recommended"] is True
