function wbWorkspaceFileTabId(file, fallback) {
  var value = file && (file.path || file.url || file.id || file.name);
  return String(value || fallback || "");
}

function wbWorkspaceTabsFromPaneLayouts(options) {
  var input = options || {};
  var activeOwner = String(input.activeOwner || "");
  var currentLayout = input.currentLayout && typeof input.currentLayout === "object"
    ? input.currentLayout : { left: [], right: [] };
  var layouts = input.layouts && typeof input.layouts === "object" ? input.layouts : {};
  var terminals = Array.isArray(input.terminals) ? input.terminals : [];
  var project = input.project || {};
  var preferredActiveKey = String(input.activeKey || "");
  var terminalById = {};
  terminals.forEach(function (terminal) {
    if (terminal && terminal.id) terminalById[String(terminal.id)] = terminal;
  });
  var orderedLayouts = [{ owner: activeOwner, layout: currentLayout }];
  Object.keys(layouts).forEach(function (owner) {
    if (String(owner) !== activeOwner) orderedLayouts.push({ owner: String(owner), layout: layouts[owner] });
  });
  var items = [];
  var byKey = {};
  var activeKey = "";
  var fallbackActiveKey = "";
  orderedLayouts.forEach(function (entry, layoutIndex) {
    var layout = entry.layout && typeof entry.layout === "object" ? entry.layout : {};
    var cards = (Array.isArray(layout.left) ? layout.left : []).concat(
      Array.isArray(layout.right) ? layout.right : []
    );
    cards.forEach(function (card) {
      if (!card) return;
      var rawKind = String(card.kind || "");
      var kind = rawKind === "viewer" ? "file" : rawKind;
      if (["chat", "terminal", "file", "plugin-view"].indexOf(kind) < 0) return;
      var id = kind === "chat" ? String(card.payload || "")
        : kind === "terminal" ? String(card.payload || "")
          : kind === "plugin-view" ? String(card.id || "")
          : wbWorkspaceFileTabId(card.payload, card.id);
      if (!id) return;
      var key = kind + ":" + id;
      if (layoutIndex === 0) {
        if (!fallbackActiveKey) fallbackActiveKey = key;
        if (preferredActiveKey && key === preferredActiveKey) activeKey = key;
      }
      if (kind === "chat") return;
      var ownerChatId = entry.owner.indexOf("project:") === 0 ? "" : entry.owner;
      var location = { ownerChatId: ownerChatId, paneCardId: String(card.id || "") };
      if (byKey[key]) {
        byKey[key].locations.push(location);
        return;
      }
      var terminal = kind === "terminal" ? terminalById[id] : null;
      var title = kind === "terminal"
        ? String(terminal && (terminal.displayTitle || terminal.title) || "Terminal")
        : kind === "plugin-view"
          ? String(card.payload && (card.payload.title || card.payload.viewId || card.payload.view_id || card.payload.packId || card.payload.pack_id) || "Plugin")
        : String(card.payload && (card.payload.name || card.payload.path) || id);
      var item = {
        id: id,
        kind: kind,
        title: title,
        projectId: String(project.id || ""),
        projectName: String(project.name || ""),
        ownerChatId: ownerChatId,
        paneCardId: String(card.id || ""),
        payload: card.payload,
        locations: [location],
        workspaceOpen: true,
      };
      byKey[key] = item;
      items.push(item);
    });
  });
  return { items: items, activeKey: activeKey || fallbackActiveKey };
}

export { wbWorkspaceFileTabId, wbWorkspaceTabsFromPaneLayouts }
