// Launch-screen readiness adapter. The HTML implementation remains synchronous
// so it can gate the first paint; UI surfaces consume it through this service.
(function (root) {
  "use strict";
  var platform = root.CyreneUI;
  if (!platform) throw new Error("CyreneUI platform registry must load first");

  var service = {
    markReady: function () {
      root.dispatchEvent(new CustomEvent("cyrene:ready"));
    },
  };
  platform.readiness = platform.register("readiness", service);
})(window);
