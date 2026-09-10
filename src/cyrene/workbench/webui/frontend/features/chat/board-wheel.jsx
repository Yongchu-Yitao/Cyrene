export function handleBoardWheel(event) {
    var viewport = event.currentTarget;
    var deltaX = Number(event.deltaX || 0);
    var deltaY = Number(event.deltaY || 0);
    if (!viewport || Math.abs(deltaY) <= Math.abs(deltaX) || Math.abs(deltaY) < 1) return;
    var columnBody = event.target && event.target.closest ? event.target.closest(".wb-board-column-body") : null;
    if (columnBody && canScrollColumn(columnBody, deltaY)) return;
    var maxBoardLeft = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    if (!maxBoardLeft) return;
    var scale = wheelScale(event.deltaMode, viewport.clientWidth);
    var nextLeft = Math.max(0, Math.min(maxBoardLeft, viewport.scrollLeft + deltaY * scale));
    if (nextLeft === viewport.scrollLeft) return;
    event.preventDefault();
    viewport.scrollLeft = nextLeft;
  }

function canScrollColumn(columnBody, deltaY) {
  var maxColumnTop = Math.max(0, columnBody.scrollHeight - columnBody.clientHeight);
  return deltaY < 0 ? columnBody.scrollTop > 1 : columnBody.scrollTop < maxColumnTop - 1;
}

function wheelScale(mode, width) { return mode === 1 ? 16 : (mode === 2 ? width : 1); }
