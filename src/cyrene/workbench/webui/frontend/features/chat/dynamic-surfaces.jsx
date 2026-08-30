import { PluginFrontendService, PluginView, pluginLocalizedField } from "../../platform/plugins.jsx"
import { WBC_ICONS, wbcT } from "../../workbench-chat.jsx"
import { WbcViewerTab } from "./viewer.jsx"
import { WbcWorkspaceSurface } from "./workspace-surface.jsx"
import { wbcSurfaceIntentsFromActivity, wbcSurfaceResourceKey } from "./dynamic-surface-broker.mjs"

var { useEffect, useMemo, useState } = React
var WBC_SURFACE_INTENT_EVENT = "cyrene:surface-intent"

function normalizeSurfaceDescriptor(value) {
  var raw = value && typeof value === "object" ? value : {}
  if (Number(raw.schemaVersion || 0) !== 1) return null
  var surfaceId = String(raw.surfaceId || raw.surface_id || "")
  var packId = String(raw.packId || raw.pack_id || "")
  var resource = raw.resource && typeof raw.resource === "object" ? raw.resource : null
  var resourceKey = String(raw.resourceKey || raw.resource_key || wbcSurfaceResourceKey(resource))
  if (!surfaceId || !packId || !resource || !resourceKey) return null
  return Object.assign({}, raw, {
    schemaVersion: 1,
    surfaceId: surfaceId,
    packId: packId,
    resource: resource,
    resourceKey: resourceKey,
  })
}

function WbcResourceSummarySurface({ descriptor, title }) {
  var resource = descriptor && descriptor.resource || {}
  return <section className="wbc-side-agent-split wbc-dynamic-surface-summary">
    <div className="wbc-plugin-view-state">
      <strong>{title || descriptor.surfaceId}</strong>
      <span>{String(resource.path || resource.url || resource.id || descriptor.resourceKey)}</span>
    </div>
  </section>
}

function projectFileResource(resource) {
  var projectId = String(resource && resource.projectId || "")
  var path = String(resource && resource.path || "").replace(/\\/g, "/")
  var encodedPath = path.split("/").filter(Boolean).map(encodeURIComponent).join("/")
  if (!projectId || !encodedPath) return null
  return {
    name: path.split("/").pop() || "file",
    path: path,
    kind: "file",
    source: "project",
    projectId: projectId,
    url: "/api/projects/" + encodeURIComponent(projectId) + "/files/content/" + encodedPath,
  }
}

function WbcWorkspaceFileSurface({ descriptor }) {
  var file = projectFileResource(descriptor && descriptor.resource)
  if (!file) return <WbcUnavailableSurface descriptor={descriptor} />
  function claim(dirty) {
    if (!dirty) return
    window.dispatchEvent(new CustomEvent("cyrene:surface-claim", {
      detail: { resourceKey: descriptor.resourceKey },
    }))
  }
  return <section className="wbc-side-agent-split wbc-dynamic-workspace-file">
    <WbcViewerTab file={file} hideHeader={false} onDirtyChange={claim} />
  </section>
}

function WbcWorkspaceDirectorySurface({ descriptor }) {
  var resource = descriptor && descriptor.resource || {}
  var projectId = String(resource.projectId || "")
  var path = String(resource.path || ".")
  var [currentPath, setCurrentPath] = useState(path)
  var [state, setState] = useState({ loading: true, entries: [], error: "" })
  useEffect(function () { setCurrentPath(path) }, [path])
  function load() {
    if (!projectId) return
    setState(function (current) { return Object.assign({}, current, { loading: true, error: "" }) })
    fetch("/api/projects/" + encodeURIComponent(projectId) + "/files?path=" + encodeURIComponent(currentPath), { cache: "no-store" })
      .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error(String(response.status))) })
      .then(function (payload) { setState({ loading: false, entries: Array.isArray(payload.entries) ? payload.entries : [], error: "" }) })
      .catch(function () { setState({ loading: false, entries: [], error: wbcT("rail.filesUnavailable", "Unable to load project files.") }) })
  }
  useEffect(load, [projectId, currentPath])
  useEffect(function () {
    function changed(event) {
      var detail = event && event.detail || {}
      var changedPath = String(detail.path || "")
      if (String(detail.projectId || "") !== projectId) return
      if (path === "." || changedPath === path || changedPath.indexOf(path.replace(/\/$/, "") + "/") === 0) load()
    }
    window.addEventListener("cyrene:workspace-file-changed", changed)
    return function () { window.removeEventListener("cyrene:workspace-file-changed", changed) }
  }, [projectId, path])
  function open(entry) {
    if (!entry) return
    if (entry.kind === "directory") {
      setCurrentPath(String(entry.path || entry.name || "."))
      return
    }
    if (entry.kind !== "file") return
    window.dispatchEvent(new CustomEvent("cyrene:workbench-navigate", {
      detail: { type: "file", entry: entry },
    }))
  }
  return <section className="wbc-side-agent-split wbc-dynamic-directory">
    <header className="wbc-dynamic-directory-head">
      {currentPath !== path ? <button type="button" onClick={function () {
        var parts = currentPath.split("/").filter(Boolean)
        parts.pop()
        var parent = parts.join("/") || "."
        setCurrentPath(path === "." || parent.indexOf(path) === 0 ? parent : path)
      }}>←</button> : null}
      <span>{WBC_ICONS.folder}</span><b>{currentPath}</b>
    </header>
    {state.loading ? <p className="workbench-muted">{wbcT("common.loading", "Loading…")}</p>
      : state.error ? <p className="workbench-muted">{state.error}</p>
      : <div className="wbc-dynamic-directory-list">{state.entries.map(function (entry) {
          return <button type="button" key={entry.path || entry.name} onClick={function () { open(entry) }}>
            <span aria-hidden="true">{entry.kind === "directory" ? WBC_ICONS.folder : WBC_ICONS.file}</span>
            <b>{entry.name}</b>
          </button>
        })}</div>}
  </section>
}

var NATIVE_SURFACE_RENDERERS = Object.freeze({
  "resource-summary": WbcResourceSummarySurface,
  "workspace-file": WbcWorkspaceFileSurface,
  "workspace-composite": WbcWorkspaceSurface,
  "workspace-directory": WbcWorkspaceDirectorySurface,
})

var ACTIVITY_EVENT_IDS = new Set()
var ACTIVITY_EVENT_ORDER = []

function markActivityEvent(event) {
  var payload = event && event.payload && typeof event.payload === "object" ? event.payload : event || {}
  var eventId = String(event && (event.eventId || event.event_id) || payload.eventId || payload.event_id || "")
  if (!eventId) return true
  if (ACTIVITY_EVENT_IDS.has(eventId)) return false
  ACTIVITY_EVENT_IDS.add(eventId)
  ACTIVITY_EVENT_ORDER.push(eventId)
  if (ACTIVITY_EVENT_ORDER.length > 500) ACTIVITY_EVENT_IDS.delete(ACTIVITY_EVENT_ORDER.shift())
  return true
}

function WbcUnavailableSurface({ descriptor, title }) {
  return <section className="wbc-side-agent-split wbc-dynamic-surface-unavailable" role="status">
    <div className="wbc-plugin-view-state">
      <strong>{title || descriptor && descriptor.surfaceId || "Surface"}</strong>
      <span>{pluginLocalizedField({
        title: "This Plugin surface is disabled or unavailable.",
        i18n: { zh: { title: "该插件分屏已禁用或不可用。" } },
      }, "title")}</span>
    </div>
  </section>
}

function WbcSurfaceHost(props) {
  var descriptor = normalizeSurfaceDescriptor(props.descriptor || props.payload)
  var [snapshot, setSnapshot] = useState(function () { return PluginFrontendService.snapshot() })
  useEffect(function () { return PluginFrontendService.subscribe(setSnapshot) }, [])
  var surface = useMemo(function () {
    if (!descriptor) return null
    return (snapshot.workbenchSurfaces || []).find(function (item) {
      return String(item && item.id || "") === descriptor.surfaceId
        && String(item && item.pack_id || "") === descriptor.packId
    }) || null
  }, [snapshot, descriptor && descriptor.surfaceId, descriptor && descriptor.packId])
  if (!descriptor || !surface) {
    return <WbcUnavailableSurface descriptor={descriptor || props.descriptor} title={props.title} />
  }
  var renderer = surface.renderer && typeof surface.renderer === "object" ? surface.renderer : {}
  if (renderer.kind === "plugin_view") {
    var pluginPayload = {
      packId: descriptor.packId,
      viewId: String(renderer.id || ""),
      projectId: props.projectId || descriptor.resource && descriptor.resource.projectId || "",
      instanceId: descriptor.resourceKey,
      title: props.title || pluginLocalizedField(surface, "title"),
      state: descriptor.state,
      resource: descriptor.resource,
    }
    return <section className="wbc-side-agent-split wbc-plugin-view-pane wbc-dynamic-surface-pane">
      <div className="wbc-plugin-view-host-strip" aria-hidden="true" />
      <div className="wbc-plugin-view-content">
        <PluginView projectId={pluginPayload.projectId} payload={pluginPayload} />
      </div>
    </section>
  }
  var NativeRenderer = renderer.kind === "native" ? NATIVE_SURFACE_RENDERERS[String(renderer.id || "")] : null
  if (!NativeRenderer) {
    return <WbcUnavailableSurface descriptor={descriptor} title={props.title || pluginLocalizedField(surface, "title")} />
  }
  return <NativeRenderer {...props} descriptor={descriptor} surface={surface} title={props.title || pluginLocalizedField(surface, "title")} />
}

var DynamicSurfaceService = Object.freeze({
  request: function (intent) {
    if (typeof window.CustomEvent !== "function") return false
    window.dispatchEvent(new window.CustomEvent(WBC_SURFACE_INTENT_EVENT, { detail: intent }))
    return true
  },
  requestActivity: function (event) {
    var snapshot = PluginFrontendService.snapshot()
    if (!snapshot.loaded) {
      PluginFrontendService.refresh().then(function () {
        DynamicSurfaceService.requestActivity(event)
      }).catch(function () {})
      return 0
    }
    if (!markActivityEvent(event)) return 0
    var intents = wbcSurfaceIntentsFromActivity(
      event,
      snapshot.workbenchSurfaces
    )
    intents.forEach(function (intent) {
      window.dispatchEvent(new window.CustomEvent(WBC_SURFACE_INTENT_EVENT, { detail: intent }))
    })
    var payload = event && event.payload && typeof event.payload === "object" ? event.payload : event || {}
    var presentation = payload.presentation && typeof payload.presentation === "object" ? payload.presentation : {}
    if (payload.failed !== true && String(presentation.phase || payload.status || "") === "completed") {
      ;(presentation.locations || []).forEach(function (location) {
        if (location && location.kind === "file" && location.access === "write") {
          window.dispatchEvent(new window.CustomEvent("cyrene:workspace-file-changed", {
            detail: Object.assign({}, location, { source: "agent" }),
          }))
        }
      })
    }
    return intents.length
  },
  eventName: WBC_SURFACE_INTENT_EVENT,
})

window.CyreneUI.dynamicSurfaces = window.CyreneUI.register("dynamicSurfaces", DynamicSurfaceService)

export {
  DynamicSurfaceService,
  NATIVE_SURFACE_RENDERERS,
  WBC_SURFACE_INTENT_EVENT,
  WbcSurfaceHost,
  WbcUnavailableSurface,
  WbcWorkspaceDirectorySurface,
  WbcWorkspaceFileSurface,
  normalizeSurfaceDescriptor,
}
