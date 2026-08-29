import { wbcChatSideZoneRect, wbcHasResourceDrag, wbcReadResourceDrag } from "../../workbench-chat.jsx"

function wbcResourceSplitDropGeometry(pageRef) {
  var page = pageRef.current;
  if (!page) return null;
  var pageRect = page.getBoundingClientRect();
  var rail = page.querySelector(".wbc-rail");
  var railRect = rail && rail.getBoundingClientRect ? rail.getBoundingClientRect() : null;
  var contentLeft = railRect && railRect.right > pageRect.left ? railRect.right : pageRect.left;
  var contentRight = pageRect.right;
  var chatSideRect = wbcChatSideZoneRect();
  var rightLeft = chatSideRect
    ? Math.max(contentLeft, Math.min(contentRight, chatSideRect.left))
    : contentLeft + ((contentRight - contentLeft) / 2);
  var rightRight = chatSideRect
    ? Math.max(rightLeft, Math.min(contentRight, chatSideRect.right))
    : contentRight;
  return { pageRect: pageRect, contentLeft: contentLeft, rightLeft: rightLeft, rightRight: rightRight };
}

function wbcResourceSplitSideAt(context, event) {
  var geometry = wbcResourceSplitDropGeometry(context.pageRef);
  if (!geometry) return "";
  var pageRect = geometry.pageRect;
  if (event.clientX < geometry.contentLeft || event.clientX > geometry.rightRight
    || event.clientY < pageRect.top || event.clientY > pageRect.bottom) return "";
  return event.clientX < geometry.rightLeft ? "left" : "right";
}

function wbcHandleResourceSplitDragOver(context, event) {
  if (!wbcHasResourceDrag(event)) return;
  if (event.target && event.target.closest && (
    event.target.closest(".wbc-rail") || event.target.closest(".wbc-pane-card")
  )) {
    if (context.resourceSplitDropSide) context.setResourceSplitDropSide("");
    return;
  }
  var side = wbcResourceSplitSideAt(context, event);
  if (!side) {
    if (context.resourceSplitDropSide) context.setResourceSplitDropSide("");
    return;
  }
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  if (context.resourceSplitDropSide !== side) context.setResourceSplitDropSide(side);
}

function wbcHandleResourceSplitDrop(context, event) {
  if (!wbcHasResourceDrag(event)) return;
  var side = wbcResourceSplitSideAt(context, event);
  var resource = wbcReadResourceDrag(event);
  context.setResourceSplitDropSide("");
  if (!side || !resource || (resource.kind !== "file" && resource.kind !== "terminal")) return;
  event.preventDefault();
  event.stopPropagation();
  if (resource.kind === "terminal") {
    context.openTerminal(resource.terminalId, side);
    return;
  }
  context.setSplitSideDirect(side);
  context.openViewer(resource.file && Object.keys(resource.file).length ? resource.file : resource, side);
}

export {
  wbcHandleResourceSplitDragOver,
  wbcHandleResourceSplitDrop,
  wbcResourceSplitDropGeometry,
  wbcResourceSplitSideAt,
}
