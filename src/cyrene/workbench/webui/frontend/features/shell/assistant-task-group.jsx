import { wbcChatRailVisualState } from "../chat/rail-status.mjs"
import { TaskConversationIcon } from "./task-conversation-icon.jsx"
import { WbcRailGroupToggle, WbcRailGroupContent } from "../chat/rail-group.jsx"

export function AssistantTaskGroup({ task, query, matches, hasChildren = false, onOpen, onDragStart, children }) {
  var key = "cyrene.assistant.task-group." + task.session_id;
  var [collapsed, setCollapsed] = React.useState(function () {
    try { return localStorage.getItem(key) !== "expanded"; } catch (_) { return true; }
  });
  React.useEffect(function () {
    if (query) setCollapsed(false);
    else {
      try { setCollapsed(localStorage.getItem(key) !== "expanded"); } catch (_) {}
    }
  }, [query, key]);
  var expanded = hasChildren && !collapsed;
  var status = wbcChatRailVisualState(task, ["running", "resumed", "planning", "initializing", "finishing"].includes(task.status), {}, function (_, fallback) { return fallback; });
  if (task.status === "planning") status.tone = " status-planning";
  var title = (task.presentation || {}).title || task.title;
  return <section className={"wbc-chat-group assistant-task-group" + status.tone + (!matches ? " search-resources-only" : "") + " " + (expanded ? "expanded" : "collapsed")} aria-label={title}>
    <header className="wbc-chat-group-head">
      <WbcRailGroupToggle title={title} expanded={expanded} expandable={hasChildren} icon={<TaskConversationIcon />}
        draggable="true" onDragStart={onDragStart}
        aria-controls={hasChildren ? "task-group-" + task.session_id : undefined} onClick={function () {
          if (!hasChildren) { if (onOpen) onOpen(); return; }
          var next = !collapsed;
          setCollapsed(next);
          if (!next && onOpen) onOpen();
          try { localStorage.setItem(key, next ? "collapsed" : "expanded"); } catch (_) {}
        }} />
    </header>
    <WbcRailGroupContent id={"task-group-" + task.session_id} expanded={expanded}>{children}</WbcRailGroupContent>
  </section>;
}
