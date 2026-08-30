import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WBC_AGENT_CHAT_FLOW_EVENT, WBC_ICONS, WorkbenchChatModel, useWbcEffect, useWbcLayoutEffect, useWbcMemo, useWbcRef, useWbcState, wbcBuildRailCardDragPreview, wbcErrorText, wbcFileViewKind, wbcFormatTime, wbcHasChatDrag, wbcHasChatRailDrag, wbcHideNativeDragImage, wbcNotifyAgentChatFlow, wbcSetChatDrag, wbcSetChatGroupDrag, wbcSetResourceDrag, wbcT } from "../../workbench-chat.jsx"
import { wbcPermissionOptionLabel, wbcPermissionQuestionText, wbcQuestionOptionValue } from "./conversation.jsx"
import { wbcStartFileDrag } from "./file-resources.jsx"

import { moveChatOrderBlock } from "./behavior.mjs"

// Workbench chat rail and project navigation.
function wbcOrderChatsByPinned(chats, pinnedChatIds) {
  var list = Array.isArray(chats) ? chats : [];
  var pinnedOrder = {};
  (Array.isArray(pinnedChatIds) ? pinnedChatIds : []).forEach(function (id, index) {
    pinnedOrder[String(id || "")] = index;
  });
  return list.map(function (chat, index) {
    return { chat: chat, index: index };
  }).sort(function (left, right) {
    var leftId = String(left.chat && left.chat.id || "");
    var rightId = String(right.chat && right.chat.id || "");
    var leftPinned = Object.prototype.hasOwnProperty.call(pinnedOrder, leftId);
    var rightPinned = Object.prototype.hasOwnProperty.call(pinnedOrder, rightId);
    if (leftPinned !== rightPinned) return leftPinned ? -1 : 1;
    if (leftPinned && pinnedOrder[leftId] !== pinnedOrder[rightId]) {
      return pinnedOrder[leftId] - pinnedOrder[rightId];
    }
    return left.index - right.index;
  }).map(function (entry) {
    return entry.chat;
  });
}

function WbcHoverMarquee({ text, className, auto }) {
  var viewportRef = useWbcRef(null);
  var trackRef = useWbcRef(null);
  var [metrics, setMetrics] = useWbcState({ overflow: false, distance: 0, duration: 7 });
  var value = String(text || "");

  useWbcEffect(function () {
    function measure() {
      var viewport = viewportRef.current;
      var track = trackRef.current;
      if (!viewport || !track) return;
      var distance = Math.max(0, Math.ceil(track.scrollWidth - viewport.clientWidth));
      var next = {
        overflow: distance > 1,
        distance: distance,
        duration: Math.max(7, Math.min(18, 5.5 + (distance / 45))),
      };
      setMetrics(function (current) {
        return current.overflow === next.overflow
          && current.distance === next.distance
          && current.duration === next.duration
          ? current
          : next;
      });
    }
    measure();
    var observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    if (observer && viewportRef.current) observer.observe(viewportRef.current);
    if (observer && trackRef.current) observer.observe(trackRef.current);
    window.addEventListener("resize", measure);
    return function () {
      if (observer) observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [value]);

  return (
    <span
      ref={viewportRef}
      className={"wbc-hover-marquee" + (metrics.overflow ? " overflow" : "") + (auto ? " auto" : "") + (className ? (" " + className) : "")}
      title={metrics.overflow ? value : undefined}
    >
      <span
        ref={trackRef}
        className="wbc-hover-marquee-track"
        style={{
          "--wbc-marquee-distance": metrics.distance + "px",
          "--wbc-marquee-duration": metrics.duration + "s",
        }}
      >{value}</span>
    </span>
  );
}

var WBC_CHAT_ORDER_PREFIX = "cyrene-workbench-chat-order-v1:";
var WBC_CHAT_GROUPS_PREFIX = "cyrene-workbench-chat-groups-v1:";

function wbcNormalizeChatOrder(defaultOrder, savedOrder) {
  var valid = Array.isArray(defaultOrder) ? defaultOrder.map(String) : [];
  var allowed = new Set(valid);
  var seen = new Set();
  var saved = [];
  (Array.isArray(savedOrder) ? savedOrder : []).forEach(function (id) {
    id = String(id);
    if (!allowed.has(id) || seen.has(id)) return;
    seen.add(id);
    saved.push(id);
  });
  var missing = valid.filter(function (id) { return !seen.has(id); });
  return missing.concat(saved);
}

function wbcLoadChatOrder(projectId, defaultOrder) {
  try {
    var saved = JSON.parse(localStorage.getItem(WBC_CHAT_ORDER_PREFIX + String(projectId || "")) || "null");
    return wbcNormalizeChatOrder(defaultOrder, saved);
  } catch (e) {
    return wbcNormalizeChatOrder(defaultOrder, null);
  }
}

function wbcMoveChatOrder(order, movingId, targetId, edge) {
  var current = Array.isArray(order) ? order.slice() : [];
  movingId = String(movingId || "");
  targetId = String(targetId || "");
  if (!movingId || movingId === targetId || current.indexOf(movingId) < 0 || current.indexOf(targetId) < 0) {
    return current;
  }
  var next = current.filter(function (id) { return id !== movingId; });
  var targetIndex = next.indexOf(targetId);
  next.splice(targetIndex + (edge === "after" ? 1 : 0), 0, movingId);
  return next;
}

function wbcMoveChatOrderBlock(order, movingIds, targetIds, edge) {
  return moveChatOrderBlock(order, movingIds, targetIds, edge);
}

function wbcNormalizeChatGroups(groups, validChatIds) {
  var allowed = new Set((Array.isArray(validChatIds) ? validChatIds : []).map(String));
  var claimed = new Set();
  return (Array.isArray(groups) ? groups : []).map(function (raw, index) {
    var chatIds = [];
    var localSeen = new Set();
    (raw && Array.isArray(raw.chatIds) ? raw.chatIds : []).forEach(function (id) {
      id = String(id || "");
      if (!allowed.has(id) || claimed.has(id) || localSeen.has(id)) return;
      localSeen.add(id);
      chatIds.push(id);
    });
    if (chatIds.length >= 2) chatIds.forEach(function (id) { claimed.add(id); });
    return {
      id: String(raw && raw.id || ("group_" + index)),
      title: String(raw && raw.title || wbcT("workbenchChat.newGroup", "New chat group")).trim().slice(0, 60)
        || wbcT("workbenchChat.newGroup", "New chat group"),
      summary: String(raw && raw.summary || "").trim().slice(0, 160),
      titleLocked: !!(raw && raw.titleLocked),
      metadataLang: String(raw && raw.metadataLang || ""),
      metadataChatIds: String(raw && raw.metadataChatIds || ""),
      chatIds: chatIds,
    };
  }).filter(function (group) { return group.chatIds.length >= 2; });
}

function wbcLoadChatGroups(projectId, validChatIds) {
  try {
    var saved = JSON.parse(localStorage.getItem(WBC_CHAT_GROUPS_PREFIX + String(projectId || "")) || "null");
    return wbcNormalizeChatGroups(saved, validChatIds);
  } catch (e) {
    return [];
  }
}

function wbcFindChatGroup(groups, chatId) {
  chatId = String(chatId || "");
  return (Array.isArray(groups) ? groups : []).find(function (group) {
    return Array.isArray(group.chatIds) && group.chatIds.indexOf(chatId) >= 0;
  }) || null;
}

function wbcRemoveChatFromGroups(groups, chatId) {
  chatId = String(chatId || "");
  return (Array.isArray(groups) ? groups : []).map(function (group) {
    return {
      ...group,
      chatIds: (Array.isArray(group.chatIds) ? group.chatIds : []).filter(function (id) {
        return String(id) !== chatId;
      }),
    };
  }).filter(function (group) { return group.chatIds.length >= 2; });
}

function wbcCreateChatGroup(groups, movingId, targetId, nextGroupId) {
  movingId = String(movingId || "");
  targetId = String(targetId || "");
  var current = (Array.isArray(groups) ? groups : []).map(function (group) {
    return { ...group, chatIds: Array.isArray(group.chatIds) ? group.chatIds.slice() : [] };
  });
  if (!movingId || !targetId || movingId === targetId) return current;
  var existingTargetGroup = wbcFindChatGroup(current, targetId);
  if (existingTargetGroup && existingTargetGroup.chatIds.indexOf(movingId) >= 0) return current;

  current.forEach(function (group) {
    group.chatIds = group.chatIds.filter(function (id) { return String(id) !== movingId; });
  });
  current = current.filter(function (group) { return group.chatIds.length >= 2; });
  existingTargetGroup = wbcFindChatGroup(current, targetId);
  if (existingTargetGroup) {
    existingTargetGroup.chatIds.push(movingId);
    return current;
  }
  current.push({
    id: String(nextGroupId || ("group_" + Date.now().toString(36))),
    title: wbcT("workbenchChat.newGroup", "New chat group"),
    summary: "",
    titleLocked: false,
    metadataLang: "",
    metadataChatIds: "",
    chatIds: [targetId, movingId],
  });
  return current;
}

function wbcBuildChatRailItems(chats, groups) {
  var list = Array.isArray(chats) ? chats : [];
  var renderedGroups = new Set();
  var visibleIds = new Set(list.map(function (chat) { return String(chat && chat.id || ""); }));
  var items = [];
  list.forEach(function (chat) {
    var group = wbcFindChatGroup(groups, chat && chat.id);
    if (!group) {
      items.push({ kind: "chat", chat: chat });
      return;
    }
    if (renderedGroups.has(group.id)) return;
    renderedGroups.add(group.id);
    items.push({
      kind: "group",
      group: group,
      chats: list.filter(function (candidate) {
        return visibleIds.has(String(candidate && candidate.id || ""))
          && group.chatIds.indexOf(String(candidate && candidate.id || "")) >= 0;
      }),
    });
  });
  return items;
}

function wbcConversationTrackRawStatus(chat) {
  return String(chat && (chat.runStatus || chat.status) || "").trim().toLowerCase();
}

function wbcConversationTrackIsRunning(chat, runningChatIds) {
  var raw = wbcConversationTrackRawStatus(chat);
  return !!(chat && runningChatIds && runningChatIds[chat.id])
    || ["running", "resumed", "planning", "initializing", "finishing"].indexOf(raw) >= 0;
}

function wbcConversationTrackIsCompleted(chat) {
  return ["completed", "complete", "done", "success", "succeeded"].indexOf(
    wbcConversationTrackRawStatus(chat)
  ) >= 0;
}

function wbcConversationTrackState(chat, runningChatIds, newResultChatIds) {
  if (!chat || !chat.id) return null;
  var raw = wbcConversationTrackRawStatus(chat);
  if (wbcConversationTrackIsRunning(chat, runningChatIds)) {
    return { kind: "running", urgent: false, label: wbcT("workbenchChat.track.running", "Agent running") };
  }
  if (
    !!chat.failed
    || !!chat.error
    || ["error", "failed", "failure", "timeout"].indexOf(raw) >= 0
  ) return { kind: "failed", urgent: true, label: wbcT("workbenchChat.track.failed", "Run failed") };
  if (
    !!chat.awaitingUser
    || !!chat.pendingQuestion
    || [
      "awaiting_user", "waiting_for_user", "waiting_for_approval", "needs_input",
      "waiting_input", "requires_confirmation", "blocked", "review",
    ].indexOf(raw) >= 0
  ) return { kind: "attention", urgent: true, label: wbcT("workbenchChat.track.attention", "Needs your attention") };
  if (newResultChatIds && newResultChatIds[chat.id]) {
    return { kind: "result", urgent: true, label: wbcT("workbenchChat.track.result", "New result") };
  }
  return null;
}

function wbcConversationTrackPositions(items, totalCount, measuredGeometry) {
  var list = Array.isArray(items) ? items.map(function (item) { return { ...item }; }) : [];
  if (!list.length) return list;
  if (Number(totalCount) <= 1) return list.map(function (item) {
    var chatId = String(item.chat && item.chat.id || "");
    var geometry = measuredGeometry && measuredGeometry[chatId];
    var measured = Number(geometry && typeof geometry === "object" ? geometry.position : geometry);
    var position = Number.isFinite(measured) ? Math.max(6, Math.min(94, measured)) : 50;
    var expandedPosition = Number.isFinite(measured) ? measured : 50;
    var trackHeight = Number(geometry && geometry.trackHeight);
    return {
      ...item,
      position: position,
      expandedPosition: expandedPosition,
      expandedX: Number(geometry && geometry.expandedX),
      collapseY: Number.isFinite(trackHeight) ? ((position - expandedPosition) / 100) * trackHeight : 0,
      measured: Number.isFinite(measured),
    };
  });
  list.forEach(function (item) {
    var chatId = String(item.chat && item.chat.id || "");
    var geometry = measuredGeometry && measuredGeometry[chatId];
    var measured = Number(geometry && typeof geometry === "object" ? geometry.position : geometry);
    item.position = Number.isFinite(measured)
      ? Math.max(6, Math.min(94, measured))
      : 6 + (Number(item.index) / Math.max(1, Number(totalCount) - 1)) * 88;
    item.expandedPosition = Number.isFinite(measured) ? measured : item.position;
    item.expandedX = Number(geometry && geometry.expandedX);
    item.trackHeight = Number(geometry && geometry.trackHeight);
    item.measured = Number.isFinite(measured);
  });
  var minimumGap = list.length > 1 ? Math.min(7, 88 / (list.length - 1)) : 0;
  for (var index = 1; index < list.length; index += 1) {
    list[index].position = Math.max(list[index].position, list[index - 1].position + minimumGap);
  }
  var overflow = list[list.length - 1].position - 94;
  if (overflow > 0) {
    list.forEach(function (item) { item.position -= overflow; });
  }
  for (var reverse = list.length - 2; reverse >= 0; reverse -= 1) {
    list[reverse].position = Math.min(list[reverse].position, list[reverse + 1].position - minimumGap);
  }
  if (list[0].position < 6) {
    var underflow = 6 - list[0].position;
    list.forEach(function (item) { item.position += underflow; });
  }
  list.forEach(function (item) {
    item.collapseY = Number.isFinite(item.trackHeight)
      ? ((item.position - item.expandedPosition) / 100) * item.trackHeight
      : 0;
  });
  return list;
}

function wbcConversationTrackRuntimeText(runtime, chat) {
  var progress = runtime && Array.isArray(runtime.progress) ? runtime.progress : [];
  for (var index = progress.length - 1; index >= 0; index -= 1) {
    var entry = progress[index];
    if (!entry) continue;
    if (entry.status === "running" && (entry.text || entry.preview)) {
      return String(entry.preview || entry.text || "").trim();
    }
    if (entry.kind === "phase" && (entry.text || entry.detailKey)) {
      return String(entry.text || entry.detailKey || "").trim();
    }
  }
  return String(chat && chat.preview || "").trim();
}

function WbcConversationStatusPreview({ chat, state, runtime, busy, result, error, onAnswer }) {
  var pending = chat && chat.pendingQuestion || null;
  var options = pending && Array.isArray(pending.options) ? pending.options : [];
  var kind = String(pending && pending.kind || "");
  var isPermission = kind === "permission.requested"
    || workbenchServices.model().isPermissionQuestionKind(kind);
  var actionOptions = options.length
    ? options.map(function (option, index) {
      return {
        value: wbcQuestionOptionValue(option),
        label: isPermission
          ? wbcPermissionOptionLabel(option, index, options.length)
          : wbcQuestionOptionValue(option),
      };
    })
    : [];
  var customState = useWbcState("");
  var customText = customState[0], setCustomText = customState[1];
  useWbcEffect(function () { setCustomText(""); }, [chat && chat.id, pending && pending.id]);
  function submit(answer, resumeMode) {
    var text = String(answer || "").trim();
    if (!text || busy || !pending || !onAnswer) return;
    onAnswer(pending.id, text, resumeMode);
  }
  var detail = state.kind === "running"
    ? (wbcConversationTrackRuntimeText(runtime, chat) || wbcT("workbenchChat.track.runningDetail", "Agent is working in the background."))
    : state.kind === "attention"
      ? String(isPermission
        ? wbcPermissionQuestionText(pending)
        : (pending && pending.text || wbcT("workbenchChat.track.attentionDetail", "Open this conversation or respond here to continue.")))
      : state.kind === "result"
        ? String(chat && chat.preview || wbcT("workbenchChat.track.resultDetail", "A background reply is ready."))
        : wbcT("workbenchChat.track.failedDetail", "The latest run stopped with an error. Open the conversation for details.");
  return (
    <div
      className={"wbc-conversation-status-preview is-" + state.kind}
      role="dialog"
      aria-label={state.label + " · " + (chat.title || wbcT("workbenchChat.newChat", "New chat"))}
      onClick={function (event) { event.stopPropagation(); }}
      onPointerDown={function (event) { event.stopPropagation(); }}
    >
      <span className="wbc-conversation-status-preview-bridge" aria-hidden="true"></span>
      <header className="wbc-conversation-status-preview-head">
        <span className="wbc-conversation-status-preview-dot" aria-hidden="true"></span>
        <b>{state.label}</b>
      </header>
      <strong className="wbc-conversation-status-preview-title">{chat.title || wbcT("workbenchChat.newChat", "New chat")}</strong>
      <p>{detail}</p>
      {state.kind === "attention" && pending && (
        <div className="wbc-conversation-status-preview-actions">
          {actionOptions.map(function (option, index) {
            var resumeMode = kind === "plan_confirmation" && index === 0 ? "auto" : undefined;
            return (
              <button
                key={index}
                type="button"
                className={"wbc-conversation-status-preview-option" + (index === 0 ? " primary" : "")}
                disabled={busy}
                onClick={function () { submit(option.value, resumeMode); }}
              >{option.label}</button>
            );
          })}
          {pending.allowCustom && (
            <div className="wbc-conversation-status-preview-reply">
              <input
                type="text"
                value={customText}
                disabled={busy}
                placeholder={wbcT("workbenchChat.customAnswer", "Or enter a custom reply...")}
                onChange={function (event) { setCustomText(event.target.value); }}
                onKeyDown={function (event) {
                  if (event.key !== "Enter") return;
                  event.preventDefault();
                  submit(customText);
                }}
              />
              <button type="button" disabled={busy || !String(customText).trim()} onClick={function () { submit(customText); }} aria-label={wbcT("workbenchChat.sendReply", "Send reply")}>{WBC_ICONS.send}</button>
            </div>
          )}
        </div>
      )}
      {busy && <span className="wbc-conversation-status-preview-feedback">{wbcT("workbenchChat.track.sending", "Sending…")}</span>}
      {!busy && result && <span className="wbc-conversation-status-preview-feedback success">{wbcT("workbenchChat.track.sent", "Sent")}</span>}
      {!busy && error && <span className="wbc-conversation-status-preview-feedback error" role="alert">{error}</span>}
    </div>
  );
}

function wbcViewportChatIds(list) {
  if (!list || typeof list.getBoundingClientRect !== "function" || typeof list.querySelectorAll !== "function") return [];
  var viewport = list.getBoundingClientRect();
  var viewportRight = Number.isFinite(Number(viewport.right)) ? Number(viewport.right) : Number(viewport.left || 0) + Number(viewport.width || 0);
  var viewportBottom = Number.isFinite(Number(viewport.bottom)) ? Number(viewport.bottom) : Number(viewport.top || 0) + Number(viewport.height || 0);
  var seen = new Set();
  var result = [];
  list.querySelectorAll(".wbc-chat-card[data-chat-id]").forEach(function (card) {
    if (card.hidden || card.getAttribute("aria-hidden") === "true") return;
    var hiddenLayer = card.closest && card.closest('[aria-hidden="true"], [inert]');
    if (hiddenLayer) return;
    var rect = card.getBoundingClientRect();
    var right = Number.isFinite(Number(rect.right)) ? Number(rect.right) : Number(rect.left || 0) + Number(rect.width || 0);
    var bottom = Number.isFinite(Number(rect.bottom)) ? Number(rect.bottom) : Number(rect.top || 0) + Number(rect.height || 0);
    if (
      Number(rect.width || 0) <= 0
      || Number(rect.height || 0) <= 0
      || right <= Number(viewport.left || 0)
      || Number(rect.left || 0) >= viewportRight
      || bottom <= Number(viewport.top || 0)
      || Number(rect.top || 0) >= viewportBottom
    ) return;
    var chatId = String(card.getAttribute("data-chat-id") || "");
    if (!chatId || seen.has(chatId)) return;
    seen.add(chatId);
    result.push(chatId);
  });
  return result;
}

function wbcProjectFileVisual(entry) {
  if (entry && entry.kind === "directory") return { kind: "folder", icon: WBC_ICONS.folder };
  var name = String(entry && entry.name || "").toLowerCase();
  var extension = name.indexOf(".") >= 0 ? name.split(".").pop() : "";
  if (["js", "jsx", "ts", "tsx", "py", "kt", "java", "go", "rs", "swift", "c", "cc", "cpp", "h", "hpp", "css", "scss", "html", "vue", "svelte", "sh"].indexOf(extension) >= 0) return { kind: "code", icon: WBC_ICONS.slash };
  if (["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"].indexOf(extension) >= 0) return { kind: "image", icon: WBC_ICONS.image };
  if (extension === "pdf") return { kind: "pdf", icon: WBC_ICONS.pdf };
  if (["md", "mdx", "txt", "rst"].indexOf(extension) >= 0) return { kind: "document", icon: WBC_ICONS.fileText };
  if (["zip", "tar", "gz", "tgz", "rar", "7z"].indexOf(extension) >= 0) return { kind: "archive", icon: WBC_ICONS.layers };
  if (["json", "yaml", "yml", "toml", "xml", "ini", "env", "lock"].indexOf(extension) >= 0 || name === "dockerfile") return { kind: "config", icon: WBC_ICONS.model };
  return { kind: "file", icon: WBC_ICONS.file };
}

function wbcProjectFileResource(projectId, entry) {
  if (!entry || entry.kind !== "file" || !projectId) return null;
  var path = String(entry.path || entry.name || "").replace(/\\/g, "/");
  var encodedPath = path.split("/").filter(Boolean).map(encodeURIComponent).join("/");
  if (!encodedPath) return null;
  return {
    name: String(entry.name || "file"),
    path: path,
    size: Number(entry.size || 0),
    modifiedNs: Number(entry.modifiedNs || 0),
    content_type: String(entry.content_type || entry.contentType || ""),
    kind: wbcFileViewKind(entry) === "image" ? "image" : "file",
    source: "project",
    projectId: String(projectId),
    url: "/api/projects/" + encodeURIComponent(projectId) + "/files/content/" + encodedPath,
  };
}


export { WBC_CHAT_GROUPS_PREFIX, WBC_CHAT_ORDER_PREFIX, WbcConversationStatusPreview, WbcHoverMarquee, wbcBuildChatRailItems, wbcConversationTrackIsCompleted, wbcConversationTrackIsRunning, wbcConversationTrackPositions, wbcConversationTrackState, wbcConversationTrackRuntimeText, wbcCreateChatGroup, wbcFindChatGroup, wbcLoadChatGroups, wbcLoadChatOrder, wbcMoveChatOrder, wbcMoveChatOrderBlock, wbcNormalizeChatGroups, wbcNormalizeChatOrder, wbcOrderChatsByPinned, wbcProjectFileResource, wbcProjectFileVisual, wbcRemoveChatFromGroups, wbcViewportChatIds }
