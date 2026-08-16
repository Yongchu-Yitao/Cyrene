// Spotlight mode: scrims dim everything except the target element, a frame
// outlines it, and a step bubble explains it. Positions are re-measured every
// animation frame so the highlight follows scrolling / streaming re-renders.
// The gap in the middle of the scrims stays clickable, so the user can really
// operate the widget being explained.
(function (root) {
  "use strict";

  const { useState, useEffect, useRef } = React;

  var RETRY_MS = 200;
  var RETRY_LIMIT = 50;   // ~10s, mirrors the settings deep-link retry window
  var EPSILON = 0.5;
  var GAP = 12;
  var VIEWPORT_PAD = 8;
  var FRAME_GAP = 2;      // highlight sits 2px around the element

  // Pick the last *visible* match: several widgets (e.g. per-message retry
  // buttons) exist once per message, and the latest one is what the user
  // wants. Visible = has a non-zero box (display:none rects are all zero).
  function resolveTarget(id) {
    var all = document.querySelectorAll('[data-tour="' + id + '"], [data-cyrene-node-id="' + id + '"]');
    for (var i = all.length - 1; i >= 0; i -= 1) {
      var r = all[i].getBoundingClientRect();
      if (r.width > 0 && r.height > 0) return all[i];
    }
    return all.length ? all[all.length - 1] : null;
  }

  function rectClose(a, b) {
    if (!a || !b) return false;
    return Math.abs(a.x - b.x) < EPSILON && Math.abs(a.y - b.y) < EPSILON &&
      Math.abs(a.w - b.w) < EPSILON && Math.abs(a.h - b.h) < EPSILON &&
      a.radius === b.radius;
  }

  // Match the frame's corner to the element's own corner radius so the
  // highlight wraps the button exactly (pill buttons stay pills). Square
  // containers still get a visible minimum radius so the spotlight reads as
  // rounded instead of a hard-edged box.
  var MIN_RADIUS = 10;
  function frameRadius(elementRadius, extra) {
    var m = String(elementRadius || "").match(/^([\d.]+)px$/);
    if (m) return Math.max(Number(m[1]) + extra, MIN_RADIUS) + "px";
    return elementRadius || (MIN_RADIUS + "px");
  }

  // Pick a bubble spot below/above/right/left of the target, scoring each
  // candidate by how much it overflows the viewport; prefer below.
  function computeLayout(rect, bubble, viewport) {
    var candidates = [];
    function push(x, y) {
      var overflow = 0;
      if (x < VIEWPORT_PAD) overflow += VIEWPORT_PAD - x;
      if (y < VIEWPORT_PAD) overflow += VIEWPORT_PAD - y;
      if (x + bubble.w > viewport.w - VIEWPORT_PAD) overflow += x + bubble.w - (viewport.w - VIEWPORT_PAD);
      if (y + bubble.h > viewport.h - VIEWPORT_PAD) overflow += y + bubble.h - (viewport.h - VIEWPORT_PAD);
      candidates.push({ x: x, y: y, overflow: overflow });
    }
    var centeredX = rect.x + rect.w / 2 - bubble.w / 2;
    push(centeredX, rect.y + rect.h + GAP);          // below (preferred)
    push(centeredX, rect.y - GAP - bubble.h);        // above
    push(rect.x + rect.w + GAP, rect.y + rect.h / 2 - bubble.h / 2); // right
    push(rect.x - GAP - bubble.w, rect.y + rect.h / 2 - bubble.h / 2); // left
    candidates.sort(function (a, b) { return a.overflow - b.overflow; });
    var best = candidates[0];
    best.x = Math.max(VIEWPORT_PAD, Math.min(best.x, viewport.w - bubble.w - VIEWPORT_PAD));
    best.y = Math.max(VIEWPORT_PAD, Math.min(best.y, viewport.h - bubble.h - VIEWPORT_PAD));
    return best;
  }

  function SpotlightOverlay({ guide, step, index, total, onNext, onPrev, onStop, onFinish, onSkipStep }) {
    var { t } = root.CyreneUI.require("i18n").use();
    var [rect, setRect] = useState(null);
    var [missing, setMissing] = useState(false);
    var [bubbleSize, setBubbleSize] = useState(null);
    var [bubbleLayout, setBubbleLayout] = useState(null);
    var bubbleRef = useRef(null);

    var hasTarget = !!(step && step.target);

    // Measure loop: retry until the target exists (it may render only after
    // navigation / async data), then re-measure every frame.
    useEffect(function () {
      if (!hasTarget) {
        setRect(null);
        setMissing(false);
        return undefined;
      }
      var cancelled = false;
      var raf = 0;
      var timer = 0;
      var cached = null;
      var element = null;
      var attempts = 0;
      var refreshCounter = 0;

      function tick() {
        if (cancelled) return;
        // Re-resolve every ~1s so a freshly rendered target (e.g. the retry
        // button of the latest message) takes over the highlight.
        if (!element || !document.body.contains(element) || refreshCounter % 60 === 0) {
          element = resolveTarget(step.target);
        }
        refreshCounter += 1;
        if (!element) {
          attempts += 1;
          if (attempts > RETRY_LIMIT) {
            setMissing(true);
            return;
          }
          timer = setTimeout(tick, RETRY_MS);
          return;
        }
        setMissing(false);
        var r = element.getBoundingClientRect();
        var cs = window.getComputedStyle(element);
        var next = {
          x: r.left, y: r.top, w: r.width, h: r.height,
          radius: (cs.borderRadius || "").split(" ")[0] || "0px",
        };
        if (!rectClose(cached, next)) {
          cached = next;
          setRect(next);
        }
        raf = requestAnimationFrame(tick);
      }
      tick();
      return function cleanup() {
        cancelled = true;
        cancelAnimationFrame(raf);
        clearTimeout(timer);
      };
    }, [hasTarget, step && step.target]);

    // For "try clicking it" steps, clicking the highlighted element itself
    // advances to the next step. Capture phase so it fires before the widget's
    // own handler (which still runs — the user's click really operates the UI).
    useEffect(function () {
      if (!hasTarget || !step || step.interact !== "click") return undefined;
      function onDocumentClick(event) {
        var el = resolveTarget(step.target);
        if (!el) return;
        var node = event.target;
        if (node === el || (el.contains && el.contains(node))) {
          onNext();
        }
      }
      document.addEventListener("click", onDocumentClick, true);
      return function () {
        document.removeEventListener("click", onDocumentClick, true);
      };
    }, [hasTarget, step && step.target, step && step.interact]);

    // Bubble placement depends on both the target rect and the bubble's own
    // measured size; recompute whenever either changes.
    useEffect(function () {
      if (!rect || !bubbleSize) {
        setBubbleLayout(null);
        return;
      }
      setBubbleLayout(computeLayout(rect, bubbleSize, {
        w: window.innerWidth,
        h: window.innerHeight,
      }));
    }, [rect, bubbleSize]);

    // Report the bubble's rendered size (only when it changes, to avoid a
    // measure/render loop).
    useEffect(function () {
      var el = bubbleRef.current;
      if (!el) return;
      var w = el.offsetWidth;
      var h = el.offsetHeight;
      if (!bubbleSize || bubbleSize.w !== w || bubbleSize.h !== h) {
        setBubbleSize({ w: w, h: h });
      }
    });

    if (!step) return null;

    var stepTitle = t("tour." + guide.id + "." + step.id + ".title", null, step.id);
    var stepBody = t(step.bodyKey || ("tour." + guide.id + "." + step.id + ".body"));
    var tip = step.tipKey ? t(step.tipKey) : null;
    var interactText = step.interact === "click" ? t("tour.interact.click")
      : step.interact === "type" ? t("tour.interact.type") : null;
    var isLast = index >= total - 1;

    var scrims = null;
    if (hasTarget && rect) {
      var vw = window.innerWidth;
      var vh = window.innerHeight;
      scrims = [
        { key: "top", style: { left: 0, top: 0, width: vw, height: rect.y } },
        { key: "bottom", style: { left: 0, top: rect.y + rect.h, width: vw, height: vh - rect.y - rect.h } },
        { key: "left", style: { left: 0, top: rect.y, width: rect.x, height: rect.h } },
        { key: "right", style: { left: rect.x + rect.w, top: rect.y, width: vw - rect.x - rect.w, height: rect.h } },
      ];
    }

    return React.createElement("div", { className: "tour-spotlight", "aria-hidden": "false" },
      hasTarget && scrims && scrims.map(function (s) {
        return React.createElement("div", {
          key: s.key,
          className: "tour-scrim",
          style: Object.assign({
            background: "rgba(4, 10, 20, 0.55)",
          }, s.style),
          onMouseDown: function (event) { if (event.target === event.currentTarget) onNext(); },
        });
      }),
      hasTarget && rect && React.createElement("div", {
        className: "tour-frame",
        style: {
          left: rect.x - FRAME_GAP,
          top: rect.y - FRAME_GAP,
          width: rect.w + FRAME_GAP * 2,
          height: rect.h + FRAME_GAP * 2,
          borderRadius: frameRadius(rect.radius, FRAME_GAP),
          border: "2px solid var(--accent, #4f7cff)",
          boxShadow: "0 0 0 4px color-mix(in srgb, var(--accent, #4f7cff) 22%, transparent)",
        },
      }),
      React.createElement("div", {
        className: "tour-bubble" + (hasTarget && bubbleLayout ? "" : " tour-bubble-centered"),
        ref: bubbleRef,
        role: "dialog",
        "aria-label": stepTitle,
        style: hasTarget && bubbleLayout
          ? { left: bubbleLayout.x, top: bubbleLayout.y }
          : null,
      },
        React.createElement("div", { className: "tour-bubble-head" },
          React.createElement("span", { className: "tour-bubble-guide" }, t(guide.titleKey)),
          React.createElement("span", { className: "tour-bubble-count" }, t("tour.bubble.stepOf", { current: index + 1, total: total })),
        ),
        React.createElement("div", { className: "tour-bubble-title" }, stepTitle),
        React.createElement("div", { className: "tour-bubble-body" },
          stepBody.split("\n").map(function (line, i) {
            return React.createElement("p", { key: i }, line);
          }),
          (step.points || []).map(function (pointKey, i) {
            return React.createElement("div", { className: "tour-bubble-point", key: "p" + i },
              React.createElement("span", { className: "tour-bubble-point-bullet" }, "•"),
              React.createElement("span", null, t(pointKey)),
            );
          }),
          tip && React.createElement("div", { className: "tour-bubble-tip" }, tip),
          interactText && React.createElement("div", { className: "tour-bubble-interact" }, interactText),
          missing && React.createElement("div", { className: "tour-bubble-missing" }, t("tour.bubble.missing")),
        ),
        React.createElement("div", { className: "tour-bubble-foot" },
          React.createElement("div", { className: "tour-bubble-dots" },
            Array.from({ length: total }, function (_, i) {
              return React.createElement("span", {
                key: i,
                className: "tour-bubble-dot" + (i === index ? " active" : ""),
              });
            })
          ),
          React.createElement("div", { className: "tour-bubble-actions" },
            index > 0 && React.createElement("button", { type: "button", className: "wb-btn ghost", onClick: onPrev },
              t("tour.bubble.prev")
            ),
            missing && React.createElement("button", { type: "button", className: "wb-btn ghost", onClick: onSkipStep },
              t("tour.bubble.skipStep")
            ),
            !isLast && React.createElement("button", { type: "button", className: "wb-btn primary", onClick: onNext },
              t("tour.bubble.next")
            ),
            isLast && React.createElement("button", { type: "button", className: "wb-btn primary", onClick: onFinish },
              t("tour.bubble.finish")
            ),
            React.createElement("button", { type: "button", className: "wb-btn ghost tour-bubble-stop", onClick: onStop },
              t("tour.bubble.stop")
            ),
          ),
        )
      )
    );
  }

  root.CyreneUI.register("tour-spotlight", { Overlay: SpotlightOverlay });
})(window);
