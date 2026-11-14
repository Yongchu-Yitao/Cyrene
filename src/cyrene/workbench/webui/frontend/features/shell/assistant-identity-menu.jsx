import { CyreneIcon } from './cyrene-icon.jsx';
import { useCyreneActivity } from './use-cyrene-activity.jsx';
import { createAssistantGripPointer } from './assistant-grip-pointer.mjs';
import { assistantMenuBounds, assistantGripPresentation, observeAssistantGrip, listenAssistantMenuDismiss } from './assistant-grip-host.mjs';
import { workbenchServices } from '../../shared/runtime/services.jsx';
import { AssistantDeviceList } from './assistant-device-list.jsx';
import { loadAssistantDevices } from './assistant-device-actions.mjs';
import { AssistantCustomize, AssistantMemory, menuApi } from './assistant-menu-panels.jsx';

function Icon({ name }) {
  const paths = { edit: <><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/></>, memory: <><rect x="6" y="3" width="12" height="18" rx="3"/><path d="M10 8h4M10 12h4M10 16h2"/></>, device: <><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/></>, back: <path d="m12 5-7 7 7 7m-7-7h15"/>, close: <path d="m6 6 12 12M6 18 18 6"/>, check: <path d="m5 12 4 4L19 6"/>, more: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></> };
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
function useAssistantArrival(loading, welcome) {
  const [arriving, setArriving] = React.useState(false);
  const wasUndocked = React.useRef(loading || welcome);
  React.useLayoutEffect(() => {
    const dock = wasUndocked.current && !loading && !welcome;
    wasUndocked.current = loading || welcome;
    if (!dock) return;
    setArriving(true);
    const timer = setTimeout(() => setArriving(false), 600);
    return () => clearTimeout(timer);
  }, [loading, welcome]);
  return arriving;
}

export function AssistantIdentityMenu({ surfaceRef, sessionId, gripActions, gripActionsRef, loading = false, welcome = false, welcomeRef, hidden = false }) {
  gripActions = gripActionsRef?.current || gripActions;
  const activity = useCyreneActivity(sessionId);
  const arriving = useAssistantArrival(loading, welcome);
  const [anchor, setAnchor] = React.useState(null), [open, setOpen] = React.useState(false), [mounted, setMounted] = React.useState(false), [screen, setScreen] = React.useState('home');
  const [remote, setRemote] = React.useState(null), [tasks, setTasks] = React.useState([]), [error, setError] = React.useState('');
  const button = React.useRef(null), slot = React.useRef(null), panel = React.useRef(null), content = React.useRef(null), destination = React.useRef(null), gripRef = React.useRef(null), portal = React.useRef(null);
  const [paneDragging, setPaneDragging] = React.useState(false);
  const interaction = React.useRef(null), latest = React.useRef(null);
  latest.current = { open, actions: gripActions, onPaneDragging: setPaneDragging };
  function pointerController() {
    return interaction.current ||= createAssistantGripPointer(() => ({ ...latest.current, actions: gripActionsRef?.current || latest.current.actions,
      grip: gripRef.current, slot: slot.current, panel: panel.current }));
  }
  const [logo, setLogo] = React.useState(null), [height, setHeight] = React.useState(null);
  const data = workbenchServices.data(); data.useVersion(); const modules = data.state.pluginModules || [];
  // The owning conversation assigns surfaceRef after child layout effects.
  // Wait for that ref on initial/cached mounts, as the original grip did.
  React.useEffect(() => {
    const card = surfaceRef.current?.closest('.wbc-pane-card');
    const grip = card?.querySelector('.wbc-side-split-grip-bar') || (loading ? card : null);
    // A loading anchor survives the brief gap between the two page trees.
    if (!grip) { if (!loading) setAnchor(null); return; }
    gripRef.current = grip;
    const oldTab = grip.getAttribute('tabindex'), oldHidden = grip.getAttribute('aria-hidden');
    if (grip !== card) { grip.classList.add('aim-original-grip'); grip.setAttribute('tabindex', '-1'); grip.setAttribute('aria-hidden', 'true'); }
    const stop = observeAssistantGrip(card, grip, next => {
      // The old card can detach before the replacement surface registers.
      // Do not tear down the portalled icon during that handoff.
      if (!card.isConnected) return;
      setAnchor(next); if (!next.visible) setOpen(false);
    }, window, welcome ? welcomeRef?.current : null);
    return () => {
      stop(); interaction.current?.destroy(); interaction.current = null; gripRef.current = null;
      grip.classList.remove('aim-original-grip');
      for (const [key, value] of [['tabindex', oldTab], ['aria-hidden', oldHidden]]) {
        if (value === null) grip.removeAttribute(key); else grip.setAttribute(key, value);
      }
    };
  }, [surfaceRef, sessionId, loading, welcome, welcomeRef]);
  React.useEffect(() => {
    if (open) { setMounted(true); return; }
    const timer = setTimeout(() => { setMounted(false); setScreen('home'); setHeight(null); }, 360);
    return () => clearTimeout(timer);
  }, [open]);
  React.useEffect(() => {
    if (!mounted) return;
    let overlays; try { overlays = workbenchServices.browserOverlays(); } catch (_) {}
    overlays?.adjust?.(1);
    const stopDismiss = listenAssistantMenuDismiss(() => setOpen(false));
    function outside(e) { if (!portal.current?.contains(e.target)) setOpen(false); }
    function key(e) {
      if (e.key === 'Escape') { setOpen(false); button.current?.focus(); }
      if (e.key === 'Tab' && open) {
        const nodes = [button.current, ...Array.from(panel.current?.querySelectorAll('button:not(:disabled),textarea:not(:disabled),select:not(:disabled),input:not(:disabled)') || [])].filter(node => node && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
        const first = nodes[0], last = nodes.at(-1);
        if (e.shiftKey && e.target === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && e.target === last) { e.preventDefault(); first?.focus(); }
      }
    }
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', key);
    return () => { stopDismiss(); overlays?.adjust?.(-1); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', key); };
  }, [mounted, open]);
  const { width, left, top, maxHeight } = assistantMenuBounds(anchor, { width: window.innerWidth, height: window.innerHeight });
  React.useLayoutEffect(() => {
    if (!mounted || !content.current) { setLogo(null); return; }
    function measure() {
      setHeight(content.current.offsetHeight + 2);
      const target = destination.current;
      if (target) {
        let x = target.offsetWidth / 2, y = target.offsetHeight / 2, node = target;
        while (node && node !== panel.current) { x += node.offsetLeft; y += node.offsetTop; node = node.offsetParent; }
        setLogo({ x: left + x + 1, y: top + y + 1 - (content.current?.scrollTop || 0) });
      }
    }
    measure(); const observer = new ResizeObserver(measure); observer.observe(content.current);
    const surface = content.current; surface.addEventListener('scroll', measure);
    return () => { observer.disconnect(); surface.removeEventListener('scroll', measure); };
  }, [mounted, screen, left, top, width]);
  const deviceRequest = React.useRef(null);
  async function refreshRemote() {
    deviceRequest.current?.abort();
    const abort = new AbortController(); deviceRequest.current = abort;
    try { return await loadAssistantDevices(menuApi, setRemote, abort.signal); }
    catch (e) { if (!abort.signal.aborted) throw e; }
  }
  React.useEffect(() => {
    if (!open) return;
    const abort = new AbortController(); setError('');
    const requests = [menuApi('/api/workbench/assistant', undefined, undefined, abort.signal).then(value => setTasks(value.sessions || []))];
    if (modules.includes('remote')) requests.push(refreshRemote());
    Promise.all(requests).catch(e => { if (!abort.signal.aborted) setError(e.message); });
    return () => { abort.abort(); deviceRequest.current?.abort(); };
  }, [open, modules.join('|')]);
  React.useEffect(() => {
    if (!open || !modules.includes('remote')) return;
    const refresh = () => { refreshRemote().catch(e => setError(e.message)); };
    window.addEventListener('cyrene:remote-devices-changed', refresh);
    const selected = event => { if (event.detail?.sessionId === sessionId) setRemote(current => ({ ...current, work_device: event.detail.workDevice })); };
    window.addEventListener('cyrene:assistant-work-device-changed', selected);
    return () => {
      window.removeEventListener('cyrene:remote-devices-changed', refresh);
      window.removeEventListener('cyrene:assistant-work-device-changed', selected);
    };
  }, [open, modules.join('|')]);
  function key(e) {
    if (!open && !gripActions?.singlePane && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      e.preventDefault(); gripActions?.onMove?.();
    }
  }
  function close() { setOpen(false); button.current?.focus(); }
  function activityClick(task) { close(); window.dispatchEvent(new CustomEvent('cyrene:assistant-open-activity', { detail: { sessionId: task.session_id } })); }
  if (!anchor?.visible || hidden) return null;
  const { position, welcomeScale, brand, className } = assistantGripPresentation(anchor, { loading, welcome, open, logo, mounted, arriving });
  return ReactDOM.createPortal(<div className={className} ref={portal} style={{ ...anchor.theme, '--aim-welcome-scale': welcomeScale, '--aim-top': top + 'px', '--aim-left': left + 'px', '--aim-width': width + 'px', '--aim-max-height': maxHeight + 'px' }}>
    {brand && <span className="aim-welcome-brand" aria-hidden="true" style={{left: brand.left, top: brand.top, font: brand.font, letterSpacing: brand.letterSpacing, color: brand.color}}>{brand.text}</span>}
    <button ref={button} className="aim-logo-button" style={{ transform: `translate(${position.x - 26}px,${position.y - 26}px)`, opacity: paneDragging ? 0 : undefined, visibility: (open && logo && (logo.y - 26 < top || logo.y + 26 > top + (height || maxHeight))) ? 'hidden' : undefined }} disabled={loading} aria-label={loading ? "正在加载助理" : "助理菜单（可拖动）"} aria-expanded={open} aria-haspopup="dialog" onKeyDown={key} onPointerDown={e => pointerController().down(e)} onPointerMove={e => interaction.current?.move(e)} onPointerUp={e => interaction.current?.release(e)} onPointerCancel={e => interaction.current?.release(e)} onLostPointerCapture={e => interaction.current?.release(e)} onClick={e => { if (e.detail === 0 || e.currentTarget.dataset.wbcPointerDragged !== 'true') setOpen(value => !value); }}>
      <span className="wbc-side-split-grip-bar-visual" ref={slot}><CyreneIcon state={loading ? "thinking" : activity} /></span>
    </button>
    {mounted && <div ref={panel} role="dialog" aria-label="助理设置" className={'aim-panel' + (open ? ' is-open' : '')} style={{ height: height == null ? 'auto' : height + 'px' }}>
      <div ref={content} className="aim-content">
        {screen === 'home' ? <header className="aim-head"><span ref={destination} className="aim-logo-slot" /><h2>Cyrene</h2><button aria-label="关闭菜单" className="aim-close" onClick={close}><Icon name="close" /></button></header> : <header className="aim-detail-head"><button aria-label="返回菜单" onClick={() => setScreen('home')}><Icon name="back" /></button><h3>{{ custom: '自定义助理', memory: '更改记忆' }[screen]}</h3><span ref={destination} className="aim-logo-slot" /></header>}
        {screen === 'home' && <>
          <div className="aim-actions"><button onClick={() => setScreen('custom')}><Icon name="edit" />自定义助理</button><button disabled={!modules.includes('memory')} onClick={() => setScreen('memory')}><Icon name="memory" />更改记忆</button></div>
          {modules.includes('remote') && <AssistantDeviceList remote={remote} localDeviceName={data.state.localDeviceName} sessionId={sessionId} onUpdated={refreshRemote} panelRef={panel} portalRef={portal} Icon={Icon} />}
          <section><h4>近期活动</h4>{tasks.length ? [...tasks].sort((a, b) => (Date.parse(b.updated_at || b.created_at) || 0) - (Date.parse(a.updated_at || a.created_at) || 0)).slice(0, 4).map(task => <button className="aim-device aim-activity" key={task.session_id} onClick={() => activityClick(task)}><Icon name="check" /><span><span className="aim-item-title">{task.presentation?.title || task.title || '任务'}</span><small>{{ completed: '已完成', running: '进行中', waiting: '等待回复', awaiting_user: '等待确认', failed: '失败', stopped: '已暂停', idle: '待处理' }[task.status] || '打开任务'}</small></span></button>) : <p className="aim-muted">暂无活动</p>}</section>
          {error && <p role="alert" className="aim-error">{error}</p>}
        </>}
        {screen === 'custom' && <AssistantCustomize />}
        {screen === 'memory' && <AssistantMemory projectId="__personal_assistant__" />}
      </div>
    </div>}
  </div>, document.body);
}
