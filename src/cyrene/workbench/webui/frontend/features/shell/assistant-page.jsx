import { usePinnedAssistantCards } from "../chat/pinned-assistant-cards.jsx"
import { WbcRailToolbar } from "../chat/rail-toolbar.jsx"
import { AssistantTaskResources, assistantFileOwners } from "./assistant-task-resources.jsx"
import { WBC_ICONS, wbcSetChatDrag } from "../../workbench-chat.jsx"
import { AssistantTaskGroup } from "./assistant-task-group.jsx"
import { AssistantChat } from "./assistant-chat.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WbcPaneCardFrame } from "../chat/split-pane.jsx"

async function assistantApi(url) {
  var response = await fetch(url, { method: "GET" });
  if (!response.ok) {
    var detail = await response.text();
    try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
    throw new Error(detail || String(response.status));
  }
  return response.json();
}

function useResourceCounts() {
  var [resourceCounts, setResourceCounts] = React.useState({});
  var receiveResourceCount = React.useCallback(function (id, count) {
    setResourceCounts(function (previous) { return previous[id] === count ? previous : {...previous, [id]: count}; });
  }, []);
  return [resourceCounts, receiveResourceCount];
}

function useTaskFiles() {
  var [filesByTask, setFilesByTask] = React.useState({});
  var receiveFiles = React.useMemo(function () { return function (id, files) {
    setFilesByTask(function (previous) {
      return JSON.stringify(previous[id]) === JSON.stringify(files) ? previous : {...previous, [id]: files};
    });
  }; }, []);
  return [filesByTask, receiveFiles];
}

function compareAssistantTasks(a, b) {
  var position = ((a.presentation || {}).position ?? 2147483647) - ((b.presentation || {}).position ?? 2147483647);
  if (position) return position;
  return (Date.parse(b.created_at || b.updated_at) || 0) - (Date.parse(a.created_at || a.updated_at) || 0);
}

function PersonalAssistantPage({ context, active = true }) {
  var pinnedCards = usePinnedAssistantCards(active);
  var [snapshot, setSnapshot] = React.useState(null);
  var snapshotRef = React.useRef(null);
  var [error, setError] = React.useState("");
  var [selected, setSelected] = React.useState("");
  var [showArchived, setShowArchived] = React.useState(false);
  var [query, setQuery] = React.useState("");
  var [homeRevision, setHomeRevision] = React.useState(0);
  var [attempt, setAttempt] = React.useState(0);
  var [resourceCounts, receiveResourceCount] = useResourceCounts();
  var [filesByTask, receiveFiles] = useTaskFiles();
  var tasks = snapshot ? snapshot.sessions : [];
  var fileOwners = assistantFileOwners(tasks, filesByTask);
  var search = query.trim().toLocaleLowerCase();
  var base = "/api/workbench/assistant";
  var project = snapshot && snapshot.scope;
  var sessionId = selected || (snapshot && snapshot.assistant_id) || "";
  var personalAssistantId = (snapshot && snapshot.assistant_id) || "";
  var setAssistantSessionId = context.assistant.setSessionId;
  React.useEffect(function () {
    if (personalAssistantId) setAssistantSessionId(personalAssistantId);
  }, [personalAssistantId, setAssistantSessionId]);
  React.useEffect(function () {
    if (!active) return;
    var cancelled = false;
    var timer;
    async function refresh() {
      try {
        var next = await assistantApi(base);
        if (!cancelled) { snapshotRef.current = next; setSnapshot(next); setError(""); }
      } catch (err) { if (!cancelled) setError(err.message); }
      if (!cancelled) timer = setTimeout(refresh, 3000);
    }
    refresh();
    return function () { cancelled = true; clearTimeout(timer); };
  }, [base, attempt, active]);
  var chatModel = workbenchServices.chat().Model;
  var managedModel = React.useMemo(function () {
    return Object.assign({}, chatModel, {
      listChats: async function () { return (snapshotRef.current || await assistantApi(base)).chats; },
    });
  }, [chatModel, base]);
  function dragSession(event, id) {
    var chat = snapshot && snapshot.chats.find(function (item) { return item.id === id; });
    if (chat) wbcSetChatDrag(event, chat);
    else event.preventDefault();
  }
  function home() {
    setQuery("");
    setSelected(personalAssistantId);
    setHomeRevision(function (value) { return value + 1; });
  }
  React.useEffect(function () {
    window.addEventListener("cyrene:assistant-home", home);
    return function () { window.removeEventListener("cyrene:assistant-home", home); };
  }, [personalAssistantId]);
  function renderRail(surface) {
    var empty = snapshot && !tasks.length;
    var rail = <aside className={"wbc-rail workbench-cyrene-rail workbench-integrated-rail" + (context.navigation.railCollapsed ? " is-collapsed" : "")} aria-label="个人助理">
      <WbcRailToolbar collapsed={context.navigation.railCollapsed}
        onAction={home} actionLabel="回到主对话" actionIcon={WBC_ICONS.chat}
        query={query} onQueryChange={setQuery} searchNodeId="assistant_search_input"
        collapseControl={context.navigation.renderCollapseControl()} />
      <div className={"wbc-chat-list workbench-integrated-rail-body" + (empty ? " is-empty" : "")}>
        {error && <div role="alert" className="assistant-error"><p>{error}</p><button onClick={function () { setError(""); setAttempt(function (value) { return value + 1; }); }}>重新加载</button></div>}
        <div className="wbc-chat-list-primary">{empty ? <div className="assistant-muted wbc-rail-empty">暂无任务</div> : <div className="wbc-rail-section-items">
          {tasks.filter(function (task) { return showArchived || !(task.presentation || {}).archived; }).sort(compareAssistantTasks).map(function (task) {
            var groupMatches = !search || String((task.presentation || {}).title || task.title || "").toLocaleLowerCase().includes(search);
            return <AssistantTaskGroup key={task.session_id} task={task} query={search} matches={groupMatches} hasChildren={resourceCounts[task.session_id] > 0}
              chat={snapshot.chats.find(function (chat) { return chat.id === task.session_id; })}
              menuActions={surface}
              onDragStart={function (event) { dragSession(event, task.session_id); }}
              onOpen={function () { if (surface) surface.openChat(task.session_id); }}>
            {surface && <AssistantTaskResources task={task} onOpen={surface.openResource} onFiles={receiveFiles} onCount={receiveResourceCount} fileOwners={fileOwners} query={groupMatches ? "" : search} active={active} />}
            </AssistantTaskGroup>;
          })}
        </div>}</div>
      </div>
      {tasks.some(function (task) { return (task.presentation || {}).archived; }) && <button type="button" className="wbc-rail-show-all" onClick={function () { setShowArchived(!showArchived); }}>{showArchived ? "收起已归档任务" : "显示已归档任务"}</button>}
      {context.navigation.renderDockSlot()}
    </aside>;
    return rail;
  }
  if (!snapshot) return <div className={"wbc-page assistant-workspace wbc-side-hidden" + (context.navigation.railCollapsed ? " wbc-nav-collapsed" : "")}>{renderRail(null)}<main className="assistant-chat-card-slot" aria-label="个人助理">
    <WbcPaneCardFrame card={{ id: "assistant-loading", kind: "assistant" }}>
      <div className="workbench-empty assistant-chat-card-state" role="status">{error ? "助理暂时无法加载" : "正在加载 Cyrene…"}</div>
    </WbcPaneCardFrame>
  </main></div>;
  return React.createElement(workbenchServices.chat().Page, {
    pinnedCards: pinnedCards.cards, pinnedCardsError: pinnedCards.error,
    active: active, project: project, isolated: true, modelOverride: managedModel,
    protectedChatId: personalAssistantId,
    railOverride: renderRail, requestedChatId: sessionId, requestedChatRevision: homeRevision, hideConversationPanel: true,
    navCollapsed: context.navigation.railCollapsed,
    onToggleNavCollapsed: context.navigation.toggleSidebar,
    onActiveChatIdChange: function (id) { if (id) setSelected(id); },
    renderConversation: function (surface) {
      var task = tasks.find(function (item) { return item.session_id === surface.sessionId; });
      return <AssistantChat key={surface.sessionId} sessionId={surface.sessionId} active={active} cache={surface.chatCache}
        personalAssistant={surface.sessionId === personalAssistantId}
        project={project} title={task ? task.title : "Cyrene"} onOpenFile={surface.onOpenFile}
        permissionRequests={surface.sessionId === personalAssistantId ? snapshot.sessions.filter(function (item) {
          return item.pending_question && item.pending_question.kind && item.pending_question.kind !== "clarification";
        }) : []} />;
    },
  });

}
export { PersonalAssistantPage }
