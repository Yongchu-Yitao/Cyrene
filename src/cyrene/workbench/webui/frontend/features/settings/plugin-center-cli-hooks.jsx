import { EditorDialog } from "./editor-dialog.jsx"
import { ExtensionFilter } from "./extension-filter.jsx"
import { Disclosure } from "./disclosure.jsx"
import { ActionIcon } from "./action-icon.jsx"
import { Toggle, workbenchServices, useStateSt, useEffectSt, readSettingsResponse, settingsFetch, showSettingsToast } from "./shared.jsx"

var EVENTS = ["SessionStart", "TurnStart", "ModelStart", "PreToolUse", "PostToolUse", "ContextChange", "ContextUsed", "SessionEnd", "ConversationTurnCommitted", "Stop"]

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

function hookDisplayName(item, t) {
  if (!item) return ""
  if (item.readonly !== true) return item.name || item.id
  const fallback = item.name || t("settings.hookForEvent", { event: hookEventLabel(item.event, t) })
  return t("settings.runtimeHookName." + item.id, null, fallback)
}

function hookLogLabel(value, t) {
  return value ? t("settings.hookLogValue." + value, null, value) : ""
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
  const title = hookDisplayName(item, props.t)
  const detailsId = React.useId()
  const toggleDetails = () => props.onExpand(item)
  const editLabel = props.t(item.configured_by_agent ? "settings.hookAdvancedEdit" : "settings.edit", "Edit")
  function toggleFromCard(event) {
    if (!event.target.closest("button, input, label, .registry-tile-details")) toggleDetails()
  }
  return <article className="registry-tile hook-tile" onClick={toggleFromCard}>
    <div className="registry-tile-head">
      <strong>{title}</strong>
      {!system && props.manageable !== false && ready &&
        Toggle(item.enabled === true, () => props.onToggle(item), props.busy, title)}
      {(system || props.manageable !== false) && ready &&
        <button type="button" className="tile-disclosure" aria-haspopup="dialog" title={editLabel} aria-label={editLabel + " · " + title}
          disabled={props.busy} onClick={() => props.onEdit(item)}><ActionIcon name="pencil"/></button>}
      <button type="button" className="tile-disclosure" aria-expanded={expanded} aria-controls={detailsId}
        aria-label={props.t(expanded ? "settings.hookCollapseDetails" : "settings.hookExpandDetails", {name: title})}
        onClick={toggleDetails}><ActionIcon name="chevron-down"/></button>
    </div>
    <p>{item.action_instruction || hookDisplayDescription(item, props.t)}</p>
    <div className="tile-id"><code>{item.id}</code>
      {(!ready || (system && item.current === false)) && <small>{statusLabel}</small>}
      {system && item.enabled === false && <small>{props.t("settings.hookDisabled", "Disabled")}</small>}
    </div>
    <Disclosure open={expanded} id={detailsId}><div className="registry-tile-details"><div className="wb-hook-details">
      <div className="wb-hook-title-row"><span>{hookEventLabel(item.event, props.t)}</span>
        {system && <em className="system">{props.t("settings.hookSystemManaged", "System")}</em>}
        <em className={statusClass}>{statusLabel}</em>
        {system && item.enabled === false && <em className="disabled">{props.t("settings.hookDisabled", "Disabled")}</em>}
      </div>
      {!system && agentStatus === "failed" && props.manageable !== false &&
        <button type="button" className="wb-btn" disabled={props.busy} onClick={() => props.onRetry(item)}>{props.t("settings.hookAgentRetry")}</button>}
      <dl>
        <div><dt>{props.t("settings.hookDetailId", "ID")}</dt><dd><code>{item.id}</code></dd></div>
        <div><dt>{props.t("settings.hookEvent", "Event")}</dt><dd>{hookEventLabel(item.event, props.t)}</dd></div>
        <div><dt>{props.t(system ? "settings.systemHookThen" : "settings.hookDetailTarget", system ? "What to do" : "Execution target")}</dt><dd><code>{[target, ...(runner.args || [])].join(" ")}</code></dd></div>
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
      {!system && props.manageable !== false && ready && <div className="wb-cli-hook-row-actions">
      <button type="button" className="wb-btn" disabled={props.busy} onClick={function () { props.onTest(item) }}>{props.t("settings.test", "Test")}</button>
      <button type="button" className="wb-btn danger" disabled={props.busy} onClick={function () { props.onDelete(item) }}>{props.t("settings.pluginCenterRemove", "Remove")}</button>
      </div>}
      {!system && props.manageable === false && <div className="wb-hook-inline-warning">{props.t("settings.hookCliUnavailable")}</div>}

    </div></div></Disclosure>
  </article>
}

function HookRequestEditor(props) {
  var draft = props.draft
  function update(name, value) { props.setDraft(Object.assign({}, draft, { [name]: value })) }
  return <form className="wb-cli-hook-editor wb-hook-request-editor" onSubmit={props.onSave}>
    <header><p>{props.t("settings.hookRequestHint")}</p></header>
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
      <label><span>{props.t("settings.hookEvent")}</span><select className="wb-select" value={draft.event} disabled={props.busy} onChange={function (event) { var nextEvent = event.target.value; props.setDraft(Object.assign({}, draft, { event: nextEvent, failure: draft.failure === "block" && nextEvent !== "PreToolUse" ? "open" : draft.failure })) }}>{EVENTS.map(function (event) { return <option key={event} value={event}>{hookEventLabel(event, props.t)}</option> })}</select></label>
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

function useHookEditor() {
  var [editing, setEditing] = useStateSt(""), [draft, setDraft] = useStateSt(emptyDraft)
  var [expanded, setExpanded] = useStateSt({})
  var [query, setQuery] = useStateSt("")
  return { editing, setEditing, draft, setDraft, expanded, setExpanded, query, setQuery };
}

function CliHooksPanel(props) {
  var t = props.t
  var [hooks, setHooks] = useStateSt([]), [systemHooks, setSystemHooks] = useStateSt([]), [proposals, setProposals] = useStateSt([])
  var [tools, setTools] = useStateSt([])
  var [results, setResults] = useStateSt({})
  var [loading, setLoading] = useStateSt(true), [busy, setBusy] = useStateSt("")
  var [customAvailable, setCustomAvailable] = useStateSt(false)
  var [loadError, setLoadError] = useStateSt(false), [systemStatus, setSystemStatus] = useStateSt("empty")
  var { editing, setEditing, draft, setDraft, expanded, setExpanded, query, setQuery } = useHookEditor();
  const [editError, setEditError] = useStateSt("")
  function tell(message, level) { if (props.notify) props.notify(message, level || "info") }
  function load(silent) {
    if (!silent) setLoading(true)
    return request("/api/hooks").then(function (payload) {
      setLoadError(false)
      setSystemStatus(payload.system_hooks_status || "empty")
      var available = payload.custom_available === true
      setCustomAvailable(available)
      setHooks(Array.isArray(payload.hooks) ? payload.hooks : [])
      setSystemHooks(Array.isArray(payload.system_hooks) ? payload.system_hooks : [])
      setTools(Array.isArray(payload.tools) ? payload.tools : [])
      setProposals(Array.isArray(payload.proposals) ? payload.proposals.filter(function (item) { return item.status === "pending" }) : [])
      setResults(payload.configuration_results && typeof payload.configuration_results === "object" ? payload.configuration_results : {})
    }).catch(function (error) { setLoadError(true); tell(error.message || String(error), "error") }).finally(function () { if (!silent) setLoading(false) })
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
    setEditError("")
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
      setEditError("")
      setDraft(systemDraftFromHook(item))
      setEditing("system:" + key)
    })
  }
  function save(event) {
    event.preventDefault()
    setEditError("")
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
    }).catch(function (error) { setEditError(error.message || String(error)) }).finally(function () { setBusy("") })
  }
  function openCreateDialog() {
    setEditError("")
    setDraft(emptyRequestDraft())
    setEditing("new")
  }
  function submitGeneration(event) {
    event.preventDefault()
    setEditError("")
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
    }).catch(function (error) { setEditError(error.message || String(error)) }).finally(function () { setBusy("") })
  }
  function saveTuning(event, item) {
    event.preventDefault()
    setEditError("")
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
    }).catch(function (error) { setEditError(error.message || String(error)) }).finally(function () { setBusy("") })
  }
  function saveSystem(event, item) {
    event.preventDefault()
    setEditError("")
    var config
    try {
      config = JSON.parse(draft.configText || "{}")
      if (!config || Array.isArray(config) || typeof config !== "object") throw new Error("config")
    } catch (error) {
      setEditError(t("settings.systemHookConfigInvalid"))
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
    }).catch(function (error) { setEditError(error.message || String(error)) }).finally(function () { setBusy("") })
  }
  var recentResults = Object.keys(results).map(function (key) { return { key: key, value: results[key] } }).slice(-4).reverse()
  var normalizedQuery = query.trim().toLowerCase()
  var visibleHooks = hooks.filter(function (item) { return !normalizedQuery || hookSearchText(item, t).indexOf(normalizedQuery) >= 0 })
  var standalone = props.standalone === true
  var visibleSystemHooks = standalone ? systemHooks.filter(function (item) { return !normalizedQuery || hookSearchText(item, t).indexOf(normalizedQuery) >= 0 }) : []
  const editedHook = editing && editing !== "new" && (
    hooks.find(item => item.id === editing) ||
    systemHooks.find(item => "system:" + hookItemKey(item) === editing)
  )
  const editorProps = { t, tools, draft, setDraft, busy: !!busy, onCancel: () => setEditing("") }
  var totalVisible = visibleSystemHooks.length + visibleHooks.length
  return <section className={'wb-cli-hooks-panel' + (standalone ? ' wb-cli-hooks-page' : '')}>
    <header>
      <div>
        {standalone ? <h2>{t("settings.hooks", "Automatic triggers")}</h2> : <h4>{t("settings.agentHooks", "CLI Hooks")}</h4>}
        <p>{standalone ? t("settings.hooksSubtitle") : t("settings.agentHooksSubtitle")}</p>
      </div>
      {customAvailable && <button type="button" className={'wb-btn' + (standalone ? ' primary' : '')}
        disabled={!!busy} aria-haspopup="dialog" onClick={openCreateDialog}>{t("settings.addHook")}</button>}
    </header>
    {(editing === "new" || editedHook) && <EditorDialog
      title={editing === "new" ? t("settings.addHook") : hookDisplayName(editedHook, t)}
      busy={!!busy} className="hook-editor-dialog" onClose={editorProps.onCancel}>
      {editError && <div className="wb-tool-dialog-error" role="alert">{editError}</div>}
      {editing === "new" && <HookRequestEditor {...editorProps} onSave={submitGeneration}/>}
      {editedHook && (editedHook.readonly
        ? <SystemHookEditor {...editorProps} item={editedHook} onSave={event => saveSystem(event, editedHook)}/>
        : editedHook.configured_by_agent
          ? <AgentHookEditor {...editorProps} onSave={event => saveTuning(event, editedHook)}/>
          : <CliHookEditor {...editorProps} editing onSave={save}/>)}
    </EditorDialog>}
    {standalone && <ExtensionFilter query={query} onChange={setQuery} disabled={loading} label={t("settings.hookFilter")}
      count={loading ? t("settings.loading") : loadError || systemStatus === "error"
        ? t("settings.hookCountUnavailable") : t("settings.hookCount", { n: totalVisible })}/>}
    {!loading && loadError && <div className="wb-extensions-empty" role="alert"><p>{t("settings.hookLoadError")}</p><button type="button" className="wb-btn" onClick={function () { load() }}>{t("settings.hookReload")}</button></div>}
    <div className="wb-cli-hook-group"><h5>{t("settings.userHooks", "User triggers")}</h5>{loading && <div className="wb-extensions-empty">{t("settings.loading", "Loading…")}</div>}{!loading && !loadError && !visibleHooks.length && <div className="wb-extensions-empty">{normalizedQuery ? t("settings.extensionEmpty", "No matching extensions.") : t(customAvailable ? "settings.hookEmpty" : "settings.hookCustomUnavailable")}</div>}<div className="hook-tile-grid">{visibleHooks.map(function (item) { var key = hookItemKey(item); return <CliHookItem key={key} item={item} t={t} busy={!!busy} manageable={customAvailable} expanded={expanded[key] === true} onExpand={toggleExpanded} onEdit={edit} onToggle={toggle} onTest={test} onDelete={remove} onRetry={retry} /> })}</div></div>
    {proposals.length > 0 && <div className="wb-cli-hook-group"><h5>{t("settings.hookPendingApprovals", "Pending approvals")}</h5>{proposals.map(function (item) { return <CliHookProposal key={item.id} item={item} t={t} busy={!!busy} onDecide={decide} /> })}</div>}
    <SystemHookGroup {...{ standalone, visibleSystemHooks, loading, loadError, systemStatus, load, t, busy, expanded, toggleExpanded, editSystem, normalizedQuery }} />
    {recentResults.length > 0 && <div className="wb-cli-hook-group"><h5>{t("settings.extensionHookTitle", "Automatic integration")}</h5>{recentResults.map(function (item) { return <div key={item.key} className="wb-cli-hook-result"><code>{item.key}</code><span>{String(item.value && (item.value.reason || hookLogLabel(item.value.status, t)) || "")}</span></div> })}</div>}
  </section>
}

function HooksPanel(props) {
  return <div className="settings-panel settings-panel-wide wb-hooks-page" id="setting-hooks"><CliHooksPanel t={props.t} notify={function (message, level) {
    showSettingsToast(message, level || "info")
  }} standalone={true} /></div>
}

export { CliHooksPanel, HooksPanel }

function SystemHookGroup({ standalone, visibleSystemHooks, loading, loadError, systemStatus, load, t, busy, expanded, toggleExpanded, editSystem, normalizedQuery }) {
  return standalone && <div className="wb-cli-hook-group"><h5>{t("settings.systemHooks", "System triggers")}</h5>{loading && <div className="wb-extensions-empty">{t("settings.loading", "Loading…")}</div>}{!loading && !loadError && !visibleSystemHooks.length && <div className="wb-extensions-empty">{systemStatus === "error" ? t("settings.hookSystemLoadError") : systemStatus === "uninitialized" ? t("settings.hookSystemUninitialized") : normalizedQuery ? t("settings.extensionEmpty", "No matching extensions.") : t("settings.hookSystemEmpty", "No persisted system triggers found.")}{(systemStatus === "error" || systemStatus === "uninitialized") && <p><button type="button" className="wb-btn" onClick={function () { load() }}>{t("settings.hookReload")}</button></p>}</div>}<div className="hook-tile-grid">{visibleSystemHooks.map(function (item) { var key = hookItemKey(item); return <CliHookItem key={key} item={item} t={t} busy={!!busy} expanded={expanded[key] === true} onExpand={toggleExpanded} onEdit={editSystem} /> })}</div></div>;
}
