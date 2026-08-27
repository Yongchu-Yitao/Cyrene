import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WbcVoice, wbcStartVoiceRecorder, wbcTranscribeVoiceBlob } from "../../workbench-chat.jsx"

var {
  useState: useStateSt,
  useEffect: useEffectSt,
  useRef: useRefSt,
  useMemo: useMemoSt,
} = React;

var REPO_URL = "https://github.com/ikerrrrrrrrrrr/Cyrene";
var REPO_ISSUES_URL = REPO_URL + "/issues/new";
var REPO_DOCS_URL = REPO_URL + "/tree/main/docs";
var DEFAULT_MODEL_BASE_URL = "https://api.deepseek.com/v1";

function readTweak(key, fallback) {
  try { var v = localStorage.getItem("cyrene-tweak-" + key); return v !== null ? JSON.parse(v) : fallback; } catch (e) { return fallback; }
}

function readCapability(key, fallback) {
  try {
    var v = localStorage.getItem("cyrene-tweak-cap-" + key);
    return v !== null ? JSON.parse(v) : fallback;
  } catch (e) {
    return fallback;
  }
}

function createEmptyModel() {
  return {
    id: "candidate-" + Date.now() + "-" + Math.random().toString(16).slice(2, 6),
    name: "", model: "", desc: "", ctx: "", price: "", priceHint: "", api_key: "", base_url: DEFAULT_MODEL_BASE_URL, provider: "openai_compatible",
  };
}

function normalizeModel(raw, idx, fbBaseUrl, fbKey) {
  var m = String(raw && (raw.model || raw.name || raw.id) || "").trim();
  return {
    id: String(raw && raw.id || "candidate-" + (idx + 1)).trim() || "candidate-" + (idx + 1),
    name: m, model: m,
    desc: String(raw && raw.desc || "").trim(),
    ctx: String(raw && raw.ctx || "").trim(),
    price: String(raw && raw.price || "").trim(),
    priceHint: String(raw && raw.priceHint || "").trim(),
    provider: String(raw && raw.provider || "openai_compatible").trim(),
    reasoning_effort: String(raw && raw.reasoning_effort || "").trim(),
    api_key: String(raw && raw.api_key || fbKey || "").trim(),
    base_url: String(raw && raw.base_url || (raw && raw.provider === "codex_oauth" ? "codex://oauth" : fbBaseUrl) || DEFAULT_MODEL_BASE_URL).trim() || DEFAULT_MODEL_BASE_URL,
  };
}

function codexModelId(item) {
  return String(item && (item.model || item.id || item.slug) || "").trim();
}

function codexModelSelectOptions(models, selectedModel) {
  var options = [].concat(models || []);
  var selected = String(selectedModel || "").trim();
  if (selected && !options.some(function (item) { return codexModelId(item) === selected; })) {
    // Model discovery can briefly be empty (or stop advertising an older
    // model). Keep the persisted choice visible instead of rendering a blank
    // native select while settings and the OAuth catalog load independently.
    options.unshift({ model: selected, displayName: selected, persisted: true });
  }
  return options;
}

function codexModelReasoningEfforts(model, selectedEffort) {
  var raw = model && (model.supportedReasoningEfforts || model.supported_reasoning_efforts) || [];
  var efforts = raw.map(function (option) {
    return String(typeof option === "string"
      ? option
      : option && (option.reasoningEffort || option.reasoning_effort) || "").trim();
  }).filter(Boolean);
  var selected = String(selectedEffort || "").trim();
  if (selected && efforts.indexOf(selected) < 0) efforts.unshift(selected);
  return Array.from(new Set(efforts));
}

function downloadPercent(state) {
  return state && state.total_bytes
    ? Math.min(100, Math.round(state.downloaded_bytes * 100 / state.total_bytes))
    : 0;
}

// Polls pollFn every intervalMs until it resolves to { done: true } (or
// rejects). onDone receives the outcome; onError receives any error, including
// a timeout error (error.code === "poll_timeout") when timeoutMs is set.
// Returns the interval id so callers can clear it on unmount.
function pollUntil(pollFn, options) {
  var intervalMs = options.intervalMs || 1000;
  var timeoutMs = options.timeoutMs || 0;
  var onDone = options.onDone;
  var onError = options.onError;
  var finished = false;
  var timer = setInterval(function () {
    var result;
    try {
      result = pollFn();
    } catch (error) {
      finish(error);
      return;
    }
    Promise.resolve(result).then(function (outcome) {
      if (outcome && outcome.done) finish(null, outcome);
    }).catch(finish);
  }, intervalMs);
  var timeoutHandle = timeoutMs > 0
    ? setTimeout(function () {
      var timeoutError = new Error("poll timeout");
      timeoutError.code = "poll_timeout";
      finish(timeoutError);
    }, timeoutMs)
    : null;
  function finish(error, outcome) {
    if (finished) return;
    finished = true;
    clearInterval(timer);
    if (timeoutHandle) clearTimeout(timeoutHandle);
    if (error) {
      if (onError) onError(error);
    } else if (outcome && outcome.error) {
      if (onError) onError(outcome.error);
    } else if (onDone) {
      onDone(outcome);
    }
  }
  return timer;
}

async function readSettingsResponse(response) {
  var payload = {};
  try {
    payload = await response.json();
  } catch (e) {
    if (response.ok) throw new Error("Invalid JSON response");
  }
  if (!response.ok) {
    var responseError = new Error(
      String(payload.detail || payload.error || ("HTTP " + response.status))
    );
    responseError.code = String(payload.code || "");
    throw responseError;
  }
  return payload;
}

async function settingsFetch(input, init) {
  var response = await window.fetch(input, init);
  if (response.ok) return response;
  var payload = {};
  try {
    payload = await response.clone().json();
  } catch (e) {}
  var error = new Error(
    String(payload.detail || payload.error || payload.message || ("HTTP " + response.status))
  );
  error.code = String(payload.code || "");
  error.status = response.status;
  throw error;
}

function showSettingsToast(message, type) {
  if (!message) return false;
  try {
    var feedback = workbenchServices.feedback();
    if (feedback && typeof feedback.showToast === "function") {
      feedback.showToast(String(message), type || "info");
      return true;
    }
  } catch (e) {}
  return false;
}

function renderSettingsMarkdown(value) {
  return workbenchServices.markdown().render(value, {
    fallback: "escaped-breaks",
    errorFallback: "escaped-breaks",
    sanitizeOptions: { ADD_ATTR: ["data-line", "data-language"] },
  });
}

function AutomationIcon() {
  return React.createElement("svg", { width: "15", height: "15", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
    React.createElement("path", { d: "M13 2 4.1 12.7a1 1 0 0 0 .8 1.6H11l-1 7.7 8.9-10.7a1 1 0 0 0-.8-1.6H12L13 2Z" })
  );
}

function AboutRelatedIcon(name) {
  if (name === "github") {
    return React.createElement("svg", { width: "22", height: "22", viewBox: "0 0 16 16", fill: "currentColor", "aria-hidden": "true" },
      React.createElement("path", { d: "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z" })
    );
  }
  var path = name === "issue" ? "M12 8v4M12 16h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
    : name === "changelog" ? "M4 19V5M4 19h16M4 19l4-4M8 15V7M8 15h12M12 11V3M12 11h8"
    : name === "website" ? "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM3.6 9h16.8M3.6 15h16.8M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"
    : name === "log" ? "M8 13v-1h8v1M8 17v-1h5M8 21v-1h3M20 5.5A2.5 2.5 0 0 0 17.5 3H6.5A2.5 2.5 0 0 0 4 5.5v15A2.5 2.5 0 0 0 6.5 23h11a2.5 2.5 0 0 0 2.5-2.5V5.5ZM4 9h16"
    : "M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15Z";
  return React.createElement("svg", { width: "23", height: "23", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
    React.createElement("path", { d: path })
  );
}

// ── Shared UI helpers ──

function SectionTitle(title, subtitle) {
  return React.createElement("div", { className: "wb-section-title" },
    React.createElement("h3", null, title),
    subtitle && React.createElement("p", null, subtitle),
  );
}

function SectionBlock(title, extra, ...children) {
  return React.createElement("div", { className: "wb-section-block" },
    React.createElement("div", { className: "wb-section-block-head" },
      React.createElement("b", null, title),
      typeof extra === "string" ? React.createElement("small", null, extra) : (extra || null),
    ),
    ...children,
  );
}

function FieldRow(label, hint, controls, key, anchorId) {
  return React.createElement("div", { className: "wb-field", key: key, id: anchorId || undefined },
    React.createElement("div", { className: "wb-label" },
      label,
      hint && React.createElement("small", null, hint),
    ),
    React.createElement("div", { className: "wb-controls" }, controls),
  );
}

function Toggle(on, onClick, disabled, label, extraProps) {
  return React.createElement("button", Object.assign({
    type: "button",
    className: "wb-toggle" + (on ? " on" : ""),
    role: "switch",
    "aria-checked": on ? "true" : "false",
    "aria-label": label || undefined,
    disabled: !!disabled,
    onClick: disabled ? undefined : onClick,
  }, extraProps || {}));
}

function ModelCard(children, key) {
  return React.createElement("div", { className: "wb-model-card", key: key }, ...children);
}

function ModelField(label, input) {
  return React.createElement("div", { className: "wb-model-line" },
    React.createElement("span", null, label),
    input,
  );
}

export {
  workbenchServices,
  WbcVoice,
  wbcStartVoiceRecorder,
  wbcTranscribeVoiceBlob,
  useStateSt,
  useEffectSt,
  useRefSt,
  useMemoSt,
  REPO_URL,
  REPO_ISSUES_URL,
  REPO_DOCS_URL,
  DEFAULT_MODEL_BASE_URL,
  readTweak,
  readCapability,
  createEmptyModel,
  normalizeModel,
  codexModelId,
  codexModelSelectOptions,
  codexModelReasoningEfforts,
  downloadPercent,
  pollUntil,
  readSettingsResponse,
  settingsFetch,
  showSettingsToast,
  renderSettingsMarkdown,
  AutomationIcon,
  AboutRelatedIcon,
  SectionTitle,
  SectionBlock,
  FieldRow,
  Toggle,
  ModelCard,
  ModelField,
};
