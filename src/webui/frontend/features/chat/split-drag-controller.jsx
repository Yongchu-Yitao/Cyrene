import { wbcClonePaneWithLiveState, wbcEscapeHtml, wbcHasSplitDrag, wbcHideNativeDragImage, wbcSetSplitDrag, wbcSplitSideForDraggedConversation, wbcT } from "../../workbench-chat.jsx"

function wbcSplitDragClonePanel(session) {
  var context = session.context;
  var event = session.event;
  var page = session.page;
  var dragHandle = event.currentTarget;
  var panel = session.fromMainGrip
    ? page.querySelector(":scope > .wbc-main")
    : (dragHandle && dragHandle.closest ? dragHandle.closest(".wbc-side-agent-split") : null);
  session.panel = panel;
  if (!panel) {
    session.ghost.style.left = event.clientX + "px";
    session.ghost.style.top = event.clientY + "px";
    return;
  }
  var panelRect = panel.getBoundingClientRect();
  var clonedPane = wbcClonePaneWithLiveState(panel);
  session.clone = clonedPane.clone;
  session.restoreGhostViewport = clonedPane.restoreViewport;
  session.clone.style.border = "0";
  session.clone.style.boxShadow = "none";
  session.ghost.appendChild(session.clone);
  session.panelW = Math.max(120, Math.min(Math.round(panelRect.width), Math.round(window.innerWidth - 16)));
  session.panelH = Math.max(120, Math.min(Math.round(panelRect.height), Math.round(window.innerHeight * 0.72)));
  session.ghost.style.width = session.panelW + "px";
  session.ghost.style.height = session.panelH + "px";
  session.ghost.style.left = panelRect.left + "px";
  var dragHandleRect = dragHandle && dragHandle.getBoundingClientRect
    ? dragHandle.getBoundingClientRect()
    : null;
  var ghostTopGrabOffset = dragHandleRect
    ? Math.max(0, Math.min(dragHandleRect.height, event.clientY - dragHandleRect.top))
    : 0;
  session.ghost.style.top = (event.clientY - ghostTopGrabOffset) + "px";
  session.grabOffset = {
    x: Math.max(0, Math.min(panelRect.width, event.clientX - panelRect.left)),
    y: ghostTopGrabOffset,
  };
}

function wbcSplitDragAppendRailCard(session) {
  var context = session.context;
  var liftChatId = session.fromMainGrip
    ? String(context.activeChatIdRef.current || "")
    : (context.splitChatId || context.splitSideAgentId);
  var railCard = liftChatId
    ? session.page.querySelector('.wbc-chat-card[data-chat-id="' + liftChatId + '"]')
    : null;
  if (!railCard && !context.splitChatId && !session.fromMainGrip) {
    railCard = session.page.querySelector(".wbc-chat-card.active");
  }
  if (railCard) {
    var cardRect = railCard.getBoundingClientRect();
    var cardClone = railCard.cloneNode(true);
    cardClone.classList.remove("active", "dragging", "menu-open", "group-drop-target", "wbc-chat-group-child");
    session.ghost.appendChild(cardClone);
    session.cardW = Math.round(cardRect.width);
    session.cardH = Math.round(cardRect.height);
  }
  session.sourceCardEl = railCard;
}

function wbcSplitDragMountGhost(session) {
  var shell = document.querySelector(".workbench-shell");
  if (shell) {
    var shellStyle = window.getComputedStyle(shell);
    for (var i = 0; i < shellStyle.length; i++) {
      var name = shellStyle[i];
      if (name.indexOf("--") === 0) {
        session.ghost.style.setProperty(name, shellStyle.getPropertyValue(name));
      }
    }
  }
  document.body.appendChild(session.ghost);
  if (!session.restoreGhostViewport) return;
  session.restoreGhostViewport();
  var sourceThread = session.panel && session.panel.querySelector
    ? session.panel.querySelector(".wbc-thread")
    : null;
  var ghostThread = session.clone && session.clone.querySelector
    ? session.clone.querySelector(".wbc-thread")
    : null;
  if (!sourceThread || !ghostThread) return;
  var sourcePadding = parseFloat(window.getComputedStyle(sourceThread).paddingTop) || 0;
  var ghostPadding = parseFloat(window.getComputedStyle(ghostThread).paddingTop) || 0;
  var removedPadding = Math.max(0, sourcePadding - ghostPadding);
  if (removedPadding) ghostThread.scrollTop = Math.max(0, ghostThread.scrollTop - removedPadding);
}

function wbcSplitDragCreateZones(session) {
  var zones = document.createElement("div");
  zones.className = "wbc-split-drop-zones";
  zones.setAttribute("role", "presentation");
  if (session.panelW) zones.style.setProperty("--wbc-split-drop-width", session.panelW + "px");
  zones.setAttribute("data-conversation-side", session.previewConversationSide);
  zones.innerHTML = ""
    + '<div class="wbc-split-drop-zone wbc-split-drop-left" data-zone="left">'
    + '<span class="wbc-chat-side-drop-hint" role="status">' + wbcEscapeHtml(wbcT("workbenchChat.splitDropLeft", "Release to move the split to the left side")) + "</span>"
    + "</div>"
    + '<div class="wbc-split-drop-zone wbc-split-drop-right" data-zone="right">'
    + '<span class="wbc-chat-side-drop-hint" role="status">' + wbcEscapeHtml(wbcT("workbenchChat.splitDropRight", "Release to move the split to the right side")) + "</span>"
    + "</div>"
    + '<div class="wbc-split-drop-zone wbc-split-drop-rail" data-zone="rail">'
    + '<span class="wbc-chat-side-drop-hint" role="status">' + wbcEscapeHtml(wbcT("workbenchChat.splitDropClose", "Release to close the split panel")) + "</span>"
    + "</div>";
  session.page.appendChild(zones);
  session.zones = zones;
}

function wbcSplitDragZoneAt(session, clientX, clientY) {
  var rail = document.querySelector(".wbc-rail");
  var railRect = rail ? rail.getBoundingClientRect() : null;
  if (railRect && clientX >= railRect.left && clientX <= railRect.right
    && clientY >= railRect.top && clientY <= railRect.bottom) return "rail";
  var page = session.context.pageRef.current;
  if (!page) return "";
  var pageRect = page.getBoundingClientRect();
  if (!pageRect.width) return "";
  var midpoint = pageRect.left + pageRect.width / 2;
  if (clientX < midpoint - 24) return "left";
  if (clientX > midpoint + 24) return "right";
  return session.previewConversationSide;
}

function wbcSplitDragSetActive(session, zone) {
  session.ghost.classList.toggle("card", zone === "rail");
  if (zone === "rail" && session.cardW) {
    session.ghost.style.width = session.cardW + "px";
    session.ghost.style.height = session.cardH + "px";
  } else if (session.panelW) {
    session.ghost.style.width = session.panelW + "px";
    session.ghost.style.height = session.panelH + "px";
  }
  if (session.sourceCardEl) {
    session.sourceCardEl.classList.toggle("wbc-split-card-lifted", zone === "rail");
  }
  var zoneEls = session.zones.querySelectorAll(".wbc-split-drop-zone");
  for (var i = 0; i < zoneEls.length; i++) {
    var element = zoneEls[i];
    element.classList.toggle("active", element.getAttribute("data-zone") === zone);
  }
}

function wbcSplitDragPositionGhost(session, clientX, clientY) {
  var rect = session.ghost.getBoundingClientRect();
  var targetWidth = session.ghost.classList.contains("card") && session.cardW
    ? session.cardW : (session.panelW || rect.width);
  var targetHeight = session.ghost.classList.contains("card") && session.cardH
    ? session.cardH : (session.panelH || rect.height);
  var rawLeft = clientX - (session.grabOffset ? session.grabOffset.x : 0);
  var rawTop = clientY - (session.grabOffset ? session.grabOffset.y : 0);
  var maxLeft = Math.max(8, window.innerWidth - targetWidth - 8);
  var maxTop = Math.max(8, window.innerHeight - targetHeight - 8);
  session.ghost.style.left = Math.max(8, Math.min(maxLeft, rawLeft)) + "px";
  session.ghost.style.top = Math.max(8, Math.min(maxTop, rawTop)) + "px";
}

function wbcSplitDragOver(session, event) {
  if (session.clearTimer) { clearTimeout(session.clearTimer); session.clearTimer = null; }
  var zone = wbcSplitDragZoneAt(session, event.clientX, event.clientY);
  if (!zone) {
    wbcSplitDragSetActive(session, "");
    wbcSplitDragPositionGhost(session, event.clientX, event.clientY);
    return;
  }
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  wbcSplitDragSetActive(session, zone);
  wbcSplitDragPositionGhost(session, event.clientX, event.clientY);
  if (zone === "rail") return;
  session.previewConversationSide = zone;
  session.zones.setAttribute("data-conversation-side", zone);
  session.context.setSplitSideDirect(wbcSplitSideForDraggedConversation(zone, session.fromMainGrip));
}

function wbcSplitDragDrop(session, event) {
  if (!wbcHasSplitDrag(event)) return;
  var zone = wbcSplitDragZoneAt(session, event.clientX, event.clientY);
  if (!zone) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  session.context.handleSplitDragEnd();
  if (zone === "rail") {
    if (session.context.splitChatId) {
      if (session.fromMainGrip) session.context.closeMainConversationSplit();
      else session.context.closeResourceSplit();
    } else {
      session.context.closeActiveSplit();
    }
    return;
  }
  session.previewConversationSide = zone;
  session.context.setSplitSideDirect(wbcSplitSideForDraggedConversation(zone, session.fromMainGrip));
}

function wbcSplitDragCleanup(session) {
  if (session.context.splitOverlayCleanupRef.current !== session.cleanup) return;
  session.context.splitOverlayCleanupRef.current = null;
  if (session.clearTimer) clearTimeout(session.clearTimer);
  document.removeEventListener("dragover", session.onDragOver, true);
  document.removeEventListener("drop", session.onDrop, true);
  if (session.sourceCardEl) session.sourceCardEl.classList.remove("wbc-split-card-lifted");
  if (session.ghost.parentNode) session.ghost.parentNode.removeChild(session.ghost);
  if (session.zones.parentNode) session.zones.parentNode.removeChild(session.zones);
}

function wbcStartSplitDrag(context, event, dragSource) {
  var transfer = event && event.dataTransfer;
  if (!transfer) return;
  wbcSetSplitDrag(event);
  wbcHideNativeDragImage(transfer);
  var page = context.pageRef.current;
  if (!page) return;
  if (context.splitOverlayCleanupRef.current) context.splitOverlayCleanupRef.current();
  var ghost = document.createElement("div");
  ghost.className = "wbc-split-drag-ghost";
  var fromMainGrip = dragSource === "main";
  var session = {
    context: context, event: event, page: page, ghost: ghost,
    fromMainGrip: fromMainGrip,
    previewConversationSide: fromMainGrip
      ? (context.splitSide === "left" ? "right" : "left")
      : (context.splitSide === "left" ? "left" : "right"),
    grabOffset: null, panelW: 0, panelH: 0, cardW: 0, cardH: 0,
    restoreGhostViewport: null, clearTimer: null,
  };
  wbcSplitDragClonePanel(session);
  wbcSplitDragAppendRailCard(session);
  wbcSplitDragMountGhost(session);
  wbcSplitDragPositionGhost(session, event.clientX, event.clientY);
  wbcSplitDragCreateZones(session);
  session.onDragOver = function (nextEvent) { wbcSplitDragOver(session, nextEvent); };
  session.onDrop = function (nextEvent) { wbcSplitDragDrop(session, nextEvent); };
  session.cleanup = function () { wbcSplitDragCleanup(session); };
  document.addEventListener("dragover", session.onDragOver, true);
  document.addEventListener("drop", session.onDrop, true);
  context.splitOverlayCleanupRef.current = session.cleanup;
}

export { wbcStartSplitDrag }
