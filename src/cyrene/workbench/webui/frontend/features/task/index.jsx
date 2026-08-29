import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WbcVoice, wbcCreateComposerVoiceFeedback, wbcStartVoiceRecorder, wbcTranscribeVoiceBlob } from "../../workbench-chat.jsx"
import { WbColResizer } from "../layout/right-panel-resizer.jsx"
import { wbLiveActivityLines } from "../session/activity.jsx"
import {
  effectiveStepPrompt,
  formatDurationSec,
  isDoneStepStatus,
  isResolvedStepStatus,
  isRunningStepStatus,
  splitStepContextFiles,
  stepDurationText,
  stepExecutionPrompt,
  stepMetaText,
  useTaskController,
} from "./controller.jsx"
import { wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"
import { wbErrorText } from "../../shared/errors.jsx"
import { WorkbenchFileDropOverlay, useWorkbenchFileDrop } from "../../shared/file-drop.jsx"
import { TaskBoard } from "./board.jsx"
import { RightContextPanel } from "./context-panel.jsx"
import { ICONS, compactText, hasAcceptanceFailure, priorityText, sessionSummaryText, wbRealGoal, wbRenderMarkdown, wbT } from "./presentation.jsx"

var {
  useEffect: useWorkbenchEffect,
  useMemo: useWorkbenchMemo,
  useRef: useWorkbenchRef,
  useState: useWorkbenchState,
} = React;
var WorkbenchModel = workbenchServices.model();

function wbTaskPaneSessionFromStore(store, taskId) {
  var id = String(taskId || "");
  if (!store || !id) return null;
  if (store.session && String(store.session.id || "") === id) return store.session;
  if (store.activeSession && String(store.activeSession.id || "") === id) return store.activeSession;
  var projects = Array.isArray(store.projects) ? store.projects : [];
  for (var index = 0; index < projects.length; index += 1) {
    var sessions = Array.isArray(projects[index].sessions) ? projects[index].sessions : [];
    var found = sessions.find(function (session) { return String(session && session.id || "") === id; });
    if (found) return found;
  }
  return null;
}

function WorkbenchTaskPane({ taskId, project, onTaskStoreChange, onOpenTask, detached, split, onSessionChange, onRightTab }) {
  var id = String(taskId || "");
  var initial = project && Array.isArray(project.sessions) ? project.sessions.find(function (session) {
    return String(session && session.id || "") === id;
  }) : null;
  var [session, setSession] = useWorkbenchState(initial || null);
  var [loading, setLoading] = useWorkbenchState(!initial || !!initial.isSummary);
  var [error, setError] = useWorkbenchState("");
  var [expandedStepId, setExpandedStepId] = useWorkbenchState("");
  var [rightTab, setRightTab] = useWorkbenchState("context");
  var [floatingPanelOpen, setFloatingPanelOpen] = useWorkbenchState(false);
  var floatingPanelRef = useWorkbenchRef(null);

  function adoptStore(nextStore) {
    var nextSession = wbTaskPaneSessionFromStore(nextStore, id);
    if (nextSession) setSession(Object.assign({}, nextSession, { isSummary: false }));
    if (onTaskStoreChange) onTaskStoreChange(nextStore, id);
    return nextStore;
  }

  useWorkbenchEffect(function () {
    var cancelled = false;
    var summary = project && Array.isArray(project.sessions) ? project.sessions.find(function (item) {
      return String(item && item.id || "") === id;
    }) : null;
    setSession(summary || null);
    setExpandedStepId("");
    setRightTab("context");
    setFloatingPanelOpen(false);
    setError("");
    if (!id) { setLoading(false); return undefined; }
    setLoading(true);
    workbenchServices.model().fetchSession(id).then(function (payload) {
      if (cancelled) return;
      var nextSession = wbTaskPaneSessionFromStore(payload, id);
      if (nextSession) setSession(Object.assign({}, nextSession, { isSummary: false }));
    }).catch(function (err) {
      if (!cancelled) setError(wbErrorText(err));
    }).finally(function () {
      if (!cancelled) setLoading(false);
    });
    return function () { cancelled = true; };
  }, [id, project && project.id]);

  useWorkbenchEffect(function () {
    if (onSessionChange) onSessionChange(session);
  }, [session, onSessionChange]);

  useWorkbenchEffect(function () {
    function openTaskContextPanel(event) {
      var detail = event && event.detail || {};
      if (!split || String(detail.taskId || "") !== id) return;
      if (detail.tab) setRightTab(String(detail.tab));
      setFloatingPanelOpen(true);
    }
    window.addEventListener("cyrene:open-task-context-panel", openTaskContextPanel);
    return function () {
      window.removeEventListener("cyrene:open-task-context-panel", openTaskContextPanel);
    };
  }, [id, split]);

  useWorkbenchEffect(function () {
    if (!split || !floatingPanelOpen) return undefined;
    function closeOutside(event) {
      if (floatingPanelRef.current && !floatingPanelRef.current.contains(event.target)) {
        setFloatingPanelOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeOutside);
    return function () { document.removeEventListener("pointerdown", closeOutside); };
  }, [split, floatingPanelOpen]);

  function openRightTab(tab) {
    var next = String(tab || "context");
    setRightTab(next);
    if (split) setFloatingPanelOpen(true);
    else if (onRightTab) onRightTab(next);
  }

  function patchLocal(patch) {
    setSession(function (current) { return current ? Object.assign({}, current, patch || {}) : current; });
  }

  return (
    <div className={"wbc-task-pane" + (detached ? " is-detached" : "")} data-task-id={id}>
      <TaskWorkArea
        key={id || "none"}
        project={project}
        session={session}
        expandedStepId={expandedStepId}
        onToggleStep={function (stepId) { setExpandedStepId(expandedStepId === stepId ? "" : stepId); }}
        onCreateRun={adoptStore}
        onRightTab={openRightTab}
        onSelectSession={onOpenTask}
        onBackToBoard={function () {
          try { window.dispatchEvent(new CustomEvent("cyrene:open-workbench-board")); } catch (e) {}
        }}
        onInitPatch={patchLocal}
        onLocalPatch={patchLocal}
        onRefresh={adoptStore}
        error={error}
        loading={loading}
        active={true}
      />
      {split && floatingPanelOpen ? (
        <div ref={floatingPanelRef} className="wbc-task-pane-floating-panel" role="dialog" aria-label={wbT("task.side.detailPanel", "Task details")}>
          <RightContextPanel
            project={project}
            session={session}
            expandedStepId={expandedStepId}
            tab={rightTab}
            onTabChange={setRightTab}
            onRefresh={adoptStore}
            onToggleSide={function () { setFloatingPanelOpen(false); }}
            floating={true}
          />
        </div>
      ) : null}
    </div>
  );
}

window.CyreneTaskPane = WorkbenchTaskPane;

// ===================================================================
// Task execution console — the Subtask state machine.
// idle → planning → waiting_for_approval → running → review →
// completed, with paused / failed / cancelled branches. Driven from
// the client via model.patchSession(); real agent work via createRun().
// ===================================================================

// Permission modes for the composer mode-switcher (mirrors the legacy chat
// modes; the workbench default is "auto" since it executes tasks).
var WB_MODES = [
  { id: "default", labelKey: "workbenchChat.mode.default.label", descKey: "workbenchChat.mode.default.desc", icon: ICONS.modeDefault },
  { id: "auto", labelKey: "workbenchChat.mode.auto.label", descKey: "workbenchChat.mode.auto.desc", icon: ICONS.modeAuto },
  { id: "plan", labelKey: "workbenchChat.mode.plan.label", descKey: "workbenchChat.mode.plan.desc", icon: ICONS.modePlan },
  { id: "full_access", labelKey: "workbenchChat.mode.full_access.label", descKey: "workbenchChat.mode.full_access.desc", icon: ICONS.modeFull },
];

function wbModeMeta(id) {
  var meta = WB_MODES[1];
  for (var i = 0; i < WB_MODES.length; i++) {
    if (WB_MODES[i].id === id) meta = WB_MODES[i];
  }
  return { ...meta, label: wbT(meta.labelKey, meta.id), desc: wbT(meta.descKey, "") };
}

var WB_REASONING_EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"];

function wbSupportedReasoningEfforts(model) {
  var raw = model && (
    model.supportedReasoningEfforts
    || model.supported_reasoning_efforts
  );
  var efforts = (Array.isArray(raw) ? raw : []).map(function (option) {
    return String(
      option && (option.reasoningEffort || option.reasoning_effort)
      || option
      || ""
    ).trim().toLowerCase();
  }).filter(function (effort) {
    return WB_REASONING_EFFORT_ORDER.indexOf(effort) >= 0;
  });
  if (!efforts.length && model) efforts = ["low", "medium", "high"];
  return Array.from(new Set(efforts)).sort(function (a, b) {
    return WB_REASONING_EFFORT_ORDER.indexOf(a) - WB_REASONING_EFFORT_ORDER.indexOf(b);
  });
}

function wbFriendlyModelName(model, fallback) {
  var configuredName = String(model && model.name || "").trim();
  var modelId = String(model && model.model || fallback || "").trim();
  if (configuredName && configuredName !== modelId) return configuredName;
  if (!modelId) return configuredName;
  var words = modelId.replace(/^gpt-/i, "").split(/[-_]+/).filter(Boolean);
  return words.map(function (word) {
    if (/^\d/.test(word)) return word.toUpperCase();
    if (word.toLowerCase() === "deepseek") return "DeepSeek";
    return word.charAt(0).toUpperCase() + word.slice(1);
  }).join(" ");
}

function GoalLoopWizard({ session, onClose, onStarted }) {
  var model = workbenchServices.model();
  var [phase, setPhase] = useWorkbenchState("config");
  var [goal, setGoal] = useWorkbenchState(String(session.goal || ""));
  var [maxHours, setMaxHours] = useWorkbenchState(2);
  var [maxRepairs, setMaxRepairs] = useWorkbenchState(3);
  var [permissionMode, setPermissionMode] = useWorkbenchState("auto");
  var [reflectionMode, setReflectionMode] = useWorkbenchState("proactive");
  var [fullAccessConfirmed, setFullAccessConfirmed] = useWorkbenchState(false);
  var [preview, setPreview] = useWorkbenchState(null);
  var [busy, setBusy] = useWorkbenchState(false);
  var [error, setError] = useWorkbenchState("");

  function previewInput() {
    return {
      goal: goal.trim(),
      maxRuntimeHours: Number(maxHours),
      maxRepairRounds: Number(maxRepairs),
      permissionMode: permissionMode,
      reflectionMode: reflectionMode,
      fullAccessConfirmed: permissionMode !== "full_access" || fullAccessConfirmed,
      basePlanDefinitionRevision: Number(session.planDefinitionRevision || 0),
    };
  }

  function generatePreview() {
    setError("");
    if (goal.trim().length < 3) {
      setError(wbT("goalLoop.validation.goal", "请输入清晰的目标。"));
      return;
    }
    if (permissionMode === "full_access" && !fullAccessConfirmed) {
      setError(wbT("goalLoop.validation.fullAccess", "请先确认完全访问风险。"));
      return;
    }
    setBusy(true);
    model.previewGoalLoop(session.id, previewInput())
      .then(function (result) {
        setPreview(result);
        setPhase("preview");
      })
      .catch(function (err) { setError(wbErrorText(err)); })
      .finally(function () { setBusy(false); });
  }

  function start() {
    if (!preview || !preview.draftId) return;
    setError("");
    setBusy(true);
    model.startGoalLoop(session.id, preview.draftId)
      .then(function (store) {
        if (onStarted) onStarted(store);
        if (onClose) onClose();
      })
      .catch(function (err) { setError(wbErrorText(err)); })
      .finally(function () { setBusy(false); });
  }

  return (
    <div className="workbench-confirm-scrim wb-goal-loop-scrim" onMouseDown={function (event) { if (!busy && event.target === event.currentTarget) onClose(); }}>
      <div className="wb-goal-loop-modal" role="dialog" aria-modal="true" aria-labelledby="goal-loop-title">
        <div className="wb-goal-loop-head">
          <div>
            <span className="wb-goal-loop-eyebrow">{wbT("goalLoop.eyebrow", "持续执行模式")}</span>
            <h2 id="goal-loop-title">{phase === "config" ? wbT("goalLoop.configure.title", "确认目标和退出条件") : wbT("goalLoop.preview.title", "确认持续执行方案")}</h2>
          </div>
          <button type="button" className="workbench-toast-close" disabled={busy} onClick={onClose} aria-label={wbT("common.close", "关闭")}>{ICONS.x}</button>
        </div>
        <div className="wb-goal-loop-steps" aria-label={wbT("goalLoop.steps", "配置进度")}>
          <span className="done">1</span><i />
          <span className={phase === "preview" ? "done" : "active"}>2</span><i />
          <span className={phase === "preview" ? "active" : ""}>3</span>
        </div>

        {phase === "config" ? (
          <div className="wb-goal-loop-body">
            <label className="wb-goal-loop-field">
              <span>{wbT("goalLoop.field.goal", "目标")}</span>
              <small>{wbT("goalLoop.field.goalHint", "描述最终应达到的状态，不要只填写执行步骤。")}</small>
              <textarea value={goal} rows={5} onChange={function (event) { setGoal(event.target.value); }} />
            </label>
            <div className="wb-goal-loop-grid">
              <label className="wb-goal-loop-field">
                <span>{wbT("goalLoop.field.runtime", "最大运行时间")}</span>
                <small>{wbT("goalLoop.field.runtimeHint", "暂停和等待确认期间不计时。")}</small>
                <div className="wb-goal-loop-number"><input type="number" min="0.5" max="24" step="0.5" value={maxHours} onChange={function (event) { setMaxHours(event.target.value); }} /><b>{wbT("goalLoop.hours", "小时")}</b></div>
              </label>
              <label className="wb-goal-loop-field">
                <span>{wbT("goalLoop.field.repairs", "最大返工轮数")}</span>
                <small>{wbT("goalLoop.field.repairsHint", "一次验收失败并重新修复计为一轮。")}</small>
                <div className="wb-goal-loop-number"><input type="number" min="0" max="10" step="1" value={maxRepairs} onChange={function (event) { setMaxRepairs(event.target.value); }} /><b>{wbT("goalLoop.rounds", "轮")}</b></div>
              </label>
            </div>

            <fieldset className="wb-goal-loop-options">
              <legend>{wbT("goalLoop.field.permission", "权限模式")}</legend>
              <button type="button" className={permissionMode === "auto" ? "selected" : ""} onClick={function () { setPermissionMode("auto"); }}>
                <b>{wbT("goalLoop.permission.auto", "Auto（推荐）")}</b>
                <small>{wbT("goalLoop.permission.autoHint", "自动审核权限边界，必要时暂停等待你确认。")}</small>
              </button>
              <button type="button" className={permissionMode === "full_access" ? "selected danger" : ""} onClick={function () { setPermissionMode("full_access"); }}>
                <b>{wbT("goalLoop.permission.full", "完全访问")}</b>
                <small>{wbT("goalLoop.permission.fullHint", "减少权限中断，但可能修改工作区外的文件。")}</small>
              </button>
            </fieldset>
            {permissionMode === "full_access" && (
              <label className="wb-goal-loop-warning">
                <input type="checkbox" checked={fullAccessConfirmed} onChange={function (event) { setFullAccessConfirmed(event.target.checked); }} />
                <span>{wbT("goalLoop.permission.confirm", "我理解完全访问的风险，并同意在本次持续任务中授予该权限。")}</span>
              </label>
            )}

            <fieldset className="wb-goal-loop-options reflection">
              <legend>{wbT("goalLoop.field.reflection", "深度思考强度")}</legend>
              {[
                ["standard", wbT("goalLoop.reflection.standard", "标准"), wbT("goalLoop.reflection.standardHint", "验收失败或明显停滞时调用。")],
                ["proactive", wbT("goalLoop.reflection.proactive", "主动（推荐）"), wbT("goalLoop.reflection.proactiveHint", "在最终验收前和验收失败后主动检查方向。")],
                ["frequent", wbT("goalLoop.reflection.frequent", "高频"), wbT("goalLoop.reflection.frequentHint", "每个步骤完成后及返工时调用，成本最高。")],
              ].map(function (option) {
                return <button type="button" key={option[0]} className={reflectionMode === option[0] ? "selected" : ""} onClick={function () { setReflectionMode(option[0]); }}><b>{option[1]}</b><small>{option[2]}</small></button>;
              })}
            </fieldset>
            <p className="wb-goal-loop-cost">{wbT("goalLoop.costHint", "较高的深度思考强度会增加模型调用、运行时间和成本。首次启动不会调用深度反思。")}</p>
          </div>
        ) : (
          <div className="wb-goal-loop-body preview">
            {preview.goalChanged && <div className="wb-goal-loop-change">{wbT("goalLoop.goalChanged", "目标已改变，原计划已失效。下面是基于新目标重新生成的计划和验收条件。")}</div>}
            <section><h3>{wbT("goalLoop.preview.goal", "目标")}</h3><p>{preview.goal}</p></section>
            <div className="wb-goal-loop-summary">
              <span><small>{wbT("goalLoop.field.runtime", "最大运行时间")}</small><b>{preview.limits.maxRuntimeHours} {wbT("goalLoop.hours", "小时")}</b></span>
              <span><small>{wbT("goalLoop.field.repairs", "最大返工轮数")}</small><b>{preview.limits.maxRepairRounds} {wbT("goalLoop.rounds", "轮")}</b></span>
              <span><small>{wbT("goalLoop.field.permission", "权限模式")}</small><b>{preview.limits.permissionMode === "full_access" ? wbT("goalLoop.permission.full", "完全访问") : wbT("goalLoop.permission.autoShort", "自动")}</b></span>
              <span><small>{wbT("goalLoop.field.reflection", "深度思考强度")}</small><b>{preview.limits.reflectionMode === "frequent" ? wbT("goalLoop.reflection.frequent", "高频") : preview.limits.reflectionMode === "standard" ? wbT("goalLoop.reflection.standard", "标准") : wbT("goalLoop.reflection.proactive", "主动")}</b></span>
            </div>
            <section><h3>{wbT("goalLoop.preview.plan", "执行计划")}</h3><ol>{(preview.plan || []).map(function (step) { return <li key={step.id}><b>{step.title}</b>{step.description && <small>{step.description}</small>}</li>; })}</ol></section>
            <section><h3>{wbT("goalLoop.preview.acceptance", "验收条件")}</h3><ul>{(preview.acceptanceCriteria || []).map(function (item) { return <li key={item.id}>{item.text}</li>; })}</ul></section>
          </div>
        )}

        {error && <div className="wb-goal-loop-error">{error}</div>}
        <div className="wb-goal-loop-foot">
          <button type="button" className="wb-btn ghost" disabled={busy} onClick={phase === "preview" ? function () { setPhase("config"); setError(""); } : onClose}>{phase === "preview" ? wbT("goalLoop.back", "返回修改") : wbT("common.cancel", "取消")}</button>
          {phase === "preview" && <button type="button" className="wb-btn ghost" disabled={busy} onClick={generatePreview}>{wbT("goalLoop.regenerate", "重新生成")}</button>}
          <button type="button" className="wb-btn primary" disabled={busy} onClick={phase === "preview" ? start : generatePreview}>{busy ? wbT("goalLoop.working", "处理中…") : phase === "preview" ? wbT("goalLoop.start", "确认并开始持续执行") : wbT("goalLoop.generate", "生成计划和验收条件")}</button>
        </div>
      </div>
    </div>
  );
}

// Adjust-and-continue dialog for a paused goal loop. The loop pauses when it
// hits the runtime / repair-round budget; bumping the budget here and resuming
// is the only way to make progress past those limits (a plain resume would just
// re-pause). Reuses the wizard's field styling in a compact modal.
function GoalLoopLimitsDialog({ session, onClose, onSaved }) {
  var model = workbenchServices.model();
  var loop = (session && session.goalLoop) || {};
  var [maxHours, setMaxHours] = useWorkbenchState(Math.max(0.5, Math.round((Number(loop.maxActiveSeconds || 7200) / 3600) * 2) / 2));
  var [maxRepairs, setMaxRepairs] = useWorkbenchState(Number(loop.maxRepairRounds || 3));
  var [reflectionMode, setReflectionMode] = useWorkbenchState(String(loop.reflectionMode || "proactive"));
  var [busy, setBusy] = useWorkbenchState(false);
  var [error, setError] = useWorkbenchState("");
  var reasonHint = loop.stopReason === "max_runtime"
    ? wbT("goalLoop.limits.reasonRuntime", "已达到最大运行时间。增加运行时间后即可继续。")
    : loop.stopReason === "max_repair_rounds"
    ? wbT("goalLoop.limits.reasonRepairs", "已达到最大返工轮数。增加返工轮数后即可继续。")
    : wbT("goalLoop.limits.reason", "调整退出条件后继续持续执行。");

  function save() {
    setError("");
    setBusy(true);
    model.updateGoalLoopLimits(session.id, {
      maxRuntimeHours: Number(maxHours),
      maxRepairRounds: Number(maxRepairs),
      reflectionMode: reflectionMode,
    })
      .then(function () { return model.resumeGoalLoop(session.id); })
      .then(function (store) { if (onSaved) onSaved(store); if (onClose) onClose(); })
      .catch(function (err) { setError(wbErrorText(err)); })
      .finally(function () { setBusy(false); });
  }

  return (
    <div className="workbench-confirm-scrim wb-goal-loop-scrim" onMouseDown={function (event) { if (!busy && event.target === event.currentTarget) onClose(); }}>
      <div className="wb-goal-loop-modal compact" role="dialog" aria-modal="true" aria-labelledby="goal-loop-limits-title">
        <div className="wb-goal-loop-head">
          <div>
            <span className="wb-goal-loop-eyebrow">{wbT("goalLoop.eyebrow", "持续执行模式")}</span>
            <h2 id="goal-loop-limits-title">{wbT("goalLoop.limits.title", "调整限制并继续")}</h2>
          </div>
          <button type="button" className="workbench-toast-close" disabled={busy} onClick={onClose} aria-label={wbT("common.close", "关闭")}>{ICONS.x}</button>
        </div>
        <div className="wb-goal-loop-body">
          <div className="wb-goal-loop-change">{reasonHint}</div>
          <div className="wb-goal-loop-grid">
            <label className="wb-goal-loop-field">
              <span>{wbT("goalLoop.field.runtime", "最大运行时间")}</span>
              <div className="wb-goal-loop-number"><input type="number" min="0.5" max="24" step="0.5" value={maxHours} onChange={function (event) { setMaxHours(event.target.value); }} /><b>{wbT("goalLoop.hours", "小时")}</b></div>
            </label>
            <label className="wb-goal-loop-field">
              <span>{wbT("goalLoop.field.repairs", "最大返工轮数")}</span>
              <div className="wb-goal-loop-number"><input type="number" min="0" max="10" step="1" value={maxRepairs} onChange={function (event) { setMaxRepairs(event.target.value); }} /><b>{wbT("goalLoop.rounds", "轮")}</b></div>
            </label>
          </div>
          <fieldset className="wb-goal-loop-options reflection">
            <legend>{wbT("goalLoop.field.reflection", "深度思考强度")}</legend>
            {[
              ["standard", wbT("goalLoop.reflection.standard", "标准")],
              ["proactive", wbT("goalLoop.reflection.proactive", "主动（推荐）")],
              ["frequent", wbT("goalLoop.reflection.frequent", "高频")],
            ].map(function (option) {
              return <button type="button" key={option[0]} className={reflectionMode === option[0] ? "selected" : ""} onClick={function () { setReflectionMode(option[0]); }}><b>{option[1]}</b></button>;
            })}
          </fieldset>
        </div>
        {error && <div className="wb-goal-loop-error">{error}</div>}
        <div className="wb-goal-loop-foot">
          <button type="button" className="wb-btn ghost" disabled={busy} onClick={onClose}>{wbT("common.cancel", "取消")}</button>
          <button type="button" className="wb-btn primary" disabled={busy} onClick={save}>{busy ? wbT("goalLoop.working", "处理中…") : wbT("goalLoop.limits.save", "保存并继续")}</button>
        </div>
      </div>
    </div>
  );
}

function TaskWorkArea(props) {
  var project = props.project;
  var session = props.session;
  var active = props.active !== false;
  var mainRef = useWorkbenchRef(null);
  var [attachments, setAttachments] = useWorkbenchState([]);
  var [mode, setMode] = useWorkbenchState("auto");
  var [configuredModels, setConfiguredModels] = useWorkbenchState([]);
  var [selectedModelId, setSelectedModelId] = useWorkbenchState("");
  var [reasoningEffort, setReasoningEffort] = useWorkbenchState("");
  var [goalLoopOpen, setGoalLoopOpen] = useWorkbenchState(false);
  var [goalLoopLimitsOpen, setGoalLoopLimitsOpen] = useWorkbenchState(false);
  var sid = session ? session.id : "";
  // Pending attachments belong to the task being composed — reset on switch.
  useWorkbenchEffect(function () { setAttachments([]); }, [sid]);
  useWorkbenchEffect(function () {
    var cancelled = false;
    function loadConfiguredModels() {
      return workbenchServices.api().json("/api/settings/model-config", { toast: false })
      .then(function (payload) {
        var options = Array.isArray(payload.selectable_models) ? payload.selectable_models : [];
        function applyInitialModels(items) {
          if (cancelled) return;
          setConfiguredModels(items);
          var sessionSelection = String(
            session && (session.modelSelectionId || session.model) || ""
          ).trim();
          var selected = items.find(function (item) {
            return sessionSelection && [
              String(item.id || ""),
              String(item.model || ""),
              String(item.name || ""),
            ].indexOf(sessionSelection) >= 0;
          }) || items.find(function (item) {
            return String(item.id || "") === String(payload.active || "");
          }) || items[0];
          if (selected) {
            setSelectedModelId(String(selected.id || selected.model || ""));
            setReasoningEffort(String(
              session && session.reasoningEffort
              || selected.reasoning_effort
              || ""
            ).trim().toLowerCase());
          } else {
            setSelectedModelId("");
            setReasoningEffort("");
          }
        }
        // Render the picker as soon as the configured model list arrives.
        // Codex capability metadata is optional enrichment and must not delay UI.
        applyInitialModels(options);
        var needsCodexCatalog = options.some(function (item) {
          return String(item.provider || "") === "codex_oauth";
        });
        var catalogRequest = needsCodexCatalog
          ? workbenchServices.api().json("/api/settings/openai-oauth", { toast: false }).catch(function () { return {}; })
          : Promise.resolve({});
        return catalogRequest.then(function (catalog) {
          if (cancelled) return;
          var codexModels = Array.isArray(catalog.models) ? catalog.models : [];
          options = options.map(function (item) {
            if (String(item.provider || "") !== "codex_oauth") return item;
            var match = codexModels.find(function (entry) {
              var id = String(entry.model || entry.id || entry.slug || "").trim();
              return id === String(item.model || "").trim();
            });
            return match ? Object.assign({}, item, {
              supportedReasoningEfforts: match.supportedReasoningEfforts || match.supported_reasoning_efforts || [],
            }) : item;
          });
          setConfiguredModels(options);
        });
      })
      .catch(function () {
        if (!cancelled) setConfiguredModels([]);
      });
    }
    function onModelConfigurationChanged() { loadConfiguredModels(); }
    loadConfiguredModels();
    window.addEventListener("cyrene:model-configuration-changed", onModelConfigurationChanged);
    window.addEventListener("cyrene:plugins-changed", onModelConfigurationChanged);
    return function () {
      cancelled = true;
      window.removeEventListener("cyrene:model-configuration-changed", onModelConfigurationChanged);
      window.removeEventListener("cyrene:plugins-changed", onModelConfigurationChanged);
    };
  }, [sid]);
  // Match the conversation surface: the fixed glass header and composer float
  // over one scrolling task plane. Dynamic reserves keep the first and last
  // cards fully reachable while still allowing scrolled content to become the
  // glass backdrop. One observer covers responsive title wrapping, chips,
  // attachments and textarea growth without polling on every keystroke.
  useWorkbenchEffect(function () {
    var main = mainRef.current;
    if (!main) return undefined;
    var composer = main.querySelector(":scope > .wbc-composer");
    var header = main.querySelector(":scope > .workbench-task-header-sticky");
    if (!composer || !header) return undefined;
    var resizeRaf = 0;
    var lastComposerHeight = 0;
    var lastHeaderHeight = 0;
    function commitFloatingReserveHeights() {
      resizeRaf = 0;
      var composerHeight = Math.ceil(composer.getBoundingClientRect().height);
      if (composerHeight > 0 && composerHeight !== lastComposerHeight) {
        lastComposerHeight = composerHeight;
        main.style.setProperty("--wbc-composer-reserve-height", composerHeight + "px");
      }
      var headerHeight = Math.ceil(header.getBoundingClientRect().height);
      if (headerHeight > 0 && headerHeight !== lastHeaderHeight) {
        lastHeaderHeight = headerHeight;
        main.style.setProperty("--wbc-task-header-reserve-height", headerHeight + "px");
      }
    }
    function scheduleFloatingReserveHeights() {
      if (resizeRaf) return;
      resizeRaf = requestAnimationFrame(commitFloatingReserveHeights);
    }
    commitFloatingReserveHeights();
    var observer = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleFloatingReserveHeights)
      : null;
    if (observer) {
      observer.observe(composer);
      observer.observe(header);
    }
    window.addEventListener("resize", scheduleFloatingReserveHeights);
    return function () {
      if (observer) observer.disconnect();
      window.removeEventListener("resize", scheduleFloatingReserveHeights);
      if (resizeRaf) cancelAnimationFrame(resizeRaf);
      main.style.removeProperty("--wbc-composer-reserve-height");
      main.style.removeProperty("--wbc-task-header-reserve-height");
    };
  }, [sid, props.loading]);
  var controller = useTaskController(session, props.onRefresh, {
    attachments: attachments,
    mode: mode,
    model: selectedModelId,
    reasoningEffort: reasoningEffort,
    clearAttachments: function () { setAttachments([]); },
    onLocalPatch: props.onLocalPatch,
    onOpenGoalLoop: function () { setGoalLoopOpen(true); },
    onOpenGoalLoopLimits: function () { setGoalLoopLimitsOpen(true); },
  });
  var taskDropEnabled = !!(active && project && session && session.kind !== "init");
  var taskFileDropActive = useWorkbenchFileDrop(function (files) {
    try {
      window.dispatchEvent(new CustomEvent("cyrene:add-task-attachments", { detail: { files: files } }));
    } catch (e) {}
  }, taskDropEnabled);
  if (props.loading && (!project || !session)) {
    return <main ref={mainRef} className="workbench-main"><div className="workbench-empty">{wbT("workbench.loading", "Loading workbench…")}</div></main>;
  }
  if (!project || !session) {
    return <main ref={mainRef} className="workbench-main"><div className="workbench-empty">{wbT("workbench.selectProjectTask", "Select a project and task.")}</div></main>;
  }
  // "初始化项目" onboarding sessions take over the whole work area with their
  // own agent-led question flow (WorkbenchInitView), bypassing the task state
  // machine, plan list and composer below.
  if (session.kind === "init" && workbenchServices.create().InitView) {
    return (
      <main ref={mainRef} className="workbench-main">
        {React.createElement(workbenchServices.create().InitView, {
          project: project,
          session: session,
          onRefresh: props.onRefresh,
          onInitPatch: props.onInitPatch,
          onBackToBoard: props.onBackToBoard,
        })}
      </main>
    );
  }
  var status = String(session.status || "idle");
  var showPlan = ["planning", "waiting_for_approval", "waiting_for_user", "running", "review", "paused", "failed", "blocked", "done", "completed"].indexOf(status) >= 0
    && Array.isArray(session.plan);
  return (
    <main ref={mainRef} className="workbench-main">
      {taskFileDropActive && <WorkbenchFileDropOverlay label={wbT("workbenchChat.dropToAttach", "Release to add files to the task input")} />}
      <div className="workbench-task-header-sticky">
        <TaskHeader project={project} session={session} controller={controller} onRightTab={props.onRightTab} onSelectSession={props.onSelectSession} />
      </div>
      <div className="workbench-stage">
        {props.error && <div className="workbench-error">{props.error}</div>}
        <ReflectionHintBanner session={session} controller={controller} />
        <StateCard
          session={session}
          project={project}
          controller={controller}
          onRightTab={props.onRightTab}
          onSelectSession={props.onSelectSession}
        />
        {showPlan && (
          <TaskPlanList
            session={session}
            expandedStepId={props.expandedStepId}
            onToggleStep={props.onToggleStep}
            onRightTab={props.onRightTab}
            controller={controller}
          />
        )}
      </div>
      <TaskComposer
        session={session}
        controller={controller}
        onRightTab={props.onRightTab}
        attachments={attachments}
        onAttachmentsChange={setAttachments}
        mode={mode}
        onModeChange={setMode}
        configuredModels={configuredModels}
        selectedModelId={selectedModelId}
        onSelectedModelIdChange={setSelectedModelId}
        reasoningEffort={reasoningEffort}
        onReasoningEffortChange={setReasoningEffort}
      />
      {goalLoopOpen && <GoalLoopWizard session={session} onClose={function () { setGoalLoopOpen(false); }} onStarted={props.onRefresh} />}
      {goalLoopLimitsOpen && <GoalLoopLimitsDialog session={session} onClose={function () { setGoalLoopLimitsOpen(false); }} onSaved={props.onRefresh} />}
    </main>
  );
}

// Banner above the task card: a sibling task's reflection produced an insight
// relevant to THIS task. Suggestion only — the user adopts (merges the packet
// into this session's reflection) or ignores it. Never auto-applied.
function ReflectionHintBanner({ session, controller }) {
  var hints = Array.isArray(session.pendingHints) ? session.pendingHints : [];
  var pending = hints.filter(function (h) { return h && h.status === "pending"; });
  if (pending.length === 0) return null;
  return (
    <div className="wb-hint-stack">
      {pending.map(function (h) {
        return (
          <div className="wb-hint-banner" key={h.id}>
            <span className="wb-hint-icon">{ICONS.spark}</span>
            <div className="wb-hint-body">
              <div className="wb-hint-label">
                {wbT("task.hint.label", "来自相关任务的启发")}
                {h.fromTitle ? " · 《" + h.fromTitle + "》" : ""}
              </div>
              <div className="wb-hint-text">{h.hint}</div>
            </div>
            <div className="wb-hint-actions">
              <WbBtn kind="primary" disabled={controller.busy} onClick={function () { controller.acceptHint(h.id); }}>
                {wbT("task.hint.accept", "纳入")}
              </WbBtn>
              <WbBtn kind="ghost" disabled={controller.busy} onClick={function () { controller.dismissHint(h.id); }}>
                {wbT("task.hint.dismiss", "忽略")}
              </WbBtn>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Picks the primary middle card for the current task status.
function StateCard(props) {
  var status = String(props.session.status || "idle");
  // A background agent op (规划 / 反思 / 验收) is in flight but the task status
  // hasn't moved to `running` — show the live activity card instead of the now
  // stale status card (otherwise a 待验收 task just keeps showing 「已完成」).
  if (props.session.agentBusy && status !== "running") return <AgentActivityCard {...props} />;
  // A run paused for a permission / clarification answer — show the question card
  // (with answer buttons) ahead of the status card, so the round can resume.
  var pq = props.session.pendingQuestion;
  if (pq && pq.id) return <AgentQuestionCard {...props} />;
  if (status === "planning") return <AgentPlanCard {...props} />;
  if (status === "answered") return <AgentReplyCard {...props} />;
  if (status === "acted") return <AgentReplyCard {...props} acted={true} />;
  if (status === "waiting_for_approval" || status === "waiting_for_user") return <ConfirmCard {...props} />;
  if (status === "running") return <AgentActivityCard {...props} />;
  if (status === "paused") return <PausedCard {...props} />;
  if (status === "blocked") return <BlockedCard {...props} />;
  if (status === "failed") return <FailedCard {...props} />;
  if (status === "review" || status === "done") return <CompletionCard {...props} />;
  if (status === "completed") return <CompletionCard {...props} confirmed={true} />;
  if (status === "cancelled") return <CancelledCard {...props} />;
  return <TaskBriefCard {...props} />; // idle / pending / unknown
}

function focusComposer() {
  window.dispatchEvent(new CustomEvent("wb-focus-composer"));
}

function openAcceptanceEditor(onRightTab) {
  if (onRightTab) onRightTab("acceptance");
}

function openNextSession(session, project, onSelectSession) {
  if (!project || !onSelectSession) return;
  var sessions = Array.isArray(project.sessions) ? project.sessions : [];
  var idx = sessions.findIndex(function (s) { return s.id === session.id; });
  var next = sessions[idx + 1] || sessions[0];
  if (next && next.id !== session.id) onSelectSession(next.id);
}

function canPauseTaskStatus(status) {
  return ["running", "waiting_for_user"].indexOf(String(status || "")) >= 0;
}

function TaskHeader({ project, session, controller, onRightTab, onSelectSession }) {
  var tone = WorkbenchModel.statusTone(session.status);
  var status = String(session.status || "idle");
  var [editing, setEditing] = useWorkbenchState(false);
  var [draftTitle, setDraftTitle] = useWorkbenchState(session.title || "");
  var [savingTitle, setSavingTitle] = useWorkbenchState(false);
  var [menuOpen, setMenuOpen] = useWorkbenchState(false);
  var titleInputRef = useWorkbenchRef(null);

  useWorkbenchEffect(function () {
    setDraftTitle(session.title || "");
    setEditing(false);
    setMenuOpen(false);
  }, [session.id]);

  useWorkbenchEffect(function () {
    if (editing && titleInputRef.current) {
      titleInputRef.current.focus();
      titleInputRef.current.select();
    }
  }, [editing]);

  function saveTitle() {
    var nextTitle = String(draftTitle || "").trim();
    if (!nextTitle || nextTitle === session.title) {
      setDraftTitle(session.title || "");
      setEditing(false);
      return;
    }
    setSavingTitle(true);
    workbenchServices.model().patchSession(session.id, { title: nextTitle })
      .then(function (next) {
        if (controller && controller.applyStore) controller.applyStore(next);
      })
      .catch(function (err) {
        workbenchServices.feedback().showToast((err && err.message) || String(err), "error");
        setDraftTitle(session.title || "");
      })
      .finally(function () {
        setSavingTitle(false);
        setEditing(false);
      });
  }

  var menuActions = headerMenuActions(status, controller, session, project, onSelectSession, onRightTab);

  return (
    <div className="workbench-task-header workbench-composer-box wbc-composer-box">
      <div className="wb-th-main">
        <div className="wb-th-title-row">
          {editing ? (
            <input
              ref={titleInputRef}
              className="wb-th-title-input"
              value={draftTitle}
              disabled={savingTitle}
              onChange={function (e) { setDraftTitle(e.target.value); }}
              onBlur={saveTitle}
              onKeyDown={function (e) {
                if (e.key === "Enter") saveTitle();
                if (e.key === "Escape") { setDraftTitle(session.title || ""); setEditing(false); }
              }}
              aria-label={wbT("task.titleLabel", "Task title")}
            />
          ) : (
            <h1 title={session.title}>{session.title}</h1>
          )}
          <div className="wb-th-title-actions">
            {!editing && (
              <button type="button" className="wb-th-iconbtn" onClick={function () { setEditing(true); }} title={wbT("task.editTitle", "Edit title")} aria-label={wbT("task.editTitle", "Edit title")}>
                {ICONS.edit}
              </button>
            )}
            {canPauseTaskStatus(status) && (
              <button
                type="button"
                className="wb-th-control-btn wb-th-pause"
                disabled={controller.busy}
                onClick={function () { status === "running" || session.agentBusy ? controller.interrupt() : controller.pause(); }}
                title={wbT("task.action.pauseTask", "Pause task")}
                aria-label={wbT("task.action.pauseTask", "Pause task")}
              >
                {ICONS.pause}
              </button>
            )}
            <div className="wb-th-menu-wrap">
              <button type="button" className="wb-th-control-btn wb-th-menu-btn" onClick={function () { setMenuOpen(!menuOpen); }} title={wbT("task.detailMenu", "Details menu")} aria-label={wbT("task.detailMenu", "Details menu")}>
                {ICONS.dots}
              </button>
              {menuOpen && (
                <>
                  <div className="wb-th-menu-scrim" onClick={function () { setMenuOpen(false); }}></div>
                  <div className="wb-th-menu">
                    {menuActions.map(function (a, i) {
                      return <button key={"act" + i} type="button" disabled={controller.busy} onClick={function () { setMenuOpen(false); a.onClick(); }}>{a.label}</button>;
                    })}
                    {menuActions.length > 0 && <div className="wb-th-menu-sep" />}
                    <button type="button" onClick={function () { setMenuOpen(false); onRightTab && onRightTab("context"); }}>{wbT("task.menu.viewContext", "View context")}</button>
                    <button type="button" onClick={function () { setMenuOpen(false); onRightTab && onRightTab("logs"); }}>{wbT("task.menu.runLogs", "Run logs")}</button>
                    <button type="button" onClick={function () { setMenuOpen(false); onRightTab && onRightTab("acceptance"); }}>{wbT("task.menu.acceptance", "Acceptance criteria")}</button>
                    <button type="button" onClick={function () { setMenuOpen(false); focusComposer(); }}>{wbT("task.menu.editTask", "Edit task content")}</button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        <p className="wb-th-summary">
          <span className={"wb-th-inline-status " + tone}>{WorkbenchModel.statusText(session.status)}</span>
          <span className="wb-th-summary-text">{sessionSummaryText(session)}</span>
        </p>
        <div className="wb-th-meta">
          <span>{wbT("task.priorityPrefix", "Priority {priority}", { priority: priorityText(session.priority) })}</span>
          <span>{project.name}</span>
        </div>
      </div>
    </div>
  );
}

// Status-dependent secondary actions, folded into the ⋯ menu. The primary action
// surface is the composer quick-chips below; this is overflow. Returns
// [{ label, onClick }]; running/idle add nothing (covered by chips + static items).
function headerMenuActions(status, controller, session, project, onSelectSession, onRightTab) {
  function openNext() { openNextSession(session, project, onSelectSession); }
  if (status === "answered" || status === "acted") {
    return [
      { label: wbT("task.action.promoteToTask", "Make it a task"), onClick: function () { controller.promoteToPlan(); } },
      { label: wbT("task.action.createFollowUp", "Create follow-up task"), onClick: function () { controller.createFollowUp(); } },
    ];
  }
  if (status === "planning") {
    return [
      { label: wbT("task.action.approveExecution", "Approve execution"), onClick: function () { controller.approvePlan(); } },
      { label: wbT("common.cancel", "Cancel"), onClick: function () { controller.cancel(); } },
    ];
  }
  if (status === "waiting_for_approval" || status === "waiting_for_user") {
    return [
      { label: wbT("task.action.approveExecution", "Approve"), onClick: function () { controller.execute(); } },
      { label: wbT("task.action.reject", "Reject"), onClick: function () { controller.reject(); } },
    ];
  }
  if (status === "blocked") {
    if (session && session.goalLoop) {
      return [
        { label: wbT("task.action.resumeTask", "Resume task"), onClick: function () { controller.resume(); } },
        { label: wbT("task.action.viewLogs", "View logs"), guard: false, onClick: function () { onRightTab && onRightTab("logs"); } },
        { label: wbT("task.action.cancelTask", "Cancel task"), onClick: function () { controller.cancel(); } },
      ];
    }
    return [
      { label: wbT("task.action.viewDetails", "View details"), guard: false, onClick: function () { onRightTab && onRightTab("context"); } },
      { label: wbT("task.action.viewLogs", "View logs"), guard: false, onClick: function () { onRightTab && onRightTab("logs"); } },
      { label: wbT("task.action.cancelTask", "Cancel task"), onClick: function () { controller.cancel(); } },
    ];
  }
  if (status === "paused") {
    return [
      { label: wbT("task.action.resumeTask", "Resume task"), onClick: function () { controller.resume(); } },
      { label: wbT("common.cancel", "Cancel"), onClick: function () { controller.cancel(); } },
    ];
  }
  if (status === "failed") {
    return [
      { label: wbT("task.action.retry", "Retry"), onClick: function () { controller.retry(); } },
      { label: wbT("common.cancel", "Cancel"), onClick: function () { controller.cancel(); } },
    ];
  }
  if (status === "review" || status === "done") {
    return [
      { label: wbT("task.action.markComplete", "Mark complete"), onClick: function () { controller.markComplete(); } },
      { label: wbT("task.action.createFollowUp", "Create follow-up task"), onClick: function () { controller.createFollowUp(); } },
      { label: wbT("task.action.openNext", "Open next task"), onClick: openNext },
    ];
  }
  if (status === "completed") {
    return [
      { label: wbT("task.action.reopen", "Reopen"), onClick: function () { controller.reopen(); } },
      { label: wbT("task.action.createFollowUp", "Create follow-up task"), onClick: function () { controller.createFollowUp(); } },
      { label: wbT("task.action.openNext", "Open next task"), onClick: openNext },
    ];
  }
  if (status === "cancelled") {
    return [{ label: wbT("task.action.reopen", "Reopen"), onClick: function () { controller.reopen(); } }];
  }
  return [];
}

// ---- Shared card primitives ------------------------------------------------

function WbCard({ tone, icon, title, badge, children }) {
  return (
    <section className={"wb-card" + (tone ? " " + tone : "")}>
      <div className="wb-card-head">
        <span className="wb-card-icon">{icon}</span>
        <b>{title}</b>
        {badge}
      </div>
      {children}
    </section>
  );
}

function WbActions({ children }) {
  return <div className="wb-card-actions">{children}</div>;
}

function WbBtn({ kind, onClick, disabled, children }) {
  return (
    <button type="button" className={"wb-btn" + (kind ? " " + kind : "")} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

function AgentReplyBlock({ text }) {
  var reply = String(text || "").trim();
  if (!reply) return null;
  return (
    <div className="wb-agent-body markdown" dangerouslySetInnerHTML={{ __html: wbRenderMarkdown(reply) }} />
  );
}

// ---- State cards -----------------------------------------------------------

// idle / pending — task detail + 开始执行.
// Legacy placeholder goal once stamped on blank 新任务 sessions (routes.py
// _workbench_new_session). New tasks now start with an empty goal; this still
// recognizes the old filler in already-stored sessions as "no real goal yet", so
// it is never shown as a goal or handed to the agent. MUST mirror the backend.
function TaskBriefCard({ session, controller }) {
  var goal = wbRealGoal(session);
  var constraints = Array.isArray(session.constraints) ? session.constraints : [];
  var accept = Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
  var hasGoal = !!goal;
  return (
    <WbCard tone="brief" icon={ICONS.target} title={wbT("task.card.details", "Task details")}>
      {hasGoal ? (
        <div className="wb-brief">
          <div className="wb-brief-row"><label>{wbT("task.field.goal", "Task goal")}</label><p>{goal}</p></div>
          {constraints.length > 0 && (
            <div className="wb-brief-row"><label>{wbT("task.field.constraints", "Constraints")}</label>
              <ul className="wb-bullet">{constraints.map(function (c, i) { return <li key={i}>{c}</li>; })}</ul>
            </div>
          )}
          {accept.length > 0 && (
            <div className="wb-brief-row"><label>{wbT("task.field.acceptance", "Acceptance criteria")}</label>
              <ul className="wb-bullet">{accept.map(function (a) { return <li key={a.id}>{a.text}</li>; })}</ul>
            </div>
          )}
        </div>
      ) : (
        <p className="wb-card-hint">{wbT("task.brief.emptyHint", "Just describe a goal or ask a question below. The agent decides whether to answer, take action, or draft a plan first — you don't have to generate a plan up front.")}</p>
      )}
      {/* One primary action per state. Real goal → hand it over (agent auto-judges
          answer/act/plan). No goal yet → 直接开始: the agent reads the project and
          proposes a plan. Either way the composer below stays open for free chat. */}
      {hasGoal ? (
        <React.Fragment>
          <p className="wb-card-hint">{wbT("task.brief.autoHint", "Clicking \"Hand to agent\" starts on this goal right away — the agent decides whether to answer, act, or propose a plan first. You can also keep refining or asking below.")}</p>
          <WbActions>
            <WbBtn kind="primary" disabled={controller.busy} onClick={function () { controller.send(goal); }}>{wbT("task.action.handToAgent", "Hand to agent")}</WbBtn>
          </WbActions>
        </React.Fragment>
      ) : (
        <WbActions>
          <WbBtn kind="primary" disabled={controller.busy} onClick={function () { controller.autoStart(); }}>{wbT("task.action.autoStart", "Start now")}</WbBtn>
        </WbActions>
      )}
    </WbCard>
  );
}

// planning — Agent 回复 with the proposed plan.
function AgentPlanCard({ session, controller, onRightTab }) {
  var plan = Array.isArray(session.plan) ? session.plan : [];
  return (
    <WbCard tone="agent" icon={ICONS.spark} title={wbT("task.card.agentReply", "Agent reply")}>
      <AgentReplyBlock text={session.agentReply || wbT("task.plan.defaultReply", "I will execute this task with the following steps.")} />
      <div className="wb-brief-row"><label>{wbT("task.field.steps", "Execution steps")}</label>
        <ol className="wb-ordered">{plan.map(function (s) { return <li key={s.id}>{s.title}</li>; })}</ol>
      </div>
      <p className="wb-card-hint">{wbT("task.plan.hint", "Continue? After approval, Cyrene will move to confirmation before execution starts.")}</p>
    </WbCard>
  );
}

// answered — a question the agent just answered. acted — a one-shot instruction
// the agent just carried out. Neither generated a plan: show the reply directly,
// and (for acted) what changed, plus a way to promote the exchange into a real
// planned task. Driven by the intent classifier behind /dispatch.
function AgentReplyCard({ session, controller, onRightTab, acted }) {
  var runs = Array.isArray(session.runs) ? session.runs : [];
  var lastRun = runs.length ? runs[runs.length - 1] : null;
  var fileChanges = (lastRun && Array.isArray(lastRun.fileChanges)) ? lastRun.fileChanges : [];
  var toolCalls = (lastRun && Array.isArray(lastRun.toolCalls)) ? lastRun.toolCalls : [];
  return (
    <WbCard tone={acted ? "done" : "agent"} icon={acted ? ICONS.check : ICONS.spark} title={acted ? wbT("task.card.agentActed", "Agent acted") : wbT("task.card.agentReply", "Agent reply")}>
      <AgentReplyBlock text={session.agentReply || (acted ? wbT("task.reply.acted", "Done as instructed.") : wbT("task.reply.answered", "Here is my answer."))} />
      {acted && fileChanges.length > 0 && (
        <div className="wb-done-grid">
          <button type="button" className="wb-done-stat" onClick={function () { onRightTab && onRightTab("files"); }}>
              <b>{fileChanges.length}</b><small>{wbT("task.stat.fileChanges", "File changes")}</small>
          </button>
          {toolCalls.length > 0 && (
            <button type="button" className="wb-done-stat" onClick={function () { onRightTab && onRightTab("logs"); }}>
              <b>{toolCalls.length}</b><small>{wbT("task.stat.toolCalls", "Tool calls")}</small>
            </button>
          )}
        </div>
      )}
      <p className="wb-card-hint">{acted ? wbT("task.reply.actedHint", "Keep chatting below and the agent will judge each message; turn this into a full task if you need structured steps.") : wbT("task.reply.answerHint", "Keep asking or give an instruction below — the agent decides how to handle each one. Promote it into a task if you need a plan.")}</p>
      <WbActions>
        <WbBtn kind="ghost" disabled={controller.busy} onClick={function () { controller.promoteToPlan(); }}>{wbT("task.action.promoteToTask", "Make it a task")}</WbBtn>
        <WbBtn kind="ghost" disabled={controller.busy} onClick={focusComposer}>{wbT("task.action.continueEditing", "Continue")}</WbBtn>
      </WbActions>
    </WbCard>
  );
}

// A run paused awaiting the user's answer to a permission-elevation request or a
// clarification question (ask_user). Renders the question + its options as
// buttons — each answer resumes the SAME round server-side; allowCustom adds a
// free-text reply for open questions.
function AgentQuestionCard({ session, controller }) {
  var pq = (session && session.pendingQuestion) || {};
  var options = Array.isArray(pq.options) ? pq.options : [];
  var kind = String(pq.kind || "");
  var isPermission = workbenchServices.model().isPermissionQuestionKind(kind);
  var permissionText = isPermission
    ? workbenchServices.i18n().permissionQuestionText(pq)
    : "";
  var treeOptions = isPermission && !options.length
    ? [wbT("workbenchChat.approve", "Confirm"), wbT("workbenchChat.reject", "Reject")]
    : options;
  var customState = useWorkbenchState("");
  var customText = customState[0], setCustomText = customState[1];
  var optionSignature = JSON.stringify(treeOptions);
  useWorkbenchEffect(function () {
    if (!pq.id || controller.busy || !window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = workbenchServices.uiSurface();
    var risk = isPermission ? "R3" : "R2";
    var actions = treeOptions.map(function (_opt, index) {
      return {
        action_id: "answer_option_" + index,
        kind: "invoke",
        risk: risk,
        gesture_aliases: ["press"],
        input_schema: {},
      };
    });
    if (pq.allowCustom && !isPermission) {
      actions.push({
        action_id: "answer_custom",
        kind: "set_value",
        risk: "R2",
        gesture_aliases: ["text_input"],
        input_schema: { value: "text<=20000" },
      });
    }
    var handlers = {};
    treeOptions.forEach(function (opt, index) {
      handlers["answer_option_" + index] = function () {
        return Promise.resolve(controller.answer(pq.id, opt)).then(function () {
          return { question_id: String(pq.id), answered: true, option_index: index };
        });
      };
    });
    if (pq.allowCustom && !isPermission) {
      handlers.answer_custom = function (input) {
        var answer = String(input.value || "").trim();
        if (!answer) throw new Error("answer is empty");
        return Promise.resolve(controller.answer(pq.id, answer)).then(function () {
          return { question_id: String(pq.id), answered: true, custom: true };
        });
      };
    }
    return uiSurface.register({
      node_id: "task_question_" + String(pq.id).replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 100),
      parent_id: "root",
      scope: "main",
      get_node: function () {
        if (controller.busy) return null;
        return {
          role: isPermission ? "approval" : "question",
          name: String(permissionText || pq.text || wbT("workbenchChat.questionFallback", "Agent needs your confirmation to continue.")),
          value_summary: treeOptions.length + " options",
          state: {
            session_id: String(session.id || ""),
            session_kind: "task",
            question_id: String(pq.id),
            question_kind: kind,
            permission: isPermission,
            allow_custom: !!pq.allowCustom && !isPermission,
          },
        };
      },
      actions: actions,
      handlers: handlers,
    });
  }, [session.id, pq.id, pq.allowCustom, kind, controller.busy, controller.answer, optionSignature, isPermission, permissionText]);
  function submitCustom() {
    var t = String(customText || "").trim();
    if (!t || controller.busy) return;
    setCustomText("");
    controller.answer(pq.id, t);
  }
  return (
    <WbCard tone="confirm" icon={ICONS.shield} title={isPermission ? wbT("workbenchChat.permissionTitle", "Authorization needed") : wbT("workbenchChat.questionTitle", "Confirmation needed")}>
      <AgentReplyBlock text={permissionText || pq.text || wbT("workbenchChat.questionFallback", "Agent needs your confirmation to continue.")} />
      {isPermission ? (
        // Authorization: a simple binary. Buttons read 确认/拒绝 but send the
        // backend-recognized option text (options[0] = allow, last = deny).
        <WbActions>
          <WbBtn kind="primary" disabled={controller.busy} onClick={function () { controller.answer(pq.id, options[0] || wbT("workbenchChat.approve", "Confirm")); }}>{wbT("workbenchChat.approve", "Confirm")}</WbBtn>
          <WbBtn kind="ghost" disabled={controller.busy} onClick={function () { controller.answer(pq.id, options.length ? options[options.length - 1] : wbT("workbenchChat.reject", "Reject")); }}>{wbT("workbenchChat.reject", "Reject")}</WbBtn>
        </WbActions>
      ) : (
        <React.Fragment>
          {options.length > 0 && (
            <WbActions>
              {options.map(function (opt, i) {
                return <WbBtn key={i} kind={i === 0 ? "primary" : "ghost"} disabled={controller.busy} onClick={function () { controller.answer(pq.id, opt); }}>{opt}</WbBtn>;
              })}
            </WbActions>
          )}
          {pq.allowCustom && (
            <div className="wb-q-custom">
              <input type="text" className="wb-q-input" value={customText} placeholder={wbT("workbenchChat.customAnswer", "Or enter a custom reply...")} disabled={controller.busy}
                onChange={function (e) { setCustomText(e.target.value); }}
                onKeyDown={function (e) { if (e.key === "Enter") { e.preventDefault(); submitCustom(); } }} />
              <WbBtn kind="ghost" disabled={controller.busy || !String(customText).trim()} onClick={submitCustom}>{wbT("workbenchChat.send", "Send")}</WbBtn>
            </div>
          )}
        </React.Fragment>
      )}
    </WbCard>
  );
}

// waiting_for_approval — the 需要你确认 card before a sensitive run.
function ConfirmCard({ session, controller, onRightTab }) {
  var summary = workbenchServices.model().confirmSummary(session);
  var riskTone = summary.riskLevel === "high" ? "red" : summary.riskLevel === "medium" ? "amber" : "green";
  return (
    <WbCard tone="confirm" icon={ICONS.shield} title={wbT("workbenchChat.questionTitle", "Confirmation needed")}
      badge={<span className={"wb-risk " + riskTone}>{wbT("task.risk", "Risk {risk}", { risk: summary.risk })}</span>}>
      <p className="wb-card-hint">{wbT("task.confirm.actionsIntro", "The agent plans to perform these actions:")}</p>
      <ol className="wb-ordered">{summary.actions.map(function (a, i) { return <li key={i}>{a}</li>; })}</ol>
      <div className="wb-brief-row"><label>{wbT("task.confirm.scope", "Scope")}</label>
        <ul className="wb-bullet">{summary.scope.map(function (s, i) { return <li key={i}>{s}</li>; })}</ul>
      </div>
    </WbCard>
  );
}

// running / busy — Agent 正在处理. For a running plan step the detailed call
// trace is shown in the expanded subtask below (执行计划), so this top card omits
// the inline feed to avoid duplication and instead shows the progress bar + mini
// step list. Non-step background ops (规划 / 反思 / 验收) have no subtask row, so
// they keep streaming the session-level live feed here instead of a silent spinner.
function AgentActivityCard({ session, controller, onRightTab }) {
  var plan = Array.isArray(session.plan) ? session.plan : [];
  var done = plan.filter(function (s) { return s.status === "completed" || s.status === "done"; }).length;
  var runningStep = plan.filter(function (s) { return s.status === "running"; })[0] || null;
  var busyOp = session.agentBusy || null;
  var pct = plan.length ? Math.round((done / plan.length) * 100) : 0;
  var lines = wbLiveActivityLines(session, runningStep, busyOp);
  var stage = runningStep ? runningStep.title : ((busyOp && busyOp.label) || wbT("status.running", "Running"));
  var goalLoop = session.goalLoop && typeof session.goalLoop === "object" ? session.goalLoop : null;
  var phaseLabels = {
    executing: wbT("goalLoop.phase.executing", "执行"),
    reflecting: wbT("goalLoop.phase.reflecting", "深度思考"),
    verifying: wbT("goalLoop.phase.verifying", "独立验收"),
    repairing: wbT("goalLoop.phase.repairing", "返工"),
    recovering: wbT("goalLoop.phase.recovering", "恢复"),
  };
  var feedRef = useWorkbenchRef(null);
  // Keep the newest activity line in view as it streams in.
  useWorkbenchEffect(function () {
    var el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);
  return (
    <WbCard tone="running" icon={<span className="wb-spinner" />} title={wbT("task.card.agentWorking", "Agent is working")}
      badge={runningStep
        ? <span className="wb-progress-badge">{done} / {plan.length}</span>
        : <span className="wb-progress-badge live">{wbT("task.processing", "Processing")}</span>}>
      <p className="wb-running-stage">{wbT("task.currentStage", "Current stage: {stage}", { stage: stage })}</p>
      {goalLoop && (
        <div className="wb-goal-loop-live">
          <span><small>{wbT("goalLoop.live.phase", "阶段")}</small><b>{phaseLabels[goalLoop.phase] || goalLoop.phase}</b></span>
          <span><small>{wbT("goalLoop.live.runtime", "运行时间")}</small><b>{formatDurationSec(goalLoop.activeSeconds || 0)} / {formatDurationSec(goalLoop.maxActiveSeconds || 0)}</b></span>
          <span><small>{wbT("goalLoop.live.repairs", "返工")}</small><b>{goalLoop.repairRound || 0} / {goalLoop.maxRepairRounds || 0}</b></span>
          <span><small>{wbT("goalLoop.live.permission", "权限")}</small><b>{goalLoop.permissionMode === "full_access" ? wbT("goalLoop.permission.full", "完全访问") : wbT("goalLoop.permission.autoShort", "自动")}</b></span>
        </div>
      )}
      {/* A running plan step shows its call details in the expanded subtask below,
          so we omit the inline feed here. Non-step ops (no runningStep) keep it. */}
      {!runningStep && (
        lines.length > 0 ? (
          <ul className="wb-live-feed" ref={feedRef}>
            {lines.map(function (ln, i) {
              var last = i === lines.length - 1;
              return (
                <li key={ln.id || i} className={"wb-live-line" + (last ? " latest" : "")}>
                  <span className="wb-live-dot" />
                  <span className="wb-live-body">{ln.body}</span>
                </li>
              );
            })}
          </ul>
        ) : (
          <AgentReplyBlock text={session.agentReply || wbT("task.workingFallback", "Processing the current task. Please wait...")} />
        )
      )}
      {runningStep && plan.length > 0 && (
        <div className="wb-progress"><span style={{ width: pct + "%" }} /></div>
      )}
      {runningStep && (
        <ul className="wb-step-mini">
          {plan.map(function (s, i) {
            var st = (s.status === "completed" || s.status === "done") ? "done" : s.status === "running" ? "active" : "todo";
            return <li key={s.id} className={st}>{i + 1}. {s.title}</li>;
          })}
        </ul>
      )}
    </WbCard>
  );
}

// paused.
function PausedCard({ session, controller }) {
  var plan = Array.isArray(session.plan) ? session.plan : [];
  var done = plan.filter(function (s) { return s.status === "completed" || s.status === "done"; }).length;
  var current = WorkbenchModel.findNextRunnableStep(plan)
    || plan.find(function (step) { return !isResolvedStepStatus(step && step.status); })
    || plan[plan.length - 1]
    || null;
  return (
    <WbCard tone="paused" icon={ICONS.pause} title={wbT("task.card.paused", "Task paused")}>
      {session.goalLoop && <AgentReplyBlock text={session.agentReply || wbT("goalLoop.paused", "持续执行已暂停，当前进度已保留。")} />}
      <p className="wb-card-hint">
        {plan.length > 0
          ? wbT("task.pausedAt", "Paused at step {n}{title}.", { n: Math.min(done + 1, plan.length), title: current ? ": " + current.title : "" })
          : wbT("task.pausedNoSteps", "This task has not started and has no execution steps yet.")}
      </p>
    </WbCard>
  );
}

function BlockedCard({ session }) {
  var plan = Array.isArray(session.plan) ? session.plan : [];
  var blocked = plan.filter(function (step) {
    return step && !isResolvedStepStatus(step.status) && WorkbenchModel.unmetDependencyIds(plan, step).length > 0;
  });
  return (
    <WbCard tone="confirm" icon={ICONS.alert} title={wbT("task.card.blocked", "Task blocked")}>
      <AgentReplyBlock text={session.agentReply || wbT("task.plan.blockedHint", "Complete or rerun the prerequisite steps before continuing.")} />
      {blocked.length > 0 && (
        <ul className="wb-bullet">
          {blocked.slice(0, 5).map(function (step) { return <li key={step.id}>{step.title}</li>; })}
        </ul>
      )}
    </WbCard>
  );
}

// failed.
function FailedCard({ session, controller }) {
  var plan = Array.isArray(session.plan) ? session.plan : [];
  var failedIdx = plan.findIndex(function (s) { return s.status === "failed"; });
  return (
    <WbCard tone="failed" icon={ICONS.alert} title={wbT("task.card.failed", "Task failed")}>
      <AgentReplyBlock text={session.agentReply || wbT("task.failedFallback", "An error occurred during execution.")} />
      {failedIdx >= 0 && <p className="wb-card-hint">{wbT("task.failedAt", "Failed at step {n}: {title}", { n: failedIdx + 1, title: plan[failedIdx].title })}</p>}
      {session.recommendReflection && (
        <p className="wb-card-hint">{wbT("task.failedReflectionHint", "Suggested review: deep reflect first, then create a new task to try a different approach, or continue in this task.")}</p>
      )}
    </WbCard>
  );
}

// review (awaiting confirm) / completed (confirmed) — 任务完成. The agent's reply
// carries the textual deliverable; downloadable file deliverables are listed
// inline below it so the user can review/grab them without leaving the card.
function CompletionCard({ session, controller, onRightTab, onSelectSession, project, confirmed }) {
  var accept = Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
  var passed = accept.filter(function (a) { return a.status === "passed" || a.status === "done"; }).length;
  var artifacts = Array.isArray(session.artifacts) ? session.artifacts : [];
  return (
    <WbCard tone="done" icon={ICONS.check} title={confirmed ? wbT("task.card.completed", "Task completed") : wbT("task.card.awaitingConfirmation", "Agent finished; awaiting your confirmation")}>
      <AgentReplyBlock text={session.agentReply || wbT("task.completedFallback", "The current task is complete.")} />
      {artifacts.length > 0 && (
        <div className="wb-deliverables">
          <div className="wb-deliverables-label">{wbT("task.deliverables", "Deliverables")}</div>
          {artifacts.map(function (artifact, i) {
            var downloadUrl = "/api/task-sessions/" + encodeURIComponent(session.id) + "/artifacts/" + encodeURIComponent(artifact.id) + "/download";
            var artifactPath = String(artifact.path || "").trim();
            return (
              <a className="workbench-artifact-row wb-artifact-download" href={downloadUrl} download={artifact.name || true}
                title={wbT("task.artifact.download", "Download {name}", { name: artifact.name || "" })} key={artifact.id || i}>
                <span className="wb-artifact-file-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path d="M7 3.75h6.4L18 8.35v11.9H7z"></path>
                    <path d="M13.25 3.9v4.7h4.7"></path>
                  </svg>
                </span>
                <span className="wb-artifact-file-copy">
                  <b>{artifact.name}</b>
                  {artifactPath && artifactPath !== artifact.name ? <small>{artifactPath}</small> : null}
                </span>
                <span className="wb-artifact-download-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path d="M12 4v11"></path>
                    <path d="m8 11 4 4 4-4"></path>
                    <path d="M5 19h14"></path>
                  </svg>
                </span>
              </a>
            );
          })}
        </div>
      )}
      <div className="wb-done-grid">
        <button type="button" className="wb-done-stat" onClick={function () { onRightTab && onRightTab("acceptance"); }}>
          <b>{passed} / {accept.length || 0}</b><small>{wbT("task.stat.acceptancePassed", "Acceptance passed")}</small>
        </button>
        <button type="button" className="wb-done-stat" onClick={function () { onRightTab && onRightTab("artifacts"); }}>
          <b>{artifacts.length}</b><small>{wbT("workbenchChat.artifacts", "Artifacts")}</small>
        </button>
      </div>
    </WbCard>
  );
}

// cancelled.
function CancelledCard({ session, controller }) {
  return (
    <WbCard tone="cancelled" icon={ICONS.x} title={wbT("task.card.cancelled", "Task cancelled")}>
      <p className="wb-card-hint">{wbT("task.cancelledHint", "This task was cancelled. Current progress is kept, and you can reopen it to continue.")}</p>
    </WbCard>
  );
}

var ICON_CLOCK = (
  <svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ flexShrink: 0 }}>
    <circle cx="8" cy="8" r="6.5" /><path d="M8 5v3.2l1.8 1.8" />
  </svg>
);

var ICON_CHEVRON = (
  <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M5 7l3 3 3-3" />
  </svg>
);

// Pre-run editor shown in an expanded step BEFORE it executes: an editable
// command (the exact prompt handed to the subagent) + a context-file list the
// user can grow by referencing workspace paths or uploading files. Both persist
// onto the step (promptOverride / contextFiles) via controller.patchStep.
// Compact read-only summary for a not-yet-run step expanded in view mode —
// description, prerequisites, command and context files, no editing affordances.
function StepSummary({ session, step, steps }) {
  var prereqTitles = (Array.isArray(step.dependsOn) ? step.dependsOn : []).map(function (id) {
    var dep = steps.find(function (candidate) { return candidate.id === id; });
    return dep ? dep.title : id;
  });
  var ctxFiles = Array.isArray(step.contextFiles) ? step.contextFiles : [];
  var command = (typeof step.promptOverride === "string" && step.promptOverride.length > 0)
    ? step.promptOverride
    : stepExecutionPrompt(session, step);
  return (
    <div className="wbp-summary">
      {step.description ? (
        <div className="wbp-summary-row">
          <span className="wbp-summary-k">{wbT("workbench.step.description", "Description")}</span>
          <span className="wbp-summary-v">{step.description}</span>
        </div>
      ) : null}
      <div className="wbp-summary-row">
        <span className="wbp-summary-k">{wbT("workbench.step.prerequisites", "Prerequisites")}</span>
        <span className="wbp-summary-v">
          {prereqTitles.length ? (
            <span className="wbp-summary-chips">
              {prereqTitles.map(function (title, i) { return <span key={i}>{title}</span>; })}
            </span>
          ) : <em className="wbp-summary-none">{wbT("common.none", "None")}</em>}
        </span>
      </div>
      <div className="wbp-summary-row">
        <span className="wbp-summary-k">{wbT("workbench.step.command", "Command")}</span>
        <span className="wbp-summary-v wbp-summary-cmd">{command || "—"}</span>
      </div>
      {ctxFiles.length > 0 ? (
        <div className="wbp-summary-row">
          <span className="wbp-summary-k">{wbT("workbench.step.files", "Files")}</span>
          <span className="wbp-summary-v">
            <span className="wbp-summary-chips">
              {ctxFiles.map(function (f, i) {
                var isUpload = f && f.source === "upload";
                var label = isUpload ? (f.name || "file") : String((f && (f.path || f.name)) || "").split("/").pop();
                return <span key={i} className="wbp-summary-file">{label}</span>;
              })}
            </span>
          </span>
        </div>
      ) : null}
    </div>
  );
}

// Unified compact editor for a not-yet-run step (edit mode). Mirrors the
// read-only StepSummary's label/value layout so view and edit modes look
// consistent. Plan fields (title/description/prerequisites) save together via
// the Save button; the command persists on blur and context files on change.
function StepEditor({ session, step, steps, controller }) {
  var model = workbenchServices.model();
  var defaultPrompt = stepExecutionPrompt(session, step);
  function overrideOf(s) { return (s && typeof s.promptOverride === "string" && s.promptOverride.length > 0) ? s.promptOverride : ""; }
  var [title, setTitle] = useWorkbenchState(step.title || "");
  var [description, setDescription] = useWorkbenchState(step.description || "");
  var [dependsOn, setDependsOn] = useWorkbenchState(Array.isArray(step.dependsOn) ? step.dependsOn : []);
  var [saving, setSaving] = useWorkbenchState(false);
  var [draft, setDraft] = useWorkbenchState(overrideOf(step) || defaultPrompt);
  var [pathInput, setPathInput] = useWorkbenchState("");
  var [adding, setAdding] = useWorkbenchState(false);
  var [uploading, setUploading] = useWorkbenchState(false);
  var [hint, setHint] = useWorkbenchState("");
  var fileRef = useWorkbenchRef(null);

  var stepIndex = steps.findIndex(function (item) { return item && item.id === step.id; });
  var dependencyOptions = steps.slice(0, Math.max(0, stepIndex));
  var contextFiles = Array.isArray(step.contextFiles) ? step.contextFiles : [];
  var hasOverride = overrideOf(step).length > 0;

  useWorkbenchEffect(function () {
    setTitle(step.title || "");
    setDescription(step.description || "");
    setDependsOn(Array.isArray(step.dependsOn) ? step.dependsOn : []);
  }, [step.id, step.title, step.description, JSON.stringify(step.dependsOn || [])]);

  // Re-sync the command textarea when the expanded step changes (the editor
  // instance is reused across steps — the key is stable at .wbp-detail).
  useWorkbenchEffect(function () {
    setDraft(overrideOf(step) || stepExecutionPrompt(session, step));
    setPathInput("");
    setHint("");
  }, [step.id]);

  function toggleDependency(stepId) {
    setDependsOn(function (current) {
      return current.indexOf(stepId) >= 0
        ? current.filter(function (id) { return id !== stepId; })
        : current.concat([stepId]);
    });
  }
  function save() {
    var nextTitle = String(title || "").trim();
    if (!nextTitle || saving) return;
    setSaving(true);
    controller.patchStep(step.id, {
      title: nextTitle,
      description: String(description || "").trim(),
      dependsOn: dependsOn,
    }).finally(function () { setSaving(false); });
  }
  function remove() {
    workbenchServices.feedback().confirmModal({
      body: wbT("task.plan.confirmDeleteStep", "Delete step \"{name}\"?", { name: step.title }),
      confirmLabel: wbT("common.delete", "Delete"),
      danger: true,
    }).then(function (ok) {
      if (ok) controller.deleteStep(step.id);
    });
  }
  function persistPrompt() {
    // Store an override only when it diverges from the default, so a step still
    // tracks a regenerated default prompt until the user actually edits it.
    var trimmed = draft.trim();
    var nextOverride = (trimmed && trimmed !== defaultPrompt.trim()) ? draft : "";
    if ((step.promptOverride || "") === nextOverride) return;
    controller.patchStep(step.id, { promptOverride: nextOverride });
  }
  function resetPrompt() {
    setDraft(defaultPrompt);
    if (step.promptOverride) controller.patchStep(step.id, { promptOverride: "" });
  }
  function addWorkspaceFile() {
    var p = pathInput.trim();
    if (!p || adding) return;
    setAdding(true);
    setHint("");
    model.checkWorkspacePath(session.id, p)
      .then(function (res) {
        if (!res || !res.exists) {
          setHint((res && res.error) ? res.error : wbT("task.context.fileNotFound", "The file was not found in the workspace."));
          return;
        }
        var rel = res.path || p;
        var dup = contextFiles.some(function (f) { return f && f.source !== "upload" && f.path === rel; });
        if (dup) { setHint(wbT("task.context.fileAlreadyAdded", "This file has already been added.")); return; }
        controller.patchStep(step.id, { contextFiles: contextFiles.concat([{ source: "workspace", path: rel, name: rel.split("/").pop() }]) });
        setPathInput("");
      })
      .finally(function () { setAdding(false); });
  }
  function pickUpload() { if (fileRef.current) fileRef.current.click(); }
  function onUploadPick(e) {
    var files = e.target.files;
    if (!files || !files.length) return;
    setUploading(true);
    setHint("");
    model.uploadAttachments(files)
      .then(function (uploaded) {
        var tagged = (uploaded || []).map(function (u) { return Object.assign({}, u, { source: "upload" }); });
        controller.patchStep(step.id, { contextFiles: contextFiles.concat(tagged) });
      })
      .catch(function (err) { setHint(wbT("task.context.uploadFailed", "Upload failed: {error}", { error: (err && err.message) || String(err) })); })
      .finally(function () { setUploading(false); if (fileRef.current) fileRef.current.value = ""; });
  }
  function removeFile(target) {
    controller.patchStep(step.id, { contextFiles: contextFiles.filter(function (f) { return f !== target; }) });
  }

  return (
    <div className="wbp-summary wbp-summary-edit">
      <label className="wbp-summary-row">
        <span className="wbp-summary-k">{wbT("workbench.step.title", "Title")}</span>
        <input className="wbp-edit-input" value={title} disabled={saving} placeholder={wbT("workbench.step.titlePlaceholder", "Step title")} onChange={function (e) { setTitle(e.target.value); }} />
      </label>
      <label className="wbp-summary-row">
        <span className="wbp-summary-k">{wbT("workbench.step.description", "Description")}</span>
        <textarea className="wbp-edit-input" rows={2} value={description} disabled={saving} placeholder={wbT("workbench.step.descriptionPlaceholder", "Describe what this step should accomplish")} onChange={function (e) { setDescription(e.target.value); }} />
      </label>
      <div className="wbp-summary-row">
        <span className="wbp-summary-k">{wbT("workbench.step.prerequisites", "Prerequisites")}</span>
        <div className="wbp-summary-v">
          {dependencyOptions.length ? (
            <div className="wbp-dependency-options">
              {dependencyOptions.map(function (candidate) {
                var checked = dependsOn.indexOf(candidate.id) >= 0;
                return (
                  <label key={candidate.id} className={"wbp-dependency-option" + (checked ? " selected" : "")}>
                    <input type="checkbox" checked={checked} disabled={saving} onChange={function () { toggleDependency(candidate.id); }} />
                    <span>{candidate.title}</span>
                  </label>
                );
              })}
            </div>
          ) : (
            <em className="wbp-summary-none">{wbT("task.plan.noEarlierSteps", "No earlier steps are available.")}</em>
          )}
        </div>
      </div>
      <div className="wbp-summary-row">
        <span className="wbp-summary-k">{wbT("workbench.step.command", "Command")}</span>
        <div className="wbp-summary-v">
          <textarea
            className="wbp-edit-input wbp-edit-cmd"
            value={draft}
            rows={5}
            spellCheck={false}
            placeholder={wbT("workbench.step.commandPlaceholder", "Describe the instruction for the subagent…")}
            onChange={function (e) { setDraft(e.target.value); }}
            onBlur={persistPrompt}
          />
          {hasOverride && (
            <div className="wbp-edit-cmd-actions">
              <button type="button" className="wbp-tiny-btn" onClick={resetPrompt}>{wbT("workbench.step.restoreDefault", "Restore default")}</button>
            </div>
          )}
        </div>
      </div>
      <div className="wbp-summary-row">
        <span className="wbp-summary-k">{wbT("workbench.step.files", "Files")}</span>
        <div className="wbp-summary-v">
          {contextFiles.length > 0 && (
            <div className="wbp-ctx-list">
              {contextFiles.map(function (f, i) {
                var isUpload = f && f.source === "upload";
                var label = isUpload ? (f.name || "file") : String((f && (f.path || f.name)) || "").split("/").pop();
                return (
                  <span key={(f && (f.path || f.id || f.name) || "") + "_" + i} className={"wbp-ctx-chip" + (isUpload ? " upload" : "")} title={(f && (f.path || f.name)) || ""}>
                    <span className="wbp-ctx-tag">{isUpload ? wbT("task.context.source.upload", "Upload") : wbT("task.context.source.workspace", "Workspace")}</span>
                    <span className="wbp-ctx-name">{label}</span>
                    <button type="button" className="wbp-ctx-x" onClick={function () { removeFile(f); }} aria-label={wbT("task.context.removeFile", "Remove file")}>{ICONS.x}</button>
                  </span>
                );
              })}
            </div>
          )}
          <div className="wbp-ctx-add">
            <div className="wbp-ctx-add-row">
              <input
                type="text"
                className="wbp-ctx-input"
                value={pathInput}
                placeholder={wbT("task.context.pathPlaceholder", "Workspace-relative path, such as src/app.py")}
                onChange={function (e) { setPathInput(e.target.value); }}
                onKeyDown={function (e) { if (e.key === "Enter") { e.preventDefault(); addWorkspaceFile(); } }}
              />
              <button type="button" className="wbp-tiny-btn" disabled={adding || !pathInput.trim()} onClick={addWorkspaceFile}>{adding ? wbT("task.context.validating", "Validating…") : wbT("common.add", "Add")}</button>
            </div>
            <button type="button" className="wbp-tiny-btn wbp-ctx-upload" disabled={uploading} onClick={pickUpload}>
              <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M8 10.5V3.5" /><path d="M5 6l3-3 3 3" /><path d="M3 11v1.5A1.5 1.5 0 0 0 4.5 14h7a1.5 1.5 0 0 0 1.5-1.5V11" /></svg>
              {uploading ? wbT("task.context.uploading", "Uploading…") : wbT("task.context.uploadFile", "Upload file")}
            </button>
            <input ref={fileRef} type="file" multiple style={{ display: "none" }} onChange={onUploadPick} />
          </div>
          {hint && <p className="wbp-ctx-hint">{hint}</p>}
        </div>
      </div>
      <div className="wbp-summary-actions">
        <button type="button" className="wbp-tiny-btn danger" onClick={remove}>{wbT("common.delete", "Delete")}</button>
        <button type="button" className="wb-btn primary compact" disabled={saving || !String(title || "").trim()} onClick={save}>
          {saving ? wbT("common.saving", "Saving...") : wbT("common.save", "Save")}
        </button>
      </div>
    </div>
  );
}

// The 执行计划 list — editable, dependency-aware and sortable before execution.
function TaskPlanList({ session, expandedStepId, onToggleStep, onRightTab, controller }) {
  var steps = Array.isArray(session.plan) ? session.plan : [];
  var [dragStepId, setDragStepId] = useWorkbenchState("");
  var [dragOverId, setDragOverId] = useWorkbenchState("");
  var [adding, setAdding] = useWorkbenchState(false);
  var [newTitle, setNewTitle] = useWorkbenchState("");
  var [newDescription, setNewDescription] = useWorkbenchState("");
  var [savingNew, setSavingNew] = useWorkbenchState(false);
  var [planEditing, setPlanEditing] = useWorkbenchState(false);
  var planStarted = steps.some(function (step) {
    return step && (
      String(step.status || "pending") !== "pending"
      || step.startedAt
      || step.completedAt
      || (Array.isArray(step.progressEvents) && step.progressEvents.length)
      || (Array.isArray(step.toolCalls) && step.toolCalls.length)
    );
  });
  var canEditStructure = !controller.busy
    && ["running", "waiting_for_user"].indexOf(String(session.status || "")) < 0;
  // Add/reorder are blocked by the backend once any step starts executing.
  var canAddReorder = canEditStructure && !planStarted;
  // Step-level editing (delete, update command/contextFiles) stays available
  // as long as the specific step is still pending.
  var editing = canEditStructure && planEditing;

  // Drop out of edit mode the moment the structure locks (execution begins).
  useWorkbenchEffect(function () {
    if (!canEditStructure && (planEditing || adding)) { setPlanEditing(false); setAdding(false); }
  }, [canEditStructure]);

  function exitEditMode() { setPlanEditing(false); setAdding(false); }

  function persistOrder(nextSteps) {
    var validation = WorkbenchModel.validatePlanGraph(nextSteps);
    if (!validation.valid) {
      workbenchServices.feedback().showToast(wbT("task.plan.invalidOrder", "This move would place a step before one of its prerequisites."), "warning");
      return;
    }
    controller.reorderSteps(nextSteps.map(function (step) { return step.id; }));
  }

  function moveStep(sourceId, targetId, placeAfter) {
    if (!canAddReorder || !sourceId || !targetId || sourceId === targetId) return;
    var next = steps.slice();
    var sourceIndex = next.findIndex(function (step) { return step.id === sourceId; });
    if (sourceIndex < 0) return;
    var moved = next.splice(sourceIndex, 1)[0];
    var targetIndex = next.findIndex(function (step) { return step.id === targetId; });
    if (targetIndex < 0) return;
    if (placeAfter) targetIndex += 1;
    next.splice(targetIndex, 0, moved);
    persistOrder(next);
  }

  function moveBy(stepId, delta) {
    var index = steps.findIndex(function (step) { return step.id === stepId; });
    var target = steps[index + delta];
    if (index < 0 || !target) return;
    var next = steps.slice();
    var moved = next.splice(index, 1)[0];
    next.splice(index + delta, 0, moved);
    persistOrder(next);
  }

  function addStep() {
    var title = String(newTitle || "").trim();
    if (!title || savingNew) return;
    setSavingNew(true);
    controller.addStep({ title: title, description: String(newDescription || "").trim(), dependsOn: [] })
      .then(function (store) {
        if (!store) return;
        setNewTitle("");
        setNewDescription("");
        setAdding(false);
      })
      .finally(function () { setSavingNew(false); });
  }

  return (
    <section className="workbench-flow wbp">
      <div className="wbp-head">
        <div>
          <b>{wbT("task.plan.title", "Execution plan")}</b>
          <span>{steps.length}</span>
        </div>
        {canEditStructure && (
          <div className="wbp-head-actions">
            {planEditing ? (
              <>
                {canAddReorder && (
                  <button type="button" className="wb-btn ghost compact" onClick={function () { setAdding(!adding); }}>
                    {adding ? wbT("common.cancel", "Cancel") : wbT("task.plan.addStep", "Add step")}
                  </button>
                )}
                <button type="button" className="wb-btn ghost compact" onClick={exitEditMode}>
                  {wbT("common.done", "Done")}
                </button>
              </>
            ) : (
              <button type="button" className="wb-btn ghost compact wbp-edit-toggle" onClick={function () { setPlanEditing(true); }}>
                {ICONS.edit}<span>{wbT("common.edit", "Edit")}</span>
              </button>
            )}
          </div>
        )}
      </div>
      {adding && (
        <div className="wbp-add-step">
          <input
            autoFocus
            value={newTitle}
            placeholder={wbT("task.plan.newStepTitle", "New step title")}
            onChange={function (e) { setNewTitle(e.target.value); }}
            onKeyDown={function (e) { if (e.key === "Enter") { e.preventDefault(); addStep(); } }}
          />
          <textarea
            rows={2}
            value={newDescription}
            placeholder={wbT("task.plan.newStepDescription", "What should this step accomplish?")}
            onChange={function (e) { setNewDescription(e.target.value); }}
          />
          <div>
            <button type="button" className="wb-btn primary" disabled={savingNew || !String(newTitle || "").trim()} onClick={addStep}>
              {savingNew ? wbT("common.saving", "Saving...") : wbT("task.plan.addStep", "Add step")}
            </button>
          </div>
        </div>
      )}
      <div className="wbp-list">
        {steps.map(function (step, index) {
          var expanded = expandedStepId === step.id;
          var doneStep = isDoneStepStatus(step.status);
          var runningStep = isRunningStepStatus(step.status);
          var failedStep = step.status === "failed";
          var skippedStep = step.status === "skipped";
          var unmetDependencyIds = WorkbenchModel.unmetDependencyIds(steps, step);
          var blockedStep = !doneStep && !runningStep && !failedStep && !skippedStep && unmetDependencyIds.length > 0;
          var state = doneStep ? "done" : runningStep ? "current" : failedStep ? "failed" : skippedStep ? "skipped" : blockedStep ? "blocked" : "idle";
          var statusLabel = doneStep ? wbT("status.done", "Done")
            : runningStep ? wbT("status.running", "Running")
            : failedStep ? wbT("status.failed", "Failed")
            : skippedStep ? wbT("status.skipped", "Skipped")
            : blockedStep ? wbT("task.plan.waitingPrerequisites", "Waiting for prerequisites")
            : wbT("status.pending", "Pending");
          var doneStamp = step.completedAt || step.updatedAt || "";
          var time = doneStep && doneStamp ? WorkbenchModel.formatTime(doneStamp) : "";
          var duration = doneStep ? stepDurationText(step) : "";
          var estimate = runningStep && step.estimate ? String(step.estimate) : "";
          var hasFiles = Array.isArray(step.relatedFiles) && step.relatedFiles.length > 0;
          var progressText = step.currentAction || step.description || "";
          var beforeRun = !step.status || step.status === "pending";
          var isLast = index === steps.length - 1;
          return (
            <div
              key={step.id}
              className={"wbp-step " + state + (expanded ? " expanded" : "") + (dragStepId === step.id ? " dragging" : "") + (dragOverId === step.id ? " drag-over" : "")}
              onDragOver={function (e) { if (canAddReorder && dragStepId) { e.preventDefault(); setDragOverId(step.id); } }}
              onDragLeave={function () { if (dragOverId === step.id) setDragOverId(""); }}
              onDrop={function (e) {
                e.preventDefault();
                var sourceId = dragStepId || e.dataTransfer.getData("text/plain");
                var dropLine = e.currentTarget.querySelector(".wbp-line-main");
                var bounds = dropLine ? dropLine.getBoundingClientRect() : e.currentTarget.getBoundingClientRect();
                var placeAfter = e.clientY > bounds.top + bounds.height / 2;
                setDragStepId("");
                setDragOverId("");
                moveStep(sourceId, step.id, placeAfter);
              }}
            >
              <div className="wbp-rail">
                <button type="button" className={"wbp-node " + state} onClick={function () { onToggleStep(step.id); }} aria-label={expanded ? wbT("task.step.collapse", "Collapse step") : wbT("task.step.expand", "Expand step")}>
                  {doneStep ? ICONS.checkSmall : null}
                </button>
                {!isLast && <span className={"wbp-line" + (doneStep ? " done" : "")} />}
              </div>
              <div className="wbp-row" onClick={function () { onToggleStep(step.id); }} role="button" tabIndex={0}
                onKeyDown={function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggleStep(step.id); } }}>
                <div className="wbp-line-main">
                  <div className="wbp-copy">
                    {canAddReorder && (
                      <button
                        type="button"
                        draggable
                        className="wbp-drag-handle"
                        title={wbT("task.plan.dragToReorder", "Drag to reorder")}
                        aria-label={wbT("task.plan.dragToReorder", "Drag to reorder")}
                        onClick={function (e) { e.stopPropagation(); }}
                        onKeyDown={function (e) {
                          e.stopPropagation();
                          if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); moveBy(step.id, -1); }
                          if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); moveBy(step.id, 1); }
                        }}
                        onDragStart={function (e) {
                          e.stopPropagation();
                          setDragStepId(step.id);
                          e.dataTransfer.effectAllowed = "move";
                          e.dataTransfer.setData("text/plain", step.id);
                        }}
                        onDragEnd={function () { setDragStepId(""); setDragOverId(""); }}
                      >
                        {ICONS.dots}
                      </button>
                    )}
                    <span className="wbp-idx">{index + 1}.</span>
                    <span className="wbp-title">{step.title}</span>
                  </div>
                  <span className={"wbp-status " + state}>{statusLabel}</span>
                  <time className="wbp-time">{time}</time>
                  <span className="wbp-dur">{duration ? <>{ICON_CLOCK}<span>{duration}</span></> : estimate ? <span className="wbp-estimate">{wbT("workbench.step.estimated", "Estimated {duration}", { duration: estimate })}</span> : null}</span>
                  <span className={"wbp-caret" + (expanded ? " open" : "")}>{ICON_CHEVRON}</span>
                </div>
                {expanded && (
                  <div className="wbp-detail" onClick={function (e) { e.stopPropagation(); }}>
                    {beforeRun ? (
                      editing ? (
                        <StepEditor session={session} step={step} steps={steps} controller={controller} />
                      ) : (
                        <StepSummary session={session} step={step} steps={steps} />
                      )
                    ) : (
                      <div className="wbp-summary">
                        <div className="wbp-summary-row">
                          <span className="wbp-summary-k">{wbT("workbench.step.progress", "Progress")}</span>
                          <span className="wbp-summary-v">
                            {progressText || wbT("workbench.step.waitingProgress", "Waiting for the Agent to update this step.")}
                            {Array.isArray(step.progressEvents) && step.progressEvents.length > 0 && (
                              <ul className="wbp-events">
                                {step.progressEvents.slice(-3).map(function (ev, i) {
                                  return <li key={i}>{ev.body || ev.text || ev.message || String(ev)}</li>;
                                })}
                              </ul>
                            )}
                          </span>
                        </div>
                        <div className="wbp-summary-row">
                          <span className="wbp-summary-k">{wbT("workbench.step.files", "Files")}</span>
                          <span className="wbp-summary-v">
                            {hasFiles ? (
                              <div className="wbp-file-chips">
                                {step.relatedFiles.map(function (file) {
                                  return <button key={file.path || file.name} type="button" className="wbp-file-chip" onClick={function () { onRightTab("files"); }}>{(file.path || file.name || "").split("/").pop()}</button>;
                                })}
                              </div>
                            ) : <em className="wbp-summary-none">{wbT("workbench.step.noFiles", "No related files")}</em>}
                          </span>
                        </div>
                      </div>
                    )}
                    {!doneStep && (
                      <div className="wbp-detail-actions">
                        {runningStep ? (
                          <button type="button" className="wb-btn danger" onClick={function () { controller.interrupt(); }}>{wbT("workbench.step.stop", "Stop")}</button>
                        ) : (
                          <button type="button" className="wb-btn primary" disabled={controller.busy || unmetDependencyIds.length > 0} onClick={function () { controller.runStep(step); }}>{wbT("workbench.step.run", "Run this step")}</button>
                        )}
                        <button type="button" className="wb-btn ghost" onClick={function () { onRightTab("logs"); }}>{wbT("workbench.step.viewLogs", "View logs")}</button>
                        {unmetDependencyIds.length > 0 && <span className="wbp-blocked-hint">{wbT("task.plan.completePrerequisitesFirst", "Complete prerequisite steps first.")}</span>}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function composerPlaceholder(status) {
  if (status === "idle" || status === "pending") return wbT("task.placeholder.idle", "Ask a question, give a direct instruction, or describe a task...");
  if (status === "answered" || status === "acted") return wbT("task.placeholder.reply", "Ask a follow-up, give the next instruction, or describe a fuller task...");
  if (status === "running") return wbT("task.placeholder.running", "The agent is running; input is temporarily disabled...");
  if (status === "planning") return wbT("task.placeholder.planning", "Add to or revise the execution plan...");
  if (status === "waiting_for_approval" || status === "waiting_for_user") return wbT("task.placeholder.waiting", "Revise requirements, or approve execution...");
  if (status === "failed") return wbT("task.placeholder.failed", "Explain how to fix it, or revise the request...");
  return wbT("task.placeholder.default", "Add requirements, request changes, or continue this task...");
}

// Quick-action chips below the composer; the set changes with status.
// `guard:false` chips stay enabled while the controller is busy (read-only).
function composerChips(status, controller, onRightTab, session) {
  if (status === "idle" || status === "pending") {
    return [];
  }
  if (status === "answered") {
    return [
      { label: wbT("task.action.promoteToTask", "Make it a task"), onClick: function () { controller.promoteToPlan(); } },
      { label: wbT("task.action.continueEditing", "Continue editing"), onClick: focusComposer },
    ];
  }
  if (status === "acted") {
    return [
      { label: wbT("task.action.viewChanges", "View changes"), guard: false, onClick: function () { onRightTab && onRightTab("files"); } },
      { label: wbT("task.action.promoteToTask", "Make it a task"), onClick: function () { controller.promoteToPlan(); } },
      { label: wbT("task.action.continueEditing", "Continue editing"), onClick: focusComposer },
    ];
  }
  if (status === "planning") {
    return [
      { label: wbT("task.action.approveExecution", "Start"), onClick: function () { controller.approvePlan(); } },
      { label: wbT("task.action.approveRunAll", "Run all"), onClick: function () { controller.approveAndRunAll(); } },
      { label: wbT("goalLoop.action.configure", "Run until pass"), className: "goal-loop", onClick: function () { controller.configureGoalLoop(); } },
      { label: wbT("task.action.editPlan", "Edit plan"), onClick: focusComposer },
      { label: wbT("task.action.regenerate", "Regenerate"), onClick: function () { controller.regeneratePlan(); } },
    ];
  }
  if (status === "waiting_for_approval" || status === "waiting_for_user" || status === "blocked") {
    if (session && session.goalLoop && status === "blocked") {
      return [
        { label: wbT("task.action.resumeTask", "Resume task"), onClick: function () { controller.resume(); } },
        { label: wbT("task.action.viewLogs", "View logs"), guard: false, onClick: function () { onRightTab && onRightTab("logs"); } },
        { label: wbT("task.action.cancelTask", "Cancel task"), onClick: function () { controller.cancel(); } },
      ];
    }
    return [
      { label: wbT("task.action.approveExecution", "Approve execution"), onClick: function () { controller.execute(); } },
      { label: wbT("task.action.reject", "Reject"), onClick: function () { controller.reject(); } },
    ];
  }
  if (status === "running") {
    return [
      { label: wbT("task.action.stopExecution", "Stop execution"), guard: false, onClick: function () { controller.interrupt(); } },
      { label: wbT("task.action.viewLogs", "View logs"), guard: false, onClick: function () { onRightTab && onRightTab("logs"); } },
      { label: wbT("task.action.viewChanges", "View changes"), guard: false, onClick: function () { onRightTab && onRightTab("files"); } },
    ];
  }
  if (status === "paused") {
    if (session && session.goalLoop) {
      // Budget-exhausted pauses can't be cleared by a plain resume (it would just
      // re-pause), so adjusting the limit is the only real "continue" path.
      var loopStop = session.goalLoop.stopReason || "";
      var budgetPaused = loopStop === "max_runtime" || loopStop === "max_repair_rounds";
      var pausedActions = [];
      if (!budgetPaused) pausedActions.push({ label: wbT("task.action.resumeTask", "Resume task"), onClick: function () { controller.resume(); } });
      pausedActions.push({ label: wbT("goalLoop.action.adjustLimits", "调整限制并继续"), onClick: function () { controller.adjustGoalLoopLimits(); } });
      pausedActions.push({ label: wbT("task.action.viewLogs", "View logs"), guard: false, onClick: function () { onRightTab && onRightTab("logs"); } });
      pausedActions.push({ label: wbT("task.action.viewChanges", "View changes"), guard: false, onClick: function () { onRightTab && onRightTab("files"); } });
      pausedActions.push({ label: wbT("task.action.cancelTask", "Cancel task"), onClick: function () { controller.cancel(); } });
      return pausedActions;
    }
    return [
      { label: wbT("task.action.resumeTask", "Resume task"), onClick: function () { controller.resume(); } },
      { label: wbT("task.action.runAll", "Run all"), onClick: function () { controller.executeAll(); } },
      { label: wbT("task.action.reflect", "深度反思"), onClick: function () { controller.reflect(); } },
      { label: wbT("task.action.reviseRequest", "Revise request"), onClick: focusComposer },
      { label: wbT("task.action.cancelTask", "Cancel task"), onClick: function () { controller.cancel(); } },
    ];
  }
  if (status === "failed") {
    if (hasAcceptanceFailure(session)) {
      return [
        { label: wbT("task.action.reflectFork", "深度反思+新建任务"), onClick: function () { controller.reflectAndFork(); } },
        { label: wbT("task.action.repairProblem", "修复问题"), onClick: function () { controller.repairProblem(); } },
        { label: wbT("task.action.continueModify", "继续修改"), onClick: function () { controller.continueModify(); } },
        { label: wbT("task.action.reviseRequest", "修改要求"), onClick: function () { openAcceptanceEditor(onRightTab); } },
      ];
    }
    return [
      { label: wbT("task.action.reflectFork", "深度反思+新建任务"), onClick: function () { controller.reflectAndFork(); } },
      { label: wbT("task.action.reflect", "深度反思"), onClick: function () { controller.reflect(); } },
      { label: wbT("task.action.retry", "Retry"), onClick: function () { controller.retry(); } },
      { label: wbT("task.action.reviseRequest", "Revise request"), onClick: focusComposer },
      { label: wbT("task.action.skipStep", "Skip this step"), onClick: function () { controller.skipStep(); } },
    ];
  }
  if (status === "review" || status === "done") {
    return [
      { label: wbT("task.action.markComplete", "Mark complete"), onClick: function () { controller.markComplete(); } },
      { label: wbT("task.action.verify", "验收"), onClick: function () { controller.verify(); } },
      { label: wbT("task.action.reflect", "深度反思"), onClick: function () { controller.reflect(); } },
      { label: wbT("task.action.continueEditing", "Continue editing"), onClick: focusComposer },
      { label: wbT("task.action.createFollowUp", "Create follow-up task"), onClick: function () { controller.createFollowUp(); } },
    ];
  }
  if (status === "completed") {
    return [
      { label: wbT("task.action.createFollowUp", "Create follow-up task"), onClick: function () { controller.createFollowUp(); } },
      { label: wbT("task.action.reopen", "Reopen"), onClick: function () { controller.reopen(); } },
    ];
  }
  if (status === "cancelled") {
    return [{ label: wbT("task.action.reopen", "Reopen"), onClick: function () { controller.reopen(); } }];
  }
  return [];
}

// Composer is always bound to the current task. Behaviour + quick-chips depend
// on the task status. Permission mode lives in the shared model menu, matching
// the conversation composer.
function TaskComposer({
  session,
  controller,
  onRightTab,
  attachments,
  onAttachmentsChange,
  mode,
  onModeChange,
  configuredModels,
  selectedModelId,
  onSelectedModelIdChange,
  reasoningEffort,
  onReasoningEffortChange,
}) {
  var model = workbenchServices.model();
  var [draft, setDraft] = useWorkbenchState("");
  var [scopePrompt, setScopePrompt] = useWorkbenchState(null);
  var [modelOpen, setModelOpen] = useWorkbenchState(false);
  var [modelPanel, setModelPanel] = useWorkbenchState("root");
  var [uploading, setUploading] = useWorkbenchState(false);
  var [voiceSnapshot, setVoiceSnapshot] = useWorkbenchState({ status: {}, activeKey: "" });
  var [voicePhase, setVoicePhase] = useWorkbenchState("");
  var taRef = useWorkbenchRef(null);
  var sendButtonRef = useWorkbenchRef(null);
  var draftRef = useWorkbenchRef(draft);
  var fileRef = useWorkbenchRef(null);
  var modelPickerRef = useWorkbenchRef(null);
  var uploadCountRef = useWorkbenchRef(0);
  var voiceRecorderRef = useWorkbenchRef(null);
  var voiceSessionIdRef = useWorkbenchRef(String(session.id || ""));
  var voiceFeedbackRef = useWorkbenchRef(null);
  var ComposerBrowserIcon = workbenchServices.browser().Icon;
  if (!voiceFeedbackRef.current) voiceFeedbackRef.current = wbcCreateComposerVoiceFeedback();
  var status = String(session.status || "idle");
  var running = status === "running";
  var awaitingAnswer = !!(session.pendingQuestion && session.pendingQuestion.id);
  var disabled = controller.busy || running || awaitingAnswer;
  // No plan yet → the composer is a free chat: every send goes through the
  // intent-aware dispatch so the agent itself decides whether to answer, act, or
  // draft a plan. Once a plan exists, the composer refines that plan instead.
  var hasPlan = Array.isArray(session.plan) && session.plan.length > 0;
  attachments = attachments || [];

  useWorkbenchEffect(function () { draftRef.current = draft; }, [draft]);

  useWorkbenchEffect(function () {
    return WbcVoice.subscribe(setVoiceSnapshot);
  }, []);

  useWorkbenchEffect(function () {
    voiceSessionIdRef.current = String(session.id || "");
    setVoicePhase("");
    return function () {
      var recorder = voiceRecorderRef.current;
      voiceRecorderRef.current = null;
      voiceFeedbackRef.current.dismiss();
      if (recorder && typeof recorder.stop === "function") recorder.stop().catch(function () {});
    };
  }, [session.id]);

  useWorkbenchEffect(function () {
    if (!awaitingAnswer) return;
    setModelOpen(false);
    setModelPanel("root");
    var recorder = voiceRecorderRef.current;
    voiceRecorderRef.current = null;
    setVoicePhase("");
    voiceFeedbackRef.current.dismiss();
    if (recorder && typeof recorder.stop === "function") recorder.stop().catch(function () {});
  }, [awaitingAnswer]);

  useWorkbenchEffect(function () {
    function onFocus() { if (taRef.current) taRef.current.focus(); }
    window.addEventListener("wb-focus-composer", onFocus);
    return function () { window.removeEventListener("wb-focus-composer", onFocus); };
  }, []);

  useWorkbenchEffect(function () {
    if (!modelOpen) return undefined;
    wbSetBrowserOverlayObscured(1);
    return function () { wbSetBrowserOverlayObscured(-1); };
  }, [modelOpen]);

  // Reset transient composer state when switching tasks.
  useWorkbenchEffect(function () {
    setScopePrompt(null);
    setModelOpen(false);
    setModelPanel("root");
  }, [session.id]);

  useWorkbenchEffect(function () {
    if (!modelOpen) return undefined;
    function closeModelPicker(event) {
      if (modelPickerRef.current && !modelPickerRef.current.contains(event.target)) {
        setModelOpen(false);
        setModelPanel("root");
      }
    }
    document.addEventListener("pointerdown", closeModelPicker);
    return function () { document.removeEventListener("pointerdown", closeModelPicker); };
  }, [modelOpen]);

  function resetDraft() {
    setDraft("");
  }

  function dispatch(text) {
    // Don't clear draft yet — wait for the send promise so the user's
    // input stays in the composer if the request is blocked (budget etc.).
    if (!running) controller.send(text).then(function (r) {
      if (!r || !r.__budgetBlock) resetDraft();
    });
  }

  function submit(overrideText) {
    if (awaitingAnswer) return;
    if (running) { controller.interrupt(); return; }
    var text = typeof overrideText === "string" ? overrideText.trim() : draft.trim();
    if ((!text && attachments.length === 0) || controller.busy) return;
    // Rule 2 — keep the agent inside the task only once a plan is committed.
    // Before that the task is still a free conversation, so don't gate it.
    if (hasPlan && model.looksOutOfScope(text)) {
      setScopePrompt({ text: text });
      return;
    }
    dispatch(text);
  }

  function onKeyDown(event) {
    var sc = workbenchServices.shortcuts();
    // Enter sends; Shift+Enter (or the user's customized newline binding)
    // inserts a newline. IME composition is guarded so multi-keystroke input
    // (zh/ja/ko) does not submit mid-composition. Falls back to the default
    // Enter-to-send behavior if the shortcut module is unavailable.
    if (sc && sc.matches(event, "composer-send")) {
      if (event.nativeEvent && event.nativeEvent.isComposing) return;
      event.preventDefault();
      submit();
      return;
    }
    if (sc && sc.matches(event, "composer-newline")) {
      // Allow the textarea's default Shift+Enter behavior (insert newline).
      return;
    }
    if (!sc && event.key === "Enter" && !event.shiftKey && !event.metaKey && !event.ctrlKey) {
      if (event.nativeEvent && event.nativeEvent.isComposing) return;
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === "Escape") {
      setModelOpen(false);
      setModelPanel("root");
    }
  }

  function pickFiles() { if (fileRef.current) fileRef.current.click(); }

  function showVoiceError(error) {
    voiceFeedbackRef.current.error(error);
  }

  function transcribeVoiceBlob(blob) {
    return wbcTranscribeVoiceBlob(blob).then(function (transcript) {
      if (transcript === false) {
        voiceFeedbackRef.current.noSpeech();
        return false;
      }
      var current = String(draftRef.current || "");
      var combined = current && !/\s$/.test(current) ? current + " " + transcript : current + transcript;
      setDraft(combined);
      draftRef.current = combined;
      voiceFeedbackRef.current.complete();
      if (voiceSnapshot.status.auto_send_after_asr === true) {
        submit(combined);
        return true;
      }
      requestAnimationFrame(function () {
        if (taRef.current) taRef.current.focus();
      });
      return true;
    });
  }

  function finishVoiceInput(recorder) {
    if (!recorder || voiceRecorderRef.current !== recorder) return;
    voiceRecorderRef.current = null;
    setVoicePhase("transcribing");
    voiceFeedbackRef.current.transcribing();
    recorder.stop()
      .then(transcribeVoiceBlob)
      .catch(showVoiceError)
      .finally(function () { setVoicePhase(""); });
  }

  function toggleVoiceInput() {
    if (disabled || voicePhase === "starting" || voicePhase === "transcribing") return;
    if (voicePhase === "recording") {
      var recorder = voiceRecorderRef.current;
      if (!recorder) {
        setVoicePhase("");
        return;
      }
      finishVoiceInput(recorder);
      return;
    }
    WbcVoice.stop();
    setVoicePhase("starting");
    voiceFeedbackRef.current.starting();
    var startedForSession = String(session.id || "");
    wbcStartVoiceRecorder({
      autoStopOnSilence: voiceSnapshot.status.auto_stop_on_silence !== false,
      onSilence: finishVoiceInput,
    })
      .then(function (recorder) {
        if (voiceSessionIdRef.current !== startedForSession) {
          recorder.stop().catch(function () {});
          return;
        }
        voiceRecorderRef.current = recorder;
        setVoicePhase("recording");
        voiceFeedbackRef.current.listening();
      })
      .catch(function (error) {
        setVoicePhase("");
        showVoiceError(error);
      });
  }

  function addFiles(files) {
    if (disabled) return;
    if (!files || !files.length) return;
    uploadCountRef.current += 1;
    setUploading(true);
    model.uploadAttachments(files)
      .then(function (uploaded) {
        onAttachmentsChange(function (current) {
          return (current || []).concat(uploaded || []);
        });
      })
      .catch(function (err) { workbenchServices.feedback().showToast(wbT("workbenchChat.uploadFailed", "Upload failed: {error}", { error: err.message || String(err) }), "error"); })
      .finally(function () {
        uploadCountRef.current = Math.max(0, uploadCountRef.current - 1);
        if (uploadCountRef.current === 0) setUploading(false);
        if (fileRef.current) fileRef.current.value = "";
      });
  }
  function onFilePick(event) {
    addFiles(event.target.files);
  }
  function onPaste(event) {
    if (disabled) return;
    var clipboard = event && (event.clipboardData || (event.nativeEvent && event.nativeEvent.clipboardData));
    if (!clipboard) return;
    var files = Array.prototype.slice.call(clipboard.files || []).filter(function (file) { return !!file; });
    // Some WebViews expose pasted files only through DataTransferItemList.
    if (!files.length) {
      files = Array.prototype.slice.call(clipboard.items || []).map(function (item) {
        return item && item.kind === "file" ? item.getAsFile() : null;
      }).filter(function (file) { return !!file; });
    }
    if (!files.length) return; // Preserve the browser's normal text paste.
    event.preventDefault();
    addFiles(files);
  }
  useWorkbenchEffect(function () {
    function onDroppedFiles(event) {
      var files = event && event.detail && event.detail.files;
      addFiles(files);
    }
    window.addEventListener("cyrene:add-task-attachments", onDroppedFiles);
    return function () { window.removeEventListener("cyrene:add-task-attachments", onDroppedFiles); };
  }, [disabled]);
  function removeAttachment(index) {
    onAttachmentsChange(attachments.filter(function (_a, i) { return i !== index; }));
  }

  var translatedModes = WB_MODES.map(function (m) { return wbModeMeta(m.id); });

  // While a run is paused awaiting a permission / clarification answer, the only
  // valid actions are on the question card itself — suppress the composer's
  // status chips so no answer buttons sit above the input box.
  var chips = awaitingAnswer ? [] : composerChips(status, controller, onRightTab, session);
  var current = wbModeMeta(mode || "auto");
  configuredModels = Array.isArray(configuredModels) ? configuredModels : [];
  selectedModelId = String(selectedModelId || "");
  var selectedModel = configuredModels.find(function (item) {
    return String(item.id || item.model || "") === selectedModelId;
  });
  var modelName = wbFriendlyModelName(
    selectedModel,
    session && (session.model || session.lastModel) || ""
  );
  reasoningEffort = String(reasoningEffort || "").trim().toLowerCase();
  var effortLabel = reasoningEffort
    ? wbT("settings.reasoningEffortValue." + reasoningEffort, reasoningEffort)
    : "";
  var modelButtonLabel = wbT("workbenchChat.chooseModel", "Choose model")
    + ": " + modelName + (effortLabel ? " · " + effortLabel : "");
  var supportedReasoningEfforts = wbSupportedReasoningEfforts(selectedModel);
  var sendDisabled = awaitingAnswer || (running ? false : (disabled || (!draft.trim() && attachments.length === 0)));

  useWorkbenchEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = workbenchServices.uiSurface();
    var unregister = [];
    unregister.push(uiSurface.register({
      node_id: "task_composer_input",
      parent_id: "root",
      scope: "main",
      get_node: function () {
        if (disabled || awaitingAnswer) return null;
        var currentDraft = String(draftRef.current || "");
        return {
          role: "textbox",
          name: composerPlaceholder(status),
          value_summary: currentDraft ? "Draft present" : "Empty draft",
          state: {
            session_id: String(session.id || ""),
            session_kind: "task",
            draft_empty: !currentDraft,
            draft_length: currentDraft.length,
            running: running === true,
            submit_exposed: !sendDisabled,
          },
        };
      },
      actions: [{
        action_id: "set_value",
        kind: "set_value",
        risk: "R1",
        gesture_aliases: ["text_input"],
        input_schema: { value: "text<=20000" },
      }, {
        action_id: "clear_value",
        kind: "set_value",
        risk: "R1",
        gesture_aliases: ["semantic_clear"],
        input_schema: { expected_value: "text<=20000" },
      }],
      handlers: {
        set_value: function (input) {
          var currentDraft = String(draftRef.current || "");
          var nextDraft = String(input.value || "");
          if (currentDraft && currentDraft !== nextDraft) {
            throw new Error("composer draft is not empty");
          }
          draftRef.current = nextDraft;
          setDraft(nextDraft);
          return { draft_length: nextDraft.length, submitted: false };
        },
        clear_value: function (input) {
          var currentDraft = String(draftRef.current || "");
          if (currentDraft !== String(input.expected_value || "")) {
            throw new Error("composer draft changed");
          }
          draftRef.current = "";
          setDraft("");
          return { draft_length: 0, cleared: true, submitted: false };
        },
      },
    }));
    var submitMode = running ? "interrupt" : "send";
    var submitActionId = running ? "interrupt" : "submit";
    unregister.push(uiSurface.register({
      node_id: "task_composer_submit",
      parent_id: "root",
      scope: "main",
      get_element: function () { return sendButtonRef.current; },
      get_node: function () {
        if (awaitingAnswer) return null;
        return {
          role: "button",
          name: running ? wbT("workbenchChat.stop", "Stop") : wbT("workbenchChat.send", "Send"),
          state: {
            session_id: String(session.id || ""),
            session_kind: "task",
            mode: submitMode,
            disabled: !!sendDisabled,
          },
        };
      },
      actions: sendDisabled ? [] : [{
        action_id: submitActionId,
        kind: "invoke",
        risk: running ? "R1" : "R2",
        gesture_aliases: ["press", "keyboard"],
        outcome: {
          effect: running ? "interrupts_current_run" : "submits_current_composer",
          target_scope: "task",
          inspect_after: true,
        },
      }],
      handlers: {
        interrupt: function () {
          var button = sendButtonRef.current;
          if (!button || button.disabled) throw new Error("composer interrupt is unavailable");
          button.click();
        },
        submit: function () {
          var button = sendButtonRef.current;
          if (!button || button.disabled) throw new Error("composer submit is unavailable");
          button.click();
        },
      },
    }));
    return function () { unregister.forEach(function (remove) { remove(); }); };
  }, [session.id, status, disabled, awaitingAnswer, running, sendDisabled]);

  return (
    <div className="workbench-composer wbc-composer">
      {scopePrompt && (
        <div className="wb-scope-prompt">
          <p>{wbT("task.scopePrompt", "This is outside the current task. Create it as a new follow-up task?")}</p>
          <div className="wb-card-actions">
            <button type="button" className="wb-btn primary" onClick={function () {
              var goal = scopePrompt.text.trim();
              controller.createFollowUp({ title: goal.slice(0, 40), goal: goal });
              setScopePrompt(null);
              resetDraft();
            }}>{wbT("task.createNewTask", "Create new task")}</button>
            <button type="button" className="wb-btn ghost" onClick={function () { var t = scopePrompt.text; setScopePrompt(null); dispatch(t); }}>{wbT("task.mergeCurrent", "Merge into current task")}</button>
            <button type="button" className="wb-btn ghost" onClick={function () { setScopePrompt(null); }}>{wbT("common.cancel", "Cancel")}</button>
          </div>
        </div>
      )}
      {chips.length > 0 && (
        <div className={"wb-composer-chips" + (status === "planning" ? " planning-actions" : "")}>
          {chips.map(function (c, i) {
            return <button key={i} type="button" className={"wb-chip" + (c.className ? " " + c.className : "")} disabled={controller.busy && c.guard !== false} onClick={c.onClick}>{c.label}</button>;
          })}
        </div>
      )}
      <div className="workbench-composer-box wbc-composer-box">
        {attachments.length > 0 && (
          <div className="wb-attach-row">
            {attachments.map(function (file, i) {
              var isImg = file.kind === "image" || String(file.content_type || "").indexOf("image") === 0;
              return (
                <div className={"wb-attach-card" + (isImg ? " image" : "")} key={file.id || i}>
                  {isImg && file.url
                    ? <img src={file.url} alt={file.name || "image"} />
                    : <span className="wb-attach-name" title={file.name}>{file.name || "file"}</span>}
                  <button type="button" className="wb-attach-x" disabled={disabled} onClick={function () { removeAttachment(i); }} aria-label={wbT("workbenchChat.removeAttachment", "Remove attachment")}>{ICONS.x}</button>
                </div>
              );
            })}
          </div>
        )}
        <textarea
          ref={taRef}
          className="wbc-composer-textarea"
          value={draft}
          onChange={function (event) {
            draftRef.current = event.target.value;
            setDraft(event.target.value);
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          placeholder={composerPlaceholder(status)}
          rows={2}
          disabled={disabled}
        />
        <div className="workbench-composer-actions wbc-composer-actions">
          <input ref={fileRef} type="file" multiple style={{ display: "none" }} onChange={onFilePick} />
          <button type="button" className="wb-composer-icon wbc-composer-icon" title={uploading ? wbT("workbenchChat.uploading", "Uploading...") : wbT("workbenchChat.addAttachment", "Add attachment")} disabled={uploading || disabled} onClick={pickFiles}>
            {uploading ? <span className="wb-spinner" /> : ICONS.attach}
          </button>
          <span className="wb-composer-spacer" />
          {modelName ? (
            <span className="wbc-pop-anchor wbc-model-anchor" ref={modelPickerRef}>
              <button
                type="button"
                className={"wbc-model-button" + (modelOpen ? " active" : "")}
                title={modelButtonLabel}
                aria-label={modelButtonLabel}
                aria-haspopup="menu"
                aria-expanded={modelOpen}
                disabled={disabled}
                onClick={function () {
                  setModelOpen(!modelOpen);
                  setModelPanel("root");
                }}
              >
                <span className="wbc-model-button-icon" aria-hidden="true">{ICONS.model}</span>
                <span className="wbc-model-button-name">{modelName}</span>
                {effortLabel ? <span className="wbc-model-button-effort">{effortLabel}</span> : null}
                <span className="wbc-model-button-chevron">{ICONS.chevronDown}</span>
              </button>
              {modelOpen && !disabled && (
                <div className="wbc-popmenu wbc-model-menu" role="menu">
                  {modelPanel === "root" && (
                    <>
                      <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("models"); }}>
                        <span className="wbc-model-menu-key">{wbT("workbenchChat.model", "Model")}</span>
                        <span className="wbc-model-menu-value wbc-model-menu-model-name">{modelName}</span>
                        <span className="wbc-model-menu-chevron">{ICONS.chevronRight}</span>
                      </button>
                      {supportedReasoningEfforts.length > 0 && (
                        <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("effort"); }}>
                          <span className="wbc-model-menu-key">{wbT("workbenchChat.reasoningEffort", "Reasoning effort")}</span>
                          <span className="wbc-model-menu-value">{effortLabel || "—"}</span>
                          <span className="wbc-model-menu-chevron">{ICONS.chevronRight}</span>
                        </button>
                      )}
                      <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("permission"); }}>
                        <span className="wbc-model-menu-key">{wbT("workbenchChat.permissionMode", "Permission mode")}</span>
                        <span className="wbc-model-menu-value">{current.label}</span>
                        <span className="wbc-model-menu-chevron">{ICONS.chevronRight}</span>
                      </button>
                    </>
                  )}
                  {modelPanel === "models" && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{ICONS.chevronLeft}</span>
                        <span>{wbT("workbenchChat.model", "Model")}</span>
                      </button>
                      {configuredModels.map(function (item) {
                        var id = String(item.id || item.model || "");
                        var active = id === selectedModelId;
                        return (
                          <button key={id} type="button" className={active ? "active" : ""} onClick={function () {
                            onSelectedModelIdChange(id);
                            onReasoningEffortChange(String(item.reasoning_effort || "").trim().toLowerCase());
                            setModelPanel("root");
                          }}>
                            <span className="wbc-popmenu-label">{item.name || item.model}</span>
                            {item.desc ? <span className="wbc-popmenu-desc">{item.desc}</span> : null}
                            {active ? <span className="wbc-popmenu-check">{ICONS.checkSmall}</span> : null}
                          </button>
                        );
                      })}
                    </>
                  )}
                  {modelPanel === "effort" && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{ICONS.chevronLeft}</span>
                        <span>{wbT("workbenchChat.reasoningEffort", "Reasoning effort")}</span>
                      </button>
                      {supportedReasoningEfforts.map(function (effort) {
                        var active = effort === reasoningEffort;
                        return (
                          <button key={effort} type="button" className={active ? "active" : ""} onClick={function () {
                            onReasoningEffortChange(effort);
                            setModelPanel("root");
                          }}>
                            <span className="wbc-popmenu-label">{wbT("settings.reasoningEffortValue." + effort, effort)}</span>
                            {active ? <span className="wbc-popmenu-check">{ICONS.checkSmall}</span> : null}
                          </button>
                        );
                      })}
                    </>
                  )}
                  {modelPanel === "permission" && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{ICONS.chevronLeft}</span>
                        <span>{wbT("workbenchChat.permissionMode", "Permission mode")}</span>
                      </button>
                      {translatedModes.map(function (item) {
                        var active = (mode || "auto") === item.id;
                        return (
                          <button key={item.id} type="button" className={active ? "active" : ""} onClick={function () {
                            onModeChange(item.id);
                            setModelPanel("root");
                          }}>
                            <span className="wbc-popmenu-label">{item.label}</span>
                            <span className="wbc-popmenu-desc">{item.desc}</span>
                            {active ? <span className="wbc-popmenu-check">{ICONS.checkSmall}</span> : null}
                          </button>
                        );
                      })}
                    </>
                  )}
                </div>
              )}
            </span>
          ) : null}
          {voiceSnapshot.status.asr_ready ? (
            <button
              type="button"
              className={"wb-composer-icon wbc-composer-icon wbc-voice-input" + (voicePhase ? " " + voicePhase : "")}
              onClick={toggleVoiceInput}
              disabled={disabled || voicePhase === "starting" || voicePhase === "transcribing"}
              title={voicePhase === "recording"
                ? (voiceSnapshot.status.auto_stop_on_silence !== false
                    ? wbT("workbenchChat.voiceInputAutoStop", "Recording · pauses automatically start recognition")
                    : wbT("workbenchChat.voiceInputStop", "Stop recording"))
                : voicePhase === "starting"
                  ? wbT("workbenchChat.voiceInputStarting", "Accessing microphone…")
                  : voicePhase === "transcribing"
                  ? wbT("workbenchChat.voiceTranscribing", "Recognizing speech…")
                  : wbT("workbenchChat.voiceInputStart", "Voice input")}
              aria-label={voicePhase === "recording"
                ? wbT("workbenchChat.voiceInputStop", "Stop recording")
                : wbT("workbenchChat.voiceInputStart", "Voice input")}
              aria-pressed={voicePhase === "recording"}
              aria-busy={voicePhase === "starting" || voicePhase === "transcribing"}
            >
              {voicePhase === "starting" || voicePhase === "transcribing"
                ? <span className="wb-spinner small" />
                : ComposerBrowserIcon ? <ComposerBrowserIcon name="microphone" size={16} /> : null}
            </button>
          ) : null}
          <button
            ref={sendButtonRef}
            type="button"
            className={"wb-composer-send wbc-send" + (running ? " stop" : "")}
            onClick={submit}
            disabled={sendDisabled}
            title={running ? wbT("workbenchChat.stop", "Stop") : wbT("workbenchChat.send", "Send")}
          >
            {running ? ICONS.stop : (controller.busy ? <span className="wb-spinner" /> : ICONS.send)}
          </button>
        </div>
      </div>
    </div>
  );
}

export { RightContextPanel, TaskBoard, TaskWorkArea, WorkbenchTaskPane }
