import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  useRefSt,
  readSettingsResponse,
  settingsFetch,
} from "./shared.jsx"
import { ExtensionCard, TaskList, displayName, displayDescription } from "./plugin-center-catalog.jsx"
import { AgentProposalDialog, AgentTab, agentId, installationId } from "./plugin-center-agents.jsx"
import { BindDialog, SourcesDialog } from "./plugin-center-admin.jsx"
import { McpConfigurationDialog } from "./plugin-center-mcp.jsx"
import { CliHooksPanel } from "./plugin-center-cli-hooks.jsx"

var KIND_IDS = ["recommended", "skill", "mcp", "cli", "toolchain", "agent"]
var ACTIVE_TASK_STATES = ["queued", "running", "cancelling"]
var TERMINAL_TASK_STATES = ["completed", "failed", "cancelled", "canceled", "interrupted"]
var EMPTY_CATALOG = { owner_pack: "", projection: null, items: [], tasks: [], agents: { recommended: [], installed: [] }, python_prompt_required: false }

function availableKindIds(props) {
  var modules = Array.isArray(props && props.pluginModules) ? props.pluginModules : []
  var packs = Array.isArray(props && props.pluginPacks) ? props.pluginPacks : []
  var cliPack = packs.find(function (pack) { return String(pack && pack.id || "") === "cyrene_cli" })
  var output = []
  if (modules.indexOf("extensions") >= 0) output = output.concat(["recommended", "toolchain", "agent"])
  if (modules.indexOf("skills") >= 0) output.push("skill")
  if (modules.indexOf("mcp") >= 0) output.push("mcp")
  // A freshly loaded application pack is registered before its frontend
  // marker can become operational after restart. Keep its Plugin Center entry
  // visible during that boundary, while still respecting an explicit disable.
  if (modules.indexOf("cli") >= 0 || (cliPack && cliPack.configured_enabled !== false)) output.push("cli")
  return KIND_IDS.filter(function (kind) { return output.indexOf(kind) >= 0 })
}

function currentLanguage() {
  try { return window.CyreneUI.require("i18n").getLang() || "en" } catch (error) { return "en" }
}

function text(t, key, english, chinese, params) {
  var fallback = currentLanguage().toLowerCase().indexOf("zh") === 0 ? chinese : english
  if (t) return t(key, params || null, fallback)
  var pluralValue = params && (params.count !== undefined ? Number(params.count) : Number(params.n))
  fallback = fallback.replace(/\{\{([^{}|]*)\|([^{}]*)\}\}/g, function (_match, singular, plural) {
    return pluralValue === 1 ? singular : plural
  })
  return fallback.replace(/\{([^{}]+)\}/g, function (match, name) {
    return params && Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match
  })
}

function jsonRequest(path, init) {
  return settingsFetch(path, init).then(readSettingsResponse).then(function (payload) {
    if (payload && payload.ok === false) throw new Error(String(payload.error || payload.detail || workbenchServices.i18n().t("settings.requestFailed")))
    return payload
  })
}

function kindText(kind, t) {
  if (kind === "recommended") return text(t, "settings.extensionTab.recommended", "Recommended", "推荐")
  if (kind === "mcp") return text(t, "settings.extensionTab.mcp", "MCP", "MCP")
  if (kind === "cli") return text(t, "settings.extensionTab.cli", "Command-line Tools", "命令行工具")
  if (kind === "toolchain") return text(t, "settings.extensionTab.toolchains", "Runtime", "运行环境")
  if (kind === "agent") return text(t, "settings.extensionTab.agent", "Agents", "Agent")
  return text(t, "settings.extensionTab.skills", "Skills", "技能")
}

function itemId(item) {
  return String(item && (item.id || item.name) || "")
}

function itemName(item) {
  return String(item && (item.name || item.id) || "—")
}

function taskId(task) {
  return String(task && (task.id || task.task_id) || "")
}

function taskIsActive(task) {
  return ACTIVE_TASK_STATES.indexOf(String(task && task.status || "")) >= 0
}

function taskIsTerminal(task) {
  return TERMINAL_TASK_STATES.indexOf(String(task && task.status || "")) >= 0
}

function errorText(value) {
  if (!value) return ""
  if (typeof value === "string") return value
  if (value.message || value.detail || value.error) return String(value.message || value.detail || value.error)
  try { return JSON.stringify(value) } catch (error) { return String(value) }
}

function sourceText(source) {
  if (!source) return ""
  if (typeof source === "string") return source
  if (typeof source !== "object") return String(source)
  return [source.type || source.kind || source.transport, source.identifier || source.id || source.ref,
    source.url || source.path || source.repo].filter(Boolean).map(String).join(" · ")
}

function enumText(t, prefix, value) {
  var raw = String(value || "")
  return raw ? text(t, prefix + raw, raw.replace(/_/g, " "), raw.replace(/_/g, " ")) : ""
}

function parseVariables(value, label, t) {
  var output = {}
  String(value || "").split(/\r?\n/).forEach(function (line) {
    if (!line.trim()) return
    var separator = line.indexOf("=")
    if (separator <= 0) throw new Error(text(t, "settings.pluginCenterVariableInvalid", "{label} must use NAME=value, one per line.", "{label} 必须按 NAME=value 填写，每行一项。", { label: label }))
    output[line.slice(0, separator).trim()] = line.slice(separator + 1)
  })
  return output
}

function PluginCenterGlyph(props) {
  var glyph = props.kind === "recommended"
    ? <><path d="m12 3-1.8 4.7-4.7 1.8 4.7 1.8L12 16l1.8-4.7 4.7-1.8-4.7-1.8L12 3Z" /><path d="m19 16-.7 2.3L16 19l2.3.7L19 22l.7-2.3L22 19l-2.3-.7L19 16Z" /></>
    : props.kind === "skill"
    ? <><path d="M12 7v14" /><path d="M3 18a1 1 0 0 1-1-1V5a2 2 0 0 1 2-2h5a3 3 0 0 1 3 3v15a3 3 0 0 0-3-3Z" /><path d="M21 18a1 1 0 0 0 1-1V5a2 2 0 0 0-2-2h-5a3 3 0 0 0-3 3v15a3 3 0 0 1 3-3Z" /></>
    : props.kind === "mcp"
    ? <><rect x="9" y="3" width="6" height="5" rx="1" /><rect x="3" y="16" width="6" height="5" rx="1" /><rect x="15" y="16" width="6" height="5" rx="1" /><path d="M12 8v4M6 16v-2h12v2" /></>
    : props.kind === "toolchain"
    ? <><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-3 3-3-3Z" /><path d="m5 19 1 1" /></>
    : props.kind === "agent"
    ? <><rect x="4" y="7" width="16" height="13" rx="2" /><path d="M12 3v4M8 12h.01M16 12h.01M8 16h8M2 12h2M20 12h2" /></>
    : <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 9 3 3-3 3M13 15h4" /></>
  return <svg className="wb-plugin-center-glyph-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{glyph}</svg>
}

function AddIcon() {
  return <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
}

function hasMcpConfiguration(item) {
  return !!(item && (item.fallback_request || (Array.isArray(item.remotes) && item.remotes.length) || (Array.isArray(item.packages) && item.packages.length)))
}

function SearchResult(props) {
  var item = props.item || {}
  var t = props.t
  var source = sourceText(item.source)
  var installed = item.installed === true || props.installed
  var hasRequest = item.install_request && typeof item.install_request === "object"
  var configure = props.kind === "mcp" && !hasRequest && hasMcpConfiguration(item)
  var reason = String(item.reason_code || "")
  return <article className="wb-plugin-center-result">
    <span className={'wb-plugin-center-card-glyph ' + props.kind}><PluginCenterGlyph kind={props.kind} /></span>
    <div className="wb-plugin-center-card-copy"><div className="wb-plugin-center-card-title"><strong>{itemName(item)}</strong>
      {item.verified === true && <span>{text(t, "settings.pluginCenterVerified", "Verified", "已验证")}</span>}{item.risk && <span>{enumText(t, "settings.extensionRisk.", item.risk)}</span>}
    </div>{item.description && <p>{String(item.description)}</p>}
      <small title={source}>{[item.version || item.resolved_version, item.publisher, item.backend, source].filter(Boolean).join(" · ")}</small>
      {!installed && !hasRequest && !configure && reason && <small className="wb-plugin-center-result-reason">{enumText(t, "settings.pluginCenterReason.", reason)}</small>}
    </div>
    <button type="button" className={'wb-btn' + (!installed ? ' primary' : '')} disabled={props.busy || installed || (!hasRequest && !configure)} onClick={configure ? props.onConfigure : props.onInstall}>
      {installed ? text(t, "settings.pluginCenterInstalled", "Installed", "已安装") : configure ? text(t, "settings.pluginCenterConfigure", "Configure", "配置") : text(t, "settings.pluginCenterInstall", "Install", "安装")}
    </button>
  </article>
}

function emptyManualMcp() {
  return { name: "", transport: "streamable_http", url: "", command: "", args: "", version: "", headers: "", env: "" }
}

function usePluginCenterModel(props) {
  var availableKinds = availableKindIds(props)
  var rootRef = useRefSt(null), triggerRef = useRefSt(null), panelRef = useRefSt(null), fileRef = useRefSt(null)
  var loadSequenceRef = useRefSt(0), searchSequenceRef = useRefSt(0), finishedTasksRef = useRefSt({}), overlayStateRef = useRefSt({})
  var loadAbortRef = useRefSt(null), searchAbortRef = useRefSt(null)
  var [open, setOpen] = useStateSt(props.inline === true), [kind, setKind] = useStateSt(function () { return availableKinds[0] || "recommended" })
  var [catalog, setCatalog] = useStateSt(EMPTY_CATALOG), [loading, setLoading] = useStateSt(false)
  var [busy, setBusy] = useStateSt(""), [notice, setNotice] = useStateSt(""), [noticeKind, setNoticeKind] = useStateSt("info")
  var [filterQuery, setFilterQuery] = useStateSt(""), [query, setQuery] = useStateSt(""), [results, setResults] = useStateSt([]), [cursor, setCursor] = useStateSt("")
  var [searchBusy, setSearchBusy] = useStateSt(false), [advanced, setAdvanced] = useStateSt(false)
  var [task, setTask] = useStateSt(null), [skillSelection, setSkillSelection] = useStateSt(null)
  var [localPath, setLocalPath] = useStateSt(""), [manualOpen, setManualOpen] = useStateSt(false)
  var [manualMcp, setManualMcp] = useStateSt(emptyManualMcp)
  var [installerOpen, setInstallerOpen] = useStateSt(false), [sourcesOpen, setSourcesOpen] = useStateSt(false)
  var [proposalOpen, setProposalOpen] = useStateSt(false), [mcpEditorItem, setMcpEditorItem] = useStateSt(null)
  var [bindItem, setBindItem] = useStateSt(null), [agentExpandedId, setAgentExpandedId] = useStateSt("")
  var [requestedVersion, setRequestedVersion] = useStateSt(""), [texChoice, setTexChoice] = useStateSt("tinytex")
  overlayStateRef.current = {
    installerOpen: installerOpen, sourcesOpen: sourcesOpen, proposalOpen: proposalOpen,
    mcpEditorItem: mcpEditorItem, bindItem: bindItem,
  }
  return {
    props: props, t: props.t, availableKinds: availableKinds,
    extensionsAvailable: availableKinds.indexOf("recommended") >= 0,
    rootRef: rootRef, triggerRef: triggerRef, panelRef: panelRef, fileRef: fileRef,
    loadSequenceRef: loadSequenceRef, searchSequenceRef: searchSequenceRef, loadAbortRef: loadAbortRef, searchAbortRef: searchAbortRef,
    finishedTasksRef: finishedTasksRef, overlayStateRef: overlayStateRef,
    open: open, setOpen: setOpen, kind: kind, setKind: setKind, catalog: catalog, setCatalog: setCatalog,
    loading: loading, setLoading: setLoading, busy: busy, setBusy: setBusy, notice: notice, setNotice: setNotice,
    noticeKind: noticeKind, setNoticeKind: setNoticeKind, filterQuery: filterQuery, setFilterQuery: setFilterQuery,
    query: query, setQuery: setQuery,
    results: results, setResults: setResults, cursor: cursor, setCursor: setCursor,
    searchBusy: searchBusy, setSearchBusy: setSearchBusy, advanced: advanced, setAdvanced: setAdvanced,
    task: task, setTask: setTask, skillSelection: skillSelection, setSkillSelection: setSkillSelection,
    localPath: localPath, setLocalPath: setLocalPath, manualOpen: manualOpen, setManualOpen: setManualOpen,
    manualMcp: manualMcp, setManualMcp: setManualMcp,
    installerOpen: installerOpen, setInstallerOpen: setInstallerOpen, sourcesOpen: sourcesOpen, setSourcesOpen: setSourcesOpen,
    proposalOpen: proposalOpen, setProposalOpen: setProposalOpen, mcpEditorItem: mcpEditorItem, setMcpEditorItem: setMcpEditorItem,
    bindItem: bindItem, setBindItem: setBindItem, agentExpandedId: agentExpandedId, setAgentExpandedId: setAgentExpandedId,
    requestedVersion: requestedVersion, setRequestedVersion: setRequestedVersion, texChoice: texChoice, setTexChoice: setTexChoice,
  }
}

function useCatalogController(model) {
  var t = model.t
  function notify(message, level) {
    model.setNotice(message || "")
    model.setNoticeKind(level || "info")
    if (model.props.onNotice) model.props.onNotice(message, level || "info")
  }
  function refreshRuntime() {
    if (!model.props.onRuntimeRefresh) return Promise.resolve()
    try { return Promise.resolve(model.props.onRuntimeRefresh()) } catch (error) { return Promise.reject(error) }
  }
  function loadKind(selected) {
    if (model.availableKinds.indexOf(selected) < 0) return Promise.resolve(null)
    var loadId = ++model.loadSequenceRef.current
    if (model.loadAbortRef.current) model.loadAbortRef.current.abort()
    var controller = new AbortController()
    model.loadAbortRef.current = controller
    model.setLoading(true)
    var path = selected === "recommended" ? "/api/plugin-center/overview" : "/api/plugin-center/" + encodeURIComponent(selected)
    return jsonRequest(path, { signal: controller.signal }).then(function (payload) {
      if (model.loadSequenceRef.current !== loadId) return payload
      var collection = selected === "skill" ? "skills" : selected === "toolchain" ? "toolchains" : selected
      var items = selected === "agent" ? [] : Array.isArray(payload.items) ? payload.items : Array.isArray(payload[collection]) ? payload[collection] : []
      if (selected === "recommended") items = items.filter(function (item) {
        var itemKind = String(item && item.kind || "")
        return !itemKind || model.availableKinds.indexOf(itemKind) >= 0
      })
      var tasks = Array.isArray(payload.tasks) ? payload.tasks : []
      model.setCatalog({
        owner_pack: selected === "skill" ? "cyrene_skills" : selected === "mcp" ? "cyrene_mcp" : selected === "cli" ? "cyrene_cli" : "cyrene_extensions",
        projection: null,
        items: items,
        tasks: tasks,
        agents: selected === "agent"
          ? { recommended: Array.isArray(payload.recommended) ? payload.recommended : [], installed: Array.isArray(payload.installed) ? payload.installed : [] }
          : payload.agents && typeof payload.agents === "object" ? payload.agents : { recommended: [], installed: [] },
        python_prompt_required: payload.python_prompt_required === true,
      })
      var running = tasks.filter(taskIsActive).sort(function (left, right) {
        return String(right.created_at || "").localeCompare(String(left.created_at || ""))
      })[0]
      if (running) model.setTask(running)
      model.setLoading(false)
      return payload
    }).catch(function (error) {
      if (model.loadSequenceRef.current === loadId && !(error && error.name === "AbortError")) {
        model.setLoading(false)
        notify(error && error.message || String(error), "error")
      }
      if (!(error && error.name === "AbortError")) throw error
      return null
    }).finally(function () { if (model.loadAbortRef.current === controller) model.loadAbortRef.current = null })
  }
  function refreshAfterMutation(message) {
    var kind = model.kind
    return Promise.all([loadKind(kind), refreshRuntime()]).then(function () {
      if (message) notify(message, "success")
    })
  }
  return { notify: notify, refreshRuntime: refreshRuntime, loadKind: loadKind, refreshAfterMutation: refreshAfterMutation }
}

function useTaskController(model, catalogController) {
  var t = model.t
  function finishTask(nextTask) {
    var id = taskId(nextTask)
    if (!id || model.finishedTasksRef.current[id]) return
    model.finishedTasksRef.current[id] = true
    var status = String(nextTask.status || "")
    if (status === "completed") {
      catalogController.refreshAfterMutation(text(t, "settings.pluginCenterInstallComplete", "Installed and connected to the Plugin Center.", "安装完成，已接入插件中心。")).catch(function (error) {
        catalogController.notify(error && error.message || String(error), "error")
      })
    } else if (status === "cancelled" || status === "canceled") {
      catalogController.loadKind(model.kind).catch(function () {})
      catalogController.notify(text(t, "settings.pluginCenterInstallCancelled", "Installation cancelled.", "安装已取消。"), "info")
    } else {
      catalogController.loadKind(model.kind).catch(function () {})
      catalogController.notify(errorText(nextTask.error) || String(nextTask.message || text(t, "settings.pluginCenterInstallFailed", "Installation failed.", "安装失败。")), "error")
    }
  }
  function cancelTask(selectedTask) {
    var target = selectedTask || model.task
    var id = taskId(target)
    if (!id || String(target && target.status || "") === "cancelling") return
    var ownerKind = String(target && target.kind || model.kind)
    if (model.availableKinds.indexOf(ownerKind) < 0) return
    model.setBusy("cancel-task")
    jsonRequest("/api/plugin-center/" + encodeURIComponent(ownerKind) + "/tasks/" + encodeURIComponent(id) + "/cancel", { method: "POST" }).then(function (payload) {
      model.setTask(payload.task || payload)
    }).catch(function (error) {
      catalogController.notify(error && error.message || String(error), "error")
    }).finally(function () { model.setBusy("") })
  }
  var activeTaskId = taskId(model.task)
  var activeTaskKind = String(model.task && model.task.kind || model.kind)
  useEffectSt(function () {
    if (!model.open || model.availableKinds.indexOf(activeTaskKind) < 0 || !activeTaskId || !taskIsActive(model.task)) return undefined
    var stopped = false
    var timer = null
    var controller = null
    function poll() {
      controller = new AbortController()
      jsonRequest("/api/plugin-center/" + encodeURIComponent(activeTaskKind) + "/tasks/" + encodeURIComponent(activeTaskId), { signal: controller.signal }).then(function (payload) {
        if (stopped) return
        var nextTask = payload.task || payload
        model.setTask(nextTask)
        model.setCatalog(function (current) {
          var found = false
          var nextTasks = (current.tasks || []).map(function (item) {
            if (taskId(item) !== taskId(nextTask)) return item
            found = true
            return nextTask
          })
          if (!found) nextTasks.unshift(nextTask)
          return Object.assign({}, current, { tasks: nextTasks })
        })
        if (taskIsTerminal(nextTask)) { finishTask(nextTask); return }
        timer = setTimeout(poll, 900)
      }).catch(function () {
        if (!stopped) timer = setTimeout(poll, 1800)
      })
    }
    timer = setTimeout(poll, 650)
    return function () { stopped = true; if (timer) clearTimeout(timer); if (controller) controller.abort() }
  }, [model.open, activeTaskKind, activeTaskId, String(model.task && model.task.status || ""), model.availableKinds.join("|")])
  return { finishTask: finishTask, cancelTask: cancelTask }
}

function useSearchController(model, catalogController) {
  var t = model.t
  function changeQuery(value) {
    if (model.searchAbortRef.current) model.searchAbortRef.current.abort()
    model.searchSequenceRef.current += 1
    model.setSearchBusy(false)
    model.setQuery(value)
    model.setResults([])
    model.setCursor("")
    model.setSkillSelection(null)
  }
  function changeAdvanced(value) {
    if (model.searchAbortRef.current) model.searchAbortRef.current.abort()
    model.searchSequenceRef.current += 1
    model.setSearchBusy(false)
    model.setAdvanced(value)
    model.setResults([])
    model.setCursor("")
  }
  function search(event, append) {
    if (event && event.preventDefault) event.preventDefault()
    var cleanQuery = model.query.trim()
    if (!cleanQuery) {
      catalogController.notify(text(t, "settings.pluginCenterSearchRequired", "Enter something to search for.", "请输入搜索内容。"), "error")
      return
    }
    model.setSearchBusy(true)
    model.setSkillSelection(null)
    var searchId = ++model.searchSequenceRef.current
    var searchCursor = append ? model.cursor : ""
    var kind = model.kind
    if (model.availableKinds.indexOf(kind) < 0) return
    if (model.searchAbortRef.current) model.searchAbortRef.current.abort()
    var controller = new AbortController()
    model.searchAbortRef.current = controller
    var advanced = model.advanced
    var path = "/api/plugin-center/" + encodeURIComponent(kind) + "/search?q=" + encodeURIComponent(cleanQuery)
      + "&advanced=" + (kind === "cli" && advanced ? "true" : "false") + "&cursor=" + encodeURIComponent(searchCursor)
    jsonRequest(path, { signal: controller.signal }).then(function (payload) {
      if (model.searchSequenceRef.current !== searchId) return
      var nextResults = Array.isArray(payload.results) ? payload.results : []
      model.setResults(function (previous) { return append ? previous.concat(nextResults) : nextResults })
      model.setCursor(String(payload.next_cursor || ""))
      if (!append && !nextResults.length) catalogController.notify(text(t, "settings.pluginCenterNoSearchResults", "No matching items found.", "没有找到匹配项。"), "info")
      else model.setNotice("")
    }).catch(function (error) {
      if (model.searchSequenceRef.current === searchId && !(error && error.name === "AbortError")) catalogController.notify(error && error.message || String(error), "error")
    }).finally(function () {
      if (model.searchSequenceRef.current === searchId) model.setSearchBusy(false)
      if (model.searchAbortRef.current === controller) model.searchAbortRef.current = null
    })
  }
  return { changeQuery: changeQuery, changeAdvanced: changeAdvanced, search: search }
}

function useInstallController(model, catalogController, taskController) {
  var t = model.t
  function startInstall(item, request) {
    if (!request || typeof request !== "object") {
      catalogController.notify(text(t, "settings.pluginCenterNoInstallRequest", "This result does not provide an install request.", "此结果没有提供可用的安装请求。"), "error")
      return Promise.resolve()
    }
    var id = itemId(item)
    var kind = String(item && item.kind || model.kind)
    var requestedVersion = String(request.version || request.requested_version || item && (item.version || item.recommended_version) || "")
    var requestedSource = sourceText(request.source) || [request.url, request.repository, request.ref, request.package].filter(Boolean).map(String).join(" · ")
    var summary = [itemName(item), requestedVersion, request.distribution, requestedSource || sourceText(item && item.source)].filter(Boolean).join(" · ")
    var feedback = workbenchServices.feedback()
    var confirmation = feedback && typeof feedback.confirmModal === "function"
      ? feedback.confirmModal({ title: t("settings.extensionInstallConfirmTitle", "Confirm installation"), body: t("settings.extensionInstallConfirmBody", { summary: summary }, "Install {summary}?"), confirmLabel: t("settings.install", "Install") })
      : Promise.resolve(typeof window.confirm !== "function" || window.confirm(t("settings.extensionInstallConfirmBody", { summary: summary }, "Install {summary}?")))
    return Promise.resolve(confirmation).then(function (confirmed) {
      if (!confirmed) return null
      model.setBusy("install:" + kind + ":" + id)
      model.setNotice("")
      return jsonRequest("/api/plugin-center/" + encodeURIComponent(kind) + "/install", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ extension_id: id, request: request }),
      }).then(function (payload) {
        var nextTask = payload.task || { id: payload.task_id, extension_id: id, status: "queued", progress: 0 }
        model.setTask(nextTask)
        model.setCatalog(function (current) {
          return Object.assign({}, current, { tasks: [nextTask].concat((current.tasks || []).filter(function (task) { return taskId(task) !== taskId(nextTask) })) })
        })
        model.setSkillSelection(null)
        model.setInstallerOpen(false)
        catalogController.notify(text(t, "settings.pluginCenterInstallStarted", "Installation started.", "已开始安装。"), "success")
        if (taskIsTerminal(nextTask)) taskController.finishTask(nextTask)
        return payload
      }).catch(function (error) {
        catalogController.notify(error && error.message || String(error), "error")
        throw error
      }).finally(function () { model.setBusy("") })
    })
  }
  return { startInstall: startInstall }
}

function reviewedSkillRequest(payload, request, t) {
  var commit = String(payload && payload.source && payload.source.source_commit || "").trim()
  if (!/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/i.test(commit)) {
    throw new Error(text(t, "settings.pluginCenterSkillCommitMissing", "The repository inspection did not return a valid immutable commit. Search again before installing.", "仓库检查未返回有效的不可变提交，请重新搜索后再安装。"))
  }
  return Object.assign({}, request, { source_commit: commit.toLowerCase() })
}

function skillCandidateSelection(item, request, candidates) {
  var selected = {}
  candidates.forEach(function (candidate) { selected[String(candidate.path || ".")] = false })
  return { item: item, request: request, candidates: candidates, selected: selected }
}

function useSkillSearchController(model, catalogController, installController) {
  var t = model.t
  function inspectSkill(item, request, repository) {
    model.setBusy("inspect:" + itemId(item))
    jsonRequest("/api/plugin-center/skill/inspect", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: repository }),
    }).then(function (payload) {
      var reviewedRequest = reviewedSkillRequest(payload, request, t)
      var candidates = Array.isArray(payload.candidates) ? payload.candidates : []
      if (!candidates.length) {
        catalogController.notify(text(t, "settings.pluginCenterNoSkillsFound", "No valid SKILL.md was found in this repository.", "仓库中没有找到有效的 SKILL.md。"), "error")
        return
      }
      if (candidates.length === 1) {
        return installController.startInstall(item, Object.assign({}, reviewedRequest, { subdirs: [String(candidates[0].path || ".")] }))
      }
      model.setSkillSelection(skillCandidateSelection(item, reviewedRequest, candidates))
    }).catch(function (error) {
      catalogController.notify(error && error.message || String(error), "error")
    }).finally(function () { model.setBusy("") })
  }
  function installSearchResult(item) {
    var request = item && item.install_request
    if (request && typeof request === "object" && (model.kind === "cli" || model.kind === "toolchain")) {
      request = Object.assign({}, request)
      if (model.requestedVersion.trim()) request.version = model.requestedVersion.trim()
      if (model.kind === "toolchain" && itemId(item) === "tex") request.distribution = model.texChoice
    }
    if (model.kind !== "skill" || !request || typeof request !== "object" || (Array.isArray(request.subdirs) && request.subdirs.length)) {
      installController.startInstall(item, request).catch(function () {})
      return
    }
    var repository = String(request.url || request.repository || "")
    if (!repository) {
      catalogController.notify(text(t, "settings.pluginCenterSkillSourceMissing", "The returned install request has no repository URL.", "返回的安装请求没有仓库地址。"), "error")
      return
    }
    inspectSkill(item, request, repository)
  }
  function installSelectedSkills() {
    if (!model.skillSelection) return
    var selected = Object.keys(model.skillSelection.selected).filter(function (path) { return model.skillSelection.selected[path] })
    if (!selected.length) {
      catalogController.notify(text(t, "settings.pluginCenterSelectSkill", "Select at least one Skill.", "请至少选择一个 Skill。"), "error")
      return
    }
    installController.startInstall(model.skillSelection.item, Object.assign({}, model.skillSelection.request, { subdirs: selected })).catch(function () {})
  }
  return { installSearchResult: installSearchResult, installSelectedSkills: installSelectedSkills }
}

function useSkillImportController(model, catalogController) {
  var t = model.t
  function uploadSkill(event) {
    var file = event.target.files && event.target.files[0]
    event.target.value = ""
    if (!file) return
    var form = new FormData()
    form.append("file", file)
    model.setBusy("skill-upload")
    jsonRequest("/api/plugin-center/skill/upload", { method: "POST", body: form }).then(function () {
      model.setInstallerOpen(false)
      return catalogController.refreshAfterMutation(text(t, "settings.pluginCenterSkillImported", "Skill imported.", "Skill 已导入。"))
    }).catch(function (error) {
      catalogController.notify(error && error.message || String(error), "error")
    }).finally(function () { model.setBusy("") })
  }
  function chooseSkillFolder() {
    if (!window.cyrene || typeof window.cyrene.pickExtensionPath !== "function") return
    Promise.resolve(window.cyrene.pickExtensionPath({
      directory: true, title: text(t, "settings.pluginCenterChooseSkillFolder", "Choose a Skill folder", "选择 Skill 文件夹"),
    })).then(function (picked) {
      if (picked && !picked.cancelled && picked.path) model.setLocalPath(String(picked.path))
    }).catch(function (error) {
      catalogController.notify(error && error.message || String(error), "error")
    })
  }
  function importSkillPath(event) {
    event.preventDefault()
    var path = model.localPath.trim()
    if (!path) {
      catalogController.notify(text(t, "settings.pluginCenterPathRequired", "Choose or enter a local Skill path.", "请选择或输入本地 Skill 路径。"), "error")
      return
    }
    model.setBusy("skill-import")
    jsonRequest("/api/plugin-center/skill/import", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: path }),
    }).then(function () {
      model.setLocalPath("")
      model.setInstallerOpen(false)
      return catalogController.refreshAfterMutation(text(t, "settings.pluginCenterSkillImported", "Skill imported.", "Skill 已导入。"))
    }).catch(function (error) {
      catalogController.notify(error && error.message || String(error), "error")
    }).finally(function () { model.setBusy("") })
  }
  return { uploadSkill: uploadSkill, chooseSkillFolder: chooseSkillFolder, importSkillPath: importSkillPath }
}

function firstObject(values) {
  return Array.isArray(values) ? values.filter(function (value) { return value && typeof value === "object" })[0] || {} : {}
}

function variableLines(values) {
  if (Array.isArray(values)) {
    return values.map(function (value) {
      if (!value || typeof value !== "object") return ""
      var name = String(value.name || value.key || "").trim()
      var configured = value.value === undefined ? value.default || "" : value.value
      return name ? name + "=" + String(configured || "") : ""
    }).filter(Boolean).join("\n")
  }
  if (!values || typeof values !== "object") return ""
  return Object.keys(values).map(function (name) { return name + "=" + String(values[name] || "") }).join("\n")
}

function preferredVariables(primary, fallback) {
  if (Array.isArray(primary) && primary.length) return primary
  if (primary && typeof primary === "object" && Object.keys(primary).length) return primary
  return fallback
}

function packageLaunchContext(packageSpec, version) {
  if (!packageSpec || typeof packageSpec !== "object") return { command: "", args: "" }
  var registryType = String(packageSpec.registryType || packageSpec.registry_type || "").toLowerCase()
  var identifier = String(packageSpec.identifier || "").trim()
  var command = String(packageSpec.runtimeHint || packageSpec.command || (registryType === "npm" ? "npx" : registryType === "pypi" ? "uvx" : ""))
  var args = Array.isArray(packageSpec.runtimeArguments) ? packageSpec.runtimeArguments.slice() : []
  if (identifier) {
    if (registryType === "npm") args.push(identifier + (version ? "@" + version : ""))
    else if (registryType === "pypi") args.push(identifier + (version ? "==" + version : ""))
    else args.push(identifier)
  }
  if (Array.isArray(packageSpec.packageArguments)) args = args.concat(packageSpec.packageArguments)
  return { command: command, args: args.map(String).join("\n") }
}

function manualMcpContext(item) {
  var fallback = item && item.fallback_request || {}
  var request = fallback.request && typeof fallback.request === "object" ? fallback.request : fallback
  var config = request.config && typeof request.config === "object" ? request.config : {}
  var installableRemote = firstObject(item && item.installable_remotes)
  var installablePackage = firstObject(item && item.installable_packages)
  var remote = request.remote && typeof request.remote === "object" ? request.remote
    : installableRemote.url ? installableRemote : firstObject(item && item.remotes)
  var packageSpec = request.package && typeof request.package === "object" ? request.package
    : Object.keys(installablePackage).length ? installablePackage : firstObject(item && item.packages)
  var version = String(config.version || request.version || item && (item.resolved_version || item.version) || packageSpec.version || "")
  var launch = packageLaunchContext(packageSpec, version)
  var remoteType = String(remote.type || remote.transport || config.transport || "streamable_http").toLowerCase().replace(/-/g, "_")
  var useRemote = !!String(config.url || remote.url || "").trim()
  var configuredArgs = Array.isArray(config.args) ? config.args : []
  return {
    name: String(config.name || itemId(item)),
    transport: useRemote ? (remoteType === "sse" ? "sse" : "streamable_http") : String(config.transport || "stdio"),
    url: String(config.url || remote.url || ""), command: String(config.command || launch.command || ""),
    args: configuredArgs.length ? configuredArgs.join("\n") : String(config.args || launch.args || ""),
    version: version, headers: variableLines(preferredVariables(config.headers, remote.headers)),
    env: variableLines(preferredVariables(config.env, packageSpec.environmentVariables)),
  }
}

function useMcpController(model, catalogController, installController) {
  var t = model.t
  function clearMcpSecrets() {
    model.setManualMcp(function (current) { return Object.assign({}, current, { headers: "", env: "" }) })
  }
  function configureMcp(item) {
    model.setManualMcp(manualMcpContext(item))
    model.setManualOpen(true)
  }
  function updateManual(field, value) {
    model.setManualMcp(Object.assign({}, model.manualMcp, { [field]: value }))
  }
  function installManualMcp(event) {
    event.preventDefault()
    var manualMcp = model.manualMcp
    var name = manualMcp.name.trim()
    var config = { name: name, transport: manualMcp.transport, enabled: true, version: manualMcp.version.trim() }
    try {
      if (manualMcp.transport === "stdio") {
        config.command = manualMcp.command.trim()
        config.args = manualMcp.args.split(/\r?\n/).map(function (value) { return value.trim() }).filter(Boolean)
        config.env = parseVariables(manualMcp.env, text(t, "settings.pluginCenterEnvironment", "Environment variables", "环境变量"), t)
      } else {
        config.url = manualMcp.url.trim()
        config.headers = parseVariables(manualMcp.headers, text(t, "settings.pluginCenterHeaders", "Headers", "请求头"), t)
      }
    } catch (error) {
      catalogController.notify(error.message, "error")
      return
    }
    if (!name || (config.transport === "stdio" ? !config.command : !config.url)) {
      catalogController.notify(text(t, "settings.pluginCenterMcpRequired", "Name and connection details are required.", "请填写名称和连接信息。"), "error")
      return
    }
    installController.startInstall({ id: name, name: name }, {
      config: config, version: config.version, source: { type: "manual" },
    }).then(function (payload) {
      if (!payload) return
      clearMcpSecrets()
      model.setManualOpen(false)
    }).catch(function () {})
  }
  return { clearMcpSecrets: clearMcpSecrets, configureMcp: configureMcp, updateManual: updateManual, installManualMcp: installManualMcp }
}

function useMutationController(model, catalogController) {
  var t = model.t
  function toggleItem(item) {
    var kind = String(item && item.kind || model.kind)
    var id = kind === "agent" ? String(installationId(item) || agentId(item)) : itemId(item)
    model.setBusy("toggle:" + id)
    jsonRequest("/api/plugin-center/" + encodeURIComponent(kind) + "/" + encodeURIComponent(id) + "/enabled", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: item.enabled === false }),
    }).then(function () {
      return catalogController.refreshAfterMutation(text(t, "settings.saved", "Saved", "已保存"))
    }).catch(function (error) {
      catalogController.notify(error && error.message || String(error), "error")
    }).finally(function () { model.setBusy("") })
  }
  function removeItem(item) {
    var kind = String(item && item.kind || model.kind)
    var id = kind === "agent" ? String(installationId(item) || agentId(item)) : itemId(item)
    var capabilities = Array.isArray(item && item.capabilities) ? item.capabilities : []
    var removableVersion = capabilities.indexOf("uninstall_managed") >= 0 ? item.managed_version : item.version
    var version = (kind === "cli" || kind === "toolchain") && removableVersion ? "?version=" + encodeURIComponent(String(removableVersion)) : ""
    var message = t("settings.extensionRemoveConfirm", { name: itemName(item) }, "Remove {name}?")
    var feedback = workbenchServices.feedback()
    var confirmation = feedback && typeof feedback.confirmModal === "function"
      ? feedback.confirmModal({ body: message, confirmLabel: t(kind === "mcp" ? "settings.delete" : "settings.uninstall", kind === "mcp" ? "Delete" : "Uninstall"), danger: true })
      : Promise.resolve(typeof window.confirm !== "function" || window.confirm(message))
    Promise.resolve(confirmation).then(function (confirmed) {
      if (!confirmed) return
      model.setBusy("remove:" + id)
      return jsonRequest("/api/plugin-center/" + encodeURIComponent(kind) + "/" + encodeURIComponent(id) + version, { method: "DELETE" }).then(function () {
        return catalogController.refreshAfterMutation(text(t, "settings.pluginCenterRemoved", "Removed from the Plugin Center.", "已从插件中心移除。"))
      }).catch(function (error) {
        catalogController.notify(error && error.message || String(error), "error")
      }).finally(function () { model.setBusy("") })
    }).catch(function (error) { catalogController.notify(error && error.message || String(error), "error") })
  }
  return { toggleItem: toggleItem, removeItem: removeItem }
}

function useLegacyCatalogController(model, catalogController, installController) {
  var t = model.t
  function mutate(path, init, busyKey, message) {
    model.setBusy(busyKey)
    return jsonRequest(path, init).then(function () {
      return catalogController.refreshAfterMutation(message || text(t, "settings.saved", "Saved", "已保存"))
    }).catch(function (error) {
      catalogController.notify(error && error.message || String(error), "error")
      throw error
    }).finally(function () { model.setBusy("") })
  }
  function installCatalogItem(item) {
    var kind = String(item && item.kind || model.kind)
    if (model.kind !== "recommended" || item && item.id === "tex") {
      if (model.kind === "recommended") model.setKind(kind)
      model.setQuery(item && item.id === "tex" ? "TeX" : displayName(item, t))
      model.setRequestedVersion(String(item && (item.recommended_version || item.version) || ""))
      model.setResults(item && item.id === "tex" ? [{ ...item, install_request: { version: item.recommended_version || "latest", distribution: "tinytex" } }] : [])
      model.setCursor("")
      model.setInstallerOpen(true)
      return
    }
    installController.startInstall({ ...item, kind: kind }, {}).catch(function () {})
  }
  function setDefault(item, version) {
    mutate("/api/plugin-center/toolchain/" + encodeURIComponent(item.id) + "/default", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: version }) }, item.key, text(t, "settings.saved", "Saved", "已保存")).catch(function () {})
  }
  function beginBind(item) {
    if (window.cyrene && typeof window.cyrene.pickExtensionPath === "function") {
      Promise.resolve(window.cyrene.pickExtensionPath({ directory: false, title: t("settings.extensionBindTitle", "Use an existing local program") })).then(function (picked) {
        if (picked && !picked.cancelled && picked.path) model.setBindItem({ ...item, selectedPath: String(picked.path) })
      }).catch(function (error) { catalogController.notify(error.message || String(error), "error") })
    } else model.setBindItem(item)
  }
  function saveBinding(path) {
    var item = model.bindItem
    if (!item) return
    var kind = item.kind === "cli" ? "cli" : "toolchain"
    mutate("/api/plugin-center/" + kind + "/" + encodeURIComponent(item.id) + "/bind", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: path }) }, item.key).then(function () { model.setBindItem(null) }).catch(function () {})
  }
  function unbind(item) {
    var kind = item.kind === "cli" ? "cli" : "toolchain"
    mutate("/api/plugin-center/" + kind + "/" + encodeURIComponent(item.id) + "/unbind", { method: "POST" }, item.key).catch(function () {})
  }
  function configureCliHook(item) {
    var id = itemId(item)
    model.setBusy("cli-hook:" + id)
    jsonRequest("/api/plugin-center/cli/" + encodeURIComponent(id) + "/configure-hook", { method: "POST" }).then(function () {
      catalogController.notify(t("settings.extensionHookStarted", "Hook assessment started in the background."), "success")
    }).catch(function (error) {
      catalogController.notify(error && error.message || String(error), "error")
    }).finally(function () { model.setBusy("") })
  }
  function installAgent(agent) {
    installController.startInstall({ ...agent, id: agentId(agent), name: displayName({ id: agentId(agent), name: agent.displayName }, t), kind: "agent" }, {}).catch(function () {})
  }
  function replaceAgent(nextAgent) {
    var target = installationId(nextAgent)
    model.setCatalog(function (current) {
      var agents = current.agents || { recommended: [], installed: [] }
      var installed = (agents.installed || []).map(function (item) { return installationId(item) === target ? { ...item, ...nextAgent } : item })
      return { ...current, agents: { ...agents, installed: installed } }
    })
  }
  return { installCatalogItem: installCatalogItem, setDefault: setDefault, beginBind: beginBind, saveBinding: saveBinding, unbind: unbind, configureCliHook: configureCliHook, installAgent: installAgent, replaceAgent: replaceAgent, mutateLegacy: mutate }
}

function focusKindTab(kind) {
  setTimeout(function () {
    var nextTab = document.getElementById("wb-plugin-center-tab-" + kind)
    if (nextTab) nextTab.focus()
  }, 0)
}

function usePopoverController(model, catalogController, mcpController) {
  function closePopover(refocus) {
    model.setOpen(false)
    model.setInstallerOpen(false)
    model.setSourcesOpen(false)
    model.setProposalOpen(false)
    model.setMcpEditorItem(null)
    model.setBindItem(null)
    mcpController.clearMcpSecrets()
    if (model.props.onClose) model.props.onClose()
    if (refocus && model.triggerRef.current) model.triggerRef.current.focus()
  }
  function togglePopover() {
    if (model.open) closePopover(false)
    else { model.setNotice(""); model.setOpen(true) }
  }
  function selectKind(nextKind) {
    if (model.availableKinds.indexOf(nextKind) < 0) return false
    if (nextKind === model.kind || model.busy) return false
    model.loadSequenceRef.current += 1
    model.searchSequenceRef.current += 1
    model.setKind(nextKind)
    model.setCatalog(EMPTY_CATALOG)
    model.setFilterQuery("")
    model.setQuery("")
    model.setResults([])
    model.setCursor("")
    model.setSearchBusy(false)
    model.setSkillSelection(null)
    model.setTask(null)
    model.setInstallerOpen(false)
    model.setRequestedVersion("")
    model.setNotice("")
    mcpController.clearMcpSecrets()
    return true
  }
  function moveTabFocus(event) {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].indexOf(event.key) < 0) return
    event.preventDefault()
    var kinds = model.availableKinds
    if (!kinds.length) return
    var index = Math.max(0, kinds.indexOf(model.kind))
    if (event.key === "Home") index = 0
    else if (event.key === "End") index = kinds.length - 1
    else if (event.key === "ArrowLeft") index = (index - 1 + kinds.length) % kinds.length
    else index = (index + 1) % kinds.length
    if (selectKind(kinds[index])) focusKindTab(kinds[index])
  }
  useEffectSt(function () {
    if (model.loadAbortRef.current) model.loadAbortRef.current.abort()
    if (model.searchAbortRef.current) model.searchAbortRef.current.abort()
    if (!model.availableKinds.length) {
      model.setOpen(false)
      if (model.props.onClose) model.props.onClose()
      return
    }
    if (model.availableKinds.indexOf(model.kind) < 0) selectKind(model.availableKinds[0])
  }, [model.availableKinds.join("|")])
  useEffectSt(function () {
    if (!model.open) return undefined
    var loadKind = catalogController.loadKind
    var refreshRuntime = catalogController.refreshRuntime
    var kind = model.kind
    Promise.all([loadKind(kind), refreshRuntime()]).catch(function () {})
    var focusTimer = setTimeout(function () {
      var firstTab = model.panelRef.current && model.panelRef.current.querySelector('[role="tab"][aria-selected="true"]')
      if (firstTab) firstTab.focus()
    }, 0)
    function closeOnOutsidePointer(event) {
      if (model.props.inline) return
      var root = model.rootRef.current
      if (root && !root.contains(event.target)) closePopover(false)
    }
    function closeOnEscape(event) {
      if (event.key !== "Escape") return
      var feedback = workbenchServices.feedback()
      var feedbackState = feedback && typeof feedback.snapshot === "function" ? feedback.snapshot() : null
      if (feedbackState && Array.isArray(feedbackState.confirms) && feedbackState.confirms.length) return
      event.preventDefault()
      event.stopPropagation()
      var overlays = model.overlayStateRef.current || {}
      if (overlays.mcpEditorItem) { model.setMcpEditorItem(null); return }
      if (overlays.bindItem) { model.setBindItem(null); return }
      if (overlays.proposalOpen) { model.setProposalOpen(false); return }
      if (overlays.sourcesOpen) { model.setSourcesOpen(false); return }
      if (overlays.installerOpen) { model.setInstallerOpen(false); mcpController.clearMcpSecrets(); return }
      closePopover(true)
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer, true)
    document.addEventListener("keydown", closeOnEscape, true)
    return function () {
      clearTimeout(focusTimer)
      document.removeEventListener("pointerdown", closeOnOutsidePointer, true)
      document.removeEventListener("keydown", closeOnEscape, true)
    }
  }, [model.open, model.kind])
  return { closePopover: closePopover, togglePopover: togglePopover, selectKind: selectKind, moveTabFocus: moveTabFocus }
}

function usePluginCenterController(props) {
  var model = usePluginCenterModel(props)
  var catalog = useCatalogController(model)
  var tasks = useTaskController(model, catalog)
  var installer = useInstallController(model, catalog, tasks)
  var search = useSearchController(model, catalog)
  var skills = useSkillSearchController(model, catalog, installer)
  var skillImport = useSkillImportController(model, catalog)
  var mcp = useMcpController(model, catalog, installer)
  var mutations = useMutationController(model, catalog)
  var legacy = useLegacyCatalogController(model, catalog, installer)
  var popover = usePopoverController(model, catalog, mcp)
  return Object.assign({}, model, catalog, tasks, installer, search, skills, skillImport, mcp, mutations, legacy, popover, {
    mutating: !!model.busy,
  })
}

function PluginCenterTrigger(props) {
  var c = props.controller
  return <button ref={c.triggerRef} type="button" className="wb-btn wb-plugin-center-add-trigger"
    aria-haspopup="dialog" aria-expanded={c.open ? "true" : "false"} aria-controls="wb-plugin-center-add-dialog"
    disabled={c.props.disabled} onClick={c.togglePopover}>
    <AddIcon />{text(c.t, "settings.pluginCenterAdd", "More", "更多")}
  </button>
}

function PluginCenterAddButton(props) {
  return <button type="button" className="wb-btn wb-plugin-center-add-trigger"
    disabled={props.disabled} onClick={props.onClick}>
    <AddIcon />{text(props.t, "settings.pluginCenterAdd", "More", "更多")}
  </button>
}

function PluginCenterHeader(props) {
  var c = props.controller
  return <header className="wb-plugin-center-popover-head">
    <div className="wb-plugin-center-popover-heading"><h3 id="wb-plugin-center-add-title">{text(c.t, "settings.extensionCenter", "Plugin Center", "插件中心")}</h3>
      <p>{text(c.t, "settings.extensionsSubtitle", "Install and manage Skills, MCP servers, command-line tools, runtimes, and Agents.", "安装与管理 Skill、MCP 服务、命令行工具、运行环境和 Agent。")}</p>
    </div>
    <div className="wb-plugin-center-popover-actions">
      {c.extensionsAvailable && <button type="button" className="wb-btn" disabled={c.mutating} onClick={function () { c.setSourcesOpen(true) }}>{text(c.t, "settings.extensionSources", "Sources", "源配置")}</button>}
      {c.kind !== "recommended" && c.kind !== "agent" && <button type="button" className="wb-btn primary wb-extension-install-button" disabled={c.mutating} onClick={function () {
        c.setQuery(""); c.setResults([]); c.setCursor(""); c.setRequestedVersion(""); c.setSkillSelection(null); c.setInstallerOpen(true)
      }}>{c.t("settings.extensionInstallAction." + c.kind, kindText(c.kind, c.t))}</button>}
      {c.props.inline
        ? <button type="button" className="wb-btn wb-plugin-center-back" onClick={function () { c.closePopover(false) }}><span aria-hidden="true">←</span>{text(c.t, "common.back", "Back", "返回")}</button>
        : <button type="button" className="wb-plugin-center-close" aria-label={text(c.t, "common.close", "Close", "关闭")} onClick={function () { c.closePopover(true) }}>×</button>}
    </div>
  </header>
}

function PluginCenterTabs(props) {
  var c = props.controller
  return <div className="wb-plugin-center-tabs" role="tablist"
    aria-label={text(c.t, "settings.pluginCenterAddType", "Plugin type", "插件类型")} onKeyDown={c.moveTabFocus}>
    {c.availableKinds.map(function (tabKind) {
      return <button key={tabKind} type="button" id={'wb-plugin-center-tab-' + tabKind} role="tab"
        aria-selected={c.kind === tabKind ? "true" : "false"} aria-controls={'wb-plugin-center-panel-' + tabKind}
        tabIndex={c.kind === tabKind ? 0 : -1} disabled={c.mutating} onClick={function () { c.selectKind(tabKind) }}>
        <PluginCenterGlyph kind={tabKind} /><span className="wb-plugin-center-tab-label">{kindText(tabKind, c.t)}</span>
      </button>
    })}
  </div>
}

function SkillLocalSection(props) {
  var c = props.controller
  var pickerAvailable = window.cyrene && typeof window.cyrene.pickExtensionPath === "function"
  return <section className="wb-plugin-center-local">
    <div className="wb-plugin-center-section-title"><div>
      <strong>{text(c.t, "settings.pluginCenterLocalSkill", "Import a local Skill", "导入本地 Skill")}</strong>
      <small>{text(c.t, "settings.pluginCenterLocalSkillHint", "Use a .md/.zip file or a local folder path.", "支持 .md/.zip 文件或本地文件夹路径。")}</small>
    </div>
      <button type="button" className="wb-btn" disabled={c.mutating} onClick={function () { if (c.fileRef.current) c.fileRef.current.click() }}>{text(c.t, "settings.pluginCenterChooseFile", "Choose file", "选择文件")}</button>
      <input ref={c.fileRef} className="wb-plugin-center-file-input" type="file" accept=".md,.zip" tabIndex="-1" disabled={c.mutating} onChange={c.uploadSkill} />
    </div>
    <form className="wb-plugin-center-path-form" onSubmit={c.importSkillPath}>
      <input className="wb-input" value={c.localPath} disabled={c.mutating} onChange={function (event) { c.setLocalPath(event.target.value) }}
        placeholder={text(c.t, "settings.pluginCenterSkillPath", "Local Skill folder path", "本地 Skill 文件夹路径")}
        aria-label={text(c.t, "settings.pluginCenterSkillPath", "Local Skill folder path", "本地 Skill 文件夹路径")} />
      {pickerAvailable && <button type="button" className="wb-btn" disabled={c.mutating} onClick={c.chooseSkillFolder}>{text(c.t, "settings.pluginCenterBrowse", "Browse", "浏览")}</button>}
      <button type="submit" className="wb-btn primary" disabled={c.mutating || !c.localPath.trim()}>{text(c.t, "settings.pluginCenterImport", "Import", "导入")}</button>
    </form>
  </section>
}

function ManualMcpConnectionFields(props) {
  var c = props.controller
  if (c.manualMcp.transport === "stdio") {
    return <React.Fragment>
      <label className="wide"><span>{text(c.t, "settings.pluginCenterCommand", "Command", "命令")}</span><input className="wb-input mono" value={c.manualMcp.command} disabled={c.mutating} onChange={function (event) { c.updateManual("command", event.target.value) }} /></label>
      <label className="wide"><span>{text(c.t, "settings.pluginCenterArguments", "Arguments (one per line)", "参数（每行一项）")}</span><textarea className="wb-input mono" rows="3" value={c.manualMcp.args} disabled={c.mutating} onChange={function (event) { c.updateManual("args", event.target.value) }} /></label>
      <label className="wide"><span>{text(c.t, "settings.pluginCenterEnvironment", "Environment variables", "环境变量")}</span><textarea className="wb-input mono" rows="3" value={c.manualMcp.env} disabled={c.mutating} placeholder="API_KEY=…" onChange={function (event) { c.updateManual("env", event.target.value) }} /></label>
    </React.Fragment>
  }
  return <React.Fragment>
    <label className="wide"><span>URL</span><input className="wb-input mono" type="url" value={c.manualMcp.url} disabled={c.mutating} onChange={function (event) { c.updateManual("url", event.target.value) }} /></label>
    <label className="wide"><span>{text(c.t, "settings.pluginCenterHeaders", "Headers", "请求头")}</span><textarea className="wb-input mono" rows="3" value={c.manualMcp.headers} disabled={c.mutating} placeholder="Authorization=Bearer …" onChange={function (event) { c.updateManual("headers", event.target.value) }} /></label>
  </React.Fragment>
}

function ManualMcpForm(props) {
  var c = props.controller
  return <form className="wb-plugin-center-manual-form" onSubmit={c.installManualMcp}>
    <label><span>{text(c.t, "settings.name", "Name", "名称")}</span><input className="wb-input" value={c.manualMcp.name} disabled={c.mutating} onChange={function (event) { c.updateManual("name", event.target.value) }} /></label>
    <label><span>{text(c.t, "settings.pluginCenterTransport", "Transport", "传输方式")}</span>
      <select className="wb-select" value={c.manualMcp.transport} disabled={c.mutating} onChange={function (event) { c.updateManual("transport", event.target.value) }}>
        <option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option><option value="stdio">stdio</option>
      </select>
    </label>
    <ManualMcpConnectionFields controller={c} />
    <label><span>{text(c.t, "settings.pluginCenterVersion", "Version (optional)", "版本（可选）")}</span><input className="wb-input mono" value={c.manualMcp.version} disabled={c.mutating} onChange={function (event) { c.updateManual("version", event.target.value) }} /></label>
    <div className="wb-plugin-center-manual-actions"><button type="submit" className="wb-btn primary" disabled={c.mutating}>{text(c.t, "settings.pluginCenterConnect", "Connect", "连接")}</button></div>
  </form>
}

function McpManualSection(props) {
  var c = props.controller
  function toggleManual() {
    if (c.manualOpen) c.clearMcpSecrets()
    c.setManualOpen(!c.manualOpen)
  }
  return <section className="wb-plugin-center-manual">
    <button type="button" className="wb-plugin-center-manual-toggle" disabled={c.mutating} aria-expanded={c.manualOpen ? "true" : "false"} onClick={toggleManual}>
      <span><strong>{text(c.t, "settings.pluginCenterManualMcp", "Configure an MCP server manually", "手工配置 MCP 服务")}</strong>
        <small>{text(c.t, "settings.pluginCenterManualMcpHint", "Connect an HTTP, SSE, or stdio server.", "连接 HTTP、SSE 或 stdio 服务。")}</small>
      </span><span aria-hidden="true">{c.manualOpen ? "−" : "+"}</span>
    </button>
    {c.manualOpen && <ManualMcpForm controller={c} />}
  </section>
}

function SkillSelection(props) {
  var c = props.controller
  if (!c.skillSelection) return null
  return <div className="wb-plugin-center-skill-selection">
    <div className="wb-plugin-center-section-title"><div>
      <strong>{text(c.t, "settings.pluginCenterSelectSkills", "Select Skills to install", "选择要安装的 Skill")}</strong>
      <small>{text(c.t, "settings.pluginCenterSelectSkillsHint", "Only the selected folders will be snapshotted.", "仅会快照选中的文件夹。")}</small>
    </div></div>
    <div className="wb-plugin-center-skill-options">
      {c.skillSelection.candidates.map(function (candidate) {
        var path = String(candidate.path || ".")
        return <label key={path}>
          <input type="checkbox" checked={!!c.skillSelection.selected[path]} disabled={c.mutating} onChange={function (event) {
            c.setSkillSelection(function (current) {
              var selected = Object.assign({}, current.selected, { [path]: event.target.checked })
              return Object.assign({}, current, { selected: selected })
            })
          }} />
          <span><strong>{String(candidate.name || path)}</strong>{candidate.description && <small>{String(candidate.description)}</small>}<code>{path}</code></span>
        </label>
      })}
    </div>
    <div className="wb-plugin-center-selection-actions">
      <button type="button" className="wb-btn" disabled={c.mutating} onClick={function () { c.setSkillSelection(null) }}>{text(c.t, "common.cancel", "Cancel", "取消")}</button>
      <button type="button" className="wb-btn primary" disabled={c.mutating} onClick={c.installSelectedSkills}>{text(c.t, "settings.pluginCenterInstallSelected", "Install selected", "安装所选项")}</button>
    </div>
  </div>
}

function discoveryHint(kind, t) {
  if (kind === "skill") return text(t, "settings.pluginCenterSearchSkillHint", "Search repositories containing SKILL.md.", "搜索包含 SKILL.md 的仓库。")
  if (kind === "mcp") return text(t, "settings.pluginCenterSearchMcpHint", "Search the configured MCP registry.", "搜索已配置的 MCP 注册表。")
  return text(t, "settings.pluginCenterSearchCliHint", "Search reviewed CLI sources and the managed runtime registry.", "搜索受信任的 CLI 来源与托管运行时注册表。")
}

function DiscoveryResults(props) {
  var c = props.controller
  if (!c.results.length) return null
  return <div className="wb-plugin-center-results">
    {c.results.map(function (item, index) {
      var id = itemId(item)
      return <SearchResult key={id + ":" + index} kind={c.kind} item={item} t={c.t}
        installed={!!props.installedIds[id]} busy={c.mutating}
        onInstall={function () { c.installSearchResult(item) }} onConfigure={function () { c.configureMcp(item) }} />
    })}
  </div>
}

function DiscoverySection(props) {
  var c = props.controller
  return <section className="wb-plugin-center-discovery">
    <div className="wb-plugin-center-section-title"><div>
      <strong>{text(c.t, "settings.pluginCenterDiscover", "Discover", "发现") + " " + kindText(c.kind, c.t)}</strong>
      <small>{discoveryHint(c.kind, c.t)}</small>
    </div></div>
    <form className="wb-plugin-center-search" onSubmit={function (event) { c.search(event, false) }}>
      <input className="wb-input" value={c.query} disabled={c.mutating} onChange={function (event) { c.changeQuery(event.target.value) }}
        placeholder={text(c.t, "settings.pluginCenterSearchPlaceholder", "Search by name", "按名称搜索")}
        aria-label={text(c.t, "settings.pluginCenterSearchPlaceholder", "Search by name", "按名称搜索")} />
      <button type="submit" className="wb-btn primary" disabled={c.searchBusy || c.mutating}>
        {c.searchBusy ? text(c.t, "settings.loading", "Loading…", "加载中…") : text(c.t, "settings.search", "Search", "搜索")}
      </button>
    </form>
    {c.kind === "cli" && <label className="wb-plugin-center-advanced">
      <input type="checkbox" checked={c.advanced} disabled={c.mutating} onChange={function (event) { c.changeAdvanced(event.target.checked) }} />
      <span>{text(c.t, "settings.pluginCenterAdvancedCli", "Include advanced community sources", "包含高级社区来源")}</span>
    </label>}
    <SkillSelection controller={c} />
    <DiscoveryResults controller={c} installedIds={props.installedIds} />
    {c.cursor && <button type="button" className="wb-btn wb-plugin-center-load-more" disabled={c.searchBusy || c.mutating} onClick={function () { c.search(null, true) }}>
      {c.searchBusy ? text(c.t, "settings.loading", "Loading…", "加载中…") : text(c.t, "settings.pluginCenterLoadMore", "Load more", "加载更多")}
    </button>}
  </section>
}

function catalogSearchText(item, t) {
  return [displayName(item, t), item && item.id, displayDescription(item, t), item && item.version,
    item && item.description].filter(Boolean).join(" ").toLowerCase()
}

function mergedTasks(c) {
  var tasks = (c.catalog.tasks || []).slice(), selectedId = taskId(c.task)
  if (!selectedId) return tasks
  var found = false
  tasks = tasks.map(function (item) {
    if (taskId(item) !== selectedId) return item
    found = true
    return c.task
  })
  if (!found) tasks.unshift(c.task)
  return tasks
}

function CatalogList(props) {
  var c = props.controller, query = c.filterQuery.trim().toLowerCase()
  var items = (c.catalog.items || []).filter(function (item) { return !query || catalogSearchText(item, c.t).indexOf(query) >= 0 })
  return <React.Fragment>
    <div className="wb-extension-filter">
      <input className="wb-input" value={c.filterQuery} disabled={c.loading} onChange={function (event) { c.setFilterQuery(event.target.value) }}
        placeholder={c.t("settings.extensionFilter", "Filter installed extensions")} aria-label={c.t("settings.extensionFilter", "Filter installed extensions")} />
      <span>{c.t("settings.extensionCount", { n: items.length }, String(items.length) + " items")}</span>
    </div>
    <div className="wb-extension-list" aria-busy={c.loading ? "true" : "false"}>
      {c.loading && !items.length && <div className="wb-extensions-empty">{c.t("settings.loading", "Loading…")}</div>}
      {!c.loading && !items.length && <div className="wb-extensions-empty">{c.t("settings.extensionEmpty", "No matching extensions.")}</div>}
      {items.map(function (item) {
        return <ExtensionCard key={String(item.key || item.kind + ":" + itemId(item))} item={item} t={c.t} busy={c.mutating}
          onInstall={c.installCatalogItem} onRemove={c.removeItem} onToggle={c.toggleItem}
          onDefault={c.setDefault} onBind={c.beginBind} onUnbind={c.unbind}
          onConfigureHook={c.kind === "cli" ? c.configureCliHook : null}
          onConfigureMcp={function (target) { c.setMcpEditorItem(target) }} />
      })}
    </div>
  </React.Fragment>
}

function PluginCenterBody(props) {
  var c = props.controller
  return <div className="wb-plugin-center-popover-body" id={'wb-plugin-center-panel-' + c.kind}
    role="tabpanel" aria-labelledby={'wb-plugin-center-tab-' + c.kind} aria-busy={c.loading ? "true" : "false"}>
    {c.catalog.python_prompt_required && <button type="button" className="wb-extension-python-callout" onClick={function () {
      if (c.kind !== "recommended") c.selectKind("recommended")
      c.setFilterQuery("Python")
    }}><strong>{c.t("settings.extensionPythonMissingTitle", "Python is recommended")}</strong>
      <span>{c.t("settings.extensionPythonMissingBody", "Install Python here when a Skill or tool needs it.")}</span>
      <span className="wb-extension-callout-action">{c.t("settings.extensionViewInstall", "View Python")}</span></button>}
    {c.notice && <div className={'wb-extension-notice ' + c.noticeKind} role={c.noticeKind === "error" ? "alert" : "status"}>{c.notice}</div>}
    <TaskList tasks={mergedTasks(c)} t={c.t} onCancel={c.cancelTask} />
    {c.kind === "agent" ? <AgentTab t={c.t} listing={c.catalog.agents} busy={c.mutating}
      expandedId={c.agentExpandedId} onToggleExpand={function (id) { c.setAgentExpandedId(c.agentExpandedId === id ? "" : id) }}
      onInstall={c.installAgent} onToggle={c.toggleItem} onRemove={c.removeItem} onChanged={c.replaceAgent}
      notify={c.notify} onOpenProposal={function () { c.setProposalOpen(true) }} /> : <React.Fragment><CatalogList controller={c} />{c.kind === "cli" && <CliHooksPanel t={c.t} notify={c.notify} />}</React.Fragment>}
  </div>
}

function RequestedVersionField(props) {
  var c = props.controller
  if (c.kind !== "cli" && c.kind !== "toolchain") return null
  return <label className="wb-extension-requested-version"><span>{c.t("settings.extensionRequestedVersion", "Requested version")}</span>
    <input className="wb-input mono" value={c.requestedVersion} disabled={c.mutating} onChange={function (event) { c.setRequestedVersion(event.target.value) }} placeholder="latest / lts / 22.14.0" /></label>
}

function TeXChoice(props) {
  var c = props.controller
  if (c.kind !== "toolchain" || !c.results.some(function (item) { return itemId(item) === "tex" })) return null
  return <fieldset className="wb-extension-tex-choice"><legend>{c.t("settings.extensionTeXChoice", "TeX distribution")}</legend>
    {[{ id: "tinytex", title: "settings.extensionTinyTeX", hint: "settings.extensionTinyTeXHint" }, { id: "texlive-full", title: "settings.extensionFullTeX", hint: "settings.extensionFullTeXHint" }].map(function (choice) {
      return <label key={choice.id}><input type="radio" name="plugin-center-tex-distribution" checked={c.texChoice === choice.id} onChange={function () { c.setTexChoice(choice.id) }} />
        <span><strong>{c.t(choice.title, choice.id)}</strong><small>{c.t(choice.hint, "")}</small></span></label>
    })}
  </fieldset>
}

function PluginInstallerDialog(props) {
  var c = props.controller
  if (!c.installerOpen) return null
  var installedIds = {}
  ;(c.catalog.items || []).forEach(function (item) { installedIds[itemId(item)] = true })
  function close() { if (!c.mutating) { c.setInstallerOpen(false); c.setSkillSelection(null); c.clearMcpSecrets() } }
  return <div className="wb-extension-modal-scrim" onMouseDown={function (event) { if (event.target === event.currentTarget) close() }}>
    <section className="wb-extension-modal wb-plugin-center-installer-modal" role="dialog" aria-modal="true" aria-labelledby="plugin-center-installer-title">
      <header><div><h3 id="plugin-center-installer-title">{c.t("settings.extensionInstallTitle." + c.kind, kindText(c.kind, c.t))}</h3>
        <p>{c.t("settings.extensionInstallSubtitle." + c.kind, "")}</p></div>
        <button type="button" className="wb-extension-close" disabled={c.mutating} onClick={close} aria-label={c.t("settings.close", "Close")}>×</button></header>
      <div className="wb-plugin-center-installer-body">
        {c.notice && <div className={'wb-extension-notice ' + c.noticeKind} role={c.noticeKind === "error" ? "alert" : "status"}>{c.notice}</div>}
        {c.kind === "skill" && <SkillLocalSection controller={c} />}
        {c.kind === "mcp" && <McpManualSection controller={c} />}
        <RequestedVersionField controller={c} />
        <DiscoverySection controller={c} installedIds={installedIds} />
        <TeXChoice controller={c} />
      </div>
    </section>
  </div>
}

function PluginCenterOverlays(props) {
  var c = props.controller
  function proposalStarted(payload) {
    var nextTask = payload && payload.task
    if (nextTask) {
      c.setTask(nextTask)
      c.setCatalog(function (current) { return Object.assign({}, current, { tasks: [nextTask].concat(current.tasks || []) }) })
      c.notify(c.t("settings.pluginCenterInstallStarted", "Installation started."), "success")
    }
    return c.loadKind("agent").catch(function () {})
  }
  return <React.Fragment>
    <PluginInstallerDialog controller={c} />
    {c.sourcesOpen && <SourcesDialog t={c.t} notify={c.notify} onClose={function () { c.setSourcesOpen(false) }} />}
    {c.bindItem && <BindDialog item={c.bindItem} t={c.t} busy={c.mutating} onSave={c.saveBinding} onClose={function () { c.setBindItem(null) }} />}
    {c.mcpEditorItem && <McpConfigurationDialog item={c.mcpEditorItem} t={c.t} onClose={function () { c.setMcpEditorItem(null) }} onSaved={function () {
      return c.refreshAfterMutation(c.t("settings.saved", "Saved"))
    }} />}
    {c.proposalOpen && <AgentProposalDialog t={c.t} onClose={function () { c.setProposalOpen(false) }} onStarted={proposalStarted} />}
  </React.Fragment>
}

function PluginCenterDialog(props) {
  var c = props.controller
  if (!c.open || !c.availableKinds.length) return null
  return <section ref={c.panelRef} id={c.props.inline ? "wb-plugin-center-page" : "wb-plugin-center-add-dialog"}
    className={'wb-plugin-center-popover' + (c.props.inline ? ' wb-plugin-center-page' : '')}
    role={c.props.inline ? "region" : "dialog"} aria-modal={c.props.inline ? undefined : "false"} aria-labelledby="wb-plugin-center-add-title">
    <PluginCenterHeader controller={c} /><PluginCenterTabs controller={c} /><PluginCenterBody controller={c} /><PluginCenterOverlays controller={c} />
  </section>
}

function PluginCenterPage(props) {
  var controller = usePluginCenterController(Object.assign({}, props, { inline: true }))
  return <PluginCenterDialog controller={controller} />
}

function PluginCenterAddPopover(props) {
  var controller = usePluginCenterController(props)
  return <div className="wb-plugin-center-add" ref={controller.rootRef}>
    <PluginCenterTrigger controller={controller} /><PluginCenterDialog controller={controller} />
  </div>
}

export { PluginCenterAddButton, PluginCenterAddPopover, PluginCenterPage }
