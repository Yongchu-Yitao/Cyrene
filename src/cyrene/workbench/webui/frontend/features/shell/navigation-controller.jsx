import { workbenchServices } from "../../shared/runtime/services.jsx"

var { useEffect } = React;

function createWorkbenchNavigationActions(
  fullPage,
  setFullPage,
  setSettingsTab,
  setSettingsScrollTo,
  setRailCollapsed,
  sidebarModuleWheelRef,
  activeDestination,
  enabledModules
) {
  var moduleOrder = Array.isArray(enabledModules) && enabledModules.length
    ? enabledModules
    : ["schedule", "board", "work", "knowledge", "memory"];

  function openPage(page) {
    var destination = page;
    if (["schedule", "board", "work", "knowledge", "memory"].indexOf(destination) >= 0
        && moduleOrder.indexOf(destination) < 0) return;
    if (page === "board") {
      if (!fullPage) return;
      setFullPage(null);
      return;
    }
    if (page === "work") {
      if (fullPage === "chat") return;
      setFullPage("chat");
      return;
    }
    if (page === "profile") {
      setSettingsTab("profile");
      setSettingsScrollTo(null);
      setFullPage("settings");
      return;
    }
    if (fullPage === page) return;
    setFullPage(page);
  }

  function toggleSidebar() {
    setRailCollapsed(function (value) {
      var next = !value;
      try { localStorage.setItem("wb-rail-collapsed", next ? "1" : "0"); } catch (e) {}
      return next;
    });
  }

  function onModuleWheel(event) {
    var target = event.target;
    if (!target || !target.closest || !target.closest(".workbench-integrated-rail, .workbench-sidebar-dock.is-persistent")) return;
    var deltaX = Number(event.deltaX || 0);
    var deltaY = Number(event.deltaY || 0);
    if (Math.abs(deltaX) < 2 || Math.abs(deltaX) <= Math.abs(deltaY) * 1.15) return;
    event.preventDefault();
    var gesture = sidebarModuleWheelRef.current;
    var now = Date.now();
    var direction = deltaX < 0 ? -1 : 1;
    if (gesture.direction && gesture.direction !== direction) gesture.delta = 0;
    gesture.direction = direction;
    if (now < gesture.lockedUntil) return;
    gesture.delta += deltaX;
    if (Math.abs(gesture.delta) < 44) return;
    var activeIndex = moduleOrder.indexOf(activeDestination());
    if (activeIndex < 0 || !moduleOrder.length) return;
    var nextIndex = (activeIndex + direction + moduleOrder.length) % moduleOrder.length;
    openPage(moduleOrder[nextIndex]);
    gesture.lockedUntil = now + 420;
    gesture.delta = 0;
  }

  return { openPage: openPage, toggleSidebar: toggleSidebar, onModuleWheel: onModuleWheel };
}

function useWorkbenchBoardNavigation(setFullPage) {
  useEffect(function () {
    function openBoard() { setFullPage(null); }
    window.addEventListener("cyrene:open-workbench-board", openBoard);
    return function () { window.removeEventListener("cyrene:open-workbench-board", openBoard); };
  }, []);
}

function useWorkbenchNavigationSurface(
  fullPage,
  settingsTab,
  railCollapsed,
  t,
  openPage,
  toggleSidebar,
  setSearchOpen,
  setSettingsTab,
  setSettingsScrollTo,
  setFullPage,
  enabledModules
) {
  useEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = workbenchServices.uiSurface();
    var settingsActive = fullPage === "settings";
    uiSurface.setScope(settingsActive ? "settings" : "main");
    var unregister = [];
    var enabled = Array.isArray(enabledModules)
      ? enabledModules
      : ["schedule", "board", "work", "knowledge", "memory"];
    var modules = [
      ["schedule", t("rail.schedule", "Schedule")],
      ["board", t("workbench.page.board", "Board")],
      ["work", t("workbench.page.work", "Work")],
      ["knowledge", t("rail.knowledge", "Knowledge")],
      ["memory", t("rail.memory", "Memory")],
    ].filter(function (item) { return enabled.indexOf(item[0]) >= 0; });
    modules.forEach(function (item) {
      var page = item[0];
      unregister.push(uiSurface.register({
        node_id: "navigation_" + page,
        parent_id: "root",
        scope: "main",
        get_node: function () {
          return {
            role: "navigation_item",
            name: item[1],
            state: {
              selected: page === "board" ? !fullPage
                : page === "work" ? fullPage === "chat" : fullPage === page,
            },
          };
        },
        actions: [{
          action_id: "open", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"],
          outcome: { effect: "opens_surface", target_scope: page, inspect_after: true },
        }],
        handlers: { open: function () { openPage(page); } },
      }));
    });
    unregister.push(uiSurface.register({
      node_id: "workspace_sidebar",
      parent_id: "root",
      scope: "main",
      get_node: function () { return { role: "complementary", name: t("rail.sidebar", "Workspace sidebar"), state: { collapsed: railCollapsed } }; },
      actions: [{ action_id: "toggle", kind: "toggle", risk: "R1", gesture_aliases: ["press"] }],
      handlers: { toggle: toggleSidebar },
    }));
    unregister.push(uiSurface.register({
      node_id: "open_search", parent_id: "root", scope: "main", order: 20,
      get_node: function () { return { role: "button", name: t("topbar.search", "Search") }; },
      actions: [{
        action_id: "open", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"],
        outcome: { effect: "opens_current_overlay", target_role: "dialog", inspect_after: true },
      }],
      handlers: { open: function () { setSearchOpen(true); } },
    }));
    unregister.push(uiSurface.register({
      node_id: "open_settings", parent_id: "root", scope: "main", order: 25,
      get_node: function () { return { role: "button", name: t("settings.title", "Settings") }; },
      actions: [{
        action_id: "open", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"],
        outcome: { effect: "opens_surface", target_node_id: "settings_page", target_scope: "settings", inspect_after: true },
      }],
      handlers: { open: function () { setSettingsTab(""); setSettingsScrollTo(null); setFullPage("settings"); } },
    }));
    if (settingsActive) {
      unregister.push(uiSurface.register({
        node_id: "settings_page",
        parent_id: "root",
        scope: "settings",
        get_node: function () { return { role: "region", name: t("settings.title", "Settings"), state: { tab: settingsTab || "general" } }; },
      }));
    }
    return function () { unregister.forEach(function (remove) { remove(); }); };
  }, [fullPage, settingsTab, railCollapsed, t, (enabledModules || []).join("|")]);
}

export { createWorkbenchNavigationActions, useWorkbenchBoardNavigation, useWorkbenchNavigationSurface }
