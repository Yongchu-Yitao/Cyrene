import { WBC_ICONS, wbcT } from "../../workbench-chat.jsx"

// Shared by Work and the personal assistant; geometry belongs to the Work rail.
export function WbcRailToolbar({ collapsed, onAction, actionLabel, actionIcon, actionRef, actionNodeId, query, onQueryChange, searchRef, searchNodeId, collapseControl, onToggleCollapsed }) {
  return (
      <div className="wbc-rail-glass">
        <div className="wbc-nav-card">
          <div className="wbc-nav-card-head workbench-integrated-rail-head workbench-integrated-rail-search-head">
            {!collapsed && onAction && (
              <button
                ref={actionRef}
                data-cyrene-node-id={actionNodeId}
                type="button"
                className="wbc-project-new-chat"
                onClick={onAction}
                title={actionLabel}
                aria-label={actionLabel}
              >{actionIcon || WBC_ICONS.plus}</button>
            )}
            {!collapsed && (
              <div className="wbc-search">
                <span className="wbc-search-icon">{WBC_ICONS.search}</span>
                <input
                  ref={searchRef}
                  data-cyrene-node-id={searchNodeId}
                  value={query}
                  onChange={function (e) { onQueryChange(e.target.value); }}
                  placeholder={wbcT("rail.searchEverythingShort", "Search all")}
                  aria-label={wbcT("rail.searchEverything", "Search chats, files, and terminals")}
                />
              </div>
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
  );
}
