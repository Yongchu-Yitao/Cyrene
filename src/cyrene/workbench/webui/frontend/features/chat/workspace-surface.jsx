import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WBC_ICONS } from "./icons.jsx"
import { WbcViewerTab } from "./viewer.jsx"
import { wbcT } from "../../workbench-chat.jsx"

var { useEffect, useMemo, useRef, useState } = React
var ACTIVE_EXECUTION_STATES = ["starting", "running", "ready", "stopping"]

function workspaceFile(projectId, path) {
  var normalized = String(path || "").replace(/\\/g, "/").replace(/^\.\//, "")
  var encoded = normalized.split("/").filter(Boolean).map(encodeURIComponent).join("/")
  if (!projectId || !encoded) return null
  return {
    name: normalized.split("/").pop() || "file",
    path: normalized,
    kind: "file",
    source: "project",
    projectId: projectId,
    url: "/api/projects/" + encodeURIComponent(projectId) + "/files/content/" + encoded,
  }
}

function apiJson(path, options) {
  return workbenchServices.api().json(path, Object.assign({ toast: false }, options || {}))
}

function post(path, body) {
  return apiJson(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
}

function WorkspaceEmpty({ title, body, action }) {
  return <div className="wbc-workspace-empty" role="status">
    <strong>{title}</strong><span>{body}</span>{action || null}
  </div>
}

function WorkspaceFiles({ projectId, initialPath, initialIsFile, onOpen }) {
  var root = String(initialPath || ".")
  if (root !== "." && initialIsFile) root = root.split("/").slice(0, -1).join("/") || "."
  var [path, setPath] = useState(root)
  var [state, setState] = useState({ loading: true, entries: [], error: "" })
  function load() {
    if (!projectId) return
    setState(function (current) { return Object.assign({}, current, { loading: true, error: "" }) })
    apiJson("/api/projects/" + encodeURIComponent(projectId) + "/files?path=" + encodeURIComponent(path))
      .then(function (payload) {
        setState({ loading: false, entries: Array.isArray(payload.entries) ? payload.entries : [], error: "" })
      })
      .catch(function (error) {
        setState({ loading: false, entries: [], error: String(error && error.message || error) })
      })
  }
  useEffect(load, [projectId, path])
  useEffect(function () {
    function changed(event) {
      var detail = event && event.detail || {}
      if (String(detail.projectId || "") === projectId) load()
    }
    window.addEventListener("cyrene:workspace-file-changed", changed)
    return function () { window.removeEventListener("cyrene:workspace-file-changed", changed) }
  }, [projectId, path])
  function up() {
    var parts = path.split("/").filter(Boolean)
    parts.pop()
    setPath(parts.join("/") || ".")
  }
  return <div className="wbc-workspace-files">
    <header><button type="button" disabled={path === "."} onClick={up} aria-label={wbcT("workspace.up", "Parent folder")}>{WBC_ICONS.chevronLeft}</button><span>{WBC_ICONS.folder}</span><b title={path}>{path}</b><button type="button" onClick={load} aria-label={wbcT("common.refresh", "Refresh")}>{WBC_ICONS.retry}</button></header>
    {state.loading ? <WorkspaceEmpty title={wbcT("common.loading", "Loading…")} body="" />
      : state.error ? <WorkspaceEmpty title={wbcT("workspace.filesUnavailable", "Files unavailable")} body={state.error} />
      : <div className="wbc-workspace-file-list">{state.entries.map(function (entry) {
        return <button type="button" key={entry.path || entry.name} onClick={function () {
          if (entry.kind === "directory") setPath(String(entry.path || entry.name || "."))
          else if (entry.kind === "file") onOpen(String(entry.path || entry.name || ""))
        }}><span aria-hidden="true">{entry.kind === "directory" ? WBC_ICONS.folder : WBC_ICONS.file}</span><span>{entry.name}</span>{entry.kind === "directory" ? WBC_ICONS.chevronRight : null}</button>
      })}</div>}
  </div>
}

function WorkspaceProblems({ diagnostics, onOpen }) {
  if (!diagnostics.length) return <WorkspaceEmpty title={wbcT("workspace.problems.none", "No problems")} body={wbcT("workspace.problems.hint", "Build and test diagnostics will appear here.")} />
  return <div className="wbc-workspace-problems">{diagnostics.map(function (item, index) {
    return <button type="button" key={item.file + ":" + item.line + ":" + index} onClick={function () { onOpen(item.file) }}>
      <span className={"is-" + item.severity}>{item.severity === "warning" ? WBC_ICONS.alert : WBC_ICONS.errorCircle}</span>
      <span><b>{item.message}</b><small>{item.file}:{item.line}:{item.column || 1}</small></span>
    </button>
  })}</div>
}

function WorkspaceReview({ projectId, chatId, refreshKey }) {
  var [payload, setPayload] = useState(null)
  var [error, setError] = useState("")
  var [mode, setMode] = useState("snapshot")
  var [selectedDiff, setSelectedDiff] = useState("")
  var [diffLoading, setDiffLoading] = useState(false)
  function load() {
    if (!projectId) return
    apiJson("/api/code/workspace-review?projectId=" + encodeURIComponent(projectId) + "&chatId=" + encodeURIComponent(chatId || ""))
      .then(function (next) { setPayload(next); setError("") })
      .catch(function (err) { setError(String(err && err.message || err)) })
  }
  useEffect(load, [projectId, chatId, refreshKey])
  useEffect(function () {
    function changed() { load() }
    window.addEventListener("workbench:workspace-changes", changed)
    window.addEventListener("cyrene:workspace-file-changed", changed)
    return function () {
      window.removeEventListener("workbench:workspace-changes", changed)
      window.removeEventListener("cyrene:workspace-file-changed", changed)
    }
  }, [projectId, chatId])
  if (error) return <WorkspaceEmpty title={wbcT("workspace.review.failed", "Review unavailable")} body={error} action={<button className="wb-btn ghost" onClick={load}>{wbcT("common.retry", "Retry")}</button>} />
  if (!payload) return <WorkspaceEmpty title={wbcT("common.loading", "Loading…")} body="" />
  var snapshot = payload.snapshot || {}
  var sets = Array.isArray(snapshot.changeSets) ? snapshot.changeSets : []
  var files = sets.reduce(function (result, set) {
    return result.concat((set.files || []).map(function (file) { return Object.assign({ setId: set.id }, file) }))
  }, [])
  var git = payload.git || {}
  var diff = mode === "git" ? String(git.diff || "") : selectedDiff
  function openSnapshot(file) {
    if (!chatId || !file.setId || !file.path) return
    setDiffLoading(true)
    apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/changes/" + encodeURIComponent(file.setId) + "/files/" + file.path.split("/").map(encodeURIComponent).join("/"))
      .then(function (next) { setSelectedDiff(String(next && next.change && next.change.diff || "")) })
      .catch(function (err) { setError(String(err && err.message || err)) })
      .finally(function () { setDiffLoading(false) })
  }
  return <div className="wbc-workspace-review">
    <div className="wbc-workspace-review-switch" role="tablist">
      <button type="button" role="tab" aria-selected={mode === "snapshot"} className={mode === "snapshot" ? "active" : ""} onClick={function () { setMode("snapshot"); setSelectedDiff("") }}>{wbcT("workspace.review.snapshot", "Cyrene snapshot")}<small>{snapshot.fileCount || 0}</small></button>
      <button type="button" role="tab" aria-selected={mode === "git"} className={mode === "git" ? "active" : ""} onClick={function () { setMode("git") }}>{wbcT("workspace.review.git", "Git diff")}<small>{git.hasChanges ? "●" : "0"}</small></button>
      <button type="button" className="refresh" onClick={load} aria-label={wbcT("common.refresh", "Refresh")}>{WBC_ICONS.retry}</button>
    </div>
    {mode === "snapshot" && !selectedDiff ? (files.length ? <div className="wbc-workspace-review-files">{files.map(function (file, index) {
      return <button type="button" key={file.setId + ":" + file.path + ":" + index} onClick={function () { openSnapshot(file) }}><span>{WBC_ICONS.fileText}</span><b>{file.path}</b><small className="plus">+{file.additions || 0}</small><small className="minus">−{file.deletions || 0}</small></button>
    })}</div> : <WorkspaceEmpty title={wbcT("workspace.review.noSnapshots", "No Cyrene snapshots yet")} body={wbcT("workspace.review.snapshotHint", "Agent and workspace-action file changes will appear here.")} />) : null}
    {mode === "git" && !git.available ? <WorkspaceEmpty title={wbcT("workspace.review.noGit", "This workspace is not a Git repository")} body="" /> : null}
    {diffLoading ? <WorkspaceEmpty title={wbcT("common.loading", "Loading…")} body="" /> : null}
    {!diffLoading && (mode === "git" || selectedDiff) && diff ? <div className="wbc-workspace-diff"><button type="button" className="wb-btn ghost compact" onClick={function () { if (mode === "snapshot") setSelectedDiff("") }}>{mode === "snapshot" ? wbcT("common.back", "Back") : String(git.status || "").trim()}</button><pre><code>{diff}</code></pre></div> : null}
    {mode === "git" && git.available && !diff ? <WorkspaceEmpty title={wbcT("workspace.review.clean", "Working tree is clean")} body="" /> : null}
  </div>
}

function WorkspacePreview({ projectId, execution }) {
  var endpoint = execution && execution.endpoints && execution.endpoints[0]
  var artifact = execution && execution.artifacts && execution.artifacts[0]
  if (endpoint && endpoint.url) return <div className="wbc-workspace-preview"><header><span>{endpoint.label || endpoint.url}</span><a href={endpoint.url} target="_blank" rel="noreferrer">{WBC_ICONS.openExternal}</a></header><iframe src={endpoint.url} title={endpoint.label || "Preview"} sandbox="allow-forms allow-modals allow-popups allow-same-origin allow-scripts" /></div>
  if (artifact && artifact.path) return <div className="wbc-workspace-preview-artifact"><WbcViewerTab file={workspaceFile(projectId, artifact.path)} hideHeader={false} /></div>
  return <WorkspaceEmpty title={wbcT("workspace.preview.empty", "No preview yet")} body={wbcT("workspace.preview.hint", "Run an action that declares a preview port or output artifact.")} />
}

function WbcWorkspaceSurface({ descriptor, projectId: projectIdProp, chatId }) {
  var resource = descriptor && descriptor.resource || {}
  var projectId = String(projectIdProp || resource.projectId || "")
  var initialPath = String(resource.path || "")
  var storageKey = "cyrene.workspace-surface." + String(descriptor && descriptor.resourceKey || projectId)
  var [tab, setTabState] = useState(function () { try { return localStorage.getItem(storageKey) || (initialPath ? "editor" : "files") } catch (_) { return initialPath ? "editor" : "files" } })
  var [filePath, setFilePath] = useState(initialPath)
  var [actions, setActions] = useState([])
  var [actionId, setActionId] = useState("")
  var [execution, setExecution] = useState(null)
  var [busy, setBusy] = useState(false)
  var [error, setError] = useState("")
  var timerRef = useRef(null)
  function setTab(next) { setTabState(next); try { localStorage.setItem(storageKey, next) } catch (_) {} }
  function selectFile(path) { setFilePath(path); setTab("editor") }
  function loadActions() {
    if (!projectId) return
    apiJson("/api/code/workspace-actions?projectId=" + encodeURIComponent(projectId) + "&path=" + encodeURIComponent(filePath || ""))
      .then(function (payload) {
        var next = Array.isArray(payload.actions) ? payload.actions : []
        setActions(next)
        setActionId(function (current) { return next.some(function (item) { return item.id === current && item.available }) ? current : ((next.find(function (item) { return item.available }) || {}).id || "") })
      }).catch(function (err) { setError(String(err && err.message || err)) })
  }
  function loadExecutions() {
    if (!projectId) return
    apiJson("/api/code/executions?projectId=" + encodeURIComponent(projectId)).then(function (payload) {
      var records = Array.isArray(payload.executions) ? payload.executions : []
      var active = records.find(function (item) { return ACTIVE_EXECUTION_STATES.indexOf(item.status) >= 0 })
      if (active) { setExecution(active); setActionId(active.actionId || "") }
    }).catch(function () {})
  }
  useEffect(loadActions, [projectId, filePath])
  useEffect(loadExecutions, [projectId])
  useEffect(function () {
    if (timerRef.current) clearTimeout(timerRef.current)
    if (!execution || ACTIVE_EXECUTION_STATES.indexOf(execution.status) < 0) return undefined
    timerRef.current = setTimeout(function () {
      apiJson("/api/code/executions/" + encodeURIComponent(execution.id)).then(function (payload) { setExecution(payload.execution || null) }).catch(function () {})
    }, 1000)
    return function () { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [execution && execution.id, execution && execution.updatedAt, execution && execution.status])
  function run() {
    if (!actionId) return
    setBusy(true); setError("")
    post("/api/code/executions", { projectId: projectId, actionId: actionId, currentPath: filePath, chatId: chatId || "" })
      .then(function (payload) { setExecution(payload.execution || null); setTab("terminal") })
      .catch(function (err) { setError(String(err && err.message || err)) })
      .finally(function () { setBusy(false) })
  }
  function executionCommand(kind) {
    if (!execution) return
    setBusy(true); setError("")
    post("/api/code/executions/" + encodeURIComponent(execution.id) + "/" + kind)
      .then(function (payload) { setExecution(payload.execution || null); if (kind === "restart") setTab("terminal") })
      .catch(function (err) { setError(String(err && err.message || err)) })
      .finally(function () { setBusy(false) })
  }
  var active = execution && ACTIVE_EXECUTION_STATES.indexOf(execution.status) >= 0
  var selectedAction = actions.find(function (item) { return item.id === actionId })
  var diagnostics = execution && Array.isArray(execution.diagnostics) ? execution.diagnostics : []
  var hasPreview = !!(execution && ((execution.endpoints || []).length || (execution.artifacts || []).length))
  var tabs = [
    { id: "editor", label: wbcT("workspace.tab.editor", "Editor") },
    { id: "terminal", label: wbcT("workspace.tab.terminal", "Terminal") },
    { id: "problems", label: wbcT("workspace.tab.problems", "Problems"), count: diagnostics.length },
    { id: "review", label: wbcT("workspace.tab.review", "Review") },
    { id: "preview", label: wbcT("workspace.tab.preview", "Preview") },
    { id: "files", label: wbcT("workspace.tab.files", "Files") },
  ]
  var selectedFile = workspaceFile(projectId, filePath)
  var TerminalPane = workbenchServices.terminal().Pane
  return <section className="wbc-workspace-surface">
    <header className="wbc-workspace-toolbar">
      <select value={actionId} onChange={function (event) { setActionId(event.target.value) }} aria-label={wbcT("workspace.action", "Workspace action")} disabled={!actions.length}>{actions.length ? actions.map(function (action) { return <option key={action.id} value={action.id} disabled={!action.available}>{action.label + (!action.available ? " — unavailable" : "")}</option> }) : <option>{wbcT("workspace.noActions", "No actions detected")}</option>}</select>
      <button type="button" className="is-primary" disabled={busy || active || !selectedAction || !selectedAction.available} onClick={run}>{WBC_ICONS.play}<span>{wbcT("workspace.run", "Run")}</span></button>
      {active ? <button type="button" disabled={busy} onClick={function () { executionCommand("stop") }}>{WBC_ICONS.stop}<span>{wbcT("workspace.stop", "Stop")}</span></button> : null}
      {execution && !active ? <button type="button" disabled={busy} onClick={function () { executionCommand("restart") }}>{WBC_ICONS.retry}<span>{wbcT("workspace.restart", "Restart")}</span></button> : null}
      <span className={"wbc-workspace-status is-" + String(execution && execution.status || "idle")}><i />{execution ? execution.status : wbcT("workspace.idle", "Idle")}</span>
    </header>
    {error ? <div className="wbc-workspace-error" role="alert">{error}<button onClick={function () { setError("") }}>{WBC_ICONS.x}</button></div> : null}
    <nav className="wbc-workspace-tabs" role="tablist">{tabs.map(function (item) { return <button type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "active" : ""} key={item.id} onClick={function () { setTab(item.id) }}>{item.label}{item.count ? <small>{item.count}</small> : null}{item.id === "preview" && hasPreview ? <i /> : null}</button> })}</nav>
    <div className="wbc-workspace-content">
      {tab === "editor" ? (selectedFile ? <WbcViewerTab file={selectedFile} hideHeader={false} onDirtyChange={function (dirty) { if (dirty) window.dispatchEvent(new CustomEvent("cyrene:surface-claim", { detail: { resourceKey: descriptor.resourceKey } })) }} /> : <WorkspaceEmpty title={wbcT("workspace.editor.empty", "Choose a file to edit")} body={wbcT("workspace.editor.hint", "Open the Files tab and select a project file.")} />) : null}
      {tab === "terminal" ? (execution && execution.terminalId ? <TerminalPane terminalId={execution.terminalId} /> : <WorkspaceEmpty title={wbcT("workspace.terminal.empty", "No execution terminal")} body={wbcT("workspace.terminal.hint", "Choose an action and run it to open a managed terminal.")} />) : null}
      {tab === "problems" ? <WorkspaceProblems diagnostics={diagnostics} onOpen={selectFile} /> : null}
      {tab === "review" ? <WorkspaceReview projectId={projectId} chatId={chatId} refreshKey={execution && execution.updatedAt} /> : null}
      {tab === "preview" ? <WorkspacePreview projectId={projectId} execution={execution} /> : null}
      {tab === "files" ? <WorkspaceFiles projectId={projectId} initialPath={filePath} initialIsFile={!!filePath} onOpen={selectFile} /> : null}
    </div>
  </section>
}

export { WbcWorkspaceSurface, workspaceFile }
