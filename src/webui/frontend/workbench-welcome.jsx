// Workbench welcome / get-started page.
//
// A full-width onboarding surface that keeps the ProjectRail (like the
// knowledge / schedule / memory module pages) and replaces the rest of the
// grid. Triggered from the "开始使用" rail entry (fullPage === "welcome").
//
// Every actionable element wires into an existing real flow — there are no
// dead buttons:
//   • create / import / template cards → open the New Project modal
//   • capability rows                  → navigate to the matching module page
//   • preference rows                  → toggle theme / language inline, or
//                                        open the relevant Settings tab
//   • import cards                     → knowledge ingestion / new project /
//                                        settings, and the guide opens the docs
//
// Registers the Workbench welcome page with the UI service registry.
(function () {
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useRef = React.useRef;

  function T(key, params, fallback) {
    return window.CyreneUI.require("i18n").t(key, params, fallback);
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
    chevron: <Svg size={16} sw={2}><path d="m9 6 6 6-6 6" /></Svg>,
    arrow: <Svg size={16} sw={2}><path d="M5 12h13M13 6l6 6-6 6" /></Svg>,
    // capabilities
    agent: <Svg><path d="M21 11.5a8.5 8.5 0 0 1-12.2 7.6L3 21l1.9-5.8A8.5 8.5 0 1 1 21 11.5Z" /></Svg>,
    knowledge: <Svg><path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H20v15H7.5A2.5 2.5 0 0 0 5 19.5Z" /><path d="M5 19.5A2.5 2.5 0 0 0 7.5 22H20" /></Svg>,
    collab: <Svg><circle cx="9" cy="8.5" r="3" /><path d="M3.5 19a5.5 5.5 0 0 1 11 0M16 6.2a3 3 0 0 1 0 5.6M20.5 19a5.5 5.5 0 0 0-3.5-5.1" /></Svg>,
    automation: <Svg><path d="M14.7 6.3a4 4 0 1 0-5 5l-7 7 2 2 7-7a4 4 0 0 0 5-5l-2.6 2.6-2.4-.6-.6-2.4Z" /></Svg>,
    // preferences
    theme: <Svg><circle cx="12" cy="12" r="9" /><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none" /></Svg>,
    globe: <Svg><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.6 2.5 2.6 15 0 18M12 3c-2.6 2.5-2.6 15 0 18" /></Svg>,
    clock: <Svg><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.5 2" /></Svg>,
    keyboard: <Svg><rect x="2.5" y="6" width="19" height="12" rx="2.5" /><path d="M7 10h.01M11 10h.01M15 10h.01M8 14h8" /></Svg>,
    // templates
    blank: <Svg><rect x="4" y="3.5" width="16" height="17" rx="2.5" /><path d="M12 9v6M9 12h6" /></Svg>,
    doc: <Svg><path d="M6 3.5h7l5 5V20a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 20Z" /><path d="M13 3.5V8a1 1 0 0 0 1 1h4M9 13h6M9 16.5h4" /></Svg>,
    code: <Svg><path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 6l-3 12" /></Svg>,
    research: <Svg><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></Svg>,
    // imports
    importDoc: <Svg><path d="M6 3.5h7l5 5V20a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 20Z" /><path d="M13 3.5V8a1 1 0 0 0 1 1h4" /><path d="M12 11v6m0 0 2.5-2.5M12 17l-2.5-2.5" /></Svg>,
    importTask: <Svg><rect x="4" y="4" width="16" height="16" rx="2.5" /><path d="m8 12 2.5 2.5L16 9" /></Svg>,
    importKb: <Svg><path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H20v15H7.5A2.5 2.5 0 0 0 5 19.5Z" /><path d="M5 19.5A2.5 2.5 0 0 0 7.5 22H20" /></Svg>,
    importMore: <Svg fill="currentColor"><circle cx="6" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="18" cy="12" r="1.7" /></Svg>,
  };

  // Resolve a friendly "(UTC±HH:MM) <IANA tz>" label from the browser, so the
  // preference row reflects the real machine timezone rather than a constant.
  function timezoneLabel() {
    try {
      var tz = (Intl.DateTimeFormat().resolvedOptions().timeZone) || "";
      var off = -new Date().getTimezoneOffset();
      var sign = off >= 0 ? "+" : "-";
      var abs = Math.abs(off);
      var pad = function (n) { return (n < 10 ? "0" : "") + n; };
      return "(UTC" + sign + pad(Math.floor(abs / 60)) + ":" + pad(abs % 60) + ")" + (tz ? " " + tz : "");
    } catch (e) {
      return "UTC";
    }
  }

  function themeValue(theme, actualTheme) {
    if (theme === "system") return T("workbench.theme.system", null, "Follow system");
    return actualTheme === "dark" ? T("workbench.theme.dark", null, "Dark mode") : T("workbench.theme.light", null, "Light mode");
  }

  // ── three-dot onboarding stepper ─────────────────────────────────────
  function Steps(props) {
    // Step 1 completes once the workspace has at least one real project.
    var steps = [
      { id: "setup", label: T("welcome.step.setup", null, "Initial setup") },
      { id: "explore", label: T("welcome.step.explore", null, "Explore features") },
      { id: "start", label: T("welcome.step.start", null, "Get started") },
    ];
    var activeIndex = props.hasProjects ? 1 : 0;
    return (
      <div className="wb-wel-steps">
        {steps.map(function (step, i) {
          var state = i < activeIndex ? "done" : i === activeIndex ? "current" : "idle";
          return (
            <React.Fragment key={step.id}>
              <div className={"wb-wel-step " + state}>
                <span className="wb-wel-step-dot">{state === "done" ? ICON.check : (i + 1)}</span>
                <span className="wb-wel-step-label">{step.label}</span>
              </div>
              {i < steps.length - 1 ? <span className={"wb-wel-step-line" + (i < activeIndex ? " done" : "")} /> : null}
            </React.Fragment>
          );
        })}
      </div>
    );
  }

  // ── card 1: create your first project ────────────────────────────────
  function CreateCard(props) {
    var points = [
      T("welcome.create.point.tasks", null, "Manage tasks and chats"),
      T("welcome.create.point.knowledge", null, "Build a knowledge base"),
      T("welcome.create.point.collab", null, "Collaborate as a team"),
      T("welcome.create.point.track", null, "Track progress"),
    ];
    return (
      <section className="wb-wel-card wb-wel-card-create">
        <div className="wb-wel-create-main">
          <h2>{T("welcome.create.title", null, "Create your first project")}</h2>
          <p className="wb-wel-create-desc">{T("welcome.create.desc", null, "Projects are the container for all your work in Cyrene. Once created, you can:")}</p>
          <ul className="wb-wel-checklist">
            {points.map(function (text, i) {
              return (
                <li key={i}>
                  <span className="wb-wel-check">{ICON.check}</span>
                  <span>{text}</span>
                </li>
              );
            })}
          </ul>
          <div className="wb-wel-create-actions">
            <button type="button" className="wb-btn primary" onClick={props.onNewProject}>
              <Svg size={16} sw={2.4}><path d="M12 5v14M5 12h14" /></Svg>
              <span>{T("welcome.create.cta", null, "Create project")}</span>
            </button>
            <button type="button" className="wb-wel-link-btn" onClick={props.onImportProject}>
              <Svg size={16}><path d="M12 15V4m0 0 4 4m-4-4-4 4" /><path d="M5 16.5V18a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-1.5" /></Svg>
              <span>{T("welcome.create.import", null, "Import existing")}</span>
            </button>
          </div>
        </div>
      </section>
    );
  }

  // ── card 2: learn what Cyrene can do ─────────────────────────────────
  function CapabilitiesCard(props) {
    var rows = [
      { id: "agent", tone: "blue", icon: ICON.agent, label: T("welcome.cap.agent", null, "Agent chat & task execution"), action: function () { props.onOpenPage("chat"); } },
      { id: "knowledge", tone: "violet", icon: ICON.knowledge, label: T("welcome.cap.knowledge", null, "Knowledge base & context"), action: function () { props.onOpenPage("knowledge"); } },
      { id: "collab", tone: "amber", icon: ICON.collab, label: T("welcome.cap.collab", null, "Projects & collaboration"), action: function () { props.onOpenPage("task"); } },
      { id: "automation", tone: "green", icon: ICON.automation, label: T("welcome.cap.automation", null, "Automation & integrations"), action: function () { props.onOpenPage("schedule"); } },
    ];
    return (
      <section className="wb-wel-card">
        <h2>{T("welcome.cap.title", null, "Learn what Cyrene can do")}</h2>
        <p className="wb-wel-card-sub">{T("welcome.cap.subtitle", null, "Take a quick tour of how Cyrene boosts your productivity")}</p>
        <div className="wb-wel-rows">
          {rows.map(function (row) {
            return (
              <button key={row.id} type="button" className="wb-wel-row" onClick={row.action}>
                <span className={"wb-wel-row-ico " + row.tone}>{row.icon}</span>
                <span className="wb-wel-row-label">{row.label}</span>
                <span className="wb-wel-row-chevron">{ICON.chevron}</span>
              </button>
            );
          })}
        </div>
      </section>
    );
  }

  // ── card 3: choose your preferences ──────────────────────────────────
  function PreferencesCard(props) {
    var rows = [
      { id: "theme", icon: ICON.theme, label: T("welcome.pref.theme", null, "Appearance"), value: themeValue(props.theme, props.actualTheme), action: props.onToggleTheme },
      { id: "language", icon: ICON.globe, label: T("welcome.pref.language", null, "Language"), value: props.lang === "zh" ? "简体中文" : "English", action: function () { props.setLang(props.lang === "zh" ? "en" : "zh"); } },
      { id: "timezone", icon: ICON.clock, label: T("welcome.pref.timezone", null, "Timezone"), value: timezoneLabel(), action: function () { props.onSettings("general"); } },
      { id: "shortcuts", icon: ICON.keyboard, label: T("welcome.pref.shortcuts", null, "Shortcuts"), value: T("welcome.pref.shortcutsValue", null, "View / customize"), action: function () { props.onSettings("general"); } },
    ];
    return (
      <section className="wb-wel-card">
        <h2>{T("welcome.pref.title", null, "Choose your preferences")}</h2>
        <p className="wb-wel-card-sub">{T("welcome.pref.subtitle", null, "Customize Cyrene to fit how you work")}</p>
        <div className="wb-wel-prefs">
          {rows.map(function (row) {
            return (
              <button
                key={row.id}
                type="button"
                className={"wb-wel-pref" + (row.id === "timezone" ? " is-long-value" : "")}
                onClick={row.action}
              >
                <span className="wb-wel-pref-ico">{row.icon}</span>
                <span className="wb-wel-pref-label">{row.label}</span>
                <span className="wb-wel-pref-value" title={row.value}>{row.value}</span>
                <span className="wb-wel-pref-caret">{ICON.chevron}</span>
              </button>
            );
          })}
        </div>
      </section>
    );
  }

  // ── bottom-left: start from a template ───────────────────────────────
  function TemplatesPanel(props) {
    var tiles = [
      { id: "blank", tone: "slate", icon: ICON.blank, title: T("welcome.templates.blank", null, "Blank project"), desc: T("welcome.templates.blankDesc", null, "Start from scratch") },
      { id: "doc", tone: "blue", icon: ICON.doc, title: T("welcome.templates.doc", null, "Document project"), desc: T("welcome.templates.docDesc", null, "Docs & collaboration") },
      { id: "dev", tone: "green", icon: ICON.code, title: T("welcome.templates.dev", null, "Dev project"), desc: T("welcome.templates.devDesc", null, "Software workflow") },
      { id: "research", tone: "amber", icon: ICON.research, title: T("welcome.templates.research", null, "Research project"), desc: T("welcome.templates.researchDesc", null, "Research & knowledge") },
    ];
    return (
      <section className="wb-wel-panel">
        <div className="wb-wel-panel-head">
          <h3>{T("welcome.templates.title", null, "Start from a template")}</h3>
          <p>{T("welcome.templates.subtitle", null, "Pick a template to kick-start your project")}</p>
        </div>
        <div className="wb-wel-tiles">
          {tiles.map(function (tile) {
            return (
              <button key={tile.id} type="button" className="wb-wel-tile" onClick={function () { props.onNewProject(tile.id); }}>
                <span className={"wb-wel-tile-ico " + tile.tone}>{tile.icon}</span>
                <b>{tile.title}</b>
                <small>{tile.desc}</small>
              </button>
            );
          })}
        </div>
        <button type="button" className="wb-wel-more" onClick={function () { props.onNewProject(); }}>
          <span>{T("welcome.templates.more", null, "More templates")}</span>
          {ICON.arrow}
        </button>
      </section>
    );
  }

  // ── bottom-right: import data ────────────────────────────────────────
  function ImportPanel(props) {
    var tiles = [
      { id: "docs", tone: "blue", icon: ICON.importDoc, title: T("welcome.import.docs", null, "Import documents"), desc: T("welcome.import.docsDesc", null, "PDF, MD, TXT supported"), action: function () { props.onOpenPage("knowledge"); } },
      { id: "tasks", tone: "green", icon: ICON.importTask, title: T("welcome.import.tasks", null, "Import tasks"), desc: T("welcome.import.tasksDesc", null, "From other tools"), action: function () { props.onNewProject(); } },
      { id: "knowledge", tone: "violet", icon: ICON.importKb, title: T("welcome.import.knowledge", null, "Import knowledge"), desc: T("welcome.import.knowledgeDesc", null, "Existing knowledge base"), action: function () { props.onOpenPage("knowledge"); } },
      { id: "other", tone: "slate", icon: ICON.importMore, title: T("welcome.import.other", null, "Other ways"), desc: T("welcome.import.otherDesc", null, "More options"), action: function () { props.onSettings(); } },
    ];
    return (
      <section className="wb-wel-panel">
        <div className="wb-wel-panel-head">
          <h3>{T("welcome.import.title", null, "Import data")}</h3>
          <p>{T("welcome.import.subtitle", null, "Migrate your existing data into Cyrene")}</p>
        </div>
        <div className="wb-wel-tiles">
          {tiles.map(function (tile) {
            return (
              <button key={tile.id} type="button" className="wb-wel-tile" onClick={tile.action}>
                <span className={"wb-wel-tile-ico " + tile.tone}>{tile.icon}</span>
                <b>{tile.title}</b>
                <small>{tile.desc}</small>
              </button>
            );
          })}
        </div>
        <a className="wb-wel-more" href={props.guideUrl} target="_blank" rel="noopener noreferrer">
          <span>{T("welcome.import.guide", null, "Import guide")}</span>
          {ICON.arrow}
        </a>
      </section>
    );
  }

  // ── first-run onboarding: LLM + personality ──────────────────────────
  // The workbench's own setup flow. Talks ONLY to the shared backend
  // (/api/onboarding/*) and refreshes the Workbench platform data store.
  function OnboardingFlow(props) {
    window.CyreneUI.require("i18n").use();
    var ob = props.onboarding || {};
    var llm = ob.llm || {};
    var persona = ob.personality || {};
    var llmDone = !!llm.configured;
    var personaDone = !!persona.configured;
    // Active step mirrors the backend's: configure the model first, then the
    // personality. Both done => the parent shell drops the onboarding takeover.
    var step = !llmDone ? "llm" : (!personaDone ? "personality" : "done");

    var [apiKey, setApiKey] = useState("");
    var [baseUrl, setBaseUrl] = useState(llm.baseUrl || "");
    var [model, setModel] = useState(llm.model || "");
    var [llmSource, setLlmSource] = useState(llm.provider === "codex_oauth" ? "codex" : "custom");
    var [codexState, setCodexState] = useState({
      available: true,
      connected: false,
      checking: true,
      models: [],
    });
    var [codexModel, setCodexModel] = useState(llm.provider === "codex_oauth" ? (llm.model || "") : "");
    var [codexEffort, setCodexEffort] = useState(llm.reasoningEffort || "");
    var [codexBusy, setCodexBusy] = useState("");
    var codexPoll = useRef(null);
    var [mode, setMode] = useState(persona.mode || "name");
    var [pName, setPName] = useState(persona.label || "");
    var [soul, setSoul] = useState(persona.currentContent || "");
    var [busy, setBusy] = useState(false);
    var [error, setError] = useState("");
    var [notice, setNotice] = useState("");

    function applyResponse(payload) {
      if (payload && payload.onboarding) {
        try { window.CyreneUI.require("data").state.onboarding = payload.onboarding; } catch (e) {}
        window.CyreneUI.require("data").bump();
      }
      try { return window.CyreneUI.require("data").reload(); } catch (e) {}
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

    function codexModelId(item) {
      return String(item && (item.model || item.id) || "");
    }

    function loadCodexState() {
      return fetch("/api/settings/openai-oauth")
        .then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (p) {
            if (!r.ok) throw new Error(p.error || p.detail || ("HTTP " + r.status));
            return p;
          });
        })
        .then(function (data) {
          var options = data.models || [];
          setCodexState({ ...data, checking: false });
          setCodexModel(function (current) {
            var validCurrent = options.some(function (item) { return codexModelId(item) === current; });
            var preferred = options.find(function (item) { return item.isDefault || item.is_default; }) || options[0];
            var next = validCurrent ? current : (preferred ? codexModelId(preferred) : "");
            var selected = options.find(function (item) { return codexModelId(item) === next; });
            if (selected) {
              setCodexEffort(function (currentEffort) {
                return currentEffort || String(selected.defaultReasoningEffort || selected.default_reasoning_effort || "");
              });
            }
            return next;
          });
          return data;
        })
        .catch(function (e) {
          setCodexState(function (previous) {
            return { ...previous, checking: false, available: false, error: e.message };
          });
          return null;
        });
    }

    useEffect(function () {
      if (step === "llm") loadCodexState();
      return function () {
        if (codexPoll.current) clearInterval(codexPoll.current);
      };
    }, [step]);

    function startCodexLogin() {
      setCodexBusy("login"); setError(""); setNotice("");
      post("/api/settings/openai-oauth/login", {})
        .then(function (data) {
          var authUrl = data.authUrl || data.auth_url || data.url;
          if (authUrl) window.open(authUrl, "_blank", "noopener,noreferrer");
          if (codexPoll.current) clearInterval(codexPoll.current);
          codexPoll.current = setInterval(function () {
            loadCodexState().then(function (state) {
              if (state && state.connected) {
                clearInterval(codexPoll.current);
                codexPoll.current = null;
                setCodexBusy("");
                setNotice(T("welcome.setup.llm.oauthConnected", null, "OpenAI account connected"));
              }
            });
          }, 1500);
        })
        .catch(function (e) {
          setCodexBusy("");
          setError(e.message || String(e));
        });
    }

    function saveLlm() {
      setBusy(true); setError(""); setNotice("");
      post("/api/onboarding/llm", { api_key: apiKey, base_url: baseUrl, model: model })
        .then(function (p) {
          setNotice(T("welcome.setup.llm.verified", null, "Connection verified") + (p.preview ? "：" + p.preview : ""));
          return applyResponse(p);
        })
        .catch(function (e) { setError(e.message || String(e)); })
        .finally(function () { setBusy(false); });
    }

    function saveCodexLlm() {
      setBusy(true); setError(""); setNotice("");
      post("/api/onboarding/openai-oauth", {
        model: codexModel,
        reasoning_effort: codexEffort,
      })
        .then(function (p) {
          setNotice(T("welcome.setup.llm.oauthSaved", null, "Codex model saved"));
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
      <section className="wb-wel-page wb-ob-page">
        <div className="wb-wel-inner wb-ob-inner">
          <header className="wb-wel-head wb-ob-head">
            <div className="wb-ob-eyebrow">
              <span className="wb-ob-eyebrow-dot" />
              {ob.isAbsoluteFreshStart ? T("welcome.setup.fresh", null, "Fresh install detected") : T("welcome.setup.incomplete", null, "Setup incomplete")}
            </div>
            <h1>{T("welcome.setup.title", null, "Set up Cyrene")}<span className="wb-wel-wave" role="img" aria-label="wave">👋</span></h1>
            <p>{T("welcome.setup.subtitle", null, "Connect a model and pick a personality. You can change both later in settings.")}</p>
          </header>

          <div className="wb-ob-steps">
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
                <p className="wb-ob-sub">{T("welcome.setup.llm.subtitle", null, "Choose a custom endpoint or use the models included with your OpenAI account.")}</p>
                <div className="wb-ob-seg wb-ob-source-seg">
                  <button type="button" className={"wb-ob-seg-btn" + (llmSource === "custom" ? " active" : "")} onClick={function () { setLlmSource("custom"); setError(""); setNotice(""); }}>
                    {T("welcome.setup.llm.customSource", null, "Custom model")}
                  </button>
                  <button type="button" className={"wb-ob-seg-btn" + (llmSource === "codex" ? " active" : "")} onClick={function () { setLlmSource("codex"); setError(""); setNotice(""); }}>
                    OpenAI OAuth
                  </button>
                </div>
                {llmSource === "custom" ? (
                  <React.Fragment>
                    <label className="wb-ob-field">
                      <span className="wb-ob-label">{T("welcome.setup.llm.apiKey", null, "API key")}<small>{T("welcome.setup.llm.apiKeyHint", null, "Stored locally, never uploaded")}</small></span>
                      <input className="wb-ob-input" type="password" value={apiKey} placeholder="sk-..." onChange={function (e) { setApiKey(e.target.value); }} />
                    </label>
                    <label className="wb-ob-field">
                      <span className="wb-ob-label">{T("welcome.setup.llm.endpoint", null, "Endpoint")}<small>{T("welcome.setup.llm.endpointHint", null, "Base URL, e.g. https://api.openai.com/v1")}</small></span>
                      <input className="wb-ob-input mono" value={baseUrl} placeholder="https://api.openai.com/v1" onChange={function (e) { setBaseUrl(e.target.value); }} />
                    </label>
                    <label className="wb-ob-field">
                      <span className="wb-ob-label">{T("welcome.setup.llm.model", null, "Model")}<small>{T("welcome.setup.llm.modelHint", null, "Model identifier")}</small></span>
                      <input className="wb-ob-input mono" value={model} placeholder="gpt-4o" onChange={function (e) { setModel(e.target.value); }} />
                    </label>
                    <div className="wb-ob-actions">
                      <button type="button" className="wb-btn primary" disabled={busy || !model.trim() || !baseUrl.trim()} onClick={saveLlm}>
                        {busy ? T("welcome.setup.llm.testing", null, "Testing...") : T("welcome.setup.llm.save", null, "Save & test")}
                      </button>
                    </div>
                  </React.Fragment>
                ) : (
                  <div className="wb-ob-oauth">
                    <div className="wb-ob-oauth-head">
                      <div className="wb-ob-oauth-copy">
                        <strong>{codexState.connected
                          ? String(codexState.account && (codexState.account.email || codexState.account.planType || codexState.account.plan_type) || "OpenAI")
                          : T("welcome.setup.llm.oauthTitle", null, "Use models from your OpenAI account")}</strong>
                        <span>{codexState.checking
                          ? T("welcome.setup.llm.oauthChecking", null, "Checking login status…")
                          : (codexState.connected
                            ? T("welcome.setup.llm.oauthHintConnected", null, "Choose a Codex model and thinking effort.")
                            : T("welcome.setup.llm.oauthHint", null, "Sign in through Codex. Cyrene does not store your OAuth token."))}</span>
                      </div>
                      {!codexState.connected ? (
                        <button type="button" className="wb-btn primary" disabled={!!codexBusy || codexState.checking || codexState.available === false} onClick={startCodexLogin}>
                          {codexBusy === "login" ? T("welcome.setup.llm.oauthWaiting", null, "Waiting for login…") : T("welcome.setup.llm.oauthLogin", null, "Sign in with OpenAI")}
                        </button>
                      ) : null}
                    </div>
                    {codexState.connected ? (
                      <div className="wb-ob-oauth-grid">
                        <label className="wb-ob-field">
                          <span className="wb-ob-label">{T("welcome.setup.llm.oauthModel", null, "Codex model")}</span>
                          <select className="wb-ob-input mono" value={codexModel} onChange={function (e) {
                            var value = e.target.value;
                            var selected = (codexState.models || []).find(function (item) { return codexModelId(item) === value; });
                            setCodexModel(value);
                            setCodexEffort(String(selected && (selected.defaultReasoningEffort || selected.default_reasoning_effort) || ""));
                          }}>
                            {(codexState.models || []).map(function (item) {
                              var id = codexModelId(item);
                              return <option key={id} value={id}>{item.displayName || item.display_name || id}</option>;
                            })}
                          </select>
                        </label>
                        <label className="wb-ob-field">
                          <span className="wb-ob-label">{T("welcome.setup.llm.oauthEffort", null, "Thinking effort")}</span>
                          <select className="wb-ob-input" value={codexEffort} onChange={function (e) { setCodexEffort(e.target.value); }}>
                            {(((codexState.models || []).find(function (item) { return codexModelId(item) === codexModel; }) || {}).supportedReasoningEfforts || []).map(function (option) {
                              var effort = String(option.reasoningEffort || option.reasoning_effort || option);
                              return <option key={effort} value={effort}>{T("settings.reasoningEffortValue." + effort, null, effort)}</option>;
                            })}
                          </select>
                        </label>
                      </div>
                    ) : null}
                    {codexState.connected ? (
                      <div className="wb-ob-actions">
                        <button type="button" className="wb-btn primary" disabled={busy || !codexModel} onClick={saveCodexLlm}>
                          {busy ? T("welcome.setup.llm.oauthSaving", null, "Saving…") : T("welcome.setup.llm.oauthSave", null, "Use this model")}
                        </button>
                      </div>
                    ) : null}
                  </div>
                )}
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
                      <button key={opt.id} type="button" className={"wb-ob-seg-btn" + (mode === opt.id ? " active" : "")} onClick={function () { setMode(opt.id); }}>{opt.label}</button>
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

  // ── page ─────────────────────────────────────────────────────────────
  function WorkbenchWelcomePage(props) {
    // First-run onboarding takes over the page until LLM + personality are set.
    if (props.onboarding && props.onboarding.needsOnboarding) {
      return <OnboardingFlow onboarding={props.onboarding} />;
    }
    var i18n = window.CyreneUI.require("i18n").use();
    var name = (window.CyreneUI.require("data").state.user || {}).name || "";
    // Greeting personalizes when we have a real (loaded) user name.
    var hasName = name && name !== "loading…" && name !== "User";

    var common = {
      onNewProject: props.onNewProject || function () {},
      onImportProject: props.onImportProject || props.onNewProject || function () {},
      onOpenPage: props.onOpenPage || function () {},
      onSettings: props.onSettings || function () {},
      onToggleTheme: props.onToggleTheme || function () {},
      theme: props.theme,
      actualTheme: props.actualTheme,
      lang: i18n.lang,
      setLang: i18n.setLang,
      guideUrl: props.docsUrl || "https://github.com/ikerrrrrrrrrrr/Cyrene#readme",
    };

    return (
      <section className="wb-wel-page">
        <div className="wb-wel-inner">
          <header className="wb-wel-head">
            <h1>
              {hasName
                ? T("welcome.titleNamed", { name: name }, "Welcome to Cyrene, {name}")
                : T("welcome.title", null, "Welcome to Cyrene")}
              <span className="wb-wel-wave" role="img" aria-label="wave">👋</span>
            </h1>
            <p>{T("welcome.subtitle", null, "Let's set up your workspace and start working together.")}</p>
          </header>

          <Steps hasProjects={!!props.hasProjects} />

          <div className="wb-wel-cards">
            <CreateCard onNewProject={common.onNewProject} onImportProject={common.onImportProject} />
            <CapabilitiesCard onOpenPage={common.onOpenPage} />
            <PreferencesCard
              theme={common.theme}
              actualTheme={common.actualTheme}
              lang={common.lang}
              setLang={common.setLang}
              onToggleTheme={common.onToggleTheme}
              onSettings={common.onSettings}
            />
          </div>

          <div className="wb-wel-bottom">
            <TemplatesPanel onNewProject={common.onNewProject} />
            <ImportPanel onOpenPage={common.onOpenPage} onNewProject={common.onNewProject} onSettings={common.onSettings} guideUrl={common.guideUrl} />
          </div>

          <footer className="wb-wel-foot">{T("welcome.footer", null, "You can change these anytime in settings.")}</footer>
        </div>
      </section>
    );
  }

  window.CyreneUI.welcome = window.CyreneUI.register("welcome", {
    Page: WorkbenchWelcomePage,
  });
})();
