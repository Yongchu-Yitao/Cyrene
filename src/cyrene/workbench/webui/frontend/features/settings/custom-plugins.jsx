import { ExtensionFilter } from "./extension-filter.jsx"
import {ActionIcon} from "./action-icon.jsx"
import {Disclosure} from "./disclosure.jsx"
import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  useRefSt,
  readSettingsResponse,
  settingsFetch,
  showSettingsToast,
  SectionTitle,
  Toggle,
} from "./shared.jsx"
import { PluginCenterAddButton, PluginCenterPage } from "./plugin-center-add.jsx"

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
  var authored = authoredTranslation(item, "name")
  if (authored) return authored
  var fallback = String(item.name || item.id || "Plugin")
  return t ? t("toolName." + String(item.id || item.name || ""), fallback) : fallback
}

function packLabel(item, t) {
  var authored = authoredTranslation(item, "name")
  if (authored) return authored
  var fallback = String(item && (item.name || item.id) || "Plugin")
  return t ? t("toolName." + String(item && item.id || ""), fallback) : fallback
}

function itemDescription(item, t, pack) {
  var id = String(item && (item.id || item.name) || "")
  if (!pack && item && item.customized_description === true) return String(item.description || "")
  var authored = authoredTranslation(item, "description")
  const description = authored || t((pack ? "pluginPackDesc." : "toolDesc.") + id, item.description || id)
  // Installed built-ins can carry descriptions from an older language pack.
  if (item.source === "builtin" || item.source === "core") {
    return description.replace(/\bWorkbench\b/gi, "Cyrene").replace(/工作台/g, "应用")
  }
  return description
}

function workbenchEntryLabel(item) {
  return authoredTranslation(item, "title") || String(item && (item.title || item.id) || "App entry")
}

function workbenchEntryDescription(item) {
  return authoredTranslation(item, "description") || String(item && item.description || "")
}

function searchable(item, t, pack) {
  if (!item || typeof item !== "object") return ""
  return [item.id, item.name, item.description, item.kind, item.source, item.source_path,
    pack ? packLabel(item, t) : itemLabel(item, t), itemDescription(item, t, !!pack)].join(" ").toLowerCase()
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

function MoreIcon() {
  return React.createElement("svg", { viewBox: "0 0 24 24", width: 18, height: 18, "aria-hidden": "true" },
    React.createElement("circle", { cx: 5, cy: 12, r: 1.7, fill: "currentColor" }),
    React.createElement("circle", { cx: 12, cy: 12, r: 1.7, fill: "currentColor" }),
    React.createElement("circle", { cx: 19, cy: 12, r: 1.7, fill: "currentColor" })
  )
}

function DisclosureIcon(props) {
  return React.createElement("svg", {
    className: "wb-registry-pack-chevron" + (props.expanded ? " expanded" : ""),
    viewBox: "0 0 24 24", width: 20, height: 20, "aria-hidden": "true",
  }, React.createElement("path", {
    d: "M6 9l6 6 6-6", fill: "none", stroke: "currentColor", strokeWidth: 2,
    strokeLinecap: "round", strokeLinejoin: "round",
  }))
}

function useToolMenuDismissal(open, setOpen, menuRootRef, triggerRef) {
  useEffectSt(function () {
    if (!open) return undefined
    function closeOnOutsidePointer(event) {
      var root = menuRootRef.current
      if (root && !root.contains(event.target)) setOpen(false)
    }
    function closeOnEscape(event) {
      if (event.key !== "Escape") return
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
      if (triggerRef.current) triggerRef.current.focus()
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer, true)
    document.addEventListener("keydown", closeOnEscape, true)
    return function () {
      document.removeEventListener("pointerdown", closeOnOutsidePointer, true)
      document.removeEventListener("keydown", closeOnEscape, true)
    }
  }, [open])
}

function useToolActionController(props) {
  var item = props.item || {}
  var t = props.t
  var menuRootRef = useRefSt(null), triggerRef = useRefSt(null)
  var [open, setOpen] = useStateSt(false), [dialog, setDialog] = useStateSt("")
  var [name, setName] = useStateSt(String(item.name || item.id || ""))
  var [description, setDescription] = useStateSt(String(item.description || ""))
  var [error, setError] = useStateSt(""), [busy, setBusy] = useStateSt(false)
  useToolMenuDismissal(open, setOpen, menuRootRef, triggerRef)
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
    Promise.resolve(props.onUpdate(item, { name: cleanName, description: description.trim() }))
      .then(function () { setDialog("") })
      .catch(function (reason) { setError(reason && reason.message || String(reason)) })
      .finally(function () { setBusy(false) })
  }
  function changeExposure() {
    setOpen(false)
    setBusy(true)
    Promise.resolve(props.onUpdate(item, { agent_exposure: item.agent_exposure === "direct" ? "discoverable" : "direct" }))
      .catch(function (reason) { setError(reason && reason.message || String(reason)) })
      .finally(function () { setBusy(false) })
  }
  function removeTool() {
    setBusy(true)
    setError("")
    Promise.resolve(props.onDelete(item)).then(function () { setDialog("") })
      .catch(function (reason) { setError(reason && reason.message || String(reason)) })
      .finally(function () { setBusy(false) })
  }
  function beginDelete() { setOpen(false); setDialog("delete"); setError("") }
  return {
    item: item, t: t, menuRootRef: menuRootRef, triggerRef: triggerRef, open: open, setOpen: setOpen,
    dialog: dialog, setDialog: setDialog, name: name, setName: setName, description: description,
    setDescription: setDescription, error: error, busy: busy, disabled: busy || props.disabled,
    beginEdit: beginEdit, saveEdit: saveEdit, changeExposure: changeExposure,
    removeTool: removeTool, beginDelete: beginDelete,
  }
}

function ToolActionMenu(props) {
  var c = props.controller
  var locked = c.item.locked === true
  var direct = c.item.agent_exposure === "direct"
  if (!c.open) return null
  return React.createElement("div", { className: "wb-tool-menu", role: "menu" },
    React.createElement("button", { type: "button", role: "menuitem", disabled: locked || c.disabled, onClick: c.beginEdit }, c.t("settings.pluginToolEdit", "Edit")),
    React.createElement("button", { type: "button", role: "menuitem", disabled: locked || c.disabled, onClick: c.changeExposure }, direct
      ? c.t("settings.pluginToolDiscoverable", "Let Agent find and use")
      : c.t("settings.pluginToolDirect", "Make directly visible to Agent")),
    React.createElement("div", { className: "wb-tool-menu-separator" }),
    React.createElement("button", { type: "button", role: "menuitem", className: "danger", disabled: locked || c.disabled, onClick: c.beginDelete }, c.t("settings.pluginToolDelete", "Delete")),
    locked && React.createElement("small", null, c.t("settings.pluginToolLockedHint", "Core and model tools are managed by the framework."))
  )
}

function ToolEditDialog(props) {
  var c = props.controller
  return React.createElement("form", { onSubmit: c.saveEdit },
    React.createElement("h3", { id: "wb-tool-dialog-title" }, c.t("settings.pluginToolEditTitle", "Edit Agent tool")),
    React.createElement("label", null,
      React.createElement("span", null, c.t("settings.pluginToolAgentName", "Name shown to Agent")),
      React.createElement("input", { className: "wb-input", value: c.name, autoFocus: true, onChange: function (event) { c.setName(event.target.value) }, disabled: c.busy })
    ),
    React.createElement("label", null,
      React.createElement("span", null, c.t("settings.pluginToolAgentDescription", "Description shown to Agent")),
      React.createElement("textarea", { className: "wb-input wb-tool-description-input", rows: 8, value: c.description, onChange: function (event) { c.setDescription(event.target.value) }, disabled: c.busy })
    ),
    c.error && React.createElement("div", { className: "wb-tool-dialog-error", role: "alert" }, c.error),
    React.createElement("div", { className: "wb-tool-dialog-actions" },
      React.createElement("button", { type: "button", className: "wb-btn", disabled: c.busy, onClick: function () { c.setDialog("") } }, c.t("common.cancel", "Cancel")),
      React.createElement("button", { type: "submit", className: "wb-btn primary", disabled: c.busy }, c.busy ? c.t("settings.saving", "Saving…") : c.t("settings.save", "Save"))
    )
  )
}

function ToolDeleteDialog(props) {
  var c = props.controller
  return React.createElement(React.Fragment, null,
    React.createElement("h3", { id: "wb-tool-dialog-title" }, c.t("settings.pluginToolDeleteTitle", "Delete tool?")),
    React.createElement("p", null, c.t("settings.pluginToolDeleteHint", { name: itemLabel(c.item, c.t) }, "{name} will be removed from the Plugin Center.")),
    c.error && React.createElement("div", { className: "wb-tool-dialog-error", role: "alert" }, c.error),
    React.createElement("div", { className: "wb-tool-dialog-actions" },
      React.createElement("button", { type: "button", className: "wb-btn", disabled: c.busy, onClick: function () { c.setDialog("") } }, c.t("common.cancel", "Cancel")),
      React.createElement("button", { type: "button", className: "wb-btn danger", disabled: c.busy, onClick: c.removeTool }, c.busy ? c.t("settings.deleting", "Deleting…") : c.t("settings.pluginToolDelete", "Delete"))
    )
  )
}

function ToolDialog(props) {
  var c = props.controller
  if (!c.dialog) return null
  return React.createElement("div", { className: "wb-tool-dialog-backdrop", onMouseDown: function (event) {
    if (event.target === event.currentTarget && !c.busy) c.setDialog("")
  } },
  React.createElement("section", { className: "wb-tool-dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": "wb-tool-dialog-title" },
    c.dialog === "edit" ? React.createElement(ToolEditDialog, { controller: c }) : React.createElement(ToolDeleteDialog, { controller: c })
  ))
}

function ToolActions(props) {
  var c = useToolActionController(props)
  return React.createElement("div", { className: "wb-tool-menu-wrap", ref: c.menuRootRef },
    React.createElement("button", {
      ref: c.triggerRef, type: "button", className: "wb-tool-menu-trigger",
      onClick: function () { c.setOpen(!c.open) }, disabled: c.disabled,
      "aria-label": c.t("settings.pluginToolMenu", { name: itemLabel(c.item, c.t) }, "Tool menu for {name}"),
      "aria-expanded": c.open ? "true" : "false",
    }, React.createElement(MoreIcon)),
    React.createElement(ToolActionMenu, { controller: c }),
    React.createElement(ToolDialog, { controller: c })
  )
}

function SourcePath({path}) {
  if (!path) return null;
  const parts = path.split("/");
  return <dl className="registry-source-path">
    <dt>{currentLanguage().startsWith("zh") ? "来源路径" : "Source path"}</dt>
    <dd><code>{parts.map((part,index)=><React.Fragment key={index}>{index>0&&<>/<wbr/></>}{part}</React.Fragment>)}</code></dd>
  </dl>;
}

function RegistryMember(props) {
  var item = props.item || {}
  var t = props.t
  var disabled = props.disabled || item.locked === true || props.parentEnabled === false
  var actionsDisabled = props.disabled || props.parentEnabled === false
  const pack=props.pack;
  const kinds=new Set((pack.plugins||[]).map(plugin=>plugin.kind));
  const notes=[];
  if(kinds.size>1 && item.kind)notes.push(t("settings.pluginKind."+item.kind,item.kind));
  if(item.source && item.source!==pack.source)notes.push(t("settings.pluginSourceKind."+item.source,item.source));
  if(item.locked)notes.push(t("settings.pluginLocked","Locked"));
  return <div className="wb-registry-member">
    <div className="wb-registry-member-copy"><strong>{itemLabel(item,t)}</strong>
      <code>{item.name||item.id}</code>{notes.length>0&&<small>{notes.join(" · ")}</small>}
    </div>
    <div className="member-actions">
      {Toggle(configuredEnabled(item),()=>props.onToggle("plugin",item,!configuredEnabled(item)),disabled,t("settings.pluginToggleLabel",{name:itemLabel(item,t)},"Enable or disable {name}"))}
      {item.kind==="tool"&&<ToolActions item={item} t={t} disabled={actionsDisabled} onUpdate={props.onUpdate} onDelete={props.onDelete}/>}
    </div>
    {item.source_path!==pack.source_path&&<SourcePath path={item.source_path}/>}
  </div>
}

function WorkbenchEntryMember(props) {
  var item = props.item || {}
  var title = workbenchEntryLabel(item)
  return React.createElement("div", { className: "wb-registry-member wb-registry-entry-member" },
    React.createElement("div", { className: "wb-registry-member-copy" },
      React.createElement("span", { className: "wb-registry-entry-eyebrow" }, props.t("settings.workbenchEntry", "App entry")),
      React.createElement("strong", null, title),
      workbenchEntryDescription(item) && React.createElement("small", null, workbenchEntryDescription(item))),
    Toggle(item.configured_visible !== false, function () {
      props.onToggle(item, item.configured_visible === false)
    }, props.disabled || props.parentEnabled === false,
    props.t("settings.workbenchEntryToggleLabel", { name: title }, "Show or hide {name} in Tools"))
  )
}

function registryViewModel(registry, query, t) {
  var normalizedQuery = query.trim().toLowerCase()
  var visiblePacks = (Array.isArray(registry.packs) ? registry.packs : []).filter(function (pack) {
    if (!normalizedQuery || searchable(pack, t, true).indexOf(normalizedQuery) >= 0) return true
    if ((Array.isArray(pack.workbench_entries) ? pack.workbench_entries : []).some(function (item) {
      return [item.id, item.title, item.description, workbenchEntryLabel(item), workbenchEntryDescription(item)].join(" ").toLowerCase().indexOf(normalizedQuery) >= 0
    })) return true
    return (Array.isArray(pack.plugins) ? pack.plugins : []).some(function (item) {
      return searchable(item, t, false).indexOf(normalizedQuery) >= 0
    })
  })
  var visibleStandalone = (Array.isArray(registry.standalonePlugins) ? registry.standalonePlugins : []).filter(function (item) {
    return !normalizedQuery || searchable(item, t, false).indexOf(normalizedQuery) >= 0
  })
  return {
    userCreatedPacks: visiblePacks.filter(function (item) { return item.user_created === true }),
    userCreatedStandalone: visibleStandalone.filter(function (item) { return item.user_created === true }),
    packs: visiblePacks.filter(function (item) { return item.user_created !== true }),
    standalone: visibleStandalone.filter(function (item) { return item.user_created !== true }),
    failures: Array.isArray(registry.failures) ? registry.failures : [],
    directory: registry.directory && typeof registry.directory === "object" ? registry.directory : {},
    resultCount: visiblePacks.length + visibleStandalone.length,
  }
}

function usePluginRegistryState(props) {
  var pluginService = workbenchServices.plugins()
  var [registry, setRegistry] = useStateSt(function () { return pluginService.snapshot() })
  var [query, setQuery] = useStateSt("")
  var [busy, setBusy] = useStateSt("")
  var [notice, setNotice] = useStateSt("")
  var [noticeKind, setNoticeKind] = useStateSt("info")
  useEffectSt(function () { return pluginService.subscribe(setRegistry) }, [])
  return {
    t: props.t, pluginService: pluginService, registry: registry, query: query, setQuery: setQuery,
    busy: busy, setBusy: setBusy, notice: notice, setNotice: setNotice,
    noticeKind: noticeKind, setNoticeKind: setNoticeKind,
  }
}

function usePluginRegistryMutations(model) {
  var t = model.t
  function tell(message, kind) {
    if (showSettingsToast(message, kind || "info")) { model.setNotice(""); return }
    model.setNotice(message || "")
    model.setNoticeKind(kind || "info")
  }
  function reloadRegistry(quiet) {
    model.setBusy("reload")
    model.setNotice("")
    model.pluginService.reload().then(function (next) {
      if (next.applicationRestartRequired) tell(t("settings.pluginRestartRequired", "Plugin files were reloaded. Restart the application to apply Application Host changes."), "info")
      else if (!quiet) tell(t("settings.pluginReloaded", "Plugin Center reloaded"), "success")
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
    }).finally(function () { model.setBusy("") })
  }
  function installFromFile() {
    if (!window.cyrene || typeof window.cyrene.pickExtensionPath !== "function") {
      tell(t("settings.pluginInstallDesktop", "Open Cyrene desktop to select a local Plugin folder or ZIP archive."), "info")
      return
    }
    model.setBusy("install")
    Promise.resolve(window.cyrene.pickExtensionPath({
      plugin: true, title: t("settings.pluginInstallFromFile", "Install from file"),
    })).then(function (picked) {
      if (!picked || picked.cancelled || !picked.path) return
      return settingsFetch("/api/plugins/install-file", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: picked.path }),
      }).then(readSettingsResponse).then(function () {
        return model.pluginService.refresh()
      }).then(function (next) {
        if (next.error) throw new Error(next.error)
        tell(next.applicationRestartRequired
          ? t("settings.pluginRestartRequired", "Restart the application to apply changes.")
          : t("settings.pluginInstalledFromFile", "Plugin installed and reloaded."),
        next.applicationRestartRequired ? "info" : "success")
      })
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
    }).finally(function () { model.setBusy("") })
  }
  function updateEnabled(kind, item, nextEnabled) {
    if (!item || item.locked === true || model.busy) return
    var id = String(item.id || item.name || "")
    if (!id) return
    var payload = kind === "pack" ? { packs: {} } : { plugins: {} }
    payload[kind === "pack" ? "packs" : "plugins"][id] = nextEnabled
    model.setBusy(kind + ":" + id)
    model.setNotice("")
    settingsFetch("/api/plugins/activation", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }).then(readSettingsResponse).then(function () {
      return Promise.all([model.pluginService.refresh(), workbenchServices.data().reload()])
    }).then(function () {
      tell(t("settings.saved", "Saved"), "success")
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
    }).finally(function () { model.setBusy("") })
  }
  function updateTool(item, values) {
    var id = item.id || item.canonical_name || item.name
    model.setBusy("tool:update:" + id)
    model.setNotice("")
    return model.pluginService.updateTool(id, values).then(function () {
      tell(t("settings.saved", "Saved"), "success")
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
      throw error
    }).finally(function () { model.setBusy("") })
  }
  function deleteTool(item) {
    var id = item.id || item.canonical_name || item.name
    model.setBusy("tool:delete:" + id)
    model.setNotice("")
    return model.pluginService.deleteTool(id).then(function () {
      tell(t("settings.pluginToolDeleted", "Tool deleted"), "success")
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
      throw error
    }).finally(function () { model.setBusy("") })
  }
  async function removeContribution(isPack, item) {
    if (model.busy || !item.user_created || item.locked) return
    const kind = isPack ? "packs" : "standalone"
    model.setBusy("remove:" + item.id)
    try {
      const result = await settingsFetch("/api/plugins/" + kind + "/" + encodeURIComponent(item.id), { method: "DELETE" }).then(readSettingsResponse)
      await Promise.all([model.pluginService.refresh(), workbenchServices.data().reload()])
      tell(result.application_restart_required ? t("settings.pluginRestartRequired") : t("settings.deletedPlugin"), "success")
    } catch (error) {
      tell(error.message || String(error), "error")
    } finally {
      model.setBusy("")
    }
  }
  function updateWorkbenchEntry(item, visible) {
    if (!item || model.busy) return
    var packId = String(item.pack_id || "")
    var entryId = String(item.id || "")
    if (!packId || !entryId) return
    model.setBusy("entry:" + packId + ":" + entryId)
    model.setNotice("")
    model.pluginService.setWorkbenchEntryVisible(packId, entryId, visible).then(function () {
      tell(t("settings.saved", "Saved"), "success")
    }).catch(function (error) {
      tell(error && error.message || String(error), "error")
    }).finally(function () { model.setBusy("") })
  }
  return { removeContribution, tell: tell, reloadRegistry: reloadRegistry, installFromFile: installFromFile, updateEnabled: updateEnabled, updateTool: updateTool, deleteTool: deleteTool, updateWorkbenchEntry: updateWorkbenchEntry }
}

function usePluginRegistryController(props) {
  var model = usePluginRegistryState(props)
  var mutations = usePluginRegistryMutations(model)
  useEffectSt(function () { mutations.reloadRegistry(true) }, [])
  var view = registryViewModel(model.registry, model.query, model.t)
  return Object.assign({}, model, mutations, view, { disabled: !!model.busy || model.registry.reloading === true })
}

function PluginRegistryHeader(props) {
  var c = props.controller
  return React.createElement("header", { className: "wb-extensions-header" },
    SectionTitle(c.t("settings.pluginRegistry", "Plugin Center"),
      c.t("settings.pluginRegistryHint", "Inspect loaded Plugin packs and add Skills, MCP servers, or CLI tools.")),
    React.createElement("div", { className: "wb-extensions-header-actions" },
      props.onAdd && React.createElement(PluginCenterAddButton, { t: c.t, disabled: c.disabled, onClick: props.onAdd }),
      React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button",
        disabled: c.disabled, onClick: c.installFromFile },
      <ActionIcon name="folder-open"/>, c.busy || c.registry.reloading ? c.t("settings.loading", "Loading…") : c.t("settings.pluginInstallFromFile", "Install from file"))
    )
  )
}

function PluginDirectorySummary(props) {
  var c = props.controller
  var directory = c.directory
  const status = directory.status || (directory.exists ? "ready" : "missing")
  const warnings = [
    status !== "ready" && c.t("settings.pluginDirectoryStatus." + status, status),
    directory.readable === false && c.t("settings.pluginDirectoryUnreadable", "not readable"),
    directory.writable === false && c.t("settings.pluginDirectoryReadOnly", "read-only"),
  ].filter(Boolean)
  return React.createElement("section", { className: "wb-plugin-registry-directory" },
    React.createElement("strong", null, c.t("settings.pluginDirectory", "Plugin directory")),
    React.createElement("code", { title: directory.path || "" }, directory.path || c.t("settings.pluginDirectoryUnavailable", "Unavailable")),
    React.createElement("small", { className: "wb-plugin-registry-directory-meta" },
      warnings.length > 0 && React.createElement("span", null, warnings.join(" · ")),
      React.createElement("span", { className: "wb-plugin-registry-directory-hint" },
        c.t("settings.pluginCreateInNewChatHint", "To create a Plugin, start a new chat."))
    )
  )
}

function PluginRegistryFilter(props) {
  var c = props.controller
  return React.createElement(React.Fragment, null,
    <ExtensionFilter query={c.query} onChange={c.setQuery} label={c.t("settings.pluginFilter", "Filter Plugins")}
      count={c.t("settings.pluginCount", { n: c.resultCount }, String(c.resultCount) + " items")}/>,
    (c.notice || c.registry.error) && React.createElement("div", {
      className: "wb-extension-notice " + (c.registry.error ? "error" : c.noticeKind),
      role: c.registry.error || c.noticeKind === "error" ? "alert" : "status",
    }, c.registry.error || c.notice)
  )
}

function RemoveUserContribution({item,isPack,title,controller:c}){
  const [confirming,setConfirming]=useStateSt(false);
  const trigger=useRefSt(null),cancel=useRefSt(null);
  const zh=currentLanguage().startsWith("zh");
  const label=zh?(isPack?"删除插件包":"删除插件"):(isPack?"Delete plugin pack":"Delete plugin");
  function dismiss(){setConfirming(false);requestAnimationFrame(()=>trigger.current?.focus())}
  function remove() {
    c.removeContribution(isPack, item);
  }
  useEffectSt(()=>{if(confirming)cancel.current?.focus()},[confirming]);
  return <div className="user-plugin-remove">
    {confirming?<div role="group" aria-label={label} onKeyDown={e=>{if(e.key==="Escape"){e.stopPropagation();dismiss()}}}>
      <strong>{zh?"确认删除「"+title+"」？":"Delete "+title+"?"}</strong>
      <p>{isPack?(zh?"将一并移除 "+(item.plugins||[]).length+" 个成员和 "+(item.workbench_entries||[]).length+" 个应用入口。":"Also removes "+(item.plugins||[]).length+" members and "+(item.workbench_entries||[]).length+" app entries."):""}{zh?"将从本机插件目录移除。":" This removes the installed source from this device."}</p>
      <div className="remove-options"><button type="button" className="wb-btn" ref={cancel} onClick={dismiss}>{zh?"取消":"Cancel"}</button><button type="button" className="wb-btn danger" data-cyrene-risk="R3" disabled={c.disabled} onClick={remove}>{zh?"确认删除":"Confirm deletion"}</button></div>
    </div>:<button type="button" className="remove-user-trigger" disabled={c.disabled||item.locked} ref={trigger} onClick={()=>setConfirming(true)}>{label}</button>}
  </div>
}
function RegistryTile({controller:c,item,isPack=false}) {
  const [open,setOpen]=useStateSt(false);
  const title=isPack?packLabel(item,c.t):itemLabel(item,c.t);
  const enabled=configuredEnabled(item);
  const locked=item.locked===true;
  const kind=isPack?'pack':'plugin';
  const detailsId='settings-plugin-'+kind+'-'+encodeURIComponent(item.id||item.name);
  const members=item.plugins||[];
  const sharedKind=members.length && members.every(x=>x.kind===members[0].kind)?members[0].kind:null;
  function toggleFromCard(event) {
    const interactive = event.target.closest([
      "button", "a", "input", "select", "textarea", "label", "form",
      "[role='switch']", ".registry-tile-details",
    ].join(","));
    if (interactive) return;
    setOpen(value => !value);
  }
  return <article className="registry-tile" onClick={toggleFromCard}>
    <div className="registry-tile-head"><strong>{title}</strong>
      {Toggle(enabled,()=>c.updateEnabled(kind,item,!enabled),c.disabled||locked,title)}
      <button type="button" className="tile-disclosure" aria-label={c.t(open?"settings.collapsePackage":"settings.expandPackage",{name:title})} aria-expanded={open} aria-controls={detailsId} onClick={()=>setOpen(!open)}><DisclosureIcon expanded={open}/></button></div>
    <p>{itemDescription(item,c.t,isPack)}</p>
    <div className="tile-id"><code>{item.id||item.name}</code>{locked&&<small>{c.t("settings.pluginLocked")}</small>}</div>
    <Disclosure id={detailsId} open={open}><div className="registry-tile-details">
      <div className="tile-metadata"><RegistryBadges item={item} t={c.t}/>{isPack&&members.length>0&&<span>{c.t("settings.pluginMemberCount",{n:members.length})}{sharedKind && " · "+c.t("settings.pluginKind."+sharedKind,sharedKind)}</span>}
      {!isPack&&item.kind==='tool'&&<ToolActions item={item} t={c.t} disabled={c.disabled} onUpdate={c.updateTool} onDelete={c.deleteTool}/>}</div>
      <SourcePath path={item.source_path}/>
      {isPack&&(item.workbench_entries||[]).map(x=><WorkbenchEntryMember key={x.id} item={x} t={c.t} disabled={c.disabled} parentEnabled={effectiveEnabled(item)} onToggle={c.updateWorkbenchEntry}/>)}
      {isPack&&(item.plugins||[]).map(x=><RegistryMember key={x.id} pack={item} item={x} t={c.t} disabled={c.disabled} parentEnabled={effectiveEnabled(item)} onToggle={c.updateEnabled} onUpdate={c.updateTool} onDelete={c.deleteTool}/>)}
      {open&&item.user_created===true&&<RemoveUserContribution item={item} isPack={isPack} title={title} controller={c}/>}
    </div></Disclosure>
  </article>
}
function PluginPackCard({controller,pack}){return <RegistryTile controller={controller} item={pack} isPack/>}

function PluginPacksSection(props) {
  var c = props.controller
  return React.createElement("section", { className: "wb-registry-section", "aria-busy": c.registry.loading ? "true" : "false" },
    React.createElement("div", { className: "wb-registry-section-heading" },
      React.createElement("strong", null, c.t("settings.pluginPacks", "Plugin packs")), React.createElement("span", null, String(c.packs.length))),
    c.registry.loading && !c.registry.loaded && React.createElement("div", { className: "wb-extensions-empty" }, c.t("settings.loading", "Loading…")),
    !c.registry.loading && !c.packs.length && React.createElement("div", { className: "wb-extensions-empty" }, c.t("settings.pluginPacksEmpty", "No plugin packs found")),
    React.createElement("div", { className: "wb-extension-list" }, c.packs.map(function (pack) {
      return React.createElement(PluginPackCard, { key: String(pack.id || ""), pack: pack, controller: c })
    }))
  )
}

function StandalonePluginCard({controller,item}){return <RegistryTile controller={controller} item={item}/>}

function UserCreatedPluginsSection(props) {
  var c = props.controller
  var count = c.userCreatedPacks.length + c.userCreatedStandalone.length
  if (!count) return null
  return React.createElement("section", { className: "wb-registry-section wb-registry-user-created" },
    React.createElement("div", { className: "wb-registry-section-heading" },
      React.createElement("strong", null, c.t("settings.pluginUserCreated", "Created by you")),
      React.createElement("span", null, String(count))),
    React.createElement("div", { className: "wb-extension-list" },
      c.userCreatedPacks.map(function (pack) {
        return React.createElement(PluginPackCard, { key: "pack:" + String(pack.id || ""), pack: pack, controller: c })
      }),
      c.userCreatedStandalone.map(function (item) {
        return React.createElement(StandalonePluginCard, { key: "plugin:" + String(item.name || item.id || ""), item: item, controller: c })
      })
    )
  )
}

function StandalonePluginsSection(props) {
  var c = props.controller
  return React.createElement("section", { className: "wb-registry-section" },
    React.createElement("div", { className: "wb-registry-section-heading" },
      React.createElement("strong", null, c.t("settings.pluginStandalone", "Standalone Plugins")),
      React.createElement("span", null, String(c.standalone.length))),
    !c.standalone.length && React.createElement("div", { className: "wb-extensions-empty" }, c.t("settings.pluginStandaloneEmpty", "No standalone Plugins found")),
    React.createElement("div", { className: "wb-extension-list" }, c.standalone.map(function (item) {
      return React.createElement(StandalonePluginCard, { key: String(item.name || item.id || ""), item: item, controller: c })
    }))
  )
}

function RegistryFailures(props) {
  var c = props.controller
  if (!c.failures.length) return null
  return React.createElement("section", { className: "wb-registry-section wb-registry-failures" },
    React.createElement("div", { className: "wb-registry-section-heading" },
      React.createElement("strong", null, c.t("settings.pluginLoadFailures", "Load failures")),
      React.createElement("span", null, String(c.failures.length))),
    React.createElement("ul", null, c.failures.map(function (failure, index) {
      return React.createElement("li", { key: String(failure.path || failure.pack_id || index) },
        React.createElement("div", { className: "wb-registry-failure-head" },
          React.createElement("code", null, failure.pack_id || failure.path || c.t("settings.pluginUnknown", "unknown")),
          React.createElement("b", null, c.t("settings.pluginFailureStage." + failure.stage, failure.stage || c.t("settings.pluginLoadError", "load error")))),
        failure.source && React.createElement("small", null, c.t("settings.pluginSource", { source: c.t("settings.pluginSourceKind." + failure.source, failure.source) }, "Source: {source}")),
        failure.path && React.createElement("small", null, failure.path),
        React.createElement("pre", null, String(failure.error || c.t("settings.pluginLoadError", "Failed to load")))
      )
    }))
  )
}

function PluginRegistryPanel(props) {
  var [showCenter, setShowCenter] = useStateSt(false)
  var c = usePluginRegistryController(props)
  var modules = Array.isArray(props.pluginModules) ? props.pluginModules : []
  var canAdd = ["extensions", "skills", "mcp", "cli"].some(function (marker) { return modules.indexOf(marker) >= 0 })
  useEffectSt(function () { if (!canAdd && showCenter) setShowCenter(false) }, [canAdd, showCenter])
  if (showCenter) {
    return React.createElement("div", { className: "settings-panel settings-panel-wide wb-plugin-registry-page wb-plugin-center-page-shell", id: "setting-plugin-registry" },
      React.createElement(PluginCenterPage, {
        t: c.t,
        disabled: c.disabled,
        pluginModules: modules,
        pluginPacks: Array.isArray(c.registry.packs) ? c.registry.packs : [],
        onClose: function () { setShowCenter(false) },
        onRuntimeRefresh: function () { return c.pluginService.refresh() },
        onNotice: c.tell,
      })
    )
  }
  return React.createElement("div", { className: "settings-panel wb-plugin-registry-page", id: "setting-plugin-registry" },
    React.createElement(PluginRegistryHeader, { controller: c, onAdd: canAdd ? function () { setShowCenter(true) } : null }),

    React.createElement(PluginRegistryFilter, { controller: c }),
    React.createElement(UserCreatedPluginsSection, { controller: c }),
    React.createElement(PluginPacksSection, { controller: c }),
    React.createElement(StandalonePluginsSection, { controller: c }),
    <PluginDirectorySummary controller={c}/>,
    React.createElement(RegistryFailures, { controller: c })
  )
}

export { PluginRegistryPanel }
