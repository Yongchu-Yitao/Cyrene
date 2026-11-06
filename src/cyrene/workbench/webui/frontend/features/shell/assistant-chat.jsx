import { WbcErrorNotice } from "../chat/messages.jsx"
import { useWbcComposerReserveHeight } from "../chat/conversation.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"

// A conversation surface, not a pane workspace. The shared engine owns streams
// across navigation; subscribing here must never replace its global hooks.
function AssistantChat({ sessionId, project, title, onOpenFile, permissionRequests = [], active = true, cache }) {
  var chatService = workbenchServices.chat();
  var model = chatService.Model;
  var engine = chatService.Runtimes;
  var [chat, setChat] = React.useState(function () { return cache && cache.details[sessionId] || null; });
  var [runtime, setRuntime] = React.useState(function () { return engine.get(sessionId); });
  var [error, setError] = React.useState("");
  var [answering, setAnswering] = React.useState(false);
  var [sending, setSending] = React.useState(false);
  var answeredIds = React.useRef(new Set());
  var answerInFlight = React.useRef(false);
  var mainRef = React.useRef(null);
  var scrollRef = React.useRef(null);
  useWbcComposerReserveHeight(mainRef, chat && chat.id, true);
  var stick = React.useRef(true);
  var refreshRef = React.useRef(function () {});
  var live = React.useRef(true);
  React.useEffect(function () {
    var cancelled = false, timer, inFlight = false, dirty = false;
    live.current = true;
    async function refresh() {
      if (cancelled) return;
      if (inFlight) { dirty = true; return; }
      inFlight = true;
      clearTimeout(timer);
      try {
        var next = await model.getChat(sessionId);
        if (!cancelled) {
          if (next && next.pendingQuestion && answeredIds.current.has(next.pendingQuestion.id)) {
            next = { ...next, pendingQuestion: null };
          }
          if (cache) cache.details[sessionId] = next;
          setChat(next);
          if (next && next.status === "running" && !next.pendingQuestion && !engine.get(sessionId)) {
            engine.reconnect(sessionId, model);
          }
        }
      } catch (err) { if (!cancelled) setError(err.message); }
      finally {
        inFlight = false;
        if (!cancelled && active) { timer = setTimeout(refresh, dirty ? 0 : 2000); dirty = false; }
      }
    }
    refreshRef.current = refresh;
    if (active) refresh();
    var previous = engine.get(sessionId);
    var unsubscribe = engine.subscribe(function (snapshot) {
      var next = snapshot[sessionId] || null;
      if (!cancelled) {
        setRuntime(next);
        if (previous && !next) refresh();
        previous = next;
      }
    });
    return function () { cancelled = true; live.current = false; clearTimeout(timer); unsubscribe(); };
  }, [sessionId, model, engine, active, cache]);
  var messages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  var candidate = runtime && runtime.pendingQuestion || chat && chat.pendingQuestion;
  var pending = candidate && !answeredIds.current.has(candidate.id) ? candidate : null;
  var running = !pending && (answering || sending || !!runtime || !!(chat && chat.status === "running"));
  var visible = messages;
  React.useEffect(function () {
    if (stick.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [chat, runtime, pending, permissionRequests]);
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
  return <section ref={mainRef} className="wbc-main assistant-chat" aria-label={title || "Cyrene 个人助理"}>
    {displayError && <WbcErrorNotice message={displayError} kind={chat ? "message" : "load"} />}
    <div className="wbc-thread-stage">
      <div className={"wbc-thread" + (visible.length || runtime ? " wbc-thread-messages" : "")} ref={scrollRef} onScroll={function (event) { var el = event.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
        {!chat ? <p role="status">正在加载对话…</p> : !visible.length && !runtime ? <section className="assistant-chat-welcome"><span className="assistant-chat-wordmark">Cyrene</span><h1>今天，有什么需要我帮忙？</h1><p>一起理清想法、推进任务，也可以从一个简单的问题开始。</p></section> : null}
        {React.createElement(chatService.Transcript, { messages: visible, runtime: runtime, chatId: sessionId, onOpenFile: onOpenFile })}
        {permissionRequests.map(function (task) {
          return <AssistantTaskPermission key={task.session_id + ":" + task.pending_question.id} task={task} />;
        })}
        {pending && React.createElement(chatService.QuestionPrompt, { pending: pending, onAnswer: answer, busy: answering })}
      </div>
    </div>
    {chat && React.createElement(chatService.Composer, { chat: { ...chat, pendingQuestion: pending }, project: project, runtime: runtime, running: running, onSend: send, onAnswer: answer, onGuidance: guidance, onInterrupt: interrupt, draftNamespace: "personal-assistant:", hideToolsButton: true, error: displayError, errorKind: "message" })}
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
