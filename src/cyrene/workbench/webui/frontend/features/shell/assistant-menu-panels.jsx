import { workbenchServices } from '../../shared/runtime/services.jsx';

export function menuApi(path, body, method, signal, options = {}) {
  return workbenchServices.api().json(path, { ...options, toast: false, signal,
    ...(method ? { method } : body === undefined ? {} : { method: 'POST' }),
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
}
export function MenuSwitch({ label, hint, on, onChange, disabled }) {
  return <div className="aim-setting"><span>{label}{hint && <small>{hint}</small>}</span><button type="button" className={'wb-toggle' + (on ? ' on' : '')} role="switch" aria-label={label} aria-checked={!!on} disabled={disabled} onClick={() => onChange(!on)} /></div>;
}
export function AssistantCustomize() {
  const [config, setConfig] = React.useState(null), [error, setError] = React.useState(''), [busy, setBusy] = React.useState(false), [notice, setNotice] = React.useState('');
  const data = workbenchServices.data(); data.useVersion();
  const modules = data.state.pluginModules || [];
  React.useEffect(() => { const abort = new AbortController(); menuApi('/api/settings/config', undefined, undefined, abort.signal).then(setConfig).catch(e => { if (!abort.signal.aborted) setError(e.message); }); return () => abort.abort(); }, []);
  async function save() {
    setBusy(true); setError(''); setNotice('');
    try {
      if (modules.includes('soul')) await menuApi('/api/settings/soul', { content: config.soul_content || '' }, 'PUT');
      if (modules.includes('proactive')) await menuApi('/api/settings/config', { agent_proactive: !!config.agent_proactive }, 'PUT');
      setNotice('已保存');
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return <>{error && <p role="alert" className="aim-error">{error}</p>}{!config ? <p role="status">正在加载…</p> : <>
    {modules.includes('soul') && <label className="aim-field">你希望助理如何与你协作<textarea disabled={busy} value={config.soul_content || ''} onChange={e => { setNotice(''); setConfig({ ...config, soul_content: e.target.value }); }} /></label>}
    {modules.includes('proactive') && <MenuSwitch label="主动提醒" hint="重要进展和待办事项" on={config.agent_proactive} disabled={busy} onChange={on => { setNotice(''); setConfig({ ...config, agent_proactive: on }); }} />}
    {!modules.some(x => ['soul', 'proactive'].includes(x)) ? <p>请先在设置中启用人格或主动行为模块。</p> : <button className="aim-save" disabled={busy} onClick={save}>{busy ? '保存中…' : notice || '保存修改'}</button>}
  </>}</>;
}

export function AssistantMemory({ projectId }) {
  const base = '/api/projects/' + encodeURIComponent(projectId);
  const [messages, setMessages] = React.useState([]), [input, setInput] = React.useState(''), [busy, setBusy] = React.useState(false), [error, setError] = React.useState('');
  const draft = React.useRef(null), version = React.useRef(''), controller = React.useRef(null), inputRef = React.useRef(null), sequence = React.useRef(0);
  async function ask(instruction = '') {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    const id = ++sequence.current; setBusy(true); setError('');
    try {
      const result = await menuApi(base + '/memory-editor', { instruction, history: messages.slice(-12).map(m => ({ role: m.user ? 'user' : 'assistant', content: m.text.slice(0, 16000) })), ...(draft.current === null ? {} : { changes: draft.current, version: version.current }) }, 'POST', abort.signal, { timeout: 0 });
      if (abort.signal.aborted) return;
      if (draft.current === null) version.current = result.version;
      draft.current = result.changes;
      setMessages(prev => [...prev, ...(instruction ? [{ user: true, text: instruction, id: 'u' + id }] : []), { text: result.summary, changes: result.changes, id, draft: !!instruction && result.changes.length > 0 }]);
      setInput('');
    } catch (e) { if (!abort.signal.aborted) setError(e.message); } finally { if (!abort.signal.aborted) setBusy(false); }
  }
  React.useEffect(() => { ask(); return () => controller.current?.abort(); }, [projectId]);
  React.useLayoutEffect(() => { const el = inputRef.current; if (el) { el.style.height = '0px'; el.style.height = Math.max(24, el.scrollHeight) + 'px'; } }, [input]);
  async function apply(message) {
    setBusy(true); setError('');
    try {
      const result = await menuApi(base + '/memory-editor/apply', { changes: message.changes, version: version.current });
      version.current = result.version; draft.current = [];
      setMessages(prev => prev.map(item => ({ ...item, draft: false, saved: item.id === message.id })));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const latest = messages.filter(m => !m.user).at(-1);
  return <><div className="aim-memory-thread" aria-live="polite">
    {messages.map(m => <div key={m.id} className={m.user ? 'aim-user-message' : 'aim-agent-message'}><p>{m.text}</p>{m.draft && m.id === latest?.id && <button className="aim-apply" disabled={busy} onClick={() => apply(m)}>确认更新</button>}{m.saved && <small>已更新</small>}</div>)}
    {busy && <p role="status" className="aim-muted">{messages.length ? '正在处理…' : '正在整理记忆…'}</p>}
  </div>{error && <div className="aim-error" role="alert">{error}{!messages.length && <button onClick={() => ask()}>重试</button>}</div>}
    <form className="aim-composer" onSubmit={e => { e.preventDefault(); if (input.trim() && !busy && draft.current !== null) ask(input.trim()); }}>
      <textarea ref={inputRef} rows="1" maxLength={4000} value={input} aria-label="告诉助理如何修改记忆" placeholder="告诉我如何修改记忆…" onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form.requestSubmit(); } }} />
      <button type="submit" aria-label="发送修改要求" disabled={busy || !input.trim() || draft.current === null}>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5m-7 7 7-7 7 7"/></svg>
      </button>
    </form></>;
}
