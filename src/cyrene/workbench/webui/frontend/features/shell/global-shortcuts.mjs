function dispatchWorkbenchGlobalShortcut(event, shortcuts, state, actions) {
  if (!shortcuts || state.searchOpen || state.newProjectOpen || state.newTaskOpen) return false;
  var target = event.target;
  var tag = target && target.tagName ? target.tagName.toLowerCase() : "";
  var isEditable = tag === "input" || tag === "textarea" || tag === "select" || !!(target && target.isContentEditable);
  if (isEditable && !(event.metaKey || event.ctrlKey || event.altKey)) return false;
  if (shortcuts.matches(event, "search")) { event.preventDefault(); actions.openSearch(); return true; }
  if (shortcuts.matches(event, "new-chat")) { event.preventDefault(); actions.createChat(); return true; }
  if (shortcuts.matches(event, "new-task")) {
    event.preventDefault();
    if (state.hasActiveProject) actions.createTask();
    return true;
  }
  if (shortcuts.matches(event, "command-palette")) { event.preventDefault(); actions.openSearch(); return true; }
  if (shortcuts.matches(event, "voice-command")) { event.preventDefault(); actions.startVoice(); return true; }
  if (shortcuts.matches(event, "settings")) { event.preventDefault(); actions.openShortcutSettings(); return true; }
  if (shortcuts.matches(event, "toggle-sidebar")) { event.preventDefault(); actions.toggleSidebar(); return true; }
  if (shortcuts.matches(event, "switch-project")) {
    var digit = String(event.key || "");
    if (/^[1-9]$/.test(digit)) {
      var project = state.projects[parseInt(digit, 10) - 1];
      if (project) { event.preventDefault(); actions.selectProject(project.id); return true; }
    }
  }
  return false;
}

export { dispatchWorkbenchGlobalShortcut }
