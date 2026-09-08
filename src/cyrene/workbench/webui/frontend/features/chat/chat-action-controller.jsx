import { beginAnswerProjection, guidanceProjection, settleAnswerProjection, hydrateAnswerProjection } from "./answer-projection.mjs"
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
  guidanceProjection(context, chatId, { type: "begin", message: optimistic }, wbcMergeChronologicalMessages);
  return context.model.sendGuidance(chatId, text, requestId).then(function (response) {
    if (response && response.userMessage) {
      guidanceProjection(context, chatId, { type: "confirm", message: response.userMessage, optimisticId: optimistic.id }, wbcMergeChronologicalMessages);
    }
    return response;
  }).catch(function (error) {
    guidanceProjection(context, chatId, { type: "reject", requestId: requestId }, wbcMergeChronologicalMessages);
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
  beginAnswerProjection({ setChats: context.setChats, setActiveChat: context.setActiveChat }, chatId);
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
  beginAnswerProjection({
    setChats: context.setChats, setActiveChat: context.setActiveChat, cache: context.chatCache.details,
  }, chatId, optimistic, wbcMergeChronologicalMessages);
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
    hydrateAnswerProjection({
      cache: context.chatCache.details, setActiveChat: context.setActiveChat,
      activeChatId: function () { return context.activeChatIdRef.current; },
      isCurrent: function () { return context.isCurrentChatHydration(chatId, hydrationSequence); },
    }, chatId, chat);
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
    settleAnswerProjection(context, chatId, status, result, wbcSettleChatListItem, function () {
      context.beginChatListRequest(String(context.projectIdRef.current || ""));
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
  if (!chat) return;
  if (context.runtimeEngine.isRunning(chat.id)) {
    workbenchServices.feedback().showToast(wbcT("workbenchChat.error.retryRunning", "Wait for the run to end, or stop it before retrying."), "info");
    return;
  }
  if (context.retryPendingChatIdRef.current) {
    workbenchServices.feedback().showToast(wbcT("workbenchChat.error.retryPending", "Retry is already being prepared."), "info");
    return;
  }
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
