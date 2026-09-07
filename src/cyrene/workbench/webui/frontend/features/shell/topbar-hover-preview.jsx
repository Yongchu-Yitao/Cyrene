import { useWbcEffect, useWbcRef, useWbcState } from "../../workbench-chat.jsx"

// Keep delayed hover state and its cancellation in the same owner.
export function useTopbarHoverPreview(onLoadSessionResources, readTopbarPortalTheme) {
  var [hoverPreview, setHoverPreview] = useWbcState(null);
  var previewTimerRef = useWbcRef(0);
  function closeSessionPreview() {
    if (previewTimerRef.current) window.clearTimeout(previewTimerRef.current);
    previewTimerRef.current = 0;
    setHoverPreview(null);
  }

  function scheduleSessionPreview(event, item, activity, immediate) {
    if (previewTimerRef.current) window.clearTimeout(previewTimerRef.current);
    if (item && item.kind === "chat" && onLoadSessionResources) {
      Promise.resolve(onLoadSessionResources(item)).catch(function () {});
    }
    var node = event.currentTarget;
    var rect = node.getBoundingClientRect();
    previewTimerRef.current = window.setTimeout(function () {
      previewTimerRef.current = 0;
      var width = 300;
      setHoverPreview({
        item: item,
        activity: activity,
        left: Math.max(8, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 8)),
        top: Math.min(window.innerHeight - 12, rect.bottom + 8),
        portalTheme: readTopbarPortalTheme(),
      });
    }, immediate ? 80 : 420);
  }

  useWbcEffect(function () {
    return function () {
      if (previewTimerRef.current) window.clearTimeout(previewTimerRef.current);
    };
  }, []);
  return { hoverPreview, closeSessionPreview, scheduleSessionPreview };
}
