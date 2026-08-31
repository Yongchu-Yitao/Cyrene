import { workbenchServices } from "./shared/runtime/services.jsx"
// Model configuration settings — provider connections, model profiles and routes.
// Loaded as a classic script and registered through the shared CyreneUI registry.
var MODEL_CONFIGURATION_ERROR_KEYS = {
  "model connection timed out": ["settings.modelConnectionTimedOut", "Model connection timed out."],
  "model connection was rejected": ["settings.modelConnectionRejected", "The model service rejected the connection."],
  "model connection failed": ["settings.modelConnectionFailed", "Model connection failed."],
  "model connection is unavailable": ["settings.modelConnectionUnavailable", "The model service is currently unavailable."],
  "model discovery timed out": ["settings.modelDiscoveryTimedOut", "Model discovery timed out."],
  "model discovery was rejected": ["settings.modelDiscoveryRejected", "The model service rejected model discovery."],
  "model discovery failed": ["settings.modelDiscoveryFailed", "Model discovery failed."],
  "model discovery is unavailable": ["settings.modelDiscoveryUnavailable", "Model discovery is currently unavailable."],
};
var MODEL_CONFIGURATION_ERROR_CODE_KEYS = {
  "model_connection_failed": ["settings.modelConnectionFailed", "Model connection failed."],
  "model_tls_failed": ["settings.modelConnectionFailed", "Model connection failed."],
  "model_timeout": ["settings.modelConnectionTimedOut", "Model connection timed out."],
  "model_authentication_failed": ["settings.modelConnectionRejected", "The model service rejected the connection."],
  "model_unavailable": ["settings.modelConnectionUnavailable", "The model service is currently unavailable."],
  "model_service_unavailable": ["settings.modelConnectionUnavailable", "The model service is currently unavailable."],
};
var ROUTE_META = {
  primary: { title: "Primary model order", titleKey: "settings.primaryRouteTitle", description: "Primary models for chat and Agent work, with automatic fallback in order.", descriptionKey: "settings.primaryRouteHint", capability: "chat", ordered: true },
  secondary: { title: "Secondary model", titleKey: "settings.secondaryRouteTitle", description: "Used for summaries, titles, and lower-cost supporting tasks.", descriptionKey: "settings.secondaryRouteHint", capability: "chat", ordered: false },
  vision: { title: "Vision model", titleKey: "settings.visionRouteTitle", description: "Handles images and visual content, with automatic fallback in order.", descriptionKey: "settings.visionRouteHint", capability: "vision", ordered: true },
  embedding: { title: "Embedding model", titleKey: "settings.embeddingRouteTitle", description: "Generate vectors for the knowledge base and semantic search.", descriptionKey: "settings.embeddingRouteHint", capability: "embedding", ordered: false },
};

function renderModelConnectionPane(v) {
  var h = v.h;
  return h("aside", { className: "wb-mcfg-connection-pane", "aria-label": v.label(v.props, "settings.modelProviders", "Model providers") },
    h("div", { className: "wb-workbench-filterbar wb-mcfg-searchbar" },
      h("label", { className: "wb-workbench-searchbox wb-mcfg-searchbox" },
        v.searchIcon(16),
        h("input", {
          type: "search", value: v.query,
          onChange: function (event) { v.setQuery(event.target.value); },
          placeholder: v.label(v.props, "settings.searchModelServicesPlaceholder", "Search model services…"), "aria-label": v.label(v.props, "settings.searchModelServices", "Search model services"),
          autoComplete: "off", spellCheck: false,
        })
      )
    ),
    h("div", { className: "wb-mcfg-connection-list", role: "listbox", "aria-label": v.label(v.props, "settings.providerConnections", "Provider connections") },
      !v.filtered.length ? h("div", { className: "wb-mcfg-list-empty" }, String(v.query || "").trim() ? v.label(v.props, "settings.noMatchingProviders", "No matching providers.") : v.label(v.props, "settings.noProviderConnections", "No provider connections yet.")) : null,
      v.filtered.map(function (connection) {
        var adapter = v.adapters.find(function (item) { return item.id === connection.adapter; });
        var local = v.isLocalConnection(connection);
        var count = local
          ? v.localModels.filter(function (model) { return model.ready === true; }).length
          : v.config.profiles.filter(function (profile) { return profile.connection_id === connection.id; }).length;
        var serviceLabel = local ? "Local" : (adapter && adapter.name || connection.adapter);
        return h("button", {
          type: "button", role: "option", key: connection.id,
          className: "wb-mcfg-connection-item" + (v.selectedId === connection.id ? " is-selected" : ""),
          "aria-selected": v.selectedId === connection.id,
          "aria-haspopup": "menu",
          "aria-expanded": !!v.connectionMenu && v.connectionMenu.connectionId === connection.id,
          "aria-controls": v.connectionMenu && v.connectionMenu.connectionId === connection.id ? "wb-mcfg-connection-menu" : undefined,
          "data-cyrene-context-menu": "true",
          onClick: function () { v.setConnectionMenu(null); v.setSelectedId(connection.id); v.store.setError(""); },
          onContextMenu: function (event) { v.openConnectionMenu(event, connection, false); },
          onKeyDown: function (event) {
            if (event.key === "ContextMenu" || event.key === "Menu" || (event.shiftKey && event.key === "F10")) v.openConnectionMenu(event, connection, true);
          },
        },
          h("span", { className: "wb-mcfg-provider-mark", "aria-hidden": "true" }, v.connectionProviderMark(connection)),
          h("span", { className: "wb-mcfg-connection-copy" },
            h("strong", null, v.connectionDisplayName(connection)),
            h("small", null, serviceLabel + " · " + v.label(v.props, "settings.modelCount", "Models: {count}", { count: count }))
          ),
          h("span", { className: "wb-mcfg-dot " + (connection.enabled ? "is-ready" : "is-off"), title: connection.enabled ? v.label(v.props, "common.enabled", "Enabled") : v.label(v.props, "common.disabled", "Disabled") }, h("span", { className: "wb-mcfg-sr-only" }, connection.enabled ? v.label(v.props, "common.enabled", "Enabled") : v.label(v.props, "common.disabled", "Disabled")))
        );
      })
    ),
    h("button", { type: "button", className: "wb-mcfg-add-connection", disabled: !v.selectableAdapters.length, onClick: v.addConnection, title: v.selectableAdapters.length ? "" : v.label(v.props, "settings.adapterUnavailable", "No adapters are available for this service.") }, v.browserIcon("plus", 17), v.label(v.props, "settings.addProvider", "Add provider"))
  );
}

function renderModelDetailPane(v) {
  var h = v.h;
  var selected = v.selected;
  return h("main", { className: "wb-mcfg-detail-pane" }, selected ? h(React.Fragment, null,
    h("div", { className: "wb-mcfg-detail-head" },
      h("div", null, h("h3", null, v.connectionDisplayName(selected)), v.selectedDescription ? h("p", null, v.selectedDescription) : null),
      v.isLocalConnection(selected)
        ? h("button", { type: "button", className: "wb-mcfg-icon-btn", onClick: v.refreshLocalModels, "aria-label": v.label(v.props, "settings.refreshLocalModels", "Refresh local models") }, v.browserIcon("reload", 16))
        : h(v.Toggle, { checked: selected.enabled, label: selected.enabled ? v.label(v.props, "settings.disableNamedProvider", "Disable {name}", { name: selected.name }) : v.label(v.props, "settings.enableNamedProvider", "Enable {name}", { name: selected.name }), onChange: function (value) { v.updateConnection("enabled", value); } })
    ),
    !v.isLocalConnection(selected) ? h("section", { className: "wb-mcfg-form-section wb-mcfg-connection-section", "aria-label": v.label(v.props, "settings.connectionSettings", "Connection settings") },
      h("div", { className: "wb-mcfg-form-grid" },
        h(v.Field, { label: v.label(v.props, "settings.connectionName", "Connection name") }, h("input", { className: "wb-input", value: selected.name, "aria-label": v.label(v.props, "settings.connectionName", "Connection name"), onChange: function (event) { v.updateConnection("name", event.target.value); } })),
        h(v.Field, { label: v.label(v.props, "settings.adapter", "Adapter") }, h("select", { className: "wb-select", value: selected.adapter, "aria-label": v.label(v.props, "settings.modelServiceAdapter", "Model service adapter"), disabled: !v.selectableAdapters.length, onChange: function (event) { v.updateConnection("adapter", event.target.value); } }, v.editorAdapters.map(function (adapter) {
          return h("option", { key: adapter.id, value: adapter.id, disabled: !v.isUserSelectableAdapter(adapter) }, v.adapterOptionName(adapter));
        }))),
        !v.isCodexConnection(selected) && !v.isLocalConnection(selected) ? h(v.Field, {
          label: v.label(v.props, "settings.apiEndpoint", "API endpoint"),
          wide: true,
          hint: selected.use_proxy && !v.proxyMasterEnabled ? v.label(v.props, "settings.modelProxyMasterDisabledHint", "A proxy is selected for this model service. Enable the master proxy switch in General settings to use it.") : "",
        }, h("div", { className: "wb-mcfg-api-proxy-row" },
          h("input", { className: "wb-input", type: "url", value: selected.base_url, "aria-label": v.label(v.props, "settings.modelServiceApiEndpoint", "Model service API endpoint"), onChange: function (event) { v.updateConnection("base_url", event.target.value); }, placeholder: "https://api.example.com/v1", autoComplete: "url" }),
          h("div", { className: "wb-mcfg-model-proxy-control" },
            h("span", null, v.label(v.props, "settings.useProxy", "Use proxy")),
            h(v.Toggle, { checked: selected.use_proxy === true, label: selected.use_proxy ? v.label(v.props, "settings.disableNamedProxy", "Disable proxy for {name}", { name: selected.name }) : v.label(v.props, "settings.enableNamedProxy", "Enable proxy for {name}", { name: selected.name }), onChange: function (value) { v.updateConnection("use_proxy", value); } })
          )
        )) : null,
        !v.isCodexConnection(selected) && !v.isLocalConnection(selected) ? h(v.Field, { label: v.label(v.props, "settings.apiKey", "API key"), wide: true, hint: selected.secret_configured && !selected.secret ? v.label(v.props, "settings.savedKeyLeaveBlank", "A key is saved; leave this blank to keep it unchanged.") : v.label(v.props, "settings.localApiKeyHint", "The key is sent only to the local configuration API.") }, h("input", {
          className: "wb-input", type: "password", value: selected.secret,
          onChange: function (event) { v.updateConnection("secret", event.target.value); },
          placeholder: selected.secret_configured ? v.label(v.props, "settings.configured", "Configured") : "sk-…", autoComplete: "new-password",
          "aria-label": v.label(v.props, "settings.apiKeyWriteOnly", "API key (write only)"), "data-cyrene-agent-secret-input": "true", "data-cyrene-risk": "R3",
        })) : null
      )
    ) : null,
    v.isCodexConnection(selected) ? h(v.OAuthSection, { state: v.oauth, busy: v.oauthBusy, cliBusy: v.oauthBusy === "cli", onLogin: v.startOauthLogin, onLogout: v.logoutOauth, onDownloadCli: v.downloadOauthCli, onImportModels: v.importOauthModels, onImportModel: v.importOauthModel }) : null,
    v.isLocalConnection(selected) ? h(v.LocalModelsSection, { t: v.props.t, models: v.localModels, cv2Runtime: v.localRuntime, error: v.localError, busy: v.localBusy, hideHeader: true, onRefresh: v.refreshLocalModels, onManage: v.manageLocalModel }) : null,
    !v.isLocalConnection(selected) ? h("section", { className: "wb-mcfg-form-section", "aria-labelledby": "wb-mcfg-profiles-heading" },
      h("div", { className: "wb-mcfg-section-head" },
        h("h4", { id: "wb-mcfg-profiles-heading" }, v.label(v.props, "settings.modelList", "Model list")),
        h("div", { className: "wb-mcfg-section-actions" },
          v.discovery && v.discovery.error ? h("span", { className: "wb-mcfg-discovery-error", title: v.discovery.error }, v.label(v.props, "settings.fetchModelsFailed", "Could not fetch models")) : null,
          h("button", {
            type: "button",
            className: "wb-btn",
            disabled: !!(v.discovery && v.discovery.loading),
            onClick: function () { v.discoverConnection({ notify: true, force: true }); },
          }, v.discovery && v.discovery.loading ? h("span", { className: "wb-spinner small" }) : v.browserIcon("reload", 15), v.discovery && v.discovery.loading ? v.label(v.props, "settings.fetchingModels", "Fetching…") : v.label(v.props, "settings.refreshModels", "Refresh models")),
          h("button", { type: "button", className: "wb-btn", disabled: !!v.profileDraft, onClick: function () { v.addProfile(); } }, v.browserIcon("plus", 15), v.label(v.props, "settings.addModel", "Add model"))
        )
      ),
      !v.profiles.length && !v.profileDraft ? h("div", { className: "wb-mcfg-inline-empty" }, v.label(v.props, "settings.noModelProfilesHint", "No model profiles yet. Add one to choose a provider model or enter a model ID manually.")) : null,
      v.profiles.length || v.profileDraft ? h("div", { className: "wb-mcfg-profile-list", "aria-label": v.label(v.props, "settings.modelList", "Model list") }, v.profiles.concat(v.profileDraft ? [v.profileDraft] : []).map(function (profile) {
        var isDraft = !!v.profileDraft && profile.id === v.profileDraft.id;
        return h(v.ProfileEditor, { key: profile.id, profile: profile, draft: isDraft, t: v.props.t,
          onChange: function (key, value) { isDraft ? v.updateProfileDraft(key, value) : v.updateProfile(profile.id, key, value); },
          onModelSelect: function (item) { isDraft ? v.applyDiscoveredModelToDraft(item) : v.applyDiscoveredModel(profile.id, item); },
          modelOptions: v.discovery && v.discovery.models || [],
          modelsLoading: !!(v.discovery && v.discovery.loading),
          modelsError: v.discovery && v.discovery.error || "",
          onRemove: function () { isDraft ? v.cancelProfileDraft() : v.removeProfile(profile.id); },
          onCommit: isDraft ? v.commitProfileDraft : undefined,
          onTest: function () { v.testProfile(profile); }, testing: v.busy === "test:" + profile.id });
      })) : null
    ) : null
  ) : h("div", { className: "wb-mcfg-state" },
    h("strong", null, v.label(v.props, "settings.selectOrAddProvider", "Select or add a model provider")),
    h("span", null, v.label(v.props, "settings.providerProfileHint", "Provider connections store credentials; model profiles store specific models and capabilities.")),
    h("button", { type: "button", className: "wb-btn primary", disabled: !v.selectableAdapters.length, onClick: v.addConnection }, v.label(v.props, "settings.addProvider", "Add provider"))
  ));
}

function useModelConfigurationLifecycle(v) {
  v.useEffect(function () {
    if (!v.config || !(v.config.connections || []).length) return;
    if (!(v.config.connections || []).some(function (item) { return item.id === v.selectedId; })) v.setSelectedId(v.config.connections[0].id);
  }, [v.config && v.config.connections && v.config.connections.length, v.selectedId]);
  v.useEffect(function () {
    return function () {
      if (v.oauthPoll.current) clearInterval(v.oauthPoll.current);
      if (v.oauthCliPoll.current) clearInterval(v.oauthCliPoll.current);
    };
  }, []);
  v.useEffect(function () {
    v.saveQueueMounted.current = true;
    return function () {
      v.saveQueueMounted.current = false;
      if (v.saveQueueTimer.current) clearTimeout(v.saveQueueTimer.current);
      v.saveQueueTimer.current = null;
    };
  }, []);
  v.useEffect(function () {
    if (!v.config || v.dirtyRef.current) return;
    v.queuedSnapshot.current = v.config;
  }, [v.config]);
}

function useConnectionMenuLifecycle(v) {
  v.useEffect(function () {
    if (!v.connectionMenu) return;
    function restoreTriggerFocus() {
      var trigger = v.connectionMenuReturnFocus.current;
      if (!trigger || !trigger.isConnected) return;
      window.requestAnimationFrame(function () {
        try { trigger.focus({ preventScroll: true }); } catch (error) { trigger.focus(); }
      });
    }
    function closeFromPointer(event) {
      if (v.connectionMenuRef.current && v.connectionMenuRef.current.contains(event.target)) return;
      v.setConnectionMenu(null);
    }
    function closeFromKey(event) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      v.setConnectionMenu(null);
      restoreTriggerFocus();
    }
    function closeFromViewport() {
      v.setConnectionMenu(null);
      restoreTriggerFocus();
    }
    document.addEventListener("pointerdown", closeFromPointer, true);
    document.addEventListener("keydown", closeFromKey, true);
    window.addEventListener("scroll", closeFromViewport, true);
    window.addEventListener("resize", closeFromViewport);
    window.requestAnimationFrame(function () {
      var firstItem = v.connectionMenuRef.current && v.connectionMenuRef.current.querySelector('[role="menuitem"]');
      if (firstItem) {
        try { firstItem.focus({ preventScroll: true }); } catch (error) { firstItem.focus(); }
      }
    });
    return function () {
      document.removeEventListener("pointerdown", closeFromPointer, true);
      document.removeEventListener("keydown", closeFromKey, true);
      window.removeEventListener("scroll", closeFromViewport, true);
      window.removeEventListener("resize", closeFromViewport);
    };
  }, [v.connectionMenu && v.connectionMenu.connectionId]);
}

(function () {
  "use strict";
  var h = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useRef = React.useRef;

  function label(props, key, fallback, values) {
    if (props && typeof props.t === "function") {
      try { return props.t(key, values || null, fallback); } catch (error) {}
    }
    return fallback;
  }

  function showSettingsToast(message, type) {
    if (!message) return;
    try {
      var feedback = workbenchServices.feedback();
      if (feedback && typeof feedback.showToast === "function") {
        feedback.showToast(String(message), type || "info");
      }
    } catch (error) {}
  }

  function localizedModelConfigurationError(error, props) {
    var coded = MODEL_CONFIGURATION_ERROR_CODE_KEYS[String(error && error.code || "").trim().toLowerCase()];
    if (coded) return label(props, coded[0], coded[1]);
    var summary = String(error && (error.summary || error.message) || error || "").trim();
    var translation = MODEL_CONFIGURATION_ERROR_KEYS[summary.toLowerCase()];
    return translation ? label(props, translation[0], translation[1]) : summary;
  }

  function browserIcon(name, size) {
    try {
      var Icon = workbenchServices.browser().Icon;
      return h(Icon, { name: name, size: size || 16 });
    } catch (error) {
      return null;
    }
  }

  function searchIcon(size) {
    return h("svg", {
      viewBox: "0 0 24 24",
      width: size || 16,
      height: size || 16,
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "1.9",
      strokeLinecap: "round",
      strokeLinejoin: "round",
      "aria-hidden": "true",
    }, h("circle", { cx: "11", cy: "11", r: "7" }), h("path", { d: "m20 20-3.2-3.2" }));
  }

  function iconMarkup(group, name) {
    var assets = window.CyreneIconAssets;
    return assets && assets[group] && assets[group][name]
      ? assets[group][name]
      : "";
  }

  function settingsGlyph(name, size) {
    var markup = iconMarkup("settings", name);
    if (markup) {
      return h("span", {
        className: "wb-mcfg-glyph is-inline",
        style: {
          width: (size || 16) + "px",
          height: (size || 16) + "px",
        },
        dangerouslySetInnerHTML: { __html: markup },
        "aria-hidden": "true",
      });
    }
    return h("span", {
      className: "wb-mcfg-glyph",
      style: {
        width: (size || 16) + "px",
        height: (size || 16) + "px",
        "--wb-mcfg-glyph": 'url("settings-icons/' + name + '.svg")',
      },
      "aria-hidden": "true",
    });
  }

  function providerGlyph(name, size) {
    var dimension = (size || 19) + "px";
    var markup = iconMarkup("providers", name);
    var brandColors = {
      openai: "#10a37f",
      anthropic: "#d97757",
      ollama: "currentColor",
      onnx: "#005ced",
    };
    if (markup) {
      return h("span", {
        className: "wb-mcfg-provider-logo is-inline is-" + name,
        style: {
          width: dimension,
          height: dimension,
          color: brandColors[name] || "currentColor",
        },
        dangerouslySetInnerHTML: { __html: markup },
        "aria-hidden": "true",
      });
    }
    return h("span", {
      className: "wb-mcfg-glyph wb-mcfg-provider-glyph is-" + name,
      style: {
        width: dimension,
        height: dimension,
        backgroundColor: brandColors[name] || "currentColor",
        "--wb-mcfg-glyph": 'url("provider-icons/' + name + '.svg")',
      },
      "aria-hidden": "true",
    });
  }

  function localModelIcon(kind) {
    if (kind === "asr") return browserIcon("microphone", 20);
    if (kind === "tts") return browserIcon("volume", 20);
    if (kind === "embedding") return settingsGlyph("database", 20);
    return settingsGlyph("device-desktop-up", 20);
  }

  function downloadPercent(model) {
    var direct = Number(model && (model.percent || model.progress_percent));
    if (Number.isFinite(direct) && direct > 0) return Math.min(100, Math.round(direct));
    var total = Number(model && model.total_bytes) || 0;
    return total ? Math.min(100, Math.round((Number(model.downloaded_bytes) || 0) * 100 / total)) : 0;
  }

  function localizedLocalModelError(rawError, props) {
    var lower = String(rawError || "").toLowerCase();
    if (lower.indexOf("archive output is missing or invalid") >= 0
        || lower.indexOf("archive has no declared outputs") >= 0) {
      return label(props, "settings.localModelErrorExtract", "Archive extraction failed — the downloaded bundle is incomplete or invalid. Retry the download.");
    }
    if (lower.indexOf("checksum") >= 0 || lower.indexOf("sha256") >= 0 || lower.indexOf("validation failed") >= 0) {
      return label(props, "settings.localModelErrorChecksum", "Integrity check failed for the downloaded file. Retry the download.");
    }
    if (lower.indexOf("all mirrors failed") >= 0 || lower.indexOf("connect") >= 0
        || lower.indexOf("timeout") >= 0 || lower.indexOf("network") >= 0
        || lower.indexOf("proxy") >= 0 || lower.indexOf("resolve") >= 0
        || lower.indexOf("httpx") >= 0 || lower.indexOf("remote protocol") >= 0) {
      return label(props, "settings.localModelErrorNetwork", "Network or mirror failure. Check your connection and retry the download.");
    }
    return label(props, "settings.localModelErrorGeneric", "Local model download failed. Retry the download.");
  }

  function uniqueStrings(values) {
    var seen = {};
    return (Array.isArray(values) ? values : []).map(function (item) {
      return String(item || "").trim().toLowerCase();
    }).filter(function (item) {
      if (!item || seen[item]) return false;
      seen[item] = true;
      return true;
    });
  }

  function normalizedCapabilities(raw) {
    if (Array.isArray(raw)) return uniqueStrings(raw);
    if (raw && typeof raw === "object") {
      return uniqueStrings(Object.keys(raw).filter(function (key) { return !!raw[key]; }));
    }
    if (typeof raw === "string") return uniqueStrings(raw.split(/[\s,，/]+/));
    return [];
  }

  function profilePriceFields(raw) {
    var value = String(raw || "").trim();
    var currency = "";
    if (value.charAt(0) === "$" || value.charAt(0) === "¥") {
      currency = value.charAt(0);
      value = value.slice(1);
    }
    var parts = value ? value.split("/") : [];
    var input = parts[0] || "";
    var output = "";
    var cache = "";
    if (parts.length >= 3) {
      cache = parts[1];
      output = parts[2];
    } else {
      output = parts[1] || "";
    }
    return { currency: currency, input: input, output: output, cache: cache };
  }

  function updateProfilePriceField(raw, field, value) {
    var pricing = profilePriceFields(raw);
    pricing[field] = String(value == null ? "" : value).trim();
    if (!pricing.input && !pricing.output && !pricing.cache) return "";
    if (pricing.cache !== "") {
      return pricing.currency + pricing.input + "/" + pricing.cache + "/" + pricing.output;
    }
    return pricing.currency + pricing.input + "/" + pricing.output;
  }

  function listFrom(raw) {
    if (Array.isArray(raw)) return raw;
    if (!raw || typeof raw !== "object") return [];
    return Object.keys(raw).map(function (key) {
      var value = raw[key];
      if (value && typeof value === "object") return Object.assign({ id: key }, value);
      return { id: key, name: String(value || key) };
    });
  }

  function safeId(prefix, value) {
    var source = String(value || (Date.now() + "-" + Math.random().toString(16).slice(2, 8))).trim().toLowerCase();
    var base = source.replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 96) || "item";
    var hash = 2166136261;
    for (var index = 0; index < source.length; index += 1) {
      hash ^= source.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return prefix + "-" + base + "-" + (hash >>> 0).toString(36);
  }

  function normalizeAdapter(raw, index) {
    raw = raw || {};
    var id = String(raw.id || ("adapter-" + index)).trim();
    return Object.assign({}, raw, {
      id: id,
      name: String(raw.label || raw.name || id).trim(),
      description: String(raw.description || "").trim(),
      capabilities: normalizedCapabilities(raw.capabilities),
    });
  }

  function adapterKey(value) {
    return String(value || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  }

  function isUserSelectableAdapter(adapter) {
    return !!adapter && adapter.user_selectable !== false;
  }

  function adapterOptionName(adapter) {
    return String(adapter && (adapter.name || adapter.id) || "");
  }

  function normalizeConnection(raw, index) {
    raw = raw || {};
    var id = String(raw.id || ("connection-" + index)).trim();
    var adapter = String(raw.adapter || "openai").trim();
    return Object.assign({}, raw, {
      id: id,
      name: String(raw.name || adapter || id).trim(),
      adapter: adapter,
      base_url: String(raw.base_url || "").trim(),
      secret: String(raw.secret || raw.api_key || ""),
      secret_configured: raw.secret_configured === true,
      enabled: raw.enabled !== false,
      use_proxy: raw.use_proxy === true,
    });
  }

  function normalizeProfile(raw, index, fallbackConnectionId) {
    raw = raw || {};
    var remoteModel = String(raw.model || "").trim();
    var connectionId = String(raw.connection_id || fallbackConnectionId || "").trim();
    var id = String(raw.id || safeId("profile", connectionId + "-" + remoteModel) || ("profile-" + index)).trim();
    var contextLimit = raw.context_limit;
    return Object.assign({}, raw, {
      id: id,
      connection_id: connectionId,
      model: remoteModel,
      name: String(raw.name || remoteModel || id).trim(),
      context_limit: contextLimit == null || contextLimit === "" ? "" : contextLimit,
      price: String(raw.price || "").trim(),
      capabilities: normalizedCapabilities(raw.capabilities),
      enabled: raw.enabled !== false,
    });
  }

  function normalizeRoutes(raw) {
    raw = raw || {};
    function route(name) {
      var value = raw[name];
      if (!Array.isArray(value)) value = [];
      return value.map(function (item) { return String(item || "").trim(); }).filter(Boolean);
    }
    return {
      primary: route("primary"),
      secondary: route("secondary"),
      vision: route("vision"),
      embedding: route("embedding"),
    };
  }

  function normalizeConfig(raw) {
    raw = raw || {};
    var adapters = listFrom(raw.adapters).map(normalizeAdapter);
    var connections = listFrom(raw.connections).map(normalizeConnection);
    var profiles = listFrom(raw.profiles).map(function (item, index) {
      return normalizeProfile(item, index, "");
    });
    return Object.assign({}, raw, {
      adapters: adapters,
      connections: connections,
      profiles: profiles,
      routes: normalizeRoutes(raw.routes),
    });
  }

  function configPayload(config) {
    var payload = {
      connections: (config.connections || []).map(function (connection) {
        var next = {
          id: connection.id,
          name: connection.name,
          adapter: connection.adapter,
          enabled: connection.enabled !== false,
          use_proxy: connection.use_proxy === true,
          base_url: connection.base_url,
          options: connection.options && typeof connection.options === "object" ? connection.options : {},
        };
        var secret = String(connection.secret || "").trim();
        if (secret) {
          next.api_key = secret;
        } else if (connection.clear_api_key === true) {
          next.clear_api_key = true;
        }
        return next;
      }),
      profiles: (config.profiles || []).map(function (profile) {
        return {
          id: profile.id,
          connection_id: profile.connection_id,
          model: profile.model,
          name: profile.name,
          enabled: profile.enabled !== false,
          context_limit: profile.context_limit,
          dimensions: Number(profile.dimensions) || 0,
          reasoning_effort: String(profile.reasoning_effort || "").trim(),
          description: String(profile.description || "").trim(),
          price: String(profile.price || "").trim(),
          max_concurrency: Number(profile.max_concurrency) || 0,
          capabilities: normalizedCapabilities(profile.capabilities),
          options: profile.options && typeof profile.options === "object" ? profile.options : {},
        };
      }),
      routes: normalizeRoutes(config.routes),
    };
    return payload;
  }

  function modelConfigurationPatchOperations(previousConfig, nextConfig) {
    var previous = configPayload(previousConfig || {});
    var next = configPayload(nextConfig || {});
    var operations = [];
    var unconfiguredConnectionIds = {};
    ((previousConfig || {}).connections || []).forEach(function (connection) {
      if (connection && connection._plugin_unconfigured === true) {
        unconfiguredConnectionIds[String(connection.id || "")] = true;
      }
    });
    function equal(left, right) {
      return JSON.stringify(left) === JSON.stringify(right);
    }
    function entityOperations(collection, singular) {
      var beforeById = {};
      var afterById = {};
      (previous[collection] || []).forEach(function (item) { beforeById[item.id] = item; });
      (next[collection] || []).forEach(function (item) { afterById[item.id] = item; });
      Object.keys(beforeById).forEach(function (id) {
        if (!afterById[id]) operations.push({ op: "remove_" + singular, id: id });
      });
      Object.keys(afterById).forEach(function (id) {
        var after = afterById[id];
        var before = beforeById[id];
        if (!before || (singular === "connection" && unconfiguredConnectionIds[id])) {
          if (before && equal(before, after)) return;
          operations.push({ op: "upsert_" + singular, id: id, value: after });
          return;
        }
        var changes = {};
        Object.keys(after).forEach(function (key) {
          if (key !== "id" && !equal(before[key], after[key])) changes[key] = after[key];
        });
        if (Object.keys(changes).length) {
          operations.push({ op: "patch_" + singular, id: id, changes: changes });
        }
      });
    }
    entityOperations("connections", "connection");
    entityOperations("profiles", "profile");
    Object.keys(next.routes || {}).forEach(function (route) {
      if (!equal((previous.routes || {})[route] || [], next.routes[route] || [])) {
        operations.push({ op: "set_route", route: route, value: next.routes[route] || [] });
      }
    });
    return operations;
  }

  function connectionDraftPayload(connection) {
    return configPayload({
      adapters: [],
      connections: [connection],
      profiles: [],
      routes: {},
    }).connections[0];
  }

  async function requestJson(url, init) {
    var response = await window.fetch(url, init);
    var payload = {};
    try { payload = await response.json(); } catch (error) {}
    if (!response.ok) {
      var requestError = new Error(String(payload.detail || payload.error || payload.message || ("HTTP " + response.status)));
      requestError.status = response.status;
      requestError.code = String(payload.code || "");
      requestError.summary = String(payload.error || payload.message || "");
      requestError.detail = String(payload.detail || "");
      throw requestError;
    }
    return payload;
  }

  function useModelConfiguration(props) {
    var [config, setConfig] = useState(function () {
      return props && props.initialConfig ? normalizeConfig(props.initialConfig) : null;
    });
    var [loading, setLoading] = useState(!(props && props.initialConfig));
    var [error, setError] = useState("");
    var [saveState, setSaveState] = useState("idle");
    var mounted = useRef(true);
    var persistedConfig = useRef(
      props && props.initialConfig ? normalizeConfig(props.initialConfig) : null
    );

    function operationIsCurrent(options) {
      if (!mounted.current) return false;
      if (!options || typeof options.isCurrent !== "function") return true;
      try { return !!options.isCurrent(); } catch (currentError) { return false; }
    }

    function load(options) {
      options = options || {};
      if (mounted.current) {
        setLoading(true);
        setError("");
      }
      return requestJson("/api/settings/model-config").then(function (payload) {
        var next = normalizeConfig(payload);
        persistedConfig.current = next;
        if (mounted.current) {
          if (operationIsCurrent(options)) setConfig(next);
          setLoading(false);
        }
        return next;
      }).catch(function (loadError) {
        if (mounted.current) {
          if (operationIsCurrent(options)) setError(loadError.message || String(loadError));
          setLoading(false);
        }
        throw loadError;
      });
    }

    useEffect(function () {
      mounted.current = true;
      if (!(props && props.initialConfig)) load().catch(function () {});
      return function () { mounted.current = false; };
    }, []);

    function saveOperations(operations, dispatchRoutes, options) {
      options = options || {};
      operations = Array.isArray(operations) ? operations : [];
      if (!operations.length) {
        return Promise.resolve(persistedConfig.current || config || normalizeConfig({}));
      }
      if (mounted.current) {
        setSaveState("saving");
        if (operationIsCurrent(options)) setError("");
      }
      return requestJson("/api/settings/model-config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_hash: String(persistedConfig.current && persistedConfig.current.content_hash || ""),
          operations: operations,
        }),
      }).then(function (payload) {
        var saved = normalizeConfig(payload);
        persistedConfig.current = saved;
        var isCurrent = operationIsCurrent(options);
        if (mounted.current) setSaveState("idle");
        if (isCurrent) {
          setConfig(saved);
          if (!options.silentSuccess) showSettingsToast(label(props, "settings.modelConfigSaved", "Saved"), "success");
          if (props && typeof props.onConfigChange === "function") props.onConfigChange(saved);
          if (dispatchRoutes) {
            try {
              window.dispatchEvent(new CustomEvent("cyrene:model-configuration-changed", {
                detail: { profiles: saved.profiles, routes: saved.routes },
              }));
            } catch (eventError) {}
          }
        }
        return saved;
      }).catch(function (saveError) {
        if (!mounted.current) throw saveError;
        setSaveState("idle");
        if (!operationIsCurrent(options) || options.surfaceErrors === false) throw saveError;
        setError(saveError.message || String(saveError));
        showSettingsToast(saveError.message || String(saveError), "error");
        throw saveError;
      });
    }

    function save(nextConfig, dispatchRoutes, options) {
      var draft = normalizeConfig(nextConfig || config || {});
      var operations = modelConfigurationPatchOperations(
        persistedConfig.current || config || {},
        draft
      );
      return saveOperations(operations, dispatchRoutes, options);
    }

    return { config: config, setConfig: setConfig, loading: loading, error: error, setError: setError, saveState: saveState, load: load, save: save, saveOperations: saveOperations };
  }

  function LoadingState(props) {
    return h("div", { className: "wb-mcfg-state", role: "status", "aria-live": "polite" },
      h("span", { className: "wb-mcfg-spinner", "aria-hidden": "true" }),
      h("strong", null, label(props, "settings.modelConfigLoading", "Loading model configuration…")),
      h("span", null, label(props, "settings.modelConfigLoadingHint", "Reading providers, model profiles, and routes."))
    );
  }

  function ErrorState(props) {
    return h("div", { className: "wb-mcfg-state is-error", role: "alert" },
      h("strong", null, label(props, "settings.modelConfigLoadFailed", "Could not load model configuration")),
      h("span", null, props.error),
      h("button", { type: "button", className: "wb-btn", onClick: props.onRetry }, label(props, "common.retry", "Retry"))
    );
  }

  function Toggle(props) {
    return h("button", {
      type: "button",
      className: "wb-mcfg-toggle" + (props.checked ? " is-on" : ""),
      role: "switch",
      "aria-checked": props.checked ? "true" : "false",
      "aria-label": props.label,
      disabled: props.disabled,
      onClick: function () { props.onChange(!props.checked); },
    }, h("span", { "aria-hidden": "true" }));
  }

  function Field(props) {
    return h("label", { className: "wb-mcfg-field" + (props.wide ? " is-wide" : "") },
      h("span", { className: "wb-mcfg-field-label" }, props.label),
      props.children,
      props.hint ? h("small", null, props.hint) : null
    );
  }

  function capabilityLabel(capability, props) {
    var normalized = String(capability || "").trim().toLowerCase();
    if (["chat", "completion", "text"].indexOf(normalized) >= 0) return label(props, "settings.modelCapability.chat", "Chat");
    if (["vision", "image", "multimodal"].indexOf(normalized) >= 0) return label(props, "settings.modelCapability.vision", "Vision");
    if (["embedding", "embeddings"].indexOf(normalized) >= 0) return label(props, "settings.modelCapability.embedding", "Embedding");
    if (normalized === "tools") return label(props, "settings.modelCapability.tools", "Tools");
    if (normalized === "reasoning") return label(props, "settings.modelCapability.reasoning", "Reasoning");
    return String(capability || "");
  }

  function capabilityText(profile, props) {
    var values = normalizedCapabilities(profile && profile.capabilities);
    var translated = (values.length ? values : ["chat"]).map(function (capability) {
      return capabilityLabel(capability, props);
    });
    return translated.filter(function (value, index) { return translated.indexOf(value) === index; }).join(" · ");
  }

  function formatContext(profile) {
    var raw = profile && profile.context_limit;
    if (raw == null || raw === "") return "—";
    var value = Number(raw);
    if (!isFinite(value)) return String(raw);
    if (value >= 1000000) return (Math.round(value / 100000) / 10) + "M";
    if (value >= 1000) return (Math.round(value / 100) / 10) + "K";
    return String(value);
  }

  function connectionAdapter(connection) {
    return String(connection && connection.adapter || "").toLowerCase();
  }

  function isCodexConnection(connection) {
    var adapter = connectionAdapter(connection);
    return adapter === "codex_oauth";
  }

  function isLocalConnection(connection) {
    var adapter = connectionAdapter(connection);
    return adapter === "local_onnx";
  }

  function connectionProviderIcon(connection) {
    var preset = adapterKey(connection && connection.options && connection.options.provider_preset);
    var presetIcons = {
      openai: "openai",
      codex_oauth: "openai",
      anthropic: "anthropic",
      gemini: "gemini",
      deepseek: "deepseek",
      minimax: "minimax",
      kimi: "kimi",
      glm: "glm",
      opencode_go: "opencode",
      openrouter: "openrouter",
      aliyun_bailian: "aliyun",
      amd_gpu_cloud: "amd",
      ollama: "ollama",
      local_onnx: "onnx",
      onnx: "onnx",
    };
    // The adapter is only a wire protocol. A user-created endpoint speaking
    // OpenAI/Anthropic/Gemini must not inherit that vendor's brand. Brand marks
    // are reserved for connections explicitly created as provider presets.
    // Local ONNX predates provider presets, so retain its established icon for
    // persisted connections whose options object is still empty.
    return presetIcons[preset] || (isLocalConnection(connection) ? "onnx" : "");
  }

  function connectionProviderMark(connection) {
    var icon = connectionProviderIcon(connection);
    return icon ? providerGlyph(icon, 19) : settingsGlyph("server", 17);
  }

  function modelPluginForConnection(config, connection) {
    var plugins = listFrom(config && config.model_plugins);
    if (!plugins.length || !connection) return null;
    var preset = adapterKey(connection.options && connection.options.provider_preset);
    if (preset) {
      var exact = plugins.find(function (plugin) { return adapterKey(plugin && plugin.id) === preset; });
      if (exact) return exact;
    }
    var adapter = adapterKey(connection.adapter);
    var fallback = adapter === "openai_responses"
      ? "openai"
      : adapter === "openai" || adapter === "openai_compatible"
        ? "openai_compatible"
        : adapter;
    return plugins.find(function (plugin) { return adapterKey(plugin && plugin.id) === fallback; }) || null;
  }

  function connectionDisplayName(connection, props) {
    var name = String(connection && connection.name || "").trim();
    if (isLocalConnection(connection) && (!name || /^local onnx$/i.test(name))) return label(props, "settings.localModels", "Local models");
    return name || label(props, "settings.unnamedProvider", "Unnamed provider");
  }

  function mergeDiscoveredProfiles(config, connectionId, rawItems) {
    var current = (config.profiles || []).slice();
    var byIdentity = {};
    current.forEach(function (profile, index) {
      byIdentity[profile.connection_id + "\n" + profile.model] = index;
    });
    listFrom(rawItems).forEach(function (item, index) {
      var remoteId = String(item && (item.model || item.model_id || item.slug || item.id || item.name) || "").trim();
      if (!remoteId) return;
      var identity = connectionId + "\n" + remoteId;
      var normalized = normalizeProfile(Object.assign({}, item, {
        id: item.profile_id || safeId("profile", connectionId + "-" + remoteId),
        connection_id: connectionId,
        model: remoteId,
        name: item.displayName || item.display_name || item.name || remoteId,
      }), index, connectionId);
      if (byIdentity[identity] == null) {
        byIdentity[identity] = current.length;
        current.push(normalized);
      } else {
        var old = current[byIdentity[identity]];
        current[byIdentity[identity]] = Object.assign({}, normalized, old, {
          capabilities: old.capabilities && old.capabilities.length ? old.capabilities : normalized.capabilities,
          context_limit: old.context_limit || normalized.context_limit,
        });
      }
    });
    return Object.assign({}, config, { profiles: current });
  }

  function OAuthSection(props) {
    var state = props.state || {};
    var models = Array.isArray(state.models) ? state.models : [];
    var cli = state.cli && typeof state.cli === "object" ? state.cli : null;
    var cliNeedsDownload = !!(cli && (!cli.installed || cli.broken));
    var cliDownloading = !!(cliNeedsDownload && (props.cliBusy || cli.downloading));
    var cliPercent = downloadPercent(cli);
    var [selectedModelId, setSelectedModelId] = useState("");
    useEffect(function () {
      if (!models.length) {
        setSelectedModelId("");
        return;
      }
      if (!models.some(function (item) {
        return String(item.model || item.id || item.slug || "") === selectedModelId;
      })) setSelectedModelId(String(models[0].model || models[0].id || models[0].slug || ""));
    }, [models.map(function (item) { return String(item.model || item.id || item.slug || ""); }).join("|")]);
    var selectedModel = models.find(function (item) {
      return String(item.model || item.id || item.slug || "") === selectedModelId;
    });
    return h("section", { className: "wb-mcfg-special", "aria-labelledby": "wb-mcfg-oauth-title" },
      h("div", { className: "wb-mcfg-special-head" },
        h("div", null,
          h("strong", { id: "wb-mcfg-oauth-title" }, "OpenAI Codex OAuth"),
          h("small", null, state.checking ? label(props, "settings.openaiOAuthChecking", "Checking sign-in status…") : state.connected ? label(props, "settings.openaiOAuthAccountConnected", "OpenAI account connected") : label(props, "settings.openaiOAuthHint", "Sign in through Codex. Cyrene never stores your OAuth tokens."))
        ),
        h("span", { className: "wb-mcfg-connection-state " + (state.connected ? "is-ready" : "is-off") }, state.connected ? label(props, "settings.openaiOAuthConnected", "Connected") : label(props, "settings.openaiOAuthNotConnected", "Not connected"))
      ),
      state.error && !cliNeedsDownload ? h("div", { className: "wb-mcfg-inline-error", role: "alert" }, state.error) : null,
      cliNeedsDownload ? h("div", { className: "wb-mcfg-cli-runtime" },
        h("div", { className: "wb-mcfg-cli-copy" },
          h("strong", null, label(props, "settings.codexCliRuntime", "Codex CLI runtime")),
          h("small", { className: cli.error ? "is-error" : "" }, cli.error || label(props, "settings.codexCliRequiredHint", "Signing in to OpenAI needs the Codex CLI runtime (~120 MB). It is downloaded to your local cache on first use."))
        ),
        cliDownloading
          ? h("div", { className: "wb-mcfg-cli-progress", role: "status", "aria-live": "polite" },
              h("div", null,
                h("span", null, label(props, "settings.codexCliDownloading", "Downloading Codex CLI…")),
                h("span", null, cliPercent ? cliPercent + "%" : "—")
              ),
              h("progress", { max: 100, value: cliPercent || undefined, "aria-label": label(props, "settings.codexCliDownloading", "Downloading Codex CLI…") + (cliPercent ? " " + cliPercent + "%" : "") })
            )
          : h("button", {
              type: "button",
              className: "wb-btn primary wb-mcfg-cli-download",
              disabled: !!props.busy,
              onClick: function () { props.onDownloadCli(!!cli.broken); },
            }, cli.broken
              ? label(props, "settings.codexCliRedownload", "Redownload Codex CLI")
              : label(props, "settings.codexCliDownload", "Download Codex CLI"))
      ) : null,
      state.connected && models.length ? h("label", { className: "wb-mcfg-oauth-picker" },
        h("span", null, label(props, "settings.availableModels", "Available models")),
        h("select", { className: "wb-select", value: selectedModelId, "aria-label": label(props, "settings.openaiAvailableModels", "Available OpenAI models"), onChange: function (event) { setSelectedModelId(event.target.value); } }, models.map(function (item) {
          var id = String(item.model || item.id || item.slug || "");
          return h("option", { key: id, value: id }, item.displayName || item.display_name || item.name || id);
        })),
        h("button", { type: "button", className: "wb-btn", disabled: !selectedModel, onClick: function () { props.onImportModel(selectedModel); } }, label(props, "settings.addAsModelProfile", "Add as model profile"))
      ) : null,
      (state.connected || !cliNeedsDownload) ? h("div", { className: "wb-mcfg-actions" },
        state.connected
          ? h("button", { type: "button", className: "wb-btn danger", "data-cyrene-risk": "R3", disabled: !!props.busy, onClick: props.onLogout }, props.busy === "logout" ? label(props, "settings.openaiOAuthSigningOut", "Signing out…") : label(props, "settings.openaiOAuthLogout", "Sign out"))
          : h("button", { type: "button", className: "wb-btn primary", "data-cyrene-risk": "R3", disabled: !!props.busy || state.checking, onClick: props.onLogin }, props.busy === "login" ? label(props, "settings.openaiOAuthWaiting", "Waiting for sign-in…") : label(props, "settings.openaiOAuthLogin", "Sign in to OpenAI")),
        state.connected && models.length
          ? h("button", { type: "button", className: "wb-btn", disabled: !!props.busy, onClick: props.onImportModels }, label(props, "settings.importAvailableModels", "Import {count} available models", { count: models.length }))
          : null
      ) : null
    );
  }

  function LocalModelsSection(props) {
    var models = props.models || [];
    var cv2Runtime = props.cv2Runtime || null;
    var copyById = {
      "qwen3-embedding-0.6b": ["settings.localEmbeddingTitle", "settings.localQwenName", "settings.localQwenHint", "Local embedding model", "Qwen3 Embedding 0.6B", "1024-dimensional multilingual semantic retrieval · about 626 MB"],
      "pp-ocrv6-medium": ["settings.localOcrTitle", "settings.localOcrName", "settings.localOcrHint", "Local OCR model", "PP-OCRv6 Medium OCR", "On-device OCR for Chinese, English, Japanese, and Latin scripts · about 130 MB"],
      "fireredasr2-aed-int8": ["settings.localAsrTitle", "settings.localFireRedName", "settings.localFireRedHint", "Local speech recognition", "FireRedASR2-AED INT8", "High-quality Chinese and English speech recognition · about 904 MB"],
      "kokoro-zh-en": ["settings.localTtsTitle", "settings.localKokoroName", "settings.localKokoroHint", "Local text to speech", "Kokoro 82M Chinese-English FP32", "Natural preset voices for Chinese and English · about 365 MB"],
      "zipvoice-zh-en": ["settings.localTtsTitle", "settings.localZipVoiceName", "settings.localZipVoiceHint", "Local text to speech", "ZipVoice Distill Chinese-English FP32", "On-device custom voice cloning with Vocos · about 532 MB"],
    };
    return h("section", { className: "wb-mcfg-special wb-mcfg-local-manager", "aria-labelledby": props.hideHeader ? undefined : "wb-mcfg-local-title" },
      !props.hideHeader ? h("div", { className: "wb-mcfg-special-head" },
        h("div", null,
          h("strong", { id: "wb-mcfg-local-title" }, label(props, "settings.localModels", "Local models")),
          h("small", null, label(props, "settings.localModelsStorageHint", "Model files stay on this device and can be selected for compatible uses after download."))
        ),
        h("button", { type: "button", className: "wb-mcfg-icon-btn", onClick: props.onRefresh, "aria-label": label(props, "settings.refreshLocalModels", "Refresh local models") }, browserIcon("reload", 16))
      ) : null,
      props.error ? h("div", { className: "wb-mcfg-inline-error", role: "alert" }, props.error) : null,
      !models.length && !props.error ? h("div", { className: "wb-mcfg-inline-empty" }, label(props, "settings.noLocalModels", "No local models are available to manage.")) : null,
      h("div", { className: "wb-mcfg-local-list" }, models.map(function (model) {
        var percent = downloadPercent(model);
        var busy = props.busy === model.id || model.downloading;
        var localCopy = copyById[model.id];
        var kind = model.kind || "model";
        var displayTitle = localCopy ? label(props, localCopy[0], localCopy[3]) : kind;
        var displayName = localCopy ? label(props, localCopy[1], localCopy[4]) : (model.name || model.id);
        var displayDescription = localCopy ? label(props, localCopy[2], localCopy[5]) : (model.description || kind || model.runtime || label(props, "settings.localModels", "Local models"));
        var runtime = String(model.runtime || "onnx").toLowerCase();
        var runtimeLabel = runtime === "onnx-cpu" ? "CPU" : runtime.toUpperCase();
        var runtimeClass = runtime.indexOf("cuda") >= 0 || runtime.indexOf("directml") >= 0 || runtime.indexOf("qnn") >= 0
          ? " is-cuda" : runtime.indexOf("mlx") >= 0 ? " is-mlx" : " is-onnx";
        var hasError = !model.ready && !!model.error;
        var statusText = hasError
          ? label(props, "settings.localModelError", "Error")
          : model.ready
            ? label(props, "settings.localModelActive", "Active · " + runtimeLabel, { runtime: runtimeLabel })
            : model.downloading
              ? label(props, "settings.localModelDownloading", "Downloading · " + percent + "%", { percent: percent })
              : label(props, "settings.localModelOptional", "Optional · not downloaded");
        var cv2RuntimeMissing = model.id === "pp-ocrv6-medium" && cv2Runtime && !cv2Runtime.installed;
        return h("article", { className: "wb-model-card wb-local-model wb-mcfg-local-row" + (model.ready ? " is-ready" : " is-optional"), key: model.id },
          h("span", { className: "wb-local-model-icon is-" + kind, "aria-hidden": "true" }, localModelIcon(kind)),
          h("div", { className: "wb-local-model-copy wb-mcfg-local-copy" },
            h("span", { className: "wb-local-model-heading" },
              h("strong", null, displayTitle),
              h("span", { className: "wb-local-model-name" }, displayName)
            ),
            h("small", null, displayDescription),
            model.downloading ? h("div", { className: "wb-local-model-progress wb-mcfg-progress" },
              h("progress", { max: 100, value: percent, "aria-label": label(props, "settings.downloadProgress", "Download progress: {percent}%", { percent: percent }) }),
              h("span", null, percent + "%")
            ) : null,
            cv2RuntimeMissing ? h("small", { className: "wb-local-model-runtime" + (cv2Runtime.error ? " wb-local-model-error" : "") },
              cv2Runtime.downloading
                ? label(props, "settings.ocrRuntimeDownloading", "OCR runtime: " + downloadPercent(cv2Runtime) + "%", { percent: downloadPercent(cv2Runtime) })
                : cv2Runtime.error
                  ? label(props, "settings.ocrRuntimeFailed", "OCR runtime download failed") + ": " + cv2Runtime.error
                  : label(props, "settings.ocrRuntimeBundled", "The OCR model and the OpenCV runtime are downloaded together.")
            ) : null,
            hasError ? h("small", { className: "wb-local-model-error wb-mcfg-error-text" }, localizedLocalModelError(model.error, props)) : null
          ),
          h("div", { className: "wb-local-model-actions wb-mcfg-local-actions" },
            h("span", { className: "wb-model-status" + (hasError ? " is-error" : model.ready ? " wb-runtime-badge" + runtimeClass : ""), role: "status" },
              h("span", { className: "wb-local-model-status-dot", "aria-hidden": "true" }),
              statusText
            ),
            h("button", {
              type: "button",
              className: "wb-btn compact " + (model.ready ? "danger" : "tonal"),
              disabled: busy || !!props.busy,
              onClick: function () { props.onManage(model, model.ready ? "delete" : "download"); },
              "aria-label": (model.ready ? label(props, "settings.delete", "Delete") : hasError ? label(props, "settings.retry", "Retry") : label(props, "settings.download", "Download")) + " " + displayName,
            }, model.ready ? label(props, "settings.delete", "Delete") : hasError ? label(props, "settings.retry", "Retry") : label(props, "settings.download", "Download"))
          )
        );
      }))
    );
  }

  function ModelIdCombobox(props) {
    var value = String(props.value || "");
    var options = listFrom(props.options);
    var rootRef = useRef(null);
    var inputRef = useRef(null);
    var [open, setOpen] = useState(false);
    var [filter, setFilter] = useState("");
    var [activeIndex, setActiveIndex] = useState(0);
    var needle = String(filter || "").trim().toLowerCase();
    var filtered = options.filter(function (item) {
      if (!needle) return true;
      return [item.model, item.id, item.name, item.displayName, item.display_name]
        .filter(Boolean).join(" ").toLowerCase().indexOf(needle) >= 0;
    }).slice(0, 100);
    var listId = "wb-mcfg-model-options-" + String(props.profileId || "model").replace(/[^a-zA-Z0-9_-]/g, "-");
    function choose(item) {
      var modelId = String(item && (item.model || item.id || item.name) || "").trim();
      if (!modelId) return;
      if (typeof props.onSelect === "function") props.onSelect(item);
      else props.onChange(modelId);
      setFilter("");
      setOpen(false);
      window.requestAnimationFrame(function () {
        if (inputRef.current) inputRef.current.focus();
      });
    }
    function closeFromBlur(event) {
      var next = event.relatedTarget;
      if (next && rootRef.current && rootRef.current.contains(next)) return;
      setOpen(false);
      setFilter("");
    }
    function onKeyDown(event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setOpen(true);
        setActiveIndex(function (current) { return filtered.length ? (open ? Math.min(current + 1, filtered.length - 1) : 0) : 0; });
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setOpen(true);
        setActiveIndex(function (current) { return filtered.length ? (open ? Math.max(current - 1, 0) : filtered.length - 1) : 0; });
      } else if (event.key === "Enter" && open && filtered[activeIndex]) {
        event.preventDefault();
        choose(filtered[activeIndex]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        setFilter("");
      }
    }
    return h("div", { className: "wb-mcfg-model-combobox", ref: rootRef, onBlur: closeFromBlur },
      h("div", { className: "wb-mcfg-model-combobox-control" },
        h("input", {
          ref: inputRef,
          className: "wb-input",
          value: value,
          role: "combobox",
          "aria-label": label(props, "settings.modelId", "Model ID"),
          "aria-autocomplete": "list",
          "aria-expanded": open,
          "aria-controls": open ? listId : undefined,
          "aria-activedescendant": open && filtered[activeIndex] ? listId + "-" + activeIndex : undefined,
          "aria-busy": props.loading === true,
          autoComplete: "off",
          spellCheck: false,
          onFocus: function () { setFilter(""); setActiveIndex(0); setOpen(true); },
          onChange: function (event) {
            var next = event.target.value;
            props.onChange(next);
            setFilter(next);
            setActiveIndex(0);
            setOpen(true);
          },
          onKeyDown: onKeyDown,
          placeholder: props.loading ? label(props, "settings.fetchingModels", "Fetching models…") : label(props, "settings.modelIdPlaceholder", "Enter or select a model ID"),
        }),
        h("button", {
          type: "button",
          className: "wb-mcfg-model-combobox-toggle",
          "aria-label": open ? label(props, "settings.collapseModelOptions", "Collapse model options") : label(props, "settings.expandModelOptions", "Expand model options"),
          "aria-expanded": open,
          onClick: function () { setFilter(""); setActiveIndex(0); setOpen(!open); },
        }, props.loading ? h("span", { className: "wb-spinner small" }) : settingsGlyph("arrow-down", 15))
      ),
      open ? h("div", { id: listId, className: "wb-mcfg-model-combobox-list", role: "listbox", "aria-label": label(props, "settings.availableModels", "Available models") },
        props.loading ? h("div", { className: "wb-mcfg-model-combobox-state", role: "status" }, label(props, "settings.fetchingProviderModels", "Fetching models from the provider…")) : null,
        !props.loading && props.error ? h("div", { className: "wb-mcfg-model-combobox-state is-error", role: "alert" }, props.error) : null,
        !props.loading && !props.error && !filtered.length ? h("div", { className: "wb-mcfg-model-combobox-state" }, value ? label(props, "settings.noMatchingModelUseManual", "No matches. You can continue with the model ID you entered manually.") : label(props, "settings.noModelsUseManual", "No models were returned. Enter a model ID manually.")) : null,
        !props.loading && filtered.map(function (item, index) {
          var modelId = String(item.model || item.id || item.name || "");
          var modelName = String(item.name || item.displayName || item.display_name || modelId);
          return h("button", {
            type: "button",
            id: listId + "-" + index,
            key: modelId,
            role: "option",
            className: "wb-mcfg-model-combobox-option" + (index === activeIndex ? " is-active" : "") + (modelId === value ? " is-selected" : ""),
            "aria-selected": modelId === value,
            onMouseDown: function (event) { event.preventDefault(); },
            onMouseEnter: function () { setActiveIndex(index); },
            onClick: function () { choose(item); },
          },
            h("strong", null, modelName),
            modelName !== modelId ? h("code", null, modelId) : null
          );
        })
      ) : null
    );
  }

  function ProfileEditor(props) {
    var profile = props.profile;
    var pricing = profilePriceFields(profile.price);
    var displayName = profile.name || profile.model || label(props, "settings.unnamedModel", "Unnamed model");
    var capabilities = normalizedCapabilities(profile.capabilities);
    var capabilityOptions = ["chat", "vision", "embedding"];
    var [expanded, setExpanded] = useState(
      !String(profile.model || "").trim()
      || (profile.name === "\u65b0\u6a21\u578b" && profile.model === "\u65b0\u6a21\u578b")
    );
    var detailsId = "wb-mcfg-profile-details-" + String(profile.id || "model").replace(/[^a-zA-Z0-9_-]/g, "-");
    function toggleCapability(capability) {
      var next = capabilities.indexOf(capability) >= 0
        ? capabilities.filter(function (item) { return item !== capability; })
        : capabilities.concat([capability]);
      props.onChange("capabilities", next);
    }
    return h("article", { className: "wb-mcfg-profile-card" + (expanded ? " is-expanded" : "") },
      h("button", {
        type: "button",
        className: "wb-mcfg-profile-summary",
        "aria-expanded": expanded,
        "aria-controls": detailsId,
        onClick: function () { setExpanded(!expanded); },
      },
        h("div", { className: "wb-mcfg-profile-identity" },
          h("strong", { title: displayName }, displayName),
          h("code", { title: profile.model || "" }, profile.model || label(props, "settings.modelIdNotSet", "Model ID not set"))
        ),
        h("span", { className: "wb-btn wb-mcfg-profile-details-button", "aria-hidden": "true" }, expanded ? label(props, "common.collapse", "Collapse") : label(props, "common.details", "Details"))
      ),
      expanded ? h("div", { id: detailsId, className: "wb-mcfg-profile-details" },
        h("div", { className: "wb-mcfg-profile-details-grid" },
          h("label", { className: "wb-mcfg-profile-editor-field is-half" },
            h("span", null, label(props, "settings.displayName", "Display name")),
            h("input", { className: "wb-input", value: profile.name || "", "aria-label": label(props, "settings.modelDisplayName", "Model display name"), onChange: function (event) { props.onChange("name", event.target.value); } })
          ),
          h("div", { className: "wb-mcfg-profile-editor-field is-half" },
            h("span", null, label(props, "settings.modelCapabilities", "Model capabilities")),
            h("div", { className: "wb-mcfg-capability-picker", role: "group", "aria-label": label(props, "settings.modelCapabilities", "Model capabilities") }, capabilityOptions.map(function (capability) {
              var selected = capabilities.indexOf(capability) >= 0;
              return h("button", {
                type: "button",
                key: capability,
                className: "wb-mcfg-capability-chip" + (selected ? " is-selected" : ""),
                "aria-pressed": selected,
                onClick: function () { toggleCapability(capability); },
              }, capabilityLabel(capability, props));
            }))
          ),
          h("div", { className: "wb-mcfg-profile-editor-field is-wide" },
            h("span", null, label(props, "settings.modelId", "Model ID")),
            h(ModelIdCombobox, {
              profileId: profile.id,
              value: profile.model || "",
              options: props.modelOptions || [],
              loading: props.modelsLoading,
              error: props.modelsError,
              onChange: function (value) { props.onChange("model", value); },
              onSelect: props.onModelSelect,
            })
          ),
          h("label", { className: "wb-mcfg-profile-editor-field" },
            h("span", null, label(props, "settings.contextTokens", "Context (tokens)")),
            h("input", { className: "wb-input", type: "number", min: 0, inputMode: "numeric", value: profile.context_limit == null ? "" : profile.context_limit, "aria-label": label(props, "settings.contextTokenLimit", "Context token limit"), onChange: function (event) { props.onChange("context_limit", event.target.value); }, placeholder: label(props, "common.automatic", "Automatic"), title: label(props, "settings.adapterAutoDetectHint", "Token count; leave blank to let the adapter determine it automatically.") })
          ),
          h("label", { className: "wb-mcfg-profile-editor-field" },
            h("span", null, label(props, "settings.inputPrice", "Input price")),
            h("input", { className: "wb-input", type: "number", min: 0, step: "any", inputMode: "decimal", value: pricing.input, "aria-label": label(props, "settings.modelInputPrice", "Model input price"), onChange: function (event) { props.onChange("price", updateProfilePriceField(profile.price, "input", event.target.value)); }, placeholder: "0", title: label(props, "settings.pricePerMillionHint", "Price per million tokens; CNY by default.") })
          ),
          h("label", { className: "wb-mcfg-profile-editor-field" },
            h("span", null, label(props, "settings.outputPrice", "Output price")),
            h("input", { className: "wb-input", type: "number", min: 0, step: "any", inputMode: "decimal", value: pricing.output, "aria-label": label(props, "settings.modelOutputPrice", "Model output price"), onChange: function (event) { props.onChange("price", updateProfilePriceField(profile.price, "output", event.target.value)); }, placeholder: "0", title: label(props, "settings.pricePerMillionHint", "Price per million tokens; CNY by default.") })
          ),
          h("label", { className: "wb-mcfg-profile-editor-field" },
            h("span", null, label(props, "settings.cachePrice", "Cache price")),
            h("input", { className: "wb-input", type: "number", min: 0, step: "any", inputMode: "decimal", value: pricing.cache, "aria-label": label(props, "settings.modelCachePrice", "Model cache price"), onChange: function (event) { props.onChange("price", updateProfilePriceField(profile.price, "cache", event.target.value)); }, placeholder: "0", title: label(props, "settings.pricePerMillionHint", "Price per million tokens; CNY by default.") })
          )
        ),
        h("div", { className: "wb-mcfg-profile-details-actions" }, props.draft
          ? h(React.Fragment, null,
              h("button", { type: "button", className: "wb-btn primary", disabled: !String(profile.model || "").trim(), onClick: props.onCommit }, label(props, "common.save", "Save")),
              h("button", { type: "button", className: "wb-btn", onClick: props.onRemove }, label(props, "common.cancel", "Cancel"))
            )
          : h(React.Fragment, null,
              h("button", { type: "button", className: "wb-btn", disabled: !!props.testing || !String(profile.model || "").trim(), onClick: props.onTest }, props.testing ? label(props, "settings.testingConnection", "Testing…") : label(props, "settings.testConnection", "Test connection")),
              h("button", { type: "button", className: "wb-btn danger", onClick: props.onRemove }, label(props, "settings.deleteModel", "Delete model"))
            )
        )
      ) : null
    );
  }

  function ServicesPage(props) {
    props = props || {};
    var store = useModelConfiguration(props);
    var config = store.config;
    var [selectedId, setSelectedId] = useState("");
    var [query, setQuery] = useState("");
    var [dirty, setDirty] = useState(false);
    var [saveError, setSaveError] = useState("");
    var editVersion = useRef(0);
    var dirtyRef = useRef(false);
    var queuedSnapshot = useRef(null);
    var queuedOperations = useRef([]);
    var queuedVersion = useRef(0);
    var saveQueueTimer = useRef(null);
    var saveQueueInFlight = useRef(false);
    var saveQueueBlockedVersion = useRef(-1);
    var saveQueueMounted = useRef(true);
    var [busy, setBusy] = useState("");
    var [connectionMenu, setConnectionMenu] = useState(null);
    var connectionMenuRef = useRef(null);
    var connectionMenuReturnFocus = useRef(null);
    var [oauth, setOauth] = useState({ checking: false, connected: false, models: [] });
    var [oauthBusy, setOauthBusy] = useState("");
    var oauthPoll = useRef(null);
    var oauthCliPoll = useRef(null);
    var oauthCliStartedAt = useRef(0);
    var [localModels, setLocalModels] = useState([]);
    var [localRuntime, setLocalRuntime] = useState(null);
    var [localError, setLocalError] = useState("");
    var [localBusy, setLocalBusy] = useState("");
    var [proxyMasterEnabled, setProxyMasterEnabled] = useState(false);
    var [modelDiscovery, setModelDiscovery] = useState({});
    var [profileDrafts, setProfileDrafts] = useState({});
    var discoveryRequestVersions = useRef({});
    var selected = config && (config.connections || []).find(function (item) { return item.id === selectedId; });
    useModelConfigurationLifecycle({ useEffect, config, selectedId, setSelectedId, oauthPoll, oauthCliPoll, saveQueueMounted, saveQueueTimer, dirtyRef, queuedSnapshot });
    useConnectionMenuLifecycle({ useEffect, connectionMenu, connectionMenuRef, connectionMenuReturnFocus, setConnectionMenu });
    function setQueueDirty(value) {
      dirtyRef.current = !!value;
      if (saveQueueMounted.current) setDirty(!!value);
    }

    function scheduleQueuedSave(delay) {
      if (!saveQueueMounted.current) return;
      if (saveQueueTimer.current) clearTimeout(saveQueueTimer.current);
      saveQueueTimer.current = null;
      if (saveQueueInFlight.current || !dirtyRef.current) return;
      if (saveQueueBlockedVersion.current === queuedVersion.current) return;
      saveQueueTimer.current = setTimeout(function () {
        saveQueueTimer.current = null;
        persistQueuedConfig();
      }, Math.max(0, Number(delay) || 0));
    }

    function queueErrorMessage(error) {
      return localizedModelConfigurationError(error, props) || label(props, "settings.modelConfigSaveFailed", "Could not save model configuration.");
    }

    function handleQueuedSaveFailure(error, failedOperations) {
      if (!saveQueueMounted.current) return;
      var failedVersion = queuedVersion.current;
      queuedOperations.current = (failedOperations || []).concat(queuedOperations.current || []);
      saveQueueBlockedVersion.current = failedVersion;
      setQueueDirty(true);
      var message = queueErrorMessage(error);
      setSaveError(message);
      store.setError(message);
      showSettingsToast(message, "error");
    }

    function persistQueuedConfig() {
      if (!saveQueueMounted.current || saveQueueInFlight.current || !dirtyRef.current) return;
      if (saveQueueBlockedVersion.current === queuedVersion.current) return;
      var operations = (queuedOperations.current || []).slice();
      var version = queuedVersion.current;
      if (!operations.length) return;
      queuedOperations.current = [];
      saveQueueInFlight.current = true;
      store.saveOperations(operations, true, {
        silentSuccess: true,
        surfaceErrors: false,
        isCurrent: function () {
          return saveQueueMounted.current && queuedVersion.current === version;
        },
      }).then(function (saved) {
        saveQueueInFlight.current = false;
        if (!saveQueueMounted.current) return;
        if (queuedVersion.current === version && !queuedOperations.current.length) {
          queuedSnapshot.current = saved;
          saveQueueBlockedVersion.current = -1;
          setQueueDirty(false);
          setSaveError("");
          store.setError("");
          return;
        }
        scheduleQueuedSave(0);
      }).catch(function (error) {
        saveQueueInFlight.current = false;
        handleQueuedSaveFailure(error, operations);
      });
    }

    function updateConfig(next, options) {
      options = options || {};
      var previous = queuedSnapshot.current || config || {};
      var snapshot = next;
      var operations = modelConfigurationPatchOperations(previous, snapshot);
      if (!operations.length) return queuedVersion.current;
      editVersion.current += 1;
      queuedVersion.current = editVersion.current;
      queuedOperations.current = queuedOperations.current.concat(operations);
      queuedSnapshot.current = snapshot;
      saveQueueBlockedVersion.current = -1;
      setQueueDirty(true);
      store.setConfig(snapshot);
      scheduleQueuedSave(options.immediate ? 0 : 600);
      return queuedVersion.current;
    }

    function retryQueuedSave() {
      if (!dirtyRef.current || saveQueueInFlight.current) return;
      saveQueueBlockedVersion.current = -1;
      scheduleQueuedSave(0);
    }

    function updateConnection(key, value) {
      if (key === "use_proxy" && value === true) setProxyMasterEnabled(true);
      updateConfig(Object.assign({}, config, {
        connections: config.connections.map(function (connection) {
          if (connection.id !== selected.id) return connection;
          var next = Object.assign({}, connection);
          next[key] = value;
          if (key === "adapter") {
            var previousAdapter = (config.adapters || []).find(function (item) { return item.id === connection.adapter; }) || {};
            var replacementAdapter = (config.adapters || []).find(function (item) { return item.id === value; }) || {};
            var previousManagedName = connection.adapter === "codex_oauth"
              ? "OpenAI Codex OAuth" : connection.adapter === "local_onnx" ? "Local ONNX" : "";
            var managedName = value === "codex_oauth"
              ? "OpenAI Codex OAuth" : value === "local_onnx" ? "Local ONNX" : "";
            if (managedName && (next.name === label(props, "settings.newProvider", "New provider") || next.name === "\u65b0\u670d\u52a1\u5546" || next.name === previousManagedName)) next.name = managedName;
            if (!String(next.base_url || "").trim()
                || String(next.base_url || "").replace(/\/$/, "") === String(previousAdapter.default_base_url || "").replace(/\/$/, "")) {
              next.base_url = String(replacementAdapter.default_base_url || "");
            }
            next.secret = "";
            next.secret_configured = false;
            if (connection.secret_configured || String(connection.secret || "").trim()) next.clear_api_key = true;
          }
          if (key === "secret" && String(value || "").trim()) {
            delete next.clear_api_key;
            delete next.clear_secret;
          }
          return next;
        }),
      }), { immediate: key === "use_proxy" && value === true });
    }

    function matchesConnectionQuery(connection) {
      var needle = String(query || "").trim().toLowerCase();
      if (!needle) return true;
      var adapter = (config.adapters || []).find(function (item) { return item.id === connection.adapter; }) || {};
      var modelTerms = (config.profiles || []).filter(function (profile) {
        return profile.connection_id === connection.id;
      }).map(function (profile) {
        return [profile.name, profile.model].filter(Boolean).join(" ");
      });
      return [
        connectionDisplayName(connection, props), connection.name, connection.id,
        connection.adapter, adapter.name, connection.base_url,
      ].concat(modelTerms).filter(Boolean).join(" ").toLowerCase().indexOf(needle) >= 0;
    }

    function addConnection() {
      var firstAdapter = selectableConnectionAdapters()[0];
      if (!firstAdapter) {
        showSettingsToast(label(props, "settings.adapterUnavailable", "No adapters are available for this service."), "error");
        return;
      }
      var id = safeId("connection", "new-" + Date.now());
      var connection = normalizeConnection({
        id: id,
        name: label(props, "settings.newProvider", "New provider"),
        adapter: firstAdapter.id,
        base_url: firstAdapter.default_base_url || "",
        enabled: true,
      }, config.connections.length);
      updateConfig(Object.assign({}, config, { connections: config.connections.concat([connection]) }));
      setSelectedId(id);
    }

    function selectableConnectionAdapters() {
      var connections = config.connections || [];
      return (config.adapters || []).filter(function (adapter) {
        if (isUserSelectableAdapter(adapter)) return true;
        var adapterId = adapterKey(adapter && adapter.id);
        if (adapterId === "codex_oauth") return !connections.some(isCodexConnection);
        if (adapterId === "local_onnx") return !connections.some(isLocalConnection);
        return false;
      });
    }

    function removeConnection(connectionId, returnFocus) {
      var targetIndex = config.connections.findIndex(function (connection) { return connection.id === connectionId; });
      var target = config.connections[targetIndex];
      if (!target) return;
      var feedback = workbenchServices.feedback();
      if (!feedback || typeof feedback.confirmModal !== "function") {
        showSettingsToast(label(props, "settings.deleteConfirmationUnavailable", "The delete confirmation is temporarily unavailable. Try again later."), "error");
        if (returnFocus && returnFocus.isConnected) window.requestAnimationFrame(function () { returnFocus.focus(); });
        return;
      }
      feedback.confirmModal({
        title: label(props, "settings.deleteModelServiceTitle", "Delete model service?"),
        body: label(props, "settings.deleteModelServiceBody", "{name} and its model profiles will be deleted and the change will be saved immediately.", { name: connectionDisplayName(target, props) }),
        confirmLabel: label(props, "common.delete", "Delete"),
        cancelLabel: label(props, "common.cancel", "Cancel"),
        danger: true,
      }).then(function (confirmed) {
        if (!confirmed) {
          if (returnFocus && returnFocus.isConnected) window.requestAnimationFrame(function () { returnFocus.focus(); });
          return;
        }
        var baseConfig = queuedSnapshot.current || config;
        var currentTargetIndex = baseConfig.connections.findIndex(function (connection) { return connection.id === target.id; });
        if (currentTargetIndex < 0) return;
        var profileIds = baseConfig.profiles.filter(function (profile) { return profile.connection_id === target.id; }).map(function (profile) { return profile.id; });
        var nextRoutes = {};
        Object.keys(baseConfig.routes).forEach(function (route) {
          nextRoutes[route] = baseConfig.routes[route].filter(function (id) { return profileIds.indexOf(id) < 0; });
        });
        var remaining = baseConfig.connections.filter(function (connection) { return connection.id !== target.id; });
        var nextConfig = Object.assign({}, baseConfig, {
          connections: remaining,
          profiles: baseConfig.profiles.filter(function (profile) { return profile.connection_id !== target.id; }),
          routes: nextRoutes,
        });
        updateConfig(nextConfig, { immediate: true });
        setProfileDrafts(function (previous) {
          if (!previous[target.id]) return previous;
          var next = Object.assign({}, previous);
          delete next[target.id];
          return next;
        });
        if (selectedId === target.id) {
          var visibleBefore = baseConfig.connections.filter(matchesConnectionQuery);
          var visibleIndex = visibleBefore.findIndex(function (connection) { return connection.id === target.id; });
          var visibleRemaining = remaining.filter(matchesConnectionQuery);
          var adjacent = visibleRemaining[Math.min(Math.max(visibleIndex, 0), visibleRemaining.length - 1)]
            || remaining[Math.min(currentTargetIndex, remaining.length - 1)] || remaining[0];
          setSelectedId(adjacent ? adjacent.id : "");
        }
      });
    }

    function openConnectionMenu(event, connection, fromKeyboard) {
      event.preventDefault();
      event.stopPropagation();
      if (isCodexConnection(connection)) {
        setConnectionMenu(null);
        return;
      }
      var trigger = event.currentTarget;
      var rect = trigger.getBoundingClientRect();
      var menuWidth = 176;
      var menuHeight = 52;
      var margin = 8;
      var desiredLeft = fromKeyboard ? rect.left + 18 : event.clientX;
      var desiredTop = fromKeyboard ? rect.top + Math.min(rect.height - 8, 38) : event.clientY;
      var portalTheme = {};
      if (typeof getComputedStyle === "function") {
        var computed = getComputedStyle(trigger);
        [
          "--wb-card-bg", "--wb-surface", "--wb-main-bg", "--wb-control-bg", "--wb-control-hover-bg",
          "--wb-row-hover-bg", "--wb-line", "--wb-line-2", "--wb-text", "--wb-muted", "--wb-faint",
          "--wb-accent", "--wb-red", "--wb-error-text", "--wb-error-bg",
          "--wb-ui-font-scale", "--wb-font", "--mcfg-border", "--mcfg-surface-strong",
        ].forEach(function (name) { portalTheme[name] = computed.getPropertyValue(name); });
        portalTheme.fontFamily = computed.fontFamily;
        portalTheme.colorScheme = computed.colorScheme;
      }
      connectionMenuReturnFocus.current = trigger;
      setConnectionMenu({
        connectionId: connection.id,
        left: Math.max(margin, Math.min(desiredLeft, window.innerWidth - menuWidth - margin)),
        top: Math.max(margin, Math.min(desiredTop, window.innerHeight - menuHeight - margin)),
        portalTheme: portalTheme,
      });
    }

    function addProfile(raw) {
      if (!selected) return;
      var profile = normalizeProfile(Object.assign({
        id: safeId("profile", selected.id + "-new-" + Date.now()),
        connection_id: selected.id,
        name: label(props, "settings.newModel", "New model"),
        model: "",
        capabilities: ["chat"],
      }, raw || {}), config.profiles.length, selected.id);
      setProfileDrafts(function (previous) {
        return Object.assign({}, previous, { [selected.id]: profile });
      });
    }

    function updateProfileDraft(key, value) {
      if (!selected) return;
      setProfileDrafts(function (previous) {
        var draft = previous[selected.id];
        if (!draft) return previous;
        return Object.assign({}, previous, {
          [selected.id]: Object.assign({}, draft, { [key]: value }),
        });
      });
    }

    function cancelProfileDraft() {
      if (!selected) return;
      setProfileDrafts(function (previous) {
        if (!previous[selected.id]) return previous;
        var next = Object.assign({}, previous);
        delete next[selected.id];
        return next;
      });
    }

    function commitProfileDraft() {
      if (!selected) return;
      var draft = profileDrafts[selected.id];
      if (!draft || !String(draft.model || "").trim()) return;
      var committed = normalizeProfile(draft, config.profiles.length, selected.id);
      updateConfig(Object.assign({}, config, {
        profiles: config.profiles.concat([committed]),
      }), { immediate: true });
      cancelProfileDraft();
    }

    function updateProfile(profileId, key, value) {
      updateConfig(Object.assign({}, config, {
        profiles: config.profiles.map(function (profile) {
          if (profile.id !== profileId) return profile;
          var next = Object.assign({}, profile);
          next[key] = value;
          return next;
        }),
      }));
    }

    function applyDiscoveredModel(profileId, item) {
      var modelId = String(item && (item.model || item.id || item.name) || "").trim();
      if (!modelId) return;
      updateConfig(Object.assign({}, config, {
        profiles: config.profiles.map(function (profile) {
          if (profile.id !== profileId) return profile;
          var next = Object.assign({}, profile, {
            model: modelId,
          });
          var discoveredName = String(item.name || item.displayName || item.display_name || modelId).trim();
          if (!String(profile.name || "").trim() || profile.name === label(props, "settings.newModel", "New model") || profile.name === "\u65b0\u6a21\u578b" || profile.name === profile.model) next.name = discoveredName;
          var capabilities = normalizedCapabilities(item.capabilities || item.supports || item.modalities);
          if (capabilities.length) next.capabilities = capabilities;
          var contextLimit = item.context_limit != null ? item.context_limit : item.ctx_limit;
          if ((profile.context_limit == null || profile.context_limit === "") && contextLimit != null) {
            next.context_limit = contextLimit;
          }
          if (!profile.dimensions && item.dimensions) next.dimensions = item.dimensions;
          return next;
        }),
      }));
    }

    function applyDiscoveredModelToDraft(item) {
      var modelId = String(item && (item.model || item.id || item.name) || "").trim();
      if (!selected || !modelId) return;
      setProfileDrafts(function (previous) {
        var profile = previous[selected.id];
        if (!profile) return previous;
        var next = Object.assign({}, profile, { model: modelId });
        var discoveredName = String(item.name || item.displayName || item.display_name || modelId).trim();
        if (!String(profile.name || "").trim() || profile.name === label(props, "settings.newModel", "New model") || profile.name === "\u65b0\u6a21\u578b" || profile.name === profile.model) next.name = discoveredName;
        var capabilities = normalizedCapabilities(item.capabilities || item.supports || item.modalities);
        if (capabilities.length) next.capabilities = capabilities;
        var contextLimit = item.context_limit != null ? item.context_limit : item.ctx_limit;
        if ((profile.context_limit == null || profile.context_limit === "") && contextLimit != null) next.context_limit = contextLimit;
        if (!profile.dimensions && item.dimensions) next.dimensions = item.dimensions;
        return Object.assign({}, previous, { [selected.id]: next });
      });
    }

    function discoverConnection(options) {
      options = options || {};
      var connection = selected;
      if (!connection || isLocalConnection(connection)) return Promise.resolve([]);
      var adapter = (config.adapters || []).find(function (item) { return item.id === connection.adapter; }) || {};
      var requiresKey = String(adapter.auth_type || "api_key") === "api_key";
      var hasKey = connection.secret_configured === true || !!String(connection.secret || "").trim();
      if (requiresKey && !hasKey && !options.force) return Promise.resolve([]);
      var connectionId = connection.id;
      var requestVersion = Number(discoveryRequestVersions.current[connectionId] || 0) + 1;
      discoveryRequestVersions.current[connectionId] = requestVersion;
      setModelDiscovery(function (previous) {
        var next = Object.assign({}, previous);
        next[connectionId] = Object.assign({}, previous[connectionId] || {}, { loading: true, error: "" });
        return next;
      });
      return requestJson("/api/settings/model-config/connections/" + encodeURIComponent(connectionId) + "/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ connection: connectionDraftPayload(connection) }),
      }).then(function (payload) {
        var models = listFrom(payload.models);
        if (requestVersion !== discoveryRequestVersions.current[connectionId]) return models;
        setModelDiscovery(function (previous) {
          var next = Object.assign({}, previous);
          next[connectionId] = { loading: false, error: "", loaded: true, models: models };
          return next;
        });
        if (options.notify) showSettingsToast(label(props, "settings.fetchedModelCount", "Available models fetched: {count}.", { count: models.length }), "success");
        return models;
      }).catch(function (error) {
        if (requestVersion !== discoveryRequestVersions.current[connectionId]) return [];
        var message = localizedModelConfigurationError(error, props);
        setModelDiscovery(function (previous) {
          var next = Object.assign({}, previous);
          next[connectionId] = Object.assign({}, previous[connectionId] || {}, { loading: false, loaded: true, error: message, models: [] });
          return next;
        });
        if (options.notify) showSettingsToast(message, "error");
        return [];
      });
    }

    function removeProfile(profileId) {
      var routes = {};
      Object.keys(config.routes).forEach(function (route) {
        routes[route] = config.routes[route].filter(function (id) { return id !== profileId; });
      });
      updateConfig(Object.assign({}, config, {
        profiles: config.profiles.filter(function (profile) { return profile.id !== profileId; }),
        routes: routes,
      }));
    }

    function testProfile(profile) {
      if (!selected || !profile) return;
      var busyKey = "test:" + profile.id;
      setBusy(busyKey);
      store.setError("");
      requestJson("/api/settings/model-config/connections/" + encodeURIComponent(selected.id) + "/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ connection: connectionDraftPayload(selected), profile: profile }),
      }).then(function (payload) {
        showSettingsToast(String(payload.message || payload.detail || label(props, "settings.modelTestSucceeded", "Model test succeeded.")), "success");
      }).catch(function (error) {
        var message = localizedModelConfigurationError(error, props);
        store.setError(message);
        showSettingsToast(message, "error");
      }).finally(function () { setBusy(function (current) { return current === busyKey ? "" : current; }); });
    }

    function refreshOauth() {
      setOauth(function (previous) { return Object.assign({}, previous, { checking: true, error: "" }); });
      return requestJson("/api/settings/openai-oauth").then(function (payload) {
        setOauth(Object.assign({}, payload, { checking: false, models: payload.models || [] }));
        if (payload.cli && payload.cli.downloading) startOauthCliPolling();
        try { window.dispatchEvent(new CustomEvent("cyrene:codex-auth-changed", { detail: payload })); } catch (error) {}
        return payload;
      }).catch(function (error) {
        setOauth({ checking: false, connected: false, models: [], error: error.message || String(error) });
      });
    }

    function stopOauthCliPolling() {
      if (oauthCliPoll.current) clearInterval(oauthCliPoll.current);
      oauthCliPoll.current = null;
      oauthCliStartedAt.current = 0;
    }

    function setOauthCliError(message) {
      setOauth(function (previous) {
        return Object.assign({}, previous, {
          cli: Object.assign({}, previous.cli || {}, {
            downloading: false,
            error: String(message || ""),
          }),
        });
      });
    }

    function pollOauthCli() {
      if (oauthCliStartedAt.current && Date.now() - oauthCliStartedAt.current >= 600000) {
        stopOauthCliPolling();
        setOauthBusy("");
        setOauthCliError(label(props, "settings.codexCliDownloadTimeout", "Codex CLI download timed out. Please retry."));
        return;
      }
      requestJson("/api/settings/openai-oauth/cli").then(function (cli) {
        setOauth(function (previous) { return Object.assign({}, previous, { cli: cli }); });
        if (cli.downloading || (!cli.installed && !cli.error)) return;
        stopOauthCliPolling();
        setOauthBusy("");
        if (cli.installed) {
          showSettingsToast(label(props, "settings.codexCliReady", "Codex CLI is ready — you can sign in now."), "success");
          refreshOauth();
        }
      }).catch(function (error) {
        stopOauthCliPolling();
        setOauthBusy("");
        setOauthCliError(error.message || String(error));
      });
    }

    function startOauthCliPolling() {
      if (oauthCliPoll.current) return;
      oauthCliStartedAt.current = Date.now();
      oauthCliPoll.current = setInterval(pollOauthCli, 1000);
    }

    function downloadOauthCli(force) {
      setOauthBusy("cli");
      setOauth(function (previous) {
        return Object.assign({}, previous, {
          cli: Object.assign({}, previous.cli || {}, { downloading: true, error: "" }),
        });
      });
      var init = { method: "POST" };
      if (force) {
        init.headers = { "Content-Type": "application/json" };
        init.body = JSON.stringify({ force: true });
      }
      requestJson("/api/settings/openai-oauth/cli/download", init).then(function (cli) {
        setOauth(function (previous) { return Object.assign({}, previous, { cli: cli }); });
        if (cli.installed && !cli.broken) {
          setOauthBusy("");
          showSettingsToast(label(props, "settings.codexCliReady", "Codex CLI is ready — you can sign in now."), "success");
          return refreshOauth();
        }
        startOauthCliPolling();
      }).catch(function (error) {
        setOauthBusy("");
        setOauthCliError(error.message || String(error));
      });
    }

    function startOauthLogin() {
      setOauthBusy("login");
      requestJson("/api/settings/openai-oauth/login", { method: "POST" }).then(function (payload) {
        var url = payload.authUrl || payload.auth_url || payload.url;
        if (url) window.open(url, "_blank", "noopener,noreferrer");
        if (oauthPoll.current) clearInterval(oauthPoll.current);
        var count = 0;
        oauthPoll.current = setInterval(function () {
          count += 1;
          refreshOauth().then(function (next) {
            if (next && next.connected || count >= 80) {
              clearInterval(oauthPoll.current);
              oauthPoll.current = null;
              setOauthBusy("");
            }
          });
        }, 1500);
      }).catch(function (error) {
        setOauthBusy("");
        setOauth(function (previous) { return Object.assign({}, previous, { error: error.message || String(error) }); });
      });
    }

    function logoutOauth() {
      setOauthBusy("logout");
      requestJson("/api/settings/openai-oauth/logout", { method: "POST" }).then(function () {
        return refreshOauth();
      }).catch(function (error) {
        setOauth(function (previous) { return Object.assign({}, previous, { error: error.message || String(error) }); });
      }).finally(function () { setOauthBusy(""); });
    }

    function importOauthModels() {
      updateConfig(mergeDiscoveredProfiles(config, selected.id, oauth.models || []));
      showSettingsToast(label(props, "settings.codexModelsImported", "Codex models imported: {count}; saving automatically.", { count: (oauth.models || []).length }), "success");
    }

    function importOauthModel(model) {
      updateConfig(mergeDiscoveredProfiles(config, selected.id, model ? [model] : []));
      showSettingsToast(label(props, "settings.codexModelProfileAdded", "Added the Codex model profile; saving automatically."), "success");
    }

    function refreshLocalModels() {
      setLocalError("");
      return requestJson("/api/settings/local-models/status").then(function (payload) {
        setLocalModels(payload.models || []);
        setLocalRuntime(payload.cv2_runtime || null);
      }).catch(function (error) { setLocalError(error.message || String(error)); });
    }

    function manageLocalModel(model, action) {
      setLocalBusy(model.id);
      var request = requestJson("/api/settings/local-models/" + encodeURIComponent(model.id) + (action === "download" ? "/download" : ""), { method: action === "download" ? "POST" : "DELETE" });
      var runtime = action === "download" && model.id === "pp-ocrv6-medium"
        ? requestJson("/api/settings/local-models/ocr-runtime/download", { method: "POST" }).catch(function () {})
        : Promise.resolve();
      Promise.all([request, runtime]).then(function () { return refreshLocalModels(); }).catch(function (error) {
        setLocalError(error.message || String(error));
      }).finally(function () { setLocalBusy(""); });
    }

    useEffect(function () {
      if (!selected || !isCodexConnection(selected)) return;
      refreshOauth();
    }, [selectedId, selected && selected.adapter]);

    useEffect(function () {
      if (!selected || isCodexConnection(selected) || isLocalConnection(selected)) return;
      var adapter = (config.adapters || []).find(function (item) { return item.id === selected.adapter; }) || {};
      if (adapter.supports_discovery === false) return;
      if (!String(selected.base_url || "").trim()) return;
      var requiresKey = String(adapter.auth_type || "api_key") === "api_key";
      if (requiresKey && !selected.secret_configured && !String(selected.secret || "").trim()) return;
      var timer = setTimeout(function () { discoverConnection(); }, 550);
      return function () { clearTimeout(timer); };
    }, [
      selectedId,
      selected && selected.adapter,
      selected && selected.base_url,
      selected && selected.secret,
      selected && selected.secret_configured,
      selected && selected.use_proxy,
    ]);

    useEffect(function () { refreshLocalModels(); }, []);

    useEffect(function () {
      var active = true;
      requestJson("/api/settings/config").then(function (payload) {
        if (active) setProxyMasterEnabled(payload.external_agent_proxy_enabled === true);
      }).catch(function () {});
      return function () { active = false; };
    }, []);

    useEffect(function () {
      if (!selected || !isLocalConnection(selected) || !localModels.some(function (model) { return model.downloading; })) return;
      var timer = setInterval(refreshLocalModels, 1500);
      return function () { clearInterval(timer); };
    }, [selectedId, localModels.some(function (model) { return model.downloading; })]);

    if (store.loading) return h("div", { className: "settings-panel settings-panel-wide wb-model-config-page" }, h(LoadingState, props));
    if (!config) return h("div", { className: "settings-panel settings-panel-wide wb-model-config-page" }, h(ErrorState, Object.assign({}, props, { error: store.error, onRetry: store.load })));

    var filtered = config.connections.filter(matchesConnectionQuery);
    var profiles = selected ? config.profiles.filter(function (profile) { return profile.connection_id === selected.id; }) : [];
    var profileDraft = selected ? profileDrafts[selected.id] || null : null;
    var discovery = selected ? (modelDiscovery[selected.id] || { loading: false, loaded: false, error: "", models: [] }) : null;
    var adapters = config.adapters || [];
    var selectableAdapters = selectableConnectionAdapters();
    var currentAdapter = selected && (adapters.find(function (adapter) { return adapter.id === selected.adapter; })
      || normalizeAdapter({ id: selected.adapter, name: selected.adapter }, adapters.length));
    var selectedModelPlugin = modelPluginForConnection(config, selected);
    var editorAdapters = selectableAdapters.slice();
    if (currentAdapter && !editorAdapters.some(function (adapter) { return adapter.id === currentAdapter.id; })) {
      editorAdapters.unshift(currentAdapter);
    }
    var selectedDescription = selected
      ? (isLocalConnection(selected)
        ? label(props, "settings.localModelsHint", "Optional, private on-device enhancements. Downloads are explicit and resumable.")
        : selectedModelPlugin
          ? label(props, "settings.modelPluginProvidedBy", "Model discovery and calls are provided by the editable model plugin {name}.", { name: String(selectedModelPlugin.plugin_name || selectedModelPlugin.name || "").trim() })
          : String(currentAdapter && currentAdapter.description || "").trim())
      : "";
    var menuConnection = connectionMenu && config.connections.find(function (connection) { return connection.id === connectionMenu.connectionId; });
    var connectionMenuNode = menuConnection ? h("div", {
      ref: connectionMenuRef,
      className: "wb-mcfg-context-menu",
      role: "menu",
      id: "wb-mcfg-connection-menu",
      "aria-label": label(props, "settings.providerActions", "Actions for {name}", { name: connectionDisplayName(menuConnection, props) }),
      style: Object.assign({}, connectionMenu.portalTheme, { left: connectionMenu.left + "px", top: connectionMenu.top + "px" }),
      onContextMenu: function (event) { event.preventDefault(); },
    },
      h("button", {
        type: "button",
        role: "menuitem",
        className: "is-danger",
        onClick: function () {
          var trigger = connectionMenuReturnFocus.current;
          var connectionId = menuConnection.id;
          setConnectionMenu(null);
          removeConnection(connectionId, trigger);
        },
      }, browserIcon("trash", 15), label(props, "common.delete", "Delete"))
    ) : null;
    var connectionMenuPortal = connectionMenuNode && typeof ReactDOM !== "undefined" && ReactDOM.createPortal
      ? ReactDOM.createPortal(connectionMenuNode, document.body)
      : connectionMenuNode;
    var modelView = {
      h: h, props: props, label: label, browserIcon: browserIcon, searchIcon: searchIcon,
      config: config, store: store, query: query, setQuery: setQuery,
      filtered: filtered, adapters: adapters, selectableAdapters: selectableAdapters,
      selected: selected, selectedId: selectedId, setSelectedId: setSelectedId,
      connectionMenu: connectionMenu, setConnectionMenu: setConnectionMenu,
      openConnectionMenu: openConnectionMenu, connectionProviderMark: connectionProviderMark,
      connectionDisplayName: function (connection) { return connectionDisplayName(connection, props); }, isLocalConnection: isLocalConnection,
      localModels: localModels, addConnection: addConnection, selectedDescription: selectedDescription,
      refreshLocalModels: refreshLocalModels, Toggle: Toggle, updateConnection: updateConnection,
      Field: Field, editorAdapters: editorAdapters, isUserSelectableAdapter: isUserSelectableAdapter,
      adapterOptionName: adapterOptionName, isCodexConnection: isCodexConnection,
      OAuthSection: OAuthSection, oauth: oauth, oauthBusy: oauthBusy,
      startOauthLogin: startOauthLogin, logoutOauth: logoutOauth, downloadOauthCli: downloadOauthCli,
      importOauthModels: importOauthModels, importOauthModel: importOauthModel,
      LocalModelsSection: LocalModelsSection, localRuntime: localRuntime, localError: localError,
      localBusy: localBusy, manageLocalModel: manageLocalModel, profiles: profiles,
      profileDraft: profileDraft, updateProfileDraft: updateProfileDraft,
      cancelProfileDraft: cancelProfileDraft, commitProfileDraft: commitProfileDraft,
      proxyMasterEnabled: proxyMasterEnabled,
      addProfile: addProfile, ProfileEditor: ProfileEditor, updateProfile: updateProfile,
      applyDiscoveredModel: applyDiscoveredModel, applyDiscoveredModelToDraft: applyDiscoveredModelToDraft, discovery: discovery,
      discoverConnection: discoverConnection,
      removeProfile: removeProfile, testProfile: testProfile, busy: busy,
    };

    return h("div", { id: "setting-model-services", className: "settings-panel settings-panel-wide wb-model-config-page" },
      h("header", { className: "wb-mcfg-page-head" },
        h("div", null,
          h("h2", null, label(props, "settings.modelServices", "Model services")),
          h("p", null, label(props, "settings.modelServicesHint", "Configure model platforms, credentials, and reusable model profiles."))
        )
      ),
      saveError ? h("div", { className: "wb-mcfg-status is-error wb-mcfg-save-error", role: "alert" },
        h("span", null, saveError),
        dirty ? h("button", { type: "button", className: "wb-btn", disabled: store.saveState === "saving", onClick: retryQueuedSave }, label(props, "settings.retrySave", "Retry save")) : null
      ) : null,
      h("div", { id: "setting-model-connections", className: "wb-mcfg-services" },
        renderModelConnectionPane(modelView),
        renderModelDetailPane(modelView)
      ),
      connectionMenuPortal
    );
  }

  function supportsRoute(profile, route) {
    var caps = normalizedCapabilities(profile.capabilities);
    var kind = String(profile.kind || profile.type || "").toLowerCase();
    if (route === "vision") return caps.indexOf("vision") >= 0 || caps.indexOf("image") >= 0 || caps.indexOf("multimodal") >= 0 || kind === "vision";
    if (route === "embedding") return caps.indexOf("embedding") >= 0 || caps.indexOf("embeddings") >= 0 || kind === "embedding";
    if (!caps.length) return kind !== "embedding";
    return caps.indexOf("chat") >= 0 || caps.indexOf("completion") >= 0 || caps.indexOf("text") >= 0 || caps.indexOf("reasoning") >= 0 || caps.indexOf("tools") >= 0;
  }

  function RouteCard(props) {
    var route = props.route;
    var meta = ROUTE_META[route];
    var metaTitle = meta.titleKey ? label(props, meta.titleKey, meta.title) : meta.title;
    var metaDescription = meta.descriptionKey ? label(props, meta.descriptionKey, meta.description) : meta.description;
    var selected = props.ids || [];
    var available = props.profiles.filter(function (profile) { return selected.indexOf(profile.id) < 0; });
    return h("section", { id: "setting-model-" + route + "-route", className: "wb-mcfg-route-card", "aria-labelledby": "wb-mcfg-route-" + route },
      h("div", { className: "wb-mcfg-route-head" },
        h("div", null,
          h("h3", { id: "wb-mcfg-route-" + route }, metaTitle),
          h("p", null, metaDescription)
        ),
        h("span", { className: "wb-mcfg-route-count" }, label(props, "settings.modelCount", "Models: {count}", { count: selected.length }))
      ),
      h("div", { className: "wb-mcfg-route-list" },
        !selected.length ? h("div", { className: "wb-mcfg-route-empty" }, label(props, "settings.noModelsSelected", "No models selected.")) : null,
        selected.map(function (id, index) {
          var profile = props.profileById[id];
          if (!profile) {
            return h("article", { className: "wb-mcfg-route-row is-missing", key: id },
              h("div", null, h("strong", null, id), h("small", null, label(props, "settings.modelProfileMissing", "Model profile no longer exists"))),
              h("button", { type: "button", className: "wb-mcfg-icon-btn is-danger", onClick: function () { props.onRemove(id); }, "aria-label": label(props, "settings.removeMissingModel", "Remove missing model {name}", { name: id }) }, browserIcon("close", 15))
            );
          }
          var connection = props.connectionById[profile.connection_id] || {};
          return h("article", { className: "wb-mcfg-route-row", key: id },
            h("span", { className: "wb-mcfg-rank", "aria-label": label(props, "settings.modelRank", "Position {position}", { position: index + 1 }) }, index + 1),
            h("div", { className: "wb-mcfg-route-model" },
              h("strong", null, profile.name || profile.model),
              h("small", null, (connection.name || label(props, "settings.unknownProvider", "Unknown provider")) + " · " + profile.model)
            ),
            h("div", { className: "wb-mcfg-route-meta" },
              h("span", null, label(props, "settings.contextValue", "Context {value}", { value: formatContext(profile) })),
              h("span", null, capabilityText(profile, props))
            ),
            meta.ordered ? h("div", { className: "wb-mcfg-order-actions", role: "group", "aria-label": label(props, "settings.adjustModelOrder", "Adjust the position of {name}", { name: profile.name || profile.model }) },
              h("button", { type: "button", disabled: index === 0, onClick: function () { props.onMove(id, -1); }, "aria-label": label(props, "settings.moveModelUp", "Move {name} up", { name: profile.name || profile.model }) }, settingsGlyph("arrow-up", 16)),
              h("button", { type: "button", disabled: index === selected.length - 1, onClick: function () { props.onMove(id, 1); }, "aria-label": label(props, "settings.moveModelDown", "Move {name} down", { name: profile.name || profile.model }) }, settingsGlyph("arrow-down", 16))
            ) : null,
            h("button", { type: "button", className: "wb-mcfg-icon-btn is-danger", onClick: function () { props.onRemove(id); }, "aria-label": label(props, "settings.removeNamedModel", "Remove {name}", { name: profile.name || profile.model }) }, browserIcon("close", 15))
          );
        })
      ),
      h("label", { className: "wb-mcfg-route-add" },
        h("span", null, label(props, "settings.addModel", "Add model")),
        h("select", {
          className: "wb-select", value: "", disabled: !available.length,
          onChange: function (event) { if (event.target.value) props.onAdd(event.target.value); },
          "aria-label": label(props, "settings.addModelForRoute", "Add model for {route}", { route: metaTitle }),
        },
          h("option", { value: "" }, available.length
            ? label(props, "settings.selectModelProfile", "Select model profile…")
            : label(props, "settings.noEligibleModelProfiles", "No models match this capability")),
          available.map(function (profile) {
            var connection = props.connectionById[profile.connection_id] || {};
            var providerName = isLocalConnection(connection)
              ? label(props, "settings.localProvider", "Local")
              : (connection.name || label(props, "settings.unknownProvider", "Unknown provider"));
            return h("option", { key: profile.id, value: profile.id }, (profile.name || profile.model) + " — " + providerName + " · " + formatContext(profile) + " · " + capabilityText(profile, props));
          })
        )
      )
    );
  }

  function UsagePage(props) {
    props = props || {};
    var store = useModelConfiguration(props);
    var config = store.config;
    var [dirty, setDirty] = useState(false);
    var usageMounted = useRef(true);

    useEffect(function () {
      usageMounted.current = true;
      return function () { usageMounted.current = false; };
    }, []);

    function updateRoute(route, nextIds) {
      store.setConfig(Object.assign({}, config, { routes: Object.assign({}, config.routes, { [route]: nextIds }) }));
      setDirty(true);
      store.setError("");
    }

    function saveRoutes() {
      store.save(config, true).then(function () {
        if (usageMounted.current) setDirty(false);
      }).catch(function (error) {
        if (usageMounted.current && error && error.reloaded) setDirty(false);
      });
    }

    if (store.loading) return h("div", { className: "settings-panel wb-model-config-page wb-mcfg-usage-page" }, h(LoadingState, props));
    if (!config) return h("div", { className: "settings-panel wb-model-config-page wb-mcfg-usage-page" }, h(ErrorState, Object.assign({}, props, { error: store.error, onRetry: store.load })));

    var profileById = {};
    config.profiles.forEach(function (profile) { profileById[profile.id] = profile; });
    var connectionById = {};
    config.connections.forEach(function (connection) { connectionById[connection.id] = connection; });

    return h("div", { id: "setting-model-usage", className: "settings-panel wb-model-config-page wb-mcfg-usage-page" },
      h("header", { className: "wb-mcfg-page-head" },
        h("div", null,
          h("h2", null, label(props, "settings.modelUsage", "Model configuration")),
          h("p", null, label(props, "settings.modelUsageHint", "Routes reference model profiles only. Changing an API key or endpoint does not alter their order."))
        ),
        h("div", { className: "wb-mcfg-save-cluster" },
          dirty ? h("span", { className: "wb-mcfg-unsaved" }, label(props, "settings.unsavedChanges", "Unsaved changes")) : null,
          h("button", { type: "button", className: "wb-btn primary", disabled: !dirty || store.saveState === "saving", onClick: saveRoutes }, store.saveState === "saving" ? label(props, "common.saving", "Saving…") : label(props, "settings.saveModelRoutes", "Save model routes"))
        )
      ),
      !config.profiles.length ? h("div", { className: "wb-mcfg-state" },
        h("strong", null, label(props, "settings.noModelProfiles", "No model profiles yet")),
        h("span", null, label(props, "settings.noModelProfilesUsageHint", "Add a connection and model profile under Model services first."))
      ) : h("div", { className: "wb-mcfg-route-stack" }, Object.keys(ROUTE_META).map(function (route) {
        var candidates = config.profiles.filter(function (profile) {
          var connection = connectionById[profile.connection_id];
          return profile.enabled !== false && (!connection || connection.enabled !== false) && supportsRoute(profile, route);
        });
        return h(RouteCard, {
          key: route, route: route, ids: config.routes[route] || [], profiles: candidates,
          t: props.t,
          profileById: profileById, connectionById: connectionById,
          onAdd: function (id) { updateRoute(route, (config.routes[route] || []).concat([id])); },
          onRemove: function (id) { updateRoute(route, (config.routes[route] || []).filter(function (value) { return value !== id; })); },
          onMove: function (id, direction) {
            var ids = (config.routes[route] || []).slice();
            var from = ids.indexOf(id);
            var to = from + direction;
            if (from < 0 || to < 0 || to >= ids.length) return;
            var swap = ids[to]; ids[to] = ids[from]; ids[from] = swap;
            updateRoute(route, ids);
          },
        });
      }))
    );
  }

  window.CyreneUI.modelSettings = window.CyreneUI.register("model-settings", {
    ServicesPage: ServicesPage,
    UsagePage: UsagePage,
    normalizeConfig: normalizeConfig,
  });
})();
