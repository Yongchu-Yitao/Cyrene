import React, { useLayoutEffect, useRef, useState } from 'react';
import { useWorkbenchI18n } from '../../workbench-i18n.jsx';

export function InlineCardPreview({ card, error, retry, children }) {
  const { t } = useWorkbenchI18n();
  const body = useRef(null);
  const [revealed, setRevealed] = useState(false);
  const ready = !!card;
  useLayoutEffect(() => {
    if (!ready || revealed) return;
    // Native blocks lay out with the prose. Sandboxed cards first report
    // their intrinsic height from the iframe at this same column width.
    const waiting = () => body.current?.querySelector('[data-awaiting-card-size]');
    if (!waiting()) {
      setRevealed(true);
      return;
    }
    const observer = new MutationObserver(() => {
      if (!waiting()) setRevealed(true);
    });
    observer.observe(body.current, { subtree: true, childList: true, attributes: true,
      attributeFilter: ['data-awaiting-card-size'] });
    // A broken embed must not withhold the remainder of a reply indefinitely.
    const timeout = setTimeout(() => setRevealed(true), 2500);
    return () => { observer.disconnect(); clearTimeout(timeout); };
  }, [ready, revealed]);

  if (!card && error) return <div className="wbc-preview-error" role="alert">{error}
    <button type="button" className="wb-btn" onClick={retry}>{t('common.retry')}</button>
  </div>;
  return <div className="wbc-inline-preview" data-pending={!revealed} aria-busy={!revealed}
    aria-label={ready ? undefined : t('common.loading')}>
    <div className="wbc-preview-body" ref={body}>{children}</div>
  </div>;
}
