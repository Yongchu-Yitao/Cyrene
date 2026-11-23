import { WbcRailToolbar } from "../chat/rail-toolbar.jsx"
import { AssistantTaskResources, assistantFileOwners } from "./assistant-task-resources.jsx"
import { WBC_ICONS, wbcSetChatDrag } from "../../workbench-chat.jsx"
import { AssistantTaskGroup } from "./assistant-task-group.jsx"
import { AssistantGripLogo, AssistantLogoProvider } from "./assistant-grip-logo.jsx"
import { AssistantChat } from "./assistant-chat.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WbcPaneCardFrame } from "../chat/split-pane.jsx"

async function assistantApi(url, options = {}) {
  var response = await fetch(url, { method: "GET", ...options });
  if (!response.ok) {
    var detail = await response.text();
    try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
    throw new Error(detail || String(response.status));
  }
  return response.json();
}

export function observeAssistantSidebar({ request, subscribe, onSnapshot, onError, target = window }) {
  const doc = target.document || document;
  let disposed = false, pending = false, dirty = false, queued = false, timer, controller, generation = 0;
  const hidden = () => doc.hidden || doc.visibilityState === 'hidden';
  function clear() { target.clearTimeout(timer); timer = null; queued = false; }
  function schedule(delay, event = false) {
    clear();
    if (disposed || hidden()) return;
    queued = event;
    timer = target.setTimeout(refresh, delay);
  }
  function invalidate() {
    if (disposed || hidden()) return;
    if (pending) { dirty = true; return; }
    if (!queued) schedule(200, true);
  }
  async function refresh() {
    clear();
    if (disposed || hidden()) return;
    if (pending) { dirty = true; return; }
    pending = true; dirty = false;
    const revision = generation;
    controller = new AbortController();
    try {
      const next = await request({ signal: controller.signal });
      if (!disposed && !hidden() && revision === generation) { onSnapshot(next); onError(''); }
    } catch (error) {
      if (!disposed && !hidden() && revision === generation && error.name !== 'AbortError') onError(error.message);
    } finally {
      pending = false;
      // Push handles normal changes; reconcile missed events once a minute.
      schedule(dirty ? 200 : 60000, dirty);
    }
  }
  function visibility() {
    if (hidden()) { generation++; clear(); controller?.abort(); }
    else refresh();
  }
  const unsubscribe = subscribe(event => {
    if (['workbench_chat_changed', 'user_question', 'user_question_answered'].includes(event.type)) invalidate();
  });
  doc.addEventListener('visibilitychange', visibility);
  target.addEventListener('online', invalidate);
  target.addEventListener('cyrene:wbc-chat-lifecycle', invalidate);
  target.addEventListener('cyrene:wbc-refresh-chats', invalidate);
  refresh();
  return () => {
    disposed = true; clear(); controller?.abort(); unsubscribe();
    doc.removeEventListener('visibilitychange', visibility);
    target.removeEventListener('online', invalidate);
    target.removeEventListener('cyrene:wbc-chat-lifecycle', invalidate);
    target.removeEventListener('cyrene:wbc-refresh-chats', invalidate);
  };
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
  var loadingRef = React.useRef(null);
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
    return observeAssistantSidebar({
      request: options => assistantApi(base, options),
      subscribe: listener => workbenchServices.events().subscribe(listener),
      onSnapshot: next => { snapshotRef.current = next; setSnapshot(next); },
      onError: setError,
    });
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
  var content = !snapshot ? <div className={"wbc-page assistant-workspace wbc-side-hidden" + (context.navigation.railCollapsed ? " wbc-nav-collapsed" : "")}>{renderRail(null)}<main className="assistant-chat-card-slot" aria-label="个人助理">
    <WbcPaneCardFrame card={{ id: "assistant-loading", kind: "assistant" }}>
      <div ref={loadingRef} className="workbench-empty assistant-chat-card-state" role="status" aria-label={error ? undefined : "正在加载助理"} aria-busy={!error}>
        <AssistantGripLogo shared surfaceRef={loadingRef} loading />
        {error ? "助理暂时无法加载" : null}
      </div>
    </WbcPaneCardFrame>
  </main></div> : React.createElement(workbenchServices.chat().Page, {
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
        gripActions={surface.gripActions}
        project={project} title={task ? task.title : "Cyrene"} onOpenFile={surface.onOpenFile} onOpenResult={surface.onOpenResult}
        permissionRequests={surface.sessionId === personalAssistantId ? snapshot.sessions.filter(function (item) {
          return item.pending_question && item.pending_question.kind && item.pending_question.kind !== "clarification";
        }) : []} />;
    },
  });
  return <AssistantLogoProvider>{content}</AssistantLogoProvider>;
}
export { PersonalAssistantPage }
