import {
  useStateSt,
  useEffectSt,
  useRefSt,
  readSettingsResponse,
  settingsFetch,
  showSettingsToast,
  SectionTitle,
  SectionBlock,
  FieldRow,
  Toggle,
} from "./shared.jsx"

// ComfyUI remains supported by the backend for existing installations, but is
// intentionally absent from the user-facing settings until that integration is
// ready to be configured safely from this screen.
var MEDIA_PROVIDER_ORDER = ["openai", "seedream", "seedance", "minimax", "google"];

var MEDIA_PROVIDER_META = {
  openai: {
    name: "OpenAI · GPT Image",
    hintKey: "settings.mediaProviderHint.openai",
    capabilityKey: "settings.mediaCapabilitiesImage",
    requiresApiKey: true,
  },
  seedream: {
    name: "Seedream",
    hintKey: "settings.mediaProviderHint.seedream",
    capabilityKey: "settings.mediaCapabilitiesImage",
    requiresApiKey: true,
  },
  seedance: {
    name: "Seedance",
    hintKey: "settings.mediaProviderHint.seedance",
    capabilityKey: "settings.mediaCapabilitiesVideo",
    requiresApiKey: true,
  },
  minimax: {
    name: "MiniMax",
    hintKey: "settings.mediaProviderHint.minimax",
    capabilityKey: "settings.mediaCapabilitiesVideoMusic",
    requiresApiKey: true,
  },
  google: {
    name: "Google Gemini / Veo",
    hintKey: "settings.mediaProviderHint.google",
    capabilityKey: "settings.mediaCapabilitiesImageVideo",
    requiresApiKey: true,
  },
};

var MEDIA_PROVIDER_DEFAULTS = {
  openai: {
    enabled: false,
    base_url: "https://api.openai.com/v1",
    image_model: "gpt-image-2",
    api_key_configured: false,
  },
  seedream: {
    enabled: false,
    base_url: "https://ark.cn-beijing.volces.com/api/v3",
    image_model: "doubao-seedream-5-0-260128",
    api_key_configured: false,
  },
  seedance: {
    enabled: false,
    base_url: "https://ark.cn-beijing.volces.com/api/v3",
    video_model: "doubao-seedance-2-0-260128",
    api_key_configured: false,
  },
  minimax: {
    enabled: false,
    base_url: "https://api.minimax.io",
    video_model: "MiniMax-H3",
    music_model: "music-3.0",
    api_key_configured: false,
  },
  google: {
    enabled: false,
    image_model: "gemini-3.1-flash-image",
    video_model: "gemini-omni-flash-preview",
    api_key_configured: false,
  },
};

var MEDIA_PROVIDER_FIELDS = {
  openai: [
    { name: "image_model", kind: "image", labelKey: "settings.mediaImageModel", hintKey: "settings.mediaModelHint", placeholder: "gpt-image-2" },
    { name: "base_url", labelKey: "settings.mediaBaseUrl", hintKey: "settings.mediaBaseUrlHint", placeholder: "https://api.openai.com/v1", advanced: true },
  ],
  seedream: [
    { name: "image_model", kind: "image", labelKey: "settings.mediaImageModel", hintKey: "settings.mediaModelHint", placeholder: "doubao-seedream-5-0-260128" },
    { name: "base_url", labelKey: "settings.mediaBaseUrl", hintKey: "settings.mediaBaseUrlHint", placeholder: "https://ark.cn-beijing.volces.com/api/v3", advanced: true },
  ],
  seedance: [
    { name: "video_model", kind: "video", labelKey: "settings.mediaVideoModel", hintKey: "settings.mediaModelHint", placeholder: "doubao-seedance-2-0-260128" },
    { name: "base_url", labelKey: "settings.mediaBaseUrl", hintKey: "settings.mediaBaseUrlHint", placeholder: "https://ark.cn-beijing.volces.com/api/v3", advanced: true },
  ],
  minimax: [
    { name: "video_model", kind: "video", labelKey: "settings.mediaVideoModel", hintKey: "settings.mediaModelHint", placeholder: "MiniMax-H3" },
    { name: "music_model", kind: "music", labelKey: "settings.mediaMusicModel", hintKey: "settings.mediaModelHint", placeholder: "music-3.0" },
    { name: "base_url", labelKey: "settings.mediaBaseUrl", hintKey: "settings.mediaBaseUrlHint", placeholder: "https://api.minimax.io", advanced: true },
  ],
  google: [
    { name: "image_model", kind: "image", labelKey: "settings.mediaImageModel", hintKey: "settings.mediaModelHint", placeholder: "gemini-3.1-flash-image" },
    { name: "video_model", kind: "video", labelKey: "settings.mediaVideoModel", hintKey: "settings.mediaModelHint", placeholder: "gemini-omni-flash-preview" },
  ],
};

var MEDIA_CUSTOM_MODEL_VALUE = "__cyrene_custom_model__";

var MEDIA_KIND_PROVIDERS = {
  image: ["openai", "seedream", "google"],
  video: ["seedance", "minimax", "google"],
  music: ["minimax"],
};

function normalizedDefaultProvider(kind, value) {
  var provider = String(value || "auto").trim().toLowerCase() || "auto";
  return provider === "auto" || MEDIA_KIND_PROVIDERS[kind].indexOf(provider) >= 0
    ? provider
    : "auto";
}

function persistedDefaultProvider(kind, value) {
  var provider = String(value || "auto").trim().toLowerCase() || "auto";
  // Keep a legacy hidden selection intact until the user explicitly picks a
  // visible replacement. This prevents an unrelated autosave from silently
  // changing an existing ComfyUI route.
  return provider === "comfyui" ? provider : normalizedDefaultProvider(kind, provider);
}

function boundedNumber(value, fallback, minimum, maximum) {
  var next = Number(value);
  if (!Number.isFinite(next)) return fallback;
  return Math.min(maximum, Math.max(minimum, next));
}

function normalizeMediaSettings(payload) {
  var raw = payload && typeof payload === "object" ? payload : {};
  var rawProviders = raw.providers && typeof raw.providers === "object" ? raw.providers : {};
  var providers = {};
  MEDIA_PROVIDER_ORDER.forEach(function (id) {
    var source = rawProviders[id] && typeof rawProviders[id] === "object" ? rawProviders[id] : {};
    var normalized = {
      ...MEDIA_PROVIDER_DEFAULTS[id],
      ...source,
      enabled: source.enabled === true,
      api_key_configured: source.api_key_configured === true,
    };
    delete normalized.api_key;
    delete normalized.clear_api_key;
    providers[id] = normalized;
  });
  var defaults = raw.default_providers && typeof raw.default_providers === "object"
    ? raw.default_providers
    : {};
  return {
    version: raw.version == null ? 1 : raw.version,
    revision: Number.isInteger(raw.revision) ? raw.revision : null,
    max_parallel_jobs: boundedNumber(raw.max_parallel_jobs, 3, 1, 8),
    max_attempts: boundedNumber(raw.max_attempts, 2, 1, 5),
    poll_interval_seconds: boundedNumber(raw.poll_interval_seconds, 3, 1, 30),
    max_download_mb: boundedNumber(raw.max_download_mb, 256, 10, 1024),
    default_providers: {
      image: persistedDefaultProvider("image", defaults.image),
      video: persistedDefaultProvider("video", defaults.video),
      music: persistedDefaultProvider("music", defaults.music),
    },
    providers: providers,
    completion_behavior: "attach_then_wake_agent",
  };
}

function mediaSavePayload(settings, draftKeys, clearKeys, expectedRevision) {
  var providers = {};
  MEDIA_PROVIDER_ORDER.forEach(function (id) {
    var source = settings.providers[id] || {};
    var output = {};
    Object.keys(source).forEach(function (key) {
      if (key !== "api_key" && key !== "api_key_configured" && key !== "clear_api_key") output[key] = source[key];
    });
    var apiKey = String(draftKeys[id] || "").trim();
    if (apiKey) output.api_key = apiKey;
    if (clearKeys[id] === true) output.clear_api_key = true;
    providers[id] = output;
  });
  return {
    version: settings.version,
    expected_revision: Number.isInteger(expectedRevision)
      ? expectedRevision
      : Number.isInteger(settings.revision) ? settings.revision : undefined,
    max_parallel_jobs: boundedNumber(settings.max_parallel_jobs, 3, 1, 8),
    max_attempts: boundedNumber(settings.max_attempts, 2, 1, 5),
    poll_interval_seconds: boundedNumber(settings.poll_interval_seconds, 3, 1, 30),
    max_download_mb: boundedNumber(settings.max_download_mb, 256, 10, 1024),
    default_providers: { ...settings.default_providers },
    providers: providers,
  };
}

function MediaNumberInput(p) {
  return React.createElement("input", {
    className: "wb-input mono",
    type: "number",
    min: String(p.min),
    max: String(p.max),
    step: String(p.step || 1),
    value: p.value,
    "aria-label": p.label,
    onChange: function (event) { p.onChange(event.target.value); },
  });
}

function MediaDefaultProviderSelect(p) {
  var legacySelection = String(p.value || "").toLowerCase() === "comfyui";
  var options = [React.createElement("option", { value: "auto", key: "auto" }, p.t("settings.mediaProviderAuto"))];
  if (legacySelection) {
    options.unshift(React.createElement("option", { value: "comfyui", key: "legacy" }, p.t("settings.mediaProviderLegacy")));
  }
  return React.createElement("select", {
    className: "wb-select",
    value: legacySelection ? "comfyui" : normalizedDefaultProvider(p.kind, p.value),
    "aria-label": p.label,
    onChange: function (event) { p.onChange(event.target.value); },
  }, options.concat(MEDIA_KIND_PROVIDERS[p.kind].map(function (id) {
      return React.createElement("option", { value: id, key: id }, MEDIA_PROVIDER_META[id].name);
    })));
}

function mediaModelEntries(catalog, kind, currentValue) {
  var current = String(currentValue || "").trim();
  var rawModels = catalog && catalog.models;
  var source = Array.isArray(rawModels)
    ? rawModels.filter(function (item) {
        return item && Array.isArray(item.kinds) && item.kinds.indexOf(kind) >= 0;
      })
    : rawModels && Array.isArray(rawModels[kind]) ? rawModels[kind] : [];
  var seen = {};
  var entries = [];
  source.forEach(function (raw) {
    var item = typeof raw === "string" ? { id: raw } : (raw || {});
    var id = String(item.id || item.model || "").trim();
    if (!id || seen[id]) return;
    seen[id] = true;
    var itemSource = item.configured === true && item.available === false
      ? "configured"
      : String(item.source || (item.configured === true ? "configured" : "catalog"));
    entries.push({
      id: id,
      name: String(item.name || item.label || id).trim() || id,
      source: itemSource,
      recommended: item.recommended === true,
      accountAvailable: item.account_available === true
        || item.available === true
        || item.verified === true
        || itemSource === "provider"
        || itemSource === "live",
    });
  });
  if (current && !seen[current]) {
    entries.unshift({
      id: current,
      name: current,
      source: catalog ? "configured" : "current",
      recommended: false,
      accountAvailable: false,
    });
  }
  return entries;
}

function mediaModelOption(item, t) {
  var suffix = "";
  if (item.source === "configured") suffix = " · " + t("settings.mediaModelCurrentUnavailable");
  else if (item.accountAvailable) suffix = " · " + t("settings.mediaModelAccountAvailable");
  else if (item.recommended) suffix = " · " + t("settings.mediaModelRecommended");
  var label = item.name === item.id ? item.id : item.name + " · " + item.id;
  return React.createElement("option", { value: item.id, key: item.id }, label + suffix);
}

function MediaModelSelect(p) {
  var current = String(p.value || "").trim();
  var entries = mediaModelEntries(p.catalog, p.kind, current);
  var configuredEntries = entries.filter(function (item) {
    return item.source === "configured" || item.source === "current";
  });
  var accountEntries = entries.filter(function (item) { return item.accountAvailable; });
  var otherEntries = entries.filter(function (item) {
    return !item.accountAvailable && item.source !== "configured" && item.source !== "current";
  });
  var options = configuredEntries.map(function (item) { return mediaModelOption(item, p.t); });
  if (accountEntries.length) {
    options.push(React.createElement("optgroup", {
      label: p.t("settings.mediaModelAccountAvailable"),
      key: "account",
    }, accountEntries.map(function (item) { return mediaModelOption(item, p.t); })));
  }
  if (otherEntries.length) {
    options.push(React.createElement("optgroup", {
      label: p.t("settings.mediaModelCommon"),
      key: "catalog",
    }, otherEntries.map(function (item) { return mediaModelOption(item, p.t); })));
  }
  options.push(React.createElement("option", {
    value: MEDIA_CUSTOM_MODEL_VALUE,
    key: MEDIA_CUSTOM_MODEL_VALUE,
  }, p.t("settings.mediaModelCustom")));

  var statusKey = "";
  if (p.loadState && p.loadState.loading) statusKey = "settings.mediaModelsLoading";
  else if (p.catalog && p.catalog.status === "verified") statusKey = "settings.mediaModelStatusVerified";
  else if (p.catalog && p.catalog.status === "missing_key") statusKey = "settings.mediaModelStatusMissingKey";
  else if (p.catalog && p.catalog.status === "failed") statusKey = "settings.mediaModelStatusFailed";
  else if (p.catalog) statusKey = "settings.mediaModelStatusCatalogOnly";
  else if (p.loadState && p.loadState.failed) statusKey = "settings.mediaModelStatusFailed";

  return React.createElement("div", { className: "wb-media-model-control" },
    React.createElement("select", {
      className: "wb-select mono",
      value: p.customMode ? MEDIA_CUSTOM_MODEL_VALUE : current,
      "aria-label": p.label,
      onFocus: function () { p.loadModels(false); },
      onChange: function (event) {
        var value = event.target.value;
        if (value === MEDIA_CUSTOM_MODEL_VALUE) {
          p.setCustomMode(true);
          return;
        }
        p.setCustomMode(false);
        p.onChange(value);
      },
    }, options),
    p.customMode && React.createElement("input", {
      className: "wb-input mono wb-media-custom-model",
      type: "text",
      value: current,
      placeholder: p.placeholder || "",
      autoComplete: "off",
      "aria-label": p.t("settings.mediaModelCustomHint"),
      onChange: function (event) { p.onChange(event.target.value); },
    }),
    React.createElement("div", { className: "wb-media-model-meta", "aria-live": "polite" },
      React.createElement("small", null, statusKey ? p.t(statusKey) : ""),
      React.createElement("button", {
        type: "button",
        className: "wb-media-model-refresh",
        disabled: !!(p.loadState && p.loadState.loading),
        onClick: function () { p.loadModels(true); },
      }, p.t("settings.mediaModelsRefresh"))));
}

function MediaProviderField(p) {
  var provider = p.provider;
  var field = p.field;
  var control = field.kind
    ? MediaModelSelect({
        t: p.t,
        label: p.t(field.labelKey),
        kind: field.kind,
        value: provider[field.name] || "",
        placeholder: field.placeholder,
        catalog: p.catalog,
        loadState: p.loadState,
        customMode: p.customMode === true,
        setCustomMode: function (enabled) { p.setCustomMode(field.name, enabled); },
        loadModels: p.loadModels,
        onChange: function (value) { p.onChange(field.name, value); },
      })
    : React.createElement("input", {
        className: "wb-input mono",
        type: field.name === "base_url" ? "url" : "text",
        value: provider[field.name] || "",
        placeholder: field.placeholder || "",
        autoComplete: field.name === "base_url" ? "url" : "off",
        "aria-label": p.t(field.labelKey),
        onChange: function (event) { p.onChange(field.name, event.target.value); },
      });
  return FieldRow(p.t(field.labelKey), p.t(field.hintKey), control, field.name);
}

function MediaApiKeyField(p) {
  var configured = p.provider.api_key_configured === true;
  var clearing = p.clearKey === true;
  var placeholder = clearing
    ? p.t("settings.mediaKeyWillBeCleared")
    : configured ? p.t("settings.secretConfigured") : p.t("settings.mediaApiKeyPlaceholder");
  return FieldRow(p.t("settings.apiKey"), p.t("settings.mediaApiKeyHint"),
    React.createElement("div", { className: "wb-integration-control wb-integration-key" },
      React.createElement("input", {
        className: "wb-input mono",
        type: "password",
        value: p.draftKey || "",
        placeholder: placeholder,
        autoComplete: "new-password",
        "aria-label": p.providerName + " " + p.t("settings.apiKey"),
        "data-cyrene-agent-secret-input": "true",
        "data-cyrene-risk": "R3",
        onChange: function (event) { p.onDraftKey(event.target.value); },
      }),
      (configured || clearing) && React.createElement("button", {
        type: "button",
        className: "wb-btn muted",
        disabled: p.busy,
        onClick: clearing ? p.undoClearKey : p.clearStoredKey,
      }, p.t(clearing ? "settings.mediaUndoClearKey" : "settings.clearStoredKey"))),
    "api_key",
  );
}

function mediaProviderStatus(provider, draftKey, clearKey, t) {
  var hasKey = clearKey !== true
    && (provider.api_key_configured === true || String(draftKey || "").trim().length > 0);
  if (provider.enabled && hasKey) return { key: "ready", label: t("settings.mediaServiceReady") };
  if (provider.enabled) return { key: "warning", label: t("settings.mediaServiceNeedsKey") };
  if (hasKey) return { key: "connected", label: t("settings.mediaServiceConnectedOff") };
  return { key: "off", label: t("settings.mediaServiceNotConfigured") };
}

function MediaProviderSummary(p) {
  var status = mediaProviderStatus(p.provider, p.draftKey, p.clearKey, p.t);
  return React.createElement("button", { type: "button", className: "wb-media-provider-summary" },
    React.createElement("span", { className: "wb-media-provider-copy" },
      React.createElement("strong", null, p.meta.name),
      React.createElement("small", null, p.t(p.meta.hintKey))),
    React.createElement("span", { className: "wb-media-provider-meta" },
      React.createElement("span", { className: "wb-media-capability" }, p.t(p.meta.capabilityKey)),
      React.createElement("span", {
        className: "wb-media-service-state is-" + status.key,
        role: "status",
      }, status.label)));
}

function MediaDisclosure(p) {
  var _open = React.useState(p.defaultOpen === true);
  var open = _open[0];
  var setOpen = _open[1];
  var toggle = function () {
    var next = !open;
    setOpen(next);
    if (next && p.onOpen) p.onOpen();
  };
  var summary = React.cloneElement(p.summary, {
    "aria-controls": p.panelId,
    "aria-expanded": open,
    onClick: toggle,
  });
  return React.createElement("div", {
    id: p.id,
    className: p.className + (open ? " is-open" : ""),
  },
    summary,
    React.createElement("div", {
      id: p.panelId,
      className: p.bodyClassName + (open ? " open" : ""),
      "aria-hidden": !open,
      inert: open ? undefined : "",
    }, React.createElement("div", { className: p.innerClassName },
      React.createElement("div", { className: p.contentClassName }, p.children))));
}

function MediaProviderCard(p) {
  var meta = MEDIA_PROVIDER_META[p.id];
  var provider = p.settings.providers[p.id];
  var fields = MEDIA_PROVIDER_FIELDS[p.id] || [];
  var primaryFields = fields.filter(function (field) { return field.advanced !== true; });
  var advancedFields = fields.filter(function (field) { return field.advanced === true; });
  var rows = [
    FieldRow(
      p.t("settings.mediaProviderEnabled"),
      p.t("settings.mediaProviderEnabledHint"),
      Toggle(provider.enabled, function () { p.updateProvider(p.id, "enabled", !provider.enabled); }, false, meta.name),
      "enabled",
    ),
  ];
  if (meta.requiresApiKey) {
    rows.push(MediaApiKeyField({
      t: p.t,
      provider: provider,
      providerName: meta.name,
      draftKey: p.draftKeys[p.id],
      clearKey: p.clearKeys[p.id],
      busy: p.saving,
      onDraftKey: function (value) { p.updateDraftKey(p.id, value); },
      clearStoredKey: function () { p.markKeyForClear(p.id); },
      undoClearKey: function () { p.undoKeyClear(p.id); },
    }));
  }
  rows = rows.concat(primaryFields.map(function (field) {
    return MediaProviderField({
      t: p.t,
      provider: provider,
      field: field,
      catalog: p.modelCatalogs[p.id],
      loadState: p.modelLoadStates[p.id],
      customMode: p.modelCustomModes[p.id + ":" + field.name],
      setCustomMode: function (name, enabled) { p.setModelCustomMode(p.id, name, enabled); },
      loadModels: function (force) { p.loadProviderModels(p.id, force); },
      onChange: function (name, value) { p.updateProvider(p.id, name, value); },
    });
  }));
  if (advancedFields.length) {
    rows.push(React.createElement(MediaDisclosure, {
      id: "setting-media-" + p.id + "-advanced",
      panelId: "setting-media-" + p.id + "-advanced-panel",
      className: "wb-media-advanced",
      bodyClassName: "wb-media-advanced-body",
      innerClassName: "wb-media-advanced-body-inner",
      contentClassName: "wb-media-advanced-body-content",
      key: "advanced",
      summary: React.createElement("button", { type: "button", className: "wb-media-advanced-summary" },
        React.createElement("strong", null, p.t("settings.mediaAdvancedProvider")),
        React.createElement("small", null, p.t("settings.mediaAdvancedProviderHint"))),
    }, advancedFields.map(function (field) {
      return MediaProviderField({
        t: p.t,
        provider: provider,
        field: field,
        catalog: p.modelCatalogs[p.id],
        loadState: p.modelLoadStates[p.id],
        customMode: p.modelCustomModes[p.id + ":" + field.name],
        setCustomMode: function (name, enabled) { p.setModelCustomMode(p.id, name, enabled); },
        loadModels: function (force) { p.loadProviderModels(p.id, force); },
        onChange: function (name, value) { p.updateProvider(p.id, name, value); },
      });
    })));
  }
  var shouldStartOpen = provider.enabled === true
    || provider.api_key_configured === true
    || String(p.draftKeys[p.id] || "").trim().length > 0;
  return React.createElement(MediaDisclosure, {
    id: "setting-media-" + p.id,
    panelId: "setting-media-" + p.id + "-panel",
    className: "wb-media-provider",
    key: p.id,
    defaultOpen: shouldStartOpen,
    onOpen: function () { p.loadProviderModels(p.id, false); },
    bodyClassName: "wb-media-provider-body",
    innerClassName: "wb-media-provider-body-inner",
    contentClassName: "wb-media-provider-body-content",
    summary: MediaProviderSummary({
      t: p.t,
      meta: meta,
      provider: provider,
      draftKey: p.draftKeys[p.id],
      clearKey: p.clearKeys[p.id],
    }),
  }, rows);
}

function MediaRuntimeSection(p) {
  return React.createElement(MediaDisclosure, {
    id: "setting-media-runtime",
    panelId: "setting-media-runtime-panel",
    className: "wb-media-runtime",
    bodyClassName: "wb-media-runtime-body",
    innerClassName: "wb-media-runtime-body-inner",
    contentClassName: "wb-media-runtime-body-content",
    summary: React.createElement("button", { type: "button", className: "wb-media-runtime-summary" },
      React.createElement("strong", null, p.t("settings.mediaRuntime")),
      React.createElement("small", null, p.t("settings.mediaRuntimeHint"))),
  },
    FieldRow(p.t("settings.mediaMaxParallelJobs"), p.t("settings.mediaMaxParallelJobsHint"),
      MediaNumberInput({ label: p.t("settings.mediaMaxParallelJobs"), value: p.settings.max_parallel_jobs, min: 1, max: 8, onChange: function (value) { p.updateRoot("max_parallel_jobs", value); } })),
    FieldRow(p.t("settings.mediaMaxAttempts"), p.t("settings.mediaMaxAttemptsHint"),
      MediaNumberInput({ label: p.t("settings.mediaMaxAttempts"), value: p.settings.max_attempts, min: 1, max: 5, onChange: function (value) { p.updateRoot("max_attempts", value); } })),
    FieldRow(p.t("settings.mediaPollInterval"), p.t("settings.mediaPollIntervalHint"),
      MediaNumberInput({ label: p.t("settings.mediaPollInterval"), value: p.settings.poll_interval_seconds, min: 1, max: 30, step: 0.5, onChange: function (value) { p.updateRoot("poll_interval_seconds", value); } })),
    FieldRow(p.t("settings.mediaMaxDownload"), p.t("settings.mediaMaxDownloadHint"),
      MediaNumberInput({ label: p.t("settings.mediaMaxDownload"), value: p.settings.max_download_mb, min: 10, max: 1024, onChange: function (value) { p.updateRoot("max_download_mb", value); } })));
}

function MediaDefaultsSection(p) {
  function card(kind, labelKey, hintKey) {
    return React.createElement("label", { className: "wb-media-default-card", key: kind },
      React.createElement("span", { className: "wb-media-default-copy" },
        React.createElement("strong", null, p.t(labelKey)),
        React.createElement("small", null, p.t(hintKey))),
      MediaDefaultProviderSelect({
        t: p.t,
        kind: kind,
        label: p.t(labelKey),
        value: p.settings.default_providers[kind],
        onChange: function (value) { p.updateDefault(kind, value); },
      }));
  }
  return React.createElement("div", { id: "setting-media-defaults" },
    SectionBlock(p.t("settings.mediaDefaultProviders"), p.t("settings.mediaDefaultProvidersHint"),
      React.createElement("div", { className: "wb-media-default-grid" },
        card("image", "settings.mediaDefaultImage", "settings.mediaDefaultImageHint"),
        card("video", "settings.mediaDefaultVideo", "settings.mediaDefaultVideoHint"),
        card("music", "settings.mediaDefaultMusic", "settings.mediaDefaultMusicHint"))));
}

function scheduleMediaSave(queue, delay) {
  if (!queue.mounted || !queue.available) return;
  if (queue.timer) clearTimeout(queue.timer);
  queue.timer = null;
  if (queue.inFlight || !queue.dirty || !queue.snapshot || queue.blockedVersion === queue.version) return;
  queue.timer = setTimeout(function () {
    queue.timer = null;
    persistMediaSave(queue);
  }, Math.max(0, Number(delay) || 0));
}

function failMediaSave(queue, error, failedVersion) {
  if (!queue.mounted) return;
  if (queue.version !== failedVersion) {
    scheduleMediaSave(queue, 0);
    return;
  }
  queue.blockedVersion = failedVersion;
  queue.setDirty(true);
  queue.setConflict(error.status === 409);
  var message = queue.t(error.status === 409 ? "settings.mediaConflict" : "settings.mediaSaveFailed")
    + ": " + (error.message || "");
  queue.setSaveError(message);
  showSettingsToast(message, "error");
}

function persistMediaSave(queue, background) {
  if (!queue.available || (!queue.mounted && !background) || !queue.dirty || !queue.snapshot || queue.blockedVersion === queue.version) return;
  if (queue.inFlight) {
    if (background && queue.version !== queue.activeVersion) queue.flushAfterFlight = true;
    return;
  }
  var snapshot = queue.snapshot;
  var version = queue.version;
  queue.inFlight = true;
  var controller = new AbortController();
  queue.requestController = controller;
  queue.activeVersion = version;
  queue.flushAfterFlight = false;
  if (queue.mounted) queue.setSaving(true);
  settingsFetch("/api/settings/media", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    signal: controller.signal,
    body: JSON.stringify(mediaSavePayload(
      snapshot.settings,
      snapshot.draftKeys,
      snapshot.clearKeys,
      queue.revision,
    )),
  }).then(readSettingsResponse).then(function (payload) {
    queue.inFlight = false;
    if (Number.isInteger(payload.revision)) queue.revision = payload.revision;
    if (queue.version === version) {
      queue.dirty = false;
      if (queue.mounted) queue.acceptSaved(payload);
      return;
    }
    queue.snapshot = {
      ...queue.snapshot,
      settings: { ...queue.snapshot.settings, revision: queue.revision },
    };
    if (queue.mounted) scheduleMediaSave(queue, 0);
    else persistMediaSave(queue, true);
  }).catch(function (error) {
    queue.inFlight = false;
    if (!(error && error.name === "AbortError")) failMediaSave(queue, error, version);
  }).finally(function () {
    if (queue.requestController === controller) queue.requestController = null;
    if (queue.mounted) {
      queue.setSaving(false);
      return;
    }
    if (queue.flushAfterFlight && queue.dirty) persistMediaSave(queue, true);
  });
}

function createMediaSettingsActions(store) {
  function queue(nextSettings, nextDraftKeys, nextClearKeys, immediate) {
    store.settingsRef.current = nextSettings;
    store.draftKeysRef.current = nextDraftKeys;
    store.clearKeysRef.current = nextClearKeys;
    store.saveQueue.version += 1;
    store.saveQueue.dirty = true;
    store.saveQueue.snapshot = {
      settings: nextSettings,
      draftKeys: { ...nextDraftKeys },
      clearKeys: { ...nextClearKeys },
    };
    store.saveQueue.blockedVersion = -1;
    store.setSettings(nextSettings);
    store.setDraftKeys(nextDraftKeys);
    store.setClearKeys(nextClearKeys);
    store.setDirty(true);
    store.setSaveError("");
    store.setConflict(false);
    scheduleMediaSave(store.saveQueue, immediate ? 0 : 600);
  }
  return {
    updateRoot: function (name, value) {
      queue(
        { ...store.settingsRef.current, [name]: value },
        store.draftKeysRef.current,
        store.clearKeysRef.current,
      );
    },
    updateDefault: function (kind, value) {
      var current = store.settingsRef.current;
      queue({
        ...current,
        default_providers: { ...current.default_providers, [kind]: normalizedDefaultProvider(kind, value) },
      }, store.draftKeysRef.current, store.clearKeysRef.current);
    },
    updateProvider: function (id, name, value) {
      var current = store.settingsRef.current;
      queue({
        ...current,
        providers: {
          ...current.providers,
          [id]: { ...current.providers[id], [name]: value },
        },
      }, store.draftKeysRef.current, store.clearKeysRef.current);
    },
    updateDraftKey: function (id, value) {
      queue(
        store.settingsRef.current,
        { ...store.draftKeysRef.current, [id]: value },
        { ...store.clearKeysRef.current, [id]: false },
      );
    },
    markKeyForClear: function (id) {
      queue(
        store.settingsRef.current,
        { ...store.draftKeysRef.current, [id]: "" },
        { ...store.clearKeysRef.current, [id]: true },
      );
    },
    undoKeyClear: function (id) {
      queue(
        store.settingsRef.current,
        store.draftKeysRef.current,
        { ...store.clearKeysRef.current, [id]: false },
      );
    },
    retrySave: function () {
      if (store.saveQueue.inFlight) return;
      store.saveQueue.blockedVersion = -1;
      store.setSaveError("");
      store.setConflict(false);
      scheduleMediaSave(store.saveQueue, 0);
    },
    reloadLatest: function () {
      if (!window.confirm(store.t("settings.mediaConflictReload"))) return;
      store.load();
    },
  };
}

function useMediaConfiguration(t, available) {
  var [settings, setSettings] = useStateSt(function () { return normalizeMediaSettings({}); });
  var [draftKeys, setDraftKeys] = useStateSt({});
  var [clearKeys, setClearKeys] = useStateSt({});
  var [loading, setLoading] = useStateSt(true);
  var [saving, setSaving] = useStateSt(false);
  var [dirty, setDirty] = useStateSt(false);
  var [loadError, setLoadError] = useStateSt("");
  var [saveError, setSaveError] = useStateSt("");
  var [conflict, setConflict] = useStateSt(false);
  var [loadFailed, setLoadFailed] = useStateSt(false);
  var [modelCatalogs, setModelCatalogs] = useStateSt({});
  var [modelLoadStates, setModelLoadStates] = useStateSt({});
  var [modelCustomModes, setModelCustomModes] = useStateSt({});
  var settingsRef = useRefSt(settings);
  var savedSettingsRef = useRefSt(null);
  var draftKeysRef = useRefSt(draftKeys);
  var clearKeysRef = useRefSt(clearKeys);
  var modelCatalogsRef = useRefSt({});
  var modelRequestsRef = useRefSt({});
  var requestControllersRef = useRefSt(new Set());
  var saveQueueRef = useRefSt(null);
  if (!saveQueueRef.current) saveQueueRef.current = {
    mounted: true,
    timer: null,
    inFlight: false,
    activeVersion: -1,
    dirty: false,
    flushAfterFlight: false,
    blockedVersion: -1,
    version: 0,
    revision: null,
    snapshot: null,
  };
  var saveQueue = saveQueueRef.current;
  saveQueue.available = available !== false;

  function loadProviderModels(id, force) {
    if (!saveQueue.available) return Promise.resolve(null);
    if (MEDIA_PROVIDER_ORDER.indexOf(id) < 0) return Promise.resolve(null);
    if (modelRequestsRef.current[id]) return modelRequestsRef.current[id];
    if (!force && modelCatalogsRef.current[id]) return Promise.resolve(modelCatalogsRef.current[id]);
    setModelLoadStates(function (current) {
      return { ...current, [id]: { loading: true, failed: false } };
    });
    var controller = new AbortController();
    requestControllersRef.current.add(controller);
    var request = settingsFetch(
      "/api/settings/media/providers/" + encodeURIComponent(id) + "/models", { signal: controller.signal }
    ).then(readSettingsResponse).then(function (payload) {
      if (!saveQueue.mounted) return payload;
      modelCatalogsRef.current = { ...modelCatalogsRef.current, [id]: payload };
      setModelCatalogs(modelCatalogsRef.current);
      setModelLoadStates(function (current) {
        return { ...current, [id]: { loading: false, failed: false } };
      });
      return payload;
    }).catch(function () {
      if (!saveQueue.mounted) return null;
      var previousCatalog = modelCatalogsRef.current[id];
      if (previousCatalog) {
        modelCatalogsRef.current = {
          ...modelCatalogsRef.current,
          [id]: { ...previousCatalog, status: "failed" },
        };
        setModelCatalogs(modelCatalogsRef.current);
      }
      setModelLoadStates(function (current) {
        return { ...current, [id]: { loading: false, failed: true } };
      });
      return null;
    }).finally(function () {
      requestControllersRef.current.delete(controller);
      delete modelRequestsRef.current[id];
    });
    modelRequestsRef.current[id] = request;
    return request;
  }

  function setModelCustomMode(id, field, enabled) {
    var key = id + ":" + field;
    setModelCustomModes(function (current) {
      return { ...current, [key]: enabled === true };
    });
  }

  function acceptSettings(payload) {
    var normalized = normalizeMediaSettings(payload);
    var previousSaved = savedSettingsRef.current;
    var modelRefreshIds = MEDIA_PROVIDER_ORDER.filter(function (id) {
      var previousProvider = previousSaved && previousSaved.providers[id];
      var endpointChanged = previousProvider
        && String(previousProvider.base_url || "") !== String(normalized.providers[id].base_url || "");
      return modelCatalogsRef.current[id]
        && (String(draftKeysRef.current[id] || "").trim()
          || clearKeysRef.current[id] === true
          || endpointChanged);
    });
    settingsRef.current = normalized;
    savedSettingsRef.current = normalized;
    draftKeysRef.current = {};
    clearKeysRef.current = {};
    saveQueue.snapshot = { settings: normalized, draftKeys: {}, clearKeys: {} };
    saveQueue.dirty = false;
    if (Number.isInteger(normalized.revision)) saveQueue.revision = normalized.revision;
    saveQueue.blockedVersion = -1;
    setSettings(normalized);
    setDraftKeys({});
    setClearKeys({});
    setDirty(false);
    setSaveError("");
    setConflict(false);
    modelRefreshIds.forEach(function (id) { loadProviderModels(id, true); });
  }

  function load() {
    if (!saveQueue.available) return Promise.resolve(null);
    if (saveQueue.timer) clearTimeout(saveQueue.timer);
    saveQueue.timer = null;
    saveQueue.version += 1;
    setLoading(true);
    setLoadError("");
    var controller = new AbortController();
    requestControllersRef.current.add(controller);
    return settingsFetch("/api/settings/media", { signal: controller.signal }).then(readSettingsResponse).then(function (payload) {
      if (!saveQueue.mounted) return;
      acceptSettings(payload);
      setLoadFailed(false);
    }).catch(function (loadError) {
      if (!saveQueue.mounted) return;
      setLoadFailed(true);
      setLoadError(t("settings.mediaLoadFailed") + ": " + (loadError.message || ""));
    }).finally(function () {
      requestControllersRef.current.delete(controller);
      if (saveQueue.mounted) setLoading(false);
    });
  }

  saveQueue.t = t;
  saveQueue.setSaving = setSaving;
  saveQueue.setDirty = setDirty;
  saveQueue.setSaveError = setSaveError;
  saveQueue.setConflict = setConflict;
  saveQueue.acceptSaved = acceptSettings;

  useEffectSt(function () {
    if (!available) {
      if (saveQueue.timer) clearTimeout(saveQueue.timer);
      saveQueue.timer = null;
      saveQueue.mounted = false;
      if (saveQueue.requestController) saveQueue.requestController.abort();
      requestControllersRef.current.forEach(function (controller) { controller.abort(); });
      requestControllersRef.current.clear();
      setLoading(false);
      return undefined;
    }
    saveQueue.mounted = true;
    load();
    return function () {
      if (saveQueue.timer) clearTimeout(saveQueue.timer);
      saveQueue.timer = null;
      saveQueue.mounted = false;
      if (saveQueue.available && saveQueue.dirty) persistMediaSave(saveQueue, true);
    };
  }, [available]);

  var state = {
    t, settings, draftKeys, clearKeys, loading, saving, dirty, loadError, saveError, conflict, loadFailed,
    modelCatalogs, modelLoadStates, modelCustomModes,
    settingsRef, draftKeysRef, clearKeysRef, saveQueue,
    setSettings, setDraftKeys, setClearKeys, setDirty, setSaveError, setConflict,
  };
  return {
    ...state,
    load: load,
    loadProviderModels: loadProviderModels,
    setModelCustomMode: setModelCustomMode,
    ...createMediaSettingsActions(state),
  };
}

function MediaFlowOverview(t) {
  var steps = [
    ["settings.mediaFlowChooseTitle", "settings.mediaFlowChooseHint"],
    ["settings.mediaFlowGenerateTitle", "settings.mediaFlowGenerateHint"],
    ["settings.mediaFlowDeliverTitle", "settings.mediaFlowDeliverHint"],
  ];
  return React.createElement("section", { className: "wb-media-flow", "aria-label": t("settings.mediaAttachThenWake") },
    React.createElement("ol", null, steps.map(function (step, index) {
      return React.createElement("li", { key: step[0] },
        React.createElement("span", { className: "wb-media-flow-index", "aria-hidden": "true" }, String(index + 1)),
        React.createElement("span", null,
          React.createElement("strong", null, t(step[0])),
          React.createElement("small", null, t(step[1]))));
    })),
    React.createElement("p", null, t("settings.mediaAttachThenWake")));
}

function MediaSaveState(p) {
  var label = p.error
    ? p.t("settings.mediaAutoSavePaused")
    : p.saving
    ? p.t("settings.mediaSaving")
    : p.dirty ? p.t("settings.mediaAutoSavePending") : p.t("settings.mediaAutoSaveSaved");
  return React.createElement("div", {
    className: "wb-media-autosave" + (p.error ? " is-error" : p.saving ? " is-saving" : p.dirty ? " is-pending" : " is-saved"),
    role: "status",
    "aria-live": "polite",
  },
    p.saving && React.createElement("span", { className: "wb-spinner", "aria-hidden": "true" }),
    React.createElement("span", null, label),
    React.createElement("small", null, p.t("settings.mediaAutoSaveHint")));
}

function MediaPanel(p) {
  var t = p.t;
  var media = useMediaConfiguration(t, p.available !== false);

  if (media.loading) {
    return React.createElement("div", { className: "settings-panel" },
      SectionTitle(t("settings.mediaGeneration"), t("settings.loading")));
  }

  if (media.loadFailed && !Number.isInteger(media.settings.revision)) {
    return React.createElement("div", { className: "settings-panel wb-media-settings" },
      SectionTitle(t("settings.mediaGeneration"), t("settings.mediaGenerationSubtitle")),
      React.createElement("div", { className: "wb-media-error", role: "alert" },
        React.createElement("span", null, media.loadError),
        React.createElement("button", {
          type: "button",
          className: "wb-btn muted",
          onClick: media.load,
        }, t("settings.mediaRetry"))));
  }

  var providerProps = {
    t: t,
    settings: media.settings,
    draftKeys: media.draftKeys,
    clearKeys: media.clearKeys,
    saving: media.saving,
    modelCatalogs: media.modelCatalogs,
    modelLoadStates: media.modelLoadStates,
    modelCustomModes: media.modelCustomModes,
    loadProviderModels: media.loadProviderModels,
    setModelCustomMode: media.setModelCustomMode,
    updateProvider: media.updateProvider,
    updateDraftKey: media.updateDraftKey,
    markKeyForClear: media.markKeyForClear,
    undoKeyClear: media.undoKeyClear,
  };
  return React.createElement("div", { className: "settings-panel wb-media-settings" },
    SectionTitle(t("settings.mediaGeneration"), t("settings.mediaGenerationSubtitle")),
    MediaFlowOverview(t),
    media.loadError && React.createElement("div", { className: "wb-media-error", role: "alert" },
      React.createElement("span", null, media.loadError),
      media.loadFailed && React.createElement("button", { type: "button", className: "wb-btn muted", onClick: media.load }, t("settings.mediaRetry"))),
    media.saveError && React.createElement("div", { className: "wb-media-error", role: "alert" },
      React.createElement("span", null, media.saveError),
      React.createElement("button", {
        type: "button",
        className: "wb-btn muted",
        disabled: media.saving,
        onClick: media.conflict ? media.reloadLatest : media.retrySave,
      }, t(media.conflict ? "settings.mediaReloadLatest" : "settings.mediaRetry"))),
    MediaDefaultsSection({ t: t, settings: media.settings, updateDefault: media.updateDefault }),
    React.createElement("div", { id: "setting-media-providers" },
      SectionTitle(t("settings.mediaProviders"), t("settings.mediaProvidersHint"))),
    MEDIA_PROVIDER_ORDER.map(function (id) {
      return MediaProviderCard({ ...providerProps, id: id });
    }),
    MediaRuntimeSection({ t: t, settings: media.settings, updateRoot: media.updateRoot }),
    MediaSaveState({
      t: t,
      saving: media.saving,
      dirty: media.dirty,
      error: media.saveError,
    }),
  );
}

export { MediaPanel, normalizeMediaSettings, mediaSavePayload }
