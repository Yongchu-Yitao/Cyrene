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
    name: "", model: "", desc: "", ctx: "", price: "", priceHint: "", api_key: "", base_url: DEFAULT_MODEL_BASE_URL, provider: "openai_compatible",
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
    provider: String(raw && raw.provider || "openai_compatible").trim(),
    reasoning_effort: String(raw && raw.reasoning_effort || "").trim(),
    api_key: String(raw && raw.api_key || fbKey || "").trim(),
    base_url: String(raw && raw.base_url || (raw && raw.provider === "codex_oauth" ? "codex://oauth" : fbBaseUrl) || DEFAULT_MODEL_BASE_URL).trim() || DEFAULT_MODEL_BASE_URL,
  };
}

function codexModelId(item) {
  return String(item && (item.model || item.id || item.slug) || "").trim();
}

function codexModelSelectOptions(models, selectedModel) {
  var options = [].concat(models || []);
  var selected = String(selectedModel || "").trim();
  if (selected && !options.some(function (item) { return codexModelId(item) === selected; })) {
    // Model discovery can briefly be empty (or stop advertising an older
    // model). Keep the persisted choice visible instead of rendering a blank
    // native select while settings and the OAuth catalog load independently.
    options.unshift({ model: selected, displayName: selected, persisted: true });
  }
  return options;
}

function codexModelReasoningEfforts(model, selectedEffort) {
  var raw = model && (model.supportedReasoningEfforts || model.supported_reasoning_efforts) || [];
  var efforts = raw.map(function (option) {
    return String(typeof option === "string"
      ? option
      : option && (option.reasoningEffort || option.reasoning_effort) || "").trim();
  }).filter(Boolean);
  var selected = String(selectedEffort || "").trim();
  if (selected && efforts.indexOf(selected) < 0) efforts.unshift(selected);
  return Array.from(new Set(efforts));
}

function downloadPercent(state) {
  return state && state.total_bytes
    ? Math.min(100, Math.round(state.downloaded_bytes * 100 / state.total_bytes))
    : 0;
}

// Polls pollFn every intervalMs until it resolves to { done: true } (or
// rejects). onDone receives the outcome; onError receives any error, including
// a timeout error (error.code === "poll_timeout") when timeoutMs is set.
// Returns the interval id so callers can clear it on unmount.
function pollUntil(pollFn, options) {
  var intervalMs = options.intervalMs || 1000;
  var timeoutMs = options.timeoutMs || 0;
  var onDone = options.onDone;
  var onError = options.onError;
  var finished = false;
  var timer = setInterval(function () {
    var result;
    try {
      result = pollFn();
    } catch (error) {
      finish(error);
      return;
    }
    Promise.resolve(result).then(function (outcome) {
      if (outcome && outcome.done) finish(null, outcome);
    }).catch(finish);
  }, intervalMs);
  var timeoutHandle = timeoutMs > 0
    ? setTimeout(function () {
      var timeoutError = new Error("poll timeout");
      timeoutError.code = "poll_timeout";
      finish(timeoutError);
    }, timeoutMs)
    : null;
  function finish(error, outcome) {
    if (finished) return;
    finished = true;
    clearInterval(timer);
    if (timeoutHandle) clearTimeout(timeoutHandle);
    if (error) {
      if (onError) onError(error);
    } else if (outcome && outcome.error) {
      if (onError) onError(outcome.error);
    } else if (onDone) {
      onDone(outcome);
    }
  }
  return timer;
}

async function readSettingsResponse(response) {
  var payload = {};
  try {
    payload = await response.json();
  } catch (e) {
    if (response.ok) throw new Error("Invalid JSON response");
  }
  if (!response.ok) {
    var responseError = new Error(
      String(payload.detail || payload.error || ("HTTP " + response.status))
    );
    responseError.code = String(payload.code || "");
    throw responseError;
  }
  return payload;
}

async function settingsFetch(input, init) {
  var response = await window.fetch(input, init);
  if (response.ok) return response;
  var payload = {};
  try {
    payload = await response.clone().json();
  } catch (e) {}
  var error = new Error(
    String(payload.detail || payload.error || payload.message || ("HTTP " + response.status))
  );
  error.code = String(payload.code || "");
  error.status = response.status;
  throw error;
}

function showSettingsToast(message, type) {
  if (!message) return false;
  try {
    var feedback = window.CyreneUI.require("feedback");
    if (feedback && typeof feedback.showToast === "function") {
      feedback.showToast(String(message), type || "info");
      return true;
    }
  } catch (e) {}
  return false;
}

function renderSettingsMarkdown(value) {
  return window.CyreneUI.require("markdown").render(value, {
    fallback: "escaped-breaks",
    errorFallback: "escaped-breaks",
    sanitizeOptions: { ADD_ATTR: ["data-line", "data-language"] },
  });
}

// ── Tab definitions ──
var TABS = [
  { id: "profile", labelKey: "rail.profile", icon: "user" },
  { id: "general", labelKey: "settings.general", icon: "settings" },
  { id: "appearance", labelKey: "settings.appearance", icon: "palette" },
  { id: "shortcuts", labelKey: "settings.shortcuts", icon: "keyboard" },
  { id: "model-usage", labelKey: "settings.modelUsage", icon: "route" },
  { id: "models", labelKey: "settings.modelServices", icon: "box" },
  { id: "agents", labelKey: "settings.agents", icon: "robot" },
  { id: "voice", labelKey: "settings.voiceTab", icon: "microphone" },
  { id: "tools", labelKey: "settings.toolsTab", icon: "tools" },
  { id: "channels", labelKey: "settings.channels", icon: "messages" },
  { id: "remote", labelKey: "settings.remoteTab", icon: "device-desktop-up" },
  { id: "extensions", labelKey: "settings.extensionCenter", icon: "puzzle" },
  { id: "custom-tools", labelKey: "settings.customTools", icon: "code" },
  { id: "integrations", labelKey: "settings.integrations", icon: "plug-connected" },
  { id: "budget", labelKey: "settings.budget", icon: "wallet" },
  { id: "usage", labelKey: "settings.usage", icon: "chart-bar" },
  { id: "data", labelKey: "settings.data", icon: "database" },
  { id: "about", labelKey: "settings.about", icon: "info-circle" },
];

var SETTINGS_TAB_GROUPS = [
  { labelKey: "settings.group.general", ids: ["profile", "general", "appearance", "shortcuts"] },
  { labelKey: "settings.group.intelligence", ids: ["model-usage", "models", "agents", "voice", "tools"] },
  { labelKey: "settings.group.connections", ids: ["channels", "remote"] },
  { labelKey: "settings.group.extensionsSystem", ids: ["extensions", "custom-tools", "integrations"] },
  { labelKey: "settings.group.data", ids: ["budget", "usage", "data"] },
  { labelKey: "settings.group.other", ids: ["about"] },
];

var TABS_BY_ID = TABS.reduce(function (acc, item) {
  acc[item.id] = item;
  return acc;
}, {});

function settingsIconMarkup(name) {
  var assets = window.CyreneIconAssets;
  return assets && assets.settings && assets.settings[name] || "";
}

function SettingsTabIcon(id) {
  var item = TABS_BY_ID[id] || TABS_BY_ID.general;
  var markup = settingsIconMarkup(item.icon);
  if (markup) {
    return React.createElement("span", {
      className: "settings-overlay-tab-glyph is-inline",
      dangerouslySetInnerHTML: { __html: markup },
      "aria-hidden": "true",
    });
  }
  return React.createElement("span", {
    className: "settings-overlay-tab-glyph",
    style: { "--settings-tab-icon": 'url("settings-icons/' + item.icon + '.svg")' },
    "aria-hidden": "true",
  });
}

function ExternalChevron() {
  return React.createElement("svg", { width: "18", height: "18", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.4", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
    React.createElement("path", { d: "m9 18 6-6-6-6" })
  );
}

function AutomationIcon() {
  return React.createElement("svg", { width: "15", height: "15", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
    React.createElement("path", { d: "M13 2 4.1 12.7a1 1 0 0 0 .8 1.6H11l-1 7.7 8.9-10.7a1 1 0 0 0-.8-1.6H12L13 2Z" })
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
    : name === "log" ? "M8 13v-1h8v1M8 17v-1h5M8 21v-1h3M20 5.5A2.5 2.5 0 0 0 17.5 3H6.5A2.5 2.5 0 0 0 4 5.5v15A2.5 2.5 0 0 0 6.5 23h11a2.5 2.5 0 0 0 2.5-2.5V5.5ZM4 9h16"
    : "M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15Z";
  return React.createElement("svg", { width: "23", height: "23", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
    React.createElement("path", { d: path })
  );
}

// ── Settings Page ──
function SettingsPage({
  collapsed,
  collapseControl,
  moduleDock,
  initialTab,
  theme: initialTheme,
  actualTheme,
  onToggleTheme,
  project,
  scrollToId,
}) {
  var { t, lang, setLang } = useWorkbenchI18n();
  function normalizeSettingsTab(value) {
    if (value === "skills") return "extensions";
    if (value === "capabilities") return "voice";
    return TABS_BY_ID[value] ? value : "general";
  }
  var [tab, setTab] = useStateSt(normalizeSettingsTab(initialTab));

  // Deep-link re-sync: React initializes `tab` only once at mount, so when the
  // search overlay sends a new deep-link request while the settings overlay is
  // already open, the initialTab prop changes but the tab state would not.
  // Re-sync the tab here. Declared before the scroll effect below so the tab
  // switch is committed before the first scroll attempt runs (the retry loop
  // also re-checks the anchor after the switch).
  useEffectSt(function () {
    if (!initialTab) return; // plain "open settings" — keep the current tab
    var target = normalizeSettingsTab(initialTab);
    setTab(function (current) { return current === target ? current : target; });
  }, [initialTab]);

  // Deep-link support: the search overlay can open a specific setting and ask
  // the panel to scroll to the anchor (id) after the tab has rendered. Some
  // anchors render only after async data arrives (e.g. codex quota), so retry
  // the lookup for up to ~10s. If the anchor still never appears (e.g.
  // setting-amap-key is only rendered when the AMap provider is selected),
  // give feedback instead of staying silent and land on the tab top.
  useEffectSt(function () {
    if (!scrollToId) return;
    var attempts = 0;
    var timer = null;
    function showDeepLinkFallback() {
      var feedback = window.CyreneUI && window.CyreneUI.require
        ? window.CyreneUI.require("feedback")
        : null;
      if (feedback && typeof feedback.showToast === "function") {
        feedback.showToast(t("settings.deepLinkUnavailable", null, "This setting is currently unavailable"), "info");
      }
      var container = document.querySelector(".settings-overlay-content");
      if (container && typeof container.scrollTo === "function") {
        container.scrollTo({ top: 0, behavior: "smooth" });
      }
    }
    function tryScroll() {
      var el = document.getElementById(scrollToId);
      if (el) {
        el.scrollIntoView({ block: "center", behavior: "smooth" });
        el.classList.add("wb-settings-highlight");
        setTimeout(function () { el.classList.remove("wb-settings-highlight"); }, 2200);
        return;
      }
      if (attempts < 50) {
        attempts += 1;
        timer = setTimeout(tryScroll, 200);
      } else {
        showDeepLinkFallback();
      }
    }
    timer = setTimeout(tryScroll, 0);
    return function () {
      if (timer) clearTimeout(timer);
    };
  }, [scrollToId]);

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
  var [modelSource, setModelSource] = useStateSt("custom");
  var [codexCandidate, setCodexCandidate] = useStateSt(null);
  var [draftModel, setDraftModel] = useStateSt(createEmptyModel());
  var [visionModels, setVisionModels] = useStateSt(function () { return [createEmptyModel()]; });
  var [draftVision, setDraftVision] = useStateSt(createEmptyModel());
  var [secondaryModel, setSecondaryModel] = useStateSt(null);
  var [modelsSaved, setModelsSaved] = useStateSt("");
  var [modelsSaving, setModelsSaving] = useStateSt(false);

  // ── Config state ──
  var [config, setConfig] = useStateSt({
    model: "—", base_url: "—", assistant_name: "—",
    base_dir: "—", data_dir: "—", soul_path: "—",
    workspace_dir: "—", soul_content: "", spawn_policy: "conservative",
    heartbeat_interval: 1800,
    subagent_execution_max_tool_calls: 200,
    subagent_execution_max_wall_seconds: 1800,
    subagent_execution_no_progress_turns: 3,
    subagent_execution_checkpoint_calls: 20,
    subagent_execution_max_cost_usd: 5,
    subagent_execution_max_context_tokens: 0,
    subagent_discussion_max_rounds: 5,
    subagent_discussion_max_messages_per_agent: 4,
    subagent_discussion_max_total_messages: 20,
    subagent_discussion_max_message_chars: 2000,
    subagent_discussion_max_wall_seconds: 600,
    subagent_discussion_max_tool_calls: 50,
    subagent_discussion_no_new_info_rounds: 2,
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
  var [redactSecrets, setRedactSecrets] = useStateSt(function () { return readCapability("redactSecrets", true); });
  var [mcpConfigs, setMcpConfigs] = useStateSt([]);
  var [mcpServers, setMcpServers] = useStateSt([]);
  var [mcpSaved, setMcpSaved] = useStateSt("");
  var [newMcpServer, setNewMcpServer] = useStateSt({ name: "", transport: "stdio", command: "", args: "", url: "", enabled: true });
  var [toolGroups, setToolGroups] = useStateSt([]);
  var [toolsSaved, setToolsSaved] = useStateSt("");
  var [voiceStatus, setVoiceStatus] = useStateSt({
    asr_ready: false,
    tts_model_ready: false,
    voice_profile_ready: false,
    voice_preset_ready: false,
    voice_mode: "preset",
    voice_preset: "kokoro-zm_009",
    voice_presets: [],
    tts_ready: false,
    auto_read: false,
    auto_send_after_asr: false,
    auto_stop_on_silence: true,
    reference_text: "",
  });
  var [voiceReferenceText, setVoiceReferenceText] = useStateSt("");
  var [voiceReferenceFile, setVoiceReferenceFile] = useStateSt(null);
  var [voiceReferencePhase, setVoiceReferencePhase] = useStateSt("idle");
  var [voiceReferenceElapsed, setVoiceReferenceElapsed] = useStateSt(0);
  var voiceReferenceRecorderRef = useRefSt(null);
  var voiceReferenceTimerRef = useRefSt(null);
  var voiceReferenceStartedAtRef = useRefSt(0);
  var voiceReferenceFinishingRef = useRefSt(false);
  var voiceReferenceMountedRef = useRefSt(true);
  var voiceReferenceSessionRef = useRefSt(0);
  var [voiceBusy, setVoiceBusy] = useStateSt("");
  var [voiceNotice, setVoiceNotice] = useStateSt("");

  function clearVoiceReferenceTimer() {
    if (!voiceReferenceTimerRef.current) return;
    clearInterval(voiceReferenceTimerRef.current);
    voiceReferenceTimerRef.current = null;
  }

  function finishVoiceReferenceRecording(activeRecorder) {
    var recorder = activeRecorder || voiceReferenceRecorderRef.current;
    if (!recorder || voiceReferenceFinishingRef.current) return;
    var session = voiceReferenceSessionRef.current;
    voiceReferenceFinishingRef.current = true;
    var recordedMs = Date.now() - voiceReferenceStartedAtRef.current;
    voiceReferenceRecorderRef.current = null;
    clearVoiceReferenceTimer();
    setVoiceReferencePhase("transcribing");
    setVoiceNotice(t("settings.voiceReferenceRecognizing"));
    recorder.stop().then(function (blob) {
      if (recordedMs < 1000) {
        throw new Error(t("settings.voiceReferenceTooShort"));
      }
      if (recordedMs > 15000) {
        throw new Error(t("settings.voiceReferenceTooLong"));
      }
      return wbcTranscribeVoiceBlob(blob).then(function (transcript) {
        if (!voiceReferenceMountedRef.current || session !== voiceReferenceSessionRef.current) return;
        if (transcript === false) throw new Error(t("workbenchChat.noRecognizedSpeech"));
        setVoiceReferenceFile(blob);
        setVoiceReferenceText(transcript);
        setVoiceReferencePhase("ready");
        setVoiceNotice(t("settings.voiceReferenceRecognized"));
      });
    }).catch(function (error) {
      if (!voiceReferenceMountedRef.current || session !== voiceReferenceSessionRef.current) return;
      setVoiceReferenceFile(null);
      setVoiceReferenceText(voiceStatus.reference_text || "");
      setVoiceReferencePhase("idle");
      setVoiceNotice(t("settings.error") + ": " + (error.message || ""));
    }).finally(function () {
      voiceReferenceFinishingRef.current = false;
    });
  }

  function startVoiceReferenceRecording() {
    if (!voiceStatus.asr_ready || voiceBusy || voiceReferencePhase !== "idle" && voiceReferencePhase !== "ready") return;
    setVoiceReferenceFile(null);
    setVoiceReferenceText("");
    setVoiceReferenceElapsed(0);
    setVoiceNotice("");
    setVoiceReferencePhase("starting");
    voiceReferenceFinishingRef.current = false;
    voiceReferenceSessionRef.current += 1;
    var session = voiceReferenceSessionRef.current;
    try {
      if (typeof WbcVoice !== "undefined" && WbcVoice) WbcVoice.stop();
    } catch (e) {}
    wbcStartVoiceRecorder().then(function (recorder) {
      if (!voiceReferenceMountedRef.current || session !== voiceReferenceSessionRef.current) {
        recorder.stop().catch(function () {});
        return;
      }
      voiceReferenceRecorderRef.current = recorder;
      voiceReferenceStartedAtRef.current = Date.now();
      setVoiceReferencePhase("recording");
      setVoiceNotice(t("settings.voiceReferenceRecordingHint"));
      voiceReferenceTimerRef.current = setInterval(function () {
        var elapsed = Math.max(0, (Date.now() - voiceReferenceStartedAtRef.current) / 1000);
        setVoiceReferenceElapsed(Math.min(14, elapsed));
        if (elapsed >= 14) finishVoiceReferenceRecording(recorder);
      }, 100);
    }).catch(function (error) {
      if (!voiceReferenceMountedRef.current || session !== voiceReferenceSessionRef.current) return;
      setVoiceReferencePhase("idle");
      setVoiceNotice(t("settings.error") + ": " + (error.message || ""));
    });
  }

  useEffectSt(function () {
    voiceReferenceMountedRef.current = true;
    return function () {
      voiceReferenceMountedRef.current = false;
      voiceReferenceSessionRef.current += 1;
      clearVoiceReferenceTimer();
      var recorder = voiceReferenceRecorderRef.current;
      voiceReferenceRecorderRef.current = null;
      if (recorder) recorder.stop().catch(function () {});
    };
  }, []);

  useEffectSt(function () {
    if (tab === "voice") return;
    voiceReferenceSessionRef.current += 1;
    clearVoiceReferenceTimer();
    var recorder = voiceReferenceRecorderRef.current;
    voiceReferenceRecorderRef.current = null;
    if (recorder) recorder.stop().catch(function () {});
    if (voiceReferencePhase === "starting" || voiceReferencePhase === "recording" || voiceReferencePhase === "transcribing") {
      voiceReferenceFinishingRef.current = false;
      setVoiceReferencePhase("idle");
      setVoiceReferenceFile(null);
      setVoiceReferenceText(voiceStatus.reference_text || "");
      setVoiceReferenceElapsed(0);
      setVoiceNotice("");
    }
  }, [tab]);

  // ── Data state ──
  var [resetStatus, setResetStatus] = useStateSt("");
  var [resetting, setResetting] = useStateSt(false);
  var [backupList, setBackupList] = useStateSt([]);
  var [backupMsg, setBackupMsg] = useStateSt("");
  var [exportSids, setExportSids] = useStateSt([]);
  var [workbenchExportSessions, setWorkbenchExportSessions] = useStateSt([]);
  var [exportFmt, setExportFmt] = useStateSt("markdown");
  var [exportMsg, setExportMsg] = useStateSt("");

  useEffectSt(function () {
    var cancelled = false;
    settingsFetch("/api/workbench/chats").then(function (response) {
      if (!response.ok) throw new Error("failed to load conversations");
      return response.json();
    }).then(function (payload) {
      if (!cancelled) setWorkbenchExportSessions(Array.isArray(payload.chats) ? payload.chats : []);
    }).catch(function () {
      if (!cancelled) setWorkbenchExportSessions([]);
    });
    return function () { cancelled = true; };
  }, []);

  // ── Tweak helpers ──
  var [tweaks, setTweaks] = useStateSt(function () {
    return {
      theme: initialTheme,
      accent: readTweak("accent", null),
      backgroundLight: readTweak("backgroundLight", null),
      backgroundDark: readTweak("backgroundDark", null),
      textSize: readTweak("textSize", "default"),
      animatePulse: readTweak("animatePulse", true),
    };
  });
  var appearanceRevisionRef = useRefSt(0);

  var appearanceKeyMap = {
    theme: "theme",
    accent: "accent",
    backgroundLight: "background_light",
    backgroundDark: "background_dark",
    textSize: "text_size",
    animatePulse: "animate_pulse",
  };

  function applyAppearanceValues(values) {
    var next = {
      theme: values.theme || "system",
      accent: values.accent || null,
      backgroundLight: values.background_light || null,
      backgroundDark: values.background_dark || null,
      textSize: values.text_size || "default",
      animatePulse: values.animate_pulse !== false,
    };
    setTweaks(function (previous) { return { ...previous, ...next }; });
    Object.keys(next).forEach(function (key) {
      try { localStorage.setItem("cyrene-tweak-" + key, JSON.stringify(next[key])); } catch (e) {}
      window.dispatchEvent(new Event("cyrene-tweak-" + key + "-change"));
    });
  }

  useEffectSt(function () {
    setTweaks(function (prev) {
      return prev.theme === initialTheme ? prev : { ...prev, theme: initialTheme };
    });
  }, [initialTheme]);

  function setTweak(key, val) {
    setTweaks(function (prev) { return { ...prev, [key]: val }; });
    try { localStorage.setItem("cyrene-tweak-" + key, JSON.stringify(val)); } catch (e) {}
    if (key === "textSize") document.documentElement.dataset.textSize = val || "default";
    if (key === "animatePulse") document.documentElement.dataset.animPulse = val ? "on" : "off";
    window.dispatchEvent(new Event("cyrene-tweak-" + key + "-change"));
    var backendKey = appearanceKeyMap[key];
    if (backendKey) {
      var changes = {};
      changes[backendKey] = val == null ? "" : val;
      settingsFetch("/api/settings/namespaces/appearance", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changes: changes, expected_revision: appearanceRevisionRef.current || undefined }),
      }).then(readSettingsResponse).then(function (payload) {
        appearanceRevisionRef.current = Number(payload.revision || appearanceRevisionRef.current || 0);
      }).catch(function () {
        settingsFetch("/api/settings/namespaces/appearance").then(readSettingsResponse).then(function (payload) {
          appearanceRevisionRef.current = Number(payload.revision || 0);
          applyAppearanceValues(payload.values || {});
        }).catch(function () {});
      });
    }
  }

  function setCapability(key, val) {
    try { localStorage.setItem("cyrene-tweak-cap-" + key, JSON.stringify(val)); } catch (e) {}
  }

  // Persist desktop notifications
  useEffectSt(function () {
    try { localStorage.setItem("cyrene-desktop-notifications", desktopNotifications ? "1" : "0"); } catch (e) {}
  }, [desktopNotifications]);

  // Load settings
  useEffectSt(function () {
    document.documentElement.dataset.density = "cozy";
    try { localStorage.removeItem("cyrene-tweak-density"); } catch (e) {}
    document.documentElement.dataset.textSize = tweaks.textSize || "default";
    document.documentElement.dataset.animPulse = tweaks.animatePulse ? "on" : "off";

    setConfigLoading(true);
    settingsFetch("/api/settings/config").then(function (r) { return r.ok ? r.json() : Promise.reject("HTTP " + r.status); })
      .then(function (p) {
        setConfig(p);
        setSoulDraft(p.soul_content || "");
        if (p.notify_telegram !== undefined) setNotifyTelegram(p.notify_telegram);
        if (p.notify_wechat !== undefined) setNotifyWechat(p.notify_wechat);
        if (p.redact_secrets !== undefined) setRedactSecrets(!!p.redact_secrets);
        if (p.agent_proactive !== undefined) setAgentProactive(p.agent_proactive);
        setConfigLoading(false);
      }).catch(function () { setConfigLoading(false); });

    settingsFetch("/api/settings/namespaces/appearance").then(readSettingsResponse).then(function (payload) {
      appearanceRevisionRef.current = Number(payload.revision || 0);
      var values = payload.values || {};
      if (values.appearance_migrated) {
        applyAppearanceValues(values);
        return;
      }
      var migration = {
        theme: readTweak("theme", "system") || "system",
        accent: readTweak("accent", "") || "",
        background_light: readTweak("backgroundLight", "") || "",
        background_dark: readTweak("backgroundDark", "") || "",
        text_size: readTweak("textSize", "default") || "default",
        animate_pulse: readTweak("animatePulse", true) !== false,
        appearance_migrated: true,
      };
      return settingsFetch("/api/settings/namespaces/appearance", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changes: migration, expected_revision: payload.revision }),
      }).then(readSettingsResponse).then(function (updated) {
        appearanceRevisionRef.current = Number(updated.revision || payload.revision || 0);
        applyAppearanceValues(migration);
      });
    }).catch(function () {});

    settingsFetch("/api/settings/models").then(readSettingsResponse).then(function (p) {
      var fb = p.base_url || DEFAULT_MODEL_BASE_URL;
      var norm = function (raw, i) { return normalizeModel(raw, i, fb, ""); };
      var ms = (p.custom_models || p.models || p.primary_candidates || [])
        .filter(function (model) { return model.provider !== "codex_oauth"; })
        .map(norm);
      var vs = (p.vision_models || p.vision_candidates || []).map(norm);
      if (!ms.length) ms = [norm({}, 0)];
      if (!vs.length) vs = [norm({}, 0)];
      setModels(ms);
      setModelSource(p.primary_source === "codex" ? "codex" : "custom");
      setCodexCandidate(p.codex_model ? norm(p.codex_model, 0) : null);
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

    settingsFetch("/api/settings/tools").then(function (r) { return r.json(); }).then(function (p) {
      setToolGroups(p.tool_groups || []);
    }).catch(function () {});
    settingsFetch("/api/settings/mcp").then(function (r) { return r.json(); }).then(function (p) { setMcpServers(p.servers || []); setMcpConfigs(p.configs || []); }).catch(function () {});
    refreshVoiceStatus();
    settingsFetch("/api/settings/keys").then(function (r) { return r.json(); }).then(function (p) {
      var tk = (p.keys || []).find(function (item) { return item.key === "TELEGRAM_BOT_TOKEN"; });
      if (tk) setTelegramToken(tk.value || "");
      var ak = (p.keys || []).find(function (item) { return item.key === "AMAP_API_KEY"; });
      if (ak) setAmapKey(ak.value || "");
    }).catch(function () {});

    settingsFetch("/api/backup/list").then(function (r) { return r.json(); }).then(function (d) { if (d.ok) setBackupList(d.backups || []); }).catch(function () {});
  }, []);

  useEffectSt(function () {
    function onVoiceStatusChanged(event) {
      var detail = event && event.detail;
      if (detail && typeof detail === "object") {
        setVoiceStatus(detail);
        setVoiceReferenceText(detail.reference_text || "");
        return;
      }
      refreshVoiceStatus();
    }
    window.addEventListener("cyrene:voice-status-changed", onVoiceStatusChanged);
    return function () {
      window.removeEventListener("cyrene:voice-status-changed", onVoiceStatusChanged);
    };
  }, []);

  function saveSoul() {
    setSoulStatus("");
    settingsFetch("/api/settings/soul", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: soulDraft }) })
      .then(function () { setSoulStatus(""); showSettingsToast(t("settings.saved"), "success"); })
      .catch(function (error) { showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error"); });
  }

  function saveModels() {
    var embeddingDraft = arguments[0];
    var onEmbeddingSaved = arguments[1];
    var norm = models.map(function (m, i) { return normalizeModel(m, i, config.base_url || DEFAULT_MODEL_BASE_URL, ""); }).filter(function (m) { return m.model; });
    var normalizedCodex = codexCandidate && codexCandidate.model
      ? normalizeModel(codexCandidate, 0, "", "")
      : null;
    var vNorm = visionModels.map(function (m, i) { return normalizeModel(m, i, config.base_url || DEFAULT_MODEL_BASE_URL, ""); }).filter(function (m) { return m.model; });
    if (!norm.length || !vNorm.length) { setModelsSaved(t("settings.modelCandidateRequired")); return; }
    if (modelSource === "codex" && !normalizedCodex) { setModelsSaved(t("settings.openaiOAuthModelRequired")); return; }
    if (modelsSaving) return;
    setModelsSaving(true);
    setModelsSaved(t("settings.saving"));
    var modelRequest = settingsFetch("/api/settings/models", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        models: modelSource === "codex" ? [normalizedCodex] : norm,
        custom_models: norm,
        codex_model: normalizedCodex,
        primary_source: modelSource,
        vision_models: vNorm,
        secondary_model: secondaryModel ? {
          model: secondaryModel.model, name: secondaryModel.name,
          api_key: secondaryModel.api_key, base_url: secondaryModel.base_url,
          ctx_limit: Number(secondaryModel.ctx_limit) || 0,
          max_concurrency: Number(secondaryModel.max_concurrency) || 0,
        } : null,
      }),
    }).then(readSettingsResponse).then(function (p) { return p; });
    var embeddingRequest = settingsFetch("/api/settings/integrations", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ embedding: embeddingDraft }),
    }).then(readSettingsResponse);
    return Promise.all([modelRequest, embeddingRequest]).then(function (responses) {
      var p = responses[0];
      var integrationPayload = responses[1];
      var fb = p.base_url || config.base_url || DEFAULT_MODEL_BASE_URL;
      setModels(((p.custom_models || norm)).map(function (m, i) { return normalizeModel(m, i, fb, ""); }));
      setModelSource(p.primary_source === "codex" ? "codex" : "custom");
      setCodexCandidate(p.codex_model ? normalizeModel(p.codex_model, 0, "", "") : null);
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
      if (onEmbeddingSaved) onEmbeddingSaved(integrationPayload.embedding);
      setModelsSaved("");
      showSettingsToast(t("settings.saved"), "success");
    }).catch(function (e) {
      setModelsSaved("");
      showSettingsToast(t("settings.error") + ": " + (e.message || ""), "error");
    }).finally(function () {
      setModelsSaving(false);
    });
  }

  function saveAgents() {
    settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        spawn_policy: config.spawn_policy || "conservative",
        heartbeat_interval: Number(config.heartbeat_interval) || 1800,
        agent_proactive: agentProactive,
        subagent_execution_max_tool_calls: Number(config.subagent_execution_max_tool_calls) || 200,
        subagent_execution_max_wall_seconds: Number(config.subagent_execution_max_wall_seconds) || 1800,
        subagent_execution_no_progress_turns: Number(config.subagent_execution_no_progress_turns) || 3,
        subagent_execution_checkpoint_calls: Number(config.subagent_execution_checkpoint_calls) || 20,
        subagent_execution_max_cost_usd: Number(config.subagent_execution_max_cost_usd == null ? 5 : config.subagent_execution_max_cost_usd),
        subagent_execution_max_context_tokens: Number(config.subagent_execution_max_context_tokens == null ? 0 : config.subagent_execution_max_context_tokens),
        subagent_discussion_max_rounds: Number(config.subagent_discussion_max_rounds) || 5,
        subagent_discussion_max_messages_per_agent: Number(config.subagent_discussion_max_messages_per_agent) || 4,
        subagent_discussion_max_total_messages: Number(config.subagent_discussion_max_total_messages) || 20,
        subagent_discussion_max_message_chars: Number(config.subagent_discussion_max_message_chars) || 2000,
        subagent_discussion_max_wall_seconds: Number(config.subagent_discussion_max_wall_seconds) || 600,
        subagent_discussion_max_tool_calls: Number(config.subagent_discussion_max_tool_calls) || 50,
        subagent_discussion_no_new_info_rounds: Number(config.subagent_discussion_no_new_info_rounds) || 2,
      }),
    }).then(function () {
      showSettingsToast(t("settings.saved"), "success");
    }).catch(function (error) {
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    });
  }

  function saveToolGroup(groupId, nextEnabled) {
    var previousGroups = toolGroups;
    var nextGroups = toolGroups.map(function (group) {
      return group.id === groupId
        ? { ...group, enabled: nextEnabled }
        : group;
    });
    var payload = { packages: {} };
    payload.packages[groupId] = nextEnabled;
    setToolGroups(nextGroups);
    setToolsSaved(t("settings.saving"));
    settingsFetch("/api/settings/tools", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (response) {
      if (!response.ok) return Promise.reject();
      setToolsSaved("");
      showSettingsToast(t("settings.saved"), "success");
      window.dispatchEvent(new Event("cyrene-tool-packages-change"));
    }).catch(function () {
      setToolGroups(previousGroups);
      setToolsSaved("");
      showSettingsToast(t("settings.error"), "error");
    });
  }

  function publishVoiceStatus(next) {
    setVoiceStatus(next);
    window.dispatchEvent(new CustomEvent("cyrene:voice-status-changed", { detail: next }));
  }

  function refreshVoiceStatus() {
    return settingsFetch("/api/voice/status").then(readSettingsResponse).then(function (payload) {
      setVoiceStatus(payload);
      setVoiceReferenceText(payload.reference_text || "");
      return payload;
    }).catch(function () {});
  }

  function saveVoiceBooleanSetting(settingKey, nextEnabled) {
    var previous = voiceStatus;
    publishVoiceStatus({ ...voiceStatus, [settingKey]: nextEnabled });
    setVoiceBusy("settings");
    settingsFetch("/api/voice/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [settingKey]: nextEnabled }),
    }).then(readSettingsResponse).then(function (payload) {
      publishVoiceStatus(payload);
      setVoiceNotice("");
    }).catch(function (error) {
      publishVoiceStatus(previous);
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    }).finally(function () { setVoiceBusy(""); });
  }

  function saveVoiceMode(nextMode) {
    if (voiceBusy || nextMode === voiceStatus.voice_mode || (nextMode === "custom" && !voiceStatus.custom_tts_model_ready)) return;
    setVoiceBusy("settings");
    settingsFetch("/api/voice/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice_mode: nextMode }),
    }).then(readSettingsResponse).then(function (payload) {
      publishVoiceStatus(payload);
      setVoiceNotice("");
    }).catch(function (error) {
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    }).finally(function () { setVoiceBusy(""); });
  }

  function saveVoicePreset(nextPreset) {
    if (voiceBusy || !nextPreset || nextPreset === voiceStatus.voice_preset) return;
    setVoiceBusy("settings");
    settingsFetch("/api/voice/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice_mode: "preset", voice_preset: nextPreset }),
    }).then(readSettingsResponse).then(function (payload) {
      publishVoiceStatus(payload);
      setVoiceNotice("");
    }).catch(function (error) {
      setVoiceNotice(t("settings.error") + ": " + (error.message || ""));
    }).finally(function () { setVoiceBusy(""); });
  }

  function saveVoiceProfile() {
    if (!voiceReferenceFile || !voiceReferenceText.trim() || voiceBusy) return;
    var form = new FormData();
    form.append("audio", voiceReferenceFile);
    form.append("reference_text", voiceReferenceText.trim());
    setVoiceBusy("profile");
    setVoiceNotice(t("settings.voiceProfileSaving"));
    settingsFetch("/api/voice/profile", { method: "POST", body: form })
      .then(readSettingsResponse).then(function (payload) {
        publishVoiceStatus(payload);
        setVoiceReferenceFile(null);
        setVoiceReferencePhase("idle");
        setVoiceReferenceText(payload.reference_text || voiceReferenceText.trim());
        setVoiceNotice("");
        showSettingsToast(t("settings.voiceProfileSaved"), "success");
      }).catch(function (error) {
        setVoiceNotice("");
        showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
      }).finally(function () { setVoiceBusy(""); });
  }

  function deleteVoiceProfile() {
    if (voiceBusy) return;
    setVoiceBusy("profile");
    settingsFetch("/api/voice/profile", { method: "DELETE" })
      .then(readSettingsResponse).then(function (payload) {
        publishVoiceStatus(payload);
        setVoiceReferenceFile(null);
        setVoiceReferencePhase("idle");
        setVoiceReferenceText("");
        setVoiceNotice("");
        showSettingsToast(t("settings.voiceProfileDeleted"), "success");
      }).catch(function (error) {
        showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
      }).finally(function () { setVoiceBusy(""); });
  }

  function saveRedactSecrets(nextEnabled) {
    var previousEnabled = redactSecrets;
    setRedactSecrets(nextEnabled);
    setCapability("redactSecrets", nextEnabled);
    settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ redact_secrets: nextEnabled }) }).catch(function () {
      setRedactSecrets(previousEnabled);
      setCapability("redactSecrets", previousEnabled);
    });
  }

  function saveMcp() {
    setMcpSaved(t("settings.saving"));
    settingsFetch("/api/settings/mcp", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ servers: mcpConfigs }) })
      .then(function () {
        setMcpSaved("");
        showSettingsToast(t("settings.saved"), "success");
        settingsFetch("/api/settings/mcp").then(function (r) { return r.json(); }).then(function (p) { setMcpServers(p.servers || []); setMcpConfigs(p.configs || []); }).catch(function () {});
      }).catch(function () { setMcpSaved(""); showSettingsToast(t("settings.error"), "error"); });
  }

  function toggleDesktopNotifications() {
    if (typeof Notification === "undefined") return;
    if (desktopNotifications) { setDesktopNotifications(false); return; }
    if (Notification.permission === "granted") { setDesktopNotifications(true); return; }
    if (Notification.permission !== "denied") { Notification.requestPermission().then(function (p) { setDesktopNotifications(p === "granted"); }); }
  }

  function loadBackups() {
    settingsFetch("/api/backup/list").then(function (r) { return r.json(); }).then(function (d) { if (d.ok) setBackupList(d.backups || []); }).catch(function () {});
  }

  function formatBytes(n) { n = Number(n || 0); if (n < 1024) return n + " B"; if (n < 1048576) return (n / 1024).toFixed(1) + " KB"; if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB"; return (n / 1073741824).toFixed(2) + " GB"; }
  function formatDate(iso) { if (!iso) return "—"; try { return new Date(iso).toLocaleString(); } catch (e) { return iso; } }

  // ── Render helpers ──
  function onChange(key, stateFn) { return function (e) { stateFn(e.target.value); }; }

  useEffectSt(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = window.CyreneUI.require("uiSurface");
    var unregister = TABS.map(function (item) {
      return uiSurface.register({
        node_id: "settings_tab_" + item.id,
        parent_id: "settings_page",
        scope: "settings",
        get_node: function () {
          return {
            role: "tab",
            name: t(item.labelKey),
            state: { selected: tab === item.id },
          };
        },
        actions: [{ action_id: "open", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] }],
        handlers: { open: function () { setTab(item.id); } },
      });
    });
    return function () { unregister.forEach(function (remove) { remove(); }); };
  }, [tab, t]);

  return React.createElement("div", {
    className: "settings-overlay",
  },
    React.createElement("div", {
      className: "settings-overlay-panel",
      "data-cyrene-surface-root": "true",
      role: "region",
      "aria-label": t("nav.settings"),
    },
      // Body: sidebar + content
      React.createElement("div", { className: "settings-overlay-body" },
        // Sidebar tabs
        React.createElement("nav", { className: "settings-overlay-nav workbench-integrated-rail" + (collapsed ? " is-collapsed" : ""), "aria-label": t("nav.settings") },
          React.createElement("header", { className: "settings-overlay-nav-head workbench-integrated-rail-head" },
            React.createElement("b", null, t("nav.settings")),
            collapseControl || null,
          ),
          React.createElement("div", { className: "settings-overlay-nav-scroll" },
          SETTINGS_TAB_GROUPS.map(function (group, groupIndex) {
            return React.createElement("div", {
              key: group.ids.join("-"),
              className: "settings-overlay-nav-section" + (groupIndex === 0 ? " first" : ""),
            },
              React.createElement("div", { className: "settings-overlay-nav-label" }, t(group.labelKey)),
              group.ids.map(function (id) {
                var item = TABS_BY_ID[id];
                if (!item) return null;
                return React.createElement("button", {
                  key: item.id,
                  type: "button",
                  className: "settings-overlay-tab" + (tab === item.id ? " active" : ""),
                  "aria-current": tab === item.id ? "page" : undefined,
                  onClick: function () { setTab(item.id); },
                },
                  React.createElement("span", { className: "settings-overlay-tab-icon" }, SettingsTabIcon(item.id)),
                  React.createElement("span", null, t(item.labelKey)),
                );
              }),
            );
          })),
          moduleDock || null,
        ),

        // Content area
        React.createElement("main", {
          className: "settings-overlay-content",
          key: tab,
          "data-settings-active-tab": tab,
          "data-cyrene-node-id": "settings_content_" + tab,
        },
          tab === "profile" && React.createElement("div", { className: "settings-profile-panel" },
            React.createElement(window.CyreneUI.require("profile").Page)
          ),
          tab === "general" && React.createElement(GeneralPanel, { t, lang, setLang, desktopNotifications, toggleDesktopNotifications, mapProvider, setMapProvider, amapKey, setAmapKey, amapKeySaved, setAmapKeySaved, project }),
          tab === "models" && React.createElement(window.CyreneUI.require("model-settings").ServicesPage, { t: t, project: project }),
          tab === "model-usage" && React.createElement(window.CyreneUI.require("model-settings").UsagePage, { t: t, project: project }),
          tab === "channels" && ChannelsPanel({ t, telegramToken, setTelegramToken, telegramSaved, setTelegramSaved, notifyTelegram, setNotifyTelegram, notifyWechat, setNotifyWechat }),
          tab === "remote" && React.createElement(RemotePanel, { t }),
          tab === "agents" && AgentsPanel({ t, config, setConfig, configLoading, soulDraft, setSoulDraft, soulStatus, saveSoul, agentProactive, setAgentProactive, saveAgents }),
          tab === "appearance" && React.createElement(AppearancePanel, { t, tweaks, setTweak, actualTheme, theme: initialTheme }),
          (tab === "voice" || tab === "tools") && CapabilitiesPanel({
            mode: tab,
            t, mcpConfigs, setMcpConfigs, mcpServers, toolGroups, toolsSaved,
            saveToolGroup, newMcpServer, setNewMcpServer, mcpSaved, saveMcp,
            voiceStatus, voiceReferenceText, setVoiceReferenceText,
            voiceReferenceFile, setVoiceReferenceFile, voiceReferencePhase, voiceReferenceElapsed,
            startVoiceReferenceRecording, finishVoiceReferenceRecording,
            voiceBusy, voiceNotice,
            saveVoiceBooleanSetting, saveVoiceMode, saveVoicePreset, saveVoiceProfile, deleteVoiceProfile,
          }),
          tab === "extensions" && React.createElement(ExtensionsPanel, { t: t }),
          tab === "custom-tools" && React.createElement("div", {
            className: "settings-panel wb-custom-tools-page",
            id: "setting-custom-tools",
          },
            SectionTitle(t("settings.customTools"), t("settings.customToolsHint")),
            React.createElement(CustomToolsPanel, { t: t }),
          ),
          tab === "integrations" && React.createElement(GeneralPanel, { integrationsOnly: true, t, lang, setLang, desktopNotifications, toggleDesktopNotifications, mapProvider, setMapProvider, amapKey, setAmapKey, amapKeySaved, setAmapKeySaved, project }),
          tab === "shortcuts" && React.createElement(ShortcutsPanel, { t }),
          tab === "data" && React.createElement(DataPanel, { t, redactSecrets, saveRedactSecrets, config, configLoading, resetStatus, setResetStatus, resetting, setResetting, backupList, backupMsg, setBackupMsg, loadBackups, exportSids, setExportSids, workbenchExportSessions, exportFmt, setExportFmt, exportMsg, setExportMsg, formatBytes, formatDate }),
          (tab === "budget" || tab === "usage") && React.createElement(BudgetPanel, { t, config, mode: tab }),
          tab === "about" && AboutPanel({ t, config }),
        ),
      ),
    ),
  );
}

// ── Remote Control Panel ──
function remoteRequiredCapabilities(required, selected) {
  return Array.from(new Set([].concat(required || [], selected || [])));
}

function remoteToolPackGrants(toolPackages, selectedWireNames) {
  var selected = new Set(selectedWireNames || []);
  return (toolPackages || [])
    .filter(function (item) { return selected.has(item.wire_name); })
    .map(function (item) { return item.grant; });
}

function remoteTransportDetail(t, transport) {
  var status = String((transport && transport.status) || "disabled");
  if (status === "connected" && transport && transport.port_fallback) {
    return t("settings.remoteTransportAlternatePort", {
      port: Number(transport.lan_port) || 37841,
    });
  }
  var key = {
    disabled: "settings.remoteTransportDisabled",
    configured: "settings.remoteTransportConfigured",
    connecting: "settings.remoteTransportConnecting",
    connected: "settings.remoteTransportConnected",
  }[status];
  if (key) return t(key);
  if (status === "error") {
    var detail = String((transport && transport.detail) || "").trim();
    return detail
      ? t("settings.remoteTransportErrorDetail", { detail: detail })
      : t("settings.remoteTransportError");
  }
  return t("settings.remoteTransportUnknown");
}

function remoteEventFallback(value) {
  var text = String(value || "").replace(/_/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "—";
}

function remoteEventLabel(t, eventType) {
  return t(
    "settings.remoteEvent." + eventType,
    null,
    remoteEventFallback(eventType),
  );
}

function remoteOutcomeLabel(t, outcome) {
  var value = String(outcome || "recorded");
  return t(
    "settings.remoteOutcome." + value,
    null,
    remoteEventFallback(value),
  );
}

function remoteEventTime(value) {
  if (!value) return "—";
  var date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function RemotePanel(p) {
  var { t } = p;
  var [remote, setRemote] = useStateSt(null);
  var [loading, setLoading] = useStateSt(true);
  var [busy, setBusy] = useStateSt("");
  var [notice, setNotice] = useStateSt("");
  var [pairingMode, setPairingMode] = useStateSt("share");
  var [inviteToolPacks, setInviteToolPacks] = useStateSt([]);
  var [inviteProjects, setInviteProjects] = useStateSt([]);
  var [pairingKey, setPairingKey] = useStateSt("");
  var [remoteAddress, setRemoteAddress] = useStateSt("");
  var [incomingPairingKey, setIncomingPairingKey] = useStateSt("");
  var [auditEvents, setAuditEvents] = useStateSt([]);
  var remoteSaveTimerRef = useRefSt(null);
  var remoteSaveQueueRef = useRefSt(Promise.resolve());
  var remoteSaveVersionRef = useRefSt(0);
  var remoteDraftRef = useRefSt(null);
  var inviteToolPacksRef = useRefSt([]);
  var inviteDefaultsInitializedRef = useRefSt(false);
  var pairingPeerIdsRef = useRefSt([]);
  var pairingExpiresAtRef = useRefSt(0);

  function showRemoteNotice(message, type) {
    var feedback = window.CyreneUI && window.CyreneUI.require
      ? window.CyreneUI.require("feedback")
      : null;
    if (feedback && typeof feedback.showToast === "function") {
      feedback.showToast(message, type || "success");
      return;
    }
    setNotice(message);
  }

  function notifyRemoteDevicesChanged(reason) {
    try {
      window.dispatchEvent(new CustomEvent("cyrene:remote-devices-changed", {
        detail: { reason: reason || "settings" },
      }));
    } catch (e) {}
  }

  function loadRemote(options) {
    var background = !!(options && options.background);
    if (!background) setLoading(true);
    return settingsFetch("/api/remote/settings").then(readSettingsResponse).then(function (payload) {
      setRemote(payload);
      remoteDraftRef.current = payload;
      if (!inviteDefaultsInitializedRef.current) {
        var defaultToolPacks = (payload.remote_tool_packages || []).map(function (item) {
          return item.wire_name;
        });
        var defaultProjects = (payload.projects || []).map(function (project) {
          return project.id;
        });
        inviteDefaultsInitializedRef.current = true;
        inviteToolPacksRef.current = defaultToolPacks;
        setInviteToolPacks(defaultToolPacks);
        setInviteProjects(defaultProjects);
      }
      if (!background) setLoading(false);
      return payload;
    }).catch(function (error) {
      if (!background) {
        setNotice(t("settings.remoteLoadFailed") + ": " + error.message);
        setLoading(false);
      }
    });
  }

  function upsertRemotePeer(peer) {
    var current = remoteDraftRef.current;
    if (!current || !peer || !peer.device_id) return;
    var peers = (current.peers || []).filter(function (item) {
      return item.device_id !== peer.device_id;
    });
    var next = { ...current, peers: peers.concat([peer]) };
    remoteDraftRef.current = next;
    setRemote(next);
  }

  function loadAudit() {
    return settingsFetch("/api/remote/audit?limit=30").then(readSettingsResponse).then(function (payload) {
      setAuditEvents(payload.events || []);
    }).catch(function () {});
  }

  useEffectSt(function () {
    loadRemote();
    loadAudit();
    return function () {
      if (remoteSaveTimerRef.current) {
        clearTimeout(remoteSaveTimerRef.current);
      }
    };
  }, []);

  useEffectSt(function () {
    if (!pairingKey) return undefined;
    var refresh = function () {
      if (pairingExpiresAtRef.current && Date.now() >= pairingExpiresAtRef.current) {
        setPairingKey("");
        return;
      }
      loadRemote({ background: true }).then(function (payload) {
        if (!payload) return;
        var previousIds = pairingPeerIdsRef.current;
        var hasNewPeer = (payload.peers || []).some(function (peer) {
          return previousIds.indexOf(peer.device_id) < 0;
        });
        if (!hasNewPeer) return;
        setPairingKey("");
        showRemoteNotice(t("settings.remotePairingComplete"));
        notifyRemoteDevicesChanged("paired");
        loadAudit();
      });
    };
    var timer = setInterval(refresh, 1000);
    return function () { clearInterval(timer); };
  }, [pairingKey]);

  function persistSettings(nextRemote, version) {
    if (!nextRemote) return;
    var snapshot = {
      enabled: !!nextRemote.enabled,
      relay_url: "",
      device_name: String(nextRemote.device_name || "").trim(),
      default_tool_packs: nextRemote.default_tool_packs || [],
    };
    setBusy("settings");
    var request = remoteSaveQueueRef.current.catch(function () {}).then(function () {
      return settingsFetch("/api/remote/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(snapshot),
      }).then(readSettingsResponse);
    });
    remoteSaveQueueRef.current = request;
    request.then(function (payload) {
      if (version !== remoteSaveVersionRef.current) return;
      setRemote(payload);
      loadAudit();
    }).catch(function (error) {
      if (version === remoteSaveVersionRef.current) {
        showRemoteNotice(t("settings.error") + ": " + error.message, "error");
      }
    }).finally(function () {
      if (version === remoteSaveVersionRef.current) {
        setBusy("");
      }
    });
  }

  function updateRemoteSettings(nextRemote, immediate) {
    remoteDraftRef.current = nextRemote;
    var version = ++remoteSaveVersionRef.current;
    setRemote(nextRemote);
    if (remoteSaveTimerRef.current) {
      clearTimeout(remoteSaveTimerRef.current);
      remoteSaveTimerRef.current = null;
    }
    if (immediate) {
      persistSettings(nextRemote, version);
      return;
    }
    remoteSaveTimerRef.current = setTimeout(function () {
      remoteSaveTimerRef.current = null;
      persistSettings(remoteDraftRef.current, version);
    }, 600);
  }

  function flushRemoteSettings() {
    if (!remoteSaveTimerRef.current) return;
    clearTimeout(remoteSaveTimerRef.current);
    remoteSaveTimerRef.current = null;
    persistSettings(remoteDraftRef.current, remoteSaveVersionRef.current);
  }

  function toggleList(value, setter) {
    setter(function (current) {
      return current.indexOf(value) >= 0
        ? current.filter(function (item) { return item !== value; })
        : current.concat([value]);
    });
  }

  function toggleInviteToolPack(wireName) {
    var current = inviteToolPacksRef.current;
    var next = current.indexOf(wireName) >= 0
      ? current.filter(function (item) { return item !== wireName; })
      : current.concat([wireName]);
    inviteToolPacksRef.current = next;
    setInviteToolPacks(next);
    updateRemoteSettings({
      ...(remoteDraftRef.current || remote),
      default_tool_packs: next,
    }, true);
  }

  function createInvitation() {
    pairingPeerIdsRef.current = ((remoteDraftRef.current || remote).peers || []).map(function (peer) {
      return peer.device_id;
    });
    setBusy("invite");
    settingsFetch("/api/remote/pairing/short-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        capabilities: remoteRequiredCapabilities(
          remote.default_capabilities,
          remoteToolPackGrants(remote.remote_tool_packages, inviteToolPacks),
        ),
        project_scopes: inviteProjects,
        ttl_seconds: 120,
      }),
    }).then(readSettingsResponse).then(function (payload) {
      pairingExpiresAtRef.current = Date.parse(payload.expires_at || "") || (Date.now() + 120000);
      setPairingKey(payload.pairing_key || "");
      showRemoteNotice(t("settings.remoteInvitationCreated"));
      loadAudit();
    }).catch(function (error) {
      showRemoteNotice(t("settings.error") + ": " + error.message, "error");
    }).finally(function () {
      setBusy("");
    });
  }

  function connectRemoteDevice() {
    if (!remoteAddress.trim() || !incomingPairingKey.trim()) return;
    setBusy("accept");
    settingsFetch("/api/remote/pairing/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        address: remoteAddress.trim(),
        pairing_key: incomingPairingKey.trim(),
      }),
    }).then(readSettingsResponse).then(function (payload) {
      setIncomingPairingKey("");
      upsertRemotePeer(payload.peer);
      showRemoteNotice(t("settings.remotePairingComplete"));
      notifyRemoteDevicesChanged("paired");
      loadRemote({ background: true });
      loadAudit();
    }).catch(function (error) {
      showRemoteNotice(error.code === "remote_pairing_peer_update_required"
        ? t("settings.remotePeerUpdateRequired")
        : t("settings.error") + ": " + error.message, "error");
    }).finally(function () {
      setBusy("");
    });
  }

  function copyText(value) {
    if (!value) return;
    var write;
    if (window.cyrene && typeof window.cyrene.writeClipboardText === "function") {
      write = Promise.resolve(window.cyrene.writeClipboardText(value));
    } else if (navigator.clipboard && navigator.clipboard.writeText) {
      write = navigator.clipboard.writeText(value);
    } else {
      write = new Promise(function (resolve, reject) {
        var input = document.createElement("textarea");
        input.value = value;
        input.setAttribute("readonly", "");
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.appendChild(input);
        input.select();
        try {
          if (!document.execCommand("copy")) throw new Error("copy failed");
          resolve();
        } catch (error) {
          reject(error);
        } finally {
          input.remove();
        }
      });
    }
    write.then(function () {
      showRemoteNotice(t("settings.remoteCopied"));
    }).catch(function (error) {
      showRemoteNotice(t("settings.error") + ": " + error.message, "error");
    });
  }

  if (loading && !remote) {
    return React.createElement("div", { className: "settings-panel" },
      SectionTitle(t("settings.remote"), t("settings.remoteSubtitle")),
      React.createElement("p", { className: "wb-hint" }, t("settings.loading")),
    );
  }

  if (!remote) {
    return React.createElement("div", { className: "settings-panel" },
      SectionTitle(t("settings.remote"), t("settings.remoteSubtitle")),
      React.createElement("p", { className: "wb-hint" }, notice || t("settings.remoteLoadFailed")),
    );
  }

  var identity = remote.identity || {};
  var transport = remote.transport || {};
  var directPairing = remote.direct_pairing || {};
  var localAddresses = directPairing.addresses || [];
  return React.createElement("div", { className: "settings-panel remote-settings-panel" },
    SectionTitle(t("settings.remote"), t("settings.remoteSubtitle")),

    FieldRow(
      remote.enabled ? t("settings.remoteEnabled") : t("settings.remoteDisabled"),
      remoteTransportDetail(t, transport),
      Toggle(!!remote.enabled, function () {
        updateRemoteSettings({ ...remote, enabled: !remote.enabled }, true);
      }, busy === "settings", t("settings.remoteEnable")),
    ),

    SectionBlock(t("settings.remoteThisDevice"), null,
      React.createElement("div", { className: "remote-identity-grid" },
        React.createElement("label", null,
          React.createElement("span", null, t("settings.remoteDeviceName")),
          React.createElement("input", {
            className: "wb-input",
            value: remote.device_name || "",
            maxLength: 120,
            onChange: function (e) {
              updateRemoteSettings(
                { ...remote, device_name: e.target.value },
                false,
              );
            },
            onBlur: flushRemoteSettings,
          }),
        ),
      ),
      React.createElement("div", { className: "remote-identity-facts" },
        React.createElement("div", null, React.createElement("span", null, t("settings.remoteLocalAddress")), React.createElement("code", null, localAddresses[0] || t("settings.remoteAddressUnavailable"))),
        React.createElement("div", null, React.createElement("span", null, t("settings.remoteDeviceId")), React.createElement("code", null, identity.device_id || "—")),
        React.createElement("div", null, React.createElement("span", null, t("settings.remoteFingerprint")), React.createElement("code", null, identity.fingerprint || "—")),
      ),
    ),

    SectionBlock(t("settings.remotePairDevice"), null,
      React.createElement("div", { className: "remote-pairing-layout" },
        React.createElement("div", { className: "remote-pairing-toolbar" },
          React.createElement("p", null, t("settings.remotePairDeviceHint")),
          React.createElement("div", { className: "wb-seg remote-pairing-tabs", role: "tablist", "aria-label": t("settings.remotePairDevice") },
            React.createElement("button", {
              type: "button",
              role: "tab",
              "aria-selected": pairingMode === "share",
              className: "wb-seg-btn" + (pairingMode === "share" ? " active" : ""),
              onClick: function () { setPairingMode("share"); },
            }, t("settings.remotePairModeShare")),
            React.createElement("button", {
              type: "button",
              role: "tab",
              "aria-selected": pairingMode === "control",
              className: "wb-seg-btn" + (pairingMode === "control" ? " active" : ""),
              onClick: function () { setPairingMode("control"); },
            }, t("settings.remotePairModeControl")),
          ),
        ),
        pairingMode === "share"
          ? React.createElement("div", { className: "remote-pairing-pane", role: "tabpanel" },
              React.createElement("div", { className: "remote-pairing-copy" },
                React.createElement("b", null, t("settings.remoteAllowController")),
                React.createElement("small", null, t("settings.remoteAllowControllerHint")),
              ),
              React.createElement("details", { className: "remote-pairing-settings" },
                React.createElement("summary", null,
                  React.createElement("span", { className: "remote-pairing-settings-chevron", "aria-hidden": "true" }, ExternalChevron()),
                  React.createElement("span", { className: "remote-pairing-settings-title" }, t("settings.remoteShareSettings")),
                  React.createElement("small", null, t("settings.remoteShareSettingsHint")),
                ),
                React.createElement("div", { className: "remote-pairing-share-grid" },
                  React.createElement("div", { className: "remote-pairing-group" },
                    React.createElement("span", { className: "remote-pairing-group-title" }, t("settings.remoteDirectToolPackages")),
                    React.createElement("small", { className: "remote-required-capabilities" }, t("settings.remoteCompatibilityAlwaysOn")),
                    React.createElement("div", { className: "remote-option-list remote-tool-package-options" },
                      (remote.remote_tool_packages || []).map(function (item) {
                        var enabled = inviteToolPacks.indexOf(item.wire_name) >= 0;
                        var name = t("toolName." + item.wire_name);
                        return React.createElement("label", {
                          key: item.wire_name,
                          className: "remote-option",
                          title: t("toolPackageDesc." + item.wire_name),
                        },
                          React.createElement("input", {
                            type: "checkbox",
                            checked: enabled,
                            onChange: function () { toggleInviteToolPack(item.wire_name); },
                            "aria-label": t("settings.remotePackageToggleLabel", { name: name }),
                          }),
                          React.createElement("span", null, name),
                        );
                      }),
                    ),
                  ),
                  React.createElement("div", { className: "remote-pairing-group" },
                    React.createElement("span", { className: "remote-pairing-group-title" }, t("settings.remoteSharedProjects")),
                    React.createElement("div", { className: "remote-project-choices" },
                      (remote.projects || []).map(function (project) {
                        return React.createElement("label", { key: project.id, className: "remote-option" },
                          React.createElement("input", { type: "checkbox", checked: inviteProjects.indexOf(project.id) >= 0, onChange: function () { toggleList(project.id, setInviteProjects); } }),
                          React.createElement("span", null, project.name || project.id),
                        );
                      }),
                    ),
                  ),
                ),
              ),
              React.createElement("div", { className: "remote-pairing-actions" },
                React.createElement("button", { className: "wb-btn primary", onClick: createInvitation, disabled: !remote.enabled || !inviteProjects.length || busy === "invite" }, busy === "invite" ? t("settings.loading") : t("settings.remoteCreateInvitation")),
              ),
              pairingKey && React.createElement("div", { className: "remote-direct-offer" },
                React.createElement("div", null,
                  React.createElement("small", null, t("settings.remoteLocalAddress")),
                  React.createElement("code", null, localAddresses[0] || t("settings.remoteAddressUnavailable")),
                ),
                React.createElement("div", null,
                  React.createElement("small", null, t("settings.remotePairingKey")),
                  React.createElement("button", {
                    type: "button",
                    className: "remote-pairing-key",
                    "data-cyrene-secret": "true",
                    onClick: function () { copyText(pairingKey); },
                    title: t("settings.remoteCopyPairingKey"),
                    "aria-label": t("settings.remoteCopyPairingKey"),
                  }, pairingKey),
                ),
                React.createElement("p", null, t("settings.remoteShortKeyExpires")),
              ),
            )
          : React.createElement("div", { className: "remote-pairing-pane", role: "tabpanel" },
              React.createElement("div", { className: "remote-pairing-copy" },
                React.createElement("b", null, t("settings.remoteControlAnother")),
                React.createElement("small", null, t("settings.remoteControlAnotherHint")),
              ),
              React.createElement("div", { className: "remote-pairing-control" },
                React.createElement("label", { className: "remote-response-field" },
                  React.createElement("span", null, t("settings.remoteDeviceAddress")),
                  React.createElement("input", { className: "wb-input mono", value: remoteAddress, spellCheck: false, autoCapitalize: "off", autoCorrect: "off", placeholder: "192.168.1.20:37841", onChange: function (e) { setRemoteAddress(e.target.value); } }),
                ),
                React.createElement("label", { className: "remote-response-field" },
                  React.createElement("span", null, t("settings.remotePairingKey")),
                  React.createElement("input", { className: "wb-input mono remote-key-input", "data-cyrene-user-ceremony": "true", value: incomingPairingKey, maxLength: 11, spellCheck: false, autoCapitalize: "characters", autoCorrect: "off", placeholder: "ABCDE-23456", onChange: function (e) { setIncomingPairingKey(e.target.value.toUpperCase()); } }),
                ),
                React.createElement("div", { className: "remote-pairing-actions" },
                  React.createElement("button", { className: "wb-btn primary", onClick: connectRemoteDevice, disabled: !remoteAddress.trim() || !incomingPairingKey.trim() || busy === "accept" }, busy === "accept" ? t("settings.remoteConnectingDevice") : t("settings.remoteConnectDevice")),
                ),
              ),
            ),
      ),
    ),

    SectionBlock(t("settings.remoteTrustedDevices"), null,
      !(remote.peers || []).length && React.createElement("p", { className: "wb-hint" }, t("settings.remoteNoDevices")),
      (remote.peers || []).map(function (peer) {
        return React.createElement(RemotePeerCard, {
          key: peer.device_id,
          t: t,
          peer: peer,
          projects: remote.projects || [],
          capabilities: remote.supported_capabilities || [],
          toolPackages: remote.remote_tool_packages || [],
          onChanged: function () { loadRemote(); loadAudit(); },
          onNotice: showRemoteNotice,
        });
      }),
    ),

    SectionBlock(t("settings.remoteAudit"), null,
      React.createElement("div", { className: "remote-audit-list" },
        !auditEvents.length && React.createElement("p", { className: "wb-hint" }, t("settings.remoteNoAudit")),
        auditEvents.map(function (event) {
          return React.createElement("div", { key: event.event_id, className: "remote-audit-row" },
            React.createElement("span", { className: "remote-audit-outcome " + (event.outcome === "error" ? "error" : "") }, remoteOutcomeLabel(t, event.outcome)),
            React.createElement("div", null,
              React.createElement("b", null, remoteEventLabel(t, event.event_type)),
              React.createElement("small", null, [event.command, event.peer_device_id, remoteEventTime(event.created_at)].filter(Boolean).join(" · ")),
            ),
          );
        }),
      ),
    ),
  );
}

function RemotePeerCard(p) {
  var { t, peer, projects, capabilities, toolPackages, onChanged, onNotice } = p;
  var [editing, setEditing] = useStateSt(false);
  var [busy, setBusy] = useStateSt(false);
  var [grantedCapabilities, setGrantedCapabilities] = useStateSt(peer.granted_capabilities || []);
  var [grantedProjects, setGrantedProjects] = useStateSt(peer.granted_project_scopes || []);

  function toggle(value, setter) {
    setter(function (current) {
      return current.indexOf(value) >= 0
        ? current.filter(function (item) { return item !== value; })
        : current.concat([value]);
    });
  }

  function saveGrant() {
    var requiredGrant = remoteRequiredCapabilities(
      capabilities,
      grantedCapabilities,
    );
    setBusy(true);
    settingsFetch("/api/remote/peers/" + encodeURIComponent(peer.device_id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ capabilities: requiredGrant, project_scopes: grantedProjects }),
    }).then(readSettingsResponse).then(function () {
      setGrantedCapabilities(requiredGrant);
      setEditing(false);
      onNotice(t("settings.remoteGrantSaved"), "success");
      try { window.dispatchEvent(new CustomEvent("cyrene:remote-devices-changed", { detail: { reason: "grant_updated" } })); } catch (e) {}
      onChanged();
    }).catch(function (error) {
      onNotice(t("settings.error") + ": " + error.message, "error");
    }).finally(function () { setBusy(false); });
  }

  function revoke() {
    setBusy(true);
    settingsFetch("/api/remote/peers/" + encodeURIComponent(peer.device_id), { method: "DELETE" })
      .then(readSettingsResponse).then(function () {
        onNotice(t("settings.remoteDeviceRevoked"), "success");
        try { window.dispatchEvent(new CustomEvent("cyrene:remote-devices-changed", { detail: { reason: "revoked" } })); } catch (e) {}
        onChanged();
      }).catch(function (error) {
        onNotice(t("settings.error") + ": " + error.message, "error");
      }).finally(function () { setBusy(false); });
  }

  return React.createElement("div", { className: "remote-peer-card" },
    React.createElement("div", { className: "remote-peer-header" },
      React.createElement("div", null,
        React.createElement("b", null, peer.display_name || peer.device_id),
        React.createElement("code", null, peer.device_id),
      ),
      React.createElement("div", { className: "remote-peer-actions" },
        React.createElement("button", { className: "wb-btn muted", onClick: function () {
          if (!editing) {
            setGrantedCapabilities(peer.granted_capabilities || []);
            setGrantedProjects(peer.granted_project_scopes || []);
          }
          setEditing(!editing);
        }, disabled: busy }, editing ? t("settings.close") : t("settings.remoteEditGrant")),
        React.createElement("button", { className: "wb-btn danger", onClick: revoke, disabled: busy }, t("settings.remoteRevoke")),
      ),
    ),
    React.createElement("div", { className: "remote-peer-summary" },
      React.createElement("span", null, t("settings.remoteDeviceAddress") + ": " + (peer.lan_address || "—")),
      React.createElement("span", null, t("settings.remoteGrantedToPeer") + ": " + (peer.granted_capabilities || []).length),
      React.createElement("span", null, t("settings.remoteReceivedFromPeer") + ": " + (peer.received_capabilities || []).length),
      React.createElement("span", null, peer.fingerprint || ""),
    ),
    editing && React.createElement("div", { className: "remote-grant-editor" },
      React.createElement("div", { className: "remote-grant-tool-packages" },
        React.createElement("b", null, t("settings.remoteDirectToolPackages")),
        React.createElement("small", null, t("settings.remoteCompatibilityAlwaysOn")),
        React.createElement("small", null, t("settings.remoteDirectToolPackagesHint")),
        React.createElement("div", { className: "remote-option-list remote-tool-package-options" },
          (toolPackages || []).map(function (item) {
            var enabled = grantedCapabilities.indexOf(item.grant) >= 0;
            var name = t("toolName." + item.wire_name);
            return React.createElement("label", {
              key: item.wire_name,
              className: "remote-option",
              title: t("toolPackageDesc." + item.wire_name),
            },
              React.createElement("input", {
                type: "checkbox",
                checked: enabled,
                onChange: function () { toggle(item.grant, setGrantedCapabilities); },
                "aria-label": t("settings.remotePackageToggleLabel", { name: name }),
              }),
              React.createElement("span", null, name),
            );
          }),
        ),
      ),
      React.createElement("div", { className: "remote-project-list" },
        projects.map(function (project) {
          return React.createElement("label", { key: project.id, className: "remote-option" },
            React.createElement("input", { type: "checkbox", checked: grantedProjects.indexOf(project.id) >= 0, onChange: function () { toggle(project.id, setGrantedProjects); } }),
            React.createElement("span", null, project.name || project.id),
          );
        }),
      ),
      React.createElement("button", { className: "wb-btn primary", onClick: saveGrant, disabled: busy }, t("settings.remoteSaveGrant")),
    ),
  );
}

// ── General Panel ──
function GeneralPanel(p) {
  var { t, lang, setLang, desktopNotifications, toggleDesktopNotifications, mapProvider, setMapProvider, amapKey, setAmapKey, amapKeySaved, setAmapKeySaved } = p;
  var timezoneOptions = [
    "Pacific/Honolulu", "America/Los_Angeles", "America/Denver",
    "America/Chicago", "America/New_York", "America/Sao_Paulo",
    "UTC", "Europe/London", "Europe/Paris", "Africa/Cairo",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai",
    "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland",
  ];
  var [selectedTimezone, setSelectedTimezone] = useStateSt(function () {
    try {
      var stored = localStorage.getItem("cyrene-timezone") || "";
      return timezoneOptions.indexOf(stored) >= 0 ? stored : "Asia/Shanghai";
    } catch (e) {
      return "Asia/Shanghai";
    }
  });

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
  var [zoteroSettings, setZoteroSettings] = useStateSt({
    base_url: "http://127.0.0.1:23119/api", auto_sync: false, copy_attachments: true,
  });
  var [zoteroStatus, setZoteroStatus] = useStateSt(null);
  var [integrationBusy, setIntegrationBusy] = useStateSt("");
  var [agentProxyEnabled, setAgentProxyEnabled] = useStateSt(false);
  var [agentProxyPort, setAgentProxyPort] = useStateSt("7897");
  var [agentProxyStatus, setAgentProxyStatus] = useStateSt("");
  useEffectSt(function () {
    var cancelled = false;
    settingsFetch("/api/settings/config").then(readSettingsResponse).then(function (payload) {
      if (cancelled) return;
      var savedTimezone = String(payload.timezone || "");
      setAgentProxyEnabled(payload.external_agent_proxy_enabled === true);
      setAgentProxyPort(String(payload.external_agent_proxy_port || 7897));
      if (timezoneOptions.indexOf(savedTimezone) < 0) return;
      var previousTimezone = "";
      try { previousTimezone = localStorage.getItem("cyrene-timezone") || ""; } catch (e) {}
      setSelectedTimezone(savedTimezone);
      try { localStorage.setItem("cyrene-timezone", savedTimezone); } catch (e) {}
      if (previousTimezone && previousTimezone !== savedTimezone) {
        try { window.CyreneUI.require("data").reload(); } catch (e) {}
      }
    }).catch(function () {});
    return function () { cancelled = true; };
  }, []);

  useEffectSt(function () {
    var cancelled = false;
    settingsFetch("/api/settings/integrations").then(readSettingsResponse).then(function (payload) {
      if (cancelled) return;
      if (payload.zotero) setZoteroSettings(payload.zotero);
    }).catch(function () {
      if (!cancelled) setZoteroStatus({ kind: "error", text: t("settings.integrationLoadFailed") });
    });
    return function () { cancelled = true; };
  }, []);

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
      if (s.shortcutUpdateOk === false) showSettingsToast(t("settings.quickChatShortcutConflict"), "error");
    }).catch(function (error) {
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    }).finally(function () { setDesktopBusy(false); });
  }

  function timezoneOptionLabel(timezone) {
    try {
      var part = new Intl.DateTimeFormat("en", {
        timeZone: timezone,
        timeZoneName: "longOffset",
      }).formatToParts(new Date()).find(function (item) { return item.type === "timeZoneName"; });
      var offset = part && part.value ? part.value.replace("GMT", "UTC") : "UTC";
      return "(" + offset + ") " + timezone;
    } catch (e) {
      return timezone;
    }
  }

  function changeTimezone(event) {
    var nextTimezone = event.target.value;
    if (timezoneOptions.indexOf(nextTimezone) < 0) return;
    var previousTimezone = selectedTimezone;
    setSelectedTimezone(nextTimezone);
    try { localStorage.setItem("cyrene-timezone", nextTimezone); } catch (e) {}
    try { window.CyreneUI.require("data").reload(); } catch (e) {}
    settingsFetch("/api/settings/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timezone: nextTimezone }),
    }).catch(function () {
      setSelectedTimezone(previousTimezone);
      try { localStorage.setItem("cyrene-timezone", previousTimezone); } catch (e) {}
      try { window.CyreneUI.require("data").reload(); } catch (e) {}
    });
  }

  function saveAgentProxy(nextEnabled, nextPort) {
    var port = Number(nextPort);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      setAgentProxyStatus(t("settings.agentProxyPortInvalid"));
      return;
    }
    setAgentProxyStatus(t("settings.saving"));
    settingsFetch("/api/settings/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        external_agent_proxy_enabled: !!nextEnabled,
        external_agent_proxy_port: port,
      }),
    }).then(readSettingsResponse).then(function () {
      setAgentProxyEnabled(!!nextEnabled);
      setAgentProxyPort(String(port));
      setAgentProxyStatus("");
      showSettingsToast(t("settings.agentProxySaved"), "success");
    }).catch(function (error) {
      setAgentProxyStatus("");
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    });
  }

  function saveAmapKey() {
    if (!amapKey || amapKey.startsWith("••")) { showSettingsToast(t("settings.noChanges"), "info"); return; }
    setAmapKeySaved(t("settings.saving"));
    settingsFetch("/api/settings/keys", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ AMAP_API_KEY: amapKey }) })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function () {
        settingsFetch("/api/amap/verify").then(function (r) { return r.json(); }).then(function (vd) {
          if (vd.valid) { setAmapKeySaved(""); showSettingsToast(t("settings.amapKeySaved"), "success"); localStorage.setItem("cyrene-tweak-map-provider", "amap"); }
          else { setAmapKeySaved(""); showSettingsToast(t("settings.amapKeyVerifyFail") + " " + (vd.error || ""), "error"); }
        }).catch(function () { setAmapKeySaved(""); showSettingsToast(t("settings.saved"), "success"); });
      }).catch(function (error) { setAmapKeySaved(""); showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error"); });
  }

  function saveIntegration() {
    setIntegrationBusy("save-zotero");
    setZoteroStatus({ kind: "info", text: t("settings.saving") });
    settingsFetch("/api/settings/integrations", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zotero: zoteroSettings }),
    }).then(readSettingsResponse).then(function (payload) {
      if (payload.zotero) setZoteroSettings(payload.zotero);
      setZoteroStatus(null);
      showSettingsToast(t("settings.saved"), "success");
    }).catch(function (error) {
      setZoteroStatus(null);
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    }).finally(function () { setIntegrationBusy(""); });
  }

  function testIntegration() {
    setIntegrationBusy("test-zotero");
    setZoteroStatus(null);
    settingsFetch("/api/settings/integrations/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ service: "zotero", config: zoteroSettings }),
    }).then(readSettingsResponse).then(function (payload) {
      showSettingsToast(t("settings.zoteroConnected"), "success");
    }).catch(function (error) {
      showSettingsToast(t("settings.connectionFailed") + ": " + (error.message || ""), "error");
    }).finally(function () { setIntegrationBusy(""); });
  }

  function importFromZotero() {
    if (!(p.project && p.project.id)) {
      showSettingsToast(t("settings.zoteroImportNoProject"), "error");
      return;
    }
    setIntegrationBusy("import-zotero");
    setZoteroStatus(null);
    settingsFetch("/api/settings/integrations", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zotero: zoteroSettings }),
    }).then(readSettingsResponse).then(function (payload) {
      if (payload.zotero) setZoteroSettings(payload.zotero);
      return settingsFetch("/api/workbench/library/zotero/sync?workspace=" + encodeURIComponent(String(p.project.id)), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ library_id: "0", library_type: "user", collection_key: "" }),
      });
    }).then(readSettingsResponse).then(function (result) {
      showSettingsToast(t("settings.zoteroImportDone", {
        created: Number(result.created || result.imported || 0),
        updated: Number(result.updated || 0),
      }), "success");
    }).catch(function (error) {
      showSettingsToast(t("settings.connectionFailed") + ": " + (error.message || ""), "error");
    }).finally(function () { setIntegrationBusy(""); });
  }

  function integrationStatus(status) {
    if (!status) return null;
    return React.createElement("div", {
      className: "wb-integration-status " + status.kind,
      role: status.kind === "error" ? "alert" : "status",
      "aria-live": "polite",
    }, status.text);
  }

  return React.createElement("div", { className: "settings-panel wb-general-settings" },
    SectionTitle(t(p.integrationsOnly ? "settings.integrations" : "settings.general")),
    !p.integrationsOnly && FieldRow(t("settings.language"), t("settings.languageHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (lang === "en" ? " active" : ""), onClick: function () { setLang("en"); } }, "English"),
        React.createElement("button", { className: "wb-seg-btn" + (lang === "zh" ? " active" : ""), onClick: function () { setLang("zh"); } }, "中文"),
      ),
      undefined, "setting-language",
    ),
    !p.integrationsOnly && FieldRow(t("settings.timezone"), t("settings.timezoneHint"),
      React.createElement("select", {
        className: "wb-select",
        value: selectedTimezone,
        "aria-label": t("settings.timezone"),
        onChange: changeTimezone,
      },
        timezoneOptions.map(function (timezone) {
          return React.createElement("option", { key: timezone, value: timezone }, timezoneOptionLabel(timezone));
        }),
      ),
      undefined, "setting-timezone",
    ),
    !p.integrationsOnly && FieldRow(t("settings.desktopNotifications"), t("settings.desktopNotificationsHint"),
      Toggle(desktopNotifications, toggleDesktopNotifications),
      undefined, "setting-desktop-notifications",
    ),
    !p.integrationsOnly && React.cloneElement(SectionBlock(t("settings.agentProxy"), t("settings.agentProxyHint"),
      FieldRow(t("settings.agentProxyEnabled"), t("settings.agentProxyEnabledHint"),
        Toggle(agentProxyEnabled, function () { saveAgentProxy(!agentProxyEnabled, agentProxyPort); }, false, t("settings.agentProxyEnabled")),
      ),
      FieldRow(t("settings.agentProxyPort"), t("settings.agentProxyPortHint"),
        React.createElement("div", { className: "wb-inline-row" },
          React.createElement("input", {
            className: "wb-input",
            type: "number",
            min: "1",
            max: "65535",
            inputMode: "numeric",
            value: agentProxyPort,
            disabled: !agentProxyEnabled,
            "aria-label": t("settings.agentProxyPort"),
            onChange: function (event) { setAgentProxyPort(event.target.value); setAgentProxyStatus(""); },
            onBlur: function () { if (agentProxyEnabled) saveAgentProxy(true, agentProxyPort); },
            onKeyDown: function (event) { if (event.key === "Enter" && agentProxyEnabled) { event.preventDefault(); saveAgentProxy(true, agentProxyPort); } },
          }),
          React.createElement("span", { className: "wb-hint" }, "127.0.0.1:" + (agentProxyPort || "—")),
        ),
        agentProxyStatus && React.createElement("span", { className: "wb-hint saved", role: "status", "aria-live": "polite" }, agentProxyStatus),
      ),
    ), { className: "wb-section-block wb-agent-proxy-settings", id: "setting-agent-proxy" }),
    !p.integrationsOnly && FieldRow(t("settings.mapProvider"), t("settings.mapProviderHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (mapProvider === "direct" ? " active" : ""), onClick: function () { setMapProvider("direct"); localStorage.setItem("cyrene-tweak-map-provider", "direct"); } }, t("settings.mapProviderDirect")),
        React.createElement("button", { className: "wb-seg-btn" + (mapProvider === "amap" ? " active" : ""), onClick: function () { setMapProvider("amap"); } }, t("settings.mapProviderAmap")),
      ),
      undefined, "setting-map-provider",
    ),
    !p.integrationsOnly && mapProvider === "amap" && FieldRow(t("settings.amapKey"), t("settings.amapKeyHint"),
      [
        React.createElement("div", { className: "wb-inline-row" },
          React.createElement("input", { className: "wb-input mono", type: "password", value: amapKey, onChange: function (e) { setAmapKey(e.target.value); }, placeholder: t("settings.amapKeyPlaceholder") }),
          React.createElement("button", { className: "wb-btn primary", onClick: saveAmapKey }, t("settings.save")),
        ),
        amapKeySaved && React.createElement("span", { className: "wb-hint saved" }, amapKeySaved),
      ],
      undefined, "setting-amap-key",
    ),
    !p.integrationsOnly && supportsDesktop && FieldRow(t("settings.runInBackground"), t("settings.runInBackgroundHint"),
      Toggle(runInBackground, function () { applyDesktop({ runInBackground: !runInBackground }); }, desktopBusy),
      undefined, "setting-run-in-background",
    ),
    !p.integrationsOnly && supportsDesktop && FieldRow(t("settings.quickChatAssistant"),
      runInBackground ? t("settings.quickChatAssistantHint") : t("settings.quickChatAssistantNeedsResident"),
      Toggle(quickChatEnabled, function () { applyDesktop({ quickChatEnabled: !quickChatEnabled }); }, desktopBusy || !runInBackground),
      undefined, "setting-quick-chat",
    ),
    !p.integrationsOnly && supportsDesktop && desktopNotice
      && React.createElement("div", { className: "wb-hint", style: { color: "var(--wb-error-text)" } }, desktopNotice),
    p.integrationsOnly && React.cloneElement(SectionBlock(t("settings.zoteroIntegration"), t("settings.zoteroIntegrationHint"),
      FieldRow(t("settings.zoteroLocalApiUrl"), t("settings.zoteroLocalApiUrlHint"),
        React.createElement("div", { className: "wb-integration-control" },
          React.createElement("input", {
            className: "wb-input mono", type: "url", value: zoteroSettings.base_url,
            "aria-label": t("settings.zoteroLocalApiUrl"),
            onChange: function (e) { setZoteroSettings({ ...zoteroSettings, base_url: e.target.value }); },
          }),
        ),
      ),
      FieldRow(t("settings.zoteroAutoSync"), t("settings.zoteroAutoSyncHint"),
        Toggle(zoteroSettings.auto_sync, function () { setZoteroSettings({ ...zoteroSettings, auto_sync: !zoteroSettings.auto_sync }); }, false, t("settings.zoteroAutoSync")),
      ),
      FieldRow(t("settings.zoteroCopyAttachments"), t("settings.zoteroCopyAttachmentsHint"),
        Toggle(zoteroSettings.copy_attachments, function () { setZoteroSettings({ ...zoteroSettings, copy_attachments: !zoteroSettings.copy_attachments }); }, false, t("settings.zoteroCopyAttachments")),
      ),
      FieldRow(
        t("settings.zoteroImport"),
        t("settings.zoteroImportHint", { project: (p.project && p.project.name) || t("settings.zoteroImportNoProjectLabel") }),
        React.createElement("button", {
          className: "wb-btn primary",
          disabled: !!integrationBusy || !(p.project && p.project.id),
          onClick: importFromZotero,
        }, integrationBusy === "import-zotero" ? t("settings.zoteroImporting") : t("settings.zoteroImportAction")),
      ),
      React.createElement("div", { className: "wb-integration-footer" },
        integrationStatus(zoteroStatus),
        React.createElement("div", { className: "wb-integration-actions" },
          React.createElement("button", {
            className: "wb-btn", disabled: !!integrationBusy,
            onClick: testIntegration,
          }, integrationBusy === "test-zotero" ? t("settings.testingConnection") : t("settings.testConnection")),
          React.createElement("button", {
            className: "wb-btn", disabled: !!integrationBusy,
            onClick: saveIntegration,
          }, integrationBusy === "save-zotero" ? t("settings.saving") : t("settings.save")),
        ),
      ),
    ), { id: "setting-zotero" }),
  );
}

// ── Models Panel ──
function EmbeddingSettingsSection(p) {
  var { t, settings, setSettings, apiKey, setApiKey, status, setStatus, busy, setBusy, anchorId } = p;

  function draft() {
    var payload = {
      provider: settings.provider,
      base_url: settings.base_url,
      model: settings.model,
      dimensions: Number(settings.dimensions) || 0,
    };
    if (apiKey.trim()) payload.api_key = apiKey.trim();
    return payload;
  }

  function test() {
    setBusy("test");
    setStatus({ kind: "info", text: t("settings.testingConnection") });
    settingsFetch("/api/settings/integrations/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ service: "embedding", config: draft() }),
    }).then(readSettingsResponse).then(function (payload) {
      setStatus(payload.fallback
        ? { kind: "info", text: t("settings.embeddingLocalFallback") }
        : { kind: "success", text: t("settings.embeddingConnected", { dimensions: payload.dimensions || 0 }) });
    }).catch(function (error) {
      setStatus({ kind: "error", text: t("settings.connectionFailed") + ": " + (error.message || "") });
    }).finally(function () { setBusy(""); });
  }

  function clearApiKey() {
    setBusy("clear");
    settingsFetch("/api/settings/integrations", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ embedding: { clear_api_key: true } }),
    }).then(readSettingsResponse).then(function (payload) {
      if (payload.embedding) setSettings(payload.embedding);
      setApiKey("");
      setStatus({ kind: "success", text: t("settings.embeddingKeyCleared") });
    }).catch(function (error) {
      setStatus({ kind: "error", text: t("settings.error") + ": " + (error.message || "") });
    }).finally(function () { setBusy(""); });
  }

  return ModelSettingsSection({
    title: t("settings.embeddingIntegration"),
    description: t("settings.embeddingIntegrationHint"),
    anchorId: anchorId,
    children: [
      FieldRow(t("settings.embeddingProvider"), t("settings.embeddingProviderHint"),
        React.createElement("select", {
          className: "wb-select", value: settings.provider,
          "aria-label": t("settings.embeddingProvider"),
          onChange: function (e) {
            var provider = e.target.value;
            var nextBase = settings.base_url;
            if (provider === "ollama" && !nextBase) nextBase = "http://127.0.0.1:11434";
            if (provider === "local_onnx") {
              setSettings({ ...settings, provider: provider, base_url: "", model: "qwen3-embedding-0.6b", dimensions: 1024 });
            } else {
              setSettings({ ...settings, provider: provider, base_url: nextBase });
            }
          },
        },
          React.createElement("option", { value: "openai_compatible" }, t("settings.embeddingOpenAiCompatible")),
          React.createElement("option", { value: "ollama" }, "Ollama"),
          React.createElement("option", { value: "local_onnx" }, t("settings.embeddingLocalOnnx")),
        ),
      ),
      settings.provider !== "local_onnx" && FieldRow(t("settings.embeddingBaseUrl"), t("settings.embeddingBaseUrlHint"),
        React.createElement("input", {
          className: "wb-input mono", type: "url", value: settings.base_url,
          placeholder: settings.provider === "ollama" ? "http://127.0.0.1:11434" : "https://api.openai.com/v1",
          "aria-label": t("settings.embeddingBaseUrl"),
          onChange: function (e) { setSettings({ ...settings, base_url: e.target.value }); },
        }),
      ),
      settings.provider !== "local_onnx" && FieldRow(t("settings.embeddingApiKey"), t("settings.embeddingApiKeyHint"),
        React.createElement("div", { className: "wb-integration-control wb-integration-key" },
          React.createElement("input", {
            className: "wb-input mono", type: "password", value: apiKey,
            autoComplete: "off", "aria-label": t("settings.embeddingApiKey"),
            placeholder: settings.api_key_configured ? t("settings.secretConfigured") : t("settings.optionalForLocal"),
            onChange: function (e) { setApiKey(e.target.value); },
          }),
          settings.api_key_configured && React.createElement("button", {
            className: "wb-btn muted", disabled: !!busy, onClick: clearApiKey,
          }, t("settings.clearStoredKey")),
        ),
      ),
      FieldRow(t("settings.embeddingModel"), t("settings.embeddingModelHint"),
        settings.provider === "local_onnx" ? React.createElement("select", {
          className: "wb-select mono", value: settings.model,
          onChange: function (e) { setSettings({ ...settings, model: e.target.value, dimensions: 1024 }); },
        }, React.createElement("option", { value: "qwen3-embedding-0.6b" }, "Qwen3 Embedding 0.6B")) :
        React.createElement("input", {
          className: "wb-input mono", value: settings.model,
          placeholder: "text-embedding-3-small", "aria-label": t("settings.embeddingModel"),
          onChange: function (e) { setSettings({ ...settings, model: e.target.value }); },
        }),
      ),
      FieldRow(t("settings.embeddingDimensions"), t("settings.embeddingDimensionsHint"),
        React.createElement("input", {
          className: "wb-input mono", type: "number", min: "0", max: "65536", step: "1",
          value: settings.dimensions, "aria-label": t("settings.embeddingDimensions"),
          readOnly: settings.provider === "local_onnx",
          onChange: function (e) { setSettings({ ...settings, dimensions: e.target.value }); },
        }),
      ),
      React.createElement("div", { className: "wb-integration-footer" },
        status && React.createElement("div", {
          className: "wb-integration-status " + status.kind,
          role: status.kind === "error" ? "alert" : "status",
          "aria-live": "polite",
        }, status.text),
        React.createElement("div", { className: "wb-integration-actions" },
          React.createElement("button", { className: "wb-btn", disabled: !!busy, onClick: test }, busy === "test" ? t("settings.testingConnection") : t("settings.testConnection")),
        ),
      ),
    ],
  });
}

function modelCredentialFields(model, update, t) {
  if (model.provider === "codex_oauth") {
    return [
      ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", {
        className: "wb-input mono", value: model.model, readOnly: true,
      })),
      React.createElement("div", { className: "wb-codex-provider-note", key: "provider" },
        React.createElement("span", { className: "wb-status-dot good" }),
        React.createElement("span", null, t("settings.openaiOAuthManaged")),
      ),
    ];
  }
  return [
    ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: model.model, onChange: function (e) { update("model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
    ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: model.api_key, onChange: function (e) { update("api_key", e.target.value); }, placeholder: "sk-..." })),
    ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: model.base_url, onChange: function (e) { update("base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
  ];
}

function normalizeLocalModels(models) {
  return (models || []).map(function (item) {
    if (item && item.ready) return { ...item, error: "" };
    return item;
  });
}

function localizeLocalModelError(rawError, t) {
  var lower = String(rawError || "").toLowerCase();
  if (lower.indexOf("archive output is missing or invalid") >= 0
      || lower.indexOf("archive has no declared outputs") >= 0) {
    return t("settings.localModelErrorExtract");
  }
  if (lower.indexOf("checksum") >= 0
      || lower.indexOf("sha256") >= 0
      || lower.indexOf("validation failed") >= 0) {
    return t("settings.localModelErrorChecksum");
  }
  if (lower.indexOf("all mirrors failed") >= 0
      || lower.indexOf("connect") >= 0
      || lower.indexOf("timeout") >= 0
      || lower.indexOf("network") >= 0
      || lower.indexOf("proxy") >= 0
      || lower.indexOf("resolve") >= 0
      || lower.indexOf("httpx") >= 0
      || lower.indexOf("remote protocol") >= 0) {
    return t("settings.localModelErrorNetwork");
  }
  return t("settings.localModelErrorGeneric");
}

function ModelsPanel(p) {
  var { t, models, setModels, modelSource, setModelSource, codexCandidate, setCodexCandidate, draftModel, setDraftModel, visionModels, setVisionModels, draftVision, setDraftVision, secondaryModel, setSecondaryModel, modelsSaved, modelsSaving, saveModels, config, project } = p;
  var [embeddingSettings, setEmbeddingSettings] = useStateSt({ provider: "openai_compatible", base_url: "", model: "", dimensions: 0, api_key_configured: false });
  var [embeddingApiKey, setEmbeddingApiKey] = useStateSt("");
  var [embeddingStatus, setEmbeddingStatus] = useStateSt(null);
  var [embeddingBusy, setEmbeddingBusy] = useStateSt("");
  var [localModels, setLocalModels] = useStateSt([]);
  var [localBusy, setLocalBusy] = useStateSt("");
  var [cv2Runtime, setCv2Runtime] = useStateSt(null);
  var [corpusEmbedding, setCorpusEmbedding] = useStateSt(null);
  var savedEmbeddingIdentityRef = useRefSt("");
  var voiceModelSignatureRef = useRefSt("");
  var workspaceId = String(project && (project.id || project.dataKey) || "");
  var savedCodexCandidate = codexCandidate;
  var [codexState, setCodexState] = useStateSt({
    available: true,
    connected: !!savedCodexCandidate,
    checking: true,
    models: [],
    limits: {},
    quota_enabled: true,
  });
  var [codexModel, setCodexModel] = useStateSt(savedCodexCandidate ? savedCodexCandidate.model : "");
  var [codexEffort, setCodexEffort] = useStateSt(savedCodexCandidate ? savedCodexCandidate.reasoning_effort : "");
  var [codexBusy, setCodexBusy] = useStateSt("");
  var [codexNotice, setCodexNotice] = useStateSt("");
  var [codexCliBusy, setCodexCliBusy] = useStateSt(false);
  var [codexCliProgress, setCodexCliProgress] = useStateSt(null);
  var [primaryMenuOpen, setPrimaryMenuOpen] = useStateSt(false);
  var [hoveredPrimarySource, setHoveredPrimarySource] = useStateSt("");
  var primarySource = modelSource;
  var setPrimarySource = setModelSource;
  var codexPoll = useRefSt(null);
  var codexCliPoll = useRefSt(null);
  var primarySourceRef = useRefSt(null);
  var codexCandidateRef = useRefSt(savedCodexCandidate);
  codexCandidateRef.current = codexCandidate;

  function loadLocalModels() {
    return settingsFetch("/api/settings/local-models/status").then(readSettingsResponse).then(function (payload) {
      var items = normalizeLocalModels(payload.models || []);
      setCv2Runtime(payload.cv2_runtime || null);
      setLocalModels(items);
      var voiceSignature = items.filter(function (item) {
        return item.kind === "asr" || item.kind === "tts";
      }).map(function (item) {
        return item.id + ":" + (item.ready ? "1" : "0");
      }).join("|");
      if (voiceModelSignatureRef.current && voiceModelSignatureRef.current !== voiceSignature) {
        window.dispatchEvent(new Event("cyrene:voice-status-changed"));
      }
      voiceModelSignatureRef.current = voiceSignature;
      return items;
    });
  }

  function loadCorpusEmbedding() {
    return settingsFetch("/api/workbench/knowledge/embedding/status?workspace=" + encodeURIComponent(workspaceId))
      .then(readSettingsResponse).then(function (payload) {
        setCorpusEmbedding(function (previous) {
          if (previous && previous.reembed && previous.reembed.running && payload.reembed && !payload.reembed.running) {
            if (payload.reembed.error) {
              setEmbeddingStatus({ kind: "error", text: t("settings.reembedFailed") + ": " + payload.reembed.error });
            } else {
              setEmbeddingStatus({ kind: "success", text: t("settings.reembedComplete", { count: payload.reembed.updated || 0 }) });
            }
          }
          return payload;
        });
        return payload;
      }).catch(function () {});
  }

  useEffectSt(function () {
    var cancelled = false;
    settingsFetch("/api/settings/integrations").then(readSettingsResponse).then(function (payload) {
      if (!cancelled && payload.embedding) {
        setEmbeddingSettings(payload.embedding);
        savedEmbeddingIdentityRef.current = [payload.embedding.provider, payload.embedding.model].join(":");
      }
    }).catch(function (error) {
      if (!cancelled) setEmbeddingStatus({ kind: "error", text: t("settings.integrationLoadFailed") + ": " + (error.message || "") });
    });
    loadLocalModels().catch(function () {});
    loadCorpusEmbedding();
    var timer = setInterval(function () {
      loadLocalModels().then(function (items) {
        if (!items.some(function (item) { return item.downloading; })) setLocalBusy("");
      }).catch(function () {});
      loadCorpusEmbedding();
    }, 1200);
    return function () { cancelled = true; clearInterval(timer); };
  }, []);

  function embeddingDraft() {
    var payload = {
      provider: embeddingSettings.provider,
      base_url: embeddingSettings.base_url,
      model: embeddingSettings.model,
      dimensions: Number(embeddingSettings.dimensions) || 0,
    };
    if (embeddingApiKey.trim()) payload.api_key = embeddingApiKey.trim();
    return payload;
  }

  function saveAllModels() {
    setEmbeddingStatus(null);
    saveModels(embeddingDraft(), function (saved) {
      var previousIdentity = savedEmbeddingIdentityRef.current;
      if (saved) setEmbeddingSettings(saved);
      var nextIdentity = saved ? [saved.provider, saved.model].join(":") : "";
      savedEmbeddingIdentityRef.current = nextIdentity;
      setEmbeddingApiKey("");
      setEmbeddingStatus(null);
      loadCorpusEmbedding().then(function (coverage) {
        if (!coverage || !coverage.configured || !coverage.pending_vectors) return;
        if (nextIdentity !== "local_onnx:qwen3-embedding-0.6b" || previousIdentity === nextIdentity) return;
        var feedback = window.CyreneUI && window.CyreneUI.require
          ? window.CyreneUI.require("feedback")
          : null;
        var title = t("settings.reembedPromptTitle");
        var body = t("settings.reembedPromptBody", { count: coverage.pending_vectors });
        var confirmed = feedback && typeof feedback.confirmModal === "function"
          ? feedback.confirmModal({ title: title, body: body, confirmLabel: t("settings.reembed") })
          : Promise.resolve(window.confirm([title, "", body].join("\n")));
        confirmed.then(function (ok) { if (ok) reembedKnowledge(); });
      });
    });
  }

  function manageLocalModel(modelId, action) {
    setLocalBusy(modelId + ":" + action);
    var modelRequest = settingsFetch("/api/settings/local-models/" + encodeURIComponent(modelId) + (action === "download" ? "/download" : ""), {
      method: action === "download" ? "POST" : "DELETE",
    }).then(readSettingsResponse);
    // OCR needs the OpenCV runtime too; download both together so the model
    // does not finish while its runtime is still missing. Keep the two
    // downloads independent: a failure in one must not cancel or mask the other.
    var runtimeRequest = action === "download" && modelId === "pp-ocrv6-medium" && cv2Runtime && !cv2Runtime.installed && !cv2Runtime.downloading
      ? settingsFetch("/api/settings/local-models/ocr-runtime/download", { method: "POST" }).then(readSettingsResponse)
      : Promise.resolve(null);
    Promise.allSettled([modelRequest, runtimeRequest]).then(function (results) {
      setLocalBusy("");
      return loadLocalModels().then(function () {
        var modelResult = results[0];
        var runtimeResult = results[1];
        if (modelResult.status === "fulfilled" && runtimeResult.status === "fulfilled") return;
        var messages = [];
        if (modelResult.status === "rejected") {
          messages.push(modelResult.reason && modelResult.reason.message || t("settings.error"));
        }
        if (runtimeResult.status === "rejected") {
          messages.push(modelResult.status === "fulfilled"
            ? t("settings.localModelRuntimeFailed")
            : (runtimeResult.reason && runtimeResult.reason.message || t("settings.ocrRuntimeFailed")));
        }
        setEmbeddingStatus({ kind: "error", text: messages.join(" ") });
      });
    }).catch(function (error) {
      setLocalBusy("");
      setEmbeddingStatus({ kind: "error", text: error.message || t("settings.error") });
    });
  }

  function reembedKnowledge() {
    setEmbeddingStatus({ kind: "info", text: t("settings.reembedding") });
    setCorpusEmbedding(function (previous) {
      return previous ? { ...previous, reembed: { running: true, error: "" } } : previous;
    });
    return settingsFetch("/api/workbench/knowledge/reembed?workspace=" + encodeURIComponent(workspaceId), { method: "POST" })
      .then(readSettingsResponse).then(function () { return loadCorpusEmbedding(); })
      .catch(function (error) {
        setEmbeddingStatus({ kind: "error", text: t("settings.reembedFailed") + ": " + (error.message || "") });
      });
  }

  function LocalModelIcon(kind) {
    if (kind === "asr" || kind === "tts") {
      var BrowserIcon = window.CyreneUI.require("browser").Icon;
      return React.createElement(BrowserIcon, {
        name: kind === "asr" ? "microphone" : "volume",
        size: 20,
      });
    }
    if (kind === "embedding") {
      return React.createElement("svg", {
        width: "20", height: "20", viewBox: "0 0 24 24", fill: "none",
        stroke: "currentColor", strokeWidth: "1.8", strokeLinecap: "round",
        strokeLinejoin: "round", "aria-hidden": "true",
      },
        React.createElement("circle", { cx: "6", cy: "6", r: "2.25" }),
        React.createElement("circle", { cx: "18", cy: "6", r: "2.25" }),
        React.createElement("circle", { cx: "12", cy: "18", r: "2.25" }),
        React.createElement("path", { d: "m7.9 7.2 2.7 8.6M16.1 7.2l-2.7 8.6M8.3 6h7.4" }),
      );
    }
    return React.createElement("svg", {
      width: "20", height: "20", viewBox: "0 0 24 24", fill: "none",
      stroke: "currentColor", strokeWidth: "1.8", strokeLinecap: "round",
      strokeLinejoin: "round", "aria-hidden": "true",
    },
      React.createElement("path", { d: "M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" }),
      React.createElement("path", { d: "M8 10h8M8 14h6" }),
    );
  }

  useEffectSt(function () {
    if (!primaryMenuOpen) return;
    function closePrimaryMenu(event) {
      if (primarySourceRef.current && !primarySourceRef.current.contains(event.target)) {
        setPrimaryMenuOpen(false);
        setHoveredPrimarySource("");
      }
    }
    function closePrimaryMenuOnEscape(event) {
      if (event.key === "Escape") {
        setPrimaryMenuOpen(false);
        setHoveredPrimarySource("");
      }
    }
    document.addEventListener("pointerdown", closePrimaryMenu, true);
    document.addEventListener("keydown", closePrimaryMenuOnEscape);
    return function () {
      document.removeEventListener("pointerdown", closePrimaryMenu, true);
      document.removeEventListener("keydown", closePrimaryMenuOnEscape);
    };
  }, [primaryMenuOpen]);

  useEffectSt(function () {
    var saved = codexCandidate;
    if (!saved) return;
    setCodexModel(saved.model || "");
    setCodexEffort(saved.reasoning_effort || "");
    setCodexState(function (previous) {
      return { ...previous, connected: true };
    });
  }, [codexCandidate]);

  function loadCodexState() {
    return settingsFetch("/api/settings/openai-oauth")
      .then(readSettingsResponse)
      .then(function (data) {
        setCodexState({ ...data, checking: false });
        try { window.dispatchEvent(new CustomEvent("cyrene:codex-auth-changed", { detail: data })); } catch (e) {}
        var options = data.models || [];
        var saved = codexCandidateRef.current;
        var savedModel = saved && saved.model || "";
        var selected = options.find(function (item) { return codexModelId(item) === savedModel; });
        var preferred = selected || options.find(function (item) { return item.isDefault || item.is_default; }) || options[0];
        if (preferred) {
          setCodexModel(savedModel || codexModelId(preferred));
          setCodexEffort(saved && saved.reasoning_effort || String(preferred.defaultReasoningEffort || preferred.default_reasoning_effort || ""));
        }
        return data;
      })
      .catch(function (error) {
        setCodexState(function (previous) {
          return { ...previous, available: false, checking: false, models: [], error: error.message };
        });
      });
  }

  useEffectSt(function () {
    loadCodexState();
    return function () {
      if (codexPoll.current) clearInterval(codexPoll.current);
      if (codexCliPoll.current) clearInterval(codexCliPoll.current);
    };
  }, []);

  function startCodexLogin() {
    setCodexBusy("login"); setCodexNotice("");
    settingsFetch("/api/settings/openai-oauth/login", { method: "POST" })
      .then(readSettingsResponse)
      .then(function (data) {
        var authUrl = data.authUrl || data.auth_url || data.url;
        if (authUrl) window.open(authUrl, "_blank", "noopener,noreferrer");
        if (codexPoll.current) clearInterval(codexPoll.current);
        codexPoll.current = pollUntil(function () {
          return loadCodexState().then(function (state) {
            return state && state.connected ? { done: true } : null;
          });
        }, {
          intervalMs: 1500,
          onDone: function () {
            codexPoll.current = null;
            setCodexBusy(""); setCodexNotice(t("settings.openaiOAuthConnected"));
          },
          onError: function (error) {
            codexPoll.current = null;
            setCodexBusy(""); setCodexNotice(String(error && error.message || error || ""));
          },
        });
      })
      .catch(function (error) { setCodexBusy(""); setCodexNotice(error.message); });
  }

  function downloadCodexCli(force) {
    setCodexCliBusy(true); setCodexNotice("");
    var init = { method: "POST" };
    if (force) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify({ force: true });
    }
    settingsFetch("/api/settings/openai-oauth/cli/download", init)
      .then(readSettingsResponse)
      .then(function () {
        if (codexCliPoll.current) clearInterval(codexCliPoll.current);
        codexCliPoll.current = pollUntil(function () {
          return settingsFetch("/api/settings/openai-oauth/cli")
            .then(readSettingsResponse)
            .then(function (cli) {
              setCodexCliProgress(cli.installed ? null : {
                downloaded_bytes: cli.downloaded_bytes || 0,
                total_bytes: cli.total_bytes || 0,
              });
              // Priority: an in-flight download wins over any stale error
              // that the backend has not reset yet.
              if (cli.downloading) return null;
              if (cli.installed) return { done: true };
              if (cli.error) return { done: true, error: cli.error };
              return null;
            });
        }, {
          intervalMs: 1000,
          timeoutMs: 600000,
          onDone: function () {
            codexCliPoll.current = null;
            setCodexCliBusy(false);
            setCodexNotice(t("settings.codexCliReady"));
            return loadCodexState();
          },
          onError: function (error) {
            codexCliPoll.current = null;
            setCodexCliBusy(false);
            setCodexNotice(error && error.code === "poll_timeout"
              ? t("settings.codexCliDownloadTimeout")
              : String(error && error.message || error || ""));
          },
        });
      })
      .catch(function (error) {
        setCodexCliBusy(false);
        setCodexNotice(error.message || "");
      });
  }

  function logoutCodex() {
    setCodexBusy("logout"); setCodexNotice("");
    settingsFetch("/api/settings/openai-oauth/logout", { method: "POST" })
      .then(readSettingsResponse)
      .then(function () { setCodexBusy(""); setCodexModel(""); return loadCodexState(); })
      .catch(function (error) { setCodexBusy(""); setCodexNotice(error.message); });
  }

  function setCodexPrimaryCandidate(selectedModel, selectedEffort) {
    var targetModel = selectedModel || codexModel;
    var targetEffort = selectedEffort != null ? selectedEffort : codexEffort;
    if (!targetModel) return;
    setCodexCandidate(normalizeModel({
      id: "codex-" + targetModel,
      model: targetModel,
      desc: "OpenAI OAuth",
      price: t("settings.codexQuota"),
      provider: "codex_oauth",
      reasoning_effort: targetEffort,
      api_key: "",
      base_url: "codex://oauth",
    }, 0, "", ""));
    setCodexNotice(t("settings.openaiOAuthPrimaryReady"));
  }

  function selectCustomPrimary() {
    setPrimaryMenuOpen(false); setPrimarySource("custom");
  }

  function selectCodexPrimary() {
    setPrimaryMenuOpen(false); setPrimarySource("codex");
    if (codexState.connected && codexModel) setCodexPrimaryCandidate();
  }

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

  var fallbackCount = Math.max(0, models.length - 1);
  var visionFallbackCount = Math.max(0, visionModels.length - 1);
  var secondaryConfigured = !!String(secondaryModel && secondaryModel.model || "").trim();
  var visionConfigured = !!String(visionModels[0] && visionModels[0].model || "").trim();
  var codexModelOptions = codexModelSelectOptions(codexState.models, codexModel);
  var selectedCodexModel = codexModelOptions.find(function (item) { return codexModelId(item) === codexModel; });
  var codexEffortOptions = codexModelReasoningEfforts(selectedCodexModel, codexEffort);
  var codexCliRequired = !!(codexState.cli && (!codexState.cli.installed || codexState.cli.broken));
  var codexCliDownloading = !!(codexCliBusy || (codexState.cli && codexState.cli.downloading));
  var codexCliPercent = downloadPercent(codexCliProgress);

  return React.createElement("div", { className: "settings-panel wb-models-panel" },
    SectionTitle(t("settings.models"), t("settings.modelsSubtitle")),

    // Primary model
    ModelSettingsSection({
      title: t("settings.primaryModelSlot"),
      anchorId: "setting-model-source",
      headerAction: React.createElement("div", { className: "wb-primary-source", ref: primarySourceRef },
        React.createElement("button", {
          className: "wb-primary-source-trigger",
          onClick: function () { setPrimaryMenuOpen(!primaryMenuOpen); },
          "aria-expanded": primaryMenuOpen,
        },
          React.createElement("span", null, primarySource === "codex" ? "OpenAI OAuth" : t("settings.customModel")),
        ),
        primaryMenuOpen && React.createElement("div", {
          className: "wb-primary-source-menu",
          onMouseLeave: function () { setHoveredPrimarySource(""); },
        },
          React.createElement("button", {
            className: "wb-menu-item" + (!hoveredPrimarySource && primarySource === "custom" ? " active" : ""),
            onMouseEnter: function () { setHoveredPrimarySource("custom"); },
            onClick: selectCustomPrimary,
          },
            React.createElement("strong", null, t("settings.customModel")),
            React.createElement("small", null, t("settings.customModelHint")),
          ),
          React.createElement("button", {
            className: "wb-menu-item" + (!hoveredPrimarySource && primarySource === "codex" ? " active" : ""),
            onMouseEnter: function () { setHoveredPrimarySource("codex"); },
            onClick: selectCodexPrimary,
          },
            React.createElement("strong", null, "OpenAI OAuth"),
            React.createElement("small", null, codexState.connected ? t("settings.openaiOAuthConnected") : t("settings.openaiOAuthNotConnected")),
          ),
        ),
      ),
      className: "is-primary" + (primaryMenuOpen ? " is-menu-open" : ""),
      children: [
        primarySource === "custom" && models[0] && ModelCard([
        ...modelCredentialFields(models[0], function (field, value) { updateModel(models[0].id, field, value); }, t),
        React.createElement("div", { className: "wb-model-meta" },
          React.createElement("div", null, React.createElement("small", null, t("settings.descriptionLabel")), React.createElement("input", { className: "wb-input mono small", value: models[0].desc, onChange: function (e) { updateModel(models[0].id, "desc", e.target.value); }, placeholder: t("settings.placeholderDesc") })),
          React.createElement("div", null, React.createElement("small", null, t("settings.contextLabel")), React.createElement("input", { className: "wb-input mono small", value: models[0].ctx, onChange: function (e) { updateModel(models[0].id, "ctx", e.target.value); }, placeholder: t("settings.placeholderCtx") })),
          React.createElement("div", null, React.createElement("small", null, t("settings.priceLabel")), React.createElement("input", { className: "wb-input mono small", value: models[0].price, onChange: function (e) { updateModel(models[0].id, "price", e.target.value); }, placeholder: models[0].priceHint || t("settings.placeholderPrice") })),
        ),
        ]),
        primarySource === "codex" && React.createElement("div", { className: "wb-codex-auth" },
          React.createElement("div", { className: "wb-codex-auth-main" },
            React.createElement("div", { className: "wb-codex-auth-copy" },
              React.createElement("strong", null, codexState.connected
                ? String(codexState.account && (codexState.account.email || codexState.account.planType || codexState.account.plan_type) || "OpenAI")
                : t("settings.openaiOAuthTitle")),
              React.createElement("span", null, codexState.connected ? t("settings.openaiOAuthConnectedHint") : t("settings.openaiOAuthHint")),
            ),
            !codexState.connected && !codexCliRequired && React.createElement("button", {
              className: "wb-btn primary", disabled: !!codexBusy || codexState.available === false, onClick: startCodexLogin,
            }, codexBusy === "login" ? t("settings.openaiOAuthWaiting") : t("settings.openaiOAuthLogin")),
            !codexState.connected && codexCliRequired && React.createElement("div", { className: "wb-codex-cli-required" },
              React.createElement("span", { className: "wb-codex-cli-hint" }, t("settings.codexCliRequiredHint")),
              React.createElement("div", { className: "wb-codex-cli-download" },
                codexCliDownloading
                  ? React.createElement("span", { className: "wb-codex-cli-progress" }, t("settings.codexCliDownloading") + (codexCliPercent ? " " + codexCliPercent + "%" : ""))
                  : React.createElement("button", {
                    className: "wb-btn primary",
                    onClick: function () { downloadCodexCli(!!(codexState.cli && codexState.cli.broken)); },
                  }, codexState.cli && codexState.cli.broken ? t("settings.codexCliRedownload") : t("settings.codexCliDownload")),
              ),
            ),
            codexState.connected && React.createElement("button", { className: "wb-btn muted", disabled: !!codexBusy, onClick: logoutCodex }, t("settings.openaiOAuthLogout")),
          ),
          codexState.connected && React.createElement("div", { className: "wb-codex-model-picker" },
            React.createElement("label", null,
              React.createElement("small", null, t("settings.openaiOAuthModel")),
              React.createElement("select", {
                className: "wb-select mono", value: codexModel,
                onChange: function (e) {
                  var value = e.target.value;
                  var selected = (codexState.models || []).find(function (item) { return codexModelId(item) === value; });
                  var effort = String(selected && (selected.defaultReasoningEffort || selected.default_reasoning_effort) || "");
                  setCodexModel(value);
                  setCodexEffort(effort);
                  setCodexPrimaryCandidate(value, effort);
                },
              }, codexModelOptions.map(function (item) {
                var id = codexModelId(item);
                return React.createElement("option", { key: id, value: id }, item.displayName || item.display_name || id);
              })),
            ),
            React.createElement("label", null,
              React.createElement("small", null, t("settings.reasoningEffort")),
              React.createElement("select", {
                className: "wb-select", value: codexEffort,
                onChange: function (e) {
                  var value = e.target.value;
                  setCodexEffort(value);
                  setCodexPrimaryCandidate(codexModel, value);
                },
              }, codexEffortOptions.map(function (effort) {
                return React.createElement("option", { key: effort, value: effort }, t("settings.reasoningEffortValue." + effort));
              })),
            ),
          ),
          codexNotice && React.createElement("p", { className: "wb-hint" }, codexNotice),
        ),
      ],
    }),

    // Fallback candidates
    ModelSettingsSection({
      title: t("settings.fallbackCandidates"),
      status: fallbackCount
        ? t("settings.modelStatusCount", { count: fallbackCount })
        : t("settings.modelStatusNone"),
      collapsible: true,
      children: [
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
          ...modelCredentialFields(m, function (field, value) { updateModel(m.id, field, value); }, t),
        ], m.id);
      }),
      !fallbackCount && React.createElement("p", { className: "wb-model-empty" }, t("settings.modelFallbackEmpty")),
      modelDraftField(draftModel, setDraftModel, addModel, t),
      ],
    }),

    ModelSettingsSection({
      title: t("settings.secondaryModelSlot"),
      status: secondaryConfigured ? t("settings.modelStatusConfigured") : t("settings.modelStatusNotConfigured"),
      description: t("settings.secondaryModelHint"),
      anchorId: "setting-secondary-model",
      collapsible: true,
      children: [
      secondaryModel && ModelCard([
        ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: secondaryModel.model, onChange: function (e) { updateSecondary("model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
        ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: secondaryModel.api_key, onChange: function (e) { updateSecondary("api_key", e.target.value); }, placeholder: "sk-..." })),
        ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: secondaryModel.base_url, onChange: function (e) { updateSecondary("base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
        React.createElement("div", { className: "wb-model-meta" },
          React.createElement("div", null, React.createElement("small", null, t("settings.secondaryModelCtxLimit")), React.createElement("input", { className: "wb-input mono small", type: "number", min: "0", value: secondaryModel.ctx_limit, onChange: function (e) { updateSecondary("ctx_limit", e.target.value); }, placeholder: "0" })),
          React.createElement("div", null, React.createElement("small", null, t("settings.secondaryModelConcurrency")), React.createElement("input", { className: "wb-input mono small", type: "number", min: "0", value: secondaryModel.max_concurrency, onChange: function (e) { updateSecondary("max_concurrency", e.target.value); }, placeholder: "0" })),
        ),
      ]),
      ],
    }),

    // Vision model
    ModelSettingsSection({
      title: t("settings.visionModelSlot"),
      status: visionConfigured
        ? t("settings.modelStatusConfiguredWithCount", { count: visionFallbackCount })
        : t("settings.modelStatusNotConfigured"),
      anchorId: "setting-vision-model",
      collapsible: true,
      children: [
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
      modelDraftField(draftVision, setDraftVision, addVisionModel, t),
      ],
    }),

    React.createElement(EmbeddingSettingsSection, {
      t: t, settings: embeddingSettings, setSettings: setEmbeddingSettings,
      apiKey: embeddingApiKey, setApiKey: setEmbeddingApiKey,
      status: embeddingStatus, setStatus: setEmbeddingStatus,
      busy: embeddingBusy, setBusy: setEmbeddingBusy,
      anchorId: "setting-embedding",
    }),
    ModelSettingsSection({
      title: t("settings.localModels"),
      description: t("settings.localModelsHint"),
      className: "is-local-models",
      children: localModels.map(function (item) {
        var percent = downloadPercent(item);
        var cv2RuntimeMissing = item.id === "pp-ocrv6-medium" && cv2Runtime && !cv2Runtime.installed;
        var cv2RuntimePercent = downloadPercent(cv2Runtime);
        var kind = item.kind || "model";
        var localCopy = {
          "qwen3-embedding-0.6b": ["settings.localEmbeddingTitle", "settings.localQwenName", "settings.localQwenHint"],
          "pp-ocrv6-medium": ["settings.localOcrTitle", "settings.localOcrName", "settings.localOcrHint"],
          "fireredasr2-aed-int8": ["settings.localAsrTitle", "settings.localFireRedName", "settings.localFireRedHint"],
          "kokoro-zh-en": ["settings.localTtsTitle", "settings.localKokoroName", "settings.localKokoroHint"],
          "zipvoice-zh-en": ["settings.localTtsTitle", "settings.localZipVoiceName", "settings.localZipVoiceHint"],
        }[item.id];
        var displayTitle = localCopy ? t(localCopy[0]) : item.kind;
        var displayName = localCopy ? t(localCopy[1]) : item.name;
        var displayDescription = localCopy ? t(localCopy[2]) : item.description;
        var runtime = String(item.runtime || "onnx").toLowerCase();
        var runtimeLabel = runtime === "onnx-cpu" ? "CPU" : runtime.toUpperCase();
        var runtimeClass = runtime.indexOf("cuda") >= 0 || runtime.indexOf("directml") >= 0 || runtime.indexOf("qnn") >= 0
          ? " is-cuda"
          : runtime.indexOf("mlx") >= 0 ? " is-mlx" : " is-onnx";
        var hasError = !item.ready && !!item.error;
        var statusText = hasError
          ? t("settings.localModelError")
          : item.ready
            ? t("settings.localModelActive", { runtime: runtimeLabel })
            : item.downloading
              ? t("settings.localModelDownloading", { percent: percent })
              : t("settings.localModelOptional");
        return React.createElement("article", { className: "wb-model-card wb-local-model" + (item.ready ? " is-ready" : " is-optional"), key: item.id },
          React.createElement("span", { className: "wb-local-model-icon is-" + kind }, LocalModelIcon(kind)),
          React.createElement("div", { className: "wb-local-model-copy" },
            React.createElement("span", { className: "wb-local-model-heading" },
              React.createElement("strong", null, displayTitle),
              React.createElement("span", { className: "wb-local-model-name" }, displayName),
            ),
            React.createElement("small", null, displayDescription),
            item.downloading && React.createElement("div", { className: "wb-local-model-progress" },
              React.createElement("progress", { max: "100", value: percent, "aria-label": t("settings.localModelDownloading", { percent: percent }) }),
              React.createElement("span", null, percent + "%"),
            ),
            cv2RuntimeMissing && React.createElement("small", {
              className: "wb-local-model-runtime" + (cv2Runtime.error ? " wb-local-model-error" : ""),
            },
              cv2Runtime.downloading
                ? t("settings.ocrRuntimeDownloading", { percent: cv2RuntimePercent })
                : cv2Runtime.error
                  ? t("settings.ocrRuntimeFailed") + ": " + cv2Runtime.error
                  : t("settings.ocrRuntimeBundled")),
            hasError && React.createElement("small", { className: "wb-local-model-error" }, localizeLocalModelError(item.error, t)),
          ),
          React.createElement("div", { className: "wb-local-model-actions" },
            React.createElement("span", { className: "wb-model-status" + (hasError ? " is-error" : item.ready ? " wb-runtime-badge" + runtimeClass : ""), role: "status" },
              React.createElement("span", { className: "wb-local-model-status-dot", "aria-hidden": "true" }),
              statusText,
            ),
            React.createElement("button", {
              type: "button",
              className: "wb-btn compact " + (item.ready ? "danger" : "tonal"),
              disabled: !!localBusy || item.downloading,
              "aria-label": (item.ready ? t("settings.delete") : hasError ? t("settings.retry") : t("settings.download")) + " " + displayName,
              onClick: function () { manageLocalModel(item.id, item.ready ? "delete" : "download"); },
            }, item.ready ? t("settings.delete") : hasError ? t("settings.retry") : t("settings.download")),
          ),
        );
      }).concat(corpusEmbedding && corpusEmbedding.mismatch ? [
        React.createElement("div", { className: "wb-integration-status error", key: "mismatch" },
          React.createElement("span", null, t("settings.embeddingMismatch", { count: corpusEmbedding.pending_vectors || 0 })),
          React.createElement("button", { className: "wb-btn", disabled: corpusEmbedding.reembed && corpusEmbedding.reembed.running, onClick: reembedKnowledge },
            corpusEmbedding.reembed && corpusEmbedding.reembed.running ? t("settings.reembedding") : t("settings.reembed")),
        ),
      ] : []),
    }),
    React.createElement("div", { className: "wb-save-actions" },
      modelsSaved && React.createElement("span", {
        className: "wb-hint saved" + (modelsSaving ? " is-saving" : ""),
        role: "status",
        "aria-live": "polite",
      },
        modelsSaving && React.createElement("span", { className: "wb-spinner", "aria-hidden": "true" }),
        React.createElement("span", null, modelsSaved),
      ),
      React.createElement("button", { className: "wb-btn primary", onClick: saveAllModels, disabled: modelsSaving || !!embeddingBusy }, t("settings.saveApply")),
    ),
  );
}

function modelDraftField(draft, setDraft, onAdd, t) {
  return React.createElement("div", { className: "wb-model-draft" },
    React.createElement("label", null,
      React.createElement("small", null, t("settings.modelIdentifierLabel")),
      React.createElement("input", { className: "wb-input mono", value: draft.model, onChange: function (e) { setDraft({ ...draft, model: e.target.value, name: e.target.value }); }, placeholder: t("settings.placeholderModelIdentifier") }),
    ),
    React.createElement("label", null,
      React.createElement("small", null, t("settings.apiKey")),
      React.createElement("input", { className: "wb-input mono", type: "password", value: draft.api_key, onChange: function (e) { setDraft({ ...draft, api_key: e.target.value }); }, placeholder: "sk-..." }),
    ),
    React.createElement("label", null,
      React.createElement("small", null, t("settings.baseUrlLabel")),
      React.createElement("input", { className: "wb-input mono", value: draft.base_url, onChange: function (e) { setDraft({ ...draft, base_url: e.target.value }); }, placeholder: DEFAULT_MODEL_BASE_URL }),
    ),
    React.createElement("button", { className: "wb-btn", onClick: onAdd }, t("settings.add")),
  );
}

function ModelSettingsSection(options) {
  var body = React.createElement("div", { className: "wb-model-section-body" }, ...(options.children || []));
  var headerContent = [
    React.createElement("div", { className: "wb-model-section-title", key: "title" },
      React.createElement("b", null, options.title),
      options.description && React.createElement("small", null, options.description),
    ),
    options.status && React.createElement("span", { className: "wb-model-status", key: "status" }, options.status),
    options.headerAction && React.createElement("div", { className: "wb-model-header-action", key: "action" }, options.headerAction),
  ];
  var sectionProps = {
    className: "wb-model-section" + (options.className ? " " + options.className : ""),
  };
  if (options.anchorId) sectionProps.id = options.anchorId;
  if (options.collapsible) {
    return React.createElement("details", sectionProps,
      React.createElement("summary", { className: "wb-model-section-head" }, ...headerContent),
      body,
    );
  }
  return React.createElement("section", sectionProps,
    React.createElement("div", { className: "wb-model-section-head" }, ...headerContent),
    body,
  );
}

// ── Channels Panel ──
function ChannelsPanel(p) {
  var { t, telegramToken, setTelegramToken, telegramSaved, setTelegramSaved, notifyTelegram, setNotifyTelegram, notifyWechat, setNotifyWechat } = p;

  function saveTelegram() {
    if (!telegramToken || telegramToken.startsWith("••")) { showSettingsToast(t("settings.noChanges"), "info"); return; }
    setTelegramSaved(t("settings.saving"));
    settingsFetch("/api/settings/keys", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ TELEGRAM_BOT_TOKEN: telegramToken }) })
      .then(function () { setTelegramSaved(""); showSettingsToast(t("settings.saved"), "success"); })
      .catch(function (error) { setTelegramSaved(""); showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error"); });
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
        [
          React.createElement("div", { className: "wb-inline-row" },
            React.createElement("input", { className: "wb-input mono", type: "password", value: telegramToken, onChange: function (e) { setTelegramToken(e.target.value); }, placeholder: t("settings.placeholderOptional") }),
            React.createElement("button", { className: "wb-btn primary", onClick: saveTelegram }, t("settings.saveNotification")),
          ),
          telegramSaved && React.createElement("span", { className: "wb-hint saved" }, telegramSaved),
        ],
        undefined, "setting-telegram",
      ),
      FieldRow(t("settings.notifyTelegram"), t("settings.notifyTelegramHint"),
        Toggle(notifyTelegram, function () {
          var next = !notifyTelegram;
          setNotifyTelegram(next);
          settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notify_telegram: next }) }).catch(function () { setNotifyTelegram(!next); });
        }),
      ),
    ),

    React.createElement(WeChatConnectionPanel, { t, notifyWechat, setNotifyWechat, anchorId: "setting-wechat" }),
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
    return settingsFetch("/api/wechat/status")
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
    settingsFetch("/api/wechat/poll-login", {
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
      return settingsFetch("/api/wechat/start", { method: "POST" })
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
    settingsFetch("/api/wechat/qr-login", { method: "POST" })
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
    settingsFetch("/api/wechat/start", { method: "POST" })
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
    settingsFetch("/api/wechat/stop", { method: "POST" })
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

  return React.createElement("div", { className: "wb-channel-card wb-wechat-card", id: p.anchorId || undefined },
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
      settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notify_wechat: next }) }).catch(function () { setNotifyWechat(!next); });
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
    React.createElement("div", { className: "wb-field wb-field-stack wb-field-soul", id: "setting-soul" },
      React.createElement("div", { className: "wb-label" }, t("settings.soulMd"), React.createElement("small", null, t("settings.soulMdHint"))),
      React.createElement("textarea", { className: "wb-textarea mono wb-textarea-soul", value: soulDraft, onChange: function (e) { setSoulDraft(e.target.value); } }),
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
      undefined, "setting-spawn-policy",
    ),
    FieldRow(t("settings.agentProactive"), t("settings.agentProactiveHint"), Toggle(agentProactive, function () { setAgentProactive(!agentProactive); }),
      undefined, "setting-agent-proactive"),
    FieldRow(t("settings.heartbeatInterval"), t("settings.heartbeatIntervalHint"),
      React.createElement("input", { className: "wb-input mono", type: "number", min: "60", step: "1", value: config.heartbeat_interval, onChange: function (e) { setConfig({ ...config, heartbeat_interval: e.target.value }); }, style: { maxWidth: 120 } }),
      undefined, "setting-heartbeat",
    ),
    React.createElement("div", { className: "wb-save-actions" },
      React.createElement("button", { className: "wb-btn primary", onClick: saveAgents }, t("settings.saveApply")),
    ),
  );
}

// ── Appearance Panel ──
function normalizeAccentHex(value) {
  var next = String(value || "").trim();
  if (!next) return "";
  if (next[0] !== "#") next = "#" + next;
  if (/^#[0-9a-f]{3}$/i.test(next)) {
    next = "#" + next.slice(1).split("").map(function (char) { return char + char; }).join("");
  }
  return /^#[0-9a-f]{6}$/i.test(next) ? next.toUpperCase() : "";
}

function hexToAccentHsv(value) {
  var hex = normalizeAccentHex(value) || "#E5488B";
  var red = parseInt(hex.slice(1, 3), 16) / 255;
  var green = parseInt(hex.slice(3, 5), 16) / 255;
  var blue = parseInt(hex.slice(5, 7), 16) / 255;
  var max = Math.max(red, green, blue);
  var min = Math.min(red, green, blue);
  var delta = max - min;
  var hue = 0;
  if (delta) {
    if (max === red) hue = 60 * (((green - blue) / delta) % 6);
    else if (max === green) hue = 60 * (((blue - red) / delta) + 2);
    else hue = 60 * (((red - green) / delta) + 4);
  }
  if (hue < 0) hue += 360;
  return {
    h: Math.round(hue),
    s: max ? Math.round((delta / max) * 100) : 0,
    v: Math.round(max * 100),
  };
}

function accentHsvToHex(hue, saturation, value) {
  var h = ((Number(hue) % 360) + 360) % 360;
  var s = Math.max(0, Math.min(100, Number(saturation))) / 100;
  var v = Math.max(0, Math.min(100, Number(value))) / 100;
  var chroma = v * s;
  var x = chroma * (1 - Math.abs((h / 60) % 2 - 1));
  var m = v - chroma;
  var red = 0, green = 0, blue = 0;
  if (h < 60) { red = chroma; green = x; }
  else if (h < 120) { red = x; green = chroma; }
  else if (h < 180) { green = chroma; blue = x; }
  else if (h < 240) { green = x; blue = chroma; }
  else if (h < 300) { red = x; blue = chroma; }
  else { red = chroma; blue = x; }
  return "#" + [red, green, blue].map(function (channel) {
    return Math.round((channel + m) * 255).toString(16).padStart(2, "0");
  }).join("").toUpperCase();
}

function ColorPickerPopover(p) {
  var { t, value, defaultValue, onApply, onReset, onClose, ariaLabel } = p;
  var current = normalizeAccentHex(value) || defaultValue;
  var [draft, setDraft] = useStateSt(current);
  var [hsv, setHsv] = useStateSt(function () { return hexToAccentHsv(current); });

  useEffectSt(function () {
    setDraft(current);
    setHsv(hexToAccentHsv(current));
  }, [current]);

  function updateDraft(next) {
    var normalized = normalizeAccentHex(next);
    setDraft(next);
    if (normalized) {
      setDraft(normalized);
      setHsv(hexToAccentHsv(normalized));
    }
  }

  function updateFromHsv(next) {
    setHsv(next);
    setDraft(accentHsvToHex(next.h, next.s, next.v));
  }

  function updatePlane(event) {
    var rect = event.currentTarget.getBoundingClientRect();
    var saturation = Math.round(Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * 100);
    var brightness = Math.round((1 - Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))) * 100);
    updateFromHsv({ h: hsv.h, s: saturation, v: brightness });
  }

  function updateHue(event) {
    var rect = event.currentTarget.getBoundingClientRect();
    var ratio = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    updateFromHsv({ h: Math.round(ratio * 359), s: hsv.s, v: hsv.v });
  }

  function applyDraft() {
    var next = normalizeAccentHex(draft);
    if (!next) return;
    onApply(next);
    onClose();
  }

  function resetDraft() {
    onReset();
    onClose();
  }

  return React.createElement("div", {
    className: "wb-accent-popover",
    role: "dialog",
    "aria-label": ariaLabel || t("settings.customColor"),
    onKeyDown: function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }
    },
  },
    React.createElement("div", { className: "wb-accent-popover-body" },
      React.createElement("div", { className: "wb-accent-picker-visuals" },
        React.createElement("div", {
          className: "wb-accent-sv",
          style: { "--picker-hue": "hsl(" + hsv.h + " 100% 50%)" },
          onPointerDown: function (event) {
            event.currentTarget.setPointerCapture(event.pointerId);
            updatePlane(event);
          },
          onPointerMove: function (event) {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) updatePlane(event);
          },
          role: "slider",
          tabIndex: 0,
          "aria-label": t("settings.colorSaturationBrightness"),
          "aria-valuetext": draft,
        }, React.createElement("span", {
          className: "wb-accent-sv-thumb",
          style: { left: hsv.s + "%", top: (100 - hsv.v) + "%" },
        })),
        React.createElement("div", {
          className: "wb-accent-hue",
          role: "slider",
          tabIndex: 0,
          "aria-label": t("settings.colorHue"),
          "aria-valuemin": "0",
          "aria-valuemax": "359",
          "aria-valuenow": String(hsv.h),
          onPointerDown: function (event) {
            event.currentTarget.setPointerCapture(event.pointerId);
            updateHue(event);
          },
          onPointerMove: function (event) {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) updateHue(event);
          },
          onKeyDown: function (event) {
            if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
            event.preventDefault();
            var delta = event.key === "ArrowUp" ? -1 : 1;
            updateFromHsv({
              h: Math.max(0, Math.min(359, hsv.h + delta)),
              s: hsv.s,
              v: hsv.v,
            });
          },
        }, React.createElement("span", {
          className: "wb-accent-hue-thumb",
          style: { top: (hsv.h / 359 * 100) + "%" },
        })),
      ),
      React.createElement("div", { className: "wb-accent-picker-fields" },
        React.createElement("div", { className: "wb-accent-preview-row" },
          React.createElement("span", null, t("settings.currentColor")),
          React.createElement("span", { className: "wb-accent-preview-dot", style: { "--swatch": current } }),
          React.createElement("code", null, current),
        ),
        React.createElement("div", { className: "wb-accent-preview-row" },
          React.createElement("span", null, t("settings.newColor")),
          React.createElement("span", { className: "wb-accent-preview-dot", style: { "--swatch": normalizeAccentHex(draft) || current } }),
          React.createElement("code", null, normalizeAccentHex(draft) || "—"),
        ),
        React.createElement("label", { className: "wb-accent-hex-field" },
          React.createElement("span", null, "HEX"),
          React.createElement("input", {
            value: draft,
            maxLength: 7,
            spellCheck: false,
            onChange: function (event) { updateDraft(event.target.value); },
            onKeyDown: function (event) { if (event.key === "Enter") applyDraft(); },
            "aria-invalid": normalizeAccentHex(draft) ? "false" : "true",
          }),
        ),
        React.createElement("input", {
          className: "wb-accent-native-input",
          type: "color",
          value: normalizeAccentHex(draft) || current,
          onChange: function (event) { updateDraft(event.target.value); },
          "aria-label": t("settings.openSystemColorPicker"),
        }),
      ),
    ),
    React.createElement("div", { className: "wb-accent-popover-actions" },
      React.createElement("button", { type: "button", className: "wb-btn muted", onClick: resetDraft }, t("settings.restoreDefault")),
      React.createElement("div", { className: "wb-accent-popover-actions-end" },
        React.createElement("button", { type: "button", className: "wb-btn muted", onClick: onClose }, t("settings.cancel")),
        React.createElement("button", {
          type: "button",
          className: "wb-btn primary",
          disabled: !normalizeAccentHex(draft),
          onClick: applyDraft,
        }, t("settings.apply")),
      ),
    ),
  );
}

function WorkbenchBackgroundColorControl(p) {
  var { t, label, tweakKey, value, defaultValue, setTweak } = p;
  var applied = normalizeAccentHex(value) || defaultValue;
  var [pickerOpen, setPickerOpen] = useStateSt(false);
  var pickerRef = useRefSt(null);

  useEffectSt(function () {
    if (!pickerOpen) return undefined;
    function closePicker(event) {
      if (pickerRef.current && !pickerRef.current.contains(event.target)) setPickerOpen(false);
    }
    document.addEventListener("pointerdown", closePicker);
    return function () { document.removeEventListener("pointerdown", closePicker); };
  }, [pickerOpen]);

  return React.createElement("div", { className: "wb-background-color-row" },
    React.createElement("span", { className: "wb-background-color-label" }, label),
    React.createElement("div", { className: "wb-background-picker", ref: pickerRef },
      React.createElement("button", {
        type: "button",
        className: "wb-color-swatch wb-background-swatch",
        style: { "--swatch": applied },
        onClick: function () { setPickerOpen(!pickerOpen); },
        title: t("settings.backgroundColorFor", { theme: label }),
        "aria-label": t("settings.backgroundColorFor", { theme: label }),
        "aria-expanded": pickerOpen ? "true" : "false",
        "aria-haspopup": "dialog",
      }),
      pickerOpen && React.createElement(ColorPickerPopover, {
        t: t,
        value: value,
        defaultValue: defaultValue,
        onApply: function (next) { setTweak(tweakKey, next === defaultValue ? null : next); },
        onReset: function () { setTweak(tweakKey, null); },
        onClose: function () { setPickerOpen(false); },
        ariaLabel: t("settings.backgroundColorFor", { theme: label }),
      }),
    ),
    React.createElement("button", {
      type: "button",
      className: "wb-btn muted wb-background-reset",
      disabled: !normalizeAccentHex(value),
      onClick: function () {
        setTweak(tweakKey, null);
        setPickerOpen(false);
      },
    }, t("settings.restoreDefault")),
  );
}

function AppearancePanel(p) {
  var { t, tweaks, setTweak, actualTheme, theme } = p;
  var [performanceMode, setPerformanceMode] = useStateSt(function () {
    try { return localStorage.getItem("cyrene-performance-mode") === "1"; } catch (e) { return false; }
  });
  var [performanceModeBusy, setPerformanceModeBusy] = useStateSt(false);
  var accentPresets = ["#4378ff", "#8b5cf6", "#e8796b", "#34b8a0", "#f4a93e", "#e5488b", "#6b8cff", "#a78bfa"];
  var defaultAccent = actualTheme === "dark" ? "#63B38F" : "#4D9A78";
  var appliedAccent = normalizeAccentHex(tweaks.accent) || defaultAccent;
  var normalizedAccent = normalizeAccentHex(tweaks.accent);
  var customAccentSelected = !!normalizedAccent && !accentPresets.some(function (color) {
    return normalizeAccentHex(color) === normalizedAccent;
  });
  var [accentPickerOpen, setAccentPickerOpen] = useStateSt(false);
  var accentPickerRef = useRefSt(null);

  useEffectSt(function () {
    var cancelled = false;
    settingsFetch("/api/settings/config").then(readSettingsResponse).then(function (payload) {
      if (cancelled) return;
      setPerformanceMode(payload.performance_mode === true);
      if (window.CyreneUI.performanceMode) window.CyreneUI.performanceMode.apply(payload.performance_mode === true);
    }).catch(function () {});
    return function () { cancelled = true; };
  }, []);

  function togglePerformanceMode() {
    var next = !performanceMode;
    setPerformanceMode(next);
    setPerformanceModeBusy(true);
    if (window.CyreneUI.performanceMode) window.CyreneUI.performanceMode.apply(next);
    settingsFetch("/api/settings/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ performance_mode: next }),
    }).then(readSettingsResponse).catch(function () {
      setPerformanceMode(!next);
      if (window.CyreneUI.performanceMode) window.CyreneUI.performanceMode.apply(!next);
    }).finally(function () { setPerformanceModeBusy(false); });
  }

  useEffectSt(function () {
    if (!accentPickerOpen) return undefined;
    function closeAccentPicker(event) {
      if (accentPickerRef.current && !accentPickerRef.current.contains(event.target)) {
        setAccentPickerOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeAccentPicker);
    return function () { document.removeEventListener("pointerdown", closeAccentPicker); };
  }, [accentPickerOpen]);

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.appearance"), t("settings.appearanceSubtitle")),
    FieldRow(t("settings.theme"), t("settings.themeHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.theme === "system" ? " active" : ""), onClick: function () { setTweak("theme", "system"); } }, t("settings.system")),
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.theme === "light" ? " active" : ""), onClick: function () { setTweak("theme", "light"); } }, t("settings.light")),
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.theme === "dark" ? " active" : ""), onClick: function () { setTweak("theme", "dark"); } }, t("settings.dark")),
      ),
      undefined, "setting-theme",
    ),
    FieldRow(t("settings.themeColor"), t("settings.themeColorHint", { theme: actualTheme || t("settings.system") }),
      React.createElement("div", { className: "wb-accent-picker", ref: accentPickerRef },
        React.createElement("div", { className: "wb-color-swatches" },
          accentPresets.map(function (color, idx) {
            var normalized = normalizeAccentHex(color);
            var selected = normalizeAccentHex(tweaks.accent) === normalized;
            return React.createElement("button", {
              key: color,
              type: "button",
              className: "wb-color-swatch" + (selected ? " active" : ""),
              style: { "--swatch": color },
              onClick: function () {
                setTweak("accent", normalized);
                setAccentPickerOpen(false);
              },
              title: t("settings.accentN", { n: idx + 1 }),
              "aria-label": t("settings.accentN", { n: idx + 1 }),
              "aria-pressed": selected ? "true" : "false",
            });
          }),
          React.createElement("button", {
            type: "button",
            className: "wb-color-swatch wb-color-swatch-custom" + (customAccentSelected ? " active" : ""),
            style: { "--swatch": appliedAccent },
            onClick: function () { setAccentPickerOpen(!accentPickerOpen); },
            title: t("settings.currentThemeColor", { color: appliedAccent }),
            "aria-label": t("settings.currentThemeColor", { color: appliedAccent }),
            "aria-pressed": customAccentSelected ? "true" : "false",
            "aria-expanded": accentPickerOpen ? "true" : "false",
            "aria-haspopup": "dialog",
          }),
        ),
        accentPickerOpen && React.createElement(ColorPickerPopover, {
          t: t,
          value: tweaks.accent,
          defaultValue: defaultAccent,
          onApply: function (next) { setTweak("accent", next); },
          onReset: function () { setTweak("accent", null); },
          onClose: function () { setAccentPickerOpen(false); },
          ariaLabel: t("settings.customColor"),
        }),
      ),
      undefined, "setting-theme-color",
    ),
    FieldRow(t("settings.workbenchBackground"), t("settings.workbenchBackgroundHint"),
      React.createElement("div", { className: "wb-workbench-backgrounds" },
        React.createElement(WorkbenchBackgroundColorControl, {
          t: t,
          label: t("settings.lightBackground"),
          tweakKey: "backgroundLight",
          value: tweaks.backgroundLight,
          defaultValue: "#F5F6F8",
          setTweak: setTweak,
        }),
        React.createElement(WorkbenchBackgroundColorControl, {
          t: t,
          label: t("settings.darkBackground"),
          tweakKey: "backgroundDark",
          value: tweaks.backgroundDark,
          defaultValue: "#1A2230",
          setTweak: setTweak,
        }),
      ),
      undefined, "setting-workbench-background",
    ),
    FieldRow(t("settings.textSize"), t("settings.textSizeHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.textSize === "default" ? " active" : ""), onClick: function () { setTweak("textSize", "default"); } }, React.createElement("span", { className: "wb-text-size-sample default" }, "A"), " ", t("settings.default")),
        React.createElement("button", { className: "wb-seg-btn" + (tweaks.textSize === "large" ? " active" : ""), onClick: function () { setTweak("textSize", "large"); } }, React.createElement("span", { className: "wb-text-size-sample large" }, "A"), " ", t("settings.large")),
      ),
      undefined, "setting-text-size",
    ),
    FieldRow(t("settings.performanceMode"), t("settings.performanceModeHint"),
      Toggle(performanceMode, togglePerformanceMode, performanceModeBusy, t("settings.performanceMode")),
      undefined, "setting-performance-mode",
    ),
    FieldRow(t("settings.pulseAnimation"), t("settings.pulseAnimationHint"), Toggle(tweaks.animatePulse, function () { setTweak("animatePulse", !tweaks.animatePulse); }),
      undefined, "setting-pulse-animation"),
  );
}

function CustomToolsPanel(p) {
  var t = p.t;
  var [status, setStatus] = useStateSt({
    root: "",
    enabled: true,
    running: false,
    packages: [],
    files: [],
    tools: [],
    errors: [],
  });
  var [loading, setLoading] = useStateSt(true);
  var [reloading, setReloading] = useStateSt(false);
  var [toggleBusy, setToggleBusy] = useStateSt("");
  var [expandedToolId, setExpandedToolId] = useStateSt("");
  var [requestError, setRequestError] = useStateSt("");
  var requestGenerationRef = useRefSt(0);
  var reloadPendingRef = useRefSt(false);
  var mountedRef = useRefSt(false);

  function request(path, options) {
    return settingsFetch(path, options).then(readSettingsResponse).then(function (payload) {
      if (payload && payload.ok === false) {
        throw new Error(String(payload.error || t("settings.customToolsLoadError")));
      }
      return payload;
    });
  }

  function normalizeStatus(payload) {
    payload = payload || {};
    var packages = Array.isArray(payload.packages) ? payload.packages : [];
    var files = Array.isArray(payload.files) ? payload.files : [];
    var tools = Array.isArray(payload.tools) ? payload.tools : [];
    var errors = Array.isArray(payload.errors) ? payload.errors : [];
    return {
      root: String(payload.root || ""),
      enabled: payload.enabled !== false && payload.pack_enabled !== false,
      running: payload.running === true,
      packages: packages.filter(function (item) {
        return item && typeof item === "object" && item.id;
      }).map(function (item) {
        return {
          ...item,
          id: String(item.id),
          configured_enabled: item.configured_enabled !== false,
          enabled: item.effective_enabled !== false && item.enabled !== false,
          source_count: Number(item.source_count || 0),
          tool_count: Number(item.tool_count || 0),
          error_count: Number(item.error_count || 0),
          tools: Array.isArray(item.tools) ? item.tools : [],
          errors: Array.isArray(item.errors) ? item.errors : [],
        };
      }),
      files: files,
      tools: tools,
      errors: errors,
    };
  }

  function load() {
    var requestGeneration = ++requestGenerationRef.current;
    setLoading(true);
    setRequestError("");
    return request("/api/custom-tools/status").then(function (payload) {
      if (requestGeneration !== requestGenerationRef.current) return;
      setStatus(normalizeStatus(payload));
    }).catch(function (caught) {
      if (requestGeneration !== requestGenerationRef.current) return;
      setRequestError(caught && caught.message || String(caught));
    }).finally(function () {
      if (requestGeneration === requestGenerationRef.current) setLoading(false);
    });
  }

  function reloadTools() {
    if (reloadPendingRef.current) return;
    reloadPendingRef.current = true;
    setReloading(true);
    setRequestError("");
    request("/api/custom-tools/reload", { method: "POST" })
      .then(function (payload) {
        if (mountedRef.current) setStatus(normalizeStatus(payload));
      })
      .catch(function (caught) {
        if (mountedRef.current) setRequestError(caught && caught.message || String(caught));
      })
      .finally(function () {
        reloadPendingRef.current = false;
        if (mountedRef.current) setReloading(false);
      });
  }

  function togglePackage(item) {
    var packageId = String(item && item.id || "");
    if (!packageId || toggleBusy) return;
    var nextEnabled = item.configured_enabled === false;
    setToggleBusy(packageId);
    setRequestError("");
    request("/api/custom-tools/packages/" + encodeURIComponent(packageId) + "/enabled", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: nextEnabled }),
    }).then(function (payload) {
      if (mountedRef.current) setStatus(normalizeStatus(payload));
    }).catch(function (caught) {
      if (!mountedRef.current) return;
      setRequestError(caught && caught.message || String(caught));
    }).finally(function () {
      if (mountedRef.current) setToggleBusy("");
    });
  }

  useEffectSt(function () {
    mountedRef.current = true;
    load();
    return function () {
      mountedRef.current = false;
      requestGenerationRef.current += 1;
    };
  }, []);

  useEffectSt(function () {
    if (!window.CyreneUI.has("events")) return undefined;
    return window.CyreneUI.require("events").subscribe(function (event) {
      if (event && event.type === "custom_tools_changed") load();
    });
  }, []);

  var stateLabel = !status.enabled
    ? t("settings.customToolsState.disabled")
    : status.running
      ? t("settings.customToolsState.running")
      : t("settings.customToolsState.stopped");
  var stateClass = !status.enabled ? "is-disabled" : status.running ? "is-running" : "is-stopped";
  return React.createElement("div", { className: "wb-custom-tools-status", "aria-busy": loading || reloading ? "true" : "false" },
    React.createElement("section", { className: "wb-section-block wb-custom-tools-overview", "aria-labelledby": "custom-tools-directory-title" },
      React.createElement("div", { className: "wb-section-block-head" },
        React.createElement("b", { id: "custom-tools-directory-title" }, t("settings.customToolsDirectory")),
        React.createElement("div", { className: "wb-custom-tools-actions" },
          React.createElement("span", {
            className: "wb-custom-tools-status-label " + stateClass,
            role: "status",
            "aria-live": "polite",
          },
            React.createElement("span", { className: "wb-custom-tools-status-dot", "aria-hidden": "true" }),
            stateLabel,
          ),
          React.createElement("button", {
            type: "button",
            className: "wb-btn",
            disabled: loading || reloading,
            onClick: reloadTools,
          }, reloading ? t("settings.customToolsReloading") : t("settings.customToolsReload")),
        ),
      ),
      React.createElement("code", {
        className: "wb-custom-tools-path",
        title: status.root || undefined,
      }, status.root || "—"),
    ),
    requestError && React.createElement("div", { className: "wb-custom-tools-error", role: "alert" }, requestError),
    React.createElement("section", { className: "wb-custom-tools-packages", "aria-labelledby": "custom-tools-packages-title" },
      React.createElement("div", { className: "wb-custom-tools-section-heading" },
        React.createElement("b", { id: "custom-tools-packages-title" }, t("settings.customToolsPackages", { n: status.packages.length })),
        React.createElement("small", null, t("settings.customToolsPackagesHint")),
      ),
      loading && !status.packages.length
        ? React.createElement("p", { className: "wb-hint" }, t("settings.loading"))
        : status.packages.length
        ? React.createElement("div", { className: "wb-custom-tools-package-list" }, status.packages.map(function (item, packageIndex) {
            var packageId = String(item.id || "");
            var configuredEnabled = item.configured_enabled !== false;
            var effectiveEnabled = item.enabled !== false;
            var packageTools = Array.isArray(item.tools) ? item.tools : [];
            var packageErrors = Array.isArray(item.errors) ? item.errors : [];
            var packageFiles = status.files.filter(function (file) {
              return file && String(file.package_id || "") === packageId;
            });
            var displayTools = packageTools.slice();
            if (!displayTools.length && !effectiveEnabled) {
              displayTools = packageFiles.filter(function (file) {
                var filename = String(file && file.path || "").split("/").pop() || "";
                return filename && filename !== "__init__.py" && filename.charAt(0) !== "_";
              }).map(function (file) {
                var path = String(file.path || "");
                var filename = path.split("/").pop() || path;
                return {
                  _source_only: true,
                  name: filename.replace(/\.py$/i, ""),
                  path: path,
                  description: t("settings.customToolsDisabledSourceDescription"),
                };
              });
            }
            var summary = !configuredEnabled
              ? t("settings.customToolsPackageDisabledSummary", { files: item.source_count })
              : !status.enabled
                ? t("settings.customToolsPackageGloballyDisabledSummary", { files: item.source_count })
                : t("settings.customToolsPackageSummary", { tools: item.tool_count, files: item.source_count });
            if (item.error_count) {
              summary += " · " + t("settings.customToolsPackageErrorCount", { n: item.error_count });
            }
            var packageTitleId = "custom-tool-package-title-" + packageIndex;
            return React.createElement("section", {
              className: "wb-section-block wb-custom-tools-package-group" + (!effectiveEnabled ? " disabled" : ""),
              key: packageId,
              "aria-labelledby": packageTitleId,
            },
              React.createElement("div", { className: "wb-field wb-custom-tools-package-heading" },
                React.createElement("div", { className: "wb-label" },
                  React.createElement("span", { className: "wb-custom-tools-package-name", id: packageTitleId }, packageId),
                  React.createElement("small", null, summary),
                ),
                React.createElement("div", { className: "wb-controls wb-custom-tools-package-control" },
                  Toggle(
                    configuredEnabled,
                    function () { togglePackage(item); },
                    !!toggleBusy,
                    t("settings.customToolsPackageToggleLabel", { name: packageId }),
                  ),
                ),
              ),
              displayTools.length
                ? React.createElement("div", { className: "wb-extension-list wb-custom-tools-tool-card-list" }, displayTools.map(function (tool, toolIndex) {
                    var sourceOnly = tool && tool._source_only === true;
                    var toolName = tool && (tool.name || tool.stable_name || tool.concrete_name) || String(tool || "");
                    var toolPath = String(tool && tool.path || "");
                    var toolKey = packageId + ":" + (tool && (tool.concrete_name || tool.capability_id || toolPath) || toolName + ":" + toolIndex);
                    var toolExpanded = expandedToolId === toolKey;
                    var toolSummaryId = "custom-tool-summary-" + packageIndex + "-" + toolIndex;
                    var toolDetailsId = "custom-tool-details-" + packageIndex + "-" + toolIndex;
                    var toggleToolDetails = function () {
                      setExpandedToolId(toolExpanded ? "" : toolKey);
                    };
                    return React.createElement("article", {
                      className: "wb-extension-card wb-custom-tool-card" + (toolExpanded ? " expanded" : "") + (sourceOnly ? " source-only" : ""),
                      key: toolKey,
                    },
                      React.createElement("div", { className: "wb-extension-card-main" },
                        React.createElement("button", {
                          type: "button",
                          className: "wb-extension-card-summary",
                          id: toolSummaryId,
                          onClick: toggleToolDetails,
                          "aria-expanded": toolExpanded ? "true" : "false",
                          "aria-controls": toolDetailsId,
                          "aria-label": t("settings.customToolsDetailsFor", { name: toolName }),
                        },
                          React.createElement("span", { className: "wb-extension-glyph custom-tool" }, React.createElement(ExtensionGlyph, { kind: "toolchain", label: toolName })),
                          React.createElement("span", { className: "wb-extension-copy" },
                            React.createElement("span", { className: "wb-extension-title-row" },
                              React.createElement("strong", null, toolName),
                              React.createElement("span", { className: "wb-extension-type" }, t(sourceOnly ? "settings.customToolsSourceType" : "settings.customToolsToolType")),
                            ),
                            React.createElement("span", { className: "wb-extension-description" }, String(tool && tool.description || t("settings.customToolsNoToolDescription"))),
                            React.createElement("span", { className: "wb-extension-meta" },
                              React.createElement("span", { className: "wb-extension-status " + (sourceOnly ? "warning" : "managed") },
                                React.createElement("span", { className: "wb-extension-status-dot", "aria-hidden": "true" }),
                                t(sourceOnly ? "settings.customToolsToolNotLoaded" : "settings.customToolsToolLoaded"),
                              ),
                              toolPath && React.createElement("span", { className: "mono" }, toolPath),
                            ),
                          ),
                          React.createElement("span", { className: "wb-extension-chevron", "aria-hidden": "true" }, ExternalChevron()),
                        ),
                      ),
                      toolExpanded && React.createElement("div", {
                        className: "wb-extension-details wb-custom-tool-details",
                        id: toolDetailsId,
                        role: "region",
                        "aria-labelledby": toolSummaryId,
                      },
                        React.createElement("dl", null,
                          React.createElement("div", null, React.createElement("dt", null, t("settings.customToolsDetailPackage")), React.createElement("dd", { className: "mono" }, packageId)),
                          React.createElement("div", null, React.createElement("dt", null, t("settings.customToolsDetailPath")), React.createElement("dd", { className: "mono" }, toolPath || "—")),
                          !sourceOnly && React.createElement("div", null, React.createElement("dt", null, t("settings.customToolsDetailCapability")), React.createElement("dd", { className: "mono" }, tool.capability_id || "—")),
                          !sourceOnly && React.createElement("div", null, React.createElement("dt", null, t("settings.customToolsDetailIdentity")), React.createElement("dd", { className: "mono" }, tool.stable_name || "—")),
                        ),
                        !sourceOnly && React.createElement("div", { className: "wb-custom-tools-code-detail" },
                          React.createElement("b", null, t("settings.customToolsDetailSchema")),
                          React.createElement("pre", null, JSON.stringify(tool.input_schema || {}, null, 2)),
                        ),
                        !sourceOnly && tool.metadata && React.createElement("div", { className: "wb-custom-tools-code-detail" },
                          React.createElement("b", null, t("settings.customToolsDetailMetadata")),
                          React.createElement("pre", null, JSON.stringify(tool.metadata, null, 2)),
                        ),
                      ),
                    );
                  }))
                : React.createElement("p", { className: "wb-hint wb-custom-tools-package-empty" }, configuredEnabled
                    ? t("settings.customToolsNoPackageTools")
                    : t("settings.customToolsDisabledPackageTools")),
              packageErrors.length > 0 && React.createElement("div", { className: "wb-custom-tools-detail-group wb-custom-tools-errors" },
                React.createElement("b", null, t("settings.customToolsPackageErrors", { n: packageErrors.length })),
                React.createElement("ul", null, packageErrors.map(function (errorItem, errorIndex) {
                  var details = errorItem && typeof errorItem === "object" ? errorItem : { error: String(errorItem || "") };
                  return React.createElement("li", { key: String(details.path || "") + ":" + errorIndex },
                    React.createElement("div", { className: "wb-custom-tools-error-head" },
                      details.path && React.createElement("code", null, details.path),
                      details.error_type && React.createElement("b", null, details.error_type),
                    ),
                    React.createElement("pre", null, String(details.error || details.message || "")),
                  );
                })),
              ),
            );
          }))
        : React.createElement("p", { className: "wb-hint" }, t("settings.customToolsNoPackages")),
    ),
  );
}

// ── Capabilities Panel ──
function CapabilitiesPanel(p) {
  var {
    t, mcpConfigs, setMcpConfigs, mcpServers, toolGroups, toolsSaved,
    saveToolGroup, newMcpServer, setNewMcpServer, mcpSaved, saveMcp,
    voiceStatus, voiceReferenceText, setVoiceReferenceText,
    voiceReferenceFile, setVoiceReferenceFile, voiceReferencePhase, voiceReferenceElapsed,
    startVoiceReferenceRecording, finishVoiceReferenceRecording,
    voiceBusy, voiceNotice,
    saveVoiceBooleanSetting, saveVoiceMode, saveVoicePreset, saveVoiceProfile, deleteVoiceProfile,
  } = p;
  var mode = p.mode === "tools" ? "tools" : "voice";

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
  var customVoiceSelected = voiceStatus.voice_mode === "custom";
  var voiceReferenceActive = voiceReferencePhase === "starting"
    || voiceReferencePhase === "recording"
    || voiceReferencePhase === "transcribing";

  function voicePresetLabel(preset) {
    var number = Number(preset && preset.ordinal) || 1;
    if (preset && preset.group === "zipvoice") return t("settings.voiceZipVoiceDefault");
    if (preset && preset.group === "zh_female") return t("settings.voiceChineseFemale", { number: number });
    if (preset && preset.group === "zh_male") return t("settings.voiceChineseMale", { number: number });
    return t("settings.voiceEnglishFemale", { number: number });
  }

  function voicePresetOptions() {
    var presets = Array.isArray(voiceStatus.voice_presets) ? voiceStatus.voice_presets : [];
    return [
      ["zipvoice", "settings.voiceZipVoiceGroup"],
      ["zh_male", "settings.voiceChineseMaleGroup"],
      ["zh_female", "settings.voiceChineseFemaleGroup"],
      ["en_female", "settings.voiceEnglishFemaleGroup"],
    ].map(function (group) {
      var options = presets.filter(function (preset) { return preset.group === group[0]; });
      if (!options.length) return null;
      return React.createElement("optgroup", { key: group[0], label: t(group[1]) }, options.map(function (preset) {
        return React.createElement("option", { key: preset.id, value: preset.id }, voicePresetLabel(preset));
      }));
    }).filter(Boolean);
  }

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t(mode === "tools" ? "settings.toolsTab" : "settings.voiceTab")),

    mode === "voice" && React.cloneElement(SectionBlock(t("settings.voiceCapability"), t("settings.voiceCapabilityHint"),
      React.createElement("div", { className: "wb-voice-settings" },
        FieldRow(
          t("settings.voiceAutoSend"),
          t("settings.voiceAutoSendHint"),
          Toggle(
            voiceStatus.auto_send_after_asr === true,
            function () { saveVoiceBooleanSetting("auto_send_after_asr", voiceStatus.auto_send_after_asr !== true); },
            voiceBusy === "settings",
            t("settings.voiceAutoSend"),
          ),
          "voice-auto-send",
        ),
        FieldRow(
          t("settings.voiceAutoStop"),
          t("settings.voiceAutoStopHint"),
          Toggle(
            voiceStatus.auto_stop_on_silence !== false,
            function () { saveVoiceBooleanSetting("auto_stop_on_silence", voiceStatus.auto_stop_on_silence === false); },
            voiceBusy === "settings",
            t("settings.voiceAutoStop"),
          ),
          "voice-auto-stop",
        ),
        FieldRow(
          t("settings.voiceAutoRead"),
          voiceStatus.tts_ready
            ? t("settings.voiceAutoReadHint")
            : t("settings.voiceAutoReadUnavailable"),
          Toggle(
            voiceStatus.auto_read === true,
            function () { saveVoiceBooleanSetting("auto_read", voiceStatus.auto_read !== true); },
            !voiceStatus.tts_ready || voiceBusy === "settings",
            t("settings.voiceAutoRead"),
          ),
          "voice-auto-read",
        ),
        React.createElement("div", { className: "wb-voice-profile" },
          React.createElement("div", { className: "wb-voice-profile-copy" },
            React.createElement("b", null, t("settings.voiceProfile")),
            React.createElement("small", null, t("settings.voiceProfileHint")),
          ),
          React.createElement("div", {
            className: "wb-seg wb-voice-mode-switch",
            role: "group",
            "aria-label": t("settings.voiceProfile"),
          },
            React.createElement("button", {
              type: "button",
              className: "wb-seg-btn" + (!customVoiceSelected ? " active" : ""),
              "aria-pressed": customVoiceSelected ? "false" : "true",
              disabled: !!voiceBusy || voiceReferenceActive,
              onClick: function () { saveVoiceMode("preset"); },
            }, t("settings.voicePresetMode")),
            React.createElement("button", {
              type: "button",
              className: "wb-seg-btn" + (customVoiceSelected ? " active" : ""),
              "aria-pressed": customVoiceSelected ? "true" : "false",
              disabled: !!voiceBusy || voiceReferenceActive || !voiceStatus.custom_tts_model_ready,
              title: voiceStatus.custom_tts_model_ready ? "" : t("settings.voiceCustomRequiresZipVoice"),
              onClick: function () { saveVoiceMode("custom"); },
            }, t("settings.voiceCustomMode")),
          ),
          customVoiceSelected
            ? React.createElement("div", { className: "wb-voice-custom-fields" },
                React.createElement("div", { className: "wb-voice-profile-copy" },
                  React.createElement("b", null, t("settings.voiceCustomTitle")),
                  React.createElement("small", null, t("settings.voiceCustomHint")),
                ),
                React.createElement("div", { className: "wb-voice-reference-recorder" },
                  React.createElement("button", {
                    type: "button",
                    className: "wb-voice-record-btn" + (voiceReferencePhase === "recording" ? " recording" : ""),
                    disabled: !voiceStatus.asr_ready || voiceReferencePhase === "starting" || voiceReferencePhase === "transcribing" || !!voiceBusy,
                    "aria-label": voiceReferencePhase === "recording"
                      ? t("settings.voiceReferenceStop")
                      : t("settings.voiceReferenceRecord"),
                    onClick: function () {
                      if (voiceReferencePhase === "recording") finishVoiceReferenceRecording();
                      else startVoiceReferenceRecording();
                    },
                  },
                    React.createElement("span", { className: "wb-voice-record-dot", "aria-hidden": "true" }),
                    React.createElement("span", null,
                      voiceReferencePhase === "starting"
                        ? t("settings.voiceReferenceStarting")
                        : voiceReferencePhase === "recording"
                          ? t("settings.voiceReferenceStop")
                          : voiceReferencePhase === "transcribing"
                            ? t("settings.voiceReferenceRecognizing")
                            : voiceReferenceFile
                              ? t("settings.voiceReferenceRecordAgain")
                              : t("settings.voiceReferenceRecord")
                    ),
                  ),
                  React.createElement("small", null,
                    voiceStatus.asr_ready
                      ? voiceReferencePhase === "recording"
                        ? t("settings.voiceReferenceRecordingStatus", { seconds: voiceReferenceElapsed.toFixed(1) })
                        : t("settings.voiceReferenceRecordingHint")
                      : t("settings.voiceReferenceAsrUnavailable")
                  ),
                ),
                voiceReferenceFile && voiceReferenceText && React.createElement("div", {
                  className: "wb-voice-reference-transcript",
                  role: "status",
                },
                  React.createElement("b", null, t("settings.voiceReferenceTranscriptLabel")),
                  React.createElement("p", null, voiceReferenceText),
                ),
                React.createElement("div", { className: "wb-save-actions" },
                  React.createElement("button", {
                    type: "button",
                    className: "wb-btn primary",
                    disabled: !voiceReferenceFile || !voiceReferenceText.trim() || !!voiceBusy,
                    onClick: saveVoiceProfile,
                  }, voiceBusy === "profile" ? t("settings.saving") : t("settings.voiceSaveProfile")),
                  voiceStatus.voice_profile_ready && React.createElement("button", {
                    type: "button",
                    className: "wb-btn danger",
                    disabled: !!voiceBusy,
                    onClick: deleteVoiceProfile,
                  }, t("settings.delete")),
                ),
              )
            : React.createElement("div", { className: "wb-voice-preset-row" },
                React.createElement("div", { className: "wb-voice-profile-copy" },
                  React.createElement("b", null, t("settings.voicePresetName")),
                  React.createElement("small", null, t("settings.voicePresetHint")),
                ),
                voiceStatus.voice_preset_ready && Array.isArray(voiceStatus.voice_presets) && voiceStatus.voice_presets.length
                  ? React.createElement("select", {
                      className: "wb-select wb-voice-preset-select",
                      value: voiceStatus.voice_preset,
                      disabled: !!voiceBusy,
                      "aria-label": t("settings.voicePresetSelect"),
                      onChange: function (event) { saveVoicePreset(event.target.value); },
                    }, voicePresetOptions())
                  : React.createElement("span", { className: "" }, t("settings.localModelNotDownloaded")),
              ),
          voiceNotice && React.createElement("span", { className: "wb-hint saved" }, voiceNotice),
        ),
        React.createElement("div", { className: "wb-voice-readiness" },
          React.createElement("span", { className: voiceStatus.asr_ready ? "ready" : "" },
            t("settings.voiceAsrStatus") + " · " + t(voiceStatus.asr_ready ? "settings.localModelReady" : "settings.localModelNotDownloaded")
          ),
          React.createElement("span", { className: voiceStatus.tts_ready ? "ready" : "" },
            t("settings.voiceTtsStatus") + " · " + t(voiceStatus.tts_ready ? "settings.localModelReady" : "settings.voiceTtsNeedsProfile")
          ),
        ),
      ),
    ), { id: "setting-voice" }),

    // Tool packages
    mode === "tools" && React.cloneElement(SectionBlock(t("settings.toolPackages"), t("settings.toolPackagesHint"),
      React.createElement("div", { className: "wb-tool-package-settings" },
        toolGroups.filter(function (group) {
          return group.kind === "package";
        }).map(function (group) {
          var packageEnabled = group.enabled !== false;
          var groupName = t("toolName." + group.wire_name);
          return FieldRow(
            groupName,
            t("toolPackageDesc." + group.id),
            Toggle(
              packageEnabled,
              function () { saveToolGroup(group.id, !packageEnabled); },
              false,
              t("settings.packageToggleLabel", { name: groupName }),
              group.wire_name === "cyrene_tools"
                ? { "data-cyrene-user-ceremony": "true" }
                : null,
            ),
            group.id,
          );
        }),
      ),
      toolsSaved && React.createElement("div", { className: "wb-save-actions" },
        React.createElement("span", { className: "wb-hint saved" }, toolsSaved),
      ),
    ), { id: "setting-tool-packages" }),
  );
}

// ── Data Panel ──
var STORAGE_LABEL = {
  database: "settings.storageDatabase",
  knowledge: "settings.storageKnowledge",
  memory: "settings.storageMemory",
  conversations: "settings.storageConversations",
  plans: "settings.storagePlans",
  projects: "settings.storageProjects",
  sessions: "settings.storageSessions",
  inbox: "settings.storageInbox",
  skills: "settings.storageSkills",
  attachments: "settings.storageAttachments",
  backups: "settings.storageBackups",
  local_models: "settings.storageLocalModels",
  codex_cli: "settings.storageCodexCli",
  opencv_runtime: "settings.storageOpencvRuntime",
  browser: "settings.storageBrowser",
  caches: "settings.storageCaches",
};

var STORAGE_COLORS = {
  database: "#3b82f6",
  knowledge: "#a855f7",
  memory: "#d946ef",
  conversations: "#22c55e",
  plans: "#f59e0b",
  projects: "#06b6d4",
  sessions: "#ec4899",
  inbox: "#84cc16",
  skills: "#8b5cf6",
  attachments: "#f97316",
  backups: "#64748b",
  local_models: "#14b8a6",
  codex_cli: "#0ea5e9",
  opencv_runtime: "#eab308",
  browser: "#6366f1",
  caches: "#78716c",
};

function DataPanel(p) {
  var { t, redactSecrets, saveRedactSecrets, config, configLoading, resetStatus, setResetStatus, resetting, setResetting, backupList, backupMsg, setBackupMsg, loadBackups, exportSids, setExportSids, workbenchExportSessions, exportFmt, setExportFmt, exportMsg, setExportMsg, formatBytes, formatDate } = p;

  var [storage, setStorage] = useStateSt(null);
  var [storageError, setStorageError] = useStateSt("");

  function loadStorage() {
    setStorageError("");
    settingsFetch("/api/settings/storage").then(function (r) { return r.json(); }).then(function (payload) {
      setStorage(payload);
    }).catch(function (e) {
      setStorageError(e.message || String(e));
    });
  }

  useEffectSt(function () { loadStorage(); }, []);

  var storageList = (storage ? storage.categories : []).slice().sort(function (a, b) { return b.bytes - a.bytes; });
  var storageNonEmpty = storageList.filter(function (c) { return c.bytes > 0; });

  var dataStore = window.CyreneUI.require("data");
  var dataState = dataStore.state;
  var seenExportSessions = {};
  var exportSessions = (workbenchExportSessions || []).concat(dataState.sessions || []).filter(function (session) {
    var id = String(session && session.id || "");
    if (!id || seenExportSessions[id]) return false;
    seenExportSessions[id] = true;
    return true;
  });

  function clearSession() {
    settingsFetch("/api/chat/clear", { method: "POST" }).then(function () { dataStore.refreshSessions(); }).catch(function () {});
  }

  function resetData() {
    var title = t("settings.resetConfirmTitle");
    var body = t("settings.resetConfirmBody");
    var feedback = window.CyreneUI && window.CyreneUI.require
      ? window.CyreneUI.require("feedback")
      : null;
    var confirmed = feedback && typeof feedback.confirmModal === "function"
      ? feedback.confirmModal({
        title: title,
        body: body,
        confirmLabel: t("settings.resetConfirmAction"),
        danger: true,
      })
      : Promise.resolve(window.confirm([title, "", body].join("\n")));
    confirmed.then(function (ok) {
      if (!ok) return;
      setResetting(true);
      setResetStatus("");
      return settingsFetch("/api/settings/reset-data", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "RESET CYRENE DATA" }),
      }).then(function (r) { return r.json(); }).then(function (payload) {
        if (!payload.ok) throw new Error(payload.detail || payload.error || t("settings.resetAppDataFailed"));
        try { localStorage.clear(); } catch (e) {}
        try { sessionStorage.clear(); } catch (e) {}
        window.location.reload();
      });
    }).catch(function (e) {
      showSettingsToast(t("settings.resetAppDataFailed") + ": " + (e.message || String(e)), "error");
      setResetting(false);
    });
  }

  function backupDefaultName() {
    var now = new Date();
    function pad(value) { return String(value).padStart(2, "0"); }
    return "cyrene_backup_" + now.getFullYear() + pad(now.getMonth() + 1) + pad(now.getDate()) + "_" + pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds()) + ".zip";
  }

  async function createBackup() {
    var bridge = window.cyrene;
    if (!bridge || typeof bridge.pickBackupSavePath !== "function") {
      showSettingsToast(t("settings.backupPickerUnavailable"), "error");
      return;
    }
    try {
      var selection = await bridge.pickBackupSavePath({ title: t("settings.backupChooseSaveTitle"), defaultName: backupDefaultName() });
      if (!selection || selection.cancelled || !selection.path) return;
      setBackupMsg("");
      showSettingsToast(t("settings.backupExporting"), "info");
      var response = await settingsFetch("/api/backup/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: selection.path }) });
      var result = await response.json();
      if (!result.ok) throw new Error(result.error || t("settings.failed"));
      showSettingsToast(t("settings.backupExported", { n: result.entries.length, size: formatBytes(result.size) }), "success");
      loadBackups();
    } catch (e) {
      showSettingsToast(t("settings.failed") + ": " + e.message, "error");
    }
  }

  async function restoreBackup() {
    var bridge = window.cyrene;
    if (!bridge || typeof bridge.pickBackupFile !== "function") {
      showSettingsToast(t("settings.backupPickerUnavailable"), "error");
      return;
    }
    try {
      var selection = await bridge.pickBackupFile({ title: t("settings.backupChooseFileTitle") });
      if (!selection || selection.cancelled || !selection.path) return;
      var response = await settingsFetch("/api/backup/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: selection.path }) });
      var result = await response.json();
      if (!result.ok) throw new Error(result.error || (result.errors || []).join(";") || t("settings.backupRestoreFailed"));
      showSettingsToast(t("settings.backupRestored", { n: result.restored.length }) + " " + t("settings.backupRestartRequired"), "success");
    } catch (e) {
      showSettingsToast(t("settings.backupRestoreFailed") + ": " + e.message, "error");
    }
  }

  function toggleExportSession(sessionId) {
    var id = String(sessionId || "");
    setExportSids(exportSids.indexOf(id) >= 0
      ? exportSids.filter(function (value) { return value !== id; })
      : exportSids.concat(id));
    setExportMsg("");
  }

  function exportSelectedSessions() {
    if (!exportSids.length) return;
    exportSids.forEach(function (sessionId) {
      var url = "/api/sessions/" + encodeURIComponent(sessionId) + "/export?format=" + exportFmt;
      var a = document.createElement("a");
      a.href = url;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    });
    setExportMsg("");
    showSettingsToast(t("settings.sessionExportStarted", { n: exportSids.length }), "success");
  }

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.data"), t("settings.dataSubtitle")),

    // Storage usage
    React.cloneElement(SectionBlock(t("settings.storageUsage"), t("settings.storageUsageHint"),
      React.createElement("div", { className: "wb-inline-row" },
        React.createElement("b", { className: "mono" }, storage ? formatBytes(storage.total) : t("settings.storageLoading")),
      ),
      storageError
        ? React.createElement("p", { className: "wb-hint" }, t("settings.storageError") + ": " + storageError)
        : storage && React.createElement("div", { className: "wb-storage" },
            storage.total > 0 && React.createElement("div", { className: "wb-storage-bar", role: "img", "aria-label": t("settings.storageUsage") },
              storageNonEmpty.map(function (c) {
                return React.createElement("span", {
                  key: c.key,
                  className: "wb-storage-seg",
                  style: { width: (c.bytes / storage.total * 100) + "%", background: STORAGE_COLORS[c.key] },
                  title: t(STORAGE_LABEL[c.key] || c.key) + ": " + formatBytes(c.bytes) + " · " + t("settings.storageFiles", { n: c.files }),
                });
              }),
            ),
            React.createElement("div", { className: "wb-storage-legend" },
              storageList.map(function (c) {
                return React.createElement("div", { className: "wb-storage-legend-row" + (c.bytes === 0 ? " empty" : ""), key: c.key },
                  React.createElement("span", { className: "wb-storage-swatch", style: { background: STORAGE_COLORS[c.key] } }),
                  React.createElement("span", { className: "wb-storage-legend-name" }, t(STORAGE_LABEL[c.key] || c.key)),
                  React.createElement("b", { className: "mono" }, formatBytes(c.bytes)),
                );
              }),
            ),
          ),
      storage && storage.truncated ? React.createElement("p", { className: "wb-hint" }, t("settings.storageTruncated")) : null,
    ), { id: "setting-storage" }),

    FieldRow(t("settings.redactSecrets"), t("settings.redactSecretsHint"), Toggle(redactSecrets, function () { saveRedactSecrets(!redactSecrets); }),
      undefined, "setting-redact-secrets"),
    FieldRow(t("settings.clearSession"), t("settings.clearSessionHint"),
      React.createElement("button", { className: "wb-btn muted", onClick: clearSession }, t("settings.clearSessionBtn")),
      undefined, "setting-clear-session",
    ),
    React.createElement("div", { className: "wb-field wb-field-stack wb-field-danger", id: "setting-reset-app-data" },
      React.createElement("div", { className: "wb-label" },
        t("settings.resetAppData"),
        React.createElement("small", null, t("settings.resetAppDataHint")),
      ),
      React.createElement("div", { className: "wb-controls" },
        React.createElement("div", { className: "wb-inline-row wb-inline-row-start" },
          React.createElement("button", { className: "wb-btn danger", onClick: resetData, disabled: resetting }, resetting ? t("settings.resettingData") : t("settings.resetAppDataBtn")),
        ),
      ),
    ),

    // Path info
    React.cloneElement(SectionBlock(t("settings.pathInfo"), null,
      FieldRow(t("settings.baseDir"), null, React.createElement("input", { className: "wb-input mono wb-path-display", value: configLoading ? t("settings.pathLoading") : config.base_dir, readOnly: true })),
      FieldRow(t("settings.dataDir"), null, React.createElement("input", { className: "wb-input mono wb-path-display", value: configLoading ? t("settings.pathLoading") : config.data_dir, readOnly: true })),
      FieldRow(t("settings.workspaceDir"), null, React.createElement("input", { className: "wb-input mono wb-path-display", value: configLoading ? t("settings.pathLoading") : config.workspace_dir, readOnly: true })),
      FieldRow(t("settings.soulPath"), null, React.createElement("input", { className: "wb-input mono wb-path-display", value: configLoading ? t("settings.pathLoading") : config.soul_path, readOnly: true })),
    ), { id: "setting-paths" }),

    // Backup
    React.cloneElement(SectionBlock(t("settings.backup"), t("settings.backupHint"),
      React.createElement("div", { className: "wb-inline-row" },
        React.createElement("button", { className: "wb-btn primary", onClick: createBackup }, t("settings.backupExportBtn")),
        React.createElement("button", { className: "wb-btn", "data-cyrene-risk": "R3", onClick: restoreBackup }, t("settings.backupRestoreBtn")),
      ),
      backupList.map(function (b) {
        return React.createElement("div", { className: "wb-backup-row", key: b.name },
          React.createElement("span", { className: "wb-backup-name" }, b.name),
          React.createElement("span", { className: "wb-backup-meta" }, formatBytes(b.size), " · ", formatDate(b.modified)),
        );
      }),
    ), { id: "setting-backup" }),

    // Session export
    React.cloneElement(SectionBlock(t("settings.sessionExport"), t("settings.sessionExportHint"),
      exportSessions.length > 0 ? React.createElement("div", { className: "wb-export-area" },
        React.createElement("div", { className: "wb-export-session-toolbar" },
          React.createElement("span", null, t("settings.sessionExportSelected", { n: exportSids.length })),
          React.createElement("div", { className: "wb-inline-row" },
            React.createElement("button", { type: "button", className: "wb-btn muted", onClick: function () { setExportSids(exportSessions.map(function (s) { return s.id; })); setExportMsg(""); } }, t("settings.selectAll")),
            React.createElement("button", { type: "button", className: "wb-btn muted", disabled: !exportSids.length, onClick: function () { setExportSids([]); setExportMsg(""); } }, t("settings.clearSelection")),
          ),
        ),
        React.createElement("div", { className: "wb-export-session-list", role: "group", "aria-label": t("settings.sessionExportSelectLabel") },
          exportSessions.map(function (s) {
            var selected = exportSids.indexOf(s.id) >= 0;
            return React.createElement("label", { className: "wb-export-session-option" + (selected ? " selected" : ""), key: s.id },
              React.createElement("input", { type: "checkbox", checked: selected, onChange: function () { toggleExportSession(s.id); } }),
              React.createElement("span", null, s.title || s.id),
            );
          }),
        ),
        React.createElement("div", { className: "wb-seg" },
          React.createElement("button", { className: "wb-seg-btn" + (exportFmt === "markdown" ? " active" : ""), onClick: function () { setExportFmt("markdown"); } }, "Markdown"),
          React.createElement("button", { className: "wb-seg-btn" + (exportFmt === "json" ? " active" : ""), onClick: function () { setExportFmt("json"); } }, "JSON"),
        ),
        React.createElement("div", { className: "wb-inline-row" },
          React.createElement("button", { className: "wb-btn primary", disabled: !exportSids.length, onClick: exportSelectedSessions }, t("settings.sessionExportBtn")),
        ),
      ) : React.createElement("p", { className: "wb-hint" }, t("settings.sessionExportNoSessions")),
    ), { id: "setting-session-export" }),
  );
}

// ── About Panel ──
function AboutPanel(p) {
  var { t, config } = p;

  return React.createElement("div", { className: "settings-panel wb-about-settings" },
    SectionTitle(t("settings.about"), t("settings.aboutSubtitle")),
    React.createElement(UpdateSection, { t: t, config: config }),
  );
}

// ── Update Section (inlined) ──
function UpdateSection({ t, config }) {
  var dataState = window.CyreneUI.require("data").state;
  var [checking, setChecking] = useStateSt(false);
  var [info, setInfo] = useStateSt(null);
  var [downloading, setDownloading] = useStateSt(false);
  var [progress, setProgress] = useStateSt({ downloaded: 0, total: 0, done: false });
  var [downloaded, setDownloaded] = useStateSt(false);
  var [error, setError] = useStateSt("");
  var [exporting, setExporting] = useStateSt(false);
  var [beta, setBeta] = useStateSt(!!(config && config.beta_updates));
  var [autoUpdate, setAutoUpdate] = useStateSt(!!(!config || config.auto_update !== false));
  var [changelogOpen, setChangelogOpen] = useStateSt(false);
  var [changelog, setChangelog] = useStateSt({ version: "", published_at: "", release_notes: "" });

  useEffectSt(function () { checkUpdate(); }, []);
  // 后台自动下载可能已完成/进行中，页面打开时恢复其状态（checkUpdate 失败也兜底）。
  useEffectSt(function () { syncDownloadState(); }, []);
  // Sync local toggle with config once it loads from the server.
  useEffectSt(function () { setBeta(!!(config && config.beta_updates)); }, [config && config.beta_updates]);
  useEffectSt(function () { setAutoUpdate(!!(!config || config.auto_update !== false)); }, [config && config.auto_update]);

  function syncDownloadState() {
    settingsFetch("/api/update/progress").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || typeof d.done === "undefined") return;
      setProgress(d);
      if (d.done) {
        setDownloading(false);
        if (d.verified) {
          setDownloaded(true);
        } else if (d.verification_error) {
          setError(d.verification_error);
        }
      } else if (d.downloaded > 0 && d.total > 0) {
        setDownloading(true);
      }
    }).catch(function () {});
  }

  function checkUpdate() {
    setChecking(true); setError("");
    settingsFetch("/api/update/check").then(function (r) { return r.json(); }).then(function (d) {
      setInfo(d);
      setChangelog({ version: d.latest_version || "", published_at: d.published_at || "", release_notes: d.release_notes || "" });
      syncDownloadState();
    }).catch(function () { setError(t("settings.updateCheckFailed")); }).finally(function () { setChecking(false); });
  }

  function openChangelog() {
    settingsFetch("/api/update/changelog").then(function (r) { return r.json(); }).then(function (d) {
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
    settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ beta_updates: next }) })
      .then(function () { checkUpdate(); })
      .catch(function () { setBeta(!next); });
  }

  function toggleAutoUpdate() {
    if (checking || downloading) return;
    var next = !autoUpdate;
    setAutoUpdate(next);
    settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ auto_update: next }) })
      .catch(function () { setAutoUpdate(!next); });
  }

  function startDownload() {
    setDownloading(true); setError("");
    settingsFetch("/api/update/download", { method: "POST" }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.ok && d.verified) {
        setDownloaded(true);
        setProgress(function (p) { return Object.assign({}, p, { done: true, verified: true, actual_sha256: d.sha256 || p.actual_sha256 || "" }); });
        return "done";
      }
      if (d.code === "update_download_in_progress") {
        // 后台已在下载：保持 downloading=true，由下方轮询 effect 直接展示后台进度，
        // 完成后按钮自动变为「重启更新」，不再报「already in progress」错误。
        return "following";
      }
      setDownloaded(false);
      setProgress(function (p) { return Object.assign({}, p, { done: !!d.done, verified: false, verification_error: d.error || "" }); });
      setError(d.error || t("settings.updateDownloadFailed"));
      return "done";
    }).catch(function () { setError(t("settings.updateDownloadFailed")); return "done"; })
      .then(function (mode) { if (mode !== "following") setDownloading(false); });
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
    var confirmTitle = t("settings.updateConfirmTitle", { version: version }, "Install update to {version}?");
    var confirmBody = t("settings.updateConfirmRestart", null, "Cyrene will close and restart during installation.");
    var confirmed = window.CyreneUI.require("feedback").confirmModal
      ? window.CyreneUI.require("feedback").confirmModal({
        title: confirmTitle,
        body: confirmBody,
        confirmLabel: t("common.confirm", null, "Confirm"),
      })
      : Promise.resolve(window.confirm([confirmTitle, "", confirmBody].join("\n")));
    confirmed.then(function (ok) {
      if (!ok) return;
      settingsFetch("/api/update/restart", { method: "POST" }).then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.message || d.error || t("settings.updateRestartFailed", null, "Restart failed")); });
      }).catch(function (err) {
        if (err && err.message) setError(err.message);
      });
    });
  }

  useEffectSt(function () {
    if (!downloading) return;
    var timer = setInterval(function () {
      settingsFetch("/api/update/progress").then(function (r) { return r.json(); }).then(function (d) {
        setProgress(d);
        if (d.done) {
          clearInterval(timer);
          setDownloading(false);
          if (d.verified) setDownloaded(true);
          else if (d.verification_error) setError(d.verification_error);
        }
      }).catch(function () { clearInterval(timer); setDownloading(false); });
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
  var progressTotal = Number(progress.total || (info && info.asset_size) || 0);
  var heroProgress = progressTotal > 0
    ? Math.max(0, Math.min(100, Math.round((Number(progress.downloaded || 0) / progressTotal) * 100)))
    : (downloaded ? 100 : 0);
  function exportLogs() {
    if (exporting) return;
    setExporting(true);
    settingsFetch("/api/logs/export", { method: "GET" })
      .then(function (response) { return response.blob(); })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = "cyrene-logs-" + new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19) + ".zip";
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showSettingsToast(t("settings.logExportDone", null, "Logs exported"), "success");
      })
      .catch(function (err) {
        showSettingsToast(t("settings.logExportFailed", null, "Log export failed") + ": " + String((err && err.message) || err), "error");
      })
      .finally(function () { setExporting(false); });
  }

  var relatedLinks = [
    { icon: "docs", title: t("settings.relatedDocs", null, "Help docs"), action: t("settings.view", null, "View"), href: REPO_DOCS_URL },
    { icon: "changelog", title: t("settings.relatedChangelog", null, "Changelog"), action: t("settings.view", null, "View"), onClick: openChangelog },
    { icon: "website", title: t("settings.relatedWebsite", null, "Official website"), action: t("settings.view", null, "View"), href: REPO_URL },
    { icon: "github", title: t("settings.relatedGithub", null, "GitHub repository"), action: t("settings.view", null, "View"), href: REPO_URL },
    { icon: "issue", title: t("settings.relatedIssue", null, "Submit Issue"), action: t("settings.feedback", null, "Feedback"), href: REPO_ISSUES_URL },
    { icon: "log", title: t("settings.exportLogs", null, "Export logs"), action: exporting ? t("common.loading", null, "Loading...") : t("settings.exportLogsAction", null, "Download"), onClick: exportLogs, disabled: exporting },
  ];

  return React.createElement("div", { className: "wb-about-stack" },
    React.createElement("section", {
      className: "wb-about-product-card" + (downloading ? " is-downloading" : "") + (downloaded ? " is-downloaded" : ""),
      style: { "--wb-about-download-progress": heroProgress + "%" },
      "aria-busy": downloading ? "true" : undefined,
    },
      React.createElement("div", { className: "wb-about-hero-progress", "aria-hidden": "true" }),
      React.createElement("div", { className: "wb-about-product-copy" },
        React.createElement("div", { className: "wb-about-logo", "aria-hidden": "true" },
          React.createElement("div", { className: "brand-mark" }),
        ),
        React.createElement("div", { className: "wb-about-product-text" },
          React.createElement("div", { className: "wb-about-title-row" },
            React.createElement("h3", null, "Cyrene"),
            React.createElement("span", { className: "wb-about-version-chip" }, dataState.appVersion || "—"),
          ),
          React.createElement("p", null, t("settings.aboutHeroCopy")),
        ),
      ),
      React.createElement("div", { className: "wb-about-hero-action" },
        React.createElement("button", {
          className: "wb-btn primary wb-about-check-btn",
          "data-cyrene-risk": downloaded ? "R3" : "R2",
          disabled: actionDisabled,
          onClick: actionHandler,
        }, actionLabel),
      ),
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
        React.createElement("div", null, React.createElement("span", null, t("settings.updateCurrentVersion", null, "Current version")), React.createElement("strong", null, info && info.current_version ? "v" + info.current_version : (dataState.appVersion || "—"))),
        React.createElement("div", null, React.createElement("span", null, t("settings.updateLatestVersion", null, "Latest version")), React.createElement("strong", null, lv || (dataState.appVersion || "—"))),
        React.createElement("div", null, React.createElement("span", null, t("settings.updateReleaseBranch", null, "Release branch")), React.createElement("strong", null, "main")),
        React.createElement("div", null, React.createElement("span", null, t("settings.updatePublishedAt", null, "Published")), React.createElement("strong", null, fmtDate(info && info.published_at))),
      ),
      statusDetail && React.createElement("p", { className: "wb-about-update-status" }, statusDetail),
      error && React.createElement("p", { className: "wb-hint", style: { color: "var(--wb-red)" } }, error),
      info && info.update_available && React.createElement("div", { className: "wb-update-notes" },
        React.createElement("span", null, t("settings.updateReleaseNotes", null, "Release notes")),
        React.createElement("div", {
          className: "wb-update-notes-body markdown",
          dangerouslySetInnerHTML: { __html: renderSettingsMarkdown(notesText()) },
        })
      ),
    ),

    React.createElement("section", { className: "wb-about-related-card" },
      React.createElement("div", { className: "wb-about-card-head" },
        React.createElement("h3", null, t("settings.relatedLinks", null, "Related links")),
        React.createElement("small", null, t("settings.relatedLinksHint", null, "Documentation, releases, support, and diagnostics.")),
      ),
      React.createElement("div", { className: "wb-about-related-list" },
        relatedLinks.map(function (item) {
          var action = item.onClick
            ? React.createElement("button", { type: "button", className: "wb-about-related-action", disabled: item.disabled, onClick: item.onClick }, item.action)
            : React.createElement("a", { className: "wb-about-related-action", href: item.href, target: "_blank", rel: "noopener noreferrer" }, item.action);
          return React.createElement("div", { key: item.title, className: "wb-about-related-row" },
            React.createElement("span", { className: "wb-about-related-icon" }, AboutRelatedIcon(item.icon)),
            React.createElement("strong", null, item.title),
            action,
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
              changelog.version ? "v" + changelog.version : (dataState.appVersion || "—"),
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
function LegacySkillsPanel(p) {
  var t = p.t;
  var [skills, setSkills] = useStateSt([]);
  var [loading, setLoading] = useStateSt(true);
  var [query, setQuery] = useStateSt("");
  var [expandedId, setExpandedId] = useStateSt("");
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
    if (showSettingsToast(text, kind || "info")) {
      setMessage("");
      setMessageKind("");
      return;
    }
    setMessage(text);
    setMessageKind(kind || "info");
    setTimeout(function () { setMessage(""); setMessageKind(""); }, 3000);
  }

  function loadSkills() {
    setLoading(true);
    return settingsFetch("/api/skills/installed")
      .then(function (r) { return r.ok ? r.json() : Promise.reject("HTTP " + r.status); })
      .then(function (data) {
        var list = (data && data.skills) || [];
        setSkills(list);
        setExpandedId(function (prev) {
          return prev && list.some(function (s) { return s.id === prev; }) ? prev : "";
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
    settingsFetch("/api/skills/" + id + "/toggle", { method: "POST" })
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
    window.CyreneUI.require("feedback").confirmModal({
      body: t("settings.uninstallSkillConfirm", { name: name || id }),
      confirmLabel: t("settings.uninstallSkill"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      setBusy(true);
      settingsFetch("/api/skills/" + id + "/uninstall", { method: "POST" })
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
    settingsFetch("/api/skills/install-upload", { method: "POST", body: formData })
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
    settingsFetch("/api/skills/install-picker", { method: "POST" })
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
    settingsFetch("/api/skills/scan", { method: "POST" })
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
        var isExpanded = expandedId === skill.id;
        var detailId = "wb-skill-detail-" + skill.id;
        return React.createElement("div", {
          key: skill.id,
          className: "wb-card wb-skill-card" + (isExpanded ? " active" : "") + (skill.enabled !== false ? " enabled" : ""),
          role: "button",
          tabIndex: 0,
          "aria-expanded": isExpanded ? "true" : "false",
          "aria-controls": detailId,
          onClick: function (e) {
            if (e.target.closest(".wb-skill-card-actions") || e.target.closest(".wb-toggle")) return;
            setExpandedId(isExpanded ? "" : skill.id);
          },
          onKeyDown: function (e) {
            if (e.key !== "Enter" && e.key !== " ") return;
            if (e.target.closest(".wb-skill-card-actions") || e.target.closest(".wb-toggle")) return;
            e.preventDefault();
            setExpandedId(isExpanded ? "" : skill.id);
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
              React.createElement("span", {
                className: "wb-skill-expand-indicator",
                "aria-hidden": "true",
                onClick: function (e) {
                  e.stopPropagation();
                  setExpandedId(isExpanded ? "" : skill.id);
                },
              }, ExternalChevron()),
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
          isExpanded && React.createElement("div", { id: detailId, className: "wb-skill-detail-body" },
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

function ExtensionGlyph(props) {
  var kind = props.kind || "extension";
  var label = String(props.label || kind || "E").slice(0, 1).toUpperCase();
  if (props.id === "github-cli") return AboutRelatedIcon("github");
  var paths = {
    skill: "M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-4Z",
    mcp: "M8 12h8M12 8v8M5 5h4v4H5zM15 15h4v4h-4z",
    cli: "M4 5h16v14H4zM7 9l3 3-3 3M12 15h5",
    toolchain: "M14.7 6.3a4 4 0 0 0-5 5L3 18l3 3 6.7-6.7a4 4 0 0 0 5-5l-3 3-3-3z",
  };
  if (!paths[kind]) return React.createElement("span", { className: "wb-extension-glyph-text" }, label);
  return React.createElement("svg", { width: "22", height: "22", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "1.9", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
    React.createElement("path", { d: paths[kind] })
  );
}

function extensionDisplayName(item, t) {
  item = item || {};
  return t("settings.extensionCatalog." + item.id + ".name", item.name || item.id || "—");
}

function extensionDisplayDescription(item, t) {
  item = item || {};
  return t("settings.extensionCatalog." + item.id + ".description", item.description || "—");
}

function extensionSourceLabel(item, t) {
  item = item || {};
  var source = item.source;
  var type = "";
  var details = [];
  if (item.ownership === "system") type = "system";
  else if (item.ownership === "builtin") type = "builtin";
  if (!type && typeof source === "string") {
    var value = source.trim();
    var lower = value.toLowerCase();
    if (lower === "manual") type = "manual";
    else if (lower === "github" || lower.indexOf("github:") === 0 || lower.indexOf("github.com") >= 0) type = "github";
    else if (lower === "cyrene-catalog" || lower.indexOf("registry") >= 0) type = "registry";
    else if (/^https?:\/\//.test(lower)) type = "registry";
    if (value && type !== "registry" && lower !== type && lower !== "cyrene-catalog") details.push(value.replace(/^github:/i, ""));
  }
  if (source && typeof source === "object") {
    var rawType = String(source.type || source.kind || "").toLowerCase().replace(/_/g, "-");
    if (!type) {
      if (rawType === "system") type = "system";
      else if (rawType === "bundled" || rawType === "builtin") type = "builtin";
      else if (rawType === "uv") type = "uv";
      else if (rawType === "mise") type = "mise";
      else if (rawType === "github-release") type = "githubRelease";
      else if (rawType === "mcp-registry" || rawType === "mcp-registry-package") type = "mcpRegistry";
      else if (rawType === "github" || String(source.url || "").toLowerCase().indexOf("github.com") >= 0) type = "github";
      else if (["local", "directory", "file", "archive", "upload"].indexOf(rawType) >= 0 || source.path) type = "local";
      else if (rawType === "manual" || source.transport) type = "manual";
      else if (rawType === "registry") type = "registry";
    }
    if (type === "mise" && source.ref) details.push(String(source.ref));
    if (type === "githubRelease") {
      if (source.repo) details.push(String(source.repo));
      if (source.tag) details.push(String(source.tag));
    }
    if (type === "mcpRegistry") {
      if (source.identifier) details.push(String(source.identifier));
      else if (source.id) details.push(String(source.id));
    }
  }
  if (!type) type = "unknown";
  return [t("settings.extensionSource." + type), ...details].filter(Boolean).join(" · ");
}

function extensionHealthLabel(item, t) {
  item = item || {};
  var value = item.kind === "mcp" ? (item.connection_status || item.health) : item.health;
  value = String(value || "unknown").toLowerCase().replace(/-/g, "_");
  var aliases = {
    missing_bundle: "missingBundle",
    error: "unhealthy",
    failed: "unhealthy",
    disabled: "disconnected",
  };
  return t("settings.extensionHealthValue." + (aliases[value] || value), t("settings.extensionHealthValue.unknown"));
}

function extensionTaskStatusLabel(status, t) {
  var value = String(status || "unknown");
  return t("settings.extensionTaskStatus." + value, t("settings.extensionTaskStatus.unknown"));
}

function extensionTaskErrorContent(task, t) {
  var knownReasons = ["dependency_conflict", "mcp_connection_failed", "executable_not_found", "fixed_version_required", "configuration_required", "uv_runtime_missing"];
  var reason = knownReasons.indexOf(task.reason_code) >= 0 ? task.reason_code : "installation_failed";
  return {
    title: t("settings.extensionTaskError." + reason + ".title", t("settings.extensionTaskStatus.failed")),
    hint: t("settings.extensionTaskError." + reason + ".hint", t("settings.extensionInstallFailed")),
  };
}

function extensionTaskIsVisible(task, now) {
  if (["queued", "running", "cancelling"].indexOf(task.status) >= 0) return true;
  if (["failed", "interrupted"].indexOf(task.status) < 0) return false;
  var finishedAt = Date.parse(task.finished_at || "");
  return Number.isFinite(finishedAt) && now - finishedAt < 30000;
}

function extensionAuditLabel(prefix, value, t) {
  var aliases = {
    "install.start": "installStart", "install.finish": "installFinish",
    uninstall: "uninstall", "mcp.enable": "mcpEnable", "mcp.disable": "mcpDisable",
    "skill.enable": "skillEnable", "skill.disable": "skillDisable",
    "extension.enable": "extensionEnable", "extension.disable": "extensionDisable",
    "source.update": "sourceUpdate", "system.bind": "systemBind", "system.unbind": "systemUnbind",
    "default.set": "defaultSet",
  };
  var resolved = prefix === "Action" ? (aliases[value] || value) : value;
  return t("settings.extensionAudit" + prefix + "." + resolved, value || "—");
}

function ExtensionStatus(props) {
  var item = props.item || {};
  var t = props.t;
  var className = "missing";
  var text = t("settings.extensionNotInstalled");
  if (item.ownership === "builtin") { className = item.health === "healthy" ? "builtin" : "warning"; text = t("settings.extensionBuiltin"); }
  else if (item.ownership === "system") { className = "system"; text = t("settings.extensionSystemInstalled"); }
  else if (item.ownership === "cyrene") { className = item.health === "healthy" ? "managed" : "warning"; text = t("settings.extensionManagedInstalled"); }
  if (item.kind === "mcp") {
    className = item.connection_status === "connected" ? "managed" : "warning";
    text = item.connection_status === "connected" ? t("settings.connected") : t("settings.disconnected");
  }
  if (item.observed_state === "installed" && item.enabled === false) { className = "disabled"; text = t("settings.extensionDisabled"); }
  return React.createElement("span", { className: "wb-extension-status " + className },
    React.createElement("span", { className: "wb-extension-status-dot", "aria-hidden": "true" }), text
  );
}

// ---- External Agent extension-center UI (phase 1, handoff §7) --------------
// The Agent Tab deliberately has no search UI: it renders the recommended
// catalog, the real installed enumeration and a fixed "install other Agent"
// API entry.  Agent runtime endpoints are phase-1 placeholders, so probe /
// restart / auth calls surface the backend's explicit unavailability instead
// of pretending the runtime is ready.

function extensionCopyText(value) {
  if (!value) return Promise.resolve();
  if (window.cyrene && typeof window.cyrene.writeClipboardText === "function") {
    return Promise.resolve(window.cyrene.writeClipboardText(value));
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(value);
  }
  return new Promise(function (resolve, reject) {
    try {
      var input = document.createElement("textarea");
      input.value = value;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      var ok = document.execCommand && document.execCommand("copy");
      document.body.removeChild(input);
      if (ok) resolve(); else reject(new Error("copy_failed"));
    } catch (error) {
      reject(error);
    }
  });
}

var AGENT_PROPOSAL_ENDPOINT = "/api/extensions/agents/install-proposals";
var AGENT_MANIFEST_TEMPLATE = JSON.stringify({
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
    output: { streaming: "supported", reasoning: "unknown", toolLifecycle: "unknown", artifacts: "unknown", diff: "unknown" },
    interaction: { permission: "agent_defined", steer: "unknown", cancel: "supported" },
    model: { agentManaged: "supported", cyreneManaged: ["openai_chat", "openai_responses"], switchDuringSession: "unsupported", reasoningEffort: "unknown" },
  },
  modelAccess: { mode: "cyrene_managed", profileId: "primary" },
}, null, 2);

function agentSourceTrustMeta(agent, t) {
  var trust = String(agent.sourceTrust || agent.source_trust || "").toLowerCase();
  if (trust === "cyrene_recommended") {
    return { className: "recommended", label: t("settings.agentSourceTrust.recommended", "Cyrene recommended") };
  }
  if (trust === "external_verified") {
    return { className: "verified", label: t("settings.agentSourceTrust.externalVerified", "External · verified") };
  }
  return { className: "unverified", label: t("settings.agentSourceTrust.externalUnverified", "External · unverified") };
}

function agentInstallStateLabel(agent, t) {
  var state = String(agent.installState || agent.install_state || "");
  if (state === "installed") return t("settings.agentInstallState.installed", "Installed");
  if (state === "upgrade_available") return t("settings.agentInstallState.upgradeAvailable", "Upgrade available");
  return t("settings.agentInstallState.available", "Available");
}

function agentDriverLabel(driver, t) {
  driver = String(driver || "");
  if (!driver || driver === "cyrene_builtin") return t("settings.agentDriver.builtin", "Built-in");
  if (driver === "acp_stdio") return "ACP · stdio";
  return t("settings.agentDriver.unknown", { driver: driver }, "Other driver · " + driver);
}

function agentAuthLabel(agent, t) {
  var state = String(agent.authState || agent.auth_state || "not_configured").toLowerCase();
  var labels = {
    not_configured: t("settings.agentAuth.notConfigured", "Not configured"),
    authenticating: t("settings.agentAuth.authenticating", "Authenticating…"),
    connected: t("settings.agentAuth.connected", "Connected"),
    failed: t("settings.agentAuth.failed", "Login failed"),
    expired: t("settings.agentAuth.expired", "Login expired"),
  };
  return labels[state] || t("settings.agentState.unknownValue", { value: state || "—" }, "Unknown · " + (state || "—"));
}

function agentModelAccessLabel(agent, t) {
  var access = agent.modelAccess || agent.model_access || {};
  if (String(access.mode || "") === "agent_managed") {
    return t("settings.agentModelSource.agentManaged", "Agent-owned configuration");
  }
  var profileId = String(access.profileId || "primary");
  return t("settings.agentModelSource.cyrene", "Cyrene") + (profileId && profileId !== "primary" ? " · " + profileId : "");
}

function agentRuntimeLabel(agent, t) {
  var state = String(agent.runtimeState || agent.runtime_state || "").toLowerCase();
  if (!state || state === "unknown") return t("settings.agentRuntime.unknown", "Unknown");
  var labels = {
    ready: t("settings.agentRuntime.ready", "Ready"),
    running: t("settings.agentRuntime.running", "Running"),
    pending_transport: t("settings.agentRuntime.pendingTransport", "Not tested"),
    not_started: t("settings.agentRuntime.notStarted", "Starts on demand"),
    stopped: t("settings.agentRuntime.stopped", "Stopped"),
    crashed: t("settings.agentRuntime.crashed", "Crashed"),
    error: t("settings.agentRuntime.error", "Error"),
  };
  return labels[state] || t("settings.agentState.unknownValue", { value: state || "—" }, "Unknown · " + (state || "—"));
}

function agentUsabilityMeta(agent, t) {
  var installState = String(agent.installState || agent.install_state || "");
  var authState = String(agent.authState || agent.auth_state || "").toLowerCase();
  var runtimeState = String(agent.runtimeState || agent.runtime_state || "").toLowerCase();
  if (installState && installState !== "installed" && installState !== "upgrade_available") {
    return { usable: false, label: t("settings.agentUsability.notInstalled", "Unavailable · not installed") };
  }
  if (agent.enabled === false) {
    return { usable: false, label: t("settings.agentUsability.disabled", "Unavailable · disabled") };
  }
  if (authState === "expired" || authState === "failed") {
    return { usable: false, label: t("settings.agentUsability.auth", "Unavailable · login required") };
  }
  if (["error", "crashed", "failed"].indexOf(runtimeState) >= 0) {
    return { usable: false, label: t("settings.agentUsability.runtime", "Unavailable · runtime error") };
  }
  return { usable: true, label: t("settings.agentUsability.available", "Available in Composer") };
}

function agentCapabilityGroupLabel(group, t) {
  var labels = {
    session: t("settings.agentCapabilityGroup.session", "Session"),
    input: t("settings.agentCapabilityGroup.input", "Input"),
    output: t("settings.agentCapabilityGroup.output", "Output"),
    interaction: t("settings.agentCapabilityGroup.interaction", "Interaction"),
    model: t("settings.agentCapabilityGroup.model", "Model"),
  };
  return labels[group] || group;
}

function agentCapabilityStateLabel(state, t) {
  state = String(state || "unknown").toLowerCase();
  var labels = {
    supported: t("settings.agentCapabilityState.supported", "Supported"),
    unsupported: t("settings.agentCapabilityState.unsupported", "Unsupported"),
    unknown: t("settings.agentCapabilityState.unknown", "Unknown"),
    degraded: t("settings.agentCapabilityState.degraded", "Degraded"),
    agent_defined: t("settings.agentCapabilityState.agentDefined", "Agent-defined"),
  };
  return labels[state] || t("settings.agentState.unknownValue", { value: state || "—" }, "Unknown · " + (state || "—"));
}

function agentCapabilityRows(agent) {
  var caps = (agent && (agent.capabilities || agent.capabilitySummary)) || {};
  var rows = [];
  ["session", "input", "output", "interaction", "model"].forEach(function (group) {
    var section = caps[group];
    if (!section || typeof section !== "object") return;
    Object.keys(section).forEach(function (feature) {
      var state = section[feature];
      rows.push({
        group: group,
        feature: feature,
        state: Array.isArray(state) ? state.join(", ") : String(state || "unknown"),
      });
    });
  });
  return rows;
}

function agentDetailSettingsFetch(installationId, path, options) {
  return settingsFetch(
    "/api/agents/" + encodeURIComponent(String(installationId || "")) + path,
    options || { method: "GET" }
  ).then(function (response) {
    return response.json().catch(function () { return {}; });
  });
}

function notifyAgentCatalogChanged(reason) {
  try {
    window.dispatchEvent(new CustomEvent("cyrene:agents-changed", { detail: { reason: String(reason || "updated") } }));
  } catch (error) {}
}

function AgentInstallProposalModal(props) {
  var t = props.t;
  var [copied, setCopied] = useStateSt("");
  var [proposalDraft, setProposalDraft] = useStateSt("");
  var [proposalResult, setProposalResult] = useStateSt(null);
  var [proposalBusy, setProposalBusy] = useStateSt("");
  var [proposalError, setProposalError] = useStateSt("");
  function copy(label, value) {
    extensionCopyText(value).then(function () {
      setCopied(label);
      setTimeout(function () { setCopied(""); }, 1600);
    }).catch(function () {});
  }
  var origin = (window.location && window.location.origin) || "http://localhost:4242";
  var apiUrl = origin + AGENT_PROPOSAL_ENDPOINT;
  var manifestExample = JSON.parse(AGENT_MANIFEST_TEMPLATE);
  var requestExample = JSON.stringify({
    source: { type: "inline", manifest: manifestExample },
    requestedVersion: manifestExample.version,
  }, null, 2);
  var curlExample = [
    "curl -X POST " + JSON.stringify(apiUrl),
    "  -H \"Content-Type: application/json\"",
    "  --data-binary @install-agent-request.json",
  ].join(" \\\n");
  var responseExample = JSON.stringify({
    ok: true,
    proposalId: "agent_prop_…",
    agentId: "my-agent",
    version: "1.0.0",
    sourceTrust: "external_unverified",
    requiresConfirmation: true,
    status: "pending",
  }, null, 2);
  var guideLines = [
    t("settings.agentProposalGuideIntro", "Create a cyrene.agent/v1 Manifest, then submit it to Cyrene as an inline manifest or HTTPS manifest_url."),
    "",
    t("settings.agentProposalApi", "API") + ": POST " + apiUrl,
    t("settings.agentProposalContentType", "Content-Type") + ": application/json",
    t("settings.agentProposalManifestApi", "Manifest API") + ": cyrene.agent/v1",
    t("settings.agentProposalDrivers", "Supported") + ": ACP stdio",
    "",
    t("settings.agentProposalRequestLabel", "Complete request") + ":",
    requestExample,
    "",
    t("settings.agentProposalResponseLabel", "Expected response") + ":",
    responseExample,
    "",
    t("settings.agentProposalConfirmLabel", "Confirmation endpoint") + ": POST " + apiUrl + "/{proposalId}/confirm",
  ].join("\n");
  var fullGuide = guideLines + "\n\n" + t("settings.agentProposalExplain", "Agents submitted through this API first become a pending install proposal. They are installed only after you confirm the source and the program that will run.") + "\n\n" + t("settings.agentProposalExternalNote", "Agents installed through this API are marked as external sources and are never presented as Cyrene recommendations.");
  function createProposal() {
    var parsed;
    try {
      parsed = JSON.parse(String(proposalDraft || ""));
    } catch (error) {
      setProposalError(t("settings.agentProposalInvalidJson", "Enter a valid JSON request or cyrene.agent/v1 Manifest."));
      return;
    }
    var body = parsed && parsed.manifestApi === "cyrene.agent/v1"
      ? { source: { type: "inline", manifest: parsed }, requestedVersion: parsed.version || "" }
      : parsed;
    setProposalBusy("create"); setProposalError(""); setProposalResult(null);
    settingsFetch(AGENT_PROPOSAL_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (response) { return response.json(); }).then(function (payload) {
      setProposalResult(payload || {});
    }).catch(function (error) {
      setProposalError(error.message || String(error));
    }).finally(function () { setProposalBusy(""); });
  }
  function confirmProposal() {
    var proposalId = String(proposalResult && proposalResult.proposalId || "");
    if (!proposalId) return;
    setProposalBusy("confirm"); setProposalError("");
    settingsFetch(AGENT_PROPOSAL_ENDPOINT + "/" + encodeURIComponent(proposalId) + "/confirm", { method: "POST" })
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        setProposalResult({ ...proposalResult, confirmation: payload, status: "confirmed" });
        notifyAgentCatalogChanged("installed");
        if (props.onUpdated) props.onUpdated();
      }).catch(function (error) {
        setProposalError(error.message || String(error));
      }).finally(function () { setProposalBusy(""); });
  }
  return React.createElement("div", { className: "wb-extension-modal-scrim", onMouseDown: function (event) { if (event.target === event.currentTarget) props.onClose(); } },
    React.createElement("section", { className: "wb-extension-modal wb-agent-proposal-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "agent-proposal-title" },
      React.createElement("header", null,
        React.createElement("div", null,
          React.createElement("h3", { id: "agent-proposal-title" }, t("settings.agentProposalTitle", "Install another Agent")),
          React.createElement("p", null, t("settings.agentProposalSubtitle", "Copy the interface description for a developer or another Agent to generate a compatible cyrene.agent/v1 manifest."))
        ),
        React.createElement("button", { type: "button", className: "wb-extension-close", onClick: props.onClose, "aria-label": t("settings.close") }, "×")
      ),
      React.createElement("div", { className: "wb-agent-proposal-body" },
        React.createElement("dl", { className: "wb-agent-proposal-facts" },
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentProposalApi", "API")), React.createElement("dd", { className: "mono" }, "POST " + apiUrl)),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentProposalContentType", "Content-Type")), React.createElement("dd", { className: "mono" }, "application/json")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentProposalManifestApi", "Manifest API")), React.createElement("dd", { className: "mono" }, "cyrene.agent/v1")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentProposalDrivers", "Supported")), React.createElement("dd", null, "ACP stdio"))
        ),
        React.createElement("div", { className: "wb-agent-proposal-copy-row" },
          React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { copy("api", curlExample); } }, copied === "api" ? t("settings.copied", "Copied") : t("settings.agentProposalCopyApi", "Copy cURL")),
          React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { copy("example", requestExample); } }, copied === "example" ? t("settings.copied", "Copied") : t("settings.agentProposalCopyExample", "Copy complete request")),
          React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { copy("template", AGENT_MANIFEST_TEMPLATE); } }, copied === "template" ? t("settings.copied", "Copied") : t("settings.agentProposalCopyTemplate", "Copy manifest template"))
        ),
        React.createElement("div", { className: "wb-agent-proposal-preview-head" },
          React.createElement("strong", null, t("settings.agentProposalRequestLabel", "Complete request")),
          React.createElement("span", null, t("settings.agentProposalRequestHint", "Replace command and capability declarations with the external Agent's actual ACP entry point."))
        ),
        React.createElement("pre", { className: "wb-agent-proposal-example mono" }, requestExample),
        React.createElement("div", { className: "wb-agent-proposal-preview-head" },
          React.createElement("strong", null, t("settings.agentProposalResponseLabel", "Expected response")),
          React.createElement("span", null, t("settings.agentProposalResponseHint", "Use proposalId to call the confirmation endpoint after reviewing the source and executable."))
        ),
        React.createElement("pre", { className: "wb-agent-proposal-example mono" }, responseExample),
        React.createElement("section", { className: "wb-agent-proposal-submit" },
          React.createElement("div", { className: "wb-agent-proposal-preview-head" },
            React.createElement("strong", null, t("settings.agentProposalSubmitTitle", "Submit in Cyrene")),
            React.createElement("span", null, t("settings.agentProposalSubmitHint", "Paste either the complete request or a cyrene.agent/v1 Manifest. Cyrene validates it before showing confirmation."))
          ),
          React.createElement("textarea", {
            className: "wb-input mono",
            rows: 9,
            value: proposalDraft,
            placeholder: t("settings.agentProposalSubmitPlaceholder", "Paste request JSON or Manifest JSON"),
            onChange: function (event) { setProposalDraft(event.target.value); setProposalError(""); },
          }),
          React.createElement("div", { className: "wb-agent-proposal-submit-actions" },
            React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { setProposalDraft(requestExample); } }, t("settings.agentProposalUseExample", "Load example")),
            React.createElement("button", { type: "button", className: "wb-btn primary", disabled: proposalBusy || !proposalDraft.trim(), onClick: createProposal }, proposalBusy === "create" ? t("settings.loading", "Loading…") : t("settings.agentProposalCreate", "Validate and create proposal"))
          ),
          proposalError && React.createElement("div", { className: "wb-agent-unavailable", role: "alert" }, proposalError),
          proposalResult && React.createElement("div", { className: "wb-agent-proposal-result", role: "status" },
            React.createElement("strong", null, proposalResult.displayName || proposalResult.agentId || t("settings.agentProposalReady", "Proposal ready")),
            React.createElement("code", null, proposalResult.proposalId || "—"),
            React.createElement("span", null, [proposalResult.version, proposalResult.inspect && proposalResult.inspect.command, proposalResult.sourceTrust].filter(Boolean).join(" · ")),
            proposalResult.requiresConfirmation && proposalResult.status !== "confirmed" && React.createElement("button", { type: "button", className: "wb-btn primary", disabled: proposalBusy === "confirm", onClick: confirmProposal }, proposalBusy === "confirm" ? t("settings.loading", "Loading…") : t("settings.agentProposalConfirmInstall", "Confirm and install")),
            proposalResult.status === "confirmed" && React.createElement("b", null, t("settings.agentProposalInstallStarted", "Installation started"))
          )
        ),
        React.createElement("div", { className: "wb-agent-proposal-note" },
          React.createElement("strong", null, t("settings.agentProposalSafetyTitle", "Confirmation required")),
          React.createElement("p", null, t("settings.agentProposalExplain", "Agents submitted through this API first become a pending install proposal. They are installed only after you confirm the source and the program that will run.")),
          React.createElement("p", null, t("settings.agentProposalExternalNote", "Agents installed through this API are marked as external sources and are never presented as Cyrene recommendations.")),
          React.createElement("p", null, t("settings.agentProposalNoCredentials", "Copied content never contains Cyrene tokens, model keys or Agent credentials."))
        ),
        React.createElement("div", { className: "wb-agent-proposal-footer" },
          React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { copy("all", fullGuide); } }, copied === "all" ? t("settings.copied", "Copied") : t("settings.agentProposalCopyAll", "Copy full instructions")),
          React.createElement("button", { type: "button", className: "wb-btn primary", onClick: props.onClose }, t("settings.close"))
        )
      )
    )
  );
}

function AgentCapabilityTable({ agent, t }) {
  var rows = agentCapabilityRows(agent);
  if (!rows.length) {
    return React.createElement("div", { className: "wb-agent-capabilities-empty" },
      t("settings.agentCapabilitiesEmpty", "No capabilities have been declared or detected yet. Use Test connection to probe this Agent.")
    );
  }
  var groups = {};
  rows.forEach(function (row) {
    (groups[row.group] = groups[row.group] || []).push(row);
  });
  return React.createElement("div", { className: "wb-agent-capabilities" },
    ["session", "input", "output", "interaction", "model"].map(function (group) {
      if (!groups[group]) return null;
      return React.createElement("section", { key: group, className: "wb-agent-capability-group" },
        React.createElement("h5", null, agentCapabilityGroupLabel(group, t)),
        React.createElement("ul", null, groups[group].map(function (row) {
          return React.createElement("li", { key: row.group + "." + row.feature },
            React.createElement("code", null, row.feature),
            React.createElement("span", null, agentCapabilityStateLabel(row.state, t))
          );
        }))
      );
    })
  );
}

function AgentCard(props) {
  var agent = props.agent || {};
  var t = props.t;
  var [localBusy, setLocalBusy] = useStateSt("");
  var busy = props.busy || localBusy;
  var expanded = props.expanded;
  var trust = agentSourceTrustMeta(agent, t);
  var usability = agentUsabilityMeta(agent, t);
  var installationId = String(agent.installationId || "");
  var [modelAccessDraft, setModelAccessDraft] = useStateSt(null);
  var [probeResult, setProbeResult] = useStateSt(null);
  var [diagnostics, setDiagnostics] = useStateSt(null);
  var [authResult, setAuthResult] = useStateSt(null);
  function loadDetail() {
    agentDetailSettingsFetch(installationId, "").then(function (payload) {
      if (payload && payload.agent) props.onChanged(payload.agent);
    }).catch(function () {});
  }
  function toggleEnabled() {
    props.onToggle(agent);
  }
  function runProbe() {
    setLocalBusy("probe");
    agentDetailSettingsFetch(installationId, "/probe", { method: "POST" }).then(function (payload) {
      setProbeResult(payload || {});
      if (payload && payload.agent) props.onChanged(payload.agent);
      else loadDetail();
      notifyAgentCatalogChanged("probe");
      setLocalBusy("");
    }).catch(function () { setLocalBusy(""); });
  }
  function runRestart() {
    setLocalBusy("restart");
    agentDetailSettingsFetch(installationId, "/restart", { method: "POST" }).then(function (payload) {
      setProbeResult(payload || {});
      notifyAgentCatalogChanged("restart");
      setLocalBusy("");
    }).catch(function () { setLocalBusy(""); });
  }
  function runAuth(action) {
    setLocalBusy("auth:" + action);
    agentDetailSettingsFetch(installationId, "/auth/" + action, { method: "POST" }).then(function (payload) {
      setAuthResult(payload || {});
      notifyAgentCatalogChanged("auth");
      setLocalBusy("");
    }).catch(function () { setLocalBusy(""); });
  }
  function loadDiagnostics() {
    setLocalBusy("diagnostics");
    agentDetailSettingsFetch(installationId, "/diagnostics").then(function (payload) {
      setDiagnostics(payload || {});
      setLocalBusy("");
    }).catch(function () { setLocalBusy(""); });
  }
  function saveModelAccess(mode, profileId) {
    var body = { modelAccess: { mode: mode } };
    if (mode === "cyrene_managed") body.modelAccess.profileId = String(profileId || "primary") || "primary";
    setLocalBusy("model");
    agentDetailSettingsFetch(installationId, "/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (payload) {
      if (payload && payload.agent) props.onChanged(payload.agent);
      notifyAgentCatalogChanged("model_access");
      setLocalBusy("");
    }).catch(function () { setLocalBusy(""); });
  }
  function placeholderNote(result) {
    if (!result) return "";
    if (result.ok !== false && !result.error) return "";
    return String(result.detail || result.error || "");
  }
  function localizedDiagnosticsNote(payload) {
    var noteCode = String(payload && (payload.noteCode || payload.reason) || "");
    if (noteCode === "starts_on_demand") {
      return t("settings.agentDiagnosticsStartsOnDemand", "ACP stdio starts on demand. Diagnostics never expose process environment or credentials.");
    }
    return "";
  }
  var version = String(agent.version || "");
  var driver = agentDriverLabel(agent.driver || agent.runtime && agent.runtime.transport, t);
  var authNote = placeholderNote(authResult);
  var probeNote = placeholderNote(probeResult);
  var diagnosticsNote = localizedDiagnosticsNote(diagnostics);
  return React.createElement("article", { className: "wb-extension-card wb-agent-card" + (expanded ? " expanded" : ""), "data-installation-id": installationId },
    React.createElement("div", { className: "wb-extension-card-main" },
      React.createElement("button", {
        type: "button",
        className: "wb-extension-card-summary",
        onClick: props.onToggleExpand,
        "aria-expanded": expanded ? "true" : "false",
        "aria-label": t("settings.extensionDetailsFor", { name: agent.displayName || agent.agentId }),
      },
        React.createElement("span", { className: "wb-extension-glyph agent extension-agent" }, React.createElement(ExtensionGlyph, { id: agent.agentId, kind: "agent", label: agent.displayName || agent.agentId })),
        React.createElement("span", { className: "wb-extension-copy" },
          React.createElement("span", { className: "wb-extension-title-row" },
            React.createElement("strong", null, agent.displayName || agent.agentId || "—"),
            React.createElement("span", { className: "wb-extension-type" }, t("settings.extensionType.agent", "Agent")),
            React.createElement("span", { className: "wb-agent-trust " + trust.className }, trust.label),
            React.createElement("span", { className: "wb-agent-usability " + (usability.usable ? "available" : "unavailable") }, usability.label)
          ),
          React.createElement("span", { className: "wb-extension-description" },
            [agentInstallStateLabel(agent, t), agentDriverLabel(agent.driver, t), agentAuthLabel(agent, t), agentModelAccessLabel(agent, t)].filter(Boolean).join(" · ")
          ),
          React.createElement("span", { className: "wb-extension-meta" },
            React.createElement("span", { className: "wb-agent-state" + (agent.enabled === false ? " disabled" : "") },
              React.createElement("span", { className: "wb-extension-status-dot", "aria-hidden": "true" }),
              agent.enabled === false ? t("settings.extensionDisabled") : agentRuntimeLabel(agent, t)
            ),
            version && React.createElement("span", { className: "mono" }, version)
          )
        )
      ),
      React.createElement("div", { className: "wb-extension-actions" },
        React.createElement("button", { type: "button", className: "wb-btn", disabled: busy, onClick: toggleEnabled }, agent.enabled === false ? t("settings.enable") : t("settings.disable")),
        React.createElement("button", { type: "button", className: "wb-btn danger", disabled: busy, onClick: function () { props.onRemove(agent); } }, t("settings.uninstall"))
      ),
      React.createElement("button", {
        type: "button",
        className: "wb-extension-expand-button",
        onClick: props.onToggleExpand,
        "aria-expanded": expanded ? "true" : "false",
        "aria-label": t("settings.extensionDetailsFor", { name: agent.displayName || agent.agentId }),
      }, React.createElement("span", { className: "wb-extension-chevron", "aria-hidden": "true" }, ExternalChevron()))
    ),
    expanded && React.createElement("div", { className: "wb-extension-details wb-agent-details" },
      React.createElement("div", { className: "wb-extension-enabled-row" },
        React.createElement("div", null,
          React.createElement("strong", null, t("settings.extensionEnabledTitle")),
          React.createElement("small", null, t("settings.agentEnabledHint", "Disabled Agents are not selectable in the Composer."))
        ),
        Toggle(agent.enabled !== false, toggleEnabled, busy, t("settings.extensionToggle", { name: agent.displayName || agent.agentId }))
      ),
      React.createElement("section", { className: "wb-agent-detail-section", "aria-labelledby": "agent-overview-" + installationId },
        React.createElement("h4", { id: "agent-overview-" + installationId }, t("settings.agentSectionOverview", "Overview")),
        React.createElement("dl", null,
          React.createElement("div", null, React.createElement("dt", null, t("settings.extensionVersion")), React.createElement("dd", { className: "mono" }, version || "—")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.extensionSource")), React.createElement("dd", null, trust.label)),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentPublisher", "Publisher")), React.createElement("dd", null, agent.publisher || "—")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentSourceUrl", "Manifest / repository")), React.createElement("dd", { className: "mono" }, String((agent.source || {}).url || agent.repository || "—"))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentChecksum", "SHA-256")), React.createElement("dd", { className: "mono" }, String((agent.checksums || {}).sha256 || t("settings.agentUnverified", "Unverified")))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentInstallationId")), React.createElement("dd", { className: "mono" }, installationId || "—")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentDriverProtocol", "Driver / protocol")), React.createElement("dd", { className: "mono" }, driver || "—")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.extensionHealth")), React.createElement("dd", null, agentRuntimeLabel(agent, t))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentLoginState", "Login")), React.createElement("dd", null, agentAuthLabel(agent, t))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentModelSourceShort", "Model source")), React.createElement("dd", null, agentModelAccessLabel(agent, t))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentComposerAvailability", "Composer availability")), React.createElement("dd", { className: "wb-agent-usability " + (usability.usable ? "available" : "unavailable") }, usability.label))
        )
      ),
      React.createElement("section", { className: "wb-agent-detail-section", "aria-labelledby": "agent-auth-" + installationId },
        React.createElement("h4", { id: "agent-auth-" + installationId }, t("settings.agentSectionAuthModel", "Login & model")),
        React.createElement("div", { className: "wb-agent-model-access" },
          React.createElement("label", null,
            React.createElement("input", { type: "radio", name: "model-access-" + installationId, checked: String((agent.modelAccess || {}).mode || "cyrene_managed") !== "agent_managed", onChange: function () { saveModelAccess("cyrene_managed", String((agent.modelAccess || {}).profileId || "primary")); } }),
            React.createElement("span", null, React.createElement("strong", null, t("settings.agentModelCyrene", "Use Cyrene models")), React.createElement("small", null, t("settings.agentModelCyreneHint", "Routes through the Cyrene Model Gateway; no long-lived key is exposed to the Agent.")))
          ),
          React.createElement("label", null,
            React.createElement("input", { type: "radio", name: "model-access-" + installationId, checked: String((agent.modelAccess || {}).mode || "") === "agent_managed", onChange: function () { saveModelAccess("agent_managed"); } }),
            React.createElement("span", null, React.createElement("strong", null, t("settings.agentModelOwn", "Use the Agent's own configuration")), React.createElement("small", null, t("settings.agentModelOwnHint", "The Agent keeps its own OAuth, API key or environment configuration.")))
          )
        ),
        React.createElement("div", { className: "wb-agent-action-row" },
          React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "auth:start", onClick: function () { runAuth("start"); } }, t("settings.agentLoginStart", "Start login")),
          React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "auth:logout", onClick: function () { runAuth("logout"); } }, t("settings.agentLoginLogout", "Log out"))
        ),
        authNote && React.createElement("div", { className: "wb-agent-unavailable", role: "status" }, authNote),
        !authNote && React.createElement("p", { className: "wb-hint" }, t("settings.agentAuthHint", "Login is handled through the methods advertised by the Agent. Some Agents require login in their own terminal instead."))
      ),
      React.createElement("section", { className: "wb-agent-detail-section", "aria-labelledby": "agent-caps-" + installationId },
        React.createElement("h4", { id: "agent-caps-" + installationId }, t("settings.agentSectionCapabilities", "Capabilities")),
        React.createElement(AgentCapabilityTable, { agent: agent, t: t })
      ),
      React.createElement("section", { className: "wb-agent-detail-section", "aria-labelledby": "agent-runtime-" + installationId },
        React.createElement("h4", { id: "agent-runtime-" + installationId }, t("settings.agentSectionRuntime", "Runtime")),
        React.createElement("dl", null,
          React.createElement("div", null, React.createElement("dt", null, t("settings.placeholderCommand", "Command")), React.createElement("dd", { className: "mono" }, agent.command || "—")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentRuntimeState", "Process state")), React.createElement("dd", null, agentRuntimeLabel(agent, t))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentLastStarted", "Last started")), React.createElement("dd", { className: "mono" }, (agent.runtime && agent.runtime.lastStartedAt) || "—"))
        ),
        React.createElement("div", { className: "wb-agent-action-row" },
          React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "restart", onClick: runRestart }, t("settings.agentRestart", "Restart Agent")),
          React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "probe", onClick: runProbe }, t("settings.agentProbe", "Test connection"))
        ),
        probeNote && React.createElement("div", { className: "wb-agent-unavailable", role: "status" }, probeNote)
      ),
      React.createElement("section", { className: "wb-agent-detail-section", "aria-labelledby": "agent-diagnostics-" + installationId },
        React.createElement("h4", { id: "agent-diagnostics-" + installationId }, t("settings.agentSectionDiagnostics", "Diagnostics")),
        React.createElement("div", { className: "wb-agent-action-row" },
          React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "diagnostics", onClick: loadDiagnostics }, t("settings.agentLoadDiagnostics", "Load diagnostics"))
        ),
        diagnosticsNote && React.createElement("p", { className: "wb-hint" }, diagnosticsNote),
        (diagnostics && (diagnostics.lastErrors || []).length > 0) && React.createElement("ul", { className: "wb-agent-errors" },
          diagnostics.lastErrors.map(function (error, index) {
            return React.createElement("li", { key: index, className: "mono" }, String(error));
          })
        )
      )
    )
  );
}

function AgentTabPanel(props) {
  var t = props.t;
  var recommended = Array.isArray(props.recommended) ? props.recommended : [];
  var installed = Array.isArray(props.installed) ? props.installed : [];
  return React.createElement("div", { className: "wb-agent-tab" },
    React.createElement("section", { className: "wb-agent-section", "aria-labelledby": "wb-agent-recommended-title" },
      React.createElement("div", { className: "wb-agent-section-head" },
        React.createElement("h4", { id: "wb-agent-recommended-title" }, t("settings.agentRecommended", "Recommended")),
        React.createElement("span", null, t("settings.agentRecommendedHint", "Curated by Cyrene"))
      ),
      recommended.length === 0
        ? React.createElement("div", { className: "wb-extensions-empty" }, t("settings.agentRecommendedEmpty", "No recommended Agents available."))
        : React.createElement("div", { className: "wb-agent-recommended-list" },
            recommended.map(function (item) {
              var state = String(item.installState || "available");
              var installedInstallationId = String(item.installationId || "");
              return React.createElement("article", { key: item.agentId, className: "wb-extension-card wb-agent-recommended-card" },
                React.createElement("div", { className: "wb-extension-card-main" },
                  React.createElement("div", { className: "wb-extension-card-summary" },
                    React.createElement("span", { className: "wb-extension-glyph agent extension-agent" }, React.createElement(ExtensionGlyph, { id: item.agentId, kind: "agent", label: item.displayName || item.agentId })),
                    React.createElement("span", { className: "wb-extension-copy" },
                      React.createElement("span", { className: "wb-extension-title-row" },
                        React.createElement("strong", null, item.displayName || item.agentId),
                        React.createElement("span", { className: "wb-extension-type" }, t("settings.extensionType.agent", "Agent"))
                      ),
                      React.createElement("span", { className: "wb-extension-description" }, String(item.description || "")),
                      React.createElement("span", { className: "wb-extension-meta" },
                        React.createElement("span", { className: "wb-agent-state" }, agentInstallStateLabel(item, t)),
                        item.version && React.createElement("span", { className: "mono" }, item.version),
                        React.createElement("span", null, agentDriverLabel(item.driver, t))
                      )
                    )
                  ),
                  React.createElement("div", { className: "wb-extension-actions" },
                    state === "installed"
                      ? (installedInstallationId
                          ? React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { props.onOpenInstalled(installedInstallationId); } }, t("settings.agentOpenDetails", "Details"))
                          : React.createElement("button", { type: "button", className: "wb-btn", disabled: true }, t("settings.agentInstallState.installed", "Installed")))
                      : state === "upgrade_available"
                        ? React.createElement("button", { type: "button", className: "wb-btn primary", disabled: props.busy, onClick: function () { props.onInstall(item); } }, t("settings.agentUpgrade", "Upgrade"))
                        : React.createElement("button", { type: "button", className: "wb-btn primary", disabled: props.busy, onClick: function () { props.onInstall(item); } }, t("settings.install"))
                  )
                )
              );
            })
          )
    ),
    React.createElement("section", { className: "wb-agent-section", "aria-labelledby": "wb-agent-installed-title" },
      React.createElement("div", { className: "wb-agent-section-head" },
        React.createElement("h4", { id: "wb-agent-installed-title" }, t("settings.agentInstalled", "Installed")),
        React.createElement("span", null, t("settings.agentInstalledCount", { n: installed.length }))
      ),
      installed.length === 0
        ? React.createElement("div", { className: "wb-extensions-empty" }, t("settings.agentInstalledEmpty", "No Agents installed yet."))
        : React.createElement("div", { className: "wb-agent-installed-list" },
            installed.map(function (agent) {
              var installationId = String(agent.installationId || "");
              var expanded = props.expandedId === installationId;
              return React.createElement(AgentCard, {
                key: installationId || agent.agentId,
                agent: agent,
                t: t,
                busy: props.busy === "agent:" + installationId,
                expanded: expanded,
                onToggleExpand: function () { props.onToggleExpand(expanded ? "" : installationId); },
                onToggle: props.onToggle,
                onRemove: props.onRemove,
                onChanged: function (updated) {
                  if (props.onChanged) props.onChanged(updated);
                },
              });
            })
          )
    ),
    React.createElement("div", { className: "wb-agent-install-other" },
      React.createElement("button", { type: "button", className: "wb-btn primary", onClick: props.onOpenProposal },
        React.createElement("span", { "aria-hidden": "true" }, "+ "),
        t("settings.agentInstallOther", "Install another Agent")
      ),
      React.createElement("p", null, t("settings.agentInstallOtherHint", "Non-recommended Agents are marked as external sources and appear in the Installed list after confirmation."))
    )
  );
}

function SkillFileDirectory(props) {
  var files = Array.isArray(props.files) ? props.files : [];
  var entrypoint = String(props.entrypoint || "SKILL.md");
  var rows = [];
  var seenDirectories = {};
  files.forEach(function (file) {
    var path = String(file.path || file.name || "");
    var parts = path.split(/[\\/]/).filter(Boolean);
    parts.slice(0, -1).forEach(function (part, index) {
      var directoryPath = parts.slice(0, index + 1).join("/");
      if (seenDirectories[directoryPath]) return;
      seenDirectories[directoryPath] = true;
      rows.push({ key: "directory:" + directoryPath, name: part + "/", path: directoryPath, depth: index, directory: true });
    });
    rows.push({
      key: "file:" + path,
      name: parts[parts.length - 1] || path || "—",
      path: path,
      depth: Math.max(0, parts.length - 1),
      directory: false,
      size: Number(file.size || 0),
    });
  });
  return React.createElement("div", { className: "wb-extension-skill-files", role: "list" },
    rows.map(function (row) {
      var isEntrypoint = !row.directory && (row.path === entrypoint || row.name === entrypoint);
      var sizeLabel = row.size < 1024 ? row.size + " B" : (row.size / 1024).toFixed(1) + " KB";
      return React.createElement("div", {
        key: row.key,
        className: "wb-extension-skill-file" + (row.directory ? " directory" : "") + (isEntrypoint ? " entrypoint" : ""),
        role: "listitem",
        style: { paddingLeft: (11 + row.depth * 14) + "px" },
        title: row.path,
      },
        React.createElement("span", { className: "wb-extension-skill-file-name mono" }, row.name),
        isEntrypoint && React.createElement("span", { className: "wb-extension-skill-entrypoint" }, "SKILL"),
        !row.directory && React.createElement("span", { className: "wb-extension-skill-file-size" }, sizeLabel)
      );
    })
  );
}

function ExtensionCard(props) {
  var item = props.item;
  var t = props.t;
  var busy = props.busy;
  var [expanded, setExpanded] = useStateSt(false);
  var canInstall = (item.capabilities || []).indexOf("install") >= 0;
  var canUninstall = (item.capabilities || []).some(function (value) { return value === "uninstall" || value === "uninstall_managed" || value === "remove"; });
  var canToggle = item.observed_state === "installed" && (item.capabilities || []).some(function (value) { return value === "enable" || value === "disable"; });
  var canUseLocalProgram = (item.capabilities || []).indexOf("bind_system") >= 0;
  var canStopUsingLocalProgram = (item.capabilities || []).indexOf("unbind_system") >= 0;
  var canConfigureHook = item.kind === "cli" && item.observed_state === "installed";
  var typeText = t("settings.extensionType." + item.kind);
  var version = String(item.version || item.recommended_version || "").replace(/^python\s+/i, "").replace(/^v/, "");
  var displayName = extensionDisplayName(item, t);
  var displayDescription = extensionDisplayDescription(item, t);
  return React.createElement("article", { className: "wb-extension-card" + (expanded ? " expanded" : "") },
    React.createElement("div", { className: "wb-extension-card-main" },
      React.createElement("button", {
        type: "button", className: "wb-extension-card-summary", onClick: function () { setExpanded(!expanded); },
        "aria-expanded": expanded ? "true" : "false", "aria-label": t("settings.extensionDetailsFor", { name: displayName }),
      },
        React.createElement("span", { className: "wb-extension-glyph " + item.kind + " extension-" + String(item.id || "").replace(/[^a-z0-9_-]/gi, "-").toLowerCase() }, React.createElement(ExtensionGlyph, { id: item.id, kind: item.kind, label: displayName })),
        React.createElement("span", { className: "wb-extension-copy" },
          React.createElement("span", { className: "wb-extension-title-row" },
            React.createElement("strong", null, displayName),
            React.createElement("span", { className: "wb-extension-type" }, typeText),
            item.id === "python" && item.observed_state === "missing" && React.createElement("span", { className: "wb-extension-recommended" }, t("settings.extensionRecommendedInstall")),
          ),
          React.createElement("span", { className: "wb-extension-description" }, displayDescription),
          React.createElement("span", { className: "wb-extension-meta" },
            React.createElement(ExtensionStatus, { item: item, t: t }),
            version && React.createElement("span", { className: "mono" }, version),
            item.tool_count > 0 && React.createElement("span", null, t("settings.toolsCount", { n: item.tool_count })),
          ),
        ),
      ),
      React.createElement("div", { className: "wb-extension-actions" },
        canInstall && React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button", disabled: busy, onClick: function () { props.onInstall(item); } }, t("settings.install")),
        canUninstall && (item.ownership === "cyrene" || item.managed_available) && React.createElement("button", { type: "button", className: "wb-btn danger", disabled: busy, onClick: function () { props.onRemove(item); } }, item.kind === "mcp" ? t("settings.delete") : t("settings.uninstall")),
      ),
      React.createElement("button", {
        type: "button",
        className: "wb-extension-expand-button",
        onClick: function () { setExpanded(!expanded); },
        "aria-expanded": expanded ? "true" : "false",
        "aria-label": t("settings.extensionDetailsFor", { name: displayName }),
      }, React.createElement("span", { className: "wb-extension-chevron", "aria-hidden": "true" }, ExternalChevron())),
    ),
    expanded && React.createElement("div", { className: "wb-extension-details" },
      canToggle && React.createElement("div", { className: "wb-extension-enabled-row" },
        React.createElement("div", null,
          React.createElement("strong", null, t("settings.extensionEnabledTitle")),
          React.createElement("small", null, t("settings.extensionEnabledHint." + item.kind))
        ),
        Toggle(item.enabled !== false, function () { props.onToggle(item); }, busy, t("settings.extensionToggle", { name: displayName }))
      ),
      React.createElement("dl", null,
        React.createElement("div", null, React.createElement("dt", null, t("settings.extensionSource")), React.createElement("dd", null, extensionSourceLabel(item, t))),
        React.createElement("div", null, React.createElement("dt", null, t("settings.extensionVersion")), React.createElement("dd", { className: "mono" }, version || "—")),
        React.createElement("div", null, React.createElement("dt", null, t("settings.extensionPath")), React.createElement("dd", { className: "mono" }, item.path || "—")),
        React.createElement("div", null, React.createElement("dt", null, t("settings.extensionHealth")), React.createElement("dd", null, extensionHealthLabel(item, t))),
        item.ownership === "system" && item.managed_available && React.createElement("div", null, React.createElement("dt", null, t("settings.extensionManagedInstalled")), React.createElement("dd", { className: "mono" }, [item.managed_version, item.managed_path].filter(Boolean).join(" · "))),
      ),
      item.kind === "mcp" && React.createElement("section", { className: "wb-extension-mcp-tools", "aria-labelledby": "mcp-tools-" + item.id },
        React.createElement("div", { className: "wb-extension-mcp-tools-head" },
          React.createElement("h4", { id: "mcp-tools-" + item.id }, t("settings.extensionMcpTools")),
          React.createElement("span", null, t("settings.toolsCount", { n: (item.tools || []).length }))
        ),
        (item.tools || []).length
          ? React.createElement("ul", null, item.tools.map(function (tool) { return React.createElement("li", { key: tool.name },
              React.createElement("code", null, tool.name),
              React.createElement("p", null, tool.description || t("settings.extensionMcpToolNoDescription"))
            ); }))
          : React.createElement("div", { className: "wb-extension-mcp-tools-empty" }, t(item.enabled === false ? "settings.extensionMcpToolsDisabled" : item.connection_status !== "connected" ? "settings.extensionMcpToolsDisconnected" : "settings.extensionMcpToolsEmpty"))
      ),
      item.kind === "toolchain" && (item.versions || []).length > 1 && React.createElement("label", { className: "wb-extension-version-select" },
        React.createElement("span", null, t("settings.extensionDefaultVersion")),
        React.createElement("select", { className: "wb-select", value: item.default_version || item.version, disabled: busy, onChange: function (event) { props.onDefault(item, event.target.value); } },
          item.versions.map(function (value) { return React.createElement("option", { key: value, value: value }, value); })
        )
      ),
      (canUseLocalProgram || canStopUsingLocalProgram) && React.createElement("div", { className: "wb-extension-detail-actions" },
        canUseLocalProgram && React.createElement("button", { type: "button", className: "wb-btn", disabled: busy, onClick: function () { props.onBind(item); } }, t(item.manual_binding ? "settings.extensionChangeSystem" : "settings.extensionBindSystem")),
        canStopUsingLocalProgram && React.createElement("button", { type: "button", className: "wb-btn danger", disabled: busy, onClick: function () { props.onUnbind(item); } }, t("settings.extensionUnbindSystem"))
      ),
      canConfigureHook && React.createElement("div", { className: "wb-extension-hook-action" },
        React.createElement("div", { className: "wb-extension-hook-copy" },
          React.createElement("span", { className: "wb-extension-hook-icon", "aria-hidden": "true" }, React.createElement(AutomationIcon)),
          React.createElement("div", null,
          React.createElement("strong", null, t("settings.extensionHookTitle")),
          React.createElement("small", null, t("settings.extensionHookHint"))
          )
        ),
        React.createElement("button", { type: "button", className: "wb-btn", disabled: busy, onClick: function () { props.onConfigureHook(item); } }, t("settings.extensionConfigureHook"))
      ),
      item.kind === "skill" && React.createElement("div", { className: "wb-extension-skill-content" },
        React.createElement("section", { className: "wb-extension-skill-document", "aria-labelledby": "skill-content-" + item.id },
          React.createElement("h4", { id: "skill-content-" + item.id }, t("settings.extensionSkillContent", "Skill instructions")),
          item.preview
            ? React.createElement("div", {
                className: "wb-extension-skill-markdown markdown",
                dangerouslySetInnerHTML: { __html: renderSettingsMarkdown(item.preview) },
              })
            : React.createElement("div", { className: "wb-extension-skill-empty" }, t("settings.extensionSkillContentEmpty", "No Skill instructions found."))
        ),
        React.createElement("aside", { className: "wb-extension-skill-directory", "aria-labelledby": "skill-files-" + item.id },
          React.createElement("div", { className: "wb-extension-skill-directory-head" },
            React.createElement("h4", { id: "skill-files-" + item.id }, t("settings.extensionSkillDirectory", "Skill file directory")),
            React.createElement("span", null, t("settings.extensionSkillFileCount", { n: (item.files || []).length }, "{n} files"))
          ),
          (item.files || []).length
            ? React.createElement(SkillFileDirectory, { files: item.files, entrypoint: item.entrypoint_name })
            : React.createElement("div", { className: "wb-extension-skill-empty" }, t("settings.extensionSkillDirectoryEmpty", "No files found."))
        )
      ),
    )
  );
}

function ExtensionsPanel(p) {
  var t = p.t;
  var [data, setData] = useStateSt({ recommended: [], skills: [], mcp: [], cli: [], toolchains: [], tasks: [] });
  var [category, setCategory] = useStateSt("recommended");
  var [query, setQuery] = useStateSt("");
  var [loading, setLoading] = useStateSt(true);
  var [busy, setBusy] = useStateSt("");
  var [notice, setNotice] = useStateSt("");
  var [noticeKind, setNoticeKind] = useStateSt("info");
  var [installOpen, setInstallOpen] = useStateSt(false);
  var [installKind, setInstallKind] = useStateSt("skill");
  var [remoteQuery, setRemoteQuery] = useStateSt("");
  var [remoteResults, setRemoteResults] = useStateSt([]);
  var [remoteCursor, setRemoteCursor] = useStateSt("");
  var [remoteLoading, setRemoteLoading] = useStateSt(false);
  var [advanced, setAdvanced] = useStateSt(false);
  var [sourceOpen, setSourceOpen] = useStateSt(false);
  var [sources, setSources] = useStateSt({});
  var [sourceTesting, setSourceTesting] = useStateSt(false);
  var [sourceHealth, setSourceHealth] = useStateSt(null);
  var [skillSelection, setSkillSelection] = useStateSt(null);
  var [manualMcp, setManualMcp] = useStateSt({ name: "", transport: "streamable_http", url: "", command: "", args: "", version: "", headers: "", env: "" });
  var [manualMcpOpen, setManualMcpOpen] = useStateSt(false);
  var [requestedVersion, setRequestedVersion] = useStateSt("");
  var [texChoice, setTexChoice] = useStateSt("tinytex");
  var [bindItem, setBindItem] = useStateSt(null);
  var [bindPath, setBindPath] = useStateSt("");
  var [auditRecords, setAuditRecords] = useStateSt([]);
  var [hooksOpen, setHooksOpen] = useStateSt(false);
  var [hooksData, setHooksData] = useStateSt({ hooks: [], proposals: [], configuration_results: {} });
  var [hookAudit, setHookAudit] = useStateSt([]);
  var emptyHookDraft = { id: "", name: "", description: "", event: "PreToolUse", matcher: "*", priority: 100, failure_policy: "open", timeout_seconds: 10, enabled: false, runner: { type: "command", executable: "", args: [], env: {} } };
  var [hookDraft, setHookDraft] = useStateSt(emptyHookDraft);
  var [hookArgsText, setHookArgsText] = useStateSt("");
  var [hookEditorOpen, setHookEditorOpen] = useStateSt(false);
  var [taskClock, setTaskClock] = useStateSt(Date.now());
  var [agentProposalOpen, setAgentProposalOpen] = useStateSt(false);
  var [agentExpandedId, setAgentExpandedId] = useStateSt("");

  function tell(text, kind) {
    if (showSettingsToast(text, kind || "info")) {
      setNotice("");
      return;
    }
    setNotice(text || ""); setNoticeKind(kind || "info");
    if (text) setTimeout(function () { setNotice(""); }, 5000);
  }

  function load() {
    return settingsFetch("/api/extensions").then(readSettingsResponse).then(function (payload) {
      setData(payload); setLoading(false);
    }).catch(function (error) { setLoading(false); tell(error.message || t("settings.networkError"), "error"); });
  }

  useEffectSt(function () { load(); }, []);
  useEffectSt(function () {
    function openAgentHooks() { loadHooks(true); }
    window.addEventListener("cyrene:open-agent-hooks", openAgentHooks);
    return function () { window.removeEventListener("cyrene:open-agent-hooks", openAgentHooks); };
  }, []);
  // The Composer's Agent submenu opens "Extensions → installed Agent details"
  // for unavailable Agents through this event (handoff §8.2).
  useEffectSt(function () {
    function openAgentDetail(event) {
      var detail = (event && event.detail) || {};
      setCategory("agent");
      setQuery("");
      var installationId = String(detail.installationId || "");
      if (installationId && installationId !== "agent_cyrene_builtin") {
        setAgentExpandedId(installationId);
      }
    }
    window.addEventListener("cyrene:open-agent-detail", openAgentDetail);
    return function () { window.removeEventListener("cyrene:open-agent-detail", openAgentDetail); };
  }, []);
  useEffectSt(function () {
    var active = (data.tasks || []).some(function (task) { return ["queued", "running", "cancelling"].indexOf(task.status) >= 0; });
    if (!active) return undefined;
    var timer = setInterval(load, 1200);
    return function () { clearInterval(timer); };
  }, [data.tasks]);
  useEffectSt(function () {
    var expirations = (data.tasks || []).filter(function (task) { return ["failed", "interrupted"].indexOf(task.status) >= 0; }).map(function (task) { return Date.parse(task.finished_at || "") + 30000; }).filter(function (value) { return Number.isFinite(value) && value > taskClock; });
    if (!expirations.length) return undefined;
    var timer = setTimeout(function () { setTaskClock(Date.now()); }, Math.max(0, Math.min.apply(Math, expirations) - Date.now() + 50));
    return function () { clearTimeout(timer); };
  }, [data.tasks, taskClock]);

  function openInstaller(kind) {
    setInstallKind(kind); setRemoteQuery(""); setRemoteResults([]); setRemoteCursor(""); setSkillSelection(null); setRequestedVersion(""); setManualMcpOpen(false); setInstallOpen(true);
  }

  function startAgentInstall(item) {
    var displayName = item.displayName || item.agentId || item.name || item.id;
    window.CyreneUI.require("feedback").confirmModal({
      title: t("settings.extensionInstallConfirmTitle"),
      body: t("settings.agentInstallConfirmBody", { name: displayName, version: item.version || item.recommended_version || "" }),
      confirmLabel: t("settings.install"),
    }).then(function (ok) {
      if (!ok) return;
      setBusy("agent-install:" + String(item.agentId || ""));
      settingsFetch("/api/extensions/install", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "agent", extension_id: item.agentId || item.id }),
      }).then(readSettingsResponse).then(function () {
        notifyAgentCatalogChanged("installed");
        tell(t("settings.extensionInstallStarted"), "success"); return load();
      }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
    });
  }

  function toggleAgent(agent) {
    var installationId = String(agent.installationId || "");
    if (!installationId) return;
    setBusy("agent:" + installationId);
    settingsFetch("/api/extensions/agent/" + encodeURIComponent(installationId) + "/enabled", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: agent.enabled === false }),
    }).then(readSettingsResponse).then(function () { notifyAgentCatalogChanged("enabled"); return load(); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  function removeAgent(agent) {
    var installationId = String(agent.installationId || "");
    var agentId = String(agent.agentId || "");
    window.CyreneUI.require("feedback").confirmModal({
      body: t("settings.agentRemoveConfirm", { name: agent.displayName || agentId || installationId }),
      confirmLabel: t("settings.uninstall"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      setBusy("agent:" + installationId);
      settingsFetch("/api/extensions/agent/" + encodeURIComponent(agentId || installationId), { method: "DELETE" })
        .then(readSettingsResponse).then(function () { notifyAgentCatalogChanged("removed"); tell(t("settings.saved"), "success"); return load(); })
        .catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
    });
  }

  function replaceAgentCard(updated) {
    setData(function (current) {
      var agents = (current.agents && typeof current.agents === "object") ? current.agents : {};
      var installed = Array.isArray(agents.installed) ? agents.installed : [];
      var nextInstalled = installed.map(function (agent) {
        return String(agent.installationId || "") === String(updated.installationId || "") ? updated : agent;
      });
      return {
        ...current,
        agents: { ...agents, installed: nextInstalled },
      };
    });
  }

  function searchRemote(append) {
    setRemoteLoading(true); setSkillSelection(null);
    var cursor = append ? remoteCursor : "";
    var url = "/api/extensions/search?kind=" + encodeURIComponent(installKind) + "&q=" + encodeURIComponent(remoteQuery) + "&advanced=" + (advanced ? "true" : "false") + "&cursor=" + encodeURIComponent(cursor);
    settingsFetch(url).then(readSettingsResponse).then(function (payload) {
      setRemoteResults(function (previous) { return append ? previous.concat(payload.results || []) : (payload.results || []); });
      setRemoteCursor(payload.next_cursor || ""); setRemoteLoading(false);
    }).catch(function (error) { setRemoteLoading(false); tell(error.message, "error"); });
  }

  function startInstall(item, request) {
    var summary = [extensionDisplayName(item, t), item.version || item.recommended_version || "", extensionSourceLabel(item, t)].filter(Boolean).join(" · ");
    var feedback = window.CyreneUI.require("feedback");
    feedback.confirmModal({
      title: t("settings.extensionInstallConfirmTitle"),
      body: t("settings.extensionInstallConfirmBody", { summary: summary }),
      confirmLabel: t("settings.install"),
    }).then(function (ok) {
      if (!ok) return;
      setBusy(item.kind + ":" + item.id);
      settingsFetch("/api/extensions/install", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: item.kind, extension_id: item.id, ...(request || {}) }),
      }).then(readSettingsResponse).then(function () {
        notifyAgentCatalogChanged("installed"); tell(t("settings.extensionInstallStarted"), "success"); return load();
      }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
    });
  }

  function installSearchResult(item) {
    if (item.kind === "skill") {
      setRemoteLoading(true);
      settingsFetch("/api/extensions/skills/inspect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: item.clone_url || item.repository }) })
        .then(readSettingsResponse).then(function (payload) {
          var candidates = payload.candidates || [];
          if (candidates.length === 1) {
            startInstall({ ...item, kind: "skill" }, { url: item.clone_url || item.repository, subdirs: [candidates[0].path] });
          } else {
            setSkillSelection({ item: item, candidates: candidates, selected: {} });
          }
        }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setRemoteLoading(false); });
      return;
    }
    if (item.kind === "mcp") {
      var remote = (item.installable_remotes || [])[0];
      if (remote) {
        startInstall(item, { version: item.version, remote: remote, source: { type: "mcp-registry", id: item.id, version: item.version } });
        return;
      }
      var packageSpec = (item.installable_packages || [])[0];
      if (!packageSpec) { tell(t("settings.extensionMcpLocalPackageHint"), "info"); return; }
      startInstall(item, { version: item.version, package: packageSpec, source: { type: "mcp-registry-package", id: item.id, version: item.version } });
      return;
    }
    startInstall(item, { version: requestedVersion.trim() || item.version || item.recommended_version, ref: item.ref || item.source, spec: item, ...(item.id === "tex" ? { distribution: texChoice } : {}) });
  }

  function configureManualMcp(item) {
    var fallback = ((item.fallback_request || {}).request || {});
    var config = fallback.config || {};
    var source = fallback.source || {};
    var packageSpec = (item.packages || [])[0] || {};
    setManualMcp({
      name: config.name || item.id || "", transport: config.transport || "stdio", url: config.url || "",
      command: config.command || "", args: (config.args || []).join("\n"),
      version: config.version || item.resolved_version || item.version || "", headers: "", env: "",
      packageIdentifier: source.identifier || packageSpec.identifier || "",
    });
    setManualMcpOpen(true);
    tell(t("settings.extensionManualPrefilled"), "info");
  }

  function removeItem(item) {
    window.CyreneUI.require("feedback").confirmModal({ body: t("settings.extensionRemoveConfirm", { name: extensionDisplayName(item, t) }), confirmLabel: item.kind === "mcp" ? t("settings.delete") : t("settings.uninstall"), danger: true }).then(function (ok) {
      if (!ok) return;
      setBusy(item.key);
      settingsFetch("/api/extensions/" + encodeURIComponent(item.kind) + "/" + encodeURIComponent(item.id), { method: "DELETE" })
        .then(readSettingsResponse).then(function () { tell(t("settings.saved"), "success"); return load(); })
        .catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
    });
  }

  function toggleItem(item) {
    setBusy(item.key);
    settingsFetch("/api/extensions/" + encodeURIComponent(item.kind) + "/" + encodeURIComponent(item.id) + "/enabled", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !item.enabled }) })
      .then(readSettingsResponse).then(load).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  function setDefault(item, version) {
    setBusy(item.key);
    settingsFetch("/api/extensions/toolchains/" + encodeURIComponent(item.id) + "/default", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: version }) })
      .then(readSettingsResponse).then(load).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  function installLocalFile() {
    var input = document.createElement("input"); input.type = "file"; input.accept = ".md,.txt,.zip,.json,.yaml,.yml,.prompt";
    input.onchange = function (event) {
      var file = event.target.files && event.target.files[0]; if (!file) return;
      var form = new FormData(); form.append("file", file); setRemoteLoading(true);
      settingsFetch("/api/skills/install-upload", { method: "POST", body: form }).then(readSettingsResponse).then(function () { setInstallOpen(false); return load(); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setRemoteLoading(false); });
    };
    input.click();
  }

  function installLocalFolder() {
    if (window.cyrene && typeof window.cyrene.pickExtensionPath === "function") {
      setRemoteLoading(true);
      window.cyrene.pickExtensionPath({ directory: true, title: t("settings.installFolder") }).then(function (picked) {
        if (!picked || picked.cancelled || !picked.path) return null;
        return settingsFetch("/api/skills/install", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: picked.path }) }).then(readSettingsResponse).then(function () { setInstallOpen(false); return load(); });
      }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setRemoteLoading(false); });
      return;
    }
    setRemoteLoading(true);
    settingsFetch("/api/skills/install-picker", { method: "POST" }).then(readSettingsResponse).then(function (payload) { if (!payload.cancelled) { setInstallOpen(false); return load(); } }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setRemoteLoading(false); });
  }

  function addManualMcp() {
    var config = { name: manualMcp.name.trim(), transport: manualMcp.transport, enabled: true, version: manualMcp.version.trim() };
    if (manualMcp.transport !== "stdio") config.url = manualMcp.url.trim();
    else { config.command = manualMcp.command.trim(); config.args = manualMcp.args.split(/\r?\n/).map(function (value) { return value.trim(); }).filter(Boolean); }
    function parseVariables(value, label) {
      var variables = {};
      String(value || "").split(/\r?\n/).forEach(function (line) {
        if (!line.trim()) return;
        var separator = line.indexOf("=");
        if (separator <= 0) throw new Error(t("settings.extensionVariableInvalid", { label: label }));
        variables[line.slice(0, separator).trim()] = line.slice(separator + 1);
      });
      return variables;
    }
    try {
      if (config.transport !== "stdio") config.headers = parseVariables(manualMcp.headers, t("settings.extensionHeaders"));
      else config.env = parseVariables(manualMcp.env, t("settings.extensionEnvironment"));
    } catch (error) { tell(error.message, "error"); return; }
    if (!config.name || (config.transport !== "stdio" ? !config.url : !config.command || !config.version)) { tell(t("settings.extensionMcpRequired"), "error"); return; }
    startInstall({ id: config.name, name: config.name, kind: "mcp", source: "manual", version: config.version }, { config: config, version: config.version, source: { type: "manual" } });
  }

  function openSources() {
    Promise.all([
      settingsFetch("/api/extensions/sources").then(readSettingsResponse),
      settingsFetch("/api/extensions/audit?limit=50").then(readSettingsResponse),
    ]).then(function (payloads) { setSources(payloads[0]); setAuditRecords(payloads[1].records || []); setSourceHealth(null); setSourceOpen(true); }).catch(function (error) { tell(error.message, "error"); });
  }

  function bindSystem(item) {
    if (window.cyrene && typeof window.cyrene.pickExtensionPath === "function") {
      window.cyrene.pickExtensionPath({ directory: false, title: t("settings.extensionBindTitle") }).then(function (picked) {
        if (!picked || picked.cancelled || !picked.path) return;
        setBindItem(item); setBindPath(picked.path);
      });
      return;
    }
    setBindItem(item); setBindPath(item.manual_binding ? (item.path || "") : "");
  }
  function saveBinding() {
    if (!bindItem || !bindPath.trim()) return;
    setBusy(bindItem.key);
    settingsFetch("/api/extensions/bind", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extension_id: bindItem.id, path: bindPath.trim() }) })
      .then(readSettingsResponse).then(function () { setBindItem(null); tell(t("settings.saved"), "success"); return load(); })
      .catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function unbindSystem(item) {
    setBusy(item.key);
    settingsFetch("/api/extensions/unbind", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extension_id: item.id }) })
      .then(readSettingsResponse).then(load).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  function saveSources() {
    setBusy("sources");
    settingsFetch("/api/extensions/sources", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sources) }).then(readSettingsResponse).then(function (payload) { setSources(payload); tell(t("settings.saved"), "success"); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  function testSources() {
    setSourceTesting(true); setSourceHealth(null);
    settingsFetch("/api/extensions/sources/test", { method: "POST" }).then(readSettingsResponse).then(setSourceHealth).catch(function (error) { tell(error.message, "error"); }).finally(function () { setSourceTesting(false); });
  }

  function loadHooks(open) {
    return Promise.all([
      settingsFetch("/api/hooks").then(readSettingsResponse),
      settingsFetch("/api/hooks/audit/records?limit=100").then(readSettingsResponse),
    ]).then(function (payloads) {
      setHooksData(payloads[0]); setHookAudit(payloads[1].records || []);
      if (open) setHooksOpen(true);
    }).catch(function (error) { tell(error.message, "error"); });
  }
  function editHook(item) {
    var value = item || emptyHookDraft;
    setHookDraft({ ...emptyHookDraft, ...value, runner: { ...emptyHookDraft.runner, ...(value.runner || {}) } });
    setHookArgsText(((value.runner || {}).args || []).join("\n"));
    setHookEditorOpen(true);
  }
  function saveHook() {
    var payload = { ...hookDraft, runner: { ...hookDraft.runner, args: hookArgsText.split("\n").map(function (value) { return value.trim(); }).filter(Boolean) } };
    delete payload.runner.env;
    var url = hookDraft.id ? "/api/hooks/" + encodeURIComponent(hookDraft.id) : "/api/hooks";
    setBusy("hook-save");
    settingsFetch(url, { method: hookDraft.id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
      .then(readSettingsResponse).then(function () { setHookDraft(emptyHookDraft); setHookArgsText(""); setHookEditorOpen(false); tell(t("settings.hookSaved"), "success"); return loadHooks(false); })
      .catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function toggleHook(item) {
    setBusy("hook:" + item.id);
    settingsFetch("/api/hooks/" + encodeURIComponent(item.id) + "/enabled", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: item.enabled !== true }) })
      .then(readSettingsResponse).then(function () { return loadHooks(false); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function deleteHook(item) {
    setBusy("hook:" + item.id);
    settingsFetch("/api/hooks/" + encodeURIComponent(item.id), { method: "DELETE" }).then(readSettingsResponse).then(function () { return loadHooks(false); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function testHook(item) {
    setBusy("hook:" + item.id);
    settingsFetch("/api/hooks/" + encodeURIComponent(item.id) + "/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(readSettingsResponse).then(function () { tell(t("settings.hookTestSucceeded"), "success"); return loadHooks(false); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function decideHookProposal(item, approve) {
    setBusy("proposal:" + item.id);
    settingsFetch("/api/hooks/proposals/" + encodeURIComponent(item.id) + "/decision", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve: approve }) }).then(readSettingsResponse).then(function () { tell(t(approve ? "settings.hookProposalApproved" : "settings.hookProposalRejected"), "success"); return loadHooks(false); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function configureExtensionHook(item) {
    setBusy(item.key);
    settingsFetch("/api/hooks/extensions/cli/" + encodeURIComponent(item.id) + "/configure", { method: "POST" }).then(readSettingsResponse).then(function () { tell(t("settings.extensionHookStarted"), "success"); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  var categories = ["recommended", "skills", "mcp", "cli", "toolchains", "agent"];
  var items = data[category] || [];
  var q = query.trim().toLowerCase();
  var filtered = items.filter(function (item) { return !q || [extensionDisplayName(item, t), item.id, extensionDisplayDescription(item, t), item.version].join(" ").toLowerCase().indexOf(q) >= 0; });
  var activeTasks = (data.tasks || []).filter(function (task) { return extensionTaskIsVisible(task, taskClock); }).slice(0, 4);
  var installCategory = category === "recommended" ? "toolchain" : category === "skills" ? "skill" : category === "toolchains" ? "toolchain" : category;
  var agentListing = (data.agents && typeof data.agents === "object") ? data.agents : { recommended: [], installed: [] };
  var agentRecommended = Array.isArray(agentListing.recommended) ? agentListing.recommended : [];
  var agentInstalled = Array.isArray(agentListing.installed) ? agentListing.installed : [];

  return React.createElement("div", { className: "wb-extensions-page", id: "setting-extensions" },
    React.createElement("header", { className: "wb-extensions-header" },
      SectionTitle(t("settings.extensionCenter"), t("settings.extensionsSubtitle")),
      React.createElement("div", { className: "wb-extension-header-actions" },
        React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { loadHooks(true); } }, t("settings.agentHooks")),
        React.createElement("button", { type: "button", className: "wb-btn", onClick: openSources }, t("settings.extensionSources")),
        category !== "recommended" && category !== "agent" && React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button wb-extension-tab-install-button", onClick: function () { openInstaller(installCategory); } }, t("settings.extensionInstallAction." + installCategory)),
      )
    ),
    data.python_prompt_required && React.createElement("button", { type: "button", className: "wb-extension-python-callout", onClick: function () { setCategory("recommended"); setQuery("Python"); } },
      React.createElement("strong", null, t("settings.extensionPythonMissingTitle")),
      React.createElement("span", null, t("settings.extensionPythonMissingBody")),
      React.createElement("span", { className: "wb-extension-callout-action" }, t("settings.extensionViewInstall"))
    ),
    React.createElement("div", { className: "wb-extension-tabs", role: "tablist", "aria-label": t("settings.extensionCenter") },
      categories.map(function (id) { return React.createElement("button", { key: id, type: "button", role: "tab", "aria-selected": category === id ? "true" : "false", className: category === id ? "active" : "", onClick: function () { setCategory(id); setQuery(""); } }, t("settings.extensionTab." + id)); })
    ),
    category !== "agent" && React.createElement("div", { className: "wb-extension-filter" },
      React.createElement("input", { className: "wb-input", value: query, onChange: function (event) { setQuery(event.target.value); }, placeholder: t("settings.extensionFilter") , "aria-label": t("settings.extensionFilter") }),
      React.createElement("span", null, t("settings.extensionCount", { n: filtered.length }))
    ),
    notice && React.createElement("div", { className: "wb-extension-notice " + noticeKind, role: noticeKind === "error" ? "alert" : "status" }, notice),
    activeTasks.length > 0 && React.createElement("section", { className: "wb-extension-tasks", "aria-label": t("settings.extensionTasks") },
      React.createElement("h3", null, t("settings.extensionTasks")),
      activeTasks.map(function (task) {
        var progress = Math.max(0, Math.min(100, Number(task.progress) || 0));
        var errorContent = task.status === "failed" ? extensionTaskErrorContent(task, t) : null;
        return React.createElement("article", { key: task.id, className: "wb-extension-task " + task.status },
          React.createElement("div", { className: "wb-extension-task-head" },
            React.createElement("strong", null, extensionDisplayName({ id: task.extension_id, name: task.extension_id }, t)),
            React.createElement("span", { className: "wb-extension-task-status" }, extensionTaskStatusLabel(task.status, t))
          ),
          React.createElement("div", { className: "wb-extension-task-progress-row" },
            React.createElement("div", { className: "wb-extension-task-progress", role: "progressbar", "aria-label": t("settings.extensionTaskProgress", { name: task.extension_id }), "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": progress }, React.createElement("span", { style: { width: progress + "%" } })),
            React.createElement("span", { className: "wb-extension-task-percent", "aria-hidden": "true" }, progress + "%")
          ),
          errorContent && React.createElement("div", { className: "wb-extension-task-error", role: "alert" },
            React.createElement("strong", null, errorContent.title),
            React.createElement("p", null, errorContent.hint),
            task.error && React.createElement("details", null,
              React.createElement("summary", null, t("settings.extensionTaskTechnicalDetails")),
              React.createElement("pre", null, task.error)
            )
          ),
          ["queued", "running"].indexOf(task.status) >= 0 && React.createElement("div", { className: "wb-extension-task-actions" }, React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { settingsFetch("/api/extensions/tasks/" + task.id + "/cancel", { method: "POST" }).then(load); } }, t("settings.cancel")))
        );
      })
    ),
    category === "agent"
      ? React.createElement(AgentTabPanel, {
          t: t,
          recommended: agentRecommended,
          installed: agentInstalled,
          busy: busy,
          expandedId: agentExpandedId,
          onToggleExpand: function (installationId) { setAgentExpandedId(installationId); },
          onInstall: startAgentInstall,
          onToggle: toggleAgent,
          onRemove: removeAgent,
          onChanged: replaceAgentCard,
          onOpenInstalled: function (installationId) {
            setAgentExpandedId(installationId);
            var panel = document.querySelector(".wb-extensions-page");
            if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
          },
          onOpenProposal: function () { setAgentProposalOpen(true); },
        })
      : React.createElement("div", { className: "wb-extension-list", "aria-busy": loading ? "true" : "false" },
          loading && React.createElement("div", { className: "wb-extensions-empty" }, t("settings.loading")),
          !loading && filtered.length === 0 && React.createElement("div", { className: "wb-extensions-empty" }, t("settings.extensionEmpty")),
          filtered.map(function (item) { return React.createElement(ExtensionCard, { key: item.key, item: item, t: t, busy: busy === item.key, onInstall: function (target) { if (category === "recommended" && target.id !== "tex") startInstall(target, {}); else { openInstaller(target.kind); if (target.id === "tex") { setRemoteQuery("TeX"); setRemoteResults([target]); } } }, onRemove: removeItem, onToggle: toggleItem, onDefault: setDefault, onBind: bindSystem, onUnbind: unbindSystem, onConfigureHook: configureExtensionHook }); })
        ),
    agentProposalOpen && React.createElement(AgentInstallProposalModal, { t: t, onUpdated: function () { notifyAgentCatalogChanged("installed"); return load(); }, onClose: function () { setAgentProposalOpen(false); } }),
    hooksOpen && React.createElement("div", { className: "wb-extension-modal-scrim", onMouseDown: function (event) { if (event.target === event.currentTarget) setHooksOpen(false); } },
      React.createElement("section", { className: "wb-extension-modal wb-hooks-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "agent-hooks-title" },
        React.createElement("header", null, React.createElement("div", null, React.createElement("h3", { id: "agent-hooks-title" }, t("settings.agentHooks")), React.createElement("p", null, t("settings.agentHooksSubtitle"))), React.createElement("button", { type: "button", className: "wb-extension-close", onClick: function () { setHooksOpen(false); }, "aria-label": t("settings.close") }, "×")),
        (hooksData.proposals || []).filter(function (item) { return item.status === "pending"; }).length > 0 && React.createElement("section", { className: "wb-hook-proposals" },
          React.createElement("h4", null, t("settings.hookPendingApprovals")),
          (hooksData.proposals || []).filter(function (item) { return item.status === "pending"; }).map(function (item) { var proposalHook = item.hook || {}; var proposalRunner = proposalHook.runner || {}; var proposalTarget = proposalRunner.executable || proposalRunner.path || ""; var proposalCommand = [proposalTarget].concat(proposalRunner.args || []).join(" "); return React.createElement("article", { key: item.id, className: "wb-hook-proposal" }, React.createElement("div", null, React.createElement("strong", null, (item.extension || {}).name || (item.extension || {}).id || proposalHook.name), React.createElement("small", null, item.rationale), React.createElement("code", null, proposalHook.event + " · " + (proposalHook.matcher || "*")), React.createElement("code", { title: proposalCommand }, proposalCommand)), React.createElement("div", null, React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "proposal:" + item.id, onClick: function () { decideHookProposal(item, false); } }, t("settings.reject")), React.createElement("button", { type: "button", className: "wb-btn primary", disabled: busy === "proposal:" + item.id, onClick: function () { decideHookProposal(item, true); } }, t("settings.approve")))); })
        ),
        React.createElement("section", { className: "wb-hook-list" },
          React.createElement("div", { className: "wb-hook-section-head" }, React.createElement("h4", null, t("settings.configuredHooks")), React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { editHook(null); } }, t("settings.addHook"))),
          (hooksData.hooks || []).length === 0 ? React.createElement("div", { className: "wb-hook-empty" }, React.createElement("p", null, t("settings.hookEmpty"))) : (hooksData.hooks || []).map(function (item) { return React.createElement("article", { key: item.id, className: "wb-hook-row" }, React.createElement("div", null, React.createElement("strong", null, item.name), React.createElement("small", null, item.event + (item.matcher && item.matcher !== "*" ? " · " + item.matcher : "")), React.createElement("code", null, (item.runner || {}).executable || (item.runner || {}).path)), React.createElement("div", null, Toggle(item.enabled === true, function () { toggleHook(item); }, busy === "hook:" + item.id, item.name), React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { testHook(item); } }, t("settings.test")), React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { editHook(item); } }, t("settings.edit")), React.createElement("button", { type: "button", className: "wb-btn danger", onClick: function () { deleteHook(item); } }, t("settings.delete")))); })
        ),
        hookEditorOpen && React.createElement("section", { className: "wb-hook-editor" },
          React.createElement("div", { className: "wb-hook-editor-head" },
            React.createElement("div", null, React.createElement("h4", null, hookDraft.id ? t("settings.editHook") : t("settings.addHook")), React.createElement("small", null, t("settings.hookEditorHint"))),
            React.createElement("button", { type: "button", className: "wb-extension-close wb-hook-editor-close", onClick: function () { setHookEditorOpen(false); }, "aria-label": t("settings.close") }, "×")
          ),
          React.createElement("div", { className: "wb-extension-form-grid wb-hook-core-fields" },
            React.createElement("label", null, React.createElement("span", null, t("settings.name")), React.createElement("input", { className: "wb-input", value: hookDraft.name, onChange: function (event) { setHookDraft({ ...hookDraft, name: event.target.value }); } })),
            React.createElement("label", null, React.createElement("span", null, t("settings.hookEvent")), React.createElement("select", { className: "wb-select", value: hookDraft.event, onChange: function (event) { setHookDraft({ ...hookDraft, event: event.target.value, failure_policy: event.target.value === "PreToolUse" ? hookDraft.failure_policy : "open" }); } }, ["PreToolUse", "PostToolUse", "SessionStart", "SessionEnd", "Stop"].map(function (value) { return React.createElement("option", { key: value, value: value }, value); }))),
            React.createElement("label", null, React.createElement("span", null, t("settings.hookRunnerType")), React.createElement("select", { className: "wb-select", value: hookDraft.runner.type, onChange: function (event) { var type = event.target.value; setHookDraft({ ...hookDraft, runner: type === "script" ? { type: "script", path: "", args: hookDraft.runner.args || [], env: hookDraft.runner.env || {} } : { type: "command", executable: "", args: hookDraft.runner.args || [], env: hookDraft.runner.env || {} } }); } }, React.createElement("option", { value: "command" }, t("settings.hookRunnerCommand")), React.createElement("option", { value: "script" }, t("settings.hookRunnerScript")))),
            ["PreToolUse", "PostToolUse"].indexOf(hookDraft.event) >= 0 && React.createElement("label", null, React.createElement("span", null, t("settings.hookMatcher")), React.createElement("input", { className: "wb-input mono", value: hookDraft.matcher, onChange: function (event) { setHookDraft({ ...hookDraft, matcher: event.target.value }); } })),
            React.createElement("label", { className: "wide" }, React.createElement("span", null, hookDraft.runner.type === "script" ? t("settings.hookScriptPath") : t("settings.hookExecutable")), React.createElement("input", { className: "wb-input mono", value: hookDraft.runner.executable || hookDraft.runner.path || "", onChange: function (event) { var runner = { ...hookDraft.runner }; if (runner.type === "script") runner.path = event.target.value; else runner.executable = event.target.value; setHookDraft({ ...hookDraft, runner: runner }); } })),
          ),
          React.createElement("details", { className: "wb-hook-advanced" },
            React.createElement("summary", null, React.createElement("span", null, t("settings.hookAdvanced")), React.createElement("small", null, t("settings.hookAdvancedHint"))),
            React.createElement("div", { className: "wb-extension-form-grid" },
              React.createElement("label", null, React.createElement("span", null, t("settings.hookPriority")), React.createElement("input", { className: "wb-input mono", type: "number", value: hookDraft.priority, onChange: function (event) { setHookDraft({ ...hookDraft, priority: Number(event.target.value) }); } })),
              React.createElement("label", null, React.createElement("span", null, t("settings.hookTimeout")), React.createElement("input", { className: "wb-input mono", type: "number", min: "0.1", max: "60", step: "0.1", value: hookDraft.timeout_seconds, onChange: function (event) { setHookDraft({ ...hookDraft, timeout_seconds: Number(event.target.value) }); } })),
              hookDraft.event === "PreToolUse" && React.createElement("label", null, React.createElement("span", null, t("settings.hookFailurePolicy")), React.createElement("select", { className: "wb-select", value: hookDraft.failure_policy, onChange: function (event) { setHookDraft({ ...hookDraft, failure_policy: event.target.value }); } }, React.createElement("option", { value: "open" }, t("settings.hookFailureOpen")), React.createElement("option", { value: "block" }, t("settings.hookFailureBlock")))),
              React.createElement("label", { className: hookDraft.event === "PreToolUse" ? "" : "wide" }, React.createElement("span", null, t("settings.description")), React.createElement("input", { className: "wb-input", value: hookDraft.description, onChange: function (event) { setHookDraft({ ...hookDraft, description: event.target.value }); } })),
              React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.hookArguments")), React.createElement("textarea", { className: "wb-input mono", rows: 2, value: hookArgsText, onChange: function (event) { setHookArgsText(event.target.value); } }), React.createElement("small", null, t("settings.hookArgumentsHint")))
            )
          ),
          React.createElement("div", { className: "wb-hook-editor-actions" }, React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { editHook(null); } }, t("settings.reset")), React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { setHookEditorOpen(false); } }, t("settings.cancel")), React.createElement("button", { type: "button", className: "wb-btn primary", disabled: busy === "hook-save" || !hookDraft.name || !(hookDraft.runner.executable || hookDraft.runner.path), onClick: saveHook }, t("settings.save")))
        ),
        React.createElement("details", { className: "wb-extension-audit" },
          React.createElement("summary", null, t("settings.hookExecutionLog")),
          hookAudit.length === 0
            ? React.createElement("p", null, t("settings.hookAuditEmpty"))
            : hookAudit.map(function (record, index) {
                return React.createElement("div", { key: record.timestamp + index },
                  React.createElement("strong", null, (record.hook_name || record.hook_id || record.action) + " · " + (record.event || record.action || "")),
                  React.createElement("small", null, new Date(record.timestamp).toLocaleString() + " · " + (record.status || record.result || ""))
                );
              })
        )
      )
    ),
    installOpen && React.createElement("div", { className: "wb-extension-modal-scrim", onMouseDown: function (event) { if (event.target === event.currentTarget) setInstallOpen(false); } },
      React.createElement("section", { className: "wb-extension-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "extension-install-title" },
        React.createElement("header", null,
          React.createElement("div", null, React.createElement("h3", { id: "extension-install-title" }, t("settings.extensionInstallTitle." + installKind)), React.createElement("p", null, t("settings.extensionInstallSubtitle." + installKind))),
          React.createElement("button", { type: "button", className: "wb-extension-close", onClick: function () { setInstallOpen(false); }, "aria-label": t("settings.close") }, "×")
        ),
        installKind === "skill" && React.createElement("div", { className: "wb-extension-local-actions" },
          React.createElement("button", { type: "button", className: "wb-btn wb-extension-install-button", onClick: installLocalFile }, t("settings.installFile")),
          React.createElement("button", { type: "button", className: "wb-btn wb-extension-install-button", onClick: installLocalFolder }, t("settings.installFolder"))
        ),
        installKind === "mcp" && React.createElement("details", { className: "wb-extension-manual", open: manualMcpOpen, onToggle: function (event) { setManualMcpOpen(event.currentTarget.open); } },
          React.createElement("summary", null, t("settings.extensionManualMcp")),
          React.createElement("div", { className: "wb-extension-form-grid" },
            React.createElement("label", null, React.createElement("span", null, t("settings.name")), React.createElement("input", { className: "wb-input", value: manualMcp.name, onChange: function (e) { setManualMcp({ ...manualMcp, name: e.target.value }); } })),
            React.createElement("label", null, React.createElement("span", null, t("settings.extensionTransport")), React.createElement("select", { className: "wb-select", value: manualMcp.transport, onChange: function (e) { setManualMcp({ ...manualMcp, transport: e.target.value }); } }, React.createElement("option", { value: "streamable_http" }, "Streamable HTTP"), React.createElement("option", { value: "sse" }, "SSE"), React.createElement("option", { value: "stdio" }, "stdio"))),
            manualMcp.transport !== "stdio"
              ? React.createElement(React.Fragment, null,
                  React.createElement("label", { className: "wide" }, React.createElement("span", null, "URL"), React.createElement("input", { className: "wb-input mono", value: manualMcp.url, onChange: function (e) { setManualMcp({ ...manualMcp, url: e.target.value }); } })),
                  React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.extensionHeaders")), React.createElement("textarea", { className: "wb-input mono", rows: 3, value: manualMcp.headers, placeholder: "Authorization=Bearer …", onChange: function (e) { setManualMcp({ ...manualMcp, headers: e.target.value }); } }), React.createElement("small", null, t("settings.extensionSecretStored")))
                )
              : React.createElement(React.Fragment, null,
                  React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.placeholderCommand")), React.createElement("input", { className: "wb-input mono", value: manualMcp.command, onChange: function (e) { setManualMcp({ ...manualMcp, command: e.target.value }); } })),
                  React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.placeholderArgs")), React.createElement("textarea", { className: "wb-input mono", rows: 3, value: manualMcp.args, placeholder: t("settings.extensionArgumentsHint"), onChange: function (e) { setManualMcp({ ...manualMcp, args: e.target.value }); } }), React.createElement("small", null, t("settings.extensionArgumentsHint"))),
                  React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.extensionEnvironment")), React.createElement("textarea", { className: "wb-input mono", rows: 3, value: manualMcp.env, placeholder: "API_KEY=…", onChange: function (e) { setManualMcp({ ...manualMcp, env: e.target.value }); } }), React.createElement("small", null, t("settings.extensionSecretStored")))
                ),
            React.createElement("label", null, React.createElement("span", null, t("settings.extensionVersion")), React.createElement("input", { className: "wb-input mono", value: manualMcp.version, onChange: function (e) { setManualMcp({ ...manualMcp, version: e.target.value }); } })),
            React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button", onClick: addManualMcp }, t("settings.add"))
          )
        ),
        React.createElement("div", { className: "wb-extension-remote-search" },
          React.createElement("input", { className: "wb-input", value: remoteQuery, onChange: function (e) { setRemoteQuery(e.target.value); setRemoteCursor(""); }, onKeyDown: function (e) { if (e.key === "Enter") searchRemote(false); }, placeholder: t("settings.extensionSearchPlaceholder." + installKind), "aria-label": t("settings.extensionRemoteSearch") }),
          React.createElement("button", { type: "button", className: "wb-btn primary", disabled: remoteLoading, onClick: function () { searchRemote(false); } }, remoteLoading ? t("settings.loading") : t("settings.search"))
        ),
        (installKind === "cli" || installKind === "toolchain") && React.createElement("label", { className: "wb-extension-requested-version" }, React.createElement("span", null, t("settings.extensionRequestedVersion")), React.createElement("input", { className: "wb-input mono", value: requestedVersion, onChange: function (event) { setRequestedVersion(event.target.value); }, placeholder: "latest / lts / 22.14.0" })),
        installKind === "cli" && React.createElement("label", { className: "wb-extension-advanced-toggle" }, React.createElement("input", { type: "checkbox", checked: advanced, onChange: function (e) { setAdvanced(e.target.checked); } }), React.createElement("span", null, t("settings.extensionAdvancedSources"))),
        installKind === "toolchain" && remoteResults.some(function (item) { return item.id === "tex"; }) && React.createElement("fieldset", { className: "wb-extension-tex-choice" },
          React.createElement("legend", null, t("settings.extensionTeXChoice")),
          [["tinytex", "settings.extensionTinyTeX", "settings.extensionTinyTeXHint"], ["texlive-full", "settings.extensionFullTeX", "settings.extensionFullTeXHint"]].map(function (entry) { return React.createElement("label", { key: entry[0] }, React.createElement("input", { type: "radio", name: "tex-distribution", checked: texChoice === entry[0], onChange: function () { setTexChoice(entry[0]); } }), React.createElement("span", null, React.createElement("strong", null, t(entry[1])), React.createElement("small", null, t(entry[2])))); })
        ),
        skillSelection && React.createElement("div", { className: "wb-extension-skill-selection" },
          React.createElement("strong", null, t("settings.extensionSelectSkills")),
          skillSelection.candidates.map(function (candidate) { return React.createElement("label", { key: candidate.path }, React.createElement("input", { type: "checkbox", checked: !!skillSelection.selected[candidate.path], onChange: function (e) { setSkillSelection({ ...skillSelection, selected: { ...skillSelection.selected, [candidate.path]: e.target.checked } }); } }), React.createElement("span", null, React.createElement("b", null, candidate.name), React.createElement("small", null, candidate.description), React.createElement("code", null, candidate.path || "."))); }),
          React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button", onClick: function () { var selected = Object.keys(skillSelection.selected).filter(function (key) { return skillSelection.selected[key]; }); if (selected.length) startInstall({ ...skillSelection.item, kind: "skill" }, { url: skillSelection.item.clone_url || skillSelection.item.repository, subdirs: selected }); } }, t("settings.extensionInstallSelected"))
        ),
        React.createElement("div", { className: "wb-extension-search-results" },
          remoteResults.map(function (item) { var displayName = extensionDisplayName(item, t); return React.createElement("div", { key: item.id + String(item.source), className: "wb-extension-result" },
            React.createElement("span", { className: "wb-extension-glyph " + item.kind + " extension-" + String(item.id || "").replace(/[^a-z0-9_-]/gi, "-").toLowerCase() }, React.createElement(ExtensionGlyph, { id: item.id, kind: item.kind, label: displayName })),
            React.createElement("div", null, React.createElement("strong", null, displayName), React.createElement("p", null, extensionDisplayDescription(item, t)), React.createElement("small", null, [item.version, item.backend, item.publisher, item.verified ? t("settings.extensionVerified") : t("settings.extensionUnverified")].filter(Boolean).join(" · "))),
            React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button", disabled: remoteLoading, title: item.installable === false ? t("settings.extensionNeedsConfiguration") : "", onClick: function () { if (item.installable === false) configureManualMcp(item); else installSearchResult(item); } }, item.installable === false ? t("settings.extensionConfigureManually") : t("settings.install"))
          ); })
        ),
        remoteCursor && React.createElement("button", { type: "button", className: "wb-btn wb-extension-load-more", disabled: remoteLoading, onClick: function () { searchRemote(true); } }, remoteLoading ? t("settings.loading") : t("settings.extensionLoadMore"))
      )
    ),
    sourceOpen && React.createElement("div", { className: "wb-extension-modal-scrim", onMouseDown: function (event) { if (event.target === event.currentTarget) setSourceOpen(false); } },
      React.createElement("section", { className: "wb-extension-modal wb-extension-source-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "extension-source-title" },
        React.createElement("header", null, React.createElement("div", null, React.createElement("h3", { id: "extension-source-title" }, t("settings.extensionSources")), React.createElement("p", null, t("settings.extensionSourcesSubtitle"))), React.createElement("button", { type: "button", className: "wb-extension-close", onClick: function () { setSourceOpen(false); }, "aria-label": t("settings.close") }, "×")),
        React.createElement("div", { className: "wb-extension-source-sections" },
          React.createElement("section", { className: "wb-extension-source-section" },
            React.createElement("div", { className: "wb-extension-source-section-head" }, React.createElement("div", null, React.createElement("h4", null, t("settings.extensionSourceSectionNetwork")), React.createElement("p", null, t("settings.extensionSourceSectionNetworkHint")))),
            React.createElement("div", { className: "wb-extension-source-form" },
              React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.extensionNetworkMode")), React.createElement("select", { className: "wb-select", value: sources.network_mode || "auto", onChange: function (event) { setSources({ ...sources, network_mode: event.target.value }); } }, React.createElement("option", { value: "auto" }, t("settings.extensionNetworkAuto")), React.createElement("option", { value: "direct" }, t("settings.extensionNetworkDirect")), React.createElement("option", { value: "china" }, t("settings.extensionNetworkChina")))),
              [["github_mirror", "settings.extensionGithubMirror", "settings.extensionGithubMirrorPlaceholder"], ["npm_registry", "settings.extensionNpmRegistry", "settings.extensionNpmRegistryPlaceholder"], ["pip_index_url", "settings.extensionPipIndex", "settings.extensionPipIndexPlaceholder"]].map(function (entry) { return React.createElement("label", { key: entry[0] }, React.createElement("span", null, t(entry[1])), React.createElement("input", { className: "wb-input mono", type: "url", value: sources[entry[0]] || "", placeholder: t(entry[2]), onChange: function (event) { setSources({ ...sources, [entry[0]]: event.target.value }); } })); }),
              React.createElement("div", { className: "wb-extension-source-toggle wide" }, React.createElement("div", null, React.createElement("strong", null, t("settings.extensionAutoMirror")), React.createElement("small", null, t("settings.extensionAutoMirrorHint"))), Toggle(sources.auto_mirror !== false, function () { setSources({ ...sources, auto_mirror: sources.auto_mirror === false }); }, false, t("settings.extensionAutoMirror")))
            )
          ),
          React.createElement("section", { className: "wb-extension-source-section" },
            React.createElement("div", { className: "wb-extension-source-section-head" }, React.createElement("div", null, React.createElement("h4", null, t("settings.extensionSourceSectionCatalogs")), React.createElement("p", null, t("settings.extensionSourceSectionCatalogsHint")))),
            React.createElement("div", { className: "wb-extension-source-form" },
              React.createElement("label", null, React.createElement("span", null, t("settings.extensionMcpRegistry")), React.createElement("input", { className: "wb-input mono", type: "url", value: sources.mcp_registry_url || "", placeholder: "https://registry.modelcontextprotocol.io", onChange: function (event) { setSources({ ...sources, mcp_registry_url: event.target.value }); } }), React.createElement("small", null, t("settings.extensionMcpRegistryHint"))),
              React.createElement("label", null, React.createElement("span", null, t("settings.extensionSkillCatalog")), React.createElement("input", { className: "wb-input mono", type: "url", value: sources.skill_catalog_url || "", placeholder: t("settings.extensionSkillCatalogPlaceholder"), onChange: function (event) { setSources({ ...sources, skill_catalog_url: event.target.value }); } }), React.createElement("small", null, t("settings.extensionSkillCatalogHint")))
            )
          ),
          React.createElement("section", { className: "wb-extension-source-section" },
            React.createElement("div", { className: "wb-extension-source-section-head" }, React.createElement("div", null, React.createElement("h4", null, t("settings.extensionSourceSectionSecurity")), React.createElement("p", null, t("settings.extensionSourceSectionSecurityHint")))),
            React.createElement("div", { className: "wb-extension-source-form" },
              React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.extensionGithubToken")), React.createElement("div", { className: "wb-extension-token-row" }, React.createElement("input", { className: "wb-input mono", type: "password", value: sources.github_token || "", placeholder: sources.github_token_configured ? t("settings.secretConfigured") : "ghp_…", onChange: function (event) { setSources({ ...sources, github_token: event.target.value }); } }), sources.github_token_configured && React.createElement("button", { type: "button", className: "wb-btn danger", onClick: function () { setSources({ ...sources, github_token: "", clear_github_token: true, github_token_configured: false }); } }, t("settings.clearStoredKey"))), React.createElement("small", null, t("settings.extensionGithubTokenHint"))),
              React.createElement("div", { className: "wb-extension-source-toggle wide" }, React.createElement("div", null, React.createElement("strong", null, t("settings.extensionVerifySignatures")), React.createElement("small", null, t("settings.extensionVerifySignaturesHint"))), Toggle(sources.verify_signatures !== false, function () { setSources({ ...sources, verify_signatures: sources.verify_signatures === false }); }, false, t("settings.extensionVerifySignatures")))
            )
          )
        ),
        sourceHealth && React.createElement("div", { className: "wb-extension-source-health" }, Object.keys(sourceHealth.checks || {}).map(function (key) { var item = sourceHealth.checks[key]; return React.createElement("span", { key: key, className: item.ok ? "ok" : "error" }, t("settings.extensionSourceCheck." + key, key) + " · " + (item.ok ? t("settings.extensionReachable") : t("settings.extensionUnreachable"))); })),
        React.createElement("details", { className: "wb-extension-audit" }, React.createElement("summary", null, t("settings.extensionAudit")),
          auditRecords.length === 0 ? React.createElement("p", null, t("settings.extensionAuditEmpty")) : auditRecords.map(function (record, index) { return React.createElement("div", { key: record.at + index }, React.createElement("strong", null, extensionAuditLabel("Action", record.action, t) + " · " + record.target), React.createElement("small", null, new Date(record.at).toLocaleString() + " · " + extensionAuditLabel("Actor", record.actor, t) + " · " + extensionAuditLabel("Result", record.result, t))); })
        ),
        React.createElement("footer", null, React.createElement("button", { type: "button", className: "wb-btn", disabled: sourceTesting, onClick: testSources }, sourceTesting ? t("settings.testingConnection") : t("settings.testConnection")), React.createElement("button", { type: "button", className: "wb-btn primary", disabled: busy === "sources", onClick: saveSources }, t("settings.save")))
      )
    ),
    bindItem && React.createElement("div", { className: "wb-extension-modal-scrim", onMouseDown: function (event) { if (event.target === event.currentTarget) setBindItem(null); } },
      React.createElement("section", { className: "wb-extension-modal wb-extension-bind-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "extension-bind-title" },
        React.createElement("header", null, React.createElement("div", null, React.createElement("h3", { id: "extension-bind-title" }, t("settings.extensionBindTitle")), React.createElement("p", null, t("settings.extensionBindHint"))), React.createElement("button", { type: "button", className: "wb-extension-close", onClick: function () { setBindItem(null); }, "aria-label": t("settings.close") }, "×")),
        React.createElement("input", { className: "wb-input mono", autoFocus: true, value: bindPath, onChange: function (event) { setBindPath(event.target.value); }, placeholder: "/usr/local/bin/" + bindItem.id }),
        React.createElement("footer", null, React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { setBindItem(null); } }, t("settings.cancel")), React.createElement("button", { type: "button", className: "wb-btn primary", disabled: !bindPath.trim() || busy === bindItem.key, onClick: saveBinding }, t("settings.save")))
      )
    )
  );
}

// ── Shortcuts Panel ──
function ShortcutsPanel(p) {
  var t = p.t;
  var sc = window.CyreneUI.require("shortcuts");
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
          showSettingsToast(t("settings.quickChatShortcutConflict"), "error");
        } else {
          showSettingsToast(t("settings.shortcutSaved"), "success");
        }
      })
      .catch(function () {
        setQuickChatError("shortcut_update_failed");
        showSettingsToast(t("settings.quickChatShortcutFailed"), "error");
      })
      .finally(function () { setQuickChatBusy(false); });
  }

  function startCapture(id) {
    setCapturingId(id);
    setConflict(null);
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
    showSettingsToast(t("settings.shortcutSaved"), "success");
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
    showSettingsToast(t("settings.shortcutReset"), "success");
  }
  function resetAll() {
    if (!sc) return;
    sc.resetAll();
    setConflict(null);
    showSettingsToast(t("settings.shortcutResetAll"), "success");
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
      return React.createElement(React.Fragment, { key: groupKey },
        SectionBlock(t(groupLabelKey[groupKey] || groupKey), null,
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
        )
      );
    }),
    React.createElement("div", { className: "wb-save-actions" },
      React.createElement("button", { type: "button", className: "wb-btn", onClick: resetAll }, t("settings.resetShortcuts")),
    ),
  );
}

function UsageTrendChart(p) {
  var { t } = p;
  var items = Array.isArray(p.items) ? p.items : [];
  var currencySymbol = String(p.currencySymbol || "");
  var chartRef = useRefSt(null);
  var signature = JSON.stringify(items.map(function (item) {
    return [item.day, item.total_tokens, item.requests, item.cost];
  })) + currencySymbol;

  function compactAxisValue(value) {
    var number = Number(value) || 0;
    if (Math.abs(number) >= 1e6) return (number / 1e6).toFixed(number >= 1e7 ? 0 : 1) + "M";
    if (Math.abs(number) >= 1e3) return (number / 1e3).toFixed(number >= 1e4 ? 0 : 1) + "K";
    return String(Math.round(number));
  }

  function compactCostAxisValue(value) {
    var number = Number(value) || 0;
    if (Math.abs(number) >= 1e3) return currencySymbol + compactAxisValue(number);
    if (Math.abs(number) >= 10) return currencySymbol + number.toFixed(0);
    if (Math.abs(number) >= 1) return currencySymbol + number.toFixed(1);
    return currencySymbol + number.toFixed(2);
  }

  useEffectSt(function () {
    var node = chartRef.current;
    if (!node || items.length < 2 || !window.echarts || typeof window.echarts.init !== "function") return undefined;
    var byDay = {};
    items.forEach(function (item) { byDay[String(item.day || "")] = item; });
    var now = new Date();
    var year = now.getFullYear();
    var month = now.getMonth() + 1;
    var prefix = String(year) + "-" + String(month).padStart(2, "0") + "-";
    var days = [];
    var tokenValues = [];
    var requestValues = [];
    var costValues = [];
    for (var day = 1; day <= now.getDate(); day += 1) {
      var key = prefix + String(day).padStart(2, "0");
      var row = byDay[key] || {};
      days.push(String(month).padStart(2, "0") + "/" + String(day).padStart(2, "0"));
      tokenValues.push(Number(row.total_tokens || 0));
      requestValues.push(Number(row.requests || 0));
      costValues.push(Number(row.cost || 0));
    }

    var chart = window.echarts.init(node);
    function renderChart() {
      var style = getComputedStyle(document.documentElement);
      var textColor = style.getPropertyValue("--wb-text-secondary").trim() || "#526070";
      var mutedColor = style.getPropertyValue("--wb-muted").trim() || "#7a8796";
      var gridColor = style.getPropertyValue("--wb-line").trim() || "#dbe1e8";
      var tokenColor = style.getPropertyValue("--wb-accent").trim() || "#4f7cff";
      var requestColor = style.getPropertyValue("--wb-warning-text").trim() || "#9a6700";
      var costColor = style.getPropertyValue("--wb-purple").trim() || "#b34ca0";
      function combinedYAxis(position, offset, formatter, color, showSplitLine) {
        return {
          type: "value",
          position: position,
          offset: offset || 0,
          min: 0,
          axisLine: { show: true, lineStyle: { color: color } },
          axisTick: { show: false },
          axisLabel: { color: color, fontSize: 10, formatter: formatter },
          splitLine: showSplitLine
            ? { show: true, lineStyle: { color: gridColor, type: "dashed" } }
            : { show: false },
        };
      }
      chart.setOption({
        animation: false,
        backgroundColor: "transparent",
        color: [tokenColor, requestColor, costColor],
        grid: { left: 58, right: 112, top: 44, bottom: 32 },
        legend: {
          type: "scroll",
          top: 0,
          left: "center",
          itemWidth: 24,
          itemHeight: 8,
          textStyle: { color: textColor, fontSize: 11 },
        },
        tooltip: { trigger: "axis", confine: true },
        xAxis: {
          type: "category",
          boundaryGap: false,
          data: days,
          axisLine: { lineStyle: { color: gridColor } },
          axisTick: { show: false },
          axisLabel: { color: mutedColor, fontSize: 10, hideOverlap: true },
          splitLine: { show: false },
        },
        yAxis: [
          combinedYAxis("left", 0, compactAxisValue, tokenColor, true),
          combinedYAxis("right", 0, compactAxisValue, requestColor, false),
          combinedYAxis("right", 56, compactCostAxisValue, costColor, false),
        ],
        series: [{
          name: t("settings.usageTrendTokens"),
          type: "line",
          yAxisIndex: 0,
          data: tokenValues,
          symbol: "circle",
          showSymbol: days.length <= 16,
          symbolSize: 6,
          lineStyle: { width: 2, type: "solid" },
          itemStyle: { color: tokenColor },
          emphasis: { focus: "series" },
          tooltip: { valueFormatter: function (value) { return Number(value || 0).toLocaleString(); } },
        }, {
          name: t("settings.usageTrendRequests"),
          type: "line",
          yAxisIndex: 1,
          data: requestValues,
          symbol: "diamond",
          showSymbol: days.length <= 16,
          symbolSize: 7,
          lineStyle: { width: 2, type: "dashed" },
          itemStyle: { color: requestColor },
          emphasis: { focus: "series" },
          tooltip: { valueFormatter: function (value) { return Number(value || 0).toLocaleString(); } },
        }, {
          name: t("settings.usageTrendCost"),
          type: "line",
          yAxisIndex: 2,
          data: costValues,
          symbol: "triangle",
          showSymbol: days.length <= 16,
          symbolSize: 7,
          lineStyle: { width: 2, type: "dotted" },
          itemStyle: { color: costColor },
          emphasis: { focus: "series" },
          tooltip: { valueFormatter: function (value) { return currencySymbol + Number(value || 0).toFixed(2); } },
        }],
      }, true);
    }
    renderChart();

    function resizeChart() { chart.resize(); }
    var resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(resizeChart) : null;
    if (resizeObserver) resizeObserver.observe(node);
    var themeObserver = typeof MutationObserver === "function" ? new MutationObserver(renderChart) : null;
    if (themeObserver) themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return function () {
      if (resizeObserver) resizeObserver.disconnect();
      if (themeObserver) themeObserver.disconnect();
      chart.dispose();
    };
  }, [signature]);

  return React.createElement("div", { className: "wb-usage-trend" },
    React.createElement("div", { className: "wb-usage-trend-head" },
      React.createElement("strong", null, t("settings.usageTrendTitle")),
      React.createElement("small", null, t("settings.usageTrendHint")),
    ),
    items.length >= 2
      ? React.createElement("div", {
          ref: chartRef,
          className: "wb-usage-trend-canvas",
          role: "img",
          "aria-label": t("settings.usageTrendHint"),
        })
      : React.createElement("div", { className: "wb-usage-trend-empty" }, t("settings.usageTrendEmpty")),
  );
}

// ── Budget Panel ──
function BudgetPanel(p) {
  var { t, config } = p;
  var mode = p.mode === "usage" ? "usage" : "budget";
  var dataStore = window.CyreneUI.require("data");
  dataStore.useVersion();
  var dashboard = dataStore.state.dashboard || {};
  var profileUsage = dashboard.usage || {};

  // ── Init from config (unified config API) ──
  var [budgetEnabled, setBudgetEnabled] = useStateSt(!!config.budget_enabled);
  var [budgetMonthly, setBudgetMonthly] = useStateSt(String(config.budget_monthly != null ? config.budget_monthly : 50));
  var [budgetCurrency, setBudgetCurrency] = useStateSt(config.budget_currency || "CNY");
  var [budgetAction, setBudgetAction] = useStateSt(config.budget_action || "warn");
  var [budgetMode, setBudgetMode] = useStateSt(config.budget_mode || "normal");
  var [budgetStartDay, setBudgetStartDay] = useStateSt(String(config.budget_start_day != null ? config.budget_start_day : 1));
  var [budgetSaved, setBudgetSaved] = useStateSt("");
  var codexQuotaModel = window.CyreneUI.require("model");
  var [codexQuota, setCodexQuota] = useStateSt({ connected: false, limits: {} });
  var [providerUsage, setProviderUsage] = useStateSt([]);
  var [providerUsageLoading, setProviderUsageLoading] = useStateSt(true);
  var providerRefreshTimer = useRefSt(null);

  var BUDGET_KEY = "cyrene-budget";

  // Sync to localStorage (cache for ProjectRail / backward compat)
  var budgetSaveTimer = useRefSt(null);

  function syncLocalStorage(values) {
    try {
      localStorage.setItem(BUDGET_KEY, JSON.stringify(values || {
        enabled: budgetEnabled,
        monthly: budgetMonthly,
        currency: budgetCurrency,
        action: budgetAction,
        mode: budgetMode,
        startDay: budgetStartDay,
      }));
    } catch (e) {}
  }

  function scheduleClearSaved() {
    if (budgetSaveTimer.current) clearTimeout(budgetSaveTimer.current);
    budgetSaveTimer.current = setTimeout(function () { setBudgetSaved(""); }, 1200);
  }

  function saveBudgetConfig(body) {
    return settingsFetch("/api/settings/config", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (r.ok) {
        setBudgetSaved("");
        showSettingsToast(t("settings.saved"), "success");
        syncLocalStorage(body);
        try { window.dispatchEvent(new CustomEvent("budget-saved")); } catch (e) {}
      } else {
        setBudgetSaved("");
        showSettingsToast(t("settings.error"), "error");
      }
      scheduleClearSaved();
    }).catch(function (error) {
      setBudgetSaved("");
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
      scheduleClearSaved();
    });
  }

  function toggleEnabled() {
    var next = !budgetEnabled;
    setBudgetEnabled(next);
    saveBudgetConfig({ budget_enabled: next });
  }

  function fetchCodexQuota() {
    settingsFetch("/api/settings/openai-oauth/limits")
      .then(readSettingsResponse)
      .then(function (data) {
        setCodexQuota(data);
        codexQuotaModel.writeCodexQuotaCache(data);
      })
      .catch(function () {});
  }

  function fetchProviderUsage(forceRefresh, quiet) {
    if (!quiet) setProviderUsageLoading(true);
    settingsFetch("/api/settings/model-config/provider-usage" + (forceRefresh ? "?refresh=true" : ""))
      .then(readSettingsResponse)
      .then(function (data) {
        var items = data && Array.isArray(data.items) ? data.items : [];
        setProviderUsage(items);
        if (items.some(function (item) { return item.refreshing === true; })) {
          if (providerRefreshTimer.current) clearTimeout(providerRefreshTimer.current);
          providerRefreshTimer.current = setTimeout(function () {
            fetchProviderUsage(false, true);
          }, 750);
        }
      })
      .catch(function () {})
      .finally(function () { if (!quiet) setProviderUsageLoading(false); });
  }

  function saveBudget() {
    saveBudgetConfig({
      budget_monthly: Number(budgetMonthly) || 0,
      budget_currency: budgetCurrency,
      budget_action: budgetAction,
      budget_mode: budgetMode,
      budget_start_day: Number(budgetStartDay) || 1,
    }).then(fetchStats);
  }

  // ── Stats from API ──
  var [budgetModels, setBudgetModels] = useStateSt([]);
  var [budgetDaily, setBudgetDaily] = useStateSt([]);
  var [totalCost, setTotalCost] = useStateSt(0);
  var [totalRequests, setTotalRequests] = useStateSt(0);
  var [maxRequestTokens, setMaxRequestTokens] = useStateSt(0);
  var [maxRequestCost, setMaxRequestCost] = useStateSt(0);
  var [budgetLoading, setBudgetLoading] = useStateSt(true);

  function fetchStats() {
    settingsFetch("/api/settings/budget/stats")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        setBudgetModels(d.models || []);
        setBudgetDaily(d.by_day || []);
        setTotalCost(d.total_cost || 0);
        setTotalRequests(d.total_requests || 0);
        setMaxRequestTokens(d.max_request_tokens || 0);
        setMaxRequestCost(d.max_request_cost || 0);
        setBudgetLoading(false);
      })
      .catch(function () { setBudgetLoading(false); });
  }

  useEffectSt(function () {
    fetchStats();
    fetchCodexQuota();
    fetchProviderUsage(false, false);
    return function () {
      if (budgetSaveTimer.current) clearTimeout(budgetSaveTimer.current);
      if (providerRefreshTimer.current) clearTimeout(providerRefreshTimer.current);
    };
  }, []);

  var budgetNum = Number(budgetMonthly) || 0;
  var budgetRatio = budgetNum > 0 ? Math.min(totalCost / budgetNum, 1) : 0;
  var currencySymbol = budgetCurrency === "CNY" ? "¥" : "$";
  var periodPromptTokens = budgetModels.reduce(function (sum, item) { return sum + (Number(item.prompt_tokens) || 0); }, 0);
  var periodCompletionTokens = budgetModels.reduce(function (sum, item) { return sum + (Number(item.completion_tokens) || 0); }, 0);
  var periodTotalTokens = periodPromptTokens + periodCompletionTokens;
  var averageRequestTokens = totalRequests > 0 ? periodTotalTokens / totalRequests : 0;
  var averageRequestCost = totalRequests > 0 ? totalCost / totalRequests : 0;
  var peakUsageDay = budgetDaily.reduce(function (peak, item) {
    return !peak || Number(item.total_tokens || 0) >= Number(peak.total_tokens || 0) ? item : peak;
  }, null);
  var peakCallsDay = budgetDaily.reduce(function (peak, item) {
    return !peak || Number(item.requests || 0) >= Number(peak.requests || 0) ? item : peak;
  }, null);
  var profileSpend = budgetCurrency === "CNY"
    ? Number(profileUsage.spend_cny || 0)
    : Number(profileUsage.spend_usd || 0);
  var profilePromptTokens = Number(profileUsage.prompt_tokens || 0);
  var profileCompletionTokens = Number(profileUsage.completion_tokens || 0);
  var profileTotalTokens = Number(profileUsage.total_tokens || (profilePromptTokens + profileCompletionTokens));
  var codexWindows = codexQuotaModel.codexQuotaWindows(codexQuota.limits);
  var codexPlan = codexQuotaModel.codexPlanLabel(
    codexQuota.account,
    codexQuota.limits
  );
  var codexUsageItem = {
    connection_id: "codex_oauth",
    provider: "codex_oauth",
    label: "Codex",
    kind: "codex_quota",
    status: codexQuota.connected ? "ok" : codexQuota.error ? "error" : "unconfigured",
    available: codexQuota.connected === true,
    error: codexQuota.error || "",
    plan: codexPlan || "",
    windows: codexWindows.map(function (windowData) {
      return {
        model: "codex",
        kind: windowData.kind,
        label: windowData.label,
        remaining_percent: windowData.remainingPercent,
        used_percent: windowData.usedPercent,
        reset_at: windowData.resetsAt ? new Date(windowData.resetsAt * 1000).toISOString() : null,
      };
    }),
  };
  var providerUsageItems = providerUsage.slice();
  if (codexQuota.connected === true) providerUsageItems.push(codexUsageItem);
  var minimaxUsageItems = providerUsageItems.filter(function (item) {
    return item.provider === "minimax";
  });
  var compactProviderUsageItems = providerUsageItems.filter(function (item) {
    return item.provider !== "minimax";
  });

  function formatCost(val) {
    return currencySymbol + val.toFixed(2);
  }

  function formatTokens(n) {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
    return String(n);
  }

  function formatPeakDate(item) {
    if (!item || !item.day) return "";
    var value = new Date(String(item.day) + "T00:00:00");
    return t("settings.usagePeakDate", {
      date: isNaN(value.getTime()) ? String(item.day) : value.toLocaleDateString(),
    });
  }

  function usageMetric(value, label, detail) {
    return React.createElement("div", { className: "wb-usage-metric" },
      React.createElement("strong", null, value),
      React.createElement("span", null, label),
      detail && React.createElement("small", null, detail),
    );
  }

  function providerAmount(value, currency) {
    var number = Number(value);
    if (!isFinite(number)) return String(value || "0") + " " + currency;
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency", currency: currency || "CNY", minimumFractionDigits: 2,
      }).format(number);
    } catch (error) {
      return number.toFixed(2) + " " + currency;
    }
  }

  function providerWindowLabel(windowData) {
    if (windowData.label) return windowData.label;
    var windowLabel = windowData.kind === "weekly"
      ? t("settings.providerUsageWeekly")
      : t("settings.providerUsageInterval");
    return (windowData.model && windowData.model !== "general" ? windowData.model + " · " : "") + windowLabel;
  }

  function providerUsageCard(item) {
    var visibleWindows = (item.windows || []).filter(function (windowData) {
      return String(windowData.model || "").trim().toLowerCase() !== "video";
    });
    var stateClass = item.status === "ok" && item.available === false ? "empty" : item.status;
    var stateLabel = item.status === "ok"
      ? item.available === false ? t("settings.providerUsageDepleted") : t("settings.providerUsageConnected")
      : item.status === "unconfigured"
        ? t("settings.providerUsageUnconfigured")
        : t("settings.providerUsageUnavailable");
    return React.createElement("article", {
      className: "wb-provider-usage-card",
      id: item.kind === "codex_quota" ? "setting-codex-quota" : undefined,
      key: item.connection_id || item.provider,
    },
      React.createElement("header", { className: "wb-provider-usage-card-head" },
        React.createElement("div", null,
          React.createElement("strong", null, item.label || item.provider),
          React.createElement("small", null, item.kind === "balance"
            ? t("settings.providerUsageBalance")
            : item.kind === "codex_quota"
              ? t("settings.codexQuotaPlan", { plan: item.plan || "—" })
              : t("settings.providerUsageQuota")),
        ),
        React.createElement("span", { className: "wb-provider-usage-state is-" + stateClass }, stateLabel),
      ),
      item.status === "unconfigured" && React.createElement("p", { className: "wb-hint" },
        t(item.kind === "codex_quota" ? "settings.codexQuotaLoginHint" : "settings.providerUsageConfigureKey")
      ),
      item.status === "error" && React.createElement("p", { className: "wb-provider-usage-error" }, item.error || t("settings.providerUsageUnavailable")),
      item.status === "ok" && item.kind === "balance" && React.createElement("div", { className: "wb-provider-balance-list" },
        (item.balances || []).map(function (balance) {
          return React.createElement("div", { className: "wb-provider-balance", key: balance.currency },
            React.createElement("strong", null, providerAmount(balance.total, balance.currency)),
            React.createElement("span", null, t("settings.providerUsageTotalBalance")),
            React.createElement("small", null,
              t("settings.providerUsageBalanceBreakdown", {
                toppedUp: providerAmount(balance.topped_up, balance.currency),
                granted: providerAmount(balance.granted, balance.currency),
              })
            ),
          );
        }),
        !(item.balances || []).length && React.createElement("p", { className: "wb-hint" }, t("settings.providerUsageNoBalance")),
      ),
      item.status === "ok" && (item.kind === "quota" || item.kind === "codex_quota") && React.createElement("div", { className: "wb-provider-quota-list" },
        visibleWindows.map(function (windowData, index) {
          var remaining = windowData.remaining_percent == null ? null : Number(windowData.remaining_percent);
          var used = windowData.used_percent == null ? 0 : Number(windowData.used_percent);
          var valueLabel = windowData.unlimited
            ? t("settings.providerUsageUnlimited")
            : windowData.ambiguous
              ? t("settings.providerUsageStatusUnknown")
            : remaining == null
              ? "—"
              : t("settings.providerUsageRemaining", { pct: Math.round(remaining) });
          return React.createElement("div", { className: "wb-provider-quota-window", key: windowData.model + "-" + windowData.kind + "-" + index },
            React.createElement("div", { className: "wb-provider-quota-label" },
              React.createElement("span", null, providerWindowLabel(windowData)),
              React.createElement("strong", null, valueLabel),
            ),
            !windowData.unlimited && !windowData.ambiguous && React.createElement("div", { className: "wb-budget-progress-bar" },
              React.createElement("div", {
                className: "wb-budget-progress-fill" + (used >= 100 ? " over" : used >= 80 ? " high" : ""),
                style: { width: Math.max(0, Math.min(100, used)) + "%" },
              }),
            ),
            React.createElement("small", null, windowData.reset_at
              ? t("settings.providerUsageResets", { time: new Date(windowData.reset_at).toLocaleString() })
              : t("settings.providerUsageResetUnknown")),
            windowData.ambiguous && React.createElement("small", { className: "wb-provider-quota-warning" },
              t("settings.providerUsageStatusUnknownHint")
            ),
          );
        }),
        !visibleWindows.length && React.createElement("p", { className: "wb-hint" }, t("settings.providerUsageNoQuota")),
      ),
      item.refreshed_at && React.createElement("footer", null, t("settings.providerUsageUpdated", { time: new Date(item.refreshed_at).toLocaleString() })),
    );
  }

  return React.createElement("div", {
    className: "settings-panel" + (mode === "usage" ? " wb-usage-settings" : ""),
  },
    SectionTitle(
      t(mode === "usage" ? "settings.usage" : "settings.budget"),
      t(mode === "usage" ? "settings.usageSubtitle" : "settings.budgetSubtitle")
    ),

    mode === "usage" && SectionBlock(t("settings.profileUsageSnapshot"), t("settings.profileUsageSnapshotHint"),
      React.createElement("div", { className: "wb-usage-metrics is-profile" },
        usageMetric(formatCost(profileSpend), t("profile.spend")),
        usageMetric(Number(profileUsage.requests || 0).toLocaleString(), t("profile.requests")),
        usageMetric(formatTokens(profileTotalTokens), t("profile.tokens")),
        usageMetric(formatTokens(profilePromptTokens), t("settings.usageInputTokens")),
        usageMetric(formatTokens(profileCompletionTokens), t("settings.usageOutputTokens")),
      ),
    ),

    // ── Overview section ──
    mode === "usage" && SectionBlock(t("settings.usageBillingPeriod"), t("settings.usageBillingPeriodHint"),
      React.createElement("div", { className: "wb-budget-summary" },
        React.createElement("div", { className: "wb-usage-metrics is-period" },
          usageMetric(formatCost(totalCost), t("settings.budgetSpend")),
          usageMetric(totalRequests.toLocaleString(), t("settings.budgetRequests")),
          usageMetric(formatTokens(periodTotalTokens), t("settings.budgetTokens")),
          usageMetric(formatTokens(periodPromptTokens), t("settings.usageInputTokens")),
          usageMetric(
            totalRequests > 0 ? formatTokens(Math.round(averageRequestTokens)) : "—",
            t("settings.usageAverageTokens"),
            totalRequests > 0 ? t("settings.usageMaxRequestTokens", { tokens: formatTokens(maxRequestTokens) }) : ""
          ),
          usageMetric(
            formatCost(averageRequestCost),
            t("settings.usageAverageCost"),
            totalRequests > 0 ? t("settings.usageMaxRequestCost", { cost: formatCost(maxRequestCost) }) : ""
          ),
          usageMetric(peakUsageDay ? formatTokens(peakUsageDay.total_tokens) : "—", t("settings.usagePeakUsage"), formatPeakDate(peakUsageDay)),
          usageMetric(peakCallsDay ? Number(peakCallsDay.requests || 0).toLocaleString() : "—", t("settings.usagePeakCalls"), formatPeakDate(peakCallsDay)),
        ),
        React.createElement(UsageTrendChart, { t: t, items: budgetDaily, currencySymbol: currencySymbol }),
      ),
    ),

    // ── Budget configuration ──
    mode === "budget" && React.cloneElement(SectionBlock(t("settings.budgetConfig"), null,
      FieldRow(t("settings.budgetEnable"), t("settings.budgetEnableHint"),
        Toggle(budgetEnabled, toggleEnabled),
      ),
      // Budget mode — always visible, independent of the budget toggle
      FieldRow(t("settings.budgetMode"), t("settings.budgetModeHint"),
        React.createElement("select", {
          className: "wb-select",
          value: budgetMode,
          onChange: function (e) { setBudgetMode(e.target.value); },
          style: { maxWidth: 160 },
        },
          React.createElement("option", { value: "economy" }, t("settings.budgetModeEconomy")),
          React.createElement("option", { value: "normal" }, t("settings.budgetModeNormal")),
        ),
      ),
      budgetEnabled && React.createElement(React.Fragment, null,
        FieldRow(t("settings.budgetMonthly"), t("settings.budgetMonthlyHint"),
          React.createElement("div", { className: "wb-inline-row" },
            React.createElement("input", {
              className: "wb-input mono",
              type: "text", inputMode: "decimal",
              value: budgetMonthly,
              onChange: function (e) { setBudgetMonthly(e.target.value); },
              placeholder: "0",
              style: { maxWidth: 120 },
              key: "budget-input",
            }),
            React.createElement("select", {
              className: "wb-select",
              value: budgetCurrency,
              onChange: function (e) { setBudgetCurrency(e.target.value); },
              style: { maxWidth: 90 },
            },
              React.createElement("option", { value: "CNY" }, "CNY (¥)"),
              React.createElement("option", { value: "USD" }, "USD ($)"),
            ),
          ),
        ),

        // Billing cycle start day
        FieldRow(t("settings.budgetStartDay"), t("settings.budgetStartDayHint"),
          React.createElement("input", {
            className: "wb-input mono",
            type: "text", inputMode: "numeric",
            value: budgetStartDay,
            onChange: function (e) { setBudgetStartDay(e.target.value); },
            placeholder: "1",
            style: { maxWidth: 80 },
          }),
        ),

        FieldRow(t("settings.budgetAction"), t("settings.budgetActionHint"),
          React.createElement("select", {
            className: "wb-select",
            value: budgetAction,
            onChange: function (e) { setBudgetAction(e.target.value); },
            style: { maxWidth: 240 },
          },
            React.createElement("option", { value: "warn" }, t("settings.budgetActionWarn")),
            React.createElement("option", { value: "block" }, t("settings.budgetActionBlock")),
          ),
        ),
        React.createElement("div", { className: "wb-save-actions" },
          React.createElement("button", { className: "wb-btn primary", onClick: saveBudget },
            t("settings.saveApply")
          ),
          budgetSaved && React.createElement("span", { className: "wb-hint saved" }, budgetSaved),
        ),
      ),
    ), { id: "setting-budget" }),

    mode === "budget" && SectionBlock(t("settings.budgetOverview"), null,
      React.createElement("div", { className: "wb-budget-summary" },
        React.createElement("div", { className: "wb-usage-metrics is-period" },
          usageMetric(budgetEnabled ? formatCost(budgetNum) : "—", t("settings.budgetLimit")),
          usageMetric(budgetEnabled && budgetNum > 0 ? Math.round(budgetRatio * 100) + "%" : "—", t("settings.usageBudgetRate")),
        ),
        React.createElement("div", { className: "wb-budget-progress-wrap" },
          React.createElement("div", { className: "wb-budget-progress-bar" },
            React.createElement("div", {
              className: "wb-budget-progress-fill" + (budgetRatio >= 1 ? " over" : budgetRatio >= 0.8 ? " high" : ""),
              style: { width: Math.round(budgetRatio * 100) + "%" },
            }),
          ),
          React.createElement("span", { className: "wb-budget-progress-label" },
            t("settings.budgetUsed", { pct: Math.round(budgetRatio * 100) })
          ),
        ),
        !budgetEnabled && React.createElement("p", { className: "wb-hint", style: { textAlign: "center", marginTop: 8 } },
          t("settings.budgetDisabledHint")
        ),
      ),
    ),

    // ── Cost by model ──
    mode === "usage" && SectionBlock(t("settings.usageByModel"), t("settings.usageByModelHint"),
      React.createElement("div", { className: "wb-budget-model-grid" },
        budgetModels.map(function (item) {
          var modelPct = totalCost > 0 ? (item.cost / totalCost * 100) : 0;
          return React.createElement("article", { className: "wb-budget-model-card", key: item.model },
            React.createElement("header", { className: "wb-budget-model-card-head" },
              React.createElement("strong", { className: "wb-budget-model-name mono", title: item.model }, item.model),
              React.createElement("div", { className: "wb-budget-model-cost" },
                React.createElement("small", null, t("settings.budgetCost")),
                React.createElement("strong", null, formatCost(item.cost)),
              ),
            ),
            React.createElement("dl", { className: "wb-budget-model-stats" },
              React.createElement("div", null,
                React.createElement("dt", null, t("settings.budgetRequests")),
                React.createElement("dd", null, Number(item.requests || 0).toLocaleString()),
              ),
              React.createElement("div", null,
                React.createElement("dt", null, t("settings.usageInputTokens")),
                React.createElement("dd", null, formatTokens(item.prompt_tokens)),
              ),
              React.createElement("div", null,
                React.createElement("dt", null, t("settings.usageOutputTokens")),
                React.createElement("dd", null, formatTokens(item.completion_tokens)),
              ),
              React.createElement("div", null,
                React.createElement("dt", null, t("settings.budgetTokens")),
                React.createElement("dd", null, formatTokens(item.prompt_tokens + item.completion_tokens)),
              ),
            ),
            modelPct > 0 && React.createElement("div", { className: "wb-budget-model-bar-wrap" },
              React.createElement("div", { className: "wb-budget-model-bar", style: { width: modelPct + "%" }, "aria-hidden": "true" }),
            ),
          );
        }),
        !budgetLoading && !budgetModels.length && React.createElement("div", { className: "wb-budget-model-empty" },
          t("settings.usageNoModelData")
        ),
      ),
    ),

    mode === "usage" && SectionBlock(t("settings.providerUsage"), t("settings.providerUsageHint"),
      providerUsageItems.length > 0 && React.createElement("div", { className: "wb-provider-usage-grid" },
        React.createElement("div", { className: "wb-provider-usage-column is-compact" },
          compactProviderUsageItems.map(providerUsageCard),
        ),
        React.createElement("div", { className: "wb-provider-usage-column is-minimax" },
          minimaxUsageItems.map(providerUsageCard),
        ),
      ),
      !providerUsageLoading && !providerUsageItems.length && React.createElement("div", { className: "wb-budget-model-empty" },
        t("settings.providerUsageEmpty")
      ),
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

function SectionBlock(title, extra, ...children) {
  return React.createElement("div", { className: "wb-section-block" },
    React.createElement("div", { className: "wb-section-block-head" },
      React.createElement("b", null, title),
      typeof extra === "string" ? React.createElement("small", null, extra) : (extra || null),
    ),
    ...children,
  );
}

function FieldRow(label, hint, controls, key, anchorId) {
  return React.createElement("div", { className: "wb-field", key: key, id: anchorId || undefined },
    React.createElement("div", { className: "wb-label" },
      label,
      hint && React.createElement("small", null, hint),
    ),
    React.createElement("div", { className: "wb-controls" }, controls),
  );
}

function Toggle(on, onClick, disabled, label, extraProps) {
  return React.createElement("button", Object.assign({
    type: "button",
    className: "wb-toggle" + (on ? " on" : ""),
    role: "switch",
    "aria-checked": on ? "true" : "false",
    "aria-label": label || undefined,
    disabled: !!disabled,
    onClick: disabled ? undefined : onClick,
  }, extraProps || {}));
}

function ModelCard(children, key) {
  return React.createElement("div", { className: "wb-model-card", key: key }, ...children);
}

function ModelField(label, input) {
  return React.createElement("div", { className: "wb-model-line" },
    React.createElement("span", null, label),
    input,
  );
}

// ── Export ──
window.CyreneUI.settings = window.CyreneUI.register("settings", {
  Page: SettingsPage,
});
