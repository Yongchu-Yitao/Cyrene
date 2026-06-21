// Cyrene — app shell + page router
const { useState: useStateApp, useEffect: useEffectApp, useMemo: useMemoApp } = React;

function readStoredTweak(key, fallback) {
  try { var v = localStorage.getItem("cyrene-tweak-" + key); return v !== null ? JSON.parse(v) : fallback; } catch(e) { return fallback; }
}

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": readStoredTweak("theme", "system"),
  "accent": readStoredTweak("accent", "#5ec59e"),
  "density": readStoredTweak("density", "cozy"),
  "textSize": readStoredTweak("textSize", "default"),
  "orientation": readStoredTweak("orientation", "horizontal"),
  "showLegend": readStoredTweak("showLegend", true),
  "animatePulse": readStoredTweak("animatePulse", true)
}/*EDITMODE-END*/;

const ACCENT_PRESETS = {
  dark:  ["#4fd1a0", "#6dbde0", "#b8a2e0", "#e8ae5c", "#e87070"],
  light: ["#2da873", "#3b90c8", "#7858b0", "#c88520", "#d04848"],
};
const VALID_UI_PAGES = new Set(["chat", "agents", "sessions", "memory", "evolution", "settings", "tasks", "entities", "context_debug", "knowledge"]);

function readStoredUiPage() {
  try {
    var page = localStorage.getItem("cyrene-ui-page");
    if (page === "status") page = "evolution"; // migrate old key
    if (page === "skills") page = "evolution"; // merge old skills page
    return VALID_UI_PAGES.has(page) ? page : "chat";
  } catch (e) {
    return "dashboard";
  }
}

function readStoredSessionId() {
  try {
    return localStorage.getItem("cyrene-ui-session-id");
  } catch (e) {
    return null;
  }
}

function readStoredBool(key, fallback) {
  try {
    var raw = localStorage.getItem(key);
    if (raw == null) return fallback;
    return raw === "1";
  } catch (e) {
    return fallback;
  }
}

function SetupWizard({ theme, onToggleTheme }) {
  useDataVersion();
  const { t } = useI18n();
  const onboarding = DATA.onboarding || {};
  const [step, setStep] = useStateApp(onboarding.activeStep || "llm");
  const [busy, setBusy] = useStateApp(false);
  const [error, setError] = useStateApp("");
  const [notice, setNotice] = useStateApp("");
  const [llmForm, setLlmForm] = useStateApp({
    api_key: "",
    base_url: onboarding.llm?.baseUrl || "",
    model: onboarding.llm?.model || "",
  });
  const [mode, setMode] = useStateApp(onboarding.personality?.mode || "name");
  const [personalityName, setPersonalityName] = useStateApp(onboarding.personality?.label || "");
  const [customSoul, setCustomSoul] = useStateApp(onboarding.personality?.currentContent || "");

  React.useEffect(function () {
    setStep(onboarding.activeStep || "done");
    setLlmForm({
      api_key: "",
      base_url: onboarding.llm?.baseUrl || "",
      model: onboarding.llm?.model || "",
    });
    setMode(onboarding.personality?.mode || "name");
    setPersonalityName(onboarding.personality?.label || "");
    setCustomSoul(onboarding.personality?.currentContent || "");
  }, [onboarding.activeStep, onboarding.llm?.baseUrl, onboarding.llm?.model, onboarding.personality?.mode, onboarding.personality?.label, onboarding.personality?.currentContent]);

  async function applyOnboardingResponse(r) {
    const payload = await r.json().catch(() => ({}));
    if (!r.ok) {
      throw new Error(payload.error || payload.detail || ("HTTP " + r.status));
    }
    if (payload.onboarding) {
      DATA.onboarding = payload.onboarding;
      window.bumpData && window.bumpData();
    }
    if (window.reloadUiData) await window.reloadUiData();
    return payload;
  }

  async function saveLlm() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload = await applyOnboardingResponse(await fetch("/api/onboarding/llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(llmForm),
      }));
      setNotice(t("setup.llmVerified") + (payload.preview ? ": " + payload.preview : "."));
      setStep((payload.onboarding && payload.onboarding.activeStep) || "personality");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function savePersonality() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload = await applyOnboardingResponse(await fetch("/api/onboarding/personality", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: mode,
          name: personalityName,
          content: customSoul,
        }),
      }));
      setNotice(t("setup.personalityApplied"));
      setStep((payload.onboarding && payload.onboarding.activeStep) || "done");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const stepItems = [
    { id: "llm", label: t("setup.llmApi"), done: !!onboarding.llm?.configured },
    { id: "personality", label: t("setup.personality"), done: !!onboarding.personality?.configured },
  ];

  return (
    <div className="setup-shell" data-theme={theme}>
      <div className="setup-topbar">
        <div className="setup-brand">
          <div className="brand-mark"></div>
          <div>
            <div className="brand-name">{(DATA.assistantName || "Cyrene").toUpperCase()}</div>
            <div className="setup-brand-meta">
              <div className="setup-kicker">{t("setup.kicker")}</div>
              <div className="setup-version">{DATA.appVersion || "—"}</div>
            </div>
          </div>
        </div>
        <button className="theme-toggle-btn" title={theme === "dark" ? t("topbar.switchToLight") : t("topbar.switchToDark")} onClick={onToggleTheme}>
          <span className="theme-toggle-icon">
            {theme === "system"
              ? <svg width="15" height="15" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="2" y="3" width="14" height="10" rx="1.5"/><path d="M7 13v2.5M11 13v2.5M5 15.5h8"/></svg>
              : theme === "dark"
                ? <svg width="15" height="15" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M11 4.5A7 7 0 1 1 9 13.5A5 5 0 1 0 11 4.5Z"/></svg>
                : <svg width="15" height="15" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="9" cy="9" r="3.5"/><path d="M9 2v2M9 14v2M2 9h2M14 9h2M4.5 4.5l1.5 1.5M12 12l1.5 1.5M4.5 13.5l1.5-1.5M12 6l1.5-1.5"/></svg>
            }
          </span>
          <span>{theme === "system" ? t("settings.system") : theme === "dark" ? t("settings.light") : t("settings.dark")}</span>
        </button>
      </div>

      <div className="setup-hero">
        <div className="setup-copy">
          <div className="setup-eyebrow">{onboarding.isAbsoluteFreshStart ? t("setup.freshDetected") : t("setup.setupIncomplete")}</div>
          <h1>{t("setup.heroTitle")}</h1>
          <p>{t("setup.heroDesc")}</p>
        </div>
        <div className="setup-steps">
          {stepItems.map((item, index) => (
            <div key={item.id} className={"setup-step-card " + ((step === item.id && onboarding.needsOnboarding) ? "active" : "")}>
              <div className="setup-step-index">{item.done ? "✓" : index + 1}</div>
              <div>
                <div className="setup-step-label">{item.label}</div>
                <div className="setup-step-meta">{item.done ? t("setup.configured") : t("setup.required")}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="setup-panel">
        {step === "llm" && (
          <div className="setup-section">
            <h2>{t("setup.llmSectionTitle")}</h2>
            <p className="subtitle">{t("setup.llmSubtitle")}</p>
            <div className="field">
              <div className="label">{t("setup.apiKeyLabel")}<small>{t("setup.apiKeyHint")}</small></div>
              <input
                className="input"
                type="password"
                value={llmForm.api_key}
                onChange={(e) => setLlmForm({ ...llmForm, api_key: e.target.value })}
                placeholder="sk-..."
              />
            </div>
            <div className="field">
              <div className="label">{t("setup.endpointLabel")}<small>{t("setup.endpointHint")}</small></div>
              <input
                className="input mono"
                value={llmForm.base_url}
                onChange={(e) => setLlmForm({ ...llmForm, base_url: e.target.value })}
              />
            </div>
            <div className="field">
              <div className="label">{t("setup.modelLabel")}<small>{t("setup.modelHint")}</small></div>
              <input
                className="input mono"
                value={llmForm.model}
                onChange={(e) => setLlmForm({ ...llmForm, model: e.target.value })}
              />
            </div>
            <div className="setup-actions">
              <button className="btn primary" onClick={saveLlm} disabled={busy}>{busy ? t("setup.testing") : t("setup.saveAndTest")}</button>
            </div>
          </div>
        )}

        {step === "personality" && (
          <div className="setup-section">
            <h2>{t("setup.personalitySectionTitle")}</h2>
            <p className="subtitle">{t("setup.personalitySubtitle")}</p>
            <div className="seg" style={{ marginBottom: 18 }}>
              <button className={"seg-btn " + (mode === "name" ? "active" : "")} onClick={() => setMode("name")}>{t("setup.byName")}</button>
              <button className={"seg-btn " + (mode === "custom" ? "active" : "")} onClick={() => setMode("custom")}>{t("setup.customSoul")}</button>
              <button className={"seg-btn " + (mode === "default" ? "active" : "")} onClick={() => setMode("default")}>{t("setup.defaultLabel")}</button>
            </div>

            {mode === "name" && (
              <div className="field">
                <div className="label">{t("setup.personalityNameLabel")}<small>{t("setup.personalityNameHint")}</small></div>
                <input
                  className="input"
                  value={personalityName}
                  onChange={(e) => setPersonalityName(e.target.value)}
                  placeholder="Lelouch Lamperouge / Steve Jobs / Sherlock Holmes"
                />
              </div>
            )}

            {mode === "custom" && (
              <div className="field" style={{ display: "block" }}>
                <div className="label" style={{ marginBottom: 8 }}>{t("setup.soulContentLabel")}<small>{t("setup.soulContentHint")}</small></div>
                <textarea
                  className="input mono"
                  value={customSoul}
                  onChange={(e) => setCustomSoul(e.target.value)}
                  style={{ width: "100%", minHeight: 260, fontSize: 12, lineHeight: 1.5 }}
                />
              </div>
            )}

            {mode === "default" && (
              <div className="setup-note">
                {t("setup.defaultDesc")}
              </div>
            )}

            <div className="setup-actions">
              <button className="btn primary" onClick={savePersonality} disabled={busy}>{busy ? t("setup.applying") : t("setup.applyPersonality")}</button>
            </div>
          </div>
        )}

        {step === "done" && (
          <div className="setup-section">
            <h2>{t("setup.workspaceReady")}</h2>
            <p className="subtitle">{t("setup.workspaceReadyDesc")}</p>
            <div className="setup-actions">
              <button className="btn primary" onClick={() => { DATA.onboarding = { ...DATA.onboarding, needsOnboarding: false }; window.bumpData && window.bumpData(); }}>{t("setup.enterWorkspace")}</button>
            </div>
          </div>
        )}

        {(notice || error) && (
          <div className={"setup-feedback " + (error ? "error" : "ok")}>
            {error || notice}
          </div>
        )}
      </div>
    </div>
  );
}

function LegacyAppShell() {
  useDataVersion();
  const { lang } = useI18n();
  const [page, setPage] = useStateApp(readStoredUiPage);
  const [evolutionTab, setEvolutionTab] = useStateApp("skills");
  const [selectedSessionId, setSelectedSessionId] = useStateApp(readStoredSessionId);
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useStateApp(function () { return readStoredBool("cyrene-left-sidebar-collapsed", false); });
  const [rightSidebarCollapsed, setRightSidebarCollapsed] = useStateApp(function () { return readStoredBool("cyrene-right-sidebar-collapsed", false); });
  const [rightSidebarView, setRightSidebarView] = useStateApp(function () {
    try { return localStorage.getItem("cyrene-right-sidebar-view") || "overview"; }
    catch (e) { return "overview"; }
  });
  const [searchOpen, setSearchOpen] = useStateApp(false);
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  // ── SSE-driven real-time status for the topbar status light ──
  const [realtimeStatus, setRealtimeStatus] = useStateApp(null);
  const realtimeStatusTimerRef = React.useRef(null);

  useEffectApp(function () {
    if (typeof window.__sseHandlers === "undefined") return;
    const PROCESSING_TYPES = ["phase_transition", "tool_call", "llm_call"];

    function handler(event) {
      var newStatus = null;
      if (PROCESSING_TYPES.indexOf(event.type) !== -1) {
        newStatus = "running";
      } else if (event.type === "chat_message") {
        newStatus = "done";
      } else if (event.type === "session_update" && event.status) {
        newStatus = event.status === "err" ? "error" : event.status;
      }
      if (newStatus) {
        setRealtimeStatus(newStatus);
      }
      // Auto-clear after 30 s of no SSE events to avoid stale "running"
      if (realtimeStatusTimerRef.current) {
        clearTimeout(realtimeStatusTimerRef.current);
      }
      realtimeStatusTimerRef.current = setTimeout(function () {
        setRealtimeStatus(null);
      }, 30000);
    }

    window.__sseHandlers.add(handler);
    return function () {
      window.__sseHandlers.delete(handler);
      if (realtimeStatusTimerRef.current) {
        clearTimeout(realtimeStatusTimerRef.current);
      }
    };
  }, []);

  const activeSession = useMemoApp(function () {
    return (selectedSessionId
      ? DATA.sessions.find(function (session) { return session.id === selectedSessionId; })
      : null) || DATA.sessions[0] || null;
  }, [selectedSessionId, DATA.sessions]);

  function selectSession(id) {
    setSelectedSessionId(id || null);
  }

  const [systemTheme, setSystemTheme] = useStateApp(function () {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  function resolveActualTheme(mode) {
    if (mode === "system") {
      return systemTheme;
    }
    return mode;
  }

  const actualTheme = React.useMemo(function () {
    return resolveActualTheme(t.theme);
  }, [t.theme, systemTheme]);

  useEffectApp(function () {
    localStorage.setItem("cyrene-tweak-theme", JSON.stringify(t.theme));
    localStorage.setItem("cyrene-tweak-accent", JSON.stringify(t.accent));
    localStorage.setItem("cyrene-tweak-density", JSON.stringify(t.density));
    localStorage.setItem("cyrene-tweak-textSize", JSON.stringify(t.textSize));
    localStorage.setItem("cyrene-tweak-orientation", JSON.stringify(t.orientation));
    localStorage.setItem("cyrene-tweak-showLegend", JSON.stringify(t.showLegend));
    localStorage.setItem("cyrene-tweak-animatePulse", JSON.stringify(t.animatePulse));
    /* Legacy key kept for backward compat (read by index.html <script>). */
    localStorage.setItem("cyrene-theme-mode", t.theme);
    const applied = actualTheme;
    document.documentElement.dataset.theme = applied;
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.documentElement.style.setProperty("--accent", t.accent);
    const m = t.accent.match(/^#([0-9a-f]{6})$/i);
    if (m) {
      const r = parseInt(m[1].slice(0,2),16), g = parseInt(m[1].slice(2,4),16), b = parseInt(m[1].slice(4,6),16);
      document.documentElement.style.setProperty("--accent-faint", `rgba(${r},${g},${b},0.08)`);
      document.documentElement.style.setProperty("--accent-dim", `rgba(${r},${g},${b},0.35)`);
      const lum = (0.299*r + 0.587*g + 0.114*b) / 255;
      document.documentElement.style.setProperty("--accent-text", lum > 0.55 ? "#0d1612" : "#ffffff");
    }
    document.documentElement.dataset.density = t.density;
    document.documentElement.dataset.textSize = t.textSize || "default";
    document.documentElement.dataset.animPulse = t.animatePulse ? "on" : "off";
    document.documentElement.dataset.legend = t.showLegend ? "on" : "off";
    window.dispatchEvent(new CustomEvent("cyrene:theme-change", {
      detail: { mode: t.theme, actualTheme: applied },
    }));
    delete document.documentElement.dataset.booting;
  }, [t.theme, t.accent, t.density, t.textSize, t.animatePulse, t.showLegend, actualTheme]);

  useEffectApp(function () {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    function onChange(event) {
      setSystemTheme(event.matches ? "dark" : "light");
    }
    mq.addEventListener("change", onChange);
    return function () { mq.removeEventListener("change", onChange); };
  }, []);

  function toggleTheme() {
    const order = ["system", "light", "dark"];
    const idx = order.indexOf(t.theme);
    const nextMode = order[(idx + 1) % 3];
    const nextActual = resolveActualTheme(nextMode);
    const presetIndex = (ACCENT_PRESETS[actualTheme] || []).indexOf(t.accent);
    const nextAccent = presetIndex >= 0 ? (ACCENT_PRESETS[nextActual] || [])[presetIndex] : t.accent;
    setTweak({ theme: nextMode, accent: nextAccent });
  }

  useEffectApp(function () {
    try {
      localStorage.setItem("cyrene-ui-page", page);
    } catch (e) {}
  }, [page]);

  useEffectApp(function () {
    try {
      if (selectedSessionId) localStorage.setItem("cyrene-ui-session-id", selectedSessionId);
      else localStorage.removeItem("cyrene-ui-session-id");
    } catch (e) {}
  }, [selectedSessionId]);

  useEffectApp(function () {
    try {
      localStorage.setItem("cyrene-left-sidebar-collapsed", leftSidebarCollapsed ? "1" : "0");
    } catch (e) {}
  }, [leftSidebarCollapsed]);

  useEffectApp(function () {
    try {
      localStorage.setItem("cyrene-right-sidebar-collapsed", rightSidebarCollapsed ? "1" : "0");
    } catch (e) {}
  }, [rightSidebarCollapsed]);

  useEffectApp(function () {
    try {
      localStorage.setItem("cyrene-right-sidebar-view", rightSidebarView);
    } catch (e) {}
  }, [rightSidebarView]);

  useEffectApp(function () {
    if (selectedSessionId && !DATA.sessions.some(function (session) { return session.id === selectedSessionId; })) {
      setSelectedSessionId(null);
    }
  }, [selectedSessionId, DATA.sessions]);

  useEffectApp(function () {
    window.__selectedSessionId = activeSession ? activeSession.id : null;
    window.selectUiSession = selectSession;
    window.__setAppPage = setPage;
    return function () {
      delete window.__selectedSessionId;
      delete window.selectUiSession;
      delete window.__setAppPage;
    };
  }, [activeSession]);

  const needsOnboarding = !!(DATA.onboarding && DATA.onboarding.needsOnboarding);
  const canCollapseRightSidebar = page === "chat" || page === "agents" || page === "sessions";

  if (needsOnboarding) {
    return <SetupWizard theme={t.theme} onToggleTheme={toggleTheme} />;
  }

  return (
    <div className={"app" + (leftSidebarCollapsed ? " sidebar-collapsed" : "")} data-screen-label={"Cyrene · " + page}>
      <Sidebar
        page={page}
        setPage={setPage}
        selectedSessionId={activeSession ? activeSession.id : null}
        onSelectSession={selectSession}
        collapsed={leftSidebarCollapsed}
        onToggleCollapsed={function () { setLeftSidebarCollapsed(function (value) { return !value; }); }}
        onOpenSearch={function () { setSearchOpen(true); }}
      />
      <div className="page">
        <Topbar
          page={page}
          theme={t.theme}
          onToggleTheme={toggleTheme}
          activeSession={activeSession}
          realtimeStatus={realtimeStatus}
          setPage={setPage}
          leftSidebarCollapsed={leftSidebarCollapsed}
          onToggleLeftSidebar={function () { setLeftSidebarCollapsed(function (value) { return !value; }); }}
          rightSidebarCollapsed={rightSidebarCollapsed}
          onToggleRightSidebar={function () {
            if (!canCollapseRightSidebar) return;
            setRightSidebarCollapsed(function (value) { return !value; });
          }}
          canCollapseRightSidebar={canCollapseRightSidebar}
          evolutionTab={evolutionTab}
          setEvolutionTab={setEvolutionTab}
        />
        {page === "dashboard" && <DashboardPage />}
        {page === "chat"     && <ChatPage
                                  selectedSessionId={activeSession ? activeSession.id : null}
                                  onSelectSession={selectSession}
                                  rightSidebarCollapsed={rightSidebarCollapsed}
                                  setRightSidebarCollapsed={setRightSidebarCollapsed}
                                  rightSidebarView={rightSidebarView}
                                  setRightSidebarView={setRightSidebarView} />}
        {page === "agents"   && <AgentsPage orientation={t.orientation} selectedSessionId={activeSession ? activeSession.id : null} rightSidebarCollapsed={false} />}
        {page === "sessions" && <SessionsPage
                                  selectedSessionId={activeSession ? activeSession.id : null}
                                  onSelectSession={selectSession}
                                  rightSidebarCollapsed={false}
                                  onOpenAgents={(sessionId) => {
                                    selectSession(sessionId);
                                    setRightSidebarCollapsed(false);
                                    setRightSidebarView("agents");
                                    setPage("chat");
                                  }} />}
        {page === "memory"   && <MemoryPage />}
        {page === "context_debug" && React.createElement(
          window.ContextDebuggerPage || (function () { return React.createElement("div", { className: "page" }, "Loading context debugger..."); }),
          {}
        )}
        {page === "evolution" && <EvolutionPage tab={evolutionTab} setTab={setEvolutionTab} />}
        {page === "tasks" && React.createElement(
          window.ScheduledTasksPage || (function () { return React.createElement("div", { className: "page" }, "Loading tasks..."); }),
          {}
        )}
        {page === "entities" && React.createElement(
          window.EntitiesPage || (function () { return React.createElement("div", { className: "page" }, "Loading..."); }),
          {}
        )}
        {page === "knowledge" && React.createElement(
          window.KnowledgePage || (function () { return React.createElement("div", { className: "page" }, "Loading..."); }),
          {}
        )}
        {page === "settings" && (
          <SettingsPage
            tweaks={t}
            setTweak={setTweak}
            actualTheme={actualTheme}
            accentPresets={ACCENT_PRESETS[actualTheme] || []}
          />
        )}
      </div>

      {searchOpen && React.createElement(
        window.SearchOverlay || (function () { return null; }),
        {
          onClose: function () { setSearchOpen(false); },
        }
      )}
    </div>
  );
}

function readDeveloperMode() {
  try { return localStorage.getItem("cyrene-developer-mode") === "1"; } catch(e) { return false; }
}

// Renders the user's avatar with priority: uploaded image > emoji > initials+color.
function UserAvatar({ user, size }) {
  user = user || {};
  size = size || 28;
  const px = size + "px";
  const base = { width: px, height: px, fontSize: Math.round(size * 0.42) + "px" };
  if (user.avatar) {
    return <div className="avatar" aria-label={user.name}
                style={{ ...base, backgroundImage: "url(" + user.avatar + ")", backgroundSize: "cover", backgroundPosition: "center", borderColor: "transparent" }}></div>;
  }
  if (user.avatar_emoji) {
    return <div className="avatar" aria-label={user.name} style={base}>{user.avatar_emoji}</div>;
  }
  const initials = user.initials || (user.name || "U").slice(0, 2).toUpperCase();
  const style = user.avatar_color
    ? { ...base, background: user.avatar_color, color: "#fff", borderColor: "transparent" }
    : base;
  return <div className="avatar" aria-label={user.name} style={style}>{initials}</div>;
}

// Milliseconds → compact human-readable duration (e.g. "1h2m", "45m", "12s").
function formatDuration(ms) {
  ms = Number(ms) || 0;
  if (ms < 1000) return "0s";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return h + "h" + (m > 0 ? m + "m" : "");
  if (m > 0) return m + "m" + (sec > 0 ? sec + "s" : "");
  return sec + "s";
}

// Compact integer (64300000 → "64.3M") for stat cards that would otherwise overflow.
function compactNumber(n) {
  n = Number(n) || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
  return String(n);
}

// "16:00-20:00" → "16–20点" (zh) / "16–20" (en); leaves "—" untouched.
function formatPeakHour(label, lang) {
  label = String(label || "");
  if (label.indexOf("-") < 0) return label || "—";
  const ends = label.split("-").map(function (s) { return s.replace(":00", ""); });
  const span = ends[0] + "–" + ends[1];
  return lang === "zh" ? span + "点" : span;
}

// Friendly labels for the "most used" tools; unknown tools fall back to a prettified name.
const PROFILE_FEATURE_LABELS = {
  web_search: { en: "Web search", zh: "联网搜索" },
  fetch_url: { en: "Fetch page", zh: "网页抓取" },
  run_shell: { en: "Shell", zh: "终端" },
  bash: { en: "Shell", zh: "终端" },
  read_file: { en: "Read file", zh: "读文件" },
  write_file: { en: "Write file", zh: "写文件" },
  edit_file: { en: "Edit file", zh: "改文件" },
  save_project_memory: { en: "Memory", zh: "记忆" },
  recall_memory: { en: "Recall", zh: "回忆" },
  recall_conversation: { en: "Recall chat", zh: "回忆对话" },
  search_project_memory: { en: "Search memory", zh: "搜索记忆" },
  schedule_task: { en: "Schedule", zh: "计划任务" },
  send_message_to_user: { en: "Message", zh: "发消息" },
};
function profileFeatureLabel(tool, lang) {
  tool = String(tool || "");
  const hit = PROFILE_FEATURE_LABELS[tool];
  if (hit) return hit[lang] || hit.en;
  if (tool.indexOf("browser") === 0) return lang === "zh" ? "浏览器" : "Browser";
  return tool.replace(/[_-]+/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
}

const PROFILE_AVATAR_COLORS = ["#1D9E75", "#378ADD", "#D4537E", "#BA7517", "#7F77DD", "#D85A30"];
const PROFILE_EMOJI_PICKS = ["😀", "🐱", "🚀", "🌟", "🦊", "🐼", "🌿", "🔥"];

// Popover anchored above the sidebar footer: identity editing + personal activity stats.
function ProfilePanel({ onClose, setPage }) {
  useDataVersion();
  const { t, lang } = useI18n();
  const user = DATA.user || {};
  const usage = (DATA.dashboard && DATA.dashboard.usage) || {};
  const taskTime = usage.task_time || {};
  const topTools = usage.top_tools || [];

  const [editing, setEditing] = useStateApp(false);
  const [name, setName] = useStateApp(user.name || "");
  const [bio, setBio] = useStateApp(user.bio || "");
  const [avatarMode, setAvatarMode] = useStateApp(user.avatar ? "image" : (user.avatar_emoji ? "emoji" : "letter"));
  const [avatarData, setAvatarData] = useStateApp(user.avatar || "");
  const [emoji, setEmoji] = useStateApp(user.avatar_emoji || "");
  const [color, setColor] = useStateApp(user.avatar_color || PROFILE_AVATAR_COLORS[0]);
  const [saving, setSaving] = useStateApp(false);
  const [err, setErr] = useStateApp("");
  const fileRef = React.useRef(null);

  useEffectApp(function () {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return function () { window.removeEventListener("keydown", onKey); };
  }, []);

  function beginEdit() {
    setName(user.name || ""); setBio(user.bio || "");
    setAvatarMode(user.avatar ? "image" : (user.avatar_emoji ? "emoji" : "letter"));
    setAvatarData(user.avatar || ""); setEmoji(user.avatar_emoji || "");
    setColor(user.avatar_color || PROFILE_AVATAR_COLORS[0]);
    setErr(""); setEditing(true);
  }

  function onPickImage(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function () {
      const img = new Image();
      img.onload = function () {
        const max = 256;
        const scale = Math.min(1, max / Math.max(img.width, img.height));
        const w = Math.round(img.width * scale), h = Math.round(img.height * scale);
        const canvas = document.createElement("canvas");
        canvas.width = w; canvas.height = h;
        canvas.getContext("2d").drawImage(img, 0, 0, w, h);
        setAvatarData(canvas.toDataURL("image/jpeg", 0.85));
        setAvatarMode("image");
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  }

  function save() {
    setSaving(true); setErr("");
    const payload = { name: name.trim(), bio: bio.trim() };
    if (avatarMode === "image") { payload.avatar = avatarData || ""; payload.avatar_emoji = ""; }
    else if (avatarMode === "emoji") { payload.avatar = ""; payload.avatar_emoji = (emoji || "").trim(); }
    else { payload.avatar = ""; payload.avatar_emoji = ""; payload.avatar_color = color || ""; }
    fetch("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { throw new Error(e.error || "HTTP " + r.status); }); })
      .then(function (d) {
        if (d.user) { DATA.user = d.user; window.bumpData && window.bumpData(); }
        setSaving(false); setEditing(false);
      })
      .catch(function (e) { setSaving(false); setErr(String(e.message || e)); });
  }

  const previewUser = editing
    ? { name: name, initials: (name || "U").slice(0, 2).toUpperCase(),
        avatar: avatarMode === "image" ? avatarData : "",
        avatar_emoji: avatarMode === "emoji" ? emoji : "",
        avatar_color: avatarMode === "letter" ? color : "" }
    : user;

  function Cell(opts) {
    var cls = "profile-cell" + (opts.hero ? " hero" : "") + (opts.accent ? " accent" : "");
    return (
      <div className={cls} title={opts.title || undefined}>
        <div className="profile-cell-value">{opts.value}</div>
        <div className="profile-cell-label">{opts.label}</div>
      </div>
    );
  }

  return (
    <>
      <div className="profile-backdrop" onClick={onClose}></div>
      <div className="profile-panel" onClick={function (e) { e.stopPropagation(); }} role="dialog" aria-label={t("profile.open")}>
        <div className="profile-head">
          <div className="profile-avatar-wrap">
            <UserAvatar user={previewUser} size={52} />
            {editing && (
              <button type="button" className="profile-avatar-cam" title={t("profile.avatarImage")}
                      onClick={function () { setAvatarMode("image"); fileRef.current && fileRef.current.click(); }}>
                <svg width="12" height="12" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M3 6h3l1.2-1.6h3.6L15 6h0v8H3z" /><circle cx="9" cy="10" r="2.4" />
                </svg>
              </button>
            )}
          </div>
          {!editing ? (
            <div className="profile-id">
              <div className="profile-name">{user.name}</div>
              <div className="profile-handle">@{user.handle} · {DATA.appVersion || "—"}</div>
              {user.bio ? <div className="profile-bio">{user.bio}</div> : null}
            </div>
          ) : (
            <div className="profile-id">
              <input className="profile-input" value={name} maxLength={60}
                     placeholder={t("profile.namePlaceholder")} onChange={function (e) { setName(e.target.value); }} />
              <input className="profile-input" value={bio} maxLength={120} style={{ marginTop: 6 }}
                     placeholder={t("profile.bioPlaceholder")} onChange={function (e) { setBio(e.target.value); }} />
            </div>
          )}
          {!editing && (
            <button type="button" className="profile-edit-btn" title={t("profile.edit")} onClick={beginEdit}>
              <svg width="15" height="15" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3l3 3-8 8H4v-3z" />
              </svg>
            </button>
          )}
        </div>

        {editing && (
          <div className="profile-edit-avatar">
            <input ref={fileRef} type="file" accept="image/*" style={{ display: "none" }}
                   onChange={function (e) { onPickImage(e.target.files && e.target.files[0]); e.target.value = ""; }} />
            <div className="profile-seg">
              <button type="button" className={avatarMode === "image" ? "active" : ""} onClick={function () { setAvatarMode("image"); if (!avatarData) fileRef.current && fileRef.current.click(); }}>{t("profile.avatarImage")}</button>
              <button type="button" className={avatarMode === "emoji" ? "active" : ""} onClick={function () { setAvatarMode("emoji"); }}>{t("profile.avatarEmoji")}</button>
              <button type="button" className={avatarMode === "letter" ? "active" : ""} onClick={function () { setAvatarMode("letter"); }}>{t("profile.avatarLetter")}</button>
            </div>
            {avatarMode === "image" && (
              <button type="button" className="profile-upload" onClick={function () { fileRef.current && fileRef.current.click(); }}>{t("profile.upload")}</button>
            )}
            {avatarMode === "emoji" && (
              <div className="profile-picks">
                {PROFILE_EMOJI_PICKS.map(function (em) {
                  return <button type="button" key={em} className={"profile-emoji" + (emoji === em ? " active" : "")} onClick={function () { setEmoji(em); }}>{em}</button>;
                })}
              </div>
            )}
            {avatarMode === "letter" && (
              <div className="profile-picks">
                {PROFILE_AVATAR_COLORS.map(function (c) {
                  return <button type="button" key={c} className={"profile-swatch" + (color === c ? " active" : "")} style={{ background: c }} onClick={function () { setColor(c); }} aria-label={c}></button>;
                })}
              </div>
            )}
            {err ? <div className="profile-err">{err}</div> : null}
            <div className="profile-edit-actions">
              <button type="button" className="profile-btn" onClick={function () { setEditing(false); }}>{t("profile.cancel")}</button>
              <button type="button" className="profile-btn primary" disabled={saving} onClick={save}>{saving ? "…" : t("profile.save")}</button>
            </div>
          </div>
        )}

        {!editing && (
          <>
            <div className="profile-section">
              <div className="profile-section-title">{t("profile.usage")}</div>
              <div className="profile-bento">
                {Cell({ hero: true, value: usage.spend || "—", label: t("profile.spend") })}
                {Cell({ value: usage.requests != null ? usage.requests : "—", label: t("profile.requests") })}
                {Cell({ value: usage.total_tokens ? compactNumber(usage.total_tokens) : "—", label: t("profile.tokens"), title: usage.tokens || "" })}
              </div>
            </div>
            <div className="profile-section">
              <div className="profile-section-title">{t("profile.activity")}</div>
              <div className="profile-bento cols-3">
                {Cell({ accent: true, value: (usage.current_streak || 0) + (lang === "zh" ? " 天" : "d"), label: t("profile.streak") })}
                {Cell({ value: usage.active_days != null ? usage.active_days : "—", label: t("profile.activeDays") })}
                {Cell({ value: formatPeakHour(usage.peak_hour, lang), label: t("profile.peakHour"), title: usage.peak_hour || "" })}
              </div>
            </div>
            <div className="profile-section">
              <div className="profile-section-title">{t("profile.tasks")}</div>
              <div className="profile-bento">
                {Cell({ value: formatDuration(taskTime.total_ms), label: t("profile.taskTotal") })}
                {Cell({ value: formatDuration(taskTime.longest_ms), label: t("profile.taskLongest") })}
              </div>
              <div className="profile-subnote">{lang === "zh" ? ("共 " + (taskTime.runs || 0) + " 次任务") : ((taskTime.runs || 0) + " runs total")}</div>
            </div>
            <div className="profile-section">
              <div className="profile-section-title">{t("profile.topTools")} <span className="profile-hint">· {t("profile.topToolsHint")}</span></div>
              {topTools.length ? (
                <div className="profile-tools">
                  {topTools.map(function (it) {
                    var max = (topTools[0] && topTools[0].count) || 1;
                    var pct = Math.max(8, Math.round((it.count / max) * 100));
                    return (
                      <div className="profile-tool" key={it.tool}>
                        <span className="profile-tool-name">{profileFeatureLabel(it.tool, lang)}</span>
                        <span className="profile-tool-bar"><span style={{ width: pct + "%" }}></span></span>
                        <span className="profile-tool-count">{it.count}</span>
                      </div>
                    );
                  })}
                </div>
              ) : <div className="profile-empty">{t("profile.empty")}</div>}
            </div>
            <button type="button" className="profile-settings-link" onClick={function () { onClose(); setPage("settings"); }}>
              {t("nav.settings")}
            </button>
          </>
        )}
      </div>
    </>
  );
}

// Exposed so the workbench shell (separate bundle, same React + DATA + styles)
// can open the same profile panel from its own account chip.
window.ProfilePanel = ProfilePanel;
window.UserAvatar = UserAvatar;

function Sidebar({ page, setPage, selectedSessionId, onSelectSession, collapsed, onToggleCollapsed, onOpenSearch }) {
  useDataVersion();
  const { t } = useI18n();
  const [devMode, setDevMode] = useStateApp(readDeveloperMode);
  const [profileOpen, setProfileOpen] = useStateApp(false);

  useEffectApp(function () {
    function onDevModeChange() { setDevMode(readDeveloperMode()); }
    window.addEventListener("cyrene-developer-mode-change", onDevModeChange);
    return function () { window.removeEventListener("cyrene-developer-mode-change", onDevModeChange); };
  }, []);

  const sessionCount = (DATA.sessions || []).length;
  const activeRecentSessionId = selectedSessionId || DATA.sessions[0]?.id || null;
  const allItems = [
    { id: "dashboard", label: t("nav.dashboard"), icon: "◫", key: "1", devOnly: true },
    { id: "chat",     label: t("nav.chat"),     icon: "▸", key: "2" },
    { id: "agents",   label: t("nav.agentFlow"),   icon: "⌘", key: "3", devOnly: true },
    { id: "tasks",    label: t("nav.tasks"),    icon: "◎", key: "4" },
    { id: "sessions", label: t("nav.sessions"), icon: "≡", key: "5", badge: sessionCount > 0 ? String(sessionCount) : null },
    { id: "memory",   label: t("nav.memory"),   icon: "▤", key: "6" },
    { id: "context_debug", label: t("nav.contextDebug"), icon: "◇", key: "7", devOnly: true },
    { id: "evolution", label: t("nav.evolution"), icon: "⟁", key: "8", cssClass: "evo-icon" },
    { id: "entities", label: t("nav.entities"), icon: "⊙", key: "9" },
    { id: "knowledge", label: t("nav.knowledge"), icon: "✦", key: "0" },
  ];
  const items = allItems.filter(function (it) { return !it.devOnly || devMode; });
  const brandName = (DATA.assistantName || "CYRENE").toUpperCase();
  return (
    <div className={"sidebar" + (collapsed ? " collapsed" : "")}>
      <div className="sidebar-tools">
        <div className="sidebar-brand-inline" title={brandName} onClick={() => { try { localStorage.setItem("cyrene-settings-section", "about"); } catch(e) {} setPage("settings"); }} style={{ cursor: "pointer" }}>
          <div className="brand-mark"></div>
          <div className="brand-name">{brandName}</div>
        </div>
        <span className="sidebar-tool-spacer"></span>
        <button className="windowbar-btn" type="button" title={t("topbar.search")} onClick={function () { onOpenSearch && onOpenSearch(); }}>
          <svg width="15" height="15" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="8" cy="8" r="4.2" />
            <path d="M11.2 11.2 15 15" />
          </svg>
        </button>
        <button className="windowbar-btn sidebar-collapse-btn" type="button" title={collapsed ? t("topbar.expandLeft") : t("topbar.collapseLeft")} onClick={onToggleCollapsed}>
          <svg width="15" height="15" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.7">
            <rect x="3" y="3" width="12" height="12" rx="2.5" />
            <path d="M7 3v12" />
          </svg>
        </button>
      </div>

      <div className="nav" style={{ paddingTop: 0 }}>
        {items.map((it) => (
          <div key={it.id}
               className={"nav-item" + (page === it.id ? " active" : "") + (it.cssClass ? " " + it.cssClass : "")}
               onClick={() => setPage(it.id)}>
            <span style={{ color: "currentColor", fontFamily: "var(--mono)", width: 14, textAlign: "center" }}>
              {it.icon}
            </span>
            <span>{it.label}</span>
            {it.badge && <span className="nav-badge">{it.badge}</span>}
          </div>
        ))}
      </div>

      {!collapsed && (
        <>
          <div className="nav-section nav-section-collapsible" style={{ cursor: "default" }}>
            <span>{t("nav.recentSessions")}</span>
            <span className="nav-section-link"
                  title={t("chat.newSessionTitle")}
                  onClick={async (e) => {
                    e.stopPropagation();
                    if (!confirm(t("chat.confirmNewSession"))) return;
                    try {
                      if (window.resetChatRuntime) window.resetChatRuntime({ abort: true });
                      const r = await fetch("/api/sessions", { method: "POST" });
                      if (!r.ok) throw new Error("HTTP " + r.status);
                      const data = await r.json();
                      if (data.sessions) { DATA.sessions = data.sessions; window.bumpData && window.bumpData(); }
                    } catch (err) { alert("Failed: " + err.message); }
                  }}>
              {t("nav.newSession")}
            </span>
          </div>
          <div className="nav recent-session-list" style={{ paddingTop: 0 }}>
            {DATA.sessions.slice(0, 4).map((r) => (
              <div key={r.id}
                   className={"nav-item recent-session-item " + (r.id === activeRecentSessionId ? "active" : "")}
                   onClick={function () {
                            onSelectSession && onSelectSession(r.id);
                          }}
                   title={r.title}>
                <span className={"sa-dot " + r.status} style={{ marginTop: 0, width: 6, height: 6, flexShrink: 0 }}></span>
                <span style={{
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  fontSize: 14, color: "inherit"
                }}>{r.title}</span>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="sidebar-footer">
        <button type="button" className="profile-chip" title={t("profile.open")} onClick={() => setProfileOpen(true)}>
          <UserAvatar user={DATA.user} size={28} />
          <div className="who">
            {DATA.user.name}
            <small>@{DATA.user.handle} · {DATA.appVersion || "—"}</small>
          </div>
        </button>
        {profileOpen && <ProfilePanel onClose={() => setProfileOpen(false)} setPage={setPage} />}
        <button className="windowbar-btn" type="button" title={t("nav.settings")} style={{ marginLeft: "auto" }} onClick={() => setPage("settings")}>
          <svg width="21" height="21" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="9" cy="9" r="4" />
            <rect x="7.5" y="2.5" width="3" height="2.5" rx="0.5" />
            <rect x="7.5" y="13" width="3" height="2.5" rx="0.5" />
            <rect x="2.5" y="7.5" width="2.5" height="3" rx="0.5" />
            <rect x="13" y="7.5" width="2.5" height="3" rx="0.5" />
            <rect x="7.5" y="2.5" width="3" height="2.5" rx="0.5" transform="rotate(45 9 9)" />
            <rect x="7.5" y="2.5" width="3" height="2.5" rx="0.5" transform="rotate(135 9 9)" />
            <rect x="7.5" y="2.5" width="3" height="2.5" rx="0.5" transform="rotate(225 9 9)" />
            <rect x="7.5" y="2.5" width="3" height="2.5" rx="0.5" transform="rotate(315 9 9)" />
            <circle cx="9" cy="9" r="2" />
          </svg>
        </button>
      </div>
    </div>
  );
}

function SkillsRail({ onOpenPage }) {
  const dv = useDataVersion();
  const [skills, setSkills] = useStateApp(() => (DATA.skills || []).map((s) => ({ ...s })));
  useEffectApp(() => {
    setSkills((DATA.skills || []).map((s) => ({ ...s })));
  }, [dv]);
  const { t: skT } = useI18n();
  const enabledCount = skills.filter((s) => s.enabled).length;
  return (
    <div className="skills-rail">
      <div className="skills-meta">
        <span>{skT("nav.enabledCount", { n: enabledCount, m: skills.length })}</span>
      </div>
      <div className="skills-list">
        {skills.map((s) => (
          <div key={s.id}
               className={"skill-item " + (s.enabled ? "on" : "off")}
               onClick={onOpenPage}
               title={s.desc}>
            <span className="skill-check" aria-hidden="true">
              {s.enabled ? (
                <svg width="9" height="9" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M1.6 5.4 L4 7.8 L8.4 2.2" />
                </svg>
              ) : null}
            </span>
            <span className="skill-name">{s.name}</span>
            {s.hotkey && <span className="skill-hotkey">/{s.hotkey.toLowerCase()}</span>}
          </div>
        ))}
        {skills.length === 0 && (
          <div className="skills-rail-empty" onClick={onOpenPage}>
            <span>{skT("skills.empty")}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function readUiShellMode() {
  try {
    var params = new URLSearchParams(window.location.search || "");
    if (params.get("shell") === "legacy" || window.location.hash === "#legacy") return "legacy";
  } catch(e) {}
  return "workbench";
}

function App() {
  useDataVersion();
  const [shellMode, setShellMode] = useStateApp(readUiShellMode);
  const [themeMode, setThemeMode] = useStateApp(function () { return readStoredTweak("theme", "system"); });
  const [accent, setAccent] = useStateApp(function () { return readStoredTweak("accent", null); });
  const [systemTheme, setSystemTheme] = useStateApp(function () {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  const needsOnboarding = !!(DATA.onboarding && DATA.onboarding.needsOnboarding);
  const actualTheme = themeMode === "system" ? systemTheme : themeMode;

  useEffectApp(function () {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    function onChange(event) { setSystemTheme(event.matches ? "dark" : "light"); }
    mq.addEventListener("change", onChange);
    return function () { mq.removeEventListener("change", onChange); };
  }, []);

  useEffectApp(function () {
    try {
      localStorage.setItem("cyrene-tweak-theme", JSON.stringify(themeMode));
      localStorage.setItem("cyrene-theme-mode", themeMode);
    } catch(e) {}
    document.documentElement.dataset.theme = actualTheme;
    delete document.documentElement.dataset.booting;
  }, [themeMode, actualTheme]);

  // Apply the accent ("主题色") chosen in the new-UI settings overlay. The
  // workbench palette derives its tints from --accent, so this drives the whole
  // shell. Independent from the legacy shell's own tweak pipeline.
  useEffectApp(function () {
    // Only the workbench shell drives --accent here; the legacy shell runs its
    // own accent pipeline, so skip when it's showing to avoid fighting the var.
    // The workbench now renders during its own onboarding too, so drive accent
    // regardless of onboarding state (the legacy-shell guard still applies).
    if (shellMode === "legacy" || typeof window.WorkbenchApp === "undefined") return;
    const root = document.documentElement.style;
    const a = typeof accent === "string" ? accent.trim() : "";
    const m = a.match(/^#([0-9a-f]{6})$/i);
    if (m) {
      const r = parseInt(m[1].slice(0, 2), 16), g = parseInt(m[1].slice(2, 4), 16), b = parseInt(m[1].slice(4, 6), 16);
      root.setProperty("--accent", a);
      root.setProperty("--accent-faint", `rgba(${r},${g},${b},0.08)`);
      root.setProperty("--accent-dim", `rgba(${r},${g},${b},0.35)`);
      root.setProperty("--accent-text", (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.55 ? "#0d1612" : "#ffffff");
    } else {
      root.removeProperty("--accent");
      root.removeProperty("--accent-faint");
      root.removeProperty("--accent-dim");
      root.removeProperty("--accent-text");
    }
  }, [accent, actualTheme, shellMode, needsOnboarding]);

  // Live-sync theme + accent when changed from the settings overlay (it writes
  // localStorage and dispatches cyrene-tweak-*-change events).
  useEffectApp(function () {
    function onTheme() { setThemeMode(readStoredTweak("theme", "system")); }
    function onAccent() { setAccent(readStoredTweak("accent", null)); }
    window.addEventListener("cyrene-tweak-theme-change", onTheme);
    window.addEventListener("cyrene-tweak-accent-change", onAccent);
    return function () {
      window.removeEventListener("cyrene-tweak-theme-change", onTheme);
      window.removeEventListener("cyrene-tweak-accent-change", onAccent);
    };
  }, []);

  function toggleWorkbenchTheme() {
    const order = ["system", "light", "dark"];
    const idx = order.indexOf(themeMode);
    setThemeMode(order[(idx + 1) % order.length]);
  }

  // The legacy shell keeps its own SetupWizard (see LegacyAppShell). The
  // workbench shell handles onboarding itself — its LLM + personality setup
  // lives in workbench-webui/, fully independent of the legacy SetupWizard — so
  // we no longer divert to the legacy shell just because onboarding is pending.
  if (shellMode === "legacy" || typeof window.WorkbenchApp === "undefined") {
    return <LegacyAppShell />;
  }

  return <window.WorkbenchApp theme={themeMode} actualTheme={actualTheme} onToggleTheme={toggleWorkbenchTheme} needsOnboarding={needsOnboarding} />;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
