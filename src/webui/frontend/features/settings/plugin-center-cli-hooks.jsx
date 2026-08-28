import { workbenchServices, useStateSt, useEffectSt, readSettingsResponse, settingsFetch } from "./shared.jsx"

var EVENTS = ["PreToolUse", "PostToolUse", "SessionStart", "TurnStart", "SessionEnd", "Stop"]

function request(path, init) {
  return settingsFetch(path, init).then(readSettingsResponse).then(function (payload) {
    if (payload && payload.ok === false) throw new Error(String(payload.error || payload.detail || workbenchServices.i18n().t("settings.requestFailed")))
    return payload
  })
}

function emptyDraft() {
  return { name: "", event: "PostToolUse", matcher: "*", executable: "", args: "", timeout: "10", failure: "open", description: "" }
}

function CliHookProposal(props) {
  var item = props.item || {}, hook = item.hook || {}, extension = item.extension || {}
  return <article className="wb-cli-hook-proposal">
    <div><strong>{extension.name || extension.id || hook.name}</strong><small>{item.rationale || hook.description}</small>
      <code>{hook.event}{hook.matcher && hook.matcher !== "*" ? " · " + hook.matcher : ""}</code></div>
    <div className="wb-cli-hook-row-actions">
      <button type="button" className="wb-btn" disabled={props.busy} onClick={function () { props.onDecide(item, false) }}>{props.t("settings.reject", "Reject")}</button>
      <button type="button" className="wb-btn primary" disabled={props.busy} onClick={function () { props.onDecide(item, true) }}>{props.t("settings.approve", "Approve")}</button>
    </div>
  </article>
}

function CliHookItem(props) {
  var item = props.item || {}, runner = item.runner || {}
  return <article className={'wb-cli-hook-item' + (item.enabled === true ? '' : ' disabled')}>
    <div className="wb-cli-hook-item-copy"><div><strong>{item.name || item.id}</strong><span>{item.event}</span></div>
      {item.description && <small>{item.description}</small>}
      <code>{runner.executable || runner.path || "—"}{Array.isArray(runner.args) && runner.args.length ? " " + runner.args.join(" ") : ""}</code>
    </div>
    <div className="wb-cli-hook-row-actions">
      <button type="button" className="wb-btn" disabled={props.busy} onClick={function () { props.onTest(item) }}>{props.t("settings.test", "Test")}</button>
      <button type="button" className="wb-btn" disabled={props.busy} onClick={function () { props.onToggle(item) }}>{item.enabled === true ? props.t("settings.pluginCenterDisable", "Disable") : props.t("settings.pluginCenterEnable", "Enable")}</button>
      <button type="button" className="wb-btn danger" disabled={props.busy} onClick={function () { props.onDelete(item) }}>{props.t("settings.pluginCenterRemove", "Remove")}</button>
    </div>
  </article>
}

function CliHookEditor(props) {
  var draft = props.draft
  function update(name, value) { props.setDraft(Object.assign({}, draft, { [name]: value })) }
  return <form className="wb-cli-hook-editor" onSubmit={props.onSave}>
    <div className="wb-cli-hook-editor-grid">
      <label><span>{props.t("settings.name", "Name")}</span><input className="wb-input" required value={draft.name} disabled={props.busy} onChange={function (event) { update("name", event.target.value) }} /></label>
      <label><span>{props.t("settings.hookEvent", "Event")}</span><select className="wb-select" value={draft.event} disabled={props.busy} onChange={function (event) { update("event", event.target.value) }}>{EVENTS.map(function (event) { return <option key={event} value={event}>{event}</option> })}</select></label>
      {(draft.event === "PreToolUse" || draft.event === "PostToolUse") && <label><span>{props.t("settings.hookMatcher", "Tool name or glob")}</span><input className="wb-input mono" value={draft.matcher} disabled={props.busy} onChange={function (event) { update("matcher", event.target.value) }} /></label>}
      <label><span>{props.t("settings.hookExecutable", "Executable")}</span><input className="wb-input mono" required value={draft.executable} disabled={props.busy} onChange={function (event) { update("executable", event.target.value) }} /></label>
      <label><span>{props.t("settings.hookTimeout", "Timeout (seconds)")}</span><input className="wb-input" type="number" min="0.1" max="60" step="0.1" value={draft.timeout} disabled={props.busy} onChange={function (event) { update("timeout", event.target.value) }} /></label>
      {draft.event === "PreToolUse" && <label><span>{props.t("settings.hookFailurePolicy", "On failure")}</span><select className="wb-select" value={draft.failure} disabled={props.busy} onChange={function (event) { update("failure", event.target.value) }}><option value="open">{props.t("settings.hookFailureOpen", "Allow and log")}</option><option value="block">{props.t("settings.hookFailureBlock", "Block tool call")}</option></select></label>}
      <label className="wide"><span>{props.t("settings.hookArguments", "Arguments")}</span><textarea className="wb-input mono" rows="3" value={draft.args} disabled={props.busy} placeholder={props.t("settings.hookArgumentsHint", "One argument per line.")} onChange={function (event) { update("args", event.target.value) }} /></label>
      <label className="wide"><span>{props.t("settings.description", "Description")}</span><textarea className="wb-input" rows="2" value={draft.description} disabled={props.busy} onChange={function (event) { update("description", event.target.value) }} /></label>
    </div>
    <div className="wb-cli-hook-editor-actions"><button type="button" className="wb-btn" disabled={props.busy} onClick={props.onCancel}>{props.t("common.cancel", "Cancel")}</button><button type="submit" className="wb-btn primary" disabled={props.busy}>{props.t("settings.save", "Save")}</button></div>
  </form>
}

function CliHooksPanel(props) {
  var t = props.t
  var [hooks, setHooks] = useStateSt([]), [proposals, setProposals] = useStateSt([])
  var [results, setResults] = useStateSt({}), [audit, setAudit] = useStateSt([])
  var [loading, setLoading] = useStateSt(true), [busy, setBusy] = useStateSt("")
  var [editing, setEditing] = useStateSt(false), [draft, setDraft] = useStateSt(emptyDraft)

  function tell(message, level) { if (props.notify) props.notify(message, level || "info") }
  function load() {
    setLoading(true)
    return Promise.all([
      request("/api/plugin-center/cli/hooks"),
      request("/api/plugin-center/cli/hooks/audit?limit=30"),
    ]).then(function (values) {
      setHooks(Array.isArray(values[0].hooks) ? values[0].hooks : [])
      setProposals(Array.isArray(values[0].proposals) ? values[0].proposals.filter(function (item) { return item.status === "pending" }) : [])
      setResults(values[0].configuration_results && typeof values[0].configuration_results === "object" ? values[0].configuration_results : {})
      setAudit(Array.isArray(values[1].records) ? values[1].records : [])
    }).catch(function (error) { tell(error.message || String(error), "error") }).finally(function () { setLoading(false) })
  }
  useEffectSt(function () { load() }, [])

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
  function save(event) {
    event.preventDefault()
    var payload = {
      name: draft.name.trim(), event: draft.event, matcher: draft.matcher.trim() || "*", enabled: true,
      description: draft.description.trim(), timeout_seconds: Number(draft.timeout || 10),
      failure_policy: draft.event === "PreToolUse" ? draft.failure : "open",
      runner: { type: "command", executable: draft.executable.trim(), args: draft.args.split(/\r?\n/).map(function (value) { return value.trim() }).filter(Boolean), env: {} },
    }
    setBusy("save")
    request("/api/plugin-center/cli/hooks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then(function () {
      setEditing(false); setDraft(emptyDraft()); tell(t("settings.hookSaved"), "success"); return load()
    }).catch(function (error) { tell(error.message || String(error), "error") }).finally(function () { setBusy("") })
  }

  var recentResults = Object.keys(results).map(function (key) { return { key: key, value: results[key] } }).slice(-4).reverse()
  return <section className="wb-cli-hooks-panel">
    <header><div><h4>{t("settings.agentHooks", "CLI Hooks")}</h4><p>{t("settings.agentHooksSubtitle", "Run approved CLI commands through tree-local Hooks.")}</p></div><button type="button" className="wb-btn" disabled={!!busy} onClick={function () { setEditing(!editing); setDraft(emptyDraft()) }}>{editing ? t("common.cancel", "Cancel") : t("settings.addHook", "Add Hook")}</button></header>
    {editing && <CliHookEditor t={t} draft={draft} setDraft={setDraft} busy={!!busy} onSave={save} onCancel={function () { setEditing(false) }} />}
    {proposals.length > 0 && <div className="wb-cli-hook-group"><h5>{t("settings.hookPendingApprovals", "Pending approvals")}</h5>{proposals.map(function (item) { return <CliHookProposal key={item.id} item={item} t={t} busy={!!busy} onDecide={decide} /> })}</div>}
    <div className="wb-cli-hook-group"><h5>{t("settings.configuredHooks", "Configured Hooks")}</h5>{loading && <div className="wb-extensions-empty">{t("settings.loading", "Loading…")}</div>}{!loading && !hooks.length && <div className="wb-extensions-empty">{t("settings.hookEmpty", "No Hooks configured.")}</div>}{hooks.map(function (item) { return <CliHookItem key={item.id} item={item} t={t} busy={!!busy} onToggle={toggle} onTest={test} onDelete={remove} /> })}</div>
    {recentResults.length > 0 && <div className="wb-cli-hook-group"><h5>{t("settings.extensionHookTitle", "Automatic integration")}</h5>{recentResults.map(function (item) { return <div key={item.key} className="wb-cli-hook-result"><code>{item.key}</code><span>{String(item.value && (item.value.reason || item.value.status) || "")}</span></div> })}</div>}
    {audit.length > 0 && <details className="wb-cli-hook-audit"><summary>{t("settings.hookExecutionLog", "Execution and audit log")}</summary>{audit.slice(0, 10).map(function (item, index) { return <div key={String(item.timestamp || index)}><code>{item.event || item.action || item.kind}</code><span>{item.status || item.result || item.hook_id}</span></div> })}</details>}
  </section>
}

export { CliHooksPanel }
