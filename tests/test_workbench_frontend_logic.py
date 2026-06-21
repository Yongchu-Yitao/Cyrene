import json
import subprocess
from pathlib import Path


def _run_workbench_model_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    model_path = root / "src" / "workbench-webui" / "workbench-model.jsx"
    script = f"""
const fs = require("fs");
global.window = {{}};
eval(fs.readFileSync({json.dumps(str(model_path))}, "utf8"));
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _run_workbench_i18n_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    i18n_path = root / "src" / "workbench-webui" / "workbench-i18n.jsx"
    script = f"""
const fs = require("fs");
global.window = {{}};
global.localStorage = {{ getItem: () => "", setItem: () => {{}} }};
global.navigator = {{ language: "zh-CN" }};
global.document = {{ documentElement: {{ dataset: {{}} }} }};
global.React = {{ useState: () => [0, () => {{}}], useEffect: () => {{}} }};
eval(fs.readFileSync({json.dumps(str(i18n_path))}, "utf8"));
window.WorkbenchI18n.setLang("zh");
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


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
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "workbench-webui" / "workbench-model.jsx").read_text(encoding="utf-8")

    assert "function markStepById" in model
    assert '"/plan"' in model
    assert "model.markStepById(basePlan, stepId" in source
    assert "controller.reorderSteps" in source
    assert "dependsOn" in source
    assert "function requirePlan(baseSession)" in source
    assert "firstUnresolvedStepIndex" not in source


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
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
    i18n = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    assert "var WorkbenchChatRuntimes = window.WorkbenchChatRuntimes || (function () {" in source
    assert "var runtimes = {};" in source
    assert "var aborts = {};" in source
    assert "window.WorkbenchChatRuntimes = WorkbenchChatRuntimes;" in source
    assert "var runtimeEngine = window.WorkbenchChatRuntimes;" in source
    assert "runtimeEngine.subscribe(function (snap) { setRuntimes(snap); })" in source
    assert "runtimeEngine.start(chatId, input || {}, model)" in source
    assert "var activeRuntime = runtimes[activeChatId] || null;" in source
    assert "if (!chatId || runtimes[chatId]) return null;" in source
    assert "otherRunning" not in source
    assert "workbenchChat.lockedByOther" not in source
    assert "workbenchChat.lockedByOther" not in i18n


def test_workbench_chat_splits_live_tools_around_intermediate_messages():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert 'type === "intermediate_message"' in source
    assert "function appendIntermediate(chatId, message)" in source
    assert "segments: segments.concat" in source
    assert "progress: []" in source
    assert "completedSegments.map" in source
    assert "<WbcAssistantMessage" in source
    assert "event.assistantMessages" in source
    assert 'event.type === "assistant_message" && event.intermediate && event.message' in source


def test_workbench_copy_uses_electron_clipboard_bridge():
    root = Path(__file__).resolve().parent.parent
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert "clipboard, contextBridge, ipcRenderer" in preload
    assert "writeClipboardText: (text) =>" in preload
    assert "clipboard.writeText(" in preload
    assert 'typeof window.cyrene.writeClipboardText === "function"' in chat
    assert "window.cyrene.writeClipboardText(text);" in chat
    assert "await navigator.clipboard.writeText(text);" in chat
    assert 'console.error("Failed to copy workbench message:", e);' in chat


def test_workbench_side_viewer_keeps_html_sandboxed_and_uses_native_pdf_zoom():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")

    assert 'split(";", 1)[0].trim().toLowerCase()' in source
    assert 'ext === "ppt"' not in source
    assert 'ext === "doc"' not in source
    assert 'wbcFileViewKind(file) !== "html"' in source
    assert 'function wbcHtmlPreviewDocument(source, sourceUrl)' in source
    assert '<base href="' in source
    assert 'sandbox="allow-scripts"' in source
    assert 'srcDoc={htmlPreview}' in source
    assert 'blobUrl + "#zoom="' in source
    assert 'key={pdfSrc}' in source
    assert "width: 100%;" in styles
    assert "height: 100%;" in styles
    assert r"/\.html?$/i.test(target.pathname)" in main


def test_workbench_acceptance_button_calls_agent_endpoint():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "workbench-webui" / "workbench-model.jsx").read_text(encoding="utf-8")

    assert "window.WorkbenchModel.generateAcceptance(session.id)" in source
    assert '"/acceptance/generate"' in model


def test_workbench_artifact_rows_download_registered_files():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "workbench-webui" / "workbench-model.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")
    routes = (root / "src" / "webui" / "routes.py").read_text(encoding="utf-8")

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
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "static" / "app" / "index.html").read_text(encoding="utf-8")

    tabs_rule = styles.split(".workbench-right-tabs {", 1)[1].split("}", 1)[0]
    body_rule = styles.split(".workbench-right-body {", 1)[1].split("}", 1)[0]

    assert "flex: 0 0 48px;" in tabs_rule
    assert "flex: 1 1 auto;" in body_rule
    assert "workbench.css?v=20260620-righttabs1" in index


def test_linux_desktop_uses_native_frame_and_directory_picker():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    create = (root / "src" / "workbench-webui" / "workbench-create.jsx").read_text(encoding="utf-8")
    chat = (root / "src" / "webui" / "static" / "app" / "chat.jsx").read_text(encoding="utf-8")

    assert "const isLinux = process.platform === 'linux';" in main
    assert "const useInsetTitleBar = !isLegacyShell && isMac;" in main
    assert "ipcMain.handle('dialog:pick-directory'" in main
    assert "properties: ['openDirectory', 'createDirectory']" in main
    assert "if (process.platform !== 'linux') return Promise.resolve(null);" in preload
    assert "ipcRenderer.invoke('dialog:pick-directory')" in preload
    assert 'window.cyrene.platform === "linux"' in create
    assert "await window.cyrene.pickDirectory()" in create
    assert 'window.cyrene.platform === "linux"' in chat
    assert "await window.cyrene.pickDirectory()" in chat


def test_workbench_chat_directory_picker_falls_back_on_macos_and_lists_default_workspace():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
    i18n = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    assert 'window.cyrene.platform === "linux"' in chat
    assert 'fetch("/api/context/pick-directory", { method: "POST" })' in chat
    assert "defaultWorkspacePath={wsDir}" in chat
    assert "if (defaultWorkspacePath) workspaceOptions.push" in chat
    assert 'wbcT("workbenchChat.defaultWorkspace", "Default workspace")' in chat
    assert '"workbenchChat.defaultWorkspace": "Default workspace"' in i18n
    assert '"workbenchChat.defaultWorkspace": "默认 workspace"' in i18n


def test_workbench_follow_up_uses_context_endpoint_without_native_prompt():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "workbench-webui" / "workbench-model.jsx").read_text(encoding="utf-8")
    routes = (root / "src" / "webui" / "routes.py").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "static" / "app" / "index.html").read_text(encoding="utf-8")

    assert 'window.prompt("后续任务标题"' not in source
    assert "model.createFollowUp(sid, options)" in source
    assert '"/follow-up"' in model
    assert '"/api/task-sessions/{session_id}/follow-up"' in routes
    assert 'session["parentSessionId"] = session_id' in routes
    assert "followUpContext" in routes
    assert "workbench-model.js?v=20260620-goalloop1" in index
    assert "workbench.js?v=20260620-goalloop2" in index


def test_workbench_regenerate_plan_failure_preserves_current_plan():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    regenerate_block = source.split("regeneratePlan: function ()", 1)[1].split("approvePlan: function ()", 1)[0]

    assert "plan: Array.isArray(session.plan) ? session.plan : []" in regenerate_block
    assert "acceptanceCriteria: Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : []" in regenerate_block
    assert "model.buildPlanSteps" not in regenerate_block


def test_workbench_plan_conflict_does_not_apply_client_fallback():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "workbench-webui" / "workbench-model.jsx").read_text(encoding="utf-8")

    assert 'err.code === "stale_plan_revision"' in source
    assert "rethrowPlanConflict(err);" in source
    assert "error.code = payload.code" in model


def test_workbench_init_plan_failure_shows_details_and_restart():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-create.jsx").read_text(encoding="utf-8")

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


def test_workbench_model_settings_preserve_form_on_failed_response():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "settings-overlay.jsx").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "static" / "app" / "index.html").read_text(encoding="utf-8")
    save_block = source.split("function saveModels()", 1)[1].split("function saveTools()", 1)[0]

    assert "async function readSettingsResponse(response)" in source
    assert "if (!response.ok)" in source
    assert "fetch(\"/api/settings/models\").then(readSettingsResponse)" in source
    assert "}).then(readSettingsResponse).then(function (p)" in save_block
    assert "p.models || p.primary_candidates || norm" in save_block
    assert "p.vision_models || p.vision_candidates || vNorm" in save_block
    assert "settings-overlay.js?v=20260619-toast1" in index


def test_workbench_chat_subagent_page_is_independent_and_localized():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")
    legacy = (root / "src" / "webui" / "static" / "app" / "chat.jsx").read_text(encoding="utf-8")

    assert 'id: "subagents"' in source
    assert "function WbcSubagentsTab" in source
    assert '"/subagents" + query' in source
    assert "AgentGroupChat" not in source
    assert ".wbc-subagent-page" in styles
    assert ".agent-chat-" not in styles.split("/* Workbench-only subagent page.", 1)[1].split("/* 计划 tab", 1)[0]
    assert "function AgentGroupChat" in legacy

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


def test_workbench_subagent_payload_recovers_chat_scoped_snapshot(monkeypatch):
    from webui import routes_workbench_chat
    from cyrene import subagent

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
