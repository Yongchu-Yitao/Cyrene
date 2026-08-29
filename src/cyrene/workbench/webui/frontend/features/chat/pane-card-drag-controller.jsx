import { wbcBuildRailCardDragPreview, wbcCaptureConversationViewport, wbcClonePaneWithLiveState, wbcPaneCardLocation, wbcRestoreConversationViewport, wbcT } from "../../workbench-chat.jsx"
import { WBC_PROJECT_FILE_DRAFTS, wbcMapItemLabel, wbcProjectFileDraftKey } from "./split-pane.jsx"
import { wbcBrowserStateForChat } from "./composer.jsx"

function wbcPaneCardDetachDescriptor(context, cardId, paneOverride) {
  var location = wbcPaneCardLocation(context.paneLayoutFor(), cardId);
  var pane = paneOverride || (location && location.card);
  if (!pane) return null;
  var descriptor = {
    kind: pane.kind, payload: pane.payload,
    ownerChatId: pane.ownerChatId || context.activeChatIdRef.current || "",
    project: context.project || null, title: "", items: [], agent: null, agents: [], draft: null,
  };
  if (pane.kind === "chat") {
    var detachedChatId = String(pane.payload || "");
    if (!detachedChatId) return null;
    var detachedChat = context.chatCache.details[detachedChatId]
      || (context.activeChat && String(context.activeChat.id || "") === detachedChatId ? context.activeChat : null);
    descriptor.title = detachedChat && detachedChat.title || wbcT("workbenchChat.chatSplitLabel", "Chat");
  } else if (pane.kind === "task") {
    var detachedTask = (Array.isArray(context.tasks) ? context.tasks : []).find(function (task) {
      return String(task && task.id || "") === String(pane.payload || "");
    });
    descriptor.title = detachedTask && detachedTask.title || wbcT("workbench.page.task", "Task");
  } else if (pane.kind === "file" || pane.kind === "viewer") {
    var detachedFile = pane.payload;
    descriptor.title = detachedFile && detachedFile.name || wbcT("workbenchChat.viewer", "Viewer");
    descriptor.items = context.artifactItems;
    var draftKey = wbcProjectFileDraftKey(detachedFile);
    descriptor.draft = draftKey && WBC_PROJECT_FILE_DRAFTS[draftKey]
      ? WBC_PROJECT_FILE_DRAFTS[draftKey] : null;
  } else if (pane.kind === "change") {
    descriptor.title = pane.payload && pane.payload.path || wbcT("workbenchChat.changes", "Changes");
  } else if (pane.kind === "map") {
    descriptor.title = wbcMapItemLabel(pane.payload) || wbcT("chat.side.map", "Map");
  } else if (pane.kind === "browser") {
    var browserState = wbcBrowserStateForChat(descriptor.ownerChatId);
    var browserTab = browserState && Array.isArray(browserState.tabs)
      ? browserState.tabs.find(function (tab) { return String(tab.id || "") === String(pane.payload || ""); })
      : null;
    descriptor.title = browserTab && (browserTab.title || browserTab.url) || wbcT("chat.side.browser", "Browser");
  } else if (pane.kind === "subagents") {
    descriptor.title = wbcT("workbenchChat.subagents", "Subagents");
  } else if (pane.kind === "terminal") {
    var terminal = context.terminals.find(function (item) {
      return String(item && item.id || "") === String(pane.payload || "");
    });
    descriptor.title = terminal && (terminal.displayTitle || terminal.title) || wbcT("terminal.title", "Terminal");
  } else if (pane.kind === "side-agent") {
    descriptor.agent = context.sideAgents.find(function (agent) {
      return String(agent && agent.id || "") === String(pane.payload || "");
    }) || null;
    descriptor.agents = context.sideAgents;
    descriptor.title = descriptor.agent && (descriptor.agent.title || descriptor.agent.sourceQuote)
      || wbcT("workbenchChat.sideAgent.tab", "Side questions");
  } else if (pane.kind === "plugin-view") {
    descriptor.title = String(
      pane.payload && (pane.payload.title || pane.payload.viewId || pane.payload.view_id)
      || "Plugin"
    );
  }
  return descriptor;
}

function wbcPreparePaneCardDetach(context, event, cardId, paneOverride) {
  if (!cardId) return null;
  var existing = context.paneCardDetachRef.current;
  var ownerChatId = String(context.activeChatIdRef.current || "");
  context.clearPaneCardDetachSubscription(existing);
  context.paneCardDetachRef.current = null;
  var dragHandle = event && event.currentTarget;
  var card = dragHandle && dragHandle.closest ? dragHandle.closest(".wbc-pane-card") : null;
  var descriptor = wbcPaneCardDetachDescriptor(context, cardId, paneOverride);
  if (!card || !dragHandle || !descriptor) return null;
  var cardRect = card.getBoundingClientRect();
  var handleRect = dragHandle.getBoundingClientRect();
  var capturedX = Number(dragHandle.dataset.wbcDragHandleX);
  var capturedY = Number(dragHandle.dataset.wbcDragHandleY);
  var handleGrabX = Number.isFinite(capturedX)
    ? Math.max(0, Math.min(handleRect.width, capturedX))
    : Math.max(0, Math.min(handleRect.width, Number(event.clientX) - handleRect.left));
  var handleGrabY = Number.isFinite(capturedY)
    ? Math.max(0, Math.min(handleRect.height, capturedY))
    : Math.max(0, Math.min(handleRect.height, Number(event.clientY) - handleRect.top));
  var sourceLocation = wbcPaneCardLocation(context.paneLayoutFor(ownerChatId), cardId);
  var pendingDetach = {
    cardId: String(cardId), layoutOwnerChatId: ownerChatId, descriptor: descriptor,
    sourceSide: sourceLocation && sourceLocation.side || "left",
    sourceIndex: sourceLocation ? sourceLocation.index : 0,
    sourceBounds: { width: Math.round(cardRect.width), height: Math.round(cardRect.height) },
    grabOffset: {
      x: Math.round((handleRect.left - cardRect.left) + handleGrabX),
      y: Math.round((handleRect.top - cardRect.top) + handleGrabY),
    },
  };
  context.paneCardDetachRef.current = pendingDetach;
  wbcBeginNativePaneDetach(context, pendingDetach);
  return pendingDetach;
}

function wbcBeginNativePaneDetach(context, pendingDetach) {
  var bridge = window.cyrene && window.cyrene.detachedPane;
  if (bridge && typeof bridge.onCreated === "function") {
    pendingDetach.unsubscribeCreated = bridge.onCreated(function (result) {
      if (!result || String(result.cardId || "") !== pendingDetach.cardId
        || String(result.layoutOwnerChatId || "") !== pendingDetach.layoutOwnerChatId) return;
      if (result.ok === false || result.detached !== true) {
        context.cancelPaneCardDetachment(pendingDetach);
        return;
      }
      context.completePaneCardDetachment(pendingDetach);
    });
  }
  if (bridge && typeof bridge.beginDrag === "function") {
    bridge.beginDrag(context.paneCardDetachIpcPayload(pendingDetach)).then(function (result) {
      if (!result || result.ok !== false) return;
      context.cancelPaneCardDetachment(pendingDetach);
    }).catch(function () { context.cancelPaneCardDetachment(pendingDetach); });
  }
}

function wbcCreatePaneDragSession(context, event, cardId, card, dragHandle) {
  var cardRect = card.getBoundingClientRect();
  var handleRect = dragHandle.getBoundingClientRect();
  var conversationViewport = wbcCaptureConversationViewport(card);
  var clonedCard = wbcClonePaneWithLiveState(card);
  var preview = clonedCard.clone;
  preview.classList.add("wbc-pane-card-drag-surface");
  preview.classList.remove("dragging");
  preview.removeAttribute("draggable");
  var ghost = document.createElement("div");
  ghost.className = "wbc-pane-card-drag-ghost";
  ghost.setAttribute("aria-hidden", "true");
  ghost.style.left = "0px"; ghost.style.top = "0px";
  ghost.style.width = cardRect.width + "px"; ghost.style.height = cardRect.height + "px";
  ghost.appendChild(preview);
  var sourceStyle = window.getComputedStyle(card);
  for (var propertyIndex = 0; propertyIndex < sourceStyle.length; propertyIndex += 1) {
    var propertyName = sourceStyle[propertyIndex];
    if (propertyName.indexOf("--") === 0) ghost.style.setProperty(propertyName, sourceStyle.getPropertyValue(propertyName));
  }
  var location = wbcPaneCardLocation(context.paneLayoutFor(), cardId);
  var descriptor = location && location.card;
  var draggedChatId = descriptor && descriptor.kind === "chat" ? String(descriptor.payload || "") : "";
  var railCard = draggedChatId
    ? Array.prototype.slice.call(document.querySelectorAll(".wbc-rail .wbc-chat-card")).find(function (candidate) {
        return String(candidate.dataset.chatId || "") === draggedChatId;
      })
    : null;
  var railElement = railCard && railCard.closest ? railCard.closest(".wbc-rail") : null;
  var railPreview = null;
  var railWidth = 0;
  var railHeight = 0;
  if (railCard) {
    var built = wbcBuildRailCardDragPreview(railCard, "wbc-pane-card-rail-drag-card");
    if (built) {
      railPreview = built.host;
      railWidth = Math.round(built.rect.width);
      railHeight = Math.round(built.rect.height);
      ghost.appendChild(railPreview);
    }
  }
  document.body.appendChild(ghost);
  clonedCard.restoreViewport();
  wbcRestoreConversationViewport(preview, conversationViewport);
  return {
    context: context, event: event, cardId: cardId, card: card, dragHandle: dragHandle,
    cardRect: cardRect, handleRect: handleRect, ghost: ghost, railCard: railCard,
    railElement: railElement, railPreview: railPreview, railWidth: railWidth,
    railHeight: railHeight, draggedChatId: draggedChatId,
  };
}

function wbcInitializePaneDragPointer(session) {
  var event = session.event;
  var handle = session.dragHandle;
  var eventX = Number(event.clientX);
  var eventY = Number(event.clientY);
  var initialX = Number.isFinite(eventX) && eventX !== 0 ? eventX : Number(handle.dataset.wbcDragClientX);
  var initialY = Number.isFinite(eventY) && eventY !== 0 ? eventY : Number(handle.dataset.wbcDragClientY);
  var capturedX = Number(handle.dataset.wbcDragHandleX);
  var capturedY = Number(handle.dataset.wbcDragHandleY);
  var handleGrabX = Number.isFinite(capturedX)
    ? Math.max(0, Math.min(session.handleRect.width, capturedX))
    : Math.max(0, Math.min(session.handleRect.width, initialX - session.handleRect.left));
  var handleGrabY = Number.isFinite(capturedY)
    ? Math.max(0, Math.min(session.handleRect.height, capturedY))
    : Math.max(0, Math.min(session.handleRect.height, initialY - session.handleRect.top));
  session.grabX = (session.handleRect.left - session.cardRect.left) + handleGrabX;
  session.grabY = (session.handleRect.top - session.cardRect.top) + handleGrabY;
  session.lastPointer = {
    clientX: initialX, clientY: initialY,
    screenX: Number(event.screenX), screenY: Number(event.screenY),
  };
  if (session.railPreview) {
    var railGrabX = session.railWidth && session.handleRect.width
      ? session.railWidth * (handleGrabX / session.handleRect.width) : 0;
    var railGrabY = session.railHeight && session.handleRect.height
      ? session.railHeight * (handleGrabY / session.handleRect.height) : 0;
    session.railPreview.style.left = (session.grabX - railGrabX) + "px";
    session.railPreview.style.top = (session.grabY - railGrabY) + "px";
  }
}

function wbcPaneDragTargetAt(session, clientX, clientY) {
  var context = session.context;
  var layout = context.paneLayoutFor();
  var sourceLocation = wbcPaneCardLocation(layout, session.cardId);
  var sourceSide = sourceLocation && sourceLocation.side;
  var oppositeSide = sourceSide === "left" ? "right" : "left";
  var sourceStack = sourceSide ? (layout[sourceSide] || []) : [];
  var layoutElement = session.card && session.card.closest ? session.card.closest(".wbc-pane-layout") : null;
  var layoutRect = layoutElement && layoutElement.getBoundingClientRect();
  if (sourceLocation && sourceStack.length === 2 && !(layout[oppositeSide] || []).length
    && layoutRect && layoutRect.width > 0 && clientX >= layoutRect.left && clientX <= layoutRect.right
    && clientY >= layoutRect.top && clientY <= layoutRect.bottom) {
    var relativeX = (clientX - layoutRect.left) / layoutRect.width;
    var axisEdge = relativeX < 0.34 ? "left" : (relativeX > 0.66 ? "right" : "");
    if (axisEdge) {
      var companion = sourceStack[sourceLocation.index === 0 ? 1 : 0];
      return { cardId: String(companion && companion.id || ""), dropKey: "axis:" + axisEdge, edge: axisEdge };
    }
  }
  var elements = typeof document.elementsFromPoint === "function"
    ? document.elementsFromPoint(clientX, clientY) : [document.elementFromPoint(clientX, clientY)];
  var targetCard = null;
  for (var index = 0; index < elements.length; index += 1) {
    var candidate = elements[index] && elements[index].closest ? elements[index].closest(".wbc-pane-card") : null;
    if (candidate && String(candidate.dataset.paneCardId || "") !== String(session.cardId || "")) {
      targetCard = candidate;
      break;
    }
  }
  if (!targetCard) return null;
  var targetId = String(targetCard.dataset.paneCardId || "");
  var location = wbcPaneCardLocation(layout, targetId);
  if (!location) return null;
  var rect = targetCard.getBoundingClientRect();
  var relativeY = rect.height > 0 ? (clientY - rect.top) / rect.height : 0.5;
  var edge = (layout[location.side] || []).length >= 2
    ? "replace" : (relativeY < 0.34 ? "top" : (relativeY > 0.66 ? "bottom" : "replace"));
  return { cardId: targetId, dropKey: String(targetCard.dataset.paneDropKey || targetId), edge: edge };
}

function wbcPaneDragPointIn(element, clientX, clientY) {
  var rect = element && element.getBoundingClientRect();
  return !!(rect && clientX >= rect.left && clientX <= rect.right
    && clientY >= rect.top && clientY <= rect.bottom);
}

function wbcMovePaneCardGhost(session, event) {
  var clientX = Number(event && event.clientX);
  var clientY = Number(event && event.clientY);
  if (!Number.isFinite(clientX) || !Number.isFinite(clientY) || (clientX === 0 && clientY === 0)) return;
  session.lastPointer = {
    clientX: clientX, clientY: clientY,
    screenX: Number(event && event.screenX), screenY: Number(event && event.screenY),
  };
  var bridge = window.cyrene && window.cyrene.detachedPane;
  if (bridge && typeof bridge.updateDrag === "function") {
    bridge.updateDrag({
      clientX: clientX, clientY: clientY,
      screenX: Number(event && event.screenX), screenY: Number(event && event.screenY),
      viewportWidth: window.innerWidth, viewportHeight: window.innerHeight,
    });
  }
  var overRail = !!(session.railPreview && session.railElement
    && wbcPaneDragPointIn(session.railElement, clientX, clientY));
  var overMatchingCard = wbcPaneDragPointIn(session.railCard, clientX, clientY);
  if (session.ghostOverRail !== overRail) {
    session.ghostOverRail = overRail;
    session.ghost.classList.toggle("rail-card", overRail);
    if (session.railCard) session.railCard.classList.toggle("dragging", overRail);
  }
  if (session.railCard) session.railCard.classList.toggle("wbc-split-return-target", overMatchingCard);
  session.pointerPaneTarget = overRail ? null : wbcPaneDragTargetAt(session, clientX, clientY);
  session.context.setPaneDropTarget(session.pointerPaneTarget);
  if (event && typeof event.preventDefault === "function") {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  }
  session.ghost.style.transform = "translate3d(" + (clientX - session.grabX) + "px, " + (clientY - session.grabY) + "px, 0)";
}

function wbcRetirePaneCardGhost(session) {
  if (session.ghostRetired) return;
  session.ghostRetired = true;
  session.ghost.style.visibility = "hidden";
  function detachGhost() {
    if (session.ghost.parentNode) session.ghost.parentNode.removeChild(session.ghost);
  }
  if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(detachGhost, { timeout: 300 });
  else setTimeout(detachGhost, 80);
}

function wbcClearPaneCardGhost(session) {
  if (session.ghostCleared) return;
  session.ghostCleared = true;
  document.removeEventListener("pointermove", session.move, true);
  document.removeEventListener("pointerup", session.finish, true);
  document.removeEventListener("pointercancel", session.finish, true);
  document.removeEventListener("lostpointercapture", session.finishLostCapture, true);
  if (session.railCard) session.railCard.classList.remove("dragging", "wbc-split-return-target");
  var reducedMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var fade = typeof session.ghost.animate === "function"
    ? session.ghost.animate([{ opacity: 0.92 }, { opacity: 0 }], {
        duration: reducedMotion ? 0 : 72, easing: "cubic-bezier(.4, 0, 1, 1)", fill: "forwards",
      })
    : null;
  var retire = function () { wbcRetirePaneCardGhost(session); };
  if (fade) Promise.resolve(fade.finished).then(retire).catch(retire);
  else session.ghost.addEventListener("transitionend", function (event) {
    if (event.propertyName === "opacity") retire();
  }, { once: true });
  session.ghost.classList.add("releasing");
  setTimeout(retire, reducedMotion ? 0 : 120);
  if (session.context.paneCardDragImageCleanupRef.current === session.clear) {
    session.context.paneCardDragImageCleanupRef.current = null;
  }
}

function wbcFinishPaneCardGhost(session, event) {
  var clientX = Number(event && event.clientX);
  var clientY = Number(event && event.clientY);
  if (!Number.isFinite(clientX)) clientX = session.lastPointer.clientX;
  if (!Number.isFinite(clientY)) clientY = session.lastPointer.clientY;
  var droppedOnRail = !!(session.railPreview && session.railElement
    && wbcPaneDragPointIn(session.railElement, clientX, clientY));
  var droppedOnMatchingCard = wbcPaneDragPointIn(session.railCard, clientX, clientY);
  if (droppedOnRail) {
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    if (event && typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();
  }
  wbcClearPaneCardGhost(session);
  if (droppedOnMatchingCard && session.draggedChatId) session.context.closePaneCard(session.cardId);
  else if (session.pointerPaneTarget) {
    session.context.placeExistingPaneCard(
      session.cardId, session.pointerPaneTarget.cardId, session.pointerPaneTarget.edge
    );
  }
  session.context.handlePaneCardDragEnd(event, {
    cancel: false,
    screenX: Number.isFinite(Number(event && event.screenX)) ? Number(event.screenX) : session.lastPointer.screenX,
    screenY: Number.isFinite(Number(event && event.screenY)) ? Number(event.screenY) : session.lastPointer.screenY,
  });
}

function wbcStartPaneCardDrag(context, event, cardId, paneOverride) {
  if (!cardId) return;
  var card = event.currentTarget && event.currentTarget.closest
    ? event.currentTarget.closest(".wbc-pane-card") : null;
  var dragHandle = event.currentTarget;
  if (!card || !dragHandle) return;
  var pendingDetach = wbcPreparePaneCardDetach(context, event, cardId, paneOverride);
  if (!pendingDetach) return;
  context.setPaneCardDragId(String(cardId));
  if (context.paneCardDragImageCleanupRef.current) context.paneCardDragImageCleanupRef.current();
  var session = wbcCreatePaneDragSession(context, event, cardId, card, dragHandle);
  wbcInitializePaneDragPointer(session);
  session.ghostOverRail = false;
  session.pointerPaneTarget = null;
  session.ghostCleared = false;
  session.ghostRetired = false;
  session.move = function (nextEvent) { wbcMovePaneCardGhost(session, nextEvent); };
  session.clear = function () { wbcClearPaneCardGhost(session); };
  session.finish = function (nextEvent) { wbcFinishPaneCardGhost(session, nextEvent); };
  session.finishLostCapture = function (captureEvent) {
    if (captureEvent && captureEvent.target !== dragHandle) return;
    wbcFinishPaneCardGhost(session, captureEvent || session.lastPointer);
  };
  document.addEventListener("pointermove", session.move, true);
  document.addEventListener("pointerup", session.finish, true);
  document.addEventListener("pointercancel", session.finish, true);
  document.addEventListener("lostpointercapture", session.finishLostCapture, true);
  context.paneCardDragImageCleanupRef.current = session.clear;
  wbcMovePaneCardGhost(session, event);
}

function wbcFinishPaneCardDrag(context, options) {
  if (context.paneCardDragImageCleanupRef.current) context.paneCardDragImageCleanupRef.current();
  context.setPaneCardDragId("");
  context.setPaneDropTarget(null);
  var pendingDetach = context.paneCardDetachRef.current;
  var bridge = window.cyrene && window.cyrene.detachedPane;
  if (!pendingDetach || !bridge || typeof bridge.finishDrag !== "function") {
    context.clearPaneCardDetachSubscription(pendingDetach);
    if (context.paneCardDetachRef.current === pendingDetach) context.paneCardDetachRef.current = null;
    return;
  }
  bridge.finishDrag(context.paneCardDetachIpcPayload(pendingDetach, {
    cancel: !!(options && options.cancel),
    screenX: Number(options && options.screenX),
    screenY: Number(options && options.screenY),
  })).then(function (result) {
    if (result && result.pending === true) return;
    if (result && result.ok !== false && result.detached === true) {
      context.completePaneCardDetachment(pendingDetach);
      return;
    }
    context.cancelPaneCardDetachment(pendingDetach);
  }).catch(function () { context.cancelPaneCardDetachment(pendingDetach); });
}

export { wbcFinishPaneCardDrag, wbcStartPaneCardDrag }
