// Tour state machine + completion persistence. The store is framework-free;
// the React host (host.jsx) subscribes and renders center/spotlight.
// Completion is plain localStorage state, same convention as the welcome
// gate (`cyrene-workbench-welcomed`) — no backend round-trip.
(function (root) {
  "use strict";

  var phase = "idle";            // idle | center | running
  var currentGuideId = null;
  var currentStepIndex = 0;
  var listeners = [];

  function emit() {
    listeners.slice().forEach(function (listener) {
      try { listener(); } catch (error) {
        console.error("Cyrene: tour subscriber failed", error);
      }
    });
  }

  function subscribe(listener) {
    listeners.push(listener);
    return function unsubscribe() {
      listeners = listeners.filter(function (item) { return item !== listener; });
    };
  }

  function storageKey(guideId) {
    return "cyrene-tour-" + guideId;
  }

  function readProgress(guideId) {
    try {
      var raw = localStorage.getItem(storageKey(guideId));
      if (raw) return JSON.parse(raw);
    } catch (e) {}
    return null;
  }

  function writeProgress(guideId, progress) {
    try {
      localStorage.setItem(storageKey(guideId), JSON.stringify(progress));
    } catch (e) {}
  }

  function open(guideId) {
    currentGuideId = guideId || currentGuideId;
    currentStepIndex = 0;
    phase = "center";
    emit();
  }

  function close() {
    if (phase === "idle") return;
    phase = "idle";
    currentGuideId = null;
    currentStepIndex = 0;
    emit();
  }

  function start(guideId, stepIndex) {
    var guide = root.CyreneUI.require("tour-guides").find(guideId);
    if (!guide) return;
    currentGuideId = guideId;
    currentStepIndex = typeof stepIndex === "number"
      ? Math.max(0, Math.min(stepIndex, guide.steps.length - 1))
      : 0;
    phase = "running";
    emit();
  }

  // Leave the guide without marking it complete; progress (last step) is kept.
  function stop() {
    if (currentGuideId) {
      var progress = readProgress(currentGuideId) || {};
      progress.lastStep = currentStepIndex;
      writeProgress(currentGuideId, progress);
    }
    phase = "idle";
    currentGuideId = null;
    currentStepIndex = 0;
    emit();
  }

  function next() {
    var guide = currentGuide();
    if (!guide) return;
    if (currentStepIndex >= guide.steps.length - 1) {
      finish();
      return;
    }
    currentStepIndex += 1;
    emit();
  }

  function prev() {
    if (currentStepIndex <= 0) return;
    currentStepIndex -= 1;
    emit();
  }

  function jump(index) {
    var guide = currentGuide();
    if (!guide) return;
    currentStepIndex = Math.max(0, Math.min(index, guide.steps.length - 1));
    emit();
  }

  function finish() {
    if (!currentGuideId) return;
    var progress = readProgress(currentGuideId) || {};
    progress.completedAt = new Date().toISOString();
    progress.lastStep = currentStepIndex;
    writeProgress(currentGuideId, progress);
    phase = "idle";
    currentGuideId = null;
    currentStepIndex = 0;
    emit();
  }

  function currentGuide() {
    if (!currentGuideId) return null;
    return root.CyreneUI.require("tour-guides").find(currentGuideId);
  }

  function snapshot() {
    var guide = currentGuide();
    return {
      phase: phase,
      guideId: currentGuideId,
      guide: guide,
      stepIndex: currentStepIndex,
      step: guide ? guide.steps[currentStepIndex] : null,
      total: guide ? guide.steps.length : 0,
    };
  }

  function isDone(guideId) {
    var progress = readProgress(guideId);
    return !!(progress && progress.completedAt);
  }

  function setDone(guideId, done) {
    var progress = readProgress(guideId) || {};
    if (done) {
      progress.completedAt = progress.completedAt || new Date().toISOString();
    } else {
      delete progress.completedAt;
    }
    writeProgress(guideId, progress);
    emit();
  }

  function catalog() {
    return root.CyreneUI.require("tour-guides").catalog().map(function (module) {
      return {
        id: module.id,
        labelKey: module.labelKey,
        guides: module.guides.map(function (guide) {
          return {
            id: guide.id,
            titleKey: guide.titleKey,
            descKey: guide.descKey,
            minutes: guide.minutes,
            total: guide.steps.length,
            done: isDone(guide.id),
          };
        }),
      };
    });
  }

  var service = {
    open: open,
    close: close,
    start: start,
    stop: stop,
    next: next,
    prev: prev,
    jump: jump,
    finish: finish,
    snapshot: snapshot,
    subscribe: subscribe,
    isDone: isDone,
    setDone: setDone,
    catalog: catalog,
  };
  root.CyreneUI.register("tour", service);
})(window);
