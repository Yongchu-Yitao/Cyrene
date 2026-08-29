import {
  useStateSt,
  useEffectSt,
  readSettingsResponse,
  settingsFetch,
  Toggle,
} from "./shared.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"

function request(path, init) {
  return settingsFetch(path, init).then(readSettingsResponse).then(function (payload) {
    if (payload && payload.ok === false) throw new Error(String(payload.error || payload.detail || workbenchServices.i18n().t("settings.requestFailed")))
    return payload
  })
}

function SourceField(props) {
  return <label className={props.wide ? "wide" : ""}><span>{props.label}</span><input className="wb-input mono" type={props.type || "url"} value={props.value || ""} placeholder={props.placeholder || ""} disabled={props.disabled} onChange={function (event) { props.onChange(event.target.value) }} />{props.hint && <small>{props.hint}</small>}</label>
}

function SourceNetworkSection(props) {
  var s = props.sources, set = props.setSources, t = props.t
  return <section className="wb-extension-source-section"><div className="wb-extension-source-section-head"><div><h4>{t("settings.extensionSourceSectionNetwork", "Network & downloads")}</h4><p>{t("settings.extensionSourceSectionNetworkHint", "Choose routing and package mirrors.")}</p></div></div><div className="wb-extension-source-form"><label className="wide"><span>{t("settings.extensionNetworkMode", "Network mode")}</span><select className="wb-select" value={s.network_mode || "auto"} onChange={function (event) { set({ ...s, network_mode: event.target.value }) }}><option value="auto">{t("settings.extensionNetworkAuto", "Automatic")}</option><option value="direct">{t("settings.extensionNetworkDirect", "Direct")}</option><option value="china">{t("settings.extensionNetworkChina", "China mirrors")}</option></select></label><SourceField label={t("settings.extensionGithubMirror", "GitHub download mirror")} value={s.github_mirror} placeholder={t("settings.extensionGithubMirrorPlaceholder", "Optional mirror")} onChange={function (value) { set({ ...s, github_mirror: value }) }} /><SourceField label={t("settings.extensionNpmRegistry", "npm registry")} value={s.npm_registry} placeholder={t("settings.extensionNpmRegistryPlaceholder", "Blank uses npm default")} onChange={function (value) { set({ ...s, npm_registry: value }) }} /><SourceField label={t("settings.extensionPipIndex", "pip index URL")} value={s.pip_index_url} placeholder={t("settings.extensionPipIndexPlaceholder", "Blank uses PyPI")} onChange={function (value) { set({ ...s, pip_index_url: value }) }} /><div className="wb-extension-source-toggle wide"><div><strong>{t("settings.extensionAutoMirror", "Automatically use a mirror after direct download fails")}</strong><small>{t("settings.extensionAutoMirrorHint", "Only falls back after a direct GitHub download fails.")}</small></div>{Toggle(s.auto_mirror !== false, function () { set({ ...s, auto_mirror: s.auto_mirror === false }) }, false, t("settings.extensionAutoMirror"))}</div></div></section>
}

function SourceCatalogSection(props) {
  var s = props.sources, set = props.setSources, t = props.t
  return <section className="wb-extension-source-section"><div className="wb-extension-source-section-head"><div><h4>{t("settings.extensionSourceSectionCatalogs", "Plugin catalogs")}</h4><p>{t("settings.extensionSourceSectionCatalogsHint", "Configure where Cyrene searches for MCP servers and Skills.")}</p></div></div><div className="wb-extension-source-form"><SourceField label={t("settings.extensionMcpRegistry", "MCP Registry URL")} value={s.mcp_registry_url} placeholder="https://registry.modelcontextprotocol.io" hint={t("settings.extensionMcpRegistryHint", "Search catalog for MCP servers.")} onChange={function (value) { set({ ...s, mcp_registry_url: value }) }} /><SourceField label={t("settings.extensionSkillCatalog", "Skill catalog URL")} value={s.skill_catalog_url} placeholder={t("settings.extensionSkillCatalogPlaceholder", "Optional HTTPS catalog URL")} hint={t("settings.extensionSkillCatalogHint", "Leave blank to search GitHub directly.")} onChange={function (value) { set({ ...s, skill_catalog_url: value }) }} /></div></section>
}

function SourceSecuritySection(props) {
  var s = props.sources, set = props.setSources, t = props.t
  return <section className="wb-extension-source-section"><div className="wb-extension-source-section-head"><div><h4>{t("settings.extensionSourceSectionSecurity", "Credentials & integrity")}</h4><p>{t("settings.extensionSourceSectionSecurityHint", "Credentials stay in encrypted local configuration.")}</p></div></div><div className="wb-extension-source-form"><label className="wide"><span>{t("settings.extensionGithubToken", "GitHub token")}</span><div className="wb-extension-token-row"><input className="wb-input mono" type="password" value={s.github_token || ""} placeholder={s.github_token_configured ? t("settings.secretConfigured", "Configured") : "ghp_…"} onChange={function (event) { set({ ...s, github_token: event.target.value, clear_github_token: false }) }} />{s.github_token_configured && <button type="button" className="wb-btn danger" onClick={function () { set({ ...s, github_token: "", clear_github_token: true, github_token_configured: false }) }}>{t("settings.clearStoredKey", "Clear")}</button>}</div><small>{t("settings.extensionGithubTokenHint", "Optional; improves GitHub API rate limits.")}</small></label><div className="wb-extension-source-toggle wide"><div><strong>{t("settings.extensionVerifySignatures", "Verify checksums and signatures")}</strong><small>{t("settings.extensionVerifySignaturesHint", "Reject downloads that fail integrity checks.")}</small></div>{Toggle(s.verify_signatures !== false, function () { set({ ...s, verify_signatures: s.verify_signatures === false }) }, false, t("settings.extensionVerifySignatures"))}</div></div></section>
}

function SourceHealth(props) {
  if (!props.payload) return null
  return <div className="wb-extension-source-health">{Object.keys(props.payload.checks || {}).map(function (key) {
    var item = props.payload.checks[key]
    return <span key={key} className={item.ok ? "ok" : "error"}>{props.t("settings.extensionSourceCheck." + key, key) + " · " + props.t(item.ok ? "settings.extensionReachable" : "settings.extensionUnreachable", item.ok ? "reachable" : "unreachable")}</span>
  })}</div>
}

function SourceAudit(props) {
  function enumLabel(prefix, value) {
    var raw = String(value || "")
    if (!raw) return ""
    var suffix = raw.replace(/[._-]+([a-zA-Z0-9])/g, function (_match, letter) { return letter.toUpperCase() })
    return props.t(prefix + suffix, raw.replace(/[._-]+/g, " "))
  }
  return <details className="wb-extension-audit"><summary>{props.t("settings.extensionAudit", "Audit log")}</summary>{!props.records.length ? <p>{props.t("settings.extensionAuditEmpty", "No Plugin Center changes recorded yet.")}</p> : props.records.map(function (record, index) { return <div key={String(record.at || index)}><strong>{enumLabel("settings.extensionAuditAction.", record.action) + " · " + String(record.target || "—")}</strong><small>{[record.at ? workbenchServices.i18n().formatDate(record.at, { dateStyle: "medium", timeStyle: "short" }) : "", enumLabel("settings.extensionAuditActor.", record.actor), enumLabel("settings.extensionAuditResult.", record.result)].filter(Boolean).join(" · ")}</small></div> })}</details>
}

function SourcesDialog(props) {
  var t = props.t, [sources, setSources] = useStateSt({}), [audit, setAudit] = useStateSt([]), [health, setHealth] = useStateSt(null), [busy, setBusy] = useStateSt("load"), [error, setError] = useStateSt("")
  useEffectSt(function () {
    Promise.all([request("/api/plugin-center/sources"), request("/api/plugin-center/audit?limit=50")]).then(function (payloads) { setSources(payloads[0]); setAudit(payloads[1].records || []) }).catch(function (reason) { setError(reason.message) }).finally(function () { setBusy("") })
  }, [])
  function save() {
    setBusy("save"); setError("")
    request("/api/plugin-center/sources", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sources) }).then(function (payload) { setSources(payload); if (props.notify) props.notify(t("settings.saved", "Saved"), "success") }).catch(function (reason) { setError(reason.message) }).finally(function () { setBusy("") })
  }
  function test() {
    setBusy("test"); setHealth(null); setError("")
    request("/api/plugin-center/sources/test", { method: "POST" }).then(setHealth).catch(function (reason) { setError(reason.message) }).finally(function () { setBusy("") })
  }
  return <div className="wb-extension-modal-scrim" onMouseDown={function (event) { if (event.target === event.currentTarget && !busy) props.onClose() }}><section className="wb-extension-modal wb-extension-source-modal" role="dialog" aria-modal="true" aria-labelledby="plugin-source-title"><header><div><h3 id="plugin-source-title">{t("settings.extensionSources", "Sources")}</h3><p>{t("settings.extensionSourcesSubtitle", "Configure download mirrors, registries, credentials, and integrity checks.")}</p></div><button type="button" className="wb-extension-close" onClick={props.onClose} aria-label={t("settings.close", "Close")}>×</button></header>{busy === "load" ? <div className="wb-extensions-empty">{t("settings.loading", "Loading…")}</div> : <div className="wb-extension-source-sections"><SourceNetworkSection sources={sources} setSources={setSources} t={t} /><SourceCatalogSection sources={sources} setSources={setSources} t={t} /><SourceSecuritySection sources={sources} setSources={setSources} t={t} /></div>}{error && <div className="wb-extension-notice error" role="alert">{error}</div>}<SourceHealth payload={health} t={t} /><SourceAudit records={audit} t={t} /><footer><button type="button" className="wb-btn" disabled={!!busy} onClick={test}>{busy === "test" ? t("settings.testingConnection", "Testing…") : t("settings.testConnection", "Test connection")}</button><button type="button" className="wb-btn primary" disabled={!!busy} onClick={save}>{busy === "save" ? t("settings.saving", "Saving…") : t("settings.save", "Save")}</button></footer></section></div>
}

function BindDialog(props) {
  var initialPath = props.item && (props.item.selectedPath || (props.item.manual_binding ? props.item.path : ""))
  var [path, setPath] = useStateSt(String(initialPath || ""))
  return <div className="wb-extension-modal-scrim" onMouseDown={function (event) { if (event.target === event.currentTarget) props.onClose() }}><section className="wb-extension-modal wb-extension-bind-modal" role="dialog" aria-modal="true" aria-labelledby="plugin-bind-title"><header><div><h3 id="plugin-bind-title">{props.t("settings.extensionBindTitle", "Use an existing local program")}</h3><p>{props.t("settings.extensionBindHint", "Cyrene records the executable path and never modifies the program.")}</p></div><button type="button" className="wb-extension-close" onClick={props.onClose} aria-label={props.t("settings.close", "Close")}>×</button></header><input className="wb-input mono" autoFocus value={path} onChange={function (event) { setPath(event.target.value) }} placeholder={'/usr/local/bin/' + String(props.item && props.item.id || "tool")} /><footer><button type="button" className="wb-btn" onClick={props.onClose}>{props.t("settings.cancel", "Cancel")}</button><button type="button" className="wb-btn primary" disabled={!path.trim() || props.busy} onClick={function () { props.onSave(path.trim()) }}>{props.t("settings.save", "Save")}</button></footer></section></div>
}

export { BindDialog, SourcesDialog }
