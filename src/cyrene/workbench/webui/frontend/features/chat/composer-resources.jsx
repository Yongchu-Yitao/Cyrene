import { workbenchServices } from "../../shared/runtime/services.jsx"
import { useWbcEffect, useWbcState, wbcT } from "../../workbench-chat.jsx"

export function useWbcComposerContextResource(projectId, projectWorkspacePath, composerContextAvailable) {
  var [contextState, setContextState] = useWbcState(null);
  var [contextCatalogLoading, setContextCatalogLoading] = useWbcState(false);
  var [contextCatalogLoaded, setContextCatalogLoaded] = useWbcState(false);
  var [contextStateRevision, setContextStateRevision] = useWbcState(0);
  useWbcEffect(function () {
    if (!composerContextAvailable) {
      setContextState(null);
      setContextCatalogLoaded(false);
      setContextCatalogLoading(false);
      return undefined;
    }
    var cancelled = false;
    var controller = new AbortController();
    setContextCatalogLoading(true);
    setContextCatalogLoaded(false);
    workbenchServices.api().json("/api/context/state", { toast: false, signal: controller.signal }).then(function (s) {
      if (cancelled) return;
      var valid = s && typeof s === "object"
        && s.catalog && typeof s.catalog === "object"
        && s.options && typeof s.options === "object";
      setContextState(valid ? s : null);
      setContextCatalogLoaded(true);
    }).catch(function (err) {
      if (cancelled || (err && err.name === "AbortError")) return;
      setContextState(null);
      setContextCatalogLoaded(true);
      workbenchServices.api().toastError(err, wbcT("workbenchChat.contextCapabilitiesLoadFailed", "Failed to load context capabilities: "));
    }).finally(function () {
      if (!cancelled) setContextCatalogLoading(false);
    });
    return function () { cancelled = true; controller.abort(); };
  }, [projectId, projectWorkspacePath, composerContextAvailable, contextStateRevision]);

  function invalidate() {
    setContextState(null);
    setContextCatalogLoaded(false);
    setContextStateRevision(function (current) { return current + 1; });
  }
  function selectWorkspace(selectedPath) {
    setContextState(function (prev) {
      if (!prev) return prev;
      var history = Array.isArray(prev.workspace_history) ? prev.workspace_history : [];
      if (selectedPath) {
        history = [selectedPath].concat(history.filter(function (item) { return item !== selectedPath; })).slice(0, 10);
      }
      return { ...prev, workspace_active: true, workspace_dir: selectedPath || prev.workspace_dir, workspace_history: history };
    });
  }
  return { contextState, contextCatalogLoading, contextCatalogLoaded, invalidate, selectWorkspace };
}

export function useWbcComposerCommandCatalog({ builtinContextCapabilities, draft, toolsOpen, projectId, command, setCommand }) {
  var [slashCommandCatalog, setSlashCommandCatalog] = useWbcState([]);
  var [slashCommandCatalogLoaded, setSlashCommandCatalogLoaded] = useWbcState(false);
  var [slashCommandCatalogLoading, setSlashCommandCatalogLoading] = useWbcState(false);
  useWbcEffect(function () {
    if (!builtinContextCapabilities || (draft.indexOf("/") !== 0 && !toolsOpen) || slashCommandCatalogLoaded) {
      setSlashCommandCatalogLoading(false);
      return undefined;
    }
    var cancelled = false;
    var controller = new AbortController();
    setSlashCommandCatalogLoading(true);
    workbenchServices.api().json(
      "/api/workbench/slash-commands?project_id=" + encodeURIComponent(projectId || ""),
      { toast: false, signal: controller.signal }
    ).then(function (payload) {
      if (cancelled) return;
      var commands = Array.isArray(payload && payload.commands) ? payload.commands : [];
      setSlashCommandCatalog(commands);
      if (command && !commands.some(function (item) { return item && item.id === command; })) {
        setCommand("");
      }
      setSlashCommandCatalogLoaded(true);
    }).catch(function (err) {
      if (!cancelled && (!err || err.name !== "AbortError")) workbenchServices.api().toastError(err, wbcT("workbenchChat.slashCommandsLoadFailed", "Failed to load commands: "));
    }).finally(function () {
      if (!cancelled) setSlashCommandCatalogLoading(false);
    });
    return function () { cancelled = true; controller.abort(); };
  }, [builtinContextCapabilities, draft.indexOf("/") === 0, toolsOpen, slashCommandCatalogLoaded, projectId]);

  useWbcEffect(function () {
    setSlashCommandCatalog([]);
    setSlashCommandCatalogLoaded(false);
  }, [projectId]);

  useWbcEffect(function () {
    if (draft.indexOf("/") !== 0 && !toolsOpen && !command && slashCommandCatalogLoaded) {
      setSlashCommandCatalogLoaded(false);
    }
  }, [draft.indexOf("/") === 0, toolsOpen, command]);

  function invalidate() {
    setSlashCommandCatalog([]);
    setSlashCommandCatalogLoaded(false);
  }
  return { slashCommandCatalog, slashCommandCatalogLoading, invalidate };
}
