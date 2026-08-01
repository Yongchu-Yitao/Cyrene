// Cyrene UI — live browser viewport (M2 screencast + S3 user control)
// Renders the agent's browser screencast (via the /ws/browser WebSocket) plus a
// ribbon describing the latest action. The user can TAKE CONTROL of the live
// view: mouse/keyboard events are forwarded over the same socket and injected
// into the headless page via CDP, so the user drives the very session the agent
// uses — no native window needed. For sites that fingerprint headless (e.g.
// CAPTCHA) a native-window escape hatch (/api/browser/takeover) is offered.
// The agent-initiated login takeover card (M3, browser_request_takeover) still
// appears when the backend emits browser_takeover_request.

function BrowserViewportPanel(props) {
  if (window.cyrene && window.cyrene.browser) {
    return React.createElement(ElectronBrowserViewportPanel, props);
  }
  return React.createElement(ScreencastBrowserViewportPanel, props);
}

function browserErrorText(err, key, fallback) {
  var raw = String((err && err.message) || err || "").trim();
  if (raw) {
    var fallbackKeys = {
      "browser action failed": "browser.error.actionFailed",
      "capture failed": "browser.error.captureFailed",
      "browser unavailable": "browser.error.unavailable",
    };
    if (fallbackKeys[raw.toLowerCase()]) {
      key = fallbackKeys[raw.toLowerCase()];
      raw = "";
    }
  }
  if (raw) {
    try {
      var api = window.CyreneUI.require("api");
      if (api && typeof api.errorText === "function") return api.errorText(err);
    } catch (e) {}
    return raw;
  }
  try {
    return window.CyreneUI.require("i18n").t(key, null, fallback);
  } catch (e) {
    return fallback;
  }
}

function BrowserIcon({ name, size }) {
  size = size || 16;
  var common = {
    viewBox: "0 0 24 24",
    width: size,
    height: size,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": "true",
  };
  if (name === "back") return <svg {...common}><path d="m15 18-6-6 6-6" /></svg>;
  if (name === "forward") return <svg {...common}><path d="m9 18 6-6-6-6" /></svg>;
  if (name === "reload") return <svg {...common}><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v7h-7" /></svg>;
  if (name === "go") return <svg {...common}><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></svg>;
  if (name === "plus") return <svg {...common}><path d="M12 5v14" /><path d="M5 12h14" /></svg>;
  if (name === "close") return <svg {...common}><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>;
  if (name === "volume") return <svg {...common}><path d="M11 5 6 9H3v6h3l5 4V5Z" /><path d="M15.5 8.5a5 5 0 0 1 0 7" /></svg>;
  if (name === "muted") return <svg {...common}><path d="M11 5 6 9H3v6h3l5 4V5Z" /><path d="m16 9 5 5" /><path d="m21 9-5 5" /></svg>;
  if (name === "fullscreen") return <svg {...common}><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M21 16v5h-5" /></svg>;
  return null;
}

function ElectronBrowserViewportPanel({ roundId, browserSessionId, onClose, browserState, zoomEnabled = true }) {
  browserState = browserState || window.CyreneUI.require("data").state.browser;
  const bridge = window.cyrene && window.cyrene.browser;
  const electronSessionId = String(browserSessionId || (browserState && browserState.sessionId) || "").trim();
  const hostRef = React.useRef(null);
  const surfaceRef = React.useRef(null);
  const addressRef = React.useRef(null);
  const boundsRafRef = React.useRef(0);
  const lastBoundsRef = React.useRef("");
  const resizeEdgeHintActiveRef = React.useRef(false);
  const overlayObscuredRef = React.useRef(false);
  const windowInteractionRef = React.useRef(false);
  const interactionPreviewTokenRef = React.useRef(0);
  const interactionPreviewMountedRef = React.useRef(false);
  const interactionKindRef = React.useRef("");
  const modeTargetBoundsRef = React.useRef(null);
  const modeTargetPreviewRef = React.useRef(null);
  const modePreparedRef = React.useRef(false);
  const tabMenuPreviewTokenRef = React.useRef(0);
  const [state, setState] = React.useState({ tabs: [], activeTabId: "", activeTab: null });
  const [address, setAddress] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [interactionPreview, setInteractionPreview] = React.useState(null);
  const [tabMenuPreview, setTabMenuPreview] = React.useState(null);
  const [tabContextMenu, setTabContextMenu] = React.useState(null);

  const active = state.activeTab || null;
  const tabs = Array.isArray(state.tabs) ? state.tabs : [];
  const videoFullscreen = state.videoFullscreen || {};
  const videoFullscreenActive = videoFullscreen.active === true;

  function refreshState() {
    if (!bridge || typeof bridge.getState !== "function") return;
    bridge.getState(electronSessionId).then(function (next) {
      if (next && next.ok !== false && String(next.sessionId || "") === electronSessionId) setState(next);
    }).catch(function () {});
  }

  React.useEffect(function () {
    refreshState();
    if (!bridge || typeof bridge.onState !== "function") return undefined;
    return bridge.onState(function (next) {
      if (next && next.ok !== false && String(next.sessionId || "") === electronSessionId) setState(next);
    });
  }, [electronSessionId]);

  React.useEffect(function () {
    if (!tabContextMenu && !tabMenuPreview) return undefined;
    function close() { closeTabContextMenu(); }
    function onKeyDown(event) { if (event.key === "Escape") close(); }
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    document.addEventListener("keydown", onKeyDown);
    return function () {
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
      document.removeEventListener("keydown", onKeyDown);
      scheduleBounds();
    };
  }, [!!tabContextMenu || !!tabMenuPreview]);

  React.useEffect(function () {
    if (!bridge || typeof bridge.setContext !== "function") return undefined;
    setState({ sessionId: electronSessionId, tabs: [], activeTabId: "", activeTab: null });
    const rid = String(roundId || (browserState && browserState.roundId) || "").trim();
    bridge.setContext({ sessionId: electronSessionId, roundId: rid }).then(function (next) {
      if (next && next.ok !== false && String(next.sessionId || "") === electronSessionId) setState(next);
    }).catch(function () {});
  }, [electronSessionId, roundId, browserState && browserState.roundId]);

  React.useEffect(function () {
    const nextUrl = (active && active.url) || "";
    setAddress(nextUrl === "about:blank" ? "" : nextUrl);
  }, [active && active.id, active && active.url]);

  function sendBounds(visible) {
    if (!bridge || typeof bridge.setBounds !== "function") return Promise.resolve(false);
    const node = surfaceRef.current;
    if (!visible || overlayObscuredRef.current || windowInteractionRef.current || !node) {
      if (lastBoundsRef.current === "hidden") return Promise.resolve(true);
      lastBoundsRef.current = "hidden";
      return bridge.setBounds({ sessionId: electronSessionId, visible: false }).then(function () {
        return true;
      }).catch(function () {
        lastBoundsRef.current = "";
        return false;
      });
    }
    const rect = node.getBoundingClientRect();
    const pipWindow = node.closest(".wbc-browser-window.pip");
    const borderRadius = 0;
    const pageCornerRadius = pipWindow ? 8 : 0;
    const payload = {
      sessionId: electronSessionId,
      visible: true,
      x: rect.left,
      y: rect.top,
      width: Math.max(0, rect.width),
      height: Math.max(0, rect.height),
      borderRadius: borderRadius,
      pageCornerRadius: pageCornerRadius,
      zoomEnabled: zoomEnabled !== false,
      resizeEdgeHintColor: getComputedStyle(node).getPropertyValue("--wb-accent").trim() || "#63b38f",
      resizeEdgeHintActive: resizeEdgeHintActiveRef.current,
    };
    const signature = [
      electronSessionId,
      Math.round(rect.left),
      Math.round(rect.top),
      Math.round(rect.width),
      Math.round(rect.height),
      borderRadius,
      pageCornerRadius,
      resizeEdgeHintActiveRef.current,
    ].join(":");
    if (lastBoundsRef.current === signature) return Promise.resolve(true);
    lastBoundsRef.current = signature;
    return bridge.setBounds(payload).then(function () {
      return true;
    }).catch(function () {
      if (lastBoundsRef.current === signature) lastBoundsRef.current = "";
      return false;
    });
  }

  function scheduleBounds() {
    if (boundsRafRef.current) cancelAnimationFrame(boundsRafRef.current);
    boundsRafRef.current = requestAnimationFrame(function () {
      boundsRafRef.current = 0;
      sendBounds(true);
    });
  }

  function finishWindowInteraction(token) {
    const node = surfaceRef.current;
    if (!bridge || typeof bridge.setBounds !== "function" || !node) {
      windowInteractionRef.current = false;
      interactionPreviewMountedRef.current = false;
      setInteractionPreview(null);
      lastBoundsRef.current = "";
      scheduleBounds();
      return;
    }
    const rect = node.getBoundingClientRect();
    const pipWindow = node.closest(".wbc-browser-window.pip");
    const borderRadius = 0;
    const pageCornerRadius = pipWindow ? 8 : 0;
    const signature = [
      electronSessionId,
      Math.round(rect.left),
      Math.round(rect.top),
      Math.round(rect.width),
      Math.round(rect.height),
      borderRadius,
      pageCornerRadius,
      resizeEdgeHintActiveRef.current,
    ].join(":");
    bridge.setBounds({
      sessionId: electronSessionId,
      visible: true,
      transition: true,
      x: rect.left,
      y: rect.top,
      width: Math.max(0, rect.width),
      height: Math.max(0, rect.height),
      borderRadius: borderRadius,
      pageCornerRadius: pageCornerRadius,
      zoomEnabled: zoomEnabled !== false,
      resizeEdgeHintColor: getComputedStyle(node).getPropertyValue("--wb-accent").trim() || "#63b38f",
      resizeEdgeHintActive: resizeEdgeHintActiveRef.current,
    }).then(function () {
      if (interactionPreviewTokenRef.current !== token) return;
      lastBoundsRef.current = signature;
      windowInteractionRef.current = false;
      interactionPreviewMountedRef.current = false;
      setInteractionPreview(null);
      scheduleBounds();
    }).catch(function () {
      if (interactionPreviewTokenRef.current !== token) return;
      lastBoundsRef.current = "";
      windowInteractionRef.current = false;
      interactionPreviewMountedRef.current = false;
      setInteractionPreview(null);
      scheduleBounds();
    });
  }

  function commitPreparedModeTransition(token) {
    const target = modeTargetBoundsRef.current;
    if (!bridge || typeof bridge.setBounds !== "function" || !target) {
      modePreparedRef.current = false;
      finishWindowInteraction(token);
      return;
    }
    bridge.setBounds({
      ...target,
      sessionId: electronSessionId,
      visible: true,
      transition: "commit",
      zoomEnabled: zoomEnabled !== false,
      resizeEdgeHintColor: getComputedStyle(surfaceRef.current).getPropertyValue("--wb-accent").trim() || "#63b38f",
    }).then(function () {
      if (interactionPreviewTokenRef.current !== token) return;
      modePreparedRef.current = false;
      modeTargetBoundsRef.current = null;
      modeTargetPreviewRef.current = null;
      lastBoundsRef.current = "";
      windowInteractionRef.current = false;
      interactionPreviewMountedRef.current = false;
      setInteractionPreview(null);
      scheduleBounds();
    }).catch(function () {
      if (interactionPreviewTokenRef.current !== token) return;
      modePreparedRef.current = false;
      modeTargetBoundsRef.current = null;
      modeTargetPreviewRef.current = null;
      lastBoundsRef.current = "";
      windowInteractionRef.current = false;
      interactionPreviewMountedRef.current = false;
      setInteractionPreview(null);
      scheduleBounds();
    });
  }

  function publishModeTargetReady(previewToken, fallback) {
    if (!windowInteractionRef.current
      || interactionPreviewTokenRef.current !== previewToken) return;
    window.dispatchEvent(new CustomEvent("workbench:browser-transition-target-ready", {
      detail: { sessionId: electronSessionId, fallback: fallback === true },
    }));
  }

  function prepareModeTargetFrame(previewToken) {
    var target = modeTargetBoundsRef.current;
    if (!bridge || typeof bridge.setBounds !== "function" || !target) {
      publishModeTargetReady(previewToken, true);
      return;
    }
    bridge.setBounds({
      ...target,
      sessionId: electronSessionId,
      visible: true,
      transition: "prepare",
      zoomEnabled: zoomEnabled !== false,
      resizeEdgeHintColor: getComputedStyle(surfaceRef.current).getPropertyValue("--wb-accent").trim() || "#63b38f",
    }).then(function (result) {
      if (!windowInteractionRef.current
        || interactionPreviewTokenRef.current !== previewToken) return;
      if (!result || result.ok === false || !result.pngBase64) {
        publishModeTargetReady(previewToken, true);
        return;
      }
      var targetSrc = "data:image/png;base64," + result.pngBase64;
      var decodedImage = new Image();
      decodedImage.src = targetSrc;
      var decoded = typeof decodedImage.decode === "function"
        ? decodedImage.decode().catch(function () {})
        : new Promise(function (resolve) {
            decodedImage.onload = resolve;
            decodedImage.onerror = resolve;
          });
      Promise.resolve(decoded).then(function () {
        if (!windowInteractionRef.current
          || interactionPreviewTokenRef.current !== previewToken) return;
        modePreparedRef.current = true;
        // Do not mount the target bitmap yet. Mounting it while the shell is
        // still PiP makes the page visibly rescale before the window changes.
        // The parent commits this pending bitmap and the target shell together
        // inside one ReactDOM.flushSync transaction.
        modeTargetPreviewRef.current = {
          token: previewToken,
          phase: "target",
          kind: "mode",
          src: targetSrc,
        };
        publishModeTargetReady(previewToken, false);
      });
    }).catch(function () {
      publishModeTargetReady(previewToken, true);
    });
  }

  function publishInteractionPreviewFallback(previewToken) {
    if (!windowInteractionRef.current
      || interactionPreviewTokenRef.current !== previewToken) return;
    windowInteractionRef.current = false;
    interactionPreviewMountedRef.current = false;
    setInteractionPreview(null);
    lastBoundsRef.current = "";
    window.dispatchEvent(new CustomEvent("workbench:browser-window-preview-ready", {
      detail: { sessionId: electronSessionId, fallback: true },
    }));
    if (interactionKindRef.current === "mode") {
      window.dispatchEvent(new CustomEvent("workbench:browser-transition-target-ready", {
        detail: { sessionId: electronSessionId, fallback: true },
      }));
    }
    scheduleBounds();
  }

  // DOM commit is not enough: a data-URL <img> can still be undecoded, and a
  // layout effect runs before that commit has painted. Wait for load/decode and
  // two animation frames so at least one complete proxy frame has reached the
  // renderer compositor before hiding Electron's native WebContentsView.
  function onInteractionPreviewLoad(event) {
    var preview = interactionPreview;
    if (!preview) return;
    if (preview.phase === "target") return;
    var previewToken = preview.token;
    var imageNode = event && event.currentTarget;
    var decoded = imageNode && typeof imageNode.decode === "function"
      ? imageNode.decode().catch(function () {})
      : Promise.resolve();
    Promise.resolve(decoded).then(function () {
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          if (!windowInteractionRef.current
            || interactionPreviewTokenRef.current !== previewToken) return;
          interactionPreviewMountedRef.current = true;
          Promise.resolve(sendBounds(false)).then(function (hidden) {
            if (!windowInteractionRef.current
              || interactionPreviewTokenRef.current !== previewToken) return;
            if (!hidden) {
              publishInteractionPreviewFallback(previewToken);
              return;
            }
            if (preview.kind === "mode") {
              prepareModeTargetFrame(previewToken);
              return;
            }
            window.dispatchEvent(new CustomEvent("workbench:browser-window-preview-ready", {
              detail: { sessionId: electronSessionId },
            }));
          });
        });
      });
    });
  }

  function onInteractionPreviewError() {
    var previewToken = interactionPreview && interactionPreview.token;
    if (previewToken == null) return;
    publishInteractionPreviewFallback(previewToken);
  }

  function closeTabContextMenu() {
    tabMenuPreviewTokenRef.current += 1;
    setTabContextMenu(null);
    setTabMenuPreview(null);
  }

  function onTabMenuPreviewLoad(event) {
    var preview = tabMenuPreview;
    if (!preview) return;
    var previewToken = preview.token;
    var imageNode = event && event.currentTarget;
    var decoded = imageNode && typeof imageNode.decode === "function"
      ? imageNode.decode().catch(function () {})
      : Promise.resolve();
    Promise.resolve(decoded).then(function () {
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          if (!tabMenuPreview || tabMenuPreviewTokenRef.current !== previewToken) return;
          Promise.resolve(sendBounds(false)).then(function (hidden) {
            if (!hidden || tabMenuPreviewTokenRef.current !== previewToken) return;
            setTabContextMenu(preview.menu);
          });
        });
      });
    });
  }

  function onTabMenuPreviewError() {
    closeTabContextMenu();
    setError(browserLabel("browser.context.previewFailed", "Could not open the tab menu."));
  }

  React.useEffect(function () {
    scheduleBounds();
    const node = hostRef.current;
    const surface = surfaceRef.current;
    const ro = typeof ResizeObserver !== "undefined" && node ? new ResizeObserver(scheduleBounds) : null;
    if (ro && node) ro.observe(node);
    if (ro && surface) ro.observe(surface);
    window.addEventListener("resize", scheduleBounds);
    return function () {
      if (boundsRafRef.current) cancelAnimationFrame(boundsRafRef.current);
      if (ro) ro.disconnect();
      window.removeEventListener("resize", scheduleBounds);
      sendBounds(false);
    };
  }, [electronSessionId]);

  React.useEffect(function () {
    function onBrowserObscured(event) {
      const obscured = !!(event && event.detail && event.detail.obscured);
      overlayObscuredRef.current = obscured;
      if (obscured) {
        if (boundsRafRef.current) {
          cancelAnimationFrame(boundsRafRef.current);
          boundsRafRef.current = 0;
        }
        sendBounds(false);
      } else {
        scheduleBounds();
      }
    }
    window.addEventListener("workbench:browser-obscured", onBrowserObscured);
    return function () {
      window.removeEventListener("workbench:browser-obscured", onBrowserObscured);
    };
  }, [electronSessionId]);

  React.useEffect(function () {
    function onResizeHint(event) {
      resizeEdgeHintActiveRef.current = !!(event && event.detail && event.detail.active);
      lastBoundsRef.current = "";
      scheduleBounds();
    }
    window.addEventListener("workbench:right-resize-hint", onResizeHint);
    return function () {
      window.removeEventListener("workbench:right-resize-hint", onResizeHint);
    };
  }, [electronSessionId]);

  React.useEffect(function () {
    function onWorkbenchRightResize(ev) {
      var phase = ev && ev.detail && ev.detail.phase;
      if (phase === "start") sendBounds(false);
      else scheduleBounds();
    }
    window.addEventListener("workbench:right-resize", onWorkbenchRightResize);
    return function () {
      window.removeEventListener("workbench:right-resize", onWorkbenchRightResize);
    };
  }, [electronSessionId]);

  // Floating browser windows can move without changing the native host's own
  // dimensions. ResizeObserver cannot see that translation, so the workbench
  // publishes an explicit layout event while a window is dragged or resized.
  React.useEffect(function () {
    function onBrowserLayout() { scheduleBounds(); }
    window.addEventListener("workbench:browser-layout", onBrowserLayout);
    return function () {
      window.removeEventListener("workbench:browser-layout", onBrowserLayout);
    };
  }, [electronSessionId]);

  // A native WebContentsView is not part of the renderer's transform tree. If
  // it stays attached while the PiP shell moves, it remains at the old hit-test
  // position and can steal the pointer stream from the renderer. Use the
  // committed bitmap proxy for drag, resize, and mode changes; the layout
  // effect above hides the native view only after that proxy is paint-ready.
  React.useEffect(function () {
    function onModeTargetPreviewCommit(event) {
      var detail = event && event.detail || {};
      if (String(detail.sessionId || "") !== electronSessionId) return;
      var pending = modeTargetPreviewRef.current;
      if (!pending || !windowInteractionRef.current) return;
      setInteractionPreview(pending);
      interactionPreviewMountedRef.current = true;
    }

    function onBrowserWindowInteraction(event) {
      var detail = event && event.detail || {};
      if (String(detail.sessionId || "") !== electronSessionId) return;
      var activeInteraction = detail.active === true;
      var token = interactionPreviewTokenRef.current + 1;
      interactionPreviewTokenRef.current = token;
      if (!activeInteraction) {
        // A very short gesture can end before capture + React commit. In that
        // case the native view was never hidden, so just move it to the final
        // bounds instead of starting an unnecessary detach/reattach cycle.
        if (!interactionPreviewMountedRef.current) {
          windowInteractionRef.current = false;
          interactionPreviewMountedRef.current = false;
          setInteractionPreview(null);
          lastBoundsRef.current = "";
          scheduleBounds();
          return;
        }
        // Keep the bitmap proxy mounted until Electron confirms that Chromium
        // has produced a frame at the final PiP/fullscreen bounds.
        if (modePreparedRef.current && interactionKindRef.current === "mode") {
          commitPreparedModeTransition(token);
        } else {
          finishWindowInteraction(token);
        }
        return;
      }
      windowInteractionRef.current = true;
      interactionPreviewMountedRef.current = false;
      interactionKindRef.current = String(detail.kind || "");
      modeTargetBoundsRef.current = detail.targetBounds || null;
      modeTargetPreviewRef.current = null;
      modePreparedRef.current = false;
      setInteractionPreview(null);
      if (!bridge || typeof bridge.screenshot !== "function") {
        publishInteractionPreviewFallback(token);
        return;
      }
      bridge.screenshot({
        sessionId: electronSessionId,
        // The source proxy is only ever shown at its current geometry. The
        // separately prepared target proxy handles the new geometry, so a
        // costly full-window high-resolution source capture is unnecessary.
        highResolution: false,
        targetWidth: 0,
        targetHeight: 0,
      }).then(function (result) {
        if (!windowInteractionRef.current || interactionPreviewTokenRef.current !== token) return;
        if (result && result.ok !== false && result.pngBase64) {
          setInteractionPreview({
            token: token,
            phase: "source",
            kind: String(detail.kind || ""),
            src: "data:image/png;base64," + result.pngBase64,
          });
          return;
        }
        windowInteractionRef.current = false;
        interactionPreviewMountedRef.current = false;
        lastBoundsRef.current = "";
        window.dispatchEvent(new CustomEvent("workbench:browser-window-preview-ready", {
          detail: { sessionId: electronSessionId, fallback: true },
        }));
        scheduleBounds();
      }).catch(function () {
        if (!windowInteractionRef.current || interactionPreviewTokenRef.current !== token) return;
        windowInteractionRef.current = false;
        interactionPreviewMountedRef.current = false;
        lastBoundsRef.current = "";
        window.dispatchEvent(new CustomEvent("workbench:browser-window-preview-ready", {
          detail: { sessionId: electronSessionId, fallback: true },
        }));
        scheduleBounds();
      });
    }
    window.addEventListener("workbench:browser-transition-commit-preview", onModeTargetPreviewCommit);
    window.addEventListener("workbench:browser-window-interaction", onBrowserWindowInteraction);
    return function () {
      interactionPreviewTokenRef.current += 1;
      windowInteractionRef.current = false;
      interactionPreviewMountedRef.current = false;
      interactionKindRef.current = "";
      modeTargetBoundsRef.current = null;
      modeTargetPreviewRef.current = null;
      modePreparedRef.current = false;
      window.removeEventListener("workbench:browser-transition-commit-preview", onModeTargetPreviewCommit);
      window.removeEventListener("workbench:browser-window-interaction", onBrowserWindowInteraction);
    };
  }, [electronSessionId]);

  React.useEffect(function () {
    scheduleBounds();
  }, [electronSessionId, state.activeTabId, tabs.length]);

  function run(action) {
    setBusy(true);
    setError("");
    return Promise.resolve()
      .then(action)
      .then(function (next) {
        if (next && next.ok === false) setError(browserErrorText(next.error, "browser.error.actionFailed", "The browser action failed. Please retry."));
        else if (next && Array.isArray(next.tabs)) setState(next);
        return next;
      })
      .catch(function (e) { setError(browserErrorText(e, "browser.error.actionFailed", "The browser action failed. Please retry.")); })
      .finally(function () { setBusy(false); scheduleBounds(); });
  }

  function createTab(url) {
    return run(function () { return bridge.createTab({ sessionId: electronSessionId, url: url || "about:blank", activate: true }); });
  }

  function navigate() {
    const url = (addressRef.current ? addressRef.current.value : address).trim();
    if (!url) return createTab("about:blank");
    run(function () { return bridge.navigate({ sessionId: electronSessionId, url: url }); });
  }

  function onAddressKeyDown(e) {
    if (e.key === "Enter") {
      e.preventDefault();
      navigate();
    }
  }

  function openTabContextMenu(tab, event) {
    event.preventDefault();
    event.stopPropagation();
    var token = tabMenuPreviewTokenRef.current + 1;
    tabMenuPreviewTokenRef.current = token;
    var menu = {
      tab: tab,
      left: Math.max(8, Math.min(event.clientX, window.innerWidth - 210 - 8)),
      top: Math.max(8, Math.min(event.clientY, window.innerHeight - 116 - 8)),
    };
    setError("");
    setTabContextMenu(null);
    if (!bridge || typeof bridge.screenshot !== "function") {
      setError(browserLabel("browser.context.previewFailed", "Could not open the tab menu."));
      return;
    }
    bridge.screenshot({ sessionId: electronSessionId, tabId: state.activeTabId || "" }).then(function (result) {
      if (tabMenuPreviewTokenRef.current !== token) return;
      if (!result || result.ok === false || !result.pngBase64) throw new Error(result && result.error || "capture failed");
      setTabMenuPreview({
        token: token,
        src: "data:image/png;base64," + result.pngBase64,
        menu: menu,
      });
    }).catch(function () {
      if (tabMenuPreviewTokenRef.current !== token) return;
      closeTabContextMenu();
      setError(browserErrorText(null, "browser.error.captureFailed", "The browser preview could not be captured. Please retry."));
    });
  }

  function runForTab(tab, action) {
    closeTabContextMenu();
    return run(function () {
      var activate = tab.id === state.activeTabId
        ? Promise.resolve()
        : bridge.activateTab({ sessionId: electronSessionId, tabId: tab.id });
      return activate.then(action);
    });
  }

  function browserLabel(key, fallback) {
    try { return window.CyreneUI.require("i18n").t(key, null, fallback); } catch (e) { return fallback; }
  }

  return (
    <div className="browser-view native">
      <div className="browser-tabs-strip">
        {tabs.map(function (tab) {
          return (
            <button key={tab.id} type="button" className={"browser-tab" + (tab.id === state.activeTabId ? " active" : "")} onClick={function () { run(function () { return bridge.activateTab({ sessionId: electronSessionId, tabId: tab.id }); }); }} onContextMenu={function (event) { openTabContextMenu(tab, event); }} title={(tab.title || tab.url || "Browser") + (tab.audible ? " · audible" : "")}>
              <span className="browser-tab-title">{tab.title || tab.url || "New tab"}</span>
              {tab.audible && <span className="browser-tab-audio" aria-hidden="true"><BrowserIcon name="volume" size={13} /></span>}
              <span
                className="browser-tab-close"
                role="button"
                tabIndex={-1}
                onClick={function (e) { e.stopPropagation(); run(function () { return bridge.closeTab({ sessionId: electronSessionId, tabId: tab.id }); }); }}
                title="Close tab"
              ><BrowserIcon name="close" size={12} /></span>
            </button>
          );
        })}
        <button type="button" className="browser-icon-btn" onClick={function () { createTab("about:blank"); }} title="New tab"><BrowserIcon name="plus" /></button>
        {onClose && <button type="button" className="browser-icon-btn" onClick={onClose} title="Close panel"><BrowserIcon name="close" /></button>}
      </div>
      {tabContextMenu && (
        <div className="wb-item-context-layer">
          <div className="wb-item-context-scrim" onPointerDown={closeTabContextMenu} />
          <div
            className="wb-item-context-menu browser-tab-context-menu"
            role="menu"
            aria-label={tabContextMenu.tab.title || tabContextMenu.tab.url || "Browser"}
            style={{ left: tabContextMenu.left + "px", top: tabContextMenu.top + "px" }}
            onContextMenu={function (event) { event.preventDefault(); }}
          >
            <button type="button" role="menuitem" onClick={function () { var tab = tabContextMenu.tab; runForTab(tab, function () { return bridge.reload(electronSessionId); }); }}>
              <BrowserIcon name="reload" size={15} />{browserLabel("browser.context.reload", "Reload")}
            </button>
            <button type="button" role="menuitem" onClick={function () { var tab = tabContextMenu.tab; runForTab(tab, function () { return bridge.setMuted({ sessionId: electronSessionId, muted: !tab.muted }); }); }}>
              <BrowserIcon name={tabContextMenu.tab.muted ? "volume" : "muted"} size={15} />{tabContextMenu.tab.muted ? browserLabel("browser.context.unmute", "Unmute") : browserLabel("browser.context.mute", "Mute")}
            </button>
            <div className="wb-item-context-separator" />
            <button type="button" role="menuitem" className="danger" onClick={function () { var tab = tabContextMenu.tab; closeTabContextMenu(); run(function () { return bridge.closeTab({ sessionId: electronSessionId, tabId: tab.id }); }); }}>
              <BrowserIcon name="close" size={15} />{browserLabel("browser.context.close", "Close tab")}
            </button>
          </div>
        </div>
      )}
      <div className="browser-nav-bar">
        <button type="button" className="browser-icon-btn" disabled={!active || !active.canGoBack || busy} onClick={function () { run(function () { return bridge.goBack(electronSessionId); }); }} title="Back"><BrowserIcon name="back" /></button>
        <button type="button" className="browser-icon-btn" disabled={!active || !active.canGoForward || busy} onClick={function () { run(function () { return bridge.goForward(electronSessionId); }); }} title="Forward"><BrowserIcon name="forward" /></button>
        <button type="button" className="browser-icon-btn" disabled={!active || busy} onClick={function () { run(function () { return bridge.reload(electronSessionId); }); }} title="Reload"><BrowserIcon name="reload" /></button>
        <input ref={addressRef} className="browser-address" value={address} onChange={function (e) { setAddress(e.target.value); }} onKeyDown={onAddressKeyDown} placeholder="https://example.com" />
        <button type="button" className="browser-icon-btn browser-go-btn" disabled={busy} onClick={navigate} title="Go"><BrowserIcon name="go" /></button>
        <button type="button" className={"browser-icon-btn" + (active && active.muted ? " muted" : "")} disabled={!active} onClick={function () { run(function () { return bridge.setMuted({ sessionId: electronSessionId, muted: !(active && active.muted) }); }); }} title={active && active.muted ? "Unmute" : "Mute"}>
          <BrowserIcon name={active && active.muted ? "muted" : "volume"} />
        </button>
      </div>
      {error && <div className="browser-error">{error}</div>}
      <div ref={hostRef} className={"browser-native-host" + (interactionPreview || tabMenuPreview ? " is-previewing" : "")}>
        <div ref={surfaceRef} className="browser-native-surface">
          {interactionPreview && (
            <img
              className="browser-native-preview"
              src={interactionPreview.src}
              onLoad={onInteractionPreviewLoad}
              onError={onInteractionPreviewError}
              alt=""
              aria-hidden="true"
            />
          )}
          {tabMenuPreview && (
            <img
              className="browser-native-preview"
              src={tabMenuPreview.src}
              onLoad={onTabMenuPreviewLoad}
              onError={onTabMenuPreviewError}
              alt=""
              aria-hidden="true"
            />
          )}
          {!tabs.length && (
            <div className="browser-empty">
              <button type="button" className="btn primary" onClick={function () { createTab("about:blank"); }}>打开浏览器</button>
            </div>
          )}
        </div>
      </div>
      {videoFullscreenActive && (
        <div className="browser-video-fullscreen-overlay" role="status" aria-live="polite">
          <span className="browser-video-fullscreen-icon" aria-hidden="true"><BrowserIcon name="fullscreen" size={24} /></span>
          <strong>已在全屏播放</strong>
          <span>{videoFullscreen.external ? "视频正在独立的全屏窗口中播放" : "视频正在 Cyrene 内全屏播放"}</span>
        </div>
      )}
    </div>
  );
}

function ScreencastBrowserViewportPanel({ roundId, onClose, onTakeoverComplete, browserState, browserSessionId }) {
  const dataStore = window.CyreneUI.require("data");
  dataStore.useVersion();
  const browser = browserState || (dataStore.state.browser || {});
  const imgRef = React.useRef(null);
  const stageRef = React.useRef(null);
  const sinkRef = React.useRef(null);      // hidden textarea: keyboard + IME sink
  const composingRef = React.useRef(false); // mid IME composition
  const wsRef = React.useRef(null);
  const moveTsRef = React.useRef(0);
  const objectUrlRef = React.useRef("");
  const [connected, setConnected] = React.useState(false);
  const [error, setError] = React.useState("");
  const [frameUrl, setFrameUrl] = React.useState("");
  const [controlling, setControlling] = React.useState(false);
  const [nativeBusy, setNativeBusy] = React.useState(false);
  // Single source of truth in the shared DATA, so the native-window state stays
  // correct across panel remounts (tab switches) and SSE updates (browser_frame
  // / takeover_cancelled clear it). useDataVersion() re-renders on every bump.
  const nativeWindow = !!browser.userWindow;
  function setNativeWindow(open) {
    const state = dataStore.state;
    state.browser = state.browser || {};
    state.browser.userWindow = open;
    const chatId = String(browserSessionId || browser.sessionId || "").trim();
    if (chatId) {
      state.browserByChat = state.browserByChat || {};
      state.browserByChat[chatId] = {
        ...(state.browserByChat[chatId] || {}),
        userWindow: open,
        sessionId: chatId,
      };
    }
    dataStore.bump();
  }
  const [takeoverSubmitting, setTakeoverSubmitting] = React.useState(false);
  const [takeoverError, setTakeoverError] = React.useState("");

  // ---- screencast socket (also the user-control channel) -----------------
  React.useEffect(function () {
    let closed = false;
    let retry = null;

    function connect() {
      try {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const ws = new WebSocket(proto + "//" + location.host + "/ws/browser");
        wsRef.current = ws;
        ws.binaryType = "arraybuffer";
        ws.onopen = function () {
          if (!closed) {
            setConnected(true); setError("");
            try {
              ws.send(JSON.stringify({
                type: "context",
                sessionId: String(browserSessionId || browser.sessionId || ""),
                roundId: String(roundId || browser.roundId || ""),
              }));
            } catch (e) {}
          }
        };
        ws.onmessage = function (ev) {
          if (typeof ev.data !== "string") {
            const img = imgRef.current;
            if (!img) return;
            const blob = ev.data instanceof Blob ? ev.data : new Blob([ev.data], { type: "image/jpeg" });
            const nextUrl = URL.createObjectURL(blob);
            const prevUrl = objectUrlRef.current;
            objectUrlRef.current = nextUrl;
            img.src = nextUrl;
            if (prevUrl) URL.revokeObjectURL(prevUrl);
            return;
          }
          let msg;
          try { msg = JSON.parse(ev.data); } catch (e) { return; }
          if (msg.type === "error") {
            setError(browserErrorText(msg.error, "browser.error.unavailable", "The browser view is unavailable."));
            closed = true;
            try { ws.close(); } catch (e) {}
            return;
          }
          if (msg.type === "frame") {
            if (msg.url) setFrameUrl(msg.url);
          }
        };
        ws.onclose = function () {
          if (wsRef.current === ws) wsRef.current = null;
          if (closed) return;
          setConnected(false);
          retry = setTimeout(connect, 1500);
        };
        ws.onerror = function () { try { ws.close(); } catch (e) {} };
      } catch (e) { /* ignore */ }
    }
    connect();

    return function () {
      closed = true;
      if (retry) clearTimeout(retry);
      const ws = wsRef.current;
      if (ws) {
        try { ws.send(JSON.stringify({ type: "control", on: false })); } catch (e) {}
        try { ws.close(); } catch (e) {}
      }
      wsRef.current = null;
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = "";
      }
    };
  }, [browserSessionId, roundId]);

  function sendWs(obj) {
    const ws = wsRef.current;
    if (ws && ws.readyState === 1) {
      try { ws.send(JSON.stringify(obj)); return true; } catch (e) {}
    }
    return false;
  }

  // ---- user live-control: forward mouse/keyboard to the headless page ----
  function modsOf(e) {
    return (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
  }
  function buttonOf(e) {
    return e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
  }
  // Map a DOM event to viewport CSS pixels. The screencast frame is the viewport
  // at scale 1 (maxWidth/maxHeight == viewport), so the JPEG's natural size maps
  // 1:1 to CDP Input coordinates regardless of how the panel scales the <img>.
  function toViewport(e) {
    const img = imgRef.current;
    if (!img || !img.naturalWidth) return null;
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const x = (e.clientX - rect.left) / rect.width * img.naturalWidth;
    const y = (e.clientY - rect.top) / rect.height * img.naturalHeight;
    if (x < 0 || y < 0 || x > img.naturalWidth || y > img.naturalHeight) return null;
    return { x: Math.round(x), y: Math.round(y) };
  }

  function focusSink() {
    const sink = sinkRef.current;
    if (!sink) return;
    try { sink.focus({ preventScroll: true }); } catch (e) { try { sink.focus(); } catch (e2) {} }
  }

  function startControl() {
    if (!connected || error) return;
    setControlling(true);
    sendWs({ type: "control", on: true });
    setTimeout(focusSink, 0);
  }
  function stopControl() {
    setControlling(false);
    sendWs({ type: "control", on: false });
  }

  // Non-passive wheel listener so we can preventDefault and forward scrolling.
  React.useEffect(function () {
    if (!controlling) return undefined;
    const el = stageRef.current;
    if (!el) return undefined;
    function onWheel(e) {
      e.preventDefault();
      const pt = toViewport(e);
      if (!pt) return;
      sendWs({ type: "mouse", event: "mouseWheel", x: pt.x, y: pt.y, deltaX: e.deltaX, deltaY: e.deltaY, modifiers: modsOf(e) });
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    return function () { el.removeEventListener("wheel", onWheel); };
  }, [controlling]);

  function onMouseDown(e) {
    if (!controlling) return;
    e.preventDefault();
    if (!composingRef.current) focusSink(); // keep keyboard captured after clicking the page
    const pt = toViewport(e); if (!pt) return;
    sendWs({ type: "mouse", event: "mousePressed", x: pt.x, y: pt.y, button: buttonOf(e), clickCount: e.detail || 1, modifiers: modsOf(e) });
  }
  function onMouseUp(e) {
    if (!controlling) return;
    e.preventDefault();
    const pt = toViewport(e); if (!pt) return;
    sendWs({ type: "mouse", event: "mouseReleased", x: pt.x, y: pt.y, button: buttonOf(e), clickCount: e.detail || 1, modifiers: modsOf(e) });
  }
  function onMouseMove(e) {
    if (!controlling) return;
    const now = Date.now();
    if (now - moveTsRef.current < 33) return; // throttle ~30fps
    moveTsRef.current = now;
    const pt = toViewport(e); if (!pt) return;
    sendWs({ type: "mouse", event: "mouseMoved", x: pt.x, y: pt.y, button: "none", modifiers: modsOf(e) });
  }
  function onContextMenu(e) {
    if (controlling) e.preventDefault();
  }

  // Keyboard + IME flow through a hidden, focused <textarea> sink. Printable keys
  // and IME composition are delivered as committed text via Input.insertText (the
  // only way CJK/IME characters get through); "special" keys and shortcuts are
  // forwarded as raw key events so Enter/Backspace/arrows/Ctrl-combos keep their
  // semantics. isComposing-guarded so pinyin keystrokes never leak as Latin.
  function isSpecialKey(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return true;
    return !(e.key && e.key.length === 1);
  }
  function onSinkKeyDown(e) {
    if (!controlling) return;
    if (e.isComposing || e.keyCode === 229) return; // IME building a candidate
    if (!isSpecialKey(e)) return;                    // printable → onSinkInput sends it
    e.preventDefault();
    sendWs({ type: "key", event: "keyDown", key: e.key, code: e.code, keyCode: e.keyCode || e.which || 0, modifiers: modsOf(e) });
  }
  function onSinkKeyUp(e) {
    if (!controlling) return;
    if (e.isComposing) return;
    if (!isSpecialKey(e)) return;
    e.preventDefault();
    sendWs({ type: "key", event: "keyUp", key: e.key, code: e.code, keyCode: e.keyCode || e.which || 0, modifiers: modsOf(e) });
  }
  function onSinkInput(e) {
    if (!controlling) return;
    const ne = e.nativeEvent || {};
    if (ne.isComposing || composingRef.current) return; // mid-composition → wait for end
    const it = ne.inputType || "";
    if ((it === "insertText" || it === "insertFromPaste") && ne.data) {
      sendWs({ type: "text", text: ne.data });
    }
    if (sinkRef.current) sinkRef.current.value = "";
  }
  function onSinkCompositionStart() { composingRef.current = true; }
  function onSinkCompositionEnd(e) {
    composingRef.current = false;
    const data = (e && e.data) || (e.nativeEvent && e.nativeEvent.data) || "";
    if (data) sendWs({ type: "text", text: data });
    if (sinkRef.current) sinkRef.current.value = "";
  }

  // ---- native-window escape hatch (sites that block headless) -----------
  function openNativeWindow() {
    if (nativeBusy) return;
    setNativeBusy(true);
    setTakeoverError("");
    stopControl();
    fetch("/api/browser/takeover", { method: "POST" })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (d) {
        if (d && d.ok) setNativeWindow(true);
        else setTakeoverError(browserErrorText(d && d.error, "browser.error.openWindowFailed", "Could not open the browser window."));
      })
      .catch(function (e) { setTakeoverError(browserErrorText(e, "browser.error.openWindowFailed", "Could not open the browser window.")); })
      .finally(function () { setNativeBusy(false); });
  }
  function closeNativeWindow() {
    if (nativeBusy) return;
    setNativeBusy(true);
    fetch("/api/browser/release", { method: "POST" })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function () { setNativeWindow(false); })
      .catch(function () {})
      .finally(function () { setNativeBusy(false); });
  }

  // ---- agent-initiated login takeover (M3) ------------------------------
  const takeover = browser.takeover || {};
  const completeLabel = "我已完成登录";

  function completeTakeover() {
    if (takeoverSubmitting) return;
    setTakeoverSubmitting(true);
    setTakeoverError("");

    if (typeof onTakeoverComplete === "function") {
      Promise.resolve(onTakeoverComplete({
        questionId: takeover.questionId || "",
        selectedOption: completeLabel,
        text: completeLabel,
      })).catch(function (e) {
        setTakeoverError(browserErrorText(e, "browser.error.submitFailed", "Could not submit the browser action. Please retry."));
      }).finally(function () {
        setTakeoverSubmitting(false);
      });
      return;
    }

    if (!takeover.questionId) {
      setTakeoverError("缺少登录确认问题，请在聊天输入区确认。");
      setTakeoverSubmitting(false);
      return;
    }

    fetch("/api/chat/answer-question", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question_id: takeover.questionId,
        answer: completeLabel,
        selected_option: completeLabel,
        stream: false,
      }),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json().catch(function () { return {}; });
    }).then(function () {
      window.CyreneUI.require("data").refreshSessions();
    }).catch(function (e) {
      setTakeoverError(browserErrorText(e, "browser.error.submitFailed", "Could not submit the browser action. Please retry."));
    }).finally(function () {
      setTakeoverSubmitting(false);
    });
  }

  // ---- render ------------------------------------------------------------
  const url = frameUrl || browser.url || "";
  const title = browser.title || "";
  const action = browser.action || "";
  const target = browser.target || "";
  const actionLabel = !action ? "" :
    action === "navigate" ? ("导航到 " + (url || "")) :
    action === "click" ? ("点击了 " + (target || "")) :
    action === "type" ? ("输入到 " + (target || "")) : action;

  const showControls = !error && !takeover.pending && !nativeWindow;
  return (
    <div className="browser-view screencast">
      <div className="browser-view-bar">
        <span className={"browser-status-dot " + (connected ? "running" : "queued")}></span>
        <span className="browser-view-title" title={url}>
          {title ? (title + " — ") : ""}{url || "浏览器"}
        </span>
        {showControls && (controlling ? (
          <React.Fragment>
            <button type="button" className="browser-text-btn active" onClick={stopControl} title="把控制权交还给 Agent">退出控制</button>
            <button type="button" className="browser-text-btn" onClick={openNativeWindow} disabled={nativeBusy} title="遇到验证码/反爬时，改用独立浏览器窗口">独立窗口</button>
          </React.Fragment>
        ) : (
          <button type="button" className="browser-text-btn active" onClick={startControl} disabled={!connected} title="在此面板内直接操作浏览器">接管</button>
        ))}
        {onClose && <button type="button" className="browser-icon-btn compact" onClick={onClose} title="关闭"><BrowserIcon name="close" size={14} /></button>}
      </div>

      <div
        ref={stageRef}
        className={"browser-stage" + (controlling ? " controlling" : "")}
        onMouseDown={onMouseDown}
        onMouseUp={onMouseUp}
        onMouseMove={onMouseMove}
        onContextMenu={onContextMenu}
      >
        {controlling && (
          <textarea
            ref={sinkRef}
            defaultValue=""
            onKeyDown={onSinkKeyDown}
            onKeyUp={onSinkKeyUp}
            onInput={onSinkInput}
            onCompositionStart={onSinkCompositionStart}
            onCompositionEnd={onSinkCompositionEnd}
            autoCapitalize="off"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
            tabIndex={-1}
            aria-hidden="true"
            style={{ position: "absolute", top: 0, left: 0, width: 1, height: 1, opacity: 0, padding: 0, margin: 0, border: 0, resize: "none", pointerEvents: "none", overflow: "hidden", whiteSpace: "nowrap", zIndex: 1 }}
          />
        )}
        {error ? (
          <div className="browser-state-card">
            浏览器实时视图不可用：{error}
            <div className="browser-state-note">请查看后端日志或重启 Cyrene 后重试。</div>
          </div>
        ) : nativeWindow ? (
          <div className="browser-state-card wide">
            <div className="browser-state-title">已在独立浏览器窗口打开</div>
            <div className="browser-state-copy">请在弹出的浏览器窗口里完成登录 / 验证码，完成后点下面切回内嵌视图继续。</div>
            <button type="button" className="btn primary" onClick={closeNativeWindow} disabled={nativeBusy} style={{ minWidth: 132 }}>
              {nativeBusy ? "正在切回…" : "切回内嵌视图"}
            </button>
            {takeoverError && <div className="browser-error-text">{takeoverError}</div>}
          </div>
        ) : takeover.pending ? (
          <div className="browser-state-card wide browser-takeover">
            <div className="browser-state-title">等待你在浏览器窗口登录…</div>
            {takeover.reason && <div className="browser-state-copy">{takeover.reason}</div>}
            <div className="browser-state-url">{takeover.url || url}</div>
            <div className="browser-state-copy">请在弹出的浏览器窗口完成登录，然后回到这里继续。</div>
            <button
              type="button"
              className="btn primary"
              onClick={completeTakeover}
              disabled={takeoverSubmitting}
              style={{ minWidth: 132 }}
            >
              {takeoverSubmitting ? "正在继续…" : completeLabel}
            </button>
            {takeoverError && <div className="browser-error-text">{takeoverError}</div>}
          </div>
        ) : (
          <img ref={imgRef} alt="browser" draggable={false} className="browser-frame-img" />
        )}
      </div>

      {controlling && !error && !takeover.pending && !nativeWindow ? (
        <div className="browser-view-action active">
          ● 你正在直接控制浏览器（agent 浏览器操作已暂停）— 点击 / 滚动 / 输入（含中文）都会作用到页面
        </div>
      ) : actionLabel && !error && !takeover.pending && !nativeWindow && (
        <div className="browser-view-action">
          ▸ {actionLabel}
        </div>
      )}
    </div>
  );
}

window.CyreneUI.browser = window.CyreneUI.register("browser", {
  ViewportPanel: BrowserViewportPanel,
});
