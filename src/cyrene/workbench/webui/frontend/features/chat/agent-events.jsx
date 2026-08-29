import { wbcT } from "./core.jsx"

function wbcAgentEventPayload(event) {
  return (
    event
    && event.payload
    && typeof event.payload === "object"
    && !Array.isArray(event.payload)
  ) ? event.payload : event;
}

function wbcAgentDeltaPayload(event) {
  var payload = wbcAgentEventPayload(event);
  return String(
    payload.delta != null ? payload.delta : (payload.text != null ? payload.text : (payload.content || ""))
  );
}

function wbcAgentDonePayload(event) {
  var payload = wbcAgentEventPayload(event);
  return String(
    payload.response != null ? payload.response : (payload.text != null ? payload.text : (payload.content || ""))
  );
}

function wbcAgentReasoningDelta(event) {
  var payload = wbcAgentEventPayload(event);
  return String(payload.delta != null ? payload.delta : (payload.text || ""));
}

function wbcAgentReasoningDone(event) {
  var payload = wbcAgentEventPayload(event);
  return String(
    payload.response != null ? payload.response : (payload.text != null ? payload.text : (payload.content || ""))
  );
}

function wbcAgentPhasePayload(event) {
  var payload = wbcAgentEventPayload(event);
  return {
    phase: String(payload.phase || payload.phaseKey || payload.phase_key || ""),
    provider: String(payload.provider || ""),
  };
}

function wbcAgentToolPayload(event) {
  var payload = wbcAgentEventPayload(event);
  var timestamp = String(event && event.timestamp || payload.timestamp || payload.createdAt || payload.created_at || "");
  var parsedAt = timestamp ? Date.parse(timestamp) : NaN;
  var progress = payload.progress && typeof payload.progress === "object"
    ? {
        current: Number(payload.progress.current) || 0,
        total: Number(payload.progress.total) || 0,
        label: String(payload.progress.label || ""),
      }
    : null;
  return {
    toolCallId: String(payload.toolCallId || payload.tool_call_id || ""),
    name: String(payload.name || payload.tool || payload.title || ""),
    title: String(payload.title || payload.name || ""),
    status: String(payload.status || "running"),
    failed: !!payload.failed,
    createdAt: Number.isFinite(parsedAt) ? parsedAt : Date.now(),
    inputSummary: wbcStructuredEventSummary(payload.inputSummary != null
      ? payload.inputSummary
      : (payload.input_summary != null ? payload.input_summary : payload.args)),
    outputSummary: wbcStructuredEventSummary(payload.outputSummary != null ? payload.outputSummary : payload.output_summary),
    input: payload.inputSummary != null
      ? payload.inputSummary
      : (payload.input_summary != null ? payload.input_summary : payload.args),
    output: payload.outputSummary != null ? payload.outputSummary : payload.output_summary,
    progress: progress,
    presentation: payload.presentation && typeof payload.presentation === "object" ? payload.presentation : {},
  };
}

function wbcAgentPermissionPayload(event) {
  var payload = wbcAgentEventPayload(event);
  var options = Array.isArray(payload.options) ? payload.options.map(function (opt) {
    if (typeof opt === "string") return { id: "", optionId: String(opt), label: String(opt), kind: "" };
    var id = String(opt.id != null ? opt.id : (opt.optionId != null ? opt.optionId : ""));
    return {
      id: id,
      optionId: id,
      label: String(opt.label || opt.title || ""),
      description: String(opt.description || ""),
      kind: String(opt.kind || ""),
    };
  }) : [];
  return {
    id: String(payload.requestId || payload.request_id || payload.id || ""),
    kind: String(payload.type || "permission.requested"),
    text: String(payload.title || payload.description || payload.message || ""),
    description: String(payload.description || ""),
    toolCallId: String(payload.toolCallId || payload.tool_call_id || ""),
    options: options,
    allowCustom: false,
    permission: true,
    meta: payload.meta && typeof payload.meta === "object" ? payload.meta : {},
  };
}

function wbcAgentPermissionReviewPayload(event) {
  var payload = wbcAgentEventPayload(event);
  var timestamp = String(event && event.timestamp || payload.createdAt || payload.created_at || "");
  var parsedAt = timestamp ? Date.parse(timestamp) : NaN;
  var approved = payload.approved === true;
  var decisions = Array.isArray(payload.decisions) ? payload.decisions : [];
  var preview = decisions.map(function (decision) {
    if (!decision || typeof decision !== "object") return "";
    return [String(decision.tool || ""), String(decision.rationale || "")]
      .filter(Boolean)
      .join(" · ");
  }).filter(Boolean).join("; ").slice(0, 240);
  return {
    id: String(payload.id || event && (event.eventId || event.event_id) || ""),
    kind: "permission",
    text: approved
      ? wbcT("workbenchChat.permissionApproved", "Permission review approved")
      : wbcT("workbenchChat.permissionDenied", "Permission review denied"),
    preview: preview,
    status: approved ? "completed" : "failed",
    failed: !approved,
    approved: approved,
    decisions: decisions,
    createdAt: Number.isFinite(parsedAt) ? parsedAt : Date.now(),
  };
}

function wbcAgentElicitationPayload(event) {
  var payload = wbcAgentEventPayload(event);
  return {
    id: String(payload.requestId || payload.request_id || payload.id || ""),
    kind: String(payload.type || "elicitation.requested"),
    text: String(payload.text || payload.message || payload.title || ""),
    options: Array.isArray(payload.options) ? payload.options : [],
    allowCustom: payload.allowCustom !== false,
    schema: payload.schema && typeof payload.schema === "object" ? payload.schema : null,
    fields: Array.isArray(payload.fields) ? payload.fields : [],
    meta: payload.meta && typeof payload.meta === "object" ? payload.meta : {},
  };
}

function wbcStructuredEventSummary(value) {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    var parts = value.map(function (item) {
      if (item && typeof item === "object") {
        var nested = item.content && typeof item.content === "object" ? item.content : item;
        return nested.text || nested.message || nested.title || nested.name || nested.path || nested.uri || nested.url || "";
      }
      return item == null ? "" : String(item);
    }).filter(Boolean);
    if (parts.length) return parts.join(" · ");
  }
  try {
    var serialized = JSON.stringify(value);
    return serialized === "{}" || serialized === "[]" ? "" : serialized;
  } catch (e) {
    return "";
  }
}

function wbcAgentSessionPayload(event) {
  var payload = wbcAgentEventPayload(event);
  return {
    sessionId: String(payload.sessionId || payload.session_id || event.sessionId || event.session_id || ""),
    updateKind: String(payload.updateKind || payload.update_kind || ""),
    commands: Array.isArray(payload.commands) ? payload.commands : [],
    mode: payload.mode,
    configOption: payload.configOption && typeof payload.configOption === "object" ? payload.configOption : null,
    configOptions: Array.isArray(payload.configOptions) ? payload.configOptions : [],
    plan: payload.plan && typeof payload.plan === "object" ? payload.plan : null,
    sessionInfo: payload.sessionInfo && typeof payload.sessionInfo === "object" ? payload.sessionInfo : null,
    update: payload.update && typeof payload.update === "object" ? payload.update : null,
  };
}

function wbcAgentAwaitingPayload(event) {
  var payload = wbcAgentEventPayload(event);
  if (payload.pending_question || payload.pendingQuestion) {
    return payload.pending_question || payload.pendingQuestion;
  }
  return {
    id: String(payload.requestId || payload.request_id || payload.id || ""),
    kind: String(payload.kind || "ask_user"),
    text: String(payload.text || payload.message || payload.title || ""),
    options: Array.isArray(payload.options) ? payload.options : [],
    allowCustom: !!payload.allowCustom,
    meta: payload.meta && typeof payload.meta === "object" ? payload.meta : {},
  };
}

function wbcAgentRunFailedError(event) {
  var payload = wbcAgentEventPayload(event);
  var failureKind = String(payload.failureKind || payload.failure_kind || payload.code || "").trim();
  var message = String(payload.message || payload.detail || payload.error || "").trim();
  var err = new Error(message || wbcT("workbenchChat.agentError.failed", "Agent run failed"));
  err.code = failureKind || "agent_run_failed";
  err.failureKind = failureKind || err.code;
  err.detailKey = String(payload.detail_key || payload.detailKey || "");
  err.detailParams = payload.detail_params || payload.detailParams || {};
  err.errorType = String(payload.error || "");
  err.agentId = String(event.agentId || payload.agentId || "");
  err.installationId = String(event.installationId || payload.installationId || "");
  return err;
}

function wbcAgentNotificationPayload(event) {
  var payload = wbcAgentEventPayload(event);
  var timestamp = String(event && event.timestamp || payload.createdAt || "");
  var parsedAt = timestamp ? Date.parse(timestamp) : NaN;
  return {
    id: String(event && (event.eventId || event.event_id) || payload.id || ""),
    createdAt: Number.isFinite(parsedAt) ? parsedAt : Date.now(),
    severity: String(payload.severity || "warning"),
    category: String(payload.category || "transport_warning"),
    message: String(payload.message || payload.detail || "").trim(),
    source: String(payload.source || "agent_runtime"),
    terminal: payload.terminal === true,
  };
}

var AGENT_EVENT_ROUTER = {
  "run.started": { handler: "onRunStarted" },
  "run.awaiting_input": { handler: "onAwaitingUser", normalize: wbcAgentAwaitingPayload },
  "run.completed": { handler: "onFinalizing" },
  "run.failed": { dispatch: function (handlers, event) { if (handlers.onError) handlers.onError(wbcAgentRunFailedError(event)); } },
  "run.cancelled": { handler: "onInterrupted" },
  "message.started": { handler: "onReplyStart" },
  "message.delta": { handler: "onReplyDelta", normalize: wbcAgentDeltaPayload },
  "message.completed": { handler: "onReplyDone", normalize: wbcAgentDonePayload },
  "notification.created": { handler: "onNotification", normalize: wbcAgentNotificationPayload },
  "reasoning.started": { handler: "onReasoningStart", normalize: wbcAgentPhasePayload },
  "reasoning.delta": { handler: "onReasoningDelta", normalize: wbcAgentReasoningDelta },
  "reasoning.completed": { handler: "onReasoningDone", normalize: wbcAgentReasoningDone },
  "tool.started": { handler: "onToolStarted", normalize: wbcAgentToolPayload },
  "tool.updated": { handler: "onToolUpdated", normalize: wbcAgentToolPayload },
  "tool.completed": { handler: "onToolCompleted", normalize: wbcAgentToolPayload },
  "permission.reviewed": { handler: "onPermissionReviewed", normalize: wbcAgentPermissionReviewPayload },
  "permission.requested": { handler: "onAwaitingUser", normalize: wbcAgentPermissionPayload },
  "permission.resolved": { handler: "onPermissionResolved" },
  "elicitation.requested": { handler: "onAwaitingUser", normalize: wbcAgentElicitationPayload },
  "elicitation.resolved": { handler: "onElicitationResolved" },
  "artifact.created": { handler: "onArtifactEvent" },
  "artifact.updated": { handler: "onArtifactEvent" },
  "usage.updated": { handler: "onUsageUpdated" },
  "session.updated": { handler: "onSessionUpdated", normalize: wbcAgentSessionPayload },
};

function wbcRouteAgentEvent(type, event, handlers) {
  var entry = AGENT_EVENT_ROUTER[type];
  if (!entry) return false;
  if (typeof entry.dispatch === "function") {
    entry.dispatch(handlers, event);
    return true;
  }
  var handler = handlers[entry.handler];
  if (!handler) return true; // recognized but no consumer — safe ignore
  var value = entry.normalize ? entry.normalize(event) : event;
  try {
    if (entry.handler === "onReasoningDelta" || entry.handler === "onReasoningDone") {
      handler(value, event);
    } else {
      handler(value);
    }
  } catch (e) {
    try { console.warn("[agent-event] handler failed for " + type, e); } catch (_e) {}
  }
  return true;
}

// ---------------------------------------------------------------------------
// Data access
// ---------------------------------------------------------------------------

export { wbcAgentEventPayload, wbcAgentDeltaPayload, wbcAgentDonePayload, wbcAgentReasoningDelta, wbcAgentReasoningDone, wbcAgentPhasePayload, wbcAgentToolPayload, wbcAgentPermissionPayload, wbcAgentPermissionReviewPayload, wbcAgentElicitationPayload, wbcStructuredEventSummary, wbcAgentSessionPayload, wbcAgentAwaitingPayload, wbcAgentRunFailedError, wbcAgentNotificationPayload, AGENT_EVENT_ROUTER, wbcRouteAgentEvent }
