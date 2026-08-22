import {
  normalizePermissionMode as normalizePermissionModeBehavior,
  toolPresentationKind as toolPresentationKindBehavior,
} from "./features/chat/behavior.mjs"

// Workbench 对话页面 — workspace-bound conversations (kind: "chat").
// Independent from the legacy chat UI (chat.jsx / chat-surface.jsx): only the
// backend endpoints (/api/workbench/chats*, /api/chat/upload, /api/events SSE)
// are shared. Layout: chat rail | conversation | right context panel.

var {
  useState: useWbcState,
  useEffect: useWbcEffect,
  useLayoutEffect: useWbcLayoutEffect,
  useMemo: useWbcMemo,
  useRef: useWbcRef,
  useCallback: useWbcCallback,
} = React;

function wbcWorkspaceDisplayName(path) {
  var normalized = String(path || "").replace(/[\\/]+$/, "");
  return normalized.split(/[\\/]/).filter(Boolean).pop() || normalized || "…";
}

var WBC_RESOURCE_DRAG_MIME = "application/x-cyrene-work-resource+json";
var WBC_CHAT_DRAG_MIME = "application/x-cyrene-chat+json";
var WBC_TASK_DRAG_MIME = "application/x-cyrene-task+json";
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

function wbcSetTaskDrag(event, task, projectId) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer || !task || !task.id) return;
  try {
    transfer.effectAllowed = "copyMove";
    transfer.setData(WBC_TASK_DRAG_MIME, JSON.stringify({
      kind: "task",
      id: String(task.id),
      projectId: String(projectId || task.projectId || ""),
      title: String(task.title || ""),
    }));
    transfer.setData("text/plain", String(task.id));
  } catch (e) {}
}

function wbcHasTaskDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return false;
  try {
    return Array.prototype.slice.call(transfer.types || []).indexOf(WBC_TASK_DRAG_MIME) >= 0;
  } catch (e) {
    return false;
  }
}

function wbcReadTaskDrag(event) {
  var transfer = event && (event.dataTransfer || (event.nativeEvent && event.nativeEvent.dataTransfer));
  if (!transfer) return null;
  try {
    var payload = JSON.parse(transfer.getData(WBC_TASK_DRAG_MIME) || "null");
    return payload && payload.kind === "task" && payload.id ? payload : null;
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
  // Task detail and conversation detail are equivalent right-side surfaces.
  // Prefer the task panel when present so task drags use its real (usually
  // narrower) track instead of the generic preview-width fallback.
  var side = page.querySelector(":scope > .wbc-task-context-panel")
    || page.querySelector(":scope > .wbc-side");
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
var WBC_PANE_CARD_SEQUENCE = 0;

function wbcPaneCard(kind, payload, options) {
  var normalizedKind = String(kind || "");
  var opts = options || {};
  var stableId = opts.freshInstance
    ? "pane:" + (++WBC_PANE_CARD_SEQUENCE)
    : normalizedKind === "chat" && payload
    ? "chat:" + String(payload)
    : normalizedKind === "terminal" && payload
    ? "terminal:" + String(payload)
    : normalizedKind === "task" && payload
    ? "task:" + String(payload)
    : "pane:" + (++WBC_PANE_CARD_SEQUENCE);
  return {
    id: opts.id || stableId,
    kind: normalizedKind,
    payload: payload,
    ownerChatId: String(opts.ownerChatId || ""),
  };
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
  var left = Array.isArray(base.left) ? base.left.filter(Boolean).slice(0, 2) : [];
  var right = Array.isArray(base.right) ? base.right.filter(Boolean).slice(0, 2) : [];
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
function wbcAgentEventPayload(event) {
  return (
    event
    && event.payload
    && typeof event.payload === "object"
    && !Array.isArray(event.payload)
  ) ? event.payload : event;
}

function wbcAgentDeltaPayload(event) {
  var payload = wbcAgentEventPayload(event);
  return String(
    payload.delta != null ? payload.delta : (payload.text != null ? payload.text : (payload.content || ""))
  );
}

function wbcAgentDonePayload(event) {
  var payload = wbcAgentEventPayload(event);
  return String(
    payload.response != null ? payload.response : (payload.text != null ? payload.text : (payload.content || ""))
  );
}

function wbcAgentReasoningDelta(event) {
  var payload = wbcAgentEventPayload(event);
  return String(payload.delta != null ? payload.delta : (payload.text || ""));
}

function wbcAgentReasoningDone(event) {
  var payload = wbcAgentEventPayload(event);
  return String(
    payload.response != null ? payload.response : (payload.text != null ? payload.text : (payload.content || ""))
  );
}

function wbcAgentPhasePayload(event) {
  var payload = wbcAgentEventPayload(event);
  return {
    phase: String(payload.phase || payload.phaseKey || payload.phase_key || ""),
    provider: String(payload.provider || ""),
  };
}

function wbcAgentToolPayload(event) {
  var payload = wbcAgentEventPayload(event);
  var timestamp = String(event && event.timestamp || payload.timestamp || payload.createdAt || payload.created_at || "");
  var parsedAt = timestamp ? Date.parse(timestamp) : NaN;
  var progress = payload.progress && typeof payload.progress === "object"
    ? {
        current: Number(payload.progress.current) || 0,
        total: Number(payload.progress.total) || 0,
        label: String(payload.progress.label || ""),
      }
    : null;
  return {
    toolCallId: String(payload.toolCallId || payload.tool_call_id || ""),
    name: String(payload.name || payload.tool || payload.title || ""),
    title: String(payload.title || payload.name || ""),
    status: String(payload.status || "running"),
    failed: !!payload.failed,
    createdAt: Number.isFinite(parsedAt) ? parsedAt : Date.now(),
    inputSummary: wbcStructuredEventSummary(payload.inputSummary != null
      ? payload.inputSummary
      : (payload.input_summary != null ? payload.input_summary : payload.args)),
    outputSummary: wbcStructuredEventSummary(payload.outputSummary != null ? payload.outputSummary : payload.output_summary),
    input: payload.inputSummary != null
      ? payload.inputSummary
      : (payload.input_summary != null ? payload.input_summary : payload.args),
    output: payload.outputSummary != null ? payload.outputSummary : payload.output_summary,
    progress: progress,
    presentation: payload.presentation && typeof payload.presentation === "object" ? payload.presentation : {},
  };
}

function wbcAgentPermissionPayload(event) {
  var payload = wbcAgentEventPayload(event);
  var options = Array.isArray(payload.options) ? payload.options.map(function (opt) {
    if (typeof opt === "string") return { id: "", optionId: String(opt), label: String(opt), kind: "" };
    var id = String(opt.id != null ? opt.id : (opt.optionId != null ? opt.optionId : ""));
    return {
      id: id,
      optionId: id,
      label: String(opt.label || opt.title || ""),
      description: String(opt.description || ""),
      kind: String(opt.kind || ""),
    };
  }) : [];
  return {
    id: String(payload.requestId || payload.request_id || payload.id || ""),
    kind: String(payload.type || "permission.requested"),
    text: String(payload.title || payload.description || payload.message || ""),
    description: String(payload.description || ""),
    toolCallId: String(payload.toolCallId || payload.tool_call_id || ""),
    options: options,
    allowCustom: false,
    permission: true,
    meta: payload.meta && typeof payload.meta === "object" ? payload.meta : {},
  };
}

function wbcAgentElicitationPayload(event) {
  var payload = wbcAgentEventPayload(event);
  return {
    id: String(payload.requestId || payload.request_id || payload.id || ""),
    kind: String(payload.type || "elicitation.requested"),
    text: String(payload.text || payload.message || payload.title || ""),
    options: Array.isArray(payload.options) ? payload.options : [],
    allowCustom: payload.allowCustom !== false,
    schema: payload.schema && typeof payload.schema === "object" ? payload.schema : null,
    fields: Array.isArray(payload.fields) ? payload.fields : [],
    meta: payload.meta && typeof payload.meta === "object" ? payload.meta : {},
  };
}

function wbcStructuredEventSummary(value) {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    var parts = value.map(function (item) {
      if (item && typeof item === "object") {
        var nested = item.content && typeof item.content === "object" ? item.content : item;
        return nested.text || nested.message || nested.title || nested.name || nested.path || nested.uri || nested.url || "";
      }
      return item == null ? "" : String(item);
    }).filter(Boolean);
    if (parts.length) return parts.join(" · ");
  }
  try {
    var serialized = JSON.stringify(value);
    return serialized === "{}" || serialized === "[]" ? "" : serialized;
  } catch (e) {
    return "";
  }
}

function wbcAgentSessionPayload(event) {
  var payload = wbcAgentEventPayload(event);
  return {
    sessionId: String(payload.sessionId || payload.session_id || event.sessionId || event.session_id || ""),
    updateKind: String(payload.updateKind || payload.update_kind || ""),
    commands: Array.isArray(payload.commands) ? payload.commands : [],
    mode: payload.mode,
    configOption: payload.configOption && typeof payload.configOption === "object" ? payload.configOption : null,
    configOptions: Array.isArray(payload.configOptions) ? payload.configOptions : [],
    plan: payload.plan && typeof payload.plan === "object" ? payload.plan : null,
    sessionInfo: payload.sessionInfo && typeof payload.sessionInfo === "object" ? payload.sessionInfo : null,
    update: payload.update && typeof payload.update === "object" ? payload.update : null,
  };
}

function wbcAgentAwaitingPayload(event) {
  var payload = wbcAgentEventPayload(event);
  if (payload.pending_question || payload.pendingQuestion) {
    return payload.pending_question || payload.pendingQuestion;
  }
  return {
    id: String(payload.requestId || payload.request_id || payload.id || ""),
    kind: String(payload.kind || "ask_user"),
    text: String(payload.text || payload.message || payload.title || ""),
    options: Array.isArray(payload.options) ? payload.options : [],
    allowCustom: !!payload.allowCustom,
    meta: payload.meta && typeof payload.meta === "object" ? payload.meta : {},
  };
}

function wbcAgentRunFailedError(event) {
  var payload = wbcAgentEventPayload(event);
  var failureKind = String(payload.failureKind || payload.failure_kind || payload.code || "").trim();
  var message = String(payload.message || payload.detail || payload.error || "").trim();
  var err = new Error(message || wbcT("workbenchChat.agentError.failed", "Agent run failed"));
  err.code = failureKind || "agent_run_failed";
  err.failureKind = failureKind || err.code;
  err.detailKey = String(payload.detail_key || payload.detailKey || "");
  err.detailParams = payload.detail_params || payload.detailParams || {};
  err.errorType = String(payload.error || "");
  err.agentId = String(event.agentId || payload.agentId || "");
  err.installationId = String(event.installationId || payload.installationId || "");
  return err;
}

function wbcAgentNotificationPayload(event) {
  var payload = wbcAgentEventPayload(event);
  var timestamp = String(event && event.timestamp || payload.createdAt || "");
  var parsedAt = timestamp ? Date.parse(timestamp) : NaN;
  return {
    id: String(event && (event.eventId || event.event_id) || payload.id || ""),
    createdAt: Number.isFinite(parsedAt) ? parsedAt : Date.now(),
    severity: String(payload.severity || "warning"),
    category: String(payload.category || "transport_warning"),
    message: String(payload.message || payload.detail || "").trim(),
    source: String(payload.source || "agent_runtime"),
    terminal: payload.terminal === true,
  };
}

var AGENT_EVENT_ROUTER = {
  "run.started": { handler: "onRunStarted" },
  "run.awaiting_input": { handler: "onAwaitingUser", normalize: wbcAgentAwaitingPayload },
  "run.completed": { handler: "onFinalizing" },
  "run.failed": { dispatch: function (handlers, event) { if (handlers.onError) handlers.onError(wbcAgentRunFailedError(event)); } },
  "run.cancelled": { handler: "onInterrupted" },
  "message.started": { handler: "onReplyStart" },
  "message.delta": { handler: "onReplyDelta", normalize: wbcAgentDeltaPayload },
  "message.completed": { handler: "onReplyDone", normalize: wbcAgentDonePayload },
  "notification.created": { handler: "onNotification", normalize: wbcAgentNotificationPayload },
  "reasoning.started": { handler: "onReasoningStart", normalize: wbcAgentPhasePayload },
  "reasoning.delta": { handler: "onReasoningDelta", normalize: wbcAgentReasoningDelta },
  "reasoning.completed": { handler: "onReasoningDone", normalize: wbcAgentReasoningDone },
  "tool.started": { handler: "onToolStarted", normalize: wbcAgentToolPayload },
  "tool.updated": { handler: "onToolUpdated", normalize: wbcAgentToolPayload },
  "tool.completed": { handler: "onToolCompleted", normalize: wbcAgentToolPayload },
  "permission.requested": { handler: "onAwaitingUser", normalize: wbcAgentPermissionPayload },
  "permission.resolved": { handler: "onPermissionResolved" },
  "elicitation.requested": { handler: "onAwaitingUser", normalize: wbcAgentElicitationPayload },
  "elicitation.resolved": { handler: "onElicitationResolved" },
  "artifact.created": { handler: "onArtifactEvent" },
  "artifact.updated": { handler: "onArtifactEvent" },
  "usage.updated": { handler: "onUsageUpdated" },
  "session.updated": { handler: "onSessionUpdated", normalize: wbcAgentSessionPayload },
};

function wbcRouteAgentEvent(type, event, handlers) {
  var entry = AGENT_EVENT_ROUTER[type];
  if (!entry) return false;
  if (typeof entry.dispatch === "function") {
    entry.dispatch(handlers, event);
    return true;
  }
  var handler = handlers[entry.handler];
  if (!handler) return true; // recognized but no consumer — safe ignore
  var value = entry.normalize ? entry.normalize(event) : event;
  try {
    if (entry.handler === "onReasoningDelta" || entry.handler === "onReasoningDone") {
      handler(value, event);
    } else {
      handler(value);
    }
  } catch (e) {
    try { console.warn("[agent-event] handler failed for " + type, e); } catch (_e) {}
  }
  return true;
}

// ---------------------------------------------------------------------------
// Data access
// ---------------------------------------------------------------------------

var WorkbenchChatModel = (function () {
  // Route ordinary JSON calls through the shared wrapper (workbench-api.jsx):
  // a 30s AbortController timeout so a stalled backend no longer spins forever,
  // plus normalized errors. toast:false keeps this conversation's own inline
  // error banner (setError → wbcErrorText) as the single feedback channel;
  // callers can pass a longer/disabled `timeout` per call.
  function apiJson(url, options) {
    return window.CyreneUI.require("api").json(url, { toast: false, ...(options || {}) });
  }

  function listChats(projectId) {
    return apiJson("/api/workbench/chats?project=" + encodeURIComponent(projectId || ""))
      .then(function (payload) { return Array.isArray(payload.chats) ? payload.chats : []; });
  }

  function createChat(projectId, title) {
    return createChatWithBinding(projectId, title, null);
  }

  // Create a chat with an optional draft Agent binding (handoff §8.4). Absent
  // binding keeps the legacy create path exactly as before — the backend
  // normalizes missing agent fields to the built-in Cyrene Agent.
  function createChatWithBinding(projectId, title, binding) {
    var body = { project: projectId, title: title || "" };
    if (binding && typeof binding === "object") {
      if (binding.agent && typeof binding.agent === "object" && binding.agent.installationId) {
        body.agent = binding.agent;
      }
      if (binding.modelAccess && typeof binding.modelAccess === "object" && binding.modelAccess.mode) {
        body.modelAccess = binding.modelAccess;
      }
    }
    return apiJson("/api/workbench/chats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (payload) { return payload.chat; });
  }

  // Installed Agent catalog for the Composer submenu. Phase 1 lists the
  // built-in Cyrene Agent plus every installed external Agent with its
  // availability state; the backend supplies the definitive cards.
  function listAgents() {
    return apiJson("/api/agents", { toast: false })
      .then(function (payload) { return Array.isArray(payload.agents) ? payload.agents : []; });
  }

  function getAgent(installationId) {
    return apiJson("/api/agents/" + encodeURIComponent(String(installationId || "")), { toast: false })
      .then(function (payload) { return payload.agent || null; });
  }

  function listSideAgents(chatId) {
    if (!chatId || String(chatId).indexOf("legacy:") === 0) {
      return Promise.resolve([]);
    }
    return apiJson(
      "/api/workbench/chats/" + encodeURIComponent(chatId) + "/side-agents"
    ).then(function (payload) {
      return Array.isArray(payload.agents) ? payload.agents : [];
    });
  }

  function createSideAgent(chatId, quote) {
    return apiJson(
      "/api/workbench/chats/" + encodeURIComponent(chatId) + "/side-agents",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quote: String(quote || "") }),
      }
    ).then(function (payload) { return payload.agent; });
  }

  function getChat(chatId, options) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), options)
      .then(function (payload) { return payload.chat; });
  }

  function getSubagents(chatId, roundId, options) {
    if (!chatId || String(chatId).indexOf("legacy:") === 0) {
      return Promise.resolve({ rounds: [], activeRoundId: "", agents: [], messages: [] });
    }
    var query = roundId ? ("?round_id=" + encodeURIComponent(roundId)) : "";
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/subagents" + query, options);
  }

  function getChanges(chatId, options) {
    if (!chatId || String(chatId).indexOf("legacy:") === 0) {
      return Promise.resolve({ changeSets: [], fileCount: 0, additions: 0, deletions: 0 });
    }
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/changes", options);
  }

  function getChangeDiff(chatId, changeSetId, path, options) {
    return apiJson(
      "/api/workbench/chats/" + encodeURIComponent(chatId)
        + "/changes/" + encodeURIComponent(changeSetId)
        + "/files/" + String(path || "").split("/").map(encodeURIComponent).join("/"),
      options
    ).then(function (payload) { return payload.change || {}; });
  }

  function getInbox(chatId, options) {
    if (!chatId || String(chatId).indexOf("legacy:") === 0) {
      return Promise.resolve({
        active: false,
        runStatus: "idle",
        counts: { queued: 0, claimed: 0, completed: 0, failed: 0, cancelled: 0, total: 0 },
        events: [],
        tools: [],
      });
    }
    return apiJson(
      "/api/workbench/chats/" + encodeURIComponent(chatId) + "/inbox",
      options
    );
  }

  function renameChat(chatId, title) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title }),
    }).then(function (payload) { return payload.chat; });
  }

  function updateChatAgent(chatId, binding) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(binding || {}),
    }).then(function (payload) { return payload.chat; });
  }

  function getAgentConfigOptions(chatId) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/agent-config-options", { toast: false });
  }

  function updateAgentConfigValues(chatId, values) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agentConfigValues: values || {} }),
    }).then(function (payload) { return payload.chat; });
  }

  function updateChatPreferences(chatId, values) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values || {}),
    }).then(function (payload) { return payload.chat; });
  }

  function generateChatGroupMetadata(input) {
    return apiJson("/api/workbench/chat-groups/metadata", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input || {}),
      timeout: 120000,
      toast: false,
    }).then(function (payload) {
      return {
        metadata: payload.metadata || {},
        group: payload.group || null,
      };
    });
  }

  function listChatGroups(projectId) {
    if (!String(projectId || "").trim()) {
      return Promise.resolve({ groups: [], migrationRequired: false });
    }
    return apiJson("/api/workbench/chat-groups?project=" + encodeURIComponent(projectId || ""), {
      toast: false,
    });
  }

  function replaceChatGroups(input) {
    return apiJson("/api/workbench/chat-groups", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input || {}),
      toast: false,
    });
  }

  function migrateChatGroups(input) {
    return apiJson("/api/workbench/chat-groups/migrate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input || {}),
      toast: false,
    });
  }

  function deleteChat(chatId) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId), { method: "DELETE" });
  }

  function toTask(chatId, input) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/to-task", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input || {}),
      timeout: 180000, // LLM reads & distills the whole conversation — long budget
    });
  }

  function compactChat(chatId) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/compact", {
      method: "POST",
    });
  }

  function generateMemory(chatId, lang) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/memory-learning", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lang: lang === "zh" ? "zh" : "en" }),
    });
  }

  function interrupt(chatId) {
    return fetch("/api/chat/interrupt?session_id=" + encodeURIComponent(chatId), { method: "POST" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response;
      });
  }

  function uploadFiles(files) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return Promise.resolve([]);
    var form = new FormData();
    list.forEach(function (f) { form.append("files", f); });
    // Uploads can be large — give a generous budget rather than the 30s default,
    // and let the caller surface failures (the composer toasts on upload error).
    return window.CyreneUI.require("api").fetch("/api/chat/upload", { method: "POST", body: form, timeout: 120000 }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (payload) {
        if (!r.ok) throw new Error(payload.error || ("HTTP " + r.status));
        return Array.isArray(payload.files) ? payload.files : [];
      });
    });
  }

  function consumeEventStream(response, handlers) {
    handlers = handlers || {};
    if (!response.ok) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        var err = new Error(payload.error || payload.detail || ("HTTP " + response.status));
        err.code = payload.code || "";
        err.detailKey = payload.detail_key || payload.detailKey || "";
        err.detailParams = payload.detail_params || payload.detailParams || {};
        err.status = response.status;
        if (String(err.code || "").startsWith("budget_")) {
          window.CyreneUI.require("feedback").showToast(wbcErrorText(err), "error");
        }
        throw err;
      });
    }
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    // Idempotency for the versioned Agent envelope: the same eventId is never
    // dispatched twice within one stream (a reconnect starts a fresh stream).
    // The dedupe window is bounded so a very long stream cannot grow the set
    // without limit; the oldest seen ids fall out of the window.
    var seenEventIds = new Set();
    var seenEventOrder = [];
    var WBC_EVENT_ID_DEDUPE_LIMIT = 4096;

    function rememberEventId(eventId) {
      if (seenEventIds.has(eventId)) return false;
      seenEventIds.add(eventId);
      seenEventOrder.push(eventId);
      if (seenEventOrder.length > WBC_EVENT_ID_DEDUPE_LIMIT) {
        var oldest = seenEventOrder.shift();
        seenEventIds.delete(oldest);
      }
      return true;
    }

    function handleLine(line) {
      if (!line.trim()) return;
      var event;
      try { event = JSON.parse(line); } catch (e) { return; }
      var type = String(event.type || "");
      var eventId = String(event.eventId || event.event_id || "");
      if (eventId) {
        if (!rememberEventId(eventId)) return;
      }
      // Versioned Agent core events first; legacy snake_case events below stay
      // untouched so the built-in runtime keeps its exact historical behavior.
      if (wbcRouteAgentEvent(type, event, handlers)) return;
      if (type === "ack" && handlers.onAck) handlers.onAck(event);
      else if (type === "intermediate_message" && handlers.onIntermediateMessage) handlers.onIntermediateMessage(event);
      else if (type === "reasoning_start" && handlers.onReasoningStart) handlers.onReasoningStart(event);
      else if (type === "reasoning_delta" && handlers.onReasoningDelta) handlers.onReasoningDelta(event.delta || "", event);
      else if (type === "reasoning_done" && handlers.onReasoningDone) handlers.onReasoningDone(event.response || "", event);
      else if (type === "reply_start" && handlers.onReplyStart) handlers.onReplyStart(event);
      else if (type === "reply_delta" && handlers.onReplyDelta) handlers.onReplyDelta(event.delta || "");
      else if (type === "reply_done" && handlers.onReplyDone) handlers.onReplyDone(event.response || "");
      else if (type === "tool_call_started" && handlers.onToolStarted) handlers.onToolStarted(wbcAgentToolPayload(event));
      else if (type === "tool_call_progress" && handlers.onToolUpdated) handlers.onToolUpdated(wbcAgentToolPayload(event));
      else if (type === "tool_call_finished" && handlers.onToolCompleted) handlers.onToolCompleted(wbcAgentToolPayload(event));
      else if (type === "run_finalizing" && handlers.onFinalizing) handlers.onFinalizing(event);
      else if (type === "saved" && handlers.onSaved) handlers.onSaved(event);
      else if (type === "awaiting_user" && handlers.onAwaitingUser) handlers.onAwaitingUser(event);
      else if (type === "guidance_received" && handlers.onGuidanceReceived) handlers.onGuidanceReceived(event);
      else if (type === "workspace_changes") {
        if (handlers.onWorkspaceChanges) handlers.onWorkspaceChanges(event);
        try { window.dispatchEvent(new CustomEvent("workbench:workspace-changes", { detail: event })); } catch (e) {}
      }
      else if (type === "interrupted" && handlers.onInterrupted) handlers.onInterrupted(event);
      else if (type === "error" && handlers.onError) {
        var streamError = new Error(event.message || wbcT("settings.failed", "Failed"));
        streamError.code = event.code || event.failure_kind || "";
        streamError.detailKey = event.detail_key || event.detailKey || "";
        streamError.detailParams = event.detail_params || event.detailParams || {};
        streamError.errorType = event.error || "";
        handlers.onError(streamError);
      }
      else if (
        type.indexOf(".") >= 0
        || event.schemaVersion != null
        || event.agentId != null
        || event.installationId != null
      ) {
        // Unknown namespaced/Agent event — keep a sanitized, expandable
        // diagnostic card instead of silently losing protocol information.
        if (handlers.onUnknownAgentEvent) handlers.onUnknownAgentEvent(event);
        try { console.debug("[agent-event] unhandled event " + type); } catch (_e) {}
      }
    }

    function pump() {
      return reader.read().then(function (step) {
        if (step.done) {
          if (buffer) handleLine(buffer);
          return null;
        }
        buffer += decoder.decode(step.value, { stream: true });
        var lines = buffer.split("\n");
        buffer = lines.pop();
        lines.forEach(handleLine);
        return pump();
      });
    }
    return pump();
  }

  // Streaming send. handlers: { onAck, onReplyStart, onReplyDelta, onReplyDone, onFinalizing, onSaved, onError }
  function sendMessage(chatId, input, handlers, signal) {
    var body = {
      message: input.message || "",
      clientRequestId: input.clientRequestId || "",
      attachments: input.attachments || [],
      mode: input.mode || "default",
      command: input.command || "",
      model: input.model || "",
      reasoningEffort: input.reasoningEffort || "",
      retry: !!input.retry,
      forkReplay: !!input.forkReplay,
      stream: true,
      lang: window.CyreneUI.require("i18n").getLang(),
      uiInstanceId: window.CyreneUI.has("uiSurface")
        ? window.CyreneUI.require("uiSurface").getInstanceId()
        : "",
    };
    if (Object.prototype.hasOwnProperty.call(input, "workspaceOverride")) {
      body.workspaceOverride = input.workspaceOverride || "";
    }
    if (Object.prototype.hasOwnProperty.call(input, "soulActive")) {
      body.soulActive = !!input.soulActive;
    }
    if (Object.prototype.hasOwnProperty.call(input, "workspaceActive")) {
      body.workspaceActive = !!input.workspaceActive;
    }
    return fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: signal,
    }).then(function (response) {
      return consumeEventStream(response, handlers);
    });
  }

  function reconnectRun(chatId, handlers, signal) {
    return fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/run-stream", {
      method: "GET",
      signal: signal,
    }).then(function (response) {
      return consumeEventStream(response, handlers);
    });
  }

  function sendGuidance(chatId, message, clientRequestId) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/guidance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: message || "",
        clientRequestId: clientRequestId || "",
        uiInstanceId: window.CyreneUI.has("uiSurface")
          ? window.CyreneUI.require("uiSurface").getInstanceId()
          : "",
      }),
      // Guidance is optimistically visible and idempotent. Do not turn a slow
      // durable acknowledgement into a false failure after the agent accepted it.
      timeout: 0,
    });
  }

  // Answer a paused chat run's permission / clarification question → resume.
  // Resolves to { awaitingUser, assistantMessage?, pendingQuestion? }.
  function answerChat(chatId, questionId, answerText, options) {
    options = options || {};
    // Resumes an agent round (open-ended LLM work) — no death timeout. toast:false
    // because handleAnswer restores the prompt and surfaces the error itself.
    return window.CyreneUI.require("api").json("/api/workbench/chats/" + encodeURIComponent(chatId) + "/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question_id: questionId || "",
        answer: answerText || "",
        mode: options.mode || undefined,
        uiInstanceId: window.CyreneUI.has("uiSurface")
          ? window.CyreneUI.require("uiSurface").getInstanceId()
          : "",
      }),
      timeout: 0,
      toast: false,
    });
  }

  function answerAgentRequest(chatId, requestId, response) {
    return window.CyreneUI.require("api").json(
      "/api/workbench/chats/" + encodeURIComponent(chatId) + "/agent-requests/"
        + encodeURIComponent(requestId) + "/respond",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response: response || {} }),
        timeout: 0,
        toast: false,
      }
    );
  }

  // Fork a conversation at an edited user message. Creates a new chat with the
  // prefix transcript + the edited user entry, and seeds the agent state. The
  // caller then replays the edit via sendMessage({ retry: true, forkReplay: true }).
  function forkChat(chatId, messageId, content) {
    return apiJson("/api/workbench/chats/" + encodeURIComponent(chatId) + "/fork", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messageId: messageId || "", content: content || "" }),
    }).then(function (payload) { return payload.chat; });
  }

  var service = {
    listChats: listChats,
    createChat: createChat,
    createChatWithBinding: createChatWithBinding,
    listAgents: listAgents,
    getAgent: getAgent,
    listSideAgents: listSideAgents,
    createSideAgent: createSideAgent,
    getChat: getChat,
    getSubagents: getSubagents,
    getChanges: getChanges,
    getChangeDiff: getChangeDiff,
    getInbox: getInbox,
    renameChat: renameChat,
    updateChatAgent: updateChatAgent,
    getAgentConfigOptions: getAgentConfigOptions,
    updateAgentConfigValues: updateAgentConfigValues,
    updateChatPreferences: updateChatPreferences,
    generateChatGroupMetadata: generateChatGroupMetadata,
    listChatGroups: listChatGroups,
    replaceChatGroups: replaceChatGroups,
    migrateChatGroups: migrateChatGroups,
    deleteChat: deleteChat,
    toTask: toTask,
    compactChat: compactChat,
    generateMemory: generateMemory,
    interrupt: interrupt,
    uploadFiles: uploadFiles,
    sendMessage: sendMessage,
    sendGuidance: sendGuidance,
    reconnectRun: reconnectRun,
    answerChat: answerChat,
    answerAgentRequest: answerAgentRequest,
    forkChat: forkChat,
  };
  return service;
})();

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

var wbcChatCacheState = { lists: {}, details: {}, subagents: {} };
var wbcLastChatByProject = {};
function wbcChatCache() { return wbcChatCacheState; }

function wbcRenderMarkdown(text, options) {
  return window.CyreneUI.require("markdown").renderRich(text, options);
}

function wbcRenderMapMarkdown(text) {
  var source = String(text == null ? "" : text).replace(/\\r\\n|\\n|\\r/g, "\n");
  var html = wbcRenderMarkdown(source);
  if (html && !(source.indexOf("**") >= 0 && html.indexOf("**") >= 0)) return html;
  // Leaflet can initialize before the full Markdown parser on a cold desktop
  // load. Keep map notes readable in that short fallback window as well.
  var markdown = window.CyreneUI.require("markdown");
  var safe = markdown.escapeHtml(source)
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\n/g, "<br>");
  return markdown.sanitizeHtml(safe);
}

function wbcClampSideSplitWidth(value, availableWidth, viewportWidth, railWidth) {
  var available = Math.max(0, Number(availableWidth) || Number(window.innerWidth) || 0);
  var viewport = Math.max(0, Number(viewportWidth) || Number(window.innerWidth) || available);
  // Both conversation panes use the same 380px floor. At exceptionally compact
  // widths, reduce that floor symmetrically so neither anchored side wins the
  // remaining space merely because it happens to be the split track.
  var rail = Math.max(0, Number(railWidth) || (viewport <= 980 ? 220 : 230));
  var paneMin = Math.min(380, Math.max(0, (available - rail) / 2));
  // A split on the left side hides the right panel track (grid column 4 is 0),
  // so both anchored sides reserve the same room for the conversation lane.
  var maxWidth = Math.min(900, Math.max(paneMin, available - rail - paneMin));
  return Math.round(Math.max(paneMin, Math.min(maxWidth, Number(value) || 520)));
}

function wbcClampSideSplitWidthForPage(value, page) {
  var available = 0;
  var rail = 0;
  if (page) {
    var rect = page.getBoundingClientRect ? page.getBoundingClientRect() : null;
    available = Math.round((rect && rect.width) || page.clientWidth || 0);
    try {
      rail = parseFloat(window.getComputedStyle(page).getPropertyValue("--wbc-rail-width")) || 0;
    } catch (e) {}
  }
  return wbcClampSideSplitWidth(value, available, window.innerWidth, rail);
}

function WbcSplitPickerMenu({ open, className, children, ...props }) {
  var [mounted, setMounted] = useWbcState(Boolean(open));
  useWbcEffect(function () {
    if (open) {
      setMounted(true);
      return undefined;
    }
    var timer = setTimeout(function () { setMounted(false); }, 260);
    return function () { clearTimeout(timer); };
  }, [open]);
  if (!mounted) return null;
  return <div {...props} className={(className || "wbc-side-agent-split-menu") + (open ? " open" : " closing")}>{children}</div>;
}

function wbcFormatTime(value) {
  if (!value) return "";
  try {
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    var now = new Date();
    var dayMs = 24 * 3600 * 1000;
    var startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    if (date >= startOfDay) {
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    if (date >= new Date(startOfDay.getTime() - dayMs)) return wbcT("workbenchChat.time.yesterday", "Yesterday");
    var days = Math.floor((startOfDay.getTime() - date.getTime()) / dayMs) + 1;
    if (days <= 7) return wbcT("workbenchChat.time.daysAgo", "{n}d ago", { n: days });
    return date.toLocaleDateString([], { month: "2-digit", day: "2-digit" });
  } catch (e) {
    return "";
  }
}

function wbcFormatProcessingDuration(value) {
  var milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "";
  if (milliseconds < 100) return "<0.1s";
  if (milliseconds < 1000) {
    return (Math.round(milliseconds / 100) / 10).toFixed(1) + "s";
  }
  var totalSeconds = Math.max(1, Math.round(milliseconds / 1000));
  var hours = Math.floor(totalSeconds / 3600);
  var minutes = Math.floor((totalSeconds % 3600) / 60);
  var seconds = totalSeconds % 60;
  if (hours > 0) return hours + "h " + minutes + "m";
  if (minutes > 0) return minutes + "m " + seconds + "s";
  return seconds + "s";
}

function wbcConfirmOptimisticMessage(previous, confirmed) {
  var prior = previous || {};
  var next = { ...prior, ...(confirmed || {}), optimistic: false };
  // The server timestamp is authoritative for persistence, but it is produced
  // after the request reaches Python. Replacing the optimistic timestamp with
  // it while the run is live can move the user's turn below the already-mounted
  // thinking placeholder. Keep the client timestamp as this render's stable
  // causal anchor; the next durable reload naturally uses the server timestamp.
  if (prior.optimistic && prior.createdAt) {
    next.serverCreatedAt = String((confirmed && (confirmed.createdAt || confirmed.created_at)) || "");
    next.createdAt = prior.createdAt;
  }
  return next;
}

function wbcReconcileLiveUserMessages(messages, liveUserMessages) {
  var merged = Array.isArray(messages) ? messages.slice() : [];
  (Array.isArray(liveUserMessages) ? liveUserMessages : []).forEach(function (liveMessage) {
    if (!liveMessage || liveMessage.role !== "user") return;
    var liveId = String(liveMessage.id || "");
    var liveRequestId = String(liveMessage.clientRequestId || "");
    var liveQuestionId = String(liveMessage.answerToQuestionId || "");
    var matchIndex = -1;
    for (var i = 0; i < merged.length; i++) {
      var current = merged[i];
      if (!current || current.role !== "user") continue;
      var sameRequest = liveRequestId
        && String(current.clientRequestId || "") === liveRequestId;
      var sameMessage = liveId && String(current.id || "") === liveId;
      // A pending-question answer is persisted before its resumed run finishes.
      // Its optimistic and durable entries have different message ids and no
      // clientRequestId, so a hydration while that run is live must correlate
      // them through the question they both answer.
      var sameQuestionAnswer = liveQuestionId
        && String(current.answerToQuestionId || "") === liveQuestionId;
      if (sameRequest || sameMessage || sameQuestionAnswer) {
        matchIndex = i;
        break;
      }
    }
    if (matchIndex < 0) {
      merged = wbcMergeChronologicalMessages(merged, [liveMessage]);
      return;
    }
    var matched = merged[matchIndex] || {};
    if (liveMessage.optimistic && !matched.optimistic) {
      merged[matchIndex] = wbcConfirmOptimisticMessage(liveMessage, matched);
    } else if (matched.optimistic && !liveMessage.optimistic) {
      merged[matchIndex] = wbcConfirmOptimisticMessage(matched, liveMessage);
    } else {
      merged[matchIndex] = {
        ...matched,
        ...liveMessage,
        createdAt: liveMessage.createdAt || matched.createdAt,
      };
    }
  });
  return merged;
}

function wbcRetryTurnSelection(chat, messageId) {
  var messages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  var targetId = String(messageId || "");
  var targetIndex = -1;
  if (targetId) {
    targetIndex = messages.findIndex(function (item) { return String(item && item.id || "") === targetId; });
  }
  if (targetIndex < 0) targetIndex = messages.length - 1;
  var userIndex = -1;
  for (var i = targetIndex; i >= 0; i--) {
    if (messages[i] && messages[i].role === "user") {
      userIndex = i;
      break;
    }
  }
  if (userIndex < 0) return { userIndex: -1, endIndex: -1, outputIds: [] };
  var endIndex = messages.length;
  for (var nextIndex = userIndex + 1; nextIndex < messages.length; nextIndex++) {
    if (messages[nextIndex] && messages[nextIndex].role === "user") {
      endIndex = nextIndex;
      break;
    }
  }
  return {
    userIndex: userIndex,
    endIndex: endIndex,
    outputIds: messages.slice(userIndex + 1, endIndex).map(function (item) {
      return String(item && item.id || "");
    }).filter(Boolean),
  };
}

function wbcClearModelOutputForRetry(chat, messageId) {
  if (!chat || !Array.isArray(chat.messages)) return chat;
  var selection = wbcRetryTurnSelection(chat, messageId);
  if (selection.userIndex < 0) return chat;
  return {
    ...chat,
    messages: chat.messages.slice(0, selection.userIndex + 1).concat(chat.messages.slice(selection.endIndex)),
    pendingQuestion: selection.endIndex === chat.messages.length ? null : chat.pendingQuestion,
  };
}

function wbcPreserveLiveTimelineAnchors(previousChat, hydratedChat, runtime) {
  if (!hydratedChat || !runtime) return hydratedChat;
  var liveUserMessages = Array.isArray(runtime.userMessages) ? runtime.userMessages : [];
  if (!liveUserMessages.length || !Array.isArray(hydratedChat.messages)) return hydratedChat;
  return {
    ...hydratedChat,
    messages: wbcReconcileLiveUserMessages(hydratedChat.messages, liveUserMessages),
  };
}

function wbcMergeChronologicalMessages(messages, additions) {
  // Runtime segments are discovered independently from persisted guidance.
  // Merge them by event time so steering stays where it happened instead of
  // forcing every user message above all assistant output.
  var merged = Array.isArray(messages) ? messages.slice() : [];
  var known = new Set();
  merged.forEach(function (item) {
    var id = String(item && item.id || "");
    if (id) known.add(id);
  });
  (additions || []).forEach(function (item) {
    if (!item) return;
    var id = String(item.id || "");
    if (id && known.has(id)) return;
    var clientRequestId = String(item.clientRequestId || "");
    if (clientRequestId) {
      for (var requestIndex = 0; requestIndex < merged.length; requestIndex++) {
        if (String(merged[requestIndex] && merged[requestIndex].clientRequestId || "") !== clientRequestId) continue;
        var previousId = String(merged[requestIndex] && merged[requestIndex].id || "");
        merged[requestIndex] = wbcConfirmOptimisticMessage(merged[requestIndex], item);
        if (previousId) known.delete(previousId);
        if (id) known.add(id);
        return;
      }
    }
    var at = String(item.createdAt || item.created_at || "");
    var atMs = at ? Date.parse(at) : NaN;
    var index = merged.length;
    if (Number.isFinite(atMs)) {
      for (var i = 0; i < merged.length; i++) {
        var currentAt = String(merged[i] && (merged[i].createdAt || merged[i].created_at) || "");
        var currentAtMs = currentAt ? Date.parse(currentAt) : NaN;
        if (Number.isFinite(currentAtMs) && currentAtMs > atMs) { index = i; break; }
      }
    }
    merged.splice(index, 0, item);
    if (id) known.add(id);
  });
  return merged;
}

function wbcMergeSavedAssistantMessages(chat, assistantMessages) {
  if (!chat) return chat;
  var current = Array.isArray(chat.messages) ? chat.messages : [];
  var knownIds = new Set(current.map(function (message) {
    return String(message && message.id || "");
  }));
  var additions = (Array.isArray(assistantMessages) ? assistantMessages : []).filter(function (message) {
    var id = String(message && message.id || "");
    if (!id || knownIds.has(id)) return false;
    knownIds.add(id);
    return true;
  });
  return {
    ...chat,
    status: "idle",
    liveAgentArtifacts: [],
    messages: wbcMergeChronologicalMessages(current, additions),
  };
}

function wbcRuntimeSegmentMessages(runtime) {
  var segments = runtime && Array.isArray(runtime.segments) ? runtime.segments : [];
  var hasLiveActivities = !!(runtime && Array.isArray(runtime.activities) && runtime.activities.length);
  return segments.map(function (segment) {
    var message = segment && segment.message ? segment.message : {};
    return {
      ...message,
      id: String(message.id || segment.id || ""),
      role: "assistant",
      // While the run is active, per-LLM activity cards own the live tool trace.
      // Hiding the segment copy prevents the same calls from appearing twice.
      trace: hasLiveActivities ? [] : (Array.isArray(segment.progress) ? segment.progress : (message.trace || [])),
      runtimeSegment: true,
    };
  });
}

function wbcRuntimeTimelineMessages(runtime, options) {
  if (!runtime) return [];
  var showReasoningPlaceholder = !options || options.showReasoningPlaceholder !== false;
  var startedAt = Number(runtime.startedAt || Date.now());
  var activities = Array.isArray(runtime.activities) && runtime.activities.length
    ? runtime.activities
    : (showReasoningPlaceholder ? [{ id: "activity_1", reasoning: "", progress: [] }] : []);
  var items = [{
    id: "runtime_heartbeat_" + String(runtime.chatId || "chat"),
    role: "assistant",
    createdAt: new Date(startedAt + 1).toISOString(),
    runtimeHeartbeat: true,
    runtimeFinalizing: !!runtime.finalizing,
  }];
  activities.forEach(function (activity, index) {
    items.push({
      id: "runtime_" + String(activity.id || index),
      role: "assistant",
      createdAt: new Date(Number(activity.createdAt || startedAt + index + 2)).toISOString(),
      runtimeActivity: activity,
      runtimeActivityActive: !runtime.finalizing && index === activities.length - 1,
      runtimeActivityHasReplyText: !!runtime.text,
    });
  });
  (Array.isArray(runtime.notifications) ? runtime.notifications : []).forEach(function (notice, index) {
    items.push({
      id: String(notice.id || ("runtime_notice_" + index)),
      role: "assistant",
      createdAt: new Date(Number(notice.createdAt || startedAt + index + 2)).toISOString(),
      runtimeNotification: true,
      notification: notice,
    });
  });
  return wbcMergeChronologicalMessages([], items);
}

function wbcFinalizeRuntime(runtime) {
  var current = runtime || {};
  function settle(items) {
    return (Array.isArray(items) ? items : []).map(function (entry) {
      if (!entry || entry.kind !== "tool" || entry.status !== "running") return entry;
      return { ...entry, status: "completed", inferredCompletion: true };
    });
  }
  return {
    ...current,
    finalizing: true,
    replying: false,
    progress: settle(current.progress),
    activities: (Array.isArray(current.activities) ? current.activities : []).map(function (activity) {
      return {
        ...activity,
        reasoningActive: false,
        timelineClosed: true,
        progress: settle(activity && activity.progress),
      };
    }),
  };
}

function wbcCreateDetachedRuntime(startedAt) {
  var now = Number(startedAt || Date.now());
  return {
    text: "",
    streamDone: false,
    activities: [],
    activitySeq: 0,
    notifications: [],
    artifacts: [],
    startedAt: now,
    lastEventAt: now,
    finalizing: false,
  };
}

function wbcReduceDetachedRuntime(runtime, action, value, sourceEvent) {
  var current = runtime || wbcCreateDetachedRuntime();
  var now = Date.now();
  function withActivity(updater) {
    var activities = Array.isArray(current.activities) ? current.activities.slice() : [];
    if (!activities.length || activities[activities.length - 1].timelineClosed) {
      var seq = Number(current.activitySeq || 0) + 1;
      activities.push({ id: "activity_" + seq, reasoning: "", reasoningActive: false, progress: [], createdAt: now });
      current = { ...current, activitySeq: seq };
    }
    var index = activities.length - 1;
    activities[index] = updater(activities[index] || {});
    return { ...current, activities: activities, lastEventAt: now };
  }
  if (action === "reply_start") return { ...current, text: "", streamDone: false, lastEventAt: now };
  if (action === "reply_delta") return { ...current, text: String(current.text || "") + String(value || ""), streamDone: false, lastEventAt: now };
  if (action === "reply_done") return { ...current, text: String(value || current.text || ""), streamDone: true, lastEventAt: now };
  if (action === "finalizing") return { ...wbcFinalizeRuntime(current), lastEventAt: now };
  if (action === "reasoning_start") return withActivity(function (activity) {
    return { ...activity, reasoningActive: true };
  });
  if (action === "reasoning_delta") return withActivity(function (activity) {
    return { ...activity, reasoning: String(activity.reasoning || "") + String(value || ""), reasoningActive: true };
  });
  if (action === "reasoning_done") return withActivity(function (activity) {
    return { ...activity, reasoning: String(value || activity.reasoning || ""), reasoningActive: false };
  });
  if (action === "tool") {
    var tool = value && typeof value === "object" ? value : {};
    var toolCallId = String(tool.toolCallId || tool.tool_call_id || "");
    var status = String(tool.status || "running").toLowerCase();
    var terminal = status === "completed" || status === "failed" || tool.terminal === true;
    var entry = {
      kind: "tool",
      toolCallId: toolCallId,
      text: String(tool.name || tool.tool || tool.title || "tool"),
      preview: String(tool.outputSummary || tool.inputSummary || ""),
      status: terminal ? "completed" : "running",
      failed: !!tool.failed || status === "failed",
      input: tool.input,
      output: tool.output,
      presentation: tool.presentation && typeof tool.presentation === "object" ? tool.presentation : {},
    };
    return withActivity(function (activity) {
      var progress = Array.isArray(activity.progress) ? activity.progress.slice() : [];
      var merged = wbcMergeToolOccurrence(progress, entry, terminal);
      progress = merged.items;
      if (!merged.matched) progress.push({
        ...entry,
        reasoningOffset: String(activity.reasoning || "").length,
        startedAt: now,
      });
      return { ...activity, progress: progress.slice(-40) };
    });
  }
  if (action === "notification") {
    var notice = value && typeof value === "object" ? value : {};
    if (!notice.message) return current;
    var notifications = Array.isArray(current.notifications) ? current.notifications.slice() : [];
    var noticeKey = String(notice.id || (notice.category + "\n" + notice.message));
    if (!notifications.some(function (item) { return String(item.id || (item.category + "\n" + item.message)) === noticeKey; })) notifications.push(notice);
    return { ...current, notifications: notifications, lastEventAt: now };
  }
  if (action === "artifact") {
    var payload = wbcAgentEventPayload(sourceEvent || value || {});
    var attachment = payload.attachment && typeof payload.attachment === "object" ? payload.attachment : null;
    if (!attachment && (payload.uri || payload.url)) {
      attachment = {
        id: String(payload.artifactId || payload.id || payload.uri || payload.url || ""),
        name: String(payload.title || payload.name || "artifact"),
        content_type: String(payload.mimeType || payload.content_type || "application/octet-stream"),
        kind: String(payload.kind || "file"),
        url: String(payload.uri || payload.url || ""),
        size: Number(payload.size || 0),
      };
    }
    if (!attachment) return current;
    var artifacts = Array.isArray(current.artifacts) ? current.artifacts.slice() : [];
    var artifactId = String(payload.artifactId || attachment.id || attachment.url || "");
    var artifactIndex = artifacts.findIndex(function (item) { return String(item && (item.artifactId || item.id || item.url) || "") === artifactId; });
    var artifact = { ...attachment, artifactId: artifactId };
    if (artifactIndex >= 0) artifacts[artifactIndex] = { ...artifacts[artifactIndex], ...artifact };
    else artifacts.push(artifact);
    return { ...current, artifacts: artifacts, lastEventAt: now };
  }
  return current;
}

function wbcMergeToolLifecycleEntry(current, incoming, terminalOnly) {
  if (!terminalOnly) return {
    ...current,
    ...incoming,
    text: incoming.text || current.text,
  };
  // The concrete executor may already have replaced a progressive package name
  // with the resolved capability. A gateway terminal event owns lifecycle only;
  // it must not regress that richer identity back to the package name.
  return {
    ...current,
    status: incoming.status,
    failed: incoming.failed,
    preview: current.preview || incoming.preview,
    input: incoming.input != null ? incoming.input : current.input,
    output: incoming.output != null ? incoming.output : current.output,
    presentation: incoming.presentation && Object.keys(incoming.presentation).length ? incoming.presentation : current.presentation,
  };
}

function wbcToolEntryIsTerminal(entry) {
  var status = String(entry && entry.status || "").trim().toLowerCase();
  return ["completed", "failed", "error", "failure", "expired", "cancelled"].indexOf(status) >= 0;
}

function wbcToolOccurrenceIndex(items, toolCallId, incomingTerminal) {
  var list = Array.isArray(items) ? items : [];
  var latestMatching = -1;
  for (var index = list.length - 1; index >= 0; index--) {
    var item = list[index];
    if (String(item && item.toolCallId || "") !== String(toolCallId || "")) continue;
    if (latestMatching < 0) latestMatching = index;
    if (!wbcToolEntryIsTerminal(item)) return index;
  }
  // A new running event after a completed occurrence is a new invocation even
  // when an Agent incorrectly reuses the same toolCallId. A duplicate terminal
  // event may still update the latest completed occurrence in place.
  return incomingTerminal ? latestMatching : -1;
}

function wbcMergeToolOccurrence(items, incoming, incomingTerminal) {
  var list = Array.isArray(items) ? items.slice() : [];
  var index = incoming && incoming.toolCallId
    ? wbcToolOccurrenceIndex(list, incoming.toolCallId, incomingTerminal)
    : -1;
  if (index < 0) return { items: list, matched: false };
  list[index] = wbcMergeToolLifecycleEntry(list[index], incoming, incomingTerminal);
  return { items: list, matched: true };
}

// The client's live tool trace (assembled from SSE tool events) is the
// authoritative execution history. On save we upload it so the completed
// conversation matches what ran live — the backend's transcript extraction can
// drop mid-run tool calls (compaction / retry) and drops runtime status fields.
var WBC_DURABLE_TRACE_FIELDS = [
  "kind", "toolCallId", "text", "tool", "preview", "status", "failed",
  "progress", "progressCurrent", "progressTotal", "startedAt", "reasoningOffset",
  "detailKey", "detailParams", "presentation",
];

function wbcCleanDurableTraceEntry(entry) {
  if (!entry || typeof entry !== "object") return null;
  var out = {};
  WBC_DURABLE_TRACE_FIELDS.forEach(function (key) {
    var value = entry[key];
    if (value === undefined || value === null) return;
    if (typeof value === "string") value = String(value).slice(0, 400);
    else if (typeof value === "number" && !Number.isFinite(value)) return;
    else if (typeof value === "boolean") { /* keep */ }
    else if (typeof value === "object") {
      try {
        value = JSON.stringify(value).slice(0, 2000);
      } catch (e) { return; }
    } else return;
    out[key] = value;
  });
  return Object.keys(out).length ? out : null;
}

// Zip the live activities (in execution order) onto the just-saved activity
// cards (also in execution order). Both sides have exactly one entry per
// LLM turn that actually called tools; anything else means the boundaries
// diverged, so skip the upload and keep the backend-extracted trace.
function wbcDurableTracePayload(chatId, runtime, assistantMessages) {
  if (!chatId || !runtime) return null;
  var withTools = (Array.isArray(runtime.activities) ? runtime.activities : []).filter(function (activity) {
    return Array.isArray(activity && activity.progress) && activity.progress.length;
  });
  if (!withTools.length) return null;
  var savedCards = (Array.isArray(assistantMessages) ? assistantMessages : []).filter(function (message) {
    return message && message.activityCard && Array.isArray(message.trace) && message.trace.length;
  });
  if (!savedCards.length || savedCards.length !== withTools.length) return null;
  var messageIds = [];
  var traces = [];
  for (var index = 0; index < withTools.length; index += 1) {
    var progress = withTools[index].progress.map(function (entry) {
      var status = String(entry && entry.status || "").toLowerCase();
      if (status === "running" || status === "resumed") {
        entry = { ...entry, status: "completed", inferredCompletion: true };
      }
      return wbcCleanDurableTraceEntry(entry);
    }).filter(Boolean);
    if (!progress.length) return null;
    messageIds.push(String(savedCards[index].id || ""));
    traces.push(progress);
  }
  if (!messageIds.some(Boolean)) return null;
  return { messageIds: messageIds, traces: traces };
}

function wbcPersistDurableTrace(chatId, payload) {
  if (!chatId || !payload) return;
  try {
    fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/trace", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(function () {});
  } catch (e) {}
}

function wbcToolPresentationKind(entry) {
  return toolPresentationKindBehavior(entry);
}

function wbcToolPresentationText(entry, kind) {
  if (["terminal", "diff", "error"].indexOf(kind) < 0) return "";
  var value = entry && entry.output != null ? entry.output : entry && entry.input;
  if (typeof value === "string") return value.slice(0, 12000);
  return wbcStructuredEventSummary(value).slice(0, 12000);
}

function wbcTraceDedupeKey(trace) {
  if (!Array.isArray(trace) || !trace.length) return "";
  return JSON.stringify(trace.map(function (entry) {
    var item = entry || {};
    return [
      String(item.tool || item.text || ""),
      String(item.preview || ""),
      String(item.kind || ""),
    ];
  }));
}

function wbcCurrentModel(chat, project, runtime, liveData) {
  // During a run, activeModel is the authoritative transport identity and can
  // change on the exact SSE tick that fallback occurs. The polled context
  // payload remains the durable source once the live runtime is gone.
  var activeModel = String(runtime && runtime.activeModel || "").trim();
  if (activeModel) return activeModel;
  var liveModel = String(liveData && liveData.model || "").trim();
  if (liveModel) return liveModel;
  var messages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  for (var i = messages.length - 1; i >= 0; i--) {
    var messageModel = String(messages[i] && messages[i].model || "").trim();
    if (messageModel) return messageModel;
  }
  return String(
    (chat && (chat.lastModel || chat.model))
    || (project && project.model)
    || ""
  ).trim();
}

var WBC_CHAT_MODEL_CHANGED_EVENT = "cyrene:wbc-chat-model-changed";

function wbcModelContextLimit(model) {
  var source = model && typeof model === "object" ? model : {};
  var raw = source.ctxLimit;
  if (raw == null) raw = source.ctx_limit;
  if (raw == null) raw = source.contextLimit;
  if (raw == null) raw = source.context_limit;
  if (raw == null) raw = source.contextWindow;
  if (raw == null) raw = source.context_window;
  if (raw == null) raw = source.ctx;
  if (typeof raw === "number") return Number.isFinite(raw) ? Math.max(0, Math.round(raw)) : 0;
  var text = String(raw == null ? "" : raw).trim().toLowerCase().replace(/[, _]/g, "");
  if (!text) return 0;
  var match = text.match(/^([0-9]+(?:\.[0-9]+)?)([kmg])?(?:tokens?)?$/);
  if (!match) return 0;
  var multiplier = match[2] === "g" ? 1000000000 : (match[2] === "m" ? 1000000 : (match[2] === "k" ? 1000 : 1));
  return Math.max(0, Math.round(Number(match[1]) * multiplier));
}

function wbcPublishChatModelChanged(chatId, selected, options) {
  if (!chatId || typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  var model = selected && typeof selected === "object" ? selected : {};
  var detail = {
    chatId: String(chatId),
    profileId: String(model.profileId || model.profile_id || model.id || model.value || ""),
    model: String(model.model || model.name || model.label || model.value || model.id || "").trim(),
    ctxLimit: wbcModelContextLimit(model),
    refresh: !(options && options.refresh === false),
  };
  try {
    window.dispatchEvent(new CustomEvent(WBC_CHAT_MODEL_CHANGED_EVENT, { detail: detail }));
  } catch (e) {}
}

function wbcSubagentStatusText(status) {
  var key = String(status || "").trim().toLowerCase();
  var labels = {
    running: wbcT("workbenchChat.subagent.status.running", "Running"),
    resumed: wbcT("workbenchChat.subagent.status.resumed", "Resumed"),
    waiting: wbcT("workbenchChat.subagent.status.waiting", "Waiting"),
    done: wbcT("workbenchChat.subagent.status.done", "Done"),
    timeout: wbcT("workbenchChat.subagent.status.timeout", "Timed out"),
  };
  return labels[key] || key || wbcT("workbenchChat.subagent.status.unknown", "Unknown");
}

function wbcSubagentStatusClass(status) {
  var key = String(status || "").trim().toLowerCase();
  if (key === "running" || key === "resumed") return "running";
  if (key === "waiting") return "waiting";
  if (key === "timeout") return "error";
  return "done";
}

// Deterministic per-agent accent colors for the subagent chat room. Defined here
// (not shared with any historical chat renderer constants) so conversation
// components keep zero front-end coupling.
var WBC_SUBAGENT_COLORS = [
  "#3b82f6", "#e8734a", "#1f9d57", "#d94a8c", "#8b6cc4",
  "#d9a64a", "#0ea5a3", "#c2570f", "#6366f1", "#7cb518",
];

function wbcAgentColor(agentId) {
  var id = String(agentId || "");
  var hash = 0;
  for (var i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash) + id.charCodeAt(i);
    hash |= 0;
  }
  return WBC_SUBAGENT_COLORS[Math.abs(hash) % WBC_SUBAGENT_COLORS.length];
}

// Two-letter avatar initials from an agent id like "research_a" -> "RA".
function wbcAgentInitials(name) {
  var raw = String(name || "").trim();
  if (!raw) return "?";
  var parts = raw.split(/[\s_\-.]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return raw.slice(0, 2).toUpperCase();
}

// Highlight @mentions only when they name a known agent (or everyone), so the
// pass never corrupts emails / links produced by the markdown renderer.
function wbcHighlightMentions(html, agentIds) {
  return String(html == null ? "" : html).replace(
    /@([\w一-龥][\w.\-一-龥]*)/g,
    function (full, name) {
      var known = agentIds && agentIds.indexOf(name) >= 0;
      if (known || name === "所有人" || name === "all" || name === "everyone") {
        return '<span class="wbc-subagent-mention">@' + name + "</span>";
      }
      return full;
    }
  );
}

function wbcCompactNumber(value) {
  var num = Number(value || 0);
  if (!num) return "0";
  if (num >= 1000000) return (num / 1000000).toFixed(1) + "M";
  if (num >= 1000) return (num / 1000).toFixed(1) + "k";
  return String(num);
}

function wbcT(key, fallback, params) {
  var i18n = window.CyreneUI.require("i18n");
  if (typeof i18n.t === "function") {
    var value = i18n.t(key, params, fallback);
    if (value && value !== key) return value;
  }
  if (params && fallback) {
    Object.keys(params).forEach(function (name) {
      fallback = fallback.split("{" + name + "}").join(String(params[name]));
    });
  }
  return fallback || key;
}

function wbcFormatToolParameter(value) {
  if (value == null || value === "") return "";
  if (Array.isArray(value)) return value.map(wbcFormatToolParameter).filter(Boolean).join(", ");
  if (typeof value === "object") return Object.entries(value).map(function (pair) {
    var formatted = wbcFormatToolParameter(pair[1]);
    return formatted ? pair[0] + ": " + formatted : "";
  }).filter(Boolean).join(", ");
  return String(value);
}

function wbcFlattenToolObjectLiterals(value) {
  var text = String(value || "");
  var objectPattern = /\{([^{}]*)\}/g;
  for (var pass = 0; pass < 4 && /\{[^{}]*\}/.test(text); pass++) {
    objectPattern.lastIndex = 0;
    text = text.replace(objectPattern, function (_match, body) {
      if (!body.trim()) return "";
      return body.split(/,\s*(?=(?:['\"][^'\"]+['\"]|[A-Za-z_][\w.-]*)\s*:)/).map(function (part) {
        var field = part.match(/^\s*['\"]?([^'\":]+)['\"]?\s*:\s*([\s\S]*?)\s*$/);
        if (!field) return part.trim().replace(/^['\"]|['\"]$/g, "");
        var fieldValue = field[2].trim();
        if ((fieldValue.startsWith("'") && fieldValue.endsWith("'")) || (fieldValue.startsWith('"') && fieldValue.endsWith('"'))) {
          fieldValue = fieldValue.slice(1, -1);
        }
        return field[1].trim() + ": " + fieldValue;
      }).filter(Boolean).join(", ");
    });
  }
  return text.replace(/[{}]/g, "").replace(/\s+,/g, ",").trim();
}

function wbcToolPreviewText(preview) {
  var text = wbcFlattenToolObjectLiterals(preview);
  if (!text) return "";
  var operationKeys = {
    discover: "toolOperation.discover",
    describe: "toolOperation.describe",
    invoke: "toolOperation.invoke",
    list_targets: "toolOperation.list_targets",
    connect: "toolOperation.connect",
    call: "toolOperation.call",
    status: "toolOperation.status",
    disconnect: "toolOperation.disconnect",
    snapshot: "toolOperation.snapshot",
    reprobe: "toolOperation.reprobe",
    visual_describe: "toolOperation.visual_describe",
    measure_coordinates: "toolOperation.measure_coordinates",
    visual_click: "toolOperation.visual_click",
    visual_type: "toolOperation.visual_type",
    focus_window: "toolOperation.focus_window",
    restore_previous_focus: "toolOperation.restore_previous_focus",
    click_at: "toolOperation.click_at",
    double_click: "toolOperation.double_click",
    right_click: "toolOperation.right_click",
    hover_at: "toolOperation.hover_at",
    drag: "toolOperation.drag",
    swipe: "toolOperation.swipe",
    scroll_at: "toolOperation.scroll_at",
    key_chord: "toolOperation.key_chord",
    key_sequence: "toolOperation.key_sequence",
    virtual_type_at: "toolOperation.virtual_type_at",
  };
  return text.split(", ").map(function (part) {
    var token = part.trim();
    if (operationKeys[token]) return wbcT(operationKeys[token], token);
    // Progressive calls expose stable capability IDs in their arguments.
    // Resolve only values with an existing tool-name translation; arbitrary
    // user input, paths, queries, and other arguments must remain untouched.
    var localizedToolName = wbcT("toolName." + token, token);
    return localizedToolName !== token ? localizedToolName : part;
  }).join(", ");
}

function wbcToolArgsPreview(args) {
  if (!args || typeof args !== "object") return "";
  return Object.values(args).map(wbcFormatToolParameter).filter(Boolean).join(", ").slice(0, 120);
}

function wbcThinkingPhrases() {
  return wbcT(
    "workbenchChat.thinkingPhrases",
    "Thinking this through|Checking the details|Reviewing the context|Verifying the result"
  ).split("|").filter(Boolean);
}

function wbcRandomThinkingPhrase() {
  var phrases = wbcThinkingPhrases();
  return phrases[Math.floor(Math.random() * phrases.length)] || wbcT("workbenchChat.stillWorking", "Still working…");
}

function wbcBrowserFullscreenStatusText(runtime) {
  if (runtime && runtime.finalizing) {
    return wbcT("workbenchChat.status.saving", "Saving");
  }
  if (runtime && String(runtime.text || "").trim()) {
    return wbcT("workbenchChat.browserChatReplying", "Agent is replying…");
  }
  var activities = runtime && Array.isArray(runtime.activities) ? runtime.activities : [];
  var activity = activities.length ? activities[activities.length - 1] : null;
  var progress = activity && Array.isArray(activity.progress) && activity.progress.length
    ? activity.progress
    : (runtime && Array.isArray(runtime.progress) ? runtime.progress : []);
  var entry = progress.length ? progress[progress.length - 1] : null;
  if (entry) {
    var key = entry.text || entry.tool || "";
    if (entry.kind === "tool" || entry.tool) return wbcT("toolName." + key, key);
    if (entry.detailKey) return wbcT(entry.detailKey, key, entry.detailParams);
    if (key) return key;
  }
  return wbcT("workbenchChat.browserChatWorking", "Agent is working in the browser…");
}

function wbcBrowserPageTitle(browserState) {
  var browser = browserState || {};
  var activeTab = browser.activeTab || {};
  var title = String(activeTab.title || browser.title || "").trim();
  if (title && title !== "about:blank") return title;
  var rawUrl = String(activeTab.url || browser.url || browser.frameUrl || "").trim();
  if (rawUrl && rawUrl !== "about:blank") {
    try {
      var host = new URL(rawUrl).hostname.replace(/^www\./, "");
      if (host) return host;
    } catch (e) {}
  }
  return "";
}

function wbcBrowserWindowTitle(browserState) {
  var page = wbcBrowserPageTitle(browserState);
  if (page) return wbcT("workbenchChat.browserWindowTitleWithPage", "Browser · {page}", { page: page });
  return wbcT("workbenchChat.browserWindowTitle", "Browser");
}

var WBC_BROWSER_TAB_PICKER_TOGGLE_DEBOUNCE_MS = 280;

function wbcBrowserTabPickerToggleIsDebounced(lastToggleAtRef) {
  var now = Date.now();
  var lastToggleAt = Number(lastToggleAtRef && lastToggleAtRef.current || 0);
  if (lastToggleAt && now - lastToggleAt < WBC_BROWSER_TAB_PICKER_TOGGLE_DEBOUNCE_MS) return true;
  if (lastToggleAtRef) lastToggleAtRef.current = now;
  return false;
}

function wbcBrowserTabPickerPayload(browserSessionId, visible, variant) {
  var paletteNode = document.querySelector(".workbench-shell") || document.documentElement;
  var rootStyles = getComputedStyle(paletteNode);
  function color(name, fallback) {
    return String(rootStyles.getPropertyValue(name) || "").trim() || fallback;
  }
  return {
    sessionId: String(browserSessionId || ""),
    visible: visible === true,
    variant: variant === "split" ? "split" : "maximized",
    labels: {
      tabs: wbcT("workbenchChat.browserTabs", "Browser tabs"),
      browser: wbcT("workbenchChat.browserWindowTitle", "Browser"),
      reload: wbcT("browser.context.reload", "Reload"),
      mute: wbcT("browser.context.mute", "Mute"),
      unmute: wbcT("browser.context.unmute", "Unmute"),
      close: wbcT("browser.context.closeTab", "Close tab"),
    },
    colors: {
      line: color("--wb-line-2", "#d8dce4"),
      panel: color("--wb-card-bg-strong", "#ffffff"),
      text: color("--wb-text", "#17191d"),
      muted: color("--wb-muted", "#6f737b"),
      faint: color("--wb-faint", "#9297a1"),
      hover: color("--wb-control-hover-bg", "#f3f4f6"),
      selected: color("--wb-card-bg", "#f7f7f8"),
    },
  };
}

function wbcClampBrowserWindowFrame(frame, areaWidth, areaHeight, minWidth, minHeight) {
  var aw = Math.max(0, Number(areaWidth) || 0);
  var ah = Math.max(0, Number(areaHeight) || 0);
  var mw = Math.min(Math.max(1, Number(minWidth) || 1), aw || 1);
  var mh = Math.min(Math.max(1, Number(minHeight) || 1), ah || 1);
  var width = Math.min(Math.max(mw, Number(frame && frame.width) || mw), aw || mw);
  var height = Math.min(Math.max(mh, Number(frame && frame.height) || mh), ah || mh);
  var x = Math.min(Math.max(0, Number(frame && frame.x) || 0), Math.max(0, aw - width));
  var y = Math.min(Math.max(0, Number(frame && frame.y) || 0), Math.max(0, ah - height));
  return { x: x, y: y, width: width, height: height };
}

function wbcBrowserComposerDockFrame(frame, areaRect, composerRect, gap, minHeight) {
  if (!frame || !areaRect || !composerRect) return frame;
  var composerLeft = composerRect.left - areaRect.left;
  var composerRight = composerRect.right - areaRect.left;
  var overlapsComposerColumn = frame.x < composerRight && frame.x + frame.width > composerLeft;
  if (!overlapsComposerColumn) return frame;
  var gutter = Math.max(0, Number(gap) || 0);
  var minimumHeight = Math.max(1, Number(minHeight) || 1);
  var ceiling = Math.max(0, composerRect.top - areaRect.top - gutter);
  if (frame.y + frame.height <= ceiling) return frame;
  var height = Math.min(frame.height, Math.max(minimumHeight, ceiling));
  return Object.assign({}, frame, {
    y: Math.max(0, ceiling - height),
    height: height,
  });
}

function wbcKeepBrowserWindowClearOfComposer(frame, area) {
  if (!frame || !area || !area.closest) return frame;
  var main = area.closest(".wbc-main");
  var composer = main && main.querySelector(":scope > .wbc-composer");
  if (!composer) return frame;
  return wbcBrowserComposerDockFrame(
    frame,
    area.getBoundingClientRect(),
    composer.getBoundingClientRect(),
    10,
    180
  );
}

var WBC_BROWSER_FRAME_STORAGE_PREFIX = "wbc-browser-window-frame:";

function wbcLoadBrowserWindowFrame(sessionId) {
  var key = String(sessionId || "").trim();
  if (!key) return null;
  try {
    var saved = JSON.parse(localStorage.getItem(WBC_BROWSER_FRAME_STORAGE_PREFIX + key) || "null");
    if (!saved || typeof saved !== "object") return null;
    var frame = {
      x: Number(saved.x),
      y: Number(saved.y),
      width: Number(saved.width),
      height: Number(saved.height),
    };
    if (!Object.keys(frame).every(function (field) { return Number.isFinite(frame[field]); })) return null;
    frame.heightCustomized = saved.heightCustomized === true;
    return frame;
  } catch (e) {
    return null;
  }
}

function wbcSaveBrowserWindowFrame(sessionId, frame) {
  var key = String(sessionId || "").trim();
  if (!key || !frame) return;
  try {
    localStorage.setItem(WBC_BROWSER_FRAME_STORAGE_PREFIX + key, JSON.stringify({
      x: Math.round(Number(frame.x) || 0),
      y: Math.round(Number(frame.y) || 0),
      width: Math.round(Number(frame.width) || 0),
      height: Math.round(Number(frame.height) || 0),
      heightCustomized: frame.heightCustomized === true,
    }));
  } catch (e) {}
}

// Pick a readable lane beside the floating browser.  Keeping this calculation
// pure makes the product rule explicit: avoid only when the PiP is clearly off
// centre and one side remains wide enough to read.  Insets are relative to the
// transcript content box, not the Electron window.
function wbcBrowserAvoidancePlan(areaLeft, areaWidth, browserLeft, browserWidth, gap) {
  var left = Number(areaLeft) || 0;
  var width = Math.max(0, Number(areaWidth) || 0);
  var right = left + width;
  var browserStart = Number(browserLeft) || 0;
  var browserSize = Math.max(0, Number(browserWidth) || 0);
  var browserEnd = browserStart + browserSize;
  var gutter = Math.max(0, Number(gap) || 0);
  if (width <= 0 || browserSize <= 0 || browserEnd <= left || browserStart >= right) return null;

  var leftLane = Math.max(0, browserStart - gutter - left);
  var rightLane = Math.max(0, right - browserEnd - gutter);
  var readable = Math.min(360, width * 0.45);
  var centreDeadZone = Math.min(80, width * 0.12);
  if (Math.max(leftLane, rightLane) < readable) return null;
  if (Math.abs(leftLane - rightLane) < centreDeadZone) return null;
  if (leftLane > rightLane) {
    return { side: "left", start: 0, end: Math.max(0, right - browserStart + gutter) };
  }
  return { side: "right", start: Math.max(0, browserEnd - left + gutter), end: 0 };
}

function wbcNotifyBrowserLayoutChanged() {
  window.dispatchEvent(new CustomEvent("workbench:browser-layout"));
}

function wbcNotifyBrowserWindowInteraction(active, kind, sessionId, extra) {
  window.dispatchEvent(new CustomEvent("workbench:browser-window-interaction", {
    detail: Object.assign(
      { active: active === true, kind: kind || "", sessionId: String(sessionId || "") },
      extra && typeof extra === "object" ? extra : {}
    ),
  }));
}

function wbcRectsOverlap(a, b) {
  return !!(
    a && b
    && a.left < b.right
    && a.right > b.left
    && a.top < b.bottom
    && a.bottom > b.top
  );
}

function wbcPageContextMenuPlacement(clientX, clientY, avoidRect) {
  var margin = 8;
  var gap = 8;
  var width = 220;
  var height = 206;
  var viewportWidth = Math.max(width + (margin * 2), Number(window.innerWidth) || 0);
  var viewportHeight = Math.max(height + (margin * 2), Number(window.innerHeight) || 0);
  function clamp(left, top) {
    var x = Math.max(margin, Math.min(Number(left) || 0, viewportWidth - width - margin));
    var y = Math.max(margin, Math.min(Number(top) || 0, viewportHeight - height - margin));
    return {
      left: x,
      top: y,
      right: x + width,
      bottom: y + height,
    };
  }
  var base = clamp(clientX, clientY);
  if (!avoidRect || !wbcRectsOverlap(base, avoidRect)) {
    return { left: base.left, top: base.top, overlapsBrowser: false };
  }
  var candidates = [
    clamp(avoidRect.left - width - gap, clientY),
    clamp(avoidRect.right + gap, clientY),
    clamp(clientX, avoidRect.top - height - gap),
    clamp(clientX, avoidRect.bottom + gap),
  ].filter(function (candidate) { return !wbcRectsOverlap(candidate, avoidRect); });
  if (candidates.length) {
    candidates.sort(function (a, b) {
      var adx = a.left - base.left;
      var ady = a.top - base.top;
      var bdx = b.left - base.left;
      var bdy = b.top - base.top;
      return ((adx * adx) + (ady * ady)) - ((bdx * bdx) + (bdy * bdy));
    });
    return { left: candidates[0].left, top: candidates[0].top, overlapsBrowser: false };
  }
  return { left: base.left, top: base.top, overlapsBrowser: true };
}

function wbcCanOpenPageContextMenu(event) {
  var target = event && event.target;
  if (!target || !target.closest) return false;
  var selection = window.getSelection && window.getSelection();
  if (selection && !selection.isCollapsed && String(selection).trim()) return false;
  return !target.closest([
    "button",
    "a",
    "input",
    "textarea",
    "select",
    "label",
    "[contenteditable='true']",
    "[role='button']",
    "[role='link']",
    "[role='menu']",
    "[role='dialog']",
    ".wbc-composer",
    ".wbc-header",
    ".wbc-browser-window",
    ".wbc-selection-menu",
    ".wbc-conversation-nav",
    ".wbc-chat-card",
    ".wb-task-detail-tabs",
    ".workbench-confirm-modal",
  ].join(","));
}

function wbcPointInsideResourceShelf(clientX, clientY) {
  var shelf = document.querySelector(".workbench-resource-shelf");
  if (!shelf) return false;
  var rect = shelf.getBoundingClientRect();
  var x = Number(clientX);
  var y = Number(clientY);
  return Number.isFinite(x) && Number.isFinite(y)
    && x >= rect.left && x <= rect.right
    && y >= rect.top && y <= rect.bottom;
}

function wbcConversationTabAtPoint(clientX, clientY, ownerSessionId) {
  var x = Number(clientX);
  var y = Number(clientY);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  var owner = String(ownerSessionId || "");
  var tabs = document.querySelectorAll('.workbench-session-tab[data-session-kind="chat"]');
  for (var index = 0; index < tabs.length; index += 1) {
    var tab = tabs[index];
    var targetId = String(tab.getAttribute("data-session-id") || "");
    if (!targetId || targetId === owner) continue;
    var rect = tab.getBoundingClientRect();
    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
      return { node: tab, chatId: targetId };
    }
  }
  return null;
}

function wbcCycleTopbarSessionTab(direction) {
  var tabs = Array.prototype.slice.call(document.querySelectorAll(
    '.workbench-session-tab[data-session-id]'
  )).filter(function (tab) {
    var rect = tab.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  });
  if (tabs.length < 2) return false;
  var activeIndex = tabs.findIndex(function (tab) {
    return tab.getAttribute("aria-current") === "page" || tab.classList.contains("active");
  });
  var step = Number(direction) < 0 ? -1 : 1;
  var nextIndex = ((activeIndex < 0 ? 0 : activeIndex) + step + tabs.length) % tabs.length;
  tabs[nextIndex].click();
  return true;
}

function wbcHandleHorizontalWheelGesture(event, gesture, onCycle) {
  var deltaX = Number(event.deltaX || 0);
  var deltaY = Number(event.deltaY || 0);
  if (Math.abs(deltaX) < 2 || Math.abs(deltaX) <= Math.abs(deltaY) * 1.15) return false;
  event.preventDefault();
  var now = Date.now();
  var idleFor = now - Number(gesture.lastEventAt || 0);
  if (gesture.waitingForIdle) {
    gesture.lastEventAt = now;
    if (idleFor < 180 || now < gesture.lockedUntil) return true;
    gesture.waitingForIdle = false;
    gesture.delta = 0;
    gesture.direction = 0;
  } else {
    gesture.lastEventAt = now;
  }
  var direction = deltaX < 0 ? -1 : 1;
  if (gesture.direction && gesture.direction !== direction) gesture.delta = 0;
  gesture.direction = direction;
  if (now < gesture.lockedUntil) return true;
  gesture.delta += deltaX;
  if (Math.abs(gesture.delta) < 44) return true;
  if (onCycle(direction)) {
    gesture.lockedUntil = now + 420;
    gesture.waitingForIdle = true;
  }
  gesture.delta = 0;
  return true;
}

function wbcNotifyResourceShelfPointerDrag(active) {
  window.dispatchEvent(new CustomEvent("cyrene:resource-shelf-drag-state", {
    detail: { active: active === true },
  }));
}

// Shared budget error code → i18n key suffix mapping.  Defined here and
// re-used by the task controller through the registered chat service
// so adding a new budget code only needs one update.
var WORKBENCH_BUDGET_CODES = {
  budget_monthly_exhausted: "monthly",
  budget_weekly_exhausted: "weekly",
  budget_5h_exhausted: "5h",
  budget_usage_unavailable: "unavailable",
};

var WORKBENCH_ERROR_I18N_KEYS = {
  quota_exhausted: "workbenchChat.error.quotaExhausted",
  authentication_expired: "workbenchChat.error.authenticationExpired",
  model_unavailable: "workbenchChat.error.modelUnavailable",
  model_not_configured: "workbenchChat.error.modelNotConfigured",
  model_authentication_failed: "workbenchChat.error.modelAuthenticationFailed",
  process_restarted: "workbenchChat.error.processRestarted",
  chat_run_driver_failed: "workbenchChat.error.driverFailed",
  chat_not_found: "workbenchChat.error.chatNotFound",
  chat_run_not_found: "workbenchChat.error.chatRunNotFound",
  chat_not_running: "workbenchChat.error.chatNotRunning",
  chat_run_in_progress: "workbenchChat.error.chatRunInProgress",
  guidance_persistence_failed: "workbenchChat.error.guidancePersistenceFailed",
  answer_resume_failed: "workbenchChat.error.answerResumeFailed",
  no_completed_context: "workbenchChat.error.memoryContextUnavailable",
  project_mismatch: "workbenchChat.error.memoryProjectMismatch",
};

function wbcErrorText(err) {
  var raw = String((err && err.message) || err || "").trim();
  if (!raw || raw === "Load failed" || raw === "Failed to fetch" || raw === "NetworkError when attempting to fetch resource.") {
    return wbcT("workbenchChat.error.loadFailed", "Load failed");
  }
  var code = (err && err.code) || "";
  if (code.startsWith("budget_")) {
    var i18nKey = "budget.error." + (WORKBENCH_BUDGET_CODES[code] || "5h");
    return wbcT(i18nKey, raw);
  }
  var detailKey = (err && (err.detailKey || err.detail_key)) || WORKBENCH_ERROR_I18N_KEYS[code] || "";
  if (detailKey) {
    return wbcT(detailKey, raw, (err && (err.detailParams || err.detail_params)) || {});
  }
  // Keep older daemons compatible while they are being upgraded: known Codex
  // availability messages can still be localized even without error metadata.
  if (/^codex\s+quota\s+is\s+exhausted\b/i.test(raw)) {
    return wbcT("workbenchChat.error.quotaExhausted", raw);
  }
  if (/^codex\s+authentication\s+has\s+expired\b/i.test(raw)) {
    return wbcT("workbenchChat.error.authenticationExpired", raw);
  }
  if (/^codex(?:\s+model)?\b.*\bunavailable\b/i.test(raw)) {
    return wbcT("workbenchChat.error.modelUnavailable", raw);
  }
  try {
    var api = window.CyreneUI.require("api");
    if (api && typeof api.errorText === "function") return api.errorText(err);
  } catch (e) {}
  return raw;
}

function wbcAgentErrorPresentation(detail, failureKind) {
  var signature = [failureKind, detail].join(" ").toLowerCase();
  var stable = {
    dependency_missing: ["dependency", "workbenchChat.error.agentDependencyTitle", "Agent dependency is missing", "workbenchChat.error.agentDependencySummary", "The installed Agent cannot start because its executable or runtime dependency is unavailable.", "workbenchChat.error.agentDependencyHint", "Reinstall the Agent or repair the executable shown in Agent settings."],
    agent_disabled: ["configuration", "workbenchChat.error.agentDisabledTitle", "Agent is disabled", "workbenchChat.error.agentDisabledSummary", "This Agent is installed but disabled in Extensions.", "workbenchChat.error.agentDisabledHint", "Enable it in the installed Agent details, then retry."],
    auth_required: ["authentication", "workbenchChat.error.agentAuthTitle", "Agent login is required", "workbenchChat.error.agentAuthSummary", "The Agent requires its own login or credentials before it can run.", "workbenchChat.error.agentAuthHint", "Open the Agent details and complete login, then retry."],
    auth_expired: ["authentication", "workbenchChat.error.agentAuthExpiredTitle", "Agent login expired", "workbenchChat.error.agentAuthExpiredSummary", "The Agent's independent login is no longer valid.", "workbenchChat.error.agentAuthExpiredHint", "Sign in again from the Agent details."],
    protocol_mismatch: ["protocol", "workbenchChat.error.agentProtocolTitle", "Agent protocol is incompatible", "workbenchChat.error.agentProtocolSummary", "The Agent returned an ACP message that Cyrene cannot safely interpret.", "workbenchChat.error.agentProtocolHint", "Update the Agent or run Test connection to inspect its protocol version."],
    capability_missing: ["capability", "workbenchChat.error.agentCapabilityTitle", "Agent capability is unavailable", "workbenchChat.error.agentCapabilitySummary", "This operation requires a capability the selected Agent did not provide.", "workbenchChat.error.agentCapabilityHint", "Choose a supported action or another Agent."],
    model_binding_unsupported: ["model", "workbenchChat.error.agentModelBindingTitle", "Agent cannot use this model source", "workbenchChat.error.agentModelBindingSummary", "The Agent does not support the selected Cyrene or Agent-owned model configuration.", "workbenchChat.error.agentModelBindingHint", "Change Model source in the Agent details."],
    model_gateway_unavailable: ["model", "workbenchChat.error.agentGatewayTitle", "Cyrene Model Gateway is unavailable", "workbenchChat.error.agentGatewaySummary", "The Agent could not access the selected Cyrene model configuration.", "workbenchChat.error.agentGatewayHint", "Check the Cyrene model configuration and proxy, then retry."],
    agent_crashed: ["runtime", "workbenchChat.error.agentCrashedTitle", "Agent process stopped", "workbenchChat.error.agentCrashedSummary", "The external Agent process exited before completing the request.", "workbenchChat.error.agentCrashedHint", "Open diagnostics, restart the Agent, and retry."],
    session_not_loadable: ["session", "workbenchChat.error.agentSessionTitle", "Agent session cannot be restored", "workbenchChat.error.agentSessionSummary", "The Agent no longer has the session associated with this conversation.", "workbenchChat.error.agentSessionHint", "Retry to start a replacement session with Cyrene's visible conversation history."],
    request_expired: ["request", "workbenchChat.error.agentRequestExpiredTitle", "Agent request expired", "workbenchChat.error.agentRequestExpiredSummary", "The permission or input request is no longer active.", "workbenchChat.error.agentRequestExpiredHint", "Retry the message and answer the new request."],
  }[String(failureKind || "").toLowerCase()];
  if (stable) return {
    tone: stable[0],
    title: wbcT(stable[1], stable[2]),
    summary: wbcT(stable[3], stable[4]),
    hint: wbcT(stable[5], stable[6]),
  };
  if (/invalid peer certificate|certificate (?:is )?not valid for name|certificate verification|certificate_verify_failed|tls handshake/.test(signature)) {
    return {
      tone: "security",
      title: wbcT("workbenchChat.error.tlsTitle", "Secure connection was intercepted"),
      summary: wbcT("workbenchChat.error.tlsSummary", "The server certificate does not match the requested Agent service, so Cyrene stopped the connection to protect your credentials."),
      hint: wbcT("workbenchChat.error.tlsHint", "Check the system proxy, VPN, DNS, or TLS-inspection rules, then retry. Do not disable certificate verification."),
    };
  }
  if (/websocket|stream disconnected|connection reset|connection refused|network|timed?\s*out|econn/.test(signature)) {
    return {
      tone: "network",
      title: wbcT("workbenchChat.error.networkTitle", "Agent network connection failed"),
      summary: wbcT("workbenchChat.error.networkSummary", "The Agent could not keep a connection to its model service."),
      hint: wbcT("workbenchChat.error.networkHint", "Check the proxy and network connection, then retry."),
    };
  }
  return null;
}

var WBC_ICONS = {
  plus: <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>,
  search: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/></svg>,
  brain: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9.5 4.5A3 3 0 0 0 4.8 7a3.2 3.2 0 0 0-1.3 5.9A3.4 3.4 0 0 0 7 18.5a3 3 0 0 0 5-2.2V7.2a3 3 0 0 0-2.5-2.7Z"/><path d="M14.5 4.5A3 3 0 0 1 19.2 7a3.2 3.2 0 0 1 1.3 5.9 3.4 3.4 0 0 1-3.5 5.6 3 3 0 0 1-5-2.2V7.2a3 3 0 0 1 2.5-2.7Z"/><path d="M8 9.5c1.7 0 3 1.3 3 3M16 9.5c-1.7 0-3 1.3-3 3M7.2 15.5c1.4-.5 2.8 0 3.5 1.1M16.8 15.5c-1.4-.5-2.8 0-3.5 1.1"/></svg>,
  browser: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/><path d="M7 6.5h.01M10 6.5h.01"/></svg>,
  code: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="m8.5 8-4 4 4 4M15.5 8l4 4-4 4M14 4l-4 16"/></svg>,
  phase: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2.1 4.9-4.9 2.1 2.1-4.9Z"/></svg>,
  subagent: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="7" width="14" height="12" rx="3"/><path d="M9 3h6M12 3v4M8.5 12h.01M15.5 12h.01M9 16h6"/></svg>,
  permission: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2.5 20 6v5.5c0 4.8-3.2 8.3-8 10-4.8-1.7-8-5.2-8-10V6Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/></svg>,
  eventPulse: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12h4l2-6 4 12 2-6h6"/></svg>,
  database: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></svg>,
  book: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V4a2 2 0 0 0-2-2H6.5A2.5 2.5 0 0 0 4 4.5Z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/></svg>,
  map: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m9 4-5 2v14l5-2 6 2 5-2V4l-5 2Z"/><path d="M9 4v14M15 6v14"/></svg>,
  alert: <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M10.3 4 2.5 18a1.5 1.5 0 0 0 1.3 2.3h16.4a1.5 1.5 0 0 0 1.3-2.3L13.7 4a1.5 1.5 0 0 0-3.4 0Z"/><path d="M12 9v4.5M12 17h.01"/></svg>,
  errorCircle: <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/></svg>,
  infoCircle: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/></svg>,
  edit: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>,
  pin: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 17v5"/><path d="M5 17h14"/><path d="M17 3a1 1 0 0 1 1 1v4.6a2 2 0 0 0 .6 1.4l1.7 1.7A1 1 0 0 1 19.6 13H4.4a1 1 0 0 1-.7-1.7l1.7-1.7A2 2 0 0 0 6 8.2V4a1 1 0 0 1 1-1Z"/></svg>,
  dots: <svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><circle cx="5.5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="18.5" cy="12" r="1.6"/></svg>,
  planning: <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="3.5" width="16" height="17" rx="2.5"/><path d="m7 9 1.3 1.3 2.5-2.5M13.5 9h3.5M7 15h10"/></svg>,
  running: <span className="wb-spinner wbc-chat-running-spinner" aria-hidden="true" />,
  play: <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M7 4.8c0-1 1.1-1.6 2-1.1l11 6.3c.9.5.9 1.8 0 2.3L9 18.6c-.9.5-2-.1-2-1.1Z"/></svg>,
  send: <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5"/><path d="m5 12 7-7 7 7"/></svg>,
  stop: <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2.5"/></svg>,
  attach: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m21.44 11.05-9.19 9.19a5 5 0 0 1-7.07-7.07l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>,
  slash: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="m7.5 9.5 2.5 2.5-2.5 2.5"/><path d="M12.5 15h4"/></svg>,
  model: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="6" y="6" width="12" height="12" rx="3"/><circle cx="12" cy="12" r="2.5"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>,
  bolt: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/></svg>,
  copy: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>,
  retry: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 1 0 2.6-6.3"/><path d="M3 4v4h4"/></svg>,
  check: <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>,
  x: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 6 12 12M18 6 6 18"/></svg>,
  tool: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14.7 6.3a4.5 4.5 0 0 0-6 6L3 18l3 3 5.7-5.7a4.5 4.5 0 0 0 6-6L14 13l-3-3Z"/></svg>,
  layers: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m12 2.8 8.2 4.6L12 12 3.8 7.4 12 2.8Z"/><path d="m3.8 12 8.2 4.6 8.2-4.6M3.8 16.6l8.2 4.6 8.2-4.6"/></svg>,
  chat: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 11.5a8.5 8.5 0 0 1-12.2 7.6L3 21l1.9-5.8A8.5 8.5 0 1 1 21 11.5Z"/></svg>,
  file: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5"/></svg>,
  fileText: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5M9 13h6M9 17h5"/></svg>,
  image: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m4 17 5-5 3.5 3.5 2-2L20 19"/></svg>,
  pdf: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5M8 16c3-5 5-5 8 0M10 13h4"/></svg>,
  trash: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>,
  task: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1.5"/><path d="M9 14 10.5 15.5 15 11"/></svg>,
  compact: <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m8 3 4 4 4-4M12 7V1M8 21l4-4 4 4M12 17v6"/><path d="M4 10h16v4H4z"/></svg>,
  spark: <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M12 2.5 13.7 9 20 10.7 13.7 12.4 12 19l-1.7-6.6L4 10.7 10.3 9Z"/></svg>,
  folder: <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg>,
  device: <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="14" height="11" rx="2"/><path d="M7 20h8M11 15v5M19 9h2M20 8v2"/></svg>,
  fork: <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/><path d="M6 8.2v7.6M8.2 6h7.6M8.2 18H15a3 3 0 0 0 3-3V8.2"/></svg>,
  chevronRight: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6"/></svg>,
  chevronDown: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6"/></svg>,
  chevronLeft: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>,
  chevronsRight: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m13 7 5 5-5 5M6 7l5 5-5 5"/></svg>,
  openExternal: <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M7 17 17 7M8 7h9v9"/></svg>,
  download: <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v12M8 11l4 4 4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>,
  sidebar: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/><path d="m9 10-2 2 2 2"/></svg>,
  windowMinimize: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M6 12h12"/></svg>,
  windowMaximize: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M21 16v5h-5"/></svg>,
  windowRestore: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="7" y="7" width="13" height="13" rx="2"/><path d="M4 16V6a2 2 0 0 1 2-2h10"/></svg>,
};

var WbcVoice = (function () {
  var currentStatus = {
    asr_ready: false,
    tts_ready: false,
    auto_read: false,
    auto_send_after_asr: false,
    auto_stop_on_silence: true,
    tts_provider: "local",
  };
  var statusPromise = null;
  var listeners = new Set();
  var activeAudio = null;
  var activeUrl = "";
  var activeKey = "";
  var activeRequest = null;
  var activePlaybackCancel = null;
  var activeSequenceId = 0;
  var autoStreamState = null;
  var autoStreamFinalText = new Map();

  function snapshot() {
    return { status: currentStatus, activeKey: activeKey };
  }

  function notify() {
    var value = snapshot();
    listeners.forEach(function (listener) { listener(value); });
  }

  function setStatus(next) {
    currentStatus = Object.assign({}, currentStatus, next || {});
    notify();
    return currentStatus;
  }

  function refresh(force) {
    if (statusPromise && !force) return statusPromise;
    statusPromise = fetch("/api/voice/status")
      .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error("voice unavailable")); })
      .then(setStatus)
      .catch(function () { return currentStatus; })
      .finally(function () { statusPromise = null; });
    return statusPromise;
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(snapshot());
    refresh(false);
    return function () { listeners.delete(listener); };
  }

  function releaseAudio() {
    if (activeAudio) {
      activeAudio.pause();
      activeAudio.onended = null;
      activeAudio.onerror = null;
      activeAudio.src = "";
      activeAudio = null;
    }
    if (activeUrl) URL.revokeObjectURL(activeUrl);
    activeUrl = "";
  }

  function stop() {
    activeSequenceId += 1;
    autoStreamState = null;
    if (activeRequest) {
      activeRequest.abort();
      activeRequest = null;
    }
    if (activePlaybackCancel) {
      var cancelPlayback = activePlaybackCancel;
      activePlaybackCancel = null;
      cancelPlayback();
    }
    releaseAudio();
    activeKey = "";
    notify();
  }

  function responseError(response) {
    return response.json().catch(function () { return {}; }).then(function (payload) {
      throw new Error(payload.error || payload.detail || ("HTTP " + response.status));
    });
  }

  function voicePlainText(value) {
    var content = String(value || "")
      // Emoji are visual-only here.  Sending them to ZipVoice makes the model
      // pronounce Unicode names such as "WHITE HEAVY CHECK MARK" or invent a
      // Chinese-sounding syllable before the visible sentence.
      .replace(/(?:[#*0-9]\uFE0F?\u20E3)/g, " ")
      .replace(/[\u{1F000}-\u{1FAFF}\u{1FC00}-\u{1FFFF}\u2600-\u27BF\u00A9\u00AE\u2122]/gu, " ")
      .replace(/[\uFE0E\uFE0F\u200D\u20E3\u{E0020}-\u{E007F}]/gu, "")
      .replace(/<!--[\s\S]*?-->/g, " ")
      .replace(/```[\s\S]*?(?:```|$)/g, " ")
      .replace(/~~~[\s\S]*?(?:~~~|$)/g, " ")
      .replace(/\$\$[\s\S]*?\$\$/g, " ")
      .replace(/\$[^$\n]+\$/g, " ")
      .replace(/^\s*::[a-zA-Z][\w-]*\{[^\n}]*\}\s*$/gm, " ")
      .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/^\s*\[\^[^\]]+\]:.*$/gm, "")
      .replace(/\[\^[^\]]+\]/g, "")
      .replace(/\[([^\]\n]+)\]/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/^\s{0,3}(?:#{1,6}\s*|(?:>\s*)+)/gm, "")
      .replace(/^\s{0,3}(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?/gm, "")
      .replace(/^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$/gm, "")
      .replace(/^\s*(?:[-*_]\s*){3,}$/gm, "")
      .replace(/\s+#{1,6}\s*$/gm, "")
      .replace(/<\/?(?:br|p|div|li|h[1-6])\b[^>]*>/gi, "\n")
      .replace(/<https?:\/\/[^>]+>/g, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/[*_~]+/g, "")
      .replace(/\\([\\`*{}\[\]()#+.!_>~-])/g, "$1")
      .replace(/https?:\/\/\S+/g, "")
      .replace(/\|/g, "，")
      .replace(/[ \t]+/g, " ")
      .replace(/\s*\n+\s*/g, "\n")
      .trim();
    if (content && typeof document === "object" && document.createElement) {
      var decoder = document.createElement("textarea");
      decoder.innerHTML = content;
      content = decoder.value;
    }
    return content.trim();
  }

  function voiceTextChunks(value) {
    var content = voicePlainText(value);
    if (!content) return [];
    function hasSpeakableText(chunk) {
      // Backend normalization can turn punctuation- or emoji-only display
      // fragments into an empty string.  Never enqueue those fragments: one
      // empty synthesis request would otherwise stop the whole playback queue.
      try {
        return /[\p{L}\p{N}]/u.test(chunk);
      } catch (e) {
        return /[A-Za-z0-9\u3400-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]/.test(chunk);
      }
    }
    var sentences = [];
    var clauses = content.match(/[^。！？!?；;\n]+[。！？!?；;\n]*/g) || [content];
    clauses.forEach(function (clause) {
      var segmented = [];
      if (typeof Intl === "object" && typeof Intl.Segmenter === "function") {
        try {
          var segmenter = new Intl.Segmenter(undefined, { granularity: "sentence" });
          segmented = Array.from(segmenter.segment(clause), function (item) { return item.segment; });
        } catch (e) {}
      }
      sentences = sentences.concat(segmented.length ? segmented : [clause]);
    });
    var chunks = [];
    // Long model sentences are internally divided at natural clause breaks so
    // the first audible result does not wait for an entire paragraph-length
    // sentence. Completed short sentences remain one synthesis request.
    // Cloud TTS is request-rate limited, so batch more text per synthesis call
    // while keeping the low-latency local voice chunks unchanged.
    var maxChars = currentStatus.tts_provider === "minimax" ? 240 : 60;
    sentences.forEach(function (sentence) {
      var remaining = String(sentence || "").trim();
      while (remaining.length > maxChars) {
        var windowText = remaining.slice(0, maxChars + 1);
        var breakAt = Math.max(
          windowText.lastIndexOf("，"), windowText.lastIndexOf(","),
          windowText.lastIndexOf("；"), windowText.lastIndexOf(";"),
          windowText.lastIndexOf("："), windowText.lastIndexOf(":"),
          windowText.lastIndexOf(" ")
        );
        if (breakAt < 24) breakAt = maxChars;
        else breakAt += 1;
        var chunk = remaining.slice(0, breakAt).trim();
        if (chunk && hasSpeakableText(chunk)) chunks.push(chunk);
        remaining = remaining.slice(breakAt).trim();
      }
      if (remaining && hasSpeakableText(remaining)) chunks.push(remaining);
    });
    return chunks;
  }

  function requestSpeechChunk(content, sequenceId, numSteps) {
    if (sequenceId !== activeSequenceId) return Promise.reject(new DOMException("Aborted", "AbortError"));
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    activeRequest = controller;
    return fetch("/api/voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: content, num_steps: numSteps === 4 ? 4 : 6 }),
      signal: controller ? controller.signal : undefined,
    }).then(function (response) {
      if (response.status === 204) return null;
      if (!response.ok) return responseError(response);
      return response.blob();
    }).then(function (blob) {
      if (sequenceId !== activeSequenceId) throw new DOMException("Aborted", "AbortError");
      return blob;
    }).finally(function () {
      if (activeRequest === controller) activeRequest = null;
    });
  }

  function playSpeechBlob(blob, sequenceId, onStarted) {
    if (sequenceId !== activeSequenceId) return Promise.resolve(false);
    releaseAudio();
    activeUrl = URL.createObjectURL(blob);
    activeAudio = new Audio(activeUrl);
    return new Promise(function (resolve, reject) {
      var settled = false;
      function finish(played, error) {
        if (settled) return;
        settled = true;
        if (activePlaybackCancel === cancel) activePlaybackCancel = null;
        releaseAudio();
        if (error) reject(error);
        else resolve(played);
      }
      function cancel() { finish(false); }
      activePlaybackCancel = cancel;
      activeAudio.onended = function () { finish(true); };
      activeAudio.onerror = function () { finish(false, new Error("audio playback failed")); };
      activeAudio.play().then(function () {
        if (typeof onStarted === "function") onStarted();
      }).catch(function (error) { finish(false, error); });
    });
  }

  function playSpeechChunks(chunks, index, targetKey, sequenceId, preparedBlob) {
    if (sequenceId !== activeSequenceId || activeKey !== targetKey) return Promise.resolve(false);
    var blobPromise = preparedBlob
      ? Promise.resolve(preparedBlob)
      : requestSpeechChunk(chunks[index], sequenceId, index === 0 ? 4 : 6);
    return blobPromise.then(function (blob) {
      if (sequenceId !== activeSequenceId || activeKey !== targetKey) return false;
      if (!blob) {
        if (index + 1 < chunks.length) {
          return playSpeechChunks(chunks, index + 1, targetKey, sequenceId, null);
        }
        activeKey = "";
        notify();
        return true;
      }
      var nextResultPromise = null;
      return playSpeechBlob(blob, sequenceId, function () {
        if (index + 1 < chunks.length) {
          nextResultPromise = requestSpeechChunk(chunks[index + 1], sequenceId, 6).then(
            function (nextBlob) { return { blob: nextBlob }; },
            function (error) { return { error: error }; }
          );
        }
      }).then(function (played) {
        if (!played || sequenceId !== activeSequenceId || activeKey !== targetKey) return false;
        if (index + 1 >= chunks.length) {
          activeKey = "";
          notify();
          return true;
        }
        var pending = nextResultPromise || requestSpeechChunk(chunks[index + 1], sequenceId, 6).then(
          function (nextBlob) { return { blob: nextBlob }; },
          function (error) { return { error: error }; }
        );
        return pending.then(function (result) {
          if (result.error) throw result.error;
          return playSpeechChunks(chunks, index + 1, targetKey, sequenceId, result.blob);
        });
      });
    });
  }

  function speechResult(content, sequenceId, numSteps) {
    return requestSpeechChunk(content, sequenceId, numSteps).then(
      function (blob) { return { blob: blob }; },
      function (error) { return { error: error }; }
    );
  }

  function streamReadyChunks(value, finished) {
    var source = String(value || "");
    var fences = source.match(/```/g) || [];
    if (fences.length % 2 === 1) source = source.slice(0, source.lastIndexOf("```"));
    var chunks = voiceTextChunks(source);
    if (finished || !chunks.length) return chunks;
    source = source.trim();
    var inlineSource = source.replace(/```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)/g, "");
    var openSquare = (inlineSource.match(/(^|[^\\])\[/g) || []).length;
    var closeSquare = (inlineSource.match(/(^|[^\\])\]/g) || []).length;
    var markdownStable = openSquare <= closeSquare
      && (inlineSource.match(/`/g) || []).length % 2 === 0
      && (inlineSource.split("**").length - 1) % 2 === 0
      && (inlineSource.split("__").length - 1) % 2 === 0
      && (inlineSource.split("~~").length - 1) % 2 === 0
      && !/\]\([^)]*$/.test(inlineSource)
      && !/<[^>]*$/.test(inlineSource)
      && !/\\$/.test(inlineSource);
    var plainSource = voicePlainText(source);
    var endsAtSentence = markdownStable && /[。！？!?；;\n]\s*$/.test(plainSource);
    // Keep the unfinished tail buffered. Once it grows beyond maxChars,
    // voiceTextChunks yields stable clause-sized prefixes and only the final
    // partial chunk remains withheld.
    if (!endsAtSentence) chunks.pop();
    return chunks;
  }

  function prepareAutoStreamNext(state) {
    if (
      !state
      || state !== autoStreamState
      || state.sequenceId !== activeSequenceId
      || !state.playing
      || !state.queue.length
      || state.queue[0].resultPromise
    ) return;
    state.queue[0].resultPromise = speechResult(
      state.queue[0].text,
      state.sequenceId,
      state.queue[0].numSteps
    );
  }

  function completeAutoStream(state) {
    if (state !== autoStreamState || state.sequenceId !== activeSequenceId) return;
    autoStreamState = null;
    activeKey = "";
    notify();
  }

  function pumpAutoStream(state) {
    if (
      !state
      || state !== autoStreamState
      || state.sequenceId !== activeSequenceId
      || activeKey !== state.key
      || state.busy
    ) return;
    if (!state.queue.length) {
      if (state.closed) completeAutoStream(state);
      return;
    }
    var item = state.queue.shift();
    state.busy = true;
    var pending = item.resultPromise || speechResult(item.text, state.sequenceId, item.numSteps);
    pending.then(function (result) {
      if (result.error) throw result.error;
      if (state !== autoStreamState || state.sequenceId !== activeSequenceId) return false;
      if (!result.blob) return true;
      return playSpeechBlob(result.blob, state.sequenceId, function () {
        state.playing = true;
        prepareAutoStreamNext(state);
      });
    }).then(function (played) {
      if (state !== autoStreamState || state.sequenceId !== activeSequenceId) return;
      state.busy = false;
      state.playing = false;
      if (!played) return;
      pumpAutoStream(state);
    }).catch(function (error) {
      if (error && error.name === "AbortError") return;
      stop();
      try {
        window.CyreneUI.require("feedback").showToast(
          wbcT("workbenchChat.voicePlaybackFailed", "Could not play speech: {error}", { error: error.message || String(error) }),
          "error"
        );
      } catch (e) {}
    });
  }

  function newAutoStreamState(targetKey) {
    return {
      key: targetKey,
      sequenceId: activeSequenceId,
      queue: [],
      produced: [],
      streamGeneration: 0,
      streamSentenceCount: 0,
      queuedKeys: new Set(),
      busy: false,
      playing: false,
      closed: false,
    };
  }

  function autoStream(text, key, finished, restart) {
    var targetKey = String(key || "auto-stream");
    return refresh(false).then(function (voiceStatus) {
      if (!voiceStatus.auto_read || !voiceStatus.tts_ready) return false;
      if (restart) autoStreamFinalText.delete(targetKey);
      var state = autoStreamState;
      if (!state || state.key !== targetKey) {
        stop();
        state = newAutoStreamState(targetKey);
        autoStreamState = state;
        activeKey = targetKey;
        notify();
      } else if (restart) {
        // A visible reply stream can begin after one or more intermediate
        // messages. Reset only the stream cursor so those queued messages
        // finish speaking instead of being cut off by reply_start.
        state.produced = [];
        state.streamGeneration += 1;
        state.streamSentenceCount = 0;
        state.closed = false;
      }
      var chunks = streamReadyChunks(text, finished === true);
      if (
        finished === true
        && state.closed
        && JSON.stringify(state.produced) !== JSON.stringify(chunks)
      ) {
        // Some providers emit a provisional reply_done before Cyrene publishes
        // the authoritative terminal reply.  Restart the terminal cursor when
        // their content differs so the actual final answer is never skipped.
        state.produced = [];
        state.streamGeneration += 1;
        state.streamSentenceCount = 0;
      }
      for (var i = state.produced.length; i < chunks.length; i += 1) {
        var streamItemKey = "stream:" + state.streamGeneration + ":" + i + ":" + chunks[i];
        if (state.queuedKeys.has(streamItemKey)) continue;
        state.queuedKeys.add(streamItemKey);
        state.queue.push({
          text: chunks[i],
          numSteps: state.streamSentenceCount === 0 ? 4 : 6,
          resultPromise: null,
        });
        state.streamSentenceCount += 1;
      }
      if (chunks.length > state.produced.length) state.produced = chunks.slice();
      if (finished === true) {
        state.closed = true;
        autoStreamFinalText.set(targetKey, voicePlainText(text));
      }
      if (state.playing) prepareAutoStreamNext(state);
      pumpAutoStream(state);
      return true;
    });
  }

  function speak(text, key) {
    var chunks = voiceTextChunks(text);
    var targetKey = String(key || "voice");
    if (!chunks.length || !currentStatus.tts_ready) return Promise.resolve(false);
    if (activeKey === targetKey) {
      stop();
      return Promise.resolve(false);
    }
    stop();
    activeKey = targetKey;
    var sequenceId = activeSequenceId;
    notify();
    return playSpeechChunks(chunks, 0, targetKey, sequenceId, null).catch(function (error) {
      if (error && error.name === "AbortError") return false;
      stop();
      try {
        window.CyreneUI.require("feedback").showToast(
          wbcT("workbenchChat.voicePlaybackFailed", "Could not play speech: {error}", { error: error.message || String(error) }),
          "error"
        );
      } catch (e) {}
      return false;
    });
  }

  function queueAutoSpeech(text, key, itemKey, voiceStatus) {
    if (!voiceStatus.auto_read || !voiceStatus.tts_ready) return false;
    var chunks = voiceTextChunks(text);
    if (!chunks.length) return false;
    var targetKey = String(key || "auto-speech");
    var state = autoStreamState;
    if (!state || state.key !== targetKey) {
      stop();
      state = newAutoStreamState(targetKey);
      state.closed = true;
      autoStreamState = state;
      activeKey = targetKey;
      notify();
    }
    var sourceKey = String(itemKey || text || "message");
    chunks.forEach(function (chunk, index) {
      var chunkKey = "message:" + sourceKey + ":" + index;
      if (state.queuedKeys.has(chunkKey)) return;
      state.queuedKeys.add(chunkKey);
      state.queue.push({ text: chunk, numSteps: index === 0 ? 4 : 6, resultPromise: null });
    });
    if (state.playing) prepareAutoStreamNext(state);
    pumpAutoStream(state);
    return true;
  }

  function autoSpeak(text, key, itemKey) {
    return refresh(false).then(function (voiceStatus) {
      return queueAutoSpeech(text, key, itemKey, voiceStatus);
    });
  }

  function autoSpeakFinal(text, key, itemKey) {
    var targetKey = String(key || "auto-speech");
    return refresh(false).then(function (voiceStatus) {
      if (!voiceStatus.auto_read || !voiceStatus.tts_ready) return false;
      var finalText = voicePlainText(text);
      var streamedText = autoStreamFinalText.get(targetKey);
      autoStreamFinalText.delete(targetKey);
      // reply_done already queued this exact terminal snapshot.  The durable
      // saved event is a fallback for providers/reconnects that did not deliver
      // a usable final stream, not a request to read the same answer twice.
      if (finalText && streamedText === finalText) return true;
      return queueAutoSpeech(text, targetKey, itemKey, voiceStatus);
    });
  }

  window.addEventListener("cyrene:voice-status-changed", function (event) {
    var detail = event && event.detail;
    if (detail && typeof detail === "object") setStatus(detail);
    else refresh(true);
  });
  window.addEventListener("cyrene:voice-stop", function () {
    if (typeof WbVoiceCommand !== "undefined" && WbVoiceCommand && WbVoiceCommand.clearSpeechQueue) {
      WbVoiceCommand.clearSpeechQueue();
    }
    stop();
  });

  return {
    autoSpeak: autoSpeak,
    autoSpeakFinal: autoSpeakFinal,
    autoStream: autoStream,
    refresh: refresh,
    speak: speak,
    plainText: voicePlainText,
    splitText: voiceTextChunks,
    getSnapshot: snapshot,
    stop: stop,
    subscribe: subscribe,
  };
})();

function wbcResampleVoice(samples, sourceRate, targetRate) {
  if (sourceRate === targetRate) return samples;
  var targetLength = Math.max(1, Math.round(samples.length * targetRate / sourceRate));
  var output = new Float32Array(targetLength);
  var scale = (samples.length - 1) / Math.max(1, targetLength - 1);
  for (var i = 0; i < targetLength; i += 1) {
    var position = i * scale;
    var left = Math.floor(position);
    var right = Math.min(samples.length - 1, left + 1);
    var weight = position - left;
    output[i] = samples[left] * (1 - weight) + samples[right] * weight;
  }
  return output;
}

function wbcVoiceWavBlob(chunks, sourceRate) {
  var length = chunks.reduce(function (total, chunk) { return total + chunk.length; }, 0);
  var merged = new Float32Array(length);
  var offset = 0;
  chunks.forEach(function (chunk) { merged.set(chunk, offset); offset += chunk.length; });
  var samples = wbcResampleVoice(merged, sourceRate, 16000);
  var buffer = new ArrayBuffer(44 + samples.length * 2);
  var view = new DataView(buffer);
  function writeString(at, value) {
    for (var i = 0; i < value.length; i += 1) view.setUint8(at + i, value.charCodeAt(i));
  }
  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (var sampleIndex = 0; sampleIndex < samples.length; sampleIndex += 1) {
    var value = Math.max(-1, Math.min(1, samples[sampleIndex]));
    view.setInt16(44 + sampleIndex * 2, value < 0 ? value * 32768 : value * 32767, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

// Shared by every composer that supports local voice input. Keep FireRedASR's
// silence-token handling and response parsing in one place so task and chat
// inputs cannot drift into subtly different behavior.
function wbcCleanVoiceTranscript(value) {
  var content = String(value || "").trim();
  if (!content) return "";
  content = content.replace(
    /(?:\*{1,3}|_{1,3})?\s*<\s*sil(?:ence)?\s*>\s*(?:\*{1,3}|_{1,3})?\s*[。.!！?？,，、;；:：…]*/gi,
    " "
  );
  content = content.replace(/\s+/g, " ").trim();
  if (/^[*_~。.!！?？,，、;；:：…\s]+$/.test(content)) return "";
  return content;
}

function wbcIsVoiceSilenceTranscript(value) {
  var content = String(value || "");
  return /<\s*sil(?:ence)?\s*>/i.test(content) && !wbcCleanVoiceTranscript(content);
}

function wbcTranscribeVoiceBlob(blob) {
  var form = new FormData();
  form.append("audio", blob, "voice-input.wav");
  return fetch("/api/voice/asr", { method: "POST", body: form })
    .then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) throw new Error(payload.error || payload.detail || ("HTTP " + response.status));
        return payload;
      });
    })
    .then(function (payload) {
      var rawTranscript = String(payload.text || "").trim();
      var silenceOnly = payload.silence_only === true || wbcIsVoiceSilenceTranscript(rawTranscript);
      var transcript = wbcCleanVoiceTranscript(rawTranscript);
      // FireRedASR can emit a literal <sil> token for a silent recording.
      // Treat it as an intentional no-op: never touch a draft or auto-send.
      if (silenceOnly) return false;
      if (!transcript) throw new Error(wbcT("workbenchChat.noRecognizedSpeech", "No speech was recognized"));
      return transcript;
    });
}

// Composer voice input uses the same persistent, replacing status-toast
// pattern as the top-bar voice command. Each mounted composer owns its toast
// id so a phase change updates one notice instead of stacking several.
function wbcCreateComposerVoiceFeedback() {
  var statusToastId = 0;

  function dismiss() {
    if (!statusToastId) return;
    try { window.CyreneUI.require("feedback").dismissToast(statusToastId); } catch (e) {}
    statusToastId = 0;
  }

  function show(message, type, duration) {
    dismiss();
    try {
      statusToastId = window.CyreneUI.require("feedback").showToast(message, type || "info", {
        duration: duration == null ? 0 : duration,
      });
    } catch (e) {
      statusToastId = 0;
    }
  }

  return {
    starting: function () {
      show(wbcT("topbar.voiceCommandStartingNotice", "Starting voice input…"), "info", 0);
    },
    listening: function () {
      show(wbcT("topbar.voiceCommandListening", "Listening; start speaking"), "info", 0);
    },
    transcribing: function () {
      show(wbcT("topbar.voiceCommandRecognizingNotice", "Recognizing speech…"), "info", 0);
    },
    complete: function () {
      show(wbcT("workbenchChat.voiceInputComplete", "Voice recognition complete"), "success", 3600);
    },
    noSpeech: function () {
      show(wbcT("workbenchChat.noRecognizedSpeech", "No speech was recognized"), "warning", 3600);
    },
    error: function (error) {
      var message = error && error.message ? error.message : String(error || "");
      show(
        wbcT("workbenchChat.voiceInputFailed", "Could not recognize speech: {error}", { error: message }),
        "error",
        6000
      );
    },
    dismiss: dismiss,
  };
}

var WBC_VOICE_SILENCE_MS = 1600;
var WBC_VOICE_MIN_SPEECH_MS = 240;
var WBC_VOICE_SPEECH_RMS = 0.012;
var WBC_VOICE_SPEECH_PEAK = 0.08;

function wbcCreateVoiceSilenceDetector(onSilence, options) {
  var detectorOptions = options || {};
  var initialSilenceMs = Math.max(0, Number(detectorOptions.initialSilenceMs) || 0);
  var speechMs = 0;
  var silenceMs = 0;
  var elapsedBeforeSpeechMs = 0;
  var speechStarted = false;
  var triggered = false;
  return function (samples, sampleRate) {
    if (triggered || !samples.length || !sampleRate) return;
    var sumSquares = 0;
    var peak = 0;
    for (var i = 0; i < samples.length; i += 1) {
      var amplitude = Math.abs(samples[i]);
      sumSquares += amplitude * amplitude;
      if (amplitude > peak) peak = amplitude;
    }
    var rms = Math.sqrt(sumSquares / samples.length);
    var durationMs = samples.length * 1000 / sampleRate;
    if (!speechStarted) elapsedBeforeSpeechMs += durationMs;
    var voiced = rms >= WBC_VOICE_SPEECH_RMS || peak >= WBC_VOICE_SPEECH_PEAK;
    if (voiced) {
      speechMs += durationMs;
      silenceMs = 0;
      if (speechMs >= WBC_VOICE_MIN_SPEECH_MS) speechStarted = true;
    } else if (speechStarted) {
      silenceMs += durationMs;
    } else {
      speechMs = Math.max(0, speechMs - durationMs);
    }
    if (
      (speechStarted && silenceMs >= WBC_VOICE_SILENCE_MS)
      || (!speechStarted && initialSilenceMs > 0 && elapsedBeforeSpeechMs >= initialSilenceMs)
    ) {
      triggered = true;
      onSilence();
    }
  };
}

function wbcStartVoiceRecorder(options) {
  var recorderOptions = options || {};
  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
    return Promise.reject(new Error(wbcT("workbenchChat.microphoneUnavailable", "Microphone access is unavailable")));
  }
  return navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  }).then(function (stream) {
    var AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      throw new Error(wbcT("workbenchChat.microphoneUnavailable", "Microphone access is unavailable"));
    }
    var context = new AudioContextClass();
    var source = context.createMediaStreamSource(stream);
    var processor = context.createScriptProcessor(4096, 1, 1);
    var silent = context.createGain();
    var chunks = [];
    var stopped = false;
    var stopPromise = null;
    var controller = null;
    var detectSilence = recorderOptions.autoStopOnSilence
      ? wbcCreateVoiceSilenceDetector(function () {
          setTimeout(function () {
            if (!stopped && controller && typeof recorderOptions.onSilence === "function") {
              recorderOptions.onSilence(controller);
            }
          }, 0);
        }, { initialSilenceMs: recorderOptions.initialSilenceMs })
      : null;
    silent.gain.value = 0;
    processor.onaudioprocess = function (event) {
      var chunk = new Float32Array(event.inputBuffer.getChannelData(0));
      chunks.push(chunk);
      if (detectSilence) detectSilence(chunk, context.sampleRate);
    };
    source.connect(processor);
    processor.connect(silent);
    silent.connect(context.destination);
    controller = {
      stop: function () {
        if (stopPromise) return stopPromise;
        stopped = true;
        processor.onaudioprocess = null;
        try { source.disconnect(); processor.disconnect(); silent.disconnect(); } catch (e) {}
        stream.getTracks().forEach(function (track) { track.stop(); });
        var sourceRate = context.sampleRate;
        stopPromise = Promise.resolve(context.close()).catch(function () {}).then(function () {
          if (!chunks.length) throw new Error(wbcT("workbenchChat.noRecordedAudio", "No audio was recorded"));
          return wbcVoiceWavBlob(chunks, sourceRate);
        });
        return stopPromise;
      },
    };
    return controller;
  });
}

var WBC_TOPBAR_INITIAL_SILENCE_MS = 5000;

// App-wide voice-command entry point used by both the titlebar button and its
// focused-window shortcut. It deliberately owns no navigation state: a valid
// transcript is sent to a silently-created chat and monitored by run id.
var WbVoiceCommand = (function () {
  var phase = "";
  var ready = false;
  var recorder = null;
  var listeners = new Set();
  var voiceSnapshot = WbcVoice.getSnapshot();
  var speechQueue = [];
  var speaking = false;
  var runStates = new Map();
  var statusToastId = 0;

  function snapshot() {
    return { phase: phase, ready: ready };
  }

  function notify() {
    var value = snapshot();
    listeners.forEach(function (listener) { listener(value); });
  }

  function setPhase(next) {
    phase = next;
    notify();
  }

  function showStatusToast(message, type, duration) {
    var feedback = window.CyreneUI.require("feedback");
    if (statusToastId) feedback.dismissToast(statusToastId);
    statusToastId = feedback.showToast(message, type || "info", {
      duration: duration == null ? 0 : duration,
    });
  }

  function showError(error) {
    var message = error && error.message ? error.message : String(error || "");
    showStatusToast(
      wbcT("topbar.voiceCommandFailed", "Voice command failed: {error}", { error: message }),
      "error",
      6000
    );
  }

  function responsePayload(response) {
    return response.json().catch(function () { return {}; }).then(function (payload) {
      if (!response.ok) throw new Error(payload.error || payload.detail || ("HTTP " + response.status));
      return payload;
    });
  }

  function currentLanguage() {
    try { return window.CyreneUI.require("i18n").getLang(); } catch (e) { return ""; }
  }

  function currentUiInstanceId() {
    try {
      if (window.CyreneUI.has("uiSurface")) return window.CyreneUI.require("uiSurface").getInstanceId();
    } catch (e) {}
    return "";
  }

  function clearSpeechQueue() {
    speechQueue = [];
  }

  function speechEnabled() {
    var status = voiceSnapshot && voiceSnapshot.status;
    return !!(status && status.auto_read && status.tts_ready);
  }

  function drainSpeechQueue() {
    if (speaking || !speechQueue.length || !speechEnabled()) return;
    if (voiceSnapshot && voiceSnapshot.activeKey) return;
    var item = speechQueue.shift();
    speaking = true;
    WbcVoice.speak(item.text, "voice-command:" + item.runId + ":" + item.id)
      .finally(function () {
        speaking = false;
        drainSpeechQueue();
      });
  }

  function enqueueSpeech(state, text, kind) {
    var plain = WbcVoice.plainText(text);
    if (!plain || !speechEnabled()) return;
    var dedupeKey = kind + ":" + plain;
    if (state.seen.has(dedupeKey)) return;
    state.seen.add(dedupeKey);
    speechQueue.push({
      id: state.sequence += 1,
      runId: state.runId,
      kind: kind,
      text: plain,
    });
    drainSpeechQueue();
  }

  function replaceQueuedRunSpeech(state, text, kind) {
    // Never interrupt an item that has already started. Everything for this
    // run that is still waiting is provisional and can be replaced atomically.
    speechQueue = speechQueue.filter(function (item) { return item.runId !== state.runId; });
    enqueueSpeech(state, text, kind);
  }

  function pendingQuestionText(pending) {
    var question = pending && typeof pending === "object" ? pending : {};
    var prompt = String(question.text || question.prompt || question.question || question.title || "").trim();
    var values = Array.isArray(question.options) ? question.options : (Array.isArray(question.choices) ? question.choices : []);
    var options = values.map(function (item) {
      if (item && typeof item === "object") return String(item.label || item.text || item.title || item.value || "").trim();
      return String(item || "").trim();
    }).filter(Boolean);
    if (!options.length) return prompt;
    return prompt + (currentLanguage() === "zh" ? "。可选项：" : ". Options: ") + options.join(currentLanguage() === "zh" ? "；" : "; ");
  }

  function latestAssistantText(chatId) {
    return fetch("/api/workbench/chats/" + encodeURIComponent(chatId))
      .then(responsePayload)
      .then(function (payload) {
        var messages = payload && payload.chat && Array.isArray(payload.chat.messages) ? payload.chat.messages : [];
        for (var i = messages.length - 1; i >= 0; i -= 1) {
          if (messages[i] && messages[i].role === "assistant" && String(messages[i].content || "").trim()) {
            return String(messages[i].content || "");
          }
        }
        return "";
      })
      .catch(function () { return ""; });
  }

  function handleRunEvent(state, event) {
    var data = event && event.data && typeof event.data === "object" ? event.data : {};
    if (event.type === "intermediate_message" && !state.finalSeen) {
      var message = data.message && typeof data.message === "object" ? data.message : {};
      if (!message.role || message.role === "assistant") {
        enqueueSpeech(state, message.content || message.text || "", "intermediate");
      }
      return;
    }
    if (event.type === "reply_done") {
      var finalText = String(data.response || "").trim();
      if (finalText) {
        state.finalSeen = true;
        replaceQueuedRunSpeech(state, finalText, "final");
      }
      return;
    }
    if (event.type === "awaiting_user") {
      var questionText = pendingQuestionText(data.pending_question || data.pendingQuestion);
      state.finalSeen = true;
      replaceQueuedRunSpeech(state, questionText, "question");
      return;
    }
    if (event.type === "error") {
      showError(new Error(data.message || data.error || data.code || "Agent run failed"));
    }
  }

  function pollRun(state) {
    if (!runStates.has(state.runId)) return;
    fetch(
      "/v1/control/runs/" + encodeURIComponent(state.runId)
      + "/events?after=" + encodeURIComponent(state.cursor) + "&limit=200"
    ).then(responsePayload).then(function (payload) {
      var events = Array.isArray(payload.events) ? payload.events : [];
      events.forEach(function (event) {
        state.cursor = Math.max(state.cursor, Number(event.cursor) || 0);
        handleRunEvent(state, event);
      });
      state.cursor = Math.max(state.cursor, Number(payload.next_cursor) || 0);
      if (!payload.completed) {
        state.timer = setTimeout(function () { pollRun(state); }, 350);
        return;
      }
      runStates.delete(state.runId);
      if (state.finalSeen) return;
      latestAssistantText(state.chatId).then(function (text) {
        if (text) replaceQueuedRunSpeech(state, text, "final-fallback");
      });
    }).catch(function (error) {
      runStates.delete(state.runId);
      showError(error);
    });
  }

  function monitorRun(runId, chatId, cursor) {
    if (!runId || runStates.has(runId)) return;
    var state = {
      runId: String(runId),
      chatId: String(chatId || ""),
      cursor: Number(cursor) || 0,
      finalSeen: false,
      sequence: 0,
      seen: new Set(),
      timer: 0,
    };
    runStates.set(state.runId, state);
    pollRun(state);
  }

  function finishRecording(controller) {
    if (phase !== "recording" && phase !== "starting") return Promise.resolve(false);
    var activeRecorder = controller || recorder;
    recorder = null;
    setPhase("recognizing");
    showStatusToast(
      wbcT("topbar.voiceCommandRecognizingNotice", "Recognizing speech…"),
      "info",
      0
    );
    if (!activeRecorder) {
      setPhase("");
      return Promise.resolve(false);
    }
    return activeRecorder.stop().then(function (blob) {
      var form = new FormData();
      form.append("audio", blob, "voice-command.wav");
      form.append("lang", currentLanguage());
      form.append("ui_instance_id", currentUiInstanceId());
      return fetch("/api/workbench/voice-command", { method: "POST", body: form });
    }).then(responsePayload).then(function (payload) {
      if (payload.created) {
        showStatusToast(
          wbcT("topbar.voiceCommandComplete", "Recognized and sent to a new chat"),
          "success",
          3600
        );
        monitorRun(payload.run_id, payload.chat_id, payload.event_cursor);
      } else {
        showStatusToast(
          wbcT("topbar.voiceCommandNoSpeech", "No speech recognized; no chat was created"),
          "warning",
          3600
        );
      }
      return !!payload.created;
    }).catch(function (error) {
      showError(error);
      return false;
    }).finally(function () {
      setPhase("");
    });
  }

  function start() {
    if (phase) return Promise.resolve(false);
    clearSpeechQueue();
    WbcVoice.stop();
    setPhase("starting");
    showStatusToast(
      wbcT("topbar.voiceCommandStartingNotice", "Starting voice input…"),
      "info",
      0
    );
    return WbcVoice.refresh(true).then(function (status) {
      ready = !!(status && status.asr_ready && status.tts_ready);
      notify();
      if (!ready) throw new Error(wbcT("topbar.voiceModelsNotReady", "Configure both local voice models first"));
      return wbcStartVoiceRecorder({
        autoStopOnSilence: true,
        initialSilenceMs: WBC_TOPBAR_INITIAL_SILENCE_MS,
        onSilence: finishRecording,
      });
    }).then(function (controller) {
      recorder = controller;
      setPhase("recording");
      showStatusToast(
        wbcT("topbar.voiceCommandListening", "Listening; start speaking"),
        "info",
        0
      );
      return true;
    }).catch(function (error) {
      recorder = null;
      setPhase("");
      showError(error);
      return false;
    });
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(snapshot());
    return function () { listeners.delete(listener); };
  }

  WbcVoice.subscribe(function (nextSnapshot) {
    voiceSnapshot = nextSnapshot;
    var status = nextSnapshot && nextSnapshot.status;
    var nextReady = !!(status && status.asr_ready && status.tts_ready);
    if (ready !== nextReady) {
      ready = nextReady;
      notify();
    }
    drainSpeechQueue();
  });

  return { clearSpeechQueue: clearSpeechQueue, start: start, subscribe: subscribe, snapshot: snapshot };
})();

// Conversation-panel icons share one 18px optical grid and stroke language.
// They are intentionally panel-specific instead of borrowing generic toolbar
// glyphs whose proportions and metaphors differ from the selected design.
var WBC_SIDE_TAB_ICONS = {
  overview: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h11A2.5 2.5 0 0 1 20 7.5v10a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5Z"/><path d="M8 5V3.8M16 5V3.8M8 10.5h8M12 8.5v4"/></svg>,
  plan: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5" y="4.5" width="14" height="16" rx="2.5"/><path d="M9 4.5V3h6v1.5M8.5 10.5l1.4 1.4 2.6-2.8M14.5 11h2M8.5 16h8"/></svg>,
  subagents: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 9.5A4.5 4.5 0 0 1 11.5 5H14a4 4 0 0 1 4 4v.5a4.5 4.5 0 0 1 2 3.7v2.3a3.5 3.5 0 0 1-3.5 3.5h-9A3.5 3.5 0 0 1 4 15.5v-2.3a4.5 4.5 0 0 1 3-4.2Z"/><path d="M9 13h.01M15 13h.01M9.5 16h5M12 5V2.8M10.5 2.8h3"/></svg>,
  context: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="4" width="6" height="6" rx="2"/><rect x="14" y="4" width="6" height="6" rx="2"/><rect x="4" y="14" width="6" height="6" rx="2"/><rect x="14" y="14" width="6" height="6" rx="2"/><path d="M10 7h4M7 10v4M17 10v4M10 17h4"/></svg>,
  files: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 4.5h10A2.5 2.5 0 0 1 19.5 7v11A2.5 2.5 0 0 1 17 20.5H7A2.5 2.5 0 0 1 4.5 18V7A2.5 2.5 0 0 1 7 4.5Z"/><path d="M9 4.5V3h6v1.5M8 10h8M8 14h5M8 17h7"/></svg>,
  artifacts: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 8.5h16v9A2.5 2.5 0 0 1 17.5 20h-11A2.5 2.5 0 0 1 4 17.5Z"/><path d="M3.5 5.5h17v3h-17zM9 12h6M12 8.5V12"/></svg>,
  changes: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="7" cy="5" r="2"/><circle cx="7" cy="19" r="2"/><circle cx="17" cy="8" r="2"/><path d="M7 7v10M9 17c5 0 8-2.5 8-7M14.5 8H10"/></svg>,
  branches: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M8 17h4a6 6 0 0 0 6-6V10"/></svg>,
  viewer: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="m6 16 3.5-3.5 2.7 2.7 2.3-2.3L18 16M8 9h.01"/><path d="M3.5 8.5h17"/></svg>,
  map: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m4 6.5 5-2.2 6 2.2 5-2.2v13.2l-5 2.2-6-2.2-5 2.2Z"/><path d="M9 4.3v13.2M15 6.5v13.2"/><circle cx="12" cy="11" r="1.5"/></svg>,
  browser: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4.5" width="18" height="15" rx="3"/><path d="M3 8.5h18M7 6.5h.01M10 6.5h.01"/><circle cx="12" cy="14" r="3.3"/><path d="M8.7 14h6.6M12 10.7c1.1 1.2 1.1 5.4 0 6.6M12 10.7c-1.1 1.2-1.1 5.4 0 6.6"/></svg>,
  terminal: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4.5" width="18" height="15" rx="3"/><path d="m7.5 9 3 3-3 3M13 15h3.5"/></svg>,
  "side-agents": <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 4.5h10A2.5 2.5 0 0 1 19.5 7v10A2.5 2.5 0 0 1 17 19.5h-4L9 22v-2.5H7A2.5 2.5 0 0 1 4.5 17V7A2.5 2.5 0 0 1 7 4.5Z"/><path d="M9 9h6M9 13h4"/></svg>,
};

// Slash commands + permission modes (mirrors the legacy agent capabilities;
// defined locally so this page stays independent from workbench.jsx).
var WBC_COMMANDS = [
  { id: "quick-answer", labelKey: "workbenchChat.command.quick-answer.label", descKey: "workbenchChat.command.quick-answer.desc" },
  { id: "deep-research", labelKey: "workbenchChat.command.deep-research.label", descKey: "workbenchChat.command.deep-research.desc" },
  { id: "deep-reflect", labelKey: "workbenchChat.command.deep-reflect.label", descKey: "workbenchChat.command.deep-reflect.desc" },
  { id: "help-me-decide", labelKey: "workbenchChat.command.help-me-decide.label", descKey: "workbenchChat.command.help-me-decide.desc" },
  { id: "learning-plan", labelKey: "workbenchChat.command.learning-plan.label", descKey: "workbenchChat.command.learning-plan.desc" },
  { id: "daily-review", labelKey: "workbenchChat.command.daily-review.label", descKey: "workbenchChat.command.daily-review.desc" },
  { id: "deep-compare", labelKey: "workbenchChat.command.deep-compare.label", descKey: "workbenchChat.command.deep-compare.desc" },
  { id: "terminal", labelKey: "workbenchChat.command.terminal.label", descKey: "workbenchChat.command.terminal.desc" },
];

var WBC_COMMAND_ICONS = {
  "quick-answer": WBC_ICONS.bolt,
  "deep-research": WBC_ICONS.search,
  "deep-reflect": WBC_ICONS.spark,
  "help-me-decide": WBC_ICONS.task,
  "learning-plan": WBC_ICONS.file,
  "daily-review": WBC_ICONS.task,
  "deep-compare": WBC_ICONS.fork,
  terminal: WBC_ICONS.terminal,
};

var WBC_MODES = [
  { id: "default", labelKey: "workbenchChat.mode.default.label", descKey: "workbenchChat.mode.default.desc" },
  { id: "auto", labelKey: "workbenchChat.mode.auto.label", descKey: "workbenchChat.mode.auto.desc" },
  { id: "plan", labelKey: "workbenchChat.mode.plan.label", descKey: "workbenchChat.mode.plan.desc" },
  { id: "full_access", labelKey: "workbenchChat.mode.full_access.label", descKey: "workbenchChat.mode.full_access.desc" },
];
var WBC_REASONING_EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"];

function wbcIsDeepSeekModel(model) {
  return String(model && (model.model || model.name || model.id) || "")
    .trim().toLowerCase().indexOf("deepseek") >= 0;
}

function wbcSupportedReasoningEfforts(model) {
  var raw = model && (
    model.supportedReasoningEfforts
    || model.supported_reasoning_efforts
  );
  var efforts = (Array.isArray(raw) ? raw : []).map(function (option) {
    return String(
      option && (option.reasoningEffort || option.reasoning_effort)
      || option
      || ""
    ).trim().toLowerCase();
  }).filter(function (effort) {
    return WBC_REASONING_EFFORT_ORDER.indexOf(effort) >= 0;
  });
  if (!efforts.length && wbcIsDeepSeekModel(model)) efforts = ["high", "max"];
  return Array.from(new Set(efforts)).sort(function (a, b) {
    return WBC_REASONING_EFFORT_ORDER.indexOf(a) - WBC_REASONING_EFFORT_ORDER.indexOf(b);
  });
}

function wbcReasoningEffortForModel(model, preferred) {
  var effort = String(
    preferred
    || model && (model.reasoning_effort || model.defaultReasoningEffort || model.default_reasoning_effort)
    || ""
  ).trim().toLowerCase();
  if (wbcIsDeepSeekModel(model)) {
    if (["low", "medium", "high"].indexOf(effort) >= 0) effort = "high";
    else if (["xhigh", "max"].indexOf(effort) >= 0) effort = "max";
    else effort = "high";
  }
  var supported = wbcSupportedReasoningEfforts(model);
  if (supported.length && supported.indexOf(effort) < 0) {
    effort = String(
      model && (model.defaultReasoningEffort || model.default_reasoning_effort)
      || supported[0]
      || ""
    ).trim().toLowerCase();
  }
  return effort;
}

function wbcFriendlyModelName(model, fallback) {
  var configuredName = String(model && model.name || "").trim();
  var modelId = String(model && model.model || fallback || "").trim();
  if (configuredName && configuredName !== modelId) return configuredName;
  if (!modelId) return configuredName;
  var words = modelId.replace(/^gpt-/i, "").split(/[-_]+/).filter(Boolean);
  return words.map(function (word) {
    if (/^\d/.test(word)) return word.toUpperCase();
    if (word.toLowerCase() === "deepseek") return "DeepSeek";
    return word.charAt(0).toUpperCase() + word.slice(1);
  }).join(" ");
}

function wbcLocalizedModelDescription(model) {
  var description = String(model && (model.desc || model.description) || "").trim();
  if (!description) return "";
  var modelName = wbcFriendlyModelName(model, model && (model.model || model.value || model.id));
  var providerName = String(modelName || "").split(/\s+/)[0];
  if (providerName && description.toLowerCase() === (providerName + " default").toLowerCase()) {
    return wbcT("workbenchChat.modelProviderDefault", "{provider} default", { provider: providerName });
  }
  return description;
}

function wbcNormalizePermissionMode(value, fallback) {
  return normalizePermissionModeBehavior(
    value,
    fallback,
    WBC_MODES.map(function (item) { return item.id; })
  );
}

function wbcModeMeta(id) {
  var meta = WBC_MODES[1];
  for (var i = 0; i < WBC_MODES.length; i++) if (WBC_MODES[i].id === id) meta = WBC_MODES[i];
  return {
    id: meta.id,
    label: wbcT(meta.labelKey, meta.id),
    desc: wbcT(meta.descKey, ""),
  };
}

// ---- external Agent identity, capability and binding helpers ----------------
// The built-in Agent installation id mirrors the backend Agent Runtime
// (cyrene/agent_runtime/builtin.py). The composer treats it as the default.
var WBC_BUILTIN_AGENT_INSTALLATION = "agent_cyrene_builtin";
var WBC_BUILTIN_AGENT_ID = "cyrene";
var WBC_OPEN_AGENT_DETAIL_EVENT = "cyrene:open-agent-detail";

function wbcIsBuiltinAgent(agent) {
  agent = agent || {};
  return String(agent.installationId || "") === WBC_BUILTIN_AGENT_INSTALLATION
    || String(agent.agentId || "") === WBC_BUILTIN_AGENT_ID
    || !!agent.builtin;
}

// A capability snapshot exists when the chat was created with (or later
// received) an Agent binding and a probed/declared capabilities object.
// Legacy chats without one keep their historical full-surface behavior.
function wbcHasAgentCapabilitySnapshot(chat) {
  return !!(chat && chat.capabilities && typeof chat.capabilities === "object");
}

function wbcCapabilityStatus(chat, group, key) {
  var caps = chat && chat.capabilities;
  if (!caps || typeof caps !== "object") return "unknown";
  var section = caps[group];
  if (!section || typeof section !== "object") return "unknown";
  var value = section[key];
  if (value === true || value === "supported") return "supported";
  if (value === false || value === "unsupported") return "unsupported";
  if (value === "degraded") return "degraded";
  if (value === "agent_defined") return "agent_defined";
  return "unknown";
}

// Capability-driven composer gating. Legacy chats (no snapshot) always allow
// the current behavior. For Agent chats, unknown input/side-effect capabilities
// are treated as unsupported (handoff §13) via opts.strictUnknown.
function wbcCapabilityEnabled(chat, group, key, opts) {
  if (!wbcHasAgentCapabilitySnapshot(chat)) return true;
  var status = wbcCapabilityStatus(chat, group, key);
  if (status === "unsupported") return false;
  if (status === "unknown") return !(opts && opts.strictUnknown);
  return true;
}

function wbcChatAgent(chat) {
  return (chat && chat.agent && typeof chat.agent === "object") ? chat.agent : null;
}

function wbcAgentDisplayName(agent) {
  agent = agent || {};
  var name = String(agent.displayName || agent.name || "").trim();
  if (wbcIsBuiltinAgent(agent)) return name || "Cyrene";
  return name || String(agent.agentId || agent.installationId || "Agent");
}

// Availability states shown in the Composer Agent submenu. The backend's
// agent_card supplies installState / enabled / authState / runtimeState;
// conservative phase-1 defaults keep an unprobed Agent unselectable until its
// detail page has configured login and runtime.
function wbcAgentAvailability(agent) {
  agent = agent || {};
  if (wbcIsBuiltinAgent(agent)) return { state: "available", reasonKey: "" };
  if (agent.enabled === false) return { state: "disabled", reasonKey: "workbenchChat.agentState.disabled" };
  var installState = String(agent.installState || "");
  if (installState && installState !== "installed" && installState !== "upgrade_available") {
    return { state: "not_installed", reasonKey: "workbenchChat.agentState.notInstalled" };
  }
  var auth = String(agent.authState || "").toLowerCase();
  if (auth === "expired") return { state: "auth_required", reasonKey: "workbenchChat.agentState.authExpired" };
  if (auth === "failed") return { state: "auth_required", reasonKey: "workbenchChat.agentState.needsLogin" };
  var runtime = String(agent.runtimeState || "").toLowerCase();
  if (["error", "crashed", "failed"].indexOf(runtime) >= 0) {
    return { state: "not_started", reasonKey: "workbenchChat.agentState.notStarted" };
  }
  return { state: "available", reasonKey: "" };
}

function wbcAgentStateLabel(state) {
  var labels = {
    available: wbcT("workbenchChat.agentState.available", "Available"),
    disabled: wbcT("workbenchChat.agentState.disabled", "Disabled"),
    not_installed: wbcT("workbenchChat.agentState.notInstalled", "Not installed"),
    auth_required: wbcT("workbenchChat.agentState.needsLogin", "Needs login / configuration"),
    not_started: wbcT("workbenchChat.agentState.notStarted", "Not started"),
    incompatible: wbcT("workbenchChat.agentState.incompatible", "Version incompatible"),
  };
  return labels[state] || String(state || "");
}

// One row of the Composer Agent submenu (handoff §8.2). Available Agents pick
// a draft binding; unavailable Agents open their extension detail; locked
// chats (first message already sent) render the row disabled with a lock note
// and can never silently re-bind.
function wbcComposerAgentRow(props) {
  var agent = props.agent || {};
  var availability = props.availability || wbcAgentAvailability(agent);
  var name = wbcAgentDisplayName(agent);
  var meta = [wbcDriverLabel(agent.driver), String(agent.version || "")].filter(Boolean).join(" · ");
  var stateLabel = availability.state === "available" ? "" : wbcAgentStateLabel(availability.state);
  return (
    <button
      key={props.key}
      type="button"
      className={"wbc-agent-menu-row"
        + (props.active ? " active" : "")
        + (props.locked ? " locked" : "")
        + (props.canPick ? "" : " unavailable state-" + String(availability.state || "unknown"))}
      disabled={!!props.locked}
      aria-disabled={props.canPick ? undefined : "true"}
      aria-label={[name, stateLabel, props.active ? wbcT("workbenchChat.agentCurrent", "Current Agent") : ""].filter(Boolean).join(" · ")}
      title={stateLabel || meta || undefined}
      onClick={function () {
        if (props.locked) return;
        if (props.canPick) { if (props.onPick) props.onPick(); }
        else if (props.onOpen) props.onOpen(agent);
      }}
    >
      <span className="wbc-agent-menu-dot" aria-hidden="true" />
      <span className="wbc-agent-menu-name">{name}</span>
      {meta ? <span className="wbc-agent-menu-meta">{meta}</span> : null}
      {stateLabel ? <span className="wbc-agent-menu-state">{stateLabel}</span> : null}
      {props.active ? <span className="wbc-popmenu-check">{WBC_ICONS.check}</span> : null}
    </button>
  );
}

function wbcDriverLabel(driver) {
  driver = String(driver || "").trim();
  if (!driver || driver === "cyrene_builtin") return "";
  if (driver === "acp_stdio") return "ACP · stdio";
  return wbcT("workbenchChat.agentDriver.unknown", "Other driver · {driver}", { driver: driver });
}

function wbcAgentConnectionLabel(chat) {
  var agent = wbcChatAgent(chat);
  if (!agent) return "";
  if (wbcIsBuiltinAgent(agent)) return wbcT("workbenchChat.connection.builtin", "Built-in · ready");
  var driver = wbcDriverLabel(agent.driver);
  var runtime = String(agent.runtimeState || agent.connectionState || "").toLowerCase();
  var stateLabel = "";
  if (runtime === "ready" || runtime === "connected" || runtime === "running") {
    stateLabel = wbcT("workbenchChat.connection.connected", "Connected");
  } else if (runtime === "error" || runtime === "crashed") {
    stateLabel = wbcT("workbenchChat.connection.failed", "Error");
  } else if (runtime) {
    stateLabel = wbcT("workbenchChat.agentState.unknownValue", "Unknown · {value}", { value: runtime });
  }
  return [driver, stateLabel].filter(Boolean).join(" · ") || wbcT("workbenchChat.connection.unknown", "Unknown");
}

function wbcModelAccessLabel(chat) {
  var access = chat && chat.modelAccess && typeof chat.modelAccess === "object" ? chat.modelAccess : null;
  if (!access) return "";
  if (String(access.mode || "") === "agent_managed") {
    return wbcT("workbenchChat.modelSource.agentManaged", "Agent-owned configuration");
  }
  return wbcT("workbenchChat.modelSource.cyrene", "Cyrene");
}

// Hide usage statistics for Agents that do not report token usage instead of
// painting fake zeros (handoff §9). Legacy chats without an Agent binding keep
// the historical always-visible summary.
function wbcUsageReported(usage) {
  usage = usage || {};
  return !!(
    Number(usage.prompt_tokens || 0)
    || Number(usage.completion_tokens || 0)
    || Number(usage.total_tokens || 0)
    || Number(usage.prompt_cache_hit_tokens || 0)
    || Number(usage.prompt_cache_miss_tokens || 0)
  );
}

// Slash commands are capability/command driven (handoff §13): when an Agent
// chat snapshot exists, only commands declared by the Agent are offered.
// The built-in Cyrene Agent (and legacy chats without a snapshot) keep the
// historical command list — external Agents never inherit Cyrene-only commands.
function wbcComposerSlashCommands(chat) {
  if (!wbcHasAgentCapabilitySnapshot(chat)) return null;
  if (wbcIsBuiltinAgent(wbcChatAgent(chat))) return null;
  var raw = chat && (
    (Array.isArray(chat.agentCommands) && chat.agentCommands)
    || (Array.isArray(chat.capabilities.commands) && chat.capabilities.commands)
    || (Array.isArray(chat.capabilities.slash) && chat.capabilities.slash)
  );
  if (!raw) return [];
  return raw.map(function (item) {
    if (typeof item === "string") return { id: item, label: item, description: "", inputHint: "" };
    var id = String(item && (item.id || item.name || item.command) || "");
    return {
      id: id,
      label: String(item && (item.label || item.title || item.name) || id),
      description: String(item && (item.description || item.help) || ""),
      inputHint: String(item && (item.inputHint || item.input_hint) || ""),
    };
  }).filter(function (item) { return !!item.id; });
}

function wbcDraftAgentBindingKey(projectId) {
  return "wbc-draft-agent-binding:" + String(projectId || "default");
}

function wbcSaveDraftAgentBinding(projectId, binding) {
  try {
    if (!binding) localStorage.removeItem(wbcDraftAgentBindingKey(projectId));
    else localStorage.setItem(wbcDraftAgentBindingKey(projectId), JSON.stringify(binding));
  } catch (e) {}
}

function wbcLoadDraftAgentBinding(projectId) {
  try {
    var raw = localStorage.getItem(wbcDraftAgentBindingKey(projectId));
    if (!raw) return null;
    var parsed = JSON.parse(raw);
    return parsed && parsed.agent && parsed.agent.installationId ? parsed : null;
  } catch (e) {
    return null;
  }
}

function wbcDefaultAgentBinding() {
  return {
    agent: { installationId: WBC_BUILTIN_AGENT_INSTALLATION },
    modelAccess: { mode: "cyrene_managed", profileId: "primary" },
  };
}

// Ask the Settings overlay to open the Extension Center on this Agent's
// installed detail. The overlay mounts only inside the Workbench shell, so a
// no-op here simply leaves the composer submenu's disabled row in place.
function wbcOpenAgentDetail(agent) {
  agent = agent || {};
  try {
    window.dispatchEvent(new CustomEvent(WBC_OPEN_AGENT_DETAIL_EVENT, {
      detail: {
        installationId: String(agent.installationId || ""),
        agentId: String(agent.agentId || ""),
        displayName: wbcAgentDisplayName(agent),
      },
    }));
  } catch (e) {}
}

// ---- file classification for the side viewer -------------------------------

var WBC_CODE_EXTS = ["py","js","ts","jsx","tsx","css","scss","json","yaml","yml","toml","xml","sql","sh","bash","rs","go","java","c","cc","cpp","h","hpp","rb","php","swift","kt","txt","csv","ini","cfg","conf","env","log","rst","properties","vue","svelte"];
var WBC_OFFICE_MAX_FILE_BYTES = 100 * 1024 * 1024;
var WBC_OFFICE_MAX_ZIP_ENTRIES = 4000;
var WBC_OFFICE_MAX_ZIP_ENTRY_BYTES = 32 * 1024 * 1024;
var WBC_OFFICE_MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024;
var WBC_OFFICE_RENDERER_LOADS = {};

function wbcOfficeAssetRevisionQuery() {
  try {
    var script = Array.from(document.scripts || []).find(function (item) {
      return String(item && item.src || "").indexOf("/compiled/workbench-chat.js") !== -1;
    });
    return script ? new URL(script.src, window.location.href).search : "";
  } catch (e) {
    return "";
  }
}

function wbcLoadOfficeRenderer(kind) {
  var isDocx = kind === "docx";
  var globalKey = isDocx ? "CyreneOfficeDocx" : "CyreneOfficePptx";
  var fileName = isDocx ? "docx-viewer.js" : "pptx-viewer.js";
  if (window[globalKey]) return Promise.resolve(window[globalKey]);
  if (WBC_OFFICE_RENDERER_LOADS[kind]) return WBC_OFFICE_RENDERER_LOADS[kind];
  WBC_OFFICE_RENDERER_LOADS[kind] = new Promise(function (resolve, reject) {
    var script = document.createElement("script");
    script.async = true;
    script.dataset.cyreneOfficeRenderer = kind;
    script.src = "/static/app/office/" + fileName + wbcOfficeAssetRevisionQuery();
    script.onload = function () {
      if (window[globalKey]) resolve(window[globalKey]);
      else reject(new Error("office_renderer_unavailable"));
    };
    script.onerror = function () { reject(new Error("office_renderer_unavailable")); };
    document.head.appendChild(script);
  }).catch(function (error) {
    delete WBC_OFFICE_RENDERER_LOADS[kind];
    throw error;
  });
  return WBC_OFFICE_RENDERER_LOADS[kind];
}

function wbcValidateOfficeArchive(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 22) throw new Error("office_invalid_archive");
  var view = new DataView(buffer);
  var minimum = Math.max(0, buffer.byteLength - 65557);
  var eocd = -1;
  for (var offset = buffer.byteLength - 22; offset >= minimum; offset -= 1) {
    if (
      view.getUint32(offset, true) === 0x06054b50
      && offset + 22 + view.getUint16(offset + 20, true) === buffer.byteLength
    ) { eocd = offset; break; }
  }
  if (eocd < 0) throw new Error("office_invalid_archive");
  var entryCount = view.getUint16(eocd + 10, true);
  var directorySize = view.getUint32(eocd + 12, true);
  var directoryOffset = view.getUint32(eocd + 16, true);
  if (entryCount === 0xffff || directorySize === 0xffffffff || directoryOffset === 0xffffffff) {
    throw new Error("office_archive_too_large");
  }
  if (entryCount > WBC_OFFICE_MAX_ZIP_ENTRIES || directoryOffset + directorySize > buffer.byteLength) {
    throw new Error("office_archive_too_large");
  }
  var cursor = directoryOffset;
  var totalBytes = 0;
  for (var index = 0; index < entryCount; index += 1) {
    if (cursor + 46 > buffer.byteLength || view.getUint32(cursor, true) !== 0x02014b50) {
      throw new Error("office_invalid_archive");
    }
    var uncompressedBytes = view.getUint32(cursor + 24, true);
    var fileNameLength = view.getUint16(cursor + 28, true);
    var extraLength = view.getUint16(cursor + 30, true);
    var commentLength = view.getUint16(cursor + 32, true);
    if (uncompressedBytes === 0xffffffff || uncompressedBytes > WBC_OFFICE_MAX_ZIP_ENTRY_BYTES) {
      throw new Error("office_archive_too_large");
    }
    totalBytes += uncompressedBytes;
    if (totalBytes > WBC_OFFICE_MAX_ZIP_TOTAL_BYTES) throw new Error("office_archive_too_large");
    cursor += 46 + fileNameLength + extraLength + commentLength;
    if (cursor > directoryOffset + directorySize || cursor > buffer.byteLength) {
      throw new Error("office_invalid_archive");
    }
  }
}

function wbcHardenOfficeLinks(container) {
  if (!container || !container.querySelectorAll) return;
  container.querySelectorAll("a[href]").forEach(function (link) {
    var raw = String(link.getAttribute("href") || "").trim();
    if (!/^(https?:|mailto:)/i.test(raw)) {
      link.removeAttribute("href");
      return;
    }
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
  });
}

function wbcFileViewKind(file) {
  if (!file) return "";
  var ct = String(file.content_type || file.contentType || file.mime_type || file.mimeType || "").split(";", 1)[0].trim().toLowerCase();
  var fileLabel = String(file.name || file.filename || file.path || file.url || "").split(/[?#]/, 1)[0];
  var ext = fileLabel.indexOf(".") >= 0 ? fileLabel.split(".").pop().toLowerCase() : "";
  if (ct.indexOf("image/") === 0 || file.kind === "image" || ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"].indexOf(ext) !== -1) return "image";
  if (ct.indexOf("audio/") === 0 || file.kind === "audio") return "audio";
  if (ct.indexOf("video/") === 0 || file.kind === "video") return "video";
  if (ct === "application/pdf" || ext === "pdf" || file.kind === "pdf") return "pdf";
  if (ext === "docx" || ct === "application/vnd.openxmlformats-officedocument.wordprocessingml.document") return "docx";
  if (ext === "pptx" || ct === "application/vnd.openxmlformats-officedocument.presentationml.presentation") return "pptx";
  if (ct === "text/html" || ct === "application/xhtml+xml" || ext === "html" || ext === "htm") return "html";
  if (file.kind === "markdown" || ext === "md" || ext === "mdx" || ext === "markdown") return "markdown";
  if (file.kind === "code" || WBC_CODE_EXTS.indexOf(ext) !== -1 || ct.indexOf("text/") === 0) return "code";
  return "download";
}

function wbcAttachmentVisualKind(file) {
  var viewKind = wbcFileViewKind(file);
  var ext = String(file && (file.name || file.filename) || "").split(".").pop().toLowerCase();
  // The library groups searchable text formats under "document".  That is a
  // useful filter category, but it is too broad for attachment labels: mapping
  // it directly to "doc" makes Markdown and source files look like Word files.
  if (viewKind === "markdown") return "markdown";
  if (viewKind === "code" || viewKind === "html") {
    if (ext === "txt" || ext === "log") return "note";
    // Let the shared classifier keep tabular and office files in their native
    // categories even when an upload reports the generic text/plain MIME type.
    if (!/^(csv|tsv|doc|docx|odt|rtf|xls|xlsm|xlsx|odp|ppt|pptx)$/.test(ext)) return "code";
  }
  var shared = window.CyreneUI.require("library").FileVisual;
  if (shared && typeof shared.visualKind === "function") return shared.visualKind(file);
  return viewKind === "image" ? "image" : (viewKind || "file");
}

function wbcAttachmentVisual(file) {
  var shared = window.CyreneUI.require("library").FileVisual;
  if (shared && typeof shared.icon === "function") {
    var kind = wbcAttachmentVisualKind(file);
    return {
      kind: kind,
      tone: typeof shared.toneForKind === "function"
        ? shared.toneForKind(kind)
        : (typeof shared.tone === "function" ? shared.tone(file) : "slate"),
      icon: typeof shared.iconForKind === "function" ? shared.iconForKind(kind) : shared.icon(file),
    };
  }
  return { kind: wbcAttachmentVisualKind(file), tone: "slate", icon: WBC_ICONS.file };
}

function wbcAttachmentTypeLabel(file) {
  var kind = wbcAttachmentVisualKind(file);
  var fallbacks = {
    image: "Image",
    audio: "Audio",
    video: "Video",
    pdf: "PDF document",
    doc: "Word document",
    sheet: "Spreadsheet",
    slide: "Presentation",
    markdown: "Markdown",
    link: "Link",
    code: "Code file",
    map: "Map data",
    note: "Text file",
    file: "File",
  };
  return wbcT("workbenchChat.attachmentType." + kind, fallbacks[kind] || fallbacks.file);
}

export { WBC_AGENT_CHAT_FLOW_EVENT, WBC_BUILTIN_AGENT_ID, WBC_BUILTIN_AGENT_INSTALLATION, WBC_CHAT_MODEL_CHANGED_EVENT, WBC_COMMANDS, WBC_COMMAND_ICONS, WBC_ICONS, WBC_MODES, WBC_OFFICE_MAX_FILE_BYTES, WBC_SIDE_TAB_ICONS, WORKBENCH_BUDGET_CODES, WbVoiceCommand, WbcSplitPickerMenu, WbcVoice, WorkbenchChatModel, useWbcCallback, useWbcEffect, useWbcLayoutEffect, useWbcMemo, useWbcRef, useWbcState, wbcAgentAvailability, wbcAgentChatFlowSnapshot, wbcAgentColor, wbcAgentConnectionLabel, wbcAgentDisplayName, wbcAgentErrorPresentation, wbcAgentEventPayload, wbcAgentInitials, wbcAgentSessionPayload, wbcAttachmentTypeLabel, wbcAttachmentVisual, wbcBrowserAvoidancePlan, wbcBrowserFullscreenStatusText, wbcBrowserPageTitle, wbcBrowserTabPickerPayload, wbcBrowserTabPickerToggleIsDebounced, wbcBrowserWindowTitle, wbcBuildRailCardDragPreview, wbcCanOpenPageContextMenu, wbcCapabilityEnabled, wbcCapabilityStatus, wbcCaptureConversationViewport, wbcChatAgent, wbcChatCache, wbcChatDropReplacesActiveConversation, wbcChatSideDropZone, wbcChatSideZoneRect, wbcClampBrowserWindowFrame, wbcClampSideSplitWidth, wbcClampSideSplitWidthForPage, wbcClearModelOutputForRetry, wbcClonePaneWithLiveState, wbcCompactNumber, wbcComposerAgentRow, wbcComposerSlashCommands, wbcConfirmOptimisticMessage, wbcConversationTabAtPoint, wbcCreateComposerVoiceFeedback, wbcCreateDetachedRuntime, wbcCurrentModel, wbcCycleTopbarSessionTab, wbcDefaultAgentBinding, wbcDefaultPaneLayout, wbcDurableTracePayload, wbcErrorText, wbcEscapeHtml, wbcFileDragPayload, wbcFileViewKind, wbcFinalizeRuntime, wbcFormatProcessingDuration, wbcFormatTime, wbcFriendlyModelName, wbcHandleHorizontalWheelGesture, wbcHardenOfficeLinks, wbcHasAgentCapabilitySnapshot, wbcHasChatDrag, wbcHasChatRailDrag, wbcHasResourceDrag, wbcHasSplitDrag, wbcHasTaskDrag, wbcHideNativeDragImage, wbcHighlightMentions, wbcIsBuiltinAgent, wbcKeepBrowserWindowClearOfComposer, wbcLastChatByProject, wbcLoadBrowserWindowFrame, wbcLoadDraftAgentBinding, wbcLoadOfficeRenderer, wbcLocalizedModelDescription, wbcMergeChronologicalMessages, wbcMergeSavedAssistantMessages, wbcMergeToolOccurrence, wbcModeMeta, wbcModelAccessLabel, wbcModelContextLimit, wbcNormalizePaneLayout, wbcNormalizePermissionMode, wbcNotifyAgentChatFlow, wbcNotifyBrowserLayoutChanged, wbcNotifyBrowserWindowInteraction, wbcNotifyResourceShelfPointerDrag, wbcOpenAgentDetail, wbcPageContextMenuPlacement, wbcPaneCard, wbcPaneCardLocation, wbcPersistDurableTrace, wbcPinPageSplitLayout, wbcPinSplitMotionOpen, wbcPlacePaneCard, wbcPointInsideResourceShelf, wbcPreserveLiveTimelineAnchors, wbcPublishChatModelChanged, wbcRandomThinkingPhrase, wbcReadChatDrag, wbcReadResourceDrag, wbcReadSplitDrag, wbcReadTaskDrag, wbcReasoningEffortForModel, wbcReconcileLiveUserMessages, wbcReduceDetachedRuntime, wbcReleasePinnedPageSplitLayout, wbcReleasePinnedSplitMotion, wbcRenderMapMarkdown, wbcRenderMarkdown, wbcRestoreConversationViewport, wbcRetryTurnSelection, wbcRuntimeSegmentMessages, wbcRuntimeTimelineMessages, wbcSaveBrowserWindowFrame, wbcSaveDraftAgentBinding, wbcSetChatDrag, wbcSetChatGroupDrag, wbcSetResourceDrag, wbcSetSplitDrag, wbcSetTaskDrag, wbcSplitSideForDraggedConversation, wbcStartVoiceRecorder, wbcStructuredEventSummary, wbcSubagentStatusClass, wbcSubagentStatusText, wbcSupportedReasoningEfforts, wbcT, wbcToolArgsPreview, wbcToolPresentationKind, wbcToolPresentationText, wbcToolPreviewText, wbcTraceDedupeKey, wbcTranscribeVoiceBlob, wbcUsageReported, wbcValidateOfficeArchive, wbcWorkspaceDisplayName }
