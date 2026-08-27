import {
  useStateSt,
  useEffectSt,
  useRefSt,
  readSettingsResponse,
  settingsFetch,
  SectionTitle,
  FieldRow,
  Toggle,
} from "./shared.jsx"

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

export { AppearancePanel };
