import { WBC_CHAT_MODEL_CHANGED_EVENT, WBC_ICONS, useWbcEffect, useWbcRef, useWbcState, wbcCompactNumber, wbcT } from "../../workbench-chat.jsx"
import { wbcContextMetadataEmptyCollectionKey, wbcContextMetadataEmptyValueKey, wbcContextMetadataLabelKey, wbcContextRingColors, wbcContextRingMetrics, wbcShouldShowContextRing } from "./context-indicator.mjs"

function wbcContextPercent(ratio, available) {
  if (!available) return "—";
  var percentage = Math.max(0, Number(ratio) || 0) * 100;
  if (percentage > 0 && percentage < 1) return "<1%";
  return Math.round(percentage) + "%";
}

function wbcExactNumber(value) {
  var number = Number(value);
  if (!Number.isFinite(number)) number = 0;
  var language = typeof document !== "undefined" ? document.documentElement.lang : "";
  try {
    return new Intl.NumberFormat(language || undefined, { maximumFractionDigits: 0 }).format(Math.round(number));
  } catch (error) {
    return String(Math.round(number));
  }
}

function wbcExactPercent(part, total) {
  var ratio = Number(total) > 0 ? Number(part || 0) / Number(total) : 0;
  var language = typeof document !== "undefined" ? document.documentElement.lang : "";
  try {
    return new Intl.NumberFormat(language || undefined, {
      style: "percent",
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    }).format(ratio);
  } catch (error) {
    return (ratio * 100).toFixed(2).replace(/\.00$/, "") + "%";
  }
}

function wbcContextDate(value) {
  var normalized = String(value || "").trim();
  if (!normalized) return wbcT("workbenchChat.contextAuditTimeMissing", "Time unavailable");
  var date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return normalized;
  var language = typeof document !== "undefined" ? document.documentElement.lang : "";
  try {
    return new Intl.DateTimeFormat(language || undefined, {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      fractionalSecondDigits: 3,
    }).format(date);
  } catch (error) {
    return normalized;
  }
}

function wbcTechnicalValue(value) {
  if (value == null || value === "") return "—";
  if (typeof value === "object") {
    try { return JSON.stringify(value, null, 2); } catch (error) { return String(value); }
  }
  return String(value);
}

function wbcMetadataCollectionSummary(value) {
  if (Array.isArray(value)) {
    return wbcT("workbenchChat.contextMetadataItems", "{count} items", { count: wbcExactNumber(value.length) });
  }
  var count = value && typeof value === "object" ? Object.keys(value).length : 0;
  return wbcT("workbenchChat.contextMetadataFields", "{count} fields", { count: wbcExactNumber(count) });
}

function wbcMetadataDefaultOpen(label, value, depth) {
  if (depth !== 0) return false;
  var key = String(label || "");
  if (key === "raw_value") return false;
  if (key === "model_identity" || key === "usage" || key === "metadata") return true;
  return Array.isArray(value) && value.length > 0 && value.length <= 3;
}

function wbcContextMetadataLabel(label) {
  var raw = String(label || "");
  if (/^#\d+$/.test(raw)) return { localized: raw, raw: "" };
  var key = wbcContextMetadataLabelKey(raw);
  var localized = key ? wbcT(key, raw) : raw;
  return { localized: localized, raw: localized === raw ? "" : raw };
}

function WbcContextMetadataLabel({ label }) {
  var display = wbcContextMetadataLabel(label);
  return (
    <span className="wbc-context-metadata-label" title={display.raw || undefined}>
      <span>{display.localized}</span>
      {display.raw ? <code>{display.raw}</code> : null}
    </span>
  );
}

function wbcContextEmptyFallback(key) {
  var fallbacks = {
    "workbenchChat.contextMetadataNotCompacted": "Not compacted yet",
    "workbenchChat.contextMetadataNoError": "No error",
    "workbenchChat.contextMetadataErrorUnavailable": "Error details not returned",
    "workbenchChat.contextMetadataModelDefault": "Use model default",
    "workbenchChat.contextMetadataNoModelProfile": "Use model directly",
    "workbenchChat.contextMetadataProviderEndpointDefault": "Use provider default endpoint",
    "workbenchChat.contextMetadataNoParent": "No parent node",
    "workbenchChat.contextMetadataLifecycleUnspecified": "Lifecycle not specified",
    "workbenchChat.contextMetadataFinishReasonUnavailable": "Finish reason not returned",
    "workbenchChat.contextMetadataNoRun": "No associated run",
    "workbenchChat.contextMetadataNoSourceNode": "No source node",
    "workbenchChat.contextMetadataStandalonePlugin": "Standalone plugin",
    "workbenchChat.contextMetadataDirectToolCall": "Direct tool call",
    "workbenchChat.contextContentEmpty": "Empty content",
    "workbenchChat.contextMetadataNoResult": "No result",
    "workbenchChat.contextMetadataNoValue": "No value",
    "workbenchChat.contextMetadataUnnamed": "Unnamed",
    "workbenchChat.contextMetadataNoCallId": "No tool call ID",
    "workbenchChat.contextMetadataTimeUnavailable": "Time not recorded",
    "workbenchChat.contextMetadataNotSet": "Not set",
    "workbenchChat.contextMetadataNoMetadata": "No metadata",
    "workbenchChat.contextMetadataNoModelIdentity": "No model identity",
    "workbenchChat.contextMetadataNoUsage": "No usage data",
    "workbenchChat.contextMetadataNoArguments": "No arguments",
    "workbenchChat.contextMetadataNoToolCalls": "No tool calls",
    "workbenchChat.contextMetadataNoToolResults": "No tool results",
    "workbenchChat.contextMetadataNoEffectResults": "No effect results",
    "workbenchChat.contextMetadataNoResults": "No results",
    "workbenchChat.contextMetadataNoMessages": "No messages",
    "workbenchChat.contextMetadataNoReasoningDetails": "No reasoning details",
    "workbenchChat.contextMetadataNoTokenDetails": "No token details",
    "workbenchChat.contextMetadataNoRawPayload": "No raw payload",
    "workbenchChat.contextMetadataNoItems": "No items",
    "workbenchChat.contextMetadataNoFields": "No fields",
  };
  return fallbacks[key] || "Not set";
}

function WbcContextMetadataPrimitive({ value, path, container }) {
  if (value === null) {
    var nullKey = wbcContextMetadataEmptyValueKey(path, container);
    return <span className="is-null">{wbcT(nullKey, wbcContextEmptyFallback(nullKey))}</span>;
  }
  if (typeof value === "boolean") {
    return <span className={value ? "is-boolean is-true" : "is-boolean is-false"}>{value
      ? wbcT("workbenchChat.contextMetadataTrue", "true")
      : wbcT("workbenchChat.contextMetadataFalse", "false")}</span>;
  }
  if (typeof value === "number") return <code className="is-number">{String(value)}</code>;
  var text = String(value == null ? "" : value);
  if (!text) {
    var emptyKey = wbcContextMetadataEmptyValueKey(path, container);
    return <span className="is-null">{wbcT(emptyKey, wbcContextEmptyFallback(emptyKey))}</span>;
  }
  if (/^https?:\/\//i.test(text)) {
    return <a href={text} target="_blank" rel="noreferrer">{text}</a>;
  }
  return <code>{text}</code>;
}

function WbcContextMetadataTree({ value, depth, path }) {
  var isArray = Array.isArray(value);
  var entries = isArray
    ? value.map(function (item, index) { return ["#" + (index + 1), item]; })
    : Object.entries(value && typeof value === "object" ? value : {});
  if (!entries.length) {
    var emptyKey = wbcContextMetadataEmptyCollectionKey(path, isArray);
    return <span className="wbc-context-metadata-empty">{wbcT(emptyKey, wbcContextEmptyFallback(emptyKey))}</span>;
  }
  return (
    <div className={isArray ? "wbc-context-metadata-tree is-array" : "wbc-context-metadata-tree"}>
      {entries.map(function (entry) {
        var label = entry[0];
        var item = entry[1];
        var itemPath = (Array.isArray(path) ? path : []).concat(label);
        var composite = item !== null && typeof item === "object";
        return composite ? (
          <details className="wbc-context-metadata-group" open={wbcMetadataDefaultOpen(label, item, depth)} key={label}>
            <summary>
              <WbcContextMetadataLabel label={label} />
              <span>{wbcMetadataCollectionSummary(item)}</span>
            </summary>
            <WbcContextMetadataTree value={item} depth={depth + 1} path={itemPath} />
          </details>
        ) : (
          <div className="wbc-context-metadata-field" key={label}>
            <WbcContextMetadataLabel label={label} />
            <WbcContextMetadataPrimitive value={item} path={itemPath} container={value} />
          </div>
        );
      })}
    </div>
  );
}

function WbcContextStructuredMetadata({ value, namespace }) {
  return <WbcContextMetadataTree value={value} depth={0} path={namespace ? [namespace] : []} />;
}

function WbcContextAuditGrid({ rows }) {
  return (
    <dl className="wbc-context-audit-grid">
      {rows.map(function (row, index) {
        var structured = row[1] !== null && typeof row[1] === "object";
        var wide = row[3] === true;
        return (
          <div className={structured ? "is-structured" : (wide ? "is-wide" : "")} key={row[0] + "-" + index}>
            <dt>{row[0]}</dt>
            <dd className={(row[2] ? "is-code " : "") + (structured ? "is-structured" : "")}>
              {structured ? <WbcContextStructuredMetadata value={row[1]} namespace={row[4]} /> : wbcTechnicalValue(row[1])}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function WbcContextAuditSection({ title, hint, children }) {
  return (
    <section className="wbc-context-audit-section">
      <header><strong>{title}</strong>{hint ? <small>{hint}</small> : null}</header>
      {children}
    </section>
  );
}

function wbcFetchContextJson(url, signal) {
  var options = { cache: "no-store" };
  if (signal) options.signal = signal;
  return fetch(url, options).then(function (response) {
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.json();
  }).then(function (payload) {
    if (!payload || payload.error) throw new Error(String(payload && payload.error || "context unavailable"));
    return payload;
  });
}

function wbcUpdateContextNode(chatId, block, content) {
  return fetch(
    "/api/workbench/chats/" + encodeURIComponent(chatId)
      + "/context-nodes/" + encodeURIComponent(block.nodeId),
    {
      method: "PATCH",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: content,
        expectedUpdatedAt: String(block.updatedAt || ""),
      }),
    }
  ).then(function (response) {
    return response.json().catch(function () { return {}; }).then(function (payload) {
      if (!response.ok || !payload || payload.error) {
        throw new Error(String(payload && payload.error || "HTTP " + response.status));
      }
      return payload;
    });
  });
}

function WbcContextDoubleRing({ metrics }) {
  var outerRadius = 12;
  var innerRadius = 8;
  var outerLength = 2 * Math.PI * outerRadius;
  var innerLength = 2 * Math.PI * innerRadius;
  var colors = wbcContextRingColors(metrics);
  return (
    <svg className="wbc-context-ring-svg" viewBox="0 0 32 32" aria-hidden="true">
      <circle className="wbc-context-ring-track" cx="16" cy="16" r={outerRadius} />
      <circle
        className="wbc-context-ring-value is-context"
        style={{ stroke: colors.context }}
        cx="16" cy="16" r={outerRadius}
        strokeDasharray={outerLength}
        strokeDashoffset={outerLength * (1 - metrics.contextRatio)}
      />
      <circle className="wbc-context-ring-track is-inner" cx="16" cy="16" r={innerRadius} />
      <circle
        className="wbc-context-ring-value is-cache"
        style={{ stroke: colors.cache }}
        cx="16" cy="16" r={innerRadius}
        strokeDasharray={innerLength}
        strokeDashoffset={innerLength * (1 - metrics.cacheRatio)}
      />
    </svg>
  );
}

function WbcContextRingTooltip({ metrics, tooltipId }) {
  var colors = wbcContextRingColors(metrics);
  return (
    <div className="wbc-context-ring-tooltip" id={tooltipId} role="tooltip">
      <strong>{wbcT("workbenchChat.contextStatus", "Context status")}</strong>
      <div>
        <span><i className="is-context" style={{ background: colors.context }} />{wbcT("workbenchChat.contextUsage", "Context usage")}</span>
        <b>{wbcContextPercent(metrics.contextRatio, metrics.hasContextLimit)}</b>
      </div>
      <small>{metrics.hasContextLimit
        ? wbcCompactNumber(metrics.contextUsed) + " / " + wbcCompactNumber(metrics.contextLimit) + " " + wbcT("workbenchChat.contextAuditTokensUnit", "tokens")
        : wbcT("workbenchChat.ctx.unknownLimit", "Window size unknown")}</small>
      <div>
        <span><i className="is-cache" style={{ background: colors.cache }} />{wbcT("workbenchChat.cacheHitRate", "Cache hit rate")}</span>
        <b>{wbcContextPercent(metrics.cacheRatio, metrics.hasCacheUsage)}</b>
      </div>
      <small>{metrics.hasCacheUsage
        ? wbcT("workbenchChat.contextCacheTokens", "{hit} hit · {miss} miss", {
            hit: wbcCompactNumber(metrics.cacheHitTokens),
            miss: wbcCompactNumber(metrics.cacheMissTokens),
          })
        : wbcT("workbenchChat.contextCacheUnavailable", "No cache data for the latest request")}</small>
    </div>
  );
}

function wbcContextBlockMetadata(block) {
  var metadata = [];
  function add(labelKey, fallback, value) {
    var normalized = String(value == null ? "" : value).trim();
    if (normalized) metadata.push(wbcT(labelKey, fallback) + ": " + normalized);
  }
  add("workbenchChat.contextTreeSourceField", "source", block.source);
  add("workbenchChat.contextTreeTypeField", "type", block.type);
  add("workbenchChat.contextTreeReasonField", "reason", block.reason);
  add("workbenchChat.contextTreeKindField", "context kind", block.contextKind);
  add("workbenchChat.contextAuditBlockId", "block ID", block.id);
  add("workbenchChat.contextAuditNodeId", "node ID", block.nodeId);
  add("workbenchChat.contextAuditParentId", "parent ID", block.parentId);
  add("workbenchChat.contextAuditLifecycle", "lifecycle", block.lifecycle);
  add("workbenchChat.contextAuditCreatedAt", "created", block.createdAt ? wbcContextDate(block.createdAt) : "");
  add("workbenchChat.contextAuditUpdatedAt", "updated", block.updatedAt ? wbcContextDate(block.updatedAt) : "");
  add("workbenchChat.contextAuditContentFormat", "content format", block.contentFormat);
  if (block.chars != null && Number.isFinite(Number(block.chars))) {
    metadata.push(wbcT("workbenchChat.contextTreeCharsField", "chars") + ": " + wbcExactNumber(block.chars));
  }
  if (block.order != null && Number.isFinite(Number(block.order))) {
    metadata.push(wbcT("workbenchChat.contextAuditOrder", "sequence index") + ": " + wbcExactNumber(block.order));
  }
  return metadata;
}

function WbcContextBlockDetails({ block, chatId, onReload }) {
  var [editing, setEditing] = useWbcState(false);
  var [draft, setDraft] = useWbcState(String(block.nodeContent || ""));
  var [saving, setSaving] = useWbcState(false);
  var [error, setError] = useWbcState("");
  var [saved, setSaved] = useWbcState(false);
  var editorRef = useWbcRef(null);
  var metadata = wbcContextBlockMetadata(block);
  var detailRows = metadata.map(function (item) {
    var parts = item.split(": ");
    return [parts.shift(), parts.join(": "), true];
  });
  detailRows.push([
    wbcT("workbenchChat.contextPromptNodeMetadata", "Custom node metadata"),
    block.metadata && typeof block.metadata === "object" ? block.metadata : {},
    true,
  ]);
  if (block.technical && typeof block.technical === "object") {
    detailRows.push([
      wbcT("workbenchChat.contextAuditNodeTechnical", "Node metadata"),
      block.technical,
      true,
    ]);
  }

  useWbcEffect(function () {
    if (!editing) {
      setDraft(String(block.nodeContent || ""));
      return;
    }
    requestAnimationFrame(function () {
      if (editorRef.current) editorRef.current.focus();
    });
  }, [editing, block.updatedAt]);

  function save() {
    if (saving || draft === String(block.nodeContent || "")) return;
    setSaving(true);
    setError("");
    setSaved(false);
    wbcUpdateContextNode(chatId, block, draft).then(function () {
      setEditing(false);
      setSaved(true);
      if (onReload) onReload();
    }).catch(function (saveError) {
      setError(String(saveError && saveError.message || saveError));
    }).finally(function () { setSaving(false); });
  }

  return (
    <div className="wbc-context-block-details" role="group">
      {Object.prototype.hasOwnProperty.call(block, "content") ? (
        <div className="wbc-context-prompt-section">
          <strong>{wbcT("workbenchChat.contextContentSection", "Node content")}</strong>
          <pre className={block.content ? "" : "is-empty"}>{block.content || wbcT("workbenchChat.contextContentEmpty", "Empty content")}</pre>
        </div>
      ) : null}
      <WbcContextAuditGrid rows={detailRows} />
      {block.editable && block.nodeId ? (
        editing ? (
          <div className="wbc-context-prompt-editor">
            <label>
              <span>{wbcT("workbenchChat.contextContentFull", "Full node content")}</span>
              <textarea ref={editorRef} value={draft} onChange={function (event) { setDraft(event.target.value); setSaved(false); }} spellCheck="false" />
            </label>
            <small>{wbcT("workbenchChat.contextContentEditHint", "Changes are persisted to this ContextTree node and apply to subsequent context projections.")}</small>
            {block.contentFormat === "json" ? <small>{wbcT("workbenchChat.contextContentJsonHint", "This node uses structured JSON and must remain valid JSON.")}</small> : null}
            {error ? <p role="alert">{error}</p> : null}
            <div>
              <button type="button" className="is-secondary" disabled={saving} onClick={function () { setDraft(String(block.nodeContent || "")); setEditing(false); setError(""); }}>
                {wbcT("common.cancel", "Cancel")}
              </button>
              <button type="button" className="is-primary" disabled={saving || draft === String(block.nodeContent || "")} onClick={save}>
                {saving ? wbcT("common.saving", "Saving…") : wbcT("common.save", "Save")}
              </button>
            </div>
          </div>
        ) : (
          <div className="wbc-context-prompt-actions">
            {saved ? <span role="status">{wbcT("workbenchChat.contextContentSaved", "Node content saved")}</span> : null}
            <button type="button" onClick={function () { setEditing(true); setSaved(false); }}>
              {wbcT("workbenchChat.contextContentEdit", "Edit node content")}
            </button>
          </div>
        )
      ) : null}
    </div>
  );
}

function WbcContextTechnicalDetails({ data }) {
  var identity = data && data.modelIdentity && typeof data.modelIdentity === "object" ? data.modelIdentity : {};
  var compaction = data && data.compaction && typeof data.compaction === "object" ? data.compaction : {};
  var rows = [
    [wbcT("workbenchChat.contextAuditTreeId", "Tree ID"), data && data.treeId, true],
    [wbcT("workbenchChat.contextAuditRootId", "Root ID"), data && data.rootId, true],
    [wbcT("workbenchChat.contextAuditLeafId", "Leaf ID"), data && data.leafId, true],
    [wbcT("workbenchChat.contextAuditSource", "Composition source"), data && data.compositionSource, true],
    [wbcT("workbenchChat.contextAuditSelectedModel", "Selected model"), data && data.selectedModel, true],
    [wbcT("workbenchChat.contextAuditActualModel", "Actual model"), data && data.actualModel, true],
    [wbcT("workbenchChat.contextAuditTotalTokens", "Total token estimate"), wbcExactNumber(data && data.totalTokensEst)],
    [wbcT("workbenchChat.contextAuditMessageTokens", "Message tokens"), wbcExactNumber(data && data.messageTokens)],
    [wbcT("workbenchChat.contextAuditContextLimit", "Context limit"), wbcExactNumber(data && data.contextLimit)],
    [wbcT("workbenchChat.messageCount", "Messages"), wbcExactNumber(data && data.messageCount)],
    [wbcT("workbenchChat.contextAuditChronology", "Chronology"), data && data.chronology, true],
    [wbcT("workbenchChat.contextAuditCreatedAt", "Created"), data && data.createdAt ? wbcContextDate(data.createdAt) : ""],
    [wbcT("workbenchChat.contextAuditUpdatedAt", "Updated"), data && data.updatedAt ? wbcContextDate(data.updatedAt) : ""],
    [wbcT("workbenchChat.contextAuditModelIdentity", "Model identity"), identity, true],
    [wbcT("workbenchChat.contextAuditCompaction", "Compaction"), compaction, true, false, "compaction"],
  ];
  return (
    <WbcContextAuditSection title={wbcT("workbenchChat.contextAuditTechnical", "Technical identity")}>
      <WbcContextAuditGrid rows={rows} />
    </WbcContextAuditSection>
  );
}

function WbcContextPlugins({ data }) {
  var packs = data && Array.isArray(data.usedPluginPacks) ? data.usedPluginPacks : [];
  var standalone = data && Array.isArray(data.usedStandalonePlugins) ? data.usedStandalonePlugins : [];
  var localizedPacks = packs.map(wbcContextToolName);
  var localizedStandalone = standalone.map(wbcContextToolName);
  var none = wbcT("workbenchChat.contextAuditNone", "None");
  return (
    <WbcContextAuditSection title={wbcT("workbenchChat.contextAuditPlugins", "Plugin sources")}>
      <div className="wbc-context-plugin-grid">
        <WbcContextAuditGrid rows={[
          [wbcT("workbenchChat.contextAuditPluginPacks", "Plugin packs"), localizedPacks.length ? localizedPacks.join(" · ") : none, false, true],
          [wbcT("workbenchChat.contextAuditStandalonePlugins", "Standalone plugins"), localizedStandalone.length ? localizedStandalone.join(" · ") : none, false, true],
        ]} />
      </div>
    </WbcContextAuditSection>
  );
}

function wbcContextRoleLabel(role) {
  var normalized = String(role || "").trim();
  if (!normalized) return wbcT("workbenchChat.contextTreeNode", "Context node");
  return wbcT("workbenchChat.contextRole." + normalized, normalized);
}

function wbcContextKindLabel(kind) {
  var normalized = String(kind || "").trim();
  if (!normalized) return "";
  return wbcT("workbenchChat.contextKind." + normalized, normalized);
}

function wbcContextSourceLabel(source) {
  var normalized = String(source || "context_tree").trim() || "context_tree";
  return wbcT("workbenchChat.contextSource." + normalized, normalized);
}

function wbcContextToolName(name) {
  var normalized = String(name || "").trim();
  if (!normalized) return "";
  return wbcT("toolName." + normalized, normalized);
}

function wbcToolResultPresentation(item) {
  if (String(item && item.role || "") !== "tool_results") return null;
  var raw = Array.isArray(item && item.toolAttributions) ? item.toolAttributions : [];
  var names = [];
  var packs = [];
  var operations = [];
  raw.forEach(function (attribution) {
    if (!attribution || typeof attribution !== "object") return;
    var name = String(attribution.pluginName || "").trim();
    var pack = String(attribution.pluginPack || "").trim();
    var operation = String(attribution.operation || "").trim();
    if (name) {
      var localizedName = wbcContextToolName(name);
      if (names.indexOf(localizedName) < 0) names.push(localizedName);
    }
    if (pack) {
      var localizedPack = wbcContextToolName(pack);
      if (packs.indexOf(localizedPack) < 0) packs.push(localizedPack);
    }
    if (operation && operations.indexOf(operation) < 0) operations.push(operation);
  });
  var title = wbcContextRoleLabel("tool_results");
  if (raw.length === 1 && String(raw[0] && raw[0].pluginName || "").trim() === "toolbox" && operations.length === 1) {
    title = wbcT("workbenchChat.contextToolboxOperation." + operations[0], "Toolbox · " + operations[0]);
  } else if (names.length && names.length <= 3) {
    title = names.join(" · ");
  } else if (names.length > 3) {
    title = wbcT("workbenchChat.contextToolResultsCount", "{count} tool results", { count: wbcExactNumber(names.length) });
  }
  return {
    title: title,
    source: [wbcContextRoleLabel("tool_results"), packs.join(" · ")].filter(Boolean).join(" · "),
  };
}

function WbcContextTimeline({ data, chatId, onReload }) {
  var timeline = data && Array.isArray(data.timeline) ? data.timeline : [];
  var [selectedKey, setSelectedKey] = useWbcState("");
  return (
    <WbcContextAuditSection
      title={wbcT("workbenchChat.contextAuditTimeline", "Effective model context")}
      hint={wbcT("workbenchChat.contextAuditOldestFirst", "Model input order")}
    >
      {timeline.length ? <ol className="wbc-context-timeline">{timeline.map(function (item, index) {
        var toolPresentation = wbcToolResultPresentation(item);
        var title = toolPresentation
          ? toolPresentation.title
          : wbcContextKindLabel(item.displayType || item.contextKind) || wbcContextRoleLabel(item.role);
        var source = toolPresentation ? toolPresentation.source : wbcContextSourceLabel(item.source);
        var itemKey = String(item.id || index) + ":" + index;
        var selected = selectedKey === itemKey;
        return (
          <li key={String(item.id || index) + "-" + index}>
            <button
              type="button"
              className="wbc-context-timeline-button"
              aria-expanded={selected}
              onClick={function () { setSelectedKey(selected ? "" : itemKey); }}
            >
              <span>
                <b>{title}</b>
                <span className="wbc-context-timeline-origin">
                  <small>{source}</small>
                  {item.activeMount ? <em>{wbcT("workbenchChat.contextAuditActiveMount", "Current mount")}</em> : null}
                </span>
              </span>
              <span className="wbc-context-timeline-measure">
                {wbcExactNumber(item.tokensEst)} {wbcT("workbenchChat.contextAuditTokensUnit", "tokens")} · {wbcExactNumber(item.chars)} {wbcT("workbenchChat.contextAuditCharsUnit", "characters")}
              </span>
              <time dateTime={String(item.createdAt || "")}>{wbcContextDate(item.createdAt)}</time>
            </button>
            {selected ? <WbcContextBlockDetails block={item} chatId={chatId} onReload={onReload} /> : null}
          </li>
        );
      })}</ol> : <p className="wbc-context-audit-empty">{wbcT("workbenchChat.contextAuditNone", "None")}</p>}
    </WbcContextAuditSection>
  );
}

function WbcContextInspectorContent({ data, chatId, onReload }) {
  return (
    <div className="wbc-context-audit">
      <WbcContextTimeline data={data} chatId={chatId} onReload={onReload} />
      <WbcContextTechnicalDetails data={data} />
      <WbcContextPlugins data={data} />
    </div>
  );
}

function WbcContextInspectorDialog({ chat, summary, metrics, details, loading, error, onRetry, onClose, triggerRef, titleId }) {
  var closeRef = useWbcRef(null);
  var dialogRef = useWbcRef(null);

  useWbcEffect(function () {
    var previous = document.activeElement;
    requestAnimationFrame(function () { if (closeRef.current) closeRef.current.focus(); });
    return function () {
      var target = triggerRef && triggerRef.current ? triggerRef.current : previous;
      if (target && typeof target.focus === "function") target.focus();
    };
  }, []);

  function onKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    var focusable = Array.from(dialogRef.current.querySelectorAll('button:not([disabled]), summary, [href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'));
    if (!focusable.length) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  var model = String(summary && summary.model || chat && (chat.lastModel || chat.model) || "");
  return window.ReactDOM.createPortal(
    <div className="wbc-context-inspector-scrim" onMouseDown={function (event) { if (event.target === event.currentTarget) onClose(); }}>
      <section
        className="wbc-context-inspector"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
      >
        <header className="wbc-context-inspector-head">
          <span>
            <strong id={titleId}>{wbcT("workbenchChat.contextTree", "Conversation context tree")}</strong>
            <small>{model || wbcT("workbenchChat.contextTreeCurrentModel", "Current model")}</small>
          </span>
          <button ref={closeRef} type="button" onClick={onClose} aria-label={wbcT("common.close", "Close")}>{WBC_ICONS.x}</button>
        </header>
        <div className="wbc-context-inspector-summary">
          <div>
            <span>{wbcT("workbenchChat.contextUsage", "Context usage")}</span>
            <b>{wbcContextPercent(metrics.contextRatio, metrics.hasContextLimit)}</b>
            <small>{metrics.hasContextLimit ? wbcExactNumber(metrics.contextUsed) + " / " + wbcExactNumber(metrics.contextLimit) + " " + wbcT("workbenchChat.contextAuditTokensUnit", "tokens") : "—"}</small>
          </div>
          <div>
            <span>{wbcT("workbenchChat.cacheHitRate", "Cache hit rate")}</span>
            <b>{wbcContextPercent(metrics.cacheRatio, metrics.hasCacheUsage)}</b>
            <small>{metrics.hasCacheUsage
              ? wbcExactNumber(metrics.cacheHitTokens) + " / " + wbcExactNumber(metrics.cacheHitTokens + metrics.cacheMissTokens) + " " + wbcT("workbenchChat.contextAuditTokensUnit", "tokens")
              : "—"}</small>
          </div>
          <div>
            <span>{wbcT("workbenchChat.messageCount", "Messages")}</span>
            <b>{Number(details && details.messageCount != null ? details.messageCount : summary && summary.messageCount || 0)}</b>
            <small>{wbcT("workbenchChat.contextTreeActivePath", "active path")}</small>
          </div>
        </div>
        <div className="wbc-context-inspector-body">
          {loading ? (
            <div className="wbc-context-inspector-state" role="status"><span className="wbc-spinner" aria-hidden="true" />{wbcT("workbenchChat.contextTreeLoading", "Loading context tree…")}</div>
          ) : error ? (
            <div className="wbc-context-inspector-state is-error" role="alert">
              <span>{wbcT("workbenchChat.contextTreeLoadFailed", "The context tree could not be loaded.")}</span>
              <button type="button" onClick={onRetry}>{wbcT("workbenchChat.error.retry", "Retry")}</button>
            </div>
          ) : <WbcContextInspectorContent data={details} chatId={String(chat && chat.id || "")} onReload={onRetry} />}
        </div>
      </section>
    </div>,
    document.querySelector(".workbench-shell") || document.body
  );
}

function useWbcContextSummary(chatId, updatedAt, contextRevision, running) {
  var [summary, setSummary] = useWbcState(null);
  useWbcEffect(function () {
    if (!chatId) { setSummary(null); return undefined; }
    var cancelled = false;
    var timer = null;
    var controller = null;
    var requestRevision = 0;
    function load() {
      if (controller) controller.abort();
      if (timer) { clearTimeout(timer); timer = null; }
      var revision = ++requestRevision;
      var nextController = typeof AbortController === "function" ? new AbortController() : null;
      controller = nextController;
      wbcFetchContextJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/context", nextController && nextController.signal)
        .then(function (payload) { if (!cancelled && revision === requestRevision) setSummary(payload); })
        .catch(function (err) {
          if (!cancelled && revision === requestRevision && (!err || err.name !== "AbortError")) setSummary(null);
        })
        .finally(function () {
          if (controller === nextController) controller = null;
          if (!cancelled && running && revision === requestRevision) timer = setTimeout(load, 3500);
        });
    }
    function onModelChanged(event) {
      var detail = event && event.detail || {};
      if (!detail.chatId || String(detail.chatId) === chatId) load();
    }
    window.addEventListener(WBC_CHAT_MODEL_CHANGED_EVENT, onModelChanged);
    window.addEventListener("cyrene:model-configuration-changed", onModelChanged);
    load();
    return function () {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (controller) controller.abort();
      window.removeEventListener(WBC_CHAT_MODEL_CHANGED_EVENT, onModelChanged);
      window.removeEventListener("cyrene:model-configuration-changed", onModelChanged);
    };
  }, [chatId, updatedAt, contextRevision, !!running]);
  return [summary, setSummary];
}

function useWbcContextDetails(chatId, open, running, onSummary) {
  var [snapshot, setSnapshot] = useWbcState({ chatId: "", data: null });
  var [loading, setLoading] = useWbcState(false);
  var [errorChatId, setErrorChatId] = useWbcState("");
  var requestRevisionRef = useWbcRef(0);
  var snapshotRef = useWbcRef(snapshot);
  snapshotRef.current = snapshot;

  var currentDetails = snapshot.chatId === chatId ? snapshot.data : null;
  var currentError = errorChatId === chatId;

  function load() {
    if (!chatId) return;
    var revision = ++requestRevisionRef.current;
    var current = snapshotRef.current;
    var hasCurrentDetails = !!(current && current.chatId === chatId && current.data);
    // Polling is a background refresh once a snapshot is visible. Replacing
    // the body with a spinner would remount the scroll container and jump the
    // inspector back to the top on every refresh.
    if (!hasCurrentDetails) setLoading(true);
    setErrorChatId("");
    Promise.all([
      wbcFetchContextJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/context"),
      wbcFetchContextJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/context-blocks"),
    ]).then(function (payloads) {
      if (revision !== requestRevisionRef.current) return;
      var nextSnapshot = { chatId: chatId, data: payloads[1] };
      snapshotRef.current = nextSnapshot;
      setSnapshot(nextSnapshot);
      onSummary(payloads[0]);
    }).catch(function () {
      // Keep a previously rendered snapshot in place when a background poll
      // fails. The blocking error state is only for an initial load failure.
      if (revision === requestRevisionRef.current && !hasCurrentDetails) setErrorChatId(chatId);
    }).finally(function () {
      if (revision === requestRevisionRef.current) setLoading(false);
    });
  }

  useWbcEffect(function () {
    if (!open) return undefined;
    load();
    var timer = running ? setInterval(load, 3500) : null;
    return function () {
      if (timer) clearInterval(timer);
      requestRevisionRef.current += 1;
    };
  }, [open, !!running, chatId]);
  return {
    data: currentDetails,
    loading: loading || (!!open && !currentDetails && !currentError),
    error: currentError,
    retry: load,
  };
}

function WbcComposerContextIndicator({ chat, runtime, running }) {
  var chatId = String(chat && chat.id || "");
  var updatedAt = String(chat && chat.updatedAt || "");
  var contextRevision = Number(chat && chat.contextRevision || 0);
  var [open, setOpen] = useWbcState(false);
  var triggerRef = useWbcRef(null);
  var summaryState = useWbcContextSummary(chatId, updatedAt, contextRevision, running);
  var summary = summaryState[0];
  var detailsState = useWbcContextDetails(chatId, open, running, summaryState[1]);
  var idSuffix = chatId.replace(/[^a-zA-Z0-9_-]/g, "-") || "chat";
  var tooltipId = "wbc-context-ring-tooltip-" + idSuffix;
  var dialogTitleId = "wbc-context-inspector-title-" + idSuffix;

  var latestUsage = chat && chat.latestUsage && typeof chat.latestUsage === "object"
    ? chat.latestUsage : {};
  var runtimeContext = runtime && runtime.contextUsage && typeof runtime.contextUsage === "object"
    ? runtime.contextUsage : {};
  var metrics = wbcContextRingMetrics(summary, latestUsage, runtimeContext);
  var label = wbcT("workbenchChat.contextRingLabel", "Context {context}; cache hit {cache}. Open context tree.", {
    context: wbcContextPercent(metrics.contextRatio, metrics.hasContextLimit),
    cache: wbcContextPercent(metrics.cacheRatio, metrics.hasCacheUsage),
  });

  if (!chatId || !wbcShouldShowContextRing(chat)) return null;
  return (
    <span className="wbc-context-ring-anchor">
      <button
        ref={triggerRef}
        type="button"
        className="wbc-context-ring-button"
        aria-label={label}
        aria-describedby={tooltipId}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={function () { setOpen(true); }}
      >
        <WbcContextDoubleRing metrics={metrics} />
      </button>
      <WbcContextRingTooltip metrics={metrics} tooltipId={tooltipId} />
      {open && (
        <WbcContextInspectorDialog
          chat={chat}
          summary={summary}
          metrics={metrics}
          details={detailsState.data}
          loading={detailsState.loading}
          error={detailsState.error}
          onRetry={detailsState.retry}
          onClose={function () { setOpen(false); }}
          triggerRef={triggerRef}
          titleId={dialogTitleId}
        />
      )}
    </span>
  );
}

export { WbcComposerContextIndicator }
