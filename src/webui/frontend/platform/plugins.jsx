import { workbenchServices } from "../shared/runtime/services.jsx"

var { useEffect, useMemo, useRef, useState } = React

function encodePluginQuery(value) {
  return encodeURIComponent(String(value || ""))
}

var PluginFrontendService = (function () {
  var snapshots = Object.create(null)
  var listeners = Object.create(null)
  var eventSources = Object.create(null)

  function notify(projectId) {
    ;(listeners[projectId] || []).slice().forEach(function (listener) {
      try { listener(snapshot(projectId)) } catch (error) {}
    })
    try {
      window.dispatchEvent(new CustomEvent("cyrene:plugins-changed", {
        detail: { projectId: projectId },
      }))
    } catch (error) {}
  }

  function snapshot(projectId) {
    var state = snapshots[String(projectId || "")]
    return state || { loading: false, contributions: [], plugins: [], error: "" }
  }

  function refresh(projectId) {
    projectId = String(projectId || "")
    if (!projectId) return Promise.resolve(snapshot(projectId))
    snapshots[projectId] = Object.assign({}, snapshot(projectId), { loading: true, error: "" })
    notify(projectId)
    var api = workbenchServices.api()
    return Promise.all([
      api.json("/api/plugins?project_id=" + encodePluginQuery(projectId), { toast: false }),
      api.json("/api/plugins/contributions?project_id=" + encodePluginQuery(projectId), { toast: false }),
    ]).then(function (payloads) {
      snapshots[projectId] = {
        loading: false,
        plugins: Array.isArray(payloads[0] && payloads[0].plugins) ? payloads[0].plugins : [],
        contributions: Array.isArray(payloads[1] && payloads[1].contributions) ? payloads[1].contributions : [],
        error: "",
      }
      connectEvents(projectId)
      notify(projectId)
      return snapshots[projectId]
    }).catch(function (error) {
      snapshots[projectId] = Object.assign({}, snapshot(projectId), {
        loading: false,
        error: api.errorText(error),
      })
      notify(projectId)
      return snapshots[projectId]
    })
  }

  function connectEvents(projectId) {
    if (eventSources[projectId] || typeof EventSource === "undefined") return
    var source = new EventSource("/api/plugins/events?project_id=" + encodePluginQuery(projectId))
    eventSources[projectId] = source
    source.onmessage = function (event) {
      var payload = null
      try { payload = JSON.parse(event.data) } catch (error) {}
      if (!payload) return
      try {
        window.dispatchEvent(new CustomEvent("cyrene:plugin-event", { detail: payload }))
      } catch (error) {}
      if (payload.type === "plugin-state" || payload.type === "plugin-removed") refresh(projectId)
    }
    source.onerror = function () {
      if (source.readyState === EventSource.CLOSED) {
        delete eventSources[projectId]
      }
    }
  }

  function subscribe(projectId, listener) {
    projectId = String(projectId || "")
    ;(listeners[projectId] = listeners[projectId] || []).push(listener)
    listener(snapshot(projectId))
    if (!snapshot(projectId).loading && !snapshot(projectId).contributions.length) refresh(projectId)
    return function () {
      listeners[projectId] = (listeners[projectId] || []).filter(function (item) { return item !== listener })
    }
  }

  function contributions(projectId, point) {
    return snapshot(projectId).contributions.filter(function (item) {
      return !point || String(item && item.point || "") === String(point)
    })
  }

  function view(projectId, pluginId, viewId) {
    return contributions(projectId, "cyrene.view").find(function (item) {
      return String(item.pluginId || "") === String(pluginId || "")
        && String(item.id || "") === String(viewId || "")
    }) || null
  }

  function call(pluginId, projectId, method, args, timeout) {
    return workbenchServices.api().json(
      "/api/plugins/" + encodePluginQuery(pluginId) + "/call",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          projectId: projectId,
          method: method,
          args: args,
          timeout: timeout || 120,
        }),
      }
    ).then(function (payload) { return payload && payload.result })
  }

  return {
    call: call,
    contributions: contributions,
    refresh: refresh,
    snapshot: snapshot,
    subscribe: subscribe,
    view: view,
  }
})()

function PluginView(props) {
  var payload = props.payload && typeof props.payload === "object" ? props.payload : {}
  var projectId = String(props.projectId || payload.projectId || "")
  var pluginId = String(payload.pluginId || "")
  var viewId = String(payload.viewId || "")
  var instanceId = String(payload.instanceId || "default")
  var iframeRef = useRef(null)
  var [snapshot, setSnapshot] = useState(function () { return PluginFrontendService.snapshot(projectId) })

  useEffect(function () {
    return PluginFrontendService.subscribe(projectId, setSnapshot)
  }, [projectId])

  var view = useMemo(function () {
    return PluginFrontendService.view(projectId, pluginId, viewId)
  }, [snapshot, projectId, pluginId, viewId])

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
      PluginFrontendService.call(pluginId, projectId, String(message.method || ""), message.args, message.timeout)
        .then(function (result) {
          post({ source: "cyrene-host", type: "response", requestId: requestId, ok: true, result: result })
        }).catch(function (error) {
          post({ source: "cyrene-host", type: "response", requestId: requestId, ok: false, error: String(error && error.message || error) })
        })
    }
    function onPluginEvent(event) {
      var detail = event && event.detail || {}
      if (String(detail.projectId || "") !== projectId || String(detail.pluginId || "") !== pluginId) return
      post({ source: "cyrene-host", type: "event", event: detail.event, payload: detail.payload })
    }
    window.addEventListener("message", onMessage)
    window.addEventListener("cyrene:plugin-event", onPluginEvent)
    return function () {
      window.removeEventListener("message", onMessage)
      window.removeEventListener("cyrene:plugin-event", onPluginEvent)
    }
  }, [projectId, pluginId, instanceId])

  if (snapshot.loading && !view) {
    return <div className="wbc-plugin-view-state" role="status">正在加载插件…</div>
  }
  if (!view) {
    var installed = (snapshot.plugins || []).find(function (item) { return String(item.id || "") === pluginId })
    var message = installed
      ? (installed.enabled ? (installed.error || "插件视图不可用") : "插件已禁用")
      : "插件未安装"
    return <div className="wbc-plugin-view-state" role="status">
      <strong>{payload.title || viewId || pluginId || "插件"}</strong>
      <span>{message}</span>
      {installed && !installed.enabled ? <button type="button" onClick={function () {
        window.dispatchEvent(new CustomEvent("cyrene:open-settings", { detail: { tab: "custom-plugins" } }))
      }}>管理插件</button> : null}
    </div>
  }
  var entry = String(view.entry || "")
  if (!entry) return <div className="wbc-plugin-view-state">插件没有声明 UI 入口</div>
  var src = "/api/plugins/" + encodePluginQuery(pluginId) + "/projects/" + encodePluginQuery(projectId) + "/assets/" + entry.split("/").map(encodePluginQuery).join("/")
    + "?project_id=" + encodePluginQuery(projectId)
    + "&view_id=" + encodePluginQuery(viewId)
    + "&instance_id=" + encodePluginQuery(instanceId)
  return <iframe
    ref={iframeRef}
    className="wbc-plugin-view-frame"
    src={src}
    title={String(view.title || payload.title || viewId || pluginId)}
    sandbox="allow-scripts allow-forms allow-modals allow-downloads allow-popups"
    onLoad={function () {
      if (!iframeRef.current || !iframeRef.current.contentWindow) return
      iframeRef.current.contentWindow.postMessage({
        source: "cyrene-host",
        type: "init",
        context: {
          pluginId: pluginId,
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

export { PluginFrontendService, PluginView }
