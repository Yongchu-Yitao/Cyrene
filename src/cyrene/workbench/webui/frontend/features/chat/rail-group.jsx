import { WBC_ICONS } from "../../workbench-chat.jsx"
import { WbcHoverMarquee } from "./rail-model.jsx"

// Shared presentation for ordinary conversation groups and assistant task groups.
export function WbcRailGroupToggle({ title, expanded, count, icon, expandable = true, ...props }) {
  return <button type="button" className="wbc-chat-group-toggle" aria-expanded={expandable ? expanded : undefined} {...props}>
    {expandable && <span className={"wbc-chat-group-leading-chevron" + (expanded ? " expanded" : "")} aria-hidden="true">{WBC_ICONS.chevronRight}</span>}
    {icon && <span className="wbc-chat-row-icon assistant-task-conversation-icon" aria-hidden="true">{icon}</span>}
    {count != null && <span className="wbc-chat-group-count">{count}</span>}
    <b><WbcHoverMarquee text={title} /></b>
  </button>;
}

export function WbcRailGroupContent({ expanded, children, id }) {
  return <div id={id} className={"wbc-chat-group-content " + (expanded ? "expanded" : "collapsed")}
    aria-hidden={!expanded} inert={!expanded ? "" : undefined}>
    <div className="wbc-chat-group-content-inner"><div className="wbc-chat-group-children">{children}</div></div>
  </div>;
}
