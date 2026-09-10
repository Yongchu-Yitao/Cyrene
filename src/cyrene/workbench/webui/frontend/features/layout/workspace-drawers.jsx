import { useNativeWheel } from "../../shared/native-wheel.jsx"
import { useMobileDrawers } from "./mobile-drawers.jsx"
const { useState: useWorkbenchState } = React;
export function useWorkspaceDrawers(fullPage, getWheelHandler, getToggleSidebar, wbApplyStoredRightWidth) {
  var [desktopRailCollapsed, setRailCollapsed] = useWorkbenchState(function () {
    // Default to collapsed (icon strip); honour the user's stored choice once set.
    try {
      var v = localStorage.getItem("wb-rail-collapsed");
      return v === null ? true : v === "1";
    } catch (e) { return true; }
  });
  var mobileDrawers = useMobileDrawers(fullPage);
  var railCollapsed = mobileDrawers.compact ? false : desktopRailCollapsed;
  var mobileGridRef = useNativeWheel(function (event) {
    if (!mobileDrawers.compact) getWheelHandler()(event);
  }, wbApplyStoredRightWidth);
  var toggleWorkspaceSidebar = function () {
    if (mobileDrawers.compact) mobileDrawers.setSide(mobileDrawers.side === "left" ? "" : "left");
    else getToggleSidebar()();
  };
  const mobileDrawerAttribute = mobileDrawers.compact ? mobileDrawers.side || "closed" : undefined;
  return { mobileDrawers, railCollapsed, setRailCollapsed, mobileGridRef, toggleWorkspaceSidebar, mobileDrawerAttribute };
}
