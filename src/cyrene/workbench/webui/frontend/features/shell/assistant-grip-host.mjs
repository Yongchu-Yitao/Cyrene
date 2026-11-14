export function assistantMenuBounds(anchor, viewport) {
  const pane = anchor?.pane || { left: 0, top: 0, right: viewport.width, bottom: viewport.height };
  const leftEdge = Math.max(0, pane.left) + 12;
  const rightEdge = Math.min(viewport.width, pane.right) - 12;
  const bottom = Math.min(viewport.height, pane.bottom) - 12;
  const topEdge = Math.max(0, pane.top) + 12;
  const width = Math.max(0, Math.min(350, rightEdge - leftEdge));
  // Ordinary pane grips occupy a 34px strip. Align the menu's top edge with
  // their centre line, independently of the taller animated logo slot.
  const top = Math.min(bottom, Math.max(topEdge, pane.top + 17));
  const left = Math.max(leftEdge, Math.min(rightEdge - width, (anchor?.x ?? leftEdge) - width / 2));
  return { left, top, width, maxHeight: Math.max(0, bottom - top) };
}

function assistantGripPosition(anchor, { loading, welcome, open, logo }) {
  if (loading) return { x: (anchor.pane.left + anchor.pane.right) / 2, y: (anchor.pane.top + anchor.pane.bottom) / 2 };
  if (open && logo) return logo;
  if (welcome && anchor.welcome) return anchor.welcome;
  return anchor;
}

export function assistantGripPresentation(anchor, { loading, welcome, open, logo, mounted, arriving }) {
  const position = assistantGripPosition(anchor, { loading, welcome, open, logo });
  const slot = welcome ? anchor.welcome : null;
  const welcomeScale = slot && !open ? slot.size / 36 : 1;
  const brand = slot?.brand;
  const states = { open, travelling: mounted, loading, welcome, arriving };
  const className = 'aim-root' + Object.entries(states).filter(([, active]) => active).map(([name]) => ' is-' + name).join('');
  return { position, welcomeScale, brand, className };
}

export function observeAssistantGrip(card, grip, onChange, win = window, welcome = null) {
  const ancestors = [];
  for (let node = card; node; node = node.parentElement) ancestors.push(node);
  for (let node = welcome?.parentElement; node && node !== card; node = node.parentElement) ancestors.push(node);
  let frame = null, trackingUntil = 0, disposed = false;
  let previous = '';
  let theme = null;
  function readTheme() {
    if (theme) return theme;
    const style = win.getComputedStyle(card);
    theme = {};
    for (let i = 0; i < style.length; i++) {
      const key = style[i];
      if (/^--(wb|wbc)-/.test(key)) theme[key] = style.getPropertyValue(key);
    }
    theme.fontFamily = style.fontFamily;
    theme.colorScheme = style.colorScheme;
    return theme;
  }
  function measure() {
    if (disposed) return;
    const rect = grip.getBoundingClientRect(), pane = card.getBoundingClientRect();
    const gripStyle = win.getComputedStyle(grip);
    const visible = card.isConnected && !card.closest('[hidden],[inert],[aria-hidden="true"]')
      && gripStyle.visibility !== 'hidden' && gripStyle.display !== 'none'
      && rect.width > 0 && rect.height > 0 && pane.bottom > 0 && pane.right > 0
      && pane.top < win.innerHeight && pane.left < win.innerWidth;
    const next = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, bottom: rect.bottom,
      pane: { left: pane.left, top: pane.top, right: pane.right, bottom: pane.bottom }, theme: readTheme(), visible };
    if (welcome?.isConnected) {
      const slot = welcome.getBoundingClientRect();
      next.welcome = { x: slot.left + slot.width / 2, y: slot.top + slot.height / 2, size: slot.width };
      const name = welcome.nextElementSibling;
      if (name) {
        const rect = name.getBoundingClientRect(), style = win.getComputedStyle(name);
        next.welcome.brand = { text: name.textContent, left: rect.left, top: rect.top,
          font: style.font, letterSpacing: style.letterSpacing, color: style.color };
      }
    }
    const encoded = JSON.stringify(next);
    if (encoded !== previous) { previous = encoded; onChange(next); }
  }
  function tick(time) {
    frame = null; measure();
    if (!disposed && time < trackingUntil) frame = win.requestAnimationFrame(tick);
  }
  function schedule() { if (!disposed && frame === null) frame = win.requestAnimationFrame(tick); }
  function invalidateTheme() { theme = null; schedule(); }
  function affectsGrip(event) { return event.target === grip || ancestors.includes(event.target); }
  function followTransition(event) {
    // Decorative logo animations and transitions in unrelated/hidden pages
    // must not keep this geometry reader running on every frame.
    if (!affectsGrip(event)) return;
    trackingUntil = win.performance.now() + 650; schedule();
  }
  function finishTransition(event) { if (affectsGrip(event)) schedule(); }
  const resize = new win.ResizeObserver(schedule);
  ancestors.forEach(node => resize.observe(node)); resize.observe(grip);
  if (welcome) resize.observe(welcome);
  // Theme tokens change with ancestor attributes, not with every interpolated
  // pixel of a sidebar transition. Reuse the snapshot while following motion.
  const mutations = new win.MutationObserver(invalidateTheme);
  ancestors.forEach(node => mutations.observe(node, { attributes: true, childList: true,
    attributeFilter: ['class', 'style', 'hidden', 'inert', 'aria-hidden', 'data-theme', 'data-performance-mode'] }));
  const events = [['resize', schedule], ['scroll', schedule], ['transitionrun', followTransition],
    ['transitionend', finishTransition], ['animationstart', followTransition], ['animationend', finishTransition],
    ['cyrene:performance-mode', invalidateTheme], ['workbench:split-resize-end', schedule]];
  events.forEach(([name, fn]) => win.addEventListener(name, fn, true));
  measure();
  return () => {
    disposed = true; resize.disconnect(); mutations.disconnect();
    if (frame !== null) win.cancelAnimationFrame(frame);
    events.forEach(([name, fn]) => win.removeEventListener(name, fn, true));
  };
}

export function listenAssistantMenuDismiss(close, doc = document, win = window) {
  win.addEventListener('blur', close);
  win.addEventListener('cyrene:plugin-view-interaction', close);
  function visibility() { if (doc.hidden) close(); }
  doc.addEventListener('visibilitychange', visibility);
  return () => {
    win.removeEventListener('blur', close);
    win.removeEventListener('cyrene:plugin-view-interaction', close);
    doc.removeEventListener('visibilitychange', visibility);
  };
}
