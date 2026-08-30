import { workbenchServices } from "../../shared/runtime/services.jsx"
import { normalizePermissionMode as normalizePermissionModeBehavior } from "./behavior.mjs"
import { wbcT } from "./core.jsx"
import { WBC_ICONS } from "./icons.jsx"

var WBC_SIDE_TAB_ICONS = {
  overview: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h11A2.5 2.5 0 0 1 20 7.5v10a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5Z"/><path d="M8 5V3.8M16 5V3.8M8 10.5h8M12 8.5v4"/></svg>,
  goal: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><path d="m14.5 9.5 5-5M16 4.5h3.5V8"/><circle cx="12" cy="12" r=".8" fill="currentColor" stroke="none"/></svg>,
  plan: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5" y="4.5" width="14" height="16" rx="2.5"/><path d="M9 4.5V3h6v1.5M8.5 10.5l1.4 1.4 2.6-2.8M14.5 11h2M8.5 16h8"/></svg>,
  subagents: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 9.5A4.5 4.5 0 0 1 11.5 5H14a4 4 0 0 1 4 4v.5a4.5 4.5 0 0 1 2 3.7v2.3a3.5 3.5 0 0 1-3.5 3.5h-9A3.5 3.5 0 0 1 4 15.5v-2.3a4.5 4.5 0 0 1 3-4.2Z"/><path d="M9 13h.01M15 13h.01M9.5 16h5M12 5V2.8M10.5 2.8h3"/></svg>,
  context: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="4" width="6" height="6" rx="2"/><rect x="14" y="4" width="6" height="6" rx="2"/><rect x="4" y="14" width="6" height="6" rx="2"/><rect x="14" y="14" width="6" height="6" rx="2"/><path d="M10 7h4M7 10v4M17 10v4M10 17h4"/></svg>,
  files: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 4.5h10A2.5 2.5 0 0 1 19.5 7v11A2.5 2.5 0 0 1 17 20.5H7A2.5 2.5 0 0 1 4.5 18V7A2.5 2.5 0 0 1 7 4.5Z"/><path d="M9 4.5V3h6v1.5M8 10h8M8 14h5M8 17h7"/></svg>,
  artifacts: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 8.5h16v9A2.5 2.5 0 0 1 17.5 20h-11A2.5 2.5 0 0 1 4 17.5Z"/><path d="M3.5 5.5h17v3h-17zM9 12h6M12 8.5V12"/></svg>,
  changes: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="7" cy="5" r="2"/><circle cx="7" cy="19" r="2"/><circle cx="17" cy="8" r="2"/><path d="M7 7v10M9 17c5 0 8-2.5 8-7M14.5 8H10"/></svg>,
  branches: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M8 17h4a6 6 0 0 0 6-6V10"/></svg>,
  viewer: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="m6 16 3.5-3.5 2.7 2.7 2.3-2.3L18 16M8 9h.01"/><path d="M3.5 8.5h17"/></svg>,
  map: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m4 6.5 5-2.2 6 2.2 5-2.2v13.2l-5 2.2-6-2.2-5 2.2Z"/><path d="M9 4.3v13.2M15 6.5v13.2"/><circle cx="12" cy="11" r="1.5"/></svg>,
  browser: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4.5" width="18" height="15" rx="3"/><path d="M3 8.5h18M7 6.5h.01M10 6.5h.01"/><circle cx="12" cy="14" r="3.3"/><path d="M8.7 14h6.6M12 10.7c1.1 1.2 1.1 5.4 0 6.6M12 10.7c-1.1 1.2-1.1 5.4 0 6.6"/></svg>,
  terminal: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4.5" width="18" height="15" rx="3"/><path d="m7.5 9 3 3-3 3M13 15h3.5"/></svg>,
  "side-agents": <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 4.5h10A2.5 2.5 0 0 1 19.5 7v10A2.5 2.5 0 0 1 17 19.5h-4L9 22v-2.5H7A2.5 2.5 0 0 1 4.5 17V7A2.5 2.5 0 0 1 7 4.5Z"/><path d="M9 9h6M9 13h4"/></svg>,
};

// Slash commands + permission modes (mirrors the legacy agent capabilities;
// defined locally so this page stays independent from workbench.jsx).
var WBC_COMMANDS = [
  { id: "quick-answer", labelKey: "workbenchChat.command.quick-answer.label", descKey: "workbenchChat.command.quick-answer.desc" },
  { id: "deep-research", labelKey: "workbenchChat.command.deep-research.label", descKey: "workbenchChat.command.deep-research.desc" },
  { id: "deep-reflect", labelKey: "workbenchChat.command.deep-reflect.label", descKey: "workbenchChat.command.deep-reflect.desc" },
  { id: "help-me-decide", labelKey: "workbenchChat.command.help-me-decide.label", descKey: "workbenchChat.command.help-me-decide.desc" },
  { id: "learning-plan", labelKey: "workbenchChat.command.learning-plan.label", descKey: "workbenchChat.command.learning-plan.desc" },
  { id: "daily-review", labelKey: "workbenchChat.command.daily-review.label", descKey: "workbenchChat.command.daily-review.desc" },
  { id: "deep-compare", labelKey: "workbenchChat.command.deep-compare.label", descKey: "workbenchChat.command.deep-compare.desc" },
  { id: "terminal", labelKey: "workbenchChat.command.terminal.label", descKey: "workbenchChat.command.terminal.desc" },
];

var WBC_COMMAND_ICONS = {
  "quick-answer": WBC_ICONS.bolt,
  "deep-research": WBC_ICONS.search,
  "deep-reflect": WBC_ICONS.spark,
  "help-me-decide": WBC_ICONS.checklist,
  "learning-plan": WBC_ICONS.file,
  "daily-review": WBC_ICONS.checklist,
  "deep-compare": WBC_ICONS.fork,
  terminal: WBC_ICONS.terminal,
};

var WBC_MODES = [
  { id: "default", labelKey: "workbenchChat.mode.default.label", descKey: "workbenchChat.mode.default.desc" },
  { id: "auto", labelKey: "workbenchChat.mode.auto.label", descKey: "workbenchChat.mode.auto.desc" },
  { id: "plan", labelKey: "workbenchChat.mode.plan.label", descKey: "workbenchChat.mode.plan.desc" },
  { id: "full_access", labelKey: "workbenchChat.mode.full_access.label", descKey: "workbenchChat.mode.full_access.desc" },
];
var WBC_REASONING_EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"];

function wbcIsDeepSeekModel(model) {
  return String(model && (model.model || model.name || model.id) || "")
    .trim().toLowerCase().indexOf("deepseek") >= 0;
}

function wbcSupportedReasoningEfforts(model) {
  var raw = model && (
    model.supportedReasoningEfforts
    || model.supported_reasoning_efforts
  );
  var efforts = (Array.isArray(raw) ? raw : []).map(function (option) {
    return String(
      option && (option.reasoningEffort || option.reasoning_effort)
      || option
      || ""
    ).trim().toLowerCase();
  }).filter(function (effort) {
    return WBC_REASONING_EFFORT_ORDER.indexOf(effort) >= 0;
  });
  if (!efforts.length && wbcIsDeepSeekModel(model)) efforts = ["high", "max"];
  return Array.from(new Set(efforts)).sort(function (a, b) {
    return WBC_REASONING_EFFORT_ORDER.indexOf(a) - WBC_REASONING_EFFORT_ORDER.indexOf(b);
  });
}

function wbcReasoningEffortForModel(model, preferred) {
  var effort = String(
    preferred
    || model && (model.reasoning_effort || model.defaultReasoningEffort || model.default_reasoning_effort)
    || ""
  ).trim().toLowerCase();
  if (wbcIsDeepSeekModel(model)) {
    if (["low", "medium", "high"].indexOf(effort) >= 0) effort = "high";
    else if (["xhigh", "max"].indexOf(effort) >= 0) effort = "max";
    else effort = "high";
  }
  var supported = wbcSupportedReasoningEfforts(model);
  if (supported.length && supported.indexOf(effort) < 0) {
    effort = String(
      model && (model.defaultReasoningEffort || model.default_reasoning_effort)
      || supported[0]
      || ""
    ).trim().toLowerCase();
  }
  return effort;
}

function wbcFriendlyModelName(model, fallback) {
  var configuredName = String(model && model.name || "").trim();
  var modelId = String(model && model.model || fallback || "").trim();
  if (configuredName && configuredName !== modelId) return configuredName;
  if (!modelId) return configuredName;
  var words = modelId.replace(/^gpt-/i, "").split(/[-_]+/).filter(Boolean);
  return words.map(function (word) {
    if (/^\d/.test(word)) return word.toUpperCase();
    if (word.toLowerCase() === "deepseek") return "DeepSeek";
    return word.charAt(0).toUpperCase() + word.slice(1);
  }).join(" ");
}

function wbcLocalizedModelDescription(model) {
  var description = String(model && (model.desc || model.description) || "").trim();
  if (!description) return "";
  var modelName = wbcFriendlyModelName(model, model && (model.model || model.value || model.id));
  var providerName = String(modelName || "").split(/\s+/)[0];
  if (providerName && description.toLowerCase() === (providerName + " default").toLowerCase()) {
    return wbcT("workbenchChat.modelProviderDefault", "{provider} default", { provider: providerName });
  }
  return description;
}

function wbcNormalizePermissionMode(value, fallback) {
  return normalizePermissionModeBehavior(
    value,
    fallback,
    WBC_MODES.map(function (item) { return item.id; })
  );
}

function wbcModeMeta(id) {
  var meta = WBC_MODES[1];
  for (var i = 0; i < WBC_MODES.length; i++) if (WBC_MODES[i].id === id) meta = WBC_MODES[i];
  return {
    id: meta.id,
    label: wbcT(meta.labelKey, meta.id),
    desc: wbcT(meta.descKey, ""),
  };
}

// ---- external Agent identity, capability and binding helpers ----------------
// The built-in Agent installation id mirrors the backend Agent Runtime
// (cyrene/agents/builtin.py). The composer treats it as the default.
var WBC_BUILTIN_AGENT_INSTALLATION = "agent_cyrene_builtin";
var WBC_BUILTIN_AGENT_ID = "cyrene";
var WBC_OPEN_AGENT_DETAIL_EVENT = "cyrene:open-agent-detail";

function wbcIsBuiltinAgent(agent) {
  agent = agent || {};
  return String(agent.installationId || "") === WBC_BUILTIN_AGENT_INSTALLATION
    || String(agent.agentId || "") === WBC_BUILTIN_AGENT_ID
    || !!agent.builtin;
}

// A capability snapshot exists when the chat was created with (or later
// received) an Agent binding and a probed/declared capabilities object.
// Legacy chats without one keep their historical full-surface behavior.
function wbcHasAgentCapabilitySnapshot(chat) {
  return !!(chat && chat.capabilities && typeof chat.capabilities === "object");
}

function wbcCapabilityStatus(chat, group, key) {
  var caps = chat && chat.capabilities;
  if (!caps || typeof caps !== "object") return "unknown";
  var section = caps[group];
  if (!section || typeof section !== "object") return "unknown";
  var value = section[key];
  if (value === true || value === "supported") return "supported";
  if (value === false || value === "unsupported") return "unsupported";
  if (value === "degraded") return "degraded";
  if (value === "agent_defined") return "agent_defined";
  return "unknown";
}

// Capability-driven composer gating. Legacy chats (no snapshot) always allow
// the current behavior. For Agent chats, unknown input/side-effect capabilities
// are treated as unsupported (handoff §13) via opts.strictUnknown.
function wbcCapabilityEnabled(chat, group, key, opts) {
  if (!wbcHasAgentCapabilitySnapshot(chat)) return true;
  var status = wbcCapabilityStatus(chat, group, key);
  if (status === "unsupported") return false;
  if (status === "unknown") return !(opts && opts.strictUnknown);
  return true;
}

function wbcChatAgent(chat) {
  return (chat && chat.agent && typeof chat.agent === "object") ? chat.agent : null;
}

function wbcAgentDisplayName(agent) {
  agent = agent || {};
  var name = String(agent.displayName || agent.name || "").trim();
  if (wbcIsBuiltinAgent(agent)) return name || "Cyrene";
  return name || String(agent.agentId || agent.installationId || "Agent");
}

// Availability states shown in the Composer Agent submenu. The backend's
// agent_card supplies installState / enabled / authState / runtimeState;
// conservative phase-1 defaults keep an unprobed Agent unselectable until its
// detail page has configured login and runtime.
function wbcAgentAvailability(agent) {
  agent = agent || {};
  if (wbcIsBuiltinAgent(agent)) return { state: "available", reasonKey: "" };
  if (agent.enabled === false) return { state: "disabled", reasonKey: "workbenchChat.agentState.disabled" };
  var installState = String(agent.installState || "");
  if (installState && installState !== "installed" && installState !== "upgrade_available") {
    return { state: "not_installed", reasonKey: "workbenchChat.agentState.notInstalled" };
  }
  var auth = String(agent.authState || "").toLowerCase();
  if (auth === "expired") return { state: "auth_required", reasonKey: "workbenchChat.agentState.authExpired" };
  if (auth === "failed") return { state: "auth_required", reasonKey: "workbenchChat.agentState.needsLogin" };
  var runtime = String(agent.runtimeState || "").toLowerCase();
  if (["error", "crashed", "failed"].indexOf(runtime) >= 0) {
    return { state: "not_started", reasonKey: "workbenchChat.agentState.notStarted" };
  }
  return { state: "available", reasonKey: "" };
}

function wbcAgentStateLabel(state) {
  var labels = {
    available: wbcT("workbenchChat.agentState.available", "Available"),
    disabled: wbcT("workbenchChat.agentState.disabled", "Disabled"),
    not_installed: wbcT("workbenchChat.agentState.notInstalled", "Not installed"),
    auth_required: wbcT("workbenchChat.agentState.needsLogin", "Needs login / configuration"),
    not_started: wbcT("workbenchChat.agentState.notStarted", "Not started"),
    incompatible: wbcT("workbenchChat.agentState.incompatible", "Version incompatible"),
  };
  return labels[state] || String(state || "");
}

// One row of the Composer Agent submenu (handoff §8.2). Available Agents pick
// a draft binding; unavailable Agents open their extension detail; locked
// chats (first message already sent) render the row disabled with a lock note
// and can never silently re-bind.
function wbcComposerAgentRow(props) {
  var agent = props.agent || {};
  var availability = props.availability || wbcAgentAvailability(agent);
  var name = wbcAgentDisplayName(agent);
  var meta = [wbcDriverLabel(agent.driver), String(agent.version || "")].filter(Boolean).join(" · ");
  var stateLabel = availability.state === "available" ? "" : wbcAgentStateLabel(availability.state);
  return (
    <button
      key={props.key}
      type="button"
      className={"wbc-agent-menu-row"
        + (props.active ? " active" : "")
        + (props.locked ? " locked" : "")
        + (props.canPick ? "" : " unavailable state-" + String(availability.state || "unknown"))}
      disabled={!!props.locked}
      aria-disabled={props.canPick ? undefined : "true"}
      aria-label={[name, stateLabel, props.active ? wbcT("workbenchChat.agentCurrent", "Current Agent") : ""].filter(Boolean).join(" · ")}
      title={stateLabel || meta || undefined}
      onClick={function () {
        if (props.locked) return;
        if (props.canPick) { if (props.onPick) props.onPick(); }
        else if (props.onOpen) props.onOpen(agent);
      }}
    >
      <span className="wbc-agent-menu-dot" aria-hidden="true" />
      <span className="wbc-agent-menu-name">{name}</span>
      {meta ? <span className="wbc-agent-menu-meta">{meta}</span> : null}
      {stateLabel ? <span className="wbc-agent-menu-state">{stateLabel}</span> : null}
      {props.active ? <span className="wbc-popmenu-check">{WBC_ICONS.check}</span> : null}
    </button>
  );
}

function wbcDriverLabel(driver) {
  driver = String(driver || "").trim();
  if (!driver || driver === "cyrene_builtin") return "";
  if (driver === "acp_stdio") return "ACP · stdio";
  return wbcT("workbenchChat.agentDriver.unknown", "Other driver · {driver}", { driver: driver });
}

function wbcAgentConnectionLabel(chat) {
  var agent = wbcChatAgent(chat);
  if (!agent) return "";
  if (wbcIsBuiltinAgent(agent)) return wbcT("workbenchChat.connection.builtin", "Built-in · ready");
  var driver = wbcDriverLabel(agent.driver);
  var runtime = String(agent.runtimeState || agent.connectionState || "").toLowerCase();
  var stateLabel = "";
  if (runtime === "ready" || runtime === "connected" || runtime === "running") {
    stateLabel = wbcT("workbenchChat.connection.connected", "Connected");
  } else if (runtime === "error" || runtime === "crashed") {
    stateLabel = wbcT("workbenchChat.connection.failed", "Error");
  } else if (runtime) {
    stateLabel = wbcT("workbenchChat.agentState.unknownValue", "Unknown · {value}", { value: runtime });
  }
  return [driver, stateLabel].filter(Boolean).join(" · ") || wbcT("workbenchChat.connection.unknown", "Unknown");
}

function wbcModelAccessLabel(chat) {
  var access = chat && chat.modelAccess && typeof chat.modelAccess === "object" ? chat.modelAccess : null;
  if (!access) return "";
  if (String(access.mode || "") === "agent_managed") {
    return wbcT("workbenchChat.modelSource.agentManaged", "Agent-owned configuration");
  }
  return wbcT("workbenchChat.modelSource.cyrene", "Cyrene");
}

// Hide usage statistics for Agents that do not report token usage instead of
// painting fake zeros (handoff §9).
function wbcUsageReported(usage) {
  usage = usage || {};
  return !!(
    Number(usage.prompt_tokens || 0)
    || Number(usage.completion_tokens || 0)
    || Number(usage.total_tokens || 0)
    || Number(usage.prompt_cache_hit_tokens || 0)
    || Number(usage.prompt_cache_miss_tokens || 0)
  );
}

// Slash commands are capability/command driven (handoff §13): when an Agent
// chat snapshot exists, only commands declared by the Agent are offered.
// The built-in Cyrene Agent keeps its native command list; external Agents
// never inherit Cyrene-only commands.
function wbcComposerSlashCommands(chat) {
  if (!wbcHasAgentCapabilitySnapshot(chat)) return null;
  if (wbcIsBuiltinAgent(wbcChatAgent(chat))) return null;
  var raw = chat && (
    (Array.isArray(chat.agentCommands) && chat.agentCommands)
    || (Array.isArray(chat.capabilities.commands) && chat.capabilities.commands)
    || (Array.isArray(chat.capabilities.slash) && chat.capabilities.slash)
  );
  if (!raw) return [];
  return raw.map(function (item) {
    if (typeof item === "string") return { id: item, label: item, description: "", inputHint: "" };
    var id = String(item && (item.id || item.name || item.command) || "");
    return {
      id: id,
      label: String(item && (item.label || item.title || item.name) || id),
      description: String(item && (item.description || item.help) || ""),
      inputHint: String(item && (item.inputHint || item.input_hint) || ""),
    };
  }).filter(function (item) { return !!item.id; });
}

function wbcDraftAgentBindingKey(projectId) {
  return "wbc-draft-agent-binding:" + String(projectId || "default");
}

function wbcSaveDraftAgentBinding(projectId, binding) {
  try {
    if (!binding) localStorage.removeItem(wbcDraftAgentBindingKey(projectId));
    else localStorage.setItem(wbcDraftAgentBindingKey(projectId), JSON.stringify(binding));
  } catch (e) {}
}

function wbcLoadDraftAgentBinding(projectId) {
  try {
    var raw = localStorage.getItem(wbcDraftAgentBindingKey(projectId));
    if (!raw) return null;
    var parsed = JSON.parse(raw);
    return parsed && parsed.agent && parsed.agent.installationId ? parsed : null;
  } catch (e) {
    return null;
  }
}

function wbcDefaultAgentBinding() {
  return {
    agent: { installationId: WBC_BUILTIN_AGENT_INSTALLATION },
    modelAccess: { mode: "cyrene_managed", profileId: "primary" },
  };
}

// Ask the Settings overlay to open this external Agent's installed detail.
// The overlay mounts only inside the Workbench shell, so a
// no-op here simply leaves the composer submenu's disabled row in place.
function wbcOpenAgentDetail(agent) {
  agent = agent || {};
  try {
    window.dispatchEvent(new CustomEvent(WBC_OPEN_AGENT_DETAIL_EVENT, {
      detail: {
        installationId: String(agent.installationId || ""),
        agentId: String(agent.agentId || ""),
        displayName: wbcAgentDisplayName(agent),
      },
    }));
  } catch (e) {}
}

// ---- file classification for the side viewer -------------------------------

var WBC_CODE_EXTS = ["py","js","ts","jsx","tsx","css","scss","json","yaml","yml","toml","xml","sql","sh","bash","rs","go","java","c","cc","cpp","h","hpp","rb","php","swift","kt","txt","csv","ini","cfg","conf","env","log","rst","properties","vue","svelte"];
var WBC_OFFICE_MAX_FILE_BYTES = 100 * 1024 * 1024;
var WBC_OFFICE_MAX_ZIP_ENTRIES = 4000;
var WBC_OFFICE_MAX_ZIP_ENTRY_BYTES = 32 * 1024 * 1024;
var WBC_OFFICE_MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024;
var WBC_OFFICE_RENDERER_LOADS = {};

function wbcOfficeAssetRevisionQuery() {
  try {
    var script = Array.from(document.scripts || []).find(function (item) {
      return String(item && item.src || "").indexOf("/compiled/workbench-chat.js") !== -1;
    });
    return script ? new URL(script.src, window.location.href).search : "";
  } catch (e) {
    return "";
  }
}

function wbcLoadOfficeRenderer(kind) {
  var isDocx = kind === "docx";
  var globalKey = isDocx ? "CyreneOfficeDocx" : "CyreneOfficePptx";
  var fileName = isDocx ? "docx-viewer.js" : "pptx-viewer.js";
  if (window[globalKey]) return Promise.resolve(window[globalKey]);
  if (WBC_OFFICE_RENDERER_LOADS[kind]) return WBC_OFFICE_RENDERER_LOADS[kind];
  WBC_OFFICE_RENDERER_LOADS[kind] = new Promise(function (resolve, reject) {
    var script = document.createElement("script");
    script.async = true;
    script.dataset.cyreneOfficeRenderer = kind;
    script.src = "/static/app/office/" + fileName + wbcOfficeAssetRevisionQuery();
    script.onload = function () {
      if (window[globalKey]) resolve(window[globalKey]);
      else reject(new Error("office_renderer_unavailable"));
    };
    script.onerror = function () { reject(new Error("office_renderer_unavailable")); };
    document.head.appendChild(script);
  }).catch(function (error) {
    delete WBC_OFFICE_RENDERER_LOADS[kind];
    throw error;
  });
  return WBC_OFFICE_RENDERER_LOADS[kind];
}

function wbcValidateOfficeArchive(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 22) throw new Error("office_invalid_archive");
  var view = new DataView(buffer);
  var minimum = Math.max(0, buffer.byteLength - 65557);
  var eocd = -1;
  for (var offset = buffer.byteLength - 22; offset >= minimum; offset -= 1) {
    if (
      view.getUint32(offset, true) === 0x06054b50
      && offset + 22 + view.getUint16(offset + 20, true) === buffer.byteLength
    ) { eocd = offset; break; }
  }
  if (eocd < 0) throw new Error("office_invalid_archive");
  var entryCount = view.getUint16(eocd + 10, true);
  var directorySize = view.getUint32(eocd + 12, true);
  var directoryOffset = view.getUint32(eocd + 16, true);
  if (entryCount === 0xffff || directorySize === 0xffffffff || directoryOffset === 0xffffffff) {
    throw new Error("office_archive_too_large");
  }
  if (entryCount > WBC_OFFICE_MAX_ZIP_ENTRIES || directoryOffset + directorySize > buffer.byteLength) {
    throw new Error("office_archive_too_large");
  }
  var cursor = directoryOffset;
  var totalBytes = 0;
  for (var index = 0; index < entryCount; index += 1) {
    if (cursor + 46 > buffer.byteLength || view.getUint32(cursor, true) !== 0x02014b50) {
      throw new Error("office_invalid_archive");
    }
    var uncompressedBytes = view.getUint32(cursor + 24, true);
    var fileNameLength = view.getUint16(cursor + 28, true);
    var extraLength = view.getUint16(cursor + 30, true);
    var commentLength = view.getUint16(cursor + 32, true);
    if (uncompressedBytes === 0xffffffff || uncompressedBytes > WBC_OFFICE_MAX_ZIP_ENTRY_BYTES) {
      throw new Error("office_archive_too_large");
    }
    totalBytes += uncompressedBytes;
    if (totalBytes > WBC_OFFICE_MAX_ZIP_TOTAL_BYTES) throw new Error("office_archive_too_large");
    cursor += 46 + fileNameLength + extraLength + commentLength;
    if (cursor > directoryOffset + directorySize || cursor > buffer.byteLength) {
      throw new Error("office_invalid_archive");
    }
  }
}

function wbcHardenOfficeLinks(container) {
  if (!container || !container.querySelectorAll) return;
  container.querySelectorAll("a[href]").forEach(function (link) {
    var raw = String(link.getAttribute("href") || "").trim();
    if (!/^(https?:|mailto:)/i.test(raw)) {
      link.removeAttribute("href");
      return;
    }
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
  });
}

function wbcFileViewKind(file) {
  if (!file) return "";
  var ct = String(file.content_type || file.contentType || file.mime_type || file.mimeType || "").split(";", 1)[0].trim().toLowerCase();
  var fileLabel = String(file.name || file.filename || file.path || file.url || "").split(/[?#]/, 1)[0];
  var ext = fileLabel.indexOf(".") >= 0 ? fileLabel.split(".").pop().toLowerCase() : "";
  if (ct.indexOf("image/") === 0 || file.kind === "image" || ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"].indexOf(ext) !== -1) return "image";
  if (ct.indexOf("audio/") === 0 || file.kind === "audio" || ["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"].indexOf(ext) !== -1) return "audio";
  if (ct.indexOf("video/") === 0 || file.kind === "video" || ["mp4", "mov", "webm", "mkv", "avi", "m4v"].indexOf(ext) !== -1) return "video";
  if (ct === "application/pdf" || ext === "pdf" || file.kind === "pdf") return "pdf";
  if (ext === "docx" || ct === "application/vnd.openxmlformats-officedocument.wordprocessingml.document") return "docx";
  if (ext === "pptx" || ct === "application/vnd.openxmlformats-officedocument.presentationml.presentation") return "pptx";
  if (ct === "text/html" || ct === "application/xhtml+xml" || ext === "html" || ext === "htm") return "html";
  if (file.kind === "markdown" || ext === "md" || ext === "mdx" || ext === "markdown") return "markdown";
  if (file.kind === "code" || WBC_CODE_EXTS.indexOf(ext) !== -1 || ct.indexOf("text/") === 0) return "code";
  return "download";
}

function wbcAttachmentVisualKind(file) {
  var viewKind = wbcFileViewKind(file);
  var ext = String(file && (file.name || file.filename) || "").split(".").pop().toLowerCase();
  // The library groups searchable text formats under "document".  That is a
  // useful filter category, but it is too broad for attachment labels: mapping
  // it directly to "doc" makes Markdown and source files look like Word files.
  if (viewKind === "markdown") return "markdown";
  if (viewKind === "code" || viewKind === "html") {
    if (ext === "txt" || ext === "log") return "note";
    // Let the shared classifier keep tabular and office files in their native
    // categories even when an upload reports the generic text/plain MIME type.
    if (!/^(csv|tsv|doc|docx|odt|rtf|xls|xlsm|xlsx|odp|ppt|pptx)$/.test(ext)) return "code";
  }
  var shared = workbenchServices.library().FileVisual;
  if (shared && typeof shared.visualKind === "function") return shared.visualKind(file);
  return viewKind === "image" ? "image" : (viewKind || "file");
}

function wbcAttachmentVisual(file) {
  var shared = workbenchServices.library().FileVisual;
  if (shared && typeof shared.icon === "function") {
    var kind = wbcAttachmentVisualKind(file);
    return {
      kind: kind,
      tone: typeof shared.toneForKind === "function"
        ? shared.toneForKind(kind)
        : (typeof shared.tone === "function" ? shared.tone(file) : "slate"),
      icon: typeof shared.iconForKind === "function" ? shared.iconForKind(kind) : shared.icon(file),
    };
  }
  return { kind: wbcAttachmentVisualKind(file), tone: "slate", icon: WBC_ICONS.file };
}

function wbcAttachmentTypeLabel(file) {
  var kind = wbcAttachmentVisualKind(file);
  var fallbacks = {
    image: "Image",
    audio: "Audio",
    video: "Video",
    pdf: "PDF document",
    doc: "Word document",
    sheet: "Spreadsheet",
    slide: "Presentation",
    markdown: "Markdown",
    link: "Link",
    code: "Code file",
    map: "Map data",
    note: "Text file",
    file: "File",
  };
  return wbcT("workbenchChat.attachmentType." + kind, fallbacks[kind] || fallbacks.file);
}

export { WBC_SIDE_TAB_ICONS, WBC_COMMANDS, WBC_COMMAND_ICONS, WBC_MODES, WBC_REASONING_EFFORT_ORDER, wbcIsDeepSeekModel, wbcSupportedReasoningEfforts, wbcReasoningEffortForModel, wbcFriendlyModelName, wbcLocalizedModelDescription, wbcNormalizePermissionMode, wbcModeMeta, WBC_BUILTIN_AGENT_INSTALLATION, WBC_BUILTIN_AGENT_ID, WBC_OPEN_AGENT_DETAIL_EVENT, wbcIsBuiltinAgent, wbcHasAgentCapabilitySnapshot, wbcCapabilityStatus, wbcCapabilityEnabled, wbcChatAgent, wbcAgentDisplayName, wbcAgentAvailability, wbcAgentStateLabel, wbcComposerAgentRow, wbcDriverLabel, wbcAgentConnectionLabel, wbcModelAccessLabel, wbcUsageReported, wbcComposerSlashCommands, wbcDraftAgentBindingKey, wbcSaveDraftAgentBinding, wbcLoadDraftAgentBinding, wbcDefaultAgentBinding, wbcOpenAgentDetail, WBC_CODE_EXTS, WBC_OFFICE_MAX_FILE_BYTES, WBC_OFFICE_MAX_ZIP_ENTRIES, WBC_OFFICE_MAX_ZIP_ENTRY_BYTES, WBC_OFFICE_MAX_ZIP_TOTAL_BYTES, WBC_OFFICE_RENDERER_LOADS, wbcOfficeAssetRevisionQuery, wbcLoadOfficeRenderer, wbcValidateOfficeArchive, wbcHardenOfficeLinks, wbcFileViewKind, wbcAttachmentVisualKind, wbcAttachmentVisual, wbcAttachmentTypeLabel }
