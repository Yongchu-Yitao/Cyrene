import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WorkbenchTopbar } from "./topbar.jsx"
import { ConversationBoard } from "../chat/conversation-board.jsx"
import { wbDeliverResourceToChat } from "../session/activity.jsx"
import { loadSessionTabBrowserPreview, loadSessionTabResources } from "../session/resources.jsx"
import { wbErrorText } from "../../shared/errors.jsx"

var WorkbenchStableSurface = React.memo(
  function WorkbenchStableSurface({ active, enterMotion, children }) {
    return (
      <div
        className={"workbench-stable-surface"
          + (active ? " is-active" : " is-hidden")
          + (enterMotion ? " has-page-enter-motion" : "")}
        style={{ display: active ? "contents" : "none" }}
      >
        {children}
      </div>
    );
  },
  function keepHiddenSurfaceStable(prev, next) {
    return !prev.active && !next.active;
  }
);

function wbOpenPinnedResource(context, resource) {
  if (!resource) return;
  var navigation = context.navigation;
  var sessions = context.sessions;
  if (resource.kind === "conversation") {
    navigation.navigate({
      type: "chat", projectId: resource.ownerProjectId,
      chatId: resource.conversationId || resource.ownerSessionId,
    });
    return;
  }
  if (resource.kind === "snippet") {
    var target = navigation.fullPage === "chat" && sessions.activeChatId
      ? sessions.activeChatId : resource.ownerSessionId;
    if (target) wbDeliverResourceToChat(target, resource);
    return;
  }
  if (!resource.ownerSessionId) return;
  var owner = sessions.candidates.find(function (item) {
    return item.kind === "chat" && String(item.id || "") === String(resource.ownerSessionId || "");
  });
  if (!owner) return;
  if (resource.kind === "browser" && resource.tabId) {
    var browserBridge = window.cyrene && window.cyrene.browser;
    if (browserBridge && typeof browserBridge.activateTab === "function") {
      browserBridge.activateTab({ sessionId: resource.ownerSessionId, tabId: resource.tabId }).catch(function () {});
    }
  }
  sessions.openResource(owner, resource.kind === "file"
    ? { type: "file", file: resource.file && Object.keys(resource.file).length ? resource.file : resource }
    : { type: "browser" });
}

function wbOpenTopbarSession(context, item) {
  if (!item || item.kind !== "chat") return;
  context.sessions.rememberOpened("chat", item.id);
  context.navigation.navigate({ type: "chat", projectId: item.projectId, chatId: item.id });
}

function wbOpenBrowserPage(context, page, owner) {
  var target = owner || context.sessions.browserOwners.find(function (item) {
    return String(item.id || "") === String(page && page.sessionId || "");
  });
  if (!target) return;
  context.sessions.rememberOpened("chat", target.id);
  context.navigation.navigate({ type: "chat", projectId: target.projectId, chatId: target.id });
}

function wbStopTopbarSession(context, item) {
  if (!item || item.kind !== "chat" || !context.chat.runtimeEngine) return Promise.resolve(null);
  return context.chat.runtimeEngine.interrupt(item.id, context.chat.module.Model).catch(function (error) {
    workbenchServices.feedback().showToast(wbErrorText(error), "error");
    return null;
  });
}

function WorkbenchShellTopbar({ context }) {
  var appearance = context.appearance;
  var navigation = context.navigation;
  var sessions = context.sessions;
  var dialogs = context.dialogs;
  return <WorkbenchTopbar
    projects={context.store.projects}
    activeProject={context.store.activeProject}
    activePage={navigation.fullPage}
    activeChatId={sessions.activeChatId}
    recentSessions={sessions.recent}
    overflowSessions={sessions.overflow}
    browserOwners={sessions.browserOwners}
    pinnedResources={context.resources.pinned}
    keyboardEnabled={!dialogs.searchOpen && !dialogs.newProjectOpen && !dialogs.editProject && !dialogs.editMemoryProject}
    onPinResource={context.resources.pin}
    onUnpinResource={context.resources.unpin}
    onOpenPinnedResource={function (resource) { wbOpenPinnedResource(context, resource); }}
    onTogglePinnedSession={sessions.togglePinned}
    onRemoveSessionTab={sessions.removeTab}
    onLoadSessionResources={loadSessionTabResources}
    onLoadSessionBrowserPreview={loadSessionTabBrowserPreview}
    onOpenSessionResource={sessions.openResource}
    onOpenSession={function (item) { wbOpenTopbarSession(context, item); }}
    onOpenBrowserPage={function (page, owner) { wbOpenBrowserPage(context, page, owner); }}
    onStopSession={function (item) { return wbStopTopbarSession(context, item); }}
    notifications={context.resources.notifications}
    onReloadNotifications={context.resources.reloadNotifications}
    onOpenNotification={navigation.navigateNotification}
    onSearch={function () { dialogs.setSearchOpen(true); }}
    onSettings={navigation.openSettings}
    onNewProject={context.actions.createProject}
    onSelectProject={context.actions.selectProject}
    onEditProject={dialogs.setEditProject}
    onEditMemory={dialogs.setEditMemoryProject}
    onDeleteProject={context.actions.deleteProject}
    onOpenPage={navigation.openPage}
    theme={appearance.theme}
    actualTheme={appearance.actualTheme}
    onToggleTheme={appearance.onToggleTheme}
  />;
}

function WorkbenchChatModuleSurface({ context }) {
  var presentation = context.presentation;
  var sessions = context.sessions;
  var project = context.store.activeProject;
  var sharedWorkspace = presentation.isBoard ? (
    <ConversationBoard
      project={project}
      chats={project && context.projectRail.recentChatsByProject[project.id] || []}
      loading={context.board.loading}
      error={context.board.error}
      onOpenChat={context.board.openChat}
      onCreateChat={context.actions.createChat}
    />
  ) : null;
  return presentation.showChatPage || presentation.isBoard ? (
    <WorkbenchStableSurface active={presentation.isChat || presentation.isBoard}>
      {React.createElement(workbenchServices.chat().Page || function () { return <div className="workbench-empty">{context.t("workbench.chatLoading")}</div>; }, {
        active: presentation.isChat,
        project: context.store.activeProject,
        workspaceContent: sharedWorkspace,
        onActivateWorkspace: function () { context.navigation.openPage("work"); },
        newChatRequestId: context.chat.newChatRequestId,
        onActiveChatIdChange: sessions.setActiveChatId,
        onChatsChange: sessions.updateRecentChats,
        pinnedSessions: sessions.pinnedView,
        navCollapsed: context.navigation.railCollapsed,
        onToggleNavCollapsed: context.navigation.toggleSidebar,
        collapseControl: presentation.isChat || presentation.isBoard ? context.navigation.renderCollapseControl() : null,
        moduleDock: presentation.isChat || presentation.isBoard ? context.navigation.renderDockSlot() : null,
      })}
    </WorkbenchStableSurface>
  ) : null;
}

function WorkbenchSecondaryModuleSurfaces({ context }) {
  var p = context.presentation;
  var navigation = context.navigation;
  var appearance = context.appearance;
  var project = context.store.activeProject;
  function fallback(key) { return function () { return <div className="workbench-empty">{context.t(key)}</div>; }; }
  return <>
    {p.showKnowledgePage && <WorkbenchStableSurface active={p.isKnowledge} enterMotion={true}>
      {React.createElement(workbenchServices.library().Page || fallback("workbench.knowledgeLoading"), {
        active: p.isKnowledge, project: project, onBack: navigation.closePage,
        onNavigate: navigation.navigate, sidebarCollapsed: navigation.railCollapsed,
        collapseControl: p.isKnowledge ? navigation.renderCollapseControl() : null,
        moduleDock: p.isKnowledge ? navigation.renderDockSlot() : null,
      })}
    </WorkbenchStableSurface>}
    {p.showSchedulePage && <WorkbenchStableSurface active={p.isSchedule} enterMotion={true}>
      {React.createElement(workbenchServices.schedule().Page || fallback("workbench.scheduleLoading"), {
        active: p.isSchedule, project: project, onBack: navigation.closePage,
        sidebarCollapsed: navigation.railCollapsed,
        collapseControl: p.isSchedule ? navigation.renderCollapseControl() : null,
        moduleDock: p.isSchedule ? navigation.renderDockSlot() : null,
      })}
    </WorkbenchStableSurface>}
    {p.showMemoryPage && <WorkbenchStableSurface active={p.isMemory} enterMotion={true}>
      {React.createElement(workbenchServices.memory().Page || fallback("workbench.memoryLoading"), {
        active: p.isMemory, project: project, onBack: navigation.closePage,
        onEditProjectMemory: context.dialogs.editActiveProjectMemory,
        sidebarCollapsed: navigation.railCollapsed,
        collapseControl: p.isMemory ? navigation.renderCollapseControl() : null,
        moduleDock: p.isMemory ? navigation.renderDockSlot() : null,
      })}
    </WorkbenchStableSurface>}
    {p.showSettingsPage && <WorkbenchStableSurface active={p.isSettings}>
      {React.createElement(workbenchServices.settings().Page, {
        active: p.isSettings, collapsed: navigation.railCollapsed,
        collapseControl: p.isSettings ? navigation.renderCollapseControl() : null,
        moduleDock: p.isSettings ? navigation.renderDockSlot() : null,
        initialTab: context.dialogs.settingsTab, scrollToId: context.dialogs.settingsScrollTo,
        theme: appearance.theme, actualTheme: appearance.actualTheme,
        onToggleTheme: appearance.onToggleTheme, project: project,
      })}
    </WorkbenchStableSurface>}
  </>;
}

function WorkbenchModuleSurfaces({ context }) {
  return <>
    <WorkbenchChatModuleSurface context={context} />
    <WorkbenchSecondaryModuleSurfaces context={context} />
  </>;
}

export { WorkbenchModuleSurfaces, WorkbenchShellTopbar, WorkbenchStableSurface }
