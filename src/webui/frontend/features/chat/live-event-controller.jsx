import { workbenchServices } from "../../shared/runtime/services.jsx"
import { useWbcEffect, wbcPublishChatModelChanged, wbcT } from "../../workbench-chat.jsx"

function wbcForwardRemoteJobUpdate(event) {
  try {
    window.dispatchEvent(new CustomEvent("cyrene:remote-job-update", { detail: event }));
    var feedback = workbenchServices.feedback();
    if (feedback && typeof feedback.showToast === "function") {
      feedback.showToast(
        wbcT("workbenchChat.remoteJobFinished", "Remote job {jobId}: {status}", {
          jobId: event.job_id || "", status: event.status || "completed",
        }),
        event.status === "completed" ? "success" : "info"
      );
    }
  } catch (error) {}
}

function wbcApplyFallbackModel(event) {
  if (event.type !== "phase_transition" || String(event.to || "") !== "fallback_model") return;
  var chatId = String(event.session_id || event.chat_id || event.chatId || "");
  var params = event.detail_params && typeof event.detail_params === "object" ? event.detail_params : {};
  var fallbackModel = String(params.fallbackModel || params.fallback_model || "").trim();
  if (chatId && fallbackModel) {
    wbcPublishChatModelChanged(chatId, { model: fallbackModel }, { refresh: false });
  }
}

function wbcScheduleRemoteChatRefresh(context, event) {
  var changedChatId = String(event.chat_id || event.session_id || event.chatId || "");
  context.remoteChangedChatIdsRef.current.add(changedChatId || "*");
  if (context.remoteChatRefreshTimerRef.current) clearTimeout(context.remoteChatRefreshTimerRef.current);
  context.remoteChatRefreshTimerRef.current = setTimeout(function () {
    context.remoteChatRefreshTimerRef.current = null;
    var changedChatIds = context.remoteChangedChatIdsRef.current;
    context.remoteChangedChatIdsRef.current = new Set();
    context.refreshChats("");
    var openChatId = String(context.activeChatIdRef.current || "");
    if (openChatId && (changedChatIds.has("*") || changedChatIds.has(openChatId))) {
      context.setLoadRevision(function (value) { return value + 1; });
    }
  }, 80);
}

function wbcApplyProactiveMessage(context, event) {
  if (String(event.project_id || "") !== String(context.projectIdRef.current || "")) return;
  var chatId = String(event.chat_id || event.session_id || "");
  var message = event.message;
  var updatedAt = String(event.updated_at || (message && message.createdAt) || "");
  context.setChats(function (previous) {
    var found = false;
    var next = previous.map(function (chat) {
      if (chat.id !== chatId) return chat;
      found = true;
      return {
        ...chat, updatedAt: updatedAt || chat.updatedAt,
        preview: message ? message.content : chat.preview,
        messageCount: (chat.messageCount || 0) + 1,
      };
    });
    if (!found) return previous;
    return next.slice().sort(function (a, b) {
      return String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""));
    });
  });
  if (context.activeChatIdRef.current === chatId && message) {
    context.setActiveChat(function (previous) {
      if (!previous || previous.id !== chatId) return previous;
      var messages = previous.messages || [];
      if (messages.some(function (item) { return item.id === message.id; })) return previous;
      return { ...previous, updatedAt: updatedAt || previous.updatedAt, messages: messages.concat([message]) };
    });
  }
}

function wbcApplyPlanEvent(context, event, chatId) {
  if (!chatId || context.activeChatIdRef.current !== chatId
    || (event.type !== "plan_progress" && event.type !== "plan") || !event.plan) return;
  context.setActiveChat(function (previous) {
    return !previous || previous.id !== chatId ? previous : { ...previous, activePlan: event.plan };
  });
}

function wbcScheduleSubagentRefresh(context, event, chatId) {
  if (!chatId || context.activeChatIdRef.current !== chatId || !(
    event.type === "subagent_update"
    || event.type === "agent_comm"
    || event.type === "agent_chat_user_message"
  )) return;
  if (context.subagentRefreshTimerRef.current) clearTimeout(context.subagentRefreshTimerRef.current);
  context.subagentRefreshTimerRef.current = setTimeout(function () { context.loadSubagents(chatId); }, 120);
}

function wbcApplyBrowserEvent(context, event, browserEventChatId) {
  if (!(event.type === "browser_frame" || event.type === "browser_takeover_request")
    || !context.activeChatIdRef.current
    || (browserEventChatId && browserEventChatId !== String(context.activeChatIdRef.current))) return;
  context.setBrowserActiveByChat(function (prev) {
    var sid = String(browserEventChatId || context.activeChatIdRef.current || "");
    return !sid || prev[sid] ? prev : { ...prev, [sid]: true };
  });
  context.setBrowserWindowModeByChat(function (prev) {
    var sid = String(browserEventChatId || context.activeChatIdRef.current || "");
    return !sid || prev[sid] ? prev : { ...prev, [sid]: "pip" };
  });
}

function wbcHandleLiveEvent(context, event) {
  if (!event) return;
  if (event.type === "remote_job_update") { wbcForwardRemoteJobUpdate(event); return; }
  wbcApplyFallbackModel(event);
  if (event.type === "workbench_chat_changed") {
    if (event.project_id && String(event.project_id) !== String(context.projectIdRef.current || "")) return;
    if (context.applyChatSummaryEvent(event)) return;
    wbcScheduleRemoteChatRefresh(context, event);
    return;
  }
  if (event.type === "workspace_changes") {
    try { window.dispatchEvent(new CustomEvent("workbench:workspace-changes", { detail: event })); } catch (error) {}
  }
  if (event.type === "workbench_proactive_message") { wbcApplyProactiveMessage(context, event); return; }
  var chatId = String(event.session_id || event.chat_id || event.chatId || "");
  wbcApplyPlanEvent(context, event, chatId);
  wbcScheduleSubagentRefresh(context, event, chatId);
  wbcApplyBrowserEvent(context, event, chatId);
}

function useWbcLiveEventController(context) {
  useWbcEffect(function () {
    var unsubscribe = workbenchServices.events().subscribe(function (event) {
      wbcHandleLiveEvent(context, event);
    });
    return function () {
      unsubscribe();
      if (context.remoteChatRefreshTimerRef.current) {
        clearTimeout(context.remoteChatRefreshTimerRef.current);
        context.remoteChatRefreshTimerRef.current = null;
      }
      context.remoteChangedChatIdsRef.current = new Set();
    };
  }, []);
}

export { useWbcLiveEventController }
