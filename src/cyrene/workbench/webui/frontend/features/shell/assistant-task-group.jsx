import { trackTaskGroupClip } from "../chat/task-group-clip.mjs"
import { createPortal } from "react-dom"
import { WbcChatMenuActions } from "../chat/chat-menu-actions.jsx"
import { wbcChatRailVisualState } from "../chat/rail-status.mjs"
import { TaskConversationIcon } from "./task-conversation-icon.jsx"
import { readTopbarPortalTheme } from "./topbar-presentation.jsx"
import { WbcRailGroupToggle, WbcRailGroupContent } from "../chat/rail-group.jsx"

export function AssistantTaskGroup({ task, chat, menuActions, query, matches, hasChildren = false, onOpen, onDragStart, children }) {
  var groupRef = React.useRef(null);
  var [menu, setMenu] = React.useState(null);
  function closeMenu() { setMenu(null); }
  function openMenu(event) {
    if (!chat || !menuActions) return;
    event.preventDefault();
    event.stopPropagation();
    var rect = event.currentTarget.getBoundingClientRect();
    setMenu({ left: Math.max(8, Math.min(event.clientX || rect.left, window.innerWidth - 240)),
      top: Math.max(8, Math.min(event.clientY || rect.bottom, window.innerHeight - 160)),
      portalTheme: readTopbarPortalTheme() });
  }
  React.useEffect(function () {
    if (!menu) return;
    function escape(event) { if (event.key === "Escape") closeMenu(); }
    window.addEventListener("keydown", escape);
    return function () { window.removeEventListener("keydown", escape); };
  }, [menu]);
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
  React.useEffect(function () {
    if (expanded) return trackTaskGroupClip(groupRef.current);
  }, [expanded]);
  var status = wbcChatRailVisualState(task, ["running", "resumed", "planning", "initializing", "finishing"].includes(task.status), {}, function (_, fallback) { return fallback; });
  if (task.status === "planning") status.tone = " status-planning";
  var title = (task.presentation || {}).title || task.title;
  return <section ref={groupRef} className={"wbc-chat-group assistant-task-group" + status.tone + (!matches ? " search-resources-only" : "") + " " + (expanded ? "expanded" : "collapsed")} aria-label={title}>
    <header className="wbc-chat-group-head">
      <WbcRailGroupToggle title={title} expanded={expanded} expandable={hasChildren}
        icon={status.running ? <span className="wb-spinner wbc-chat-running-spinner" aria-hidden="true" /> : <TaskConversationIcon />}
        draggable="true" onDragStart={onDragStart} onContextMenu={openMenu}
        onKeyDown={function (event) {
          if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) openMenu(event);
        }}
        aria-controls={hasChildren ? "task-group-" + task.session_id : undefined} onClick={function () {
          if (!hasChildren) { if (onOpen) onOpen(); return; }
          var next = !collapsed;
          setCollapsed(next);
          if (!next && onOpen) onOpen();
          try { localStorage.setItem(key, next ? "collapsed" : "expanded"); } catch (_) {}
        }} />
    </header>
    {menu && createPortal(<div className="wb-item-context-layer wbc-page-context-layer" style={menu.portalTheme}>
      <div className="wb-item-context-scrim" onPointerDown={closeMenu} />
      <div className="wb-item-context-menu wbc-page-context-menu" role="menu" style={{ left: menu.left, top: menu.top }}
        onContextMenu={function (event) { event.preventDefault(); }}>
        <WbcChatMenuActions chat={chat} isPinned={(menuActions.pinnedChatIds || []).includes(chat.id)}
          onClose={closeMenu} onTogglePinned={menuActions.togglePinned}
          onRename={menuActions.renameChat} onDelete={menuActions.deleteChat} />
      </div>
    </div>, document.body)}
    <WbcRailGroupContent id={"task-group-" + task.session_id} expanded={expanded}>{children}</WbcRailGroupContent>
  </section>;
}
