import {
  useStateSt,
  renderSettingsMarkdown,
  ExternalChevron,
  Toggle,
} from "./shared.jsx"

function displayName(item, t) {
  item = item || {}
  return t("settings.extensionCatalog." + item.id + ".name", item.name || item.id || "—")
}

function displayDescription(item, t) {
  item = item || {}
  return t("settings.extensionCatalog." + item.id + ".description", item.description || "—")
}

function sourceLabel(item, t) {
  item = item || {}
  var source = item.source
  var type = item.ownership === "system" ? "system" : item.ownership === "builtin" ? "builtin" : ""
  var details = []
  if (typeof source === "string") {
    var value = source.trim(), lower = value.toLowerCase()
    if (!type && lower === "manual") type = "manual"
    else if (!type && (lower.indexOf("github") >= 0)) type = "github"
    else if (!type && (lower.indexOf("registry") >= 0 || /^https?:\/\//.test(lower))) type = "registry"
    if (value && lower !== type && lower !== "cyrene-catalog") details.push(value.replace(/^github:/i, ""))
  }
  if (source && typeof source === "object") {
    var rawType = String(source.type || source.kind || "").toLowerCase().replace(/_/g, "-")
    var mappings = { bundled: "builtin", builtin: "builtin", uv: "uv", mise: "mise", "github-release": "githubRelease", "mcp-registry": "mcpRegistry", "mcp-registry-package": "mcpRegistry", github: "github", local: "local", directory: "local", file: "local", archive: "local", upload: "local", manual: "manual", registry: "registry", system: "system" }
    if (!type) type = mappings[rawType] || (source.path ? "local" : source.transport ? "manual" : "")
    ;[source.ref, source.identifier, source.repo, source.tag].filter(Boolean).forEach(function (value) { details.push(String(value)) })
  }
  return [t("settings.extensionSource." + (type || "unknown")), ...details].filter(Boolean).join(" · ")
}

function healthLabel(item, t) {
  var value = String(item.kind === "mcp" ? (item.connection_status || item.health) : item.health || "unknown").toLowerCase().replace(/-/g, "_")
  var aliases = { missing_bundle: "missingBundle", error: "unhealthy", failed: "unhealthy", disabled: "disconnected" }
  return t("settings.extensionHealthValue." + (aliases[value] || value), t("settings.extensionHealthValue.unknown", "Unknown"))
}

function extensionIconMarkup(name) {
  if (typeof window === "undefined") return ""
  var assets = window.CyreneIconAssets
  var icons = assets && assets.extensions
  return icons && typeof icons[name] === "string" ? icons[name] : ""
}

function ExtensionGlyph(props) {
  var icon = String(props.icon || "").trim().toLowerCase()
  var markup = extensionIconMarkup(icon)
  if (markup) return <span className={'wb-extension-brand-icon icon-' + icon.replace(/[^a-z0-9_-]+/g, "-")} dangerouslySetInnerHTML={{ __html: markup }} />
  var paths = {
    skill: "M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-4Z",
    mcp: "M8 12h8M12 8v8M5 5h4v4H5zM15 15h4v4h-4z",
    cli: "M4 5h16v14H4zM7 9l3 3-3 3M12 15h5",
    toolchain: "M14.7 6.3a4 4 0 0 0-5 5L3 18l3 3 6.7-6.7a4 4 0 0 0 5-5l-3 3-3-3z",
    agent: "M8 9h8M9 14h.01M15 14h.01M12 3v3M5 7h14v12H5z",
  }
  var path = paths[props.kind]
  if (!path) return <span className="wb-extension-glyph-text">{String(props.label || "P").slice(0, 1).toUpperCase()}</span>
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={path} /></svg>
}

function statusMeta(item, t) {
  var key = "missing"
  var label = t("settings.extensionNotInstalled", "Not installed")
  if (item.ownership === "builtin") { key = item.health === "healthy" ? "builtin" : "warning"; label = t("settings.extensionBuiltin", "Built in") }
  else if (item.ownership === "system") { key = "system"; label = t("settings.extensionSystemInstalled", "System installed") }
  else if (item.ownership === "cyrene") { key = item.health === "healthy" ? "managed" : "warning"; label = t("settings.extensionManagedInstalled", "Managed by Cyrene") }
  if (item.kind === "mcp") { key = item.connection_status === "connected" ? "managed" : "warning"; label = t(item.connection_status === "connected" ? "settings.connected" : "settings.disconnected", item.connection_status || "disconnected") }
  if (item.observed_state === "installed" && item.enabled === false) { key = "disabled"; label = t("settings.extensionDisabled", "Disabled") }
  return { key: key, label: label }
}

function ExtensionStatus(props) {
  var meta = statusMeta(props.item || {}, props.t)
  return <span className={'wb-extension-status ' + meta.key}><span className="wb-extension-status-dot" aria-hidden="true" />{meta.label}</span>
}

function SkillFileDirectory(props) {
  var rows = [], directories = {}
  ;(Array.isArray(props.files) ? props.files : []).forEach(function (file) {
    var path = String(file.path || file.name || ""), parts = path.split(/[\\/]/).filter(Boolean)
    parts.slice(0, -1).forEach(function (part, index) {
      var directoryPath = parts.slice(0, index + 1).join("/")
      if (!directories[directoryPath]) { directories[directoryPath] = true; rows.push({ key: "directory:" + directoryPath, name: part + "/", path: directoryPath, depth: index, directory: true }) }
    })
    rows.push({ key: "file:" + path, name: parts[parts.length - 1] || path || "—", path: path, depth: Math.max(0, parts.length - 1), size: Number(file.size || 0) })
  })
  return <div className="wb-extension-skill-files" role="list">{rows.map(function (row) {
    var entrypoint = !row.directory && (row.path === props.entrypoint || row.name === props.entrypoint)
    var size = row.size < 1024 ? row.size + " B" : (row.size / 1024).toFixed(1) + " KB"
    return <div key={row.key} className={'wb-extension-skill-file' + (row.directory ? ' directory' : '') + (entrypoint ? ' entrypoint' : '')} role="listitem" style={{ paddingLeft: (11 + row.depth * 14) + "px" }} title={row.path}>
      <span className="wb-extension-skill-file-name mono">{row.name}</span>{entrypoint && <span className="wb-extension-skill-entrypoint">SKILL</span>}{!row.directory && <span className="wb-extension-skill-file-size">{size}</span>}
    </div>
  })}</div>
}

function McpToolDetails(props) {
  var item = props.item, t = props.t, tools = Array.isArray(item.tools) ? item.tools : []
  return <section className="wb-extension-mcp-tools">
    <div className="wb-extension-mcp-tools-head"><h4>{t("settings.extensionMcpTools", "Available tools")}</h4><span>{t("settings.toolsCount", { n: tools.length }, String(tools.length))}</span></div>
    {tools.length ? <ul>{tools.map(function (tool) { return <li key={String(tool.plugin || tool.name)}><code>{String(tool.plugin || tool.name)}</code><p>{tool.description || t("settings.extensionMcpToolNoDescription", "No description provided.")}</p></li> })}</ul>
      : <div className="wb-extension-mcp-tools-empty">{t(item.enabled === false ? "settings.extensionMcpToolsDisabled" : item.connection_status !== "connected" ? "settings.extensionMcpToolsDisconnected" : "settings.extensionMcpToolsEmpty")}</div>}
  </section>
}

function SkillDetails(props) {
  var item = props.item, t = props.t, files = Array.isArray(item.files) ? item.files : []
  return <div className="wb-extension-skill-content">
    <section className="wb-extension-skill-document"><h4>{t("settings.extensionSkillContent", "Skill instructions")}</h4>
      {item.preview ? <div className="wb-extension-skill-markdown markdown" dangerouslySetInnerHTML={{ __html: renderSettingsMarkdown(item.preview) }} /> : <div className="wb-extension-skill-empty">{t("settings.extensionSkillContentEmpty", "No Skill instructions found.")}</div>}
    </section>
    <aside className="wb-extension-skill-directory"><div className="wb-extension-skill-directory-head"><h4>{t("settings.extensionSkillDirectory", "Skill file directory")}</h4><span>{t("settings.extensionSkillFileCount", { n: files.length }, String(files.length))}</span></div>
      {files.length ? <SkillFileDirectory files={files} entrypoint={String(item.entrypoint_name || "SKILL.md")} /> : <div className="wb-extension-skill-empty">{t("settings.extensionSkillDirectoryEmpty", "No files found.")}</div>}
    </aside>
  </div>
}

function ExtensionDetails(props) {
  var item = props.item, t = props.t, busy = props.busy
  var capabilities = Array.isArray(item.capabilities) ? item.capabilities : []
  var canToggle = item.observed_state === "installed" && capabilities.some(function (value) { return value === "enable" || value === "disable" })
  var canBind = capabilities.indexOf("bind_system") >= 0, canUnbind = capabilities.indexOf("unbind_system") >= 0
  var version = String(item.version || item.recommended_version || "").replace(/^python\s+/i, "").replace(/^v/, "")
  return <div className="wb-extension-details">
    {canToggle && <div className="wb-extension-enabled-row"><div><strong>{t("settings.extensionEnabledTitle", "Enabled in Cyrene")}</strong><small>{t("settings.extensionEnabledHint." + item.kind, "")}</small></div>{Toggle(item.enabled !== false, function () { props.onToggle(item) }, busy, t("settings.extensionToggle", { name: displayName(item, t) }))}</div>}
    <dl><div><dt>{t("settings.extensionSource", "Source")}</dt><dd>{sourceLabel(item, t)}</dd></div><div><dt>{t("settings.extensionVersion", "Version")}</dt><dd className="mono">{version || "—"}</dd></div><div><dt>{t("settings.extensionPath", "Path")}</dt><dd className="mono">{item.path || "—"}</dd></div><div><dt>{t("settings.extensionHealth", "Health")}</dt><dd>{healthLabel(item, t)}</dd></div>{item.ownership === "system" && item.managed_available && <div><dt>{t("settings.extensionManagedInstalled", "Managed by Cyrene")}</dt><dd className="mono">{[item.managed_version, item.managed_path].filter(Boolean).join(" · ") || "—"}</dd></div>}</dl>
    {item.kind === "mcp" && <React.Fragment>
      <div className="wb-extension-mcp-runtime-meta"><span>{t("settings.pluginPack", "Plugin pack")}</span><code>{item.pack_id || ("mcp." + item.id)}</code></div>
      {item.error && <div className="wb-extension-task-error" role="alert"><strong>{t("settings.mcpConnectionError", "Connection error")}</strong><p>{String(item.error)}</p></div>}
      <McpToolDetails item={item} t={t} />
      {props.onConfigureMcp && <div className="wb-extension-detail-actions"><button type="button" className="wb-btn" disabled={busy} onClick={function () { props.onConfigureMcp(item) }}>{t("settings.mcpConfiguration", "Provider configuration")}</button></div>}
    </React.Fragment>}
    {item.kind === "toolchain" && (item.versions || []).length > 1 && <label className="wb-extension-version-select"><span>{t("settings.extensionDefaultVersion", "Default version")}</span><select className="wb-select" value={item.default_version || item.version} disabled={busy} onChange={function (event) { props.onDefault(item, event.target.value) }}>{item.versions.map(function (value) { return <option key={value} value={value}>{value}</option> })}</select></label>}
    {(canBind || canUnbind) && <div className="wb-extension-detail-actions">{canBind && <button type="button" className="wb-btn" disabled={busy} onClick={function () { props.onBind(item) }}>{t(item.manual_binding ? "settings.extensionChangeSystem" : "settings.extensionBindSystem")}</button>}{canUnbind && <button type="button" className="wb-btn danger" disabled={busy} onClick={function () { props.onUnbind(item) }}>{t("settings.extensionUnbindSystem")}</button>}</div>}
    {item.kind === "cli" && props.onConfigureHook && <div className="wb-extension-hook-action"><div className="wb-extension-hook-copy"><div><strong>{t("settings.extensionHookTitle", "Agent Hook")}</strong><small>{t("settings.extensionHookHint", "Run this CLI from lifecycle hooks.")}</small></div></div><button type="button" className="wb-btn" disabled={busy} onClick={function () { props.onConfigureHook(item) }}>{t("settings.extensionConfigureHook", "Configure Hook")}</button></div>}
    {item.kind === "skill" && <SkillDetails item={item} t={t} />}
  </div>
}

function ExtensionCard(props) {
  var item = props.item || {}, t = props.t, busy = props.busy
  var [expanded, setExpanded] = useStateSt(false)
  var capabilities = Array.isArray(item.capabilities) ? item.capabilities : []
  var canInstall = capabilities.indexOf("install") >= 0
  var canRemove = capabilities.some(function (value) { return value === "uninstall" || value === "uninstall_managed" || value === "remove" })
  var name = displayName(item, t), version = String(item.version || item.recommended_version || "").replace(/^python\s+/i, "").replace(/^v/, "")
  return <article className={'wb-extension-card' + (expanded ? ' expanded' : '')}>
    <div className="wb-extension-card-main"><button type="button" className="wb-extension-card-summary" onClick={function () { setExpanded(!expanded) }} aria-expanded={expanded ? "true" : "false"} aria-label={t("settings.extensionDetailsFor", { name: name }, "Show details for {name}")}>
      <span className={'wb-extension-glyph ' + item.kind + (item.icon ? ' branded' : '')}><ExtensionGlyph kind={item.kind} icon={item.icon} label={name} /></span><span className="wb-extension-copy"><span className="wb-extension-title-row"><strong>{name}</strong><span className="wb-extension-type">{t("settings.extensionType." + item.kind, item.kind)}</span>{item.recommended && <span className="wb-extension-recommended">{t("settings.extensionRecommendedInstall", "Recommended")}</span>}</span><span className="wb-extension-description">{displayDescription(item, t)}</span><span className="wb-extension-meta"><ExtensionStatus item={item} t={t} />{version && <span className="mono">{version}</span>}{Number(item.tool_count || 0) > 0 && <span>{t("settings.toolsCount", { n: item.tool_count })}</span>}</span></span>
    </button><div className="wb-extension-actions">{canInstall && <button type="button" className="wb-btn primary" disabled={busy} onClick={function () { props.onInstall(item) }}>{t("settings.install", "Install")}</button>}{canRemove && (item.ownership === "cyrene" || item.managed_available) && <button type="button" className="wb-btn danger" disabled={busy} onClick={function () { props.onRemove(item) }}>{t(item.kind === "mcp" ? "settings.delete" : "settings.uninstall")}</button>}</div>
      <button type="button" className="wb-extension-expand-button" onClick={function () { setExpanded(!expanded) }} aria-expanded={expanded ? "true" : "false"} aria-label={t("settings.extensionDetailsFor", { name: name })}><span className="wb-extension-chevron" aria-hidden="true">{ExternalChevron()}</span></button></div>
    {expanded && <ExtensionDetails {...props} item={item} />}
  </article>
}

function taskVisible(task, now) {
  if (["queued", "running", "cancelling"].indexOf(String(task.status)) >= 0) return true
  if (["failed", "interrupted"].indexOf(String(task.status)) < 0) return false
  var finishedAt = Date.parse(task.finished_at || "")
  return Number.isFinite(finishedAt) && now - finishedAt < 30000
}

function TaskList(props) {
  var t = props.t, tasks = (props.tasks || []).filter(function (task) { return taskVisible(task, props.now || Date.now()) }).slice(0, 4)
  if (!tasks.length) return null
  return <section className="wb-extension-tasks" aria-label={t("settings.extensionTasks", "Installation tasks")}><h3>{t("settings.extensionTasks", "Installation tasks")}</h3>{tasks.map(function (task) {
    var progress = Math.max(0, Math.min(100, Number(task.progress) || 0)), failed = ["failed", "interrupted"].indexOf(task.status) >= 0
    return <article key={task.id} className={'wb-extension-task ' + task.status}><div className="wb-extension-task-head"><strong>{String(task.extension_id || task.name || "")}</strong><span className="wb-extension-task-status">{t("settings.extensionTaskStatus." + task.status, task.status)}</span></div><div className="wb-extension-task-progress-row"><div className="wb-extension-task-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress}><span style={{ width: progress + "%" }} /></div><span className="wb-extension-task-percent">{progress}%</span></div>{failed && <div className="wb-extension-task-error" role="alert"><strong>{t("settings.extensionInstallFailed", "Installation failed")}</strong><p>{String(task.message || "")}</p>{task.error && <details><summary>{t("settings.extensionTaskTechnicalDetails", "Technical details")}</summary><pre>{String(task.error)}</pre></details>}</div>}{["queued", "running"].indexOf(task.status) >= 0 && <div className="wb-extension-task-actions"><button type="button" className="wb-btn" onClick={function () { props.onCancel(task) }}>{t("settings.cancel", "Cancel")}</button></div>}</article>
  })}</section>
}

export {
  ExtensionCard,
  ExtensionGlyph,
  TaskList,
  displayName,
  displayDescription,
}
