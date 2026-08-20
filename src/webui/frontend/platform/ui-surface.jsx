// Current-renderer semantic UI surface. Explicit component registrations remain
// authoritative for product operations. A bounded accessibility projection
// adds visible, standard controls (press/value/select/scroll/context menu)
// without accepting selectors, coordinates, scripts, URLs, or raw events.
(function (root) {
  "use strict";

  var platform = root.CyreneUI;
  if (!platform) throw new Error("CyreneUI platform registry must load first");

  var ACTION_KINDS = {
    invoke: true, set_value: true, select: true, toggle: true, adjust: true,
    scroll: true, move: true, set_frame: true, open_menu: true, dismiss: true,
  };
  var instanceId = (root.crypto && typeof root.crypto.randomUUID === "function")
    ? root.crypto.randomUUID()
    : "ui_" + Date.now().toString(36) + Math.random().toString(36).slice(2);
  var snapshotId = "tree_" + instanceId.replace(/[^a-zA-Z0-9]/g, "").slice(0, 24);
  var revision = 1;
  var nodes = new Map();
  var projectedNodes = new Map();
  var actionLeasesByRevision = new Map();
  var nodeLeasesByRevision = new Map();
  var actionableSignature = "";
  var projectedLayer = { container: null, overlay: false };
  var domNodeIds = typeof WeakMap === "function" ? new WeakMap() : null;
  var domRegionIds = typeof WeakMap === "function" ? new WeakMap() : null;
  var nextDomNodeId = 1;
  var nextRegistrationSequence = 1;
  var activeScope = "main";
  var agentCursorState = {
    sequence: 0, visible: false, fading: false, idleTimer: null, fadeTimer: null, targetElement: null,
    running: false, lastActivityAt: 0, lastX: null, lastY: null,
  };
  var AGENT_CURSOR_IDLE_MS = 3000;
  var AGENT_CURSOR_FADE_IN_MS = 150;
  var AGENT_CURSOR_FADE_OUT_MS = 250;
  var AGENT_CURSOR_MOVE_MS = 180;
  var AGENT_CURSOR_DRAG_MS = 350;
  var AGENT_CURSOR_PRESS_MS = 100;
  var AGENT_CURSOR_HOTSPOT = { x: 6, y: 6 };
  var agentControlHighlightState = {
    sequence: 0, targetElement: null, hideTimer: null, fadeTimer: null,
    frame: null, visible: false,
  };
  var AGENT_CONTROL_FLOW_CYCLE_MS = 3200;
  var AGENT_CONTROL_FLOW_HOLD_MS = 3600;
  var AGENT_CONTROL_FLOW_FADE_MS = 420;
  var AGENT_CONTROL_FLOW_INSET = 3;
  var AGENT_CURSOR_SVG = '<svg width="32" height="32" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
    + '<defs><linearGradient id="cyrene-ui-agent-cursor-fill" x1="13" y1="8" x2="48" y2="52" gradientUnits="userSpaceOnUse">'
    + '<stop stop-color="#A78BFA"/><stop offset="0.52" stop-color="#6366F1"/><stop offset="1" stop-color="#22D3EE"/>'
    + '</linearGradient></defs><path d="M16.8 11.1 C13.5 9.2 10.5 11.5 11.2 15.2 L17.3 46.9 C18.2 51.8 22.5 53.1 25.2 48.9 '
    + 'L30.5 40.6 C31.6 38.9 32.7 38.2 34.7 37.8 L45.8 35.7 C50.8 34.8 51.9 30.3 47.5 27.8 L16.8 11.1Z" '
    + 'fill="url(#cyrene-ui-agent-cursor-fill)" stroke="#FFFFFF" stroke-width="2.5" stroke-linejoin="round"/></svg>';
  var STABLE_STATE_KEYS = [
    "disabled", "checked", "pressed", "selected", "expanded", "mode",
    "project_id", "session_id", "session_kind", "draft_empty",
    "submit_exposed", "pinned", "grouped",
  ];
  var surfaceKind = (function () {
    try {
      return new URLSearchParams(root.location.search).get("surface") === "quick-chat"
        ? "quick_chat"
        : "main";
    } catch (error) {
      return "main";
    }
  })();

  function clone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function agentCursorElement() {
    var doc = root.document;
    if (!doc || typeof doc.createElement !== "function") return null;
    var cursor = doc.getElementById && doc.getElementById("cyrene-ui-agent-cursor");
    if (cursor) return cursor;
    cursor = doc.createElement("div");
    cursor.id = "cyrene-ui-agent-cursor";
    cursor.setAttribute("aria-hidden", "true");
    cursor.style.cssText = [
      "position:fixed", "left:0", "top:0", "width:32px", "height:32px",
      "pointer-events:none", "user-select:none", "z-index:2147483647", "opacity:0",
      "contain:layout style paint", "will-change:transform,opacity",
    ].join(";");
    var art = doc.createElement("div");
    art.setAttribute("data-cyrene-agent-cursor-art", "true");
    art.style.cssText = "width:100%;height:100%;transform-origin:6px 6px";
    art.innerHTML = AGENT_CURSOR_SVG;
    cursor.appendChild(art);
    (doc.body || doc.documentElement).appendChild(cursor);
    if (!doc.getElementById("cyrene-ui-agent-cursor-style")) {
      var style = doc.createElement("style");
      style.id = "cyrene-ui-agent-cursor-style";
      style.textContent = "@keyframes cyrene-ui-agent-cursor-press{0%,100%{transform:scale(1)}50%{transform:scale(.92)}}"
        + "#cyrene-ui-agent-cursor [data-cyrene-agent-cursor-art].is-pressing{animation:cyrene-ui-agent-cursor-press 100ms ease-in-out 1}"
        + "@media (prefers-reduced-motion:reduce){#cyrene-ui-agent-cursor [data-cyrene-agent-cursor-art].is-pressing{animation:none!important}}";
      (doc.head || doc.documentElement).appendChild(style);
    }
    return cursor;
  }

  function clearAgentCursorTimers() {
    if (agentCursorState.idleTimer && root.clearTimeout) root.clearTimeout(agentCursorState.idleTimer);
    if (agentCursorState.fadeTimer && root.clearTimeout) root.clearTimeout(agentCursorState.fadeTimer);
    agentCursorState.idleTimer = null;
    agentCursorState.fadeTimer = null;
  }

  function hideAgentCursor(sequence) {
    if (sequence != null && Number(sequence) !== agentCursorState.sequence) return false;
    var cursor = root.document && root.document.getElementById
      ? root.document.getElementById("cyrene-ui-agent-cursor")
      : null;
    if (!cursor) return false;
    clearAgentCursorTimers();
    agentCursorState.fading = true;
    cursor.style.transition = "opacity " + AGENT_CURSOR_FADE_OUT_MS + "ms ease-in";
    cursor.style.opacity = "0";
    var current = agentCursorState.sequence;
    if (root.setTimeout) {
      agentCursorState.fadeTimer = root.setTimeout(function () {
        if (agentCursorState.sequence !== current) return;
        agentCursorState.visible = false;
        agentCursorState.fading = false;
        agentCursorState.targetElement = null;
      }, AGENT_CURSOR_FADE_OUT_MS);
    }
    return true;
  }

  function scheduleAgentCursorIdle() {
    if (agentCursorState.idleTimer && root.clearTimeout) root.clearTimeout(agentCursorState.idleTimer);
    agentCursorState.idleTimer = null;
    if (!agentCursorState.visible) return;
    var elapsed = Math.max(0, Date.now() - Number(agentCursorState.lastActivityAt || 0));
    var remaining = Math.max(0, AGENT_CURSOR_IDLE_MS - elapsed);
    var sequence = agentCursorState.sequence;
    if (remaining === 0) {
      hideAgentCursor(sequence);
      return;
    }
    if (root.setTimeout) {
      agentCursorState.idleTimer = root.setTimeout(function () {
        hideAgentCursor(sequence);
      }, remaining);
    }
  }

  function setAgentRunning(running) {
    agentCursorState.running = running === true;
    // A run can spend a long time reasoning or using a different surface.
    // Only a real cursor update may refresh visibility; lifecycle changes must
    // not pin or resurrect the previous target.
    return agentCursorState.running;
  }

  function claimAgentCursorOwner() {
    var bridge = root.cyrene && root.cyrene.agentCursor;
    if (bridge && typeof bridge.claim === "function") {
      bridge.claim("ui").catch(function () {});
    }
  }

  function showAgentCursor(point, options) {
    if (!point) return null;
    claimAgentCursorOwner();
    var cursor = agentCursorElement();
    if (!cursor) return null;
    var config = options || {};
    clearAgentCursorTimers();
    agentCursorState.sequence += 1;
    var sequence = agentCursorState.sequence;
    var wasVisible = agentCursorState.visible;
    var wasFading = agentCursorState.fading;
    var moved = !wasVisible || agentCursorState.lastX !== point.x || agentCursorState.lastY !== point.y;
    agentCursorState.visible = true;
    agentCursorState.fading = false;
    agentCursorState.lastActivityAt = Date.now();
    agentCursorState.lastX = point.x;
    agentCursorState.lastY = point.y;
    agentCursorState.targetElement = config.element || null;
    var position = "translate3d(" + Math.round(point.x - AGENT_CURSOR_HOTSPOT.x) + "px,"
      + Math.round(point.y - AGENT_CURSOR_HOTSPOT.y) + "px,0)";
    if (!wasVisible) {
      cursor.style.transition = "none";
      cursor.style.transform = position;
      cursor.style.opacity = "0";
      void cursor.offsetWidth;
      var frame = root.requestAnimationFrame || function (callback) { callback(); };
      frame(function () { frame(function () {
        if (agentCursorState.sequence !== sequence) return;
        cursor.style.transition = "opacity " + AGENT_CURSOR_FADE_IN_MS + "ms ease-out";
        cursor.style.opacity = "1";
      }); });
    } else {
      if (wasFading) {
        cursor.style.transition = "none";
        cursor.style.opacity = "1";
        void cursor.offsetWidth;
      }
      if (moved) {
        cursor.style.transition = "transform " + Number(config.moveDurationMs == null ? AGENT_CURSOR_MOVE_MS : config.moveDurationMs)
          + "ms cubic-bezier(.2,.8,.2,1),opacity " + AGENT_CURSOR_FADE_OUT_MS + "ms ease-in";
        cursor.style.transform = position;
      }
      cursor.style.opacity = "1";
    }
    if (config.press === true && typeof cursor.querySelector === "function") {
      var art = cursor.querySelector("[data-cyrene-agent-cursor-art]");
      if (art) {
        art.classList.remove("is-pressing");
        void art.offsetWidth;
        art.classList.add("is-pressing");
        if (root.setTimeout) root.setTimeout(function () {
          if (agentCursorState.sequence === sequence) art.classList.remove("is-pressing");
        }, AGENT_CURSOR_PRESS_MS);
      }
    }
    scheduleAgentCursorIdle();
    return {
      sequence: sequence,
      first: !wasVisible,
      moved: moved,
      waitMs: !wasVisible
        ? AGENT_CURSOR_FADE_IN_MS + 34
        : (moved ? Number(config.moveDurationMs == null ? AGENT_CURSOR_MOVE_MS : config.moveDurationMs) : 0),
    };
  }

  function delayCursor(ms) {
    return new Promise(function (resolve) {
      if (root.setTimeout) root.setTimeout(resolve, ms);
      else resolve();
    });
  }

  function agentControlHighlightElement() {
    var doc = root.document;
    if (!doc || typeof doc.createElement !== "function") return null;
    var highlight = doc.getElementById && doc.getElementById("cyrene-ui-agent-control-highlight");
    if (highlight) return highlight;
    highlight = doc.createElement("div");
    highlight.id = "cyrene-ui-agent-control-highlight";
    highlight.setAttribute("aria-hidden", "true");
    highlight.style.cssText = [
      "position:fixed", "left:0", "top:0", "width:0", "height:0",
      "box-sizing:border-box", "padding:2px", "pointer-events:none", "user-select:none",
      "z-index:2147483646", "opacity:0", "display:none",
      "background-size:280% 100%", "background-position:145% 0",
      "-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0)",
      "-webkit-mask-composite:xor",
      "mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0)",
      "mask-composite:exclude", "contain:layout style paint",
      "will-change:left,top,width,height,background-position,opacity",
    ].join(";");
    (doc.body || doc.documentElement).appendChild(highlight);
    if (!doc.getElementById("cyrene-ui-agent-control-highlight-style")) {
      var style = doc.createElement("style");
      style.id = "cyrene-ui-agent-control-highlight-style";
      style.textContent = "@keyframes cyrene-ui-agent-control-flow{"
        + "0%{background-position:145% 0;opacity:.38}"
        + "45%{opacity:.9}"
        + "100%{background-position:-145% 0;opacity:.38}}"
        + "#cyrene-ui-agent-control-highlight.is-active{"
        + "background-image:linear-gradient(104deg,transparent 22%,"
        + "color-mix(in srgb,var(--wb-accent,#8b5cf6) 84%,transparent) 44%,"
        + "color-mix(in srgb,#22d3ee 70%,var(--wb-accent,#8b5cf6)) 52%,transparent 78%);"
        + "animation:cyrene-ui-agent-control-flow " + AGENT_CONTROL_FLOW_CYCLE_MS + "ms cubic-bezier(.4,0,.2,1) infinite}"
        + "#cyrene-ui-agent-control-highlight.is-fading{opacity:0!important;"
        + "transition:opacity " + AGENT_CONTROL_FLOW_FADE_MS + "ms ease-in}"
        + "@media (prefers-reduced-motion:reduce){#cyrene-ui-agent-control-highlight.is-active{"
        + "animation:none!important;background:color-mix(in srgb,var(--wb-accent,#8b5cf6) 48%,transparent)!important;"
        + "opacity:.76!important}}";
      (doc.head || doc.documentElement).appendChild(style);
    }
    return highlight;
  }

  function clearAgentControlHighlightTimers() {
    if (agentControlHighlightState.hideTimer && root.clearTimeout) {
      root.clearTimeout(agentControlHighlightState.hideTimer);
    }
    if (agentControlHighlightState.fadeTimer && root.clearTimeout) {
      root.clearTimeout(agentControlHighlightState.fadeTimer);
    }
    if (agentControlHighlightState.frame && root.cancelAnimationFrame) {
      root.cancelAnimationFrame(agentControlHighlightState.frame);
    }
    agentControlHighlightState.hideTimer = null;
    agentControlHighlightState.fadeTimer = null;
    agentControlHighlightState.frame = null;
  }

  function positionAgentControlHighlight(sequence) {
    if (sequence != null && Number(sequence) !== agentControlHighlightState.sequence) return false;
    var element = agentControlHighlightState.targetElement;
    var highlight = root.document && root.document.getElementById
      ? root.document.getElementById("cyrene-ui-agent-control-highlight")
      : null;
    if (!element || !highlight || !elementVisible(element) || typeof element.getBoundingClientRect !== "function") {
      hideAgentControlHighlight(sequence, true);
      return false;
    }
    var rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) {
      hideAgentControlHighlight(sequence, true);
      return false;
    }
    var inset = AGENT_CONTROL_FLOW_INSET;
    highlight.style.left = Math.round(rect.left - inset) + "px";
    highlight.style.top = Math.round(rect.top - inset) + "px";
    highlight.style.width = Math.round(rect.width + inset * 2) + "px";
    highlight.style.height = Math.round(rect.height + inset * 2) + "px";
    var computed;
    try { computed = root.getComputedStyle ? root.getComputedStyle(element) : null; } catch (error) { computed = null; }
    var radius = computed && String(computed.borderRadius || "").trim();
    highlight.style.borderRadius = radius && radius !== "0px" ? radius : "8px";
    return true;
  }

  function refreshAgentControlHighlight() {
    if (!agentControlHighlightState.visible || agentControlHighlightState.frame) return;
    var frame = root.requestAnimationFrame || function (callback) { callback(); return null; };
    var sequence = agentControlHighlightState.sequence;
    agentControlHighlightState.frame = frame(function () {
      agentControlHighlightState.frame = null;
      positionAgentControlHighlight(sequence);
    });
  }

  function hideAgentControlHighlight(sequence, immediate) {
    if (sequence != null && Number(sequence) !== agentControlHighlightState.sequence) return false;
    var highlight = root.document && root.document.getElementById
      ? root.document.getElementById("cyrene-ui-agent-control-highlight")
      : null;
    clearAgentControlHighlightTimers();
    if (!highlight) return false;
    var current = agentControlHighlightState.sequence;
    if (immediate === true) {
      highlight.classList.remove("is-active");
      highlight.classList.remove("is-fading");
      highlight.style.display = "none";
      highlight.style.opacity = "0";
      agentControlHighlightState.visible = false;
      agentControlHighlightState.targetElement = null;
      return true;
    }
    highlight.classList.add("is-fading");
    if (root.setTimeout) {
      agentControlHighlightState.fadeTimer = root.setTimeout(function () {
        if (agentControlHighlightState.sequence !== current) return;
        highlight.classList.remove("is-active");
        highlight.classList.remove("is-fading");
        highlight.style.display = "none";
        highlight.style.opacity = "0";
        agentControlHighlightState.visible = false;
        agentControlHighlightState.targetElement = null;
        agentControlHighlightState.fadeTimer = null;
      }, AGENT_CONTROL_FLOW_FADE_MS);
    }
    return true;
  }

  function showAgentControlHighlight(element, nodeId, actionId) {
    if (!element || !elementVisible(element)) return null;
    var highlight = agentControlHighlightElement();
    if (!highlight) return null;
    clearAgentControlHighlightTimers();
    agentControlHighlightState.sequence += 1;
    var sequence = agentControlHighlightState.sequence;
    agentControlHighlightState.targetElement = element;
    agentControlHighlightState.visible = true;
    highlight.setAttribute("data-node-id", String(nodeId || ""));
    highlight.setAttribute("data-action-id", String(actionId || ""));
    highlight.style.display = "block";
    highlight.style.opacity = "1";
    highlight.style.transition = "none";
    highlight.classList.remove("is-active");
    highlight.classList.remove("is-fading");
    if (!positionAgentControlHighlight(sequence)) return null;
    void highlight.offsetWidth;
    highlight.style.transition = "";
    highlight.classList.add("is-active");
    return sequence;
  }

  function settleAgentControlHighlight(sequence) {
    if (sequence == null || Number(sequence) !== agentControlHighlightState.sequence) return;
    if (agentControlHighlightState.hideTimer && root.clearTimeout) {
      root.clearTimeout(agentControlHighlightState.hideTimer);
    }
    if (root.setTimeout) {
      agentControlHighlightState.hideTimer = root.setTimeout(function () {
        hideAgentControlHighlight(sequence, false);
      }, AGENT_CONTROL_FLOW_HOLD_MS);
    }
  }

  function touch() {
    // Kept for compatibility with older components. Revision is advanced by
    // refreshRevision only when the current actionable semantic manifest
    // actually changes.
    return revision;
  }

  function normalizeAction(action) {
    if (!action || !action.action_id || !ACTION_KINDS[action.kind]) {
      throw new Error("UI action requires a stable action_id and allowed kind");
    }
    return {
      action_id: String(action.action_id),
      kind: String(action.kind),
      risk: String(action.risk || "R1"),
      gesture_aliases: Array.isArray(action.gesture_aliases)
        ? action.gesture_aliases.map(String)
        : [],
      input_schema: clone(action.input_schema || {}),
      requires_capability: String(action.requires_capability || ""),
      outcome: clone(action.outcome || inferredOutcome(action)),
    };
  }

  function inferredOutcome(action) {
    var kind = String(action && action.kind || "");
    var actionId = String(action && action.action_id || "");
    if (kind === "open_menu") return { effect: "may_open_menu", inspect_after: true };
    if (kind === "dismiss") return { effect: "may_close_current_layer", inspect_after: true };
    if (kind === "scroll") return { effect: "changes_visible_viewport", inspect_after: true };
    if (kind === "move" || kind === "set_frame" || kind === "adjust") {
      return { effect: "changes_layout", inspect_after: true };
    }
    if (kind === "set_value" || kind === "select" || kind === "toggle") {
      return { effect: "changes_component_state", inspect_after: true };
    }
    if (/^(open|new|show|switch|navigate|maximize|restore)/.test(actionId)) {
      return { effect: "may_change_current_interface", inspect_after: true };
    }
    return { effect: "declared_component_action", inspect_after: true };
  }

  function register(spec) {
    if (!spec || !spec.node_id || typeof spec.get_node !== "function") {
      throw new Error("UI surface registrations require node_id and get_node");
    }
    var nodeId = String(spec.node_id);
    var handlers = spec.handlers || {};
    var actions = (spec.actions || []).map(normalizeAction);
    actions.forEach(function (action) {
      if (!action.requires_capability && typeof handlers[action.action_id] !== "function") {
        throw new Error("UI action " + action.action_id + " has no explicit semantic handler");
      }
    });
    var token = {
      node_id: nodeId,
      parent_id: String(spec.parent_id || "root"),
      scope: String(spec.scope || "main"),
      affects_revision: spec.affects_revision !== false,
      order: Number.isFinite(Number(spec.order)) ? Number(spec.order) : 1000,
      sequence: nextRegistrationSequence++,
      get_node: spec.get_node,
      get_element: typeof spec.get_element === "function" ? spec.get_element : null,
      get_highlight_element: typeof spec.get_highlight_element === "function"
        ? spec.get_highlight_element
        : null,
      actions: actions,
      handlers: handlers,
    };
    nodes.set(nodeId, token);
    return function unregister() {
      if (nodes.get(nodeId) === token) {
        nodes.delete(nodeId);
      }
    };
  }

  function elementVisible(element) {
    if (!element || element.nodeType !== 1) return false;
    var ancestor = element;
    while (ancestor && ancestor.nodeType === 1) {
      if (
        ancestor.hidden
        || ancestor.inert
        || ancestor.getAttribute("aria-hidden") === "true"
      ) return false;
      var style;
      try { style = root.getComputedStyle ? root.getComputedStyle(ancestor) : null; } catch (error) { style = null; }
      if (style && (style.display === "none" || style.visibility === "hidden")) return false;
      ancestor = ancestor.parentElement;
    }
    if (typeof element.getBoundingClientRect === "function") {
      var rect = element.getBoundingClientRect();
      if (rect && rect.width <= 0 && rect.height <= 0) return false;
    }
    return true;
  }

  function projectionRoot() {
    var doc = root.document;
    if (!doc || typeof doc.querySelector !== "function") return null;
    if (activeScope === "settings") {
      var settings = doc.querySelector(".settings-overlay-panel");
      if (settings && elementVisible(settings)) return settings;
    }
    var surfaces = Array.prototype.slice.call(doc.querySelectorAll(
      '[data-cyrene-surface-root="true"], [role="dialog"][aria-modal="true"], [role="alertdialog"][aria-modal="true"], [role="menu"]'
    )).filter(elementVisible);
    if (surfaces.length) return surfaces[surfaces.length - 1];
    return doc.getElementById("root") || doc.body || null;
  }

  function isBaseProjectionRoot(container) {
    var doc = root.document;
    if (!doc || !container) return true;
    return container === doc.body || container === doc.getElementById("root");
  }

  function rectanglesIntersect(left, right) {
    if (!left || !right) return true;
    function edges(rect) {
      var leftEdge = Number(rect.left || 0);
      var topEdge = Number(rect.top || 0);
      return {
        left: leftEdge,
        top: topEdge,
        right: Number.isFinite(Number(rect.right)) ? Number(rect.right) : leftEdge + Number(rect.width || 0),
        bottom: Number.isFinite(Number(rect.bottom)) ? Number(rect.bottom) : topEdge + Number(rect.height || 0),
      };
    }
    var a = edges(left);
    var b = edges(right);
    return a.right > b.left && a.left < b.right
      && a.bottom > b.top && a.top < b.bottom;
  }

  function elementInCurrentViewport(element, container) {
    if (!elementVisible(element) || typeof element.getBoundingClientRect !== "function") return false;
    var rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    var viewportWidth = Number(root.innerWidth || 0);
    var viewportHeight = Number(root.innerHeight || 0);
    if (
      viewportWidth > 0 && viewportHeight > 0
      && !rectanglesIntersect(rect, { left: 0, top: 0, right: viewportWidth, bottom: viewportHeight })
    ) return false;
    var ancestor = element.parentElement;
    while (ancestor && ancestor.nodeType === 1) {
      var clips = Number(ancestor.scrollHeight || 0) > Number(ancestor.clientHeight || 0) + 2
        || Number(ancestor.scrollWidth || 0) > Number(ancestor.clientWidth || 0) + 2;
      if (clips && typeof ancestor.getBoundingClientRect === "function") {
        if (!rectanglesIntersect(rect, ancestor.getBoundingClientRect())) return false;
      }
      if (ancestor === container) break;
      ancestor = ancestor.parentElement;
    }
    if (
      container && container !== element && !isBaseProjectionRoot(container)
      && typeof container.getBoundingClientRect === "function"
      && !rectanglesIntersect(rect, container.getBoundingClientRect())
    ) return false;
    return true;
  }

  function entryElement(item) {
    if (!item || !item.entry) return null;
    var entry = item.entry;
    if (entry.get_element) {
      try {
        var explicit = entry.get_element();
        if (explicit && elementVisible(explicit)) return explicit;
      } catch (error) {}
    }
    var container = projectionRoot();
    if (!container || typeof container.querySelectorAll !== "function") return null;
    var identified = Array.prototype.slice.call(container.querySelectorAll("[data-cyrene-node-id]"));
    var exact = identified.find(function (element) {
      return String(element.getAttribute("data-cyrene-node-id") || "") === entry.node_id;
    });
    if (exact && elementVisible(exact)) return exact;
    var name = String(item.value && item.value.name || "").replace(/\s+/g, " ").trim();
    if (!name) return null;
    var controls = Array.prototype.slice.call(container.querySelectorAll(
      "button,input,textarea,select,[role=button],[role=menuitem],[role=menuitemradio],[role=tab],[aria-label]"
    ));
    return controls.find(function (element) {
      return elementInCurrentViewport(element, container) && projectedName(element) === name;
    }) || null;
  }

  function entryHighlightElement(item) {
    if (item && item.entry && item.entry.get_highlight_element) {
      try {
        var explicit = item.entry.get_highlight_element();
        if (explicit && elementVisible(explicit)) return explicit;
      } catch (error) {}
    }
    return entryElement(item);
  }

  function visibleCenter(element) {
    if (!element || typeof element.getBoundingClientRect !== "function" || !elementVisible(element)) return null;
    var rect = element.getBoundingClientRect();
    var left = Number(rect.left || 0);
    var top = Number(rect.top || 0);
    var right = Number.isFinite(Number(rect.right)) ? Number(rect.right) : left + Number(rect.width || 0);
    var bottom = Number.isFinite(Number(rect.bottom)) ? Number(rect.bottom) : top + Number(rect.height || 0);
    var viewportWidth = Number(root.innerWidth || 0);
    var viewportHeight = Number(root.innerHeight || 0);
    if (viewportWidth > 0) { left = Math.max(0, left); right = Math.min(viewportWidth, right); }
    if (viewportHeight > 0) { top = Math.max(0, top); bottom = Math.min(viewportHeight, bottom); }
    var ancestor = element.parentElement;
    while (ancestor && ancestor.nodeType === 1) {
      var clips = Number(ancestor.scrollHeight || 0) > Number(ancestor.clientHeight || 0) + 2
        || Number(ancestor.scrollWidth || 0) > Number(ancestor.clientWidth || 0) + 2;
      if (clips && typeof ancestor.getBoundingClientRect === "function") {
        var clip = ancestor.getBoundingClientRect();
        var clipLeft = Number(clip.left || 0);
        var clipTop = Number(clip.top || 0);
        left = Math.max(left, clipLeft);
        top = Math.max(top, clipTop);
        right = Math.min(right, Number.isFinite(Number(clip.right)) ? Number(clip.right) : clipLeft + Number(clip.width || 0));
        bottom = Math.min(bottom, Number.isFinite(Number(clip.bottom)) ? Number(clip.bottom) : clipTop + Number(clip.height || 0));
      }
      ancestor = ancestor.parentElement;
    }
    if (!(right > left && bottom > top)) return null;
    return { x: Math.round((left + right) / 2), y: Math.round((top + bottom) / 2) };
  }

  function entryCenter(item) {
    return visibleCenter(entryElement(item));
  }

  function projectedNodeId(element) {
    if (domNodeIds && domNodeIds.has(element)) return domNodeIds.get(element);
    var explicitNodeId = String(element.getAttribute("data-cyrene-node-id") || "")
      .replace(/[^a-zA-Z0-9_-]/g, "_")
      .slice(0, 100);
    if (explicitNodeId && !nodes.has(explicitNodeId)) {
      if (domNodeIds) domNodeIds.set(element, explicitNodeId);
      return explicitNodeId;
    }
    var hint = String(
      element.id
      || element.getAttribute("name")
      || "control"
    ).replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 64) || "control";
    var id = "dom_" + hint + "_" + nextDomNodeId++;
    if (domNodeIds) domNodeIds.set(element, id);
    return id;
  }

  function projectedRegionId(element, fallback) {
    if (domRegionIds && domRegionIds.has(element)) return domRegionIds.get(element);
    var hint = String(
      element && (
        element.getAttribute("aria-label")
        || element.getAttribute("role")
        || element.id
        || element.tagName
      ) || fallback || "surface"
    ).replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 64) || "surface";
    var id = "dom_region_" + hint + "_" + nextDomNodeId++;
    if (domRegionIds && element) domRegionIds.set(element, id);
    return id;
  }

  function projectedName(element) {
    var text = String(
      element.getAttribute("aria-label")
      || element.getAttribute("title")
      || element.innerText
      || element.getAttribute("placeholder")
      || element.getAttribute("name")
      || element.id
      || "Control"
    ).replace(/\s+/g, " ").trim();
    return text.slice(0, 240) || "Control";
  }

  function projectedRisk(element) {
    var declared = String(element.getAttribute("data-cyrene-risk") || "");
    if (declared === "R1" || declared === "R2" || declared === "R3") return declared;
    if (element.classList && (
      element.classList.contains("danger")
      || element.classList.contains("is-danger")
    )) return "R3";
    return activeScope === "settings" ? "R2" : "R1";
  }

  function projectedRole(element) {
    var declared = String(element.getAttribute("role") || "");
    if (declared) return declared;
    var tag = String(element.tagName || "").toLowerCase();
    var type = String(element.getAttribute("type") || "").toLowerCase();
    if (tag === "button") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input" && (type === "checkbox" || type === "radio")) return type;
    if (tag === "input") return "textbox";
    return "listitem";
  }

  function setProjectedValue(element, value) {
    var tag = String(element.tagName || "").toLowerCase();
    var prototype = tag === "textarea" ? root.HTMLTextAreaElement && root.HTMLTextAreaElement.prototype
      : tag === "select" ? root.HTMLSelectElement && root.HTMLSelectElement.prototype
        : root.HTMLInputElement && root.HTMLInputElement.prototype;
    var descriptor = prototype && Object.getOwnPropertyDescriptor(prototype, "value");
    if (descriptor && typeof descriptor.set === "function") descriptor.set.call(element, value);
    else element.value = value;
    element.dispatchEvent(new root.Event("input", { bubbles: true }));
    element.dispatchEvent(new root.Event("change", { bubbles: true }));
  }

  function projectedActions(element, role) {
    if (element.disabled || element.readOnly || element.getAttribute("aria-disabled") === "true") return [];
    var risk = projectedRisk(element);
    var actions = [];
    var tag = String(element.tagName || "").toLowerCase();
    var type = String(element.getAttribute("type") || "").toLowerCase();
    if (tag === "button" || role === "button" || role.indexOf("menuitem") === 0 || role === "tab") {
      actions.push(normalizeAction({
        action_id: "invoke", kind: "invoke", risk: risk,
        gesture_aliases: ["press", "keyboard"],
      }));
    } else if (tag === "input" && (type === "checkbox" || type === "radio")) {
      actions.push(normalizeAction({
        action_id: "toggle", kind: "toggle", risk: risk,
        gesture_aliases: ["press", "keyboard"],
      }));
    } else if (tag === "select") {
      actions.push(normalizeAction({
        action_id: "select", kind: "select", risk: risk,
        gesture_aliases: ["selection", "keyboard"],
        input_schema: { value: "text<=400" },
      }));
    } else if (tag === "input" && type === "password" && element.getAttribute("data-cyrene-agent-secret-input") === "true") {
      actions.push(normalizeAction({
        action_id: "set_secret", kind: "set_value", risk: risk,
        gesture_aliases: ["text_input"],
        input_schema: { secret_value: "text<=4000" },
      }));
    } else if (tag === "input" || tag === "textarea") {
      actions.push(normalizeAction({
        action_id: "set_value", kind: "set_value", risk: risk,
        gesture_aliases: ["text_input"],
        input_schema: { value: "text<=4000" },
      }));
    }
    if (element.getAttribute("data-cyrene-context-menu") === "true") {
      actions.push(normalizeAction({
        action_id: "open_menu", kind: "open_menu", risk: risk,
        gesture_aliases: ["context_menu"],
      }));
    }
    return actions;
  }

  function projectedHandlers(element, role, isScrollable) {
    var handlers = {};
    var tag = String(element.tagName || "").toLowerCase();
    var type = String(element.getAttribute("type") || "").toLowerCase();
    if (tag === "button" || role === "button" || role.indexOf("menuitem") === 0 || role === "tab") {
      handlers.invoke = function () { element.click(); };
    } else if (tag === "input" && (type === "checkbox" || type === "radio")) {
      handlers.toggle = function () { element.click(); };
    } else if (tag === "select") {
      handlers.select = function (input) {
        var value = String(input.value || "");
        var exists = Array.prototype.some.call(element.options || [], function (option) {
          return String(option.value) === value;
        });
        if (!exists) throw new Error("unknown option");
        setProjectedValue(element, value);
      };
    } else if (tag === "input" && type === "password" && element.getAttribute("data-cyrene-agent-secret-input") === "true") {
      handlers.set_secret = function (input) { setProjectedValue(element, String(input.secret_value || "")); };
    } else if (tag === "input" || tag === "textarea") {
      handlers.set_value = function (input) { setProjectedValue(element, String(input.value || "")); };
    }
    if (element.getAttribute("data-cyrene-context-menu") === "true") {
      handlers.open_menu = function () {
        var rect = typeof element.getBoundingClientRect === "function"
          ? element.getBoundingClientRect()
          : { left: 0, top: 0, width: 0, height: 0 };
        element.dispatchEvent(new root.MouseEvent("contextmenu", {
          bubbles: true, cancelable: true, button: 2,
          clientX: Math.round(rect.left + rect.width / 2),
          clientY: Math.round(rect.top + rect.height / 2),
        }));
      };
    }
    if (isScrollable) {
      handlers.scroll_page = function (input) {
        var delta = Number(input.delta || 0);
        if (typeof element.scrollBy === "function") element.scrollBy({ top: delta, behavior: "auto" });
        else element.scrollTop = Number(element.scrollTop || 0) + delta;
      };
    }
    return handlers;
  }

  function semanticRegion(element, container) {
    var current = element && element.parentElement;
    while (current && current.nodeType === 1) {
      var tag = String(current.tagName || "").toLowerCase();
      var role = String(current.getAttribute("role") || "").toLowerCase();
      if (
        tag === "main" || tag === "nav" || tag === "aside" || tag === "header"
        || tag === "footer" || tag === "form" || tag === "section"
        || role === "dialog" || role === "alertdialog" || role === "menu"
        || role === "navigation" || role === "form" || role === "region"
      ) return current;
      if (current === container) break;
      current = current.parentElement;
    }
    return container;
  }

  function projectedRegionRole(element, isLayerRoot) {
    var role = String(element && element.getAttribute("role") || "");
    if (role) return role;
    var tag = String(element && element.tagName || "").toLowerCase();
    if (tag === "nav") return "navigation";
    if (tag === "form") return "form";
    if (tag === "main") return "main";
    return isLayerRoot ? "region" : "group";
  }

  function refreshProjectedNodes() {
    var container = projectionRoot();
    var next = new Map();
    projectedLayer = { container: container, overlay: !!container && !isBaseProjectionRoot(container) };
    if (!container || typeof container.querySelectorAll !== "function") {
      projectedNodes = next;
      return;
    }
    var explicitElements = new Set();
    var explicitIdsByElement = new Map();
    nodes.forEach(function (entry) {
      if (!entry.get_element) return;
      var element;
      try { element = entry.get_element(); } catch (error) { element = null; }
      if (element) {
        explicitElements.add(element);
        explicitIdsByElement.set(element, entry.node_id);
      }
    });
    var controls = Array.prototype.slice.call(container.querySelectorAll(
      'button, input:not([type="password"]):not([type="file"]):not([type="hidden"]), textarea, select, [role="button"], [role="menuitem"], [role="menuitemradio"], [role="tab"], [data-cyrene-context-menu="true"]'
    ));
    var all = Array.prototype.slice.call(container.querySelectorAll("*"));
    all.forEach(function (element) {
      if (
        Number(element.scrollHeight || 0) > Number(element.clientHeight || 0) + 2
        || Number(element.scrollWidth || 0) > Number(element.clientWidth || 0) + 2
      ) controls.push(element);
    });
    var seen = new Set();
    function ensureRegion(regionElement, explicitParentId) {
      var regionId = projectedRegionId(regionElement, "current_surface");
      if (!next.has(regionId)) {
        var isLayerRoot = regionElement === container;
        next.set(regionId, {
          node_id: regionId,
          parent_id: explicitParentId || "root",
          scope: activeScope,
          affects_revision: !(
            regionElement
            && typeof regionElement.closest === "function"
            && regionElement.closest('[data-cyrene-revision-volatile="true"]')
          ),
          order: 1900 + next.size,
          sequence: nextRegistrationSequence + next.size,
          get_element: function () { return regionElement; },
          get_node: function () {
            return {
              role: projectedRegionRole(regionElement, isLayerRoot),
              name: isLayerRoot
                ? (projectedLayer.overlay ? "Current overlay controls" : "Current interface controls")
                : projectedName(regionElement),
              state: {},
            };
          },
          actions: [],
          handlers: {},
        });
      }
      return regionId;
    }
    controls.forEach(function (element) {
      if (seen.has(element) || explicitElements.has(element) || !elementInCurrentViewport(element, container)) return;
      seen.add(element);
      var explicitNodeId = String(element.getAttribute("data-cyrene-node-id") || "");
      if (explicitNodeId && nodes.has(explicitNodeId)) return;
      var explicitOwner = typeof element.closest === "function"
        ? element.closest("[data-cyrene-node-id]")
        : null;
      var explicitOwnerId = explicitOwner
        ? String(explicitOwner.getAttribute("data-cyrene-node-id") || "")
        : "";
      if (explicitOwnerId && nodes.has(explicitOwnerId)) return;
      var tag = String(element.tagName || "").toLowerCase();
      var inputType = String(element.getAttribute("type") || "").toLowerCase();
      var agentSecretInput = tag === "input"
        && inputType === "password"
        && element.getAttribute("data-cyrene-agent-secret-input") === "true";
      if (tag === "input" && ((inputType === "password" && !agentSecretInput) || inputType === "file" || inputType === "hidden")) return;
      if (
        element.getAttribute("data-cyrene-secret") === "true"
        || element.getAttribute("data-cyrene-user-ceremony") === "true"
      ) return;
      var isScrollable = Number(element.scrollHeight || 0) > Number(element.clientHeight || 0) + 2
        || Number(element.scrollWidth || 0) > Number(element.clientWidth || 0) + 2;
      var role = projectedRole(element);
      var actions = projectedActions(element, role);
      if (isScrollable) actions.push(normalizeAction({
        action_id: "scroll_page", kind: "scroll", risk: "R1",
        gesture_aliases: ["wheel", "keyboard"],
        input_schema: { delta: "-2000..2000" },
      }));
      if (!actions.length) return;
      var nodeId = projectedNodeId(element);
      var handlers = projectedHandlers(element, role, isScrollable);
      var affectsRevision = !(
        typeof element.closest === "function"
        && element.closest('[data-cyrene-revision-volatile="true"]')
      );
      var explicitParentId = "";
      var ancestor = element.parentElement;
      while (ancestor && ancestor.nodeType === 1) {
        if (explicitIdsByElement.has(ancestor)) {
          explicitParentId = explicitIdsByElement.get(ancestor);
          break;
        }
        if (ancestor === container) break;
        ancestor = ancestor.parentElement;
      }
      var parentId = explicitParentId || ensureRegion(semanticRegion(element, container), "");
      next.set(nodeId, {
        node_id: nodeId,
        parent_id: parentId,
        scope: activeScope,
        affects_revision: affectsRevision,
        order: 2000 + next.size,
        sequence: nextRegistrationSequence + next.size,
        get_element: function () { return element; },
        get_node: function () {
          var type = String(element.getAttribute("type") || "").toLowerCase();
          var elementTag = String(element.tagName || "").toLowerCase();
          var value = "";
          if (elementTag === "select") value = String(element.value || "");
          else if (role === "textbox" && type !== "password") value = String(element.value || "").slice(0, 4000);
          var state = {
            disabled: !!element.disabled,
            checked: typeof element.checked === "boolean" ? element.checked : undefined,
            pressed: element.getAttribute("aria-pressed") == null
              ? undefined
              : element.getAttribute("aria-pressed") === "true",
            expanded: element.getAttribute("aria-expanded") == null
              ? undefined
              : element.getAttribute("aria-expanded") === "true",
            has_popup: element.getAttribute("aria-haspopup") || undefined,
            scroll_top: isScrollable ? Number(element.scrollTop || 0) : undefined,
            scroll_height: isScrollable ? Number(element.scrollHeight || 0) : undefined,
            client_height: isScrollable ? Number(element.clientHeight || 0) : undefined,
          };
          if (elementTag === "select") {
            state.options = Array.prototype.slice.call(element.options || [], 0, 200).map(function (option) {
              return {
                value: String(option.value || "").slice(0, 400),
                label: String(option.label || option.text || option.value || "").replace(/\s+/g, " ").trim().slice(0, 240),
                disabled: !!option.disabled,
              };
            });
          } else if (role === "textbox") {
            state.input_type = type || (elementTag === "textarea" ? "textarea" : "text");
            var min = element.getAttribute("min");
            var max = element.getAttribute("max");
            var maxLength = Number(element.maxLength);
            if (min != null && min !== "") state.min = String(min).slice(0, 80);
            if (max != null && max !== "") state.max = String(max).slice(0, 80);
            if (Number.isInteger(maxLength) && maxLength >= 0) state.max_length = maxLength;
          }
          return {
            role: role,
            name: projectedName(element),
            value_summary: value,
            state: state,
          };
        },
        actions: actions,
        handlers: handlers,
      });
    });
    projectedNodes = next;
  }

  function setScope(scope) {
    var next = String(scope || "main");
    if (next !== activeScope) {
      hideAgentCursor();
      activeScope = next;
    }
  }

  function visibleEntries() {
    refreshProjectedNodes();
    var result = [];
    nodes.forEach(function (entry) {
      if (activeScope === "main" ? entry.scope !== "main" : entry.scope !== activeScope) return;
      if (activeScope === "main" && projectedLayer.overlay) return;
      var value = entry.get_node();
      if (!value || value.visible === false) return;
      result.push({ entry: entry, value: value });
    });
    if (activeScope === "main" || projectedLayer.overlay) {
      projectedNodes.forEach(function (entry) {
        var value = entry.get_node();
        if (value && value.visible !== false) result.push({ entry: entry, value: value });
      });
    }
    result.sort(function (left, right) {
      return Number(left.entry.order || 0) - Number(right.entry.order || 0)
        || Number(left.entry.sequence || 0) - Number(right.entry.sequence || 0);
    });
    refreshRevision(result);
    rememberActionLeases(result);
    rememberNodeLeases(result);
    return result;
  }

  function visibleSessionState(entries) {
    var preferredNodeIds = [
      "chat_composer_input", "task_composer_input",
      "chat_composer_submit", "task_composer_submit",
    ];
    var item = null;
    preferredNodeIds.some(function (nodeId) {
      item = entries.find(function (candidate) {
        return candidate.entry.node_id === nodeId
          && candidate.value && candidate.value.state
          && candidate.value.state.session_id;
      });
      return !!item;
    });
    if (!item) {
      item = entries.find(function (candidate) {
        return candidate.value && candidate.value.state
          && candidate.value.state.session_id;
      });
    }
    var state = item && item.value && item.value.state || {};
    return {
      visible_session_id: String(state.session_id || ""),
      visible_session_kind: String(state.session_kind || ""),
    };
  }

  function stableSemanticState(value) {
    var state = value && value.state || {};
    var semanticState = {};
    STABLE_STATE_KEYS.forEach(function (key) {
      if (state[key] !== undefined) semanticState[key] = state[key];
    });
    return semanticState;
  }

  function actionLeaseKey(nodeId, actionId) {
    return String(nodeId || "") + "\u0000" + String(actionId || "");
  }

  function actionLeaseSignature(item, action) {
    return JSON.stringify({
      node_id: item.entry.node_id,
      parent_id: item.entry.parent_id,
      scope: item.entry.scope,
      state: stableSemanticState(item.value),
      action: action,
    });
  }

  function nodeLeaseSignature(item) {
    if (!item || !item.entry || !item.value) return "";
    return JSON.stringify({
      node_id: item.entry.node_id,
      parent_id: item.entry.parent_id,
      scope: item.entry.scope,
      role: String(item.value.role || "group"),
      name: String(item.value.name || ""),
      value_summary: item.value.value_summary == null ? "" : String(item.value.value_summary),
      state: clone(item.value.state || {}),
      actions: item.entry.actions,
    });
  }

  function rememberActionLeases(entries) {
    var leases = new Map();
    entries.forEach(function (item) {
      item.entry.actions.forEach(function (action) {
        leases.set(
          actionLeaseKey(item.entry.node_id, action.action_id),
          actionLeaseSignature(item, action),
        );
      });
    });
    actionLeasesByRevision.set(revision, leases);
    while (actionLeasesByRevision.size > 64) {
      actionLeasesByRevision.delete(actionLeasesByRevision.keys().next().value);
    }
  }

  function rememberNodeLeases(entries) {
    var leases = new Map();
    entries.forEach(function (item) {
      leases.set(item.entry.node_id, nodeLeaseSignature(item));
    });
    nodeLeasesByRevision.set(revision, leases);
    while (nodeLeasesByRevision.size > 64) {
      nodeLeasesByRevision.delete(nodeLeasesByRevision.keys().next().value);
    }
  }

  function actionRevisionCompatible(requestedRevision, item, action) {
    if (Number(requestedRevision) === revision) return true;
    var leases = actionLeasesByRevision.get(Number(requestedRevision));
    if (!leases || !item || !action) return false;
    return leases.get(actionLeaseKey(item.entry.node_id, action.action_id))
      === actionLeaseSignature(item, action);
  }

  function nodeRevisionCompatible(requestedRevision, item) {
    if (Number(requestedRevision) === revision) return true;
    var leases = nodeLeasesByRevision.get(Number(requestedRevision));
    if (!leases || !item) return false;
    return leases.get(item.entry.node_id) === nodeLeaseSignature(item);
  }

  function refreshRevision(entries) {
    var byId = new Map();
    entries.forEach(function (item) { byId.set(item.entry.node_id, item); });
    var semanticIds = new Set();
    entries.forEach(function (item) {
      if (item.entry.affects_revision !== false && item.entry.actions.length) {
        semanticIds.add(item.entry.node_id);
      }
    });
    Array.from(semanticIds).forEach(function (nodeId) {
      var current = byId.get(nodeId);
      while (current && current.entry.parent_id && current.entry.parent_id !== "root") {
        var parentId = current.entry.parent_id;
        if (semanticIds.has(parentId)) break;
        current = byId.get(parentId);
        if (current && current.entry.affects_revision !== false) semanticIds.add(parentId);
      }
    });
    var manifest = entries.filter(function (item) {
      return semanticIds.has(item.entry.node_id);
    }).map(function (item) {
      return {
        node_id: item.entry.node_id,
        parent_id: item.entry.parent_id,
        scope: item.entry.scope,
        state: stableSemanticState(item.value),
        actions: item.entry.actions,
      };
    });
    var nextSignature = activeScope + "|" + (projectedLayer.overlay ? "overlay" : "base")
      + "|" + JSON.stringify(manifest);
    if (nextSignature !== actionableSignature) {
      actionableSignature = nextSignature;
      revision += 1;
    }
  }

  function entryInCurrentLayer(entry) {
    if (!entry) return false;
    if (activeScope === "main") {
      if (projectedLayer.overlay && nodes.get(entry.node_id) === entry) return false;
      return entry.scope === "main";
    }
    return entry.scope === activeScope;
  }

  function validateActionInput(action, rawInput) {
    var input = rawInput && typeof rawInput === "object" && !Array.isArray(rawInput)
      ? rawInput
      : {};
    var schema = action.input_schema || {};
    var allowed = Object.keys(schema);
    var forbidden = {
      selector: true, xpath: true, script: true, javascript: true,
      url: true, method: true, ipc_channel: true, callable: true,
      raw_request: true, x: true, y: true, clientX: true, clientY: true,
      screenX: true, screenY: true, key_code: true,
    };
    Object.keys(input).forEach(function (key) {
      if (forbidden[key] || allowed.indexOf(key) < 0) {
        throw new Error("UI action input contains an undeclared field");
      }
      var rule = String(schema[key] || "");
      if (rule.indexOf("text<=") === 0) {
        var limit = Number(rule.slice("text<=".length));
        if (typeof input[key] !== "string" || !Number.isInteger(limit) || input[key].length > limit) {
          throw new Error("UI action text is outside its declared limit");
        }
      } else if (rule.indexOf("..") >= 0) {
        var parts = rule.split("..").map(Number);
        var value = Number(input[key]);
        if (!Number.isFinite(value) || value < parts[0] || value > parts[1]) {
          throw new Error("UI action input is outside its declared range");
        }
      }
      if (key === "target_node_id") {
        var target = nodes.get(String(input[key] || "")) || projectedNodes.get(String(input[key] || ""));
        var targetVisible = target && (
          activeScope === "main" ? target.scope === "main" : target.scope === activeScope
        ) && target.get_node();
        if (!targetVisible) throw new Error("UI action target is not in the current tree");
      }
    });
    return input;
  }

  function encodeCursor(parentNodeId, offset) {
    return "v1:" + revision + ":" + Number(offset || 0) + ":" + encodeURIComponent(parentNodeId);
  }

  function decodeCursor(cursor, parentNodeId) {
    if (!cursor) return { ok: true, offset: 0 };
    var match = /^v1:(\d+):(\d+):(.*)$/.exec(String(cursor));
    if (!match) return { ok: false, error: "invalid_cursor" };
    var decodedParent;
    try { decodedParent = decodeURIComponent(match[3]); } catch (error) {
      return { ok: false, error: "invalid_cursor" };
    }
    if (Number(match[1]) !== revision) return { ok: false, error: "stale_cursor" };
    if (decodedParent !== parentNodeId) return { ok: false, error: "cursor_parent_mismatch" };
    return { ok: true, offset: Number(match[2]) };
  }

  function snapshot(args) {
    var options = args || {};
    var maxDepth = Math.max(1, Math.min(Number(options.max_depth) || 8, 12));
    var pageSize = Math.max(1, Math.min(Number(options.page_size) || 40, 200));
    var unpaged = options.unpaged === true;
    var includes = Array.isArray(options.include) ? options.include.map(String) : ["interactive"];
    var includeText = includes.indexOf("text") >= 0;
    var includeInteractive = includes.indexOf("interactive") >= 0;
    var entries = visibleEntries();
    var sessionState = visibleSessionState(entries);
    if (options.snapshot_id && String(options.snapshot_id) !== snapshotId) {
      return { ok: false, error: "stale_snapshot", snapshot_id: snapshotId, revision: revision };
    }
    var requestedRevisionCompatible = true;
    if (options.revision && Number(options.revision) !== revision) {
      var requestedNodeId = String(options.parent_node_id || "");
      var requestedActionId = String(options.action_id || "");
      var requestedItem = entries.find(function (item) {
        return item.entry.node_id === requestedNodeId;
      });
      var requestedAction = requestedItem && requestedItem.entry.actions.find(function (action) {
        return action.action_id === requestedActionId;
      });
      requestedRevisionCompatible = (
        options.allow_compatible_node === true
        && nodeRevisionCompatible(Number(options.revision), requestedItem)
      ) || (
        options.allow_compatible_action === true
        && actionRevisionCompatible(Number(options.revision), requestedItem, requestedAction)
      );
      if (!requestedRevisionCompatible) {
        return { ok: false, error: "stale_snapshot", snapshot_id: snapshotId, revision: revision };
      }
    }
    var entryById = new Map();
    entries.forEach(function (item) { entryById.set(item.entry.node_id, item); });
    if (!includeText) {
      var includedIds = new Set();
      if (includeInteractive) {
        entries.forEach(function (item) {
          if (item.entry.actions.length) includedIds.add(item.entry.node_id);
        });
        Array.from(includedIds).forEach(function (nodeId) {
          var current = entryById.get(nodeId);
          while (current && current.entry.parent_id && current.entry.parent_id !== "root") {
            includedIds.add(current.entry.parent_id);
            current = entryById.get(current.entry.parent_id);
          }
        });
      }
      entries = entries.filter(function (item) { return includedIds.has(item.entry.node_id); });
      entryById = new Map();
      entries.forEach(function (item) { entryById.set(item.entry.node_id, item); });
    }
    var byParent = new Map();
    entries.forEach(function (item) {
      var parent = item.entry.parent_id;
      if (!byParent.has(parent)) byParent.set(parent, []);
      byParent.get(parent).push(item);
    });
    function build(item, depth, pageOffset) {
      var value = item.value;
      var childItems = byParent.get(item.entry.node_id) || [];
      var offset = Math.min(Math.max(0, Number(pageOffset || 0)), childItems.length);
      var selectedChildren = unpaged ? childItems : childItems.slice(offset, offset + pageSize);
      var output = {
        node_id: item.entry.node_id,
        role: String(value.role || "group"),
        name: String(value.name || ""),
        value_summary: value.value_summary == null ? "" : String(value.value_summary),
        state: clone(value.state || {}),
        actions: clone(item.entry.actions),
        children: [],
      };
      if (depth < maxDepth) {
        output.children = selectedChildren.map(function (child) {
          return build(child, depth + 1, 0);
        });
      }
      var returned = depth < maxDepth ? output.children.length : 0;
      output.children_page = {
        parent_node_id: item.entry.node_id,
        offset: offset,
        page_size: unpaged ? childItems.length : pageSize,
        returned: returned,
        total: childItems.length,
        next_cursor: !unpaged && depth < maxDepth && offset + returned < childItems.length
          ? encodeCursor(item.entry.node_id, offset + returned)
          : null,
      };
      return output;
    }
    var rootItem = {
      entry: { node_id: "root", parent_id: "", actions: [] },
      value: {
        role: "application",
        name: "Cyrene",
        value_summary: "",
        state: {
          scope: activeScope,
          visible_session_id: sessionState.visible_session_id,
          visible_session_kind: sessionState.visible_session_kind,
        },
      },
    };
    var parentNodeId = String(options.parent_node_id || "root");
    var selectedRoot = parentNodeId === "root" ? rootItem : entryById.get(parentNodeId);
    if (!selectedRoot) {
      return { ok: false, error: "node_not_available", snapshot_id: snapshotId, revision: revision };
    }
    var cursor = decodeCursor(options.cursor, parentNodeId);
    if (!cursor.ok) {
      return { ok: false, error: cursor.error, snapshot_id: snapshotId, revision: revision };
    }
    var rootNode = build(selectedRoot, 0, cursor.offset);
    if (String(options._agent_cursor_mode || "") === "inspect") {
      var inspectedElement = parentNodeId === "root"
        ? projectionRoot()
        : entryHighlightElement(selectedRoot);
      var inspectHighlightSequence = showAgentControlHighlight(
        inspectedElement,
        parentNodeId,
        "inspect"
      );
      settleAgentControlHighlight(inspectHighlightSequence);
      var inspectPoint = parentNodeId === "root"
        ? visibleCenter(projectionRoot())
        : entryCenter(selectedRoot);
      if (inspectPoint) showAgentCursor(inspectPoint, {
        element: parentNodeId === "root" ? projectionRoot() : entryElement(selectedRoot),
      });
    }
    return {
      ok: true,
      snapshot_id: snapshotId,
      revision: revision,
      requested_revision_compatible: requestedRevisionCompatible,
      surface: {
        kind: surfaceKind,
        scope: activeScope,
        visible_session_id: sessionState.visible_session_id,
        visible_session_kind: sessionState.visible_session_kind,
      },
      include: includes,
      page: clone(rootNode.children_page),
      root: rootNode,
    };
  }

  async function act(args) {
    var entries = visibleEntries();
    if (String(args.snapshot_id || "") !== snapshotId) {
      return { ok: false, error: "stale_snapshot", snapshot_id: snapshotId, revision: revision };
    }
    var nodeId = String(args.node_id || "");
    var actionId = String(args.action_id || "");
    var item = entries.find(function (candidate) { return candidate.entry.node_id === nodeId; });
    var entry = item && item.entry;
    if (!entryInCurrentLayer(entry) || !item || !item.value) {
      return { ok: false, error: "node_not_available", revision: revision };
    }
    var action = entry.actions.find(function (item) { return item.action_id === actionId; });
    if (!action) return { ok: false, error: "action_not_available", revision: revision };
    if (!actionRevisionCompatible(Number(args.revision), item, action)) {
      return { ok: false, error: "stale_snapshot", snapshot_id: snapshotId, revision: revision };
    }
    if (action.requires_capability) {
      return {
        ok: false,
        error: "requires_capability",
        requires_capability: action.requires_capability,
        revision: revision,
      };
    }
    var controlHighlightSequence = null;
    try {
      var input = validateActionInput(action, clone(args.input || {}));
      controlHighlightSequence = showAgentControlHighlight(
        entryHighlightElement(item), nodeId, actionId
      );
      var cursorSequence = null;
      var cursorMode = String(args._agent_cursor_mode || "");
      if (cursorMode === "click") {
        var clickPoint = entryCenter(item);
        if (clickPoint) {
          var clickMove = showAgentCursor(clickPoint, { element: entryElement(item) });
          cursorSequence = clickMove && clickMove.sequence;
          await delayCursor(clickMove ? clickMove.waitMs : 0);
          var stableItem = visibleEntries().find(function (candidate) {
            return candidate.entry.node_id === nodeId;
          });
          var stablePoint = entryCenter(stableItem);
          if (!stablePoint) return { ok: false, error: "node_not_available", revision: revision };
          if (stablePoint.x !== clickPoint.x || stablePoint.y !== clickPoint.y) {
            clickPoint = stablePoint;
            clickMove = showAgentCursor(clickPoint, { element: entryElement(stableItem) });
            cursorSequence = clickMove && clickMove.sequence;
            await delayCursor(clickMove ? clickMove.waitMs : 0);
            stableItem = visibleEntries().find(function (candidate) {
              return candidate.entry.node_id === nodeId;
            });
            stablePoint = entryCenter(stableItem);
            if (!stablePoint || stablePoint.x !== clickPoint.x || stablePoint.y !== clickPoint.y) {
              return { ok: false, error: "target_unstable", revision: revision };
            }
          }
          var clickPress = showAgentCursor(clickPoint, {
            press: true,
            moveDurationMs: 0,
            element: entryElement(stableItem),
          });
          cursorSequence = clickPress && clickPress.sequence;
          await delayCursor(AGENT_CURSOR_PRESS_MS);
          var finalItem = visibleEntries().find(function (candidate) {
            return candidate.entry.node_id === nodeId;
          });
          var finalPoint = entryCenter(finalItem);
          if (!finalPoint || finalPoint.x !== clickPoint.x || finalPoint.y !== clickPoint.y) {
            return { ok: false, error: "target_unstable", revision: revision };
          }
          item = finalItem;
          entry = finalItem.entry;
        }
      } else if (cursorMode === "drag") {
        var sourcePoint = entryCenter(item);
        var targetItem = input.target_node_id
          ? entries.find(function (candidate) {
            return candidate.entry.node_id === String(input.target_node_id || "");
          })
          : null;
        var targetPoint = entryCenter(targetItem);
        if (sourcePoint) {
          var sourceMove = showAgentCursor(sourcePoint, { element: entryElement(item) });
          cursorSequence = sourceMove && sourceMove.sequence;
          if (targetPoint) {
            await delayCursor(sourceMove ? sourceMove.waitMs : 0);
            controlHighlightSequence = showAgentControlHighlight(
              entryHighlightElement(targetItem),
              String(input.target_node_id || ""),
              actionId
            ) || controlHighlightSequence;
            var targetMove = showAgentCursor(targetPoint, {
              moveDurationMs: AGENT_CURSOR_DRAG_MS,
              element: entryElement(targetItem),
            });
            cursorSequence = targetMove && targetMove.sequence;
            await delayCursor(AGENT_CURSOR_DRAG_MS);
          }
        }
      } else if (cursorMode === "target") {
        var actionPoint = entryCenter(item);
        if (actionPoint) {
          var actionMove = showAgentCursor(actionPoint, { element: entryElement(item) });
          cursorSequence = actionMove && actionMove.sequence;
          await delayCursor(actionMove ? actionMove.waitMs : 0);
        }
      }
      var handler = entry.handlers[actionId];
      var result = await handler(input);
      var refreshedEntries = visibleEntries();
      if (cursorSequence != null) {
        var refreshedItem = refreshedEntries.find(function (candidate) {
          return candidate.entry.node_id === nodeId;
        });
        if (!entryCenter(refreshedItem)) hideAgentCursor(cursorSequence);
      }
      return { ok: true, revision: revision, result: clone(result || {}) };
    } catch (error) {
      return { ok: false, error: "action_failed", revision: revision };
    } finally {
      settleAgentControlHighlight(controlHighlightSequence);
    }
  }

  async function handleHostRequest(method, args) {
    if (method === "snapshot") return snapshot(args || {});
    if (method === "act") return act(args || {});
    if (method === "terminal.current") {
      var currentBridge = root.CyreneTerminalSurface;
      return currentBridge && typeof currentBridge.current === "function"
        ? currentBridge.current()
        : { ok: false, error: "terminal_surface_unavailable" };
    }
    if (method === "terminal.show") {
      var showBridge = root.CyreneTerminalSurface;
      return showBridge && typeof showBridge.show === "function"
        ? showBridge.show(args && args.terminalId, args && args.side)
        : { ok: false, error: "terminal_surface_unavailable" };
    }
    if (method === "terminal.control") {
      var terminalId = String(args && args.terminalId || "");
      var terminalElement = null;
      var panes = root.document && root.document.querySelectorAll
        ? root.document.querySelectorAll(".wbc-terminal-pane[data-terminal-id]")
        : [];
      Array.prototype.some.call(panes, function (pane) {
        if (String(pane.getAttribute("data-terminal-id") || "") !== terminalId) return false;
        if (!elementVisible(pane)) return false;
        terminalElement = pane;
        return true;
      });
      if (!terminalElement) return { ok: true, highlighted: false, terminalId: terminalId };
      var controlSequence = showAgentControlHighlight(
        terminalElement,
        "terminal:" + terminalId,
        String(args && args.action || "input")
      );
      settleAgentControlHighlight(controlSequence);
      var terminalPoint = visibleCenter(terminalElement);
      if (terminalPoint) showAgentCursor(terminalPoint, { element: terminalElement });
      return { ok: true, highlighted: controlSequence != null, terminalId: terminalId };
    }
    return { ok: false, error: "unsupported_surface_method" };
  }

  var cursorDocument = root.document;
  if (cursorDocument && typeof cursorDocument.addEventListener === "function") {
    cursorDocument.addEventListener("visibilitychange", function () {
      if (cursorDocument.hidden || cursorDocument.visibilityState === "hidden") {
        hideAgentCursor();
        hideAgentControlHighlight(null, true);
      }
    });
  }
  if (typeof root.addEventListener === "function") {
    root.addEventListener("pagehide", function () {
      hideAgentCursor();
      hideAgentControlHighlight(null, true);
    });
    root.addEventListener("resize", refreshAgentControlHighlight);
    root.addEventListener("scroll", refreshAgentControlHighlight, true);
  }
  var cursorOwnerBridge = root.cyrene && root.cyrene.agentCursor;
  if (cursorOwnerBridge && typeof cursorOwnerBridge.onOwnerChanged === "function") {
    cursorOwnerBridge.onOwnerChanged(function (owner) {
      if (String(owner || "") !== "ui") hideAgentCursor();
    });
  }
  if (typeof root.MutationObserver === "function" && cursorDocument) {
    var cursorTargetObserver = new root.MutationObserver(function () {
      var target = agentCursorState.targetElement;
      if (
        target && agentCursorState.visible
        && (target.isConnected === false || !elementVisible(target))
      ) hideAgentCursor(agentCursorState.sequence);
      var controlTarget = agentControlHighlightState.targetElement;
      if (controlTarget && agentControlHighlightState.visible) {
        if (controlTarget.isConnected === false || !elementVisible(controlTarget)) {
          hideAgentControlHighlight(agentControlHighlightState.sequence, true);
        } else {
          refreshAgentControlHighlight();
        }
      }
    });
    var observerRoot = cursorDocument.documentElement || cursorDocument.body;
    if (observerRoot) cursorTargetObserver.observe(observerRoot, { childList: true, subtree: true, attributes: true });
  }

  var service = platform.register("uiSurface", {
    getInstanceId: function () { return instanceId; },
    getRevision: function () { return revision; },
    getScope: function () { return activeScope; },
    setAgentRunning: setAgentRunning,
    register: register,
    setScope: setScope,
    touch: touch,
    snapshot: snapshot,
    act: act,
  });

  var surfaceDisposed = false;
  var surfaceSocket = null;
  var electronSurfaceRegistered = false;

  function disposeSurface() {
    if (surfaceDisposed) return;
    surfaceDisposed = true;
    hideAgentCursor();
    hideAgentControlHighlight(null, true);
    if (cursorTargetObserver) cursorTargetObserver.disconnect();
    if (typeof root.removeEventListener === "function") {
      root.removeEventListener("resize", refreshAgentControlHighlight);
      root.removeEventListener("scroll", refreshAgentControlHighlight, true);
    }
    if (surfaceSocket) {
      try { surfaceSocket.close(); } catch (error) {}
      surfaceSocket = null;
    }
    if (
      root.cyrene && root.cyrene.uiSurface
      && typeof root.cyrene.uiSurface.unregister === "function"
    ) {
      root.cyrene.uiSurface.unregister(instanceId).catch(function () {});
      electronSurfaceRegistered = false;
    }
  }

  if (typeof root.addEventListener === "function") {
    root.addEventListener("cyrene:page-invalidated", disposeSurface, { once: true });
    root.addEventListener("pagehide", disposeSurface, { once: true });
    root.addEventListener("beforeunload", disposeSurface, { once: true });
  }

  if (root.cyrene && root.cyrene.uiSurface) {
    root.cyrene.uiSurface.register(instanceId, handleHostRequest).then(function (result) {
      electronSurfaceRegistered = !!(result && result.ok !== false);
      if (surfaceDisposed && electronSurfaceRegistered) {
        root.cyrene.uiSurface.unregister(instanceId).catch(function () {});
        electronSurfaceRegistered = false;
        return;
      }
      var registeredKind = String(result && result.surfaceKind || "");
      if (registeredKind === "main" || registeredKind === "quick_chat") {
        if (surfaceKind !== registeredKind) {
          surfaceKind = registeredKind;
          touch();
        }
      }
    }).catch(function (error) {
      console.warn("[ui-surface] Electron registration failed", error);
    });
  }

  // Electron's IPC surface serves native desktop controls, while Agent tools
  // such as ShowShell are dispatched by the Python backend. Keep the backend
  // WebSocket registered as well so both request paths resolve the same live
  // renderer instance instead of reporting no_current_surface in Electron.
  if (typeof root.WebSocket === "function") {
    var protocol = root.location.protocol === "https:" ? "wss:" : "ws:";
    var socket = new root.WebSocket(protocol + "//" + root.location.host + "/api/app-control/ui-surface/" + encodeURIComponent(instanceId));
    surfaceSocket = socket;
    socket.addEventListener("message", function (event) {
      if (surfaceDisposed) return;
      var payload;
      try { payload = JSON.parse(event.data); } catch (error) { return; }
      if (!payload || payload.type !== "request") return;
      Promise.resolve(handleHostRequest(payload.method, payload.args || {})).catch(function () {
        return { ok: false, error: "surface_action_failed", revision: revision };
      }).then(function (result) {
        if (socket.readyState === root.WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "response", requestId: payload.requestId, result: result }));
        }
      });
    });
  }
})(window);
