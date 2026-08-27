import { workbenchServices } from "../shared/runtime/services.jsx"

var { useEffect, useState } = React

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
    failures: Array.isArray(payload.failures) ? payload.failures : [],
    attachedApplicationPacks: Array.isArray(payload.attached_application_packs) ? payload.attached_application_packs : [],
    applicationRestartRequired: payload.application_restart_required === true,
    reload: payload.reload && typeof payload.reload === "object" ? payload.reload : null,
    error: "",
    request: pending || "",
  }
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
    try {
      window.dispatchEvent(new CustomEvent("cyrene:plugins-changed", {
        detail: { source: "plugin-registry" },
      }))
    } catch (error) {}
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

  function subscribe(scope, listener) {
    if (typeof scope === "function") listener = scope
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
    snapshot: snapshot,
    subscribe: subscribe,
  }
})()

function PluginView(props) {
  var payload = props.payload && typeof props.payload === "object" ? props.payload : {}
  var pluginId = String(payload.pluginId || "")
  var viewId = String(payload.viewId || "")
  var [registry, setRegistry] = useState(function () { return PluginFrontendService.snapshot() })

  useEffect(function () {
    return PluginFrontendService.subscribe(setRegistry)
  }, [])

  if (registry.loading && !registry.loaded) {
    return <div className="wbc-plugin-view-state" role="status">正在读取插件注册表…</div>
  }

  var installed = (registry.plugins || []).find(function (item) {
    return String(item && (item.id || item.name) || "") === pluginId
  })
  var message = installed
    ? "新的插件协议不再提供嵌入式前端视图。"
    : "这个已保存的插件视图对应的插件不在当前注册表中。"
  return <div className="wbc-plugin-view-state" role="status">
    <strong>{payload.title || viewId || pluginId || "插件"}</strong>
    <span>{message}</span>
    <button type="button" onClick={function () {
      window.dispatchEvent(new CustomEvent("cyrene:open-settings", { detail: { tab: "plugin-registry" } }))
    }}>查看插件注册表</button>
  </div>
}

window.CyreneUI.plugins = window.CyreneUI.register("plugins", PluginFrontendService)

export { PluginFrontendService, PluginView }
