import { EXTENSION_TRANSLATIONS } from "./shared/i18n/extension-translations.jsx"
import { WORKBENCH_TRANSLATIONS_EN } from "./shared/i18n/catalog-en.jsx"
import { WORKBENCH_TRANSLATIONS_ZH } from "./shared/i18n/catalog-zh.jsx"
import { WORKBENCH_TOOL_NAME_ALIASES } from "./shared/i18n/tool-name-aliases.jsx"

// Workbench-only i18n. This intentionally does not depend on the legacy WebUI
// i18n globals, so the workbench can evolve its language keys independently.
var WORKBENCH_TRANSLATIONS = {
  en: { ...EXTENSION_TRANSLATIONS.en, ...WORKBENCH_TRANSLATIONS_EN },
  zh: { ...EXTENSION_TRANSLATIONS.zh, ...WORKBENCH_TRANSLATIONS_ZH },
};

var workbenchI18nLang = "en";
var workbenchI18nVersion = 0;
var __workbenchI18nSubscribers = new Set();

function workbenchInterpolate(text, params) {
  if (!params) return text;
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
  if (text === undefined) text = (WORKBENCH_TRANSLATIONS.en || {})[resolvedKey];
  if (text === undefined) text = fallback !== undefined ? fallback : key;
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
  var legacyText = String(question.text || "").trim();
  // Questions persisted by older builds contain only the backend-formatted
  // text. Recover its display fields so upgrading the renderer localizes an
  // already-open permission card as well as newly created cards.
  if (!meta.tool_name && legacyText) {
    var legacyTool = legacyText.match(/(?:^|\n)(?:工具|Tool)\s*[：:]\s*([^\n]+)/i);
    if (legacyTool) meta.tool_name = String(legacyTool[1] || "").trim();
  }
  if (!meta.operation && legacyText) {
    var legacyOperation = legacyText.match(/\bcyrene\.[a-z0-9_.-]+(?:\.r[23])?\b/i);
    if (legacyOperation) meta.operation = String(legacyOperation[0] || "").trim();
  }
  if (!meta.path_hint && legacyText) {
    var legacyTarget = legacyText.match(/(?:^|\n)(?:📂\s*)?(?:目标路径|Target)\s*[：:]\s*([^\n]+)/i);
    if (legacyTarget) meta.path_hint = String(legacyTarget[1] || "").trim();
  }
  if (!meta.reason && legacyText) {
    var legacyReason = legacyText.match(/(?:^|\n)(?:💡\s*)?(?:原因|Reason)\s*[：:]\s*([^\n]+)/i);
    if (legacyReason) meta.reason = String(legacyReason[1] || "").trim();
  }
  var operationId = String(meta.operation || meta.tool_name || "").trim();
  var toolId = String(meta.tool_name || operationId).trim();
  // Older persisted questions predate structured public metadata. Preserve
  // their readable text instead of inventing an empty "Tool" field.
  if (!operationId && !toolId) {
    return legacyText;
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

function setWorkbenchLang(lang) {
  if (lang !== "en" && lang !== "zh") return;
  workbenchI18nLang = lang;
  try { localStorage.setItem("cyrene-workbench-lang", lang); } catch (e) {}
  try {
    if (window.cyrene && typeof window.cyrene.updateDesktopSettings === "function") {
      window.cyrene.updateDesktopSettings({ language: lang }).catch(function () {});
    }
  } catch (e) {}
  workbenchI18nVersion += 1;
  __workbenchI18nSubscribers.forEach(function (fn) { fn(workbenchI18nVersion); });
  document.documentElement.dataset.workbenchLang = lang;
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  try {
    window.dispatchEvent(new CustomEvent("cyrene:i18n-changed", { detail: { lang: lang } }));
  } catch (e) {}
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
  ["en", "zh"].forEach(function (lang) {
    if (translations && translations[lang]) {
      Object.assign(WORKBENCH_TRANSLATIONS[lang], translations[lang]);
    }
  });
}

window.CyreneUI.i18n = window.CyreneUI.register("i18n", {
  translations: WORKBENCH_TRANSLATIONS,
  toolNameAliases: WORKBENCH_TOOL_NAME_ALIASES,
  t: workbenchT,
  tForLang: workbenchTranslateForLang,
  toolName: workbenchToolName,
  permissionQuestionText: workbenchPermissionQuestionText,
  setLang: setWorkbenchLang,
  getLang: function () { return workbenchI18nLang || "en"; },
  use: useWorkbenchI18n,
  registerTranslations: registerWorkbenchTranslations,
});

export { useWorkbenchI18n }
