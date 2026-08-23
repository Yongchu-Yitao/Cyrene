import { workbenchServices } from "./shared/runtime/services.jsx"
// Workbench keyboard-shortcut manager.
//
// Goals:
//   • One source of truth for the global workbench shortcuts (search, new chat,
//     new task, command palette, switch project …) and the composer "send"
//     binding used by the chat / task input boxes.
//   • Platform-aware by default: `mod` resolves to ⌘ on macOS / iOS and Ctrl
//     everywhere else (Windows, Linux, Electron). The active binding is read
//     from the event's metaKey/ctrlKey flags so a Mac user with a Windows
//     keyboard still triggers correctly.
//   • User-customizable: every action's binding can be rebound from the
//     Shortcuts settings tab. The versioned backend namespace is authoritative;
//     `localStorage` under `cyrene-shortcuts` is only the immediate startup and
//     optimistic UI cache.
//   • Display helpers (`describe`, `keysToLabel`) render the same binding the
//     help center and the settings panel show, in the user's OS style.
//
// Registered as the Workbench shortcuts service:
//   { ACTIONS, list, get, set, reset, matches, describe, keysToLabel,
//     isMacPlatform, captureEvent }

(function () {
  var STORAGE_KEY = "cyrene-shortcuts";
  var remoteRevision = 0;
  var remoteWriteQueue = Promise.resolve();
  var localMutationGeneration = 0;
  var pendingRemoteWrites = 0;

  // ---- platform detection -------------------------------------------------

  function isMacPlatform() {
    try {
      var nav = window.navigator || {};
      var uaData = nav.userAgentData;
      if (uaData && uaData.platform) return /mac/i.test(uaData.platform);
      if (nav.platform) return /mac|iphone|ipad|ipod/i.test(nav.platform);
      return /mac|iphone|ipad|ipod/i.test(nav.userAgent || "");
    } catch (e) {
      return false;
    }
  }

  // ---- action catalogue ---------------------------------------------------
  //
  // `keys` is an ordered array of tokens. Recognized tokens:
  //   "mod"   → ⌘ on mac, Ctrl elsewhere (matches metaKey OR ctrlKey in event
  //             tests so both Windows keyboards on a Mac and Mac keyboards on
  //             Windows work)
  //   "shift" → ⇧ / Shift
  //   "alt"   → ⌥ / Alt (option on mac)
  //   "ctrl"  → ⌃ / Ctrl (raw control — use when you want control explicitly,
  //             independent of the platform's "mod" convention)
  //   other   → a literal key, e.g. "K", "Enter", "1"
  //
  // `labelKey` is an i18n key into WORKBENCH_TRANSLATIONS.
  // `group` is used to group entries in the settings panel.
  // `allowRebind` false marks read-only bindings (e.g. the composer Enter to
  // send) that should still be listed in settings but cannot be customized —
  // changing "Enter sends" would surprise too many users.
  var ACTIONS = [
    {
      id: "search",
      labelKey: "shortcut.action.search",
      descKey: "shortcut.action.searchDesc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "K"],
    },
    {
      id: "new-chat",
      labelKey: "shortcut.action.newChat",
      descKey: "shortcut.action.newChatDesc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "N"],
    },
    {
      id: "new-task",
      labelKey: "shortcut.action.newTask",
      descKey: "shortcut.action.newTaskDesc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "T"],
    },
    {
      id: "command-palette",
      labelKey: "shortcut.action.commandPalette",
      descKey: "shortcut.action.commandPaletteDesc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "shift", "P"],
    },
    {
      id: "switch-project",
      labelKey: "shortcut.action.switchProject",
      descKey: "shortcut.action.switchProjectDesc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "shift", "1"],
    },
    {
      id: "switch-session-1",
      labelKey: "shortcut.action.switchSession1",
      descKey: "shortcut.action.switchSession1Desc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "1"],
    },
    {
      id: "switch-session-2",
      labelKey: "shortcut.action.switchSession2",
      descKey: "shortcut.action.switchSession2Desc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "2"],
    },
    {
      id: "switch-session-3",
      labelKey: "shortcut.action.switchSession3",
      descKey: "shortcut.action.switchSession3Desc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "3"],
    },
    {
      id: "next-session",
      labelKey: "shortcut.action.nextSession",
      descKey: "shortcut.action.nextSessionDesc",
      group: "global",
      allowRebind: true,
      keys: ["ctrl", "Tab"],
    },
    {
      id: "previous-session",
      labelKey: "shortcut.action.previousSession",
      descKey: "shortcut.action.previousSessionDesc",
      group: "global",
      allowRebind: true,
      keys: ["ctrl", "shift", "Tab"],
    },
    {
      id: "close-session-tab",
      labelKey: "shortcut.action.closeSessionTab",
      descKey: "shortcut.action.closeSessionTabDesc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "W"],
    },
    {
      id: "toggle-sidebar",
      labelKey: "shortcut.action.toggleSidebar",
      descKey: "shortcut.action.toggleSidebarDesc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "\\"],
    },
    {
      id: "settings",
      labelKey: "shortcut.action.settings",
      descKey: "shortcut.action.settingsDesc",
      group: "global",
      allowRebind: true,
      keys: ["mod", ","],
    },
    {
      id: "voice-command",
      labelKey: "shortcut.action.voiceCommand",
      descKey: "shortcut.action.voiceCommandDesc",
      group: "global",
      allowRebind: true,
      keys: ["mod", "shift", "M"],
    },
    {
      id: "composer-send",
      labelKey: "shortcut.action.composerSend",
      descKey: "shortcut.action.composerSendDesc",
      group: "composer",
      allowRebind: true,
      keys: ["Enter"],
    },
    {
      id: "composer-newline",
      labelKey: "shortcut.action.composerNewline",
      descKey: "shortcut.action.composerNewlineDesc",
      group: "composer",
      allowRebind: true,
      keys: ["shift", "Enter"],
    },
  ];

  // ---- persistence --------------------------------------------------------

  function loadCustom() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      var parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) {
      return {};
    }
  }

  function saveCustomLocal(map) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(map || {})); } catch (e) {}
    // Notify listeners (settings panel) that bindings changed.
    try { window.dispatchEvent(new Event("cyrene-shortcuts-change")); } catch (e) {}
  }

  function readRemote(applyLocal) {
    if (typeof window.fetch !== "function") return Promise.resolve(null);
    return window.fetch("/api/settings/namespaces/shortcuts").then(function (response) {
      if (!response.ok) throw new Error("shortcut_settings_read_failed");
      return response.json();
    }).then(function (payload) {
      remoteRevision = Number(payload.revision || 0);
      var values = payload.values || {};
      var bindings = values.shortcut_bindings;
      if (!bindings || typeof bindings !== "object" || Array.isArray(bindings)) bindings = {};
      if (applyLocal !== false) saveCustomLocal(bindings);
      return bindings;
    }).catch(function () { return null; });
  }

  function persistRemote(patch, retry) {
    if (typeof window.fetch !== "function") return Promise.resolve();
    return window.fetch("/api/settings/namespaces/shortcuts", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        changes: { shortcut_bindings: patch || {} },
        expected_revision: remoteRevision,
      }),
    }).then(function (response) {
      if (response.status === 409 && retry !== false) {
        // Re-read the winning revision but do not overwrite the user's current
        // optimistic UI. Replaying only this action-level patch preserves
        // unrelated Agent/user changes made since our previous read.
        return readRemote(false).then(function () { return persistRemote(patch, false); });
      }
      if (!response.ok) throw new Error("shortcut_settings_update_failed");
      return response.json();
    }).then(function (payload) {
      if (payload) {
        remoteRevision = Number(payload.revision || remoteRevision || 0);
      }
    }).catch(function () { return readRemote(pendingRemoteWrites === 0); });
  }

  function shortcutPatch(before, after, resetEverything) {
    var patch = {};
    ACTIONS.forEach(function (action) {
      var id = action.id;
      var hadBefore = Object.prototype.hasOwnProperty.call(before || {}, id);
      var hasAfter = Object.prototype.hasOwnProperty.call(after || {}, id);
      var beforeKeys = hadBefore ? normalizeKeys(before[id]) : null;
      var afterKeys = hasAfter ? normalizeKeys(after[id]) : null;
      if (resetEverything === true) patch[id] = null;
      else if (JSON.stringify(beforeKeys) !== JSON.stringify(afterKeys)) {
        patch[id] = hasAfter ? afterKeys : null;
      }
    });
    return patch;
  }

  function saveCustom(map, resetEverything) {
    var before = loadCustom();
    var desired = map && typeof map === "object" && !Array.isArray(map) ? map : {};
    var patch = shortcutPatch(before, desired, resetEverything === true);
    saveCustomLocal(desired);
    if (!Object.keys(patch).length) return;
    localMutationGeneration += 1;
    var generation = localMutationGeneration;
    pendingRemoteWrites += 1;
    remoteWriteQueue = remoteWriteQueue.catch(function () {}).then(function () {
      return persistRemote(patch, true);
    }).then(function () {
      pendingRemoteWrites = Math.max(0, pendingRemoteWrites - 1);
      if (generation === localMutationGeneration) return readRemote(true);
      return null;
    });
  }

  // actionId -> normalized keys array (always uppercase literals, tokens from
  // the recognized set). Reads custom bindings on top of defaults.
  function resolveBindings() {
    var custom = loadCustom();
    var map = {};
    ACTIONS.forEach(function (action) {
      var customKeys = custom && custom[action.id];
      map[action.id] = normalizeKeys(customKeys && customKeys.length ? customKeys : action.keys);
    });
    return map;
  }

  function normalizeKeys(keys) {
    if (!Array.isArray(keys) || !keys.length) return [];
    return keys.map(function (token) {
      var t = String(token || "").trim();
      if (!t) return "";
      if (t === "mod" || t === "shift" || t === "alt" || t === "ctrl") return t;
      // Single-char literals get uppercased ("k" → "K"); named keys like
      // "Enter", "Escape" keep their casing so event.key matches work.
      if (t.length === 1) return t.toUpperCase();
      return t;
    }).filter(Boolean);
  }

  // ---- matching -----------------------------------------------------------

  function eventKeyToken(event) {
    var key = String(event.key || "");
    if (!key) return "";
    if (key === "Meta") return "mod";
    if (key === "Control") return "ctrl";
    if (key === "Shift") return "shift";
    if (key === "Alt" || key === "AltGraph") return "alt";
    if (key === " ") return "Space";
    if (key.length === 1) return key.toUpperCase();
    return key;
  }

  // True when `event` satisfies the binding for `actionId`. The `mod` token
  // matches either metaKey (mac) or ctrlKey (windows/linux + windows keyboards
  // on mac), so a user with a non-native keyboard still triggers the shortcut.
  function matches(event, actionId) {
    if (!event) return false;
    var bindings = resolveBindings();
    var keys = bindings[actionId];
    if (!keys || !keys.length) return false;
    // Ignore pure modifier keypresses — we only trigger on a terminal key.
    var token = eventKeyToken(event);
    if (token === "mod" || token === "ctrl" || token === "shift" || token === "alt") return false;
    var wantsMod = keys.indexOf("mod") >= 0;
    var wantsCtrl = keys.indexOf("ctrl") >= 0;
    var wantsShift = keys.indexOf("shift") >= 0;
    var wantsAlt = keys.indexOf("alt") >= 0;
    if (wantsMod && !(event.metaKey || event.ctrlKey)) return false;
    if (!wantsMod && !wantsCtrl && (event.metaKey || event.ctrlKey)) return false;
    if (wantsCtrl && !event.ctrlKey) return false;
    if (!wantsCtrl && wantsMod && event.ctrlKey && !event.metaKey) {
      // On mac, "mod" matches Cmd OR Ctrl — but if the binding explicitly uses
      // "mod" and not "ctrl", a Ctrl-only press is still allowed (Windows
      // keyboard on mac). Don't reject here.
    }
    if (wantsShift && !event.shiftKey) return false;
    if (!wantsShift && event.shiftKey) return false;
    if (wantsAlt && !event.altKey) return false;
    if (!wantsAlt && event.altKey) return false;
    // Compare the terminal key against the non-modifier tokens.
    var literal = keys.filter(function (t) {
      return t !== "mod" && t !== "ctrl" && t !== "shift" && t !== "alt";
    });
    if (literal.length !== 1) return false;
    return token === literal[0];
  }

  // ---- display ------------------------------------------------------------

  function shortcutGlyph(token, isMac) {
    if (token === "mod") return isMac ? "⌘" : "Ctrl";
    if (token === "shift") return isMac ? "⇧" : "Shift";
    if (token === "alt") return isMac ? "⌥" : "Alt";
    if (token === "ctrl") return isMac ? "⌃" : "Ctrl";
    if (token === "Space") return isMac ? "Space" : "Space";
    if (token === "Enter") return isMac ? "⏎" : "Enter";
    if (token === "Escape") return isMac ? "⎋" : "Esc";
    if (token === "Backspace") return isMac ? "⌫" : "Backspace";
    if (token === "\\") return "\\";
    if (token === ",") return ",";
    if (token.length === 1) return token.toUpperCase();
    return token;
  }

  // Returns the keys array (tokens) currently bound to `actionId`.
  function describe(actionId) {
    var bindings = resolveBindings();
    return (bindings[actionId] || []).slice();
  }

  // Renders the binding as a list of glyphs for display, e.g. ["⌘", "K"].
  function keysToLabel(keys, isMac) {
    return (keys || []).map(function (token) { return shortcutGlyph(token, isMac); });
  }

  // ---- capture (settings panel) -------------------------------------------

  // Turns a keyboard event into a normalized keys array suitable for storing.
  // Returns [] for pure-modifier presses so the settings UI can wait for a
  // terminal key. Esc is converted to a sentinel so the panel can cancel.
  function captureEvent(event) {
    if (!event) return { cancelled: false, keys: [] };
    if (event.key === "Escape") return { cancelled: true, keys: [] };
    var tokens = [];
    if (event.metaKey) tokens.push("mod");
    if (event.ctrlKey) {
      // On mac, Ctrl is "ctrl"; on Windows/Linux Ctrl is the platform "mod".
      // We store "mod" so the binding stays portable across OSes when the
      // user rebinds on a Windows machine and later opens the app on a mac.
      if (!isMacPlatform()) {
        if (tokens.indexOf("mod") < 0) tokens.push("mod");
      } else {
        tokens.push("ctrl");
      }
    }
    if (event.shiftKey) tokens.push("shift");
    if (event.altKey) tokens.push("alt");
    var terminal = eventKeyToken(event);
    if (terminal === "mod" || terminal === "ctrl" || terminal === "shift" || terminal === "alt") {
      return { cancelled: false, keys: [] };
    }
    if (!terminal) return { cancelled: false, keys: [] };
    tokens.push(terminal);
    return { cancelled: false, keys: normalizeKeys(tokens) };
  }

  // ---- mutation -----------------------------------------------------------

  function set(actionId, keys) {
    var custom = loadCustom();
    custom[actionId] = normalizeKeys(keys);
    saveCustom(custom);
  }

  function reset(actionId) {
    var custom = loadCustom();
    delete custom[actionId];
    saveCustom(custom);
  }

  function resetAll() {
    saveCustom({}, true);
  }

  function replaceAll(bindings) {
    saveCustom(bindings && typeof bindings === "object" ? bindings : {});
  }

  function list() {
    var custom = loadCustom();
    return ACTIONS.map(function (action) {
      var keys = custom && custom[action.id] && custom[action.id].length
        ? normalizeKeys(custom[action.id])
        : action.keys.slice();
      return {
        id: action.id,
        labelKey: action.labelKey,
        descKey: action.descKey,
        group: action.group,
        allowRebind: action.allowRebind !== false,
        keys: keys,
        isCustom: !!(custom && custom[action.id] && custom[action.id].length),
      };
    });
  }

  function get(actionId) {
    var bindings = resolveBindings();
    return bindings[actionId] || [];
  }

  function isCustom(actionId) {
    var custom = loadCustom();
    return !!(custom && custom[actionId] && custom[actionId].length);
  }

  window.CyreneUI.shortcuts = window.CyreneUI.register("shortcuts", {
    ACTIONS: ACTIONS,
    list: list,
    get: get,
    set: set,
    reset: reset,
    resetAll: resetAll,
    replaceAll: replaceAll,
    reloadRemote: readRemote,
    matches: matches,
    describe: describe,
    keysToLabel: keysToLabel,
    shortcutGlyph: shortcutGlyph,
    isMacPlatform: isMacPlatform,
    captureEvent: captureEvent,
    isCustom: isCustom,
  });

  // Backend settings are authoritative so Agent and UI mutations converge.
  // localStorage remains an immediate startup cache and migration fallback.
  readRemote(true);
  try {
    var events = window.CyreneUI.has("events") ? workbenchServices.events() : null;
    if (events && typeof events.subscribe === "function") {
      events.subscribe(function (event) {
        if (event && event.type === "settings_changed" && event.namespace === "shortcuts") {
          readRemote(pendingRemoteWrites === 0);
        }
      });
    }
  } catch (e) {}
})();
