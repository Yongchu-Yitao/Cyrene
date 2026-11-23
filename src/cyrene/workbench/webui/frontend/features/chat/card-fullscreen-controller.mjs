const activePanes = new WeakMap();

// Pinned assistant-card frames are individual scroll items, not split panes.
export function cardFullscreenPane(element) {
  return element.parentElement?.closest('.wbc-pane-card:not(.wbc-pane-card-assistant-card), .wbc-pinned-card-column, .wbc-detached-pane-content')
    || element.parentElement?.closest('.wbc-main, .wbc-side-agent-split, .wbc-pane-column');
}

export function enterCardFullscreen(element, onClose) {
  const pane = element && cardFullscreenPane(element);
  if (!pane || !element.showPopover) return null;
  activePanes.get(pane)?.();
  const doc = element.ownerDocument, win = doc.defaultView;
  const focus = doc.activeElement;
  if (!pane.isConnected || !pane.getClientRects().length) return null;
  // Read both boxes before changing the DOM to avoid an extra synchronous layout.
  const paneBox = pane.getBoundingClientRect();
  const rect = element.getBoundingClientRect(), margin = win.getComputedStyle(element).margin;
  // Reserve the inline position without moving/remounting the live iframe.
  const placeholder = doc.createElement('div');
  placeholder.setAttribute('aria-hidden', 'true');
  Object.assign(placeholder.style, { width: rect.width + 'px', height: rect.height + 'px',
    margin, flexShrink: '0', pointerEvents: 'none' });
  element.before(placeholder);
  element.setAttribute('popover', 'manual');
  element.setAttribute('data-card-fullscreen', '');
  let observer, animation, closed = false, previousBounds = '';
  function measure(box) {
    if (!pane.isConnected || (!box && !pane.getClientRects().length)) { exit(); return; }
    box ||= pane.getBoundingClientRect();
    const bounds = [box.left, box.top, box.width, box.height];
    if (bounds.join() === previousBounds) return;
    previousBounds = bounds.join();
    ['left', 'top', 'width', 'height'].forEach((key, i) => element.style.setProperty('--card-fullscreen-' + key, bounds[i] + 'px'));
  }
  function scheduleMeasure() {
    win.cancelAnimationFrame(animation);
    animation = win.requestAnimationFrame(() => measure());
  }
  function onKey(event) {
    if (event.key === 'Escape' && element.contains(doc.activeElement)) {
      event.preventDefault(); event.stopPropagation(); exit();
    }
  }
  function onMessage(event) {
    if (event.origin === 'null' && event.data?.channel === 'cyrene-card-fullscreen-exit'
      && [...element.querySelectorAll('iframe')].some(frame => frame.contentWindow === event.source)) exit();
  }
  function onToggle(event) { if (event.newState === 'closed') exit(); }
  function exit(restoreFocus = true) {
    if (closed) return;
    closed = true;
    observer?.disconnect(); win.cancelAnimationFrame(animation);
    win.removeEventListener('resize', scheduleMeasure);
    doc.removeEventListener('scroll', scheduleMeasure, true);
    doc.removeEventListener('keydown', onKey, true);
    win.removeEventListener('message', onMessage);
    element.removeEventListener('toggle', onToggle);
    if (element.matches(':popover-open')) element.hidePopover();
    element.removeAttribute('popover');
    element.removeAttribute('data-card-fullscreen');
    for (const key of ['left', 'top', 'width', 'height']) element.style.removeProperty('--card-fullscreen-' + key);
    placeholder.remove();
    activePanes.delete(pane);
    if (restoreFocus) {
      onClose();
      if (focus?.isConnected) focus.focus({ preventScroll: true });
    }
  }
  activePanes.set(pane, exit);
  measure(paneBox);
  if (closed) return null;
  try { element.showPopover(); } catch { exit(); return null; }
  observer = new win.ResizeObserver(scheduleMeasure);
  for (let ancestor = pane; ancestor; ancestor = ancestor.parentElement) observer.observe(ancestor);
  win.addEventListener('resize', scheduleMeasure);
  doc.addEventListener('scroll', scheduleMeasure, true);
  doc.addEventListener('keydown', onKey, true);
  win.addEventListener('message', onMessage);
  element.addEventListener('toggle', onToggle);
  element.querySelector('.wbc-card-fullscreen-button')?.focus({ preventScroll: true });
  return exit;
}
