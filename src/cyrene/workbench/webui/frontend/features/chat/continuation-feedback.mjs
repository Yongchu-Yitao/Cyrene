// Keep initial-send feedback visible while empty reply records arrive.
// Quiet handoffs still use a cancellable delay to avoid transient rows.
export function scheduleContinuationFeedback(immediate, visible, setVisible, target) {
  if (immediate) { setVisible(true); return; }
  if (visible) return;
  const timer = target.setTimeout(function () { setVisible(true); }, 400);
  return function () { target.clearTimeout(timer); };
}
