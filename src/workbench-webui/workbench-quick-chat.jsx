// Quick Chat surface. The Electron main process owns the global shortcut,
// screenshot and window lifecycle; this renderer reuses the workbench data layer
// (WorkbenchChatModel), the shared composer (window.WbcComposer) AND the shared
// run-manager + message cards (window.WorkbenchChatRuntimes /
// window.WbcUserMessage / WbcAssistantMessage / WbcLiveMessage) rather than
// forking a second input box or a simplified transcript — so attachments,
// commands, permission mode, tool-call traces and the live "thinking" card all
// stay identical to the main chat.

var {
  useEffect: useQuickChatEffect,
  useMemo: useQuickChatMemo,
  useRef: useQuickChatRef,
  useState: useQuickChatState,
} = React;

function quickChatText(zh, en) {
  try {
    var lang = window.WorkbenchI18n && window.WorkbenchI18n.getLang
      ? window.WorkbenchI18n.getLang()
      : "";
    return String(lang || "").toLowerCase().startsWith("zh") ? zh : en;
  } catch (e) {
    return zh;
  }
}

// The quick-chat window is a standalone surface that never mounts MainApp, so it
// must drive the accent ("主题色") itself — otherwise it ignores the theme color
// chosen in settings and falls back to the stylesheet default. Mirrors app.jsx;
// localStorage is shared across same-origin Electron windows, so the value the
// main window saved is readable here.
function quickChatReadTweak(key, fallback) {
  try {
    var raw = localStorage.getItem("cyrene-tweak-" + key);
    return raw !== null ? JSON.parse(raw) : fallback;
  } catch (e) {
    return fallback;
  }
}

function quickChatApplyAccent() {
  var root = document.documentElement.style;
  var a = quickChatReadTweak("accent", null);
  a = typeof a === "string" ? a.trim() : "";
  var m = a.match(/^#([0-9a-f]{6})$/i);
  if (!m) {
    root.removeProperty("--accent");
    root.removeProperty("--accent-faint");
    root.removeProperty("--accent-dim");
    root.removeProperty("--accent-text");
    return;
  }
  var r = parseInt(m[1].slice(0, 2), 16);
  var g = parseInt(m[1].slice(2, 4), 16);
  var b = parseInt(m[1].slice(4, 6), 16);
  root.setProperty("--accent", a);
  root.setProperty("--accent-faint", "rgba(" + r + "," + g + "," + b + ",0.08)");
  root.setProperty("--accent-dim", "rgba(" + r + "," + g + "," + b + ",0.35)");
  root.setProperty("--accent-text", (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.55 ? "#0d1612" : "#ffffff");
}

// Same story for light/dark: this window never mounts MainApp, so the theme
// effect that sets html[data-theme] (app.jsx) never runs here. The index.html
// boot script paints it once on first load, but the window is then only
// hidden/shown — never reloaded — so without this it stays frozen on whatever
// theme was active when it first opened. Re-read the shared mode and resolve
// "system" against the OS, matching app.jsx's resolveActualTheme.
function quickChatApplyTheme() {
  var mode = quickChatReadTweak("theme", "system");
  var actual = mode === "dark" || mode === "light"
    ? mode
    : (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = actual;
}

function quickChatApplyPresentation() {
  document.documentElement.dataset.density = quickChatReadTweak("density", "cozy") || "cozy";
  document.documentElement.dataset.textSize = quickChatReadTweak("textSize", "default") || "default";
}

function quickChatJson(url) {
  if (window.WorkbenchAPI && typeof window.WorkbenchAPI.json === "function") {
    return window.WorkbenchAPI.json(url, { toast: false });
  }
  return fetch(url).then(function (response) {
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.json();
  });
}

var QUICK_CHAT_TARGETS_URL = "/api/workbench/quick-chat/targets";

// Window heights (CSS px). Empty/idle stays compact but tall enough that the
// composer's upward permission / slash menus always have room above them; after
// the first message the window grows so the conversation has space.
var QUICK_CHAT_GROW_HEIGHT = 640;

// Inline icon so the surface stays self-contained (no dependency on WBC_ICONS
// load order). Matches the 1.8-stroke line style used across the composer chips.
var QUICK_CHAT_ICON = (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M21 11.5a8.5 8.5 0 0 1-12.2 7.6L3 21l1.9-5.8A8.5 8.5 0 1 1 21 11.5Z" />
  </svg>
);

// Append messages to the local transcript, skipping any whose id is already
// present (the run-manager hooks can fire more than once across a reconnect).
function quickChatDedupAppend(prev, additions) {
  var list = Array.isArray(additions) ? additions : [];
  if (!list.length) return prev;
  var seen = {};
  prev.forEach(function (m) { seen[String((m && m.id) || "")] = true; });
  var add = [];
  list.forEach(function (m) {
    var id = String((m && m.id) || "");
    if (!id || seen[id]) return;
    seen[id] = true;
    add.push(m);
  });
  return add.length ? prev.concat(add) : prev;
}

function quickChatConfirmUserMessage(prev, confirmation) {
  var userMessage = confirmation && confirmation.userMessage;
  if (!userMessage) return prev;
  var optimisticId = String(confirmation.optimisticId || "");
  if (optimisticId) {
    for (var i = 0; i < prev.length; i++) {
      if (String(prev[i] && prev[i].id || "") !== optimisticId) continue;
      var confirmed = prev.slice();
      confirmed[i] = { ...userMessage, optimistic: false };
      return confirmed;
    }
  }
  return quickChatDedupAppend(prev, [userMessage]);
}

function QuickChatApp() {
  var model = window.WorkbenchChatModel;
  // Shared singleton run-manager: owns the send stream and folds live SSE
  // tool-call / phase / subagent progress into the runtime, exactly like the
  // main conversation page. This renderer is a separate Electron window (its own
  // JS context), so its run-manager and transcript hooks never collide with the
  // main window's.
  var runtimeEngine = window.WorkbenchChatRuntimes;
  var [loading, setLoading] = useQuickChatState(true);
  var [error, setError] = useQuickChatState("");
  var [defaultProject, setDefaultProject] = useQuickChatState(null);
  var [targets, setTargets] = useQuickChatState([]);
  var [context, setContext] = useQuickChatState(null);
  // The chosen target as a full object (null = new chat in the default project).
  // Stored standalone — not looked up in `targets` — so a search that filters
  // the list out doesn't silently drop the selection.
  var [selected, setSelected] = useQuickChatState(null);
  var [pickerOpen, setPickerOpen] = useQuickChatState(false);
  var [search, setSearch] = useQuickChatState("");
  var [sendError, setSendError] = useQuickChatState("");
  var [screenshotAddedAt, setScreenshotAddedAt] = useQuickChatState("");
  // Bumped on a session reset (re-trigger) to remount the composer with a clean
  // slate (the window only hides, so its React state would otherwise survive).
  var [composerKey, setComposerKey] = useQuickChatState(0);
  // Committed transcript for THIS quick session (built from run-manager hooks).
  // The window stays open after a send so the conversation can continue; a fresh
  // shortcut trigger (new screenshot) clears it via resetSession().
  var [messages, setMessages] = useQuickChatState([]);
  // The conversation this session is bound to (an existing target's chat or a
  // lazily-created new chat). Drives which runtime / hooks we read.
  var [activeChatId, setActiveChatId] = useQuickChatState("");
  // Live runtime map mirrored from the run-manager so the live card re-renders.
  var [runtimes, setRuntimes] = useQuickChatState(function () {
    return runtimeEngine && runtimeEngine.snapshot ? runtimeEngine.snapshot() : {};
  });
  var activeChatIdRef = useQuickChatRef("");
  var activeProjectIdRef = useQuickChatRef("");
  // Remembers a conversation created for the "new chat" path so a failed send
  // reuses it on retry instead of spawning a second empty chat.
  var createdChatIdRef = useQuickChatRef("");
  var searchTimerRef = useQuickChatRef(null);
  var searchRef = useQuickChatRef("");
  // One-shot: grow the window the first time a message is sent in a session.
  var grewRef = useQuickChatRef(false);
  // Transcript scroller; stickRef tracks whether the user is reading scrollback.
  var scrollRef = useQuickChatRef(null);
  var stickRef = useQuickChatRef(true);

  var runtime = activeChatId && runtimes ? (runtimes[activeChatId] || null) : null;
  var sending = !!runtime;

  function bridge() {
    return (window.cyrene && window.cyrene.quickChat) || null;
  }

  useQuickChatEffect(function () { activeChatIdRef.current = activeChatId; }, [activeChatId]);

  useQuickChatEffect(function () {
    var cancelled = false;
    var b = bridge();
    var contextPromise = b && typeof b.getLaunchContext === "function"
      ? b.getLaunchContext()
      : Promise.resolve({ screenshot: null, screenPermissionStatus: "unknown" });

    Promise.all([
      quickChatJson(QUICK_CHAT_TARGETS_URL + "?limit=40"),
      contextPromise,
    ]).then(function (results) {
      if (cancelled) return;
      var payload = results[0] || {};
      setDefaultProject(payload.defaultProject || null);
      setTargets(Array.isArray(payload.targets) ? payload.targets : []);
      setContext(results[1] || null);
      setLoading(false);
    }).catch(function (err) {
      if (cancelled) return;
      setError(String((err && err.message) || err || quickChatText("加载失败", "Failed to load")));
      setLoading(false);
    });

    var unsubscribe = b && typeof b.onContextUpdated === "function"
      ? b.onContextUpdated(function (nextContext) {
          if (cancelled) return;
          setContext(nextContext || null);
          // A re-triggered shortcut replaces the screenshot — re-enable "add".
          setScreenshotAddedAt("");
          // A new trigger starts a fresh conversation: clear the transcript and
          // the pinned chat so the next message opens a new one.
          resetSession();
          // The window only hides between triggers, so refresh the target list
          // to pick up chats created since it was first opened.
          refetchTargets(searchRef.current);
        })
      : null;

    return function () {
      cancelled = true;
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
      if (typeof unsubscribe === "function") unsubscribe();
    };
  }, []);

  // Mirror the run-manager's runtime map so the live tool-call / reply card
  // re-renders as progress streams in.
  useQuickChatEffect(function () {
    if (!runtimeEngine || !runtimeEngine.subscribe) return;
    setRuntimes(runtimeEngine.snapshot());
    return runtimeEngine.subscribe(function (snap) { setRuntimes(snap); });
  }, []);

  // Register transcript hooks so a streaming run patches THIS session's local
  // transcript. Re-registered every render so closures never go stale; each hook
  // guards by the active chat id. Mirrors WorkbenchChatPage but scoped to the
  // quick session (we never re-pull the whole backing chat, keeping the window a
  // lightweight view of just this session's turns).
  useQuickChatEffect(function () {
    if (!runtimeEngine || !runtimeEngine.setHooks) return;
    runtimeEngine.setHooks({
      onUserMessage: function (chatId, userMessage) {
        if (chatId !== activeChatIdRef.current) return;
        setMessages(function (prev) { return quickChatDedupAppend(prev, [userMessage]); });
      },
      onUserMessageConfirmed: function (chatId, confirmation) {
        if (chatId !== activeChatIdRef.current) return;
        // Tell the main window only after the server accepted the turn; the
        // optimistic message itself is solely a local ordering/latency fix.
        notifySent(activeProjectIdRef.current, chatId);
        setMessages(function (prev) { return quickChatConfirmUserMessage(prev, confirmation); });
      },
      onAssistantSaved: function (chatId, assistantMessages) {
        if (chatId !== activeChatIdRef.current) return;
        setMessages(function (prev) { return quickChatDedupAppend(prev, assistantMessages); });
      },
      onAwaitingUser: function (chatId) {
        if (chatId !== activeChatIdRef.current) return;
        // Quick chat has no inline answer prompt; surface a hint and let the user
        // continue in the main window if a permission/clarification is needed.
        setSendError(quickChatText(
          "需要在主窗口里确认权限或回答问题后才能继续。",
          "Needs a permission/clarification answer in the main window to continue."
        ));
      },
      onError: function (chatId, err) {
        if (chatId !== activeChatIdRef.current) return;
        showSendError(err);
      },
    });
    return function () { runtimeEngine.setHooks(null); };
  });

  // Escape closes the picker first, then the window. Re-bound when the picker
  // toggles so it reads the current state without a ref.
  useQuickChatEffect(function () {
    function onKeyDown(event) {
      if (event.key !== "Escape") return;
      if (pickerOpen) { event.stopPropagation(); setPickerOpen(false); return; }
      closeWindow();
    }
    window.addEventListener("keydown", onKeyDown);
    return function () { window.removeEventListener("keydown", onKeyDown); };
  }, [pickerOpen]);

  // Keep the transcript pinned to the newest message as the reply streams in,
  // but only while the user is near the bottom — so scrolling up to read history
  // isn't yanked back down.
  useQuickChatEffect(function () {
    var el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [
    messages.length,
    runtime && runtime.text,
    runtime && runtime.progress && runtime.progress.length,
    runtime && runtime.segments && runtime.segments.length,
  ]);

  // Keep accent + light/dark live-synced with the main window. Both are applied
  // at module load too so the first paint is correct.
  useQuickChatEffect(function () {
    quickChatApplyAccent();
    quickChatApplyTheme();
    quickChatApplyPresentation();
    function onChange() { quickChatApplyAccent(); quickChatApplyTheme(); quickChatApplyPresentation(); }

    // The settings overlay lives in the main window, so its custom events fire
    // on a different renderer's `window` and never reach us — but the
    // localStorage writes they trigger surface here as `storage` events. That's
    // the only reliable cross-window signal; the custom-event listeners below
    // are kept only for parity in case this surface ever gains its own settings.
    function onStorage(e) {
      if (!e || !e.key) { onChange(); return; }
      if (e.key === "cyrene-tweak-theme" || e.key === "cyrene-theme-mode") quickChatApplyTheme();
      else if (e.key === "cyrene-tweak-accent") quickChatApplyAccent();
      else if (e.key === "cyrene-tweak-density" || e.key === "cyrene-tweak-textSize") quickChatApplyPresentation();
    }
    window.addEventListener("storage", onStorage);
    window.addEventListener("cyrene-tweak-accent-change", onChange);
    window.addEventListener("cyrene-tweak-theme-change", onChange);
    window.addEventListener("cyrene-tweak-density-change", onChange);
    window.addEventListener("cyrene-tweak-textSize-change", onChange);

    // Follow the OS while the user is on "system" mode.
    var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    function onSystem() { quickChatApplyTheme(); }
    if (mq && mq.addEventListener) mq.addEventListener("change", onSystem);

    return function () {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("cyrene-tweak-accent-change", onChange);
      window.removeEventListener("cyrene-tweak-theme-change", onChange);
      window.removeEventListener("cyrene-tweak-density-change", onChange);
      window.removeEventListener("cyrene-tweak-textSize-change", onChange);
      if (mq && mq.removeEventListener) mq.removeEventListener("change", onSystem);
    };
  }, []);

  function onScroll() {
    var el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  function closeWindow() {
    var b = bridge();
    if (b && typeof b.close === "function") b.close();
    else window.close();
  }

  function openPermissionSettings() {
    var b = bridge();
    if (b && typeof b.openScreenPermissionSettings === "function") {
      b.openScreenPermissionSettings();
    }
  }

  // Grow the window once per session on the first send so the conversation has
  // room. Never shrink — the user may have already enlarged it, and the layout
  // (flex transcript that fills the window) adapts to any size they pick.
  function maybeGrowWindow() {
    if (grewRef.current) return;
    grewRef.current = true;
    try { if (window.outerHeight && window.outerHeight >= QUICK_CHAT_GROW_HEIGHT) return; } catch (e) {}
    var b = bridge();
    if (b && typeof b.resize === "function") {
      try { b.resize({ height: QUICK_CHAT_GROW_HEIGHT }); } catch (e) {}
    }
  }

  // Project + chat passed to the shared composer. New-chat path → no chat object
  // and the default project; existing target → its own project + chat.
  var composerProject = useQuickChatMemo(function () {
    var source = selected
      ? {
          id: selected.projectId,
          name: selected.projectName,
          workspacePath: selected.workspacePath,
          model: selected.model,
        }
      : defaultProject;
    if (!source) return null;
    return {
      id: source.id,
      name: source.name,
      workspacePath: source.workspacePath || "",
      model: source.model || "",
    };
  }, [selected, defaultProject]);

  var composerChat = useQuickChatMemo(function () {
    if (!selected) return null;
    return { id: selected.chatId, legacy: false, model: selected.model };
  }, [selected]);

  function refetchTargets(query) {
    quickChatJson(QUICK_CHAT_TARGETS_URL + "?limit=40&q=" + encodeURIComponent(query || ""))
      .then(function (payload) {
        setTargets(Array.isArray(payload.targets) ? payload.targets : []);
        if (!payload.defaultProject) return;
        setDefaultProject(payload.defaultProject);
      })
      .catch(function () {});
  }

  function onSearchChange(event) {
    var value = event.target.value;
    setSearch(value);
    searchRef.current = value;
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(function () { refetchTargets(value); }, 200);
  }

  function selectTarget(target) {
    setSelected(target || null);
    setPickerOpen(false);
    setSendError("");
  }

  function ensureChatId(target, projectId) {
    if (target) return Promise.resolve({ chatId: target.chatId, projectId: target.projectId });
    if (createdChatIdRef.current) {
      return Promise.resolve({ chatId: createdChatIdRef.current, projectId: projectId });
    }
    if (!projectId) {
      return Promise.reject(new Error(quickChatText("未找到默认项目", "Default project not found")));
    }
    return model.createChat(projectId).then(function (chat) {
      createdChatIdRef.current = chat.id;
      return { chatId: chat.id, projectId: chat.projectId || projectId };
    });
  }

  // Full reset for a brand-new quick session (a fresh shortcut trigger). Stops
  // watching the previous run locally (it stays durable server-side), clears the
  // transcript, forgets the pinned/auto-created chat, wipes the namespaced draft
  // and remounts the composer so its internal state resets.
  function resetSession() {
    var composerChatId = selected ? selected.chatId : "";
    if (composerChatId && typeof window.wbcClearComposerDraft === "function") {
      window.wbcClearComposerDraft(composerChatId, "quick-chat:");
    }
    // Abort our local stream consumption only (not a server interrupt) so a run
    // started here keeps going in the background, visible in the main window.
    if (runtimeEngine && runtimeEngine.abort && activeChatIdRef.current) {
      try { runtimeEngine.abort(activeChatIdRef.current); } catch (e) {}
    }
    createdChatIdRef.current = "";
    activeChatIdRef.current = "";
    activeProjectIdRef.current = "";
    grewRef.current = false;
    stickRef.current = true;
    setActiveChatId("");
    setMessages([]);
    setSelected(null);
    setSendError("");
    setComposerKey(function (k) { return k + 1; });
  }

  function notifySent(projectId, chatId) {
    var b = bridge();
    if (b && typeof b.notifySent === "function") {
      try { b.notifySent({ projectId: projectId, chatId: chatId }); } catch (e) {}
    }
  }

  function showSendError(err) {
    var code = err && err.code;
    if (code === "chat_run_in_progress") {
      setSendError(quickChatText(
        "该对话正在回复中，请换一个对话或稍后再试。",
        "That chat is still replying — pick another or try again."
      ));
    } else {
      setSendError(String((err && err.message) || quickChatText("发送失败，请重试。", "Send failed. Try again.")));
    }
  }

  // input: { message, attachments, mode, command } from the shared composer.
  // The window stays open and the reply (with tool-call cards) streams into the
  // in-place transcript via the run-manager so the user can keep chatting;
  // follow-ups reuse the same (pinned) chat.
  function handleSend(input) {
    if (sending || !runtimeEngine) return;
    setSendError("");
    stickRef.current = true;
    var target = selected;
    var projectId = target ? target.projectId : (defaultProject ? defaultProject.id : "");
    ensureChatId(target, projectId).then(function (resolved) {
      activeChatIdRef.current = resolved.chatId;
      activeProjectIdRef.current = resolved.projectId;
      setActiveChatId(resolved.chatId);
      maybeGrowWindow();
      // The engine owns the stream (durable server-side) and enforces a single
      // in-flight run per conversation; null = a run was already in flight.
      runtimeEngine.start(resolved.chatId, input || {}, model);
    }).catch(function (err) {
      showSendError(err);
    });
  }

  function handleInterrupt() {
    if (runtimeEngine && runtimeEngine.interrupt) {
      runtimeEngine.interrupt(activeChatIdRef.current, model);
    }
  }

  function handleGuidance(message) {
    var chatId = activeChatIdRef.current;
    var text = String(message || "").trim();
    if (!chatId || !text || !sending) return Promise.resolve(null);
    setSendError("");
    return model.sendGuidance(chatId, text, "guide_" + Date.now()).catch(function (err) {
      if (err && err.code === "chat_not_running" && runtimeEngine.deferSend) {
        runtimeEngine.deferSend(chatId, { message: text }, model);
        return { deferred: true };
      }
      showSendError(err);
      throw err;
    });
  }

  var screenshot = context && context.screenshot;
  var permissionStatus = context && context.screenPermissionStatus;
  var screenshotKey = screenshot ? (screenshot.capturedAt || "ready") : "";
  var screenshotAdded = !!screenshot && screenshotAddedAt === screenshotKey;

  function addScreenshot() {
    var b = bridge();
    if (!b || typeof b.getScreenshot !== "function") return;
    b.getScreenshot().then(function (shot) {
      if (!shot || !shot.bytes) {
        if (window.showToast) window.showToast(quickChatText("截图不可用", "Screenshot unavailable"), "error");
        return;
      }
      var bytes = shot.bytes instanceof Uint8Array ? shot.bytes : new Uint8Array(shot.bytes);
      var file = new File([bytes], "screenshot-" + Date.now() + ".png", { type: shot.mimeType || "image/png" });
      // The shared composer listens for this event, uploads via the normal
      // chat upload endpoint and shows the thumbnail in its attachment row.
      window.dispatchEvent(new CustomEvent("cyrene:add-chat-attachments", { detail: { files: [file] } }));
      setScreenshotAddedAt(screenshotKey);
    }).catch(function () {
      if (window.showToast) window.showToast(quickChatText("截图读取失败", "Failed to read screenshot"), "error");
    });
  }

  var targetLabel = selected
    ? ((selected.projectName ? selected.projectName + " · " : "") + (selected.title || quickChatText("新对话", "New chat")))
    : (quickChatText("新建对话", "New chat") + (defaultProject ? " · " + defaultProject.name : ""));

  var composerReady = typeof window.WbcComposer === "function" && composerProject;
  var hasTranscript = messages.length > 0 || !!runtime;

  return (
    <div className="workbench-shell wbq-shell" data-screen-label="Cyrene · quick chat">
      <header className="wbq-header">
        <div className="wbq-brand">
          <span className="brand-mark" aria-hidden="true"></span>
          <strong>{quickChatText("快捷对话", "Quick Chat")}</strong>
        </div>
        <button type="button" className="wbq-close" onClick={closeWindow} aria-label={quickChatText("关闭", "Close")}>ESC</button>
      </header>

      <main className="wbq-main">
        <div className="wbq-content">
        {loading ? (
          <div className="wbq-state"><span className="wb-spinner small" />{quickChatText("正在准备快捷对话…", "Preparing quick chat…")}</div>
        ) : error ? (
          <div className="wbq-state is-error">{error}</div>
        ) : (
          <>
            <div className="wbq-toolbar">
              <div className="wbq-target">
                <span className="wbq-target-label">{quickChatText("发送到", "Send to")}</span>
                <div className="wbq-target-pop">
                  <button
                    type="button"
                    className={"wbq-target-chip" + (pickerOpen ? " active" : "")}
                    onClick={function () { setPickerOpen(!pickerOpen); }}
                    title={targetLabel}
                  >
                    <span className="wbq-target-ic" aria-hidden="true">{QUICK_CHAT_ICON}</span>
                    <span className="wbq-target-text">{targetLabel}</span>
                    <span className="wbq-caret" aria-hidden="true">▾</span>
                  </button>
                  {pickerOpen && (
                    <QuickChatPicker
                      targets={targets}
                      defaultProject={defaultProject}
                      selectedChatId={selected ? selected.chatId : ""}
                      search={search}
                      onSearchChange={onSearchChange}
                      onSelect={selectTarget}
                      onClose={function () { setPickerOpen(false); }}
                    />
                  )}
                </div>
              </div>

              {!screenshot ? (
                <span className="wbq-screenshot-status">
                  <span className="wbq-screenshot-dot" aria-hidden="true"></span>
                  {quickChatText("未获取截图", "No screenshot")}
                </span>
              ) : !screenshotAdded ? (
                // Waits here until the user adds it; the button disappears once
                // the screenshot has been handed to the composer as an attachment.
                <button
                  type="button"
                  className="wbq-screenshot-btn"
                  onClick={addScreenshot}
                  title={quickChatText("把刚才截取的屏幕作为附件", "Attach the screen captured a moment ago")}
                >
                  <span className="wbq-screenshot-dot" aria-hidden="true"></span>
                  {quickChatText("添加截图 " + screenshot.width + "×" + screenshot.height, "Add screenshot " + screenshot.width + "×" + screenshot.height)}
                </button>
              ) : null}
            </div>

            {(permissionStatus === "denied" || permissionStatus === "restricted") ? (
              <div className="wbq-permission">
                <span>{quickChatText("需要允许 Cyrene 录制屏幕，授权后请重启应用。", "Allow screen recording for Cyrene, then restart the app.")}</span>
                <button type="button" onClick={openPermissionSettings}>{quickChatText("打开系统设置", "Open Settings")}</button>
              </div>
            ) : null}

            {/* Transcript reuses the full chat's message cards so tool-call
                traces / attachments / the live "thinking" card all match the
                main UI. Always rendered (even empty) so it fills the window —
                the composer stays pinned at the bottom with room above for its
                upward menus, and the user can scroll the history. */}
            <div className="wbc-thread wbq-thread" ref={scrollRef} onScroll={onScroll}>
              {!hasTranscript ? (
                <div className="wbc-empty-thread wbq-empty">
                  <div className="wbc-empty-icon">{QUICK_CHAT_ICON}</div>
                  <p>
                    {screenshot
                      ? quickChatText("截图好了，问我点什么吧", "Screenshot ready — ask away")
                      : quickChatText("问我点什么吧", "Ask me anything")}
                  </p>
                </div>
              ) : null}
              {messages.map(function (m) {
                return m.role === "user"
                  ? <window.WbcUserMessage key={m.id} msg={m} />
                  : <window.WbcAssistantMessage key={m.id} msg={m} />;
              })}
              {runtime ? <window.WbcLiveMessage runtime={runtime} /> : null}
            </div>

            <div className="wbq-footer">
              {sendError ? <div className="wbq-send-error">{sendError}</div> : null}

              {composerReady ? (
                <div className="wbq-composer-host">
                  <window.WbcComposer
                    key={composerKey}
                    chat={composerChat}
                    project={composerProject}
                    running={sending}
                    onSend={handleSend}
                    onGuidance={handleGuidance}
                    onInterrupt={handleInterrupt}
                    draftNamespace="quick-chat:"
                    autoFocus={true}
                  />
                </div>
              ) : (
                <div className="wbq-state is-error">
                  {composerProject
                    ? quickChatText("输入框组件加载失败", "Composer failed to load")
                    : quickChatText("未找到可用项目，请先在主窗口创建项目。", "No project available — create one in the main window first.")}
                </div>
              )}
            </div>
          </>
        )}
        </div>
      </main>
      {/* Render the shared toast/confirm host so the composer's upload errors
          (and any other feedback) surface in this window too. */}
      {typeof window.WorkbenchFeedbackHost === "function"
        ? React.createElement(window.WorkbenchFeedbackHost)
        : null}
    </div>
  );
}

// Searchable cross-project conversation picker. Modern (writable) chats only;
// running chats are flagged and disabled so a send can't hit a 409.
function QuickChatPicker({ targets, defaultProject, selectedChatId, search, onSearchChange, onSelect, onClose }) {
  var searchRef = useQuickChatRef(null);
  useQuickChatEffect(function () {
    if (searchRef.current) searchRef.current.focus();
  }, []);
  return (
    <>
      <div className="wbq-picker-backdrop" onClick={onClose}></div>
      <div className="wbq-picker" role="listbox">
        <input
          ref={searchRef}
          className="wbq-picker-search"
          value={search}
          onChange={onSearchChange}
          placeholder={quickChatText("搜索对话、项目…", "Search chats, projects…")}
        />
        <div className="wbq-picker-list">
          <button
            type="button"
            className={"wbq-picker-item" + (!selectedChatId ? " active" : "")}
            onClick={function () { onSelect(null); }}
          >
            <span className="wbq-picker-title">{quickChatText("新建对话", "New chat")}</span>
            <span className="wbq-picker-sub">
              {quickChatText("默认项目", "Default project") + (defaultProject ? " · " + defaultProject.name : "")}
            </span>
          </button>
          {targets.map(function (item) {
            var on = item.chatId === selectedChatId;
            return (
              <button
                key={item.chatId}
                type="button"
                className={"wbq-picker-item" + (on ? " active" : "") + (item.running ? " is-running" : "")}
                disabled={item.running}
                onClick={function () { if (!item.running) onSelect(item); }}
                title={item.preview || ""}
              >
                <span className="wbq-picker-title">{item.title || quickChatText("新对话", "New chat")}</span>
                <span className="wbq-picker-sub">
                  {item.projectName || quickChatText("未知项目", "Unknown project")}
                  {item.running ? " · " + quickChatText("回复中", "Replying") : ""}
                </span>
              </button>
            );
          })}
          {targets.length === 0 && (
            <div className="wbq-picker-empty">{quickChatText("没有匹配的对话", "No matching chats")}</div>
          )}
        </div>
      </div>
    </>
  );
}

// Apply the user's accent + light/dark before first paint when this bundle
// loads inside the quick-chat window (the main window drives both via app.jsx).
(function () {
  try {
    var surface = new URLSearchParams(window.location.search || "").get("surface");
    if (surface === "quick-chat") { quickChatApplyAccent(); quickChatApplyTheme(); quickChatApplyPresentation(); }
  } catch (e) {}
})();

window.QuickChatApp = QuickChatApp;
