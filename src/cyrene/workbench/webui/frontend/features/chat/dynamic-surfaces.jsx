import { PluginFrontendService, PluginView, pluginLocalizedField } from "../../platform/plugins.jsx"
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

var NATIVE_SURFACE_RENDERERS = Object.freeze({
  "resource-summary": WbcResourceSummarySurface,
})

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
    var intents = wbcSurfaceIntentsFromActivity(
      event,
      PluginFrontendService.snapshot().workbenchSurfaces
    )
    intents.forEach(function (intent) {
      window.dispatchEvent(new window.CustomEvent(WBC_SURFACE_INTENT_EVENT, { detail: intent }))
    })
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
  normalizeSurfaceDescriptor,
}
