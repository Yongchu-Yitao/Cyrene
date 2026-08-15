import json
import re
import subprocess
from pathlib import Path


def test_external_agent_frontend_consumes_unified_dynamic_events_and_viewers():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    route_source = (root / "src" / "route" / "workbench" / "chat.py").read_text(encoding="utf-8")
    settings = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")

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
    assert 'tool_entry["preview"] = str(visible_summary)' in route_source
    assert "matching_tool_indices = [" in route_source
    assert "open_tool_indices = [" in route_source
    assert 'tool_entry["preview"] = existing_preview' in route_source
    assert 'tool_entry["reasoningOffset"] = len("".join(external_reasoning_parts))' in route_source
    assert "if external_trace or external_reasoning_parts:" in route_source
    assert 'del external_trace[:-40]' in route_source
    assert '"trace": external_trace[-40:]' in route_source
    assert 'tool_status in {"failed", "error", "failure", "expired", "cancelled"}' in route_source
    assert 'notifyAgentCatalogChanged("installed")' in settings
    assert '"workbenchChat.attachmentType.audio": "音频"' in i18n


def test_external_agent_context_uses_report_then_transcript_fallback_copy():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    assert 'data.compositionSource === "agent_report"' in source
    assert 'data.compositionSource === "public_transcript"' in source
    assert "workbenchChat.ctxBlocks.agentReportDetailed" in source
    assert 'className: "wbc-context-source-info"' in source
    assert 'className: "wbc-context-source-popover"' in source
    assert 'usesAgentReport && React.createElement("p"' not in source
    assert '"workbenchChat.ctxBlocks.externalEstimate": "以下为 Cyrene 可见的对话内容估算。' in i18n
    assert 'className: "wbc-ctx-layer-row"' in source
    assert '"workbenchChat.ctxBlocks.layer.agent_other": "External Agent"' in i18n
    assert '"workbenchChat.ctxBlocks.layer.agent_other": "外部 Agent"' in i18n


def test_external_agent_context_hides_cyrene_only_inbox_and_tool_packages():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    assert 'var externalAgent = !!wbcChatAgent(chat) && !wbcIsBuiltinAgent(wbcChatAgent(chat));' in source
    assert '!externalAgent && <WbcInboxCard' in source
    assert '!externalAgent && <section className="workbench-side-section" aria-label={wbcT("workbenchChat.usedToolPackages"' in source


def test_external_agent_permission_labels_are_localized_by_protocol_semantics():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    helper = "function wbcPermissionOptionLabel(" + source.split("function wbcPermissionOptionLabel(", 1)[1].split("function wbcPermissionQuestionText", 1)[0]
    script = f'''\nfunction wbcT(key, fallback) {{\n  const zh = {{\n    "workbenchChat.permissionOnce": "允许一次",\n    "workbenchChat.permissionAlways": "始终允许",\n    "workbenchChat.permissionSession": "本次会话允许",\n    "workbenchChat.reject": "拒绝"\n  }};\n  return zh[key] || fallback;\n}}\nfunction wbcQuestionOptionValue(option) {{ return String(option.optionId || option.id || option.label || ""); }}\n{helper}\nconst labels = [\n  wbcPermissionOptionLabel({{optionId:"allow_once", label:"Allow once"}}, 0, 3),\n  wbcPermissionOptionLabel({{optionId:"allow_always", label:"Always allow"}}, 1, 3),\n  wbcPermissionOptionLabel({{optionId:"reject", label:"Reject"}}, 2, 3)\n];\nprocess.stdout.write(JSON.stringify(labels));\n'''
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == ["允许一次", "始终允许", "拒绝"]
    assert '"workbenchChat.permissionAlways": "始终允许"' in i18n


def test_system_default_model_description_is_localized_without_rewriting_custom_copy():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    friendly = "function wbcFriendlyModelName(" + source.split("function wbcFriendlyModelName(", 1)[1].split("function wbcLocalizedModelDescription", 1)[0]
    localized = "function wbcLocalizedModelDescription(" + source.split("function wbcLocalizedModelDescription(", 1)[1].split("function wbcNormalizePermissionMode", 1)[0]
    script = f'''\nfunction wbcT(key, fallback, params) {{\n  if (key === "workbenchChat.modelProviderDefault") return params.provider + " 默认配置";\n  return fallback;\n}}\n{friendly}\n{localized}\nprocess.stdout.write(JSON.stringify([\n  wbcLocalizedModelDescription({{model:"deepseek-v4-flash", desc:"DeepSeek default"}}),\n  wbcLocalizedModelDescription({{model:"deepseek-v4-flash", desc:"团队默认模型"}})\n]));\n'''
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == ["DeepSeek 默认配置", "团队默认模型"]
    assert '"workbenchChat.modelProviderDefault": "{provider} 默认配置"' in i18n


def test_chat_files_merge_message_attachments_and_nested_agent_output():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    helper_source = "function wbcChatArtifactFiles(" + source.split(
        "function wbcChatArtifactFiles(", 1
    )[1].split("function wbcArtifactFileKey", 1)[0]
    script = f"""
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
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    helper_source = "function wbcChatDeliveredArtifacts(" + source.split(
        "function wbcChatDeliveredArtifacts(", 1
    )[1].split("function wbcArtifactFileKey", 1)[0]
    script = f"""
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


def test_chat_file_type_labels_do_not_treat_text_formats_as_word_documents():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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


def test_file_view_kind_recognizes_project_images_without_mime_metadata():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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
  {{ contentType: 'image/jpeg' }}
];
process.stdout.write(JSON.stringify(files.map(wbcFileViewKind)));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    assert json.loads(completed.stdout) == ["image", "image", "image", "image"]


def test_office_files_use_lazy_browser_renderers_with_resource_limits():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    build_script = (root / "src" / "webui" / "build-jsx.mjs").read_text(
        encoding="utf-8"
    )
    package = json.loads(
        (root / "src" / "webui" / "package.json").read_text(encoding="utf-8")
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
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    visual = source.split("function wbcAttachmentVisual(file)", 1)[1].split(
        "function wbcAttachmentTypeLabel", 1
    )[0]

    assert "var kind = wbcAttachmentVisualKind(file);" in visual
    assert "shared.toneForKind(kind)" in visual
    assert "shared.iconForKind(kind)" in visual


def test_library_file_visual_exposes_kind_based_rendering_without_reclassification():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(
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
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    assert 'tabs.push({ id: "files", label: wbcT("workbenchChat.files", "Files") });' in source
    assert 'if (artifactItems.length) tabs.push({ id: "artifacts", label: wbcT("workbenchChat.artifacts", "Artifacts") });' in source
    assert 'files: hasFiles ? String(fileItems.length) : ""' in source
    assert 'artifacts: artifactItems.length ? String(artifactItems.length) : ""' in source


def test_send_file_prompt_registers_user_requested_save_locations_as_artifacts():
    from cyrene.agent.prompts import _MAIN_DELIVERY_FILE_PROMPT, workspace_scope_block
    from cyrene.tooling.native_definitions import get_native_tool_def

    tool = get_native_tool_def("send_file")["function"]
    description = tool["description"]
    path_description = tool["parameters"]["properties"]["path"]["description"]

    assert "file you actually created" in description
    assert "never guess paths or merely print one" in description
    assert "specific save location" in description
    assert "does not save or move files" in description
    assert "authorized user-requested locations" in path_description
    assert "real file you created" in _MAIN_DELIVERY_FILE_PROMPT
    assert "printing or guessing a path is not delivery" in _MAIN_DELIVERY_FILE_PROMPT
    assert "specific save location" in _MAIN_DELIVERY_FILE_PROMPT
    assert "registered as an artifact" in _MAIN_DELIVERY_FILE_PROMPT
    assert "save the file there first" in workspace_scope_block()


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
    )[1].split("function wbcNormalizeChatGroups", 1)[0]
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
    )[1].split("function wbcConversationTrackRawStatus", 1)[0]
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
    assert "function handleConversationHorizontalWheel(event)" in source
    assert 'onWheel={handleConversationHorizontalWheel}' in source
    assert "horizontalSessionWheelRef.current" in source
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
    assert '<WbcContextBlockList data={contextBlocks} compact={false} />' in context_source
    assert 'className: "wbc-context-detail"' in source
    assert '<WbcInboxCard liveView={inboxView} hideTitle={true} />' in context_source
    assert "usedToolPackages.length === 0" in context_source
    assert "workbenchChat.noUsedToolPackages" in context_source
    tool_package_heading = context_source.index('className="wbc-context-empty-head wbc-tool-pack-head"')
    tool_package_condition = context_source.index("usedToolPackages.length === 0 ? (")
    assert tool_package_heading < tool_package_condition
    assert 'className="workbench-side-section wbc-context-stats"' in context_source
    assert "WbcSortableCardStack" not in context_source
    context_css = styles.split(".wbc-context-sections {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column;" in context_css
    first_tool_package_css = styles.split(
        ".wbc-context-sections .wbc-tool-pack-head + .wbc-tool-pack-row {",
        1,
    )[1].split("}", 1)[0]
    assert "border-top: 0;" in first_tool_package_css


def test_builtin_agent_in_overview_uses_plain_text_without_builtin_suffix():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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


def test_workbench_chat_search_and_custom_background_composer_stay_distinct():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    search_css = styles.split(".wbc-search input {", 1)[1].split("}", 1)[0]
    search_focus_css = styles.split(".wbc-search input:focus {", 1)[1].split("}", 1)[0]
    composer_css = styles.split(".wbc-composer-box {", 1)[1].split("}", 1)[0]
    assert "border: 1px solid" in search_css
    assert "box-shadow: none;" in search_css
    assert "border-color:" in search_focus_css
    assert "border: 1px solid color-mix(in srgb, var(--wb-line-2) 64%, transparent);" in composer_css
    assert "background: color-mix(in srgb, var(--wb-card-bg) 72%, transparent);" in composer_css
    assert "backdrop-filter: blur(18px) saturate(120%) contrast(102%);" in composer_css
    assert ".wbc-composer-box:focus-within {" not in styles


def test_main_chat_composer_uses_a_solid_canvas_and_readable_input_card():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    stage_css = styles.split("\n.wbc-thread-stage {", 1)[1].split("}", 1)[0]
    dock_css = styles.split("\n.wbc-main > .wbc-composer {", 1)[1].split("}", 1)[0]
    page_css = styles.split(".wbc-page {", 1)[1].split("}", 1)[0]
    topbar_css = styles.split(".workbench-topbar {", 1)[1].split("}", 1)[0]
    input_css = styles.split(".wbc-composer-box {", 1)[1].split("}", 1)[0]
    scroll_css = styles.split(".wbc-scroll-to-bottom {", 1)[1].split("}", 1)[0]

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
    assert "background: color-mix(in srgb, var(--wb-card-bg) 72%, transparent);" in input_css
    assert "border: 1px solid color-mix(in srgb, var(--wb-line-2) 64%, transparent);" in input_css
    assert "backdrop-filter: blur(18px) saturate(120%) contrast(102%);" in input_css
    assert "border-radius: 14px;" in input_css
    assert "padding: 10px 12px 8px;" in input_css
    assert "bottom: calc(100% + 8px);" in scroll_css
    assert "position: relative;" in input_css
    assert "topOverlay={showScrollToBottom ? (" in source
    assert "{topOverlay}" in source


def test_split_chat_composers_align_with_the_floating_workspace_rail():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

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

    assert "width: calc(100% - var(--wbc-card-gutter));" in rail_css
    assert "margin: var(--wbc-card-top-inset) 0 var(--wbc-card-gutter) var(--wbc-card-gutter);" in rail_css
    assert "padding-bottom: 12px;" in composer_css
    assert "padding: 10px 14px 14px !important;" in split_main_composer_css
    assert "padding: 10px 14px 14px !important;" in pane_split_main_composer_css


def test_hidden_chat_sidebar_slightly_widens_and_centers_the_conversation_lane():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    page_css = styles.split(".wbc-page {", 1)[1].split("}", 1)[0]
    hidden_css = styles.split(".wbc-page.wbc-side-hidden {", 1)[1].split("}", 1)[0]
    stage_css = styles.split("\n.wbc-thread-stage {", 1)[1].split("}", 1)[0]
    dock_css = styles.split("\n.wbc-main > .wbc-composer {", 1)[1].split("}", 1)[0]

    assert "--wbc-reclaimed-side-width: 0px;" in page_css
    assert "--wbc-conversation-shift: 0px;" in page_css
    assert "--wbc-side-track-width: var(--wb-right-w, 350px);" in page_css
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
    hidden_side_css = styles.split(".wbc-page.wbc-side-hidden > .wbc-side {", 1)[1].split("}", 1)[0]
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


def test_global_topbar_is_solid_and_conversation_header_panel_is_removed():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

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
    mode_toggle_css = styles.split(".workbench-rail-mode-toggle {", 1)[1].split("}", 1)[0]
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
    assert "width: calc(100% - var(--wbc-card-gutter));" in shared_chat_rail_css
    assert "margin: var(--wbc-card-top-inset) 0 var(--wbc-card-gutter) var(--wbc-card-gutter);" in shared_chat_rail_css
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
    assert "flex: 0 0 60px;" in mode_toggle_css
    assert "width: 60px;" in mode_toggle_css
    assert 'className="wbc-project-new-chat"' in source
    assert 'className="wbc-chat-nav-title"' not in source
    assert 'className="wbc-rail-filter"' not in source
    assert 'className="wbc-rail-toolbar"' not in source
    assert 'className="wbc-module-nav"' not in source
    assert 'aria-label={wbcT("workbenchChat.newChat"' in source
    rail_markup = source.split('<div className="wbc-rail-glass">', 1)[1].split(
        "{menuId &&", 1
    )[0]
    assert 'workbenchChat.railTitle' not in rail_markup
    assert '<span>{wbcT("workbenchChat.newChat"' not in rail_markup
    assert "height: var(--wb-rail-toolbar-control-size, 32px);" in search_input_css
    assert "border-right: 0;" in rail_css
    assert ".wbc-rail::after {" not in styles
    assert "position: relative;" in chat_list_css
    assert "z-index: 21;" in chat_list_css
    assert "padding: calc(var(--wbc-card-top-inset) + var(--wbc-rail-content-inset)) 10px 18px;" in chat_list_css


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
    card_css = styles.split("\n.wbc-side-card {", 1)[1].split("}", 1)[0]
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
    assert "padding: var(--wbc-card-top-inset) var(--wbc-card-gutter) var(--wbc-card-gutter);" in side_css
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


def test_workbench_chat_single_card_uses_independent_hidden_gutter_resize_handles():
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
    assert 'getPropertyValue("--wbc-main-min-width")' in dynamic_max
    assert 'child.classList.contains("wbc-pane-layout")' not in dynamic_max
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
    assert "padding: var(--wbc-card-top-inset) 0 var(--wbc-card-gutter) var(--wbc-card-gutter);" in pane_layout_css
    assert "padding: var(--wbc-card-top-inset) var(--wbc-card-gutter) var(--wbc-card-gutter);" in side_css


def test_workbench_deleted_chat_closes_every_split_reference():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    close_deleted = source.split("  function closeDeletedChatSplits(chatId) {", 1)[1].split(
        "\n  function movePaneCardOtherSide", 1
    )[0]
    assert "setPaneLayoutsByChat(function (current)" in close_deleted
    assert 'card.kind === "chat" && String(card.payload || "") === deletedChatId' in close_deleted
    assert "if (!left.length && right.length)" in close_deleted
    assert "setResourceSplitByChat(function (current)" in close_deleted
    assert 'resource.type === "chat" && String(resource.payload || "") === deletedChatId' in close_deleted
    assert "delete paneLayoutRestoreRef.current[cardId]" in close_deleted
    delete_success = source.split("model.deleteChat(chatId).then(function () {", 1)[1].split(
        "}).catch(function (err)", 1
    )[0]
    assert "closeDeletedChatSplits(chatId);" in delete_success


def test_workbench_pane_drag_ghost_preserves_current_viewport_and_handle_hotspot():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    pane_drag = source.split("  function handlePaneCardDragStart(event, cardId) {", 1)[1].split(
        "\n  function handlePaneCardDragEnd", 1
    )[0]
    assert "var conversationViewport = wbcCaptureConversationViewport(card);" in pane_drag
    assert "var clonedCard = wbcClonePaneWithLiveState(card);" in pane_drag
    assert "var panePreview = clonedCard.clone;" in pane_drag
    assert 'panePreview.classList.add("wbc-pane-card-drag-surface")' in pane_drag
    assert 'ghost.className = "wbc-pane-card-drag-ghost";' in pane_drag
    assert "clonedCard.restoreViewport();" in pane_drag
    assert "wbcRestoreConversationViewport(panePreview, conversationViewport);" in pane_drag
    assert "var handleGrabX" in pane_drag
    assert "var handleGrabY" in pane_drag
    assert "var capturedClientX = Number(dragHandle.dataset.wbcDragClientX);" in pane_drag
    assert "var capturedHandleX = Number(dragHandle.dataset.wbcDragHandleX);" in pane_drag
    assert "movePaneCardGhost({ clientX: initialClientX, clientY: initialClientY });" in pane_drag
    assert "var grabX = (handleRect.left - cardRect.left) + handleGrabX;" in pane_drag
    assert "var grabY = (handleRect.top - cardRect.top) + handleGrabY;" in pane_drag
    assert 'ghost.style.transform = "translate3d("' in pane_drag
    assert 'ghost.classList.add("releasing")' in pane_drag
    assert "function retirePaneCardGhost()" in pane_drag
    assert 'ghost.style.visibility = "hidden";' in pane_drag
    assert "typeof ghost.animate === \"function\"" in pane_drag
    assert "Promise.resolve(fadeAnimation.finished)" in pane_drag
    assert "window.requestIdleCallback(detachGhost, { timeout: 300 });" in pane_drag
    assert 'wbcBuildRailCardDragPreview(railCard, "wbc-pane-card-rail-drag-card")' in pane_drag
    assert 'moveEvent.dataTransfer.dropEffect = "move";' in pane_drag
    assert 'moveEvent.preventDefault();' in pane_drag
    assert 'ghost.classList.toggle("rail-card", overRail);' in pane_drag
    assert 'railCard.classList.toggle("dragging", overRail);' in pane_drag
    assert "function pointerIsOverMatchingRailCard(clientX, clientY)" in pane_drag
    assert 'railCard.classList.toggle("wbc-split-return-target", !!overMatchingCard);' in pane_drag
    assert 'railPreview.style.left = (grabX - railGrabX) + "px";' in pane_drag
    assert 'ghost.style.width = (overRail ? railPreviewWidth' not in pane_drag
    assert "function finishPaneCardGhost(dropEvent)" in pane_drag
    assert "if (droppedOnMatchingCard && draggedChatId) closePaneCard(cardId);" in pane_drag
    assert "wbcHideNativeDragImage(transfer);" in pane_drag
    assert "event.dataTransfer.setDragImage(" not in pane_drag
    assert ".wbc-pane-card-drag-ghost" in styles
    assert "will-change: transform, opacity;" in styles
    ghost_css = styles.split(".wbc-pane-card-drag-ghost {", 1)[1].split("}", 1)[0]
    assert "opacity 72ms cubic-bezier(.4, 0, 1, 1)" in ghost_css
    assert ".wbc-pane-card-drag-ghost.rail-card" in styles
    assert ".wbc-pane-card-rail-drag-card" in styles
    assert ".wbc-pane-card-drag-surface" in styles
    assert ".wbc-rail .wbc-chat-card.wbc-split-return-target" in styles
    grip = source.split("function WbcSplitGripBar(", 1)[1].split("function WbcSplitPickerMenu", 1)[0]
    assert "function captureDragPointer(event)" in grip
    assert "onPointerDown={captureDragPointer}" in grip
    pane_drop = source.split("  function handlePaneDrop(event, targetCardId, edge) {", 1)[1].split(
        "\n  function handleSideLayerDragOver", 1
    )[0]
    assert pane_drop.index("paneCardDragImageCleanupRef.current()") < pane_drop.index("updatePaneLayout(function (current)")


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
    assert 'if (hasFiles) tabs.push({ id: "files"' in tabs
    assert 'if (artifactItems.length) tabs.push({ id: "artifacts"' in tabs
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
    assert "var flush = false;" in source
    assert 'activeTab === "changes" || activeTab === "side-agents"' not in source
    list_css = styles.split(".wbc-side-agent-list {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column;" in list_css
    assert "gap: 6px;" in list_css


def test_workbench_two_level_card_panes_share_drag_resize_and_menu_contracts():
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

    helper = "var WBC_PANE_CARD_SEQUENCE" + source.split(
        "var WBC_PANE_CARD_SEQUENCE", 1
    )[1].split("function wbcSetSplitDrag", 1)[0]
    script = f"""
global.localStorage = {{ getItem: () => null }};
eval({json.dumps(helper)});
const base = wbcDefaultPaneLayout("main");
const fileA = wbcPaneCard("file", {{ path: "a.md" }}, {{ id: "file:a" }});
const fileB = wbcPaneCard("file", {{ path: "b.md" }}, {{ id: "file:b" }});
const chatB = wbcPaneCard("chat", "chat-b", {{ id: "chat:chat-b" }});
const duplicateMain = wbcPaneCard("chat", "main", {{ freshInstance: true, ownerChatId: "main" }});
const verticalSameChat = wbcPlacePaneCard(base, duplicateMain, "left", "top", "", "chat:main");
const horizontal = wbcPlacePaneCard(base, fileA, "right", "replace", "", "");
const vertical = wbcPlacePaneCard(horizontal, chatB, "right", "bottom", "", "file:a");
const replaced = wbcPlacePaneCard(vertical, fileB, "right", "top", "", "file:a");
const swapped = wbcPlacePaneCard(replaced, chatB, "right", "top", "chat:chat-b", "file:b");
const files = wbcPlacePaneCard(horizontal, fileB, "left", "replace", "", "chat:main");
process.stdout.write(JSON.stringify({{
  base: base.left.map(card => card.id),
  horizontal: horizontal.right.map(card => card.id),
  vertical: vertical.right.map(card => card.id),
  replaced: replaced.right.map(card => card.id),
  swapped: swapped.right.map(card => card.id),
  files: [files.left[0].kind, files.right[0].kind],
  maxDepth: Math.max(replaced.left.length, replaced.right.length),
  duplicateChat: {{
    count: verticalSameChat.left.length,
    distinctIds: verticalSameChat.left[0].id !== verticalSameChat.left[1].id,
    payloads: verticalSameChat.left.map(card => card.payload),
  }},
}}));
"""
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    contract = json.loads(result.stdout)
    assert contract == {
        "base": ["chat:main"],
        "horizontal": ["file:a"],
        "vertical": ["file:a", "chat:chat-b"],
        "replaced": ["file:b", "chat:chat-b"],
        "swapped": ["chat:chat-b", "file:b"],
        "files": ["file", "file"],
        "maxDepth": 2,
        "duplicateChat": {
            "count": 2,
            "distinctIds": True,
            "payloads": ["main", "main"],
        },
    }

    assert "function WbcPaneCardFrame" in source
    assert "var showActiveConversationGrip = paneCardCount > 1 || !sideVisible;" in source
    assert "grip = showActiveConversationGrip ? <WbcSplitGripBar" in source
    assert "function WbcPaneRowResizer" in source
    assert "function WbcPaneColumnResizer" in source
    assert 'edge === "replace"' in source
    assert 'menuType="content"' in source
    assert "onNewConversation={columnLength === 1" in source
    assert 'next[liveLocation.side] = [' in source
    assert "movePaneCardOtherSide(card.id)" in source
    assert 'String(paneCardDragId || "") !== String(card.id || "")' in source
    assert "replaceOnly={columnLength === 2}" in source
    assert 'replaceConversation={paneCardCount === 1 && card.kind === "chat"}' in source
    assert 'dropKey={dropKey || card.id}' in source
    assert 'dropTarget.dropKey || ""' in source
    assert 'side + ":" + index' in source
    assert "freshInstance: !!existingChat" in source
    assert "var existingChatCard = canonicalCardId" in source
    assert 'wbcPaneCard("chat", payload, { ownerChatId: ownerId, freshInstance: true })' in source
    assert 'sourceCardId = replacingSameChatCard ? canonicalChatCardId : ""' in source
    assert 'String(card.id || "") === "chat:" + String(activeChatId || "")' in source
    assert '(layout[target.side] || []).length >= 2 ? "replace" : edge' in source
    assert 'className={"wbc-pane-card-drop-layer" + (replaceOnly ? " replace-only" : "")}' in source
    assert 'promoteSourceLeft: true' in source
    assert 'restore: !!floating' in source
    assert 'card.kind === "file" || card.kind === "viewer"' in source
    assert 'card.kind === "side-agent"' in source
    assert 'setSideTab("side-agents")' in source
    assert "wbcPlacePaneCard(current, card, target.side, effectiveEdge" in source
    assert "function openConversationPanelFromMainGrip()" in source
    assert "if (paneCardCount === 1)" in source
    assert 'window.dispatchEvent(new CustomEvent("workbench:show-chat-side"));' in source.split(
        "function openConversationPanelFromMainGrip()", 1
    )[1].split("function renderPaneCard", 1)[0]
    assert "onOpenConversationPanel={openConversationPanelFromMainGrip}" in source
    assert ".wbc-page.wbc-side-hidden > .wbc-side" in styles

    pane_card_css = styles.split(".wbc-pane-card {", 1)[1].split("}", 1)[0]
    assert "border: var(--wb-floating-rail-border);" in pane_card_css
    assert "border-radius: var(--wb-floating-rail-radius);" in pane_card_css
    assert "background: var(--wb-floating-rail-bg);" in pane_card_css
    assert "box-shadow: var(--wb-floating-rail-shadow);" in pane_card_css
    assert "overflow: hidden;" in pane_card_css
    assert "cubic-bezier(.22, 1.16, .36, 1)" in pane_card_css
    pane_layout_css = styles.split("\n.wbc-pane-layout {", 1)[1].split("}", 1)[0]
    assert "padding: var(--wbc-card-top-inset) 0 var(--wbc-card-gutter) var(--wbc-card-gutter);" in pane_layout_css
    assert ".wbc-page > .wbc-pane-layout { grid-row: 1; }" in styles
    column_resizer = source.split("function WbcPaneColumnResizer", 1)[1].split(
        "function WbcSideAgentSplitResizer", 1
    )[0]
    assert 'style={{ right:' not in column_resizer
    assert "var minimum = Math.min(380, trackWidth / 2);" in column_resizer
    assert "maximum: Math.max(minimum, trackWidth - minimum)" in column_resizer
    assert '"--wbc-pane-right-width": paneColumnWidth + "px"' in source
    assert "width={paneColumnWidth} onResize={resizePaneColumn}" in source
    assert "!floatingConversationPanelOpen && !splitDetailOpen" in source
    row_resizer = source.split("function WbcPaneRowResizer", 1)[1].split(
        "function WbcPaneColumnResizer", 1
    )[0]
    assert "var seamOffset = 6 - (safeRatio * 12);" in row_resizer
    assert "rect.height - 12" in row_resizer
    assert ".wbc-pane-row-resizer" in styles
    assert ".wbc-pane-column-resizer" in styles
    replace_only_css = styles.split(".wbc-pane-card-drop-layer.replace-only {", 1)[1].split("}", 1)[0]
    assert "grid-template-rows: minmax(0, 1fr);" in replace_only_css
    assert ".wbc-pane-card-drop-layer.replace-only .wbc-pane-card-drop-zone.replace" in styles
    assert "--wbc-pane-column-floor: min(380px" in styles
    assert "grid-template-columns:" in styles
    assert "grid-column: 2;" in styles
    assert "align-self: center;" in styles
    assert "height: 88px;" in styles
    assert "body.wbc-resizing-pane-row .wbc-pane-column" in styles
    assert "animation: none;" in styles.split(
        "@media (prefers-reduced-motion: reduce)", 1
    )[1]
    assert i18n.count('"workbenchChat.newConversation"') == 2
    assert '"workbenchChat.newConversation": "新建对话"' in i18n
    assert '"workbenchChat.dropPaneTop"' in i18n
    assert '"workbenchChat.dropPaneBottom"' in i18n
    assert '"workbenchChat.dropPaneReplace"' in i18n
    assert '"workbenchChat.dropConversationReplace": "松手替换当前对话"' in i18n


def test_workbench_side_question_opens_the_existing_conversation_ui_in_a_split():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    pane = source.split("function renderPaneCard", 1)[1].split(
        "function renderPaneColumn", 1
    )[0]
    assert "var close = function ()" in pane
    assert "closePaneCard(card.id)" in pane
    assert "var move = function () { movePaneCardOtherSide(card.id); };" in pane
    assert "onClose={close}" in pane
    assert "onToggleSide={move}" in pane


def test_floating_conversation_panel_resource_split_replaces_right_and_restores_previous_split():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    opener = source.split("function openPaneContent", 1)[1].split(
        "function updatePaneCard", 1
    )[0]
    panel = source.split("function renderConversationPanel", 1)[1].split(
        "function openContentFromPaneCard", 1
    )[0]
    closer = source.split("function closePaneCard", 1)[1].split(
        "function movePaneCardOtherSide", 1
    )[0]
    assert "paneLayoutRestoreRef.current[card.id] = layout" in opener
    assert "next.left = [source.card]" in opener
    assert "next.right = [card]" in opener
    assert "restore: !!floating" in panel
    assert "promoteSourceLeft: true" in panel
    assert "updatePaneLayout(restore, ownerChatId)" in closer


def test_workbench_message_viewer_action_opens_the_file_split_directly():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    open_viewer = source.split("  function openViewer(file, preferredSide) {", 1)[1].split(
        "\n  function resourceSplitSideAt", 1
    )[0]
    assert "setViewerFile(file);" in open_viewer
    assert 'selectResourceSplit("viewer", wbcArtifactFileKey(file), true);' in open_viewer
    assert 'openPaneContent("file", file' in open_viewer
    assert 'side: preferredSide === "left" ? "left" : "right"' in open_viewer


def _legacy_test_workbench_side_question_opens_the_existing_conversation_ui_in_a_split():
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
    assert 'className="wbc-side-agent-split"' in split_component
    assert 'className="wbc-side-agent-split-picker"' in split_component
    assert '<WbcSplitPickerMenu open={pickerOpen}' in split_component
    assert "items.map(function (item, index)" in split_component
    assert "if (onSelect) onSelect(item.id);" in split_component
    assert "<WbcSideAgentTab" not in panel
    assert '(splitDetailOpen ? " side-agent-split-open" : "")' in source
    assert "browserVisible={hasActiveBrowser && !browserTabOpen && !splitDetailOpen}" in source
    assert "activeSideAgentId={splitSideAgentId}" in source
    assert "position: absolute;" in styles.split(".wbc-side-agent-split-motion {", 1)[1].split("}", 1)[0]
    # The open-state selector is needed only inside the compact responsive
    # override, where it restores the split's reserved grid track.
    assert ".wbc-page.side-agent-split-open {\n  grid-template-columns" not in styles.split(
        "@media (max-width: 980px) {", 1
    )[0]
    split_motion_css = styles.split(".wbc-side-agent-split-motion {", 1)[1].split("}", 1)[0]
    assert "width: var(--wbc-side-track-width);" in split_motion_css
    assert "transform 500ms cubic-bezier(.22, 1.16, .36, 1)," in split_motion_css
    assert "transform: translateX(100%);" in styles
    assert ".wbc-side-agent-split-motion.open" in styles
    assert 'localStorage.setItem("wbc-side-agent-split-width"' in source
    assert "function wbcClampSideSplitWidth" in source
    assert "var paneMin = Math.min(380, Math.max(0, (available - rail) / 2));" in source
    assert "function wbcClampSideSplitWidthForPage" in source
    assert "available - rail - paneMin" in source
    assert "wbcClampSideSplitWidthForPage(current, pageRef.current)" in source
    assert 'new ResizeObserver(keepSplitWithinViewport)' in source
    assert "--wbc-main-min-width: 300px;" not in styles
    assert "--wbc-main-min-width: 380px;" in styles
    assert 'window.addEventListener("resize", keepSplitWithinViewport);' in source
    assert '"--wbc-chat-side-preview-width": sideAgentSplitWidth + "px"' in source
    assert '...(splitDetailOpen ? { "--wbc-side-track-width": sideAgentSplitWidth + "px" } : {})' in source
    assert ".wbc-side-agent-split-resizer" in styles
    assert "body.wbc-resizing-side-agent .wbc-page" in styles
    # Pointer movement stays out of React while the grid remains live. Heavy
    # renderer observers pause during the gesture and refresh on pointerup;
    # the native browser alone keeps its coalesced bounds stream live.
    resizer = source.split("function WbcSideAgentSplitResizer", 1)[1].split(
        "function WbcSplitGripBar", 1
    )[0]
    move = resizer.split("function move(moveEvent)", 1)[1].split(
        "function stop(stopEvent)", 1
    )[0]
    stop = resizer.split("function stop(stopEvent)", 1)[1].split(
        "function resizeWithKeyboard", 1
    )[0]
    assert 'page.style.setProperty("--wbc-side-track-width", nextWidth + "px")' in resizer
    assert "requestAnimationFrame(paint)" in move
    assert "wbcNotifyBrowserLayoutChanged" not in move
    assert "onResize(" not in move
    assert 'new CustomEvent("workbench:split-resize-end"' in stop
    assert "wbcNotifyBrowserLayoutChanged();" in stop
    assert "onResize(nextWidth);" in stop
    assert 'window.addEventListener("pointercancel", stop' in resizer
    assert "aria-valuemin={380}" in resizer
    assert 'aria-valuenow={Math.round(Number(width) || 520)}' in resizer
    resize_motion_css = styles.split(
        "body.wbc-resizing-side-agent .wbc-side-agent-split-motion,", 1
    )[1].split("}", 1)[0]
    assert ".wbc-main > .wbc-composer" in resize_motion_css
    assert "transition: none;" in resize_motion_css
    # The outside edge is a physical anchor during direct manipulation: only
    # the divider facing the conversation is allowed to move.
    right_anchor_css = styles.split(
        "body.wbc-resizing-side-agent .wbc-page:not(.wbc-split-left) .wbc-side-agent-split-motion {",
        1,
    )[1].split("}", 1)[0]
    left_anchor_css = styles.split(
        "body.wbc-resizing-side-agent .wbc-page.wbc-split-left .wbc-side-agent-split-motion {",
        1,
    )[1].split("}", 1)[0]
    assert "right: 0;" in right_anchor_css
    assert "left: auto;" in right_anchor_css
    assert "right: auto;" in left_anchor_css
    assert "left: var(--wbc-rail-width);" in left_anchor_css
    assert 'document.body.classList.contains("wbc-resizing-side-agent")' in source
    chart_mount = (root / "src" / "webui" / "frontend" / "shared" / "chart" / "mount.jsx").read_text(
        encoding="utf-8"
    )
    browser_viewport = (root / "src" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(
        encoding="utf-8"
    )
    assert 'root.addEventListener("workbench:split-resize-end", resizeChart)' in chart_mount
    assert 'root.document.body.classList.contains("wbc-resizing-side-agent")' in chart_mount
    browser_resize_observer = browser_viewport.split(
        'const ro = typeof ResizeObserver !== "undefined"', 1
    )[1].split("if (ro && node)", 1)[0]
    assert 'if (!splitEntranceActive) scheduleBounds();' in browser_resize_observer
    assert "wbc-resizing-side-agent" not in browser_resize_observer
    assert 'window.addEventListener("workbench:split-resize-end", onSplitResizeEnd);' in browser_viewport
    assert 'lastBoundsRef.current = "";' in browser_viewport.split(
        "function onSplitResizeEnd()", 1
    )[1].split("}", 1)[0]
    # A minimum-width chat split owns a shrinkable, border-box composer.
    split_composer_css = styles.split(
        ".wbc-chat-split > .wbc-composer,", 1
    )[1].split("}", 1)[0]
    assert "width: 100%;" in split_composer_css
    assert "min-width: 0;" in split_composer_css
    assert "box-sizing: border-box;" in split_composer_css
    assert ".wbc-conversation-split::after" not in styles
    assert ".workbench-grid.is-task-detail .workbench-main::after" not in styles
    foreground_composer_css = styles.split(
        ".wbc-chat-split > .wbc-composer {", 1
    )[1].split("}", 1)[0]
    assert "position: absolute;" in foreground_composer_css
    assert "z-index: 24;" in foreground_composer_css
    assert "inset: auto 0 0;" in foreground_composer_css
    assert "padding: 10px 14px 14px;" in foreground_composer_css
    split_thread_css = styles.split(
        ".wbc-chat-split-stage .wbc-thread {", 1
    )[1].split("}", 1)[0]
    assert "padding: 4px 18px var(--wbc-thread-inset-bottom);" in split_thread_css
    split_message_css = styles.split(
        ".wbc-chat-split-stage .wbc-thread-item,", 1
    )[1].split("}", 1)[0]
    assert ".wbc-chat-split-stage .wbc-msg-body" in split_message_css
    assert ".wbc-chat-split-stage .wbc-chart" in split_message_css
    assert "min-width: 0;" in split_message_css
    assert "max-width: 100%;" in split_message_css
    split_overflow_css = styles.split(
        ".wbc-chat-split-stage .wbc-msg-body.markdown pre,", 1
    )[1].split("}", 1)[0]
    assert ".wbc-chat-split-stage .wbc-msg-body.markdown table" in split_overflow_css
    assert "overflow-x: auto;" in split_overflow_css
    compact_composer_css = styles.split(
        "@container wbc-composer (max-width: 420px) {", 1
    )[1].split("\n}", 1)[0]
    assert ".wbc-model-button-name" in compact_composer_css
    assert ".wbc-model-button-effort" in compact_composer_css
    assert ".wbc-composer-icon.mode > span" not in compact_composer_css
    assert ".wbc-send > span" not in compact_composer_css
    assert ".wbc-composer-actions" in compact_composer_css
    assert "flex-wrap: nowrap;" in compact_composer_css
    assert ".wbc-composer-spacer" in compact_composer_css
    assert "flex: 1 1 0;" in compact_composer_css
    assert "min-width: 44px;" in compact_composer_css
    main_clip_css = styles.split(
        ".wbc-page.side-agent-split-open > .wbc-main {", 1
    )[1].split("}", 1)[0]
    assert "overflow: hidden;" in main_clip_css
    main_message_css = styles.split(
        ".wbc-thread .wbc-thread-item,", 1
    )[1].split("}", 1)[0]
    assert ".wbc-thread .wbc-msg-body" in main_message_css
    assert "min-width: 0;" in main_message_css
    assert "max-width: 100%;" in main_message_css
    # Below 980px, an open split must override the generic two-column fallback
    # and reserve a physical track instead of covering the main conversation.
    responsive_css = styles.rsplit("@media (max-width: 980px) {", 1)[1].split(
        "@media (prefers-reduced-motion: reduce)", 1
    )[0]
    responsive_right = responsive_css.split(
        ".wbc-page.side-agent-split-open {", 1
    )[1].split("}", 1)[0]
    responsive_left = responsive_css.split(
        ".wbc-page.side-agent-split-open.wbc-split-left {", 1
    )[1].split("}", 1)[0]
    assert "var(--wbc-side-track-width);" in responsive_right
    assert "minmax(0, 1fr)" in responsive_right
    assert "var(--wbc-side-track-width)" in responsive_left
    assert responsive_left.rstrip().endswith("0px;")
    # The split panel is lifted with a native drag like an image/document:
    # the grip starts the drag, the panel stays put and drop zones react.
    assert 'var WBC_SPLIT_DRAG_MIME = "application/x-cyrene-split+json";' in source
    assert "function wbcSetSplitDrag" in source
    assert "function wbcHasSplitDrag" in source
    assert "function handleSplitDragStart" in source
    assert "setDragImage" in source
    # The native ghost is hidden with a preloaded transparent image; a raw-DOM overlay (drag ghost
    # + drop zones) follows the pointer and shrinks to a chat card over the
    # conversation rail. It is built outside React so the drag source's DOM
    # never changes mid-drag (a re-render there cancels the drag).
    assert "var WBC_EMPTY_DRAG_IMAGE = new Image(1, 1);" in source
    assert "function wbcHideNativeDragImage(transfer)" in source
    assert "splitOverlayCleanupRef" in source
    assert "onDocumentDrop" in source
    assert "function zoneAt" in source
    # The ghost is a clone of the real split panel (the lifted dialog); over
    # the conversation rail it switches to the matching rail card clone.
    assert "function wbcClonePaneWithLiveState(panel)" in source
    assert "var clone = panel.cloneNode(true);" in source
    assert "var scrollTop = Number(source.scrollTop) || 0;" in source
    assert "if (scrollTop || scrollLeft)" in source
    assert "viewportState[j].target.scrollTop = viewportState[j].scrollTop;" in source
    assert "if (restoreGhostViewport) {" in source
    assert "restoreGhostViewport();" in source
    assert "var removedThreadPadding = Math.max(0, sourceThreadPaddingTop - ghostThreadPaddingTop);" in source
    assert "ghostThread.scrollTop = Math.max(0, ghostThread.scrollTop - removedThreadPadding);" in source
    assert "var dragHandleRect = dragHandle && dragHandle.getBoundingClientRect" in source
    assert "var ghostTopGrabOffset = dragHandleRect" in source
    assert 'ghost.style.top = (event.clientY - ghostTopGrabOffset) + "px";' in source
    assert "y: ghostTopGrabOffset" in source
    assert "function positionGhostAt(clientX, clientY)" in source
    assert 'ghost.classList.contains("card") && cardW ? cardW' in source
    assert "window.innerWidth - targetWidth - viewportInset" in source
    assert "window.innerHeight - targetHeight - viewportInset" in source
    assert "positionGhostAt(ev.clientX, ev.clientY);" in source
    assert "positionGhostAt(event.clientX, event.clientY);" in source
    assert "cardClone" in source
    assert 'data-chat-id={String(chat.id)}' in source
    assert "wbc-split-card-lifted" in styles
    # Only conversation splits carry their own top grip: the split-chat panel
    # (rail chat dragged onto the side). Side questions belong to the content
    # tabs and, like artifact/change/map/browser/subagents, render only the
    # main-conversation grip with no duplicate handle.
    # Each handle identifies its own conversation explicitly. The split-side
    # ghost is cloned from the exact pane containing that handle, so a stale
    # closing host elsewhere on the page cannot make both handles lift the
    # same conversation.
    assert 'function handleSplitDragStart(event, dragSource)' in source
    assert 'var fromMainGrip = dragSource === "main";' in source
    assert 'page.querySelector(":scope > .wbc-main")' in source
    assert 'dragHandle.closest(".wbc-side-agent-split")' in source
    assert 'dragSource="main"' in source
    assert 'dragSource="split"' in source
    assert "activeChatIdRef.current" in source
    assert "splitSideAgentId" in source
    # A drop zone names the dragged conversation's destination. The split
    # anchor matches it for the split grip and is opposite for the main grip;
    # this prevents an immediate exchange at drag start. A midpoint dead band
    # also filters tiny/stale initial drag coordinates.
    assert "function wbcSplitSideForDraggedConversation(conversationSide, fromMainGrip)" in source
    assert "setSplitSideDirect(wbcSplitSideForDraggedConversation(zone, fromMainGrip));" in source
    assert "var swapThreshold = 24;" in source
    assert "return previewConversationSide;" in source
    split_side_helper = "function wbcSplitSideForDraggedConversation(" + source.split(
        "function wbcSplitSideForDraggedConversation(", 1
    )[1].split("function wbcChatSideDropZone", 1)[0]
    mapping_script = f"""
eval({json.dumps(split_side_helper)});
process.stdout.write(JSON.stringify([
  wbcSplitSideForDraggedConversation("left", false),
  wbcSplitSideForDraggedConversation("right", false),
  wbcSplitSideForDraggedConversation("left", true),
  wbcSplitSideForDraggedConversation("right", true)
]));
"""
    mapping_result = subprocess.run(
        ["node", "-e", mapping_script], check=True, capture_output=True, text=True
    )
    assert json.loads(mapping_result.stdout) == ["left", "right", "right", "left"]
    # A missing exact rail card must not silently turn into another session's
    # card ghost.
    assert "if (!railCard && !splitChatId && !fromMainGrip)" in source
    assert "wbc-conversation-split" in source
    assert '<div className="wbc-split-panel-grip">' in source
    side_split = source.split("function WbcSideAgentSplit({", 1)[1].split(
        "function WbcSideAgentTab", 1
    )[0]
    assert "<WbcSplitGripBar" not in side_split
    assert "wbc-conversation-split" not in side_split
    chat_split = source.split("function WbcChatSplit({", 1)[1].split(
        "function WbcChatSplitHost", 1
    )[0]
    assert "<WbcSplitGripBar" in chat_split
    assert "wbc-conversation-split" in chat_split
    # The split chat's grip menu opens the full conversation panel (the same
    # WbcSide component as the main float) inside the split, fed with the
    # split chat's own data — never the main conversation's float.
    assert "splitPanelOpen" in chat_split
    assert "setSplitPanelOpen(true)" in chat_split
    assert '<div className="wbc-split-chat-panel"' in chat_split
    assert "<WbcSide" in chat_split
    assert "activeChatId={chatId}" in chat_split
    assert "chats={chat ? [chat] : []}" in chat_split
    assert "floating={true}" in chat_split
    assert "onCloseFloating={function () { setSplitPanelOpen(false); }}" in chat_split
    # The panel starts collapsed (no accordion tab open), like the main one,
    # and a pointer-down outside the panel dismisses it.
    assert 'var [splitPanelTab, setSplitPanelTab] = useWbcState("");' in chat_split
    assert "splitPanelRef" in chat_split
    assert "function closeOutside(event)" in chat_split
    assert "splitPanelRef.current.contains(event.target)" in chat_split
    assert 'ref={splitPanelRef}' in chat_split
    for name, marker, tail in (
        ("artifact", "function WbcArtifactSplit({", "function WbcChangeSplitHost"),
        ("change", "function WbcChangeSplit({", "function WbcResourceSplitHost"),
        ("map", "function WbcMapSplit({", "function WbcBrowserSplitHost"),
        ("browser", "function WbcBrowserSplit({", "function WbcSideAgentSplitHost"),
        ("subagents", "function WbcSubagentsSplitHost", "function WbcSideAgentSplitHost"),
        ("side-question", "function WbcSideAgentSplit({", "function WbcSideAgentTab"),
    ):
        body = source.split(marker, 1)[1].split(tail, 1)[0]
        assert "<WbcSplitGripBar" not in body, name
    assert "wbc-split-drag-ghost" in styles
    drag_ghost_css = styles.split(".wbc-split-drag-ghost {", 1)[1].split("}", 1)[0]
    assert "box-sizing: border-box;" in drag_ghost_css
    assert "background: var(--wb-main-bg);" in drag_ghost_css
    assert "0 6px 18px rgba(15, 23, 42, 0.08)" in drag_ghost_css
    assert "--wbc-thread-inset-top: 24px;" in styles
    assert ".wbc-split-drag-ghost .wbc-thread" in styles
    assert "padding-inline: 24px;" in styles
    assert ".wbc-split-drag-ghost > .wbc-conversation-split" in styles
    assert ".wbc-split-drag-ghost .wbc-split-panel-grip" in styles
    assert 'wbcT("workbenchChat.splitDropClose"' in source
    assert 'wbcT("workbenchChat.splitDropLeft"' in source
    assert 'wbcT("workbenchChat.splitDropRight"' in source
    side_zone_helper = source.split("function wbcChatSideZoneRect()", 1)[1].split(
        "function wbcClonePaneWithLiveState", 1
    )[0]
    assert 'page.classList.contains("wbc-side-hidden")' in side_zone_helper
    assert "if (side && !sideHidden)" in side_zone_helper
    assert "sr.left < pr.right && sr.right > pr.left" in side_zone_helper
    assert "left: pr.right - zoneWidth" in side_zone_helper
    assert 'getPropertyValue("--wbc-chat-side-preview-width")' in side_zone_helper
    assert 'document.addEventListener("dragover", onDocumentChatDragOver, true)' in source
    assert 'document.removeEventListener("dragover", onDocumentChatDragOver, true)' in source
    assert "function handleSideLayerDragLeave" not in source
    side_drop_layer = source.split('key="chat-side-drop-layer"', 1)[1].split(
        "{splitDetailOpen &&", 1
    )[0]
    assert 'className="wbc-chat-side-drop-hint"' in side_drop_layer
    assert "chatSideDropActive &&" not in side_drop_layer
    side_drop_css = styles.split(".wbc-chat-side-drop-layer {", 1)[1].split("}", 1)[0]
    side_drop_active_css = styles.split(".wbc-chat-side-drop-layer.active {", 1)[1].split("}", 1)[0]
    assert "background: transparent;" in side_drop_css
    assert "background: transparent;" in side_drop_active_css
    assert "top: 58px;" in side_drop_css
    assert "border: 2px solid transparent;" in side_drop_css
    assert "border-color: color-mix(in srgb, var(--wb-accent) 72%, transparent);" in side_drop_active_css
    assert "dashed" not in side_drop_css
    assert "var(--wb-accent) 5%" not in side_drop_css
    assert "var(--wb-accent) 8%" not in side_drop_active_css
    side_hint_hidden_css = styles.split(
        ".wbc-chat-side-drop-layer .wbc-chat-side-drop-hint {", 1
    )[1].split("}", 1)[0]
    side_hint_visible_css = styles.split(
        ".wbc-chat-side-drop-layer.active .wbc-chat-side-drop-hint {", 1
    )[1].split("}", 1)[0]
    assert "opacity: 0;" in side_hint_hidden_css
    assert "visibility: hidden;" in side_hint_hidden_css
    assert "opacity: 1;" in side_hint_visible_css
    assert "visibility: visible;" in side_hint_visible_css
    hidden_drop_preview_css = styles.split(
        ".wbc-page.wbc-side-hidden.wbc-chat-side-drop-active {", 1
    )[1].split("}", 1)[0]
    assert "--wbc-side-track-width: var(--wbc-chat-side-preview-width);" in hidden_drop_preview_css
    assert "--wbc-reclaimed-side-width: 0px;" in hidden_drop_preview_css
    assert "--wbc-conversation-shift: 0px;" in hidden_drop_preview_css
    assert "var(--wbc-chat-side-preview-width);" in hidden_drop_preview_css
    grip_bar = source.split("function WbcSplitGripBar", 1)[1].split(
        "function WbcSideAgentSplit", 1
    )[0]
    assert 'draggable="true"' in grip_bar
    assert "onSplitDragStart(event, dragSource)" in grip_bar
    assert "onDragEnd" in grip_bar
    assert "onClick" in grip_bar
    assert "onDragPanel" not in grip_bar
    assert ".wbc-split-drop-zones" in styles
    assert ".wbc-split-drop-zone.active" in styles
    assert ".wbc-split-drop-rail" in styles
    assert ".wbc-split-drop-left" in styles
    assert ".wbc-split-drop-right" in styles
    # Highlights use the pane under the exact grip: the flexible main pane can
    # be a different width from the fixed split track.
    drop_zone_css = styles.split(".wbc-split-drop-zone {", 1)[1].split("}", 1)[0]
    assert "width: var(--wbc-split-drop-width, var(--wbc-side-track-width));" in drop_zone_css
    drag_start = source.split("function handleSplitDragStart", 1)[1].split(
        "function handleSplitDragEnd", 1
    )[0]
    assert 'zones.style.setProperty("--wbc-split-drop-width", panelW + "px")' in drag_start
    assert 'zones.setAttribute("data-conversation-side", previewConversationSide)' in drag_start
    assert '.wbc-split-drop-zones[data-conversation-side="left"] .wbc-split-drop-rail' in styles
    assert ".wbc-split-drop-main" not in styles
    assert ".wbc-split-drop-half" not in styles
    split_head_css = styles.split(".wbc-side-agent-split-head {", 1)[1].split("}", 1)[0]
    assert "border-radius: 14px;" in split_head_css
    assert "margin: 12px 12px 8px;" in split_head_css
    assert "z-index: 1001;" in split_head_css
    split_css = styles.split(".wbc-side-agent-split {", 1)[1].split("}", 1)[0]
    # Content splits clear only the fixed app bar; there is no split grip band.
    assert "padding-top: 58px;" in split_css
    assert "function WbcSideSplitGrip" not in source
    resource_host = source.split("function WbcResourceSplitHost", 1)[1].split(
        "function WbcMapSplitHost", 1
    )[0]
    assert "WbcSideSplitGrip" not in resource_host
    # Conversation splits extend the panel with a 26px grip band below the
    # fixed app bar; the panel grip mirrors the main-conversation grip.
    conv_css = styles.split(
        ".wbc-side-agent-split.wbc-conversation-split {", 1
    )[1].split("}", 1)[0]
    assert "position: relative;" in conv_css
    assert "padding-top: 72px;" in conv_css
    panel_grip_css = styles.split(".wbc-split-panel-grip {", 1)[1].split("}", 1)[0]
    assert "top: 58px;" in panel_grip_css
    assert "height: 26px;" in panel_grip_css
    assert "z-index: 42;" in panel_grip_css
    assert ".wbc-split-panel-grip:hover .wbc-side-split-grip-bar" in styles
    # The split chat's own floating panel reuses the full conversation panel:
    # the container anchors inside the split, sizes to its content like the
    # main float (never fills the split) and its card stays opaque.
    split_panel_css = styles.split(".wbc-split-chat-panel {", 1)[1].split("}", 1)[0]
    assert "position: absolute;" in split_panel_css
    assert "z-index: 1190;" in split_panel_css
    assert "max-height: calc(100% - 100px);" in split_panel_css
    assert "width: min(var(--wb-right-w, 350px), calc(100% - 24px));" in split_panel_css
    assert "transform: translateX(-50%);" in split_panel_css
    # Matches the main floating panel: non-blocking shell, entrance animation,
    # flush padding so the card spans the full 350px.
    assert "overflow: visible;" in split_panel_css
    assert "pointer-events: none;" in split_panel_css
    assert "animation: wbc-floating-side-panel-in" in split_panel_css
    split_panel_side_css = styles.split(
        ".wbc-split-chat-panel .wbc-side {", 1
    )[1].split("}", 1)[0]
    assert "padding: 0;" in split_panel_side_css
    split_panel_card_css = styles.split(
        ".wbc-split-chat-panel .wbc-side-card {", 1
    )[1].split("}", 1)[0]
    assert "max-height: calc(100vh - 104px);" in split_panel_card_css
    assert "background: var(--wb-card-bg);" in split_panel_card_css
    assert "--wbc-main-min-width: 380px;" in styles


def test_workbench_split_grip_opens_a_centered_floating_conversation_panel():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    grip_bar = source.split("function WbcSplitGripBar", 1)[1].split(
        "function WbcSideAgentSplit", 1
    )[0]
    assert 'window.CyreneUI.require("browser-overlays")' in grip_bar
    assert "overlays.adjust(1);" in grip_bar
    assert "overlays.adjust(-1);" in grip_bar
    assert "}, [menuOpen]);" in grip_bar
    assert "WBC_ICONS.sidebar" in grip_bar
    assert 'wbcT("workbenchChat.detailPanel.openConversationPanel"' in grip_bar
    assert "if (onOpenConversationPanel) onOpenConversationPanel();" in grip_bar
    assert "if (onClose) onClose();" not in grip_bar.split("function openConversationPanel", 1)[1].split("return (", 1)[0]
    assert "floatingConversationPanelOpen" in source
    assert "renderConversationPanel(true)" in source
    assert 'floating ? " wbc-side-floating" : ""' in source
    assert "floating ? WBC_ICONS.x : WBC_ICONS.chevronsRight" in source
    assert 'wbcT("workbenchChat.closeFloatingConversationPanel"' in source

    split_menu_icon_css = styles.split(
        ".wbc-side-split-grip-menu button > span:first-child {", 1
    )[1].split("}", 1)[0]
    assert "flex: 0 0 32px;" in split_menu_icon_css
    assert "justify-content: center;" in split_menu_icon_css
    split_menu_css = styles.split(".wbc-side-split-grip-menu {", 1)[1].split("}", 1)[0]
    assert "top: calc(100% + 4px);" in split_menu_css
    assert "left: 50%;" in split_menu_css
    assert "transform: translateX(-50%);" in split_menu_css
    assert "right:" not in split_menu_css

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


def _legacy_test_each_conversation_split_grip_closes_its_own_conversation():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    close_main = source.split("  function closeMainConversationSplit() {", 1)[1].split(
        "\n  function closeActiveSplit", 1
    )[0]
    drag_drop = source.split("    function onDocumentDrop(ev) {", 1)[1].split(
        "\n    function cleanup", 1
    )[0]
    main_grip = source.split('{splitDetailOpen && (\n        <div key="split-main-grip"', 1)[1].split(
        "\n      )}", 1
    )[0]
    chat_split = source.split("function WbcChatSplit({", 1)[1].split(
        "function WbcSideAgentSplitResizer", 1
    )[0]

    # Closing A removes A's stored split before the eagerly-updated selection
    # switches to B, so B remains as the sole visible conversation.
    assert "delete updated[sourceChatId];" in close_main
    assert "selectChat(targetChatId);" in close_main
    assert "closeResourceSplit();" not in close_main
    assert "onClose={splitChatId ? closeMainConversationSplit : closeActiveSplit}" in main_grip
    # B keeps its own close callback. Drag-to-rail uses the drag source to make
    # the same A/B decision instead of closing whichever split is globally active.
    assert "onClose={onClose}" in chat_split
    assert "if (fromMainGrip) closeMainConversationSplit();" in drag_drop
    assert "else closeResourceSplit();" in drag_drop

    # Both physical placements expose one stable, position-independent label.
    grip_bar = source.split("function WbcSplitGripBar", 1)[1].split(
        "function WbcSideAgentSplit", 1
    )[0]
    assert 'wbcT("workbenchChat.splitMoveOtherSide", "Move split to the other side")' in grip_bar
    assert '"workbenchChat.splitMoveLeft"' not in i18n
    assert '"workbenchChat.splitMoveRight"' not in i18n
    assert i18n.count('"workbenchChat.splitMoveOtherSide"') == 2
    assert '"workbenchChat.splitMoveOtherSide": "把分屏移动到另一侧"' in i18n


def _legacy_test_floating_conversation_panel_resource_split_replaces_right_and_restores_previous_split():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    begin = source.split("  function beginFloatingPanelSplit(openSplit, sourceChatId, sourceChatSnapshot) {", 1)[1].split(
        "\n  function restoreFloatingPanelSplit", 1
    )[0]
    restore = source.split("  function restoreFloatingPanelSplit() {", 1)[1].split(
        "\n  function closeSideAgentSplit", 1
    )[0]
    render_panel = source.split("  function renderConversationPanel(floating) {", 1)[1].split(
        "\n\n  return (\n    <div", 1
    )[0]

    split_chat_content = source.split("  function openSplitChatContent(type, payload, sourceChatSnapshot) {", 1)[1].split(
        "\n  function renderConversationPanel", 1
    )[0]
    chat_split = source.split("function WbcChatSplit({", 1)[1].split(
        "function WbcSideAgentSplitResizer", 1
    )[0]

    # Snapshot both the original main conversation and, when different, the
    # conversation that opened the content split.
    assert "floatingSplitRestoreRef.current = {" in begin
    assert "splitSide: splitSide" in begin
    assert "activeChatId: activeId" in begin
    assert "activeChat: activeChat &&" in begin
    assert "activeSplit: splitStateSnapshot(activeId)" in begin
    assert "sourceSplit: sourceId === activeId ? null : splitStateSnapshot(sourceId)" in begin
    # Whether the source conversation started left or right, it owns the left
    # track and the new content owns the right. Selecting a right-side source
    # causes the existing grid transition to slide it into the left position.
    assert "function promoteSourceAndOpenContent()" in begin
    assert "selectChat(sourceId);" in begin
    assert 'setSplitSideDirect("right");' in begin
    assert "openSplit();" in begin
    # Reuse the split pane's already-loaded transcript and commit the owner
    # handoff atomically, preventing a one-frame loading/empty flash.
    assert "chatCache.details[sourceId] = sourceChat;" in begin
    assert "setActiveChat(sourceChat);" in begin
    assert "setChatLoading(false);" in begin
    assert "window.ReactDOM.flushSync(commitPromotion)" in begin
    assert "document.startViewTransition" in begin
    assert 'var transitionName = "wbc-promoted-conversation";' in begin
    assert 'querySelector(":scope > .wbc-main")' in begin
    assert "html.wbc-split-view-transition::view-transition-old(root)" in styles
    assert "::view-transition-group(wbc-promoted-conversation)" in styles
    assert "animation-duration: 380ms;" in styles
    assert "cubic-bezier(.4, 0, .6, 1)" in styles
    # The conversation moves as one solid pane. Do not cross-fade its old and
    # new snapshots, which reads as two different panels replacing each other.
    solid_pane_styles = styles.split(
        "/* Move one solid pane instead of cross-fading the source and destination",
        1,
    )[1].split("/* The two supporting surfaces are time-reversed as well", 1)[0]
    promoted_old = solid_pane_styles.split(
        "html.wbc-split-view-transition::view-transition-old(wbc-promoted-conversation) {",
        1,
    )[1].split("}", 1)[0]
    promoted_new = solid_pane_styles.split(
        "html.wbc-split-view-transition::view-transition-new(wbc-promoted-conversation) {",
        1,
    )[1].split("}", 1)[0]
    assert "animation-name: none" in promoted_old
    assert "opacity: 1" in promoted_old
    assert "animation-name: none" in promoted_new
    assert "opacity: 0" in promoted_new
    assert "wbc-split-view-transition-opening" in begin
    assert 'var displacedName = "wbc-displaced-conversation";' in begin
    assert 'var resourceName = "wbc-promoted-resource";' in begin
    # Position and width change in one shared-element transition. There is no
    # narrow left-side intermediate layout (or timer boundary) that can snap
    # to the wider main conversation after arriving.
    assert 'setSplitSideDirect("left");' not in begin
    assert 'movingPane.addEventListener("transitionend", onMoveEnd);' not in begin
    assert "setTimeout(promoteSourceAndOpenContent" not in begin
    assert 'window.matchMedia("(prefers-reduced-motion: reduce)").matches' in begin
    # Closing temporary content restores both conversations and the original
    # left/right placement.
    assert "restoreSplitState(snapshot.activeChatId, snapshot.activeSplit);" in restore
    assert "restoreSplitState(snapshot.chatId, snapshot.sourceSplit);" in restore
    assert "selectChat(snapshot.activeChatId);" in restore
    assert 'setSplitSideDirect(snapshot.splitSide === "left" ? "left" : "right");' in restore
    assert "function commitRestoreNow()" in restore
    assert "snapshot.activeChat || chatCache.details[snapshot.activeChatId]" in restore
    assert "window.ReactDOM.flushSync(commitRestore)" in restore
    assert "document.startViewTransition" in restore
    assert "wbc-split-view-transition-closing" in restore
    assert 'sourcePane.style.viewTransitionName = transitionName;' in restore
    assert 'targetPane.style.viewTransitionName = transitionName;' in restore
    assert 'targetMainPane.style.viewTransitionName = displacedName;' in restore
    # Newly mounted split hosts must be captured at their settled rectangle.
    # Otherwise the close transition targets the host's offscreen enter frame
    # and then snaps/slides to the real split position after the handoff.
    assert "function wbcPinSplitMotionOpen(host)" in source
    assert "host.style.transition = \"none\";" in source
    assert "host.style.transform = \"translateX(0)\";" in source
    assert "wbcPinSplitMotionOpen(targetResourcePane);" in begin
    assert "wbcPinSplitMotionOpen(targetMotion);" in restore
    assert "wbcReleasePinnedSplitMotion(targetResourcePane);" in begin
    assert 'wbcReleasePinnedSplitMotion(targetPane.closest(".wbc-side-agent-split-motion"));' in restore
    # Preserve the same visible message while the pane changes width/owner.
    # Copying scrollTop directly would drift because text wraps differently in
    # the narrow main pane and the wider split pane.
    assert "function wbcCaptureConversationViewport(pane)" in source
    assert "function wbcRestoreConversationViewport(pane, viewport)" in source
    assert 'querySelectorAll(":scope > [data-wbc-thread-item]")' in source
    assert "anchorOffset: anchorOffset" in source
    assert "var promotedViewport = wbcCaptureConversationViewport(sourcePane);" in begin
    assert "wbcRestoreConversationViewport(targetPane, promotedViewport);" in begin
    assert "var restoredViewport = wbcCaptureConversationViewport(sourcePane);" in restore
    assert "wbcRestoreConversationViewport(targetPane, restoredViewport);" in restore
    assert "Promise.resolve(transition.ready)" in begin
    assert "Promise.resolve(transition.ready)" in restore
    # Swap the split/resource width with the old main width in the same commit.
    # This keeps the promoted pane's source and destination rectangles equal,
    # so the whole transcript + composer translates without scaling/reflow.
    assert "splitWidth: sideAgentSplitWidth" in begin
    assert "promotedResourceWidth:" in begin
    assert "floatingSplitRestoreRef.current.promotedResourceWidth" in begin
    assert "setSideAgentSplitWidth(wbcClampSideSplitWidthForPage(snapshot.splitWidth" in restore
    assert "function wbcPinPageSplitLayout(page)" in source
    assert 'page.style.transition = "none";' in source
    assert "wbcPinPageSplitLayout(page);" in begin
    assert "wbcPinPageSplitLayout(page);" in restore
    assert "wbcReleasePinnedPageSplitLayout(page);" in begin
    assert "wbcReleasePinnedPageSplitLayout(page);" in restore
    assert 'data-split-open={openKey ? "true" : "false"}' in source
    assert 'data-split-open="true"' in begin
    assert 'data-split-open="true"' in restore
    assert "wbc-split-surface-in" in styles
    assert "wbc-split-surface-out" in styles
    assert source.count("if (restoreFloatingPanelSplit()) return;") >= 5
    assert "if (floating) beginFloatingPanelSplit(openSplit);" in render_panel
    assert 'selectResourceSplit("viewer"' in render_panel
    assert 'selectResourceSplit("map"' in render_panel
    assert 'selectResourceSplit("browser"' in render_panel
    assert 'selectResourceSplit("subagents"' in render_panel
    assert "selectArtifact(file)" in render_panel
    assert "selectChange(change)" in render_panel
    assert "selectSideAgent(agentId)" in render_panel
    # The split conversation's own floating panel exposes the same content
    # entries instead of swallowing them with placeholder callbacks.
    assert "beginFloatingPanelSplit(function ()" in split_chat_content
    assert "}, sourceChatId, sourceChatSnapshot);" in split_chat_content
    assert 'type === "artifact"' in split_chat_content
    assert 'type === "change"' in split_chat_content
    assert 'type === "viewer"' in split_chat_content
    assert 'type === "map"' in split_chat_content
    assert 'type === "browser"' in split_chat_content
    assert 'onSelectArtifact={function (file) { openContent("artifact", file); }}' in chat_split
    assert 'onSelectChange={function (change) { openContent("change", change); }}' in chat_split
    assert 'onSelectMap={function (item) { openContent("map", item); }}' in chat_split
    assert 'onSelectBrowser={function (tabId) { openContent("browser", tabId); }}' in chat_split
    assert "onOpenContent(type, payload, chat)" in chat_split
    assert "browserActiveByChat={browserActiveByChat}" in chat_split
    assert "browserSuppressed={false}" in chat_split


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
    assert "var splitDetailOpen = paneUsesWorkspace;" in source
    assert ".wbc-artifact-list-row" in styles
    assert ".wbc-artifact-split-viewer" in styles


def test_workbench_viewer_split_grip_has_symmetric_vertical_spacing():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

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


def _legacy_test_workbench_message_viewer_action_opens_the_file_split_directly():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    open_viewer = source.split("  function openViewer(file) {", 1)[1].split(
        "\n  function revealTopbarResource", 1
    )[0]
    assert "setViewerFile(file);" in open_viewer
    assert 'setSideTab("");' in open_viewer
    assert 'setSideTab("viewer");' not in open_viewer
    assert 'selectResourceSplit("viewer", wbcArtifactFileKey(file));' in open_viewer
    assert "viewerFile && wbcArtifactFileKey(viewerFile)" in source
    close_viewer = source.split("  function closeResourceSplit() {", 1)[1].split(
        "\n  function closeMainConversationSplit", 1
    )[0]
    assert 'resourceSplitByChat[chatId].type === "viewer"' in close_viewer
    assert "setViewerFile(null);" in close_viewer
    assert 'return current === "viewer" ? "" : current;' in close_viewer


def test_project_file_rows_drag_to_viewer_split_and_topbar_resource_shelf():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    project_resource = source.split("function wbcProjectFileResource", 1)[1].split(
        "function WbcRail", 1
    )[0]
    project_rows = source.split('{railMode === "files" ? (', 1)[1].split(
        ") : (", 1
    )[0]
    project_browser = source.split("function WbcRail", 1)[1].split(
        "function WbcBrowserFloatingSurface", 1
    )[0]
    split_drop = source.split("  function resourceSplitSideAt", 1)[1].split(
        "\n  function revealTopbarResource", 1
    )[0]

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
    assert 'className="wbc-resource-file-drop-zones"' in source
    assert 'className="wbc-chat-side-drop-hint" role="status"' in source
    assert 'event.target.closest(".wbc-pane-card")' in split_drop
    assert ".wbc-resource-file-drop-zones" in styles
    resource_zone = styles.split(".wbc-resource-file-drop-zone {", 1)[1].split("}", 1)[0]
    resource_active = styles.split(".wbc-resource-file-drop-zone.active {", 1)[1].split("}", 1)[0]
    resource_hint = styles.split(
        ".wbc-resource-file-drop-zone .wbc-chat-side-drop-hint {", 1
    )[1].split("}", 1)[0]
    assert "border: 2px solid transparent;" in resource_zone
    assert "border-color: color-mix(in srgb, var(--wb-accent) 72%, transparent);" in resource_active
    assert "cubic-bezier(.22, 1.16, .36, 1)" in resource_hint
    assert 'return { projectId: String(projectId || ""), path: "." };' in project_browser
    assert "fileLocation.projectId === currentFileProjectId" in project_browser
    assert 'setQuery("");' in project_browser
    assert "setFileEntries([]);" in project_browser


def test_project_files_open_in_a_project_scoped_pane_without_an_active_chat():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    pane_helpers = source.split("  function projectPaneOwnerKey()", 1)[1].split(
        "\n  function updatePaneCard", 1
    )[0]
    open_viewer = source.split("  function openViewer(file, preferredSide)", 1)[1].split(
        "\n  function openProjectFile", 1
    )[0]

    assert 'return projectId ? "project:" + String(projectId) : "";' in pane_helpers
    assert "chatId || activeChatIdRef.current || projectPaneOwnerKey()" in pane_helpers
    assert "wbcNormalizePaneLayout(paneLayoutsByChat[ownerId], ownerChatId)" in pane_helpers
    assert 'if (!ownerId || !type) return null;' in pane_helpers
    assert 'openPaneContent("file", file' in open_viewer
    assert "var projectPaneOnly = !activeChatId && paneCardCount > 0;" in source
    assert "var showNewConversationWorkspace = !activeChatId && paneCardCount === 0;" in source
    assert '(projectPaneOnly ? " wbc-project-pane-only" : "")' in source
    assert "!projectPaneOnly && !floatingConversationPanelOpen && !splitDetailOpen" in source
    assert 'className="wbc-pane-column left wbc-new-conversation-column"' in source
    assert 'renderPaneCard({ id: "new-conversation", kind: "chat", payload: "", ownerChatId: "" }, "left", 1)' in source
    project_only_styles = styles.split(".wbc-page.wbc-project-pane-only {", 1)[1].split(
        "}", 1
    )[0]
    assert "--wbc-side-track-width: 0px;" in project_only_styles
    assert "minmax(0, 1fr)" in project_only_styles
    assert ".wbc-page.wbc-project-pane-only > .wbc-pane-layout.single" in styles


def test_project_text_files_use_codemirror_with_live_markdown_and_conflict_controls():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    editor = (root / "src" / "webui" / "frontend" / "code" / "editor.jsx").read_text(
        encoding="utf-8"
    )
    renderer = (
        root / "src" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx"
    ).read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(
        encoding="utf-8"
    )
    package = json.loads((root / "src" / "webui" / "package.json").read_text(encoding="utf-8"))

    assert "@codemirror/view" in package["dependencies"]
    assert "@codemirror/lang-markdown" in package["dependencies"]
    assert "turndown" in package["dependencies"]
    assert "turndown-plugin-gfm" in package["dependencies"]
    assert 'import { Compartment, EditorState } from "@codemirror/state";' in editor
    assert "root.CyreneCodeMirror = Object.freeze({" in editor
    assert "Editor: Editor," in editor
    assert 'key: "Mod-s"' in editor
    assert 'compiled/code/editor.js?v=0.7.9' in index
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
    diff_content_styles = styles.split(
        ".wbc-change-diff .diff-viewer-content {", 1
    )[1].split("}", 1)[0]
    assert "overflow-y: auto;" in diff_content_styles


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


def test_memory_detail_uses_shared_floating_card_and_animated_accordion():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    memory_source = (
        root / "src" / "webui" / "frontend" / "workbench-memory.jsx"
    ).read_text(encoding="utf-8")
    library_source = (
        root / "src" / "webui" / "frontend" / "workbench-library.jsx"
    ).read_text(encoding="utf-8")
    library_styles = (
        root / "src" / "webui" / "frontend" / "workbench-library.css"
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
    assert "padding: 12px;" in shell_css
    assert "padding: 12px;" in detail_css
    assert "--wb-floating-detail-width: 350px;" in shared_width_css
    assert "flex: 0 0 var(--wb-floating-detail-width);" in detail_css
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


def test_memory_first_card_clears_the_dark_glass_toolbar():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

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
    source = (root / "src/webui/frontend/workbench-memory.jsx").read_text(
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
    source = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench-library.css").read_text(
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
    assert ".then(function (result)" in runtime_interrupt
    assert ".finally(function () {" not in runtime_interrupt
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


def test_background_chat_refresh_never_overwrites_a_newer_selection():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    helper_source = "function wbcResolveRefreshedChatSelection(" + source.split(
        "function wbcResolveRefreshedChatSelection(", 1
    )[1].split("function WorkbenchChatPage", 1)[0]
    script = f"""
eval({json.dumps(helper_source)});
const chats = [{{ id: "old" }}, {{ id: "other" }}];
const result = {{
  selectionChangedDuringRequest: wbcResolveRefreshedChatSelection(chats, "", "old", "new"),
  unchangedSelectionStillPresent: wbcResolveRefreshedChatSelection(chats, "", "old", "old"),
  unchangedSelectionWasDeleted: wbcResolveRefreshedChatSelection([{{ id: "other" }}], "", "old", "old"),
  explicitSelection: wbcResolveRefreshedChatSelection(chats, "other", "old", "new"),
  missingExplicitSelection: wbcResolveRefreshedChatSelection(chats, "missing", "old", "new")
}};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result == {
        "selectionChangedDuringRequest": None,
        "unchangedSelectionStillPresent": None,
        "unchangedSelectionWasDeleted": "other",
        "explicitSelection": "other",
        "missingExplicitSelection": "old",
    }


def test_background_chat_completion_updates_detail_cache_before_runtime_is_cleared():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    saved_hook = source.split(
        "onAssistantSaved: function (chatId, assistantMessages) {", 1
    )[1].split("onAwaitingUser:", 1)[0]
    settled_hook = source.split("onSettled: function (chatId) {", 1)[1].split(
        "onResync:", 1
    )[0]
    resync_hook = source.split("onResync: function (chatId) {", 1)[1].split(
        "    });", 1
    )[0]

    assert "chatCache.details[chatId] = wbcMergeSavedAssistantMessages" in saved_hook
    assert "chatCache.details[chatId] = chat;" in settled_hook
    assert "chatCache.details[chatId] = chat;" in resync_hook


def test_saved_assistant_messages_merge_reasoning_into_stale_background_chat():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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
    create = source.split("  function handleCreateChat() {", 1)[1].split(
        "\n  // The shell-level menu/shortcut", 1
    )[0]
    assert "var selectionAtRequest = String(activeChatIdRef.current || \"\");" in refresh
    assert "wbcResolveRefreshedChatSelection(" in refresh
    assert "if (targetId !== null) selectChat(targetId);" in refresh
    assert "selectChat(chat.id);" in create


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
    args_preview_source = "function wbcFormatToolParameter(" + source.split(
        "function wbcFormatToolParameter(", 1
    )[1].split("function wbcThinkingPhrases", 1)[0]
    timeline_source = "function wbcConfirmOptimisticMessage(" + source.split(
        "function wbcConfirmOptimisticMessage(", 1
    )[1].split("function wbcCurrentModel(", 1)[0]
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
eval({json.dumps(timeline_source)});
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

    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    selection_hydration = source.split(
        "var hydrationSequence = beginChatHydration(activeChatId);", 1
    )[1].split("model.getSubagents", 1)[0]
    settled_hydration = source.split(
        "onSettled: function (chatId) {", 1
    )[1].split("onResync:", 1)[0]
    assert "isCurrentChatHydration(activeChatId, hydrationSequence)" in selection_hydration
    assert "var hydrationSequence = beginChatHydration(chatId);" in settled_hydration
    assert "if (!isCurrentChatHydration(chatId, hydrationSequence)) return;" in settled_hydration


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
    assert "subscribeSummary: subscribeSummary" in source
    assert "runtimeEngine.subscribe(applyRuntimeSnapshot)" in source
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
    assert 'function alignFloatingBrowser(forceNativeSync, immediateNativeSync)' in source
    assert 'sideRect.left - floatingRect.left' in source
    assert 'paneRect.bottom + userOffsetY - floatingRect.bottom' in source
    assert 'source: "pip-context-panel-alignment"' in source
    assert 'event.detail.source === "pip-context-panel-alignment"' in source
    assert 'function publishNativeBounds(deferUntilMounted)' in source
    assert 'alignFloatingBrowser(true);' in source
    assert 'nativeBoundsCommitRaf = requestAnimationFrame' in source
    assert '--wbc-browser-pip-aligned-width' in source
    assert '--wbc-browser-pip-aligned-height' in source
    assert 'var maximumHeight = Math.max(0, Math.floor(paneRect.bottom - sideRect.bottom - 12));' in source
    assert 'var alignedHeight = Math.min(Math.round(alignedWidth * 3 / 4), maximumHeight);' in source
    assert 'var boundedOffsetY = Math.min(0, Math.max(minimumOffsetY, userOffsetY));' in source
    assert 'window.addEventListener("workbench:right-resize", onRightResize);' in source
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
    assert "WBC_ICONS.windowMinimize" not in source
    assert 'Array.isArray(displayBrowserState.tabs) && displayBrowserState.tabs.length === 0' in source
    assert 'hasNoBrowserTabs && effectiveMode === "pip"' in source
    assert 'action_id: "set_frame"' not in surface
    assert 'action_id: "maximize"' in surface
    assert "runModeTransition(onRestore, \"pip\")" in source
    assert "{WBC_ICONS.x}" in source
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


def _run_conversation_stick_sequence(steps):
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    function_source = "function wbcShouldStickToConversationBottom" + source.split(
        "function wbcShouldStickToConversationBottom", 1
    )[1].split("\nfunction WbcConversationNavigator", 1)[0]
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
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    active_tab_styles = styles.split("\n.browser-tab.active {", 1)[1].split("}", 1)[0]
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
    assert 'browserLabel("browser.fullscreen.active", "Playing in full screen")' in browser_view
    assert 'browserLabel("browser.fullscreen.external", "The video is playing in a separate full-screen window")' in browser_view
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


def test_browser_window_docks_above_composer_without_changing_its_normal_frame():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
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
    assert "var usage = Object.assign({}, (liveData && liveData.usage) || chat.usage || {}, runtimeUsage);" in overview
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
    assert handler.index("WbcVoice.stop()") < handler.index("model.deleteChat(chatId)")
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
    assert "window.ReactDOM.createPortal(" in rename_dialog
    assert 'document.querySelector(".workbench-shell") || document.body' in rename_dialog
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
    assert "transfer.setDragImage(" not in rail
    assert "function prepareRailDragImage(root, transfer, clientX, clientY)" in rail
    assert rail.count("prepareRailDragImage(") == 3
    assert "function wbcBuildRailCardDragPreview(root, extraClassName)" in source
    drag_preview_helper = source.split(
        "function wbcBuildRailCardDragPreview(root, extraClassName) {", 1
    )[1].split("\n}", 1)[0]
    assert 'host.style.overflow = "visible"' in drag_preview_helper
    assert 'host.style.isolation = "auto"' in drag_preview_helper
    assert 'clone.querySelectorAll(".wbc-chat-row-icon")' in drag_preview_helper
    assert 'icon.style.opacity = "1"' in drag_preview_helper
    assert '"wbc-split-return-target"' in drag_preview_helper
    assert 'wbcBuildRailCardDragPreview(root, "")' in rail
    assert 'document.body.appendChild(host)' in rail
    assert 'document.addEventListener("dragover", moveDragImage, true)' in rail
    assert "wbcHideNativeDragImage(transfer)" in rail
    assert "clearRailDragImage();" in rail
    assert ".wbc-native-chat-drag-image::before" in styles
    drag_image_css = styles.split(
        ".wbc-rail.wbc-native-chat-drag-image > .wbc-chat-card {", 1
    )[1].split("}", 1)[0]
    assert "background: var(--wb-card-bg-strong);" in drag_image_css
    assert "border-radius: 11px;" in drag_image_css
    assert "box-shadow:" in drag_image_css
    assert "event.altKey" in rail
    assert "onOpenDroppedChat={function (chatId)" in source
    assert "onDrop={handleChatDrop}" in main
    assert 'className="wbc-chat-open-drop-hint"' in main
    assert ".wbc-chat-card.dragging" in styles
    dragged_card_css = styles.split(
        ".wbc-chat-card.dragging {", 1
    )[1].split("}", 1)[0]
    assert "opacity: .22;" in dragged_card_css
    assert "transform: scale(.985);" in dragged_card_css
    lifted_card_css = styles.split(
        ".wbc-chat-card.wbc-split-card-lifted {", 1
    )[1].split("}", 1)[0]
    assert "opacity: 1;" in lifted_card_css
    assert "transform: translateY(-3px);" in lifted_card_css
    assert ".wbc-main.chat-drop-active" in styles
    drop_border_css = styles.split(
        ".wbc-main.chat-drop-active::after {", 1
    )[1].split("}", 1)[0]
    assert "inset: 58px 0 0;" in drop_border_css
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
    assert "workbench-chat.js?v=0.7.9" in index
    assert "workbench-i18n.js?v=0.7.9" in index


def test_task_answer_resume_uses_interrupt_not_pause_and_suppresses_cancel_error():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    answer = source.split("answer: function (questionId, optionText)", 1)[1].split(
        "promoteToPlan: function", 1
    )[0]
    header = source.split("function TaskHeader", 1)[1].split(
        "function TaskBriefCard", 1
    )[0]

    assert "interruptedRef.current = false" in answer
    assert 'if (interruptedRef.current || (err && err.name === "AbortError"))' in answer
    assert 'status === "running" || session.agentBusy ? controller.interrupt() : controller.pause()' in header


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
    saved = normal_completion.index('"type": "saved"')
    workspace_finalize = normal_completion.index("await _finalize_workspace_changes(")

    assert reply_done < finalizing < saved < workspace_finalize


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
    assert "runtimeFinalizing: !!runtime.finalizing" in source
    assert "!runtime.finalizing && index === activities.length - 1" in source
    assert 'wbcT("workbenchChat.finalizing", "Reply complete · saving results…")' in source


def test_workbench_permission_prompt_renders_every_scoped_option():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    prompt = source.split("function WbcQuestionPrompt", 1)[1].split(
        "function WbcErrorNotice", 1
    )[0]

    assert "options.length ? options.map" in prompt
    assert "wbc-question-protocol-error" in prompt
    assert "onAnswer(pq.id, wbcQuestionOptionValue(opt))" in prompt
    assert "options[options.length - 1]" not in prompt.split(") : (", 1)[0]

    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
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
    root = Path(__file__).resolve().parents[1]
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
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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

    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
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
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    answer = source.split("function answerQuestionForChat", 1)[1].split(
        "// Regenerate the last assistant reply", 1
    )[0]
    preview = source.split("function WbcConversationStatusPreview", 1)[1].split(
        "function WbcRail", 1
    )[0]

    assert "model.answerChat(chatId, questionId, optionText" in answer
    assert "runtimeEngine.update(chatId" in answer
    assert "setChats(function (previous)" in answer
    assert "chatCache.details[chatId]" in answer
    assert "return answerQuestionForChat(chatId" in answer
    assert "isPermissionQuestionKind(kind)" in preview
    assert "pending.allowCustom" in preview
    assert "onAnswer(pending.id, text, resumeMode)" in preview
    assert "wbcPermissionOptionLabel(option, index, options.length)" in preview
    assert "var actionOptions = options.length" in preview


def test_permission_option_labels_follow_backend_values_instead_of_position():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    labeler = source.split("function wbcPermissionOptionLabel", 1)[1].split(
        "function wbcPermissionQuestionText", 1
    )[0]

    assert "var semanticValues = option" in labeler
    assert "option.optionId, option.id, option.kind" in labeler
    assert '"允许这次"' in labeler
    assert '"本次会话内总是允许"' in labeler
    assert '"拒绝"' in labeler
    assert labeler.index('"允许这次"') < labeler.index("if (total <= 2)")


def test_agent_tab_hides_search_filter_and_header_install_button():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(
        encoding="utf-8"
    )
    panel = source.split("function ExtensionsPanel(", 1)[1].split(
        "function ShortcutsPanel(", 1
    )[0]

    assert '"agent"' in panel
    assert 'category !== "agent" && React.createElement("div", { className: "wb-extension-filter" }' in panel
    assert 'category !== "recommended" && category !== "agent" && React.createElement("button"' in panel
    assert 'category === "agent"\n      ? React.createElement(AgentTabPanel' in panel or 'category === "agent" ? React.createElement(AgentTabPanel' in panel


def test_composer_model_flyout_lists_agent_row_before_model_row():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    option_value = source.split("function wbcQuestionOptionValue(", 1)[1].split(
        "function wbcIsLiveAgentRequest", 1
    )[0]
    answer = source.split("function answerQuestionForChat(", 1)[1].split(
        "// Regenerate the last assistant reply", 1
    )[0]
    question_buttons = source.split("function WbcQuestionPrompt", 1)[1].split(
        "function WbcErrorNotice", 1
    )[0]

    assert "option.optionId" in option_value
    assert "option.optionId" in option_value.split("option.label", 1)[0]
    assert 'onAnswer(pq.id, wbcQuestionOptionValue(opt)' in question_buttons
    assert '{ type: "option", optionId: String(optionText || "") }' in answer


def test_quick_chat_inherits_agent_binding_without_picker():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-quick-chat.jsx").read_text(
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
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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


def test_permission_prompt_localizes_capability_ids_and_hides_internal_fingerprint():
    root = Path(__file__).resolve().parents[1]
    chat = (root / "src/webui/frontend/workbench-chat.jsx").read_text(encoding="utf-8")
    i18n = (root / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")
    model = (root / "src/webui/frontend/workbench-model.jsx").read_text(encoding="utf-8")
    runtime = (root / "src/cyrene/workbench/runtime.py").read_text(encoding="utf-8")

    assert "function wbcPermissionQuestionText(pending)" in chat
    assert "i18n.permissionQuestionText(pending, i18n.getLang())" in chat
    assert "function workbenchPermissionQuestionText(pending, lang)" in i18n
    assert '/^cyrene-(?:setting|lifecycle):/' in i18n
    assert "legacyTool" in i18n
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
    chat = (root / "src/webui/frontend/workbench-chat.jsx").read_text(encoding="utf-8")
    css = (root / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")
    i18n = (root / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")
    surface = (root / "src/webui/frontend/platform/ui-surface.jsx").read_text(encoding="utf-8")

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
    inbox_call = '<WbcInboxCard liveView={inboxView} hideTitle={true} />'
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
    assert 'className="wbc-context-empty-label"' in inbox_card
    assert "hideTitle && feed.length === 0" not in inbox_card
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
    assert 'className={"wbc-inbox-event-preview"' in inbox_card
    assert 'item.type === "tool_result" || item.type === "tool_activity"' in inbox_card
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
    assert inbox_card.index('className={"wbc-inbox-event-preview"') > inbox_card.index('className="wbc-inbox-event-meta"')
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
    voice_setting_helpers = overlay.split("function saveVoiceBooleanSetting", 1)[1].split(
        "function saveVoiceProfile", 1
    )[0]
    assert 'setVoiceNotice(t("settings.saved"))' not in voice_setting_helpers
    assert voice_setting_helpers.count('setVoiceNotice("");') == 3
    voice_save_actions_css = css.split(
        ".wb-voice-custom-fields > .wb-save-actions {", 1
    )[1].split("}", 1)[0]
    assert "justify-content: flex-end;" in voice_save_actions_css

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
        "environment_tools",
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
    assert load_effect.index("wbcPreserveLiveTimelineAnchors(") < load_effect.index("setSubagentData(payload)")
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

    assert '"wbc-chat-list workbench-integrated-rail-body" + (loading ? " is-loading" : "")' in source
    assert 'className="workbench-muted wbc-rail-loading" role="status"' in source
    assert '!loading && renderRailSection("recent"' in source
    assert "!loading && visibleGroupRailItems.length > 0" in source
    assert 'renderRailSection("groups"' in source
    loading_styles = styles.split(".wbc-chat-list.is-loading {", 1)[1].split("}", 1)[0]
    assert "align-items: center;" in loading_styles
    assert "justify-content: center;" in loading_styles


def test_every_workspace_sidebar_card_can_swipe_between_module_tabs():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )

    grid_markup = source.split('className={"workbench-grid integrated-sidebars"', 1)[1]
    assert "function handleSidebarModuleWheel(event)" in source
    sidebar_wheel = source.split("function handleSidebarModuleWheel(event) {", 1)[1].split(
        "function toggleWorkspaceSidebar()", 1
    )[0]
    assert 'target.closest(".workbench-integrated-rail, .workbench-sidebar-dock.is-persistent")' in sidebar_wheel
    assert 'var moduleOrder = ["schedule", "task", "chat", "knowledge", "memory"];' in sidebar_wheel
    assert "Math.abs(deltaX) <= Math.abs(deltaY) * 1.15" in sidebar_wheel
    assert "Math.abs(gesture.delta) < 44" in sidebar_wheel
    assert "gesture.lockedUntil = now + 420" in sidebar_wheel
    assert "handleOpenPage(moduleOrder[nextIndex])" in sidebar_wheel
    assert "onWheel={handleSidebarModuleWheel}" in grid_markup


def test_sidebar_account_card_has_balanced_vertical_spacing():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    account_css = styles.split(".workbench-rail-account {", 1)[1].split("}", 1)[0]
    assert "transform: translateY(5px);" in account_css
    collapsed_account_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail.is-collapsed .workbench-rail-account {",
        1,
    )[1].split("}", 1)[0]
    assert "transform: translateY(4px);" in collapsed_account_css
    collapsed_button_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail.is-collapsed .workbench-rail-account-button {",
        1,
    )[1].split("}", 1)[0]
    assert "grid-template-columns: 32px minmax(0, 0fr) 0fr;" in collapsed_button_css
    assert "justify-content: center;" not in collapsed_button_css
    assert "justify-items: center;" not in collapsed_button_css
    assert "padding: 3px 4px;" in collapsed_button_css
    assert ".workbench-rail-account-button .workbench-account-avatar {" in styles
    base_avatar_css = styles.split(
        ".workbench-rail-account-button .workbench-account-avatar {",
        1,
    )[1].split("}", 1)[0]
    assert "transform: translateX(0);" in base_avatar_css
    assert "transition: transform var(--wb-sidebar-motion-duration)" in base_avatar_css
    collapsed_avatar_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail.is-collapsed .workbench-rail-account-button .workbench-account-avatar {",
        1,
    )[1].split("}", 1)[0]
    assert "transform: translateX(2px);" in collapsed_avatar_css


def test_collapsed_workspace_headers_center_their_expand_button_and_hide_task_empty_state():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    collapsed_head_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail.is-collapsed .workbench-integrated-rail-head {",
        1,
    )[1].split("}", 1)[0]
    assert "justify-content: center;" in collapsed_head_css
    assert "gap: 0;" in collapsed_head_css
    assert "padding: 0;" in collapsed_head_css
    task_rail = source.split("function TaskRail(", 1)[1].split(
        "function TaskBoard(", 1
    )[0]
    assert 'className="wb-task-rail-empty"' in task_rail
    assert 't("rail.emptyTasksHint"' in task_rail
    assert task_rail.index('className="wb-task-rail-empty"') > task_rail.index("workbench-integrated-rail-body")
    collapsed_body_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail.is-collapsed .workbench-integrated-rail-body {",
        1,
    )[1].split("}", 1)[0]
    assert "visibility: hidden;" in collapsed_body_css


def test_task_rail_delete_menu_is_above_the_outside_click_scrim():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    open_actions_css = styles.split(
        ".workbench-task-card.menu-open .wb-card-actions {", 1
    )[1].split("}", 1)[0]
    assert "z-index: 200;" in open_actions_css


def test_task_menus_use_document_click_away_without_a_blocking_scrim():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )

    task_rail = source.split("function TaskRail(", 1)[1].split(
        "function TaskBoard(", 1
    )[0]
    task_board = source.split("function TaskBoard(", 1)[1].split(
        "function TaskBoardCard(", 1
    )[0]
    for component in (task_rail, task_board):
        assert 'document.addEventListener("pointerdown", closeOnOutside, true);' in component
        assert 'target.closest(".wb-card-menu")' in component
        assert 'className="wb-card-menu-scrim"' not in component


def test_expanded_task_detail_rail_uses_its_own_lane_and_hides_scrollbar():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    detail_grid_css = styles.split(
        ".workbench-grid.integrated-sidebars.is-task-detail {", 1
    )[1].split("}", 1)[0]
    assert (
        "grid-template-columns: 300px minmax(420px, 1fr) "
        "var(--wb-right-w, 350px);" in detail_grid_css
    )
    collapsed_detail_grid_css = styles.split(
        ".workbench-grid.integrated-sidebars.rail-collapsed.is-task-detail {", 1
    )[1].split("}", 1)[0]
    assert (
        "grid-template-columns: 64px minmax(420px, 1fr) "
        "var(--wb-right-w, 350px);" in collapsed_detail_grid_css
    )
    compact_detail_grid_css = styles.rsplit(
        "@media (max-width: 1320px) {", 1
    )[1].split("}", 1)[0]
    assert (
        "grid-template-columns: 300px minmax(360px, 1fr) "
        "var(--wb-right-w, 280px);" in compact_detail_grid_css
    )
    rail_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail {", 1
    )[1].split("}", 1)[0]
    assert "z-index: 21;" in rail_css
    assert "width: calc(var(--wb-rail-w) - 16px);" in rail_css
    task_list_css = styles.rsplit(".workbench-task-list {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in task_list_css
    assert "scrollbar-width: none;" in task_list_css
    assert ".workbench-task-list::-webkit-scrollbar {" in styles


def test_task_and_chat_empty_rail_states_center_vertically():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    chat_source = (
        root / "src" / "webui" / "frontend" / "workbench-chat.jsx"
    ).read_text(encoding="utf-8")

    task_empty_css = styles.split(".workbench-task-list.is-empty {", 1)[1].split(
        "}", 1
    )[0]
    assert "flex-direction: column;" in task_empty_css
    assert "justify-content: center;" in task_empty_css
    task_card_css = styles.split(".wb-task-rail-empty {", 1)[1].split("}", 1)[0]
    assert "margin-top: 0;" in task_card_css

    assert '!loading && visibleRailItemCount === 0 ? " is-empty" : ""' in chat_source
    chat_empty_css = styles.split(
        ".wbc-chat-list.is-empty .wbc-chat-list-primary {", 1
    )[1].split("}", 1)[0]
    assert "display: flex;" in chat_empty_css
    assert "align-items: center;" in chat_empty_css
    assert "justify-content: center;" in chat_empty_css


def test_sidebar_account_menu_keeps_codex_and_custom_model_limits_independent():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )

    account = source.split("function WorkbenchRailAccount(", 1)[1].split(
        "function WorkbenchSidebarDock(", 1
    )[0]
    quota_loader = account.split("function fetchCodexQuotaSummary() {", 1)[1].split(
        "function formatTimeDiff(", 1
    )[0]
    assert 'fetch("/api/settings/openai-oauth/limits")' in quota_loader
    assert 'fetch("/api/settings/models")' in quota_loader
    assert 'primary.provider !== "codex_oauth"' in quota_loader
    assert "codexQuotaState.primary && codexQuotaState.connected && codexQuotaState.windows.length > 0" in account
    assert "codexQuotaState.windows.map" in account
    assert 'fetch("/api/budget/status")' in account
    assert "budgetState && budgetState.monthly_budget > 0" in account
    assert 'className="wb-budget-progress-bar"' in account
    config_store = (root / "src" / "cyrene" / "runtime" / "config_store.py").read_text(
        encoding="utf-8"
    )
    assert '"budget_enabled": False' in config_store
    assert '"budget_monthly": 50.0' in config_store
    assert '"codex_budget_enabled": True' in config_store


def test_workbench_keeps_one_persistent_module_dock_across_workspace_switches():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    library = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    schedule = (root / "src" / "webui" / "frontend" / "workbench-schedule.jsx").read_text(
        encoding="utf-8"
    )
    memory = (root / "src" / "webui" / "frontend" / "workbench-memory.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    grid_markup = source.split('className={"workbench-grid integrated-sidebars"', 1)[1]
    assert "<WorkbenchModuleRail" not in grid_markup
    assert "function WorkbenchSidebarDock(" in source
    module_dock = source.split("function WorkbenchSidebarDock(", 1)[1].split(
        "// Temporarily keep sign-out", 1
    )[0]
    for module_id in ("task", "chat", "knowledge", "schedule", "memory"):
        assert f'id: "{module_id}"' in module_dock
    dock_item_positions = [
        module_dock.index(f'id: "{module_id}"')
        for module_id in ("schedule", "task", "chat", "knowledge", "memory")
    ]
    assert dock_item_positions == sorted(dock_item_positions)
    assert "function renderSidebarDockSlot()" in source
    assert 'return <div className="workbench-sidebar-dock-slot" aria-hidden="true" />;' in source
    assert "var [railCollapsed, setRailCollapsed] = useWorkbenchState" in source
    assert "function toggleWorkspaceSidebar()" in source
    assert 'localStorage.setItem("wb-rail-collapsed", next ? "1" : "0")' in source
    assert '(railCollapsed ? " rail-collapsed" : "")' in source
    assert grid_markup.count("<WorkbenchSidebarDock") == 1
    assert "persistent={true}" in grid_markup
    assert "collapsed={railCollapsed}" in grid_markup
    assert "moduleDock: isChat ? renderSidebarDockSlot() : null" in source
    assert "moduleDock: isKnowledge ? renderSidebarDockSlot() : null" in source
    assert "moduleDock: isSchedule ? renderSidebarDockSlot() : null" in source
    assert "moduleDock: isMemory ? renderSidebarDockSlot() : null" in source
    assert "moduleDock={!isModulePage ? renderSidebarDockSlot() : null}" in source
    assert "navCollapsed: railCollapsed" in source
    assert "sidebarCollapsed: railCollapsed" in source
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
    assert "width: calc(var(--wb-rail-w) - 46px);" in persistent_dock_css
    assert ".workbench-sidebar-dock.is-persistent:has(.workbench-module-account-menu) {" in styles
    assert ".workbench-sidebar-dock::before {" in styles
    dock_separator = styles.split(".workbench-sidebar-dock::before {", 1)[1].split("}", 1)[0]
    assert "content: none;" in dock_separator
    assert ".workbench-integrated-rail {" in styles
    integrated_grid_css = styles.split(".workbench-grid.integrated-sidebars {", 1)[1].split("}", 1)[0]
    integrated_rail_css = styles.split(".workbench-integrated-rail {", 1)[1].split("}", 1)[0]
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
    assert "margin: 12px 4px 12px 12px;" in integrated_rail_css
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
    assert 'className="workbench-integrated-rail-head workbench-integrated-rail-search-head"' in source
    assert 'className={"workbench-task-list workbench-integrated-rail-body"' in source
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
    account_button_css = styles.split(".workbench-rail-account-button {", 1)[1].split("}", 1)[0]
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
    assert "height: 46px;" in account_button_css
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
    account_row_css = styles.split(".workbench-rail-account {", 1)[1].split("}", 1)[0]
    assert "grid-row: 2;" in account_row_css
    assert "height var(--wb-sidebar-motion-duration) var(--wb-sidebar-motion-ease)," in account_row_css
    assert "transform var(--wb-sidebar-motion-duration) var(--wb-sidebar-motion-ease);" in account_row_css
    assert "grid-template-columns var(--wb-sidebar-motion-duration) var(--wb-sidebar-motion-ease)" in account_button_css
    collapsed_account_button_css = styles.split(
        ".workbench-grid.integrated-sidebars > .workbench-sidebar-dock.is-persistent.is-collapsed .workbench-rail-account-button {",
        1,
    )[1].split("}", 1)[0]
    assert "width: 100%;" in collapsed_account_button_css
    assert "grid-template-columns: 32px minmax(0, 0fr) 0fr;" in collapsed_account_button_css
    assert "padding: 3px 4px;" in collapsed_account_button_css
    assert "function WorkbenchRailAccount(" in source
    assert "<WorkbenchRailAccount" in module_dock
    assert 'className="workbench-rail-account-button"' in source
    account_name_css = styles.split(".workbench-rail-account-summary b {", 1)[1].split("}", 1)[0]
    account_model_css = styles.split(".workbench-rail-account-summary small {", 2)[2].split("}", 1)[0]
    assert "font-size: calc(14px * var(--wb-ui-font-scale, 1));" in account_name_css
    assert "font-size: calc(11.5px * var(--wb-ui-font-scale, 1));" in account_model_css
    assert 'className="workbench-account-menu workbench-module-account-menu"' in source
    assert 't("settings.codexQuota")' in source
    assert 't("rail.settings")' in source
    assert 't("rail.profile")' in source
    assert ".workbench-rail-account {" in styles
    assert ".workbench-module-account-menu {" in styles
    raised_menu_css = styles.split(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail:has(.workbench-module-account-menu) {",
        1,
    )[1].split("}", 1)[0]
    assert "z-index: 90;" in raised_menu_css
    assert "overflow: visible;" in raised_menu_css
    project_menu_styles = styles.split(".workbench-top-project-menu {", 1)[1].split("}", 1)[0]
    account_menu_styles = styles.split(".workbench-module-account-menu {", 1)[1].split("}", 1)[0]
    assert "background: var(--wb-flyout-bg);" in project_menu_styles
    assert "background: var(--wb-card-bg-strong);" in account_menu_styles
    assert "backdrop-filter: none;" in project_menu_styles
    assert "backdrop-filter: none;" in account_menu_styles
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


def test_chat_rail_show_all_expands_recent_items_without_removed_filter_state():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    assert 'var [showAllRecent, setShowAllRecent] = useWbcState(false);' in source
    assert "showAllRecent\n    ? recentRailItems\n    : recentRailItems.slice(0, recentOverviewLimit)" in source
    assert "setShowAllRecent(true);" in source
    assert "setRailFilter" not in source


def test_active_module_dock_item_is_not_a_toggle_back_to_tasks():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )

    handler = source.split("function handleOpenPage(page) {", 1)[1].split(
        "function toggleWorkspaceSidebar()", 1
    )[0]
    assert 'if (page === "task") {' in handler
    assert "if (!fullPage) return;" in handler
    assert "if (fullPage === page) return;" in handler
    assert "setFullPage(page);" in handler
    assert "prev === page ? null : page" not in handler


def test_knowledge_sidebar_is_persistent_at_compact_desktop_widths():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench-library.css").read_text(
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
    assert 'var bubbleClassName = "wbc-bubble" + (hasInlineImage ? " with-inline-image" : "");' in source
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
    image_bubble_rule = styles.split(
        ".wbc-msg.user .wbc-bubble.with-inline-image {", 1
    )[1].split("}", 1)[0]
    assert "width: fit-content;" in image_bubble_rule
    assert "max-width: 100%;" in image_bubble_rule
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


def test_workbench_execution_card_uses_collapsible_activity_summary():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

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
    assert source.count("var activityHasReasoning = !!String(latestActivity && latestActivity.reasoning") == 2
    assert source.count("appendActivity(closeActivityTimeline(latest), { createdAt: eventAt })") == 2
    assert 'className="wbc-trace-list wbc-trace-timeline"' in source
    assert "var seen = new Set();" in source
    assert "seen.has(key)" in source

    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )
    assert '"workbenchChat.thinkingProcess": "Thinking process"' in i18n
    assert '"workbenchChat.thinkingProcess": "思考过程"' in i18n

    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
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
    assert "{previewText ? <small>（{previewText}）</small> : null}" in source
    assert ".wbc-trace-details .wbc-trace-mark" not in styles
    assert 'className="wbc-trace-mark"' in source
    assert "编辑了文件" in (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    assert '"workbenchChat.traceAction.usedSkill": "使用了技能工具"' in i18n
    assert '"workbenchChat.traceAction.conjunction": "并"' in i18n
    assert '"workbenchChat.traceAction.executed"' not in i18n


def test_workbench_trace_timeline_removes_blank_lines_and_interleaves_tools():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
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


def test_workbench_chat_retry_clears_model_output_before_start_and_reconciles_terminal_event():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    selection_helper = source.split("function wbcRetryTurnSelection(chat, messageId) {", 1)[1].split(
        "function wbcClearModelOutputForRetry", 1
    )[0]
    clear_helper = source.split("function wbcClearModelOutputForRetry(chat, messageId) {", 1)[1].split(
        "function wbcPreserveLiveTimelineAnchors", 1
    )[0]
    retry_block = source.split("function handleRetryMessage(messageId) {", 1)[1].split(
        "function handleEditMessage", 1
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
    assert "chat.messages.slice(0, selection.userIndex + 1).concat(chat.messages.slice(selection.endIndex))" in clear_helper
    assert "wbcClearModelOutputForRetry(cachedChat, retryMessageId)" in retry_block
    assert "wbcClearModelOutputForRetry(prev, retryMessageId)" in retry_block
    assert "setRetryClearingMessageIds(selection.outputIds);" in retry_block
    assert "retrySuppressedTurnRef.current = suppressedTurn;" in retry_block
    assert "setRetrySuppressedTurn(suppressedTurn);" in retry_block
    assert "retryClearCommitRef.current = startRetryAfterClear;" in retry_block
    assert "setTimeout(startRetryAfterClear" not in retry_block
    assert 'event.animationName === "wbc-retry-output-clear"' in source
    assert "onRetryClearAnimationEnd();" in source
    assert 'runtimeEngine.start(retryChatId, { retry: true, mode: retryMode }, model);' in retry_block
    assert 'className={retryClearing ? "retry-clearing" : ""}' in source
    assert "retrySuppressedIds.has(String(message && message.id || \"\"))" in source
    retry_truncate = source.split("onRetryTruncate: function (chatId, truncateInfo) {", 1)[1].split(
        "onReplyStream:", 1
    )[0]
    assert "retrySuppressedTurnRef.current" in retry_truncate
    assert ".concat(locallySuppressedIds)" in retry_truncate
    assert 'retrySuppressedTurnRef.current = { chatId: "", messageIds: [] };' in retry_truncate
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
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
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    runtime_error = source.split(
        'onError: function (chatId, err) {', 1
    )[1].split("onSettled:", 1)[0]
    main_props = source.split("<WbcMain", 1)[1].split("/>", 1)[0]

    assert 'setErrorKind("message");' in runtime_error
    assert 'onRetry={errorKind === "message" ? handleRetryMessage : (errorKind === "memory" ? handleGenerateMemory : retryLoad)}' in main_props
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
    assert "if (!runtime.text && !(runtime.artifacts && runtime.artifacts.length)) return null;" in live_message
    assert "<WbcLiveAgentArtifacts files={runtime.artifacts}" in live_message
    assert "function wbcRuntimeTimelineMessages(runtime, options)" in chat
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
    assert "!msg.runtimeActivityActive\n              && activityEntries.length === 0" in chat
    assert "wbcRuntimeSegmentMessages(runtime).concat(wbcRuntimeTimelineMessages(runtime, { showReasoningPlaceholder }))" in chat
    assert "if (msg.runtimeActivity || msg.activityCard)" in chat
    assert "if (msg.runtimeHeartbeat) {\n            return null;" in chat
    assert "if (item.runtimeHeartbeat) {\n          return null;" in chat
    assert "<WbcHeartbeat startedAt=" not in chat
    assert "activity={activity}" in chat
    assert "reasoning={visibleReasoning}" in activity_card
    assert "useWbcState(false)" not in live_message
    assert "useWbcState(0)" not in live_message
    assert "trace: hasLiveActivities ? []" in chat
    assert 'summaryRunning ? <span className="wb-spinner small" aria-hidden="true" /> : null' in trace_card
    assert 'isRunning ? <span className="wb-spinner small" /> : WBC_ICONS.check' in trace_card
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
    assert 'className="wbc-model-menu-value">{modelName}</span>' in chat
    assert ".wbc-model-menu-model-name" not in styles


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
    assert "if (label && label !== key) return label;" in chat
    assert 'wbcT("toolName." + toolKey, toolKey)' in chat
    assert '"workbenchChat.ctxBlock.skills.learned": "Learned skills"' in i18n
    assert '"workbenchChat.ctxBlock.skills.learned": "已学习技能"' in i18n
    assert (
        '"workbenchChat.ctxBlock.client.renderer.workbench": '
        '"Workbench response format"'
    ) in i18n
    assert (
        '"workbenchChat.ctxBlock.client.renderer.workbench": '
        '"Workbench 响应格式"'
    ) in i18n
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
  appSnapshot: window.WorkbenchI18n.toolName("AppUISnapshot", "zh"),
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
        "appSnapshot": "快照应用界面",
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
    assert "onClick={function () { onRetryMessage(msg.id); }}" in user_message
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
    artifacts_tab = source.split("function ArtifactsTab", 1)[1].split(
        "function SideSection", 1
    )[0]
    assert 'className="wbc-artifact-list wb-task-artifact-list"' in artifacts_tab
    assert 'className="wbc-artifact-list-row wb-task-artifact-download"' in artifacts_tab
    assert "<SideSection" not in artifacts_tab
    assert 'download={artifact.name || true}' in source
    assert '"/artifacts/" + encodeURIComponent(artifact.id) + "/download"' in source
    assert "artifact.type !== \"file_change\"" in model
    assert 'name: "task-summary.md"' not in model
    assert ".wb-task-detail-tab-panel:last-child.open" in styles
    assert ".wb-task-artifact-list" in styles
    assert '@router.get("/api/task-sessions/{session_id}/artifacts/{artifact_id}/download")' in routes
    assert "_workbench_artifact_download_target(project, session, artifact_id)" in routes


def test_workbench_task_details_reuse_floating_animated_accordion():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    i18n = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    panel = source.split("function RightContextPanel", 1)[1].split("function ReflectionSection", 1)[0]
    assert 'className="workbench-right-panel wb-floating-detail-shell wb-task-detail-shell"' in panel
    assert 'className="wb-floating-detail-card wb-task-detail-card"' in panel
    assert '<WbColResizer trackGutter surfaceId="task-detail" />' in panel
    assert 'className="wb-detail-accordion wb-task-detail-tabs"' in panel
    assert 'aria-expanded={expanded}' in panel
    assert 'onTabChange(expanded ? "" : item.id)' in panel
    assert 'artifacts.length ? [{ id: "artifacts"' in panel
    assert 'tab === "artifacts" && !artifacts.length' in panel
    assert 'className={"wb-detail-accordion-panel wb-task-detail-tab-panel"' in panel
    assert 'className="workbench-right-body"' in panel
    files_tab = source.split("function FilesTab", 1)[1].split("function LogsTab", 1)[0]
    logs_tab = source.split("function LogsTab", 1)[1].split("function AcceptanceTab", 1)[0]
    acceptance_tab = source.split("function AcceptanceTab", 1)[1].split("function ArtifactsTab", 1)[0]
    context_tab = source.split("function ContextTab", 1)[1].split("function FilesTab", 1)[0]
    assert "<SideSection" not in files_tab
    assert "<SideSection" not in logs_tab
    assert "<SideSection" not in acceptance_tab
    assert 'className="workbench-side-stack wb-task-context-tab"' in context_tab
    assert 'className="wb-task-overview-meta"' in context_tab
    assert 'className="wb-task-context-goal"' in context_tab
    assert 'className="workbench-muted wb-task-context-empty"' in context_tab
    assert "activeBodyRef.current.scrollTop = 0" in panel
    assert ".workbench-grid.integrated-sidebars.is-task-detail .workbench-main" in styles
    assert ".workbench-grid.integrated-sidebars.is-task-detail .wb-task-detail-shell" in styles
    task_main_css = styles.split(
        ".workbench-grid.integrated-sidebars.is-task-detail .workbench-main {", 1
    )[1].split("}", 1)[0]
    task_shell_css = styles.split(
        ".workbench-grid.integrated-sidebars.is-task-detail .wb-task-detail-shell {", 1
    )[1].split("}", 1)[0]
    task_card_css = styles.split(".wb-task-detail-card {", 1)[1].split("}", 1)[0]
    assert "height: calc(100% - 24px);" in task_main_css
    assert "max-height: calc(100% - 24px);" in task_main_css
    assert "margin: 12px 8px;" in task_main_css
    assert "border: var(--wb-floating-rail-border);" in task_main_css
    assert "border-radius: var(--wb-floating-rail-radius);" in task_main_css
    assert "background: var(--wb-floating-rail-bg);" in task_main_css
    assert "box-shadow: var(--wb-floating-rail-shadow);" in task_main_css
    assert "padding: 12px 12px 12px 4px;" in task_shell_css
    assert "overflow: visible;" in task_shell_css
    assert "height: auto;" in task_card_css
    assert "max-height: 100%;" in task_card_css
    assert "overflow: visible;" in task_card_css
    assert ".wb-task-detail-card > .wb-col-resizer.track-gutter" in styles
    shared_grip_css = styles.split(
        ".wbc-side-card > .wb-col-resizer.track-gutter,", 1
    )[1].split("}", 1)[0]
    shared_grip_visual_css = styles.split(
        ".wbc-side-card > .wb-col-resizer.track-gutter::after,", 1
    )[1].split("}", 1)[0]
    assert ".wb-task-detail-card > .wb-col-resizer.track-gutter" in shared_grip_css
    assert "left: -12px;" in shared_grip_css
    assert "width: 12px;" in shared_grip_css
    assert ".wb-task-detail-card > .wb-col-resizer.track-gutter::after" in shared_grip_visual_css
    assert "top: 50%;" in shared_grip_visual_css
    assert "left: calc(50% - 1px);" in shared_grip_visual_css
    assert "width: 4px;" in shared_grip_visual_css
    assert "height: 72px;" in shared_grip_visual_css
    assert "opacity: 0;" in shared_grip_visual_css
    assert ".wb-task-detail-tab-panel.open" in styles
    assert "max-height: none;" in styles
    assert ".wb-task-detail-card .workbench-side-section + .workbench-side-section" in styles
    assert ".wb-task-overview-meta" in styles
    assert ".wb-task-detail-card .wb-task-context-tab" in styles
    assert "@container (min-width: 430px)" in styles
    assert ".wb-task-context-empty" in styles
    assert ".wb-task-detail-card .wb-accept-toggle:hover" in styles
    assert "background: var(--wb-row-hover-bg);" in styles
    assert ".wb-acceptance-summary" in styles
    assert ".wb-acceptance-empty" in styles
    assert 'className="wb-acceptance-list"' in acceptance_tab
    assert 'className="wb-empty-action wb-acceptance-empty"' in acceptance_tab
    assert 'html[data-theme="dark"] .wb-task-detail-card' in styles
    assert '"task.side.detailPanel": "Task details"' in i18n
    assert '"task.side.detailPanel": "任务详情"' in i18n
    assert "workbench.css?v=0.7.9" in index


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
    assert "workbench.css?v=0.7.9" in index


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
    assert 'settingsFetch("/api/wechat/status")' in settings
    assert 'settingsFetch("/api/wechat/qr-login"' in settings
    assert 'settingsFetch("/api/wechat/poll-login"' in settings
    assert 'settingsFetch("/api/wechat/start"' in settings
    assert 'settingsFetch("/api/wechat/stop"' in settings
    assert "result.qrcode_image || result.qrcode_img" in settings
    assert "WECHAT_BOT_TOKEN" not in settings
    assert '"settings.wechatScanConnect": "扫描二维码连接"' in translations
    assert ".wb-wechat-qr-overlay" in styles
    assert "settings-overlay.js?v=0.7.9" in index


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


def test_topbar_theme_toggle_persists_to_the_appearance_namespace():
    root = Path(__file__).resolve().parent.parent
    bootstrap = (root / "src/webui/frontend/entry/bootstrap.jsx").read_text(encoding="utf-8")

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
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

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


def test_backup_actions_use_native_file_pickers_and_comfortable_density_only():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    settings = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")
    bootstrap = (root / "src" / "webui" / "frontend" / "entry" / "bootstrap.jsx").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")
    route = (root / "src" / "route" / "backup.py").read_text(encoding="utf-8")
    session_route = (root / "src" / "route" / "agent" / "sessions.py").read_text(encoding="utf-8")

    assert "ipcMain.handle('dialog:pick-backup-save-path'" in main
    assert "ipcMain.handle('dialog:pick-backup-file'" in main
    assert "dialog.showSaveDialog" in main
    assert "filters: [{ name: 'Cyrene backup', extensions: ['zip'] }]" in main
    assert "ipcRenderer.invoke('dialog:pick-backup-save-path'" in preload
    assert "ipcRenderer.invoke('dialog:pick-backup-file'" in preload
    assert "await bridge.pickBackupSavePath" in settings
    assert "await bridge.pickBackupFile" in settings
    assert 'JSON.stringify({ path: selection.path })' in settings
    assert 't("settings.backupRestoreBtn")' in settings
    assert 't("settings.backupHint")' in settings
    assert 'var [exportSids, setExportSids] = useStateSt([])' in settings
    assert 'settingsFetch("/api/workbench/chats")' in settings
    assert "(workbenchExportSessions || []).concat(dataState.sessions || [])" in settings
    assert "exportSessions.map(function (s)" in settings
    assert 'className: "wb-export-session-list"' in settings
    assert 'type: "checkbox", checked: selected' in settings
    assert 't("settings.sessionExportHint")' in settings
    assert 'exportSids.forEach(function (sessionId)' in settings
    assert 'target_path=target_path or None' in route
    assert "from cyrene.workbench.chat import get_workbench_chat" in session_route
    assert "chat = await asyncio.to_thread(get_workbench_chat, session_id)" in session_route

    assert 'document.documentElement.dataset.density = "cozy"' in settings
    assert 'localStorage.removeItem("cyrene-tweak-density")' in settings
    assert 'document.documentElement.dataset.density = "cozy"' in bootstrap
    assert 'document.documentElement.dataset.density = "cozy"' in index
    assert 'FieldRow(t("settings.density")' not in settings


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
    assert "if (!sessionMenu && !resourceMenu)" in workbench
    assert "if (!overflowMenu && !hoverPreview) return undefined;" in workbench
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


def test_browser_snapshot_filters_non_interactable_page_nodes():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    browser = (root / "src" / "cyrene" / "browser.py").read_text(encoding="utf-8")

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


def test_agent_browser_tabs_are_owned_reused_and_finalized_per_round():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    browser = (root / "src" / "cyrene" / "browser.py").read_text(encoding="utf-8")
    coordinator = (root / "src" / "cyrene" / "agent" / "coordinator.py").read_text(encoding="utf-8")

    assert "this.activeAgentRoundId = ''" in main
    assert "this.agentOwnedTabIdsByRound = new Map()" in main
    assert "_recordAgentTab(tab, roundId)" in main
    assert "agentCreated: Boolean(String(agentOwnerRoundId" in main
    assert "beginAgentRound(roundId)" in main
    assert "finishAgentRound(roundId)" in main
    assert "this._reduceAgentTabs(this._agentTabs(), { activateKept: true })" in main
    assert "tab.agentOwnerRoundId === normalized" in main
    assert "agentRequest: true" in main
    assert "if (method === 'finishRound')" in main
    assert "opener && opener.agentClickInFlight" in main
    assert '"finishRound"' in browser
    assert "finish_electron_browser_round(_current_session_id.get(), round_id)" in coordinator


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
    assert "[wsDir, projectWorkspacePath].concat(wsHistory).forEach" in chat
    assert "workspaceOptions.push({ path: normalized, isDefault: normalized === projectWorkspacePath })" in chat
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
    assert 'var nextOverride = String(chat && chat.workspaceOverride || "").trim()' in chat
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
    assert "workspaceOverride: workspaceOverride," in chat
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
    assert "var personaOn = soulActive !== false;" in chat
    assert "var workspaceOn = workspaceActive !== false;" in chat
    assert "if (Number(catalog.revision) < 0) return;" in chat
    assert "Session summaries now carry this field." in chat


def test_workbench_tools_menu_combines_content_commands_and_long_workspace_paths():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")

    tools_rule = styles.split(".wbc-tools-menu {", 1)[1].split("}", 1)[0]
    content_rule = styles.split(".wbc-tools-content-list {", 1)[1].split("}", 1)[0]
    command_rule = styles.split(".wbc-tools-command-grid {", 1)[1].split("}", 1)[0]
    command_section_rule = styles.split(".wbc-tools-commands {", 1)[1].split("}", 1)[0]

    assert "width: min(260px, calc(100cqw - 58px));" in tools_rule
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
    assert 'className={"wbc-composer-icon wbc-tools-trigger"' in chat
    assert 'enabledContentCount > 0 ? " has-content" : ""' in chat
    assert 'className="wbc-tools-trigger-count"' in chat
    assert ".wbc-tools-trigger.has-content {" in styles
    assert "border-radius: 999px;" in styles.split(".wbc-tools-trigger.has-content {", 1)[1].split("}", 1)[0]
    assert 'className={"wbc-send"' in chat
    assert ".wbc-send span" not in styles
    assert "transform: none;" in styles
    assert "workbench-chat.js?v=0.7.9" in index


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
    assert "workbench-model.js?v=0.7.9" in index
    assert "workbench.js?v=0.7.9" in index


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
    assert "settingsFetch(\"/api/settings/models\").then(readSettingsResponse)" in source
    assert "}).then(readSettingsResponse).then(function (p)" in save_block
    assert "p.custom_models || norm" in save_block
    assert "p.vision_models || p.vision_candidates || vNorm" in save_block
    assert "settings-overlay.js?v=0.7.9" in index


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


def test_settings_models_use_the_shared_settings_typography():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

    typography = styles.split(
        "/* Models is a Settings page, not a code editor.", 1
    )[1].split(".workbench-shell .wb-text-size-sample", 1)[0]
    assert ".settings-overlay .wb-models-panel" in typography
    assert "font-family: var(--wb-font) !important;" in typography
    assert "font-size: calc(14px * var(--wb-ui-font-scale)) !important;" in typography
    assert "font-size: calc(12px * var(--wb-ui-font-scale)) !important;" in typography


def test_workbench_task_composer_reuses_chat_voice_input_flow():
    root = Path(__file__).resolve().parent.parent
    workbench = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")

    composer = workbench.split("function TaskComposer(", 1)[1].split(
        "function ComposerDisclaimer", 1
    )[0]
    assert "WbcVoice.subscribe(setVoiceSnapshot)" in composer
    assert "wbcStartVoiceRecorder" in composer
    assert "wbcTranscribeVoiceBlob(blob)" in composer
    assert "voiceSnapshot.status.auto_send_after_asr" in composer
    assert 'ComposerBrowserIcon name="microphone"' in composer
    assert 'className={"wb-composer-icon wbc-composer-icon wbc-voice-input"' in composer
    assert "function wbcTranscribeVoiceBlob(blob)" in chat


def test_workbench_task_composer_matches_chat_floating_card_material():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    task_box = styles.split(".workbench-composer-box {", 1)[1].split("}", 1)[0]
    task_header = source.split("function TaskHeader", 1)[1].split(
        "function headerMenuActions", 1
    )[0]
    task_header_layout = styles.split(".workbench-task-header {", 1)[1].split("}", 1)[0]
    task_header_sticky = styles.split(".workbench-task-header-sticky {", 1)[1].split("}", 1)[0]
    task_header_corner_masks = styles.split(
        ".workbench-task-header-sticky::before,\n.workbench-task-header-sticky::after {", 1
    )[1].split("}", 1)[0]
    task_header_left_mask = styles.split(".workbench-task-header-sticky::before {", 1)[1].split("}", 1)[0]
    task_header_right_mask = styles.rsplit(".workbench-task-header-sticky::after {", 1)[1].split("}", 1)[0]
    chat_box = styles.split(".wbc-composer-box {", 1)[1].split("}", 1)[0]
    chat_actions = styles.split("\n.wbc-composer-actions {", 1)[1].split("}", 1)[0]
    disclaimer = styles.split(".wb-composer-disclaimer {", 1)[1].split("}", 1)[0]

    assert ".wbc-conversation-split::after" not in styles
    assert ".workbench-grid.is-task-detail .workbench-main::after" not in styles
    for declaration in (
        "border-radius: 14px;",
        "background: color-mix(in srgb, var(--wb-card-bg) 72%, transparent);",
        "blur(18px) saturate(120%) contrast(102%)",
    ):
        assert declaration in task_box
        assert declaration in chat_box
    assert "width: fit-content;" in disclaimer
    assert "max-width: calc(100% - 24px);" in disclaimer
    assert "border: 0;" in disclaimer
    assert "border-radius: 14px;" in disclaimer
    assert "background: color-mix(in srgb, var(--wb-card-bg) 72%, transparent);" in disclaimer
    assert "blur(18px) saturate(120%) contrast(102%)" in disclaimer
    assert "border-top: 0;" in chat_actions
    assert 'className="workbench-composer wbc-composer"' in source
    assert 'className="workbench-composer-box wbc-composer-box"' in source
    assert 'className="workbench-task-header workbench-composer-box"' in task_header
    assert 'className="wb-th-main"' in task_header
    assert "position: sticky;" in task_header_sticky
    assert "top: 0;" in task_header_sticky
    assert "isolation: isolate;" in task_header_sticky
    assert "position: relative;" in task_header_layout
    assert "z-index: 1;" in task_header_layout
    assert "padding: 14px 16px;" in task_header_layout
    assert "width: 14px;" in task_header_corner_masks
    assert "height: 14px;" in task_header_corner_masks
    assert "background: var(--wb-floating-rail-bg, var(--wb-main-bg));" in task_header_corner_masks
    assert "right: 0;" not in task_header_corner_masks
    assert "left: 0;" not in task_header_corner_masks
    assert "radial-gradient(circle at 100% 100%" in task_header_left_mask
    assert "radial-gradient(circle at 0 100%" in task_header_right_mask
    task_main = source.split("function TaskWorkArea", 1)[1].split(
        "function TaskHeader", 1
    )[0]
    stage_body = task_main.split('<div className="workbench-stage">', 1)[1]
    assert '<div className="workbench-task-header-sticky">' in stage_body
    assert stage_body.index("<TaskHeader") < stage_body.index("<StateCard")
    assert 'className="workbench-composer-actions wbc-composer-actions"' in source
    assert 'className={"wb-composer-send wbc-send"' in source
    assert '.workbench-composer.wbc-composer {' in styles
    assert "padding: 0 !important;" in styles


def test_workbench_task_detail_max_height_aligns_with_main_card_bottom():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    detail_shell = styles.split(
        ".workbench-grid.integrated-sidebars.is-task-detail .wb-task-detail-shell {",
        1,
    )[1].split("}", 1)[0]
    detail_card = styles.split(".wb-task-detail-card {", 1)[1].split("}", 1)[0]
    open_panel = styles.split(".wb-task-detail-tab-panel.open {", 1)[1].split("}", 1)[0]

    assert "padding: 12px 12px 12px 4px;" in detail_shell
    assert "height: 100%;" in detail_shell
    assert "max-height: 100%;" in detail_card
    assert "max-height: calc(100vh" not in detail_card
    assert "flex: 1 1 auto;" in open_panel
    assert "max-height: none;" in open_panel
    assert "100vh" not in open_panel


def test_composer_disclaimer_has_balanced_vertical_clearance():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    disclaimer = styles.split(".wb-composer-disclaimer {", 1)[1].split("}", 1)[0]
    assert "margin: 4px auto;" in disclaimer


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
    assert "compiled/workbench-shortcuts.js?v=0.7.9" in index


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


def test_extension_center_uses_fixed_settings_geometry_and_localized_catalog_copy():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")
    translations = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

    assert '.settings-overlay-panel:has([data-settings-active-tab="extensions"])' not in styles
    assert 'if (props.id === "github-cli") return AboutRelatedIcon("github");' in source
    assert 'className: "wb-extension-expand-button"' in source
    assert source.index('className: "wb-extension-actions"') < source.index('className: "wb-extension-expand-button"')
    assert 'className: "wb-extension-source-sections"' in source
    assert 't("settings.extensionMcpRegistryHint")' in source
    assert '.wb-extension-source-modal { width: min(820px, calc(100vw - 32px)); padding: 0; overflow: auto; }' in styles
    assert '.wb-extension-glyph.github-cli { color: var(--wb-text); }' in styles
    assert 'min-width: 92px;' in styles.split('.wb-extensions-page .wb-extension-install-button {', 1)[1].split('}', 1)[0]
    assert 'className: "wb-btn primary wb-extension-install-button wb-extension-tab-install-button"' in source
    tab_install_button_css = styles.split(
        ".wb-extension-header-actions .wb-extension-tab-install-button {", 1
    )[1].split("}", 1)[0]
    assert "width: 128px;" in tab_install_button_css
    assert "min-width: 128px;" in tab_install_button_css

    extension_scroll_css = styles.split(
        '.settings-overlay-content[data-settings-active-tab="extensions"] {', 1
    )[1].split("}", 1)[0]
    extension_webkit_scroll_css = styles.split(
        '.settings-overlay-content[data-settings-active-tab="extensions"]::-webkit-scrollbar {', 1
    )[1].split("}", 1)[0]
    settings_content_css = styles.split("/* Content area */", 1)[1].split(
        ".settings-overlay-content {", 1
    )[1].split("}", 1)[0]
    assert "overflow-y: auto;" in settings_content_css
    assert "scrollbar-width: none;" in extension_scroll_css
    assert "-ms-overflow-style: none;" in extension_scroll_css
    assert "display: none;" in extension_webkit_scroll_css
    assert "width: 0;" in extension_webkit_scroll_css
    assert "height: 0;" in extension_webkit_scroll_css

    for extension_id in ("python", "uv", "tex", "node", "github-cli", "bun"):
        assert translations.count(f'"settings.extensionCatalog.{extension_id}.name"') == 2
        assert translations.count(f'"settings.extensionCatalog.{extension_id}.description"') == 2
    assert translations.count('"settings.extensionSource.system"') == 2
    assert translations.count('"settings.extensionHealthValue.healthy"') == 2


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


def test_workbench_memory_combines_overview_into_source_card():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-memory.jsx").read_text(encoding="utf-8")
    css = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")

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
    assert '\n      rail,\n      main,' in source
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
    narrow_rule = ".wb-mem-page.learning-active > .wb-mem-detail { display: none; }"
    narrow_rule_index = css.index(narrow_rule)
    narrow_media_index = css.rfind("@media (max-width: 760px)", 0, narrow_rule_index)
    assert narrow_media_index >= 0
    assert css.find("@media", narrow_media_index + 1, narrow_rule_index) < 0
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


def test_markdown_interactive_blocks_render_only_after_streaming_finishes():
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
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    contract = (root / "src" / "cyrene" / "tool_impl" / "renderer" / "load_contract.py").read_text(encoding="utf-8")

    live_message = chat.split("function WbcLiveMessage", 1)[1].split("// ---------------------------------------------------------------------------", 1)[0]
    assistant_message = chat.split("function WbcAssistantMessage", 1)[1].split("var WBC_HEARTBEAT_STALL_MS", 1)[0]
    assert "WBC_LIVE_MARKDOWN_INTERVAL_MS = 120" in chat
    assert "wbcUseThrottledLiveText(runtime.text, !!runtime.streamDone)" in live_message
    assert "wbcRenderMarkdown(renderedText, { interactive: false })" in live_message
    assert "wbcRenderMarkdown(msg.content)" in assistant_message
    assert ".wbc-fold > summary:focus-visible" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert ":::details Title" in contract
    assert ":::card Title" in contract


def test_workbench_agent_transport_notice_has_structured_event_and_durable_bubble():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    events = (root / "src" / "cyrene" / "agent_runtime" / "events.py").read_text(encoding="utf-8")
    route = (root / "src" / "route" / "workbench" / "chat.py").read_text(encoding="utf-8")

    assert '"notification.created"' in events
    assert '"notification.created": { handler: "onNotification"' in chat
    assert "function WbcAgentNotification" in chat
    assert "msg.runtimeNotification || msg.notificationCard" in chat
    assert '"notificationCard": True' in route
    assert ".wbc-agent-notification" in styles
    assert 'role="status" aria-live="polite"' in chat


def test_quick_chat_renders_live_and_durable_agent_notifications():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    quick = (root / "src" / "webui" / "frontend" / "workbench-quick-chat.jsx").read_text(encoding="utf-8")

    assert "AgentNotification: WbcAgentNotification" in chat
    assert "RuntimeTranscript: WbcRuntimeTranscript" in chat
    assert "m.notificationCard" in quick
    assert "chatService.AgentNotification" in quick
    assert "chatService.RuntimeTranscript" in quick
    assert "runtime && runtime.notifications && runtime.notifications.length" in quick


def test_compact_chat_surfaces_share_capability_driven_agent_runtime_ui():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    quick = (root / "src" / "webui" / "frontend" / "workbench-quick-chat.jsx").read_text(encoding="utf-8")

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
    source = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(encoding="utf-8")

    assert "var PAGE_SIZE = 120" in source
    assert "function loadMore()" in source
    assert "data.items.length < data.total" in source
    assert "library.loadMore" in source


def test_workbench_library_does_not_merge_stale_detail_when_switching_items():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-library.jsx").read_text(
        encoding="utf-8"
    )

    assert (
        "detail && String(detail.id) === String(selectedId) ? detail : null"
        in source
    )
    assert (
        'function select(id) { setDetail(null); setSelectedId(String(id));'
        in source
    )


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


def test_external_agent_probe_and_install_help_are_actionable():
    root = Path(__file__).resolve().parent.parent
    settings = (root / "src/webui/frontend/settings-overlay.jsx").read_text(encoding="utf-8")
    i18n = (root / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")

    assert 'if (payload && payload.agent) props.onChanged(payload.agent);' in settings
    assert 'not_started: t("settings.agentRuntime.notStarted"' in settings
    assert 'source: { type: "inline", manifest: manifestExample }' in settings
    assert 'settingsFetch(AGENT_PROPOSAL_ENDPOINT' in settings
    assert '"/" + encodeURIComponent(proposalId) + "/confirm"' in settings
    assert 't("settings.agentProposalConfirmInstall"' in settings
    assert '"settings.agentRuntime.pendingTransport": "Not tested"' in i18n
    assert '"settings.agentRuntime.pendingTransport": "尚未测试"' in i18n
    assert i18n.count('"settings.agentProposalSubmitTitle"') == 2


def test_agent_picker_rebinds_empty_chat_and_confirms_new_nonempty_chat():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src/webui/frontend/workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")

    assert "function handleSwitchAgent(binding)" in source
    assert 'model.createChatWithBinding(projectId, "", binding)' in source
    assert "model.updateChatAgent(activeChat.id, binding)" in source
    assert 'wbcT("workbenchChat.agentNewChatTitle"' in source
    assert "onSwitchAgent={handleSwitchAgent}" in source
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
    root = Path(__file__).resolve().parent.parent
    source = (root / "src/webui/frontend/workbench-chat.jsx").read_text(encoding="utf-8")
    i18n = (root / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")

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
    root = Path(__file__).resolve().parent.parent
    route = (root / "src/route/workbench/chat.py").read_text(encoding="utf-8")
    state_capture = route.index("state_ids_before: set[str] = set()")
    runner = route.index("async def _run(run: ChatRun) -> str:", state_capture)
    finalizer = route.index("def _finalize(reply_text: str)", runner)
    usage_definition = route.index("external_usage: dict[str, int] = {}", state_capture)

    assert state_capture < usage_definition < runner < finalizer
    assert "external_usage: dict[str, int] = {}" not in route[runner:finalizer]
    assert "effective_usage.update(external_usage)" in route[finalizer:]


def test_agent_diagnostics_note_is_localized_from_a_stable_code():
    root = Path(__file__).resolve().parent.parent
    settings = (root / "src/webui/frontend/settings-overlay.jsx").read_text(encoding="utf-8")
    i18n = (root / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")
    runtime = (root / "src/cyrene/extensions/agent_runtime.py").read_text(encoding="utf-8")

    assert 'payload.noteCode || payload.reason' in settings
    assert 't("settings.agentDiagnosticsStartsOnDemand"' in settings
    assert 'diagnostics.note || ""' not in settings
    assert '"noteCode": "starts_on_demand"' in runtime
    assert "ACP stdio 会在需要时启动；诊断信息不会暴露进程环境变量或凭据。" in i18n


def test_agent_settings_show_composer_usability_and_localized_reasons():
    root = Path(__file__).resolve().parent.parent
    settings = (root / "src/webui/frontend/settings-overlay.jsx").read_text(encoding="utf-8")
    i18n = (root / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")

    assert "function agentUsabilityMeta(agent, t)" in settings
    assert 't("settings.agentComposerAvailability"' in settings
    assert 't("settings.agentUsability.available"' in settings
    assert '["error", "crashed", "failed"].indexOf(runtimeState)' in settings
    assert 'authState === "expired" || authState === "failed"' in settings
    assert i18n.count('"settings.agentUsability.available"') == 2
    assert '"settings.agentUsability.available": "可在 Composer 中使用"' in i18n


def test_agent_network_errors_render_full_actionable_details():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src/webui/frontend/workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")
    i18n = (root / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")

    assert "function wbcAgentErrorPresentation(detail, failureKind)" in source
    assert "invalid peer certificate" in source
    assert 'className="wbc-error-detail"' in source
    assert 'wbcT("workbenchChat.error.copyDetail"' in source
    assert "white-space: pre-wrap;" in styles.split(".wbc-error-detail {", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: anywhere;" in styles.split(".wbc-error-detail {", 1)[1].split("}", 1)[0]
    assert i18n.count('"workbenchChat.error.tlsTitle"') == 2
    assert i18n.count('"workbenchChat.error.copyDetail"') == 2


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


# ---------------------------------------------------------------------------
# :::chart declarative interactive blocks
# ---------------------------------------------------------------------------

def _run_chart_services_js(expression: str):
    """Run JS against the chart spec + mount modules (no marked/DOM needed)."""
    root = Path(__file__).resolve().parent.parent
    spec_path = root / "src" / "webui" / "frontend" / "shared" / "chart" / "spec.jsx"
    mount_path = root / "src" / "webui" / "frontend" / "shared" / "chart" / "mount.jsx"
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
    renderer_path = root / "src" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx"
    spec_path = root / "src" / "webui" / "frontend" / "shared" / "chart" / "spec.jsx"
    marked_path = root / "src" / "webui" / "static" / "app" / "marked.min.js"
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
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    contract = (root / "src" / "cyrene" / "tool_impl" / "renderer" / "load_contract.py").read_text(encoding="utf-8")
    index_html = (root / "src" / "webui" / "frontend" / "index.html").read_text(encoding="utf-8")
    build_script = (root / "src" / "webui" / "build-jsx.mjs").read_text(encoding="utf-8")

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
    assert "compiled/shared/chart/spec.js" in index_html
    assert "compiled/shared/chart/mount.js" in index_html
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
    assert '[\\u6309\\u94ae: \\u5f00\\u59cb\\u7ffb\\u8bd1]' in result["finalHtml"] or "[按钮: 开始翻译]" in result["finalHtml"]
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
    chat = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")
    mount = (root / "src" / "webui" / "frontend" / "shared" / "chart" / "mount.jsx").read_text(encoding="utf-8")
    renderer = (root / "src" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    contract = (root / "src" / "cyrene" / "tool_impl" / "renderer" / "load_contract.py").read_text(encoding="utf-8")

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
    renderer = (root / "src" / "webui" / "frontend" / "shared" / "markdown" / "renderer.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    contract = (root / "src" / "cyrene" / "tool_impl" / "renderer" / "load_contract.py").read_text(encoding="utf-8")

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
    mount = (root / "src" / "webui" / "frontend" / "shared" / "chart" / "mount.jsx").read_text(encoding="utf-8")
    chat_route = (root / "src" / "route" / "workbench" / "chat.py").read_text(encoding="utf-8")
    service = (root / "src" / "cyrene" / "workbench" / "chat.py").read_text(encoding="utf-8")
    schemas = (root / "src" / "route" / "schemas.py").read_text(encoding="utf-8")
    wbc = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert 'spec.mode === "model"' in mount
    assert '/actions"' in mount
    assert 'actionId: spec.action_id' in mount
    assert 'chatId: String(chatId || "")' in wbc
    assert 'chatId={String(chat && chat.id || "")}' in wbc
    assert "@router.post(\"/api/workbench/chats/{chat_id}/actions\")" in chat_route or '@router.post("/api/workbench/chats/{chat_id}/actions")' in chat_route
    assert "action_duplicate" in chat_route
    assert "disable_button_block" in chat_route
    assert "def disable_button_block" in service
    assert "def has_button_block" in service
    assert "class ChatActionBody" in schemas
    assert "actionId" in schemas


def test_settings_controls_share_memory_floating_material():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    source = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")

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
    assert ".settings-overlay .wb-export-area .wb-seg" in styles
    assert "function escapeHtml(" not in source
    assert ".remote-pairing-result" not in styles
    assert ".remote-bundle" not in styles
    assert ".wb-accent-custom-button" not in styles
    assert ".wb-skill-detail-card" not in styles
    assert ".settings-overlay .wb-export-session-select" not in styles


def test_profile_rail_displays_budget_in_existing_spacer():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )
    i18n = (
        root / "src" / "webui" / "frontend" / "workbench-i18n.jsx"
    ).read_text(encoding="utf-8")

    profile_rail = source[source.index("function WorkbenchProfileRail"):source.index("// Temporarily keep sign-out")]
    assert 'fetch("/api/budget/status")' in profile_rail
    assert 'fetch("/api/settings/openai-oauth/limits")' in profile_rail
    assert "WorkbenchModel.codexQuotaWindows(payload.limits)" in profile_rail
    assert "codexQuotaState.connected" in profile_rail
    assert 'className="workbench-profile-codex-quota"' in profile_rail
    assert 'window.addEventListener("budget-saved", onBudgetSaved)' in profile_rail
    assert 'window.addEventListener("cyrene:codex-auth-changed", onCodexAuthChanged)' in profile_rail
    assert 'className="workbench-profile-rail-spacer"' in profile_rail
    assert 'className="workbench-profile-budget-stack"' in profile_rail
    assert profile_rail.count('className="workbench-profile-budget workbench-profile-') == 2
    assert 'workbench-profile-codex-card' in profile_rail
    assert 'workbench-profile-currency-card' in profile_rail
    assert 't("profile.budgetDisabled")' in profile_rail
    assert ".workbench-profile-budget {" in styles
    budget_stack_rule = styles.split(".workbench-profile-budget-stack {", 1)[1].split("}", 1)[0]
    assert "position: absolute;" in budget_stack_rule
    assert "gap: 12px;" in budget_stack_rule
    assert '"profile.budgetDisabled": "Budget is not enabled"' in i18n
    assert '"profile.budgetDisabled": "未开启预算功能"' in i18n


def test_task_board_scroll_canvas_reaches_behind_floating_rail_gutter():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    columns_rule = styles.split(
        ".workbench-grid.integrated-sidebars.is-task-board .wb-board-columns {", 1
    )[1].split("}", 1)[0]
    scroll_rule = styles.split(
        ".workbench-grid.integrated-sidebars.is-task-board .wb-board-scroll {", 1
    )[1].split("}", 1)[0]
    floating_rail_rule = styles.rsplit(
        ".workbench-grid.integrated-sidebars .workbench-integrated-rail {", 1
    )[1].split("}", 1)[0]

    assert "--wb-task-board-canvas-gutter: 22px;" in styles
    assert 'html[data-theme="dark"] .workbench-grid.integrated-sidebars.is-task-board {' in styles
    assert "--wb-floating-rail-tint: #24323f;" in styles
    assert "padding-left: calc(var(--wb-task-board-rail-reserve) + var(--wb-task-board-canvas-gutter));" in columns_rule
    assert "margin-inline: calc(0px - var(--wb-task-board-canvas-gutter));" in scroll_rule
    assert "background: var(--wb-floating-rail-bg);" in floating_rail_rule
    assert "opacity:" not in floating_rail_rule
    assert "function handleBoardWheel(event)" in source
    assert 'target.closest(".wb-board-column-body")' in source
    assert "columnBody.scrollTop < maxColumnTop - 1" in source
    assert "viewport.scrollWidth - viewport.clientWidth" in source
    assert "viewport.scrollLeft = nextLeft;" in source
    assert 'className="wb-board-scroll" onWheel={handleBoardWheel}' in source


def test_conversation_status_preview_controls_share_floating_material():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

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
    frontend = Path("src/webui/frontend")
    overlay = (frontend / "settings-overlay.jsx").read_text(encoding="utf-8")
    workbench = (frontend / "workbench.jsx").read_text(encoding="utf-8")
    css = (frontend / "workbench.css").read_text(encoding="utf-8")
    index = (frontend / "index.html").read_text(encoding="utf-8")

    assert 'JSON.stringify({ performance_mode: next })' in overlay
    assert 'settings.performanceMode' in overlay
    appearance = overlay.split("function AppearancePanel(p)", 1)[1].split("// ── Capabilities Panel ──", 1)[0]
    general = overlay.split("function GeneralPanel(p)", 1)[1].split("// ── Models Panel ──", 1)[0]
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
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    durable_source = "var WBC_DURABLE_TRACE_FIELDS" + source.split(
        "var WBC_DURABLE_TRACE_FIELDS", 1
    )[1].split("function wbcToolPresentationKind(entry)", 1)[0]
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
