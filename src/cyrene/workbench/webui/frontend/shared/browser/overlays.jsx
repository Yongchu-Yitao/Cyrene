// Native WebContentsView instances live above the renderer's CSS stacking
// context. Keep a shared count of renderer overlays that must cover it, so a
// popover can safely overlap another modal without restoring the native view
// too early.
var wbBrowserOverlayCount = 0;
var wbBrowserOverlayObscured = false;
var wbBrowserOverlayTransition = 0;
function wbSetBrowserOverlayObscured(delta) {
  wbBrowserOverlayCount = Math.max(0, wbBrowserOverlayCount + delta);
  var obscured = wbBrowserOverlayCount > 0;
  var forceRestore = delta === 0 && wbBrowserOverlayCount === 0;
  if (!forceRestore && obscured === wbBrowserOverlayObscured) return;
  wbBrowserOverlayObscured = obscured;
  var transition = wbBrowserOverlayTransition + 1;
  wbBrowserOverlayTransition = transition;
  var bridge = window.cyrene && window.cyrene.browser;

  function setNativeObscured(value) {
    if (transition !== wbBrowserOverlayTransition) return Promise.resolve(null);
    if (!bridge || typeof bridge.setObscured !== "function") return Promise.resolve(null);
    return bridge.setObscured(value).catch(function (err) {
      console.error("setObscured failed", err);
      return null;
    });
  }

  if (obscured) {
    var captureStarted = false;
    var nativeHidden = false;
    function hideNativeAfterPreview() {
      if (nativeHidden || transition !== wbBrowserOverlayTransition) return;
      nativeHidden = true;
      setNativeObscured(true);
    }
    // A native WebContentsView cannot be covered by renderer CSS. Ask the
    // viewport to capture and paint its current frame first; only its onReady
    // callback may hide the native layer. This keeps the page visually stable
    // while renderer menus render above the bitmap proxy.
    window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
      detail: {
        obscured: true,
        preview: true,
        onCaptureStarted: function () { captureStarted = true; },
        onReady: hideNativeAfterPreview,
      },
    }));
    // No mounted native viewport accepted the preview request. There is no
    // frame to preserve, so fall back to the ordinary authoritative guard.
    if (!captureStarted) hideNativeAfterPreview();
    return;
  }

  // Re-enable the native compositor before asking the renderer proxy to fade
  // away. The viewport keeps its screenshot mounted until a live frame at the
  // current bounds is confirmed, avoiding a white flash in the other direction.
  setNativeObscured(false).finally(function () {
    if (transition !== wbBrowserOverlayTransition) return;
    window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
      detail: { obscured: false },
    }));
  });
}

function wbResetBrowserOverlayObscured() {
  wbBrowserOverlayCount = 0;
  wbSetBrowserOverlayObscured(0);
}
// Other classic-script bundles (chat composer and shared feedback host) render
// overlays too. Register the reference-counted coordinator instead of creating
// an ad-hoc browser global or letting each surface race a boolean call.
window.CyreneUI.browserOverlays = window.CyreneUI.register("browser-overlays", {
  adjust: wbSetBrowserOverlayObscured,
});

export { wbResetBrowserOverlayObscured, wbSetBrowserOverlayObscured }
