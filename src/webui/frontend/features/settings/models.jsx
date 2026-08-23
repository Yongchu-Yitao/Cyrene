import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  useRefSt,
  DEFAULT_MODEL_BASE_URL,
  createEmptyModel,
  normalizeModel,
  codexModelId,
  codexModelSelectOptions,
  codexModelReasoningEfforts,
  downloadPercent,
  pollUntil,
  readSettingsResponse,
  settingsFetch,
  SectionTitle,
  FieldRow,
  ModelCard,
  ModelField,
} from "./shared.jsx"

// ── Models Panel ──
function EmbeddingSettingsSection(p) {
  var { t, settings, setSettings, apiKey, setApiKey, status, setStatus, busy, setBusy, anchorId } = p;

  function draft() {
    var payload = {
      provider: settings.provider,
      base_url: settings.base_url,
      model: settings.model,
      dimensions: Number(settings.dimensions) || 0,
    };
    if (apiKey.trim()) payload.api_key = apiKey.trim();
    return payload;
  }

  function test() {
    setBusy("test");
    setStatus({ kind: "info", text: t("settings.testingConnection") });
    settingsFetch("/api/settings/integrations/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ service: "embedding", config: draft() }),
    }).then(readSettingsResponse).then(function (payload) {
      setStatus(payload.fallback
        ? { kind: "info", text: t("settings.embeddingLocalFallback") }
        : { kind: "success", text: t("settings.embeddingConnected", { dimensions: payload.dimensions || 0 }) });
    }).catch(function (error) {
      setStatus({ kind: "error", text: t("settings.connectionFailed") + ": " + (error.message || "") });
    }).finally(function () { setBusy(""); });
  }

  function clearApiKey() {
    setBusy("clear");
    settingsFetch("/api/settings/integrations", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ embedding: { clear_api_key: true } }),
    }).then(readSettingsResponse).then(function (payload) {
      if (payload.embedding) setSettings(payload.embedding);
      setApiKey("");
      setStatus({ kind: "success", text: t("settings.embeddingKeyCleared") });
    }).catch(function (error) {
      setStatus({ kind: "error", text: t("settings.error") + ": " + (error.message || "") });
    }).finally(function () { setBusy(""); });
  }

  return ModelSettingsSection({
    title: t("settings.embeddingIntegration"),
    description: t("settings.embeddingIntegrationHint"),
    anchorId: anchorId,
    children: [
      FieldRow(t("settings.embeddingProvider"), t("settings.embeddingProviderHint"),
        React.createElement("select", {
          className: "wb-select", value: settings.provider,
          "aria-label": t("settings.embeddingProvider"),
          onChange: function (e) {
            var provider = e.target.value;
            var nextBase = settings.base_url;
            if (provider === "ollama" && !nextBase) nextBase = "http://127.0.0.1:11434";
            if (provider === "local_onnx") {
              setSettings({ ...settings, provider: provider, base_url: "", model: "qwen3-embedding-0.6b", dimensions: 1024 });
            } else {
              setSettings({ ...settings, provider: provider, base_url: nextBase });
            }
          },
        },
          React.createElement("option", { value: "openai_compatible" }, t("settings.embeddingOpenAiCompatible")),
          React.createElement("option", { value: "ollama" }, "Ollama"),
          React.createElement("option", { value: "local_onnx" }, t("settings.embeddingLocalOnnx")),
        ),
      ),
      settings.provider !== "local_onnx" && FieldRow(t("settings.embeddingBaseUrl"), t("settings.embeddingBaseUrlHint"),
        React.createElement("input", {
          className: "wb-input mono", type: "url", value: settings.base_url,
          placeholder: settings.provider === "ollama" ? "http://127.0.0.1:11434" : "https://api.openai.com/v1",
          "aria-label": t("settings.embeddingBaseUrl"),
          onChange: function (e) { setSettings({ ...settings, base_url: e.target.value }); },
        }),
      ),
      settings.provider !== "local_onnx" && FieldRow(t("settings.embeddingApiKey"), t("settings.embeddingApiKeyHint"),
        React.createElement("div", { className: "wb-integration-control wb-integration-key" },
          React.createElement("input", {
            className: "wb-input mono", type: "password", value: apiKey,
            autoComplete: "off", "aria-label": t("settings.embeddingApiKey"),
            placeholder: settings.api_key_configured ? t("settings.secretConfigured") : t("settings.optionalForLocal"),
            onChange: function (e) { setApiKey(e.target.value); },
          }),
          settings.api_key_configured && React.createElement("button", {
            className: "wb-btn muted", disabled: !!busy, onClick: clearApiKey,
          }, t("settings.clearStoredKey")),
        ),
      ),
      FieldRow(t("settings.embeddingModel"), t("settings.embeddingModelHint"),
        settings.provider === "local_onnx" ? React.createElement("select", {
          className: "wb-select mono", value: settings.model,
          onChange: function (e) { setSettings({ ...settings, model: e.target.value, dimensions: 1024 }); },
        }, React.createElement("option", { value: "qwen3-embedding-0.6b" }, "Qwen3 Embedding 0.6B")) :
        React.createElement("input", {
          className: "wb-input mono", value: settings.model,
          placeholder: "text-embedding-3-small", "aria-label": t("settings.embeddingModel"),
          onChange: function (e) { setSettings({ ...settings, model: e.target.value }); },
        }),
      ),
      FieldRow(t("settings.embeddingDimensions"), t("settings.embeddingDimensionsHint"),
        React.createElement("input", {
          className: "wb-input mono", type: "number", min: "0", max: "65536", step: "1",
          value: settings.dimensions, "aria-label": t("settings.embeddingDimensions"),
          readOnly: settings.provider === "local_onnx",
          onChange: function (e) { setSettings({ ...settings, dimensions: e.target.value }); },
        }),
      ),
      React.createElement("div", { className: "wb-integration-footer" },
        status && React.createElement("div", {
          className: "wb-integration-status " + status.kind,
          role: status.kind === "error" ? "alert" : "status",
          "aria-live": "polite",
        }, status.text),
        React.createElement("div", { className: "wb-integration-actions" },
          React.createElement("button", { className: "wb-btn", disabled: !!busy, onClick: test }, busy === "test" ? t("settings.testingConnection") : t("settings.testConnection")),
        ),
      ),
    ],
  });
}

function modelCredentialFields(model, update, t) {
  if (model.provider === "codex_oauth") {
    return [
      ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", {
        className: "wb-input mono", value: model.model, readOnly: true,
      })),
      React.createElement("div", { className: "wb-codex-provider-note", key: "provider" },
        React.createElement("span", { className: "wb-status-dot good" }),
        React.createElement("span", null, t("settings.openaiOAuthManaged")),
      ),
    ];
  }
  return [
    ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: model.model, onChange: function (e) { update("model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
    ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: model.api_key, onChange: function (e) { update("api_key", e.target.value); }, placeholder: "sk-..." })),
    ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: model.base_url, onChange: function (e) { update("base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
  ];
}

function normalizeLocalModels(models) {
  return (models || []).map(function (item) {
    if (item && item.ready) return { ...item, error: "" };
    return item;
  });
}

function localizeLocalModelError(rawError, t) {
  var lower = String(rawError || "").toLowerCase();
  if (lower.indexOf("archive output is missing or invalid") >= 0
      || lower.indexOf("archive has no declared outputs") >= 0) {
    return t("settings.localModelErrorExtract");
  }
  if (lower.indexOf("checksum") >= 0
      || lower.indexOf("sha256") >= 0
      || lower.indexOf("validation failed") >= 0) {
    return t("settings.localModelErrorChecksum");
  }
  if (lower.indexOf("all mirrors failed") >= 0
      || lower.indexOf("connect") >= 0
      || lower.indexOf("timeout") >= 0
      || lower.indexOf("network") >= 0
      || lower.indexOf("proxy") >= 0
      || lower.indexOf("resolve") >= 0
      || lower.indexOf("httpx") >= 0
      || lower.indexOf("remote protocol") >= 0) {
    return t("settings.localModelErrorNetwork");
  }
  return t("settings.localModelErrorGeneric");
}

function ModelsPanel(p) {
  var { t, models, setModels, modelSource, setModelSource, codexCandidate, setCodexCandidate, draftModel, setDraftModel, visionModels, setVisionModels, draftVision, setDraftVision, secondaryModel, setSecondaryModel, modelsSaved, modelsSaving, saveModels, config, project } = p;
  var [embeddingSettings, setEmbeddingSettings] = useStateSt({ provider: "openai_compatible", base_url: "", model: "", dimensions: 0, api_key_configured: false });
  var [embeddingApiKey, setEmbeddingApiKey] = useStateSt("");
  var [embeddingStatus, setEmbeddingStatus] = useStateSt(null);
  var [embeddingBusy, setEmbeddingBusy] = useStateSt("");
  var [localModels, setLocalModels] = useStateSt([]);
  var [localBusy, setLocalBusy] = useStateSt("");
  var [cv2Runtime, setCv2Runtime] = useStateSt(null);
  var [corpusEmbedding, setCorpusEmbedding] = useStateSt(null);
  var savedEmbeddingIdentityRef = useRefSt("");
  var voiceModelSignatureRef = useRefSt("");
  var workspaceId = String(project && (project.id || project.dataKey) || "");
  var savedCodexCandidate = codexCandidate;
  var [codexState, setCodexState] = useStateSt({
    available: true,
    connected: !!savedCodexCandidate,
    checking: true,
    models: [],
    limits: {},
    quota_enabled: true,
  });
  var [codexModel, setCodexModel] = useStateSt(savedCodexCandidate ? savedCodexCandidate.model : "");
  var [codexEffort, setCodexEffort] = useStateSt(savedCodexCandidate ? savedCodexCandidate.reasoning_effort : "");
  var [codexBusy, setCodexBusy] = useStateSt("");
  var [codexNotice, setCodexNotice] = useStateSt("");
  var [codexCliBusy, setCodexCliBusy] = useStateSt(false);
  var [codexCliProgress, setCodexCliProgress] = useStateSt(null);
  var [primaryMenuOpen, setPrimaryMenuOpen] = useStateSt(false);
  var [hoveredPrimarySource, setHoveredPrimarySource] = useStateSt("");
  var primarySource = modelSource;
  var setPrimarySource = setModelSource;
  var codexPoll = useRefSt(null);
  var codexCliPoll = useRefSt(null);
  var primarySourceRef = useRefSt(null);
  var codexCandidateRef = useRefSt(savedCodexCandidate);
  codexCandidateRef.current = codexCandidate;

  function loadLocalModels() {
    return settingsFetch("/api/settings/local-models/status").then(readSettingsResponse).then(function (payload) {
      var items = normalizeLocalModels(payload.models || []);
      setCv2Runtime(payload.cv2_runtime || null);
      setLocalModels(items);
      var voiceSignature = items.filter(function (item) {
        return item.kind === "asr" || item.kind === "tts";
      }).map(function (item) {
        return item.id + ":" + (item.ready ? "1" : "0");
      }).join("|");
      if (voiceModelSignatureRef.current && voiceModelSignatureRef.current !== voiceSignature) {
        window.dispatchEvent(new Event("cyrene:voice-status-changed"));
      }
      voiceModelSignatureRef.current = voiceSignature;
      return items;
    });
  }

  function loadCorpusEmbedding() {
    return settingsFetch("/api/workbench/library/embedding/status?workspace=" + encodeURIComponent(workspaceId))
      .then(readSettingsResponse).then(function (payload) {
        setCorpusEmbedding(function (previous) {
          if (previous && previous.reembed && previous.reembed.running && payload.reembed && !payload.reembed.running) {
            if (payload.reembed.error) {
              setEmbeddingStatus({ kind: "error", text: t("settings.reembedFailed") + ": " + payload.reembed.error });
            } else {
              setEmbeddingStatus({ kind: "success", text: t("settings.reembedComplete", { count: payload.reembed.updated || 0 }) });
            }
          }
          return payload;
        });
        return payload;
      }).catch(function () {});
  }

  useEffectSt(function () {
    var cancelled = false;
    settingsFetch("/api/settings/integrations").then(readSettingsResponse).then(function (payload) {
      if (!cancelled && payload.embedding) {
        setEmbeddingSettings(payload.embedding);
        savedEmbeddingIdentityRef.current = [payload.embedding.provider, payload.embedding.model].join(":");
      }
    }).catch(function (error) {
      if (!cancelled) setEmbeddingStatus({ kind: "error", text: t("settings.integrationLoadFailed") + ": " + (error.message || "") });
    });
    loadLocalModels().catch(function () {});
    loadCorpusEmbedding();
    var timer = setInterval(function () {
      loadLocalModels().then(function (items) {
        if (!items.some(function (item) { return item.downloading; })) setLocalBusy("");
      }).catch(function () {});
      loadCorpusEmbedding();
    }, 1200);
    return function () { cancelled = true; clearInterval(timer); };
  }, []);

  function embeddingDraft() {
    var payload = {
      provider: embeddingSettings.provider,
      base_url: embeddingSettings.base_url,
      model: embeddingSettings.model,
      dimensions: Number(embeddingSettings.dimensions) || 0,
    };
    if (embeddingApiKey.trim()) payload.api_key = embeddingApiKey.trim();
    return payload;
  }

  function saveAllModels() {
    setEmbeddingStatus(null);
    saveModels(embeddingDraft(), function (saved) {
      var previousIdentity = savedEmbeddingIdentityRef.current;
      if (saved) setEmbeddingSettings(saved);
      var nextIdentity = saved ? [saved.provider, saved.model].join(":") : "";
      savedEmbeddingIdentityRef.current = nextIdentity;
      setEmbeddingApiKey("");
      setEmbeddingStatus(null);
      loadCorpusEmbedding().then(function (coverage) {
        if (!coverage || !coverage.configured || !coverage.pending_vectors) return;
        if (nextIdentity !== "local_onnx:qwen3-embedding-0.6b" || previousIdentity === nextIdentity) return;
        var feedback = workbenchServices.feedback();
        var title = t("settings.reembedPromptTitle");
        var body = t("settings.reembedPromptBody", { count: coverage.pending_vectors });
        var confirmed = feedback && typeof feedback.confirmModal === "function"
          ? feedback.confirmModal({ title: title, body: body, confirmLabel: t("settings.reembed") })
          : Promise.resolve(window.confirm([title, "", body].join("\n")));
        confirmed.then(function (ok) { if (ok) reembedKnowledge(); });
      });
    });
  }

  function manageLocalModel(modelId, action) {
    setLocalBusy(modelId + ":" + action);
    var modelRequest = settingsFetch("/api/settings/local-models/" + encodeURIComponent(modelId) + (action === "download" ? "/download" : ""), {
      method: action === "download" ? "POST" : "DELETE",
    }).then(readSettingsResponse);
    // OCR needs the OpenCV runtime too; download both together so the model
    // does not finish while its runtime is still missing. Keep the two
    // downloads independent: a failure in one must not cancel or mask the other.
    var runtimeRequest = action === "download" && modelId === "pp-ocrv6-medium" && cv2Runtime && !cv2Runtime.installed && !cv2Runtime.downloading
      ? settingsFetch("/api/settings/local-models/ocr-runtime/download", { method: "POST" }).then(readSettingsResponse)
      : Promise.resolve(null);
    Promise.allSettled([modelRequest, runtimeRequest]).then(function (results) {
      setLocalBusy("");
      return loadLocalModels().then(function () {
        var modelResult = results[0];
        var runtimeResult = results[1];
        if (modelResult.status === "fulfilled" && runtimeResult.status === "fulfilled") return;
        var messages = [];
        if (modelResult.status === "rejected") {
          messages.push(modelResult.reason && modelResult.reason.message || t("settings.error"));
        }
        if (runtimeResult.status === "rejected") {
          messages.push(modelResult.status === "fulfilled"
            ? t("settings.localModelRuntimeFailed")
            : (runtimeResult.reason && runtimeResult.reason.message || t("settings.ocrRuntimeFailed")));
        }
        setEmbeddingStatus({ kind: "error", text: messages.join(" ") });
      });
    }).catch(function (error) {
      setLocalBusy("");
      setEmbeddingStatus({ kind: "error", text: error.message || t("settings.error") });
    });
  }

  function reembedKnowledge() {
    setEmbeddingStatus({ kind: "info", text: t("settings.reembedding") });
    setCorpusEmbedding(function (previous) {
      return previous ? { ...previous, reembed: { running: true, error: "" } } : previous;
    });
    return settingsFetch("/api/workbench/library/reembed?workspace=" + encodeURIComponent(workspaceId), { method: "POST" })
      .then(readSettingsResponse).then(function () { return loadCorpusEmbedding(); })
      .catch(function (error) {
        setEmbeddingStatus({ kind: "error", text: t("settings.reembedFailed") + ": " + (error.message || "") });
      });
  }

  function LocalModelIcon(kind) {
    if (kind === "asr" || kind === "tts") {
      var BrowserIcon = workbenchServices.browser().Icon;
      return React.createElement(BrowserIcon, {
        name: kind === "asr" ? "microphone" : "volume",
        size: 20,
      });
    }
    if (kind === "embedding") {
      return React.createElement("svg", {
        width: "20", height: "20", viewBox: "0 0 24 24", fill: "none",
        stroke: "currentColor", strokeWidth: "1.8", strokeLinecap: "round",
        strokeLinejoin: "round", "aria-hidden": "true",
      },
        React.createElement("circle", { cx: "6", cy: "6", r: "2.25" }),
        React.createElement("circle", { cx: "18", cy: "6", r: "2.25" }),
        React.createElement("circle", { cx: "12", cy: "18", r: "2.25" }),
        React.createElement("path", { d: "m7.9 7.2 2.7 8.6M16.1 7.2l-2.7 8.6M8.3 6h7.4" }),
      );
    }
    return React.createElement("svg", {
      width: "20", height: "20", viewBox: "0 0 24 24", fill: "none",
      stroke: "currentColor", strokeWidth: "1.8", strokeLinecap: "round",
      strokeLinejoin: "round", "aria-hidden": "true",
    },
      React.createElement("path", { d: "M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" }),
      React.createElement("path", { d: "M8 10h8M8 14h6" }),
    );
  }

  useEffectSt(function () {
    if (!primaryMenuOpen) return;
    function closePrimaryMenu(event) {
      if (primarySourceRef.current && !primarySourceRef.current.contains(event.target)) {
        setPrimaryMenuOpen(false);
        setHoveredPrimarySource("");
      }
    }
    function closePrimaryMenuOnEscape(event) {
      if (event.key === "Escape") {
        setPrimaryMenuOpen(false);
        setHoveredPrimarySource("");
      }
    }
    document.addEventListener("pointerdown", closePrimaryMenu, true);
    document.addEventListener("keydown", closePrimaryMenuOnEscape);
    return function () {
      document.removeEventListener("pointerdown", closePrimaryMenu, true);
      document.removeEventListener("keydown", closePrimaryMenuOnEscape);
    };
  }, [primaryMenuOpen]);

  useEffectSt(function () {
    var saved = codexCandidate;
    if (!saved) return;
    setCodexModel(saved.model || "");
    setCodexEffort(saved.reasoning_effort || "");
    setCodexState(function (previous) {
      return { ...previous, connected: true };
    });
  }, [codexCandidate]);

  function loadCodexState() {
    return settingsFetch("/api/settings/openai-oauth")
      .then(readSettingsResponse)
      .then(function (data) {
        setCodexState({ ...data, checking: false });
        try { window.dispatchEvent(new CustomEvent("cyrene:codex-auth-changed", { detail: data })); } catch (e) {}
        var options = data.models || [];
        var saved = codexCandidateRef.current;
        var savedModel = saved && saved.model || "";
        var selected = options.find(function (item) { return codexModelId(item) === savedModel; });
        var preferred = selected || options.find(function (item) { return item.isDefault || item.is_default; }) || options[0];
        if (preferred) {
          setCodexModel(savedModel || codexModelId(preferred));
          setCodexEffort(saved && saved.reasoning_effort || String(preferred.defaultReasoningEffort || preferred.default_reasoning_effort || ""));
        }
        return data;
      })
      .catch(function (error) {
        setCodexState(function (previous) {
          return { ...previous, available: false, checking: false, models: [], error: error.message };
        });
      });
  }

  useEffectSt(function () {
    loadCodexState();
    return function () {
      if (codexPoll.current) clearInterval(codexPoll.current);
      if (codexCliPoll.current) clearInterval(codexCliPoll.current);
    };
  }, []);

  function startCodexLogin() {
    setCodexBusy("login"); setCodexNotice("");
    settingsFetch("/api/settings/openai-oauth/login", { method: "POST" })
      .then(readSettingsResponse)
      .then(function (data) {
        var authUrl = data.authUrl || data.auth_url || data.url;
        if (authUrl) window.open(authUrl, "_blank", "noopener,noreferrer");
        if (codexPoll.current) clearInterval(codexPoll.current);
        codexPoll.current = pollUntil(function () {
          return loadCodexState().then(function (state) {
            return state && state.connected ? { done: true } : null;
          });
        }, {
          intervalMs: 1500,
          onDone: function () {
            codexPoll.current = null;
            setCodexBusy(""); setCodexNotice(t("settings.openaiOAuthConnected"));
          },
          onError: function (error) {
            codexPoll.current = null;
            setCodexBusy(""); setCodexNotice(String(error && error.message || error || ""));
          },
        });
      })
      .catch(function (error) { setCodexBusy(""); setCodexNotice(error.message); });
  }

  function downloadCodexCli(force) {
    setCodexCliBusy(true); setCodexNotice("");
    var init = { method: "POST" };
    if (force) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify({ force: true });
    }
    settingsFetch("/api/settings/openai-oauth/cli/download", init)
      .then(readSettingsResponse)
      .then(function () {
        if (codexCliPoll.current) clearInterval(codexCliPoll.current);
        codexCliPoll.current = pollUntil(function () {
          return settingsFetch("/api/settings/openai-oauth/cli")
            .then(readSettingsResponse)
            .then(function (cli) {
              setCodexCliProgress(cli.installed ? null : {
                downloaded_bytes: cli.downloaded_bytes || 0,
                total_bytes: cli.total_bytes || 0,
              });
              // Priority: an in-flight download wins over any stale error
              // that the backend has not reset yet.
              if (cli.downloading) return null;
              if (cli.installed) return { done: true };
              if (cli.error) return { done: true, error: cli.error };
              return null;
            });
        }, {
          intervalMs: 1000,
          timeoutMs: 600000,
          onDone: function () {
            codexCliPoll.current = null;
            setCodexCliBusy(false);
            setCodexNotice(t("settings.codexCliReady"));
            return loadCodexState();
          },
          onError: function (error) {
            codexCliPoll.current = null;
            setCodexCliBusy(false);
            setCodexNotice(error && error.code === "poll_timeout"
              ? t("settings.codexCliDownloadTimeout")
              : String(error && error.message || error || ""));
          },
        });
      })
      .catch(function (error) {
        setCodexCliBusy(false);
        setCodexNotice(error.message || "");
      });
  }

  function logoutCodex() {
    setCodexBusy("logout"); setCodexNotice("");
    settingsFetch("/api/settings/openai-oauth/logout", { method: "POST" })
      .then(readSettingsResponse)
      .then(function () { setCodexBusy(""); setCodexModel(""); return loadCodexState(); })
      .catch(function (error) { setCodexBusy(""); setCodexNotice(error.message); });
  }

  function setCodexPrimaryCandidate(selectedModel, selectedEffort) {
    var targetModel = selectedModel || codexModel;
    var targetEffort = selectedEffort != null ? selectedEffort : codexEffort;
    if (!targetModel) return;
    setCodexCandidate(normalizeModel({
      id: "codex-" + targetModel,
      model: targetModel,
      desc: "OpenAI OAuth",
      price: t("settings.codexQuota"),
      provider: "codex_oauth",
      reasoning_effort: targetEffort,
      api_key: "",
      base_url: "codex://oauth",
    }, 0, "", ""));
    setCodexNotice(t("settings.openaiOAuthPrimaryReady"));
  }

  function selectCustomPrimary() {
    setPrimaryMenuOpen(false); setPrimarySource("custom");
  }

  function selectCodexPrimary() {
    setPrimaryMenuOpen(false); setPrimarySource("codex");
    if (codexState.connected && codexModel) setCodexPrimaryCandidate();
  }

  function updateModel(id, field, val) {
    setModels(models.map(function (m) {
      if (m.id !== id) return m;
      // Clear server-supplied priceHint when user changes the model identifier
      var extra = field === "model" ? { name: val, priceHint: "" } : {};
      return { ...m, [field]: val, ...extra };
    }));
  }
  function moveModel(id, dir) {
    var idx = models.findIndex(function (m) { return m.id === id; });
    var tgt = idx + dir;
    if (idx < 0 || tgt < 0 || tgt >= models.length) return;
    var next = models.slice();
    var cur = next[idx]; next[idx] = next[tgt]; next[tgt] = cur;
    setModels(next);
  }
  function deleteModel(id) { if (models.length > 1) setModels(models.filter(function (m) { return m.id !== id; })); }
  function addModel() { var c = normalizeModel(draftModel, models.length, "", ""); if (!c.model) return; setModels(models.concat(c)); setDraftModel(createEmptyModel()); }

  function updateVisionModel(id, field, val) {
    setVisionModels(visionModels.map(function (m) { return m.id === id ? { ...m, [field]: val, name: field === "model" ? val : m.name } : m; }));
  }
  function moveVisionModel(id, dir) {
    var idx = visionModels.findIndex(function (m) { return m.id === id; });
    var tgt = idx + dir;
    if (idx < 0 || tgt < 0 || tgt >= visionModels.length) return;
    var next = visionModels.slice();
    var cur = next[idx]; next[idx] = next[tgt]; next[tgt] = cur;
    setVisionModels(next);
  }
  function deleteVisionModel(id) { if (visionModels.length > 1) setVisionModels(visionModels.filter(function (m) { return m.id !== id; })); }
  function addVisionModel() { var c = normalizeModel(draftVision, visionModels.length, "", ""); if (!c.model) return; setVisionModels(visionModels.concat(c)); setDraftVision(createEmptyModel()); }

  function updateSecondary(field, val) { setSecondaryModel(function (prev) { return prev ? { ...prev, [field]: val, name: field === "model" ? val : prev.name } : prev; }); }

  var fallbackCount = Math.max(0, models.length - 1);
  var visionFallbackCount = Math.max(0, visionModels.length - 1);
  var secondaryConfigured = !!String(secondaryModel && secondaryModel.model || "").trim();
  var visionConfigured = !!String(visionModels[0] && visionModels[0].model || "").trim();
  var codexModelOptions = codexModelSelectOptions(codexState.models, codexModel);
  var selectedCodexModel = codexModelOptions.find(function (item) { return codexModelId(item) === codexModel; });
  var codexEffortOptions = codexModelReasoningEfforts(selectedCodexModel, codexEffort);
  var codexCliRequired = !!(codexState.cli && (!codexState.cli.installed || codexState.cli.broken));
  var codexCliDownloading = !!(codexCliBusy || (codexState.cli && codexState.cli.downloading));
  var codexCliPercent = downloadPercent(codexCliProgress);

  return React.createElement("div", { className: "settings-panel wb-models-panel" },
    SectionTitle(t("settings.models"), t("settings.modelsSubtitle")),

    // Primary model
    ModelSettingsSection({
      title: t("settings.primaryModelSlot"),
      anchorId: "setting-model-source",
      headerAction: React.createElement("div", { className: "wb-primary-source", ref: primarySourceRef },
        React.createElement("button", {
          className: "wb-primary-source-trigger",
          onClick: function () { setPrimaryMenuOpen(!primaryMenuOpen); },
          "aria-expanded": primaryMenuOpen,
        },
          React.createElement("span", null, primarySource === "codex" ? "OpenAI OAuth" : t("settings.customModel")),
        ),
        primaryMenuOpen && React.createElement("div", {
          className: "wb-primary-source-menu",
          onMouseLeave: function () { setHoveredPrimarySource(""); },
        },
          React.createElement("button", {
            className: "wb-menu-item" + (!hoveredPrimarySource && primarySource === "custom" ? " active" : ""),
            onMouseEnter: function () { setHoveredPrimarySource("custom"); },
            onClick: selectCustomPrimary,
          },
            React.createElement("strong", null, t("settings.customModel")),
            React.createElement("small", null, t("settings.customModelHint")),
          ),
          React.createElement("button", {
            className: "wb-menu-item" + (!hoveredPrimarySource && primarySource === "codex" ? " active" : ""),
            onMouseEnter: function () { setHoveredPrimarySource("codex"); },
            onClick: selectCodexPrimary,
          },
            React.createElement("strong", null, "OpenAI OAuth"),
            React.createElement("small", null, codexState.connected ? t("settings.openaiOAuthConnected") : t("settings.openaiOAuthNotConnected")),
          ),
        ),
      ),
      className: "is-primary" + (primaryMenuOpen ? " is-menu-open" : ""),
      children: [
        primarySource === "custom" && models[0] && ModelCard([
        ...modelCredentialFields(models[0], function (field, value) { updateModel(models[0].id, field, value); }, t),
        React.createElement("div", { className: "wb-model-meta" },
          React.createElement("div", null, React.createElement("small", null, t("settings.descriptionLabel")), React.createElement("input", { className: "wb-input mono small", value: models[0].desc, onChange: function (e) { updateModel(models[0].id, "desc", e.target.value); }, placeholder: t("settings.placeholderDesc") })),
          React.createElement("div", null, React.createElement("small", null, t("settings.contextLabel")), React.createElement("input", { className: "wb-input mono small", value: models[0].ctx, onChange: function (e) { updateModel(models[0].id, "ctx", e.target.value); }, placeholder: t("settings.placeholderCtx") })),
          React.createElement("div", null, React.createElement("small", null, t("settings.priceLabel")), React.createElement("input", { className: "wb-input mono small", value: models[0].price, onChange: function (e) { updateModel(models[0].id, "price", e.target.value); }, placeholder: models[0].priceHint || t("settings.placeholderPrice") })),
        ),
        ]),
        primarySource === "codex" && React.createElement("div", { className: "wb-codex-auth" },
          React.createElement("div", { className: "wb-codex-auth-main" },
            React.createElement("div", { className: "wb-codex-auth-copy" },
              React.createElement("strong", null, codexState.connected
                ? String(codexState.account && (codexState.account.email || codexState.account.planType || codexState.account.plan_type) || "OpenAI")
                : t("settings.openaiOAuthTitle")),
              React.createElement("span", null, codexState.connected ? t("settings.openaiOAuthConnectedHint") : t("settings.openaiOAuthHint")),
            ),
            !codexState.connected && !codexCliRequired && React.createElement("button", {
              className: "wb-btn primary", disabled: !!codexBusy || codexState.available === false, onClick: startCodexLogin,
            }, codexBusy === "login" ? t("settings.openaiOAuthWaiting") : t("settings.openaiOAuthLogin")),
            !codexState.connected && codexCliRequired && React.createElement("div", { className: "wb-codex-cli-required" },
              React.createElement("span", { className: "wb-codex-cli-hint" }, t("settings.codexCliRequiredHint")),
              React.createElement("div", { className: "wb-codex-cli-download" },
                codexCliDownloading
                  ? React.createElement("span", { className: "wb-codex-cli-progress" }, t("settings.codexCliDownloading") + (codexCliPercent ? " " + codexCliPercent + "%" : ""))
                  : React.createElement("button", {
                    className: "wb-btn primary",
                    onClick: function () { downloadCodexCli(!!(codexState.cli && codexState.cli.broken)); },
                  }, codexState.cli && codexState.cli.broken ? t("settings.codexCliRedownload") : t("settings.codexCliDownload")),
              ),
            ),
            codexState.connected && React.createElement("button", { className: "wb-btn muted", disabled: !!codexBusy, onClick: logoutCodex }, t("settings.openaiOAuthLogout")),
          ),
          codexState.connected && React.createElement("div", { className: "wb-codex-model-picker" },
            React.createElement("label", null,
              React.createElement("small", null, t("settings.openaiOAuthModel")),
              React.createElement("select", {
                className: "wb-select mono", value: codexModel,
                onChange: function (e) {
                  var value = e.target.value;
                  var selected = (codexState.models || []).find(function (item) { return codexModelId(item) === value; });
                  var effort = String(selected && (selected.defaultReasoningEffort || selected.default_reasoning_effort) || "");
                  setCodexModel(value);
                  setCodexEffort(effort);
                  setCodexPrimaryCandidate(value, effort);
                },
              }, codexModelOptions.map(function (item) {
                var id = codexModelId(item);
                return React.createElement("option", { key: id, value: id }, item.displayName || item.display_name || id);
              })),
            ),
            React.createElement("label", null,
              React.createElement("small", null, t("settings.reasoningEffort")),
              React.createElement("select", {
                className: "wb-select", value: codexEffort,
                onChange: function (e) {
                  var value = e.target.value;
                  setCodexEffort(value);
                  setCodexPrimaryCandidate(codexModel, value);
                },
              }, codexEffortOptions.map(function (effort) {
                return React.createElement("option", { key: effort, value: effort }, t("settings.reasoningEffortValue." + effort));
              })),
            ),
          ),
          codexNotice && React.createElement("p", { className: "wb-hint" }, codexNotice),
        ),
      ],
    }),

    // Fallback candidates
    ModelSettingsSection({
      title: t("settings.fallbackCandidates"),
      status: fallbackCount
        ? t("settings.modelStatusCount", { count: fallbackCount })
        : t("settings.modelStatusNone"),
      collapsible: true,
      children: [
      models.slice(1).map(function (m) {
        return ModelCard([
          React.createElement("div", { className: "wb-model-actions" },
            React.createElement("div", { className: "wb-sort-group" },
              React.createElement("button", { className: "wb-sort-btn", title: t("common.moveUp"), onClick: function () { moveModel(m.id, -1); } },
                React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                  React.createElement("polyline", { points: "18 15 12 9 6 15" })
                )
              ),
              React.createElement("button", { className: "wb-sort-btn", title: t("common.moveDown"), onClick: function () { moveModel(m.id, 1); } },
                React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                  React.createElement("polyline", { points: "6 9 12 15 18 9" })
                )
              ),
            ),
            React.createElement("button", { className: "wb-delete-btn", title: t("common.remove"), onClick: function () { deleteModel(m.id); } },
              React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                React.createElement("line", { x1: "18", y1: "6", x2: "6", y2: "18" }),
                React.createElement("line", { x1: "6", y1: "6", x2: "18", y2: "18" })
              )
            ),
          ),
          ...modelCredentialFields(m, function (field, value) { updateModel(m.id, field, value); }, t),
        ], m.id);
      }),
      !fallbackCount && React.createElement("p", { className: "wb-model-empty" }, t("settings.modelFallbackEmpty")),
      modelDraftField(draftModel, setDraftModel, addModel, t),
      ],
    }),

    ModelSettingsSection({
      title: t("settings.secondaryModelSlot"),
      status: secondaryConfigured ? t("settings.modelStatusConfigured") : t("settings.modelStatusNotConfigured"),
      description: t("settings.secondaryModelHint"),
      anchorId: "setting-secondary-model",
      collapsible: true,
      children: [
      secondaryModel && ModelCard([
        ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: secondaryModel.model, onChange: function (e) { updateSecondary("model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
        ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: secondaryModel.api_key, onChange: function (e) { updateSecondary("api_key", e.target.value); }, placeholder: "sk-..." })),
        ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: secondaryModel.base_url, onChange: function (e) { updateSecondary("base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
        React.createElement("div", { className: "wb-model-meta" },
          React.createElement("div", null, React.createElement("small", null, t("settings.secondaryModelCtxLimit")), React.createElement("input", { className: "wb-input mono small", type: "number", min: "0", value: secondaryModel.ctx_limit, onChange: function (e) { updateSecondary("ctx_limit", e.target.value); }, placeholder: "0" })),
          React.createElement("div", null, React.createElement("small", null, t("settings.secondaryModelConcurrency")), React.createElement("input", { className: "wb-input mono small", type: "number", min: "0", value: secondaryModel.max_concurrency, onChange: function (e) { updateSecondary("max_concurrency", e.target.value); }, placeholder: "0" })),
        ),
      ]),
      ],
    }),

    // Vision model
    ModelSettingsSection({
      title: t("settings.visionModelSlot"),
      status: visionConfigured
        ? t("settings.modelStatusConfiguredWithCount", { count: visionFallbackCount })
        : t("settings.modelStatusNotConfigured"),
      anchorId: "setting-vision-model",
      collapsible: true,
      children: [
      visionModels[0] && ModelCard([
        ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: visionModels[0].model, onChange: function (e) { updateVisionModel(visionModels[0].id, "model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
        ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: visionModels[0].api_key, onChange: function (e) { updateVisionModel(visionModels[0].id, "api_key", e.target.value); }, placeholder: "sk-..." })),
        ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: visionModels[0].base_url, onChange: function (e) { updateVisionModel(visionModels[0].id, "base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
      ]),
      visionModels.slice(1).map(function (m) {
        return ModelCard([
          React.createElement("div", { className: "wb-model-actions" },
            React.createElement("div", { className: "wb-sort-group" },
              React.createElement("button", { className: "wb-sort-btn", title: t("common.moveUp"), onClick: function () { moveVisionModel(m.id, -1); } },
                React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                  React.createElement("polyline", { points: "18 15 12 9 6 15" })
                )
              ),
              React.createElement("button", { className: "wb-sort-btn", title: t("common.moveDown"), onClick: function () { moveVisionModel(m.id, 1); } },
                React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                  React.createElement("polyline", { points: "6 9 12 15 18 9" })
                )
              ),
            ),
            React.createElement("button", { className: "wb-delete-btn", title: t("common.remove"), onClick: function () { deleteVisionModel(m.id); } },
              React.createElement("svg", { width: "12", height: "12", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2.5", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
                React.createElement("line", { x1: "18", y1: "6", x2: "6", y2: "18" }),
                React.createElement("line", { x1: "6", y1: "6", x2: "18", y2: "18" })
              )
            ),
          ),
          ModelField(t("settings.modelIdentifierLabel"), React.createElement("input", { className: "wb-input mono", value: m.model, onChange: function (e) { updateVisionModel(m.id, "model", e.target.value); }, placeholder: t("settings.placeholderModelIdentifier") })),
          ModelField(t("settings.apiKey"), React.createElement("input", { className: "wb-input mono", type: "password", value: m.api_key, onChange: function (e) { updateVisionModel(m.id, "api_key", e.target.value); }, placeholder: "sk-..." })),
          ModelField(t("settings.baseUrlLabel"), React.createElement("input", { className: "wb-input mono", value: m.base_url, onChange: function (e) { updateVisionModel(m.id, "base_url", e.target.value); }, placeholder: DEFAULT_MODEL_BASE_URL })),
        ], m.id);
      }),
      modelDraftField(draftVision, setDraftVision, addVisionModel, t),
      ],
    }),

    React.createElement(EmbeddingSettingsSection, {
      t: t, settings: embeddingSettings, setSettings: setEmbeddingSettings,
      apiKey: embeddingApiKey, setApiKey: setEmbeddingApiKey,
      status: embeddingStatus, setStatus: setEmbeddingStatus,
      busy: embeddingBusy, setBusy: setEmbeddingBusy,
      anchorId: "setting-embedding",
    }),
    ModelSettingsSection({
      title: t("settings.localModels"),
      description: t("settings.localModelsHint"),
      className: "is-local-models",
      children: localModels.map(function (item) {
        var percent = downloadPercent(item);
        var cv2RuntimeMissing = item.id === "pp-ocrv6-medium" && cv2Runtime && !cv2Runtime.installed;
        var cv2RuntimePercent = downloadPercent(cv2Runtime);
        var kind = item.kind || "model";
        var localCopy = {
          "qwen3-embedding-0.6b": ["settings.localEmbeddingTitle", "settings.localQwenName", "settings.localQwenHint"],
          "pp-ocrv6-medium": ["settings.localOcrTitle", "settings.localOcrName", "settings.localOcrHint"],
          "fireredasr2-aed-int8": ["settings.localAsrTitle", "settings.localFireRedName", "settings.localFireRedHint"],
          "kokoro-zh-en": ["settings.localTtsTitle", "settings.localKokoroName", "settings.localKokoroHint"],
          "zipvoice-zh-en": ["settings.localTtsTitle", "settings.localZipVoiceName", "settings.localZipVoiceHint"],
        }[item.id];
        var displayTitle = localCopy ? t(localCopy[0]) : item.kind;
        var displayName = localCopy ? t(localCopy[1]) : item.name;
        var displayDescription = localCopy ? t(localCopy[2]) : item.description;
        var runtime = String(item.runtime || "onnx").toLowerCase();
        var runtimeLabel = runtime === "onnx-cpu" ? "CPU" : runtime.toUpperCase();
        var runtimeClass = runtime.indexOf("cuda") >= 0 || runtime.indexOf("directml") >= 0 || runtime.indexOf("qnn") >= 0
          ? " is-cuda"
          : runtime.indexOf("mlx") >= 0 ? " is-mlx" : " is-onnx";
        var hasError = !item.ready && !!item.error;
        var statusText = hasError
          ? t("settings.localModelError")
          : item.ready
            ? t("settings.localModelActive", { runtime: runtimeLabel })
            : item.downloading
              ? t("settings.localModelDownloading", { percent: percent })
              : t("settings.localModelOptional");
        return React.createElement("article", { className: "wb-model-card wb-local-model" + (item.ready ? " is-ready" : " is-optional"), key: item.id },
          React.createElement("span", { className: "wb-local-model-icon is-" + kind }, LocalModelIcon(kind)),
          React.createElement("div", { className: "wb-local-model-copy" },
            React.createElement("span", { className: "wb-local-model-heading" },
              React.createElement("strong", null, displayTitle),
              React.createElement("span", { className: "wb-local-model-name" }, displayName),
            ),
            React.createElement("small", null, displayDescription),
            item.downloading && React.createElement("div", { className: "wb-local-model-progress" },
              React.createElement("progress", { max: "100", value: percent, "aria-label": t("settings.localModelDownloading", { percent: percent }) }),
              React.createElement("span", null, percent + "%"),
            ),
            cv2RuntimeMissing && React.createElement("small", {
              className: "wb-local-model-runtime" + (cv2Runtime.error ? " wb-local-model-error" : ""),
            },
              cv2Runtime.downloading
                ? t("settings.ocrRuntimeDownloading", { percent: cv2RuntimePercent })
                : cv2Runtime.error
                  ? t("settings.ocrRuntimeFailed") + ": " + cv2Runtime.error
                  : t("settings.ocrRuntimeBundled")),
            hasError && React.createElement("small", { className: "wb-local-model-error" }, localizeLocalModelError(item.error, t)),
          ),
          React.createElement("div", { className: "wb-local-model-actions" },
            React.createElement("span", { className: "wb-model-status" + (hasError ? " is-error" : item.ready ? " wb-runtime-badge" + runtimeClass : ""), role: "status" },
              React.createElement("span", { className: "wb-local-model-status-dot", "aria-hidden": "true" }),
              statusText,
            ),
            React.createElement("button", {
              type: "button",
              className: "wb-btn compact " + (item.ready ? "danger" : "tonal"),
              disabled: !!localBusy || item.downloading,
              "aria-label": (item.ready ? t("settings.delete") : hasError ? t("settings.retry") : t("settings.download")) + " " + displayName,
              onClick: function () { manageLocalModel(item.id, item.ready ? "delete" : "download"); },
            }, item.ready ? t("settings.delete") : hasError ? t("settings.retry") : t("settings.download")),
          ),
        );
      }).concat(corpusEmbedding && corpusEmbedding.mismatch ? [
        React.createElement("div", { className: "wb-integration-status error", key: "mismatch" },
          React.createElement("span", null, t("settings.embeddingMismatch", { count: corpusEmbedding.pending_vectors || 0 })),
          React.createElement("button", { className: "wb-btn", disabled: corpusEmbedding.reembed && corpusEmbedding.reembed.running, onClick: reembedKnowledge },
            corpusEmbedding.reembed && corpusEmbedding.reembed.running ? t("settings.reembedding") : t("settings.reembed")),
        ),
      ] : []),
    }),
    React.createElement("div", { className: "wb-save-actions" },
      modelsSaved && React.createElement("span", {
        className: "wb-hint saved" + (modelsSaving ? " is-saving" : ""),
        role: "status",
        "aria-live": "polite",
      },
        modelsSaving && React.createElement("span", { className: "wb-spinner", "aria-hidden": "true" }),
        React.createElement("span", null, modelsSaved),
      ),
      React.createElement("button", { className: "wb-btn primary", onClick: saveAllModels, disabled: modelsSaving || !!embeddingBusy }, t("settings.saveApply")),
    ),
  );
}

function modelDraftField(draft, setDraft, onAdd, t) {
  return React.createElement("div", { className: "wb-model-draft" },
    React.createElement("label", null,
      React.createElement("small", null, t("settings.modelIdentifierLabel")),
      React.createElement("input", { className: "wb-input mono", value: draft.model, onChange: function (e) { setDraft({ ...draft, model: e.target.value, name: e.target.value }); }, placeholder: t("settings.placeholderModelIdentifier") }),
    ),
    React.createElement("label", null,
      React.createElement("small", null, t("settings.apiKey")),
      React.createElement("input", { className: "wb-input mono", type: "password", value: draft.api_key, onChange: function (e) { setDraft({ ...draft, api_key: e.target.value }); }, placeholder: "sk-..." }),
    ),
    React.createElement("label", null,
      React.createElement("small", null, t("settings.baseUrlLabel")),
      React.createElement("input", { className: "wb-input mono", value: draft.base_url, onChange: function (e) { setDraft({ ...draft, base_url: e.target.value }); }, placeholder: DEFAULT_MODEL_BASE_URL }),
    ),
    React.createElement("button", { className: "wb-btn", onClick: onAdd }, t("settings.add")),
  );
}

function ModelSettingsSection(options) {
  var body = React.createElement("div", { className: "wb-model-section-body" }, ...(options.children || []));
  var headerContent = [
    React.createElement("div", { className: "wb-model-section-title", key: "title" },
      React.createElement("b", null, options.title),
      options.description && React.createElement("small", null, options.description),
    ),
    options.status && React.createElement("span", { className: "wb-model-status", key: "status" }, options.status),
    options.headerAction && React.createElement("div", { className: "wb-model-header-action", key: "action" }, options.headerAction),
  ];
  var sectionProps = {
    className: "wb-model-section" + (options.className ? " " + options.className : ""),
  };
  if (options.anchorId) sectionProps.id = options.anchorId;
  if (options.collapsible) {
    return React.createElement("details", sectionProps,
      React.createElement("summary", { className: "wb-model-section-head" }, ...headerContent),
      body,
    );
  }
  return React.createElement("section", sectionProps,
    React.createElement("div", { className: "wb-model-section-head" }, ...headerContent),
    body,
  );
}

export { EmbeddingSettingsSection, ModelsPanel };
