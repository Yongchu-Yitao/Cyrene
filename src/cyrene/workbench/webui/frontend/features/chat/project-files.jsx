import { WBC_ICONS, useWbcEffect, useWbcState, wbcT } from "../../workbench-chat.jsx"
import { wbcStartFileDrag } from "./file-resources.jsx"
import { wbcProjectFileResource, wbcProjectFileVisual } from "./rail-model.jsx"

function useWbcProjectFiles({ enabled, path, projectId }) {
  var normalizedProjectId = String(projectId || "")
  var normalizedPath = String(path || ".")
  var key = normalizedProjectId + ":" + normalizedPath
  var [revision, setRevision] = useWbcState(0)
  var [snapshot, setSnapshot] = useWbcState({ key: "", entries: [], loading: false, error: "", hasLoaded: false })

  useWbcEffect(function () {
    function onWorkspaceFileChanged(event) {
      var detail = event && event.detail || {}
      if (String(detail.projectId || "") === normalizedProjectId) {
        setRevision(function (current) { return current + 1 })
      }
    }
    window.addEventListener("cyrene:workspace-file-changed", onWorkspaceFileChanged)
    return function () { window.removeEventListener("cyrene:workspace-file-changed", onWorkspaceFileChanged) }
  }, [normalizedProjectId])

  useWbcEffect(function () {
    if (!enabled || !normalizedProjectId) return undefined
    var cancelled = false
    var controller = typeof AbortController === "function" ? new AbortController() : null
    setSnapshot(function (current) {
      return {
        key: key,
        entries: current.key === key ? current.entries : [],
        loading: true,
        error: "",
        hasLoaded: current.key === key && current.hasLoaded,
      }
    })
    fetch("/api/projects/" + encodeURIComponent(normalizedProjectId) + "/files?path=" + encodeURIComponent(normalizedPath), {
      cache: "no-store",
      signal: controller ? controller.signal : undefined,
    })
      .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error(String(response.status))) })
      .then(function (payload) {
        if (!cancelled) setSnapshot({ key: key, entries: Array.isArray(payload.entries) ? payload.entries : [], loading: false, error: "", hasLoaded: true })
      })
      .catch(function (error) {
        if (!cancelled && (!error || error.name !== "AbortError")) {
          setSnapshot({ key: key, entries: [], loading: false, error: wbcT("rail.filesUnavailable", "Unable to load project files."), hasLoaded: false })
        }
      })
    return function () {
      cancelled = true
      if (controller) controller.abort()
    }
  }, [enabled, key, revision])

  if (snapshot.key !== key) return { entries: [], loading: !!enabled, error: "", hasLoaded: false }
  return snapshot
}

function WbcProjectFileHeader({ collapseControls, onBack, onCollapse, onContextMenu, onKeyDown, path, rootLabel, workspace }) {
  var normalizedPath = String(path || ".")
  return <div
    className={"wbc-project-tool-inline-header is-file wbc-project-file-header" + (workspace ? " is-workspace" : "")}
    tabIndex={onContextMenu ? 0 : undefined}
    data-cyrene-context-menu={onContextMenu ? "true" : undefined}
    onContextMenu={onContextMenu}
    onKeyDown={onKeyDown}
  >
    {normalizedPath === "." ? (
      <span className="wbc-project-tool-icon" aria-hidden="true">{WBC_ICONS.folder}</span>
    ) : (
      <button
        type="button"
        className="wbc-project-tool-directory-control"
        aria-label={wbcT("common.back", "Back")}
        onClick={onBack}
      >{WBC_ICONS.chevronLeft}</button>
    )}
    <span className="wbc-project-tool-copy">
      <b>{wbcT("rail.files", "Files")}</b>
      <small title={normalizedPath}>{normalizedPath === "." ? (rootLabel || ".") : normalizedPath}</small>
    </span>
    {onCollapse ? <button
      type="button"
      className="wbc-project-tool-inline-collapse"
      onClick={onCollapse}
      aria-label={wbcT("common.collapse", "Collapse")}
      aria-expanded="true"
      aria-controls={collapseControls}
    >{WBC_ICONS.chevronRight}</button> : null}
  </div>
}

function WbcProjectFileRow({ direction, entry, onOpen, projectId }) {
  var visual = wbcProjectFileVisual(entry)
  var projectFile = wbcProjectFileResource(projectId, entry)
  return <button
    type="button"
    className={"workbench-project-file-row is-" + visual.kind + (direction ? " enter-" + direction : "")}
    draggable={projectFile ? "true" : undefined}
    onDragStart={projectFile ? function (event) { wbcStartFileDrag(event, projectFile) } : undefined}
    onClick={function () { if (onOpen) onOpen(entry) }}
  >
    <span className="workbench-project-file-icon" aria-hidden="true">{visual.icon}</span>
    <b title={entry.path}>{entry.name}</b>
    {entry.kind === "directory" && <span className="workbench-project-file-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>}
  </button>
}

export { WbcProjectFileHeader, WbcProjectFileRow, useWbcProjectFiles }
