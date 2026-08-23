import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WBC_COMMANDS, WBC_ICONS, wbcAgentEventPayload, wbcAgentSessionPayload, wbcAttachmentVisual, wbcConfirmOptimisticMessage, wbcDurableTracePayload, wbcErrorText, wbcFileDragPayload, wbcFileViewKind, wbcFinalizeRuntime, wbcMergeToolOccurrence, wbcPersistDurableTrace, wbcSetResourceDrag, wbcStructuredEventSummary, wbcSubagentStatusText, wbcT, wbcToolArgsPreview } from "../../workbench-chat.jsx"

// Workbench chat feature module with explicit ESM dependencies.
function WbcFileVisual({ file, className }) {
  var visual = wbcAttachmentVisual(file);
  return <span className={(className || "wbc-file-visual") + " " + visual.tone} aria-hidden="true">{visual.icon}</span>;
}

function wbcCanOpenExternally(file) {
  // Opening user-controlled HTML in a normal Electron child window would give
  // it the authenticated local-backend session. Keep HTML inside the sandboxed
  // srcDoc viewer; source mode remains available beside it.
  return !!(file && file.url && wbcFileViewKind(file) !== "html");
}

function wbcStartFileDrag(event, file) {
  var page = event.currentTarget && event.currentTarget.closest(".wbc-page");
  wbcSetResourceDrag(event, wbcFileDragPayload(
    file,
    page && page.getAttribute("data-active-chat-id"),
    page && page.getAttribute("data-project-id")
  ));
}

function wbcDownloadLink(file, overrides) {
  var url = file && file.url;
  if (!url) return null;
  var baseClass = "wb-btn ghost";
  var extraClass = overrides && overrides.className;
  var attrs = {
    className: extraClass ? baseClass + " " + extraClass : baseClass,
    href: url,
    download: (file.name && file.name.trim()) ? file.name : true,
    title: wbcT("workbenchChat.download", "Download"),
  };
  if (overrides) {
    for (var k in overrides) {
      if (overrides.hasOwnProperty(k) && k !== "className") attrs[k] = overrides[k];
    }
  }
  return <a {...attrs}>{WBC_ICONS.download}</a>;
}

function wbcHtmlPreviewDocument(source, sourceUrl) {
  var html = String(source || "");
  if (!sourceUrl) return html;
  var absoluteUrl = "";
  try {
    absoluteUrl = new URL(sourceUrl, window.location.href).href;
  } catch (e) {
    return html;
  }
  var escapedUrl = absoluteUrl.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  var headContent = '<base href="' + escapedUrl + '">';
  // Only inject viewport meta if the source HTML doesn't already have one
  if (!/<meta\s+name=["']viewport["']/i.test(html)) {
    headContent += '<meta name="viewport" content="width=device-width, initial-scale=1">';
  }
  if (/<head(?:\s[^>]*)?>/i.test(html)) {
    return html.replace(/<head(\s[^>]*)?>/i, function (match) { return match + headContent; });
  }
  if (/<html(?:\s[^>]*)?>/i.test(html)) {
    return html.replace(/<html(\s[^>]*)?>/i, function (match) { return match + "<head>" + headContent + "</head>"; });
  }
  // Fragment: wrap in a minimal document. Browsers in iframes gracefully handle
  // stray closing tags ( </body> and </html> are ignored by the parser), so
  // no tag stripping is needed — it would only risk mangling code-block content.
  return "<!DOCTYPE html>\n<html><head>" + headContent + "</head><body>" + html + "</body></html>";
}

// Map tools mark the conversation as having a 地图 tab (same tool set as the
// legacy chat surface: pin_location / connect_pins).
function wbcIsMapTool(name) {
  var raw = String(name || "").trim();
  return raw === "pin_location" || raw === "connect_pins";
}

function wbcChatUsedMap(chat, runtime) {
  if (runtime && Array.isArray(runtime.progress)) {
    for (var i = 0; i < runtime.progress.length; i++) {
      if (wbcIsMapTool(runtime.progress[i].text)) return true;
    }
  }
  if (runtime && Array.isArray(runtime.segments)) {
    for (var s = 0; s < runtime.segments.length; s++) {
      var segmentProgress = runtime.segments[s] && runtime.segments[s].progress;
      if (!Array.isArray(segmentProgress)) continue;
      for (var p = 0; p < segmentProgress.length; p++) {
        if (wbcIsMapTool(segmentProgress[p].text)) return true;
      }
    }
  }
  var messages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  for (var m = 0; m < messages.length; m++) {
    var trace = messages[m].trace;
    if (!Array.isArray(trace)) continue;
    for (var t = 0; t < trace.length; t++) {
      if (wbcIsMapTool(trace[t].tool)) return true;
    }
  }
  return false;
}

function wbcCommandMeta(id) {
  for (var i = 0; i < WBC_COMMANDS.length; i++) {
    if (WBC_COMMANDS[i].id === id) {
      return {
        id: WBC_COMMANDS[i].id,
        label: wbcT(WBC_COMMANDS[i].labelKey, WBC_COMMANDS[i].id),
        desc: wbcT(WBC_COMMANDS[i].descKey, ""),
      };
    }
  }
  return null;
}

function wbcRuntimeToolEvent(event, eventAt) {
  var toolName = String(event.tool || "");
  if (["use_tools", "quit", "send_message", "update_plan_progress"].indexOf(toolName) >= 0) return null;
  var toolStarted = event.type === "tool_call_started";
  var toolProgress = event.type === "tool_call_progress";
  var toolResult = String(event.result || "");
  var failed = !!event.failed
    || String(event.status || "").toLowerCase() === "failed"
    || (!toolStarted && toolResult.toLowerCase().startsWith("tool failed:"));
  var error = event.error && typeof event.error === "object" ? event.error : null;
  var errorMessage = String(error && error.message || event.message || "").trim();
  return {
    terminal: event.type === "tool_call_finished",
    entry: {
      kind: "tool", toolCallId: String(event.tool_call_id || ""), text: toolName || undefined,
      preview: toolProgress ? String(event.label || "") : (failed && errorMessage ? errorMessage.slice(0, 240) : wbcToolArgsPreview(event.args || {})),
      status: (toolStarted || toolProgress) ? "running" : "completed", failed: failed,
      progress: toolProgress ? Math.max(0, Math.min(1, Number(event.progress) || 0)) : undefined,
      progressCurrent: toolProgress ? Math.max(0, Number(event.current) || 0) : undefined,
      progressTotal: toolProgress ? Math.max(0, Number(event.total) || 0) : undefined,
      startedAt: eventAt, output: failed && error ? error : undefined,
      presentation: failed && error ? { kind: "error" } : undefined,
    },
  };
}

// ---------------------------------------------------------------------------
// Streaming runtime engine (module-level — survives view switches)
// ---------------------------------------------------------------------------
// The chat page unmounts whenever the user switches to another workbench module
// (任务 / 知识 / 记忆 …). A run started here keeps going on the server, so its
// live state — streaming reply text, tool-call progress, the abort handle — must
// live OUTSIDE the component or it is lost on unmount: the page would come back
// with an idle composer sitting over a conversation the status panel still shows
// as "running", and the tool-call trace would vanish. This singleton owns that
// state, drives the send stream, and folds in SSE tool progress even while no
// page is mounted. The mounted page subscribes for re-renders and registers
// transcript hooks; when it unmounts the hooks fall away and the run streams on,
// with the transcript re-pulled from the server on remount.
var WorkbenchChatRuntimes = (function () {
  var runtimes = {};            // chatId -> { chatId, text, progress, activities, startedAt, lastEventAt, replying }
  var aborts = {};              // chatId -> AbortController
  var reconnectTimers = {};     // chatId -> bounded transport-reconnect timer
  var deferredSends = {};       // chatId -> terminal-race guidance promoted to the next normal turn
  var failures = {};            // chatId -> terminal Error; retained until the next explicit run attempt
  var subscribers = new Set();
  var summarySubscribers = new Set();
  var hooks = null;             // live transcript hooks from the mounted page

  function publishLifecycle(chatId, status, event) {
    if (!chatId || typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
    var payload = event && typeof event === "object" ? event : {};
    try {
      window.dispatchEvent(new CustomEvent("cyrene:wbc-chat-lifecycle", {
        detail: {
          chatId: String(chatId),
          status: String(status || "refresh"),
          runId: String(payload.runId || payload.run_id || ""),
          timestamp: String(payload.timestamp || new Date().toISOString()),
        },
      }));
    } catch (e) {}
  }

  // `emit` notifies subscribers (the mounted page → React re-render). Reply
  // deltas can arrive many times per second, and each re-render re-parses +
  // re-sanitizes the whole streaming markdown (O(n²) over a long reply). So the
  // delta path defers emits via rAF coalescing: the text is applied to the
  // runtime synchronously (so reads stay correct), but the re-render is batched
  // to at most once per frame. Every other (low-frequency) update emits at once.
  var emitHandle = 0;
  var emitIsRaf = false;
  function emit() {
    subscribers.forEach(function (fn) { try { fn(runtimes); } catch (e) {} });
  }
  function emitSummary() {
    summarySubscribers.forEach(function (fn) { try { fn(runtimes); } catch (e) {} });
  }
  function flushEmit() { emitHandle = 0; emit(); }
  function scheduleEmit() {
    if (emitHandle) return;
    if (typeof requestAnimationFrame === "function") {
      emitIsRaf = true;
      emitHandle = requestAnimationFrame(flushEmit);
    } else {
      emitIsRaf = false;
      emitHandle = setTimeout(flushEmit, 16);
    }
  }
  function cancelScheduledEmit() {
    if (!emitHandle) return;
    if (emitIsRaf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(emitHandle);
    else clearTimeout(emitHandle);
    emitHandle = 0;
  }

  function subscribe(fn) {
    subscribers.add(fn);
    return function () { subscribers.delete(fn); };
  }

  // Shell-level consumers need every semantic runtime transition (start/stop,
  // reasoning phase, tools, finalization), but not each text-only token delta.
  // This preserves topbar fidelity without repainting the entire workbench on
  // every animation frame.
  function subscribeSummary(fn) {
    summarySubscribers.add(fn);
    return function () { summarySubscribers.delete(fn); };
  }

  function snapshot() { return runtimes; }
  function get(chatId) { return (chatId && runtimes[chatId]) || null; }
  function isRunning(chatId) { return !!(chatId && runtimes[chatId]); }

  function update(chatId, updater, defer) {
    if (!chatId) return null;
    var current = runtimes[chatId] || null;
    var next = typeof updater === "function" ? updater(current) : updater;
    var nextMap = {};
    Object.keys(runtimes).forEach(function (key) { nextMap[key] = runtimes[key]; });
    if (next) nextMap[chatId] = next; else delete nextMap[chatId];
    runtimes = nextMap;
    if (!defer || !!current !== !!next) emitSummary();
    // Defer only the high-frequency reply-delta path; everything else (including
    // the delta's terminal siblings reply_done / saved) emits now and cancels any
    // pending coalesced emit so the latest state renders without delay.
    if (defer) scheduleEmit();
    else { cancelScheduledEmit(); emit(); }
    return next;
  }

  function recordUserMessage(chatId, message, previousId) {
    if (!chatId || !message) return null;
    return update(chatId, function (current) {
      if (!current) return null;
      var userMessages = Array.isArray(current.userMessages) ? current.userMessages.slice() : [];
      var requestId = String(message.clientRequestId || "");
      var messageId = String(message.id || "");
      var priorId = String(previousId || "");
      var matchIndex = -1;
      for (var i = 0; i < userMessages.length; i++) {
        var candidate = userMessages[i] || {};
        if (
          (priorId && String(candidate.id || "") === priorId)
          || (requestId && String(candidate.clientRequestId || "") === requestId)
          || (messageId && String(candidate.id || "") === messageId)
        ) {
          matchIndex = i;
          break;
        }
      }
      if (matchIndex < 0) {
        userMessages.push(message);
      } else {
        var previous = userMessages[matchIndex] || {};
        if (previous.optimistic && !message.optimistic) {
          userMessages[matchIndex] = wbcConfirmOptimisticMessage(previous, message);
        } else if (!previous.optimistic && message.optimistic) {
          userMessages[matchIndex] = previous;
        } else {
          userMessages[matchIndex] = {
            ...previous,
            ...message,
            createdAt: previous.createdAt || message.createdAt,
            serverCreatedAt: previous.serverCreatedAt
              || String(message.createdAt || message.created_at || ""),
          };
        }
      }
      return { ...current, userMessages: userMessages };
    });
  }

  function clearReconnectTimer(chatId) {
    var timer = reconnectTimers[chatId];
    if (timer) clearTimeout(timer);
    delete reconnectTimers[chatId];
  }

  function clear(chatId) {
    clearReconnectTimer(chatId);
    update(chatId, null);
  }

  function getFailure(chatId) {
    return failures[String(chatId || "")] || null;
  }

  function clearFailure(chatId) {
    delete failures[String(chatId || "")];
  }

  function failRun(chatId, err) {
    if (!chatId) return;
    clearReconnectTimer(chatId);
    failures[chatId] = err || new Error(wbcT("workbenchChat.agentError.failed", "Agent run failed"));
    // The provider has exhausted its bounded retries and emitted the terminal
    // run failure; this is no longer a recoverable transport gap.
    // Remove the live runtime before ownStream.finally so it cannot enter the
    // generic re-sync/reconnect branch and replace the durable error with a
    // fresh thinking card.
    update(chatId, null);
    publishLifecycle(chatId, "failed", err || {});
    fire("onError", chatId, failures[chatId], { terminal: true });
  }

  function appendActivity(cur, fields) {
    var activities = Array.isArray(cur.activities) ? cur.activities : [];
    var nextSeq = Number(cur.activitySeq || 0) + 1;
    return {
      ...cur,
      activitySeq: nextSeq,
      activities: activities.concat([{
        id: "activity_" + nextSeq,
        reasoning: "",
        reasoningActive: false,
        awaitingLlmEvent: false,
        progress: [],
        createdAt: Date.now(),
        ...(fields || {}),
      }]),
    };
  }

  function updateLastActivity(cur, updater, createFields) {
    var activities = Array.isArray(cur.activities) ? cur.activities : [];
    if (!activities.length) {
      cur = appendActivity(cur, createFields || {});
      activities = cur.activities;
    }
    var nextActivities = activities.slice();
    var lastIndex = nextActivities.length - 1;
    nextActivities[lastIndex] = updater(nextActivities[lastIndex] || {});
    return { ...cur, activities: nextActivities };
  }

  function closeActivityTimeline(cur) {
    var activities = Array.isArray(cur.activities) ? cur.activities.slice() : [];
    if (!activities.length) return cur;
    var lastIndex = activities.length - 1;
    activities[lastIndex] = { ...activities[lastIndex], timelineClosed: true };
    return { ...cur, activities: activities };
  }

  function closeTimeline(chatId) {
    update(chatId, function (cur) { return cur ? closeActivityTimeline(cur) : null; });
  }

  function abort(chatId) {
    var ac = aborts[chatId];
    if (ac) { try { ac.abort(); } catch (e) {} }
    delete aborts[chatId];
  }

  function interrupt(chatId, model) {
    if (!chatId) return Promise.resolve(null);
    var request = runtimes[chatId] && model && model.interrupt
      ? model.interrupt(chatId)
      : Promise.resolve(null);
    // Keep the live stream attached until the server has accepted the
    // interruption and repaired the persisted chat status. If the interrupted
    // event wins the race it clears the runtime directly; otherwise aborting
    // here makes ownStream perform one authoritative re-pull.
    return Promise.resolve(request).then(function (result) {
      abort(chatId);
      return result;
    }).catch(function (err) {
      fire("onError", chatId, err);
      return null;
    });
  }

  function deferSend(chatId, input, model) {
    if (!chatId || !model) return null;
    var previous = deferredSends[chatId];
    var nextInput = input || {};
    if (previous && previous.input) {
      var previousText = String(previous.input.message || "").trim();
      var nextText = String(nextInput.message || "").trim();
      nextInput = { ...nextInput, message: [previousText, nextText].filter(Boolean).join("\n\n") };
    }
    deferredSends[chatId] = { input: nextInput, model: model };
    // A sealed guidance endpoint waits for the old run to finish before
    // returning ``chat_not_running``. If its stream already closed first,
    // there will be no later terminal callback to wake this deferred send.
    if (!runtimes[chatId]) {
      var ready = deferredSends[chatId];
      delete deferredSends[chatId];
      return start(chatId, ready.input, ready.model);
    }
    return true;
  }

  // The mounted page registers transcript hooks so a streaming run patches its
  // local transcript / chat list. All are optional and guarded by chatId; when
  // no page is mounted they are simply absent and the page re-pulls on remount.
  function setHooks(next) { hooks = next || null; }
  function fire(name, a, b, c) {
    if (hooks && typeof hooks[name] === "function") {
      try { hooks[name](a, b, c); } catch (e) {}
    }
  }

  function splitActivityAtVisiblePreamble(cur, message) {
    var activities = Array.isArray(cur.activities) ? cur.activities.slice() : [];
    var last = activities.length ? activities[activities.length - 1] : null;
    if (!last) {
      var messageAt = Date.parse(String(message && message.createdAt || ""));
      return appendActivity(cur, {
        createdAt: Math.max(Date.now(), Number.isFinite(messageAt) ? messageAt + 1 : 0),
      });
    }

    // The LLM reasons before emitting its visible preamble, and the tools run
    // after that preamble. Keep those as three distinct timeline events even
    // when the scanner discovers the prose after streaming has already begun.
    var reasoning = String(last.reasoning || "");
    var allProgress = Array.isArray(last.progress) ? last.progress : [];
    activities.pop();
    if (reasoning.trim()) {
      activities.push({
        ...last,
        reasoning: reasoning.replace(/\s+$/, ""),
        progress: [],
        reasoningActive: false,
        timelineClosed: true,
      });
    }

    var nextSeq = Number(cur.activitySeq || 0) + 1;
    var messageAt = Date.parse(String(message && message.createdAt || ""));
    activities.push({
      ...last,
      id: "activity_" + nextSeq,
      reasoning: "",
      reasoningCallStart: 0,
      progressCallStart: 0,
      reasoningActive: false,
      progress: allProgress,
      createdAt: Math.max(Date.now(), Number.isFinite(messageAt) ? messageAt + 1 : 0),
      timelineClosed: false,
    });
    return { ...cur, activitySeq: nextSeq, activities: activities };
  }

  function appendIntermediate(chatId, message) {
    if (!chatId || !message || !message.id) return;
    fire("onIntermediateMessage", chatId, message);
    update(chatId, function (cur) {
      if (!cur) return null;
      var segments = Array.isArray(cur.segments) ? cur.segments : [];
      var messageFiles = Array.isArray(message.attachments) ? message.attachments : [];
      var messageKey = String(message.liveDedupeKey || "").trim();
      if (!messageKey && messageFiles.length === 0) {
        var normalizedMessageContent = String(message.content || "").replace(/\s+/g, " ").trim();
        if (normalizedMessageContent) messageKey = "content:" + normalizedMessageContent;
      }
      var existingIndex = -1;
      for (var si = 0; si < segments.length; si++) {
        var segment = segments[si];
        var segmentMsg = segment && segment.message;
        if (!segmentMsg) continue;
        if (String(segmentMsg.id || "") === String(message.id || "")) {
          existingIndex = si;
          break;
        }
        var segmentFiles = Array.isArray(segmentMsg.attachments) ? segmentMsg.attachments : [];
        var segmentKey = String(segmentMsg.liveDedupeKey || "").trim();
        if (!segmentKey && segmentFiles.length === 0) {
          var normalizedSegmentContent = String(segmentMsg.content || "").replace(/\s+/g, " ").trim();
          if (normalizedSegmentContent) segmentKey = "content:" + normalizedSegmentContent;
        }
        if (messageKey && segmentKey && messageKey === segmentKey) {
          existingIndex = si;
          break;
        }
      }
      if (existingIndex >= 0) {
        var nextSegments = segments.slice();
        var existing = nextSegments[existingIndex] || {};
        nextSegments[existingIndex] = {
          ...existing,
          id: String(message.id || existing.id || ""),
          message: { ...(existing.message || {}), ...message },
          progress: Array.isArray(message.trace) ? message.trace : (Array.isArray(existing.progress) ? existing.progress : []),
        };
        return {
          ...cur,
          text: "",
          progress: [],
          replying: false,
          lastEventAt: Date.now(),
          segments: nextSegments,
        };
      }
      var closed = message.opensActivity
        ? splitActivityAtVisiblePreamble(cur, message)
        : closeActivityTimeline(cur);
      return {
        ...closed,
        text: "",
        progress: [],
        replying: false,
        lastEventAt: Date.now(),
        segments: segments.concat([{
          id: String(message.id),
          message: message,
          progress: Array.isArray(message.trace) ? message.trace : (Array.isArray(cur.progress) ? cur.progress : []),
        }]),
      };
    });
  }

  // Fold a unified ``tool.*`` stream event into the live runtime's tool
  // timeline, mirroring the persistent SSE path: a stable toolCallId updates
  // its row in place so concurrent tools keep their start order, and a
  // completion never regresses richer identity fields.
  function applyStreamToolEvent(chatId, event) {
    if (!chatId || !event) return;
    var toolCallId = String(event.toolCallId || event.tool_call_id || "");
    var toolName = String(event.name || event.tool || event.title || "");
    if (!toolCallId && !toolName) return;
    if (["use_tools", "quit", "send_message", "update_plan_progress"].indexOf(toolName) >= 0) return;
    var status = String(event.status || "running").toLowerCase();
    var eventAt = Number(event.createdAt || event.startedAt);
    if (!Number.isFinite(eventAt)) eventAt = Date.now();
    var terminal = status === "completed" || status === "failed" || event.terminal === true;
    var progress = event.progress && typeof event.progress === "object" ? event.progress : null;
    var entry = {
      kind: "tool",
      toolCallId: toolCallId,
      text: toolName || undefined,
      preview: terminal
        ? String(event.outputSummary || event.inputSummary || "")
        : String(progress && progress.label || event.inputSummary || event.title || ""),
      status: terminal ? "completed" : "running",
      failed: !!event.failed || status === "failed",
      progress: !terminal && progress && Number(progress.total) > 0
        ? Math.max(0, Math.min(1, Number(progress.current) / Number(progress.total)))
        : undefined,
      progressCurrent: progress ? Math.max(0, Number(progress.current) || 0) : undefined,
      progressTotal: progress ? Math.max(0, Number(progress.total) || 0) : undefined,
      input: event.input,
      output: event.output,
      presentation: event.presentation && typeof event.presentation === "object" ? event.presentation : {},
    };
    update(chatId, function (latest) {
      if (!latest) return null;
      if (entry.toolCallId) {
        var matchedToolCall = false;
        function mergeToolProgress(items) {
          var merged = wbcMergeToolOccurrence(items, entry, terminal);
          if (merged.matched) matchedToolCall = true;
          return merged.items;
        }
        var mergedActivities = (Array.isArray(latest.activities) ? latest.activities : []).map(function (activity) {
          return { ...activity, progress: mergeToolProgress(activity && activity.progress) };
        });
        var mergedProgress = mergeToolProgress(latest.progress);
        if (matchedToolCall) {
          return {
            ...latest,
            activities: mergedActivities,
            progress: mergedProgress,
            lastEventAt: Date.now(),
          };
        }
      }
      var latestActivities = Array.isArray(latest.activities) ? latest.activities : [];
      var latestActivity = latestActivities.length ? latestActivities[latestActivities.length - 1] : null;
      var activityHasReasoning = !!String(latestActivity && latestActivity.reasoning || "").trim();
      var activityHasTools = !!(latestActivity && Array.isArray(latestActivity.progress) && latestActivity.progress.length);
      var activityBase = latestActivity && (latestActivity.timelineClosed || (activityHasReasoning && !activityHasTools))
        ? appendActivity(closeActivityTimeline(latest), { createdAt: eventAt })
        : latest;
      var appendedEntry = {
        ...entry,
        reasoningOffset: String(latestActivity && latestActivity.reasoning || "").length,
        startedAt: eventAt,
      };
      var next = updateLastActivity(activityBase, function (activity) {
        var activityProgress = Array.isArray(activity.progress) ? activity.progress : [];
        appendedEntry = {
          ...appendedEntry,
          reasoningOffset: String(activity && activity.reasoning || "").length,
        };
        return { ...activity, progress: activityProgress.concat([appendedEntry]).slice(-40) };
      });
      return {
        ...next,
        lastEventAt: Date.now(),
        progress: latest.progress.concat([appendedEntry]).slice(-40),
      };
    });
  }

  function applyAgentArtifactEvent(chatId, event) {
    if (!chatId || !event) return;
    var payload = wbcAgentEventPayload(event);
    var attachment = payload.attachment && typeof payload.attachment === "object"
      ? payload.attachment
      : null;
    if (!attachment && (payload.uri || payload.url)) {
      var uri = String(payload.uri || payload.url || "");
      attachment = {
        id: String(payload.artifactId || payload.id || uri),
        name: String(payload.title || payload.name || "artifact"),
        content_type: String(payload.mimeType || payload.content_type || "application/octet-stream"),
        kind: String(payload.kind || "file"),
        url: uri,
        size: Number(payload.size || 0),
      };
    }
    if (!attachment) return;
    var artifactId = String(payload.artifactId || attachment.id || attachment.url || "");
    update(chatId, function (cur) {
      if (!cur) return null;
      var artifacts = Array.isArray(cur.artifacts) ? cur.artifacts.slice() : [];
      var index = artifacts.findIndex(function (item) {
        return String(item && (item.artifactId || item.id || item.url) || "") === artifactId;
      });
      var next = { ...attachment, artifactId: artifactId, state: String(payload.state || "") };
      if (index >= 0) artifacts[index] = { ...artifacts[index], ...next };
      else artifacts.push(next);
      return { ...cur, artifacts: artifacts, lastEventAt: Date.now() };
    });
    fire("onAgentArtifact", chatId, { attachment: attachment, artifactId: artifactId });
  }

  function applyAgentUsageEvent(chatId, event) {
    var payload = wbcAgentEventPayload(event);
    update(chatId, function (cur) {
      if (!cur) return null;
      var usage = { ...(cur.usage || {}) };
      [["inputTokens", "prompt_tokens"], ["outputTokens", "completion_tokens"], ["totalTokens", "total_tokens"], ["used", "total_tokens"]].forEach(function (pair) {
        var value = Number(payload[pair[0]] || 0);
        if (value > 0) usage[pair[1]] = value;
      });
      return { ...cur, usage: usage, contextUsage: payload, lastEventAt: Date.now() };
    });
    fire("onAgentUsageUpdated", chatId, payload);
  }

  function applyAgentSessionEvent(chatId, session) {
    if (!chatId || !session) return;
    update(chatId, function (cur) {
      if (!cur) return null;
      var patch = { lastEventAt: Date.now() };
      if (session.sessionId) patch.externalSessionId = session.sessionId;
      if (session.commands.length) patch.agentCommands = session.commands;
      if (session.mode != null) patch.agentMode = session.mode;
      if (session.plan) patch.activePlan = session.plan;
      if (session.configOption || session.configOptions.length) {
        var options = Array.isArray(cur.agentConfigOptions) ? cur.agentConfigOptions.slice() : [];
        var incomingOptions = session.configOptions.concat(session.configOption ? [session.configOption] : []);
        incomingOptions.forEach(function (incoming) {
          var optionId = String(incoming && incoming.id || "");
          var optionIndex = options.findIndex(function (item) { return String(item && item.id || "") === optionId; });
          if (optionId && optionIndex >= 0) options[optionIndex] = { ...options[optionIndex], ...incoming };
          else if (optionId) options.push(incoming);
        });
        patch.agentConfigOptions = options;
      }
      return { ...cur, ...patch };
    });
    fire("onAgentSessionUpdated", chatId, session);
  }

  function resolveAgentRequestEvent(chatId, event) {
    var payload = wbcAgentEventPayload(event);
    var requestId = String(payload.requestId || payload.request_id || "");
    update(chatId, function (cur) {
      if (!cur) return null;
      return { ...cur, awaitingRequestId: "", pendingQuestion: null, lastEventAt: Date.now() };
    });
    fire("onAgentRequestResolved", chatId, event);
  }

  function streamHandlers(chatId) {
    return {
      onEventCursor: function (cursor) {
        update(chatId, function (cur) {
          if (!cur) return null;
          return {
            ...cur,
            eventCursor: Math.max(Number(cur.eventCursor || 0), Number(cursor || 0)),
            reconnecting: false,
            reconnectAttempts: 0,
          };
        }, true);
      },
      onRunStarted: function (event) {
        update(chatId, function (cur) {
          if (!cur) return null;
          var next = { ...cur, lastEventAt: Date.now() };
          if (event && event.activeModel) next.activeModel = String(event.activeModel);
          if (event && (event.session_id || event.sessionId)) next.externalSessionId = String(event.session_id || event.sessionId);
          return next;
        });
        fire("onAgentSessionUpdated", chatId, wbcAgentSessionPayload(event || {}));
      },
      onAck: function (event) {
        if (event.retry) return;
        if (event.userMessage) {
          var runtime = get(chatId);
          var optimisticId = String(runtime && runtime.optimisticUserMessageId || "");
          recordUserMessage(chatId, event.userMessage, optimisticId);
          fire("onUserMessageConfirmed", chatId, {
            optimisticId: optimisticId,
            userMessage: event.userMessage,
          });
        }
      },
      onReplyStart: function () {
        update(chatId, function (cur) { return cur ? { ...cur, replying: true, lastEventAt: Date.now() } : null; });
        fire("onReplyStream", chatId, { text: "", start: true, done: false });
      },
      onReasoningStart: function (event) {
        update(chatId, function (cur) {
          if (!cur) return null;
          var eventPhase = String(event && event.phase || "");
          var eventProvider = String(event && event.provider || "");
          var activities = Array.isArray(cur.activities) ? cur.activities : [];
          var last = activities.length ? activities[activities.length - 1] : null;
          var reuseLlmCard = !!(
            last
            && !last.timelineClosed
            && !(Array.isArray(last.progress) && last.progress.length)
            && (!eventPhase || !last.llmPhase || String(last.llmPhase) === eventPhase)
          );
          var next = reuseLlmCard
            ? updateLastActivity(cur, function (activity) {
                var current = String(activity.reasoning || "");
                var startsContinuousCall = !!(
                  activity.mergeReasoning
                  || (activity.llmStatus === "completed" && activity.reasoningStreamSeen)
                );
                var prior = "";
                if (startsContinuousCall) {
                  prior = current.replace(/\s+$/, "");
                } else if (activity.fallbackReasoningApplied) {
                  var fallbackStart = Math.max(0, Math.min(Number(activity.reasoningCallStart || 0), current.length));
                  prior = current.slice(0, fallbackStart).replace(/\s+$/, "");
                }
                var prefix = prior ? prior + "\n\n" : "";
                return {
                  ...activity,
                  reasoning: prefix,
                  reasoningCallStart: prefix.length,
                  progressCallStart: startsContinuousCall
                    ? (Array.isArray(activity.progress) ? activity.progress.length : 0)
                    : Number(activity.progressCallStart || 0),
                  reasoningActive: true,
                  reasoningStreamSeen: true,
                  mergeReasoning: false,
                  fallbackReasoningApplied: false,
                  llmPhase: eventPhase || activity.llmPhase || "",
                  provider: eventProvider || activity.provider || "",
                };
              })
            : appendActivity(cur, {
                reasoning: "",
                reasoningCallStart: 0,
                progressCallStart: 0,
                reasoningActive: true,
                reasoningStreamSeen: true,
                awaitingLlmEvent: true,
                llmPhase: eventPhase,
                provider: eventProvider,
              });
          return { ...next, lastEventAt: Date.now() };
        });
      },
      onReasoningDelta: function (delta, event) {
        update(chatId, function (cur) {
          if (!cur) return null;
          var eventPhase = String(event && event.phase || "");
          var eventProvider = String(event && event.provider || "");
          var next = updateLastActivity(cur, function (activity) {
            return {
              ...activity,
              reasoning: String(activity.reasoning || "") + delta,
              reasoningActive: true,
              reasoningStreamSeen: true,
              awaitingLlmEvent: true,
              llmPhase: eventPhase || activity.llmPhase || "",
              provider: eventProvider || activity.provider || "",
            };
          }, { reasoningActive: true, reasoningStreamSeen: true, awaitingLlmEvent: true, llmPhase: eventPhase, provider: eventProvider });
          return { ...next, lastEventAt: Date.now() };
        }, true);
      },
      onReasoningDone: function (text, event) {
        update(chatId, function (cur) {
          if (!cur) return null;
          var eventPhase = String(event && event.phase || "");
          var eventProvider = String(event && event.provider || "");
          var next = updateLastActivity(cur, function (activity) {
            var current = String(activity.reasoning || "");
            var callStart = Math.max(0, Math.min(Number(activity.reasoningCallStart || 0), current.length));
            return {
              ...activity,
              reasoning: current.slice(0, callStart) + (text || current.slice(callStart)),
              reasoningActive: false,
              llmPhase: eventPhase || activity.llmPhase || "",
              provider: eventProvider || activity.provider || "",
            };
          }, { reasoning: text || "", reasoningCallStart: 0, awaitingLlmEvent: true, llmPhase: eventPhase, provider: eventProvider });
          return { ...next, lastEventAt: Date.now() };
        });
      },
      onReplyDelta: function (delta) {
        var next = update(chatId, function (cur) { return cur ? { ...cur, replying: true, streamDone: false, text: cur.text + delta, lastEventAt: Date.now() } : null; }, true);
        if (next) fire("onReplyStream", chatId, { text: next.text, start: false, done: false });
      },
      onReplyDone: function (text) {
        var next = update(chatId, function (cur) { return cur ? { ...cur, streamDone: true, text: text || cur.text, lastEventAt: Date.now() } : null; });
        if (next) fire("onReplyStream", chatId, { text: next.text, start: false, done: true });
      },
      onNotification: function (notice) {
        if (!notice || !notice.message) return;
        update(chatId, function (cur) {
          if (!cur) return null;
          var notices = Array.isArray(cur.notifications) ? cur.notifications.slice() : [];
          var key = String(notice.id || (notice.category + "\n" + notice.message));
          if (!notices.some(function (item) {
            return String(item.id || (item.category + "\n" + item.message)) === key;
          })) notices.push(notice);
          return { ...cur, notifications: notices, lastEventAt: Date.now() };
        });
      },
      onFinalizing: function () {
        update(chatId, function (cur) {
          return cur ? { ...wbcFinalizeRuntime(cur), lastEventAt: Date.now() } : null;
        });
      },
      onToolStarted: function (event) { applyStreamToolEvent(chatId, event); },
      onToolUpdated: function (event) { applyStreamToolEvent(chatId, event); },
      onToolCompleted: function (event) { applyStreamToolEvent(chatId, event, true); },
      onArtifactEvent: function (event) { applyAgentArtifactEvent(chatId, event); },
      onUsageUpdated: function (event) { applyAgentUsageEvent(chatId, event); },
      onSessionUpdated: function (event) { applyAgentSessionEvent(chatId, event); },
      onPermissionResolved: function (event) { resolveAgentRequestEvent(chatId, event); },
      onElicitationResolved: function (event) { resolveAgentRequestEvent(chatId, event); },
      onUnknownAgentEvent: function (event) {
        applyStreamToolEvent(chatId, {
          toolCallId: "agent-event:" + String(event && (event.eventId || event.event_id) || Date.now()),
          name: wbcT("workbenchChat.agentEvent", "Agent event") + " · " + String(event && event.type || "unknown"),
          status: "completed",
          outputSummary: wbcStructuredEventSummary(wbcAgentEventPayload(event || {})),
          output: wbcAgentEventPayload(event || {}),
          presentation: { kind: "event" },
        }, true);
      },
      onIntermediateMessage: function (event) { appendIntermediate(chatId, event && event.message); },
      onGuidanceReceived: function (event) {
        if (event && event.userMessage) {
          recordUserMessage(chatId, event.userMessage);
          fire("onUserMessage", chatId, event.userMessage);
        }
        update(chatId, function (cur) {
          if (!cur) return null;
          return { ...closeActivityTimeline(cur), lastEventAt: Date.now() };
        });
      },
      onSaved: function (event) {
        if (event.retry) {
          // The old model output is hidden optimistically when retry starts.
          // Reconcile again with the server's durable replacement ids so a
          // background retry or a late hydration reaches the same transcript.
          fire("onRetryTruncate", chatId, {
            afterId: String(event.truncateAfterMessageId || ""),
            replacedIds: Array.isArray(event.retryReplacedMessageIds) ? event.retryReplacedMessageIds : [],
          });
        }
        var savedMessages = Array.isArray(event.assistantMessages) && event.assistantMessages.length
          ? event.assistantMessages
          : (event.assistantMessage ? [event.assistantMessage] : []);
        // The client-assembled live trace is the authoritative execution
        // history: the backend's transcript extraction can drop mid-run tool
        // calls and drops runtime status fields. Overlay it on the saved cards
        // before they render, and persist it so a reload sees the same data.
        // Failure to persist is fine — the backend-extracted trace remains.
        var durableTrace = wbcDurableTracePayload(chatId, runtimes[chatId], savedMessages);
        if (durableTrace && savedMessages.length) {
          var durableByMessageId = {};
          durableTrace.messageIds.forEach(function (mid, index) {
            durableByMessageId[String(mid || "")] = durableTrace.traces[index];
          });
          savedMessages = savedMessages.map(function (message) {
            var replacement = message && durableByMessageId[String(message.id || "")];
            return replacement ? { ...message, trace: replacement } : message;
          });
        }
        // Even a terminal event without a returned message must settle the
        // rail; message persistence and status convergence are independent.
        fire("onAssistantSaved", chatId, savedMessages, event);
        publishLifecycle(chatId, "completed", event);
        clear(chatId);
        if (durableTrace) wbcPersistDurableTrace(chatId, durableTrace);
        fire("onSettled", chatId);
      },
      onAwaitingUser: function (event) {
        // The run paused for a permission / clarification answer.
        if (event.retry) {
          fire("onRetryTruncate", chatId, {
            afterId: String(event.truncateAfterMessageId || ""),
            replacedIds: Array.isArray(event.retryReplacedMessageIds) ? event.retryReplacedMessageIds : [],
          });
        }
        var awaitingMessages = Array.isArray(event.assistantMessages) ? event.assistantMessages : [];
        if (awaitingMessages.length) fire("onAssistantSaved", chatId, awaitingMessages);
        var pending = event.pending_question || event.pendingQuestion
          || (event && event.kind ? event : null);
        update(chatId, function (cur) {
          return cur ? { ...cur, pendingQuestion: pending || null, lastEventAt: Date.now() } : null;
        });
        fire("onAwaitingUser", chatId, pending);
        publishLifecycle(chatId, "awaiting_user", event);
        // ACP permission/elicitation requests pause the external process but do
        // not end its event stream. Keep the runtime alive so the same stream
        // can resume after the original optionId/text response is forwarded.
        if (pending && ["permission.requested", "elicitation.requested"].indexOf(String(pending.kind || "")) >= 0) {
          return;
        }
        clear(chatId);
        fire("onSettled", chatId);
      },
      onInterrupted: function (event) {
        publishLifecycle(chatId, "cancelled", event);
        clear(chatId);
        fire("onInterrupted", chatId);
      },
      onError: function (err) {
        failRun(chatId, err);
      },
    };
  }

  function scheduleReconnect(chatId, model) {
    if (!chatId || !runtimes[chatId] || !model || !model.reconnectRun || reconnectTimers[chatId]) return;
    var current = runtimes[chatId];
    var attempts = Math.max(0, Number(current.reconnectAttempts || 0)) + 1;
    var delay = Math.min(4000, 250 * Math.pow(2, Math.min(attempts - 1, 4)));
    update(chatId, function (cur) {
      return cur ? { ...cur, reconnecting: true, reconnectAttempts: attempts } : null;
    });
    reconnectTimers[chatId] = setTimeout(function () {
      delete reconnectTimers[chatId];
      if (runtimes[chatId]) reconnect(chatId, model, true);
    }, delay);
  }

  function ownStream(chatId, streamPromise, ac, model) {
    var shouldReconnect = true;
    return streamPromise.catch(function (err) {
      if (err && err.name === "AbortError") {
        shouldReconnect = false;
        return;
      }
      if (err && err.code === "chat_run_in_progress") {
        fire("onResync", chatId);
        return;
      }
      if (err && err.code === "chat_run_not_found") {
        shouldReconnect = false;
        return;
      }
      // A rejected/reconnecting transport is not yet the run's terminal
      // result. The backend/provider owns its bounded retries and will emit a
      // final `error` / `run.failed` event only after those attempts are spent.
      if (!(err && err.code === "chat_run_not_found")) fire("onError", chatId, err, { terminal: false });
    }).finally(function () {
      if (aborts[chatId] === ac) delete aborts[chatId];
      if (runtimes[chatId]) {
        if (shouldReconnect && model && model.reconnectRun) {
          // A transport ending is not a run ending. Keep the assembled
          // activities/segments visible and resume after the highest event
          // cursor instead of replacing the timeline with an empty runtime.
          fire("onResync", chatId);
          scheduleReconnect(chatId, model);
        } else {
          clear(chatId);
          publishLifecycle(chatId, "refresh");
          fire("onResync", chatId);
        }
      }
      // A guidance POST can race the server's finishing window: the UI still
      // has a live stream, but the agent has already returned and correctly
      // rejects new steering. Promote that text to a normal follow-up exactly
      // when this stream closes. No timer/polling is involved—the stream's
      // terminal event wakes the deferred send.
      var deferred = deferredSends[chatId];
      if (deferred) {
        delete deferredSends[chatId];
        start(chatId, deferred.input, deferred.model);
      }
    });
  }

  // Begin a streamed send for `chatId`. No-op (returns null) when a run is
  // already in flight for that conversation, keeping message ordering
  // deterministic; returns the send promise otherwise.
  function start(chatId, input, model) {
    if (!chatId || runtimes[chatId]) return null;
    clearFailure(chatId);
    input = input || {};
    var ac = (typeof AbortController !== "undefined") ? new AbortController() : null;
    if (ac) aborts[chatId] = ac;
    var startedAt = Date.now();
    var clientRequestId = String(input.clientRequestId || ("send_" + startedAt + "_" + Math.random().toString(36).slice(2, 9)));
    input = { ...input, clientRequestId: clientRequestId };
    var optimisticUserMessage = null;
    if (!input.retry) {
      var optimisticId = "user_pending_" + startedAt + "_" + Math.random().toString(36).slice(2, 9);
      optimisticUserMessage = {
        id: optimisticId,
        role: "user",
        content: String(input.message || ""),
        attachments: Array.isArray(input.attachments) ? input.attachments.slice() : [],
        createdAt: new Date(startedAt).toISOString(),
        optimistic: true,
        clientRequestId: clientRequestId,
      };
      // Publish the user's turn before the runtime. React can batch both state
      // changes into one paint, but this ordering guarantees the transcript is
      // already populated when the live thinking card becomes visible.
      fire("onUserMessage", chatId, optimisticUserMessage);
    }
    update(chatId, {
      chatId: chatId,
      text: "",
      progress: [],
      activities: [],
      activitySeq: 0,
      segments: [],
      notifications: [],
      eventCursor: 0,
      reconnectAttempts: 0,
      startedAt: startedAt,
      lastEventAt: startedAt,
      replying: false,
      optimisticUserMessageId: optimisticUserMessage ? optimisticUserMessage.id : "",
      userMessages: optimisticUserMessage ? [optimisticUserMessage] : [],
      clientRequestId: clientRequestId,
    });
    return ownStream(
      chatId,
      model.sendMessage(chatId, input, streamHandlers(chatId), ac ? ac.signal : undefined),
      ac,
      model
    );
  }

  function reconnect(chatId, model, preserveRuntime) {
    if (!chatId || !model || !model.reconnectRun) return null;
    var existing = runtimes[chatId] || null;
    if (existing && !preserveRuntime) return null;
    clearReconnectTimer(chatId);
    var ac = (typeof AbortController !== "undefined") ? new AbortController() : null;
    if (ac) aborts[chatId] = ac;
    if (existing) {
      update(chatId, function (cur) {
        return cur ? { ...cur, reconnecting: true } : null;
      });
    } else {
      update(chatId, { chatId: chatId, text: "", progress: [], activities: [], activitySeq: 0, segments: [], notifications: [], startedAt: Date.now(), lastEventAt: Date.now(), replying: false, reconnecting: true, reconnectAttempts: 0, eventCursor: 0 });
    }
    var cursor = Number(existing && existing.eventCursor || 0);
    return ownStream(
      chatId,
      model.reconnectRun(chatId, streamHandlers(chatId), ac ? ac.signal : undefined, cursor),
      ac,
      model
    );
  }

  // Persistent SSE subscription: fold live tool / phase / subagent progress into
  // the running conversation's runtime regardless of whether its page is
  // mounted. Mirrors the legacy per-component handler but never tears down.
  function onSseEvent(event) {
    if (!event) return;
    var chatId = String(event.session_id || "");
    // The event bridge is process-wide. Ignore warnings and progress for
    // sessions this client is not currently tracking.
    if (!chatId || !runtimes[chatId]) return;
    if (event.type === "budget_warning") {
      var warningError = new Error(String(event.message || ""));
      warningError.code = String(event.code || "");
      workbenchServices.feedback().showToast(wbcErrorText(warningError), "warning");
      return;
    }
    var eventAt = Date.parse(String(event.timestamp || event.createdAt || event.created_at || ""));
    if (!Number.isFinite(eventAt)) eventAt = Date.now();
    if (event.type === "llm_call") {
      update(chatId, function (latest) {
        if (!latest) return null;
        var eventId = String(event.event_id || "");
        var eventReasoning = String(event.response && event.response.reasoning_content || "");
        var eventStatus = String(event.status || "completed").toLowerCase();
        var eventPhase = String(event.phase || "");
        var activities = Array.isArray(latest.activities) ? latest.activities : [];
        var duplicate = eventId && activities.some(function (activity) {
          var ids = activity && Array.isArray(activity.llmEventIds) ? activity.llmEventIds : [];
          return ids.indexOf(eventId) >= 0;
        });
        var next = latest;
        if (!duplicate) {
          var last = activities.length ? activities[activities.length - 1] : null;
          if (eventStatus === "started") {
            // The SSE start event and the direct reasoning stream travel over
            // different connections, so either may arrive first. Reuse the
            // provisional reasoning card (or a retrying start card) instead of
            // creating two cards for one LLM call.
            var continuesActivity = !!(
              last
              && !last.timelineClosed
              && !(Array.isArray(last.progress) && last.progress.length)
              && (!eventPhase || !last.llmPhase || String(last.llmPhase) === eventPhase)
            );
            if (continuesActivity) {
              next = updateLastActivity(latest, function (activity) {
                var eventIds = Array.isArray(activity.llmEventIds) ? activity.llmEventIds : [];
                return {
                  ...activity,
                  awaitingLlmEvent: false,
                  llmStatus: "started",
                  llmStartedEventId: eventId,
                  llmEventIds: eventId ? eventIds.concat([eventId]).slice(-100) : eventIds,
                  model: String(event.model || ""),
                  provider: String(event.provider || activity.provider || ""),
                  llmPhase: eventPhase || activity.llmPhase || "",
                  reasoningStreamSeen: activity.awaitingLlmEvent ? activity.reasoningStreamSeen : false,
                  mergeReasoning: activity.llmStatus === "completed",
                  progressCallStart: activity.llmStatus === "completed"
                    ? (Array.isArray(activity.progress) ? activity.progress.length : 0)
                    : Number(activity.progressCallStart || 0),
                };
              });
            } else {
              next = appendActivity(latest, {
                llmStatus: "started",
                llmStartedEventId: eventId,
                llmEventIds: eventId ? [eventId] : [],
                model: String(event.model || ""),
                provider: String(event.provider || ""),
                llmPhase: eventPhase,
                reasoningCallStart: 0,
                progressCallStart: 0,
                reasoningStreamSeen: false,
              });
            }
          } else if (last && !last.timelineClosed) {
            next = updateLastActivity(latest, function (activity) {
              var eventIds = Array.isArray(activity.llmEventIds) ? activity.llmEventIds : [];
              var current = String(activity.reasoning || "");
              var reasoning = current;
              var callStart = Math.max(0, Math.min(Number(activity.reasoningCallStart || 0), current.length));
              var fallbackApplied = false;
              if (!activity.reasoningStreamSeen && eventReasoning) {
                var prior = activity.mergeReasoning ? current.replace(/\s+$/, "") : "";
                var prefix = prior ? prior + "\n\n" : "";
                reasoning = prefix + eventReasoning;
                callStart = prefix.length;
                fallbackApplied = true;
              }
              return {
                ...activity,
                awaitingLlmEvent: false,
                llmStatus: "completed",
                llmEventId: eventId,
                llmEventIds: eventId ? eventIds.concat([eventId]).slice(-100) : eventIds,
                model: String(event.model || ""),
                provider: String(event.provider || activity.provider || ""),
                llmPhase: eventPhase || activity.llmPhase || "",
                reasoning: reasoning,
                reasoningCallStart: callStart,
                mergeReasoning: false,
                fallbackReasoningApplied: fallbackApplied,
              };
            });
          } else {
            next = appendActivity(latest, {
              llmStatus: "completed",
              llmEventId: eventId,
              llmEventIds: eventId ? [eventId] : [],
              model: String(event.model || ""),
              provider: String(event.provider || ""),
              llmPhase: eventPhase,
              reasoning: eventReasoning,
              reasoningCallStart: 0,
              reasoningStreamSeen: false,
              fallbackReasoningApplied: !!eventReasoning,
            });
          }
        }
        return {
          ...next,
          activeModel: String(event.model || next.activeModel || ""),
          lastEventAt: Date.now(),
        };
      });
      return;
    }
    var entry = null;
    var eventActiveModel = "";
    var terminalToolEvent = false;
    if (event.type === "tool_call_started" || event.type === "tool_call" || event.type === "tool_call_finished" || event.type === "tool_call_progress") {
      var toolEvent = wbcRuntimeToolEvent(event, eventAt);
      if (!toolEvent) return;
      entry = toolEvent.entry;
      terminalToolEvent = toolEvent.terminal;
    } else if (event.type === "phase_transition" && (event.detail || event.detail_key)) {
      var phaseParams = event.detail_params && typeof event.detail_params === "object"
        ? event.detail_params
        : {};
      var phaseFallbackModel = String(
        phaseParams.fallbackModel || phaseParams.fallback_model || ""
      ).trim();
      if (String(event.to || "") === "fallback_model") {
        eventActiveModel = phaseFallbackModel;
      }
      var phaseText = event.detail_key
        ? wbcT(event.detail_key, String(event.detail || ""), phaseParams)
        : String(event.detail || "");
      if (event.alert && workbenchServices.feedback().showToast) {
        workbenchServices.feedback().showToast(
          phaseText,
          String(event.alert_level || "warning")
        );
      }
      entry = {
        kind: "phase",
        text: event.detail ? String(event.detail).slice(0, 160) : "",
        detailKey: event.detail_key || "",
        detailParams: phaseParams,
        preview: "",
        failed: !!event.failed,
      };
    } else if (event.type === "subagent_update") {
      entry = {
        kind: "subagent",
        text: String(event.agent_id || wbcT("workbenchChat.subagents", "Subagents")),
        preview: wbcSubagentStatusText(event.status),
      };
    } else if (event.type === "auto_review" || event.type === "permission_decision") {
      var permissionApproved = event.approved === true;
      entry = {
        kind: "permission",
        text: permissionApproved
          ? wbcT("workbenchChat.permissionApproved", "Permission review approved")
          : wbcT("workbenchChat.permissionDenied", "Permission review denied"),
        preview: [
          String(event.operation || ""),
          String(event.path_hint || ""),
          String(event.rationale || ""),
        ].filter(Boolean).join(" · ").slice(0, 240),
        failed: !permissionApproved,
      };
    } else if (
      (event.type === "guidance_acknowledged" || event.type === "chat_message")
      && event.message
      && event.message.intermediate
    ) {
      appendIntermediate(chatId, event.message);
      return;
    } else if (event.type === "assistant_message" && event.intermediate && event.message) {
      appendIntermediate(chatId, event.message);
      return;
    }
    if (!entry) return;
    update(chatId, function (latest) {
      if (!latest) return null;
      // A completion updates the row created by tool_call_started in place.
      // Mapping instead of remove/reinsert preserves the LLM's tool-call order,
      // including when several tools run concurrently and finish out of order.
      if (entry.toolCallId) {
        var matchedToolCall = false;
        function mergeToolProgress(items) {
          var merged = wbcMergeToolOccurrence(items, entry, terminalToolEvent);
          if (merged.matched) matchedToolCall = true;
          return merged.items;
        }
        var mergedActivities = (Array.isArray(latest.activities) ? latest.activities : []).map(function (activity) {
          return { ...activity, progress: mergeToolProgress(activity && activity.progress) };
        });
        var mergedProgress = mergeToolProgress(latest.progress);
        if (matchedToolCall) {
          return {
            ...latest,
            activities: mergedActivities,
            progress: mergedProgress,
            lastEventAt: Date.now(),
          };
        }
      }
      var latestActivities = Array.isArray(latest.activities) ? latest.activities : [];
      var latestActivity = latestActivities.length ? latestActivities[latestActivities.length - 1] : null;
      var activityHasReasoning = !!String(latestActivity && latestActivity.reasoning || "").trim();
      var activityHasTools = !!(latestActivity && Array.isArray(latestActivity.progress) && latestActivity.progress.length);
      // Reasoning describes the LLM call before its tools start. Close that
      // thought as its own timeline event; only consecutive tool-only calls
      // continue in the same activity card.
      var activityBase = latestActivity && (latestActivity.timelineClosed || (activityHasReasoning && !activityHasTools))
        ? appendActivity(closeActivityTimeline(latest), { createdAt: eventAt })
        : latest;
      var appendedEntry = {
        ...entry,
        reasoningOffset: String(latestActivity && latestActivity.reasoning || "").length,
        startedAt: eventAt,
      };
      var next = updateLastActivity(activityBase, function (activity) {
        var activityProgress = Array.isArray(activity.progress) ? activity.progress : [];
        appendedEntry = {
          ...appendedEntry,
          reasoningOffset: String(activity && activity.reasoning || "").length,
        };
        return { ...activity, progress: activityProgress.concat([appendedEntry]).slice(-40) };
      });
      return {
        ...next,
        activeModel: eventActiveModel || next.activeModel || "",
        lastEventAt: Date.now(),
        progress: latest.progress.concat([appendedEntry]).slice(-40),
      };
    });
  }
  workbenchServices.events().subscribe(onSseEvent);

  return {
    subscribe: subscribe, subscribeSummary: subscribeSummary, snapshot: snapshot, get: get, isRunning: isRunning,
    update: update, recordUserMessage: recordUserMessage, clear: clear, abort: abort, interrupt: interrupt,
    start: start, reconnect: reconnect, deferSend: deferSend, closeTimeline: closeTimeline, setHooks: setHooks,
    publishLifecycle: publishLifecycle, getFailure: getFailure, clearFailure: clearFailure,
  };
})();

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function wbcRuntimePresenceSnapshot(snapshot) {
  var presence = {};
  Object.keys(snapshot || {}).forEach(function (chatId) { presence[chatId] = true; });
  return presence;
}

function wbcSameRuntimePresence(left, right) {
  var leftKeys = Object.keys(left || {});
  var rightKeys = Object.keys(right || {});
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every(function (chatId) { return !!right[chatId]; });
}

function wbcTaskSessionFromStore(store, taskId) {
  var id = String(taskId || "");
  if (!store || !id) return null;
  if (store.session && String(store.session.id || "") === id) return store.session;
  if (store.activeSession && String(store.activeSession.id || "") === id) return store.activeSession;
  var projects = Array.isArray(store.projects) ? store.projects : [];
  for (var index = 0; index < projects.length; index += 1) {
    var sessions = Array.isArray(projects[index] && projects[index].sessions)
      ? projects[index].sessions
      : [];
    var found = sessions.find(function (session) {
      return String(session && session.id || "") === id;
    });
    if (found) return found;
  }
  return null;
}

export { WbcFileVisual, WorkbenchChatRuntimes, wbcCanOpenExternally, wbcChatUsedMap, wbcCommandMeta, wbcDownloadLink, wbcHtmlPreviewDocument, wbcRuntimePresenceSnapshot, wbcSameRuntimePresence, wbcStartFileDrag, wbcTaskSessionFromStore }
