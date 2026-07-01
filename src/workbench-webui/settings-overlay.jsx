// Workbench Settings Overlay — floating panel (like search)
var {
  useState: useStateSt,
  useEffect: useEffectSt,
  useRef: useRefSt,
  useMemo: useMemoSt,
} = React;

var REPO_URL = "https://github.com/ikerrrrrrrrrrr/Cyrene";
var REPO_ISSUES_URL = REPO_URL + "/issues/new";
var REPO_DOCS_URL = REPO_URL + "/tree/main/docs";
var DEFAULT_MODEL_BASE_URL = "https://api.deepseek.com/v1";

function readTweak(key, fallback) {
  try { var v = localStorage.getItem("cyrene-tweak-" + key); return v !== null ? JSON.parse(v) : fallback; } catch (e) { return fallback; }
}

function readCapability(key, fallback) {
  try {
    var v = localStorage.getItem("cyrene-tweak-cap-" + key);
    return v !== null ? JSON.parse(v) : fallback;
  } catch (e) {
    return fallback;
  }
}

function createEmptyModel() {
  return {
    id: "candidate-" + Date.now() + "-" + Math.random().toString(16).slice(2, 6),
    name: "", model: "", desc: "", ctx: "", price: "", priceHint: "", api_key: "", base_url: DEFAULT_MODEL_BASE_URL,
  };
}

function normalizeModel(raw, idx, fbBaseUrl, fbKey) {
  var m = String(raw && (raw.model || raw.name || raw.id) || "").trim();
  return {
    id: String(raw && raw.id || "candidate-" + (idx + 1)).trim() || "candidate-" + (idx + 1),
    name: m, model: m,
    desc: String(raw && raw.desc || "").trim(),
    ctx: String(raw && raw.ctx || "").trim(),
    price: String(raw && raw.price || "").trim(),
    priceHint: String(raw && raw.priceHint || "").trim(),
    api_key: String(raw && raw.api_key || fbKey || "").trim(),
    base_url: String(raw && raw.base_url || fbBaseUrl || DEFAULT_MODEL_BASE_URL).trim() || DEFAULT_MODEL_BASE_URL,
  };
}

async function readSettingsResponse(response) {
  var payload = {};
  try {
    payload = await response.json();
  } catch (e) {
    if (response.ok) throw new Error("Invalid JSON response");
  }
  if (!response.ok) {
    throw new Error(
      String(payload.detail || payload.error || ("HTTP " + response.status))
    );
  }
  return payload;
}

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderSettingsMarkdown(value) {
  var source = String(value == null ? "" : value);
  try {
    var raw = window.marked ? window.marked.parse(source) : escapeHtml(source).replace(/\n/g, "<br>");
    return window.DOMPurify
      ? window.DOMPurify.sanitize(raw, { ADD_ATTR: ["data-line", "data-language"] })
      : raw;
  } catch (e) {
    return escapeHtml(source).replace(/\n/g, "<br>");
  }
}

// ── Tab definitions ──
var TABS = [
  { id: "general", labelKey: "settings.general" },
  { id: "models", labelKey: "settings.models" },
  { id: "channels", labelKey: "settings.channels" },
  { id: "agents", labelKey: "settings.agents" },
  { id: "appearance", labelKey: "settings.appearance" },
  { id: "capabilities", labelKey: "settings.capabilities" },
  { id: "skills", labelKey: "settings.skills" },
  { id: "shortcuts", labelKey: "settings.shortcuts" },
  { id: "data", labelKey: "settings.data" },
  { id: "about", labelKey: "settings.about" },
];

var SETTINGS_TAB_GROUPS = [
  ["general", "appearance", "shortcuts"],
  ["models", "capabilities", "skills"],
  ["channels", "agents"],
  ["data"],
  ["about"],
];

var TABS_BY_ID = TABS.reduce(function (acc, item) {
  acc[item.id] = item;
  return acc;
}, {});

function SettingsTabIcon(id) {
  var common = { width: "18", height: "18", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" };
  var paths = {
    general: [
      React.createElement("path", { key: "p1", d: "M12 15.5A3.5 3.5 0 1 0 12 8.5a3.5 3.5 0 0 0 0 7Z" }),
      React.createElement("path", { key: "p2", d: "M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .92V20a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-.92 1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.92-1H3.5a2 2 0 1 1 0-4h.18a1.7 1.7 0 0 0 .92-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.92V3.5a2 2 0 1 1 4 0v.18a1.7 1.7 0 0 0 1 .92 1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9c.34.16.66.47.92 1h.18a2 2 0 1 1 0 4h-.18c-.26.53-.58.84-.92 1Z" }),
    ],
    models: [React.createElement("path", { key: "p", d: "M12 3 4 7v10l8 4 8-4V7l-8-4Z" }), React.createElement("path", { key: "p2", d: "M4 7l8 4 8-4M12 11v10" })],
    channels: [React.createElement("path", { key: "p", d: "M21 8a6 6 0 0 1-8.7 5.3L8 16l1.1-4.8A6 6 0 1 1 21 8Z" }), React.createElement("path", { key: "p2", d: "M7.5 12.5A5 5 0 0 0 3 17.5L2 22l4.5-1A5 5 0 0 0 14 17" })],
    agents: [React.createElement("path", { key: "p", d: "M12 3v4M6 8h12a2 2 0 0 1 2 2v7a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-7a2 2 0 0 1 2-2Z" }), React.createElement("path", { key: "p2", d: "M9 14h.01M15 14h.01M8 20v2M16 20v2" })],
    appearance: [React.createElement("path", { key: "p", d: "M12 3a9 9 0 1 0 9 9 4 4 0 0 1-4 4h-1.2a2 2 0 0 1-1.5-3.3l.7-.8A5 5 0 0 0 12 3Z" }), React.createElement("circle", { key: "c1", cx: "7.5", cy: "10.5", r: ".8" }), React.createElement("circle", { key: "c2", cx: "10", cy: "7.5", r: ".8" }), React.createElement("circle", { key: "c3", cx: "14", cy: "7.5", r: ".8" })],
    capabilities: [React.createElement("path", { key: "p", d: "M7 7h10M7 17h10M9 7a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM19 17a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z" })],
    skills: [React.createElement("path", { key: "p", d: "M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-4Z" }), React.createElement("path", { key: "p2", d: "m9 12 2 2 4-5" })],
    shortcuts: [React.createElement("rect", { key: "r", x: "3", y: "5", width: "18", height: "14", rx: "2" }), React.createElement("path", { key: "p", d: "M7 9h.01M11 9h.01M15 9h.01M7 13h10" })],
    data: [React.createElement("path", { key: "p", d: "M4 6c0-2 3.6-3 8-3s8 1 8 3-3.6 3-8 3-8-1-8-3Z" }), React.createElement("path", { key: "p2", d: "M4 6v6c0 2 3.6 3 8 3s8-1 8-3V6M4 12v6c0 2 3.6 3 8 3s8-1 8-3v-6" })],
    about: [React.createElement("circle", { key: "c", cx: "12", cy: "12", r: "9" }), React.createElement("path", { key: "p", d: "M12 11v5M12 8h.01" })],
  };
  return React.createElement("svg", common, paths[id] || paths.general);
}

function ExternalChevron() {
  return React.createElement("svg", { width: "18", height: "18", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.4", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
    React.createElement("path", { d: "m9 18 6-6-6-6" })
  );
}

function AboutRelatedIcon(name) {
  if (name === "github") {
    return React.createElement("svg", { width: "22", height: "22", viewBox: "0 0 16 16", fill: "currentColor", "aria-hidden": "true" },
      React.createElement("path", { d: "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z" })
    );
  }
  var path = name === "issue" ? "M12 8v4M12 16h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
    : name === "changelog" ? "M4 19V5M4 19h16M4 19l4-4M8 15V7M8 15h12M12 11V3M12 11h8"
    : name === "website" ? "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM3.6 9h16.8M3.6 15h16.8M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"
    : "M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15Z";
  return React.createElement("svg", { width: "23", height: "23", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
    React.createElement("path", { d: path })
  );
}

// ── Settings Overlay ──
function SettingsOverlay({
  onClose,
  initialTab,
  theme: initialTheme,
  actualTheme,
  onToggleTheme,
}) {
  var { t, lang, setLang } = useWorkbenchI18n();
  var [tab, setTab] = useStateSt(initialTab || "general");

  // ── General state ──
  var [desktopNotifications, setDesktopNotifications] = useStateSt(function () {
    try { return localStorage.getItem("cyrene-desktop-notifications") === "1"; } catch (e) { return false; }
  });
  var [mapProvider, setMapProvider] = useStateSt(function () {
    try { return localStorage.getItem("cyrene-tweak-map-provider") || "direct"; } catch (e) { return "direct"; }
  });
  var [amapKey, setAmapKey] = useStateSt("");
  var [amapKeySaved, setAmapKeySaved] = useStateSt("");

  // ── Models state ──
  var [models, setModels] = useStateSt(function () { return [createEmptyModel()]; });
  var [draftModel, setDraftModel] = useStateSt(createEmptyModel());
  var [visionModels, setVisionModels] = useStateSt(function () { return [createEmptyModel()]; });
  var [draftVision, setDraftVision] = useStateSt(createEmptyModel());
  var [secondaryModel, setSecondaryModel] = useStateSt(null);
  var [modelsSaved, setModelsSaved] = useStateSt("");

  // ── Config state ──
  var [config, setConfig] = useStateSt({
    model: "—", base_url: "—", assistant_name: "—",
    base_dir: "—", data_dir: "—", soul_path: "—",
    workspace_dir: "—", soul_content: "", spawn_policy: "conservative",
    heartbeat_interval: 1800, max_tool_rounds: 15,
    search_port: "8888",
    auto_update: true,
  });
  var [configLoading, setConfigLoading] = useStateSt(true);
  var [soulDraft, setSoulDraft] = useStateSt("");
  var [soulStatus, setSoulStatus] = useStateSt("");
  var [agentProactive, setAgentProactive] = useStateSt(true);

  // ── Channels state ──
  var [telegramToken, setTelegramToken] = useStateSt("");
  var [telegramSaved, setTelegramSaved] = useStateSt("");
  var [notifyTelegram, setNotifyTelegram] = useStateSt(true);
  var [notifyWechat, setNotifyWechat] = useStateSt(true);

  // ── Capabilities state ──
  var [browserTools, setBrowserTools] = useStateSt(function () { return readCapability("browserTools", true); });
  var [redactSecrets, setRedactSecrets] = useStateSt(function () { return readCapability("redactSecrets", true); });
  var [mcpConfigs, setMcpConfigs] = useStateSt([]);
  var [mcpServers, setMcpServers] = useStateSt([]);
  var [mcpSaved, setMcpSaved] = useStateSt("");
  var [newMcpServer, setNewMcpServer] = useStateSt({ name: "", transport: "stdio", command: "", args: "", url: "", enabled: true });
  var [toolList, setToolList] = useStateSt([]);
  var [toolsExpanded, setToolsExpanded] = useStateSt(false);
  var [toolsSaved, setToolsSaved] = useStateSt("");

  // ── Data state ──
  var [resetStatus, setResetStatus] = useStateSt("");
  var [resetting, setResetting] = useStateSt(false);
  var [backupList, setBackupList] = useStateSt([]);
  var [backupMsg, setBackupMsg] = useStateSt("");
  var [exportSid, setExportSid] = useStateSt("");
  var [exportFmt, setExportFmt] = useStateSt("markdown");
  var [exportMsg, setExportMsg] = useStateSt("");

  // ── Tweak helpers ──
  var tweaks = {
    theme: initialTheme,
    accent: readTweak("accent", null),
    textSize: readTweak("textSize", "default"),
    density: readTweak("density", "cozy"),
    animatePulse: readTweak("animatePulse", true),
  };

  function setTweak(key, val) {
    try { localStorage.setItem("cyrene-tweak-" + key, JSON.stringify(val)); } catch (e) {}
    if (key === "density") document.documentElement.dataset.density = val;
    if (key === "textSize") document.documentElement.dataset.textSize = val || "default";
    if (key === "animatePulse") document.documentElement.dataset.animPulse = val ? "on" : "off";
    window.dispatchEvent(new Event("cyrene-tweak-" + key + "-change"));
  }

  function setCapability(key, val) {
    try { localStorage.setItem("cyrene-tweak-cap-" + key, JSON.stringify(val)); } catch (e) {}
  }

  // ── Keyboard: Escape to close ──
  useEffectSt(function () {
    function onKeyDown(e) {
      if (e.key === "Escape") { e.preventDefault(); onClose && onClose(); }
    }
    window.addEventListener("keydown", onKeyDown);
    return function () { window.removeEventListener("keydown", onKeyDown); };
  }, [onClose]);

  // Persist desktop notifications
  useEffectSt(function () {
    try { localStorage.setItem("cyrene-desktop-notifications", desktopNotifications ? "1" : "0"); } catch (e) {}
  }, [desktopNotifications]);

  // Load settings
  useEffectSt(function () {
    document.documentElement.dataset.density = tweaks.density;
    document.documentElement.dataset.textSize = tweaks.textSize || "default";
    document.documentElement.dataset.animPulse = tweaks.animatePulse ? "on" : "off";

    setConfigLoading(true);
    fetch("/api/settings/config").then(function (r) { return r.ok ? r.json() : Promise.reject("HTTP " + r.status); })
      .then(function (p) {
        setConfig(p);
        setSoulDraft(p.soul_content || "");
        if (p.notify_telegram !== undefined) setNotifyTelegram(p.notify_telegram);
        if (p.notify_wechat !== undefined) setNotifyWechat(p.notify_wechat);
        if (p.redact_secrets !== undefined) setRedactSecrets(!!p.redact_secrets);
        if (p.agent_proactive !== undefined) setAgentProactive(p.agent_proactive);
        setConfigLoading(false);
      }).catch(function () { setConfigLoading(false); });

    fetch("/api/settings/models").then(readSettingsResponse).then(function (p) {
      var fb = p.base_url || DEFAULT_MODEL_BASE_URL;
      var norm = function (raw, i) { return normalizeModel(raw, i, fb, ""); };
      var ms = (p.models || p.primary_candidates || []).map(norm);
      var vs = (p.vision_models || p.vision_candidates || []).map(norm);
      if (!ms.length) ms = [norm({}, 0)];
      if (!vs.length) vs = [norm({}, 0)];
      setModels(ms);
      setVisionModels(vs);
      setSecondaryModel({
        id: "secondary", model: (p.secondary_model && p.secondary_model.model) || "",
        api_key: (p.secondary_model && p.secondary_model.api_key) || "",
        base_url: (p.secondary_model && p.secondary_model.base_url) || fb,
        name: (p.secondary_model && (p.secondary_model.name || p.secondary_model.model)) || "",
        ctx_limit: (p.secondary_model && Number(p.secondary_model.ctx_limit)) || 0,
        max_concurrency: (p.secondary_model && Number(p.secondary_model.max_concurrency)) || 0,
      });
    }).catch(function (e) {
      setModelsSaved(t("settings.error") + ": " + (e.message || ""));
    });

    fetch("/api/settings/tools").then(function (r) { return r.json(); }).then(function (p) {
      var tools = p.tools || [];
      var browserToolNames = ["browser_navigate", "browser_snapshot", "browser_screenshot", "browser_click", "browser_click_ref", "browser_click_text", "browser_click_at", "browser_type", "browser_type_ref", "browser_wait", "browser_network_log", "browser_tab_list", "browser_tab_new", "browser_tab_select", "browser_tab_close", "browser_scroll", "browser_request_takeover"];
      setToolList(tools);
      if (tools.length) {
        var browserToolsList = tools.filter(function (tool) { return browserToolNames.indexOf(tool.name) >= 0; });
        if (browserToolsList.length) setBrowserTools(browserToolsList.every(function (tool) { return tool.enabled !== false; }));
      }
    }).catch(function () {});
    fetch("/api/settings/mcp").then(function (r) { return r.json(); }).then(function (p) { setMcpServers(p.servers || []); setMcpConfigs(p.configs || []); }).catch(function () {});
    fetch("/api/settings/keys").then(function (r) { return r.json(); }).then(function (p) {
      var tk = (p.keys || []).find(function (item) { return item.key === "TELEGRAM_BOT_TOKEN"; });
      if (tk) setTelegramToken(tk.value || "");
      var ak = (p.keys || []).find(function (item) { return item.key === "AMAP_API_KEY"; });
      if (ak) setAmapKey(ak.value || "");
    }).catch(function () {});

    fetch("/api/backup/list").then(function (r) { return r.json(); }).then(function (d) { if (d.ok) setBackupList(d.backups || []); }).catch(function () {});
  }, []);

  function saveSoul() {
    setSoulStatus(t("settings.saving"));
    fetch("/api/settings/soul", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: soulDraft }) })
      .then(function (r) { return r.ok ? setSoulStatus(t("settings.saved")) : Promise.reject(); })
      .catch(function () { setSoulStatus(t("settings.error")); });
    setTimeout(function () { setSoulStatus(""); }, 1500);
  }

  function saveModels() {
    var norm = models.map(function (m, i) { return normalizeModel(m, i, config.base_url || DEFAULT_MODEL_BASE_URL, ""); }).filter(function (m) { return m.model; });
    var vNorm = visionModels.map(function (m, i) { return normalizeModel(m, i, config.base_url || DEFAULT_MODEL_BASE_URL, ""); }).filter(function (m) { return m.model; });
    if (!norm.length || !vNorm.length) { setModelsSaved(t("settings.modelCandidateRequired")); return; }
    setModelsSaved(t("settings.saving"));
    fetch("/api/settings/models", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        models: norm, vision_models: vNorm,
        secondary_model: secondaryModel ? {
          model: secondaryModel.model, name: secondaryModel.name,
          api_key: secondaryModel.api_key, base_url: secondaryModel.base_url,
          ctx_limit: Number(secondaryModel.ctx_limit) || 0,
          max_concurrency: Number(secondaryModel.max_concurrency) || 0,
        } : null,
      }),
    }).then(readSettingsResponse).then(function (p) {
      var fb = p.base_url || config.base_url || DEFAULT_MODEL_BASE_URL;
      setModels(((p.models || p.primary_candidates || norm)).map(function (m, i) { return normalizeModel(m, i, fb, ""); }));
      setVisionModels(((p.vision_models || p.vision_candidates || vNorm)).map(function (m, i) { return normalizeModel(m, i, fb, ""); }));
      setSecondaryModel({
        id: "secondary", model: (p.secondary_model && p.secondary_model.model) || "",
        api_key: (p.secondary_model && p.secondary_model.api_key) || "",
        base_url: (p.secondary_model && p.secondary_model.base_url) || fb,
        name: (p.secondary_model && (p.secondary_model.name || p.secondary_model.model)) || "",
        ctx_limit: (p.secondary_model && Number(p.secondary_model.ctx_limit)) || 0,
        max_concurrency: (p.secondary_model && Number(p.secondary_model.max_concurrency)) || 0,
      });
      setConfig(function (previous) {
        return {
          ...previous,
          model: p.active_model_name || previous.model,
          base_url: p.base_url || previous.base_url,
        };
      });
      setModelsSaved(t("settings.saved"));
      setTimeout(function () { setModelsSaved(""); }, 1500);
    }).catch(function (e) {
      setModelsSaved(t("settings.error") + ": " + (e.message || ""));
    });
  }

  function saveTools() {
    setToolsSaved(t("settings.saving"));
    var map = {};
    toolList.forEach(function (t) { map[t.name] = t.enabled; });
    fetch("/api/settings/tools", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tools: map }) })
      .then(function (r) { return r.ok ? (setToolsSaved(t("settings.saved")), setTimeout(function () { setToolsSaved(""); }, 1500)) : Promise.reject(); })
      .catch(function () { setToolsSaved(t("settings.error")); });
  }

  function saveAgents() {
    fetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        spawn_policy: config.spawn_policy || "conservative",
        heartbeat_interval: Number(config.heartbeat_interval) || 1800,
        agent_proactive: agentProactive,
        max_tool_rounds: Number(config.max_tool_rounds) || 15,
      }),
    }).catch(function () {});
  }

  function saveBrowserTools(nextEnabled) {
    var browserToolNames = ["browser_navigate", "browser_snapshot", "browser_screenshot", "browser_click", "browser_click_ref", "browser_click_text", "browser_click_at", "browser_type", "browser_type_ref", "browser_wait", "browser_network_log", "browser_tab_list", "browser_tab_new", "browser_tab_select", "browser_tab_close", "browser_scroll", "browser_request_takeover"];
    var nextToolList = toolList.map(function (tool) {
      return browserToolNames.indexOf(tool.name) >= 0 ? { ...tool, enabled: nextEnabled } : tool;
    });
    var map = {};
    nextToolList.forEach(function (tool) { map[tool.name] = tool.enabled; });
    setBrowserTools(nextEnabled);
    setToolList(nextToolList);
    fetch("/api/settings/tools", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tools: map }) }).catch(function () {});
  }

  function saveRedactSecrets(nextEnabled) {
    setRedactSecrets(nextEnabled);
    setCapability("redactSecrets", nextEnabled);
    fetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ redact_secrets: nextEnabled }) }).catch(function () {});
  }

  function saveMcp() {
    setMcpSaved(t("settings.saving"));
    fetch("/api/settings/mcp", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ servers: mcpConfigs }) })
      .then(function () {
        setMcpSaved(t("settings.saved"));
        setTimeout(function () { setMcpSaved(""); }, 1500);
        fetch("/api/settings/mcp").then(function (r) { return r.json(); }).then(function (p) { setMcpServers(p.servers || []); setMcpConfigs(p.configs || []); }).catch(function () {});
      }).catch(function () { setMcpSaved(t("settings.error")); });
  }

  function toggleDesktopNotifications() {
    if (typeof Notification === "undefined") return;
    if (desktopNotifications) { setDesktopNotifications(false); return; }
    if (Notification.permission === "granted") { setDesktopNotifications(true); return; }
    if (Notification.permission !== "denied") { Notification.requestPermission().then(function (p) { setDesktopNotifications(p === "granted"); }); }
  }

  function loadBackups() {
    fetch("/api/backup/list").then(function (r) { return r.json(); }).then(function (d) { if (d.ok) setBackupList(d.backups || []); }).catch(function () {});
  }

  function formatBytes(n) { n = Number(n || 0); if (n < 1024) return n + " B"; if (n < 1048576) return (n / 1024).toFixed(1) + " KB"; return (n / 1048576).toFixed(1) + " MB"; }
  function formatDate(iso) { if (!iso) return "—"; try { return new Date(iso).toLocaleString(); } catch (e) { return iso; } }

  // ── Render helpers ──
  function onChange(key, stateFn) { return function (e) { stateFn(e.target.value); }; }

  return React.createElement("div", {
    className: "settings-overlay",
    onClick: function (e) { if (e.target === e.currentTarget) onClose && onClose(); },
  },
    React.createElement("div", { className: "settings-overlay-panel", onClick: function (e) { e.stopPropagation(); } },
      // Header
      React.createElement("div", { className: "settings-overlay-header" },
        React.createElement("span", { className: "settings-overlay-icon" }, SettingsTabIcon("general")),
        React.createElement("strong", null, t("nav.settings")),
        React.createElement("button", { className: "settings-overlay-close", onClick: onClose }, "ESC"),
      ),

      // Body: sidebar + content
      React.createElement("div", { className: "settings-overlay-body" },
        // Sidebar tabs
        React.createElement("div", { className: "settings-overlay-nav" },
          SETTINGS_TAB_GROUPS.map(function (ids, groupIndex) {
            return React.createElement("div", { key: ids.join("-"), className: "settings-overlay-nav-section" + (groupIndex === 0 ? " first" : "") },
              ids.map(function (id) {
                var item = TABS_BY_ID[id];
                if (!item) return null;
                return React.createElement("button", {
                  key: item.id,
                  className: "settings-overlay-tab" + (tab === item.id ? " active" : ""),
                  onClick: function () { setTab(item.id); },
                },
                  React.createElement("span", { className: "settings-overlay-tab-icon" }, SettingsTabIcon(item.id)),
                  React.createElement("span", null, t(item.labelKey)),
                );
              }),
            );
          }),
        ),

        // Content area
        React.createElement("div", { className: "settings-overlay-content" },
          tab === "general" && React.createElement(GeneralPanel, { t, lang, setLang, desktopNotifications, toggleDesktopNotifications, mapProvider, setMapProvider, amapKey, setAmapKey, amapKeySaved, setAmapKeySaved }),
          tab === "models" && ModelsPanel({ t, models, setModels, draftModel, setDraftModel, visionModels, setVisionModels, draftVision, setDraftVision, secondaryModel, setSecondaryModel, modelsSaved, saveModels, config }),
          tab === "channels" && ChannelsPanel({ t, telegramToken, setTelegramToken, telegramSaved, setTelegramSaved, notifyTelegram, setNotifyTelegram, notifyWechat, setNotifyWechat }),
          tab === "agents" && AgentsPanel({ t, config, setConfig, configLoading, soulDraft, setSoulDraft, soulStatus, saveSoul, agentProactive, setAgentProactive, saveAgents }),
          tab === "appearance" && AppearancePanel({ t, tweaks, setTweak, actualTheme, theme: initialTheme }),
          tab === "capabilities" && CapabilitiesPanel({ t, browserTools, saveBrowserTools, mcpConfigs, setMcpConfigs, mcpServers, toolList, toolsExpanded, setToolsExpanded, toolsSaved, saveTools, newMcpServer, setNewMcpServer, mcpSaved, saveMcp, config }),
          tab === "skills" && React.createElement(SkillsPanel, { t }),
          tab === "shortcuts" && React.createElement(ShortcutsPanel, { t }),
          tab === "data" && DataPanel({ t, redactSecrets, saveRedactSecrets, config, configLoading, resetStatus, setResetStatus, resetting, setResetting, backupList, backupMsg, setBackupMsg, loadBackups, exportSid, setExportSid, exportFmt, setExportFmt, exportMsg, setExportMsg, formatBytes, formatDate }),
          tab === "about" && AboutPanel({ t, config }),
        ),
      ),
    ),
  );
}

// ── General Panel ──
function GeneralPanel(p) {
  var { t, lang, setLang, desktopNotifications, toggleDesktopNotifications, mapProvider, setMapProvider, amapKey, setAmapKey, amapKeySaved, setAmapKeySaved } = p;

  // Desktop-only (Electron) toggles. Quick chat depends on background residency,
  // so its toggle is gated on runInBackground.
  var supportsDesktop = !!(
    window.cyrene
    && typeof window.cyrene.getDesktopSettings === "function"
    && typeof window.cyrene.updateDesktopSettings === "function"
  );
  var [runInBackground, setRunInBackground] = useStateSt(false);
  var [quickChatEnabled, setQuickChatEnabled] = useStateSt(false);
  var [desktopBusy, setDesktopBusy] = useStateSt(false);
  var [desktopNotice, setDesktopNotice] = useStateSt("");

  useEffectSt(function () {
    if (!supportsDesktop) return undefined;
    var cancelled = false;
    window.cyrene.getDesktopSettings().then(function (s) {
      if (cancelled || !s) return;
      setRunInBackground(s.runInBackground === true);
      setQuickChatEnabled(s.quickChatEnabled === true);
      if ((s.language === "en" || s.language === "zh") && s.language !== lang) {
        setLang(s.language);
      } else if (!s.language) {
        window.cyrene.updateDesktopSettings({ language: lang }).catch(function () {});
      }
    }).catch(function () {});
    return function () { cancelled = true; };
  }, []);

  function applyDesktop(updates) {
    setDesktopBusy(true);
    setDesktopNotice("");
    window.cyrene.updateDesktopSettings(updates).then(function (s) {
      if (!s) return;
      setRunInBackground(s.runInBackground === true);
      setQuickChatEnabled(s.quickChatEnabled === true);
      if (s.shortcutUpdateOk === false) setDesktopNotice(t("settings.quickChatShortcutConflict"));
    }).catch(function () {
      setDesktopNotice(t("settings.error"));
    }).finally(function () { setDesktopBusy(false); });
  }

  function saveAmapKey() {
    if (!amapKey || amapKey.startsWith("••")) { setAmapKeySaved(t("settings.noChanges")); setTimeout(function () { setAmapKeySaved(""); }, 1500); return; }
    setAmapKeySaved(t("settings.saving"));
    fetch("/api/settings/keys", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ AMAP_API_KEY: amapKey }) })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function () {
        fetch("/api/amap/verify").then(function (r) { return r.json(); }).then(function (vd) {
          if (vd.valid) { setAmapKeySaved(t("settings.amapKeySaved")); localStorage.setItem("cyrene-tweak-map-provider", "amap"); }
          else { setAmapKeySaved(t("settings.amapKeyVerifyFail") + " " + (vd.error || "")); }
        }).catch(function () { setAmapKeySaved(t("settings.saved")); });
        setTimeout(function () { setAmapKeySaved(""); }, 3000);
      }).catch(function () { setAmapKeySaved(t("settings.error")); setTimeout(function () { setAmapKeySaved(""); }, 3000); });
  }

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.general")),
    FieldRow(t("settings.language"), t("settings.languageHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (lang === "en" ? " active" : ""), onClick: function () { setLang("en"); } }, "English"),
        React.createElement("button", { className: "wb-seg-btn" + (lang === "zh" ? " active" : ""), onClick: function () { setLang("zh"); } }, "中文"),
      ),
    ),
    FieldRow(t("settings.desktopNotifications"), t("settings.desktopNotificationsHint"),
      Toggle(desktopNotifications, toggleDesktopNotifications),
    ),
    FieldRow(t("settings.mapProvider"), t("settings.mapProviderHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (mapProvider === "direct" ? " active" : ""), onClick: function () { setMapProvider("direct"); localStorage.setItem("cyrene-tweak-map-provider", "direct"); } }, t("settings.mapProviderDirect")),
        React.createElement("button", { className: "wb-seg-btn" + (mapProvider === "amap" ? " active" : ""), onClick: function () { setMapProvider("amap"); } }, t("settings.mapProviderAmap")),
      ),
    ),
    mapProvider === "amap" && FieldRow(t("settings.amapKey"), t("settings.amapKeyHint"),
      React.createElement("div", { className: "wb-inline-row" },
        React.createElement("input", { className: "wb-input mono", type: "password", value: amapKey, onChange: function (e) { setAmapKey(e.target.value); }, placeholder: t("settings.amapKeyPlaceholder") }),
        React.createElement("button", { className: "wb-btn primary", onClick: saveAmapKey }, t("settings.save")),
      ),
      amapKeySaved && React.createElement("span", { className: "wb-hint saved" }, amapKeySaved),
    ),
    supportsDesktop && FieldRow(t("settings.runInBackground"), t("settings.runInBackgroundHint"),
      Toggle(runInBackground, function () { applyDesktop({ runInBackground: !runInBackground }); }, desktopBusy),
    ),
    supportsDesktop && FieldRow(t("settings.quickChatAssistant"),
      runInBackground ? t("settings.quickChatAssistantHint") : t("settings.quickChatAssistantNeedsResident"),
      Toggle(quickChatEnabled, function () { applyDesktop({ quickChatEnabled: !quickChatEnabled }); }, desktopBusy || !runInBackground),
    ),
    supportsDesktop && desktopNotice
      && React.createElement("div", { className: "wb-hint", style: { color: "var(--wb-error-text)" } }, desktopNotice),
  );
}

// ── Models Panel ──
function ModelsPanel(p) {
  var { t, models, setModels, draftModel, setDraftModel, visionModels, setVisionModels, draftVision, setDraftVision, secondaryModel, setSecondaryModel, modelsSaved, saveModels, config } = p;

  function updateModel(id, field, val) {
    setModels(models.map(function (m) {
      if (m.id !== id) return m;
      // Clear server-supplied priceHint when user changes the model identifier
      var extra = field === "model" ? { name: val, priceHint: "" } : {};
      return { ...m, [field]: val, ...extra };
    }));
  }
  function moveModel(id, dir) {
    var idx = models.findIndex(function (m) { return m.id === id; });
    var tgt = idx + dir;
    if (idx < 0 || tgt < 0 || tgt >= models.length) return;
    var next = models.slice();
    var cur = next[idx]; next[idx] = next[tgt]; next[tgt] = cur;
    setModels(next);
  }
  function deleteModel(id) { if (models.length > 1) setModels(models.filter(function (m) { return m.id !== id; })); }
  function addModel() { var c = normalizeModel(draftModel, models.length, "", ""); if (!c.model) return; setModels(models.concat(c)); setDraftModel(createEmptyModel()); }

  function updateVisionModel(id, field, val) {
    setVisionModels(visionModels.map(function (m) { return m.id === id ? { ...m, [field]: val, name: field === "model" ? val : m.name } : m; }));
  }
  function moveVisionModel(id, dir) {
    var idx = visionModels.findIndex(function (m) { return m.id === id; });
    var tgt = idx + dir;
    if (idx < 0 || tgt < 0 || tgt >= visionModels.length) return;
    var next = visionModels.slice();
    var cur = next[idx]; next[idx] = next[tgt]; next[tgt] = cur;
    setVisionModels(next);
  }
  function deleteVisionModel(id) { if (visionModels.length > 1) setVisionModels(visionModels.filter(function (m) { return m.id !== id; })); }
  function addVisionModel() { var c = normalizeModel(draftVision, visionModels.length, "", ""); if (!c.model) return; setVisionModels(visionModels.concat(c)); setDraftVision(createEmptyModel()); }

  function updateSecondary(field, val) { setSecondaryModel(function (prev) { return prev ? { ...prev, [field]: val, name: field === "model" ? val : prev.name } : prev; }); }

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.models"), t("settings.modelsSubtitle")),

    // Primary model
    SectionBlock(t("settings.primaryModelSlot"), null,
      models[0] && ModelCard([
        ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: models[0].model, onChange: function (e) { updateModel(models[0].id, "model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
        ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: models[0].api_key, onChange: function (e) { updateModel(models[0].id, "api_key", e.target.value); }, placeholder: "sk-..." })),
        ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: models[0].base_url, onChange: function (e) { updateModel(models[0].id, "base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
        React.createElement("div", { className: "wb-model-meta" },
          React.createElement("div", null, React.createElement("small", null, t("settings.descriptionLabel")), React.createElement("input", { className: "wb-input mono small", value: models[0].desc, onChange: function (e) { updateModel(models[0].id, "desc", e.target.value); }, placeholder: t("settings.placeholderDesc") })),
          React.createElement("div", null, React.createElement("small", null, t("settings.contextLabel")), React.createElement("input", { className: "wb-input mono small", value: models[0].ctx, onChange: function (e) { updateModel(models[0].id, "ctx", e.target.value); }, placeholder: t("settings.placeholderCtx") })),
          React.createElement("div", null, React.createElement("small", null, t("settings.priceLabel")), React.createElement("input", { className: "wb-input mono small", value: models[0].price, onChange: function (e) { updateModel(models[0].id, "price", e.target.value); }, placeholder: models[0].priceHint || t("settings.placeholderPrice") })),
        ),
      ]),
    ),

    // Fallback candidates
    SectionBlock(t("settings.fallbackCandidates"), React.createElement("button", { className: "wb-btn", onClick: addModel }, t("settings.addFallbackCandidate")),
      models.slice(1).map(function (m) {
        return ModelCard([
          React.createElement("div", { className: "wb-model-actions" },
            React.createElement("div", { className: "wb-sort-group" },
              React.createElement("button", { className: "wb-sort-btn", title: t("common.moveUp"), onClick: function () { moveModel(m.id, -1); } },
                React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                  React.createElement("polyline", { points: "18 15 12 9 6 15" })
                )
              ),
              React.createElement("button", { className: "wb-sort-btn", title: t("common.moveDown"), onClick: function () { moveModel(m.id, 1); } },
                React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                  React.createElement("polyline", { points: "6 9 12 15 18 9" })
                )
              ),
            ),
            React.createElement("button", { className: "wb-delete-btn", title: t("common.remove"), onClick: function () { deleteModel(m.id); } },
              React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                React.createElement("line", { x1: "18", y1: "6", x2: "6", y2: "18" }),
                React.createElement("line", { x1: "6", y1: "6", x2: "18", y2: "18" })
              )
            ),
          ),
          ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: m.model, onChange: function (e) { updateModel(m.id, "model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
          ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: m.api_key, onChange: function (e) { updateModel(m.id, "api_key", e.target.value); }, placeholder: "sk-..." })),
          ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: m.base_url, onChange: function (e) { updateModel(m.id, "base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
        ], m.id);
      }),
    ),
    modelDraftField(draftModel, setDraftModel, addModel, t),

    SectionBlock(t("settings.secondaryModelSlot"), t("settings.secondaryModelHint"),
      secondaryModel && ModelCard([
        ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: secondaryModel.model, onChange: function (e) { updateSecondary("model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
        ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: secondaryModel.api_key, onChange: function (e) { updateSecondary("api_key", e.target.value); }, placeholder: "sk-..." })),
        ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: secondaryModel.base_url, onChange: function (e) { updateSecondary("base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
        React.createElement("div", { className: "wb-model-meta" },
          React.createElement("div", null, React.createElement("small", null, t("settings.secondaryModelCtxLimit")), React.createElement("input", { className: "wb-input mono small", type: "number", min: "0", value: secondaryModel.ctx_limit, onChange: function (e) { updateSecondary("ctx_limit", e.target.value); }, placeholder: "0" })),
          React.createElement("div", null, React.createElement("small", null, t("settings.secondaryModelConcurrency")), React.createElement("input", { className: "wb-input mono small", type: "number", min: "0", value: secondaryModel.max_concurrency, onChange: function (e) { updateSecondary("max_concurrency", e.target.value); }, placeholder: "0" })),
        ),
      ]),
    ),

    // Vision model
    SectionBlock(t("settings.visionModelSlot"), null,
      visionModels[0] && ModelCard([
        ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: visionModels[0].model, onChange: function (e) { updateVisionModel(visionModels[0].id, "model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
        ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: visionModels[0].api_key, onChange: function (e) { updateVisionModel(visionModels[0].id, "api_key", e.target.value); }, placeholder: "sk-..." })),
        ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: visionModels[0].base_url, onChange: function (e) { updateVisionModel(visionModels[0].id, "base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
      ]),
      visionModels.slice(1).map(function (m) {
        return ModelCard([
          React.createElement("div", { className: "wb-model-actions" },
            React.createElement("div", { className: "wb-sort-group" },
              React.createElement("button", { className: "wb-sort-btn", title: t("common.moveUp"), onClick: function () { moveVisionModel(m.id, -1); } },
                React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                  React.createElement("polyline", { points: "18 15 12 9 6 15" })
                )
              ),
              React.createElement("button", { className: "wb-sort-btn", title: t("common.moveDown"), onClick: function () { moveVisionModel(m.id, 1); } },
                React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                  React.createElement("polyline", { points: "6 9 12 15 18 9" })
                )
              ),
            ),
            React.createElement("button", { className: "wb-delete-btn", title: t("common.remove"), onClick: function () { deleteVisionModel(m.id); } },
              React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                React.createElement("line", { x1: "18", y1: "6", x2: "6", y2: "18" }),
                React.createElement("line", { x1: "6", y1: "6", x2: "18", y2: "18" })
              )
            ),
          ),
          ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: m.model, onChange: function (e) { updateVisionModel(m.id, "model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
          ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: m.api_key, onChange: function (e) { updateVisionModel(m.id, "api_key", e.target.value); }, placeholder: "sk-..." })),
          ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: m.base_url, onChange: function (e) { updateVisionModel(m.id, "base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
        ], m.id);
      }),
    ),
    modelDraftField(draftVision, setDraftVision, addVisionModel, t),

    React.createElement("div", { className: "wb-save-actions" },
      React.createElement("button", { className: "wb-btn primary", onClick: saveModels }, t("settings.saveApply")),
      modelsSaved && React.createElement("span", { className: "wb-hint saved" }, modelsSaved),
    ),
  );
}

function modelDraftField(draft, setDraft, onAdd, t) {
  return React.createElement("div", { className: "wb-model-draft" },
    React.createElement("input", { className: "wb-input mono", value: draft.model, onChange: function (e) { setDraft({ ...draft, model: e.target.value, name: e.target.value }); }, placeholder: t("settings.placeholderModelIdentifier") }),
    React.createElement("input", { className: "wb-input mono", type: "password", value: draft.api_key, onChange: function (e) { setDraft({ ...draft, api_key: e.target.value }); }, placeholder: "sk-..." }),
    React.createElement("input", { className: "wb-input mono", value: draft.base_url, onChange: function (e) { setDraft({ ...draft, base_url: e.target.value }); }, placeholder: DEFAULT_MODEL_BASE_URL }),
    React.createElement("button", { className: "wb-btn", onClick: onAdd }, t("settings.add")),
  );
}

// ── Channels Panel ──
function ChannelsPanel(p) {
  var { t, telegramToken, setTelegramToken, telegramSaved, setTelegramSaved, notifyTelegram, setNotifyTelegram, notifyWechat, setNotifyWechat } = p;

  function saveTelegram() {
    if (!telegramToken || telegramToken.startsWith("••")) { setTelegramSaved(t("settings.noChanges")); setTimeout(function () { setTelegramSaved(""); }, 1500); return; }
    setTelegramSaved(t("settings.saving"));
    fetch("/api/settings/keys", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ TELEGRAM_BOT_TOKEN: telegramToken }) })
      .then(function () { setTelegramSaved(t("settings.saved")); setTimeout(function () { setTelegramSaved(""); }, 1500); })
      .catch(function () { setTelegramSaved(t("settings.error")); });
  }

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.channels"), t("settings.channelsSubtitle")),

    React.createElement("div", { className: "wb-channel-card" },
      React.createElement("div", { className: "wb-channel-head" },
        React.createElement("span", { className: "wb-channel-icon" }, "⌖"),
        React.createElement("b", null, t("settings.telegram")),
      ),
      React.createElement("p", { className: "wb-channel-desc" }, t("settings.telegramTokenHint")),
      FieldRow(t("settings.telegramToken"), null,
        React.createElement("div", { className: "wb-inline-row" },
          React.createElement("input", { className: "wb-input mono", type: "password", value: telegramToken, onChange: function (e) { setTelegramToken(e.target.value); }, placeholder: t("settings.placeholderOptional") }),
          React.createElement("button", { className: "wb-btn primary", onClick: saveTelegram }, t("settings.saveNotification")),
        ),
        telegramSaved && React.createElement("span", { className: "wb-hint saved" }, telegramSaved),
      ),
      FieldRow(t("settings.notifyTelegram"), t("settings.notifyTelegramHint"),
        Toggle(notifyTelegram, function () {
          var next = !notifyTelegram;
          setNotifyTelegram(next);
          fetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notify_telegram: next }) }).catch(function () {});
        }),
      ),
    ),

    React.createElement(WeChatConnectionPanel, { t, notifyWechat, setNotifyWechat }),
  );
}

function WeChatConnectionPanel(p) {
  var { t, notifyWechat, setNotifyWechat } = p;
  var [connected, setConnected] = useStateSt(false);
  var [running, setRunning] = useStateSt(false);
  var [ownerWxid, setOwnerWxid] = useStateSt("");
  var [qrCode, setQrCode] = useStateSt("");
  var [qrStatus, setQrStatus] = useStateSt("");
  var [busy, setBusy] = useStateSt(false);
  var cancelledRef = useRefSt(false);
  var pollAbortRef = useRefSt(null);

  function refreshStatus() {
    return fetch("/api/wechat/status")
      .then(readSettingsResponse)
      .then(function (status) {
        setConnected(!!status.connected);
        setRunning(!!status.running);
        setOwnerWxid(status.owner_wxid || "");
        return status;
      });
  }

  useEffectSt(function () {
    cancelledRef.current = false;
    refreshStatus().catch(function () {
      if (!cancelledRef.current) setQrStatus(t("settings.wechatStatusFailed"));
    });
    return function () {
      cancelledRef.current = true;
      if (pollAbortRef.current) pollAbortRef.current.abort();
    };
  }, []);

  function closeQrModal() {
    cancelledRef.current = true;
    if (pollAbortRef.current) pollAbortRef.current.abort();
    pollAbortRef.current = null;
    setQrCode("");
    setQrStatus("");
    setBusy(false);
  }

  function qrImageUrl(content) {
    if (String(content || "").startsWith("data:image/")) return content;
    return "https://api.qrserver.com/v1/create-qr-code/?size=280x280&margin=8&data=" + encodeURIComponent(content);
  }

  function pollLogin(qrcodeId) {
    var controller = new AbortController();
    pollAbortRef.current = controller;
    setQrStatus(t("settings.wechatWaitingConfirm"));
    fetch("/api/wechat/poll-login", {
      method: "POST",
      body: JSON.stringify({ qrcode_id: qrcodeId }),
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    }).then(readSettingsResponse).then(function (result) {
      if (cancelledRef.current) return;
      if (!result.ok) {
        setBusy(false);
        setQrStatus(t("settings.wechatQrExpired"));
        return;
      }
      setQrStatus(t("settings.wechatLoginSuccess"));
      return fetch("/api/wechat/start", { method: "POST" })
        .then(readSettingsResponse)
        .then(refreshStatus)
        .then(function () {
          if (cancelledRef.current) return;
          setBusy(false);
          setQrCode("");
          setQrStatus("");
        });
    }).catch(function (error) {
      if (cancelledRef.current || error.name === "AbortError") return;
      setBusy(false);
      setQrStatus(t("settings.wechatConnectionFailed") + ": " + error.message);
    }).finally(function () {
      if (pollAbortRef.current === controller) pollAbortRef.current = null;
    });
  }

  function startLogin() {
    cancelledRef.current = false;
    if (pollAbortRef.current) pollAbortRef.current.abort();
    setBusy(true);
    setQrCode("");
    setQrStatus(t("settings.wechatFetchingQr"));
    fetch("/api/wechat/qr-login", { method: "POST" })
      .then(readSettingsResponse)
      .then(function (result) {
        if (!result.qrcode_id || (!result.qrcode_image && !result.qrcode_img)) {
          throw new Error(t("settings.wechatInvalidQr"));
        }
        if (cancelledRef.current) return;
        setQrCode(qrImageUrl(result.qrcode_image || result.qrcode_img));
        setQrStatus(t("settings.wechatScanPrompt"));
        pollLogin(result.qrcode_id);
      })
      .catch(function (error) {
        if (cancelledRef.current || error.name === "AbortError") return;
        setBusy(false);
        setQrStatus(t("settings.wechatConnectionFailed") + ": " + error.message);
      });
  }

  function startWechat() {
    setBusy(true);
    setQrStatus("");
    fetch("/api/wechat/start", { method: "POST" })
      .then(readSettingsResponse)
      .then(refreshStatus)
      .catch(function (error) {
        setQrStatus(t("settings.wechatStartFailed") + ": " + error.message);
      })
      .finally(function () { setBusy(false); });
  }

  function stopWechat() {
    setBusy(true);
    setQrStatus("");
    fetch("/api/wechat/stop", { method: "POST" })
      .then(readSettingsResponse)
      .then(refreshStatus)
      .catch(function (error) {
        setQrStatus(t("settings.wechatStopFailed") + ": " + error.message);
      })
      .finally(function () { setBusy(false); });
  }

  var statusText = connected
    ? (running ? t("settings.wechatConnectedRunning") : t("settings.wechatConnectedStopped"))
    : t("settings.wechatNotConnected");

  return React.createElement("div", { className: "wb-channel-card wb-wechat-card" },
    React.createElement("div", { className: "wb-channel-head wb-channel-head-spread" },
      React.createElement("div", { className: "wb-channel-title" },
        React.createElement("span", { className: "wb-channel-icon" }, "⌖"),
        React.createElement("b", null, t("settings.wechat")),
      ),
      connected && React.createElement("span", {
        className: "wb-channel-state " + (running ? "running" : "stopped"),
      }, running ? t("settings.wechatRunning") : t("settings.wechatStopped")),
    ),
    React.createElement("p", { className: "wb-channel-desc" }, t("settings.wechatDescription")),
    React.createElement("div", { className: "wb-wechat-status-row" },
      React.createElement("div", { className: "wb-wechat-status-copy" },
        React.createElement("small", null, t("settings.wechatCurrentStatus")),
        React.createElement("span", null,
          React.createElement("i", { className: "wb-channel-dot " + (running ? "running" : (connected ? "stopped" : "off")) }),
          React.createElement("strong", null, statusText),
        ),
        ownerWxid && React.createElement("code", null, ownerWxid),
      ),
      React.createElement("div", { className: "wb-wechat-actions" },
        connected && running && React.createElement("button", {
          className: "wb-btn danger", onClick: stopWechat, disabled: busy,
        }, t("settings.wechatStop")),
        connected && !running && React.createElement("button", {
          className: "wb-btn primary", onClick: startWechat, disabled: busy,
        }, t("settings.wechatStart")),
        !connected && React.createElement("button", {
          className: "wb-btn primary", onClick: startLogin, disabled: busy,
        }, busy ? t("settings.wechatFetchingQr") : t("settings.wechatScanConnect")),
      ),
    ),
    qrStatus && !qrCode && React.createElement("div", { className: "wb-wechat-message", role: "status" }, qrStatus),
    FieldRow(t("settings.notifyWechat"), t("settings.notifyWechatHint"), Toggle(notifyWechat, function () {
      var next = !notifyWechat;
      setNotifyWechat(next);
      fetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notify_wechat: next }) }).catch(function () {});
    })),
    qrCode && React.createElement("div", {
      className: "wb-wechat-qr-overlay",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": t("settings.wechatScanningTitle"),
      onClick: closeQrModal,
    },
      React.createElement("div", { className: "wb-wechat-qr-dialog", onClick: function (event) { event.stopPropagation(); } },
        React.createElement("button", {
          className: "wb-wechat-qr-close",
          onClick: closeQrModal,
          title: t("common.close"),
          "aria-label": t("common.close"),
        }, "×"),
        React.createElement("h3", null, t("settings.wechatScanningTitle")),
        React.createElement("img", { src: qrCode, alt: t("settings.wechatQrAlt") }),
        React.createElement("p", { role: "status" }, qrStatus),
        qrStatus === t("settings.wechatQrExpired") && React.createElement("button", {
          className: "wb-btn primary",
          onClick: startLogin,
        }, t("settings.wechatQrRetry")),
      ),
    ),
  );
}

// ── Agents Panel ──
function AgentsPanel(p) {
  var { t, config, setConfig, configLoading, soulDraft, setSoulDraft, soulStatus, saveSoul, agentProactive, setAgentProactive, saveAgents } = p;

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.agents"), t("settings.agentsSubtitle")),

    // SOUL.md
    React.createElement("div", { className: "wb-field wb-field-stack wb-field-soul" },
      React.createElement("div", { className: "wb-label" }, t("settings.soulMd"), React.createElement("small", null, t("settings.soulMdHint"))),
      React.createElement("textarea", { className: "wb-input mono wb-textarea-soul", value: soulDraft, onChange: function (e) { setSoulDraft(e.target.value); } }),
      React.createElement("div", { className: "wb-inline-row wb-inline-row-start", style: { marginTop: 8 } },
        React.createElement("button", { className: "wb-btn primary", onClick: saveSoul }, t("settings.saveSoul")),
        React.createElement("span", { className: "wb-hint" }, soulStatus || (configLoading ? t("settings.pathLoading") : config.soul_path)),
      ),
    ),

    FieldRow(t("settings.spawnPolicy"), t("settings.spawnPolicyHint"),
      React.createElement("select", { className: "wb-select", value: config.spawn_policy || "conservative", onChange: function (e) { setConfig({ ...config, spawn_policy: e.target.value }); } },
        React.createElement("option", { value: "aggressive" }, t("settings.aggressive")),
        React.createElement("option", { value: "conservative" }, t("settings.conservative")),
        React.createElement("option", { value: "off" }, t("settings.off")),
      ),
    ),
    FieldRow(t("settings.agentProactive"), t("settings.agentProactiveHint"), Toggle(agentProactive, function () { setAgentProactive(!agentProactive); })),
    FieldRow(t("settings.heartbeatInterval"), t("settings.heartbeatIntervalHint"),
      React.createElement("input", { className: "wb-input mono", type: "number", min: "60", step: "1", value: config.heartbeat_interval, onChange: function (e) { setConfig({ ...config, heartbeat_interval: e.target.value }); }, style: { maxWidth: 120 } }),
    ),
    FieldRow(t("settings.maxToolRounds"), t("settings.maxToolRoundsHint"),
      React.createElement("input", { className: "wb-input mono", type: "number", min: "5", max: "200", step: "1", value: config.max_tool_rounds, onChange: function (e) { setConfig({ ...config, max_tool_rounds: Number(e.target.value) || 15 }); }, style: { maxWidth: 120 } }),
    ),
    React.createElement("div", { className: "wb-save-actions" },
      React.createElement("button", { className: "wb-btn primary", onClick: saveAgents }, t("settings.saveApply")),
    ),
  );
}

// ── Appearance Panel ──
function AppearancePanel(p) {
  var { t, tweaks, setTweak, actualTheme, theme } = p;
  var accentPresets = ["#4378ff", "#8b5cf6", "#e8796b", "#34b8a0", "#f4a93e", "#e5488b", "#6b8cff", "#a78bfa"];

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.appearance"), t("settings.appearanceSubtitle")),
    FieldRow(t("settings.theme"), t("settings.themeHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.theme === "system" ? " active" : ""), onClick: function () { setTweak("theme", "system"); } }, t("settings.system")),
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.theme === "light" ? " active" : ""), onClick: function () { setTweak("theme", "light"); } }, t("settings.light")),
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.theme === "dark" ? " active" : ""), onClick: function () { setTweak("theme", "dark"); } }, t("settings.dark")),
      ),
    ),
    FieldRow(t("settings.themeColor"), t("settings.themeColorHint", { theme: actualTheme || t("settings.system") }),
      React.createElement("div", { className: "wb-color-swatches" },
        accentPresets.map(function (color, idx) {
          return React.createElement("button", {
            key: color,
            className: "wb-color-swatch" + (tweaks.accent === color ? " active" : ""),
            style: { "--swatch": color },
            onClick: function () { setTweak("accent", color); },
            title: t("settings.accentN", { n: idx + 1 }),
          });
        }),
      ),
    ),
    FieldRow(t("settings.textSize"), t("settings.textSizeHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.textSize === "default" ? " active" : ""), onClick: function () { setTweak("textSize", "default"); } }, React.createElement("span", { style: { fontSize: 11 } }, "A"), " ", t("settings.default")),
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.textSize === "large" ? " active" : ""), onClick: function () { setTweak("textSize", "large"); } }, React.createElement("span", { style: { fontSize: 15 } }, "A"), " ", t("settings.large")),
      ),
    ),
    FieldRow(t("settings.density"), t("settings.densityHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.density === "cozy" ? " active" : ""), onClick: function () { setTweak("density", "cozy"); } }, t("settings.cozy")),
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.density === "compact" ? " active" : ""), onClick: function () { setTweak("density", "compact"); } }, t("settings.compact")),
      ),
    ),
    FieldRow(t("settings.pulseAnimation"), t("settings.pulseAnimationHint"), Toggle(tweaks.animatePulse, function () { setTweak("animatePulse", !tweaks.animatePulse); })),
  );
}

// ── Capabilities Panel ──
function CapabilitiesPanel(p) {
  var { t, browserTools, saveBrowserTools, mcpConfigs, setMcpConfigs, mcpServers, toolList, toolsExpanded, setToolsExpanded, toolsSaved, saveTools, newMcpServer, setNewMcpServer, mcpSaved, saveMcp, config } = p;

  function addMcp() {
    var name = (newMcpServer.name || "").trim();
    if (!name) return;
    setMcpConfigs(mcpConfigs.concat({
      name: name, transport: newMcpServer.transport || "stdio",
      command: newMcpServer.command || "",
      args: (newMcpServer.args || "").split(" ").filter(Boolean),
      url: newMcpServer.url || "",
      enabled: newMcpServer.enabled !== false,
    }));
    setNewMcpServer({ name: "", transport: "stdio", command: "", args: "", url: "", enabled: true });
  }

  function removeMcp(name) { setMcpConfigs(mcpConfigs.filter(function (s) { return s.name !== name; })); }
  function toggleMcp(name) { setMcpConfigs(mcpConfigs.map(function (s) { return s.name === name ? { ...s, enabled: !s.enabled } : s; })); }
  function toggleTool(name) { setToolList(toolList.map(function (t) { return t.name === name ? { ...t, enabled: !t.enabled } : t; })); }

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.capabilities"), t("settings.capabilitiesSubtitle")),
    FieldRow(t("settings.browserTools"), t("settings.browserToolsHint"), Toggle(browserTools, function () { saveBrowserTools(!browserTools); })),

    // Web Search (read only)
    SectionBlock(t("settings.webSearch"), null,
      FieldRow(t("settings.searchBackend"), null, React.createElement("input", { className: "wb-input mono", value: t("settings.builtin"), readOnly: true, style: { maxWidth: 240 } })),
      FieldRow(t("settings.builtinStatus"), null, React.createElement("input", { className: "wb-input mono", value: t("settings.autoStarted"), readOnly: true, style: { maxWidth: 240 } })),
      FieldRow(t("settings.searchProxy"), null, React.createElement("input", { className: "wb-input mono", value: t("settings.searchProxyAuto"), readOnly: true, style: { maxWidth: 240 } })),
    ),

    // MCP
    SectionBlock(t("settings.mcpServers"), null,
      mcpConfigs.map(function (server) {
        var live = mcpServers.find(function (s) { return s.name === server.name; });
        var st = live ? live.status : "disconnected";
        var tc = live ? live.tool_count : 0;
        return React.createElement("div", { className: "wb-mcp-row", key: server.name },
          React.createElement("div", { className: "wb-mcp-info" },
            React.createElement("b", null, server.name),
            React.createElement("small", null, server.transport === "stdio" ? server.command + " " + (server.args || []).join(" ") : server.url),
          ),
          React.createElement("div", { className: "wb-mcp-status" },
            React.createElement("span", { className: "wb-mcp-indicator " + st }, st === "connected" ? "● " + t("settings.connected") : "○ " + t("settings.disconnected")),
            tc > 0 && React.createElement("small", null, t("settings.toolsCount", { n: tc })),
            Toggle(server.enabled !== false, function () { toggleMcp(server.name); }),
            React.createElement("button", { className: "wb-icon-btn-small danger", onClick: function () { removeMcp(server.name); } }, "✖"),
          ),
        );
      }),
      React.createElement("div", { className: "wb-mcp-add" },
        React.createElement("input", { className: "wb-input mono", placeholder: t("settings.placeholderName"), value: newMcpServer.name, onChange: function (e) { setNewMcpServer({ ...newMcpServer, name: e.target.value }); } }),
        React.createElement("select", { className: "wb-select", value: newMcpServer.transport, onChange: function (e) { setNewMcpServer({ ...newMcpServer, transport: e.target.value }); } },
          React.createElement("option", { value: "stdio" }, "stdio"),
          React.createElement("option", { value: "sse" }, "SSE"),
        ),
        newMcpServer.transport === "stdio"
          ? React.createElement(React.Fragment, null,
              React.createElement("input", { className: "wb-input mono", placeholder: t("settings.placeholderCommand"), value: newMcpServer.command, onChange: function (e) { setNewMcpServer({ ...newMcpServer, command: e.target.value }); } }),
              React.createElement("input", { className: "wb-input mono", placeholder: t("settings.placeholderArgs"), value: newMcpServer.args, onChange: function (e) { setNewMcpServer({ ...newMcpServer, args: e.target.value }); } }),
            )
          : React.createElement("input", { className: "wb-input mono", placeholder: t("settings.placeholderMcpUrl"), value: newMcpServer.url, onChange: function (e) { setNewMcpServer({ ...newMcpServer, url: e.target.value }); } }),
        React.createElement("button", { className: "wb-btn", onClick: addMcp }, t("settings.add")),
      ),
      React.createElement("div", { className: "wb-save-actions" },
        React.createElement("button", { className: "wb-btn primary", onClick: saveMcp }, t("settings.saveRestartMcp")),
        mcpSaved && React.createElement("span", { className: "wb-hint saved" }, mcpSaved),
      ),
    ),

    // Tools
    React.createElement("div", null,
      React.createElement("button", { className: "wb-collapse-head", onClick: function () { setToolsExpanded(!toolsExpanded); } },
        React.createElement("b", null, t("settings.tools")),
        React.createElement("span", { className: "wb-collapse-icon" + (toolsExpanded ? " open" : "") }, "⌖ ".concat(toolsExpanded ? t("settings.collapseTools") : t("settings.expandTools", { count: toolList.length }))),
      ),
      toolsExpanded && React.createElement("div", { className: "wb-tool-list" },
        toolList.map(function (tool, idx) {
          return FieldRow(React.createElement("span", { className: "mono" }, tool.name), tool.desc, Toggle(tool.enabled, function () { toggleTool(tool.name); }), tool.name || "tool-" + idx);
        }),
        React.createElement("div", { className: "wb-save-actions" },
          React.createElement("button", { className: "wb-btn primary", onClick: saveTools }, t("settings.saveTools")),
          toolsSaved && React.createElement("span", { className: "wb-hint saved" }, toolsSaved),
        ),
      ),
    ),
  );
}

// ── Data Panel ──
function DataPanel(p) {
  var { t, redactSecrets, saveRedactSecrets, config, configLoading, resetStatus, setResetStatus, resetting, setResetting, backupList, backupMsg, setBackupMsg, loadBackups, exportSid, setExportSid, exportFmt, setExportFmt, exportMsg, setExportMsg, formatBytes, formatDate } = p;

  var DATA = window.DATA || {};

  function clearSession() {
    fetch("/api/chat/clear", { method: "POST" }).then(function () { if (window.refreshSessions) window.refreshSessions(); }).catch(function () {});
  }

  function resetData() {
    setResetting(true);
    setResetStatus(t("settings.resettingData"));
    fetch("/api/settings/reset-data", { method: "POST" }).then(function (r) { return r.json(); }).then(function (p) {
      if (p.ok) {
        try { Object.keys(localStorage).forEach(function (k) { if (k.indexOf("cyrene-") === 0) localStorage.removeItem(k); }); } catch (e) {}
        window.location.reload();
      } else { setResetStatus(t("settings.resetAppDataFailed")); setResetting(false); }
    }).catch(function (e) { setResetStatus(t("settings.resetAppDataFailed") + ": " + e.message); setResetting(false); });
  }

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.data"), t("settings.dataSubtitle")),
    FieldRow(t("settings.redactSecrets"), t("settings.redactSecretsHint"), Toggle(redactSecrets, function () { saveRedactSecrets(!redactSecrets); })),
    FieldRow(t("settings.clearSession"), t("settings.clearSessionHint"),
      React.createElement("button", { className: "wb-btn muted", onClick: clearSession }, t("settings.clearSessionBtn")),
    ),
    React.createElement("div", { className: "wb-field wb-field-stack wb-field-danger" },
      React.createElement("div", { className: "wb-label" },
        t("settings.resetAppData"),
        React.createElement("small", null, t("settings.resetAppDataHint")),
      ),
      React.createElement("div", { className: "wb-controls" },
        React.createElement("div", { className: "wb-inline-row wb-inline-row-start" },
          React.createElement("button", { className: "wb-btn danger", onClick: resetData, disabled: resetting }, resetting ? t("settings.resettingData") : t("settings.resetAppDataBtn")),
          resetStatus && React.createElement("span", { className: "wb-hint" }, resetStatus),
        ),
      ),
    ),

    // Path info
    SectionBlock(t("settings.pathInfo"), null,
      FieldRow(t("settings.baseDir"), null, React.createElement("input", { className: "wb-input mono", value: configLoading ? t("settings.pathLoading") : config.base_dir, readOnly: true })),
      FieldRow(t("settings.dataDir"), null, React.createElement("input", { className: "wb-input mono", value: configLoading ? t("settings.pathLoading") : config.data_dir, readOnly: true })),
      FieldRow(t("settings.workspaceDir"), null, React.createElement("input", { className: "wb-input mono", value: configLoading ? t("settings.pathLoading") : config.workspace_dir, readOnly: true })),
      FieldRow(t("settings.soulPath"), null, React.createElement("input", { className: "wb-input mono", value: configLoading ? t("settings.pathLoading") : config.soul_path, readOnly: true })),
    ),

    // Backup
    SectionBlock(t("settings.backup"), null,
      React.createElement("div", { className: "wb-inline-row" },
        React.createElement("button", { className: "wb-btn primary", onClick: function () {
          setBackupMsg(t("settings.backupExporting"));
          fetch("/api/backup/export", { method: "POST" }).then(function (r) { return r.json(); }).then(function (d) {
            if (d.ok) { setBackupMsg(t("settings.backupExported", { n: d.entries.length, size: formatBytes(d.size) })); loadBackups(); }
            else throw new Error(d.error);
          }).catch(function (e) { setBackupMsg(t("settings.failed") + ": " + e.message); });
        } }, t("settings.backupExportBtn")),
        React.createElement("button", { className: "wb-btn", onClick: function () {
          if (!backupList.length) { setBackupMsg(t("settings.backupNoBackups")); return; }
          var last = backupList[0];
          fetch("/api/backup/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: last.path }) })
            .then(function (r) { return r.json(); }).then(function (d) { if (d.ok) setBackupMsg(t("settings.backupRestored", { n: d.restored.length })); else throw new Error(d.error); })
            .catch(function (e) { setBackupMsg(t("settings.backupRestoreFailed") + ": " + e.message); });
        } }, t("settings.backupRestoreBtn")),
      ),
      backupMsg && React.createElement("p", { className: "wb-hint" }, backupMsg),
      backupList.map(function (b) {
        return React.createElement("div", { className: "wb-backup-row", key: b.name },
          React.createElement("span", { className: "wb-backup-name" }, b.name),
          React.createElement("span", { className: "wb-backup-meta" }, formatBytes(b.size), " · ", formatDate(b.modified)),
        );
      }),
    ),

    // Session export
    SectionBlock(t("settings.sessionExport"), null,
      DATA.sessions && DATA.sessions.length > 0 ? React.createElement("div", { className: "wb-export-area" },
        React.createElement("select", { className: "wb-select", value: exportSid, onChange: function (e) { setExportSid(e.target.value); setExportMsg(""); }, style: { maxWidth: 300 } },
          React.createElement("option", { value: "" }, t("settings.sessionExportSelectPlaceholder")),
          DATA.sessions.map(function (s) {
            return React.createElement("option", { key: s.id, value: s.id }, s.title || s.id);
          }),
        ),
        React.createElement("div", { className: "wb-seg" },
          React.createElement("button", { className: "wb-seg-btn" + (exportFmt === "markdown" ? " active" : ""), onClick: function () { setExportFmt("markdown"); } }, "Markdown"),
          React.createElement("button", { className: "wb-seg-btn" + (exportFmt === "json" ? " active" : ""), onClick: function () { setExportFmt("json"); } }, "JSON"),
        ),
        React.createElement("div", { className: "wb-inline-row" },
          React.createElement("button", { className: "wb-btn primary", disabled: !exportSid, onClick: function () {
            if (!exportSid) return;
            var url = "/api/sessions/" + encodeURIComponent(exportSid) + "/export?format=" + exportFmt;
            var a = document.createElement("a"); a.href = url; document.body.appendChild(a); a.click(); document.body.removeChild(a);
            setExportMsg("✓"); setTimeout(function () { setExportMsg(""); }, 2000);
          } }, t("settings.sessionExportBtn")),
          exportMsg && React.createElement("span", { className: "wb-hint" }, exportMsg),
        ),
      ) : React.createElement("p", { className: "wb-hint" }, t("settings.sessionExportNoSessions")),
    ),
  );
}

// ── About Panel ──
function AboutPanel(p) {
  var { t, config } = p;

  return React.createElement("div", { className: "settings-panel settings-panel-wide" },
    React.createElement(UpdateSection, { t: t, config: config }),
  );
}

// ── Update Section (inlined) ──
function UpdateSection({ t, config }) {
  var [checking, setChecking] = useStateSt(false);
  var [info, setInfo] = useStateSt(null);
  var [downloading, setDownloading] = useStateSt(false);
  var [progress, setProgress] = useStateSt({ downloaded: 0, total: 0, done: false });
  var [downloaded, setDownloaded] = useStateSt(false);
  var [error, setError] = useStateSt("");
  var [beta, setBeta] = useStateSt(!!(config && config.beta_updates));
  var [autoUpdate, setAutoUpdate] = useStateSt(!!(!config || config.auto_update !== false));
  var [changelogOpen, setChangelogOpen] = useStateSt(false);
  var [changelog, setChangelog] = useStateSt({ version: "", published_at: "", release_notes: "" });

  useEffectSt(function () { checkUpdate(); }, []);
  // Sync local toggle with config once it loads from the server.
  useEffectSt(function () { setBeta(!!(config && config.beta_updates)); }, [config && config.beta_updates]);
  useEffectSt(function () { setAutoUpdate(!!(!config || config.auto_update !== false)); }, [config && config.auto_update]);

  function checkUpdate() {
    setChecking(true); setError("");
    fetch("/api/update/check").then(function (r) { return r.json(); }).then(function (d) {
      setInfo(d);
      setChangelog({ version: d.latest_version || "", published_at: d.published_at || "", release_notes: d.release_notes || "" });
      setDownloaded(false);
      setProgress({ downloaded: 0, total: d.asset_size || 0, done: false, verified: false, verification_error: "" });
    }).catch(function () { setError(t("settings.updateCheckFailed")); }).finally(function () { setChecking(false); });
  }

  function openChangelog() {
    fetch("/api/update/changelog").then(function (r) { return r.json(); }).then(function (d) {
      setChangelog({
        version: d.version || (info && info.latest_version) || "",
        published_at: d.published_at || (info && info.published_at) || "",
        release_notes: d.release_notes || (info && info.release_notes) || "",
      });
      setChangelogOpen(true);
    }).catch(function () {
      setChangelog({
        version: (info && info.latest_version) || "",
        published_at: (info && info.published_at) || "",
        release_notes: (info && info.release_notes) || "",
      });
      setChangelogOpen(true);
    });
  }

  function toggleBeta() {
    if (checking || downloading) return;
    var next = !beta;
    setBeta(next);
    fetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ beta_updates: next }) })
      .then(function () { checkUpdate(); })
      .catch(function () { setBeta(!next); });
  }

  function toggleAutoUpdate() {
    if (checking || downloading) return;
    var next = !autoUpdate;
    setAutoUpdate(next);
    fetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ auto_update: next }) })
      .catch(function () { setAutoUpdate(!next); });
  }

  function startDownload() {
    setDownloading(true); setError("");
    fetch("/api/update/download", { method: "POST" }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.ok && d.verified) {
        setDownloaded(true);
        setProgress(function (p) { return Object.assign({}, p, { done: true, verified: true, actual_sha256: d.sha256 || p.actual_sha256 || "" }); });
      } else {
        setDownloaded(false);
        setProgress(function (p) { return Object.assign({}, p, { done: !!d.done, verified: false, verification_error: d.error || "" }); });
        setError(d.error || t("settings.updateDownloadFailed"));
      }
    }).catch(function () { setError(t("settings.updateDownloadFailed")); }).finally(function () { setDownloading(false); });
  }

  function fmtBytes(n) {
    n = Number(n || 0);
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
    return (n / 1073741824).toFixed(1) + " GB";
  }

  function fmtDate(value) {
    if (!value) return "—";
    var d = new Date(value);
    if (isNaN(d.getTime())) return value;
    try { return d.toLocaleDateString(); } catch (e) { return value; }
  }

  function notesText() {
    return String((info && info.release_notes) || "").trim() || t("settings.updateNoReleaseNotes", null, "No release notes provided.");
  }

  function downloadStatus() {
    if (!info) return "—";
    if (downloading) return t("settings.updateDownloading", null, "Downloading...") + " " + fmtBytes(progress.downloaded) + " / " + fmtBytes(progress.total || info.asset_size);
    if (downloaded && progress.verified) return t("settings.updateVerified", null, "Downloaded and verified");
    if (progress && progress.verification_error) return t("settings.updateVerificationFailed", null, "Verification failed") + ": " + progress.verification_error;
    if (info.update_available && !info.checksum_available) return t("settings.updateCannotVerify", null, "Cannot verify: release has no sha256 checksum.");
    if (info.update_available) return t("settings.updateReadyToDownload", null, "Ready to download");
    return t("settings.upToDate");
  }

  function statusDetailText() {
    if (!info || checking) return "";
    var detail = downloadStatus();
    if (!detail || detail === "—" || detail === statusText) return "";
    if (!info.update_available && detail === t("settings.upToDate")) return "";
    return detail;
  }

  function confirmInstall() {
    var version = info && info.latest_version ? "v" + info.latest_version : "—";
    var message = [
      t("settings.updateConfirmTitle", { version: version }, "Install update to {version}?"),
      "",
      t("settings.updateConfirmRestart", null, "Cyrene will close and restart during installation."),
      "",
      t("settings.updateConfirmNotes", null, "Release notes:"),
      notesText()
    ].join("\n");
    if (!window.confirm(message)) return;
    fetch("/api/update/restart", { method: "POST" }).then(function (r) {
      if (!r.ok) return r.json().then(function (d) { throw new Error(d.message || d.error || t("settings.updateRestartFailed", null, "Restart failed")); });
    }).catch(function (err) {
      if (err && err.message) setError(err.message);
    });
  }

  useEffectSt(function () {
    if (!downloading) return;
    var timer = setInterval(function () {
      fetch("/api/update/progress").then(function (r) { return r.json(); }).then(function (d) { setProgress(d); if (d.done) clearInterval(timer); }).catch(function () { clearInterval(timer); });
    }, 500);
    return function () { clearInterval(timer); };
  }, [downloading]);

  var lv = info && info.latest_version ? "v" + info.latest_version : "";
  var statusText = checking
    ? t("settings.updateChecking")
    : (info && info.update_available
      ? t("settings.updateAvailable")
      : (info ? t("settings.upToDate") : "—"));
  var actionDisabled = checking || downloading || !!(info && info.update_available && !downloaded && !info.checksum_available);
  var actionLabel = downloaded
    ? t("settings.updateRestartNow")
    : (checking
      ? t("settings.updateChecking")
      : (info && info.update_available ? t("settings.updateToVersion", { version: lv }) : t("settings.checkForUpdates")));
  var actionHandler = downloaded ? confirmInstall : (info && info.update_available ? startDownload : checkUpdate);
  var statusDetail = statusDetailText();
  var relatedLinks = [
    { icon: "docs", title: t("settings.relatedDocs", null, "Help docs"), action: t("settings.view", null, "View"), href: REPO_DOCS_URL },
    { icon: "changelog", title: t("settings.relatedChangelog", null, "Changelog"), action: t("settings.view", null, "View"), onClick: openChangelog },
    { icon: "website", title: t("settings.relatedWebsite", null, "Official website"), action: t("settings.view", null, "View"), href: REPO_URL },
    { icon: "github", title: t("settings.relatedGithub", null, "GitHub repository"), action: t("settings.feedback", null, "Feedback"), href: REPO_URL },
    { icon: "issue", title: t("settings.relatedIssue", null, "Submit Issue"), action: t("settings.feedback", null, "Feedback"), href: REPO_ISSUES_URL },
  ];

  return React.createElement("div", { className: "wb-about-stack" },
    React.createElement("section", { className: "wb-about-product-card" },
      React.createElement("div", { className: "wb-about-product-copy" },
        React.createElement("div", { className: "wb-about-logo", "aria-hidden": "true" },
          React.createElement("div", { className: "brand-mark" }),
        ),
        React.createElement("div", { className: "wb-about-product-text" },
          React.createElement("div", { className: "wb-about-title-row" },
            React.createElement("h3", null, "Cyrene"),
            React.createElement("span", { className: "wb-about-version-chip" }, DATA.appVersion || "—"),
          ),
          React.createElement("p", null, t("settings.aboutHeroCopy")),
        ),
      ),
      React.createElement("button", { className: "wb-btn wb-about-check-btn", disabled: actionDisabled, onClick: actionHandler }, actionLabel),
    ),

    React.createElement("section", { className: "wb-about-update-card" },
      React.createElement("div", { className: "wb-about-card-head" },
        React.createElement("h3", null, t("settings.updateSettings", null, "Update settings")),
        (info || checking) && React.createElement("span", { className: "wb-about-status-pill" }, statusText),
      ),
      React.createElement("div", { className: "wb-about-toggle-list" },
        React.createElement("label", { className: "wb-about-toggle-row" },
          React.createElement("span", null,
            React.createElement("strong", null, t("settings.autoUpdate", null, "Automatic updates")),
            React.createElement("small", null, t("settings.autoUpdateHint", null, "Automatically download and install new versions")),
          ),
          Toggle(autoUpdate, toggleAutoUpdate),
        ),
        React.createElement("label", { className: "wb-about-toggle-row" },
          React.createElement("span", null,
            React.createElement("strong", null, t("settings.betaUpdates")),
            React.createElement("small", null, t("settings.betaUpdatesHint", null, "Preview the latest features and improvements")),
          ),
          Toggle(beta, toggleBeta),
        ),
      ),
      React.createElement("div", { className: "wb-about-version-grid" },
        React.createElement("div", null, React.createElement("span", null, t("settings.updateCurrentVersion", null, "Current version")), React.createElement("strong", null, info && info.current_version ? "v" + info.current_version : (DATA.appVersion || "—"))),
        React.createElement("div", null, React.createElement("span", null, t("settings.updateLatestVersion", null, "Latest version")), React.createElement("strong", null, lv || (DATA.appVersion || "—"))),
        React.createElement("div", null, React.createElement("span", null, t("settings.updateReleaseBranch", null, "Release branch")), React.createElement("strong", null, "main")),
        React.createElement("div", null, React.createElement("span", null, t("settings.updatePublishedAt", null, "Published")), React.createElement("strong", null, fmtDate(info && info.published_at))),
      ),
      statusDetail && React.createElement("p", { className: "wb-about-update-status" }, statusDetail),
      info && info.update_available && React.createElement("div", { className: "wb-update-notes" },
        React.createElement("span", null, t("settings.updateReleaseNotes", null, "Release notes")),
        React.createElement("pre", null, notesText())
      ),
      error && React.createElement("p", { className: "wb-hint", style: { color: "var(--wb-red)" } }, error),
      downloading && React.createElement("div", { className: "wb-progress-bar" },
        React.createElement("div", { style: { width: progress.total > 0 ? Math.round((progress.downloaded / progress.total) * 100) + "%" : "0%", height: 4, background: "var(--wb-blue)", borderRadius: 2, transition: "width 0.3s" } }),
      ),
    ),

    React.createElement("section", { className: "wb-about-related-card" },
      React.createElement("h3", null, t("settings.relatedLinks", null, "Related links")),
      React.createElement("div", { className: "wb-about-related-list" },
        relatedLinks.map(function (item) {
          var props = item.onClick
            ? { key: item.title, type: "button", className: "wb-about-related-row", onClick: item.onClick }
            : { key: item.title, className: "wb-about-related-row", href: item.href, target: "_blank", rel: "noopener noreferrer" };
          return React.createElement(item.onClick ? "button" : "a", props,
            React.createElement("span", { className: "wb-about-related-icon" }, AboutRelatedIcon(item.icon)),
            React.createElement("strong", null, item.title),
            React.createElement("span", { className: "wb-about-related-action" }, item.action),
          );
        })
      ),
    ),
    changelogOpen && React.createElement("div", { className: "wb-changelog-modal-scrim", onMouseDown: function (e) { if (e.target === e.currentTarget) setChangelogOpen(false); } },
      React.createElement("div", { className: "wb-changelog-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "wb-changelog-title" },
        React.createElement("div", { className: "wb-changelog-head" },
          React.createElement("div", null,
            React.createElement("h3", { id: "wb-changelog-title" }, t("settings.relatedChangelog", null, "Changelog")),
            React.createElement("p", null,
              changelog.version ? "v" + changelog.version : (DATA.appVersion || "—"),
              changelog.published_at ? " · " + fmtDate(changelog.published_at) : "",
            ),
          ),
          React.createElement("button", { className: "wb-btn", onClick: function () { setChangelogOpen(false); } }, t("settings.close", null, "Close")),
        ),
        React.createElement("div", {
          className: "wb-changelog-body markdown",
          dangerouslySetInnerHTML: { __html: renderSettingsMarkdown(String(changelog.release_notes || "").trim() || t("settings.updateNoReleaseNotes", null, "No release notes provided.")) },
        }),
      )
    ),
  );
}

// ── Skills Panel ──
function SkillsPanel(p) {
  var t = p.t;
  var [skills, setSkills] = useStateSt([]);
  var [loading, setLoading] = useStateSt(true);
  var [query, setQuery] = useStateSt("");
  var [selectedId, setSelectedId] = useStateSt("");
  var [busy, setBusy] = useStateSt(false);
  var [message, setMessage] = useStateSt("");
  var [messageKind, setMessageKind] = useStateSt("");
  var [showMenu, setShowMenu] = useStateSt(false);

  function fmtBytes(n) {
    n = Number(n || 0);
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(); } catch (e) { return String(iso); }
  }
  function setNotice(text, kind) {
    setMessage(text);
    setMessageKind(kind || "info");
    setTimeout(function () { setMessage(""); setMessageKind(""); }, 3000);
  }

  function loadSkills() {
    setLoading(true);
    return fetch("/api/skills/installed")
      .then(function (r) { return r.ok ? r.json() : Promise.reject("HTTP " + r.status); })
      .then(function (data) {
        var list = (data && data.skills) || [];
        setSkills(list);
        setSelectedId(function (prev) {
          return prev && list.some(function (s) { return s.id === prev; }) ? prev : (list[0] && list[0].id) || "";
        });
        setLoading(false);
      })
      .catch(function () {
        setNotice(t("settings.networkError"), "error");
        setLoading(false);
      });
  }

  useEffectSt(function () { loadSkills(); }, []);

  useEffectSt(function () {
    if (!showMenu) return;
    function onDocClick(e) {
      if (!e.target.closest(".wb-skill-install-menu") && !e.target.closest(".wb-skill-install-btn")) {
        setShowMenu(false);
      }
    }
    document.addEventListener("click", onDocClick);
    return function () { document.removeEventListener("click", onDocClick); };
  }, [showMenu]);

  function handleToggle(id) {
    if (busy) return;
    setBusy(true);
    fetch("/api/skills/" + id + "/toggle", { method: "POST" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        if (data && data.ok) {
          loadSkills().then(function () { setBusy(false); });
        } else {
          setNotice(t("settings.toggleFailed"), "error");
          setBusy(false);
        }
      })
      .catch(function () { setNotice(t("settings.toggleFailed"), "error"); setBusy(false); });
  }

  function handleUninstall(id, name) {
    if (busy) return;
    window.confirmModal({
      body: t("settings.uninstallSkillConfirm", { name: name || id }),
      confirmLabel: t("settings.uninstallSkill"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      setBusy(true);
      fetch("/api/skills/" + id + "/uninstall", { method: "POST" })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (data) {
          if (data && data.ok) {
            loadSkills().then(function () { setBusy(false); });
          } else {
            setNotice(t("settings.uninstallFailed"), "error");
            setBusy(false);
          }
        })
        .catch(function () { setNotice(t("settings.uninstallFailed"), "error"); setBusy(false); });
    });
  }

  function handleFileSelected(e) {
    var file = e.target.files && e.target.files[0];
    if (!file) {
      setNotice(t("settings.installCancelled"), "info");
      return;
    }
    setBusy(true);
    setShowMenu(false);
    var formData = new FormData();
    formData.append("file", file);
    fetch("/api/skills/install-upload", { method: "POST", body: formData })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        if (data && data.ok) {
          setNotice(t("settings.saved"), "success");
          loadSkills().then(function () { setBusy(false); });
        } else {
          setNotice(data && data.error ? data.error : t("settings.installFailed"), "error");
          setBusy(false);
        }
      })
      .catch(function () { setNotice(t("settings.installFailed"), "error"); setBusy(false); });
  }

  function handleInstallFile() {
    var input = document.createElement("input");
    input.type = "file";
    input.accept = ".md,.txt,.zip,.json,.yaml,.yml,.prompt";
    input.onchange = handleFileSelected;
    input.click();
  }

  function handleInstallFolder() {
    setBusy(true);
    setShowMenu(false);
    fetch("/api/skills/install-picker", { method: "POST" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        if (data && data.cancelled) {
          setNotice(t("settings.installCancelled"), "info");
          setBusy(false);
          return;
        }
        if (data && data.ok) {
          setNotice(t("settings.saved"), "success");
          loadSkills().then(function () { setBusy(false); });
        } else {
          setNotice(data && data.error ? data.error : t("settings.installFailed"), "error");
          setBusy(false);
        }
      })
      .catch(function () { setNotice(t("settings.installFailed"), "error"); setBusy(false); });
  }

  function handleScanExisting() {
    if (busy) return;
    setBusy(true);
    fetch("/api/skills/scan", { method: "POST" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        if (data && data.ok) {
          var count = Number(data.added) || 0;
          setNotice(t("settings.skillsImported", { n: count }), count > 0 ? "success" : "info");
          loadSkills().then(function () { setBusy(false); });
        } else {
          setNotice(data && data.error ? data.error : t("settings.installFailed"), "error");
          setBusy(false);
        }
      })
      .catch(function () { setNotice(t("settings.networkError"), "error"); setBusy(false); });
  }

  var filtered = skills.filter(function (skill) {
    if (!query) return true;
    var q = query.toLowerCase();
    return [skill.name, skill.desc, skill.file_name, skill.source_path].join(" ").toLowerCase().indexOf(q) >= 0;
  });
  var selected = filtered.find(function (s) { return s.id === selectedId; }) || filtered[0] || null;

  return React.createElement("div", { className: "wb-skills-page" },
    React.createElement("div", { className: "wb-skills-head" },
      SectionTitle(t("settings.skills"), t("settings.skillsSubtitle")),
      React.createElement("div", { className: "wb-skill-actions" },
        React.createElement("button", {
          className: "wb-btn",
          onClick: handleScanExisting,
          disabled: busy,
          title: t("settings.scanExistingSkillsHint"),
        }, t("settings.scanExistingSkills")),
        React.createElement("div", { className: "wb-skill-install-wrap" },
          React.createElement("button", {
            className: "wb-btn primary wb-skill-install-btn",
            onClick: function () { setShowMenu(!showMenu); },
            disabled: busy,
          }, t("settings.installSkill")),
        showMenu && React.createElement("div", { className: "wb-skill-install-menu" },
          React.createElement("div", {
            className: "wb-skill-install-item",
            onClick: function () { handleInstallFile(); },
          }, t("settings.installFile")),
          React.createElement("div", {
            className: "wb-skill-install-item",
            onClick: function () { handleInstallFolder(); },
          }, t("settings.installFolder"))
        )
      )
    )
  ),
  React.createElement("div", { className: "wb-skills-search" },
      React.createElement("input", {
        className: "wb-input",
        value: query,
        placeholder: t("settings.filterPlaceholder"),
        onChange: function (e) { setQuery(e.target.value); },
      })
    ),
    message && React.createElement("div", { className: "wb-skills-message " + messageKind }, message),
    loading && skills.length === 0 && React.createElement("div", { className: "wb-skills-empty" }, t("settings.loading")),
    !loading && filtered.length === 0 && React.createElement("div", { className: "wb-skills-empty" },
      query ? t("settings.noSkillsMatch") : t("settings.noSkills")
    ),
    React.createElement("div", { className: "wb-skills-list" },
      filtered.map(function (skill) {
        var isActive = selected && selected.id === skill.id;
        return React.createElement("div", {
          key: skill.id,
          className: "wb-card wb-skill-card" + (isActive ? " active" : ""),
          onClick: function (e) {
            if (e.target.closest(".wb-skill-card-actions") || e.target.closest(".wb-toggle")) return;
            setSelectedId(isActive ? "" : skill.id);
          },
        },
          React.createElement("div", { className: "wb-skill-card-head" },
            React.createElement("div", { className: "wb-skill-card-icon" }, (skill.name || "S").slice(0, 1)),
            React.createElement("div", { className: "wb-skill-card-body" },
              React.createElement("div", { className: "wb-skill-card-name" }, skill.name),
              React.createElement("div", { className: "wb-skill-card-desc" }, skill.desc || "—"),
              React.createElement("div", { className: "wb-skill-card-meta" },
                React.createElement("span", { className: "wb-skill-status-pill " + (skill.enabled ? "enabled" : "disabled") },
                  skill.enabled ? t("settings.skillEnabled") : t("settings.skillDisabled")
                ),
                React.createElement("span", null, fmtBytes(skill.size_bytes || 0))
              )
            ),
            React.createElement("div", { className: "wb-skill-card-actions" },
              Toggle(skill.enabled !== false, function (e) {
                if (e && e.stopPropagation) e.stopPropagation();
                handleToggle(skill.id);
              }),
              React.createElement("button", {
                className: "wb-btn danger",
                onClick: function (e) { e.stopPropagation(); handleUninstall(skill.id, skill.name); },
                disabled: busy,
              }, t("settings.uninstallSkill"))
            )
          ),
          isActive && React.createElement("div", { className: "wb-skill-detail-body" },
            SectionBlock(t("settings.skillDetails"), null,
              React.createElement("div", { className: "wb-skill-meta-grid" },
                React.createElement("div", { className: "wb-skill-meta-row wide" },
                  React.createElement("span", { className: "wb-skill-meta-label" }, t("settings.sourcePath")),
                  React.createElement("pre", { className: "wb-skill-code compact" }, skill.source_path || "—")
                ),
                React.createElement("div", { className: "wb-skill-meta-row wide" },
                  React.createElement("span", { className: "wb-skill-meta-label" }, t("settings.storedPath")),
                  React.createElement("pre", { className: "wb-skill-code compact" }, skill.stored_path || "—")
                ),
                React.createElement("div", { className: "wb-skill-meta-row" },
                  React.createElement("span", { className: "wb-skill-meta-label" }, t("settings.installedAt")),
                  React.createElement("span", { className: "wb-skill-meta-value" }, fmtDate(skill.installed_at))
                ),
                React.createElement("div", { className: "wb-skill-meta-row" },
                  React.createElement("span", { className: "wb-skill-meta-label" }, t("settings.updatedAt")),
                  React.createElement("span", { className: "wb-skill-meta-value" }, fmtDate(skill.updated_at))
                )
              )
            ),
            (skill.files || []).length > 1 && SectionBlock(t("settings.files"), null,
              React.createElement("div", { className: "wb-skill-files" },
                skill.files.map(function (f) {
                  return React.createElement("div", { key: f.path, className: "wb-skill-file-row" },
                    React.createElement("span", null, f.path),
                    React.createElement("span", null, fmtBytes(f.size))
                  );
                })
              )
            ),
            SectionBlock(t("settings.preview"), null,
              React.createElement("pre", { className: "wb-skill-code wb-skill-preview" }, skill.preview || "—")
            )
          )
        );
      })
    )
  );
}

// ── Shortcuts Panel ──
function ShortcutsPanel(p) {
  var t = p.t;
  var sc = window.WorkbenchShortcuts;
  var isMac = sc ? sc.isMacPlatform() : false;
  var supportsSystemShortcut = !!(
    window.cyrene
    && typeof window.cyrene.getDesktopSettings === "function"
    && typeof window.cyrene.updateDesktopSettings === "function"
  );
  var [items, setItems] = useStateSt(function () { return sc ? sc.list() : []; });
  var [quickChatKeys, setQuickChatKeys] = useStateSt(["mod", "shift", "Space"]);
  var [quickChatRegistered, setQuickChatRegistered] = useStateSt(false);
  var [quickChatError, setQuickChatError] = useStateSt("");
  var [quickChatBusy, setQuickChatBusy] = useStateSt(false);
  var [capturingId, setCapturingId] = useStateSt("");
  // conflict: { reboundId, withId } — the rebound action now clashes with
  // `withId`. The warning is shown on the rebound row so the user knows which
  // binding they need to clear.
  var [conflict, setConflict] = useStateSt(null);
  var [notice, setNotice] = useStateSt("");

  function conflictLabel(id, list, tt) {
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return tt(list[i].labelKey);
    }
    return id;
  }

  // Re-read the binding list whenever the underlying store changes (a rebind
  // in this panel, a reset, or another tab editing localStorage).
  useEffectSt(function () {
    function refresh() { if (sc) setItems(sc.list()); }
    refresh();
    window.addEventListener("cyrene-shortcuts-change", refresh);
    return function () { window.removeEventListener("cyrene-shortcuts-change", refresh); };
  }, []);

  useEffectSt(function () {
    if (!supportsSystemShortcut) return undefined;
    var cancelled = false;
    window.cyrene.getDesktopSettings().then(function (settings) {
      if (cancelled || !settings) return;
      setQuickChatKeys(acceleratorToKeys(settings.quickChatShortcut));
      setQuickChatRegistered(settings.quickChatShortcutRegistered === true);
      setQuickChatError(settings.quickChatShortcutError || "");
    }).catch(function () {});
    return function () { cancelled = true; };
  }, []);

  function acceleratorToKeys(accelerator) {
    var map = {
      CommandOrControl: "mod",
      Command: "mod",
      Cmd: "mod",
      Control: "ctrl",
      Ctrl: "ctrl",
      Shift: "shift",
      Alt: "alt",
      Option: "alt",
    };
    var keys = String(accelerator || "CommandOrControl+Shift+Space").split("+").map(function (token) {
      var clean = token.trim();
      return map[clean] || clean;
    }).filter(Boolean);
    return keys.length ? keys : ["mod", "shift", "Space"];
  }

  function keysToAccelerator(keys) {
    var map = { mod: "CommandOrControl", ctrl: "Control", shift: "Shift", alt: "Alt" };
    return (keys || []).map(function (token) { return map[token] || token; }).join("+");
  }

  function saveQuickChatShortcut(keys) {
    if (!supportsSystemShortcut) return;
    setQuickChatBusy(true);
    setQuickChatError("");
    window.cyrene.updateDesktopSettings({ quickChatShortcut: keysToAccelerator(keys) })
      .then(function (settings) {
        if (!settings) throw new Error("shortcut_update_failed");
        setQuickChatKeys(acceleratorToKeys(settings.quickChatShortcut));
        setQuickChatRegistered(settings.quickChatShortcutRegistered === true);
        setQuickChatError(settings.quickChatShortcutError || "");
        if (settings.shortcutUpdateOk === false) {
          setNotice(t("settings.quickChatShortcutConflict"));
        } else {
          setNotice(t("settings.shortcutSaved"));
        }
        setTimeout(function () { setNotice(""); }, 1800);
      })
      .catch(function () {
        setQuickChatError("shortcut_update_failed");
        setNotice(t("settings.quickChatShortcutFailed"));
        setTimeout(function () { setNotice(""); }, 1800);
      })
      .finally(function () { setQuickChatBusy(false); });
  }

  function startCapture(id) {
    setCapturingId(id);
    setConflict(null);
    setNotice("");
  }
  function cancelCapture() {
    setCapturingId("");
    setConflict(null);
  }

  function onCaptureKeydown(event) {
    if (!capturingId) return;
    event.preventDefault();
    event.stopPropagation();
    var result = sc.captureEvent(event);
    if (result.cancelled) { cancelCapture(); return; }
    if (!result.keys.length) return; // wait for a terminal key
    // Reject empty / modifier-only bindings.
    var hasTerminal = result.keys.some(function (tok) {
      return tok !== "mod" && tok !== "ctrl" && tok !== "shift" && tok !== "alt";
    });
    if (!hasTerminal) { cancelCapture(); return; }
    if (capturingId === "system-quick-chat") {
      setCapturingId("");
      setConflict(null);
      saveQuickChatShortcut(result.keys);
      return;
    }
    // Detect conflicts with other actions.
    var conflictId = "";
    for (var i = 0; i < items.length; i++) {
      if (items[i].id === capturingId) continue;
      if (sameBinding(items[i].keys, result.keys)) { conflictId = items[i].id; break; }
    }
    sc.set(capturingId, result.keys);
    if (conflictId) {
      // Show the warning on the row that was just rebound so the user knows
      // which other action they need to rebind to clear the clash.
      setConflict({ reboundId: capturingId, withId: conflictId });
    } else {
      setConflict(null);
    }
    setCapturingId("");
    setNotice(t("settings.shortcutSaved"));
    setTimeout(function () { setNotice(""); }, 1500);
  }

  // Listen for keydown while capturing. Attached at the panel root so it
  // captures before the textarea / input handlers can swallow the event.
  // We use capture phase to grab the key early.
  useEffectSt(function () {
    if (!capturingId) return undefined;
    function handler(e) { onCaptureKeydown(e); }
    window.addEventListener("keydown", handler, true);
    return function () { window.removeEventListener("keydown", handler, true); };
  }, [capturingId, items]);

  function resetOne(id) {
    if (!sc) return;
    sc.reset(id);
    setConflict(null);
    setNotice(t("settings.shortcutReset"));
    setTimeout(function () { setNotice(""); }, 1500);
  }
  function resetAll() {
    if (!sc) return;
    sc.resetAll();
    setConflict(null);
    setNotice(t("settings.shortcutResetAll"));
    setTimeout(function () { setNotice(""); }, 1500);
  }

  function resetQuickChatShortcut() {
    saveQuickChatShortcut(["mod", "shift", "Space"]);
  }

  function sameBinding(a, b) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    var norm = function (arr) { return arr.slice().sort().join("|"); };
    return norm(a) === norm(b);
  }

  function renderKeys(keys) {
    return keys.map(function (token, idx) {
      return React.createElement("kbd", { key: idx }, sc ? sc.shortcutGlyph(token, isMac) : token);
    });
  }

  var groups = {};
  items.forEach(function (item) {
    if (!groups[item.group]) groups[item.group] = [];
    groups[item.group].push(item);
  });
  var groupOrder = ["global", "composer"];
  var groupLabelKey = {
    global: "settings.shortcutGroupGlobal",
    composer: "settings.shortcutGroupComposer",
  };

  return React.createElement("div", { className: "settings-panel wb-shortcuts-panel" },
    SectionTitle(t("settings.shortcuts"), t("settings.shortcutsSubtitle")),
    React.createElement("p", { className: "wb-shortcuts-platform" },
      t("settings.shortcutPlatformHint", { os: isMac ? "macOS" : "Windows / Linux" })
    ),
    supportsSystemShortcut && SectionBlock(t("settings.shortcutGroupSystem"), null,
      React.createElement("div", { className: "wb-shortcut-row" },
        React.createElement("div", { className: "wb-shortcut-info" },
          React.createElement("b", null, t("settings.quickChatShortcut")),
          React.createElement("small", null, t("settings.quickChatShortcutHint")),
        ),
        React.createElement("div", { className: "wb-shortcut-controls" },
          capturingId === "system-quick-chat"
            ? React.createElement("span", { className: "wb-shortcut-capture" }, t("settings.shortcutCapture"))
            : React.createElement("span", { className: "wb-shortcut-keys" + (quickChatRegistered ? "" : " custom") },
                renderKeys(quickChatKeys)
              ),
          capturingId !== "system-quick-chat" && React.createElement("button", {
            type: "button",
            className: "wb-btn",
            disabled: quickChatBusy,
            onClick: function () { startCapture("system-quick-chat"); },
          }, t("settings.shortcutRebind")),
          capturingId === "system-quick-chat" && React.createElement("button", {
            type: "button",
            className: "wb-btn ghost",
            onClick: cancelCapture,
          }, t("common.cancel")),
          capturingId !== "system-quick-chat" && React.createElement("button", {
            type: "button",
            className: "wb-icon-btn-small",
            title: t("settings.shortcutReset"),
            disabled: quickChatBusy,
            onClick: resetQuickChatShortcut,
          }, "↺"),
        ),
        quickChatError && React.createElement("div", { className: "wb-shortcut-conflict" },
          quickChatError === "shortcut_in_use"
            ? t("settings.quickChatShortcutConflict")
            : t("settings.quickChatShortcutFailed")
        ),
      ),
    ),
    groupOrder.map(function (groupKey) {
      var groupItems = groups[groupKey] || [];
      if (!groupItems.length) return null;
      return SectionBlock(t(groupLabelKey[groupKey] || groupKey), null,
        groupItems.map(function (item) {
          var isCapturing = capturingId === item.id;
          var isConflict = conflict && conflict.reboundId === item.id;
          var canRebind = item.allowRebind !== false;
          return React.createElement("div", { className: "wb-shortcut-row", key: item.id },
            React.createElement("div", { className: "wb-shortcut-info" },
              React.createElement("b", null, t(item.labelKey)),
              React.createElement("small", null, t(item.descKey)),
            ),
            React.createElement("div", { className: "wb-shortcut-controls" },
              isCapturing
                ? React.createElement("span", { className: "wb-shortcut-capture" }, t("settings.shortcutCapture"))
                : React.createElement("span", { className: "wb-shortcut-keys" + (item.isCustom ? " custom" : "") },
                    renderKeys(item.keys)
                  ),
              canRebind && !isCapturing && React.createElement("button", {
                type: "button",
                className: "wb-btn",
                onClick: function () { startCapture(item.id); },
              }, t("settings.shortcutRebind")),
              isCapturing && React.createElement("button", {
                type: "button",
                className: "wb-btn ghost",
                onClick: cancelCapture,
              }, t("common.cancel")),
              !isCapturing && item.isCustom && React.createElement("button", {
                type: "button",
                className: "wb-icon-btn-small",
                title: t("settings.shortcutReset"),
                onClick: function () { resetOne(item.id); },
              }, "↺"),
            ),
            isConflict && React.createElement("div", { className: "wb-shortcut-conflict" },
              t("settings.shortcutConflict", { name: conflictLabel(conflict.withId, items, t) })
            ),
          );
        })
      );
    }),
    React.createElement("div", { className: "wb-save-actions" },
      React.createElement("button", { type: "button", className: "wb-btn", onClick: resetAll }, t("settings.resetShortcuts")),
      notice && React.createElement("span", { className: "wb-hint saved" }, notice),
    ),
  );
}

// ── Shared UI helpers ──

function SectionTitle(title, subtitle) {
  return React.createElement("div", { className: "wb-section-title" },
    React.createElement("h3", null, title),
    subtitle && React.createElement("p", null, subtitle),
  );
}

function SectionBlock(title, extra, children) {
  return React.createElement("div", { className: "wb-section-block" },
    React.createElement("div", { className: "wb-section-block-head" },
      React.createElement("b", null, title),
      typeof extra === "string" ? React.createElement("small", null, extra) : (extra || null),
    ),
    children,
  );
}

function FieldRow(label, hint, controls, key) {
  return React.createElement("div", { className: "wb-field", key: key },
    React.createElement("div", { className: "wb-label" },
      label,
      hint && React.createElement("small", null, hint),
    ),
    React.createElement("div", { className: "wb-controls" }, controls),
  );
}

function Toggle(on, onClick, disabled) {
  return React.createElement("button", {
    type: "button",
    className: "wb-toggle" + (on ? " on" : ""),
    role: "switch",
    "aria-checked": on ? "true" : "false",
    disabled: !!disabled,
    onClick: disabled ? undefined : onClick,
  });
}

function ModelCard(children, key) {
  return React.createElement("div", { className: "wb-model-card", key: key }, children);
}

function ModelField(label, input) {
  return React.createElement("div", { className: "wb-model-line" },
    React.createElement("span", null, label),
    input,
  );
}

// ── Export ──
window.SettingsOverlay = SettingsOverlay;
