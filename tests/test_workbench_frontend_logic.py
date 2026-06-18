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


def test_workbench_regenerate_plan_fallback_updates_acceptance():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    regenerate_block = source.split("regeneratePlan: function ()", 1)[1].split("approvePlan: function ()", 1)[0]

    assert "plan: model.buildPlanSteps(goal, constraints)" in regenerate_block
    assert "acceptanceCriteria: model.buildAcceptance(goal, constraints)" in regenerate_block


def test_workbench_plan_conflict_does_not_apply_client_fallback():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(encoding="utf-8")
    model = (root / "src" / "workbench-webui" / "workbench-model.jsx").read_text(encoding="utf-8")

    assert 'err.code === "stale_plan_revision"' in source
    assert "rethrowPlanConflict(err);" in source
    assert "error.code = payload.code" in model
