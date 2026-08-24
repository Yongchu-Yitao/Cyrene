import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WorkbenchTopbar } from "./topbar.jsx"
import { RightContextPanel, TaskBoard, TaskWorkArea, WorkbenchTaskPane } from "../task/index.jsx"
import { wbDeliverResourceToChat } from "../session/activity.jsx"
import { loadSessionTabBrowserPreview, loadSessionTabResources } from "../session/resources.jsx"
import { mergeTaskResponse } from "../task/store-merge.jsx"
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
  if (!item) return;
  context.sessions.rememberOpened(item.kind, item.id);
  context.navigation.navigate(item.kind === "chat"
    ? { type: "chat", projectId: item.projectId, chatId: item.id }
    : { type: "task", projectId: item.projectId, sessionId: item.id });
}

function wbOpenBrowserPage(context, page, owner) {
  var target = owner || context.sessions.browserOwners.find(function (item) {
    return String(item.id || "") === String(page && page.sessionId || "");
  });
  if (!target) return;
  context.sessions.rememberOpened("chat", target.id);
  context.navigation.navigate({ type: "chat", projectId: target.projectId, chatId: target.id });
}

function wbPauseTopbarSession(context, item) {
  if (!item || item.kind !== "task") return Promise.resolve(null);
  var model = context.model;
  var t = context.t;
  return model.fetchSession(item.id).then(function (payload) {
    var session = payload && payload.session;
    if (!session) throw new Error(t("workbench.sessionActivity.missing", "Session is unavailable"));
    if (session.goalLoop && session.goalLoop.status === "running") return model.pauseGoalLoop(item.id);
    return model.interruptSession(item.id).then(function () {
      var now = new Date().toISOString();
      var plan = Array.isArray(session.plan) ? session.plan.map(function (step) {
        if (!step || step.status !== "running") return step;
        return Object.assign({}, step, {
          status: "pending", startedAt: null,
          currentAction: t("workbench.sessionActivity.stoppedAction", "Stopped; ready to run again."),
          updatedAt: now,
        });
      }) : session.plan;
      return model.patchSession(item.id, {
        status: "paused", plan: plan,
        agentReply: t("workbench.sessionActivity.pausedReply", "Execution was paused from the topbar."),
        events: model.withEvent(session, "Paused", t("workbench.sessionActivity.pausedEvent", "Paused from the topbar.")),
      });
    });
  }).then(function (next) {
    if (next && next.projects) context.setStore(function (previous) {
      return mergeTaskResponse(previous, next, item.id);
    });
    return next;
  }).catch(function (error) {
    workbenchServices.feedback().showToast(wbErrorText(error), "error");
    return null;
  });
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
    taskView={context.task.view}
    activeTaskId={context.store.activeSessionId}
    activeChatId={sessions.activeChatId}
    recentSessions={sessions.recent}
    overflowSessions={sessions.overflow}
    browserOwners={sessions.browserOwners}
    pinnedResources={context.resources.pinned}
    keyboardEnabled={!dialogs.searchOpen && !dialogs.newProjectOpen && !dialogs.newTaskOpen && !dialogs.editProject && !dialogs.editMemoryProject}
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
    onPauseSession={function (item) { return wbPauseTopbarSession(context, item); }}
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
    onNewTask={context.actions.createSession}
    onOpenPage={navigation.openPage}
    theme={appearance.theme}
    actualTheme={appearance.actualTheme}
    onToggleTheme={appearance.onToggleTheme}
  />;
}

function WorkbenchChatModuleSurface({ context }) {
  var presentation = context.presentation;
  var task = context.task;
  var sessions = context.sessions;
  return presentation.showChatPage ? (
    <WorkbenchStableSurface active={presentation.isChat}>
      {React.createElement(workbenchServices.chat().Page || function () { return <div className="workbench-empty">{context.t("workbench.chatLoading")}</div>; }, {
        active: presentation.isChat,
        project: context.store.activeProject,
        newChatRequestId: context.chat.newChatRequestId,
        taskOpenRequest: task.openRequest,
        taskWorkspace: {
          tasks: context.store.activeProject && Array.isArray(context.store.activeProject.sessions) ? context.store.activeProject.sessions : [],
          activeTaskId: context.store.activeSessionId,
          onSelectTask: task.selectSession, onCreateTask: context.actions.createSession,
          onDeleteTask: context.actions.deleteSession, onRenameTask: task.renameRailTask,
          onStoreChange: task.mergeStore, PaneComponent: WorkbenchTaskPane,
          ContextPanelComponent: RightContextPanel, onOpenTask: task.chatToTask,
        },
        onActiveChatIdChange: sessions.setActiveChatId,
        onChatsChange: sessions.updateRecentChats,
        pinnedSessions: sessions.pinnedView,
        navCollapsed: context.navigation.railCollapsed,
        onToggleNavCollapsed: context.navigation.toggleSidebar,
        collapseControl: presentation.isChat ? context.navigation.renderCollapseControl() : null,
        moduleDock: presentation.isChat ? context.navigation.renderDockSlot() : null,
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

function WorkbenchProjectRail({ context }) {
  var rail = context.projectRail;
  var sessions = context.sessions;
  var project = context.store.activeProject;
  return React.createElement(context.chat.module.Rail, {
    active: !context.presentation.isModulePage,
    projectId: project && project.id || "",
    projectName: project && project.name || "",
    chats: project && rail.recentChatsByProject[project.id] || [],
    tasks: project && project.sessions || [],
    railMode: rail.mode,
    workRailMode: rail.mode,
    pinnedChatIds: sessions.pinnedView.chatIds,
    pinnedTaskIds: sessions.pinnedView.taskIds,
    activeChatId: sessions.activeChatId,
    activeTaskId: context.store.activeSessionId,
    loading: context.task.loading,
    runningChatIds: [],
    runtimeEngine: context.chat.runtimeEngine,
    onSelect: rail.selectChat,
    onSelectTask: context.task.openTask,
    onAnswer: function () {},
    onCreate: context.actions.createChat,
    onCreateTask: context.actions.createSession,
    onRename: rail.renameChat,
    onRenameTask: rail.renameTask,
    onDelete: rail.deleteChat,
    onDeleteTask: context.actions.deleteSession,
    onToTask: rail.promoteChat,
    toTaskBusy: rail.toTaskBusy,
    onTogglePinned: sessions.pinnedView.onToggleChat,
    onTogglePinnedTask: sessions.pinnedView.onToggleTask,
    onOpenFile: function (entry) { rail.openResource("file", entry); },
    onOpenTerminal: function (terminalId) { rail.openResource("terminal", terminalId); },
    onRailModeChange: rail.setMode,
    collapsed: context.navigation.railCollapsed,
    onToggleCollapsed: context.navigation.toggleSidebar,
    collapseControl: context.navigation.renderCollapseControl(),
    moduleDock: !context.presentation.isModulePage ? context.navigation.renderDockSlot() : null,
  });
}

function WorkbenchTaskContent({ context }) {
  var task = context.task;
  var store = context.store;
  if (task.view === "board") return <TaskBoard
    project={store.activeProject}
    chats={store.activeProject && context.projectRail.recentChatsByProject[store.activeProject.id] || []}
    loading={task.loading}
    error={task.error}
    onOpenSession={task.openTask}
    onOpenChat={task.openChat}
    onCreateSession={context.actions.createSession}
    onCreateChat={context.actions.createChat}
    onDeleteSession={context.actions.deleteSession}
  />;
  return <>
    <TaskWorkArea
      key={store.activeSessionId || "none"}
      project={store.activeProject}
      session={store.activeSession}
      expandedStepId={task.expandedStepId}
      onToggleStep={task.toggleStep}
      onCreateRun={task.runCreated}
      onRightTab={task.setRightTab}
      onSelectSession={task.selectSession}
      onBackToBoard={task.backToBoard}
      onCreateSession={context.actions.createSession}
      onInitPatch={task.patchInit}
      onLocalPatch={task.patchLocal}
      onRefresh={task.refresh}
      error={task.error}
      loading={task.loading}
      active={!context.presentation.isModulePage}
    />
    <RightContextPanel
      project={store.activeProject}
      session={store.activeSession}
      expandedStepId={task.expandedStepId}
      tab={task.rightTab}
      onTabChange={task.setRightTab}
      onRefresh={task.refresh}
    />
  </>;
}

function WorkbenchTaskModuleSurface({ context }) {
  return <WorkbenchStableSurface active={!context.presentation.isModulePage} enterMotion={true}>
    <>
      <WorkbenchProjectRail context={context} />
      <WorkbenchTaskContent context={context} />
    </>
  </WorkbenchStableSurface>;
}

export { WorkbenchModuleSurfaces, WorkbenchShellTopbar, WorkbenchStableSurface, WorkbenchTaskModuleSurface }
