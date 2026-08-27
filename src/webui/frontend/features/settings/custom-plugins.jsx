import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  readSettingsResponse,
  settingsFetch,
  showSettingsToast,
  SectionTitle,
  Toggle,
} from "./shared.jsx"

function configuredEnabled(item) {
  return !!item && item.configured_enabled === true
}

function effectiveEnabled(item) {
  return !!item && item.effective_enabled === true
}

function itemLabel(item) {
  return String(item && (item.name || item.id) || "Plugin")
}

function searchable(item) {
  if (!item || typeof item !== "object") return ""
  return [item.id, item.name, item.description, item.kind, item.source, item.source_path]
    .join(" ").toLowerCase()
}

function RegistryBadges(props) {
  var item = props.item || {}
  var t = props.t
  var kind = String(item.kind || (item.plugins ? "pack" : "plugin"))
  return React.createElement(React.Fragment, null,
    React.createElement("span", { className: "wb-extension-type" }, t("settings.pluginKind." + kind, kind)),
    item.source && React.createElement("span", { className: "wb-extension-type", title: item.source_path || "" }, t("settings.pluginSourceKind." + item.source, String(item.source))),
    kind === "model" && React.createElement("span", { className: "wb-extension-type wb-registry-model-badge" }, t("settings.pluginModel", "Model")),
    item.source === "core" && React.createElement("span", { className: "wb-extension-type wb-registry-core-badge" }, t("settings.pluginCore", "Core")),
    item.locked === true && React.createElement("span", { className: "wb-extension-type wb-registry-locked-badge" }, t("settings.pluginLocked", "Locked")),
  )
}

function RegistryMember(props) {
  var item = props.item || {}
  var t = props.t
  return React.createElement("div", { className: "wb-registry-member" },
    React.createElement("div", { className: "wb-registry-member-copy" },
      React.createElement("strong", null, itemLabel(item)),
      React.createElement("code", { title: item.source_path || "" }, String(item.name || item.id || "")),
    ),
    React.createElement("div", { className: "wb-extension-title-row" },
      React.createElement(RegistryBadges, { item: item, t: t }),
      React.createElement("span", {
        className: "wb-extension-status " + (effectiveEnabled(item) ? "managed" : "disabled"),
      },
        React.createElement("span", { className: "wb-extension-status-dot" }),
        effectiveEnabled(item) ? t("settings.pluginEnabled", "Enabled") : t("settings.pluginDisabled", "Disabled"),
      ),
    ),
  )
}

function PluginRegistryPanel(props) {
  var t = props.t
  var pluginService = workbenchServices.plugins()
  var [registry, setRegistry] = useStateSt(function () { return pluginService.snapshot() })
  var [query, setQuery] = useStateSt("")
  var [busy, setBusy] = useStateSt("")
  var [notice, setNotice] = useStateSt("")
  var [noticeKind, setNoticeKind] = useStateSt("info")

  function tell(message, kind) {
    if (showSettingsToast(message, kind || "info")) {
      setNotice("")
      return
    }
    setNotice(message || "")
    setNoticeKind(kind || "info")
  }

  useEffectSt(function () {
    return pluginService.subscribe(setRegistry)
  }, [])

  function reloadRegistry() {
    setBusy("reload")
    setNotice("")
    pluginService.reload().then(function (next) {
      if (next.applicationRestartRequired) {
        tell(t("settings.pluginRestartRequired", "Plugin files were reloaded. Restart the application to apply Application Host changes."), "info")
      } else {
        tell(t("settings.pluginReloaded", "Plugin registry reloaded"), "success")
      }
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
    }).finally(function () {
      setBusy("")
    })
  }

  function updateEnabled(kind, item, nextEnabled) {
    if (!item || item.locked === true || busy) return
    var id = String(kind === "pack" ? item.id : item.name || item.id || "")
    if (!id) return
    var payload = kind === "pack" ? { packs: {} } : { plugins: {} }
    payload[kind === "pack" ? "packs" : "plugins"][id] = nextEnabled
    setBusy(kind + ":" + id)
    setNotice("")
    settingsFetch("/api/settings/plugins", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(readSettingsResponse).then(function () {
      return pluginService.refresh()
    }).then(function () {
      tell(t("settings.saved", "Saved"), "success")
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
    }).finally(function () {
      setBusy("")
    })
  }

  var normalizedQuery = query.trim().toLowerCase()
  var packs = (Array.isArray(registry.packs) ? registry.packs : []).filter(function (pack) {
    if (!normalizedQuery) return true
    if (searchable(pack).indexOf(normalizedQuery) >= 0) return true
    return (Array.isArray(pack.plugins) ? pack.plugins : []).some(function (item) {
      return searchable(item).indexOf(normalizedQuery) >= 0
    })
  })
  var standalone = (Array.isArray(registry.standalonePlugins) ? registry.standalonePlugins : []).filter(function (item) {
    return !normalizedQuery || searchable(item).indexOf(normalizedQuery) >= 0
  })
  var failures = Array.isArray(registry.failures) ? registry.failures : []
  var directory = registry.directory && typeof registry.directory === "object" ? registry.directory : {}
  var attachedPacks = Array.isArray(registry.attachedApplicationPacks) ? registry.attachedApplicationPacks : []
  var resultCount = packs.length + standalone.length

  return React.createElement("div", {
    className: "settings-panel wb-plugin-registry-page",
    id: "setting-plugin-registry",
  },
    React.createElement("header", { className: "wb-extensions-header" },
      SectionTitle(
        t("settings.pluginRegistry", "Plugin Registry"),
        t("settings.pluginRegistryHint", "Inspect Plugin packs and standalone Plugins loaded from the editable Plugin directory.")
      ),
      React.createElement("button", {
        type: "button",
        className: "wb-btn primary wb-extension-install-button",
        disabled: busy === "reload" || registry.reloading,
        onClick: reloadRegistry,
      }, busy === "reload" || registry.reloading ? t("settings.loading", "Loading…") : t("settings.pluginReload", "Reload"))
    ),
    React.createElement("section", { className: "wb-plugin-registry-directory" },
      React.createElement("strong", null, t("settings.pluginDirectory", "Plugin directory")),
      React.createElement("code", { title: directory.path || "" }, directory.path || t("settings.pluginDirectoryUnavailable", "Unavailable")),
      React.createElement("small", null,
        [
          t("settings.pluginDirectoryStatus." + (directory.status || (directory.exists ? "ready" : "missing")), directory.status || (directory.exists ? "ready" : "missing")),
          directory.readable === false ? t("settings.pluginDirectoryUnreadable", "not readable") : "",
          directory.writable === false ? t("settings.pluginDirectoryReadOnly", "read-only") : "",
          directory.seeded === true ? t("settings.pluginDirectorySeeded", "seeded") : "",
        ].filter(Boolean).join(" · ")
      ),
    ),
    attachedPacks.length > 0 && React.createElement("div", { className: "wb-registry-attached-packs" },
      React.createElement("strong", null, t("settings.pluginApplicationPacks", "Application Host packs")),
      attachedPacks.map(function (item, index) {
        var label = typeof item === "string" ? item : itemLabel(item)
        return React.createElement("span", { key: label + ":" + index, className: "wb-extension-type" }, label)
      }),
    ),
    React.createElement("div", { className: "wb-extension-filter" },
      React.createElement("input", {
        className: "wb-input",
        value: query,
        onChange: function (event) { setQuery(event.target.value) },
        placeholder: t("settings.pluginFilter", "Filter Plugins"),
        "aria-label": t("settings.pluginFilter", "Filter Plugins"),
      }),
      React.createElement("span", null, t("settings.pluginCount", { n: resultCount }, String(resultCount) + " items"))
    ),
    (notice || registry.error) && React.createElement("div", {
      className: "wb-extension-notice " + (registry.error ? "error" : noticeKind),
      role: registry.error || noticeKind === "error" ? "alert" : "status",
    }, registry.error || notice),
    React.createElement("section", { className: "wb-registry-section", "aria-busy": registry.loading ? "true" : "false" },
      React.createElement("div", { className: "wb-registry-section-heading" },
        React.createElement("strong", null, t("settings.pluginPacks", "Plugin packs")),
        React.createElement("span", null, String(packs.length)),
      ),
      registry.loading && !registry.loaded && React.createElement("div", { className: "wb-extensions-empty" }, t("settings.loading", "Loading…")),
      !registry.loading && !packs.length && React.createElement("div", { className: "wb-extensions-empty" }, t("settings.pluginPacksEmpty", "No plugin packs found")),
      React.createElement("div", { className: "wb-extension-list" }, packs.map(function (pack) {
        var packId = String(pack.id || "")
        var packBusy = busy === "pack:" + packId
        var members = Array.isArray(pack.plugins) ? pack.plugins : []
        return React.createElement("article", { key: packId, className: "wb-extension-card wb-registry-pack-card" },
          React.createElement("div", { className: "wb-extension-card-main" },
            React.createElement("span", { className: "wb-extension-glyph extension-agent", "aria-hidden": "true" },
              React.createElement("span", { className: "wb-extension-glyph-text" }, itemLabel(pack).slice(0, 1).toUpperCase())
            ),
            React.createElement("div", { className: "wb-extension-copy" },
              React.createElement("div", { className: "wb-extension-title-row" },
                React.createElement("strong", null, itemLabel(pack)),
                React.createElement(RegistryBadges, { item: pack, t: t }),
              ),
              React.createElement("span", { className: "wb-extension-description" }, pack.description || packId),
              React.createElement("div", { className: "wb-extension-meta" },
                React.createElement("span", { className: "wb-extension-status " + (effectiveEnabled(pack) ? "managed" : "disabled") },
                  React.createElement("span", { className: "wb-extension-status-dot" }),
                  effectiveEnabled(pack) ? t("settings.pluginEnabled", "Enabled") : t("settings.pluginDisabled", "Disabled")
                ),
                React.createElement("code", { title: pack.source_path || "" }, packId),
                React.createElement("span", null, t("settings.pluginMemberCount", { n: members.length }, String(members.length) + " plugins")),
              ),
            ),
            React.createElement("div", { className: "wb-extension-actions" },
              Toggle(configuredEnabled(pack), function () {
                updateEnabled("pack", pack, !configuredEnabled(pack))
              }, packBusy || pack.locked === true, itemLabel(pack))
            ),
          ),
          members.length > 0 && React.createElement("div", { className: "wb-registry-pack-members" }, members.map(function (item) {
            return React.createElement(RegistryMember, { key: item.name || item.id, item: item, t: t })
          })),
        )
      }))
    ),
    React.createElement("section", { className: "wb-registry-section" },
      React.createElement("div", { className: "wb-registry-section-heading" },
        React.createElement("strong", null, t("settings.pluginStandalone", "Standalone Plugins")),
        React.createElement("span", null, String(standalone.length)),
      ),
      !standalone.length && React.createElement("div", { className: "wb-extensions-empty" }, t("settings.pluginStandaloneEmpty", "No standalone Plugins found")),
      React.createElement("div", { className: "wb-extension-list" }, standalone.map(function (item) {
        var id = String(item.name || item.id || "")
        var itemBusy = busy === "plugin:" + id
        return React.createElement("article", { key: id, className: "wb-extension-card" },
          React.createElement("div", { className: "wb-extension-card-main" },
            React.createElement("span", { className: "wb-extension-glyph", "aria-hidden": "true" },
              React.createElement("span", { className: "wb-extension-glyph-text" }, itemLabel(item).slice(0, 1).toUpperCase())
            ),
            React.createElement("div", { className: "wb-extension-copy" },
              React.createElement("div", { className: "wb-extension-title-row" },
                React.createElement("strong", null, itemLabel(item)),
                React.createElement(RegistryBadges, { item: item, t: t }),
              ),
              React.createElement("span", { className: "wb-extension-description" }, item.description || item.desc || id),
              React.createElement("div", { className: "wb-extension-meta" },
                React.createElement("span", { className: "wb-extension-status " + (effectiveEnabled(item) ? "managed" : "disabled") },
                  React.createElement("span", { className: "wb-extension-status-dot" }),
                  effectiveEnabled(item) ? t("settings.pluginEnabled", "Enabled") : t("settings.pluginDisabled", "Disabled")
                ),
                React.createElement("code", { title: item.source_path || "" }, id),
              ),
            ),
            React.createElement("div", { className: "wb-extension-actions" },
              Toggle(configuredEnabled(item), function () {
                updateEnabled("plugin", item, !configuredEnabled(item))
              }, itemBusy || item.locked === true, itemLabel(item))
            ),
          ),
        )
      }))
    ),
    failures.length > 0 && React.createElement("section", { className: "wb-registry-section wb-registry-failures" },
      React.createElement("div", { className: "wb-registry-section-heading" },
        React.createElement("strong", null, t("settings.pluginLoadFailures", "Load failures")),
        React.createElement("span", null, String(failures.length)),
      ),
      React.createElement("ul", null, failures.map(function (failure, index) {
        return React.createElement("li", { key: String(failure.path || failure.pack_id || index) },
          React.createElement("div", { className: "wb-registry-failure-head" },
            React.createElement("code", null, failure.pack_id || failure.path || t("settings.pluginUnknown", "unknown")),
            React.createElement("b", null, t("settings.pluginFailureStage." + failure.stage, failure.stage || t("settings.pluginLoadError", "load error"))),
          ),
          failure.source && React.createElement("small", null, t("settings.pluginSource", { source: failure.source }, "Source: {source}")),
          failure.path && React.createElement("small", null, failure.path),
          React.createElement("pre", null, String(failure.error || t("settings.pluginLoadError", "Failed to load"))),
        )
      }))
    ),
  )
}

export { PluginRegistryPanel }
