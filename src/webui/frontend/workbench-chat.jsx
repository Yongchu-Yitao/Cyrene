// Workbench 对话页面 — workspace-bound conversations (kind: "chat").
// Independent from the legacy chat UI (chat.jsx / chat-surface.jsx): only the
// backend endpoints (/api/workbench/chats*, /api/chat/upload, /api/events SSE)
// are shared. Layout: chat rail | conversation | right context panel.

var {
  useState: useWbcState,
  useEffect: useWbcEffect,
  useLayoutEffect: useWbcLayoutEffect,
  useMemo: useWbcMemo,
  useRef: useWbcRef,
  useCallback: useWbcCallback,
} = React;

function wbcWorkspaceDisplayName(path) {
  var normalized = String(path || "").replace(/[\\/]+$/, "");
  return normalized.split(/[\\/]/).filter(Boolean).pop() || normalized || "…";
}

var WBC_RESOURCE_DRAG_MIME = "application/x-cyrene-work-resource+json";
var WBC_CHAT_DRAG_MIME = "application/x-cyrene-chat+json";
var WBC_CHAT_GROUP_DRAG_MIME = "application/x-cyrene-chat-group+json";

function wbcSetChatDrag(event, chat) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer || !chat || !chat.id) return;
  try {
    transfer.effectAllowed = "move";
    transfer.setData(WBC_CHAT_DRAG_MIME, JSON.stringify({
      kind: "chat",
      id: String(chat.id),
      projectId: String(chat.projectId || ""),
      title: String(chat.title || ""),
    }));
    transfer.setData("text/plain", String(chat.id));
  } catch (e) {}
}

function wbcHasChatDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return false;
  try {
    return Array.prototype.slice.call(transfer.types || []).indexOf(WBC_CHAT_DRAG_MIME) >= 0;
  } catch (e) {
    return false;
  }
}

function wbcSetChatGroupDrag(event, group, projectId) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer || !group || !group.id) return;
  try {
    transfer.effectAllowed = "move";
    transfer.setData(WBC_CHAT_GROUP_DRAG_MIME, JSON.stringify({
      kind: "chat-group",
      id: String(group.id),
      projectId: String(projectId || ""),
      title: String(group.title || ""),
      chatIds: (Array.isArray(group.chatIds) ? group.chatIds : []).map(String),
    }));
    transfer.setData("text/plain", String(group.title || group.id));
  } catch (e) {}
}

function wbcHasChatGroupDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return false;
  try {
    return Array.prototype.slice.call(transfer.types || []).indexOf(WBC_CHAT_GROUP_DRAG_MIME) >= 0;
  } catch (e) {
    return false;
  }
}

function wbcHasChatRailDrag(event) {
  return wbcHasChatDrag(event) || wbcHasChatGroupDrag(event);
}

function wbcReadChatDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return null;
  try {
    var payload = JSON.parse(transfer.getData(WBC_CHAT_DRAG_MIME) || "null");
    return payload && payload.kind === "chat" && payload.id ? payload : null;
  } catch (e) {
    return null;
  }
}

// The right drop zone for rail chats: the side panel track on wide windows,
// or a reserved band at the page's right edge when the panel is hidden
// (display:none below 980px). The conversation column's own drag handling is
// untouched — a dedicated drop layer sits above this zone only while a chat
// drag is in progress, so the rest of the main area keeps its original
// "drop to open" behaviour.
function wbcChatSideZoneRect() {
  var page = document.querySelector(".wbc-page");
  if (!page) return null;
  var pr = page.getBoundingClientRect();
  if (!pr.width) return null;
  var side = document.querySelector(".wbc-side");
  if (side) {
    var sr = side.getBoundingClientRect();
    if (sr.width > 0) {
      return { left: sr.left, top: pr.top, right: sr.right, bottom: pr.bottom };
    }
  }
  var zoneWidth = Math.max(300, Math.min(340, Math.round(pr.width * 0.32)));
  return {
    left: pr.right - zoneWidth,
    top: pr.top,
    right: pr.right,
    bottom: pr.bottom,
  };
}

function wbcChatSideDropZone(event) {
  if (event.clientX == null || event.clientY == null) return false;
  var zone = wbcChatSideZoneRect();
  if (!zone) return false;
  return event.clientX >= zone.left && event.clientX <= zone.right
    && event.clientY >= zone.top && event.clientY <= zone.bottom;
}

var WBC_SPLIT_DRAG_MIME = "application/x-cyrene-split+json";

function wbcSetSplitDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return;
  try {
    transfer.effectAllowed = "move";
    transfer.setData(WBC_SPLIT_DRAG_MIME, "1");
  } catch (e) {}
}

function wbcHasSplitDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return false;
  try {
    return Array.prototype.slice.call(transfer.types || []).indexOf(WBC_SPLIT_DRAG_MIME) >= 0;
  } catch (e) {
    return false;
  }
}

function wbcEscapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function wbcSetResourceDrag(event, payload) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer || !payload) return;
  try {
    transfer.effectAllowed = "copy";
    transfer.setData(WBC_RESOURCE_DRAG_MIME, JSON.stringify(payload));
    transfer.setData("text/plain", payload.kind === "snippet"
      ? String(payload.text || "")
      : String(payload.title || payload.name || payload.url || ""));
  } catch (e) {}
}

function wbcReadResourceDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return null;
  try {
    var raw = transfer.getData(WBC_RESOURCE_DRAG_MIME);
    if (raw) return JSON.parse(raw);
    var types = Array.prototype.slice.call(transfer.types || []);
    if (types.indexOf("Files") >= 0 || types.indexOf("text/plain") < 0) return null;
    // macOS already gives selected text a native Chromium drag. Preserve that
    // interaction and turn its text/plain payload into the same snippet shape
    // used by the pinned-resource API; the server converts it to Markdown.
    var text = String(transfer.getData("text/plain") || "").trim();
    if (!text) return null;
    var page = document.querySelector(".wbc-page");
    var ownerSessionId = String(page && page.getAttribute("data-active-chat-id") || "");
    var ownerProjectId = String(page && page.getAttribute("data-project-id") || "");
    return {
      kind: "snippet",
      ownerSessionId: ownerSessionId,
      ownerProjectId: ownerProjectId,
      stableRef: "snippet:" + ownerSessionId + ":" + Date.now(),
      title: text.replace(/\s+/g, " ").slice(0, 48),
      text: text.slice(0, 12000),
    };
  } catch (e) {
    return null;
  }
}

function wbcFileDragPayload(file, ownerSessionId, ownerProjectId) {
  var safeFile = {
    id: file && file.id,
    name: file && file.name,
    content_type: file && file.content_type,
    size: file && file.size,
    kind: file && file.kind,
    url: file && file.url,
    width: file && file.width,
    height: file && file.height,
  };
  return {
    kind: "file",
    ownerSessionId: String(ownerSessionId || ""),
    ownerProjectId: String(ownerProjectId || ""),
    stableRef: String(file && (file.url || file.id || file.name) || ""),
    title: String(file && (file.name || file.title) || "file"),
    name: String(file && file.name || "file"),
    url: String(file && file.url || ""),
    content_type: String(file && file.content_type || ""),
    size: Number(file && file.size || 0),
    file: safeFile,
  };
}

window.CyreneUI.resources = window.CyreneUI.register("resources", {
  mime: WBC_RESOURCE_DRAG_MIME,
  readDrag: wbcReadResourceDrag,
  setDrag: wbcSetResourceDrag,
  filePayload: wbcFileDragPayload,
});

// ---------------------------------------------------------------------------
// Data access
// ---------------------------------------------------------------------------

var WorkbenchChatModel = (function () {
  // Route ordinary JSON calls through the shared wrapper (workbench-api.jsx):
  // a 30s AbortController timeout so a stalled backend no longer spins forever,
  // plus normalized errors. toast:false keeps this conversation's own inline
  // error banner (setError → wbcErrorText) as the single feedback channel;
  // callers can pass a longer/disabled `timeout` per call.
  function apiJson(url, options) {
    return window.CyreneUI.require("api").json(url, { toast: false, ...(options || {}) });
  }

  function listChats(projectId) {
    return apiJson("/api/workbench/chats?project=" + encodeURIComponent(projectId || ""))
      .then(function (payload) { return Array.isArray(payload.chats) ? payload.chats : []; });
  }

  function createChat(projectId, title) {
    return apiJson("/api/workbench/chats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: projectId, title: title || "" }),
    }).then(function (payload) { return payload.chat; });
  }

  function listSideAgents(chatId) {
    if (!chatId || String(chatId).indexOf("legacy:") === 0) {
      return Promise.resolve([]);
    }
    return apiJson(
      "/api/workbench/chats/" + encodeURIComponent(chatId) + "/side-agents"
    ).then(function (payload) {
      return Array.isArray(payload.agents) ? payload.agents : [];
    });
  }

  function createSideAgent(chatId, quote) {
    return apiJson(
      "/api/workbench/chats/" + encodeURIComponent(chatId) + "/side-agents",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quote: String(quote || "") }),
      }
    ).then(function (payload) { return payload.agent; });
  }

  function getChat(chatId, options) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), options)
      .then(function (payload) { return payload.chat; });
  }

  function getSubagents(chatId, roundId, options) {
    if (!chatId || String(chatId).indexOf("legacy:") === 0) {
      return Promise.resolve({ rounds: [], activeRoundId: "", agents: [], messages: [] });
    }
    var query = roundId ? ("?round_id=" + encodeURIComponent(roundId)) : "";
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/subagents" + query, options);
  }

  function getChanges(chatId, options) {
    if (!chatId || String(chatId).indexOf("legacy:") === 0) {
      return Promise.resolve({ changeSets: [], fileCount: 0, additions: 0, deletions: 0 });
    }
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/changes", options);
  }

  function getChangeDiff(chatId, changeSetId, path, options) {
    return apiJson(
      "/api/workbench/chats/" + encodeURIComponent(chatId)
        + "/changes/" + encodeURIComponent(changeSetId)
        + "/files/" + String(path || "").split("/").map(encodeURIComponent).join("/"),
      options
    ).then(function (payload) { return payload.change || {}; });
  }

  function getInbox(chatId, options) {
    if (!chatId || String(chatId).indexOf("legacy:") === 0) {
      return Promise.resolve({
        active: false,
        runStatus: "idle",
        counts: { queued: 0, claimed: 0, completed: 0, failed: 0, cancelled: 0, total: 0 },
        events: [],
        tools: [],
      });
    }
    return apiJson(
      "/api/workbench/chats/" + encodeURIComponent(chatId) + "/inbox",
      options
    );
  }

  function renameChat(chatId, title) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title }),
    }).then(function (payload) { return payload.chat; });
  }

  function generateChatGroupMetadata(input) {
    return apiJson("/api/workbench/chat-groups/metadata", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input || {}),
      timeout: 120000,
      toast: false,
    }).then(function (payload) {
      return {
        metadata: payload.metadata || {},
        group: payload.group || null,
      };
    });
  }

  function listChatGroups(projectId) {
    if (!String(projectId || "").trim()) {
      return Promise.resolve({ groups: [], migrationRequired: false });
    }
    return apiJson("/api/workbench/chat-groups?project=" + encodeURIComponent(projectId || ""), {
      toast: false,
    });
  }

  function replaceChatGroups(input) {
    return apiJson("/api/workbench/chat-groups", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input || {}),
      toast: false,
    });
  }

  function migrateChatGroups(input) {
    return apiJson("/api/workbench/chat-groups/migrate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input || {}),
      toast: false,
    });
  }

  function deleteChat(chatId) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), { method: "DELETE" });
  }

  function toTask(chatId, input) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/to-task", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input || {}),
      timeout: 180000, // LLM reads & distills the whole conversation — long budget
    });
  }

  function compactChat(chatId) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/compact", {
      method: "POST",
    });
  }

  function interrupt(chatId) {
    return fetch("/api/chat/interrupt?session_id=" + encodeURIComponent(chatId), { method: "POST" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response;
      });
  }

  function uploadFiles(files) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return Promise.resolve([]);
    var form = new FormData();
    list.forEach(function (f) { form.append("files", f); });
    // Uploads can be large — give a generous budget rather than the 30s default,
    // and let the caller surface failures (the composer toasts on upload error).
    return window.CyreneUI.require("api").fetch("/api/chat/upload", { method: "POST", body: form, timeout: 120000 }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (payload) {
        if (!r.ok) throw new Error(payload.error || ("HTTP " + r.status));
        return Array.isArray(payload.files) ? payload.files : [];
      });
    });
  }

  function consumeEventStream(response, handlers) {
    handlers = handlers || {};
    if (!response.ok) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        var err = new Error(payload.error || payload.detail || ("HTTP " + response.status));
        err.code = payload.code || "";
        err.detailKey = payload.detail_key || payload.detailKey || "";
        err.detailParams = payload.detail_params || payload.detailParams || {};
        err.status = response.status;
        throw err;
      });
    }
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";

    function handleLine(line) {
      if (!line.trim()) return;
      var event;
      try { event = JSON.parse(line); } catch (e) { return; }
      var type = String(event.type || "");
      if (type === "ack" && handlers.onAck) handlers.onAck(event);
      else if (type === "intermediate_message" && handlers.onIntermediateMessage) handlers.onIntermediateMessage(event);
      else if (type === "reasoning_start" && handlers.onReasoningStart) handlers.onReasoningStart(event);
      else if (type === "reasoning_delta" && handlers.onReasoningDelta) handlers.onReasoningDelta(event.delta || "", event);
      else if (type === "reasoning_done" && handlers.onReasoningDone) handlers.onReasoningDone(event.response || "", event);
      else if (type === "reply_start" && handlers.onReplyStart) handlers.onReplyStart(event);
      else if (type === "reply_delta" && handlers.onReplyDelta) handlers.onReplyDelta(event.delta || "");
      else if (type === "reply_done" && handlers.onReplyDone) handlers.onReplyDone(event.response || "");
      else if (type === "run_finalizing" && handlers.onFinalizing) handlers.onFinalizing(event);
      else if (type === "saved" && handlers.onSaved) handlers.onSaved(event);
      else if (type === "awaiting_user" && handlers.onAwaitingUser) handlers.onAwaitingUser(event);
      else if (type === "guidance_received" && handlers.onGuidanceReceived) handlers.onGuidanceReceived(event);
      else if (type === "workspace_changes") {
        if (handlers.onWorkspaceChanges) handlers.onWorkspaceChanges(event);
        try { window.dispatchEvent(new CustomEvent("workbench:workspace-changes", { detail: event })); } catch (e) {}
      }
      else if (type === "interrupted" && handlers.onInterrupted) handlers.onInterrupted(event);
      else if (type === "error" && handlers.onError) {
        var streamError = new Error(event.message || wbcT("settings.failed", "Failed"));
        streamError.code = event.code || event.failure_kind || "";
        streamError.detailKey = event.detail_key || event.detailKey || "";
        streamError.detailParams = event.detail_params || event.detailParams || {};
        streamError.errorType = event.error || "";
        handlers.onError(streamError);
      }
    }

    function pump() {
      return reader.read().then(function (step) {
        if (step.done) {
          if (buffer) handleLine(buffer);
          return null;
        }
        buffer += decoder.decode(step.value, { stream: true });
        var lines = buffer.split("\n");
        buffer = lines.pop();
        lines.forEach(handleLine);
        return pump();
      });
    }
    return pump();
  }

  // Streaming send. handlers: { onAck, onReplyStart, onReplyDelta, onReplyDone, onFinalizing, onSaved, onError }
  function sendMessage(chatId, input, handlers, signal) {
    return fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: input.message || "",
        clientRequestId: input.clientRequestId || "",
        attachments: input.attachments || [],
        mode: input.mode || "default",
        command: input.command || "",
        model: input.model || "",
        reasoningEffort: input.reasoningEffort || "",
        retry: !!input.retry,
        forkReplay: !!input.forkReplay,
        stream: true,
        lang: window.CyreneUI.require("i18n").getLang(),
      }),
      signal: signal,
    }).then(function (response) {
      return consumeEventStream(response, handlers);
    });
  }

  function reconnectRun(chatId, handlers, signal) {
    return fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/run-stream", {
      method: "GET",
      signal: signal,
    }).then(function (response) {
      return consumeEventStream(response, handlers);
    });
  }

  function sendGuidance(chatId, message, clientRequestId) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/guidance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message || "", clientRequestId: clientRequestId || "" }),
      // Guidance is optimistically visible and idempotent. Do not turn a slow
      // durable acknowledgement into a false failure after the agent accepted it.
      timeout: 0,
    });
  }

  // Answer a paused chat run's permission / clarification question → resume.
  // Resolves to { awaitingUser, assistantMessage?, pendingQuestion? }.
  function answerChat(chatId, questionId, answerText, options) {
    options = options || {};
    // Resumes an agent round (open-ended LLM work) — no death timeout. toast:false
    // because handleAnswer restores the prompt and surfaces the error itself.
    return window.CyreneUI.require("api").json("/api/workbench/chats/" + encodeURIComponent(chatId) + "/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_id: questionId || "", answer: answerText || "", mode: options.mode || undefined }),
      timeout: 0,
      toast: false,
    });
  }

  // Fork a conversation at an edited user message. Creates a new chat with the
  // prefix transcript + the edited user entry, and seeds the agent state. The
  // caller then replays the edit via sendMessage({ retry: true, forkReplay: true }).
  function forkChat(chatId, messageId, content) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/fork", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messageId: messageId || "", content: content || "" }),
    }).then(function (payload) { return payload.chat; });
  }

  var service = {
    listChats: listChats,
    createChat: createChat,
    listSideAgents: listSideAgents,
    createSideAgent: createSideAgent,
    getChat: getChat,
    getSubagents: getSubagents,
    getChanges: getChanges,
    getChangeDiff: getChangeDiff,
    getInbox: getInbox,
    renameChat: renameChat,
    generateChatGroupMetadata: generateChatGroupMetadata,
    listChatGroups: listChatGroups,
    replaceChatGroups: replaceChatGroups,
    migrateChatGroups: migrateChatGroups,
    deleteChat: deleteChat,
    toTask: toTask,
    compactChat: compactChat,
    interrupt: interrupt,
    uploadFiles: uploadFiles,
    sendMessage: sendMessage,
    sendGuidance: sendGuidance,
    reconnectRun: reconnectRun,
    answerChat: answerChat,
    forkChat: forkChat,
  };
  return service;
})();

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

var wbcChatCacheState = { lists: {}, details: {}, subagents: {} };
var wbcLastChatByProject = {};
function wbcChatCache() { return wbcChatCacheState; }

function wbcRenderMarkdown(text, options) {
  return window.CyreneUI.require("markdown").renderRich(text, options);
}

function wbcRenderMapMarkdown(text) {
  var source = String(text == null ? "" : text).replace(/\\r\\n|\\n|\\r/g, "\n");
  var html = wbcRenderMarkdown(source);
  if (html && !(source.indexOf("**") >= 0 && html.indexOf("**") >= 0)) return html;
  // Leaflet can initialize before the full Markdown parser on a cold desktop
  // load. Keep map notes readable in that short fallback window as well.
  var markdown = window.CyreneUI.require("markdown");
  var safe = markdown.escapeHtml(source)
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\n/g, "<br>");
  return markdown.sanitizeHtml(safe);
}

function wbcClampSideSplitWidth(value, viewportWidth) {
  var viewport = Math.max(0, Number(viewportWidth) || Number(window.innerWidth) || 0);
  // Mirror the grid's --wbc-main-min-width (clamp(380px, 36vw, 440px)) so the
  // split never reserves more room than the window actually has. A fixed
  // lower floor under-estimates the lane and over-reserves the split, which
  // pushes the panel past the window edge on smaller sizes.
  var mainMin = Math.min(440, Math.max(380, viewport * 0.36));
  // A split on the left side hides the right panel track (grid column 4 is 0),
  // so both anchored sides reserve the same room for the conversation lane.
  var maxWidth = Math.min(900, Math.max(mainMin, viewport - 230 - mainMin));
  return Math.round(Math.max(340, Math.min(maxWidth, Number(value) || 520)));
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
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    if (date >= new Date(startOfDay.getTime() - dayMs)) return wbcT("workbenchChat.time.yesterday", "Yesterday");
    var days = Math.floor((startOfDay.getTime() - date.getTime()) / dayMs) + 1;
    if (days <= 7) return wbcT("workbenchChat.time.daysAgo", "{n}d ago", { n: days });
    return date.toLocaleDateString([], { month: "2-digit", day: "2-digit" });
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

function wbcPreserveLiveTimelineAnchors(previousChat, hydratedChat, runtime) {
  if (!hydratedChat || !runtime) return hydratedChat;
  var runtimeRequestId = String(runtime.clientRequestId || "");
  var runtimeMessageId = String(runtime.confirmedUserMessageId || "");
  if (!runtimeRequestId && !runtimeMessageId) return hydratedChat;
  var previousMessages = previousChat && Array.isArray(previousChat.messages)
    ? previousChat.messages
    : [];
  if (!previousMessages.length || !Array.isArray(hydratedChat.messages)) return hydratedChat;
  var anchor = previousMessages.find(function (message) {
    if (!message || !message.createdAt) return false;
    return (runtimeRequestId && String(message.clientRequestId || "") === runtimeRequestId)
      || (runtimeMessageId && String(message.id || "") === runtimeMessageId);
  });
  if (!anchor) return hydratedChat;
  var changed = false;
  var messages = hydratedChat.messages.map(function (message) {
    if (!message) return message;
    var matches = (runtimeRequestId && String(message.clientRequestId || "") === runtimeRequestId)
      || (runtimeMessageId && String(message.id || "") === runtimeMessageId);
    if (!matches) return message;
    var serverCreatedAt = String(message.createdAt || message.created_at || "");
    if (String(anchor.createdAt) === serverCreatedAt) return message;
    changed = true;
    return {
      ...message,
      createdAt: anchor.createdAt,
      serverCreatedAt: serverCreatedAt,
    };
  });
  return changed ? { ...hydratedChat, messages: messages } : hydratedChat;
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
  var current = Array.isArray(chat.messages) ? chat.messages : [];
  var knownIds = new Set(current.map(function (message) {
    return String(message && message.id || "");
  }));
  var additions = (Array.isArray(assistantMessages) ? assistantMessages : []).filter(function (message) {
    var id = String(message && message.id || "");
    if (!id || knownIds.has(id)) return false;
    knownIds.add(id);
    return true;
  });
  return {
    ...chat,
    status: "idle",
    messages: wbcMergeChronologicalMessages(current, additions),
  };
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

function wbcRuntimeTimelineMessages(runtime) {
  if (!runtime) return [];
  var startedAt = Number(runtime.startedAt || Date.now());
  var activities = Array.isArray(runtime.activities) && runtime.activities.length
    ? runtime.activities
    : [{ id: "activity_1", reasoning: "", progress: [] }];
  var items = [{
    id: "runtime_heartbeat_" + String(runtime.chatId || "chat"),
    role: "assistant",
    createdAt: new Date(startedAt + 1).toISOString(),
    runtimeHeartbeat: true,
    runtimeFinalizing: !!runtime.finalizing,
  }];
  activities.forEach(function (activity, index) {
    items.push({
      id: "runtime_" + String(activity.id || index),
      role: "assistant",
      createdAt: new Date(Number(activity.createdAt || startedAt + index + 2)).toISOString(),
      runtimeActivity: activity,
      runtimeActivityActive: !runtime.finalizing && index === activities.length - 1,
      runtimeActivityHasReplyText: !!runtime.text,
    });
  });
  return items;
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
  };
}

function wbcTraceDedupeKey(trace) {
  if (!Array.isArray(trace) || !trace.length) return "";
  return JSON.stringify(trace.map(function (entry) {
    var item = entry || {};
    return [
      String(item.tool || item.text || ""),
      String(item.preview || ""),
      String(item.kind || ""),
    ];
  }));
}

function wbcCurrentModel(chat, project, runtime, liveData) {
  var activeModel = String(runtime && runtime.activeModel || "").trim();
  if (activeModel) return activeModel;
  var liveModel = String(liveData && liveData.model || "").trim();
  if (liveModel) return liveModel;
  var messages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  for (var i = messages.length - 1; i >= 0; i--) {
    var messageModel = String(messages[i] && messages[i].model || "").trim();
    if (messageModel) return messageModel;
  }
  return String(
    (chat && (chat.lastModel || chat.model))
    || (project && project.model)
    || ""
  ).trim();
}

function wbcSubagentStatusText(status) {
  var key = String(status || "").trim().toLowerCase();
  var labels = {
    running: wbcT("workbenchChat.subagent.status.running", "Running"),
    resumed: wbcT("workbenchChat.subagent.status.resumed", "Resumed"),
    waiting: wbcT("workbenchChat.subagent.status.waiting", "Waiting"),
    done: wbcT("workbenchChat.subagent.status.done", "Done"),
    timeout: wbcT("workbenchChat.subagent.status.timeout", "Timed out"),
  };
  return labels[key] || key || wbcT("workbenchChat.subagent.status.unknown", "Unknown");
}

function wbcSubagentStatusClass(status) {
  var key = String(status || "").trim().toLowerCase();
  if (key === "running" || key === "resumed") return "running";
  if (key === "waiting") return "waiting";
  if (key === "timeout") return "error";
  return "done";
}

// Deterministic per-agent accent colors for the subagent chat room. Defined here
// (not shared with any historical chat renderer constants) so conversation
// components keep zero front-end coupling.
var WBC_SUBAGENT_COLORS = [
  "#3b82f6", "#e8734a", "#1f9d57", "#d94a8c", "#8b6cc4",
  "#d9a64a", "#0ea5a3", "#c2570f", "#6366f1", "#7cb518",
];

function wbcAgentColor(agentId) {
  var id = String(agentId || "");
  var hash = 0;
  for (var i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash) + id.charCodeAt(i);
    hash |= 0;
  }
  return WBC_SUBAGENT_COLORS[Math.abs(hash) % WBC_SUBAGENT_COLORS.length];
}

// Two-letter avatar initials from an agent id like "research_a" -> "RA".
function wbcAgentInitials(name) {
  var raw = String(name || "").trim();
  if (!raw) return "?";
  var parts = raw.split(/[\s_\-.]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return raw.slice(0, 2).toUpperCase();
}

// Highlight @mentions only when they name a known agent (or everyone), so the
// pass never corrupts emails / links produced by the markdown renderer.
function wbcHighlightMentions(html, agentIds) {
  return String(html == null ? "" : html).replace(
    /@([\w一-龥][\w.\-一-龥]*)/g,
    function (full, name) {
      var known = agentIds && agentIds.indexOf(name) >= 0;
      if (known || name === "所有人" || name === "all" || name === "everyone") {
        return '<span class="wbc-subagent-mention">@' + name + "</span>";
      }
      return full;
    }
  );
}

function wbcCompactNumber(value) {
  var num = Number(value || 0);
  if (!num) return "0";
  if (num >= 1000000) return (num / 1000000).toFixed(1) + "M";
  if (num >= 1000) return (num / 1000).toFixed(1) + "k";
  return String(num);
}

function wbcT(key, fallback, params) {
  var i18n = window.CyreneUI.require("i18n");
  if (typeof i18n.t === "function") {
    var value = i18n.t(key, params, fallback);
    if (value && value !== key) return value;
  }
  if (params && fallback) {
    Object.keys(params).forEach(function (name) {
      fallback = fallback.split("{" + name + "}").join(String(params[name]));
    });
  }
  return fallback || key;
}

function wbcToolPreviewText(preview) {
  var text = String(preview || "");
  if (!text) return "";
  var operationKeys = {
    discover: "toolOperation.discover",
    describe: "toolOperation.describe",
    invoke: "toolOperation.invoke",
  };
  return text.split(", ").map(function (part) {
    var token = part.trim();
    if (operationKeys[token]) return wbcT(operationKeys[token], token);
    // Progressive calls expose stable capability IDs in their arguments.
    // Resolve only values with an existing tool-name translation; arbitrary
    // user input, paths, queries, and other arguments must remain untouched.
    var localizedToolName = wbcT("toolName." + token, token);
    return localizedToolName !== token ? localizedToolName : part;
  }).join(", ");
}

function wbcToolArgsPreview(args) {
  if (!args || typeof args !== "object") return "";
  return Object.values(args).map(function (value) {
    if (value == null || value === "") return "";
    if (typeof value === "object") {
      try {
        return JSON.stringify(value) || "";
      } catch (_) {
        return "";
      }
    }
    return String(value);
  }).filter(Boolean).join(", ").slice(0, 60);
}

function wbcThinkingPhrases() {
  return wbcT(
    "workbenchChat.thinkingPhrases",
    "Thinking this through|Checking the details|Reviewing the context|Verifying the result"
  ).split("|").filter(Boolean);
}

function wbcRandomThinkingPhrase() {
  var phrases = wbcThinkingPhrases();
  return phrases[Math.floor(Math.random() * phrases.length)] || wbcT("workbenchChat.stillWorking", "Still working…");
}

function wbcBrowserFullscreenStatusText(runtime) {
  if (runtime && runtime.finalizing) {
    return wbcT("workbenchChat.status.saving", "Saving");
  }
  if (runtime && String(runtime.text || "").trim()) {
    return wbcT("workbenchChat.browserChatReplying", "Agent is replying…");
  }
  var activities = runtime && Array.isArray(runtime.activities) ? runtime.activities : [];
  var activity = activities.length ? activities[activities.length - 1] : null;
  var progress = activity && Array.isArray(activity.progress) && activity.progress.length
    ? activity.progress
    : (runtime && Array.isArray(runtime.progress) ? runtime.progress : []);
  var entry = progress.length ? progress[progress.length - 1] : null;
  if (entry) {
    var key = entry.text || entry.tool || "";
    if (entry.kind === "tool" || entry.tool) return wbcT("toolName." + key, key);
    if (entry.detailKey) return wbcT(entry.detailKey, key, entry.detailParams);
    if (key) return key;
  }
  return wbcT("workbenchChat.browserChatWorking", "Agent is working in the browser…");
}

function wbcBrowserPageTitle(browserState) {
  var browser = browserState || {};
  var activeTab = browser.activeTab || {};
  var title = String(activeTab.title || browser.title || "").trim();
  if (title && title !== "about:blank") return title;
  var rawUrl = String(activeTab.url || browser.url || browser.frameUrl || "").trim();
  if (rawUrl && rawUrl !== "about:blank") {
    try {
      var host = new URL(rawUrl).hostname.replace(/^www\./, "");
      if (host) return host;
    } catch (e) {}
  }
  return "";
}

function wbcBrowserWindowTitle(browserState) {
  var page = wbcBrowserPageTitle(browserState);
  if (page) return wbcT("workbenchChat.browserWindowTitleWithPage", "Browser · {page}", { page: page });
  return wbcT("workbenchChat.browserWindowTitle", "Browser");
}

function wbcClampBrowserWindowFrame(frame, areaWidth, areaHeight, minWidth, minHeight) {
  var aw = Math.max(0, Number(areaWidth) || 0);
  var ah = Math.max(0, Number(areaHeight) || 0);
  var mw = Math.min(Math.max(1, Number(minWidth) || 1), aw || 1);
  var mh = Math.min(Math.max(1, Number(minHeight) || 1), ah || 1);
  var width = Math.min(Math.max(mw, Number(frame && frame.width) || mw), aw || mw);
  var height = Math.min(Math.max(mh, Number(frame && frame.height) || mh), ah || mh);
  var x = Math.min(Math.max(0, Number(frame && frame.x) || 0), Math.max(0, aw - width));
  var y = Math.min(Math.max(0, Number(frame && frame.y) || 0), Math.max(0, ah - height));
  return { x: x, y: y, width: width, height: height };
}

function wbcKeepBrowserWindowClearOfComposer(frame, area) {
  if (!frame || !area || !area.closest) return frame;
  var main = area.closest(".wbc-main");
  var composer = main && main.querySelector(":scope > .wbc-composer");
  if (!composer) return frame;
  var areaRect = area.getBoundingClientRect();
  var composerRect = composer.getBoundingClientRect();
  var composerLeft = composerRect.left - areaRect.left;
  var composerRight = composerRect.right - areaRect.left;
  var overlapsComposerColumn = frame.x < composerRight && frame.x + frame.width > composerLeft;
  if (!overlapsComposerColumn) return frame;
  var gap = 10;
  var ceiling = Math.max(0, composerRect.top - areaRect.top - gap);
  if (frame.y + frame.height <= ceiling) return frame;
  var height = Math.min(frame.height, Math.max(180, ceiling));
  return Object.assign({}, frame, {
    y: Math.max(0, ceiling - height),
    height: height,
  });
}

var WBC_BROWSER_FRAME_STORAGE_PREFIX = "wbc-browser-window-frame:";

function wbcLoadBrowserWindowFrame(sessionId) {
  var key = String(sessionId || "").trim();
  if (!key) return null;
  try {
    var saved = JSON.parse(localStorage.getItem(WBC_BROWSER_FRAME_STORAGE_PREFIX + key) || "null");
    if (!saved || typeof saved !== "object") return null;
    var frame = {
      x: Number(saved.x),
      y: Number(saved.y),
      width: Number(saved.width),
      height: Number(saved.height),
    };
    return Object.keys(frame).every(function (field) { return Number.isFinite(frame[field]); }) ? frame : null;
  } catch (e) {
    return null;
  }
}

function wbcSaveBrowserWindowFrame(sessionId, frame) {
  var key = String(sessionId || "").trim();
  if (!key || !frame) return;
  try {
    localStorage.setItem(WBC_BROWSER_FRAME_STORAGE_PREFIX + key, JSON.stringify({
      x: Math.round(Number(frame.x) || 0),
      y: Math.round(Number(frame.y) || 0),
      width: Math.round(Number(frame.width) || 0),
      height: Math.round(Number(frame.height) || 0),
    }));
  } catch (e) {}
}

// Pick a readable lane beside the floating browser.  Keeping this calculation
// pure makes the product rule explicit: avoid only when the PiP is clearly off
// centre and one side remains wide enough to read.  Insets are relative to the
// transcript content box, not the Electron window.
function wbcBrowserAvoidancePlan(areaLeft, areaWidth, browserLeft, browserWidth, gap) {
  var left = Number(areaLeft) || 0;
  var width = Math.max(0, Number(areaWidth) || 0);
  var right = left + width;
  var browserStart = Number(browserLeft) || 0;
  var browserSize = Math.max(0, Number(browserWidth) || 0);
  var browserEnd = browserStart + browserSize;
  var gutter = Math.max(0, Number(gap) || 0);
  if (width <= 0 || browserSize <= 0 || browserEnd <= left || browserStart >= right) return null;

  var leftLane = Math.max(0, browserStart - gutter - left);
  var rightLane = Math.max(0, right - browserEnd - gutter);
  var readable = Math.min(360, width * 0.45);
  var centreDeadZone = Math.min(80, width * 0.12);
  if (Math.max(leftLane, rightLane) < readable) return null;
  if (Math.abs(leftLane - rightLane) < centreDeadZone) return null;
  if (leftLane > rightLane) {
    return { side: "left", start: 0, end: Math.max(0, right - browserStart + gutter) };
  }
  return { side: "right", start: Math.max(0, browserEnd - left + gutter), end: 0 };
}

function wbcNotifyBrowserLayoutChanged() {
  window.dispatchEvent(new CustomEvent("workbench:browser-layout"));
}

function wbcNotifyBrowserWindowInteraction(active, kind, sessionId, extra) {
  window.dispatchEvent(new CustomEvent("workbench:browser-window-interaction", {
    detail: Object.assign(
      { active: active === true, kind: kind || "", sessionId: String(sessionId || "") },
      extra && typeof extra === "object" ? extra : {}
    ),
  }));
}

function wbcRectsOverlap(a, b) {
  return !!(
    a && b
    && a.left < b.right
    && a.right > b.left
    && a.top < b.bottom
    && a.bottom > b.top
  );
}

function wbcPageContextMenuPlacement(clientX, clientY, avoidRect) {
  var margin = 8;
  var gap = 8;
  var width = 220;
  var height = 166;
  var viewportWidth = Math.max(width + (margin * 2), Number(window.innerWidth) || 0);
  var viewportHeight = Math.max(height + (margin * 2), Number(window.innerHeight) || 0);
  function clamp(left, top) {
    var x = Math.max(margin, Math.min(Number(left) || 0, viewportWidth - width - margin));
    var y = Math.max(margin, Math.min(Number(top) || 0, viewportHeight - height - margin));
    return {
      left: x,
      top: y,
      right: x + width,
      bottom: y + height,
    };
  }
  var base = clamp(clientX, clientY);
  if (!avoidRect || !wbcRectsOverlap(base, avoidRect)) {
    return { left: base.left, top: base.top, overlapsBrowser: false };
  }
  var candidates = [
    clamp(avoidRect.left - width - gap, clientY),
    clamp(avoidRect.right + gap, clientY),
    clamp(clientX, avoidRect.top - height - gap),
    clamp(clientX, avoidRect.bottom + gap),
  ].filter(function (candidate) { return !wbcRectsOverlap(candidate, avoidRect); });
  if (candidates.length) {
    candidates.sort(function (a, b) {
      var adx = a.left - base.left;
      var ady = a.top - base.top;
      var bdx = b.left - base.left;
      var bdy = b.top - base.top;
      return ((adx * adx) + (ady * ady)) - ((bdx * bdx) + (bdy * bdy));
    });
    return { left: candidates[0].left, top: candidates[0].top, overlapsBrowser: false };
  }
  return { left: base.left, top: base.top, overlapsBrowser: true };
}

function wbcCanOpenPageContextMenu(event) {
  var target = event && event.target;
  if (!target || !target.closest) return false;
  var selection = window.getSelection && window.getSelection();
  if (selection && !selection.isCollapsed && String(selection).trim()) return false;
  return !target.closest([
    "button",
    "a",
    "input",
    "textarea",
    "select",
    "label",
    "[contenteditable='true']",
    "[role='button']",
    "[role='link']",
    "[role='menu']",
    "[role='dialog']",
    ".wbc-composer",
    ".wbc-header",
    ".wbc-browser-window",
    ".wbc-selection-menu",
    ".wbc-conversation-nav",
    ".wbc-chat-card",
    ".workbench-right-tabs",
    ".workbench-confirm-modal",
  ].join(","));
}

function wbcPointInsideResourceShelf(clientX, clientY) {
  var shelf = document.querySelector(".workbench-resource-shelf");
  if (!shelf) return false;
  var rect = shelf.getBoundingClientRect();
  var x = Number(clientX);
  var y = Number(clientY);
  return Number.isFinite(x) && Number.isFinite(y)
    && x >= rect.left && x <= rect.right
    && y >= rect.top && y <= rect.bottom;
}

function wbcConversationTabAtPoint(clientX, clientY, ownerSessionId) {
  var x = Number(clientX);
  var y = Number(clientY);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  var owner = String(ownerSessionId || "");
  var tabs = document.querySelectorAll('.workbench-session-tab[data-session-kind="chat"]');
  for (var index = 0; index < tabs.length; index += 1) {
    var tab = tabs[index];
    var targetId = String(tab.getAttribute("data-session-id") || "");
    if (!targetId || targetId === owner) continue;
    var rect = tab.getBoundingClientRect();
    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
      return { node: tab, chatId: targetId };
    }
  }
  return null;
}

function wbcNotifyResourceShelfPointerDrag(active) {
  window.dispatchEvent(new CustomEvent("cyrene:resource-shelf-drag-state", {
    detail: { active: active === true },
  }));
}

// Shared budget error code → i18n key suffix mapping.  Defined here and
// re-used by the task controller through the registered chat service
// so adding a new budget code only needs one update.
var WORKBENCH_BUDGET_CODES = {
  budget_monthly_exhausted: "monthly",
  budget_weekly_exhausted: "weekly",
  budget_5h_exhausted: "5h",
};

var WORKBENCH_ERROR_I18N_KEYS = {
  quota_exhausted: "workbenchChat.error.quotaExhausted",
  authentication_expired: "workbenchChat.error.authenticationExpired",
  model_unavailable: "workbenchChat.error.modelUnavailable",
  process_restarted: "workbenchChat.error.processRestarted",
  chat_run_driver_failed: "workbenchChat.error.driverFailed",
  chat_not_found: "workbenchChat.error.chatNotFound",
  chat_run_not_found: "workbenchChat.error.chatRunNotFound",
  chat_not_running: "workbenchChat.error.chatNotRunning",
  chat_run_in_progress: "workbenchChat.error.chatRunInProgress",
  guidance_persistence_failed: "workbenchChat.error.guidancePersistenceFailed",
  answer_resume_failed: "workbenchChat.error.answerResumeFailed",
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
    var api = window.CyreneUI.require("api");
    if (api && typeof api.errorText === "function") return api.errorText(err);
  } catch (e) {}
  return raw;
}

var WBC_ICONS = {
  plus: <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>,
  search: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/></svg>,
  alert: <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M10.3 4 2.5 18a1.5 1.5 0 0 0 1.3 2.3h16.4a1.5 1.5 0 0 0 1.3-2.3L13.7 4a1.5 1.5 0 0 0-3.4 0Z"/><path d="M12 9v4.5M12 17h.01"/></svg>,
  edit: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>,
  pin: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 17v5"/><path d="M5 17h14"/><path d="M17 3a1 1 0 0 1 1 1v4.6a2 2 0 0 0 .6 1.4l1.7 1.7A1 1 0 0 1 19.6 13H4.4a1 1 0 0 1-.7-1.7l1.7-1.7A2 2 0 0 0 6 8.2V4a1 1 0 0 1 1-1Z"/></svg>,
  dots: <svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><circle cx="5.5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="18.5" cy="12" r="1.6"/></svg>,
  play: <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M7 4.8c0-1 1.1-1.6 2-1.1l11 6.3c.9.5.9 1.8 0 2.3L9 18.6c-.9.5-2-.1-2-1.1Z"/></svg>,
  send: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4Z"/></svg>,
  stop: <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2.5"/></svg>,
  attach: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m21.44 11.05-9.19 9.19a5 5 0 0 1-7.07-7.07l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>,
  slash: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="m7.5 9.5 2.5 2.5-2.5 2.5"/><path d="M12.5 15h4"/></svg>,
  model: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="6" y="6" width="12" height="12" rx="3"/><circle cx="12" cy="12" r="2.5"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>,
  bolt: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/></svg>,
  copy: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>,
  retry: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 1 0 2.6-6.3"/><path d="M3 4v4h4"/></svg>,
  check: <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>,
  x: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 6 12 12M18 6 6 18"/></svg>,
  tool: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14.7 6.3a4.5 4.5 0 0 0-6 6L3 18l3 3 5.7-5.7a4.5 4.5 0 0 0 6-6L14 13l-3-3Z"/></svg>,
  chat: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 11.5a8.5 8.5 0 0 1-12.2 7.6L3 21l1.9-5.8A8.5 8.5 0 1 1 21 11.5Z"/></svg>,
  file: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5"/></svg>,
  trash: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>,
  task: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1.5"/><path d="M9 14 10.5 15.5 15 11"/></svg>,
  compact: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m8 3 4 4 4-4M12 7V1M8 21l4-4 4 4M12 17v6"/><path d="M4 10h16v4H4z"/></svg>,
  spark: <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M12 2.5 13.7 9 20 10.7 13.7 12.4 12 19l-1.7-6.6L4 10.7 10.3 9Z"/></svg>,
  folder: <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg>,
  device: <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="14" height="11" rx="2"/><path d="M7 20h8M11 15v5M19 9h2M20 8v2"/></svg>,
  fork: <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/><path d="M6 8.2v7.6M8.2 6h7.6M8.2 18H15a3 3 0 0 0 3-3V8.2"/></svg>,
  chevronRight: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6"/></svg>,
  chevronDown: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6"/></svg>,
  chevronLeft: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>,
  chevronsRight: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m13 7 5 5-5 5M6 7l5 5-5 5"/></svg>,
  openExternal: <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M7 17 17 7M8 7h9v9"/></svg>,
  download: <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v12M8 11l4 4 4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>,
  sidebar: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/><path d="m9 10-2 2 2 2"/></svg>,
  windowMaximize: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M21 16v5h-5"/></svg>,
  windowMinimize: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M5 12h14"/></svg>,
  windowRestore: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="7" y="7" width="13" height="13" rx="2"/><path d="M4 16V6a2 2 0 0 1 2-2h10"/></svg>,
};

// Conversation-panel icons share one 18px optical grid and stroke language.
// They are intentionally panel-specific instead of borrowing generic toolbar
// glyphs whose proportions and metaphors differ from the selected design.
var WBC_SIDE_TAB_ICONS = {
  overview: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h11A2.5 2.5 0 0 1 20 7.5v10a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5Z"/><path d="M8 5V3.8M16 5V3.8M8 10.5h8M12 8.5v4"/></svg>,
  plan: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5" y="4.5" width="14" height="16" rx="2.5"/><path d="M9 4.5V3h6v1.5M8.5 10.5l1.4 1.4 2.6-2.8M14.5 11h2M8.5 16h8"/></svg>,
  subagents: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 9.5A4.5 4.5 0 0 1 11.5 5H14a4 4 0 0 1 4 4v.5a4.5 4.5 0 0 1 2 3.7v2.3a3.5 3.5 0 0 1-3.5 3.5h-9A3.5 3.5 0 0 1 4 15.5v-2.3a4.5 4.5 0 0 1 3-4.2Z"/><path d="M9 13h.01M15 13h.01M9.5 16h5M12 5V2.8M10.5 2.8h3"/></svg>,
  context: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="4" width="6" height="6" rx="2"/><rect x="14" y="4" width="6" height="6" rx="2"/><rect x="4" y="14" width="6" height="6" rx="2"/><rect x="14" y="14" width="6" height="6" rx="2"/><path d="M10 7h4M7 10v4M17 10v4M10 17h4"/></svg>,
  artifacts: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 4.5h10A2.5 2.5 0 0 1 19.5 7v11A2.5 2.5 0 0 1 17 20.5H7A2.5 2.5 0 0 1 4.5 18V7A2.5 2.5 0 0 1 7 4.5Z"/><path d="M9 4.5V3h6v1.5M8 10h8M8 14h5M8 17h7"/></svg>,
  changes: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="7" cy="5" r="2"/><circle cx="7" cy="19" r="2"/><circle cx="17" cy="8" r="2"/><path d="M7 7v10M9 17c5 0 8-2.5 8-7M14.5 8H10"/></svg>,
  branches: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M8 17h4a6 6 0 0 0 6-6V10"/></svg>,
  viewer: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="m6 16 3.5-3.5 2.7 2.7 2.3-2.3L18 16M8 9h.01"/><path d="M3.5 8.5h17"/></svg>,
  map: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m4 6.5 5-2.2 6 2.2 5-2.2v13.2l-5 2.2-6-2.2-5 2.2Z"/><path d="M9 4.3v13.2M15 6.5v13.2"/><circle cx="12" cy="11" r="1.5"/></svg>,
  browser: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4.5" width="18" height="15" rx="3"/><path d="M3 8.5h18M7 6.5h.01M10 6.5h.01"/><circle cx="12" cy="14" r="3.3"/><path d="M8.7 14h6.6M12 10.7c1.1 1.2 1.1 5.4 0 6.6M12 10.7c-1.1 1.2-1.1 5.4 0 6.6"/></svg>,
  "side-agents": <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 4.5h10A2.5 2.5 0 0 1 19.5 7v10A2.5 2.5 0 0 1 17 19.5h-4L9 22v-2.5H7A2.5 2.5 0 0 1 4.5 17V7A2.5 2.5 0 0 1 7 4.5Z"/><path d="M9 9h6M9 13h4"/></svg>,
};

// Slash commands + permission modes (mirrors the legacy agent capabilities;
// defined locally so this page stays independent from workbench.jsx).
var WBC_COMMANDS = [
  { id: "quick-answer", labelKey: "workbenchChat.command.quick-answer.label", descKey: "workbenchChat.command.quick-answer.desc" },
  { id: "deep-research", labelKey: "workbenchChat.command.deep-research.label", descKey: "workbenchChat.command.deep-research.desc" },
  { id: "deep-reflect", labelKey: "workbenchChat.command.deep-reflect.label", descKey: "workbenchChat.command.deep-reflect.desc" },
  { id: "help-me-decide", labelKey: "workbenchChat.command.help-me-decide.label", descKey: "workbenchChat.command.help-me-decide.desc" },
  { id: "learning-plan", labelKey: "workbenchChat.command.learning-plan.label", descKey: "workbenchChat.command.learning-plan.desc" },
  { id: "daily-review", labelKey: "workbenchChat.command.daily-review.label", descKey: "workbenchChat.command.daily-review.desc" },
  { id: "deep-compare", labelKey: "workbenchChat.command.deep-compare.label", descKey: "workbenchChat.command.deep-compare.desc" },
  { id: "claude-code", labelKey: "workbenchChat.command.claude-code.label", descKey: "workbenchChat.command.claude-code.desc" },
];

var WBC_MODES = [
  { id: "default", labelKey: "workbenchChat.mode.default.label", descKey: "workbenchChat.mode.default.desc" },
  { id: "auto", labelKey: "workbenchChat.mode.auto.label", descKey: "workbenchChat.mode.auto.desc" },
  { id: "plan", labelKey: "workbenchChat.mode.plan.label", descKey: "workbenchChat.mode.plan.desc" },
  { id: "full_access", labelKey: "workbenchChat.mode.full_access.label", descKey: "workbenchChat.mode.full_access.desc" },
];
var WBC_REASONING_EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"];

function wbcIsDeepSeekModel(model) {
  return String(model && (model.model || model.name || model.id) || "")
    .trim().toLowerCase().indexOf("deepseek") >= 0;
}

function wbcSupportedReasoningEfforts(model) {
  var raw = model && (
    model.supportedReasoningEfforts
    || model.supported_reasoning_efforts
  );
  var efforts = (Array.isArray(raw) ? raw : []).map(function (option) {
    return String(
      option && (option.reasoningEffort || option.reasoning_effort)
      || option
      || ""
    ).trim().toLowerCase();
  }).filter(function (effort) {
    return WBC_REASONING_EFFORT_ORDER.indexOf(effort) >= 0;
  });
  if (!efforts.length && wbcIsDeepSeekModel(model)) efforts = ["high", "max"];
  return Array.from(new Set(efforts)).sort(function (a, b) {
    return WBC_REASONING_EFFORT_ORDER.indexOf(a) - WBC_REASONING_EFFORT_ORDER.indexOf(b);
  });
}

function wbcReasoningEffortForModel(model, preferred) {
  var effort = String(
    preferred
    || model && (model.reasoning_effort || model.defaultReasoningEffort || model.default_reasoning_effort)
    || ""
  ).trim().toLowerCase();
  if (wbcIsDeepSeekModel(model)) {
    if (["low", "medium", "high"].indexOf(effort) >= 0) effort = "high";
    else if (["xhigh", "max"].indexOf(effort) >= 0) effort = "max";
    else effort = "high";
  }
  var supported = wbcSupportedReasoningEfforts(model);
  if (supported.length && supported.indexOf(effort) < 0) {
    effort = String(
      model && (model.defaultReasoningEffort || model.default_reasoning_effort)
      || supported[0]
      || ""
    ).trim().toLowerCase();
  }
  return effort;
}

function wbcFriendlyModelName(model, fallback) {
  var configuredName = String(model && model.name || "").trim();
  var modelId = String(model && model.model || fallback || "").trim();
  if (configuredName && configuredName !== modelId) return configuredName;
  if (!modelId) return configuredName;
  var words = modelId.replace(/^gpt-/i, "").split(/[-_]+/).filter(Boolean);
  return words.map(function (word) {
    if (/^\d/.test(word)) return word.toUpperCase();
    if (word.toLowerCase() === "deepseek") return "DeepSeek";
    return word.charAt(0).toUpperCase() + word.slice(1);
  }).join(" ");
}

function wbcNormalizePermissionMode(value, fallback) {
  var normalized = String(value || "").trim().toLowerCase();
  if (WBC_MODES.some(function (item) { return item.id === normalized; })) {
    return normalized;
  }
  var safeFallback = String(fallback || "default").trim().toLowerCase();
  return WBC_MODES.some(function (item) { return item.id === safeFallback; })
    ? safeFallback
    : "default";
}

function wbcModeMeta(id) {
  var meta = WBC_MODES[1];
  for (var i = 0; i < WBC_MODES.length; i++) if (WBC_MODES[i].id === id) meta = WBC_MODES[i];
  return {
    id: meta.id,
    label: wbcT(meta.labelKey, meta.id),
    desc: wbcT(meta.descKey, ""),
  };
}

// ---- file classification for the side viewer -------------------------------

var WBC_CODE_EXTS = ["py","js","ts","jsx","tsx","css","json","yaml","yml","toml","xml","sql","sh","bash","rs","go","java","c","cpp","h","rb","php","swift","kt","txt","csv","ini","cfg","env","log"];

function wbcFileViewKind(file) {
  if (!file) return "";
  var ct = String(file.content_type || "").split(";", 1)[0].trim().toLowerCase();
  var ext = String(file.name || "").split(".").pop().toLowerCase();
  if (ct.indexOf("image/") === 0 || file.kind === "image") return "image";
  if (ct === "application/pdf" || ext === "pdf" || file.kind === "pdf") return "pdf";
  if (ct === "text/html" || ct === "application/xhtml+xml" || ext === "html" || ext === "htm") return "html";
  if (file.kind === "markdown" || ext === "md" || ext === "markdown") return "markdown";
  if (file.kind === "code" || WBC_CODE_EXTS.indexOf(ext) !== -1 || ct.indexOf("text/") === 0) return "code";
  return "download";
}

function wbcAttachmentVisualKind(file) {
  var shared = window.CyreneUI.require("library").FileVisual;
  if (shared && typeof shared.visualKind === "function") return shared.visualKind(file);
  return wbcFileViewKind(file) === "image" ? "image" : (wbcFileViewKind(file) || "file");
}

function wbcAttachmentVisual(file) {
  var shared = window.CyreneUI.require("library").FileVisual;
  if (shared && typeof shared.icon === "function") {
    return {
      kind: wbcAttachmentVisualKind(file),
      tone: typeof shared.tone === "function" ? shared.tone(file) : "slate",
      icon: shared.icon(file),
    };
  }
  return { kind: wbcAttachmentVisualKind(file), tone: "slate", icon: WBC_ICONS.file };
}

function wbcAttachmentTypeLabel(file) {
  var kind = wbcAttachmentVisualKind(file);
  var fallbacks = {
    image: "Image",
    pdf: "PDF document",
    doc: "Word document",
    sheet: "Spreadsheet",
    slide: "Presentation",
    markdown: "Markdown",
    link: "Link",
    code: "Code file",
    map: "Map data",
    note: "Text file",
    file: "File",
  };
  return wbcT("workbenchChat.attachmentType." + kind, fallbacks[kind] || fallbacks.file);
}

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
  var deferredSends = {};       // chatId -> terminal-race guidance promoted to the next normal turn
  var subscribers = new Set();
  var hooks = null;             // live transcript hooks from the mounted page

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
    // Defer only the high-frequency reply-delta path; everything else (including
    // the delta's terminal siblings reply_done / saved) emits now and cancels any
    // pending coalesced emit so the latest state renders without delay.
    if (defer) scheduleEmit();
    else { cancelScheduledEmit(); emit(); }
    return next;
  }

  function clear(chatId) { update(chatId, null); }

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
    return Promise.resolve(request).catch(function (err) {
      fire("onError", chatId, err);
      return null;
    }).finally(function () {
      abort(chatId);
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
    return true;
  }

  // The mounted page registers transcript hooks so a streaming run patches its
  // local transcript / chat list. All are optional and guarded by chatId; when
  // no page is mounted they are simply absent and the page re-pulls on remount.
  function setHooks(next) { hooks = next || null; }
  function fire(name, a, b) {
    if (hooks && typeof hooks[name] === "function") {
      try { hooks[name](a, b); } catch (e) {}
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

    // The current LLM call's reasoning was provisionally merged into the last
    // activity while it streamed. Once its visible tool preamble arrives, the
    // true boundary is known: keep prior tools/reasoning above the prose and
    // move only this call's reasoning into a fresh activity below the prose.
    var reasoning = String(last.reasoning || "");
    var callStart = Math.max(0, Math.min(Number(last.reasoningCallStart || 0), reasoning.length));
    var priorReasoning = reasoning.slice(0, callStart).replace(/\s+$/, "");
    var currentReasoning = reasoning.slice(callStart).replace(/^\s+/, "");
    var priorProgress = Array.isArray(last.progress) ? last.progress : [];
    activities.pop();
    if (priorProgress.length || priorReasoning.trim()) {
      activities.push({
        ...last,
        reasoning: priorReasoning,
        reasoningActive: false,
        timelineClosed: true,
      });
    }

    var nextSeq = Number(cur.activitySeq || 0) + 1;
    var messageAt = Date.parse(String(message && message.createdAt || ""));
    activities.push({
      ...last,
      id: "activity_" + nextSeq,
      reasoning: currentReasoning,
      reasoningCallStart: 0,
      reasoningActive: false,
      progress: [],
      createdAt: Math.max(Date.now(), Number.isFinite(messageAt) ? messageAt + 1 : 0),
      timelineClosed: false,
    });
    return { ...cur, activitySeq: nextSeq, activities: activities };
  }

  function appendIntermediate(chatId, message) {
    if (!chatId || !message || !message.id) return;
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

  function streamHandlers(chatId) {
    return {
      onAck: function (event) {
        if (event.retry) return;
        if (event.userMessage) {
          var runtime = get(chatId);
          update(chatId, function (current) {
            return current ? {
              ...current,
              confirmedUserMessageId: String(event.userMessage.id || ""),
            } : null;
          });
          fire("onUserMessageConfirmed", chatId, {
            optimisticId: String(runtime && runtime.optimisticUserMessageId || ""),
            userMessage: event.userMessage,
          });
        }
      },
      onReplyStart: function () {
        update(chatId, function (cur) { return cur ? { ...cur, replying: true, lastEventAt: Date.now() } : null; });
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
        update(chatId, function (cur) { return cur ? { ...cur, replying: true, text: cur.text + delta, lastEventAt: Date.now() } : null; }, true);
      },
      onReplyDone: function (text) {
        update(chatId, function (cur) { return cur ? { ...cur, text: text || cur.text, lastEventAt: Date.now() } : null; });
      },
      onFinalizing: function () {
        update(chatId, function (cur) {
          return cur ? { ...wbcFinalizeRuntime(cur), lastEventAt: Date.now() } : null;
        });
      },
      onIntermediateMessage: function (event) {
        appendIntermediate(chatId, event && event.message);
      },
      onGuidanceReceived: function (event) {
        if (event && event.userMessage) fire("onUserMessage", chatId, event.userMessage);
        update(chatId, function (cur) {
          if (!cur) return null;
          return { ...closeActivityTimeline(cur), lastEventAt: Date.now() };
        });
      },
      onSaved: function (event) {
        if (event.retry) {
          // Commit the transcript replacement only after the regenerated reply
          // is durable. A failed retry therefore leaves the old reply visible.
          fire("onRetryTruncate", chatId, {
            afterId: String(event.truncateAfterMessageId || ""),
            replacedIds: Array.isArray(event.retryReplacedMessageIds) ? event.retryReplacedMessageIds : [],
          });
        }
        var savedMessages = Array.isArray(event.assistantMessages) && event.assistantMessages.length
          ? event.assistantMessages
          : (event.assistantMessage ? [event.assistantMessage] : []);
        if (savedMessages.length) fire("onAssistantSaved", chatId, savedMessages);
        update(chatId, null);
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
        fire("onAwaitingUser", chatId, event.pending_question || null);
        update(chatId, null);
        fire("onSettled", chatId);
      },
      onInterrupted: function () {
        update(chatId, null);
        fire("onInterrupted", chatId);
      },
      onError: function (err) {
        // Keep the runtime until the stream closes so `finally` performs the
        // same server re-sync used for interrupts and transport failures.
        fire("onError", chatId, err);
      },
    };
  }

  function ownStream(chatId, streamPromise, ac) {
    return streamPromise.catch(function (err) {
      if (err && err.name === "AbortError") return;
      if (err && err.code === "chat_run_in_progress") {
        fire("onResync", chatId);
        return;
      }
      if (!(err && err.code === "chat_run_not_found")) fire("onError", chatId, err);
    }).finally(function () {
      if (aborts[chatId] === ac) delete aborts[chatId];
      if (runtimes[chatId]) {
        // Stream ended without a `saved` / `awaiting` event (interrupted or a
        // transport failure) — drop the runtime and let the page re-pull.
        update(chatId, null);
        fire("onResync", chatId);
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
      startedAt: startedAt,
      lastEventAt: startedAt,
      replying: false,
      optimisticUserMessageId: optimisticUserMessage ? optimisticUserMessage.id : "",
      clientRequestId: clientRequestId,
    });
    return ownStream(
      chatId,
      model.sendMessage(chatId, input, streamHandlers(chatId), ac ? ac.signal : undefined),
      ac
    );
  }

  function reconnect(chatId, model) {
    if (!chatId || runtimes[chatId] || !model || !model.reconnectRun) return null;
    var ac = (typeof AbortController !== "undefined") ? new AbortController() : null;
    if (ac) aborts[chatId] = ac;
    update(chatId, { chatId: chatId, text: "", progress: [], activities: [], activitySeq: 0, segments: [], startedAt: Date.now(), lastEventAt: Date.now(), replying: false, reconnecting: true });
    return ownStream(
      chatId,
      model.reconnectRun(chatId, streamHandlers(chatId), ac ? ac.signal : undefined),
      ac
    );
  }

  // Persistent SSE subscription: fold live tool / phase / subagent progress into
  // the running conversation's runtime regardless of whether its page is
  // mounted. Mirrors the legacy per-component handler but never tears down.
  function onSseEvent(event) {
    if (!event) return;
    var chatId = String(event.session_id || "");
    if (!chatId || !runtimes[chatId]) return;
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
    var terminalToolEvent = false;
    if (event.type === "tool_call_started" || event.type === "tool_call" || event.type === "tool_call_finished" || event.type === "tool_call_progress") {
      var toolName = String(event.tool || "");
      if (["use_tools", "quit", "send_message", "update_plan_progress"].indexOf(toolName) >= 0) return;
      var args = event.args || {};
      var preview = wbcToolArgsPreview(args);
      var toolStarted = event.type === "tool_call_started";
      var toolProgress = event.type === "tool_call_progress";
      terminalToolEvent = event.type === "tool_call_finished";
      var toolResult = String(event.result || "");
      var toolFailed = !!event.failed
        || String(event.status || "").toLowerCase() === "failed"
        || (!toolStarted && toolResult.toLowerCase().startsWith("tool failed:"));
      entry = {
        kind: "tool",
        toolCallId: String(event.tool_call_id || ""),
        text: toolName || undefined,
        preview: toolProgress ? String(event.label || "") : preview,
        status: (toolStarted || toolProgress) ? "running" : "completed",
        failed: toolFailed,
        progress: toolProgress ? Math.max(0, Math.min(1, Number(event.progress) || 0)) : undefined,
        progressCurrent: toolProgress ? Math.max(0, Number(event.current) || 0) : undefined,
        progressTotal: toolProgress ? Math.max(0, Number(event.total) || 0) : undefined,
      };
    } else if (event.type === "phase_transition" && (event.detail || event.detail_key)) {
      var phaseText = event.detail_key
        ? wbcT(event.detail_key, String(event.detail || ""), event.detail_params || {})
        : String(event.detail || "");
      if (event.alert && window.CyreneUI.require("feedback").showToast) {
        window.CyreneUI.require("feedback").showToast(
          phaseText,
          String(event.alert_level || "warning")
        );
      }
      entry = {
        kind: "phase",
        text: event.detail ? String(event.detail).slice(0, 160) : "",
        detailKey: event.detail_key || "",
        detailParams: event.detail_params || {},
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
          return (Array.isArray(items) ? items : []).map(function (item) {
            if (String(item && item.toolCallId || "") !== entry.toolCallId) return item;
            matchedToolCall = true;
            return wbcMergeToolLifecycleEntry(item, entry, terminalToolEvent);
          });
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
      var activityBase = latestActivity && latestActivity.timelineClosed
        ? appendActivity(latest, {})
        : latest;
      var next = updateLastActivity(activityBase, function (activity) {
        var activityProgress = Array.isArray(activity.progress) ? activity.progress : [];
        return { ...activity, progress: activityProgress.concat([entry]).slice(-30) };
      });
      return {
        ...next,
        lastEventAt: Date.now(),
        progress: latest.progress.concat([entry]).slice(-30),
      };
    });
  }
  window.CyreneUI.require("events").subscribe(onSseEvent);

  return {
    subscribe: subscribe, snapshot: snapshot, get: get, isRunning: isRunning,
    update: update, clear: clear, abort: abort, interrupt: interrupt,
    start: start, reconnect: reconnect, deferSend: deferSend, closeTimeline: closeTimeline, setHooks: setHooks,
  };
})();

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function wbcResolveRefreshedChatSelection(list, selectId, selectionAtRequest, liveSelectionId) {
  var chats = Array.isArray(list) ? list : [];
  var requestedId = String(selectId || "");
  var liveId = String(liveSelectionId || "");
  if (requestedId) {
    return chats.some(function (chat) { return String(chat.id || "") === requestedId; })
      ? requestedId
      : (chats[0] ? String(chats[0].id || "") : "");
  }
  // A list request can finish after the user selects or creates another chat.
  // In that case its snapshot may predate the new chat, so it must not restore
  // the selection that was current when the request started.
  if (liveId !== String(selectionAtRequest || "")) return null;
  if (liveId && chats.some(function (chat) { return String(chat.id || "") === liveId; })) {
    return null;
  }
  return chats[0] ? String(chats[0].id || "") : "";
}

function WorkbenchChatPage({ active, project, newChatRequestId, onOpenTask, onActiveChatChange, onActiveChatIdChange, onChatsChange, pinnedChatIds, onTogglePinnedChat }) {
  window.CyreneUI.require("i18n").use();
  window.CyreneUI.require("data").useVersion();
  var isActive = active !== false;
  var model = WorkbenchChatModel;
  var projectId = project ? project.id : "";
  var chatCache = wbcChatCache();
  var [chats, setChats] = useWbcState([]);
  var chatsRef = useWbcRef([]);
  var chatsProjectIdRef = useWbcRef("");
  var [activeChatId, setActiveChatId] = useWbcState("");
  var activeChatIdRef = useWbcRef("");
  function selectChat(chatId) {
    var nextId = String(chatId || "");
    // Publish selection intent immediately. Passive effects run too late to
    // protect a newly-created chat from an already in-flight list refresh.
    activeChatIdRef.current = nextId;
    setActiveChatId(nextId);
  }
  var [activeChat, setActiveChat] = useWbcState(null);
  var [loading, setLoading] = useWbcState(true);
  var [chatLoading, setChatLoading] = useWbcState(false);
  var [loadRevision, setLoadRevision] = useWbcState(0);
  var projectIdRef = useWbcRef(projectId);
  // This page stays mounted while the user switches projects. Event and
  // navigation listeners are intentionally registered once, so publish the
  // latest project synchronously on every render instead of leaving those
  // long-lived callbacks with the project captured on their first render.
  projectIdRef.current = projectId;
  // POST /chats and /fork already return the complete conversation. Mark the
  // adopted id so the selection effect does not clear it and fetch it again.
  var skipNextHydrationChatIdRef = useWbcRef("");
  var handledNewChatRequestIdRef = useWbcRef(0);

  useWbcEffect(function () {
    chatsRef.current = chats;
    if (
      projectId
      && chatsProjectIdRef.current === projectId
      && Array.isArray(chats)
      && chats.every(function (chat) { return String((chat && chat.projectId) || "") === String(projectId); })
    ) {
      chatCache.lists[projectId] = chats;
    }
    if (onChatsChange && projectId) onChatsChange(projectId, chats);
  }, [chats]);
  useWbcEffect(function () {
    if (
      activeChat
      && activeChat.id
      && String(activeChat.projectId || "") === String(projectId)
    ) {
      chatCache.details[activeChat.id] = activeChat;
    }
  }, [activeChat]);
  useWbcEffect(function () {
    activeChatIdRef.current = activeChatId;
    // Remember the open conversation per project so switching to another module
    // and back restores it (rather than snapping to the most-recent chat) — key
    // for not "losing" a conversation whose run is streaming in the background.
    if (activeChatId) {
      wbcLastChatByProject[projectId] = activeChatId;
    }
  }, [activeChatId]);
  var [error, setError] = useWbcState("");
  var [errorKind, setErrorKind] = useWbcState("load");
  // Which side of the conversation the detail split anchors to. Global across
  // chats (like the split width) so the choice survives conversation switches.
  var [splitSide, setSplitSide] = useWbcState(function () {
    try {
      return localStorage.getItem("wbc-split-side") === "left" ? "left" : "right";
    } catch (e) {
      return "right";
    }
  });
  function toggleSplitSide() {
    setSplitSide(function (current) {
      var next = current === "left" ? "right" : "left";
      try { localStorage.setItem("wbc-split-side", next); } catch (e) {}
      return next;
    });
  }
  // Idempotent setter used by the grip drag so moving the pointer across the
  // window midline follows the split live without toggling on every move.
  function setSplitSideDirect(next) {
    setSplitSide(function (current) {
      if (current === next) return current;
      try { localStorage.setItem("wbc-split-side", next); } catch (e) {}
      return next;
    });
  }
  // The split panel is lifted with a native drag, same as images/documents:
  // the panel stays in place, a drag ghost follows the pointer (shrinking to
  // a chat card over the rail), and the drop zones (rail = close, main
  // left/right half = anchored side) light up under the cursor. The ghost and
  // zones are created as raw DOM during the drag session — never through
  // React — so the drag source's DOM stays untouched while Chromium is
  // tracking the gesture (any React re-render here cancels the drag).
  var splitOverlayCleanupRef = useWbcRef(null);

  function handleSplitDragStart(event) {
    var transfer = event && event.dataTransfer;
    if (!transfer) return;
    wbcSetSplitDrag(event);
    // Hide the native ghost with a transparent 1x1 image; the custom overlay
    // (panel-shaped, shrinking to a chat card over the rail) takes over.
    try {
      var canvas = document.createElement("canvas");
      canvas.width = 1;
      canvas.height = 1;
      transfer.setDragImage(canvas, 0, 0);
    } catch (e) {}
    var page = pageRef.current;
    if (!page) return;
    if (splitOverlayCleanupRef.current) splitOverlayCleanupRef.current();
    // The ghost is the real dialog being lifted: a clone of the live DOM
    // (same transcript, header, composer) in a fixed overlay. The main
    // conversation's grip lifts the main column; the split panel's grip
    // lifts the split panel. It starts at the panel's own position and
    // follows the pointer from the grab point. A clone of the matching rail
    // card rides along; over the conversation rail the ghost switches to
    // that card, as if the conversation itself were being picked up from
    // the list.
    var ghost = document.createElement("div");
    ghost.className = "wbc-split-drag-ghost";
    var grabOffset = null;
    var panelW = 0;
    var panelH = 0;
    var cardW = 0;
    var cardH = 0;
    var fromMainGrip = !!(event.currentTarget && event.currentTarget.closest
      && event.currentTarget.closest(".wbc-split-main-grip"));
    var panel = fromMainGrip
      ? page.querySelector(".wbc-main")
      : page.querySelector(".wbc-side-agent-split");
    if (panel) {
      var panelRect = panel.getBoundingClientRect();
      var clone = panel.cloneNode(true);
      if (!fromMainGrip) {
        // The split panel reserves 84px for the grip strip; the clone sits
        // in its own shell, so drop that blank band.
        clone.style.paddingTop = "0px";
      }
      clone.style.border = "0";
      clone.style.boxShadow = "none";
      ghost.appendChild(clone);
      panelW = Math.round(panelRect.width);
      panelH = Math.max(120, Math.min(
        Math.round(panelRect.height),
        Math.round(window.innerHeight * 0.72)
      ));
      ghost.style.width = panelW + "px";
      ghost.style.height = panelH + "px";
      ghost.style.left = panelRect.left + "px";
      ghost.style.top = panelRect.top + "px";
      grabOffset = {
        x: Math.max(0, Math.min(panelRect.width, event.clientX - panelRect.left)),
        y: Math.max(0, Math.min(panelRect.height, event.clientY - panelRect.top)),
      };
    } else {
      ghost.style.left = event.clientX + "px";
      ghost.style.top = event.clientY + "px";
    }
    var liftChatId = fromMainGrip ? String(activeChatIdRef.current || "") : splitChatId;
    var railCard = liftChatId
      ? page.querySelector('.wbc-chat-card[data-chat-id="' + liftChatId + '"]')
      : null;
    if (!railCard) railCard = page.querySelector(".wbc-chat-card.active");
    if (!railCard) railCard = page.querySelector(".wbc-chat-card");
    if (railCard) {
      var cardRect = railCard.getBoundingClientRect();
      var cardClone = railCard.cloneNode(true);
      cardClone.classList.remove("active", "dragging", "menu-open", "group-drop-target", "wbc-chat-group-child");
      ghost.appendChild(cardClone);
      cardW = Math.round(cardRect.width);
      cardH = Math.round(cardRect.height);
    }
    // Over the rail the matching real card lifts off the list, echoing the
    // ghost: the conversation itself looks picked up from the rail.
    var sourceCardEl = railCard;
    // The theme palette lives on .workbench-shell; the ghost sits outside it,
    // so copy the custom properties or the cloned panel renders unstyleed.
    var shell = document.querySelector(".workbench-shell");
    if (shell) {
      var shellStyle = window.getComputedStyle(shell);
      for (var i = 0; i < shellStyle.length; i++) {
        var name = shellStyle[i];
        if (name.indexOf("--") === 0) {
          ghost.style.setProperty(name, shellStyle.getPropertyValue(name));
        }
      }
    }
    document.body.appendChild(ghost);
    // The zones are pure visual (never hit-testable): an interactive overlay
    // covering the drag source would make Chromium cancel the drag. Zone
    // detection happens on document-level dragover/drop via pointer position.
    var zones = document.createElement("div");
    zones.className = "wbc-split-drop-zones";
    zones.setAttribute("role", "presentation");
    zones.innerHTML = ""
      + '<div class="wbc-split-drop-zone wbc-split-drop-left" data-zone="left">'
      + '<span class="wbc-chat-side-drop-hint" role="status">' + wbcEscapeHtml(wbcT("workbenchChat.splitDropLeft", "Release to move the split to the left side")) + '</span>'
      + '</div>'
      + '<div class="wbc-split-drop-zone wbc-split-drop-right" data-zone="right">'
      + '<span class="wbc-chat-side-drop-hint" role="status">' + wbcEscapeHtml(wbcT("workbenchChat.splitDropRight", "Release to move the split to the right side")) + '</span>'
      + '</div>'
      + '<div class="wbc-split-drop-zone wbc-split-drop-rail" data-zone="rail">'
      + '<span class="wbc-chat-side-drop-hint" role="status">' + wbcEscapeHtml(wbcT("workbenchChat.splitDropClose", "Release to close the split panel")) + '</span>'
      + '</div>';
    page.appendChild(zones);

    var clearTimer = null;
    // Sides are judged against the PAGE midline, not the main column's: the
    // layout moves while the drag preview swaps anchors, so a column-relative
    // test would lock the preview onto the side it already previewed.
    function zoneAt(clientX, clientY) {
      var rail = document.querySelector(".wbc-rail");
      var r = rail ? rail.getBoundingClientRect() : null;
      if (r && clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom) return "rail";
      var page = pageRef.current;
      if (!page) return "";
      var pr = page.getBoundingClientRect();
      if (!pr.width) return "";
      return clientX < pr.left + pr.width / 2 ? "left" : "right";
    }
    function setActive(zone) {
      ghost.classList.toggle("card", zone === "rail");
      if (zone === "rail" && cardW) {
        ghost.style.width = cardW + "px";
        ghost.style.height = cardH + "px";
      } else if (panelW) {
        ghost.style.width = panelW + "px";
        ghost.style.height = panelH + "px";
      }
      if (sourceCardEl) {
        sourceCardEl.classList.toggle("wbc-split-card-lifted", zone === "rail");
      }
      // querySelectorAll: the half zones nest inside .wbc-split-drop-main.
      var zoneEls = zones.querySelectorAll(".wbc-split-drop-zone");
      for (var i = 0; i < zoneEls.length; i++) {
        var el = zoneEls[i];
        el.classList.toggle("active", el.getAttribute("data-zone") === zone);
      }
    }
    function onDocumentDragOver(ev) {
      if (grabOffset) {
        ghost.style.left = (ev.clientX - grabOffset.x) + "px";
        ghost.style.top = (ev.clientY - grabOffset.y) + "px";
      } else {
        ghost.style.left = ev.clientX + "px";
        ghost.style.top = ev.clientY + "px";
      }
      if (clearTimer) { clearTimeout(clearTimer); clearTimer = null; }
      var zone = zoneAt(ev.clientX, ev.clientY);
      if (!zone) {
        setActive("");
        return;
      }
      ev.preventDefault();
      if (ev.dataTransfer) ev.dataTransfer.dropEffect = "move";
      setActive(zone);
      if (zone !== "rail") {
        // Live preview: crossing the midline glides the split to that side
        // immediately (elastic grid animation pushing the main conversation
        // to the other side); releasing anywhere keeps the last position.
        setSplitSideDirect(zone === "left" ? "left" : "right");
      }
    }
    function onDocumentDrop(ev) {
      if (!wbcHasSplitDrag(ev)) return;
      var zone = zoneAt(ev.clientX, ev.clientY);
      if (!zone) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      handleSplitDragEnd();
      if (zone === "rail") {
        closeActiveSplit();
        return;
      }
      setSplitSideDirect(zone === "left" ? "left" : "right");
    }
    function cleanup() {
      if (splitOverlayCleanupRef.current !== cleanup) return;
      splitOverlayCleanupRef.current = null;
      if (clearTimer) clearTimeout(clearTimer);
      document.removeEventListener("dragover", onDocumentDragOver, true);
      document.removeEventListener("drop", onDocumentDrop, true);
      if (sourceCardEl) sourceCardEl.classList.remove("wbc-split-card-lifted");
      if (ghost.parentNode) ghost.parentNode.removeChild(ghost);
      if (zones.parentNode) zones.parentNode.removeChild(zones);
    }
    document.addEventListener("dragover", onDocumentDragOver, true);
    document.addEventListener("drop", onDocumentDrop, true);
    splitOverlayCleanupRef.current = cleanup;
  }

  function handleSplitDragEnd() {
    if (splitOverlayCleanupRef.current) splitOverlayCleanupRef.current();
  }
  var [sideTab, setSideTab] = useWbcState("");
  var [sideVisible, setSideVisible] = useWbcState(true);
  var [sideAgents, setSideAgents] = useWbcState([]);
  var [sideAgentsLoading, setSideAgentsLoading] = useWbcState(false);
  var [sideAgentCreating, setSideAgentCreating] = useWbcState(false);
  var [activeSideAgentByChat, setActiveSideAgentByChat] = useWbcState({});
  // Selecting a side question in the right-hand list opens its conversation
  // beside the main thread. Keep this separate from the remembered list
  // selection so creating/loading an agent never opens the split by itself.
  var [sideAgentSplitByChat, setSideAgentSplitByChat] = useWbcState({});
  // Artifacts use the same resizable detail track as side conversations. Store
  // only the stable file key so refreshed chat payloads can supply fresh URLs.
  var [artifactSplitByChat, setArtifactSplitByChat] = useWbcState({});
  var [changeSplitByChat, setChangeSplitByChat] = useWbcState({});
  // Map, browser, viewer and subagent details share the same right-side split
  // shell. The side panel only exposes their lightweight index/list surface.
  var [resourceSplitByChat, setResourceSplitByChat] = useWbcState({});
  // While a detail split is open, the conversation panel can float beneath
  // the main conversation grip without disturbing that split. If a resource
  // is opened from the floating panel, remember the displaced split so its
  // close action can restore the exact previous layout.
  var [floatingConversationPanelOpen, setFloatingConversationPanelOpen] = useWbcState(false);
  var floatingSplitRestoreRef = useWbcRef(null);
  var [sideAgentSplitWidth, setSideAgentSplitWidth] = useWbcState(function () {
    var initial = 520;
    try {
      var saved = Number(localStorage.getItem("wbc-side-agent-split-width"));
      if (Number.isFinite(saved) && saved >= 300) initial = saved;
    } catch (e) {}
    return wbcClampSideSplitWidth(initial, window.innerWidth);
  });
  var [browserActiveByChat, setBrowserActiveByChat] = useWbcState({});
  // The side-panel Browser tab and the floating browser are two presentations
  // of the same session. Keep only the floating presentation state here so the
  // WebContentsView is never mounted in two places at once.
  var [browserWindowModeByChat, setBrowserWindowModeByChat] = useWbcState({});
  var [viewerFile, setViewerFile] = useWbcState(null);
  var [subagentData, setSubagentData] = useWbcState({ rounds: [], activeRoundId: "", agents: [], messages: [] });
  var [subagentLoading, setSubagentLoading] = useWbcState(false);
  var subagentRefreshTimerRef = useWbcRef(null);

  useWbcEffect(function () {
    function keepSplitWithinViewport() {
      setSideAgentSplitWidth(function (current) {
        var next = wbcClampSideSplitWidth(current, window.innerWidth);
        if (next === current) return current;
        try { localStorage.setItem("wbc-side-agent-split-width", String(next)); } catch (e) {}
        return next;
      });
    }
    keepSplitWithinViewport();
    window.addEventListener("resize", keepSplitWithinViewport);
    return function () { window.removeEventListener("resize", keepSplitWithinViewport); };
  }, [splitSide]);

  useWbcEffect(function () {
    function handleShowChatSide() {
      if (isActive) setSideVisible(true);
    }
    window.addEventListener("workbench:show-chat-side", handleShowChatSide);
    return function () {
      window.removeEventListener("workbench:show-chat-side", handleShowChatSide);
    };
  }, [isActive]);

  useWbcEffect(function () {
    window.dispatchEvent(new CustomEvent("workbench:chat-side-visibility", {
      detail: { active: isActive, hidden: isActive && !sideVisible },
    }));
  }, [isActive, sideVisible]);
  var remoteChatRefreshTimerRef = useWbcRef(null);
  var remoteChangedChatIdsRef = useWbcRef(new Set());
  // True while the backend reads the whole conversation and synthesizes a task.
  var [toTaskBusy, setToTaskBusy] = useWbcState(false);
  var [compactBusy, setCompactBusy] = useWbcState(false);
  var [pageContextMenu, setPageContextMenu] = useWbcState(null);
  var pageContextMenuRef = useWbcRef(null);
  var pendingPageContextMenuRef = useWbcRef(null);
  var pageContextPreviewTimerRef = useWbcRef(null);
  var [quickRenameChat, setQuickRenameChat] = useWbcState(null);
  // Streaming runtimes live in the module-level engine so a run survives this
  // page unmounting when the user switches modules mid-reply. We mirror its
  // snapshot into local state only to drive re-renders.
  var runtimeEngine = WorkbenchChatRuntimes;
  var [runtimes, setRuntimes] = useWbcState(function () { return runtimeEngine.snapshot(); });
  useWbcEffect(function () {
    setRuntimes(runtimeEngine.snapshot());
    return runtimeEngine.subscribe(function (snap) { setRuntimes(snap); });
  }, []);

  useWbcEffect(function () {
    var chatId = String(activeChatId || "");
    var cancelled = false;
    if (!chatId || chatId.indexOf("legacy:") === 0) {
      setSideAgents([]);
      setSideAgentsLoading(false);
      return undefined;
    }
    // Never let the previous conversation's side questions leak into the next
    // conversation while its request is in flight.
    setSideAgents([]);
    setSideAgentsLoading(true);
    model.listSideAgents(chatId).then(function (agents) {
      if (!cancelled && activeChatIdRef.current === chatId) {
        setSideAgents(agents);
        if (agents.length) {
          setActiveSideAgentByChat(function (current) {
            if (agents.some(function (agent) { return agent.id === current[chatId]; })) {
              return current;
            }
            return Object.assign({}, current, { [chatId]: agents[agents.length - 1].id });
          });
        }
      }
    }).catch(function () {
      if (!cancelled && activeChatIdRef.current === chatId) setSideAgents([]);
    }).finally(function () {
      if (!cancelled && activeChatIdRef.current === chatId) setSideAgentsLoading(false);
    });
    return function () { cancelled = true; };
  }, [activeChatId]);
  // Holds a chat id requested by global search until the chat list is loaded.
  var pendingChatIdRef = useWbcRef("");
  // A topbar context-menu action can navigate to another conversation and
  // reveal one of its resources in the same operation.
  var pendingTopbarResourceRef = useWbcRef(null);
  var chatFileDropActive = useWorkbenchFileDrop(function (files) {
    try {
      window.dispatchEvent(new CustomEvent("cyrene:add-chat-attachments", { detail: { files: files } }));
    } catch (e) {}
  }, !!(isActive && project));

  function openViewer(file) {
    if (!file) return;
    setViewerFile(file);
    // Message file actions are direct navigation: open the shared preview
    // split immediately instead of expanding the Viewer index in the
    // conversation panel first.
    setSideTab("");
    setSideVisible(true);
    selectResourceSplit("viewer", wbcArtifactFileKey(file));
  }

  function revealTopbarResource(chatId, resource) {
    if (!chatId || !resource) return;
    if (resource.type === "browser") {
      setBrowserActiveByChat(function (prev) {
        return Object.assign({}, prev, { [chatId]: true });
      });
      setBrowserWindowModeByChat(function (prev) {
        return Object.assign({}, prev, { [chatId]: "pip" });
      });
      return;
    }
    if (resource.type === "file" && resource.file) openViewer(resource.file);
  }

  function markViewerFileRead(file) {
    if (!file || !projectId || !file.url) return;
    fetch("/api/workbench/library/read?workspace=" + encodeURIComponent(projectId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        attachment_url: String(file.url || ""),
        file_name: String(file.name || ""),
      }),
    }).catch(function () { /* Reading history must never interrupt the viewer. */ });
  }

  function loadSubagents(chatId, roundId) {
    if (!chatId) {
      setSubagentData({ rounds: [], activeRoundId: "", agents: [], messages: [] });
      return Promise.resolve(null);
    }
    setSubagentLoading(true);
    return model.getSubagents(chatId, roundId)
      .then(function (payload) {
        if (activeChatIdRef.current === chatId) setSubagentData(payload);
        return payload;
      })
      .catch(function (err) {
        if (activeChatIdRef.current === chatId) setError(wbcErrorText(err));
        return null;
      })
      .finally(function () {
        if (activeChatIdRef.current === chatId) setSubagentLoading(false);
      });
  }

  function refreshChats(selectId) {
    // Callers include one-time SSE and navigation subscriptions. Resolve the
    // target at invocation time so a mobile update after a project switch does
    // not refresh the project that happened to be active at mount time.
    var requestedProjectId = String(projectIdRef.current || "");
    if (!requestedProjectId) return Promise.resolve([]);
    var selectionAtRequest = String(activeChatIdRef.current || "");
    return model.listChats(requestedProjectId).then(function (list) {
      // A background run may finish after the user has switched projects.
      if (projectIdRef.current !== requestedProjectId) return list;
      chatCache.lists[requestedProjectId] = list;
      chatsProjectIdRef.current = requestedProjectId;
      setChats(list);
      var targetId = wbcResolveRefreshedChatSelection(
        list,
        selectId,
        selectionAtRequest,
        activeChatIdRef.current
      );
      if (targetId !== null) selectChat(targetId);
      return list;
    });
  }

  // Initial load + project switch.
  useWbcEffect(function () {
    var requestedProjectId = projectId;
    var cachedList = Array.isArray(chatCache.lists[requestedProjectId])
      ? chatCache.lists[requestedProjectId]
      : null;
    setLoading(!cachedList);
    setError("");
    setErrorKind("load");
    if (!projectId) { setChats([]); setLoading(false); return; }
    var navigation = window.CyreneUI.require("navigation");
    var pending = navigation.getPending();
    var pendingChatId = pending && pending.type === "chat" ? (pending.chatId || pending.id) : "";
    if (pendingChatId && pending.topbarResource) {
      pendingTopbarResourceRef.current = { chatId: pendingChatId, resource: pending.topbarResource };
    }
    var remembered = wbcLastChatByProject[projectId];
    function selectFrom(list) {
      var targetId = pendingChatId && list.some(function (c) { return c.id === pendingChatId; })
        ? pendingChatId
        : (remembered && list.some(function (c) { return c.id === remembered; })
          ? remembered
          : (list[0] ? list[0].id : ""));
      if (targetId === pendingChatId) pendingChatIdRef.current = pendingChatId;
      selectChat(targetId);
      setActiveChat(targetId && chatCache.details[targetId] ? chatCache.details[targetId] : null);
      return targetId;
    }
    if (cachedList) {
      chatsProjectIdRef.current = requestedProjectId;
      setChats(cachedList);
      selectFrom(cachedList);
    } else {
      chatsProjectIdRef.current = "";
      setChats([]);
      setActiveChat(null);
      selectChat("");
    }
    model.listChats(requestedProjectId)
      .then(function (list) {
        if (projectIdRef.current !== requestedProjectId) return;
        chatCache.lists[requestedProjectId] = list;
        chatsProjectIdRef.current = requestedProjectId;
        setChats(list);
        selectFrom(list);
      })
      .catch(function (err) {
        if (projectIdRef.current === requestedProjectId && !cachedList) setError(wbcErrorText(err));
      })
      .finally(function () {
        if (projectIdRef.current === requestedProjectId) setLoading(false);
      });
  }, [projectId]);

  // Apply a chat id requested by global search once the chat list is available.
  useWbcEffect(function () {
    var targetId = pendingChatIdRef.current;
    if (!targetId) return;
    if (Array.isArray(chats) && chats.some(function (c) { return c.id === targetId; })) {
      selectChat(targetId);
      pendingChatIdRef.current = "";
      window.CyreneUI.require("navigation").clearPending();
    }
  }, [chats]);

  // Load the full transcript when the selection changes. The transcript and
  // subagent history are deliberately independent: auxiliary history must not
  // prevent an otherwise healthy conversation from rendering.
  useWbcEffect(function () {
    if (activeChatId && skipNextHydrationChatIdRef.current === activeChatId) {
      skipNextHydrationChatIdRef.current = "";
      setChatLoading(false);
      setSubagentLoading(false);
      setSubagentData({ rounds: [], activeRoundId: "", agents: [], messages: [] });
      return;
    }
    var cachedChat = activeChatId ? (chatCache.details[activeChatId] || null) : null;
    // Never show a transcript from a different conversation. A cache hit is
    // safe because it is keyed by the exact target id; a miss clears first.
    if (!cachedChat) setActiveChat(null);
    if (!activeChatId) {
      setChatLoading(false);
      setSubagentData({ rounds: [], activeRoundId: "", agents: [], messages: [] });
      return;
    }
    var controller = (typeof AbortController !== "undefined") ? new AbortController() : null;
    var requestOptions = controller ? { signal: controller.signal } : {};
    var cachedSubagents = chatCache.subagents[activeChatId] || null;
    // Switching back to a recently viewed project paints its cached transcript
    // immediately. The requests below still refresh it in the background.
    setActiveChat(cachedChat);
    if (cachedSubagents) setSubagentData(cachedSubagents);
    else setSubagentData({ rounds: [], activeRoundId: "", agents: [], messages: [] });
    setError("");
    setErrorKind("load");
    setChatLoading(!cachedChat);
    setSubagentLoading(!cachedSubagents);
    model.getChat(activeChatId, requestOptions)
      .then(function (chat) {
        if (activeChatIdRef.current !== activeChatId) return;
        setActiveChat(function (previous) {
          var reconciled = wbcPreserveLiveTimelineAnchors(
            previous,
            chat,
            runtimeEngine.get(activeChatId)
          );
          chatCache.details[activeChatId] = reconciled;
          return reconciled;
        });
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        if (activeChatIdRef.current === activeChatId) {
          setError(wbcT("workbenchChat.error.transcriptPrefix", "Conversation details: {error}", { error: wbcErrorText(err) }));
        }
      })
      .finally(function () {
        if (activeChatIdRef.current === activeChatId) setChatLoading(false);
      });
    model.getSubagents(activeChatId, "", requestOptions)
      .then(function (payload) {
        chatCache.subagents[activeChatId] = payload;
        if (activeChatIdRef.current === activeChatId) setSubagentData(payload);
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        // Subagent history is auxiliary. Keep the transcript usable and retain
        // a precise diagnostic without turning this into a chat-load failure.
        console.warn("Workbench subagent history load failed", activeChatId, err);
      })
      .finally(function () {
        if (activeChatIdRef.current === activeChatId) setSubagentLoading(false);
      });
    return function () {
      if (controller) controller.abort();
    };
  }, [activeChatId, loadRevision]);

  // Viewer / content tabs belong to one conversation — reset on switch.
  useWbcEffect(function () {
    setViewerFile(null);
    setSideTab("");
  }, [activeChatId]);

  // Run after the conversation-switch reset above so a requested file remains
  // open and a requested browser session is restored directly as a PiP window.
  useWbcEffect(function () {
    var pendingResource = pendingTopbarResourceRef.current;
    if (!pendingResource || String(pendingResource.chatId) !== String(activeChatId || "")) return;
    pendingTopbarResourceRef.current = null;
    revealTopbarResource(activeChatId, pendingResource.resource);
  }, [activeChatId]);

  // New PDFs reveal the Viewer row but keep it collapsed until the user opens it.
  var lastAutoPdfUrlRef = useWbcRef("");
  useWbcEffect(function () {
    if (!activeChat || !Array.isArray(activeChat.messages)) return;
    var msgs = activeChat.messages;
    for (var mi = msgs.length - 1; mi >= 0; mi--) {
      var files = Array.isArray(msgs[mi].attachments) ? msgs[mi].attachments : [];
      for (var fi = 0; fi < files.length; fi++) {
        var f = files[fi];
        var isPdf = wbcFileViewKind(f) === "pdf";
        if (isPdf) {
          var fUrl = f.url || f.id || "";
          if (fUrl && fUrl !== lastAutoPdfUrlRef.current) {
            lastAutoPdfUrlRef.current = fUrl;
            setViewerFile(f);
            setSideVisible(true);
            return;
          }
        }
      }
    }
  }, [activeChat && activeChat.messages && activeChat.messages.map(function (m) {
    return (m.attachments || []).map(function (a) { return a.url || a.id || ''; }).join(',');
  }).join('|')]);

  // Surface the active conversation title in the topbar crumbs.
  useWbcEffect(function () {
    if (onActiveChatChange) onActiveChatChange(activeChat ? activeChat.title : "");
    return function () { if (onActiveChatChange) onActiveChatChange(""); };
  }, [activeChat && activeChat.title]);

  // Report the open conversation id up to the shell so the notification center
  // can treat replies in *this* chat as already-seen (no redundant "new" badge).
  useWbcEffect(function () {
    if (onActiveChatIdChange) onActiveChatIdChange(activeChatId || "");
    return function () { if (onActiveChatIdChange) onActiveChatIdChange(""); };
  }, [activeChatId]);

  // Global search navigation: select the requested chat when the user clicks a
  // search result. If the chat list is already loaded we apply immediately,
  // otherwise we stash the id in a ref so the effect above can apply it once
  // the list loads.
  function applyPendingChatSelection() {
    var navigation = window.CyreneUI.require("navigation");
    var pending = navigation.getPending();
    var targetId = pending && pending.type === "chat" ? (pending.chatId || pending.id) : "";
    if (!targetId) return;
    var topbarResource = pending.topbarResource || null;
    if (Array.isArray(chatsRef.current) && chatsRef.current.some(function (c) { return c.id === targetId; })) {
      if (topbarResource && String(activeChatIdRef.current || "") === String(targetId)) {
        revealTopbarResource(targetId, topbarResource);
      } else {
        if (topbarResource) pendingTopbarResourceRef.current = { chatId: targetId, resource: topbarResource };
        selectChat(targetId);
      }
      navigation.clearPending(pending);
    } else {
      pendingChatIdRef.current = targetId;
      if (topbarResource) pendingTopbarResourceRef.current = { chatId: targetId, resource: topbarResource };
      // A notification may target a chat created after this long-lived page
      // last loaded its list. Refresh the current project now; otherwise the
      // pending id has no state change that would ever cause it to be applied.
      // A cross-project target is handled by the project-change loading effect.
      var targetProjectId = String(pending.projectId || "");
      if (!targetProjectId || targetProjectId === String(projectIdRef.current || "")) {
        refreshChats(targetId);
      }
    }
  }

  useWbcEffect(function () {
    function onNavigate(event) {
      var detail = event && event.detail;
      if (detail && detail.type === "chat") applyPendingChatSelection();
    }
    window.addEventListener("cyrene:workbench-navigate", onNavigate);
    applyPendingChatSelection();
    return function () { window.removeEventListener("cyrene:workbench-navigate", onNavigate); };
  }, []);

  // Re-pull the chat list when another surface (the quick-chat window) sent a
  // message into this project, so the new conversation / reply shows up without
  // a manual refresh. Re-registered per project so refreshChats stays current.
  useWbcEffect(function () {
    function onRefresh(event) {
      var detail = (event && event.detail) || {};
      if (detail.projectId && String(detail.projectId) !== String(projectId)) return;
      refreshChats(detail.selectId || "");
    }
    window.addEventListener("cyrene:wbc-refresh-chats", onRefresh);
    return function () { window.removeEventListener("cyrene:wbc-refresh-chats", onRefresh); };
  }, [projectId]);

  // Live tool progress: reuse the platform SSE feed and keep only
  // events tagged with a running conversation's session id.
  useWbcEffect(function () {
    function onEvent(event) {
      if (!event) return;
      if (event.type === "workbench_chat_changed") {
        if (
          event.project_id
          && String(event.project_id) !== String(projectIdRef.current || "")
        ) return;
        var changedChatId = String(event.chat_id || event.session_id || event.chatId || "");
        remoteChangedChatIdsRef.current.add(changedChatId || "*");
        if (remoteChatRefreshTimerRef.current) {
          clearTimeout(remoteChatRefreshTimerRef.current);
        }
        remoteChatRefreshTimerRef.current = setTimeout(function () {
          remoteChatRefreshTimerRef.current = null;
          var changedChatIds = remoteChangedChatIdsRef.current;
          remoteChangedChatIdsRef.current = new Set();
          refreshChats("");
          var openChatId = String(activeChatIdRef.current || "");
          if (openChatId && (changedChatIds.has("*") || changedChatIds.has(openChatId))) {
            // Remote-control surfaces persist the user/reply messages without
            // going through this page's optimistic streaming hooks. Re-run the
            // detail hydration as well as the rail refresh so the conversation
            // already on screen reflects the durable transcript immediately.
            setLoadRevision(function (value) { return value + 1; });
          }
        }, 80);
        return;
      }
      if (event.type === "workspace_changes") {
        try { window.dispatchEvent(new CustomEvent("workbench:workspace-changes", { detail: event })); } catch (e) {}
      }
      if (event.type === "workbench_proactive_message") {
        if (String(event.project_id || "") !== String(projectIdRef.current || "")) return;
        var proactiveChatId = String(event.chat_id || event.session_id || "");
        var proactiveMessage = event.message;
        var updatedAt = String(event.updated_at || (proactiveMessage && proactiveMessage.createdAt) || "");
        setChats(function (prev) {
          var found = false;
          var next = prev.map(function (chat) {
            if (chat.id !== proactiveChatId) return chat;
            found = true;
            return {
              ...chat,
              updatedAt: updatedAt || chat.updatedAt,
              preview: proactiveMessage ? proactiveMessage.content : chat.preview,
              messageCount: (chat.messageCount || 0) + 1,
            };
          });
          if (!found) return prev;
          return next.slice().sort(function (a, b) {
            return String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""));
          });
        });
        if (activeChatIdRef.current === proactiveChatId && proactiveMessage) {
          setActiveChat(function (prev) {
            if (!prev || prev.id !== proactiveChatId) return prev;
            var messages = prev.messages || [];
            if (messages.some(function (item) { return item.id === proactiveMessage.id; })) return prev;
            return {
              ...prev,
              updatedAt: updatedAt || prev.updatedAt,
              messages: messages.concat([proactiveMessage]),
            };
          });
        }
        return;
      }
      var chatId = String(event.session_id || event.chat_id || event.chatId || "");
      if (
        chatId
        && activeChatIdRef.current === chatId
        && (event.type === "plan_progress" || event.type === "plan")
        && event.plan
      ) {
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return { ...prev, activePlan: event.plan };
        });
      }
      if (
        chatId
        && activeChatIdRef.current === chatId
        && (event.type === "subagent_update" || event.type === "agent_comm" || event.type === "agent_chat_user_message")
      ) {
        if (subagentRefreshTimerRef.current) clearTimeout(subagentRefreshTimerRef.current);
        subagentRefreshTimerRef.current = setTimeout(function () {
          loadSubagents(chatId);
        }, 120);
      }
      var browserEventChatId = String(event.session_id || event.chat_id || event.chatId || "");
      if (
        (event.type === "browser_frame" || event.type === "browser_takeover_request")
        && activeChatIdRef.current
        && (!browserEventChatId || browserEventChatId === String(activeChatIdRef.current))
      ) {
        setBrowserActiveByChat(function (prev) {
          var sid = String(browserEventChatId || activeChatIdRef.current || "");
          if (!sid || prev[sid]) return prev;
          return { ...prev, [sid]: true };
        });
        setBrowserWindowModeByChat(function (prev) {
          var sid = String(browserEventChatId || activeChatIdRef.current || "");
          if (!sid || prev[sid]) return prev;
          return { ...prev, [sid]: "pip" };
        });
      }
      // Live tool/phase/subagent progress is folded into the runtime by the
      // module-level engine (WorkbenchChatRuntimes) so it keeps accumulating even
      // when this page is unmounted; nothing to do here.
    }
    var unsubscribe = window.CyreneUI.require("events").subscribe(onEvent);
    return function () {
      unsubscribe();
      if (remoteChatRefreshTimerRef.current) {
        clearTimeout(remoteChatRefreshTimerRef.current);
        remoteChatRefreshTimerRef.current = null;
      }
      remoteChangedChatIdsRef.current = new Set();
    };
  }, []);

  // 按对话查询 Electron 中对应的 BrowserTabManager。每个 manager 的 tabs
  // 和 persistent partition 都由 chatId 隔离，刷新 UI 不会把别的对话误认
  // 为当前对话的浏览器。
  var browserRestoredRef = useWbcRef({});
  useWbcEffect(function () {
    function handleCopiedBrowser(event) {
      var targetChatId = String(event && event.detail && event.detail.targetChatId || "");
      if (!targetChatId) return;
      browserRestoredRef.current[targetChatId] = true;
      setBrowserActiveByChat(function (prev) {
        return Object.assign({}, prev, { [targetChatId]: true });
      });
      setBrowserWindowModeByChat(function (prev) {
        return Object.assign({}, prev, { [targetChatId]: "pip" });
      });
    }
    window.addEventListener("cyrene:browser-copied-to-chat", handleCopiedBrowser);
    return function () {
      window.removeEventListener("cyrene:browser-copied-to-chat", handleCopiedBrowser);
    };
  }, []);

  useWbcEffect(function () {
    var bridge = window.cyrene && window.cyrene.browser;
    if (!bridge || typeof bridge.getState !== "function") return;
    var chatId = activeChatId || "";
    if (!chatId) return;
    if (browserRestoredRef.current[chatId]) return;
    browserRestoredRef.current[chatId] = true;
    bridge.getState(chatId).then(function (state) {
      if (String(state && state.sessionId || "") !== String(chatId)) return;
      if (!state || !state.tabs || !Array.isArray(state.tabs) || !state.tabs.length) return;
      setBrowserActiveByChat(function (prev) {
        if (prev[chatId]) return prev;
        return Object.assign({}, prev, { [chatId]: true });
      });
      setBrowserWindowModeByChat(function (prev) {
        if (prev[chatId]) return prev;
        return Object.assign({}, prev, { [chatId]: "pip" });
      });
    }).catch(function (err) { console.error("getState failed", err); });
  }, [activeChatId]);

  function ensureChat() {
    if (activeChatId) return Promise.resolve(activeChatId);
    return model.createChat(projectId).then(function (chat) {
      try {
        window.dispatchEvent(new CustomEvent("cyrene:wbc-chat-created", {
          detail: { projectId: projectId, chatId: chat.id },
        }));
      } catch (e) {}
      setChats(function (prev) { return [chat].concat(prev); });
      skipNextHydrationChatIdRef.current = chat.id;
      selectChat(chat.id);
      setActiveChat(chat);
      return chat.id;
    });
  }

  function retryLoad() {
    if (!projectId) return;
    setError("");
    setErrorKind("load");
    setChatLoading(true);
    refreshChats(activeChatId)
      .then(function (list) {
        var chatId = activeChatId || (list[0] && list[0].id) || "";
        if (!chatId) {
          setActiveChat(null);
          setChatLoading(false);
          return null;
        }
        selectChat(chatId);
        setLoadRevision(function (value) { return value + 1; });
        return null;
      })
      .catch(function (err) {
        setChatLoading(false);
        setError(wbcT("workbenchChat.error.listPrefix", "Chat list: {error}", { error: wbcErrorText(err) }));
      });
  }

  // Register transcript hooks with the streaming engine so a run patches THIS
  // page's local transcript / chat list while it is mounted. Re-registered every
  // render so the closures (refreshChats, model …) never go stale; on unmount the
  // cleanup clears them and the run streams on, with the transcript re-pulled
  // from the server on remount. Each hook guards by chatId so a background run
  // only touches the conversation it belongs to.
  useWbcEffect(function () {
    runtimeEngine.setHooks({
      onUserMessage: function (chatId, userMessage) {
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return { ...prev, messages: wbcMergeChronologicalMessages(prev.messages || [], [userMessage]) };
        });
      },
      onUserMessageConfirmed: function (chatId, confirmation) {
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          var userMessage = confirmation && confirmation.userMessage;
          if (!userMessage) return prev;
          var optimisticId = String(confirmation.optimisticId || "");
          var messages = prev.messages || [];
          if (optimisticId) {
            for (var i = 0; i < messages.length; i++) {
              if (String(messages[i] && messages[i].id || "") !== optimisticId) continue;
              var confirmed = messages.slice();
              confirmed[i] = wbcConfirmOptimisticMessage(messages[i], userMessage);
              return { ...prev, messages: confirmed };
            }
          }
          return { ...prev, messages: wbcMergeChronologicalMessages(messages, [userMessage]) };
        });
      },
      onRetryTruncate: function (chatId, truncateInfo) {
        // Regenerating: drop everything after the replayed user message.
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          var list = prev.messages || [];
          var afterId = typeof truncateInfo === "string" ? truncateInfo : String(truncateInfo && truncateInfo.afterId || "");
          var hasExplicitReplacedIds = !!(truncateInfo && Array.isArray(truncateInfo.replacedIds));
          var replacedIds = new Set(
            hasExplicitReplacedIds ? truncateInfo.replacedIds.map(String) : []
          );
          if (hasExplicitReplacedIds) {
            return {
              ...prev,
              messages: list.filter(function (item) { return !replacedIds.has(String(item && item.id || "")); }),
            };
          }
          var cut = -1;
          for (var i = 0; i < list.length; i++) {
            if (String(list[i].id) === afterId) { cut = i; break; }
          }
          if (cut < 0) return prev;
          return { ...prev, messages: list.slice(0, cut + 1) };
        });
      },
      onAssistantSaved: function (chatId, assistantMessages) {
        // A background conversation has no active React transcript to patch.
        // Persist the terminal messages into its detail cache before the
        // runtime is cleared so switching to it never paints a stale snapshot.
        var cachedChat = chatCache.details[chatId] || null;
        if (cachedChat) {
          chatCache.details[chatId] = wbcMergeSavedAssistantMessages(
            cachedChat,
            assistantMessages
          );
        }
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return wbcMergeSavedAssistantMessages(prev, assistantMessages);
        });
      },
      onAwaitingUser: function (chatId, pendingQuestion) {
        // The run paused for a permission / clarification answer — stash the
        // question so the composer shows an answer prompt instead of a reply.
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return { ...prev, status: "idle", pendingQuestion: pendingQuestion || null };
        });
      },
      onInterrupted: function (chatId) {
        // The server emits this only after accepting the interruption. Clear
        // the stale persisted-looking state immediately; the interrupt request
        // also settles storage before its response completes.
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return { ...prev, status: "idle" };
        });
        refreshChats();
      },
      onError: function (chatId, err) {
        setErrorKind("message");
        setError(wbcErrorText(err));
      },
      onSettled: function (chatId) {
        model.getChat(chatId).then(function (chat) {
          chatCache.details[chatId] = chat;
          if (activeChatIdRef.current === chatId) setActiveChat(chat);
        }).catch(function () {});
        refreshChats();
      },
      onResync: function (chatId) {
        // Stream ended without a `saved` event (e.g. interrupted) — re-pull.
        model.getChat(chatId).then(function (chat) {
          chatCache.details[chatId] = chat;
          if (activeChatIdRef.current === chatId) setActiveChat(chat);
        }).catch(function () {});
        refreshChats();
      },
    });
    return function () { runtimeEngine.setHooks(null); };
  });

  useWbcEffect(function () {
    if (
      activeChat
      && activeChat.id
      && activeChat.status === "running"
      && !runtimeEngine.isRunning(activeChat.id)
    ) {
      runtimeEngine.reconnect(activeChat.id, model);
    }
  }, [activeChat && activeChat.id, activeChat && activeChat.status]);

  function handleSend(input) {
    setError("");
    setErrorKind("load");
    var preparedInput = Object.assign({}, input || {});
    preparedInput.mode = wbcNormalizePermissionMode(
      preparedInput.mode,
      activeChat && activeChat.permissionMode
        ? activeChat.permissionMode
        : "auto"
    );
    return ensureChat().then(function (chatId) {
      setActiveChat(function (prev) {
        if (!prev || prev.id !== chatId) return prev;
        return { ...prev, permissionMode: preparedInput.mode };
      });
      // The engine owns the stream (so it outlives this page) and enforces a
      // single in-flight run per conversation.
      return runtimeEngine.start(chatId, preparedInput, model);
    }).catch(function (err) {
      setError(wbcErrorText(err));
    });
  }

  function handleAskSelection(text) {
    var quote = String(text || "").trim().slice(0, 12000);
    var parentChatId = String(activeChatIdRef.current || "");
    if (!quote || !parentChatId || parentChatId.indexOf("legacy:") === 0 || sideAgentCreating) {
      return Promise.resolve(null);
    }
    setSideAgentCreating(true);
    setSideVisible(true);
    return model.createSideAgent(parentChatId, quote).then(function (agent) {
      if (activeChatIdRef.current !== parentChatId) return agent;
      setSideAgents(function (current) {
        return current.some(function (item) { return item.id === agent.id; })
          ? current
          : current.concat([agent]);
      });
      setActiveSideAgentByChat(function (current) {
        return Object.assign({}, current, { [parentChatId]: agent.id });
      });
      setSideTab("side-agents");
      return agent;
    }).catch(function (err) {
      setErrorKind("message");
      setError(wbcErrorText(err));
      throw err;
    }).finally(function () {
      setSideAgentCreating(false);
    });
  }

  function updateSideAgent(nextAgent) {
    if (!nextAgent || !nextAgent.id) return;
    setSideAgents(function (current) {
      return current.map(function (item) {
        return item.id === nextAgent.id ? nextAgent : item;
      });
    });
  }

  function deleteSideAgent(agentId) {
    var id = String(agentId || "");
    if (!id) return Promise.resolve();
    return model.deleteChat(id).then(function () {
      setSideAgents(function (current) {
        var next = current.filter(function (item) { return item.id !== id; });
        var parentChatId = String(activeChatIdRef.current || "");
        setActiveSideAgentByChat(function (selection) {
          if (selection[parentChatId] !== id) return selection;
          var updated = Object.assign({}, selection);
          if (next.length) updated[parentChatId] = next[next.length - 1].id;
          else delete updated[parentChatId];
          return updated;
        });
        setSideAgentSplitByChat(function (openByChat) {
          if (openByChat[parentChatId] !== id) return openByChat;
          var updated = Object.assign({}, openByChat);
          delete updated[parentChatId];
          return updated;
        });
        if (!next.length) setSideTab("");
        return next;
      });
    }).catch(function (err) {
      setErrorKind("message");
      setError(wbcErrorText(err));
    });
  }

  function selectSideAgent(agentId) {
    var chatId = String(activeChatIdRef.current || "");
    var id = String(agentId || "");
    if (!chatId || !id) return;
    setActiveSideAgentByChat(function (current) {
      return Object.assign({}, current, { [chatId]: id });
    });
    setSideAgentSplitByChat(function (current) {
      return Object.assign({}, current, { [chatId]: id });
    });
    setArtifactSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setChangeSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setResourceSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function selectArtifact(file) {
    var chatId = String(activeChatIdRef.current || "");
    var key = wbcArtifactFileKey(file);
    if (!chatId || !key) return;
    setArtifactSplitByChat(function (current) {
      return Object.assign({}, current, { [chatId]: key });
    });
    setSideAgentSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setChangeSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setResourceSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function selectChange(change) {
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId || !change || !change.setId || !change.path) return;
    setChangeSplitByChat(function (current) {
      return Object.assign({}, current, { [chatId]: change });
    });
    setSideAgentSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setArtifactSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setResourceSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function selectResourceSplit(type, payload) {
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId || !type) return;
    setResourceSplitByChat(function (current) {
      return Object.assign({}, current, { [chatId]: { type: type, payload: payload } });
    });
    setSideAgentSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setArtifactSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setChangeSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function beginFloatingPanelSplit(openSplit) {
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId || typeof openSplit !== "function") return;
    if (!floatingSplitRestoreRef.current) {
      floatingSplitRestoreRef.current = {
        chatId: chatId,
        splitSide: splitSide,
        sideAgentId: sideAgentSplitByChat[chatId] || "",
        artifactKey: artifactSplitByChat[chatId] || "",
        change: changeSplitByChat[chatId] || null,
        resource: resourceSplitByChat[chatId] || null,
      };
    }
    setFloatingConversationPanelOpen(false);
    // A resource chosen from the floating conversation panel always owns the
    // right track. If the conversation was on the right, the animated grid
    // moves it left before the new resource enters from the right.
    setSplitSideDirect("right");
    openSplit();
  }

  function restoreFloatingPanelSplit() {
    var snapshot = floatingSplitRestoreRef.current;
    var chatId = String(activeChatIdRef.current || "");
    if (!snapshot || !chatId || snapshot.chatId !== chatId) return false;
    floatingSplitRestoreRef.current = null;

    function restoreEntry(setter, value) {
      setter(function (current) {
        var updated = Object.assign({}, current);
        if (value) updated[chatId] = value;
        else delete updated[chatId];
        return updated;
      });
    }

    restoreEntry(setSideAgentSplitByChat, snapshot.sideAgentId);
    restoreEntry(setArtifactSplitByChat, snapshot.artifactKey);
    restoreEntry(setChangeSplitByChat, snapshot.change);
    restoreEntry(setResourceSplitByChat, snapshot.resource);
    setSplitSideDirect(snapshot.splitSide === "left" ? "left" : "right");
    return true;
  }

  function closeSideAgentSplit() {
    setFloatingConversationPanelOpen(false);
    if (restoreFloatingPanelSplit()) return;
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId) return;
    setSideAgentSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function closeArtifactSplit() {
    setFloatingConversationPanelOpen(false);
    if (restoreFloatingPanelSplit()) return;
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId) return;
    setArtifactSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function closeChangeSplit() {
    setFloatingConversationPanelOpen(false);
    if (restoreFloatingPanelSplit()) return;
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId) return;
    setChangeSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function closeResourceSplit() {
    setFloatingConversationPanelOpen(false);
    if (restoreFloatingPanelSplit()) return;
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId) return;
    setResourceSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function closeActiveSplit() {
    setFloatingConversationPanelOpen(false);
    if (restoreFloatingPanelSplit()) return;
    closeSideAgentSplit();
    closeArtifactSplit();
    closeChangeSplit();
    closeResourceSplit();
  }

  // A rail chat dragged onto the right side (or the open split) is opened
  // beside the main conversation instead of replacing it. A dedicated drop
  // layer covers the right zone while a chat drag is in progress; the main
  // conversation column keeps its original drop-to-open behaviour untouched.
  var pageRef = useWbcRef(null);
  var [chatDragSession, setChatDragSession] = useWbcState(false);
  var [chatSideDropActive, setChatSideDropActive] = useWbcState(false);
  var chatSideDropClearTimerRef = useWbcRef(null);

  useWbcEffect(function () {
    function onDocumentDragStart(event) {
      // Bubbles after the rail's React onDragStart has populated the
      // dataTransfer, so the chat MIME is already visible here.
      if (!wbcHasChatDrag(event)) return;
      setChatDragSession(true);
    }
    function onDocumentDragEnd() {
      setChatDragSession(false);
      setChatSideDropActive(false);
    }
    document.addEventListener("dragstart", onDocumentDragStart);
    document.addEventListener("dragend", onDocumentDragEnd);
    document.addEventListener("drop", onDocumentDragEnd);
    return function () {
      document.removeEventListener("dragstart", onDocumentDragStart);
      document.removeEventListener("dragend", onDocumentDragEnd);
      document.removeEventListener("drop", onDocumentDragEnd);
    };
  }, []);

  function handleSideLayerDragOver(event) {
    if (!wbcHasChatDrag(event)) return;
    if (chatSideDropClearTimerRef.current) {
      clearTimeout(chatSideDropClearTimerRef.current);
      chatSideDropClearTimerRef.current = null;
    }
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    setChatSideDropActive(true);
  }

  function handleSideLayerDragLeave(event) {
    if (event.currentTarget.contains(event.relatedTarget)) return;
    // Dragleave fires between element transitions with a null relatedTarget,
    // so clearing immediately would fight the dragover highlight. Defer the
    // clear; an in-zone dragover cancels it.
    if (chatSideDropClearTimerRef.current) clearTimeout(chatSideDropClearTimerRef.current);
    chatSideDropClearTimerRef.current = setTimeout(function () {
      setChatSideDropActive(false);
      chatSideDropClearTimerRef.current = null;
    }, 200);
  }

  function handleSideLayerDrop(event) {
    if (!wbcHasChatDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (chatSideDropClearTimerRef.current) {
      clearTimeout(chatSideDropClearTimerRef.current);
      chatSideDropClearTimerRef.current = null;
    }
    setChatSideDropActive(false);
    setChatDragSession(false);
    var payload = wbcReadChatDrag(event);
    if (payload && payload.id) openChatSplit(String(payload.id));
  }


  function openChatSplit(chatId) {
    var parentId = String(activeChatIdRef.current || "");
    if (!parentId || !chatId) return;
    selectResourceSplit("chat", String(chatId));
    setSideVisible(true);
    // The drop zone lives on the right side, so a drag-opened split should
    // appear there regardless of the remembered side preference.
    setSplitSideDirect("right");
  }

  function resizeSideAgentSplit(width) {
    var next = wbcClampSideSplitWidth(width, window.innerWidth);
    if (!next) return;
    setSideAgentSplitWidth(next);
    try { localStorage.setItem("wbc-side-agent-split-width", String(next)); } catch (e) {}
  }

  function handleInterrupt() {
    runtimeEngine.interrupt(activeChatIdRef.current, model);
  }

  function handleGuidance(message) {
    var chatId = activeChatIdRef.current;
    var text = String(message || "").trim();
    if (!chatId || !text || !runtimeEngine.isRunning(chatId)) return Promise.resolve(null);
    var clientRequestId = "guide_" + Date.now();
    var optimisticMessage = {
      id: "guidance_pending_" + clientRequestId,
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
      guidance: true,
      optimistic: true,
      clientRequestId: clientRequestId,
    };
    setError("");
    runtimeEngine.closeTimeline(chatId);
    setActiveChat(function (prev) {
      if (!prev || prev.id !== chatId) return prev;
      return { ...prev, messages: wbcMergeChronologicalMessages(prev.messages || [], [optimisticMessage]) };
    });
    return model.sendGuidance(chatId, text, clientRequestId).then(function (response) {
      if (response && response.userMessage) {
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return { ...prev, messages: wbcMergeChronologicalMessages(prev.messages || [], [response.userMessage]) };
        });
      }
      return response;
    }).catch(function (err) {
      setActiveChat(function (prev) {
        if (!prev || prev.id !== chatId) return prev;
        return {
          ...prev,
          messages: (prev.messages || []).filter(function (item) {
            return String(item && item.clientRequestId || "") !== clientRequestId;
          }),
        };
      });
      if (err && err.code === "chat_not_running") {
        runtimeEngine.deferSend(chatId, { message: text }, model);
        return { deferred: true };
      }
      setErrorKind("message");
      setError(wbcErrorText(err));
      throw err;
    });
  }

  // Answer the pending permission / clarification question → resume the round.
  // The server returns the continued reply (append it) or a follow-up question
  // (swap the prompt). Optimistically clears the prompt while resuming.
  function handleAnswer(questionId, optionText, resumeMode) {
    var chatId = activeChatId;
    if (!chatId || !questionId || !optionText) return;
    setError("");
    var optimisticAnswer = {
      id: "answer_pending_" + Date.now(),
      role: "user",
      content: optionText,
      createdAt: new Date().toISOString(),
      answerToQuestionId: questionId,
      optimistic: true,
    };
    setActiveChat(function (prev) {
      if (!prev || prev.id !== chatId) return prev;
      return {
        ...prev,
        pendingQuestion: null,
        status: "running",
        messages: wbcMergeChronologicalMessages(prev.messages || [], [optimisticAnswer]),
      };
    });
    // Drive a live runtime for the resume so the thread streams the same feedback
    // as a normal send: the "Thinking..." card renders immediately and SSE tool
    // progress folds into it (onSseEvent only fills a runtime that already exists).
    // Without it the resume ran invisibly — an empty thread while the side panel
    // showed a frozen "Replying" — and the composer offered no way to stop it.
    runtimeEngine.update(chatId, { chatId: chatId, text: "", progress: [], activities: [], activitySeq: 0, segments: [], startedAt: Date.now(), lastEventAt: Date.now(), replying: true });
    var answerMode = wbcNormalizePermissionMode(
      resumeMode,
      activeChat && activeChat.permissionMode
        ? activeChat.permissionMode
        : "default"
    );
    model.answerChat(chatId, questionId, optionText, { mode: answerMode }).then(function (res) {
      runtimeEngine.update(chatId, null);
      // Pull the durable transcript: it now contains the question, this answer,
      // every pre-question tool/intermediate block, and the continued reply.
      return model.getChat(chatId).then(function (chat) {
        if (activeChatIdRef.current === chatId) setActiveChat(chat);
      });
    }).then(function () {
      refreshChats();
    }).catch(function (err) {
      runtimeEngine.update(chatId, null);
      setError(wbcErrorText(err));
      // Restore the prompt so the user can retry.
      model.getChat(chatId).then(setActiveChat).catch(function () {});
    });
  }

  // Regenerate the last assistant reply (replays the last user message).
  function handleRetryMessage() {
    if (!activeChat || activeChat.legacy || runtimeEngine.isRunning(activeChat.id)) return;
    handleSend({ retry: true });
  }

  // Edit a user message → fork the conversation at that point, switch to the
  // forked chat, and replay the edited turn through the streaming engine. The
  // original conversation is preserved untouched.
  function handleEditMessage(messageId, newContent) {
    if (!activeChat || activeChat.legacy || runtimeEngine.isRunning(activeChat.id)) return;
    if (!messageId || !newContent) return;
    setError("");
    var replayMode = wbcNormalizePermissionMode(
      activeChat && activeChat.permissionMode,
      "auto"
    );
    model.forkChat(activeChat.id, messageId, newContent).then(function (newChat) {
      newChat = { ...newChat, permissionMode: replayMode };
      setChats(function (prev) { return [newChat].concat(prev); });
      skipNextHydrationChatIdRef.current = newChat.id;
      selectChat(newChat.id);
      setActiveChat(newChat);
      // Replay the edited user message (already the last entry in the forked
      // transcript) through the agent. forkReplay tells the server the state
      // was already truncated by the fork — no re-truncation needed.
      return runtimeEngine.start(newChat.id, { retry: true, forkReplay: true, mode: replayMode }, model);
    }).catch(function (err) { setError(wbcErrorText(err)); });
  }

  function handleCreateChat() {
    return model.createChat(projectId).then(function (chat) {
      setChats(function (prev) { return [chat].concat(prev); });
      skipNextHydrationChatIdRef.current = chat.id;
      selectChat(chat.id);
      setActiveChat(chat);
    }).catch(function (err) { setError(wbcErrorText(err)); });
  }

  // The shell-level menu/shortcut owns navigation, while this page owns chat
  // persistence. A monotonically increasing request id bridges those layers and
  // still works when the chat page is mounted by the same render as Cmd/Ctrl+N.
  useWbcEffect(function () {
    var requestId = Number(newChatRequestId || 0);
    if (
      !requestId
      || requestId === handledNewChatRequestIdRef.current
      || !isActive
      || !projectId
    ) return;
    handledNewChatRequestIdRef.current = requestId;
    handleCreateChat();
  }, [newChatRequestId, isActive, projectId]);

  function handleRenameChat(chatId, title) {
    if (!chatId) return Promise.resolve();
    return model.renameChat(chatId, title).then(function (chat) {
      setActiveChat(function (prev) {
        return prev && prev.id === chat.id ? { ...prev, title: chat.title } : prev;
      });
      setChats(function (prev) {
        return prev.map(function (item) { return item.id === chat.id ? { ...item, title: chat.title } : item; });
      });
      return chat;
    });
  }

  function handleRename(title) {
    if (!activeChat) return Promise.resolve();
    return handleRenameChat(activeChat.id, title);
  }

  function openQuickRename() {
    if (!activeChat || activeChat.legacy) return;
    closePageContextMenu();
    setQuickRenameChat(activeChat);
  }

  function setOpenPageContextMenu(menu) {
    pageContextMenuRef.current = menu;
    setPageContextMenu(menu);
  }

  function clearPendingPageContextMenu() {
    pendingPageContextMenuRef.current = null;
    if (pageContextPreviewTimerRef.current) {
      clearTimeout(pageContextPreviewTimerRef.current);
      pageContextPreviewTimerRef.current = null;
    }
  }

  function closePageContextMenu() {
    var current = pageContextMenuRef.current;
    var pending = pendingPageContextMenuRef.current;
    clearPendingPageContextMenu();
    setOpenPageContextMenu(null);
    if ((current && current.browserPreview) || pending) {
      wbcNotifyBrowserWindowInteraction(false, "context-menu", (current && current.browserSessionId) || (pending && pending.browserSessionId) || activeChatIdRef.current);
    }
  }

  function openPageContextMenu(event) {
    if (!activeChat || activeChat.legacy || !wbcCanOpenPageContextMenu(event)) return;
    event.preventDefault();
    event.stopPropagation();
    closePageContextMenu();
    var nativeHost = document.querySelector(".wbc-browser-window .browser-native-host");
    var nativeRect = nativeHost && nativeHost.getBoundingClientRect();
    var placement = wbcPageContextMenuPlacement(event.clientX, event.clientY, nativeRect);
    var menu = {
      left: placement.left,
      top: placement.top,
      browserPreview: false,
      browserSessionId: String(activeChat.id || ""),
    };
    if (!placement.overlapsBrowser) {
      setOpenPageContextMenu(menu);
      return;
    }
    pendingPageContextMenuRef.current = menu;
    wbcNotifyBrowserWindowInteraction(true, "context-menu", menu.browserSessionId);
    pageContextPreviewTimerRef.current = setTimeout(function () {
      if (pendingPageContextMenuRef.current !== menu) return;
      clearPendingPageContextMenu();
      wbcNotifyBrowserWindowInteraction(false, "context-menu", menu.browserSessionId);
      window.CyreneUI.require("feedback").showToast(
        wbcT("workbenchChat.contextMenuUnavailable", "Could not open the chat menu over the browser window."),
        "warning"
      );
    }, 900);
  }

  function handleDelete() {
    if (!activeChat) return;
    handleDeleteChat(activeChat.id);
  }

  function handleDeleteChat(chatId) {
    if (!chatId) return;
    var deletingActiveChat = activeChatId === chatId;
    function detachDeletedForkSource(item) {
      if (!item || String(item.forkedFromChatId || "") !== String(chatId)) return item;
      var cleaned = { ...item };
      delete cleaned.forkedFromChatId;
      delete cleaned.forkedAtMessageId;
      delete cleaned.forkMessage;
      return cleaned;
    }
    window.CyreneUI.require("feedback").confirmModal({
      body: wbcT("workbenchChat.confirmDelete", "Delete this chat? Its messages cannot be recovered."),
      confirmLabel: wbcT("common.delete", "Delete"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      var deletedIndex = chats.findIndex(function (item) { return item.id === chatId; });
      var deletedItem = deletedIndex >= 0 ? chats[deletedIndex] : null;
      var deletedActiveChat = deletingActiveChat ? activeChat : null;
      var detachedForks = chats.filter(function (item) {
        return String(item.forkedFromChatId || "") === String(chatId);
      });
      var previousActiveChat = activeChat;
      setChats(function (prev) {
        var next = prev
          .filter(function (item) { return item.id !== chatId; })
          .map(detachDeletedForkSource);
        if (deletingActiveChat) selectChat(next[0] ? next[0].id : "");
        return next;
      });
      if (deletingActiveChat) setActiveChat(null);
      else setActiveChat(function (prev) { return detachDeletedForkSource(prev); });
      model.deleteChat(chatId).then(function () {
        runtimeEngine.abort(chatId);
        runtimeEngine.clear(chatId);
      }).catch(function (err) {
        if (deletedItem) {
          setChats(function (prev) {
            var next = prev.map(function (item) {
              var original = detachedForks.find(function (fork) { return fork.id === item.id; });
              return original ? {
                ...item,
                forkedFromChatId: original.forkedFromChatId,
                forkedAtMessageId: original.forkedAtMessageId,
                forkMessage: original.forkMessage,
              } : item;
            });
            if (!next.some(function (item) { return item.id === chatId; })) {
              next.splice(Math.min(Math.max(deletedIndex, 0), next.length), 0, deletedItem);
            }
            return next;
          });
        }
        if (deletingActiveChat) {
          selectChat(chatId);
          setActiveChat(deletedActiveChat);
        } else if (
          previousActiveChat
          && String(previousActiveChat.forkedFromChatId || "") === String(chatId)
        ) {
          setActiveChat(previousActiveChat);
        }
        setError(wbcErrorText(err));
      });
    });
  }

  function handleToTask(chatId) {
    var targetChatId = typeof chatId === "string"
      ? chatId
      : String(activeChat && activeChat.id || "");
    if (!targetChatId || toTaskBusy) return;
    setToTaskBusy(true);
    setError("");
    model.toTask(targetChatId).then(function (payload) {
      if (onOpenTask) onOpenTask(payload);
    }).catch(function (err) {
      setError(wbcErrorText(err));
    }).then(function () {
      setToTaskBusy(false);
    });
  }

  function handleCompact() {
    if (!activeChat || activeChat.legacy || compactBusy) return;
    setCompactBusy(true);
    setError("");
    model.compactChat(activeChat.id).then(function (payload) {
      var before = Number(payload.beforeTokens || 0);
      var after = Number(payload.afterTokens || before);
      var limit = Number(payload.ctxLimit || 0);
      if (payload.compacted) {
        setActiveChat(function (prev) {
          return prev ? { ...prev, contextRevision: Date.now() } : prev;
        });
        window.CyreneUI.require("feedback").showToast(wbcT(
          "workbenchChat.compactSuccess",
          "Chat compressed: {before}% → {after}%",
          {
            before: limit > 0 ? Math.round(before / limit * 100) : "—",
            after: limit > 0 ? Math.round(after / limit * 100) : "—",
          }
        ), "success");
        return;
      }
      if (payload.reason === "empty") {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactEmpty", "There is no agent context to compress."), "warning");
      } else if (payload.reason === "running") {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactRunning", "The agent is currently working. Try again after it finishes."), "warning");
      } else if (payload.reason === "awaiting_user") {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactAwaitingUser", "Answer the agent's question before compressing this chat."), "warning");
      } else if (payload.reason === "no_tool_activity") {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactNoTools", "This chat has no tool activity to compress."), "warning");
      } else if (payload.reason === "distilling") {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactDistilling", "Background context compression is still running. Try again shortly."), "warning");
      } else {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactNoChange", "No earlier context is available to compress."), "warning");
      }
    }).catch(function (err) {
      setError(wbcErrorText(err));
    }).then(function () {
      setCompactBusy(false);
    });
  }

  function onToggleSide() { setSideVisible(function (v) { return !v; }); }

  useWbcEffect(function () {
    function onBrowserPreviewReady(event) {
      var pending = pendingPageContextMenuRef.current;
      if (!pending) return;
      var detail = event && event.detail || {};
      if (String(detail.sessionId || "") !== String(pending.browserSessionId || "")) return;
      clearPendingPageContextMenu();
      if (detail.fallback) {
        wbcNotifyBrowserWindowInteraction(false, "context-menu", pending.browserSessionId);
        window.CyreneUI.require("feedback").showToast(
          wbcT("workbenchChat.contextMenuUnavailable", "Could not open the chat menu over the browser window."),
          "warning"
        );
        return;
      }
      setOpenPageContextMenu({ ...pending, browserPreview: true });
    }
    window.addEventListener("workbench:browser-window-preview-ready", onBrowserPreviewReady);
    return function () {
      window.removeEventListener("workbench:browser-window-preview-ready", onBrowserPreviewReady);
      closePageContextMenu();
    };
  }, []);

  useWbcEffect(function () {
    closePageContextMenu();
    setQuickRenameChat(null);
  }, [activeChatId]);

  // The open conversation only renders and controls its own runtime. Other
  // conversations continue streaming in the background.
  var activeRuntime = runtimes[activeChatId] || null;
  var activeRunning = !!activeRuntime;
  // Effects run after paint, so also guard the render itself against a stale
  // activeChat during the ID -> transcript fetch gap.
  var visibleChat = activeChat && String(activeChat.id || "") === String(activeChatId || "")
    ? activeChat
    : null;
  var selectedChatSummary = chats.find(function (item) {
    return String(item.id || "") === String(activeChatId || "");
  }) || null;
  var activeBrowserState = wbcBrowserStateForChat(activeChatId);
  var browserMarkedActive = !!(browserActiveByChat && browserActiveByChat[activeChatId]);
  var hasActiveBrowser = !!((activeBrowserState && activeBrowserState.active) || browserMarkedActive);
  var browserWindowMode = browserWindowModeByChat[activeChatId] || "pip";
  var splitResource = resourceSplitByChat[activeChatId] || null;
  var browserTabOpen = !!(
    hasActiveBrowser
    && splitResource
    && splitResource.type === "browser"
    && browserWindowMode !== "maximized"
  );
  var conversationLoading = loading || chatLoading;
  var splitSideAgentId = sideAgentSplitByChat[activeChatId] || "";
  var splitSideAgent = sideAgents.find(function (agent) {
    return String(agent && agent.id || "") === String(splitSideAgentId);
  }) || null;
  var artifactItems = wbcChatArtifactFiles(visibleChat || selectedChatSummary);
  var splitArtifactKey = artifactSplitByChat[activeChatId] || "";
  var splitArtifactItem = artifactItems.find(function (item) {
    return wbcArtifactFileKey(item && item.file) === splitArtifactKey;
  }) || null;
  var splitArtifact = splitArtifactItem && splitArtifactItem.file;
  var splitChange = changeSplitByChat[activeChatId] || null;
  var viewerItems = artifactItems;
  var splitViewer = splitResource && splitResource.type === "viewer"
    ? (
      viewerFile && wbcArtifactFileKey(viewerFile) === String(splitResource.payload || "")
        ? viewerFile
        : (viewerItems.find(function (item) { return wbcArtifactFileKey(item && item.file) === String(splitResource.payload || ""); }) || {}).file
    )
    : null;
  var splitMap = splitResource && splitResource.type === "map" ? splitResource.payload : null;
  var splitBrowserTabId = splitResource && splitResource.type === "browser" ? String(splitResource.payload || "") : "";
  var splitSubagents = !!(splitResource && splitResource.type === "subagents");
  // Dragging a rail chat onto the right panel opens that conversation here.
  var splitChatId = splitResource && splitResource.type === "chat" ? String(splitResource.payload || "") : "";
  var splitDetailOpen = !!(splitSideAgent || splitArtifact || splitChange || splitViewer || splitMap || splitBrowserTabId || splitSubagents || splitChatId);

  useWbcEffect(function () {
    if (!splitDetailOpen) setFloatingConversationPanelOpen(false);
  }, [splitDetailOpen]);

  useWbcEffect(function () {
    setFloatingConversationPanelOpen(false);
    var snapshot = floatingSplitRestoreRef.current;
    if (!snapshot || snapshot.chatId === String(activeChatId || "")) return;
    floatingSplitRestoreRef.current = null;
    var snapshotChatId = snapshot.chatId;
    function restoreEntry(setter, value) {
      setter(function (current) {
        var updated = Object.assign({}, current);
        if (value) updated[snapshotChatId] = value;
        else delete updated[snapshotChatId];
        return updated;
      });
    }
    restoreEntry(setSideAgentSplitByChat, snapshot.sideAgentId);
    restoreEntry(setArtifactSplitByChat, snapshot.artifactKey);
    restoreEntry(setChangeSplitByChat, snapshot.change);
    restoreEntry(setResourceSplitByChat, snapshot.resource);
  }, [activeChatId]);

  // The browser page is an Electron WebContentsView, so it does not
  // participate in the renderer's grid layout. ResizeObserver normally keeps
  // it aligned, but the split track can change both the surface's left edge
  // and width in one committed grid update without producing a reliable
  // observation on every macOS/Electron frame. Publish one authoritative
  // layout pass after React has committed each split-width change. The browser
  // viewport coalesces these notifications to one bounds IPC per animation
  // frame, keeping drag resizing live without reintroducing resize flashing.
  useWbcLayoutEffect(function () {
    if (!splitDetailOpen) return undefined;
    var frame = requestAnimationFrame(function () {
      window.dispatchEvent(new CustomEvent("workbench:browser-layout", {
        detail: { source: "side-split-resize", width: sideAgentSplitWidth },
      }));
    });
    return function () { cancelAnimationFrame(frame); };
  }, [splitDetailOpen, sideAgentSplitWidth, activeChatId]);

  function setActiveBrowserWindowMode(mode) {
    var chatId = String(activeChatId || "");
    if (!chatId) return;
    setBrowserWindowModeByChat(function (prev) {
      if (prev[chatId] === mode) return prev;
      return Object.assign({}, prev, { [chatId]: mode });
    });
  }

  function renderConversationPanel(floating) {
    function openPanelSplit(openSplit) {
      if (floating) beginFloatingPanelSplit(openSplit);
      else openSplit();
    }
    return (
      <WbcSide
        project={project}
        chat={visibleChat || selectedChatSummary}
        chatLoading={chatLoading}
        chatDetailed={!!visibleChat}
        chats={chats}
        activeChatId={activeChatId}
        onSelectChat={selectChat}
        runtime={activeRuntime}
        subagentData={subagentData}
        subagentLoading={subagentLoading}
        onSelectSubagentRound={function (roundId) { loadSubagents(activeChatId, roundId); }}
        tab={sideTab}
        onTabChange={setSideTab}
        viewerFile={viewerFile}
        onOpenFile={function (file) { openPanelSplit(function () { openViewer(file); }); }}
        onSelectArtifact={function (file) { openPanelSplit(function () { selectArtifact(file); }); }}
        onSelectChange={function (change) { openPanelSplit(function () { selectChange(change); }); }}
        onSelectViewer={function (file) { openPanelSplit(function () { selectResourceSplit("viewer", wbcArtifactFileKey(file)); }); }}
        onSelectMap={function (item) { openPanelSplit(function () { selectResourceSplit("map", item); }); }}
        onSelectBrowser={function (tabId) {
          openPanelSplit(function () {
            setActiveBrowserWindowMode("pip");
            selectResourceSplit("browser", tabId);
          });
        }}
        onOpenSubagents={function () { openPanelSplit(function () { selectResourceSplit("subagents", true); }); }}
        onViewerViewed={markViewerFileRead}
        onRename={openQuickRename}
        onDelete={handleDelete}
        onToTask={handleToTask}
        toTaskBusy={toTaskBusy}
        onCompact={handleCompact}
        compactBusy={compactBusy}
        sideAgents={sideAgents}
        sideAgentsLoading={sideAgentsLoading}
        activeSideAgentId={splitSideAgentId}
        onSelectSideAgent={function (agentId) { openPanelSplit(function () { selectSideAgent(agentId); }); }}
        onUpdateSideAgent={updateSideAgent}
        onDeleteSideAgent={deleteSideAgent}
        onToggleSide={onToggleSide}
        onBrowserTakeoverComplete={function (payload) {
          var pending = activeChat && activeChat.pendingQuestion;
          if (!pending || !pending.id) return Promise.reject(new Error("登录确认已不在等待中。"));
          var takeoverQuestionId = String(payload && payload.questionId || "");
          if (takeoverQuestionId && String(pending.id || "") !== takeoverQuestionId) {
            return Promise.reject(new Error("登录确认已更新，请使用对话中的最新确认。"));
          }
          handleAnswer(pending.id, (payload && payload.text) || "我已完成登录");
          return Promise.resolve();
        }}
        browserActiveByChat={browserActiveByChat}
        browserSuppressed={browserWindowMode === "maximized"}
        floating={floating}
        onCloseFloating={function () { setFloatingConversationPanelOpen(false); }}
      />
    );
  }

  return (
    <div
      ref={pageRef}
      className={"wbc-page"
        + (sideVisible ? "" : " wbc-side-hidden")
        + (splitDetailOpen ? " side-agent-split-open" : "")
        + (splitDetailOpen && splitSide === "left" ? " wbc-split-left" : "")
        + (chatSideDropActive ? " wbc-chat-side-drop-active" : "")}
      style={splitDetailOpen ? { "--wbc-side-track-width": sideAgentSplitWidth + "px" } : undefined}
      data-active-chat-id={activeChatId || ""}
      data-project-id={projectId || ""}
    >
      {chatFileDropActive && <WorkbenchFileDropOverlay key="file-drop-overlay" label={wbcT("workbenchChat.dropToAttach", "Release to add files to the message input")} />}
      {chatDragSession && (function () {
        var zone = wbcChatSideZoneRect();
        if (!zone) return null;
        var pageRect = pageRef.current ? pageRef.current.getBoundingClientRect() : null;
        var left = pageRect ? zone.left - pageRect.left : 0;
        return (
          <div
            key="chat-side-drop-layer"
            className={"wbc-chat-side-drop-layer" + (chatSideDropActive ? " active" : "")}
            style={{ left: left + "px", width: (zone.right - zone.left) + "px" }}
            onDragOver={handleSideLayerDragOver}
            onDragLeave={handleSideLayerDragLeave}
            onDrop={handleSideLayerDrop}
          >
            {chatSideDropActive && (
              <span className="wbc-chat-side-drop-hint" role="status">
                {wbcT("workbenchChat.dropToOpenSide", "Release to open in the side panel")}
              </span>
            )}
          </div>
        );
      })()}
      {splitDetailOpen && (
        <div key="split-main-grip" className="wbc-split-main-grip">
          <WbcSplitGripBar
            side={splitSide}
            onToggleSide={toggleSplitSide}
            onClose={closeActiveSplit}
            onOpenConversationPanel={function () { setFloatingConversationPanelOpen(true); }}
            onSplitDragStart={handleSplitDragStart}
            onSplitDragEnd={handleSplitDragEnd}
          />
          {floatingConversationPanelOpen ? renderConversationPanel(true) : null}
        </div>
      )}
      <WbcRail
        projectId={projectId}
        chats={chats}
        pinnedChatIds={pinnedChatIds}
        activeChatId={activeChatId}
        loading={loading}
        runningChatIds={runtimes}
        onSelect={selectChat}
        onCreate={handleCreateChat}
        onRename={handleRenameChat}
        onDelete={handleDeleteChat}
        onToTask={handleToTask}
        toTaskBusy={toTaskBusy}
        onTogglePinned={onTogglePinnedChat}
      />
      <WbcMain
        project={project}
        chat={visibleChat}
        chatSummary={selectedChatSummary}
        loading={conversationLoading}
        runtime={activeRuntime}
        error={error}
        errorKind={errorKind}
        onRetry={errorKind === "message" ? handleRetryMessage : retryLoad}
        running={activeRunning}
        onSend={handleSend}
        onGuidance={handleGuidance}
        onInterrupt={handleInterrupt}
        onAnswer={handleAnswer}
        onRetryMessage={handleRetryMessage}
        onEditMessage={handleEditMessage}
        onAskSelection={handleAskSelection}
        sideAgentCreating={sideAgentCreating}
        onConversationContextMenu={openPageContextMenu}
        onRename={handleRename}
        onDelete={handleDelete}
        onToTask={handleToTask}
        toTaskBusy={toTaskBusy}
        onOpenFile={openViewer}
        onOpenDroppedChat={function (chatId) {
          if ((chatsRef.current || []).some(function (item) {
            return String(item && item.id || "") === String(chatId || "");
          })) selectChat(chatId);
        }}
        sideVisible={sideVisible}
        sidePanelTabExpanded={sideVisible && !!sideTab}
        onToggleSide={onToggleSide}
        splitOpen={splitDetailOpen}
        browserState={activeBrowserState}
        browserSessionId={activeChatId || ""}
        browserVisible={hasActiveBrowser && !browserTabOpen}
        browserWindowMode={browserWindowMode}
        onBrowserMinimize={function () { setActiveBrowserWindowMode("minimized"); }}
        onBrowserMaximize={function () { setActiveBrowserWindowMode("maximized"); }}
        onBrowserRestore={function () { setActiveBrowserWindowMode("pip"); }}
        onBrowserTakeoverComplete={function (payload) {
          var pending = activeChat && activeChat.pendingQuestion;
          if (!pending || !pending.id) return Promise.reject(new Error("登录确认已不在等待中。"));
          var takeoverQuestionId = String(payload && payload.questionId || "");
          if (takeoverQuestionId && String(pending.id || "") !== takeoverQuestionId) {
            return Promise.reject(new Error("登录确认已更新，请使用对话中的最新确认。"));
          }
          handleAnswer(pending.id, (payload && payload.text) || "我已完成登录");
          return Promise.resolve();
        }}
      />
      {pageContextMenu && visibleChat && (
        <div className="wb-item-context-layer wbc-page-context-layer">
          <div className="wb-item-context-scrim" onPointerDown={closePageContextMenu} />
          <div
            className="wb-item-context-menu wbc-page-context-menu"
            role="menu"
            aria-label={wbcT("workbenchChat.quickActions", "Quick actions")}
            style={{ left: pageContextMenu.left + "px", top: pageContextMenu.top + "px" }}
            onContextMenu={function (event) { event.preventDefault(); }}
          >
            <WbcQuickActionItems
              chat={visibleChat}
              menu={true}
              onBeforeAction={closePageContextMenu}
              onRename={openQuickRename}
              onDelete={handleDelete}
              onToTask={handleToTask}
              toTaskBusy={toTaskBusy}
              onCompact={handleCompact}
              compactBusy={compactBusy}
            />
          </div>
        </div>
      )}
      <WbcRenameDialog
        chat={quickRenameChat}
        onClose={function () { setQuickRenameChat(null); }}
        onRename={handleRenameChat}
      />
      {!floatingConversationPanelOpen ? renderConversationPanel(false) : null}
      <WbcSideAgentSplitHost
        agent={splitSideAgent}
        agents={sideAgents}
        width={sideAgentSplitWidth}
        project={project}
        onOpenFile={openViewer}
        onUpdate={updateSideAgent}
        onSelect={selectSideAgent}
        onResize={resizeSideAgentSplit}
        onClose={closeSideAgentSplit}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcArtifactSplitHost
        file={splitArtifact}
        items={artifactItems}
        width={sideAgentSplitWidth}
        onSelect={selectArtifact}
        onResize={resizeSideAgentSplit}
        onClose={closeArtifactSplit}
        onViewed={markViewerFileRead}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcChangeSplitHost
        change={splitChange}
        width={sideAgentSplitWidth}
        onSelect={selectChange}
        onResize={resizeSideAgentSplit}
        onClose={closeChangeSplit}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcArtifactSplitHost
        file={splitViewer}
        items={viewerItems}
        width={sideAgentSplitWidth}
        label={wbcT("workbenchChat.viewer", "Viewer")}
        onSelect={function (file) { selectResourceSplit("viewer", wbcArtifactFileKey(file)); }}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        onViewed={markViewerFileRead}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcMapSplitHost
        chatId={activeChatId}
        item={splitMap}
        width={sideAgentSplitWidth}
        onSelect={function (next) { selectResourceSplit("map", next); }}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcBrowserSplitHost
        tabId={splitBrowserTabId}
        browserState={activeBrowserState}
        browserSessionId={activeChatId || ""}
        width={sideAgentSplitWidth}
        onSelect={function (tabId) { selectResourceSplit("browser", tabId); }}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        onTakeoverComplete={function (payload) {
          var pending = activeChat && activeChat.pendingQuestion;
          if (!pending || !pending.id) return Promise.reject(new Error("登录确认已不在等待中。"));
          handleAnswer(pending.id, (payload && payload.text) || "我已完成登录");
          return Promise.resolve();
        }}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcSubagentsSplitHost
        open={splitSubagents}
        data={subagentData}
        loading={subagentLoading}
        width={sideAgentSplitWidth}
        onSelectRound={function (roundId) { loadSubagents(activeChatId, roundId); }}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcChatSplitHost
        chatId={splitChatId}
        project={project}
        width={sideAgentSplitWidth}
        onOpenFile={openViewer}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        onOpenInMain={selectChat}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Conversation rail (column 2)
// ---------------------------------------------------------------------------

function WbcRenameDialog({ chat, onClose, onRename, entity }) {
  var [draft, setDraft] = useWbcState(chat ? chat.title || "" : "");
  var [saving, setSaving] = useWbcState(false);
  var [error, setError] = useWbcState("");
  var inputRef = useWbcRef(null);
  var originalTitle = String((chat && chat.title) || "");
  var nextTitle = String(draft || "").trim();
  var canSave = !!nextTitle && nextTitle !== originalTitle && !saving;
  var isGroup = entity === "group";

  useWbcEffect(function () {
    setDraft(originalTitle);
    setError("");
    setSaving(false);
    requestAnimationFrame(function () {
      if (inputRef.current) {
        inputRef.current.focus();
        inputRef.current.select();
      }
    });
  }, [chat && chat.id]);

  function close() {
    if (!saving && onClose) onClose();
  }

  function submit(e) {
    if (e) e.preventDefault();
    if (!canSave || !chat || !onRename) return;
    setSaving(true);
    setError("");
    onRename(chat.id, nextTitle).then(function () {
      window.CyreneUI.require("feedback").showToast(
        isGroup
          ? wbcT("workbenchChat.groupRenameSuccess", "Chat group renamed")
          : wbcT("workbenchChat.renameSuccess", "Chat renamed"),
        "success"
      );
      if (onClose) onClose();
    }).catch(function (err) {
      setError(wbcErrorText(err));
      setSaving(false);
    });
  }

  if (!chat) return null;
  return (
    <div
      className="wbc-rename-scrim"
      onMouseDown={function (e) { if (e.target === e.currentTarget) close(); }}
      onKeyDown={function (e) { if (e.key === "Escape") close(); }}
    >
      <form
        className="wbc-rename-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="wbc-rename-title"
        onSubmit={submit}
      >
        <div className="wbc-rename-head">
          <strong id="wbc-rename-title">{isGroup
            ? wbcT("workbenchChat.groupRename", "Rename group")
            : wbcT("workbenchChat.rename", "Rename chat")}</strong>
          <button
            type="button"
            className="wbc-rename-close"
            aria-label={wbcT("common.close", "Close")}
            disabled={saving}
            onClick={close}
          >{WBC_ICONS.x}</button>
        </div>
        <div className="wbc-rename-body">
          <label htmlFor="wbc-rename-input">{isGroup
            ? wbcT("workbenchChat.groupTitleLabel", "Group title")
            : wbcT("workbenchChat.titleLabel", "Chat title")}</label>
          <input
            id="wbc-rename-input"
            ref={inputRef}
            value={draft}
            maxLength={60}
            disabled={saving}
            onChange={function (e) {
              setDraft(e.target.value);
              if (error) setError("");
            }}
            placeholder={isGroup
              ? wbcT("workbenchChat.groupRenamePlaceholder", "Enter a group title")
              : wbcT("workbenchChat.renamePlaceholder", "Enter a chat title")}
          />
          <div className="wbc-rename-meta">
            <span className={error ? "is-error" : ""} role={error ? "alert" : undefined}>
              {error || (!nextTitle ? wbcT("workbenchChat.renameRequired", "The title cannot be empty") : "")}
            </span>
            <span>{String(draft || "").length}/60</span>
          </div>
        </div>
        <div className="wbc-rename-foot">
          <button type="button" className="wb-btn" disabled={saving} onClick={close}>
            {wbcT("common.cancel", "Cancel")}
          </button>
          <button type="submit" className="wb-btn primary" disabled={!canSave}>
            {saving ? wbcT("common.saving", "Saving...") : wbcT("common.save", "Save")}
          </button>
        </div>
      </form>
    </div>
  );
}

function wbcOrderChatsByPinned(chats, pinnedChatIds) {
  var list = Array.isArray(chats) ? chats : [];
  var pinnedOrder = {};
  (Array.isArray(pinnedChatIds) ? pinnedChatIds : []).forEach(function (id, index) {
    pinnedOrder[String(id || "")] = index;
  });
  return list.map(function (chat, index) {
    return { chat: chat, index: index };
  }).sort(function (left, right) {
    var leftId = String(left.chat && left.chat.id || "");
    var rightId = String(right.chat && right.chat.id || "");
    var leftPinned = Object.prototype.hasOwnProperty.call(pinnedOrder, leftId);
    var rightPinned = Object.prototype.hasOwnProperty.call(pinnedOrder, rightId);
    if (leftPinned !== rightPinned) return leftPinned ? -1 : 1;
    if (leftPinned && pinnedOrder[leftId] !== pinnedOrder[rightId]) {
      return pinnedOrder[leftId] - pinnedOrder[rightId];
    }
    return left.index - right.index;
  }).map(function (entry) {
    return entry.chat;
  });
}

function WbcHoverMarquee({ text, className }) {
  var viewportRef = useWbcRef(null);
  var trackRef = useWbcRef(null);
  var [metrics, setMetrics] = useWbcState({ overflow: false, distance: 0, duration: 7 });
  var value = String(text || "");

  useWbcEffect(function () {
    function measure() {
      var viewport = viewportRef.current;
      var track = trackRef.current;
      if (!viewport || !track) return;
      var distance = Math.max(0, Math.ceil(track.scrollWidth - viewport.clientWidth));
      var next = {
        overflow: distance > 1,
        distance: distance,
        duration: Math.max(7, Math.min(18, 5.5 + (distance / 45))),
      };
      setMetrics(function (current) {
        return current.overflow === next.overflow
          && current.distance === next.distance
          && current.duration === next.duration
          ? current
          : next;
      });
    }
    measure();
    var observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    if (observer && viewportRef.current) observer.observe(viewportRef.current);
    if (observer && trackRef.current) observer.observe(trackRef.current);
    window.addEventListener("resize", measure);
    return function () {
      if (observer) observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [value]);

  return (
    <span
      ref={viewportRef}
      className={"wbc-hover-marquee" + (metrics.overflow ? " overflow" : "") + (className ? (" " + className) : "")}
      title={metrics.overflow ? value : undefined}
    >
      <span
        ref={trackRef}
        className="wbc-hover-marquee-track"
        style={{
          "--wbc-marquee-distance": metrics.distance + "px",
          "--wbc-marquee-duration": metrics.duration + "s",
        }}
      >{value}</span>
    </span>
  );
}

var WBC_CHAT_ORDER_PREFIX = "cyrene-workbench-chat-order-v1:";
var WBC_CHAT_GROUPS_PREFIX = "cyrene-workbench-chat-groups-v1:";

function wbcNormalizeChatOrder(defaultOrder, savedOrder) {
  var valid = Array.isArray(defaultOrder) ? defaultOrder.map(String) : [];
  var allowed = new Set(valid);
  var seen = new Set();
  var saved = [];
  (Array.isArray(savedOrder) ? savedOrder : []).forEach(function (id) {
    id = String(id);
    if (!allowed.has(id) || seen.has(id)) return;
    seen.add(id);
    saved.push(id);
  });
  var missing = valid.filter(function (id) { return !seen.has(id); });
  return missing.concat(saved);
}

function wbcLoadChatOrder(projectId, defaultOrder) {
  try {
    var saved = JSON.parse(localStorage.getItem(WBC_CHAT_ORDER_PREFIX + String(projectId || "")) || "null");
    return wbcNormalizeChatOrder(defaultOrder, saved);
  } catch (e) {
    return wbcNormalizeChatOrder(defaultOrder, null);
  }
}

function wbcMoveChatOrder(order, movingId, targetId, edge) {
  var current = Array.isArray(order) ? order.slice() : [];
  movingId = String(movingId || "");
  targetId = String(targetId || "");
  if (!movingId || movingId === targetId || current.indexOf(movingId) < 0 || current.indexOf(targetId) < 0) {
    return current;
  }
  var next = current.filter(function (id) { return id !== movingId; });
  var targetIndex = next.indexOf(targetId);
  next.splice(targetIndex + (edge === "after" ? 1 : 0), 0, movingId);
  return next;
}

function wbcMoveChatOrderBlock(order, movingIds, targetIds, edge) {
  var current = (Array.isArray(order) ? order : []).map(String);
  var movingSet = new Set((Array.isArray(movingIds) ? movingIds : []).map(String));
  var movingBlock = current.filter(function (id) { return movingSet.has(id); });
  if (!movingBlock.length) return current;

  var withoutMoving = current.filter(function (id) { return !movingSet.has(id); });
  var targetSet = new Set((Array.isArray(targetIds) ? targetIds : []).map(String).filter(function (id) {
    return !movingSet.has(id);
  }));
  var targetBlock = withoutMoving.filter(function (id) { return targetSet.has(id); });
  if (!targetBlock.length) {
    return edge === "before"
      ? movingBlock.concat(withoutMoving)
      : withoutMoving.concat(movingBlock);
  }

  var firstTargetIndex = withoutMoving.findIndex(function (id) { return targetSet.has(id); });
  var targetAnchor = withoutMoving.slice(0, firstTargetIndex).filter(function (id) {
    return !targetSet.has(id);
  }).length;
  var withoutEither = withoutMoving.filter(function (id) { return !targetSet.has(id); });
  withoutEither.splice.apply(withoutEither, [targetAnchor, 0].concat(targetBlock));
  var insertionIndex = targetAnchor + (edge === "after" ? targetBlock.length : 0);
  withoutEither.splice.apply(withoutEither, [insertionIndex, 0].concat(movingBlock));
  return withoutEither;
}

function wbcNormalizeChatGroups(groups, validChatIds) {
  var allowed = new Set((Array.isArray(validChatIds) ? validChatIds : []).map(String));
  var claimed = new Set();
  return (Array.isArray(groups) ? groups : []).map(function (raw, index) {
    var chatIds = [];
    var localSeen = new Set();
    (raw && Array.isArray(raw.chatIds) ? raw.chatIds : []).forEach(function (id) {
      id = String(id || "");
      if (!allowed.has(id) || claimed.has(id) || localSeen.has(id)) return;
      localSeen.add(id);
      chatIds.push(id);
    });
    if (chatIds.length >= 2) chatIds.forEach(function (id) { claimed.add(id); });
    return {
      id: String(raw && raw.id || ("group_" + index)),
      title: String(raw && raw.title || wbcT("workbenchChat.newGroup", "New chat group")).trim().slice(0, 60)
        || wbcT("workbenchChat.newGroup", "New chat group"),
      summary: String(raw && raw.summary || "").trim().slice(0, 160),
      titleLocked: !!(raw && raw.titleLocked),
      metadataLang: String(raw && raw.metadataLang || ""),
      metadataChatIds: String(raw && raw.metadataChatIds || ""),
      chatIds: chatIds,
    };
  }).filter(function (group) { return group.chatIds.length >= 2; });
}

function wbcLoadChatGroups(projectId, validChatIds) {
  try {
    var saved = JSON.parse(localStorage.getItem(WBC_CHAT_GROUPS_PREFIX + String(projectId || "")) || "null");
    return wbcNormalizeChatGroups(saved, validChatIds);
  } catch (e) {
    return [];
  }
}

function wbcFindChatGroup(groups, chatId) {
  chatId = String(chatId || "");
  return (Array.isArray(groups) ? groups : []).find(function (group) {
    return Array.isArray(group.chatIds) && group.chatIds.indexOf(chatId) >= 0;
  }) || null;
}

function wbcRemoveChatFromGroups(groups, chatId) {
  chatId = String(chatId || "");
  return (Array.isArray(groups) ? groups : []).map(function (group) {
    return {
      ...group,
      chatIds: (Array.isArray(group.chatIds) ? group.chatIds : []).filter(function (id) {
        return String(id) !== chatId;
      }),
    };
  }).filter(function (group) { return group.chatIds.length >= 2; });
}

function wbcCreateChatGroup(groups, movingId, targetId, nextGroupId) {
  movingId = String(movingId || "");
  targetId = String(targetId || "");
  var current = (Array.isArray(groups) ? groups : []).map(function (group) {
    return { ...group, chatIds: Array.isArray(group.chatIds) ? group.chatIds.slice() : [] };
  });
  if (!movingId || !targetId || movingId === targetId) return current;
  var existingTargetGroup = wbcFindChatGroup(current, targetId);
  if (existingTargetGroup && existingTargetGroup.chatIds.indexOf(movingId) >= 0) return current;

  current.forEach(function (group) {
    group.chatIds = group.chatIds.filter(function (id) { return String(id) !== movingId; });
  });
  current = current.filter(function (group) { return group.chatIds.length >= 2; });
  existingTargetGroup = wbcFindChatGroup(current, targetId);
  if (existingTargetGroup) {
    existingTargetGroup.chatIds.push(movingId);
    return current;
  }
  current.push({
    id: String(nextGroupId || ("group_" + Date.now().toString(36))),
    title: wbcT("workbenchChat.newGroup", "New chat group"),
    summary: "",
    titleLocked: false,
    metadataLang: "",
    metadataChatIds: "",
    chatIds: [targetId, movingId],
  });
  return current;
}

function wbcBuildChatRailItems(chats, groups) {
  var list = Array.isArray(chats) ? chats : [];
  var renderedGroups = new Set();
  var visibleIds = new Set(list.map(function (chat) { return String(chat && chat.id || ""); }));
  var items = [];
  list.forEach(function (chat) {
    var group = wbcFindChatGroup(groups, chat && chat.id);
    if (!group) {
      items.push({ kind: "chat", chat: chat });
      return;
    }
    if (renderedGroups.has(group.id)) return;
    renderedGroups.add(group.id);
    items.push({
      kind: "group",
      group: group,
      chats: list.filter(function (candidate) {
        return visibleIds.has(String(candidate && candidate.id || ""))
          && group.chatIds.indexOf(String(candidate && candidate.id || "")) >= 0;
      }),
    });
  });
  return items;
}

function WbcRail({ projectId, chats, pinnedChatIds, activeChatId, loading, runningChatIds, onSelect, onCreate, onRename, onDelete, onToTask, toTaskBusy, onTogglePinned }) {
  var [query, setQuery] = useWbcState("");
  var [menuId, setMenuId] = useWbcState("");
  var [renameChat, setRenameChat] = useWbcState(null);
  var [renameGroup, setRenameGroup] = useWbcState(null);
  var [collapsedGroups, setCollapsedGroups] = useWbcState({});
  var defaultChats = useWbcMemo(function () {
    return wbcOrderChatsByPinned(chats, pinnedChatIds);
  }, [chats, pinnedChatIds]);
  var defaultOrder = defaultChats.map(function (chat) { return String(chat.id); });
  var defaultOrderKey = defaultOrder.join("|");
  var [order, setOrder] = useWbcState(function () {
    return wbcLoadChatOrder(projectId, defaultOrder);
  });
  useWbcEffect(function () { orderRef.current = order; }, [order]);
  var [groups, setGroups] = useWbcState(function () {
    return wbcLoadChatGroups(projectId, defaultOrder);
  });
  var [groupBackendReady, setGroupBackendReady] = useWbcState(false);
  var [groupMetadataPending, setGroupMetadataPending] = useWbcState({});
  var [dragState, setDragState] = useWbcState(null);
  var [announcement, setAnnouncement] = useWbcState("");
  var dragOriginOrderRef = useWbcRef([]);
  var orderRef = useWbcRef(order);
  var dropCommittedRef = useWbcRef(false);
  var suppressClickRef = useWbcRef("");
  var suppressGroupClickRef = useWbcRef("");
  var groupMetadataRequestRef = useWbcRef({ sequence: 0, active: {} });
  var groupBackendLoadRef = useWbcRef(0);
  var groupBackendWriteRef = useWbcRef({ projectId: String(projectId || ""), sequence: 0, chain: Promise.resolve(), baseGroups: [] });
  var groupMetadataLang = window.CyreneUI.require("i18n").getLang();
  var chatMap = new Map((Array.isArray(chats) ? chats : []).map(function (chat) {
    return [String(chat.id), chat];
  }));

  useWbcEffect(function () {
    setOrder(wbcLoadChatOrder(projectId, defaultOrder));
    var legacyGroups = wbcLoadChatGroups(projectId, defaultOrder);
    setGroups(legacyGroups);
    setGroupBackendReady(false);
    setCollapsedGroups({});
    setGroupMetadataPending({});
    groupMetadataRequestRef.current.active = {};
    setDragState(null);
    if (loading || !String(projectId || "").trim()) return;
    groupBackendLoadRef.current += 1;
    var loadToken = groupBackendLoadRef.current;
    var backendRef = groupBackendWriteRef.current;
    backendRef.projectId = String(projectId || "");
    backendRef.sequence = 0;
    backendRef.chain = Promise.resolve();
    backendRef.baseGroups = [];
    var loadPromise = WorkbenchChatModel.listChatGroups(projectId).then(function (payload) {
      if (groupBackendLoadRef.current !== loadToken) return null;
      if (payload && payload.migrationRequired) {
        return WorkbenchChatModel.migrateChatGroups({
          projectId: projectId,
          groups: legacyGroups,
        });
      }
      return payload;
    }).then(function (payload) {
      if (!payload || groupBackendLoadRef.current !== loadToken) return;
      var authoritative = storeNormalizedGroups(payload.groups || []);
      backendRef.baseGroups = authoritative;
      setGroups(authoritative);
      setGroupBackendReady(true);
    }).catch(function () {
      // Keep the legacy/last-known browser cache for offline startup. The next
      // project load or mutation retries against the authoritative backend.
    });
    backendRef.chain = loadPromise;
    return function () {
      if (groupBackendLoadRef.current === loadToken) groupBackendLoadRef.current += 1;
    };
  }, [projectId, defaultOrderKey, loading]);

  var orderedChats = wbcNormalizeChatOrder(defaultOrder, order).map(function (id) {
    return chatMap.get(id);
  }).filter(Boolean);
  var filtered = useWbcMemo(function () {
    var q = query.trim().toLowerCase();
    return !q ? orderedChats : orderedChats.filter(function (chat) {
      var group = wbcFindChatGroup(groups, chat.id);
      return String(chat.title || "").toLowerCase().indexOf(q) !== -1
        || String(chat.preview || "").toLowerCase().indexOf(q) !== -1
        || String(group && group.title || "").toLowerCase().indexOf(q) !== -1
        || String(group && group.summary || "").toLowerCase().indexOf(q) !== -1;
    });
  }, [orderedChats, query, groups]);
  var railItems = useWbcMemo(function () {
    return wbcBuildChatRailItems(filtered, groups);
  }, [filtered, groups]);
  var groupMetadataRefreshKey = groups.map(function (group) {
    return [
      group.id,
      group.chatIds.join(","),
      group.metadataLang || "",
      group.metadataChatIds || "",
      group.summary ? "ready" : "empty",
    ].join(":");
  }).join("|");

  useWbcEffect(function () {
    if (!groupBackendReady) return;
    groups.forEach(function (group) {
      if (
        !group.summary
        || group.metadataLang !== groupMetadataLang
        || group.metadataChatIds !== group.chatIds.map(String).join("|")
      ) {
        refreshChatGroupMetadata(group);
      }
    });
  }, [projectId, groupBackendReady, groupMetadataLang, groupMetadataRefreshKey]);

  function storeNormalizedGroups(nextGroups) {
    var normalized = wbcNormalizeChatGroups(nextGroups, defaultOrder);
    try {
      localStorage.setItem(
        WBC_CHAT_GROUPS_PREFIX + String(projectId || ""),
        JSON.stringify(normalized)
      );
    } catch (e) {}
    return normalized;
  }

  function commitGroups(nextGroups, intent) {
    var normalized = storeNormalizedGroups(nextGroups);
    setGroups(normalized);
    persistGroups(normalized, intent);
    return normalized;
  }

  function persistGroups(normalized, intent) {
    var state = groupBackendWriteRef.current;
    var currentProjectId = String(projectId || "");
    if (state.projectId !== currentProjectId) {
      state.projectId = currentProjectId;
      state.sequence = 0;
      state.chain = Promise.resolve();
      state.baseGroups = [];
    }
    state.sequence += 1;
    var sequence = state.sequence;
    var desired = wbcNormalizeChatGroups(normalized, defaultOrder);
    var write = state.chain.catch(function () {}).then(function () {
      return WorkbenchChatModel.replaceChatGroups({
        projectId: currentProjectId,
        groups: desired,
        baseGroups: state.baseGroups || [],
        intent: intent || undefined,
      });
    });
    state.chain = write;
    return write.then(function (payload) {
      var live = groupBackendWriteRef.current;
      var serverGroups = wbcNormalizeChatGroups(payload.groups || [], defaultOrder);
      if (live.projectId === currentProjectId) live.baseGroups = serverGroups;
      if (
        live.projectId === currentProjectId
        && live.sequence === sequence
        && String(projectId || "") === currentProjectId
      ) {
        var authoritative = storeNormalizedGroups(serverGroups);
        setGroups(authoritative);
      }
      return payload;
    });
  }

  function refreshChatGroupMetadata(group) {
    if (!group || !Array.isArray(group.chatIds) || group.chatIds.length < 2) {
      return Promise.resolve(null);
    }
    var members = group.chatIds.map(function (chatId) {
      var chat = chatMap.get(String(chatId));
      return chat ? {
        id: String(chat.id || ""),
        title: String(chat.title || ""),
        preview: String(chat.preview || ""),
      } : null;
    }).filter(Boolean);
    if (members.length < 2) return Promise.resolve(null);
    var signature = group.chatIds.map(String).join("|");
    var requestState = groupMetadataRequestRef.current;
    requestState.sequence += 1;
    var token = requestState.sequence;
    requestState.active[group.id] = token;
    setGroupMetadataPending(function (current) { return { ...current, [group.id]: true }; });
    return groupBackendWriteRef.current.chain.catch(function () {}).then(function () {
      if (groupMetadataRequestRef.current.active[group.id] !== token) return null;
      return WorkbenchChatModel.generateChatGroupMetadata({
        projectId: projectId,
        groupId: group.id,
        signature: signature,
        members: members,
        currentTitle: group.title || "",
        titleLocked: !!group.titleLocked,
        lang: groupMetadataLang,
      });
    }).then(function (result) {
      if (!result) return null;
      var metadata = result.metadata || {};
      var persistedGroup = result.group;
      if (groupMetadataRequestRef.current.active[group.id] !== token) return null;
      setGroups(function (current) {
        var live = current.find(function (candidate) { return candidate.id === group.id; });
        if (!live || live.chatIds.map(String).join("|") !== signature) return current;
        var next = current.map(function (candidate) {
          if (candidate.id !== group.id) return candidate;
          if (
            persistedGroup
            && String(persistedGroup.id || "") === group.id
            && Array.isArray(persistedGroup.chatIds)
            && persistedGroup.chatIds.map(String).join("|") === signature
          ) {
            return {
              ...candidate,
              title: String(persistedGroup.title || candidate.title),
              summary: String(persistedGroup.summary || candidate.summary),
              titleLocked: !!persistedGroup.titleLocked,
              metadataLang: String(persistedGroup.metadataLang || metadata.lang || groupMetadataLang),
              metadataChatIds: String(persistedGroup.metadataChatIds || signature),
            };
          }
          return {
            ...candidate,
            title: candidate.titleLocked
              ? candidate.title
              : (String(metadata.title || "").trim().slice(0, 60) || candidate.title),
            summary: String(metadata.summary || "").trim().slice(0, 160) || candidate.summary,
            metadataLang: String(metadata.lang || groupMetadataLang),
            metadataChatIds: signature,
          };
        });
        var normalized = storeNormalizedGroups(next);
        if (groupBackendWriteRef.current.projectId === String(projectId || "")) {
          groupBackendWriteRef.current.baseGroups = normalized;
        }
        return normalized;
      });
      return result;
    }).catch(function () {
      return null;
    }).finally(function () {
      if (groupMetadataRequestRef.current.active[group.id] !== token) return;
      delete groupMetadataRequestRef.current.active[group.id];
      setGroupMetadataPending(function (current) {
        if (!current[group.id]) return current;
        var next = { ...current };
        delete next[group.id];
        return next;
      });
    });
  }

  function commitGroupDrop(movingId, targetId) {
    var nextOrder = wbcMoveChatOrder(dragOriginOrderRef.current, movingId, targetId, "after");
    commitOrder(nextOrder, movingId);
    var desiredGroups = wbcCreateChatGroup(
      groups,
      movingId,
      targetId,
      "group_" + Date.now().toString(36)
    );
    var created = wbcFindChatGroup(desiredGroups, targetId);
    commitGroups(desiredGroups, {
      type: "move",
      sessionId: String(movingId || ""),
      targetGroupId: created ? created.id : "",
    });
    if (created) {
      setCollapsedGroups(function (current) { return { ...current, [created.id]: false }; });
      setAnnouncement(wbcT("workbenchChat.groupCreated", "Created {title} with {count} chats.", {
        title: created.title,
        count: created.chatIds.length,
      }));
    }
  }

  function commitUngroupDrop(chatId) {
    var sourceGroup = wbcFindChatGroup(groups, chatId);
    if (!sourceGroup) return;
    commitGroups(wbcRemoveChatFromGroups(groups, chatId), {
      type: "remove_member",
      sessionId: String(chatId || ""),
    });
    setAnnouncement(wbcT("workbenchChat.removedFromGroup", "Removed {title} from {group}.", {
      title: (chatMap.get(String(chatId)) || {}).title || wbcT("workbenchChat.newChat", "New chat"),
      group: sourceGroup.title,
    }));
  }

  function renameChatGroup(groupId, title) {
    var next = groups.map(function (group) {
      return group.id === groupId ? {
        ...group,
        title: String(title || "").trim().slice(0, 60),
        titleLocked: true,
      } : group;
    });
    commitGroups(next, {
      type: "rename",
      groupId: groupId,
      title: String(title || "").trim().slice(0, 60),
    });
    return groupBackendWriteRef.current.chain;
  }

  function dissolveChatGroup(groupId) {
    var group = groups.find(function (candidate) { return candidate.id === groupId; });
    commitGroups(groups.filter(function (candidate) { return candidate.id !== groupId; }), {
      type: "dissolve",
      groupId: groupId,
    });
    setMenuId("");
    if (group) {
      setAnnouncement(wbcT("workbenchChat.groupDissolved", "Dissolved {title}.", { title: group.title }));
    }
  }

  function commitOrder(nextOrder, movedId) {
    var normalized = wbcNormalizeChatOrder(defaultOrder, nextOrder);
    var positionChanged = normalized.join("|") !== (orderRef.current || []).join("|");
    setOrder(normalized);
    try {
      localStorage.setItem(WBC_CHAT_ORDER_PREFIX + String(projectId || ""), JSON.stringify(normalized));
    } catch (e) {}
    var movedChat = chatMap.get(String(movedId || ""));
    if (movedChat && positionChanged) {
      setAnnouncement(wbcT(
        "workbenchChat.chatMoved",
        "{title} moved to position {position} of {total}.",
        {
          title: movedChat.title || wbcT("workbenchChat.newChat", "New chat"),
          position: normalized.indexOf(String(movedId)) + 1,
          total: normalized.length,
        }
      ));
    }
  }

  function commitGroupOrder(nextOrder, group) {
    commitOrder(nextOrder, "");
    if (group) {
      setAnnouncement(wbcT("workbenchChat.groupMoved", "{title} moved.", {
        title: group.title || wbcT("workbenchChat.newGroup", "New chat group"),
      }));
    }
  }

  function moveChatByKeyboard(event, id) {
    if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return false;
    var visibleOrder = filtered.map(function (chat) { return String(chat.id); });
    var index = visibleOrder.indexOf(String(id));
    var nextIndex = event.key === "ArrowUp" ? index - 1 : index + 1;
    if (index < 0 || nextIndex < 0 || nextIndex >= visibleOrder.length) return false;
    event.preventDefault();
    event.stopPropagation();
    var targetId = visibleOrder[nextIndex];
    commitOrder(wbcMoveChatOrder(
      order,
      String(id),
      targetId,
      event.key === "ArrowUp" ? "before" : "after"
    ), id);
    return true;
  }

  function updateDragState(next) {
    setDragState(function (current) {
      if (!current || !next) return next;
      var resolved = {
        ...next,
        dragKind: next.dragKind === undefined ? current.dragKind : next.dragKind,
        movingGroupId: next.movingGroupId === undefined ? current.movingGroupId : next.movingGroupId,
        movingIds: next.movingIds === undefined ? current.movingIds : next.movingIds,
        sourceGroupId: next.sourceGroupId === undefined ? current.sourceGroupId : next.sourceGroupId,
      };
      if (
        current.dragKind === resolved.dragKind
        && current.movingId === resolved.movingId
        && current.movingGroupId === resolved.movingGroupId
        && (current.movingIds || []).join("|") === (resolved.movingIds || []).join("|")
        && current.targetId === resolved.targetId
        && current.targetGroupId === resolved.targetGroupId
        && current.sourceGroupId === resolved.sourceGroupId
        && current.edge === resolved.edge
        && current.mode === resolved.mode
      ) return current;
      return resolved;
    });
  }

  function chatCanGroupWith(movingId, targetId) {
    var movingGroup = wbcFindChatGroup(groups, movingId);
    var targetGroup = wbcFindChatGroup(groups, targetId);
    return !(movingGroup && targetGroup && movingGroup.id === targetGroup.id);
  }

  function chatDropMode(event, movingId, targetId) {
    if (!chatCanGroupWith(movingId, targetId)) return "reorder";
    if (
      dragState
      && dragState.mode === "group"
      && dragState.movingId === String(movingId)
      && dragState.targetId === String(targetId)
    ) return "group";
    var rect = event.currentTarget.getBoundingClientRect();
    var ratio = rect.height ? (event.clientY - rect.top) / rect.height : 0;
    return ratio >= 0.22 && ratio <= 0.78 ? "group" : "reorder";
  }

  function renderDropClone(chat) {
    if (!chat) return null;
    var chatRunning = !!(runningChatIds && runningChatIds[chat.id]) || chat.status === "running";
    return (
      <div className="wbc-chat-card wbc-chat-group-drop-clone" aria-hidden="true">
        <span className="wbc-chat-card-top">
          <span className="wbc-chat-card-title">
            <b><WbcHoverMarquee text={chat.title || wbcT("workbenchChat.newChat", "New chat")} /></b>
          </span>
          <time className="wbc-chat-card-time">{wbcFormatTime(chat.updatedAt || chat.createdAt)}</time>
        </span>
        <span className="wbc-chat-card-preview">
          {chatRunning ? <i className="wbc-running-dot" /> : null}
          <WbcHoverMarquee text={chat.preview || wbcT("workbenchChat.noMessages", "No messages yet")} />
        </span>
      </div>
    );
  }

  function renderChatCard(chat, options) {
    options = options || {};
    var active = chat.id === activeChatId;
    var chatRunning = !!(runningChatIds && runningChatIds[chat.id]) || chat.status === "running";
    var isMenuOpen = menuId === chat.id;
    var isPinned = (Array.isArray(pinnedChatIds) ? pinnedChatIds : []).some(function (id) {
      return String(id || "") === String(chat.id || "");
    });
    var isDragging = dragState && dragState.movingId === String(chat.id);
    var isGroupTarget = dragState && dragState.mode === "group" && dragState.targetId === String(chat.id);
    return (
      <div
        key={chat.id}
        data-chat-id={String(chat.id)}
        role="button"
        tabIndex={0}
        draggable="true"
        className={"wbc-chat-card"
          + (options.insideGroup ? " wbc-chat-group-child" : "")
          + (active ? " active" : "")
          + (isMenuOpen ? " menu-open" : "")
          + (isDragging ? " dragging" : "")
          + (isGroupTarget ? " group-drop-target" : "")}
        title={wbcT("workbenchChat.dragChat", "Drag to reorder, overlap another chat to group, or drop in the conversation area to open {title}.", {
          title: chat.title || wbcT("workbenchChat.newChat", "New chat"),
        })}
        onClick={function () {
          if (suppressClickRef.current === String(chat.id)) return;
          setMenuId("");
          onSelect(chat.id);
        }}
        onContextMenu={function (event) {
          event.preventDefault();
          event.stopPropagation();
          setMenuId(chat.id);
        }}
        onKeyDown={function (e) {
          if (moveChatByKeyboard(e, chat.id)) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(chat.id);
          }
        }}
        onDragStart={function (event) {
          if (event.target && event.target.closest && event.target.closest("button")) {
            event.preventDefault();
            return;
          }
          var id = String(chat.id);
          dragOriginOrderRef.current = order.slice();
          dropCommittedRef.current = false;
          suppressClickRef.current = id;
          setMenuId("");
          wbcSetChatDrag(event, chat);
          var cardRect = event.currentTarget.getBoundingClientRect();
          if (event.dataTransfer) {
            event.dataTransfer.setDragImage(
              event.currentTarget,
              Math.max(0, Math.min(cardRect.width, event.clientX - cardRect.left)),
              Math.max(0, Math.min(cardRect.height, event.clientY - cardRect.top))
            );
          }
          var sourceGroup = wbcFindChatGroup(groups, id);
          setDragState({
            dragKind: "chat",
            movingId: id,
            movingGroupId: "",
            movingIds: [id],
            targetId: "",
            targetGroupId: "",
            sourceGroupId: sourceGroup ? sourceGroup.id : "",
            edge: "before",
            mode: "reorder",
          });
        }}
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          if (dragState.dragKind === "group") {
            if ((dragState.movingIds || []).indexOf(String(chat.id)) >= 0) return;
            event.preventDefault();
            event.stopPropagation();
            if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
            var railGroupTarget = wbcFindChatGroup(groups, chat.id);
            if (railGroupTarget && railGroupTarget.id === dragState.movingGroupId) return;
            var railTarget = railGroupTarget
              ? event.currentTarget.closest(".wbc-chat-group") || event.currentTarget
              : event.currentTarget;
            var railRect = railTarget.getBoundingClientRect();
            var railEdge = event.clientY < railRect.top + (railRect.height / 2) ? "before" : "after";
            var railTargetIds = railGroupTarget ? railGroupTarget.chatIds : [String(chat.id)];
            var groupOrder = wbcMoveChatOrderBlock(order, dragState.movingIds, railTargetIds, railEdge);
            if (groupOrder.join("|") !== order.join("|")) setOrder(groupOrder);
            updateDragState({
              movingId: dragState.movingId,
              targetId: String(chat.id),
              targetGroupId: railGroupTarget ? railGroupTarget.id : "",
              edge: railEdge,
              mode: "group-reorder",
            });
            return;
          }
          if (dragState.movingId === String(chat.id) || !wbcHasChatDrag(event)) return;
          event.preventDefault();
          event.stopPropagation();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          var mode = chatDropMode(event, dragState.movingId, String(chat.id));
          var targetGroup = wbcFindChatGroup(groups, chat.id);
          if (mode === "group") {
            if (order.join("|") !== dragOriginOrderRef.current.join("|")) {
              setOrder(dragOriginOrderRef.current.slice());
            }
            updateDragState({
              movingId: dragState.movingId,
              targetId: String(chat.id),
              targetGroupId: targetGroup ? targetGroup.id : "",
              edge: "center",
              mode: "group",
            });
            return;
          }
          var rect = event.currentTarget.getBoundingClientRect();
          var edge = event.clientY < rect.top + (rect.height / 2) ? "before" : "after";
          var nextOrder = wbcMoveChatOrder(order, dragState.movingId, String(chat.id), edge);
          if (nextOrder.join("|") !== order.join("|")) setOrder(nextOrder);
          updateDragState({ movingId: dragState.movingId, targetId: String(chat.id), targetGroupId: "", edge: edge, mode: "reorder" });
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          event.preventDefault();
          event.stopPropagation();
          if (dragState.dragKind === "group") {
            var droppedGroup = groups.find(function (candidate) {
              return candidate.id === dragState.movingGroupId;
            });
            dropCommittedRef.current = true;
            commitGroupOrder(order, droppedGroup);
            setDragState(null);
            return;
          }
          if (!wbcHasChatDrag(event)) return;
          var mode = chatDropMode(event, dragState.movingId, String(chat.id));
          dropCommittedRef.current = true;
          if (mode === "group") {
            commitGroupDrop(dragState.movingId, String(chat.id));
          } else {
            var nextOrder = dragState.movingId === String(chat.id)
              ? order
              : wbcMoveChatOrder(order, dragState.movingId, String(chat.id), dragState.edge);
            commitOrder(nextOrder, dragState.movingId);
            var reorderTargetGroup = wbcFindChatGroup(groups, chat.id);
            if (
              dragState.sourceGroupId
              && (!reorderTargetGroup || reorderTargetGroup.id !== dragState.sourceGroupId)
            ) commitUngroupDrop(dragState.movingId);
          }
          setDragState(null);
        }}
        onDragEnd={function () {
          if (!dropCommittedRef.current) setOrder(dragOriginOrderRef.current);
          dropCommittedRef.current = false;
          setDragState(null);
          window.setTimeout(function () {
            if (suppressClickRef.current === String(chat.id)) suppressClickRef.current = "";
          }, 0);
        }}
      >
        <span className="wbc-chat-card-top">
          <span className="wbc-chat-card-title">
            <b><WbcHoverMarquee text={chat.title || wbcT("workbenchChat.newChat", "New chat")} /></b>
            {isPinned ? (
              <span className="wbc-chat-card-pin" title={wbcT("workbenchChat.pinned", "Pinned")} aria-label={wbcT("workbenchChat.pinned", "Pinned")}>
                {WBC_ICONS.pin}
              </span>
            ) : null}
            {chat.forkedFromChatId && (
              <span
                className="wbc-fork-marker"
                title={wbcT("workbenchChat.forkSource", "Forked from another chat — click to open the original")}
                onClick={function (e) { e.stopPropagation(); onSelect(chat.forkedFromChatId); }}
              >
                {WBC_ICONS.fork}
                {wbcT("workbenchChat.forked", "Forked")}
              </span>
            )}
          </span>
          <span className="wbc-chat-card-right">
            <time className="wbc-chat-card-time">{wbcFormatTime(chat.updatedAt || chat.createdAt)}</time>
            <span className="wbc-chat-card-actions">
              <button
                type="button"
                className="wb-card-menu-btn"
                title={wbcT("common.moreActions", "More actions")}
                onClick={function (e) { e.stopPropagation(); setMenuId(isMenuOpen ? "" : chat.id); }}
              >
                {WBC_ICONS.dots}
              </button>
              {isMenuOpen && (
                <div className="wb-card-menu" role="menu">
                  <button type="button" role="menuitem" className="wbc-chat-pin-action" onClick={function (e) {
                    e.stopPropagation();
                    setMenuId("");
                    if (onTogglePinned) onTogglePinned(chat, !isPinned);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.pin}</span>
                    <span>{isPinned
                      ? wbcT("workbenchChat.unpin", "Unpin chat")
                      : wbcT("workbenchChat.pin", "Pin chat")}</span>
                  </button>
                  {!chat.legacy && (
                    <button type="button" role="menuitem" className="wbc-chat-menu-action" onClick={function (e) {
                      e.stopPropagation();
                      setMenuId("");
                      setRenameChat(chat);
                    }}>
                      <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span>
                      <span>{wbcT("workbenchChat.rename", "Rename chat")}</span>
                    </button>
                  )}
                  <button type="button" role="menuitem" className="wbc-chat-menu-action" disabled={toTaskBusy} onClick={function (e) {
                    e.stopPropagation();
                    setMenuId("");
                    if (onToTask) onToTask(chat.id);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.task}</span>
                    <span>{wbcT(toTaskBusy ? "workbenchChat.toTaskBusy" : "workbenchChat.toTask", toTaskBusy ? "Analyzing chat…" : "Convert to task")}</span>
                  </button>
                  <button type="button" role="menuitem" className="wbc-chat-menu-action danger" onClick={function (e) {
                    e.stopPropagation();
                    setMenuId("");
                    onDelete && onDelete(chat.id);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.trash}</span>
                    <span>{wbcT("workbenchChat.delete", "Delete chat")}</span>
                  </button>
                </div>
              )}
            </span>
          </span>
        </span>
        <span className="wbc-chat-card-preview">
          {chatRunning ? <i className="wbc-running-dot" /> : null}
          <WbcHoverMarquee text={chat.preview || wbcT("workbenchChat.noMessages", "No messages yet")} />
        </span>
      </div>
    );
  }

  function renderGroupFrame(group, groupChats) {
    var isCollapsed = !!collapsedGroups[group.id];
    var groupMenuId = "group:" + group.id;
    var isMenuOpen = menuId === groupMenuId;
    var isGroupDragging = !!(
      dragState
      && dragState.dragKind === "group"
      && dragState.movingGroupId === group.id
    );
    function openGroupMenu(event) {
      event.preventDefault();
      event.stopPropagation();
      setMenuId(groupMenuId);
    }
    function toggleGroupMenu(event) {
      event.stopPropagation();
      setMenuId(isMenuOpen ? "" : groupMenuId);
    }
    var movingChat = dragState && chatMap.get(String(dragState.movingId));
    var groupDropReady = !!(
      dragState
      && dragState.dragKind !== "group"
      && dragState.mode === "group"
      && (dragState.targetGroupId === group.id || group.chatIds.indexOf(String(dragState.targetId)) >= 0)
      && group.chatIds.indexOf(String(dragState.movingId)) < 0
    );
    return (
      <section
        key={group.id}
        className={"wbc-chat-group" + (isCollapsed ? " collapsed" : " expanded") + (groupDropReady ? " drop-ready" : "") + (isMenuOpen ? " menu-open" : "") + (isGroupDragging ? " dragging" : "")}
        onContextMenu={openGroupMenu}
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          if (event.target && event.target.closest && event.target.closest(".wbc-chat-card")) return;
          if (dragState.dragKind === "group") {
            if (dragState.movingGroupId === group.id) return;
            event.preventDefault();
            event.stopPropagation();
            if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
            var groupRect = event.currentTarget.getBoundingClientRect();
            var groupEdge = event.clientY < groupRect.top + (groupRect.height / 2) ? "before" : "after";
            var groupOrder = wbcMoveChatOrderBlock(order, dragState.movingIds, group.chatIds, groupEdge);
            if (groupOrder.join("|") !== order.join("|")) setOrder(groupOrder);
            updateDragState({
              movingId: dragState.movingId,
              targetId: String(group.chatIds[0] || ""),
              targetGroupId: group.id,
              edge: groupEdge,
              mode: "group-reorder",
            });
            return;
          }
          if (!wbcHasChatDrag(event) || group.chatIds.indexOf(String(dragState.movingId)) >= 0) return;
          event.preventDefault();
          event.stopPropagation();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          if (order.join("|") !== dragOriginOrderRef.current.join("|")) setOrder(dragOriginOrderRef.current.slice());
          updateDragState({
            movingId: dragState.movingId,
            targetId: String(group.chatIds[0] || ""),
            targetGroupId: group.id,
            edge: "center",
            mode: "group",
          });
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          if (dragState.dragKind === "group") {
            if (dragState.movingGroupId === group.id) return;
            event.preventDefault();
            event.stopPropagation();
            var reorderedGroup = groups.find(function (candidate) {
              return candidate.id === dragState.movingGroupId;
            });
            dropCommittedRef.current = true;
            commitGroupOrder(order, reorderedGroup);
            setDragState(null);
            return;
          }
          if (!wbcHasChatDrag(event) || group.chatIds.indexOf(String(dragState.movingId)) >= 0) return;
          event.preventDefault();
          event.stopPropagation();
          dropCommittedRef.current = true;
          commitGroupDrop(dragState.movingId, String(group.chatIds[group.chatIds.length - 1] || group.chatIds[0]));
          setDragState(null);
        }}
      >
        <header className="wbc-chat-group-head">
          <button
            type="button"
            className="wbc-chat-group-toggle"
            draggable="true"
            title={wbcT("workbenchChat.dragGroup", "Drag to move {title}.", { title: group.title })}
            onClick={function () {
              if (suppressGroupClickRef.current === group.id) return;
              setCollapsedGroups(function (current) { return { ...current, [group.id]: !isCollapsed }; });
            }}
            onDragStart={function (event) {
              dragOriginOrderRef.current = order.slice();
              dropCommittedRef.current = false;
              suppressGroupClickRef.current = group.id;
              setMenuId("");
              wbcSetChatGroupDrag(event, group, projectId);
              var frame = event.currentTarget.closest(".wbc-chat-group") || event.currentTarget;
              var frameRect = frame.getBoundingClientRect();
              if (event.dataTransfer) {
                event.dataTransfer.setDragImage(
                  frame,
                  Math.max(0, Math.min(frameRect.width, event.clientX - frameRect.left)),
                  Math.max(0, Math.min(frameRect.height, event.clientY - frameRect.top))
                );
              }
              setDragState({
                dragKind: "group",
                movingId: String(group.chatIds[0] || ""),
                movingGroupId: group.id,
                movingIds: group.chatIds.map(String),
                targetId: "",
                targetGroupId: "",
                sourceGroupId: group.id,
                edge: "before",
                mode: "group-reorder",
              });
            }}
            onDragEnd={function () {
              if (!dropCommittedRef.current) setOrder(dragOriginOrderRef.current);
              dropCommittedRef.current = false;
              setDragState(null);
              window.setTimeout(function () {
                if (suppressGroupClickRef.current === group.id) suppressGroupClickRef.current = "";
              }, 0);
            }}
            aria-expanded={!isCollapsed}
          >
            <span className="wbc-chat-group-icon" aria-hidden="true">
              {group.chatIds.length + (groupDropReady ? 1 : 0)}
            </span>
            <b><WbcHoverMarquee text={group.title} /></b>
          </button>
          <span className="wbc-chat-group-actions">
            <button
              type="button"
              className="wb-card-menu-btn"
              title={wbcT("common.moreActions", "More actions")}
              onClick={toggleGroupMenu}
            >{WBC_ICONS.dots}</button>
            {isMenuOpen && (
              <div className="wb-card-menu" role="menu">
                <button type="button" role="menuitem" onClick={function (event) {
                  event.stopPropagation();
                  setMenuId("");
                  setRenameGroup(group);
                }}>
                  <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span>
                  <span>{wbcT("workbenchChat.groupRename", "Rename group")}</span>
                </button>
                <button type="button" role="menuitem" className="danger" onClick={function (event) {
                  event.stopPropagation();
                  dissolveChatGroup(group.id);
                }}>
                  <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.x}</span>
                  <span>{wbcT("workbenchChat.groupDissolve", "Dissolve group")}</span>
                </button>
              </div>
            )}
            <button
              type="button"
              className={"wbc-chat-group-chevron" + (!isCollapsed ? " expanded" : "")}
              aria-label={isCollapsed
                ? wbcT("workbenchChat.groupExpand", "Expand group")
                : wbcT("workbenchChat.groupCollapse", "Collapse group")}
              onClick={function () {
                setCollapsedGroups(function (current) { return { ...current, [group.id]: !isCollapsed }; });
              }}
            >{WBC_ICONS.chevronRight}</button>
          </span>
        </header>
        <div className={"wbc-chat-group-summary" + (groupMetadataPending[group.id] ? " is-updating" : "")}>
          <WbcHoverMarquee text={group.summary || (groupMetadataPending[group.id]
            ? wbcT("workbenchChat.groupSummaryGenerating", "Generating summary…")
            : groupChats.map(function (chat) { return chat.title; }).join(" · "))} />
        </div>
        <div
          className={"wbc-chat-group-content" + (isCollapsed ? " collapsed" : " expanded")}
          aria-hidden={isCollapsed}
          inert={isCollapsed ? "" : undefined}
        >
          <div className="wbc-chat-group-content-inner">
            <div className="wbc-chat-group-children">
              {groupChats.map(function (chat) { return renderChatCard(chat, { insideGroup: true }); })}
              {groupDropReady ? renderDropClone(movingChat) : null}
            </div>
          </div>
        </div>
        {groupDropReady && (
          <span className="wbc-chat-group-drop-hint">{WBC_ICONS.copy}{wbcT("workbenchChat.releaseToExistingGroup", "Release to add to this chat group")}</span>
        )}
      </section>
    );
  }

  return (
    <aside className="wbc-rail">
      <div className="wbc-rail-glass">
        <div className="wbc-rail-toolbar">
          <div className="wbc-search">
            <span className="wbc-search-icon">{WBC_ICONS.search}</span>
            <input
              value={query}
              onChange={function (e) { setQuery(e.target.value); }}
              placeholder={wbcT("workbenchChat.search", "Search chats...")}
            />
          </div>
          <button
            type="button"
            className="workbench-icon-btn wbc-new-chat-btn"
            onClick={onCreate}
            title={wbcT("workbenchChat.newChat", "New chat")}
            aria-label={wbcT("workbenchChat.newChat", "New chat")}
          >
            {WBC_ICONS.plus}
          </button>
        </div>
      </div>
      {menuId && <div className="wb-card-menu-scrim" onClick={function () { setMenuId(""); }} />}
      <div
        className={"wbc-chat-list" + (loading ? " is-loading" : "") + (menuId ? " menu-active" : "")}
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          event.preventDefault();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          if (event.target === event.currentTarget) {
            if (dragState.dragKind === "group") {
              var trailingOrder = wbcMoveChatOrderBlock(order, dragState.movingIds, [], "after");
              if (trailingOrder.join("|") !== order.join("|")) setOrder(trailingOrder);
            }
            updateDragState({
              movingId: dragState.movingId,
              targetId: "",
              targetGroupId: "",
              edge: "after",
              mode: dragState.dragKind === "group" ? "group-reorder" : "reorder",
            });
          }
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          event.preventDefault();
          dropCommittedRef.current = true;
          if (dragState.dragKind === "group") {
            commitGroupOrder(order, groups.find(function (candidate) {
              return candidate.id === dragState.movingGroupId;
            }));
            setDragState(null);
            return;
          }
          commitOrder(order, dragState.movingId);
          if (dragState.sourceGroupId) commitUngroupDrop(dragState.movingId);
          setDragState(null);
        }}
      >
        {loading && (
          <div className="workbench-muted wbc-rail-loading" role="status">
            {wbcT("workbenchChat.loading", "Loading chats...")}
          </div>
        )}
        {!loading && filtered.length === 0 && (
          <div className="workbench-muted">{query ? wbcT("workbenchChat.noMatches", "No matching chats.") : wbcT("workbenchChat.emptyRail", "No chats yet. Create one from the top right.")}</div>
        )}
        {!loading && railItems.map(function (item) {
          if (item.kind === "group") return renderGroupFrame(item.group, item.chats);
          var chat = item.chat;
          var isNewGroupTarget = !!(
            dragState
            && dragState.mode === "group"
            && dragState.targetId === String(chat.id)
            && !wbcFindChatGroup(groups, chat.id)
          );
          if (!isNewGroupTarget) return renderChatCard(chat);
          var movingChat = chatMap.get(String(dragState.movingId));
          return (
            <section
              key={"drop-group:" + chat.id}
              className="wbc-chat-group wbc-chat-group-preview drop-ready"
              onDragOver={function (event) {
                if (!dragState || !wbcHasChatDrag(event)) return;
                event.preventDefault();
                event.stopPropagation();
                if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
                updateDragState({
                  movingId: dragState.movingId,
                  targetId: String(chat.id),
                  targetGroupId: "",
                  edge: "center",
                  mode: "group",
                });
              }}
              onDrop={function (event) {
                if (!dragState || !wbcHasChatDrag(event)) return;
                event.preventDefault();
                event.stopPropagation();
                dropCommittedRef.current = true;
                commitGroupDrop(dragState.movingId, String(chat.id));
                setDragState(null);
              }}
            >
              <header className="wbc-chat-group-head">
                <span className="wbc-chat-group-toggle">
                  <span className="wbc-chat-group-icon" aria-hidden="true">2</span>
                  <b>{wbcT("workbenchChat.newGroup", "New chat group")}</b>
                </span>
              </header>
              <div className="wbc-chat-group-children">
                {renderChatCard(chat, { insideGroup: true })}
                {renderDropClone(movingChat)}
              </div>
              <span className="wbc-chat-group-drop-hint">{WBC_ICONS.copy}{wbcT("workbenchChat.releaseToGroup", "Release to create a chat group")}</span>
            </section>
          );
        })}
        <span className="wbc-sr-only" aria-live="polite">{announcement}</span>
      </div>
      <WbcRenameDialog
        chat={renameChat}
        onClose={function () { setRenameChat(null); }}
        onRename={onRename}
      />
      <WbcRenameDialog
        chat={renameGroup}
        entity="group"
        onClose={function () { setRenameGroup(null); }}
        onRename={renameChatGroup}
      />
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Conversation main (column 3)
// ---------------------------------------------------------------------------

function WbcBrowserFloatingSurface({ browserState, browserSessionId, visible, mode, runtime, running, latestAssistantReplyId, latestAssistantReplyText, onSend, onGuidance, onInterrupt, onMinimize, onMaximize, onRestore, onTakeoverComplete }) {
  var shellRef = useWbcRef(null);
  var minimizedRef = useWbcRef(null);
  var frameRef = useWbcRef(null);
  var frameSessionRef = useWbcRef("");
  var previousVisibleRef = useWbcRef(visible);
  var pipRestoreGuardUntilRef = useWbcRef(0);
  var pipRestoreTimerRef = useWbcRef(null);
  var minimizedFrameRef = useWbcRef(null);
  var interactionRef = useWbcRef(null);
  var minimizedDragRef = useWbcRef(null);
  var suppressMinimizedClickRef = useWbcRef(false);
  var modeTransitionRafRef = useWbcRef(0);
  var modeTransitionTimerRef = useWbcRef(null);
  var modeTransitionReadyHandlerRef = useWbcRef(null);
  var [frame, setFrame] = useWbcState(null);
  var [minimizedFrame, setMinimizedFrame] = useWbcState(null);
  var [nativeBrowserState, setNativeBrowserState] = useWbcState(null);
  var [fullscreenDraft, setFullscreenDraft] = useWbcState("");
  var [fullscreenStatusRequested, setFullscreenStatusRequested] = useWbcState(false);
  var [fullscreenSubmitting, setFullscreenSubmitting] = useWbcState(false);
  var [fullscreenFinalReply, setFullscreenFinalReply] = useWbcState("");
  var [maximizedPickerOpen, setMaximizedPickerOpen] = useWbcState(false);
  var [chatOverlayThemeRevision, setChatOverlayThemeRevision] = useWbcState(0);
  var fullscreenFinalReplyTimerRef = useWbcRef(null);
  var fullscreenReplyBaselineRef = useWbcRef("");
  var effectiveMode = mode || "pip";
  var displayBrowserState = nativeBrowserState || browserState || {};
  var displayBrowserTabs = Array.isArray(displayBrowserState.tabs) ? displayBrowserState.tabs : [];
  var displayActiveBrowserTab = displayBrowserState.activeTab || displayBrowserTabs.find(function (tab) {
    return String(tab && tab.id || "") === String(displayBrowserState.activeTabId || "");
  }) || displayBrowserTabs[0] || {};
  var displayBrowserFavicon = String(displayActiveBrowserTab.favicon || "");
  var hasNoBrowserTabs = Array.isArray(displayBrowserState.tabs) && displayBrowserState.tabs.length === 0;
  var browserBridge = window.cyrene && window.cyrene.browser;
  var FloatingBrowserIcon = window.CyreneUI.require("browser").Icon;
  var hasNativeChatOverlay = !!(browserBridge && typeof browserBridge.setChatOverlay === "function");
  var fullscreenSavedReply = !running && !fullscreenSubmitting
    && String(latestAssistantReplyId || "")
    && String(latestAssistantReplyId || "") !== fullscreenReplyBaselineRef.current
    ? String(latestAssistantReplyText || "").replace(/\s+/g, " ").trim()
    : "";
  var fullscreenCompletedReply = fullscreenFinalReply || fullscreenSavedReply;
  var fullscreenStatusVisible = effectiveMode === "maximized"
    && fullscreenStatusRequested
    && (!!running || fullscreenSubmitting || !!fullscreenCompletedReply);
  var fullscreenStatusText = fullscreenCompletedReply || wbcBrowserFullscreenStatusText(runtime);

  function browserDragPayload() {
    return {
      kind: "browser",
      ownerSessionId: String(browserSessionId || ""),
      stableRef: String(browserSessionId || ""),
      title: String(displayActiveBrowserTab.title || wbcT("workbenchChat.browserWindowTitle", "Browser")),
      url: String(displayActiveBrowserTab.url || ""),
      tabId: String(displayActiveBrowserTab.id || displayBrowserState.activeTabId || ""),
      favicon: displayBrowserFavicon,
    };
  }

  function updateResourceShelfTarget(interaction, clientX, clientY) {
    if (!interaction || interaction.kind !== "drag") return false;
    interaction.lastClientX = clientX;
    interaction.lastClientY = clientY;
    var overShelf = wbcPointInsideResourceShelf(clientX, clientY);
    if (interaction.overShelf !== overShelf) {
      interaction.overShelf = overShelf;
      wbcNotifyResourceShelfPointerDrag(overShelf);
    }
    var conversationTarget = overShelf
      ? null
      : wbcConversationTabAtPoint(clientX, clientY, browserSessionId);
    var nextNode = conversationTarget && conversationTarget.node;
    if (interaction.targetChatNode !== nextNode) {
      if (interaction.targetChatNode) interaction.targetChatNode.classList.remove("resource-drop-target");
      interaction.targetChatNode = nextNode || null;
      if (interaction.targetChatNode) interaction.targetChatNode.classList.add("resource-drop-target");
    }
    interaction.targetChatId = conversationTarget ? conversationTarget.chatId : "";
    if (interaction.ghost) {
      interaction.ghost.classList.toggle("drop-ready", overShelf || !!interaction.targetChatId);
    }
    return overShelf || !!interaction.targetChatId;
  }

  function clearBrowserPointerDropTarget(interaction) {
    if (interaction && interaction.targetChatNode) {
      interaction.targetChatNode.classList.remove("resource-drop-target");
      interaction.targetChatNode = null;
      interaction.targetChatId = "";
    }
    wbcNotifyResourceShelfPointerDrag(false);
  }

  function pinBrowserFromPointerInteraction(interaction) {
    if (!interaction || interaction.pinned || interaction.delivered) return false;
    if (interaction.targetChatId) {
      interaction.delivered = true;
      try {
        window.dispatchEvent(new CustomEvent("cyrene:copy-browser-to-chat", {
          detail: {
            targetChatId: interaction.targetChatId,
            resource: browserDragPayload(),
          },
        }));
      } catch (e) {}
      return true;
    }
    if (!interaction.overShelf) return false;
    interaction.pinned = true;
    try {
      window.dispatchEvent(new CustomEvent("cyrene:pin-topbar-resource", {
        detail: browserDragPayload(),
      }));
    } catch (e) {}
    return true;
  }

  function clearFullscreenFinalReplyTimer() {
    if (!fullscreenFinalReplyTimerRef.current) return;
    clearTimeout(fullscreenFinalReplyTimerRef.current);
    fullscreenFinalReplyTimerRef.current = null;
  }

  function sendFullscreenChatText(value) {
    var text = String(value || "").trim();
    if (!text) return;
    var wasRunning = !!running;
    clearFullscreenFinalReplyTimer();
    fullscreenReplyBaselineRef.current = String(latestAssistantReplyId || "");
    setFullscreenFinalReply("");
    setFullscreenStatusRequested(true);
    setFullscreenSubmitting(true);
    var request;
    try {
      request = wasRunning && onGuidance
        ? onGuidance(text)
        : (onSend ? onSend({ message: text, attachments: [], command: "" }) : null);
    } catch (error) {
      if (!hasNativeChatOverlay) setFullscreenDraft(text);
      setFullscreenStatusRequested(false);
      setFullscreenSubmitting(false);
      return;
    }
    Promise.resolve(request).catch(function () {
      if (!hasNativeChatOverlay) setFullscreenDraft(text);
      setFullscreenStatusRequested(false);
    }).finally(function () {
      setFullscreenSubmitting(false);
    });
  }

  function submitFullscreenChat(event) {
    if (event) event.preventDefault();
    var text = String(fullscreenDraft || "").trim();
    if (!text) {
      if (running && onInterrupt) onInterrupt();
      return;
    }
    setFullscreenDraft("");
    sendFullscreenChatText(text);
  }

  function cancelModeTransition() {
    if (modeTransitionReadyHandlerRef.current) {
      window.removeEventListener("workbench:browser-transition-target-ready", modeTransitionReadyHandlerRef.current);
      modeTransitionReadyHandlerRef.current = null;
    }
    if (modeTransitionRafRef.current) {
      cancelAnimationFrame(modeTransitionRafRef.current);
      modeTransitionRafRef.current = 0;
    }
    if (modeTransitionTimerRef.current) {
      clearTimeout(modeTransitionTimerRef.current);
      modeTransitionTimerRef.current = null;
    }
  }

  function measureBrowserSurfaceForMode(targetMode) {
    var shell = shellRef.current;
    var host = targetMode === "pip"
      ? document.querySelector(".wbc-browser-movement-region")
      : shell && shell.parentElement;
    if (!shell || !host) return null;
    // The maximized shell is viewport-fixed. Measure its preview under body so
    // it uses the same containing block as the committed shell after the
    // conversation stage releases its transform promotion.
    var measurementHost = targetMode === "maximized" ? document.body : host;
    var clone = shell.cloneNode(true);
    clone.className = "wbc-browser-window " + targetMode;
    clone.removeAttribute("style");
    clone.setAttribute("aria-hidden", "true");
    clone.style.visibility = "hidden";
    clone.style.pointerEvents = "none";
    clone.style.transition = "none";
    if (targetMode === "pip" && frameRef.current) {
      var saved = frameRef.current;
      clone.style.left = saved.x + "px";
      clone.style.top = saved.y + "px";
      clone.style.width = saved.width + "px";
      clone.style.height = saved.height + "px";
      clone.style.right = "auto";
      clone.style.bottom = "auto";
    }
    measurementHost.appendChild(clone);
    var surface = clone.querySelector(".browser-native-surface");
    var rect = surface && surface.getBoundingClientRect();
    clone.remove();
    if (!rect || rect.width <= 8 || rect.height <= 8) return null;
    return {
      x: rect.left,
      y: rect.top,
      width: rect.width,
      height: rect.height,
      borderRadius: 0,
      pageCornerRadius: targetMode === "pip" ? 8 : 0,
    };
  }

  function runModeTransition(action, targetMode) {
    if (!action) return;
    cancelModeTransition();

    // Restoring from the full browser back to PiP does not need a screenshot
    // preflight: both surfaces display the same live WebContentsView. Commit
    // the React mode and hand Electron the already measurable PiP rectangle in
    // the same frame so the page is visible immediately instead of waiting for
    // the transition-preview timeout.
    if (targetMode === "pip" && effectiveMode === "maximized") {
      var restoreBounds = measureBrowserSurfaceForMode("pip");
      var commitRestore = function () { action(); };
      if (window.ReactDOM && typeof window.ReactDOM.flushSync === "function") {
        window.ReactDOM.flushSync(commitRestore);
      } else {
        commitRestore();
      }
      if (restoreBounds && browserBridge && typeof browserBridge.setBounds === "function") {
        browserBridge.setBounds({
          ...restoreBounds,
          sessionId: browserSessionId,
          visible: true,
          forceVisible: true,
          zoomEnabled: true,
        }).catch(function () {});
      }
      modeTransitionRafRef.current = requestAnimationFrame(function () {
        modeTransitionRafRef.current = 0;
        wbcNotifyBrowserLayoutChanged();
        wbcNotifyBrowserWindowInteraction(false, "mode", browserSessionId);
      });
      return;
    }

    var started = false;
    function applyModeAfterPreview() {
      if (started) return;
      started = true;
      if (modeTransitionReadyHandlerRef.current) {
        window.removeEventListener("workbench:browser-transition-target-ready", modeTransitionReadyHandlerRef.current);
        modeTransitionReadyHandlerRef.current = null;
      }
      if (modeTransitionTimerRef.current) {
        clearTimeout(modeTransitionTimerRef.current);
        modeTransitionTimerRef.current = null;
      }
      var commitModeAndPreview = function () {
        action();
        window.dispatchEvent(new CustomEvent("workbench:browser-transition-commit-preview", {
          detail: { sessionId: browserSessionId },
        }));
      };
      // The target screenshot and the target shell must become visible in the
      // same renderer commit. Without flushSync React may paint the target page
      // inside the old PiP shell first, which looks like an extra zoom step.
      if (window.ReactDOM && typeof window.ReactDOM.flushSync === "function") {
        window.ReactDOM.flushSync(commitModeAndPreview);
      } else {
        commitModeAndPreview();
      }
      // Let React commit the target shell and the browser surface finish layout
      // before asking Electron to attach at the new rectangle.
      modeTransitionRafRef.current = requestAnimationFrame(function () {
        modeTransitionRafRef.current = requestAnimationFrame(function () {
          modeTransitionRafRef.current = 0;
          wbcNotifyBrowserWindowInteraction(false, "mode", browserSessionId);
        });
      });
    }
    modeTransitionReadyHandlerRef.current = function (event) {
      var detail = event && event.detail || {};
      if (String(detail.sessionId || "") !== String(browserSessionId || "")) return;
      applyModeAfterPreview();
    };
    window.addEventListener("workbench:browser-transition-target-ready", modeTransitionReadyHandlerRef.current);
    // Keep the current shell unchanged while Electron prepares a frame at the
    // target rectangle. The target proxy and the React mode commit then land in
    // one batch, so no stretched intermediate frame reaches the screen.
    wbcNotifyBrowserWindowInteraction(true, "mode", browserSessionId, {
      targetMode: targetMode || "",
      targetBounds: measureBrowserSurfaceForMode(targetMode || ""),
    });
    modeTransitionTimerRef.current = setTimeout(applyModeAfterPreview, 1800);
  }

  function measuredFloatingFrame(node) {
    var area = node && node.parentElement;
    if (!node || !area) return null;
    var nodeRect = node.getBoundingClientRect();
    var areaRect = area.getBoundingClientRect();
    return {
      x: nodeRect.left - areaRect.left,
      y: nodeRect.top - areaRect.top,
      width: nodeRect.width,
      height: nodeRect.height,
    };
  }

  function measuredFrame() {
    return measuredFloatingFrame(shellRef.current);
  }

  function commitFrame(next, area) {
    var host = area || (shellRef.current && shellRef.current.parentElement);
    var clamped = host
      ? wbcClampBrowserWindowFrame(next, host.clientWidth, host.clientHeight, 240, 180)
      : next;
    var constrained = wbcKeepBrowserWindowClearOfComposer(clamped, host);
    var committed = commitFloatingFrame(shellRef.current, constrained, host, 240, 180, frameRef, setFrame);
    if (committed) wbcSaveBrowserWindowFrame(browserSessionId, committed);
  }

  // PiP and minimized mode share the same coordinate system and clamping
  // path. This keeps drag persistence, resize handling, and transcript
  // avoidance in sync instead of maintaining two subtly different movers.
  function commitFloatingFrame(node, next, host, minWidth, minHeight, targetRef, updateState) {
    if (!node || !host || !next) return null;
    var clamped = wbcClampBrowserWindowFrame(
      next,
      host.clientWidth,
      host.clientHeight,
      minWidth,
      minHeight
    );
    targetRef.current = clamped;
    // Keep the DOM shell and Electron's native WebContentsView on the same
    // pointer frame. Waiting for React to commit here makes the page visibly
    // trail the window chrome during a drag or resize.
    node.style.left = clamped.x + "px";
    node.style.top = clamped.y + "px";
    node.style.width = clamped.width + "px";
    node.style.height = clamped.height + "px";
    node.style.right = "auto";
    node.style.bottom = "auto";
    updateState(clamped);
    wbcNotifyBrowserLayoutChanged();
    return clamped;
  }

  function commitMinimizedFrame(next, area) {
    var node = minimizedRef.current;
    var host = area || (node && node.parentElement);
    return commitFloatingFrame(node, next, host, 42, 42, minimizedFrameRef, setMinimizedFrame);
  }

  function removeMinimizedDragGhost(interaction) {
    var ghost = interaction && interaction.ghost;
    if (ghost && ghost.parentNode) ghost.parentNode.removeChild(ghost);
    if (interaction) interaction.ghost = null;
  }

  function ensureMinimizedDragGhost(interaction) {
    if (!interaction || interaction.ghost || !interaction.node) return interaction && interaction.ghost;
    var rect = interaction.node.getBoundingClientRect();
    var ghost = interaction.node.cloneNode(true);
    ghost.removeAttribute("id");
    ghost.removeAttribute("title");
    ghost.setAttribute("aria-hidden", "true");
    ghost.setAttribute("tabindex", "-1");
    ghost.classList.add("dragging", "wbc-browser-drag-ghost");
    // Keep the preview slightly smaller than the resting control so it reads
    // as a lifted drag token instead of merging with header action buttons.
    var ghostInset = 3;
    ghost.style.left = (rect.left + ghostInset) + "px";
    ghost.style.top = (rect.top + ghostInset) + "px";
    ghost.style.width = Math.max(32, rect.width - (ghostInset * 2)) + "px";
    ghost.style.height = Math.max(32, rect.height - (ghostInset * 2)) + "px";
    document.body.appendChild(ghost);
    interaction.ghost = ghost;
    interaction.ghostLeft = rect.left + ghostInset;
    interaction.ghostTop = rect.top + ghostInset;
    interaction.node.classList.add("drag-source-hidden");
    return ghost;
  }

  function finalizeInteraction(interaction) {
    if (!interaction) return;
    if (interaction.previewTimer) clearTimeout(interaction.previewTimer);
    interaction.previewTimer = null;
    if (interactionRef.current === interaction) interactionRef.current = null;
    window.removeEventListener("workbench:browser-window-preview-ready", onBrowserWindowPreviewReady);
    document.body.classList.remove("wbc-browser-window-interacting");
    clearBrowserPointerDropTarget(interaction);
    wbcNotifyBrowserLayoutChanged();
    wbcNotifyBrowserWindowInteraction(false, interaction.kind, browserSessionId);
  }

  function stopInteraction() {
    var event = arguments[0];
    var interaction = interactionRef.current;
    if (interaction && interaction.captureNode && interaction.captureNode.releasePointerCapture) {
      try { interaction.captureNode.releasePointerCapture(interaction.pointerId); } catch (e) {}
    }
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", stopInteraction);
    window.removeEventListener("pointercancel", stopInteraction);
    // A plain click on the title bar never starts a native-view transition.
    // Only finish an interaction after movement crossed the drag threshold.
    if (!interaction || !interaction.started) {
      interactionRef.current = null;
      window.removeEventListener("workbench:browser-window-preview-ready", onBrowserWindowPreviewReady);
      clearBrowserPointerDropTarget(interaction);
      return;
    }
    if (event && event.type === "pointerup" && interaction.kind === "drag") {
      updateResourceShelfTarget(interaction, event.clientX, event.clientY);
      pinBrowserFromPointerInteraction(interaction);
    }
    interaction.pointerReleased = true;
    // A fast flick can release before capturePage resolves. Keep its final
    // delta alive; preview-ready will commit it without exposing an old native
    // frame. The timeout follows the same path if capture IPC ever stalls.
    if (!interaction.previewReady) return;
    finalizeInteraction(interaction);
  }

  function commitInteractionDelta(interaction, dx, dy) {
    if (!interaction) return;
    var start = interaction.frame;
    var next = { x: start.x, y: start.y, width: start.width, height: start.height };
    if (interaction.kind === "drag") {
      next.x = start.x + dx;
      next.y = start.y + dy;
    } else {
      var direction = interaction.direction;
      var right = start.x + start.width;
      var bottom = start.y + start.height;
      var minWidth = Math.min(240, interaction.area.clientWidth);
      var minHeight = Math.min(180, interaction.area.clientHeight);
      if (direction.indexOf("e") !== -1) right = Math.min(interaction.area.clientWidth, Math.max(start.x + minWidth, right + dx));
      if (direction.indexOf("s") !== -1) bottom = Math.min(interaction.area.clientHeight, Math.max(start.y + minHeight, bottom + dy));
      if (direction.indexOf("w") !== -1) next.x = Math.max(0, Math.min(right - minWidth, start.x + dx));
      if (direction.indexOf("n") !== -1) next.y = Math.max(0, Math.min(bottom - minHeight, start.y + dy));
      next.width = right - next.x;
      next.height = bottom - next.y;
    }
    commitFrame(next, interaction.area);
  }

  function onBrowserWindowPreviewReady(event) {
    var detail = event && event.detail || {};
    if (String(detail.sessionId || "") !== String(browserSessionId || "")) return;
    var interaction = interactionRef.current;
    if (!interaction || !interaction.started) return;
    if (interaction.previewTimer) clearTimeout(interaction.previewTimer);
    interaction.previewTimer = null;
    if (detail.fallback) {
      // The native view was not replaced by a painted proxy. Keep the shell at
      // its original position and cancel this gesture; moving it now would
      // expose the still-visible native view as a second detached rectangle.
      interaction.cancelled = true;
      interaction.previewReady = true;
      if (interaction.pointerReleased) finalizeInteraction(interaction);
      return;
    }
    interaction.previewReady = true;
    commitInteractionDelta(interaction, interaction.pendingDx, interaction.pendingDy);
    if (interaction.pointerReleased) finalizeInteraction(interaction);
  }

  function onPointerMove(event) {
    var interaction = interactionRef.current;
    if (!interaction) return;
    var dx = event.clientX - interaction.clientX;
    var dy = event.clientY - interaction.clientY;
    if (!interaction.started) {
      if ((dx * dx) + (dy * dy) < 9) return;
      interaction.started = true;
      document.body.classList.add("wbc-browser-window-interacting");
      wbcNotifyBrowserWindowInteraction(true, interaction.kind, browserSessionId);
      interaction.previewTimer = setTimeout(function () {
        wbcNotifyBrowserWindowInteraction(false, interaction.kind, browserSessionId);
        onBrowserWindowPreviewReady({ detail: { sessionId: browserSessionId, fallback: true } });
      }, 750);
    }
    updateResourceShelfTarget(interaction, event.clientX, event.clientY);
    interaction.pendingDx = dx;
    interaction.pendingDy = dy;
    if (interaction.cancelled) return;
    // Keep the native page and shell at their original coordinates until the
    // bitmap proxy is committed and Electron confirms the native view hidden.
    // This removes the single exposed background/old-position frame at start.
    if (!interaction.previewReady) return;
    commitInteractionDelta(interaction, dx, dy);
  }

  function beginInteraction(event, kind, direction) {
    if (effectiveMode !== "pip" || event.button !== 0) return;
    if (kind === "drag" && event.target && event.target.closest && event.target.closest("button")) return;
    var node = shellRef.current;
    var area = node && node.parentElement;
    var start = frameRef.current || measuredFrame();
    if (!node || !area || !start) return;
    event.preventDefault();
    interactionRef.current = {
      kind: kind,
      direction: direction || "",
      clientX: event.clientX,
      clientY: event.clientY,
      frame: start,
      area: area,
      pointerId: event.pointerId,
      captureNode: event.currentTarget,
      started: false,
      previewReady: false,
      pendingDx: 0,
      pendingDy: 0,
      pointerReleased: false,
      cancelled: false,
      previewTimer: null,
      overShelf: false,
      targetChatId: "",
      targetChatNode: null,
      lastClientX: event.clientX,
      lastClientY: event.clientY,
    };
    if (event.currentTarget && event.currentTarget.setPointerCapture) {
      try { event.currentTarget.setPointerCapture(event.pointerId); } catch (e) {}
    }
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", stopInteraction);
    window.addEventListener("pointercancel", stopInteraction);
    window.addEventListener("workbench:browser-window-preview-ready", onBrowserWindowPreviewReady);
  }

  function finishMinimizedDrag(event) {
    var interaction = minimizedDragRef.current;
    if (!interaction) return;
    minimizedDragRef.current = null;
    window.removeEventListener("pointermove", moveMinimizedDrag);
    window.removeEventListener("pointerup", finishMinimizedDrag);
    window.removeEventListener("pointercancel", finishMinimizedDrag);
    if (interaction.captureNode && interaction.captureNode.releasePointerCapture) {
      try { interaction.captureNode.releasePointerCapture(interaction.pointerId); } catch (e) {}
    }
    if (interaction.node) interaction.node.classList.remove("dragging", "drag-source-hidden");
    removeMinimizedDragGhost(interaction);
    document.body.classList.remove("wbc-browser-window-interacting");
    var handled = false;
    if (event && event.type === "pointerup" && interaction.started) {
      updateResourceShelfTarget(interaction, event.clientX, event.clientY);
      handled = pinBrowserFromPointerInteraction(interaction);
    }
    // A resource drop is an operation on the browser, not a request to move
    // its restore button to the stage boundary. Put the button back where the
    // drag started after pinning/copying; ordinary drags keep their new frame.
    if (handled && interaction.frame) {
      commitMinimizedFrame(interaction.frame, interaction.area);
    } else if (interaction.started) {
      wbcNotifyBrowserLayoutChanged();
    }
    clearBrowserPointerDropTarget(interaction);
    if (interaction.started) {
      suppressMinimizedClickRef.current = true;
      setTimeout(function () { suppressMinimizedClickRef.current = false; }, 0);
    }
  }

  function moveMinimizedDrag(event) {
    var interaction = minimizedDragRef.current;
    if (!interaction) return;
    var dx = event.clientX - interaction.clientX;
    var dy = event.clientY - interaction.clientY;
    if (!interaction.started) {
      if ((dx * dx) + (dy * dy) < 9) return;
      interaction.started = true;
      document.body.classList.add("wbc-browser-window-interacting");
      if (interaction.node) interaction.node.classList.add("dragging");
      ensureMinimizedDragGhost(interaction);
    }
    updateResourceShelfTarget(interaction, event.clientX, event.clientY);
    if (interaction.ghost) {
      interaction.ghost.style.left = (interaction.ghostLeft + dx) + "px";
      interaction.ghost.style.top = (interaction.ghostTop + dy) + "px";
    }
    if (interaction.frame) {
      commitMinimizedFrame({
        x: interaction.frame.x + dx,
        y: interaction.frame.y + dy,
        width: interaction.frame.width,
        height: interaction.frame.height,
      }, interaction.area);
    }
  }

  function beginMinimizedDrag(event) {
    if (event.button !== 0) return;
    var node = event.currentTarget;
    var area = node && node.parentElement;
    var start = minimizedFrameRef.current || measuredFloatingFrame(node);
    if (!node || !area || !start) return;
    event.preventDefault();
    minimizedDragRef.current = {
      kind: "drag",
      clientX: event.clientX,
      clientY: event.clientY,
      lastClientX: event.clientX,
      lastClientY: event.clientY,
      pointerId: event.pointerId,
      captureNode: event.currentTarget,
      node: node,
      area: area,
      frame: start,
      ghost: null,
      started: false,
      overShelf: false,
      targetChatId: "",
      targetChatNode: null,
      pinned: false,
    };
    if (event.currentTarget && event.currentTarget.setPointerCapture) {
      try { event.currentTarget.setPointerCapture(event.pointerId); } catch (e) {}
    }
    window.addEventListener("pointermove", moveMinimizedDrag);
    window.addEventListener("pointerup", finishMinimizedDrag);
    window.addEventListener("pointercancel", finishMinimizedDrag);
  }

  useWbcEffect(function () {
    var savedFrame = wbcLoadBrowserWindowFrame(browserSessionId);
    frameSessionRef.current = String(browserSessionId || "");
    frameRef.current = savedFrame;
    minimizedFrameRef.current = null;
    setFrame(savedFrame);
    setMinimizedFrame(null);
  }, [browserSessionId]);

  // A fullscreen session starts visually quiet. Only commands sent from this
  // compact composer opt into the live status pill; runs that were already in
  // progress before maximizing therefore do not create unsolicited chrome.
  useWbcEffect(function () {
    clearFullscreenFinalReplyTimer();
    setFullscreenDraft("");
    setFullscreenStatusRequested(false);
    setFullscreenSubmitting(false);
    setFullscreenFinalReply("");
    fullscreenReplyBaselineRef.current = String(latestAssistantReplyId || "");
    return clearFullscreenFinalReplyTimer;
  }, [effectiveMode, browserSessionId]);

  // A saved assistant message lands in the durable transcript at the same time
  // the live runtime disappears. Keep that final reply in the compact status
  // pill briefly, then remove the pill and return its pixels to the page. Runs
  // that finish without a textual reply retain the old immediate-hide behavior
  // after a short grace period for the saved message update to arrive.
  useWbcEffect(function () {
    if (effectiveMode !== "maximized" || !fullscreenStatusRequested) return undefined;
    if (running || fullscreenSubmitting || fullscreenFinalReply) return undefined;
    var replyId = String(latestAssistantReplyId || "");
    var replyText = String(latestAssistantReplyText || "").replace(/\s+/g, " ").trim();
    if (replyText && replyId && replyId !== fullscreenReplyBaselineRef.current) {
      setFullscreenFinalReply(replyText);
      clearFullscreenFinalReplyTimer();
      fullscreenFinalReplyTimerRef.current = setTimeout(function () {
        fullscreenFinalReplyTimerRef.current = null;
        setFullscreenFinalReply("");
        setFullscreenStatusRequested(false);
      }, 5000);
      return undefined;
    }
    var settleTimer = setTimeout(function () {
      setFullscreenStatusRequested(false);
    }, 1200);
    return function () { clearTimeout(settleTimer); };
  }, [effectiveMode, running, fullscreenSubmitting, fullscreenStatusRequested, fullscreenFinalReply, latestAssistantReplyId, latestAssistantReplyText]);

  // Electron's live page is a native WebContentsView and therefore composites
  // above renderer DOM. Its compact chat is a second transparent native view,
  // raised above the page; the web/screencast fallback keeps the DOM version.
  useWbcEffect(function () {
    if (!hasNativeChatOverlay || typeof browserBridge.onChatOverlayAction !== "function") return undefined;
    return browserBridge.onChatOverlayAction(function (action) {
      if (!action || String(action.sessionId || "") !== String(browserSessionId || "")) return;
      if (effectiveMode !== "maximized") return;
      if (action.type === "stop") {
        if (running && onInterrupt) onInterrupt();
        return;
      }
      sendFullscreenChatText(action.text || "");
    });
  }, [hasNativeChatOverlay, browserSessionId, effectiveMode, running, onSend, onGuidance, onInterrupt]);

  // The native overlay lives in a separate renderer, so CSS variables do not
  // cascade into it. Re-send its palette whenever the host theme or accent is
  // applied to the document root.
  useWbcEffect(function () {
    if (!hasNativeChatOverlay) return undefined;
    var root = document.documentElement;
    var frameId = 0;
    function refreshOverlayTheme() {
      if (frameId) return;
      frameId = requestAnimationFrame(function () {
        frameId = 0;
        setChatOverlayThemeRevision(function (value) { return value + 1; });
      });
    }
    var observer = typeof MutationObserver === "function"
      ? new MutationObserver(refreshOverlayTheme)
      : null;
    if (observer) observer.observe(root, { attributes: true, attributeFilter: ["data-theme", "style"] });
    window.addEventListener("cyrene-tweak-theme-change", refreshOverlayTheme);
    window.addEventListener("cyrene-tweak-accent-change", refreshOverlayTheme);
    return function () {
      if (frameId) cancelAnimationFrame(frameId);
      if (observer) observer.disconnect();
      window.removeEventListener("cyrene-tweak-theme-change", refreshOverlayTheme);
      window.removeEventListener("cyrene-tweak-accent-change", refreshOverlayTheme);
    };
  }, [hasNativeChatOverlay]);

  useWbcEffect(function () {
    if (!hasNativeChatOverlay) return;
    var paletteNode = document.querySelector(".workbench-shell") || document.documentElement;
    var rootStyles = getComputedStyle(paletteNode);
    function color(name, fallback) {
      return String(rootStyles.getPropertyValue(name) || "").trim() || fallback;
    }
    browserBridge.setChatOverlay({
      sessionId: browserSessionId || "",
      visible: visible && effectiveMode === "maximized",
      running: !!running,
      showStatus: fullscreenStatusVisible,
      statusText: fullscreenStatusText,
      statusComplete: !!fullscreenCompletedReply,
      placeholder: wbcT("workbenchChat.browserChatPlaceholder", "Tell Agent what to do in the browser…"),
      placeholderRunning: wbcT("workbenchChat.browserChatPlaceholderRunning", "Add an instruction…"),
      sendLabel: wbcT("workbenchChat.send", "Send"),
      guideLabel: wbcT("workbenchChat.sendGuidance", "Send guidance"),
      stopLabel: wbcT("workbenchChat.stop", "Stop"),
      colors: {
        line: color("--wb-line-2", "#d8dce4"),
        panel: color("--wb-card-bg-strong", "#ffffff"),
        text: color("--wb-text", "#17191d"),
        muted: color("--wb-muted", "#6f737b"),
        faint: color("--wb-faint", "#9297a1"),
        accent: color("--wb-accent", "#6d5dfc"),
        "accent-text": color("--wb-accent-text", "#ffffff"),
        green: color("--wb-green", "#1f9d57"),
        red: color("--wb-red", "#d84848"),
      },
    }).catch(function () {});
  }, [hasNativeChatOverlay, browserSessionId, visible, effectiveMode, running, fullscreenStatusVisible, fullscreenStatusText, fullscreenCompletedReply, chatOverlayThemeRevision]);

  useWbcEffect(function () {
    if (!hasNativeChatOverlay) return undefined;
    return function () {
      browserBridge.setChatOverlay({ sessionId: browserSessionId || "", visible: false }).catch(function () {});
    };
  }, [hasNativeChatOverlay, browserSessionId]);

  useWbcEffect(function () {
    var bridge = window.cyrene && window.cyrene.browser;
    var sessionId = String(browserSessionId || "");
    setNativeBrowserState(null);
    if (!visible || !sessionId || !bridge || typeof bridge.getState !== "function") return undefined;
    bridge.getState(sessionId).then(function (next) {
      if (next && String(next.sessionId || "") === sessionId) setNativeBrowserState(next);
    }).catch(function () {});
    if (typeof bridge.onState !== "function") return undefined;
    return bridge.onState(function (next) {
      if (next && String(next.sessionId || "") === sessionId) setNativeBrowserState(next);
    });
  }, [visible, browserSessionId]);

  function updateFloatingBrowserState(next) {
    if (next && next.ok !== false && String(next.sessionId || "") === String(browserSessionId || "")) {
      setNativeBrowserState(next);
    }
    return next;
  }

  function setMaximizedBrowserPicker(nextOpen) {
    nextOpen = nextOpen === true;
    if (nextOpen) {
      // Mount the menu immediately so the opening animation responds to the
      // click without waiting for the native page preview capture.
      setMaximizedPickerOpen(true);
      window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
        detail: {
          obscured: true,
          preview: true,
          sessionId: String(browserSessionId || ""),
        },
      }));
      return;
    }
    setMaximizedPickerOpen(false);
    window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
      detail: { obscured: false, sessionId: String(browserSessionId || "") },
    }));
  }

  function selectMaximizedBrowserTab(tab) {
    if (!tab || !tab.id) return;
    setMaximizedBrowserPicker(false);
    if (browserBridge && typeof browserBridge.activateTab === "function") {
      browserBridge.activateTab({ sessionId: browserSessionId, tabId: tab.id }).then(updateFloatingBrowserState).catch(function () {});
    }
  }

  function refreshMaximizedBrowserTab(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!browserBridge || !tab || !tab.id || typeof browserBridge.reload !== "function") return;
    browserBridge.reload({ sessionId: browserSessionId, tabId: tab.id }).then(updateFloatingBrowserState).catch(function () {});
  }

  function toggleMaximizedBrowserMute(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!browserBridge || !tab || !tab.id || typeof browserBridge.setMuted !== "function") return;
    browserBridge.setMuted({ sessionId: browserSessionId, tabId: tab.id, muted: !tab.muted }).then(updateFloatingBrowserState).catch(function () {});
  }

  function closeMaximizedBrowserTab(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!browserBridge || !tab || !tab.id || typeof browserBridge.closeTab !== "function") return;
    browserBridge.closeTab({ sessionId: browserSessionId, tabId: tab.id }).then(function (next) {
      updateFloatingBrowserState(next);
      if (!next || !Array.isArray(next.tabs) || !next.tabs.length) setMaximizedBrowserPicker(false);
    }).catch(function () {});
  }

  useWbcEffect(function () {
    if (effectiveMode === "maximized") return undefined;
    setMaximizedPickerOpen(false);
    window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
      detail: { obscured: false, sessionId: String(browserSessionId || "") },
    }));
    return undefined;
  }, [effectiveMode, browserSessionId]);

  useWbcEffect(function () {
    return function () {
      window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
        detail: { obscured: false, sessionId: String(browserSessionId || "") },
      }));
    };
  }, [browserSessionId]);

  useWbcEffect(function () {
    var wasVisible = previousVisibleRef.current;
    previousVisibleRef.current = visible;
    if (!wasVisible && visible && effectiveMode === "pip") {
      // The grid is still returning from its wider split column. Keep the
      // stored PiP frame immutable until that layout animation settles;
      // otherwise ResizeObserver progressively clamps and persists a new
      // position while the available area is changing.
      pipRestoreGuardUntilRef.current = performance.now() + 560;
      if (pipRestoreTimerRef.current) clearTimeout(pipRestoreTimerRef.current);
      pipRestoreTimerRef.current = setTimeout(function () {
        pipRestoreTimerRef.current = null;
        var node = shellRef.current;
        var area = node && node.parentElement;
        var saved = frameRef.current;
        if (area && saved) commitFrame(saved, area);
        wbcNotifyBrowserLayoutChanged();
      }, 570);
    }
    return function () {
      if (pipRestoreTimerRef.current) {
        clearTimeout(pipRestoreTimerRef.current);
        pipRestoreTimerRef.current = null;
      }
    };
  }, [visible, effectiveMode]);

  useWbcEffect(function () {
    if (!visible || (effectiveMode !== "pip" && effectiveMode !== "minimized")) return undefined;
    var node = effectiveMode === "pip" ? shellRef.current : minimizedRef.current;
    var area = node && node.parentElement;
    if (!area) return undefined;
    if (effectiveMode === "minimized" && !minimizedFrameRef.current) {
      var initialMinimizedFrame = measuredFloatingFrame(node);
      if (initialMinimizedFrame) commitMinimizedFrame(initialMinimizedFrame, area);
    }
    if (typeof ResizeObserver === "undefined") return undefined;
    var observer = new ResizeObserver(function () {
      if (effectiveMode === "pip") {
        if (performance.now() < pipRestoreGuardUntilRef.current) {
          wbcNotifyBrowserLayoutChanged();
          return;
        }
        var current = frameRef.current;
        if (current) commitFrame(current, area);
      } else {
        var minimizedCurrent = minimizedFrameRef.current;
        if (minimizedCurrent) commitMinimizedFrame(minimizedCurrent, area);
      }
      wbcNotifyBrowserLayoutChanged();
    });
    observer.observe(area);
    return function () { observer.disconnect(); };
  }, [visible, effectiveMode]);

  useWbcEffect(function () {
    var raf = requestAnimationFrame(wbcNotifyBrowserLayoutChanged);
    return function () { cancelAnimationFrame(raf); };
  }, [frame && frame.x, frame && frame.y, frame && frame.width, frame && frame.height, minimizedFrame && minimizedFrame.x, minimizedFrame && minimizedFrame.y, effectiveMode, visible]);

  useWbcEffect(function () {
    if (effectiveMode !== "maximized") return undefined;
    function onKeyDown(event) {
      if (event.key === "Escape" && onRestore) onRestore();
    }
    window.addEventListener("keydown", onKeyDown);
    return function () { window.removeEventListener("keydown", onKeyDown); };
  }, [effectiveMode, onRestore]);

  useWbcEffect(function () {
    return function () {
      stopInteraction();
      finishMinimizedDrag();
      cancelModeTransition();
    };
  }, []);

  if (!visible) return null;
  if (hasNoBrowserTabs && (effectiveMode === "pip" || effectiveMode === "minimized")) return null;
  if (effectiveMode === "minimized") {
    var minimizedInlineStyle = minimizedFrame ? {
      left: minimizedFrame.x + "px",
      top: minimizedFrame.y + "px",
      width: minimizedFrame.width + "px",
      height: minimizedFrame.height + "px",
      right: "auto",
      bottom: "auto",
    } : undefined;
    return (
      <button
        ref={minimizedRef}
        type="button"
        className="wbc-browser-restore-float"
        style={minimizedInlineStyle}
        onPointerDown={beginMinimizedDrag}
        onClick={function () {
          if (!suppressMinimizedClickRef.current && onRestore) onRestore();
        }}
        aria-label={wbcBrowserWindowTitle(displayBrowserState)}
        title={wbcBrowserWindowTitle(displayBrowserState) + " · " + wbcT("workbenchChat.dragBrowserToTopbar", "Drag to the topbar to pin")}
      >
        <span className="wbc-browser-restore-favicon" aria-hidden="true">
          <svg className="fallback" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 8h18M6 6h.01M9 6h.01"/></svg>
          {displayBrowserFavicon ? (
            <img
              key={displayBrowserFavicon}
              src={displayBrowserFavicon}
              alt=""
              draggable="false"
              onError={function (event) { event.currentTarget.hidden = true; }}
            />
          ) : null}
        </span>
      </button>
    );
  }

  var inlineStyle = effectiveMode === "pip" && frame && frameSessionRef.current === String(browserSessionId || "") ? {
    left: frame.x + "px",
    top: frame.y + "px",
    width: frame.width + "px",
    height: frame.height + "px",
    right: "auto",
    bottom: "auto",
  } : undefined;
  var resizeDirections = ["n", "e", "s", "w", "ne", "nw", "se", "sw"];
  var browserWindow = (
    <section
      ref={shellRef}
      className={"wbc-browser-window " + effectiveMode}
      style={inlineStyle}
      aria-label={wbcT("workbenchChat.browserWindowRegion", "Live browser window")}
    >
      <div className={effectiveMode === "maximized" ? "wbc-resource-split-picker-wrap wbc-browser-maximized-picker-wrap" : "wbc-browser-pip-head-wrap"}>
        <div
          className={"wbc-browser-window-bar" + (effectiveMode === "maximized" ? " wbc-browser-maximized-head" : "")}
          onPointerDown={effectiveMode === "pip" ? function (event) { beginInteraction(event, "drag", ""); } : undefined}
          onDoubleClick={effectiveMode === "pip" ? function () { runModeTransition(onMaximize, "maximized"); } : undefined}
        >
          {effectiveMode === "maximized" ? (
            <button type="button" className="wbc-browser-maximized-picker" onClick={function () { setMaximizedBrowserPicker(!maximizedPickerOpen); }} aria-expanded={maximizedPickerOpen}>
              <span className="wbc-browser-window-title">
                <span className="wbc-browser-title-pill">{wbcT("workbenchChat.browserWindowTitle", "Browser")}</span>
                <strong title={wbcBrowserWindowTitle(displayBrowserState)}>{wbcBrowserPageTitle(displayBrowserState) || wbcT("workbenchChat.browserWindowTitle", "Browser")}</strong>
              </span>
              <span className="wbc-browser-maximized-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            </button>
          ) : (
            <span className="wbc-browser-window-title">
              <span className="wbc-browser-title-pill" title={wbcT("workbenchChat.dragBrowserToTopbar", "Drag to the topbar to pin")}>{wbcT("workbenchChat.browserWindowTitle", "Browser")}</span>
              {wbcBrowserPageTitle(displayBrowserState) && <strong title={wbcBrowserWindowTitle(displayBrowserState)}>{wbcBrowserPageTitle(displayBrowserState)}</strong>}
            </span>
          )}
          <div className="wbc-browser-window-actions" onPointerDown={function (event) { event.stopPropagation(); }}>
            {effectiveMode === "pip" ? (
              <button type="button" onClick={function () { runModeTransition(onMaximize, "maximized"); }} title={wbcT("workbenchChat.browserMaximize", "Maximize")} aria-label={wbcT("workbenchChat.browserMaximize", "Maximize")}>{WBC_ICONS.windowMaximize}</button>
            ) : (
              <React.Fragment>
                <button type="button" className="wbc-browser-split-action" onClick={function (event) { refreshMaximizedBrowserTab(displayActiveBrowserTab, event); }} title={wbcT("browser.context.reload", "Reload")} aria-label={wbcT("browser.context.reload", "Reload")}>{FloatingBrowserIcon ? <FloatingBrowserIcon name="reload" size={15} /> : WBC_ICONS.retry}</button>
                <button type="button" className={"wbc-browser-split-action" + (displayActiveBrowserTab.muted ? " active" : "")} onClick={function (event) { toggleMaximizedBrowserMute(displayActiveBrowserTab, event); }} title={displayActiveBrowserTab.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")} aria-label={displayActiveBrowserTab.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")}>{FloatingBrowserIcon ? <FloatingBrowserIcon name={displayActiveBrowserTab.muted ? "muted" : "volume"} size={15} /> : null}</button>
                <button type="button" onClick={function () { setMaximizedBrowserPicker(false); runModeTransition(onRestore, "pip"); }} title={wbcT("workbenchChat.browserRestoreSize", "Restore")} aria-label={wbcT("workbenchChat.browserRestoreSize", "Restore")}>{WBC_ICONS.x}</button>
              </React.Fragment>
            )}
            {effectiveMode === "pip" && <button type="button" onClick={onMinimize} title={wbcT("workbenchChat.browserMinimize", "Minimize")} aria-label={wbcT("workbenchChat.browserMinimize", "Minimize")}>{WBC_ICONS.windowMinimize}</button>}
          </div>
        </div>
        {effectiveMode === "maximized" && (
          <WbcSplitPickerMenu open={maximizedPickerOpen} className="wbc-side-agent-split-menu wbc-resource-picker-menu wbc-browser-picker-menu wbc-browser-maximized-menu" role="listbox">
            {displayBrowserTabs.map(function (tab) {
              var selected = String(tab.id || "") === String(displayActiveBrowserTab.id || displayBrowserState.activeTabId || "");
              return <div key={tab.id} className={"wbc-browser-picker-row" + (selected ? " active" : "")} role="option" aria-selected={selected}><button type="button" className="wbc-browser-picker-select" onClick={function () { selectMaximizedBrowserTab(tab); }}><span className="wbc-browser-picker-favicon" aria-hidden="true"><span className="wbc-browser-picker-favicon-fallback">{WBC_SIDE_TAB_ICONS.browser}</span>{tab.favicon ? <img src={tab.favicon} alt="" draggable="false" onError={function (event) { event.currentTarget.hidden = true; }} /> : null}</span><b>{tab.title || tab.url || wbcT("workbenchChat.browserWindowTitle", "Browser")}</b></button><span className="wbc-browser-picker-actions"><button type="button" onClick={function (event) { refreshMaximizedBrowserTab(tab, event); }} aria-label={wbcT("browser.context.reload", "Reload")}>{FloatingBrowserIcon ? <FloatingBrowserIcon name="reload" size={14} /> : WBC_ICONS.retry}</button><button type="button" className={tab.muted ? "active" : ""} onClick={function (event) { toggleMaximizedBrowserMute(tab, event); }} aria-label={tab.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")}>{FloatingBrowserIcon ? <FloatingBrowserIcon name={tab.muted ? "muted" : "volume"} size={14} /> : null}</button><button type="button" onClick={function (event) { closeMaximizedBrowserTab(tab, event); }} aria-label={wbcT("browser.context.closeTab", "Close tab")} title={wbcT("browser.context.closeTab", "Close tab")}>{WBC_ICONS.x}</button></span></div>;
            })}
          </WbcSplitPickerMenu>
        )}
      </div>
      <div className="wbc-browser-window-content">
        {window.CyreneUI.require("browser").ViewportPanel
          ? React.createElement(window.CyreneUI.require("browser").ViewportPanel, {
              browserState: browserState || {},
              browserSessionId: browserSessionId || "",
              roundId: (browserState && browserState.roundId) || "",
              onTakeoverComplete: onTakeoverComplete,
              hideTabStrip: effectiveMode === "maximized",
              hideReload: effectiveMode === "maximized",
              hideMute: effectiveMode === "maximized",
            })
          : <p className="workbench-muted">{wbcT("chat.side.browserUnavailable", "Browser view is unavailable.")}</p>}
        {effectiveMode === "maximized" && !hasNativeChatOverlay && (
          <div className="wbc-browser-fullscreen-chat">
            {fullscreenStatusVisible && (
              <div className={"wbc-browser-fullscreen-status" + (fullscreenCompletedReply ? " completed" : "")} role="status" aria-live="polite">
                <span className="wbc-browser-fullscreen-status-dot" aria-hidden="true" />
                <span>{fullscreenStatusText}</span>
              </div>
            )}
            <form className="wbc-browser-fullscreen-composer" onSubmit={submitFullscreenChat}>
              <input
                type="text"
                value={fullscreenDraft}
                onChange={function (event) { setFullscreenDraft(event.target.value); }}
                placeholder={running
                  ? wbcT("workbenchChat.browserChatPlaceholderRunning", "Add an instruction…")
                  : wbcT("workbenchChat.browserChatPlaceholder", "Tell Agent what to do in the browser…")}
                aria-label={wbcT("workbenchChat.browserChatInput", "Browser Agent instruction")}
              />
              <button
                type="submit"
                className={running && !fullscreenDraft.trim() ? "stop" : ""}
                disabled={!running && !fullscreenDraft.trim()}
                title={running && !fullscreenDraft.trim()
                  ? wbcT("workbenchChat.stop", "Stop")
                  : (running
                    ? wbcT("workbenchChat.sendGuidance", "Send guidance")
                    : wbcT("workbenchChat.send", "Send"))}
              >
                {running && !fullscreenDraft.trim() ? WBC_ICONS.stop : WBC_ICONS.send}
              </button>
            </form>
          </div>
        )}
      </div>
      {effectiveMode === "pip" && resizeDirections.map(function (direction) {
        return (
          <span
            key={direction}
            className={"wbc-browser-resize-handle " + direction}
            onPointerDown={function (event) { beginInteraction(event, "resize", direction); }}
            aria-hidden="true"
          />
        );
      })}
    </section>
  );
  if (effectiveMode === "maximized" && window.ReactDOM && typeof window.ReactDOM.createPortal === "function") {
    // Keep the maximized browser inside the Workbench theme scope. Portaling
    // directly to <body> drops the --wb-* custom properties, which makes the
    // picker background transparent and lets the address bar show through.
    var workbenchPortalRoot = document.querySelector(".workbench-shell") || document.body;
    return window.ReactDOM.createPortal(browserWindow, workbenchPortalRoot);
  }
  return browserWindow;
}

// One stable layout box per transcript entry.  Browser avoidance is applied to
// this wrapper so the existing child alignment stays intact: user bubbles keep
// hugging the lane's right edge and assistant content keeps its left edge.
function wbcNavigationPreview(value) {
  return String(value == null ? "" : value)
    .replace(/```[\s\S]*?```/g, function (block) { return block.replace(/```[^\n]*\n?/g, "").replace(/```/g, ""); })
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/(^|\s)[#>*_`~-]+/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 150);
}

function wbcUserMessageNavigationMeta(message) {
  var msg = message || {};
  var prefix = wbcT("workbenchChat.navigation.you", "You");
  var contentPreview = wbcNavigationPreview(msg.content || "");
  var attachments = Array.isArray(msg.attachments) ? msg.attachments : [];
  var attachmentTypes = [];
  attachments.forEach(function (file) {
    var type = wbcAttachmentTypeLabel(file);
    if (type && attachmentTypes.indexOf(type) === -1) attachmentTypes.push(type);
  });
  var attachmentPreview = attachmentTypes.slice(0, 2).join(" · ");
  if (attachmentTypes.length === 1 && attachments.length > 1) attachmentPreview += " × " + attachments.length;
  if (attachmentTypes.length > 2) attachmentPreview += " · +" + (attachmentTypes.length - 2);
  var preview = contentPreview || attachmentPreview || prefix;
  return {
    role: "user",
    label: contentPreview ? prefix + ": " + preview : preview,
    text: preview,
  };
}

function WbcThreadItem({ children, navigation }) {
  var nav = navigation || null;
  return (
    <div
      className="wbc-thread-item"
      data-wbc-thread-item="true"
      data-wbc-nav-item={nav ? "true" : undefined}
      data-wbc-nav-role={nav ? nav.role : undefined}
      data-wbc-nav-label={nav ? nav.label : undefined}
      data-wbc-nav-text={nav ? nav.text : undefined}
    >
      {children}
    </div>
  );
}

function WbcConversationNavigator({ threadRef, chatId }) {
  var [snapshot, setSnapshot] = useWbcState({
    visible: false,
    active: -1,
    markers: [],
  });

  useWbcEffect(function () {
    var thread = threadRef.current;
    if (!thread) return undefined;
    var raf = 0;
    var itemObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleMeasure)
      : null;
    var observedItems = typeof WeakSet === "function" ? new WeakSet() : null;

    function collectItems() {
      var items = Array.prototype.slice.call(thread.querySelectorAll(":scope > [data-wbc-nav-item='true']"));
      if (itemObserver) {
        items.forEach(function (item) {
          if (observedItems && observedItems.has(item)) return;
          if (observedItems) observedItems.add(item);
          itemObserver.observe(item);
        });
      }
      return items;
    }

    function measure() {
      raf = 0;
      var clientHeight = Math.max(1, thread.clientHeight);
      var items = collectItems();
      var viewportCenter = thread.scrollTop + clientHeight * 0.42;
      var active = -1;
      var activeDistance = Infinity;
      var markers = items.map(function (item, index) {
        var center = item.offsetTop + item.offsetHeight / 2;
        var distance = Math.abs(center - viewportCenter);
        if (distance < activeDistance) {
          activeDistance = distance;
          active = index;
        }
        var role = String(item.dataset.wbcNavRole || "assistant");
        return {
          index: index,
          role: role,
          label: String(item.dataset.wbcNavLabel || ""),
          text: String(item.dataset.wbcNavText || item.dataset.wbcNavLabel || ""),
        };
      });
      setSnapshot({
        visible: markers.length > 5,
        active: active,
        markers: markers,
      });
    }

    function scheduleMeasure() {
      if (raf) return;
      raf = requestAnimationFrame(measure);
    }

    var threadObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleMeasure)
      : null;
    if (threadObserver) threadObserver.observe(thread);
    var mutationObserver = typeof MutationObserver === "function"
      ? new MutationObserver(scheduleMeasure)
      : null;
    if (mutationObserver) mutationObserver.observe(thread, { childList: true, subtree: true, characterData: true });
    thread.addEventListener("scroll", scheduleMeasure, { passive: true });
    window.addEventListener("resize", scheduleMeasure);
    scheduleMeasure();
    return function () {
      if (raf) cancelAnimationFrame(raf);
      if (threadObserver) threadObserver.disconnect();
      if (itemObserver) itemObserver.disconnect();
      if (mutationObserver) mutationObserver.disconnect();
      thread.removeEventListener("scroll", scheduleMeasure);
      window.removeEventListener("resize", scheduleMeasure);
    };
  }, [threadRef, chatId]);

  function jumpToMarker(index) {
    var thread = threadRef.current;
    if (!thread) return;
    var items = thread.querySelectorAll(":scope > [data-wbc-nav-item='true']");
    var target = items[index];
    if (!target) return;
    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    thread.scrollTo({
      top: Math.max(0, target.offsetTop - 18),
      behavior: reducedMotion ? "auto" : "smooth",
    });
  }

  if (!snapshot.visible) return null;
  return (
    <nav className="wbc-conversation-nav" aria-label={wbcT("workbenchChat.navigation.label", "Conversation navigation")}>
      <button
        type="button"
        className="wbc-conversation-nav-trigger"
        aria-label={wbcT("workbenchChat.navigation.label", "Conversation navigation")}
      >
        <span /><span /><span /><span /><span />
      </button>
      <div className="wbc-conversation-nav-panel">
        <div className="wbc-conversation-nav-heading">
          <span>{wbcT("workbenchChat.navigation.messages", "Your messages")}</span>
          <span>{snapshot.markers.length}</span>
        </div>
        <div className="wbc-conversation-nav-list">
          {snapshot.markers.map(function (marker) {
            return (
              <button
                type="button"
                key={marker.index}
                className={"wbc-conversation-marker " + marker.role + (marker.index === snapshot.active ? " active" : "")}
                aria-label={wbcT("workbenchChat.navigation.jump", "Jump to: {label}", { label: marker.label })}
                aria-current={marker.index === snapshot.active ? "location" : undefined}
                onClick={function () { jumpToMarker(marker.index); }}
              >
                <span className="wbc-conversation-marker-index">{marker.index + 1}</span>
                <span className="wbc-conversation-marker-text">{marker.text}</span>
              </button>
            );
          })}
        </div>
      </div>
    </nav>
  );
}

function WbcMain({ project, chat, chatSummary, loading, runtime, error, errorKind, onRetry, running, onSend, onGuidance, onInterrupt, onAnswer, onRetryMessage, onEditMessage, onAskSelection, sideAgentCreating, onConversationContextMenu, onRename, onDelete, onToTask, toTaskBusy, onOpenFile, onOpenDroppedChat, sideVisible, sidePanelTabExpanded, onToggleSide, browserState, browserSessionId, browserVisible, browserWindowMode, onBrowserMinimize, onBrowserMaximize, onBrowserRestore, onBrowserTakeoverComplete, splitOpen }) {
  var mainRef = useWbcRef(null);
  var stageRef = useWbcRef(null);
  var scrollRef = useWbcRef(null);
  var selectionMenuRef = useWbcRef(null);
  var stickRef = useWbcRef(true);
  var [showScrollToBottom, setShowScrollToBottom] = useWbcState(false);
  var [selectionMenu, setSelectionMenu] = useWbcState(null);
  var [chatDropActive, setChatDropActive] = useWbcState(false);
  var [browserSuppressedForSide, setBrowserSuppressedForSide] = useWbcState(false);
  var avoidanceRafRef = useWbcRef(0);
  var stickyRestoreRafRef = useWbcRef(0);
  var avoidanceScrollingRef = useWbcRef(false);
  var avoidanceScrollTimerRef = useWbcRef(null);
  // ResizeObserver reports the height changes caused by our own PiP lane
  // classes. Treating those reports like fresh external layout changes creates
  // a remove/re-add/restore loop which is especially visible at scrollTop=0.
  var avoidanceApplyingRef = useWbcRef(false);
  var avoidanceApplyingRafRef = useWbcRef(0);
  var durableMessages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  var runtimeTimeline = wbcRuntimeSegmentMessages(runtime).concat(wbcRuntimeTimelineMessages(runtime));
  var messages = wbcMergeChronologicalMessages(durableMessages, runtimeTimeline);
  var activityTraceKeys = new Set();
  messages.forEach(function (message) {
    if (!message || !(message.activityCard || message.runtimeActivity)) return;
    var trace = message.runtimeActivity
      ? message.runtimeActivity.progress
      : message.trace;
    var key = wbcTraceDedupeKey(trace);
    if (key) activityTraceKeys.add(key);
  });
  var isLegacy = !!(chat && chat.legacy);
  var latestAssistantReplyId = "";
  var latestAssistantReplyText = "";
  for (var di = durableMessages.length - 1; di >= 0; di--) {
    var durableMessage = durableMessages[di] || {};
    var durableContent = String(durableMessage.content || "").trim();
    if (durableMessage.role !== "assistant" || !durableContent) continue;
    latestAssistantReplyId = String(durableMessage.id || durableMessage.createdAt || ("assistant-" + di));
    latestAssistantReplyText = durableContent;
    break;
  }
  var lastAssistantId = "";
  var lastUserId = "";
  for (var mi = messages.length - 1; mi >= 0; mi--) {
    if (messages[mi].role === "user") {
      if (!lastUserId) lastUserId = String(messages[mi].id || "");
    } else if (!lastAssistantId) {
      lastAssistantId = String(messages[mi].id || "");
    }
    if (lastUserId && lastAssistantId) break;
  }

  // The bottom arm of the shared L-shaped glass follows the composer's real
  // rendered height. This keeps its upper edge aligned when runtime controls,
  // attachments or wrapped content change the composer size.
  useWbcEffect(function () {
    var main = mainRef.current;
    if (!main) return undefined;
    var page = main.closest(".wbc-page");
    var composer = main.querySelector(":scope > .wbc-composer");
    if (!page || !composer) return undefined;
    function syncSharedGlassHeight() {
      var height = Math.ceil(composer.getBoundingClientRect().height);
      if (height > 0) page.style.setProperty("--wbc-shared-glass-height", height + "px");
    }
    syncSharedGlassHeight();
    var observer = typeof ResizeObserver === "function"
      ? new ResizeObserver(syncSharedGlassHeight)
      : null;
    if (observer) observer.observe(composer);
    window.addEventListener("resize", syncSharedGlassHeight);
    return function () {
      if (observer) observer.disconnect();
      window.removeEventListener("resize", syncSharedGlassHeight);
      page.style.removeProperty("--wbc-shared-glass-height");
    };
  }, [chat && chat.id]);

  // Expanded side-panel content only suppresses a floating browser when the
  // browser actually occupies the right-side track. A window parked fully to
  // the left remains available while the accordion content is open.
  useWbcEffect(function () {
    var shouldMeasure = !!(
      sidePanelTabExpanded
      && browserVisible
      && (browserWindowMode === "pip" || browserWindowMode === "minimized")
    );
    if (!shouldMeasure) {
      setBrowserSuppressedForSide(false);
      return undefined;
    }
    var raf = 0;
    function measureOverlap() {
      raf = 0;
      var main = mainRef.current;
      var page = main && main.closest(".wbc-page");
      var side = page && page.querySelector(":scope > .wbc-side");
      var floating = main && main.querySelector(".wbc-browser-window.pip, .wbc-browser-restore-float");
      if (!side || !floating) return;
      var sideRect = side.getBoundingClientRect();
      var floatingRect = floating.getBoundingClientRect();
      setBrowserSuppressedForSide(
        floatingRect.right > sideRect.left && floatingRect.left < sideRect.right
      );
    }
    function scheduleMeasure() {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measureOverlap);
    }
    scheduleMeasure();
    window.addEventListener("workbench:browser-layout", scheduleMeasure);
    window.addEventListener("resize", scheduleMeasure);
    return function () {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener("workbench:browser-layout", scheduleMeasure);
      window.removeEventListener("resize", scheduleMeasure);
    };
  }, [sidePanelTabExpanded, browserVisible, browserWindowMode]);

  var floatingBrowserVisible = browserVisible && !browserSuppressedForSide;

  // Content can finish reflowing one frame after a message/ PiP resize. A
  // scrollHeight change does not emit a scroll event, so the synchronous
  // bottom restoration alone can still leave the live tail a few pixels above
  // the real bottom. Re-assert it after layout settles, but only while the
  // reader has not intentionally left the live tail.
  var scheduleStickyViewportRestore = useWbcCallback(function () {
    if (!stickRef.current || stickyRestoreRafRef.current) return;
    stickyRestoreRafRef.current = requestAnimationFrame(function () {
      stickyRestoreRafRef.current = 0;
      var thread = scrollRef.current;
      if (!thread || !stickRef.current) return;
      thread.scrollTop = thread.scrollHeight;
      setShowScrollToBottom(false);
    });
  }, []);

  var applyBrowserAvoidance = useWbcCallback(function (preserveViewport) {
    var stage = stageRef.current;
    var thread = scrollRef.current;
    if (!stage || !thread) return;
    var items = Array.prototype.slice.call(thread.querySelectorAll(":scope > [data-wbc-thread-item]"));
    if (!items.length) return;
    avoidanceApplyingRef.current = true;
    if (avoidanceApplyingRafRef.current) cancelAnimationFrame(avoidanceApplyingRafRef.current);
    avoidanceApplyingRafRef.current = requestAnimationFrame(function () {
      avoidanceApplyingRafRef.current = 0;
      avoidanceApplyingRef.current = false;
    });

    // Preserve the reader's visual anchor across text reflow.  At the live tail
    // the bottom is the anchor; in scrollback it is the first visible entry.
    var anchorNode = null;
    var anchorOffset = 0;
    if (preserveViewport && !stickRef.current) {
      var anchorTarget = thread.scrollTop;
      var anchorLow = 0, anchorHigh = items.length;
      while (anchorLow < anchorHigh) {
        var anchorMid = Math.floor((anchorLow + anchorHigh) / 2);
        var anchorItem = items[anchorMid];
        if (anchorItem.offsetTop + anchorItem.offsetHeight <= anchorTarget) anchorLow = anchorMid + 1;
        else anchorHigh = anchorMid;
      }
      anchorNode = items[Math.min(anchorLow, items.length - 1)] || null;
      if (anchorNode) anchorOffset = anchorNode.offsetTop - thread.scrollTop;
    }

    function restoreViewport() {
      if (!preserveViewport) return;
      if (stickRef.current) {
        thread.scrollTop = thread.scrollHeight;
      } else if (anchorNode && anchorNode.isConnected) {
        thread.scrollTop = Math.max(0, anchorNode.offsetTop - anchorOffset);
      }
    }

    items.forEach(function (item) {
      item.classList.remove("wbc-browser-avoid-left", "wbc-browser-avoid-right");
      item.style.removeProperty("--wbc-browser-avoid-start");
      item.style.removeProperty("--wbc-browser-avoid-end");
    });
    restoreViewport();

    var browserWindow = stage.querySelector(".wbc-browser-window.pip")
      || stage.querySelector(".wbc-browser-restore-float");
    if (!browserWindow) return;
    var browserRect = browserWindow.getBoundingClientRect();
    var threadRect = thread.getBoundingClientRect();
    var threadStyles = getComputedStyle(thread);
    var paddingLeft = parseFloat(threadStyles.paddingLeft) || 0;
    var paddingRight = parseFloat(threadStyles.paddingRight) || 0;
    var areaLeft = threadRect.left + paddingLeft;
    var areaWidth = Math.max(0, thread.clientWidth - paddingLeft - paddingRight);
    var gap = 14;
    var plan = wbcBrowserAvoidancePlan(areaLeft, areaWidth, browserRect.left, browserRect.width, gap);
    if (!plan) return;

    // Adding a lane can make a long entry taller and move later entries under
    // the fixed PiP.  Grow the avoided set monotonically for a few cheap passes
    // until no newly intersecting entry appears; never remove one mid-pass.
    for (var pass = 0; pass < 5; pass++) {
      var contentTop = thread.scrollTop + browserRect.top - threadRect.top - gap;
      var contentBottom = thread.scrollTop + browserRect.bottom - threadRect.top + gap;
      var low = 0, high = items.length;
      while (low < high) {
        var mid = Math.floor((low + high) / 2);
        var candidate = items[mid];
        if (candidate.offsetTop + candidate.offsetHeight <= contentTop) low = mid + 1;
        else high = mid;
      }
      var changed = false;
      for (var index = low; index < items.length; index++) {
        var item = items[index];
        if (item.offsetTop >= contentBottom) break;
        var expectedClass = plan.side === "left" ? "wbc-browser-avoid-left" : "wbc-browser-avoid-right";
        if (item.classList.contains(expectedClass)) continue;
        item.classList.add(expectedClass);
        item.style.setProperty("--wbc-browser-avoid-start", Math.round(plan.start) + "px");
        item.style.setProperty("--wbc-browser-avoid-end", Math.round(plan.end) + "px");
        changed = true;
      }
      if (!changed) break;
      restoreViewport();
    }
    scheduleStickyViewportRestore();
  }, [scheduleStickyViewportRestore]);

  var scheduleBrowserAvoidance = useWbcCallback(function () {
    if (avoidanceRafRef.current) return;
    avoidanceRafRef.current = requestAnimationFrame(function () {
      avoidanceRafRef.current = 0;
      // Width changes while a wheel/trackpad gesture is active alter message
      // heights and fight the browser's scroll position. Keep the current lane
      // assignment stable until the gesture settles, then recompute once. Once
      // it has settled, preserve either the live-tail bottom or the reader's
      // first visible entry because the narrower lane can increase row height.
      if (avoidanceScrollingRef.current) return;
      applyBrowserAvoidance(true);
    });
  }, [applyBrowserAvoidance]);

  // Track whether the user is reading scrollback; only auto-stick near bottom.
  function onScroll() {
    var el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setShowScrollToBottom(!stickRef.current);
    if (!stickRef.current && stickyRestoreRafRef.current) {
      cancelAnimationFrame(stickyRestoreRafRef.current);
      stickyRestoreRafRef.current = 0;
    }
    // A wheel/trackpad gesture owns both scrollTop and the visible message
    // anchor. Do not change avoided message widths during the gesture: their
    // height reflow would make the transcript jump in the opposite direction.
    avoidanceScrollingRef.current = true;
    if (avoidanceScrollTimerRef.current) clearTimeout(avoidanceScrollTimerRef.current);
    avoidanceScrollTimerRef.current = setTimeout(function () {
      avoidanceScrollTimerRef.current = null;
      avoidanceScrollingRef.current = false;
      scheduleStickyViewportRestore();
      scheduleBrowserAvoidance();
    }, 120);
  }

  useWbcEffect(function () {
    var el = scrollRef.current;
    if (el && stickRef.current) {
      el.scrollTop = el.scrollHeight;
      scheduleStickyViewportRestore();
    }
  }, [messages.length, runtime && runtime.text, runtime && runtime.progress.length, runtime && runtime.activities && runtime.activities.length, runtime && runtime.segments && runtime.segments.length]);

  useWbcEffect(function () {
    stickRef.current = true;
    setShowScrollToBottom(false);
    var el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    scheduleStickyViewportRestore();
    scheduleBrowserAvoidance();
  }, [chat && chat.id]);

  useWbcEffect(function () {
    var stage = stageRef.current;
    var thread = scrollRef.current;
    if (!stage || !thread) return undefined;
    var observedItems = typeof WeakSet === "function" ? new WeakSet() : null;
    var itemObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(function () {
          if (avoidanceApplyingRef.current) return;
          scheduleStickyViewportRestore();
          scheduleBrowserAvoidance();
        })
      : null;
    function observeItems() {
      if (!itemObserver) return;
      thread.querySelectorAll(":scope > [data-wbc-thread-item]").forEach(function (item) {
        if (observedItems && observedItems.has(item)) return;
        if (observedItems) observedItems.add(item);
        itemObserver.observe(item);
      });
    }
    observeItems();
    var stageObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(function () {
          if (avoidanceApplyingRef.current) return;
          scheduleStickyViewportRestore();
          scheduleBrowserAvoidance();
        })
      : null;
    if (stageObserver) stageObserver.observe(stage);
    var mutationObserver = typeof MutationObserver === "function"
      ? new MutationObserver(function () {
          observeItems();
          scheduleStickyViewportRestore();
          scheduleBrowserAvoidance();
        })
      : null;
    if (mutationObserver) mutationObserver.observe(thread, { childList: true, subtree: true, characterData: true });
    window.addEventListener("workbench:browser-layout", scheduleBrowserAvoidance);
    window.addEventListener("resize", scheduleBrowserAvoidance);
    scheduleBrowserAvoidance();
    return function () {
      if (avoidanceRafRef.current) cancelAnimationFrame(avoidanceRafRef.current);
      avoidanceRafRef.current = 0;
      if (avoidanceApplyingRafRef.current) cancelAnimationFrame(avoidanceApplyingRafRef.current);
      avoidanceApplyingRafRef.current = 0;
      avoidanceApplyingRef.current = false;
      if (stickyRestoreRafRef.current) cancelAnimationFrame(stickyRestoreRafRef.current);
      stickyRestoreRafRef.current = 0;
      avoidanceScrollingRef.current = false;
      if (avoidanceScrollTimerRef.current) clearTimeout(avoidanceScrollTimerRef.current);
      avoidanceScrollTimerRef.current = null;
      if (itemObserver) itemObserver.disconnect();
      if (stageObserver) stageObserver.disconnect();
      if (mutationObserver) mutationObserver.disconnect();
      window.removeEventListener("workbench:browser-layout", scheduleBrowserAvoidance);
      window.removeEventListener("resize", scheduleBrowserAvoidance);
    };
  }, [scheduleBrowserAvoidance, scheduleStickyViewportRestore, project && project.id]);

  useWbcEffect(function () {
    scheduleBrowserAvoidance();
  }, [messages.length, runtime && runtime.text, runtime && runtime.progress && runtime.progress.length, runtime && runtime.activities && runtime.activities.length, browserVisible, browserWindowMode, sideVisible]);

  useWbcEffect(function () {
    var thread = scrollRef.current;
    if (!thread || !onAskSelection || isLegacy) {
      setSelectionMenu(null);
      return undefined;
    }

    function readSelection() {
      var selection = window.getSelection && window.getSelection();
      if (!selection || selection.isCollapsed || !selection.rangeCount) {
        setSelectionMenu(null);
        return;
      }
      if (
        !selection.anchorNode
        || !selection.focusNode
        || !thread.contains(selection.anchorNode)
        || !thread.contains(selection.focusNode)
      ) {
        setSelectionMenu(null);
        return;
      }
      var text = String(selection.toString() || "").trim().slice(0, 12000);
      if (!text) {
        setSelectionMenu(null);
        return;
      }
      var range = selection.getRangeAt(0);
      var rect = range.getBoundingClientRect();
      if (!rect || (!rect.width && !rect.height)) {
        var rects = range.getClientRects();
        rect = rects && rects.length ? rects[rects.length - 1] : null;
      }
      if (!rect) {
        setSelectionMenu(null);
        return;
      }
      var placeBelow = rect.top < 64;
      setSelectionMenu({
        text: text,
        left: Math.max(92, Math.min(window.innerWidth - 92, rect.left + rect.width / 2)),
        top: placeBelow ? Math.min(window.innerHeight - 12, rect.bottom + 10) : Math.max(12, rect.top - 10),
        placement: placeBelow ? "below" : "above",
      });
    }

    function handlePointerUp(event) {
      if (selectionMenuRef.current && selectionMenuRef.current.contains(event.target)) return;
      window.setTimeout(readSelection, 0);
    }
    function handleKeyUp(event) {
      if (event.key === "Escape") {
        setSelectionMenu(null);
        return;
      }
      if (event.shiftKey || event.key.indexOf("Arrow") >= 0) {
        window.setTimeout(readSelection, 0);
      }
    }
    function closeOutside(event) {
      if (selectionMenuRef.current && selectionMenuRef.current.contains(event.target)) return;
      setSelectionMenu(null);
    }
    function closeMenu() { setSelectionMenu(null); }

    thread.addEventListener("pointerup", handlePointerUp);
    thread.addEventListener("keyup", handleKeyUp);
    thread.addEventListener("scroll", closeMenu, { passive: true });
    document.addEventListener("pointerdown", closeOutside, true);
    window.addEventListener("resize", closeMenu);
    return function () {
      thread.removeEventListener("pointerup", handlePointerUp);
      thread.removeEventListener("keyup", handleKeyUp);
      thread.removeEventListener("scroll", closeMenu);
      document.removeEventListener("pointerdown", closeOutside, true);
      window.removeEventListener("resize", closeMenu);
    };
  }, [chat && chat.id, onAskSelection, isLegacy]);

  function askAboutSelection() {
    if (!selectionMenu || sideAgentCreating) return;
    var selectedText = selectionMenu.text;
    setSelectionMenu(null);
    var selection = window.getSelection && window.getSelection();
    if (selection) selection.removeAllRanges();
    onAskSelection(selectedText);
  }

  function scrollToConversationBottom() {
    var el = scrollRef.current;
    if (!el) return;
    stickRef.current = true;
    setShowScrollToBottom(false);
    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({ top: el.scrollHeight, behavior: reducedMotion ? "auto" : "smooth" });
    scheduleStickyViewportRestore();
  }

  function handleChatDragEnter(event) {
    if (!wbcHasChatDrag(event)) return;
    event.preventDefault();
    setChatDropActive(true);
  }

  function handleChatDragOver(event) {
    if (!wbcHasChatDrag(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    setChatDropActive(true);
  }

  function handleChatDragLeave(event) {
    if (event.currentTarget.contains(event.relatedTarget)) return;
    setChatDropActive(false);
  }

  function handleChatDrop(event) {
    if (!wbcHasChatDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    setChatDropActive(false);
    var payload = wbcReadChatDrag(event);
    if (payload && onOpenDroppedChat) onOpenDroppedChat(payload.id);
  }

  if (!project) {
    return <main className="wbc-main"><div className="workbench-empty">{wbcT("workbenchChat.noProject", "Select a project first.")}</div></main>;
  }

  return (
    <main
      ref={mainRef}
      className={"wbc-main" + (chatDropActive ? " chat-drop-active" : "")}
      onDragEnter={handleChatDragEnter}
      onDragOver={handleChatDragOver}
      onDragLeave={handleChatDragLeave}
      onDrop={handleChatDrop}
    >
      {chatDropActive && (
        <div className="wbc-chat-open-drop-hint" role="status">
          {wbcT("workbenchChat.dropToOpen", "Release to open this conversation")}
        </div>
      )}
      {error && <WbcErrorNotice message={error} kind={errorKind} onRetry={onRetry} />}
      <div
        className={"wbc-thread-stage" + (browserWindowMode === "maximized" ? " browser-window-maximized" : "")}
        ref={stageRef}
      >
      <div
        className="wbc-thread"
        ref={scrollRef}
        onScroll={onScroll}
        onContextMenu={onConversationContextMenu}
      >
        {loading && !chat && (
          <div className="wbc-empty-thread wbc-loading-thread" role="status">
            <span className="wbc-spinner" aria-hidden="true"></span>
            <b>{wbcT("workbenchChat.loadingConversation", "Loading conversation…")}</b>
          </div>
        )}
        {messages.length === 0 && !runtime && !loading && !error && (
          <div className="wbc-empty-thread">
            <div className="wbc-empty-icon">{WBC_ICONS.chat}</div>
            <b>{wbcT("workbenchChat.emptyTitle", "Start a new chat")}</b>
            <p>{wbcT("workbenchChat.emptyBody", "Chats are bound to the current workspace. The agent can read project context, and work can be converted into a task when needed.")}</p>
          </div>
        )}
        {messages.map(function (msg) {
          var canRetryAssistant = !isLegacy && !running && String(msg.id || "") === lastAssistantId;
          var canRetryUser = !isLegacy && !running && msg.role === "user" && String(msg.id || "") === lastUserId;
          var canEdit = !isLegacy && !running && msg.role === "user" && !!onEditMessage;
          var isActiveQuestion = !!(
            msg.questionPrompt
            && chat.pendingQuestion
            && String(chat.pendingQuestion.id || "") === String(msg.questionId || "")
          );
          if (msg.runtimeHeartbeat) {
            return <WbcThreadItem key={msg.id}><WbcHeartbeat startedAt={runtime && runtime.startedAt} lastEventAt={runtime && runtime.lastEventAt} finalizing={!!msg.runtimeFinalizing} /></WbcThreadItem>;
          }
          if (msg.runtimeActivity || msg.activityCard) {
            var activity = msg.runtimeActivity || {
              id: msg.id,
              reasoning: msg.reasoning || "",
              progress: Array.isArray(msg.trace) ? msg.trace : [],
            };
            var activityEntries = Array.isArray(activity.progress) ? activity.progress : [];
            // A tool-free thinking card is useful only while it is the live
            // phase. Once completed, omit it instead of leaving a durable
            // "thinking complete" placeholder between messages.
            if (!msg.runtimeActivityActive && activityEntries.length === 0) return null;
            return (
              <WbcThreadItem key={msg.id}>
                <WbcLiveActivityCard
                  activity={activity}
                  active={!!msg.runtimeActivityActive}
                  hasReplyText={!!msg.runtimeActivityHasReplyText}
                />
              </WbcThreadItem>
            );
          }
          if (isActiveQuestion) {
            return <WbcThreadItem key={msg.id}><WbcQuestionPrompt pending={chat.pendingQuestion} onAnswer={onAnswer} busy={running} trace={msg.trace} /></WbcThreadItem>;
          }
          var messageTraceKey = wbcTraceDedupeKey(msg.trace);
          var visibleMessage = messageTraceKey && activityTraceKeys.has(messageTraceKey)
            ? { ...msg, trace: [] }
            : msg;
          return (
            <WbcThreadItem key={msg.id} navigation={msg.role === "user" ? wbcUserMessageNavigationMeta(msg) : null}>
              {msg.role === "user"
                ? <WbcUserMessage msg={visibleMessage} onOpenFile={onOpenFile} onEditMessage={onEditMessage} canEdit={canEdit} onRetryMessage={canRetryUser ? onRetryMessage : null} />
                : <WbcAssistantMessage msg={visibleMessage} onOpenFile={onOpenFile} onRetryMessage={canRetryAssistant ? onRetryMessage : null} />}
            </WbcThreadItem>
          );
        })}
        {runtime && runtime.text && <WbcThreadItem><WbcLiveMessage runtime={runtime} onOpenFile={onOpenFile} /></WbcThreadItem>}
        {chat && chat.pendingQuestion && chat.pendingQuestion.id && !runtime && !messages.some(function (msg) {
          return msg.questionPrompt && String(msg.questionId || "") === String(chat.pendingQuestion.id || "");
        }) && (
          <WbcThreadItem><WbcQuestionPrompt pending={chat.pendingQuestion} onAnswer={onAnswer} busy={running} /></WbcThreadItem>
        )}
      </div>
      <WbcConversationNavigator threadRef={scrollRef} chatId={chat && chat.id} />
      {selectionMenu && (
        <div
          ref={selectionMenuRef}
          className={"wbc-selection-menu " + selectionMenu.placement}
          style={{ left: selectionMenu.left + "px", top: selectionMenu.top + "px" }}
          role="toolbar"
          aria-label={wbcT("workbenchChat.selection.actions", "Selection actions")}
        >
          <button
            type="button"
            onMouseDown={function (event) { event.preventDefault(); }}
            onClick={askAboutSelection}
            disabled={sideAgentCreating}
          >
            <span aria-hidden="true">{WBC_ICONS.chat}</span>
            <span>{sideAgentCreating
              ? wbcT("workbenchChat.sideAgent.creating", "Creating…")
              : wbcT("workbenchChat.selection.askInSidebar", "Ask in sidebar")}</span>
          </button>
        </div>
      )}
      {showScrollToBottom && (
        <button
          type="button"
          className="wbc-scroll-to-bottom"
          onClick={scrollToConversationBottom}
          title={wbcT("workbenchChat.navigation.backToBottom", "Back to latest message")}
          aria-label={wbcT("workbenchChat.navigation.backToBottom", "Back to latest message")}
        >
          <span aria-hidden="true">{WBC_ICONS.chevronsRight}</span>
        </button>
      )}
      <div className="wbc-browser-movement-region">
        <WbcBrowserFloatingSurface
          browserState={browserState}
          browserSessionId={browserSessionId}
          visible={floatingBrowserVisible}
          mode={browserWindowMode}
          runtime={runtime}
          running={running}
          latestAssistantReplyId={latestAssistantReplyId}
          latestAssistantReplyText={latestAssistantReplyText}
          onSend={onSend}
          onGuidance={onGuidance}
          onInterrupt={onInterrupt}
          onMinimize={onBrowserMinimize}
          onMaximize={onBrowserMaximize}
          onRestore={onBrowserRestore}
          onTakeoverComplete={onBrowserTakeoverComplete}
        />
      </div>
      </div>
      <WbcComposer
        chat={chat}
        project={project}
        runtime={runtime}
        running={running}
        error={error}
        errorKind={errorKind}
        onSend={onSend}
        onGuidance={onGuidance}
        onInterrupt={onInterrupt}
        hideDisclaimer={splitOpen}
      />
    </main>
  );
}

// A paused chat run awaiting the user's answer to a permission elevation or a
// clarification (ask_user). Renders the question + option buttons inline at the
// bottom of the thread; each answer resumes the same round server-side.
function WbcQuestionPrompt({ pending, onAnswer, busy, trace }) {
  var pq = pending || {};
  var options = Array.isArray(pq.options) ? pq.options : [];
  var kind = String(pq.kind || "");
  var isPermission = window.CyreneUI.require("model").isPermissionQuestionKind(kind);
  var isPlanConfirmation = kind === "plan_confirmation";
  var customState = useWbcState("");
  var customText = customState[0], setCustomText = customState[1];
  function submitCustom() {
    var t = String(customText || "").trim();
    if (!t || busy || !onAnswer) return;
    setCustomText("");
    onAnswer(pq.id, t);
  }
  return (
    <div className="wbc-question-group">
      {trace && trace.length > 0 && <WbcTraceCard trace={trace} />}
      <div className="wbc-question">
        <div className="wbc-question-head">
          <span className="wbc-question-ico">{WBC_ICONS.alert}</span>
          <b>{isPermission ? wbcT("workbenchChat.permissionTitle", "Authorization needed") : wbcT("workbenchChat.questionTitle", "Confirmation needed")}</b>
        </div>
        <p className="wbc-question-text">{pq.text || wbcT("workbenchChat.questionFallback", "Agent needs your confirmation to continue.")}</p>
        {isPermission ? (
          <div className="wbc-question-options">
            {(options.length ? options : ["在本次会话同意", "同意一次", "拒绝"]).map(function (opt, i) {
              return <button key={i} type="button" className={"wbc-question-opt" + (i === 0 ? " primary" : "")} disabled={busy} onClick={function () { if (!busy && onAnswer) onAnswer(pq.id, opt); }}>{opt}</button>;
            })}
          </div>
        ) : (
          <React.Fragment>
            {isPlanConfirmation && options.length > 0 ? (
              <div className="wbc-question-options">
                <button type="button" className="wbc-question-opt primary" disabled={busy} onClick={function () { if (!busy && onAnswer) onAnswer(pq.id, options[0], "auto"); }}>
                  {options[0] || wbcT("workbenchChat.approveAuto", "Confirm and continue in Auto")}
                </button>
                <button type="button" className="wbc-question-opt" disabled={busy} onClick={function () { if (!busy && onAnswer) onAnswer(pq.id, options.length ? options[options.length - 1] : "拒绝"); }}>
                  {options.length ? options[options.length - 1] : wbcT("workbenchChat.reject", "Reject")}
                </button>
              </div>
            ) : options.length > 0 && (
              <div className="wbc-question-options">
                {options.map(function (opt, i) {
                  return <button key={i} type="button" className={"wbc-question-opt" + (i === 0 ? " primary" : "")} disabled={busy} onClick={function () { if (!busy && onAnswer) onAnswer(pq.id, opt); }}>{opt}</button>;
                })}
              </div>
            )}
            {pq.allowCustom && (
              <div className="wbc-question-custom">
                <input type="text" value={customText} placeholder={wbcT("workbenchChat.customAnswer", "Or enter a custom reply...")} disabled={busy}
                  onChange={function (e) { setCustomText(e.target.value); }}
                  onKeyDown={function (e) { if (e.key === "Enter") { e.preventDefault(); submitCustom(); } }} />
                <button type="button" className="wbc-question-send" disabled={busy || !String(customText).trim()} onClick={submitCustom}>{WBC_ICONS.send}</button>
              </div>
            )}
          </React.Fragment>
        )}
      </div>
    </div>
  );
}

function WbcErrorNotice({ message, kind, onRetry }) {
  var isMessageError = kind === "message";
  var title = isMessageError
    ? wbcT("workbenchChat.error.messageTitle", "Message processing failed")
    : wbcT("workbenchChat.error.title", "Could not load this chat");
  var detail = String(message || "").trim() || wbcT("workbenchChat.error.loadFailed", "Load failed");
  var generic = wbcT("workbenchChat.error.loadFailed", "Load failed");
  var body = detail === generic
    ? (isMessageError
      ? wbcT("workbenchChat.error.messageBody", "The message was saved but could not be processed. Retry to run it again.")
      : wbcT("workbenchChat.error.body", "The conversation data did not load. Check the local service and try again."))
    : detail;
  return (
    <div className="workbench-error wbc-error-card" role="alert">
      <span className="wbc-error-icon">{WBC_ICONS.alert}</span>
      <span className="wbc-error-copy">
        <b>{title}</b>
        <small>{body}</small>
      </span>
      {onRetry && (
        <button type="button" className="wbc-error-retry" onClick={onRetry}>
          {wbcT("workbenchChat.error.retry", "Retry")}
        </button>
      )}
    </div>
  );
}

function WbcHeader({ project, chat, running, finalizing, onRename, onDelete, onToTask, toTaskBusy }) {
  var [editing, setEditing] = useWbcState(false);
  var [draft, setDraft] = useWbcState(chat.title || "");
  var [menuOpen, setMenuOpen] = useWbcState(false);
  var inputRef = useWbcRef(null);

  useWbcEffect(function () {
    setDraft(chat.title || "");
    setEditing(false);
    setMenuOpen(false);
  }, [chat.id]);

  useWbcEffect(function () {
    if (editing && inputRef.current) { inputRef.current.focus(); inputRef.current.select(); }
  }, [editing]);

  function commitTitle() {
    var next = String(draft || "").trim();
    setEditing(false);
    if (!next || next === chat.title) { setDraft(chat.title || ""); return; }
    onRename(next).catch(function (err) {
      window.CyreneUI.require("feedback").showToast(err.message || String(err), "error");
      setDraft(chat.title || "");
    });
  }

  var isLegacy = !!chat.legacy;
  var statusText = isLegacy
    ? wbcT("workbenchChat.status.archived", "Archived")
    : finalizing
      ? wbcT("workbenchChat.status.saving", "Saving")
      : running ? wbcT("workbenchChat.status.replying", "Replying") : wbcT("workbenchChat.status.idle", "Idle");

  return (
    <div className="wbc-header">
      <div className="wbc-header-info">
        <div className="wbc-header-title">
          {editing ? (
            <input
              ref={inputRef}
              className="wbc-title-input"
              value={draft}
              onChange={function (e) { setDraft(e.target.value); }}
              onBlur={commitTitle}
              onKeyDown={function (e) {
                if (e.key === "Enter") commitTitle();
                if (e.key === "Escape") { setDraft(chat.title || ""); setEditing(false); }
              }}
              aria-label={wbcT("workbenchChat.titleLabel", "Chat title")}
            />
          ) : (
            <h1 title={chat.title}>{chat.title || wbcT("workbenchChat.newChat", "New chat")}</h1>
          )}
          {!editing && !isLegacy && (
            <button type="button" className="wbc-icon-btn" title={wbcT("workbenchChat.rename", "Rename chat")} onClick={function () { setEditing(true); }}>
              {WBC_ICONS.edit}
            </button>
          )}
        </div>
        <div className="wbc-header-meta">
          <span className={"wbc-status-chip" + (running ? " running" : "")}>{statusText}</span>
          <span>{chat.model || "—"}</span>
          <span>{project.name}</span>
        </div>
      </div>
      <div className="wbc-header-actions">
        {!isLegacy && (
          <button type="button" className={"wb-btn primary wbc-totask" + (toTaskBusy ? " is-busy" : "")} disabled={running || toTaskBusy} onClick={onToTask} title={wbcT("workbenchChat.toTaskTitle", "Create a task from this chat")}>
            {toTaskBusy
              ? <><span className="wbc-spinner" aria-hidden="true"></span><span>{wbcT("workbenchChat.toTaskBusy", "Analyzing chat…")}</span></>
              : <>{WBC_ICONS.play}<span>{wbcT("workbenchChat.toTask", "Convert to task")}</span></>}
          </button>
        )}
        {!isLegacy && (
          <div className="wbc-menu-wrap">
            <button type="button" className="wbc-icon-btn" title={wbcT("workbenchChat.more", "More")} onClick={function () { setMenuOpen(!menuOpen); }}>
              {WBC_ICONS.dots}
            </button>
            {menuOpen && (
              <>
                <div className="wbc-menu-scrim" onClick={function () { setMenuOpen(false); }}></div>
                <div className="wbc-menu">
                  <button type="button" onClick={function () { setMenuOpen(false); setEditing(true); }}>{WBC_ICONS.edit}<span>{wbcT("workbenchChat.rename", "Rename chat")}</span></button>
                  <button type="button" disabled={toTaskBusy} onClick={function () { setMenuOpen(false); onToTask(); }}>{WBC_ICONS.task}<span>{wbcT(toTaskBusy ? "workbenchChat.toTaskBusy" : "workbenchChat.toTask", toTaskBusy ? "Analyzing chat…" : "Convert to task")}</span></button>
                  <button type="button" className="danger" onClick={function () { setMenuOpen(false); onDelete(); }}>{WBC_ICONS.trash}<span>{wbcT("workbenchChat.delete", "Delete chat")}</span></button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function WbcMessageAttachment({ file, onOpenFile }) {
  var [imageFailed, setImageFailed] = useWbcState(false);
  var isImg = file.kind === "image" || String(file.content_type || "").indexOf("image") === 0;
  var open = function () { if (onOpenFile && file.url) onOpenFile(file); };

  if (isImg && file.url && !imageFailed) {
    return (
      <div
        className="wbc-inline-image"
        draggable="true"
        onDragStart={function (event) { wbcStartFileDrag(event, file); }}
      >
        <button
          type="button"
          className="wbc-inline-image-preview"
          onClick={open}
          title={wbcT("workbenchChat.viewInSide", "View on the right")}
        >
          <img
            src={file.url}
            alt={file.name || wbcT("workbenchChat.attachmentType.image", "Image")}
            draggable="false"
            onError={function () { setImageFailed(true); }}
          />
        </button>
        <div className="wbc-inline-image-footer">
          <b title={file.name}>{file.name || "image"}</b>
          <span className="wbc-inline-image-actions">
            {wbcCanOpenExternally(file) ? (
              <a
                className="wbc-inline-image-action"
                href={file.url}
                target="_blank"
                rel="noreferrer"
                draggable="false"
                title={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}
                aria-label={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}
              >{WBC_ICONS.openExternal}</a>
            ) : null}
            {wbcDownloadLink(file, {
              className: "wbc-inline-image-action",
              draggable: "false",
              "aria-label": wbcT("workbenchChat.download", "Download"),
            })}
          </span>
        </div>
      </div>
    );
  }
  var canOpen = !!(onOpenFile && file.url);
  function startFileDrag(event) {
    wbcStartFileDrag(event, file);
  }
  var content = (
    <>
      <WbcFileVisual file={file} />
      <span className="wbc-attach-file-meta">
        <b title={file.name}>{file.name || "file"}</b>
        <small>{wbcAttachmentTypeLabel(file)}</small>
      </span>
      {canOpen ? (
        <span className="wbc-attach-file-open">
          <span>{wbcT("workbenchChat.openPreview", "Open preview")}</span>
          {WBC_ICONS.chevronsRight}
        </span>
      ) : null}
    </>
  );
  if (!canOpen) return <div className="wbc-attach-file" draggable="true" onDragStart={startFileDrag}>{content}</div>;
  return (
    <button type="button" className="wbc-attach-file" draggable="true" onDragStart={startFileDrag} onClick={open} title={wbcT("workbenchChat.viewInSide", "View on the right")}>
      {content}
    </button>
  );
}

function WbcUserMessage({ msg, onOpenFile, onEditMessage, canEdit, onRetryMessage }) {
  var attachments = Array.isArray(msg.attachments) ? msg.attachments : [];
  var [editing, setEditing] = useWbcState(false);
  var [draft, setDraft] = useWbcState(String(msg.content || ""));
  var taRef = useWbcRef(null);

  useWbcEffect(function () {
    if (editing && taRef.current) {
      taRef.current.style.height = "auto";
      taRef.current.style.height = Math.min(taRef.current.scrollHeight, 240) + "px";
      taRef.current.focus();
      taRef.current.setSelectionRange(taRef.current.value.length, taRef.current.value.length);
    }
  }, [editing]);

  function startEdit(e) {
    e.stopPropagation();
    setDraft(String(msg.content || ""));
    setEditing(true);
  }
  function cancelEdit() {
    setEditing(false);
    setDraft(String(msg.content || ""));
  }
  function saveEdit() {
    var text = String(draft || "").trim();
    if (!text || !onEditMessage) { setEditing(false); return; }
    if (text === String(msg.content || "").trim()) { setEditing(false); return; }
    setEditing(false);
    onEditMessage(msg.id, text);
  }
  function onEditKeyDown(event) {
    var sc = window.CyreneUI.require("shortcuts");
    if (sc && sc.matches(event, "composer-send")) {
      if (event.nativeEvent && event.nativeEvent.isComposing) return;
      event.preventDefault();
      saveEdit();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      cancelEdit();
    }
  }

  if (editing) {
    return (
      <div className="wbc-msg user editing">
        <div className="wbc-bubble wbc-edit-bubble">
          <textarea
            ref={taRef}
            className="wbc-edit-textarea"
            value={draft}
            onChange={function (e) { setDraft(e.target.value); }}
            onKeyDown={onEditKeyDown}
            placeholder={wbcT("workbenchChat.editPlaceholder", "Edit your message...")}
          />
          {attachments.length > 0 && (
            <div className={"wbc-msg-attachments" + (draft.trim() ? " after-copy" : "")}>
              {attachments.map(function (file, i) {
                return <WbcMessageAttachment key={file.id || file.url || i} file={file} onOpenFile={onOpenFile} />;
              })}
            </div>
          )}
          <div className="wbc-edit-actions">
            <button type="button" className="wb-btn ghost" onClick={cancelEdit}>{wbcT("common.cancel", "Cancel")}</button>
            <button type="button" className="wb-btn primary" onClick={saveEdit} disabled={!draft.trim()}>{wbcT("workbenchChat.editSave", "Save & send")}</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="wbc-msg user">
      <div className="wbc-msg-row">
        <time>{wbcFormatTime(msg.createdAt)}</time>
        <div className="wbc-bubble">
          {msg.content ? <p>{msg.content}</p> : null}
          {attachments.length > 0 && (
            <div className={"wbc-msg-attachments" + (msg.content ? " after-copy" : "")}>
              {attachments.map(function (file, i) {
                return <WbcMessageAttachment key={file.id || file.url || i} file={file} onOpenFile={onOpenFile} />;
              })}
            </div>
          )}
        </div>
      </div>
      {((canEdit && onEditMessage) || onRetryMessage) && (
        <div className="wbc-msg-foot wbc-user-foot">
          {canEdit && onEditMessage && (
            <button type="button" className="wbc-msg-action wbc-edit-btn" onClick={startEdit} title={wbcT("workbenchChat.editMessage", "Edit & branch")}>
              {WBC_ICONS.edit}
            </button>
          )}
          {onRetryMessage && (
            <button type="button" className="wbc-msg-action" onClick={onRetryMessage} title={wbcT("workbenchChat.retryUserMessage", "Retry message")}>
              {WBC_ICONS.retry}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// Files the agent produced in this reply — rendered like the reference's
// artifact card, with a 查看 action that opens the side viewer.
function WbcAgentFiles({ files, onOpenFile }) {
  if (!files || !files.length) return null;
  return (
    <div className="wbc-agent-files">
      {files.map(function (file, i) {
        if (wbcFileViewKind(file) === "image" && file.url) {
          return <WbcMessageAttachment key={file.id || file.url || i} file={file} onOpenFile={onOpenFile} />;
        }
        return (
          <div
            className="wbc-agent-file"
            key={file.id || file.url || i}
            draggable="true"
            onDragStart={function (event) { wbcStartFileDrag(event, file); }}
          >
            <span className="wbc-file-icon">{WBC_ICONS.file}</span>
            <span className="wbc-file-meta">
              <b title={file.name}>{file.name || "file"}</b>
              <small>{file.content_type || ""}</small>
            </span>
            <span className="wbc-agent-file-actions">
              <button type="button" className="wb-btn ghost" onClick={function () { onOpenFile && onOpenFile(file); }}>{wbcT("workbenchChat.viewer", "Viewer")}</button>
              {wbcCanOpenExternally(file) ? <a className="wb-btn ghost" href={file.url} target="_blank" rel="noreferrer" title={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}>↗</a> : null}
              {wbcDownloadLink(file)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function WbcTraceCard({ trace, live, running, label, reasoning, showReasoning, onToggle, cardRef, reasoningRef, lockedHeight }) {
  var entries = Array.isArray(trace) ? trace : [];
  if (!entries.length && !live) return null;
  var interactive = live && typeof onToggle === "function";
  var activityRunning = live && running !== false;
  var cardClass = "wbc-trace" + (live ? " live" : "") + (interactive ? " wbc-trace-interactive" : "") + (lockedHeight ? " wbc-trace-locked" : "") + (showReasoning ? " showing-reasoning" : "");
  var toggleLabel = showReasoning
    ? wbcT("workbenchChat.showActivity", "Show thinking or tool activity")
    : wbcT("workbenchChat.showReasoning", "Show live reasoning");
  function handleKeyDown(event) {
    if (!interactive || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    onToggle();
  }
  return (
    <div
      className={cardClass}
      ref={cardRef}
      style={lockedHeight ? { height: lockedHeight + "px" } : null}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-label={interactive ? toggleLabel : undefined}
      aria-pressed={interactive ? !!showReasoning : undefined}
      title={interactive ? toggleLabel : undefined}
      onClick={interactive ? onToggle : undefined}
      onKeyDown={interactive ? handleKeyDown : undefined}
    >
      {showReasoning ? (
        <div className="wbc-thinking-detail" aria-live="polite">
          {activityRunning ? <span className="wb-spinner small" aria-hidden="true" /> : null}
          <span className="wbc-thinking-detail-text" ref={reasoningRef}>
            {reasoning || wbcT("workbenchChat.reasoningPending", "Waiting for live reasoning...")}
          </span>
        </div>
      ) : (
        <div className="wbc-trace-view">
          <div className="wbc-trace-head">
            {activityRunning && entries.length === 0 ? <span className="wb-spinner" /> : (!live ? <span className="wbc-trace-icon">{WBC_ICONS.tool}</span> : null)}
            <b>{label || (live ? wbcT("workbenchChat.traceIdle", "Thinking...") : wbcT("workbenchChat.traceSummary", "Execution ({count} tool calls)", { count: entries.length }))}</b>
          </div>
          {entries.length > 0 && (
            <ul className="wbc-trace-list">
              {entries.map(function (entry, i) {
                var isRunning = activityRunning && entry.status === "running";
                var failed = !!entry.failed;
                return (
                  <li key={entry.toolCallId || i} className={failed ? "failed" : (isRunning ? "active" : "done")}>
                    <span className="wbc-trace-mark">{failed ? WBC_ICONS.x : (isRunning ? <span className="wb-spinner small" /> : WBC_ICONS.check)}</span>
                    <span className="wbc-trace-text">
                      {(function () {
                        var toolKey = entry.text || entry.tool || "";
                        var isToolEntry = entry.kind === "tool" || !!entry.tool;
                        if (isToolEntry) return wbcT("toolName." + toolKey, toolKey);
                        if (entry.detailKey) return wbcT(entry.detailKey, toolKey, entry.detailParams);
                        return toolKey;
                      })()}
                      {(entry.preview) ? <small>（{wbcToolPreviewText(entry.preview)}）</small> : null}
                      {isRunning && Number(entry.progressTotal) > 0 ? (
                        <span className="wbc-transfer-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(Number(entry.progress || 0) * 100)}>
                          <span style={{ width: Math.round(Number(entry.progress || 0) * 100) + "%" }} />
                          <small>{Math.round(Number(entry.progress || 0) * 100)}%</small>
                        </span>
                      ) : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function WbcAssistantMessage({ msg, onOpenFile, onRetryMessage }) {
  var [copied, setCopied] = useWbcState(false);
  var processingDuration = wbcFormatProcessingDuration(msg.processingDurationMs);
  // Parse each finalized message's markdown once and reuse it: the whole thread
  // re-renders on every streaming frame, so without this every prior message
  // would be re-parsed + re-sanitized per frame.
  var bodyHtml = useWbcMemo(function () { return wbcRenderMarkdown(msg.content); }, [msg.content]);
  var bodyRef = useWbcRef(null);
  useWbcEffect(function () {
    if (!bodyRef.current) return undefined;
    var chartService = window.CyreneUI && window.CyreneUI.chart;
    if (chartService && typeof chartService.mount === "function") chartService.mount(bodyRef.current);
    return function () {
      if (chartService && typeof chartService.dispose === "function") chartService.dispose(bodyRef.current);
    };
  }, [bodyHtml]);
  async function copyText() {
    try {
      var text = String(msg.content || "");
      if (window.cyrene && typeof window.cyrene.writeClipboardText === "function") {
        window.cyrene.writeClipboardText(text);
      } else {
        await navigator.clipboard.writeText(text);
      }
      setCopied(true);
      setTimeout(function () { setCopied(false); }, 1600);
    } catch (e) {
      console.error("Failed to copy workbench message:", e);
    }
  }
  return (
    <div className="wbc-msg assistant">
      {msg.trace && msg.trace.length > 0 && <WbcTraceCard trace={msg.trace} />}
      <div className="wbc-msg-body markdown" ref={bodyRef} dangerouslySetInnerHTML={{ __html: bodyHtml }} />
      <WbcAgentFiles files={msg.attachments} onOpenFile={onOpenFile} />
      <div className="wbc-msg-foot">
        <button type="button" className="wbc-msg-action" onClick={copyText} title={wbcT("workbenchChat.copy", "Copy")}>
          {copied ? WBC_ICONS.check : WBC_ICONS.copy}
        </button>
        {onRetryMessage && (
          <button type="button" className="wbc-msg-action" onClick={onRetryMessage} title={wbcT("workbenchChat.regenerate", "Regenerate")}>
            {WBC_ICONS.retry}
          </button>
        )}
        <time>{wbcFormatTime(msg.createdAt)}</time>
        {processingDuration ? (
          <small
            className="wbc-msg-duration"
            title={wbcT("workbenchChat.processingDuration", "Total processing time")}
          >{processingDuration}</small>
        ) : null}
        {msg.usage && msg.usage.total_tokens ? <small>{wbcCompactNumber(msg.usage.total_tokens)} tokens</small> : null}
      </div>
    </div>
  );
}

// After this many ms with no new sign of life (reply delta, tool call, phase,
// subagent or intermediate message) the live card switches from a plain elapsed
// counter to an explicit "still working…" reassurance, so a quiet stretch reads
// as "alive but busy" rather than "frozen".
var WBC_HEARTBEAT_STALL_MS = 10000;

// Live "heartbeat" for a running reply: a self-ticking elapsed counter plus a
// random thinking phrase that fades out/in when cycling every ~4 s. Self-contained
// interval so it re-renders only itself (a leaf), never the whole conversation.
// Mounts/unmounts with the runtime, so the timer is torn down the moment the run settles.
function WbcHeartbeat({ startedAt, lastEventAt, finalizing }) {
  var heartbeatI18n = useWorkbenchI18n();
  var heartbeatLang = heartbeatI18n.lang;
  var [now, setNow] = useWbcState(function () { return Date.now(); });
  var [displayText, setDisplayText] = useWbcState(wbcRandomThinkingPhrase);
  var [animClass, setAnimClass] = useWbcState("");

  function handleAnimEnd() {
    if (animClass === "wbc-still-leave") {
      setDisplayText(wbcRandomThinkingPhrase());
      setAnimClass("wbc-still-enter");
    } else if (animClass === "wbc-still-enter") {
      setAnimClass("");
    }
  }

  useWbcEffect(function () {
    if (finalizing) return undefined;
    var timer = setInterval(function () {
      setNow(Date.now());
      setAnimClass("wbc-still-leave");
    }, 4000);
    return function () { clearInterval(timer); };
  }, [finalizing]);
  useWbcEffect(function () {
    setDisplayText(wbcRandomThinkingPhrase());
  }, [heartbeatLang]);
  if (!startedAt) return null;
  if (finalizing) {
    return (
      <div className="wbc-heartbeat finalizing" role="status" aria-live="polite">
        <span className="wbc-heartbeat-check" aria-hidden="true">{WBC_ICONS.check}</span>
        <span>{wbcT("workbenchChat.finalizing", "Reply complete · saving results…")}</span>
      </div>
    );
  }
  var elapsed = Math.max(0, Math.round((now - startedAt) / 1000));
  var stalled = !!lastEventAt && (now - lastEventAt) > WBC_HEARTBEAT_STALL_MS;
  return (
    <div className={"wbc-heartbeat" + (stalled ? " stalled" : "")} aria-live="polite">
      <span className="wbc-heartbeat-pulse" />
      <span className="wbc-heartbeat-elapsed">{wbcT("workbenchChat.elapsed", "Running {s}s", { s: elapsed })}</span>
      <span className={"wbc-heartbeat-still" + (animClass ? " " + animClass : "")} onAnimationEnd={handleAnimEnd}>{displayText}</span>
    </div>
  );
}

function wbcPhase1ReasoningPreview(text) {
  var compact = String(text || "").replace(/\s+/g, " ").trim();
  if (!compact) return "";
  return compact.length > 220 ? compact.slice(0, 217).trimEnd() + "…" : compact;
}

function wbcPhase1ProgressDetail(entries) {
  return (Array.isArray(entries) ? entries : []).map(function (entry) {
    var text = String(entry && (entry.text || entry.tool) || "").trim();
    if (entry && entry.detailKey) {
      text = wbcT(entry.detailKey, text, entry.detailParams || {});
    } else if (entry && (entry.kind === "tool" || entry.tool)) {
      text = wbcT("toolName." + text, text);
    }
    var preview = String(entry && entry.preview || "").trim();
    var mark = entry && entry.failed ? "×" : (entry && entry.status === "running" ? "◌" : "✓");
    return [mark, text, preview ? "（" + wbcToolPreviewText(preview) + "）" : ""]
      .filter(Boolean)
      .join(" ");
  }).filter(Boolean).join("\n");
}

function WbcLiveActivityCard({ activity, active, hasReplyText }) {
  var item = activity || {};
  var entries = Array.isArray(item.progress) ? item.progress : [];
  var isPhase1 = String(item.llmPhase || "") === "phase1";
  var phase1Running = isPhase1 && active && String(item.llmStatus || "") !== "completed";
  var phase1Preview = isPhase1 ? wbcPhase1ReasoningPreview(item.reasoning) : "";
  var visibleEntries = entries;
  if (isPhase1) {
    visibleEntries = [{
      kind: "phase1",
      text: phase1Running
        ? wbcT("workbenchChat.phase1Understanding", "Understanding the request")
        : wbcT("workbenchChat.phase1Understood", "Understood the request"),
      preview: phase1Preview,
      status: phase1Running ? "running" : "completed",
    }].concat(entries);
  }
  var hasRunningTools = entries.some(function (entry) {
    return entry && entry.kind === "tool" && entry.status === "running";
  });
  var toolCount = entries.filter(function (entry) {
    return entry && entry.kind === "tool";
  }).length;
  var hasReasoning = !!String(item.reasoning || "").trim();
  var isCodexProvider = String(item.provider || "") === "codex_oauth";
  var phase1Detail = hasReasoning
    ? String(item.reasoning || "")
    : wbcPhase1ProgressDetail(visibleEntries);
  var hasExpandableDetail = !isCodexProvider
    && (hasReasoning || (isPhase1 && !!phase1Detail));
  var [showReasoning, setShowReasoning] = useWbcState(false);
  var [lockedHeight, setLockedHeight] = useWbcState(0);
  var cardRef = useWbcRef(null);
  var reasoningRef = useWbcRef(null);
  function toggleReasoning() {
    if (!hasExpandableDetail) return;
    if (typeof window.getSelection === "function" && String(window.getSelection() || "")) return;
    if (!showReasoning && !lockedHeight && cardRef.current) {
      setLockedHeight(cardRef.current.getBoundingClientRect().height);
    } else if (showReasoning) {
      // The lock exists only to keep the reasoning side the same size as the
      // tool side. Returning to tools must always restore natural layout: the
      // same number of rows can still change height after wrapping or resize.
      setLockedHeight(0);
    }
    setShowReasoning(function (visible) { return !visible; });
  }
  useWbcEffect(function () {
    var detail = reasoningRef.current;
    if (showReasoning && detail) {
      // Follow a live stream, but open completed reasoning from its beginning.
      // Scrolling a one-line locked card to the end can otherwise land on an
      // empty trailing line and make a populated detail look blank.
      detail.scrollTop = active ? detail.scrollHeight : 0;
    }
  }, [item.reasoning, showReasoning, active]);

  var label = toolCount
    ? (hasRunningTools && !hasReplyText
      ? wbcT("workbenchChat.toolRunning", "Calling tools...")
      : wbcT("workbenchChat.traceSummary", "Execution ({count} tool calls)", { count: toolCount }))
    : (isPhase1
      ? wbcT("workbenchChat.phase1Card", "Execution · Phase 1")
      : active
      ? wbcT("workbenchChat.traceIdle", "Thinking...")
      : wbcT("workbenchChat.traceLabel", "Execution"));

  return (
    <WbcTraceCard
      trace={visibleEntries}
      live={true}
      running={isPhase1 ? phase1Running : active}
      reasoning={phase1Detail}
      showReasoning={hasExpandableDetail && showReasoning}
      onToggle={hasExpandableDetail ? toggleReasoning : null}
      cardRef={cardRef}
      reasoningRef={reasoningRef}
      lockedHeight={hasExpandableDetail ? lockedHeight : 0}
      label={label}
    />
  );
}

function WbcLiveMessage({ runtime, onOpenFile }) {
  // Re-parse the streaming markdown only when the text actually changed — not on
  // every heartbeat / progress-driven re-render of this card.
  var liveHtml = useWbcMemo(function () {
    return runtime.text ? wbcRenderMarkdown(runtime.text, { interactive: false }) : "";
  }, [runtime.text]);
  if (!runtime.text) return null;
  return (
    <React.Fragment>
      <div className="wbc-msg assistant">
        <div className="wbc-msg-body markdown">
          <div dangerouslySetInnerHTML={{ __html: liveHtml }} />
          <span className="wbc-caret" />
        </div>
      </div>
    </React.Fragment>
  );
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

var WBC_DRAFT_PREFIX = "cyrene-wbc-draft-";
var WBC_ATTACH_PREFIX = "cyrene-wbc-attach-";
var WBC_WORKSPACE_PREFIX = "cyrene-wbc-workspace-";

function wbcIsPersistableChatId(id) {
  return !!(id && String(id).indexOf("legacy:") !== 0);
}

// The optional `ns` prefix isolates a surface's drafts/attachments/workspace
// from the main chat's (the quick-chat window shares localStorage with the main
// window, so it passes a namespace to avoid clobbering an in-progress draft for
// the same chat id). The persistability gate still tests the raw chat id, so a
// brand-new chat (id "") is never stored regardless of namespace.
function wbcLoadDraft(id, ns) {
  if (!wbcIsPersistableChatId(id)) return "";
  try { return localStorage.getItem(WBC_DRAFT_PREFIX + (ns || "") + id) || ""; } catch (e) { return ""; }
}

function wbcSaveDraft(id, text, ns) {
  if (!wbcIsPersistableChatId(id)) return;
  try {
    if (text) localStorage.setItem(WBC_DRAFT_PREFIX + (ns || "") + id, text);
    else localStorage.removeItem(WBC_DRAFT_PREFIX + (ns || "") + id);
  } catch (e) {}
}

function wbcLoadAttachments(id, ns) {
  if (!wbcIsPersistableChatId(id)) return [];
  try {
    var raw = localStorage.getItem(WBC_ATTACH_PREFIX + (ns || "") + id);
    var parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) { return []; }
}

function wbcSaveAttachments(id, list, ns) {
  if (!wbcIsPersistableChatId(id)) return;
  try {
    if (list && list.length) localStorage.setItem(WBC_ATTACH_PREFIX + (ns || "") + id, JSON.stringify(list));
    else localStorage.removeItem(WBC_ATTACH_PREFIX + (ns || "") + id);
  } catch (e) {}
}

function wbcWorkspaceContextKey(chatId, projectId) {
  return String(projectId || "") + ":" + (wbcIsPersistableChatId(chatId) ? String(chatId) : "__new__");
}

function wbcLoadWorkspaceOverride(key, ns) {
  if (!key) return "";
  try { return localStorage.getItem(WBC_WORKSPACE_PREFIX + (ns || "") + key) || ""; } catch (e) { return ""; }
}

function wbcSaveWorkspaceOverride(key, path, ns) {
  if (!key) return;
  try {
    if (path) localStorage.setItem(WBC_WORKSPACE_PREFIX + (ns || "") + key, path);
    else localStorage.removeItem(WBC_WORKSPACE_PREFIX + (ns || "") + key);
  } catch (e) {}
}

function WbcComposer({ chat, project, runtime, running, onSend, onGuidance, onInterrupt, draftNamespace, autoFocus, clearOnSend, error, errorKind, compact, placeholder, runningPlaceholder, hideDisclaimer }) {
  var model = WorkbenchChatModel;
  var chatId = chat ? chat.id : "";
  var projectId = (project && project.id) || "";
  var projectWorkspacePath = (project && project.workspacePath) || "";
  // Surface-scoped storage prefix (empty for the main chat). The quick-chat
  // window passes one so its draft/attachments never overwrite the main
  // window's for the same chat id.
  var draftNs = draftNamespace || "";
  var shouldClearOnSend = clearOnSend !== false;
  var workspaceContextKey = wbcWorkspaceContextKey(chatId, projectId);
  var [draft, setDraft] = useWbcState(function () { return wbcLoadDraft(chatId, draftNs); });
  var [attachments, setAttachments] = useWbcState(function () { return wbcLoadAttachments(chatId, draftNs); });
  var [mode, setMode] = useWbcState(function () {
    return wbcNormalizePermissionMode(chat && chat.permissionMode, "auto");
  });
  var [command, setCommand] = useWbcState("");
  var [uploading, setUploading] = useWbcState(false);
  var [failedImagePreviews, setFailedImagePreviews] = useWbcState({});
  var [slashOpen, setSlashOpen] = useWbcState(false);
  var [modeOpen, setModeOpen] = useWbcState(false);
  var [modelOpen, setModelOpen] = useWbcState(false);
  var [modelPanel, setModelPanel] = useWbcState("root");
  var [configuredModels, setConfiguredModels] = useWbcState([]);
  var [selectedModelId, setSelectedModelId] = useWbcState("");
  var [reasoningEffort, setReasoningEffort] = useWbcState(function () {
    return String(chat && chat.reasoningEffort || "").trim().toLowerCase();
  });
  var [contextState, setContextState] = useWbcState(null);
  var [workspaceOverride, setWorkspaceOverride] = useWbcState(function () {
    return wbcLoadWorkspaceOverride(workspaceContextKey, draftNs);
  });
  var [remoteDevices, setRemoteDevices] = useWbcState([]);
  var [remoteDeviceIds, setRemoteDeviceIds] = useWbcState([]);
  var [ctxPickerOpen, setCtxPickerOpen] = useWbcState(false);
  var taRef = useWbcRef(null);
  var fileRef = useWbcRef(null);
  var slashPickerRef = useWbcRef(null);
  var modePickerRef = useWbcRef(null);
  var ctxPickerRef = useWbcRef(null);
  var modelPickerRef = useWbcRef(null);
  var uploadCountRef = useWbcRef(0);
  var draftRef = useWbcRef(draft);
  var attachRef = useWbcRef(attachments);
  var prevChatIdRef = useWbcRef(chatId);
  var workspaceOverrideRef = useWbcRef(workspaceOverride);
  var remoteDeviceIdsRef = useWbcRef(remoteDeviceIds);
  var pendingRemoteContextRef = useWbcRef({});
  var prevWorkspaceContextKeyRef = useWbcRef(workspaceContextKey);
  // Last payload snapshot for optimistic clear with restore on error
  var lastSentRef = useWbcRef(null);
  var prevRunningRef = useWbcRef(running);

  useWbcEffect(function () { draftRef.current = draft; });
  useWbcEffect(function () { attachRef.current = attachments; });
  useWbcEffect(function () { workspaceOverrideRef.current = workspaceOverride; });
  useWbcEffect(function () { remoteDeviceIdsRef.current = remoteDeviceIds; });

  useWbcEffect(function () {
    if (!modelOpen) return undefined;
    var overlays;
    try { overlays = window.CyreneUI.require("browser-overlays"); } catch (e) {}
    if (!overlays || typeof overlays.adjust !== "function") return undefined;
    overlays.adjust(1);
    return function () { overlays.adjust(-1); };
  }, [modelOpen]);

  useWbcEffect(function () {
    if (prevChatIdRef.current === chatId) wbcSaveDraft(chatId, draft, draftNs);
  }, [draft]);

  useWbcEffect(function () {
    if (prevChatIdRef.current === chatId) wbcSaveAttachments(chatId, attachments, draftNs);
  }, [attachments]);

  useWbcEffect(function () {
    if (prevWorkspaceContextKeyRef.current === workspaceContextKey) {
      wbcSaveWorkspaceOverride(workspaceContextKey, workspaceOverride, draftNs);
    }
  }, [workspaceOverride]);

  useWbcEffect(function () { syncHeight(); }, [draft]);

  // Focus the textarea on mount when the host surface asks for it (the quick
  // chat window opens straight into typing).
  useWbcEffect(function () {
    if (autoFocus && taRef.current) {
      taRef.current.focus();
    }
  }, []);

  useWbcEffect(function () {
    if (!slashOpen && !modeOpen) return undefined;
    function closeComposerMenu(event) {
      if (
        slashOpen
        && slashPickerRef.current
        && !slashPickerRef.current.contains(event.target)
      ) {
        setSlashOpen(false);
      }
      if (
        modeOpen
        && modePickerRef.current
        && !modePickerRef.current.contains(event.target)
      ) {
        setModeOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeComposerMenu);
    return function () { document.removeEventListener("pointerdown", closeComposerMenu); };
  }, [slashOpen, modeOpen]);

  useWbcEffect(function () {
    if (!ctxPickerOpen) return undefined;
    function closeContextPicker(event) {
      if (ctxPickerRef.current && !ctxPickerRef.current.contains(event.target)) {
        setCtxPickerOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeContextPicker);
    return function () { document.removeEventListener("pointerdown", closeContextPicker); };
  }, [ctxPickerOpen]);

  useWbcEffect(function () {
    if (!modelOpen) return undefined;
    function closeModelPicker(event) {
      if (modelPickerRef.current && !modelPickerRef.current.contains(event.target)) {
        setModelOpen(false);
        setModelPanel("root");
      }
    }
    document.addEventListener("pointerdown", closeModelPicker);
    return function () { document.removeEventListener("pointerdown", closeModelPicker); };
  }, [modelOpen]);

  useWbcEffect(function () {
    var cancelled = false;
    window.CyreneUI.require("api").json("/api/settings/models", { toast: false })
      .then(function (payload) {
        var options = Array.isArray(payload.models) ? payload.models : [];
        var needsCodexCatalog = options.some(function (item) {
          return String(item.provider || "") === "codex_oauth";
        });
        var catalogRequest = needsCodexCatalog
          ? window.CyreneUI.require("api").json("/api/settings/openai-oauth", { toast: false }).catch(function () { return {}; })
          : Promise.resolve({});
        return catalogRequest.then(function (catalog) {
          if (cancelled) return;
          var codexModels = Array.isArray(catalog.models) ? catalog.models : [];
          options = options.map(function (item) {
            if (String(item.provider || "") !== "codex_oauth") return item;
            var match = codexModels.find(function (entry) {
              var id = String(entry.model || entry.id || entry.slug || "").trim();
              return id === String(item.model || "").trim();
            });
            return match ? Object.assign({}, item, {
              supportedReasoningEfforts: match.supportedReasoningEfforts || match.supported_reasoning_efforts || [],
              defaultReasoningEffort: match.defaultReasoningEffort || match.default_reasoning_effort || "",
            }) : item;
          });
          setConfiguredModels(options);
        var chatSelection = String(
          chat && (chat.modelSelectionId || chat.model || chat.lastModel) || ""
        ).trim();
        var selected = options.find(function (item) {
          return chatSelection && [
            String(item.id || ""),
            String(item.model || ""),
            String(item.name || ""),
          ].indexOf(chatSelection) >= 0;
        }) || options.find(function (item) {
          return String(item.id || "") === String(payload.active || "");
        }) || options[0];
        if (selected) {
          setSelectedModelId(String(selected.id || selected.model || ""));
          setReasoningEffort(wbcReasoningEffortForModel(
            selected,
            chat && chat.reasoningEffort
          ));
        }
        });
      })
      .catch(function () {
        if (!cancelled) setConfiguredModels([]);
      });
    return function () { cancelled = true; };
  }, [chatId]);

  useWbcEffect(function () {
    var prev = prevChatIdRef.current;
    if (prev !== chatId) {
      wbcSaveDraft(prev, draftRef.current, draftNs);
      wbcSaveAttachments(prev, attachRef.current, draftNs);
      setDraft(wbcLoadDraft(chatId, draftNs));
      setAttachments(wbcLoadAttachments(chatId, draftNs));
      setMode(wbcNormalizePermissionMode(chat && chat.permissionMode, "auto"));
      setFailedImagePreviews({});
      prevChatIdRef.current = chatId;
    }
      setCommand("");
      setSlashOpen(false);
      setModeOpen(false);
      setModelOpen(false);
      setModelPanel("root");
      setCtxPickerOpen(false);
  }, [chatId]);

  useWbcEffect(function () {
    var prevKey = prevWorkspaceContextKeyRef.current;
    if (prevKey === workspaceContextKey) return;
    var currentOverride = workspaceOverrideRef.current;
    wbcSaveWorkspaceOverride(prevKey, currentOverride, draftNs);
    var nextOverride = wbcLoadWorkspaceOverride(workspaceContextKey, draftNs);
    setWorkspaceOverride(nextOverride);
    workspaceOverrideRef.current = nextOverride;
    prevWorkspaceContextKeyRef.current = workspaceContextKey;
  }, [workspaceContextKey]);

  // Track running→false transitions where an error occurred to restore the draft
  // that was optimistically cleared in submit() — only for the main chat surface
  // (shouldClearOnSend is true) and only for message-kind errors.
  useWbcEffect(function () {
    var wasRunning = prevRunningRef.current;
    prevRunningRef.current = running;
    if (wasRunning && !running && lastSentRef.current && shouldClearOnSend) {
      var isSendError = error && (errorKind === "message" || errorKind === "load");
      if (isSendError) {
        var saved = lastSentRef.current;
        setDraft(saved.message || "");
        setAttachments(saved.attachments || []);
        if (saved.command) setCommand(saved.command);
      }
      lastSentRef.current = null;
    }
  }, [running, error, errorKind]);

  useWbcEffect(function () {
    function onChatCreated(event) {
      var detail = (event && event.detail) || {};
      if (String(detail.projectId || "") !== String(projectId || "") || !detail.chatId) return;
      var nextKey = wbcWorkspaceContextKey(detail.chatId, projectId);
      wbcSaveWorkspaceOverride(nextKey, workspaceOverrideRef.current, draftNs);
      if (remoteDeviceIdsRef.current.length) {
        var selectedIds = remoteDeviceIdsRef.current.slice();
        pendingRemoteContextRef.current[detail.chatId] = selectedIds;
        wbcSaveRemoteContext(detail.chatId, selectedIds).finally(function () {
          delete pendingRemoteContextRef.current[detail.chatId];
        });
      }
    }
    window.addEventListener("cyrene:wbc-chat-created", onChatCreated);
    return function () { window.removeEventListener("cyrene:wbc-chat-created", onChatCreated); };
  }, [projectId]);

  function wbcRefreshCtxState() {
    window.CyreneUI.require("api").json("/api/context/state", { toast: false }).then(function (s) {
      setContextState(s);
    }).catch(function () {});
  }

  useWbcEffect(function () {
    var cancelled = false;
    window.CyreneUI.require("api").json("/api/context/state", { toast: false }).then(function (s) {
      if (!cancelled) setContextState(s);
    }).catch(function () {});
    return function () { cancelled = true; };
  }, [projectId, projectWorkspacePath]);

  useWbcEffect(function () {
    var cancelled = false;
    fetch("/api/remote/settings").then(function (r) { return r.json(); }).then(function (payload) {
      if (cancelled) return;
      var eligible = (payload.peers || []).filter(function (peer) {
        return peer
          && Array.isArray(peer.received_capabilities)
          && peer.received_capabilities.length > 0
          && Array.isArray(peer.received_project_scopes)
          && peer.received_project_scopes.length > 0
          && !peer.revoked_at;
      });
      setRemoteDevices(eligible);
    }).catch(function () {
      if (!cancelled) setRemoteDevices([]);
    });
    if (!chatId || String(chatId).indexOf("legacy:") === 0) {
      setRemoteDeviceIds([]);
      return function () { cancelled = true; };
    }
    var pendingIds = pendingRemoteContextRef.current[chatId];
    if (Array.isArray(pendingIds)) {
      setRemoteDeviceIds(pendingIds);
      return function () { cancelled = true; };
    }
    fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/remote-context")
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)); })
      .then(function (payload) {
        if (!cancelled) setRemoteDeviceIds(payload.device_ids || []);
      }).catch(function () {
        if (!cancelled) setRemoteDeviceIds([]);
      });
    return function () { cancelled = true; };
  }, [chatId]);

  function syncHeight() {
    var ta = taRef.current;
    if (!ta) return;
    // An empty textarea can report the height of an animating/overlaid parent
    // as its scrollHeight in Chromium. Keep the resting composer deterministic
    // and only measure content once there is an actual draft.
    if (!String(draftRef.current || "")) {
      ta.style.height = compact ? "32px" : "44px";
      return;
    }
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
  }

  function submit() {
    var text = draft.trim();
    if (running) {
      if (!text || !onGuidance) return;
      setDraft("");
      onGuidance(text).catch(function () { setDraft(text); });
      return;
    }
    if (!text && attachments.length === 0) return;
    var payload = {
      message: text,
      attachments: attachments,
      mode: mode,
      command: command,
      model: selectedModelId,
      reasoningEffort: reasoningEffort,
    };
    // Optimistically clear on send; restored in the running-transition effect
    // if the send fails (error). The quick-chat surface passes clearOnSend=false
    // and manages its own draft lifecycle.
    if (shouldClearOnSend) {
      lastSentRef.current = payload;
      setDraft("");
      setAttachments([]);
      setCommand("");
    }
    onSend(payload);
  }

  function onKeyDown(event) {
    var sc = window.CyreneUI.require("shortcuts");
    if (sc && sc.matches(event, "composer-send")) {
      if (event.nativeEvent && event.nativeEvent.isComposing) return; // IME guard
      event.preventDefault();
      submit();
      return;
    }
    if (sc && sc.matches(event, "composer-newline")) {
      // Allow the textarea's default Shift+Enter behavior (insert newline).
      return;
    }
    // Fallback when the shortcut module is unavailable: plain Enter sends,
    // Shift/Cmd/Ctrl+Enter inserts a newline.
    if (!sc && event.key === "Enter" && !event.shiftKey && !event.metaKey && !event.ctrlKey) {
      if (event.nativeEvent && event.nativeEvent.isComposing) return; // IME guard
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === "Escape") {
      setSlashOpen(false);
      setModeOpen(false);
      setModelOpen(false);
      setModelPanel("root");
    }
  }

  function pickFiles() { if (fileRef.current) fileRef.current.click(); }
  function addFiles(files) {
    if (!files || !files.length) return;
    uploadCountRef.current += 1;
    setUploading(true);
    model.uploadFiles(files)
      .then(function (uploaded) { setAttachments(function (prev) { return prev.concat(uploaded); }); })
      .catch(function (err) { window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.uploadFailed", "Upload failed: {error}", { error: wbcErrorText(err) }), "error"); })
      .finally(function () {
        uploadCountRef.current = Math.max(0, uploadCountRef.current - 1);
        if (uploadCountRef.current === 0) setUploading(false);
        if (fileRef.current) fileRef.current.value = "";
      });
  }
  function onFilePick(event) {
    addFiles(event.target.files);
  }
  function onPaste(event) {
    if (running) return;
    var clipboard = event && (event.clipboardData || (event.nativeEvent && event.nativeEvent.clipboardData));
    if (!clipboard) return;
    var files = Array.prototype.slice.call(clipboard.files || []).filter(function (file) { return !!file; });
    // Some WebViews expose pasted files only through DataTransferItemList.
    if (!files.length) {
      files = Array.prototype.slice.call(clipboard.items || []).map(function (item) {
        return item && item.kind === "file" ? item.getAsFile() : null;
      }).filter(function (file) { return !!file; });
    }
    if (!files.length) return; // Preserve the browser's normal text paste.
    event.preventDefault();
    addFiles(files);
  }
  useWbcEffect(function () {
    function onDroppedFiles(event) {
      var detail = event && event.detail || {};
      if (detail.targetChatId && String(detail.targetChatId) !== String(chatId)) return;
      if (detail.resource && detail.resource.kind === "file") {
        var file = detail.resource.file || detail.resource;
        setAttachments(function (prev) {
          var key = String(file.id || file.path || file.url || file.name || "");
          if (key && prev.some(function (item) {
            return String(item.id || item.path || item.url || item.name || "") === key;
          })) return prev;
          return prev.concat([file]);
        });
        return;
      }
      if (detail.resource && detail.resource.kind === "snippet") {
        var quote = String(detail.resource.text || "").trim().split("\n").map(function (line) {
          return "> " + line;
        }).join("\n");
        if (quote) setDraft(function (prev) { return prev ? prev + "\n\n" + quote : quote; });
        return;
      }
      var files = detail.files;
      addFiles(files);
    }
    window.addEventListener("cyrene:add-chat-attachments", onDroppedFiles);
    return function () { window.removeEventListener("cyrene:add-chat-attachments", onDroppedFiles); };
  }, []);

  var slashQuery = draft.indexOf("/") === 0 ? draft.slice(1).toLowerCase() : "";
  var translatedCommands = WBC_COMMANDS.map(function (c) { return wbcCommandMeta(c.id); }).filter(Boolean);
  var translatedModes = WBC_MODES.map(function (m) { return wbcModeMeta(m.id); });
  var slashItems = translatedCommands.filter(function (c) {
    return !slashQuery || c.id.indexOf(slashQuery) !== -1 || c.label.toLowerCase().indexOf(slashQuery) !== -1;
  });
  var showSlash = (slashOpen || (draft.indexOf("/") === 0 && draft.indexOf(" ") === -1)) && slashItems.length > 0 && !running;
  var activeCommand = command ? wbcCommandMeta(command) : null;
  var currentMode = wbcModeMeta(mode);
  var personaOn = !contextState || contextState.soul_active !== false;
  var workspaceOn = !!(contextState && contextState.workspace_active !== false);
  // Follow the active project's workspace by default. A directory explicitly
  // chosen from the composer remains selected when the user switches projects.
  var wsDir = workspaceOverride || projectWorkspacePath || (contextState && contextState.workspace_dir) || "";
  var wsHistory = (contextState && Array.isArray(contextState.workspace_history)) ? contextState.workspace_history : [];
  var selectedModel = configuredModels.find(function (item) {
    return String(item.id || item.model || "") === String(selectedModelId || "");
  });
  var modelName = wbcCurrentModel(chat, project, runtime, null);
  modelName = wbcFriendlyModelName(selectedModel, modelName);
  var effortLabel = reasoningEffort
    ? wbcT("settings.reasoningEffortValue." + reasoningEffort, reasoningEffort)
    : "";
  var modelButtonLabel = wbcT("workbenchChat.chooseModel", "Choose model")
    + ": " + modelName + (effortLabel ? " · " + effortLabel : "");
  var supportedReasoningEfforts = wbcSupportedReasoningEfforts(selectedModel);

  function wbcTogglePersona() {
    window.CyreneUI.require("api").fetch(personaOn ? "/api/context/remove-soul" : "/api/context/add-soul", { method: "POST" })
      .then(wbcRefreshCtxState, function (err) {
        window.CyreneUI.require("api").toastError(err, wbcT("workbenchChat.personaFailed", "Failed to toggle persona: "));
      }).catch(function () {});
    setCtxPickerOpen(false);
  }

  function wbcAddWorkspace(path) {
    var selectedPath = String(path || "").trim();
    var previousOverride = workspaceOverride;
    setWorkspaceOverride(selectedPath && selectedPath !== projectWorkspacePath ? selectedPath : "");
    setContextState(function (prev) {
      if (!prev) return prev;
      var history = Array.isArray(prev.workspace_history) ? prev.workspace_history : [];
      if (selectedPath) {
        history = [selectedPath].concat(history.filter(function (item) { return item !== selectedPath; })).slice(0, 10);
      }
      return { ...prev, workspace_active: true, workspace_dir: selectedPath || prev.workspace_dir, workspace_history: history };
    });
    window.CyreneUI.require("api").json("/api/context/add-workspace", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: selectedPath }),
      toast: false,
    }).then(function () {
      wbcRefreshCtxState();
    }, function (err) {
      setWorkspaceOverride(previousOverride);
      wbcRefreshCtxState();
      window.CyreneUI.require("api").toastError(err, wbcT("workbenchChat.workspaceAddFailed", "Failed to add workspace: "));
    }).catch(function () {});
    setCtxPickerOpen(false);
  }

  function wbcRemoveWorkspace() {
    setContextState(function (prev) { return prev ? { ...prev, workspace_active: false } : prev; });
    window.CyreneUI.require("api").json("/api/context/remove-workspace", { method: "POST", toast: false })
      .then(wbcRefreshCtxState, function (err) {
        wbcRefreshCtxState();
        window.CyreneUI.require("api").toastError(err, wbcT("workbenchChat.workspaceRemoveFailed", "Failed to remove workspace: "));
      }).catch(function () {});
  }

  function wbcPickWorkspace() {
    setCtxPickerOpen(false);
    if (
      window.cyrene &&
      window.cyrene.platform === "linux" &&
      typeof window.cyrene.pickDirectory === "function"
    ) {
      window.cyrene.pickDirectory().then(function (data) {
        if (data && data.path) wbcAddWorkspace(data.path);
      }).catch(function () {});
      return;
    }
    window.CyreneUI.require("api").fetch("/api/context/pick-directory", { method: "POST" })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (data) { if (data && data.path) wbcAddWorkspace(data.path); })
      .catch(function (err) {
        window.CyreneUI.require("api").toastError(err, wbcT("workbenchChat.pickDirFailed", "Failed to open directory picker: "));
      });
  }

  function wbcSaveRemoteContext(targetChatId, nextDeviceIds) {
    var normalized = Array.from(new Set(nextDeviceIds || []));
    setRemoteDeviceIds(normalized);
    if (!targetChatId || String(targetChatId).indexOf("legacy:") === 0) {
      return Promise.resolve();
    }
    return window.CyreneUI.require("api").fetch(
      "/api/workbench/chats/" + encodeURIComponent(targetChatId) + "/remote-context",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_ids: normalized }),
      }
    ).then(function () {}, function (err) {
      window.CyreneUI.require("api").toastError(err, wbcT("workbenchChat.remoteContextFailed", "Failed to update remote context: "));
      throw err;
    });
  }

  function wbcToggleRemoteDevice(deviceId) {
    var previousIds = remoteDeviceIds.slice();
    var selected = remoteDeviceIds.indexOf(deviceId) >= 0;
    var nextIds = selected
      ? remoteDeviceIds.filter(function (item) { return item !== deviceId; })
      : remoteDeviceIds.concat([deviceId]);
    wbcSaveRemoteContext(chatId, nextIds).catch(function () {
      setRemoteDeviceIds(previousIds);
    });
    setCtxPickerOpen(false);
  }

  function wbcRemoveRemoteDevice(deviceId) {
    var previousIds = remoteDeviceIds.slice();
    var nextIds = remoteDeviceIds.filter(function (item) { return item !== deviceId; });
    wbcSaveRemoteContext(chatId, nextIds).catch(function () {
      setRemoteDeviceIds(previousIds);
    });
  }
  var hasRuntimeGuidance = running && !!draft.trim();
  var sendDisabled = running ? false : (!draft.trim() && attachments.length === 0);
  var isLegacy = !!(chat && chat.legacy);

  if (isLegacy) {
    return (
      <div className={"wbc-composer" + (compact ? " compact" : "")}>
        <div className="wbc-composer-box wbc-composer-readonly">
          {wbcT("workbenchChat.legacyReadonly", "This is an archived legacy session — read-only. Start a new chat to continue the topic.")}
        </div>
      </div>
    );
  }

  return (
    <div className={"wbc-composer" + (compact ? " compact" : "")}>
      {activeCommand && (
        <div className="wbc-command-row">
          <span className="wbc-command-chip">
            {WBC_ICONS.slash}
            {activeCommand.label}
            <button type="button" onClick={function () { setCommand(""); }} aria-label={wbcT("workbenchChat.removeCommand", "Remove command")}>{WBC_ICONS.x}</button>
          </span>
        </div>
      )}
      <div className="wbc-composer-box">
        {attachments.length > 0 && (
          <div className="wbc-attach-row">
            {attachments.map(function (file, i) {
              var isImg = file.kind === "image" || String(file.content_type || "").indexOf("image") === 0;
              var attachmentKey = String(file.id || file.url || i);
              var showImagePreview = isImg && file.url && !failedImagePreviews[attachmentKey];
              return (
                <div className={"wbc-attach-card" + (showImagePreview ? " image" : " file")} key={attachmentKey}>
                  {showImagePreview
                    ? <img src={file.url} alt="" onError={function () {
                        setFailedImagePreviews(function (prev) {
                          return Object.assign({}, prev, { [attachmentKey]: true });
                        });
                      }} />
                    : <>
                        <WbcFileVisual file={file} className="wbc-composer-file-visual" />
                        <span className="wbc-attach-file-meta">
                          <b title={file.name}>{file.name || "file"}</b>
                          <small>{wbcAttachmentTypeLabel(file)}</small>
                        </span>
                      </>}
                  <button type="button" className="wbc-attach-x" onClick={function () {
                    setAttachments(attachments.filter(function (_f, idx) { return idx !== i; }));
                  }} aria-label={wbcT("workbenchChat.removeAttachment", "Remove attachment")}>{WBC_ICONS.x}</button>
                </div>
              );
            })}
          </div>
        )}
        <textarea
          ref={taRef}
          value={draft}
          rows={compact ? 1 : 2}
          disabled={compact && running}
          onChange={function (e) { setDraft(e.target.value); syncHeight(); }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          placeholder={running
            ? (runningPlaceholder || wbcT("workbenchChat.placeholderRunning", "Send guidance to the running agent..."))
            : (placeholder || wbcT("workbenchChat.placeholder", "Message Cyrene..."))}
        />
        {!compact && <div className="wbc-context-chips">
          {personaOn && (
            <span className="wbc-ctx-chip on">
              {WBC_ICONS.spark}
              <span>{wbcT("workbenchChat.persona", "Persona")}</span>
              <button type="button" className="wbc-ctx-x" title={wbcT("workbenchChat.removeContext", "Remove")} onClick={wbcTogglePersona}>{WBC_ICONS.x}</button>
            </span>
          )}
          {workspaceOn && (
            <span className="wbc-ctx-chip on">
              {WBC_ICONS.folder}
              <span title={wsDir}>{wbcT("workbenchChat.workspaceChip", "Workspace: {name}", { name: wbcWorkspaceDisplayName(wsDir) })}</span>
              <button type="button" className="wbc-ctx-x" title={wbcT("workbenchChat.removeContext", "Remove")} onClick={wbcRemoveWorkspace}>{WBC_ICONS.x}</button>
            </span>
          )}
          {remoteDeviceIds.map(function (deviceId) {
            var device = remoteDevices.find(function (item) { return item.device_id === deviceId; });
            if (!device) return null;
            return (
              <span className="wbc-ctx-chip on remote" key={deviceId}>
                {WBC_ICONS.device}
                <span title={device.device_id}>{wbcT("workbenchChat.remoteDeviceChip", "Remote: {name}", { name: device.display_name || device.device_id })}</span>
                <button type="button" className="wbc-ctx-x" title={wbcT("workbenchChat.removeContext", "Remove")} onClick={function () { wbcRemoveRemoteDevice(deviceId); }}>{WBC_ICONS.x}</button>
              </span>
            );
          })}
          {(!personaOn || !workspaceOn || remoteDevices.length > 0) && (
            <span className="wbc-pop-anchor" ref={ctxPickerRef}>
              <button type="button" className={"wbc-ctx-add-btn" + (ctxPickerOpen ? " active" : "")} onClick={function () { setCtxPickerOpen(!ctxPickerOpen); setSlashOpen(false); setModeOpen(false); }}>
                {WBC_ICONS.plus}<span>{wbcT("workbenchChat.addContext", "Add context")}</span>
              </button>
              {ctxPickerOpen && (
                <WbcCtxPicker
                  personaOn={personaOn}
                  workspaceOn={workspaceOn}
                  defaultWorkspacePath={projectWorkspacePath || wsDir}
                  wsHistory={wsHistory}
                  onTogglePersona={wbcTogglePersona}
                  onAddWorkspace={wbcAddWorkspace}
                  onPickWorkspace={wbcPickWorkspace}
                  remoteDevices={remoteDevices}
                  selectedRemoteDeviceIds={remoteDeviceIds}
                  onToggleRemoteDevice={wbcToggleRemoteDevice}
                />
              )}
            </span>
          )}
        </div>}
        <div className="wbc-composer-actions">
          <input ref={fileRef} type="file" multiple style={{ display: "none" }} onChange={onFilePick} />
          <button type="button" className="wbc-composer-icon" title={uploading ? wbcT("workbenchChat.uploading", "Uploading...") : wbcT("workbenchChat.addAttachment", "Add attachment")} disabled={uploading || running} onClick={pickFiles}>
            {uploading ? <span className="wb-spinner small" /> : WBC_ICONS.attach}
          </button>
          {!compact && <>
            <span className="wbc-pop-anchor" ref={slashPickerRef}>
            <button type="button" className={"wbc-composer-icon" + (showSlash || command ? " active" : "")} title={wbcT("workbenchChat.commands", "Commands")} disabled={running} onClick={function () { setSlashOpen(!slashOpen); setModeOpen(false); }}>
              {WBC_ICONS.slash}
            </button>
            {showSlash && (
              <div className="wbc-popmenu">
                <div className="wbc-popmenu-head">{wbcT("workbenchChat.commands", "Commands")}</div>
                {slashItems.map(function (c) {
                  var on = command === c.id;
                  return (
                    <button key={c.id} type="button" className={on ? "active" : ""} onClick={function () {
                      setCommand(on ? "" : c.id);
                      setSlashOpen(false);
                      if (draft.indexOf("/") === 0) setDraft("");
                      if (taRef.current) taRef.current.focus();
                    }}>
                      <span className="wbc-popmenu-label">{c.label}</span>
                      <span className="wbc-popmenu-desc">{c.desc}</span>
                      {on ? <span className="wbc-popmenu-check">{WBC_ICONS.check}</span> : null}
                    </button>
                  );
                })}
              </div>
            )}
            </span>
            <span className="wbc-pop-anchor" ref={modePickerRef}>
            <button type="button" className={"wbc-composer-icon mode" + (modeOpen ? " active" : "")} title={wbcT("workbenchChat.permissionMode", "Permission mode")} onClick={function () { setModeOpen(!modeOpen); setSlashOpen(false); }}>
              {WBC_ICONS.bolt}
              <span>{currentMode.label}</span>
            </button>
            {modeOpen && (
              <div className="wbc-popmenu">
                <div className="wbc-popmenu-head">{wbcT("workbenchChat.permissionMode", "Permission mode")}</div>
                {translatedModes.map(function (m) {
                  var on = mode === m.id;
                  return (
                    <button key={m.id} type="button" className={on ? "active" : ""} onClick={function () { setMode(m.id); setModeOpen(false); }}>
                      <span className="wbc-popmenu-label">{m.label}</span>
                      <span className="wbc-popmenu-desc">{m.desc}</span>
                      {on ? <span className="wbc-popmenu-check">{WBC_ICONS.check}</span> : null}
                    </button>
                  );
                })}
              </div>
            )}
            </span>
          </>}
          <span className="wbc-composer-spacer" />
          {!compact && modelName ? (
            <span className="wbc-pop-anchor wbc-model-anchor" ref={modelPickerRef}>
              <button
                type="button"
                className={"wbc-model-button" + (modelOpen ? " active" : "")}
                title={modelButtonLabel}
                aria-label={modelButtonLabel}
                aria-haspopup="menu"
                aria-expanded={modelOpen}
                disabled={running}
                onClick={function () {
                  setModelOpen(!modelOpen);
                  setModelPanel("root");
                  setSlashOpen(false);
                  setModeOpen(false);
                }}
              >
                <span className="wbc-model-button-icon" aria-hidden="true">{WBC_ICONS.model}</span>
                <span className="wbc-model-button-name">{modelName}</span>
                {effortLabel ? <span className="wbc-model-button-effort">{effortLabel}</span> : null}
                <span className="wbc-model-button-chevron">{WBC_ICONS.chevronDown}</span>
              </button>
              {modelOpen && (
                <div className="wbc-popmenu wbc-model-menu" role="menu">
                  {modelPanel === "root" && (
                    <>
                      <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("models"); }}>
                        <span className="wbc-model-menu-key">{wbcT("workbenchChat.model", "Model")}</span>
                        <span className="wbc-model-menu-value wbc-model-menu-model-name">{modelName}</span>
                        <span className="wbc-model-menu-chevron">{WBC_ICONS.chevronRight}</span>
                      </button>
                      {supportedReasoningEfforts.length > 0 && (
                        <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("effort"); }}>
                          <span className="wbc-model-menu-key">{wbcT("workbenchChat.reasoningEffort", "Reasoning effort")}</span>
                          <span className="wbc-model-menu-value">{effortLabel || "—"}</span>
                          <span className="wbc-model-menu-chevron">{WBC_ICONS.chevronRight}</span>
                        </button>
                      )}
                    </>
                  )}
                  {modelPanel === "models" && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{WBC_ICONS.chevronLeft}</span>
                        <span>{wbcT("workbenchChat.model", "Model")}</span>
                      </button>
                      {configuredModels.map(function (item) {
                        var id = String(item.id || item.model || "");
                        var active = id === selectedModelId;
                        return (
                          <button key={id} type="button" className={active ? "active" : ""} onClick={function () {
                            setSelectedModelId(id);
                            setReasoningEffort(wbcReasoningEffortForModel(item, ""));
                            setModelPanel("root");
                          }}>
                            <span className="wbc-popmenu-label">{item.name || item.model}</span>
                            {item.desc ? <span className="wbc-popmenu-desc">{item.desc}</span> : null}
                            {active ? <span className="wbc-popmenu-check">{WBC_ICONS.check}</span> : null}
                          </button>
                        );
                      })}
                    </>
                  )}
                  {modelPanel === "effort" && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{WBC_ICONS.chevronLeft}</span>
                        <span>{wbcT("workbenchChat.reasoningEffort", "Reasoning effort")}</span>
                      </button>
                      {supportedReasoningEfforts.map(function (effort) {
                        var active = effort === reasoningEffort;
                        return (
                          <button key={effort} type="button" className={active ? "active" : ""} onClick={function () {
                            setReasoningEffort(effort);
                            setModelPanel("root");
                          }}>
                            <span className="wbc-popmenu-label">{wbcT("settings.reasoningEffortValue." + effort, effort)}</span>
                            {active ? <span className="wbc-popmenu-check">{WBC_ICONS.check}</span> : null}
                          </button>
                        );
                      })}
                    </>
                  )}
                </div>
              )}
            </span>
          ) : null}
          <button
            type="button"
            className={"wbc-send" + (running && !hasRuntimeGuidance ? " stop" : "")}
            onClick={running && !hasRuntimeGuidance ? onInterrupt : submit}
            disabled={sendDisabled}
            title={running
              ? (hasRuntimeGuidance ? wbcT("workbenchChat.sendGuidance", "Send guidance") : wbcT("workbenchChat.stop", "Stop"))
              : wbcT("workbenchChat.send", "Send")}
          >
            {running && !hasRuntimeGuidance ? WBC_ICONS.stop : WBC_ICONS.send}
            <span>{running
              ? (hasRuntimeGuidance ? wbcT("workbenchChat.guidance", "Guide") : wbcT("workbenchChat.stop", "Stop"))
              : wbcT("workbenchChat.send", "Send")}</span>
          </button>
        </div>
      </div>
      {!compact && !hideDisclaimer && <div className="wb-composer-disclaimer">{wbcT("workbench.composerDisclaimer", "Cyrene is AI and can make mistakes. Please verify responses.")}</div>}
    </div>
  );
}

// Shared with the quick-chat surface (workbench-quick-chat.jsx), which renders
// the exact same composer (attachments, commands, permission mode, IME-safe
// send) rather than forking a second input box.
// Also shared with the quick-chat surface so its transcript renders with the
// exact same message cards (tool-call traces, agent files, attachments, the live
// "thinking/calling tools" card) as the main conversation instead of a
// simplified text bubble. They are self-contained (only module-level helpers +
// optional callbacks), so the quick-chat window can mount them standalone.
// Clears a persisted draft + attachments for one chat in a given namespace.
// The quick-chat window keeps its draft on a failed send (clearOnSend=false),
// so it calls this on success to wipe the namespaced draft before remounting.
function wbcClearComposerDraft(chatId, ns) {
  wbcSaveDraft(chatId, "", ns);
  wbcSaveAttachments(chatId, [], ns);
}

// Context picker popup — shown inside the composer when the user clicks "+ Add context".
// Fully independent from the legacy ModernContextPicker in chat-surface.jsx.
function WbcCtxPicker({ personaOn, workspaceOn, defaultWorkspacePath, wsHistory, onTogglePersona, onAddWorkspace, onPickWorkspace, remoteDevices, selectedRemoteDeviceIds, onToggleRemoteDevice }) {
  var hasAny = !personaOn || !workspaceOn || (remoteDevices && remoteDevices.length > 0);
  var workspaceOptions = [];
  if (defaultWorkspacePath) workspaceOptions.push({ path: defaultWorkspacePath, isDefault: true });
  wsHistory.forEach(function (path) {
    if (path && path !== defaultWorkspacePath) workspaceOptions.push({ path: path, isDefault: false });
  });
  if (!hasAny) return null;
  return (
    <div className="wbc-popmenu wbc-ctx-picker">
      <div className="wbc-popmenu-head">{wbcT("workbenchChat.addContext", "Add context")}</div>
      {!personaOn && (
        <button type="button" onClick={onTogglePersona}>
          <span className="wbc-popmenu-label">{WBC_ICONS.spark} {wbcT("workbenchChat.persona", "Persona")}</span>
          <span className="wbc-popmenu-desc">{wbcT("workbenchChat.addPersonaHint", "Include SOUL.md persona in context")}</span>
        </button>
      )}
      {!workspaceOn && (
        <React.Fragment>
          <div className="wbc-popmenu-head">{wbcT("workbenchChat.workspaceSection", "Workspace")}</div>
          {workspaceOptions.map(function (option) {
            var p = option.path;
            var name = wbcWorkspaceDisplayName(p);
            return (
              <button key={p} type="button" onClick={function () { onAddWorkspace(p); }}>
                <span className="wbc-popmenu-label mono">
                  {option.isDefault ? wbcT("workbenchChat.defaultWorkspace", "Default workspace") : name}
                </span>
                <span className="wbc-popmenu-desc" title={p}>{p}</span>
              </button>
            );
          })}
          <button type="button" onClick={onPickWorkspace}>
            <span className="wbc-popmenu-label">{wbcT("workbenchChat.chooseDirectory", "Choose directory…")}</span>
          </button>
        </React.Fragment>
      )}
      {remoteDevices && remoteDevices.length > 0 && (
        <React.Fragment>
          <div className="wbc-popmenu-head">{wbcT("workbenchChat.remoteDevicesSection", "Remote devices")}</div>
          {remoteDevices.map(function (device) {
            var selected = selectedRemoteDeviceIds.indexOf(device.device_id) >= 0;
            var capabilityCount = (device.received_capabilities || []).length;
            return (
              <button key={device.device_id} type="button" className={selected ? "active" : ""} onClick={function () { onToggleRemoteDevice(device.device_id); }}>
                <span className="wbc-popmenu-label">{WBC_ICONS.device} {device.display_name || device.device_id}</span>
                <span className="wbc-popmenu-desc">{wbcT("workbenchChat.remoteDeviceHint", "{count} granted capabilities", { count: capabilityCount })}</span>
                {selected ? <span className="wbc-popmenu-check">{WBC_ICONS.check}</span> : null}
              </button>
            );
          })}
        </React.Fragment>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Branch tree (fork lineage navigator)
// ---------------------------------------------------------------------------

// Resolve the fork lineage the active chat belongs to. Walks up
// forkedFromChatId to the lineage root, then confirms the lineage spans more
// than one chat (a lone conversation has no branches to show). Returns
// { root, byId, children } or null when there's nothing to draw.
function wbcBranchLineage(chats, activeChatId) {
  if (!activeChatId || !Array.isArray(chats) || !chats.length) return null;
  var byId = {};
  chats.forEach(function (c) { if (c && c.id) byId[c.id] = c; });
  var active = byId[activeChatId];
  if (!active) return null;
  var children = {};
  chats.forEach(function (c) {
    var parent = c && c.forkedFromChatId;
    if (parent && byId[parent]) (children[parent] = children[parent] || []).push(c);
  });
  Object.keys(children).forEach(function (key) {
    children[key].sort(function (a, b) {
      return String(a.createdAt || "").localeCompare(String(b.createdAt || ""));
    });
  });
  // Climb to the lineage root (a missing/deleted parent terminates the walk).
  var root = active;
  var guard = 0;
  while (root.forkedFromChatId && byId[root.forkedFromChatId] && guard < 500) {
    root = byId[root.forkedFromChatId];
    guard += 1;
  }
  var size = 0;
  (function count(node) {
    size += 1;
    (children[node.id] || []).forEach(count);
  })(root);
  if (size < 2) return null;
  return { root: root, byId: byId, children: children };
}

// Flatten a lineage into render rows via DFS. Each chat is a vertical lane at
// its depth: a head row (root start or fork divergence) and, once it has a
// reply, a tip row (its latest message). Children nest between the two at
// depth+1. Per-row line flags drive the connectors so the lane stays unbroken:
//   lineDown — own column runs from the dot to the row bottom (head with more
//              below it); lineUp — own column runs from the top into the dot
//              (tip closing the lane); elbow — horizontal join from the parent
//              column (only a fork head taps its parent's trunk).
function wbcBranchRows(lineage) {
  if (!lineage) return [];
  var rows = [];
  (function walk(chat, depth, isRoot) {
    var children = lineage.children[chat.id] || [];
    var head = isRoot
      ? String(chat.firstMessage || chat.preview || "")
      : String(chat.forkMessage || chat.firstMessage || chat.preview || "");
    var tip = String(chat.preview || "");
    // A branch with no reply yet has tip === head; render only the head node.
    var hasTip = !!(tip && tip !== head);
    var hasKids = children.length > 0;
    rows.push({
      chatId: chat.id, kind: isRoot ? "root" : "fork", depth: depth,
      text: head, title: chat.title, isHead: true,
      lineUp: false, lineDown: hasTip || hasKids, elbow: depth > 0,
    });
    children.forEach(function (child) { walk(child, depth + 1, false); });
    if (hasTip) {
      rows.push({
        chatId: chat.id, kind: "tip", depth: depth,
        text: tip, title: chat.title, isHead: false,
        lineUp: true, lineDown: false, elbow: false,
      });
    }
  })(lineage.root, 0, true);
  return rows;
}

// Connector segments for one compact Git-style row. Each depth gets a narrow
// lane; the root lane uses the source-control blue while nested lanes use the
// Workbench accent. Keeping the tone on each segment lets a fork stay readable
// without adding cards, badges, or other decoration around the row.
function wbcBranchConnectors(row) {
  var U = 14, CY = 28, BASE = 14, CURVE_W = 14, CURVE_H = 24, d = row.depth;
  function cx(col) { return col * U + BASE; }
  function tone(col) { return col === 0 ? "main-lane" : "fork-lane"; }
  var segs = [];
  for (var c = 0; c < d; c += 1) {
    segs.push({ cls: "v " + tone(c), style: { left: cx(c) + "px", top: 0, bottom: 0 } });
  }
  if (row.lineDown) segs.push({ cls: "v " + tone(d), style: { left: cx(d) + "px", top: CY + "px", bottom: 0 } });
  if (row.lineUp) segs.push({ cls: "v " + tone(d), style: { left: cx(d) + "px", top: 0, height: CY + "px" } });
  if (row.elbow) {
    var nodeX = cx(d), parentX = cx(d - 1);
    var curveWidth = Math.min(CURVE_W, nodeX - parentX);
    var straightWidth = nodeX - curveWidth - parentX;
    if (straightWidth > 0) {
      segs.push({ cls: "h fork-lane", style: { left: parentX + "px", top: (CY - CURVE_H) + "px", width: (straightWidth + 1) + "px" } });
    }
    segs.push({
      cls: "arc fork-lane",
      style: {
        left: (nodeX - curveWidth) + "px",
        top: (CY - CURVE_H) + "px",
        width: curveWidth + "px",
        height: CURVE_H + "px",
      },
    });
  }
  return segs;
}

function wbcBranchKindLabel(kind) {
  if (kind === "root") return wbcT("workbenchChat.branchStart", "Start");
  if (kind === "tip") return wbcT("workbenchChat.branchEnd", "Latest");
  return wbcT("workbenchChat.branchFork", "Branch");
}

function wbcBrowserStateForChat(chatId) {
  var id = String(chatId || "").trim();
  if (!id) return {};
  var dataState = window.CyreneUI.require("data").state;
  var byChat = dataState.browserByChat || {};
  if (byChat[id]) return byChat[id];
  var browser = dataState.browser || {};
  var browserSessionId = String(browser.sessionId || browser.chatId || "").trim();
  return browserSessionId && browserSessionId === id ? browser : {};
}

// Right-panel tab rendering the fork lineage as a node-and-line tree. Clicking
// a node switches to that branch; the active chat's nodes stay highlighted.
function WbcBranchTab({ chats, activeChatId, onSelectChat }) {
  var rows = useWbcMemo(function () {
    return wbcBranchRows(wbcBranchLineage(chats, activeChatId));
  }, [chats, activeChatId]);
  if (!rows.length) {
    return <p className="workbench-muted wbc-branch-empty">{wbcT("workbenchChat.branchEmpty", "This conversation has no branches.")}</p>;
  }
  var maxDepth = rows.reduce(function (depth, row) {
    return Math.max(depth, Number(row.depth) || 0);
  }, 0);
  return (
    <div className="wbc-branch" style={{ "--wbc-branch-rail": (maxDepth * 14 + 30) + "px" }}>
      <ul className="wbc-branch-tree">
        {rows.map(function (row, index) {
          var isActive = row.chatId === activeChatId;
          var isCurrent = isActive && row.isHead;
          var lane = row.depth > 0 ? " lane-fork" : " lane-main";
          var cls = "wbc-branch-row depth-" + row.depth + " kind-" + row.kind + lane + (isActive ? " on-current-branch" : "") + (isCurrent ? " current" : "");
          return (
            <li
              key={row.chatId + ":" + row.kind + ":" + index}
              className={cls}
            >
              <button
                type="button"
                className="wbc-branch-button"
                title={row.text || row.title || ""}
                aria-current={isCurrent ? "true" : undefined}
                onClick={function () { onSelectChat(row.chatId); }}
              >
                {wbcBranchConnectors(row).map(function (seg, segIndex) {
                  return <span key={"seg" + segIndex} className={"wbc-branch-line " + seg.cls} style={seg.style} aria-hidden="true" />;
                })}
                <span className="wbc-branch-node" style={{ left: (row.depth * 14 + 14) + "px" }} aria-hidden="true">
                  <span className="wbc-branch-node-core" />
                </span>
                <span className="wbc-branch-card">
                  <span className="wbc-branch-kind">{wbcBranchKindLabel(row.kind)}</span>
                  <span className="wbc-branch-text">{row.text || wbcT("workbenchChat.branchNoText", "(empty message)")}</span>
                  {isCurrent && <span className="wbc-branch-here">{wbcT("workbenchChat.branchHere", "Current")}</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right context panel (column 4)
// ---------------------------------------------------------------------------

function wbcChatArtifactFiles(chat) {
  var files = [];
  (chat && chat.messages || []).forEach(function (msg) {
    (msg.attachments || []).forEach(function (file) {
      if (file) files.push({ file: file, role: msg.role });
    });
  });
  return files;
}

function wbcArtifactFileKey(file) {
  if (!file) return "";
  return String(file.id || file.url || file.path || file.name || "");
}

function WbcArtifactSplitHost({ file, items, width, label, onSelect, onResize, onClose, onViewed, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  return (
       <WbcResourceSplitHost openKey={wbcArtifactFileKey(file)} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {file ? (
        <WbcArtifactSplit
          file={file}
          items={items}
          label={label}
          onSelect={onSelect}
          onClose={onClose}
          onViewed={onViewed}
        />
      ) : null}
    </WbcResourceSplitHost>
  );
}

function WbcArtifactSplit({ file, items, label, onSelect, onClose, onViewed }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  var [htmlMode, setHtmlMode] = useWbcState("rendered");
  var headerRef = useWbcRef(null);
  var files = Array.isArray(items) ? items : [];
  var currentKey = wbcArtifactFileKey(file);
  var kind = wbcFileViewKind(file);
  var splitLabel = label || wbcT("workbenchChat.artifacts", "Artifacts");

  useWbcEffect(function () {
    setHtmlMode("rendered");
  }, [currentKey]);

  useWbcEffect(function () {
    if (!pickerOpen) return undefined;
    function closePicker(event) {
      if (headerRef.current && !headerRef.current.contains(event.target)) setPickerOpen(false);
    }
    document.addEventListener("pointerdown", closePicker);
    return function () { document.removeEventListener("pointerdown", closePicker); };
  }, [pickerOpen]);

  return (
    <aside className="wbc-side-agent-split wbc-artifact-split" aria-label={splitLabel}>
      <header className="wbc-side-agent-split-head" ref={headerRef}>
        <button
          type="button"
          className="wbc-side-agent-split-picker"
          onClick={function () { setPickerOpen(function (open) { return !open; }); }}
          aria-expanded={pickerOpen}
          aria-haspopup="listbox"
        >
          <span className="wbc-side-agent-split-title">
            <span>{splitLabel}</span>
            <b title={file && file.name}>{file && file.name || "file"}</b>
          </span>
          <span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
        </button>
        <span className="wbc-artifact-split-actions">
          {kind === "html" && (
            <span className="wbc-artifact-mode-switch">
              <button type="button" className={htmlMode === "rendered" ? "active" : ""} onClick={function () { setHtmlMode("rendered"); }}>{wbcT("workbenchChat.viewerRendered", "Rendered")}</button>
              <button type="button" className={htmlMode === "source" ? "active" : ""} onClick={function () { setHtmlMode("source"); }}>{wbcT("workbenchChat.viewerSource", "Source")}</button>
            </span>
          )}
          {wbcCanOpenExternally(file) ? (
            <a className="wbc-side-agent-split-action" href={file.url} target="_blank" rel="noreferrer" title={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")} aria-label={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}>{WBC_ICONS.openExternal}</a>
          ) : null}
          {wbcDownloadLink(file, { className: "wbc-side-agent-split-action", "aria-label": wbcT("workbenchChat.download", "Download") })}
          <button
            type="button"
            className="wbc-side-agent-split-close"
            onClick={onClose}
            title={wbcT("workbenchChat.closeArtifactPreview", "Close artifact preview")}
            aria-label={wbcT("workbenchChat.closeArtifactPreview", "Close artifact preview")}
          >{WBC_ICONS.x}</button>
        </span>
        <WbcSplitPickerMenu open={pickerOpen} role="listbox" aria-label={splitLabel}>
            {files.map(function (item, index) {
              var itemFile = item && item.file;
              var selected = wbcArtifactFileKey(itemFile) === currentKey;
              return (
                <button
                  type="button"
                  key={wbcArtifactFileKey(itemFile) + ":" + index}
                  className={selected ? "active" : ""}
                  role="option"
                  aria-selected={selected}
                  onClick={function () {
                    setPickerOpen(false);
                    if (onSelect) onSelect(itemFile);
                  }}
                >
                  <span className="wbc-artifact-picker-icon" aria-hidden="true">{WBC_ICONS.file}</span>
                  <b>{itemFile && itemFile.name || "file"}</b>
                </button>
              );
            })}
        </WbcSplitPickerMenu>
      </header>
      <div className="wbc-artifact-split-viewer">
        <WbcViewerTab file={file} onViewed={onViewed} hideHeader={true} htmlMode={htmlMode} onHtmlModeChange={setHtmlMode} />
      </div>
    </aside>
  );
}

function WbcChangeSplitHost({ change, width, onSelect, onResize, onClose, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  var changeKey = change ? String(change.setId || "") + ":" + String(change.path || "") : "";
  return (
       <WbcResourceSplitHost openKey={changeKey} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {change ? <WbcChangeSplit change={change} onSelect={onSelect} onClose={onClose} /> : null}
    </WbcResourceSplitHost>
  );
}

function WbcChangeSplit({ change, onSelect, onClose }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  var [diffState, setDiffState] = useWbcState({ loading: true, diff: "", error: "", change: null });
  var headerRef = useWbcRef(null);
  var files = Array.isArray(change && change.files) ? change.files : [];

  useWbcEffect(function () {
    if (!pickerOpen) return undefined;
    function closePicker(event) {
      if (headerRef.current && !headerRef.current.contains(event.target)) setPickerOpen(false);
    }
    document.addEventListener("pointerdown", closePicker);
    return function () { document.removeEventListener("pointerdown", closePicker); };
  }, [pickerOpen]);

  useWbcEffect(function () {
    if (!change || !change.chatId || !change.setId || !change.path) return undefined;
    var cancelled = false;
    setDiffState({ loading: true, diff: "", error: "", change: null });
    WorkbenchChatModel.getChangeDiff(change.chatId, change.setId, change.path, { toast: false })
      .then(function (detail) {
        if (!cancelled) setDiffState({ loading: false, diff: String(detail.diff || ""), error: "", change: detail });
      })
      .catch(function (err) {
        if (!cancelled) setDiffState({ loading: false, diff: "", error: wbcErrorText(err), change: null });
      });
    return function () { cancelled = true; };
  }, [change && change.chatId, change && change.setId, change && change.path]);

  return (
    <aside className="wbc-side-agent-split wbc-change-split" aria-label={wbcT("workbenchChat.changePreview", "Change preview")}>
      <header className="wbc-side-agent-split-head" ref={headerRef}>
        <button
          type="button"
          className="wbc-side-agent-split-picker"
          onClick={function () { setPickerOpen(function (open) { return !open; }); }}
          aria-expanded={pickerOpen}
          aria-haspopup="listbox"
        >
          <span className="wbc-side-agent-split-title">
            <span>{wbcT("workbenchChat.changes", "Changes")}</span>
            <b title={change && change.path}>{change && change.path || "file"}</b>
          </span>
          <span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
        </button>
        <button type="button" className="wbc-side-agent-split-close" onClick={onClose} aria-label={wbcT("workbenchChat.closeChangePreview", "Close change preview")}>{WBC_ICONS.x}</button>
        <WbcSplitPickerMenu open={pickerOpen} role="listbox" aria-label={wbcT("workbenchChat.changes", "Changes")}>
            {files.map(function (item) {
              var selected = item.path === change.path;
              return (
                <button
                  type="button"
                  key={item.id || item.path}
                  className={selected ? "active" : ""}
                  role="option"
                  aria-selected={selected}
                  onClick={function () {
                    setPickerOpen(false);
                    if (onSelect) onSelect(Object.assign({}, change, { path: item.path, file: item }));
                  }}
                >
                  <span className="wbc-artifact-picker-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.changes}</span>
                  <b>{item.path}</b>
                </button>
              );
            })}
        </WbcSplitPickerMenu>
      </header>
      <div className="wbc-change-split-diff wbc-change-diff">
        {diffState.loading ? (
          <p className="workbench-muted wbc-changes-state">{wbcT("workbenchChat.changes.loadingDiff", "Loading diff...")}</p>
        ) : diffState.error ? (
          <p className="workbench-muted wbc-changes-state">{diffState.error}</p>
        ) : diffState.diff && window.CyreneUI.require("diff").Panel ? (
          React.createElement(window.CyreneUI.require("diff").Panel, { diff: diffState.diff, mode: "text", hideHeader: true, hideHunkHeaders: true })
        ) : diffState.change && diffState.change.binary ? (
          <p className="workbench-muted wbc-changes-state">{wbcT("workbenchChat.changes.binary", "Binary or large file changed; text diff is unavailable.")}</p>
        ) : (
          <p className="workbench-muted wbc-changes-state">{wbcT("workbenchChat.changes.noDiff", "No text diff is available.")}</p>
        )}
      </div>
    </aside>
  );
}

function WbcViewerList({ files, selectedFile, onSelect }) {
  var items = Array.isArray(files) ? files : [];
  var selectedKey = wbcArtifactFileKey(selectedFile);
  return (
    <div className="wbc-resource-list">
      {items.map(function (item, index) {
        var file = item && item.file;
        var active = wbcArtifactFileKey(file) === selectedKey;
        return (
          <button type="button" className={"wbc-resource-list-row" + (active ? " current" : "")} key={wbcArtifactFileKey(file) + ":" + index} onClick={function () { if (onSelect) onSelect(file); }}>
            <span className="wbc-resource-list-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.viewer}</span>
            <span className="wbc-resource-list-copy"><b>{file && file.name || "file"}</b><small>{wbcAttachmentTypeLabel(file)}</small></span>
            <span className="wbc-resource-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
        );
      })}
    </div>
  );
}

function WbcBrowserList({ browserState, onSelect }) {
  var tabs = browserState && Array.isArray(browserState.tabs) ? browserState.tabs : [];
  var activeId = String(browserState && browserState.activeTabId || "");
  if (!tabs.length) return <p className="workbench-muted wbc-resource-empty">{wbcT("chat.side.browserUnavailable", "Browser view is unavailable.")}</p>;
  return (
    <div className="wbc-resource-list">
      {tabs.map(function (item, index) {
        var title = String(item.title || item.url || wbcT("chat.side.browser", "Browser"));
        var host = "";
        try { host = new URL(item.url).host; } catch (e) { host = String(item.url || ""); }
        return (
          <button type="button" className={"wbc-resource-list-row" + (String(item.id || "") === activeId ? " current" : "")} key={item.id || index} onClick={function () { if (onSelect) onSelect(item.id); }}>
            <span className="wbc-resource-list-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.browser}</span>
            <span className="wbc-resource-list-copy"><b>{title}</b><small>{host || wbcT("chat.side.browser", "Browser")}</small></span>
            <span className="wbc-resource-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
        );
      })}
    </div>
  );
}

function wbcMapItemKey(item) {
  return item ? String(item.kind || "map") + ":" + String(item.id || item.name || item.from_name || "") : "";
}

function wbcMapItemLabel(item) {
  if (!item) return wbcT("chat.side.map", "Map");
  if (item.kind === "route") return [item.from_name || item.from, item.to_name || item.to].filter(Boolean).join(" → ") || wbcT("workbenchChat.mapRoute", "Route");
  return String(item.name || wbcT("workbenchChat.mapPin", "Location"));
}

function useWbcMapData(chatId) {
  var [data, setData] = useWbcState({ chatId: "", loading: true, pins: [], routes: [] });
  useWbcEffect(function () {
    if (!chatId) { setData({ chatId: "", loading: false, pins: [], routes: [] }); return undefined; }
    var cancelled = false;
    setData({ chatId: chatId, loading: true, pins: [], routes: [] });
    fetch("/api/map/pins?session_id=" + encodeURIComponent(chatId))
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        if (!cancelled) setData({ chatId: chatId, loading: false, pins: Array.isArray(payload.pins) ? payload.pins : [], routes: Array.isArray(payload.routes) ? payload.routes : [] });
      })
      .catch(function () { if (!cancelled) setData({ chatId: chatId, loading: false, pins: [], routes: [] }); });
    return function () { cancelled = true; };
  }, [chatId]);
  return data.chatId === chatId ? data : { chatId: chatId, loading: true, pins: [], routes: [] };
}

function wbcMapItems(data) {
  return (data.pins || []).map(function (item) { return Object.assign({ kind: "pin" }, item); })
    .concat((data.routes || []).map(function (item) { return Object.assign({ kind: "route" }, item); }));
}

function WbcMapList({ chatId, onSelect }) {
  var data = useWbcMapData(chatId);
  var items = wbcMapItems(data);
  if (data.loading) return <p className="workbench-muted wbc-resource-empty">{wbcT("workbenchChat.mapLoading", "Loading maps...")}</p>;
  if (!items.length) return <p className="workbench-muted wbc-resource-empty">{wbcT("workbenchChat.mapEmpty", "No map pins in this chat yet.")}</p>;
  return (
    <div className="wbc-resource-list">
      {items.map(function (item, index) {
        var detail = item.kind === "route" ? (item.transport || item.route_note || "") : (item.note || item.note_md || [item.lat, item.lng].filter(function (value) { return value !== undefined; }).join(", "));
        var detailHtml = detail ? wbcRenderMapMarkdown(detail) : "";
        return (
          <button type="button" className="wbc-resource-list-row" key={wbcMapItemKey(item) + ":" + index} onClick={function () { if (onSelect) onSelect(item); }}>
            <span className="wbc-resource-list-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.map}</span>
            <span className="wbc-resource-list-copy">
              <b>{wbcMapItemLabel(item)}</b>
              {detailHtml
                ? <span className="wbc-resource-list-summary wbc-resource-list-markdown" dangerouslySetInnerHTML={{ __html: detailHtml }} />
                : <small>{item.kind === "route" ? wbcT("workbenchChat.mapRoute", "Route") : wbcT("workbenchChat.mapPin", "Location")}</small>}
            </span>
            <span className="wbc-resource-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
        );
      })}
    </div>
  );
}

function WbcResourceSplitHost({ openKey, children, closingChildren, width, onResize, splitSide, onToggleSide, onClose, onSplitDragStart, onSplitDragEnd }) {
  var [lastChildren, setLastChildren] = useWbcState(children || null);
  var [entered, setEntered] = useWbcState(false);
  // A closing variant is meaningful only after this host has actually shown
  // content. Without the lastChildren guard, browser hosts would mount an
  // invisible off-canvas closing panel on conversations that never opened a
  // split, allowing it to steal the native browser surface from PiP.
  var visibleChildren = openKey && children
    ? children
    : (lastChildren ? (closingChildren || lastChildren) : null);
  useWbcEffect(function () { if (openKey && children) setLastChildren(children); }, [openKey]);
  useWbcEffect(function () {
    if (openKey) {
      setEntered(false);
      var frame = requestAnimationFrame(function () { setEntered(true); });
      return function () { cancelAnimationFrame(frame); };
    }
    setEntered(false);
    var timer = setTimeout(function () { setLastChildren(null); }, 540);
    return function () { clearTimeout(timer); };
  }, [openKey]);
  if (!visibleChildren) return null;
  return <div className={"wbc-side-agent-split-motion" + (entered ? " open" : "")}>
    <WbcSideAgentSplitResizer width={width} onResize={onResize} splitSide={splitSide} />
    {visibleChildren}
  </div>;
}

function WbcMapSplitHost({ chatId, item, width, onSelect, onResize, onClose, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  var data = useWbcMapData(chatId);
  var items = wbcMapItems(data);
  var key = wbcMapItemKey(item);
  return (
       <WbcResourceSplitHost openKey={key} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {item ? <WbcMapSplit chatId={chatId} item={item} items={items} onSelect={onSelect} onClose={onClose} /> : null}
    </WbcResourceSplitHost>
  );
}

function WbcMapSplit({ chatId, item, items, onSelect, onClose }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  return (
    <aside className="wbc-side-agent-split wbc-map-split" aria-label={wbcT("chat.side.map", "Map")}>
      <div className="wbc-resource-split-picker-wrap">
        <header className="wbc-side-agent-split-head">
          <button type="button" className="wbc-side-agent-split-picker" onClick={function () { setPickerOpen(function (open) { return !open; }); }} aria-expanded={pickerOpen}>
            <span className="wbc-side-agent-split-title"><span>{wbcT("chat.side.map", "Map")}</span><b>{wbcMapItemLabel(item)}</b></span><span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
          <button type="button" className="wbc-side-agent-split-close" onClick={onClose} aria-label={wbcT("workbenchChat.closeMap", "Close map")}>{WBC_ICONS.x}</button>
        </header>
        <WbcSplitPickerMenu open={pickerOpen} className="wbc-side-agent-split-menu wbc-resource-picker-menu" role="listbox">{items.map(function (next) { var selected = wbcMapItemKey(next) === wbcMapItemKey(item); return <button type="button" key={wbcMapItemKey(next)} className={selected ? "active" : ""} role="option" aria-selected={selected} onClick={function () { setPickerOpen(false); if (onSelect) onSelect(next); }}><span aria-hidden="true">{WBC_SIDE_TAB_ICONS.map}</span><b>{wbcMapItemLabel(next)}</b></button>; })}</WbcSplitPickerMenu>
      </div>
      <div className="wbc-resource-split-body"><WbcMapTab chatId={chatId} focusItem={item} /></div>
    </aside>
  );
}

function WbcBrowserSplitHost({ tabId, browserState, browserSessionId, width, onSelect, onResize, onClose, onTakeoverComplete, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  var tabs = browserState && Array.isArray(browserState.tabs) ? browserState.tabs : [];
  var activeStateTab = browserState && browserState.activeTab || {};
  var resolvedTabId = tabId === "__active__"
    ? String(browserState && browserState.activeTabId || activeStateTab.id || "")
    : String(tabId || "");
  var browserSplit = <WbcBrowserSplit active={!!tabId} tabId={resolvedTabId} tabs={tabs} browserState={browserState} browserSessionId={browserSessionId} onSelect={onSelect} onClose={onClose} onTakeoverComplete={onTakeoverComplete} />;
  return (
       <WbcResourceSplitHost openKey={tabId} closingChildren={!tabId ? browserSplit : null} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {tabId ? browserSplit : null}
    </WbcResourceSplitHost>
  );
}

function WbcBrowserSplit({ active: splitActive = true, tabId, tabs, browserState, browserSessionId, onSelect, onClose, onTakeoverComplete }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  var [liveState, setLiveState] = useWbcState(browserState || {});
  var bridge = window.cyrene && window.cyrene.browser;
  var BrowserIcon = window.CyreneUI.require("browser").Icon;
  var stateTabs = liveState && Array.isArray(liveState.tabs) ? liveState.tabs : [];
  var propTabs = Array.isArray(tabs) ? tabs : [];
  // setContext briefly reports an empty state while ownership moves from the
  // floating window to the split. Preserve the last useful list during that
  // handoff so an open picker cannot collapse into an empty strip by itself.
  var liveTabs = stateTabs.length ? stateTabs : propTabs;
  var active = liveTabs.find(function (tab) { return String(tab.id || "") === String(tabId || ""); }) || liveState && liveState.activeTab || browserState && browserState.activeTab || liveTabs[0] || {};

  useWbcEffect(function () {
    if (!bridge || !browserSessionId) return undefined;
    if (browserState) setLiveState(browserState);
    if (typeof bridge.getState === "function") {
      bridge.getState(browserSessionId).then(function (next) {
        if (next && next.ok !== false) setLiveState(next);
      }).catch(function () {});
    }
    if (typeof bridge.onState !== "function") return undefined;
    return bridge.onState(function (next) {
      if (next && next.ok !== false && String(next.sessionId || "") === String(browserSessionId || "")) setLiveState(next);
    });
  }, [browserSessionId]);

  function updateFrom(next) {
    if (next && next.ok !== false && Array.isArray(next.tabs)) setLiveState(next);
    return next;
  }

  function setBrowserPickerOpen(open) {
    var nextOpen = open === true;
    if (nextOpen) {
      // Electron's WebContentsView always composites above renderer DOM. Ask
      // the viewport to replace it with a same-frame preview before opening
      // the menu, keeping the webpage visible while the dropdown sits above it.
      setPickerOpen(true);
      window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
        detail: {
          obscured: true,
          preview: true,
          sessionId: String(browserSessionId || ""),
        },
      }));
      return;
    }
    setPickerOpen(false);
    window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
      detail: { obscured: false, sessionId: String(browserSessionId || "") },
    }));
  }

  useWbcEffect(function () {
    return function () {
      window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
        detail: { obscured: false, sessionId: String(browserSessionId || "") },
      }));
    };
  }, [browserSessionId]);

  function selectTab(tab) {
    if (!tab || !tab.id) return;
    setBrowserPickerOpen(false);
    if (onSelect) onSelect(tab.id);
    if (bridge && typeof bridge.activateTab === "function") {
      bridge.activateTab({ sessionId: browserSessionId, tabId: tab.id }).then(updateFrom).catch(function () {});
    }
  }

  function refreshTab(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!bridge || !tab || !tab.id) return;
    bridge.reload({ sessionId: browserSessionId, tabId: tab.id }).then(updateFrom).catch(function () {});
  }

  function toggleMute(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!bridge || !tab || !tab.id || typeof bridge.setMuted !== "function") return;
    bridge.setMuted({ sessionId: browserSessionId, tabId: tab.id, muted: !tab.muted }).then(updateFrom).catch(function () {});
  }

  function closeTab(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!bridge || !tab || !tab.id || typeof bridge.closeTab !== "function") return;
    bridge.closeTab({ sessionId: browserSessionId, tabId: tab.id }).then(function (next) {
      updateFrom(next);
      var remaining = next && Array.isArray(next.tabs) ? next.tabs : [];
      if (!remaining.length) {
        setBrowserPickerOpen(false);
        if (onClose) onClose();
        return;
      }
      if (String(tab.id || "") === String(active.id || tabId || "")) {
        var nextId = String(next.activeTabId || next.activeTab && next.activeTab.id || remaining[0].id || "");
        if (nextId && onSelect) onSelect(nextId);
      }
    }).catch(function () {});
  }

  return (
    <aside className="wbc-side-agent-split wbc-browser-split" aria-label={wbcT("chat.side.browser", "Browser")}>
      <div className="wbc-resource-split-picker-wrap">
        <header className="wbc-side-agent-split-head">
          <button type="button" className="wbc-side-agent-split-picker" onClick={function () { setBrowserPickerOpen(!pickerOpen); }} aria-expanded={pickerOpen}>
            <span className="wbc-side-agent-split-title"><span>{wbcT("chat.side.browser", "Browser")}</span><b>{active.title || active.url || wbcT("chat.side.browser", "Browser")}</b></span><span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
          <button type="button" className="wbc-browser-split-action" onClick={function (event) { refreshTab(active, event); }} aria-label={wbcT("browser.context.reload", "Reload")} title={wbcT("browser.context.reload", "Reload")}>{BrowserIcon ? <BrowserIcon name="reload" size={15} /> : WBC_ICONS.retry}</button>
          <button type="button" className={"wbc-browser-split-action" + (active.muted ? " active" : "")} onClick={function (event) { toggleMute(active, event); }} aria-label={active.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")} title={active.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")}>{BrowserIcon ? <BrowserIcon name={active.muted ? "muted" : "volume"} size={15} /> : null}</button>
          <button type="button" className="wbc-side-agent-split-close" onClick={onClose} aria-label={wbcT("workbenchChat.closeBrowser", "Close browser")}>{WBC_ICONS.x}</button>
        </header>
        <WbcSplitPickerMenu open={pickerOpen} className="wbc-side-agent-split-menu wbc-resource-picker-menu wbc-browser-picker-menu" role="listbox">{liveTabs.map(function (tab) { var selected = String(tab.id || "") === String(active.id || tabId || ""); return <div key={tab.id} className={"wbc-browser-picker-row" + (selected ? " active" : "")} role="option" aria-selected={selected}><button type="button" className="wbc-browser-picker-select" onClick={function () { selectTab(tab); }}><span className="wbc-browser-picker-favicon" aria-hidden="true"><span className="wbc-browser-picker-favicon-fallback">{WBC_SIDE_TAB_ICONS.browser}</span>{tab.favicon ? <img src={tab.favicon} alt="" draggable="false" onError={function (event) { event.currentTarget.hidden = true; }} /> : null}</span><b>{tab.title || tab.url || wbcT("chat.side.browser", "Browser")}</b></button><span className="wbc-browser-picker-actions"><button type="button" onClick={function (event) { refreshTab(tab, event); }} aria-label={wbcT("browser.context.reload", "Reload")} title={wbcT("browser.context.reload", "Reload")}>{BrowserIcon ? <BrowserIcon name="reload" size={14} /> : WBC_ICONS.retry}</button><button type="button" className={tab.muted ? "active" : ""} onClick={function (event) { toggleMute(tab, event); }} aria-label={tab.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")} title={tab.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")}>{BrowserIcon ? <BrowserIcon name={tab.muted ? "muted" : "volume"} size={14} /> : null}</button><button type="button" onClick={function (event) { closeTab(tab, event); }} aria-label={wbcT("browser.context.closeTab", "Close tab")} title={wbcT("browser.context.closeTab", "Close tab")}>{WBC_ICONS.x}</button></span></div>; })}</WbcSplitPickerMenu>
      </div>
      <div className="wbc-resource-split-body wbc-browser-split-body">
        {splitActive && window.CyreneUI.require("browser").ViewportPanel ? React.createElement(window.CyreneUI.require("browser").ViewportPanel, { browserState: liveState, browserSessionId: browserSessionId, roundId: liveState && liveState.roundId || browserState && browserState.roundId || "", desiredTabId: active.id || tabId, onClose: onClose, onTakeoverComplete: onTakeoverComplete, zoomEnabled: false, hideTabStrip: true, hideReload: true, hideMute: true, splitChrome: true }) : null}
      </div>
    </aside>
  );
}

function WbcSubagentsSplitHost({ open, data, loading, width, onSelectRound, onResize, onClose, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  return (
       <WbcResourceSplitHost openKey={open ? "subagents" : ""} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {open ? <aside className="wbc-side-agent-split wbc-subagents-split" aria-label={wbcT("workbenchChat.subagents", "Subagents")}><header className="wbc-side-agent-split-head wbc-static-split-head"><span className="wbc-side-agent-split-title"><span>{wbcT("workbenchChat.subagents", "Subagents")}</span><b>{data && Array.isArray(data.agents) ? wbcT("workbenchChat.subagent.count", "{n} agents", { n: data.agents.length }) : ""}</b></span><button type="button" className="wbc-side-agent-split-close" onClick={onClose} aria-label={wbcT("workbenchChat.closeSubagents", "Close subagents")}>{WBC_ICONS.x}</button></header><div className="wbc-resource-split-body wbc-subagents-split-body"><WbcSubagentsTab data={data} loading={loading} onSelectRound={onSelectRound} /></div></aside> : null}
    </WbcResourceSplitHost>
  );
}

function WbcSideAgentSplitHost({ agent, agents, width, project, onOpenFile, onUpdate, onSelect, onResize, onClose, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  return (
       <WbcResourceSplitHost openKey={agent && agent.id || ""} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {agent ? (
        <WbcSideAgentSplit
          agent={agent}
          agents={agents}
          project={project}
          onOpenFile={onOpenFile}
          onUpdate={onUpdate}
          onSelect={onSelect}
          onClose={onClose}
        />
      ) : null}
    </WbcResourceSplitHost>
  );
}

// A rail chat dragged onto the side panel opens as a read-only conversation
// beside the main thread. It polls while the source chat is running so a
// background run keeps the split in sync.
function WbcChatSplitHost({ chatId, width, onOpenFile, onResize, onClose, onOpenInMain, splitSide, onToggleSide, project, onSplitDragStart, onSplitDragEnd }) {
  // The openKey must be empty when no chat is split, otherwise the host's
  // close branch (exit animation + lastChildren cleanup) never runs.
  var key = chatId ? "chat:" + chatId : "";
  return (
     <WbcResourceSplitHost openKey={key} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {chatId ? <WbcChatSplit chatId={chatId} project={project} onOpenFile={onOpenFile} onClose={onClose} onOpenInMain={onOpenInMain} /> : null}
    </WbcResourceSplitHost>
  );
}

function WbcChatSplit({ chatId, project, onOpenFile, onClose, onOpenInMain }) {
  var [chat, setChat] = useWbcState(null);
  var [loading, setLoading] = useWbcState(true);
  var [error, setError] = useWbcState("");
  var [streamText, setStreamText] = useWbcState("");
  var [running, setRunning] = useWbcState(false);
  var scrollRef = useWbcRef(null);
  var chatIdRef = useWbcRef(chatId);
  var pollTimerRef = useWbcRef(null);
  var disposedRef = useWbcRef(false);
  var streamAttachedRef = useWbcRef(false);
  var runStartedAtRef = useWbcRef(Date.now());
  chatIdRef.current = chatId;

  function stopPolling() {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  function refresh(background) {
    var requestedId = chatIdRef.current;
    if (!requestedId) return Promise.resolve(null);
    if (!background) setLoading(true);
    return WorkbenchChatModel.getChat(requestedId, { toast: false })
      .then(function (fresh) {
        if (disposedRef.current || String(chatIdRef.current || "") !== requestedId) return null;
        setChat(fresh);
        setLoading(false);
        return fresh;
      })
      .catch(function (err) {
        if (disposedRef.current || String(chatIdRef.current || "") !== requestedId) return null;
        setError(wbcErrorText(err));
        setLoading(false);
        return null;
      });
  }

  useWbcEffect(function () {
    disposedRef.current = false;
    setChat(null);
    setError("");
    setLoading(true);
    stopPolling();
    refresh(true).then(function (fresh) {
      if (disposedRef.current || !fresh || fresh.status !== "running") return;
      pollTimerRef.current = setInterval(function () {
        refresh(true).then(function (next) {
          if (next && next.status !== "running") stopPolling();
        });
      }, 5000);
    });
    return function () {
      disposedRef.current = true;
      stopPolling();
    };
  }, [chatId]);

  useWbcLayoutEffect(function () {
    var el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat && chat.messages && chat.messages.length, loading, running, streamText]);

  // Sending goes through the same streamed sendMessage path as the main
  // conversation, so the split transcript updates live while the agent works.
  function streamHandlers() {
    return {
      onReplyStart: function () {
        if (disposedRef.current) return;
        setStreamText("");
      },
      onReplyDelta: function (delta) {
        if (disposedRef.current) return;
        setStreamText(function (current) { return current + delta; });
      },
      onReplyDone: function (text) {
        if (disposedRef.current) return;
        setStreamText(String(text || ""));
      },
      onSaved: function () {
        if (disposedRef.current) return;
        setRunning(false);
        streamAttachedRef.current = false;
        refresh(true).finally(function () {
          if (disposedRef.current) return;
          setStreamText("");
        });
      },
      onAwaitingUser: function () {
        if (disposedRef.current) return;
        setRunning(false);
        streamAttachedRef.current = false;
        refresh(true).finally(function () {
          if (disposedRef.current) return;
          setStreamText("");
        });
      },
      onError: function (err) {
        if (disposedRef.current) return;
        setError(wbcErrorText(err));
        setRunning(false);
        streamAttachedRef.current = false;
        setStreamText("");
      },
    };
  }

  function ownStream(promise) {
    streamAttachedRef.current = true;
    setRunning(true);
    promise.catch(function (err) {
      if (disposedRef.current || !(err && err.name === "AbortError")) {
        setError(wbcErrorText(err));
      }
    }).finally(function () {
      if (disposedRef.current) return;
      streamAttachedRef.current = false;
      setRunning(false);
      refresh(true).catch(function () {}).finally(function () {
        if (disposedRef.current) return;
        setStreamText("");
      });
    });
  }

  function submit(payload) {
    var question = String(payload && payload.message || "").trim();
    var attachments = payload && Array.isArray(payload.attachments) ? payload.attachments : [];
    var current = chatIdRef.current;
    if ((!question && !attachments.length) || running || !current) return;
    var optimistic = {
      id: "chat_split_pending_" + Date.now(),
      role: "user",
      content: question,
      attachments: attachments,
      createdAt: new Date().toISOString(),
      optimistic: true,
    };
    setChat(function (prev) {
      if (!prev || !prev.id) return prev;
      return { ...prev, messages: (prev.messages || []).concat([optimistic]) };
    });
    setError("");
    setStreamText("");
    runStartedAtRef.current = Date.now();
    ownStream(WorkbenchChatModel.sendMessage(current, {
      message: question,
      attachments: attachments,
    }, streamHandlers()));
  }

  function stop() {
    if (!running) return;
    WorkbenchChatModel.interrupt(chatIdRef.current).catch(function () {});
    setRunning(false);
  }

  var messages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  var errorText = error;
  return (
    <aside className="wbc-side-agent-split wbc-chat-split" aria-label={wbcT("workbenchChat.chatSplitLabel", "Chat")}>
      <div className="wbc-thread-stage wbc-chat-split-stage">
        <div className="wbc-thread" ref={scrollRef}>
        {loading && !messages.length && (
          <div className="wbc-chat-split-state" role="status">
            <span className="wbc-spinner" aria-hidden="true" />
            <span>{wbcT("workbenchChat.loading", "Loading chats...")}</span>
          </div>
        )}
        {!loading && !messages.length && !errorText && (
          <div className="wbc-chat-split-state">{wbcT("workbenchChat.noMessages", "No messages yet")}</div>
        )}
        {errorText && <div className="wbc-side-agent-error" role="alert">{errorText}</div>}
        {messages.map(function (message) {
          return (
            <WbcThreadItem key={message.id || message.createdAt}>
              {message.role === "user"
                ? <WbcUserMessage msg={message} onOpenFile={onOpenFile} />
                : <WbcAssistantMessage msg={message} onOpenFile={onOpenFile} />}
            </WbcThreadItem>
          );
        })}
        {running && !streamText && (
          <WbcThreadItem>
            <WbcHeartbeat startedAt={runStartedAtRef.current} />
          </WbcThreadItem>
        )}
        {running && streamText && (
          <WbcThreadItem>
            <WbcLiveMessage runtime={{ text: streamText }} onOpenFile={onOpenFile} />
          </WbcThreadItem>
        )}
        </div>
      </div>
      <WbcComposer
        chat={chat}
        project={project}
        runtime={streamText ? { text: streamText } : null}
        running={running}
        onSend={submit}
        onInterrupt={stop}
        draftNamespace={"chat-split:"}
        autoFocus={false}
        clearOnSend={true}
        error={error}
        errorKind="message"
        compact={false}
        hideDisclaimer={true}
        placeholder={wbcT("workbenchChat.placeholder", "Message Cyrene...")}
        runningPlaceholder={wbcT("workbenchChat.placeholderRunning", "Send guidance to the running agent...")}
      />
    </aside>
  );
}

function WbcSideAgentSplitResizer({ width, onResize, splitSide }) {
  function clampWidth(next) {
    return wbcClampSideSplitWidth(next, window.innerWidth);
  }

  function startResize(event) {
    if (event.button !== 0 || !onResize) return;
    event.preventDefault();
    var startX = event.clientX;
    var startWidth = Number(width) || 520;
    function move(moveEvent) {
      // The resizer rides the edge that faces the conversation: right-anchored
      // panels widen when dragged left, left-anchored ones when dragged right.
      var delta = splitSide === "left"
        ? (moveEvent.clientX - startX)
        : (startX - moveEvent.clientX);
      onResize(clampWidth(startWidth + delta));
    }
    function stop() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.classList.remove("wbc-resizing-side-agent");
    }
    document.body.classList.add("wbc-resizing-side-agent");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  }

  function resizeWithKeyboard(event) {
    if (!onResize || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
    event.preventDefault();
    var step = splitSide === "left"
      ? (event.key === "ArrowRight" ? 16 : -16)
      : (event.key === "ArrowLeft" ? 16 : -16);
    onResize(clampWidth((Number(width) || 520) + step));
  }

  return (
    <div
      className="wbc-side-agent-split-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label={wbcT("workbenchChat.detailPanel.resize", "Resize detail panel")}
      tabIndex={0}
      onPointerDown={startResize}
      onKeyDown={resizeWithKeyboard}
    />
  );
}

// Top edge of the main conversation while a detail split is open. The grip
// lifts the conversation with a native drag (like an image/document): dropping
// it on the rail closes the split, and dropping it on either side moves the
// split there. Its menu opens the floating conversation panel, swaps the split
// side, or closes it. Content splits intentionally have no second grip.
function WbcSplitGripBar({ side, onToggleSide, onClose, onOpenConversationPanel, onSplitDragStart, onSplitDragEnd }) {
  var [menuOpen, setMenuOpen] = useWbcState(false);
  var rootRef = useWbcRef(null);

  useWbcEffect(function () {
    if (!menuOpen) return undefined;
    function closeOutside(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) setMenuOpen(false);
    }
    document.addEventListener("pointerdown", closeOutside);
    return function () { document.removeEventListener("pointerdown", closeOutside); };
  }, [menuOpen]);

  function handleKey(event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setMenuOpen(function (open) { return !open; });
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      if (onToggleSide) onToggleSide();
    }
  }

  var swapLabel = side === "left"
    ? wbcT("workbenchChat.splitMoveRight", "Move split to the right side")
    : wbcT("workbenchChat.splitMoveLeft", "Move split to the left side");
  function openConversationPanel() {
    setMenuOpen(false);
    if (onOpenConversationPanel) onOpenConversationPanel();
  }
  return (
    <div className="wbc-split-grip-bar-host" ref={rootRef}>
      <div
        className="wbc-side-split-grip-bar"
        role="button"
        tabIndex={0}
        draggable="true"
        aria-label={wbcT("workbenchChat.detailPanel.move", "Move split panel")}
        title={wbcT("workbenchChat.detailPanel.move", "Move split panel")}
        onClick={function () { setMenuOpen(function (open) { return !open; }); }}
        onDragStart={function (event) { if (onSplitDragStart) onSplitDragStart(event); }}
        onDragEnd={function () { if (onSplitDragEnd) onSplitDragEnd(); }}
        onKeyDown={handleKey}
      >
        <span className="wbc-side-split-grip-bar-visual" aria-hidden="true" />
      </div>
      {menuOpen && (
        <div className="wbc-side-split-grip-menu" role="menu">
          <button
            type="button"
            role="menuitem"
            onClick={openConversationPanel}
          >
            <span aria-hidden="true">{WBC_ICONS.sidebar}</span>
            <span>{wbcT("workbenchChat.detailPanel.openConversationPanel", "Open conversation panel")}</span>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={function () { setMenuOpen(false); if (onToggleSide) onToggleSide(); }}
          >
            <span aria-hidden="true">{WBC_ICONS.chevronLeft}{WBC_ICONS.chevronRight}</span>
            <span>{swapLabel}</span>
          </button>
          {onClose ? (
            <button
              type="button"
              role="menuitem"
              onClick={function () { setMenuOpen(false); onClose(); }}
            >
              <span aria-hidden="true">{WBC_ICONS.x}</span>
              <span>{wbcT("workbenchChat.detailPanel.close", "Close split panel")}</span>
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

function WbcSideAgentSplit({ agent, agents, project, onOpenFile, onUpdate, onSelect, onClose }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  var headerRef = useWbcRef(null);
  var items = Array.isArray(agents) ? agents : [];
  var title = String((agent && (agent.sourceQuote || agent.title)) || "")
    .replace(/\s+/g, " ")
    .trim();

  useWbcEffect(function () {
    if (!pickerOpen) return undefined;
    function closePicker(event) {
      if (headerRef.current && !headerRef.current.contains(event.target)) setPickerOpen(false);
    }
    document.addEventListener("pointerdown", closePicker);
    return function () { document.removeEventListener("pointerdown", closePicker); };
  }, [pickerOpen]);

  return (
    <aside className="wbc-side-agent-split" aria-label={wbcT("workbenchChat.sideAgent.conversation", "Side conversation")}>
      <header className="wbc-side-agent-split-head" ref={headerRef}>
        <button
          type="button"
          className="wbc-side-agent-split-picker"
          onClick={function () { setPickerOpen(function (open) { return !open; }); }}
          aria-expanded={pickerOpen}
          aria-haspopup="listbox"
        >
          <span className="wbc-side-agent-split-title">
          <span>{wbcT("workbenchChat.sideAgent.tab", "Side questions")}</span>
          <b title={title}>{title || wbcT("workbenchChat.sideAgent.untitled", "Side question")}</b>
          </span>
          <span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
        </button>
        <button
          type="button"
          className="wbc-side-agent-split-close"
          onClick={onClose}
          title={wbcT("workbenchChat.sideAgent.closeConversation", "Close side conversation")}
          aria-label={wbcT("workbenchChat.sideAgent.closeConversation", "Close side conversation")}
        >{WBC_ICONS.x}</button>
        <WbcSplitPickerMenu open={pickerOpen} role="listbox" aria-label={wbcT("workbenchChat.sideAgent.list", "Side questions")}>
            {items.map(function (item, index) {
              var itemTitle = String(item.sourceQuote || item.title || "").replace(/\s+/g, " ").trim();
              var selected = item.id === (agent && agent.id);
              return (
                <button
                  type="button"
                  key={item.id}
                  className={selected ? "active" : ""}
                  role="option"
                  aria-selected={selected}
                  onClick={function () {
                    setPickerOpen(false);
                    if (onSelect) onSelect(item.id);
                  }}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <b>{itemTitle || wbcT("workbenchChat.sideAgent.untitled", "Side question")}</b>
                </button>
              );
            })}
        </WbcSplitPickerMenu>
      </header>
      <WbcSideAgentTab agent={agent} project={project} onOpenFile={onOpenFile} onUpdate={onUpdate} />
    </aside>
  );
}

function WbcSideAgentTab({ agent, project, onOpenFile, onUpdate }) {
  var agentRef = useWbcRef(agent);
  var scrollRef = useWbcRef(null);
  var mountedRef = useWbcRef(true);
  var streamAttachedRef = useWbcRef(false);
  var runStartedAtRef = useWbcRef(Date.now());
  var [running, setRunning] = useWbcState(!!(agent && agent.status === "running"));
  var [streamText, setStreamText] = useWbcState("");
  var [error, setError] = useWbcState("");

  useWbcEffect(function () {
    agentRef.current = agent;
    setRunning(!!(agent && agent.status === "running"));
  }, [agent]);

  useWbcEffect(function () {
    mountedRef.current = true;
    return function () { mountedRef.current = false; };
  }, []);

  useWbcLayoutEffect(function () {
    var el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [agent && agent.messages && agent.messages.length, streamText, running]);

  function refreshAgent() {
    return WorkbenchChatModel.getChat(agentRef.current.id).then(function (fresh) {
      agentRef.current = fresh;
      onUpdate(fresh);
      if (mountedRef.current) setRunning(fresh.status === "running");
      return fresh;
    });
  }

  function streamHandlers() {
    return {
      onReplyStart: function () {
        if (mountedRef.current) setStreamText("");
      },
      onReplyDelta: function (delta) {
        if (mountedRef.current) setStreamText(function (current) { return current + delta; });
      },
      onReplyDone: function (text) {
        if (mountedRef.current) setStreamText(String(text || ""));
      },
      onSaved: function () {
        refreshAgent().finally(function () {
          streamAttachedRef.current = false;
          if (mountedRef.current) {
            setStreamText("");
            setRunning(false);
          }
        });
      },
      onAwaitingUser: function () {
        refreshAgent().finally(function () {
          streamAttachedRef.current = false;
          if (mountedRef.current) {
            setStreamText("");
            setRunning(false);
          }
        });
      },
      onError: function (err) {
        if (mountedRef.current) setError(wbcErrorText(err));
      },
    };
  }

  function ownStream(promise) {
    streamAttachedRef.current = true;
    promise.catch(function (err) {
      if (mountedRef.current && !(err && err.name === "AbortError")) {
        setError(wbcErrorText(err));
      }
    }).finally(function () {
      if (!streamAttachedRef.current) return;
      streamAttachedRef.current = false;
      refreshAgent().catch(function () {}).finally(function () {
        if (mountedRef.current) {
          setStreamText("");
          setRunning(false);
        }
      });
    });
  }

  useWbcEffect(function () {
    if (!agent || agent.status !== "running" || streamAttachedRef.current) return undefined;
    runStartedAtRef.current = Date.now();
    setRunning(true);
    ownStream(WorkbenchChatModel.reconnectRun(agent.id, streamHandlers()));
    return undefined;
  }, [agent && agent.id, agent && agent.status]);

  function submit(payload) {
    var question = String(payload && payload.message || "").trim();
    var attachments = payload && Array.isArray(payload.attachments) ? payload.attachments : [];
    var current = agentRef.current;
    if ((!question && !attachments.length) || running || !current || !current.id) return;
    var optimistic = {
      id: "side_user_pending_" + Date.now(),
      role: "user",
      content: question,
      attachments: attachments,
      createdAt: new Date().toISOString(),
      optimistic: true,
    };
    var next = {
      ...current,
      status: "running",
      messages: (current.messages || []).concat([optimistic]),
    };
    agentRef.current = next;
    onUpdate(next);
    setError("");
    setStreamText("");
    runStartedAtRef.current = Date.now();
    setRunning(true);
    ownStream(WorkbenchChatModel.sendMessage(
      current.id,
      {
        message: question,
        attachments: attachments,
        mode: current.permissionMode || payload.mode || "default",
        model: current.modelSelectionId || payload.model || "",
        reasoningEffort: current.reasoningEffort || payload.reasoningEffort || "",
      },
      streamHandlers()
    ));
  }

  function stop() {
    if (!running) return;
    WorkbenchChatModel.interrupt(agentRef.current.id).catch(function (err) {
      if (mountedRef.current) setError(wbcErrorText(err));
    });
  }

  var messages = agent && Array.isArray(agent.messages) ? agent.messages : [];
  var hasAsked = messages.some(function (message) { return message.role === "user"; });
  return (
    <section className="wbc-side-agent">
      {!hasAsked && <blockquote className="wbc-side-agent-quote">
        <span>{wbcT("workbenchChat.sideAgent.quote", "Selected text")}</span>
        <p>{agent && agent.sourceQuote}</p>
      </blockquote>}
      <div className="wbc-side-agent-thread wbc-thread" ref={scrollRef}>
        {!messages.length && !running && (
          <div className="wbc-side-agent-empty">
            <b>{wbcT("workbenchChat.sideAgent.askTitle", "Ask about this text")}</b>
            <p>{wbcT("workbenchChat.sideAgent.askHint", "This agent has its own context and will not interrupt the main conversation.")}</p>
          </div>
        )}
        {messages.map(function (message) {
          return (
            <WbcThreadItem key={message.id || message.createdAt}>
              {message.role === "user"
                ? <WbcUserMessage msg={message} onOpenFile={onOpenFile} />
                : <WbcAssistantMessage msg={message} onOpenFile={onOpenFile} />}
            </WbcThreadItem>
          );
        })}
        {running && !streamText && (
          <WbcThreadItem>
            <WbcHeartbeat startedAt={runStartedAtRef.current} />
          </WbcThreadItem>
        )}
        {running && streamText && (
          <WbcThreadItem>
            <WbcLiveMessage runtime={{ text: streamText }} onOpenFile={onOpenFile} />
          </WbcThreadItem>
        )}
      </div>
      {error && <div className="wbc-side-agent-error" role="alert">{error}</div>}
      <div className="wbc-side-agent-composer-host">
        <WbcComposer
          chat={agent}
          project={project}
          runtime={streamText ? { text: streamText } : null}
          running={running}
          onSend={submit}
          onInterrupt={stop}
          draftNamespace="side-agent:"
          autoFocus={false}
          clearOnSend={true}
          error={error}
          errorKind="message"
          compact={true}
          placeholder={wbcT("workbenchChat.sideAgent.placeholder", "Ask a question about the selected text…")}
          runningPlaceholder={wbcT("workbenchChat.sideAgent.placeholderRunning", "Agent is working…")}
        />
      </div>
    </section>
  );
}

function WbcSideAgentsPanel({
  agents,
  activeAgentId,
  loading,
  onSelect,
  onDelete,
}) {
  var items = Array.isArray(agents) ? agents : [];

  if (loading && !items.length) {
    return (
      <div className="wbc-side-agent-panel-state" role="status">
        <span className="wbc-spinner" aria-hidden="true" />
        <span>{wbcT("workbenchChat.sideAgent.loading", "Loading side agents…")}</span>
      </div>
    );
  }

  if (!items.length) {
    return (
      <div className="wbc-side-agent-panel-state">
        <b>{wbcT("workbenchChat.sideAgent.empty", "No side questions")}</b>
        <span>{wbcT("workbenchChat.sideAgent.emptyHint", "Select text in the conversation to start one.")}</span>
      </div>
    );
  }

  return (
    <div className="wbc-side-agents-panel">
      <div className="wbc-side-agent-list" role="list" aria-label={wbcT("workbenchChat.sideAgent.list", "Side questions")}>
        {items.map(function (agent, index) {
          var selected = agent.id === activeAgentId;
          var preview = String(agent.sourceQuote || agent.title || "")
            .replace(/\s+/g, " ")
            .trim();
          var running = agent.status === "running";
          return (
            <div key={agent.id} className={"wbc-side-agent-index-row" + (selected ? " active" : "")} role="listitem">
              <button
                type="button"
                className="wbc-side-agent-index-select"
                onClick={function () { onSelect(agent.id); }}
                aria-current={selected ? "true" : undefined}
                title={preview}
              >
                <span className="wbc-side-agent-index-number">{String(index + 1).padStart(2, "0")}</span>
                <span className="wbc-side-agent-index-copy">
                  <b>{preview || wbcT("workbenchChat.sideAgent.untitled", "Side question")}</b>
                  <small>{running
                    ? wbcT("workbenchChat.sideAgent.thinking", "Thinking…")
                    : wbcT("workbenchChat.sideAgent.ready", "Ready")}</small>
                </span>
              </button>
              <button
                type="button"
                className="wbc-side-agent-index-close"
                onClick={function () { onDelete(agent.id); }}
                title={wbcT("workbenchChat.sideAgent.close", "Close side agent")}
                aria-label={wbcT("workbenchChat.sideAgent.close", "Close side agent")}
              >×</button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function WbcSideAccordionBody({ expanded, flush, bodyClass, children }) {
  var [mounted, setMounted] = useWbcState(expanded);
  var contentRef = useWbcRef(children);

  if (expanded) contentRef.current = children;

  useWbcEffect(function () {
    if (expanded) {
      setMounted(true);
      return undefined;
    }
    if (!mounted) return undefined;
    var timer = window.setTimeout(function () { setMounted(false); }, 190);
    return function () { window.clearTimeout(timer); };
  }, [expanded, mounted]);

  if (!mounted && !expanded) return null;
  return (
    <div className={"wbc-side-collapse" + (expanded ? " open" : " closing")} aria-hidden={!expanded}>
      <div className="wbc-side-collapse-inner">
        <div className={"wbc-side-body" + (flush ? " flush" : "") + (bodyClass ? " " + bodyClass : "")}>{contentRef.current}</div>
      </div>
    </div>
  );
}

function WbcSide({
  project,
  chat,
  chatLoading,
  chatDetailed,
  chats,
  activeChatId,
  onSelectChat,
  runtime,
  subagentData,
  subagentLoading,
  onSelectSubagentRound,
  tab,
  onTabChange,
  viewerFile,
  onOpenFile,
  onSelectArtifact,
  onSelectChange,
  onSelectViewer,
  onSelectMap,
  onSelectBrowser,
  onOpenSubagents,
  onViewerViewed,
  onRename,
  onDelete,
  onToTask,
  toTaskBusy,
  onCompact,
  compactBusy,
  sideAgents,
  sideAgentsLoading,
  activeSideAgentId,
  onSelectSideAgent,
  onUpdateSideAgent,
  onDeleteSideAgent,
  onBrowserTakeoverComplete,
  browserActiveByChat,
  browserSuppressed,
  onToggleSide,
  floating,
  onCloseFloating,
}) {
  window.CyreneUI.require("data").useVersion();
  var [changesAvailability, setChangesAvailability] = useWbcState({ chatId: "", hasChanges: false });
  var hasWorkspaceChanges = (
    String(changesAvailability.chatId || "") === String(activeChatId || "")
    && !!changesAvailability.hasChanges
  );

  useWbcEffect(function () {
    var currentChatId = String(activeChatId || "");
    var disposed = false;
    setChangesAvailability({ chatId: currentChatId, hasChanges: false });
    if (!currentChatId || currentChatId.indexOf("legacy:") === 0) return undefined;

    function revealFromEvent(event) {
      var detail = (event && event.detail) || {};
      var eventChatId = String(detail.chatId || detail.session_id || "");
      if (eventChatId && eventChatId !== currentChatId) return;
      if (Number(detail.fileCount || 0) > 0) {
        setChangesAvailability({ chatId: currentChatId, hasChanges: true });
      }
    }

    window.addEventListener("workbench:workspace-changes", revealFromEvent);
    WorkbenchChatModel.getChanges(currentChatId, { toast: false })
      .then(function (payload) {
        if (disposed) return;
        var sets = Array.isArray(payload && payload.changeSets) ? payload.changeSets : [];
        setChangesAvailability(function (current) {
          if (String(current.chatId || "") === currentChatId && current.hasChanges) return current;
          return { chatId: currentChatId, hasChanges: sets.length > 0 };
        });
      })
      .catch(function () {
        if (!disposed) {
          setChangesAvailability(function (current) {
            if (String(current.chatId || "") === currentChatId && current.hasChanges) return current;
            return { chatId: currentChatId, hasChanges: false };
          });
        }
      });
    return function () {
      disposed = true;
      window.removeEventListener("workbench:workspace-changes", revealFromEvent);
    };
  }, [activeChatId]);

  var browserState = wbcBrowserStateForChat(activeChatId);
  var browserMarkedActive = !!(browserActiveByChat && browserActiveByChat[activeChatId]);
  var browserPanelState = browserState || {};
  var hasMap = wbcChatUsedMap(chat, runtime);
  var hasBrowser = !!((browserState && browserState.active) || browserMarkedActive);
  var hasArtifacts = wbcChatArtifactFiles(chat).length > 0;
  var viewerItems = wbcChatArtifactFiles(chat);
  var hasBranches = useWbcMemo(function () {
    return !!wbcBranchLineage(chats, activeChatId);
  }, [chats, activeChatId]);
  var pendingPlan = wbcActivePlan(chat);
  var hasSubagents = !!(
    subagentData
    && (
      (Array.isArray(subagentData.rounds) && subagentData.rounds.length)
      || (Array.isArray(subagentData.agents) && subagentData.agents.length)
    )
  );
  var tabs = [
    { id: "overview", label: wbcT("chat.side.overview", "Overview") },
  ];
  if (pendingPlan) tabs.push({ id: "plan", label: wbcT("chat.side.plan", "Plan") });
  if (hasSubagents) tabs.push({ id: "subagents", label: wbcT("workbenchChat.subagents", "Subagents") });
  tabs.push({ id: "context", label: wbcT("workbenchChat.context", "Context") });
  if (hasArtifacts) tabs.push({ id: "artifacts", label: wbcT("workbenchChat.artifacts", "Artifacts") });
  if (hasWorkspaceChanges) {
    tabs.push({ id: "changes", label: wbcT("workbenchChat.changes", "Changes") });
  }
  if (hasBranches) tabs.push({ id: "branches", label: wbcT("chat.side.branches", "Branches") });
  if (viewerFile) tabs.push({ id: "viewer", label: wbcT("workbenchChat.viewer", "Viewer") });
  if (hasMap) tabs.push({ id: "map", label: wbcT("chat.side.map", "Map") });
  if (hasBrowser) tabs.push({ id: "browser", label: wbcT("chat.side.browser", "Browser") });
  if (sideAgents && sideAgents.length) {
    tabs.push({
      id: "side-agents",
      label: wbcT("workbenchChat.sideAgent.tab", "Side questions"),
    });
  }
  var activeTab = tabs.some(function (item) { return item.id === tab; }) ? tab : "";
  useWbcLiveChatMetrics(chat, !!runtime);
  var flush = false;
  var sideTabMeta = {
    plan: pendingPlan && Array.isArray(pendingPlan.steps) && pendingPlan.steps.length
      ? pendingPlan.steps.filter(function (step) { return step.status === "completed"; }).length + "/" + pendingPlan.steps.length
      : "",
    subagents: subagentData && Array.isArray(subagentData.agents) && subagentData.agents.length
      ? String(subagentData.agents.length)
      : "",
    artifacts: hasArtifacts ? String(wbcChatArtifactFiles(chat).length) : "",
    viewer: viewerItems.length ? String(viewerItems.length) : "",
    browser: browserPanelState && Array.isArray(browserPanelState.tabs) ? String(browserPanelState.tabs.length) : "",
    "side-agents": sideAgents && sideAgents.length ? String(sideAgents.length) : "",
  };
  var activeContent = (
    <>
      {activeTab === "overview" && <WbcOverviewTab chat={chat} loading={chatLoading} detailed={chatDetailed} runtime={runtime} onRename={onRename} onDelete={onDelete} onToTask={onToTask} toTaskBusy={toTaskBusy} onCompact={onCompact} compactBusy={compactBusy} />}
      {activeTab === "plan" && <WbcPlanTab plan={pendingPlan} />}
      {activeTab === "context" && <WbcContextTab project={project} chat={chat} runtime={runtime} />}
      {activeTab === "artifacts" && <WbcArtifactsTab chat={chat} onSelectArtifact={onSelectArtifact} />}
      {activeTab === "changes" && <WbcChangesTab chatId={activeChatId} onSelectChange={onSelectChange} />}
      {activeTab === "branches" && <WbcBranchTab chats={chats} activeChatId={activeChatId} onSelectChat={onSelectChat} />}
      {activeTab === "viewer" && <WbcViewerList files={viewerItems} selectedFile={viewerFile} onSelect={onSelectViewer} />}
      {activeTab === "map" && <WbcMapList chatId={chat ? chat.id : ""} onSelect={onSelectMap} />}
      {activeTab === "browser" && !browserSuppressed && (
        <WbcBrowserList browserState={browserPanelState} onSelect={onSelectBrowser} />
      )}
      {activeTab === "side-agents" && (
        <WbcSideAgentsPanel
          agents={sideAgents}
          project={project}
          onOpenFile={onOpenFile}
          activeAgentId={activeSideAgentId}
          loading={sideAgentsLoading}
          onSelect={onSelectSideAgent}
          onDelete={onDeleteSideAgent}
          onUpdate={onUpdateSideAgent}
        />
      )}
    </>
  );
  return (
    <aside className={"wbc-side" + (floating ? " wbc-side-floating" : "")}>
      <div className="wbc-side-card">
        {!floating && React.createElement(window.CyreneUI.require("shell").ColResizer, { cardEdge: true })}
        <div className="wbc-side-card-head">
          <strong>{wbcT("workbenchChat.sidePanelTitle", "Conversation panel")}</strong>
          <button
            type="button"
            className={"wbc-side-hide-btn" + (floating ? " wbc-side-floating-close" : "")}
            onClick={floating ? onCloseFloating : onToggleSide}
            title={floating
              ? wbcT("workbenchChat.closeFloatingConversationPanel", "Close floating conversation panel")
              : wbcT("workbenchChat.hideSidebar", "Hide side panel")}
            aria-label={floating
              ? wbcT("workbenchChat.closeFloatingConversationPanel", "Close floating conversation panel")
              : wbcT("workbenchChat.hideSidebar", "Hide side panel")}
          >
            {floating ? WBC_ICONS.x : WBC_ICONS.chevronsRight}
          </button>
        </div>
        <div className="wbc-side-accordion">
          {tabs.map(function (item) {
            var opensSplit = item.id === "subagents" || item.id === "browser";
            var expanded = !opensSplit && activeTab === item.id;
            var meta = sideTabMeta[item.id] || "";
            return (
              <section key={item.id} className={"wbc-side-accordion-item" + (expanded ? " expanded" : "")}>
                <button
                  type="button"
                  className="wbc-side-accordion-trigger"
                  aria-expanded={expanded}
                  onClick={function () {
                    if (opensSplit) {
                      onTabChange("");
                      if (item.id === "subagents" && onOpenSubagents) onOpenSubagents();
                      if (item.id === "browser" && onSelectBrowser) {
                        var currentBrowserTab = browserPanelState && (browserPanelState.activeTabId || browserPanelState.activeTab && browserPanelState.activeTab.id);
                        onSelectBrowser(currentBrowserTab || "__active__");
                      }
                      return;
                    }
                    onTabChange(expanded ? "" : item.id);
                  }}
                >
                  <span className="wbc-side-accordion-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS[item.id] || WBC_SIDE_TAB_ICONS.overview}</span>
                  <span className="wbc-side-accordion-label">{item.label}</span>
                  {meta && <span className="wbc-side-accordion-meta">{meta}</span>}
                  <span className="wbc-side-accordion-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
                </button>
                <WbcSideAccordionBody expanded={expanded} flush={flush}>{activeContent}</WbcSideAccordionBody>
              </section>
            );
          })}
        </div>
      </div>
    </aside>
  );
}

function wbcChangeTypeLabel(changeType) {
  if (changeType === "created") return wbcT("workbenchChat.changes.created", "Created");
  if (changeType === "deleted") return wbcT("workbenchChat.changes.deleted", "Deleted");
  return wbcT("workbenchChat.changes.modified", "Modified");
}

function WbcChangesTab({ chatId, onSelectChange }) {
  var [payload, setPayload] = useWbcState({ changeSets: [], fileCount: 0, additions: 0, deletions: 0 });
  var [loading, setLoading] = useWbcState(true);
  var [error, setError] = useWbcState("");
  var [selectedSetId, setSelectedSetId] = useWbcState("");
  var refreshTimerRef = useWbcRef(null);
  var chatIdRef = useWbcRef(chatId);
  var refreshSeqRef = useWbcRef(0);
  chatIdRef.current = chatId;

  function refresh(background) {
    if (!chatId) return Promise.resolve(null);
    var requestedChatId = String(chatId);
    var requestSeq = ++refreshSeqRef.current;
    if (!background) setLoading(true);
    setError("");
    return WorkbenchChatModel.getChanges(chatId, { toast: false })
      .then(function (next) {
        if (String(chatIdRef.current || "") !== requestedChatId || refreshSeqRef.current !== requestSeq) return null;
        var sets = Array.isArray(next.changeSets) ? next.changeSets : [];
        setPayload(next);
        setSelectedSetId(function (current) {
          return sets.some(function (item) { return item.id === current; })
            ? current
            : (sets[0] ? sets[0].id : "");
        });
        return next;
      })
      .catch(function (err) {
        if (String(chatIdRef.current || "") === requestedChatId && refreshSeqRef.current === requestSeq) setError(wbcErrorText(err));
        return null;
      })
      .finally(function () {
        if (String(chatIdRef.current || "") === requestedChatId && refreshSeqRef.current === requestSeq) setLoading(false);
      });
  }

  useWbcEffect(function () {
    setPayload({ changeSets: [], fileCount: 0, additions: 0, deletions: 0 });
    setSelectedSetId("");
    refreshSeqRef.current += 1;
    refresh(false);
  }, [chatId]);

  useWbcEffect(function () {
    function onChanges(event) {
      var detail = (event && event.detail) || {};
      var eventChatId = String(detail.chatId || detail.session_id || "");
      if (eventChatId && eventChatId !== String(chatId || "")) return;
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = setTimeout(function () { refresh(true); }, 80);
    }
    window.addEventListener("workbench:workspace-changes", onChanges);
    return function () {
      window.removeEventListener("workbench:workspace-changes", onChanges);
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, [chatId]);

  var changeSets = Array.isArray(payload.changeSets) ? payload.changeSets : [];
  var selectedSet = changeSets.find(function (item) { return item.id === selectedSetId; }) || changeSets[0] || null;
  var files = selectedSet && Array.isArray(selectedSet.files) ? selectedSet.files : [];
  return (
    <div className="wbc-changes-tab">
      {changeSets.length > 1 && (
        <div className="wbc-changes-run-picker">
          <select value={selectedSet ? selectedSet.id : ""} onChange={function (event) { setSelectedSetId(event.target.value); }}>
            {changeSets.map(function (item, index) {
              return <option value={item.id} key={item.id}>{index === 0 ? wbcT("workbenchChat.changes.latestRun", "Latest run") : wbcFormatTime(item.completedAt)}</option>;
            })}
          </select>
        </div>
      )}
      {loading && !changeSets.length ? (
        <p className="workbench-muted wbc-changes-state">{wbcT("workbenchChat.changes.loading", "Loading changes...")}</p>
      ) : error ? (
        <div className="wbc-changes-state"><p className="workbench-muted">{error}</p><button type="button" className="wb-btn ghost" onClick={function () { refresh(false); }}>{wbcT("workbenchChat.error.retry", "Retry")}</button></div>
      ) : !changeSets.length ? (
        <div className="wbc-changes-empty">
          <b>{wbcT("workbenchChat.changes.emptyTitle", "No agent changes yet")}</b>
          <p>{wbcT("workbenchChat.changes.emptyBody", "Files created, edited, or deleted by future agent runs will appear here automatically.")}</p>
        </div>
      ) : (
        <React.Fragment>
          <div className="wbc-resource-list wbc-changes-files">
            {files.map(function (item) {
              return (
                <button
                  type="button"
                  key={item.id || item.path}
                  className={"wbc-resource-list-row wbc-change-file " + item.changeType}
                  onClick={function () {
                    if (onSelectChange) onSelectChange({ chatId: chatId, setId: selectedSet.id, path: item.path, file: item, files: files });
                  }}
                >
                  <span className="wbc-resource-list-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.changes}</span>
                  <span className="wbc-resource-list-copy">
                    <b className="wbc-change-file-path" title={item.path}>{item.path}</b>
                    <small><span className="wbc-change-file-status">{wbcChangeTypeLabel(item.changeType)}</span><span className="wbc-change-file-lines"><i>+{item.additions || 0}</i><em>−{item.deletions || 0}</em></span></small>
                  </span>
                  <span className="wbc-resource-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
                </button>
              );
            })}
          </div>
        </React.Fragment>
      )}
    </div>
  );
}

// Workbench subagent panel, styled as a read-only group chat room. The main
// agent's delegated subagents appear as roster members; their inter-agent
// messages and results stream as chat bubbles. Observational only — the
// Workbench has no user→subagent send endpoint, so there is no composer.
function WbcSubagentsTab({ data, loading, onSelectRound }) {
  var rounds = data && Array.isArray(data.rounds) ? data.rounds : [];
  var agents = data && Array.isArray(data.agents) ? data.agents : [];
  var messages = data && Array.isArray(data.messages) ? data.messages : [];
  var activeRoundId = String((data && data.activeRoundId) || "");
  var [selectedAgentId, setSelectedAgentId] = useWbcState("");

  var roster = agents.map(function (agent) { return agent.id; }).join("|");
  // Default to no focused agent; reset when the round / roster changes.
  useWbcEffect(function () { setSelectedAgentId(""); }, [activeRoundId, roster]);

  var selectedAgent = agents.find(function (agent) { return agent.id === selectedAgentId; }) || null;
  var activeCount = agents.filter(function (agent) {
    return ["running", "resumed", "waiting"].indexOf(String(agent.status || "")) >= 0;
  }).length;
  var activeRound = rounds.find(function (round) { return round.id === activeRoundId; }) || rounds[0] || null;

  function focusAgent(id) {
    setSelectedAgentId(id === selectedAgentId ? "" : id);
  }

  if (loading && !rounds.length && !agents.length) {
    return (
      <div className="wbc-subagent-empty">
        <span className="wbc-spinner" aria-hidden="true"></span>
        <p>{wbcT("workbenchChat.subagent.loading", "Loading subagents...")}</p>
      </div>
    );
  }
  if (!rounds.length && !agents.length) {
    return (
      <div className="wbc-subagent-empty">
        <span className="wbc-subagent-empty-glyph" aria-hidden="true">⠿</span>
        <b>{wbcT("workbenchChat.subagent.emptyTitle", "No subagents in this chat")}</b>
        <p>{wbcT("workbenchChat.subagent.emptyBody", "When the main agent delegates work, subagents and their results will appear here.")}</p>
      </div>
    );
  }

  return (
    <div className="wbc-subagent-page">
      <header className="wbc-subagent-bar">
        <div className="wbc-subagent-bar-main">
          <span className="wbc-subagent-eyebrow">{wbcT("workbenchChat.subagent.title", "Subagent activity")}</span>
          <b title={activeRound ? activeRound.title : ""}>
            {(activeRound && activeRound.title) || wbcT("workbenchChat.subagents", "Subagents")}
          </b>
        </div>
        <span className={"wbc-subagent-livepill " + (activeCount ? "live" : "idle")}>
          <i aria-hidden="true"></i>
          {activeCount
            ? wbcT("workbenchChat.subagent.liveCount", "{n} working", { n: activeCount })
            : wbcT("workbenchChat.subagent.complete", "Complete")}
        </span>
      </header>

      {rounds.length > 1 ? (
        <label className="wbc-subagent-round">
          <span>{wbcT("workbenchChat.subagent.round", "Round")}</span>
          <select value={activeRoundId} onChange={function (event) { onSelectRound && onSelectRound(event.target.value); }}>
            {rounds.map(function (round) {
              return <option key={round.id} value={round.id}>{round.title}</option>;
            })}
          </select>
        </label>
      ) : null}

      <WbcSubagentRoster agents={agents} selectedId={selectedAgentId} onSelect={focusAgent} />

      {selectedAgent ? (
        <WbcSubagentSpotlight agent={selectedAgent} onClose={function () { setSelectedAgentId(""); }} />
      ) : null}

      <WbcSubagentStream
        messages={messages}
        agents={agents}
        active={activeCount > 0}
        selectedId={selectedAgentId}
        onSelectAgent={focusAgent}
      />
    </div>
  );
}

// Horizontal avatar strip of the round's subagents — the chat room's member list.
function WbcSubagentRoster({ agents, selectedId, onSelect }) {
  return (
    <div className="wbc-subagent-roster">
      {agents.map(function (agent) {
        var color = wbcAgentColor(agent.id);
        var statusCls = wbcSubagentStatusClass(agent.status);
        var name = agent.name || agent.id;
        return (
          <button
            key={agent.id}
            type="button"
            className={"wbc-subagent-chip" + (agent.id === selectedId ? " active" : "")}
            style={{ "--wb-agent-color": color }}
            onClick={function () { onSelect(agent.id); }}
            title={name + " · " + wbcSubagentStatusText(agent.status)}
          >
            <span className="wbc-subagent-avatar" style={{ background: color }}>
              {wbcAgentInitials(name)}
              <i className={"wbc-subagent-avatar-dot " + statusCls} aria-hidden="true"></i>
            </span>
            <span className="wbc-subagent-chip-name">{name}</span>
          </button>
        );
      })}
    </div>
  );
}

// Focused-agent card: its task brief and (when available) full result.
function WbcSubagentSpotlight({ agent, onClose }) {
  var color = wbcAgentColor(agent.id);
  var name = agent.name || agent.id;
  return (
    <section className="wbc-subagent-spotlight" style={{ "--wb-agent-color": color }}>
      <header>
        <span className="wbc-subagent-avatar lg" style={{ background: color }}>{wbcAgentInitials(name)}</span>
        <div className="wbc-subagent-spotlight-id">
          <b title={name}>{name}</b>
          <span className={"wbc-subagent-status " + wbcSubagentStatusClass(agent.status)}>
            {wbcSubagentStatusText(agent.status)}
          </span>
        </div>
        <button type="button" className="wbc-subagent-spotlight-close" onClick={onClose} aria-label={wbcT("workbenchChat.subagent.close", "Close")}>×</button>
      </header>
      <div className="wbc-subagent-spotlight-body">
        <label>{wbcT("workbenchChat.subagent.task", "Task")}</label>
        <p>{agent.task || "—"}</p>
        <label>{wbcT("workbenchChat.subagent.result", "Result")}</label>
        {agent.result ? (
          <div className="markdown wbc-subagent-result" dangerouslySetInnerHTML={{ __html: wbcRenderMarkdown(agent.result) }} />
        ) : (
          <p className="workbench-muted">{wbcT("workbenchChat.subagent.resultPending", "No result yet.")}</p>
        )}
      </div>
    </section>
  );
}

// Scrolling chat transcript of inter-agent messages and results.
function WbcSubagentStream({ messages, agents, active, selectedId, onSelectAgent }) {
  var scrollRef = useWbcRef(null);
  var atBottomRef = useWbcRef(true);
  var initedRef = useWbcRef(false);

  function handleScroll() {
    var el = scrollRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 48;
  }

  // First render jumps to the latest message; later updates only follow when the
  // reader is already near the bottom.
  useWbcLayoutEffect(function () {
    var el = scrollRef.current;
    if (!el) return;
    if (!initedRef.current) {
      initedRef.current = true;
      el.scrollTop = el.scrollHeight;
      atBottomRef.current = true;
      return;
    }
    if (atBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  var nameById = {};
  var agentIds = [];
  agents.forEach(function (agent) { nameById[agent.id] = agent.name || agent.id; agentIds.push(agent.id); });

  if (!messages.length) {
    return (
      <div className="wbc-subagent-stream is-empty">
        <div className="wbc-subagent-stream-empty">
          {active ? (
            <span className="wbc-subagent-typing">
              <i></i><i></i><i></i>
              {wbcT("workbenchChat.subagent.working", "Subagents are working…")}
            </span>
          ) : (
            wbcT("workbenchChat.subagent.noActivity", "No messages recorded for this round.")
          )}
        </div>
      </div>
    );
  }

  var rows = [];
  var prevFrom = null;
  var prevTs = null;
  for (var i = 0; i < messages.length; i++) {
    var msg = messages[i];
    if (prevTs && msg.timestamp) {
      try {
        if (new Date(msg.timestamp) - new Date(prevTs) > 300000) {
          rows.push(<div className="wbc-subagent-timesep" key={"ts_" + i}><span>{wbcFormatTime(msg.timestamp)}</span></div>);
          prevFrom = null;
        }
      } catch (e) { /* unparseable timestamp — skip separator */ }
    }
    rows.push(
      <WbcSubagentBubble
        key={msg.id || i}
        msg={msg}
        name={nameById[msg.from] || msg.from}
        agentIds={agentIds}
        grouped={prevFrom === msg.from && msg.from}
        dimmed={!!selectedId && msg.from !== selectedId}
        onSelectAgent={onSelectAgent}
      />
    );
    prevFrom = msg.from;
    prevTs = msg.timestamp;
  }

  return (
    <div className="wbc-subagent-stream" ref={scrollRef} onScroll={handleScroll}>
      {rows}
    </div>
  );
}

// A single transcript entry: agent message, broadcast, result card, or system note.
function WbcSubagentBubble({ msg, name, agentIds, grouped, dimmed, onSelectAgent }) {
  var kind = String(msg.type || "message");
  var from = String(msg.from || "");
  var color = wbcAgentColor(from);
  var html = wbcHighlightMentions(wbcRenderMarkdown(msg.content || ""), agentIds);

  if (!from) {
    return <div className="wbc-subagent-syssep"><span dangerouslySetInnerHTML={{ __html: html }} /></div>;
  }

  if (kind === "result") {
    return (
      <article className={"wbc-subagent-msg result" + (dimmed ? " dimmed" : "")} style={{ "--wb-agent-color": color }}>
        <div className="wbc-subagent-msg-head">
          <span className="wbc-subagent-avatar sm" style={{ background: color }}>{wbcAgentInitials(name)}</span>
          <b>{name}</b>
          <span className="wbc-subagent-tag result">{wbcT("workbenchChat.subagent.result", "Result")}</span>
          <time>{wbcFormatTime(msg.timestamp)}</time>
        </div>
        <div className="wbc-subagent-bubble result markdown" dangerouslySetInnerHTML={{ __html: html }} />
      </article>
    );
  }

  var isBroadcast = kind === "broadcast" || String(msg.to || "") === "all";
  var toUser = String(msg.to || "") === "user";

  return (
    <article className={"wbc-subagent-msg" + (grouped ? " grouped" : "") + (dimmed ? " dimmed" : "")} style={{ "--wb-agent-color": color }}>
      {grouped ? (
        <span className="wbc-subagent-avatar-spacer" aria-hidden="true"></span>
      ) : (
        <button type="button" className="wbc-subagent-avatar sm" style={{ background: color }}
          onClick={function () { onSelectAgent && onSelectAgent(from); }} title={name}>
          {wbcAgentInitials(name)}
        </button>
      )}
      <div className="wbc-subagent-msg-body">
        {grouped ? null : (
          <div className="wbc-subagent-msg-head">
            <b style={{ color: color }}>{name}</b>
            {isBroadcast ? <span className="wbc-subagent-tag broadcast">{wbcT("workbenchChat.subagent.broadcast", "Broadcast")}</span> : null}
            {toUser ? <span className="wbc-subagent-tag touser">{wbcT("workbenchChat.subagent.toUser", "To you")}</span> : null}
            <time>{wbcFormatTime(msg.timestamp)}</time>
          </div>
        )}
        <div className="wbc-subagent-bubble markdown" dangerouslySetInnerHTML={{ __html: html }} />
      </div>
    </article>
  );
}

// Prefer the durable chat plan. Fall back to the pending confirmation payload
// during the small window before the chat record is re-fetched.
function wbcActivePlan(chat) {
  var active = chat && chat.activePlan;
  if (active && typeof active === "object") return active;
  var pq = chat && chat.pendingQuestion;
  var plan = pq && pq.plan;
  if (!plan || typeof plan !== "object") return null;
  var hasSteps = Array.isArray(plan.steps) && plan.steps.length > 0;
  return (plan.title || plan.summary || hasSteps) ? plan : null;
}

function wbcPlanStatusText(status) {
  return {
    proposed: wbcT("chat.side.planProposed", "Awaiting approval"),
    active: wbcT("chat.side.planActive", "In progress"),
    paused: wbcT("chat.side.planPaused", "Paused"),
  }[status] || "";
}

function wbcPlanStepStatusText(status) {
  return {
    pending: wbcT("chat.side.planStepPending", "Pending"),
    in_progress: wbcT("chat.side.planStepActive", "Working"),
    completed: wbcT("chat.side.planStepCompleted", "Completed"),
    failed: wbcT("chat.side.planStepFailed", "Failed"),
    skipped: wbcT("chat.side.planStepSkipped", "Skipped"),
  }[status] || "";
}

// Right-panel 计划 tab — durable from proposal through execution completion.
function WbcPlanTab({ plan }) {
  var p = plan || {};
  var steps = Array.isArray(p.steps) ? p.steps : [];
  return (
    <div className="workbench-side-stack">
      <section className="workbench-side-section wbc-plan">
        <div className="wbc-plan-head">
          <h3>{p.title || wbcT("chat.side.planTitle", "Proposed plan")}</h3>
          {p.status ? <span className={"wbc-plan-state " + p.status}>{wbcPlanStatusText(p.status)}</span> : null}
        </div>
        {p.summary ? <p className="workbench-muted">{p.summary}</p> : null}
        {p.markdownPath ? <p className="wbc-plan-path" title={p.markdownPath}>{p.markdownPath}</p> : null}
        {steps.length === 0 ? (
          <p className="workbench-muted">{wbcT("chat.side.planEmpty", "The agent has not detailed any steps yet.")}</p>
        ) : (
          <ol className="wbc-plan-steps">
            {steps.map(function (step, i) {
              var tasks = Array.isArray(step.tasks) ? step.tasks : [];
              var status = step.status || "pending";
              return (
                <li key={step.id || i} className={"wbc-plan-step " + status}>
                  <div className="wbc-plan-step-title">
                    <b>{step.title || (wbcT("chat.side.planStep", "Step") + " " + (i + 1))}</b>
                    <span>{wbcPlanStepStatusText(status)}</span>
                  </div>
                  {tasks.length > 0 && (
                    <ul className="wbc-plan-tasks">
                      {tasks.map(function (t, j) { return <li key={j}>{String(t)}</li>; })}
                    </ul>
                  )}
                  {step.note ? <p className="wbc-plan-note">{step.note}</p> : null}
                </li>
              );
            })}
          </ol>
        )}
      </section>
    </div>
  );
}

// ---- PDF.js viewer (replaces <embed> for PDF files) -------------------------

function WbcPdfJsViewer({ file, url, onViewed }) {
  var pdf = window.CyreneUI.require("pdf");
  var containerRef = useWbcRef(null);
  var viewerRef = useWbcRef(null);
  var [pageNum, setPageNum] = useWbcState(1);
  var [pageCount, setPageCount] = useWbcState(0);
  var [scale, setScale] = useWbcState(1);
  var [loading, setLoading] = useWbcState(true);
  var [failed, setFailed] = useWbcState(false);
  var [failReason, setFailReason] = useWbcState("");
  var [analyzing, setAnalyzing] = useWbcState(false);
  var [analysisResult, setAnalysisResult] = useWbcState("");
  var analyzeButtonRef = useWbcRef(null);

  useWbcEffect(function () {
    var container = containerRef.current;
    if (!container) { setFailReason('container not mounted'); setFailed(true); setLoading(false); return; }
    if (!url) { setFailReason('no URL'); setFailed(true); setLoading(false); return; }
    if (!pdf.lib || !pdf.viewer || !pdf.setupViewer) { setFailReason('PDF.js not loaded'); setFailed(true); setLoading(false); return; }

    var cancelled = false;
    var abortLoader = new AbortController();
    var loadTimedOut = false;
    var timer = setTimeout(function () {
      loadTimedOut = true;
      abortLoader.abort(new DOMException('PDF loading timed out', 'TimeoutError'));
      setFailReason('timeout (60s)');
      setFailed(true);
      setLoading(false);
    }, 60000);

    var result = pdf.setupViewer(container);
    var viewer = result.viewer;
    var eventBus = result.eventBus;
    var loadedDocument = null;
    viewerRef.current = viewer;

    // Track page changes
    function onPageChanging(evt) {
      if (!cancelled) setPageNum(evt.pageNumber);
    }
    eventBus.on('pagechanging', onPageChanging);

    // Handle resize (e.g. sidebar panel resize)
    var resizeObserver = new ResizeObserver(function () { viewer.update(); });
    resizeObserver.observe(container);

    // Copy the original PDF text rather than browser-measured text-layer content.
    var selectionSanitizer = pdf.installSelectionSanitizer(container, viewer, eventBus);
    var copyFix = pdf.installCopyFix(container, viewer);

    // Fetch and load PDF document
    pdf.loadPdf(url, viewer, abortLoader.signal).then(function (doc) {
      loadedDocument = doc;
      if (cancelled) {
        try { doc.destroy(); } catch (e) {}
        return;
      }
      clearTimeout(timer);
      setPageCount(doc.numPages);
      setPageNum(1);
      setLoading(false);
      setScale(viewer.currentScale);
      if (onViewed) onViewed();
    }).catch(function (err) {
      if (!cancelled) {
        clearTimeout(timer);
        setFailReason(loadTimedOut ? 'timeout (60s)' : String(err && err.message || err));
        setFailed(true);
        setLoading(false);
      }
    });

    return function () {
      cancelled = true;
      clearTimeout(timer);
      abortLoader.abort();
      selectionSanitizer.abort();
      copyFix.abort();
      resizeObserver.disconnect();
      eventBus.off('pagechanging', onPageChanging);
      if (viewerRef.current) {
        try { viewerRef.current.setDocument(null); } catch (e) {}
      }
      if (loadedDocument) {
        try { loadedDocument.destroy(); } catch (e) {}
      }
      viewerRef.current = null;
    };
  }, [url]);

  function zoomIn() {
    var v = viewerRef.current;
    if (v) { v.currentScale = Math.min(5, v.currentScale * 1.15); setScale(v.currentScale); }
  }
  function zoomOut() {
    var v = viewerRef.current;
    if (v) { v.currentScale = Math.max(0.25, v.currentScale / 1.15); setScale(v.currentScale); }
  }
  function zoomReset() {
    var v = viewerRef.current;
    if (v) { v.currentScaleValue = 'page-width'; setScale(v.currentScale); }
  }

  // Text selection → agent analysis
  function analyzePdfText() {
    var text = pdf.getSelectedText(containerRef.current).trim();
    if (!text || analyzing) return;
    var language = window.CyreneUI.require("i18n").getLang();

    setAnalyzing(true);
    setAnalysisResult('');

    if (!pdf.buildAnalysisInventory || !pdf.extractAnalysisContext) {
      setAnalysisResult(wbcT(
        "workbenchChat.pdfAnalysisFailed",
        "Analysis failed: {error}",
        { error: wbcT("workbenchChat.pdfAnalysisUnavailable", "PDF context tools unavailable") }
      ));
      setAnalyzing(false);
      return;
    }

    pdf.buildAnalysisInventory(containerRef.current, viewerRef.current, pageNum)
      .then(function (inventory) {
        return fetch('/api/pdf/context-plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: text,
            pdf_name: file ? file.name || 'PDF' : 'PDF',
            lang: language,
            inventory: inventory,
          }),
        }).then(function (response) { return response.json(); })
          .then(function (plan) {
            if (plan.error) throw new Error(plan.error);
            return pdf.extractAnalysisContext(
              viewerRef.current,
              plan.page_numbers,
              inventory,
              plan.reason
            );
          });
      })
      .then(function (context) {
        return fetch('/api/pdf/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: text,
            pdf_name: file ? file.name || 'PDF' : 'PDF',
            lang: language,
            context: context,
          }),
        });
      })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        setAnalysisResult(data.result || wbcT("workbenchChat.pdfAnalysisEmpty", "No result"));
        var sel = window.getSelection();
        if (sel) sel.removeAllRanges();
      }).catch(function (err) {
        setAnalysisResult(wbcT(
          "workbenchChat.pdfAnalysisFailed",
          "Analysis failed: {error}",
          { error: err.message }
        ));
      }).finally(function () {
        setAnalyzing(false);
        if (analyzeButtonRef.current) analyzeButtonRef.current.style.display = 'none';
      });
  }

  var head = (
    <div className="wbc-viewer-head">
      <span className="wbc-viewer-name" title={file && file.name}>{(file && file.name) || "PDF"}</span>
      {!loading && !failed && (
        <span className="wbc-viewer-switch">
          <button type="button" onClick={zoomOut}>−</button>
          <button type="button" onClick={zoomReset}>{Math.round(scale * 100) + "%"}</button>
          <button type="button" onClick={zoomIn}>+</button>
        </span>
      )}
      {!loading && !failed && (
        <span style={{ fontSize: 11, color: 'var(--text-3)', marginLeft: 4, whiteSpace: 'nowrap' }}>
          {pageNum} / {pageCount}
        </span>
      )}
      {url ? <a className="wbc-viewer-open" href={"/pdf/viewer?url=" + encodeURIComponent(url) + "&name=" + encodeURIComponent((file && file.name) || "PDF") + "&lang=" + encodeURIComponent(window.CyreneUI.require("i18n").getLang())} target="_blank" rel="noreferrer" title={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}>↗</a> : null}
      {file ? wbcDownloadLink(file, { className: "wbc-viewer-download" }) : null}
    </div>
  );

  var body = (
    <div className="wbc-viewer-scroll" style={{ overflow: 'hidden', position: 'relative' }} onMouseUp={function () {
      if (loading || failed) return;
      setTimeout(function () {
        if (pdf.getSelectedText(containerRef.current).trim()) {
          if (analyzeButtonRef.current) analyzeButtonRef.current.style.display = 'inline-flex';
        }
      }, 200);
    }}>
      {/* Container div for PDF.js — always rendered so ref is available */}
      <div ref={containerRef} style={{ position: 'relative', overflow: 'auto', height: '100%' }} />

      {loading && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg, #fff)', zIndex: 10 }}>
          <p className="workbench-muted wbc-viewer-pad">{wbcT("settings.pathLoading", "Loading...")}</p>
        </div>
      )}
      {failed && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg, #fff)', zIndex: 10 }}>
          <p className="workbench-muted wbc-viewer-pad">
            {wbcT("workbenchChat.viewerLoadFailed", "File failed to load.")}
            {url ? " " + wbcT("workbenchChat.viewerOpenFallback", "Try opening it in a new window.") : ""}
            {failReason ? <><br /><small style={{ opacity: 0.6 }}>{failReason}</small></> : null}
          </p>
        </div>
      )}

      <button ref={analyzeButtonRef} id="wbc-pdf-analyze-btn" className="wbc-pdf-analyze" style={{ display: 'none' }}
        onClick={analyzePdfText}
        disabled={analyzing}
      >
        {analyzing ? <span className="wbc-pdf-analysis-spinner" aria-hidden="true" /> : null}
        <span>{analyzing
          ? wbcT("workbenchChat.pdfAnalyzing", "Analyzing…")
          : wbcT("workbenchChat.pdfAnalyze", "Analyze selection")}</span>
      </button>

      {(analyzing || analysisResult) ? (
        <section className="wbc-pdf-analysis" role="region" aria-live="polite" aria-label={wbcT("workbenchChat.pdfAnalysisTitle", "PDF analysis")}>
          {!analyzing ? <button type="button" className="wbc-pdf-analysis-close" aria-label={wbcT("workbenchChat.pdfAnalysisClose", "Close PDF analysis")} onClick={function () { setAnalysisResult(''); }}>×</button> : null}
          {analyzing ? (
            <div className="wbc-pdf-analysis-loading">
              <span className="wbc-pdf-analysis-spinner" aria-hidden="true" />
              <span>{wbcT("workbenchChat.pdfAnalysisLoading", "Agent is choosing and reading the relevant PDF context…")}</span>
            </div>
          ) : (
            <div className="wbc-pdf-analysis-body markdown wbc-msg-body" dangerouslySetInnerHTML={{ __html: wbcRenderMarkdown(analysisResult) }} />
          )}
        </section>
      ) : null}
    </div>
  );

  return (
    <div className="wbc-viewer">
      {head}
      {body}
    </div>
  );
}

// ---- side viewer (PDF / HTML / Markdown / 代码 / 图片) ----------------------

function WbcViewerTab({ file, onViewed, hideHeader, htmlMode: controlledHtmlMode, onHtmlModeChange }) {
  var kind = wbcFileViewKind(file);
  var [text, setText] = useWbcState("");
  var [localHtmlMode, setLocalHtmlMode] = useWbcState("rendered");
  var htmlMode = controlledHtmlMode || localHtmlMode;
  function setHtmlMode(next) {
    setLocalHtmlMode(next);
    if (onHtmlModeChange) onHtmlModeChange(next);
  }
  var [failed, setFailed] = useWbcState(false);
  var codeRef = useWbcRef(null);
  var viewedRef = useWbcRef("");
  var url = file && file.url;
  function confirmViewed() {
    var key = String(url || "") + "::" + String(file && file.name || "");
    if (!key || viewedRef.current === key) return;
    viewedRef.current = key;
    if (onViewed) onViewed(file);
  }
  var htmlPreview = useWbcMemo(function () {
    return kind === "html" ? wbcHtmlPreviewDocument(text, url) : "";
  }, [text, url, kind]);

  // text-ish contents are fetched (PDF is handled by WbcPdfJsViewer)
  useWbcEffect(function () {
    setText("");
    setFailed(false);
    setHtmlMode("rendered");
    if (!url) return;
    var cancelled = false;
    if (kind === "html" || kind === "markdown" || kind === "code") {
      fetch(url).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      }).then(function (body) {
        if (!cancelled) {
          setText(body);
          confirmViewed();
        }
      }).catch(function () { if (!cancelled) setFailed(true); });
    }
    return function () { cancelled = true; };
  }, [url, kind]);

  // syntax highlight code once loaded
  useWbcEffect(function () {
    if (kind === "code" && text && codeRef.current && window.hljs) {
      try { window.hljs.highlightElement(codeRef.current); } catch (e) {}
    }
  }, [text, kind]);

  if (!file) return <p className="workbench-muted">{wbcT("workbenchChat.viewerEmpty", "Select a file from message attachments or artifacts.")}</p>;

  // PDF is handled entirely by its own component — skip the wrapper.
  if (kind === "pdf") {
    return <WbcPdfJsViewer file={file} url={url} onViewed={confirmViewed} />;
  }

  var head = (
    <div className="wbc-viewer-head">
      <span className="wbc-viewer-name" title={file.name}>{file.name || "file"}</span>
      {kind === "html" && (
        <span className="wbc-viewer-switch">
          <button type="button" className={htmlMode === "rendered" ? "active" : ""} onClick={function () { setHtmlMode("rendered"); }}>{wbcT("workbenchChat.viewerRendered", "Rendered")}</button>
          <button type="button" className={htmlMode === "source" ? "active" : ""} onClick={function () { setHtmlMode("source"); }}>{wbcT("workbenchChat.viewerSource", "Source")}</button>
        </span>
      )}
      {wbcCanOpenExternally(file) ? <a className="wbc-viewer-open" href={url} target="_blank" rel="noreferrer" title={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}>↗</a> : null}
      {wbcDownloadLink(file, { className: "wbc-viewer-download" })}
    </div>
  );

  var body = null;
  if (failed) {
    body = <p className="workbench-muted wbc-viewer-pad">{wbcT("workbenchChat.viewerLoadFailed", "File failed to load.")}{url ? " " + wbcT("workbenchChat.viewerOpenFallback", "Try opening it in a new window.") : ""}</p>;
  } else if (kind === "image") {
    body = <div className="wbc-viewer-scroll center"><img className="wbc-viewer-img" src={url} alt={file.name || "image"} onLoad={confirmViewed} /></div>;
  } else if (kind === "html") {
    body = htmlMode === "rendered"
      ? <iframe key={url + "::" + (text ? "1" : "0")} className="wbc-viewer-iframe" sandbox="allow-scripts" srcDoc={htmlPreview} title={file.name || "HTML"} />
      : <pre className="wbc-viewer-pre">{text}</pre>;
  } else if (kind === "markdown") {
    body = <div className="wbc-viewer-md wbc-msg-body markdown" dangerouslySetInnerHTML={{ __html: wbcRenderMarkdown(text) }} />;
  } else if (kind === "code") {
    body = <pre className="wbc-viewer-pre"><code ref={codeRef}>{text}</code></pre>;
  } else {
    body = (
      <div className="wbc-viewer-pad">
        <p className="workbench-muted">{wbcT("workbenchChat.viewerUnsupported", "Preview is not supported for this file type.")}</p>
        {url ? <a className="wb-btn ghost" href={url} target="_blank" rel="noreferrer">{wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}</a> : null}
      </div>
    );
  }

  return (
    <div className="wbc-viewer">
      {!hideHeader && head}
      {body}
    </div>
  );
}

// ---- side map (pin_location / connect_pins 结果) ----------------------------

// WGS-84 → GCJ-02 (火星坐标) — AMap tiles use GCJ-02, so raw WGS pins must be
// shifted or they land ~500m off. Same math as the legacy map view.
function wbcWgs84ToGcj02(wgsLat, wgsLng) {
  if (wgsLng < 72.004 || wgsLng > 137.8347 || wgsLat < 0.8293 || wgsLat > 55.8271) return [wgsLat, wgsLng];
  var pi = 3.1415926535897932384626, a = 6378245.0, ee = 0.00669342162296594323;
  function tLat(x, y) {
    var r = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
    r += (20.0 * Math.sin(6.0 * x * pi) + 20.0 * Math.sin(2.0 * x * pi)) * 2.0 / 3.0;
    r += (20.0 * Math.sin(y * pi) + 40.0 * Math.sin(y / 3.0 * pi)) * 2.0 / 3.0;
    r += (160.0 * Math.sin(y / 12.0 * pi) + 320.0 * Math.sin(y * pi / 30.0)) * 2.0 / 3.0;
    return r;
  }
  function tLng(x, y) {
    var r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
    r += (20.0 * Math.sin(6.0 * x * pi) + 20.0 * Math.sin(2.0 * x * pi)) * 2.0 / 3.0;
    r += (20.0 * Math.sin(x * pi) + 40.0 * Math.sin(x / 3.0 * pi)) * 2.0 / 3.0;
    r += (150.0 * Math.sin(x / 12.0 * pi) + 300.0 * Math.sin(x / 30.0 * pi)) * 2.0 / 3.0;
    return r;
  }
  var dlat = tLat(wgsLng - 105.0, wgsLat - 35.0);
  var dlng = tLng(wgsLng - 105.0, wgsLat - 35.0);
  var radlat = wgsLat / 180.0 * pi;
  var magic = Math.sin(radlat);
  magic = 1 - ee * magic * magic;
  var sqrtmagic = Math.sqrt(magic);
  dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi);
  dlng = (dlng * 180.0) / (a / sqrtmagic * Math.cos(radlat) * pi);
  return [wgsLat + dlat, wgsLng + dlng];
}

// Same provider setting as the legacy map ("direct" = CARTO, "amap" = 高德).
function wbcMapProvider() {
  try { return localStorage.getItem("cyrene-tweak-map-provider") || "direct"; } catch (e) { return "direct"; }
}

function wbcTileConfig(provider) {
  var isDark = document.documentElement.dataset.theme === "dark";
  if (provider === "amap") {
    return {
      url: "https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=" + (isDark ? 8 : 7) + "&x={x}&y={y}&z={z}",
      options: { keepBuffer: 4, updateWhenZooming: false, updateWhenIdle: true },
    };
  }
  return {
    url: "https://{s}.basemaps.cartocdn.com/" + (isDark ? "dark_all" : "light_all") + "/{z}/{x}/{y}{r}.png",
    options: { subdomains: "abcd", keepBuffer: 4, updateWhenZooming: false, updateWhenIdle: true },
  };
}

function WbcMapTab({ chatId, focusItem }) {
  var holderRef = useWbcRef(null);
  var mapRef = useWbcRef(null);
  var layerRef = useWbcRef(null);
  var tileRef = useWbcRef(null);
  var switchedRef = useWbcRef(false);
  var [provider, setProvider] = useWbcState(wbcMapProvider());
  var mapData = useWbcMapData(chatId);
  var data = mapData.loading ? null : mapData;

  useWbcEffect(function () {
    if (!window.L || !holderRef.current || mapRef.current) return;
    var L = window.L;
    var map = L.map(holderRef.current, { zoomControl: true, attributionControl: false }).setView([35, 105], 4);
    mapRef.current = map;
    layerRef.current = L.layerGroup().addTo(map);
    var frame = 0;
    var invalidate = function () {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(function () {
        try { map.invalidateSize({ pan: false, animate: false }); } catch (e) {}
      });
    };
    var observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(invalidate) : null;
    if (observer) observer.observe(holderRef.current);
    window.addEventListener("resize", invalidate);
    var settleTimer = setTimeout(invalidate, 560);
    invalidate();
    return function () {
      clearTimeout(settleTimer);
      cancelAnimationFrame(frame);
      if (observer) observer.disconnect();
      window.removeEventListener("resize", invalidate);
      try { map.remove(); } catch (e) {}
      mapRef.current = null;
      layerRef.current = null;
      tileRef.current = null;
    };
  }, []);

  // (Re)mount the tile layer per provider; on repeated tile failures fall back
  // to the other provider once (e.g. CARTO unreachable → 高德, and vice versa).
  useWbcEffect(function () {
    var map = mapRef.current;
    if (!map || !window.L) return;
    var L = window.L;
    if (tileRef.current) { try { map.removeLayer(tileRef.current); } catch (e) {} }
    var config = wbcTileConfig(provider);
    var errors = 0;
    var tiles = L.tileLayer(config.url, config.options);
    tiles.on("tileerror", function () {
      errors += 1;
      if (errors >= 3 && !switchedRef.current) {
        switchedRef.current = true;
        setProvider(provider === "amap" ? "direct" : "amap");
      }
    });
    tiles.addTo(map);
    tileRef.current = tiles;
  }, [provider]);

  // Render pins + routes; AMap needs GCJ-02 coordinates.
  useWbcEffect(function () {
    var layer = layerRef.current;
    if (!layer || !window.L || !data) return;
    var L = window.L;
    layer.clearLayers();
    var pins = Array.isArray(data.pins) ? data.pins : [];
    var routes = Array.isArray(data.routes) ? data.routes : [];
    var convert = provider === "amap"
      ? function (lat, lng) { return wbcWgs84ToGcj02(lat, lng); }
      : function (lat, lng) { return [lat, lng]; };
    var byName = {};
    var latlngs = [];
    var focusKey = wbcMapItemKey(focusItem);
    pins.forEach(function (pin) {
      var lat = Number(pin.lat), lng = Number(pin.lng);
      if (!isFinite(lat) || !isFinite(lng)) return;
      var pos = convert(lat, lng);
      byName[String(pin.name || "")] = pos;
      latlngs.push(pos);
      var selected = focusKey === wbcMapItemKey(Object.assign({ kind: "pin" }, pin));
      var marker = L.circleMarker(pos, {
        radius: selected ? 8 : 5,
        color: selected ? "#dff8ea" : "#ffffff",
        weight: selected ? 3 : 2,
        fillColor: "#22a861",
        fillOpacity: 0.96,
      }).addTo(layer);
      var note = String(pin.note || pin.note_md || "").trim();
      var popup = document.createElement("div");
      popup.className = "wbc-map-popup";
      var title = document.createElement("strong");
      title.className = "wbc-map-popup-title";
      title.textContent = String(pin.name || "");
      popup.appendChild(title);
      if (note) {
        var body = document.createElement("div");
        body.className = "wbc-map-popup-markdown markdown";
        var noteHtml = wbcRenderMapMarkdown(note)
          .replace(/\*\*([^*<]+)\*\*/g, "<strong>$1</strong>")
          .replace(/\\n/g, "<br>");
        body.innerHTML = noteHtml;
        popup.appendChild(body);
      }
      marker.bindPopup(popup, { maxWidth: 340, minWidth: 210 });
    });
    routes.forEach(function (route) {
      var from = byName[String(route.from_name || route.from || "")];
      var to = byName[String(route.to_name || route.to || "")];
      if (!from || !to) return;
      var selected = focusKey === wbcMapItemKey(Object.assign({ kind: "route" }, route));
      var line = L.polyline([from, to], { color: "#22a861", weight: selected ? 5 : 3, opacity: selected ? 1 : 0.78, dashArray: selected ? "" : "6 6" }).addTo(layer);
      var label = [route.transport, route.route_note].filter(Boolean).join(" · ");
      if (label) line.bindPopup(String(label).replace(/</g, "&lt;"));
    });
    if (latlngs.length && mapRef.current) {
      try { mapRef.current.fitBounds(latlngs, { padding: [28, 28], maxZoom: 12 }); } catch (e) {}
    }
  }, [data, provider, wbcMapItemKey(focusItem)]);

  useWbcEffect(function () {
    if (!focusItem || !data || !mapRef.current) return;
    var pins = Array.isArray(data.pins) ? data.pins : [];
    var convert = provider === "amap"
      ? function (lat, lng) { return wbcWgs84ToGcj02(lat, lng); }
      : function (lat, lng) { return [lat, lng]; };
    var targets = [];
    if (focusItem.kind === "route") {
      [focusItem.from_name || focusItem.from, focusItem.to_name || focusItem.to].forEach(function (name) {
        var pin = pins.find(function (candidate) { return String(candidate.name || "") === String(name || ""); });
        if (pin && isFinite(Number(pin.lat)) && isFinite(Number(pin.lng))) targets.push(convert(Number(pin.lat), Number(pin.lng)));
      });
    } else if (isFinite(Number(focusItem.lat)) && isFinite(Number(focusItem.lng))) {
      targets.push(convert(Number(focusItem.lat), Number(focusItem.lng)));
    }
    try {
      if (targets.length > 1) mapRef.current.fitBounds(targets, { padding: [48, 48], maxZoom: 11 });
      else if (targets.length === 1) mapRef.current.setView(targets[0], 12);
    } catch (e) {}
  }, [wbcMapItemKey(focusItem), data, provider]);

  var empty = data && (!Array.isArray(data.pins) || data.pins.length === 0);

  return (
    <div className="wbc-map">
      <div className="wbc-map-holder" ref={holderRef}></div>
      {empty && <div className="wbc-map-empty">{wbcT("workbenchChat.mapEmpty", "No map pins in this chat yet.")}</div>}
    </div>
  );
}

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

// Context-window gauge + composition for ONE conversation. Reads the agent's
// raw per-session state (sessions/<id>/state.json) so the numbers are scoped to
// this chat and reflect what the compactor actually measures; polls while a run
// streams so the panel updates in real time as turns are appended.
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
    function load() {
      fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/context")
        .then(function (r) { return r.json(); })
        .then(function (payload) {
          if (!cancelled && payload && !payload.error) {
            var nextData = { chatId: chatId, payload: payload };
            WBC_LIVE_CHAT_METRICS_CACHE.set(chatId, nextData);
            setData(nextData);
          }
        })
        .catch(function () {});
    }
    load();
    var timer = running ? setInterval(load, 3500) : null;
    return function () { cancelled = true; if (timer) clearInterval(timer); };
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

  if (segTotal <= 0 && used <= 0) {
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

function WbcQuickActionItems({ chat, menu, onBeforeAction, onRename, onDelete, onToTask, toTaskBusy, onCompact, compactBusy }) {
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
      {!chat.legacy && onCompact && (
        <button type="button" role={role} disabled={compactBusy} onClick={run(onCompact)}>
          {compactBusy ? <span className="wbc-spinner" aria-hidden="true"></span> : WBC_ICONS.compact}
          <span>{wbcT(compactBusy ? "workbenchChat.compactBusy" : "workbenchChat.compact", compactBusy ? "Compressing…" : "Compress chat")}</span>
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
  var usage = (liveData && liveData.usage) || chat.usage || {};
  var currentModel = wbcCurrentModel(chat, null, runtime, liveData);
  var convertedTitle = chat.convertedSessionId ? String(chat.convertedTaskTitle || "").trim() : "";
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
        <div className="wbc-overview-details">
          <div><span>{wbcT("workbenchChat.model", "Model")}</span><b className="wbc-kv-mono" title={currentModel || ""}>{currentModel || "—"}</b></div>
          <div><span>{wbcT("chat.runId", "Session ID")}</span><b className="wbc-kv-mono" title={chat.id}>{chat.id}</b></div>
        </div>
        <div className="wbc-overview-facts">
          <div>
            <span>{wbcT("workbenchChat.messageCount", "Messages")}</span>
            <b>{chat.messageCount != null ? chat.messageCount : (chat.messages || []).length}</b>
          </div>
          <div>
            <span>{wbcT("workbenchChat.createdAt", "Created")}</span>
            <b>{wbcFormatTime(chat.createdAt) || "—"}</b>
          </div>
        </div>
      </section>
      <WbcOverviewUsage usage={usage} />
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
  if (label) return label;
  // Match known prefixes for a generic label
  if (id.startsWith("history.compacted.")) return wbcT("workbenchChat.ctxBlock.history.compacted", "Compacted history");
  if (id.startsWith("history.deep_reflection.")) return wbcT("workbenchChat.ctxBlock.history.deep_reflection", "Deep reflection");
  if (id.startsWith("history.tool_result.")) return wbcT("workbenchChat.ctxBlock.history.tool_result", "Tool result");
  if (id.startsWith("session.history.")) return wbcT("workbenchChat.ctxBlock.session.history", "History message");
  if (id.startsWith("user.current.")) return wbcT("workbenchChat.ctxBlock.user.current", "User message");
  return id.replace(/^(main\.|runtime\.|command\.|spawn_policy\.|history\.|session\.)/, "");
}

function WbcContextBlockList({ chat, running, compact }) {
  var [data, setData] = useWbcState(null);
  var chatId = chat ? chat.id : "";
  var updatedAt = chat ? chat.updatedAt : "";
  var contextRevision = chat ? chat.contextRevision : 0;

  useWbcEffect(function () {
    if (!chatId) { setData(null); return undefined; }
    var cancelled = false;
    function load() {
      fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/context-blocks")
        .then(function (r) { return r.json(); })
        .then(function (payload) { if (!cancelled && payload && !payload.error) setData(payload); })
        .catch(function () {});
    }
    load();
    var timer = running ? setInterval(load, 3500) : null;
    return function () { cancelled = true; if (timer) clearInterval(timer); };
  }, [chatId, updatedAt, contextRevision, running]);

  if (!data || !Array.isArray(data.layers) || data.layers.length === 0) {
    return React.createElement("p", { className: "workbench-muted" },
      wbcT("workbenchChat.ctxBlocks.empty", "Send a message and the context composition will appear here."));
  }

  var layers = data.layers;
  var msgTokens = data.messageTokens || 0;
  // Total for the bar: include all layers (system + ephemeral + messages)
  var barTotal = layers.reduce(function (sum, l) { return sum + (Number(l.totalTokens) || 0); }, 0);
  // Gauge head shows message tokens only (matches Overview ctxUsed)
  var total = msgTokens || barTotal;

  // Build legend: explode system_prefix and messages sub-blocks for the bar
  var SYS_SHADE_MAP = { system: 0, memory: 1, skills: 2, runtime: 3, command_prompt: 4, spawn_policy: 5, short_term: 6 };
  function sysShadeForBlock(b) {
    // Use block type first, fall back to id prefix matching for "system"-typed blocks
    var t = b.type || "";
    if (SYS_SHADE_MAP[t] != null && t !== "system") return SYS_SHADE_MAP[t];
    // "system" type covers many blocks — differentiate by id prefix
    var id = b.id || "";
    if (id.startsWith("main.system.static_extra")) return 4;
    if (id.startsWith("main.system.language")) return 1;
    if (id.startsWith("memory.")) return 1;
    if (id.startsWith("skills.")) return 2;
    if (id.startsWith("runtime.workspace")) return 3;
    if (id.startsWith("runtime.permission")) return 6;
    if (id.startsWith("runtime.project")) return 1;
    if (id.startsWith("runtime.session")) return 2;
    if (id.startsWith("runtime.spawn")) return 5;
    if (id.startsWith("runtime.goal")) return 4;
    if (id.startsWith("command.")) return 5;
    if (id.startsWith("spawn_policy.")) return 7;
    if (id.startsWith("short_term.")) return 7;
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
      React.createElement("b", null, wbcCompactNumber(total)),
      React.createElement("span", null, wbcT("workbenchChat.ctxBlocks.totalTokens", "tokens"))
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
          return React.createElement("details", { key: layer.id, className: "wbc-ctx-layer-detail", open: true },
            React.createElement("summary", { className: "wbc-ctx-legend-layer-head" },
              React.createElement("i", { className: "wbc-ctx-dot seg-" + layer.id, "aria-hidden": "true" }),
              React.createElement("span", null, layerLabel),
              React.createElement("em", null, wbcCompactNumber(tokens)),
              React.createElement("span", { className: "wbc-ctx-layer-chevron", "aria-hidden": "true" }, WBC_ICONS.chevronDown)
            ),
            React.createElement("div", { className: "wbc-ctx-legend-layer-body" },
              (isMsg || isSys) && blocks.length > 0
                ? blocks.map(function (b) {
                    var seg = _ctxSegFromBlock(b, isMsg);
                    if (!seg) return null;
                    return React.createElement("div", { key: seg.key, className: "wbc-ctx-legend-item" },
                      React.createElement("i", { className: "wbc-ctx-dot " + seg.dotClass }),
                      React.createElement("span", null, seg.label),
                      React.createElement("em", null, wbcCompactNumber(seg.tokens))
                    );
                  }).filter(Boolean)
                : React.createElement("div", { className: "wbc-ctx-legend-item" },
                    React.createElement("i", { className: "wbc-ctx-dot seg-" + layer.id }),
                    React.createElement("span", null, layerLabel),
                    React.createElement("em", null, wbcCompactNumber(tokens))
                  )
            )
          );
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
    // The inbox can change independently of the chat transcript (tool result,
    // guidance claim, recovery), so keep observing while the Context tab is
    // mounted instead of relying on the UI's possibly stale `running` flag.
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
  var value = String(status || "queued");
  var labels = {
    queued: ["workbenchChat.inbox.status.queued", "Queued"],
    claimed: ["workbenchChat.inbox.status.claimed", "Claimed"],
    completed: ["workbenchChat.inbox.status.completed", "Completed"],
    failed: ["workbenchChat.inbox.status.failed", "Failed"],
    cancelled: ["workbenchChat.inbox.status.cancelled", "Cancelled"],
    running: ["workbenchChat.inbox.status.running", "Running"],
    ready: ["workbenchChat.inbox.status.ready", "Ready"],
    consumed: ["workbenchChat.inbox.status.consumed", "Consumed"],
  };
  var item = labels[value] || ["workbenchChat.inbox.status.unknown", value || "Unknown"];
  return { value: value, label: wbcT(item[0], item[1]) };
}

function wbcInboxEventLabel(item) {
  if (item.type === "guidance") return wbcT("workbenchChat.inbox.guidance", "User guidance");
  if (item.type === "tool_result" || item.type === "tool_activity") {
    return item.toolName
      ? wbcT("toolName." + item.toolName, item.toolName)
      : wbcT(
          item.type === "tool_result" ? "workbenchChat.inbox.toolResult" : "workbenchChat.inbox.toolActivity",
          item.type === "tool_result" ? "Tool result" : "Tool activity"
        );
  }
  return String(item.type || wbcT("workbenchChat.inbox.event", "Inbox event"));
}

function wbcInboxArgumentPreview(argumentsValue) {
  if (!argumentsValue || typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) return "";
  return Object.keys(argumentsValue).map(function (key) {
    var value = argumentsValue[key];
    if (value === null || value === undefined || value === "") return "";
    if (typeof value === "string") return value;
    try { return JSON.stringify(value); } catch (e) { return String(value); }
  }).filter(Boolean).join(" · ").slice(0, 240);
}

function WbcInboxCard({ chat, running, hideTitle }) {
  var liveView = useWbcLiveInbox(chat, running);
  var data = liveView.data;
  var counts = (data && data.counts) || {};
  var live = (data && data.live) || {};
  var events = data && Array.isArray(data.events) ? data.events : [];
  var tools = data && Array.isArray(data.tools) ? data.tools : [];
  var activeTools = tools.filter(function (tool) {
    return tool.state === "queued" || tool.state === "running";
  });
  var feed = events.concat(activeTools.map(function (tool) {
    return {
      eventId: "active:" + tool.toolCallId,
      type: "tool_activity",
      status: tool.state,
      toolName: tool.toolName,
      toolCallId: tool.toolCallId,
      createdAt: tool.updatedAt,
      preview: wbcInboxArgumentPreview(tool.arguments),
    };
  })).sort(function (left, right) {
    return String(right.createdAt || "").localeCompare(String(left.createdAt || ""));
  }).slice(0, 20);
  var queueDepth = !data
    ? null
    : data.active
      ? Number(live.queueDepth || 0)
      : Number(counts.queued || 0) + Number(counts.claimed || 0);

  return (
    <section className={"workbench-side-section wbc-inbox-card" + (hideTitle ? " title-hidden" : "")} aria-labelledby={hideTitle ? undefined : "wbc-inbox-title"} aria-label={hideTitle ? wbcT("workbenchChat.inbox.title", "Session inbox") : undefined}>
      <div className="wbc-inbox-head">
        {!hideTitle && <h3 id="wbc-inbox-title">{wbcT("workbenchChat.inbox.title", "Session inbox")}</h3>}
        {hideTitle && feed.length === 0 && (
          <span className="wbc-context-empty-label">{wbcT("workbenchChat.inbox.title", "Session inbox")}</span>
        )}
        <span className={"wbc-inbox-queue-count" + (queueDepth !== null && queueDepth > 0 ? " active" : "")} aria-live="polite">
          {queueDepth === 0 ? (
            <span>{wbcT("workbenchChat.inbox.queueEmpty", "Queue empty")}</span>
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
              <p>{wbcT("workbenchChat.inbox.empty", "No inbox events for this run yet.")}</p>
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
                      {item.preview && <p title={item.preview}>{item.preview}</p>}
                      <div className="wbc-inbox-event-meta">
                        {item.createdAt && <time dateTime={item.createdAt} title={item.createdAt}>{wbcFormatTime(item.createdAt)}</time>}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </React.Fragment>
      )}
    </section>
  );
}

var WBC_PROGRESSIVE_TOOL_PACKAGES = new Set([
  "code_tools",
  "browser_tools",
  "desktop_tools",
  "memory_tools",
  "knowledge_tools",
  "task_tools",
  "entity_tools",
  "map_tools",
  "subagent_tools",
  "delivery_tools",
  "skill_tools",
  "remote_tools",
  "integration_tools",
]);

function wbcUsedToolPackages(chat, runtime) {
  var used = [];
  var seen = new Set();
  function addToolName(value) {
    var name = String(value || "").trim();
    if (!WBC_PROGRESSIVE_TOOL_PACKAGES.has(name) || seen.has(name)) return;
    seen.add(name);
    used.push(name);
  }
  (chat && Array.isArray(chat.messages) ? chat.messages : []).forEach(function (message) {
    (message && Array.isArray(message.tools) ? message.tools : []).forEach(function (tool) {
      addToolName(tool && tool.name);
    });
  });
  function addProgress(items) {
    (Array.isArray(items) ? items : []).forEach(function (entry) {
      addToolName(entry && (entry.tool || entry.text));
    });
  }
  addProgress(runtime && runtime.progress);
  (runtime && Array.isArray(runtime.activities) ? runtime.activities : []).forEach(function (activity) {
    addProgress(activity && activity.progress);
  });
  (runtime && Array.isArray(runtime.segments) ? runtime.segments : []).forEach(function (segment) {
    addProgress(segment && segment.progress);
  });
  return used;
}

function WbcContextTab({ project, chat, runtime }) {
  var usedToolPackages = wbcUsedToolPackages(chat, runtime);
  var conversationTitle = wbcT("workbenchChat.conversationContext", "Conversation context");
  return (
    <div className="wbc-context-sections">
      <section className="workbench-side-section" aria-label={conversationTitle}>
        <WbcContextBlockList chat={chat} running={!!runtime} compact={false} />
      </section>
      <WbcInboxCard chat={chat} running={!!runtime} hideTitle={true} />
      <section className="workbench-side-section" aria-label={wbcT("workbenchChat.usedToolPackages", "Used tool packages")}>
        {usedToolPackages.length === 0 ? (
          <div className="wbc-context-empty-module">
            <div className="wbc-context-empty-head">
              <span className="wbc-context-empty-label">{wbcT("workbenchChat.usedToolPackages", "Used tool packages")}</span>
              <b>{wbcT("workbenchChat.notUsed", "Not used")}</b>
            </div>
            <div className="wbc-side-empty">
              <p>{wbcT("workbenchChat.noUsedToolPackages", "The agent has not used a tool package in this chat.")}</p>
            </div>
          </div>
        ) : usedToolPackages.map(function (wireName) {
            return (
              <div className="workbench-check wbc-tool-pack-row" key={wireName}>
                <span className="workbench-status-dot green" aria-hidden="true"></span>
                <span>{wbcT("toolName." + wireName, wireName)}</span>
              </div>
            );
          })}
      </section>
      <section className="workbench-side-section wbc-context-stats" aria-label={wbcT("workbenchChat.stats", "Chat stats")}>
        <div className="wb-kv"><span>{wbcT("workbenchChat.messageCount", "Messages")}</span><b>{chat ? (chat.messageCount != null ? chat.messageCount : (chat.messages || []).length) : 0}</b></div>
        <div className="wb-kv"><span>{wbcT("workbenchChat.updatedAt", "Last updated")}</span><b>{chat ? (wbcFormatTime(chat.updatedAt) || "—") : "—"}</b></div>
      </section>
    </div>
  );
}

function WbcArtifactsTab({ chat, onSelectArtifact }) {
  var files = wbcChatArtifactFiles(chat);
  return (
    <div className="wbc-artifact-list">
        {files.length === 0 && <p className="workbench-muted">{wbcT("workbenchChat.noFiles", "This chat has not produced files yet. Uploads and agent-generated files will appear here.")}</p>}
        {files.map(function (item, i) {
          var file = item.file;
          return (
            <button
              type="button"
              className="wbc-artifact-list-row"
              key={(file.id || file.url || i) + "_" + i}
              onClick={function () { if (onSelectArtifact) onSelectArtifact(file); }}
              title={wbcT("workbenchChat.openArtifactPreview", "Open artifact preview")}
            >
              <span className="wbc-artifact-list-icon" aria-hidden="true">{WBC_ICONS.file}</span>
              <span className="wbc-artifact-list-copy">
                <b>{file.name || "file"}</b>
                <small>{item.role === "user" ? wbcT("workbenchChat.userUpload", "User upload") : wbcT("workbenchChat.agentGenerated", "Agent generated")}</small>
              </span>
              <span className="wbc-artifact-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            </button>
          );
        })}
    </div>
  );
}

window.CyreneUI.chat = window.CyreneUI.register("chat", {
  Model: WorkbenchChatModel,
  Runtimes: WorkbenchChatRuntimes,
  budgetCodes: WORKBENCH_BUDGET_CODES,
  Composer: WbcComposer,
  UserMessage: WbcUserMessage,
  AssistantMessage: WbcAssistantMessage,
  LiveMessage: WbcLiveMessage,
  clearComposerDraft: wbcClearComposerDraft,
  Page: WorkbenchChatPage,
});
