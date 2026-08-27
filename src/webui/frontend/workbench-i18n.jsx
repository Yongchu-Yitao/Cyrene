import { EXTENSION_TRANSLATIONS } from "./shared/i18n/extension-translations.jsx"
import { WORKBENCH_TRANSLATIONS_EN } from "./shared/i18n/catalog-en.jsx"
import { WORKBENCH_TRANSLATIONS_ZH } from "./shared/i18n/catalog-zh.jsx"
import { WORKBENCH_TOOL_NAME_ALIASES } from "./shared/i18n/tool-name-aliases.jsx"

// Workbench-only i18n, independent from global renderer state.
var WORKBENCH_TRANSLATIONS = {
  en: { ...EXTENSION_TRANSLATIONS.en, ...WORKBENCH_TRANSLATIONS_EN },
  zh: { ...EXTENSION_TRANSLATIONS.zh, ...WORKBENCH_TRANSLATIONS_ZH },
};

var workbenchI18nLang = "en";
var workbenchI18nVersion = 0;
var __workbenchI18nSubscribers = new Set();
var __workbenchMissingTranslationKeys = new Set();
var __workbenchExpectedDesktopLanguage = "";

function workbenchUsableTranslation(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function reportWorkbenchMissingTranslation(key, lang, fallbackUsed) {
  var marker = lang + ":" + key;
  if (__workbenchMissingTranslationKeys.has(marker)) return;
  __workbenchMissingTranslationKeys.add(marker);
  try {
    window.dispatchEvent(new CustomEvent("cyrene:i18n-missing", {
      detail: { key: String(key), lang: lang, fallbackUsed: fallbackUsed === true },
    }));
  } catch (e) {}
}

function workbenchInterpolate(text, params) {
  if (!params) return text;
  var pluralValue = params.count !== undefined ? Number(params.count) : Number(params.n);
  text = text.replace(/\{\{([^{}|]*)\|([^{}]*)\}\}/g, function (_match, singular, plural) {
    return pluralValue === 1 ? singular : plural;
  });
  Object.keys(params).forEach(function (name) {
    text = text.split("{" + name + "}").join(String(params[name]));
  });
  return text;
}

function workbenchTranslateForLang(key, lang, params, fallback) {
  lang = lang === "zh" ? "zh" : "en";
  var dict = WORKBENCH_TRANSLATIONS[lang] || WORKBENCH_TRANSLATIONS.en;
  var text = dict[key];
  var resolvedKey = key;
  if (text === undefined && String(key).indexOf("toolName.") === 0) {
    var toolName = String(key).slice("toolName.".length);
    toolName = toolName.replace(/\.r[23]$/, "");
    var alias = WORKBENCH_TOOL_NAME_ALIASES[toolName];
    if (alias) {
      resolvedKey = "toolName." + alias;
      text = dict[resolvedKey];
    } else if (toolName !== String(key).slice("toolName.".length)) {
      resolvedKey = "toolName." + toolName;
      text = dict[resolvedKey];
    }
  }
  var localeMissing = !workbenchUsableTranslation(text);
  if (localeMissing) text = (WORKBENCH_TRANSLATIONS.en || {})[resolvedKey];
  if (!workbenchUsableTranslation(text)) {
    text = fallback !== undefined ? fallback : key;
  }
  if (localeMissing) reportWorkbenchMissingTranslation(key, lang, text !== key);
  return workbenchInterpolate(text, params);
}

function workbenchT(key, params, fallback) {
  // Most Workbench components historically call t(key, fallback), while
  // parameterized strings call t(key, params, fallback). Accept both forms so
  // unknown dynamic keys fall back to their raw user-facing value instead of
  // leaking prefixes such as `toolName.` or `memory.learning.toolParam.`.
  if (typeof params === "string" && fallback === undefined) {
    fallback = params;
    params = null;
  }
  return workbenchTranslateForLang(
    key,
    workbenchI18nLang || "en",
    params,
    fallback
  );
}

function workbenchLocale(lang) {
  return (lang || workbenchI18nLang) === "zh" ? "zh-CN" : "en-US";
}

function workbenchFormatDate(value, options) {
  if (value === undefined || value === null || value === "") return "";
  try {
    var date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(workbenchLocale(), options || {}).format(date);
  } catch (e) {
    return String(value);
  }
}

function workbenchFormatNumber(value, options) {
  var number = Number(value);
  if (!Number.isFinite(number)) return String(value == null ? "" : value);
  try { return new Intl.NumberFormat(workbenchLocale(), options || {}).format(number); }
  catch (e) { return String(number); }
}

function workbenchToolName(toolName, lang) {
  var raw = String(toolName || "").trim();
  if (!raw) return lang === "zh" ? "工具" : "Tool";
  // Permission events use risk-qualified operation IDs while tool-name
  // translations are keyed by the stable capability ID.
  raw = raw.replace(/\.r[23]$/, "");
  return workbenchTranslateForLang(
    "toolName." + raw,
    lang || workbenchI18nLang || "en",
    null,
    raw
  );
}

function workbenchPermissionQuestionText(pending, lang) {
  var question = pending && typeof pending === "object" ? pending : {};
  var meta = question.meta && typeof question.meta === "object" ? { ...question.meta } : {};
  var rawText = String(question.text || "").trim();
  var operationId = String(meta.operation || meta.tool_name || "").trim();
  var toolId = String(meta.tool_name || operationId).trim();
  if (!operationId && !toolId) {
    return rawText;
  }
  var resolvedLang = lang || workbenchI18nLang || "en";
  var operationName = workbenchToolName(operationId, resolvedLang);
  var localizedToolName = workbenchToolName(toolId, resolvedLang);
  var lines = [
    workbenchTranslateForLang(
      "workbenchChat.permissionRequest",
      resolvedLang,
      { operation: operationName },
      "Agent requests authorization for {operation}."
    ),
    workbenchTranslateForLang(
      "workbenchChat.permissionTool",
      resolvedLang,
      { tool: localizedToolName },
      "Tool: {tool}"
    ),
  ];
  var target = String(meta.path_hint || "").trim();
  if (target && !/^cyrene-(?:setting|lifecycle):/.test(target)) {
    lines.push(workbenchTranslateForLang(
      "workbenchChat.permissionTarget",
      resolvedLang,
      { target: target },
      "Target: {target}"
    ));
  }
  var reason = String(meta.reason || "").trim();
  if (reason) {
    lines.push(workbenchTranslateForLang(
      "workbenchChat.permissionReason",
      resolvedLang,
      { reason: reason },
      "Reason: {reason}"
    ));
  }
  lines.push(workbenchTranslateForLang(
    "workbenchChat.permissionPrompt",
    resolvedLang,
    null,
    "Allow this operation?"
  ));
  return lines.join("\n");
}

function persistWorkbenchRuntimeLanguage(lang) {
  try {
    return fetch("/api/settings/namespaces/runtime", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes: { app_language: lang } }),
    }).catch(function () {});
  } catch (e) {
    return Promise.resolve();
  }
}

function syncWorkbenchDesktopLanguage(lang) {
  try {
    if (!window.cyrene || typeof window.cyrene.updateDesktopSettings !== "function") return Promise.resolve();
    var read = typeof window.cyrene.getDesktopSettings === "function"
      ? window.cyrene.getDesktopSettings()
      : Promise.resolve(null);
    return Promise.resolve(read).then(function (settings) {
      if (settings && settings.language === lang) return;
      __workbenchExpectedDesktopLanguage = lang;
      return window.cyrene.updateDesktopSettings({ language: lang }).catch(function () {
        if (__workbenchExpectedDesktopLanguage === lang) __workbenchExpectedDesktopLanguage = "";
      });
    }).catch(function () {});
  } catch (e) {
    return Promise.resolve();
  }
}

function applyWorkbenchLang(lang, options) {
  if (lang !== "en" && lang !== "zh") return;
  options = options || {};
  var changed = workbenchI18nLang !== lang;
  workbenchI18nLang = lang;
  if (options.persist !== false) {
    try { localStorage.setItem("cyrene-workbench-lang", lang); } catch (e) {}
    syncWorkbenchDesktopLanguage(lang);
    persistWorkbenchRuntimeLanguage(lang);
  }
  if (changed || options.force === true) {
    workbenchI18nVersion += 1;
    __workbenchI18nSubscribers.forEach(function (fn) { fn(workbenchI18nVersion); });
  }
  document.documentElement.dataset.workbenchLang = lang;
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  if (changed || options.force === true) {
    try {
      window.dispatchEvent(new CustomEvent("cyrene:i18n-changed", { detail: { lang: lang } }));
    } catch (e) {}
  }
}

function setWorkbenchLang(lang) {
  applyWorkbenchLang(lang, { persist: true });
}

function useWorkbenchI18n() {
  var vState = React.useState(workbenchI18nVersion);
  var setV = vState[1];
  React.useEffect(function () {
    __workbenchI18nSubscribers.add(setV);
    return function () { __workbenchI18nSubscribers.delete(setV); };
  }, []);
  return {
    t: workbenchT,
    lang: workbenchI18nLang || "en",
    setLang: setWorkbenchLang,
    formatDate: workbenchFormatDate,
    formatNumber: workbenchFormatNumber,
  };
}

(function initWorkbenchLang() {
  var stored = "";
  try { stored = localStorage.getItem("cyrene-workbench-lang"); } catch (e) {}
  if (stored !== "en" && stored !== "zh") {
    try { stored = localStorage.getItem("cyrene-lang"); } catch (e2) {}
  }
  if (stored === "en" || stored === "zh") {
    workbenchI18nLang = stored;
  } else {
    var nav = (navigator.language || "").toLowerCase();
    workbenchI18nLang = nav.indexOf("zh") === 0 ? "zh" : "en";
  }
  document.documentElement.dataset.workbenchLang = workbenchI18nLang;
  document.documentElement.lang = workbenchI18nLang === "zh" ? "zh-CN" : "en";
})();

function registerWorkbenchTranslations(translations) {
  var changed = false;
  ["en", "zh"].forEach(function (lang) {
    if (translations && translations[lang]) {
      Object.assign(WORKBENCH_TRANSLATIONS[lang], translations[lang]);
      changed = true;
    }
  });
  if (changed) {
    workbenchI18nVersion += 1;
    __workbenchI18nSubscribers.forEach(function (fn) { fn(workbenchI18nVersion); });
  }
}

try {
  window.addEventListener("storage", function (event) {
    if (!event || event.key !== "cyrene-workbench-lang") return;
    if (event.newValue === "en" || event.newValue === "zh") {
      applyWorkbenchLang(event.newValue, { persist: false });
    }
  });
} catch (e) {}

try {
  if (window.cyrene && typeof window.cyrene.onDesktopLanguageChanged === "function") {
    window.cyrene.onDesktopLanguageChanged(function (lang) {
      if (lang !== "en" && lang !== "zh") return;
      var expected = __workbenchExpectedDesktopLanguage === lang;
      if (expected) __workbenchExpectedDesktopLanguage = "";
      try { localStorage.setItem("cyrene-workbench-lang", lang); } catch (e) {}
      applyWorkbenchLang(lang, { persist: false });
      if (!expected) persistWorkbenchRuntimeLanguage(lang);
    });
  }
} catch (e) {}

try {
  var workbenchEvents = window.CyreneUI && window.CyreneUI.has("events")
    ? window.CyreneUI.require("events")
    : null;
  if (workbenchEvents && typeof workbenchEvents.subscribe === "function") {
    workbenchEvents.subscribe(function (event) {
      if (!event || event.type !== "settings_changed" || event.namespace !== "runtime") return;
      if (Array.isArray(event.changed) && event.changed.indexOf("app_language") < 0) return;
      syncWorkbenchLangFromRuntime();
    });
  }
} catch (e) {}

function syncWorkbenchLangFromRuntime() {
  try {
    return fetch("/api/settings/namespaces/runtime").then(function (response) {
      return response.ok ? response.json() : null;
    }).then(function (payload) {
      var lang = payload && payload.values && payload.values.app_language;
      if (lang !== "en" && lang !== "zh") return;
      try { localStorage.setItem("cyrene-workbench-lang", lang); } catch (e) {}
      applyWorkbenchLang(lang, { persist: false });
      syncWorkbenchDesktopLanguage(lang);
    }).catch(function () {});
  } catch (e) {
    return Promise.resolve();
  }
}

syncWorkbenchLangFromRuntime();

window.CyreneUI.i18n = window.CyreneUI.register("i18n", {
  translations: WORKBENCH_TRANSLATIONS,
  toolNameAliases: WORKBENCH_TOOL_NAME_ALIASES,
  t: workbenchT,
  tForLang: workbenchTranslateForLang,
  toolName: workbenchToolName,
  permissionQuestionText: workbenchPermissionQuestionText,
  setLang: setWorkbenchLang,
  getLang: function () { return workbenchI18nLang || "en"; },
  getLocale: workbenchLocale,
  formatDate: workbenchFormatDate,
  formatNumber: workbenchFormatNumber,
  getMissingKeys: function () { return Array.from(__workbenchMissingTranslationKeys); },
  use: useWorkbenchI18n,
  registerTranslations: registerWorkbenchTranslations,
});

export { useWorkbenchI18n }
