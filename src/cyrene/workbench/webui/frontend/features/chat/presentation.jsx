import { workbenchServices } from "../../shared/runtime/services.jsx"
import { toolPresentationKind as toolPresentationKindBehavior } from "./behavior.mjs"
import { wbcT } from "./core.jsx"
import { wbcStructuredEventSummary } from "./agent-events.jsx"

function wbcToolPresentationKind(entry) {
  return toolPresentationKindBehavior(entry);
}

function wbcToolPresentationText(entry, kind) {
  if (["terminal", "diff", "error"].indexOf(kind) < 0) return "";
  var value = entry && entry.output != null ? entry.output : entry && entry.input;
  var text = typeof value === "string" ? value : wbcStructuredEventSummary(value);
  return (kind === "error" ? wbcLocalizedToolErrorText(text) : text).slice(0, 12000);
}

function wbcTraceDedupeKey(trace) {
  if (!Array.isArray(trace) || !trace.length) return "";
  return JSON.stringify(trace.map(function (entry) {
    var item = entry || {};
    return [
      String(item.tool || item.text || ""),
      String(item.preview || ""),
      String(item.kind || ""),
    ];
  }));
}

function wbcCurrentModel(chat, project, runtime, liveData) {
  // During a run, activeModel is the authoritative transport identity and can
  // change on the exact SSE tick that fallback occurs. The polled context
  // payload remains the durable source once the live runtime is gone.
  var activeModel = String(runtime && runtime.activeModel || "").trim();
  if (activeModel) return activeModel;
  var liveModel = String(liveData && liveData.model || "").trim();
  if (liveModel) return liveModel;
  var messages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  for (var i = messages.length - 1; i >= 0; i--) {
    var messageModel = String(messages[i] && messages[i].model || "").trim();
    if (messageModel) return messageModel;
  }
  return String(
    (chat && chat.model)
    || (project && project.model)
    || ""
  ).trim();
}

var WBC_CHAT_MODEL_CHANGED_EVENT = "cyrene:wbc-chat-model-changed";

function wbcModelContextLimit(model) {
  var source = model && typeof model === "object" ? model : {};
  var raw = source.ctxLimit;
  if (raw == null) raw = source.ctx_limit;
  if (raw == null) raw = source.contextLimit;
  if (raw == null) raw = source.context_limit;
  if (raw == null) raw = source.contextWindow;
  if (raw == null) raw = source.context_window;
  if (raw == null) raw = source.ctx;
  if (typeof raw === "number") return Number.isFinite(raw) ? Math.max(0, Math.round(raw)) : 0;
  var text = String(raw == null ? "" : raw).trim().toLowerCase().replace(/[, _]/g, "");
  if (!text) return 0;
  var match = text.match(/^([0-9]+(?:\.[0-9]+)?)([kmg])?(?:tokens?)?$/);
  if (!match) return 0;
  var multiplier = match[2] === "g" ? 1000000000 : (match[2] === "m" ? 1000000 : (match[2] === "k" ? 1000 : 1));
  return Math.max(0, Math.round(Number(match[1]) * multiplier));
}

function wbcPublishChatModelChanged(chatId, selected, options) {
  if (!chatId || typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  var model = selected && typeof selected === "object" ? selected : {};
  var detail = {
    chatId: String(chatId),
    profileId: String(model.profileId || model.profile_id || model.id || model.value || ""),
    model: String(model.model || model.name || model.label || model.value || model.id || "").trim(),
    ctxLimit: wbcModelContextLimit(model),
    refresh: !(options && options.refresh === false),
  };
  try {
    window.dispatchEvent(new CustomEvent(WBC_CHAT_MODEL_CHANGED_EVENT, { detail: detail }));
  } catch (e) {}
}

function wbcSubagentStatusText(status) {
  var key = String(status || "").trim().toLowerCase();
  var labels = {
    running: wbcT("workbenchChat.subagent.status.running", "Running"),
    resumed: wbcT("workbenchChat.subagent.status.resumed", "Resumed"),
    waiting: wbcT("workbenchChat.subagent.status.waiting", "Waiting"),
    done: wbcT("workbenchChat.subagent.status.done", "Done"),
    timeout: wbcT("workbenchChat.subagent.status.timeout", "Timed out"),
  };
  return labels[key] || key || wbcT("workbenchChat.subagent.status.unknown", "Unknown");
}

function wbcSubagentStatusClass(status) {
  var key = String(status || "").trim().toLowerCase();
  if (key === "running" || key === "resumed") return "running";
  if (key === "waiting") return "waiting";
  if (key === "timeout") return "error";
  return "done";
}

// Deterministic per-agent accent colors for the subagent chat room. Defined here
// (not shared with any historical chat renderer constants) so conversation
// components keep zero front-end coupling.
var WBC_SUBAGENT_COLORS = [
  "#3b82f6", "#e8734a", "#1f9d57", "#d94a8c", "#8b6cc4",
  "#d9a64a", "#0ea5a3", "#c2570f", "#6366f1", "#7cb518",
];

function wbcAgentColor(agentId) {
  var id = String(agentId || "");
  var hash = 0;
  for (var i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash) + id.charCodeAt(i);
    hash |= 0;
  }
  return WBC_SUBAGENT_COLORS[Math.abs(hash) % WBC_SUBAGENT_COLORS.length];
}

// Two-letter avatar initials from an agent id like "research_a" -> "RA".
function wbcAgentInitials(name) {
  var raw = String(name || "").trim();
  if (!raw) return "?";
  var parts = raw.split(/[\s_\-.]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return raw.slice(0, 2).toUpperCase();
}

// Highlight @mentions only when they name a known agent (or everyone), so the
// pass never corrupts emails / links produced by the markdown renderer.
function wbcHighlightMentions(html, agentIds) {
  return String(html == null ? "" : html).replace(
    /@([\w一-龥][\w.\-一-龥]*)/g,
    function (full, name) {
      var known = agentIds && agentIds.indexOf(name) >= 0;
      if (known || name === "所有人" || name === "all" || name === "everyone") {
        return '<span class="wbc-subagent-mention">@' + name + "</span>";
      }
      return full;
    }
  );
}

function wbcCompactNumber(value) {
  var num = Number(value || 0);
  if (!num) return "0";
  if (num >= 1000000) return (num / 1000000).toFixed(1) + "M";
  if (num >= 1000) return (num / 1000).toFixed(1) + "k";
  return String(num);
}


function wbcFormatToolParameter(value) {
  if (value == null || value === "") return "";
  if (Array.isArray(value)) return value.map(wbcFormatToolParameter).filter(Boolean).join(", ");
  if (typeof value === "object") return Object.entries(value).map(function (pair) {
    var formatted = wbcFormatToolParameter(pair[1]);
    return formatted ? pair[0] + ": " + formatted : "";
  }).filter(Boolean).join(", ");
  return String(value);
}

function wbcFlattenToolObjectLiterals(value) {
  var text = String(value || "");
  var objectPattern = /\{([^{}]*)\}/g;
  for (var pass = 0; pass < 4 && /\{[^{}]*\}/.test(text); pass++) {
    objectPattern.lastIndex = 0;
    text = text.replace(objectPattern, function (_match, body) {
      if (!body.trim()) return "";
      return body.split(/,\s*(?=(?:['\"][^'\"]+['\"]|[A-Za-z_][\w.-]*)\s*:)/).map(function (part) {
        var field = part.match(/^\s*['\"]?([^'\":]+)['\"]?\s*:\s*([\s\S]*?)\s*$/);
        if (!field) return part.trim().replace(/^['\"]|['\"]$/g, "");
        var fieldValue = field[2].trim();
        if ((fieldValue.startsWith("'") && fieldValue.endsWith("'")) || (fieldValue.startsWith('"') && fieldValue.endsWith('"'))) {
          fieldValue = fieldValue.slice(1, -1);
        }
        return field[1].trim() + ": " + fieldValue;
      }).filter(Boolean).join(", ");
    });
  }
  return text.replace(/[{}]/g, "").replace(/\s+,/g, ",").trim();
}

function wbcToolPreviewText(preview) {
  var text = wbcLocalizedToolErrorText(String(preview || ""));
  if (text !== String(preview || "")) return text;
  text = wbcFlattenToolObjectLiterals(preview);
  if (!text) return "";
  var operationKeys = {
    discover: "toolOperation.discover",
    describe: "toolOperation.describe",
    invoke: "toolOperation.invoke",
    list_targets: "toolOperation.list_targets",
    connect: "toolOperation.connect",
    call: "toolOperation.call",
    status: "toolOperation.status",
    disconnect: "toolOperation.disconnect",
    snapshot: "toolOperation.snapshot",
    reprobe: "toolOperation.reprobe",
    visual_describe: "toolOperation.visual_describe",
    measure_coordinates: "toolOperation.measure_coordinates",
    visual_click: "toolOperation.visual_click",
    visual_type: "toolOperation.visual_type",
    focus_window: "toolOperation.focus_window",
    restore_previous_focus: "toolOperation.restore_previous_focus",
    click_at: "toolOperation.click_at",
    double_click: "toolOperation.double_click",
    right_click: "toolOperation.right_click",
    hover_at: "toolOperation.hover_at",
    drag: "toolOperation.drag",
    swipe: "toolOperation.swipe",
    scroll_at: "toolOperation.scroll_at",
    key_chord: "toolOperation.key_chord",
    key_sequence: "toolOperation.key_sequence",
    virtual_type_at: "toolOperation.virtual_type_at",
  };
  return text.split(", ").map(function (part) {
    var token = part.trim();
    if (operationKeys[token]) return wbcT(operationKeys[token], token);
    // Progressive calls expose stable capability IDs in their arguments.
    // Resolve only values with an existing tool-name translation; arbitrary
    // user input, paths, queries, and other arguments must remain untouched.
    var localizedToolName = wbcT("toolName." + token, token);
    return localizedToolName !== token ? localizedToolName : part;
  }).join(", ");
}

function wbcLocalizedValidationToken(value, kind) {
  var raw = String(value || "").trim().replace(/^['\"]|['\"]$/g, "");
  if (!raw) return "";
  if (kind === "field") return "“" + raw + "”";
  var key = kind === "operation" ? "toolOperation." + raw : "toolName." + raw;
  var localized = wbcT(key, raw);
  if (localized === raw && kind === "operation") localized = wbcT("toolName." + raw, raw);
  return "“" + localized + "”";
}

function wbcLocalizedToolErrorText(value) {
  var text = String(value || "").trim();
  if (!text) return "";
  var invalid = text.match(/^(?:插件参数无效：\s*)?Invalid arguments for Plugin\s+['\"][^'\"]+['\"]\s+at\s+([^:]+):\s*(.+)$/i);
  if (!invalid) return text;
  var path = String(invalid[1] || "").trim().replace(/^arguments\.?/, "") || "arguments";
  var reason = String(invalid[2] || "").trim();
  var required = reason.match(/^['\"]([^'\"]+)['\"] is a required property$/i);
  if (required) {
    reason = wbcT("workbenchChat.toolError.missingRequired", "Missing required argument: {field}", {
      field: wbcLocalizedValidationToken(required[1], "field"),
    });
    return reason;
  }
  var choice = reason.match(/^(.+?) is not one of \[(.*)\]$/i);
  if (choice) {
    var values = String(choice[2] || "").split(/\s*,\s*/).filter(Boolean);
    var choiceKind = path === "operation" ? "operation" : "tool";
    reason = wbcT("workbenchChat.toolError.invalidChoice", "{value} is not valid; choose from {choices}", {
      value: wbcLocalizedValidationToken(choice[1], choiceKind),
      choices: values.map(function (item) {
        return wbcLocalizedValidationToken(item, choiceKind);
      }).join(wbcT("workbenchChat.toolError.choiceSeparator", ", ")),
    });
  }
  return wbcT("workbenchChat.toolError.invalidArgument", "Invalid argument {path}: {reason}", {
    path: path,
    reason: reason,
  });
}

function wbcToolArgsPreview(args) {
  if (!args || typeof args !== "object") return "";
  return Object.values(args).map(wbcFormatToolParameter).filter(Boolean).join(", ").slice(0, 120);
}

function wbcThinkingPhrases() {
  return wbcT(
    "workbenchChat.thinkingPhrases",
    "Thinking this through|Checking the details|Reviewing the context|Verifying the result"
  ).split("|").filter(Boolean);
}

function wbcRandomThinkingPhrase() {
  var phrases = wbcThinkingPhrases();
  return phrases[Math.floor(Math.random() * phrases.length)] || wbcT("workbenchChat.stillWorking", "Still working…");
}

function wbcBrowserFullscreenStatusText(runtime) {
  if (runtime && runtime.finalizing) {
    return wbcT("workbenchChat.status.saving", "Saving");
  }
  if (runtime && String(runtime.text || "").trim()) {
    return wbcT("workbenchChat.browserChatReplying", "Agent is replying…");
  }
  var activities = runtime && Array.isArray(runtime.activities) ? runtime.activities : [];
  var activity = activities.length ? activities[activities.length - 1] : null;
  var progress = activity && Array.isArray(activity.progress) && activity.progress.length
    ? activity.progress
    : (runtime && Array.isArray(runtime.progress) ? runtime.progress : []);
  var entry = progress.length ? progress[progress.length - 1] : null;
  if (entry) {
    var key = entry.text || entry.tool || "";
    if (entry.kind === "tool" || entry.tool) return wbcT("toolName." + key, key);
    if (entry.detailKey) return wbcT(entry.detailKey, key, entry.detailParams);
    if (key) return key;
  }
  return wbcT("workbenchChat.browserChatWorking", "Agent is working in the browser…");
}

function wbcBrowserPageTitle(browserState) {
  var browser = browserState || {};
  var activeTab = browser.activeTab || {};
  var title = String(activeTab.title || browser.title || "").trim();
  if (title && title !== "about:blank") return title;
  var rawUrl = String(activeTab.url || browser.url || browser.frameUrl || "").trim();
  if (rawUrl && rawUrl !== "about:blank") {
    try {
      var host = new URL(rawUrl).hostname.replace(/^www\./, "");
      if (host) return host;
    } catch (e) {}
  }
  return "";
}

function wbcBrowserWindowTitle(browserState) {
  var page = wbcBrowserPageTitle(browserState);
  if (page) return wbcT("workbenchChat.browserWindowTitleWithPage", "Browser · {page}", { page: page });
  return wbcT("workbenchChat.browserWindowTitle", "Browser");
}

var WBC_BROWSER_TAB_PICKER_TOGGLE_DEBOUNCE_MS = 280;

function wbcBrowserTabPickerToggleIsDebounced(lastToggleAtRef) {
  var now = Date.now();
  var lastToggleAt = Number(lastToggleAtRef && lastToggleAtRef.current || 0);
  if (lastToggleAt && now - lastToggleAt < WBC_BROWSER_TAB_PICKER_TOGGLE_DEBOUNCE_MS) return true;
  if (lastToggleAtRef) lastToggleAtRef.current = now;
  return false;
}

function wbcBrowserTabPickerPayload(browserSessionId, visible, variant) {
  var paletteNode = document.querySelector(".workbench-shell") || document.documentElement;
  var rootStyles = getComputedStyle(paletteNode);
  function color(name, fallback) {
    return String(rootStyles.getPropertyValue(name) || "").trim() || fallback;
  }
  return {
    sessionId: String(browserSessionId || ""),
    visible: visible === true,
    variant: variant === "split" ? "split" : "maximized",
    labels: {
      tabs: wbcT("workbenchChat.browserTabs", "Browser tabs"),
      browser: wbcT("workbenchChat.browserWindowTitle", "Browser"),
      reload: wbcT("browser.context.reload", "Reload"),
      mute: wbcT("browser.context.mute", "Mute"),
      unmute: wbcT("browser.context.unmute", "Unmute"),
      close: wbcT("browser.context.closeTab", "Close tab"),
    },
    colors: {
      line: color("--wb-line-2", "#d8dce4"),
      panel: color("--wb-card-bg-strong", "#ffffff"),
      text: color("--wb-text", "#17191d"),
      muted: color("--wb-muted", "#6f737b"),
      faint: color("--wb-faint", "#9297a1"),
      hover: color("--wb-control-hover-bg", "#f3f4f6"),
      selected: color("--wb-card-bg", "#f7f7f8"),
    },
  };
}

function wbcClampBrowserWindowFrame(frame, areaWidth, areaHeight, minWidth, minHeight) {
  var aw = Math.max(0, Number(areaWidth) || 0);
  var ah = Math.max(0, Number(areaHeight) || 0);
  var mw = Math.min(Math.max(1, Number(minWidth) || 1), aw || 1);
  var mh = Math.min(Math.max(1, Number(minHeight) || 1), ah || 1);
  var width = Math.min(Math.max(mw, Number(frame && frame.width) || mw), aw || mw);
  var height = Math.min(Math.max(mh, Number(frame && frame.height) || mh), ah || mh);
  var x = Math.min(Math.max(0, Number(frame && frame.x) || 0), Math.max(0, aw - width));
  var y = Math.min(Math.max(0, Number(frame && frame.y) || 0), Math.max(0, ah - height));
  return { x: x, y: y, width: width, height: height };
}

function wbcBrowserComposerDockFrame(frame, areaRect, composerRect, gap, minHeight) {
  if (!frame || !areaRect || !composerRect) return frame;
  var composerLeft = composerRect.left - areaRect.left;
  var composerRight = composerRect.right - areaRect.left;
  var overlapsComposerColumn = frame.x < composerRight && frame.x + frame.width > composerLeft;
  if (!overlapsComposerColumn) return frame;
  var gutter = Math.max(0, Number(gap) || 0);
  var minimumHeight = Math.max(1, Number(minHeight) || 1);
  var ceiling = Math.max(0, composerRect.top - areaRect.top - gutter);
  if (frame.y + frame.height <= ceiling) return frame;
  var height = Math.min(frame.height, Math.max(minimumHeight, ceiling));
  return Object.assign({}, frame, {
    y: Math.max(0, ceiling - height),
    height: height,
  });
}

function wbcKeepBrowserWindowClearOfComposer(frame, area) {
  if (!frame || !area || !area.closest) return frame;
  var main = area.closest(".wbc-main");
  var composer = main && main.querySelector(":scope > .wbc-composer");
  if (!composer) return frame;
  return wbcBrowserComposerDockFrame(
    frame,
    area.getBoundingClientRect(),
    composer.getBoundingClientRect(),
    10,
    180
  );
}

var WBC_BROWSER_FRAME_STORAGE_PREFIX = "wbc-browser-window-frame:";

function wbcLoadBrowserWindowFrame(sessionId) {
  var key = String(sessionId || "").trim();
  if (!key) return null;
  try {
    var saved = JSON.parse(localStorage.getItem(WBC_BROWSER_FRAME_STORAGE_PREFIX + key) || "null");
    if (!saved || typeof saved !== "object") return null;
    var frame = {
      x: Number(saved.x),
      y: Number(saved.y),
      width: Number(saved.width),
      height: Number(saved.height),
    };
    if (!Object.keys(frame).every(function (field) { return Number.isFinite(frame[field]); })) return null;
    frame.heightCustomized = saved.heightCustomized === true;
    return frame;
  } catch (e) {
    return null;
  }
}

function wbcSaveBrowserWindowFrame(sessionId, frame) {
  var key = String(sessionId || "").trim();
  if (!key || !frame) return;
  try {
    localStorage.setItem(WBC_BROWSER_FRAME_STORAGE_PREFIX + key, JSON.stringify({
      x: Math.round(Number(frame.x) || 0),
      y: Math.round(Number(frame.y) || 0),
      width: Math.round(Number(frame.width) || 0),
      height: Math.round(Number(frame.height) || 0),
      heightCustomized: frame.heightCustomized === true,
    }));
  } catch (e) {}
}

// Pick a readable lane beside the floating browser.  Keeping this calculation
// pure makes the product rule explicit: avoid only when the PiP is clearly off
// centre and one side remains wide enough to read.  Insets are relative to the
// transcript content box, not the Electron window.
function wbcBrowserAvoidancePlan(areaLeft, areaWidth, browserLeft, browserWidth, gap) {
  var left = Number(areaLeft) || 0;
  var width = Math.max(0, Number(areaWidth) || 0);
  var right = left + width;
  var browserStart = Number(browserLeft) || 0;
  var browserSize = Math.max(0, Number(browserWidth) || 0);
  var browserEnd = browserStart + browserSize;
  var gutter = Math.max(0, Number(gap) || 0);
  if (width <= 0 || browserSize <= 0 || browserEnd <= left || browserStart >= right) return null;

  var leftLane = Math.max(0, browserStart - gutter - left);
  var rightLane = Math.max(0, right - browserEnd - gutter);
  var readable = Math.min(360, width * 0.45);
  var centreDeadZone = Math.min(80, width * 0.12);
  if (Math.max(leftLane, rightLane) < readable) return null;
  if (Math.abs(leftLane - rightLane) < centreDeadZone) return null;
  if (leftLane > rightLane) {
    return { side: "left", start: 0, end: Math.max(0, right - browserStart + gutter) };
  }
  return { side: "right", start: Math.max(0, browserEnd - left + gutter), end: 0 };
}

function wbcNotifyBrowserLayoutChanged() {
  window.dispatchEvent(new CustomEvent("workbench:browser-layout"));
}

function wbcNotifyBrowserWindowInteraction(active, kind, sessionId, extra) {
  window.dispatchEvent(new CustomEvent("workbench:browser-window-interaction", {
    detail: Object.assign(
      { active: active === true, kind: kind || "", sessionId: String(sessionId || "") },
      extra && typeof extra === "object" ? extra : {}
    ),
  }));
}

function wbcRectsOverlap(a, b) {
  return !!(
    a && b
    && a.left < b.right
    && a.right > b.left
    && a.top < b.bottom
    && a.bottom > b.top
  );
}

function wbcPageContextMenuPlacement(clientX, clientY, avoidRect) {
  var margin = 8;
  var gap = 8;
  var width = 220;
  var height = 206;
  var viewportWidth = Math.max(width + (margin * 2), Number(window.innerWidth) || 0);
  var viewportHeight = Math.max(height + (margin * 2), Number(window.innerHeight) || 0);
  function clamp(left, top) {
    var x = Math.max(margin, Math.min(Number(left) || 0, viewportWidth - width - margin));
    var y = Math.max(margin, Math.min(Number(top) || 0, viewportHeight - height - margin));
    return {
      left: x,
      top: y,
      right: x + width,
      bottom: y + height,
    };
  }
  var base = clamp(clientX, clientY);
  if (!avoidRect || !wbcRectsOverlap(base, avoidRect)) {
    return { left: base.left, top: base.top, overlapsBrowser: false };
  }
  var candidates = [
    clamp(avoidRect.left - width - gap, clientY),
    clamp(avoidRect.right + gap, clientY),
    clamp(clientX, avoidRect.top - height - gap),
    clamp(clientX, avoidRect.bottom + gap),
  ].filter(function (candidate) { return !wbcRectsOverlap(candidate, avoidRect); });
  if (candidates.length) {
    candidates.sort(function (a, b) {
      var adx = a.left - base.left;
      var ady = a.top - base.top;
      var bdx = b.left - base.left;
      var bdy = b.top - base.top;
      return ((adx * adx) + (ady * ady)) - ((bdx * bdx) + (bdy * bdy));
    });
    return { left: candidates[0].left, top: candidates[0].top, overlapsBrowser: false };
  }
  return { left: base.left, top: base.top, overlapsBrowser: true };
}

function wbcCanOpenPageContextMenu(event) {
  var target = event && event.target;
  if (!target || !target.closest) return false;
  var selection = window.getSelection && window.getSelection();
  if (selection && !selection.isCollapsed && String(selection).trim()) return false;
  return !target.closest([
    "button",
    "a",
    "input",
    "textarea",
    "select",
    "label",
    "[contenteditable='true']",
    "[role='button']",
    "[role='link']",
    "[role='menu']",
    "[role='dialog']",
    ".wbc-composer",
    ".wbc-header",
    ".wbc-browser-window",
    ".wbc-selection-menu",
    ".wbc-conversation-nav",
    ".wbc-chat-card",
    ".workbench-confirm-modal",
  ].join(","));
}

function wbcPointInsideResourceShelf(clientX, clientY) {
  var shelf = document.querySelector(".workbench-resource-shelf");
  if (!shelf) return false;
  var rect = shelf.getBoundingClientRect();
  var x = Number(clientX);
  var y = Number(clientY);
  return Number.isFinite(x) && Number.isFinite(y)
    && x >= rect.left && x <= rect.right
    && y >= rect.top && y <= rect.bottom;
}

function wbcConversationTabAtPoint(clientX, clientY, ownerSessionId) {
  var x = Number(clientX);
  var y = Number(clientY);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  var owner = String(ownerSessionId || "");
  var tabs = document.querySelectorAll('.workbench-session-tab[data-session-kind="chat"]');
  for (var index = 0; index < tabs.length; index += 1) {
    var tab = tabs[index];
    var targetId = String(tab.getAttribute("data-session-id") || "");
    if (!targetId || targetId === owner) continue;
    var rect = tab.getBoundingClientRect();
    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
      return { node: tab, chatId: targetId };
    }
  }
  return null;
}

function wbcCycleTopbarSessionTab(direction) {
  var tabs = Array.prototype.slice.call(document.querySelectorAll(
    '.workbench-session-tab[data-session-id]'
  )).filter(function (tab) {
    var rect = tab.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  });
  if (tabs.length < 2) return false;
  var activeIndex = tabs.findIndex(function (tab) {
    return tab.getAttribute("aria-current") === "page" || tab.classList.contains("active");
  });
  var step = Number(direction) < 0 ? -1 : 1;
  var nextIndex = ((activeIndex < 0 ? 0 : activeIndex) + step + tabs.length) % tabs.length;
  tabs[nextIndex].click();
  return true;
}

function wbcHandleHorizontalWheelGesture(event, gesture, onCycle) {
  var deltaX = Number(event.deltaX || 0);
  var deltaY = Number(event.deltaY || 0);
  if (Math.abs(deltaX) < 2 || Math.abs(deltaX) <= Math.abs(deltaY) * 1.15) return false;
  event.preventDefault();
  var now = Date.now();
  var idleFor = now - Number(gesture.lastEventAt || 0);
  if (gesture.waitingForIdle) {
    gesture.lastEventAt = now;
    if (idleFor < 180 || now < gesture.lockedUntil) return true;
    gesture.waitingForIdle = false;
    gesture.delta = 0;
    gesture.direction = 0;
  } else {
    gesture.lastEventAt = now;
  }
  var direction = deltaX < 0 ? -1 : 1;
  if (gesture.direction && gesture.direction !== direction) gesture.delta = 0;
  gesture.direction = direction;
  if (now < gesture.lockedUntil) return true;
  gesture.delta += deltaX;
  if (Math.abs(gesture.delta) < 44) return true;
  if (onCycle(direction)) {
    gesture.lockedUntil = now + 420;
    gesture.waitingForIdle = true;
  }
  gesture.delta = 0;
  return true;
}

function wbcNotifyResourceShelfPointerDrag(active) {
  window.dispatchEvent(new CustomEvent("cyrene:resource-shelf-drag-state", {
    detail: { active: active === true },
  }));
}

// Shared budget error code → i18n key suffix mapping.  Defined here and
// shared through the registered conversation service
// so adding a new budget code only needs one update.

export { wbcToolPresentationKind, wbcToolPresentationText, wbcTraceDedupeKey, wbcCurrentModel, WBC_CHAT_MODEL_CHANGED_EVENT, wbcModelContextLimit, wbcPublishChatModelChanged, wbcSubagentStatusText, wbcSubagentStatusClass, WBC_SUBAGENT_COLORS, wbcAgentColor, wbcAgentInitials, wbcHighlightMentions, wbcCompactNumber, wbcFormatToolParameter, wbcFlattenToolObjectLiterals, wbcToolPreviewText, wbcToolArgsPreview, wbcThinkingPhrases, wbcRandomThinkingPhrase, wbcBrowserFullscreenStatusText, wbcBrowserPageTitle, wbcBrowserWindowTitle, WBC_BROWSER_TAB_PICKER_TOGGLE_DEBOUNCE_MS, wbcBrowserTabPickerToggleIsDebounced, wbcBrowserTabPickerPayload, wbcClampBrowserWindowFrame, wbcBrowserComposerDockFrame, wbcKeepBrowserWindowClearOfComposer, WBC_BROWSER_FRAME_STORAGE_PREFIX, wbcLoadBrowserWindowFrame, wbcSaveBrowserWindowFrame, wbcBrowserAvoidancePlan, wbcNotifyBrowserLayoutChanged, wbcNotifyBrowserWindowInteraction, wbcRectsOverlap, wbcPageContextMenuPlacement, wbcCanOpenPageContextMenu, wbcPointInsideResourceShelf, wbcConversationTabAtPoint, wbcCycleTopbarSessionTab, wbcHandleHorizontalWheelGesture, wbcNotifyResourceShelfPointerDrag }
