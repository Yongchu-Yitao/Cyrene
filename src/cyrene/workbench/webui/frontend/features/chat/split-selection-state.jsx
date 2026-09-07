import { useWbcState } from "../../workbench-chat.jsx"

const splitSlots = { "side-agent": "sideAgentId", artifact: "artifactKey", change: "change", resource: "resource" };

function withoutEntry(current, chatId) {
  if (!current[chatId]) return current;
  var next = Object.assign({}, current);
  delete next[chatId];
  return next;
}

// Keep all four historical slots: restoration can legitimately contain more
// than one. Only an explicit selection clears the other slots.
export function transitionSplitSelection(state, action) {
  var kind = action.kind, chatId = action.chatId;
  if (action.type === "close") {
    if (action.expected !== undefined && state[kind][chatId] !== action.expected) return state;
    var closed = withoutEntry(state[kind], chatId);
    return closed === state[kind] ? state : Object.assign({}, state, { [kind]: closed });
  }
  if (action.type === "prune-resources") {
    var resources = state.resource, nextResources = {}, changed = false;
    Object.keys(resources).forEach(function (id) {
      if (action.matches(resources[id], id)) changed = true;
      else nextResources[id] = resources[id];
    });
    return changed ? Object.assign({}, state, { resource: nextResources }) : state;
  }
  var next = Object.assign({}, state);
  Object.keys(splitSlots).forEach(function (slot) {
    if (action.type === "restore") {
      var restored = Object.assign({}, state[slot]);
      var value = action.snapshot[splitSlots[slot]];
      if (value) restored[chatId] = value;
      else delete restored[chatId];
      next[slot] = restored;
    } else if (slot === kind) {
      next[slot] = Object.assign({}, state[slot], { [chatId]: action.value });
    } else {
      next[slot] = withoutEntry(state[slot], chatId);
    }
  });
  return next;
}

export function useWbcSplitSelection() {
  var [state, setState] = useWbcState(function () {
    return { "side-agent": {}, artifact: {}, change: {}, resource: {} };
  });
  function apply(action) { setState(function (current) { return transitionSplitSelection(current, action); }); }
  return {
    sideAgentSplitByChat: state["side-agent"], artifactSplitByChat: state.artifact,
    changeSplitByChat: state.change, resourceSplitByChat: state.resource,
    select: function (chatId, kind, value) { apply({ type: "select", chatId, kind, value }); },
    close: function (chatId, kind, expected) { apply({ type: "close", chatId, kind, expected }); },
    pruneResources: function (matches) { apply({ type: "prune-resources", matches }); },
    snapshot: function (chatId) {
      return { sideAgentId: state["side-agent"][chatId] || "", artifactKey: state.artifact[chatId] || "",
        change: state.change[chatId] || null, resource: state.resource[chatId] || null };
    },
    restore: function (chatId, snapshot) {
      if (chatId && snapshot) apply({ type: "restore", chatId, snapshot });
    },
  };
}
