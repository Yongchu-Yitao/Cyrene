import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WorkbenchWindowControls } from "./window-controls.jsx"
import { wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"
import { WorkbenchEditProjectModal, WorkbenchProjectMemoryModal } from "./support.jsx"

function WorkbenchSearchPortal({
  open,
  onClose,
  onOpenChatPage,
  onCreateChat,
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
    {React.createElement(workbenchServices.feedback().Host)}
    {React.createElement(workbenchServices.tourHost().Host, {
      setOverlayObscured: wbSetBrowserOverlayObscured,
      onOpenPage: onOpenPage,
      onOpenSettings: onOpenSettings,
    })}
  </>;
}

function WorkbenchOnboardingShell({ onboarding, appearance, onComplete, t }) {
  return (
    <div className="workbench-shell wb-ob-shell" data-screen-label="Cyrene · onboarding">
      <div className="wb-ob-topbar"><WorkbenchWindowControls t={t} /></div>
      {React.createElement(workbenchServices.welcome().Page || function () { return <div className="workbench-empty">{t("workbench.welcomeLoading")}</div>; }, {
        onboarding: onboarding,
        appearance: appearance,
        onComplete: onComplete,
      })}
      {React.createElement(workbenchServices.feedback().Host)}
    </div>
  );
}

export { WorkbenchAppModals, WorkbenchOnboardingShell, WorkbenchSearchPortal }
