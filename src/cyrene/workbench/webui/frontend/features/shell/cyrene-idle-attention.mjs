// Only geometry is observed. Composer text and conversation state are not read.
const composerSelector = '[data-cyrene-personal-composer="true"] > .wbc-composer .wbc-composer-textarea';

export function attachCyreneIdleAttention(svg, motion) {
  const doc = svg.ownerDocument, win = doc.defaultView;
  const anchor = svg.parentElement;
  let pointer = null, pointerAt = -Infinity, sampledAt = -Infinity, destroyed = false;
  const removers = [];
  const active = () => !destroyed && motion.state === 'idle' && !motion.resting && !doc.hidden;
  function listen(target, event, fn) {
    target.addEventListener(event, fn, { passive: true });
    removers.push(() => target.removeEventListener(event, fn));
  }
  function visible(element, decorative = false) {
    if (!element || !element.isConnected || element.closest(decorative ? '[hidden], [inert]' : '[hidden], [inert], [aria-hidden="true"]')) return false;
    const box = element.getBoundingClientRect();
    const css = win.getComputedStyle(element);
    return box.width > 0 && box.height > 0 && box.bottom > 0 && box.right > 0
      && box.top < win.innerHeight && box.left < win.innerWidth
      && css.visibility !== 'hidden' && css.display !== 'none';
  }
  function sample(force = false) {
    if (!active()) return;
    const now = win.performance.now();
    if (!force && now - sampledAt < 80) return;
    sampledAt = now;
    if (!visible(anchor, true)) { motion.setAttentionTargets({}); return; }
    const box = anchor.getBoundingClientRect();
    const direction = point => {
      const x = point.x - box.x - box.width / 2, y = point.y - box.y - box.height / 2;
      const length = Math.hypot(x, y) || 1;
      return { x: x / length, y: y / length };
    };
    const composer = [...doc.querySelectorAll(composerSelector)].find(element => visible(element));
    const inputBox = composer?.getBoundingClientRect();
    motion.setAttentionTargets({
      pointer: pointer && now - pointerAt < 3000 ? direction(pointer) : null,
      input: inputBox ? direction({ x: inputBox.x + inputBox.width * .55, y: inputBox.y + inputBox.height * .45 }) : null,
    });
  }
  function clear() { pointer = null; pointerAt = -Infinity; sampledAt = -Infinity; motion.setAttentionTargets({}); }
  listen(doc, 'pointermove', event => {
    if (!active() || event.pointerType === 'touch') return;
    pointer = { x: event.clientX, y: event.clientY }; pointerAt = win.performance.now();
  });
  listen(doc, 'pointerout', event => { if (!event.relatedTarget) clear(); });
  listen(win, 'blur', clear);
  listen(doc, 'visibilitychange', clear);
  function noticeInput(event) {
    if (!active() || !event.target?.matches?.(composerSelector) || !visible(event.target)) return;
    sample(true); motion.noticeInput();
  }
  listen(doc, 'focusin', noticeInput);
  listen(doc, 'input', noticeInput);
  return { sample, clear, destroy() { clear(); destroyed = true; removers.forEach(remove => remove()); } };
}
