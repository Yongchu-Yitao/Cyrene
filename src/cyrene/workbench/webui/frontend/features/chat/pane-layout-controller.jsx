import { wbcDefaultPaneLayout, wbcErrorText, wbcNormalizePaneLayout, wbcPaneCard, wbcPaneCardLocation } from "../../workbench-chat.jsx"
import { wbcEditableChatFileResource } from "./split-pane.jsx"

function wbcProjectPaneOwnerKey(context) {
  return context.projectId ? "project:" + String(context.projectId) : "";
}

function wbcPaneOwnerKey(context, chatId) {
  return String(chatId || context.activeChatIdRef.current || wbcProjectPaneOwnerKey(context));
}

function wbcPaneLayoutFor(context, chatId) {
  var ownerChatId = String(chatId || context.activeChatIdRef.current || "");
  var ownerId = wbcPaneOwnerKey(context, ownerChatId);
  return wbcNormalizePaneLayout(context.paneLayoutsByChat[ownerId], ownerChatId);
}

function wbcUpdatePaneLayout(context, updater, ownerChatId) {
  var chatId = String(ownerChatId || context.activeChatIdRef.current || "");
  var ownerId = wbcPaneOwnerKey(context, chatId);
  if (!ownerId) return;
  context.setPaneLayoutsByChat(function (current) {
    var previous = wbcNormalizePaneLayout(current[ownerId], chatId);
    var updated = typeof updater === "function" ? updater(previous) : updater;
    return Object.assign({}, current, { [ownerId]: wbcNormalizePaneLayout(updated, chatId) });
  });
}

function wbcRestoreTerminalReplacement(context, ownerChatId) {
  var chatId = String(ownerChatId || "");
  var layout = wbcPaneLayoutFor(context, chatId);
  var cards = layout.left.concat(layout.right);
  if (cards.length !== 1 || cards[0].kind !== "terminal") return false;
  var terminalCard = cards[0];
  var restore = context.paneLayoutRestoreRef.current[terminalCard.id];
  if (!restore) return false;
  delete context.paneLayoutRestoreRef.current[terminalCard.id];
  wbcUpdatePaneLayout(context, restore, chatId);
  context.setActiveTerminalId("");
  context.terminalClient.activate(context.projectId, null).catch(function () {});
  return true;
}

function wbcPaneContentCard(context, type, payload, ownerChatId) {
  var normalizedType = type === "artifact" ? "file" : String(type || "");
  return wbcPaneCard(normalizedType, payload, {
    ownerChatId: ownerChatId || context.activeChatIdRef.current || wbcProjectPaneOwnerKey(context),
  });
}

function wbcPromotePaneSourceLayout(layout, source, card) {
  var sourceColumn = (layout[source.side] || []).slice();
  var oppositeSide = source.side === "left" ? "right" : "left";
  // A lone vertical stack still has an unused outer column. Keep both stacked
  // panes and use that column instead of dropping the source pane's sibling.
  if (sourceColumn.length === 2 && !(layout[oppositeSide] || []).length) {
    return {
      left: sourceColumn,
      right: [card],
      leftRatio: source.side === "right" ? layout.rightRatio : layout.leftRatio,
      rightRatio: layout.rightRatio,
    };
  }
  return {
    left: [source.card],
    right: [card],
    leftRatio: layout.leftRatio,
    rightRatio: layout.rightRatio,
  };
}

function wbcOpenPaneContent(context, type, payload, options) {
  var opts = options || {};
  var ownerChatId = String(opts.ownerChatId || context.activeChatIdRef.current || "");
  var ownerId = wbcPaneOwnerKey(context, ownerChatId);
  if (!ownerId || !type) return null;
  var normalizedType = type === "artifact" ? "file" : String(type || "");
  if (normalizedType === "file" || normalizedType === "viewer") {
    payload = wbcEditableChatFileResource({ projectId: context.projectId }, payload);
  }
  var canonicalId = ["chat", "terminal"].indexOf(normalizedType) >= 0 && payload
    ? normalizedType + ":" + String(payload) : "";
  var baseCard = wbcPaneContentCard(context, normalizedType, payload, ownerId);
  var reusableId = canonicalId || (normalizedType === "plugin-view" ? baseCard.id : "");
  var existing = reusableId ? wbcPaneCardLocation(wbcPaneLayoutFor(context, ownerChatId), reusableId) : null;
  if (normalizedType === "terminal" && existing && !opts.replaceWorkspace) return existing.card;
  var card = existing
    ? wbcPaneCard(normalizedType, payload, { ownerChatId: ownerId, freshInstance: true })
    : baseCard;
  wbcUpdatePaneLayout(context, function (layout) {
    var source = opts.sourceCardId ? wbcPaneCardLocation(layout, opts.sourceCardId) : null;
    var targetSide = opts.side === "left" || opts.side === "right"
      ? opts.side : (source && source.side === "right" ? "left" : "right");
    if (opts.restore) context.paneLayoutRestoreRef.current[card.id] = layout;
    if (opts.restore && Object.prototype.hasOwnProperty.call(opts, "restoreLayout")) {
      context.paneLayoutRestoreRef.current[card.id] = opts.restoreLayout;
    }
    var next = {
      left: layout.left.slice(), right: layout.right.slice(),
      leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
    };
    if (opts.replaceWorkspace) { next.left = [card]; next.right = []; return next; }
    if (opts.promoteSourceLeft && source) return wbcPromotePaneSourceLayout(layout, source, card);
    next[targetSide] = [card];
    return next;
  }, ownerChatId);
  return card;
}

function wbcUpdatePaneCard(context, cardId, updater) {
  wbcUpdatePaneLayout(context, function (layout) {
    var next = {
      left: layout.left.slice(), right: layout.right.slice(),
      leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
    };
    var location = wbcPaneCardLocation(next, cardId);
    if (!location) return next;
    next[location.side][location.index] = typeof updater === "function" ? updater(location.card) : updater;
    return next;
  });
}

function wbcClosePaneCard(context, cardId, requestedOwnerChatId) {
  var ownerChatId = String(requestedOwnerChatId != null ? requestedOwnerChatId : (context.activeChatIdRef.current || ""));
  var ownerId = wbcPaneOwnerKey(context, ownerChatId);
  var restore = context.paneLayoutRestoreRef.current[cardId];
  if (restore) {
    delete context.paneLayoutRestoreRef.current[cardId];
    wbcUpdatePaneLayout(context, restore, ownerChatId);
    return;
  }
  var layout = wbcPaneLayoutFor(context, ownerChatId);
  var location = wbcPaneCardLocation(layout, cardId);
  if (!location) return;
  var remaining = layout.left.concat(layout.right).filter(function (card) { return String(card.id) !== String(cardId); });
  var nextChat = location.card.kind === "chat" && String(location.card.payload || "") === ownerChatId
    ? remaining.find(function (card) { return card.kind === "chat"; }) : null;
  var next = {
    left: layout.left.filter(function (card) { return String(card.id) !== String(cardId); }),
    right: layout.right.filter(function (card) { return String(card.id) !== String(cardId); }),
    leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
  };
  if (!next.left.length && next.right.length) { next.left = next.right; next.right = []; }
  if (!next.left.length && !next.right.length) next = wbcDefaultPaneLayout(ownerChatId);
  if (nextChat && nextChat.payload) {
    context.setPaneLayoutsByChat(function (current) {
      var updated = Object.assign({}, current);
      updated[String(nextChat.payload)] = next;
      delete updated[ownerId];
      return updated;
    });
    if (String(context.activeChatIdRef.current || "") === ownerChatId) context.selectChat(String(nextChat.payload));
    return;
  }
  wbcUpdatePaneLayout(context, next, ownerChatId);
}

function wbcCloseDeletedChatSplits(context, chatId) {
  var deletedChatId = String(chatId || "");
  if (!deletedChatId) return;
  var detachedBridge = window.cyrene && window.cyrene.detachedPane;
  if (detachedBridge && typeof detachedBridge.closeByChat === "function") {
    detachedBridge.closeByChat(deletedChatId).catch(function () {});
  }
  context.setPaneLayoutsByChat(function (current) {
    var updated = Object.assign({}, current);
    var changed = false;
    Object.keys(current).forEach(function (ownerId) {
      if (String(ownerId) === deletedChatId) { delete updated[ownerId]; changed = true; return; }
      var ownerChatId = String(ownerId).indexOf("project:") === 0 ? "" : String(ownerId);
      var layout = wbcNormalizePaneLayout(current[ownerId], ownerChatId);
      var left = layout.left.filter(function (card) { return !(card && card.kind === "chat" && String(card.payload || "") === deletedChatId); });
      var right = layout.right.filter(function (card) { return !(card && card.kind === "chat" && String(card.payload || "") === deletedChatId); });
      if (left.length === layout.left.length && right.length === layout.right.length) return;
      if (!left.length && right.length) { left = right; right = []; }
      updated[ownerId] = wbcNormalizePaneLayout({
        left: left, right: right, leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
      }, ownerChatId);
      changed = true;
    });
    return changed ? updated : current;
  });
  context.setResourceSplitByChat(function (current) {
    var updated = Object.assign({}, current);
    var changed = false;
    Object.keys(current).forEach(function (ownerId) {
      var resource = current[ownerId];
      if (String(ownerId) === deletedChatId
        || (resource && resource.type === "chat" && String(resource.payload || "") === deletedChatId)) {
        delete updated[ownerId]; changed = true;
      }
    });
    return changed ? updated : current;
  });
  Object.keys(context.paneLayoutRestoreRef.current).forEach(function (cardId) {
    var restore = context.paneLayoutRestoreRef.current[cardId];
    var cards = restore && (restore.left || []).concat(restore.right || []);
    if (cards && cards.some(function (card) {
      return card && card.kind === "chat" && String(card.payload || "") === deletedChatId;
    })) delete context.paneLayoutRestoreRef.current[cardId];
  });
  var floatingRestore = context.floatingSplitRestoreRef.current;
  if (floatingRestore && (String(floatingRestore.chatId || "") === deletedChatId
    || String(floatingRestore.activeChatId || "") === deletedChatId)) {
    context.floatingSplitRestoreRef.current = null;
  }
}

function wbcMovePaneCardOtherSide(context, cardId) {
  wbcUpdatePaneLayout(context, function (layout) {
    var location = wbcPaneCardLocation(layout, cardId);
    if (!location) return layout;
    var next = {
      left: layout.left.slice(), right: layout.right.slice(),
      leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
    };
    if (next[location.side].length === 2) next[location.side].reverse();
    else { var left = next.left; next.left = next.right; next.right = left; }
    return next;
  });
}

function wbcMovePaneCardLayout(layout, cardId, options) {
  var opts = options || {};
  var targetSide = String(opts.side || "");
  if (targetSide !== "left" && targetSide !== "right") {
    throw new Error("pane side must be left or right");
  }
  var source = wbcPaneCardLocation(layout, cardId);
  if (!source) throw new Error("pane card is not available");
  var position = String(opts.position || "");
  if (!position) position = source.side === targetSide && source.index === 0 ? "top" : "bottom";
  if (position !== "top" && position !== "bottom") {
    throw new Error("pane position must be top or bottom");
  }
  if (source.side !== targetSide && (layout[targetSide] || []).length >= 2) {
    throw new Error("target pane column is full");
  }
  var next = {
    left: layout.left.slice(), right: layout.right.slice(),
    leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
  };
  var moving = next[source.side].splice(source.index, 1)[0];
  var target = next[targetSide];
  if (position === "top") target.unshift(moving);
  else target.push(moving);
  return next;
}

function wbcMovePaneCard(context, cardId, options) {
  var layout = wbcPaneLayoutFor(context);
  var next = wbcMovePaneCardLayout(layout, cardId, options);
  var location = wbcPaneCardLocation(next, cardId);
  wbcUpdatePaneLayout(context, next);
  return {
    card_id: String(cardId || ""),
    side: location && location.side || "",
    position: location && location.index === 0 ? "top" : "bottom",
  };
}

function wbcSwapPaneCardsLayout(layout, firstCardId, secondCardId) {
  var first = wbcPaneCardLocation(layout, firstCardId);
  var second = wbcPaneCardLocation(layout, secondCardId);
  if (!first || !second) throw new Error("both pane cards must be available");
  if (String(firstCardId || "") === String(secondCardId || "")) return layout;
  var next = {
    left: layout.left.slice(), right: layout.right.slice(),
    leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
  };
  next[first.side][first.index] = second.card;
  next[second.side][second.index] = first.card;
  return next;
}

function wbcSwapPaneCards(context, firstCardId, secondCardId) {
  var next = wbcSwapPaneCardsLayout(wbcPaneLayoutFor(context), firstCardId, secondCardId);
  wbcUpdatePaneLayout(context, next);
  return { first_card_id: String(firstCardId || ""), second_card_id: String(secondCardId || "") };
}

function wbcCreatePaneConversation(context, cardId) {
  var layout = wbcPaneLayoutFor(context);
  var location = wbcPaneCardLocation(layout, cardId);
  if (!location || layout[location.side].length > 1 || !context.projectId) return Promise.resolve(null);
  return context.model.createChat(context.projectId).then(function (chat) {
    context.chatCache.details[chat.id] = chat;
    context.setChats(function (previous) { return [chat].concat(previous); });
    wbcUpdatePaneLayout(context, function (current) {
      var liveLocation = wbcPaneCardLocation(current, cardId);
      if (!liveLocation || current[liveLocation.side].length > 1) return current;
      var next = {
        left: current.left.slice(), right: current.right.slice(),
        leftRatio: current.leftRatio, rightRatio: current.rightRatio,
      };
      next[liveLocation.side] = [
        liveLocation.card,
        wbcPaneCard("chat", chat.id, { id: "chat:" + chat.id, ownerChatId: chat.id }),
      ];
      return next;
    });
    return chat;
  }).catch(function (error) {
    context.setErrorKind("message");
    context.setError(wbcErrorText(error));
    return null;
  });
}

function wbcResizePaneRow(context, side, ratio) {
  var normalizedSide = side === "left" ? "left" : "right";
  var nextRatio = Math.max(0.2, Math.min(0.8, Number(ratio) || 0.5));
  try { localStorage.setItem("wbc-pane-" + normalizedSide + "-height", String(nextRatio)); } catch (error) {}
  wbcUpdatePaneLayout(context, function (layout) {
    return Object.assign({}, layout, { [normalizedSide + "Ratio"]: nextRatio });
  });
}

export {
  wbcCloseDeletedChatSplits, wbcClosePaneCard, wbcCreatePaneConversation,
  wbcMovePaneCard, wbcMovePaneCardLayout, wbcMovePaneCardOtherSide,
  wbcOpenPaneContent,
  wbcPaneContentCard, wbcPaneLayoutFor, wbcPaneOwnerKey, wbcProjectPaneOwnerKey,
  wbcResizePaneRow, wbcRestoreTerminalReplacement, wbcUpdatePaneCard, wbcUpdatePaneLayout,
  wbcSwapPaneCards, wbcSwapPaneCardsLayout,
}
