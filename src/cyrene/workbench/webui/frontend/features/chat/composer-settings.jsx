import { useWbcState, useWbcMemo, wbcNormalizePermissionMode } from "../../workbench-chat.jsx"

const WBC_CONTEXT_ACTIVATION_KEYS = ["mcpServers", "skills", "pluginPacks"];

export function wbcNormalizeContextActivations(value) {
  var source = value && typeof value === "object" ? value : {};
  var result = {};
  WBC_CONTEXT_ACTIVATION_KEYS.forEach(function (key) {
    result[key] = Array.from(new Set((Array.isArray(source[key]) ? source[key] : []).map(function (item) {
      return String(item || "").trim();
    }).filter(Boolean)));
  });
  return result;
}

function booleanPreference(chat, key, fallback) {
  return chat && typeof chat[key] === "boolean" ? chat[key] : fallback;
}

export function resolveComposerSettings(chat, capabilities) {
  var settings = {
    mode: wbcNormalizePermissionMode(chat && chat.permissionMode, "auto"),
    soulActive: booleanPreference(chat, "soulActive", true),
    workspaceActive: booleanPreference(chat, "workspaceActive", true),
    shortTermMemoryActive: booleanPreference(chat, "shortTermMemoryActive", true),
    projectMemoryActive: booleanPreference(chat, "projectMemoryActive", true),
    contextActivations: wbcNormalizeContextActivations(chat && chat.contextActivations),
  };
  // Initial mount historically reads saved values before catalog effects run.
  // Chat switches additionally apply the current capability defaults.
  if (capabilities) {
    settings.soulActive = capabilities.soulAvailable && booleanPreference(chat, "soulActive", capabilities.contextOptions.soul.selected === true);
    settings.workspaceActive = capabilities.workspaceAvailable && booleanPreference(chat, "workspaceActive", capabilities.contextOptions.workspace.selected === true);
    if (!capabilities.mcpAvailable) settings.contextActivations.mcpServers = [];
    if (!capabilities.skillsAvailable) settings.contextActivations.skills = [];
    if (!capabilities.pluginPacksAvailable) settings.contextActivations.pluginPacks = [];
  }
  return settings;
}

export function useWbcComposerSettings(chat) {
  var [settings, setSettings] = useWbcState(function () { return resolveComposerSettings(chat); });
  var actions = useWbcMemo(function () {
    function setter(key) {
      return function (update) {
        setSettings(function (current) {
          var value = typeof update === "function" ? update(current[key]) : update;
          return Object.is(value, current[key]) ? current : Object.assign({}, current, { [key]: value });
        });
      };
    }
    return {
      setMode: setter("mode"), setSoulActive: setter("soulActive"),
      setWorkspaceActive: setter("workspaceActive"), setShortTermMemoryActive: setter("shortTermMemoryActive"),
      setProjectMemoryActive: setter("projectMemoryActive"), setContextActivations: setter("contextActivations"),
      restore: function (next) { setSettings(next); },
    };
  }, [setSettings]);
  return { settings, actions };
}
