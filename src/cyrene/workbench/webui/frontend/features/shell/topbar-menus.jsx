import { useWbcEffect as useWorkbenchEffect, useWbcLayoutEffect as useWorkbenchLayoutEffect, useWbcRef as useWorkbenchRef, useWbcState as useWorkbenchState } from "../../workbench-chat.jsx"
import { sessionMenuResources } from "./topbar-resource-projection.mjs"
import { wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"

// Menu state, stale-response guards, positioning and cleanup have one owner.
export function useTopbarMenus({ browserAvailable, onLoadSessionResources, onLoadSessionBrowserPreview, hoverPreview, closeSessionPreview, readTopbarPortalTheme }) {
  var [sessionMenu, setSessionMenu] = useWorkbenchState(null);
  var [resourceMenu, setResourceMenu] = useWorkbenchState(null);
  var [overflowMenu, setOverflowMenu] = useWorkbenchState(null);
  var resourceMenuRef = useWorkbenchRef(null);
  var sessionMenuSeqRef = useWorkbenchRef(0);
  var overflowCloseTimerRef = useWorkbenchRef(0);

  useWorkbenchLayoutEffect(function () {
    if (!resourceMenu || !resourceMenuRef.current) return undefined;
    var menu = resourceMenuRef.current;
    function placeResourceMenu() {
      var bounds = menu.getBoundingClientRect();
      var left = Math.max(8, Math.min(resourceMenu.anchorX, window.innerWidth - bounds.width - 8));
      var top = Math.max(8, Math.min(resourceMenu.anchorY, window.innerHeight - bounds.height - 8));
      setResourceMenu(function (current) {
        if (!current || (current.left === left && current.top === top)) return current;
        return Object.assign({}, current, { left: left, top: top });
      });
    }
    placeResourceMenu();
    window.addEventListener("resize", placeResourceMenu);
    return function () { window.removeEventListener("resize", placeResourceMenu); };
  }, [resourceMenu && resourceMenu.anchorX, resourceMenu && resourceMenu.anchorY, resourceMenu && resourceMenu.resource && resourceMenu.resource.kind]);

  function openOverflowMenu(event) {
    event.preventDefault();
    event.stopPropagation();
    closeSessionPreview();
    var rect = event.currentTarget.getBoundingClientRect();
    var width = Math.min(480, window.innerWidth - 16);
    var height = Math.min(500, Math.max(180, window.innerHeight - rect.bottom - 16));
    if (overflowCloseTimerRef.current) clearTimeout(overflowCloseTimerRef.current);
    overflowCloseTimerRef.current = 0;
    setSessionMenu(null);
    setResourceMenu(null);
    setOverflowMenu({
      left: Math.max(8, Math.min(
        rect.right - width,
        window.innerWidth - width - 8
      )),
      top: rect.bottom + 8,
      height: height,
      portalTheme: readTopbarPortalTheme(),
      closing: false,
    });
  }

  function closeOverflowMenu() {
    if (overflowCloseTimerRef.current) clearTimeout(overflowCloseTimerRef.current);
    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) {
      overflowCloseTimerRef.current = 0;
      setOverflowMenu(null);
      return;
    }
    setOverflowMenu(function (current) {
      return current ? Object.assign({}, current, { closing: true }) : current;
    });
    overflowCloseTimerRef.current = setTimeout(function () {
      overflowCloseTimerRef.current = 0;
      setOverflowMenu(null);
    }, 140);
  }

  useWorkbenchEffect(function () {
    if (!sessionMenu && !resourceMenu && !overflowMenu) return undefined;
    function closeMenu() {
      sessionMenuSeqRef.current += 1;
      setSessionMenu(null);
      setResourceMenu(null);
      closeOverflowMenu();
    }
    function handleKey(event) {
      if (event.key === "Escape") closeMenu();
      if (["ArrowDown", "ArrowUp", "Home", "End"].indexOf(event.key) < 0) return;
      var menu = document.querySelector(".workbench-session-context-menu[role='menu']");
      if (!menu) return;
      var items = Array.prototype.slice.call(menu.querySelectorAll("[role='menuitem']:not(:disabled)"));
      if (!items.length) return;
      var current = items.indexOf(document.activeElement);
      var nextIndex = event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : event.key === "ArrowUp"
            ? (current <= 0 ? items.length - 1 : current - 1)
            : (current < 0 || current >= items.length - 1 ? 0 : current + 1);
      event.preventDefault();
      items[nextIndex].focus();
    }
    function handleScroll(event) {
      var target = event && event.target;
      if (target && target.nodeType === 1 && target.closest && target.closest(
        ".workbench-session-overflow-menu, .workbench-session-menu"
      )) return;
      closeMenu();
    }
    window.addEventListener("resize", closeMenu);
    window.addEventListener("scroll", handleScroll, true);
    document.addEventListener("keydown", handleKey);
    return function () {
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("scroll", handleScroll, true);
      document.removeEventListener("keydown", handleKey);
    };
  }, [!!sessionMenu, !!resourceMenu, !!overflowMenu]);

  useWorkbenchEffect(function () {
    if (!sessionMenu && !resourceMenu) {
      if (!overflowMenu && !hoverPreview) return undefined;
    }
    wbSetBrowserOverlayObscured(1);
    return function () { wbSetBrowserOverlayObscured(-1); };
  }, [!!sessionMenu, !!resourceMenu, !!overflowMenu, !!hoverPreview]);

  useWorkbenchEffect(function () {
    if (!browserAvailable || !sessionMenu || sessionMenu.item.kind !== "chat" || !onLoadSessionBrowserPreview) return undefined;
    var item = sessionMenu.item;
    var cancelled = false;
    var inFlight = false;
    function refreshBrowserPreview() {
      if (cancelled || inFlight) return;
      inFlight = true;
      Promise.resolve(onLoadSessionBrowserPreview(item)).then(function (browser) {
        if (cancelled) return;
        setSessionMenu(function (current) {
          if (!current || current.item.id !== item.id || current.item.kind !== item.kind) return current;
          var nextBrowser = browser || null;
          var previous = current.resources && current.resources.browser;
          if (previous && nextBrowser
              && previous.previewUrl === nextBrowser.previewUrl
              && previous.title === nextBrowser.title
              && previous.url === nextBrowser.url) return current;
          if (!previous && !nextBrowser) return current;
          return Object.assign({}, current, {
            resources: Object.assign({}, current.resources, { browser: nextBrowser }),
          });
        });
      }).catch(function () {}).finally(function () {
        inFlight = false;
      });
    }
    var timer = setInterval(refreshBrowserPreview, 1200);
    return function () {
      cancelled = true;
      clearInterval(timer);
    };
  }, [browserAvailable, sessionMenu ? sessionMenu.item.kind + ":" + sessionMenu.item.id : "", !!onLoadSessionBrowserPreview]);

  useWorkbenchEffect(function () {
    return function () {
      if (overflowCloseTimerRef.current) clearTimeout(overflowCloseTimerRef.current);
    };
  }, []);

  function openSessionMenu(event, item, activity, anchored) {
    event.preventDefault();
    event.stopPropagation();
    closeSessionPreview();
    setOverflowMenu(null);
    var menuWidth = Math.min(340, Math.max(0, window.innerWidth - 16));
    var menuHeight = 440;
    var rect = event.currentTarget && event.currentTarget.getBoundingClientRect ? event.currentTarget.getBoundingClientRect() : null;
    var left = anchored && rect ? rect.left + (rect.width - menuWidth) / 2 : event.clientX;
    var top = anchored && rect ? rect.bottom + 8 : event.clientY;
    left = Math.max(8, Math.min(left, window.innerWidth - menuWidth - 8));
    top = Math.max(8, Math.min(top, window.innerHeight - menuHeight - 8));
    var portalTheme = readTopbarPortalTheme();
    var seq = sessionMenuSeqRef.current + 1;
    sessionMenuSeqRef.current = seq;
    var loadResources = item.kind === "chat" && onLoadSessionResources;
    var cachedResources = loadResources && typeof loadResources.peek === "function"
      ? loadResources.peek(item) : null;
    setSessionMenu({
      item: item,
      activity: activity || item.activity || {},
      left: left,
      top: top,
      portalTheme: portalTheme,
      resources: sessionMenuResources(browserAvailable, cachedResources),
    });
    if (!loadResources) return;
    Promise.resolve(loadResources(item))
      .then(function (resources) {
        if (sessionMenuSeqRef.current !== seq) return;
        setSessionMenu(function (current) {
          if (!current || current.item.id !== item.id || current.item.kind !== item.kind) return current;
          return Object.assign({}, current, {
            resources: sessionMenuResources(browserAvailable, resources),
          });
        });
      })
      .catch(function () {
        return null;
      });
  }

  function closeSessionMenu() {
    sessionMenuSeqRef.current += 1;
    setSessionMenu(null);
  }

  function runSessionMenuAction(action) {
    closeSessionMenu();
    if (action) action();
  }

  function portalThemeAt(event) {
    var themeStyle = readTopbarPortalTheme();
    return {
      anchorX: event.clientX,
      anchorY: event.clientY,
      left: Math.max(8, event.clientX),
      top: Math.max(8, event.clientY),
      portalTheme: themeStyle,
    };
  }

  function openResourceMenu(event, resource) {
    event.preventDefault();
    event.stopPropagation();
    closeSessionPreview();
    setSessionMenu(null);
    setOverflowMenu(null);
    setResourceMenu(Object.assign({ resource: resource }, portalThemeAt(event)));
  }

  function closeResourceMenu() {
    setResourceMenu(null);
  }

  return { sessionMenu, resourceMenu, overflowMenu, resourceMenuRef, openSessionMenu, closeSessionMenu, runSessionMenuAction, openResourceMenu, closeResourceMenu, openOverflowMenu, closeOverflowMenu };
}
