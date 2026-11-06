import { WbcRailToolbar } from "../chat/rail-toolbar.jsx"
import { AssistantTaskResources, assistantFileOwners } from "./assistant-task-resources.jsx"
import { WbcHoverMarquee } from "../chat/rail-model.jsx"
import { wbcChatRailVisualState } from "../chat/rail-status.mjs"
import { WBC_ICONS, wbcFormatTime, wbcT, wbcSetChatDrag } from "../../workbench-chat.jsx"
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

function PersonalAssistantPage({ context, active = true }) {
  var [snapshot, setSnapshot] = React.useState(null);
  var snapshotRef = React.useRef(null);
  var [error, setError] = React.useState("");
  var [selected, setSelected] = React.useState("");
  var [query, setQuery] = React.useState("");
  var [homeRevision, setHomeRevision] = React.useState(0);
  var [attempt, setAttempt] = React.useState(0);
  var [filesByTask, setFilesByTask] = React.useState({});
  var receiveFiles = React.useMemo(function () { return function (id, files) {
    setFilesByTask(function (previous) {
      return JSON.stringify(previous[id]) === JSON.stringify(files) ? previous : {...previous, [id]: files};
    });
  }; }, []);
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
  function open(id) { setSelected(id); }
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
    var rail = <aside className={"wbc-rail workbench-cyrene-rail workbench-integrated-rail" + (context.navigation.railCollapsed ? " is-collapsed" : "")} aria-label="个人助理">
      <WbcRailToolbar collapsed={context.navigation.railCollapsed}
        onAction={home} actionLabel="回到主对话" actionIcon={WBC_ICONS.chat}
        query={query} onQueryChange={setQuery} searchNodeId="assistant_search_input"
        collapseControl={context.navigation.renderCollapseControl()} />
      <div className="wbc-chat-list workbench-integrated-rail-body">
        {error && <div role="alert" className="assistant-error"><p>{error}</p><button onClick={function () { setError(""); setAttempt(function (value) { return value + 1; }); }}>重新加载</button></div>}
        <div className="wbc-chat-list-primary"><div className="wbc-rail-section-items">
          {tasks.slice().sort(function (a, b) {
            return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
          }).map(function (task) {
            var chat = {id: task.session_id, title: task.title, status: task.status, pendingQuestion: task.pending_question};
            var visual = wbcChatRailVisualState(chat, task.status === "running", WBC_ICONS, wbcT);
            return <React.Fragment key={task.session_id}>{(!search || String(task.title || "").toLocaleLowerCase().includes(search)) && <div role="button" tabIndex={0} draggable="true"
              className={"wbc-chat-card" + visual.tone + (sessionId === task.session_id ? " active" : "")}
              title={task.title} aria-label={task.title} aria-current={sessionId === task.session_id ? "true" : undefined}
              onClick={function () { open(task.session_id); }}
              onKeyDown={function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(task.session_id); } }}
              onDragStart={function (event) { dragSession(event, task.session_id); }}>
              <span className="wbc-chat-card-top">
                <span className="wbc-chat-row-icon" aria-hidden="true">{visual.icon}</span>
                <span className="wbc-chat-card-title"><b><WbcHoverMarquee text={task.title} /></b></span>
                <span className="wbc-chat-card-right">
                  {visual.label ? <em className="wbc-chat-card-status">{visual.label}</em>
                    : <time className="wbc-chat-card-time">{wbcFormatTime(task.updated_at)}</time>}
                </span>
              </span>
            </div>}
            {surface && <AssistantTaskResources task={task} onOpen={surface.openResource} onFiles={receiveFiles} fileOwners={fileOwners} query={search} active={active} />}
            </React.Fragment>;
          })}
        </div></div>
        {snapshot && !tasks.length && <p className="assistant-muted">委派任务后，对话会显示在这里。</p>}
      </div>
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
    active: active, project: project, isolated: true, modelOverride: managedModel,
    railOverride: renderRail, requestedChatId: sessionId, requestedChatRevision: homeRevision, hideConversationPanel: true,
    navCollapsed: context.navigation.railCollapsed,
    onToggleNavCollapsed: context.navigation.toggleSidebar,
    onActiveChatIdChange: function (id) { if (id) setSelected(id); },
    renderConversation: function (surface) {
      var task = tasks.find(function (item) { return item.session_id === surface.sessionId; });
      return <AssistantChat key={surface.sessionId} sessionId={surface.sessionId} active={active} cache={surface.chatCache}
        project={project} title={task ? task.title : "Cyrene"} onOpenFile={surface.onOpenFile}
        permissionRequests={surface.sessionId === personalAssistantId ? snapshot.sessions.filter(function (item) {
          return item.pending_question && item.pending_question.kind && item.pending_question.kind !== "clarification";
        }) : []} />;
    },
  });

}
export { PersonalAssistantPage }
