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

function CustomPluginsPanel(props) {
  var t = props.t
  var project = props.project || null
  var projectId = String(project && project.id || "")
  var [plugins, setPlugins] = useStateSt([])
  var [query, setQuery] = useStateSt("")
  var [loading, setLoading] = useStateSt(false)
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
    if (message) window.setTimeout(function () { setNotice("") }, 5000)
  }

  function load() {
    setLoading(true)
    return settingsFetch("/api/plugins" + (projectId ? "?project_id=" + encodeURIComponent(projectId) : ""))
      .then(readSettingsResponse)
      .then(function (payload) { setPlugins(Array.isArray(payload.plugins) ? payload.plugins : []) })
      .catch(function (error) { tell(error.message, "error") })
      .finally(function () { setLoading(false) })
  }

  useEffectSt(function () { load() }, [projectId])
  useEffectSt(function () {
    function refresh(event) {
      var changedProject = String(event && event.detail && event.detail.projectId || "")
      if (!changedProject || !projectId || changedProject === projectId) load()
    }
    window.addEventListener("cyrene:plugins-changed", refresh)
    return function () { window.removeEventListener("cyrene:plugins-changed", refresh) }
  }, [projectId])

  function installPlugin() {
    if (!(window.cyrene && typeof window.cyrene.pickExtensionPath === "function")) {
      tell(t("settings.pluginDesktopRequired", "Select a plugin folder in the desktop app, or use the plugin install API."), "error")
      return
    }
    window.cyrene.pickExtensionPath({
      directory: true,
      title: t("settings.pluginInstall", "Install project plugin"),
    }).then(function (picked) {
      if (!picked || picked.cancelled || !picked.path) return null
      return workbenchServices.feedback().confirmModal({
        title: t("settings.pluginTrustTitle", "Trust and install this plugin?"),
        body: t("settings.pluginTrustBody", "When enabled, the plugin can read project files, access the network, and start local processes. Install only code you trust.") + "\n\n" + picked.path,
        confirmLabel: t("settings.install", "Install"),
        danger: true,
      }).then(function (confirmed) {
        if (!confirmed) return null
        setBusy("install")
        return settingsFetch("/api/plugins/install", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: picked.path }),
        }).then(readSettingsResponse).then(function () {
          tell(t("settings.pluginInstalled", "Plugin installed"), "success")
          return load()
        })
      })
    }).catch(function (error) { tell(error.message, "error") })
      .finally(function () { setBusy("") })
  }

  function togglePlugin(item) {
    if (!projectId) {
      tell(t("settings.pluginProjectRequired", "Select a project first"), "error")
      return
    }
    setBusy("plugin:" + item.id)
    settingsFetch("/api/plugins/" + encodeURIComponent(item.id) + "/enabled", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ projectId: projectId, enabled: !item.enabled }),
    }).then(readSettingsResponse).then(function () {
      return workbenchServices.plugins().refresh(projectId)
    }).then(load).catch(function (error) { tell(error.message, "error") })
      .finally(function () { setBusy("") })
  }

  function reloadPlugin(item) {
    if (!projectId) return
    setBusy("plugin:" + item.id)
    settingsFetch("/api/plugins/" + encodeURIComponent(item.id) + "/reload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ projectId: projectId }),
    }).then(readSettingsResponse).then(function () {
      return workbenchServices.plugins().refresh(projectId)
    }).then(load).catch(function (error) { tell(error.message, "error") })
      .finally(function () { setBusy("") })
  }

  function deletePlugin(item, deleteData) {
    workbenchServices.feedback().confirmModal({
      title: deleteData
        ? t("settings.pluginDeleteDataTitle", "Delete plugin and data")
        : t("settings.pluginDeleteTitle", "Delete plugin"),
      body: deleteData
        ? t("settings.pluginDeleteDataBody", "The plugin package, data for every project, and logs will be permanently deleted.")
        : t("settings.pluginDeleteBody", "The plugin package will be deleted, while plugin data and logs are retained."),
      confirmLabel: t("settings.delete", "Delete"),
      danger: true,
    }).then(function (confirmed) {
      if (!confirmed) return null
      setBusy("plugin:" + item.id)
      return settingsFetch("/api/plugins/" + encodeURIComponent(item.id) + "?delete_data=" + (deleteData ? "true" : "false"), {
        method: "DELETE",
      }).then(readSettingsResponse).then(function () {
        if (projectId) return workbenchServices.plugins().refresh(projectId)
        return null
      }).then(load).catch(function (error) { tell(error.message, "error") })
        .finally(function () { setBusy("") })
    })
  }

  var normalizedQuery = query.trim().toLowerCase()
  var filtered = plugins.filter(function (item) {
    return !normalizedQuery || [item.name, item.id, item.description, item.version]
      .join(" ").toLowerCase().indexOf(normalizedQuery) >= 0
  })

  return React.createElement("div", {
    className: "settings-panel wb-extensions-page wb-custom-plugins-page",
    id: "setting-custom-plugins",
  },
    React.createElement("header", { className: "wb-extensions-header" },
      SectionTitle(
        t("settings.customPlugins", "Custom plugins"),
        t("settings.customPluginsHint", "Install trusted plugins and control them independently for the current project.")
      ),
      React.createElement("button", {
        type: "button",
        className: "wb-btn primary wb-extension-install-button",
        disabled: busy === "install",
        onClick: installPlugin,
      }, busy === "install" ? t("settings.loading") : t("settings.pluginInstall", "Install project plugin"))
    ),
    React.createElement("div", { className: "wb-custom-plugins-scope" },
      React.createElement("strong", null, t("settings.pluginCurrentProject", "Current project")),
      React.createElement("span", null, project ? String(project.name || project.id) : t("settings.pluginNoProject", "No project selected")),
      React.createElement("small", null, t("settings.pluginScopeHint", "The switch controls whether the plugin runs and appears under Tools in this project."))
    ),
    React.createElement("div", { className: "wb-extension-filter" },
      React.createElement("input", {
        className: "wb-input",
        value: query,
        onChange: function (event) { setQuery(event.target.value) },
        placeholder: t("settings.extensionFilter"),
        "aria-label": t("settings.extensionFilter"),
      }),
      React.createElement("span", null, t("settings.extensionCount", { n: filtered.length }))
    ),
    notice && React.createElement("div", {
      className: "wb-extension-notice " + noticeKind,
      role: noticeKind === "error" ? "alert" : "status",
    }, notice),
    React.createElement("div", {
      className: "wb-extension-list wb-project-plugin-list",
      "aria-busy": loading ? "true" : "false",
    },
      loading && !filtered.length && React.createElement("div", { className: "wb-extensions-empty" }, t("settings.loading")),
      !loading && !filtered.length && React.createElement("div", { className: "wb-extensions-empty" }, t("settings.pluginEmpty", "No project plugins installed")),
      filtered.map(function (item) {
        var itemBusy = busy === "plugin:" + item.id
        return React.createElement("article", { key: item.id, className: "wb-extension-card wb-project-plugin-card" },
          React.createElement("div", { className: "wb-extension-card-main" },
            React.createElement("span", { className: "wb-extension-glyph extension-agent", "aria-hidden": "true" },
              React.createElement("span", { className: "wb-extension-glyph-text" }, String(item.name || item.id || "P").slice(0, 1).toUpperCase())
            ),
            React.createElement("div", { className: "wb-extension-copy" },
              React.createElement("div", { className: "wb-extension-title-row" },
                React.createElement("strong", null, item.name || item.id),
                React.createElement("span", { className: "wb-extension-type" }, item.version || "0.0.0")
              ),
              React.createElement("span", { className: "wb-extension-description" }, item.description || item.id),
              React.createElement("div", { className: "wb-extension-meta" },
                React.createElement("span", { className: "wb-extension-status " + (item.state === "load-error" ? "warning" : item.enabled ? "managed" : "disabled") },
                  React.createElement("span", { className: "wb-extension-status-dot" }),
                  item.state === "load-error"
                    ? t("settings.pluginLoadError", "Failed to load")
                    : item.enabled
                      ? t("settings.pluginEnabled", "Enabled for this project")
                      : t("settings.pluginDisabled", "Disabled for this project")
                ),
                React.createElement("code", null, item.id)
              ),
              item.error && React.createElement("small", { className: "workbench-error" }, item.error)
            ),
            React.createElement("div", { className: "wb-extension-actions" },
              Toggle(item.enabled === true, function () { togglePlugin(item) }, itemBusy || !projectId, item.name || item.id)
            )
          ),
          React.createElement("div", { className: "wb-project-plugin-actions" },
            React.createElement("button", { type: "button", className: "wb-btn", disabled: itemBusy || !item.enabled || !projectId, onClick: function () { reloadPlugin(item) } }, t("settings.pluginReload", "Reload")),
            React.createElement("button", { type: "button", className: "wb-btn danger", disabled: itemBusy, onClick: function () { deletePlugin(item, false) } }, t("settings.pluginDeleteKeepData", "Delete and retain data")),
            React.createElement("button", { type: "button", className: "wb-btn danger", disabled: itemBusy, onClick: function () { deletePlugin(item, true) } }, t("settings.pluginDeleteWithData", "Delete plugin and data"))
          )
        )
      })
    )
  )
}

export { CustomPluginsPanel }
