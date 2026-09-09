import { drawerSwipe } from "./mobile-gesture.mjs"

export function useMobileDrawers(page) {
  const [compact, setCompact] = React.useState(() => window.matchMedia('(max-width: 767px)').matches);
  const [side, setSide] = React.useState('');
  React.useEffect(() => {
    const query = window.matchMedia('(max-width: 767px)');
    const change = () => { setCompact(query.matches); setSide(''); };
    query.addEventListener('change', change);
    return () => query.removeEventListener('change', change);
  }, []);
  React.useEffect(() => { setSide(''); }, [page]);
  return { compact, side, setSide };
}

// Preserve the module DOM (and its scroll position); only change its presentation.
export function MobileDrawerControls({ state }) {
  const root = React.useRef(null);
  // The Android shell shades system insets outside the WebView with the same scrim.
  React.useEffect(() => {
    window.CyreneAndroid?.setDrawerOpen?.(!!(state.compact && state.side));
  }, [state.compact, state.side]);
  React.useEffect(() => () => window.CyreneAndroid?.setDrawerOpen?.(false), []);
  const [available, setAvailable] = React.useState({ left: false, right: false });
  const zh = (document.documentElement.lang || navigator.language).startsWith('zh');
  const labels = zh ? ['左侧卡片', '右侧卡片', '关闭侧栏'] : ['Left cards', 'Right cards', 'Close sidebar'];
  React.useEffect(() => {
    if (!state.compact || !root.current) return;
    const shell = root.current.closest('.workbench-shell');
    const leftSelector = '.workbench-integrated-rail, .workbench-sidebar-dock.is-persistent';
    const rightSelector = '.wbc-page > .wbc-side, .wbc-pane-layout.split > .wbc-pane-column.right, .workbench-right-panel, .wb-lib-right';
    const saved = new Map();
    const background = new Map();
    const sync = () => {
      let left = false, right = false;
      shell.querySelectorAll(leftSelector + ',' + rightSelector).forEach(node => {
        if (!saved.has(node)) saved.set(node, node.inert);
        const which = node.matches(leftSelector) ? 'left' : 'right';
        node.dataset.mobileCard = which;
        const active = !node.closest('.workbench-stable-surface.is-hidden');
        if (active) { if (which === 'left') left = true; else right = true; }
        node.inert = !active || state.side !== which;
      });
      shell.querySelectorAll('.workbench-topbar, .wbc-pane-column.left, .wbc-page > .wbc-main, .wb-lib-main, .wb-sched-main, .wb-mem-main, .workbench-conversation-board').forEach(node => {
        if (node.closest('[data-mobile-card]')) return;
        if (!background.has(node)) background.set(node, node.inert);
        node.inert = !!state.side;
      });
      setAvailable(old => old.left === left && old.right === right ? old : { left, right });
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(shell, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    return () => {
      observer.disconnect();
      background.forEach((inert, node) => { node.inert = inert; });
      saved.forEach((inert, node) => { node.inert = inert; delete node.dataset.mobileCard; });
    };
  }, [state.compact, state.side]);
  React.useEffect(() => {
    if (!state.side || !root.current) return;
    const shell = root.current.closest('.workbench-shell');
    const previous = document.activeElement;
    const panel = shell.querySelector('[data-mobile-card="' + state.side + '"]:not([inert])');
    const target = panel && panel.querySelector('button, input, textarea, select, a[href], [tabindex="0"]');
    const focusFrame = window.setTimeout(() => { if (target) target.focus({ preventScroll: true }); }, 230);
    const key = event => {
      if (event.key === 'Escape') { event.preventDefault(); state.setSide(''); }
      if (event.key === 'Tab') {
        const panels = [...shell.querySelectorAll('[data-mobile-card="' + state.side + '"]:not([inert])')];
        const nodes = panels.flatMap(node => [...node.querySelectorAll('button, input, textarea, select, a[href], [tabindex="0"]')])
          .concat([...shell.querySelectorAll('.wb-mobile-scrim')])
          .filter(n => !n.disabled && !n.closest('[inert]') && n.getClientRects().length && getComputedStyle(n).visibility === 'visible');
        if (!nodes.length) return;
        const i = nodes.indexOf(document.activeElement);
        if ((event.shiftKey && i <= 0) || (!event.shiftKey && (i < 0 || i === nodes.length - 1))) {
          event.preventDefault(); nodes[event.shiftKey ? nodes.length - 1 : 0].focus();
        }
      }
    };
    document.addEventListener('keydown', key);
    return () => { window.clearTimeout(focusFrame); document.removeEventListener('keydown', key); if (previous && previous.isConnected) previous.focus({ preventScroll: true }); };
  }, [state.side]);
  React.useEffect(() => {
    if (!state.compact || !root.current) return;
    const shell = root.current.closest('.workbench-shell');
    let pointer = null, mouseActive = false, wheelTotal = 0, wheelAt = 0;
    const scrollOwns = target => {
      if (target.closest('input, textarea, select, [contenteditable="true"], pre, .wbc-table-wrap, .xterm, .cm-editor')) return true;
      for (let n = target; n && n !== shell; n = n.parentElement) {
        if (n.scrollWidth > n.clientWidth + 2 && /auto|scroll/.test(getComputedStyle(n).overflowX)) return true;
      }
      return false;
    };
    const down = event => {
      pointer = null;
      if (event.touches.length !== 1 || scrollOwns(event.target) || event.target.closest('.workbench-topbar')) return;
      const panel = event.target.closest('[data-mobile-card]');
      const touch = event.touches[0];
      const bounds = shell.getBoundingClientRect();
      const edge = touch.clientX <= bounds.left + 48 ? 'left' : touch.clientX >= bounds.right - 48 ? 'right' : 'center';
      if (state.side ? panel && panel.dataset.mobileCard === state.side : (edge === 'center' ? available.left || available.right : available[edge])) {
        pointer = { x: touch.clientX, y: touch.clientY, dx: 0, dy: 0, edge: state.side || edge };
      }
    };
    const move = event => {
      if (!pointer || event.touches.length !== 1) { pointer = null; return; }
      pointer.dx = event.touches[0].clientX - pointer.x;
      pointer.dy = event.touches[0].clientY - pointer.y;
      if (Math.abs(pointer.dy) > 12 && Math.abs(pointer.dy) > Math.abs(pointer.dx)) { pointer = null; return; }
      const wanted = pointer.dx > 0 ? 'left' : 'right';
      const reverse = state.side ? (state.side === 'left' ? pointer.dx < 0 : pointer.dx > 0) : available[wanted] && (pointer.edge === 'center' || pointer.edge === wanted);
      if (reverse && Math.abs(pointer.dx) > 12 && Math.abs(pointer.dx) > Math.abs(pointer.dy) * 1.5) event.preventDefault();
    };
    const up = event => {
      if (!pointer) return;
      const action = drawerSwipe(pointer.edge, state.side, pointer.dx, pointer.dy);
      pointer = null;
      if (action !== null && (!action || available[action])) { event.preventDefault(); state.setSide(action); }
    };
    const cancel = () => { pointer = null; mouseActive = false; };
    const mouseDown = event => {
      if (event.pointerType !== 'mouse' || event.button !== 0 || event.target.closest('button, a')) return;
      down({ target: event.target, touches: [event] });
      mouseActive = !!pointer;
      if (mouseActive) event.preventDefault();
    };
    const mouseMove = event => { if (mouseActive) move({ touches: [event], preventDefault: () => event.preventDefault() }); };
    const mouseUp = event => { if (mouseActive) { mouseActive = false; up(event); } };

    // Trackpads provide wheel rather than pointer swipes. Only own horizontal
    // motion over content or an open card; nested scrollers keep theirs.
    const wheel = event => {
      if (event.target.closest('.workbench-topbar') || scrollOwns(event.target) || Math.abs(event.deltaX) <= Math.abs(event.deltaY) * 1.5) return;
      const bounds = shell.getBoundingClientRect();
      const edge = event.clientX < bounds.left + 56 ? 'left' : event.clientX > bounds.right - 56 ? 'right' : 'center';
      const panel = event.target.closest('[data-mobile-card]');
      if (state.side && (!panel || panel.dataset.mobileCard !== state.side)) return;
      const now = Date.now();
      if (now - wheelAt > 180 || Math.sign(wheelTotal) !== Math.sign(event.deltaX)) wheelTotal = 0;
      wheelAt = now; wheelTotal += event.deltaX;
      const action = drawerSwipe(edge || state.side, state.side, -wheelTotal, 0);
      if (action === null || (action && !available[action])) return;
      event.preventDefault(); wheelTotal = 0; state.setSide(action);
    };
    shell.addEventListener('pointerdown', mouseDown);
    window.addEventListener('pointermove', mouseMove);
    window.addEventListener('pointerup', mouseUp);
    window.addEventListener('blur', cancel);
    shell.addEventListener('touchstart', down, { passive: true });
    shell.addEventListener('touchmove', move, { passive: false });
    shell.addEventListener('touchend', up, { passive: false });
    shell.addEventListener('touchcancel', cancel);
    shell.addEventListener('wheel', wheel, { passive: false });
    return () => {
      shell.removeEventListener('pointerdown', mouseDown);
      window.removeEventListener('pointermove', mouseMove); window.removeEventListener('pointerup', mouseUp);
      window.removeEventListener('blur', cancel);
      shell.removeEventListener('touchstart', down); shell.removeEventListener('touchmove', move); shell.removeEventListener('touchend', up);
      shell.removeEventListener('touchcancel', cancel); shell.removeEventListener('wheel', wheel);
    };
  }, [state.compact, state.side, available]);
  if (!state.compact) return null;
  return <div ref={root} className="wb-mobile-controls">
    <div className="wb-mobile-accessibility">
      {['left', 'right'].map((side, i) => <button key={side} disabled={!available[side]} onClick={() => state.setSide(side)}>{labels[i]}</button>)}
    </div>
    {state.side && <button className="wb-mobile-scrim" aria-label={labels[2]} onClick={() => state.setSide('')} />}
  </div>;
}
