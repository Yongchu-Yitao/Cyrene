import { AssistantHomeTranscript } from "../chat/assistant-home-transcript.jsx"
import { WbcErrorNotice } from "../chat/messages.jsx"
import { WbcScrollToBottomButton, useWbcComposerReserveHeight, wbcScrollThreadToBottom, wbcShouldStickToConversationBottom } from "../chat/conversation.jsx"
import { wbcMergeChatSummary } from "../chat/runtime-page-hooks.jsx"
import { wbcMergeSavedAssistantMessages, wbcReconcileLiveUserMessages, wbcTruncateMessagesAfterUser } from "../chat/runtime-timeline.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"
import { AssistantGripLogo } from "./assistant-grip-logo.jsx"
import { CyreneIcon } from "./cyrene-icon.jsx"

const EMPTY_PERMISSION_REQUESTS = [];

function assistantSavedChat(chat, sessionId, messages, event, runtime) {
  var current = wbcMergeChatSummary(chat || { id: sessionId }, event.chatSummary, "completed");
  var history = current.messages || [];
  if (event.retry) history = wbcTruncateMessagesAfterUser(history, event.truncateAfterMessageId);
  var users = (runtime?.userMessages || []).filter(message => !message.optimistic);
  if (event.userMessage) users.push(event.userMessage);
  history = wbcReconcileLiveUserMessages(history, users);
  var saved = messages.filter(message => message && message.id);
  // Some saved events omit message details. The terminal timeline is already
  // available in that case, until the next detail request supplies the history.
  var records = saved.length ? saved : runtime?.timeline?.messages;
  return wbcMergeSavedAssistantMessages({
    ...current, messages: history, pendingQuestion: null, runStatus: "completed",
  }, records);
}

function useAssistantChatSync({ sessionId, model, engine, active, cache, answeredIds, stick }) {
  var [chat, setChat] = React.useState(function () { return cache && cache.details[sessionId] || null; });
  var [runtime, setRuntime] = React.useState(function () { return engine.get(sessionId); });
  var [refreshError, setRefreshError] = React.useState("");
  var refreshRef = React.useRef(function () {});
  var live = React.useRef(true);
  React.useEffect(function () {
    var cancelled = false, timer, inFlight = false, dirty = false, savedRevision = 0;
    live.current = true;
    function applyRefresh(next) {
      if (next?.pendingQuestion && answeredIds.current.has(next.pendingQuestion.id)) {
        next = { ...next, pendingQuestion: null };
      }
      if (cache) cache.details[sessionId] = next;
      setChat(next);
      setRefreshError("");
      if ((next?.runStatus || next?.status) === "running" && !next.pendingQuestion && !engine.get(sessionId)) {
        engine.reconnect(sessionId, model);
      }
    }
    async function refresh() {
      if (cancelled) return;
      if (inFlight) { dirty = true; return; }
      inFlight = true;
      clearTimeout(timer);
      var requestRevision = savedRevision;
      try {
        var next = await model.getChat(sessionId);
        if (!cancelled && requestRevision === savedRevision) applyRefresh(next);
      } catch (err) {
        if (!cancelled && requestRevision === savedRevision) {
          setRefreshError("连接中断，正在尝试重新连接。已显示的内容仍然保留。");
        }
      }
      finally {
        inFlight = false;
        if (!cancelled && active) { timer = setTimeout(refresh, dirty ? 0 : 2000); dirty = false; }
      }
    }
    refreshRef.current = refresh;
    function cardSubmitted(event) {
      if (event.detail?.session_id === sessionId && active) { stick.current = true; refresh(); }
    }
    window.addEventListener('cyrene:card-submitted', cardSubmitted);
    if (active) refresh();
    var unsubscribeSaved = engine.subscribeSaved(sessionId, function (messages, event, runtime) {
      // This runs before the engine removes the live transcript. A poll that
      // started before this write can still report the run as active.
      savedRevision++;
      function merge(current) { return assistantSavedChat(current, sessionId, messages, event, runtime); }
      if (cache) cache.details[sessionId] = merge(cache.details[sessionId]);
      setChat(merge);
    });
    var previous = engine.get(sessionId);
    var unsubscribe = engine.subscribe(function (snapshot) {
      var next = snapshot[sessionId] || null;
      if (!cancelled) {
        setRuntime(next);
        if (previous && !next) refresh();
        previous = next;
      }
    });
    return function () {
      cancelled = true; live.current = false; clearTimeout(timer);
      unsubscribeSaved(); unsubscribe();
      window.removeEventListener('cyrene:card-submitted', cardSubmitted);
    };
  }, [sessionId, model, engine, active, cache, answeredIds, stick]);
  return { chat, setChat, runtime, refreshError, refreshRef, live };
}

// A conversation surface, not a pane workspace. The shared engine owns streams
// across navigation; subscribing here must never replace its global hooks.
function assistantGripVisible(personalAssistant, chat, visible, runtime) {
  return personalAssistant && chat && (visible.length > 0 || runtime);
}

function assistantWelcome(chat, visible, runtime) {
  return !chat ? <p role="status">正在加载对话…</p> : !visible.length && !runtime ? <AssistantWelcome /> : null;
}

function AssistantWelcome() {
  return <section className="assistant-chat-welcome">
          <div className="assistant-chat-brand">
            <div className="assistant-chat-avatar"><CyreneIcon /></div>
            <span>Cyrene</span>
          </div>
          <h1>需要我做什么？</h1>
        </section>;
}

function AssistantChat({ sessionId, project, title, onOpenFile, permissionRequests = EMPTY_PERMISSION_REQUESTS, active = true, personalAssistant = false, cache }) {
  var chatService = workbenchServices.chat();
  var model = chatService.Model;
  var engine = chatService.Runtimes;
  var [error, setError] = React.useState("");
  var [answering, setAnswering] = React.useState(false);
  var [sending, setSending] = React.useState(false);
  var [showScrollToBottom, setShowScrollToBottom] = React.useState(false);
  var answeredIds = React.useRef(new Set());
  var answerInFlight = React.useRef(false);
  var mainRef = React.useRef(null);
  var scrollRef = React.useRef(null);
  var stick = React.useRef(true);
  var lastScrollTop = React.useRef(null);
  var { chat, setChat, runtime, refreshError, refreshRef, live } = useAssistantChatSync({
    sessionId, model, engine, active, cache, answeredIds, stick,
  });
  useWbcComposerReserveHeight(mainRef, chat?.id, true);
  var messages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  var candidate = runtime && runtime.pendingQuestion || chat && chat.pendingQuestion;
  var pending = candidate && !answeredIds.current.has(candidate.id) ? candidate : null;
  var running = !pending && (answering || sending || !!runtime || !!(chat && (chat.runStatus || chat.status) === "running"));
  var visible = messages;
  React.useEffect(function () {
    var thread = scrollRef.current;
    if (stick.current && thread) {
      thread.scrollTop = thread.scrollHeight;
      lastScrollTop.current = thread.scrollTop;
      setShowScrollToBottom(false);
    }
  }, [chat, runtime, pending, permissionRequests]);

  function onScroll(event) {
    var thread = event.currentTarget;
    stick.current = wbcShouldStickToConversationBottom(
      stick.current, lastScrollTop.current, thread.scrollTop, thread.scrollHeight, thread.clientHeight
    );
    lastScrollTop.current = thread.scrollTop;
    setShowScrollToBottom(!stick.current);
  }

  function scrollToBottom() {
    var thread = scrollRef.current;
    if (!thread) return;
    stick.current = true;
    lastScrollTop.current = thread.scrollTop;
    setShowScrollToBottom(false);
    wbcScrollThreadToBottom(thread);
  }

  function fail(err) { if (live.current) setError(err && err.message || "操作失败，请重试。"); }
  async function send(input) {
    if (running || !chat) return;
    if (pending && input && input.message && !(input.attachments || []).length) {
      return answer(pending.id, input.message, input.mode);
    }
    setError(""); setSending(true); stick.current = true;
    var requestId = "assistant_ui_" + Date.now() + "_" + Math.random().toString(36).slice(2);
    try { await engine.start(sessionId, Object.assign({}, input, {clientRequestId: requestId}), model); }
    catch (err) { fail(err); }
    finally {
      if (live.current) { setSending(false); refreshRef.current(); }
    }
  }
  async function interrupt() {
    try { await engine.interrupt(sessionId, model); }
    catch (err) { fail(err); }
    refreshRef.current();
  }
  async function guidance(text) {
    try { return await model.sendGuidance(sessionId, text, "assistant_guide_" + Date.now()); }
    catch (err) { fail(err); throw err; }
  }
  async function answer(questionId, value, mode) {
    if (answerInFlight.current || answeredIds.current.has(questionId)) return;
    answerInFlight.current = true;
    answeredIds.current.add(questionId);
    setAnswering(true); setError(""); stick.current = true;
    setChat(function (current) { return current ? { ...current, pendingQuestion: null, status: "running" } : current; });
    try {
      await engine.start(sessionId, { message: value, questionId: questionId,
        mode: mode || chat && chat.permissionMode || "default" }, model);
      if (engine.getFailure && engine.getFailure(sessionId)) answeredIds.current.delete(questionId);
    }
    catch (err) { answeredIds.current.delete(questionId); fail(err); }
    finally {
      answerInFlight.current = false;
      if (live.current) { setAnswering(false); refreshRef.current(); }
    }
  }

  var failure = engine.getFailure && engine.getFailure(sessionId);
  var displayError = error || (failure && failure.message) || "";
  var showGrip = assistantGripVisible(personalAssistant, chat, visible, runtime);
  return <section ref={mainRef} className="wbc-main assistant-chat" data-cyrene-personal-composer={personalAssistant && active ? "true" : undefined} aria-label={title || "Cyrene 个人助理"}>
    {showGrip ? <AssistantGripLogo surfaceRef={mainRef} sessionId={sessionId} /> : null}
    {refreshError && <p className="wbc-card-notice" role="status">{refreshError}</p>}
    {displayError && <WbcErrorNotice message={displayError} kind={chat ? "message" : "load"} />}
    <div className="wbc-thread-stage">
      <div className={"wbc-thread" + (visible.length || runtime ? " wbc-thread-messages" : "")} ref={scrollRef} onScroll={onScroll}>
        {assistantWelcome(chat, visible, runtime)}
        {personalAssistant && chat
          ? <AssistantHomeTranscript key={sessionId} Transcript={chatService.Transcript} messages={visible} runtime={runtime} sessionId={sessionId} active={active} onOpenFile={onOpenFile} />
          : React.createElement(chatService.Transcript, { messages: visible, runtime: runtime, chatId: sessionId, onOpenFile: onOpenFile })}
        {permissionRequests.map(function (task) {
          return <AssistantTaskPermission key={task.session_id + ":" + task.pending_question.id} task={task} />;
        })}
        {pending && React.createElement(chatService.QuestionPrompt, { pending: pending, onAnswer: answer, busy: answering })}
      </div>
    </div>
    {chat && React.createElement(chatService.Composer, {
      chat: { ...chat, pendingQuestion: pending }, project, runtime, running,
      onSend: send, onAnswer: answer, onGuidance: guidance, onInterrupt: interrupt,
      draftNamespace: "personal-assistant:", hideToolsButton: true, error: displayError, errorKind: "message",
      topOverlay: showScrollToBottom ? <WbcScrollToBottomButton onClick={scrollToBottom} /> : null,
    })}
  </section>;
}
// A human answers the original task request. Never route this through the Agent's
// session_answer_question tool or change the main assistant's permission mode.
function AssistantTaskPermission({ task }) {
  var service = workbenchServices.chat();
  var [state, setState] = React.useState("pending");
  var [error, setError] = React.useState("");
  var inFlight = React.useRef(false);
  var live = React.useRef(true);
  React.useEffect(function () { live.current = true; return function () { live.current = false; }; }, []);
  async function answer(questionId, value, mode) {
    if (inFlight.current || questionId !== task.pending_question.id) return;
    inFlight.current = true;
    setState("submitting"); setError("");
    try {
      await service.Runtimes.start(task.session_id, {questionId: questionId, message: value, mode: mode}, service.Model);
      var failure = service.Runtimes.getFailure && service.Runtimes.getFailure(task.session_id);
      if (failure) throw new Error(failure.message || "授权提交失败，请重试。");
      if (live.current) setState("answered");
    } catch (err) {
      inFlight.current = false;
      if (live.current) { setState("pending"); setError(err.message || "授权提交失败，请重试。"); }
    }
  }
  if (state === "answered") return null;
  return <section className="assistant-task-permission" aria-label={"任务授权：" + task.title}>
    <p>{task.title}</p>
    {error && <p role="alert">{error}</p>}
    {state === "submitting" ? <p role="status">正在提交授权决定…</p>
      : React.createElement(service.QuestionPrompt, {pending: task.pending_question, onAnswer: answer, busy: false})}
  </section>;
}
export { AssistantChat, AssistantTaskPermission }
