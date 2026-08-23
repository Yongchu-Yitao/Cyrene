import { workbenchServices } from "../../shared/runtime/services.jsx"
var { useRef: useWorkbenchRef, useState: useWorkbenchState } = React;

function wbT(key, fallback, params) {
  return workbenchServices.i18n().t(key, params, fallback);
}

function isDoneStepStatus(status) {
  return status === "completed" || status === "done";
}

// A step the user no longer needs to act on: completed OR explicitly skipped.
// Used to find the "next" runnable step and to decide when the plan is finished.
function isResolvedStepStatus(status) {
  return isDoneStepStatus(status) || status === "skipped";
}

function isRunningStepStatus(status) {
  return status === "running";
}

function stepExecutionPrompt(session, step) {
  var lines = [
    "请为当前任务计划中的这个步骤生成一个 subagent 执行，并在完成后汇总结果。",
    "当前任务：" + String((session && (session.goal || session.title)) || "").trim(),
    "步骤：" + String((step && step.title) || "").trim(),
  ];
  if (step && step.description) lines.push("步骤说明：" + String(step.description).trim());
  return lines.filter(Boolean).join("\n");
}

// Pre-run context files the user pinned for a step, split by source: workspace
// path references (read by the subagent's file tools) vs uploaded attachments.
function splitStepContextFiles(step) {
  var files = (step && Array.isArray(step.contextFiles)) ? step.contextFiles : [];
  var workspace = [];
  var uploads = [];
  files.forEach(function (f) {
    if (!f) return;
    if (f.source === "upload") uploads.push(f);
    else workspace.push(f);
  });
  return { workspace: workspace, uploads: uploads };
}

// The prompt actually sent to the subagent: the user's edited override (or the
// default), plus a reference block for any workspace context files they pinned.
function effectiveStepPrompt(session, step) {
  var override = (step && typeof step.promptOverride === "string") ? step.promptOverride.trim() : "";
  var base = override || stepExecutionPrompt(session, step);
  var workspace = splitStepContextFiles(step).workspace;
  if (workspace.length) {
    var rows = workspace
      .map(function (f) { return "- " + String((f && (f.path || f.name)) || "").trim(); })
      .filter(function (row) { return row !== "- "; });
    if (rows.length) {
      base += "\n\n请重点参考以下工作区文件（先用 read_file 等工具阅读再动手）：\n" + rows.join("\n");
    }
  }
  return base;
}

function formatDurationSec(sec) {
  if (!Number.isFinite(sec) || sec < 1) return "";
  sec = Math.max(1, Math.round(sec));
  if (sec < 60) return sec + "s";
  var min = Math.floor(sec / 60);
  var rest = sec % 60;
  if (min < 60) return rest ? (min + "m " + rest + "s") : (min + "m");
  var hour = Math.floor(min / 60);
  var remMin = min % 60;
  return remMin ? (hour + "h " + remMin + "m") : (hour + "h");
}

// Duration of a step, in priority order: an explicit recorded `durationSec`,
// then the startedAt→completedAt/updatedAt span, then the first/last
// progress-event timestamps. Returns "" when nothing reliable is known.
function stepDurationText(step) {
  if (!step) return "";
  if (Number.isFinite(step.durationSec)) return formatDurationSec(step.durationSec);
  var startMs = step.startedAt ? Date.parse(step.startedAt) : NaN;
  var endMs = (step.completedAt || step.updatedAt) ? Date.parse(step.completedAt || step.updatedAt) : NaN;
  if (Number.isFinite(startMs) && Number.isFinite(endMs) && endMs > startMs) {
    return formatDurationSec((endMs - startMs) / 1000);
  }
  if (Array.isArray(step.progressEvents) && step.progressEvents.length >= 2) {
    var first = Date.parse(step.progressEvents[0] && step.progressEvents[0].time || "");
    var last = Date.parse(step.progressEvents[step.progressEvents.length - 1] && step.progressEvents[step.progressEvents.length - 1].time || "");
    if (Number.isFinite(first) && Number.isFinite(last) && last > first) {
      return formatDurationSec((last - first) / 1000);
    }
  }
  return "";
}

function stepMetaText(step) {
  var duration = stepDurationText(step);
  if (duration) return duration;
  if (!step) return "";
  if (isRunningStepStatus(step.status)) return "进行中";
  if (isDoneStepStatus(step.status)) return "已完成";
  if (step.status === "failed") return "需处理";
  if (step.status === "paused") return "已暂停";
  return "";
}

function useTaskController(session, onRefresh, runtime) {
  var model = workbenchServices.model();
  var [busy, setBusy] = useWorkbenchState(false);
  var runAbortRef = useWorkbenchRef(null);
  var interruptedRef = useWorkbenchRef(false);
  var sid = session ? session.id : "";

  function apply(next) { if (onRefresh && next && !next.__budgetBlock) onRefresh(next); return next; }
  function sessionFromStore(next, fallback) {
    if (!next || !sid) return fallback || session;
    var projects = Array.isArray(next.projects) ? next.projects : [];
    for (var i = 0; i < projects.length; i++) {
      var sessions = Array.isArray(projects[i].sessions) ? projects[i].sessions : [];
      for (var j = 0; j < sessions.length; j++) {
        if (sessions[j] && sessions[j].id === sid) return sessions[j];
      }
    }
    if (next.activeSession && next.activeSession.id === sid) return next.activeSession;
    return fallback || session;
  }
  function fail(err) { workbenchServices.feedback().showToast((err && err.message) || String(err), "error"); }
  function rethrowPlanConflict(err) {
    if (err && err.code === "stale_plan_revision") throw err;
  }
  function patch(p) { return model.patchSession(sid, p); }
  function run(promise) {
    setBusy(true);
    return promise.then(apply).catch(fail).finally(function () { setBusy(false); });
  }
  // Client-only "agent is working" marker that drives the 「Agent 正在处理」card
  // for background ops that don't enter the `running` status (规划 / 反思 / 验收).
  function setAgentBusy(op) {
    if (runtime && runtime.onLocalPatch) runtime.onLocalPatch({ agentBusy: op || null });
  }
  // Like run(), but also flips on the activity card + opens the live feed window
  // (events after startedAt are this op's). Cleared when the server response lands.
  function runAgentic(op, promise) {
    setBusy(true);
    setAgentBusy(Object.assign({ startedAt: new Date().toISOString() }, op || {}));
    return promise.then(apply).catch(fail).finally(function () { setBusy(false); setAgentBusy(null); });
  }
  function stepById(plan, stepId) {
    var items = Array.isArray(plan) ? plan : [];
    return items.find(function (item) { return item && item.id === stepId; }) || null;
  }
  function ensurePlanApproved(baseSession) {
    var current = baseSession || session;
    var definitionRevision = Number(current && current.planDefinitionRevision || 0);
    if (
      current
      && current.approvedPlanDefinitionRevision != null
      && Number(current.approvedPlanDefinitionRevision) === definitionRevision
    ) {
      return Promise.resolve(current);
    }
    return model.patchSession(sid, {
      approvedPlanDefinitionRevision: definitionRevision,
      events: model.withEvent(current, "PlanApproved", "用户确认执行当前版本的计划。"),
    })
      .then(function (store) {
        apply(store);
        return sessionFromStore(store, current);
      });
  }
  function requirePlan(baseSession) {
    var plan = baseSession && Array.isArray(baseSession.plan) ? baseSession.plan : [];
    if (plan.length) return true;
    workbenchServices.feedback().showToast(wbT("task.plan.addAtLeastOneStep", "Add at least one step before approval or execution."), "warning");
    return false;
  }
  function stepFailedPatch(baseSession, basePlan, stepTitle, stepId, msg) {
    return model.patchSession(sid, {
      status: "failed",
      plan: model.markStepById(basePlan, stepId, "failed", msg),
      agentReply: "步骤执行失败：" + msg,
      events: model.withEvent(baseSession, "ExecutionFailed", "步骤「" + stepTitle + "」执行失败：" + msg, { stepId: stepId || "" }),
    }).then(apply);
  }
  function runStepCore(baseSession, stepId, options) {
    options = options || {};
    var basePlan = Array.isArray(baseSession && baseSession.plan) ? baseSession.plan : [];
    var step = stepById(basePlan, stepId);
    if (!baseSession || !step || !stepId) return Promise.resolve(null);
    interruptedRef.current = false;
    var ac = (typeof AbortController !== "undefined") ? new AbortController() : null;
    runAbortRef.current = ac;
    var index = basePlan.findIndex(function (item) { return item && item.id === stepId; });
    var stepTitle = String(step.title || ("步骤 " + (index + 1))).trim();
    var startPlan = model.markStepById(basePlan, stepId, "running", "正在启动 subagent，等待模型思考…");
    var startEvents = model.withEvent(baseSession, "ExecutionStarted", "开始执行步骤：" + stepTitle, { stepId: step.id || "" });
    return model.patchSession(sid, { status: "running", plan: startPlan, agentReply: "正在执行步骤：" + stepTitle, events: startEvents })
      .then(apply)
      .then(function (patched) {
        var patchedSession = sessionFromStore(patched, baseSession);
        var uploadCtx = splitStepContextFiles(step).uploads;
        return model.createRun(sid, effectiveStepPrompt(patchedSession, step), {
          attachments: uploadCtx.concat((runtime && runtime.attachments) || []),
          mode: (runtime && runtime.mode) || undefined,
          model: (runtime && runtime.model) || undefined,
          reasoningEffort: (runtime && runtime.reasoningEffort) || "",
          stepId: step.id || undefined,
          stepTitle: stepTitle,
          action: "spawn_subagent",
          meta: { scope: "plan_step", continueAll: !!options.continueAll },
          planDefinitionRevision: Number(patchedSession.planDefinitionRevision || 0),
          signal: ac ? ac.signal : undefined,
        });
      })
      .then(function (next) {
        var s2 = sessionFromStore(next, baseSession);
        if (String(s2.status || "") === "waiting_for_user") return next;
        // /runs owns the durable run + step transition in one server-side write.
        // Never issue a second completion PATCH here: losing that request after
        // the tools already ran used to strand the task in `running` forever.
        var completedStep = stepById(s2.plan, stepId);
        if (!completedStep || !isDoneStepStatus(completedStep.status)) {
          throw new Error("服务端未能提交步骤完成状态，请刷新后重试。");
        }
        if (runtime && runtime.clearAttachments) runtime.clearAttachments();
        return next;
      })
      .then(apply)
      .catch(function (err) {
        if (interruptedRef.current || (err && err.name === "AbortError")) return null;
        if (err && ["stale_plan_revision", "plan_not_approved", "unmet_dependencies", "step_not_found"].indexOf(err.code) >= 0) {
          throw err;
        }
        var msg = (err && err.message) || String(err);
        return stepFailedPatch(baseSession, basePlan, stepTitle, step.id || "", msg).then(function (next) {
          throw err;
        });
      })
      .finally(function () { runAbortRef.current = null; });
  }

  var ctrl = {
    busy: busy,
    applyStore: apply,

    // Intent-aware composer entry (idle / answered / acted). The server decides:
    // a question → a direct answer (status `answered`); a one-shot instruction →
    // execute + report (status `acted`); a complex goal → a plan (status
    // `planning`). The reply card follows the returned status. On total failure,
    // degrade to an honest client-side plan rather than swallowing the input.
    send: function (text) {
      var input = (text != null ? String(text) : "").trim();
      var hasAttach = (((runtime && runtime.attachments) || []).length > 0);
      if (!input && !hasAttach) return Promise.resolve();
      return runAgentic({ kind: "dispatch", label: "正在理解你的输入…" }, model.dispatch(sid, input, {
        attachments: (runtime && runtime.attachments) || [],
        mode: (runtime && runtime.mode) || undefined,
        model: (runtime && runtime.model) || undefined,
        reasoningEffort: (runtime && runtime.reasoningEffort) || "",
        basePlanRevision: Number(session.planRevision || 0),
      }).then(function (store) {
        if (runtime && runtime.clearAttachments) runtime.clearAttachments();
        return store;
      }).catch(function (err) {
        rethrowPlanConflict(err);
        // Budget errors: show toast and return sentinel so the composer
        // keeps the user's input in the draft instead of clearing it.
        var code = err.code || (err.payload && err.payload.code) || "";
        if (code.startsWith("budget_")) {
          var codes = workbenchServices.chat().budgetCodes || {};
          var i18nKey = "budget.error." + (codes[code] || "5h");
          workbenchServices.feedback().showToast(wbT(i18nKey, err.message || ""), "error");
          return { __budgetBlock: true };
        }
        var goal = (session.goal || input).trim();
        return patch({
          status: "planning",
          goal: goal,
          plan: model.buildPlanSteps(goal, session.constraints || []),
          acceptanceCriteria: model.buildAcceptance(goal, session.constraints || []),
          agentReply: "处理服务暂时不可用，我先给出一份基础计划，你可以编辑后逐步执行，或稍后重试。",
          events: model.withEvent(session, "PlanGenerated", "生成基础执行计划（兜底）。"),
        });
      }));
    },

    // Answer a paused run's permission / clarification question → resume the
    // round. The server returns either the continued reply or a follow-up
    // question; apply() swaps the card accordingly.
    answer: function (questionId, optionText) {
      var qid = String(questionId || "").trim();
      var ans = String(optionText || "").trim();
      if (!qid || !ans) return Promise.resolve();
      interruptedRef.current = false;
      setBusy(true);
      setAgentBusy({ kind: "answer", label: "正在继续…", startedAt: new Date().toISOString() });
      return model.answer(sid, qid, ans)
        .then(apply)
        .then(function (store) {
          if (store && store.continuePlanExecution) {
            return ctrl.executeAll({ continuing: true, baseSession: store.activeSession });
          }
          return store;
        })
        .catch(function (err) {
          if (interruptedRef.current || (err && err.name === "AbortError")) return null;
          fail(err);
          return null;
        })
        .finally(function () { setBusy(false); setAgentBusy(null); });
    },

    // answered / acted → promote this exchange into a real, planned task.
    promoteToPlan: function () {
      var goal = (session.goal || "").trim();
      if (!goal) { focusComposer(); return Promise.resolve(); }
      return runAgentic({ kind: "plan", label: "正在把它整理成执行计划…" }, model.generatePlan(sid, goal, { basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        return patch({ status: "planning", goal: goal, plan: model.buildPlanSteps(goal, session.constraints || []), acceptanceCriteria: model.buildAcceptance(goal, session.constraints || []), agentReply: "计划生成服务暂时不可用，已生成基础计划。", events: model.withEvent(session, "PlanGenerated", "生成基础执行计划（兜底）。") });
      }));
    },

    // idle → planning. Generate a REAL plan from the goal — the agent explores
    // the project workspace server-side ("执行前必须有计划"); no agent work runs
    // yet. On failure, fall back to an honest client-side template (all pending).
    start: function (goalText) {
      var goal = (goalText != null ? String(goalText) : (session.goal || "")).trim();
      if (!goal) return Promise.resolve();
      return runAgentic({ kind: "plan", label: "正在分析任务并生成执行计划…" }, model.generatePlan(sid, goal, { basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        var constraints = session.constraints || [];
        return patch({
          status: "planning",
          goal: goal,
          plan: model.buildPlanSteps(goal, constraints),
          acceptanceCriteria: model.buildAcceptance(goal, constraints),
          agentReply: "计划生成服务暂时不可用，已生成基础计划，你可以编辑后逐步执行，或稍后重试。",
          events: model.withEvent(session, "PlanGenerated", "生成基础执行计划（兜底）。"),
        });
      }));
    },

    // Empty task (no real goal) → 「直接开始」. The agent reads the project
    // workspace + notes server-side and proposes a plan to kick things off, so the
    // user doesn't have to phrase a goal first. Same path as start(), but seeded
    // with a project-derived default goal (passed in from the card so it follows
    // the UI language).
    autoStart: function () {
      return runAgentic({ kind: "plan", label: "正在阅读项目并规划…" }, model.generatePlan(sid, "", { autoStart: true, basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        var basis = (session.goal || "").trim() || "推进本项目当前最该做的工作";
        return patch({
          status: "planning",
          plan: model.buildPlanSteps(basis, session.constraints || []),
          acceptanceCriteria: model.buildAcceptance(basis, session.constraints || []),
          agentReply: "计划生成服务暂时不可用，已生成一份基础计划，你可以编辑后逐步执行，或稍后重试。",
          events: model.withEvent(session, "PlanGenerated", "自动生成执行计划（兜底）。"),
        });
      }));
    },

    // Revise the plan from natural-language feedback. While the plan is still
    // untouched or already fully handled, regenerate it with the feedback. While
    // execution is still in progress, just record the note — regenerating would
    // wipe completed steps' progress (use 重新生成 explicitly to start over).
    modifyPlan: function (text) {
      var goal = (session.goal || "").trim();
      var plan = Array.isArray(session.plan) ? session.plan : [];
      if (model.hasUnresolvedStartedSteps(plan)) {
        return run(patch({
          agentReply: "已记录你的补充：\n" + text + "\n（任务已在执行中，计划未重置；如需重排可点「重新生成」。）",
          events: model.withEvent(session, "PlanRevised", "执行中补充：" + text),
        }));
      }
      return runAgentic({ kind: "plan", label: "正在结合你的补充重新规划…" }, model.generatePlan(sid, goal, { feedback: text, operation: "auto", basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        var keepPlan = Array.isArray(session.plan) && session.plan.length ? session.plan : model.buildPlanSteps(goal, session.constraints || []);
        var keepAcceptance = Array.isArray(session.acceptanceCriteria) && session.acceptanceCriteria.length
          ? session.acceptanceCriteria
          : model.buildAcceptance(goal, session.constraints || []);
        return patch({
          status: "planning",
          plan: keepPlan,
          acceptanceCriteria: keepAcceptance,
          agentReply: "计划生成服务暂时不可用，已保留原计划并记录你的调整：\n" + text,
          events: model.withEvent(session, "PlanRevised", "按用户要求调整计划：" + text),
        });
      }));
    },

    regeneratePlan: function () {
      var goal = (session.goal || "").trim();
      return runAgentic({ kind: "plan", label: "正在重新生成执行计划…" }, model.generatePlan(sid, goal, {
        feedback: "请基于当前任务目标生成一份全新的执行计划，不保留原计划步骤。",
        operation: "replace",
        basePlanRevision: Number(session.planRevision || 0),
      }).catch(function (err) {
        rethrowPlanConflict(err);
        return patch({
          status: "planning",
          plan: Array.isArray(session.plan) ? session.plan : [],
          acceptanceCriteria: Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [],
          agentReply: "重新生成失败，原计划保持不变，请稍后重试。",
          events: model.withEvent(session, "PlanGenerated", "重新生成执行计划失败，保留原计划。"),
        });
      }));
    },

    // planning → waiting_for_approval — the 需要你确认 gate before any change.
    approvePlan: function () {
      if (!requirePlan(session)) return Promise.resolve();
      var events = model.withEvent(session, "PlanApproved", "用户批准执行计划。");
      return run(patch({
        status: "waiting_for_approval",
        approvedPlanDefinitionRevision: Number(session.planDefinitionRevision || 0),
        agentReply: "执行前请确认下面的操作。",
        events: events,
      }));
    },

    // planning → 跳过单独确认，直接连续执行全部步骤。
    approveAndRunAll: function () {
      if (!requirePlan(session)) return Promise.resolve();
      return model.patchSession(sid, {
        approvedPlanDefinitionRevision: Number(session.planDefinitionRevision || 0),
        events: model.withEvent(session, "PlanApproved", "用户批准计划并连续执行全部步骤。"),
      }).then(apply).then(function (store) {
        return ctrl.executeAll({ baseSession: sessionFromStore(store, session) });
      });
    },

    configureGoalLoop: function () {
      return workbenchServices.feedback().confirmModal({
        title: wbT("goalLoop.risk.title", "持续执行到验收通过"),
        body: wbT(
          "goalLoop.risk.body",
          "Agent 会在后台反复执行计划、独立验收，并在验收失败时自动返工，直到验收通过或达到退出条件。\n\n这个模式通常会产生更多模型调用、工具调用和文件修改，成本明显高于普通执行。关闭页面不会停止任务，你可以随时暂停或取消。"
        ),
        confirmLabel: wbT("goalLoop.risk.confirm", "了解并继续"),
      }).then(function (ok) {
        if (ok && runtime && runtime.onOpenGoalLoop) runtime.onOpenGoalLoop();
        return ok;
      });
    },

    adjustGoalLoopLimits: function () {
      if (runtime && runtime.onOpenGoalLoopLimits) runtime.onOpenGoalLoopLimits();
    },

    reject: function () {
      var events = model.withEvent(session, "ActionRejected", "用户拒绝了当前操作。");
      return run(patch({ status: "planning", agentReply: "操作已取消。你可以修改要求，或让我重新规划。", events: events }));
    },

    // Honest execution: run the NEXT dependency-ready step for real. Delegates
    // to the per-step run, which executes one step and marks ONLY that step
    // done, with real timing + real tool data. Reused by resume / retry.
    execute: function () {
      if (!requirePlan(session)) return Promise.resolve();
      return ensurePlanApproved(session).then(function (approvedSession) {
        var plan = Array.isArray(approvedSession.plan) ? approvedSession.plan : [];
        var nextStep = model.findNextRunnableStep(plan);
        if (!nextStep) {
          var remaining = plan.filter(function (item) { return !isResolvedStepStatus(item && item.status); });
          if (!remaining.length) {
            return run(model.patchSession(sid, {
              status: "review",
              agentReply: "所有步骤已完成，请验收。",
              events: model.withEvent(approvedSession, "ExecutionFinished", "全部步骤已完成，等待你验收。"),
            }));
          }
          return run(model.patchSession(sid, {
            status: "blocked",
            agentReply: "没有可执行的步骤，请先完成或调整被阻塞步骤的前置依赖。",
            events: model.withEvent(approvedSession, "ExecutionBlocked", "步骤依赖尚未满足，任务已阻塞。"),
          }));
        }
        setBusy(true);
        return runStepCore(approvedSession, nextStep.id)
          .catch(function (err) {
            if (interruptedRef.current || (err && err.name === "AbortError")) return;
            fail(err);
          })
          .finally(function () { setBusy(false); });
      });
    },

    // Run every unresolved step in order. Each iteration starts from the latest
    // server-returned session so completed/failed/skipped state is preserved.
    executeAll: function (options) {
      options = options || {};
      var initialSession = options.baseSession || session;
      if (!requirePlan(initialSession)) return Promise.resolve();
      setBusy(true);
      interruptedRef.current = false;
      var currentSession = initialSession;
      var approvalPromise = options.continuing ? Promise.resolve(initialSession) : ensurePlanApproved(initialSession);
      return approvalPromise.then(function (approvedSession) {
        currentSession = approvedSession;
        if (options.continuing) return { activeSession: approvedSession };
        var startedEvents = model.withEvent(approvedSession, "ExecutionStarted", "开始连续执行全部剩余步骤。");
        return model.patchSession(sid, { status: "running", agentReply: "正在按依赖顺序执行全部剩余步骤。", events: startedEvents });
      })
        .then(apply)
        .then(function (next) {
          currentSession = sessionFromStore(next, currentSession);
          function loop() {
            if (interruptedRef.current) return null;
            var plan = Array.isArray(currentSession.plan) ? currentSession.plan : [];
            var nextStep = model.findNextRunnableStep(plan);
            if (!nextStep) {
              var remaining = plan.filter(function (item) { return !isResolvedStepStatus(item && item.status); });
              if (remaining.length) {
                return model.patchSession(sid, {
                  status: "blocked",
                  agentReply: "没有可执行的步骤，请先完成或调整被阻塞步骤的前置依赖。",
                  events: model.withEvent(currentSession, "ExecutionBlocked", "步骤依赖尚未满足，连续执行已停止。"),
                }).then(apply);
              }
              return model.patchSession(sid, {
                status: "review",
                agentReply: "所有步骤已完成，请验收。",
                artifacts: model.ensureArtifacts(currentSession),
                events: model.withEvent(currentSession, "ExecutionFinished", "全部步骤已完成，等待你验收。"),
              }).then(apply);
            }
            return runStepCore(currentSession, nextStep.id, { continueAll: true })
              .then(function (nextStore) {
                if (interruptedRef.current || !nextStore) return null;
                currentSession = sessionFromStore(nextStore, currentSession);
                if (String(currentSession.status || "") === "failed") return nextStore;
                if (String(currentSession.status || "") === "review") return nextStore;
                if (String(currentSession.status || "") === "waiting_for_user") return nextStore;
                if (String(currentSession.status || "") === "blocked") return nextStore;
                return loop();
              });
          }
          return loop();
        })
        .catch(function (err) {
          if (interruptedRef.current || (err && err.name === "AbortError")) return;
          fail(err);
        })
        .finally(function () { setBusy(false); });
    },

    // Stop the in-flight run (abort the fetch + server-side interrupt) → paused.
    // A running STEP must also drop out of "running" — otherwise the plan card
    // keeps the step spinning with a live 停止 button and the click looks dead.
    // Reset startedAt so a later re-run times the step fresh.
    interrupt: function () {
      if (session.goalLoop && session.goalLoop.status === "running") {
        interruptedRef.current = true;
        model.interruptSession(sid);
        return run(model.pauseGoalLoop(sid));
      }
      interruptedRef.current = true;
      if (runAbortRef.current) { try { runAbortRef.current.abort(); } catch (e) {} }
      model.interruptSession(sid);
      var now = new Date().toISOString();
      var stoppedPlan = Array.isArray(session.plan) ? session.plan.map(function (s) {
        if (!s || s.status !== "running") return s;
        return Object.assign({}, s, { status: "pending", startedAt: null, currentAction: "已停止，可重新执行。", updatedAt: now });
      }) : session.plan;
      return model.patchSession(sid, {
        status: "paused",
        plan: stoppedPlan,
        agentReply: "执行已被你中断，可继续或调整后重试。",
        events: model.withEvent(session, "Paused", "用户中断了执行。"),
      }).then(apply).catch(fail);
    },

    pause: function () {
      return run(patch({ status: "paused", events: model.withEvent(session, "Paused", "任务已暂停。") }));
    },

    runStep: function (step) {
      if (!step || !step.id) return Promise.resolve();
      setBusy(true);
      return ensurePlanApproved(session)
        .then(function (approvedSession) {
          return runStepCore(approvedSession, step.id);
        })
        .catch(function (err) {
          if (interruptedRef.current || (err && err.name === "AbortError")) return;
          fail(err);
        })
        .finally(function () { setBusy(false); });
    },

    // Merge fields into a single plan step and persist (used by the pre-run
    // command editor: prompt override + context files). Does not toggle busy —
    // these are lightweight edits that shouldn't disable the run buttons.
    mutatePlan: function (operation, input) {
      var payload = Object.assign({}, input || {}, {
        operation: operation,
        basePlanRevision: Number(session.planDefinitionRevision || 0),
      });
      return model.mutatePlan(sid, payload).then(apply).catch(function (err) {
        fail(err);
        return null;
      });
    },

    patchStep: function (stepId, fields) {
      if (!stepId) return Promise.resolve();
      return ctrl.mutatePlan("update", { stepId: stepId, fields: fields });
    },

    addStep: function (step) {
      return ctrl.mutatePlan("add", { step: step || {} });
    },

    deleteStep: function (stepId) {
      return ctrl.mutatePlan("delete", { stepId: stepId });
    },

    reorderSteps: function (orderedStepIds) {
      return ctrl.mutatePlan("reorder", { orderedStepIds: orderedStepIds });
    },

    resume: function () {
      if (session.goalLoop && ["paused", "blocked"].indexOf(session.goalLoop.status) >= 0) {
        return run(model.resumeGoalLoop(sid));
      }
      return model.patchSession(sid, { events: model.withEvent(session, "Resumed", "继续执行任务。") })
        .then(apply).then(function () { return ctrl.execute(); });
    },

    retry: function () { return ctrl.execute(); },

    // After independent acceptance fails, repair the current task in-place. The
    // server receives the explicit repair command and injects the latest failed
    // criteria/evidence into the same session's agent context.
    continueModify: function () {
      var criteria = Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
      var failed = criteria.filter(function (item) { return item && item.status === "failed"; });
      var lines = ["请参考最近一次验收结果，继续修改并完成当前 session 的任务。请保留已通过的验收标准，优先修复未通过项。"];
      failed.slice(0, 8).forEach(function (item) {
        var text = String(item.text || "").trim();
        var evidence = String(item.evidence || "").trim();
        if (text) lines.push("- 未通过：" + text + (evidence ? "；验收依据：" + evidence : ""));
      });
      if (session.verifyReason) lines.push("验收结论：" + String(session.verifyReason));
      return runAgentic({ kind: "repair", label: "正在参考验收结果继续修改…" }, model.continueAcceptanceRepair(sid, lines.join("\n"), {
        attachments: (runtime && runtime.attachments) || [],
        mode: (runtime && runtime.mode) || undefined,
        model: (runtime && runtime.model) || undefined,
        reasoningEffort: (runtime && runtime.reasoningEffort) || "",
      }).then(function (store) {
        if (runtime && runtime.clearAttachments) runtime.clearAttachments();
        return store;
      }));
    },

    // The acceptance-failure action is intentionally the same in-session repair
    // path; the separate label makes the intent clearer than the old reflection
    // action while keeping the repair evidence hand-off identical.
    repairProblem: function () { return ctrl.continueModify(); },

    // Skip the failed step (or the first unresolved one) — only that step, not
    // the whole plan. Continue if work remains, else go to review.
    skipStep: function () {
      var plan = Array.isArray(session.plan) ? session.plan : [];
      var idx = -1;
      for (var i = 0; i < plan.length; i++) {
        if (plan[i] && plan[i].status === "failed") { idx = i; break; }
      }
      if (idx < 0) {
        for (var j = 0; j < plan.length; j++) {
          if (!isResolvedStepStatus(plan[j] && plan[j].status)) { idx = j; break; }
        }
      }
      var skippedStepId = idx >= 0 && plan[idx] ? plan[idx].id : "";
      var skipped = skippedStepId ? model.markStepById(plan, skippedStepId, "skipped", "已跳过该步骤。") : plan;
      var remaining = skipped.filter(function (s) { return !isResolvedStepStatus(s && s.status); }).length;
      var runnable = model.findNextRunnableStep(skipped);
      var events = model.withEvent(session, "StepSkipped", "跳过该步骤。");
      return run(patch({
        status: remaining > 0 ? (runnable ? "paused" : "blocked") : "review",
        plan: skipped,
        agentReply: remaining > 0
          ? (runnable ? "已跳过该步骤，可继续执行不依赖它的剩余步骤。" : "该步骤已跳过，其后续依赖步骤已被阻塞。")
          : "已跳过该步骤，剩余步骤已处理完，请验收。",
        events: events,
      }));
    },

    markComplete: function () {
      // Confirm the still-unverified criteria as passed, but respect any the user
      // explicitly marked 未通过 — don't silently flip them green.
      var items = Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
      var passed = items.map(function (a) {
        return (a && a.status === "failed") ? a : Object.assign({}, a, { status: "passed" });
      });
      var events = model.withEvent(session, "TaskCompleted", "用户确认任务完成。");
      return run(patch({ status: "completed", acceptanceCriteria: passed, events: events }));
    },

    // Deep reflection over the task's accumulated history → session.reflection.
    reflect: function (focus) {
      return runAgentic({ kind: "reflect", label: "正在深度反思整个任务…" }, model.reflect(sid, { focus: focus || "" }));
    },
    // Independent acceptance agent verifies criteria against the real results.
    verify: function () {
      return runAgentic({ kind: "verify", label: "正在独立核验验收标准…" }, model.verify(sid));
    },
    // Reflect on a failed task, then fork a fresh session carrying the packet.
    reflectAndFork: function () {
      return runAgentic({ kind: "reflect", label: "正在反思并另起新任务…" }, model.reflectAndFork(sid));
    },
    // Accept a sibling-reflection hint → merge its packet into this session.
    acceptHint: function (hintId) {
      return run(model.acceptHint(sid, hintId));
    },
    // Dismiss a sibling-reflection hint (no change to this session).
    dismissHint: function (hintId) {
      return run(model.dismissHint(sid, hintId));
    },

    reopen: function () {
      var events = model.withEvent(session, "Reopened", "重新打开任务。");
      return run(patch({ status: "planning", agentReply: "任务已重新打开，请确认计划后继续。", events: events }));
    },

    cancel: function () {
      return workbenchServices.feedback().confirmModal({ body: "确定取消这个任务吗？当前进度会被保留。", danger: true }).then(function (ok) {
        if (!ok) return undefined;
        if (session.goalLoop && ["running", "waiting_for_user", "paused", "blocked"].indexOf(session.goalLoop.status) >= 0) {
          return run(model.cancelGoalLoop(sid));
        }
        return run(patch({ status: "cancelled", events: model.withEvent(session, "Cancelled", "任务已取消。") }));
      });
    },

    createFollowUp: function (input) {
      var options = (input && typeof input === "object") ? input : {};
      return run(model.createFollowUp(sid, options));
    },
  };
  return ctrl;
}


export {
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
}
