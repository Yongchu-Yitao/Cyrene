import { wbcPaneCard, wbcPaneCardLocation } from "../../workbench-chat.jsx"

function wbcClearPaneCardDetachSubscription(pendingDetach) {
  if (!pendingDetach || typeof pendingDetach.unsubscribeCreated !== "function") return;
  pendingDetach.unsubscribeCreated();
  pendingDetach.unsubscribeCreated = null;
}

function wbcCancelPaneCardDetachment(context, pendingDetach) {
  wbcClearPaneCardDetachSubscription(pendingDetach);
  if (context.paneCardDetachRef.current === pendingDetach) context.paneCardDetachRef.current = null;
  if (context.paneCardDragImageCleanupRef.current) context.paneCardDragImageCleanupRef.current();
  context.setPaneCardDragId("");
  context.setPaneDropTarget(null);
}

function wbcPaneCardDetachIpcPayload(pendingDetach, extra) {
  if (!pendingDetach) return Object.assign({}, extra || {});
  return Object.assign({
    cardId: String(pendingDetach.cardId || ""),
    layoutOwnerChatId: String(pendingDetach.layoutOwnerChatId || ""),
    descriptor: pendingDetach.descriptor,
    sourceSide: pendingDetach.sourceSide,
    sourceIndex: pendingDetach.sourceIndex,
    sourceBounds: pendingDetach.sourceBounds,
    grabOffset: pendingDetach.grabOffset,
  }, extra || {});
}

function wbcSelectAlternateChatAfterDetachment(context, pendingDetach) {
  var ownerChatId = String(pendingDetach && pendingDetach.layoutOwnerChatId || "");
  var descriptor = pendingDetach && pendingDetach.descriptor;
  var detachedChatId = String(descriptor && descriptor.kind === "chat" ? descriptor.payload || "" : "");
  var canonicalCardId = detachedChatId ? "chat:" + detachedChatId : "";
  if (
    !detachedChatId
    || detachedChatId !== ownerChatId
    || String(pendingDetach && pendingDetach.cardId || "") !== canonicalCardId
    || String(context.activeChatIdRef.current || "") !== ownerChatId
  ) return;
  var layout = context.paneLayoutFor(ownerChatId);
  var remainingPaneChat = layout.left.concat(layout.right).find(function (card) {
    return card
      && card.kind === "chat"
      && String(card.id || "") !== String(pendingDetach.cardId || "");
  });
  if (remainingPaneChat) return;
  var alternate = (context.chatsRef.current || []).find(function (chat) {
    var candidateId = String(chat && chat.id || "");
    return candidateId && candidateId !== detachedChatId;
  });
  if (alternate && alternate.id) context.selectChat(String(alternate.id));
}

function wbcRemoveDetachedPaneCard(context, pendingDetach) {
  wbcSelectAlternateChatAfterDetachment(context, pendingDetach);
  context.closePaneCard(pendingDetach.cardId, pendingDetach.layoutOwnerChatId);
}

function wbcCompletePaneCardDetachment(context, pendingDetach) {
  if (!pendingDetach || pendingDetach.completed) return;
  pendingDetach.completed = true;
  wbcClearPaneCardDetachSubscription(pendingDetach);
  if (context.paneCardDetachRef.current === pendingDetach) context.paneCardDetachRef.current = null;
  if (context.paneCardDragImageCleanupRef.current) context.paneCardDragImageCleanupRef.current();
  context.setPaneCardDragId("");
  context.setPaneDropTarget(null);
  var sourceCard = Array.prototype.slice.call(document.querySelectorAll(".wbc-pane-card")).find(function (candidate) {
    return String(candidate.dataset.paneCardId || "") === String(pendingDetach.cardId || "");
  });
  var reducedMotion = !!(window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  if (!sourceCard || reducedMotion || typeof sourceCard.animate !== "function") {
    wbcRemoveDetachedPaneCard(context, pendingDetach);
    return;
  }
  var animation = sourceCard.animate([
    { opacity: 1, transform: "scale(1) translate3d(0, 0, 0)" },
    { opacity: 0.82, transform: "scale(.985) translate3d(0, 2px, 0)", offset: 0.66 },
    { opacity: 0, transform: "scale(.965) translate3d(0, -6px, 0)" },
  ], {
    duration: 190,
    easing: "cubic-bezier(.22, 1.12, .36, 1)",
    fill: "forwards",
  });
  Promise.resolve(animation.finished).catch(function () {}).then(function () {
    wbcRemoveDetachedPaneCard(context, pendingDetach);
  });
}

function wbcRestoreReturnedDetachedPane(context, info) {
  var returned = info && typeof info === "object" ? info : {};
  var descriptor = returned.descriptor && typeof returned.descriptor === "object"
    ? returned.descriptor
    : null;
  var cardId = String(returned.cardId || "");
  var ownerChatId = String(returned.layoutOwnerChatId || descriptor && descriptor.ownerChatId || "");
  if (!descriptor || !descriptor.kind || !cardId) return;
  var restoredCard = wbcPaneCard(descriptor.kind, descriptor.payload, {
    id: cardId,
    ownerChatId: descriptor.ownerChatId || ownerChatId,
    meta: descriptor.meta,
  });
  var side = returned.sourceSide === "right" ? "right" : "left";
  var index = Math.max(0, Math.min(1, Number(returned.sourceIndex) || 0));
  context.updatePaneLayout(function (current) {
    if (wbcPaneCardLocation(current, cardId)) return current;
    var next = {
      left: current.left.slice(), right: current.right.slice(),
      leftRatio: current.leftRatio, rightRatio: current.rightRatio,
    };
    if (next[side].length < 2) next[side].splice(Math.min(index, next[side].length), 0, restoredCard);
    else next[side][index] = restoredCard;
    return next;
  }, ownerChatId);
  if (descriptor.kind === "chat" && descriptor.payload) {
    context.selectChat(String(descriptor.payload));
  }
}

export {
  wbcCancelPaneCardDetachment,
  wbcClearPaneCardDetachSubscription,
  wbcCompletePaneCardDetachment,
  wbcPaneCardDetachIpcPayload,
  wbcRestoreReturnedDetachedPane,
}
