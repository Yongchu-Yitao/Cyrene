// The list and detail intentionally carry different fields. Keep their update
// order and identity rules centralized without fetching or cloning transcripts.
export function answerRunningSummary(chat, chatId) {
  return String(chat && chat.id || '') === chatId
    ? { ...chat, pendingQuestion: null, status: 'running', runStatus: 'running' } : chat;
}

export function answerRunningDetail(chat, optimistic, mergeMessages) {
  return optimistic ? {
    ...chat, pendingQuestion: null, status: 'running',
    messages: mergeMessages(chat.messages || [], [optimistic]),
  } : { ...chat, pendingQuestion: null, status: 'running' };
}

export function beginAnswerProjection({ setChats, setActiveChat, cache }, chatId, optimistic, mergeMessages) {
  setChats(previous => previous.map(chat => answerRunningSummary(chat, chatId)));
  if (cache && cache[chatId]) cache[chatId] = answerRunningDetail(cache[chatId], optimistic, mergeMessages);
  setActiveChat(previous => previous && String(previous.id || '') === chatId
    ? answerRunningDetail(previous, optimistic, mergeMessages) : previous);
}

// One domain entry for answer/guidance mutations. Runtime remains authoritative
// for in-flight messages; durable detail hydration stays lazy and sequenced.
export function guidanceProjection({ runtimeEngine, setActiveChat }, chatId, action, mergeMessages) {
  if (action.type === 'begin') {
    runtimeEngine.closeTimeline(chatId);
    runtimeEngine.recordUserMessage(chatId, action.message);
  } else if (action.type === 'confirm') {
    runtimeEngine.recordUserMessage(chatId, action.message, action.optimisticId);
  }
  setActiveChat(previous => {
    if (!previous || previous.id !== chatId) return previous;
    return { ...previous, messages: action.type === 'reject'
      ? (previous.messages || []).filter(item => String(item && item.clientRequestId || '') !== action.requestId)
      : mergeMessages(previous.messages || [], [action.message]) };
  });
}

export function settleAnswerProjection({ runtimeEngine, setChats }, chatId, status, result, settleSummary, invalidateList) {
  runtimeEngine.publishLifecycle(chatId, status, result || {});
  runtimeEngine.update(chatId, null);
  invalidateList();
  setChats(previous => previous.map(chat => String(chat && chat.id || '') === chatId
    ? settleSummary(chat, status, result) : chat));
}

export function hydrateAnswerProjection({ cache, setActiveChat, activeChatId, isCurrent }, chatId, chat) {
  if (!isCurrent()) return;
  cache[chatId] = chat;
  if (activeChatId() === chatId) setActiveChat(chat);
}
