// Require deliberate travel beyond the size floor so reaching the minimum
// during an ordinary resize never dismisses a pane.
export const RESIZE_CLOSE_OVERSHOOT = 48;

export function resizeCollapseEdge(size, minimum, maximum = Infinity) {
  if (size <= minimum - RESIZE_CLOSE_OVERSHOOT) return "minimum";
  if (size >= maximum + RESIZE_CLOSE_OVERSHOOT) return "maximum";
  return "";
}
