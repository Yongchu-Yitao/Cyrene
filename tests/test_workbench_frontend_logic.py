import json
import subprocess
from pathlib import Path


def test_new_workbench_chat_reuses_create_response_without_refetching():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    assert 'var skipNextHydrationChatIdRef = useWbcRef("");' in source
    assert "skipNextHydrationChatIdRef.current = chat.id;" in source
    assert "skipNextHydrationChatIdRef.current === activeChatId" in source


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


def _run_workbench_runtime_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    runtime_source = source.split(
        "var WorkbenchChatRuntimes = window.WorkbenchChatRuntimes || (function () {", 1
    )[1].split("// Page", 1)[0]
    runtime_source = (
        "var WorkbenchChatRuntimes = window.WorkbenchChatRuntimes || (function () {"
        + runtime_source
    )
    script = f"""
global.window = {{ __sseHandlers: {{ add: () => {{}} }} }};
eval({json.dumps(runtime_source)});
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


def test_workbench_keeps_live_subagent_logs_across_silent_refreshes():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")

    assert 'data.type === "subagent_update"' in source
    assert 'session_id = str(entry.get("session_id") or "")' in (
        root / "src" / "cyrene" / "subagent.py"
    ).read_text(encoding="utf-8")
    assert "event.live && event.id" in source
    assert "data.message" in source


def test_workbench_uses_light_project_payload_and_lazy_session_detail():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "workbench-webui" / "workbench-model.jsx").read_text(encoding="utf-8")

    assert 'apiJson("/api/projects?detail=summary")' in model
    assert "function fetchSession(sessionId)" in model
    assert '"/api/task-sessions/" + encodeURIComponent(sessionId)' in model
    assert "mergeSessionPayload(prev, payload)" in source
    assert "if (session.isSummary) fetchAndMergeSession(session.id)" in source
    assert "if (nextSession && nextSession.isSummary) fetchAndMergeSession(nextSessionId)" in source
    assert "seq !== sessionLoadSeqRef.current" in source


def test_workbench_module_pages_are_kept_alive_without_hidden_file_drop():
    root = Path(__file__).resolve().parent.parent
    shell = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
    knowledge = (root / "src" / "workbench-webui" / "workbench-knowledge.jsx").read_text(encoding="utf-8")

    assert "mountedPages" in shell
    assert 'style={{ display: isChat ? "contents" : "none" }}' in shell
    assert 'style={{ display: isKnowledge ? "contents" : "none" }}' in shell
    assert 'style={{ display: isSchedule ? "contents" : "none" }}' in shell
    assert 'style={{ display: isMemory ? "contents" : "none" }}' in shell
    assert "active={!isModulePage}" in shell
    assert "var taskDropEnabled = !!(active && project && session && session.kind !== \"init\")" in shell
    assert "function WorkbenchChatPage({ active, project" in chat
    assert "!!(isActive && project)" in chat
    assert "var active = !props || props.active !== false" in knowledge
    assert "!!(active && project)" in knowledge


def test_workbench_task_controller_uses_current_session_from_returned_store():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
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
    source = (root / "src" / "workbench-webui" / "workbench-memory.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")
    i18n = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    routes = (root / "src" / "webui" / "routes.py").read_text(encoding="utf-8")
    pattern = (root / "src" / "cyrene" / "pattern.py").read_text(encoding="utf-8")
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
    assert "/learn-skill" in source
    assert "grid-template-columns: 34px 42px minmax(0, 1fr) 22px" in styles
    assert ".wb-detail-shot" in styles
    assert ".wb-detail-files" in styles
    assert "memory.learning.detailsTitle" in i18n
    assert "memory.learning.sessionSelect" in i18n
    assert "memory.learning.review.parameterize" in i18n
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


def test_workbench_chat_renders_new_user_turn_before_live_thinking_card():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    quick_source = (
        root / "src" / "workbench-webui" / "workbench-quick-chat.jsx"
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


def test_workbench_chat_reveals_browser_tab_from_live_browser_events():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert "browserActiveByChat" in source
    assert 'event.type === "browser_frame" || event.type === "browser_takeover_request"' in source
    assert "(!browserEventChatId || browserEventChatId === String(activeChatIdRef.current))" in source
    assert "setBrowserActiveByChat(function (prev)" in source
    assert "(browserState && browserState.active) || browserMarkedActive" in source


def test_workbench_chat_delete_detaches_local_fork_markers():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
    handler = source.split("function handleDeleteChat(chatId)", 1)[1].split("function handleToTask", 1)[0]

    assert "function detachDeletedForkSource(item)" in handler
    assert "delete cleaned.forkedFromChatId" in handler
    assert "delete cleaned.forkedAtMessageId" in handler
    assert "delete cleaned.forkMessage" in handler
    assert ".map(detachDeletedForkSource)" in handler
    assert "setActiveChat(function (prev) { return detachDeletedForkSource(prev); })" in handler


def test_workbench_chat_switches_stop_to_guidance_while_running():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    index = (root / "src" / "webui" / "static" / "app" / "index.html").read_text(
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
        root / "src" / "workbench-webui" / "workbench-i18n.jsx"
    ).read_text(encoding="utf-8")
    assert "workbench-chat.js?v=0.6.8" in index
    assert "workbench-i18n.js?v=0.6.8" in index


def test_workbench_guidance_is_optimistic_and_completed_tools_do_not_spin():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
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
    assert 'status: "completed"' in source
    assert 'entry.status !== "completed"' in trace_card


def test_workbench_chat_does_not_render_previous_transcript_during_switch():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
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
    assert "loading={chatLoading}" in source


def test_workbench_chat_loading_keeps_lightweight_overview_visible():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    assert "var selectedChatSummary = chats.find" in source
    assert "chatSummary={selectedChatSummary}" in source
    assert "chatDetailed={!!visibleChat}" in source
    assert "loading && !chat" in source
    assert "messages.length === 0 && !runtime && !loading && !error" in source
    assert '"workbenchChat.loadingConversation": "正在加载对话…"' in i18n
    assert '"workbenchChat.error.transcriptPrefix": "对话详情：{error}"' in i18n


def test_workbench_chat_plan_confirmation_can_continue_in_auto_mode():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(
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


def test_workbench_attachment_preview_falls_back_without_overflowing():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(
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
    assert source.count("<WbcMessageAttachment key=") == 2
    image_rule = styles.split(".wbc-attach-card.image {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden;" in image_rule


def test_workbench_chat_splits_live_tools_around_intermediate_messages():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
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
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
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
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
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


def test_workbench_knowledge_related_tab_loads_and_navigates_conversations():
    root = Path(__file__).resolve().parent.parent
    knowledge = (
        root / "src" / "workbench-webui" / "workbench-knowledge.jsx"
    ).read_text(encoding="utf-8")
    shell = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(
        encoding="utf-8"
    )

    assert '"/related?" + withWs()' in knowledge
    assert 'detailTab !== "related"' in knowledge
    assert '{ type: "task", projectId: item.project_id, sessionId: item.session_id' in knowledge
    assert '{ type: "chat", projectId: item.project_id, chatId: item.chat_id }' in knowledge
    assert "onNavigate: navigateFromSearch" in shell


def test_workbench_chat_plan_tab_uses_durable_plan_and_live_step_events():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

    assert "function wbcActivePlan(chat)" in source
    assert "var active = chat && chat.activePlan;" in source
    assert 'event.type === "plan_progress" || event.type === "plan"' in source
    assert 'className={"wbc-plan-step " + status}' in source
    assert "wbcPlanStepStatusText(status)" in source


def test_workbench_chat_tool_trace_preserves_i18n_metadata():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(
        encoding="utf-8"
    )

    live_message = chat.split("function WbcLiveMessage(", 1)[1].split(
        "var WBC_DRAFT_PREFIX", 1
    )[0]
    segment_adapter = chat.split("function wbcRuntimeSegmentMessages(", 1)[1].split(
        "function wbcSubagentStatusText", 1
    )[0]
    assert "var progressEntries = Array.isArray(runtime.progress) ? runtime.progress : [];" in live_message
    assert "trace: Array.isArray(segment.progress) ? segment.progress" in segment_adapter
    assert "return { tool: entry.text, preview: entry.preview };" not in live_message
    assert 'wbcT(entry.detailKey, toolKey, entry.detailParams)' in chat
    assert '"update_plan_progress"].indexOf(toolName)' in chat
    assert '"toolName.retire_project_memory": "Retire project memory"' in i18n
    assert '"toolName.retire_project_memory": "停用项目记忆"' in i18n
    assert '"workbenchChat.thinkingPhrases":' in i18n
    assert "WBC_THINKING_PHRASES" not in chat
    assert "var heartbeatI18n = useWorkbenchI18n();" in chat
    assert "}, [heartbeatLang]);" in chat


def test_workbench_chat_context_and_browser_trace_have_dynamic_i18n_labels():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(
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


def test_workbench_phase_events_publish_translation_keys():
    root = Path(__file__).resolve().parent.parent
    planning = (root / "src" / "cyrene" / "agent" / "planning.py").read_text(encoding="utf-8")
    guidance = (root / "src" / "cyrene" / "agent" / "guidance.py").read_text(encoding="utf-8")
    reflection = (root / "src" / "cyrene" / "agent" / "deep_reflection.py").read_text(encoding="utf-8")

    assert '"detail_key": "phase.planning"' in planning
    assert '"detail_key": "phase.applyingGuidanceToSubagents"' in guidance
    assert '"detail_params": {"count": len(snapshot)}' in guidance
    assert '"detail_key": "phase.guidedRoundContinuation"' in guidance
    assert '"detail_key": "phase.guidanceExecution"' in guidance
    assert '"detail_key": "phase.deepReflection"' in reflection


def test_workbench_chat_last_user_message_has_retry_action():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    i18n = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(
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
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
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
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert "clipboard, contextBridge, ipcRenderer" in preload
    assert "writeClipboardText: (text) =>" in preload
    assert "clipboard.writeText(" in preload
    assert 'typeof window.cyrene.writeClipboardText === "function"' in chat
    assert "window.cyrene.writeClipboardText(text);" in chat
    assert "await navigator.clipboard.writeText(text);" in chat
    assert 'console.error("Failed to copy workbench message:", e);' in chat


def test_code_blocks_use_declared_language_and_resilient_clipboard_actions():
    root = Path(__file__).resolve().parent.parent
    highlight = (root / "src" / "webui" / "static" / "app" / "code" / "highlight.jsx").read_text(encoding="utf-8")
    actions = (root / "src" / "webui" / "static" / "app" / "code" / "actions.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "static" / "app" / "code" / "highlight.css").read_text(encoding="utf-8")

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
    assert 'window.pdfjsInstallCopyFix(container, viewer)' in source
    assert 'window.pdfjsInstallSelectionSanitizer(container, viewer, eventBus)' in source
    assert 'selectionSanitizer.abort();' in source
    assert '.wbc-viewer .pdfViewer .textLayer' not in styles
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
    compact_tabs = styles.split("@container (max-width: 320px) {", 1)[1].split("}", 2)

    assert "flex: 0 0 48px;" in tabs_rule
    assert "flex: 1 1 auto;" in body_rule
    assert "container-type: inline-size;" in styles
    assert "gap: 2px;" in compact_tabs[0]
    assert "padding-inline: 8px;" in compact_tabs[0]
    assert "padding-inline: 2px;" in compact_tabs[1]
    assert "font-size: calc(12px * var(--wb-ui-font-scale, 1));" in compact_tabs[1]
    assert "workbench.css?v=0.6.8" in index


def test_workbench_collapsed_rail_keeps_labels_horizontal_during_expansion():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "static" / "app" / "index.html").read_text(encoding="utf-8")

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
    assert "workbench.css?v=0.6.8" in index


def test_workbench_collapsed_rail_icons_stay_left_anchored_while_closing():
    root = Path(__file__).resolve().parent.parent
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")

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
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")

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
    settings = (root / "src" / "workbench-webui" / "settings-overlay.jsx").read_text(encoding="utf-8")
    translations = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "static" / "app" / "index.html").read_text(encoding="utf-8")

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
    assert "settings-overlay.js?v=0.6.8" in index


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


def test_electron_browser_panel_uses_native_browser_bridge():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (root / "electron" / "preload.js").read_text(encoding="utf-8")
    view = (root / "src" / "webui" / "static" / "app" / "browser-view.jsx").read_text(encoding="utf-8")

    assert "WebContentsView" in main
    assert "class BrowserTabManager" in main
    assert "CYRENE_ELECTRON_RPC_PORT" in main
    assert "ipcMain.handle('browser:set-bounds'" in main
    assert "setAudioMuted" in main
    assert "isCurrentlyAudible" in main
    assert "browser_tab_new" in (root / "src" / "cyrene" / "registry_tools.py").read_text(encoding="utf-8")
    assert "browser: {" in preload
    assert "ipcRenderer.invoke('browser:navigate'" in preload
    assert "ipcRenderer.invoke('browser:set-context'" in preload
    assert "window.cyrene && window.cyrene.browser" in view
    assert "ElectronBrowserViewportPanel" in view
    assert "bridge.setBounds" in view
    assert "bridge.setContext" in view
    assert "bridge.setMuted" in view
    assert "browser_user_events" in (root / "src" / "cyrene" / "registry_tools.py").read_text(encoding="utf-8")


def test_electron_browser_user_events_are_recorded_for_learning():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    routes = (root / "src" / "webui" / "routes.py").read_text(encoding="utf-8")
    view = (root / "src" / "webui" / "static" / "app" / "browser-view.jsx").read_text(encoding="utf-8")

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
    assert "bridge.setContext({ sessionId: sessionId, roundId: rid })" in view


def test_electron_browser_panel_does_not_restore_closed_tabs_from_stale_state():
    root = Path(__file__).resolve().parent.parent
    view = (root / "src" / "webui" / "static" / "app" / "browser-view.jsx").read_text(encoding="utf-8")
    panel = view.split("function ElectronBrowserViewportPanel", 1)[1].split("function ScreencastBrowserViewportPanel", 1)[0]

    assert 'const nextUrl = (active && active.url) || "";' in panel
    assert "browserState && browserState.url" not in panel
    assert "browserState && browserState.active" not in panel
    assert "if (!tabs.length" not in panel


def test_workbench_chat_directory_picker_falls_back_on_macos_and_lists_default_workspace():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
    i18n = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    assert 'window.cyrene.platform === "linux"' in chat
    assert 'fetch("/api/context/pick-directory", { method: "POST" })' in chat
    assert "defaultWorkspacePath={projectWorkspacePath || wsDir}" in chat
    assert "if (defaultWorkspacePath) workspaceOptions.push" in chat
    assert 'wbcT("workbenchChat.defaultWorkspace", "Default workspace")' in chat
    assert '"workbenchChat.defaultWorkspace": "Default workspace"' in i18n
    assert '"workbenchChat.defaultWorkspace": "默认 workspace"' in i18n


def test_workbench_chat_workspace_chip_follows_project_until_user_overrides_it():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(
        encoding="utf-8"
    )

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
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "static" / "app" / "index.html").read_text(encoding="utf-8")

    picker_rule = styles.rsplit(".wbc-ctx-picker {", 1)[1].split("}", 1)[0]
    text_rule = styles.rsplit(
        ".wbc-ctx-picker .wbc-popmenu-label,\n.wbc-ctx-picker .wbc-popmenu-desc {",
        1,
    )[1].split("}", 1)[0]

    assert "max-width: calc(100vw - 24px);" in picker_rule
    assert "overflow-x: hidden;" in picker_rule
    assert "min-width: 0;" in styles
    assert "text-overflow: ellipsis;" in text_rule
    assert "white-space: nowrap;" in text_rule
    assert 'className="wbc-popmenu-desc" title={p}' in chat
    assert "workbench-chat.js?v=0.6.8" in index


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
    assert "workbench-model.js?v=0.6.8" in index
    assert "workbench.js?v=0.6.8" in index


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
    api = (root / "src" / "workbench-webui" / "workbench-api.jsx").read_text(encoding="utf-8")

    assert 'err.code === "stale_plan_revision"' in source
    assert "rethrowPlanConflict(err);" in source
    assert "error.code = (payload && payload.code)" in api


def test_workbench_api_timeout_covers_response_body_consumption():
    root = Path(__file__).resolve().parent.parent
    api = (root / "src" / "workbench-webui" / "workbench-api.jsx").read_text(encoding="utf-8")

    assert "Keep the deadline active until" in api
    assert "resp.__workbenchRequestDone = done" in api
    assert "resp.__workbenchNormalizeAbort = normalizeAbort" in api
    assert 'err.name === "AbortError" || err.isTimeout' in api


def test_workbench_api_json_times_out_when_body_stalls_after_headers():
    root = Path(__file__).resolve().parent.parent
    api_path = root / "src" / "workbench-webui" / "workbench-api.jsx"
    script = f"""
const fs = require("fs");
global.window = {{}};
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
window.WorkbenchAPI.json("/slow-body", {{ timeout: 10, toast: false }}).then(
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


def test_workbench_init_answer_updates_do_not_set_parent_state_inside_local_updater():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-create.jsx").read_text(encoding="utf-8")
    answer_block = source.split("function setAnswer(qid, value)", 1)[1].split("function regenerate()", 1)[0]

    assert "answersRef.current = nextAnswers;" in answer_block
    assert "setAnswers(nextAnswers);" in answer_block
    assert "persist(nextAnswers);" in answer_block
    assert "setAnswers(function" not in answer_block


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
    assert "settings-overlay.js?v=0.6.8" in index


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


def test_workbench_chat_quick_actions_include_manual_context_compaction():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert 'function compactChat(chatId)' in source
    assert '"/compact"' in source
    assert 'wbcT(compactBusy ? "workbenchChat.compactBusy" : "workbenchChat.compact"' in source
    assert "activeRunning || compactBusy" not in source
    assert "disabled={compactBusy} onClick={onCompact}" in source
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
    source = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")

    assert 'event.type === "browser_frame" || event.type === "browser_takeover_request"' in source
    assert 'setSideTab("browser")' in source
    browser_switch_block = source.split('event.type === "browser_frame" || event.type === "browser_takeover_request"', 1)[1].split('setSideTab("browser")', 1)[0]
    assert "runtimeEngine.isRunning" not in browser_switch_block
    assert 'id: "browser", label: wbcT("chat.side.browser", "Browser")' in source
    assert "window.BrowserViewportPanel" in source
    assert "onTakeoverComplete: onBrowserTakeoverComplete" in source
    assert "handleAnswer(pending.id" in source


def test_warning_toast_has_no_colored_left_accent():
    root = Path(__file__).resolve().parent.parent
    css = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")

    assert ".workbench-toast.is-warning { border-left: 1px solid var(--wb-line); }" in css
    assert ".workbench-toast.is-warning { border-left-color: var(--wb-amber); }" not in css


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


def _run_workbench_shortcuts_js(expression: str):
    root = Path(__file__).resolve().parent.parent
    shortcuts_path = root / "src" / "workbench-webui" / "workbench-shortcuts.jsx"
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
    eval(fs.readFileSync({json.dumps(str(shortcuts_path))}, "utf8"));
    const result = ({expression});
    process.stdout.write(JSON.stringify(result));
    """
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def test_workbench_shortcuts_module_exposes_actions_and_platform_aware_mod():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-shortcuts.jsx").read_text(encoding="utf-8")

    assert "window.WorkbenchShortcuts" in source
    assert "isMacPlatform" in source
    assert '"mod"' in source
    # Composer Enter-to-send is one of the default bindings so the setting panel
    # can show and rebind it.
    assert '"composer-send"' in source
    assert '"Enter"' in source

    ids = _run_workbench_shortcuts_js(
        "window.WorkbenchShortcuts.list().map(function (i) { return i.id; })"
    )
    assert "search" in ids
    assert "new-chat" in ids
    assert "new-task" in ids
    assert "composer-send" in ids
    assert "composer-newline" in ids


def test_workbench_shortcuts_matches_mod_k_on_windows_user_agent():
    # The "mod" token resolves to Ctrl on Windows/Linux user agents. A Cmd+K
    # event (metaKey) on a Windows UA should also match search, because "mod"
    # matches meta OR ctrl so Mac keyboards work everywhere; a plain "k"
    # should not match.
    result = _run_workbench_shortcuts_js(
        "{"
        ' ctrlK: window.WorkbenchShortcuts.matches({ key: "k", metaKey: false, ctrlKey: true, shiftKey: false, altKey: false }, "search"),'
        ' cmdK: window.WorkbenchShortcuts.matches({ key: "k", metaKey: true, ctrlKey: false, shiftKey: false, altKey: false }, "search"),'
        ' plainK: window.WorkbenchShortcuts.matches({ key: "k", metaKey: false, ctrlKey: false, shiftKey: false, altKey: false }, "search"),'
        ' enter: window.WorkbenchShortcuts.matches({ key: "Enter", metaKey: false, ctrlKey: false, shiftKey: false, altKey: false }, "composer-send"),'
        ' shiftEnter: window.WorkbenchShortcuts.matches({ key: "Enter", metaKey: false, ctrlKey: false, shiftKey: true, altKey: false }, "composer-send"),'
        ' shiftEnterNewline: window.WorkbenchShortcuts.matches({ key: "Enter", metaKey: false, ctrlKey: false, shiftKey: true, altKey: false }, "composer-newline")'
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
        " var sc = window.WorkbenchShortcuts;"
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
        ' ctrlK: window.WorkbenchShortcuts.captureEvent({ key: "k", metaKey: false, ctrlKey: true, shiftKey: false, altKey: false }),'
        ' shiftEnter: window.WorkbenchShortcuts.captureEvent({ key: "Enter", metaKey: false, ctrlKey: false, shiftKey: true, altKey: false }),'
        ' escape: window.WorkbenchShortcuts.captureEvent({ key: "Escape", metaKey: false, ctrlKey: false, shiftKey: false, altKey: false }),'
        ' pureMod: window.WorkbenchShortcuts.captureEvent({ key: "Control", metaKey: false, ctrlKey: true, shiftKey: false, altKey: false })'
        "}"
    )
    assert result["ctrlK"] == {"cancelled": False, "keys": ["mod", "K"]}
    assert result["shiftEnter"] == {"cancelled": False, "keys": ["shift", "Enter"]}
    assert result["escape"] == {"cancelled": True, "keys": []}
    assert result["pureMod"] == {"cancelled": False, "keys": []}


def test_workbench_task_composer_uses_enter_to_send_via_shortcut_module():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")

    # The old Cmd/Ctrl+Enter to send behavior is replaced by the shortcut module
    # so Enter sends directly (matching the chat composer).
    composer_block = source.split("function TaskComposer(", 1)[1].split("function composerPlaceholder", 1)[0]
    assert 'sc.matches(event, "composer-send")' in composer_block
    assert "event.metaKey || event.ctrlKey" not in composer_block.split("function onKeyDown")[1].split("}")[0]


def test_workbench_file_drop_routes_files_to_task_chat_and_knowledge():
    root = Path(__file__).resolve().parent.parent
    workbench = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
    knowledge = (root / "src" / "workbench-webui" / "workbench-knowledge.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")

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

    # Knowledge drops reuse the existing ingestion path rather than chat uploads.
    assert "knowledgeFileDropActive = useWorkbenchFileDrop" in knowledge
    assert "handleFiles(files)" in knowledge
    assert "client.upload(files)" in knowledge
    assert ".wb-file-drop-overlay" in styles


def test_workbench_file_drop_hook_prevents_navigation_and_delivers_files():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
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
    source = (root / "src" / "workbench-webui" / "settings-overlay.jsx").read_text(encoding="utf-8")
    translations = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")
    index = (root / "src" / "webui" / "static" / "app" / "index.html").read_text(encoding="utf-8")

    assert '{ id: "shortcuts", labelKey: "settings.shortcuts" }' in source
    assert "function ShortcutsPanel" in source
    assert "React.createElement(ShortcutsPanel" in source
    assert "window.WorkbenchShortcuts" in source
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
    assert "compiled/workbench-shortcuts.js?v=0.6.8" in index


def test_workbench_about_related_actions_only_click_right_button():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "settings-overlay.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")

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


def test_workbench_help_center_lists_shortcuts_from_module_with_customize_link():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")

    # Help center reads the binding list from WorkbenchShortcuts instead of
    # hardcoding the keys array, so customizations surface there too.
    help_block = source.split("function WorkbenchHelpCenter", 1)[1].split("function WorkbenchEditProjectModal", 1)[0]
    assert "WorkbenchShortcuts" in help_block
    assert "shortcutList" in help_block
    assert "help.customizeShortcuts" in help_block
    # The old hardcoded list is gone.
    assert '{ id: "search", label: t("help.shortcut.search"), keys: ["mod", "K"] }' not in help_block


def test_workbench_global_shortcut_handler_wired_in_workbench_app():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")

    app_block = source.split("function WorkbenchApp", 1)[1].split("function WorkbenchTopbar", 1)[0]
    # A keydown listener dispatches the global shortcuts.
    assert 'addEventListener("keydown"' in app_block
    assert 'sc.matches(event, "search")' in app_block
    assert 'sc.matches(event, "new-chat")' in app_block
    assert 'sc.matches(event, "new-task")' in app_block
    assert 'sc.matches(event, "settings")' in app_block
    assert 'sc.matches(event, "toggle-sidebar")' in app_block
    assert 'sc.matches(event, "switch-project")' in app_block


def test_workbench_memory_cite_tab_renders_actual_citations_not_placeholder():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-memory.jsx").read_text(encoding="utf-8")

    # The old placeholder text is gone.
    assert "引用记录会在 Agent 引用此记忆时自动记录" not in source
    # The Cite tab now renders citations from the memory's citations list.
    assert "m.citations" in source
    assert "wb-mem-cite-list" in source
    assert "wb-mem-cite-row" in source


def test_workbench_memory_history_tab_renders_events_not_hardcoded():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-memory.jsx").read_text(encoding="utf-8")

    # The old hardcoded two-row history is gone — isolate the historyBody block.
    history_block = source.split("var historyBody", 1)[1].split("return h(\"aside\"", 1)[0]
    assert '"最后更新"' not in history_block
    assert '"创建记忆"' not in history_block
    # The History tab now renders from m.history.
    assert "m.history" in source
    assert "historyEvents" in source
    assert "action_label" in source


def test_workbench_skill_learning_uses_actionable_candidate_status_only():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-memory.jsx").read_text(encoding="utf-8")
    translations = (root / "src" / "workbench-webui" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    assert 'activeCandidate ? h("div", { className: "wb-learning-review-pill "' in source
    assert "candidateNextStepText(activeCandidate, t)" in source
    assert 'activePanel === "learning" ? null : rail' in source
    assert 'onExit: function () { setActivePanel(""); }' in source
    assert "不是可复用的多工具流程" not in translations
    assert '"memory.learning.noRepeatYet": "尚未发现重复"' in translations


def test_workbench_skill_learning_has_small_screen_progressive_disclosure():
    root = Path(__file__).resolve().parent.parent
    css = (root / "src" / "workbench-webui" / "workbench.css").read_text(encoding="utf-8")

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


def test_workbench_memory_related_uses_tag_and_content_matching_not_category_only():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-memory.jsx").read_text(encoding="utf-8")

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


def test_workbench_knowledge_folders_group_by_kind_not_source():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-knowledge.jsx").read_text(encoding="utf-8")

    # The old source-based grouping is gone from the folders tab.
    folders_block = source.split('if (activeTab === "folders")', 1)[1].split('if (activeTab === "tags")', 1)[0]
    assert "bySource" not in folders_block
    assert "d.source" not in folders_block
    # The new grouping uses visualKind.
    assert "visualKind" in folders_block
    assert "byKind" in folders_block
    assert "FOLDER_LABELS" in source


def test_workbench_knowledge_tags_are_editable_inline():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-knowledge.jsx").read_text(encoding="utf-8")

    assert "function KbTagEditor" in source
    assert "wb-kb-tag-input" in source
    assert "wb-kb-tag-edit-btn" in source
    assert "onSaveTags" in source
    assert "handleSaveTags" in source
    # The PATCH API is used for saving tags.
    assert "client.update" in source


def test_workbench_knowledge_content_tab_renders_markdown_chunks():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-knowledge.jsx").read_text(encoding="utf-8")

    assert "renderChunkHtml" in source
    assert "window.marked" in source
    assert "DOMPurify" in source
    assert "dangerouslySetInnerHTML" in source
    assert "wb-kb-chunk-md" in source
    assert "wb-kb-chunk-text" in source


def test_workbench_knowledge_list_does_not_silently_truncate():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-knowledge.jsx").read_text(encoding="utf-8")

    # The old silent limit:500 is gone.
    assert "limit: 500" not in source
    # The total count from the backend is used to show truncation awareness.
    assert "_total" in source
    assert "totalDocs" in source
    assert "显示前" in source


def test_packaged_electron_preserves_explicit_runtime_path_overrides():
    root = Path(__file__).resolve().parent.parent
    source = (root / "electron" / "main.js").read_text(encoding="utf-8")

    assert "process.env.CYRENE_USER_DATA_DIR || getCyreneUserDataDir()" in source
    assert "process.env.CYRENE_CACHE_DIR || getCyreneCacheDir()" in source
    assert "process.env.CYRENE_TEMP_DIR || getCyreneTempDir()" in source


def test_workbench_composers_upload_files_pasted_from_clipboard():
    root = Path(__file__).resolve().parent.parent
    chat = (root / "src" / "workbench-webui" / "workbench-chat.jsx").read_text(encoding="utf-8")
    task = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")

    for source in (chat, task):
        assert "onPaste={onPaste}" in source
        assert "clipboard.files" in source
        assert "clipboard.items" in source
        assert 'item.kind === "file" ? item.getAsFile() : null' in source
        assert "if (!files.length) return; // Preserve the browser's normal text paste." in source
        assert "event.preventDefault();" in source
        assert "addFiles(files);" in source
