import { WBC_ICONS, WBC_SIDE_TAB_ICONS, useWbcCallback, useWbcEffect, useWbcLayoutEffect, useWbcRef, useWbcState, wbcAttachmentTypeLabel, wbcBrowserAvoidancePlan, wbcBrowserFullscreenStatusText, wbcBrowserPageTitle, wbcBrowserTabPickerPayload, wbcBrowserTabPickerToggleIsDebounced, wbcBrowserWindowTitle, wbcCapabilityEnabled, wbcCapabilityStatus, wbcChatAgent, wbcClampBrowserWindowFrame, wbcConversationTabAtPoint, wbcCycleTopbarSessionTab, wbcHandleHorizontalWheelGesture, wbcHasAgentCapabilitySnapshot, wbcHasChatDrag, wbcIsBuiltinAgent, wbcKeepBrowserWindowClearOfComposer, wbcLoadBrowserWindowFrame, wbcMergeChronologicalMessages, wbcNotifyBrowserLayoutChanged, wbcNotifyBrowserWindowInteraction, wbcNotifyResourceShelfPointerDrag, wbcPointInsideResourceShelf, wbcReadChatDrag, wbcReconcileLiveUserMessages, wbcRuntimeSegmentMessages, wbcRuntimeTimelineMessages, wbcSaveBrowserWindowFrame, wbcT, wbcTraceDedupeKey } from "../../workbench-chat.jsx"
import { WbcActivityGroup, WbcAgentNotification, WbcAssistantMessage, WbcErrorNotice, WbcLiveActivityCard, WbcLiveMessage, WbcModelStatusMessage, WbcQuestionPrompt, WbcUserMessage, wbcGroupConsecutiveActivityMessages } from "./messages.jsx"
import { WbcComposer } from "./composer.jsx"

import { permissionOptionLabel } from "./behavior.mjs"

// Workbench chat conversation and interactive prompts.
function WbcBrowserFloatingSurface({ browserState, browserSessionId, visible, mode, composerDocked, runtime, running, latestAssistantReplyId, latestAssistantReplyText, onSend, onGuidance, onInterrupt, onMaximize, onRestore, onTakeoverComplete }) {
  var shellRef = useWbcRef(null);
  var maximizedPickerRef = useWbcRef(null);
  var minimizedRef = useWbcRef(null);
  var frameRef = useWbcRef(null);
  var frameSessionRef = useWbcRef("");
  var composerDockedRef = useWbcRef(composerDocked === true);
  var preComposerDockFrameRef = useWbcRef(null);
  var composerDockRestoreTimerRef = useWbcRef(null);
  var previousVisibleRef = useWbcRef(visible);
  var pipRestoreGuardUntilRef = useWbcRef(0);
  var pipRestoreTimerRef = useWbcRef(null);
  var minimizedFrameRef = useWbcRef(null);
  var interactionRef = useWbcRef(null);
  var minimizedDragRef = useWbcRef(null);
  var suppressMinimizedClickRef = useWbcRef(false);
  var modeTransitionRafRef = useWbcRef(0);
  var modeTransitionTimerRef = useWbcRef(null);
  var modeTransitionReadyHandlerRef = useWbcRef(null);
  var maximizedPickerToggleAtRef = useWbcRef(0);
  var [frame, setFrame] = useWbcState(null);
  var [minimizedFrame, setMinimizedFrame] = useWbcState(null);
  var [nativeBrowserState, setNativeBrowserState] = useWbcState(null);
  var [fullscreenDraft, setFullscreenDraft] = useWbcState("");
  var [fullscreenStatusRequested, setFullscreenStatusRequested] = useWbcState(false);
  var [fullscreenSubmitting, setFullscreenSubmitting] = useWbcState(false);
  var [fullscreenFinalReply, setFullscreenFinalReply] = useWbcState("");
  var [maximizedPickerOpen, setMaximizedPickerOpen] = useWbcState(false);
  var [chatOverlayThemeRevision, setChatOverlayThemeRevision] = useWbcState(0);
  var fullscreenFinalReplyTimerRef = useWbcRef(null);
  var fullscreenReplyBaselineRef = useWbcRef("");
  // Minimized browser mode is temporarily retired. Normalize any stale
  // in-memory value back to the fixed PiP surface.
  var effectiveMode = mode === "minimized" ? "pip" : (mode || "pip");
  // Enter avoidance synchronously with the collapsed render. On reopening,
  // keep avoidance active through the panel's 500ms grid transition; the
  // effect below releases it only after the composer has finished shrinking.
  if (composerDocked) composerDockedRef.current = true;
  var displayBrowserState = nativeBrowserState || browserState || {};
  var displayBrowserTabs = Array.isArray(displayBrowserState.tabs) ? displayBrowserState.tabs : [];
  var displayActiveBrowserTab = displayBrowserState.activeTab || displayBrowserTabs.find(function (tab) {
    return String(tab && tab.id || "") === String(displayBrowserState.activeTabId || "");
  }) || displayBrowserTabs[0] || {};
  var displayBrowserFavicon = String(displayActiveBrowserTab.favicon || "");
  var hasNoBrowserTabs = Array.isArray(displayBrowserState.tabs) && displayBrowserState.tabs.length === 0;
  var browserBridge = window.cyrene && window.cyrene.browser;
  var FloatingBrowserIcon = window.CyreneUI.require("browser").Icon;
  var hasNativeChatOverlay = !!(browserBridge && typeof browserBridge.setChatOverlay === "function");
  var hasNativeTabPicker = !!(browserBridge && typeof browserBridge.setTabPicker === "function");
  var fullscreenSavedReply = !running && !fullscreenSubmitting
    && String(latestAssistantReplyId || "")
    && String(latestAssistantReplyId || "") !== fullscreenReplyBaselineRef.current
    ? String(latestAssistantReplyText || "").replace(/\s+/g, " ").trim()
    : "";
  var fullscreenCompletedReply = fullscreenFinalReply || fullscreenSavedReply;
  var fullscreenStatusVisible = effectiveMode === "maximized"
    && fullscreenStatusRequested
    && (!!running || fullscreenSubmitting || !!fullscreenCompletedReply);
  var fullscreenStatusText = fullscreenCompletedReply || wbcBrowserFullscreenStatusText(runtime);

  function browserDragPayload() {
    return {
      kind: "browser",
      ownerSessionId: String(browserSessionId || ""),
      stableRef: String(browserSessionId || ""),
      title: String(displayActiveBrowserTab.title || wbcT("workbenchChat.browserWindowTitle", "Browser")),
      url: String(displayActiveBrowserTab.url || ""),
      tabId: String(displayActiveBrowserTab.id || displayBrowserState.activeTabId || ""),
      favicon: displayBrowserFavicon,
    };
  }

  function updateResourceShelfTarget(interaction, clientX, clientY) {
    if (!interaction || interaction.kind !== "drag") return false;
    interaction.lastClientX = clientX;
    interaction.lastClientY = clientY;
    var overShelf = wbcPointInsideResourceShelf(clientX, clientY);
    if (interaction.overShelf !== overShelf) {
      interaction.overShelf = overShelf;
      wbcNotifyResourceShelfPointerDrag(overShelf);
    }
    var conversationTarget = overShelf
      ? null
      : wbcConversationTabAtPoint(clientX, clientY, browserSessionId);
    var nextNode = conversationTarget && conversationTarget.node;
    if (interaction.targetChatNode !== nextNode) {
      if (interaction.targetChatNode) interaction.targetChatNode.classList.remove("resource-drop-target");
      interaction.targetChatNode = nextNode || null;
      if (interaction.targetChatNode) interaction.targetChatNode.classList.add("resource-drop-target");
    }
    interaction.targetChatId = conversationTarget ? conversationTarget.chatId : "";
    if (interaction.ghost) {
      interaction.ghost.classList.toggle("drop-ready", overShelf || !!interaction.targetChatId);
    }
    return overShelf || !!interaction.targetChatId;
  }

  function clearBrowserPointerDropTarget(interaction) {
    if (interaction && interaction.targetChatNode) {
      interaction.targetChatNode.classList.remove("resource-drop-target");
      interaction.targetChatNode = null;
      interaction.targetChatId = "";
    }
    wbcNotifyResourceShelfPointerDrag(false);
  }

  function pinBrowserFromPointerInteraction(interaction) {
    if (!interaction || interaction.pinned || interaction.delivered) return false;
    if (interaction.targetChatId) {
      interaction.delivered = true;
      try {
        window.dispatchEvent(new CustomEvent("cyrene:copy-browser-to-chat", {
          detail: {
            targetChatId: interaction.targetChatId,
            resource: browserDragPayload(),
          },
        }));
      } catch (e) {}
      return true;
    }
    if (!interaction.overShelf) return false;
    interaction.pinned = true;
    try {
      window.dispatchEvent(new CustomEvent("cyrene:pin-topbar-resource", {
        detail: browserDragPayload(),
      }));
    } catch (e) {}
    return true;
  }

  function clearFullscreenFinalReplyTimer() {
    if (!fullscreenFinalReplyTimerRef.current) return;
    clearTimeout(fullscreenFinalReplyTimerRef.current);
    fullscreenFinalReplyTimerRef.current = null;
  }

  function sendFullscreenChatText(value) {
    var text = String(value || "").trim();
    if (!text) return;
    var wasRunning = !!running;
    clearFullscreenFinalReplyTimer();
    fullscreenReplyBaselineRef.current = String(latestAssistantReplyId || "");
    setFullscreenFinalReply("");
    setFullscreenStatusRequested(true);
    setFullscreenSubmitting(true);
    var request;
    try {
      request = wasRunning && onGuidance
        ? onGuidance(text)
        : (onSend ? onSend({ message: text, attachments: [], command: "" }) : null);
    } catch (error) {
      if (!hasNativeChatOverlay) setFullscreenDraft(text);
      setFullscreenStatusRequested(false);
      setFullscreenSubmitting(false);
      return;
    }
    Promise.resolve(request).catch(function () {
      if (!hasNativeChatOverlay) setFullscreenDraft(text);
      setFullscreenStatusRequested(false);
    }).finally(function () {
      setFullscreenSubmitting(false);
    });
  }

  function submitFullscreenChat(event) {
    if (event) event.preventDefault();
    var text = String(fullscreenDraft || "").trim();
    if (!text) {
      if (running && onInterrupt) onInterrupt();
      return;
    }
    setFullscreenDraft("");
    sendFullscreenChatText(text);
  }

  function cancelModeTransition() {
    if (modeTransitionReadyHandlerRef.current) {
      window.removeEventListener("workbench:browser-transition-target-ready", modeTransitionReadyHandlerRef.current);
      modeTransitionReadyHandlerRef.current = null;
    }
    if (modeTransitionRafRef.current) {
      cancelAnimationFrame(modeTransitionRafRef.current);
      modeTransitionRafRef.current = 0;
    }
    if (modeTransitionTimerRef.current) {
      clearTimeout(modeTransitionTimerRef.current);
      modeTransitionTimerRef.current = null;
    }
  }

  function measureBrowserSurfaceForMode(targetMode) {
    var shell = shellRef.current;
    var host = targetMode === "pip"
      ? document.querySelector(".wbc-browser-movement-region")
      : shell && shell.parentElement;
    if (!shell || !host) return null;
    // The maximized shell is viewport-fixed. Measure its preview under body so
    // it uses the same containing block as the committed shell after the
    // conversation stage releases its transform promotion.
    var measurementHost = targetMode === "maximized" ? document.body : host;
    var clone = shell.cloneNode(true);
    clone.className = "wbc-browser-window " + targetMode;
    clone.removeAttribute("style");
    clone.setAttribute("aria-hidden", "true");
    clone.style.visibility = "hidden";
    clone.style.pointerEvents = "none";
    clone.style.transition = "none";
    measurementHost.appendChild(clone);
    var surface = clone.querySelector(".browser-native-surface");
    var rect = surface && surface.getBoundingClientRect();
    clone.remove();
    if (!rect || rect.width <= 8 || rect.height <= 8) return null;
    return {
      x: rect.left,
      y: rect.top,
      width: rect.width,
      height: rect.height,
      borderRadius: 0,
      pageCornerRadius: targetMode === "pip" ? 8 : 0,
    };
  }

  function runModeTransition(action, targetMode) {
    if (!action) return;
    cancelModeTransition();

    // Restoring from the full browser back to PiP does not need a screenshot
    // preflight: both surfaces display the same live WebContentsView. Commit
    // the React mode and hand Electron the already measurable PiP rectangle in
    // the same frame so the page is visible immediately instead of waiting for
    // the transition-preview timeout.
    if (targetMode === "pip" && effectiveMode === "maximized") {
      var restoreBounds = measureBrowserSurfaceForMode("pip");
      var commitRestore = function () { action(); };
      if (window.ReactDOM && typeof window.ReactDOM.flushSync === "function") {
        window.ReactDOM.flushSync(commitRestore);
      } else {
        commitRestore();
      }
      if (restoreBounds && browserBridge && typeof browserBridge.setBounds === "function") {
        browserBridge.setBounds({
          ...restoreBounds,
          sessionId: browserSessionId,
          visible: true,
          forceVisible: true,
          zoomEnabled: true,
        }).catch(function () {});
      }
      modeTransitionRafRef.current = requestAnimationFrame(function () {
        modeTransitionRafRef.current = 0;
        wbcNotifyBrowserLayoutChanged();
        wbcNotifyBrowserWindowInteraction(false, "mode", browserSessionId);
      });
      return;
    }

    var started = false;
    function applyModeAfterPreview() {
      if (started) return;
      started = true;
      if (modeTransitionReadyHandlerRef.current) {
        window.removeEventListener("workbench:browser-transition-target-ready", modeTransitionReadyHandlerRef.current);
        modeTransitionReadyHandlerRef.current = null;
      }
      if (modeTransitionTimerRef.current) {
        clearTimeout(modeTransitionTimerRef.current);
        modeTransitionTimerRef.current = null;
      }
      var commitModeAndPreview = function () {
        action();
        window.dispatchEvent(new CustomEvent("workbench:browser-transition-commit-preview", {
          detail: { sessionId: browserSessionId },
        }));
      };
      // The target screenshot and the target shell must become visible in the
      // same renderer commit. Without flushSync React may paint the target page
      // inside the old PiP shell first, which looks like an extra zoom step.
      if (window.ReactDOM && typeof window.ReactDOM.flushSync === "function") {
        window.ReactDOM.flushSync(commitModeAndPreview);
      } else {
        commitModeAndPreview();
      }
      // Let React commit the target shell and the browser surface finish layout
      // before asking Electron to attach at the new rectangle.
      modeTransitionRafRef.current = requestAnimationFrame(function () {
        modeTransitionRafRef.current = requestAnimationFrame(function () {
          modeTransitionRafRef.current = 0;
          wbcNotifyBrowserWindowInteraction(false, "mode", browserSessionId);
        });
      });
    }
    modeTransitionReadyHandlerRef.current = function (event) {
      var detail = event && event.detail || {};
      if (String(detail.sessionId || "") !== String(browserSessionId || "")) return;
      applyModeAfterPreview();
    };
    window.addEventListener("workbench:browser-transition-target-ready", modeTransitionReadyHandlerRef.current);
    // Keep the current shell unchanged while Electron prepares a frame at the
    // target rectangle. The target proxy and the React mode commit then land in
    // one batch, so no stretched intermediate frame reaches the screen.
    wbcNotifyBrowserWindowInteraction(true, "mode", browserSessionId, {
      targetMode: targetMode || "",
      targetBounds: measureBrowserSurfaceForMode(targetMode || ""),
    });
    modeTransitionTimerRef.current = setTimeout(applyModeAfterPreview, 1800);
  }

  function measuredFloatingFrame(node) {
    var area = node && node.parentElement;
    if (!node || !area) return null;
    var nodeRect = node.getBoundingClientRect();
    var areaRect = area.getBoundingClientRect();
    return {
      x: nodeRect.left - areaRect.left,
      y: nodeRect.top - areaRect.top,
      width: nodeRect.width,
      height: nodeRect.height,
    };
  }

  function measuredFrame() {
    return measuredFloatingFrame(shellRef.current);
  }

  function browserVerticalOffset(node) {
    if (!node) return 0;
    return parseFloat(node.style.getPropertyValue("--wbc-browser-pip-user-offset-y")) || 0;
  }

  function browserVerticalBounds(host) {
    if (!host) return { minY: 0, maxBottom: 0 };
    var hostRect = host.getBoundingClientRect();
    var maxBottom = host.clientHeight;
    var minY = 0;
    var pane = host.closest && host.closest(".wbc-pane-card");
    if (pane) {
      var paneRect = pane.getBoundingClientRect();
      maxBottom = Math.max(maxBottom, paneRect.bottom - hostRect.top);
      var page = pane.closest && pane.closest(".wbc-page");
      var sideCard = page && page.querySelector(":scope > .wbc-side .wbc-side-card");
      if (sideCard) minY = Math.max(0, sideCard.getBoundingClientRect().bottom + 12 - hostRect.top);
    }
    minY = Math.min(minY, maxBottom);
    return { minY: minY, maxBottom: maxBottom };
  }

  function commitFrame(next, area, anchorFrame, anchorOffset, customizeHeight) {
    var host = area || (shellRef.current && shellRef.current.parentElement);
    var node = shellRef.current;
    var measured = measuredFloatingFrame(node);
    if (!host || !node || !measured || !next) return;
    // The context-panel track owns horizontal geometry. User interaction only
    // changes the PiP's vertical position and height, so resizing the panel can
    // continue to update the browser width without fighting an inline frame.
    var verticalBounds = browserVerticalBounds(host);
    var availableHeight = Math.max(0, verticalBounds.maxBottom - verticalBounds.minY);
    var minimumHeight = Math.min(180, availableHeight || 180);
    var nextHeight = Math.min(
      Math.max(minimumHeight, Number(next.height) || measured.height),
      availableHeight || measured.height
    );
    var verticalFrame = {
      x: measured.x,
      y: Math.min(
        Math.max(verticalBounds.minY, Number(next.y) || 0),
        Math.max(verticalBounds.minY, verticalBounds.maxBottom - nextHeight)
      ),
      width: measured.width,
      height: nextHeight,
    };
    var clamped = verticalFrame;
    var constrained = composerDockedRef.current
      ? wbcKeepBrowserWindowClearOfComposer(clamped, host)
      : clamped;
    var anchor = anchorFrame || measured;
    var offset = Number.isFinite(anchorOffset) ? anchorOffset : browserVerticalOffset(node);
    // The CSS shell is bottom-anchored. Express the user's translation as a
    // bottom-edge delta so north resizing keeps the bottom fixed, south
    // resizing keeps the top fixed, and dragging moves both edges together.
    var nextOffset = offset
      + (constrained.y + constrained.height)
      - (anchor.y + anchor.height);
    var heightCustomized = customizeHeight === true || node.dataset.verticalHeightCustomized === "true";
    if (heightCustomized) {
      node.style.setProperty("--wbc-browser-pip-user-height", Math.round(constrained.height) + "px");
      node.dataset.verticalHeightCustomized = "true";
    } else {
      node.style.removeProperty("--wbc-browser-pip-user-height");
      delete node.dataset.verticalHeightCustomized;
    }
    node.style.setProperty("--wbc-browser-pip-user-offset-y", Math.round(nextOffset) + "px");
    node.dataset.verticalCustomized = "true";
    constrained.heightCustomized = heightCustomized;
    frameRef.current = constrained;
    setFrame(constrained);
    wbcNotifyBrowserLayoutChanged();
    var committed = constrained;
    // Collapsing the conversation panel creates a temporary dock above the
    // composer. Do not overwrite the user's saved drag/resize position with
    // that transient frame; reopening the panel must restore it exactly.
    if (committed && !composerDockedRef.current) wbcSaveBrowserWindowFrame(browserSessionId, committed);
  }

  // PiP and minimized mode share the same coordinate system and clamping
  // path. This keeps drag persistence, resize handling, and transcript
  // avoidance in sync instead of maintaining two subtly different movers.
  function commitFloatingFrame(node, next, host, minWidth, minHeight, targetRef, updateState) {
    if (!node || !host || !next) return null;
    var clamped = wbcClampBrowserWindowFrame(
      next,
      host.clientWidth,
      host.clientHeight,
      minWidth,
      minHeight
    );
    targetRef.current = clamped;
    // Keep the DOM shell and Electron's native WebContentsView on the same
    // pointer frame. Waiting for React to commit here makes the page visibly
    // trail the window chrome during a drag or resize.
    node.style.left = clamped.x + "px";
    node.style.top = clamped.y + "px";
    node.style.width = clamped.width + "px";
    node.style.height = clamped.height + "px";
    node.style.right = "auto";
    node.style.bottom = "auto";
    updateState(clamped);
    wbcNotifyBrowserLayoutChanged();
    return clamped;
  }

  function commitMinimizedFrame(next, area) {
    var node = minimizedRef.current;
    var host = area || (node && node.parentElement);
    return commitFloatingFrame(node, next, host, 42, 42, minimizedFrameRef, setMinimizedFrame);
  }

  function removeMinimizedDragGhost(interaction) {
    var ghost = interaction && interaction.ghost;
    if (ghost && ghost.parentNode) ghost.parentNode.removeChild(ghost);
    if (interaction) interaction.ghost = null;
  }

  function ensureMinimizedDragGhost(interaction) {
    if (!interaction || interaction.ghost || !interaction.node) return interaction && interaction.ghost;
    var rect = interaction.node.getBoundingClientRect();
    var ghost = interaction.node.cloneNode(true);
    ghost.removeAttribute("id");
    ghost.removeAttribute("title");
    ghost.setAttribute("aria-hidden", "true");
    ghost.setAttribute("tabindex", "-1");
    ghost.classList.add("dragging", "wbc-browser-drag-ghost");
    // Keep the preview slightly smaller than the resting control so it reads
    // as a lifted drag token instead of merging with header action buttons.
    var ghostInset = 3;
    ghost.style.left = (rect.left + ghostInset) + "px";
    ghost.style.top = (rect.top + ghostInset) + "px";
    ghost.style.width = Math.max(32, rect.width - (ghostInset * 2)) + "px";
    ghost.style.height = Math.max(32, rect.height - (ghostInset * 2)) + "px";
    document.body.appendChild(ghost);
    interaction.ghost = ghost;
    interaction.ghostLeft = rect.left + ghostInset;
    interaction.ghostTop = rect.top + ghostInset;
    interaction.node.classList.add("drag-source-hidden");
    return ghost;
  }

  function finalizeInteraction(interaction) {
    if (!interaction) return;
    if (interaction.previewTimer) clearTimeout(interaction.previewTimer);
    interaction.previewTimer = null;
    if (interactionRef.current === interaction) interactionRef.current = null;
    window.removeEventListener("workbench:browser-window-preview-ready", onBrowserWindowPreviewReady);
    document.body.classList.remove("wbc-browser-window-interacting");
    clearBrowserPointerDropTarget(interaction);
    wbcNotifyBrowserLayoutChanged();
    wbcNotifyBrowserWindowInteraction(false, interaction.kind, browserSessionId);
  }

  function stopInteraction() {
    var event = arguments[0];
    var interaction = interactionRef.current;
    if (interaction && interaction.captureNode && interaction.captureNode.releasePointerCapture) {
      try { interaction.captureNode.releasePointerCapture(interaction.pointerId); } catch (e) {}
    }
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", stopInteraction);
    window.removeEventListener("pointercancel", stopInteraction);
    // A plain click on the title bar never starts a native-view transition.
    // Only finish an interaction after movement crossed the drag threshold.
    if (!interaction || !interaction.started) {
      interactionRef.current = null;
      window.removeEventListener("workbench:browser-window-preview-ready", onBrowserWindowPreviewReady);
      clearBrowserPointerDropTarget(interaction);
      return;
    }
    if (event && event.type === "pointerup" && interaction.kind === "drag") {
      updateResourceShelfTarget(interaction, event.clientX, event.clientY);
      pinBrowserFromPointerInteraction(interaction);
    }
    interaction.pointerReleased = true;
    // A fast flick can release before capturePage resolves. Keep its final
    // delta alive; preview-ready will commit it without exposing an old native
    // frame. The timeout follows the same path if capture IPC ever stalls.
    if (!interaction.previewReady) return;
    finalizeInteraction(interaction);
  }

  function commitInteractionDelta(interaction, dx, dy) {
    if (!interaction) return;
    var start = interaction.frame;
    var next = { x: start.x, y: start.y, width: start.width, height: start.height };
    if (interaction.kind === "drag") {
      next.y = start.y + dy;
    } else {
      var direction = interaction.direction;
      var right = start.x + start.width;
      var bottom = start.y + start.height;
      var minWidth = Math.min(240, interaction.area.clientWidth);
      var verticalBounds = browserVerticalBounds(interaction.area);
      var areaHeight = verticalBounds.maxBottom;
      var minHeight = Math.min(180, Math.max(0, areaHeight - verticalBounds.minY));
      if (direction.indexOf("e") !== -1) right = Math.min(interaction.area.clientWidth, Math.max(start.x + minWidth, right + dx));
      if (direction.indexOf("s") !== -1) bottom = Math.min(areaHeight, Math.max(start.y + minHeight, bottom + dy));
      if (direction.indexOf("w") !== -1) next.x = Math.max(0, Math.min(right - minWidth, start.x + dx));
      if (direction.indexOf("n") !== -1) next.y = Math.max(verticalBounds.minY, Math.min(bottom - minHeight, start.y + dy));
      next.width = right - next.x;
      next.height = bottom - next.y;
    }
    commitFrame(
      next,
      interaction.area,
      interaction.frame,
      interaction.offsetY,
      interaction.kind === "resize"
    );
  }

  function onBrowserWindowPreviewReady(event) {
    var detail = event && event.detail || {};
    if (String(detail.sessionId || "") !== String(browserSessionId || "")) return;
    var interaction = interactionRef.current;
    if (!interaction || !interaction.started) return;
    if (interaction.previewTimer) clearTimeout(interaction.previewTimer);
    interaction.previewTimer = null;
    if (detail.fallback) {
      // The native view was not replaced by a painted proxy. Keep the shell at
      // its original position and cancel this gesture; moving it now would
      // expose the still-visible native view as a second detached rectangle.
      interaction.cancelled = true;
      interaction.previewReady = true;
      if (interaction.pointerReleased) finalizeInteraction(interaction);
      return;
    }
    interaction.previewReady = true;
    commitInteractionDelta(interaction, interaction.pendingDx, interaction.pendingDy);
    if (interaction.pointerReleased) finalizeInteraction(interaction);
  }

  function onPointerMove(event) {
    var interaction = interactionRef.current;
    if (!interaction) return;
    var dx = event.clientX - interaction.clientX;
    var dy = event.clientY - interaction.clientY;
    if (!interaction.started) {
      if ((dx * dx) + (dy * dy) < 9) return;
      interaction.started = true;
      document.body.classList.add("wbc-browser-window-interacting");
      wbcNotifyBrowserWindowInteraction(true, interaction.kind, browserSessionId);
      interaction.previewTimer = setTimeout(function () {
        wbcNotifyBrowserWindowInteraction(false, interaction.kind, browserSessionId);
        onBrowserWindowPreviewReady({ detail: { sessionId: browserSessionId, fallback: true } });
      }, 750);
    }
    updateResourceShelfTarget(interaction, event.clientX, event.clientY);
    interaction.pendingDx = dx;
    interaction.pendingDy = dy;
    if (interaction.cancelled) return;
    // Keep the native page and shell at their original coordinates until the
    // bitmap proxy is committed and Electron confirms the native view hidden.
    // This removes the single exposed background/old-position frame at start.
    if (!interaction.previewReady) return;
    commitInteractionDelta(interaction, dx, dy);
  }

  function beginInteraction(event, kind, direction) {
    if (effectiveMode !== "pip" || event.button !== 0) return;
    if (kind === "drag" && event.target && event.target.closest && event.target.closest("button")) return;
    var node = shellRef.current;
    var area = node && node.parentElement;
    // Width and default 4:3 height can change whenever the context panel is
    // resized, so every gesture must start from the live rendered rectangle.
    var start = measuredFrame();
    if (!node || !area || !start) return;
    event.preventDefault();
    interactionRef.current = {
      kind: kind,
      direction: direction || "",
      clientX: event.clientX,
      clientY: event.clientY,
      frame: start,
      offsetY: browserVerticalOffset(node),
      area: area,
      pointerId: event.pointerId,
      captureNode: event.currentTarget,
      started: false,
      previewReady: false,
      pendingDx: 0,
      pendingDy: 0,
      pointerReleased: false,
      cancelled: false,
      previewTimer: null,
      overShelf: false,
      targetChatId: "",
      targetChatNode: null,
      lastClientX: event.clientX,
      lastClientY: event.clientY,
    };
    if (event.currentTarget && event.currentTarget.setPointerCapture) {
      try { event.currentTarget.setPointerCapture(event.pointerId); } catch (e) {}
    }
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", stopInteraction);
    window.addEventListener("pointercancel", stopInteraction);
    window.addEventListener("workbench:browser-window-preview-ready", onBrowserWindowPreviewReady);
  }

  function finishMinimizedDrag(event) {
    var interaction = minimizedDragRef.current;
    if (!interaction) return;
    minimizedDragRef.current = null;
    window.removeEventListener("pointermove", moveMinimizedDrag);
    window.removeEventListener("pointerup", finishMinimizedDrag);
    window.removeEventListener("pointercancel", finishMinimizedDrag);
    if (interaction.captureNode && interaction.captureNode.releasePointerCapture) {
      try { interaction.captureNode.releasePointerCapture(interaction.pointerId); } catch (e) {}
    }
    if (interaction.node) interaction.node.classList.remove("dragging", "drag-source-hidden");
    removeMinimizedDragGhost(interaction);
    document.body.classList.remove("wbc-browser-window-interacting");
    var handled = false;
    if (event && event.type === "pointerup" && interaction.started) {
      updateResourceShelfTarget(interaction, event.clientX, event.clientY);
      handled = pinBrowserFromPointerInteraction(interaction);
    }
    // A resource drop is an operation on the browser, not a request to move
    // its restore button to the stage boundary. Put the button back where the
    // drag started after pinning/copying; ordinary drags keep their new frame.
    if (handled && interaction.frame) {
      commitMinimizedFrame(interaction.frame, interaction.area);
    } else if (interaction.started) {
      wbcNotifyBrowserLayoutChanged();
    }
    clearBrowserPointerDropTarget(interaction);
    if (interaction.started) {
      suppressMinimizedClickRef.current = true;
      setTimeout(function () { suppressMinimizedClickRef.current = false; }, 0);
    }
  }

  function moveMinimizedDrag(event) {
    var interaction = minimizedDragRef.current;
    if (!interaction) return;
    var dx = event.clientX - interaction.clientX;
    var dy = event.clientY - interaction.clientY;
    if (!interaction.started) {
      if ((dx * dx) + (dy * dy) < 9) return;
      interaction.started = true;
      document.body.classList.add("wbc-browser-window-interacting");
      if (interaction.node) interaction.node.classList.add("dragging");
      ensureMinimizedDragGhost(interaction);
    }
    updateResourceShelfTarget(interaction, event.clientX, event.clientY);
    if (interaction.ghost) {
      interaction.ghost.style.left = (interaction.ghostLeft + dx) + "px";
      interaction.ghost.style.top = (interaction.ghostTop + dy) + "px";
    }
    if (interaction.frame) {
      commitMinimizedFrame({
        x: interaction.frame.x + dx,
        y: interaction.frame.y + dy,
        width: interaction.frame.width,
        height: interaction.frame.height,
      }, interaction.area);
    }
  }

  function beginMinimizedDrag(event) {
    if (event.button !== 0) return;
    var node = event.currentTarget;
    var area = node && node.parentElement;
    var start = minimizedFrameRef.current || measuredFloatingFrame(node);
    if (!node || !area || !start) return;
    event.preventDefault();
    minimizedDragRef.current = {
      kind: "drag",
      clientX: event.clientX,
      clientY: event.clientY,
      lastClientX: event.clientX,
      lastClientY: event.clientY,
      pointerId: event.pointerId,
      captureNode: event.currentTarget,
      node: node,
      area: area,
      frame: start,
      ghost: null,
      started: false,
      overShelf: false,
      targetChatId: "",
      targetChatNode: null,
      pinned: false,
    };
    if (event.currentTarget && event.currentTarget.setPointerCapture) {
      try { event.currentTarget.setPointerCapture(event.pointerId); } catch (e) {}
    }
    window.addEventListener("pointermove", moveMinimizedDrag);
    window.addEventListener("pointerup", finishMinimizedDrag);
    window.addEventListener("pointercancel", finishMinimizedDrag);
  }

  useWbcEffect(function () {
    var savedFrame = wbcLoadBrowserWindowFrame(browserSessionId);
    frameSessionRef.current = String(browserSessionId || "");
    frameRef.current = savedFrame;
    composerDockedRef.current = composerDocked === true;
    preComposerDockFrameRef.current = null;
    minimizedFrameRef.current = null;
    setFrame(savedFrame);
    setMinimizedFrame(null);
  }, [browserSessionId]);

  useWbcEffect(function () {
    if (!visible || effectiveMode !== "pip" || !frameRef.current) return undefined;
    var raf = requestAnimationFrame(function () {
      var node = shellRef.current;
      var area = node && node.parentElement;
      var measured = measuredFloatingFrame(node);
      var saved = frameRef.current;
      if (!node || !area || !measured || !saved) return;
      commitFrame({
        x: measured.x,
        y: saved.y,
        width: measured.width,
        height: saved.height,
      }, area, measured, browserVerticalOffset(node), saved.heightCustomized === true);
    });
    return function () { cancelAnimationFrame(raf); };
  }, [visible, effectiveMode, browserSessionId]);

  useWbcEffect(function () {
    if (!visible || effectiveMode !== "pip") return undefined;
    var node = shellRef.current;
    var area = node && node.parentElement;
    if (!node || !area) return undefined;

    if (composerDocked) {
      if (composerDockRestoreTimerRef.current) {
        clearTimeout(composerDockRestoreTimerRef.current);
        composerDockRestoreTimerRef.current = null;
      }
      if (!preComposerDockFrameRef.current) {
        preComposerDockFrameRef.current = frameRef.current || measuredFrame();
      }
      var original = preComposerDockFrameRef.current;
      if (original) commitFrame(original, area);
      return undefined;
    }

    var restore = preComposerDockFrameRef.current;
    if (!restore) return undefined;
    composerDockRestoreTimerRef.current = setTimeout(function () {
      composerDockRestoreTimerRef.current = null;
      composerDockedRef.current = false;
      preComposerDockFrameRef.current = null;
      commitFrame(restore, area);
      wbcNotifyBrowserLayoutChanged();
    }, 520);
    return function () {
      if (!composerDockRestoreTimerRef.current) return;
      clearTimeout(composerDockRestoreTimerRef.current);
      composerDockRestoreTimerRef.current = null;
    };
  }, [composerDocked, visible, effectiveMode]);

  // A fullscreen session starts visually quiet. Only commands sent from this
  // compact composer opt into the live status pill; runs that were already in
  // progress before maximizing therefore do not create unsolicited chrome.
  useWbcEffect(function () {
    clearFullscreenFinalReplyTimer();
    setFullscreenDraft("");
    setFullscreenStatusRequested(false);
    setFullscreenSubmitting(false);
    setFullscreenFinalReply("");
    fullscreenReplyBaselineRef.current = String(latestAssistantReplyId || "");
    return clearFullscreenFinalReplyTimer;
  }, [effectiveMode, browserSessionId]);

  // A saved assistant message lands in the durable transcript at the same time
  // the live runtime disappears. Keep that final reply in the compact status
  // pill briefly, then remove the pill and return its pixels to the page. Runs
  // that finish without a textual reply retain the old immediate-hide behavior
  // after a short grace period for the saved message update to arrive.
  useWbcEffect(function () {
    if (effectiveMode !== "maximized" || !fullscreenStatusRequested) return undefined;
    if (running || fullscreenSubmitting || fullscreenFinalReply) return undefined;
    var replyId = String(latestAssistantReplyId || "");
    var replyText = String(latestAssistantReplyText || "").replace(/\s+/g, " ").trim();
    if (replyText && replyId && replyId !== fullscreenReplyBaselineRef.current) {
      setFullscreenFinalReply(replyText);
      clearFullscreenFinalReplyTimer();
      fullscreenFinalReplyTimerRef.current = setTimeout(function () {
        fullscreenFinalReplyTimerRef.current = null;
        setFullscreenFinalReply("");
        setFullscreenStatusRequested(false);
      }, 5000);
      return undefined;
    }
    var settleTimer = setTimeout(function () {
      setFullscreenStatusRequested(false);
    }, 1200);
    return function () { clearTimeout(settleTimer); };
  }, [effectiveMode, running, fullscreenSubmitting, fullscreenStatusRequested, fullscreenFinalReply, latestAssistantReplyId, latestAssistantReplyText]);

  // Electron's live page is a native WebContentsView and therefore composites
  // above renderer DOM. Its compact chat is a second transparent native view,
  // raised above the page; the web/screencast fallback keeps the DOM version.
  useWbcEffect(function () {
    if (!hasNativeChatOverlay || typeof browserBridge.onChatOverlayAction !== "function") return undefined;
    return browserBridge.onChatOverlayAction(function (action) {
      if (!action || String(action.sessionId || "") !== String(browserSessionId || "")) return;
      if (effectiveMode !== "maximized") return;
      if (action.type === "stop") {
        if (running && onInterrupt) onInterrupt();
        return;
      }
      sendFullscreenChatText(action.text || "");
    });
  }, [hasNativeChatOverlay, browserSessionId, effectiveMode, running, onSend, onGuidance, onInterrupt]);

  // The native overlay lives in a separate renderer, so CSS variables do not
  // cascade into it. Re-send its palette whenever the host theme or accent is
  // applied to the document root.
  useWbcEffect(function () {
    if (!hasNativeChatOverlay) return undefined;
    var root = document.documentElement;
    var frameId = 0;
    function refreshOverlayTheme() {
      if (frameId) return;
      frameId = requestAnimationFrame(function () {
        frameId = 0;
        setChatOverlayThemeRevision(function (value) { return value + 1; });
      });
    }
    var observer = typeof MutationObserver === "function"
      ? new MutationObserver(refreshOverlayTheme)
      : null;
    if (observer) observer.observe(root, { attributes: true, attributeFilter: ["data-theme", "style"] });
    window.addEventListener("cyrene-tweak-theme-change", refreshOverlayTheme);
    window.addEventListener("cyrene-tweak-accent-change", refreshOverlayTheme);
    return function () {
      if (frameId) cancelAnimationFrame(frameId);
      if (observer) observer.disconnect();
      window.removeEventListener("cyrene-tweak-theme-change", refreshOverlayTheme);
      window.removeEventListener("cyrene-tweak-accent-change", refreshOverlayTheme);
    };
  }, [hasNativeChatOverlay]);

  useWbcEffect(function () {
    if (!hasNativeChatOverlay) return;
    var paletteNode = document.querySelector(".workbench-shell") || document.documentElement;
    var rootStyles = getComputedStyle(paletteNode);
    function color(name, fallback) {
      return String(rootStyles.getPropertyValue(name) || "").trim() || fallback;
    }
    browserBridge.setChatOverlay({
      sessionId: browserSessionId || "",
      visible: visible && effectiveMode === "maximized",
      running: !!running,
      showStatus: fullscreenStatusVisible,
      statusText: fullscreenStatusText,
      statusComplete: !!fullscreenCompletedReply,
      placeholder: wbcT("workbenchChat.browserChatPlaceholder", "Tell Agent what to do in the browser…"),
      placeholderRunning: wbcT("workbenchChat.browserChatPlaceholderRunning", "Add an instruction…"),
      sendLabel: wbcT("workbenchChat.send", "Send"),
      guideLabel: wbcT("workbenchChat.sendGuidance", "Send guidance"),
      stopLabel: wbcT("workbenchChat.stop", "Stop"),
      colors: {
        line: color("--wb-line-2", "#d8dce4"),
        panel: color("--wb-card-bg-strong", "#ffffff"),
        text: color("--wb-text", "#17191d"),
        muted: color("--wb-muted", "#6f737b"),
        faint: color("--wb-faint", "#9297a1"),
        accent: color("--wb-accent", "#6d5dfc"),
        "accent-text": color("--wb-accent-text", "#ffffff"),
        green: color("--wb-green", "#1f9d57"),
        red: color("--wb-red", "#d84848"),
      },
    }).catch(function () {});
  }, [hasNativeChatOverlay, browserSessionId, visible, effectiveMode, running, fullscreenStatusVisible, fullscreenStatusText, fullscreenCompletedReply, chatOverlayThemeRevision]);

  useWbcEffect(function () {
    if (!hasNativeChatOverlay) return undefined;
    return function () {
      browserBridge.setChatOverlay({ sessionId: browserSessionId || "", visible: false }).catch(function () {});
    };
  }, [hasNativeChatOverlay, browserSessionId]);

  useWbcEffect(function () {
    var bridge = window.cyrene && window.cyrene.browser;
    var sessionId = String(browserSessionId || "");
    setNativeBrowserState(null);
    if (!visible || !sessionId || !bridge || typeof bridge.getState !== "function") return undefined;
    bridge.getState(sessionId).then(function (next) {
      if (next && String(next.sessionId || "") === sessionId) setNativeBrowserState(next);
    }).catch(function () {});
    if (typeof bridge.onState !== "function") return undefined;
    return bridge.onState(function (next) {
      if (next && String(next.sessionId || "") === sessionId) setNativeBrowserState(next);
    });
  }, [visible, browserSessionId]);

  function updateFloatingBrowserState(next) {
    if (next && next.ok !== false && String(next.sessionId || "") === String(browserSessionId || "")) {
      setNativeBrowserState(next);
    }
    return next;
  }

  function setMaximizedBrowserPicker(nextOpen) {
    var visible = nextOpen === true;
    setMaximizedPickerOpen(visible);
    if (!hasNativeTabPicker) return;
    browserBridge.setTabPicker(
      wbcBrowserTabPickerPayload(browserSessionId, visible, "maximized")
    ).catch(function () { setMaximizedPickerOpen(false); });
  }

  function selectMaximizedBrowserTab(tab) {
    if (!tab || !tab.id) return;
    setMaximizedBrowserPicker(false);
    if (browserBridge && typeof browserBridge.activateTab === "function") {
      browserBridge.activateTab({ sessionId: browserSessionId, tabId: tab.id }).then(updateFloatingBrowserState).catch(function () {});
    }
  }

  function refreshMaximizedBrowserTab(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!browserBridge || !tab || !tab.id || typeof browserBridge.reload !== "function") return;
    browserBridge.reload({ sessionId: browserSessionId, tabId: tab.id }).then(updateFloatingBrowserState).catch(function () {});
  }

  function toggleMaximizedBrowserMute(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!browserBridge || !tab || !tab.id || typeof browserBridge.setMuted !== "function") return;
    browserBridge.setMuted({ sessionId: browserSessionId, tabId: tab.id, muted: !tab.muted }).then(updateFloatingBrowserState).catch(function () {});
  }

  function closeMaximizedBrowserTab(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!browserBridge || !tab || !tab.id || typeof browserBridge.closeTab !== "function") return;
    browserBridge.closeTab({ sessionId: browserSessionId, tabId: tab.id }).then(function (next) {
      updateFloatingBrowserState(next);
      if (!next || !Array.isArray(next.tabs) || !next.tabs.length) setMaximizedBrowserPicker(false);
    }).catch(function () {});
  }

  useWbcEffect(function () {
    if (!hasNativeTabPicker || typeof browserBridge.onTabPickerAction !== "function") return undefined;
    return browserBridge.onTabPickerAction(function (action) {
      if (!action || String(action.sessionId || "") !== String(browserSessionId || "")) return;
      if (action.variant !== "maximized") return;
      setMaximizedPickerOpen(action.visible === true);
    });
  }, [hasNativeTabPicker, browserSessionId]);

  useWbcEffect(function () {
    if (!maximizedPickerOpen) return undefined;
    function closeOnOutsidePointer(event) {
      if (maximizedPickerRef.current && !maximizedPickerRef.current.contains(event.target)) {
        setMaximizedBrowserPicker(false);
      }
    }
    function closeOnWindowBlur() { setMaximizedBrowserPicker(false); }
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    if (!hasNativeTabPicker) window.addEventListener("blur", closeOnWindowBlur);
    return function () {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      if (!hasNativeTabPicker) window.removeEventListener("blur", closeOnWindowBlur);
    };
  }, [maximizedPickerOpen, browserSessionId, hasNativeTabPicker]);

  useWbcEffect(function () {
    if (effectiveMode === "maximized") return undefined;
    setMaximizedBrowserPicker(false);
    return undefined;
  }, [effectiveMode, browserSessionId, hasNativeTabPicker]);

  useWbcEffect(function () {
    if (!hasNativeTabPicker) return undefined;
    return function () {
      browserBridge.setTabPicker(
        wbcBrowserTabPickerPayload(browserSessionId, false, "maximized")
      ).catch(function () {});
    };
  }, [hasNativeTabPicker, browserSessionId]);

  useWbcEffect(function () {
    var wasVisible = previousVisibleRef.current;
    previousVisibleRef.current = visible;
    if (!wasVisible && visible && effectiveMode === "pip") {
      // The fixed PiP follows the conversation-panel track. Notify the native
      // browser again after the elastic grid transition has settled.
      if (pipRestoreTimerRef.current) clearTimeout(pipRestoreTimerRef.current);
      pipRestoreTimerRef.current = setTimeout(function () {
        pipRestoreTimerRef.current = null;
        wbcNotifyBrowserLayoutChanged();
      }, 570);
    }
    return function () {
      if (pipRestoreTimerRef.current) {
        clearTimeout(pipRestoreTimerRef.current);
        pipRestoreTimerRef.current = null;
      }
    };
  }, [visible, effectiveMode]);

  useWbcEffect(function () {
    if (!visible || effectiveMode !== "pip") return undefined;
    var node = shellRef.current;
    var area = node && node.parentElement;
    if (!area) return undefined;
    if (typeof ResizeObserver === "undefined") return undefined;
    var observer = new ResizeObserver(function () {
      wbcNotifyBrowserLayoutChanged();
    });
    observer.observe(area);
    var main = area.closest && area.closest(".wbc-main");
    var composer = main && main.querySelector(":scope > .wbc-composer");
    if (composer) observer.observe(composer);
    return function () { observer.disconnect(); };
  }, [visible, effectiveMode]);

  useWbcEffect(function () {
    var raf = requestAnimationFrame(wbcNotifyBrowserLayoutChanged);
    return function () { cancelAnimationFrame(raf); };
  }, [frame && frame.x, frame && frame.y, frame && frame.width, frame && frame.height, minimizedFrame && minimizedFrame.x, minimizedFrame && minimizedFrame.y, effectiveMode, visible]);

  useWbcEffect(function () {
    if (effectiveMode !== "maximized") return undefined;
    function onKeyDown(event) {
      if (event.key !== "Escape") return;
      if (maximizedPickerOpen) setMaximizedBrowserPicker(false);
      else if (onRestore) onRestore();
    }
    window.addEventListener("keydown", onKeyDown);
    return function () { window.removeEventListener("keydown", onKeyDown); };
  }, [effectiveMode, onRestore, maximizedPickerOpen]);

  useWbcEffect(function () {
    return function () {
      stopInteraction();
      finishMinimizedDrag();
      cancelModeTransition();
      if (composerDockRestoreTimerRef.current) clearTimeout(composerDockRestoreTimerRef.current);
    };
  }, []);

  function maximizeBrowserWindow() {
    return runModeTransition(onMaximize, "maximized");
  }

  function restoreBrowserWindow() {
    setMaximizedBrowserPicker(false);
    return runModeTransition(onRestore, "pip");
  }

  useWbcEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = window.CyreneUI.require("uiSurface");
    var isPresent = visible && !hasNoBrowserTabs && (effectiveMode === "pip" || effectiveMode === "maximized");
    var actions = effectiveMode === "pip" ? [{
      action_id: "maximize",
      kind: "invoke",
      gesture_aliases: ["double_press", "maximize_button"],
      risk: "R1",
    }] : [{
      action_id: "restore",
      kind: "invoke",
      gesture_aliases: ["double_press", "restore_button", "escape_key"],
      risk: "R1",
    }];
    return uiSurface.register({
      node_id: "browser_window_titlebar",
      parent_id: "root",
      get_node: function () {
        return isPresent ? {
          role: "window_titlebar",
          name: wbcT("workbenchChat.browserWindowTitle", "Browser"),
          state: { mode: effectiveMode },
        } : null;
      },
      actions: actions,
      handlers: {
        maximize: maximizeBrowserWindow,
        restore: restoreBrowserWindow,
      },
    });
  }, [visible, hasNoBrowserTabs, effectiveMode, browserSessionId, onMaximize, onRestore]);

  if (!visible) return null;
  if (hasNoBrowserTabs && effectiveMode === "pip") return null;
  var browserWindow = (
    <section
      ref={shellRef}
      className={"wbc-browser-window " + effectiveMode}
      aria-label={wbcT("workbenchChat.browserWindowRegion", "Live browser window")}
    >
      <div ref={effectiveMode === "maximized" ? maximizedPickerRef : undefined} className={effectiveMode === "maximized" ? "wbc-resource-split-picker-wrap wbc-browser-maximized-picker-wrap" : "wbc-browser-pip-head-wrap"}>
        <div
          className={"wbc-browser-window-bar" + (effectiveMode === "maximized" ? " wbc-browser-maximized-head" : "")}
          onPointerDown={effectiveMode === "pip" ? function (event) { beginInteraction(event, "drag", ""); } : undefined}
          onDoubleClick={effectiveMode === "pip" ? maximizeBrowserWindow : undefined}
        >
          {effectiveMode === "maximized" ? (
            <button type="button" className="wbc-browser-maximized-picker" onClick={function () {
              if (wbcBrowserTabPickerToggleIsDebounced(maximizedPickerToggleAtRef)) return;
              setMaximizedBrowserPicker(!maximizedPickerOpen);
            }} aria-expanded={maximizedPickerOpen}>
              <span className="wbc-browser-window-title">
                <span className="wbc-browser-title-pill">{wbcT("workbenchChat.browserWindowTitle", "Browser")}</span>
                <strong title={wbcBrowserWindowTitle(displayBrowserState)}>{wbcBrowserPageTitle(displayBrowserState) || wbcT("workbenchChat.browserWindowTitle", "Browser")}</strong>
              </span>
              <span className="wbc-browser-maximized-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            </button>
          ) : (
            <span className="wbc-browser-window-title">
              <span className="wbc-browser-title-pill">{wbcT("workbenchChat.browserWindowTitle", "Browser")}</span>
              {wbcBrowserPageTitle(displayBrowserState) && <strong title={wbcBrowserWindowTitle(displayBrowserState)}>{wbcBrowserPageTitle(displayBrowserState)}</strong>}
            </span>
          )}
          <div className="wbc-browser-window-actions" onPointerDown={function (event) { event.stopPropagation(); }}>
            {effectiveMode === "pip" ? (
              <button type="button" onClick={maximizeBrowserWindow} title={wbcT("workbenchChat.browserMaximize", "Maximize")} aria-label={wbcT("workbenchChat.browserMaximize", "Maximize")}>{WBC_ICONS.windowMaximize}</button>
            ) : (
              <React.Fragment>
                <button type="button" className="wbc-browser-split-action" onClick={function (event) { refreshMaximizedBrowserTab(displayActiveBrowserTab, event); }} title={wbcT("browser.context.reload", "Reload")} aria-label={wbcT("browser.context.reload", "Reload")}>{FloatingBrowserIcon ? <FloatingBrowserIcon name="reload" size={15} /> : WBC_ICONS.retry}</button>
                <button type="button" className={"wbc-browser-split-action" + (displayActiveBrowserTab.muted ? " active" : "")} onClick={function (event) { toggleMaximizedBrowserMute(displayActiveBrowserTab, event); }} title={displayActiveBrowserTab.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")} aria-label={displayActiveBrowserTab.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")}>{FloatingBrowserIcon ? <FloatingBrowserIcon name={displayActiveBrowserTab.muted ? "muted" : "volume"} size={15} /> : null}</button>
                <button type="button" onClick={restoreBrowserWindow} title={wbcT("workbenchChat.browserRestoreSize", "Restore")} aria-label={wbcT("workbenchChat.browserRestoreSize", "Restore")}>{WBC_ICONS.x}</button>
              </React.Fragment>
            )}
          </div>
        </div>
        {effectiveMode === "maximized" && !hasNativeTabPicker && maximizedPickerOpen && (
          <div className="wbc-side-agent-split-menu wbc-resource-picker-menu wbc-browser-picker-menu wbc-browser-maximized-menu open" role="listbox">
            {displayBrowserTabs.map(function (tab) {
              var selected = String(tab.id || "") === String(displayActiveBrowserTab.id || displayBrowserState.activeTabId || "");
              return <div key={tab.id} className={"wbc-browser-picker-row" + (selected ? " active" : "")} role="option" aria-selected={selected}><button type="button" className="wbc-browser-picker-select" onClick={function () { selectMaximizedBrowserTab(tab); }}><span className="wbc-browser-picker-favicon" aria-hidden="true"><span className="wbc-browser-picker-favicon-fallback">{WBC_SIDE_TAB_ICONS.browser}</span>{tab.favicon ? <img src={tab.favicon} alt="" draggable="false" onError={function (event) { event.currentTarget.hidden = true; }} /> : null}</span><b>{tab.title || tab.url || wbcT("workbenchChat.browserWindowTitle", "Browser")}</b></button><span className="wbc-browser-picker-actions"><button type="button" onClick={function (event) { refreshMaximizedBrowserTab(tab, event); }} aria-label={wbcT("browser.context.reload", "Reload")}>{FloatingBrowserIcon ? <FloatingBrowserIcon name="reload" size={14} /> : WBC_ICONS.retry}</button><button type="button" className={tab.muted ? "active" : ""} onClick={function (event) { toggleMaximizedBrowserMute(tab, event); }} aria-label={tab.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")}>{FloatingBrowserIcon ? <FloatingBrowserIcon name={tab.muted ? "muted" : "volume"} size={14} /> : null}</button><button type="button" onClick={function (event) { closeMaximizedBrowserTab(tab, event); }} aria-label={wbcT("browser.context.closeTab", "Close tab")} title={wbcT("browser.context.closeTab", "Close tab")}>{WBC_ICONS.x}</button></span></div>;
            })}
          </div>
        )}
      </div>
      <div className="wbc-browser-window-content">
        {window.CyreneUI.require("browser").ViewportPanel
          ? React.createElement(window.CyreneUI.require("browser").ViewportPanel, {
              browserState: browserState || {},
              browserSessionId: browserSessionId || "",
              roundId: (browserState && browserState.roundId) || "",
              onTakeoverComplete: onTakeoverComplete,
              hideTabStrip: effectiveMode === "maximized",
              hideReload: effectiveMode === "maximized",
              hideMute: effectiveMode === "maximized",
            })
          : <p className="workbench-muted">{wbcT("chat.side.browserUnavailable", "Browser view is unavailable.")}</p>}
        {effectiveMode === "maximized" && !hasNativeChatOverlay && (
          <div className="wbc-browser-fullscreen-chat">
            {fullscreenStatusVisible && (
              <div className={"wbc-browser-fullscreen-status" + (fullscreenCompletedReply ? " completed" : "")} role="status" aria-live="polite">
                <span className="wbc-browser-fullscreen-status-dot" aria-hidden="true" />
                <span>{fullscreenStatusText}</span>
              </div>
            )}
            <form className="wbc-browser-fullscreen-composer" onSubmit={submitFullscreenChat}>
              <input
                type="text"
                value={fullscreenDraft}
                onChange={function (event) { setFullscreenDraft(event.target.value); }}
                placeholder={running
                  ? wbcT("workbenchChat.browserChatPlaceholderRunning", "Add an instruction…")
                  : wbcT("workbenchChat.browserChatPlaceholder", "Tell Agent what to do in the browser…")}
                aria-label={wbcT("workbenchChat.browserChatInput", "Browser Agent instruction")}
              />
              <button
                type="submit"
                className={running && !fullscreenDraft.trim() ? "stop" : ""}
                disabled={!running && !fullscreenDraft.trim()}
                title={running && !fullscreenDraft.trim()
                  ? wbcT("workbenchChat.stop", "Stop")
                  : (running
                    ? wbcT("workbenchChat.sendGuidance", "Send guidance")
                    : wbcT("workbenchChat.send", "Send"))}
              >
                {running && !fullscreenDraft.trim() ? WBC_ICONS.stop : WBC_ICONS.send}
              </button>
            </form>
          </div>
        )}
      </div>
      {effectiveMode === "pip" && React.createElement(
        window.CyreneUI.require("shell").ColResizer,
        { trackGutter: true, surfaceId: "browser" }
      )}
      {effectiveMode === "pip" && ["n", "s"].map(function (direction) {
        return <div
          key={direction}
          className={"wbc-browser-resize-handle " + direction}
          aria-hidden="true"
          onPointerDown={function (event) { beginInteraction(event, "resize", direction); }}
        />;
      })}
    </section>
  );
  if (effectiveMode === "maximized" && window.ReactDOM && typeof window.ReactDOM.createPortal === "function") {
    // Keep the maximized browser inside the Workbench theme scope. Portaling
    // directly to <body> drops the --wb-* custom properties, which makes the
    // picker background transparent and lets the address bar show through.
    var workbenchPortalRoot = document.querySelector(".workbench-shell") || document.body;
    return window.ReactDOM.createPortal(browserWindow, workbenchPortalRoot);
  }
  return browserWindow;
}

// One stable layout box per transcript entry.  Browser avoidance is applied to
// this wrapper so the existing child alignment stays intact: user bubbles keep
// hugging the lane's right edge and assistant content keeps its left edge.
function wbcNavigationPreview(value) {
  return String(value == null ? "" : value)
    .replace(/```[\s\S]*?```/g, function (block) { return block.replace(/```[^\n]*\n?/g, "").replace(/```/g, ""); })
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/(^|\s)[#>*_`~-]+/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 150);
}

function wbcUserMessageNavigationMeta(message) {
  var msg = message || {};
  var prefix = wbcT("workbenchChat.navigation.you", "You");
  var contentPreview = wbcNavigationPreview(msg.content || "");
  var attachments = Array.isArray(msg.attachments) ? msg.attachments : [];
  var attachmentTypes = [];
  attachments.forEach(function (file) {
    var type = wbcAttachmentTypeLabel(file);
    if (type && attachmentTypes.indexOf(type) === -1) attachmentTypes.push(type);
  });
  var attachmentPreview = attachmentTypes.slice(0, 2).join(" · ");
  if (attachmentTypes.length === 1 && attachments.length > 1) attachmentPreview += " × " + attachments.length;
  if (attachmentTypes.length > 2) attachmentPreview += " · +" + (attachmentTypes.length - 2);
  var preview = contentPreview || attachmentPreview || prefix;
  return {
    role: "user",
    label: contentPreview ? prefix + ": " + preview : preview,
    text: preview,
  };
}

function WbcThreadItem({ children, navigation, className }) {
  var nav = navigation || null;
  return (
    <div
      className={"wbc-thread-item" + (className ? " " + className : "")}
      data-wbc-thread-item="true"
      data-wbc-nav-item={nav ? "true" : undefined}
      data-wbc-nav-role={nav ? nav.role : undefined}
      data-wbc-nav-label={nav ? nav.label : undefined}
      data-wbc-nav-text={nav ? nav.text : undefined}
    >
      {children}
    </div>
  );
}

function wbcShouldStickToConversationBottom(wasSticking, previousScrollTop, scrollTop, scrollHeight, clientHeight) {
  var currentTop = Number(scrollTop) || 0;
  var previousTop = Number(previousScrollTop);
  var hasPreviousTop = previousScrollTop !== null
    && previousScrollTop !== undefined
    && Number.isFinite(previousTop);
  var bottomDistance = Math.max(0, (Number(scrollHeight) || 0) - currentTop - (Number(clientHeight) || 0));
  if (bottomDistance <= 2) return true;
  if (hasPreviousTop && currentTop < previousTop - 1) return false;
  return wasSticking === true;
}

function WbcConversationNavigator({ threadRef, chatId }) {
  var [snapshot, setSnapshot] = useWbcState({
    visible: false,
    active: -1,
    markers: [],
  });

  useWbcEffect(function () {
    var thread = threadRef.current;
    if (!thread) return undefined;
    var raf = 0;
    var itemObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleMeasure)
      : null;
    var observedItems = typeof WeakSet === "function" ? new WeakSet() : null;

    function collectItems() {
      var items = Array.prototype.slice.call(thread.querySelectorAll(":scope > [data-wbc-nav-item='true']"));
      if (itemObserver) {
        items.forEach(function (item) {
          if (observedItems && observedItems.has(item)) return;
          if (observedItems) observedItems.add(item);
          itemObserver.observe(item);
        });
      }
      return items;
    }

    function measure() {
      raf = 0;
      var clientHeight = Math.max(1, thread.clientHeight);
      var items = collectItems();
      var viewportCenter = thread.scrollTop + clientHeight * 0.42;
      var active = -1;
      var activeDistance = Infinity;
      var markers = items.map(function (item, index) {
        var center = item.offsetTop + item.offsetHeight / 2;
        var distance = Math.abs(center - viewportCenter);
        if (distance < activeDistance) {
          activeDistance = distance;
          active = index;
        }
        var role = String(item.dataset.wbcNavRole || "assistant");
        return {
          index: index,
          role: role,
          label: String(item.dataset.wbcNavLabel || ""),
          text: String(item.dataset.wbcNavText || item.dataset.wbcNavLabel || ""),
        };
      });
      setSnapshot({
        visible: markers.length > 5,
        active: active,
        markers: markers,
      });
    }

    function scheduleMeasure() {
      if (document.body.classList.contains("wbc-resizing-side-agent")) return;
      if (raf) return;
      raf = requestAnimationFrame(measure);
    }

    var threadObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleMeasure)
      : null;
    if (threadObserver) threadObserver.observe(thread);
    var mutationObserver = typeof MutationObserver === "function"
      ? new MutationObserver(scheduleMeasure)
      : null;
    if (mutationObserver) mutationObserver.observe(thread, { childList: true, subtree: true, characterData: true });
    thread.addEventListener("scroll", scheduleMeasure, { passive: true });
    window.addEventListener("resize", scheduleMeasure);
    window.addEventListener("workbench:split-resize-end", scheduleMeasure);
    scheduleMeasure();
    return function () {
      if (raf) cancelAnimationFrame(raf);
      if (threadObserver) threadObserver.disconnect();
      if (itemObserver) itemObserver.disconnect();
      if (mutationObserver) mutationObserver.disconnect();
      thread.removeEventListener("scroll", scheduleMeasure);
      window.removeEventListener("resize", scheduleMeasure);
      window.removeEventListener("workbench:split-resize-end", scheduleMeasure);
    };
  }, [threadRef, chatId]);

  function jumpToMarker(index) {
    var thread = threadRef.current;
    if (!thread) return;
    var items = thread.querySelectorAll(":scope > [data-wbc-nav-item='true']");
    var target = items[index];
    if (!target) return;
    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    thread.scrollTo({
      top: Math.max(0, target.offsetTop - 18),
      behavior: reducedMotion ? "auto" : "smooth",
    });
  }

  if (!snapshot.visible) return null;
  return (
    <nav className="wbc-conversation-nav" data-cyrene-revision-volatile="true" aria-label={wbcT("workbenchChat.navigation.label", "Conversation navigation")}>
      <button
        type="button"
        className="wbc-conversation-nav-trigger"
        aria-label={wbcT("workbenchChat.navigation.label", "Conversation navigation")}
      >
        <span /><span /><span /><span /><span />
      </button>
      <div className="wbc-conversation-nav-panel">
        <div className="wbc-conversation-nav-heading">
          <span>{wbcT("workbenchChat.navigation.messages", "Your messages")}</span>
          <span>{snapshot.markers.length}</span>
        </div>
        <div className="wbc-conversation-nav-list">
          {snapshot.markers.map(function (marker) {
            return (
              <button
                type="button"
                key={marker.index}
                className={"wbc-conversation-marker " + marker.role + (marker.index === snapshot.active ? " active" : "")}
                aria-label={wbcT("workbenchChat.navigation.jump", "Jump to: {label}", { label: marker.label })}
                aria-current={marker.index === snapshot.active ? "location" : undefined}
                onClick={function () { jumpToMarker(marker.index); }}
              >
                <span className="wbc-conversation-marker-index">{marker.index + 1}</span>
                <span className="wbc-conversation-marker-text">{marker.text}</span>
              </button>
            );
          })}
        </div>
      </div>
    </nav>
  );
}

function wbcSelectionTextRect(range) {
  if (!range) return null;
  var rects = typeof range.getClientRects === "function" ? range.getClientRects() : null;
  var fragments = [];
  for (var i = 0; rects && i < rects.length; i += 1) {
    var fragment = rects[i];
    if (!fragment || fragment.width <= 0 || fragment.height <= 0) continue;
    fragments.push(fragment);
  }
  if (!fragments.length && typeof range.getBoundingClientRect === "function") {
    var fallback = range.getBoundingClientRect();
    if (fallback && (fallback.width > 0 || fallback.height > 0)) fragments.push(fallback);
  }
  if (!fragments.length) return null;

  var left = fragments[0].left;
  var top = fragments[0].top;
  var right = fragments[0].right;
  var bottom = fragments[0].bottom;
  for (var j = 1; j < fragments.length; j += 1) {
    left = Math.min(left, fragments[j].left);
    top = Math.min(top, fragments[j].top);
    right = Math.max(right, fragments[j].right);
    bottom = Math.max(bottom, fragments[j].bottom);
  }
  return { left: left, top: top, right: right, bottom: bottom, width: right - left, height: bottom - top };
}

function WbcMain({ project, chat, chatSummary, loading, runtime, error, errorKind, onRetry, running, onSend, onGuidance, onInterrupt, onAnswer, onRetryMessage, onRetryClearAnimationEnd, retryClearingMessageIds, retrySuppressedMessageIds, onEditMessage, onAskSelection, sideAgentCreating, onConversationContextMenu, onRename, onDelete, onToTask, toTaskBusy, onOpenFile, onOpenDroppedChat, sideVisible, sidePanelTabExpanded, onToggleSide, browserState, browserSessionId, browserVisible, browserWindowMode, onBrowserMaximize, onBrowserRestore, onBrowserTakeoverComplete, splitOpen, draftAgent, onDraftAgentChange, onSwitchAgent, onOpenAgentDetail, horizontalSessionWheelGesture }) {
  // The lightweight list item already contains every Composer preference.
  // Keep using it while the full transcript hydrates so switching chats never
  // paints a temporary "new chat" Composer with global/default settings.
  var composerChat = chat || chatSummary || null;
  var mainRef = useWbcRef(null);
  var stageRef = useWbcRef(null);
  var scrollRef = useWbcRef(null);
  var selectionMenuRef = useWbcRef(null);
  var stickRef = useWbcRef(true);
  var lastObservedScrollTopRef = useWbcRef(null);
  var [showScrollToBottom, setShowScrollToBottom] = useWbcState(false);
  var [selectionMenu, setSelectionMenu] = useWbcState(null);
  var [chatDropActive, setChatDropActive] = useWbcState(false);
  var [browserSuppressedForSide, setBrowserSuppressedForSide] = useWbcState(false);
  var avoidanceRafRef = useWbcRef(0);
  var stickyRestoreRafRef = useWbcRef(0);
  var traceDisclosureRafRef = useWbcRef(0);
  var avoidanceScrollingRef = useWbcRef(false);
  var avoidanceScrollTimerRef = useWbcRef(null);
  // ResizeObserver reports the height changes caused by our own PiP lane
  // classes. Treating those reports like fresh external layout changes creates
  // a remove/re-add/restore loop which is especially visible at scrollTop=0.
  var avoidanceApplyingRef = useWbcRef(false);
  var avoidanceApplyingRafRef = useWbcRef(0);

  function syncAgentCursorRunning(isRunning) {
    try {
      if (window.CyreneUI.has("uiSurface")) {
        window.CyreneUI.require("uiSurface").setAgentRunning(isRunning === true);
      }
    } catch (error) {}
    var cursorBridge = window.cyrene && window.cyrene.agentCursor;
    if (cursorBridge && typeof cursorBridge.setRunning === "function") {
      cursorBridge.setRunning(isRunning === true).catch(function () {});
    }
  }

  useWbcEffect(function () {
    syncAgentCursorRunning(running === true);
  }, [running]);

  useWbcEffect(function () {
    return function () {
      syncAgentCursorRunning(false);
    };
  }, []);
  var durableMessages = wbcReconcileLiveUserMessages(
    chat && Array.isArray(chat.messages) ? chat.messages : [],
    runtime && runtime.userMessages
  );
  var reasoningStatus = wbcCapabilityStatus(chat, "output", "reasoning");
  var showReasoningPlaceholder = !wbcHasAgentCapabilitySnapshot(chat)
    || reasoningStatus === "supported"
    || reasoningStatus === "degraded";
  var runtimeTimeline = wbcRuntimeSegmentMessages(runtime).concat(wbcRuntimeTimelineMessages(runtime, { showReasoningPlaceholder }));
  var messages = wbcMergeChronologicalMessages(durableMessages, runtimeTimeline);
  var retrySuppressedIds = new Set(Array.isArray(retrySuppressedMessageIds) ? retrySuppressedMessageIds.map(String) : []);
  if (retrySuppressedIds.size) {
    messages = messages.filter(function (message) {
      return !retrySuppressedIds.has(String(message && message.id || ""));
    });
  }
  var retryClearingIds = new Set(Array.isArray(retryClearingMessageIds) ? retryClearingMessageIds.map(String) : []);
  var activityTraceKeys = new Set();
  messages.forEach(function (message) {
    if (!message || !(message.activityCard || message.runtimeActivity)) return;
    var trace = message.runtimeActivity
      ? message.runtimeActivity.progress
      : message.trace;
    var key = wbcTraceDedupeKey(trace);
    if (key) activityTraceKeys.add(key);
  });
  var isLegacy = !!(chat && chat.legacy);
  var latestAssistantReplyId = "";
  var latestAssistantReplyText = "";
  for (var di = durableMessages.length - 1; di >= 0; di--) {
    var durableMessage = durableMessages[di] || {};
    var durableContent = String(durableMessage.content || "").trim();
    if (durableMessage.role !== "assistant" || !durableContent) continue;
    latestAssistantReplyId = String(durableMessage.id || durableMessage.createdAt || ("assistant-" + di));
    latestAssistantReplyText = durableContent;
    break;
  }
  var lastAssistantId = "";
  var lastUserId = "";
  for (var mi = messages.length - 1; mi >= 0; mi--) {
    if (messages[mi].role === "user") {
      if (!lastUserId) lastUserId = String(messages[mi].id || "");
    } else if (!lastAssistantId) {
      lastAssistantId = String(messages[mi].id || "");
    }
    if (lastUserId && lastAssistantId) break;
  }
  var displayMessages = wbcGroupConsecutiveActivityMessages(messages, runtime);

  // Keep the transcript's bottom reserve synchronized with the composer's
  // rendered height when controls, attachments or wrapped content change it.
  useWbcEffect(function () {
    var main = mainRef.current;
    if (!main) return undefined;
    var page = main.closest(".wbc-page");
    var composer = main.querySelector(":scope > .wbc-composer");
    if (!page || !composer) return undefined;
    var resizeRaf = 0;
    var lastHeight = 0;
    function commitComposerReserveHeight() {
      resizeRaf = 0;
      var height = Math.ceil(composer.getBoundingClientRect().height);
      if (height <= 0 || height === lastHeight) return;
      lastHeight = height;
      page.style.setProperty("--wbc-composer-reserve-height", height + "px");
    }
    function scheduleComposerReserveHeight() {
      if (resizeRaf) return;
      resizeRaf = requestAnimationFrame(commitComposerReserveHeight);
    }
    commitComposerReserveHeight();
    var observer = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleComposerReserveHeight)
      : null;
    if (observer) observer.observe(composer);
    window.addEventListener("resize", scheduleComposerReserveHeight);
    return function () {
      if (observer) observer.disconnect();
      window.removeEventListener("resize", scheduleComposerReserveHeight);
      if (resizeRaf) cancelAnimationFrame(resizeRaf);
      page.style.removeProperty("--wbc-composer-reserve-height");
    };
  }, [chat && chat.id]);

  // Expanded side-panel content owns the whole right-side corridor below the
  // conversation card, leaving no room for a floating browser there. Hide the
  // PiP outright while a side tab is open instead of compressing it into a
  // sliver or letting it overlap the panel.
  useWbcEffect(function () {
    setBrowserSuppressedForSide(
      !!(sidePanelTabExpanded && browserVisible && (browserWindowMode === "pip" || browserWindowMode === "minimized"))
    );
  }, [sidePanelTabExpanded, browserVisible, browserWindowMode]);

  var floatingBrowserVisible = browserVisible && !browserSuppressedForSide;

  // The PiP is rendered inside the conversation so it can share the native
  // browser lifecycle, but visually it belongs below the context card. Align
  // it from the actual rendered rectangles instead of reconstructing those
  // coordinates from grid variables: card gutters, transcript insets and
  // density scaling otherwise accumulate into a visible horizontal drift.
  useWbcLayoutEffect(function () {
    if (!sideVisible || !floatingBrowserVisible || browserWindowMode !== "pip") return undefined;
    var main = mainRef.current;
    var page = main && main.closest(".wbc-page");
    var paneCard = main && main.closest(".wbc-pane-card");
    var sideCard = page && page.querySelector(":scope > .wbc-side .wbc-side-card");
    var floating = main && main.querySelector(".wbc-browser-window.pip");
    if (!paneCard || !sideCard || !floating) return undefined;
    var raf = 0;
    var nativeBoundsRaf = 0;
    var nativeBoundsCommitRaf = 0;
    function dispatchNativeBounds() {
      window.dispatchEvent(new CustomEvent("workbench:browser-layout", {
        detail: { source: "pip-context-panel-alignment" },
      }));
    }
    function publishNativeBounds(deferUntilMounted) {
      if (nativeBoundsRaf) cancelAnimationFrame(nativeBoundsRaf);
      if (nativeBoundsCommitRaf) cancelAnimationFrame(nativeBoundsCommitRaf);
      if (!deferUntilMounted) {
        dispatchNativeBounds();
        return;
      }
      // The viewport subscribes from a passive effect. Two paint frames ensure
      // that listener exists and that layout has committed the translated
      // shell before its native surface rectangle is measured.
      nativeBoundsRaf = requestAnimationFrame(function () {
        nativeBoundsRaf = 0;
        nativeBoundsCommitRaf = requestAnimationFrame(function () {
          nativeBoundsCommitRaf = 0;
          dispatchNativeBounds();
        });
      });
    }
    function alignFloatingBrowser(forceNativeSync, immediateNativeSync) {
      raf = 0;
      if (!paneCard.isConnected || !sideCard.isConnected || !floating.isConnected) return;
      var sideRect = sideCard.getBoundingClientRect();
      var paneRect = paneCard.getBoundingClientRect();
      var alignedWidth = Math.round(sideRect.width);
      // The compact browser is a 4:3 card. Width and height must change as one
      // geometry update so the native page never appears stretched mid-drag.
      // The right rail has one strict vertical corridor: 12px below the
      // context panel through the conversation card's bottom edge.
      var maximumHeight = Math.max(0, Math.floor(paneRect.bottom - sideRect.bottom - 12));
      var alignedHeight = Math.min(Math.round(alignedWidth * 3 / 4), maximumHeight);
      var userHeight = parseFloat(floating.style.getPropertyValue("--wbc-browser-pip-user-height")) || 0;
      var userOffsetY = parseFloat(floating.style.getPropertyValue("--wbc-browser-pip-user-offset-y")) || 0;
      if (userHeight > maximumHeight) {
        userHeight = maximumHeight;
        floating.style.setProperty("--wbc-browser-pip-user-height", userHeight + "px");
      }
      // Offset zero is the lowest legal position. The negative bound keeps the
      // title bar exactly 12px below the context card at the highest position.
      var renderedHeight = userHeight || alignedHeight;
      var minimumOffsetY = sideRect.bottom + 12 + renderedHeight - paneRect.bottom;
      var boundedOffsetY = Math.min(0, Math.max(minimumOffsetY, userOffsetY));
      if (boundedOffsetY !== userOffsetY) {
        userOffsetY = boundedOffsetY;
        floating.style.setProperty("--wbc-browser-pip-user-offset-y", userOffsetY + "px");
      }
      var currentWidth = parseFloat(floating.style.getPropertyValue("--wbc-browser-pip-aligned-width")) || 0;
      var currentHeight = parseFloat(floating.style.getPropertyValue("--wbc-browser-pip-aligned-height")) || 0;
      if (currentWidth !== alignedWidth) {
        floating.style.setProperty("--wbc-browser-pip-aligned-width", alignedWidth + "px");
      }
      if (!userHeight && currentHeight !== alignedHeight) {
        floating.style.setProperty("--wbc-browser-pip-aligned-height", alignedHeight + "px");
      }
      var floatingRect = floating.getBoundingClientRect();
      var currentX = parseFloat(floating.style.getPropertyValue("--wbc-browser-pip-align-x")) || 0;
      var currentY = parseFloat(floating.style.getPropertyValue("--wbc-browser-pip-align-y")) || 0;
      var nextX = Math.round(currentX + sideRect.left - floatingRect.left);
      // floatingRect already includes the user's vertical offset. Include that
      // offset in the target baseline so alignment updates do not cancel a
      // manual drag when the panel or window is resized.
      var nextY = Math.round(currentY + paneRect.bottom + userOffsetY - floatingRect.bottom);
      var geometryChanged = currentWidth !== alignedWidth
        || (!userHeight && currentHeight !== alignedHeight)
        || Math.round(currentX) !== nextX
        || Math.round(currentY) !== nextY;
      if (geometryChanged) {
        floating.style.setProperty("--wbc-browser-pip-align-x", nextX + "px");
        floating.style.setProperty("--wbc-browser-pip-align-y", nextY + "px");
      }
      // WebContentsView lives outside the renderer transform tree. Publish one
      // authoritative bounds refresh after the shell moves, otherwise the old
      // native rectangle can cover the PiP title bar. Also force the first
      // mounted frame to sync even when the CSS fallback was already exact.
      if (geometryChanged || forceNativeSync) publishNativeBounds(!immediateNativeSync);
    }
    function scheduleAlignment() {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(function () { alignFloatingBrowser(false); });
    }
    alignFloatingBrowser(true);
    var observer = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleAlignment)
      : null;
    if (observer) {
      observer.observe(sideCard);
      observer.observe(paneCard);
    }
    function onBrowserLayout(event) {
      if (event && event.detail && event.detail.source === "pip-context-panel-alignment") return;
      scheduleAlignment();
    }
    function onRightResize(event) {
      var phase = event && event.detail && event.detail.phase;
      // The panel width has already been written when this event fires. A
      // synchronous rect read flushes that style and keeps the PiP attached to
      // the pointer instead of waiting for ResizeObserver's next delivery.
      alignFloatingBrowser(phase === "end", true);
    }
    window.addEventListener("workbench:browser-layout", onBrowserLayout);
    window.addEventListener("workbench:right-resize", onRightResize);
    window.addEventListener("resize", scheduleAlignment);
    return function () {
      if (raf) cancelAnimationFrame(raf);
      if (nativeBoundsRaf) cancelAnimationFrame(nativeBoundsRaf);
      if (nativeBoundsCommitRaf) cancelAnimationFrame(nativeBoundsCommitRaf);
      if (observer) observer.disconnect();
      window.removeEventListener("workbench:browser-layout", onBrowserLayout);
      window.removeEventListener("workbench:right-resize", onRightResize);
      window.removeEventListener("resize", scheduleAlignment);
      floating.style.removeProperty("--wbc-browser-pip-aligned-width");
      floating.style.removeProperty("--wbc-browser-pip-aligned-height");
      floating.style.removeProperty("--wbc-browser-pip-align-x");
      floating.style.removeProperty("--wbc-browser-pip-align-y");
    };
  }, [sideVisible, floatingBrowserVisible, browserWindowMode, chat && chat.id]);

  // Content can finish reflowing one frame after a message/ PiP resize. A
  // scrollHeight change does not emit a scroll event, so the synchronous
  // bottom restoration alone can still leave the live tail a few pixels above
  // the real bottom. Re-assert it after layout settles, but only while the
  // reader has not intentionally left the live tail.
  var scheduleStickyViewportRestore = useWbcCallback(function () {
    if (!stickRef.current || stickyRestoreRafRef.current) return;
    stickyRestoreRafRef.current = requestAnimationFrame(function () {
      stickyRestoreRafRef.current = 0;
      var thread = scrollRef.current;
      if (!thread || !stickRef.current) return;
      thread.scrollTop = thread.scrollHeight;
      setShowScrollToBottom(false);
    });
  }, []);

  var applyBrowserAvoidance = useWbcCallback(function (preserveViewport) {
    var stage = stageRef.current;
    var thread = scrollRef.current;
    if (!stage || !thread) return;
    var items = Array.prototype.slice.call(thread.querySelectorAll(":scope > [data-wbc-thread-item]"));
    if (!items.length) return;
    avoidanceApplyingRef.current = true;
    if (avoidanceApplyingRafRef.current) cancelAnimationFrame(avoidanceApplyingRafRef.current);
    avoidanceApplyingRafRef.current = requestAnimationFrame(function () {
      avoidanceApplyingRafRef.current = 0;
      avoidanceApplyingRef.current = false;
    });

    // Preserve the reader's visual anchor across text reflow.  At the live tail
    // the bottom is the anchor; in scrollback it is the first visible entry.
    var anchorNode = null;
    var anchorOffset = 0;
    if (preserveViewport && !stickRef.current) {
      var anchorTarget = thread.scrollTop;
      var anchorLow = 0, anchorHigh = items.length;
      while (anchorLow < anchorHigh) {
        var anchorMid = Math.floor((anchorLow + anchorHigh) / 2);
        var anchorItem = items[anchorMid];
        if (anchorItem.offsetTop + anchorItem.offsetHeight <= anchorTarget) anchorLow = anchorMid + 1;
        else anchorHigh = anchorMid;
      }
      anchorNode = items[Math.min(anchorLow, items.length - 1)] || null;
      if (anchorNode) anchorOffset = anchorNode.offsetTop - thread.scrollTop;
    }

    function restoreViewport() {
      if (!preserveViewport) return;
      if (stickRef.current) {
        thread.scrollTop = thread.scrollHeight;
      } else if (anchorNode && anchorNode.isConnected) {
        thread.scrollTop = Math.max(0, anchorNode.offsetTop - anchorOffset);
      }
    }

    items.forEach(function (item) {
      item.classList.remove("wbc-browser-avoid-left", "wbc-browser-avoid-right");
      item.style.removeProperty("--wbc-browser-avoid-start");
      item.style.removeProperty("--wbc-browser-avoid-end");
    });
    restoreViewport();

    var browserWindow = stage.querySelector(".wbc-browser-window.pip")
      || stage.querySelector(".wbc-browser-restore-float");
    if (!browserWindow) return;
    var browserRect = browserWindow.getBoundingClientRect();
    var threadRect = thread.getBoundingClientRect();
    var threadStyles = getComputedStyle(thread);
    var paddingLeft = parseFloat(threadStyles.paddingLeft) || 0;
    var paddingRight = parseFloat(threadStyles.paddingRight) || 0;
    var areaLeft = threadRect.left + paddingLeft;
    var areaWidth = Math.max(0, thread.clientWidth - paddingLeft - paddingRight);
    var gap = 14;
    var plan = wbcBrowserAvoidancePlan(areaLeft, areaWidth, browserRect.left, browserRect.width, gap);
    if (!plan) return;

    // Adding a lane can make a long entry taller and move later entries under
    // the fixed PiP.  Grow the avoided set monotonically for a few cheap passes
    // until no newly intersecting entry appears; never remove one mid-pass.
    for (var pass = 0; pass < 5; pass++) {
      var contentTop = thread.scrollTop + browserRect.top - threadRect.top - gap;
      var contentBottom = thread.scrollTop + browserRect.bottom - threadRect.top + gap;
      var low = 0, high = items.length;
      while (low < high) {
        var mid = Math.floor((low + high) / 2);
        var candidate = items[mid];
        if (candidate.offsetTop + candidate.offsetHeight <= contentTop) low = mid + 1;
        else high = mid;
      }
      var changed = false;
      for (var index = low; index < items.length; index++) {
        var item = items[index];
        if (item.offsetTop >= contentBottom) break;
        var expectedClass = plan.side === "left" ? "wbc-browser-avoid-left" : "wbc-browser-avoid-right";
        if (item.classList.contains(expectedClass)) continue;
        item.classList.add(expectedClass);
        item.style.setProperty("--wbc-browser-avoid-start", Math.round(plan.start) + "px");
        item.style.setProperty("--wbc-browser-avoid-end", Math.round(plan.end) + "px");
        changed = true;
      }
      if (!changed) break;
      restoreViewport();
    }
    scheduleStickyViewportRestore();
  }, [scheduleStickyViewportRestore]);

  var scheduleBrowserAvoidance = useWbcCallback(function () {
    if (avoidanceRafRef.current) return;
    avoidanceRafRef.current = requestAnimationFrame(function () {
      avoidanceRafRef.current = 0;
      // Width changes while a wheel/trackpad gesture is active alter message
      // heights and fight the browser's scroll position. Keep the current lane
      // assignment stable until the gesture settles, then recompute once. Once
      // it has settled, preserve either the live-tail bottom or the reader's
      // first visible entry because the narrower lane can increase row height.
      if (avoidanceScrollingRef.current) return;
      applyBrowserAvoidance(true);
    });
  }, [applyBrowserAvoidance]);

  // Moving toward older messages immediately releases the live-tail anchor,
  // even within the small bottom tolerance. Re-enable it only at the bottom.
  function onScroll() {
    var el = scrollRef.current;
    if (!el) return;
    stickRef.current = wbcShouldStickToConversationBottom(
      stickRef.current,
      lastObservedScrollTopRef.current,
      el.scrollTop,
      el.scrollHeight,
      el.clientHeight
    );
    lastObservedScrollTopRef.current = el.scrollTop;
    setShowScrollToBottom(!stickRef.current);
    if (!stickRef.current && stickyRestoreRafRef.current) {
      cancelAnimationFrame(stickyRestoreRafRef.current);
      stickyRestoreRafRef.current = 0;
    }
    // A wheel/trackpad gesture owns both scrollTop and the visible message
    // anchor. Do not change avoided message widths during the gesture: their
    // height reflow would make the transcript jump in the opposite direction.
    avoidanceScrollingRef.current = true;
    if (avoidanceScrollTimerRef.current) clearTimeout(avoidanceScrollTimerRef.current);
    avoidanceScrollTimerRef.current = setTimeout(function () {
      avoidanceScrollTimerRef.current = null;
      avoidanceScrollingRef.current = false;
      scheduleBrowserAvoidance();
    }, 120);
  }

  useWbcEffect(function () {
    var el = scrollRef.current;
    if (el && stickRef.current) {
      el.scrollTop = el.scrollHeight;
      scheduleStickyViewportRestore();
    }
  }, [messages.length, runtime && runtime.text, runtime && runtime.progress.length, runtime && runtime.activities && runtime.activities.length, runtime && runtime.segments && runtime.segments.length]);

  useWbcEffect(function () {
    stickRef.current = true;
    setShowScrollToBottom(false);
    var el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
      lastObservedScrollTopRef.current = el.scrollTop;
    }
    scheduleStickyViewportRestore();
    scheduleBrowserAvoidance();
  }, [chat && chat.id]);

  useWbcEffect(function () {
    var stage = stageRef.current;
    var thread = scrollRef.current;
    if (!stage || !thread) return undefined;
    var observedItems = typeof WeakSet === "function" ? new WeakSet() : null;
    var itemObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(function () {
          if (avoidanceApplyingRef.current) return;
          if (document.body.classList.contains("wbc-resizing-side-agent")) return;
          scheduleStickyViewportRestore();
          scheduleBrowserAvoidance();
        })
      : null;
    function observeItems() {
      if (!itemObserver) return;
      thread.querySelectorAll(":scope > [data-wbc-thread-item]").forEach(function (item) {
        if (observedItems && observedItems.has(item)) return;
        if (observedItems) observedItems.add(item);
        itemObserver.observe(item);
      });
    }
    observeItems();
    var stageObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(function () {
          if (avoidanceApplyingRef.current) return;
          if (document.body.classList.contains("wbc-resizing-side-agent")) return;
          scheduleStickyViewportRestore();
          scheduleBrowserAvoidance();
        })
      : null;
    if (stageObserver) stageObserver.observe(stage);
    var mutationObserver = typeof MutationObserver === "function"
      ? new MutationObserver(function () {
          observeItems();
          scheduleStickyViewportRestore();
          scheduleBrowserAvoidance();
        })
      : null;
    if (mutationObserver) mutationObserver.observe(thread, { childList: true, subtree: true, characterData: true });
    function preserveTraceDisclosureAnchor(event) {
      var detail = event && event.detail || {};
      var anchor = detail.anchor;
      if (!anchor || !anchor.isConnected) return;
      var anchorTop = anchor.getBoundingClientRect().top;
      // Expanding a disclosure is an explicit reading action. Release the
      // live-tail lock before ResizeObserver sees the taller row; otherwise
      // sticky-bottom restoration scrolls to the new bottom and makes the
      // details look as though they opened upward above the clicked summary.
      if (detail.expanding) {
        stickRef.current = false;
        setShowScrollToBottom(true);
      }
      if (stickyRestoreRafRef.current) {
        cancelAnimationFrame(stickyRestoreRafRef.current);
        stickyRestoreRafRef.current = 0;
      }
      if (traceDisclosureRafRef.current) cancelAnimationFrame(traceDisclosureRafRef.current);
      traceDisclosureRafRef.current = requestAnimationFrame(function () {
        traceDisclosureRafRef.current = requestAnimationFrame(function () {
          traceDisclosureRafRef.current = 0;
          if (!anchor.isConnected) return;
          thread.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
          lastObservedScrollTopRef.current = thread.scrollTop;
        });
      });
    }
    thread.addEventListener("workbench:trace-disclosure", preserveTraceDisclosureAnchor);
    window.addEventListener("workbench:browser-layout", scheduleBrowserAvoidance);
    window.addEventListener("resize", scheduleBrowserAvoidance);
    scheduleBrowserAvoidance();
    return function () {
      if (avoidanceRafRef.current) cancelAnimationFrame(avoidanceRafRef.current);
      avoidanceRafRef.current = 0;
      if (avoidanceApplyingRafRef.current) cancelAnimationFrame(avoidanceApplyingRafRef.current);
      avoidanceApplyingRafRef.current = 0;
      avoidanceApplyingRef.current = false;
      if (stickyRestoreRafRef.current) cancelAnimationFrame(stickyRestoreRafRef.current);
      stickyRestoreRafRef.current = 0;
      avoidanceScrollingRef.current = false;
      if (avoidanceScrollTimerRef.current) clearTimeout(avoidanceScrollTimerRef.current);
      avoidanceScrollTimerRef.current = null;
      if (traceDisclosureRafRef.current) cancelAnimationFrame(traceDisclosureRafRef.current);
      traceDisclosureRafRef.current = 0;
      if (itemObserver) itemObserver.disconnect();
      if (stageObserver) stageObserver.disconnect();
      if (mutationObserver) mutationObserver.disconnect();
      thread.removeEventListener("workbench:trace-disclosure", preserveTraceDisclosureAnchor);
      window.removeEventListener("workbench:browser-layout", scheduleBrowserAvoidance);
      window.removeEventListener("resize", scheduleBrowserAvoidance);
    };
  }, [scheduleBrowserAvoidance, scheduleStickyViewportRestore, project && project.id]);

  useWbcEffect(function () {
    scheduleBrowserAvoidance();
  }, [messages.length, runtime && runtime.text, runtime && runtime.progress && runtime.progress.length, runtime && runtime.activities && runtime.activities.length, browserVisible, browserWindowMode, sideVisible]);

  useWbcEffect(function () {
    var thread = scrollRef.current;
    if (!thread || !onAskSelection || isLegacy) {
      setSelectionMenu(null);
      return undefined;
    }

    function readSelection() {
      var selection = window.getSelection && window.getSelection();
      if (!selection || selection.isCollapsed || !selection.rangeCount) {
        setSelectionMenu(null);
        return;
      }
      if (
        !selection.anchorNode
        || !selection.focusNode
        || !thread.contains(selection.anchorNode)
        || !thread.contains(selection.focusNode)
      ) {
        setSelectionMenu(null);
        return;
      }
      var text = String(selection.toString() || "").trim().slice(0, 12000);
      if (!text) {
        setSelectionMenu(null);
        return;
      }
      var rect = wbcSelectionTextRect(selection.getRangeAt(0));
      if (!rect) {
        setSelectionMenu(null);
        return;
      }
      setSelectionMenu({
        text: text,
        left: Math.max(92, Math.min(window.innerWidth - 92, rect.left + rect.width / 2)),
        top: rect.bottom + 10,
        placement: "below",
      });
    }

    function handlePointerUp(event) {
      if (selectionMenuRef.current && selectionMenuRef.current.contains(event.target)) return;
      window.setTimeout(readSelection, 0);
    }
    function handleKeyUp(event) {
      if (event.key === "Escape") {
        setSelectionMenu(null);
        return;
      }
      if (event.shiftKey || event.key.indexOf("Arrow") >= 0) {
        window.setTimeout(readSelection, 0);
      }
    }
    function closeOutside(event) {
      if (selectionMenuRef.current && selectionMenuRef.current.contains(event.target)) return;
      setSelectionMenu(null);
    }
    function closeMenu() { setSelectionMenu(null); }

    thread.addEventListener("pointerup", handlePointerUp);
    thread.addEventListener("keyup", handleKeyUp);
    thread.addEventListener("scroll", closeMenu, { passive: true });
    document.addEventListener("pointerdown", closeOutside, true);
    window.addEventListener("resize", closeMenu);
    return function () {
      thread.removeEventListener("pointerup", handlePointerUp);
      thread.removeEventListener("keyup", handleKeyUp);
      thread.removeEventListener("scroll", closeMenu);
      document.removeEventListener("pointerdown", closeOutside, true);
      window.removeEventListener("resize", closeMenu);
    };
  }, [chat && chat.id, onAskSelection, isLegacy]);

  function askAboutSelection() {
    if (!selectionMenu || sideAgentCreating) return;
    var selectedText = selectionMenu.text;
    setSelectionMenu(null);
    var selection = window.getSelection && window.getSelection();
    if (selection) selection.removeAllRanges();
    onAskSelection(selectedText);
  }

  function scrollToConversationBottom() {
    var el = scrollRef.current;
    if (!el) return;
    stickRef.current = true;
    setShowScrollToBottom(false);
    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    lastObservedScrollTopRef.current = el.scrollTop;
    el.scrollTo({ top: el.scrollHeight, behavior: reducedMotion ? "auto" : "smooth" });
    scheduleStickyViewportRestore();
  }

  function handleChatDragEnter(event) {
    if (!wbcHasChatDrag(event)) return;
    event.preventDefault();
    setChatDropActive(true);
  }

  function handleChatDragOver(event) {
    if (!wbcHasChatDrag(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    setChatDropActive(true);
  }

  function handleChatDragLeave(event) {
    if (event.currentTarget.contains(event.relatedTarget)) return;
    setChatDropActive(false);
  }

  function handleChatDrop(event) {
    if (!wbcHasChatDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    setChatDropActive(false);
    var payload = wbcReadChatDrag(event);
    if (payload && onOpenDroppedChat) onOpenDroppedChat(payload.id);
  }

  function handleConversationHorizontalWheel(event) {
    if (event.target && event.target.closest && event.target.closest(
      "pre, .wbc-table-wrap, .wbc-browser-window, input, textarea, select"
    )) return;
    wbcHandleHorizontalWheelGesture(
      event,
      horizontalSessionWheelGesture,
      wbcCycleTopbarSessionTab
    );
  }

  var selectionMenuPortal = selectionMenu && typeof ReactDOM !== "undefined" && document.body
    ? ReactDOM.createPortal((
      <div
        ref={selectionMenuRef}
        className={"wbc-selection-menu " + selectionMenu.placement}
        style={{ left: selectionMenu.left + "px", top: selectionMenu.top + "px" }}
        role="toolbar"
        aria-label={wbcT("workbenchChat.selection.actions", "Selection actions")}
      >
        <button
          type="button"
          onMouseDown={function (event) { event.preventDefault(); }}
          onClick={askAboutSelection}
          disabled={sideAgentCreating}
        >
          <span aria-hidden="true">{WBC_ICONS.chat}</span>
          <span>{sideAgentCreating
            ? wbcT("workbenchChat.sideAgent.creating", "Creating…")
            : wbcT("workbenchChat.selection.askInSidebar", "Ask in sidebar")}</span>
        </button>
      </div>
    ), document.body)
    : null;

  if (!project) {
    return <main className="wbc-main"><div className="workbench-empty">{wbcT("workbenchChat.noProject", "Select a project first.")}</div></main>;
  }

  return (
    <main
      ref={mainRef}
      className={"wbc-main" + (chatDropActive ? " chat-drop-active" : "")}
      onDragEnter={handleChatDragEnter}
      onDragOver={handleChatDragOver}
      onDragLeave={handleChatDragLeave}
      onDrop={handleChatDrop}
    >
      {chatDropActive && (
        <div className="wbc-chat-open-drop-hint" role="status">
          {wbcT("workbenchChat.dropToOpen", "Release to open this conversation")}
        </div>
      )}
      {error && <WbcErrorNotice message={error} kind={errorKind} onRetry={onRetry} />}
      <div
        className={"wbc-thread-stage" + (browserWindowMode === "maximized" ? " browser-window-maximized" : "")}
        ref={stageRef}
      >
      <div
        className="wbc-thread"
        data-cyrene-revision-volatile="true"
        ref={scrollRef}
        onScroll={onScroll}
        onWheel={handleConversationHorizontalWheel}
        onAnimationEnd={function (event) {
          if (event.animationName === "wbc-retry-output-clear" && onRetryClearAnimationEnd) {
            onRetryClearAnimationEnd();
          }
        }}
        onContextMenu={onConversationContextMenu}
      >
        {loading && !chat && (
          <div className="wbc-empty-thread wbc-loading-thread" role="status">
            <span className="wbc-spinner" aria-hidden="true"></span>
            <b>{wbcT("workbenchChat.loadingConversation", "Loading conversation…")}</b>
          </div>
        )}
        {messages.length === 0 && !runtime && !loading && !error && (
          <div className="wbc-empty-thread">
            <div className="wbc-empty-icon">{WBC_ICONS.chat}</div>
            <b>{wbcT("workbenchChat.emptyTitle", "Start a new chat")}</b>
            <p>{wbcT("workbenchChat.emptyBody", "Chats are bound to the current workspace. The agent can read project context, and work can be converted into a task when needed.")}</p>
          </div>
        )}
        {displayMessages.map(function (msg) {
          var retryClearing = msg && msg.activityGroup
            ? msg.activities.some(function (activityMessage) {
                return retryClearingIds.has(String(activityMessage && activityMessage.id || ""));
              })
            : retryClearingIds.has(String(msg && msg.id || ""));
          if (msg.activityGroup) {
            return (
              <WbcThreadItem key={msg.id} className={retryClearing ? "retry-clearing" : ""}>
                <WbcActivityGroup group={msg} />
              </WbcThreadItem>
            );
          }
          var canRetryAssistant = !isLegacy && !running && String(msg.id || "") === lastAssistantId;
          var canRetryUser = !isLegacy && !running && msg.role === "user" && String(msg.id || "") === lastUserId;
          var canEdit = !isLegacy
            && !running
            && msg.role === "user"
            && !!onEditMessage
            && (!wbcChatAgent(chat) || wbcIsBuiltinAgent(wbcChatAgent(chat)) || wbcCapabilityEnabled(chat, "session", "fork", { strictUnknown: true }));
          var isActiveQuestion = !!(
            msg.questionPrompt
            && chat.pendingQuestion
            && String(chat.pendingQuestion.id || "") === String(msg.questionId || "")
          );
          if (msg.runtimeHeartbeat) {
            return null;
          }
          if (msg.modelStatusCard) {
            return <WbcThreadItem key={msg.id} className={retryClearing ? "retry-clearing" : ""}><WbcModelStatusMessage msg={msg} /></WbcThreadItem>;
          }
          if (msg.runtimeNotification || msg.notificationCard) {
            return <WbcThreadItem key={msg.id} className={retryClearing ? "retry-clearing" : ""}><WbcAgentNotification notice={msg.notification} /></WbcThreadItem>;
          }
          if (msg.runtimeActivity || msg.activityCard) {
            var activity = msg.runtimeActivity || {
              id: msg.id,
              reasoning: msg.reasoning || "",
              progress: Array.isArray(msg.trace) ? msg.trace : [],
            };
            var activityEntries = Array.isArray(activity.progress) ? activity.progress : [];
            // A tool-free thinking card is useful only while it is the live
            // phase. Once completed, omit it instead of leaving a durable
            // "thinking complete" placeholder between messages.
            if (
              !msg.runtimeActivityActive
              && activityEntries.length === 0
              && !String(activity.reasoning || "").trim()
            ) return null;
            return (
              <WbcThreadItem key={msg.id} className={retryClearing ? "retry-clearing" : ""}>
                <WbcLiveActivityCard
                  activity={activity}
                  active={!!msg.runtimeActivityActive}
                  hasReplyText={!!msg.runtimeActivityHasReplyText}
                  live={!!msg.runtimeActivity}
                />
              </WbcThreadItem>
            );
          }
          if (isActiveQuestion) {
            return <WbcThreadItem key={msg.id} className={retryClearing ? "retry-clearing" : ""}><WbcQuestionPrompt pending={chat.pendingQuestion} onAnswer={onAnswer} busy={running} trace={msg.trace} /></WbcThreadItem>;
          }
          var messageTraceKey = wbcTraceDedupeKey(msg.trace);
          var visibleMessage = messageTraceKey && activityTraceKeys.has(messageTraceKey)
            ? { ...msg, trace: [] }
            : msg;
          return (
            <WbcThreadItem key={msg.id} navigation={msg.role === "user" ? wbcUserMessageNavigationMeta(msg) : null} className={retryClearing ? "retry-clearing" : ""}>
              {msg.role === "user"
                ? <WbcUserMessage msg={visibleMessage} onOpenFile={onOpenFile} onEditMessage={onEditMessage} canEdit={canEdit} onRetryMessage={canRetryUser ? onRetryMessage : null} />
                : <WbcAssistantMessage msg={visibleMessage} onOpenFile={onOpenFile} onRetryMessage={canRetryAssistant ? onRetryMessage : null} chatId={String(chat && chat.id || "")} />}
            </WbcThreadItem>
          );
        })}
        {runtime && (runtime.text || (runtime.artifacts && runtime.artifacts.length)) && <WbcThreadItem><WbcLiveMessage runtime={runtime} onOpenFile={onOpenFile} /></WbcThreadItem>}
        {chat && chat.pendingQuestion && chat.pendingQuestion.id && (!runtime || wbcIsLiveAgentRequest(chat.pendingQuestion)) && !messages.some(function (msg) {
          return msg.questionPrompt && String(msg.questionId || "") === String(chat.pendingQuestion.id || "");
        }) && (
          <WbcThreadItem><WbcQuestionPrompt pending={chat.pendingQuestion} onAnswer={onAnswer} busy={running && !wbcIsLiveAgentRequest(chat.pendingQuestion)} /></WbcThreadItem>
        )}
      </div>
      <WbcConversationNavigator threadRef={scrollRef} chatId={chat && chat.id} />
      {selectionMenuPortal}
      <div className="wbc-browser-movement-region">
        <WbcBrowserFloatingSurface
          browserState={browserState}
          browserSessionId={browserSessionId}
          visible={floatingBrowserVisible}
          mode={browserWindowMode}
          composerDocked={!sideVisible}
          runtime={runtime}
          running={running}
          latestAssistantReplyId={latestAssistantReplyId}
          latestAssistantReplyText={latestAssistantReplyText}
          onSend={onSend}
          onGuidance={onGuidance}
          onInterrupt={onInterrupt}
          onMaximize={onBrowserMaximize}
          onRestore={onBrowserRestore}
          onTakeoverComplete={onBrowserTakeoverComplete}
        />
      </div>
      </div>
      <WbcComposer
        key={"main-composer:" + String(composerChat && composerChat.id || "new")}
        chat={composerChat}
        project={project}
        runtime={runtime}
        running={running}
        error={error}
        errorKind={errorKind}
        topOverlay={showScrollToBottom ? (
          <button
            type="button"
            className="wbc-scroll-to-bottom"
            onClick={scrollToConversationBottom}
            title={wbcT("workbenchChat.navigation.backToBottom", "Back to latest message")}
            aria-label={wbcT("workbenchChat.navigation.backToBottom", "Back to latest message")}
          >
            <span aria-hidden="true">{WBC_ICONS.chevronsRight}</span>
          </button>
        ) : null}
        onSend={onSend}
        onGuidance={onGuidance}
        onInterrupt={onInterrupt}
        draftAgent={draftAgent}
        onDraftAgentChange={onDraftAgentChange}
        onSwitchAgent={onSwitchAgent}
        onOpenAgentDetail={onOpenAgentDetail}
      />
    </main>
  );
}

function wbcQuestionOptionValue(option) {
  if (typeof option === "string") return option;
  // Agent-defined permission options submit their original option id
  // (handoff §11.1); never re-map by localized label or index. Legacy string
  // options and label-only objects keep their historical behavior.
  return String(
    option
    && (
      option.optionId
      || option.id
      || option.label
      || option.title
      || option.value
      || option.description
    )
    || ""
  ).trim();
}

function wbcIsLiveAgentRequest(pending) {
  var kind = String(pending && pending.kind || "");
  return kind === "permission.requested" || kind === "elicitation.requested";
}

function wbcPermissionOptionLabel(option, index, total) {
  return permissionOptionLabel(option, index, total, function (key, fallback) {
    return wbcT(key, fallback);
  });
}

function wbcPermissionQuestionText(pending) {
  var i18n = window.CyreneUI.require("i18n");
  return i18n.permissionQuestionText(pending, i18n.getLang());
}

function wbcVoiceQuestionText(pending) {
  var question = pending && typeof pending === "object" ? pending : {};
  var kind = String(question.kind || "");
  var isPermission = kind === "permission.requested"
    || window.CyreneUI.require("model").isPermissionQuestionKind(kind);
  var text = String(isPermission
    ? wbcPermissionQuestionText(question)
    : (question.text || wbcT("workbenchChat.questionFallback", "Agent needs your confirmation to continue."))).trim();
  var options = Array.isArray(question.options) ? question.options : [];
  var optionText = options.map(function (option, index) {
    if (isPermission) return wbcPermissionOptionLabel(option, index, options.length);
    return wbcQuestionOptionValue(option);
  }).filter(Boolean).join("。 ");
  return [text, optionText].filter(Boolean).join("。 ");
}

function wbcElicitationFields(pending) {
  var question = pending && typeof pending === "object" ? pending : {};
  var schema = question.schema && typeof question.schema === "object" ? question.schema : {};
  var properties = schema.properties && typeof schema.properties === "object" ? schema.properties : {};
  var required = new Set(Array.isArray(schema.required) ? schema.required.map(String) : []);
  var fields = [];
  Object.keys(properties).slice(0, 50).forEach(function (name) {
    var item = properties[name];
    if (!item || typeof item !== "object") return;
    var type = Array.isArray(item.type) ? item.type.find(function (value) { return value !== "null"; }) : item.type;
    type = String(type || (Array.isArray(item.enum) ? "string" : "string"));
    if (["string", "number", "integer", "boolean"].indexOf(type) < 0 && !Array.isArray(item.enum)) return;
    var enumNames = Array.isArray(item.enumNames) ? item.enumNames : (Array.isArray(item["x-enumNames"]) ? item["x-enumNames"] : []);
    fields.push({
      name: String(name),
      title: String(item.title || name),
      description: String(item.description || ""),
      type: type,
      inputType: type === "number" || type === "integer" ? "number" : (String(item.format || "") === "password" ? "password" : "text"),
      required: required.has(String(name)),
      defaultValue: item.default,
      placeholder: String(item.placeholder || item.examples && item.examples[0] || ""),
      minimum: item.minimum,
      maximum: item.maximum,
      minLength: item.minLength,
      maxLength: item.maxLength,
      enumValues: (Array.isArray(item.enum) ? item.enum : []).map(function (value, index) {
        return { value: value, label: String(enumNames[index] != null ? enumNames[index] : value) };
      }),
    });
  });
  (Array.isArray(question.fields) ? question.fields : []).slice(0, 50).forEach(function (item, index) {
    if (!item || typeof item !== "object") return;
    var name = String(item.name || item.id || ("field_" + index));
    if (fields.some(function (field) { return field.name === name; })) return;
    var choices = Array.isArray(item.options) ? item.options : (Array.isArray(item.enum) ? item.enum : []);
    var type = String(item.type || (choices.length ? "string" : "string")).toLowerCase();
    fields.push({
      name: name,
      title: String(item.label || item.title || name),
      description: String(item.description || item.help || ""),
      type: type,
      inputType: type === "number" || type === "integer" ? "number" : (type === "password" ? "password" : "text"),
      required: item.required === true,
      defaultValue: item.defaultValue != null ? item.defaultValue : item.default,
      placeholder: String(item.placeholder || ""),
      minimum: item.minimum,
      maximum: item.maximum,
      minLength: item.minLength,
      maxLength: item.maxLength,
      enumValues: choices.map(function (choice) {
        if (choice && typeof choice === "object") return { value: choice.value != null ? choice.value : choice.id, label: String(choice.label || choice.name || choice.value || choice.id || "") };
        return { value: choice, label: String(choice) };
      }),
    });
  });
  return fields;
}

function wbcElicitationInitialValues(fields) {
  var values = {};
  (fields || []).forEach(function (field) {
    if (field.defaultValue != null) values[field.name] = field.defaultValue;
    else if (field.type === "boolean") values[field.name] = false;
    else if (field.required && field.enumValues.length) values[field.name] = field.enumValues[0].value;
    else values[field.name] = "";
  });
  return values;
}

function wbcValidateElicitationForm(fields, values) {
  var errors = {};
  var normalized = {};
  (fields || []).forEach(function (field) {
    var raw = values[field.name];
    var empty = raw == null || raw === "";
    if (field.required && empty) {
      errors[field.name] = wbcT("workbenchChat.elicitationRequired", "This field is required.");
      return;
    }
    if (empty && !field.required) return;
    if (field.type === "number" || field.type === "integer") {
      var numeric = Number(raw);
      if (!Number.isFinite(numeric) || (field.type === "integer" && !Number.isInteger(numeric))) {
        errors[field.name] = field.type === "integer" ? wbcT("workbenchChat.elicitationInteger", "Enter a whole number.") : wbcT("workbenchChat.elicitationNumber", "Enter a valid number.");
        return;
      }
      if (field.minimum != null && numeric < Number(field.minimum)) errors[field.name] = wbcT("workbenchChat.elicitationMinimum", "Value must be at least {value}.", { value: field.minimum });
      if (field.maximum != null && numeric > Number(field.maximum)) errors[field.name] = wbcT("workbenchChat.elicitationMaximum", "Value must be at most {value}.", { value: field.maximum });
      normalized[field.name] = numeric;
      return;
    }
    var text = String(raw);
    if (field.minLength != null && text.length < Number(field.minLength)) errors[field.name] = wbcT("workbenchChat.elicitationMinLength", "Enter at least {value} characters.", { value: field.minLength });
    if (field.maxLength != null && text.length > Number(field.maxLength)) errors[field.name] = wbcT("workbenchChat.elicitationMaxLength", "Enter no more than {value} characters.", { value: field.maxLength });
    normalized[field.name] = field.type === "boolean" ? raw === true : raw;
  });
  return { valid: Object.keys(errors).length === 0, errors: errors, values: normalized };
}

// A paused chat run awaiting the user's answer to a permission elevation or a
// clarification (ask_user). Renders the question + option buttons inline at the
// bottom of the thread; each answer resumes the same round server-side.

export { WbcMain, WbcThreadItem, wbcElicitationFields, wbcElicitationInitialValues, wbcIsLiveAgentRequest, wbcPermissionOptionLabel, wbcPermissionQuestionText, wbcQuestionOptionValue, wbcValidateElicitationForm, wbcVoiceQuestionText }
