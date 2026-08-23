import { wbcT } from "./core.jsx"

var WORKBENCH_BUDGET_CODES = {
  budget_monthly_exhausted: "monthly",
  budget_weekly_exhausted: "weekly",
  budget_5h_exhausted: "5h",
  budget_usage_unavailable: "unavailable",
};

var WORKBENCH_ERROR_I18N_KEYS = {
  quota_exhausted: "workbenchChat.error.quotaExhausted",
  authentication_expired: "workbenchChat.error.authenticationExpired",
  model_unavailable: "workbenchChat.error.modelUnavailable",
  model_not_configured: "workbenchChat.error.modelNotConfigured",
  model_authentication_failed: "workbenchChat.error.modelAuthenticationFailed",
  process_restarted: "workbenchChat.error.processRestarted",
  chat_run_driver_failed: "workbenchChat.error.driverFailed",
  chat_not_found: "workbenchChat.error.chatNotFound",
  chat_run_not_found: "workbenchChat.error.chatRunNotFound",
  chat_not_running: "workbenchChat.error.chatNotRunning",
  chat_run_in_progress: "workbenchChat.error.chatRunInProgress",
  guidance_persistence_failed: "workbenchChat.error.guidancePersistenceFailed",
  answer_resume_failed: "workbenchChat.error.answerResumeFailed",
  no_completed_context: "workbenchChat.error.memoryContextUnavailable",
  project_mismatch: "workbenchChat.error.memoryProjectMismatch",
};

function wbcErrorText(err) {
  var raw = String((err && err.message) || err || "").trim();
  if (!raw || raw === "Load failed" || raw === "Failed to fetch" || raw === "NetworkError when attempting to fetch resource.") {
    return wbcT("workbenchChat.error.loadFailed", "Load failed");
  }
  var code = (err && err.code) || "";
  if (code.startsWith("budget_")) {
    var i18nKey = "budget.error." + (WORKBENCH_BUDGET_CODES[code] || "5h");
    return wbcT(i18nKey, raw);
  }
  var detailKey = (err && (err.detailKey || err.detail_key)) || WORKBENCH_ERROR_I18N_KEYS[code] || "";
  if (detailKey) {
    return wbcT(detailKey, raw, (err && (err.detailParams || err.detail_params)) || {});
  }
  // Keep older daemons compatible while they are being upgraded: known Codex
  // availability messages can still be localized even without error metadata.
  if (/^codex\s+quota\s+is\s+exhausted\b/i.test(raw)) {
    return wbcT("workbenchChat.error.quotaExhausted", raw);
  }
  if (/^codex\s+authentication\s+has\s+expired\b/i.test(raw)) {
    return wbcT("workbenchChat.error.authenticationExpired", raw);
  }
  if (/^codex(?:\s+model)?\b.*\bunavailable\b/i.test(raw)) {
    return wbcT("workbenchChat.error.modelUnavailable", raw);
  }
  try {
    var api = workbenchServices.api();
    if (api && typeof api.errorText === "function") return api.errorText(err);
  } catch (e) {}
  return raw;
}

function wbcAgentErrorPresentation(detail, failureKind) {
  var signature = [failureKind, detail].join(" ").toLowerCase();
  var stable = {
    dependency_missing: ["dependency", "workbenchChat.error.agentDependencyTitle", "Agent dependency is missing", "workbenchChat.error.agentDependencySummary", "The installed Agent cannot start because its executable or runtime dependency is unavailable.", "workbenchChat.error.agentDependencyHint", "Reinstall the Agent or repair the executable shown in Agent settings."],
    agent_disabled: ["configuration", "workbenchChat.error.agentDisabledTitle", "Agent is disabled", "workbenchChat.error.agentDisabledSummary", "This Agent is installed but disabled in Extensions.", "workbenchChat.error.agentDisabledHint", "Enable it in the installed Agent details, then retry."],
    auth_required: ["authentication", "workbenchChat.error.agentAuthTitle", "Agent login is required", "workbenchChat.error.agentAuthSummary", "The Agent requires its own login or credentials before it can run.", "workbenchChat.error.agentAuthHint", "Open the Agent details and complete login, then retry."],
    auth_expired: ["authentication", "workbenchChat.error.agentAuthExpiredTitle", "Agent login expired", "workbenchChat.error.agentAuthExpiredSummary", "The Agent's independent login is no longer valid.", "workbenchChat.error.agentAuthExpiredHint", "Sign in again from the Agent details."],
    protocol_mismatch: ["protocol", "workbenchChat.error.agentProtocolTitle", "Agent protocol is incompatible", "workbenchChat.error.agentProtocolSummary", "The Agent returned an ACP message that Cyrene cannot safely interpret.", "workbenchChat.error.agentProtocolHint", "Update the Agent or run Test connection to inspect its protocol version."],
    capability_missing: ["capability", "workbenchChat.error.agentCapabilityTitle", "Agent capability is unavailable", "workbenchChat.error.agentCapabilitySummary", "This operation requires a capability the selected Agent did not provide.", "workbenchChat.error.agentCapabilityHint", "Choose a supported action or another Agent."],
    model_binding_unsupported: ["model", "workbenchChat.error.agentModelBindingTitle", "Agent cannot use this model source", "workbenchChat.error.agentModelBindingSummary", "The Agent does not support the selected Cyrene or Agent-owned model configuration.", "workbenchChat.error.agentModelBindingHint", "Change Model source in the Agent details."],
    model_gateway_unavailable: ["model", "workbenchChat.error.agentGatewayTitle", "Cyrene Model Gateway is unavailable", "workbenchChat.error.agentGatewaySummary", "The Agent could not access the selected Cyrene model configuration.", "workbenchChat.error.agentGatewayHint", "Check the Cyrene model configuration and proxy, then retry."],
    agent_crashed: ["runtime", "workbenchChat.error.agentCrashedTitle", "Agent process stopped", "workbenchChat.error.agentCrashedSummary", "The external Agent process exited before completing the request.", "workbenchChat.error.agentCrashedHint", "Open diagnostics, restart the Agent, and retry."],
    session_not_loadable: ["session", "workbenchChat.error.agentSessionTitle", "Agent session cannot be restored", "workbenchChat.error.agentSessionSummary", "The Agent no longer has the session associated with this conversation.", "workbenchChat.error.agentSessionHint", "Retry to start a replacement session with Cyrene's visible conversation history."],
    request_expired: ["request", "workbenchChat.error.agentRequestExpiredTitle", "Agent request expired", "workbenchChat.error.agentRequestExpiredSummary", "The permission or input request is no longer active.", "workbenchChat.error.agentRequestExpiredHint", "Retry the message and answer the new request."],
  }[String(failureKind || "").toLowerCase()];
  if (stable) return {
    tone: stable[0],
    title: wbcT(stable[1], stable[2]),
    summary: wbcT(stable[3], stable[4]),
    hint: wbcT(stable[5], stable[6]),
  };
  if (/invalid peer certificate|certificate (?:is )?not valid for name|certificate verification|certificate_verify_failed|tls handshake/.test(signature)) {
    return {
      tone: "security",
      title: wbcT("workbenchChat.error.tlsTitle", "Secure connection was intercepted"),
      summary: wbcT("workbenchChat.error.tlsSummary", "The server certificate does not match the requested Agent service, so Cyrene stopped the connection to protect your credentials."),
      hint: wbcT("workbenchChat.error.tlsHint", "Check the system proxy, VPN, DNS, or TLS-inspection rules, then retry. Do not disable certificate verification."),
    };
  }
  if (/websocket|stream disconnected|connection reset|connection refused|network|timed?\s*out|econn/.test(signature)) {
    return {
      tone: "network",
      title: wbcT("workbenchChat.error.networkTitle", "Agent network connection failed"),
      summary: wbcT("workbenchChat.error.networkSummary", "The Agent could not keep a connection to its model service."),
      hint: wbcT("workbenchChat.error.networkHint", "Check the proxy and network connection, then retry."),
    };
  }
  return null;
}

export { WORKBENCH_BUDGET_CODES, WORKBENCH_ERROR_I18N_KEYS, wbcErrorText, wbcAgentErrorPresentation }
