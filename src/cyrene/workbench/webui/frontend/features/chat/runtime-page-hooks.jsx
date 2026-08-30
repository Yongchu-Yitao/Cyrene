import { WbcVoice, useWbcEffect, wbcAgentEventPayload, wbcConfirmOptimisticMessage, wbcMergeChronologicalMessages, wbcMergeSavedAssistantMessages, wbcPreserveLiveTimelineAnchors, wbcT, wbcTruncateMessagesAfterUser } from "../../workbench-chat.jsx"
import { wbcVoiceQuestionText } from "./conversation.jsx"
import { settleChatListItem as wbcSettleChatListItem } from "./behavior.mjs"

function wbcMergeChatSummary(chat, summary, runStatus) {
  if (!chat || !summary || typeof summary !== "object") return chat;
  var messages = Array.isArray(chat.messages) ? chat.messages : null;
  var merged = { ...chat, ...summary };
  if (messages) merged.messages = messages;
  if (runStatus) {
    merged.runStatus = runStatus;
    merged.status = runStatus === "running" ? "running" : "idle";
  }
  return merged;
}

function wbcRuntimeUserMessage(context, chatId, userMessage) {
  context.setActiveChat(function (previous) {
    if (!previous || previous.id !== chatId) return previous;
    return { ...previous, messages: wbcMergeChronologicalMessages(previous.messages || [], [userMessage]) };
  });
}

function wbcRuntimeUserMessageConfirmed(context, chatId, confirmation) {
  context.setActiveChat(function (previous) {
    if (!previous || previous.id !== chatId) return previous;
    var userMessage = confirmation && confirmation.userMessage;
    if (!userMessage) return previous;
    var optimisticId = String(confirmation.optimisticId || "");
    var messages = previous.messages || [];
    if (optimisticId) {
      for (var index = 0; index < messages.length; index += 1) {
        if (String(messages[index] && messages[index].id || "") !== optimisticId) continue;
        var confirmed = messages.slice();
        confirmed[index] = wbcConfirmOptimisticMessage(messages[index], userMessage);
        return { ...previous, messages: confirmed };
      }
    }
    return { ...previous, messages: wbcMergeChronologicalMessages(messages, [userMessage]) };
  });
}

function wbcRuntimeRetryTruncate(context, chatId, truncateAfterMessageId) {
  context.beginChatHydration(chatId);
  context.setActiveChat(function (previous) {
    if (!previous || previous.id !== chatId) return previous;
    var list = previous.messages || [];
    var nextMessages = wbcTruncateMessagesAfterUser(list, truncateAfterMessageId);
    return nextMessages === list ? previous : { ...previous, messages: nextMessages };
  });
}

function wbcRuntimeReplyStream(context, chatId, event) {
  if (String(context.activeChatIdRef.current || "") !== String(chatId || "")) return;
  var payload = event && typeof event === "object" ? event : {};
  WbcVoice.autoStream(
    String(payload.text || ""), "auto-chat:" + String(chatId || ""),
    payload.done === true, payload.start === true
  );
}

function wbcRuntimeIntermediateMessage(context, chatId, message) {
  if (String(context.activeChatIdRef.current || "") !== String(chatId || "")) return;
  var item = message && typeof message === "object" ? message : {};
  WbcVoice.autoSpeak(
    String(item.content || ""), "auto-chat:" + String(chatId || ""),
    "intermediate:" + String(item.id || item.content || "")
  );
}

function wbcRuntimeAssistantSaved(context, chatId, assistantMessages, terminalEvent) {
  // The terminal stream event is newer than every transcript request started
  // while the run was active. Reject those responses before applying it.
  context.beginChatHydration(chatId);
  if (String(context.activeChatIdRef.current || "") === String(chatId || "")) {
    var messages = Array.isArray(assistantMessages) ? assistantMessages : [];
    var terminalMessage = null;
    for (var index = messages.length - 1; index >= 0; index -= 1) {
      var candidate = messages[index];
      if (candidate && candidate.role === "assistant" && String(candidate.content || "").trim()) {
        terminalMessage = candidate;
        break;
      }
    }
    if (terminalMessage) {
      WbcVoice.autoSpeakFinal(
        String(terminalMessage.content || ""), "auto-chat:" + String(chatId || ""),
        "final:" + String(terminalMessage.id || terminalMessage.content || "")
      );
    }
  }
  var terminalSummary = terminalEvent && terminalEvent.chatSummary;
  function mergeTerminal(chat) {
    return wbcMergeSavedAssistantMessages(
      wbcMergeChatSummary(chat, terminalSummary, "completed"), assistantMessages
    );
  }
  var cachedChat = context.chatCache.details[chatId] || null;
  if (cachedChat) context.chatCache.details[chatId] = mergeTerminal(cachedChat);
  context.setActiveChat(function (previous) {
    return !previous || previous.id !== chatId ? previous : mergeTerminal(previous);
  });
  var currentProjectId = String(context.projectIdRef.current || "");
  context.beginChatListRequest(currentProjectId);
  context.setChats(function (previous) {
    return previous.map(function (chat) {
      if (String(chat && chat.id || "") !== String(chatId || "")) return chat;
      var projected = wbcMergeChatSummary(chat, terminalSummary, "completed");
      return wbcSettleChatListItem(projected, "completed", terminalEvent);
    });
  });
}

function wbcRuntimeAgentArtifact(context, chatId, artifactEvent) {
  var attachment = artifactEvent && artifactEvent.attachment;
  if (!attachment) return;
  context.setActiveChat(function (previous) {
    if (!previous || previous.id !== chatId) return previous;
    var live = Array.isArray(previous.liveAgentArtifacts) ? previous.liveAgentArtifacts.slice() : [];
    var key = String(artifactEvent.artifactId || attachment.id || attachment.url || "");
    var index = live.findIndex(function (item) {
      return String(item && (item.artifactId || item.id || item.url) || "") === key;
    });
    var next = { ...attachment, artifactId: key };
    if (index >= 0) live[index] = { ...live[index], ...next };
    else live.push(next);
    return { ...previous, liveAgentArtifacts: live };
  });
}

function wbcRuntimeAgentUsage(context, chatId, payload) {
  context.setActiveChat(function (previous) {
    if (!previous || previous.id !== chatId) return previous;
    var usage = { ...(previous.usage || {}) };
    [["inputTokens", "prompt_tokens"], ["outputTokens", "completion_tokens"], ["totalTokens", "total_tokens"], ["used", "total_tokens"]].forEach(function (pair) {
      var value = Number(payload && payload[pair[0]] || 0);
      if (value > 0) usage[pair[1]] = value;
    });
    return { ...previous, usage: usage, liveAgentContextUsage: payload || {} };
  });
}

function wbcRuntimeAgentSession(context, chatId, session) {
  context.setActiveChat(function (previous) {
    if (!previous || previous.id !== chatId) return previous;
    var next = { ...previous };
    if (session.sessionId) next.agent = { ...(previous.agent || {}), externalSessionId: session.sessionId, runtimeState: "ready" };
    if (session.updateKind === "available_commands_update" || session.commands.length) next.agentCommands = session.commands;
    if (session.mode != null) next.agentMode = session.mode;
    if (session.plan) next.activePlan = session.plan;
    if (session.configOption || session.configOptions.length) {
      var options = Array.isArray(previous.agentConfigOptions) ? previous.agentConfigOptions.slice() : [];
      var incomingOptions = session.configOptions.concat(session.configOption ? [session.configOption] : []);
      incomingOptions.forEach(function (incoming) {
        var id = String(incoming && incoming.id || "");
        var index = options.findIndex(function (item) { return String(item && item.id || "") === id; });
        if (id && index >= 0) options[index] = { ...options[index], ...incoming };
        else if (id) options.push(incoming);
      });
      next.agentConfigOptions = options;
    }
    return next;
  });
}

function wbcRuntimeRequestResolved(context, chatId, event) {
  var payload = wbcAgentEventPayload(event);
  var requestId = String(payload.requestId || payload.request_id || "");
  context.setActiveChat(function (previous) {
    if (!previous || previous.id !== chatId || !previous.pendingQuestion) return previous;
    if (requestId && String(previous.pendingQuestion.id || "") !== requestId) return previous;
    return { ...previous, pendingQuestion: null, status: "running" };
  });
}

function wbcRuntimeAwaitingUser(context, chatId, pendingQuestion) {
  // Awaiting-user is also an authoritative transcript transition.
  context.beginChatHydration(chatId);
  if (String(context.activeChatIdRef.current || "") === String(chatId || "") && pendingQuestion) {
    WbcVoice.autoSpeak(
      wbcVoiceQuestionText(pendingQuestion), "auto-chat:" + String(chatId || ""),
      "question:" + String(pendingQuestion.id || pendingQuestion.text || "")
    );
  }
  context.setActiveChat(function (previous) {
    if (!previous || previous.id !== chatId) return previous;
    return { ...previous, status: "idle", pendingQuestion: pendingQuestion || null };
  });
}

function wbcRuntimeInterrupted(context, chatId) {
  context.setActiveChat(function (previous) {
    return !previous || previous.id !== chatId ? previous : { ...previous, status: "idle" };
  });
  if (String(context.activeChatIdRef.current || "") === String(chatId || "")) WbcVoice.stop();
  context.refreshChats();
}

function wbcRuntimeError(context, chatId, error, failureState) {
  var terminal = !!(failureState && failureState.terminal);
  var budgetError = String(error && error.code || "").startsWith("budget_");
  if (budgetError && String(context.activeChatIdRef.current || "") === String(chatId || "")) {
    context.setErrorKind("message");
    context.setError(error);
  }
  if (terminal) {
    var cachedChat = context.chatCache.details[chatId];
    if (cachedChat) context.chatCache.details[chatId] = { ...cachedChat, status: "idle", runStatus: "failed" };
    context.setActiveChat(function (previous) {
      return !previous || String(previous.id || "") !== String(chatId || "")
        ? previous : { ...previous, status: "idle", runStatus: "failed" };
    });
    var currentProjectId = String(context.projectIdRef.current || "");
    context.beginChatListRequest(currentProjectId);
    context.setChats(function (previous) {
      return previous.map(function (chat) {
        return String(chat && chat.id || "") === String(chatId || "")
          ? wbcSettleChatListItem(chat, "failed", error) : chat;
      });
    });
  }
  if (terminal && String(context.activeChatIdRef.current || "") === String(chatId || "")) {
    context.setErrorKind("message");
    context.setError(error || wbcT("workbenchChat.agentError.failed", "Agent run failed"));
    WbcVoice.stop();
  }
}

function wbcRuntimeResync(context, chatId) {
  var hydrationSequence = context.beginChatHydration(chatId);
  context.model.getChat(chatId).then(function (chat) {
    if (!context.isCurrentChatHydration(chatId, hydrationSequence)) return;
    var runtime = context.runtimeEngine.get(chatId);
    var cachedChat = context.chatCache.details[chatId] || null;
    context.chatCache.details[chatId] = wbcPreserveLiveTimelineAnchors(cachedChat, chat, runtime);
    if (context.activeChatIdRef.current === chatId) {
      context.setActiveChat(function (previous) {
        return wbcPreserveLiveTimelineAnchors(previous, chat, runtime);
      });
    }
  }).catch(function () {});
  context.refreshChats();
}

function wbcRuntimePageHooks(context) {
  return {
    onUserMessage: function (chatId, value) { wbcRuntimeUserMessage(context, chatId, value); },
    onUserMessageConfirmed: function (chatId, value) { wbcRuntimeUserMessageConfirmed(context, chatId, value); },
    onRetryTruncate: function (chatId, value) { wbcRuntimeRetryTruncate(context, chatId, value); },
    onReplyStream: function (chatId, value) { wbcRuntimeReplyStream(context, chatId, value); },
    onIntermediateMessage: function (chatId, value) { wbcRuntimeIntermediateMessage(context, chatId, value); },
    onAssistantSaved: function (chatId, messages, event) { wbcRuntimeAssistantSaved(context, chatId, messages, event); },
    onAgentArtifact: function (chatId, value) { wbcRuntimeAgentArtifact(context, chatId, value); },
    onAgentUsageUpdated: function (chatId, value) { wbcRuntimeAgentUsage(context, chatId, value); },
    onAgentSessionUpdated: function (chatId, value) { wbcRuntimeAgentSession(context, chatId, value); },
    onAgentRequestResolved: function (chatId, value) { wbcRuntimeRequestResolved(context, chatId, value); },
    onAwaitingUser: function (chatId, value) { wbcRuntimeAwaitingUser(context, chatId, value); },
    onInterrupted: function (chatId) { wbcRuntimeInterrupted(context, chatId); },
    onError: function (chatId, error, state) { wbcRuntimeError(context, chatId, error, state); },
    onResync: function (chatId) { wbcRuntimeResync(context, chatId); },
  };
}

function useWbcRuntimePageHooks(context) {
  useWbcEffect(function () {
    context.runtimeEngine.setHooks(wbcRuntimePageHooks(context));
    return function () { context.runtimeEngine.setHooks(null); };
  });
}

export { useWbcRuntimePageHooks, wbcMergeChatSummary }
