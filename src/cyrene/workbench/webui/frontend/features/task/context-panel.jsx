import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WbColResizer } from "../layout/right-panel-resizer.jsx"
import { ICONS, hasAcceptanceFailure, priorityText, wbRealGoal, wbRenderMarkdown, wbT } from "./presentation.jsx"

var { useEffect: useWorkbenchEffect, useRef: useWorkbenchRef, useState: useWorkbenchState } = React;
var WorkbenchModel = workbenchServices.model();

function RightContextPanel({ project, session, expandedStepId, tab, onTabChange, onRefresh, onToggleSide, floating, className }) {
  var activeBodyRef = useWorkbenchRef(null);
  var steps = session && Array.isArray(session.plan) ? session.plan : [];
  var artifacts = WorkbenchModel.ensureArtifacts(session);
  var activeStep = steps.find(function (step) { return step.id === expandedStepId; }) || null;
  var isInit = !!(session && session.kind === "init");
  var tabs = isInit ? [
    { id: "context", label: wbT("init.progress.title", "Initialization progress") },
  ] : [
    { id: "context", label: wbT("task.side.context", "Context") },
    { id: "files", label: wbT("task.side.fileChanges", "File changes") },
    { id: "logs", label: wbT("task.side.runLogs", "Run logs") },
    { id: "acceptance", label: wbT("task.side.acceptance", "Acceptance") },
  ].concat(artifacts.length ? [{ id: "artifacts", label: wbT("workbenchChat.artifacts", "Artifacts") }] : []);
  useWorkbenchEffect(function () {
    if (activeBodyRef.current) activeBodyRef.current.scrollTop = 0;
  }, [tab, session && session.id]);
  useWorkbenchEffect(function () {
    if (tab === "artifacts" && !artifacts.length) onTabChange("acceptance");
  }, [tab, artifacts.length]);
  if (!session) {
    return (
      <aside className={"workbench-right-panel wb-floating-detail-shell wb-task-detail-shell" + (floating ? " wbc-side-floating" : "") + (className ? " " + className : "")}>
        <div className="wbc-side-card wb-floating-detail-card wb-task-detail-card empty">
          {!floating && <WbColResizer trackGutter surfaceId="task-detail-empty" />}
          {onToggleSide ? (
            <div className="wbc-side-card-head">
              <strong>{wbT("task.side.detailPanel", "Task panel")}</strong>
              <button type="button" className="wbc-side-hide-btn wb-task-side-hide-btn" onClick={onToggleSide} aria-label={wbT("workbenchChat.hideSidebar", "Hide side panel")}>
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m13 7 5 5-5 5M6 7l5 5-5 5"/></svg>
              </button>
            </div>
          ) : null}
          <div className="wb-detail-empty-state">
            {ICONS.target}
            <p>{wbT("task.noTaskSelected", "Select a task.")}</p>
          </div>
        </div>
      </aside>
    );
  }
  var tabIcons = {
    context: isInit ? ICONS.check : ICONS.target,
    files: ICONS.attach,
    logs: ICONS.cmdReflect,
    acceptance: ICONS.check,
    artifacts: ICONS.cmdCode,
  };
  function tabBody(id) {
    if (id === "context") return <ContextTab project={project} session={session} activeStep={activeStep} />;
    if (id === "files") return <FilesTab session={session} activeStep={activeStep} />;
    if (id === "logs") return <LogsTab session={session} />;
    if (id === "acceptance") return <AcceptanceTab session={session} onRefresh={onRefresh} />;
    if (id === "artifacts") return <ArtifactsTab session={session} />;
    return null;
  }
  return (
    <aside className={"workbench-right-panel wb-floating-detail-shell wb-task-detail-shell" + (isInit ? " wb-task-detail-init" : "") + (floating ? " wbc-side-floating is-floating" : "") + (className ? " " + className : "")} aria-label={wbT("task.side.detailPanel", "Task panel")}>
      <div className="wbc-side-card wb-floating-detail-card wb-task-detail-card">
        {!floating && <WbColResizer trackGutter surfaceId="task-detail" />}
        <div className="wbc-side-card-head">
          <strong>{wbT("task.side.detailPanel", "Task panel")}</strong>
          {onToggleSide ? (
            <button
              type="button"
              className={"wbc-side-hide-btn wb-task-side-hide-btn" + (floating ? " wbc-side-floating-close" : "")}
              onClick={onToggleSide}
              title={floating ? wbT("common.close", "Close") : wbT("workbenchChat.hideSidebar", "Hide side panel")}
              aria-label={floating ? wbT("common.close", "Close") : wbT("workbenchChat.hideSidebar", "Hide side panel")}
            >
              {floating ? ICONS.x : <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m13 7 5 5-5 5M6 7l5 5-5 5"/></svg>}
            </button>
          ) : null}
        </div>
        <nav className="wbc-side-accordion wb-task-detail-tabs" aria-label={wbT("task.side.detailPanel", "Task panel")}>
          {tabs.map(function (item) {
            var expanded = tab === item.id;
            var panelId = "wb-task-detail-panel-" + item.id;
            return (
              <section key={item.id} className={"wbc-side-accordion-item" + (expanded ? " expanded" : "")}>
                <button
                  type="button"
                  className={"wbc-side-accordion-trigger wb-task-detail-tab" + (expanded ? " active" : "")}
                  aria-expanded={expanded}
                  aria-controls={panelId}
                  onClick={function () { onTabChange(expanded ? "" : item.id); }}
                >
                  <span className="wbc-side-accordion-icon wb-task-detail-tab-icon" aria-hidden="true">{tabIcons[item.id]}</span>
                  <span className="wbc-side-accordion-label">{item.label}</span>
                  <span className="wbc-side-accordion-chevron" aria-hidden="true">{ICONS.chevronRight}</span>
                </button>
                <div
                  id={panelId}
                  className={"wbc-side-collapse wb-task-detail-tab-panel" + (expanded ? " open" : "")}
                  aria-hidden={!expanded}
                >
                  <div className="wbc-side-collapse-inner">
                    <div ref={expanded ? activeBodyRef : null} className="wbc-side-body workbench-right-body">{tabBody(item.id)}</div>
                  </div>
                </div>
              </section>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}

// Renders the latest deep-reflection packet attached to a task session.
function ReflectionSection({ session }) {
  var reflection = session && session.reflection;
  var packet = reflection && reflection.packet;
  if (!packet || typeof packet !== "object") return null;
  function bullets(items) {
    var arr = Array.isArray(items) ? items.filter(Boolean) : [];
    if (!arr.length) return null;
    return <ul className="wb-bullet">{arr.map(function (x, i) { return <li key={i}>{String(x)}</li>; })}</ul>;
  }
  return (
    <SideSection title={wbT("task.reflection.title", "Deep reflection")} className="wb-task-context-reflection">
      {packet.goal_gap && <div className="wb-brief-row"><label>{wbT("task.reflection.goalGap", "Goal gap")}</label><p>{String(packet.goal_gap)}</p></div>}
      {Array.isArray(packet.excluded_paths) && packet.excluded_paths.length > 0 && (
        <div className="wb-brief-row"><label>{wbT("task.reflection.excludedPaths", "Avoid")}</label>{bullets(packet.excluded_paths)}</div>
      )}
      {Array.isArray(packet.promising_directions) && packet.promising_directions.length > 0 && (
        <div className="wb-brief-row"><label>{wbT("task.reflection.promisingDirections", "Promising directions")}</label>{bullets(packet.promising_directions)}</div>
      )}
      {packet.next_step && <div className="wb-brief-row"><label>{wbT("task.reflection.nextStep", "Next step")}</label><p>{String(packet.next_step)}</p></div>}
      {Array.isArray(packet.open_questions) && packet.open_questions.length > 0 && (
        <div className="wb-brief-row"><label>{wbT("task.reflection.openQuestions", "Open questions")}</label>{bullets(packet.open_questions)}</div>
      )}
    </SideSection>
  );
}

function ContextTab({ project, session, activeStep }) {
  var constraints = (session && session.constraints) || [];
  var plan = session && Array.isArray(session.plan) ? session.plan : [];
  var planById = {};
  plan.forEach(function (step) { if (step && step.id) planById[step.id] = step; });
  var prerequisites = activeStep
    ? (Array.isArray(activeStep.dependsOn) ? activeStep.dependsOn : []).map(function (id) { return planById[id]; }).filter(Boolean)
    : [];
  var dependents = activeStep
    ? plan.filter(function (step) { return step && Array.isArray(step.dependsOn) && step.dependsOn.indexOf(activeStep.id) >= 0; })
    : [];
  var dependencyCount = plan.reduce(function (count, step) {
    return count + (step && Array.isArray(step.dependsOn) ? step.dependsOn.length : 0);
  }, 0);
  var parentSession = project && session && session.parentSessionId
    ? (project.sessions || []).find(function (item) { return item.id === session.parentSessionId; })
    : null;
  var isInit = !!(session && session.kind === "init");
  if (isInit && workbenchServices.create().InitProgress) {
    return (
      <div className="workbench-side-stack wb-init-progress-stack">
        {React.createElement(workbenchServices.create().InitProgress, { session: session })}
      </div>
    );
  }
  return (
    <div className="workbench-side-stack wb-task-context-tab">
      <SideSection title={wbT("task.side.overview", "Task overview")} className="wb-task-context-overview">
        <div className="wb-task-overview-meta">
          <div className="wb-kv"><span>{wbT("workbenchChat.statusLabel", "Status")}</span><b>{WorkbenchModel.statusText(session.status)}</b></div>
          {!isInit && <div className="wb-kv"><span>{wbT("create.task.priority", "Priority")}</span><b>{priorityText(session.priority)}</b></div>}
        </div>
        <p className="wb-task-context-goal">{wbRealGoal(session) || wbT("task.noGoal", "No task goal yet")}</p>
      </SideSection>
      <ReflectionSection session={session} />
      <SideSection title={wbT("task.side.projectContext", "Project context")} className="wb-task-context-project">
        {project && project.context && project.context.summary && !isInit && <div className="wb-agent-body markdown" dangerouslySetInnerHTML={{ __html: wbRenderMarkdown(project.context.summary) }} />}
      </SideSection>
      <SideSection title={wbT("task.side.constraintsCount", "Constraints ({count})", { count: constraints.length })} className="wb-task-context-constraints">
        {constraints.length
          ? constraints.map(function (item, i) { return <div className="workbench-check wb-constraint-row" key={i}><span className="workbench-status-dot amber"></span><span className="wb-constraint-text">{item}</span></div>; })
          : <p className="workbench-muted wb-task-context-empty">{wbT("task.noConstraints", "No constraints yet. Explicit scope, prohibitions, compatibility, or other requirements in the task are identified automatically.")}</p>}
      </SideSection>
      {isInit && workbenchServices.create().InitProgress ? (
        <SideSection title={wbT("init.progress.title", "Initialization progress")}>
          {React.createElement(workbenchServices.create().InitProgress, { session: session })}
        </SideSection>
      ) : (
        <SideSection title={wbT("task.side.taskRelations", "Task relations")} className="wb-task-context-relations">
          {parentSession ? (
            <div className="wb-brief-row">
              <label>{wbT("task.followUpSource", "Source task")}</label>
              <p>{parentSession.title || wbT("task.thisTask", "this task")}</p>
            </div>
          ) : (
            <p className="workbench-muted wb-task-context-empty">{wbT("task.noDependencies", "No dependent tasks yet.")}</p>
          )}
        </SideSection>
      )}
      {!isInit && (
        <SideSection title={wbT("task.side.stepDependencies", "Step dependencies")} className="wb-task-context-dependencies">
          {activeStep ? (
            <div className="wb-step-dependency-side">
              <div className="wb-brief-row">
                <label>{wbT("task.plan.selectedStep", "Selected step")}</label>
                <p>{activeStep.title}</p>
              </div>
              <div className="wb-brief-row">
                <label>{wbT("task.plan.prerequisites", "Prerequisites")}</label>
                {prerequisites.length
                  ? <ul className="wb-bullet">{prerequisites.map(function (step) { return <li key={step.id}>{step.title}</li>; })}</ul>
                  : <p className="workbench-muted">{wbT("task.plan.noPrerequisites", "No prerequisite steps.")}</p>}
              </div>
              <div className="wb-brief-row">
                <label>{wbT("task.plan.dependents", "Dependent steps")}</label>
                {dependents.length
                  ? <ul className="wb-bullet">{dependents.map(function (step) { return <li key={step.id}>{step.title}</li>; })}</ul>
                  : <p className="workbench-muted">{wbT("task.plan.noDependents", "No steps depend on this step.")}</p>}
              </div>
            </div>
          ) : (
            <p className="workbench-muted wb-task-context-empty">
              {dependencyCount
                ? wbT("task.plan.dependencySummary", "{count} dependencies. Select a step to inspect them.", { count: dependencyCount })
                : wbT("task.noDependencies", "No dependent tasks yet.")}
            </p>
          )}
        </SideSection>
      )}
    </div>
  );
}

function FilesTab({ session, activeStep }) {
  var files = [];
  var seen = {};
  var [selectedFile, setSelectedFile] = useWorkbenchState(null);
  var [diffState, setDiffState] = useWorkbenchState({ loading: false, diff: "", error: "", path: "" });
  function add(list) {
    (Array.isArray(list) ? list : []).forEach(function (file) {
      if (!file) return;
      var key = String(file.path || file.name || file.id || "").trim();
      if (!key || seen[key]) return;
      seen[key] = true;
      files.push(file);
    });
  }
  if (activeStep && Array.isArray(activeStep.relatedFiles)) add(activeStep.relatedFiles);
  (session && session.plan || []).forEach(function (step) {
    add(step && step.relatedFiles);
  });
  (session && session.runs || []).forEach(function (run) {
    add(run && run.fileChanges);
  });
  (session && session.artifacts || []).forEach(function (artifact) {
    if (artifact.type === "file_change") add([artifact]);
  });
  useWorkbenchEffect(function () {
    setSelectedFile(null);
    setDiffState({ loading: false, diff: "", error: "", path: "" });
  }, [session && session.id]);
  function openDiff(file) {
    var path = String((file && (file.path || file.name)) || "").trim();
    if (!path || !session || !session.id) return;
    var selectedPath = selectedFile && String(selectedFile.path || selectedFile.name || "");
    if (selectedPath === path) {
      setSelectedFile(null);
      setDiffState({ loading: false, diff: "", error: "", path: "" });
      return;
    }
    setSelectedFile(file);
    setDiffState({ loading: true, diff: "", error: "", path: path });
    WorkbenchModel.fetchFileDiff(session.id, path)
      .then(function (data) {
        setDiffState({
          loading: false,
          diff: data.diff || "",
          error: data.has_changes ? "" : wbT("task.files.noDiff", "No displayable diff in the current git worktree."),
          path: data.path || path,
        });
      })
      .catch(function (err) {
        setDiffState({ loading: false, diff: "", error: (err && err.message) || String(err), path: path });
      });
  }
  return (
    <div className="workbench-side-stack wb-task-tab-content">
      {files.length ? files.map(function (file, i) {
        var path = file.path || file.name || "";
        var selected = selectedFile && String(selectedFile.path || selectedFile.name || "") === String(path);
        return (
          <div
            className={"workbench-file-row wb-file-diff-card" + (selected ? " active" : "")}
            key={file.id || file.path || file.name || i}
          >
            <button
              type="button"
              className="wb-file-diff-trigger"
              onClick={function () { openDiff(file); }}
              title={selected ? wbT("task.files.collapseDiff", "Collapse file diff") : wbT("task.files.viewDiff", "View file diff")}
            >
              <span>{path}</span>
              <small>{file.status || file.changeType || file.type || ""}</small>
            </button>
            {selected && (
              <div className="wb-file-diff-inline">
                {diffState.loading ? (
                  <p className="workbench-muted">{wbT("task.files.loadingDiff", "Loading diff...")}</p>
                ) : diffState.error ? (
                  <p className="workbench-muted">{diffState.error}</p>
                ) : workbenchServices.diff().Panel ? (
                  <div className="wb-file-diff-panel">
                    {React.createElement(workbenchServices.diff().Panel, { diff: diffState.diff, mode: "text" })}
                  </div>
                ) : (
                  <pre className="wb-file-diff-fallback">{diffState.diff}</pre>
                )}
              </div>
            )}
          </div>
        );
      }) : <p className="workbench-muted">{wbT("task.files.empty", "No file changes recorded for this task yet.")}</p>}
    </div>
  );
}

function LogsTab({ session }) {
  var events = session && Array.isArray(session.events) ? session.events : [];
  return (
    <div className="workbench-side-stack wb-task-tab-content">
      {events.length ? events.slice().reverse().slice(0, 60).map(function (event, i) {
          if (event.type === "ToolCallEvent") {
            return (
              <div className="workbench-log-row wb-log-tool" key={event.id || i}>
                <time>{WorkbenchModel.formatTime(event.createdAt)}</time>
                <span className="wb-log-tool-name">{event.body || event.tool || "tool"}</span>
                {event.argsPreview ? <small className="wb-log-tool-args">{event.argsPreview}</small> : null}
              </div>
            );
          }
          if (event.type === "LlmCallEvent" || event.type === "SubagentStatusEvent") {
            return (
              <div className="workbench-log-row" key={event.id || i}>
                <time>{WorkbenchModel.formatTime(event.createdAt)}</time>
                <span>{WorkbenchModel.eventLabel(event.type)}</span>
                <div className="wb-agent-body markdown wb-log-body" dangerouslySetInnerHTML={{ __html: wbRenderMarkdown(event.body || "") }} />
              </div>
            );
          }
          var logBody = event.body || (event.stepCount != null ? wbT("task.logs.stepCount", "Steps {count}", { count: event.stepCount }) : "");
          return <div className="workbench-log-row" key={event.id || i}><time>{WorkbenchModel.formatTime(event.createdAt)}</time><span>{WorkbenchModel.eventLabel(event.type)}</span>{logBody && <div className="wb-agent-body markdown wb-log-body" dangerouslySetInnerHTML={{ __html: wbRenderMarkdown(logBody) }} />}</div>;
      }) : <p className="workbench-muted">{wbT("task.logs.empty", "No run logs yet.")}</p>}
    </div>
  );
}

function AcceptanceTab({ session, onRefresh }) {
  var [busy, setBusy] = useWorkbenchState(false);
  var items = session && Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
  var passedCount = items.filter(function (item) { return item.status === "passed" || item.status === "done"; }).length;
  var failedCount = items.filter(function (item) { return item.status === "failed"; }).length;
  var acceptanceFailure = hasAcceptanceFailure(session);
  var [editing, setEditing] = useWorkbenchState(acceptanceFailure);
  var [draft, setDraft] = useWorkbenchState(items.map(function (item) { return String((item && item.text) || ""); }));
  useWorkbenchEffect(function () {
    setDraft(items.map(function (item) { return String((item && item.text) || ""); }));
    if (acceptanceFailure) setEditing(true);
  }, [session && session.id, JSON.stringify(items.map(function (item) { return [item && item.id, item && item.text, item && item.status]; }))]);
  function generate() {
    setBusy(true);
    workbenchServices.model().generateAcceptance(session.id)
      .then(function (next) { onRefresh && onRefresh(next); })
      .catch(function (err) { workbenchServices.feedback().showToast(err.message || String(err), "error"); })
      .finally(function () { setBusy(false); });
  }
  // Verify a criterion by clicking it — cycle 待验证 → 已通过 → 未通过 → 待验证.
  function toggle(id) {
    var nextStatus = { pending: "passed", passed: "failed", failed: "pending", done: "pending" };
    var next = items.map(function (a) {
      return a.id === id ? Object.assign({}, a, { status: nextStatus[a.status] || "passed" }) : a;
    });
    setBusy(true);
    workbenchServices.model().patchSession(session.id, { acceptanceCriteria: next })
      .then(function (n) { onRefresh && onRefresh(n); })
      .catch(function (err) { workbenchServices.feedback().showToast(err.message || String(err), "error"); })
      .finally(function () { setBusy(false); });
  }
  function saveEdits() {
    var next = items.map(function (item, index) {
      var text = String(draft[index] || "").trim();
      var changed = text !== String((item && item.text) || "").trim();
      return Object.assign({}, item, {
        text: text,
        // A changed criterion needs a fresh verification; do not keep stale
        // failed evidence attached to the new wording.
        status: changed ? "pending" : item.status,
        evidence: changed ? "" : item.evidence,
      });
    }).filter(function (item) { return item.text; });
    if (!next.length) {
      workbenchServices.feedback().showToast(wbT("task.acceptance.minimumOne", "Keep at least one acceptance criterion."), "warning");
      return;
    }
    setBusy(true);
    workbenchServices.model().patchSession(session.id, { acceptanceCriteria: next })
      .then(function (n) { setEditing(false); onRefresh && onRefresh(n); })
      .catch(function (err) { workbenchServices.feedback().showToast(err.message || String(err), "error"); })
      .finally(function () { setBusy(false); });
  }
  function cancelEdits() {
    setDraft(items.map(function (item) { return String((item && item.text) || ""); }));
    setEditing(false);
  }
  return (
    <div className="workbench-side-stack wb-task-tab-content wb-acceptance-panel">
        {items.length ? (
          <React.Fragment>
            <div className="wb-acceptance-summary">
              <div className="wb-acceptance-summary-copy">
                <span>{wbT("task.acceptance.progress", "Verification progress")}</span>
                <b>{passedCount}<small> / {items.length}</small></b>
                <p>{failedCount
                  ? wbT("task.acceptance.failedSummary", "{count} criteria need attention", { count: failedCount })
                  : wbT("task.acceptance.progressHint", "Verify each criterion against the result")}</p>
              </div>
              <div className="wb-acceptance-progress" aria-label={wbT("task.acceptance.progress", "Verification progress")}>
                <span style={{ width: ((passedCount / Math.max(items.length, 1)) * 100) + "%" }}></span>
              </div>
            </div>
            {acceptanceFailure && (
              <div className="wb-acceptance-edit-hint">{wbT("task.acceptance.editHint", "Changed criteria return to pending verification; passed criteria remain unchanged.")}</div>
            )}
            <div className="wb-acceptance-list">{items.map(function (item, index) {
              var done = item.status === "passed" || item.status === "done";
              var dot = done ? "green" : item.status === "failed" ? "red" : "muted";
              var label = done ? wbT("task.acceptance.passed", "Passed") : item.status === "failed" ? wbT("task.acceptance.failed", "Failed") : wbT("task.acceptance.pending", "Pending");
              if (editing) {
                return (
                  <div className="wb-accept-edit-row" key={item.id}>
                    <span className={"workbench-status-dot " + dot}></span>
                    <input type="text" autoFocus={index === 0} value={draft[index] || ""} disabled={busy} onChange={function (event) {
                      var value = event.target.value;
                      setDraft(function (current) { var next = current.slice(); next[index] = value; return next; });
                    }} aria-label={wbT("task.acceptance.criterionNumber", "Acceptance criterion {number}", { number: index + 1 })} />
                    <span className={"wb-accept-state " + dot}>{label}</span>
                  </div>
                );
              }
                return (
                  <button type="button" className="workbench-check wb-accept-toggle" key={item.id} disabled={busy} onClick={function () { toggle(item.id); }} title={wbT("task.acceptance.toggleTitle", "Click to verify this acceptance criterion")}>
                  <span className={"workbench-status-dot " + dot}></span>
                  <span className="wb-accept-copy">
                    <span className="wb-accept-text">{item.text}</span>
                    {item.evidence ? <small className="wb-accept-evidence">{wbT("task.acceptance.evidence", "Evidence: {evidence}", { evidence: item.evidence })}</small> : null}
                  </span>
                  <span className={"wb-accept-state " + dot}>{label}</span>
                </button>
              );
            })}</div>
            {editing ? (
              <div className="wb-accept-edit-actions">
                <button type="button" className="wb-btn ghost compact" disabled={busy} onClick={cancelEdits}>{wbT("common.cancel", "Cancel")}</button>
                <button type="button" className="wb-btn primary compact" disabled={busy} onClick={saveEdits}>{wbT("task.acceptance.save", "Save criteria")}</button>
              </div>
            ) : (
              <button type="button" className="wb-btn ghost compact wb-accept-edit-trigger" disabled={busy} onClick={function () { setEditing(true); }}>{wbT("task.acceptance.edit", "Edit criteria")}</button>
            )}
          </React.Fragment>
        ) : (
          <div className="wb-empty-action wb-acceptance-empty">
            <span className="wb-acceptance-empty-icon" aria-hidden="true">{ICONS.check}</span>
            <div>
              <b>{wbT("task.acceptance.empty", "No acceptance criteria yet.")}</b>
              <p>{wbT("task.acceptance.emptyHint", "Generate clear, verifiable criteria from the current task goal.")}</p>
            </div>
            <button type="button" className="wb-btn primary" disabled={busy} onClick={generate}>{busy ? wbT("init.generating", "Generating...") : wbT("task.acceptance.generate", "Ask Agent to generate acceptance criteria")}</button>
          </div>
        )}
    </div>
  );
}

function ArtifactsTab({ session }) {
  var artifacts = WorkbenchModel.ensureArtifacts(session);
  return (
    <div className="wbc-artifact-list wb-task-artifact-list">
      {artifacts.length ? artifacts.map(function (artifact, i) {
        var downloadUrl = "/api/task-sessions/" + encodeURIComponent(session.id) + "/artifacts/" + encodeURIComponent(artifact.id) + "/download";
        var artifactPath = String(artifact.path || "").trim();
        return (
          <a
            className="wbc-artifact-list-row wb-task-artifact-download"
            href={downloadUrl}
            download={artifact.name || true}
            title={wbT("task.artifact.download", "Download {name}", { name: artifact.name || "" })}
            key={artifact.id || i}
          >
            <span className="wbc-artifact-list-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M7 3.75h6.4L18 8.35v11.9H7z"></path>
                <path d="M13.25 3.9v4.7h4.7"></path>
              </svg>
            </span>
            <span className="wbc-artifact-list-copy">
              <b>{artifact.name}</b>
              {artifactPath && artifactPath !== artifact.name ? <small>{artifactPath}</small> : null}
            </span>
            <span className="wbc-artifact-list-chevron" aria-hidden="true">{ICONS.chevronRight}</span>
          </a>
        );
      }) : <p className="workbench-muted wb-task-artifact-empty">{wbT("task.artifacts.empty", "No artifacts generated for this task yet.")}</p>}
    </div>
  );
}

function SideSection({ title, children, className }) {
  return (
    <section className={"workbench-side-section" + (className ? " " + className : "")}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}



export { RightContextPanel }
