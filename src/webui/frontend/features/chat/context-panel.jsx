import { workbenchServices } from "../../shared/runtime/services.jsx"
import { PluginView } from "../../platform/plugins.jsx"
import { WBC_CHAT_MODEL_CHANGED_EVENT, WBC_ICONS, WORKBENCH_BUDGET_CODES, WorkbenchChatModel, useWbcEffect, useWbcRef, useWbcState, wbcAgentConnectionLabel, wbcAgentDisplayName, wbcChatAgent, wbcCompactNumber, wbcCurrentModel, wbcErrorText, wbcFormatTime, wbcIsBuiltinAgent, wbcModelAccessLabel, wbcModelContextLimit, wbcOpenAgentDetail, wbcStructuredEventSummary, wbcT, wbcUsageReported } from "../../workbench-chat.jsx"
import { WBC_PROJECT_FILE_DRAFTS, WbcArtifactSplit, WbcBrowserSplit, WbcChangeSplit, WbcChatSplit, WbcMapPaneContent, WbcSideAgentSplit, WbcSubagentsTab, wbcChatArtifactFiles, wbcMapItemLabel, wbcProjectFileDraftKey } from "./split-pane.jsx"
import { wbcStartFileDrag } from "./file-resources.jsx"

// Workbench chat feature module with explicit ESM dependencies.
function WbcUsageRing({ usage }) {
  usage = usage || {};
  var hit = Number(usage.prompt_cache_hit_tokens || 0);
  var miss = Number(usage.prompt_cache_miss_tokens || 0);
  var prompt = Number(usage.prompt_tokens || 0);
  var completion = Number(usage.completion_tokens || 0);
  var total = Number(usage.total_tokens || 0) || (prompt + completion);
  var cacheTotal = hit + miss;
  var ratio = cacheTotal > 0 ? hit / cacheTotal : 0;
  var label = cacheTotal > 0 ? Math.round(ratio * 100) + "%" : (total ? wbcCompactNumber(total) : "—");
  var sub = cacheTotal > 0 ? wbcT("workbenchChat.cacheHitRate", "Cache hit rate") : wbcT("workbenchChat.tokenTotal", "Total");
  var r = 40, c = 2 * Math.PI * r;
  var dashOffset = c * (1 - (cacheTotal > 0 ? ratio : (total ? 1 : 0)));
  return (
    <div className="wbc-ring-wrap">
      <div className="wbc-ring">
        <svg width="96" height="96" viewBox="0 0 96 96">
          <circle cx="48" cy="48" r={r} fill="none" stroke="var(--wb-line)" strokeWidth="7" />
          <circle cx="48" cy="48" r={r} fill="none" stroke="var(--wb-green)" strokeWidth="7"
            strokeDasharray={c} strokeDashoffset={dashOffset}
            transform="rotate(-90 48 48)" strokeLinecap="round" />
        </svg>
        <div className="wbc-ring-label">
          <b>{label}</b>
          <small>{sub}</small>
        </div>
      </div>
      <div className="wbc-ring-meta">
        <div><span className="wbc-dot in" />{wbcT("workbenchChat.tokenInput", "Input")}<b>{prompt ? wbcCompactNumber(prompt) : "—"}</b></div>
        <div><span className="wbc-dot out" />{wbcT("workbenchChat.tokenOutput", "Output")}<b>{completion ? wbcCompactNumber(completion) : "—"}</b></div>
        <div><span className="wbc-dot total" />{wbcT("workbenchChat.tokenTotal", "Total")}<b>{total ? wbcCompactNumber(total) : "—"}</b></div>
      </div>
    </div>
  );
}

// Context-window gauge + composition for one conversation. The HTTP read model
// is projected exclusively from the Agent ContextTree; poll while a run streams
// so the panel updates as the durable tree is extended.
var WBC_CTX_SEG_ORDER = ["compacted", "system", "user", "assistant", "tool"];
var WBC_CTX_SEG_LABEL = {
  compacted: ["workbenchChat.ctx.seg.compacted", "Compressed"],
  system: ["workbenchChat.ctx.seg.system", "System"],
  user: ["workbenchChat.ctx.seg.user", "User"],
  assistant: ["workbenchChat.ctx.seg.assistant", "Assistant"],
  tool: ["workbenchChat.ctx.seg.tool", "Tools"],
};

function wbcCtxPct(ratio) {
  var p = (Number(ratio) || 0) * 100;
  if (p > 0 && p < 1) return "<1%";
  return Math.round(p) + "%";
}

var WBC_LIVE_CHAT_METRICS_CACHE = new Map();

function useWbcLiveChatMetrics(chat, running) {
  var chatId = chat ? chat.id : "";
  var [data, setData] = useWbcState(function () {
    return chatId ? (WBC_LIVE_CHAT_METRICS_CACHE.get(chatId) || null) : null;
  });
  var updatedAt = chat ? chat.updatedAt : "";
  var contextRevision = chat ? chat.contextRevision : 0;

  useWbcEffect(function () {
    if (!chatId) { setData(null); return undefined; }
    var cancelled = false;
    var requestRevision = 0;
    var inFlight = false;
    var pendingLoad = false;
    var timer = null;
    var requestController = null;
    function storePayload(payload) {
      var nextData = { chatId: chatId, payload: payload };
      WBC_LIVE_CHAT_METRICS_CACHE.set(chatId, nextData);
      setData(nextData);
    }
    function load() {
      if (cancelled) return;
      if (inFlight) {
        pendingLoad = true;
        return;
      }
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      inFlight = true;
      var revision = ++requestRevision;
      requestController = typeof AbortController === "function" ? new AbortController() : null;
      var requestOptions = { cache: "no-store" };
      if (requestController) requestOptions.signal = requestController.signal;
      fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/context", requestOptions)
        .then(function (r) { return r.json(); })
        .then(function (payload) {
          // A model switch can invalidate an older in-flight request. Always
          // accept the endpoint's complete response (including an actual
          // fallback model) rather than merging an optimistic selection over it.
          if (!cancelled && revision === requestRevision && payload && !payload.error) storePayload(payload);
        })
        .catch(function () {})
        .finally(function () {
          inFlight = false;
          requestController = null;
          if (cancelled) return;
          if (pendingLoad) {
            pendingLoad = false;
            load();
          } else if (running) {
            timer = setTimeout(load, 3500);
          }
        });
    }
    function applyOptimisticModel(detail) {
      if (!detail || (detail.chatId && String(detail.chatId) !== String(chatId))) return;
      var selectedModel = String(
        detail.model
        || detail.modelName
        || detail.model_name
        || detail.profile && (detail.profile.model || detail.profile.name)
        || ""
      ).trim();
      var hasContextLimit = detail.ctxLimit != null
        || detail.contextLimit != null
        || detail.profile && (
          detail.profile.ctxLimit != null
          || detail.profile.ctx_limit != null
          || detail.profile.contextLimit != null
          || detail.profile.context_limit != null
          || detail.profile.ctx != null
        );
      if (!selectedModel && !hasContextLimit) return;
      // Invalidate an earlier request before publishing the selected model.
      // The follow-up refresh replaces this snapshot with the authoritative
      // payload once the chat preference PATCH has completed.
      requestRevision += 1;
      setData(function (current) {
        var cached = WBC_LIVE_CHAT_METRICS_CACHE.get(chatId);
        var previous = current && current.chatId === chatId
          ? current.payload
          : (cached && cached.chatId === chatId ? cached.payload : {});
        var nextPayload = Object.assign({}, previous || {});
        if (selectedModel) nextPayload.model = selectedModel;
        if (hasContextLimit) {
          var rawLimit = detail.ctxLimit != null ? detail.ctxLimit : detail.contextLimit;
          var nextLimit = rawLimit != null
            ? wbcModelContextLimit({ ctxLimit: rawLimit })
            : wbcModelContextLimit(detail.profile);
          nextPayload.ctxLimit = nextLimit;
          nextPayload.ratio = nextLimit > 0 ? Number(nextPayload.ctxUsed || 0) / nextLimit : null;
        }
        var next = { chatId: chatId, payload: nextPayload };
        WBC_LIVE_CHAT_METRICS_CACHE.set(chatId, next);
        return next;
      });
    }
    function onChatModelChanged(event) {
      var detail = event && event.detail || {};
      if (detail.chatId && String(detail.chatId) !== String(chatId)) return;
      applyOptimisticModel(detail);
      if (detail.refresh !== false) load();
    }
    function onModelConfigurationChanged(event) {
      var detail = event && event.detail || {};
      // Settings changes can alter the selected profile's context window even
      // when the chat selection id itself stays stable. The endpoint resolves
      // that profile; only consume explicit per-chat details optimistically.
      if (detail.chatId && String(detail.chatId) === String(chatId)) applyOptimisticModel(detail);
      load();
    }
    window.addEventListener(WBC_CHAT_MODEL_CHANGED_EVENT, onChatModelChanged);
    window.addEventListener("cyrene:model-configuration-changed", onModelConfigurationChanged);
    load();
    return function () {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (requestController) {
        try { requestController.abort(); } catch (e) {}
      }
      window.removeEventListener(WBC_CHAT_MODEL_CHANGED_EVENT, onChatModelChanged);
      window.removeEventListener("cyrene:model-configuration-changed", onModelConfigurationChanged);
    };
  }, [chatId, updatedAt, contextRevision, running]);

  return data && data.chatId === chatId ? data.payload : null;
}

var WBC_CONTEXT_BLOCKS_CACHE = new Map();

// Same always-mounted preload contract as useWbcLiveChatMetrics: the panel
// keeps the context composition warm so opening the Context tab renders the
// latest snapshot immediately instead of flashing placeholder states.
function useWbcLiveContextBlocks(chat, running) {
  var chatId = chat ? chat.id : "";
  var [data, setData] = useWbcState(function () {
    var cached = chatId ? WBC_CONTEXT_BLOCKS_CACHE.get(chatId) : null;
    return cached && cached.chatId === chatId ? cached : null;
  });
  var updatedAt = chat ? chat.updatedAt : "";
  var contextRevision = chat ? chat.contextRevision : 0;

  useWbcEffect(function () {
    if (!chatId) { setData(null); return undefined; }
    var cancelled = false;
    var requestRevision = 0;
    var inFlight = false;
    var timer = null;
    var requestController = null;
    function load() {
      if (cancelled || inFlight) return;
      inFlight = true;
      var revision = ++requestRevision;
      requestController = typeof AbortController === "function" ? new AbortController() : null;
      var requestOptions = { cache: "no-store" };
      if (requestController) requestOptions.signal = requestController.signal;
      fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/context-blocks", requestOptions)
        .then(function (r) { return r.json(); })
        .then(function (payload) {
          if (!cancelled && revision === requestRevision && payload && !payload.error) {
            var next = { chatId: chatId, payload: payload };
            WBC_CONTEXT_BLOCKS_CACHE.set(chatId, next);
            setData(next);
          }
        })
        .catch(function (err) {
          if (!err || err.name !== "AbortError") return undefined;
        })
        .finally(function () {
          inFlight = false;
          requestController = null;
          if (!cancelled && running) timer = setTimeout(load, 3500);
        });
    }
    load();
    return function () {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (requestController) {
        try { requestController.abort(); } catch (e) {}
      }
    };
  }, [chatId, updatedAt, contextRevision, running]);

  return data && data.chatId === chatId ? data.payload : null;
}

function WbcContextUsage({ data, compact }) {

  if (!data) return null;

  var segments = Array.isArray(data.segments) ? data.segments : [];
  var segTotal = segments.reduce(function (sum, seg) { return sum + Number(seg.tokens || 0); }, 0);
  var used = Number(data.ctxUsed || 0);
  var limit = Number(data.ctxLimit || 0);
  var ratio = (typeof data.ratio === "number") ? data.ratio : (limit > 0 ? used / limit : 0);
  var triggerRatio = Number(data.compactTriggerRatio) || 0.6;
  var triggerPct = Math.round(triggerRatio * 100);
  var fillLevel = ratio >= triggerRatio ? "high" : (ratio >= triggerRatio * 0.66 ? "mid" : "low");
  var compaction = data.compaction || {};

  // A newly created conversation can have zero tokens while already having a
  // model-specific context window. Keep the 0% gauge visible so switching the
  // model updates both its name and limit immediately.
  if (segTotal <= 0 && used <= 0 && limit <= 0) {
    return (
      <section className={"workbench-side-section" + (compact ? " wbc-context-usage-compact" : "")}>
        <p className="workbench-muted">{wbcT("workbenchChat.ctx.empty", "No agent context yet.")}</p>
      </section>
    );
  }

  var legend = WBC_CTX_SEG_ORDER.map(function (key) {
    var entry = segments.find(function (seg) { return seg.key === key; });
    var tokens = entry ? Number(entry.tokens || 0) : 0;
    if (tokens <= 0) return null;
    var label = wbcT(WBC_CTX_SEG_LABEL[key][0], WBC_CTX_SEG_LABEL[key][1]);
    return { key: key, tokens: tokens, label: label, pct: (tokens / segTotal) * 100 };
  }).filter(Boolean);

  return (
    <section className={"workbench-side-section" + (compact ? " wbc-context-usage-compact" : "")}>
      <div className="wbc-ctx-gauge">
        <div className="wbc-ctx-gauge-head">
          <b>{limit > 0 ? wbcCtxPct(ratio) : wbcCompactNumber(used)}</b>
          <span>{limit > 0
            ? (wbcCompactNumber(used) + " / " + wbcCompactNumber(limit))
            : wbcT("workbenchChat.ctx.unknownLimit", "Window size unknown")}</span>
        </div>
        <div className={"wbc-ctx-bar level-" + fillLevel}>
          <span className="wbc-ctx-bar-fill" style={{ width: Math.max(1.5, Math.min(100, ratio * 100)) + "%" }} />
          {limit > 0 && (
            <span className="wbc-ctx-bar-tick" style={{ left: triggerPct + "%" }}
              title={wbcT("workbenchChat.ctx.compactAt", "Compaction triggers at {pct}%", { pct: triggerPct })} />
          )}
        </div>
        {(compaction.active
          ? <p className="wbc-ctx-note hot">{wbcT("workbenchChat.ctx.compacted", "Compressed {n} earlier block(s) · {tokens} tok", { n: compaction.blocks, tokens: wbcCompactNumber(compaction.tokens) })}</p>
          : (limit > 0 ? <p className="wbc-ctx-note">{wbcT("workbenchChat.ctx.compactAt", "Compaction triggers at {pct}%", { pct: triggerPct })}</p> : null))}
      </div>
      {legend.length > 0 && !compact && (
        <div className="wbc-ctx-split">
          <div className="wbc-ctx-split-label">{wbcT("workbenchChat.ctx.breakdown", "Context breakdown")}</div>
          <div className="wbc-ctx-splitbar">
            {legend.map(function (item) {
              return <span key={item.key} className={"wbc-ctx-seg seg-" + item.key}
                style={{ width: item.pct + "%" }}
                title={item.label + " · " + wbcCompactNumber(item.tokens) + " (" + item.pct.toFixed(1) + "%)"} />;
            })}
          </div>
          <div className="wbc-ctx-legend">
            {legend.map(function (item) {
              return (
                <span key={item.key} className="wbc-ctx-legend-item">
                  <i className={"wbc-ctx-dot seg-" + item.key} />
                  {item.label}
                  <em>{item.pct.toFixed(1)}%</em>
                </span>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}

function WbcOverviewUsage({ usage }) {
  usage = usage || {};
  var hit = Number(usage.prompt_cache_hit_tokens || 0);
  var miss = Number(usage.prompt_cache_miss_tokens || 0);
  var prompt = Number(usage.prompt_tokens || 0);
  var completion = Number(usage.completion_tokens || 0);
  var total = Number(usage.total_tokens || 0) || (prompt + completion);
  var cacheTotal = hit + miss;
  var cacheRate = cacheTotal > 0 ? Math.round(hit / cacheTotal * 100) : 0;
  return (
    <section className="workbench-side-section wbc-overview-usage" aria-label={wbcT("chat.runSummary", "Run summary")}>
      {cacheTotal > 0 && (
        <div className="wbc-overview-cache-row">
          <span>{wbcT("workbenchChat.cacheHitRate", "Cache hit rate")}</span>
          <b>{cacheRate + "%"}</b>
          <div className="wbc-overview-cache-track" role="progressbar"
            aria-label={wbcT("workbenchChat.cacheHitRate", "Cache hit rate")}
            aria-valuemin="0" aria-valuemax="100" aria-valuenow={cacheRate}>
            <i style={{ width: cacheRate + "%" }} />
          </div>
        </div>
      )}
      <div className="wbc-overview-token-grid">
        <div><span>{wbcT("workbenchChat.tokenInput", "Input")}</span><b>{prompt ? wbcCompactNumber(prompt) : "—"}</b></div>
        <div><span>{wbcT("workbenchChat.tokenOutput", "Output")}</span><b>{completion ? wbcCompactNumber(completion) : "—"}</b></div>
        <div><span>{wbcT("workbenchChat.tokenTotal", "Total")}</span><b>{total ? wbcCompactNumber(total) : "—"}</b></div>
      </div>
    </section>
  );
}

function WbcQuickActionItems({ chat, menu, onBeforeAction, onRename, onDelete, onToTask, toTaskBusy, onCompact, compactBusy, onGenerateMemory, memoryLearningBusy }) {
  function run(action) {
    return function () {
      if (onBeforeAction) onBeforeAction();
      if (action) action();
    };
  }
  var role = menu ? "menuitem" : undefined;
  return (
    <>
      <button type="button" role={role} onClick={run(onRename)}>{WBC_ICONS.edit}<span>{wbcT("workbenchChat.rename", "Rename chat")}</span></button>
      <button type="button" role={role} disabled={toTaskBusy} onClick={run(onToTask)}>{WBC_ICONS.task}<span>{wbcT(toTaskBusy ? "workbenchChat.toTaskBusy" : "workbenchChat.toTask", toTaskBusy ? "Analyzing chat…" : "Convert to task")}</span></button>
      {onCompact && (
        <button type="button" role={role} disabled={compactBusy} onClick={run(onCompact)}>
          {compactBusy ? <span className="wbc-spinner" aria-hidden="true"></span> : WBC_ICONS.compact}
          <span>{wbcT(compactBusy ? "workbenchChat.compactBusy" : "workbenchChat.compact", compactBusy ? "Compressing…" : "Compress chat")}</span>
        </button>
      )}
      {onGenerateMemory && (
        <button type="button" role={role} disabled={memoryLearningBusy} onClick={run(onGenerateMemory)}>
          {memoryLearningBusy ? <span className="wbc-spinner" aria-hidden="true"></span> : WBC_ICONS.spark}
          <span>{wbcT(memoryLearningBusy ? "workbenchChat.generateMemoryBusy" : "workbenchChat.generateMemory", memoryLearningBusy ? "Starting memory learning…" : "Generate memory")}</span>
        </button>
      )}
      <button type="button" role={role} className="danger" onClick={run(onDelete)}>{WBC_ICONS.trash}<span>{wbcT("workbenchChat.delete", "Delete chat")}</span></button>
    </>
  );
}

var WBC_SIDE_CARD_ORDER_PREFIX = "cyrene-workbench-side-card-order-v1:";

function wbcNormalizeSideCardOrder(defaultOrder, savedOrder) {
  var valid = Array.isArray(defaultOrder) ? defaultOrder.map(String) : [];
  var allowed = new Set(valid);
  var seen = new Set();
  var normalized = [];
  (Array.isArray(savedOrder) ? savedOrder : []).forEach(function (id) {
    id = String(id);
    if (!allowed.has(id) || seen.has(id)) return;
    seen.add(id);
    normalized.push(id);
  });
  valid.forEach(function (id) {
    if (!seen.has(id)) normalized.push(id);
  });
  return normalized;
}

function wbcLoadSideCardOrder(tabId, defaultOrder) {
  try {
    var saved = JSON.parse(localStorage.getItem(WBC_SIDE_CARD_ORDER_PREFIX + tabId) || "[]");
    return wbcNormalizeSideCardOrder(defaultOrder, saved);
  } catch (e) {
    return wbcNormalizeSideCardOrder(defaultOrder, []);
  }
}

function wbcMoveSideCard(order, movingId, targetId, edge) {
  var current = Array.isArray(order) ? order.slice() : [];
  if (movingId === targetId || current.indexOf(movingId) < 0 || current.indexOf(targetId) < 0) {
    return current;
  }
  var next = current.filter(function (id) { return id !== movingId; });
  var targetIndex = next.indexOf(targetId);
  next.splice(targetIndex + (edge === "after" ? 1 : 0), 0, movingId);
  return next;
}

function WbcSortableCardStack({ tabId, defaultOrder, cards }) {
  var cardList = Array.isArray(cards) ? cards : [];
  var cardMap = new Map(cardList.map(function (card) { return [card.id, card]; }));
  var dragOriginOrderRef = useWbcRef([]);
  var dropCommittedRef = useWbcRef(false);
  var [order, setOrder] = useWbcState(function () {
    return wbcLoadSideCardOrder(tabId, defaultOrder);
  });
  var [dragState, setDragState] = useWbcState(null);
  var [announcement, setAnnouncement] = useWbcState("");

  useWbcEffect(function () {
    setOrder(function (current) {
      return wbcNormalizeSideCardOrder(defaultOrder, current);
    });
    setDragState(null);
  }, [tabId, defaultOrder.join("|")]);

  function commit(nextOrder, movedId) {
    var normalized = wbcNormalizeSideCardOrder(defaultOrder, nextOrder);
    setOrder(normalized);
    try {
      localStorage.setItem(WBC_SIDE_CARD_ORDER_PREFIX + tabId, JSON.stringify(normalized));
    } catch (e) {}
    var movedCard = cardMap.get(movedId);
    if (movedCard) {
      var visibleOrder = normalized.filter(function (id) { return cardMap.has(id); });
      setAnnouncement(wbcT(
        "workbenchChat.cardMoved",
        "{title} moved to position {position} of {total}.",
        {
          title: movedCard.title,
          position: visibleOrder.indexOf(movedId) + 1,
          total: visibleOrder.length,
        }
      ));
    }
  }

  function moveByKeyboard(event, id) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    var visibleOrder = order.filter(function (cardId) { return cardMap.has(cardId); });
    var index = visibleOrder.indexOf(id);
    var nextIndex = event.key === "ArrowUp" ? index - 1 : index + 1;
    if (index < 0 || nextIndex < 0 || nextIndex >= visibleOrder.length) return;
    event.preventDefault();
    var targetId = visibleOrder[nextIndex];
    commit(wbcMoveSideCard(
      order,
      id,
      targetId,
      event.key === "ArrowUp" ? "before" : "after"
    ), id);
  }

  return (
    <div
      className="wbc-sortable-card-stack"
      onDragOver={function (event) {
        if (dragState) event.preventDefault();
      }}
      onDrop={function (event) {
        if (!dragState) return;
        event.preventDefault();
        dropCommittedRef.current = true;
        commit(order, dragState.movingId);
        setDragState(null);
      }}
    >
      {order.map(function (id) {
        var card = cardMap.get(id);
        if (!card) return null;
        return (
          <div
            className={"wbc-sortable-card" + (dragState && dragState.movingId === id ? " dragging" : "")}
            data-card-id={id}
            key={id}
            onDragOver={function (event) {
              if (!dragState || dragState.movingId === id) return;
              event.preventDefault();
              if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
              var rect = event.currentTarget.getBoundingClientRect();
              var edge = event.clientY < rect.top + (rect.height / 2) ? "before" : "after";
              var nextOrder = wbcMoveSideCard(order, dragState.movingId, id, edge);
              if (nextOrder.join("|") !== order.join("|")) setOrder(nextOrder);
              setDragState({ movingId: dragState.movingId, targetId: id, edge: edge });
            }}
            onDrop={function (event) {
              event.preventDefault();
              event.stopPropagation();
              if (!dragState) return;
              var nextOrder = dragState.movingId === id
                ? order
                : wbcMoveSideCard(order, dragState.movingId, id, dragState.edge);
              dropCommittedRef.current = true;
              commit(nextOrder, dragState.movingId);
              setDragState(null);
            }}
          >
            <button
              type="button"
              className="wbc-card-drag-handle"
              draggable="true"
              title={wbcT("workbenchChat.reorderCard", "Drag to reorder {title}. Use arrow keys to move.", { title: card.title })}
              aria-label={wbcT("workbenchChat.reorderCard", "Drag to reorder {title}. Use arrow keys to move.", { title: card.title })}
              aria-pressed={dragState && dragState.movingId === id ? "true" : "false"}
              onKeyDown={function (event) { moveByKeyboard(event, id); }}
              onDragStart={function (event) {
                dragOriginOrderRef.current = order.slice();
                dropCommittedRef.current = false;
                if (event.dataTransfer) {
                  event.dataTransfer.effectAllowed = "move";
                  event.dataTransfer.setData("text/plain", id);
                  var cardNode = event.currentTarget.closest(".wbc-sortable-card");
                  if (cardNode) {
                    var cardRect = cardNode.getBoundingClientRect();
                    event.dataTransfer.setDragImage(
                      cardNode,
                      Math.max(0, Math.min(cardRect.width, event.clientX - cardRect.left)),
                      Math.max(0, Math.min(cardRect.height, event.clientY - cardRect.top))
                    );
                  }
                }
                setDragState({ movingId: id, targetId: "", edge: "before" });
              }}
              onDragEnd={function () {
                if (!dropCommittedRef.current) setOrder(dragOriginOrderRef.current);
                dropCommittedRef.current = false;
                setDragState(null);
              }}
            >
              <span aria-hidden="true">{WBC_ICONS.dots}</span>
            </button>
            {card.content}
          </div>
        );
      })}
      <span className="wbc-sr-only" aria-live="polite">{announcement}</span>
    </div>
  );
}

function WbcOverviewTab({ chat, loading, detailed, runtime }) {
  var liveData = useWbcLiveChatMetrics(chat, !!runtime);
  if (!chat) {
    return <p className="workbench-muted">{loading
      ? wbcT("workbenchChat.loadingConversation", "Loading conversation…")
      : wbcT("workbenchChat.noMessages", "Select or create a chat.")}</p>;
  }
  var runtimeUsage = runtime && runtime.usage && typeof runtime.usage === "object" ? runtime.usage : {};
  var usage = Object.assign({}, (liveData && liveData.usage) || {}, runtimeUsage);
  if (runtime && runtime.contextUsage && typeof runtime.contextUsage === "object") {
    var context = runtime.contextUsage;
    var used = Number(context.used || 0);
    var size = Number(context.size || 0);
    liveData = Object.assign({}, liveData || {}, {
      ctxUsed: used || (liveData && liveData.ctxUsed) || 0,
      ctxLimit: size || (liveData && liveData.ctxLimit) || 0,
      ratio: size > 0 ? used / size : (liveData && liveData.ratio),
      segments: Array.isArray(context.segments) ? context.segments : (liveData && liveData.segments),
      usage: usage,
    });
  }
  var currentModel = String(
    runtime && runtime.activeModel
    || liveData && liveData.model
    || ""
  ).trim();
  var convertedTitle = chat.convertedSessionId ? String(chat.convertedTaskTitle || "").trim() : "";
  // Agent identity block (handoff §9). Legacy chats normalize to the built-in
  // Cyrene Agent; external Agents surface connection, model source and both
  // session ids. Token usage stays hidden for Agents that report none.
  var overviewAgent = wbcChatAgent(chat);
  var overviewHasAgent = !!overviewAgent;
  var overviewAgentName = wbcAgentDisplayName(overviewAgent);
  var overviewIsBuiltin = wbcIsBuiltinAgent(overviewAgent);
  var overviewExternal = overviewHasAgent && !overviewIsBuiltin;
  var overviewConnection = wbcAgentConnectionLabel(chat);
  var overviewModelSource = wbcModelAccessLabel(chat);
  var overviewExternalSessionId = String((overviewAgent && overviewAgent.externalSessionId) || "");
  var overviewShowUsage = !overviewHasAgent || overviewIsBuiltin || wbcUsageReported(usage);
  var overviewModelDisplay = currentModel || (overviewExternal
    ? wbcT("workbenchChat.modelSource.agentConfigured", "Configured by Agent")
    : "");
  return (
    <div className="wbc-overview-compact">
      {loading && <p className="workbench-muted wbc-side-loading" role="status">
        <span className="wbc-spinner" aria-hidden="true"></span>
        {wbcT("workbenchChat.loadingConversation", "Loading conversation…")}
      </p>}
      <section className="workbench-side-section wbc-overview-session">
        <div className="wbc-overview-state-row">
          <span>{wbcT("workbenchChat.statusLabel", "Status")}</span>
          <b className={"wbc-overview-status" + (runtime ? " live" : "")}>
            {runtime ? wbcT("workbenchChat.status.replying", "Replying") : wbcT("workbenchChat.status.idle", "Idle")}
          </b>
        </div>
        {overviewExternal && (
          <div className="wbc-overview-agent-block">
            <div className="wbc-overview-agent-row">
              <span>{wbcT("workbenchChat.agent", "Agent")}</span>
              <b>
                <button
                  type="button"
                  className="wbc-overview-agent-name"
                  onClick={function () { wbcOpenAgentDetail(overviewAgent); }}
                  title={wbcT("workbenchChat.agentOpenDetail", "Open Agent details")}
                >
                  <span>{overviewAgentName}</span>
                  <span className="wbc-overview-agent-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
                </button>
              </b>
            </div>
            <div><span>{wbcT("workbenchChat.connection", "Connection")}</span><b>{overviewConnection || "—"}</b></div>
            <div><span>{wbcT("workbenchChat.modelSource", "Model source")}</span><b>{overviewModelSource || "—"}</b></div>
            <div><span>{wbcT("workbenchChat.model", "Model")}</span><b className="wbc-kv-mono" title={overviewModelDisplay || ""}>{overviewModelDisplay || "—"}</b></div>
            {chat.agentMode != null ? <div><span>{wbcT("workbenchChat.agentMode", "Agent mode")}</span><b>{wbcStructuredEventSummary(chat.agentMode) || "—"}</b></div> : null}
            <div><span>{wbcT("workbenchChat.externalSessionId", "Agent session ID")}</span><b className="wbc-kv-mono" title={overviewExternalSessionId}>{overviewExternalSessionId || "—"}</b></div>
            <div><span>{wbcT("workbenchChat.cyreneChatId", "Cyrene chat ID")}</span><b className="wbc-kv-mono" title={chat.id}>{chat.id}</b></div>
          </div>
        )}
        <div className="wbc-overview-details">
          {overviewHasAgent && overviewIsBuiltin && (
            <div>
              <span>{wbcT("workbenchChat.agent", "Agent")}</span>
              <b>{overviewAgentName}</b>
            </div>
          )}
          {!overviewExternal && <div><span>{wbcT("workbenchChat.model", "Model")}</span><b className="wbc-kv-mono" title={currentModel || ""}>{currentModel || "—"}</b></div>}
          {!overviewExternal && <div><span>{wbcT("chat.runId", "Session ID")}</span><b className="wbc-kv-mono" title={chat.id}>{chat.id}</b></div>}
        </div>
        <div className="wbc-overview-facts">
          <div>
            <span>{wbcT("workbenchChat.messageCount", "Messages")}</span>
            <b>{liveData && liveData.messageCount != null ? liveData.messageCount : 0}</b>
          </div>
          <div>
            <span>{wbcT("workbenchChat.createdAt", "Created")}</span>
            <b>{wbcFormatTime(chat.createdAt) || "—"}</b>
          </div>
        </div>
      </section>
      {overviewShowUsage ? <WbcOverviewUsage usage={usage} /> : null}
      {detailed && liveData && <WbcContextUsage data={liveData} compact={true} />}
      {convertedTitle && (
        <section className="workbench-side-section wbc-overview-converted">
          <p className="wbc-converted-note">{wbcT("workbenchChat.convertedNote", "Converted to task")}：<b>{convertedTitle}</b></p>
        </section>
      )}
    </div>
  );
}

function wbcBlockLabel(block) {
  var id = block.id || "";
  var key = "workbenchChat.ctxBlock." + id;
  var label = wbcT(key, "");
  // An empty fallback intentionally asks the i18n layer whether this dynamic
  // key exists. wbcT returns the key itself when it does not, so never render
  // that value as user-facing copy.
  if (label && label !== key) return label;
  // Context kinds may be supplied by user plugins, so unknown kinds use one
  // stable label instead of leaking a generated identifier.
  if (id.startsWith("context.")) return wbcT("workbenchChat.ctxBlock.context", "Turn context");
  return id;
}

// Data is supplied by the panel-level useWbcLiveContextBlocks preload; this
// stays a pure renderer so expanding the Context tab never waits on a fetch.
function WbcContextLayerDisclosure({ layerId, label, tokens, items }) {
  var [open, setOpen] = useWbcState(true);
  return React.createElement("div", {
      className: "wbc-ctx-layer-detail" + (open ? " is-open" : " is-collapsed"),
    },
    React.createElement("button", {
        type: "button",
        className: "wbc-ctx-legend-layer-head",
        "aria-expanded": open ? "true" : "false",
        "aria-controls": "wbc-context-layer-" + layerId,
        onClick: function () { setOpen(function (current) { return !current; }); },
      },
      React.createElement("i", { className: "wbc-ctx-dot seg-" + layerId, "aria-hidden": "true" }),
      React.createElement("span", null, label),
      React.createElement("em", null, wbcCompactNumber(tokens)),
      React.createElement("span", { className: "wbc-ctx-layer-chevron", "aria-hidden": "true" }, WBC_ICONS.chevronDown)
    ),
    React.createElement("div", {
        id: "wbc-context-layer-" + layerId,
        className: "wbc-ctx-layer-collapse",
        "aria-hidden": open ? "false" : "true",
      },
      React.createElement("div", { className: "wbc-ctx-layer-collapse-inner" },
        React.createElement("div", { className: "wbc-ctx-legend-layer-body" },
          items.map(function (item) {
            return React.createElement("div", { key: item.key, className: "wbc-ctx-legend-item" },
              React.createElement("i", { className: "wbc-ctx-dot " + item.dotClass }),
              React.createElement("span", null, item.label),
              React.createElement("em", null, wbcCompactNumber(item.tokens))
            );
          })
        )
      )
    )
  );
}

function WbcContextBlockList({ data, compact }) {
  if (!data || !Array.isArray(data.layers) || data.layers.length === 0) {
    return React.createElement("p", { className: "workbench-muted" },
      wbcT("workbenchChat.ctxBlocks.empty", "Send a message and the context composition will appear here."));
  }

  var layers = data.layers;
  // Total for the bar: include all layers (system + ephemeral + messages)
  var barTotal = layers.reduce(function (sum, l) { return sum + (Number(l.totalTokens) || 0); }, 0);
  // Use the same complete next-request context total shown in Overview.
  var total = Number(data.contextUsed);
  if (!Number.isFinite(total) || total < 0) total = barTotal;

  // Build legend: explode system_prefix and messages sub-blocks for the bar
  var SYS_SHADE_MAP = {
    identity: 0,
    instructions: 2,
    tools: 3,
    workspace: 4,
    memory: 1,
    runtime: 5,
    system: 6,
  };
  function sysShadeForBlock(b) {
    var t = b.type || "";
    return SYS_SHADE_MAP[t] != null ? SYS_SHADE_MAP[t] : 7;
  }
  function msgSubClass(b) {
    var key = b.type || "";
    return "sub msg-sub seg-" + key;
  }
  function sysSubClass(b) {
    var shade = sysShadeForBlock(b);
    return "sub sys-sub sys-sub-" + shade;
  }
  // Enforce consistent order: system_prefix → ephemeral → messages
  var LAYER_ORDER = ["system_prefix", "ephemeral", "messages"];
  var orderedLayers = LAYER_ORDER.map(function (id) {
    return layers.find(function (l) { return l.id === id; });
  }).filter(Boolean);
  // Append any unknown layers at the end
  layers.forEach(function (l) { if (LAYER_ORDER.indexOf(l.id) === -1) orderedLayers.push(l); });

  if (compact) {
    var compactLayers = orderedLayers.map(function (layer) {
      var tokens = Number(layer.totalTokens) || 0;
      if (tokens <= 0) return null;
      return {
        id: layer.id,
        label: wbcT("workbenchChat.ctxBlocks.layer." + layer.id, layer.label),
        tokens: tokens,
        pct: barTotal > 0 ? (tokens / barTotal) * 100 : 0,
      };
    }).filter(Boolean);
    return React.createElement("div", { className: "wbc-context-layer-summary" },
      React.createElement("div", { className: "wbc-ctx-gauge-head" },
        React.createElement("b", null, wbcCompactNumber(total)),
        React.createElement("span", null, wbcT("workbenchChat.ctxBlocks.totalTokens", "tokens"))
      ),
      compactLayers.length > 0 && React.createElement("div", { className: "wbc-ctx-split" },
        React.createElement("div", { className: "wbc-ctx-splitbar" },
          compactLayers.map(function (item) {
            return React.createElement("span", {
              key: item.id,
              className: "wbc-ctx-seg seg-" + item.id,
              style: { width: Math.max(1.5, item.pct) + "%" },
              title: item.label + " · " + wbcCompactNumber(item.tokens),
            });
          })
        ),
        React.createElement("div", { className: "wbc-context-layer-list" },
          compactLayers.map(function (item) {
            return React.createElement("div", { key: item.id, className: "wbc-ctx-legend-item" },
              React.createElement("i", { className: "wbc-ctx-dot seg-" + item.id }),
              React.createElement("span", null, item.label),
              React.createElement("em", null, wbcCompactNumber(item.tokens))
            );
          })
        )
      )
    );
  }

  function _ctxSegFromBlock(b, isMsg) {
    var t = Number(b.tokens_est) || 0;
    if (t <= 0) return null;
    var key = isMsg ? (b.type || "") : (b.id || "");
    var label = isMsg
      ? wbcT("workbenchChat.ctx.seg." + key, WBC_CTX_SEG_LABEL[key] && WBC_CTX_SEG_LABEL[key][1] || key)
      : wbcBlockLabel(b);
    var dotClass = isMsg ? msgSubClass(b) : sysSubClass(b);
    return { key: key, tokens: t, label: label, dotClass: dotClass };
  }

  var segItems = [];
  orderedLayers.forEach(function (layer) {
    var tokens = Number(layer.totalTokens) || 0;
    if (tokens <= 0) return;
    var blocks = Array.isArray(layer.blocks) ? layer.blocks : [];
    var isMsg = layer.id === "messages";
    var isSys = layer.id === "system_prefix";
    var explode = (isMsg || isSys) && blocks.length > 0;
    if (explode) {
      blocks.forEach(function (b) {
        var seg = _ctxSegFromBlock(b, isMsg);
        if (!seg) return;
        var pct = barTotal > 0 ? (seg.tokens / barTotal) * 100 : 0;
        segItems.push({ id: layer.id + "-" + seg.key, tokens: seg.tokens, label: seg.label, pct: pct, dotClass: seg.dotClass });
      });
    } else {
      var label = wbcT("workbenchChat.ctxBlocks.layer." + layer.id, layer.label);
      var pct = barTotal > 0 ? (tokens / barTotal) * 100 : 0;
      segItems.push({ id: layer.id, tokens: tokens, label: label, pct: pct, dotClass: "seg-" + layer.id });
    }
  });

  return React.createElement("div", { className: "wbc-context-detail" },
    // Gauge head
    React.createElement("div", { className: "wbc-ctx-gauge-head" },
      React.createElement("div", { className: "wbc-ctx-token-total" },
        React.createElement("b", null, wbcCompactNumber(total))
      ),
      React.createElement("span", null, Number(data.contextLimit || 0) > 0
        ? ("/ " + wbcCompactNumber(data.contextLimit) + " " + wbcT("workbenchChat.ctxBlocks.totalTokens", "tokens"))
        : wbcT("workbenchChat.ctxBlocks.totalTokens", "tokens"))
    ),
    // Split bar
    segItems.length > 0 && React.createElement("div", { className: "wbc-ctx-split" },
      React.createElement("div", { className: "wbc-ctx-splitbar" },
        segItems.map(function (item) {
          return React.createElement("span", {
            key: item.id,
            className: "wbc-ctx-seg " + (item.dotClass || ""),
            style: { width: Math.max(1.5, item.pct) + "%" },
            title: item.label + " · " + wbcCompactNumber(item.tokens),
          });
        })
      ),
      // Grouped legend: layer headers with sub-item color→name→tokens
      React.createElement("div", { className: "wbc-ctx-legend-group" },
        orderedLayers.map(function (layer) {
          var tokens = Number(layer.totalTokens) || 0;
          if (tokens <= 0) return null;
          var blocks = Array.isArray(layer.blocks) ? layer.blocks : [];
          var isMsg = layer.id === "messages";
          var isSys = layer.id === "system_prefix";
          var layerLabel = wbcT("workbenchChat.ctxBlocks.layer." + layer.id, layer.label);
          if (!(isMsg || isSys) || blocks.length === 0) {
            return React.createElement("div", { key: layer.id, className: "wbc-ctx-layer-row" },
              React.createElement("i", { className: "wbc-ctx-dot seg-" + layer.id, "aria-hidden": "true" }),
              React.createElement("span", null, layerLabel),
              React.createElement("em", null, wbcCompactNumber(tokens))
            );
          }
          var items = blocks.map(function (b) {
            return _ctxSegFromBlock(b, isMsg);
          }).filter(Boolean);
          return React.createElement(WbcContextLayerDisclosure, {
            key: layer.id,
            layerId: layer.id,
            label: layerLabel,
            tokens: tokens,
            items: items,
          });
        })
      )
    )
  );
}

var WBC_INBOX_CACHE_LIMIT = 32;
var wbcInboxSnapshotCache = new Map();

function wbcCachedInbox(chatId) {
  return chatId ? (wbcInboxSnapshotCache.get(String(chatId)) || null) : null;
}

function wbcCacheInbox(chatId, payload) {
  var key = String(chatId || "");
  if (!key || !payload) return;
  // Refresh insertion order so the least-recently-viewed conversation is
  // evicted first. Inbox snapshots are small, but the chat list is unbounded.
  wbcInboxSnapshotCache.delete(key);
  wbcInboxSnapshotCache.set(key, payload);
  if (wbcInboxSnapshotCache.size > WBC_INBOX_CACHE_LIMIT) {
    var oldestKey = wbcInboxSnapshotCache.keys().next().value;
    if (oldestKey) wbcInboxSnapshotCache.delete(oldestKey);
  }
}

function useWbcLiveInbox(chat, activeHint) {
  var chatId = chat ? chat.id : "";
  var [retryRevision, setRetryRevision] = useWbcState(0);
  var [view, setView] = useWbcState(function () {
    var cached = wbcCachedInbox(chatId);
    return { chatId: chatId, data: cached, loading: !!chatId && !cached, error: "" };
  });

  useWbcEffect(function () {
    if (!chatId) {
      setView({ chatId: "", data: null, loading: false, error: "" });
      return undefined;
    }
    var cancelled = false;
    var inFlight = false;
    var timer = null;
    var requestController = null;
    setView(function (previous) {
      var nextData = previous.chatId === chatId
        ? previous.data
        : wbcCachedInbox(chatId);
      return {
        chatId: chatId,
        data: nextData,
        // A cached snapshot remains visible while the fresh request runs.
        // Loading UI is reserved for the first visit with no usable state.
        loading: !nextData,
        error: "",
      };
    });

    function schedule(delay) {
      if (cancelled) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(load, delay);
    }

    function load() {
      if (cancelled || inFlight) return;
      inFlight = true;
      // Keep exactly one poll alive for this hook instance. Aborting on cleanup
      // prevents a retry/chat switch from leaving an obsolete fetch behind;
      // the cancelled check below also protects the cache if a transport races
      // with abort after it has already received the response.
      requestController = typeof AbortController !== "undefined" ? new AbortController() : null;
      var nextDelay = 1000;
      var requestOptions = {
        toast: false,
        timeout: 5000,
        cache: "no-store",
      };
      if (requestController) requestOptions.signal = requestController.signal;
      WorkbenchChatModel.getInbox(chatId, requestOptions)
        .then(function (payload) {
          nextDelay = (payload && payload.active) || activeHint ? 1000 : 5000;
          if (!cancelled) {
            wbcCacheInbox(chatId, payload);
            setView({ chatId: chatId, data: payload, loading: false, error: "" });
          }
        })
        .catch(function (err) {
          if (!cancelled && (!err || err.name !== "AbortError")) {
            setView(function (previous) {
              return {
                chatId: chatId,
                data: previous.chatId === chatId ? previous.data : null,
                loading: false,
                error: wbcErrorText(err),
              };
            });
          }
        })
        .finally(function () {
          inFlight = false;
          requestController = null;
          schedule(nextDelay);
        });
    }

    load();
    // Agent-to-agent messages can arrive independently of the transcript, so
    // keep observing while the Context tab is mounted.
    return function () {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (requestController) {
        try { requestController.abort(); } catch (e) {}
      }
    };
  }, [chatId, retryRevision, activeHint]);

  var cachedData = wbcCachedInbox(chatId);
  var currentData = view.chatId === chatId ? view.data : cachedData;
  return {
    data: currentData,
    loading: currentData ? false : (view.chatId === chatId ? view.loading : true),
    error: view.chatId === chatId ? view.error : "",
    retry: function () { setRetryRevision(function (value) { return value + 1; }); },
  };
}

function wbcInboxStatus(status) {
  var value = String(status || "ready") === "consumed" ? "consumed" : "ready";
  var labels = {
    ready: ["workbenchChat.inbox.status.ready", "Ready"],
    consumed: ["workbenchChat.inbox.status.consumed", "Consumed"],
  };
  var item = labels[value];
  return { value: value, label: wbcT(item[0], item[1]) };
}

function wbcInboxEventLabel(item) {
  return item.messageType === "result" || item.messageType === "task_result"
    ? wbcT("workbenchChat.inbox.subagentResult", "Subagent result")
    : wbcT("workbenchChat.inbox.agentMessage", "Agent message");
}

// The Agent inbox view is produced by the panel-level useWbcLiveInbox preload
// so the card renders the latest snapshot the moment the tab opens.
function WbcInboxCard({ liveView, hideTitle }) {
  var data = liveView.data;
  var counts = (data && data.counts) || {};
  var events = data && Array.isArray(data.events) ? data.events : [];
  var feed = events.slice().sort(function (left, right) {
    return String(right.createdAt || "").localeCompare(String(left.createdAt || ""));
  });
  var queueDepth = !data
    ? null
    : Number(data.queueDepth != null ? data.queueDepth : counts.ready || 0);
  var historyTruncated = !!(data && (
    data.eventsTruncated || data.historyWindowTruncated
  ));

  return (
    <section className={"workbench-side-section wbc-inbox-card" + (hideTitle ? " title-hidden" : "")} aria-labelledby={hideTitle ? undefined : "wbc-inbox-title"} aria-label={hideTitle ? wbcT("workbenchChat.inbox.title", "Session inbox") : undefined}>
      <div className="wbc-inbox-head">
        {!hideTitle ? (
          <h3 id="wbc-inbox-title">{wbcT("workbenchChat.inbox.title", "Session inbox")}</h3>
        ) : (
          <span className="wbc-context-empty-label">{wbcT("workbenchChat.inbox.title", "Session inbox")}</span>
        )}
        <span className={"wbc-inbox-queue-count" + (queueDepth !== null && queueDepth > 0 ? " active" : "")} aria-live="polite">
          {queueDepth === 0 && !historyTruncated ? (
            <span>{wbcT("workbenchChat.inbox.queueEmpty", "Queue empty")}</span>
          ) : queueDepth === 0 ? (
            <span>{wbcT("workbenchChat.inbox.visibleWindow", "Recent window")}</span>
          ) : (
            <React.Fragment>
              <span>{wbcT("workbenchChat.inbox.queue", "In queue")}</span>
              <b>{queueDepth === null ? "—" : queueDepth}</b>
            </React.Fragment>
          )}
        </span>
      </div>

      {liveView.loading && !data ? (
        <div className="wbc-inbox-skeleton" role="status" aria-label={wbcT("workbenchChat.inbox.loading", "Loading inbox") }>
          <span /><span /><span />
        </div>
      ) : (
        <React.Fragment>
          {liveView.error ? (
            <div className="wbc-inbox-error" role="alert">
              <span>{liveView.error}</span>
              <button type="button" onClick={liveView.retry}>{wbcT("workbenchChat.error.retry", "Retry")}</button>
            </div>
          ) : feed.length === 0 ? (
            <div className="wbc-side-empty">
              <p>{historyTruncated
                ? wbcT("workbenchChat.inbox.visibleEmpty", "No inbox events are present in the visible history window.")
                : wbcT("workbenchChat.inbox.empty", "No inbox events for this run yet.")}</p>
            </div>
          ) : (
            <div className="wbc-inbox-feed">
              {feed.map(function (item) {
                var status = wbcInboxStatus(item.status);
                return (
                  <article className="wbc-inbox-row" key={item.eventId}>
                    <div className="wbc-inbox-event-body">
                      <div className="wbc-inbox-event-head">
                        <b>{wbcInboxEventLabel(item)}</b>
                        <span className={"wbc-inbox-status status-" + status.value}><i aria-hidden="true" />{status.label}</span>
                      </div>
                      <div className="wbc-inbox-event-meta">
                        {item.preview && (
                          <p
                            className="wbc-inbox-event-preview"
                            title={item.preview}
                          >
                            {item.preview}
                          </p>
                        )}
                        {item.createdAt && <time dateTime={item.createdAt} title={item.createdAt}>{wbcFormatTime(item.createdAt)}</time>}
                      </div>
                    </div>
                  </article>
                );
              })}
              {historyTruncated && (
                <p className="workbench-muted">{wbcT(
                  "workbenchChat.inbox.historyTruncated",
                  "Showing the most recent inbox events."
                )}</p>
              )}
            </div>
          )}
        </React.Fragment>
      )}
    </section>
  );
}

function wbcUsedPluginPacks(contextBlocks) {
  var used = [];
  var seen = new Set();
  function addReportedPackage(value) {
    var name = String(value || "").trim();
    if (!name || seen.has(name)) return;
    seen.add(name);
    used.push(name);
  }
  (contextBlocks && Array.isArray(contextBlocks.usedPluginPacks)
    ? contextBlocks.usedPluginPacks
    : []).forEach(addReportedPackage);
  return used;
}

function WbcContextTab({ chat, contextBlocks, inboxView }) {
  var usedPluginPacks = wbcUsedPluginPacks(contextBlocks);
  var messageCount = contextBlocks && contextBlocks.messageCount != null
    ? Number(contextBlocks.messageCount) || 0
    : 0;
  var contextUpdatedAt = contextBlocks ? String(contextBlocks.updatedAt || "") : "";
  var conversationTitle = wbcT("workbenchChat.conversationContext", "Conversation context");
  var externalAgent = !!wbcChatAgent(chat) && !wbcIsBuiltinAgent(wbcChatAgent(chat));
  return (
    <div className="wbc-context-sections">
      <section className="workbench-side-section" aria-label={conversationTitle}>
        <WbcContextBlockList data={contextBlocks} compact={false} />
      </section>
      {!externalAgent && <WbcInboxCard liveView={inboxView} hideTitle={true} />}
      {!externalAgent && <section className="workbench-side-section" aria-label={wbcT("workbenchChat.usedPluginPacks", "Used Plugin packs")}>
        <div className="wbc-context-empty-head wbc-plugin-pack-head">
          <span className="wbc-context-empty-label">{wbcT("workbenchChat.usedPluginPacks", "Used Plugin packs")}</span>
          {usedPluginPacks.length === 0 ? <b>{wbcT("workbenchChat.notUsed", "Not used")}</b> : null}
        </div>
        {usedPluginPacks.length === 0 ? (
          <div className="wbc-context-empty-module">
            <div className="wbc-side-empty">
              <p>{wbcT("workbenchChat.noUsedPluginPacks", "The agent has not used a Plugin pack in this chat.")}</p>
            </div>
          </div>
        ) : usedPluginPacks.map(function (packId) {
            return (
              <div className="workbench-check wbc-plugin-pack-row" key={packId}>
                <span className="workbench-status-dot green" aria-hidden="true"></span>
                <span>{packId}</span>
              </div>
            );
          })}
      </section>}
      <section className="workbench-side-section wbc-context-stats" aria-label={wbcT("workbenchChat.stats", "Chat stats")}>
        <div className="wb-kv"><span>{wbcT("workbenchChat.messageCount", "Messages")}</span><b>{messageCount}</b></div>
        <div className="wb-kv"><span>{wbcT("workbenchChat.updatedAt", "Last updated")}</span><b>{wbcFormatTime(contextUpdatedAt) || "—"}</b></div>
      </section>
    </div>
  );
}

function WbcArtifactsTab({ chat, files: providedFiles, emptyKey, emptyFallback, onSelectArtifact }) {
  var files = Array.isArray(providedFiles) ? providedFiles : wbcChatArtifactFiles(chat);
  return (
    <div className="wbc-artifact-list">
        {files.length === 0 && <p className="workbench-muted">{wbcT(emptyKey || "workbenchChat.noFiles", emptyFallback || "This chat has not produced files yet. Uploads and agent-generated files will appear here.")}</p>}
        {files.map(function (item, i) {
          var file = item.file;
          return (
            <button
              type="button"
              className="wbc-artifact-list-row"
              key={(file.id || file.url || i) + "_" + i}
              draggable="true"
              onDragStart={function (event) { wbcStartFileDrag(event, file); }}
              onClick={function () { if (onSelectArtifact) onSelectArtifact(file); }}
              title={wbcT("workbenchChat.openFilePreview", "Open file preview")}
            >
              <span className="wbc-artifact-list-icon" aria-hidden="true">{WBC_ICONS.file}</span>
              <span className="wbc-artifact-list-copy">
                <b>{file.name || "file"}</b>
                <small title={file.path || ""}>{file.path || (item.role === "user" ? wbcT("workbenchChat.userUpload", "User upload") : wbcT("workbenchChat.agentGenerated", "Agent generated"))}</small>
              </span>
              <span className="wbc-artifact-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            </button>
          );
        })}
    </div>
  );
}

function WbcDetachedPaneApp() {
  var bridge = window.cyrene && window.cyrene.detachedPane;
  var [context, setContext] = useWbcState(null);
  var [loadError, setLoadError] = useWbcState("");
  var [subagents, setSubagents] = useWbcState(null);
  var [subagentsLoading, setSubagentsLoading] = useWbcState(false);
  var [returnHover, setReturnHover] = useWbcState(false);
  var [windowMaximized, setWindowMaximized] = useWbcState(false);
  var returnPointerRef = useWbcRef(null);

  function closeWindow() {
    if (bridge && typeof bridge.close === "function") bridge.close();
  }

  function toggleWindowMaximize() {
    if (!bridge || typeof bridge.toggleMaximize !== "function") return;
    Promise.resolve(bridge.toggleMaximize()).then(function (result) {
      if (result && typeof result.maximized === "boolean") setWindowMaximized(result.maximized);
    }).catch(function () {});
  }

  function updateDescriptor(updates) {
    setContext(function (current) {
      if (!current) return current;
      return Object.assign({}, current, updates || {});
    });
    if (bridge && typeof bridge.updateContext === "function") {
      bridge.updateContext(updates || {}).catch(function () {});
    }
  }

  function beginReturnDrag(event) {
    if (event.button !== 0 || event.target.closest("button")) return;
    var target = event.currentTarget;
    var rect = document.body.getBoundingClientRect();
    returnPointerRef.current = event.pointerId;
    if (typeof target.setPointerCapture === "function") {
      try { target.setPointerCapture(event.pointerId); } catch (e) {}
    }
    if (bridge && typeof bridge.returnBegin === "function") {
      bridge.returnBegin({
        grab: { x: event.clientX - rect.left, y: event.clientY - rect.top },
      }).catch(function () {});
    }
    if (bridge && typeof bridge.returnMove === "function") {
      bridge.returnMove({ screenX: event.screenX, screenY: event.screenY });
    }
    event.preventDefault();
  }

  function moveReturnDrag(event) {
    if (returnPointerRef.current !== event.pointerId || !event.buttons) return;
    if (bridge && typeof bridge.returnMove === "function") {
      bridge.returnMove({ screenX: event.screenX, screenY: event.screenY });
    }
  }

  function finishReturnDrag(event) {
    if (returnPointerRef.current !== event.pointerId) return;
    returnPointerRef.current = null;
    if (bridge && typeof bridge.returnEnd === "function") {
      bridge.returnEnd({ screenX: event.screenX, screenY: event.screenY }).catch(function () {});
    }
  }

  useWbcEffect(function () {
    var disposed = false;
    if (!bridge || typeof bridge.getContext !== "function") {
      setLoadError(wbcT("workbenchChat.detachedUnavailable", "This pane cannot be opened in a separate window."));
      return undefined;
    }
    bridge.getContext().then(function (result) {
      if (disposed) return;
      if (!result || result.ok === false || !result.descriptor) {
        setLoadError(wbcT("workbenchChat.detachedUnavailable", "This pane cannot be opened in a separate window."));
        return;
      }
      var descriptor = result.descriptor;
      if ((descriptor.kind === "file" || descriptor.kind === "viewer") && descriptor.draft) {
        var draftKey = wbcProjectFileDraftKey(descriptor.payload);
        if (draftKey) WBC_PROJECT_FILE_DRAFTS[draftKey] = descriptor.draft;
      }
      setContext(descriptor);
      if (typeof bridge.ready === "function") {
        window.requestAnimationFrame(function () {
          window.requestAnimationFrame(function () { bridge.ready().catch(function () {}); });
        });
      }
    }).catch(function (error) {
      if (!disposed) setLoadError(wbcErrorText(error));
    });
    return function () { disposed = true; };
  }, []);

  useWbcEffect(function () {
    if (!bridge || typeof bridge.onReturnHover !== "function") return undefined;
    return bridge.onReturnHover(setReturnHover);
  }, []);

  useWbcEffect(function () {
    var chatId = context && context.kind === "subagents" ? String(context.ownerChatId || "") : "";
    if (!chatId) return undefined;
    var disposed = false;
    setSubagentsLoading(true);
    WorkbenchChatModel.getSubagents(chatId, "", { toast: false }).then(function (data) {
      if (!disposed) setSubagents(data);
    }).catch(function () {
      if (!disposed) setSubagents({ rounds: [], activeRoundId: "", agents: [], messages: [] });
    }).finally(function () {
      if (!disposed) setSubagentsLoading(false);
    });
    return function () { disposed = true; };
  }, [context && context.kind, context && context.ownerChatId]);

  useWbcEffect(function () {
    if (!context || context.kind !== "browser" || !context.ownerChatId) return undefined;
    var browserBridge = window.cyrene && window.cyrene.browser;
    if (browserBridge && typeof browserBridge.setContext === "function") {
      browserBridge.setContext({ sessionId: context.ownerChatId }).catch(function () {});
    }
    return undefined;
  }, [context && context.kind, context && context.ownerChatId]);

  function openContent(type, payload) {
    var kind = type === "artifact" ? "file" : String(type || "");
    if (!kind) return;
    updateDescriptor({
      kind: kind,
      payload: payload,
      title: payload && (payload.name || payload.path || payload.title) || "",
      items: kind === "file" || kind === "viewer" ? [{ file: payload, role: "assistant" }] : [],
    });
  }

  function renderContent() {
    if (!context) return null;
    var kind = context.kind;
    var ownerChatId = String(context.ownerChatId || "");
    if (kind === "chat") {
      return <WbcChatSplit
        chatId={String(context.payload || "")}
        project={context.project || null}
        onOpenContent={openContent}
        browserActiveByChat={{}}
        onClose={closeWindow}
        onDeleted={closeWindow}
        onOpenInMain={function () {}}
      />;
    }
    if (kind === "task") {
      var TaskPane = window.CyreneTaskPane;
      return TaskPane ? <TaskPane
        taskId={String(context.payload || "")}
        project={context.project || null}
        detached={true}
      /> : <div className="wbc-detached-pane-error">{wbcT("workbenchChat.detachedUnavailable", "This pane cannot be opened in a separate window.")}</div>;
    }
    if (kind === "file" || kind === "viewer") {
      var files = Array.isArray(context.items) && context.items.length
        ? context.items
        : [{ file: context.payload, role: "assistant" }];
      return <WbcArtifactSplit
        file={context.payload}
        items={files}
        label={wbcT("workbenchChat.viewer", "Viewer")}
        onSelect={function (file) { updateDescriptor({ payload: file, title: file && file.name || "" }); }}
        onClose={closeWindow}
      />;
    }
    if (kind === "change") {
      return <WbcChangeSplit
        change={context.payload}
        onSelect={function (change) { updateDescriptor({ payload: change, title: change && change.path || "" }); }}
        onClose={closeWindow}
      />;
    }
    if (kind === "map") {
      return <WbcMapPaneContent
        chatId={ownerChatId}
        item={context.payload}
        onSelect={function (item) { updateDescriptor({ payload: item, title: wbcMapItemLabel(item) }); }}
        onClose={closeWindow}
      />;
    }
    if (kind === "browser") {
      return <WbcBrowserSplit
        active={true}
        tabId={String(context.payload || "")}
        tabs={[]}
        browserState={null}
        browserSessionId={ownerChatId}
        onSelect={function (tabId) { updateDescriptor({ payload: tabId }); }}
        onClose={closeWindow}
        onTakeoverComplete={function () { return Promise.resolve(); }}
      />;
    }
    if (kind === "subagents") {
      return <aside className="wbc-side-agent-split wbc-subagents-split">
        <div className="wbc-resource-split-body wbc-subagents-split-body">
          <WbcSubagentsTab
            data={subagents}
            loading={subagentsLoading}
            onSelectRound={function (roundId) {
              setSubagentsLoading(true);
              WorkbenchChatModel.getSubagents(ownerChatId, roundId, { toast: false }).then(setSubagents).catch(function () {}).finally(function () { setSubagentsLoading(false); });
            }}
          />
        </div>
      </aside>;
    }
    if (kind === "terminal") {
      var TerminalPane = workbenchServices.terminal().Pane;
      return <TerminalPane terminalId={String(context.payload || "")} />;
    }
    if (kind === "plugin-view") {
      return <section className="wbc-plugin-view-pane detached">
        <PluginView
          projectId={String(context.payload && context.payload.projectId || context.project && context.project.id || "")}
          payload={context.payload}
        />
      </section>;
    }
    if (kind === "side-agent") {
      var agents = Array.isArray(context.agents) ? context.agents : [];
      var agent = context.agent || agents.find(function (candidate) {
        return String(candidate && candidate.id || "") === String(context.payload || "");
      }) || null;
      return agent ? <WbcSideAgentSplit
        agent={agent}
        agents={agents}
        project={context.project || null}
        onOpenFile={function (file) { openContent("file", file); }}
        onUpdate={function (next) {
          var nextAgents = agents.map(function (candidate) { return candidate.id === next.id ? next : candidate; });
          updateDescriptor({ agent: next, agents: nextAgents });
        }}
        onSelect={function (agentId) {
          var next = agents.find(function (candidate) { return String(candidate.id || "") === String(agentId || ""); }) || null;
          updateDescriptor({ payload: agentId, agent: next });
        }}
        onClose={closeWindow}
      /> : null;
    }
    return null;
  }

  return (
    <main className="workbench-shell wbc-detached-pane-window">
      <header
        className={"wbc-detached-pane-titlebar" + (returnHover ? " merge-target" : "")}
        onPointerDown={beginReturnDrag}
        onPointerMove={moveReturnDrag}
        onPointerUp={finishReturnDrag}
        onPointerCancel={finishReturnDrag}
      >
        <span className="wbc-detached-pane-title" title={context && context.title || "Cyrene"}>{context && context.title || "Cyrene"}</span>
        <span
          className="wbc-detached-pane-window-actions"
          onPointerDown={function (event) { event.stopPropagation(); }}
          onDoubleClick={function (event) { event.stopPropagation(); }}
        >
          <button
            type="button"
            className="minimize"
            onClick={function () { bridge && bridge.minimize && bridge.minimize(); }}
            aria-label={wbcT("common.minimize", "Minimize")}
            title={wbcT("common.minimize", "Minimize")}
          >{WBC_ICONS.windowMinimize}</button>
          <button
            type="button"
            className="maximize"
            onClick={toggleWindowMaximize}
            aria-label={wbcT(windowMaximized ? "common.restore" : "common.maximize", windowMaximized ? "Restore" : "Maximize")}
            title={wbcT(windowMaximized ? "common.restore" : "common.maximize", windowMaximized ? "Restore" : "Maximize")}
          >{windowMaximized ? WBC_ICONS.windowRestore : WBC_ICONS.windowMaximize}</button>
          <button
            type="button"
            className="close"
            onClick={closeWindow}
            aria-label={wbcT("common.close", "Close")}
            title={wbcT("common.close", "Close")}
          >{WBC_ICONS.x}</button>
        </span>
      </header>
      <section className="wbc-detached-pane-content" data-pane-kind={context && context.kind || ""}>
        {loadError ? <div className="wbc-detached-pane-error" role="alert">{loadError}</div> : renderContent()}
      </section>
    </main>
  );
}

export { WbcArtifactsTab, WbcContextTab, WbcDetachedPaneApp, WbcOverviewTab, WbcQuickActionItems, useWbcLiveChatMetrics, useWbcLiveContextBlocks, useWbcLiveInbox }
