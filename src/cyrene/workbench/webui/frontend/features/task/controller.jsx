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
    wbT("task.prompt.executeStep", "Create a subagent to execute this step in the current task plan, then summarize the result when it finishes."),
    wbT("task.prompt.currentTask", "Current task: {task}", { task: String((session && (session.goal || session.title)) || "").trim() }),
    wbT("task.prompt.step", "Step: {step}", { step: String((step && step.title) || "").trim() }),
  ];
  if (step && step.description) lines.push(wbT("task.prompt.stepDescription", "Step description: {description}", { description: String(step.description).trim() }));
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
      base += "\n\n" + wbT("task.prompt.workspaceFiles", "Pay particular attention to these workspace files (read them with read_file or equivalent tools before making changes):") + "\n" + rows.join("\n");
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
  if (isRunningStepStatus(step.status)) return wbT("status.running", "Running");
  if (isDoneStepStatus(step.status)) return wbT("status.done", "Done");
  if (step.status === "failed") return wbT("status.failed", "Needs attention");
  if (step.status === "paused") return wbT("status.paused", "Paused");
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
      events: model.withEvent(current, "PlanApproved", wbT("task.event.planVersionApproved", "The user approved the current version of the plan.")),
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
      agentReply: wbT("task.reply.stepFailed", "Step failed: {error}", { error: msg }),
      events: model.withEvent(baseSession, "ExecutionFailed", wbT("task.event.stepFailed", "Step \"{step}\" failed: {error}", { step: stepTitle, error: msg }), { stepId: stepId || "" }),
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
    var stepTitle = String(step.title || wbT("task.step.numbered", "Step {number}", { number: index + 1 })).trim();
    var startPlan = model.markStepById(basePlan, stepId, "running", wbT("task.progress.startingSubagent", "Starting the subagent and waiting for model reasoning…"));
    var startEvents = model.withEvent(baseSession, "ExecutionStarted", wbT("task.event.stepStarted", "Started step: {step}", { step: stepTitle }), { stepId: step.id || "" });
    return model.patchSession(sid, { status: "running", plan: startPlan, agentReply: wbT("task.reply.executingStep", "Executing step: {step}", { step: stepTitle }), events: startEvents })
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
          throw new Error(wbT("task.error.stepCompletionNotSaved", "The server could not save the completed step state. Refresh and try again."));
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
      return runAgentic({ kind: "dispatch", label: wbT("task.busy.understanding", "Understanding your request…") }, model.dispatch(sid, input, {
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
          agentReply: wbT("task.reply.fallbackPlanEditable", "The processing service is temporarily unavailable. I created a basic plan that you can edit and run step by step, or you can try again later."),
          events: model.withEvent(session, "PlanGenerated", wbT("task.event.fallbackPlanGenerated", "Generated a fallback execution plan.")),
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
      setAgentBusy({ kind: "answer", label: wbT("task.busy.continuing", "Continuing…"), startedAt: new Date().toISOString() });
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
      return runAgentic({ kind: "plan", label: wbT("task.busy.organizingPlan", "Turning it into an execution plan…") }, model.generatePlan(sid, goal, { basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        return patch({ status: "planning", goal: goal, plan: model.buildPlanSteps(goal, session.constraints || []), acceptanceCriteria: model.buildAcceptance(goal, session.constraints || []), agentReply: wbT("task.reply.fallbackPlan", "The planning service is temporarily unavailable, so a basic plan was created."), events: model.withEvent(session, "PlanGenerated", wbT("task.event.fallbackPlanGenerated", "Generated a fallback execution plan.")) });
      }));
    },

    // idle → planning. Generate a REAL plan from the goal — the agent explores
    // the project workspace server-side ("执行前必须有计划"); no agent work runs
    // yet. On failure, fall back to an honest client-side template (all pending).
    start: function (goalText) {
      var goal = (goalText != null ? String(goalText) : (session.goal || "")).trim();
      if (!goal) return Promise.resolve();
      return runAgentic({ kind: "plan", label: wbT("task.busy.generatingPlan", "Analyzing the task and generating an execution plan…") }, model.generatePlan(sid, goal, { basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        var constraints = session.constraints || [];
        return patch({
          status: "planning",
          goal: goal,
          plan: model.buildPlanSteps(goal, constraints),
          acceptanceCriteria: model.buildAcceptance(goal, constraints),
          agentReply: wbT("task.reply.fallbackPlanEditable", "The processing service is temporarily unavailable. I created a basic plan that you can edit and run step by step, or you can try again later."),
          events: model.withEvent(session, "PlanGenerated", wbT("task.event.fallbackPlanGenerated", "Generated a fallback execution plan.")),
        });
      }));
    },

    // Empty task (no real goal) → 「直接开始」. The agent reads the project
    // workspace + notes server-side and proposes a plan to kick things off, so the
    // user doesn't have to phrase a goal first. Same path as start(), but seeded
    // with a project-derived default goal (passed in from the card so it follows
    // the UI language).
    autoStart: function () {
      return runAgentic({ kind: "plan", label: wbT("task.busy.readingProject", "Reading the project and planning…") }, model.generatePlan(sid, "", { autoStart: true, basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        var basis = (session.goal || "").trim() || wbT("task.defaultGoal", "Advance the most important current work in this project");
        return patch({
          status: "planning",
          plan: model.buildPlanSteps(basis, session.constraints || []),
          acceptanceCriteria: model.buildAcceptance(basis, session.constraints || []),
          agentReply: wbT("task.reply.fallbackPlanEditable", "The processing service is temporarily unavailable. I created a basic plan that you can edit and run step by step, or you can try again later."),
          events: model.withEvent(session, "PlanGenerated", wbT("task.event.autoFallbackPlanGenerated", "Automatically generated a fallback execution plan.")),
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
          agentReply: wbT("task.reply.guidanceRecorded", "Your guidance was recorded:\n{text}\n(The task is already running, so the plan was not reset. Choose Regenerate if you need to reorder it.)", { text: text }),
          events: model.withEvent(session, "PlanRevised", wbT("task.event.guidanceDuringRun", "Guidance added during execution: {text}", { text: text })),
        }));
      }
      return runAgentic({ kind: "plan", label: wbT("task.busy.replanningWithGuidance", "Replanning with your guidance…") }, model.generatePlan(sid, goal, { feedback: text, operation: "auto", basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        var keepPlan = Array.isArray(session.plan) && session.plan.length ? session.plan : model.buildPlanSteps(goal, session.constraints || []);
        var keepAcceptance = Array.isArray(session.acceptanceCriteria) && session.acceptanceCriteria.length
          ? session.acceptanceCriteria
          : model.buildAcceptance(goal, session.constraints || []);
        return patch({
          status: "planning",
          plan: keepPlan,
          acceptanceCriteria: keepAcceptance,
          agentReply: wbT("task.reply.planServiceUnavailableGuidance", "The planning service is temporarily unavailable. The original plan was kept and your adjustment was recorded:\n{text}", { text: text }),
          events: model.withEvent(session, "PlanRevised", wbT("task.event.planAdjusted", "Plan adjustment requested by the user: {text}", { text: text })),
        });
      }));
    },

    regeneratePlan: function () {
      var goal = (session.goal || "").trim();
      return runAgentic({ kind: "plan", label: wbT("task.busy.regeneratingPlan", "Regenerating the execution plan…") }, model.generatePlan(sid, goal, {
        feedback: wbT("task.prompt.regeneratePlan", "Create a completely new execution plan from the current task goal without retaining any steps from the old plan."),
        operation: "replace",
        basePlanRevision: Number(session.planRevision || 0),
      }).catch(function (err) {
        rethrowPlanConflict(err);
        return patch({
          status: "planning",
          plan: Array.isArray(session.plan) ? session.plan : [],
          acceptanceCriteria: Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [],
          agentReply: wbT("task.reply.regenerateFailed", "The plan could not be regenerated. The original plan is unchanged; try again later."),
          events: model.withEvent(session, "PlanGenerated", wbT("task.event.regenerateFailed", "Plan regeneration failed; kept the original plan.")),
        });
      }));
    },

    // planning → waiting_for_approval — the 需要你确认 gate before any change.
    approvePlan: function () {
      if (!requirePlan(session)) return Promise.resolve();
      var events = model.withEvent(session, "PlanApproved", wbT("task.event.planApproved", "The user approved the execution plan."));
      return run(patch({
        status: "waiting_for_approval",
        approvedPlanDefinitionRevision: Number(session.planDefinitionRevision || 0),
        agentReply: wbT("task.reply.confirmActions", "Confirm the actions below before execution."),
        events: events,
      }));
    },

    // planning → 跳过单独确认，直接连续执行全部步骤。
    approveAndRunAll: function () {
      if (!requirePlan(session)) return Promise.resolve();
      return model.patchSession(sid, {
        approvedPlanDefinitionRevision: Number(session.planDefinitionRevision || 0),
        events: model.withEvent(session, "PlanApproved", wbT("task.event.planApprovedRunAll", "The user approved the plan and chose to run all steps continuously.")),
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
      var events = model.withEvent(session, "ActionRejected", wbT("task.event.actionRejected", "The user rejected the current action."));
      return run(patch({ status: "planning", agentReply: wbT("task.reply.actionCancelled", "The action was cancelled. You can revise the request or ask me to replan."), events: events }));
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
              agentReply: wbT("task.reply.allStepsComplete", "All steps are complete. Please review the result."),
              events: model.withEvent(approvedSession, "ExecutionFinished", wbT("task.event.allStepsComplete", "All steps are complete and awaiting your review.")),
            }));
          }
          return run(model.patchSession(sid, {
            status: "blocked",
            agentReply: wbT("task.reply.noRunnableSteps", "No steps can run. Complete or adjust the prerequisites of the blocked steps first."),
            events: model.withEvent(approvedSession, "ExecutionBlocked", wbT("task.event.dependenciesBlocked", "Step dependencies are not satisfied, so the task is blocked.")),
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
        var startedEvents = model.withEvent(approvedSession, "ExecutionStarted", wbT("task.event.runAllStarted", "Started continuous execution of all remaining steps."));
        return model.patchSession(sid, { status: "running", agentReply: wbT("task.reply.runningAll", "Running all remaining steps in dependency order."), events: startedEvents });
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
                  agentReply: wbT("task.reply.noRunnableSteps", "No steps can run. Complete or adjust the prerequisites of the blocked steps first."),
                  events: model.withEvent(currentSession, "ExecutionBlocked", wbT("task.event.runAllDependenciesBlocked", "Step dependencies are not satisfied, so continuous execution stopped.")),
                }).then(apply);
              }
              return model.patchSession(sid, {
                status: "review",
                agentReply: wbT("task.reply.allStepsComplete", "All steps are complete. Please review the result."),
                artifacts: model.ensureArtifacts(currentSession),
                events: model.withEvent(currentSession, "ExecutionFinished", wbT("task.event.allStepsComplete", "All steps are complete and awaiting your review.")),
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
        return Object.assign({}, s, { status: "pending", startedAt: null, currentAction: wbT("task.progress.stopped", "Stopped; ready to run again."), updatedAt: now });
      }) : session.plan;
      return model.patchSession(sid, {
        status: "paused",
        plan: stoppedPlan,
        agentReply: wbT("task.reply.interrupted", "You interrupted execution. Continue now or adjust the task before retrying."),
        events: model.withEvent(session, "Paused", wbT("task.event.interrupted", "The user interrupted execution.")),
      }).then(apply).catch(fail);
    },

    pause: function () {
      return run(patch({ status: "paused", events: model.withEvent(session, "Paused", wbT("task.event.paused", "The task was paused.")) }));
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
      return model.patchSession(sid, { events: model.withEvent(session, "Resumed", wbT("task.event.resumed", "Task execution resumed.")) })
        .then(apply).then(function () { return ctrl.execute(); });
    },

    retry: function () { return ctrl.execute(); },

    // After independent acceptance fails, repair the current task in-place. The
    // server receives the explicit repair command and injects the latest failed
    // criteria/evidence into the same session's agent context.
    continueModify: function () {
      var criteria = Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
      var failed = criteria.filter(function (item) { return item && item.status === "failed"; });
      var lines = [wbT("task.prompt.repairFromReview", "Use the latest review results to continue modifying and complete the current session task. Preserve the acceptance criteria that passed and prioritize the failed items.")];
      failed.slice(0, 8).forEach(function (item) {
        var text = String(item.text || "").trim();
        var evidence = String(item.evidence || "").trim();
        if (text) lines.push(wbT("task.prompt.failedCriterion", "- Failed: {criterion}{evidence}", {
          criterion: text,
          evidence: evidence ? wbT("task.prompt.acceptanceEvidence", "; evidence: {evidence}", { evidence: evidence }) : "",
        }));
      });
      if (session.verifyReason) lines.push(wbT("task.prompt.reviewConclusion", "Review conclusion: {conclusion}", { conclusion: String(session.verifyReason) }));
      return runAgentic({ kind: "repair", label: wbT("task.busy.repairingFromReview", "Continuing the work from the review results…") }, model.continueAcceptanceRepair(sid, lines.join("\n"), {
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
      var skipped = skippedStepId ? model.markStepById(plan, skippedStepId, "skipped", wbT("task.progress.stepSkipped", "This step was skipped.")) : plan;
      var remaining = skipped.filter(function (s) { return !isResolvedStepStatus(s && s.status); }).length;
      var runnable = model.findNextRunnableStep(skipped);
      var events = model.withEvent(session, "StepSkipped", wbT("task.event.stepSkipped", "Skipped the step."));
      return run(patch({
        status: remaining > 0 ? (runnable ? "paused" : "blocked") : "review",
        plan: skipped,
        agentReply: remaining > 0
          ? (runnable
            ? wbT("task.reply.stepSkippedContinue", "The step was skipped. You can continue with the remaining steps that do not depend on it.")
            : wbT("task.reply.stepSkippedBlocked", "The step was skipped, and its dependent steps are now blocked."))
          : wbT("task.reply.stepSkippedReview", "The step was skipped and all remaining steps are resolved. Please review the result."),
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
      var events = model.withEvent(session, "TaskCompleted", wbT("task.event.completedByUser", "The user confirmed that the task is complete."));
      return run(patch({ status: "completed", acceptanceCriteria: passed, events: events }));
    },

    // Deep reflection over the task's accumulated history → session.reflection.
    reflect: function (focus) {
      return runAgentic({ kind: "reflect", label: wbT("task.busy.reflecting", "Reflecting deeply on the entire task…") }, model.reflect(sid, { focus: focus || "" }));
    },
    // Independent acceptance agent verifies criteria against the real results.
    verify: function () {
      return runAgentic({ kind: "verify", label: wbT("task.busy.verifying", "Independently verifying the acceptance criteria…") }, model.verify(sid));
    },
    // Reflect on a failed task, then fork a fresh session carrying the packet.
    reflectAndFork: function () {
      return runAgentic({ kind: "reflect", label: wbT("task.busy.reflectingAndForking", "Reflecting and creating a new task…") }, model.reflectAndFork(sid));
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
      var events = model.withEvent(session, "Reopened", wbT("task.event.reopened", "Reopened the task."));
      return run(patch({ status: "planning", agentReply: wbT("task.reply.reopened", "The task was reopened. Confirm the plan to continue."), events: events }));
    },

    cancel: function () {
      return workbenchServices.feedback().confirmModal({ body: wbT("task.confirm.cancel", "Cancel this task? Its current progress will be preserved."), danger: true }).then(function (ok) {
        if (!ok) return undefined;
        if (session.goalLoop && ["running", "waiting_for_user", "paused", "blocked"].indexOf(session.goalLoop.status) >= 0) {
          return run(model.cancelGoalLoop(sid));
        }
        return run(patch({ status: "cancelled", events: model.withEvent(session, "Cancelled", wbT("task.event.cancelled", "The task was cancelled.")) }));
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
