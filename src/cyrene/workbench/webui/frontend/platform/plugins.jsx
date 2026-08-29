import { workbenchServices } from "../shared/runtime/services.jsx"

var { useEffect, useMemo, useRef, useState } = React

function emptyPluginSnapshot() {
  return {
    loaded: false,
    loading: false,
    reloading: false,
    ok: true,
    directory: null,
    packs: [],
    plugins: [],
    standalonePlugins: [],
    frontendViews: [],
    projectTools: [],
    failures: [],
    attachedApplicationPacks: [],
    applicationRestartRequired: false,
    reload: null,
    error: "",
  }
}

function normalizePluginSnapshot(payload, pending) {
  payload = payload && typeof payload === "object" ? payload : {}
  return {
    loaded: true,
    loading: false,
    reloading: false,
    ok: payload.ok !== false,
    directory: payload.directory && typeof payload.directory === "object" ? payload.directory : null,
    packs: Array.isArray(payload.packs) ? payload.packs : [],
    plugins: Array.isArray(payload.plugins) ? payload.plugins : [],
    standalonePlugins: Array.isArray(payload.standalone_plugins) ? payload.standalone_plugins : [],
    frontendViews: Array.isArray(payload.frontend_views) ? payload.frontend_views : [],
    projectTools: Array.isArray(payload.project_tools) ? payload.project_tools : [],
    failures: Array.isArray(payload.failures) ? payload.failures : [],
    attachedApplicationPacks: Array.isArray(payload.attached_application_packs) ? payload.attached_application_packs : [],
    applicationRestartRequired: payload.application_restart_required === true,
    reload: payload.reload && typeof payload.reload === "object" ? payload.reload : null,
    error: "",
    request: pending || "",
  }
}

function dispatchPluginChange() {
  try {
    window.dispatchEvent(new CustomEvent("cyrene:plugins-changed", {
      detail: { source: "plugin-registry" },
    }))
  } catch (error) {}
}

var PluginFrontendService = (function () {
  var state = emptyPluginSnapshot()
  var listeners = []
  var refreshPromise = null
  var reloadPromise = null

  function snapshot() {
    return state
  }

  function notify() {
    listeners.slice().forEach(function (listener) {
      try { listener(state) } catch (error) {}
    })
    dispatchPluginChange()
  }

  function commit(next) {
    state = next
    notify()
    return state
  }

  function refresh() {
    if (refreshPromise) return refreshPromise
    state = Object.assign({}, state, { loading: true, error: "", request: "status" })
    notify()
    var api = workbenchServices.api()
    refreshPromise = api.json("/api/plugins", { toast: false }).then(function (payload) {
      return commit(normalizePluginSnapshot(payload, "status"))
    }).catch(function (error) {
      return commit(Object.assign({}, state, {
        loaded: true,
        loading: false,
        error: api.errorText(error),
        request: "status",
      }))
    }).finally(function () {
      refreshPromise = null
    })
    return refreshPromise
  }

  function reload() {
    if (reloadPromise) return reloadPromise
    state = Object.assign({}, state, { reloading: true, error: "", request: "reload" })
    notify()
    var api = workbenchServices.api()
    reloadPromise = api.json("/api/plugins/reload", {
      method: "POST",
      toast: false,
    }).then(function (payload) {
      return commit(normalizePluginSnapshot(payload, "reload"))
    }).catch(function (error) {
      commit(Object.assign({}, state, {
        reloading: false,
        error: api.errorText(error),
        request: "reload",
      }))
      throw error
    }).finally(function () {
      reloadPromise = null
    })
    return reloadPromise
  }

  function mutateTool(canonicalId, payload, remove) {
    var api = workbenchServices.api()
    var path = "/api/plugins/tools/" + encodeURIComponent(String(canonicalId || ""))
    return api.json(path, {
      method: remove ? "DELETE" : "PATCH",
      headers: remove ? undefined : { "Content-Type": "application/json" },
      body: remove ? undefined : JSON.stringify(payload || {}),
      toast: false,
    }).then(function (response) {
      return commit(normalizePluginSnapshot(response, remove ? "delete-tool" : "update-tool"))
    })
  }

  function subscribe(listener) {
    if (typeof listener !== "function") return function () {}
    listeners.push(listener)
    listener(state)
    if (!state.loaded && !state.loading) refresh()
    return function () {
      listeners = listeners.filter(function (item) { return item !== listener })
    }
  }

  return {
    refresh: refresh,
    reload: reload,
    updateTool: function (canonicalId, payload) { return mutateTool(canonicalId, payload, false) },
    deleteTool: function (canonicalId) { return mutateTool(canonicalId, null, true) },
    snapshot: snapshot,
    subscribe: subscribe,
  }
})()

function pluginLocalizedField(item, field) {
  item = item && typeof item === "object" ? item : {}
  var locale = String(document.documentElement.lang || navigator.language || "en").replace(/_/g, "-").toLowerCase()
  var language = locale.split("-", 1)[0]
  var translations = item.i18n && typeof item.i18n === "object" ? item.i18n : {}
  var value = translations[locale] || translations[language] || null
  if (!value) {
    Object.keys(translations).some(function (key) {
      var normalized = String(key).replace(/_/g, "-").toLowerCase()
      if (normalized !== locale && normalized !== language) return false
      value = translations[key]
      return true
    })
  }
  return String(value && value[field] || item[field] || "")
}

function PluginView(props) {
  var payload = props.payload && typeof props.payload === "object" ? props.payload : {}
  var packId = String(payload.packId || payload.pack_id || "")
  var viewId = String(payload.viewId || payload.view_id || "")
  var projectId = String(props.projectId || payload.projectId || payload.project_id || "")
  var instanceId = String(payload.instanceId || payload.instance_id || "default")
  var iframeRef = useRef(null)
  var [registry, setRegistry] = useState(function () { return PluginFrontendService.snapshot() })

  useEffect(function () { return PluginFrontendService.subscribe(setRegistry) }, [])
  var view = useMemo(function () {
    return (registry.frontendViews || []).find(function (item) {
      return String(item && item.pack_id || "") === packId
        && String(item && item.id || "") === viewId
    }) || null
  }, [registry, packId, viewId])

  useEffect(function () {
    function post(message) {
      var frame = iframeRef.current
      if (frame && frame.contentWindow) frame.contentWindow.postMessage(message, "*")
    }
    function onMessage(event) {
      var frame = iframeRef.current
      if (!frame || event.source !== frame.contentWindow) return
      if (event.origin !== window.location.origin && event.origin !== "null") return
      var message = event.data && typeof event.data === "object" ? event.data : {}
      if (message.source !== "cyrene-plugin" || message.type !== "call") return
      var requestId = String(message.requestId || "")
      workbenchServices.api().json(
        "/api/plugins/packs/" + encodeURIComponent(packId) + "/call",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            method: String(message.method || ""),
            args: message.args,
            project_id: projectId,
          }),
          toast: false,
        }
      ).then(function (response) {
        post({ source: "cyrene-host", type: "response", requestId: requestId, ok: true, result: response && response.result })
      }).catch(function (error) {
        post({ source: "cyrene-host", type: "response", requestId: requestId, ok: false, error: workbenchServices.api().errorText(error) })
      })
    }
    window.addEventListener("message", onMessage)
    return function () { window.removeEventListener("message", onMessage) }
  }, [packId, projectId, instanceId])

  if (registry.loading && !registry.loaded) {
    return <div className="wbc-plugin-view-state" role="status">{pluginLocalizedField({ title: "Loading Plugin…", i18n: { zh: { title: "正在加载插件…" } } }, "title")}</div>
  }
  if (!view) {
    return <div className="wbc-plugin-view-state" role="status">
      <strong>{payload.title || viewId || packId || "Plugin"}</strong>
      <span>{pluginLocalizedField({ title: "This Plugin view is disabled or unavailable.", i18n: { zh: { title: "该插件视图已禁用或不可用。" } } }, "title")}</span>
    </div>
  }
  var entry = String(view.entry || "")
  var src = "/api/plugins/packs/" + encodeURIComponent(packId) + "/assets/"
    + entry.split("/").map(encodeURIComponent).join("/")
  return <iframe
    ref={iframeRef}
    className="wbc-plugin-view-frame"
    src={src}
    title={pluginLocalizedField(view, "title") || viewId || packId}
    sandbox="allow-scripts allow-forms allow-modals allow-downloads allow-popups"
    onLoad={function () {
      if (!iframeRef.current || !iframeRef.current.contentWindow) return
      iframeRef.current.contentWindow.postMessage({
        source: "cyrene-host",
        type: "init",
        context: {
          packId: packId,
          projectId: projectId,
          viewId: viewId,
          instanceId: instanceId,
          state: payload.state == null ? null : payload.state,
        },
      }, "*")
    }}
  />
}

window.CyreneUI.plugins = window.CyreneUI.register("plugins", PluginFrontendService)

export { PluginFrontendService, PluginView, pluginLocalizedField }
