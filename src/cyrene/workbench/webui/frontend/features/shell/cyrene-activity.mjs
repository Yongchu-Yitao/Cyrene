const terminalState = { failed: 'error', error: 'error', cancelled: 'stopped', stopped: 'stopped', interrupted: 'stopped' };

export function cyreneActivityState({ runtime, chat, failure, completion = false }) {
  if (runtime) {
    if (runtime.pendingQuestion) return 'waiting';
    if (runtime.reconnecting) return 'reconnecting';
    const activities = (runtime.activities || []).filter(item => !item.timelineClosed);
    const messages = (runtime.timeline?.messages || []).filter(item => item.status === 'running');
    const tools = runtime.timeline ? messages.flatMap(item => item.trace || [])
      : [...(runtime.progress || []), ...activities.flatMap(item => item.progress || [])];
    if (tools.some(item => (item.kind === 'tool' || item.toolCallId) && item.status === 'running')) return 'tool';
    if (runtime.timeline) return messages.some(item => item.role === 'assistant' && !item.activityCard && !item.notificationCard && !item.questionPrompt) && !runtime.finalizing ? 'replying' : 'thinking';
    if (runtime.replying && !runtime.streamDone && !runtime.finalizing) return 'replying';
    return 'thinking';
  }
  if (failure || terminalState[chat?.status] === 'error') return 'error';
  if (chat?.pendingQuestion || chat?.status === 'awaiting_user') return 'waiting';
  if (terminalState[chat?.status]) return terminalState[chat.status];
  if (chat?.status === 'running') return 'thinking';
  return completion ? 'done' : 'idle';
}

// One observer belongs to the persistent dock, never to the transcript hooks.
// Derive a small semantic state before notifying React; token deltas do not
// rerender the shell or restart CSS animations.
export function observeCyreneActivity({ sessionId, engine, model, onState, target = window }) {
  let disposed = false, chat = null, terminal = '', completion = false;
  let lastState = '', generation = '', revision = 0, pending = false;
  let pollTimer, completionTimer;
  const getRuntime = () => engine.get(sessionId);
  const emit = state => { if (!disposed && state !== lastState) { lastState = state; onState(state); } };
  function render() {
    const runtime = getRuntime();
    const nextGeneration = runtime && String(runtime.streamGeneration || runtime.startedAt || 'active');
    if (nextGeneration && nextGeneration !== generation) {
      generation = nextGeneration; revision++; terminal = ''; completion = false;
      target.clearTimeout(completionTimer);
      chat = { ...chat, status: 'running', pendingQuestion: null };
    }
    emit(cyreneActivityState({ runtime: terminal ? null : runtime, chat: terminal ? { ...chat, status: terminal, pendingQuestion: null } : chat,
      failure: !runtime && engine.getFailure?.(sessionId), completion }));
  }
  function complete() {
    completion = true;
    target.clearTimeout(completionTimer);
    completionTimer = target.setTimeout(() => { completion = false; render(); }, 2400);
  }
  async function refresh() {
    if (disposed || pending) return;
    pending = true;
    const requestRevision = revision;
    try {
      const next = await model.getChat(sessionId);
      if (!disposed && requestRevision === revision) {
        const wasRunning = chat?.status === 'running';
        if (!wasRunning && next?.status === 'running') { terminal = ''; completion = false; }
        chat = next;
        if (wasRunning && !getRuntime() && ['idle', 'completed'].includes(next?.status) && !next.pendingQuestion) complete();
        render();
      }
    } catch (_) {
      if (!disposed && !getRuntime() && !terminal && requestRevision === revision) emit('reconnecting');
    } finally {
      pending = false;
      if (!disposed) {
        target.clearTimeout(pollTimer);
        // Reconcile server-side approvals and runs while the transcript is hidden.
        const quiet = getRuntime() || terminal || ['idle', 'completed'].includes(chat?.status);
        pollTimer = target.setTimeout(refresh, quiet ? 15000 : 3000);
      }
    }
  }
  function onLifecycle(event) {
    const detail = event.detail || {};
    if (String(detail.chatId || detail.sessionId || '') !== sessionId) return;
    const runtime = getRuntime();
    if (detail.runId && runtime?.runId && detail.runId !== runtime.runId) return;
    const status = detail.status;
    if (!['completed', 'failed', 'cancelled', 'stopped', 'awaiting_user', 'running'].includes(status)) return;
    revision++;
    terminal = ['completed', 'failed', 'cancelled', 'stopped'].includes(status) ? status : '';
    chat = { ...chat, status, pendingQuestion: status === 'awaiting_user' ? chat?.pendingQuestion : null };
    if (status === 'completed') complete();
    else { completion = false; target.clearTimeout(completionTimer); }
    render();
  }
  if (!sessionId) { emit('idle'); return () => { disposed = true; }; }
  const unsubscribe = engine.subscribe(render);
  target.addEventListener('cyrene:wbc-chat-lifecycle', onLifecycle);
  render();
  refresh();
  return () => {
    disposed = true; unsubscribe();
    target.clearTimeout(pollTimer); target.clearTimeout(completionTimer);
    target.removeEventListener('cyrene:wbc-chat-lifecycle', onLifecycle);
  };
}
