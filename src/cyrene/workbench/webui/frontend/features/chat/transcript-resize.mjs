// Keep offscreen history out of per-frame line layout during sidebar motion.
// Rows stay mounted, with their measured height, until the transition settles.
export function protectTranscriptResize(thread, page, isSticking) {
  const doc = thread.ownerDocument;
  const win = doc.defaultView;
  let frozen = [];
  let timeout = 0;

  function restore(preserve = true) {
    win.clearTimeout(timeout);
    timeout = 0;
    if (!frozen.length) return;
    const bottom = isSticking();
    const top = thread.getBoundingClientRect().top;
    const anchor = preserve && !bottom
      ? Array.from(thread.children).find(row => !row.hasAttribute('data-wbc-resize-frozen')
        && row.getBoundingClientRect().bottom > top)
      : null;
    const offset = anchor ? anchor.getBoundingClientRect().top : 0;
    for (const row of frozen) {
      row.removeAttribute('data-wbc-resize-frozen');
      row.style.removeProperty('--wbc-resize-row-height');
    }
    frozen = [];
    if (!preserve) return;
    if (bottom) thread.scrollTop = thread.scrollHeight;
    else if (anchor?.isConnected) thread.scrollTop += anchor.getBoundingClientRect().top - offset;
  }

  function start(event) {
    if (event.target !== page || event.propertyName !== 'grid-template-columns') return;
    restore();
    // PiP avoidance measures individual rows; keep its existing geometry path.
    // Do not hide selected text or an active editor from the browser either.
    if (page.querySelector('.wbc-browser-window.pip, .wbc-browser-restore-float')) return;
    const selection = win.getSelection();
    if (selection && !selection.isCollapsed) return;
    const rows = thread.querySelectorAll(':scope > [data-wbc-thread-item]');
    if (rows.length < 24) return;
    const viewport = thread.getBoundingClientRect();
    const buffer = Math.max(viewport.height, 600);
    // Read all geometry before any style writes. Keep a full viewport on each
    // side live so wrapping near the edge cannot expose a frozen row.
    const candidates = [];
    for (const row of rows) {
      const rect = row.getBoundingClientRect();
      if ((rect.bottom < viewport.top - buffer || rect.top > viewport.bottom + buffer)
        && !row.contains(doc.activeElement) && !row.classList.contains('retry-clearing')) {
        candidates.push([row, rect.height]);
      }
    }
    for (const [row, height] of candidates) {
      row.style.setProperty('--wbc-resize-row-height', height + 'px');
      row.setAttribute('data-wbc-resize-frozen', '');
      frozen.push(row);
    }
    // transitionend/cancel normally restores immediately. Bound the lifetime
    // if a host hides the page or replaces the transition mid-flight.
    timeout = win.setTimeout(restore, 1000);
  }

  function end(event) {
    if (event.target === page && event.propertyName === 'grid-template-columns') restore();
  }
  function interrupt() { restore(); }
  page.addEventListener('transitionrun', start);
  page.addEventListener('transitionend', end);
  page.addEventListener('transitioncancel', end);
  // User navigation must never scroll into temporarily skipped content.
  thread.addEventListener('wheel', interrupt, { passive: true });
  thread.addEventListener('touchstart', interrupt, { passive: true });
  doc.addEventListener('pointerdown', interrupt, true);
  doc.addEventListener('keydown', interrupt, true);
  thread.addEventListener('focusin', interrupt);
  return function () {
    restore(false);
    page.removeEventListener('transitionrun', start);
    page.removeEventListener('transitionend', end);
    page.removeEventListener('transitioncancel', end);
    thread.removeEventListener('wheel', interrupt);
    thread.removeEventListener('touchstart', interrupt);
    doc.removeEventListener('pointerdown', interrupt, true);
    doc.removeEventListener('keydown', interrupt, true);
    thread.removeEventListener('focusin', interrupt);
  };
}
