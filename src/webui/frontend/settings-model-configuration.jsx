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
var LEGACY_USER_ADAPTERS = {
  anthropic: "Anthropic", anthropic_messages: "Anthropic",
  openai: "OpenAI", openai_chat: "OpenAI", openai_compatible: "OpenAI",
  openai_responses: "OpenAI Responses",
  gemini: "Gemini", gemini_api: "Gemini", google_gemini: "Gemini",
};
var ROUTE_META = {
  primary: { title: "默认模型顺位", description: "对话与 Agent 的主要模型，按顺序自动回退。", capability: "chat", ordered: true },
  secondary: { title: "次要模型", description: "用于摘要、标题和低成本辅助任务。", capability: "chat", ordered: false },
  vision: { title: "识图模型", description: "处理图片和视觉内容，按顺序自动回退。", capability: "vision", ordered: true },
  embedding: { title: "嵌入模型", titleKey: "settings.embeddingRouteTitle", description: "为知识库和语义检索生成向量。", descriptionKey: "settings.embeddingRouteHint", capability: "embedding", ordered: false },
};

function renderModelConnectionPane(v) {
  var h = v.h;
  return h("aside", { className: "wb-mcfg-connection-pane", "aria-label": "模型服务商" },
    h("div", { className: "wb-workbench-filterbar wb-mcfg-searchbar" },
      h("label", { className: "wb-workbench-searchbox wb-mcfg-searchbox" },
        v.searchIcon(16),
        h("input", {
          type: "search", value: v.query,
          onChange: function (event) { v.setQuery(event.target.value); },
          placeholder: "搜索模型服务…", "aria-label": "搜索模型服务",
          autoComplete: "off", spellCheck: false,
        })
      )
    ),
    h("div", { className: "wb-mcfg-connection-list", role: "listbox", "aria-label": "服务商连接" },
      !v.filtered.length ? h("div", { className: "wb-mcfg-list-empty" }, String(v.query || "").trim() ? "没有匹配的服务商。" : "还没有服务商连接。") : null,
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
            h("small", null, serviceLabel + " · " + count + " 个模型")
          ),
          h("span", { className: "wb-mcfg-dot " + (connection.enabled ? "is-ready" : "is-off"), title: connection.enabled ? "已启用" : "已停用" }, h("span", { className: "wb-mcfg-sr-only" }, connection.enabled ? "已启用" : "已停用"))
        );
      })
    ),
    h("button", { type: "button", className: "wb-mcfg-add-connection", disabled: !v.selectableAdapters.length, onClick: v.addConnection, title: v.selectableAdapters.length ? "" : v.label(v.props, "settings.adapterUnavailable", "当前服务没有可添加的协议。") }, v.browserIcon("plus", 17), "添加服务商")
  );
}

function renderModelDetailPane(v) {
  var h = v.h;
  var selected = v.selected;
  return h("main", { className: "wb-mcfg-detail-pane" }, selected ? h(React.Fragment, null,
    h("div", { className: "wb-mcfg-detail-head" },
      h("div", null, h("h3", null, v.connectionDisplayName(selected)), v.selectedDescription ? h("p", null, v.selectedDescription) : null),
      v.isLocalConnection(selected)
        ? h("button", { type: "button", className: "wb-mcfg-icon-btn", onClick: v.refreshLocalModels, "aria-label": "刷新本地模型" }, v.browserIcon("reload", 16))
        : h(v.Toggle, { checked: selected.enabled, label: (selected.enabled ? "停用" : "启用") + selected.name, onChange: function (value) { v.updateConnection("enabled", value); } })
    ),
    !v.isLocalConnection(selected) ? h("section", { className: "wb-mcfg-form-section wb-mcfg-connection-section", "aria-label": "连接设置" },
      h("div", { className: "wb-mcfg-form-grid" },
        h(v.Field, { label: "连接名称" }, h("input", { className: "wb-input", value: selected.name, "aria-label": "连接名称", onChange: function (event) { v.updateConnection("name", event.target.value); } })),
        h(v.Field, { label: v.label(v.props, "settings.adapter", "协议") }, h("select", { className: "wb-select", value: selected.adapter, "aria-label": "模型服务协议", disabled: !v.selectableAdapters.length, onChange: function (event) { v.updateConnection("adapter", event.target.value); } }, v.editorAdapters.map(function (adapter) {
          return h("option", { key: adapter.id, value: adapter.id, disabled: !v.isUserSelectableAdapter(adapter) }, v.adapterOptionName(adapter));
        }))),
        !v.isCodexConnection(selected) && !v.isLocalConnection(selected) ? h(v.Field, {
          label: "API 地址",
          wide: true,
          hint: selected.use_proxy && !v.proxyMasterEnabled ? "已为此模型服务选择代理；开启通用设置中的代理总开关后生效。" : "",
        }, h("div", { className: "wb-mcfg-api-proxy-row" },
          h("input", { className: "wb-input", type: "url", value: selected.base_url, "aria-label": "模型服务 API 地址", onChange: function (event) { v.updateConnection("base_url", event.target.value); }, placeholder: "https://api.example.com/v1", autoComplete: "url" }),
          h("div", { className: "wb-mcfg-model-proxy-control" },
            h("span", null, "使用代理"),
            h(v.Toggle, { checked: selected.use_proxy === true, label: (selected.use_proxy ? "关闭" : "开启") + selected.name + "的代理", onChange: function (value) { v.updateConnection("use_proxy", value); } })
          )
        )) : null,
        !v.isCodexConnection(selected) && !v.isLocalConnection(selected) ? h(v.Field, { label: "API 密钥", wide: true, hint: selected.secret_configured && !selected.secret ? "已保存密钥；留空不会覆盖。" : "密钥只发送给本机配置 API。" }, h("input", {
          className: "wb-input", type: "password", value: selected.secret,
          onChange: function (event) { v.updateConnection("secret", event.target.value); },
          placeholder: selected.secret_configured ? "已配置" : "sk-…", autoComplete: "new-password",
          "aria-label": "API 密钥（只写）", "data-cyrene-agent-secret-input": "true", "data-cyrene-risk": "R3",
        })) : null
      )
    ) : null,
    v.isCodexConnection(selected) ? h(v.OAuthSection, { state: v.oauth, busy: v.oauthBusy, cliBusy: v.oauthBusy === "cli", onLogin: v.startOauthLogin, onLogout: v.logoutOauth, onDownloadCli: v.downloadOauthCli, onImportModels: v.importOauthModels, onImportModel: v.importOauthModel }) : null,
    v.isLocalConnection(selected) ? h(v.LocalModelsSection, { t: v.props.t, models: v.localModels, cv2Runtime: v.localRuntime, error: v.localError, busy: v.localBusy, hideHeader: true, onRefresh: v.refreshLocalModels, onManage: v.manageLocalModel }) : null,
    !v.isLocalConnection(selected) ? h("section", { className: "wb-mcfg-form-section", "aria-labelledby": "wb-mcfg-profiles-heading" },
      h("div", { className: "wb-mcfg-section-head" },
        h("h4", { id: "wb-mcfg-profiles-heading" }, "模型列表"),
        h("button", { type: "button", className: "wb-btn", onClick: function () { v.addProfile(); } }, v.browserIcon("plus", 15), "添加模型")
      ),
      !v.profiles.length ? h("div", { className: "wb-mcfg-inline-empty" }, "还没有模型档案。请手动添加模型。") : null,
      v.profiles.length ? h("div", { className: "wb-mcfg-profile-list", "aria-label": "模型列表" }, v.profiles.map(function (profile) {
        return h(v.ProfileEditor, { key: profile.id, profile: profile, t: v.props.t,
          onChange: function (key, value) { v.updateProfile(profile.id, key, value); },
          onRemove: function () { v.removeProfile(profile.id); },
          onTest: function () { v.testProfile(profile); }, testing: v.busy === "test:" + profile.id });
      })) : null
    ) : null
  ) : h("div", { className: "wb-mcfg-state" },
    h("strong", null, "选择或添加一个模型服务商"),
    h("span", null, "服务商连接保存认证信息，模型档案保存具体模型和能力。"),
    h("button", { type: "button", className: "wb-btn primary", disabled: !v.selectableAdapters.length, onClick: v.addConnection }, "添加服务商")
  ));
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
      return label(props, "settings.localModelErrorExtract", "压缩包解压失败，下载的模型文件不完整或无效，请重试下载。");
    }
    if (lower.indexOf("checksum") >= 0 || lower.indexOf("sha256") >= 0 || lower.indexOf("validation failed") >= 0) {
      return label(props, "settings.localModelErrorChecksum", "文件完整性校验失败，下载内容可能已损坏，请重试下载。");
    }
    if (lower.indexOf("all mirrors failed") >= 0 || lower.indexOf("connect") >= 0
        || lower.indexOf("timeout") >= 0 || lower.indexOf("network") >= 0
        || lower.indexOf("proxy") >= 0 || lower.indexOf("resolve") >= 0
        || lower.indexOf("httpx") >= 0 || lower.indexOf("remote protocol") >= 0) {
      return label(props, "settings.localModelErrorNetwork", "网络或镜像源连接失败，请检查网络后重试下载。");
    }
    return label(props, "settings.localModelErrorGeneric", "本地模型下载失败，请重试。");
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
      var middle = Number(parts[1]);
      var last = Number(parts[2]);
      var legacyOrder = parts[1] !== "" && parts[2] !== ""
        && Number.isFinite(middle) && Number.isFinite(last) && middle > last;
      output = legacyOrder ? parts[1] : parts[2];
      cache = legacyOrder ? parts[2] : parts[1];
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
    var id = String(raw.id || raw.adapter_id || raw.type || raw.slug || ("adapter-" + index)).trim();
    return Object.assign({}, raw, {
      id: id,
      name: String(raw.name || raw.label || raw.display_name || id).trim(),
      description: String(raw.description || raw.desc || "").trim(),
      capabilities: normalizedCapabilities(raw.capabilities || raw.supports),
    });
  }

  function adapterKey(value) {
    return String(value || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  }

  function legacyUserAdapterName(adapter) {
    var byId = LEGACY_USER_ADAPTERS[adapterKey(adapter && adapter.id)];
    if (byId) return byId;
    var byLabel = adapterKey(adapter && adapter.name);
    if (byLabel === "anthropic") return "Anthropic";
    if (byLabel === "openai" || byLabel === "openai_compatible") return "OpenAI";
    if (byLabel === "openai_responses") return "OpenAI Responses";
    if (byLabel === "gemini") return "Gemini";
    return "";
  }

  function adapterSelectableMetadata(adapter) {
    if (!adapter || typeof adapter !== "object") return null;
    var keys = ["user_selectable", "selectable"];
    for (var index = 0; index < keys.length; index += 1) {
      if (!Object.prototype.hasOwnProperty.call(adapter, keys[index])) continue;
      var raw = adapter[keys[index]];
      if (raw === true || raw === 1 || String(raw).toLowerCase() === "true" || String(raw) === "1") return true;
      if (raw === false || raw === 0 || String(raw).toLowerCase() === "false" || String(raw) === "0") return false;
    }
    return null;
  }

  function isUserSelectableAdapter(adapter) {
    var declared = adapterSelectableMetadata(adapter);
    return declared == null ? !!legacyUserAdapterName(adapter) : declared;
  }

  function adapterOptionName(adapter) {
    if (adapterKey(adapter && adapter.id) === "openai_compatible" && adapterSelectableMetadata(adapter) === false) {
      return "OpenAI Compatible（旧配置）";
    }
    return legacyUserAdapterName(adapter) || String(adapter && (adapter.name || adapter.id) || "");
  }

  function normalizeConnection(raw, index) {
    raw = raw || {};
    var id = String(raw.id || raw.connection_id || raw.provider_id || ("connection-" + index)).trim();
    var adapter = String(raw.adapter || raw.adapter_id || raw.provider || raw.type || "openai_compatible").trim();
    return Object.assign({}, raw, {
      id: id,
      name: String(raw.name || raw.label || raw.display_name || adapter || id).trim(),
      adapter: adapter,
      adapter_id: adapter,
      base_url: String(raw.base_url || raw.endpoint || raw.api_base || "").trim(),
      secret: String(raw.secret || raw.api_key || ""),
      secret_configured: raw.secret_configured === true || raw.api_key_configured === true || raw.has_secret === true,
      enabled: raw.enabled !== false,
      use_proxy: raw.use_proxy === true,
    });
  }

  function normalizeProfile(raw, index, fallbackConnectionId) {
    raw = raw || {};
    var remoteModel = String(raw.model || raw.model_id || raw.remote_id || raw.slug || raw.name || raw.id || "").trim();
    var connectionId = String(raw.connection_id || raw.connection || raw.provider_id || fallbackConnectionId || "").trim();
    var id = String(raw.id || raw.profile_id || safeId("profile", connectionId + "-" + remoteModel) || ("profile-" + index)).trim();
    var ctx = raw.context_limit != null ? raw.context_limit : raw.ctx;
    return Object.assign({}, raw, {
      id: id,
      connection_id: connectionId,
      connection: connectionId,
      model: remoteModel,
      model_id: remoteModel,
      name: String(raw.name || raw.display_name || remoteModel || id).trim(),
      context_limit: ctx == null || ctx === "" ? "" : ctx,
      ctx: ctx == null || ctx === "" ? "" : ctx,
      price: String(raw.price || "").trim(),
      capabilities: normalizedCapabilities(raw.capabilities || raw.supports || raw.modalities),
      enabled: raw.enabled !== false,
    });
  }

  function normalizeRoutes(raw) {
    raw = raw || {};
    function route(name) {
      var value = raw[name];
      if (value == null && name === "primary") value = raw.default;
      if (value == null && name === "secondary") value = raw.minor;
      if (!Array.isArray(value)) value = value ? [value] : [];
      return value.map(function (item) {
        return String(item && typeof item === "object" ? (item.profile_id || item.id || "") : item || "").trim();
      }).filter(Boolean);
    }
    return {
      primary: route("primary"),
      secondary: route("secondary"),
      vision: route("vision"),
      embedding: route("embedding"),
    };
  }

  function normalizeConfig(raw) {
    raw = raw && raw.config && !raw.connections && !raw.profiles ? raw.config : (raw || {});
    var adapters = listFrom(raw.adapters || raw.adapter_definitions).map(normalizeAdapter);
    var connections = listFrom(raw.connections || raw.model_connections || raw.providers).map(normalizeConnection);
    var profiles = listFrom(raw.profiles || raw.model_profiles || raw.models).map(function (item, index) {
      return normalizeProfile(item, index, "");
    });
    return Object.assign({}, raw, {
      adapters: adapters,
      connections: connections,
      profiles: profiles,
      routes: normalizeRoutes(raw.routes || raw.model_routes || raw.usage),
    });
  }

  function configPayload(config) {
    return Object.assign({}, config, {
      adapters: config.adapters || [],
      connections: (config.connections || []).map(function (connection) {
        var next = Object.assign({}, connection, { adapter: connection.adapter, adapter_id: connection.adapter });
        var secret = String(next.secret || "").trim();
        if (secret) {
          next.api_key = secret;
          delete next.clear_api_key;
          delete next.clear_secret;
        } else {
          delete next.secret;
          delete next.api_key;
        }
        return next;
      }),
      profiles: (config.profiles || []).map(function (profile) {
        return Object.assign({}, profile, {
          connection_id: profile.connection_id,
          model: profile.model,
          model_id: profile.model,
          context_limit: profile.context_limit,
          ctx: profile.context_limit,
          price: String(profile.price || "").trim(),
          capabilities: normalizedCapabilities(profile.capabilities),
        });
      }),
      routes: normalizeRoutes(config.routes),
    });
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

    function save(nextConfig, dispatchRoutes, options) {
      options = options || {};
      var draft = normalizeConfig(nextConfig || config || {});
      if (mounted.current) {
        setSaveState("saving");
        if (operationIsCurrent(options)) setError("");
      }
      return requestJson("/api/settings/model-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configPayload(draft)),
      }).then(function (payload) {
        var saved = normalizeConfig(payload && (payload.connections || payload.profiles || payload.routes || payload.config) ? payload : draft);
        var isCurrent = operationIsCurrent(options);
        if (mounted.current) setSaveState("idle");
        if (isCurrent) {
          setConfig(saved);
          if (!options.silentSuccess) showSettingsToast(label(props, "settings.modelConfigSaved", "已保存"), "success");
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
        if (saveError.status === 409) {
          setError("模型配置已在其他位置更新，请重新加载后再修改。" );
          if (options.handleConflict !== false && window.confirm("模型配置已被其他页面更新。是否立即重新加载最新配置？当前未保存更改将丢失。")) {
            return load({ isCurrent: options.isCurrent }).then(function () {
              saveError.reloaded = true;
              throw saveError;
            }).catch(function (reloadError) {
              if (reloadError !== saveError) saveError.reloadError = reloadError;
              throw saveError;
            });
          }
        } else {
          setError(saveError.message || String(saveError));
        }
        showSettingsToast(saveError.message || String(saveError), "error");
        throw saveError;
      });
    }

    return { config: config, setConfig: setConfig, loading: loading, error: error, setError: setError, saveState: saveState, load: load, save: save };
  }

  function LoadingState(props) {
    return h("div", { className: "wb-mcfg-state", role: "status", "aria-live": "polite" },
      h("span", { className: "wb-mcfg-spinner", "aria-hidden": "true" }),
      h("strong", null, label(props, "settings.modelConfigLoading", "正在加载模型配置…")),
      h("span", null, label(props, "settings.modelConfigLoadingHint", "正在读取服务商、模型档案和用途。"))
    );
  }

  function ErrorState(props) {
    return h("div", { className: "wb-mcfg-state is-error", role: "alert" },
      h("strong", null, label(props, "settings.modelConfigLoadFailed", "模型配置加载失败")),
      h("span", null, props.error),
      h("button", { type: "button", className: "wb-btn", onClick: props.onRetry }, label(props, "common.retry", "重试"))
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
    var raw = profile && (profile.context_limit != null ? profile.context_limit : profile.ctx);
    if (raw == null || raw === "") return "—";
    var value = Number(raw);
    if (!isFinite(value)) return String(raw);
    if (value >= 1000000) return (Math.round(value / 100000) / 10) + "M";
    if (value >= 1000) return (Math.round(value / 100) / 10) + "K";
    return String(value);
  }

  function connectionAdapter(connection) {
    return String(connection && (connection.adapter || connection.adapter_id || connection.provider) || "").toLowerCase();
  }

  function isCodexConnection(connection) {
    var adapter = connectionAdapter(connection);
    return adapter === "codex_oauth" || adapter === "openai_oauth" || adapter.indexOf("codex") >= 0;
  }

  function isLocalConnection(connection) {
    var adapter = connectionAdapter(connection);
    return adapter === "local_onnx" || adapter === "onnx" || adapter.indexOf("local") >= 0;
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

  function connectionDisplayName(connection) {
    var name = String(connection && connection.name || "").trim();
    if (isLocalConnection(connection) && (!name || /^local onnx$/i.test(name))) return "本地模型";
    return name || "未命名服务商";
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
          ctx: old.context_limit || normalized.context_limit,
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
          h("small", null, state.checking ? "正在检查登录状态…" : state.connected ? "已连接 OpenAI 账号" : "使用 OpenAI 账号授权，无需手动填写 API Key。")
        ),
        h("span", { className: "wb-mcfg-connection-state " + (state.connected ? "is-ready" : "is-off") }, state.connected ? "已连接" : "未连接")
      ),
      state.error && !cliNeedsDownload ? h("div", { className: "wb-mcfg-inline-error", role: "alert" }, state.error) : null,
      cliNeedsDownload ? h("div", { className: "wb-mcfg-cli-runtime" },
        h("div", { className: "wb-mcfg-cli-copy" },
          h("strong", null, label(props, "settings.codexCliRuntime", "Codex CLI 运行时")),
          h("small", { className: cli.error ? "is-error" : "" }, cli.error || label(props, "settings.codexCliRequiredHint", "登录 OpenAI 需要 Codex CLI 运行时（约 120 MB），下载后保存在本地缓存。"))
        ),
        cliDownloading
          ? h("div", { className: "wb-mcfg-cli-progress", role: "status", "aria-live": "polite" },
              h("div", null,
                h("span", null, label(props, "settings.codexCliDownloading", "正在下载 Codex CLI…")),
                h("span", null, cliPercent ? cliPercent + "%" : "—")
              ),
              h("progress", { max: 100, value: cliPercent || undefined, "aria-label": label(props, "settings.codexCliDownloading", "正在下载 Codex CLI…") + (cliPercent ? " " + cliPercent + "%" : "") })
            )
          : h("button", {
              type: "button",
              className: "wb-btn primary wb-mcfg-cli-download",
              disabled: !!props.busy,
              onClick: function () { props.onDownloadCli(!!cli.broken); },
            }, cli.broken
              ? label(props, "settings.codexCliRedownload", "重新下载 Codex CLI")
              : label(props, "settings.codexCliDownload", "下载 Codex CLI"))
      ) : null,
      state.connected && models.length ? h("label", { className: "wb-mcfg-oauth-picker" },
        h("span", null, "可用模型"),
        h("select", { className: "wb-select", value: selectedModelId, "aria-label": "OpenAI 可用模型", onChange: function (event) { setSelectedModelId(event.target.value); } }, models.map(function (item) {
          var id = String(item.model || item.id || item.slug || "");
          return h("option", { key: id, value: id }, item.displayName || item.display_name || item.name || id);
        })),
        h("button", { type: "button", className: "wb-btn", disabled: !selectedModel, onClick: function () { props.onImportModel(selectedModel); } }, "添加为模型档案")
      ) : null,
      (state.connected || !cliNeedsDownload) ? h("div", { className: "wb-mcfg-actions" },
        state.connected
          ? h("button", { type: "button", className: "wb-btn danger", "data-cyrene-risk": "R3", disabled: !!props.busy, onClick: props.onLogout }, props.busy === "logout" ? "正在退出…" : "退出登录")
          : h("button", { type: "button", className: "wb-btn primary", "data-cyrene-risk": "R3", disabled: !!props.busy || state.checking, onClick: props.onLogin }, props.busy === "login" ? "等待授权…" : "登录 OpenAI"),
        state.connected && models.length
          ? h("button", { type: "button", className: "wb-btn", disabled: !!props.busy, onClick: props.onImportModels }, "导入 " + models.length + " 个可用模型")
          : null
      ) : null
    );
  }

  function LocalModelsSection(props) {
    var models = props.models || [];
    var cv2Runtime = props.cv2Runtime || null;
    var copyById = {
      "qwen3-embedding-0.6b": ["settings.localEmbeddingTitle", "settings.localQwenName", "settings.localQwenHint", "本地嵌入模型", "Qwen3 Embedding 0.6B", "1024 维多语言语义检索 · 约 626 MB"],
      "pp-ocrv6-medium": ["settings.localOcrTitle", "settings.localOcrName", "settings.localOcrHint", "本地 OCR 模型", "PP-OCRv6 Medium OCR", "本机中英日文及拉丁语系文字识别 · 约 130 MB"],
      "fireredasr2-aed-int8": ["settings.localAsrTitle", "settings.localFireRedName", "settings.localFireRedHint", "本地语音识别", "FireRedASR2-AED INT8", "高质量中英文语音识别 · 约 904 MB"],
      "kokoro-zh-en": ["settings.localTtsTitle", "settings.localKokoroName", "settings.localKokoroHint", "本地语音合成", "Kokoro 82M 中英双语 FP32", "自然流畅的中英文预设音色 · 约 365 MB"],
      "zipvoice-zh-en": ["settings.localTtsTitle", "settings.localZipVoiceName", "settings.localZipVoiceHint", "本地语音合成", "ZipVoice Distill 中英双语 FP32", "使用 Vocos 在本机复刻自定义音色 · 约 532 MB"],
    };
    return h("section", { className: "wb-mcfg-special wb-mcfg-local-manager", "aria-labelledby": props.hideHeader ? undefined : "wb-mcfg-local-title" },
      !props.hideHeader ? h("div", { className: "wb-mcfg-special-head" },
        h("div", null,
          h("strong", { id: "wb-mcfg-local-title" }, "本地模型"),
          h("small", null, "模型文件存放在本机；下载完成后即可被对应用途选择。")
        ),
        h("button", { type: "button", className: "wb-mcfg-icon-btn", onClick: props.onRefresh, "aria-label": "刷新本地模型" }, browserIcon("reload", 16))
      ) : null,
      props.error ? h("div", { className: "wb-mcfg-inline-error", role: "alert" }, props.error) : null,
      !models.length && !props.error ? h("div", { className: "wb-mcfg-inline-empty" }, "没有可管理的本地模型。") : null,
      h("div", { className: "wb-mcfg-local-list" }, models.map(function (model) {
        var percent = downloadPercent(model);
        var busy = props.busy === model.id || model.downloading;
        var localCopy = copyById[model.id];
        var kind = model.kind || "model";
        var displayTitle = localCopy ? label(props, localCopy[0], localCopy[3]) : kind;
        var displayName = localCopy ? label(props, localCopy[1], localCopy[4]) : (model.name || model.id);
        var displayDescription = localCopy ? label(props, localCopy[2], localCopy[5]) : (model.description || kind || model.runtime || "本地模型");
        var runtime = String(model.runtime || "onnx").toLowerCase();
        var runtimeLabel = runtime === "onnx-cpu" ? "CPU" : runtime.toUpperCase();
        var runtimeClass = runtime.indexOf("cuda") >= 0 || runtime.indexOf("directml") >= 0 || runtime.indexOf("qnn") >= 0
          ? " is-cuda" : runtime.indexOf("mlx") >= 0 ? " is-mlx" : " is-onnx";
        var hasError = !model.ready && !!model.error;
        var statusText = hasError
          ? label(props, "settings.localModelError", "异常")
          : model.ready
            ? label(props, "settings.localModelActive", "已启用 · " + runtimeLabel, { runtime: runtimeLabel })
            : model.downloading
              ? label(props, "settings.localModelDownloading", "正在下载 · " + percent + "%", { percent: percent })
              : label(props, "settings.localModelOptional", "可选 · 未下载");
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
              h("progress", { max: 100, value: percent, "aria-label": "下载进度 " + percent + "%" }),
              h("span", null, percent + "%")
            ) : null,
            cv2RuntimeMissing ? h("small", { className: "wb-local-model-runtime" + (cv2Runtime.error ? " wb-local-model-error" : "") },
              cv2Runtime.downloading
                ? label(props, "settings.ocrRuntimeDownloading", "OCR 运行时正在下载 · " + downloadPercent(cv2Runtime) + "%", { percent: downloadPercent(cv2Runtime) })
                : cv2Runtime.error
                  ? label(props, "settings.ocrRuntimeFailed", "OCR 运行时下载失败") + ": " + cv2Runtime.error
                  : label(props, "settings.ocrRuntimeBundled", "OCR 还需要本机运行时，下载模型时会一并安装。")
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
              "aria-label": (model.ready ? label(props, "settings.delete", "删除") : hasError ? label(props, "settings.retry", "重试") : label(props, "settings.download", "下载")) + " " + displayName,
            }, model.ready ? label(props, "settings.delete", "删除") : hasError ? label(props, "settings.retry", "重试") : label(props, "settings.download", "下载"))
          )
        );
      }))
    );
  }

  function ProfileEditor(props) {
    var profile = props.profile;
    var pricing = profilePriceFields(profile.price);
    var displayName = profile.name || profile.model || "未命名模型";
    var capabilities = normalizedCapabilities(profile.capabilities);
    var capabilityOptions = ["chat", "vision", "embedding"];
    var [expanded, setExpanded] = useState(false);
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
          h("code", { title: profile.model || "" }, profile.model || "未设置模型 ID")
        ),
        h("span", { className: "wb-btn wb-mcfg-profile-details-button", "aria-hidden": "true" }, expanded ? "收起" : "详情")
      ),
      expanded ? h("div", { id: detailsId, className: "wb-mcfg-profile-details" },
        h("div", { className: "wb-mcfg-profile-details-grid" },
          h("label", { className: "wb-mcfg-profile-editor-field is-half" },
            h("span", null, "显示名称"),
            h("input", { className: "wb-input", value: profile.name || "", "aria-label": "模型显示名称", onChange: function (event) { props.onChange("name", event.target.value); } })
          ),
          h("div", { className: "wb-mcfg-profile-editor-field is-half" },
            h("span", null, "能力"),
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
          h("label", { className: "wb-mcfg-profile-editor-field is-wide" },
            h("span", null, "模型 ID"),
            h("input", { className: "wb-input", value: profile.model || "", "aria-label": "模型 ID", onChange: function (event) { props.onChange("model", event.target.value); }, placeholder: "例如 gpt-5" })
          ),
          h("label", { className: "wb-mcfg-profile-editor-field" },
            h("span", null, "上下文（Token）"),
            h("input", { className: "wb-input", type: "number", min: 0, inputMode: "numeric", value: profile.context_limit == null ? "" : profile.context_limit, "aria-label": "上下文 Token 上限", onChange: function (event) { props.onChange("context_limit", event.target.value); }, placeholder: "自动", title: label(props, "settings.adapterAutoDetectHint", "Token 数；留空表示由协议自动判断。") })
          ),
          h("label", { className: "wb-mcfg-profile-editor-field" },
            h("span", null, label(props, "settings.inputPrice", "输入价格")),
            h("input", { className: "wb-input", type: "number", min: 0, step: "any", inputMode: "decimal", value: pricing.input, "aria-label": "模型输入价格", onChange: function (event) { props.onChange("price", updateProfilePriceField(profile.price, "input", event.target.value)); }, placeholder: "0", title: label(props, "settings.pricePerMillionHint", "每百万 Token 的价格，默认人民币。") })
          ),
          h("label", { className: "wb-mcfg-profile-editor-field" },
            h("span", null, label(props, "settings.outputPrice", "输出价格")),
            h("input", { className: "wb-input", type: "number", min: 0, step: "any", inputMode: "decimal", value: pricing.output, "aria-label": "模型输出价格", onChange: function (event) { props.onChange("price", updateProfilePriceField(profile.price, "output", event.target.value)); }, placeholder: "0", title: label(props, "settings.pricePerMillionHint", "每百万 Token 的价格，默认人民币。") })
          ),
          h("label", { className: "wb-mcfg-profile-editor-field" },
            h("span", null, label(props, "settings.cachePrice", "缓存价格")),
            h("input", { className: "wb-input", type: "number", min: 0, step: "any", inputMode: "decimal", value: pricing.cache, "aria-label": "模型缓存价格", onChange: function (event) { props.onChange("price", updateProfilePriceField(profile.price, "cache", event.target.value)); }, placeholder: "0", title: label(props, "settings.pricePerMillionHint", "每百万 Token 的价格，默认人民币。") })
          )
        ),
        h("div", { className: "wb-mcfg-profile-details-actions" },
          h("button", { type: "button", className: "wb-btn", disabled: !!props.testing || !String(profile.model || "").trim(), onClick: props.onTest }, props.testing ? "正在测试…" : "测试连接"),
          h("button", { type: "button", className: "wb-btn danger", onClick: props.onRemove }, "删除模型")
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
    var queuedVersion = useRef(0);
    var knownRevision = useRef(null);
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

    var selected = config && (config.connections || []).find(function (item) { return item.id === selectedId; });
    useEffect(function () {
      if (!config || !(config.connections || []).length) return;
      if (!(config.connections || []).some(function (item) { return item.id === selectedId; })) setSelectedId(config.connections[0].id);
    }, [config && config.connections && config.connections.length, selectedId]);

    useEffect(function () {
      return function () {
        if (oauthPoll.current) clearInterval(oauthPoll.current);
        if (oauthCliPoll.current) clearInterval(oauthCliPoll.current);
      };
    }, []);

    useEffect(function () {
      saveQueueMounted.current = true;
      return function () {
        saveQueueMounted.current = false;
        if (saveQueueTimer.current) clearTimeout(saveQueueTimer.current);
        saveQueueTimer.current = null;
      };
    }, []);

    useEffect(function () {
      if (!config || dirtyRef.current) return;
      queuedSnapshot.current = config;
      if (Number.isInteger(config.revision)) knownRevision.current = config.revision;
    }, [config]);

    useEffect(function () {
      if (!connectionMenu) return;
      function restoreTriggerFocus() {
        var trigger = connectionMenuReturnFocus.current;
        if (!trigger || !trigger.isConnected) return;
        window.requestAnimationFrame(function () {
          try { trigger.focus({ preventScroll: true }); } catch (error) { trigger.focus(); }
        });
      }
      function closeFromPointer(event) {
        if (connectionMenuRef.current && connectionMenuRef.current.contains(event.target)) return;
        setConnectionMenu(null);
      }
      function closeFromKey(event) {
        if (event.key !== "Escape") return;
        event.preventDefault();
        event.stopPropagation();
        setConnectionMenu(null);
        restoreTriggerFocus();
      }
      function closeFromViewport() {
        setConnectionMenu(null);
        restoreTriggerFocus();
      }
      document.addEventListener("pointerdown", closeFromPointer, true);
      document.addEventListener("keydown", closeFromKey, true);
      window.addEventListener("scroll", closeFromViewport, true);
      window.addEventListener("resize", closeFromViewport);
      window.requestAnimationFrame(function () {
        var firstItem = connectionMenuRef.current && connectionMenuRef.current.querySelector('[role="menuitem"]');
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
    }, [connectionMenu && connectionMenu.connectionId]);

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
      if (error && error.status === 409) return "模型配置已在其他位置更新，请重新加载后再修改。";
      return localizedModelConfigurationError(error, props) || "保存模型配置失败。";
    }

    function handleQueuedSaveFailure(error) {
      if (!saveQueueMounted.current) return;
      var failedVersion = queuedVersion.current;
      saveQueueBlockedVersion.current = failedVersion;
      setQueueDirty(true);
      var message = queueErrorMessage(error);
      setSaveError(message);
      store.setError(message);
      showSettingsToast(message, "error");
      if (!error || error.status !== 409) return;
      if (!window.confirm("模型配置已被其他页面更新。是否立即重新加载最新配置？当前未保存更改将丢失。")) return;
      store.load({
        isCurrent: function () {
          return saveQueueMounted.current && queuedVersion.current === failedVersion;
        },
      }).then(function (reloaded) {
        if (!saveQueueMounted.current || queuedVersion.current !== failedVersion) return;
        queuedSnapshot.current = reloaded;
        if (Number.isInteger(reloaded.revision)) knownRevision.current = reloaded.revision;
        saveQueueBlockedVersion.current = -1;
        setQueueDirty(false);
        setSaveError("");
        store.setError("");
      }).catch(function (reloadError) {
        if (!saveQueueMounted.current || queuedVersion.current !== failedVersion) return;
        saveQueueBlockedVersion.current = failedVersion;
        setQueueDirty(true);
        var reloadMessage = reloadError.message || String(reloadError);
        setSaveError(reloadMessage);
        store.setError(reloadMessage);
      });
    }

    function persistQueuedConfig() {
      if (!saveQueueMounted.current || saveQueueInFlight.current || !dirtyRef.current) return;
      if (saveQueueBlockedVersion.current === queuedVersion.current) return;
      var snapshot = queuedSnapshot.current;
      var version = queuedVersion.current;
      if (!snapshot) return;
      saveQueueInFlight.current = true;
      store.save(snapshot, true, {
        silentSuccess: true,
        surfaceErrors: false,
        handleConflict: false,
        isCurrent: function () {
          return saveQueueMounted.current && queuedVersion.current === version;
        },
      }).then(function (saved) {
        saveQueueInFlight.current = false;
        if (!saveQueueMounted.current) return;
        if (Number.isInteger(saved.revision)) knownRevision.current = saved.revision;
        if (queuedVersion.current === version) {
          queuedSnapshot.current = saved;
          saveQueueBlockedVersion.current = -1;
          setQueueDirty(false);
          setSaveError("");
          store.setError("");
          return;
        }
        queuedSnapshot.current = Object.assign({}, queuedSnapshot.current || {}, {
          revision: knownRevision.current,
        });
        scheduleQueuedSave(0);
      }).catch(function (error) {
        saveQueueInFlight.current = false;
        handleQueuedSaveFailure(error);
      });
    }

    function updateConfig(next, options) {
      options = options || {};
      var snapshot = next;
      if (Number.isInteger(knownRevision.current)) {
        snapshot = Object.assign({}, next, { revision: knownRevision.current });
      }
      editVersion.current += 1;
      queuedVersion.current = editVersion.current;
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
            next.adapter_id = value;
            if (managedName && (next.name === "新服务商" || next.name === previousManagedName)) next.name = managedName;
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
      }));
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
        connectionDisplayName(connection), connection.name, connection.id,
        connection.adapter, adapter.name, connection.base_url,
      ].concat(modelTerms).filter(Boolean).join(" ").toLowerCase().indexOf(needle) >= 0;
    }

    function addConnection() {
      var firstAdapter = selectableConnectionAdapters()[0];
      if (!firstAdapter) {
        showSettingsToast(label(props, "settings.adapterUnavailable", "当前服务没有可添加的协议。"), "error");
        return;
      }
      var id = safeId("connection", "new-" + Date.now());
      var connection = normalizeConnection({
        id: id,
        name: "新服务商",
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
        showSettingsToast("暂时无法打开删除确认，请稍后重试。", "error");
        if (returnFocus && returnFocus.isConnected) window.requestAnimationFrame(function () { returnFocus.focus(); });
        return;
      }
      feedback.confirmModal({
        title: "删除模型服务？",
        body: "“" + connectionDisplayName(target) + "”以及它的模型档案将被删除，并立即保存。",
        confirmLabel: "删除",
        cancelLabel: "取消",
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
      var profile = normalizeProfile(Object.assign({
        id: safeId("profile", selected.id + "-new-" + Date.now()),
        connection_id: selected.id,
        name: "新模型",
        model: "",
        capabilities: ["chat"],
      }, raw || {}), config.profiles.length, selected.id);
      updateConfig(Object.assign({}, config, { profiles: config.profiles.concat([profile]) }));
    }

    function updateProfile(profileId, key, value) {
      updateConfig(Object.assign({}, config, {
        profiles: config.profiles.map(function (profile) {
          if (profile.id !== profileId) return profile;
          var next = Object.assign({}, profile);
          next[key] = value;
          if (key === "model") next.model_id = value;
          if (key === "context_limit") next.ctx = value;
          return next;
        }),
      }));
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
        showSettingsToast(String(payload.message || payload.detail || "模型测试成功。"), "success");
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
        setOauthCliError(label(props, "settings.codexCliDownloadTimeout", "Codex CLI 下载超时，请重试。"));
        return;
      }
      requestJson("/api/settings/openai-oauth/cli").then(function (cli) {
        setOauth(function (previous) { return Object.assign({}, previous, { cli: cli }); });
        if (cli.downloading || (!cli.installed && !cli.error)) return;
        stopOauthCliPolling();
        setOauthBusy("");
        if (cli.installed) {
          showSettingsToast(label(props, "settings.codexCliReady", "Codex CLI 已就绪，现在可以登录。"), "success");
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
          showSettingsToast(label(props, "settings.codexCliReady", "Codex CLI 已就绪，现在可以登录。"), "success");
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
      showSettingsToast("已导入 " + (oauth.models || []).length + " 个 Codex 模型，正在自动保存。", "success");
    }

    function importOauthModel(model) {
      updateConfig(mergeDiscoveredProfiles(config, selected.id, model ? [model] : []));
      showSettingsToast("已添加 Codex 模型档案，正在自动保存。", "success");
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
    var adapters = config.adapters || [];
    var selectableAdapters = selectableConnectionAdapters();
    var currentAdapter = selected && (adapters.find(function (adapter) { return adapter.id === selected.adapter; })
      || normalizeAdapter({ id: selected.adapter, name: selected.adapter }, adapters.length));
    var editorAdapters = selectableAdapters.slice();
    if (currentAdapter && !editorAdapters.some(function (adapter) { return adapter.id === currentAdapter.id; })) {
      editorAdapters.unshift(currentAdapter);
    }
    var selectedDescription = selected
      ? (isLocalConnection(selected)
        ? label(props, "settings.localModelsHint", "可选的本机增强能力，数据无需离开设备；下载需手动触发并支持断点续传。")
        : String(currentAdapter && currentAdapter.description || "").trim())
      : "";
    var menuConnection = connectionMenu && config.connections.find(function (connection) { return connection.id === connectionMenu.connectionId; });
    var connectionMenuNode = menuConnection ? h("div", {
      ref: connectionMenuRef,
      className: "wb-mcfg-context-menu",
      role: "menu",
      id: "wb-mcfg-connection-menu",
      "aria-label": connectionDisplayName(menuConnection) + " 操作",
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
      }, browserIcon("trash", 15), "删除")
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
      connectionDisplayName: connectionDisplayName, isLocalConnection: isLocalConnection,
      localModels: localModels, addConnection: addConnection, selectedDescription: selectedDescription,
      refreshLocalModels: refreshLocalModels, Toggle: Toggle, updateConnection: updateConnection,
      Field: Field, editorAdapters: editorAdapters, isUserSelectableAdapter: isUserSelectableAdapter,
      adapterOptionName: adapterOptionName, isCodexConnection: isCodexConnection,
      OAuthSection: OAuthSection, oauth: oauth, oauthBusy: oauthBusy,
      startOauthLogin: startOauthLogin, logoutOauth: logoutOauth, downloadOauthCli: downloadOauthCli,
      importOauthModels: importOauthModels, importOauthModel: importOauthModel,
      LocalModelsSection: LocalModelsSection, localRuntime: localRuntime, localError: localError,
      localBusy: localBusy, manageLocalModel: manageLocalModel, profiles: profiles,
      proxyMasterEnabled: proxyMasterEnabled,
      addProfile: addProfile, ProfileEditor: ProfileEditor, updateProfile: updateProfile,
      removeProfile: removeProfile, testProfile: testProfile, busy: busy,
    };

    return h("div", { id: "setting-model-services", className: "settings-panel settings-panel-wide wb-model-config-page" },
      h("header", { className: "wb-mcfg-page-head" },
        h("div", null,
          h("h2", null, label(props, "settings.modelServices", "模型服务")),
          h("p", null, label(props, "settings.modelServicesHint", "配置模型平台、认证信息和可复用的模型档案。模型配置在独立页面设置。"))
        )
      ),
      saveError ? h("div", { className: "wb-mcfg-status is-error wb-mcfg-save-error", role: "alert" },
        h("span", null, saveError),
        dirty ? h("button", { type: "button", className: "wb-btn", disabled: store.saveState === "saving", onClick: retryQueuedSave }, "重试保存") : null
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
        h("span", { className: "wb-mcfg-route-count" }, selected.length + " 个模型")
      ),
      h("div", { className: "wb-mcfg-route-list" },
        !selected.length ? h("div", { className: "wb-mcfg-route-empty" }, "尚未选择模型。") : null,
        selected.map(function (id, index) {
          var profile = props.profileById[id];
          if (!profile) {
            return h("article", { className: "wb-mcfg-route-row is-missing", key: id },
              h("div", null, h("strong", null, id), h("small", null, "模型档案已不存在")),
              h("button", { type: "button", className: "wb-mcfg-icon-btn is-danger", onClick: function () { props.onRemove(id); }, "aria-label": "移除失效模型 " + id }, browserIcon("close", 15))
            );
          }
          var connection = props.connectionById[profile.connection_id] || {};
          return h("article", { className: "wb-mcfg-route-row", key: id },
            h("span", { className: "wb-mcfg-rank", "aria-label": "顺位 " + (index + 1) }, index + 1),
            h("div", { className: "wb-mcfg-route-model" },
              h("strong", null, profile.name || profile.model),
              h("small", null, (connection.name || "未知服务商") + " · " + profile.model)
            ),
            h("div", { className: "wb-mcfg-route-meta" },
              h("span", null, "上下文 " + formatContext(profile)),
              h("span", null, capabilityText(profile, props))
            ),
            meta.ordered ? h("div", { className: "wb-mcfg-order-actions", role: "group", "aria-label": "调整 " + (profile.name || profile.model) + " 顺位" },
              h("button", { type: "button", disabled: index === 0, onClick: function () { props.onMove(id, -1); }, "aria-label": "上移 " + (profile.name || profile.model) }, settingsGlyph("arrow-up", 16)),
              h("button", { type: "button", disabled: index === selected.length - 1, onClick: function () { props.onMove(id, 1); }, "aria-label": "下移 " + (profile.name || profile.model) }, settingsGlyph("arrow-down", 16))
            ) : null,
            h("button", { type: "button", className: "wb-mcfg-icon-btn is-danger", onClick: function () { props.onRemove(id); }, "aria-label": "移除 " + (profile.name || profile.model) }, browserIcon("close", 15))
          );
        })
      ),
      h("label", { className: "wb-mcfg-route-add" },
        h("span", null, "添加模型"),
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
          h("h2", null, label(props, "settings.modelUsage", "模型配置")),
          h("p", null, label(props, "settings.modelUsageHint", "用途只引用模型档案。更换 API Key 或 Endpoint 不会改变默认顺位。"))
        ),
        h("div", { className: "wb-mcfg-save-cluster" },
          dirty ? h("span", { className: "wb-mcfg-unsaved" }, "有未保存更改") : null,
          h("button", { type: "button", className: "wb-btn primary", disabled: !dirty || store.saveState === "saving", onClick: saveRoutes }, store.saveState === "saving" ? "正在保存…" : "保存用途")
        )
      ),
      !config.profiles.length ? h("div", { className: "wb-mcfg-state" },
        h("strong", null, "还没有模型档案"),
        h("span", null, "请先在“模型服务”中添加连接和模型档案。")
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
