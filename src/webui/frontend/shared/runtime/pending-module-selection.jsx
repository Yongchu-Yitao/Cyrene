function createPendingMemorySelection(navigation, workspaceRef, onSelect) {
  var pendingId = "";

  function capture(target) {
    var pending = target || navigation.getPending();
    if (!pending || pending.type !== "memory") return "";
    var targetWorkspace = String(pending.projectId || "");
    if (targetWorkspace && targetWorkspace !== String(workspaceRef.current || "")) return "";
    var id = String(pending.memId || pending.id || "");
    if (id) pendingId = id;
    return id;
  }

  function apply() {
    var pending = navigation.getPending();
    var id = pendingId || capture(pending);
    if (!id) return false;
    pendingId = "";
    onSelect(id);
    if (pending && pending.type === "memory"
        && (!pending.projectId || String(pending.projectId) === String(workspaceRef.current || ""))
        && String(pending.memId || pending.id || "") === id) {
      navigation.clearPending(pending);
    }
    return true;
  }

  return {
    apply: apply,
    capture: capture,
    captureAndApply: function (target) { return capture(target) ? apply() : false; },
    reset: function () { pendingId = ""; },
  };
}

function usePendingKnowledgeSelection(options) {
  var directId = React.useRef("");
  React.useEffect(function () {
    if (options.active === false) return undefined;
    var navigation = options.navigation;
    function apply(target) {
      var pending = target || navigation.getPending();
      if (!pending || pending.type !== "knowledge") return false;
      var targetWorkspace = String(pending.projectId || "");
      if (targetWorkspace && targetWorkspace !== options.workspace) return false;
      var documentId = String(pending.docId || pending.id || "");
      if (!documentId) return false;
      directId.current = documentId;
      options.onSelect(documentId);
      navigation.clearPending(pending);
      return true;
    }
    function onNavigate(event) { apply(event && event.detail); }
    window.addEventListener("cyrene:workbench-navigate", onNavigate);
    apply();
    return function () { window.removeEventListener("cyrene:workbench-navigate", onNavigate); };
  }, [options.workspace, options.active]);
  return {
    clear: function () { directId.current = ""; },
    isDirect: function (id) { return String(id || "") === directId.current; },
  };
}

export { createPendingMemorySelection, usePendingKnowledgeSelection };
