function wbcWorkspaceDisplayName(path) {
  var normalized = String(path || "").replace(/[\\/]+$/, "");
  return normalized.split(/[\\/]/).filter(Boolean).pop() || normalized || "…";
}

var WBC_RESOURCE_DRAG_MIME = "application/x-cyrene-work-resource+json";
var WBC_CHAT_DRAG_MIME = "application/x-cyrene-chat+json";
var WBC_PLUGIN_VIEW_DRAG_MIME = "application/x-cyrene-plugin-view+json";
var WBC_CHAT_GROUP_DRAG_MIME = "application/x-cyrene-chat-group+json";
var WBC_AGENT_CHAT_FLOW_EVENT = "cyrene:agent-chat-flow";
var WBC_AGENT_CHAT_FLOW_TTLS = { created: 4200, typing: 3200 };
var WBC_AGENT_CHAT_FLOW_STATE = Object.create(null);
// Chromium on macOS shows a globe placeholder when setDragImage receives an
// image that was created during dragstart and has not finished loading yet.
// Keep one transparent pixel ready for every custom DOM drag preview.
var WBC_EMPTY_DRAG_IMAGE = new Image(1, 1);
WBC_EMPTY_DRAG_IMAGE.src = "data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==";

function wbcHideNativeDragImage(transfer) {
  if (!transfer || !WBC_EMPTY_DRAG_IMAGE.complete) return;
  try {
    transfer.setDragImage(WBC_EMPTY_DRAG_IMAGE, 0, 0);
  } catch (e) {}
}

// Build every conversation-row drag preview through the same DOM and inline
// box model. In particular, the preview must not inherit the real rail's
// clipping/isolation rules or its rounded elevation gets cropped.
function wbcBuildRailCardDragPreview(root, extraClassName) {
  if (!root || !root.querySelectorAll) return null;
  var rect = root.getBoundingClientRect();
  var host = document.createElement("div");
  host.className = "wbc-rail wbc-native-chat-drag-image" + (extraClassName ? " " + extraClassName : "");
  host.setAttribute("aria-hidden", "true");
  host.style.width = rect.width + "px";
  host.style.height = rect.height + "px";
  host.style.display = "block";
  host.style.padding = "0";
  host.style.overflow = "visible";
  host.style.isolation = "auto";
  host.style.background = "transparent";
  host.style.pointerEvents = "none";
  var sourceStyle = window.getComputedStyle(root);
  for (var index = 0; index < sourceStyle.length; index += 1) {
    var property = sourceStyle[index];
    if (property.indexOf("--") === 0) {
      host.style.setProperty(property, sourceStyle.getPropertyValue(property));
    }
  }
  var clone = root.cloneNode(true);
  clone.classList.remove(
    "track-marker-ready",
    "dragging",
    "menu-open",
    "group-drop-target",
    "wbc-split-card-lifted",
    "wbc-split-return-target"
  );
  clone.querySelectorAll(".track-marker-ready").forEach(function (card) {
    card.classList.remove("track-marker-ready");
  });
  clone.querySelectorAll(".wbc-chat-row-icon").forEach(function (icon) {
    icon.style.opacity = "1";
  });
  clone.style.width = rect.width + "px";
  clone.style.height = rect.height + "px";
  clone.style.margin = "0";
  clone.style.opacity = "1";
  clone.style.transform = "none";
  host.appendChild(clone);
  return { host: host, clone: clone, rect: rect };
}

function wbcAgentChatFlowSnapshot(chatId) {
  var normalizedChatId = String(chatId || "").trim();
  var current = normalizedChatId ? WBC_AGENT_CHAT_FLOW_STATE[normalizedChatId] : null;
  if (!current) return null;
  if (Number(current.expiresAt || 0) <= Date.now()) {
    delete WBC_AGENT_CHAT_FLOW_STATE[normalizedChatId];
    return null;
  }
  return { chatId: normalizedChatId, kind: current.kind, expiresAt: current.expiresAt };
}

function wbcNotifyAgentChatFlow(kind, chatId) {
  var normalizedKind = String(kind || "").trim();
  var normalizedChatId = String(chatId || "").trim();
  var ttl = Number(WBC_AGENT_CHAT_FLOW_TTLS[normalizedKind] || 0);
  if (!ttl || !normalizedChatId) return;
  var expiresAt = Date.now() + ttl;
  WBC_AGENT_CHAT_FLOW_STATE[normalizedChatId] = {
    kind: normalizedKind,
    expiresAt: expiresAt,
  };
  if (typeof window.CustomEvent !== "function") return;
  window.dispatchEvent(new window.CustomEvent(WBC_AGENT_CHAT_FLOW_EVENT, {
    detail: { kind: normalizedKind, chatId: normalizedChatId, expiresAt: expiresAt },
  }));
}

function wbcSetChatDrag(event, chat) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer || !chat || !chat.id) return;
  try {
    // Chat cards still move when reordered in the rail, while the topbar
    // consumes the same drag as a copy that creates a pinned conversation.
    transfer.effectAllowed = "copyMove";
    transfer.setData(WBC_CHAT_DRAG_MIME, JSON.stringify({
      kind: "chat",
      id: String(chat.id),
      projectId: String(chat.projectId || ""),
      title: String(chat.title || ""),
    }));
    transfer.setData("text/plain", String(chat.id));
  } catch (e) {}
}

function wbcHasChatDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return false;
  try {
    return Array.prototype.slice.call(transfer.types || []).indexOf(WBC_CHAT_DRAG_MIME) >= 0;
  } catch (e) {
    return false;
  }
}

function wbcSetChatGroupDrag(event, group, projectId) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer || !group || !group.id) return;
  try {
    transfer.effectAllowed = "move";
    transfer.setData(WBC_CHAT_GROUP_DRAG_MIME, JSON.stringify({
      kind: "chat-group",
      id: String(group.id),
      projectId: String(projectId || ""),
      title: String(group.title || ""),
      chatIds: (Array.isArray(group.chatIds) ? group.chatIds : []).map(String),
    }));
    transfer.setData("text/plain", String(group.title || group.id));
  } catch (e) {}
}

function wbcHasChatGroupDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return false;
  try {
    return Array.prototype.slice.call(transfer.types || []).indexOf(WBC_CHAT_GROUP_DRAG_MIME) >= 0;
  } catch (e) {
    return false;
  }
}

function wbcHasChatRailDrag(event) {
  return wbcHasChatDrag(event) || wbcHasChatGroupDrag(event);
}

function wbcReadChatDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return null;
  try {
    var payload = JSON.parse(transfer.getData(WBC_CHAT_DRAG_MIME) || "null");
    return payload && payload.kind === "chat" && payload.id ? payload : null;
  } catch (e) {
    return null;
  }
}

function wbcSetPluginViewDrag(event, payload) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  var pluginView = payload && typeof payload === "object" ? payload : null;
  var packId = String(pluginView && (pluginView.packId || pluginView.pack_id) || "");
  var viewId = String(pluginView && (pluginView.viewId || pluginView.view_id) || "");
  if (!transfer || !pluginView || !packId || !viewId) return;
  try {
    // Match conversation cards: the rail owns the source while the workspace
    // treats the same drag as a copy that creates a pane-card instance.
    transfer.effectAllowed = "copyMove";
    transfer.setData(WBC_PLUGIN_VIEW_DRAG_MIME, JSON.stringify({
      kind: "plugin-view",
      packId: packId,
      viewId: viewId,
      instanceId: String(pluginView.instanceId || pluginView.instance_id || "default"),
      projectId: String(pluginView.projectId || pluginView.project_id || ""),
      title: String(pluginView.title || viewId || packId),
      subtitle: String(pluginView.subtitle || packId),
      state: pluginView.state == null ? null : pluginView.state,
    }));
    transfer.setData("text/plain", String(pluginView.title || viewId || packId));
  } catch (e) {}
}

function wbcHasPluginViewDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return false;
  try {
    return Array.prototype.slice.call(transfer.types || []).indexOf(WBC_PLUGIN_VIEW_DRAG_MIME) >= 0;
  } catch (e) {
    return false;
  }
}

function wbcReadPluginViewDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return null;
  try {
    var payload = JSON.parse(transfer.getData(WBC_PLUGIN_VIEW_DRAG_MIME) || "null");
    return payload && payload.kind === "plugin-view" && payload.packId && payload.viewId
      ? payload : null;
  } catch (e) {
    return null;
  }
}

// The right drop zone for rail chats: the side panel track on wide windows,
// or a reserved band at the page's right edge when the panel is hidden
// (display:none below 980px). The conversation column's own drag handling is
// untouched — a dedicated drop layer sits above this zone only while a chat
// drag is in progress, so the rest of the main area keeps its original
// "drop to open" behaviour.
function wbcChatSideZoneRect() {
  var page = document.querySelector(".wbc-page");
  if (!page) return null;
  var pr = page.getBoundingClientRect();
  if (!pr.width) return null;
  var side = page.querySelector(":scope > .wbc-side");
  // A collapsed side panel keeps a narrow off-canvas box during its close
  // transition. Its rect can therefore have a non-zero width even though it
  // is not a usable drop target. Only reuse the real side track while it is
  // visible and intersects the page; otherwise reserve an in-page edge band.
  var sideHidden = page.classList.contains("wbc-side-hidden");
  if (side && !sideHidden) {
    var sr = side.getBoundingClientRect();
    if (sr.width > 0 && sr.left < pr.right && sr.right > pr.left) {
      return { left: sr.left, top: pr.top, right: sr.right, bottom: pr.bottom };
    }
  }
  var previewWidth = Number.parseFloat(
    window.getComputedStyle(page).getPropertyValue("--wbc-chat-side-preview-width")
  );
  var zoneWidth = Number.isFinite(previewWidth) && previewWidth > 0
    ? Math.round(previewWidth)
    : Math.max(300, Math.min(340, Math.round(pr.width * 0.32)));
  zoneWidth = Math.max(1, Math.min(zoneWidth, Math.round(pr.width)));
  return {
    left: pr.right - zoneWidth,
    top: pr.top,
    right: pr.right,
    bottom: pr.bottom,
  };
}

// A newly mounted split host normally begins one track-width offscreen and
// glides into place. During a shared-element handoff that entrance must not
// become the View Transition's destination geometry: the conversation should
// morph to the settled split rectangle, not to the host's transient offscreen
// frame and then continue sliding after the handoff. Keep the final geometry
// pinned until the View Transition finishes; React adopts the same `open`
// class on its next frame.
function wbcPinSplitMotionOpen(host) {
  if (!host) return;
  host.classList.add("open");
  host.style.transition = "none";
  host.style.transform = "translateX(0)";
  host.style.opacity = "1";
  // Force the settled rectangle into the new View Transition snapshot.
  host.getBoundingClientRect();
}

function wbcReleasePinnedSplitMotion(host) {
  if (!host) return;
  host.style.removeProperty("transition");
  host.style.removeProperty("transform");
  host.style.removeProperty("opacity");
}

// Width swapping is part of the shared-element handoff, not a second grid
// animation. Freeze the page grid while Chromium captures both endpoints so
// the destination rectangle is already settled and equal to the source.
function wbcPinPageSplitLayout(page) {
  if (!page) return;
  page.style.transition = "none";
  page.getBoundingClientRect();
}

function wbcReleasePinnedPageSplitLayout(page) {
  if (!page) return;
  page.style.removeProperty("transition");
}

// `cloneNode` copies markup but not the live viewport state of scrollable
// descendants. Preserve it explicitly so a lifted conversation shows the
// messages currently under the user's pointer instead of jumping to the first
// message in that session.
function wbcClonePaneWithLiveState(panel) {
  var clone = panel.cloneNode(true);
  var sourceNodes = [panel].concat(Array.prototype.slice.call(panel.querySelectorAll("*")));
  var cloneNodes = [clone].concat(Array.prototype.slice.call(clone.querySelectorAll("*")));
  var viewportState = [];
  for (var i = 0; i < sourceNodes.length && i < cloneNodes.length; i++) {
    var source = sourceNodes[i];
    var target = cloneNodes[i];
    var scrollTop = Number(source.scrollTop) || 0;
    var scrollLeft = Number(source.scrollLeft) || 0;
    if (scrollTop || scrollLeft) {
      viewportState.push({ target: target, scrollTop: scrollTop, scrollLeft: scrollLeft });
    }
    // Property state is not always reflected in cloned attributes either
    // (notably an unsent composer draft), so keep the visible control state.
    try {
      if ("value" in source && "value" in target) target.value = source.value;
      if ("checked" in source && "checked" in target) target.checked = source.checked;
    } catch (e) {}
  }
  return {
    clone: clone,
    restoreViewport: function () {
      for (var j = 0; j < viewportState.length; j++) {
        viewportState[j].target.scrollTop = viewportState[j].scrollTop;
        viewportState[j].target.scrollLeft = viewportState[j].scrollLeft;
      }
    },
  };
}

// Preserve the visible transcript position when the same conversation changes
// React owners (split pane <-> main pane). A raw scrollTop is insufficient
// because the two panes have different widths and therefore different text
// wrapping/heights. Anchor by direct thread-item index and its visual offset.
function wbcCaptureConversationViewport(pane) {
  var thread = pane && pane.querySelector ? pane.querySelector(".wbc-thread") : null;
  if (!thread) return null;
  var maxScroll = Math.max(0, thread.scrollHeight - thread.clientHeight);
  var items = Array.prototype.slice.call(
    thread.querySelectorAll(":scope > [data-wbc-thread-item]")
  );
  var threadRect = thread.getBoundingClientRect();
  var anchorIndex = -1;
  var anchorOffset = 0;
  for (var i = 0; i < items.length; i++) {
    var itemRect = items[i].getBoundingClientRect();
    if (itemRect.bottom > threadRect.top + 1) {
      anchorIndex = i;
      anchorOffset = itemRect.top - threadRect.top;
      break;
    }
  }
  return {
    atBottom: maxScroll - thread.scrollTop < 80,
    progress: maxScroll ? thread.scrollTop / maxScroll : 0,
    anchorIndex: anchorIndex,
    anchorOffset: anchorOffset,
    scrollLeft: Number(thread.scrollLeft) || 0,
  };
}

function wbcRestoreConversationViewport(pane, viewport) {
  if (!pane || !viewport) return;
  var thread = pane.querySelector(".wbc-thread");
  if (!thread) return;
  if (viewport.atBottom) {
    thread.scrollTop = thread.scrollHeight;
  } else {
    var items = thread.querySelectorAll(":scope > [data-wbc-thread-item]");
    var anchor = viewport.anchorIndex >= 0 ? items[viewport.anchorIndex] : null;
    if (anchor) {
      var currentOffset = anchor.getBoundingClientRect().top - thread.getBoundingClientRect().top;
      thread.scrollTop += currentOffset - viewport.anchorOffset;
    } else {
      var maxScroll = Math.max(0, thread.scrollHeight - thread.clientHeight);
      thread.scrollTop = maxScroll * Math.max(0, Math.min(1, viewport.progress || 0));
    }
  }
  thread.scrollLeft = viewport.scrollLeft;
}

// `splitSide` describes where the secondary split is anchored, while the drop
// zones describe where the conversation being dragged should land. Those are
// identical for the split grip and opposite for the main-conversation grip.
function wbcSplitSideForDraggedConversation(conversationSide, fromMainGrip) {
  var side = conversationSide === "left" ? "left" : "right";
  if (!fromMainGrip) return side;
  return side === "left" ? "right" : "left";
}

function wbcChatSideDropZone(event) {
  if (event.clientX == null || event.clientY == null) return false;
  var zone = wbcChatSideZoneRect();
  if (!zone) return false;
  return event.clientX >= zone.left && event.clientX <= zone.right
    && event.clientY >= zone.top && event.clientY <= zone.bottom;
}

var WBC_SPLIT_DRAG_MIME = "application/x-cyrene-split+json";

function wbcStablePaneValue(value) {
  if (value == null) return "";
  if (typeof value !== "object") return String(value);
  if (Array.isArray(value)) return "[" + value.map(wbcStablePaneValue).join(",") + "]";
  return "{" + Object.keys(value).sort().map(function (key) {
    return JSON.stringify(key) + ":" + wbcStablePaneValue(value[key]);
  }).join(",") + "}";
}

function wbcPaneIdentity(kind, payload) {
  var normalizedKind = String(kind || "pane");
  var resourceIdentity = wbcStablePaneValue(payload);
  var hash = 2166136261;
  var source = normalizedKind + "\n" + resourceIdentity;
  for (var index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return normalizedKind + ":" + (hash >>> 0).toString(36);
}

function wbcPaneCard(kind, payload, options) {
  var normalizedKind = String(kind || "");
  var opts = options || {};
  var canonicalId = normalizedKind === "chat" && payload
    ? "chat:" + String(payload)
    : normalizedKind === "terminal" && payload
    ? "terminal:" + String(payload)
    : wbcPaneIdentity(normalizedKind, payload);
  var stableId = opts.freshInstance
    ? canonicalId + ":instance:" + crypto.randomUUID()
    : canonicalId;
  var card = {
    id: opts.id || stableId,
    kind: normalizedKind,
    payload: payload,
    ownerChatId: String(opts.ownerChatId || ""),
  };
  if (opts.meta && typeof opts.meta === "object") card.meta = wbcNormalizePaneCardMeta(opts.meta);
  return card;
}

function wbcNormalizePaneCardMeta(value) {
  var raw = value && typeof value === "object" ? value : {};
  var autoClosePolicy = ["run-end", "idle", "never"].indexOf(String(raw.autoClosePolicy || "")) >= 0
    ? String(raw.autoClosePolicy) : "never";
  return {
    origin: raw.origin === "agent" ? "agent" : "user",
    claimedByUser: raw.claimedByUser === true,
    pinned: raw.pinned === true,
    autoClosePolicy: autoClosePolicy,
    createdAt: Math.max(0, Number(raw.createdAt) || 0),
    lastIntentAt: Math.max(0, Number(raw.lastIntentAt) || 0),
  };
}

function wbcNormalizePaneCard(card) {
  if (!card || typeof card !== "object" || card.kind !== "surface") return card;
  return Object.assign({}, card, { meta: wbcNormalizePaneCardMeta(card.meta) });
}

function wbcDefaultPaneLayout(chatId) {
  var id = String(chatId || "");
  var leftRatio = 0.5;
  var rightRatio = 0.5;
  try {
    leftRatio = Number(localStorage.getItem("wbc-pane-left-height")) || 0.5;
    rightRatio = Number(localStorage.getItem("wbc-pane-right-height")) || 0.5;
  } catch (e) {}
  return {
    left: id ? [wbcPaneCard("chat", id, { id: "chat:" + id, ownerChatId: id })] : [],
    right: [],
    leftRatio: Math.max(0.2, Math.min(0.8, leftRatio)),
    rightRatio: Math.max(0.2, Math.min(0.8, rightRatio)),
  };
}

function wbcNormalizePaneLayout(layout, chatId) {
  var base = layout && typeof layout === "object" ? layout : wbcDefaultPaneLayout(chatId);
  var left = Array.isArray(base.left) ? base.left.filter(Boolean).slice(0, 2).map(wbcNormalizePaneCard) : [];
  var right = Array.isArray(base.right) ? base.right.filter(Boolean).slice(0, 2).map(wbcNormalizePaneCard) : [];
  if (!left.length && !right.length && chatId) return wbcDefaultPaneLayout(chatId);
  return {
    left: left,
    right: right,
    leftRatio: Math.max(0.2, Math.min(0.8, Number(base.leftRatio) || 0.5)),
    rightRatio: Math.max(0.2, Math.min(0.8, Number(base.rightRatio) || 0.5)),
  };
}

function wbcPaneCardLocation(layout, cardId) {
  var sides = ["left", "right"];
  for (var s = 0; s < sides.length; s++) {
    var side = sides[s];
    var cards = layout[side] || [];
    for (var i = 0; i < cards.length; i++) {
      if (String(cards[i] && cards[i].id || "") === String(cardId || "")) {
        return { side: side, index: i, card: cards[i] };
      }
    }
  }
  return null;
}

function wbcPlacePaneCard(layout, card, side, edge, sourceCardId, targetCardId) {
  var next = wbcNormalizePaneLayout(layout, "");
  next = {
    left: next.left.slice(),
    right: next.right.slice(),
    leftRatio: next.leftRatio,
    rightRatio: next.rightRatio,
  };
  var targetSide = side === "left" ? "left" : "right";
  var source = sourceCardId ? wbcPaneCardLocation(next, sourceCardId) : null;
  // Pulling one card from a vertical pair to the outer left/right edge turns
  // that pair into two columns. The dragged card follows the pointer and its
  // former neighbour occupies the opposite column.
  if ((edge === "left" || edge === "right") && source) {
    var sourceStack = next[source.side] || [];
    var oppositeSide = source.side === "left" ? "right" : "left";
    if (sourceStack.length === 2 && !(next[oppositeSide] || []).length) {
      var companion = sourceStack[source.index === 0 ? 1 : 0];
      next.left = edge === "left" ? [card] : [companion];
      next.right = edge === "right" ? [card] : [companion];
      return next;
    }
  }
  if ((edge === "left" || edge === "right") && !source) {
    var axisTarget = wbcPaneCardLocation(next, targetCardId);
    var totalCards = next.left.length + next.right.length;
    if (axisTarget && totalCards === 1) {
      next.left = edge === "left" ? [card] : [axisTarget.card];
      next.right = edge === "right" ? [card] : [axisTarget.card];
      return next;
    }
  }
  if (edge === "replace" && source && String(sourceCardId) === String(targetCardId || "")) return next;
  if (edge === "replace" && source && source.side !== targetSide) {
    var crossTarget = wbcPaneCardLocation(next, targetCardId);
    if (crossTarget) {
      next[source.side][source.index] = crossTarget.card;
      next[crossTarget.side][crossTarget.index] = card;
      return next;
    }
  }
  if (edge !== "replace" && source && source.side === targetSide && next[targetSide].length === 2) {
    var desiredIndex = edge === "top" ? 0 : 1;
    if (source.index !== desiredIndex) next[targetSide].reverse();
    return next;
  }
  if (source) next[source.side].splice(source.index, 1);
  var target = next[targetSide];
  if (edge === "replace") {
    var targetIndex = target.findIndex(function (item) {
      return String(item && item.id || "") === String(targetCardId || "");
    });
    if (targetIndex < 0) targetIndex = 0;
    if (target.length) target[targetIndex] = card;
    else target.push(card);
  } else if (!target.length) {
    target.push(card);
  } else if (target.length === 1) {
    if (edge === "top") target.unshift(card);
    else target.push(card);
  } else {
    target[edge === "top" ? 0 : 1] = card;
  }
  return next;
}

function wbcChatDropReplacesActiveConversation(target, edge, draggedChatId, activeChatId) {
  var targetCard = target && target.card;
  var draggedId = String(draggedChatId || "");
  var activeId = String(activeChatId || "");
  return !!(
    draggedId
    && activeId
    && edge === "replace"
    && targetCard
    && targetCard.kind === "chat"
    && String(targetCard.payload || "") === activeId
    && String(targetCard.id || "") === "chat:" + activeId
  );
}

function wbcSetSplitDrag(event, payload) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return;
  try {
    transfer.effectAllowed = "move";
    transfer.setData(WBC_SPLIT_DRAG_MIME, JSON.stringify(payload || { kind: "legacy" }));
  } catch (e) {}
}

function wbcReadSplitDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return null;
  try {
    return JSON.parse(transfer.getData(WBC_SPLIT_DRAG_MIME) || "null");
  } catch (e) {
    return null;
  }
}

function wbcHasSplitDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return false;
  try {
    return Array.prototype.slice.call(transfer.types || []).indexOf(WBC_SPLIT_DRAG_MIME) >= 0;
  } catch (e) {
    return false;
  }
}

function wbcEscapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function wbcSetResourceDrag(event, payload) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer || !payload) return;
  // The macOS Electron titlebar is a native app-region. Even though the
  // resource shelf itself opts out with `no-drag`, Chromium can hand an
  // in-flight HTML drag back to the window drag region before the shelf sees
  // dragenter/dragover. DevTools happens to rebuild that native hit-test map,
  // which is why the same file drag works only while DevTools is docked.
  // Temporarily make the whole topbar a DOM interaction region for the life of
  // this resource drag, then restore normal window dragging on every terminal
  // drag event.
  var root = document.documentElement;
  root.classList.add("wbc-resource-drag-active");
  function clearResourceDragRegion() {
    root.classList.remove("wbc-resource-drag-active");
    document.removeEventListener("dragend", clearResourceDragRegion, true);
    document.removeEventListener("drop", clearResourceDragRegion, true);
  }
  document.addEventListener("dragend", clearResourceDragRegion, true);
  document.addEventListener("drop", clearResourceDragRegion, true);
  try {
    transfer.effectAllowed = "copy";
    transfer.setData(WBC_RESOURCE_DRAG_MIME, JSON.stringify(payload));
    transfer.setData("text/plain", payload.kind === "snippet"
      ? String(payload.text || "")
      : String(payload.title || payload.name || payload.url || ""));
  } catch (e) {}
}

function wbcReadResourceDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return null;
  try {
    var chatRaw = transfer.getData(WBC_CHAT_DRAG_MIME);
    if (chatRaw) {
      var chat = JSON.parse(chatRaw);
      if (chat && chat.kind === "chat" && chat.id) {
        return {
          kind: "conversation",
          ownerSessionId: String(chat.id),
          ownerProjectId: String(chat.projectId || ""),
          conversationId: String(chat.id),
          stableRef: String(chat.id),
          title: String(chat.title || "Conversation"),
        };
      }
    }
    var raw = transfer.getData(WBC_RESOURCE_DRAG_MIME);
    if (raw) return JSON.parse(raw);
    var types = Array.prototype.slice.call(transfer.types || []);
    if (types.indexOf("Files") >= 0 || types.indexOf("text/plain") < 0) return null;
    // macOS already gives selected text a native Chromium drag. Preserve that
    // interaction and turn its text/plain payload into the same snippet shape
    // used by the pinned-resource API; the server converts it to Markdown.
    var text = String(transfer.getData("text/plain") || "").trim();
    if (!text) return null;
    var page = document.querySelector(".wbc-page");
    var ownerSessionId = String(page && page.getAttribute("data-active-chat-id") || "");
    var ownerProjectId = String(page && page.getAttribute("data-project-id") || "");
    return {
      kind: "snippet",
      ownerSessionId: ownerSessionId,
      ownerProjectId: ownerProjectId,
      stableRef: "snippet:" + ownerSessionId + ":" + Date.now(),
      title: text.replace(/\s+/g, " ").slice(0, 48),
      text: text.slice(0, 12000),
    };
  } catch (e) {
    return null;
  }
}

function wbcHasResourceDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return false;
  try {
    return Array.prototype.slice.call(transfer.types || []).indexOf(WBC_RESOURCE_DRAG_MIME) >= 0;
  } catch (e) {
    return false;
  }
}

function wbcFileDragPayload(file, ownerSessionId, ownerProjectId) {
  var safeFile = {
    id: file && file.id,
    name: file && file.name,
    content_type: file && file.content_type,
    size: file && file.size,
    kind: file && file.kind,
    url: file && file.url,
    width: file && file.width,
    height: file && file.height,
    path: file && file.path,
    source: file && file.source,
    projectId: file && file.projectId,
  };
  return {
    kind: "file",
    ownerSessionId: String(ownerSessionId || ""),
    ownerProjectId: String(ownerProjectId || ""),
    stableRef: String(file && (file.url || file.id || file.name) || ""),
    title: String(file && (file.name || file.title) || "file"),
    name: String(file && file.name || "file"),
    url: String(file && file.url || ""),
    content_type: String(file && file.content_type || ""),
    size: Number(file && file.size || 0),
    file: safeFile,
  };
}

window.CyreneUI.resources = window.CyreneUI.register("resources", {
  mime: WBC_RESOURCE_DRAG_MIME,
  hasDrag: wbcHasResourceDrag,
  chatMime: WBC_CHAT_DRAG_MIME,
  readDrag: wbcReadResourceDrag,
  setDrag: wbcSetResourceDrag,
  filePayload: wbcFileDragPayload,
});

// ---------------------------------------------------------------------------
// Unified Agent event router (handoff §12)
// ---------------------------------------------------------------------------
// Phase-1 Agent events arrive in a versioned envelope { schemaVersion, eventId,
// type, payload, agentId, installationId, ... }. This router maps the new core
// event names onto the existing stream handlers so the runtime keeps a single
// rendering path; legacy snake_case stream events remain first-class. Unknown
// core events are ignored safely (diagnostics only), and the same eventId is
// processed at most once per stream so a reconnect cannot duplicate cards.

export { wbcWorkspaceDisplayName, WBC_RESOURCE_DRAG_MIME, WBC_CHAT_DRAG_MIME, WBC_PLUGIN_VIEW_DRAG_MIME, WBC_CHAT_GROUP_DRAG_MIME, WBC_AGENT_CHAT_FLOW_EVENT, WBC_AGENT_CHAT_FLOW_TTLS, WBC_AGENT_CHAT_FLOW_STATE, WBC_EMPTY_DRAG_IMAGE, wbcHideNativeDragImage, wbcBuildRailCardDragPreview, wbcAgentChatFlowSnapshot, wbcNotifyAgentChatFlow, wbcSetChatDrag, wbcHasChatDrag, wbcSetChatGroupDrag, wbcHasChatGroupDrag, wbcHasChatRailDrag, wbcReadChatDrag, wbcSetPluginViewDrag, wbcHasPluginViewDrag, wbcReadPluginViewDrag, wbcChatSideZoneRect, wbcPinSplitMotionOpen, wbcReleasePinnedSplitMotion, wbcPinPageSplitLayout, wbcReleasePinnedPageSplitLayout, wbcClonePaneWithLiveState, wbcCaptureConversationViewport, wbcRestoreConversationViewport, wbcSplitSideForDraggedConversation, wbcChatSideDropZone, WBC_SPLIT_DRAG_MIME, wbcPaneIdentity, wbcPaneCard, wbcDefaultPaneLayout, wbcNormalizePaneLayout, wbcPaneCardLocation, wbcPlacePaneCard, wbcChatDropReplacesActiveConversation, wbcSetSplitDrag, wbcReadSplitDrag, wbcHasSplitDrag, wbcEscapeHtml, wbcSetResourceDrag, wbcReadResourceDrag, wbcHasResourceDrag, wbcFileDragPayload }
