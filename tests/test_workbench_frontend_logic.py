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

    assert "var [runtimes, setRuntimes] = useWbcState({});" in source
    assert "var abortRefs = useWbcRef({});" in source
    assert "var activeRuntime = runtimes[activeChatId] || null;" in source
    assert "if (runtimesRef.current[chatId]) return;" in source
    assert "otherRunning" not in source
    assert "workbenchChat.lockedByOther" not in source
    assert "workbenchChat.lockedByOther" not in i18n


def test_workbench_acceptance_button_calls_agent_endpoint():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "workbench-webui" / "workbench-model.jsx").read_text(encoding="utf-8")

    assert "window.WorkbenchModel.generateAcceptance(session.id)" in source
    assert '"/acceptance/generate"' in model


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
    assert "settings-overlay.js?v=20260618-model-save-fix1" in index


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
