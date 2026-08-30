import { workbenchServices } from "../../shared/runtime/services.jsx"

var { useEffect: useWorkbenchEffect, useMemo: useWorkbenchMemo, useState: useWorkbenchState } = React;
var WorkbenchModel = workbenchServices.model();

var WB_CHAT_DRAG_MIME = "application/x-cyrene-chat+json";
var WB_BOARD_CARD_DRAG_MIME = "application/x-cyrene-board-conversation+json";
var WB_CONVERSATION_BOARD_LAYOUT_PREFIX = "cyrene-workbench-conversation-board-v2:";

// Columns are manual conversation groupings. They never follow execution,
// Goal, or Plan status automatically; moving a card is the user's decision.
var WB_CONVERSATION_BOARD_COLUMNS = [
  { id: "planning", labelKey: "conversationBoard.column.planning" },
  { id: "executing", labelKey: "conversationBoard.column.executing" },
  { id: "review", labelKey: "conversationBoard.column.review" },
  { id: "completed", labelKey: "conversationBoard.column.completed" },
  { id: "blocked", labelKey: "conversationBoard.column.blocked" },
];

function wbEmptyConversationBoardLayout() {
  var columns = {};
  WB_CONVERSATION_BOARD_COLUMNS.forEach(function (column) { columns[column.id] = []; });
  return { placements: {}, columns: columns };
}

function wbNormalizeConversationBoardLayout(savedLayout) {
  var saved = savedLayout && typeof savedLayout === "object" ? savedLayout : {};
  var savedPlacements = saved.placements && typeof saved.placements === "object" ? saved.placements : {};
  var savedColumns = saved.columns && typeof saved.columns === "object" ? saved.columns : {};
  var next = wbEmptyConversationBoardLayout();
  var seen = new Set();

  WB_CONVERSATION_BOARD_COLUMNS.forEach(function (column) {
    var ordered = Array.isArray(savedColumns[column.id]) ? savedColumns[column.id] : [];
    ordered.forEach(function (rawId) {
      var chatId = String(rawId || "").trim();
      if (!chatId || seen.has(chatId)) return;
      seen.add(chatId);
      next.columns[column.id].push(chatId);
      next.placements[chatId] = { column: column.id };
    });
  });

  Object.keys(savedPlacements).forEach(function (rawId) {
    var chatId = String(rawId || "").trim();
    if (!chatId || seen.has(chatId)) return;
    var requested = String(savedPlacements[rawId] && savedPlacements[rawId].column || "");
    var columnId = next.columns[requested] ? requested : WB_CONVERSATION_BOARD_COLUMNS[0].id;
    seen.add(chatId);
    next.columns[columnId].push(chatId);
    next.placements[chatId] = { column: columnId };
  });
  return next;
}

function wbLoadConversationBoardLayout(projectId) {
  var saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(
      WB_CONVERSATION_BOARD_LAYOUT_PREFIX + String(projectId || "")
    ) || "null");
  } catch (e) {}
  return wbNormalizeConversationBoardLayout(saved);
}

function wbStoreConversationBoardLayout(projectId, layout) {
  try {
    localStorage.setItem(
      WB_CONVERSATION_BOARD_LAYOUT_PREFIX + String(projectId || ""),
      JSON.stringify(wbNormalizeConversationBoardLayout(layout))
    );
  } catch (e) {}
}

function wbPlaceConversationBoardCard(layout, chatId, columnId, targetId, edge) {
  var next = wbNormalizeConversationBoardLayout(layout);
  var id = String(chatId || "").trim();
  var destination = String(columnId || "");
  if (!id || !next.columns[destination]) return next;

  Object.keys(next.columns).forEach(function (column) {
    next.columns[column] = next.columns[column].filter(function (candidate) { return candidate !== id; });
  });
  var target = next.columns[destination];
  var targetIndex = targetId ? target.indexOf(String(targetId)) : -1;
  if (targetIndex < 0) target.push(id);
  else target.splice(targetIndex + (edge === "after" ? 1 : 0), 0, id);
  next.placements[id] = { column: destination };
  return next;
}

function wbRemoveConversationBoardCard(layout, chatId) {
  var next = wbNormalizeConversationBoardLayout(layout);
  var id = String(chatId || "").trim();
  Object.keys(next.columns).forEach(function (column) {
    next.columns[column] = next.columns[column].filter(function (candidate) { return candidate !== id; });
  });
  delete next.placements[id];
  return next;
}

function wbReadConversationDrag(event) {
  var transfer = event && event.dataTransfer;
  if (!transfer) return null;
  try {
    var payload = JSON.parse(transfer.getData(WB_CHAT_DRAG_MIME) || "null");
    return payload && payload.kind === "chat" && payload.id ? payload : null;
  } catch (e) {
    return null;
  }
}

function wbHasConversationDrag(event) {
  var transfer = event && event.dataTransfer;
  if (!transfer) return false;
  try {
    return Array.prototype.slice.call(transfer.types || []).indexOf(WB_CHAT_DRAG_MIME) >= 0;
  } catch (e) {
    return false;
  }
}

function wbChatBoardTone(chat) {
  var raw = String(chat && (chat.runStatus || chat.status) || "").toLowerCase();
  if (chat && (chat.failed || chat.error) || ["failed", "error", "timeout"].indexOf(raw) >= 0) return "red";
  if (chat && (chat.pendingQuestion || chat.awaitingUser) || ["blocked", "waiting_for_user", "waiting_for_approval"].indexOf(raw) >= 0) return "amber";
  if (["completed", "complete", "done", "success"].indexOf(raw) >= 0) return "green";
  return chat && (chat.agentBusy || raw === "running") ? "blue" : "muted";
}

function wbChatBoardStateLabel(chat, t) {
  var raw = String(chat && (chat.runStatus || chat.status) || "").toLowerCase();
  if (chat && (chat.pendingQuestion || chat.awaitingUser) || ["blocked", "waiting_for_user", "waiting_for_approval"].indexOf(raw) >= 0) return t("conversationBoard.chat.waiting");
  if (chat && (chat.failed || chat.error) || ["failed", "error", "timeout"].indexOf(raw) >= 0) return t("conversationBoard.chat.failed");
  if (chat && (chat.agentBusy || raw === "running")) return t("conversationBoard.chat.running");
  return t("conversationBoard.chat.label");
}

function WbBoardSearchIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.3"/><path d="m16 16 4 4"/></svg>;
}

function WbBoardMoreIcon() {
  return <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>;
}

function ConversationBoard({ project, chats, loading, error, onOpenChat, onCreateChat }) {
  var { t } = workbenchServices.i18n().use();
  var conversations = Array.isArray(chats) ? chats : [];
  var [recentFirst, setRecentFirst] = useWorkbenchState(false);
  var [query, setQuery] = useWorkbenchState("");
  var [menuId, setMenuId] = useWorkbenchState("");
  var [layout, setLayout] = useWorkbenchState(function () { return wbLoadConversationBoardLayout(project && project.id); });
  var [dragChatId, setDragChatId] = useWorkbenchState("");
  var [dropTarget, setDropTarget] = useWorkbenchState(null);
  var normalizedQuery = query.trim().toLowerCase();
  var chatMap = useWorkbenchMemo(function () {
    return new Map(conversations.map(function (chat) { return [String(chat && chat.id || ""), chat]; }));
  }, [conversations]);

  useWorkbenchEffect(function () {
    setLayout(wbLoadConversationBoardLayout(project && project.id));
    setDragChatId("");
    setDropTarget(null);
    setMenuId("");
  }, [project && project.id]);

  useWorkbenchEffect(function () {
    if (!menuId) return undefined;
    function closeOnOutside(event) {
      var target = event.target;
      if (target && target.closest && (target.closest(".wb-card-menu") || target.closest(".wb-card-menu-btn"))) return;
      setMenuId("");
    }
    document.addEventListener("pointerdown", closeOnOutside, true);
    return function () { document.removeEventListener("pointerdown", closeOnOutside, true); };
  }, [menuId]);

  useWorkbenchEffect(function () {
    function clearDragTarget() {
      setDropTarget(null);
    }
    window.addEventListener("dragend", clearDragTarget);
    return function () { window.removeEventListener("dragend", clearDragTarget); };
  }, []);

  function commitLayout(update) {
    setLayout(function (current) {
      var next = update(current);
      wbStoreConversationBoardLayout(project && project.id, next);
      return next;
    });
  }

  function canAcceptDrop(event) {
    return !!dragChatId || wbHasConversationDrag(event);
  }

  function dropConversation(event, columnId, targetId, edge) {
    event.preventDefault();
    event.stopPropagation();
    var chatId = dragChatId;
    if (!chatId) {
      var payload = wbReadConversationDrag(event);
      var projectId = String(project && project.id || "");
      if (!payload || (payload.projectId && String(payload.projectId) !== projectId)) return;
      chatId = String(payload.id || "");
      // Only active-project conversations exposed by the rail can become cards.
      if (!chatMap.has(chatId)) return;
    }
    commitLayout(function (current) { return wbPlaceConversationBoardCard(current, chatId, columnId, targetId, edge); });
    setDragChatId("");
    setDropTarget(null);
  }

  function handleBoardWheel(event) {
    var viewport = event.currentTarget;
    var deltaX = Number(event.deltaX || 0);
    var deltaY = Number(event.deltaY || 0);
    if (!viewport || Math.abs(deltaY) <= Math.abs(deltaX) || Math.abs(deltaY) < 1) return;
    var columnBody = event.target && event.target.closest ? event.target.closest(".wb-board-column-body") : null;
    if (columnBody) {
      var maxColumnTop = Math.max(0, columnBody.scrollHeight - columnBody.clientHeight);
      var canScrollColumn = deltaY < 0 ? columnBody.scrollTop > 1 : columnBody.scrollTop < maxColumnTop - 1;
      if (canScrollColumn) return;
    }
    var maxBoardLeft = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    if (!maxBoardLeft) return;
    var scale = event.deltaMode === 1 ? 16 : (event.deltaMode === 2 ? viewport.clientWidth : 1);
    var nextLeft = Math.max(0, Math.min(maxBoardLeft, viewport.scrollLeft + deltaY * scale));
    if (nextLeft === viewport.scrollLeft) return;
    event.preventDefault();
    viewport.scrollLeft = nextLeft;
  }

  if (!project) {
    return <main className="workbench-conversation-board wb-board-no-project"><div className="wb-board-empty-overall"><b>{t("conversationBoard.noProject")}</b><span>{t("conversationBoard.noProjectHint")}</span></div></main>;
  }

  var boardCardCount = Object.keys(layout.placements || {}).filter(function (chatId) { return chatMap.has(chatId); }).length;
  return (
    <main className="workbench-conversation-board" data-tour="conversation_board" aria-label={t("conversationBoard.title")}>
      <header className="wb-board-header">
        <div className="wb-board-heading">
          <span className="wb-board-kicker">{t("conversationBoard.title")}</span>
          <h1>{project.name}</h1>
        </div>
        <div className="wb-board-toolbar">
          <button type="button" className={"wb-board-tool-btn" + (recentFirst ? " active" : "")} onClick={function () { setRecentFirst(!recentFirst); }}>
            {recentFirst ? t("conversationBoard.sortRecent") : t("conversationBoard.sortDefault")}
          </button>
          <button type="button" className="wb-board-new-btn" onClick={onCreateChat}>{t("conversationBoard.newConversation")}</button>
          <label className="wb-board-search">
            <span><WbBoardSearchIcon /></span>
            <input type="search" value={query} onChange={function (event) { setQuery(event.target.value); }} placeholder={t("conversationBoard.searchPlaceholder")} aria-label={t("conversationBoard.search")} />
          </label>
        </div>
      </header>
      {error && <div className="workbench-error wb-board-error">{error}</div>}
      {loading && conversations.length === 0 ? (
        <div className="wb-board-loading">{t("workbenchChat.loading")}</div>
      ) : (
        <div className="wb-board-scroll" onWheel={handleBoardWheel}>
          <div className="wb-board-columns">
            {WB_CONVERSATION_BOARD_COLUMNS.map(function (column) {
              var orderedIds = layout.columns && Array.isArray(layout.columns[column.id]) ? layout.columns[column.id] : [];
              var cards = orderedIds.map(function (chatId) { return chatMap.get(chatId); }).filter(Boolean).filter(function (chat) {
                if (!normalizedQuery) return true;
                return [chat.title, chat.preview, chat.summary, chat.status, chat.runStatus].some(function (value) {
                  return String(value || "").toLowerCase().indexOf(normalizedQuery) !== -1;
                });
              });
              if (recentFirst) cards = cards.slice().sort(function (left, right) {
                return String(right.updatedAt || right.createdAt || "").localeCompare(String(left.updatedAt || left.createdAt || ""));
              });
              return (
                <section
                  key={column.id}
                  className={"wb-board-column is-" + column.id + (dropTarget && dropTarget.column === column.id && !dropTarget.chatId ? " drop-active" : "")}
                  aria-label={t(column.labelKey)}
                  onDragEnter={function (event) {
                    if (!canAcceptDrop(event)) return;
                    event.preventDefault();
                    if (!event.target.closest || !event.target.closest(".wb-board-card")) setDropTarget({ column: column.id, chatId: "", edge: "after" });
                  }}
                  onDragOver={function (event) {
                    if (!canAcceptDrop(event)) return;
                    event.preventDefault();
                    if (event.dataTransfer) event.dataTransfer.dropEffect = dragChatId ? "move" : "copy";
                    if (!event.target.closest || !event.target.closest(".wb-board-card")) setDropTarget({ column: column.id, chatId: "", edge: "after" });
                  }}
                  onDrop={function (event) { if (canAcceptDrop(event)) dropConversation(event, column.id, "", "after"); }}
                >
                  <header className="wb-board-column-head">
                    <span className="wb-board-column-title">{t(column.labelKey)}</span>
                    <span className="wb-board-column-count">{cards.length}</span>
                  </header>
                  <div className="wb-board-column-body">
                    {cards.map(function (chat) {
                      var chatId = String(chat.id);
                      return <ConversationBoardCard
                        key={chatId}
                        chat={chat}
                        column={column.id}
                        dragging={dragChatId === chatId}
                        dropTarget={dropTarget && dropTarget.chatId === chatId ? dropTarget : null}
                        menuOpen={menuId === chatId}
                        onMenu={function () { setMenuId(menuId === chatId ? "" : chatId); }}
                        onOpen={function () { setMenuId(""); if (onOpenChat) onOpenChat(chat); }}
                        onRemove={function () {
                          setMenuId("");
                          commitLayout(function (current) { return wbRemoveConversationBoardCard(current, chatId); });
                        }}
                        onDragStart={function (event) {
                          setMenuId("");
                          setDragChatId(chatId);
                          if (event.dataTransfer) {
                            event.dataTransfer.effectAllowed = "move";
                            event.dataTransfer.setData(WB_BOARD_CARD_DRAG_MIME, JSON.stringify({ kind: "conversation-board-card", id: chatId }));
                            event.dataTransfer.setData("text/plain", chat.title || chatId);
                          }
                        }}
                        onDragOver={function (event) {
                          if (!canAcceptDrop(event) || dragChatId === chatId) return;
                          event.preventDefault();
                          event.stopPropagation();
                          if (event.dataTransfer) event.dataTransfer.dropEffect = dragChatId ? "move" : "copy";
                          var rect = event.currentTarget.getBoundingClientRect();
                          setDropTarget({ column: column.id, chatId: chatId, edge: event.clientY < rect.top + rect.height / 2 ? "before" : "after" });
                        }}
                        onDrop={function (event) {
                          if (!canAcceptDrop(event)) return;
                          var rect = event.currentTarget.getBoundingClientRect();
                          dropConversation(event, column.id, chatId, event.clientY < rect.top + rect.height / 2 ? "before" : "after");
                        }}
                        onDragEnd={function () { setDragChatId(""); setDropTarget(null); }}
                      />;
                    })}
                    {cards.length === 0 && (
                      <div className="wb-board-column-empty">
                        <b>{normalizedQuery ? t("conversationBoard.noSearchResults") : t("conversationBoard.empty")}</b>
                        <span>{normalizedQuery ? t("conversationBoard.noSearchResultsHint") : t("conversationBoard.emptyHint")}</span>
                      </div>
                    )}
                  </div>
                </section>
              );
            })}
          </div>
        </div>
      )}
      {!loading && boardCardCount === 0 && <div className="wb-board-default-empty-hint">{t("conversationBoard.defaultEmptyHint")}</div>}
    </main>
  );
}

function ConversationBoardCard({ chat, column, dragging, dropTarget, menuOpen, onMenu, onOpen, onRemove, onDragStart, onDragOver, onDrop, onDragEnd }) {
  var { t } = workbenchServices.i18n().use();
  var tone = wbChatBoardTone(chat);
  var summary = String(chat.preview || chat.summary || t("workbenchChat.noMessages", null, "No messages yet"));
  return (
    <article
      role="button"
      tabIndex={0}
      draggable="true"
      data-board-conversation-id={String(chat.id || "")}
      className={"wb-board-card is-" + column + " is-chat" + (menuOpen ? " menu-open" : "") + (dragging ? " dragging" : "") + (dropTarget ? " drop-" + dropTarget.edge : "")}
      onClick={onOpen}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
      onContextMenu={function (event) { event.preventDefault(); event.stopPropagation(); if (!menuOpen) onMenu(); }}
      onKeyDown={function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(); } }}
    >
      <div className="wb-board-card-title">
        <span className={"workbench-status-dot " + tone}></span>
        <b>{chat.title || t("workbenchChat.newChat")}</b>
        <button type="button" className="wb-card-menu-btn wb-board-card-menu-btn" onClick={function (event) { event.stopPropagation(); onMenu(); }} aria-label={t("common.moreActions")}><WbBoardMoreIcon /></button>
      </div>
      {summary && summary !== chat.title && <p>{summary}</p>}
      <div className="wb-board-card-meta">
        <span className={"workbench-conversation-status " + tone}>{wbChatBoardStateLabel(chat, t)}</span>
        <time>{WorkbenchModel.formatRelativeTime(chat.updatedAt || chat.createdAt)}</time>
      </div>
      {menuOpen && (
        <div className="wb-card-menu wb-board-card-menu" onClick={function (event) { event.stopPropagation(); }}>
          <button type="button" onClick={onRemove}>{t("conversationBoard.removeConversation")}</button>
        </div>
      )}
    </article>
  );
}

export {
  ConversationBoard,
  wbEmptyConversationBoardLayout,
  wbNormalizeConversationBoardLayout,
  wbPlaceConversationBoardCard,
  wbRemoveConversationBoardCard,
}
