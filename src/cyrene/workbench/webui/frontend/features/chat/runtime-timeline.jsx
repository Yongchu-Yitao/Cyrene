import { workbenchServices } from "../../shared/runtime/services.jsx"
import { useWbcEffect, useWbcState, wbcT } from "./core.jsx"
import { wbcAgentEventPayload } from "./agent-events.jsx"

var wbcChatCacheState = { lists: {}, details: {}, subagents: {} };
var wbcLastChatByProject = {};
function wbcChatCache() { return wbcChatCacheState; }

function wbcRenderMarkdown(text, options) {
  return workbenchServices.markdown().renderRich(text, options);
}

function wbcRenderMapMarkdown(text) {
  var source = String(text == null ? "" : text).replace(/\\r\\n|\\n|\\r/g, "\n");
  var html = wbcRenderMarkdown(source);
  if (html && !(source.indexOf("**") >= 0 && html.indexOf("**") >= 0)) return html;
  // Leaflet can initialize before the full Markdown parser on a cold desktop
  // load. Keep map notes readable in that short fallback window as well.
  var markdown = workbenchServices.markdown();
  var safe = markdown.escapeHtml(source)
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\n/g, "<br>");
  return markdown.sanitizeHtml(safe);
}

function wbcClampSideSplitWidth(value, availableWidth, viewportWidth, railWidth) {
  var available = Math.max(0, Number(availableWidth) || Number(window.innerWidth) || 0);
  var viewport = Math.max(0, Number(viewportWidth) || Number(window.innerWidth) || available);
  // Both conversation panes use the same 380px floor. At exceptionally compact
  // widths, reduce that floor symmetrically so neither anchored side wins the
  // remaining space merely because it happens to be the split track.
  var rail = Math.max(0, Number(railWidth) || (viewport <= 980 ? 220 : 230));
  var paneMin = Math.min(380, Math.max(0, (available - rail) / 2));
  // A split on the left side hides the right panel track (grid column 4 is 0),
  // so both anchored sides reserve the same room for the conversation lane.
  var maxWidth = Math.min(900, Math.max(paneMin, available - rail - paneMin));
  return Math.round(Math.max(paneMin, Math.min(maxWidth, Number(value) || 520)));
}

function wbcClampSideSplitWidthForPage(value, page) {
  var available = 0;
  var rail = 0;
  if (page) {
    var rect = page.getBoundingClientRect ? page.getBoundingClientRect() : null;
    available = Math.round((rect && rect.width) || page.clientWidth || 0);
    try {
      rail = parseFloat(window.getComputedStyle(page).getPropertyValue("--wbc-rail-width")) || 0;
    } catch (e) {}
  }
  return wbcClampSideSplitWidth(value, available, window.innerWidth, rail);
}

function WbcSplitPickerMenu({ open, className, children, ...props }) {
  var [mounted, setMounted] = useWbcState(Boolean(open));
  useWbcEffect(function () {
    if (open) {
      setMounted(true);
      return undefined;
    }
    var timer = setTimeout(function () { setMounted(false); }, 260);
    return function () { clearTimeout(timer); };
  }, [open]);
  if (!mounted) return null;
  return <div {...props} className={(className || "wbc-side-agent-split-menu") + (open ? " open" : " closing")}>{children}</div>;
}

function wbcFormatTime(value) {
  if (!value) return "";
  try {
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    var now = new Date();
    var dayMs = 24 * 3600 * 1000;
    var startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    if (date >= startOfDay) {
      return workbenchServices.i18n().formatDate(date, { hour: "2-digit", minute: "2-digit" });
    }
    if (date >= new Date(startOfDay.getTime() - dayMs)) return wbcT("workbenchChat.time.yesterday", "Yesterday");
    var days = Math.floor((startOfDay.getTime() - date.getTime()) / dayMs) + 1;
    if (days <= 7) return wbcT("workbenchChat.time.daysAgo", "{n}d ago", { n: days });
    return workbenchServices.i18n().formatDate(date, { month: "2-digit", day: "2-digit" });
  } catch (e) {
    return "";
  }
}

function wbcFormatProcessingDuration(value) {
  var milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "";
  if (milliseconds < 100) return "<0.1s";
  if (milliseconds < 1000) {
    return (Math.round(milliseconds / 100) / 10).toFixed(1) + "s";
  }
  var totalSeconds = Math.max(1, Math.round(milliseconds / 1000));
  var hours = Math.floor(totalSeconds / 3600);
  var minutes = Math.floor((totalSeconds % 3600) / 60);
  var seconds = totalSeconds % 60;
  if (hours > 0) return hours + "h " + minutes + "m";
  if (minutes > 0) return minutes + "m " + seconds + "s";
  return seconds + "s";
}

function wbcConfirmOptimisticMessage(previous, confirmed) {
  var prior = previous || {};
  var next = { ...prior, ...(confirmed || {}), optimistic: false };
  // The server timestamp is authoritative for persistence, but it is produced
  // after the request reaches Python. Replacing the optimistic timestamp with
  // it while the run is live can move the user's turn below the already-mounted
  // thinking placeholder. Keep the client timestamp as this render's stable
  // causal anchor; the next durable reload naturally uses the server timestamp.
  if (prior.optimistic && prior.createdAt) {
    next.serverCreatedAt = String((confirmed && (confirmed.createdAt || confirmed.created_at)) || "");
    next.createdAt = prior.createdAt;
  }
  return next;
}

function wbcReconcileLiveUserMessages(messages, liveUserMessages) {
  var merged = Array.isArray(messages) ? messages.slice() : [];
  (Array.isArray(liveUserMessages) ? liveUserMessages : []).forEach(function (liveMessage) {
    if (!liveMessage || liveMessage.role !== "user") return;
    var liveId = String(liveMessage.id || "");
    var liveRequestId = String(liveMessage.clientRequestId || "");
    var liveQuestionId = String(liveMessage.answerToQuestionId || "");
    var matchIndex = -1;
    for (var i = 0; i < merged.length; i++) {
      var current = merged[i];
      if (!current || current.role !== "user") continue;
      var sameRequest = liveRequestId
        && String(current.clientRequestId || "") === liveRequestId;
      var sameMessage = liveId && String(current.id || "") === liveId;
      // A pending-question answer is persisted before its resumed run finishes.
      // Its optimistic and durable entries have different message ids and no
      // clientRequestId, so a hydration while that run is live must correlate
      // them through the question they both answer.
      var sameQuestionAnswer = liveQuestionId
        && String(current.answerToQuestionId || "") === liveQuestionId;
      if (sameRequest || sameMessage || sameQuestionAnswer) {
        matchIndex = i;
        break;
      }
    }
    if (matchIndex < 0) {
      merged = wbcMergeChronologicalMessages(merged, [liveMessage]);
      return;
    }
    var matched = merged[matchIndex] || {};
    if (liveMessage.optimistic && !matched.optimistic) {
      merged[matchIndex] = wbcConfirmOptimisticMessage(liveMessage, matched);
    } else if (matched.optimistic && !liveMessage.optimistic) {
      merged[matchIndex] = wbcConfirmOptimisticMessage(matched, liveMessage);
    } else {
      merged[matchIndex] = {
        ...matched,
        ...liveMessage,
        createdAt: liveMessage.createdAt || matched.createdAt,
      };
    }
  });
  return merged;
}

function wbcRetryTurnSelection(chat, messageId) {
  var messages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  var targetId = String(messageId || "");
  var targetIndex = -1;
  if (targetId) {
    targetIndex = messages.findIndex(function (item) { return String(item && item.id || "") === targetId; });
  }
  if (targetIndex < 0) targetIndex = messages.length - 1;
  var userIndex = -1;
  for (var i = targetIndex; i >= 0; i--) {
    if (messages[i] && messages[i].role === "user") {
      userIndex = i;
      break;
    }
  }
  if (userIndex < 0) return { userIndex: -1, endIndex: -1, truncateAfterMessageId: "", outputIds: [] };
  var endIndex = messages.length;
  for (var nextIndex = userIndex + 1; nextIndex < messages.length; nextIndex++) {
    if (messages[nextIndex] && messages[nextIndex].role === "user") {
      endIndex = nextIndex;
      break;
    }
  }
  return {
    userIndex: userIndex,
    endIndex: endIndex,
    truncateAfterMessageId: String(messages[userIndex] && messages[userIndex].id || ""),
    outputIds: messages.slice(userIndex + 1, endIndex).map(function (item) {
      return String(item && item.id || "");
    }).filter(Boolean),
  };
}

function wbcTruncateMessagesAfterUser(messages, truncateAfterMessageId) {
  var list = Array.isArray(messages) ? messages : [];
  var afterId = String(truncateAfterMessageId || "");
  if (!afterId) return list;
  var userIndex = list.findIndex(function (item) {
    return String(item && item.id || "") === afterId && item && item.role === "user";
  });
  if (userIndex < 0) return list;
  var endIndex = list.length;
  for (var index = userIndex + 1; index < list.length; index += 1) {
    if (list[index] && list[index].role === "user") {
      endIndex = index;
      break;
    }
  }
  if (endIndex === userIndex + 1) return list;
  return list.slice(0, userIndex + 1).concat(list.slice(endIndex));
}

function wbcClearModelOutputForRetry(chat, messageId) {
  if (!chat || !Array.isArray(chat.messages)) return chat;
  var selection = wbcRetryTurnSelection(chat, messageId);
  if (selection.userIndex < 0) return chat;
  return {
    ...chat,
    messages: chat.messages.slice(0, selection.userIndex + 1).concat(chat.messages.slice(selection.endIndex)),
    pendingQuestion: selection.endIndex === chat.messages.length ? null : chat.pendingQuestion,
  };
}

function wbcPreserveLiveTimelineAnchors(previousChat, hydratedChat, runtime) {
  if (!hydratedChat || !runtime) return hydratedChat;
  var hydratedMessages = Array.isArray(hydratedChat.messages) ? hydratedChat.messages : [];
  var messages = wbcTruncateMessagesAfterUser(
    hydratedMessages,
    runtime.retryTruncateAfterMessageId
  );
  var liveUserMessages = Array.isArray(runtime.userMessages) ? runtime.userMessages : [];
  if (liveUserMessages.length) {
    messages = wbcReconcileLiveUserMessages(messages, liveUserMessages);
  }
  if (messages === hydratedMessages) return hydratedChat;
  return {
    ...hydratedChat,
    messages: messages,
  };
}

function wbcMergeChronologicalMessages(messages, additions) {
  // Runtime segments are discovered independently from persisted guidance.
  // Merge them by event time so steering stays where it happened instead of
  // forcing every user message above all assistant output.
  var merged = Array.isArray(messages) ? messages.slice() : [];
  var known = new Set();
  merged.forEach(function (item) {
    var id = String(item && item.id || "");
    if (id) known.add(id);
  });
  (additions || []).forEach(function (item) {
    if (!item) return;
    var id = String(item.id || "");
    if (id && known.has(id)) return;
    var answerToQuestionId = String(item.answerToQuestionId || "");
    if (item.role === "user" && answerToQuestionId) {
      for (var answerIndex = 0; answerIndex < merged.length; answerIndex++) {
        var priorAnswer = merged[answerIndex] || {};
        if (priorAnswer.role !== "user"
          || String(priorAnswer.answerToQuestionId || "") !== answerToQuestionId) continue;
        var priorAnswerId = String(priorAnswer.id || "");
        if (priorAnswer.optimistic && !item.optimistic) {
          merged[answerIndex] = wbcConfirmOptimisticMessage(priorAnswer, item);
        } else if (!priorAnswer.optimistic && item.optimistic) {
          merged[answerIndex] = priorAnswer;
        } else {
          merged[answerIndex] = { ...priorAnswer, ...item };
        }
        if (priorAnswerId) known.delete(priorAnswerId);
        var mergedAnswerId = String(merged[answerIndex] && merged[answerIndex].id || "");
        if (mergedAnswerId) known.add(mergedAnswerId);
        return;
      }
    }
    var clientRequestId = String(item.clientRequestId || "");
    if (clientRequestId) {
      for (var requestIndex = 0; requestIndex < merged.length; requestIndex++) {
        if (String(merged[requestIndex] && merged[requestIndex].clientRequestId || "") !== clientRequestId) continue;
        var previousId = String(merged[requestIndex] && merged[requestIndex].id || "");
        merged[requestIndex] = wbcConfirmOptimisticMessage(merged[requestIndex], item);
        if (previousId) known.delete(previousId);
        if (id) known.add(id);
        return;
      }
    }
    var at = String(item.createdAt || item.created_at || "");
    var atMs = at ? Date.parse(at) : NaN;
    var index = merged.length;
    if (Number.isFinite(atMs)) {
      for (var i = 0; i < merged.length; i++) {
        var currentAt = String(merged[i] && (merged[i].createdAt || merged[i].created_at) || "");
        var currentAtMs = currentAt ? Date.parse(currentAt) : NaN;
        if (Number.isFinite(currentAtMs) && currentAtMs > atMs) { index = i; break; }
      }
    }
    merged.splice(index, 0, item);
    if (id) known.add(id);
  });
  return merged;
}

function wbcMergeSavedAssistantMessages(chat, assistantMessages) {
  if (!chat) return chat;
  var updates = new Map((assistantMessages || []).filter(Boolean).map(function (message) { return [message.id, message]; }));
  var current = (chat.messages || []).map(function (message) {
    var update = updates.get(message.id);
    updates.delete(message.id);
    return update ? { ...message, ...update } : message;
  });
  return { ...chat, status: "idle", liveAgentArtifacts: [],
    messages: wbcMergeChronologicalMessages(current, Array.from(updates.values())) };
}

function wbcActivityDedupeKeys(message) {
  if (!message) return [];
  var activity = message.runtimeActivity && typeof message.runtimeActivity === "object"
    ? message.runtimeActivity
    : (message.activityCard ? message : null);
  if (!activity) return [];
  var keys = new Set();
  [message.id, activity.id].forEach(function (value) {
    var id = String(value || "");
    // Older clients wrapped the server activity id in a UI-only ``runtime_``
    // prefix. Treat both representations as the same durable timeline record.
    if (id.startsWith("runtime_")) id = id.slice("runtime_".length);
    if (id) keys.add("activity:" + id);
  });
  var entries = Array.isArray(activity.progress)
    ? activity.progress
    : (Array.isArray(activity.trace) ? activity.trace : (Array.isArray(message.trace) ? message.trace : []));
  entries.forEach(function (entry) {
    var toolCallId = String(entry && (entry.toolCallId || entry.tool_call_id) || "");
    if (toolCallId) keys.add("tool:" + toolCallId);
  });
  return Array.from(keys);
}

function wbcProjectRuntimeTranscript(messages, additions) {
  var runtimeMessages = Array.isArray(additions) ? additions.filter(Boolean) : [];
  var liveActivityKeys = new Set();
  runtimeMessages.forEach(function (message) {
    wbcActivityDedupeKeys(message).forEach(function (key) { liveActivityKeys.add(key); });
  });
  var durable = (Array.isArray(messages) ? messages : []).filter(function (message) {
    if (!liveActivityKeys.size) return true;
    return !wbcActivityDedupeKeys(message).some(function (key) { return liveActivityKeys.has(key); });
  });
  return wbcMergeChronologicalMessages(durable, runtimeMessages);
}

// One projection for all surfaces. Server identities survive hydration and save.
function wbcApplyTimeline(runtime, patch) {
  if (!patch || (patch.version !== 1 && patch.version !== 2)) return runtime;
  var current = runtime || {};
  if (current.timeline && current.timeline.runId === patch.runId && current.timeline.revision >= patch.revision) return current;
  var prior = !patch.snapshot && current.timeline && current.timeline.runId === patch.runId ? current.timeline.messages : [];
  var byId = new Map((prior || []).map(function (message) { return [message.id, message]; }));
  (patch.messages || []).forEach(function (message) { byId.set(message.id, message); });
  (patch.updates || []).forEach(function (update) {
    var previous = byId.get(update.id);
    if (!previous || Number(previous.timelineRevision || 0) !== update.baseRevision) {
      throw new Error("Timeline text update is missing its base revision");
    }
    var message = { ...previous, ...update.set };
    Object.keys(update.append || {}).forEach(function (field) {
      message[field] = String(previous[field] || "") + update.append[field];
    });
    (update.unset || []).forEach(function (field) { delete message[field]; });
    byId.set(update.id, message);
  });
  var messages = Array.from(byId.values()).sort(function (a, b) { return a.timelineOrder - b.timelineOrder; });
  var lastReply = messages.filter(function (message) { return !message.activityCard && !message.intermediate && !message.notificationCard; }).slice(-1)[0];
  // These are read-only summaries for the composer/browser/resource shelf.
  // The transcript itself is rendered exclusively from timeline.messages.
  return { ...current, reconnecting: false,
    text: lastReply ? lastReply.content : "", streamDone: !lastReply || lastReply.status !== "running",
    artifacts: lastReply && lastReply.attachments || [],
    activities: messages.filter(function (message) { return message.activityCard; }).map(function (message) {
      return { ...message, progress: message.trace, createdAt: Date.parse(message.createdAt) };
    }),
    timeline: { ...patch, messages: messages } };

}

function wbcProjectTranscript(messages, runtime) {
  var durable = wbcReconcileLiveUserMessages(messages || [], runtime && runtime.userMessages);
  if (!runtime || !runtime.timeline) return durable;
  var patch = runtime.timeline;
  var liveIds = new Set(patch.messages.map(function (message) { return message.id; }));
  var liveActivityKeys = new Set();
  patch.messages.forEach(function (message) {
    wbcActivityDedupeKeys(message).forEach(function (key) { liveActivityKeys.add(key); });
  });
  var saved = new Map(durable.map(function (message) { return [message.id, message]; }));
  var records = patch.messages.map(function (message) {
    var historical = saved.get(message.id);
    return historical && Number(historical.timelineRevision || 0) > Number(message.timelineRevision || 0) ? historical : { ...historical, ...message };
  });
  var merged = wbcMergeChronologicalMessages(durable.filter(function (message) {
    if (liveIds.has(message.id)) return false;
    return !wbcActivityDedupeKeys(message).some(function (key) { return liveActivityKeys.has(key); });
  }), records);
  var busy = patch.messages.some(function (message) {
    return message.status === "running" && (message.activityCard || String(message.content || "").length > 0);
  });
  if (patch.status === "running" && !busy && !runtime.pendingQuestion && !runtime.reconnecting) {
    merged.push({ id: patch.runId + ":continuation", role: "assistant", runtimeContinuation: true });
  }
  return merged;
}

function wbcRuntimeSegmentMessages(runtime) {
  var segments = runtime && Array.isArray(runtime.segments) ? runtime.segments : [];
  var hasLiveActivities = !!(runtime && Array.isArray(runtime.activities) && runtime.activities.length);
  return segments.map(function (segment) {
    var message = segment && segment.message ? segment.message : {};
    return {
      ...message,
      id: String(message.id || segment.id || ""),
      role: "assistant",
      // While the run is active, per-LLM activity cards own the live tool trace.
      // Hiding the segment copy prevents the same calls from appearing twice.
      trace: hasLiveActivities ? [] : (Array.isArray(segment.progress) ? segment.progress : (message.trace || [])),
      runtimeSegment: true,
    };
  });
}

function wbcRuntimeTimelineMessages(runtime, options) {
  if (!runtime) return [];
  var showReasoningPlaceholder = !options || options.showReasoningPlaceholder !== false;
  var startedAt = Number(runtime.startedAt || Date.now());
  var runtimeActivities = Array.isArray(runtime.activities) ? runtime.activities : [];
  var activities = runtimeActivities.length
    ? runtimeActivities
    : (showReasoningPlaceholder ? [{ id: "activity_1", reasoning: "", progress: [] }] : []);
  var items = [{
    id: "runtime_heartbeat_" + String(runtime.chatId || "chat"),
    role: "assistant",
    createdAt: new Date(startedAt + 1).toISOString(),
    runtimeHeartbeat: true,
    runtimeFinalizing: !!runtime.finalizing,
  }];
  activities.forEach(function (activity, index) {
    items.push({
      // Activity ids originate on the server and must remain stable across the
      // live -> checkpointed transition. A UI-only prefix creates a second
      // identity and causes the same card to be rendered twice after hydration.
      id: String(activity.id || ("runtime_activity_" + index)),
      role: "assistant",
      createdAt: new Date(Number(activity.createdAt || startedAt + index + 2)).toISOString(),
      runtimeActivity: activity,
      runtimeActivityActive: !runtime.finalizing && index === activities.length - 1,
      runtimeActivityHasReplyText: !!runtime.text,
    });
  });
  (Array.isArray(runtime.notifications) ? runtime.notifications : []).forEach(function (notice, index) {
    items.push({
      id: String(notice.id || ("runtime_notice_" + index)),
      role: "assistant",
      createdAt: new Date(Number(notice.createdAt || startedAt + index + 2)).toISOString(),
      runtimeNotification: true,
      notification: notice,
    });
  });
  var hasRunningEntry = runtimeActivities.some(function (activity) {
    return (Array.isArray(activity && activity.progress) ? activity.progress : []).some(function (entry) {
      return String(entry && entry.status || "").trim().toLowerCase() === "running";
    });
  });
  var latestActivity = runtimeActivities.length ? runtimeActivities[runtimeActivities.length - 1] : null;
  var latestEntries = Array.isArray(latestActivity && latestActivity.progress) ? latestActivity.progress : [];
  var hasLiveReply = !!String(runtime.text || "") || !!(runtime.artifacts && runtime.artifacts.length);
  // A completed activity describes work that has already finished, so leave
  // its card settled. If the runtime itself remains active, append a separate
  // continuation row to cover the quiet gap before the next event or reply.
  var needsContinuation = !runtime.finalizing
    && !hasLiveReply
    && !hasRunningEntry
    && (
      (runtimeActivities.length === 0 && !showReasoningPlaceholder)
      || !!(latestActivity && latestEntries.length > 0)
    );
  if (needsContinuation) {
    var continuationAt = items.reduce(function (latest, item) {
      var timestamp = Date.parse(String(item && item.createdAt || ""));
      return Number.isFinite(timestamp) ? Math.max(latest, timestamp) : latest;
    }, Math.max(startedAt, Number(runtime.lastEventAt || 0))) + 1;
    items.push({
      id: "runtime_continuation_" + String(runtime.chatId || "chat"),
      role: "assistant",
      createdAt: new Date(continuationAt).toISOString(),
      runtimeContinuation: true,
    });
  }
  return wbcMergeChronologicalMessages([], items);
}

function wbcFinalizeRuntime(runtime) {
  var current = runtime || {};
  function settle(items) {
    return (Array.isArray(items) ? items : []).map(function (entry) {
      if (!entry || entry.kind !== "tool" || entry.status !== "running") return entry;
      return { ...entry, status: "completed", inferredCompletion: true };
    });
  }
  return {
    ...current,
    finalizing: true,
    replying: false,
    progress: settle(current.progress),
    activities: (Array.isArray(current.activities) ? current.activities : []).map(function (activity) {
      return {
        ...activity,
        reasoningActive: false,
        timelineClosed: true,
        progress: settle(activity && activity.progress),
      };
    }),
  };
}

function wbcCreateDetachedRuntime(startedAt) {
  var now = Number(startedAt || Date.now());
  return {
    text: "",
    streamDone: false,
    activities: [],
    activitySeq: 0,
    notifications: [],
    artifacts: [],
    startedAt: now,
    lastEventAt: now,
    finalizing: false,
  };
}

function wbcReduceDetachedRuntime(runtime, action, value, sourceEvent) {
  var current = runtime || wbcCreateDetachedRuntime();
  if (action === "timeline") return wbcApplyTimeline(current, value);
  var now = Date.now();
  function withActivity(updater) {
    var activities = Array.isArray(current.activities) ? current.activities.slice() : [];
    if (!activities.length || activities[activities.length - 1].timelineClosed) {
      var seq = Number(current.activitySeq || 0) + 1;
      activities.push({ id: "activity_" + seq, reasoning: "", reasoningActive: false, progress: [], createdAt: now });
      current = { ...current, activitySeq: seq };
    }
    var index = activities.length - 1;
    activities[index] = updater(activities[index] || {});
    return { ...current, activities: activities, lastEventAt: now };
  }
  if (action === "reply_start") return { ...current, text: "", streamDone: false, lastEventAt: now };
  if (action === "reply_delta") return { ...current, text: String(current.text || "") + String(value || ""), streamDone: false, lastEventAt: now };
  if (action === "reply_done") return { ...current, text: String(value || current.text || ""), streamDone: true, lastEventAt: now };
  if (action === "finalizing") return { ...wbcFinalizeRuntime(current), lastEventAt: now };
  if (action === "reasoning_start") return withActivity(function (activity) {
    return { ...activity, reasoningActive: true };
  });
  if (action === "reasoning_delta") return withActivity(function (activity) {
    return { ...activity, reasoning: String(activity.reasoning || "") + String(value || ""), reasoningActive: true };
  });
  if (action === "reasoning_done") return withActivity(function (activity) {
    return { ...activity, reasoning: String(value || activity.reasoning || ""), reasoningActive: false };
  });
  if (action === "tool") {
    var tool = value && typeof value === "object" ? value : {};
    var toolCallId = String(tool.toolCallId || tool.tool_call_id || "");
    var status = String(tool.status || "running").toLowerCase();
    var terminal = status === "completed" || status === "failed" || tool.terminal === true;
    var entry = {
      kind: "tool",
      toolCallId: toolCallId,
      text: String(tool.name || tool.tool || tool.title || "tool"),
      preview: String(tool.outputSummary || tool.inputSummary || ""),
      status: terminal ? "completed" : "running",
      failed: !!tool.failed || status === "failed",
      input: tool.input,
      output: tool.output,
      presentation: tool.presentation && typeof tool.presentation === "object" ? tool.presentation : {},
    };
    return withActivity(function (activity) {
      var progress = Array.isArray(activity.progress) ? activity.progress.slice() : [];
      var merged = wbcMergeToolOccurrence(progress, entry, terminal);
      progress = merged.items;
      if (!merged.matched) progress.push({
        ...entry,
        reasoningOffset: String(activity.reasoning || "").length,
        startedAt: now,
      });
      return { ...activity, progress: progress.slice(-40) };
    });
  }
  if (action === "notification") {
    var notice = value && typeof value === "object" ? value : {};
    if (!notice.message) return current;
    var notifications = Array.isArray(current.notifications) ? current.notifications.slice() : [];
    var noticeKey = String(notice.id || (notice.category + "\n" + notice.message));
    if (!notifications.some(function (item) { return String(item.id || (item.category + "\n" + item.message)) === noticeKey; })) notifications.push(notice);
    return { ...current, notifications: notifications, lastEventAt: now };
  }
  if (action === "artifact") {
    var payload = wbcAgentEventPayload(sourceEvent || value || {});
    var attachment = payload.attachment && typeof payload.attachment === "object" ? payload.attachment : null;
    if (!attachment && (payload.uri || payload.url)) {
      attachment = {
        id: String(payload.artifactId || payload.id || payload.uri || payload.url || ""),
        name: String(payload.title || payload.name || "artifact"),
        content_type: String(payload.mimeType || payload.content_type || "application/octet-stream"),
        kind: String(payload.kind || "file"),
        url: String(payload.uri || payload.url || ""),
        size: Number(payload.size || 0),
      };
    }
    if (!attachment) return current;
    var artifacts = Array.isArray(current.artifacts) ? current.artifacts.slice() : [];
    var artifactId = String(payload.artifactId || attachment.id || attachment.url || "");
    var artifactIndex = artifacts.findIndex(function (item) { return String(item && (item.artifactId || item.id || item.url) || "") === artifactId; });
    var artifact = { ...attachment, artifactId: artifactId };
    if (artifactIndex >= 0) artifacts[artifactIndex] = { ...artifacts[artifactIndex], ...artifact };
    else artifacts.push(artifact);
    return { ...current, artifacts: artifacts, lastEventAt: now };
  }
  return current;
}

function wbcMergeToolLifecycleEntry(current, incoming, terminalOnly) {
  if (!terminalOnly) return {
    ...current,
    ...incoming,
    text: incoming.text || current.text,
  };
  // The concrete executor may already have replaced a progressive package name
  // with the resolved capability. A gateway terminal event owns lifecycle only;
  // it must not regress that richer identity back to the package name.
  return {
    ...current,
    status: incoming.status,
    failed: incoming.failed,
    preview: current.preview || incoming.preview,
    input: incoming.input != null ? incoming.input : current.input,
    output: incoming.output != null ? incoming.output : current.output,
    presentation: incoming.presentation && Object.keys(incoming.presentation).length ? incoming.presentation : current.presentation,
  };
}

function wbcToolEntryIsTerminal(entry) {
  var status = String(entry && entry.status || "").trim().toLowerCase();
  return ["completed", "failed", "error", "failure", "expired", "cancelled"].indexOf(status) >= 0;
}

function wbcToolOccurrenceIndex(items, toolCallId, incomingTerminal) {
  var list = Array.isArray(items) ? items : [];
  var latestMatching = -1;
  for (var index = list.length - 1; index >= 0; index--) {
    var item = list[index];
    if (String(item && item.toolCallId || "") !== String(toolCallId || "")) continue;
    if (latestMatching < 0) latestMatching = index;
    if (!wbcToolEntryIsTerminal(item)) return index;
  }
  // A new running event after a completed occurrence is a new invocation even
  // when an Agent incorrectly reuses the same toolCallId. A duplicate terminal
  // event may still update the latest completed occurrence in place.
  return incomingTerminal ? latestMatching : -1;
}

function wbcMergeToolOccurrence(items, incoming, incomingTerminal) {
  var list = Array.isArray(items) ? items.slice() : [];
  var index = incoming && incoming.toolCallId
    ? wbcToolOccurrenceIndex(list, incoming.toolCallId, incomingTerminal)
    : -1;
  if (index < 0) return { items: list, matched: false };
  list[index] = wbcMergeToolLifecycleEntry(list[index], incoming, incomingTerminal);
  return { items: list, matched: true };
}

// The client's live tool trace (assembled from SSE tool events) is the
// authoritative execution history. On save we upload it so the completed
// conversation matches what ran live — the backend's transcript extraction can
// drop mid-run tool calls (compaction / retry) and drops runtime status fields.
var WBC_DURABLE_TRACE_FIELDS = [
  "kind", "toolCallId", "text", "tool", "preview", "status", "failed",
  "progress", "progressCurrent", "progressTotal", "startedAt", "reasoningOffset",
  "detailKey", "detailParams", "presentation",
];

function wbcCleanDurableTraceEntry(entry) {
  if (!entry || typeof entry !== "object") return null;
  var out = {};
  WBC_DURABLE_TRACE_FIELDS.forEach(function (key) {
    var value = entry[key];
    if (value === undefined || value === null) return;
    if (typeof value === "string") value = String(value).slice(0, 400);
    else if (typeof value === "number" && !Number.isFinite(value)) return;
    else if (typeof value === "boolean") { /* keep */ }
    else if (typeof value === "object") {
      try {
        value = JSON.stringify(value).slice(0, 2000);
      } catch (e) { return; }
    } else return;
    out[key] = value;
  });
  return Object.keys(out).length ? out : null;
}

// Zip the live activities (in execution order) onto the just-saved activity
// cards (also in execution order). Both sides have exactly one entry per
// LLM turn that actually called tools; anything else means the boundaries
// diverged, so skip the upload and keep the backend-extracted trace.
function wbcDurableTracePayload(chatId, runtime, assistantMessages) {
  if (!chatId || !runtime) return null;
  var withTools = (Array.isArray(runtime.activities) ? runtime.activities : []).filter(function (activity) {
    return Array.isArray(activity && activity.progress) && activity.progress.length;
  });
  if (!withTools.length) return null;
  var savedCards = (Array.isArray(assistantMessages) ? assistantMessages : []).filter(function (message) {
    return message && message.activityCard && Array.isArray(message.trace) && message.trace.length;
  });
  if (!savedCards.length || savedCards.length !== withTools.length) return null;
  var messageIds = [];
  var traces = [];
  for (var index = 0; index < withTools.length; index += 1) {
    var progress = withTools[index].progress.map(function (entry) {
      var status = String(entry && entry.status || "").toLowerCase();
      if (status === "running" || status === "resumed") {
        entry = { ...entry, status: "completed", inferredCompletion: true };
      }
      return wbcCleanDurableTraceEntry(entry);
    }).filter(Boolean);
    if (!progress.length) return null;
    messageIds.push(String(savedCards[index].id || ""));
    traces.push(progress);
  }
  if (!messageIds.some(Boolean)) return null;
  return { messageIds: messageIds, traces: traces };
}

function wbcPersistDurableTrace(chatId, payload) {
  if (!chatId || !payload) return;
  try {
    fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/trace", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(function () {});
  } catch (e) {}
}

export { wbcApplyTimeline, wbcProjectTranscript, wbcProjectRuntimeTranscript, wbcChatCacheState, wbcLastChatByProject, wbcChatCache, wbcRenderMarkdown, wbcRenderMapMarkdown, wbcClampSideSplitWidth, wbcClampSideSplitWidthForPage, WbcSplitPickerMenu, wbcFormatTime, wbcFormatProcessingDuration, wbcConfirmOptimisticMessage, wbcReconcileLiveUserMessages, wbcRetryTurnSelection, wbcTruncateMessagesAfterUser, wbcClearModelOutputForRetry, wbcPreserveLiveTimelineAnchors, wbcMergeChronologicalMessages, wbcMergeSavedAssistantMessages, wbcRuntimeSegmentMessages, wbcRuntimeTimelineMessages, wbcFinalizeRuntime, wbcCreateDetachedRuntime, wbcReduceDetachedRuntime, wbcMergeToolLifecycleEntry, wbcToolEntryIsTerminal, wbcToolOccurrenceIndex, wbcMergeToolOccurrence, WBC_DURABLE_TRACE_FIELDS, wbcCleanDurableTraceEntry, wbcDurableTracePayload, wbcPersistDurableTrace }
