import assert from "node:assert/strict"
import test from "node:test"

import { wbcContextMetadataEmptyCollectionKey, wbcContextMetadataEmptyValueKey, wbcContextMetadataLabelKey, wbcContextRingColors, wbcContextRingMetrics, wbcShouldShowContextRing } from "./context-indicator.mjs"

test("context ring stays hidden until the conversation has a message", () => {
  assert.equal(wbcShouldShowContextRing({ messages: [], messageCount: 0 }), false)
  assert.equal(wbcShouldShowContextRing({ messages: [{ role: "user", content: "hello" }] }), true)
  assert.equal(wbcShouldShowContextRing({ messageCount: 2 }), true)
})

test("context metadata reuses canonical Workbench translation keys", () => {
  assert.equal(wbcContextMetadataLabelKey("adapter"), "settings.adapter")
  assert.equal(wbcContextMetadataLabelKey("reasoningEffort"), "settings.reasoningEffort")
  assert.equal(wbcContextMetadataLabelKey("contextLimit"), "workbenchChat.contextAuditContextLimit")
  assert.equal(wbcContextMetadataLabelKey("prompt_tokens"), "settings.usageInputTokens")
  assert.equal(wbcContextMetadataLabelKey("before_tokens"), "workbenchChat.contextMetadataField.beforeTokens")
  assert.equal(wbcContextMetadataLabelKey("plugin_name"), "workbenchChat.contextMetadataField.pluginName")
  assert.equal(wbcContextMetadataLabelKey("updatedAt"), "workbenchChat.contextAuditUpdatedAt")
  assert.equal(wbcContextMetadataLabelKey("updated_at"), "workbenchChat.contextAuditUpdatedAt")
  assert.equal(wbcContextMetadataLabelKey("tokens_est"), "workbenchChat.contextAuditTokensEst")
  assert.equal(wbcContextMetadataLabelKey("source_node_id"), "workbenchChat.contextMetadataField.sourceNodeId")
  assert.equal(wbcContextMetadataLabelKey("base_url"), "settings.baseUrlLabel")
  assert.equal(wbcContextMetadataLabelKey("reasoning_effort"), "settings.reasoningEffort")
  assert.equal(wbcContextMetadataLabelKey("cache_read_input_tokens"), "workbenchChat.contextMetadataField.cacheHitTokens")
  assert.equal(wbcContextMetadataLabelKey("unknown_extension_field"), "")
})

test("empty metadata values use field semantics instead of data-type labels", () => {
  assert.equal(
    wbcContextMetadataEmptyValueKey(["compaction", "updatedAt"]),
    "workbenchChat.contextMetadataNotCompacted",
  )
  assert.equal(
    wbcContextMetadataEmptyValueKey(["raw_value", "error"], { success: true }),
    "workbenchChat.contextMetadataNoError",
  )
  assert.equal(
    wbcContextMetadataEmptyValueKey(["tool_results", "#1", "error"], { success: false }),
    "workbenchChat.contextMetadataErrorUnavailable",
  )
  assert.equal(wbcContextMetadataEmptyValueKey(["model_identity", "reasoningEffort"]), "workbenchChat.contextMetadataModelDefault")
  assert.equal(wbcContextMetadataEmptyValueKey(["model_identity", "endpoint"]), "workbenchChat.contextMetadataProviderEndpointDefault")
  assert.equal(wbcContextMetadataEmptyValueKey(["raw_value", "parent_id"]), "workbenchChat.contextMetadataNoParent")
  assert.equal(wbcContextMetadataEmptyValueKey(["tool_results", "#1", "plugin_pack"]), "workbenchChat.contextMetadataStandalonePlugin")
  assert.equal(wbcContextMetadataEmptyValueKey(["raw_value", "custom_field"]), "workbenchChat.contextMetadataNotSet")
  assert.equal(wbcContextMetadataEmptyCollectionKey(["raw_value", "tool_calls"], true), "workbenchChat.contextMetadataNoToolCalls")
  assert.equal(wbcContextMetadataEmptyCollectionKey(["raw_value", "metadata"], false), "workbenchChat.contextMetadataNoMetadata")
  assert.equal(wbcContextMetadataEmptyCollectionKey(["raw_value", "extension_items"], true), "workbenchChat.contextMetadataNoItems")
})

test("context ring uses runtime context and latest-request cache usage", () => {
  assert.deepEqual(
    wbcContextRingMetrics(
      { ctxUsed: 20, ctxLimit: 100 },
      { prompt_cache_hit_tokens: 75, prompt_cache_miss_tokens: 25 },
      { used: 40, size: 200 },
    ),
    {
      contextUsed: 40,
      contextLimit: 200,
      contextRatio: 0.2,
      cacheHitTokens: 75,
      cacheMissTokens: 25,
      cacheRatio: 0.75,
      compactTriggerRatio: 0.6,
      hasContextLimit: true,
      hasCacheUsage: true,
    },
  )
})

test("context ring exposes unavailable cache data and clamps overfull context", () => {
  assert.deepEqual(
    wbcContextRingMetrics({ ctxUsed: 250, ctxLimit: 100 }, {}, {}),
    {
      contextUsed: 250,
      contextLimit: 100,
      contextRatio: 1,
      cacheHitTokens: 0,
      cacheMissTokens: 0,
      cacheRatio: 0,
      compactTriggerRatio: 0.6,
      hasContextLimit: true,
      hasCacheUsage: false,
    },
  )
})

test("context and cache rings interpolate colors continuously", () => {
  var low = wbcContextRingColors({ contextRatio: 0, compactTriggerRatio: 0.6, cacheRatio: 0, hasContextLimit: true, hasCacheUsage: true });
  var middle = wbcContextRingColors({ contextRatio: 0.3, compactTriggerRatio: 0.6, cacheRatio: 0.5, hasContextLimit: true, hasCacheUsage: true });
  var high = wbcContextRingColors({ contextRatio: 0.6, compactTriggerRatio: 0.6, cacheRatio: 1, hasContextLimit: true, hasCacheUsage: true });
  assert.match(low.context, /var\(--wb-green\) 100%/);
  assert.match(low.cache, /var\(--wb-red\) 100%/);
  assert.notEqual(middle.context, low.context);
  assert.match(middle.cache, /var\(--wb-amber\) 100%/);
  assert.match(high.context, /var\(--wb-red\)/);
  assert.match(high.cache, /var\(--wb-green\) 100%/);
})
