import { useDrawerCards, useDrawerFocus, useDrawerGestures } from "./mobile-drawer-effects.jsx"

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
  const sideRef = React.useRef(state.side);
  sideRef.current = state.side;
  const syncCards = React.useRef(null);
  // The Android shell shades system insets outside the WebView with the same scrim.
  React.useEffect(() => {
    window.CyreneAndroid?.setDrawerOpen?.(!!(state.compact && state.side));
  }, [state.compact, state.side]);
  React.useEffect(() => () => window.CyreneAndroid?.setDrawerOpen?.(false), []);
  const [available, setAvailable] = React.useState({ left: false, right: false });
  const zh = (document.documentElement.lang || navigator.language).startsWith('zh');
  const labels = zh ? ['左侧卡片', '右侧卡片', '关闭侧栏'] : ['Left cards', 'Right cards', 'Close sidebar'];
  useDrawerCards(state, root, sideRef, syncCards, setAvailable);
  useDrawerFocus(state, root);
  useDrawerGestures(state, root, available);
  if (!state.compact) return null;
  return <div ref={root} className="wb-mobile-controls">
    <div className="wb-mobile-accessibility">
      {['left', 'right'].map((side, i) => <button key={side} disabled={!available[side]} onClick={() => state.setSide(side)}>{labels[i]}</button>)}
    </div>
    {state.side && <button className="wb-mobile-scrim" aria-label={labels[2]} onClick={() => state.setSide('')} />}
  </div>;
}
