import { wbcChatDropReplacesActiveConversation, wbcHasChatDrag, wbcHasPluginViewDrag, wbcHasResourceDrag, wbcHasSplitDrag, wbcHasTaskDrag, wbcPaneCard, wbcPaneCardLocation, wbcPlacePaneCard, wbcReadChatDrag, wbcReadPluginViewDrag, wbcReadResourceDrag, wbcReadSplitDrag, wbcReadTaskDrag } from "../../workbench-chat.jsx"

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
  } else if (wbcHasTaskDrag(event)) {
    var taskPayload = wbcReadTaskDrag(event);
    if (taskPayload && taskPayload.id) {
      var taskId = String(taskPayload.id);
      var taskCardId = "task:" + taskId;
      var existingTask = wbcPaneCardLocation(layout, taskCardId);
      var replacingTask = existingTask
        && String(existingTask.card && existingTask.card.id || "") === String(targetCardId || "")
        && effectiveEdge === "replace";
      sourceCardId = replacingTask ? taskCardId : "";
      card = replacingTask ? existingTask.card : wbcPaneCard("task", taskId, {
        id: existingTask ? undefined : taskCardId,
        ownerChatId: context.activeChatIdRef.current || context.projectPaneOwnerKey(),
        freshInstance: !!existingTask,
      });
      if (context.onSelectTask) context.onSelectTask(taskId);
    }
  } else if (wbcHasPluginViewDrag(event)) {
    var pluginPayload = wbcReadPluginViewDrag(event);
    if (pluginPayload) {
      var pluginCard = context.paneContentCard(
        "plugin-view",
        Object.assign({ projectId: context.projectId }, pluginPayload),
        context.activeChatIdRef.current
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
    if (resource && resource.kind === "file") {
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
    && !wbcHasTaskDrag(event) && !wbcHasPluginViewDrag(event)
    && !wbcHasResourceDrag(event)) return;
  event.preventDefault();
  event.stopPropagation();
  var layout = context.paneLayoutFor();
  var target = wbcPaneCardLocation(layout, targetCardId);
  if (!target) return;
  var effectiveEdge = (layout[target.side] || []).length >= 2 ? "replace" : edge;
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
    return wbcPlacePaneCard(
      current, dropped.card, target.side, effectiveEdge, dropped.sourceCardId, targetCardId
    );
  });
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

export { wbcHandlePaneDrop, wbcPlaceExistingPaneCard }
