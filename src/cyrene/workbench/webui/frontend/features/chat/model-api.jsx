import { workbenchServices } from "../../shared/runtime/services.jsx"
import { wbcT } from "./core.jsx"
import { wbcAgentToolPayload, wbcRouteAgentEvent } from "./agent-events.jsx"
import { wbcErrorText } from "./errors.jsx"

// Route ordinary JSON calls through the shared wrapper (workbench-api.jsx):
  // a 30s AbortController timeout so a stalled backend no longer spins forever,
  // plus normalized errors. toast:false keeps this conversation's own inline
  // error banner (setError → wbcErrorText) as the single feedback channel;
  // callers can pass a longer/disabled `timeout` per call.
  function apiJson(url, options) {
    return workbenchServices.api().json(url, { toast: false, ...(options || {}) });
  }

  function listChats(projectId) {
    return apiJson("/api/workbench/chats?project=" + encodeURIComponent(projectId || ""))
      .then(function (payload) { return Array.isArray(payload.chats) ? payload.chats : []; });
  }

  function createChat(projectId, title) {
    return createChatWithBinding(projectId, title, null);
  }

  // Create a chat with an optional draft Agent binding (handoff §8.4). The
  // backend normalizes a missing binding to the built-in Cyrene Agent.
  function createChatWithBinding(projectId, title, binding) {
    var body = { project: projectId, title: title || "" };
    if (binding && typeof binding === "object") {
      if (binding.agent && typeof binding.agent === "object" && binding.agent.installationId) {
        body.agent = binding.agent;
      }
      if (binding.modelAccess && typeof binding.modelAccess === "object" && binding.modelAccess.mode) {
        body.modelAccess = binding.modelAccess;
      }
    }
    return apiJson("/api/workbench/chats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (payload) { return payload.chat; });
  }

  // Installed Agent catalog for the Composer submenu. Phase 1 lists the
  // built-in Cyrene Agent plus every installed external Agent with its
  // availability state; the backend supplies the definitive cards.
  function listAgents() {
    return apiJson("/api/agents", { toast: false })
      .then(function (payload) { return Array.isArray(payload.agents) ? payload.agents : []; });
  }

  function getAgent(installationId) {
    return apiJson("/api/agents/" + encodeURIComponent(String(installationId || "")), { toast: false })
      .then(function (payload) { return payload.agent || null; });
  }

  function listSideAgents(chatId) {
    if (!chatId) {
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
    if (!chatId) {
      return Promise.resolve({ rounds: [], activeRoundId: "", agents: [], messages: [] });
    }
    var query = roundId ? ("?round_id=" + encodeURIComponent(roundId)) : "";
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/subagents" + query, options);
  }

  function getChanges(chatId, options) {
    if (!chatId) {
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
    if (!chatId) {
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

  function updateChatAgent(chatId, binding) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(binding || {}),
    }).then(function (payload) { return payload.chat; });
  }

  function getAgentConfigOptions(chatId) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/agent-config-options", { toast: false });
  }

  function updateAgentConfigValues(chatId, values) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agentConfigValues: values || {} }),
    }).then(function (payload) { return payload.chat; });
  }

  function updateChatPreferences(chatId, values) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values || {}),
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

  function compactChat(chatId) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/compact", {
      method: "POST",
    });
  }

  function generateMemory(chatId, lang) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/memory-learning", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lang: lang === "zh" ? "zh" : "en" }),
    });
  }

  function interrupt(chatId) {
    return fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/interrupt", { method: "POST" })
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
    return workbenchServices.api().fetch("/api/workbench/uploads", { method: "POST", body: form, timeout: 120000 }).then(function (r) {
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
        if (String(err.code || "").startsWith("budget_")) {
          workbenchServices.feedback().showToast(wbcErrorText(err), "error");
        }
        throw err;
      });
    }
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    // Idempotency for the versioned Agent envelope: the same eventId is never
    // dispatched twice within one stream (a reconnect starts a fresh stream).
    // The dedupe window is bounded so a very long stream cannot grow the set
    // without limit; the oldest seen ids fall out of the window.
    var seenEventIds = new Set();
    var seenEventOrder = [];
    var WBC_EVENT_ID_DEDUPE_LIMIT = 4096;

    function rememberEventId(eventId) {
      if (seenEventIds.has(eventId)) return false;
      seenEventIds.add(eventId);
      seenEventOrder.push(eventId);
      if (seenEventOrder.length > WBC_EVENT_ID_DEDUPE_LIMIT) {
        var oldest = seenEventOrder.shift();
        seenEventIds.delete(oldest);
      }
      return true;
    }

    function handleLine(line) {
      if (!line.trim()) return;
      var event;
      try { event = JSON.parse(line); } catch (e) { return; }
      var eventCursor = Number(event._seq || 0);
      if (eventCursor > 0 && handlers.onEventCursor) {
        handlers.onEventCursor(eventCursor);
      }
      var type = String(event.type || "");
      var eventId = String(event.eventId || event.event_id || "");
      if (eventId) {
        if (!rememberEventId(eventId)) return;
      }
      // Versioned Agent core events first; legacy snake_case events below stay
      // untouched so the built-in runtime keeps its exact historical behavior.
      if (wbcRouteAgentEvent(type, event, handlers)) return;
      if (type === "ack" && handlers.onAck) handlers.onAck(event);
      else if (type === "chat_timing" && handlers.onTiming) handlers.onTiming(event);
      else if (type === "intermediate_message" && handlers.onIntermediateMessage) handlers.onIntermediateMessage(event);
      else if (type === "reasoning_start" && handlers.onReasoningStart) handlers.onReasoningStart(event);
      else if (type === "reasoning_delta" && handlers.onReasoningDelta) handlers.onReasoningDelta(event.delta || "", event);
      else if (type === "reasoning_done" && handlers.onReasoningDone) handlers.onReasoningDone(event.response || "", event);
      else if (type === "reply_start" && handlers.onReplyStart) handlers.onReplyStart(event);
      else if (type === "reply_delta" && handlers.onReplyDelta) handlers.onReplyDelta(event.delta || "");
      else if (type === "reply_done" && handlers.onReplyDone) handlers.onReplyDone(event.response || "");
      else if (type === "tool_call_started" && handlers.onToolStarted) handlers.onToolStarted(wbcAgentToolPayload(event));
      else if (type === "tool_call_progress" && handlers.onToolUpdated) handlers.onToolUpdated(wbcAgentToolPayload(event));
      else if (type === "tool_call_finished" && handlers.onToolCompleted) handlers.onToolCompleted(wbcAgentToolPayload(event));
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
      else if (
        type.indexOf(".") >= 0
        || event.schemaVersion != null
        || event.agentId != null
        || event.installationId != null
      ) {
        // Unknown namespaced/Agent event — keep a sanitized, expandable
        // diagnostic card instead of silently losing protocol information.
        if (handlers.onUnknownAgentEvent) handlers.onUnknownAgentEvent(event);
        try { console.debug("[agent-event] unhandled event " + type); } catch (_e) {}
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
    var body = {
      message: input.message || "",
      clientRequestId: input.clientRequestId || "",
      clientSendEpochMs: Number(input.clientSendEpochMs || Date.now()),
      attachments: input.attachments || [],
      mode: input.mode || "default",
      command: input.command || "",
      model: input.model || "",
      reasoningEffort: input.reasoningEffort || "",
      retry: !!input.retry,
      forkReplay: !!input.forkReplay,
      stream: true,
      lang: workbenchServices.i18n().getLang(),
      uiInstanceId: window.CyreneUI.has("uiSurface")
        ? workbenchServices.uiSurface().getInstanceId()
        : "",
    };
    if (Object.prototype.hasOwnProperty.call(input, "workspaceOverride")) {
      body.workspaceOverride = input.workspaceOverride || "";
    }
    if (Object.prototype.hasOwnProperty.call(input, "soulActive")) {
      body.soulActive = !!input.soulActive;
    }
    if (Object.prototype.hasOwnProperty.call(input, "workspaceActive")) {
      body.workspaceActive = !!input.workspaceActive;
    }
    if (Object.prototype.hasOwnProperty.call(input, "remoteDeviceIds")) {
      body.remoteDeviceIds = Array.isArray(input.remoteDeviceIds) ? input.remoteDeviceIds : [];
    }
    if (Object.prototype.hasOwnProperty.call(input, "contextActivations")) {
      body.contextActivations = input.contextActivations || {};
    }
    return fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: signal,
    }).then(function (response) {
      return consumeEventStream(response, handlers);
    });
  }

  function reconnectRun(chatId, handlers, signal, cursor) {
    var eventCursor = Math.max(0, Number(cursor || 0));
    var query = eventCursor > 0 ? ("?cursor=" + encodeURIComponent(eventCursor)) : "";
    return fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/run-stream" + query, {
      method: "GET",
      signal: signal,
    }).then(function (response) {
      return consumeEventStream(response, handlers);
    });
  }

  function recordChatTiming(chatId, runId, payload) {
    if (!chatId || !runId) return Promise.resolve(null);
    return fetch(
      "/api/workbench/chats/" + encodeURIComponent(chatId)
        + "/runs/" + encodeURIComponent(runId) + "/timing",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
        keepalive: true,
      }
    ).catch(function () { return null; });
  }

  function sendGuidance(chatId, message, clientRequestId) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/guidance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: message || "",
        clientRequestId: clientRequestId || "",
        uiInstanceId: window.CyreneUI.has("uiSurface")
          ? workbenchServices.uiSurface().getInstanceId()
          : "",
      }),
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
    return workbenchServices.api().json("/api/workbench/chats/" + encodeURIComponent(chatId) + "/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question_id: questionId || "",
        answer: answerText || "",
        mode: options.mode || undefined,
        uiInstanceId: window.CyreneUI.has("uiSurface")
          ? workbenchServices.uiSurface().getInstanceId()
          : "",
      }),
      timeout: 0,
      toast: false,
    });
  }

  function answerAgentRequest(chatId, requestId, response) {
    return workbenchServices.api().json(
      "/api/workbench/chats/" + encodeURIComponent(chatId) + "/agent-requests/"
        + encodeURIComponent(requestId) + "/respond",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response: response || {} }),
        timeout: 0,
        toast: false,
      }
    );
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

  var WorkbenchChatModel = {
    listChats: listChats,
    createChat: createChat,
    createChatWithBinding: createChatWithBinding,
    listAgents: listAgents,
    getAgent: getAgent,
    listSideAgents: listSideAgents,
    createSideAgent: createSideAgent,
    getChat: getChat,
    getSubagents: getSubagents,
    getChanges: getChanges,
    getChangeDiff: getChangeDiff,
    getInbox: getInbox,
    renameChat: renameChat,
    updateChatAgent: updateChatAgent,
    getAgentConfigOptions: getAgentConfigOptions,
    updateAgentConfigValues: updateAgentConfigValues,
    updateChatPreferences: updateChatPreferences,
    generateChatGroupMetadata: generateChatGroupMetadata,
    listChatGroups: listChatGroups,
    replaceChatGroups: replaceChatGroups,
    migrateChatGroups: migrateChatGroups,
    deleteChat: deleteChat,
    compactChat: compactChat,
    generateMemory: generateMemory,
    interrupt: interrupt,
    uploadFiles: uploadFiles,
    sendMessage: sendMessage,
    recordChatTiming: recordChatTiming,
    sendGuidance: sendGuidance,
    reconnectRun: reconnectRun,
    answerChat: answerChat,
    answerAgentRequest: answerAgentRequest,
    forkChat: forkChat,
  };

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

export { WorkbenchChatModel }
