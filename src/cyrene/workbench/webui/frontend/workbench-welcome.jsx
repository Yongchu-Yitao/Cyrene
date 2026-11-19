import { workbenchServices } from "./shared/runtime/services.jsx"
import { ActionIcon } from "./features/settings/action-icon.jsx"
import { wbcFriendlyModelName } from "./features/chat/capabilities.jsx"
import { readSettingsResponse, settingsFetch } from "./features/settings/shared.jsx"
import { SetupDisclosure, SetupPreferences, savedSetupTimezone } from "./features/settings/onboarding-preferences.jsx"

const stages = ["welcome", "prefs", "llm", "personality"];
const stageLabels = ["welcome.setup.meet", "welcome.setup.prefs", "welcome.setup.llm.title", "welcome.setup.personality.title"];
const checkpoint = "cyrene-first-run-step";

function initialStep(ob) {
  if (ob.llm?.configured) return "personality";
  const saved = localStorage.getItem(checkpoint);
  return ["welcome", "prefs", "llm"].includes(saved) ? saved : "language";
}

function Logo() {
  return <img className="ob-logo" src="/static/app/logo-mark.png" alt=""/>;
}

function Welcome({ t }) {
  return <>
    <div className="ob-greeting"><Logo/><h1>{t("welcome.setup.hello")}</h1><p>{t("welcome.setup.intro")}</p></div>
    <ul className="ob-examples">{[["research","search"],["slides","devices"],["weekly","reload"]].map(([key, icon]) =>
      <li key={key}><span className={"ob-example-icon " + key}><ActionIcon name={icon}/></span><div>
        <b>{t("welcome.setup." + key)}</b><p>{t("welcome.setup." + key + "Hint")}</p></div></li>)}</ul>
  </>;
}

export default function OnboardingFlow(props) {
  const { t, lang, setLang } = workbenchServices.i18n().use();
  const ob = props.onboarding, llm = ob.llm || {}, persona = ob.personality || {};
  const options = llm.endpointOptions || [];
  const preset = options.find(item => item.providerId === llm.provider) || options.find(item => item.providerId === "deepseek") || options[0];
  const [step, setStep] = React.useState(() => initialStep(ob));
  const [language, chooseLanguage] = React.useState(null);
  const chosen = language || lang;
  const languageText = key => workbenchServices.i18n().tForLang(key, chosen);
  const [providerId, setProviderId] = React.useState(llm.configured ? llm.provider : preset?.providerId || "");
  const [baseUrl, setBaseUrl] = React.useState(llm.baseUrl || preset?.url || "");
  const [model, setModel] = React.useState(llm.model || preset?.defaultModel || "");
  const [manualModel, setManualModel] = React.useState(false);
  const [apiKey, setApiKey] = React.useState("");
  const [editedModel, setEditedModel] = React.useState(false);
  const [advanced, setAdvanced] = React.useState(!preset);
  const [mode, setMode] = React.useState(persona.mode || "default");
  const [name, setName] = React.useState(persona.label || "");
  const [soul, setSoul] = React.useState(persona.isDefaultSoul ? "" : persona.currentContent || "");
  const [desktop, setDesktop] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const locked = React.useRef(false);
  const panel = React.useRef(null);
  const index = stages.indexOf(step);
  const omitPersona = persona.available === false;
  const verified = llm.configured && !editedModel;
  const completed = React.useRef(null);

  React.useEffect(() => {
    panel.current?.focus({ preventScroll: true });
  }, [step]);

  function go(next) {
    setError("");
    setStep(next);
    if (next === "done" || next === "language") localStorage.removeItem(checkpoint);
    else localStorage.setItem(checkpoint, next);
  }

  async function run(action) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    try { await action(); }
    catch (reason) { setError(reason.message || String(reason)); }
    finally { locked.current = false; setBusy(false); }
  }

  async function submit(url, body) {
    const p = await settingsFetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(readSettingsResponse);
    if (!p.onboarding) throw new Error(t("settings.invalidJsonResponse"));
    if (p.onboarding && !p.onboarding.needsOnboarding && props.onComplete) {
      completed.current = p.onboarding;
      go("done");
    } else {
      go(p.onboarding.activeStep);
    }
    const data = workbenchServices.data();
    data.state.onboarding = p.onboarding;
    data.bump();
    data.reload().catch(() => {});
  }

  function chooseProvider(value) {
    const provider = options.find(item => item.providerId === value);
    setProviderId(value);
    setBaseUrl(provider?.url || "");
    setModel(provider?.defaultModel || "");
    setManualModel(false);
    setApiKey("");
    setAdvanced(!provider);
    setEditedModel(true);
  }

  function next() {
    if (step === "language") return run(async () => { await setLang(chosen, { requireSave: true }); go("welcome"); });
    if (step === "welcome") return go("prefs");
    if (step === "prefs") return go("llm");
    if (step === "llm") {
      if (verified && !omitPersona) return go("personality");
      return run(async () => {
        await submit("/api/onboarding/llm", { api_key: apiKey, base_url: baseUrl, model, provider_id: providerId });
        setEditedModel(false);
        setApiKey("");
      });
    }
    if (step === "personality" && verified) return run(() => submit("/api/onboarding/personality", { mode, name, content: soul }));
    if (step === "done") props.onComplete();
  }

  const labels = { language: "welcome.setup.continue", welcome: "welcome.setup.start", prefs: "welcome.setup.continue",
    llm: busy ? "welcome.setup.llm.testing" : verified ? "welcome.setup.continue" : "welcome.setup.llm.save",
    personality: busy ? "welcome.setup.personality.applying" : mode === "name" ? "welcome.setup.generate" : "welcome.setup.personality.apply",
    done: "welcome.setup.enter" };
  const blocked = step === "llm" && (!model.trim() || !baseUrl.trim()) ||
    step === "personality" && (!verified || mode === "name" && !name.trim() || mode === "custom" && !soul.trim());
  const provider = options.find(item => item.providerId === providerId);
  const modelOptions = [...new Set([provider?.defaultModel,
    ...(provider?.providerId === "deepseek" ? ["deepseek-v4-flash", "deepseek-v4-pro"] : []),
  ].filter(Boolean))];
  const showModelInput = manualModel || !modelOptions.includes(model);
  const savedModel = completed.current?.llm?.model || model;
  const visibleStages = omitPersona ? stages.slice(0, 3) : stages;
  return <main className="wb-ob-page" ref={panel} tabIndex={-1} data-cyrene-node-id="onboarding" data-step={step}>
    {step !== "language" && <ol className="ob-progress" aria-label={t("welcome.setup.progress")}>
      {visibleStages.map((key,i) => <li key={key} aria-current={key === step ? "step" : undefined} data-complete={step === "done" || i < index}>{t(stageLabels[i])}</li>)}
    </ol>}
    <div className="ob-content">
      <div className="ob-scroll" key={step}>
      <fieldset className="ob-fields" disabled={busy} aria-label={step === "language" ? languageText("welcome.setup.language") : t("welcome.setup.progress")}>
        {step === "language" && <div className="ob-language"><Logo/><h1>{languageText("welcome.setup.language")}</h1>
          <div className="ob-language-choices">{[["zh","中文"],["en","English"]].map(([id,label]) =>
            <label key={id}><span lang={id}>{label}</span><input type="radio" name="setup-language" checked={chosen === id} onChange={() => chooseLanguage(id)}/></label>)}</div>
        </div>}
        {step === "welcome" && <Welcome t={t}/>}
        {step === "prefs" && <SetupPreferences t={t} appearance={props.appearance} run={run} onDesktopChange={setDesktop}/>}
        {step === "llm" && <>
          <h1>{t("welcome.setup.llm.title")}</h1><p className="ob-lead">{t("welcome.setup.llm.subtitle")}</p>
          <label className="ob-field">{t("welcome.setup.provider")}<select value={provider?.providerId || ""} onChange={e => chooseProvider(e.target.value)}>
            {options.map(item => <option key={item.providerId} value={item.providerId}>{item.name}</option>)}
            <option value="">{t("welcome.setup.llm.customSource")}</option></select></label>
          {modelOptions.length > 0 && <label className="ob-field">{t("welcome.setup.llm.model")}
            <select value={showModelInput ? "" : model} onChange={e => {
              setManualModel(!e.target.value);
              setModel(e.target.value);
              setEditedModel(true);
            }}>
              {modelOptions.map(id => <option key={id} value={id}>{wbcFriendlyModelName({ model: id })}</option>)}
              <option value="">{t("welcome.setup.llm.customModel")}</option>
            </select></label>}
          {showModelInput && <label className="ob-field">{t("settings.modelId")}<input data-cyrene-node-id="onboarding_model" value={model} spellCheck={false}
            onChange={e => { setManualModel(true); setModel(e.target.value); setEditedModel(true); }}/></label>}
          <label className="ob-field">{t("welcome.setup.llm.apiKey")}<input type="password" autoComplete="off" spellCheck={false} value={apiKey}
            onChange={e => { setApiKey(e.target.value); setEditedModel(true); }}/></label>
          <SetupDisclosure id="onboarding-connection" open={advanced} onToggle={() => setAdvanced(!advanced)} label={t("welcome.setup.connection")}>
            <label className="ob-field">{t("welcome.setup.llm.endpoint")}
              <input data-cyrene-node-id="onboarding_base_url" list="onboarding_endpoint_options" value={baseUrl} type="url" onChange={e => {
                const value = e.target.value;
                const selected = options.find(item => item.url.replace(/\/+$/, "") === value.trim().replace(/\/+$/, "")) || {};
                setProviderId(selected.providerId || "");
                if (selected.providerId && selected.providerId !== providerId) {
                  setModel(selected.defaultModel || "");
                  setManualModel(false);
                }
                setBaseUrl(value);
                setEditedModel(true);
              }}/>
              <datalist id="onboarding_endpoint_options">{options.map(item => <option key={item.providerId} value={item.url} label={item.name}/>)}</datalist>
            </label>
          </SetupDisclosure>
        </>}
        {step === "personality" && <>
          <h1>{t("welcome.setup.personality.title")}</h1><p className="ob-lead">{t("welcome.setup.personality.subtitle")}</p>
          {[["default","default","defaultDesc"],["name","byName","nameHint"],["custom","custom","contentHint"]].map(([id,label,hint]) =>
            <div key={id}><label className="ob-persona"><input type="radio" name="setup-persona" checked={mode === id} onChange={() => setMode(id)}/>
              <span><b>{t("welcome.setup.personality." + label)}</b><small>{t("welcome.setup.personality." + hint)}</small></span></label>
              {mode === id && id === "name" && <label className="ob-field ob-inset">{t("welcome.setup.personality.nameLabel")}<input value={name} onChange={e => setName(e.target.value)}/></label>}
              {mode === id && id === "custom" && <label className="ob-field ob-inset">{t("welcome.setup.personality.contentLabel")}<textarea rows={4} value={soul} onChange={e => setSoul(e.target.value)}/></label>}
            </div>)}
        </>}
        {step === "done" && <><h1>{t("welcome.setup.ready")}</h1><p className="ob-lead">{t("welcome.setup.finished")}</p>
          <dl className="ob-summary">{[
            [t("settings.language"), lang === "zh" ? "中文" : "English"],
            [t("settings.theme"), t("settings." + props.appearance.theme)],
            [t("settings.textSize"), t("settings." + props.appearance.textSize)],
            [t("settings.timezone"), savedSetupTimezone()],
            [t("welcome.setup.llm.model"), modelOptions.includes(savedModel) ? wbcFriendlyModelName({ model: savedModel }) : savedModel],
            ...(!omitPersona ? [[t("welcome.setup.personality.title"), completed.current?.personality?.label || t("welcome.setup.personality.default")]] : []),
            [t("welcome.setup.desktop"), [localStorage.getItem("cyrene-desktop-notifications") === "1" && t("settings.desktopNotifications"),
              desktop?.runInBackground && t("settings.runInBackground"), desktop?.quickChatEnabled && t("settings.quickChatAssistant")].filter(Boolean).join(lang === "zh" ? "、" : ", ")],
          ].filter(([,value]) => value).map(([key,value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl></>}
      </fieldset>
      </div>
      {error && <p className="ob-error" role="alert">{error}</p>}
      <footer className="ob-actions">
        {index >= 0 && <button type="button" className="wb-btn" disabled={busy} onClick={() => go(stages[index-1] || "language")}>{t("common.back")}</button>}
        <button type="button" className="wb-btn primary" disabled={busy || blocked} onClick={next}>{step === "language" ? languageText(labels[step]) : t(labels[step])}</button>
      </footer>
    </div>
  </main>;
}
