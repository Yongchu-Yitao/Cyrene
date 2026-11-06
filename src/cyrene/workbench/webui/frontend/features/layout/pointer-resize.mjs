export function startPointerResize(event, options) {
  var handle = event.currentTarget;
  var pointerId = event.pointerId;
  var frame = 0;
  var finished = false;
  var collapsedEdge = "";
  var collapsePosition = 0;
  var preview = options.collapsePreview;
  var collapseMotion = null;
  var rowTemplate;
  function changePreview(change, closing) {
    var element = preview.element;
    var canAnimate = element.animate && window.getComputedStyle
      && !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    function geometry() {
      var style = window.getComputedStyle(element);
      // Computed tracks are pixels on both ends, even when authored tracks
      // mix minmax(), fr and px. This makes the collapse interpolable.
      return { gridTemplateColumns: style.gridTemplateColumns, gridTemplateRows: style.gridTemplateRows,
        columnGap: style.columnGap, rowGap: style.rowGap };
    }
    var before = canAnimate ? geometry() : null;
    if (collapseMotion) { collapseMotion.cancel(); collapseMotion = null; }
    if (closing && preview.kind === "row") rowTemplate = element.style.gridTemplateRows;
    change();
    if (preview.kind === "row") {
      element.style.gridTemplateRows = closing
        ? (collapsedEdge === "minimum" ? "0fr 1fr" : "1fr 0fr") : rowTemplate;
    }
    if (canAnimate) {
      collapseMotion = element.animate([before, geometry()], {
        duration: 240, easing: "cubic-bezier(.22, 1, .36, 1)",
      });
      // Reversing cancels the previous animation; cancellation is expected.
      collapseMotion.finished.catch(function () {});
    }
  }
  function coordinate(e) { return preview.axis === "y" ? e.clientY : e.clientX; }
  function restorePreview() {
    if (!collapsedEdge) return;
    changePreview(function () { preview.element.removeAttribute("data-resize-collapsed"); });
    collapsedEdge = "";
    if (options.resetCollapse) options.resetCollapse();
  }
  function paint() {
    frame = 0;
    options.paint();
  }
  function move(moveEvent) {
    if (finished || moveEvent.pointerId !== pointerId) return;
    // Sandboxed content may swallow pointerup. Finish on the next mouse move
    // once the primary button is no longer held, without closing anything.
    if (moveEvent.pointerType === "mouse" && !(moveEvent.buttons & 1)) { stop(moveEvent); return; }
    if (collapsedEdge) {
      var direction = preview.direction(collapsedEdge);
      var position = coordinate(moveEvent);
      // A small reverse drag reopens from the furthest closing position.
      if ((collapsePosition - position) * direction < 24) {
        if ((position - collapsePosition) * direction > 0) collapsePosition = position;
        return;
      }
      restorePreview();
    }
    var closeEdge = options.update(moveEvent);
    if (options.paint && !frame) frame = requestAnimationFrame(paint);
    if (options.close && closeEdge) {
      if (preview) {
        if (frame) { cancelAnimationFrame(frame); paint(); }
        collapsedEdge = closeEdge;
        collapsePosition = coordinate(moveEvent);
        changePreview(function () { preview.element.setAttribute("data-resize-collapsed", preview.kind + "-" + closeEdge); }, true);
      } else {
        stop(moveEvent);
        options.close(closeEdge);
      }
    }
  }
  function stop(stopEvent) {
    if (finished) return;
    if (stopEvent && Number.isFinite(stopEvent.pointerId) && stopEvent.pointerId !== pointerId) return;
    var closeEdge = stopEvent && stopEvent.type === "pointerup" ? collapsedEdge : "";
    if (!closeEdge) restorePreview();
    finished = true;
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    window.removeEventListener("pointercancel", stop);
    window.removeEventListener("blur", stop);
    handle.removeEventListener("lostpointercapture", stop);
    if (handle.hasPointerCapture && handle.hasPointerCapture(pointerId)) {
      try { handle.releasePointerCapture(pointerId); } catch (error) {}
    }
    if (frame) { cancelAnimationFrame(frame); paint(); }
    document.body.classList.remove(options.bodyClass);
    options.commit();
    if (closeEdge && options.close) {
      function confirmClose() {
        options.close(closeEdge);
        // Also clear the preview if a confirmation dialog declines closing.
        if (preview.kind === "row") preview.element.style.gridTemplateRows = rowTemplate;
        preview.element.removeAttribute("data-resize-collapsed");
      }
      // A quick release must not unmount the pane halfway through its exit.
      if (collapseMotion && collapseMotion.playState !== "finished") {
        collapseMotion.finished.then(confirmClose, function () {
          if (preview.kind === "row") preview.element.style.gridTemplateRows = rowTemplate;
          preview.element.removeAttribute("data-resize-collapsed");
        });
      } else confirmClose();
    }
  }
  if (handle.setPointerCapture) {
    try { handle.setPointerCapture(pointerId); } catch (error) {}
  }
  handle.addEventListener("lostpointercapture", stop);
  document.body.classList.add(options.bodyClass);
  options.start();
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
  window.addEventListener("pointercancel", stop);
  window.addEventListener("blur", stop);
}
