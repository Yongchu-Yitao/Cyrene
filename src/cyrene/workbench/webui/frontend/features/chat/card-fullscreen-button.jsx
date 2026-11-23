import React from 'react';
import { useWorkbenchI18n } from '../../workbench-i18n.jsx';

export default function CardFullscreenButton({ expanded, toggle, onStop, stopped, allowFullscreen = true }) {
  const { lang } = useWorkbenchI18n();
  const label = lang === 'zh' ? (expanded ? '退出全屏' : '全屏') : (expanded ? 'Exit fullscreen' : 'Fullscreen');
  const stopLabel = lang === 'zh' ? '停止运行' : 'Stop running';
  if (!allowFullscreen && !onStop) return null;
  if (typeof HTMLElement !== 'undefined' && !HTMLElement.prototype.showPopover) return null;
  return <div className="wbc-card-fullscreen-corner">
    <div className="wbc-card-controls" role="group" aria-label={lang === 'zh' ? '卡片控制' : 'Card controls'}>
    {onStop && <button type="button" className="wb-btn wbc-card-stop-button" onClick={onStop}
      disabled={stopped} aria-label={stopLabel} title={stopLabel}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2" /></svg>
    </button>}
    {allowFullscreen && <button type="button" className="wb-btn wbc-card-fullscreen-button" onClick={toggle}
      aria-label={label} title={label} aria-pressed={expanded}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d={expanded ? 'M9 3v6H3m18 0h-6V3M3 15h6v6m6 0v-6h6' : 'M9 3H3v6m12-6h6v6M3 15v6h6m6 0h6v-6'} />
      </svg>
    </button>}
    </div>
  </div>;
}
