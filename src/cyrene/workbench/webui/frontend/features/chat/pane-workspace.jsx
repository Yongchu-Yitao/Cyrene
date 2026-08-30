import { wbcPaneCardLocation } from "../../workbench-chat.jsx"

function wbcPaneWorkspacePresentation(
  paneLayout,
  paneCardDragId,
  chatDragSession,
  resourceDragSession,
  activeChatId
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
    && paneOnlyCard.kind === "chat"
  );
  var paneDropSessionActive = !!(paneCardDragId || chatDragSession || resourceDragSession);
  var singlePaneContextDropActive = !!(
    singlePaneDropUsesContextTracks
    && paneDropSessionActive
    && String(paneCardDragId || "") !== String(paneOnlyCard.id || "")
  );
  // Pane rank comes only from the layout. `activeChatId` selects conversation
  // data; it no longer changes whether an otherwise identical card is treated
  // as the main surface.
  var splitDetailOpen = paneCardCount > 1;
  var projectPaneOnly = paneCardCount === 1 && !!(
    paneOnlyCard
    && paneOnlyCard.kind !== "chat"
  );
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
    showNewConversationWorkspace: !activeChatId && paneCardCount === 0,
    singleColumnWorkspaceOpen: splitDetailOpen && !projectPaneOnly && !paneHasTwoColumns,
  };
}

export { wbcPaneWorkspacePresentation }
