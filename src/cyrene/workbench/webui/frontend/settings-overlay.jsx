import { useWorkbenchI18n } from "./workbench-i18n.jsx"
import { DoctorPanel } from "./features/doctor/doctor.jsx"
import {
  workbenchServices, WbcVoice, wbcStartVoiceRecorder, wbcTranscribeVoiceBlob,
  useStateSt, useEffectSt, useRefSt, useMemoSt,
  readTweak, readCapability, readSettingsResponse, settingsFetch, showSettingsToast,
} from "./features/settings/shared.jsx"
import {
  RemotePanel, GeneralPanel, SearchPanel, ChannelsPanel, AgentsPanel,
  AppearancePanel, CapabilitiesPanel, DataPanel, requestDataPanelStorage, AboutPanel,
  PluginRegistryPanel, HooksPanel, ShortcutsPanel, BudgetPanel, MediaPanel,
} from "./features/settings/index.jsx"

// ── Tab definitions ──
var TABS = [
  { id: "doctor", labelKey: "settings.doctor", icon: "stethoscope" },
  { id: "profile", labelKey: "rail.profile", icon: "user" },
  { id: "general", labelKey: "settings.general", icon: "settings" },
  { id: "search", labelKey: "settings.searchProviders", icon: "browser" },
  { id: "appearance", labelKey: "settings.appearance", icon: "palette" },
  { id: "shortcuts", labelKey: "settings.shortcuts", icon: "keyboard" },
  { id: "model-usage", labelKey: "settings.modelUsage", icon: "route" },
  { id: "models", labelKey: "settings.modelServices", icon: "box" },
  { id: "media", labelKey: "settings.mediaGeneration", icon: "photo-video" },
  { id: "agents", labelKey: "settings.agents", icon: "robot" },
  { id: "voice", labelKey: "settings.voiceTab", icon: "microphone" },
  { id: "channels", labelKey: "settings.channels", icon: "messages" },
  { id: "remote", labelKey: "settings.remoteTab", icon: "device-desktop-up" },
  { id: "plugin-registry", labelKey: "settings.pluginRegistry", icon: "package" },
  { id: "hooks", labelKey: "settings.hooks", icon: "webhook" },
  { id: "integrations", labelKey: "settings.integrations", icon: "plug-connected" },
  // Keep the budget surface available for direct routing while its sidebar
  // entry is temporarily withheld from the settings navigation.
  { id: "budget", labelKey: "settings.budget", icon: "wallet", hidden: true },
  { id: "usage", labelKey: "settings.usage", icon: "chart-bar" },
  { id: "data", labelKey: "settings.data", icon: "database" },
  { id: "about", labelKey: "settings.about", icon: "info-circle" },
];

var SETTINGS_TAB_GROUPS = [
  { labelKey: "settings.group.general", ids: ["profile", "general", "search", "appearance", "shortcuts"] },
  { labelKey: "settings.group.intelligence", ids: ["model-usage", "models", "media", "agents", "voice"] },
  { labelKey: "settings.group.connections", ids: ["channels", "remote"] },
  { labelKey: "settings.group.extensionsSystem", ids: ["plugin-registry", "hooks", "integrations"] },
  { labelKey: "settings.group.data", ids: ["budget", "usage", "data"] },
  { labelKey: "settings.group.other", ids: ["doctor", "about"] },
];

var TABS_BY_ID = TABS.reduce(function (acc, item) {
  acc[item.id] = item;
  return acc;
}, {});

var SETTINGS_TAB_MODULES = {
  search: ["search"],
  "model-usage": ["model"],
  models: ["model"],
  media: ["media"],
  agents: ["soul", "proactive", "skills", "subagent"],
  voice: ["voice"],
  channels: ["channels"],
  remote: ["remote"],
  integrations: ["office", "knowledge"],
};

var LEGACY_SETTINGS_TABS = {
  "mcp-providers": "plugin-registry",
  extensions: "plugin-registry",
  skills: "plugin-registry",
  plugins: "plugin-registry",
};

var LEGACY_SETTINGS_ANCHORS = {
  "setting-mcp-providers": "setting-plugin-registry",
  "setting-extensions": "setting-plugin-registry",
  "setting-skills": "setting-plugin-registry",
  "setting-plugin-packs": "setting-plugin-registry",
  "setting-standalone-plugins": "setting-plugin-registry",
};

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

// ── Settings Page ──
function agentSettingsPayload(config, agentProactive, pluginModules) {
  var modules = Array.isArray(pluginModules) ? pluginModules : [];
  var payload = {};
  if (modules.indexOf("subagent") >= 0) Object.assign(payload, {
    spawn_policy: config.spawn_policy || "conservative",
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
  });
  if (modules.indexOf("proactive") >= 0) Object.assign(payload, {
    heartbeat_interval: Number(config.heartbeat_interval) || 1800,
    agent_proactive: agentProactive,
  });
  if (modules.indexOf("skills") >= 0) {
    payload.background_skill_learning = config.background_skill_learning !== false;
  }
  return payload;
}

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
  var dataStore = workbenchServices.data();
  dataStore.useVersion();
  var pluginModules = Array.isArray(dataStore.state.pluginModules)
    ? dataStore.state.pluginModules : [];
  function settingsTabAvailable(id) {
    var required = SETTINGS_TAB_MODULES[id];
    return !required || required.some(function (module) {
      return pluginModules.indexOf(module) >= 0;
    });
  }
  function normalizeSettingsTab(value) {
    var target = LEGACY_SETTINGS_TABS[value] || value;
    return TABS_BY_ID[target] && settingsTabAvailable(target) ? target : "general";
  }
  var normalizedScrollToId = LEGACY_SETTINGS_ANCHORS[scrollToId] || scrollToId;
  var [tab, setTab] = useStateSt(normalizeSettingsTab(initialTab));

  useEffectSt(function () {
    if (!settingsTabAvailable(tab)) setTab("general");
  }, [tab, pluginModules.join("|")]);

  // Warm the comparatively expensive storage scan as soon as Settings is
  // mounted. DataPanel shares this request, so opening Data while the scan is
  // in flight never starts a second disk walk.
  useEffectSt(function () {
    requestDataPanelStorage().catch(function () {});
  }, []);

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
    if (!normalizedScrollToId) return;
    var attempts = 0;
    var timer = null;
    function showDeepLinkFallback() {
      var feedback = workbenchServices.feedback();
      if (feedback && typeof feedback.showToast === "function") {
        feedback.showToast(t("settings.deepLinkUnavailable", null, "This setting is currently unavailable"), "info");
      }
      var container = document.querySelector(".settings-overlay-content");
      if (container && typeof container.scrollTo === "function") {
        container.scrollTo({ top: 0, behavior: "smooth" });
      }
    }
    function tryScroll() {
      var el = document.getElementById(normalizedScrollToId);
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
  }, [normalizedScrollToId]);

  // ── General state ──
  var [desktopNotifications, setDesktopNotifications] = useStateSt(function () {
    try { return localStorage.getItem("cyrene-desktop-notifications") === "1"; } catch (e) { return false; }
  });
  var [mapProvider, setMapProvider] = useStateSt(function () {
    try { return localStorage.getItem("cyrene-tweak-map-provider") || "direct"; } catch (e) { return "direct"; }
  });
  var [amapKey, setAmapKey] = useStateSt("");
  var [amapKeySaved, setAmapKeySaved] = useStateSt("");

  // ── Config state ──
  var [config, setConfig] = useStateSt({
    model: "—", base_url: "—", assistant_name: "—",
    base_dir: "—", data_dir: "—", soul_path: "—",
    workspace_dir: "—", soul_content: "", spawn_policy: "conservative",
    heartbeat_interval: 1800, background_skill_learning: true,
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
  var [voiceStatus, setVoiceStatus] = useStateSt({
    asr_ready: false,
    tts_model_ready: false,
    voice_profile_ready: false,
    voice_preset_ready: false,
    voice_mode: "preset",
    voice_preset: "kokoro-zm_009",
    voice_presets: [],
    tts_model_selection: "auto",
    tts_models: [],
    tts_provider: "local",
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
  var [exportFmt, setExportFmt] = useStateSt("markdown");
  var [exportMsg, setExportMsg] = useStateSt("");

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

    settingsFetch("/api/backup/list").then(function (r) { return r.json(); }).then(function (d) { if (d.ok) setBackupList(d.backups || []); }).catch(function () {});
  }, []);

  useEffectSt(function () {
    if (pluginModules.indexOf("voice") < 0) return;
    refreshVoiceStatus();
  }, [pluginModules.indexOf("voice") >= 0]);

  useEffectSt(function () {
    var hasChannels = pluginModules.indexOf("channels") >= 0;
    var hasMap = pluginModules.indexOf("map") >= 0;
    if (!hasChannels && !hasMap) return;
    settingsFetch("/api/settings/keys").then(function (r) { return r.json(); }).then(function (p) {
      if (hasChannels) {
        var tk = (p.keys || []).find(function (item) { return item.key === "TELEGRAM_BOT_TOKEN"; });
        if (tk) setTelegramToken(tk.value || "");
      }
      if (hasMap) {
        var ak = (p.keys || []).find(function (item) { return item.key === "AMAP_API_KEY"; });
        if (ak) setAmapKey(ak.value || "");
      }
    }).catch(function () {});
  }, [pluginModules.indexOf("channels") >= 0, pluginModules.indexOf("map") >= 0]);

  useEffectSt(function () {
    if (pluginModules.indexOf("voice") < 0) return undefined;
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
  }, [pluginModules.indexOf("voice") >= 0]);

  function saveSoul() {
    setSoulStatus("");
    settingsFetch("/api/settings/soul", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: soulDraft }) })
      .then(function () { setSoulStatus(""); showSettingsToast(t("settings.saved"), "success"); })
      .catch(function (error) { showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error"); });
  }

  function saveAgents() {
    settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(agentSettingsPayload(config, agentProactive, pluginModules)),
    }).then(function () {
      showSettingsToast(t("settings.saved"), "success");
    }).catch(function (error) {
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
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

  function saveVoiceTtsModel(nextModel) {
    if (voiceBusy || !nextModel || nextModel === voiceStatus.tts_model_selection) return;
    var previous = voiceStatus;
    publishVoiceStatus({ ...voiceStatus, tts_model_selection: nextModel });
    setVoiceBusy("settings");
    settingsFetch("/api/voice/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tts_model: nextModel }),
    }).then(readSettingsResponse).then(function (payload) {
      publishVoiceStatus(payload);
      setVoiceNotice("");
    }).catch(function (error) {
      publishVoiceStatus(previous);
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
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
  function formatDate(iso) {
    if (!iso) return "—";
    return workbenchServices.i18n().formatDate(iso, { dateStyle: "medium", timeStyle: "short" }) || "—";
  }

  useEffectSt(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = workbenchServices.uiSurface();
    var unregister = TABS.filter(function (item) {
      return item.hidden !== true && settingsTabAvailable(item.id);
    }).map(function (item) {
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
  }, [tab, t, pluginModules.join("|")]);

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
                if (!item || item.hidden === true || !settingsTabAvailable(id)) return null;
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
            React.createElement(workbenchServices.profile().Page)
          ),
          tab === "general" && React.createElement(GeneralPanel, { t, lang, setLang, desktopNotifications, toggleDesktopNotifications, mapProvider, setMapProvider, amapKey, setAmapKey, amapKeySaved, setAmapKeySaved, project, pluginModules }),
          tab === "search" && React.createElement(SearchPanel, { t: t }),
          tab === "models" && React.createElement(workbenchServices.modelSettings().ServicesPage, { t: t, project: project }),
          tab === "model-usage" && React.createElement(workbenchServices.modelSettings().UsagePage, { t: t, project: project }),
          tab === "media" && React.createElement(MediaPanel, { t: t, available: settingsTabAvailable("media") }),
          tab === "channels" && ChannelsPanel({ t, telegramToken, setTelegramToken, telegramSaved, setTelegramSaved, notifyTelegram, setNotifyTelegram, notifyWechat, setNotifyWechat }),
          tab === "remote" && React.createElement(RemotePanel, { t }),
          tab === "agents" && AgentsPanel({ t, config, setConfig, configLoading, soulDraft, setSoulDraft, soulStatus, saveSoul, agentProactive, setAgentProactive, saveAgents, pluginModules }),
          tab === "appearance" && React.createElement(AppearancePanel, { t, tweaks, setTweak, actualTheme, theme: initialTheme }),
          tab === "voice" && CapabilitiesPanel({
            t,
            voiceStatus, voiceReferenceText, setVoiceReferenceText,
            voiceReferenceFile, setVoiceReferenceFile, voiceReferencePhase, voiceReferenceElapsed,
            startVoiceReferenceRecording, finishVoiceReferenceRecording,
            voiceBusy, voiceNotice,
            saveVoiceBooleanSetting, saveVoiceMode, saveVoicePreset, saveVoiceTtsModel, saveVoiceProfile, deleteVoiceProfile,
          }),
          tab === "plugin-registry" && React.createElement(PluginRegistryPanel, { t: t, pluginModules: pluginModules }),
          tab === "hooks" && React.createElement(HooksPanel, { t: t }),
          tab === "integrations" && React.createElement(GeneralPanel, { integrationsOnly: true, t, lang, setLang, desktopNotifications, toggleDesktopNotifications, mapProvider, setMapProvider, amapKey, setAmapKey, amapKeySaved, setAmapKeySaved, project, pluginModules }),
          tab === "shortcuts" && React.createElement(ShortcutsPanel, { t }),
          tab === "data" && React.createElement(DataPanel, { t, redactSecrets, saveRedactSecrets, config, configLoading, resetStatus, setResetStatus, resetting, setResetting, backupList, backupMsg, setBackupMsg, loadBackups, exportSids, setExportSids, exportFmt, setExportFmt, exportMsg, setExportMsg, formatBytes, formatDate }),
          (tab === "budget" || tab === "usage") && React.createElement(BudgetPanel, { t, config, mode: tab }),
          tab === "doctor" && React.createElement(DoctorPanel, {}),
          tab === "about" && AboutPanel({ t, config }),
        ),
      ),
    ),
  );
}

// ── Export ──
window.CyreneUI.settings = window.CyreneUI.register("settings", {
  Page: SettingsPage,
});
