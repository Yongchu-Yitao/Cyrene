import { workbenchServices } from "../../shared/runtime/services.jsx"
import { useWbcEffect, wbcT } from "../../workbench-chat.jsx"

function wbcPaneSemanticNodeId(cardId) {
  return "pane_card_" + String(cardId || "pane")
    .replace(/[^a-zA-Z0-9_-]/g, "_")
    .slice(0, 120);
}

function wbcPaneSemanticName(card, catalogs) {
  var kind = String(card && card.kind || "pane");
  var payload = card && card.payload;
  var items = catalogs || {};
  var match;
  if (kind === "chat") {
    match = (items.chats || []).find(function (item) { return String(item && item.id || "") === String(payload || ""); });
    return match && match.title || wbcT("workbenchChat.chatSplitLabel", "Chat");
  }
  if (kind === "task") {
    match = (items.tasks || []).find(function (item) { return String(item && item.id || "") === String(payload || ""); });
    return match && match.title || wbcT("workbench.page.task", "Task");
  }
  if (kind === "terminal") {
    match = (items.terminals || []).find(function (item) { return String(item && item.id || "") === String(payload || ""); });
    return match && (match.displayTitle || match.title) || wbcT("terminal.title", "Terminal");
  }
  if (kind === "file" || kind === "viewer") {
    return String(payload && (payload.name || payload.path) || wbcT("workbenchChat.viewer", "Viewer"));
  }
  if (kind === "plugin-view") return String(payload && (payload.title || payload.viewId || payload.view_id) || "Plugin");
  if (kind === "subagents") return wbcT("workbenchChat.subagents", "Subagents");
  if (kind === "side-agent") return wbcT("workbenchChat.sideAgent.tab", "Side questions");
  if (kind === "browser") return wbcT("chat.side.browser", "Browser");
  if (kind === "change") return String(payload && payload.path || wbcT("workbenchChat.changes", "Changes"));
  if (kind === "map") return wbcT("chat.side.map", "Map");
  return kind;
}

function WbcPaneSemanticController({ active, layout, rootRef, chats, tasks, terminals, onOpenPane, onMovePane, onSwapPanes, onClosePane }) {
  useWbcEffect(function () {
    if (!active || !window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = workbenchServices.uiSurface();
    var unregister = [];
    var cards = (layout.left || []).concat(layout.right || []);
    var catalogs = { chats: chats || [], tasks: tasks || [], terminals: terminals || [] };
    function paneElement(nodeId) {
      var root = rootRef && rootRef.current;
      return root && root.querySelector('[data-pane-semantic-node-id="' + nodeId + '"]');
    }
    function normalizeSide(input) {
      var side = String(input && input.side || "right");
      if (side !== "left" && side !== "right") throw new Error("pane side must be left or right");
      return side;
    }
    function ensureCatalogItem(kind, value) {
      var id = String(value || "");
      var source = kind === "chat" ? catalogs.chats : kind === "task" ? catalogs.tasks : catalogs.terminals;
      if (!id || !source.some(function (item) { return String(item && item.id || "") === id; })) {
        throw new Error(kind + " is not available in the current project");
      }
      return id;
    }
    var workspaceActions = [
      { action_id: "open_chat", kind: "move", risk: "R1", gesture_aliases: ["drag_to_split"], input_schema: { chat_id: "text<=160", side: "text<=5" } },
    ];
    if (catalogs.tasks.length) workspaceActions.push(
      { action_id: "open_task", kind: "move", risk: "R1", gesture_aliases: ["drag_to_split"], input_schema: { task_id: "text<=160", side: "text<=5" } }
    );
    if (catalogs.terminals.length) workspaceActions.push(
      { action_id: "open_terminal", kind: "move", risk: "R1", gesture_aliases: ["drag_to_split"], input_schema: { terminal_id: "text<=160", side: "text<=5" } }
    );
    unregister.push(uiSurface.register({
      node_id: "pane_workspace",
      parent_id: "root",
      scope: "main",
      order: 200,
      get_element: function () {
        var root = rootRef && rootRef.current;
        return root && root.querySelector(".wbc-pane-layout");
      },
      get_node: function () {
        return {
          role: "group",
          name: wbcT("tour.chat-deep.split.title", "Split view"),
          value_summary: cards.length + " pane" + (cards.length === 1 ? "" : "s"),
          state: {
            card_count: cards.length,
            left_card_ids: (layout.left || []).map(function (card) { return String(card.id || ""); }),
            right_card_ids: (layout.right || []).map(function (card) { return String(card.id || ""); }),
          },
        };
      },
      actions: workspaceActions,
      handlers: {
        open_chat: function (input) {
          var id = ensureCatalogItem("chat", input && input.chat_id);
          onOpenPane("chat", id, { side: normalizeSide(input) });
          return { kind: "chat", id: id, side: normalizeSide(input) };
        },
        open_task: function (input) {
          var id = ensureCatalogItem("task", input && input.task_id);
          onOpenPane("task", id, { side: normalizeSide(input) });
          return { kind: "task", id: id, side: normalizeSide(input) };
        },
        open_terminal: function (input) {
          var id = ensureCatalogItem("terminal", input && input.terminal_id);
          onOpenPane("terminal", id, { side: normalizeSide(input) });
          return { kind: "terminal", id: id, side: normalizeSide(input) };
        },
      },
    }));
    cards.forEach(function (card, order) {
      var nodeId = wbcPaneSemanticNodeId(card.id);
      var location = (layout.left || []).indexOf(card) >= 0
        ? { side: "left", index: (layout.left || []).indexOf(card) }
        : { side: "right", index: (layout.right || []).indexOf(card) };
      var actions = cards.length > 1 ? [
        { action_id: "move", kind: "move", risk: "R1", gesture_aliases: ["drag_to_split"], input_schema: { side: "text<=5", position: "text<=6" } },
        { action_id: "swap", kind: "move", risk: "R1", gesture_aliases: ["drag_to_split"], input_schema: { target_node_id: "text<=160" } },
        { action_id: "close", kind: "invoke", risk: "R1", gesture_aliases: ["close_button"] },
      ] : [];
      unregister.push(uiSurface.register({
        node_id: nodeId,
        parent_id: "pane_workspace",
        scope: "main",
        order: 210 + order,
        get_element: function () { return paneElement(nodeId); },
        get_node: function () {
          return {
            role: "region",
            name: wbcPaneSemanticName(card, catalogs),
            value_summary: String(card.kind || "pane"),
            state: {
              card_id: String(card.id || ""),
              content_kind: String(card.kind || ""),
              content_id: typeof card.payload === "string" ? card.payload : "",
              side: location.side,
              position: location.index === 0 ? "top" : "bottom",
            },
          };
        },
        actions: actions,
        handlers: {
          move: function (input) { return onMovePane(card.id, input || {}); },
          swap: function (input) {
            var targetNodeId = String(input && input.target_node_id || "");
            var targetCard = cards.find(function (candidate) {
              return wbcPaneSemanticNodeId(candidate.id) === targetNodeId;
            });
            if (!targetCard || String(targetCard.id || "") === String(card.id || "")) {
              throw new Error("a different visible pane card target is required");
            }
            return onSwapPanes(card.id, targetCard.id);
          },
          close: function () { return onClosePane(card); },
        },
      }));
    });
    return function () { unregister.forEach(function (remove) { remove(); }); };
  }, [active, layout, chats, tasks, terminals, onOpenPane, onMovePane, onSwapPanes, onClosePane]);
  return null;
}

export { WbcPaneSemanticController, wbcPaneSemanticNodeId }
