// Cross-feature Workbench navigation without ad-hoc window callbacks.
(function (root) {
  "use strict";
  var handler = null;
  var pendingSelection = null;

  var service = {
    setHandler: function (nextHandler) {
      handler = typeof nextHandler === "function" ? nextHandler : null;
      return function clearHandler() {
        if (handler === nextHandler) handler = null;
      };
    },
    navigate: function (target) {
      if (!handler) return false;
      handler(target || {});
      return true;
    },
    setPending: function (target) {
      pendingSelection = target || null;
    },
    getPending: function () {
      return pendingSelection;
    },
    clearPending: function (expected) {
      if (expected === undefined || pendingSelection === expected) {
        pendingSelection = null;
      }
    },
  };
  root.CyreneUI.navigation = root.CyreneUI.register("navigation", service);
})(window);
