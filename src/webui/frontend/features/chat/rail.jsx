import { WBC_AGENT_CHAT_FLOW_EVENT, WBC_ICONS, WorkbenchChatModel, useWbcEffect, useWbcLayoutEffect, useWbcMemo, useWbcRef, useWbcState, wbcBuildRailCardDragPreview, wbcErrorText, wbcFileViewKind, wbcFormatTime, wbcHasChatDrag, wbcHasChatRailDrag, wbcHasTaskDrag, wbcHideNativeDragImage, wbcNotifyAgentChatFlow, wbcSetChatDrag, wbcSetChatGroupDrag, wbcSetResourceDrag, wbcSetTaskDrag, wbcT } from "../../workbench-chat.jsx"
import { wbcPermissionOptionLabel, wbcPermissionQuestionText, wbcQuestionOptionValue } from "./conversation.jsx"
import { wbcStartFileDrag } from "./file-resources.jsx"

import { moveChatOrderBlock } from "./behavior.mjs"

// Workbench chat rail and project navigation.
function WbcRenameDialog({ chat, onClose, onRename, entity }) {
  var [draft, setDraft] = useWbcState(chat ? chat.title || "" : "");
  var [saving, setSaving] = useWbcState(false);
  var [error, setError] = useWbcState("");
  var inputRef = useWbcRef(null);
  var originalTitle = String((chat && chat.title) || "");
  var nextTitle = String(draft || "").trim();
  var canSave = !!nextTitle && nextTitle !== originalTitle && !saving;
  var isGroup = entity === "group";
  var isTerminal = entity === "terminal";
  var isTask = entity === "task";

  useWbcEffect(function () {
    setDraft(originalTitle);
    setError("");
    setSaving(false);
    requestAnimationFrame(function () {
      if (inputRef.current) {
        inputRef.current.focus();
        inputRef.current.select();
      }
    });
  }, [chat && chat.id]);

  function close() {
    if (!saving && onClose) onClose();
  }

  function submit(e) {
    if (e) e.preventDefault();
    if (!canSave || !chat || !onRename) return;
    setSaving(true);
    setError("");
    onRename(chat.id, nextTitle).then(function () {
      window.CyreneUI.require("feedback").showToast(
        isGroup
          ? wbcT("workbenchChat.groupRenameSuccess", "Chat group renamed")
          : isTerminal
            ? wbcT("terminal.renameSuccess", "Terminal renamed")
            : isTask
              ? wbcT("task.renameSuccess", "Task renamed")
          : wbcT("workbenchChat.renameSuccess", "Chat renamed"),
        "success"
      );
      if (onClose) onClose();
    }).catch(function (err) {
      setError(wbcErrorText(err));
      setSaving(false);
    });
  }

  if (!chat) return null;
  return window.ReactDOM.createPortal(
    <div
      className="wbc-rename-scrim"
      onMouseDown={function (e) { if (e.target === e.currentTarget) close(); }}
      onKeyDown={function (e) { if (e.key === "Escape") close(); }}
    >
      <form
        className="wbc-rename-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="wbc-rename-title"
        onSubmit={submit}
      >
        <div className="wbc-rename-head">
          <strong id="wbc-rename-title">{isGroup
            ? wbcT("workbenchChat.groupRename", "Rename group")
            : isTerminal
              ? wbcT("terminal.rename", "Rename terminal")
              : isTask
                ? wbcT("task.rename", "Rename task")
            : wbcT("workbenchChat.rename", "Rename chat")}</strong>
          <button
            type="button"
            className="wbc-rename-close"
            aria-label={wbcT("common.close", "Close")}
            disabled={saving}
            onClick={close}
          >{WBC_ICONS.x}</button>
        </div>
        <div className="wbc-rename-body">
          <label htmlFor="wbc-rename-input">{isGroup
            ? wbcT("workbenchChat.groupTitleLabel", "Group title")
            : isTerminal
              ? wbcT("terminal.titleLabel", "Terminal title")
              : isTask
                ? wbcT("task.titleLabel", "Task title")
            : wbcT("workbenchChat.titleLabel", "Chat title")}</label>
          <input
            id="wbc-rename-input"
            ref={inputRef}
            value={draft}
            maxLength={60}
            disabled={saving}
            onChange={function (e) {
              setDraft(e.target.value);
              if (error) setError("");
            }}
            placeholder={isGroup
              ? wbcT("workbenchChat.groupRenamePlaceholder", "Enter a group title")
              : isTask
                ? wbcT("task.renamePlaceholder", "Enter a task title")
              : wbcT("workbenchChat.renamePlaceholder", "Enter a chat title")}
          />
          <div className="wbc-rename-meta">
            <span className={error ? "is-error" : ""} role={error ? "alert" : undefined}>
              {error || (!nextTitle ? wbcT("workbenchChat.renameRequired", "The title cannot be empty") : "")}
            </span>
            <span>{String(draft || "").length}/60</span>
          </div>
        </div>
        <div className="wbc-rename-foot">
          <button type="button" className="wb-btn" disabled={saving} onClick={close}>
            {wbcT("common.cancel", "Cancel")}
          </button>
          <button type="submit" className="wb-btn primary" data-cyrene-risk="R2" disabled={!canSave}>
            {saving ? wbcT("common.saving", "Saving...") : wbcT("common.save", "Save")}
          </button>
        </div>
      </form>
    </div>,
    document.querySelector(".workbench-shell") || document.body
  );
}

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

function WbcHoverMarquee({ text, className }) {
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
      className={"wbc-hover-marquee" + (metrics.overflow ? " overflow" : "") + (className ? (" " + className) : "")}
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
var WBC_TASK_ORDER_PREFIX = "cyrene-workbench-task-order-v1:";
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
    || window.CyreneUI.require("model").isPermissionQuestionKind(kind);
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

function WbcRail({ projectId, projectName, chats, tasks, terminals, terminalsLoading, activeTerminalId, railMode, workRailMode, pinnedChatIds, pinnedTaskIds, activeChatId, activeTaskId, loading, runningChatIds, runtimeEngine, onSelect, onSelectTask, onAnswer, onCreate, onCreateTask, onRename, onRenameTask, onDelete, onDeleteTask, onToTask, toTaskBusy, onTogglePinned, onTogglePinnedTask, onOpenFile, onOpenTerminal, onCreateTerminal, onRenameTerminal, onDeleteTerminal, onUpdateTerminalLayout, onRailModeChange, collapsed, onToggleCollapsed, collapseControl, moduleDock }) {
  var [query, setQuery] = useWbcState("");
  var [fileToolsExpanded, setFileToolsExpanded] = useWbcState(false);
  var [terminalToolsExpanded, setTerminalToolsExpanded] = useWbcState(false);
  var [fileLocation, setFileLocation] = useWbcState(function () {
    return { projectId: String(projectId || ""), path: "." };
  });
  var currentFileProjectId = String(projectId || "");
  var filePath = fileLocation.projectId === currentFileProjectId
    ? String(fileLocation.path || ".")
    : ".";
  function setFilePath(nextPath) {
    setFileLocation({
      projectId: currentFileProjectId,
      path: String(nextPath || "."),
    });
  }
  var [fileEntries, setFileEntries] = useWbcState([]);
  var [filesLoading, setFilesLoading] = useWbcState(false);
  var [filesError, setFilesError] = useWbcState("");
  var [hasLoadedFiles, setHasLoadedFiles] = useWbcState(false);
  var [globalFileEntries, setGlobalFileEntries] = useWbcState([]);
  var [globalFilesLoading, setGlobalFilesLoading] = useWbcState(false);
  var [globalFilesError, setGlobalFilesError] = useWbcState("");
  var [fileDirection, setFileDirection] = useWbcState("forward");
  var fileDirectionRef = useWbcRef("forward");
  var [showAllRecent, setShowAllRecent] = useWbcState(false);
  var [menuId, setMenuId] = useWbcState("");
  var [renameChat, setRenameChat] = useWbcState(null);
  var [renameTask, setRenameTask] = useWbcState(null);
  var [renameGroup, setRenameGroup] = useWbcState(null);
  var [renameTerminalItem, setRenameTerminalItem] = useWbcState(null);
  var terminalDefaultOrder = (Array.isArray(terminals) ? terminals : []).slice().sort(function (left, right) {
    return Number(left && left.orderIndex || 0) - Number(right && right.orderIndex || 0);
  }).map(function (terminal) { return String(terminal.id); });
  var [terminalOrder, setTerminalOrder] = useWbcState([]);
  var [terminalPinnedIds, setTerminalPinnedIds] = useWbcState([]);
  var [terminalDragId, setTerminalDragId] = useWbcState("");
  var taskDefaultOrder = (Array.isArray(tasks) ? tasks : []).map(function (task) { return String(task.id); });
  var taskDefaultOrderKey = taskDefaultOrder.join("|");
  var [taskOrder, setTaskOrder] = useWbcState(function () {
    try {
      return wbcNormalizeChatOrder(taskDefaultOrder, JSON.parse(localStorage.getItem(WBC_TASK_ORDER_PREFIX + String(projectId || "")) || "null"));
    } catch (e) {
      return wbcNormalizeChatOrder(taskDefaultOrder, null);
    }
  });
  var [taskDragId, setTaskDragId] = useWbcState("");
  var [collapsedGroups, setCollapsedGroups] = useWbcState({});
  var defaultChats = useWbcMemo(function () {
    return wbcOrderChatsByPinned(chats, pinnedChatIds);
  }, [chats, pinnedChatIds]);
  var defaultOrder = defaultChats.map(function (chat) { return String(chat.id); });
  var defaultOrderKey = defaultOrder.join("|");
  var [order, setOrder] = useWbcState(function () {
    return wbcLoadChatOrder(projectId, defaultOrder);
  });
  useWbcEffect(function () { orderRef.current = order; }, [order]);
  var [groups, setGroups] = useWbcState(function () {
    return wbcLoadChatGroups(projectId, defaultOrder);
  });
  var [groupBackendReady, setGroupBackendReady] = useWbcState(false);
  var [groupMetadataPending, setGroupMetadataPending] = useWbcState({});
  var [dragState, setDragState] = useWbcState(null);
  var [announcement, setAnnouncement] = useWbcState("");
  var [previewChatId, setPreviewChatId] = useWbcState("");
  var [newResultChatIds, setNewResultChatIds] = useWbcState({});
  var [previewAnswerState, setPreviewAnswerState] = useWbcState({ chatId: "", busy: false, result: "", error: "" });
  var [trackGeometryByChatId, setTrackGeometryByChatId] = useWbcState({});
  var [agentFlowByChatId, setAgentFlowByChatId] = useWbcState({});
  var [railMotionPhase, setRailMotionPhase] = useWbcState("");
  var [uiViewportRevision, setUiViewportRevision] = useWbcState(0);
  var normalizedQuery = query.trim().toLowerCase();
  var visibleFiles = normalizedQuery ? fileEntries.filter(function (entry) {
    return String(entry && entry.name || "").toLowerCase().indexOf(normalizedQuery) !== -1
      || String(entry && entry.path || "").toLowerCase().indexOf(normalizedQuery) !== -1;
  }) : fileEntries;
  useWbcEffect(function () {
    setQuery("");
    setFileToolsExpanded(false);
    setTerminalToolsExpanded(false);
    setFileEntries([]);
    setGlobalFileEntries([]);
    setGlobalFilesLoading(false);
    setGlobalFilesError("");
    setFilesError("");
    setHasLoadedFiles(false);
    setFileDirection("forward");
    fileDirectionRef.current = "forward";
  }, [projectId]);
  useWbcEffect(function () {
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(WBC_TASK_ORDER_PREFIX + String(projectId || "")) || "null"); } catch (e) {}
    setTaskOrder(wbcNormalizeChatOrder(taskDefaultOrder, saved));
    setTaskDragId("");
  }, [projectId, taskDefaultOrderKey]);
  useWbcEffect(function () {
    setTerminalOrder(terminalDefaultOrder);
    setTerminalPinnedIds((Array.isArray(terminals) ? terminals : []).filter(function (terminal) {
      return Boolean(terminal && terminal.pinned);
    }).map(function (terminal) { return String(terminal.id); }));
  }, [projectId, terminalDefaultOrder.join("|")]);
  useWbcEffect(function () {
    if (!fileToolsExpanded || !projectId) return undefined;
    var cancelled = false;
    setFilesLoading(true);
    setFilesError("");
    fetch("/api/projects/" + encodeURIComponent(projectId) + "/files?path=" + encodeURIComponent(filePath), { cache: "no-store" })
      .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error(String(response.status))); })
      .then(function (payload) { if (!cancelled) { setFileDirection(fileDirectionRef.current); setFileEntries(Array.isArray(payload.entries) ? payload.entries : []); setHasLoadedFiles(true); } })
      .catch(function () { if (!cancelled) { setFileEntries([]); setFilesError(wbcT("rail.filesUnavailable", "Unable to load project files.")); } })
      .finally(function () { if (!cancelled) setFilesLoading(false); });
    return function () { cancelled = true; };
  }, [fileToolsExpanded, filePath, projectId]);

  useWbcEffect(function () {
    var search = query.trim();
    if (!search || !projectId) {
      setGlobalFileEntries([]);
      setGlobalFilesLoading(false);
      setGlobalFilesError("");
      return undefined;
    }
    var cancelled = false;
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    setGlobalFilesLoading(true);
    setGlobalFilesError("");
    var timer = window.setTimeout(function () {
      fetch("/api/projects/" + encodeURIComponent(projectId) + "/files?query=" + encodeURIComponent(search), {
        cache: "no-store",
        signal: controller ? controller.signal : undefined,
      })
        .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error(String(response.status))); })
        .then(function (payload) {
          if (cancelled) return;
          var normalizedSearch = search.toLowerCase();
          setGlobalFileEntries((Array.isArray(payload.entries) ? payload.entries : []).filter(function (entry) {
            return String(entry && entry.name || "").toLowerCase().indexOf(normalizedSearch) >= 0
              || String(entry && entry.path || "").toLowerCase().indexOf(normalizedSearch) >= 0;
          }));
        })
        .catch(function (error) {
          if (!cancelled && (!error || error.name !== "AbortError")) {
            setGlobalFileEntries([]);
            setGlobalFilesError(wbcT("rail.filesUnavailable", "Unable to load project files."));
          }
        })
        .finally(function () { if (!cancelled) setGlobalFilesLoading(false); });
    }, 160);
    return function () {
      cancelled = true;
      window.clearTimeout(timer);
      if (controller) controller.abort();
    };
  }, [query, projectId]);

  useWbcEffect(function () {
    if (!query.trim()) return;
    setFileToolsExpanded(false);
    setTerminalToolsExpanded(false);
  }, [query]);

  function toggleRailMode() {
    setQuery("");
    var nextMode = railMode === "chat"
      ? "task"
      : railMode === "task"
        ? "chat"
        : (workRailMode === "task" ? "task" : "chat");
    if (onRailModeChange) onRailModeChange(nextMode);
  }
  var railMotionCollapsedRef = useWbcRef(!!collapsed);
  /* Derive the phase during the first render that sees the new collapsed prop.
     Waiting for an Effect (or scheduling state while rendering) leaves one
     paint where `is-collapsed` has gone but `is-status-expanding` has not yet
     arrived. In that paint the marker snaps to the moving list geometry. */
  var renderedRailMotionPhase = railMotionCollapsedRef.current !== !!collapsed
    ? (collapsed ? "collapsing" : "expanding")
    : railMotionPhase;
  var trackLifecycleRef = useWbcRef({ projectId: "", chats: {} });
  var railRef = useWbcRef(null);
  var projectToolsRef = useWbcRef(null);
  var projectToolPullRef = useWbcRef({
    wheelDistance: 0,
    wheelAt: 0,
    touchStartX: null,
    touchStartY: null,
    touchActive: false,
  });
  var chatListRef = useWbcRef(null);
  var chatSearchRef = useWbcRef(null);
  var newChatButtonRef = useWbcRef(null);
  var trackRef = useWbcRef(null);
  var trackMeasuredExpandedRef = useWbcRef(false);
  var railDragWasActiveRef = useWbcRef(false);
  var railDragImageCleanupRef = useWbcRef(null);
  var previewCloseTimerRef = useWbcRef(null);
  var dragOriginOrderRef = useWbcRef([]);
  var orderRef = useWbcRef(order);
  var dropCommittedRef = useWbcRef(false);
  var suppressClickRef = useWbcRef("");
  var suppressGroupClickRef = useWbcRef("");
  var groupMetadataRequestRef = useWbcRef({ sequence: 0, active: {} });
  var agentFlowTimersRef = useWbcRef({});
  var groupBackendLoadRef = useWbcRef(0);
  var groupBackendWriteRef = useWbcRef({ projectId: String(projectId || ""), sequence: 0, chain: Promise.resolve(), baseGroups: [] });
  var groupMetadataLang = window.CyreneUI.require("i18n").getLang();
  var chatMap = new Map((Array.isArray(chats) ? chats : []).map(function (chat) {
    return [String(chat.id), chat];
  }));

  function cancelPreviewClose() {
    if (!previewCloseTimerRef.current) return;
    window.clearTimeout(previewCloseTimerRef.current);
    previewCloseTimerRef.current = null;
  }

  function openStatusPreview(chatId) {
    cancelPreviewClose();
    setPreviewChatId(String(chatId || ""));
  }

  function closeStatusPreviewSoon() {
    cancelPreviewClose();
    previewCloseTimerRef.current = window.setTimeout(function () {
      previewCloseTimerRef.current = null;
      setPreviewChatId("");
    }, 250);
  }

  function activeProjectToolScroller() {
    var projectTools = projectToolsRef.current;
    if (!projectTools) return null;
    if (fileToolsExpanded) return projectTools.querySelector(".wbc-project-file-list");
    if (terminalToolsExpanded) return projectTools.querySelector(".wbc-project-terminal-list");
    return null;
  }

  function resetProjectToolPull() {
    var pull = projectToolPullRef.current;
    pull.wheelDistance = 0;
    pull.wheelAt = 0;
    pull.touchStartX = null;
    pull.touchStartY = null;
    pull.touchActive = false;
  }

  function collapseProjectToolFromPull() {
    resetProjectToolPull();
    setMenuId("");
    if (fileToolsExpanded) setFileToolsExpanded(false);
    if (terminalToolsExpanded) setTerminalToolsExpanded(false);
  }

  function handleProjectToolWheel(event) {
    if (!fileToolsExpanded && !terminalToolsExpanded) return;
    var scroller = activeProjectToolScroller();
    var pull = projectToolPullRef.current;
    var delta = Number(event.deltaY || 0);
    if (!scroller || scroller.scrollTop > 1 || delta >= 0) {
      pull.wheelDistance = 0;
      pull.wheelAt = 0;
      return;
    }
    var now = Date.now();
    if (!pull.wheelAt || now - pull.wheelAt > 260) pull.wheelDistance = 0;
    pull.wheelAt = now;
    pull.wheelDistance += Math.abs(delta);
    if (pull.wheelDistance >= 72) collapseProjectToolFromPull();
  }

  function handleProjectToolTouchStart(event) {
    var touch = event.touches && event.touches[0];
    var scroller = activeProjectToolScroller();
    var pull = projectToolPullRef.current;
    pull.touchActive = Boolean(touch && scroller && scroller.scrollTop <= 1);
    pull.touchStartX = pull.touchActive ? Number(touch.clientX) : null;
    pull.touchStartY = pull.touchActive ? Number(touch.clientY) : null;
  }

  function handleProjectToolTouchMove(event) {
    var pull = projectToolPullRef.current;
    if (!pull.touchActive || pull.touchStartY === null) return;
    var touch = event.touches && event.touches[0];
    var scroller = activeProjectToolScroller();
    if (!touch || !scroller || scroller.scrollTop > 1) {
      resetProjectToolPull();
      return;
    }
    var distanceY = Number(touch.clientY) - pull.touchStartY;
    var distanceX = Math.abs(Number(touch.clientX) - pull.touchStartX);
    if (distanceY >= 56 && distanceY > distanceX) collapseProjectToolFromPull();
  }

  function answerFromStatusPreview(chat, questionId, answerText, resumeMode) {
    if (!chat || !chat.id || !onAnswer || previewAnswerState.busy) return;
    var chatId = String(chat.id);
    setPreviewAnswerState({ chatId: chatId, busy: true, result: "", error: "" });
    Promise.resolve(onAnswer(chatId, questionId, answerText, resumeMode)).then(function () {
      setPreviewAnswerState({ chatId: chatId, busy: false, result: "sent", error: "" });
    }).catch(function (err) {
      setPreviewAnswerState({ chatId: chatId, busy: false, result: "", error: wbcErrorText(err) });
    });
  }

  useWbcEffect(function () {
    return function () { cancelPreviewClose(); };
  }, []);

  useWbcEffect(function () {
    function onAgentChatFlow(event) {
      var detail = event && event.detail && typeof event.detail === "object" ? event.detail : {};
      var chatId = String(detail.chatId || "").trim();
      var kind = String(detail.kind || "").trim();
      if (!chatId || ["created", "typing"].indexOf(kind) < 0) return;
      var expiresAt = Number(detail.expiresAt || 0);
      var remaining = Math.max(0, expiresAt - Date.now());
      if (!remaining) return;
      var timers = agentFlowTimersRef.current;
      if (timers[chatId]) window.clearTimeout(timers[chatId]);
      setAgentFlowByChatId(function (current) { return { ...current, [chatId]: kind }; });
      timers[chatId] = window.setTimeout(function () {
        delete timers[chatId];
        setAgentFlowByChatId(function (current) {
          if (!current[chatId]) return current;
          var next = { ...current };
          delete next[chatId];
          return next;
        });
      }, remaining);
    }
    window.addEventListener(WBC_AGENT_CHAT_FLOW_EVENT, onAgentChatFlow);
    return function () {
      window.removeEventListener(WBC_AGENT_CHAT_FLOW_EVENT, onAgentChatFlow);
      Object.keys(agentFlowTimersRef.current).forEach(function (chatId) {
        window.clearTimeout(agentFlowTimersRef.current[chatId]);
      });
      agentFlowTimersRef.current = {};
    };
  }, []);

  useWbcEffect(function () {
    Object.keys(agentFlowTimersRef.current).forEach(function (chatId) {
      window.clearTimeout(agentFlowTimersRef.current[chatId]);
    });
    agentFlowTimersRef.current = {};
    setAgentFlowByChatId({});
  }, [projectId]);

  useWbcEffect(function () {
    return function () { clearRailDragImage(); };
  }, []);

  useWbcLayoutEffect(function () {
    if (railMotionCollapsedRef.current === !!collapsed) return;
    railMotionCollapsedRef.current = !!collapsed;
    setRailMotionPhase(collapsed ? "collapsing" : "expanding");
  }, [collapsed]);

  useWbcEffect(function () {
    if (!railMotionPhase) return undefined;
    var phase = railMotionPhase;
    var timer = window.setTimeout(function () {
      setRailMotionPhase(function (current) { return current === phase ? "" : current; });
    /* Keep the expanding phase alive through the post-settle hold and the
       dot-to-glyph crossfade (440ms motion + 70ms hold + 90ms morph). */
    }, 630);
    return function () { window.clearTimeout(timer); };
  }, [railMotionPhase]);

  useWbcEffect(function () {
    if (!collapsed) setPreviewChatId("");
  }, [collapsed]);

  /* Blue is deliberately ephemeral: it records only an inactive conversation
     that transitioned from running to completed while this page is alive. */
  useWbcEffect(function () {
    var currentProjectId = String(projectId || "");
    var current = {};
    (Array.isArray(chats) ? chats : []).forEach(function (chat) {
      if (!chat || !chat.id) return;
      current[chat.id] = {
        running: wbcConversationTrackIsRunning(chat, runningChatIds),
        completed: wbcConversationTrackIsCompleted(chat),
      };
    });
    var lifecycle = trackLifecycleRef.current;
    if (lifecycle.projectId !== currentProjectId) {
      trackLifecycleRef.current = { projectId: currentProjectId, chats: current };
      setNewResultChatIds({});
      setPreviewChatId("");
      return;
    }
    var previous = lifecycle.chats || {};
    trackLifecycleRef.current = { projectId: currentProjectId, chats: current };
    setNewResultChatIds(function (existing) {
      var next = {};
      var changed = false;
      Object.keys(existing || {}).forEach(function (chatId) {
        if (current[chatId] && !current[chatId].running && current[chatId].completed && chatId !== String(activeChatId || "")) {
          next[chatId] = true;
        } else {
          changed = true;
        }
      });
      Object.keys(current).forEach(function (chatId) {
        if (
          previous[chatId]
          && previous[chatId].running
          && !current[chatId].running
          && current[chatId].completed
          && chatId !== String(activeChatId || "")
          && !next[chatId]
        ) {
          next[chatId] = true;
          changed = true;
        }
      });
      var existingKeys = Object.keys(existing || {});
      var nextKeys = Object.keys(next);
      if (!changed && existingKeys.length === nextKeys.length) return existing;
      return next;
    });
  }, [projectId, chats, runningChatIds, activeChatId]);

  useWbcEffect(function () {
    var activeId = String(activeChatId || "");
    if (!activeId) return;
    setNewResultChatIds(function (existing) {
      if (!existing[activeId]) return existing;
      var next = { ...existing };
      delete next[activeId];
      return next;
    });
  }, [activeChatId]);

  function revealRailMenu(actions) {
    if (!actions) return;
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        if (!actions.isConnected) return;
        var menu = actions.querySelector(".wb-card-menu");
        if (menu) menu.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
      });
    });
  }

  useWbcEffect(function () {
    setOrder(wbcLoadChatOrder(projectId, defaultOrder));
    var legacyGroups = wbcLoadChatGroups(projectId, defaultOrder);
    setGroups(legacyGroups);
    setGroupBackendReady(false);
    setCollapsedGroups(legacyGroups.reduce(function (state, group) {
      state[group.id] = true;
      return state;
    }, {}));
    setGroupMetadataPending({});
    groupMetadataRequestRef.current.active = {};
    setDragState(null);
    if (loading || !String(projectId || "").trim()) return;
    groupBackendLoadRef.current += 1;
    var loadToken = groupBackendLoadRef.current;
    var backendRef = groupBackendWriteRef.current;
    backendRef.projectId = String(projectId || "");
    backendRef.sequence = 0;
    backendRef.chain = Promise.resolve();
    backendRef.baseGroups = [];
    var loadPromise = WorkbenchChatModel.listChatGroups(projectId).then(function (payload) {
      if (groupBackendLoadRef.current !== loadToken) return null;
      if (payload && payload.migrationRequired) {
        return WorkbenchChatModel.migrateChatGroups({
          projectId: projectId,
          groups: legacyGroups,
        });
      }
      return payload;
    }).then(function (payload) {
      if (!payload || groupBackendLoadRef.current !== loadToken) return;
      var authoritative = storeNormalizedGroups(payload.groups || []);
      backendRef.baseGroups = authoritative;
      setGroups(authoritative);
      setCollapsedGroups(authoritative.reduce(function (state, group) {
        state[group.id] = true;
        return state;
      }, {}));
      setGroupBackendReady(true);
    }).catch(function () {
      // Keep the legacy/last-known browser cache for offline startup. The next
      // project load or mutation retries against the authoritative backend.
    });
    backendRef.chain = loadPromise;
    return function () {
      if (groupBackendLoadRef.current === loadToken) groupBackendLoadRef.current += 1;
    };
  }, [projectId, defaultOrderKey, loading]);

  var orderedChats = wbcNormalizeChatOrder(defaultOrder, order).map(function (id) {
    return chatMap.get(id);
  }).filter(Boolean);
  var filtered = useWbcMemo(function () {
    var q = query.trim().toLowerCase();
    return !q ? orderedChats : orderedChats.filter(function (chat) {
      var group = wbcFindChatGroup(groups, chat.id);
      return String(chat.title || "").toLowerCase().indexOf(q) !== -1
        || String(chat.preview || "").toLowerCase().indexOf(q) !== -1
        || String(group && group.title || "").toLowerCase().indexOf(q) !== -1
        || String(group && group.summary || "").toLowerCase().indexOf(q) !== -1;
    });
  }, [orderedChats, query, groups]);
  var railItems = useWbcMemo(function () {
    return wbcBuildChatRailItems(filtered, groups);
  }, [filtered, groups]);
  var pinnedChatIdSet = new Set((Array.isArray(pinnedChatIds) ? pinnedChatIds : []).map(function (id) { return String(id || ""); }));
  var pinnedRailItems = railItems.filter(function (item) {
    return item.kind === "chat" && pinnedChatIdSet.has(String(item.chat && item.chat.id || ""));
  });
  var recentRailItems = railItems.filter(function (item) {
    return item.kind === "chat" && !pinnedChatIdSet.has(String(item.chat && item.chat.id || ""));
  });
  var groupRailItems = railItems.filter(function (item) { return item.kind === "group"; });
  var groupMetadataRefreshKey = groups.map(function (group) {
    return [
      group.id,
      group.chatIds.join(","),
      group.metadataLang || "",
      group.metadataChatIds || "",
      group.summary ? "ready" : "empty",
    ].join(":");
  }).join("|");

  useWbcEffect(function () {
    if (!groupBackendReady) return;
    groups.forEach(function (group) {
      if (
        !group.summary
        || group.metadataLang !== groupMetadataLang
        || group.metadataChatIds !== group.chatIds.map(String).join("|")
      ) {
        refreshChatGroupMetadata(group);
      }
    });
  }, [projectId, groupBackendReady, groupMetadataLang, groupMetadataRefreshKey]);

  function storeNormalizedGroups(nextGroups) {
    var normalized = wbcNormalizeChatGroups(nextGroups, defaultOrder);
    try {
      localStorage.setItem(
        WBC_CHAT_GROUPS_PREFIX + String(projectId || ""),
        JSON.stringify(normalized)
      );
    } catch (e) {}
    return normalized;
  }

  function commitGroups(nextGroups, intent) {
    var normalized = storeNormalizedGroups(nextGroups);
    setGroups(normalized);
    persistGroups(normalized, intent);
    return normalized;
  }

  function persistGroups(normalized, intent) {
    var state = groupBackendWriteRef.current;
    var currentProjectId = String(projectId || "");
    if (state.projectId !== currentProjectId) {
      state.projectId = currentProjectId;
      state.sequence = 0;
      state.chain = Promise.resolve();
      state.baseGroups = [];
    }
    state.sequence += 1;
    var sequence = state.sequence;
    var desired = wbcNormalizeChatGroups(normalized, defaultOrder);
    var write = state.chain.catch(function () {}).then(function () {
      return WorkbenchChatModel.replaceChatGroups({
        projectId: currentProjectId,
        groups: desired,
        baseGroups: state.baseGroups || [],
        intent: intent || undefined,
      });
    });
    state.chain = write;
    return write.then(function (payload) {
      var live = groupBackendWriteRef.current;
      var serverGroups = wbcNormalizeChatGroups(payload.groups || [], defaultOrder);
      if (live.projectId === currentProjectId) live.baseGroups = serverGroups;
      if (
        live.projectId === currentProjectId
        && live.sequence === sequence
        && String(projectId || "") === currentProjectId
      ) {
        var authoritative = storeNormalizedGroups(serverGroups);
        setGroups(authoritative);
      }
      return payload;
    });
  }

  function refreshChatGroupMetadata(group) {
    if (!group || !Array.isArray(group.chatIds) || group.chatIds.length < 2) {
      return Promise.resolve(null);
    }
    var members = group.chatIds.map(function (chatId) {
      var chat = chatMap.get(String(chatId));
      return chat ? {
        id: String(chat.id || ""),
        title: String(chat.title || ""),
        preview: String(chat.preview || ""),
      } : null;
    }).filter(Boolean);
    if (members.length < 2) return Promise.resolve(null);
    var signature = group.chatIds.map(String).join("|");
    var requestState = groupMetadataRequestRef.current;
    requestState.sequence += 1;
    var token = requestState.sequence;
    requestState.active[group.id] = token;
    setGroupMetadataPending(function (current) { return { ...current, [group.id]: true }; });
    return groupBackendWriteRef.current.chain.catch(function () {}).then(function () {
      if (groupMetadataRequestRef.current.active[group.id] !== token) return null;
      return WorkbenchChatModel.generateChatGroupMetadata({
        projectId: projectId,
        groupId: group.id,
        signature: signature,
        members: members,
        currentTitle: group.title || "",
        titleLocked: !!group.titleLocked,
        lang: groupMetadataLang,
      });
    }).then(function (result) {
      if (!result) return null;
      var metadata = result.metadata || {};
      var persistedGroup = result.group;
      if (groupMetadataRequestRef.current.active[group.id] !== token) return null;
      setGroups(function (current) {
        var live = current.find(function (candidate) { return candidate.id === group.id; });
        if (!live || live.chatIds.map(String).join("|") !== signature) return current;
        var next = current.map(function (candidate) {
          if (candidate.id !== group.id) return candidate;
          if (
            persistedGroup
            && String(persistedGroup.id || "") === group.id
            && Array.isArray(persistedGroup.chatIds)
            && persistedGroup.chatIds.map(String).join("|") === signature
          ) {
            return {
              ...candidate,
              title: String(persistedGroup.title || candidate.title),
              summary: String(persistedGroup.summary || candidate.summary),
              titleLocked: !!persistedGroup.titleLocked,
              metadataLang: String(persistedGroup.metadataLang || metadata.lang || groupMetadataLang),
              metadataChatIds: String(persistedGroup.metadataChatIds || signature),
            };
          }
          return {
            ...candidate,
            title: candidate.titleLocked
              ? candidate.title
              : (String(metadata.title || "").trim().slice(0, 60) || candidate.title),
            summary: String(metadata.summary || "").trim().slice(0, 160) || candidate.summary,
            metadataLang: String(metadata.lang || groupMetadataLang),
            metadataChatIds: signature,
          };
        });
        var normalized = storeNormalizedGroups(next);
        if (groupBackendWriteRef.current.projectId === String(projectId || "")) {
          groupBackendWriteRef.current.baseGroups = normalized;
        }
        return normalized;
      });
      return result;
    }).catch(function () {
      return null;
    }).finally(function () {
      if (groupMetadataRequestRef.current.active[group.id] !== token) return;
      delete groupMetadataRequestRef.current.active[group.id];
      setGroupMetadataPending(function (current) {
        if (!current[group.id]) return current;
        var next = { ...current };
        delete next[group.id];
        return next;
      });
    });
  }

  function commitGroupDrop(movingId, targetId) {
    var nextOrder = wbcMoveChatOrder(dragOriginOrderRef.current, movingId, targetId, "after");
    commitOrder(nextOrder, movingId);
    var desiredGroups = wbcCreateChatGroup(
      groups,
      movingId,
      targetId,
      "group_" + Date.now().toString(36)
    );
    var created = wbcFindChatGroup(desiredGroups, targetId);
    commitGroups(desiredGroups, {
      type: "move",
      sessionId: String(movingId || ""),
      targetGroupId: created ? created.id : "",
    });
    if (created) {
      setCollapsedGroups(function (current) { return { ...current, [created.id]: false }; });
      setAnnouncement(wbcT("workbenchChat.groupCreated", "Created {title} with {count} chats.", {
        title: created.title,
        count: created.chatIds.length,
      }));
    }
  }

  function commitUngroupDrop(chatId) {
    var sourceGroup = wbcFindChatGroup(groups, chatId);
    if (!sourceGroup) return;
    commitGroups(wbcRemoveChatFromGroups(groups, chatId), {
      type: "remove_member",
      sessionId: String(chatId || ""),
    });
    setAnnouncement(wbcT("workbenchChat.removedFromGroup", "Removed {title} from {group}.", {
      title: (chatMap.get(String(chatId)) || {}).title || wbcT("workbenchChat.newChat", "New chat"),
      group: sourceGroup.title,
    }));
  }

  function renameChatGroup(groupId, title) {
    var next = groups.map(function (group) {
      return group.id === groupId ? {
        ...group,
        title: String(title || "").trim().slice(0, 60),
        titleLocked: true,
      } : group;
    });
    commitGroups(next, {
      type: "rename",
      groupId: groupId,
      title: String(title || "").trim().slice(0, 60),
    });
    return groupBackendWriteRef.current.chain;
  }

  function dissolveChatGroup(groupId) {
    var group = groups.find(function (candidate) { return candidate.id === groupId; });
    commitGroups(groups.filter(function (candidate) { return candidate.id !== groupId; }), {
      type: "dissolve",
      groupId: groupId,
    });
    setMenuId("");
    if (group) {
      setAnnouncement(wbcT("workbenchChat.groupDissolved", "Dissolved {title}.", { title: group.title }));
    }
  }

  function commitOrder(nextOrder, movedId) {
    var normalized = wbcNormalizeChatOrder(defaultOrder, nextOrder);
    var positionChanged = normalized.join("|") !== (orderRef.current || []).join("|");
    setOrder(normalized);
    try {
      localStorage.setItem(WBC_CHAT_ORDER_PREFIX + String(projectId || ""), JSON.stringify(normalized));
    } catch (e) {}
    var movedChat = chatMap.get(String(movedId || ""));
    if (movedChat && positionChanged) {
      setAnnouncement(wbcT(
        "workbenchChat.chatMoved",
        "{title} moved to position {position} of {total}.",
        {
          title: movedChat.title || wbcT("workbenchChat.newChat", "New chat"),
          position: normalized.indexOf(String(movedId)) + 1,
          total: normalized.length,
        }
      ));
    }
  }

  function commitGroupOrder(nextOrder, group) {
    commitOrder(nextOrder, "");
    if (group) {
      setAnnouncement(wbcT("workbenchChat.groupMoved", "{title} moved.", {
        title: group.title || wbcT("workbenchChat.newGroup", "New chat group"),
      }));
    }
  }

  function moveChatByKeyboard(event, id) {
    if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return false;
    var visibleOrder = filtered.map(function (chat) { return String(chat.id); });
    var index = visibleOrder.indexOf(String(id));
    var nextIndex = event.key === "ArrowUp" ? index - 1 : index + 1;
    if (index < 0 || nextIndex < 0 || nextIndex >= visibleOrder.length) return false;
    event.preventDefault();
    event.stopPropagation();
    var targetId = visibleOrder[nextIndex];
    commitOrder(wbcMoveChatOrder(
      order,
      String(id),
      targetId,
      event.key === "ArrowUp" ? "before" : "after"
    ), id);
    return true;
  }

  function updateDragState(next) {
    setDragState(function (current) {
      if (!current || !next) return next;
      var resolved = {
        ...next,
        dragKind: next.dragKind === undefined ? current.dragKind : next.dragKind,
        movingGroupId: next.movingGroupId === undefined ? current.movingGroupId : next.movingGroupId,
        movingIds: next.movingIds === undefined ? current.movingIds : next.movingIds,
        sourceGroupId: next.sourceGroupId === undefined ? current.sourceGroupId : next.sourceGroupId,
      };
      if (
        current.dragKind === resolved.dragKind
        && current.movingId === resolved.movingId
        && current.movingGroupId === resolved.movingGroupId
        && (current.movingIds || []).join("|") === (resolved.movingIds || []).join("|")
        && current.targetId === resolved.targetId
        && current.targetGroupId === resolved.targetGroupId
        && current.sourceGroupId === resolved.sourceGroupId
        && current.edge === resolved.edge
        && current.mode === resolved.mode
      ) return current;
      return resolved;
    });
  }

  function chatCanGroupWith(movingId, targetId) {
    var movingGroup = wbcFindChatGroup(groups, movingId);
    var targetGroup = wbcFindChatGroup(groups, targetId);
    return !(movingGroup && targetGroup && movingGroup.id === targetGroup.id);
  }

  function chatDropMode(event, movingId, targetId) {
    if (!chatCanGroupWith(movingId, targetId)) return "reorder";
    if (
      dragState
      && dragState.mode === "group"
      && dragState.movingId === String(movingId)
      && dragState.targetId === String(targetId)
    ) return "group";
    var rect = event.currentTarget.getBoundingClientRect();
    var ratio = rect.height ? (event.clientY - rect.top) / rect.height : 0;
    return ratio >= 0.22 && ratio <= 0.78 ? "group" : "reorder";
  }

  function chatRailVisualState(chat) {
    var running = wbcConversationTrackIsRunning(chat, runningChatIds);
    var rawStatus = String(chat.runStatus || chat.status || "").trim().toLowerCase();
    var failed = !!chat.failed || !!chat.error || ["error", "failed", "failure", "timeout"].indexOf(rawStatus) >= 0;
    var attention = !!chat.awaitingUser || !!chat.pendingQuestion || [
      "awaiting_user", "waiting_for_user", "waiting_for_approval", "needs_input",
      "waiting_input", "requires_confirmation", "blocked", "review",
    ].indexOf(rawStatus) >= 0;
    var completed = ["completed", "complete", "done", "success", "succeeded"].indexOf(rawStatus) >= 0;
    return {
      running: running,
      tone: failed ? " status-failed" : attention ? " status-attention" : completed ? " status-completed" : running ? " status-running" : "",
      icon: failed ? WBC_ICONS.errorCircle : attention ? WBC_ICONS.alert : completed ? WBC_ICONS.check : running ? WBC_ICONS.running : WBC_ICONS.file,
      label: failed
        ? wbcT("status.failed", "Failed")
        : attention ? wbcT("workbenchChat.awaitingUser", "Needs input") : "",
    };
  }

  function prepareRailDragImage(root, transfer, clientX, clientY) {
    if (!root || !root.querySelectorAll || !transfer) return;
    if (railDragImageCleanupRef.current) railDragImageCleanupRef.current();
    /* Native drag-image capture timing differs across Chromium platforms.
       Hide that native image and move a real DOM clone with the pointer so the
       complete card surface and its status icon remain deterministic. */
    var builtPreview = wbcBuildRailCardDragPreview(root, "");
    if (!builtPreview) return;
    var rect = builtPreview.rect;
    var host = builtPreview.host;
    host.style.position = "fixed";
    host.style.left = rect.left + "px";
    host.style.top = rect.top + "px";
    host.style.zIndex = "2147483647";
    document.body.appendChild(host);
    var grabX = Math.max(0, Math.min(rect.width, clientX - rect.left));
    var grabY = Math.max(0, Math.min(rect.height, clientY - rect.top));
    function moveDragImage(event) {
      var x = Number(event && event.clientX);
      var y = Number(event && event.clientY);
      if (!Number.isFinite(x) || !Number.isFinite(y) || (x === 0 && y === 0)) return;
      host.style.left = (x - grabX) + "px";
      host.style.top = (y - grabY) + "px";
    }
    function finishDragImage() {
      clearRailDragImage();
    }
    document.addEventListener("drag", moveDragImage, true);
    document.addEventListener("dragover", moveDragImage, true);
    document.addEventListener("drop", finishDragImage, true);
    wbcHideNativeDragImage(transfer);
    railDragImageCleanupRef.current = function () {
      document.removeEventListener("drag", moveDragImage, true);
      document.removeEventListener("dragover", moveDragImage, true);
      document.removeEventListener("drop", finishDragImage, true);
      host.remove();
      railDragImageCleanupRef.current = null;
    };
  }

  function clearRailDragImage() {
    if (railDragImageCleanupRef.current) railDragImageCleanupRef.current();
  }

  function renderDropClone(chat) {
    if (!chat) return null;
    var visualState = chatRailVisualState(chat);
    return (
      <div className={"wbc-chat-card wbc-chat-group-drop-clone" + visualState.tone} aria-hidden="true">
        <span className="wbc-chat-card-top">
          <span className="wbc-chat-row-icon" aria-hidden="true">{visualState.icon}</span>
          <span className="wbc-chat-card-title">
            <b><WbcHoverMarquee text={chat.title || wbcT("workbenchChat.newChat", "New chat")} /></b>
          </span>
          <time className="wbc-chat-card-time">{wbcFormatTime(chat.updatedAt || chat.createdAt)}</time>
        </span>
        <span className="wbc-chat-card-preview">
          {visualState.running ? <i className="wbc-running-dot" /> : null}
          <WbcHoverMarquee text={chat.preview || wbcT("workbenchChat.noMessages", "No messages yet")} />
        </span>
      </div>
    );
  }

  function renderChatCard(chat, options) {
    options = options || {};
    var active = chat.id === activeChatId;
    /* Keep real rows and drag-preview clones on one status mapping so a chat
       cannot change icon merely because it is being grouped. */
    var visualState = chatRailVisualState(chat);
    var chatRunning = visualState.running;
    var chatStatusTone = visualState.tone;
    var chatStatusIcon = visualState.icon;
    var chatStatusLabel = visualState.label;
    var chatTrackState = wbcConversationTrackState(chat, runningChatIds, newResultChatIds);
    var chatTrackGeometry = trackGeometryByChatId[String(chat.id)] || null;
    var chatTrackMarkerReady = !!(
      !dragState
      && chatTrackState
      && chatTrackGeometry
      && Number.isFinite(Number(chatTrackGeometry.position))
      && Number.isFinite(Number(chatTrackGeometry.expandedX))
    );
    var isMenuOpen = menuId === chat.id;
    var isPinned = (Array.isArray(pinnedChatIds) ? pinnedChatIds : []).some(function (id) {
      return String(id || "") === String(chat.id || "");
    });
    var isDragging = dragState && dragState.movingId === String(chat.id);
    var isGroupTarget = dragState && dragState.mode === "group" && dragState.targetId === String(chat.id);
    var agentFlow = String(agentFlowByChatId[String(chat.id)] || "");
    var agentFlowLabel = agentFlow === "created"
      ? wbcT("workbenchChat.agentFlow.created", "Agent created this chat")
      : agentFlow === "typing"
        ? wbcT("workbenchChat.agentFlow.typing", "Agent is entering a message")
        : "";
    var chatLabel = chat.title || wbcT("workbenchChat.newChat", "New chat");
    var dragTitle = wbcT("workbenchChat.dragChat", "Drag to reorder, overlap another chat to group, or drop in the conversation area to open {title}.", {
      title: chatLabel,
    });
    return (
      <div
        key={chat.id}
        data-chat-id={String(chat.id)}
        data-cyrene-node-id={"chat_" + String(chat.id)}
        role="button"
        tabIndex={0}
        draggable="true"
        className={"wbc-chat-card"
          + (options.insideGroup ? " wbc-chat-group-child" : "")
          + (active ? " active" : "")
          + (isMenuOpen ? " menu-open" : "")
          + (isDragging ? " dragging" : "")
          + (isGroupTarget ? " group-drop-target" : "")
          + (chatTrackMarkerReady ? " track-marker-ready" : "")
          + (agentFlow ? (" agent-flow agent-flow-" + agentFlow) : "")
          + chatStatusTone}
        title={agentFlowLabel ? (dragTitle + " · " + agentFlowLabel) : dragTitle}
        aria-label={agentFlowLabel ? (chatLabel + " · " + agentFlowLabel) : chatLabel}
        data-agent-flow={agentFlow || undefined}
        data-cyrene-context-menu="true"
        onClick={function () {
          if (suppressClickRef.current === String(chat.id)) return;
          setMenuId("");
          onSelect(chat.id);
        }}
        onContextMenu={function (event) {
          event.preventDefault();
          event.stopPropagation();
          setMenuId(chat.id);
        }}
        onKeyDown={function (e) {
          if (moveChatByKeyboard(e, chat.id)) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(chat.id);
          }
        }}
        onDragStart={function (event) {
          if (event.target && event.target.closest && event.target.closest("button")) {
            event.preventDefault();
            return;
          }
          var id = String(chat.id);
          dragOriginOrderRef.current = order.slice();
          dropCommittedRef.current = false;
          suppressClickRef.current = id;
          setMenuId("");
          wbcSetChatDrag(event, chat);
          if (event.dataTransfer) {
            prepareRailDragImage(
              event.currentTarget,
              event.dataTransfer,
              event.clientX,
              event.clientY
            );
          }
          var sourceGroup = wbcFindChatGroup(groups, id);
          setDragState({
            dragKind: "chat",
            movingId: id,
            movingGroupId: "",
            movingIds: [id],
            targetId: "",
            targetGroupId: "",
            sourceGroupId: sourceGroup ? sourceGroup.id : "",
            edge: "before",
            mode: "reorder",
          });
        }}
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          if (dragState.dragKind === "group") {
            if ((dragState.movingIds || []).indexOf(String(chat.id)) >= 0) return;
            event.preventDefault();
            event.stopPropagation();
            if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
            var railGroupTarget = wbcFindChatGroup(groups, chat.id);
            if (railGroupTarget && railGroupTarget.id === dragState.movingGroupId) return;
            var railTarget = railGroupTarget
              ? event.currentTarget.closest(".wbc-chat-group") || event.currentTarget
              : event.currentTarget;
            var railRect = railTarget.getBoundingClientRect();
            var railEdge = event.clientY < railRect.top + (railRect.height / 2) ? "before" : "after";
            var railTargetIds = railGroupTarget ? railGroupTarget.chatIds : [String(chat.id)];
            var groupOrder = wbcMoveChatOrderBlock(order, dragState.movingIds, railTargetIds, railEdge);
            if (groupOrder.join("|") !== order.join("|")) setOrder(groupOrder);
            updateDragState({
              movingId: dragState.movingId,
              targetId: String(chat.id),
              targetGroupId: railGroupTarget ? railGroupTarget.id : "",
              edge: railEdge,
              mode: "group-reorder",
            });
            return;
          }
          if (dragState.movingId === String(chat.id) || !wbcHasChatDrag(event)) return;
          event.preventDefault();
          event.stopPropagation();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          var mode = chatDropMode(event, dragState.movingId, String(chat.id));
          var targetGroup = wbcFindChatGroup(groups, chat.id);
          if (mode === "group") {
            if (order.join("|") !== dragOriginOrderRef.current.join("|")) {
              setOrder(dragOriginOrderRef.current.slice());
            }
            updateDragState({
              movingId: dragState.movingId,
              targetId: String(chat.id),
              targetGroupId: targetGroup ? targetGroup.id : "",
              edge: "center",
              mode: "group",
            });
            return;
          }
          var rect = event.currentTarget.getBoundingClientRect();
          var edge = event.clientY < rect.top + (rect.height / 2) ? "before" : "after";
          var nextOrder = wbcMoveChatOrder(order, dragState.movingId, String(chat.id), edge);
          if (nextOrder.join("|") !== order.join("|")) setOrder(nextOrder);
          updateDragState({ movingId: dragState.movingId, targetId: String(chat.id), targetGroupId: "", edge: edge, mode: "reorder" });
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          event.preventDefault();
          event.stopPropagation();
          if (dragState.dragKind === "group") {
            var droppedGroup = groups.find(function (candidate) {
              return candidate.id === dragState.movingGroupId;
            });
            dropCommittedRef.current = true;
            commitGroupOrder(order, droppedGroup);
            setDragState(null);
            return;
          }
          if (!wbcHasChatDrag(event)) return;
          var mode = chatDropMode(event, dragState.movingId, String(chat.id));
          dropCommittedRef.current = true;
          if (mode === "group") {
            commitGroupDrop(dragState.movingId, String(chat.id));
          } else {
            var nextOrder = dragState.movingId === String(chat.id)
              ? order
              : wbcMoveChatOrder(order, dragState.movingId, String(chat.id), dragState.edge);
            commitOrder(nextOrder, dragState.movingId);
            var reorderTargetGroup = wbcFindChatGroup(groups, chat.id);
            if (
              dragState.sourceGroupId
              && (!reorderTargetGroup || reorderTargetGroup.id !== dragState.sourceGroupId)
            ) commitUngroupDrop(dragState.movingId);
          }
          setDragState(null);
        }}
        onDragEnd={function () {
          clearRailDragImage();
          if (!dropCommittedRef.current) setOrder(dragOriginOrderRef.current);
          dropCommittedRef.current = false;
          setDragState(null);
          window.setTimeout(function () {
            if (suppressClickRef.current === String(chat.id)) suppressClickRef.current = "";
          }, 0);
        }}
      >
        <span className="wbc-chat-card-top">
          <span className="wbc-chat-row-icon" aria-hidden="true">{chatStatusIcon}</span>
          <span className="wbc-chat-card-title">
            <b><WbcHoverMarquee text={chat.title || wbcT("workbenchChat.newChat", "New chat")} /></b>
            {isPinned ? (
              <span className="wbc-chat-card-pin" title={wbcT("workbenchChat.pinned", "Pinned")} aria-label={wbcT("workbenchChat.pinned", "Pinned")}>
                {WBC_ICONS.pin}
              </span>
            ) : null}
            {chat.forkedFromChatId && (
              <span
                className="wbc-fork-marker"
                title={wbcT("workbenchChat.forkSource", "Forked from another chat — click to open the original")}
                onClick={function (e) { e.stopPropagation(); onSelect(chat.forkedFromChatId); }}
              >
                {WBC_ICONS.fork}
                {wbcT("workbenchChat.forked", "Forked")}
              </span>
            )}
          </span>
          <span className="wbc-chat-card-right">
            {!chatStatusLabel && (
              <time className="wbc-chat-card-time">{wbcFormatTime(chat.updatedAt || chat.createdAt)}</time>
            )}
            {chatStatusLabel && (
              <em className="wbc-chat-card-status">{chatStatusLabel}</em>
            )}
            <span className="wbc-chat-card-actions">
              <button
                type="button"
                className="wb-card-menu-btn"
                title={wbcT("common.moreActions", "More actions")}
                onClick={function (e) {
                  e.stopPropagation();
                  var actions = e.currentTarget.parentElement;
                  var opening = !isMenuOpen;
                  setMenuId(isMenuOpen ? "" : chat.id);
                  if (opening) revealRailMenu(actions);
                }}
              >
                {WBC_ICONS.dots}
              </button>
              {isMenuOpen && (
                <div className="wb-card-menu" role="menu" data-cyrene-node-id="chat_context_menu">
                  <button type="button" role="menuitem" data-cyrene-node-id="chat_menu_pin" className="wbc-chat-pin-action" onClick={function (e) {
                    e.stopPropagation();
                    setMenuId("");
                    if (onTogglePinned) onTogglePinned(chat, !isPinned);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.pin}</span>
                    <span>{isPinned
                      ? wbcT("workbenchChat.unpin", "Unpin chat")
                      : wbcT("workbenchChat.pin", "Pin chat")}</span>
                  </button>
                  {!chat.legacy && (
                    <button type="button" role="menuitem" data-cyrene-node-id="chat_menu_rename" className="wbc-chat-menu-action" onClick={function (e) {
                      e.stopPropagation();
                      setMenuId("");
                      setRenameChat(chat);
                    }}>
                      <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span>
                      <span>{wbcT("workbenchChat.rename", "Rename chat")}</span>
                    </button>
                  )}
                  <button type="button" role="menuitem" data-cyrene-node-id="chat_menu_to_task" className="wbc-chat-menu-action" disabled={toTaskBusy} onClick={function (e) {
                    e.stopPropagation();
                    setMenuId("");
                    if (onToTask) onToTask(chat.id);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.task}</span>
                    <span>{wbcT(toTaskBusy ? "workbenchChat.toTaskBusy" : "workbenchChat.toTask", toTaskBusy ? "Analyzing chat…" : "Convert to task")}</span>
                  </button>
                  <button type="button" role="menuitem" data-cyrene-node-id="chat_menu_delete" className="wbc-chat-menu-action danger" onClick={function (e) {
                    e.stopPropagation();
                    setMenuId("");
                    onDelete && onDelete(chat.id);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.trash}</span>
                    <span>{wbcT("workbenchChat.delete", "Delete chat")}</span>
                  </button>
                </div>
              )}
            </span>
          </span>
        </span>
        <span className="wbc-chat-card-preview">
          {chatRunning ? <i className="wbc-running-dot" /> : null}
          <WbcHoverMarquee text={chat.preview || wbcT("workbenchChat.noMessages", "No messages yet")} />
        </span>
      </div>
    );
  }

  function renderGroupFrame(group, groupChats) {
    var isCollapsed = !!collapsedGroups[group.id];
    var groupMenuId = "group:" + group.id;
    var isMenuOpen = menuId === groupMenuId;
    var isGroupDragging = !!(
      dragState
      && dragState.dragKind === "group"
      && dragState.movingGroupId === group.id
    );
    function openGroupMenu(event) {
      event.preventDefault();
      event.stopPropagation();
      var actions = event.currentTarget.querySelector(".wbc-chat-group-actions");
      setMenuId(groupMenuId);
      revealRailMenu(actions);
    }
    function toggleGroupMenu(event) {
      event.stopPropagation();
      var actions = event.currentTarget.parentElement;
      var opening = !isMenuOpen;
      setMenuId(isMenuOpen ? "" : groupMenuId);
      if (opening) revealRailMenu(actions);
    }
    var movingChat = dragState && chatMap.get(String(dragState.movingId));
    var groupDropReady = !!(
      dragState
      && dragState.dragKind !== "group"
      && dragState.mode === "group"
      && (dragState.targetGroupId === group.id || group.chatIds.indexOf(String(dragState.targetId)) >= 0)
      && group.chatIds.indexOf(String(dragState.movingId)) < 0
    );
    return (
      <section
        key={group.id}
        className={"wbc-chat-group" + (isCollapsed ? " collapsed" : " expanded") + (groupDropReady ? " drop-ready" : "") + (isMenuOpen ? " menu-open" : "") + (isGroupDragging ? " dragging" : "")}
        data-cyrene-context-menu="true"
        onContextMenu={openGroupMenu}
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          if (event.target && event.target.closest && event.target.closest(".wbc-chat-card")) return;
          if (dragState.dragKind === "group") {
            if (dragState.movingGroupId === group.id) return;
            event.preventDefault();
            event.stopPropagation();
            if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
            var groupRect = event.currentTarget.getBoundingClientRect();
            var groupEdge = event.clientY < groupRect.top + (groupRect.height / 2) ? "before" : "after";
            var groupOrder = wbcMoveChatOrderBlock(order, dragState.movingIds, group.chatIds, groupEdge);
            if (groupOrder.join("|") !== order.join("|")) setOrder(groupOrder);
            updateDragState({
              movingId: dragState.movingId,
              targetId: String(group.chatIds[0] || ""),
              targetGroupId: group.id,
              edge: groupEdge,
              mode: "group-reorder",
            });
            return;
          }
          if (!wbcHasChatDrag(event) || group.chatIds.indexOf(String(dragState.movingId)) >= 0) return;
          event.preventDefault();
          event.stopPropagation();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          if (order.join("|") !== dragOriginOrderRef.current.join("|")) setOrder(dragOriginOrderRef.current.slice());
          updateDragState({
            movingId: dragState.movingId,
            targetId: String(group.chatIds[0] || ""),
            targetGroupId: group.id,
            edge: "center",
            mode: "group",
          });
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          if (dragState.dragKind === "group") {
            if (dragState.movingGroupId === group.id) return;
            event.preventDefault();
            event.stopPropagation();
            var reorderedGroup = groups.find(function (candidate) {
              return candidate.id === dragState.movingGroupId;
            });
            dropCommittedRef.current = true;
            commitGroupOrder(order, reorderedGroup);
            setDragState(null);
            return;
          }
          if (!wbcHasChatDrag(event) || group.chatIds.indexOf(String(dragState.movingId)) >= 0) return;
          event.preventDefault();
          event.stopPropagation();
          dropCommittedRef.current = true;
          commitGroupDrop(dragState.movingId, String(group.chatIds[group.chatIds.length - 1] || group.chatIds[0]));
          setDragState(null);
        }}
      >
        <header className="wbc-chat-group-head">
          <button
            type="button"
            className="wbc-chat-group-toggle"
            draggable="true"
            title={wbcT("workbenchChat.dragGroup", "Drag to move {title}.", { title: group.title })}
            onClick={function () {
              if (suppressGroupClickRef.current === group.id) return;
              setCollapsedGroups(function (current) { return { ...current, [group.id]: !isCollapsed }; });
            }}
            onDragStart={function (event) {
              dragOriginOrderRef.current = order.slice();
              dropCommittedRef.current = false;
              suppressGroupClickRef.current = group.id;
              setMenuId("");
              wbcSetChatGroupDrag(event, group, projectId);
              var frame = event.currentTarget.closest(".wbc-chat-group") || event.currentTarget;
              if (event.dataTransfer) {
                prepareRailDragImage(
                  frame,
                  event.dataTransfer,
                  event.clientX,
                  event.clientY
                );
              }
              setDragState({
                dragKind: "group",
                movingId: String(group.chatIds[0] || ""),
                movingGroupId: group.id,
                movingIds: group.chatIds.map(String),
                targetId: "",
                targetGroupId: "",
                sourceGroupId: group.id,
                edge: "before",
                mode: "group-reorder",
              });
            }}
            onDragEnd={function () {
              clearRailDragImage();
              if (!dropCommittedRef.current) setOrder(dragOriginOrderRef.current);
              dropCommittedRef.current = false;
              setDragState(null);
              window.setTimeout(function () {
                if (suppressGroupClickRef.current === group.id) suppressGroupClickRef.current = "";
              }, 0);
            }}
            aria-expanded={!isCollapsed}
          >
            <span className={"wbc-chat-group-leading-chevron" + (!isCollapsed ? " expanded" : "")} aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            <span className="wbc-chat-group-count">{group.chatIds.length + (groupDropReady ? 1 : 0)}</span>
            <b><WbcHoverMarquee text={group.title} /></b>
          </button>
          <span className="wbc-chat-group-actions">
            <button
              type="button"
              className="wb-card-menu-btn"
              title={wbcT("common.moreActions", "More actions")}
              onClick={toggleGroupMenu}
            >{WBC_ICONS.dots}</button>
            {isMenuOpen && (
              <div className="wb-card-menu" role="menu">
                <button type="button" role="menuitem" onClick={function (event) {
                  event.stopPropagation();
                  setMenuId("");
                  setRenameGroup(group);
                }}>
                  <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span>
                  <span>{wbcT("workbenchChat.groupRename", "Rename group")}</span>
                </button>
                <button type="button" role="menuitem" className="danger" onClick={function (event) {
                  event.stopPropagation();
                  dissolveChatGroup(group.id);
                }}>
                  <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.x}</span>
                  <span>{wbcT("workbenchChat.groupDissolve", "Dissolve group")}</span>
                </button>
              </div>
            )}
          </span>
        </header>
        <div className={"wbc-chat-group-summary" + (groupMetadataPending[group.id] ? " is-updating" : "")}>
          <WbcHoverMarquee text={group.summary || (groupMetadataPending[group.id]
            ? wbcT("workbenchChat.groupSummaryGenerating", "Generating summary…")
            : groupChats.map(function (chat) { return chat.title; }).join(" · "))} />
        </div>
        <div
          className={"wbc-chat-group-content" + (isCollapsed ? " collapsed" : " expanded")}
          aria-hidden={isCollapsed}
          inert={isCollapsed ? "" : undefined}
        >
          <div className="wbc-chat-group-content-inner">
            <div className="wbc-chat-group-children">
              {groupChats.map(function (chat) { return renderChatCard(chat, { insideGroup: true }); })}
              {groupDropReady ? renderDropClone(movingChat) : null}
            </div>
          </div>
        </div>
        {groupDropReady && (
          <span className="wbc-chat-group-drop-hint">{WBC_ICONS.copy}{wbcT("workbenchChat.releaseToExistingGroup", "Release to add to this chat group")}</span>
        )}
      </section>
    );
  }

  var recentOverviewLimit = 10;
  var visiblePinnedRailItems = pinnedRailItems;
  var visibleRecentRailItems = showAllRecent
    ? recentRailItems
    : recentRailItems.slice(0, recentOverviewLimit);
  var visibleGroupRailItems = groupRailItems;
  var recentOverflowCount = Math.max(0, recentRailItems.length - visibleRecentRailItems.length);
  var visibleRailItemCount = visiblePinnedRailItems.length + visibleRecentRailItems.length + visibleGroupRailItems.length;
  var visibleTrackLayoutKey = [
    visiblePinnedRailItems.map(function (item) { return String(item.chat && item.chat.id || ""); }).join(","),
    visibleRecentRailItems.map(function (item) { return String(item.chat && item.chat.id || ""); }).join(","),
    visibleGroupRailItems.map(function (item) {
      return String(item.group && item.group.id || "") + ":" + (collapsedGroups[item.group && item.group.id] ? "closed" : "open");
    }).join(","),
  ].join("|");
  /* Group-drop previews temporarily replace one flat row with a taller group
     frame. Suspend the floating status layer while that preview is visible;
     the cards keep showing their own status icons, and the dependency below
     remeasures the overlay as soon as the real list is restored. */
  var railDragActive = !!dragState;

  useWbcLayoutEffect(function () {
    var list = chatListRef.current;
    if (!list) return undefined;
    var frame = 0;
    function refreshViewport() {
      if (frame) return;
      frame = window.requestAnimationFrame(function () {
        frame = 0;
        setUiViewportRevision(function (value) { return value + 1; });
      });
    }
    list.addEventListener("scroll", refreshViewport, { passive: true });
    window.addEventListener("resize", refreshViewport);
    var observer = typeof ResizeObserver === "function" ? new ResizeObserver(refreshViewport) : null;
    if (observer) observer.observe(list);
    refreshViewport();
    return function () {
      if (frame) window.cancelAnimationFrame(frame);
      list.removeEventListener("scroll", refreshViewport);
      window.removeEventListener("resize", refreshViewport);
      if (observer) observer.disconnect();
    };
  }, [projectId, query, showAllRecent, visibleTrackLayoutKey, collapsed]);

  useWbcLayoutEffect(function () {
    if (railDragActive) {
      railDragWasActiveRef.current = true;
      return;
    }
    if (!railDragWasActiveRef.current) return;
    railDragWasActiveRef.current = false;
    /* Do not paint the pre-drag overlay coordinates for a frame while the
       restored flat list is waiting for its next measurement. */
    setTrackGeometryByChatId({});
  }, [railDragActive, projectId]);

  useWbcLayoutEffect(function () {
    trackMeasuredExpandedRef.current = false;
    setTrackGeometryByChatId({});
  }, [projectId]);

  /* Measure the expanded list itself instead of inferring geometry from the
     conversation index. Search, section headings, row height, groups, scroll,
     and the overview limit all affect the real Y coordinate. The hidden track
     keeps its collapsed bounds while expanded, while the collapsed list keeps
     its layout geometry even though it is not painted. Projecting each row
     centre into the track therefore works on initial load and in both states. */
  useWbcLayoutEffect(function () {
    /* Keep the last settled row geometry for the whole rail transition. A
       ResizeObserver fires while the hidden list is becoming visible; using
       those transient rectangles rewrites `top` for one paint and makes the
       marker flash before its transform animation can take over. */
    if (renderedRailMotionPhase || railDragActive) return undefined;
    if (collapsed && trackMeasuredExpandedRef.current) return undefined;
    if (!collapsed) trackMeasuredExpandedRef.current = true;
    var rail = railRef.current;
    var track = trackRef.current;
    if (!rail || !track) return undefined;
    var frame = 0;
    function measure() {
      if (frame) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(function () {
        frame = 0;
        var trackRect = track.getBoundingClientRect();
        if (trackRect.height <= 0) return;
        var measured = {};
        rail.querySelectorAll(".wbc-chat-card[data-chat-id]").forEach(function (card) {
          var rect = card.getBoundingClientRect();
          var iconRect = card.querySelector(".wbc-chat-row-icon")?.getBoundingClientRect();
          var chatId = String(card.getAttribute("data-chat-id") || "");
          if (!chatId || rect.height <= 0) return;
          measured[chatId] = {
            position: ((rect.top + (rect.height / 2) - trackRect.top) / trackRect.height) * 100,
            trackHeight: trackRect.height,
            expandedX: !collapsed && !renderedRailMotionPhase && iconRect
              ? iconRect.left + (iconRect.width / 2) - trackRect.left
              : null,
          };
        });
        setTrackGeometryByChatId(function (current) {
          var next = { ...current };
          Object.keys(measured).forEach(function (chatId) {
            var previous = current[chatId] || {};
            var measuredExpandedX = measured[chatId].expandedX;
            next[chatId] = {
              position: measured[chatId].position,
              trackHeight: measured[chatId].trackHeight,
              expandedX: measuredExpandedX != null && Number.isFinite(Number(measuredExpandedX))
                ? Number(measuredExpandedX)
                : (Number.isFinite(Number(previous.expandedX)) ? Number(previous.expandedX) : 29),
            };
          });
          var keys = Object.keys(next);
          if (
            keys.length === Object.keys(current).length
            && keys.every(function (key) {
              return Math.abs(Number(next[key].position) - Number(current[key] && current[key].position)) < 0.05
                && Math.abs(Number(next[key].trackHeight) - Number(current[key] && current[key].trackHeight)) < 0.05
                && Math.abs(Number(next[key].expandedX) - Number(current[key] && current[key].expandedX)) < 0.05;
            })
          ) return current;
          return next;
        });
      });
    }
    measure();
    rail.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    var observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    if (observer) {
      observer.observe(rail);
      observer.observe(track);
      rail.querySelectorAll(".wbc-chat-list, .wbc-chat-list-primary, .wbc-chat-card[data-chat-id]").forEach(function (element) {
        observer.observe(element);
      });
    }
    return function () {
      if (frame) cancelAnimationFrame(frame);
      rail.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
      if (observer) observer.disconnect();
    };
  }, [collapsed, projectId, visibleTrackLayoutKey, renderedRailMotionPhase, railDragActive]);

  var statusTrackItems = wbcConversationTrackPositions(orderedChats.map(function (chat, index) {
    return {
      chat: chat,
      index: index,
      state: wbcConversationTrackState(chat, runningChatIds, newResultChatIds),
    };
  }).filter(function (item) { return !!item.state; }), orderedChats.length, trackGeometryByChatId);

  function renderRailItem(item) {
    if (item.kind === "group") return renderGroupFrame(item.group, item.chats);
    var chat = item.chat;
    var isNewGroupTarget = !!(
      dragState
      && dragState.mode === "group"
      && dragState.targetId === String(chat.id)
      && !wbcFindChatGroup(groups, chat.id)
    );
    if (!isNewGroupTarget) return renderChatCard(chat);
    var movingChat = chatMap.get(String(dragState.movingId));
    return (
      <section
        key={"drop-group:" + chat.id}
        className="wbc-chat-group wbc-chat-group-preview drop-ready"
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatDrag(event)) return;
          event.preventDefault();
          event.stopPropagation();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          updateDragState({
            movingId: dragState.movingId,
            targetId: String(chat.id),
            targetGroupId: "",
            edge: "center",
            mode: "group",
          });
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatDrag(event)) return;
          event.preventDefault();
          event.stopPropagation();
          dropCommittedRef.current = true;
          commitGroupDrop(dragState.movingId, String(chat.id));
          setDragState(null);
        }}
      >
        <header className="wbc-chat-group-head">
          <span className="wbc-chat-group-toggle">
            <span className="wbc-chat-group-leading-chevron expanded" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            <span className="wbc-chat-group-count">2</span>
            <b>{wbcT("workbenchChat.newGroup", "New chat group")}</b>
          </span>
        </header>
        <div className="wbc-chat-group-children">
          {renderChatCard(chat, { insideGroup: true })}
          {renderDropClone(movingChat)}
        </div>
        <span className="wbc-chat-group-drop-hint">{WBC_ICONS.copy}{wbcT("workbenchChat.releaseToGroup", "Release to create a chat group")}</span>
      </section>
    );
  }

  function renderRailSection(id, label, icon, items) {
    if (!items.length) return null;
    return (
      <section key={id} className={"wbc-rail-section wbc-rail-section-" + id}>
        <header className="wbc-rail-section-label">
          {icon ? <span aria-hidden="true">{icon}</span> : null}
          <b>{label}</b>
        </header>
        <div className="wbc-rail-section-items">
          {items.map(renderRailItem)}
        </div>
      </section>
    );
  }

  useWbcEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = window.CyreneUI.require("uiSurface");
    var unregister = [];
    unregister.push(uiSurface.register({
        node_id: "new_chat",
        parent_id: "root",
        scope: "main",
        order: 30,
        get_element: function () { return newChatButtonRef.current; },
        get_node: function () { return { role: "button", name: wbcT("workbenchChat.newChat", "New chat") }; },
        actions: [{
          action_id: "invoke", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"],
          outcome: { effect: "creates_and_opens_surface", target_scope: "chat", inspect_after: true },
        }],
        handlers: {
          invoke: function () {
            return Promise.resolve(onCreate && onCreate()).then(function (chat) {
              if (chat && chat.id) wbcNotifyAgentChatFlow("created", chat.id);
              return chat && chat.id ? { chat_id: String(chat.id), created_by_agent: true } : {};
            });
          },
        },
      }));
    unregister.push(uiSurface.register({
        node_id: "chat_search_input",
        parent_id: "root",
        scope: "main",
        order: 40,
        get_element: function () { return chatSearchRef.current; },
        get_node: function () {
          return {
            role: "searchbox",
            name: wbcT("workbenchChat.searchCurrentProject", "Search current project..."),
            value_summary: query,
            state: {
              project_id: String(projectId || ""),
              query_length: String(query || "").length,
              result_count: filtered.length,
            },
          };
        },
        actions: [
          { action_id: "set_value", kind: "set_value", risk: "R1", gesture_aliases: ["text_input"], input_schema: { value: "text<=500" } },
          { action_id: "clear_value", kind: "set_value", risk: "R1", gesture_aliases: ["semantic_clear"], input_schema: {} },
        ],
        handlers: {
          set_value: function (input) { setQuery(String(input.value || "")); },
          clear_value: function () { setQuery(""); },
        },
      }));
    var visibleChatIds = wbcViewportChatIds(chatListRef.current);
    var visibleChats = visibleChatIds.map(function (chatId) { return chatMap.get(chatId); }).filter(Boolean);
    unregister.push(uiSurface.register({
      node_id: "chat_list",
      parent_id: "root",
      scope: "main",
      order: 100,
      get_element: function () { return chatListRef.current; },
      get_node: function () {
        var list = chatListRef.current;
        var scrollTop = Number(list && list.scrollTop || 0);
        var scrollHeight = Number(list && list.scrollHeight || 0);
        var clientHeight = Number(list && list.clientHeight || 0);
        return {
          role: "list",
          name: wbcT("rail.chat", "Chats"),
          state: {
            total_count: filtered.length,
            visible_count: wbcViewportChatIds(list).length,
            query: query,
            scroll_top: scrollTop,
            scroll_height: scrollHeight,
            client_height: clientHeight,
            can_page_previous: scrollTop > 1,
            can_page_next: scrollTop + clientHeight < scrollHeight - 1 || recentOverflowCount > 0,
            hidden_result_count: recentOverflowCount,
            all_recent_rendered: showAllRecent,
          },
        };
      },
      actions: [
        { action_id: "scroll_page", kind: "scroll", risk: "R1", gesture_aliases: ["wheel", "keyboard"], input_schema: { delta: "-2000..2000" } },
        { action_id: "page_previous", kind: "invoke", risk: "R1", gesture_aliases: ["page_up", "keyboard"] },
        { action_id: "page_next", kind: "invoke", risk: "R1", gesture_aliases: ["page_down", "keyboard"] },
        { action_id: "search", kind: "set_value", risk: "R1", gesture_aliases: ["text_input"], input_schema: { value: "text<=500" } },
        { action_id: "clear_search", kind: "set_value", risk: "R1", gesture_aliases: ["semantic_clear"], input_schema: {} },
      ].concat(recentOverflowCount > 0 ? [
        { action_id: "show_all_results", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] },
      ] : []),
      handlers: {
        scroll_page: function (input) {
          var list = chatListRef.current;
          if (list && typeof list.scrollBy === "function") list.scrollBy({ top: Number(input.delta || 0), behavior: "auto" });
          setUiViewportRevision(function (value) { return value + 1; });
        },
        page_previous: function () {
          var list = chatListRef.current;
          if (list && typeof list.scrollBy === "function") list.scrollBy({ top: -Math.max(1, Number(list.clientHeight || 0) * 0.9), behavior: "auto" });
          setUiViewportRevision(function (value) { return value + 1; });
        },
        page_next: function () {
          var list = chatListRef.current;
          if (recentOverflowCount > 0 && list && Number(list.scrollTop || 0) + Number(list.clientHeight || 0) >= Number(list.scrollHeight || 0) - 1) {
            setShowAllRecent(true);
            setUiViewportRevision(function (value) { return value + 1; });
            return;
          }
          if (list && typeof list.scrollBy === "function") list.scrollBy({ top: Math.max(1, Number(list.clientHeight || 0) * 0.9), behavior: "auto" });
          setUiViewportRevision(function (value) { return value + 1; });
        },
        search: function (input) { setQuery(String(input.value || "")); },
        clear_search: function () { setQuery(""); },
        show_all_results: function () { setShowAllRecent(true); },
      },
    }));
    visibleChats.forEach(function (chat) {
      var chatId = String(chat.id || "");
      var nodeId = "chat_" + chatId;
      unregister.push(uiSurface.register({
        node_id: nodeId,
        parent_id: "chat_list",
        scope: "main",
        order: 1000 + visibleChats.indexOf(chat),
        get_node: function () {
          return chatId && wbcViewportChatIds(chatListRef.current).indexOf(chatId) >= 0 ? {
            role: "listitem",
            name: chat.title || wbcT("workbenchChat.newChat", "New chat"),
            value_summary: chat.preview || "",
            state: {
              selected: chatId === String(activeChatId || ""),
              pinned: (Array.isArray(pinnedChatIds) ? pinnedChatIds : []).map(String).indexOf(chatId) >= 0,
              grouped: !!wbcFindChatGroup(groups, chatId),
            },
          } : null;
        },
        actions: [
          { action_id: "open", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] },
          { action_id: "open_menu", kind: "open_menu", risk: "R1", gesture_aliases: ["context_menu", "more_button"] },
        ],
        handlers: {
          open: function () { setMenuId(""); return onSelect(chatId); },
          open_menu: function () { setMenuId(chatId); },
        },
      }));
    });
    if (menuId && String(menuId).indexOf("group:") !== 0) {
      var menuChat = chatMap.get(String(menuId));
      if (menuChat) {
        var menuChatId = String(menuChat.id || "");
        var pinned = (Array.isArray(pinnedChatIds) ? pinnedChatIds : []).map(String).indexOf(menuChatId) >= 0;
        uiSurface.setScope("chat_menu");
        unregister.push(uiSurface.register({
          node_id: "chat_context_menu",
          parent_id: "root",
          scope: "chat_menu",
          order: 10,
          get_node: function () { return { role: "menu", name: menuChat.title || wbcT("workbenchChat.newChat", "New chat") }; },
          actions: [{ action_id: "dismiss", kind: "dismiss", risk: "R1", gesture_aliases: ["escape_key", "scrim"] }],
          handlers: { dismiss: function () { setMenuId(""); } },
        }));
        unregister.push(uiSurface.register({
          node_id: "chat_menu_pin",
          parent_id: "chat_context_menu",
          scope: "chat_menu",
          order: 20,
          get_node: function () { return { role: "menuitem", name: pinned ? wbcT("workbenchChat.unpin", "Unpin chat") : wbcT("workbenchChat.pin", "Pin chat") }; },
          actions: [{ action_id: "invoke", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] }],
          handlers: { invoke: function () { setMenuId(""); return onTogglePinned && onTogglePinned(menuChat, !pinned); } },
        }));
        if (!menuChat.legacy) {
          unregister.push(uiSurface.register({
            node_id: "chat_menu_rename",
            parent_id: "chat_context_menu",
            scope: "chat_menu",
            order: 30,
            get_node: function () { return { role: "menuitem", name: wbcT("workbenchChat.rename", "Rename chat") }; },
            actions: [{ action_id: "invoke", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] }],
            handlers: { invoke: function () { setMenuId(""); setRenameChat(menuChat); } },
          }));
        }
        unregister.push(uiSurface.register({
          node_id: "chat_menu_to_task",
          parent_id: "chat_context_menu",
          scope: "chat_menu",
          order: 40,
          get_node: function () { return { role: "menuitem", name: wbcT(toTaskBusy ? "workbenchChat.toTaskBusy" : "workbenchChat.toTask", toTaskBusy ? "Analyzing chat..." : "Convert to task"), state: { disabled: !!toTaskBusy } }; },
          actions: toTaskBusy ? [] : [{ action_id: "invoke", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] }],
          handlers: { invoke: function () { setMenuId(""); return onToTask && onToTask(menuChatId); } },
        }));
        unregister.push(uiSurface.register({
          node_id: "chat_menu_delete",
          parent_id: "chat_context_menu",
          scope: "chat_menu",
          order: 50,
          get_node: function () { return { role: "menuitem", name: wbcT("workbenchChat.delete", "Delete chat") }; },
          actions: [{ action_id: "invoke", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] }],
          handlers: { invoke: function () { setMenuId(""); return onDelete && onDelete(menuChatId); } },
        }));
      }
    } else if (uiSurface.getScope() === "chat_menu") {
      uiSurface.setScope("main");
    }
    return function () { unregister.forEach(function (remove) { remove(); }); };
  }, [projectId, defaultOrderKey, filtered, query, collapsed, groups, menuId, activeChatId, pinnedChatIds, onSelect, onCreate, onDelete, onToTask, toTaskBusy, onTogglePinned, showAllRecent, recentOverflowCount, uiViewportRevision]);

  var taskMap = new Map((Array.isArray(tasks) ? tasks : []).map(function (task) {
    return [String(task.id), task];
  }));
  var orderedTasks = wbcOrderChatsByPinned(wbcNormalizeChatOrder(taskDefaultOrder, taskOrder).map(function (id) {
    return taskMap.get(id);
  }).filter(Boolean), pinnedTaskIds).filter(function (task) {
    var normalized = query.trim().toLowerCase();
    return !normalized
      || String(task.title || "").toLowerCase().indexOf(normalized) >= 0
      || String(task.goal || task.summary || "").toLowerCase().indexOf(normalized) >= 0;
  });
  var pinnedTaskIdSet = new Set((Array.isArray(pinnedTaskIds) ? pinnedTaskIds : []).map(function (id) { return String(id || ""); }));
  var pinnedTasks = orderedTasks.filter(function (task) { return pinnedTaskIdSet.has(String(task.id)); });
  var recentTasks = orderedTasks.filter(function (task) { return !pinnedTaskIdSet.has(String(task.id)); });

  function taskRailVisualState(task) {
    var raw = String(task && task.status || "idle").toLowerCase();
    var failed = ["failed", "cancelled", "blocked"].indexOf(raw) >= 0;
    var attention = ["waiting_for_user", "waiting_for_approval", "review"].indexOf(raw) >= 0 || !!(task && task.pendingQuestion);
    var planning = raw === "planning";
    var running = ["running", "paused"].indexOf(raw) >= 0 || !!(task && task.agentBusy);
    var completed = ["completed", "done", "skipped"].indexOf(raw) >= 0;
    var rawSummary = task && task.summary;
    var summaryText = typeof rawSummary === "string"
      ? rawSummary
      : rawSummary && typeof rawSummary === "object"
        ? String(rawSummary.text || rawSummary.summary || "")
        : "";
    return {
      tone: failed ? " status-failed" : attention ? " status-attention" : completed ? " status-completed" : running ? " status-running" : planning ? " status-planning" : "",
      icon: failed ? WBC_ICONS.errorCircle : attention ? WBC_ICONS.alert : completed ? WBC_ICONS.check : running ? WBC_ICONS.running : planning ? WBC_ICONS.planning : WBC_ICONS.file,
      preview: summaryText || String(task && task.goal || "") || wbcT("task.summaryFallback", "The agent will create a summary while this task runs."),
    };
  }

  function storeTaskOrder(nextOrder) {
    var normalized = wbcNormalizeChatOrder(taskDefaultOrder, nextOrder);
    setTaskOrder(normalized);
    try { localStorage.setItem(WBC_TASK_ORDER_PREFIX + String(projectId || ""), JSON.stringify(normalized)); } catch (e) {}
  }

  function renderTaskCard(task) {
    var id = String(task.id || "");
    var visual = taskRailVisualState(task);
    var isMenuOpen = menuId === "task:" + id;
    var isPinned = pinnedTaskIdSet.has(id);
    return <div
      key={id}
      role="button"
      tabIndex={0}
      draggable="true"
      data-task-id={id}
      data-cyrene-context-menu="true"
      className={"wbc-chat-card wbc-task-card"
        + (String(activeTaskId || "") === id ? " active" : "")
        + (isMenuOpen ? " menu-open" : "")
        + (taskDragId === id ? " dragging" : "")
        + visual.tone}
      onClick={function () { setMenuId(""); if (onSelectTask) onSelectTask(id); }}
      onKeyDown={function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          if (onSelectTask) onSelectTask(id);
        }
      }}
      onContextMenu={function (event) {
        event.preventDefault();
        event.stopPropagation();
        setMenuId("task:" + id);
      }}
      onDragStart={function (event) {
        if (event.target && event.target.closest && event.target.closest("button")) {
          event.preventDefault();
          return;
        }
        setMenuId("");
        setTaskDragId(id);
        wbcSetTaskDrag(event, task, projectId);
        if (event.dataTransfer) prepareRailDragImage(event.currentTarget, event.dataTransfer, event.clientX, event.clientY);
      }}
      onDragOver={function (event) {
        if (!taskDragId || taskDragId === id || !wbcHasTaskDrag(event)) return;
        event.preventDefault();
        event.stopPropagation();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
        var rect = event.currentTarget.getBoundingClientRect();
        var edge = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
        setTaskOrder(function (current) { return wbcMoveChatOrder(current, taskDragId, id, edge); });
      }}
      onDrop={function (event) {
        if (!taskDragId || !wbcHasTaskDrag(event)) return;
        event.preventDefault();
        event.stopPropagation();
        storeTaskOrder(taskOrder);
        setTaskDragId("");
      }}
      onDragEnd={function () { storeTaskOrder(taskOrder); setTaskDragId(""); }}
    >
      <span className="wbc-chat-card-top">
        <span className="wbc-chat-row-icon" aria-hidden="true">{visual.icon}</span>
        <span className="wbc-chat-card-title">
          {isPinned && <span className="wbc-chat-card-pin" title={wbcT("task.pinned", "Pinned")} aria-label={wbcT("task.pinned", "Pinned")}>{WBC_ICONS.pin}</span>}
          <b><WbcHoverMarquee text={task.title || wbcT("workbench.page.task", "Task")} /></b>
        </span>
        <span className="wbc-chat-card-right">
          <time className="wbc-chat-card-time">{wbcFormatTime(task.updatedAt || task.createdAt)}</time>
          <span className="wbc-chat-card-actions">
            <button type="button" className="wb-card-menu-btn wbc-chat-card-menu-btn" onClick={function (event) {
              event.stopPropagation();
              setMenuId(isMenuOpen ? "" : "task:" + id);
            }} aria-label={wbcT("common.moreActions", "More actions")}>{WBC_ICONS.dots}</button>
            {isMenuOpen && <div className="wb-card-menu" role="menu">
              <button type="button" role="menuitem" className="wbc-chat-pin-action" onClick={function (event) {
                event.stopPropagation();
                setMenuId("");
                if (onTogglePinnedTask) onTogglePinnedTask(task, !isPinned);
              }}><span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.pin}</span><span>{isPinned ? wbcT("task.unpin", "Unpin task") : wbcT("task.pin", "Pin task")}</span></button>
              <button type="button" role="menuitem" className="wbc-chat-menu-action" onClick={function (event) {
                event.stopPropagation();
                setMenuId("");
                setRenameTask(task);
              }}><span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span><span>{wbcT("task.rename", "Rename task")}</span></button>
              <button type="button" role="menuitem" className="wbc-chat-menu-action danger" onClick={function (event) {
                event.stopPropagation();
                setMenuId("");
                if (onDeleteTask) onDeleteTask(task);
              }}><span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.trash}</span><span>{wbcT("rail.deleteTask", "Delete task")}</span></button>
            </div>}
          </span>
        </span>
      </span>
      <span className="wbc-chat-card-preview"><WbcHoverMarquee text={visual.preview} /></span>
    </div>;
  }

  var terminalMap = new Map((Array.isArray(terminals) ? terminals : []).map(function (terminal) {
    return [String(terminal.id), terminal];
  }));
  var orderedTerminals = wbcNormalizeChatOrder(terminalDefaultOrder, terminalOrder).map(function (id) {
    return terminalMap.get(id);
  }).filter(Boolean).filter(function (terminal) {
    return !normalizedQuery
      || String(terminal.title || "").toLowerCase().indexOf(normalizedQuery) >= 0
      || String(terminal.cwd || "").toLowerCase().indexOf(normalizedQuery) >= 0;
  });
  var terminalPinnedSet = new Set(terminalPinnedIds.map(String));
  var pinnedTerminals = orderedTerminals.filter(function (terminal) { return terminalPinnedSet.has(String(terminal.id)); });
  var recentTerminals = orderedTerminals.filter(function (terminal) { return !terminalPinnedSet.has(String(terminal.id)); });

  function storeTerminalOrder(nextOrder) {
    var normalized = wbcNormalizeChatOrder(terminalDefaultOrder, nextOrder);
    setTerminalOrder(normalized);
    if (onUpdateTerminalLayout) onUpdateTerminalLayout(normalized, terminalPinnedIds);
  }

  function toggleTerminalPinned(terminalId) {
    var id = String(terminalId || "");
    var next = terminalPinnedSet.has(id)
      ? terminalPinnedIds.filter(function (candidate) { return String(candidate) !== id; })
      : terminalPinnedIds.concat([id]);
    setTerminalPinnedIds(next);
    if (onUpdateTerminalLayout) onUpdateTerminalLayout(terminalOrder, next);
  }

  function renderTerminalCard(terminal) {
    var id = String(terminal.id || "");
    var isMenuOpen = menuId === "terminal:" + id;
    var isPinned = terminalPinnedSet.has(id);
    var running = terminal.status === "running" || terminal.status === "starting";
    return <div
      key={id}
      role="button"
      tabIndex={0}
      draggable="true"
      data-terminal-id={id}
      data-cyrene-context-menu="true"
      className={"wbc-chat-card wbc-terminal-card"
        + (String(activeTerminalId || "") === id ? " active" : "")
        + (isMenuOpen ? " menu-open" : "")
        + (terminalDragId === id ? " dragging" : "")
        + (running ? " status-running" : " status-completed")}
      onClick={function () { setMenuId(""); if (onOpenTerminal) onOpenTerminal(id); }}
      onKeyDown={function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          if (onOpenTerminal) onOpenTerminal(id);
        }
      }}
      onContextMenu={function (event) {
        event.preventDefault();
        event.stopPropagation();
        setMenuId("terminal:" + id);
      }}
      onDragStart={function (event) {
        if (event.target && event.target.closest && event.target.closest("button")) {
          event.preventDefault();
          return;
        }
        setMenuId("");
        setTerminalDragId(id);
        wbcSetResourceDrag(event, { kind: "terminal", terminalId: id, title: terminal.title || "Terminal" });
      }}
      onDragOver={function (event) {
        if (!terminalDragId || terminalDragId === id) return;
        event.preventDefault();
        event.stopPropagation();
        var rect = event.currentTarget.getBoundingClientRect();
        var edge = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
        setTerminalOrder(function (current) { return wbcMoveChatOrder(current, terminalDragId, id, edge); });
      }}
      onDrop={function (event) {
        if (!terminalDragId) return;
        event.preventDefault();
        event.stopPropagation();
        storeTerminalOrder(terminalOrder);
        setTerminalDragId("");
      }}
      onDragEnd={function () { storeTerminalOrder(terminalOrder); setTerminalDragId(""); }}
    >
      <span className="wbc-chat-card-top">
        <span className="wbc-chat-row-icon" aria-hidden="true">{WBC_ICONS.slash}</span>
        <span className="wbc-chat-card-title">
          {isPinned && <span className="wbc-chat-card-pin" aria-hidden="true">{WBC_ICONS.pin}</span>}
          <b><WbcHoverMarquee text={terminal.title || wbcT("terminal.title", "Terminal")} /></b>
        </span>
        <span className="wbc-chat-card-right">
          <time className="wbc-chat-card-time">{wbcFormatTime(terminal.updatedAt || terminal.createdAt)}</time>
          <span className="wbc-chat-card-actions">
            <button type="button" className="wb-card-menu-btn wbc-chat-card-menu-btn" onClick={function (event) {
              event.stopPropagation();
              setMenuId(isMenuOpen ? "" : "terminal:" + id);
            }} aria-label={wbcT("common.moreActions", "More actions")}>{WBC_ICONS.dots}</button>
            {isMenuOpen && <div className="wb-card-menu" role="menu">
              <button type="button" role="menuitem" className="wbc-chat-pin-action" onClick={function (event) { event.stopPropagation(); setMenuId(""); toggleTerminalPinned(id); }}>
                <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.pin}</span>
                <span>{isPinned ? wbcT("terminal.unpin", "Unpin terminal") : wbcT("terminal.pin", "Pin terminal")}</span>
              </button>
              <button type="button" role="menuitem" className="wbc-chat-menu-action" onClick={function (event) { event.stopPropagation(); setMenuId(""); setRenameTerminalItem(terminal); }}>
                <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span>
                <span>{wbcT("terminal.rename", "Rename terminal")}</span>
              </button>
              <button type="button" role="menuitem" className="wbc-chat-menu-action danger" onClick={function (event) { event.stopPropagation(); setMenuId(""); if (onDeleteTerminal) onDeleteTerminal(id); }}>
                <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.trash}</span>
                <span>{wbcT("terminal.delete", "Delete terminal")}</span>
              </button>
            </div>}
          </span>
        </span>
      </span>
      <span className="wbc-chat-card-preview">
        {running ? <i className="wbc-running-dot" /> : null}
        <WbcHoverMarquee text={running ? String(terminal.cwd || "") : wbcT("terminal.exited", "Process exited")} />
      </span>
    </div>;
  }

  function renderTerminalSection(id, label, items) {
    if (!items.length) return null;
    return <section className={"wbc-rail-section wbc-rail-section-" + id + " wbc-terminal-section"}>
      <header className="wbc-rail-section-label">{id === "pinned" ? <span aria-hidden="true">{WBC_ICONS.pin}</span> : null}<b>{label}</b></header>
      <div className="wbc-rail-section-items">{items.map(renderTerminalCard)}</div>
    </section>;
  }

  function openUnifiedFileResult(entry) {
    if (!entry) return;
    if (entry.kind === "directory") {
      setQuery("");
      setMenuId("");
      setTerminalToolsExpanded(false);
      setFilePath(entry.path);
      setFileToolsExpanded(true);
      return;
    }
    if (onOpenFile) onOpenFile(entry);
  }

  function renderUnifiedFileResult(entry) {
    var visual = wbcProjectFileVisual(entry);
    return <button
      key={entry.path}
      type="button"
      className={"workbench-project-file-row wbc-unified-search-file is-" + visual.kind}
      onClick={function () { openUnifiedFileResult(entry); }}
    >
      <span className="workbench-project-file-icon" aria-hidden="true">{visual.icon}</span>
      <span className="wbc-unified-search-file-copy">
        <b>{entry.name}</b>
        <small>{entry.path}</small>
      </span>
      {entry.kind === "directory" && <span className="workbench-project-file-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>}
    </button>;
  }

  var unifiedSearchActive = normalizedQuery.length > 0;
  var unifiedSearchResultCount = filtered.length + orderedTasks.length + globalFileEntries.length + orderedTerminals.length;

  return (
    <aside ref={railRef} className={"wbc-rail workbench-integrated-rail"
      + (collapsed ? " is-collapsed" : "")
      + (fileToolsExpanded || terminalToolsExpanded ? " has-expanded-project-tool" : "")
      + (renderedRailMotionPhase ? (" is-status-" + renderedRailMotionPhase) : "")}>
      <div className="wbc-rail-glass">
        <div className="wbc-nav-card">
          <div className="wbc-nav-card-head workbench-integrated-rail-head workbench-integrated-rail-search-head">
            {!collapsed && (
              <button type="button" className="workbench-rail-mode-toggle" onClick={toggleRailMode} aria-label={wbcT("rail.toggleMode", "Switch rail mode")}>
                {railMode === "task"
                      ? wbcT("workbench.page.task", "Tasks")
                      : wbcT("workbench.page.chat", "Chats")}
              </button>
            )}
            {!collapsed && (
              <div className="wbc-search">
                <span className="wbc-search-icon">{WBC_ICONS.search}</span>
                <input
                  ref={chatSearchRef}
                  data-cyrene-node-id="chat_search_input"
                  value={query}
                  onChange={function (e) { setQuery(e.target.value); }}
                  placeholder={wbcT("rail.searchEverythingShort", "Search all")}
                  aria-label={wbcT("rail.searchEverything", "Search chats, tasks, files, and terminals")}
                />
              </div>
            )}
            {!collapsed && railMode === "chat" && (
              <button
                ref={newChatButtonRef}
                data-cyrene-node-id="new_chat"
                type="button"
                className="wbc-project-new-chat"
                onClick={onCreate}
                title={wbcT("workbenchChat.newChat", "New chat")}
                aria-label={wbcT("workbenchChat.newChat", "New chat")}
              >{WBC_ICONS.plus}</button>
            )}
            {!collapsed && railMode === "task" && (
              <button
                type="button"
                className="wbc-project-new-chat"
                onClick={onCreateTask}
                title={wbcT("rail.newTask", "New task")}
                aria-label={wbcT("rail.newTask", "New task")}
              >{WBC_ICONS.plus}</button>
            )}
            {collapseControl || (
              <button
                type="button"
                className="workbench-sidebar-collapse-control"
                onClick={onToggleCollapsed}
                title={collapsed ? wbcT("rail.expand", "Expand sidebar") : wbcT("rail.collapse", "Collapse sidebar")}
                aria-label={collapsed ? wbcT("rail.expand", "Expand sidebar") : wbcT("rail.collapse", "Collapse sidebar")}
              >{WBC_ICONS.sidebar}</button>
            )}
          </div>

        </div>
      </div>
      {menuId && <div className="wb-card-menu-scrim" onClick={function () { setMenuId(""); }} />}
      {unifiedSearchActive ? (
        <div
          ref={chatListRef}
          data-cyrene-node-id="chat_list"
          className={"wbc-chat-list workbench-integrated-rail-body wbc-unified-search-results" + (globalFilesLoading ? " is-loading" : "") + (!globalFilesLoading && unifiedSearchResultCount === 0 ? " is-empty" : "") + (menuId ? " menu-active" : "")}
        >
          <div className="wbc-chat-list-primary">
            {filtered.length ? <section className="wbc-rail-section wbc-unified-search-section is-chat">
              <header className="wbc-rail-section-label"><b>{wbcT("workbench.page.chat", "Chats")}</b><span>{filtered.length}</span></header>
              <div className="wbc-rail-section-items">{filtered.map(function (chat) { return renderChatCard(chat); })}</div>
            </section> : null}
            {orderedTasks.length ? <section className="wbc-rail-section wbc-unified-search-section is-task">
              <header className="wbc-rail-section-label"><b>{wbcT("workbench.page.task", "Tasks")}</b><span>{orderedTasks.length}</span></header>
              <div className="wbc-rail-section-items">{orderedTasks.map(renderTaskCard)}</div>
            </section> : null}
            {globalFileEntries.length ? <section className="wbc-rail-section wbc-unified-search-section is-file">
              <header className="wbc-rail-section-label"><b>{wbcT("rail.files", "Files")}</b><span>{globalFileEntries.length}</span></header>
              <div className="wbc-rail-section-items wbc-unified-search-file-items">{globalFileEntries.map(renderUnifiedFileResult)}</div>
            </section> : null}
            {orderedTerminals.length ? <section className="wbc-rail-section wbc-unified-search-section is-terminal">
              <header className="wbc-rail-section-label"><b>{wbcT("terminal.title", "Terminal")}</b><span>{orderedTerminals.length}</span></header>
              <div className="wbc-rail-section-items">{orderedTerminals.map(renderTerminalCard)}</div>
            </section> : null}
            {globalFilesLoading ? <div className="workbench-muted wbc-rail-loading" role="status">{wbcT("rail.searchingEverything", "Searching project...")}</div> : null}
            {!globalFilesLoading && globalFilesError ? <div className="workbench-error wbc-unified-search-warning">{globalFilesError}</div> : null}
            {!globalFilesLoading && unifiedSearchResultCount === 0 ? <div className="workbench-muted wbc-rail-empty">{wbcT("rail.noUnifiedMatches", "No matching chats, tasks, files, or terminals.")}</div> : null}
          </div>
        </div>
      ) : railMode === "task" ? (
        <div
          className={"wbc-chat-list workbench-integrated-rail-body wbc-task-list" + (loading ? " is-loading" : "") + (!loading && orderedTasks.length === 0 ? " is-empty" : "") + (menuId ? " menu-active" : "")}
          onDragOver={function (event) {
            if (!taskDragId || !wbcHasTaskDrag(event)) return;
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          }}
          onDrop={function (event) {
            if (!taskDragId || !wbcHasTaskDrag(event)) return;
            event.preventDefault();
            storeTaskOrder(taskOrder);
            setTaskDragId("");
          }}
        >
          <div className="wbc-chat-list-primary">
            {loading && !orderedTasks.length ? <div className="workbench-muted wbc-rail-loading" role="status">{wbcT("rail.loadingTasks", "Loading tasks...")}</div> : null}
            {!loading && !orderedTasks.length ? <div className="workbench-muted wbc-rail-empty">{query ? wbcT("rail.noMatchingTasks", "No matching tasks.") : wbcT("rail.noTasks", "No tasks yet.")}</div> : null}
            {pinnedTasks.length ? <section className="wbc-rail-section wbc-rail-section-pinned wbc-task-section">
              <header className="wbc-rail-section-label"><span aria-hidden="true">{WBC_ICONS.pin}</span><b>{wbcT("workbenchChat.pinnedSection", "Pinned")}</b></header>
              <div className="wbc-rail-section-items">{pinnedTasks.map(renderTaskCard)}</div>
            </section> : null}
            {recentTasks.length ? <section className="wbc-rail-section wbc-rail-section-recent wbc-task-section">
              <header className="wbc-rail-section-label"><b>{wbcT("workbenchChat.recent", "Recent")}</b></header>
              <div className="wbc-rail-section-items">{recentTasks.map(renderTaskCard)}</div>
            </section> : null}
          </div>
        </div>
      ) : (
      <div
        ref={chatListRef}
        data-cyrene-node-id="chat_list"
        className={"wbc-chat-list workbench-integrated-rail-body" + (loading ? " is-loading" : "") + (!loading && visibleRailItemCount === 0 ? " is-empty" : "") + (menuId ? " menu-active" : "") + (!loading && visibleGroupRailItems.length ? " has-groups" : "")}
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          event.preventDefault();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          var overPrimarySurface = !!(
            event.target.closest
            && event.target.closest(".wbc-chat-list-primary")
            && !event.target.closest(".wbc-chat-card, .wbc-chat-group, .wbc-rail-show-all")
          );
          if (event.target === event.currentTarget || overPrimarySurface) {
            if (dragState.dragKind === "group") {
              var trailingOrder = wbcMoveChatOrderBlock(order, dragState.movingIds, [], "after");
              if (trailingOrder.join("|") !== order.join("|")) setOrder(trailingOrder);
            }
            updateDragState({
              movingId: dragState.movingId,
              targetId: "",
              targetGroupId: "",
              edge: "after",
              mode: dragState.dragKind === "group" ? "group-reorder" : "reorder",
            });
          }
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          event.preventDefault();
          dropCommittedRef.current = true;
          if (dragState.dragKind === "group") {
            commitGroupOrder(order, groups.find(function (candidate) {
              return candidate.id === dragState.movingGroupId;
            }));
            setDragState(null);
            return;
          }
          commitOrder(order, dragState.movingId);
          if (dragState.sourceGroupId) commitUngroupDrop(dragState.movingId);
          setDragState(null);
        }}
      >
        <div className="wbc-chat-list-primary">
          {loading && (
            <div className="workbench-muted wbc-rail-loading" role="status">
              {wbcT("workbenchChat.loading", "Loading chats...")}
            </div>
          )}
          {!loading && visibleRailItemCount === 0 && (
            <div className="workbench-muted wbc-rail-empty">{query
              ? wbcT("workbenchChat.noMatches", "No matching chats.")
              : wbcT("workbenchChat.emptyFilter", "No chats in this section.")}</div>
          )}
          {!loading && renderRailSection("pinned", wbcT("workbenchChat.pinnedSection", "Pinned"), WBC_ICONS.pin, visiblePinnedRailItems)}
          {!loading && renderRailSection("recent", wbcT("workbenchChat.recent", "Recent"), null, visibleRecentRailItems)}
          {!loading && recentOverflowCount > 0 && (
            <button type="button" className="wbc-rail-show-all" onClick={function () { setShowAllRecent(true); }}>
              {wbcT("workbenchChat.showAllRecent", "Show all {count}", { count: recentRailItems.length })}
              {WBC_ICONS.chevronRight}
            </button>
          )}
        </div>
        {!loading && visibleGroupRailItems.length > 0 && (
          <div className="wbc-chat-list-group-region">
            {renderRailSection("groups", wbcT("workbenchChat.groups", "Chat groups"), null, visibleGroupRailItems)}
          </div>
        )}
        <span className="wbc-sr-only" aria-live="polite">{announcement}</span>
      </div>
      )}
      {railMode === "chat" && !unifiedSearchActive && <div ref={trackRef} className="wbc-conversation-status-track" role="navigation" aria-label={wbcT("workbenchChat.track.label", "Conversation status map")}>
        {!railDragActive && statusTrackItems.map(function (item) {
          var chat = item.chat;
          var previewOpen = previewChatId === String(chat.id);
          var answerState = previewAnswerState.chatId === String(chat.id)
            ? previewAnswerState
            : { busy: false, result: "", error: "" };
          var markerGlyph = item.state.kind === "attention"
            ? WBC_ICONS.alert
            : item.state.kind === "failed"
              ? WBC_ICONS.errorCircle
              : item.state.kind === "result"
                ? WBC_ICONS.check
                : WBC_ICONS.running;
          var alignment = item.position < 24 ? " align-top" : item.position > 76 ? " align-bottom" : " align-center";
          var title = item.state.label + " · " + (chat.title || wbcT("workbenchChat.newChat", "New chat"));
          return (
            <div
              key={chat.id}
              className={"wbc-conversation-status-anchor" + alignment + (item.measured ? " is-measured" : "") + (previewOpen ? " preview-open" : "")}
              style={{
                "--wbc-track-position": item.position + "%",
                "--wbc-track-expanded-position": item.expandedPosition + "%",
                "--wbc-track-expanded-x": (Number.isFinite(item.expandedX) ? item.expandedX : 29) + "px",
                "--wbc-track-collapse-y": (Number.isFinite(item.collapseY) ? item.collapseY : 0) + "px",
              }}
              onMouseEnter={function () { openStatusPreview(chat.id); }}
              onMouseMove={function () { if (!previewOpen) openStatusPreview(chat.id); }}
              onMouseLeave={closeStatusPreviewSoon}
              onFocusCapture={function () { openStatusPreview(chat.id); }}
              onBlurCapture={function (event) {
                if (event.currentTarget.contains(event.relatedTarget)) return;
                closeStatusPreviewSoon();
              }}
              onKeyDown={function (event) {
                if (event.key !== "Escape") return;
                event.stopPropagation();
                setPreviewChatId("");
                if (event.currentTarget.querySelector(".wbc-conversation-status-marker")) {
                  event.currentTarget.querySelector(".wbc-conversation-status-marker").focus();
                }
              }}
            >
              <button
                type="button"
                className={"wbc-conversation-status-marker is-" + item.state.kind + (item.state.urgent ? " is-urgent" : "")}
                aria-label={title}
                aria-expanded={previewOpen}
                onClick={function () {
                  setNewResultChatIds(function (existing) {
                    if (!existing[chat.id]) return existing;
                    var next = { ...existing };
                    delete next[chat.id];
                    return next;
                  });
                  onSelect(chat.id);
                }}
              >
                <span className="wbc-conversation-status-glyph" aria-hidden="true">{markerGlyph}</span>
                <span className="wbc-conversation-status-dot" aria-hidden="true"></span>
              </button>
              {previewOpen && (
                <WbcConversationStatusPreview
                  chat={chat}
                  state={item.state}
                  runtime={runtimeEngine && runtimeEngine.get ? runtimeEngine.get(chat.id) : null}
                  busy={answerState.busy}
                  result={answerState.result}
                  error={answerState.error}
                  onAnswer={function (questionId, answerText, resumeMode) {
                    answerFromStatusPreview(chat, questionId, answerText, resumeMode);
                  }}
                />
              )}
            </div>
          );
        })}
      </div>}
      {(railMode === "chat" || railMode === "task") && !collapsed ? (
        <section
          ref={projectToolsRef}
          className={"wbc-project-tools"
            + (fileToolsExpanded || terminalToolsExpanded ? " has-expanded-tool" : "")
            + (fileToolsExpanded ? " expanded-file" : "")
            + (terminalToolsExpanded ? " expanded-terminal" : "")}
          aria-label={wbcT("rail.projectTools", "Project tools")}
          onWheel={handleProjectToolWheel}
          onTouchStart={handleProjectToolTouchStart}
          onTouchMove={handleProjectToolTouchMove}
          onTouchEnd={resetProjectToolPull}
          onTouchCancel={resetProjectToolPull}
        >
          <header><span>{wbcT("rail.projectTools", "Project tools")}</span></header>
          {fileToolsExpanded ? (
            <div className="wbc-project-tool-inline-header is-file">
              {filePath === "." ? (
                <span className="wbc-project-tool-icon" aria-hidden="true">{WBC_ICONS.folder}</span>
              ) : (
                <button
                  type="button"
                  className="wbc-project-tool-directory-control"
                  aria-label={wbcT("common.back", "Back")}
                  onClick={function () {
                    fileDirectionRef.current = "back";
                    setFilePath(filePath.indexOf("/") === -1 ? "." : filePath.split("/").slice(0, -1).join("/"));
                  }}
                >{WBC_ICONS.chevronLeft}</button>
              )}
              <span className="wbc-project-tool-copy"><b>{wbcT("rail.files", "Files")}</b><small>{filePath === "." ? (projectName || ".") : filePath}</small></span>
              <button
                type="button"
                className="wbc-project-tool-inline-collapse"
                onClick={function () { setFileToolsExpanded(false); }}
                aria-label={wbcT("common.collapse", "Collapse")}
                aria-expanded="true"
                aria-controls="wbc-project-file-list"
              >{WBC_ICONS.chevronRight}</button>
            </div>
          ) : (
            <button
              type="button"
              onClick={function () {
                setMenuId("");
                setTerminalToolsExpanded(false);
                setFileToolsExpanded(true);
              }}
              aria-expanded="false"
              aria-controls="wbc-project-file-list"
            >
              <span className="wbc-project-tool-icon" aria-hidden="true">{WBC_ICONS.folder}</span>
              <span className="wbc-project-tool-copy"><b>{wbcT("rail.files", "Files")}</b><small>{projectName || "."}</small></span>
              <span className="wbc-project-tool-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            </button>
          )}
          <div
            id="wbc-project-file-list"
            className={"wbc-project-tool-expand wbc-project-file-expand" + (fileToolsExpanded ? " is-expanded" : "")}
            aria-hidden={!fileToolsExpanded}
          >
            <div className="wbc-project-tool-expand-inner">
              <div className="wbc-project-file-list">
                {filesLoading && !hasLoadedFiles && <div className="workbench-muted wbc-rail-loading">{wbcT("rail.loadingFiles", "Loading files...")}</div>}
                {!filesLoading && filesError && <div className="workbench-error wbc-rail-empty">{filesError}</div>}
                {visibleFiles.map(function (entry) {
                  var visual = wbcProjectFileVisual(entry);
                  var projectFile = wbcProjectFileResource(projectId, entry);
                  return <button
                    key={entry.path}
                    type="button"
                    className={"workbench-project-file-row is-" + visual.kind + " enter-" + fileDirection}
                    draggable={projectFile ? "true" : undefined}
                    onDragStart={projectFile ? function (event) { wbcStartFileDrag(event, projectFile); } : undefined}
                    onClick={function () {
                      if (entry.kind === "directory") {
                        fileDirectionRef.current = "forward";
                        setFilePath(entry.path);
                      } else if (onOpenFile) {
                        onOpenFile(entry);
                      }
                    }}>
                    <span className="workbench-project-file-icon" aria-hidden="true">{visual.icon}</span>
                    <b title={entry.path}>{entry.name}</b>
                    {entry.kind === "directory" && <span className="workbench-project-file-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>}
                  </button>;
                })}
                {!filesLoading && !filesError && visibleFiles.length === 0 && <div className="workbench-muted wbc-rail-empty">{query ? wbcT("rail.noMatchingFiles", "No matching files.") : wbcT("rail.noFiles", "This folder is empty.")}</div>}
              </div>
            </div>
          </div>
          {terminalToolsExpanded ? (
            <div className="wbc-project-tool-inline-header is-terminal">
              <span className="wbc-project-tool-icon" aria-hidden="true">{WBC_ICONS.slash}</span>
              <span className="wbc-project-tool-copy"><b>{wbcT("terminal.title", "Terminal")}</b><small>{terminals.filter(function (terminal) { return terminal && (terminal.status === "running" || terminal.status === "starting"); }).length} {wbcT("terminal.runningCountSuffix", "running")}</small></span>
              <button
                type="button"
                className="wbc-project-tool-inline-action"
                onClick={onCreateTerminal}
                title={wbcT("terminal.new", "New terminal")}
                aria-label={wbcT("terminal.new", "New terminal")}
              >{WBC_ICONS.plus}</button>
              <button
                type="button"
                className="wbc-project-tool-inline-collapse"
                onClick={function () { setMenuId(""); setTerminalToolsExpanded(false); }}
                aria-label={wbcT("common.collapse", "Collapse")}
                aria-expanded="true"
                aria-controls="wbc-project-terminal-list"
              >{WBC_ICONS.chevronRight}</button>
            </div>
          ) : (
            <button
              type="button"
              onClick={function () {
                setMenuId("");
                setFileToolsExpanded(false);
                setTerminalToolsExpanded(true);
              }}
              aria-expanded="false"
              aria-controls="wbc-project-terminal-list"
            >
              <span className="wbc-project-tool-icon" aria-hidden="true">{WBC_ICONS.slash}</span>
              <span className="wbc-project-tool-copy"><b>{wbcT("terminal.title", "Terminal")}</b><small>{terminals.filter(function (terminal) { return terminal && (terminal.status === "running" || terminal.status === "starting"); }).length} {wbcT("terminal.runningCountSuffix", "running")}</small></span>
              <span className="wbc-project-tool-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            </button>
          )}
          <div
            id="wbc-project-terminal-list"
            className={"wbc-project-tool-expand wbc-project-terminal-expand" + (terminalToolsExpanded ? " is-expanded" : "")}
            aria-hidden={!terminalToolsExpanded}
          >
            <div className="wbc-project-tool-expand-inner wbc-project-terminal-expand-inner">
              <div className={"wbc-project-terminal-list" + (terminalsLoading ? " is-loading" : "") + (menuId ? " menu-active" : "")}>
                {terminalsLoading && !orderedTerminals.length ? <div className="workbench-muted wbc-rail-loading" role="status">{wbcT("terminal.loading", "Loading terminals...")}</div> : null}
                {!terminalsLoading && !orderedTerminals.length ? <div className="workbench-muted wbc-rail-empty">{query ? wbcT("terminal.noMatches", "No matching terminals.") : wbcT("terminal.empty", "No terminals yet.")}</div> : null}
                {renderTerminalSection("pinned", wbcT("workbenchChat.pinnedSection", "Pinned"), pinnedTerminals)}
                {renderTerminalSection("recent", wbcT("workbenchChat.recent", "Recent"), recentTerminals)}
              </div>
            </div>
          </div>
        </section>
      ) : null}
      {moduleDock}
      <WbcRenameDialog
        chat={renameChat}
        onClose={function () { setRenameChat(null); }}
        onRename={onRename}
      />
      <WbcRenameDialog
        chat={renameTask}
        entity="task"
        onClose={function () { setRenameTask(null); }}
        onRename={onRenameTask}
      />
      <WbcRenameDialog
        chat={renameGroup}
        entity="group"
        onClose={function () { setRenameGroup(null); }}
        onRename={renameChatGroup}
      />
      <WbcRenameDialog
        chat={renameTerminalItem}
        entity="terminal"
        onClose={function () { setRenameTerminalItem(null); }}
        onRename={onRenameTerminal}
      />
    </aside>
  );
}

/* Shared host for module pages that need the exact Work rail without mounting
   the whole conversation workspace. The rail remains the single renderer for
   chats, tasks, unified search, project files, terminals, drag ordering, and
   menus; this host only supplies the terminal collection normally owned by
   WorkbenchChatPage. */
function WbcProjectRail(props) {
  props = props || {};
  var projectId = String(props.projectId || "");
  var terminalModule = window.CyreneUI.require("terminal");
  var terminalClient = terminalModule.Client;
  var [terminals, setTerminals] = useWbcState([]);
  var [terminalsLoading, setTerminalsLoading] = useWbcState(false);
  var [activeTerminalId, setActiveTerminalId] = useWbcState("");

  function mergeTerminal(terminal) {
    if (!terminal || !terminal.id) return;
    setTerminals(function (current) {
      var found = false;
      var next = current.map(function (item) {
        if (String(item.id) !== String(terminal.id)) return item;
        found = true;
        return Object.assign({}, item, terminal);
      });
      return found ? next : [terminal].concat(next);
    });
  }

  function refreshTerminals(options) {
    if (!projectId) {
      setTerminals([]);
      setActiveTerminalId("");
      return Promise.resolve([]);
    }
    if (!(options && options.background)) setTerminalsLoading(true);
    return terminalClient.list(projectId).then(function (payload) {
      var items = Array.isArray(payload && payload.terminals) ? payload.terminals : [];
      setTerminals(items);
      var restoredId = String(payload && payload.activeTerminalId || "");
      if (restoredId && items.some(function (item) { return String(item.id) === restoredId; })) {
        setActiveTerminalId(restoredId);
      }
      return items;
    }).catch(function () {
      return [];
    }).finally(function () {
      if (!(options && options.background)) setTerminalsLoading(false);
    });
  }

  useWbcEffect(function () {
    setActiveTerminalId("");
    refreshTerminals();
  }, [projectId]);

  useWbcEffect(function () {
    if (!projectId || props.active === false) return undefined;
    var timer = window.setInterval(function () {
      refreshTerminals({ background: true });
    }, 1500);
    return function () { window.clearInterval(timer); };
  }, [projectId, props.active]);

  function openTerminal(terminalId, side) {
    var id = String(terminalId || "");
    if (!id) return;
    setActiveTerminalId(id);
    if (props.onOpenTerminal) props.onOpenTerminal(id, side);
  }

  function createTerminal() {
    if (!projectId) return Promise.resolve(null);
    setTerminalsLoading(true);
    return terminalClient.create(projectId).then(function (terminal) {
      mergeTerminal(terminal);
      if (terminal && terminal.id) openTerminal(terminal.id);
      return terminal;
    }).catch(function (error) {
      window.CyreneUI.require("feedback").showToast(wbcErrorText(error), "error");
      return null;
    }).finally(function () { setTerminalsLoading(false); });
  }

  function renameTerminal(terminalId, title) {
    return terminalClient.rename(terminalId, title).then(function (terminal) {
      mergeTerminal(terminal);
      return terminal;
    });
  }

  function updateTerminalLayout(order, pinned) {
    return terminalClient.layout(projectId, order, pinned).then(function (payload) {
      if (Array.isArray(payload && payload.terminals)) setTerminals(payload.terminals);
      return payload;
    });
  }

  function deleteTerminal(terminalId) {
    var terminal = terminals.find(function (item) { return String(item.id) === String(terminalId); });
    var feedback = window.CyreneUI.require("feedback");
    var request = feedback.confirmModal ? feedback.confirmModal({
      title: wbcT("terminal.deleteTitle", "Delete terminal"),
      body: wbcT("terminal.deleteBody", "This will stop the running process, cancel any pending Agent wake, and remove {title}.", { title: terminal && terminal.title || "Terminal" }),
      confirmLabel: wbcT("terminal.delete", "Delete terminal"),
      danger: true,
    }) : Promise.resolve(window.confirm(wbcT("terminal.deleteBody", "This will stop the running process and remove this terminal.")));
    return request.then(function (confirmed) {
      if (!confirmed) return null;
      return terminalClient.remove(terminalId);
    }).then(function (result) {
      if (!result) return null;
      setTerminals(function (current) { return current.filter(function (item) { return String(item.id) !== String(terminalId); }); });
      if (String(activeTerminalId) === String(terminalId)) setActiveTerminalId("");
      return result;
    });
  }

  return <WbcRail
    {...props}
    terminals={terminals}
    terminalsLoading={terminalsLoading}
    activeTerminalId={activeTerminalId}
    onOpenTerminal={openTerminal}
    onCreateTerminal={createTerminal}
    onRenameTerminal={renameTerminal}
    onDeleteTerminal={deleteTerminal}
    onUpdateTerminalLayout={updateTerminalLayout}
  />;
}

// ---------------------------------------------------------------------------
// Conversation main (column 3)
// ---------------------------------------------------------------------------

export { WbcHoverMarquee, WbcProjectRail, WbcRail, WbcRenameDialog, wbcProjectFileResource }
