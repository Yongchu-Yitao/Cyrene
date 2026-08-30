import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WBC_ICONS } from "./icons.jsx"
import { WbcViewerTab } from "./viewer.jsx"
import { WbcProjectFileHeader, WbcProjectFileRow, useWbcProjectFiles } from "./project-files.jsx"
import { wbcT } from "../../workbench-chat.jsx"

var { useEffect, useRef, useState } = React
var ACTIVE_EXECUTION_STATES = ["starting", "running", "ready", "stopping"]

function workspaceActionLabel(action, lang) {
  var item = action && typeof action === "object" ? action : {}
  var translations = item.i18n && typeof item.i18n === "object" ? item.i18n : {}
  var locale = String(lang || "en").replace(/_/g, "-").toLowerCase()
  var authored = translations[locale] || translations[locale.split("-")[0]] || {}
  return String(authored && authored.label || item.label || item.id || "")
}

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
  var [direction, setDirection] = useState("forward")
  var projectFiles = useWbcProjectFiles({ enabled: true, path: path, projectId: projectId })
  function up() {
    var parts = path.split("/").filter(Boolean)
    parts.pop()
    setDirection("back")
    setPath(parts.join("/") || ".")
  }
  return <div className="wbc-workspace-files">
    <WbcProjectFileHeader path={path} onBack={up} workspace={true} />
    {projectFiles.loading && !projectFiles.hasLoaded ? <WorkspaceEmpty title={wbcT("common.loading", "Loading…")} body="" />
      : projectFiles.error ? <WorkspaceEmpty title={wbcT("workspace.filesUnavailable", "Files unavailable")} body={projectFiles.error} />
      : <div className="wbc-workspace-file-list">{projectFiles.entries.map(function (entry) {
        return <WbcProjectFileRow key={entry.path || entry.name} direction={direction} entry={entry} projectId={projectId} onOpen={function (selected) {
          if (selected.kind === "directory") { setDirection("forward"); setPath(String(selected.path || selected.name || ".")) }
          else if (selected.kind === "file") onOpen(String(selected.path || selected.name || ""))
        }} />
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

function gitStatusEntries(status) {
  return String(status || "").split(/\r?\n/).map(function (line) {
    if (!line.trim()) return null
    var code = line.slice(0, 2).trim() || "M"
    var path = line.slice(3).trim()
    var tone = code.indexOf("?") >= 0 ? "untracked"
      : code.indexOf("A") >= 0 ? "added"
      : code.indexOf("D") >= 0 ? "deleted"
      : "modified"
    return path ? { code: code, path: path, tone: tone } : null
  }).filter(Boolean)
}

function workspaceDiffStats(diff) {
  return String(diff || "").split(/\r?\n/).reduce(function (totals, line) {
    if (line.indexOf("+++") === 0 || line.indexOf("---") === 0) return totals
    if (line.charAt(0) === "+") totals.additions += 1
    else if (line.charAt(0) === "-") totals.deletions += 1
    return totals
  }, { additions: 0, deletions: 0 })
}

function workspaceGitDiffFiles(diff, statusEntries) {
  var text = String(diff || "")
  var chunks = []
  var current = []
  text.split(/\r?\n/).forEach(function (line) {
    if (line.indexOf("diff --git ") === 0 && current.length) {
      chunks.push(current)
      current = []
    }
    if (line || current.length) current.push(line)
  })
  if (current.length) chunks.push(current)
  if (!chunks.length && text.trim()) chunks.push(text.split(/\r?\n/))
  var files = chunks.map(function (lines, index) {
    var header = lines.find(function (line) { return line.indexOf("diff --git ") === 0 }) || ""
    var match = header.match(/^diff --git (?:"?a\/(.+?)"?) (?:"?b\/(.+)"?)$/)
    var path = match && String(match[2] || "").replace(/"$/, "") || ""
    if (!path) {
      var marker = lines.find(function (line) { return line.indexOf("+++ b/") === 0 }) || ""
      path = marker ? marker.slice(6) : ""
    }
    if (!path) path = statusEntries[index] && statusEntries[index].path || wbcT("workspace.review.workspaceChanges", "Workspace changes")
    var patch = lines.join("\n")
    return Object.assign({ path: path, diff: patch }, workspaceDiffStats(patch))
  })
  statusEntries.forEach(function (entry) {
    if (!files.some(function (file) { return file.path === entry.path })) {
      files.push({ path: entry.path, diff: "", additions: 0, deletions: 0 })
    }
  })
  return files
}

function WorkspaceDiff({ diff }) {
  var DiffPanel = workbenchServices.diff().Panel
  return <section className="wbc-workspace-diff">
    <div className="wbc-change-split-diff wbc-change-diff wbc-workspace-diff-view">
      {DiffPanel
        ? React.createElement(DiffPanel, { diff: diff, mode: "text", hideHeader: true, hideHunkHeaders: true })
        : <WorkspaceEmpty title={wbcT("workspace.review.diffUnavailable", "Diff viewer unavailable")} body="" />}
    </div>
  </section>
}

function workspaceReviewHasContent(payload) {
  var source = payload && typeof payload === "object" ? payload : {}
  var snapshot = source.snapshot || {}
  var sets = Array.isArray(snapshot.changeSets) ? snapshot.changeSets : []
  var git = source.git || {}
  return sets.some(function (set) { return Array.isArray(set.files) && set.files.length > 0 })
    || gitStatusEntries(git.status).length > 0
    || !!String(git.diff || "").trim()
}

function useWorkspaceReviewData(projectId, chatId, refreshKey) {
  var [payload, setPayload] = useState(null)
  var [error, setError] = useState("")
  var [loading, setLoading] = useState(!!projectId)
  var requestRef = useRef(0)
  function load() {
    var request = requestRef.current + 1
    requestRef.current = request
    if (!projectId) { setPayload(null); setError(""); setLoading(false); return }
    setLoading(true)
    apiJson("/api/code/workspace-review?projectId=" + encodeURIComponent(projectId) + "&chatId=" + encodeURIComponent(chatId || ""))
      .then(function (next) { if (request === requestRef.current) { setPayload(next); setError("") } })
      .catch(function (err) { if (request === requestRef.current) setError(String(err && err.message || err)) })
      .finally(function () { if (request === requestRef.current) setLoading(false) })
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
  return { payload: payload, error: error, loading: loading, load: load }
}

function WorkspaceReview({ chatId, review }) {
  var [source, setSource] = useState("snapshot")
  var [selectedDiff, setSelectedDiff] = useState("")
  var [selectedPath, setSelectedPath] = useState("")
  var [diffLoading, setDiffLoading] = useState(false)
  var [detailError, setDetailError] = useState("")
  var payload = review.payload || {}
  var snapshot = payload.snapshot || {}
  var sets = Array.isArray(snapshot.changeSets) ? snapshot.changeSets : []
  var files = sets.reduce(function (result, set) {
    return result.concat((set.files || []).map(function (file) { return Object.assign({ setId: set.id }, file) }))
  }, [])
  var git = payload.git || {}
  var gitEntries = gitStatusEntries(git.status)
  var gitFiles = workspaceGitDiffFiles(git.diff, gitEntries)
  var hasSnapshot = files.length > 0
  var hasGit = gitFiles.length > 0
  var visibleSource = source === "snapshot" && hasSnapshot ? "snapshot" : source === "git" && hasGit ? "git" : hasSnapshot ? "snapshot" : "git"
  var sourceFiles = visibleSource === "snapshot" ? files : gitFiles
  var selectedFile = sourceFiles.find(function (file) { return file.path === selectedPath }) || sourceFiles[0] || null
  var diff = visibleSource === "snapshot" ? selectedDiff : String(selectedFile && selectedFile.diff || "")
  var additions = sourceFiles.reduce(function (total, file) { return total + Number(file.additions || 0) }, 0)
  var deletions = sourceFiles.reduce(function (total, file) { return total + Number(file.deletions || 0) }, 0)
  useEffect(function () {
    if (visibleSource !== "snapshot" || !chatId || !selectedFile || !selectedFile.setId || !selectedFile.path) {
      setSelectedDiff("")
      setDiffLoading(false)
      return undefined
    }
    var cancelled = false
    setDiffLoading(true)
    setDetailError("")
    apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/changes/" + encodeURIComponent(selectedFile.setId) + "/files/" + selectedFile.path.split("/").map(encodeURIComponent).join("/"))
      .then(function (next) { if (!cancelled) setSelectedDiff(String(next && next.change && next.change.diff || "")) })
      .catch(function (err) { if (!cancelled) setDetailError(String(err && err.message || err)) })
      .finally(function () { if (!cancelled) setDiffLoading(false) })
    return function () { cancelled = true }
  }, [visibleSource, chatId, selectedFile && selectedFile.setId, selectedFile && selectedFile.path])
  if ((review.error && !review.payload) || detailError) return <WorkspaceEmpty title={wbcT("workspace.review.failed", "Review unavailable")} body={review.error || detailError} action={<button className="wb-btn ghost" onClick={function () { setDetailError(""); review.load() }}>{wbcT("common.retry", "Retry")}</button>} />
  if (!review.payload) return <WorkspaceEmpty title={wbcT("common.loading", "Loading…")} body="" />
  return <div className="wbc-workspace-review">
    <header className="wbc-workspace-review-overview">
      <div className="wbc-workspace-review-summary">
        <label className="wbc-workspace-review-source">
          <select aria-label={wbcT("workspace.review.source", "Review source")} value={visibleSource} onChange={function (event) { setSource(event.target.value); setSelectedPath("") }}>
            {hasSnapshot ? <option value="snapshot">Cyrene</option> : null}
            {hasGit ? <option value="git">Git</option> : null}
          </select>
          <span aria-hidden="true">{WBC_ICONS.chevronRight}</span>
        </label>
        <span className="wbc-workspace-review-kind">{visibleSource === "snapshot" ? wbcT("workspace.review.snapshotKind", "Snapshot") : wbcT("workspace.review.gitKind", "Working tree")}</span>
        <span className="wbc-workspace-review-totals"><b>+{additions}</b><i>−{deletions}</i></span>
        <button type="button" className="wbc-workspace-review-refresh" onClick={review.load} disabled={review.loading} aria-busy={review.loading ? "true" : undefined} aria-label={wbcT("common.refresh", "Refresh")}>{WBC_ICONS.retry}</button>
      </div>
      <span className="wbc-workspace-review-context">{visibleSource === "snapshot" ? wbcT("workspace.review.snapshotContext", "Conversation snapshot") : wbcT("workspace.review.gitContext", "Workspace changes")} · {sourceFiles.length}</span>
    </header>
    <div className="wbc-workspace-review-body">
      {diffLoading ? <WorkspaceEmpty title={wbcT("common.loading", "Loading…")} body="" /> : <WorkspaceDiff diff={diff} />}
      <footer className="wbc-workspace-review-file">
        <span aria-hidden="true">{WBC_ICONS.fileText}</span>
        {sourceFiles.length > 1 ? <select aria-label={wbcT("workspace.review.file", "Changed file")} value={selectedFile && selectedFile.path || ""} onChange={function (event) { setSelectedPath(event.target.value) }}>{sourceFiles.map(function (file, index) { return <option value={file.path} key={visibleSource + ":" + file.path + ":" + index}>{file.path}</option> })}</select> : <b title={selectedFile && selectedFile.path}>{selectedFile && selectedFile.path}</b>}
        <span className="wbc-workspace-review-file-stats"><b>+{Number(selectedFile && selectedFile.additions || 0)}</b><i>−{Number(selectedFile && selectedFile.deletions || 0)}</i></span>
      </footer>
    </div>
  </div>
}

function WorkspacePreview({ projectId, execution }) {
  var endpoint = execution && execution.endpoints && execution.endpoints[0]
  var artifact = execution && execution.artifacts && execution.artifacts[0]
  if (endpoint && endpoint.url) return <div className="wbc-workspace-preview"><header><span>{endpoint.label || endpoint.url}</span><a href={endpoint.url} target="_blank" rel="noreferrer">{WBC_ICONS.openExternal}</a></header><iframe src={endpoint.url} title={endpoint.label || "Preview"} sandbox="allow-forms allow-modals allow-popups allow-same-origin allow-scripts" /></div>
  if (artifact && artifact.path) return <div className="wbc-workspace-preview-artifact"><WbcViewerTab file={workspaceFile(projectId, artifact.path)} hideHeader={false} /></div>
  return <WorkspaceEmpty title={wbcT("workspace.preview.empty", "No preview yet")} body={wbcT("workspace.preview.hint", "Run an action that declares a preview port or output artifact.")} />
}

function workspaceSurfaceTabs(selectedFile, execution, diagnostics, hasReview, hasPreview) {
  var tabs = []
  if (selectedFile) tabs.push({ id: "editor", label: wbcT("workspace.tab.editor", "Editor"), icon: WBC_ICONS.code })
  if (execution && execution.terminalId) tabs.push({ id: "terminal", label: wbcT("workspace.tab.terminal", "Terminal"), icon: WBC_ICONS.slash })
  if (diagnostics.length) tabs.push({ id: "problems", label: wbcT("workspace.tab.problems", "Problems"), icon: WBC_ICONS.errorCircle, count: diagnostics.length })
  if (hasReview) tabs.push({ id: "review", label: wbcT("workspace.tab.review", "Review"), icon: WBC_ICONS.checklist })
  if (hasPreview) tabs.push({ id: "preview", label: wbcT("workspace.tab.preview", "Preview"), icon: WBC_ICONS.browser })
  tabs.push({ id: "files", label: wbcT("workspace.tab.files", "Files"), icon: WBC_ICONS.folder })
  return tabs
}

function WbcWorkspaceSurface({ descriptor, projectId: projectIdProp, chatId }) {
  var { lang } = workbenchServices.i18n().use()
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
  var timerRef = useRef(null)
  var feedback = workbenchServices.feedback()
  var feedbackKey = "workspace-surface:" + String(descriptor && descriptor.resourceKey || projectId)
  function notifyError(err) {
    var message = String(err && err.message || err || "")
    if (message) feedback.showToast(message, "error", { key: feedbackKey })
  }
  function setTab(next) { setTabState(next); try { localStorage.setItem(storageKey, next) } catch (_) {} }
  function selectFile(path) { setFilePath(path); setTab("editor") }
  function loadActions() {
    if (!projectId) return
    apiJson("/api/code/workspace-actions?projectId=" + encodeURIComponent(projectId) + "&path=" + encodeURIComponent(filePath || ""))
      .then(function (payload) {
        var next = Array.isArray(payload.actions) ? payload.actions : []
        setActions(next)
        setActionId(function (current) { return next.some(function (item) { return item.id === current && item.available }) ? current : ((next.find(function (item) { return item.available }) || {}).id || "") })
      }).catch(notifyError)
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
    setBusy(true)
    post("/api/code/executions", { projectId: projectId, actionId: actionId, currentPath: filePath, chatId: chatId || "" })
      .then(function (payload) { setExecution(payload.execution || null); setTab("terminal") })
      .catch(notifyError)
      .finally(function () { setBusy(false) })
  }
  function stopExecution() {
    if (!execution) return
    setBusy(true)
    post("/api/code/executions/" + encodeURIComponent(execution.id) + "/stop")
      .then(function (payload) { setExecution(payload.execution || null) })
      .catch(notifyError)
      .finally(function () { setBusy(false) })
  }
  var active = execution && ACTIVE_EXECUTION_STATES.indexOf(execution.status) >= 0
  var selectedAction = actions.find(function (item) { return item.id === actionId })
  var selectedActionLabel = workspaceActionLabel(selectedAction, lang)
  var diagnostics = execution && Array.isArray(execution.diagnostics) ? execution.diagnostics : []
  var hasPreview = !!(execution && ((execution.endpoints || []).length || (execution.artifacts || []).length))
  var selectedFile = workspaceFile(projectId, filePath)
  var review = useWorkspaceReviewData(projectId, chatId, execution && execution.id)
  var hasReview = !!review.error || workspaceReviewHasContent(review.payload) || (review.loading && tab === "review")
  var tabs = workspaceSurfaceTabs(selectedFile, execution, diagnostics, hasReview, hasPreview)
  var fallbackTab = selectedFile ? "editor" : "files"
  var visibleTab = tabs.some(function (item) { return item.id === tab }) ? tab : fallbackTab
  useEffect(function () {
    if (tab !== visibleTab) setTab(visibleTab)
  }, [tab, visibleTab])
  var TerminalPane = workbenchServices.terminal().Pane
  return <section className="wbc-workspace-surface">
    <div className="wbc-workspace-chrome">
      <header className="wbc-workspace-toolbar">
        <label className="wbc-workspace-action-picker"><span aria-hidden="true">{WBC_ICONS.bolt}</span><select value={actionId} title={selectedActionLabel} onChange={function (event) { setActionId(event.target.value) }} aria-label={wbcT("workspace.action", "Workspace action")} disabled={!actions.length}>{actions.length ? actions.map(function (action) { return <option key={action.id} value={action.id} disabled={!action.available}>{workspaceActionLabel(action, lang) + (!action.available ? " — " + wbcT("workspace.actionUnavailable", "Unavailable") : "")}</option> }) : <option>{wbcT("workspace.noActions", "No actions detected")}</option>}</select></label>
        <button type="button" className="is-primary" disabled={busy || active || !selectedAction || !selectedAction.available} onClick={run}>{WBC_ICONS.play}<span>{wbcT("workspace.run", "Run")}</span></button>
        {active ? <button type="button" disabled={busy} onClick={stopExecution}>{WBC_ICONS.stop}<span>{wbcT("workspace.stop", "Stop")}</span></button> : null}
        <span className={"wbc-workspace-status is-" + String(execution && execution.status || "idle")}><i /><span>{execution ? wbcT("workspace.status." + execution.status, execution.status) : wbcT("workspace.idle", "Idle")}</span></span>
      </header>
    </div>
    <div className="wbc-workspace-content">
      {visibleTab === "editor" ? (selectedFile ? <WbcViewerTab file={selectedFile} hideHeader={false} onDirtyChange={function (dirty) { if (dirty) window.dispatchEvent(new CustomEvent("cyrene:surface-claim", { detail: { resourceKey: descriptor.resourceKey } })) }} /> : <WorkspaceEmpty title={wbcT("workspace.editor.empty", "Choose a file to edit")} body={wbcT("workspace.editor.hint", "Open the Files tab and select a project file.")} />) : null}
      {visibleTab === "terminal" ? (execution && execution.terminalId ? <TerminalPane terminalId={execution.terminalId} /> : <WorkspaceEmpty title={wbcT("workspace.terminal.empty", "No execution terminal")} body={wbcT("workspace.terminal.hint", "Choose an action and run it to open a managed terminal.")} />) : null}
      {visibleTab === "problems" ? <WorkspaceProblems diagnostics={diagnostics} onOpen={selectFile} /> : null}
      {visibleTab === "review" ? <WorkspaceReview chatId={chatId} review={review} /> : null}
      {visibleTab === "preview" ? <WorkspacePreview projectId={projectId} execution={execution} /> : null}
      {visibleTab === "files" ? <WorkspaceFiles projectId={projectId} initialPath={filePath} initialIsFile={!!filePath} onOpen={selectFile} /> : null}
    </div>
    <nav className="wbc-workspace-tabs" role="tablist">{tabs.map(function (item) { return <button type="button" role="tab" aria-selected={visibleTab === item.id} aria-label={item.label} className={visibleTab === item.id ? "active" : ""} key={item.id} onClick={function () { setTab(item.id) }}><span className="wbc-workspace-tab-icon" aria-hidden="true">{item.icon}</span><span>{item.label}</span>{item.count ? <small>{item.count}</small> : null}{item.id === "preview" ? <i /> : null}</button> })}</nav>
  </section>
}

export { WbcWorkspaceSurface, workspaceActionLabel, workspaceFile }
