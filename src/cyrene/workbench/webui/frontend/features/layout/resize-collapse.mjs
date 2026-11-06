export const RESIZE_CLOSE_OVERSHOOT = 48;
export const RESIZE_CLOSE_PAUSE_MS = 400;
const PAUSE_SLOP = 4;

// Hold the pane at its floor for a fixed dwell. Continued outward movement
// does not restart the hold; only travel AFTER the hold counts toward closing.
export function createResizeCollapseGuard(timers = globalThis) {
  let edge = "";
  let anchor = 0;
  let latest = 0;
  let furthest = 0;
  let armed = false;
  let timer = null;
  let disposed = false;
  function clearPause() {
    if (timer !== null) timers.clearTimeout(timer);
    timer = null;
  }
  function beginPause(size) {
    clearPause();
    anchor = furthest = latest = size;
    armed = false;
    if (edge) timer = timers.setTimeout(() => {
      timer = null;
      // Waiting never closes anything. Only a later outward move may close.
      anchor = furthest = latest;
      armed = true;
    }, RESIZE_CLOSE_PAUSE_MS);
  }
  function resizeCollapseEdge(size, minimum, maximum = Infinity) {
    if (disposed) return "";
    const nextEdge = size <= minimum ? "minimum" : size >= maximum ? "maximum" : "";
    if (!nextEdge || nextEdge !== edge) {
      edge = nextEdge;
      beginPause(size);
      return "";
    }
    latest = size;
    const retreat = edge === "minimum" ? size - furthest : furthest - size;
    if (retreat > PAUSE_SLOP) {
      beginPause(size);
      return "";
    }
    furthest = edge === "minimum" ? Math.min(furthest, size) : Math.max(furthest, size);
    const travel = edge === "minimum" ? anchor - size : size - anchor;
    if (armed) return travel >= RESIZE_CLOSE_OVERSHOOT ? edge : "";
    // Keep the latest pointer position for the timer to rebase from.
    // Restarting on every move would make continuous drag-to-close impossible.
    return "";
  }
  resizeCollapseEdge.reset = function () {
    clearPause();
    edge = "";
    armed = false;
  };
  resizeCollapseEdge.dispose = function () {
    disposed = true;
    clearPause();
  };
  return resizeCollapseEdge;
}
