import { workbenchServices } from "../../shared/runtime/services.jsx"
import { wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"
import { WorkbenchEditProjectModal, WorkbenchProjectMemoryModal } from "./support.jsx"

function WorkbenchSearchPortal({
  open,
  onClose,
  onOpenChatPage,
  onCreateChat,
  onCreateTask,
  onCreateProject,
  onToggleTheme,
  onToggleSidebar,
  onOpenSettings,
}) {
  if (!open || typeof ReactDOM === "undefined") return null;
  return ReactDOM.createPortal(React.createElement(
    workbenchServices.search().Overlay,
    {
      onClose: onClose,
      onOpenSession: function () {
        onClose();
        onOpenChatPage();
      },
      onCommand: function (id) {
        onClose();
        if (id === "new-chat") { onCreateChat(); return; }
        if (id === "new-task") { onCreateTask(); return; }
        if (id === "new-project") { onCreateProject(); return; }
        if (id === "toggle-theme") { onToggleTheme(); return; }
        if (id === "toggle-sidebar") { onToggleSidebar(); return; }
        var tab = id === "open-shortcuts" ? "shortcuts"
          : id === "open-plugin-registry" ? "plugin-registry"
          : id === "open-budget" ? "budget"
          : id === "open-about" ? "about" : "";
        onOpenSettings(tab, null);
      },
      onOpenSettings: function (tab, anchorId) {
        onClose();
        onOpenSettings(tab || "", anchorId || null);
      },
    }
  ), document.body);
}

function WorkbenchAppModals({
  newProjectOpen,
  onCloseNewProject,
  onCreateProject,
  editProject,
  onCloseEditProject,
  onUpdateProject,
  editMemoryProject,
  memoryAvailable,
  onCloseEditMemory,
  newTaskOpen,
  onCloseNewTask,
  onCreateTask,
  onOpenPage,
  onOpenSettings,
}) {
  return <>
    {newProjectOpen && workbenchServices.create().NewProjectModal && React.createElement(
      workbenchServices.create().NewProjectModal,
      {
        defaultWorkspacePath: "",
        onClose: onCloseNewProject,
        onCreate: function (input) {
          return onCreateProject(input).then(onCloseNewProject);
        },
      }
    )}
    {editProject && (
      <WorkbenchEditProjectModal
        project={editProject}
        onClose={onCloseEditProject}
        onSave={function (input) {
          return onUpdateProject(editProject.id, input).then(onCloseEditProject);
        }}
      />
    )}
    {memoryAvailable && editMemoryProject && (
      <WorkbenchProjectMemoryModal
        project={editMemoryProject}
        onClose={onCloseEditMemory}
      />
    )}
    {newTaskOpen && workbenchServices.create().NewTaskModal && React.createElement(
      workbenchServices.create().NewTaskModal,
      {
        onClose: onCloseNewTask,
        onCreate: function (input) {
          return onCreateTask(input).then(onCloseNewTask);
        },
      }
    )}
    {React.createElement(workbenchServices.feedback().Host)}
    {React.createElement(workbenchServices.tourHost().Host, {
      setOverlayObscured: wbSetBrowserOverlayObscured,
      onOpenPage: onOpenPage,
      onOpenSettings: onOpenSettings,
    })}
  </>;
}

function WorkbenchOnboardingShell({ onboarding, theme, actualTheme, onToggleTheme, onComplete, t }) {
  return (
    <div className="workbench-shell wb-ob-shell" data-screen-label="Cyrene · onboarding">
      <div className="wb-ob-topbar">
        <div className="workbench-brand">
          <div className="workbench-traffic-space"></div>
          <span className="brand-mark" aria-hidden="true"></span>
          <strong>Cyrene</strong>
        </div>
        <button type="button" className="workbench-icon-btn" onClick={onToggleTheme} title={t("workbench.theme." + (theme === "system" ? "system" : actualTheme === "dark" ? "dark" : "light"))}>
          {theme === "system" ? (
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/></svg>
          ) : actualTheme === "dark" ? (
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/></svg>
          ) : (
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
          )}
        </button>
      </div>
      {React.createElement(workbenchServices.welcome().Page || function () { return <div className="workbench-empty">{t("workbench.welcomeLoading")}</div>; }, {
        onboarding: onboarding,
        onComplete: onComplete,
      })}
      {React.createElement(workbenchServices.feedback().Host)}
    </div>
  );
}

export { WorkbenchAppModals, WorkbenchOnboardingShell, WorkbenchSearchPortal }
