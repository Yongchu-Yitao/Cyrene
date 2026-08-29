import { wbcPaneCardLocation } from "../../workbench-chat.jsx"

function wbcPaneWorkspacePresentation(
  paneLayout,
  paneCardDragId,
  chatDragSession,
  resourceDragSession,
  activeChatId,
  taskPaneSessions,
  tasks
) {
  var paneCardCount = paneLayout.left.length + paneLayout.right.length;
  var paneHasTwoColumns = !!(paneLayout.left.length && paneLayout.right.length);
  var paneDraggedLocation = paneCardDragId ? wbcPaneCardLocation(paneLayout, paneCardDragId) : null;
  var paneAxisDropAvailable = !!(
    paneDraggedLocation
    && (paneLayout[paneDraggedLocation.side] || []).length === 2
    && !(paneLayout[paneDraggedLocation.side === "left" ? "right" : "left"] || []).length
  );
  var paneOnlyCard = paneLayout.left[0] || paneLayout.right[0] || null;
  var singlePaneDropUsesContextTracks = !!(
    paneCardCount === 1
    && paneOnlyCard
    && (paneOnlyCard.kind === "chat" || paneOnlyCard.kind === "task")
  );
  var paneDropSessionActive = !!(paneCardDragId || chatDragSession || resourceDragSession);
  var singlePaneContextDropActive = !!(
    singlePaneDropUsesContextTracks
    && paneDropSessionActive
    && String(paneCardDragId || "") !== String(paneOnlyCard.id || "")
  );
  // Pane rank comes only from the layout. `activeChatId` selects conversation
  // data; it no longer changes whether an otherwise identical card is treated
  // as the main surface. A single chat/task owns the contextual right panel,
  // while every other single card receives the full workspace.
  var splitDetailOpen = paneCardCount > 1;
  var projectPaneOnly = paneCardCount === 1 && !!(
    paneOnlyCard
    && paneOnlyCard.kind !== "chat"
  );
  var projectTaskPanelCard = paneCardCount === 1 && paneOnlyCard && paneOnlyCard.kind === "task"
    ? paneOnlyCard
    : null;
  var projectTaskPanelId = projectTaskPanelCard ? String(projectTaskPanelCard.payload || "") : "";
  var projectTaskPanelSession = projectTaskPanelId
    ? (taskPaneSessions[projectTaskPanelId] || (Array.isArray(tasks) ? tasks.find(function (task) {
        return String(task && task.id || "") === projectTaskPanelId;
      }) : null))
    : null;
  return {
    paneCardCount: paneCardCount,
    paneHasTwoColumns: paneHasTwoColumns,
    paneAxisDropAvailable: paneAxisDropAvailable,
    paneOnlyCard: paneOnlyCard,
    singlePaneDropUsesContextTracks: singlePaneDropUsesContextTracks,
    paneDropSessionActive: paneDropSessionActive,
    singlePaneContextDropActive: singlePaneContextDropActive,
    splitDetailOpen: splitDetailOpen,
    projectPaneOnly: projectPaneOnly,
    projectTaskPanelCard: projectTaskPanelCard,
    projectTaskPanelId: projectTaskPanelId,
    projectTaskPanelSession: projectTaskPanelSession,
    showNewConversationWorkspace: !activeChatId && paneCardCount === 0,
    singleColumnWorkspaceOpen: splitDetailOpen && !projectPaneOnly && !paneHasTwoColumns && !projectTaskPanelCard,
  };
}

export { wbcPaneWorkspacePresentation }
