export function startPointerResize(event, options) {
  var handle = event.currentTarget;
  var pointerId = event.pointerId;
  var frame = 0;
  var finished = false;
  function paint() {
    frame = 0;
    options.paint();
  }
  function move(moveEvent) {
    if (finished || moveEvent.pointerId !== pointerId) return;
    // Sandboxed content may swallow pointerup. Finish on the next mouse move
    // once the primary button is no longer held, without closing anything.
    if (moveEvent.pointerType === "mouse" && !(moveEvent.buttons & 1)) { stop(moveEvent); return; }
    var closeEdge = options.update(moveEvent);
    if (options.paint && !frame) frame = requestAnimationFrame(paint);
    if (options.close && closeEdge) {
      stop(moveEvent);
      options.close(closeEdge);
    }
  }
  function stop(stopEvent) {
    if (finished) return;
    if (stopEvent && Number.isFinite(stopEvent.pointerId) && stopEvent.pointerId !== pointerId) return;
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
