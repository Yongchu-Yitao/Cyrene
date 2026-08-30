function wbcFiniteNonNegative(value) {
  var number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

// Context metadata mixes model configuration, usage accounting and durable
// tree bookkeeping. Reuse the established Workbench catalog for shared
// concepts; only context-inspector-specific fields get their own keys.
var WBC_CONTEXT_METADATA_LABEL_KEYS = Object.freeze({
  raw_value: "workbenchChat.contextMetadataField.rawValue",
  model_identity: "workbenchChat.contextAuditModelIdentity",
  modelIdentity: "workbenchChat.contextAuditModelIdentity",
  usage: "settings.usage",
  candidateId: "workbenchChat.contextMetadataField.candidateId",
  candidate_id: "workbenchChat.contextMetadataField.candidateId",
  adapter: "settings.adapter",
  provider: "settings.embeddingProvider",
  model: "workbenchChat.model",
  baseUrl: "settings.baseUrlLabel",
  baseURL: "settings.baseUrlLabel",
  base_url: "settings.baseUrlLabel",
  endpoint: "settings.modelServiceApiEndpoint",
  reasoningEffort: "settings.reasoningEffort",
  reasoning_effort: "settings.reasoningEffort",
  active: "common.enabled",
  blocks: "workbenchChat.contextMetadataField.blocks",
  beforeTokens: "workbenchChat.contextMetadataField.beforeTokens",
  before_tokens: "workbenchChat.contextMetadataField.beforeTokens",
  afterTokens: "workbenchChat.contextMetadataField.afterTokens",
  after_tokens: "workbenchChat.contextMetadataField.afterTokens",
  contextLimit: "workbenchChat.contextAuditContextLimit",
  context_limit: "workbenchChat.contextAuditContextLimit",
  distilled: "workbenchChat.contextMetadataField.distilled",
  run_id: "chat.runId",
  runId: "chat.runId",
  prompt_tokens: "settings.usageInputTokens",
  completion_tokens: "settings.usageOutputTokens",
  total_tokens: "workbenchChat.contextMetadataField.totalTokens",
  prompt_cache_hit_tokens: "workbenchChat.contextMetadataField.cacheHitTokens",
  prompt_cache_miss_tokens: "workbenchChat.contextMetadataField.cacheMissTokens",
  cache_creation_input_tokens: "workbenchChat.contextMetadataField.cacheMissTokens",
  finish_reason: "workbenchChat.contextMetadataField.finishReason",
  finishReason: "workbenchChat.contextMetadataField.finishReason",
  tool_calls: "workbenchChat.contextMetadataField.toolCalls",
  toolCalls: "workbenchChat.contextMetadataField.toolCalls",
  tool_results: "workbenchChat.contextRole.tool_results",
  toolResults: "workbenchChat.contextRole.tool_results",
  effect_results: "workbenchChat.contextMetadataField.effectResults",
  effectResults: "workbenchChat.contextMetadataField.effectResults",
  call_id: "workbenchChat.contextMetadataField.callId",
  callId: "workbenchChat.contextMetadataField.callId",
  success: "workbenchChat.contextMetadataField.success",
  error: "workbenchChat.contextMetadataField.error",
  plugin_pack: "workbenchChat.contextMetadataField.pluginPack",
  pluginPack: "workbenchChat.contextMetadataField.pluginPack",
  plugin_name: "workbenchChat.contextMetadataField.pluginName",
  pluginName: "workbenchChat.contextMetadataField.pluginName",
  role: "workbenchChat.contextMetadataField.role",
  content: "workbenchChat.contextContentSection",
  context_kind: "workbenchChat.contextTreeKindField",
  contextKind: "workbenchChat.contextTreeKindField",
  context_source: "workbenchChat.contextTreeSourceField",
  contextSource: "workbenchChat.contextTreeSourceField",
  context_lifecycle: "workbenchChat.contextAuditLifecycle",
  contextLifecycle: "workbenchChat.contextAuditLifecycle",
  messages: "workbenchChat.contextMetadataField.messages",
  results: "workbenchChat.contextMetadataField.results",
  reasoning_details: "workbenchChat.contextMetadataField.reasoningDetails",
  reasoningDetails: "workbenchChat.contextMetadataField.reasoningDetails",
  auxiliary_usage: "workbenchChat.contextMetadataField.auxiliaryUsage",
  auxiliaryUsage: "workbenchChat.contextMetadataField.auxiliaryUsage",
  id: "workbenchChat.contextMetadataField.id",
  name: "common.name",
  arguments: "workbenchChat.contextMetadataField.arguments",
  operation: "workbenchChat.contextMetadataField.operation",
  metadata: "workbenchChat.contextPromptNodeMetadata",
  updatedAt: "workbenchChat.contextAuditUpdatedAt",
  updated_at: "workbenchChat.contextAuditUpdatedAt",
  createdAt: "workbenchChat.contextAuditCreatedAt",
  created_at: "workbenchChat.contextAuditCreatedAt",
  parentId: "workbenchChat.contextAuditParentId",
  parent_id: "workbenchChat.contextAuditParentId",
  nodeId: "workbenchChat.contextAuditNodeId",
  node_id: "workbenchChat.contextAuditNodeId",
  source_node_id: "workbenchChat.contextMetadataField.sourceNodeId",
  sourceNodeId: "workbenchChat.contextMetadataField.sourceNodeId",
  tokensEst: "workbenchChat.contextAuditTokensEst",
  tokens_est: "workbenchChat.contextAuditTokensEst",
  chars: "workbenchChat.contextTreeCharsField",
  order: "workbenchChat.contextAuditOrder",
  source: "workbenchChat.contextTreeSourceField",
  kind: "workbenchChat.contextTreeKindField",
  type: "workbenchChat.contextTreeTypeField",
  reason: "workbenchChat.contextTreeReasonField",
  lifecycle: "workbenchChat.contextAuditLifecycle",
  value: "workbenchChat.contextMetadataField.value",
  result: "workbenchChat.contextMetadataField.result",
  pack: "workbenchChat.contextMetadataField.pluginPack",
  status: "workbenchChat.statusLabel",
  prompt_tokens_details: "workbenchChat.contextMetadataField.promptTokenDetails",
  cached_tokens: "workbenchChat.contextMetadataField.cacheHitTokens",
  cached_prompt_tokens: "workbenchChat.contextMetadataField.cacheHitTokens",
  cached_input_tokens: "workbenchChat.contextMetadataField.cacheHitTokens",
  cache_read_input_tokens: "workbenchChat.contextMetadataField.cacheHitTokens",
  cache_hit_tokens: "workbenchChat.contextMetadataField.cacheHitTokens",
  cache_miss_tokens: "workbenchChat.contextMetadataField.cacheMissTokens",
  session_end_complete: "workbenchChat.contextMetadataField.sessionEndComplete",
  trigger_model: "workbenchChat.contextMetadataField.triggerModel",
  compacted: "workbenchChat.contextMetadataField.compacted",
  compacted_block: "workbenchChat.contextMetadataField.compactedBlock",
  llm_compacted: "workbenchChat.contextMetadataField.modelCompacted",
  ephemeral_context: "workbenchChat.contextMetadataField.ephemeralContext",
  root: "workbenchChat.contextMetadataField.rootNode",
});

function wbcContextMetadataLabelKey(label) {
  return WBC_CONTEXT_METADATA_LABEL_KEYS[String(label || "")] || "";
}

function wbcContextMetadataEmptyValueKey(path, container) {
  var parts = Array.isArray(path) ? path.map(String) : [];
  var field = parts.length ? parts[parts.length - 1] : "";
  if (parts[0] === "compaction" && (field === "updatedAt" || field === "updated_at")) {
    return "workbenchChat.contextMetadataNotCompacted";
  }
  if (field === "error") {
    return container && container.success === false
      ? "workbenchChat.contextMetadataErrorUnavailable"
      : "workbenchChat.contextMetadataNoError";
  }
  var semanticKeys = {
    reasoningEffort: "workbenchChat.contextMetadataModelDefault",
    reasoning_effort: "workbenchChat.contextMetadataModelDefault",
    candidateId: "workbenchChat.contextMetadataNoModelProfile",
    candidate_id: "workbenchChat.contextMetadataNoModelProfile",
    baseUrl: "workbenchChat.contextMetadataProviderEndpointDefault",
    baseURL: "workbenchChat.contextMetadataProviderEndpointDefault",
    base_url: "workbenchChat.contextMetadataProviderEndpointDefault",
    endpoint: "workbenchChat.contextMetadataProviderEndpointDefault",
    parentId: "workbenchChat.contextMetadataNoParent",
    parent_id: "workbenchChat.contextMetadataNoParent",
    lifecycle: "workbenchChat.contextMetadataLifecycleUnspecified",
    context_lifecycle: "workbenchChat.contextMetadataLifecycleUnspecified",
    contextLifecycle: "workbenchChat.contextMetadataLifecycleUnspecified",
    finish_reason: "workbenchChat.contextMetadataFinishReasonUnavailable",
    finishReason: "workbenchChat.contextMetadataFinishReasonUnavailable",
    run_id: "workbenchChat.contextMetadataNoRun",
    runId: "workbenchChat.contextMetadataNoRun",
    source_node_id: "workbenchChat.contextMetadataNoSourceNode",
    sourceNodeId: "workbenchChat.contextMetadataNoSourceNode",
    plugin_pack: "workbenchChat.contextMetadataStandalonePlugin",
    pluginPack: "workbenchChat.contextMetadataStandalonePlugin",
    pack: "workbenchChat.contextMetadataStandalonePlugin",
    operation: "workbenchChat.contextMetadataDirectToolCall",
    content: "workbenchChat.contextContentEmpty",
    result: "workbenchChat.contextMetadataNoResult",
    value: "workbenchChat.contextMetadataNoValue",
    name: "workbenchChat.contextMetadataUnnamed",
    plugin_name: "workbenchChat.contextMetadataUnnamed",
    pluginName: "workbenchChat.contextMetadataUnnamed",
    call_id: "workbenchChat.contextMetadataNoCallId",
    callId: "workbenchChat.contextMetadataNoCallId",
    createdAt: "workbenchChat.contextMetadataTimeUnavailable",
    created_at: "workbenchChat.contextMetadataTimeUnavailable",
    updatedAt: "workbenchChat.contextMetadataTimeUnavailable",
    updated_at: "workbenchChat.contextMetadataTimeUnavailable",
  };
  return semanticKeys[field] || "workbenchChat.contextMetadataNotSet";
}

function wbcContextMetadataEmptyCollectionKey(path, isArray) {
  var parts = Array.isArray(path) ? path.map(String) : [];
  var field = parts.length ? parts[parts.length - 1] : "";
  var semanticKeys = {
    metadata: "workbenchChat.contextMetadataNoMetadata",
    model_identity: "workbenchChat.contextMetadataNoModelIdentity",
    modelIdentity: "workbenchChat.contextMetadataNoModelIdentity",
    usage: "workbenchChat.contextMetadataNoUsage",
    arguments: "workbenchChat.contextMetadataNoArguments",
    tool_calls: "workbenchChat.contextMetadataNoToolCalls",
    toolCalls: "workbenchChat.contextMetadataNoToolCalls",
    tool_results: "workbenchChat.contextMetadataNoToolResults",
    toolResults: "workbenchChat.contextMetadataNoToolResults",
    effect_results: "workbenchChat.contextMetadataNoEffectResults",
    effectResults: "workbenchChat.contextMetadataNoEffectResults",
    results: "workbenchChat.contextMetadataNoResults",
    messages: "workbenchChat.contextMetadataNoMessages",
    reasoning_details: "workbenchChat.contextMetadataNoReasoningDetails",
    reasoningDetails: "workbenchChat.contextMetadataNoReasoningDetails",
    auxiliary_usage: "workbenchChat.contextMetadataNoUsage",
    auxiliaryUsage: "workbenchChat.contextMetadataNoUsage",
    prompt_tokens_details: "workbenchChat.contextMetadataNoTokenDetails",
    raw_value: "workbenchChat.contextMetadataNoRawPayload",
  };
  return semanticKeys[field] || (isArray
    ? "workbenchChat.contextMetadataNoItems"
    : "workbenchChat.contextMetadataNoFields");
}

function wbcShouldShowContextRing(chat) {
  chat = chat && typeof chat === "object" ? chat : {};
  return (Array.isArray(chat.messages) && chat.messages.length > 0)
    || Number(chat.messageCount || 0) > 0;
}

function wbcContextRingMetrics(summary, latestUsage, runtimeContext) {
  summary = summary && typeof summary === "object" ? summary : {};
  latestUsage = latestUsage && typeof latestUsage === "object" ? latestUsage : {};
  runtimeContext = runtimeContext && typeof runtimeContext === "object" ? runtimeContext : {};

  var contextUsed = wbcFiniteNonNegative(runtimeContext.used)
    || wbcFiniteNonNegative(summary.ctxUsed);
  var contextLimit = wbcFiniteNonNegative(runtimeContext.size)
    || wbcFiniteNonNegative(summary.ctxLimit);
  var contextRatio = contextLimit > 0
    ? Math.max(0, Math.min(1, contextUsed / contextLimit))
    : 0;
  var cacheHitTokens = wbcFiniteNonNegative(latestUsage.prompt_cache_hit_tokens);
  var cacheMissTokens = wbcFiniteNonNegative(latestUsage.prompt_cache_miss_tokens);
  var cacheTotal = cacheHitTokens + cacheMissTokens;
  var cacheRatio = cacheTotal > 0 ? cacheHitTokens / cacheTotal : 0;
  var compactTriggerRatio = Number(summary.compactTriggerRatio);
  if (!Number.isFinite(compactTriggerRatio) || compactTriggerRatio <= 0) {
    compactTriggerRatio = 0.6;
  }
  compactTriggerRatio = Math.min(1, compactTriggerRatio);

  return {
    contextUsed: contextUsed,
    contextLimit: contextLimit,
    contextRatio: contextRatio,
    cacheHitTokens: cacheHitTokens,
    cacheMissTokens: cacheMissTokens,
    cacheRatio: cacheRatio,
    compactTriggerRatio: compactTriggerRatio,
    hasContextLimit: contextLimit > 0,
    hasCacheUsage: cacheTotal > 0,
  };
}

function wbcContextColorMix(from, to, progress) {
  var clamped = Math.max(0, Math.min(1, Number(progress) || 0));
  var percentage = Math.round(clamped * 1000) / 10;
  return "color-mix(in srgb, " + from + " " + (100 - percentage) + "%, " + to + " " + percentage + "%)";
}

function wbcContextRingColors(metrics) {
  metrics = metrics && typeof metrics === "object" ? metrics : {};
  var contextRatio = Math.max(0, Math.min(1, Number(metrics.contextRatio) || 0));
  var triggerRatio = Math.max(0.01, Math.min(1, Number(metrics.compactTriggerRatio) || 0.6));
  var cacheRatio = Math.max(0, Math.min(1, Number(metrics.cacheRatio) || 0));
  var contextProgress = Math.min(1, contextRatio / triggerRatio);
  var orange = "color-mix(in srgb, var(--wb-amber) 58%, var(--wb-red))";
  var contextColor = contextProgress <= 0.75
    ? wbcContextColorMix("var(--wb-green)", "var(--wb-amber)", contextProgress / 0.75)
    : wbcContextColorMix("var(--wb-amber)", orange, (contextProgress - 0.75) / 0.25);
  var cacheColor = cacheRatio <= 0.5
    ? wbcContextColorMix("var(--wb-red)", "var(--wb-amber)", cacheRatio / 0.5)
    : wbcContextColorMix("var(--wb-amber)", "var(--wb-green)", (cacheRatio - 0.5) / 0.5);
  return {
    context: metrics.hasContextLimit === false ? "var(--wb-muted)" : contextColor,
    cache: metrics.hasCacheUsage === false ? "var(--wb-muted)" : cacheColor,
  };
}

export { wbcContextMetadataEmptyCollectionKey, wbcContextMetadataEmptyValueKey, wbcContextMetadataLabelKey, wbcContextRingColors, wbcContextRingMetrics, wbcShouldShowContextRing }
