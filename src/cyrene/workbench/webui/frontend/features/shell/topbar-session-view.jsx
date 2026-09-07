import { workbenchServices } from "../../shared/runtime/services.jsx"

function wbSessionStatusLabel(activity, t) {
  var state = activity || {};
  if (state.phase === "attention") {
    return {
      input: t("workbench.sessionStatus.needsInput", "Needs input"),
      approval: t("workbench.sessionStatus.needsApproval", "Needs approval"),
      review: t("workbench.sessionStatus.needsReview", "Needs review"),
      blocked: t("workbench.sessionStatus.blocked", "Blocked"),
    }[state.reason] || t("workbench.sessionStatus.needsAttention", "Needs attention");
  }
  return {
    idle: t("workbench.sessionStatus.idle", "Idle"),
    planning: state.isLive
      ? t("workbench.sessionStatus.planning", "Planning")
      : t("workbench.sessionStatus.planningStage", "Planning stage"),
    running: t("workbench.sessionStatus.running", "Running"),
    paused: t("workbench.sessionStatus.paused", "Paused"),
    cancelled: t("workbench.sessionStatus.cancelled", "Stopped"),
    completed: t("workbench.sessionStatus.completed", "Completed"),
    failed: t("workbench.sessionStatus.failed", "Failed"),
  }[state.phase] || t("workbench.sessionStatus.idle", "Idle");
}

function wbSessionActivityCopy(activity, t) {
  var state = activity || {};
  if (state.phase === "attention" || state.phase === "failed" || state.phase === "paused" || state.phase === "cancelled" || state.phase === "completed") {
    return wbSessionStatusLabel(state, t);
  }
  if (state.phase === "planning") return wbSessionStatusLabel(state, t);
  if (state.phase === "running") {
    if (state.activity && state.activity.kind === "browser" && state.activity.label) return state.activity.label;
    if (state.progress && state.progress.current && state.progress.total) {
      return t("workbench.sessionStatus.step", {
        current: state.progress.current,
        total: state.progress.total,
      }, "Step {current}/{total}");
    }
    if (state.activity && state.activity.label) return state.activity.label;
  }
  return "";
}

function WorkbenchSessionStatusIcon({ phase, active }) {
  var state = String(phase || "idle");
  if (state === "attention") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M8 2 14 13H2Z"/><path d="M8 5.5v3.4M8 11.3h.01"/></svg>;
  }
  if (state === "completed") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m3.2 8.2 3 3L12.8 4.8"/></svg>;
  }
  if (state === "failed") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><circle cx="8" cy="8" r="5.6"/><path d="m6 6 4 4m0-4-4 4"/></svg>;
  }
  if (state === "cancelled") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><circle cx="8" cy="8" r="5.6"/><path d="M5.7 8h4.6"/></svg>;
  }
  if (state === "paused") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M5.7 4.5v7M10.3 4.5v7"/></svg>;
  }
  return <span className={"workbench-session-status-dot " + state + (active ? " is-live" : "")} />;
}

function WorkbenchAssetIcon({ name, className }) {
  var assets = window.CyreneIconAssets;
  var markup = assets && assets.settings && assets.settings[name] || "";
  if (!markup) return null;
  return <span className={className || "workbench-asset-icon"} dangerouslySetInnerHTML={{ __html: markup }} aria-hidden="true" />;
}

function WorkbenchSessionActivityPreview({ preview, t }) {
  if (!preview) return null;
  var item = preview.item;
  var activity = preview.activity || {};
  var progress = activity.progress || {};
  var activeAgents = (activity.agents || []).filter(function (agent) {
    return ["running", "resumed", "waiting"].indexOf(String(agent.status || "")) >= 0;
  });
  var percent = progress.total ? Math.max(0, Math.min(100, Math.round((progress.completed / progress.total) * 100))) : 0;
  return (
    <div
      id="workbench-session-activity-preview"
      className="workbench-session-activity-preview"
      role="tooltip"
      style={{ left: preview.left, top: preview.top, ...preview.portalTheme }}
    >
      <div className="workbench-session-activity-preview-head">
        <WorkbenchSessionStatusIcon phase={activity.phase} active={activity.isLive} />
        <div><b>{item.title}</b><small>{wbSessionStatusLabel(activity, t)}</small></div>
      </div>
      {progress.total ? (
        <div className="workbench-session-activity-progress">
          <div><span>{t("workbench.sessionStatus.progress", "Progress")}</span><b>{progress.current || progress.completed}/{progress.total}</b></div>
          <span className="workbench-session-activity-progress-track"><i style={{ width: percent + "%" }} /></span>
          {progress.title || progress.action ? <p>{progress.action || progress.title}</p> : null}
        </div>
      ) : null}
      {activity.activity && (activity.activity.label || activity.activity.detail) ? (
        <div className="workbench-session-activity-current">
          <span>{activity.activity.kind === "browser" ? t("workbench.sessionStatus.browsing", "Browsing") : t("workbench.sessionStatus.currentActivity", "Current activity")}</span>
          <b>{activity.activity.label || activity.activity.detail}</b>
          {activity.activity.label && activity.activity.detail ? <small>{activity.activity.detail}</small> : null}
        </div>
      ) : null}
      {activeAgents.length ? <div className="workbench-session-activity-agents">{t("workbench.sessionStatus.agentsRunning", { count: activeAgents.length }, "{count} agents active")}</div> : null}
    </div>
  );
}


export { wbSessionStatusLabel, wbSessionActivityCopy, WorkbenchSessionStatusIcon, WorkbenchAssetIcon, WorkbenchSessionActivityPreview };
