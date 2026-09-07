import { wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"
var useWorkbenchEffect = React.useEffect;

export function useTopbarBrowserSubscription(browserAvailable, browserManagerMenu, browserManagerState, setBrowserManagerState, setBrowserManagerMenu) {
  useWorkbenchEffect(function () {
    if (!browserAvailable) {
      setBrowserManagerState({ ok: true, pageCount: 0, downloadCount: 0, pages: [], downloads: [] });
      setBrowserManagerMenu(null);
      return undefined;
    }
    var bridge = window.cyrene && window.cyrene.browser;
    if (!bridge || typeof bridge.getManagerState !== "function") return undefined;
    var mounted = true;
    bridge.getManagerState().then(function (next) {
      if (mounted && next && next.ok !== false) setBrowserManagerState(next);
    }).catch(function () {});
    var unsubscribe = typeof bridge.onManagerState === "function"
      ? bridge.onManagerState(function (next) {
          if (mounted && next && next.ok !== false) setBrowserManagerState(next);
        })
      : function () {};
    return function () {
      mounted = false;
      unsubscribe();
    };
  }, [browserAvailable]);

  useWorkbenchEffect(function () {
    if (!browserManagerMenu) return undefined;
    wbSetBrowserOverlayObscured(1);
    function close(event) {
      if (event && event.key && event.key !== "Escape") return;
      setBrowserManagerMenu(null);
    }
    window.addEventListener("resize", close);
    document.addEventListener("keydown", close);
    return function () {
      window.removeEventListener("resize", close);
      document.removeEventListener("keydown", close);
      wbSetBrowserOverlayObscured(-1);
    };
  }, [!!browserManagerMenu]);

  useWorkbenchEffect(function () {
    if (browserManagerMenu && !browserManagerState.pageCount && !browserManagerState.downloadCount) {
      setBrowserManagerMenu(null);
    }
  }, [browserManagerState.pageCount, browserManagerState.downloadCount, !!browserManagerMenu]);

}
