export const RESIZE_CLOSE_OVERSHOOT = 32;

// Distance beyond the actual size limit is deliberate closing travel, even
// when a fast pointer skips over the boundary in a single event. Reversing a
// preview rebases that travel so it cannot immediately collapse again.
export function createResizeCollapseGuard() {
  let edge = "";
  let anchor = 0;
  let rebase = false;
  let disposed = false;
  function resizeCollapseEdge(size, minimum, maximum = Infinity) {
    if (disposed) return "";
    const nextEdge = size < minimum ? "minimum" : size > maximum ? "maximum" : "";
    if (!nextEdge) {
      edge = "";
      rebase = false;
      return "";
    }
    if (nextEdge !== edge) {
      edge = nextEdge;
      anchor = rebase ? size : edge === "minimum" ? minimum : maximum;
      rebase = false;
    }
    const travel = edge === "minimum" ? anchor - size : size - anchor;
    return travel >= RESIZE_CLOSE_OVERSHOOT ? edge : "";
  }
  resizeCollapseEdge.reset = function () {
    edge = "";
    rebase = true;
  };
  resizeCollapseEdge.dispose = function () { disposed = true; };
  return resizeCollapseEdge;
}
