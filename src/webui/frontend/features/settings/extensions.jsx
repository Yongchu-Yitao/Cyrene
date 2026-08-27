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

function McpGlyph() {
  return React.createElement("svg", {
    width: "22", height: "22", viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", strokeWidth: "1.9", strokeLinecap: "round",
    strokeLinejoin: "round", "aria-hidden": "true",
  }, React.createElement("path", { d: "M8 12h8M12 8v8M5 5h4v4H5zM15 15h4v4h-4z" }))
}

function variablesToText(values) {
  if (!values || typeof values !== "object") return ""
  return Object.keys(values).sort().map(function (key) {
    return key + "=" + String(values[key] == null ? "" : values[key])
  }).join("\n")
}

function parseVariables(value, label, t) {
  var variables = {}
  String(value || "").split(/\r?\n/).forEach(function (line) {
    if (!line.trim()) return
    var separator = line.indexOf("=")
    if (separator <= 0 || !line.slice(0, separator).trim()) {
      throw new Error(t("settings.mcpVariablesInvalid", { label: label }, "{label} must use KEY=VALUE, one entry per line."))
    }
    variables[line.slice(0, separator).trim()] = line.slice(separator + 1)
  })
  return variables
}

function editableConfig(raw) {
  raw = raw && typeof raw === "object" ? raw : {}
  return {
    ...raw,
    name: String(raw.name || ""),
    transport: String(raw.transport || "stdio"),
    command: String(raw.command || ""),
    url: String(raw.url || ""),
    argsText: Array.isArray(raw.args) ? raw.args.join("\n") : "",
    variablesText: variablesToText(raw.transport === "stdio" ? raw.env : raw.headers),
    enabled: raw.enabled !== false,
  }
}

function persistedConfig(editor, t) {
  var transport = String(editor.transport || "stdio")
  var config = {
    name: String(editor.name || "").trim(),
    transport: transport,
    enabled: editor.enabled !== false,
  }
  if (transport === "stdio") {
    config.command = String(editor.command || "").trim()
    config.args = String(editor.argsText || "").split(/\r?\n/).map(function (value) {
      return value.trim()
    }).filter(Boolean)
    config.env = parseVariables(editor.variablesText, t("settings.mcpEnvironment", "Environment"), t)
  } else {
    config.url = String(editor.url || "").trim()
    config.headers = parseVariables(editor.variablesText, t("settings.mcpHeaders", "HTTP headers"), t)
  }
  return config
}

function providerStatus(server) {
  if (server && server.enabled === false) return "disabled"
  return String(server && server.status || "disconnected")
}

function ProviderCard(props) {
  var editor = props.editor
  var t = props.t
  var server = props.server || {}
  var status = providerStatus(server)
  var packId = String(server.pack_id || "mcp." + editor.name)
  var providerBusy = props.busy === "provider:" + editor.name
  var variablesLabel = editor.transport === "stdio"
    ? t("settings.mcpEnvironment", "Environment")
    : t("settings.mcpHeaders", "HTTP headers")

  function update(changes) {
    props.onChange(editor.name, { ...editor, ...changes })
  }

  return React.createElement("article", { className: "wb-plugin-card wb-mcp-provider-card" },
    React.createElement("div", { className: "wb-plugin-card-main" },
      React.createElement("span", { className: "wb-plugin-glyph", "aria-hidden": "true" }, React.createElement(McpGlyph)),
      React.createElement("div", { className: "wb-plugin-card-copy" },
        React.createElement("div", { className: "wb-plugin-title-row" },
          React.createElement("strong", null, editor.name),
          React.createElement("span", { className: "wb-plugin-badge" }, t("settings.pluginPack", "Plugin pack")),
          React.createElement("span", { className: "wb-plugin-badge" }, t("settings.pluginSourceMcp", "Source: MCP")),
        ),
        React.createElement("div", { className: "wb-plugin-meta" },
          React.createElement("span", { className: "wb-plugin-status " + status },
            React.createElement("span", { className: "wb-plugin-status-dot", "aria-hidden": "true" }),
            t("settings.mcpStatus." + status, status),
          ),
          React.createElement("code", { title: packId }, packId),
          React.createElement("span", null, t("settings.mcpPluginCount", { n: Number(server.tool_count || 0) }, "{n} Plugins")),
        ),
      ),
      Toggle(editor.enabled !== false, function () {
        props.onToggle(editor.name, editor.enabled === false)
      }, !!props.busy, t("settings.mcpToggle", { name: editor.name }, "Enable or disable {name}")),
    ),
    server.error && React.createElement("div", { className: "wb-plugin-inline-error", role: "alert" },
      React.createElement("strong", null, t("settings.mcpConnectionError", "Connection failed")),
      React.createElement("span", null, String(server.error)),
    ),
    React.createElement("details", { className: "wb-plugin-details" },
      React.createElement("summary", null, t("settings.mcpConfiguration", "Provider configuration")),
      React.createElement("div", { className: "wb-mcp-editor-grid" },
        React.createElement("label", null,
          React.createElement("span", null, t("settings.mcpTransport", "Transport")),
          React.createElement("select", {
            className: "wb-select", value: editor.transport, disabled: !!props.busy,
            onChange: function (event) { update({ transport: event.target.value, variablesText: "" }) },
          },
            React.createElement("option", { value: "stdio" }, "stdio"),
            React.createElement("option", { value: "streamable_http" }, "Streamable HTTP"),
            React.createElement("option", { value: "sse" }, "SSE"),
          ),
        ),
        editor.transport === "stdio"
          ? React.createElement(React.Fragment, null,
              React.createElement("label", { className: "wide" },
                React.createElement("span", null, t("settings.mcpCommand", "Executable")),
                React.createElement("input", { className: "wb-input mono", value: editor.command, disabled: !!props.busy, onChange: function (event) { update({ command: event.target.value }) } }),
              ),
              React.createElement("label", { className: "wide" },
                React.createElement("span", null, t("settings.mcpArguments", "Arguments (one per line)")),
                React.createElement("textarea", { className: "wb-input mono", rows: 3, value: editor.argsText, disabled: !!props.busy, onChange: function (event) { update({ argsText: event.target.value }) } }),
              ),
            )
          : React.createElement("label", { className: "wide" },
              React.createElement("span", null, "URL"),
              React.createElement("input", { className: "wb-input mono", type: "url", value: editor.url, disabled: !!props.busy, onChange: function (event) { update({ url: event.target.value }) } }),
            ),
        React.createElement("label", { className: "wide" },
          React.createElement("span", null, variablesLabel + " · KEY=VALUE"),
          React.createElement("textarea", { className: "wb-input mono", rows: 3, value: editor.variablesText, disabled: !!props.busy, onChange: function (event) { update({ variablesText: event.target.value }) } }),
          React.createElement("small", null, t("settings.mcpSecretsHint", "Configured secrets stay redacted and are preserved when saved.")),
        ),
      ),
      React.createElement("div", { className: "wb-plugin-card-actions" },
        React.createElement("button", { type: "button", className: "wb-btn danger", disabled: !!props.busy, onClick: function () { props.onRemove(editor.name) } }, t("settings.delete", "Delete")),
        React.createElement("button", { type: "button", className: "wb-btn primary", disabled: !!props.busy, onClick: function () { props.onSave(editor.name) } }, providerBusy ? t("settings.saving", "Saving…") : t("settings.save", "Save")),
      ),
    ),
    Array.isArray(server.tools) && server.tools.length > 0 && React.createElement("section", { className: "wb-mcp-plugin-list" },
      React.createElement("div", { className: "wb-plugin-section-heading" },
        React.createElement("strong", null, t("settings.mcpProvidedPlugins", "Provided Plugins")),
        React.createElement("span", null, String(server.tools.length)),
      ),
      React.createElement("ul", null, server.tools.map(function (tool) {
        return React.createElement("li", { key: String(tool.plugin || tool.name) },
          React.createElement("code", null, String(tool.plugin || tool.name)),
          React.createElement("span", null, tool.description || tool.name),
        )
      })),
    ),
  )
}

function McpProvidersPanel(props) {
  var t = props.t
  var pluginService = workbenchServices.plugins()
  var [configs, setConfigs] = useStateSt([])
  var [servers, setServers] = useStateSt([])
  var [busy, setBusy] = useStateSt("load")
  var [notice, setNotice] = useStateSt("")
  var [noticeKind, setNoticeKind] = useStateSt("info")
  var [addOpen, setAddOpen] = useStateSt(false)
  var [draft, setDraft] = useStateSt(editableConfig({ name: "", transport: "streamable_http" }))

  function tell(message, kind) {
    if (showSettingsToast(message, kind || "info")) {
      setNotice("")
      return
    }
    setNotice(message || "")
    setNoticeKind(kind || "info")
  }

  function applyPayload(payload) {
    setConfigs((Array.isArray(payload.configs) ? payload.configs : []).map(editableConfig))
    setServers(Array.isArray(payload.servers) ? payload.servers : [])
  }

  function load() {
    setBusy("load")
    return settingsFetch("/api/settings/mcp").then(readSettingsResponse).then(function (payload) {
      applyPayload(payload)
      return pluginService.refresh()
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
    }).finally(function () { setBusy("") })
  }

  useEffectSt(function () { load() }, [])

  function replaceEditor(name, next) {
    setConfigs(function (current) {
      return current.map(function (item) { return item.name === name ? next : item })
    })
  }

  function persist(nextEditors, busyKey) {
    var nextConfigs
    try {
      nextConfigs = nextEditors.map(function (editor) { return persistedConfig(editor, t) })
    } catch (error) {
      tell(error && error.message || String(error), "error")
      return Promise.reject(error)
    }
    setBusy(busyKey)
    setNotice("")
    return settingsFetch("/api/settings/mcp", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ servers: nextConfigs }),
    }).then(readSettingsResponse).then(function (payload) {
      applyPayload(payload)
      return pluginService.refresh()
    }).then(function () {
      tell(t("settings.saved", "Saved"), "success")
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
      throw error
    }).finally(function () { setBusy("") })
  }

  function saveProvider(name) {
    persist(configs, "provider:" + name).catch(function () {})
  }

  function toggleProvider(name, enabled) {
    var next = configs.map(function (item) {
      return item.name === name ? { ...item, enabled: enabled } : item
    })
    setConfigs(next)
    persist(next, "provider:" + name).catch(function () {})
  }

  function removeProvider(name) {
    workbenchServices.feedback().confirmModal({
      body: t("settings.mcpRemoveConfirm", { name: name }, "Remove MCP Provider “{name}” and its Plugin pack?"),
      confirmLabel: t("settings.delete", "Delete"), danger: true,
    }).then(function (confirmed) {
      if (!confirmed) return
      persist(configs.filter(function (item) { return item.name !== name }), "provider:" + name).catch(function () {})
    })
  }

  function addProvider() {
    var name = String(draft.name || "").trim()
    if (!name) {
      tell(t("settings.mcpNameRequired", "Provider name is required."), "error")
      return
    }
    if (configs.some(function (item) { return item.name === name })) {
      tell(t("settings.mcpNameDuplicate", { name: name }, "An MCP Provider named {name} already exists."), "error")
      return
    }
    var next = configs.concat([{ ...draft, name: name, enabled: true }])
    persist(next, "add").then(function () {
      setDraft(editableConfig({ name: "", transport: "streamable_http" }))
      setAddOpen(false)
    }).catch(function () {})
  }

  function statusFor(name) {
    return servers.find(function (item) { return String(item.name || "") === String(name || "") }) || {}
  }

  return React.createElement("div", { className: "settings-panel wb-plugin-settings-page", id: "setting-mcp-providers" },
    React.createElement("header", { className: "wb-plugin-page-header" },
      SectionTitle(
        t("settings.mcpProviders", "MCP Providers"),
        t("settings.mcpProvidersHint", "Each connected MCP Provider is registered as a Plugin pack and participates in toolbox.list → describe → invoke.")
      ),
      React.createElement("button", {
        type: "button", className: "wb-btn primary", disabled: !!busy,
        onClick: function () { setAddOpen(!addOpen) }, "aria-expanded": addOpen ? "true" : "false",
      }, t(addOpen ? "settings.cancel" : "settings.mcpAddProvider", addOpen ? "Cancel" : "Add Provider")),
    ),
    notice && React.createElement("div", { className: "wb-plugin-notice " + noticeKind, role: noticeKind === "error" ? "alert" : "status" }, notice),
    addOpen && React.createElement("section", { className: "wb-mcp-add-card", "aria-labelledby": "mcp-add-title" },
      React.createElement("div", { className: "wb-plugin-section-heading" }, React.createElement("strong", { id: "mcp-add-title" }, t("settings.mcpAddProvider", "Add MCP Provider"))),
      React.createElement("div", { className: "wb-mcp-editor-grid" },
        React.createElement("label", null, React.createElement("span", null, t("settings.name", "Name")), React.createElement("input", { className: "wb-input", autoFocus: true, value: draft.name, onChange: function (event) { setDraft({ ...draft, name: event.target.value }) } })),
        React.createElement("label", null, React.createElement("span", null, t("settings.mcpTransport", "Transport")), React.createElement("select", { className: "wb-select", value: draft.transport, onChange: function (event) { setDraft({ ...draft, transport: event.target.value, variablesText: "" }) } },
          React.createElement("option", { value: "streamable_http" }, "Streamable HTTP"), React.createElement("option", { value: "sse" }, "SSE"), React.createElement("option", { value: "stdio" }, "stdio"))),
        draft.transport === "stdio"
          ? React.createElement(React.Fragment, null,
              React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.mcpCommand", "Executable")), React.createElement("input", { className: "wb-input mono", value: draft.command, onChange: function (event) { setDraft({ ...draft, command: event.target.value }) } })),
              React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.mcpArguments", "Arguments (one per line)")), React.createElement("textarea", { className: "wb-input mono", rows: 3, value: draft.argsText, onChange: function (event) { setDraft({ ...draft, argsText: event.target.value }) } })))
          : React.createElement("label", { className: "wide" }, React.createElement("span", null, "URL"), React.createElement("input", { className: "wb-input mono", type: "url", value: draft.url, onChange: function (event) { setDraft({ ...draft, url: event.target.value }) } })),
        React.createElement("label", { className: "wide" },
          React.createElement("span", null, (draft.transport === "stdio" ? t("settings.mcpEnvironment", "Environment") : t("settings.mcpHeaders", "HTTP headers")) + " · KEY=VALUE"),
          React.createElement("textarea", { className: "wb-input mono", rows: 3, value: draft.variablesText, onChange: function (event) { setDraft({ ...draft, variablesText: event.target.value }) } })),
      ),
      React.createElement("div", { className: "wb-plugin-card-actions" }, React.createElement("button", { type: "button", className: "wb-btn primary", disabled: busy === "add", onClick: addProvider }, busy === "add" ? t("settings.saving", "Saving…") : t("settings.add", "Add"))),
    ),
    busy === "load" && React.createElement("div", { className: "wb-plugin-empty", role: "status" }, t("settings.loading", "Loading…")),
    busy !== "load" && configs.length === 0 && React.createElement("div", { className: "wb-plugin-empty" },
      React.createElement("strong", null, t("settings.mcpEmpty", "No MCP Providers configured")),
      React.createElement("span", null, t("settings.mcpEmptyHint", "Add a Provider to register its tools as a Plugin pack."))),
    React.createElement("div", { className: "wb-plugin-card-list" }, configs.map(function (editor) {
      return React.createElement(ProviderCard, {
        key: editor.name, editor: editor, server: statusFor(editor.name), busy: busy, t: t,
        onChange: replaceEditor, onToggle: toggleProvider, onRemove: removeProvider, onSave: saveProvider,
      })
    })),
  )
}

export { McpProvidersPanel }
