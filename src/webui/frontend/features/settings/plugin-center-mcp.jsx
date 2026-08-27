import {
  workbenchServices,
  useStateSt,
  readSettingsResponse,
  settingsFetch,
} from "./shared.jsx"

function variablesToText(values) {
  if (!values || typeof values !== "object") return ""
  return Object.keys(values).sort().map(function (key) { return key + "=" + String(values[key] == null ? "" : values[key]) }).join("\n")
}

function parseVariables(value, label, t) {
  var variables = {}
  String(value || "").split(/\r?\n/).forEach(function (line) {
    if (!line.trim()) return
    var separator = line.indexOf("=")
    if (separator <= 0 || !line.slice(0, separator).trim()) throw new Error(t("settings.mcpVariablesInvalid", { label: label }, "{label} must use KEY=VALUE, one entry per line."))
    variables[line.slice(0, separator).trim()] = line.slice(separator + 1)
  })
  return variables
}

function editorFromItem(item) {
  var config = item && item.config && typeof item.config === "object" ? item.config : item || {}
  return {
    name: String(item && item.id || config.name || ""),
    transport: String(config.transport || "stdio"),
    command: String(config.command || ""),
    url: String(config.url || ""),
    argsText: Array.isArray(config.args) ? config.args.join("\n") : "",
    variablesText: variablesToText(config.transport === "stdio" ? config.env : config.headers),
    enabled: config.enabled !== false,
  }
}

function payloadFromEditor(editor, t) {
  var payload = { transport: editor.transport, enabled: editor.enabled !== false }
  if (editor.transport === "stdio") {
    payload.command = editor.command.trim()
    payload.args = editor.argsText.split(/\r?\n/).map(function (value) { return value.trim() }).filter(Boolean)
    payload.env = parseVariables(editor.variablesText, t("settings.mcpEnvironment", "Environment"), t)
  } else {
    payload.url = editor.url.trim()
    payload.headers = parseVariables(editor.variablesText, t("settings.mcpHeaders", "HTTP headers"), t)
  }
  return payload
}

function McpConnectionFields(props) {
  var e = props.editor, update = props.update, t = props.t
  if (e.transport === "stdio") return <React.Fragment><label className="wide"><span>{t("settings.mcpCommand", "Executable")}</span><input className="wb-input mono" value={e.command} disabled={props.busy} onChange={function (event) { update({ command: event.target.value }) }} /></label><label className="wide"><span>{t("settings.mcpArguments", "Arguments (one per line)")}</span><textarea className="wb-input mono" rows="4" value={e.argsText} disabled={props.busy} onChange={function (event) { update({ argsText: event.target.value }) }} /></label></React.Fragment>
  return <label className="wide"><span>URL</span><input className="wb-input mono" type="url" value={e.url} disabled={props.busy} onChange={function (event) { update({ url: event.target.value }) }} /></label>
}

function McpConfigurationDialog(props) {
  var t = props.t, [editor, setEditor] = useStateSt(function () { return editorFromItem(props.item) }), [busy, setBusy] = useStateSt(false), [error, setError] = useStateSt("")
  function update(changes) { setEditor({ ...editor, ...changes }) }
  function save(event) {
    event.preventDefault()
    var payload
    try { payload = payloadFromEditor(editor, t) } catch (reason) { setError(reason.message); return }
    setBusy(true); setError("")
    settingsFetch("/api/plugin-center/mcp/" + encodeURIComponent(editor.name) + "/configuration", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then(readSettingsResponse).then(function (response) {
      if (response && response.ok === false) throw new Error(String(response.error || workbenchServices.i18n().t("settings.saveFailed")))
      if (props.onSaved) return props.onSaved(response)
    }).then(props.onClose).catch(function (reason) { setError(reason.message || String(reason)) }).finally(function () { setBusy(false) })
  }
  var variablesLabel = editor.transport === "stdio" ? t("settings.mcpEnvironment", "Environment") : t("settings.mcpHeaders", "HTTP headers")
  return <div className="wb-extension-modal-scrim" onMouseDown={function (event) { if (event.target === event.currentTarget && !busy) props.onClose() }}><section className="wb-extension-modal wb-mcp-configuration-modal" role="dialog" aria-modal="true" aria-labelledby="mcp-config-title"><header><div><h3 id="mcp-config-title">{t("settings.mcpConfiguration", "Provider configuration")}</h3><p>{editor.name}</p></div><button type="button" className="wb-extension-close" disabled={busy} onClick={props.onClose} aria-label={t("settings.close", "Close")}>×</button></header><form onSubmit={save}><div className="wb-mcp-editor-grid"><label><span>{t("settings.mcpTransport", "Transport")}</span><select className="wb-select" value={editor.transport} disabled={busy} onChange={function (event) { update({ transport: event.target.value, variablesText: "" }) }}><option value="stdio">stdio</option><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option></select></label><McpConnectionFields editor={editor} update={update} t={t} busy={busy} /><label className="wide"><span>{variablesLabel + " · KEY=VALUE"}</span><textarea className="wb-input mono" rows="4" value={editor.variablesText} disabled={busy} onChange={function (event) { update({ variablesText: event.target.value }) }} /><small>{t("settings.mcpSecretsHint", "Configured secrets stay redacted and are preserved when saved.")}</small></label></div>{error && <div className="wb-extension-notice error" role="alert">{error}</div>}<footer><button type="button" className="wb-btn" disabled={busy} onClick={props.onClose}>{t("settings.cancel", "Cancel")}</button><button type="submit" className="wb-btn primary" disabled={busy}>{busy ? t("settings.saving", "Saving…") : t("settings.save", "Save")}</button></footer></form></section></div>
}

export { McpConfigurationDialog }
