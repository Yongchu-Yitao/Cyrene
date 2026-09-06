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
  model_not_configured: "workbenchChat.error.modelNotConfigured",
  model_authentication_failed: "workbenchChat.error.modelAuthenticationFailed",
  model_quota_exhausted: "workbenchChat.error.modelQuotaExhausted",
  model_rate_limited: "workbenchChat.error.modelRateLimited",
  model_unavailable: "workbenchChat.error.modelUnavailableGeneric",
  model_request_too_large: "workbenchChat.error.modelRequestTooLarge",
  model_request_invalid: "workbenchChat.error.modelRequestInvalid",
  model_timeout: "workbenchChat.error.modelTimeout",
  model_tls_failed: "workbenchChat.error.modelTlsFailed",
  model_connection_failed: "workbenchChat.error.modelConnectionFailed",
  model_service_unavailable: "workbenchChat.error.modelServiceUnavailable",
  model_response_invalid: "workbenchChat.error.modelResponseInvalid",
  model_output_truncated: "workbenchChat.error.modelOutputTruncated",
  model_response_incomplete: "workbenchChat.error.modelResponseIncomplete",
  model_call_failed: "workbenchChat.error.modelCallFailed",
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
    model_not_configured: ["model", "workbenchChat.error.modelNotConfiguredTitle", "No model is configured", "workbenchChat.error.modelNotConfiguredSummary", "This conversation has no available model configuration.", "workbenchChat.error.modelNotConfiguredHint", "Configure a model in Settings → Models, then retry."],
    model_authentication_failed: ["authentication", "workbenchChat.error.modelAuthTitle", "Model authentication failed", "workbenchChat.error.modelAuthSummary", "The model service rejected the configured API key or login.", "workbenchChat.error.modelAuthHint", "Check the credentials in Settings → Models, then retry."],
    model_quota_exhausted: ["model", "workbenchChat.error.modelQuotaTitle", "Model quota is exhausted", "workbenchChat.error.modelQuotaSummary", "The model account has no available quota or credit.", "workbenchChat.error.modelQuotaHint", "Add credit, wait for the quota to reset, or switch models."],
    model_rate_limited: ["network", "workbenchChat.error.modelRateTitle", "Too many model requests", "workbenchChat.error.modelRateSummary", "The model service is temporarily rate limiting requests.", "workbenchChat.error.modelRateHint", "Wait briefly and retry, or switch to another model."],
    model_unavailable: ["model", "workbenchChat.error.modelUnavailableTitle", "Model is unavailable", "workbenchChat.error.modelUnavailableSummary", "The configured model or endpoint could not be found.", "workbenchChat.error.modelUnavailableHint", "Check the model ID and endpoint in Settings → Models."],
    model_request_too_large: ["model", "workbenchChat.error.modelContextTitle", "Conversation is too long", "workbenchChat.error.modelContextSummary", "This request exceeds the model's context window.", "workbenchChat.error.modelContextHint", "Start a new conversation, reduce attached context, or use a model with a larger context window."],
    model_request_invalid: ["model", "workbenchChat.error.modelRequestTitle", "Model rejected the request", "workbenchChat.error.modelRequestSummary", "The service does not accept the current request format or parameters.", "workbenchChat.error.modelRequestHint", "Check model compatibility and configuration, then retry."],
    model_timeout: ["network", "workbenchChat.error.modelTimeoutTitle", "Model response timed out", "workbenchChat.error.modelTimeoutSummary", "The model service did not respond before the timeout.", "workbenchChat.error.modelTimeoutHint", "Check the service load and network, then retry."],
    model_tls_failed: ["security", "workbenchChat.error.modelTlsTitle", "Model connection is not secure", "workbenchChat.error.modelTlsSummary", "Cyrene could not verify the model service certificate.", "workbenchChat.error.modelTlsHint", "Check the endpoint, proxy, VPN, and certificate configuration."],
    model_connection_failed: ["network", "workbenchChat.error.modelConnectionTitle", "Cannot reach model service", "workbenchChat.error.modelConnectionSummary", "Cyrene could not connect to the configured model endpoint.", "workbenchChat.error.modelConnectionHint", "Check that the service is running and verify its address, port, proxy, and network."],
    model_service_unavailable: ["network", "workbenchChat.error.modelServiceTitle", "Model service is unavailable", "workbenchChat.error.modelServiceSummary", "The upstream model service is overloaded or temporarily unavailable.", "workbenchChat.error.modelServiceHint", "Wait briefly and retry, or switch models."],
    model_response_invalid: ["model", "workbenchChat.error.modelResponseTitle", "Invalid model response", "workbenchChat.error.modelResponseSummary", "The service returned an empty or unsupported response.", "workbenchChat.error.modelResponseHint", "Check API compatibility or switch to another model."],
    model_output_truncated: ["model", "workbenchChat.error.modelOutputTruncatedTitle", "Model output limit reached", "workbenchChat.error.modelOutputTruncated", "The model reached its output limit and returned invalid tool arguments.", "workbenchChat.error.modelOutputTruncatedHint", "Split the output into smaller tool calls and retry."],
    model_response_incomplete: ["model", "workbenchChat.error.modelResponseIncompleteTitle", "Incomplete model response", "workbenchChat.error.modelResponseIncomplete", "The model response was not fully received.", "workbenchChat.error.modelResponseIncompleteHint", "Retry to receive a complete response."],
    model_call_failed: ["model", "workbenchChat.error.modelCallTitle", "Model call failed", "workbenchChat.error.modelCallSummary", "The model call failed for an unclassified reason.", "workbenchChat.error.modelCallHint", "Copy the details, check the model service, and retry."],
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
