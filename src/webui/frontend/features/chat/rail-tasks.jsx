import {
  WBC_ICONS,
  useWbcEffect,
  useWbcState,
  wbcFormatTime,
  wbcHasTaskDrag,
  wbcSetTaskDrag,
  wbcT,
} from "../../workbench-chat.jsx"
import {
  WBC_TASK_ORDER_PREFIX,
  WbcHoverMarquee,
  wbcMoveChatOrder,
  wbcNormalizeChatOrder,
  wbcOrderChatsByPinned,
} from "./rail-model.jsx"

function useWbcTaskRail(projectId, tasks, pinnedTaskIds, query) {
  var defaultOrder = (Array.isArray(tasks) ? tasks : []).map(function (task) { return String(task.id) })
  var defaultOrderKey = defaultOrder.join("|")
  var [order, setOrder] = useWbcState(function () {
    try {
      return wbcNormalizeChatOrder(defaultOrder, JSON.parse(localStorage.getItem(WBC_TASK_ORDER_PREFIX + String(projectId || "")) || "null"))
    } catch (e) {
      return wbcNormalizeChatOrder(defaultOrder, null)
    }
  })
  var [dragId, setDragId] = useWbcState("")

  useWbcEffect(function () {
    var saved = null
    try { saved = JSON.parse(localStorage.getItem(WBC_TASK_ORDER_PREFIX + String(projectId || "")) || "null") } catch (e) {}
    setOrder(wbcNormalizeChatOrder(defaultOrder, saved))
    setDragId("")
  }, [projectId, defaultOrderKey])

  var taskMap = new Map((Array.isArray(tasks) ? tasks : []).map(function (task) { return [String(task.id), task] }))
  var ordered = wbcOrderChatsByPinned(wbcNormalizeChatOrder(defaultOrder, order).map(function (id) {
    return taskMap.get(id)
  }).filter(Boolean), pinnedTaskIds).filter(function (task) {
    var normalized = String(query || "").trim().toLowerCase()
    return !normalized
      || String(task.title || "").toLowerCase().indexOf(normalized) >= 0
      || String(task.goal || task.summary || "").toLowerCase().indexOf(normalized) >= 0
  })
  var pinnedIds = new Set((Array.isArray(pinnedTaskIds) ? pinnedTaskIds : []).map(function (id) { return String(id || "") }))

  function storeOrder(nextOrder) {
    var normalized = wbcNormalizeChatOrder(defaultOrder, nextOrder)
    setOrder(normalized)
    try { localStorage.setItem(WBC_TASK_ORDER_PREFIX + String(projectId || ""), JSON.stringify(normalized)) } catch (e) {}
  }

  return {
    defaultOrder: defaultOrder,
    dragId: dragId,
    order: order,
    ordered: ordered,
    pinned: ordered.filter(function (task) { return pinnedIds.has(String(task.id)) }),
    pinnedIds: pinnedIds,
    recent: ordered.filter(function (task) { return !pinnedIds.has(String(task.id)) }),
    setDragId: setDragId,
    setOrder: setOrder,
    storeOrder: storeOrder,
  }
}

function wbcTaskRailVisualState(task) {
  var raw = String(task && task.status || "idle").toLowerCase()
  var failed = ["failed", "cancelled", "blocked"].indexOf(raw) >= 0
  var attention = ["waiting_for_user", "waiting_for_approval", "review"].indexOf(raw) >= 0 || !!(task && task.pendingQuestion)
  var planning = raw === "planning"
  var running = ["running", "paused"].indexOf(raw) >= 0 || !!(task && task.agentBusy)
  var completed = ["completed", "done", "skipped"].indexOf(raw) >= 0
  var rawSummary = task && task.summary
  var summaryText = typeof rawSummary === "string"
    ? rawSummary
    : rawSummary && typeof rawSummary === "object"
      ? String(rawSummary.text || rawSummary.summary || "")
      : ""
  return {
    tone: failed ? " status-failed" : attention ? " status-attention" : completed ? " status-completed" : running ? " status-running" : planning ? " status-planning" : "",
    icon: failed ? WBC_ICONS.errorCircle : attention ? WBC_ICONS.alert : completed ? WBC_ICONS.check : running ? WBC_ICONS.running : planning ? WBC_ICONS.planning : WBC_ICONS.file,
    preview: summaryText || String(task && task.goal || "") || wbcT("task.summaryFallback", "The agent will create a summary while this task runs."),
  }
}

function WbcTaskRailCard({ activeTaskId, menuId, onDeleteTask, onRenameTask, onSelectTask, onTogglePinnedTask, prepareDragImage, projectId, setMenuId, state, task }) {
    var id = String(task.id || "")
    var visual = wbcTaskRailVisualState(task)
    var isMenuOpen = menuId === "task:" + id
    var isPinned = state.pinnedIds.has(id)
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
        + (state.dragId === id ? " dragging" : "")
        + visual.tone}
      onClick={function () { setMenuId(""); if (onSelectTask) onSelectTask(id) }}
      onKeyDown={function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          if (onSelectTask) onSelectTask(id)
        }
      }}
      onContextMenu={function (event) { event.preventDefault(); event.stopPropagation(); setMenuId("task:" + id) }}
      onDragStart={function (event) {
        if (event.target && event.target.closest && event.target.closest("button")) { event.preventDefault(); return }
        setMenuId("")
        state.setDragId(id)
        wbcSetTaskDrag(event, task, projectId)
        if (event.dataTransfer) prepareDragImage(event.currentTarget, event.dataTransfer, event.clientX, event.clientY)
      }}
      onDragOver={function (event) {
        if (!state.dragId || state.dragId === id || !wbcHasTaskDrag(event)) return
        event.preventDefault()
        event.stopPropagation()
        if (event.dataTransfer) event.dataTransfer.dropEffect = "move"
        var rect = event.currentTarget.getBoundingClientRect()
        var edge = event.clientY < rect.top + rect.height / 2 ? "before" : "after"
        state.setOrder(function (current) { return wbcMoveChatOrder(current, state.dragId, id, edge) })
      }}
      onDrop={function (event) {
        if (!state.dragId || !wbcHasTaskDrag(event)) return
        event.preventDefault(); event.stopPropagation(); state.storeOrder(state.order); state.setDragId("")
      }}
      onDragEnd={function () { state.storeOrder(state.order); state.setDragId("") }}
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
            <button type="button" className="wb-card-menu-btn wbc-chat-card-menu-btn" onClick={function (event) { event.stopPropagation(); setMenuId(isMenuOpen ? "" : "task:" + id) }} aria-label={wbcT("common.moreActions", "More actions")}>{WBC_ICONS.dots}</button>
            {isMenuOpen && <div className="wb-card-menu" role="menu">
              <button type="button" role="menuitem" className="wbc-chat-pin-action" onClick={function (event) { event.stopPropagation(); setMenuId(""); if (onTogglePinnedTask) onTogglePinnedTask(task, !isPinned) }}><span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.pin}</span><span>{isPinned ? wbcT("task.unpin", "Unpin task") : wbcT("task.pin", "Pin task")}</span></button>
              <button type="button" role="menuitem" className="wbc-chat-menu-action" onClick={function (event) { event.stopPropagation(); setMenuId(""); onRenameTask(task) }}><span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span><span>{wbcT("task.rename", "Rename task")}</span></button>
              <button type="button" role="menuitem" className="wbc-chat-menu-action danger" onClick={function (event) { event.stopPropagation(); setMenuId(""); if (onDeleteTask) onDeleteTask(task) }}><span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.trash}</span><span>{wbcT("rail.deleteTask", "Delete task")}</span></button>
            </div>}
          </span>
        </span>
      </span>
      <span className="wbc-chat-card-preview"><WbcHoverMarquee text={visual.preview} /></span>
    </div>
}

function WbcTaskRailCards({ activeTaskId, menuId, onDeleteTask, onRenameTask, onSelectTask, onTogglePinnedTask, prepareDragImage, projectId, setMenuId, state, tasks }) {
  return (Array.isArray(tasks) ? tasks : []).map(function (task) {
    return <WbcTaskRailCard
      key={task.id}
      activeTaskId={activeTaskId}
      menuId={menuId}
      onDeleteTask={onDeleteTask}
      onRenameTask={onRenameTask}
      onSelectTask={onSelectTask}
      onTogglePinnedTask={onTogglePinnedTask}
      prepareDragImage={prepareDragImage}
      projectId={projectId}
      setMenuId={setMenuId}
      state={state}
      task={task}
    />
  })
}

function WbcTaskRailList({ activeTaskId, loading, menuId, onDeleteTask, onRenameTask, onSelectTask, onTogglePinnedTask, prepareDragImage, projectId, query, setMenuId, state }) {
  function cards(tasks) {
    return <WbcTaskRailCards
      activeTaskId={activeTaskId}
      menuId={menuId}
      onDeleteTask={onDeleteTask}
      onRenameTask={onRenameTask}
      onSelectTask={onSelectTask}
      onTogglePinnedTask={onTogglePinnedTask}
      prepareDragImage={prepareDragImage}
      projectId={projectId}
      setMenuId={setMenuId}
      state={state}
      tasks={tasks}
    />
  }

  return <div
    className={"wbc-chat-list workbench-integrated-rail-body wbc-task-list" + (loading ? " is-loading" : "") + (!loading && state.ordered.length === 0 ? " is-empty" : "") + (menuId ? " menu-active" : "")}
    onDragOver={function (event) {
      if (!state.dragId || !wbcHasTaskDrag(event)) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move"
    }}
    onDrop={function (event) {
      if (!state.dragId || !wbcHasTaskDrag(event)) return
      event.preventDefault(); state.storeOrder(state.order); state.setDragId("")
    }}
  >
    <div className="wbc-chat-list-primary">
      {loading && !state.ordered.length ? <div className="workbench-muted wbc-rail-loading" role="status">{wbcT("rail.loadingTasks", "Loading tasks...")}</div> : null}
      {!loading && !state.ordered.length ? <div className="workbench-muted wbc-rail-empty">{query ? wbcT("rail.noMatchingTasks", "No matching tasks.") : wbcT("rail.noTasks", "No tasks yet.")}</div> : null}
      {state.pinned.length ? <section className="wbc-rail-section wbc-rail-section-pinned wbc-task-section">
        <header className="wbc-rail-section-label"><span aria-hidden="true">{WBC_ICONS.pin}</span><b>{wbcT("workbenchChat.pinnedSection", "Pinned")}</b></header>
        <div className="wbc-rail-section-items">{cards(state.pinned)}</div>
      </section> : null}
      {state.recent.length ? <section className="wbc-rail-section wbc-rail-section-recent wbc-task-section">
        <header className="wbc-rail-section-label"><b>{wbcT("workbenchChat.recent", "Recent")}</b></header>
        <div className="wbc-rail-section-items">{cards(state.recent)}</div>
      </section> : null}
    </div>
  </div>
}

export { WbcTaskRailCards, WbcTaskRailList, useWbcTaskRail }
