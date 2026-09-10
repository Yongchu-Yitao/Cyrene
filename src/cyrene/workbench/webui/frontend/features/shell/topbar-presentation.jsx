export function readTopbarPortalTheme() {
    var portalTheme = {};
    var themeSource = document.querySelector(".workbench-shell");
    if (themeSource && typeof getComputedStyle === "function") {
      var computedTheme = getComputedStyle(themeSource);
      [
        "--wb-surface", "--wb-card-bg", "--wb-card-bg-strong", "--wb-line", "--wb-line-2",
        "--wb-text", "--wb-muted", "--wb-faint",
        "--wb-control-bg", "--wb-control-hover-bg", "--wb-row-hover-bg",
        "--wb-flyout-bg", "--wb-flyout-border", "--wb-flyout-shadow",
        "--wb-green", "--wb-amber", "--wb-red", "--wb-accent", "--wb-ui-font-scale",
      ].forEach(function (name) { portalTheme[name] = computedTheme.getPropertyValue(name); });
      portalTheme.fontFamily = computedTheme.fontFamily;
    }
    return portalTheme;
  }

export function topbarCandidates(recentSessions, overflowSessions, sessionCandidates, activeTabKey, activePage, activeChatId) {
  var fallbackTabs = (Array.isArray(recentSessions) ? recentSessions : []).concat(
    Array.isArray(overflowSessions) ? overflowSessions : []
  );
  var candidates = Array.isArray(sessionCandidates) && sessionCandidates.length
    ? sessionCandidates : fallbackTabs;
  var selectedTabKey = String(activeTabKey || (activePage === "chat" && activeChatId ? "chat:" + activeChatId : ""));
  return { candidates, selectedTabKey };
}
