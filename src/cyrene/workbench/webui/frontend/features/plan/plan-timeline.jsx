import { workbenchServices } from "../../shared/runtime/services.jsx"

var {
  useEffect,
  useState,
} = React

var PLAN_ICONS = {
  check: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="m5 12.5 4.5 4.5L19 7" /></svg>,
  dots: <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><circle cx="5.5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="18.5" cy="12" r="1.7" /></svg>,
  edit: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>,
  x: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="m7 7 10 10M17 7 7 17" /></svg>,
}

var ICON_CLOCK = (
  <svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ flexShrink: 0 }}>
    <circle cx="8" cy="8" r="6.5" /><path d="M8 5v3.2l1.8 1.8" />
  </svg>
)

var ICON_CHEVRON = (
  <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M5 7l3 3 3-3" />
  </svg>
)

function planT(key, fallback, params) {
  return workbenchServices.i18n().t(key, params, fallback)
}

function isDone(status) {
  return ["done", "completed", "succeeded"].indexOf(String(status || "")) >= 0
}

function isRunning(status) {
  return ["running", "in_progress", "executing"].indexOf(String(status || "")) >= 0
}

function unmetDependencyIds(steps, step) {
  var dependencies = Array.isArray(step && step.dependsOn) ? step.dependsOn : []
  return dependencies.filter(function (dependencyId) {
    var dependency = steps.find(function (candidate) { return String(candidate && candidate.id || "") === String(dependencyId || "") })
    return !dependency || !isDone(dependency.status)
  })
}

function validPlanOrder(steps) {
  var positions = {}
  steps.forEach(function (step, index) { positions[String(step && step.id || "")] = index })
  return steps.every(function (step, index) {
    return (Array.isArray(step && step.dependsOn) ? step.dependsOn : []).every(function (dependencyId) {
      return Object.prototype.hasOwnProperty.call(positions, String(dependencyId || ""))
        && positions[String(dependencyId || "")] < index
    })
  })
}

function formatTime(value) {
  if (!value) return ""
  try {
    return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(new Date(value))
  } catch (_) {
    return ""
  }
}

function durationText(step) {
  var seconds = Number(step && (step.durationSeconds || step.durationSec || step.duration_seconds) || 0)
  if (!seconds && step && step.startedAt && step.completedAt) {
    seconds = Math.max(0, (new Date(step.completedAt).getTime() - new Date(step.startedAt).getTime()) / 1000)
  }
  if (!Number.isFinite(seconds) || seconds <= 0) return ""
  if (seconds < 60) return Math.round(seconds) + "s"
  var minutes = Math.floor(seconds / 60)
  var remainder = Math.round(seconds % 60)
  return remainder ? minutes + "m " + remainder + "s" : minutes + "m"
}

function stepCommand(step, commandForStep) {
  if (typeof step.promptOverride === "string" && step.promptOverride.length) return step.promptOverride
  if (typeof step.command === "string" && step.command.length) return step.command
  return typeof commandForStep === "function" ? String(commandForStep(step) || "") : ""
}

function PlanStepSummary({ step, steps, commandForStep }) {
  var prereqTitles = (Array.isArray(step.dependsOn) ? step.dependsOn : []).map(function (id) {
    var dependency = steps.find(function (candidate) { return String(candidate && candidate.id || "") === String(id || "") })
    return dependency ? dependency.title : id
  })
  var contextFiles = Array.isArray(step.contextFiles) ? step.contextFiles : []
  var command = stepCommand(step, commandForStep)
  return <div className="wbp-summary">
    {step.description ? <div className="wbp-summary-row">
      <span className="wbp-summary-k">{planT("workbench.step.description", "Description")}</span>
      <span className="wbp-summary-v">{step.description}</span>
    </div> : null}
    <div className="wbp-summary-row">
      <span className="wbp-summary-k">{planT("workbench.step.prerequisites", "Prerequisites")}</span>
      <span className="wbp-summary-v">{prereqTitles.length
        ? <span className="wbp-summary-chips">{prereqTitles.map(function (title, index) { return <span key={index}>{title}</span> })}</span>
        : <em className="wbp-summary-none">{planT("common.none", "None")}</em>}</span>
    </div>
    {command ? <div className="wbp-summary-row">
      <span className="wbp-summary-k">{planT("workbench.step.command", "Command")}</span>
      <span className="wbp-summary-v wbp-summary-cmd">{command}</span>
    </div> : null}
    {contextFiles.length ? <div className="wbp-summary-row">
      <span className="wbp-summary-k">{planT("workbench.step.files", "Files")}</span>
      <span className="wbp-summary-v"><span className="wbp-summary-chips">{contextFiles.map(function (file, index) {
        var label = String(file && (file.path || file.name) || "").split("/").pop()
        return <span key={index} className="wbp-summary-file">{label}</span>
      })}</span></span>
    </div> : null}
  </div>
}

function PlanStepFilesEditor({ step, patch, checkWorkspacePath }) {
  var [pathInput, setPathInput] = useState("")
  var [hint, setHint] = useState("")
  var contextFiles = Array.isArray(step.contextFiles) ? step.contextFiles : []

  function addWorkspaceFile() {
    var path = String(pathInput || "").trim()
    if (!path) return
    var validate = typeof checkWorkspacePath === "function" ? checkWorkspacePath(path) : Promise.resolve({ exists: true, path: path })
    setHint("")
    Promise.resolve(validate).then(function (result) {
      if (!result || result.exists === false) {
        setHint(result && result.error || planT("plan.context.fileNotFound", "The file was not found in the workspace."))
        return
      }
      var normalized = result.path || path
      if (contextFiles.some(function (file) { return file && file.path === normalized })) {
        setHint(planT("plan.context.fileAlreadyAdded", "This file has already been added."))
        return
      }
      patch({ contextFiles: contextFiles.concat([{ source: "workspace", path: normalized, name: normalized.split("/").pop() }]) })
      setPathInput("")
    }).catch(function (error) { setHint(error && error.message || String(error)) })
  }

  return <div className="wbp-summary-row"><span className="wbp-summary-k">{planT("workbench.step.files", "Files")}</span><div className="wbp-summary-v">
    {contextFiles.length ? <div className="wbp-ctx-list">{contextFiles.map(function (file, index) {
      var label = String(file && (file.path || file.name) || "").split("/").pop()
      return <span key={(file && (file.path || file.name) || "") + "_" + index} className="wbp-ctx-chip"><span className="wbp-ctx-tag">{planT("plan.context.source.workspace", "Workspace")}</span><span className="wbp-ctx-name">{label}</span><button type="button" className="wbp-ctx-x" onClick={function () { patch({ contextFiles: contextFiles.filter(function (candidate) { return candidate !== file }) }) }} aria-label={planT("plan.context.removeFile", "Remove file")}>{PLAN_ICONS.x}</button></span>
    })}</div> : null}
    <div className="wbp-ctx-add"><div className="wbp-ctx-add-row"><input className="wbp-ctx-input" value={pathInput} placeholder={planT("plan.context.pathPlaceholder", "Workspace-relative path, such as src/app.py")} onChange={function (event) { setPathInput(event.target.value) }} onKeyDown={function (event) { if (event.key === "Enter") { event.preventDefault(); addWorkspaceFile() } }} /><button type="button" className="wbp-tiny-btn" disabled={!pathInput.trim()} onClick={addWorkspaceFile}>{planT("common.add", "Add")}</button></div>
    </div>
    {hint ? <p className="wbp-ctx-hint">{hint}</p> : null}
  </div></div>
}

function PlanStepEditor({ step, steps, controller, commandForStep, checkWorkspacePath }) {
  var [title, setTitle] = useState(step.title || "")
  var [description, setDescription] = useState(step.description || "")
  var [dependsOn, setDependsOn] = useState(Array.isArray(step.dependsOn) ? step.dependsOn : [])
  var [command, setCommand] = useState(stepCommand(step, commandForStep))
  var [saving, setSaving] = useState(false)
  var stepIndex = steps.findIndex(function (candidate) { return String(candidate && candidate.id || "") === String(step.id || "") })
  var dependencyOptions = steps.slice(0, Math.max(0, stepIndex))

  useEffect(function () {
    setTitle(step.title || "")
    setDescription(step.description || "")
    setDependsOn(Array.isArray(step.dependsOn) ? step.dependsOn : [])
    setCommand(stepCommand(step, commandForStep))
  }, [step.id, step.title, step.description, step.command, step.promptOverride, JSON.stringify(step.dependsOn || [])])

  function patch(values) {
    if (!controller || typeof controller.patchStep !== "function") return Promise.resolve(null)
    return Promise.resolve(controller.patchStep(step.id, values))
  }

  function save() {
    var normalizedTitle = String(title || "").trim()
    if (!normalizedTitle || saving) return
    setSaving(true)
    var defaultCommand = typeof commandForStep === "function" ? String(commandForStep(step) || "") : ""
    var commandPatch = String(command || "").trim() === defaultCommand.trim() ? "" : command
    var values = {
      title: normalizedTitle,
      description: String(description || "").trim(),
      dependsOn: dependsOn,
    }
    if (typeof commandForStep === "function") values.promptOverride = commandPatch
    else values.command = String(command || "").trim()
    patch(values).finally(function () { setSaving(false) })
  }

  function remove() {
    workbenchServices.feedback().confirmModal({
      body: planT("plan.confirmDeleteStep", "Delete step \"{name}\"?", { name: step.title }),
      confirmLabel: planT("common.delete", "Delete"),
      danger: true,
    }).then(function (confirmed) {
      if (confirmed && controller && typeof controller.deleteStep === "function") controller.deleteStep(step.id)
    })
  }

  return <div className="wbp-summary wbp-summary-edit">
    <label className="wbp-summary-row"><span className="wbp-summary-k">{planT("workbench.step.title", "Title")}</span><input className="wbp-edit-input" value={title} disabled={saving} onChange={function (event) { setTitle(event.target.value) }} /></label>
    <label className="wbp-summary-row"><span className="wbp-summary-k">{planT("workbench.step.description", "Description")}</span><textarea className="wbp-edit-input" rows={2} value={description} disabled={saving} onChange={function (event) { setDescription(event.target.value) }} /></label>
    <div className="wbp-summary-row"><span className="wbp-summary-k">{planT("workbench.step.prerequisites", "Prerequisites")}</span><div className="wbp-summary-v">
      {dependencyOptions.length ? <div className="wbp-dependency-options">{dependencyOptions.map(function (candidate) {
        var checked = dependsOn.indexOf(candidate.id) >= 0
        return <label key={candidate.id} className={"wbp-dependency-option" + (checked ? " selected" : "")}><input type="checkbox" checked={checked} disabled={saving} onChange={function () { setDependsOn(function (current) { return checked ? current.filter(function (id) { return id !== candidate.id }) : current.concat([candidate.id]) }) }} /><span>{candidate.title}</span></label>
      })}</div> : <em className="wbp-summary-none">{planT("plan.noEarlierSteps", "No earlier steps are available.")}</em>}
    </div></div>
    <div className="wbp-summary-row"><span className="wbp-summary-k">{planT("workbench.step.command", "Command")}</span><textarea className="wbp-edit-input wbp-edit-cmd" rows={5} value={command} spellCheck={false} onChange={function (event) { setCommand(event.target.value) }} /></div>
    <PlanStepFilesEditor step={step} patch={patch} checkWorkspacePath={checkWorkspacePath} />
    <div className="wbp-summary-actions"><button type="button" className="wbp-tiny-btn danger" onClick={remove}>{planT("common.delete", "Delete")}</button><button type="button" className="wb-btn primary compact" disabled={saving || !String(title || "").trim()} onClick={save}>{saving ? planT("common.saving", "Saving...") : planT("common.save", "Save")}</button></div>
  </div>
}

function usePlanTimelineState(props, steps, controller) {
  var [internalExpanded, setInternalExpanded] = useState("")
  var [dragStepId, setDragStepId] = useState("")
  var [dragOverId, setDragOverId] = useState("")
  var [adding, setAdding] = useState(false)
  var [newTitle, setNewTitle] = useState("")
  var [newDescription, setNewDescription] = useState("")
  var [savingNew, setSavingNew] = useState(false)
  var [planEditing, setPlanEditing] = useState(false)
  var expandedStepId = props.expandedStepId === undefined ? internalExpanded : props.expandedStepId
  var busy = Boolean(props.busy || controller && controller.busy)
  var planStarted = steps.some(function (step) { return step && (String(step.status || "pending") !== "pending" || step.startedAt || step.completedAt || Array.isArray(step.progressEvents) && step.progressEvents.length) })
  var canMutate = Boolean(controller && typeof controller.patchStep === "function")
  var canEditStructure = canMutate && !busy && ["running", "waiting_for_user"].indexOf(String(props.hostStatus || "")) < 0
  var canAddReorder = canEditStructure && !planStarted && typeof controller.addStep === "function" && typeof controller.reorderSteps === "function"
  var editing = canEditStructure && planEditing

  useEffect(function () {
    if (!canEditStructure && (planEditing || adding)) { setPlanEditing(false); setAdding(false) }
  }, [canEditStructure])

  function toggleStep(stepId) {
    if (typeof props.onToggleStep === "function") props.onToggleStep(stepId)
    else setInternalExpanded(function (current) { return current === stepId ? "" : stepId })
  }

  function persistOrder(nextSteps) {
    if (!validPlanOrder(nextSteps)) {
      workbenchServices.feedback().showToast(planT("plan.invalidOrder", "This move would place a step before one of its prerequisites."), "warning")
      return
    }
    controller.reorderSteps(nextSteps.map(function (step) { return step.id }))
  }

  function moveStep(sourceId, targetId, after) {
    if (!canAddReorder || !sourceId || !targetId || sourceId === targetId) return
    var next = steps.slice()
    var sourceIndex = next.findIndex(function (step) { return step.id === sourceId })
    if (sourceIndex < 0) return
    var moved = next.splice(sourceIndex, 1)[0]
    var targetIndex = next.findIndex(function (step) { return step.id === targetId })
    if (targetIndex < 0) return
    next.splice(targetIndex + (after ? 1 : 0), 0, moved)
    persistOrder(next)
  }

  function moveBy(stepId, delta) {
    var index = steps.findIndex(function (step) { return step.id === stepId })
    if (index < 0 || !steps[index + delta]) return
    var next = steps.slice()
    var moved = next.splice(index, 1)[0]
    next.splice(index + delta, 0, moved)
    persistOrder(next)
  }

  function addStep() {
    var title = String(newTitle || "").trim()
    if (!title || savingNew || !canAddReorder) return
    setSavingNew(true)
    Promise.resolve(controller.addStep({ title: title, description: String(newDescription || "").trim(), dependsOn: [] })).then(function (result) {
      if (result === false || result === null) return
      setNewTitle("")
      setNewDescription("")
      setAdding(false)
    }).finally(function () { setSavingNew(false) })
  }

  return {
    expandedStepId: expandedStepId,
    busy: busy,
    canEditStructure: canEditStructure,
    canAddReorder: canAddReorder,
    editing: editing,
    dragStepId: dragStepId,
    dragOverId: dragOverId,
    adding: adding,
    newTitle: newTitle,
    newDescription: newDescription,
    savingNew: savingNew,
    planEditing: planEditing,
    setDragStepId: setDragStepId,
    setDragOverId: setDragOverId,
    setAdding: setAdding,
    setNewTitle: setNewTitle,
    setNewDescription: setNewDescription,
    setPlanEditing: setPlanEditing,
    toggleStep: toggleStep,
    moveStep: moveStep,
    moveBy: moveBy,
    addStep: addStep,
  }
}

function PlanTimeline(props) {
  var source = props.plan && typeof props.plan === "object" ? props.plan : {}
  var steps = Array.isArray(source) ? source : Array.isArray(source.steps) ? source.steps : Array.isArray(source.entries) ? source.entries : []
  var controller = props.controller || null
  var timeline = usePlanTimelineState(props, steps, controller)
  var {
    expandedStepId, busy, canEditStructure, canAddReorder, editing,
    dragStepId, dragOverId, adding, newTitle, newDescription, savingNew,
    planEditing, setDragStepId, setDragOverId, setAdding, setNewTitle,
    setNewDescription, setPlanEditing, toggleStep, moveStep, moveBy, addStep,
  } = timeline

  return <section className="workbench-flow wbp wbp-shared-plan">
    <div className="wbp-head"><div><b>{planT("plan.title", "Execution plan")}</b><span>{steps.length}</span></div>
      {canEditStructure ? <div className="wbp-head-actions">{planEditing ? <>{canAddReorder ? <button type="button" className="wb-btn ghost compact" onClick={function () { setAdding(!adding) }}>{adding ? planT("common.cancel", "Cancel") : planT("plan.addStep", "Add step")}</button> : null}<button type="button" className="wb-btn ghost compact" onClick={function () { setPlanEditing(false); setAdding(false) }}>{planT("common.done", "Done")}</button></> : <button type="button" className="wb-btn ghost compact wbp-edit-toggle" onClick={function () { setPlanEditing(true) }}>{PLAN_ICONS.edit}<span>{planT("common.edit", "Edit")}</span></button>}</div> : null}
    </div>
    {adding ? <div className="wbp-add-step"><input autoFocus value={newTitle} placeholder={planT("plan.newStepTitle", "New step title")} onChange={function (event) { setNewTitle(event.target.value) }} onKeyDown={function (event) { if (event.key === "Enter") { event.preventDefault(); addStep() } }} /><textarea rows={2} value={newDescription} placeholder={planT("plan.newStepDescription", "What should this step accomplish?")} onChange={function (event) { setNewDescription(event.target.value) }} /><div><button type="button" className="wb-btn primary" disabled={savingNew || !newTitle.trim()} onClick={addStep}>{savingNew ? planT("common.saving", "Saving...") : planT("plan.addStep", "Add step")}</button></div></div> : null}
    <div className="wbp-list">{steps.map(function (step, index) {
      var expanded = String(expandedStepId || "") === String(step.id || "")
      var doneStep = isDone(step.status)
      var runningStep = isRunning(step.status)
      var failedStep = String(step.status || "") === "failed"
      var skippedStep = String(step.status || "") === "skipped"
      var unmet = unmetDependencyIds(steps, step)
      var blockedStep = !doneStep && !runningStep && !failedStep && !skippedStep && unmet.length > 0
      var state = doneStep ? "done" : runningStep ? "current" : failedStep ? "failed" : skippedStep ? "skipped" : blockedStep ? "blocked" : "idle"
      var statusLabel = doneStep ? planT("status.done", "Done") : runningStep ? planT("status.running", "Running") : failedStep ? planT("status.failed", "Failed") : skippedStep ? planT("status.skipped", "Skipped") : blockedStep ? planT("plan.waitingPrerequisites", "Waiting for prerequisites") : planT("status.pending", "Pending")
      var time = doneStep ? formatTime(step.completedAt || step.updatedAt) : ""
      var duration = doneStep ? durationText(step) : ""
      var estimate = runningStep && step.estimate ? String(step.estimate) : ""
      var isLast = index === steps.length - 1
      var beforeRun = !step.status || step.status === "pending"
      var canReorderStep = canAddReorder && (!Array.isArray(step.dependsOn) || step.dependsOn.length === 0)
      var hasFiles = Array.isArray(step.relatedFiles) && step.relatedFiles.length > 0
      var progressText = step.currentAction || step.note || step.description || ""
      return <div key={step.id || index} className={"wbp-step " + state + (expanded ? " expanded" : "") + (dragStepId === step.id ? " dragging" : "") + (dragOverId === step.id ? " drag-over" : "")} onDragOver={function (event) { if (canAddReorder && dragStepId) { event.preventDefault(); setDragOverId(step.id) } }} onDragLeave={function () { if (dragOverId === step.id) setDragOverId("") }} onDrop={function (event) { event.preventDefault(); var bounds = event.currentTarget.getBoundingClientRect(); moveStep(dragStepId || event.dataTransfer.getData("text/plain"), step.id, event.clientY > bounds.top + bounds.height / 2); setDragStepId(""); setDragOverId("") }}>
        <div className="wbp-rail"><button type="button" className={"wbp-node " + state} onClick={function () { toggleStep(step.id) }} aria-label={expanded ? planT("plan.step.collapse", "Collapse step") : planT("plan.step.expand", "Expand step")}>{doneStep ? PLAN_ICONS.check : null}</button>{!isLast ? <span className={"wbp-line" + (doneStep ? " done" : "")} /> : null}</div>
        <div className="wbp-row" onClick={function () { toggleStep(step.id) }} role="button" tabIndex={0} onKeyDown={function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleStep(step.id) } }}>
          <div className="wbp-line-main"><div className="wbp-copy">{canAddReorder ? canReorderStep ? <button type="button" draggable className="wbp-drag-handle" title={planT("plan.dragToReorder", "Drag to reorder")} aria-label={planT("plan.dragToReorder", "Drag to reorder")} onClick={function (event) { event.stopPropagation() }} onKeyDown={function (event) { event.stopPropagation(); if (event.altKey && event.key === "ArrowUp") { event.preventDefault(); moveBy(step.id, -1) } if (event.altKey && event.key === "ArrowDown") { event.preventDefault(); moveBy(step.id, 1) } }} onDragStart={function (event) { event.stopPropagation(); setDragStepId(step.id); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", step.id) }} onDragEnd={function () { setDragStepId(""); setDragOverId("") }}>{PLAN_ICONS.dots}</button> : <span className="wbp-drag-spacer" aria-hidden="true" /> : null}<span className="wbp-idx">{index + 1}.</span><span className="wbp-title">{step.title || step.content || planT("chat.side.planStep", "Step") + " " + (index + 1)}</span></div><span className={"wbp-status " + state}>{statusLabel}</span><time className="wbp-time">{time}</time><span className="wbp-dur">{duration ? <>{ICON_CLOCK}<span>{duration}</span></> : estimate ? <span className="wbp-estimate">{planT("workbench.step.estimated", "Estimated {duration}", { duration: estimate })}</span> : null}</span><span className={"wbp-caret" + (expanded ? " open" : "")}>{ICON_CHEVRON}</span></div>
          {expanded ? <div className="wbp-detail" onClick={function (event) { event.stopPropagation() }}>{beforeRun ? editing ? <PlanStepEditor step={step} steps={steps} controller={controller} commandForStep={props.commandForStep} checkWorkspacePath={props.checkWorkspacePath} /> : <PlanStepSummary step={step} steps={steps} commandForStep={props.commandForStep} /> : <div className="wbp-summary"><div className="wbp-summary-row"><span className="wbp-summary-k">{planT("workbench.step.progress", "Progress")}</span><span className="wbp-summary-v">{progressText || planT("workbench.step.waitingProgress", "Waiting for the Agent to update this step.")}{Array.isArray(step.progressEvents) && step.progressEvents.length ? <ul className="wbp-events">{step.progressEvents.slice(-3).map(function (event, eventIndex) { return <li key={eventIndex}>{event.body || event.text || event.message || String(event)}</li> })}</ul> : null}</span></div><div className="wbp-summary-row"><span className="wbp-summary-k">{planT("workbench.step.files", "Files")}</span><span className="wbp-summary-v">{hasFiles ? <div className="wbp-file-chips">{step.relatedFiles.map(function (file) { return <button key={file.path || file.name} type="button" className="wbp-file-chip" onClick={function () { if (typeof props.onOpenFiles === "function") props.onOpenFiles(file) }}>{String(file.path || file.name || "").split("/").pop()}</button> })}</div> : <em className="wbp-summary-none">{planT("workbench.step.noFiles", "No related files")}</em>}</span></div></div>}
            {props.showExecutionActions && !doneStep ? <div className="wbp-detail-actions">{runningStep ? <button type="button" className="wb-btn danger" onClick={function () { if (controller && typeof controller.interrupt === "function") controller.interrupt() }}>{planT("workbench.step.stop", "Stop")}</button> : <button type="button" className="wb-btn primary" disabled={busy || unmet.length > 0} onClick={function () { if (controller && typeof controller.runStep === "function") controller.runStep(step) }}>{planT("workbench.step.run", "Run this step")}</button>}<button type="button" className="wb-btn ghost" onClick={function () { if (typeof props.onOpenLogs === "function") props.onOpenLogs(step) }}>{planT("workbench.step.viewLogs", "View logs")}</button>{unmet.length ? <span className="wbp-blocked-hint">{planT("plan.completePrerequisitesFirst", "Complete prerequisite steps first.")}</span> : null}</div> : null}
          </div> : null}
        </div>
      </div>
    })}</div>
  </section>
}

export { PlanTimeline }
