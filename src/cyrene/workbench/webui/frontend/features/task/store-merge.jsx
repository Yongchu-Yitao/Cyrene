// Pure task/session store reconciliation shared by the Workbench shell.
function projectForSession(snapshot, sessionId) {
  if (!snapshot || !sessionId) return null;
  var projects = Array.isArray(snapshot.projects) ? snapshot.projects : [];
  for (var i = 0; i < projects.length; i++) {
    var sessions = Array.isArray(projects[i].sessions) ? projects[i].sessions : [];
    if (sessions.some(function (item) { return item && String(item.id || "") === String(sessionId); })) {
      return projects[i];
    }
  }
  return null;
}

// Merge a task response without allowing a late response from an old task
// to change the project the user is currently viewing. The response still
// updates the project/session lists, so background work is not lost.
function mergeTaskResponse(prev, nextStore, sourceSessionId) {
  if (!nextStore || typeof nextStore !== "object") return prev;
  var merged = Object.assign({}, prev);
  if (Array.isArray(nextStore.projects)) merged.projects = nextStore.projects;

  var sourceId = String(sourceSessionId || "");
  if (sourceId && String(prev.activeSessionId || "") !== sourceId) return merged;

  var responseSession = null;
  var responseProject = projectForSession(nextStore, sourceId);
  if (responseProject) {
    responseSession = (responseProject.sessions || []).find(function (item) {
      return item && String(item.id || "") === sourceId;
    }) || null;
  }
  if (!responseSession && nextStore.activeSession && (!sourceId || String(nextStore.activeSession.id || "") === sourceId)) {
    responseSession = nextStore.activeSession;
  }
  if (!responseProject && nextStore.activeProject && (!sourceId || !responseSession || String(nextStore.activeProject.id || "") === String(responseSession.projectId || ""))) {
    responseProject = nextStore.activeProject;
  }
  if (responseProject && String(responseProject.id || "") === String(prev.activeProjectId || "")) {
    merged.activeProject = responseProject;
    merged.activeProjectId = responseProject.id;
  }
  if (responseSession && String(responseSession.id || "") === String(prev.activeSessionId || "")) {
    merged.activeSession = responseSession;
    merged.activeSessionId = responseSession.id;
  }
  return merged;
}


function mergeSessionPayload(prev, payload) {
  if (!prev || !payload || !payload.session) return prev;
  var fullSession = Object.assign({}, payload.session, { isSummary: false });
  // A silent refresh can arrive while SSE activity is still streaming. Keep
  // live runtime entries so the run-log panel does not blink back to an old
  // snapshot between two subagent updates.
  var priorSession = prev.activeSession && String(prev.activeSession.id || "") === String(fullSession.id || "")
    ? prev.activeSession : null;
  if (priorSession && Array.isArray(priorSession.events)) {
    var persistedEvents = Array.isArray(fullSession.events) ? fullSession.events.slice() : [];
    var seenEventIds = {};
    persistedEvents.forEach(function (event) { if (event && event.id) seenEventIds[event.id] = true; });
    priorSession.events.forEach(function (event) {
      if (event && event.live && event.id && !seenEventIds[event.id]) {
        persistedEvents.push(event);
        seenEventIds[event.id] = true;
      }
    });
    persistedEvents.sort(function (a, b) { return String(a.createdAt || "").localeCompare(String(b.createdAt || "")); });
    fullSession.events = persistedEvents.slice(-240);
  }
  var projectPayload = payload.project && typeof payload.project === "object" ? payload.project : null;
  var projectId = String(
    (projectPayload && projectPayload.id)
    || fullSession.projectId
    || payload.projectId
    || prev.activeProjectId
    || ""
  );
  var foundProject = false;
  var nextProjects = (prev.projects || []).map(function (project) {
    if (!project || String(project.id || "") !== projectId) return project;
    foundProject = true;
    var projectPatch = {};
    if (projectPayload) {
      Object.keys(projectPayload).forEach(function (key) {
        if (key !== "sessions") projectPatch[key] = projectPayload[key];
      });
    }
    var foundSession = false;
    var sessions = (project.sessions || []).map(function (session) {
      if (session && String(session.id || "") === String(fullSession.id || "")) {
        foundSession = true;
        return fullSession;
      }
      return session;
    });
    if (!foundSession) sessions = sessions.concat([fullSession]);
    return Object.assign({}, project, projectPatch, { sessions: sessions });
  });
  if (!foundProject && projectPayload) {
    nextProjects = nextProjects.concat([Object.assign({}, projectPayload, { sessions: [fullSession] })]);
  }
  var updatedProject = nextProjects.find(function (project) { return String(project.id || "") === projectId; }) || null;
  var shouldActivate = String(prev.activeSessionId || "") === String(fullSession.id || "");
  // The fetched session may belong to an old project after the user has
  // switched projects. Keep that data in the list, but preserve the visible
  // project/session unless this response is for the current session.
  var activeProject = nextProjects.find(function (project) {
    return String(project.id || "") === String(prev.activeProjectId || "");
  }) || (shouldActivate ? updatedProject : prev.activeProject);
  var activeSession = shouldActivate
    ? fullSession
    : (
      activeProject && (activeProject.sessions || []).find(function (session) {
        return session && String(session.id || "") === String(prev.activeSessionId || "");
      })
    ) || prev.activeSession;
  return Object.assign({}, prev, {
    projects: nextProjects,
    activeProjectId: activeProject ? activeProject.id : prev.activeProjectId,
    activeProject: activeProject,
    activeSessionId: shouldActivate ? (fullSession.id || prev.activeSessionId) : prev.activeSessionId,
    activeSession: activeSession,
  });
}


export { mergeTaskResponse, mergeSessionPayload }
