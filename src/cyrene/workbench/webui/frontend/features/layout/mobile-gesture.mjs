// Only dedicated edge strips initiate navigation, never scrollable content.
export function drawerSwipe(edge, open, dx, dy) {
  if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy) * 1.5) return null;
  if (open) return (open === 'left' && dx < 0) || (open === 'right' && dx > 0) ? '' : null;
  if (edge === 'left' && dx > 0) return 'left';
  if (edge === 'right' && dx < 0) return 'right';
  return null;
}
