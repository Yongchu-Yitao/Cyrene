import { wbErrorText } from "../../shared/errors.jsx"
import { mergeSessionPayload } from "./store-merge.jsx"

function createWorkbenchProjectDataActions(
  model,
  sessionLoadSeqRef,
  setStore,
  setLoading,
  setError
) {
  function reloadWorkbench(nextProjectId, nextSessionId, options) {
    options = options || {};
    var showLoading = options.showLoading !== false;
    if (showLoading) setLoading(true);
    setError("");
    return model.fetchProjects()
      .then(function (next) {
        setStore(function (prev) {
          var projectId = nextProjectId || (prev && prev.activeProjectId) || next.activeProjectId;
          var sessionId = nextSessionId || (prev && prev.activeSessionId) || next.activeSessionId;
          var project = (next.projects || []).find(function (item) { return item.id === projectId; }) || next.activeProject;
          if (!project) return next;
          var session = (project.sessions || []).find(function (item) { return item.id === sessionId; }) || project.sessions[0] || null;
          return Object.assign({}, next, {
            activeProjectId: project.id,
            activeProject: project,
            activeSessionId: session ? session.id : "",
            activeSession: session,
          });
        });
        return next;
      })
      .catch(function (err) { setError(wbErrorText(err)); })
      .finally(function () { if (showLoading) setLoading(false); });
  }

  function refreshTaskBoard() {
    return model.fetchProjects().then(function (next) {
      setStore(function (prev) {
        var activeProjectId = (prev && prev.activeProjectId) || next.activeProjectId;
        var activeProject = (next.projects || []).find(function (project) {
          return project && String(project.id || "") === String(activeProjectId || "");
        }) || (prev && prev.activeProject) || next.activeProject;
        var activeSessionId = (prev && prev.activeSessionId) || next.activeSessionId;
        var activeSession = activeProject && (activeProject.sessions || []).find(function (session) {
          return session && String(session.id || "") === String(activeSessionId || "");
        });
        if (!activeSession && activeProject) activeSession = (activeProject.sessions || [])[0] || null;
        return Object.assign({}, next, {
          activeProjectId: activeProject ? activeProject.id : "",
          activeProject: activeProject || null,
          activeSessionId: activeSession ? activeSession.id : "",
          activeSession: activeSession || null,
        });
      });
      return next;
    }).catch(function () { return null; });
  }

  function fetchAndMergeSession(sessionId, options) {
    if (!sessionId) return Promise.resolve(null);
    options = options || {};
    var showLoading = options.showLoading !== false;
    var sequence = ++sessionLoadSeqRef.current;
    if (showLoading) setLoading(true);
    return model.fetchSession(sessionId)
      .then(function (payload) {
        if (sequence !== sessionLoadSeqRef.current) return null;
        setStore(function (prev) { return mergeSessionPayload(prev, payload); });
        return payload;
      })
      .catch(function (err) {
        if (sequence === sessionLoadSeqRef.current) setError(wbErrorText(err));
        return null;
      })
      .finally(function () {
        if (showLoading && sequence === sessionLoadSeqRef.current) setLoading(false);
      });
  }

  return {
    reloadWorkbench: reloadWorkbench,
    refreshTaskBoard: refreshTaskBoard,
    fetchAndMergeSession: fetchAndMergeSession,
  };
}

function patchWorkbenchActiveInit(setStore, initPatch) {
  if (!initPatch) return;
  setStore(function (prev) {
    if (!prev.activeSession) return prev;
    var activeId = prev.activeSession.id;
    function mergeSession(session) {
      if (!session || session.id !== activeId) return session;
      return { ...session, init: { ...(session.init || {}), ...initPatch } };
    }
    var nextProjects = (prev.projects || []).map(function (project) {
      if (!project || project.id !== prev.activeProjectId) return project;
      return { ...project, sessions: (project.sessions || []).map(mergeSession) };
    });
    return {
      ...prev,
      projects: nextProjects,
      activeProject: nextProjects.find(function (project) { return project.id === prev.activeProjectId; }) || prev.activeProject,
      activeSession: mergeSession(prev.activeSession),
    };
  });
}

function patchWorkbenchActiveSession(setStore, partial) {
  if (!partial) return;
  setStore(function (prev) {
    if (!prev.activeSession) return prev;
    var activeId = prev.activeSession.id;
    function mergeSession(session) {
      return !session || session.id !== activeId ? session : Object.assign({}, session, partial);
    }
    var nextProjects = (prev.projects || []).map(function (project) {
      if (!project || project.id !== prev.activeProjectId) return project;
      return Object.assign({}, project, { sessions: (project.sessions || []).map(mergeSession) });
    });
    return Object.assign({}, prev, {
      projects: nextProjects,
      activeProject: nextProjects.find(function (project) { return project.id === prev.activeProjectId; }) || prev.activeProject,
      activeSession: mergeSession(prev.activeSession),
    });
  });
}

export { createWorkbenchProjectDataActions, patchWorkbenchActiveInit, patchWorkbenchActiveSession }
