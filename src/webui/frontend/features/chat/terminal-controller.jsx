import { workbenchServices } from "../../shared/runtime/services.jsx"
import { useWbcState, wbcErrorText, wbcNormalizePaneLayout, wbcPaneCardLocation, wbcT } from "../../workbench-chat.jsx"

function useWbcTerminalCatalog(projectId) {
  var terminalClient = workbenchServices.terminal().Client;
  var [terminals, setTerminals] = useWbcState([]);
  var [terminalsLoading, setTerminalsLoading] = useWbcState(false);
  var [activeTerminalId, setActiveTerminalId] = useWbcState("");

  function refresh(options, onRestore) {
    if (!projectId) {
      setTerminals([]);
      return Promise.resolve([]);
    }
    if (!(options && options.background)) setTerminalsLoading(true);
    return terminalClient.list(projectId).then(function (payload) {
      var items = Array.isArray(payload && payload.terminals) ? payload.terminals : [];
      setTerminals(items);
      var restoredId = String(payload && payload.activeTerminalId || "");
      if (
        !(options && options.skipRestore)
        && restoredId
        && items.some(function (item) { return String(item.id) === restoredId; })
        && onRestore
      ) onRestore(restoredId);
      return items;
    }).catch(function (err) {
      if (!(options && options.background)) workbenchServices.feedback().showToast(wbcErrorText(err), "error");
      return [];
    }).finally(function () {
      if (!(options && options.background)) setTerminalsLoading(false);
    });
  }

  function updateSummary(terminal) {
    if (!terminal || !terminal.id) return;
    setTerminals(function (current) {
      var found = false;
      var next = current.map(function (item) {
        if (String(item.id) !== String(terminal.id)) return item;
        found = true;
        return Object.assign({}, item, terminal);
      });
      return found ? next : [terminal].concat(next);
    });
  }

  function rename(terminalId, title) {
    return terminalClient.rename(terminalId, title).then(function (terminal) {
      updateSummary(terminal);
      return terminal;
    });
  }

  function updateLayout(order, pinned) {
    return terminalClient.layout(projectId, order, pinned).then(function (payload) {
      if (Array.isArray(payload && payload.terminals)) setTerminals(payload.terminals);
      return payload;
    }).catch(function (err) {
      workbenchServices.feedback().showToast(wbcErrorText(err), "error");
      return null;
    });
  }

  return {
    client: terminalClient,
    terminals: terminals,
    setTerminals: setTerminals,
    loading: terminalsLoading,
    setLoading: setTerminalsLoading,
    activeId: activeTerminalId,
    setActiveId: setActiveTerminalId,
    refresh: refresh,
    updateSummary: updateSummary,
    rename: rename,
    updateLayout: updateLayout,
  };
}

function wbcOpenTerminal(context, terminalId, side) {
  var id = String(terminalId || "");
  if (!id) return;
  if (side !== "left" && side !== "right") {
    context.replaceWithTerminal(id);
    return;
  }
  context.setActiveTerminalId(id);
  context.terminalClient.activate(context.projectId, id).catch(function () {});
  context.openPaneContent("terminal", id, { side: side });
}

function wbcShowAgentTerminal(context, terminalId, preferredSide) {
  var id = String(terminalId || "");
  if (!id) return null;
  var ownerChatId = String(context.activeChatIdRef.current || "");
  var layout = context.paneLayoutFor(ownerChatId);
  var existing = wbcPaneCardLocation(layout, "terminal:" + id);
  context.setActiveTerminalId(id);
  context.terminalClient.activate(context.projectId, id).catch(function () {});
  if (existing) return existing.card;
  var card = context.paneContentCard("terminal", id, ownerChatId);
  var requestedSide = preferredSide === "left" ? "left" : "right";
  context.updatePaneLayout(function (current) {
    var next = {
      left: current.left.slice(), right: current.right.slice(),
      leftRatio: current.leftRatio, rightRatio: current.rightRatio,
    };
    var count = next.left.length + next.right.length;
    if (count <= 1) {
      var occupiedSide = next.left.length ? "left" : (next.right.length ? "right" : "");
      var targetSide = occupiedSide === requestedSide
        ? (requestedSide === "left" ? "right" : "left")
        : requestedSide;
      next[targetSide] = [card];
      return next;
    }
    var replaceSide = next[requestedSide].length
      ? requestedSide
      : (requestedSide === "left" ? "right" : "left");
    var replaceIndex = Math.max(0, next[replaceSide].length - 1);
    next[replaceSide][replaceIndex] = card;
    return next;
  }, ownerChatId);
  return card;
}

function wbcReplaceWithTerminal(context, terminalId, options) {
  var id = String(terminalId || "");
  if (!id) return;
  var ownerChatId = String(
    options && options.ownerChatId != null
      ? options.ownerChatId
      : (context.activeChatIdRef.current || "")
  );
  var currentLayout = context.paneLayoutFor(ownerChatId);
  var currentCards = currentLayout.left.concat(currentLayout.right);
  var replacedTerminal = currentCards.length === 1 && currentCards[0].kind === "terminal"
    ? currentCards[0]
    : null;
  context.setActiveTerminalId(id);
  if (!(options && options.skipPersist)) {
    context.terminalClient.activate(context.projectId, id).catch(function () {});
  }
  if (replacedTerminal && String(replacedTerminal.payload || "") === id) return;
  var restoreLayouts = context.paneLayoutRestoreRef.current;
  var restoreLayout = replacedTerminal
    && Object.prototype.hasOwnProperty.call(restoreLayouts, replacedTerminal.id)
    ? restoreLayouts[replacedTerminal.id]
    : currentLayout;
  if (replacedTerminal) delete restoreLayouts[replacedTerminal.id];
  context.openPaneContent("terminal", id, {
    replaceWorkspace: true,
    restore: true,
    restoreLayout: restoreLayout,
    ownerChatId: ownerChatId,
  });
}

function wbcCreateTerminal(context) {
  context.setTerminalsLoading(true);
  return context.terminalClient.create(context.projectId).then(function (terminal) {
    context.updateTerminalSummary(terminal);
    context.replaceWithTerminal(terminal.id);
    return terminal;
  }).catch(function (err) {
    workbenchServices.feedback().showToast(wbcErrorText(err), "error");
    return null;
  }).finally(function () { context.setTerminalsLoading(false); });
}

function wbcDeleteTerminal(context, terminalId) {
  var terminal = context.terminals.find(function (item) {
    return String(item.id) === String(terminalId);
  });
  var feedback = workbenchServices.feedback();
  var confirmation = feedback.confirmModal ? feedback.confirmModal({
    title: wbcT("terminal.deleteTitle", "Delete terminal"),
    body: wbcT("terminal.deleteBody", "This will stop the running process, cancel any pending Agent wake, and remove {title}.", { title: terminal && terminal.title || "Terminal" }),
    confirmLabel: wbcT("terminal.delete", "Delete terminal"),
    danger: true,
  }) : Promise.resolve(window.confirm(wbcT("terminal.deleteBody", "This will stop the running process and remove this terminal.")));
  return confirmation.then(function (confirmed) {
    if (!confirmed) return null;
    return context.terminalClient.remove(terminalId);
  }).then(function (result) {
    if (!result) return null;
    context.setTerminals(function (current) {
      return current.filter(function (item) { return String(item.id) !== String(terminalId); });
    });
    context.setActiveTerminalId(function (current) {
      return String(current) === String(terminalId) ? "" : current;
    });
    context.setPaneLayoutsByChat(function (current) {
      var next = {};
      Object.keys(current).forEach(function (ownerId) {
        var layout = wbcNormalizePaneLayout(current[ownerId], ownerId.indexOf("project:") === 0 ? "" : ownerId);
        next[ownerId] = Object.assign({}, layout, {
          left: layout.left.filter(function (card) {
            return !(card.kind === "terminal" && String(card.payload) === String(terminalId));
          }),
          right: layout.right.filter(function (card) {
            return !(card.kind === "terminal" && String(card.payload) === String(terminalId));
          }),
        });
      });
      return next;
    });
    return result;
  });
}

export {
  useWbcTerminalCatalog,
  wbcCreateTerminal,
  wbcDeleteTerminal,
  wbcOpenTerminal,
  wbcReplaceWithTerminal,
  wbcShowAgentTerminal,
}
