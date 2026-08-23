import {
  useStateSt,
  useEffectSt,
  useRefSt,
  readSettingsResponse,
  settingsFetch,
  showSettingsToast,
  SectionTitle,
  FieldRow,
  Toggle,
} from "./shared.jsx"

var SEARCH_PROVIDER_NAMES = {
  simplexng: "SimpleXNG",
  deepseek: "DeepSeek",
  tavily: "Tavily",
  brave: "Brave Search",
};

function searchSavePayload(snapshot, revision) {
  return {
    enabled: snapshot.config.enabled === true,
    providers: snapshot.config.providers.map(function (provider) {
      var row = { id: provider.id, enabled: provider.enabled === true };
      var draft = String(snapshot.draftKeys[provider.id] || "").trim();
      if (draft) row.api_key = draft;
      if (snapshot.clearKeys[provider.id]) row.clear_api_key = true;
      return row;
    }),
    expected_revision: Number.isInteger(revision) ? revision : snapshot.config.revision,
  };
}

function scheduleSearchSave(queue, delay) {
  if (!queue.mounted) return;
  if (queue.timer) clearTimeout(queue.timer);
  queue.timer = null;
  if (queue.inFlight || queue.blockedVersion === queue.version) return;
  queue.timer = setTimeout(function () {
    queue.timer = null;
    persistSearchSave(queue);
  }, Math.max(0, Number(delay) || 0));
}

function failSearchSave(queue, error) {
  if (!queue.mounted) return;
  queue.blockedVersion = queue.version;
  queue.setDirty(true);
  var message = queue.t("settings.searchSaveFailed") + ": " + (error.message || "");
  queue.setError(message);
  showSettingsToast(message, "error");
  if (error.status === 409 && window.confirm(queue.t("settings.searchConflictReload"))) queue.reload();
}

function persistSearchSave(queue) {
  if (!queue.mounted || queue.inFlight || queue.blockedVersion === queue.version || !queue.snapshot) return;
  var snapshot = queue.snapshot;
  var version = queue.version;
  queue.inFlight = true;
  queue.setSaving(true);
  settingsFetch("/api/settings/search", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(searchSavePayload(snapshot, queue.revision)),
  }).then(readSettingsResponse).then(function (payload) {
    queue.inFlight = false;
    if (!queue.mounted) return;
    if (Number.isInteger(payload.revision)) queue.revision = payload.revision;
    if (queue.version === version) {
      queue.acceptSaved(payload);
      return;
    }
    queue.snapshot = {
      ...queue.snapshot,
      config: { ...queue.snapshot.config, revision: queue.revision },
    };
    scheduleSearchSave(queue, 0);
  }).catch(function (error) {
    queue.inFlight = false;
    failSearchSave(queue, error);
  }).finally(function () {
    if (queue.mounted) queue.setSaving(false);
  });
}

function createSearchActions(store) {
  function queue(nextConfig, nextDraftKeys, nextClearKeys) {
    store.configRef.current = nextConfig;
    store.draftKeysRef.current = nextDraftKeys;
    store.clearKeysRef.current = nextClearKeys;
    store.saveQueue.version += 1;
    store.saveQueue.snapshot = {
      config: nextConfig,
      draftKeys: { ...nextDraftKeys },
      clearKeys: { ...nextClearKeys },
    };
    store.saveQueue.blockedVersion = -1;
    store.setDirty(true);
    store.setSaveError("");
    store.setConfig(nextConfig);
    store.setDraftKeys(nextDraftKeys);
    store.setClearKeys(nextClearKeys);
    scheduleSearchSave(store.saveQueue, 600);
  }
  return {
    updateEnabled: function (enabled) {
      queue({ ...store.configRef.current, enabled: enabled === true }, store.draftKeysRef.current, store.clearKeysRef.current);
    },
    updateProvider: function (id, updates) {
      var providers = store.configRef.current.providers.map(function (provider) {
        return provider.id === id ? { ...provider, ...updates } : provider;
      });
      queue({ ...store.configRef.current, providers: providers }, store.draftKeysRef.current, store.clearKeysRef.current);
    },
    moveProvider: function (index, direction) {
      var target = index + direction;
      if (target < 0 || target >= store.configRef.current.providers.length) return;
      var providers = store.configRef.current.providers.slice();
      var current = providers[index];
      providers[index] = providers[target];
      providers[target] = current;
      queue({ ...store.configRef.current, providers: providers }, store.draftKeysRef.current, store.clearKeysRef.current);
    },
    updateKey: function (id, value) {
      queue(store.configRef.current, { ...store.draftKeysRef.current, [id]: value }, { ...store.clearKeysRef.current, [id]: false });
    },
    clearKey: function (id) {
      queue(store.configRef.current, { ...store.draftKeysRef.current, [id]: "" }, { ...store.clearKeysRef.current, [id]: true });
    },
    retrySave: function () {
      if (store.saveQueue.inFlight) return;
      store.saveQueue.blockedVersion = -1;
      store.setSaveError("");
      scheduleSearchSave(store.saveQueue, 0);
    },
  };
}

function useSearchConfiguration(t) {
  var [config, setConfig] = useStateSt({ revision: null, enabled: true, providers: [] });
  var [draftKeys, setDraftKeys] = useStateSt({});
  var [clearKeys, setClearKeys] = useStateSt({});
  var [loading, setLoading] = useStateSt(true);
  var [saving, setSaving] = useStateSt(false);
  var [dirty, setDirty] = useStateSt(false);
  var [saveError, setSaveError] = useStateSt("");
  var configRef = useRefSt(config);
  var draftKeysRef = useRefSt(draftKeys);
  var clearKeysRef = useRefSt(clearKeys);
  var saveQueueRef = useRefSt(null);
  if (!saveQueueRef.current) saveQueueRef.current = {
    mounted: true, timer: null, inFlight: false, blockedVersion: -1,
    version: 0, revision: null, snapshot: null,
  };
  var saveQueue = saveQueueRef.current;

  function acceptConfig(payload) {
    configRef.current = payload;
    draftKeysRef.current = {};
    clearKeysRef.current = {};
    saveQueue.snapshot = { config: payload, draftKeys: {}, clearKeys: {} };
    if (Number.isInteger(payload.revision)) saveQueue.revision = payload.revision;
    saveQueue.blockedVersion = -1;
    setConfig(payload);
    setDraftKeys({});
    setClearKeys({});
    setDirty(false);
    setSaveError("");
  }

  function load() {
    setLoading(true);
    settingsFetch("/api/settings/search").then(readSettingsResponse).then(function (payload) {
      if (saveQueue.mounted) acceptConfig(payload);
    }).catch(function (error) {
      if (saveQueue.mounted) showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    }).finally(function () { if (saveQueue.mounted) setLoading(false); });
  }

  saveQueue.t = t;
  saveQueue.setSaving = setSaving;
  saveQueue.setDirty = setDirty;
  saveQueue.setError = setSaveError;
  saveQueue.acceptSaved = acceptConfig;
  saveQueue.reload = load;

  useEffectSt(function () {
    saveQueue.mounted = true;
    load();
    return function () {
      saveQueue.mounted = false;
      if (saveQueue.timer) clearTimeout(saveQueue.timer);
      saveQueue.timer = null;
    };
  }, []);

  var actions = createSearchActions({
    configRef, draftKeysRef, clearKeysRef, saveQueue,
    setConfig, setDraftKeys, setClearKeys, setDirty, setSaveError,
  });
  return {
    config, draftKeys, clearKeys, loading, saving, dirty, saveError,
    ...actions,
  };
}

function SearchMoveIcon(direction) {
  var isUp = direction === "up";
  return React.createElement("svg", {
    width: "16", height: "16", viewBox: "0 0 24 24",
    fill: "none", stroke: "currentColor", strokeWidth: "2",
    strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true",
  },
    React.createElement("path", { d: isUp ? "M12 19V5M5 12l7-7 7 7" : "M12 5v14M19 12l-7 7-7-7" }));
}

function SearchClearIcon() {
  return React.createElement("svg", {
    width: "14", height: "14", viewBox: "0 0 24 24",
    fill: "none", stroke: "currentColor", strokeWidth: "2",
    strokeLinecap: "round", "aria-hidden": "true",
  },
    React.createElement("path", { d: "M6 6l12 12M18 6 6 18" }));
}

function SearchProviderRow(p) {
  var provider = p.provider;
  var providerName = SEARCH_PROVIDER_NAMES[provider.id] || provider.id;
  var keyConfigured = provider.api_key_configured && !p.clearKeys[provider.id];
  return React.createElement("div", {
    className: "wb-field wb-search-provider-row",
    key: provider.id,
  },
    React.createElement("div", { className: "wb-label wb-search-provider-label" },
      React.createElement("span", { className: "wb-search-provider-name" },
        React.createElement("span", { className: "wb-search-priority mono" }, String(p.index + 1)),
        providerName),
      React.createElement("small", null, p.t("settings.searchProviderHint." + provider.id))),
    React.createElement("div", { className: "wb-controls wb-search-provider-controls" },
      provider.requires_api_key && React.createElement("div", {
        className: "wb-search-provider-credential" + (keyConfigured ? " has-clear" : ""),
      },
        React.createElement("input", {
          className: "wb-input mono",
          type: "password",
          value: p.draftKeys[provider.id] || "",
          placeholder: keyConfigured
            ? p.t("settings.searchConfigured")
            : p.t("settings.searchApiKeyPlaceholder"),
          autoComplete: "new-password",
          "aria-label": providerName + " " + p.t("settings.apiKey"),
          onChange: function (event) {
            p.updateKey(provider.id, event.target.value);
          },
        }),
        keyConfigured && React.createElement("button", {
          type: "button",
          className: "wb-search-key-clear",
          "aria-label": p.t("settings.clearStoredKey"),
          title: p.t("settings.clearStoredKey"),
          onClick: function () {
            p.clearKey(provider.id);
          },
        }, SearchClearIcon())),
      React.createElement("div", { className: "wb-search-provider-actions" },
        React.createElement("button", {
          type: "button", className: "wb-icon-btn-small",
          disabled: p.index === 0,
          onClick: function () { p.moveProvider(p.index, -1); },
          "aria-label": p.t("settings.searchMoveUp"),
        }, SearchMoveIcon("up")),
        React.createElement("button", {
          type: "button", className: "wb-icon-btn-small",
          disabled: p.index === p.providerCount - 1,
          onClick: function () { p.moveProvider(p.index, 1); },
          "aria-label": p.t("settings.searchMoveDown"),
        }, SearchMoveIcon("down")),
        Toggle(provider.enabled, function () {
          p.updateProvider(provider.id, { enabled: !provider.enabled });
        }, false, providerName))));
}

function SearchPanel(p) {
  var state = useSearchConfiguration(p.t);
  if (state.loading) {
    return React.createElement("div", { className: "settings-panel wb-search-settings" },
      SectionTitle(p.t("settings.searchProviders"), p.t("settings.searchProvidersSubtitle")),
      React.createElement("div", { className: "wb-hint" }, p.t("settings.loading")));
  }
  return React.createElement("div", { className: "settings-panel wb-search-settings" },
    SectionTitle(p.t("settings.searchProviders"), p.t("settings.searchProvidersSubtitle")),
    FieldRow(
      p.t("settings.searchEnabled"),
      p.t("settings.searchEnabledHint"),
      Toggle(state.config.enabled, function () {
        state.updateEnabled(!state.config.enabled);
      }, false, p.t("settings.searchEnabled")),
      undefined,
      "setting-search-enabled",
    ),
    React.createElement("div", { className: "wb-search-provider-list", id: "setting-search-providers" },
      state.config.providers.map(function (provider, index) {
        return React.createElement(SearchProviderRow, {
          ...state, t: p.t, provider: provider, index: index,
          providerCount: state.config.providers.length, key: provider.id,
        });
      })),
    state.saveError && React.createElement("div", { className: "wb-save-actions wb-search-settings-footer" },
      React.createElement("span", {
        className: "wb-search-save-error", role: "alert",
      }, state.saveError),
      React.createElement("button", {
        type: "button", className: "wb-btn muted",
        disabled: state.saving, onClick: state.retrySave,
      }, p.t("settings.searchRetrySave"))));
}

export { SearchPanel };
