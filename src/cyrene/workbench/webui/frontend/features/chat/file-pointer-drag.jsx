import { wbcStartFileDrag } from "./file-resources.jsx"

function wbcUsesFilePointerDrag() {
  return !!(window.cyrene
    && document.documentElement.getAttribute("data-platform") === "darwin");
}

function wbcCopyFileDragAppearance(sourceNode, cloneNode) {
  if (!sourceNode || !cloneNode || !window.getComputedStyle) return;
  var computed = window.getComputedStyle(sourceNode);
  for (var index = 0; index < computed.length; index += 1) {
    var property = computed[index];
    cloneNode.style.setProperty(
      property,
      computed.getPropertyValue(property),
      computed.getPropertyPriority(property)
    );
  }
  var sourceChildren = sourceNode.children || [];
  var cloneChildren = cloneNode.children || [];
  for (var childIndex = 0; childIndex < sourceChildren.length; childIndex += 1) {
    wbcCopyFileDragAppearance(sourceChildren[childIndex], cloneChildren[childIndex]);
  }
}

function wbcFilePointerDropSession(source) {
  var transfer = new DataTransfer();
  var target = null;
  function send(node, type, point, relatedTarget) {
    return !node.dispatchEvent(new DragEvent(type, {
      bubbles: true, cancelable: true, dataTransfer: transfer, relatedTarget: relatedTarget,
      clientX: point.clientX, clientY: point.clientY,
    }));
  }
  function move(point) {
    var next = document.elementFromPoint(point.clientX, point.clientY);
    if (target !== next) {
      if (target) send(target, "dragleave", point, next);
      if (next) send(next, "dragenter", point, target);
      target = next;
    }
    return target && send(target, "dragover", point);
  }
  return {
    start: function (point) { send(source, "dragstart", point); },
    move: move,
    finish: function (point) {
      if (point.type === "pointerup" && move(point)) send(target, "drop", point);
      if (target && target.isConnected) send(target, "dragleave", point, null);
      send(source.isConnected ? source : document, "dragend", point);
    },
  };
}

function wbcStartFilePointerDrag(event) {
  // On macOS Electron an HTML drag can be converted into a native window drag
  // as soon as it reaches the titlebar. Docked DevTools rebuilds that hit-test
  // surface, masking the bug. Keep the pointer in the renderer and send the
  // usual resource drag events to the shelf, panes and composer ourselves.
  if (!event || event.button !== 0 || event.isPrimary === false || !wbcUsesFilePointerDrag()) return;
  var source = event.currentTarget;
  var interactive = event.target && event.target.closest
    ? event.target.closest("button, a, input, textarea, select, [contenteditable='true']")
    : null;
  if (!source || (interactive && !interactive.hasAttribute("data-file-drag-handle"))) return;
  if (!interactive) event.preventDefault();
  var pointerId = event.pointerId;
  var startX = event.clientX;
  var startY = event.clientY;
  var dropSession = null;
  var finished = false;
  var ghost = null;

  function beginPointerDrag(moveEvent) {
    dropSession = wbcFilePointerDropSession(source);
    // Capture only after crossing the drag threshold. A normal press and
    // release still clicks the preview button; a drag releases on the card.
    if (source.setPointerCapture) {
      try { source.setPointerCapture(pointerId); } catch (e) {}
      source.addEventListener("lostpointercapture", finishPointerDrag);
    }
    ghost = source.cloneNode(true);
    var rect = source.getBoundingClientRect();
    // Copy before dimming the source so the floating card stays visually
    // identical to its resting state, including inherited typography, theme
    // tokens and every nested action/icon style.
    wbcCopyFileDragAppearance(source, ghost);
    ghost.classList.add("wbc-file-pointer-drag-ghost");
    ghost.setAttribute("aria-hidden", "true");
    ghost.inert = true;
    ghost.style.position = "fixed";
    ghost.style.zIndex = "2147483647";
    ghost.style.margin = "0";
    ghost.style.pointerEvents = "none";
    ghost.style.left = rect.left + "px";
    ghost.style.top = rect.top + "px";
    ghost.style.width = rect.width + "px";
    ghost.style.height = rect.height + "px";
    source.classList.add("wbc-file-pointer-drag-source");
    document.body.appendChild(ghost);
    dropSession.start(moveEvent);
  }

  function movePointerDrag(moveEvent) {
    if (moveEvent.pointerId !== pointerId) return;
    var dx = moveEvent.clientX - startX;
    var dy = moveEvent.clientY - startY;
    if (!dropSession) {
      if ((dx * dx) + (dy * dy) < 16) return;
      beginPointerDrag(moveEvent);
    }
    dropSession.move(moveEvent);
    if (ghost) ghost.style.transform = "translate3d(" + dx + "px," + dy + "px,0)";
  }

  function clearPointerDrag() {
    if (finished) return;
    finished = true;
    window.removeEventListener("pointermove", movePointerDrag);
    window.removeEventListener("pointerup", finishPointerDrag);
    window.removeEventListener("pointercancel", finishPointerDrag);
    window.removeEventListener("blur", finishPointerDrag);
    window.removeEventListener("keydown", cancelOnEscape);
    source.removeEventListener("lostpointercapture", finishPointerDrag);
    if (source.releasePointerCapture) {
      try { source.releasePointerCapture(pointerId); } catch (e) {}
    }
    source.classList.remove("wbc-file-pointer-drag-source");
    if (ghost && ghost.parentNode) ghost.parentNode.removeChild(ghost);
  }

  function finishPointerDrag(finishEvent) {
    if (finished) return;
    if (finishEvent && finishEvent.pointerId != null && finishEvent.pointerId !== pointerId) return;
    clearPointerDrag();
    if (dropSession) dropSession.finish(finishEvent);
  }

  function cancelOnEscape(keyEvent) {
    if (keyEvent.key === "Escape") finishPointerDrag(keyEvent);
  }
  window.addEventListener("pointermove", movePointerDrag);
  window.addEventListener("pointerup", finishPointerDrag);
  window.addEventListener("pointercancel", finishPointerDrag);
  window.addEventListener("blur", finishPointerDrag);
  window.addEventListener("keydown", cancelOnEscape);
}

function wbcFileDragHandlers(file) {
  return {
    draggable: wbcUsesFilePointerDrag() ? undefined : "true",
    onDragStart: function (event) { wbcStartFileDrag(event, file); },
    onPointerDown: wbcStartFilePointerDrag,
  };
}

export { wbcFileDragHandlers }
