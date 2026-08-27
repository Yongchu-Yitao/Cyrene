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

function currentLanguage() {
  try { return window.CyreneUI.require("i18n").getLang() || "en" } catch (error) { return "en" }
}

function authoredTranslation(item, field) {
  var translations = item && item.i18n && typeof item.i18n === "object" ? item.i18n : {}
  var localized = translations[currentLanguage()] || translations[currentLanguage().split("-")[0]] || {}
  return String(localized && localized[field] || "")
}

function itemLabel(item, t) {
  if (!item) return "Plugin"
  if (item.customized_name === true) return String(item.name || item.id || "Plugin")
  var fallback = authoredTranslation(item, "name") || String(item.name || item.id || "Plugin")
  if (!t) return fallback
  return t("toolName." + String(item.id || item.name || ""), fallback)
}

function packLabel(item, t) {
  var fallback = authoredTranslation(item, "name") || String(item && (item.name || item.id) || "Plugin")
  return t ? t("toolName." + String(item && item.id || ""), fallback) : fallback
}

function itemDescription(item, t, pack) {
  var id = String(item && (item.id || item.name) || "")
  var authored = authoredTranslation(item, "description")
  if (pack) return t("pluginPackDesc." + id, authored || item.description || id)
  if (currentLanguage() === "zh" && !authored) {
    return t("toolDesc." + id, t("settings.pluginToolDescriptionFallback", { name: itemLabel(item, t) }, "Agent 可使用的 {name} 工具。"))
  }
  return t("toolDesc." + id, authored || item.description || id)
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
      React.createElement("strong", null, itemLabel(item, t)),
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
      item.kind === "tool" && React.createElement(ToolActions, {
        item: item,
        t: t,
        onUpdate: props.onUpdate,
        onDelete: props.onDelete,
      }),
    ),
  )
}

function MoreIcon() {
  return React.createElement("svg", { viewBox: "0 0 24 24", width: 18, height: 18, "aria-hidden": "true" },
    React.createElement("circle", { cx: 5, cy: 12, r: 1.7, fill: "currentColor" }),
    React.createElement("circle", { cx: 12, cy: 12, r: 1.7, fill: "currentColor" }),
    React.createElement("circle", { cx: 19, cy: 12, r: 1.7, fill: "currentColor" })
  )
}

function ToolActions(props) {
  var item = props.item || {}
  var t = props.t
  var [open, setOpen] = useStateSt(false)
  var [dialog, setDialog] = useStateSt("")
  var [name, setName] = useStateSt(String(item.name || item.id || ""))
  var [description, setDescription] = useStateSt(String(item.description || ""))
  var [error, setError] = useStateSt("")
  var [busy, setBusy] = useStateSt(false)
  var locked = item.locked === true
  var direct = item.agent_exposure === "direct"

  function beginEdit() {
    setName(String(item.name || item.id || ""))
    setDescription(String(item.description || ""))
    setError("")
    setOpen(false)
    setDialog("edit")
  }

  function saveEdit(event) {
    event.preventDefault()
    var cleanName = name.trim()
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(cleanName)) {
      setError(t("settings.pluginToolNameInvalid", "Use 1–128 letters, numbers, dots, underscores, or hyphens."))
      return
    }
    setBusy(true)
    setError("")
    Promise.resolve(props.onUpdate(item, { name: cleanName, description: description.trim() })).then(function () {
      setDialog("")
    }).catch(function (reason) {
      setError(reason && reason.message || String(reason))
    }).finally(function () { setBusy(false) })
  }

  function changeExposure() {
    setOpen(false)
    setBusy(true)
    Promise.resolve(props.onUpdate(item, { agent_exposure: direct ? "discoverable" : "direct" }))
      .catch(function (reason) { setError(reason && reason.message || String(reason)) })
      .finally(function () { setBusy(false) })
  }

  function removeTool() {
    setBusy(true)
    setError("")
    Promise.resolve(props.onDelete(item)).then(function () {
      setDialog("")
    }).catch(function (reason) {
      setError(reason && reason.message || String(reason))
    }).finally(function () { setBusy(false) })
  }

  return React.createElement("div", { className: "wb-tool-menu-wrap" },
    React.createElement("button", {
      type: "button",
      className: "wb-tool-menu-trigger",
      onClick: function () { setOpen(!open) },
      "aria-label": t("settings.pluginToolMenu", { name: itemLabel(item, t) }, "Tool menu for {name}"),
      "aria-expanded": open ? "true" : "false",
      disabled: busy,
    }, React.createElement(MoreIcon)),
    open && React.createElement("div", { className: "wb-tool-menu", role: "menu" },
      React.createElement("button", { type: "button", role: "menuitem", disabled: locked, onClick: beginEdit }, t("settings.pluginToolEdit", "Edit")),
      React.createElement("button", { type: "button", role: "menuitem", disabled: locked, onClick: changeExposure }, direct
        ? t("settings.pluginToolDiscoverable", "Let Agent find and use")
        : t("settings.pluginToolDirect", "Make directly visible to Agent")),
      React.createElement("div", { className: "wb-tool-menu-separator" }),
      React.createElement("button", { type: "button", role: "menuitem", className: "danger", disabled: locked, onClick: function () { setOpen(false); setDialog("delete"); setError("") } }, t("settings.pluginToolDelete", "Delete")),
      locked && React.createElement("small", null, t("settings.pluginToolLockedHint", "Core and model tools are managed by the framework."))
    ),
    dialog && React.createElement("div", { className: "wb-tool-dialog-backdrop", onMouseDown: function (event) { if (event.target === event.currentTarget && !busy) setDialog("") } },
      React.createElement("section", { className: "wb-tool-dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": "wb-tool-dialog-title" },
        dialog === "edit" ? React.createElement("form", { onSubmit: saveEdit },
          React.createElement("h3", { id: "wb-tool-dialog-title" }, t("settings.pluginToolEditTitle", "Edit Agent tool")),
          React.createElement("label", null,
            React.createElement("span", null, t("settings.pluginToolAgentName", "Name shown to Agent")),
            React.createElement("input", { className: "wb-input", value: name, autoFocus: true, onChange: function (event) { setName(event.target.value) }, disabled: busy })
          ),
          React.createElement("label", null,
            React.createElement("span", null, t("settings.pluginToolAgentDescription", "Description shown to Agent")),
            React.createElement("textarea", { className: "wb-input", rows: 5, value: description, onChange: function (event) { setDescription(event.target.value) }, disabled: busy })
          ),
          error && React.createElement("div", { className: "wb-tool-dialog-error", role: "alert" }, error),
          React.createElement("div", { className: "wb-tool-dialog-actions" },
            React.createElement("button", { type: "button", className: "wb-btn", disabled: busy, onClick: function () { setDialog("") } }, t("common.cancel", "Cancel")),
            React.createElement("button", { type: "submit", className: "wb-btn primary", disabled: busy }, busy ? t("settings.saving", "Saving…") : t("settings.save", "Save"))
          )
        ) : React.createElement(React.Fragment, null,
          React.createElement("h3", { id: "wb-tool-dialog-title" }, t("settings.pluginToolDeleteTitle", "Delete tool?")),
          React.createElement("p", null, t("settings.pluginToolDeleteHint", { name: itemLabel(item, t) }, "{name} will be removed from the Plugin registry.")),
          error && React.createElement("div", { className: "wb-tool-dialog-error", role: "alert" }, error),
          React.createElement("div", { className: "wb-tool-dialog-actions" },
            React.createElement("button", { type: "button", className: "wb-btn", disabled: busy, onClick: function () { setDialog("") } }, t("common.cancel", "Cancel")),
            React.createElement("button", { type: "button", className: "wb-btn danger", disabled: busy, onClick: removeTool }, busy ? t("settings.deleting", "Deleting…") : t("settings.pluginToolDelete", "Delete"))
          )
        )
      )
    )
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
    var id = String(item.id || item.name || "")
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

  function updateTool(item, values) {
    setNotice("")
    return pluginService.updateTool(item.id || item.canonical_name || item.name, values).then(function () {
      tell(t("settings.saved", "Saved"), "success")
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
      throw error
    })
  }

  function deleteTool(item) {
    setNotice("")
    return pluginService.deleteTool(item.id || item.canonical_name || item.name).then(function () {
      tell(t("settings.pluginToolDeleted", "Tool deleted"), "success")
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
      throw error
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
        var label = typeof item === "string" ? item : itemLabel(item, t)
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
              React.createElement("span", { className: "wb-extension-glyph-text" }, packLabel(pack, t).slice(0, 1).toUpperCase())
            ),
            React.createElement("div", { className: "wb-extension-copy" },
              React.createElement("div", { className: "wb-extension-title-row" },
                React.createElement("strong", null, packLabel(pack, t)),
                React.createElement(RegistryBadges, { item: pack, t: t }),
              ),
              React.createElement("span", { className: "wb-extension-description" }, itemDescription(pack, t, true)),
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
              }, packBusy || pack.locked === true, packLabel(pack, t))
            ),
          ),
          members.length > 0 && React.createElement("div", { className: "wb-registry-pack-members" }, members.map(function (item) {
            return React.createElement(RegistryMember, { key: item.id || item.name, item: item, t: t, onUpdate: updateTool, onDelete: deleteTool })
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
              React.createElement("span", { className: "wb-extension-glyph-text" }, itemLabel(item, t).slice(0, 1).toUpperCase())
            ),
            React.createElement("div", { className: "wb-extension-copy" },
              React.createElement("div", { className: "wb-extension-title-row" },
                React.createElement("strong", null, itemLabel(item, t)),
                React.createElement(RegistryBadges, { item: item, t: t }),
              ),
              React.createElement("span", { className: "wb-extension-description" }, itemDescription(item, t, false)),
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
              }, itemBusy || item.locked === true, itemLabel(item, t)),
              item.kind === "tool" && React.createElement(ToolActions, { item: item, t: t, onUpdate: updateTool, onDelete: deleteTool })
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
