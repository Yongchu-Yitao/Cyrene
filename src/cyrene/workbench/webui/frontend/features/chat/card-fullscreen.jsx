import React, { useEffect, useRef, useState } from 'react';
import { useWorkbenchI18n } from '../../workbench-i18n.jsx';

export function useCardFullscreen(ref) {
  const [expanded, setExpanded] = useState(false);
  const close = useRef(null), pending = useRef(false);
  useEffect(() => () => { pending.current = false; close.current?.(false); }, []);
  async function toggle() {
    if (close.current) { close.current(); return; }
    if (pending.current) return;
    const element = ref.current;
    pending.current = true;
    try {
      const { enterCardFullscreen } = await import('./card-fullscreen-controller.mjs');
      if (!pending.current || element !== ref.current || !element?.isConnected) return;
      close.current = enterCardFullscreen(element, () => { close.current = null; setExpanded(false); });
      if (close.current) setExpanded(true);
    } finally { pending.current = false; }
  }
  return { expanded, toggle };
}

export function CardFullscreenButton({ expanded, toggle }) {
  const { lang } = useWorkbenchI18n();
  const label = lang === 'zh' ? (expanded ? '退出全屏' : '全屏') : (expanded ? 'Exit fullscreen' : 'Fullscreen');
  if (typeof HTMLElement !== 'undefined' && !HTMLElement.prototype.showPopover) return null;
  return <div className="wbc-card-fullscreen-corner">
    <button type="button" className="wb-btn wbc-card-fullscreen-button" onClick={toggle}
      aria-label={label} title={label} aria-pressed={expanded}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d={expanded ? 'M9 3v6H3m18 0h-6V3M3 15h6v6m6 0v-6h6' : 'M9 3H3v6m12-6h6v6M3 15v6h6m6 0h6v-6'} />
      </svg>
    </button>
  </div>;
}
