const pendingLayouts = new WeakMap();

// One owner for frozen rows, frame restoration and paired subscriptions.
class TranscriptResizeProtection {
  constructor(thread, page, isSticking, onRestored, conversationId) {
    this.thread = thread;
    this.page = page;
    this.isSticking = isSticking;
    this.onRestored = onRestored;
    this.conversationId = conversationId;

    this.doc = this.thread.ownerDocument;
    this.win = this.doc.defaultView;
    this.frozen = [];
    this.timeout = 0;
    this.prepared = false;
    this.restoreFrame = 0;
    this.dragging = false;
    this.transitions = new Set();
    this.disclosure = { timer: 0, active: new Set() };

    // Closing a pane may replace WbcChatSplit with WbcMain. Adopt measured
    // offscreen heights before the new owner's first layout effects measure it.
    const layouts = pendingLayouts.get(this.page);
    const snapshot = this.conversationId && layouts?.get(this.conversationId);
    if (snapshot) {
      layouts.delete(this.conversationId);
      const rows = this.thread.querySelectorAll(':scope > [data-wbc-thread-item]');
      if (rows.length === snapshot.count) {
        rows.forEach((row, index) => {
          const height = snapshot.heights[index];
          if (!height) return;
          row.style.setProperty('--wbc-resize-row-height', height);
          row.setAttribute('data-wbc-resize-frozen', '');
          this.frozen.push(row);
        });
        this.thread.wbcResizeActive = this.frozen.length > 0;
        this.timeout = this.win.setTimeout(() => this.restore(true, true), 1000);
      }
    }
    this.prepareDisclosure = this.prepareDisclosure.bind(this);
    this.finishDisclosure = this.finishDisclosure.bind(this);
    this.prepare = this.prepare.bind(this);
    this.prepareHandoff = this.prepareHandoff.bind(this);
    this.resizePhase = this.resizePhase.bind(this);
    this.cancelDrag = this.cancelDrag.bind(this);
    this.start = this.start.bind(this);
    this.end = this.end.bind(this);
    this.interrupt = this.interrupt.bind(this);
    this.beforeInteraction = this.beforeInteraction.bind(this);
    this.listeners = [
      [this.thread, "workbench:trace-disclosure", this.prepareDisclosure],
      [this.thread, "transitionend", this.finishDisclosure],
      [this.thread, "transitioncancel", this.finishDisclosure],
      [this.win, "workbench:show-chat-side", this.prepare, true],
      [this.win, "workbench:pane-layout-change", this.prepareHandoff],
      [this.win, "workbench:right-resize", this.resizePhase],
      [this.win, "workbench:split-resize-start", this.resizePhase],
      [this.win, "workbench:split-resize-end", this.resizePhase],
      [this.win, "blur", this.cancelDrag],
      [this.page, "transitionrun", this.start],
      [this.page, "transitionend", this.end],
      [this.page, "transitioncancel", this.end],
      [this.thread, "wheel", this.interrupt, { passive: true }],
      [this.thread, "touchstart", this.interrupt, { passive: true }],
      [this.doc, "pointerdown", this.beforeInteraction, true],
      [this.doc, "keydown", this.beforeInteraction, true],
      [this.thread, "focusin", this.interrupt],
    ];
    for (const [target, ...args] of this.listeners) target.addEventListener(...args);
  }

  prepareDisclosure(event) {
    const summary = event.detail?.anchor;
    const collapse = summary?.parentElement?.querySelector(':scope > .wbc-trace-collapse, :scope > .wbc-activity-group-collapse');
    if (!collapse) return;
    this.disclosure.active.add(collapse);
    this.thread.wbcDisclosureActive = true;
    this.win.clearTimeout(this.disclosure.timer);
    this.disclosure.timer = this.win.setTimeout(() => this.finishDisclosure(), 500);
  }

  finishDisclosure(event) {
    if (event) {
      if (event.propertyName !== 'grid-template-rows' || !this.disclosure.active.has(event.target)) return;
      this.disclosure.active.delete(event.target);
      if (this.disclosure.active.size) return;
    }
    this.disclosure.active.clear();
    this.win.clearTimeout(this.disclosure.timer);
    this.disclosure.timer = 0;
    if (!this.thread.wbcDisclosureActive) return;
    this.thread.wbcDisclosureActive = false;
    this.thread.dispatchEvent(new this.win.Event("workbench:transcript-resize-end"));
  }

  restore(preserve = true, gradual = false) {
    if (this.restoreFrame) this.win.cancelAnimationFrame(this.restoreFrame);
    this.restoreFrame = 0;
    this.prepared = false;
    this.win.clearTimeout(this.timeout);
    this.timeout = 0;
    if (!this.frozen.length) return;
    const bottom = (0, this.isSticking)();
    const top = this.thread.getBoundingClientRect().top;
    const { anchor, offset } = this.captureAnchor(preserve, bottom, top);
    const batch = this.frozen.splice(0, gradual ? 32 : this.frozen.length);
    for (const row of batch) {
      row.removeAttribute('data-wbc-resize-frozen');
      row.style.removeProperty('--wbc-resize-row-height');
    }
    this.thread.wbcResizeActive = this.frozen.length > 0;
    if (!preserve) return;
    if (bottom) this.thread.scrollTop = this.thread.scrollHeight;
    else if (anchor?.isConnected) this.thread.scrollTop += anchor.getBoundingClientRect().top - offset;
    if (this.frozen.length) {
      // Restoring a thousand rows together causes a large style/layout task.
      // Release a bounded batch per frame, preserving the viewport each time.
      this.restoreFrame = this.win.requestAnimationFrame(() => this.restore(true, true));
      return;
    }
    this.thread.dispatchEvent(new this.win.Event("workbench:transcript-resize-end"));
    (0, this.onRestored)();
  }

  captureAnchor(preserve, bottom, top) {
    let anchor = preserve && !bottom
      ? Array.from(this.thread.children).find(row => !row.hasAttribute('data-wbc-resize-frozen')
        && row.getBoundingClientRect().bottom > top)
      : null;
    if (anchor) {
      const block = Array.from(anchor.querySelectorAll('.wbc-msg-body.markdown > *'))
        .find(node => !node.hasAttribute('data-wbc-resize-frozen') && node.getBoundingClientRect().bottom > top);
      if (block) anchor = block;
    }
    const offset = anchor ? anchor.getBoundingClientRect().top : 0;
    return { anchor, offset };
  }

  layoutTransition(event) {
    return event.propertyName === 'grid-template-columns'
      && (event.target === this.page || event.target.matches?.('.wbc-pane-layout'));
  }

  start(event) {
    if (!this.layoutTransition(event)) return;
    this.transitions.add(event.target);
    if (this.dragging || this.frozen.length) return;
    if (this.prepared) { this.prepared = false; return; }
    this.freeze();
  }

  freeze() {
    this.restore();
    // Keep selection, editors and rows currently yielding space to a PiP live.
    const selection = this.win.getSelection();
    if (selection && !selection.isCollapsed) return;
    const rows = this.thread.querySelectorAll(':scope > [data-wbc-thread-item]');
    const viewport = this.thread.getBoundingClientRect();
    const buffer = Math.max(viewport.height, 600);
    // Read all geometry before any style writes. Keep a full viewport on each
    // side live so wrapping near the edge cannot expose a frozen row.
    const candidates = [];
    function outside(rect) {
      return rect.bottom < viewport.top - buffer || rect.top > viewport.bottom + buffer;
    }
    for (const row of rows) {
      if (row.contains(this.doc.activeElement) || row.classList.contains('retry-clearing')) continue;
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
      this.frozen.push(row);
    }
    this.thread.wbcResizeActive = this.frozen.length > 0;
    // transitionend/cancel normally restores immediately. Bound the lifetime
    // if a host hides the page or replaces the transition mid-flight.
    this.timeout = this.win.setTimeout(() => this.restore(true, true), 1000);
  }

  end(event) {
    if (!this.layoutTransition(event)) return;
    this.transitions.delete(event.target);
    if (!this.dragging && !this.transitions.size) this.restore(true, true);
  }

  interrupt() { this.dragging = false; this.transitions.clear(); this.restore(); }

  resizePhase(event) {
    const phase = event.detail?.phase;
    if (phase === 'start' || event.type === 'workbench:split-resize-start') {
      this.freeze();
      this.dragging = true;
      // A held separator can remain stationary for any length of time.
      // Its explicit end/cancel/blur owns restoration, not the sidebar timer.
      this.win.clearTimeout(this.timeout);
      this.timeout = 0;
    } else if (phase === 'end' || event.type === 'workbench:split-resize-end') {
      this.dragging = false;
      // React commits the persisted size after the pointer handler returns.
      if (this.restoreFrame) this.win.cancelAnimationFrame(this.restoreFrame);
      this.restoreFrame = this.win.requestAnimationFrame(() => this.restore(true, true));
    }
  }

  cancelDrag() {
    if (this.dragging) this.resizePhase({detail: {phase: 'end'}});
  }

  prepare() { this.freeze(); this.prepared = this.frozen.length > 0; }

  prepareHandoff() {
    this.prepare();
    if (!this.conversationId || !this.frozen.length) return;
    const rows = Array.from(this.thread.querySelectorAll(':scope > [data-wbc-thread-item]'));
    let layouts = pendingLayouts.get(this.page);
    if (!layouts) pendingLayouts.set(this.page, layouts = new Map());
    const snapshot = {
      count: rows.length,
      heights: rows.map(row => row.hasAttribute('data-wbc-resize-frozen')
        ? row.style.getPropertyValue('--wbc-resize-row-height') : null),
    };
    layouts.set(this.conversationId, snapshot);
    this.win.setTimeout(() => {
      if (layouts.get(this.conversationId) === snapshot) layouts.delete(this.conversationId);
    }, 1000);
  }

  beforeInteraction(event) {
    const control = event.target?.closest?.('.workbench-sidebar-collapse-control, .wbc-side-hide-btn');
    if (control && this.page.contains(control) && (event.type !== 'keydown' || event.key === 'Enter' || event.key === ' ')) {
      // Run before React changes the grid class, while old geometry is clean.
      // Waiting for transitionrun pays one full-history layout first.
      this.prepare();
    } else this.restore();
  }

  dispose() {
    this.win.clearTimeout(this.disclosure.timer);
    this.thread.wbcDisclosureActive = false;
    this.restore(false);
    for (const [target, type, handler, options] of this.listeners) {
      if (options === true) target.removeEventListener(type, handler, true);
      else target.removeEventListener(type, handler);
    }
  }
}

export function protectTranscriptResize(thread, page, isSticking, onRestored = () => {}, conversationId = "") {
  const owner = new TranscriptResizeProtection(thread, page, isSticking, onRestored, conversationId);
  return () => owner.dispose();
}
