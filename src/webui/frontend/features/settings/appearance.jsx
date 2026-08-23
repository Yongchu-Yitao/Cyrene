import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  useRefSt,
  readSettingsResponse,
  settingsFetch,
  ExternalChevron,
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
    return workbenchServices.events().subscribe(function (event) {
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

export { AppearancePanel, CustomToolsPanel };
