import { WbcVoice, wbcClearModelOutputForRetry, wbcErrorText, wbcMergeChronologicalMessages, wbcNormalizePermissionMode, wbcRetryTurnSelection } from "../../workbench-chat.jsx"
import { wbcIsLiveAgentRequest } from "./conversation.jsx"
import { settleChatListItem as wbcSettleChatListItem } from "./behavior.mjs"

function wbcHandleGuidance(context, message) {
  var chatId = context.activeChatIdRef.current;
  var text = String(message || "").trim();
  if (!chatId || !text || !context.runtimeEngine.isRunning(chatId)) return Promise.resolve(null);
  var requestId = "guide_" + Date.now();
  var optimistic = {
    id: "guidance_pending_" + requestId, role: "user", content: text,
    createdAt: new Date().toISOString(), guidance: true, optimistic: true,
    clientRequestId: requestId,
  };
  context.setError("");
  context.runtimeEngine.closeTimeline(chatId);
  context.runtimeEngine.recordUserMessage(chatId, optimistic);
  context.setActiveChat(function (previous) {
    if (!previous || previous.id !== chatId) return previous;
    return { ...previous, messages: wbcMergeChronologicalMessages(previous.messages || [], [optimistic]) };
  });
  return context.model.sendGuidance(chatId, text, requestId).then(function (response) {
    if (response && response.userMessage) {
      context.runtimeEngine.recordUserMessage(chatId, response.userMessage, optimistic.id);
      context.setActiveChat(function (previous) {
        if (!previous || previous.id !== chatId) return previous;
        return { ...previous, messages: wbcMergeChronologicalMessages(previous.messages || [], [response.userMessage]) };
      });
    }
    return response;
  }).catch(function (error) {
    context.setActiveChat(function (previous) {
      if (!previous || previous.id !== chatId) return previous;
      return { ...previous, messages: (previous.messages || []).filter(function (item) {
        return String(item && item.clientRequestId || "") !== requestId;
      }) };
    });
    if (error && error.code === "chat_not_running") {
      context.runtimeEngine.deferSend(chatId, { message: text }, context.model);
      return { deferred: true };
    }
    context.setErrorKind("message");
    context.setError(wbcErrorText(error));
    throw error;
  });
}

function wbcAnswerLiveAgentRequest(context, chatId, questionId, optionText, formAnswer, request) {
  var response = String(request.kind || "") === "permission.requested"
    ? { type: "option", optionId: String(optionText || "") }
    : (formAnswer
      ? { type: "form", form: optionText.values && typeof optionText.values === "object" ? optionText.values : {} }
      : { type: "text", text: String(optionText || "") });
  context.setChats(function (previous) {
    return previous.map(function (chat) {
      return String(chat && chat.id || "") === chatId
        ? { ...chat, pendingQuestion: null, status: "running", runStatus: "running" } : chat;
    });
  });
  context.setActiveChat(function (previous) {
    return previous && String(previous.id || "") === chatId
      ? { ...previous, pendingQuestion: null, status: "running" } : previous;
  });
  return context.model.answerAgentRequest(chatId, questionId, response).catch(function (error) {
    context.setActiveChat(function (previous) {
      return previous && String(previous.id || "") === chatId
        ? { ...previous, pendingQuestion: request, status: "idle" } : previous;
    });
    if (context.activeChatIdRef.current === chatId) context.setError(wbcErrorText(error));
    throw error;
  });
}

function wbcBeginAnswerRuntime(context, chatId, questionId, optionText) {
  var optimistic = {
    id: "answer_pending_" + Date.now(), role: "user", content: optionText,
    createdAt: new Date().toISOString(), answerToQuestionId: questionId, optimistic: true,
  };
  context.setChats(function (previous) {
    return previous.map(function (chat) {
      return String(chat && chat.id || "") === chatId
        ? { ...chat, pendingQuestion: null, status: "running", runStatus: "running" } : chat;
    });
  });
  var cached = context.chatCache.details[chatId];
  if (cached) context.chatCache.details[chatId] = {
    ...cached, pendingQuestion: null, status: "running",
    messages: wbcMergeChronologicalMessages(cached.messages || [], [optimistic]),
  };
  context.setActiveChat(function (previous) {
    if (!previous || String(previous.id || "") !== chatId) return previous;
    return {
      ...previous, pendingQuestion: null, status: "running",
      messages: wbcMergeChronologicalMessages(previous.messages || [], [optimistic]),
    };
  });
  var startedAt = Date.parse(String(optimistic.createdAt || "")) || Date.now();
  context.runtimeEngine.update(chatId, {
    chatId: chatId, text: "", progress: [], activities: [], activitySeq: 0,
    segments: [], notifications: [], userMessages: [optimistic],
    startedAt: startedAt, lastEventAt: startedAt, replying: true,
  });
  return optimistic;
}

function wbcHydrateAnsweredChat(context, chatId) {
  var hydrationSequence = context.beginChatHydration(chatId);
  return context.model.getChat(chatId).then(function (chat) {
    if (!context.isCurrentChatHydration(chatId, hydrationSequence)) return;
    context.chatCache.details[chatId] = chat;
    if (context.activeChatIdRef.current === chatId) context.setActiveChat(chat);
  });
}

function wbcAnswerRegularQuestion(context, chatId, questionId, optionText, resumeMode, summary) {
  wbcBeginAnswerRuntime(context, chatId, questionId, optionText);
  var permissionMode = context.activeChatIdRef.current === chatId
    && context.activeChat && context.activeChat.permissionMode
    ? context.activeChat.permissionMode : summary.permissionMode;
  var answerMode = wbcNormalizePermissionMode(resumeMode, permissionMode || "default");
  var answerSettled = false;
  return context.model.answerChat(chatId, questionId, optionText, { mode: answerMode }).then(function (result) {
    answerSettled = true;
    var status = result && result.interrupted
      ? "cancelled" : (result && result.awaitingUser ? "awaiting_user" : "completed");
    context.runtimeEngine.publishLifecycle(chatId, status, result || {});
    context.runtimeEngine.update(chatId, null);
    context.beginChatListRequest(String(context.projectIdRef.current || ""));
    context.setChats(function (previous) {
      return previous.map(function (chat) {
        return String(chat && chat.id || "") === chatId ? wbcSettleChatListItem(chat, status, result) : chat;
      });
    });
    return wbcHydrateAnsweredChat(context, chatId);
  }).then(function () {
    return context.refreshChats();
  }).catch(function (error) {
    if (!answerSettled) context.runtimeEngine.publishLifecycle(chatId, "failed", {});
    context.runtimeEngine.update(chatId, null);
    if (context.activeChatIdRef.current === chatId) context.setError(wbcErrorText(error));
    return wbcHydrateAnsweredChat(context, chatId).catch(function () {}).then(function () {
      return context.refreshChats().catch(function () {});
    }).then(function () { throw error; });
  });
}

function wbcAnswerQuestionForChat(context, chatId, questionId, optionText, resumeMode) {
  chatId = String(chatId || "");
  var formAnswer = optionText && typeof optionText === "object" && optionText.__agentForm === true;
  if (!chatId || !questionId || (!formAnswer && !optionText)) return Promise.resolve(null);
  WbcVoice.stop();
  var summary = context.chatsRef.current.find(function (chat) {
    return String(chat && chat.id || "") === chatId;
  }) || {};
  var detail = context.activeChatIdRef.current === chatId
    ? (context.activeChat || {}) : (context.chatCache.details[chatId] || {});
  var request = detail.pendingQuestion || summary.pendingQuestion || null;
  if (context.activeChatIdRef.current === chatId) context.setError("");
  if (wbcIsLiveAgentRequest(request)) {
    return wbcAnswerLiveAgentRequest(context, chatId, questionId, optionText, formAnswer, request);
  }
  return wbcAnswerRegularQuestion(context, chatId, questionId, optionText, resumeMode, summary);
}

function wbcHandleRetryMessage(context, messageId) {
  var chat = context.activeChat;
  if (!chat || context.runtimeEngine.isRunning(chat.id) || context.retryPendingChatIdRef.current) return;
  var chatId = String(chat.id || "");
  var targetMessageId = typeof messageId === "string" ? messageId : "";
  var selection = wbcRetryTurnSelection(chat, targetMessageId);
  var retryMode = wbcNormalizePermissionMode(chat.permissionMode, "auto");
  context.retryPendingChatIdRef.current = chatId;
  // A transcript request started before retry still contains the durable old
  // output. Invalidate it before the clear animation so its late response
  // cannot restore the reply that the user just removed.
  context.beginChatHydration(chatId);
  context.setError(""); context.setErrorKind("load");
  context.setRetryClearingMessageIds(selection.outputIds);
  function startRetryAfterClear() {
    var cached = context.chatCache.details[chatId];
    if (cached) context.chatCache.details[chatId] = wbcClearModelOutputForRetry(cached, targetMessageId);
    context.setActiveChat(function (previous) {
      return !previous || String(previous.id || "") !== chatId
        ? previous : wbcClearModelOutputForRetry(previous, targetMessageId);
    });
    context.setRetryClearingMessageIds([]);
    context.retryPendingChatIdRef.current = "";
    context.runtimeEngine.start(chatId, {
      retry: true,
      mode: retryMode,
      retryTruncateAfterMessageId: selection.truncateAfterMessageId,
    }, context.model);
  }
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!selection.outputIds.length || reduceMotion) { startRetryAfterClear(); return; }
  context.retryClearCommitRef.current = startRetryAfterClear;
}

function wbcHandleEditMessage(context, messageId, newContent) {
  var chat = context.activeChat;
  if (!chat || context.runtimeEngine.isRunning(chat.id) || !messageId || !newContent) return;
  context.setError("");
  var replayMode = wbcNormalizePermissionMode(chat.permissionMode, "auto");
  context.model.forkChat(chat.id, messageId, newContent).then(function (newChat) {
    newChat = { ...newChat, permissionMode: replayMode };
    context.setChats(function (previous) { return [newChat].concat(previous); });
    context.skipNextHydrationChatIdRef.current = newChat.id;
    context.selectChat(newChat.id);
    context.setActiveChat(newChat);
    return context.runtimeEngine.start(newChat.id, { retry: true, forkReplay: true, mode: replayMode }, context.model);
  }).catch(function (error) { context.setError(wbcErrorText(error)); });
}

function wbcHandleCreateChat(context) {
  return context.model.createChat(context.projectId).then(function (chat) {
    context.setChats(function (previous) { return [chat].concat(previous); });
    context.skipNextHydrationChatIdRef.current = chat.id;
    context.selectChat(chat.id);
    context.setActiveChat(chat);
    return chat;
  }).catch(function (error) { context.setError(wbcErrorText(error)); });
}

function wbcHandleRenameChat(context, chatId, title) {
  if (!chatId) return Promise.resolve();
  return context.model.renameChat(chatId, title).then(function (chat) {
    context.setActiveChat(function (previous) {
      return previous && previous.id === chat.id ? { ...previous, title: chat.title } : previous;
    });
    context.setChats(function (previous) {
      return previous.map(function (item) { return item.id === chat.id ? { ...item, title: chat.title } : item; });
    });
    return chat;
  });
}

function wbcHandleRename(context, title) {
  if (!context.activeChat) return Promise.resolve();
  return wbcHandleRenameChat(context, context.activeChat.id, title);
}

function wbcOpenQuickRename(context) {
  if (!context.activeChat) return;
  context.closePageContextMenu();
  context.setQuickRenameChat(context.activeChat);
}

export {
  wbcAnswerQuestionForChat, wbcHandleCreateChat, wbcHandleEditMessage,
  wbcHandleGuidance, wbcHandleRename, wbcHandleRenameChat, wbcHandleRetryMessage,
  wbcOpenQuickRename,
}
