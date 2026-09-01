import { wbcChatDropReplacesActiveConversation, wbcHasChatDrag, wbcHasPluginViewDrag, wbcHasResourceDrag, wbcHasSplitDrag, wbcPaneCard, wbcPaneCardLocation, wbcPlacePaneCard, wbcReadChatDrag, wbcReadPluginViewDrag, wbcReadResourceDrag, wbcReadSplitDrag } from "../../workbench-chat.jsx"

function wbcCommittedPaneDropEdge(context, targetCardId, eventEdge) {
  var preview = context && context.paneDropTarget;
  var previewEdge = String(preview && preview.edge || "");
  var previewMatchesTarget = preview
    && String(preview.cardId || "") === String(targetCardId || "");
  if (previewMatchesTarget
    && ["top", "left", "replace", "right", "bottom"].indexOf(previewEdge) >= 0) {
    return previewEdge;
  }
  return ["top", "left", "replace", "right", "bottom"].indexOf(eventEdge) >= 0
    ? eventEdge : "bottom";
}

function wbcDroppedPaneCard(context, event, layout, target, targetCardId, effectiveEdge) {
  var sourceCardId = "";
  var card = null;
  var droppedChatSelection = "";
  var splitPayload = wbcReadSplitDrag(event);
  if (splitPayload && splitPayload.kind === "pane-card") {
    sourceCardId = String(splitPayload.cardId || "");
    var source = wbcPaneCardLocation(layout, sourceCardId);
    card = source && source.card;
  } else if (wbcHasChatDrag(event)) {
    var chatPayload = wbcReadChatDrag(event);
    if (chatPayload && chatPayload.id) {
      var chatId = String(chatPayload.id);
      if (wbcChatDropReplacesActiveConversation(
        target, effectiveEdge, chatId, context.activeChatIdRef.current
      )) droppedChatSelection = chatId;
      var chatCardId = "chat:" + chatId;
      var existingChat = wbcPaneCardLocation(layout, chatCardId);
      var replacingChat = existingChat
        && String(existingChat.card && existingChat.card.id || "") === String(targetCardId || "")
        && effectiveEdge === "replace";
      sourceCardId = replacingChat ? chatCardId : "";
      card = replacingChat ? existingChat.card : wbcPaneCard("chat", chatId, {
        id: existingChat ? undefined : chatCardId,
        ownerChatId: chatId,
        freshInstance: !!existingChat,
      });
    }
  } else if (wbcHasPluginViewDrag(event)) {
    var pluginPayload = wbcReadPluginViewDrag(event);
    if (pluginPayload) {
      var pluginOwnerChatId = String(pluginPayload.paneOwnerScope || "chat") === "project"
        ? context.projectPaneOwnerKey()
        : context.activeChatIdRef.current;
      var pluginCard = context.paneContentCard(
        "plugin-view",
        Object.assign({ projectId: context.projectId }, pluginPayload),
        pluginOwnerChatId
      );
      var existingPlugin = wbcPaneCardLocation(layout, pluginCard.id);
      var replacingPlugin = existingPlugin
        && String(existingPlugin.card && existingPlugin.card.id || "") === String(targetCardId || "")
        && effectiveEdge === "replace";
      sourceCardId = replacingPlugin ? pluginCard.id : "";
      card = replacingPlugin ? existingPlugin.card : (existingPlugin
        ? wbcPaneCard("plugin-view", pluginCard.payload, {
            ownerChatId: pluginCard.ownerChatId,
            freshInstance: true,
          })
        : pluginCard);
    }
  } else if (wbcHasResourceDrag(event)) {
    var resource = wbcReadResourceDrag(event);
    if (resource && resource.kind === "conversation") {
      var resourceChatId = String(resource.conversationId || resource.ownerSessionId || "");
      if (resourceChatId) {
        var resourceChatCardId = "chat:" + resourceChatId;
        var existingResourceChat = wbcPaneCardLocation(layout, resourceChatCardId);
        card = existingResourceChat ? wbcPaneCard("chat", resourceChatId, {
          ownerChatId: resourceChatId,
          freshInstance: true,
        }) : wbcPaneCard("chat", resourceChatId, {
          id: resourceChatCardId,
          ownerChatId: resourceChatId,
        });
      }
    } else if (resource && resource.kind === "file") {
      var file = resource.file && Object.keys(resource.file).length ? resource.file : resource;
      card = context.paneContentCard("file", file, context.activeChatIdRef.current);
    } else if (resource && resource.kind === "terminal" && resource.terminalId) {
      context.setActiveTerminalId(String(resource.terminalId));
      context.terminalClient.activate(context.projectId, String(resource.terminalId)).catch(function () {});
      card = context.paneContentCard("terminal", String(resource.terminalId), context.activeChatIdRef.current);
    }
  }
  return { card: card, sourceCardId: sourceCardId, droppedChatSelection: droppedChatSelection };
}

function wbcHandlePaneDrop(context, event, targetCardId, edge) {
  if (!wbcHasSplitDrag(event) && !wbcHasChatDrag(event)
    && !wbcHasPluginViewDrag(event)
    && !wbcHasResourceDrag(event)) return;
  event.preventDefault();
  event.stopPropagation();
  var layout = context.paneLayoutFor();
  var target = wbcPaneCardLocation(layout, targetCardId);
  if (!target) return;
  // Commit the drop target that was actually presented to the user. Overlapping
  // native drag sensors can report a different final `drop` element from the
  // last rendered preview, so the visible replace/split action owns the result.
  var committedEdge = wbcCommittedPaneDropEdge(context, targetCardId, edge);
  var effectiveEdge = (layout[target.side] || []).length >= 2 ? "replace" : committedEdge;
  var pluginPayload = wbcHasPluginViewDrag(event) ? wbcReadPluginViewDrag(event) : null;
  var projectOwnedPlugin = String(pluginPayload && pluginPayload.paneOwnerScope || "chat") === "project";
  var destinationOwnerId = projectOwnedPlugin ? context.projectPaneOwnerKey() : "";
  var dropped = wbcDroppedPaneCard(context, event, layout, target, targetCardId, effectiveEdge);
  if (!dropped.card) return;
  if (context.paneCardDragImageCleanupRef.current) context.paneCardDragImageCleanupRef.current();
  if (dropped.droppedChatSelection) {
    context.setPaneCardDragId("");
    context.setResourceDragSession(false);
    context.setChatDragKind("");
    context.setPaneDropTarget(null);
    context.selectChat(dropped.droppedChatSelection);
    return;
  }
  context.updatePaneLayout(function (current) {
    var placementLayout = projectOwnedPlugin ? layout : current;
    return wbcPlacePaneCard(
      placementLayout, dropped.card, target.side, effectiveEdge, dropped.sourceCardId, targetCardId
    );
  }, destinationOwnerId || undefined);
  if (projectOwnedPlugin) context.activateProjectPaneWorkspace();
  context.setPaneCardDragId("");
  context.setResourceDragSession(false);
  context.setChatDragKind("");
  context.setPaneDropTarget(null);
}

function wbcPlaceExistingPaneCard(context, sourceCardId, targetCardId, edge) {
  var layout = context.paneLayoutFor();
  var source = wbcPaneCardLocation(layout, sourceCardId);
  var target = wbcPaneCardLocation(layout, targetCardId);
  if (!source || !target || String(sourceCardId || "") === String(targetCardId || "")) return;
  var axisEdge = edge === "left" || edge === "right";
  var effectiveEdge = axisEdge
    ? edge : ((layout[target.side] || []).length >= 2 ? "replace" : edge);
  context.updatePaneLayout(function (current) {
    var currentSource = wbcPaneCardLocation(current, sourceCardId);
    var currentTarget = wbcPaneCardLocation(current, targetCardId);
    if (!currentSource || !currentTarget) return current;
    if (!axisEdge && currentSource.side === currentTarget.side
      && current[currentTarget.side].length === 2) {
      var reordered = {
        left: current.left.slice(), right: current.right.slice(),
        leftRatio: current.leftRatio, rightRatio: current.rightRatio,
      };
      if (currentSource.index !== currentTarget.index) reordered[currentTarget.side].reverse();
      return reordered;
    }
    return wbcPlacePaneCard(
      current, currentSource.card, currentTarget.side,
      effectiveEdge, sourceCardId, targetCardId
    );
  });
  context.setPaneCardDragId("");
  context.setPaneDropTarget(null);
}

export { wbcCommittedPaneDropEdge, wbcHandlePaneDrop, wbcPlaceExistingPaneCard }
