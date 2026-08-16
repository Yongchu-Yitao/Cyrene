// TourHost: the only React surface the shell mounts. It subscribes to the
// tour store, renders the tutorial center (phase=center) or the spotlight
// (phase=running), runs per-step navigation, and pairs the browser overlay
// obscuring (native WebContentsView sits above the renderer DOM).
(function (root) {
  "use strict";

  const { useState, useEffect, useRef } = React;

  var tour = function () { return root.CyreneUI.require("tour"); };

  function TourHost({ setOverlayObscured, onOpenPage, onOpenSettings }) {
    var [snapshot, setSnapshot] = useState(tour().snapshot());
    var stepNavRef = useRef("");

    useEffect(function () {
      return tour().subscribe(function () {
        setSnapshot(tour().snapshot());
      });
    }, []);

    // Obscure the native browser layer while the center or spotlight is up,
    // mirroring the help center / search overlay pairing (counter-based).
    var overlayActive = snapshot.phase === "center" || snapshot.phase === "running";
    useEffect(function () {
      if (!overlayActive) return undefined;
      if (typeof setOverlayObscured === "function") setOverlayObscured(1);
      return function () {
        if (typeof setOverlayObscured === "function") setOverlayObscured(-1);
      };
    }, [overlayActive]);

    // Per-step side effects: navigate to a page or open settings before the
    // spotlight resolves the target (targets live inside the page/settings).
    var navKey = snapshot.phase === "running" && snapshot.step
      ? String(snapshot.stepIndex) + ":" + (snapshot.step.navigate && snapshot.step.navigate.page || "") + ":" + (snapshot.step.openSettings || "")
      : "";
    useEffect(function () {
      if (snapshot.phase !== "running" || !snapshot.step) return;
      if (snapshot.step.navigate && typeof onOpenPage === "function") {
        onOpenPage(snapshot.step.navigate.page);
      }
      if (snapshot.step.openSettings && typeof onOpenSettings === "function") {
        onOpenSettings(snapshot.step.openSettings);
      }
    }, [navKey]);

    // Escape always aborts a running guide (the center handles its own Esc).
    useEffect(function () {
      if (snapshot.phase !== "running") return undefined;
      function onKey(e) {
        if (e.key === "Escape") {
          e.preventDefault();
          tour().stop();
        }
      }
      window.addEventListener("keydown", onKey, true);
      return function () { window.removeEventListener("keydown", onKey, true); };
    }, [snapshot.phase]);

    // Render into a portal on <body>: fixed-positioning children then resolve
    // against the viewport no matter what transforms the shell uses, so the
    // highlight aligns exactly with the target element.
    if (snapshot.phase === "center") {
      var Center = root.CyreneUI.require("tour-center").Overlay;
      return ReactDOM.createPortal(
        React.createElement(Center, {
          initialGuideId: snapshot.guideId,
          onClose: function () { tour().close(); },
          onStart: function (guideId) { tour().start(guideId, 0); },
        }),
        document.body
      );
    }

    if (snapshot.phase === "running" && snapshot.guide && snapshot.step) {
      var Spotlight = root.CyreneUI.require("tour-spotlight").Overlay;
      return ReactDOM.createPortal(
        React.createElement(Spotlight, {
          guide: snapshot.guide,
          step: snapshot.step,
          index: snapshot.stepIndex,
          total: snapshot.total,
          onNext: function () { tour().next(); },
          onPrev: function () { tour().prev(); },
          onStop: function () { tour().stop(); },
          onFinish: function () { tour().finish(); },
          onSkipStep: function () {
            // Skip the current step: jump past it (finish when it was the last).
            var state = tour().snapshot();
            if (state.stepIndex >= state.total - 1) tour().finish();
            else tour().jump(state.stepIndex + 1);
          },
        }),
        document.body
      );
    }

    return null;
  }

  root.CyreneUI.register("tour-host", { Host: TourHost });
})(window);
