import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  readSettingsResponse,
  settingsFetch,
  showSettingsToast,
  renderSettingsMarkdown,
  ExternalChevron,
  AutomationIcon,
  AboutRelatedIcon,
  SectionTitle,
  Toggle,
} from "./shared.jsx"

// ── Skills Panel ──
function ExtensionGlyph(props) {
  var kind = props.kind || "extension";
  var label = String(props.label || kind || "E").slice(0, 1).toUpperCase();
  if (props.id === "github-cli") return AboutRelatedIcon("github");
  var paths = {
    skill: "M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-4Z",
    mcp: "M8 12h8M12 8v8M5 5h4v4H5zM15 15h4v4h-4z",
    cli: "M4 5h16v14H4zM7 9l3 3-3 3M12 15h5",
    toolchain: "M14.7 6.3a4 4 0 0 0-5 5L3 18l3 3 6.7-6.7a4 4 0 0 0 5-5l-3 3-3-3z",
  };
  if (!paths[kind]) return React.createElement("span", { className: "wb-extension-glyph-text" }, label);
  return React.createElement("svg", { width: "22", height: "22", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "1.9", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
    React.createElement("path", { d: paths[kind] })
  );
}

function extensionDisplayName(item, t) {
  item = item || {};
  return t("settings.extensionCatalog." + item.id + ".name", item.name || item.id || "—");
}

function extensionDisplayDescription(item, t) {
  item = item || {};
  return t("settings.extensionCatalog." + item.id + ".description", item.description || "—");
}

function extensionSourceLabel(item, t) {
  item = item || {};
  var source = item.source;
  var type = "";
  var details = [];
  if (item.ownership === "system") type = "system";
  else if (item.ownership === "builtin") type = "builtin";
  if (!type && typeof source === "string") {
    var value = source.trim();
    var lower = value.toLowerCase();
    if (lower === "manual") type = "manual";
    else if (lower === "github" || lower.indexOf("github:") === 0 || lower.indexOf("github.com") >= 0) type = "github";
    else if (lower === "cyrene-catalog" || lower.indexOf("registry") >= 0) type = "registry";
    else if (/^https?:\/\//.test(lower)) type = "registry";
    if (value && type !== "registry" && lower !== type && lower !== "cyrene-catalog") details.push(value.replace(/^github:/i, ""));
  }
  if (source && typeof source === "object") {
    var rawType = String(source.type || source.kind || "").toLowerCase().replace(/_/g, "-");
    if (!type) {
      if (rawType === "system") type = "system";
      else if (rawType === "bundled" || rawType === "builtin") type = "builtin";
      else if (rawType === "uv") type = "uv";
      else if (rawType === "mise") type = "mise";
      else if (rawType === "github-release") type = "githubRelease";
      else if (rawType === "mcp-registry" || rawType === "mcp-registry-package") type = "mcpRegistry";
      else if (rawType === "github" || String(source.url || "").toLowerCase().indexOf("github.com") >= 0) type = "github";
      else if (["local", "directory", "file", "archive", "upload"].indexOf(rawType) >= 0 || source.path) type = "local";
      else if (rawType === "manual" || source.transport) type = "manual";
      else if (rawType === "registry") type = "registry";
    }
    if (type === "mise" && source.ref) details.push(String(source.ref));
    if (type === "githubRelease") {
      if (source.repo) details.push(String(source.repo));
      if (source.tag) details.push(String(source.tag));
    }
    if (type === "mcpRegistry") {
      if (source.identifier) details.push(String(source.identifier));
      else if (source.id) details.push(String(source.id));
    }
  }
  if (!type) type = "unknown";
  return [t("settings.extensionSource." + type), ...details].filter(Boolean).join(" · ");
}

function extensionHealthLabel(item, t) {
  item = item || {};
  var value = item.kind === "mcp" ? (item.connection_status || item.health) : item.health;
  value = String(value || "unknown").toLowerCase().replace(/-/g, "_");
  var aliases = {
    missing_bundle: "missingBundle",
    error: "unhealthy",
    failed: "unhealthy",
    disabled: "disconnected",
  };
  return t("settings.extensionHealthValue." + (aliases[value] || value), t("settings.extensionHealthValue.unknown"));
}

function extensionTaskStatusLabel(status, t) {
  var value = String(status || "unknown");
  return t("settings.extensionTaskStatus." + value, t("settings.extensionTaskStatus.unknown"));
}

function extensionTaskErrorContent(task, t) {
  var knownReasons = ["dependency_conflict", "mcp_connection_failed", "executable_not_found", "fixed_version_required", "configuration_required", "uv_runtime_missing"];
  var reason = knownReasons.indexOf(task.reason_code) >= 0 ? task.reason_code : "installation_failed";
  return {
    title: t("settings.extensionTaskError." + reason + ".title", t("settings.extensionTaskStatus.failed")),
    hint: t("settings.extensionTaskError." + reason + ".hint", t("settings.extensionInstallFailed")),
  };
}

function extensionTaskIsVisible(task, now) {
  if (["queued", "running", "cancelling"].indexOf(task.status) >= 0) return true;
  if (["failed", "interrupted"].indexOf(task.status) < 0) return false;
  var finishedAt = Date.parse(task.finished_at || "");
  return Number.isFinite(finishedAt) && now - finishedAt < 30000;
}

function extensionAuditLabel(prefix, value, t) {
  var aliases = {
    "install.start": "installStart", "install.finish": "installFinish",
    uninstall: "uninstall", "mcp.enable": "mcpEnable", "mcp.disable": "mcpDisable",
    "skill.enable": "skillEnable", "skill.disable": "skillDisable",
    "extension.enable": "extensionEnable", "extension.disable": "extensionDisable",
    "source.update": "sourceUpdate", "system.bind": "systemBind", "system.unbind": "systemUnbind",
    "default.set": "defaultSet",
  };
  var resolved = prefix === "Action" ? (aliases[value] || value) : value;
  return t("settings.extensionAudit" + prefix + "." + resolved, value || "—");
}

function ExtensionStatus(props) {
  var item = props.item || {};
  var t = props.t;
  var className = "missing";
  var text = t("settings.extensionNotInstalled");
  if (item.ownership === "builtin") { className = item.health === "healthy" ? "builtin" : "warning"; text = t("settings.extensionBuiltin"); }
  else if (item.ownership === "system") { className = "system"; text = t("settings.extensionSystemInstalled"); }
  else if (item.ownership === "cyrene") { className = item.health === "healthy" ? "managed" : "warning"; text = t("settings.extensionManagedInstalled"); }
  if (item.kind === "mcp") {
    className = item.connection_status === "connected" ? "managed" : "warning";
    text = item.connection_status === "connected" ? t("settings.connected") : t("settings.disconnected");
  }
  if (item.observed_state === "installed" && item.enabled === false) { className = "disabled"; text = t("settings.extensionDisabled"); }
  return React.createElement("span", { className: "wb-extension-status " + className },
    React.createElement("span", { className: "wb-extension-status-dot", "aria-hidden": "true" }), text
  );
}

// ---- External Agent extension-center UI (phase 1, handoff §7) --------------
// The Agent Tab deliberately has no search UI: it renders the recommended
// catalog, the real installed enumeration and a fixed "install other Agent"
// API entry.  Agent runtime endpoints are phase-1 placeholders, so probe /
// restart / auth calls surface the backend's explicit unavailability instead
// of pretending the runtime is ready.

function extensionCopyText(value) {
  if (!value) return Promise.resolve();
  if (window.cyrene && typeof window.cyrene.writeClipboardText === "function") {
    return Promise.resolve(window.cyrene.writeClipboardText(value));
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(value);
  }
  return new Promise(function (resolve, reject) {
    try {
      var input = document.createElement("textarea");
      input.value = value;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      var ok = document.execCommand && document.execCommand("copy");
      document.body.removeChild(input);
      if (ok) resolve(); else reject(new Error("copy_failed"));
    } catch (error) {
      reject(error);
    }
  });
}

var AGENT_PROPOSAL_ENDPOINT = "/api/extensions/agents/install-proposals";
var AGENT_MANIFEST_TEMPLATE = JSON.stringify({
  manifestApi: "cyrene.agent/v1",
  agentId: "my-agent",
  displayName: "My Agent",
  version: "1.0.0",
  driver: "acp_stdio",
  command: "my-agent",
  protocolVersion: 1,
  description: "Declarative ACP stdio Agent profile.",
  capabilities: {
    session: { load: "unknown" },
    input: { text: "supported", image: "unknown", file: "unknown", audio: "unknown" },
    output: { streaming: "supported", reasoning: "unknown", toolLifecycle: "unknown", artifacts: "unknown", diff: "unknown" },
    interaction: { permission: "agent_defined", steer: "unknown", cancel: "supported" },
    model: { agentManaged: "supported", cyreneManaged: ["openai_chat", "openai_responses"], switchDuringSession: "unsupported", reasoningEffort: "unknown" },
  },
  modelAccess: { mode: "cyrene_managed", profileId: "primary" },
}, null, 2);

function agentSourceTrustMeta(agent, t) {
  var trust = String(agent.sourceTrust || agent.source_trust || "").toLowerCase();
  if (trust === "cyrene_recommended") {
    return { className: "recommended", label: t("settings.agentSourceTrust.recommended", "Cyrene recommended") };
  }
  if (trust === "external_verified") {
    return { className: "verified", label: t("settings.agentSourceTrust.externalVerified", "External · verified") };
  }
  return { className: "unverified", label: t("settings.agentSourceTrust.externalUnverified", "External · unverified") };
}

function agentInstallStateLabel(agent, t) {
  var state = String(agent.installState || agent.install_state || "");
  if (state === "installed") return t("settings.agentInstallState.installed", "Installed");
  if (state === "upgrade_available") return t("settings.agentInstallState.upgradeAvailable", "Upgrade available");
  return t("settings.agentInstallState.available", "Available");
}

function agentDriverLabel(driver, t) {
  driver = String(driver || "");
  if (!driver || driver === "cyrene_builtin") return t("settings.agentDriver.builtin", "Built-in");
  if (driver === "acp_stdio") return "ACP · stdio";
  return t("settings.agentDriver.unknown", { driver: driver }, "Other driver · " + driver);
}

function agentAuthLabel(agent, t) {
  var state = String(agent.authState || agent.auth_state || "not_configured").toLowerCase();
  var labels = {
    not_configured: t("settings.agentAuth.notConfigured", "Not configured"),
    authenticating: t("settings.agentAuth.authenticating", "Authenticating…"),
    connected: t("settings.agentAuth.connected", "Connected"),
    failed: t("settings.agentAuth.failed", "Login failed"),
    expired: t("settings.agentAuth.expired", "Login expired"),
  };
  return labels[state] || t("settings.agentState.unknownValue", { value: state || "—" }, "Unknown · " + (state || "—"));
}

function agentModelAccessLabel(agent, t) {
  var access = agent.modelAccess || agent.model_access || {};
  if (String(access.mode || "") === "agent_managed") {
    return t("settings.agentModelSource.agentManaged", "Agent-owned configuration");
  }
  var profileId = String(access.profileId || "primary");
  return t("settings.agentModelSource.cyrene", "Cyrene") + (profileId && profileId !== "primary" ? " · " + profileId : "");
}

function agentRuntimeLabel(agent, t) {
  var state = String(agent.runtimeState || agent.runtime_state || "").toLowerCase();
  if (!state || state === "unknown") return t("settings.agentRuntime.unknown", "Unknown");
  var labels = {
    ready: t("settings.agentRuntime.ready", "Ready"),
    running: t("settings.agentRuntime.running", "Running"),
    pending_transport: t("settings.agentRuntime.pendingTransport", "Not tested"),
    not_started: t("settings.agentRuntime.notStarted", "Starts on demand"),
    stopped: t("settings.agentRuntime.stopped", "Stopped"),
    crashed: t("settings.agentRuntime.crashed", "Crashed"),
    error: t("settings.agentRuntime.error", "Error"),
  };
  return labels[state] || t("settings.agentState.unknownValue", { value: state || "—" }, "Unknown · " + (state || "—"));
}

function agentUsabilityMeta(agent, t) {
  var installState = String(agent.installState || agent.install_state || "");
  var authState = String(agent.authState || agent.auth_state || "").toLowerCase();
  var runtimeState = String(agent.runtimeState || agent.runtime_state || "").toLowerCase();
  if (installState && installState !== "installed" && installState !== "upgrade_available") {
    return { usable: false, label: t("settings.agentUsability.notInstalled", "Unavailable · not installed") };
  }
  if (agent.enabled === false) {
    return { usable: false, label: t("settings.agentUsability.disabled", "Unavailable · disabled") };
  }
  if (authState === "expired" || authState === "failed") {
    return { usable: false, label: t("settings.agentUsability.auth", "Unavailable · login required") };
  }
  if (["error", "crashed", "failed"].indexOf(runtimeState) >= 0) {
    return { usable: false, label: t("settings.agentUsability.runtime", "Unavailable · runtime error") };
  }
  return { usable: true, label: t("settings.agentUsability.available", "Available in Composer") };
}

function agentCapabilityGroupLabel(group, t) {
  var labels = {
    session: t("settings.agentCapabilityGroup.session", "Session"),
    input: t("settings.agentCapabilityGroup.input", "Input"),
    output: t("settings.agentCapabilityGroup.output", "Output"),
    interaction: t("settings.agentCapabilityGroup.interaction", "Interaction"),
    model: t("settings.agentCapabilityGroup.model", "Model"),
  };
  return labels[group] || group;
}

function agentCapabilityStateLabel(state, t) {
  state = String(state || "unknown").toLowerCase();
  var labels = {
    supported: t("settings.agentCapabilityState.supported", "Supported"),
    unsupported: t("settings.agentCapabilityState.unsupported", "Unsupported"),
    unknown: t("settings.agentCapabilityState.unknown", "Unknown"),
    degraded: t("settings.agentCapabilityState.degraded", "Degraded"),
    agent_defined: t("settings.agentCapabilityState.agentDefined", "Agent-defined"),
  };
  return labels[state] || t("settings.agentState.unknownValue", { value: state || "—" }, "Unknown · " + (state || "—"));
}

function agentCapabilityRows(agent) {
  var caps = (agent && (agent.capabilities || agent.capabilitySummary)) || {};
  var rows = [];
  ["session", "input", "output", "interaction", "model"].forEach(function (group) {
    var section = caps[group];
    if (!section || typeof section !== "object") return;
    Object.keys(section).forEach(function (feature) {
      var state = section[feature];
      rows.push({
        group: group,
        feature: feature,
        state: Array.isArray(state) ? state.join(", ") : String(state || "unknown"),
      });
    });
  });
  return rows;
}

function agentDetailSettingsFetch(installationId, path, options) {
  return settingsFetch(
    "/api/agents/" + encodeURIComponent(String(installationId || "")) + path,
    options || { method: "GET" }
  ).then(function (response) {
    return response.json().catch(function () { return {}; });
  });
}

function notifyAgentCatalogChanged(reason) {
  try {
    window.dispatchEvent(new CustomEvent("cyrene:agents-changed", { detail: { reason: String(reason || "updated") } }));
  } catch (error) {}
}

function AgentInstallProposalModal(props) {
  var t = props.t;
  var [copied, setCopied] = useStateSt("");
  var [proposalDraft, setProposalDraft] = useStateSt("");
  var [proposalResult, setProposalResult] = useStateSt(null);
  var [proposalBusy, setProposalBusy] = useStateSt("");
  var [proposalError, setProposalError] = useStateSt("");
  function copy(label, value) {
    extensionCopyText(value).then(function () {
      setCopied(label);
      setTimeout(function () { setCopied(""); }, 1600);
    }).catch(function () {});
  }
  var origin = (window.location && window.location.origin) || "http://localhost:4242";
  var apiUrl = origin + AGENT_PROPOSAL_ENDPOINT;
  var manifestExample = JSON.parse(AGENT_MANIFEST_TEMPLATE);
  var requestExample = JSON.stringify({
    source: { type: "inline", manifest: manifestExample },
    requestedVersion: manifestExample.version,
  }, null, 2);
  var curlExample = [
    "curl -X POST " + JSON.stringify(apiUrl),
    "  -H \"Content-Type: application/json\"",
    "  --data-binary @install-agent-request.json",
  ].join(" \\\n");
  var responseExample = JSON.stringify({
    ok: true,
    proposalId: "agent_prop_…",
    agentId: "my-agent",
    version: "1.0.0",
    sourceTrust: "external_unverified",
    requiresConfirmation: true,
    status: "pending",
  }, null, 2);
  var guideLines = [
    t("settings.agentProposalGuideIntro", "Create a cyrene.agent/v1 Manifest, then submit it to Cyrene as an inline manifest or HTTPS manifest_url."),
    "",
    t("settings.agentProposalApi", "API") + ": POST " + apiUrl,
    t("settings.agentProposalContentType", "Content-Type") + ": application/json",
    t("settings.agentProposalManifestApi", "Manifest API") + ": cyrene.agent/v1",
    t("settings.agentProposalDrivers", "Supported") + ": ACP stdio",
    "",
    t("settings.agentProposalRequestLabel", "Complete request") + ":",
    requestExample,
    "",
    t("settings.agentProposalResponseLabel", "Expected response") + ":",
    responseExample,
    "",
    t("settings.agentProposalConfirmLabel", "Confirmation endpoint") + ": POST " + apiUrl + "/{proposalId}/confirm",
  ].join("\n");
  var fullGuide = guideLines + "\n\n" + t("settings.agentProposalExplain", "Agents submitted through this API first become a pending install proposal. They are installed only after you confirm the source and the program that will run.") + "\n\n" + t("settings.agentProposalExternalNote", "Agents installed through this API are marked as external sources and are never presented as Cyrene recommendations.");
  function createProposal() {
    var parsed;
    try {
      parsed = JSON.parse(String(proposalDraft || ""));
    } catch (error) {
      setProposalError(t("settings.agentProposalInvalidJson", "Enter a valid JSON request or cyrene.agent/v1 Manifest."));
      return;
    }
    var body = parsed && parsed.manifestApi === "cyrene.agent/v1"
      ? { source: { type: "inline", manifest: parsed }, requestedVersion: parsed.version || "" }
      : parsed;
    setProposalBusy("create"); setProposalError(""); setProposalResult(null);
    settingsFetch(AGENT_PROPOSAL_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (response) { return response.json(); }).then(function (payload) {
      setProposalResult(payload || {});
    }).catch(function (error) {
      setProposalError(error.message || String(error));
    }).finally(function () { setProposalBusy(""); });
  }
  function confirmProposal() {
    var proposalId = String(proposalResult && proposalResult.proposalId || "");
    if (!proposalId) return;
    setProposalBusy("confirm"); setProposalError("");
    settingsFetch(AGENT_PROPOSAL_ENDPOINT + "/" + encodeURIComponent(proposalId) + "/confirm", { method: "POST" })
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        setProposalResult({ ...proposalResult, confirmation: payload, status: "confirmed" });
        notifyAgentCatalogChanged("installed");
        if (props.onUpdated) props.onUpdated();
      }).catch(function (error) {
        setProposalError(error.message || String(error));
      }).finally(function () { setProposalBusy(""); });
  }
  return React.createElement("div", { className: "wb-extension-modal-scrim", onMouseDown: function (event) { if (event.target === event.currentTarget) props.onClose(); } },
    React.createElement("section", { className: "wb-extension-modal wb-agent-proposal-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "agent-proposal-title" },
      React.createElement("header", null,
        React.createElement("div", null,
          React.createElement("h3", { id: "agent-proposal-title" }, t("settings.agentProposalTitle", "Install another Agent")),
          React.createElement("p", null, t("settings.agentProposalSubtitle", "Copy the interface description for a developer or another Agent to generate a compatible cyrene.agent/v1 manifest."))
        ),
        React.createElement("button", { type: "button", className: "wb-extension-close", onClick: props.onClose, "aria-label": t("settings.close") }, "×")
      ),
      React.createElement("div", { className: "wb-agent-proposal-body" },
        React.createElement("dl", { className: "wb-agent-proposal-facts" },
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentProposalApi", "API")), React.createElement("dd", { className: "mono" }, "POST " + apiUrl)),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentProposalContentType", "Content-Type")), React.createElement("dd", { className: "mono" }, "application/json")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentProposalManifestApi", "Manifest API")), React.createElement("dd", { className: "mono" }, "cyrene.agent/v1")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentProposalDrivers", "Supported")), React.createElement("dd", null, "ACP stdio"))
        ),
        React.createElement("div", { className: "wb-agent-proposal-copy-row" },
          React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { copy("api", curlExample); } }, copied === "api" ? t("settings.copied", "Copied") : t("settings.agentProposalCopyApi", "Copy cURL")),
          React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { copy("example", requestExample); } }, copied === "example" ? t("settings.copied", "Copied") : t("settings.agentProposalCopyExample", "Copy complete request")),
          React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { copy("template", AGENT_MANIFEST_TEMPLATE); } }, copied === "template" ? t("settings.copied", "Copied") : t("settings.agentProposalCopyTemplate", "Copy manifest template"))
        ),
        React.createElement("div", { className: "wb-agent-proposal-preview-head" },
          React.createElement("strong", null, t("settings.agentProposalRequestLabel", "Complete request")),
          React.createElement("span", null, t("settings.agentProposalRequestHint", "Replace command and capability declarations with the external Agent's actual ACP entry point."))
        ),
        React.createElement("pre", { className: "wb-agent-proposal-example mono" }, requestExample),
        React.createElement("div", { className: "wb-agent-proposal-preview-head" },
          React.createElement("strong", null, t("settings.agentProposalResponseLabel", "Expected response")),
          React.createElement("span", null, t("settings.agentProposalResponseHint", "Use proposalId to call the confirmation endpoint after reviewing the source and executable."))
        ),
        React.createElement("pre", { className: "wb-agent-proposal-example mono" }, responseExample),
        React.createElement("section", { className: "wb-agent-proposal-submit" },
          React.createElement("div", { className: "wb-agent-proposal-preview-head" },
            React.createElement("strong", null, t("settings.agentProposalSubmitTitle", "Submit in Cyrene")),
            React.createElement("span", null, t("settings.agentProposalSubmitHint", "Paste either the complete request or a cyrene.agent/v1 Manifest. Cyrene validates it before showing confirmation."))
          ),
          React.createElement("textarea", {
            className: "wb-input mono",
            rows: 9,
            value: proposalDraft,
            placeholder: t("settings.agentProposalSubmitPlaceholder", "Paste request JSON or Manifest JSON"),
            onChange: function (event) { setProposalDraft(event.target.value); setProposalError(""); },
          }),
          React.createElement("div", { className: "wb-agent-proposal-submit-actions" },
            React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { setProposalDraft(requestExample); } }, t("settings.agentProposalUseExample", "Load example")),
            React.createElement("button", { type: "button", className: "wb-btn primary", disabled: proposalBusy || !proposalDraft.trim(), onClick: createProposal }, proposalBusy === "create" ? t("settings.loading", "Loading…") : t("settings.agentProposalCreate", "Validate and create proposal"))
          ),
          proposalError && React.createElement("div", { className: "wb-agent-unavailable", role: "alert" }, proposalError),
          proposalResult && React.createElement("div", { className: "wb-agent-proposal-result", role: "status" },
            React.createElement("strong", null, proposalResult.displayName || proposalResult.agentId || t("settings.agentProposalReady", "Proposal ready")),
            React.createElement("code", null, proposalResult.proposalId || "—"),
            React.createElement("span", null, [proposalResult.version, proposalResult.inspect && proposalResult.inspect.command, proposalResult.sourceTrust].filter(Boolean).join(" · ")),
            proposalResult.requiresConfirmation && proposalResult.status !== "confirmed" && React.createElement("button", { type: "button", className: "wb-btn primary", disabled: proposalBusy === "confirm", onClick: confirmProposal }, proposalBusy === "confirm" ? t("settings.loading", "Loading…") : t("settings.agentProposalConfirmInstall", "Confirm and install")),
            proposalResult.status === "confirmed" && React.createElement("b", null, t("settings.agentProposalInstallStarted", "Installation started"))
          )
        ),
        React.createElement("div", { className: "wb-agent-proposal-note" },
          React.createElement("strong", null, t("settings.agentProposalSafetyTitle", "Confirmation required")),
          React.createElement("p", null, t("settings.agentProposalExplain", "Agents submitted through this API first become a pending install proposal. They are installed only after you confirm the source and the program that will run.")),
          React.createElement("p", null, t("settings.agentProposalExternalNote", "Agents installed through this API are marked as external sources and are never presented as Cyrene recommendations.")),
          React.createElement("p", null, t("settings.agentProposalNoCredentials", "Copied content never contains Cyrene tokens, model keys or Agent credentials."))
        ),
        React.createElement("div", { className: "wb-agent-proposal-footer" },
          React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { copy("all", fullGuide); } }, copied === "all" ? t("settings.copied", "Copied") : t("settings.agentProposalCopyAll", "Copy full instructions")),
          React.createElement("button", { type: "button", className: "wb-btn primary", onClick: props.onClose }, t("settings.close"))
        )
      )
    )
  );
}

function AgentCapabilityTable({ agent, t }) {
  var rows = agentCapabilityRows(agent);
  if (!rows.length) {
    return React.createElement("div", { className: "wb-agent-capabilities-empty" },
      t("settings.agentCapabilitiesEmpty", "No capabilities have been declared or detected yet. Use Test connection to probe this Agent.")
    );
  }
  var groups = {};
  rows.forEach(function (row) {
    (groups[row.group] = groups[row.group] || []).push(row);
  });
  return React.createElement("div", { className: "wb-agent-capabilities" },
    ["session", "input", "output", "interaction", "model"].map(function (group) {
      if (!groups[group]) return null;
      return React.createElement("section", { key: group, className: "wb-agent-capability-group" },
        React.createElement("h5", null, agentCapabilityGroupLabel(group, t)),
        React.createElement("ul", null, groups[group].map(function (row) {
          return React.createElement("li", { key: row.group + "." + row.feature },
            React.createElement("code", null, row.feature),
            React.createElement("span", null, agentCapabilityStateLabel(row.state, t))
          );
        }))
      );
    })
  );
}

function AgentCard(props) {
  var agent = props.agent || {};
  var t = props.t;
  var [localBusy, setLocalBusy] = useStateSt("");
  var busy = props.busy || localBusy;
  var expanded = props.expanded;
  var trust = agentSourceTrustMeta(agent, t);
  var usability = agentUsabilityMeta(agent, t);
  var installationId = String(agent.installationId || "");
  var [modelAccessDraft, setModelAccessDraft] = useStateSt(null);
  var [probeResult, setProbeResult] = useStateSt(null);
  var [diagnostics, setDiagnostics] = useStateSt(null);
  var [authResult, setAuthResult] = useStateSt(null);
  function loadDetail() {
    agentDetailSettingsFetch(installationId, "").then(function (payload) {
      if (payload && payload.agent) props.onChanged(payload.agent);
    }).catch(function () {});
  }
  function toggleEnabled() {
    props.onToggle(agent);
  }
  function runProbe() {
    setLocalBusy("probe");
    agentDetailSettingsFetch(installationId, "/probe", { method: "POST" }).then(function (payload) {
      setProbeResult(payload || {});
      if (payload && payload.agent) props.onChanged(payload.agent);
      else loadDetail();
      notifyAgentCatalogChanged("probe");
      setLocalBusy("");
    }).catch(function () { setLocalBusy(""); });
  }
  function runRestart() {
    setLocalBusy("restart");
    agentDetailSettingsFetch(installationId, "/restart", { method: "POST" }).then(function (payload) {
      setProbeResult(payload || {});
      notifyAgentCatalogChanged("restart");
      setLocalBusy("");
    }).catch(function () { setLocalBusy(""); });
  }
  function runAuth(action) {
    setLocalBusy("auth:" + action);
    agentDetailSettingsFetch(installationId, "/auth/" + action, { method: "POST" }).then(function (payload) {
      setAuthResult(payload || {});
      notifyAgentCatalogChanged("auth");
      setLocalBusy("");
    }).catch(function () { setLocalBusy(""); });
  }
  function loadDiagnostics() {
    setLocalBusy("diagnostics");
    agentDetailSettingsFetch(installationId, "/diagnostics").then(function (payload) {
      setDiagnostics(payload || {});
      setLocalBusy("");
    }).catch(function () { setLocalBusy(""); });
  }
  function saveModelAccess(mode, profileId) {
    var body = { modelAccess: { mode: mode } };
    if (mode === "cyrene_managed") body.modelAccess.profileId = String(profileId || "primary") || "primary";
    setLocalBusy("model");
    agentDetailSettingsFetch(installationId, "/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (payload) {
      if (payload && payload.agent) props.onChanged(payload.agent);
      notifyAgentCatalogChanged("model_access");
      setLocalBusy("");
    }).catch(function () { setLocalBusy(""); });
  }
  function placeholderNote(result) {
    if (!result) return "";
    if (result.ok !== false && !result.error) return "";
    return String(result.detail || result.error || "");
  }
  function localizedDiagnosticsNote(payload) {
    var noteCode = String(payload && (payload.noteCode || payload.reason) || "");
    if (noteCode === "starts_on_demand") {
      return t("settings.agentDiagnosticsStartsOnDemand", "ACP stdio starts on demand. Diagnostics never expose process environment or credentials.");
    }
    return "";
  }
  var version = String(agent.version || "");
  var driver = agentDriverLabel(agent.driver || agent.runtime && agent.runtime.transport, t);
  var authNote = placeholderNote(authResult);
  var probeNote = placeholderNote(probeResult);
  var diagnosticsNote = localizedDiagnosticsNote(diagnostics);
  return React.createElement("article", { className: "wb-extension-card wb-agent-card" + (expanded ? " expanded" : ""), "data-installation-id": installationId },
    React.createElement("div", { className: "wb-extension-card-main" },
      React.createElement("button", {
        type: "button",
        className: "wb-extension-card-summary",
        onClick: props.onToggleExpand,
        "aria-expanded": expanded ? "true" : "false",
        "aria-label": t("settings.extensionDetailsFor", { name: agent.displayName || agent.agentId }),
      },
        React.createElement("span", { className: "wb-extension-glyph agent extension-agent" }, React.createElement(ExtensionGlyph, { id: agent.agentId, kind: "agent", label: agent.displayName || agent.agentId })),
        React.createElement("span", { className: "wb-extension-copy" },
          React.createElement("span", { className: "wb-extension-title-row" },
            React.createElement("strong", null, agent.displayName || agent.agentId || "—"),
            React.createElement("span", { className: "wb-extension-type" }, t("settings.extensionType.agent", "Agent")),
            React.createElement("span", { className: "wb-agent-trust " + trust.className }, trust.label),
            React.createElement("span", { className: "wb-agent-usability " + (usability.usable ? "available" : "unavailable") }, usability.label)
          ),
          React.createElement("span", { className: "wb-extension-description" },
            [agentInstallStateLabel(agent, t), agentDriverLabel(agent.driver, t), agentAuthLabel(agent, t), agentModelAccessLabel(agent, t)].filter(Boolean).join(" · ")
          ),
          React.createElement("span", { className: "wb-extension-meta" },
            React.createElement("span", { className: "wb-agent-state" + (agent.enabled === false ? " disabled" : "") },
              React.createElement("span", { className: "wb-extension-status-dot", "aria-hidden": "true" }),
              agent.enabled === false ? t("settings.extensionDisabled") : agentRuntimeLabel(agent, t)
            ),
            version && React.createElement("span", { className: "mono" }, version)
          )
        )
      ),
      React.createElement("div", { className: "wb-extension-actions" },
        React.createElement("button", { type: "button", className: "wb-btn", disabled: busy, onClick: toggleEnabled }, agent.enabled === false ? t("settings.enable") : t("settings.disable")),
        React.createElement("button", { type: "button", className: "wb-btn danger", disabled: busy, onClick: function () { props.onRemove(agent); } }, t("settings.uninstall"))
      ),
      React.createElement("button", {
        type: "button",
        className: "wb-extension-expand-button",
        onClick: props.onToggleExpand,
        "aria-expanded": expanded ? "true" : "false",
        "aria-label": t("settings.extensionDetailsFor", { name: agent.displayName || agent.agentId }),
      }, React.createElement("span", { className: "wb-extension-chevron", "aria-hidden": "true" }, ExternalChevron()))
    ),
    expanded && React.createElement("div", { className: "wb-extension-details wb-agent-details" },
      React.createElement("div", { className: "wb-extension-enabled-row" },
        React.createElement("div", null,
          React.createElement("strong", null, t("settings.extensionEnabledTitle")),
          React.createElement("small", null, t("settings.agentEnabledHint", "Disabled Agents are not selectable in the Composer."))
        ),
        Toggle(agent.enabled !== false, toggleEnabled, busy, t("settings.extensionToggle", { name: agent.displayName || agent.agentId }))
      ),
      React.createElement("section", { className: "wb-agent-detail-section", "aria-labelledby": "agent-overview-" + installationId },
        React.createElement("h4", { id: "agent-overview-" + installationId }, t("settings.agentSectionOverview", "Overview")),
        React.createElement("dl", null,
          React.createElement("div", null, React.createElement("dt", null, t("settings.extensionVersion")), React.createElement("dd", { className: "mono" }, version || "—")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.extensionSource")), React.createElement("dd", null, trust.label)),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentPublisher", "Publisher")), React.createElement("dd", null, agent.publisher || "—")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentSourceUrl", "Manifest / repository")), React.createElement("dd", { className: "mono" }, String((agent.source || {}).url || agent.repository || "—"))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentChecksum", "SHA-256")), React.createElement("dd", { className: "mono" }, String((agent.checksums || {}).sha256 || t("settings.agentUnverified", "Unverified")))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentInstallationId")), React.createElement("dd", { className: "mono" }, installationId || "—")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentDriverProtocol", "Driver / protocol")), React.createElement("dd", { className: "mono" }, driver || "—")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.extensionHealth")), React.createElement("dd", null, agentRuntimeLabel(agent, t))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentLoginState", "Login")), React.createElement("dd", null, agentAuthLabel(agent, t))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentModelSourceShort", "Model source")), React.createElement("dd", null, agentModelAccessLabel(agent, t))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentComposerAvailability", "Composer availability")), React.createElement("dd", { className: "wb-agent-usability " + (usability.usable ? "available" : "unavailable") }, usability.label))
        )
      ),
      React.createElement("section", { className: "wb-agent-detail-section", "aria-labelledby": "agent-auth-" + installationId },
        React.createElement("h4", { id: "agent-auth-" + installationId }, t("settings.agentSectionAuthModel", "Login & model")),
        React.createElement("div", { className: "wb-agent-model-access" },
          React.createElement("label", null,
            React.createElement("input", { type: "radio", name: "model-access-" + installationId, checked: String((agent.modelAccess || {}).mode || "cyrene_managed") !== "agent_managed", onChange: function () { saveModelAccess("cyrene_managed", String((agent.modelAccess || {}).profileId || "primary")); } }),
            React.createElement("span", null, React.createElement("strong", null, t("settings.agentModelCyrene", "Use Cyrene models")), React.createElement("small", null, t("settings.agentModelCyreneHint", "Routes through the Cyrene Model Gateway; no long-lived key is exposed to the Agent.")))
          ),
          React.createElement("label", null,
            React.createElement("input", { type: "radio", name: "model-access-" + installationId, checked: String((agent.modelAccess || {}).mode || "") === "agent_managed", onChange: function () { saveModelAccess("agent_managed"); } }),
            React.createElement("span", null, React.createElement("strong", null, t("settings.agentModelOwn", "Use the Agent's own configuration")), React.createElement("small", null, t("settings.agentModelOwnHint", "The Agent keeps its own OAuth, API key or environment configuration.")))
          )
        ),
        React.createElement("div", { className: "wb-agent-action-row" },
          React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "auth:start", onClick: function () { runAuth("start"); } }, t("settings.agentLoginStart", "Start login")),
          React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "auth:logout", onClick: function () { runAuth("logout"); } }, t("settings.agentLoginLogout", "Log out"))
        ),
        authNote && React.createElement("div", { className: "wb-agent-unavailable", role: "status" }, authNote),
        !authNote && React.createElement("p", { className: "wb-hint" }, t("settings.agentAuthHint", "Login is handled through the methods advertised by the Agent. Some Agents require login in their own terminal instead."))
      ),
      React.createElement("section", { className: "wb-agent-detail-section", "aria-labelledby": "agent-caps-" + installationId },
        React.createElement("h4", { id: "agent-caps-" + installationId }, t("settings.agentSectionCapabilities", "Capabilities")),
        React.createElement(AgentCapabilityTable, { agent: agent, t: t })
      ),
      React.createElement("section", { className: "wb-agent-detail-section", "aria-labelledby": "agent-runtime-" + installationId },
        React.createElement("h4", { id: "agent-runtime-" + installationId }, t("settings.agentSectionRuntime", "Runtime")),
        React.createElement("dl", null,
          React.createElement("div", null, React.createElement("dt", null, t("settings.placeholderCommand", "Command")), React.createElement("dd", { className: "mono" }, agent.command || "—")),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentRuntimeState", "Process state")), React.createElement("dd", null, agentRuntimeLabel(agent, t))),
          React.createElement("div", null, React.createElement("dt", null, t("settings.agentLastStarted", "Last started")), React.createElement("dd", { className: "mono" }, (agent.runtime && agent.runtime.lastStartedAt) || "—"))
        ),
        React.createElement("div", { className: "wb-agent-action-row" },
          React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "restart", onClick: runRestart }, t("settings.agentRestart", "Restart Agent")),
          React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "probe", onClick: runProbe }, t("settings.agentProbe", "Test connection"))
        ),
        probeNote && React.createElement("div", { className: "wb-agent-unavailable", role: "status" }, probeNote)
      ),
      React.createElement("section", { className: "wb-agent-detail-section", "aria-labelledby": "agent-diagnostics-" + installationId },
        React.createElement("h4", { id: "agent-diagnostics-" + installationId }, t("settings.agentSectionDiagnostics", "Diagnostics")),
        React.createElement("div", { className: "wb-agent-action-row" },
          React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "diagnostics", onClick: loadDiagnostics }, t("settings.agentLoadDiagnostics", "Load diagnostics"))
        ),
        diagnosticsNote && React.createElement("p", { className: "wb-hint" }, diagnosticsNote),
        (diagnostics && (diagnostics.lastErrors || []).length > 0) && React.createElement("ul", { className: "wb-agent-errors" },
          diagnostics.lastErrors.map(function (error, index) {
            return React.createElement("li", { key: index, className: "mono" }, String(error));
          })
        )
      )
    )
  );
}

function AgentTabPanel(props) {
  var t = props.t;
  var recommended = Array.isArray(props.recommended) ? props.recommended : [];
  var installed = Array.isArray(props.installed) ? props.installed : [];
  return React.createElement("div", { className: "wb-agent-tab" },
    React.createElement("section", { className: "wb-agent-section", "aria-labelledby": "wb-agent-recommended-title" },
      React.createElement("div", { className: "wb-agent-section-head" },
        React.createElement("h4", { id: "wb-agent-recommended-title" }, t("settings.agentRecommended", "Recommended")),
        React.createElement("span", null, t("settings.agentRecommendedHint", "Curated by Cyrene"))
      ),
      recommended.length === 0
        ? React.createElement("div", { className: "wb-extensions-empty" }, t("settings.agentRecommendedEmpty", "No recommended Agents available."))
        : React.createElement("div", { className: "wb-agent-recommended-list" },
            recommended.map(function (item) {
              var state = String(item.installState || "available");
              var installedInstallationId = String(item.installationId || "");
              return React.createElement("article", { key: item.agentId, className: "wb-extension-card wb-agent-recommended-card" },
                React.createElement("div", { className: "wb-extension-card-main" },
                  React.createElement("div", { className: "wb-extension-card-summary" },
                    React.createElement("span", { className: "wb-extension-glyph agent extension-agent" }, React.createElement(ExtensionGlyph, { id: item.agentId, kind: "agent", label: item.displayName || item.agentId })),
                    React.createElement("span", { className: "wb-extension-copy" },
                      React.createElement("span", { className: "wb-extension-title-row" },
                        React.createElement("strong", null, item.displayName || item.agentId),
                        React.createElement("span", { className: "wb-extension-type" }, t("settings.extensionType.agent", "Agent"))
                      ),
                      React.createElement("span", { className: "wb-extension-description" }, String(item.description || "")),
                      React.createElement("span", { className: "wb-extension-meta" },
                        React.createElement("span", { className: "wb-agent-state" }, agentInstallStateLabel(item, t)),
                        item.version && React.createElement("span", { className: "mono" }, item.version),
                        React.createElement("span", null, agentDriverLabel(item.driver, t))
                      )
                    )
                  ),
                  React.createElement("div", { className: "wb-extension-actions" },
                    state === "installed"
                      ? (installedInstallationId
                          ? React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { props.onOpenInstalled(installedInstallationId); } }, t("settings.agentOpenDetails", "Details"))
                          : React.createElement("button", { type: "button", className: "wb-btn", disabled: true }, t("settings.agentInstallState.installed", "Installed")))
                      : state === "upgrade_available"
                        ? React.createElement("button", { type: "button", className: "wb-btn primary", disabled: props.busy, onClick: function () { props.onInstall(item); } }, t("settings.agentUpgrade", "Upgrade"))
                        : React.createElement("button", { type: "button", className: "wb-btn primary", disabled: props.busy, onClick: function () { props.onInstall(item); } }, t("settings.install"))
                  )
                )
              );
            })
          )
    ),
    React.createElement("section", { className: "wb-agent-section", "aria-labelledby": "wb-agent-installed-title" },
      React.createElement("div", { className: "wb-agent-section-head" },
        React.createElement("h4", { id: "wb-agent-installed-title" }, t("settings.agentInstalled", "Installed")),
        React.createElement("span", null, t("settings.agentInstalledCount", { n: installed.length }))
      ),
      installed.length === 0
        ? React.createElement("div", { className: "wb-extensions-empty" }, t("settings.agentInstalledEmpty", "No Agents installed yet."))
        : React.createElement("div", { className: "wb-agent-installed-list" },
            installed.map(function (agent) {
              var installationId = String(agent.installationId || "");
              var expanded = props.expandedId === installationId;
              return React.createElement(AgentCard, {
                key: installationId || agent.agentId,
                agent: agent,
                t: t,
                busy: props.busy === "agent:" + installationId,
                expanded: expanded,
                onToggleExpand: function () { props.onToggleExpand(expanded ? "" : installationId); },
                onToggle: props.onToggle,
                onRemove: props.onRemove,
                onChanged: function (updated) {
                  if (props.onChanged) props.onChanged(updated);
                },
              });
            })
          )
    ),
    React.createElement("div", { className: "wb-agent-install-other" },
      React.createElement("button", { type: "button", className: "wb-btn primary", onClick: props.onOpenProposal },
        React.createElement("span", { "aria-hidden": "true" }, "+ "),
        t("settings.agentInstallOther", "Install another Agent")
      ),
      React.createElement("p", null, t("settings.agentInstallOtherHint", "Non-recommended Agents are marked as external sources and appear in the Installed list after confirmation."))
    )
  );
}

function SkillFileDirectory(props) {
  var files = Array.isArray(props.files) ? props.files : [];
  var entrypoint = String(props.entrypoint || "SKILL.md");
  var rows = [];
  var seenDirectories = {};
  files.forEach(function (file) {
    var path = String(file.path || file.name || "");
    var parts = path.split(/[\\/]/).filter(Boolean);
    parts.slice(0, -1).forEach(function (part, index) {
      var directoryPath = parts.slice(0, index + 1).join("/");
      if (seenDirectories[directoryPath]) return;
      seenDirectories[directoryPath] = true;
      rows.push({ key: "directory:" + directoryPath, name: part + "/", path: directoryPath, depth: index, directory: true });
    });
    rows.push({
      key: "file:" + path,
      name: parts[parts.length - 1] || path || "—",
      path: path,
      depth: Math.max(0, parts.length - 1),
      directory: false,
      size: Number(file.size || 0),
    });
  });
  return React.createElement("div", { className: "wb-extension-skill-files", role: "list" },
    rows.map(function (row) {
      var isEntrypoint = !row.directory && (row.path === entrypoint || row.name === entrypoint);
      var sizeLabel = row.size < 1024 ? row.size + " B" : (row.size / 1024).toFixed(1) + " KB";
      return React.createElement("div", {
        key: row.key,
        className: "wb-extension-skill-file" + (row.directory ? " directory" : "") + (isEntrypoint ? " entrypoint" : ""),
        role: "listitem",
        style: { paddingLeft: (11 + row.depth * 14) + "px" },
        title: row.path,
      },
        React.createElement("span", { className: "wb-extension-skill-file-name mono" }, row.name),
        isEntrypoint && React.createElement("span", { className: "wb-extension-skill-entrypoint" }, "SKILL"),
        !row.directory && React.createElement("span", { className: "wb-extension-skill-file-size" }, sizeLabel)
      );
    })
  );
}

function ExtensionCard(props) {
  var item = props.item;
  var t = props.t;
  var busy = props.busy;
  var [expanded, setExpanded] = useStateSt(false);
  var canInstall = (item.capabilities || []).indexOf("install") >= 0;
  var canUninstall = (item.capabilities || []).some(function (value) { return value === "uninstall" || value === "uninstall_managed" || value === "remove"; });
  var canToggle = item.observed_state === "installed" && (item.capabilities || []).some(function (value) { return value === "enable" || value === "disable"; });
  var canUseLocalProgram = (item.capabilities || []).indexOf("bind_system") >= 0;
  var canStopUsingLocalProgram = (item.capabilities || []).indexOf("unbind_system") >= 0;
  var canConfigureHook = item.kind === "cli" && item.observed_state === "installed";
  var typeText = t("settings.extensionType." + item.kind);
  var version = String(item.version || item.recommended_version || "").replace(/^python\s+/i, "").replace(/^v/, "");
  var displayName = extensionDisplayName(item, t);
  var displayDescription = extensionDisplayDescription(item, t);
  return React.createElement("article", { className: "wb-extension-card" + (expanded ? " expanded" : "") },
    React.createElement("div", { className: "wb-extension-card-main" },
      React.createElement("button", {
        type: "button", className: "wb-extension-card-summary", onClick: function () { setExpanded(!expanded); },
        "aria-expanded": expanded ? "true" : "false", "aria-label": t("settings.extensionDetailsFor", { name: displayName }),
      },
        React.createElement("span", { className: "wb-extension-glyph " + item.kind + " extension-" + String(item.id || "").replace(/[^a-z0-9_-]/gi, "-").toLowerCase() }, React.createElement(ExtensionGlyph, { id: item.id, kind: item.kind, label: displayName })),
        React.createElement("span", { className: "wb-extension-copy" },
          React.createElement("span", { className: "wb-extension-title-row" },
            React.createElement("strong", null, displayName),
            React.createElement("span", { className: "wb-extension-type" }, typeText),
            item.id === "python" && item.observed_state === "missing" && React.createElement("span", { className: "wb-extension-recommended" }, t("settings.extensionRecommendedInstall")),
          ),
          React.createElement("span", { className: "wb-extension-description" }, displayDescription),
          React.createElement("span", { className: "wb-extension-meta" },
            React.createElement(ExtensionStatus, { item: item, t: t }),
            version && React.createElement("span", { className: "mono" }, version),
            item.tool_count > 0 && React.createElement("span", null, t("settings.toolsCount", { n: item.tool_count })),
          ),
        ),
      ),
      React.createElement("div", { className: "wb-extension-actions" },
        canInstall && React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button", disabled: busy, onClick: function () { props.onInstall(item); } }, t("settings.install")),
        canUninstall && (item.ownership === "cyrene" || item.managed_available) && React.createElement("button", { type: "button", className: "wb-btn danger", disabled: busy, onClick: function () { props.onRemove(item); } }, item.kind === "mcp" ? t("settings.delete") : t("settings.uninstall")),
      ),
      React.createElement("button", {
        type: "button",
        className: "wb-extension-expand-button",
        onClick: function () { setExpanded(!expanded); },
        "aria-expanded": expanded ? "true" : "false",
        "aria-label": t("settings.extensionDetailsFor", { name: displayName }),
      }, React.createElement("span", { className: "wb-extension-chevron", "aria-hidden": "true" }, ExternalChevron())),
    ),
    expanded && React.createElement("div", { className: "wb-extension-details" },
      canToggle && React.createElement("div", { className: "wb-extension-enabled-row" },
        React.createElement("div", null,
          React.createElement("strong", null, t("settings.extensionEnabledTitle")),
          React.createElement("small", null, t("settings.extensionEnabledHint." + item.kind))
        ),
        Toggle(item.enabled !== false, function () { props.onToggle(item); }, busy, t("settings.extensionToggle", { name: displayName }))
      ),
      React.createElement("dl", null,
        React.createElement("div", null, React.createElement("dt", null, t("settings.extensionSource")), React.createElement("dd", null, extensionSourceLabel(item, t))),
        React.createElement("div", null, React.createElement("dt", null, t("settings.extensionVersion")), React.createElement("dd", { className: "mono" }, version || "—")),
        React.createElement("div", null, React.createElement("dt", null, t("settings.extensionPath")), React.createElement("dd", { className: "mono" }, item.path || "—")),
        React.createElement("div", null, React.createElement("dt", null, t("settings.extensionHealth")), React.createElement("dd", null, extensionHealthLabel(item, t))),
        item.ownership === "system" && item.managed_available && React.createElement("div", null, React.createElement("dt", null, t("settings.extensionManagedInstalled")), React.createElement("dd", { className: "mono" }, [item.managed_version, item.managed_path].filter(Boolean).join(" · "))),
      ),
      item.kind === "mcp" && React.createElement("section", { className: "wb-extension-mcp-tools", "aria-labelledby": "mcp-tools-" + item.id },
        React.createElement("div", { className: "wb-extension-mcp-tools-head" },
          React.createElement("h4", { id: "mcp-tools-" + item.id }, t("settings.extensionMcpTools")),
          React.createElement("span", null, t("settings.toolsCount", { n: (item.tools || []).length }))
        ),
        (item.tools || []).length
          ? React.createElement("ul", null, item.tools.map(function (tool) { return React.createElement("li", { key: tool.name },
              React.createElement("code", null, tool.name),
              React.createElement("p", null, tool.description || t("settings.extensionMcpToolNoDescription"))
            ); }))
          : React.createElement("div", { className: "wb-extension-mcp-tools-empty" }, t(item.enabled === false ? "settings.extensionMcpToolsDisabled" : item.connection_status !== "connected" ? "settings.extensionMcpToolsDisconnected" : "settings.extensionMcpToolsEmpty"))
      ),
      item.kind === "toolchain" && (item.versions || []).length > 1 && React.createElement("label", { className: "wb-extension-version-select" },
        React.createElement("span", null, t("settings.extensionDefaultVersion")),
        React.createElement("select", { className: "wb-select", value: item.default_version || item.version, disabled: busy, onChange: function (event) { props.onDefault(item, event.target.value); } },
          item.versions.map(function (value) { return React.createElement("option", { key: value, value: value }, value); })
        )
      ),
      (canUseLocalProgram || canStopUsingLocalProgram) && React.createElement("div", { className: "wb-extension-detail-actions" },
        canUseLocalProgram && React.createElement("button", { type: "button", className: "wb-btn", disabled: busy, onClick: function () { props.onBind(item); } }, t(item.manual_binding ? "settings.extensionChangeSystem" : "settings.extensionBindSystem")),
        canStopUsingLocalProgram && React.createElement("button", { type: "button", className: "wb-btn danger", disabled: busy, onClick: function () { props.onUnbind(item); } }, t("settings.extensionUnbindSystem"))
      ),
      canConfigureHook && React.createElement("div", { className: "wb-extension-hook-action" },
        React.createElement("div", { className: "wb-extension-hook-copy" },
          React.createElement("span", { className: "wb-extension-hook-icon", "aria-hidden": "true" }, React.createElement(AutomationIcon)),
          React.createElement("div", null,
          React.createElement("strong", null, t("settings.extensionHookTitle")),
          React.createElement("small", null, t("settings.extensionHookHint"))
          )
        ),
        React.createElement("button", { type: "button", className: "wb-btn", disabled: busy, onClick: function () { props.onConfigureHook(item); } }, t("settings.extensionConfigureHook"))
      ),
      item.kind === "skill" && React.createElement("div", { className: "wb-extension-skill-content" },
        React.createElement("section", { className: "wb-extension-skill-document", "aria-labelledby": "skill-content-" + item.id },
          React.createElement("h4", { id: "skill-content-" + item.id }, t("settings.extensionSkillContent", "Skill instructions")),
          item.preview
            ? React.createElement("div", {
                className: "wb-extension-skill-markdown markdown",
                dangerouslySetInnerHTML: { __html: renderSettingsMarkdown(item.preview) },
              })
            : React.createElement("div", { className: "wb-extension-skill-empty" }, t("settings.extensionSkillContentEmpty", "No Skill instructions found."))
        ),
        React.createElement("aside", { className: "wb-extension-skill-directory", "aria-labelledby": "skill-files-" + item.id },
          React.createElement("div", { className: "wb-extension-skill-directory-head" },
            React.createElement("h4", { id: "skill-files-" + item.id }, t("settings.extensionSkillDirectory", "Skill file directory")),
            React.createElement("span", null, t("settings.extensionSkillFileCount", { n: (item.files || []).length }, "{n} files"))
          ),
          (item.files || []).length
            ? React.createElement(SkillFileDirectory, { files: item.files, entrypoint: item.entrypoint_name })
            : React.createElement("div", { className: "wb-extension-skill-empty" }, t("settings.extensionSkillDirectoryEmpty", "No files found."))
        )
      ),
    )
  );
}

function ExtensionsPanel(p) {
  var t = p.t;
  var [data, setData] = useStateSt({ recommended: [], skills: [], mcp: [], cli: [], toolchains: [], tasks: [] });
  var [category, setCategory] = useStateSt("recommended");
  var [query, setQuery] = useStateSt("");
  var [loading, setLoading] = useStateSt(true);
  var [busy, setBusy] = useStateSt("");
  var [notice, setNotice] = useStateSt("");
  var [noticeKind, setNoticeKind] = useStateSt("info");
  var [installOpen, setInstallOpen] = useStateSt(false);
  var [installKind, setInstallKind] = useStateSt("skill");
  var [remoteQuery, setRemoteQuery] = useStateSt("");
  var [remoteResults, setRemoteResults] = useStateSt([]);
  var [remoteCursor, setRemoteCursor] = useStateSt("");
  var [remoteLoading, setRemoteLoading] = useStateSt(false);
  var [advanced, setAdvanced] = useStateSt(false);
  var [sourceOpen, setSourceOpen] = useStateSt(false);
  var [sources, setSources] = useStateSt({});
  var [sourceTesting, setSourceTesting] = useStateSt(false);
  var [sourceHealth, setSourceHealth] = useStateSt(null);
  var [skillSelection, setSkillSelection] = useStateSt(null);
  var [manualMcp, setManualMcp] = useStateSt({ name: "", transport: "streamable_http", url: "", command: "", args: "", version: "", headers: "", env: "" });
  var [manualMcpOpen, setManualMcpOpen] = useStateSt(false);
  var [requestedVersion, setRequestedVersion] = useStateSt("");
  var [texChoice, setTexChoice] = useStateSt("tinytex");
  var [bindItem, setBindItem] = useStateSt(null);
  var [bindPath, setBindPath] = useStateSt("");
  var [auditRecords, setAuditRecords] = useStateSt([]);
  var [hooksOpen, setHooksOpen] = useStateSt(false);
  var [hooksData, setHooksData] = useStateSt({ hooks: [], proposals: [], configuration_results: {} });
  var [hookAudit, setHookAudit] = useStateSt([]);
  var emptyHookDraft = { id: "", name: "", description: "", event: "PreToolUse", matcher: "*", priority: 100, failure_policy: "open", timeout_seconds: 10, enabled: false, runner: { type: "command", executable: "", args: [], env: {} } };
  var [hookDraft, setHookDraft] = useStateSt(emptyHookDraft);
  var [hookArgsText, setHookArgsText] = useStateSt("");
  var [hookEditorOpen, setHookEditorOpen] = useStateSt(false);
  var [taskClock, setTaskClock] = useStateSt(Date.now());
  var [agentProposalOpen, setAgentProposalOpen] = useStateSt(false);
  var [agentExpandedId, setAgentExpandedId] = useStateSt("");

  function tell(text, kind) {
    if (showSettingsToast(text, kind || "info")) {
      setNotice("");
      return;
    }
    setNotice(text || ""); setNoticeKind(kind || "info");
    if (text) setTimeout(function () { setNotice(""); }, 5000);
  }

  function load() {
    return settingsFetch("/api/extensions").then(readSettingsResponse).then(function (payload) {
      setData(payload); setLoading(false);
    }).catch(function (error) { setLoading(false); tell(error.message || t("settings.networkError"), "error"); });
  }

  useEffectSt(function () { load(); }, []);
  useEffectSt(function () {
    function openAgentHooks() { loadHooks(true); }
    window.addEventListener("cyrene:open-agent-hooks", openAgentHooks);
    return function () { window.removeEventListener("cyrene:open-agent-hooks", openAgentHooks); };
  }, []);
  // The Composer's Agent submenu opens "Extensions → installed Agent details"
  // for unavailable Agents through this event (handoff §8.2).
  useEffectSt(function () {
    function openAgentDetail(event) {
      var detail = (event && event.detail) || {};
      setCategory("agent");
      setQuery("");
      var installationId = String(detail.installationId || "");
      if (installationId && installationId !== "agent_cyrene_builtin") {
        setAgentExpandedId(installationId);
      }
    }
    window.addEventListener("cyrene:open-agent-detail", openAgentDetail);
    return function () { window.removeEventListener("cyrene:open-agent-detail", openAgentDetail); };
  }, []);
  useEffectSt(function () {
    var active = (data.tasks || []).some(function (task) { return ["queued", "running", "cancelling"].indexOf(task.status) >= 0; });
    if (!active) return undefined;
    var timer = setInterval(load, 1200);
    return function () { clearInterval(timer); };
  }, [data.tasks]);
  useEffectSt(function () {
    var expirations = (data.tasks || []).filter(function (task) { return ["failed", "interrupted"].indexOf(task.status) >= 0; }).map(function (task) { return Date.parse(task.finished_at || "") + 30000; }).filter(function (value) { return Number.isFinite(value) && value > taskClock; });
    if (!expirations.length) return undefined;
    var timer = setTimeout(function () { setTaskClock(Date.now()); }, Math.max(0, Math.min.apply(Math, expirations) - Date.now() + 50));
    return function () { clearTimeout(timer); };
  }, [data.tasks, taskClock]);

  function openInstaller(kind) {
    setInstallKind(kind); setRemoteQuery(""); setRemoteResults([]); setRemoteCursor(""); setSkillSelection(null); setRequestedVersion(""); setManualMcpOpen(false); setInstallOpen(true);
  }

  function startAgentInstall(item) {
    var displayName = item.displayName || item.agentId || item.name || item.id;
    workbenchServices.feedback().confirmModal({
      title: t("settings.extensionInstallConfirmTitle"),
      body: t("settings.agentInstallConfirmBody", { name: displayName, version: item.version || item.recommended_version || "" }),
      confirmLabel: t("settings.install"),
    }).then(function (ok) {
      if (!ok) return;
      setBusy("agent-install:" + String(item.agentId || ""));
      settingsFetch("/api/extensions/install", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "agent", extension_id: item.agentId || item.id }),
      }).then(readSettingsResponse).then(function () {
        notifyAgentCatalogChanged("installed");
        tell(t("settings.extensionInstallStarted"), "success"); return load();
      }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
    });
  }

  function toggleAgent(agent) {
    var installationId = String(agent.installationId || "");
    if (!installationId) return;
    setBusy("agent:" + installationId);
    settingsFetch("/api/extensions/agent/" + encodeURIComponent(installationId) + "/enabled", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: agent.enabled === false }),
    }).then(readSettingsResponse).then(function () { notifyAgentCatalogChanged("enabled"); return load(); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  function removeAgent(agent) {
    var installationId = String(agent.installationId || "");
    var agentId = String(agent.agentId || "");
    workbenchServices.feedback().confirmModal({
      body: t("settings.agentRemoveConfirm", { name: agent.displayName || agentId || installationId }),
      confirmLabel: t("settings.uninstall"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      setBusy("agent:" + installationId);
      settingsFetch("/api/extensions/agent/" + encodeURIComponent(agentId || installationId), { method: "DELETE" })
        .then(readSettingsResponse).then(function () { notifyAgentCatalogChanged("removed"); tell(t("settings.saved"), "success"); return load(); })
        .catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
    });
  }

  function replaceAgentCard(updated) {
    setData(function (current) {
      var agents = (current.agents && typeof current.agents === "object") ? current.agents : {};
      var installed = Array.isArray(agents.installed) ? agents.installed : [];
      var nextInstalled = installed.map(function (agent) {
        return String(agent.installationId || "") === String(updated.installationId || "") ? updated : agent;
      });
      return {
        ...current,
        agents: { ...agents, installed: nextInstalled },
      };
    });
  }

  function searchRemote(append) {
    setRemoteLoading(true); setSkillSelection(null);
    var cursor = append ? remoteCursor : "";
    var url = "/api/extensions/search?kind=" + encodeURIComponent(installKind) + "&q=" + encodeURIComponent(remoteQuery) + "&advanced=" + (advanced ? "true" : "false") + "&cursor=" + encodeURIComponent(cursor);
    settingsFetch(url).then(readSettingsResponse).then(function (payload) {
      setRemoteResults(function (previous) { return append ? previous.concat(payload.results || []) : (payload.results || []); });
      setRemoteCursor(payload.next_cursor || ""); setRemoteLoading(false);
    }).catch(function (error) { setRemoteLoading(false); tell(error.message, "error"); });
  }

  function startInstall(item, request) {
    var summary = [extensionDisplayName(item, t), item.version || item.recommended_version || "", extensionSourceLabel(item, t)].filter(Boolean).join(" · ");
    var feedback = workbenchServices.feedback();
    feedback.confirmModal({
      title: t("settings.extensionInstallConfirmTitle"),
      body: t("settings.extensionInstallConfirmBody", { summary: summary }),
      confirmLabel: t("settings.install"),
    }).then(function (ok) {
      if (!ok) return;
      setBusy(item.kind + ":" + item.id);
      settingsFetch("/api/extensions/install", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: item.kind, extension_id: item.id, ...(request || {}) }),
      }).then(readSettingsResponse).then(function () {
        notifyAgentCatalogChanged("installed"); tell(t("settings.extensionInstallStarted"), "success"); return load();
      }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
    });
  }

  function installSearchResult(item) {
    if (item.kind === "skill") {
      setRemoteLoading(true);
      settingsFetch("/api/extensions/skills/inspect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: item.clone_url || item.repository }) })
        .then(readSettingsResponse).then(function (payload) {
          var candidates = payload.candidates || [];
          if (candidates.length === 1) {
            startInstall({ ...item, kind: "skill" }, { url: item.clone_url || item.repository, subdirs: [candidates[0].path] });
          } else {
            setSkillSelection({ item: item, candidates: candidates, selected: {} });
          }
        }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setRemoteLoading(false); });
      return;
    }
    if (item.kind === "mcp") {
      var remote = (item.installable_remotes || [])[0];
      if (remote) {
        startInstall(item, { version: item.version, remote: remote, source: { type: "mcp-registry", id: item.id, version: item.version } });
        return;
      }
      var packageSpec = (item.installable_packages || [])[0];
      if (!packageSpec) { tell(t("settings.extensionMcpLocalPackageHint"), "info"); return; }
      startInstall(item, { version: item.version, package: packageSpec, source: { type: "mcp-registry-package", id: item.id, version: item.version } });
      return;
    }
    startInstall(item, { version: requestedVersion.trim() || item.version || item.recommended_version, ref: item.ref || item.source, spec: item, ...(item.id === "tex" ? { distribution: texChoice } : {}) });
  }

  function configureManualMcp(item) {
    var fallback = ((item.fallback_request || {}).request || {});
    var config = fallback.config || {};
    var source = fallback.source || {};
    var packageSpec = (item.packages || [])[0] || {};
    setManualMcp({
      name: config.name || item.id || "", transport: config.transport || "stdio", url: config.url || "",
      command: config.command || "", args: (config.args || []).join("\n"),
      version: config.version || item.resolved_version || item.version || "", headers: "", env: "",
      packageIdentifier: source.identifier || packageSpec.identifier || "",
    });
    setManualMcpOpen(true);
    tell(t("settings.extensionManualPrefilled"), "info");
  }

  function removeItem(item) {
    workbenchServices.feedback().confirmModal({ body: t("settings.extensionRemoveConfirm", { name: extensionDisplayName(item, t) }), confirmLabel: item.kind === "mcp" ? t("settings.delete") : t("settings.uninstall"), danger: true }).then(function (ok) {
      if (!ok) return;
      setBusy(item.key);
      settingsFetch("/api/extensions/" + encodeURIComponent(item.kind) + "/" + encodeURIComponent(item.id), { method: "DELETE" })
        .then(readSettingsResponse).then(function () { tell(t("settings.saved"), "success"); return load(); })
        .catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
    });
  }

  function toggleItem(item) {
    setBusy(item.key);
    settingsFetch("/api/extensions/" + encodeURIComponent(item.kind) + "/" + encodeURIComponent(item.id) + "/enabled", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !item.enabled }) })
      .then(readSettingsResponse).then(load).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  function setDefault(item, version) {
    setBusy(item.key);
    settingsFetch("/api/extensions/toolchains/" + encodeURIComponent(item.id) + "/default", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: version }) })
      .then(readSettingsResponse).then(load).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  function installLocalFile() {
    var input = document.createElement("input"); input.type = "file"; input.accept = ".md,.txt,.zip,.json,.yaml,.yml,.prompt";
    input.onchange = function (event) {
      var file = event.target.files && event.target.files[0]; if (!file) return;
      var form = new FormData(); form.append("file", file); setRemoteLoading(true);
      settingsFetch("/api/extensions/skills/install-upload", { method: "POST", body: form }).then(readSettingsResponse).then(function () { setInstallOpen(false); return load(); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setRemoteLoading(false); });
    };
    input.click();
  }

  function installLocalFolder() {
    if (window.cyrene && typeof window.cyrene.pickExtensionPath === "function") {
      setRemoteLoading(true);
      window.cyrene.pickExtensionPath({ directory: true, title: t("settings.installFolder") }).then(function (picked) {
        if (!picked || picked.cancelled || !picked.path) return null;
        return settingsFetch("/api/extensions/skills/install", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: picked.path }) }).then(readSettingsResponse).then(function () { setInstallOpen(false); return load(); });
      }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setRemoteLoading(false); });
      return;
    }
    setRemoteLoading(true);
    settingsFetch("/api/extensions/skills/install-picker", { method: "POST" }).then(readSettingsResponse).then(function (payload) { if (!payload.cancelled) { setInstallOpen(false); return load(); } }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setRemoteLoading(false); });
  }

  function addManualMcp() {
    var config = { name: manualMcp.name.trim(), transport: manualMcp.transport, enabled: true, version: manualMcp.version.trim() };
    if (manualMcp.transport !== "stdio") config.url = manualMcp.url.trim();
    else { config.command = manualMcp.command.trim(); config.args = manualMcp.args.split(/\r?\n/).map(function (value) { return value.trim(); }).filter(Boolean); }
    function parseVariables(value, label) {
      var variables = {};
      String(value || "").split(/\r?\n/).forEach(function (line) {
        if (!line.trim()) return;
        var separator = line.indexOf("=");
        if (separator <= 0) throw new Error(t("settings.extensionVariableInvalid", { label: label }));
        variables[line.slice(0, separator).trim()] = line.slice(separator + 1);
      });
      return variables;
    }
    try {
      if (config.transport !== "stdio") config.headers = parseVariables(manualMcp.headers, t("settings.extensionHeaders"));
      else config.env = parseVariables(manualMcp.env, t("settings.extensionEnvironment"));
    } catch (error) { tell(error.message, "error"); return; }
    if (!config.name || (config.transport !== "stdio" ? !config.url : !config.command || !config.version)) { tell(t("settings.extensionMcpRequired"), "error"); return; }
    startInstall({ id: config.name, name: config.name, kind: "mcp", source: "manual", version: config.version }, { config: config, version: config.version, source: { type: "manual" } });
  }

  function openSources() {
    Promise.all([
      settingsFetch("/api/extensions/sources").then(readSettingsResponse),
      settingsFetch("/api/extensions/audit?limit=50").then(readSettingsResponse),
    ]).then(function (payloads) { setSources(payloads[0]); setAuditRecords(payloads[1].records || []); setSourceHealth(null); setSourceOpen(true); }).catch(function (error) { tell(error.message, "error"); });
  }

  function bindSystem(item) {
    if (window.cyrene && typeof window.cyrene.pickExtensionPath === "function") {
      window.cyrene.pickExtensionPath({ directory: false, title: t("settings.extensionBindTitle") }).then(function (picked) {
        if (!picked || picked.cancelled || !picked.path) return;
        setBindItem(item); setBindPath(picked.path);
      });
      return;
    }
    setBindItem(item); setBindPath(item.manual_binding ? (item.path || "") : "");
  }
  function saveBinding() {
    if (!bindItem || !bindPath.trim()) return;
    setBusy(bindItem.key);
    settingsFetch("/api/extensions/bind", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extension_id: bindItem.id, path: bindPath.trim() }) })
      .then(readSettingsResponse).then(function () { setBindItem(null); tell(t("settings.saved"), "success"); return load(); })
      .catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function unbindSystem(item) {
    setBusy(item.key);
    settingsFetch("/api/extensions/unbind", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extension_id: item.id }) })
      .then(readSettingsResponse).then(load).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  function saveSources() {
    setBusy("sources");
    settingsFetch("/api/extensions/sources", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sources) }).then(readSettingsResponse).then(function (payload) { setSources(payload); tell(t("settings.saved"), "success"); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  function testSources() {
    setSourceTesting(true); setSourceHealth(null);
    settingsFetch("/api/extensions/sources/test", { method: "POST" }).then(readSettingsResponse).then(setSourceHealth).catch(function (error) { tell(error.message, "error"); }).finally(function () { setSourceTesting(false); });
  }

  function loadHooks(open) {
    return Promise.all([
      settingsFetch("/api/hooks").then(readSettingsResponse),
      settingsFetch("/api/hooks/audit/records?limit=100").then(readSettingsResponse),
    ]).then(function (payloads) {
      setHooksData(payloads[0]); setHookAudit(payloads[1].records || []);
      if (open) setHooksOpen(true);
    }).catch(function (error) { tell(error.message, "error"); });
  }
  function editHook(item) {
    var value = item || emptyHookDraft;
    setHookDraft({ ...emptyHookDraft, ...value, runner: { ...emptyHookDraft.runner, ...(value.runner || {}) } });
    setHookArgsText(((value.runner || {}).args || []).join("\n"));
    setHookEditorOpen(true);
  }
  function saveHook() {
    var payload = { ...hookDraft, runner: { ...hookDraft.runner, args: hookArgsText.split("\n").map(function (value) { return value.trim(); }).filter(Boolean) } };
    delete payload.runner.env;
    var url = hookDraft.id ? "/api/hooks/" + encodeURIComponent(hookDraft.id) : "/api/hooks";
    setBusy("hook-save");
    settingsFetch(url, { method: hookDraft.id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
      .then(readSettingsResponse).then(function () { setHookDraft(emptyHookDraft); setHookArgsText(""); setHookEditorOpen(false); tell(t("settings.hookSaved"), "success"); return loadHooks(false); })
      .catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function toggleHook(item) {
    setBusy("hook:" + item.id);
    settingsFetch("/api/hooks/" + encodeURIComponent(item.id) + "/enabled", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: item.enabled !== true }) })
      .then(readSettingsResponse).then(function () { return loadHooks(false); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function deleteHook(item) {
    setBusy("hook:" + item.id);
    settingsFetch("/api/hooks/" + encodeURIComponent(item.id), { method: "DELETE" }).then(readSettingsResponse).then(function () { return loadHooks(false); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function testHook(item) {
    setBusy("hook:" + item.id);
    settingsFetch("/api/hooks/" + encodeURIComponent(item.id) + "/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(readSettingsResponse).then(function () { tell(t("settings.hookTestSucceeded"), "success"); return loadHooks(false); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function decideHookProposal(item, approve) {
    setBusy("proposal:" + item.id);
    settingsFetch("/api/hooks/proposals/" + encodeURIComponent(item.id) + "/decision", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve: approve }) }).then(readSettingsResponse).then(function () { tell(t(approve ? "settings.hookProposalApproved" : "settings.hookProposalRejected"), "success"); return loadHooks(false); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }
  function configureExtensionHook(item) {
    setBusy(item.key);
    settingsFetch("/api/hooks/extensions/cli/" + encodeURIComponent(item.id) + "/configure", { method: "POST" }).then(readSettingsResponse).then(function () { tell(t("settings.extensionHookStarted"), "success"); }).catch(function (error) { tell(error.message, "error"); }).finally(function () { setBusy(""); });
  }

  var categories = ["recommended", "skills", "mcp", "cli", "toolchains", "agent"];
  var items = data[category] || [];
  var q = query.trim().toLowerCase();
  var filtered = items.filter(function (item) { return !q || [extensionDisplayName(item, t), item.id, extensionDisplayDescription(item, t), item.version].join(" ").toLowerCase().indexOf(q) >= 0; });
  var activeTasks = (data.tasks || []).filter(function (task) { return extensionTaskIsVisible(task, taskClock); }).slice(0, 4);
  var installCategory = category === "recommended" ? "toolchain" : category === "skills" ? "skill" : category === "toolchains" ? "toolchain" : category;
  var agentListing = (data.agents && typeof data.agents === "object") ? data.agents : { recommended: [], installed: [] };
  var agentRecommended = Array.isArray(agentListing.recommended) ? agentListing.recommended : [];
  var agentInstalled = Array.isArray(agentListing.installed) ? agentListing.installed : [];

  return React.createElement("div", { className: "wb-extensions-page", id: "setting-extensions" },
    React.createElement("header", { className: "wb-extensions-header" },
      SectionTitle(t("settings.extensionCenter"), t("settings.extensionsSubtitle")),
      React.createElement("div", { className: "wb-extension-header-actions" },
        React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { loadHooks(true); } }, t("settings.agentHooks")),
        React.createElement("button", { type: "button", className: "wb-btn", onClick: openSources }, t("settings.extensionSources")),
        category !== "recommended" && category !== "agent" && React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button wb-extension-tab-install-button", onClick: function () { openInstaller(installCategory); } }, t("settings.extensionInstallAction." + installCategory)),
      )
    ),
    data.python_prompt_required && React.createElement("button", { type: "button", className: "wb-extension-python-callout", onClick: function () { setCategory("recommended"); setQuery("Python"); } },
      React.createElement("strong", null, t("settings.extensionPythonMissingTitle")),
      React.createElement("span", null, t("settings.extensionPythonMissingBody")),
      React.createElement("span", { className: "wb-extension-callout-action" }, t("settings.extensionViewInstall"))
    ),
    React.createElement("div", { className: "wb-extension-tabs", role: "tablist", "aria-label": t("settings.extensionCenter") },
      categories.map(function (id) { return React.createElement("button", { key: id, type: "button", role: "tab", "aria-selected": category === id ? "true" : "false", className: category === id ? "active" : "", onClick: function () { setCategory(id); setQuery(""); } }, t("settings.extensionTab." + id)); })
    ),
    category !== "agent" && React.createElement("div", { className: "wb-extension-filter" },
      React.createElement("input", { className: "wb-input", value: query, onChange: function (event) { setQuery(event.target.value); }, placeholder: t("settings.extensionFilter") , "aria-label": t("settings.extensionFilter") }),
      React.createElement("span", null, t("settings.extensionCount", { n: filtered.length }))
    ),
    notice && React.createElement("div", { className: "wb-extension-notice " + noticeKind, role: noticeKind === "error" ? "alert" : "status" }, notice),
    activeTasks.length > 0 && React.createElement("section", { className: "wb-extension-tasks", "aria-label": t("settings.extensionTasks") },
      React.createElement("h3", null, t("settings.extensionTasks")),
      activeTasks.map(function (task) {
        var progress = Math.max(0, Math.min(100, Number(task.progress) || 0));
        var errorContent = task.status === "failed" ? extensionTaskErrorContent(task, t) : null;
        return React.createElement("article", { key: task.id, className: "wb-extension-task " + task.status },
          React.createElement("div", { className: "wb-extension-task-head" },
            React.createElement("strong", null, extensionDisplayName({ id: task.extension_id, name: task.extension_id }, t)),
            React.createElement("span", { className: "wb-extension-task-status" }, extensionTaskStatusLabel(task.status, t))
          ),
          React.createElement("div", { className: "wb-extension-task-progress-row" },
            React.createElement("div", { className: "wb-extension-task-progress", role: "progressbar", "aria-label": t("settings.extensionTaskProgress", { name: task.extension_id }), "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": progress }, React.createElement("span", { style: { width: progress + "%" } })),
            React.createElement("span", { className: "wb-extension-task-percent", "aria-hidden": "true" }, progress + "%")
          ),
          errorContent && React.createElement("div", { className: "wb-extension-task-error", role: "alert" },
            React.createElement("strong", null, errorContent.title),
            React.createElement("p", null, errorContent.hint),
            task.error && React.createElement("details", null,
              React.createElement("summary", null, t("settings.extensionTaskTechnicalDetails")),
              React.createElement("pre", null, task.error)
            )
          ),
          ["queued", "running"].indexOf(task.status) >= 0 && React.createElement("div", { className: "wb-extension-task-actions" }, React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { settingsFetch("/api/extensions/tasks/" + task.id + "/cancel", { method: "POST" }).then(load); } }, t("settings.cancel")))
        );
      })
    ),
    category === "agent"
      ? React.createElement(AgentTabPanel, {
          t: t,
          recommended: agentRecommended,
          installed: agentInstalled,
          busy: busy,
          expandedId: agentExpandedId,
          onToggleExpand: function (installationId) { setAgentExpandedId(installationId); },
          onInstall: startAgentInstall,
          onToggle: toggleAgent,
          onRemove: removeAgent,
          onChanged: replaceAgentCard,
          onOpenInstalled: function (installationId) {
            setAgentExpandedId(installationId);
            var panel = document.querySelector(".wb-extensions-page");
            if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
          },
          onOpenProposal: function () { setAgentProposalOpen(true); },
        })
      : React.createElement("div", { className: "wb-extension-list", "aria-busy": loading ? "true" : "false" },
          loading && React.createElement("div", { className: "wb-extensions-empty" }, t("settings.loading")),
          !loading && filtered.length === 0 && React.createElement("div", { className: "wb-extensions-empty" }, t("settings.extensionEmpty")),
          filtered.map(function (item) { return React.createElement(ExtensionCard, { key: item.key, item: item, t: t, busy: busy === item.key, onInstall: function (target) { if (category === "recommended" && target.id !== "tex") startInstall(target, {}); else { openInstaller(target.kind); if (target.id === "tex") { setRemoteQuery("TeX"); setRemoteResults([target]); } } }, onRemove: removeItem, onToggle: toggleItem, onDefault: setDefault, onBind: bindSystem, onUnbind: unbindSystem, onConfigureHook: configureExtensionHook }); })
        ),
    agentProposalOpen && React.createElement(AgentInstallProposalModal, { t: t, onUpdated: function () { notifyAgentCatalogChanged("installed"); return load(); }, onClose: function () { setAgentProposalOpen(false); } }),
    hooksOpen && React.createElement("div", { className: "wb-extension-modal-scrim", onMouseDown: function (event) { if (event.target === event.currentTarget) setHooksOpen(false); } },
      React.createElement("section", { className: "wb-extension-modal wb-hooks-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "agent-hooks-title" },
        React.createElement("header", null, React.createElement("div", null, React.createElement("h3", { id: "agent-hooks-title" }, t("settings.agentHooks")), React.createElement("p", null, t("settings.agentHooksSubtitle"))), React.createElement("button", { type: "button", className: "wb-extension-close", onClick: function () { setHooksOpen(false); }, "aria-label": t("settings.close") }, "×")),
        (hooksData.proposals || []).filter(function (item) { return item.status === "pending"; }).length > 0 && React.createElement("section", { className: "wb-hook-proposals" },
          React.createElement("h4", null, t("settings.hookPendingApprovals")),
          (hooksData.proposals || []).filter(function (item) { return item.status === "pending"; }).map(function (item) { var proposalHook = item.hook || {}; var proposalRunner = proposalHook.runner || {}; var proposalTarget = proposalRunner.executable || proposalRunner.path || ""; var proposalCommand = [proposalTarget].concat(proposalRunner.args || []).join(" "); return React.createElement("article", { key: item.id, className: "wb-hook-proposal" }, React.createElement("div", null, React.createElement("strong", null, (item.extension || {}).name || (item.extension || {}).id || proposalHook.name), React.createElement("small", null, item.rationale), React.createElement("code", null, proposalHook.event + " · " + (proposalHook.matcher || "*")), React.createElement("code", { title: proposalCommand }, proposalCommand)), React.createElement("div", null, React.createElement("button", { type: "button", className: "wb-btn", disabled: busy === "proposal:" + item.id, onClick: function () { decideHookProposal(item, false); } }, t("settings.reject")), React.createElement("button", { type: "button", className: "wb-btn primary", disabled: busy === "proposal:" + item.id, onClick: function () { decideHookProposal(item, true); } }, t("settings.approve")))); })
        ),
        React.createElement("section", { className: "wb-hook-list" },
          React.createElement("div", { className: "wb-hook-section-head" }, React.createElement("h4", null, t("settings.configuredHooks")), React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { editHook(null); } }, t("settings.addHook"))),
          (hooksData.hooks || []).length === 0 ? React.createElement("div", { className: "wb-hook-empty" }, React.createElement("p", null, t("settings.hookEmpty"))) : (hooksData.hooks || []).map(function (item) { return React.createElement("article", { key: item.id, className: "wb-hook-row" }, React.createElement("div", null, React.createElement("strong", null, item.name), React.createElement("small", null, item.event + (item.matcher && item.matcher !== "*" ? " · " + item.matcher : "")), React.createElement("code", null, (item.runner || {}).executable || (item.runner || {}).path)), React.createElement("div", null, Toggle(item.enabled === true, function () { toggleHook(item); }, busy === "hook:" + item.id, item.name), React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { testHook(item); } }, t("settings.test")), React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { editHook(item); } }, t("settings.edit")), React.createElement("button", { type: "button", className: "wb-btn danger", onClick: function () { deleteHook(item); } }, t("settings.delete")))); })
        ),
        hookEditorOpen && React.createElement("section", { className: "wb-hook-editor" },
          React.createElement("div", { className: "wb-hook-editor-head" },
            React.createElement("div", null, React.createElement("h4", null, hookDraft.id ? t("settings.editHook") : t("settings.addHook")), React.createElement("small", null, t("settings.hookEditorHint"))),
            React.createElement("button", { type: "button", className: "wb-extension-close wb-hook-editor-close", onClick: function () { setHookEditorOpen(false); }, "aria-label": t("settings.close") }, "×")
          ),
          React.createElement("div", { className: "wb-extension-form-grid wb-hook-core-fields" },
            React.createElement("label", null, React.createElement("span", null, t("settings.name")), React.createElement("input", { className: "wb-input", value: hookDraft.name, onChange: function (event) { setHookDraft({ ...hookDraft, name: event.target.value }); } })),
            React.createElement("label", null, React.createElement("span", null, t("settings.hookEvent")), React.createElement("select", { className: "wb-select", value: hookDraft.event, onChange: function (event) { setHookDraft({ ...hookDraft, event: event.target.value, failure_policy: event.target.value === "PreToolUse" ? hookDraft.failure_policy : "open" }); } }, ["PreToolUse", "PostToolUse", "SessionStart", "SessionEnd", "Stop"].map(function (value) { return React.createElement("option", { key: value, value: value }, value); }))),
            React.createElement("label", null, React.createElement("span", null, t("settings.hookRunnerType")), React.createElement("select", { className: "wb-select", value: hookDraft.runner.type, onChange: function (event) { var type = event.target.value; setHookDraft({ ...hookDraft, runner: type === "script" ? { type: "script", path: "", args: hookDraft.runner.args || [], env: hookDraft.runner.env || {} } : { type: "command", executable: "", args: hookDraft.runner.args || [], env: hookDraft.runner.env || {} } }); } }, React.createElement("option", { value: "command" }, t("settings.hookRunnerCommand")), React.createElement("option", { value: "script" }, t("settings.hookRunnerScript")))),
            ["PreToolUse", "PostToolUse"].indexOf(hookDraft.event) >= 0 && React.createElement("label", null, React.createElement("span", null, t("settings.hookMatcher")), React.createElement("input", { className: "wb-input mono", value: hookDraft.matcher, onChange: function (event) { setHookDraft({ ...hookDraft, matcher: event.target.value }); } })),
            React.createElement("label", { className: "wide" }, React.createElement("span", null, hookDraft.runner.type === "script" ? t("settings.hookScriptPath") : t("settings.hookExecutable")), React.createElement("input", { className: "wb-input mono", value: hookDraft.runner.executable || hookDraft.runner.path || "", onChange: function (event) { var runner = { ...hookDraft.runner }; if (runner.type === "script") runner.path = event.target.value; else runner.executable = event.target.value; setHookDraft({ ...hookDraft, runner: runner }); } })),
          ),
          React.createElement("details", { className: "wb-hook-advanced" },
            React.createElement("summary", null, React.createElement("span", null, t("settings.hookAdvanced")), React.createElement("small", null, t("settings.hookAdvancedHint"))),
            React.createElement("div", { className: "wb-extension-form-grid" },
              React.createElement("label", null, React.createElement("span", null, t("settings.hookPriority")), React.createElement("input", { className: "wb-input mono", type: "number", value: hookDraft.priority, onChange: function (event) { setHookDraft({ ...hookDraft, priority: Number(event.target.value) }); } })),
              React.createElement("label", null, React.createElement("span", null, t("settings.hookTimeout")), React.createElement("input", { className: "wb-input mono", type: "number", min: "0.1", max: "60", step: "0.1", value: hookDraft.timeout_seconds, onChange: function (event) { setHookDraft({ ...hookDraft, timeout_seconds: Number(event.target.value) }); } })),
              hookDraft.event === "PreToolUse" && React.createElement("label", null, React.createElement("span", null, t("settings.hookFailurePolicy")), React.createElement("select", { className: "wb-select", value: hookDraft.failure_policy, onChange: function (event) { setHookDraft({ ...hookDraft, failure_policy: event.target.value }); } }, React.createElement("option", { value: "open" }, t("settings.hookFailureOpen")), React.createElement("option", { value: "block" }, t("settings.hookFailureBlock")))),
              React.createElement("label", { className: hookDraft.event === "PreToolUse" ? "" : "wide" }, React.createElement("span", null, t("settings.description")), React.createElement("input", { className: "wb-input", value: hookDraft.description, onChange: function (event) { setHookDraft({ ...hookDraft, description: event.target.value }); } })),
              React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.hookArguments")), React.createElement("textarea", { className: "wb-input mono", rows: 2, value: hookArgsText, onChange: function (event) { setHookArgsText(event.target.value); } }), React.createElement("small", null, t("settings.hookArgumentsHint")))
            )
          ),
          React.createElement("div", { className: "wb-hook-editor-actions" }, React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { editHook(null); } }, t("settings.reset")), React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { setHookEditorOpen(false); } }, t("settings.cancel")), React.createElement("button", { type: "button", className: "wb-btn primary", disabled: busy === "hook-save" || !hookDraft.name || !(hookDraft.runner.executable || hookDraft.runner.path), onClick: saveHook }, t("settings.save")))
        ),
        React.createElement("details", { className: "wb-extension-audit" },
          React.createElement("summary", null, t("settings.hookExecutionLog")),
          hookAudit.length === 0
            ? React.createElement("p", null, t("settings.hookAuditEmpty"))
            : hookAudit.map(function (record, index) {
                return React.createElement("div", { key: record.timestamp + index },
                  React.createElement("strong", null, (record.hook_name || record.hook_id || record.action) + " · " + (record.event || record.action || "")),
                  React.createElement("small", null, new Date(record.timestamp).toLocaleString() + " · " + (record.status || record.result || ""))
                );
              })
        )
      )
    ),
    installOpen && React.createElement("div", { className: "wb-extension-modal-scrim", onMouseDown: function (event) { if (event.target === event.currentTarget) setInstallOpen(false); } },
      React.createElement("section", { className: "wb-extension-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "extension-install-title" },
        React.createElement("header", null,
          React.createElement("div", null, React.createElement("h3", { id: "extension-install-title" }, t("settings.extensionInstallTitle." + installKind)), React.createElement("p", null, t("settings.extensionInstallSubtitle." + installKind))),
          React.createElement("button", { type: "button", className: "wb-extension-close", onClick: function () { setInstallOpen(false); }, "aria-label": t("settings.close") }, "×")
        ),
        installKind === "skill" && React.createElement("div", { className: "wb-extension-local-actions" },
          React.createElement("button", { type: "button", className: "wb-btn wb-extension-install-button", onClick: installLocalFile }, t("settings.installFile")),
          React.createElement("button", { type: "button", className: "wb-btn wb-extension-install-button", onClick: installLocalFolder }, t("settings.installFolder"))
        ),
        installKind === "mcp" && React.createElement("details", { className: "wb-extension-manual", open: manualMcpOpen, onToggle: function (event) { setManualMcpOpen(event.currentTarget.open); } },
          React.createElement("summary", null, t("settings.extensionManualMcp")),
          React.createElement("div", { className: "wb-extension-form-grid" },
            React.createElement("label", null, React.createElement("span", null, t("settings.name")), React.createElement("input", { className: "wb-input", value: manualMcp.name, onChange: function (e) { setManualMcp({ ...manualMcp, name: e.target.value }); } })),
            React.createElement("label", null, React.createElement("span", null, t("settings.extensionTransport")), React.createElement("select", { className: "wb-select", value: manualMcp.transport, onChange: function (e) { setManualMcp({ ...manualMcp, transport: e.target.value }); } }, React.createElement("option", { value: "streamable_http" }, "Streamable HTTP"), React.createElement("option", { value: "sse" }, "SSE"), React.createElement("option", { value: "stdio" }, "stdio"))),
            manualMcp.transport !== "stdio"
              ? React.createElement(React.Fragment, null,
                  React.createElement("label", { className: "wide" }, React.createElement("span", null, "URL"), React.createElement("input", { className: "wb-input mono", value: manualMcp.url, onChange: function (e) { setManualMcp({ ...manualMcp, url: e.target.value }); } })),
                  React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.extensionHeaders")), React.createElement("textarea", { className: "wb-input mono", rows: 3, value: manualMcp.headers, placeholder: "Authorization=Bearer …", onChange: function (e) { setManualMcp({ ...manualMcp, headers: e.target.value }); } }), React.createElement("small", null, t("settings.extensionSecretStored")))
                )
              : React.createElement(React.Fragment, null,
                  React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.placeholderCommand")), React.createElement("input", { className: "wb-input mono", value: manualMcp.command, onChange: function (e) { setManualMcp({ ...manualMcp, command: e.target.value }); } })),
                  React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.placeholderArgs")), React.createElement("textarea", { className: "wb-input mono", rows: 3, value: manualMcp.args, placeholder: t("settings.extensionArgumentsHint"), onChange: function (e) { setManualMcp({ ...manualMcp, args: e.target.value }); } }), React.createElement("small", null, t("settings.extensionArgumentsHint"))),
                  React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.extensionEnvironment")), React.createElement("textarea", { className: "wb-input mono", rows: 3, value: manualMcp.env, placeholder: "API_KEY=…", onChange: function (e) { setManualMcp({ ...manualMcp, env: e.target.value }); } }), React.createElement("small", null, t("settings.extensionSecretStored")))
                ),
            React.createElement("label", null, React.createElement("span", null, t("settings.extensionVersion")), React.createElement("input", { className: "wb-input mono", value: manualMcp.version, onChange: function (e) { setManualMcp({ ...manualMcp, version: e.target.value }); } })),
            React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button", onClick: addManualMcp }, t("settings.add"))
          )
        ),
        React.createElement("div", { className: "wb-extension-remote-search" },
          React.createElement("input", { className: "wb-input", value: remoteQuery, onChange: function (e) { setRemoteQuery(e.target.value); setRemoteCursor(""); }, onKeyDown: function (e) { if (e.key === "Enter") searchRemote(false); }, placeholder: t("settings.extensionSearchPlaceholder." + installKind), "aria-label": t("settings.extensionRemoteSearch") }),
          React.createElement("button", { type: "button", className: "wb-btn primary", disabled: remoteLoading, onClick: function () { searchRemote(false); } }, remoteLoading ? t("settings.loading") : t("settings.search"))
        ),
        (installKind === "cli" || installKind === "toolchain") && React.createElement("label", { className: "wb-extension-requested-version" }, React.createElement("span", null, t("settings.extensionRequestedVersion")), React.createElement("input", { className: "wb-input mono", value: requestedVersion, onChange: function (event) { setRequestedVersion(event.target.value); }, placeholder: "latest / lts / 22.14.0" })),
        installKind === "cli" && React.createElement("label", { className: "wb-extension-advanced-toggle" }, React.createElement("input", { type: "checkbox", checked: advanced, onChange: function (e) { setAdvanced(e.target.checked); } }), React.createElement("span", null, t("settings.extensionAdvancedSources"))),
        installKind === "toolchain" && remoteResults.some(function (item) { return item.id === "tex"; }) && React.createElement("fieldset", { className: "wb-extension-tex-choice" },
          React.createElement("legend", null, t("settings.extensionTeXChoice")),
          [["tinytex", "settings.extensionTinyTeX", "settings.extensionTinyTeXHint"], ["texlive-full", "settings.extensionFullTeX", "settings.extensionFullTeXHint"]].map(function (entry) { return React.createElement("label", { key: entry[0] }, React.createElement("input", { type: "radio", name: "tex-distribution", checked: texChoice === entry[0], onChange: function () { setTexChoice(entry[0]); } }), React.createElement("span", null, React.createElement("strong", null, t(entry[1])), React.createElement("small", null, t(entry[2])))); })
        ),
        skillSelection && React.createElement("div", { className: "wb-extension-skill-selection" },
          React.createElement("strong", null, t("settings.extensionSelectSkills")),
          skillSelection.candidates.map(function (candidate) { return React.createElement("label", { key: candidate.path }, React.createElement("input", { type: "checkbox", checked: !!skillSelection.selected[candidate.path], onChange: function (e) { setSkillSelection({ ...skillSelection, selected: { ...skillSelection.selected, [candidate.path]: e.target.checked } }); } }), React.createElement("span", null, React.createElement("b", null, candidate.name), React.createElement("small", null, candidate.description), React.createElement("code", null, candidate.path || "."))); }),
          React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button", onClick: function () { var selected = Object.keys(skillSelection.selected).filter(function (key) { return skillSelection.selected[key]; }); if (selected.length) startInstall({ ...skillSelection.item, kind: "skill" }, { url: skillSelection.item.clone_url || skillSelection.item.repository, subdirs: selected }); } }, t("settings.extensionInstallSelected"))
        ),
        React.createElement("div", { className: "wb-extension-search-results" },
          remoteResults.map(function (item) { var displayName = extensionDisplayName(item, t); return React.createElement("div", { key: item.id + String(item.source), className: "wb-extension-result" },
            React.createElement("span", { className: "wb-extension-glyph " + item.kind + " extension-" + String(item.id || "").replace(/[^a-z0-9_-]/gi, "-").toLowerCase() }, React.createElement(ExtensionGlyph, { id: item.id, kind: item.kind, label: displayName })),
            React.createElement("div", null, React.createElement("strong", null, displayName), React.createElement("p", null, extensionDisplayDescription(item, t)), React.createElement("small", null, [item.version, item.backend, item.publisher, item.verified ? t("settings.extensionVerified") : t("settings.extensionUnverified")].filter(Boolean).join(" · "))),
            React.createElement("button", { type: "button", className: "wb-btn primary wb-extension-install-button", disabled: remoteLoading, title: item.installable === false ? t("settings.extensionNeedsConfiguration") : "", onClick: function () { if (item.installable === false) configureManualMcp(item); else installSearchResult(item); } }, item.installable === false ? t("settings.extensionConfigureManually") : t("settings.install"))
          ); })
        ),
        remoteCursor && React.createElement("button", { type: "button", className: "wb-btn wb-extension-load-more", disabled: remoteLoading, onClick: function () { searchRemote(true); } }, remoteLoading ? t("settings.loading") : t("settings.extensionLoadMore"))
      )
    ),
    sourceOpen && React.createElement("div", { className: "wb-extension-modal-scrim", onMouseDown: function (event) { if (event.target === event.currentTarget) setSourceOpen(false); } },
      React.createElement("section", { className: "wb-extension-modal wb-extension-source-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "extension-source-title" },
        React.createElement("header", null, React.createElement("div", null, React.createElement("h3", { id: "extension-source-title" }, t("settings.extensionSources")), React.createElement("p", null, t("settings.extensionSourcesSubtitle"))), React.createElement("button", { type: "button", className: "wb-extension-close", onClick: function () { setSourceOpen(false); }, "aria-label": t("settings.close") }, "×")),
        React.createElement("div", { className: "wb-extension-source-sections" },
          React.createElement("section", { className: "wb-extension-source-section" },
            React.createElement("div", { className: "wb-extension-source-section-head" }, React.createElement("div", null, React.createElement("h4", null, t("settings.extensionSourceSectionNetwork")), React.createElement("p", null, t("settings.extensionSourceSectionNetworkHint")))),
            React.createElement("div", { className: "wb-extension-source-form" },
              React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.extensionNetworkMode")), React.createElement("select", { className: "wb-select", value: sources.network_mode || "auto", onChange: function (event) { setSources({ ...sources, network_mode: event.target.value }); } }, React.createElement("option", { value: "auto" }, t("settings.extensionNetworkAuto")), React.createElement("option", { value: "direct" }, t("settings.extensionNetworkDirect")), React.createElement("option", { value: "china" }, t("settings.extensionNetworkChina")))),
              [["github_mirror", "settings.extensionGithubMirror", "settings.extensionGithubMirrorPlaceholder"], ["npm_registry", "settings.extensionNpmRegistry", "settings.extensionNpmRegistryPlaceholder"], ["pip_index_url", "settings.extensionPipIndex", "settings.extensionPipIndexPlaceholder"]].map(function (entry) { return React.createElement("label", { key: entry[0] }, React.createElement("span", null, t(entry[1])), React.createElement("input", { className: "wb-input mono", type: "url", value: sources[entry[0]] || "", placeholder: t(entry[2]), onChange: function (event) { setSources({ ...sources, [entry[0]]: event.target.value }); } })); }),
              React.createElement("div", { className: "wb-extension-source-toggle wide" }, React.createElement("div", null, React.createElement("strong", null, t("settings.extensionAutoMirror")), React.createElement("small", null, t("settings.extensionAutoMirrorHint"))), Toggle(sources.auto_mirror !== false, function () { setSources({ ...sources, auto_mirror: sources.auto_mirror === false }); }, false, t("settings.extensionAutoMirror")))
            )
          ),
          React.createElement("section", { className: "wb-extension-source-section" },
            React.createElement("div", { className: "wb-extension-source-section-head" }, React.createElement("div", null, React.createElement("h4", null, t("settings.extensionSourceSectionCatalogs")), React.createElement("p", null, t("settings.extensionSourceSectionCatalogsHint")))),
            React.createElement("div", { className: "wb-extension-source-form" },
              React.createElement("label", null, React.createElement("span", null, t("settings.extensionMcpRegistry")), React.createElement("input", { className: "wb-input mono", type: "url", value: sources.mcp_registry_url || "", placeholder: "https://registry.modelcontextprotocol.io", onChange: function (event) { setSources({ ...sources, mcp_registry_url: event.target.value }); } }), React.createElement("small", null, t("settings.extensionMcpRegistryHint"))),
              React.createElement("label", null, React.createElement("span", null, t("settings.extensionSkillCatalog")), React.createElement("input", { className: "wb-input mono", type: "url", value: sources.skill_catalog_url || "", placeholder: t("settings.extensionSkillCatalogPlaceholder"), onChange: function (event) { setSources({ ...sources, skill_catalog_url: event.target.value }); } }), React.createElement("small", null, t("settings.extensionSkillCatalogHint")))
            )
          ),
          React.createElement("section", { className: "wb-extension-source-section" },
            React.createElement("div", { className: "wb-extension-source-section-head" }, React.createElement("div", null, React.createElement("h4", null, t("settings.extensionSourceSectionSecurity")), React.createElement("p", null, t("settings.extensionSourceSectionSecurityHint")))),
            React.createElement("div", { className: "wb-extension-source-form" },
              React.createElement("label", { className: "wide" }, React.createElement("span", null, t("settings.extensionGithubToken")), React.createElement("div", { className: "wb-extension-token-row" }, React.createElement("input", { className: "wb-input mono", type: "password", value: sources.github_token || "", placeholder: sources.github_token_configured ? t("settings.secretConfigured") : "ghp_…", onChange: function (event) { setSources({ ...sources, github_token: event.target.value }); } }), sources.github_token_configured && React.createElement("button", { type: "button", className: "wb-btn danger", onClick: function () { setSources({ ...sources, github_token: "", clear_github_token: true, github_token_configured: false }); } }, t("settings.clearStoredKey"))), React.createElement("small", null, t("settings.extensionGithubTokenHint"))),
              React.createElement("div", { className: "wb-extension-source-toggle wide" }, React.createElement("div", null, React.createElement("strong", null, t("settings.extensionVerifySignatures")), React.createElement("small", null, t("settings.extensionVerifySignaturesHint"))), Toggle(sources.verify_signatures !== false, function () { setSources({ ...sources, verify_signatures: sources.verify_signatures === false }); }, false, t("settings.extensionVerifySignatures")))
            )
          )
        ),
        sourceHealth && React.createElement("div", { className: "wb-extension-source-health" }, Object.keys(sourceHealth.checks || {}).map(function (key) { var item = sourceHealth.checks[key]; return React.createElement("span", { key: key, className: item.ok ? "ok" : "error" }, t("settings.extensionSourceCheck." + key, key) + " · " + (item.ok ? t("settings.extensionReachable") : t("settings.extensionUnreachable"))); })),
        React.createElement("details", { className: "wb-extension-audit" }, React.createElement("summary", null, t("settings.extensionAudit")),
          auditRecords.length === 0 ? React.createElement("p", null, t("settings.extensionAuditEmpty")) : auditRecords.map(function (record, index) { return React.createElement("div", { key: record.at + index }, React.createElement("strong", null, extensionAuditLabel("Action", record.action, t) + " · " + record.target), React.createElement("small", null, new Date(record.at).toLocaleString() + " · " + extensionAuditLabel("Actor", record.actor, t) + " · " + extensionAuditLabel("Result", record.result, t))); })
        ),
        React.createElement("footer", null, React.createElement("button", { type: "button", className: "wb-btn", disabled: sourceTesting, onClick: testSources }, sourceTesting ? t("settings.testingConnection") : t("settings.testConnection")), React.createElement("button", { type: "button", className: "wb-btn primary", disabled: busy === "sources", onClick: saveSources }, t("settings.save")))
      )
    ),
    bindItem && React.createElement("div", { className: "wb-extension-modal-scrim", onMouseDown: function (event) { if (event.target === event.currentTarget) setBindItem(null); } },
      React.createElement("section", { className: "wb-extension-modal wb-extension-bind-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "extension-bind-title" },
        React.createElement("header", null, React.createElement("div", null, React.createElement("h3", { id: "extension-bind-title" }, t("settings.extensionBindTitle")), React.createElement("p", null, t("settings.extensionBindHint"))), React.createElement("button", { type: "button", className: "wb-extension-close", onClick: function () { setBindItem(null); }, "aria-label": t("settings.close") }, "×")),
        React.createElement("input", { className: "wb-input mono", autoFocus: true, value: bindPath, onChange: function (event) { setBindPath(event.target.value); }, placeholder: "/usr/local/bin/" + bindItem.id }),
        React.createElement("footer", null, React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { setBindItem(null); } }, t("settings.cancel")), React.createElement("button", { type: "button", className: "wb-btn primary", disabled: !bindPath.trim() || busy === bindItem.key, onClick: saveBinding }, t("settings.save")))
      )
    )
  );
}

export { ExtensionsPanel };
