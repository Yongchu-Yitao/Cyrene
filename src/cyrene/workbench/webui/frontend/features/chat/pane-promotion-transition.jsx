import { wbcCaptureConversationViewport, wbcPinPageSplitLayout, wbcPinSplitMotionOpen, wbcReleasePinnedPageSplitLayout, wbcReleasePinnedSplitMotion, wbcRestoreConversationViewport } from "../../workbench-chat.jsx"

// DOM transition ownership is independent of conversation selection and persistence.
export function promoteSplitConversation({ sourceId, activeId, pageRef, commitPromotionNow }) {
  var reducedMotion = !!(window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var page = pageRef.current;
  var sourcePane = page && page.querySelector(".wbc-side-agent-split-motion.open .wbc-chat-split");
  var canTransitionHandoff = !!(
    sourceId !== activeId
    && !reducedMotion
    && sourcePane
    && document.startViewTransition
    && window.ReactDOM
    && typeof window.ReactDOM.flushSync === "function"
  );
  if (!canTransitionHandoff) {
    commitPromotionNow();
    return;
  }

  // The conversation changes React owners here (split pane -> main pane).
  // Give both complete panes one shared transition identity. Their widths
  // are swapped with the resource track in commitPromotion, so Chromium
  // translates this snapshot without resizing or reflowing it.
  var transitionName = "wbc-promoted-conversation";
  var displacedName = "wbc-displaced-conversation";
  var resourceName = "wbc-promoted-resource";
  var displacedPane = page && page.querySelector(":scope > .wbc-main");
  var targetPane = null;
  var targetResourcePane = null;
  var promotedViewport = wbcCaptureConversationViewport(sourcePane);
  sourcePane.style.viewTransitionName = transitionName;
  if (displacedPane) displacedPane.style.viewTransitionName = displacedName;
  document.documentElement.classList.add("wbc-split-view-transition");
  document.documentElement.classList.add("wbc-split-view-transition-opening");
  wbcPinPageSplitLayout(page);
  function clearTransitionIdentity() {
    sourcePane.style.viewTransitionName = "";
    if (displacedPane) displacedPane.style.viewTransitionName = "";
    if (targetPane) targetPane.style.viewTransitionName = "";
    if (targetResourcePane) {
      targetResourcePane.style.viewTransitionName = "";
      wbcReleasePinnedSplitMotion(targetResourcePane);
    }
    document.documentElement.classList.remove("wbc-split-view-transition");
    document.documentElement.classList.remove("wbc-split-view-transition-opening");
    wbcReleasePinnedPageSplitLayout(page);
  }
  try {
    var transition = document.startViewTransition(function () {
      // The old snapshot has already captured the split pane. Remove its
      // name before the final DOM is captured, then assign it to the main
      // pane created by the atomic promotion commit.
      sourcePane.style.viewTransitionName = "";
      if (displacedPane) displacedPane.style.viewTransitionName = "";
      commitPromotionNow();
      targetPane = pageRef.current && pageRef.current.querySelector(":scope > .wbc-main");
      wbcRestoreConversationViewport(targetPane, promotedViewport);
      if (targetPane) targetPane.style.viewTransitionName = transitionName;
      var resourceContent = pageRef.current && pageRef.current.querySelector(
        '.wbc-side-agent-split-motion[data-split-open="true"] .wbc-side-agent-split:not(.wbc-chat-split)'
      );
      targetResourcePane = resourceContent && resourceContent.closest(".wbc-side-agent-split-motion");
      if (targetResourcePane) {
        // Resource hosts normally enter on the next animation frame. Make
        // the final snapshot measurable now and suppress that independent
        // entrance, otherwise the resource is captured one track-width
        // offscreen and visibly slides again after the handoff.
        wbcPinSplitMotionOpen(targetResourcePane);
        targetResourcePane.style.viewTransitionName = resourceName;
      }
    });
    // Passive mount effects and transcript measurement can try to restore
    // the live tail after the atomic owner handoff. Reapply the visual
    // anchor once the new snapshot is ready and again before its overlay
    // is removed, so the final live pane cannot reveal another position.
    Promise.resolve(transition.ready).then(function () {
      wbcRestoreConversationViewport(targetPane, promotedViewport);
    }).catch(function () {});
    Promise.resolve(transition.finished).catch(function () {}).then(function () {
      wbcRestoreConversationViewport(targetPane, promotedViewport);
      clearTransitionIdentity();
    });
  } catch (error) {
    clearTransitionIdentity();
    commitPromotionNow();
  }
}

export function restoreSplitConversation({ snapshot, pageRef, commitRestoreNow }) {
  var reducedMotion = !!(window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var page = pageRef.current;
  var sourcePane = page && page.querySelector(":scope > .wbc-main");
  var sourceResourcePane = page && page.querySelector(".wbc-side-agent-split-motion.open");
  var canTransitionRestore = !!(
    snapshot.chatId !== snapshot.activeChatId
    && !reducedMotion
    && sourcePane
    && document.startViewTransition
    && window.ReactDOM
    && typeof window.ReactDOM.flushSync === "function"
  );
  if (!canTransitionRestore) {
    commitRestoreNow();
    return true;
  }

  // Exact inverse of promotion: the current main conversation returns to
  // its original split rectangle, the resource fades out, and the displaced
  // main conversation fades back into the left track.
  var transitionName = "wbc-promoted-conversation";
  var displacedName = "wbc-displaced-conversation";
  var resourceName = "wbc-promoted-resource";
  var targetPane = null;
  var targetMainPane = null;
  var restoredViewport = wbcCaptureConversationViewport(sourcePane);
  sourcePane.style.viewTransitionName = transitionName;
  if (sourceResourcePane) sourceResourcePane.style.viewTransitionName = resourceName;
  document.documentElement.classList.add("wbc-split-view-transition");
  document.documentElement.classList.add("wbc-split-view-transition-closing");
  wbcPinPageSplitLayout(page);
  function clearRestoreTransitionIdentity() {
    sourcePane.style.viewTransitionName = "";
    if (sourceResourcePane) sourceResourcePane.style.viewTransitionName = "";
    if (targetPane) {
      targetPane.style.viewTransitionName = "";
      wbcReleasePinnedSplitMotion(targetPane.closest(".wbc-side-agent-split-motion"));
    }
    if (targetMainPane) targetMainPane.style.viewTransitionName = "";
    document.documentElement.classList.remove("wbc-split-view-transition");
    document.documentElement.classList.remove("wbc-split-view-transition-closing");
    wbcReleasePinnedPageSplitLayout(page);
  }
  try {
    var transition = document.startViewTransition(function () {
      sourcePane.style.viewTransitionName = "";
      if (sourceResourcePane) sourceResourcePane.style.viewTransitionName = "";
      commitRestoreNow();
      targetPane = pageRef.current && pageRef.current.querySelector(
        '.wbc-side-agent-split-motion[data-split-open="true"] .wbc-chat-split'
      );
      var targetMotion = targetPane && targetPane.closest(".wbc-side-agent-split-motion");
      // Capture the restored conversation at its settled right-hand
      // rectangle. Without pinning, the host's own enter transition makes
      // the shared layer target x=offscreen, so closing is not the inverse
      // of opening and the pane snaps back after the View Transition.
      wbcPinSplitMotionOpen(targetMotion);
      wbcRestoreConversationViewport(targetPane, restoredViewport);
      if (targetPane) targetPane.style.viewTransitionName = transitionName;
      targetMainPane = pageRef.current && pageRef.current.querySelector(":scope > .wbc-main");
      if (targetMainPane) targetMainPane.style.viewTransitionName = displacedName;
    });
    Promise.resolve(transition.ready).then(function () {
      wbcRestoreConversationViewport(targetPane, restoredViewport);
    }).catch(function () {});
    Promise.resolve(transition.finished).catch(function () {}).then(function () {
      wbcRestoreConversationViewport(targetPane, restoredViewport);
      clearRestoreTransitionIdentity();
    });
  } catch (error) {
    clearRestoreTransitionIdentity();
    commitRestoreNow();
  }
  return true;
}
