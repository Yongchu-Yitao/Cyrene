import { workbenchServices } from "../../shared/runtime/services.jsx"
import { ICONS, sessionSummaryText } from "./presentation.jsx"

var { useEffect: useWorkbenchEffect, useMemo: useWorkbenchMemo, useState: useWorkbenchState } = React;
var WorkbenchModel = workbenchServices.model();

var WB_TASK_BOARD_COLUMNS = [
  { id: "planning", labelKey: "taskBoard.column.planning" },
  { id: "executing", labelKey: "taskBoard.column.executing" },
  { id: "review", labelKey: "taskBoard.column.review" },
  { id: "completed", labelKey: "taskBoard.column.completed" },
  { id: "blocked", labelKey: "taskBoard.column.blocked" },
];

function wbTaskBoardColumnKey(status) {
  var raw = String(status || "idle");
  if (["running", "answered", "acted", "waiting_for_user", "waiting_for_approval", "paused"].indexOf(raw) >= 0) return "executing";
  if (["review", "done"].indexOf(raw) >= 0) return "review";
  if (["completed", "skipped"].indexOf(raw) >= 0) return "completed";
  if (["blocked", "failed", "cancelled"].indexOf(raw) >= 0) return "blocked";
  return "planning";
}

var WB_MIXED_BOARD_LAYOUT_PREFIX = "cyrene-workbench-mixed-board-v1:";

function wbMixedBoardCardKey(kind, id) {
  return String(kind || "") + ":" + String(id || "");
}

function wbMixedBoardDefaultColumn(card) {
  return card && card.kind === "task" ? wbTaskBoardColumnKey(card.item && card.item.status) : "planning";
}

function wbNormalizeMixedBoardLayout(savedLayout, cards) {
  var saved = savedLayout && typeof savedLayout === "object" ? savedLayout : {};
  var savedPlacements = saved.placements && typeof saved.placements === "object" ? saved.placements : {};
  var savedColumns = saved.columns && typeof saved.columns === "object" ? saved.columns : {};
  var cardMap = new Map((Array.isArray(cards) ? cards : []).map(function (card) { return [card.key, card]; }));
  var placements = {};
  var columns = {};
  WB_TASK_BOARD_COLUMNS.forEach(function (column) { columns[column.id] = []; });

  cardMap.forEach(function (card, key) {
    var previous = savedPlacements[key] || {};
    var systemColumn = wbMixedBoardDefaultColumn(card);
    var column = systemColumn;
    if (card.kind === "chat") {
      column = columns[previous.column] ? previous.column : systemColumn;
    } else if (String(previous.statusBasis || "") === String(card.item && card.item.status || "")) {
      column = columns[previous.column] ? previous.column : systemColumn;
    }
    placements[key] = {
      column: column,
      statusBasis: card.kind === "task" ? String(card.item && card.item.status || "") : "user",
    };
  });

  var seen = new Set();
  WB_TASK_BOARD_COLUMNS.forEach(function (column) {
    var ordered = Array.isArray(savedColumns[column.id]) ? savedColumns[column.id] : [];
    ordered.forEach(function (key) {
      key = String(key || "");
      if (!cardMap.has(key) || seen.has(key) || placements[key].column !== column.id) return;
      seen.add(key);
      columns[column.id].push(key);
    });
  });
  cardMap.forEach(function (_card, key) {
    if (seen.has(key)) return;
    columns[placements[key].column].push(key);
  });
  return { placements: placements, columns: columns };
}

function wbLoadMixedBoardLayout(projectId, cards) {
  var saved = null;
  try { saved = JSON.parse(localStorage.getItem(WB_MIXED_BOARD_LAYOUT_PREFIX + String(projectId || "")) || "null"); } catch (e) {}
  return wbNormalizeMixedBoardLayout(saved, cards);
}

function wbStoreMixedBoardLayout(projectId, layout) {
  try { localStorage.setItem(WB_MIXED_BOARD_LAYOUT_PREFIX + String(projectId || ""), JSON.stringify(layout)); } catch (e) {}
}

function wbMoveMixedBoardCard(layout, cards, movingKey, columnId, targetKey, edge) {
  var next = wbNormalizeMixedBoardLayout(layout, cards);
  var key = String(movingKey || "");
  var targetColumn = String(columnId || "");
  var card = (Array.isArray(cards) ? cards : []).find(function (candidate) { return candidate.key === key; });
  if (!card || !next.columns[targetColumn]) return next;
  Object.keys(next.columns).forEach(function (column) {
    next.columns[column] = next.columns[column].filter(function (candidate) { return candidate !== key; });
  });
  var target = next.columns[targetColumn];
  var targetIndex = targetKey ? target.indexOf(String(targetKey)) : -1;
  if (targetIndex < 0) target.push(key);
  else target.splice(targetIndex + (edge === "after" ? 1 : 0), 0, key);
  next.placements[key] = {
    column: targetColumn,
    statusBasis: card.kind === "task" ? String(card.item && card.item.status || "") : "user",
  };
  return next;
}

function wbChatBoardTone(chat) {
  var raw = String(chat && (chat.runStatus || chat.status) || "").toLowerCase();
  if (chat && (chat.failed || chat.error) || ["failed", "error", "timeout"].indexOf(raw) >= 0) return "red";
  if (chat && (chat.pendingQuestion || chat.awaitingUser) || ["blocked", "waiting_for_user", "waiting_for_approval"].indexOf(raw) >= 0) return "amber";
  if (["completed", "complete", "done", "success"].indexOf(raw) >= 0) return "green";
  return chat && chat.agentBusy ? "blue" : "muted";
}

function TaskBoard({ project, chats, loading, error, onOpenSession, onOpenChat, onCreateSession, onCreateChat, onDeleteSession }) {
  var { t } = workbenchServices.i18n().use();
  var sessions = project && Array.isArray(project.sessions) ? project.sessions : [];
  var conversations = Array.isArray(chats) ? chats : [];
  var [recentFirst, setRecentFirst] = useWorkbenchState(false);
  var [query, setQuery] = useWorkbenchState("");
  var [menuId, setMenuId] = useWorkbenchState("");
  var [completedOpen, setCompletedOpen] = useWorkbenchState(true);
  var mixedCards = useWorkbenchMemo(function () {
    return sessions.map(function (session) {
      return { key: wbMixedBoardCardKey("task", session.id), kind: "task", item: session };
    }).concat(conversations.map(function (chat) {
      return { key: wbMixedBoardCardKey("chat", chat.id), kind: "chat", item: chat };
    }));
  }, [sessions, conversations]);
  var mixedCardSignature = mixedCards.map(function (card) {
    return card.key + ":" + (card.kind === "task" ? String(card.item.status || "") : "chat");
  }).join("|");
  var [layout, setLayout] = useWorkbenchState(function () {
    return wbLoadMixedBoardLayout(project && project.id, mixedCards);
  });
  var [dragKey, setDragKey] = useWorkbenchState("");
  var [dropTarget, setDropTarget] = useWorkbenchState(null);
  var normalizedQuery = query.trim().toLowerCase();
  var visibleMixedCards = normalizedQuery ? mixedCards.filter(function (card) {
    var item = card.item || {};
    var searchableValues = card.kind === "task"
      ? [item.title, item.goal, item.summary, item.description, item.status, item.priority, sessionSummaryText(item)]
      : [item.title, item.preview, item.summary, item.status, item.runStatus];
    return searchableValues.some(function (value) {
      return String(value || "").toLowerCase().indexOf(normalizedQuery) !== -1;
    });
  }) : mixedCards;

  useWorkbenchEffect(function () {
    var next = wbLoadMixedBoardLayout(project && project.id, mixedCards);
    setLayout(next);
    setDragKey("");
    setDropTarget(null);
  }, [project && project.id]);

  useWorkbenchEffect(function () {
    setLayout(function (current) {
      var next = wbNormalizeMixedBoardLayout(current, mixedCards);
      wbStoreMixedBoardLayout(project && project.id, next);
      return JSON.stringify(next) === JSON.stringify(current) ? current : next;
    });
  }, [mixedCardSignature]);

  useWorkbenchEffect(function () {
    if (!menuId) return undefined;
    function closeOnOutside(event) {
      var target = event.target;
      if (target && target.closest && (
        target.closest(".wb-card-menu") || target.closest(".wb-card-menu-btn")
      )) return;
      setMenuId("");
    }
    document.addEventListener("pointerdown", closeOnOutside, true);
    return function () {
      document.removeEventListener("pointerdown", closeOnOutside, true);
    };
  }, [menuId]);

  useWorkbenchEffect(function () {
    setMenuId("");
  }, [project && project.id]);

  function handleBoardWheel(event) {
    var viewport = event.currentTarget;
    var deltaX = Number(event.deltaX || 0);
    var deltaY = Number(event.deltaY || 0);
    // Trackpads already provide a native horizontal delta. Only translate a
    // primarily vertical mouse-wheel gesture so both input types feel natural.
    if (!viewport || Math.abs(deltaY) <= Math.abs(deltaX) || Math.abs(deltaY) < 1) return;

    // A task column owns vertical wheel input while it can still move in that
    // direction. At its boundary, hand the same gesture to the board canvas.
    var target = event.target;
    var columnBody = target && target.closest ? target.closest(".wb-board-column-body") : null;
    if (columnBody) {
      var maxColumnTop = Math.max(0, columnBody.scrollHeight - columnBody.clientHeight);
      var canScrollColumn = deltaY < 0
        ? columnBody.scrollTop > 1
        : columnBody.scrollTop < maxColumnTop - 1;
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

  var cardMap = new Map(visibleMixedCards.map(function (card) { return [card.key, card]; }));
  var completedSessions = sessions.filter(function (session) {
    if (wbTaskBoardColumnKey(session.status) !== "completed") return false;
    if (!normalizedQuery) return true;
    return [session.title, session.goal, session.summary, session.description, session.status, session.priority, sessionSummaryText(session)].some(function (value) {
      return String(value || "").toLowerCase().indexOf(normalizedQuery) !== -1;
    });
  });

  function moveCard(columnId, targetKey, edge) {
    if (!dragKey) return;
    setRecentFirst(false);
    setLayout(function (current) {
      var next = wbMoveMixedBoardCard(current, mixedCards, dragKey, columnId, targetKey, edge);
      wbStoreMixedBoardLayout(project && project.id, next);
      return next;
    });
    setDragKey("");
    setDropTarget(null);
  }

  if (!project) {
    return (
      <main className="workbench-task-board wb-board-no-project">
        <div className="wb-board-empty-overall">
          <b>{t("taskBoard.noProject")}</b>
          <span>{t("taskBoard.noProjectHint")}</span>
        </div>
      </main>
    );
  }

  return (
    <main className="workbench-task-board" data-tour="task_board" aria-label={t("taskBoard.title")}>
      <header className="wb-board-header">
        <div className="wb-board-heading">
          <span className="wb-board-kicker">{t("taskBoard.title")}</span>
          <h1>{project.name}</h1>
          <p>{project.description || t("taskBoard.subtitle")}</p>
        </div>
        <div className="wb-board-toolbar">
          <button type="button" className={"wb-board-tool-btn" + (recentFirst ? " active" : "")} onClick={function () { setRecentFirst(!recentFirst); }}>
            {recentFirst ? t("taskBoard.sortRecent") : t("taskBoard.sortDefault")}
          </button>
          <div className="wb-board-create-menu">
            <button
              type="button"
              data-tour="task_new"
              className="wb-board-new-btn"
              onClick={function () { setMenuId(menuId === "create" ? "" : "create"); }}
              aria-haspopup="menu"
              aria-expanded={menuId === "create"}
            >
              <span>{t("taskBoard.new")}</span>
              <span className={"wb-board-new-chevron" + (menuId === "create" ? " open" : "")} aria-hidden="true">{ICONS.chevronDown}</span>
            </button>
            {menuId === "create" && <div className="wb-card-menu wb-board-create-dropdown" role="menu">
              <button type="button" role="menuitem" onClick={function () { setMenuId(""); if (onCreateChat) onCreateChat(); }}>{t("workbenchChat.newChat")}</button>
              <button type="button" role="menuitem" onClick={function () { setMenuId(""); onCreateSession(); }}>{t("taskBoard.newTask")}</button>
            </div>}
          </div>
          <label className="wb-board-search">
            <span aria-hidden="true">{ICONS.cmdResearch}</span>
            <input
              type="search"
              value={query}
              onChange={function (event) { setQuery(event.target.value); }}
              placeholder={t("taskBoard.searchPlaceholder")}
              aria-label={t("taskBoard.search")}
            />
          </label>
        </div>
      </header>
      {error && <div className="workbench-error wb-board-error">{error}</div>}
      {loading && mixedCards.length === 0 ? (
        <div className="wb-board-loading">{t("rail.loadingTasks")}</div>
      ) : (
        <div className="wb-board-scroll" onWheel={handleBoardWheel}>
          <div className="wb-board-columns">
            {WB_TASK_BOARD_COLUMNS.map(function (column) {
              var orderedKeys = layout && layout.columns && Array.isArray(layout.columns[column.id]) ? layout.columns[column.id] : [];
              var cards = orderedKeys.map(function (key) { return cardMap.get(key); }).filter(Boolean);
              if (recentFirst) cards = cards.slice().sort(function (left, right) {
                return String(right.item.updatedAt || right.item.createdAt || "").localeCompare(String(left.item.updatedAt || left.item.createdAt || ""));
              });
              return (
                <section
                  key={column.id}
                  className={"wb-board-column is-" + column.id + (dropTarget && dropTarget.column === column.id && !dropTarget.key ? " drop-active" : "")}
                  aria-label={t(column.labelKey)}
                  onDragOver={function (event) {
                    if (!dragKey) return;
                    event.preventDefault();
                    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
                    if (!event.target.closest || !event.target.closest(".wb-board-card")) setDropTarget({ column: column.id, key: "", edge: "after" });
                  }}
                  onDrop={function (event) {
                    if (!dragKey) return;
                    event.preventDefault();
                    moveCard(column.id, "", "after");
                  }}
                >
                  <header className="wb-board-column-head">
                    <span className="wb-board-column-title">{t(column.labelKey)}</span>
                    <span className="wb-board-column-count">{cards.length}</span>
                    <button type="button" onClick={onCreateSession} aria-label={t("taskBoard.addInColumn", { stage: t(column.labelKey) })}>{t("taskBoard.add")}</button>
                  </header>
                  <div className="wb-board-column-body">
                    {cards.map(function (card) {
                      return (
                        <TaskBoardCard
                          key={card.key}
                          card={card}
                          column={column.id}
                          dragging={dragKey === card.key}
                          dropTarget={dropTarget && dropTarget.key === card.key ? dropTarget : null}
                          menuOpen={menuId === card.key}
                          onMenu={function () { setMenuId(menuId === card.key ? "" : card.key); }}
                          onOpen={function () {
                            setMenuId("");
                            if (card.kind === "task") onOpenSession(card.item.id);
                            else if (onOpenChat) onOpenChat(card.item);
                          }}
                          onDelete={card.kind === "task" ? function () { setMenuId(""); onDeleteSession && onDeleteSession(card.item); } : null}
                          onDragStart={function (event) {
                            setMenuId("");
                            setDragKey(card.key);
                            if (event.dataTransfer) {
                              event.dataTransfer.effectAllowed = "move";
                              event.dataTransfer.setData("application/x-cyrene-board-card+json", JSON.stringify({ key: card.key, kind: card.kind, id: card.item.id }));
                              event.dataTransfer.setData("text/plain", card.item.title || card.item.id);
                            }
                          }}
                          onDragOver={function (event) {
                            if (!dragKey || dragKey === card.key) return;
                            event.preventDefault();
                            event.stopPropagation();
                            var rect = event.currentTarget.getBoundingClientRect();
                            var edge = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
                            setDropTarget({ column: column.id, key: card.key, edge: edge });
                          }}
                          onDrop={function (event) {
                            if (!dragKey) return;
                            event.preventDefault();
                            event.stopPropagation();
                            var rect = event.currentTarget.getBoundingClientRect();
                            moveCard(column.id, card.key, event.clientY < rect.top + rect.height / 2 ? "before" : "after");
                          }}
                          onDragEnd={function () { setDragKey(""); setDropTarget(null); }}
                        />
                      );
                    })}
                    {cards.length === 0 && (
                      <div className="wb-board-column-empty">
                        <b>{normalizedQuery ? t("taskBoard.noSearchResults") : (column.id === "blocked" ? t("taskBoard.emptyBlocked") : t("taskBoard.empty"))}</b>
                        <span>{normalizedQuery ? t("taskBoard.noSearchResultsHint") : (column.id === "blocked" ? t("taskBoard.emptyBlockedHint") : t("taskBoard.emptyHint"))}</span>
                      </div>
                    )}
                  </div>
                  <button type="button" className="wb-board-column-add" onClick={onCreateSession}>{t("taskBoard.newTask")}</button>
                </section>
              );
            })}
          </div>
        </div>
      )}
      {completedSessions.length > 0 && (
        <section className="wb-board-completed-strip">
          <button type="button" className="wb-board-completed-toggle" onClick={function () { setCompletedOpen(!completedOpen); }} aria-expanded={completedOpen}>
            <span>{t("taskBoard.completedStrip", { count: completedSessions.length })}</span>
            <small>{completedOpen ? t("common.collapse") : t("common.expand")}</small>
          </button>
          {completedOpen && (
            <div className="wb-board-completed-list">
              {completedSessions.map(function (session) {
                return (
                  <button key={session.id} type="button" onClick={function () { onOpenSession(session.id); }} title={session.title}>
                    <span className="wb-board-completed-check">{ICONS.checkSmall}</span>
                    <span>{session.title}</span>
                  </button>
                );
              })}
            </div>
          )}
        </section>
      )}
    </main>
  );
}

function TaskBoardCard({ card, column, dragging, dropTarget, menuOpen, onMenu, onOpen, onDelete, onDragStart, onDragOver, onDrop, onDragEnd }) {
  var { t } = workbenchServices.i18n().use();
  var item = card.item;
  var task = card.kind === "task";
  var tone = task ? WorkbenchModel.statusTone(item.status) : wbChatBoardTone(item);
  var summary = task ? sessionSummaryText(item) : String(item.preview || t("workbenchChat.noMessages", null, "No messages yet"));
  var stepCount = task ? Number(item.planStepCount != null ? item.planStepCount : (Array.isArray(item.plan) ? item.plan.length : 0)) : 0;
  return (
    <article
      role="button"
      tabIndex={0}
      draggable="true"
      data-board-card-key={card.key}
      className={"wb-board-card is-" + column + " is-" + card.kind + (menuOpen ? " menu-open" : "") + (dragging ? " dragging" : "") + (dropTarget ? " drop-" + dropTarget.edge : "")}
      onClick={onOpen}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
      onContextMenu={function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (!menuOpen) onMenu();
      }}
      onKeyDown={function (event) {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(); }
      }}
    >
      <div className="wb-board-card-title">
        <span className={"workbench-status-dot " + tone}></span>
        <b>{item.title}</b>
        <button type="button" className="wb-card-menu-btn wb-board-card-menu-btn" onClick={function (event) { event.stopPropagation(); onMenu(); }} aria-label={t("common.moreActions")}>{ICONS.dots}</button>
      </div>
      {summary && summary !== item.title && <p>{summary}</p>}
      <div className="wb-board-card-meta">
        <span className={"workbench-task-status " + tone}>{task ? WorkbenchModel.statusText(item.status) : t("workbench.page.chat")}</span>
        {stepCount > 0 && <span>{t("taskBoard.steps", { count: stepCount })}</span>}
        <time>{WorkbenchModel.formatRelativeTime(item.updatedAt || item.createdAt)}</time>
      </div>
      {menuOpen && onDelete && (
        <div className="wb-card-menu wb-board-card-menu" onClick={function (event) { event.stopPropagation(); }}>
          <button type="button" className="danger" onClick={onDelete}>{t("rail.deleteTask")}</button>
        </div>
      )}
    </article>
  );
}


export { TaskBoard }
