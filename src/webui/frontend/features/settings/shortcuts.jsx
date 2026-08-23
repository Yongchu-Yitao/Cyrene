import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  showSettingsToast,
  SectionTitle,
  SectionBlock,
} from "./shared.jsx"

// ── Shortcuts Panel ──
function ShortcutsPanel(p) {
  var t = p.t;
  var sc = workbenchServices.shortcuts();
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

export { ShortcutsPanel };
