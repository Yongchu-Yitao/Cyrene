import { workbenchServices } from "./shared/runtime/services.jsx"
// Required first-run configuration for the model and assistant personality.
// Registered under the legacy "welcome" service name for bootstrap compatibility.
(function () {
  var useState = React.useState;

  function T(key, params, fallback) {
    return workbenchServices.i18n().t(key, params, fallback);
  }

  // Shared SVG wrapper — matches the stroke style used across the workbench.
  function Svg(props) {
    return React.createElement(
      "svg",
      {
        viewBox: "0 0 24 24",
        width: props.size || 20,
        height: props.size || 20,
        fill: props.fill || "none",
        stroke: props.fill ? "none" : "currentColor",
        strokeWidth: props.sw || 1.7,
        strokeLinecap: "round",
        strokeLinejoin: "round",
        "aria-hidden": "true",
      },
      props.children
    );
  }

  var ICON = {
    check: <Svg size={14} sw={2.4}><path d="m5 12.5 4.2 4.2L19 7" /></Svg>,
  };
  // ── first-run onboarding: LLM + personality ──────────────────────────
  // The workbench's own setup flow. Talks ONLY to the shared backend
  // (/api/onboarding/*) and refreshes the Workbench platform data store.
  function OnboardingFlow(props) {
    workbenchServices.i18n().use();
    var ob = props.onboarding || {};
    var llm = ob.llm || {};
    var persona = ob.personality || {};
    var llmDone = !!llm.configured;
    var personaDone = !!persona.configured;
    // Active step mirrors the backend's: configure the model first, then the
    // personality. Both done => the parent shell drops the onboarding takeover.
    var step = !llmDone ? "llm" : (!personaDone ? "personality" : "done");

    var endpointOptions = Array.isArray(llm.endpointOptions) ? llm.endpointOptions : [];
    var preferredEndpoint = endpointOptions.find(function (item) { return item.providerId === llm.provider; })
      || endpointOptions[0] || {};
    var [apiKey, setApiKey] = useState("");
    var [baseUrl, setBaseUrl] = useState(llm.baseUrl || preferredEndpoint.url || "");
    var [providerId, setProviderId] = useState(llm.provider || preferredEndpoint.providerId || "");
    var [model, setModel] = useState(llm.model || preferredEndpoint.defaultModel || "");
    var [mode, setMode] = useState(persona.mode || "name");
    var [pName, setPName] = useState(persona.label || "");
    var [soul, setSoul] = useState(persona.currentContent || "");
    var [busy, setBusy] = useState(false);
    var [error, setError] = useState("");
    var [notice, setNotice] = useState("");

    function applyResponse(payload) {
      if (payload && payload.onboarding) {
        try { workbenchServices.data().state.onboarding = payload.onboarding; } catch (e) {}
        workbenchServices.data().bump();
      }
      try { return workbenchServices.data().reload(); } catch (e) {}
      return Promise.resolve();
    }

    function post(url, body) {
      return fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (p) {
          if (!r.ok) throw new Error(p.error || p.detail || ("HTTP " + r.status));
          return p;
        });
      });
    }

    function saveLlm() {
      setBusy(true); setError(""); setNotice("");
      post("/api/onboarding/llm", { api_key: apiKey, base_url: baseUrl, model: model, provider_id: providerId })
        .then(function (p) {
          setNotice(T("welcome.setup.llm.verified", null, "Connection verified") + (p.preview ? "：" + p.preview : ""));
          return applyResponse(p);
        })
        .catch(function (e) { setError(e.message || String(e)); })
        .finally(function () { setBusy(false); });
    }

    function savePersonality() {
      setBusy(true); setError(""); setNotice("");
      post("/api/onboarding/personality", { mode: mode, name: pName, content: soul })
        .then(function (p) {
          setNotice(T("welcome.setup.personality.applied", null, "Personality applied"));
          if (p && p.onboarding && !p.onboarding.needsOnboarding && props.onComplete) {
            props.onComplete();
          }
          return applyResponse(p);
        })
        .catch(function (e) { setError(e.message || String(e)); })
        .finally(function () { setBusy(false); });
    }

    var stepCards = [
      { id: "llm", label: T("welcome.setup.llm.step", null, "Model"), done: llmDone },
      { id: "personality", label: T("welcome.setup.personality.step", null, "Personality"), done: personaDone },
    ];

    return (
      <section className="wb-wel-page wb-ob-page" data-cyrene-node-id="onboarding">
        <div className="wb-wel-inner wb-ob-inner">
          <header className="wb-wel-head wb-ob-head">
            <div className="wb-ob-eyebrow">
              <span className="wb-ob-eyebrow-dot" />
              {ob.isAbsoluteFreshStart ? T("welcome.setup.fresh", null, "Fresh install detected") : T("welcome.setup.incomplete", null, "Setup incomplete")}
            </div>
            <h1>{T("welcome.setup.title", null, "Set up Cyrene")}</h1>
            <p>{T("welcome.setup.subtitle", null, "Connect a model and pick a personality. You can change both later in settings.")}</p>
          </header>

          <div className="wb-ob-steps" aria-label={T("welcome.setup.progress", null, "Setup progress")}>
            {stepCards.map(function (sc, i) {
              var state = sc.done ? "done" : (sc.id === step ? "current" : "idle");
              return (
                <div key={sc.id} className={"wb-ob-step " + state}>
                  <span className="wb-ob-step-index">{sc.done ? ICON.check : (i + 1)}</span>
                  <span className="wb-ob-step-text">
                    <b>{sc.label}</b>
                    <small>{sc.done ? T("welcome.setup.configured", null, "Configured") : T("welcome.setup.required", null, "Required")}</small>
                  </span>
                </div>
              );
            })}
          </div>

          <div className="wb-ob-panel">
            {step === "llm" ? (
              <div className="wb-ob-section">
                <h2>{T("welcome.setup.llm.title", null, "Connect your model")}</h2>
                <p className="wb-ob-sub">{T("welcome.setup.llm.subtitle", null, "Choose a service or enter a custom OpenAI-compatible endpoint, then provide its credentials and model identifier.")}</p>
                <div className="wb-ob-form-grid">
                    <label className="wb-ob-field">
                      <span className="wb-ob-label">{T("welcome.setup.llm.endpoint", null, "Endpoint")}<small>{T("welcome.setup.llm.endpointHint", null, "Choose a preset or enter a custom OpenAI-compatible endpoint")}</small></span>
                      <input className="wb-ob-input mono" data-cyrene-node-id="onboarding_base_url" list="onboarding_endpoint_options" value={baseUrl} placeholder="https://api.example.com/v1" autoComplete="url" onChange={function (e) {
                        var nextUrl = e.target.value;
                        var normalizedUrl = String(nextUrl || "").trim().replace(/\/+$/, "");
                        var selected = endpointOptions.find(function (item) {
                          return String(item.url || "").trim().replace(/\/+$/, "") === normalizedUrl;
                        }) || {};
                        setBaseUrl(nextUrl);
                        setProviderId(selected.providerId || "");
                        if (selected.providerId) setModel(selected.defaultModel || "");
                        setError("");
                        setNotice("");
                      }} />
                      <datalist id="onboarding_endpoint_options">
                        {endpointOptions.map(function (item) {
                          return <option key={item.providerId} value={item.url} label={item.name} />;
                        })}
                      </datalist>
                    </label>
                    <label className="wb-ob-field">
                      <span className="wb-ob-label">{T("welcome.setup.llm.apiKey", null, "API key")}<small>{T("welcome.setup.llm.apiKeyHint", null, "Stored locally, never uploaded")}</small></span>
                      <input className="wb-ob-input" type="password" value={apiKey} placeholder="sk-..." onChange={function (e) { setApiKey(e.target.value); }} />
                    </label>
                    <label className="wb-ob-field">
                      <span className="wb-ob-label">{T("welcome.setup.llm.model", null, "Model")}<small>{T("welcome.setup.llm.modelHint", null, "Model identifier")}</small></span>
                      <input className="wb-ob-input mono" data-cyrene-node-id="onboarding_model" value={model} placeholder="gpt-4o" onChange={function (e) { setModel(e.target.value); }} />
                    </label>
                </div>
                <div className="wb-ob-actions">
                  <button type="button" className="wb-btn primary" disabled={busy || !model.trim() || !baseUrl.trim()} onClick={saveLlm}>
                    {busy ? T("welcome.setup.llm.testing", null, "Testing...") : T("welcome.setup.llm.save", null, "Save & test")}
                  </button>
                </div>
              </div>
            ) : (
              <div className="wb-ob-section">
                <h2>{T("welcome.setup.personality.title", null, "Choose a personality")}</h2>
                <p className="wb-ob-sub">{T("welcome.setup.personality.subtitle", null, "Shapes how the assistant talks and behaves. This becomes SOUL.md.")}</p>
                <div className="wb-ob-seg">
                  {[
                    { id: "name", label: T("welcome.setup.personality.byName", null, "By name") },
                    { id: "custom", label: T("welcome.setup.personality.custom", null, "Custom") },
                    { id: "default", label: T("welcome.setup.personality.default", null, "Default") },
                  ].map(function (opt) {
                    return (
                      <button key={opt.id} type="button" aria-pressed={mode === opt.id} className={"wb-ob-seg-btn" + (mode === opt.id ? " active" : "")} onClick={function () { setMode(opt.id); }}>{opt.label}</button>
                    );
                  })}
                </div>
                {mode === "name" ? (
                  <label className="wb-ob-field">
                    <span className="wb-ob-label">{T("welcome.setup.personality.nameLabel", null, "Persona name")}<small>{T("welcome.setup.personality.nameHint", null, "A real or fictional figure; the model drafts a SOUL.md from it")}</small></span>
                    <input className="wb-ob-input" value={pName} placeholder="Lelouch Lamperouge / Steve Jobs / Sherlock Holmes" onChange={function (e) { setPName(e.target.value); }} />
                  </label>
                ) : mode === "custom" ? (
                  <label className="wb-ob-field wb-ob-field-block">
                    <span className="wb-ob-label">{T("welcome.setup.personality.contentLabel", null, "SOUL.md content")}<small>{T("welcome.setup.personality.contentHint", null, "Write the persona instructions directly")}</small></span>
                    <textarea className="wb-ob-input mono wb-ob-textarea" value={soul} onChange={function (e) { setSoul(e.target.value); }} />
                  </label>
                ) : (
                  <div className="wb-ob-note">{T("welcome.setup.personality.defaultDesc", null, "Use Cyrene's balanced default persona. You can customize it anytime later.")}</div>
                )}
                <div className="wb-ob-actions">
                  <button type="button" className="wb-btn primary" disabled={busy || (mode === "name" && !pName.trim())} onClick={savePersonality}>
                    {busy ? T("welcome.setup.personality.applying", null, "Applying...") : T("welcome.setup.personality.apply", null, "Apply & finish")}
                  </button>
                </div>
              </div>
            )}

            {(notice || error) ? (
              <div className={"wb-ob-feedback " + (error ? "err" : "ok")}>{error || notice}</div>
            ) : null}
          </div>
        </div>
      </section>
    );
  }

  window.CyreneUI.welcome = window.CyreneUI.register("welcome", {
    Page: OnboardingFlow,
  });
})();
