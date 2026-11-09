// Live backdrop sampling is expensive while the rail and its underlying page
// both resize. Keep the resting glass, but use its neutral fill during motion.
const geometryProperties = new Set([
  'width', 'height', 'min-height', 'min-width', 'flex-basis',
  'grid-template-columns', 'grid-template-rows', 'transform',
  'left', 'margin-left', 'padding-left',
]);

export function startSidebarGlassMotion(grid) {
  const win = grid.ownerDocument.defaultView;
  if (win.matchMedia('(prefers-reduced-motion: reduce)').matches) return () => {};
  let active = true;
  let frame = 0;
  let timeout = 0;
  function finish() {
    if (!active) return;
    active = false;
    win.cancelAnimationFrame(frame);
    win.clearTimeout(timeout);
    grid.removeAttribute('data-sidebar-motion');
  }
  grid.setAttribute('data-sidebar-motion', 'true');
  // Covers hidden documents and an interrupted/unmounted animation. Normal
  // completion follows the actual CSS transitions, not a duplicated duration.
  timeout = win.setTimeout(finish, 1500);
  frame = win.requestAnimationFrame(() => {
    const transitions = grid.getAnimations({ subtree: true }).filter(animation =>
      geometryProperties.has(animation.transitionProperty));
    Promise.allSettled(transitions.map(animation => animation.finished)).then(finish);
  });
  return finish;
}
