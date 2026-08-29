import {
  useStateSt,
  useEffectSt,
  readSettingsResponse,
  settingsFetch,
  Toggle,
} from "./shared.jsx"
import { ExtensionGlyph } from "./plugin-center-catalog.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"

var MANIFEST_TEMPLATE = JSON.stringify({
  manifestApi: "cyrene.agent/v1",
  agentId: "my-agent",
  displayName: "My Agent",
  version: "1.0.0",
  driver: "acp_stdio",
  command: "my-agent",
  protocolVersion: 1,
  description: "Declarative ACP stdio Agent profile.",
  capabilities: {
    session: { load: "unknown" },
    input: { text: "supported", image: "unknown", file: "unknown", audio: "unknown" },
    output: { streaming: "supported", reasoning: "unknown", toolLifecycle: "unknown" },
    interaction: { permission: "agent_defined", steer: "unknown", cancel: "supported" },
    model: { agentManaged: "supported", cyreneManaged: ["openai_chat", "openai_responses"] },
  },
  modelAccess: { mode: "cyrene_managed", profileId: "primary" },
}, null, 2)

function request(path, init) {
  return settingsFetch(path, init).then(readSettingsResponse).then(function (payload) {
    if (payload && payload.ok === false) throw new Error(String(payload.error || payload.detail || workbenchServices.i18n().t("settings.requestFailed")))
    return payload
  })
}

function agentId(agent) {
  return String(agent && (agent.agentId || agent.agent_id || agent.id) || "")
}

function installationId(agent) {
  return String(agent && (agent.installationId || agent.installation_id) || "")
}

function agentName(agent) {
  return String(agent && (agent.displayName || agent.display_name || agent.name || agentId(agent)) || "Agent")
}

function trustLabel(agent, t) {
  var trust = String(agent && (agent.sourceTrust || agent.source_trust) || (agent.recommended ? "cyrene_recommended" : "external_unverified"))
  var key = trust === "cyrene_recommended" ? "recommended" : trust === "external_verified" ? "externalVerified" : "externalUnverified"
  return t("settings.agentSourceTrust." + key, trust.replace(/_/g, " "))
}

function stateLabel(prefix, value, t) {
  if (Array.isArray(value)) return value.map(String).join(", ")
  var camel = String(value || "unknown").replace(/_([a-z])/g, function (_match, letter) { return letter.toUpperCase() })
  var translationKey = ["settings", "agent" + prefix, camel].join(".")
  return t(translationKey, String(value || "unknown").replace(/_/g, " "))
}

function capabilityStateLabel(value, t) {
  if (Array.isArray(value)) return value.map(String).join(", ")
  return stateLabel("CapabilityState", value, t)
}

function capabilityRows(agent) {
  var groups = agent && agent.capabilities && typeof agent.capabilities === "object" ? agent.capabilities : {}
  var rows = []
  Object.keys(groups).forEach(function (group) {
    var values = groups[group]
    if (!values || typeof values !== "object") return
    Object.keys(values).forEach(function (name) { rows.push({ group: group, name: name, state: values[name] }) })
  })
  return rows
}

function AgentCapabilities(props) {
  var rows = capabilityRows(props.agent)
  if (!rows.length) return <div className="wb-agent-capabilities-empty">{props.t("settings.agentCapabilitiesEmpty", "No capabilities reported yet.")}</div>
  var grouped = {}
  rows.forEach(function (row) { (grouped[row.group] || (grouped[row.group] = [])).push(row) })
  return <div className="wb-agent-capabilities">{Object.keys(grouped).map(function (group) {
    return <section key={group} className="wb-agent-capability-group"><h5>{props.t("settings.agentCapabilityGroup." + group, group)}</h5>{grouped[group].map(function (row) { return <div key={row.name}><code>{row.name}</code><span>{capabilityStateLabel(row.state, props.t)}</span></div> })}</section>
  })}</div>
}

function useAgentDetails(agent, expanded, onChanged, notify, t) {
  var id = installationId(agent), [details, setDetails] = useStateSt(agent || {}), [busy, setBusy] = useStateSt("")
  var [probeResult, setProbeResult] = useStateSt(null), [diagnostics, setDiagnostics] = useStateSt(null), [authResult, setAuthResult] = useStateSt(null)
  function load() {
    if (!id) return Promise.resolve(agent)
    return request("/api/agents/" + encodeURIComponent(id)).then(function (payload) {
      var next = payload.agent || payload
      setDetails(next); if (onChanged) onChanged(next); return next
    }).catch(function (error) { if (notify) notify(error.message, "error") })
  }
  useEffectSt(function () { if (expanded && id) load() }, [expanded, id])
  function action(name, method, body) {
    if (!id) return Promise.resolve(null)
    setBusy(name)
    return settingsFetch("/api/agents/" + encodeURIComponent(id) + "/" + name, { method: method || "POST", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined }).then(readSettingsResponse)
      .then(function (payload) {
        if (name === "probe" || name === "restart") setProbeResult(payload || {})
        if (name.indexOf("auth/") === 0) setAuthResult(payload || {})
        if (name === "diagnostics") setDiagnostics(payload || {})
        if (payload && payload.ok === false) {
          if (notify) notify(String(payload.detail || payload.error || "Request failed"), "error")
          if (name === "probe") return load().then(function () { return payload })
          return payload
        }
        if (payload && payload.agent) {
          setDetails(payload.agent)
          if (onChanged) onChanged(payload.agent)
          return payload
        }
        if (name === "diagnostics") return payload
        return load().then(function () { return payload })
      }).catch(function (error) {
        var failure = { ok: false, error: error.message }
        if (name === "probe" || name === "restart") setProbeResult(failure)
        if (name.indexOf("auth/") === 0) setAuthResult(failure)
        if (name === "diagnostics") setDiagnostics(failure)
        if (notify) notify(error.message, "error")
        throw error
      }).finally(function () { setBusy("") })
  }
  function saveModelAccess(mode, profileId) {
    var modelAccess = { mode: mode }
    if (mode === "cyrene_managed") modelAccess.profileId = String(profileId || "primary") || "primary"
    return action("settings", "PATCH", { modelAccess: modelAccess }).then(function (payload) {
      if (notify) notify(t ? t("settings.saved", "Saved") : "Saved", "success")
      return payload
    }).catch(function () {})
  }
  return { details: details, busy: busy, load: load, action: action, saveModelAccess: saveModelAccess,
    probeResult: probeResult, diagnostics: diagnostics, authResult: authResult }
}

function resultNote(result) {
  if (!result || typeof result !== "object") return ""
  return String(result.detail || result.message || result.error || result.instructions || result.url || "")
}

function diagnosticsErrors(payload) {
  var diagnostics = payload && payload.diagnostics && typeof payload.diagnostics === "object" ? payload.diagnostics : payload
  return Array.isArray(diagnostics && (diagnostics.lastErrors || diagnostics.last_errors)) ? (diagnostics.lastErrors || diagnostics.last_errors) : []
}

function diagnosticsNote(payload, t) {
  var diagnostics = payload && payload.diagnostics && typeof payload.diagnostics === "object" ? payload.diagnostics : payload
  var code = String(diagnostics && (diagnostics.noteCode || diagnostics.note_code || diagnostics.reason) || "")
  if (code === "starts_on_demand") return t("settings.agentDiagnosticsStartsOnDemand", "ACP stdio starts on demand. Diagnostics never expose process environment or credentials.")
  return resultNote(diagnostics)
}

function sourceUrl(agent) {
  var source = agent && agent.source && typeof agent.source === "object" ? agent.source : {}
  return String(source.url || source.manifestUrl || source.manifest_url || agent.repository || agent.sourceUrl || agent.source_url || "")
}

function checksum(agent) {
  var checksums = agent && agent.checksums && typeof agent.checksums === "object" ? agent.checksums : {}
  return String(checksums.sha256 || agent.sha256 || "")
}

function runtimeDetails(agent) {
  var runtime = agent && agent.runtime && typeof agent.runtime === "object" ? agent.runtime : {}
  return {
    state: agent.runtimeState || agent.runtime_state || runtime.state || "unknown",
    command: agent.command || runtime.command || "",
    lastStarted: runtime.lastStartedAt || runtime.last_started_at || agent.lastStartedAt || agent.last_started_at || "",
  }
}

function agentUsability(agent, t) {
  var installState = String(agent.installState || agent.install_state || "installed")
  var auth = String(agent.authState || agent.auth_state || "not_configured")
  var runtime = runtimeDetails(agent).state
  if (installState !== "installed") return t("settings.agentUsability.notInstalled", "Unavailable · not installed")
  if (agent.enabled === false) return t("settings.agentUsability.disabled", "Unavailable · disabled")
  if (auth === "failed" || auth === "expired") return t("settings.agentUsability.auth", "Unavailable · login required")
  if (runtime === "crashed" || runtime === "error") return t("settings.agentUsability.runtime", "Unavailable · runtime error")
  return t("settings.agentUsability.available", "Available in Composer")
}

function AgentDetails(props) {
  var agent = props.controller.details || props.agent, t = props.t, id = installationId(agent)
  var model = agent.modelAccess || agent.model_access || {}, runtime = runtimeDetails(agent), auth = agent.authState || agent.auth_state || "not_configured"
  var authNote = resultNote(props.controller.authResult), probeNote = resultNote(props.controller.probeResult)
  var diagnosticNote = diagnosticsNote(props.controller.diagnostics, t), errors = diagnosticsErrors(props.controller.diagnostics)
  var busy = props.controller.busy
  return <div className="wb-agent-details">
    <section className="wb-agent-detail-section"><h4>{t("settings.agentSectionOverview", "Overview")}</h4><dl>
      <div><dt>{t("settings.extensionVersion", "Version")}</dt><dd className="mono">{agent.version || "—"}</dd></div>
      <div><dt>{t("settings.agentPublisher", "Publisher")}</dt><dd>{agent.publisher || "—"}</dd></div>
      <div><dt>{t("settings.agentSourceUrl", "Manifest / repository")}</dt><dd className="mono">{sourceUrl(agent) || "—"}</dd></div>
      <div><dt>{t("settings.agentChecksum", "SHA-256")}</dt><dd className="mono">{checksum(agent) || t("settings.agentUnverified", "Unverified")}</dd></div>
      <div><dt>{t("settings.agentInstallationId", "Installation ID")}</dt><dd className="mono">{id || "—"}</dd></div>
      <div><dt>{t("settings.agentDriverProtocol", "Driver / protocol")}</dt><dd className="mono">{[agent.driver, agent.protocolVersion || agent.protocol_version].filter(Boolean).join(" · ") || "—"}</dd></div>
      <div><dt>{t("settings.agentLoginState", "Login")}</dt><dd>{stateLabel("Auth", auth, t)}</dd></div>
      <div><dt>{t("settings.agentComposerAvailability", "Composer availability")}</dt><dd>{agentUsability(agent, t)}</dd></div>
    </dl></section>
    <section className="wb-agent-detail-section"><h4>{t("settings.agentSectionAuthModel", "Login & model")}</h4>
      <div className="wb-agent-model-access">
        <label><input type="radio" name={'model-access-' + id} checked={String(model.mode || "cyrene_managed") !== "agent_managed"} disabled={!!busy} onChange={function () { props.controller.saveModelAccess("cyrene_managed", model.profileId || model.profile_id || "primary") }} /><span><strong>{t("settings.agentModelCyrene", "Use Cyrene models")}</strong><small>{t("settings.agentModelCyreneHint", "Routes through the Cyrene Model Gateway; no long-lived key is exposed to the Agent.")}</small></span></label>
        <label><input type="radio" name={'model-access-' + id} checked={String(model.mode || "") === "agent_managed"} disabled={!!busy} onChange={function () { props.controller.saveModelAccess("agent_managed") }} /><span><strong>{t("settings.agentModelOwn", "Use the Agent's own configuration")}</strong><small>{t("settings.agentModelOwnHint", "The Agent keeps its own OAuth, API key or environment configuration.")}</small></span></label>
      </div>
      <div className="wb-agent-action-row"><button type="button" className="wb-btn" disabled={!!busy} onClick={function () { props.controller.action("auth/start").catch(function () {}) }}>{t("settings.agentLoginStart", "Start login")}</button><button type="button" className="wb-btn" disabled={!!busy} onClick={function () { props.controller.action("auth/logout").catch(function () {}) }}>{t("settings.agentLoginLogout", "Log out")}</button></div>
      {authNote ? <div className="wb-agent-feedback" role="status">{authNote}</div> : <p className="wb-hint">{t("settings.agentAuthHint", "Login uses the methods advertised by the Agent. Some Agents require login in their own terminal instead.")}</p>}
    </section>
    <section className="wb-agent-detail-section"><h4>{t("settings.agentSectionCapabilities", "Capabilities")}</h4><AgentCapabilities agent={agent} t={t} /></section>
    <section className="wb-agent-detail-section"><h4>{t("settings.agentSectionRuntime", "Runtime")}</h4><dl>
      <div><dt>{t("settings.placeholderCommand", "Command")}</dt><dd className="mono">{runtime.command || "—"}</dd></div>
      <div><dt>{t("settings.agentRuntimeState", "Process state")}</dt><dd>{stateLabel("Runtime", runtime.state, t)}</dd></div>
      <div><dt>{t("settings.agentLastStarted", "Last started")}</dt><dd className="mono">{runtime.lastStarted ? workbenchServices.i18n().formatDate(runtime.lastStarted, { dateStyle: "medium", timeStyle: "short" }) : "—"}</dd></div>
    </dl><div className="wb-agent-action-row"><button type="button" className="wb-btn" disabled={!!busy} onClick={function () { props.controller.action("restart").catch(function () {}) }}>{t("settings.agentRestart", "Restart Agent")}</button><button type="button" className="wb-btn" disabled={!!busy} onClick={function () { props.controller.action("probe").catch(function () {}) }}>{t("settings.agentProbe", "Test connection")}</button></div>{probeNote && <div className="wb-agent-feedback" role="status">{probeNote}</div>}</section>
    <section className="wb-agent-detail-section wb-agent-diagnostics"><h4>{t("settings.agentSectionDiagnostics", "Diagnostics")}</h4><div className="wb-agent-action-row"><button type="button" className="wb-btn" disabled={!!busy} onClick={function () { props.controller.action("diagnostics", "GET").catch(function () {}) }}>{t("settings.agentLoadDiagnostics", "Load diagnostics")}</button></div>{diagnosticNote && <p className="wb-hint">{diagnosticNote}</p>}{errors.length > 0 && <ul className="wb-agent-errors">{errors.map(function (error, index) { return <li key={index} className="mono">{String(error)}</li> })}</ul>}</section>
  </div>
}

function AgentCard(props) {
  var agent = props.agent || {}, t = props.t, id = installationId(agent), installed = !!id || String(agent.installState || agent.install_state) === "installed"
  var upgrade = String(agent.installState || agent.install_state) === "upgrade_available"
  var expanded = props.expandedId === (id || agentId(agent)), details = useAgentDetails(agent, expanded, props.onChanged, props.notify, t)
  return <article className={'wb-agent-card' + (expanded ? ' expanded' : '')}><div className="wb-extension-card-main"><button type="button" className="wb-extension-card-summary" onClick={function () { props.onToggleExpand(id || agentId(agent)) }} aria-expanded={expanded ? "true" : "false"}><span className="wb-extension-glyph agent"><ExtensionGlyph kind="agent" label={agentName(agent)} /></span><span className="wb-extension-copy"><span className="wb-extension-title-row"><strong>{agentName(agent)}</strong><span className="wb-agent-trust">{trustLabel(agent, t)}</span></span><span className="wb-extension-description">{agent.description || ""}</span><span className="wb-extension-meta"><span className="wb-agent-state">{stateLabel("InstallState", agent.installState || agent.install_state || (installed ? "installed" : "available"), t)}</span><span>{agent.version || ""}</span><span>{stateLabel("Runtime", agent.runtimeState || agent.runtime_state, t)}</span></span></span></button><div className="wb-extension-actions">{(!installed || upgrade) && <button type="button" className="wb-btn primary" disabled={props.busy} onClick={function () { props.onInstall(agent) }}>{t(upgrade ? "settings.agentUpgrade" : "settings.install", upgrade ? "Upgrade" : "Install")}</button>}{installed && agent.enabled !== undefined && Toggle(agent.enabled !== false, function () { props.onToggle(agent) }, props.busy, agentName(agent))}{installed && <button type="button" className="wb-btn danger" disabled={props.busy} onClick={function () { props.onRemove(agent) }}>{t("settings.uninstall", "Uninstall")}</button>}</div></div>{expanded && installed && <AgentDetails agent={agent} controller={details} t={t} />}</article>
}

function AgentProposalDialog(props) {
  var t = props.t, [value, setValue] = useStateSt(MANIFEST_TEMPLATE), [result, setResult] = useStateSt(null), [busy, setBusy] = useStateSt(""), [error, setError] = useStateSt("")
  function createProposal() {
    var parsed
    try { parsed = JSON.parse(value) } catch (_error) { setError(t("settings.agentProposalInvalidJson", "Enter valid JSON.")); return }
    var payload = parsed.manifestApi ? { source: { type: "inline", manifest: parsed }, requestedVersion: parsed.version || "" } : parsed
    setBusy("create"); setError("")
    request("/api/plugin-center/agent/install-proposals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then(function (response) {
      if (response && response.requiresConfirmation === false) {
        if (props.onStarted) props.onStarted(response)
        props.onClose()
        return
      }
      setResult(response)
    }).catch(function (reason) { setError(reason.message) }).finally(function () { setBusy("") })
  }
  function confirmProposal() {
    var id = String(result && (result.proposalId || result.proposal_id) || "")
    if (!id) return
    setBusy("confirm")
    request("/api/plugin-center/agent/install-proposals/" + encodeURIComponent(id) + "/confirm", { method: "POST" }).then(function (payload) { if (props.onStarted) props.onStarted(payload); props.onClose() }).catch(function (reason) { setError(reason.message) }).finally(function () { setBusy("") })
  }
  return <div className="wb-extension-modal-scrim" onMouseDown={function (event) { if (event.target === event.currentTarget && !busy) props.onClose() }}><section className="wb-extension-modal wb-agent-proposal-modal" role="dialog" aria-modal="true" aria-labelledby="agent-proposal-title"><header><div><h3 id="agent-proposal-title">{t("settings.agentProposalTitle", "Install another Agent")}</h3><p>{t("settings.agentProposalSubmitHint", "Paste a cyrene.agent/v1 manifest. Cyrene validates it before installation.")}</p></div><button type="button" className="wb-extension-close" onClick={props.onClose} aria-label={t("settings.close", "Close")}>×</button></header><div className="wb-agent-proposal-body"><textarea className="wb-input mono" rows="18" value={value} disabled={!!busy} onChange={function (event) { setValue(event.target.value) }} />{error && <div className="wb-extension-notice error" role="alert">{error}</div>}{result && <div className="wb-agent-proposal-result"><strong>{t("settings.agentProposalReady", "Proposal ready")}</strong><pre>{JSON.stringify(result, null, 2)}</pre></div>}</div><footer><button type="button" className="wb-btn" disabled={!!busy} onClick={props.onClose}>{t("settings.cancel", "Cancel")}</button>{result ? <button type="button" className="wb-btn primary" disabled={!!busy} onClick={confirmProposal}>{t("settings.agentProposalConfirmInstall", "Confirm and install")}</button> : <button type="button" className="wb-btn primary" disabled={!!busy} onClick={createProposal}>{t("settings.agentProposalCreate", "Validate and create proposal")}</button>}</footer></section></div>
}

function AgentTab(props) {
  var t = props.t, recommended = Array.isArray(props.listing && props.listing.recommended) ? props.listing.recommended : [], installed = Array.isArray(props.listing && props.listing.installed) ? props.listing.installed : []
  return <div className="wb-agent-tab"><section className="wb-agent-section"><div className="wb-agent-section-head"><h4>{t("settings.agentRecommended", "Recommended")}</h4><span>{recommended.length}</span></div><div className="wb-agent-recommended-list">{recommended.map(function (agent) { return <AgentCard key={agentId(agent)} {...props} agent={agent} /> })}</div></section><section className="wb-agent-section"><div className="wb-agent-section-head"><h4>{t("settings.agentInstalled", "Installed")}</h4><span>{installed.length}</span></div>{installed.length ? <div className="wb-agent-installed-list">{installed.map(function (agent) { return <AgentCard key={installationId(agent)} {...props} agent={agent} /> })}</div> : <div className="wb-extensions-empty">{t("settings.agentInstalledEmpty", "No Agents installed yet.")}</div>}</section><div className="wb-agent-install-other"><button type="button" className="wb-btn primary" onClick={props.onOpenProposal}>+ {t("settings.agentInstallOther", "Install another Agent")}</button><p>{t("settings.agentInstallOtherHint", "External Agents are marked by source trust and require confirmation.")}</p></div></div>
}

export { AgentProposalDialog, AgentTab, agentId, installationId }
