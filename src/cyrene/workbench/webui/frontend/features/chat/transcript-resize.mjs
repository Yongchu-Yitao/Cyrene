// Keep offscreen history out of per-frame line layout during sidebar motion.
// Rows stay mounted, with their measured height, until the transition settles.
export function protectTranscriptResize(thread, page, isSticking, onRestored = () => {}) {
  const doc = thread.ownerDocument;
  const win = doc.defaultView;
  let frozen = [];
  let timeout = 0;
  let prepared = false;
  let restoreFrame = 0;

  function restore(preserve = true, gradual = false) {
    if (restoreFrame) win.cancelAnimationFrame(restoreFrame);
    restoreFrame = 0;
    prepared = false;
    win.clearTimeout(timeout);
    timeout = 0;
    if (!frozen.length) return;
    const bottom = isSticking();
    const top = thread.getBoundingClientRect().top;
    let anchor = preserve && !bottom
      ? Array.from(thread.children).find(row => !row.hasAttribute('data-wbc-resize-frozen')
        && row.getBoundingClientRect().bottom > top)
      : null;
    if (anchor) {
      const block = Array.from(anchor.querySelectorAll('.wbc-msg-body.markdown > *'))
        .find(node => !node.hasAttribute('data-wbc-resize-frozen') && node.getBoundingClientRect().bottom > top);
      if (block) anchor = block;
    }
    const offset = anchor ? anchor.getBoundingClientRect().top : 0;
    const batch = frozen.splice(0, gradual ? 32 : frozen.length);
    for (const row of batch) {
      row.removeAttribute('data-wbc-resize-frozen');
      row.style.removeProperty('--wbc-resize-row-height');
    }
    thread.wbcResizeActive = frozen.length > 0;
    if (!preserve) return;
    if (bottom) thread.scrollTop = thread.scrollHeight;
    else if (anchor?.isConnected) thread.scrollTop += anchor.getBoundingClientRect().top - offset;
    if (frozen.length) {
      // Restoring a thousand rows together causes a large style/layout task.
      // Release a bounded batch per frame, preserving the viewport each time.
      restoreFrame = win.requestAnimationFrame(() => restore(true, true));
      return;
    }
    thread.dispatchEvent(new win.Event("workbench:transcript-resize-end"));
    onRestored();
  }

  function start(event) {
    if (event.target !== page || event.propertyName !== 'grid-template-columns') return;
    if (prepared) { prepared = false; return; }
    freeze();
  }

  function freeze() {
    restore();
    // Keep selection, editors and rows currently yielding space to a PiP live.
    const selection = win.getSelection();
    if (selection && !selection.isCollapsed) return;
    const rows = thread.querySelectorAll(':scope > [data-wbc-thread-item]');
    const viewport = thread.getBoundingClientRect();
    const buffer = Math.max(viewport.height, 600);
    // Read all geometry before any style writes. Keep a full viewport on each
    // side live so wrapping near the edge cannot expose a frozen row.
    const candidates = [];
    function outside(rect) {
      return rect.bottom < viewport.top - buffer || rect.top > viewport.bottom + buffer;
    }
    for (const row of rows) {
      if (row.contains(doc.activeElement) || row.classList.contains('retry-clearing')) continue;
      const rect = row.getBoundingClientRect();
      if (outside(rect) && !row.classList.contains('wbc-browser-avoid-left')
        && !row.classList.contains('wbc-browser-avoid-right')) {
        candidates.push([row, rect.height]);
        continue;
      }
      // A single reply can span hundreds of screens. Its visible wrapper
      // must stay live, but distant Markdown blocks need not rewrap each frame.
      // Avoid list/blockquote containers whose child margin collapse changes
      // under containment; keep live streaming output entirely untouched.
      for (const block of row.querySelectorAll('.wbc-msg-body.markdown:not(.streaming) > :is(p, pre, table)')) {
        const blockRect = block.getBoundingClientRect();
        if (outside(blockRect)) candidates.push([block, blockRect.height]);
      }
    }
    for (const [row, height] of candidates) {
      row.style.setProperty('--wbc-resize-row-height', height + 'px');
      row.setAttribute('data-wbc-resize-frozen', '');
      frozen.push(row);
    }
    thread.wbcResizeActive = frozen.length > 0;
    // transitionend/cancel normally restores immediately. Bound the lifetime
    // if a host hides the page or replaces the transition mid-flight.
    timeout = win.setTimeout(() => restore(true, true), 1000);
  }

  function end(event) {
    if (event.target === page && event.propertyName === 'grid-template-columns') restore(true, event.type !== 'transitioncancel');
  }
  function interrupt() { restore(); }
  function prepare() { freeze(); prepared = frozen.length > 0; }
  function beforeInteraction(event) {
    const control = event.target?.closest?.('.workbench-sidebar-collapse-control, .wbc-side-hide-btn');
    if (control && page.contains(control) && (event.type !== 'keydown' || event.key === 'Enter' || event.key === ' ')) {
      // Run before React changes the grid class, while old geometry is clean.
      // Waiting for transitionrun pays one full-history layout first.
      prepare();
    } else restore();
  }
  win.addEventListener('workbench:show-chat-side', prepare, true);
  page.addEventListener('transitionrun', start);
  page.addEventListener('transitionend', end);
  page.addEventListener('transitioncancel', end);
  // User navigation must never scroll into temporarily skipped content.
  thread.addEventListener('wheel', interrupt, { passive: true });
  thread.addEventListener('touchstart', interrupt, { passive: true });
  doc.addEventListener('pointerdown', beforeInteraction, true);
  doc.addEventListener('keydown', beforeInteraction, true);
  thread.addEventListener('focusin', interrupt);
  return function () {
    restore(false);
    win.removeEventListener('workbench:show-chat-side', prepare, true);
    page.removeEventListener('transitionrun', start);
    page.removeEventListener('transitionend', end);
    page.removeEventListener('transitioncancel', end);
    thread.removeEventListener('wheel', interrupt);
    thread.removeEventListener('touchstart', interrupt);
    doc.removeEventListener('pointerdown', beforeInteraction, true);
    doc.removeEventListener('keydown', beforeInteraction, true);
    thread.removeEventListener('focusin', interrupt);
  };
}
