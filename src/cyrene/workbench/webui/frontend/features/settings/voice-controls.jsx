import {downloadPercent, readSettingsResponse, settingsFetch} from "./shared.jsx"

export function VoiceMethods({t, kind, cloud, available, busy, onChange}) {
  const [hint, setHint] = React.useState(false)
  const root = React.useRef(null)
  const trigger = React.useRef(null)
  const id = React.useId()
  React.useEffect(() => {
    if (!hint) return
    const outside = event => { if (!root.current?.contains(event.target)) setHint(false) }
    const escape = event => { if (event.key === "Escape") { setHint(false); trigger.current?.focus() } }
    document.addEventListener("pointerdown", outside)
    document.addEventListener("keydown", escape)
    return () => {
      document.removeEventListener("pointerdown", outside)
      document.removeEventListener("keydown", escape)
    }
  }, [hint])
  return <div className="voice-methods" ref={root} role="group" aria-label={t("settings.voice."+kind)}>
    {[false,true].map(remote => {
      const locked = remote && !available
      return <button key={String(remote)} type="button" ref={remote ? trigger : undefined}
        disabled={busy} aria-disabled={locked || busy} aria-pressed={remote === cloud}
        aria-expanded={locked ? hint : undefined} aria-controls={locked && hint ? id : undefined}
        onClick={() => { if (locked) setHint(!hint); else {setHint(false); onChange(remote)} }}>
        <strong>{t("settings.voice."+kind+(remote?"Cloud":"Local"))}</strong>
        <small>{t("settings.voice."+(locked?"needsSetup":kind+(remote?"CloudDetail":"LocalDetail")))}</small>
      </button>
    })}
    {hint && !available && <div id={id} className="voice-setup-popover" role="status">
      <button type="button" aria-label={t("settings.close")} onClick={() => {setHint(false);trigger.current?.focus()}}>×</button>
      <strong>{t("settings.voice.setup")}</strong><p>{t("settings.voice."+kind+"Setup")}</p>
    </div>}
  </div>
}

export function VoiceDownload({id, name, size, t, onReady}) {
  const [state, setState] = React.useState(null)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState("")
  const [refresh, setRefresh] = React.useState(0)
  const ready = React.useRef(onReady)
  ready.current = onReady
  React.useEffect(() => {
    let active = true, timer
    async function poll() {
      try {
        const response = await settingsFetch("/api/settings/local-models/status").then(readSettingsResponse)
        if (!active) return
        const model = (response.models || []).find(item => item.id === id)
        setState(model)
        if (model?.ready) ready.current()
        if (model?.downloading) timer = setTimeout(poll, 900)
      } catch (reason) { if (active) setError(reason.message) }
    }
    poll()
    return () => { active = false; clearTimeout(timer) }
  }, [id, refresh])
  async function start() {
    setBusy(true); setError("")
    try {
      await settingsFetch("/api/settings/local-models/"+encodeURIComponent(id)+"/download", {method:"POST"}).then(readSettingsResponse)
      setRefresh(value => value+1)
    } catch (reason) { setError(reason.message) }
    finally { setBusy(false) }
  }
  return <div className="voice-download"><span role="status">{name} · {error || state?.error || t(state?.ready?"settings.voice.runtimeMissing":state?.downloading?"settings.voice.downloading":"settings.localModelNotDownloaded")}</span>
    {state?.downloading ? <progress max="100" value={downloadPercent(state)} aria-label={name}/> :
      !state?.ready && <button type="button" className="wb-btn tonal" disabled={busy} onClick={start}>{t("settings.download")} · {size} MB</button>}
  </div>
}
