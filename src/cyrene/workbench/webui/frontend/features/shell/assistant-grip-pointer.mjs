import { createGripLogoDrag } from '../chat/grip-logo-drag.mjs';

// Keep the real pane handle as the drag source, even though the interactive
// logo lives in a portal. The existing pane controller owns moving/detaching.
export function createAssistantGripPointer(read, win = window, spring = createGripLogoDrag) {
  let pointer = null, drag = null;
  function release(event = {}) {
    const p = pointer;
    if (!p || (event.pointerId != null && p.id !== event.pointerId)) return;
    pointer = null;
    drag?.finish();
    p.button.dataset.wbcPointerDragged = String(p.moved);
    if (p.started && event.type !== 'pointerup') p.actions.onDragEnd?.(event, { cancel: true });
    if (p.button.hasPointerCapture?.(p.id)) p.button.releasePointerCapture(p.id);
    read().onPaneDragging(false);
    win.removeEventListener('blur', cancel);
    win.removeEventListener('pointerup', release);
    win.removeEventListener('pointercancel', release);
  }
  function cancel() { release({ type: 'pointercancel' }); }
  function down(event) {
    if (event.button !== 0) return;
    cancel(); drag?.destroy(); drag = null;
    const { grip, slot, panel, open, actions = {}, onPaneDragging } = read();
    const button = event.currentTarget;
    const paneDrag = !open && !actions.singlePane && !!actions.onDragStart && !!grip;
    pointer = { id: event.pointerId, x: event.clientX, y: event.clientY,
      button, grip, actions, paneDrag, moved: false, started: false };
    button.dataset.wbcPointerDragged = 'false';
    if (paneDrag) {
      const rect = grip.getBoundingClientRect();
      grip.dataset.wbcDragClientX = String(event.clientX);
      grip.dataset.wbcDragClientY = String(event.clientY);
      grip.dataset.wbcDragHandleX = String(event.clientX - rect.left);
      grip.dataset.wbcDragHandleY = String(event.clientY - rect.top);
    } else {
      drag = spring(slot, event, win, open ? panel : grip?.closest('.wbc-pane-card'));
    }
    onPaneDragging(false);
    button.setPointerCapture?.(event.pointerId);
    win.addEventListener('blur', cancel);
    win.addEventListener('pointerup', release);
    win.addEventListener('pointercancel', release);
    event.preventDefault();
  }
  function move(event) {
    const p = pointer;
    if (!p || p.id !== event.pointerId) return;
    if (!p.moved && Math.abs(event.clientX - p.x) + Math.abs(event.clientY - p.y) > 4) p.moved = true;
    if (!p.moved) return;
    p.button.dataset.wbcPointerDragged = 'true';
    if (!p.paneDrag) { drag?.move(event); return; }
    if (p.started) return;
    p.started = true;
    // The pane preview is a snapshot, as before. Do not leave a second live logo.
    const visual = p.grip.querySelector('.wbc-side-split-grip-bar-visual');
    const artwork = read().slot.querySelector('.cyrene-motion')?.cloneNode(true);
    if (artwork && visual) visual.appendChild(artwork);
    try {
      p.actions.onDragStart({ ...event, currentTarget: p.grip, pointerCaptureTarget: p.button,
        preventDefault: () => event.preventDefault(), stopPropagation: () => event.stopPropagation() });
      read().onPaneDragging(true);
    } finally { artwork?.remove(); }
  }
  return { down, move, release, cancel, destroy() { cancel(); drag?.destroy(); drag = null; } };
}
