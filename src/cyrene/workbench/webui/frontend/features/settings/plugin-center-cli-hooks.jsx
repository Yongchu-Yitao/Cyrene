import { workbenchServices, useStateSt, useEffectSt, readSettingsResponse, settingsFetch, showSettingsToast } from "./shared.jsx"

var EVENTS = ["PreToolUse", "PostToolUse", "SessionStart", "TurnStart", "SessionEnd", "Stop"]
var SYSTEM_EVENTS = ["ContextChange", "ContextUsed"].concat(EVENTS)
var LOCALIZED_RUNTIME_HOOK_IDS = {
  "agent-session-context-mount": true, "agent-session-transition": true, "core-permission-review": true,
  "cyrene-browser-session-end": true, "cyrene-browser-stop": true, "cyrene-cli-post-tool-use": true,
  "cyrene-cli-pre-tool-use": true, "cyrene-cli-session-end": true, "cyrene-cli-session-start": true,
  "cyrene-cli-stop": true, "cyrene-composer-context-session-start": true, "cyrene-composer-context-turn-start": true,
  "cyrene-context-session-start": true, "cyrene-context-turn-start": true,
  "cyrene-entity-proactive-session-start": true, "cyrene-entity-proactive-turn-start": true,
  "cyrene-global-hooks-post-tool-use": true, "cyrene-global-hooks-pre-tool-use": true,
  "cyrene-global-hooks-session-end": true, "cyrene-global-hooks-session-start": true, "cyrene-global-hooks-stop": true,
  "cyrene-memory-context_used": true, "cyrene-memory-session_end": true, "cyrene-memory-session_start": true,
  "cyrene-memory-stop": true, "cyrene-skills-learning-post-tool-use": true,
  "cyrene-skills-learning-session-end": true, "cyrene-skills-learning-session-start": true,
  "cyrene-skills-learning-stop": true, "cyrene-soul-session-start": true,
  "cyrene-subagent-spawn-policy-turn-start": true, "cyrene-system-prompt-session-start": true,
}

function request(path, init) {
  return settingsFetch(path, init).then(readSettingsResponse).then(function (payload) {
    if (payload && payload.ok === false) throw new Error(String(payload.error || payload.detail || workbenchServices.i18n().t("settings.requestFailed")))
    return payload
  })
}

function emptyDraft() {
  return { name: "", event: "PostToolUse", matcher: "*", runnerType: "command", executable: "", scriptPath: "", args: "", timeout: "10", priority: "100", failure: "open", description: "" }
}

function emptyRequestDraft() {
  return { name: "", event: "PostToolUse", matcher: "", actionInstruction: "", description: "" }
}

function isToolEvent(event) {
  return event === "PreToolUse" || event === "PostToolUse"
}

function draftFromHook(item) {
  var runner = item && item.runner || {}
  return {
    name: String(item && (item.name || item.id) || ""),
    event: String(item && item.event || "PostToolUse"),
    matcher: String(item && item.matcher || "*"),
    runnerType: runner.type === "script" ? "script" : "command",
    executable: String(runner.executable || ""),
    scriptPath: String(runner.path || ""),
    args: Array.isArray(runner.args) ? runner.args.join("\n") : "",
    timeout: String(item && item.timeout_seconds || 10),
    priority: String(item && item.priority === 0 ? 0 : item && item.priority || 100),
    failure: String(item && item.failure_policy || "open"),
    description: String(item && item.description || ""),
    actionInstruction: String(item && item.action_instruction || ""),
    enabled: item && item.enabled === false ? false : true,
  }
}

function systemDraftFromHook(item) {
  var action = item && item.action || { type: "plugin" }
  return {
    hookId: String(item && item.id || ""),
    event: String(item && item.event || "SessionStart"),
    pluginId: String(item && item.plugin_id || ""),
    enabled: item && item.enabled !== false,
    rootOnly: item && item.root_only === true,
    matcher: String(item && item.matcher || ""),
    failure: String(item && item.failure_policy || "open"),
    configText: JSON.stringify(item && item.config || {}, null, 2),
    createdAt: String(item && item.created_at || ""),
    actionType: ["command", "script"].indexOf(action.type) >= 0 ? action.type : "plugin",
    actionExecutable: String(action.executable || ""),
    actionScriptPath: String(action.path || ""),
    actionArgs: Array.isArray(action.args) ? action.args.join("\n") : "",
    actionTimeout: String(action.timeout_seconds || 10),
  }
}

function HookGlyph() {
  var assets = window.CyreneIconAssets
  var markup = assets && assets.settings && assets.settings.webhook || ""
  return <span className="wb-hook-glyph" aria-hidden="true" dangerouslySetInnerHTML={{ __html: markup }} />
}

function ChevronGlyph(props) {
  var assets = window.CyreneIconAssets
  var markup = assets && assets.settings && assets.settings["chevron-down"] || ""
  return <span className={'wb-hook-chevron' + (props.expanded ? ' expanded' : '')} aria-hidden="true" dangerouslySetInnerHTML={{ __html: markup }} />
}

function humanizeHookId(value) {
  return String(value || "").replace(/^cyrene[-_.]/, "").replace(/[-_.]+/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase() })
}

function hookDisplayName(item, t) {
  if (item && item.readonly !== true && item.name) return item.name
  var id = String(item && item.id || "")
  return LOCALIZED_RUNTIME_HOOK_IDS[id] ? t("settings.runtimeHookName." + id) : humanizeHookId(id) || id
}

function hookDisplayDescription(item, t) {
  if (item && item.description) return item.description
  return t("settings.hookRegisteredBy", { plugin: String(item && item.plugin_id || "") }, "Registered by {plugin}.")
}

function hookEventLabel(event, t) {
  return t("settings.hookEventName." + String(event || ""), null, String(event || ""))
}

function hookFailureLabel(policy, t) {
  if (policy === "block") return t("settings.hookFailureBlock")
  if (policy === "closed") return t("settings.hookFailureClosed")
  return t("settings.hookFailureOpen")
}

function toolOptionLabel(item, t) {
  var language = "en"
  try { language = workbenchServices.i18n().getLang() || "en" } catch (error) {}
  var translations = item && item.i18n && typeof item.i18n === "object" ? item.i18n : {}
  var localized = translations[language] || translations[String(language).split("-")[0]] || {}
  var runtimeName = String(item && item.name || item && item.id || "")
  var label = String(localized && localized.name || t("toolName." + String(item && item.id || runtimeName), runtimeName) || runtimeName)
  return label && label !== runtimeName ? label + " · " + runtimeName : runtimeName
}

function ToolMatcherSelect(props) {
  var tools = Array.isArray(props.tools) ? props.tools : []
  var value = String(props.value || "").trim()
  if (value === "*") value = ""
  var known = !value || tools.some(function (item) { return String(item && item.name || "") === value })
  return <label><span>{props.t("settings.hookTool")} · {props.t("settings.optional")}</span>
    <select className="wb-select" value={value} disabled={props.busy} onChange={function (event) { props.onChange(event.target.value) }}>
      <option value="">{props.t("settings.hookToolAll")}</option>
      {!known && <option value={value}>{props.t("settings.hookToolCurrent", { name: value })}</option>}
      {tools.map(function (item) { var name = String(item && item.name || ""); return <option key={String(item && item.id || name)} value={name}>{toolOptionLabel(item, props.t)}{item && item.enabled === false ? " · " + props.t("settings.hookToolDisabled") : ""}</option> })}
    </select><small>{props.t("settings.hookToolHint")}</small>
  </label>
}

function hookDateLabel(value) {
  try { return workbenchServices.i18n().formatDate(value, { dateStyle: "medium", timeStyle: "short" }) }
  catch (error) { return String(value || "") }
}

function hookItemKey(item) {
  return (item && item.readonly === true ? "system:" : "user:") + [item && item.id, item && item.event, item && item.plugin_id].filter(Boolean).join(":")
}

function hookSearchText(item, t) {
  var runner = item && item.runner || {}
  var action = item && item.action || {}
  return [hookDisplayName(item, t), hookDisplayDescription(item, t), hookEventLabel(item && item.event, t), item && item.name, item && item.id, item && item.event, item && item.matcher,
    item && item.description, item && item.plugin_id, item && item.source,
    runner.executable, runner.path, action.executable, action.path].concat(Array.isArray(runner.args) ? runner.args : []).concat(Array.isArray(action.args) ? action.args : []).filter(Boolean).join(" ").toLowerCase()
}

function CliHookProposal(props) {
  var item = props.item || {}, hook = item.hook || {}, extension = item.extension || {}
  return <article className="wb-cli-hook-proposal">
    <div><strong>{extension.name || extension.id || hook.name}</strong><small>{item.rationale || hook.description}</small>
      <code>{hookEventLabel(hook.event, props.t)}{hook.matcher && hook.matcher !== "*" ? " · " + hook.matcher : ""}</code></div>
    <div className="wb-cli-hook-row-actions">
      <button type="button" className="wb-btn" disabled={props.busy} onClick={function () { props.onDecide(item, false) }}>{props.t("settings.reject", "Reject")}</button>
      <button type="button" className="wb-btn primary" disabled={props.busy} onClick={function () { props.onDecide(item, true) }}>{props.t("settings.approve", "Approve")}</button>
    </div>
  </article>
}

function CliHookItem(props) {
  var item = props.item || {}, runner = item.runner || {}
  var action = item.action || { type: "plugin" }
  var target = action.type === "command" ? action.executable : action.type === "script" ? action.path : runner.executable || runner.path || item.plugin_id || "—"
  var expanded = props.expanded === true
  var system = item.readonly === true
  var agentStatus = item.configured_by_agent === true ? String(item.configuration_status || "ready") : "ready"
  var ready = agentStatus === "ready"
  var statusClass = system ? (item.current === false ? "historical" : "current") : agentStatus === "configuring" ? "configuring" : agentStatus === "failed" ? "failed" : (item.enabled === true ? "enabled" : "disabled")
  var statusLabel = system ? (item.current === false ? props.t("settings.hookHistorical", "Historical") : props.t("settings.hookCurrent", "Current")) : agentStatus === "configuring" ? props.t("settings.hookAgentConfiguring") : agentStatus === "failed" ? props.t("settings.hookAgentFailed") : (item.enabled === true ? props.t("settings.hookEnabled", "Enabled") : props.t("settings.hookDisabled", "Disabled"))
  return <article className={'wb-cli-hook-item wb-hook-card' + (!system && item.enabled !== true ? ' disabled' : '') + (expanded ? ' expanded' : '')}>
    <div className="wb-hook-card-summary">
    <button type="button" className="wb-hook-card-toggle" onClick={function () { props.onExpand(item) }} aria-expanded={expanded ? "true" : "false"} aria-label={props.t(expanded ? "settings.hookCollapseDetails" : "settings.hookExpandDetails", { name: hookDisplayName(item, props.t) })}>
    <div className="wb-hook-card-main"><HookGlyph /><div className="wb-cli-hook-item-copy">
      <div className="wb-hook-title-row"><strong>{hookDisplayName(item, props.t)}</strong><span>{hookEventLabel(item.event, props.t)}</span>
        {item.readonly === true && <em className="system">{props.t("settings.hookSystemManaged", "System")}</em>}
        <em className={statusClass}>{statusLabel}</em>
        {system && item.enabled === false && <em className="disabled">{props.t("settings.hookDisabled", "Disabled")}</em>}</div>
      <small>{item.action_instruction || hookDisplayDescription(item, props.t)}</small>
      <div className="wb-hook-meta">{item.matcher && item.matcher !== "*" && <code>{item.matcher}</code>}
        <code>{target}{Array.isArray(runner.args) && runner.args.length ? " " + runner.args.join(" ") : ""}</code></div>
    </div>
    </div></button>
    <div className="wb-hook-card-actions">
      {!props.editing && (system || props.manageable !== false) && ready && <button type="button" className="wb-btn wb-hook-card-edit" disabled={props.busy} onClick={function () { props.onEdit(item) }}>{props.t(item.configured_by_agent === true ? "settings.hookAdvancedEdit" : "settings.edit", "Edit")}</button>}
      {!system && agentStatus === "failed" && props.manageable !== false && <button type="button" className="wb-btn" disabled={props.busy} onClick={function () { props.onRetry(item) }}>{props.t("settings.hookAgentRetry")}</button>}
      <button type="button" className="wb-hook-chevron-button" disabled={props.busy} onClick={function () { props.onExpand(item) }} aria-expanded={expanded ? "true" : "false"} aria-label={props.t(expanded ? "settings.hookCollapseDetails" : "settings.hookExpandDetails", { name: hookDisplayName(item, props.t) })}><ChevronGlyph expanded={expanded} /></button>
    </div>
    </div>
    {expanded && <div className="wb-hook-details">
      <dl>
        <div><dt>{props.t("settings.hookDetailId", "ID")}</dt><dd><code>{item.id}</code></dd></div>
        <div><dt>{props.t("settings.hookEvent", "Event")}</dt><dd>{hookEventLabel(item.event, props.t)}</dd></div>
        <div><dt>{props.t(system ? "settings.systemHookThen" : "settings.hookDetailTarget", system ? "What to do" : "Execution target")}</dt><dd><code>{target}</code></dd></div>
        {item.matcher && <div><dt>{props.t("settings.hookMatcher", "Tool name or glob")}</dt><dd><code>{item.matcher}</code></dd></div>}
        {item.priority !== undefined && <div><dt>{props.t("settings.hookPriority", "Priority")}</dt><dd>{String(item.priority)}</dd></div>}
        {item.timeout_seconds !== undefined && <div><dt>{props.t("settings.hookTimeout", "Timeout (seconds)")}</dt><dd>{String(item.timeout_seconds)}</dd></div>}
        <div><dt>{props.t("settings.hookFailurePolicy", "On failure")}</dt><dd>{hookFailureLabel(item.failure_policy, props.t)}</dd></div>
        {system && <div><dt>{props.t("settings.hookScope", "Scope")}</dt><dd>{props.t(item.root_only ? "settings.hookRootOnly" : "settings.hookEveryAgent")}</dd></div>}
        {system && <div><dt>{props.t("settings.hookSeenInTrees", "Persisted sessions")}</dt><dd>{props.t("settings.hookTreeCount", { n: Number(item.tree_count || 1) })}</dd></div>}
        {system && item.tree_id && <div><dt>{props.t("settings.hookLatestTree", "Latest session tree")}</dt><dd><code>{item.tree_id}</code></dd></div>}
        {item.created_at && <div><dt>{props.t("settings.hookCreatedAt", "Registered at")}</dt><dd>{hookDateLabel(item.created_at)}</dd></div>}
      </dl>
      {!system && agentStatus === "configuring" && <div className="wb-hook-generation-state" role="status"><span className="wb-hook-generation-spinner" aria-hidden="true" /><div><strong>{props.t("settings.hookAgentConfiguring")}</strong><small>{props.t("settings.hookAgentConfiguringHint")}</small></div></div>}
      {!system && agentStatus === "failed" && <div className="wb-hook-generation-state failed" role="alert"><div><strong>{props.t("settings.hookAgentFailed")}</strong><small>{item.configuration_error || props.t("settings.hookAgentFailedHint")}</small></div></div>}
      {!props.editing && !system && props.manageable !== false && ready && <div className="wb-cli-hook-row-actions">
      <button type="button" className="wb-btn" disabled={props.busy} onClick={function () { props.onTest(item) }}>{props.t("settings.test", "Test")}</button>
      <button type="button" className="wb-btn" disabled={props.busy} onClick={function () { props.onToggle(item) }}>{item.enabled === true ? props.t("settings.pluginCenterDisable", "Disable") : props.t("settings.pluginCenterEnable", "Enable")}</button>
      <button type="button" className="wb-btn danger" disabled={props.busy} onClick={function () { props.onDelete(item) }}>{props.t("settings.pluginCenterRemove", "Remove")}</button>
      </div>}
      {!system && props.manageable === false && <div className="wb-hook-inline-warning">{props.t("settings.hookCliUnavailable")}</div>}
      {props.editing && (system ? <SystemHookEditor item={item} t={props.t} tools={props.tools} draft={props.draft} setDraft={props.setDraft} busy={props.busy} onSave={props.onSaveSystem} onCancel={props.onCancelEdit} /> : item.configured_by_agent === true ? <AgentHookEditor t={props.t} tools={props.tools} draft={props.draft} setDraft={props.setDraft} busy={props.busy} onSave={props.onSaveTuning} onCancel={props.onCancelEdit} /> : <CliHookEditor t={props.t} tools={props.tools} draft={props.draft} setDraft={props.setDraft} busy={props.busy} onSave={props.onSave} onCancel={props.onCancelEdit} editing={true} />)}
    </div>}
  </article>
}

function HookRequestEditor(props) {
  var draft = props.draft
  function update(name, value) { props.setDraft(Object.assign({}, draft, { [name]: value })) }
  return <form className="wb-cli-hook-editor wb-hook-request-editor" onSubmit={props.onSave}>
    <header><h4>{props.t("settings.hookRequestTitle")}</h4><p>{props.t("settings.hookRequestHint")}</p></header>
    <div className="wb-cli-hook-editor-grid">
      <label><span>{props.t("settings.name")} *</span><input className="wb-input" required maxLength="120" value={draft.name} disabled={props.busy} onChange={function (event) { update("name", event.target.value) }} /></label>
      <label><span>{props.t("settings.hookEvent")} *</span><select className="wb-select" value={draft.event} disabled={props.busy} onChange={function (event) { update("event", event.target.value) }}>{EVENTS.map(function (event) { return <option key={event} value={event}>{hookEventLabel(event, props.t)}</option> })}</select></label>
      {isToolEvent(draft.event) && <ToolMatcherSelect t={props.t} tools={props.tools} value={draft.matcher} busy={props.busy} onChange={function (value) { update("matcher", value) }} />}
      <label className="wide"><span>{props.t("settings.hookActionInstruction")} *</span><textarea className="wb-input" required rows="5" maxLength="4000" value={draft.actionInstruction} disabled={props.busy} placeholder={props.t("settings.hookActionInstructionPlaceholder")} onChange={function (event) { update("actionInstruction", event.target.value) }} /><small>{props.t("settings.hookActionInstructionHint")}</small></label>
      <label className="wide"><span>{props.t("settings.description")} · {props.t("settings.optional")}</span><textarea className="wb-input" rows="2" maxLength="500" value={draft.description} disabled={props.busy} onChange={function (event) { update("description", event.target.value) }} /></label>
    </div>
    <div className="wb-cli-hook-editor-actions"><button type="button" className="wb-btn" disabled={props.busy} onClick={props.onCancel}>{props.t("common.cancel")}</button><button type="submit" className="wb-btn primary" disabled={props.busy}>{props.busy ? props.t("settings.hookAgentStarting") : props.t("settings.hookCreateWithAgent")}</button></div>
  </form>
}

function AgentHookEditor(props) {
  var draft = props.draft
  function update(name, value) { props.setDraft(Object.assign({}, draft, { [name]: value })) }
  return <form className="wb-cli-hook-editor wb-hook-tuning-editor" onSubmit={props.onSave}>
    <header><h4>{props.t("settings.hookAdvancedTitle")}</h4><p>{props.t("settings.hookAdvancedReadyHint")}</p></header>
    <section className="wb-system-hook-rule-section">
    <h4>{props.t("settings.systemHookWhen")}</h4>
    <div className="wb-cli-hook-editor-grid">
      <label><span>{props.t("settings.hookEvent")}</span><select className="wb-select" value={draft.event} disabled={props.busy} onChange={function (event) { update("event", event.target.value) }}>{EVENTS.map(function (event) { return <option key={event} value={event}>{hookEventLabel(event, props.t)}</option> })}</select></label>
      {isToolEvent(draft.event) && <ToolMatcherSelect t={props.t} tools={props.tools} value={draft.matcher} busy={props.busy} onChange={function (value) { update("matcher", value) }} />}
    </div>
    </section>
    <section className="wb-system-hook-rule-section">
    <h4>{props.t("settings.systemHookThen")}</h4>
    <div className="wb-cli-hook-editor-grid">
      <label className="wide"><span>{props.t("settings.hookActionInstruction")}</span><textarea className="wb-input" required rows="5" maxLength="4000" value={draft.actionInstruction} disabled={props.busy} placeholder={props.t("settings.hookActionInstructionPlaceholder")} onChange={function (event) { update("actionInstruction", event.target.value) }} /><small>{props.t("settings.hookActionEditHint")}</small></label>
      <label className="wide"><span>{props.t("settings.description")} · {props.t("settings.optional")}</span><textarea className="wb-input" rows="2" maxLength="500" value={draft.description} disabled={props.busy} onChange={function (event) { update("description", event.target.value) }} /></label>
      <label><span>{props.t("settings.hookTimeout")}</span><input className="wb-input" type="number" min="0.1" max="60" step="0.1" required value={draft.timeout} disabled={props.busy} onChange={function (event) { update("timeout", event.target.value) }} /><small>{props.t("settings.hookTimeoutRange")}</small></label>
      <label><span>{props.t("settings.hookPriority")}</span><input className="wb-input" type="number" min="-10000" max="10000" step="1" required value={draft.priority} disabled={props.busy} onChange={function (event) { update("priority", event.target.value) }} /><small>{props.t("settings.hookPriorityRange")}</small></label>
    </div>
    </section>
    <div className="wb-cli-hook-editor-actions"><button type="button" className="wb-btn" disabled={props.busy} onClick={props.onCancel}>{props.t("common.cancel")}</button><button type="submit" className="wb-btn primary" disabled={props.busy}>{props.busy ? props.t("settings.hookAgentStarting") : props.t("settings.hookSaveAndRegenerate")}</button></div>
  </form>
}

function SystemHookEditor(props) {
  var draft = props.draft
  function update(name, value) { props.setDraft(Object.assign({}, draft, { [name]: value })) }
  return <form className="wb-cli-hook-editor wb-system-hook-editor" onSubmit={props.onSave}>
    <div className="wb-system-hook-warning" role="alert"><strong>{props.t("settings.systemHookWarningTitle")}</strong><p>{props.t("settings.systemHookWarningBody")}</p></div>
    <section className="wb-system-hook-rule-section">
    <h4>{props.t("settings.systemHookWhen")}</h4>
    <p>{props.t("settings.systemHookWhenHint")}</p>
    <div className="wb-cli-hook-editor-grid">
      <label><span>{props.t("settings.hookEvent")}</span><select className="wb-select" value={draft.event} disabled={props.busy} onChange={function (event) { var nextEvent = event.target.value; props.setDraft(Object.assign({}, draft, { event: nextEvent, failure: draft.failure === "block" && nextEvent !== "PreToolUse" ? "open" : draft.failure })) }}>{SYSTEM_EVENTS.map(function (event) { return <option key={event} value={event}>{hookEventLabel(event, props.t)}</option> })}</select></label>
      <label><span>{props.t("settings.hookStatus")}</span><select className="wb-select" value={draft.enabled ? "enabled" : "disabled"} disabled={props.busy} onChange={function (event) { update("enabled", event.target.value === "enabled") }}><option value="enabled">{props.t("settings.hookEnabled")}</option><option value="disabled">{props.t("settings.hookDisabled")}</option></select></label>
      <label><span>{props.t("settings.hookScope")}</span><select className="wb-select" value={draft.rootOnly ? "root" : "all"} disabled={props.busy} onChange={function (event) { update("rootOnly", event.target.value === "root") }}><option value="all">{props.t("settings.hookEveryAgent")}</option><option value="root">{props.t("settings.hookRootOnly")}</option></select></label>
      {isToolEvent(draft.event) && <ToolMatcherSelect t={props.t} tools={props.tools} value={draft.matcher} busy={props.busy} onChange={function (value) { update("matcher", value) }} />}
    </div>
    </section>
    <section className="wb-system-hook-rule-section">
    <h4>{props.t("settings.systemHookThen")}</h4>
    <p>{props.t("settings.systemHookThenHint")}</p>
    <div className="wb-cli-hook-editor-grid">
      <label><span>{props.t("settings.systemHookActionType")}</span><select className="wb-select" value={draft.actionType} disabled={props.busy} onChange={function (event) { update("actionType", event.target.value) }}><option value="plugin">{props.t("settings.systemHookActionPlugin")}</option><option value="command">{props.t("settings.hookRunnerCommand")}</option><option value="script">{props.t("settings.hookRunnerScript")}</option></select></label>
      <label><span>{props.t("settings.hookFailurePolicy")}</span><select className="wb-select" value={draft.failure} disabled={props.busy} onChange={function (event) { update("failure", event.target.value) }}><option value="open">{props.t("settings.hookFailureOpen")}</option>{draft.event === "PreToolUse" && <option value="block">{props.t("settings.hookFailureBlock")}</option>}<option value="closed">{props.t("settings.hookFailureClosed")}</option></select></label>
      {draft.actionType === "plugin" && <React.Fragment><label><span>{props.t("settings.systemHookActionPluginId")}</span><input className="wb-input mono" required maxLength="200" value={draft.pluginId} disabled={props.busy} onChange={function (event) { update("pluginId", event.target.value) }} /></label><label className="wide"><span>{props.t("settings.systemHookConfig")}</span><textarea className="wb-input mono" rows="6" value={draft.configText} disabled={props.busy} onChange={function (event) { update("configText", event.target.value) }} /></label></React.Fragment>}
      {draft.actionType === "command" && <label><span>{props.t("settings.hookExecutable")}</span><input className="wb-input mono" required value={draft.actionExecutable} disabled={props.busy} onChange={function (event) { update("actionExecutable", event.target.value) }} /></label>}
      {draft.actionType === "script" && <label><span>{props.t("settings.hookScriptPath")}</span><input className="wb-input mono" required value={draft.actionScriptPath} disabled={props.busy} onChange={function (event) { update("actionScriptPath", event.target.value) }} /></label>}
      {draft.actionType !== "plugin" && <React.Fragment><label><span>{props.t("settings.hookTimeout")}</span><input className="wb-input" type="number" min="0.1" max="60" step="0.1" value={draft.actionTimeout} disabled={props.busy} onChange={function (event) { update("actionTimeout", event.target.value) }} /></label><label className="wide"><span>{props.t("settings.hookArguments")}</span><textarea className="wb-input mono" rows="4" value={draft.actionArgs} disabled={props.busy} placeholder={props.t("settings.hookArgumentsHint")} onChange={function (event) { update("actionArgs", event.target.value) }} /></label></React.Fragment>}
    </div>
    </section>
    <div className="wb-cli-hook-editor-actions"><button type="button" className="wb-btn" disabled={props.busy} onClick={props.onCancel}>{props.t("common.cancel")}</button><button type="submit" className="wb-btn danger" disabled={props.busy}>{props.t("settings.systemHookConfirmSave")}</button></div>
  </form>
}

function CliHookEditor(props) {
  var draft = props.draft
  function update(name, value) { props.setDraft(Object.assign({}, draft, { [name]: value })) }
  return <form className="wb-cli-hook-editor" onSubmit={props.onSave}>
    <div className="wb-cli-hook-editor-grid">
      <label><span>{props.t("settings.name", "Name")}</span><input className="wb-input" required value={draft.name} disabled={props.busy} onChange={function (event) { update("name", event.target.value) }} /></label>
      <label><span>{props.t("settings.hookEvent", "Event")}</span><select className="wb-select" value={draft.event} disabled={props.busy} onChange={function (event) { update("event", event.target.value) }}>{EVENTS.map(function (event) { return <option key={event} value={event}>{hookEventLabel(event, props.t)}</option> })}</select></label>
      {isToolEvent(draft.event) && <ToolMatcherSelect t={props.t} tools={props.tools} value={draft.matcher} busy={props.busy} onChange={function (value) { update("matcher", value) }} />}
      <label><span>{props.t("settings.hookRunnerType", "Runner type")}</span><select className="wb-select" value={draft.runnerType} disabled={props.busy} onChange={function (event) { update("runnerType", event.target.value) }}><option value="command">{props.t("settings.hookRunnerCommand")}</option><option value="script">{props.t("settings.hookRunnerScript")}</option></select></label>
      {draft.runnerType === "script" ? <label><span>{props.t("settings.hookScriptPath", "Executable script path")}</span><input className="wb-input mono" required value={draft.scriptPath} disabled={props.busy} onChange={function (event) { update("scriptPath", event.target.value) }} /></label> : <label><span>{props.t("settings.hookExecutable", "Executable")}</span><input className="wb-input mono" required value={draft.executable} disabled={props.busy} onChange={function (event) { update("executable", event.target.value) }} /></label>}
      <label><span>{props.t("settings.hookTimeout", "Timeout (seconds)")}</span><input className="wb-input" type="number" min="0.1" max="60" step="0.1" value={draft.timeout} disabled={props.busy} onChange={function (event) { update("timeout", event.target.value) }} /></label>
      <label><span>{props.t("settings.hookPriority", "Priority")}</span><input className="wb-input" type="number" min="-10000" max="10000" step="1" value={draft.priority} disabled={props.busy} onChange={function (event) { update("priority", event.target.value) }} /></label>
      {draft.event === "PreToolUse" && <label><span>{props.t("settings.hookFailurePolicy", "On failure")}</span><select className="wb-select" value={draft.failure} disabled={props.busy} onChange={function (event) { update("failure", event.target.value) }}><option value="open">{props.t("settings.hookFailureOpen", "Allow and log")}</option><option value="block">{props.t("settings.hookFailureBlock", "Block tool call")}</option></select></label>}
      <label className="wide"><span>{props.t("settings.hookArguments", "Arguments")}</span><textarea className="wb-input mono" rows="3" value={draft.args} disabled={props.busy} placeholder={props.t("settings.hookArgumentsHint", "One argument per line.")} onChange={function (event) { update("args", event.target.value) }} /></label>
      <label className="wide"><span>{props.t("settings.description", "Description")}</span><textarea className="wb-input" rows="2" value={draft.description} disabled={props.busy} onChange={function (event) { update("description", event.target.value) }} /></label>
    </div>
    <div className="wb-cli-hook-editor-actions"><button type="button" className="wb-btn" disabled={props.busy} onClick={props.onCancel}>{props.t("common.cancel", "Cancel")}</button><button type="submit" className="wb-btn primary" disabled={props.busy}>{props.t(props.editing ? "settings.saveChanges" : "settings.save", props.editing ? "Save changes" : "Save")}</button></div>
  </form>
}

function CliHooksPanel(props) {
  var t = props.t
  var [hooks, setHooks] = useStateSt([]), [systemHooks, setSystemHooks] = useStateSt([]), [proposals, setProposals] = useStateSt([])
  var [tools, setTools] = useStateSt([])
  var [results, setResults] = useStateSt({}), [audit, setAudit] = useStateSt([])
  var [loading, setLoading] = useStateSt(true), [busy, setBusy] = useStateSt("")
  var [customAvailable, setCustomAvailable] = useStateSt(false)
  var [editing, setEditing] = useStateSt(""), [draft, setDraft] = useStateSt(emptyDraft)
  var [expanded, setExpanded] = useStateSt({})
  var [query, setQuery] = useStateSt("")

  function tell(message, level) { if (props.notify) props.notify(message, level || "info") }
  function load(silent) {
    if (!silent) setLoading(true)
    return request("/api/hooks").then(function (payload) {
      var available = payload.custom_available === true
      setCustomAvailable(available)
      setHooks(Array.isArray(payload.hooks) ? payload.hooks : [])
      setSystemHooks(Array.isArray(payload.system_hooks) ? payload.system_hooks : [])
      setTools(Array.isArray(payload.tools) ? payload.tools : [])
      setProposals(Array.isArray(payload.proposals) ? payload.proposals.filter(function (item) { return item.status === "pending" }) : [])
      setResults(payload.configuration_results && typeof payload.configuration_results === "object" ? payload.configuration_results : {})
      if (!available) { setAudit([]); return null }
      return request("/api/plugin-center/cli/hooks/audit?limit=30").then(function (auditPayload) { setAudit(Array.isArray(auditPayload.records) ? auditPayload.records : []) })
    }).catch(function (error) { tell(error.message || String(error), "error") }).finally(function () { if (!silent) setLoading(false) })
  }
  useEffectSt(function () { load() }, [])
  var generationSignature = hooks.filter(function (item) { return item.configuration_status === "configuring" }).map(function (item) { return item.id }).join("|")
  useEffectSt(function () {
    if (!generationSignature) return undefined
    var timer = window.setInterval(function () { load(true) }, 2000)
    return function () { window.clearInterval(timer) }
  }, [generationSignature])

  function mutate(key, path, init, success) {
    setBusy(key)
    return request(path, init).then(function () { if (success) tell(success, "success"); return load() }).catch(function (error) { tell(error.message || String(error), "error") }).finally(function () { setBusy("") })
  }
  function decide(item, approve) {
    mutate("proposal:" + item.id, "/api/plugin-center/cli/hooks/proposals/" + encodeURIComponent(item.id) + "/decision", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve: approve }) }, t(approve ? "settings.hookProposalApproved" : "settings.hookProposalRejected"))
  }
  function toggle(item) {
    mutate("toggle:" + item.id, "/api/plugin-center/cli/hooks/" + encodeURIComponent(item.id) + "/enabled", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: item.enabled !== true }) }, t("settings.saved", "Saved"))
  }
  function test(item) {
    mutate("test:" + item.id, "/api/plugin-center/cli/hooks/" + encodeURIComponent(item.id) + "/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, t("settings.hookTestSucceeded"))
  }
  function remove(item) {
    mutate("delete:" + item.id, "/api/plugin-center/cli/hooks/" + encodeURIComponent(item.id), { method: "DELETE" }, t("settings.pluginCenterRemoved", "Removed from the Plugin Center."))
  }
  function toggleExpanded(item) {
    var key = hookItemKey(item)
    setExpanded(Object.assign({}, expanded, { [key]: !expanded[key] }))
  }
  function edit(item) {
    if (item.configured_by_agent === true && item.configuration_status !== "ready") return
    var key = hookItemKey(item)
    setExpanded(Object.assign({}, expanded, { [key]: true }))
    setDraft(draftFromHook(item))
    setEditing(item.id)
  }
  function retry(item) {
    mutate("retry:" + item.id, "/api/plugin-center/cli/hooks/" + encodeURIComponent(item.id) + "/regenerate", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, t("settings.hookAgentRestarted"))
  }
  function editSystem(item) {
    var title = t("settings.systemHookEditConfirmTitle")
    var body = t("settings.systemHookWarningBody")
    var feedback = workbenchServices.feedback()
    var confirmation = feedback && typeof feedback.confirmModal === "function"
      ? feedback.confirmModal({ title: title, body: body, confirmLabel: t("settings.systemHookContinueEdit"), danger: true })
      : Promise.resolve(typeof window.confirm !== "function" || window.confirm([title, "", body].join("\n")))
    Promise.resolve(confirmation).then(function (confirmed) {
      if (!confirmed) return
      var key = hookItemKey(item)
      setExpanded(Object.assign({}, expanded, { [key]: true }))
      setDraft(systemDraftFromHook(item))
      setEditing("system:" + key)
    })
  }
  function save(event) {
    event.preventDefault()
    var payload = {
      name: draft.name.trim(), event: draft.event, matcher: draft.matcher.trim() || "*", enabled: draft.enabled !== false,
      description: draft.description.trim(), timeout_seconds: Number(draft.timeout || 10), priority: Number(draft.priority || 100),
      failure_policy: draft.event === "PreToolUse" ? draft.failure : "open",
      runner: Object.assign({ type: draft.runnerType, args: draft.args.split(/\r?\n/).map(function (value) { return value.trim() }).filter(Boolean) }, draft.runnerType === "script" ? { path: draft.scriptPath.trim() } : { executable: draft.executable.trim() }),
    }
    setBusy("save")
    var path = editing && editing !== "new" ? "/api/plugin-center/cli/hooks/" + encodeURIComponent(editing) : "/api/plugin-center/cli/hooks"
    request(path, { method: editing && editing !== "new" ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then(function () {
      setEditing(""); setDraft(emptyDraft()); tell(t("settings.hookSaved"), "success"); return load()
    }).catch(function (error) { tell(error.message || String(error), "error") }).finally(function () { setBusy("") })
  }
  function submitGeneration(event) {
    event.preventDefault()
    setBusy("generate")
    request("/api/plugin-center/cli/hooks/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: draft.name.trim(), event: draft.event,
        matcher: isToolEvent(draft.event) ? draft.matcher.trim() || "*" : "*",
        action_instruction: draft.actionInstruction.trim(), description: draft.description.trim(),
      }),
    }).then(function () {
      setEditing(""); setDraft(emptyDraft()); tell(t("settings.hookAgentStarted"), "success"); return load()
    }).catch(function (error) { tell(error.message || String(error), "error") }).finally(function () { setBusy("") })
  }
  function saveTuning(event, item) {
    event.preventDefault()
    setBusy("tune:" + item.id)
    request("/api/plugin-center/cli/hooks/" + encodeURIComponent(item.id) + "/regenerate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        event: draft.event, action_instruction: draft.actionInstruction.trim(),
        matcher: isToolEvent(draft.event) ? draft.matcher.trim() || "*" : "*",
        description: draft.description.trim(), timeout_seconds: Number(draft.timeout),
        priority: Number(draft.priority),
      }),
    }).then(function () {
      setEditing(""); tell(t("settings.hookReconfigurationStarted"), "success"); return load()
    }).catch(function (error) { tell(error.message || String(error), "error") }).finally(function () { setBusy("") })
  }
  function saveSystem(event, item) {
    event.preventDefault()
    var config
    try {
      config = JSON.parse(draft.configText || "{}")
      if (!config || Array.isArray(config) || typeof config !== "object") throw new Error("config")
    } catch (error) {
      tell(t("settings.systemHookConfigInvalid"), "error")
      return
    }
    setBusy("system-save:" + item.id)
    var actionArgs = draft.actionArgs.split(/\r?\n/).map(function (value) { return value.trim() }).filter(Boolean)
    var action = draft.actionType === "plugin" ? { type: "plugin" } : Object.assign({
      type: draft.actionType, args: actionArgs, timeout_seconds: Number(draft.actionTimeout || 10), env: {},
    }, draft.actionType === "script" ? { path: draft.actionScriptPath.trim() } : { executable: draft.actionExecutable.trim() })
    request("/api/hooks/system/" + encodeURIComponent(item.id), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        event: item.event, plugin_id: item.plugin_id,
        new_hook_id: draft.hookId.trim(), new_event: draft.event, new_plugin_id: draft.pluginId.trim(),
        created_at: draft.createdAt.trim(), enabled: draft.enabled === true,
        root_only: draft.rootOnly === true,
        matcher: draft.event === "PreToolUse" || draft.event === "PostToolUse" ? draft.matcher.trim() || null : null,
        failure_policy: draft.failure, config: config, action: action, acknowledge_risk: true,
      }),
    }).then(function () {
      setEditing(""); tell(t("settings.systemHookSaved"), "success"); return load()
    }).catch(function (error) { tell(error.message || String(error), "error") }).finally(function () { setBusy("") })
  }

  var recentResults = Object.keys(results).map(function (key) { return { key: key, value: results[key] } }).slice(-4).reverse()
  var normalizedQuery = query.trim().toLowerCase()
  var visibleHooks = hooks.filter(function (item) { return !normalizedQuery || hookSearchText(item, t).indexOf(normalizedQuery) >= 0 })
  var standalone = props.standalone === true
  var visibleSystemHooks = standalone ? systemHooks.filter(function (item) { return !normalizedQuery || hookSearchText(item, t).indexOf(normalizedQuery) >= 0 }) : []
  var totalVisible = visibleSystemHooks.length + visibleHooks.length
  return <section className={'wb-cli-hooks-panel' + (standalone ? ' wb-cli-hooks-page' : '')}>
    <header><div>{standalone ? <h2>{t("settings.hooks", "Automatic triggers")}</h2> : <h4>{t("settings.agentHooks", "CLI Hooks")}</h4>}<p>{standalone ? t("settings.hooksSubtitle", "Review every system and user-defined lifecycle trigger in one place.") : t("settings.agentHooksSubtitle", "Run approved CLI commands through tree-local Hooks.")}</p></div>{customAvailable && <button type="button" className={'wb-btn' + (standalone ? ' primary' : '')} disabled={!!busy} onClick={function () { setEditing(editing === "new" ? "" : "new"); setDraft(emptyRequestDraft()) }}>{editing === "new" ? t("common.cancel", "Cancel") : t("settings.addHook", "Add trigger")}</button>}</header>
    {editing === "new" && <HookRequestEditor t={t} tools={tools} draft={draft} setDraft={setDraft} busy={busy === "generate"} onSave={submitGeneration} onCancel={function () { setEditing("") }} />}
    {standalone && <div className="wb-hook-filter"><input className="wb-input" value={query} disabled={loading} onChange={function (event) { setQuery(event.target.value) }} placeholder={t("settings.hookFilter", "Filter Hooks")} aria-label={t("settings.hookFilter", "Filter Hooks")} /><span>{t("settings.hookCount", { n: totalVisible }, String(totalVisible) + " Hooks")}</span></div>}
    <div className="wb-cli-hook-group"><h5>{t("settings.userHooks", "User triggers")}</h5>{loading && <div className="wb-extensions-empty">{t("settings.loading", "Loading…")}</div>}{!loading && !visibleHooks.length && <div className="wb-extensions-empty">{normalizedQuery ? t("settings.extensionEmpty", "No matching extensions.") : t(customAvailable ? "settings.hookEmpty" : "settings.hookCustomUnavailable")}</div>}{visibleHooks.map(function (item) { var key = hookItemKey(item); return <CliHookItem key={key} item={item} t={t} tools={tools} busy={!!busy} manageable={customAvailable} expanded={expanded[key] === true} editing={editing === item.id} draft={draft} setDraft={setDraft} onExpand={toggleExpanded} onEdit={edit} onSave={save} onSaveTuning={function (event) { saveTuning(event, item) }} onCancelEdit={function () { setEditing("") }} onToggle={toggle} onTest={test} onDelete={remove} onRetry={retry} /> })}</div>
    {proposals.length > 0 && <div className="wb-cli-hook-group"><h5>{t("settings.hookPendingApprovals", "Pending approvals")}</h5>{proposals.map(function (item) { return <CliHookProposal key={item.id} item={item} t={t} busy={!!busy} onDecide={decide} /> })}</div>}
    {standalone && <div className="wb-cli-hook-group"><h5>{t("settings.systemHooks", "System triggers")}</h5>{loading && <div className="wb-extensions-empty">{t("settings.loading", "Loading…")}</div>}{!loading && !visibleSystemHooks.length && <div className="wb-extensions-empty">{normalizedQuery ? t("settings.extensionEmpty", "No matching extensions.") : t("settings.hookSystemEmpty", "No persisted system triggers found.")}</div>}{visibleSystemHooks.map(function (item) { var key = hookItemKey(item); return <CliHookItem key={key} item={item} t={t} tools={tools} busy={!!busy} expanded={expanded[key] === true} editing={editing === "system:" + key} draft={draft} setDraft={setDraft} onExpand={toggleExpanded} onEdit={editSystem} onSaveSystem={function (event) { saveSystem(event, item) }} onCancelEdit={function () { setEditing("") }} /> })}</div>}
    {recentResults.length > 0 && <div className="wb-cli-hook-group"><h5>{t("settings.extensionHookTitle", "Automatic integration")}</h5>{recentResults.map(function (item) { return <div key={item.key} className="wb-cli-hook-result"><code>{item.key}</code><span>{String(item.value && (item.value.reason || item.value.status) || "")}</span></div> })}</div>}
    {audit.length > 0 && <details className="wb-cli-hook-audit"><summary>{t("settings.hookExecutionLog", "Execution and audit log")}</summary>{audit.slice(0, 10).map(function (item, index) { return <div key={String(item.timestamp || index)}><code>{item.event ? hookEventLabel(item.event, t) : item.action || item.kind}</code><span>{item.status || item.result || item.hook_id}</span></div> })}</details>}
  </section>
}

function HooksPanel(props) {
  return <div className="settings-panel settings-panel-wide wb-hooks-page" id="setting-hooks"><CliHooksPanel t={props.t} notify={function (message, level) {
    showSettingsToast(message, level || "info")
  }} standalone={true} /></div>
}

export { CliHooksPanel, HooksPanel }
