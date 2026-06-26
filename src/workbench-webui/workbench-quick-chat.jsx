// Quick Chat surface. The Electron main process owns the global shortcut,
// screenshot and window lifecycle; this renderer reuses the workbench data layer
// (WorkbenchChatModel) and the shared composer (window.WbcComposer) rather than
// forking a second input box, so attachments / commands / permission mode all
// stay in sync with the main chat.

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

// Inline icon so the surface stays self-contained (no dependency on WBC_ICONS
// load order). Matches the 1.8-stroke line style used across the composer chips.
var QUICK_CHAT_ICON = (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M21 11.5a8.5 8.5 0 0 1-12.2 7.6L3 21l1.9-5.8A8.5 8.5 0 1 1 21 11.5Z" />
  </svg>
);

function quickChatRenderMarkdown(text) {
  if (typeof window.wbcRenderMarkdown === "function") return window.wbcRenderMarkdown(text);
  return String(text == null ? "" : text);
}

function QuickChatApp() {
  var model = window.WorkbenchChatModel;
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
  var [sending, setSending] = useQuickChatState(false);
  var [sendError, setSendError] = useQuickChatState("");
  var [screenshotAddedAt, setScreenshotAddedAt] = useQuickChatState("");
  // Bumped on a session reset (re-trigger) to remount the composer with a clean
  // slate (the window only hides, so its React state would otherwise survive).
  var [composerKey, setComposerKey] = useQuickChatState(0);
  // Running transcript for THIS quick session. The window stays open after a
  // send so the reply streams in-place and the user can keep chatting; a fresh
  // shortcut trigger (new screenshot) clears it via resetSession().
  var [thread, setThread] = useQuickChatState([]);
  var streamIdRef = useQuickChatRef("");
  var threadRef = useQuickChatRef(null);
  // Remembers a conversation created for the "new chat" path so a failed send
  // reuses it on retry instead of spawning a second empty chat.
  var createdChatIdRef = useQuickChatRef("");
  var abortRef = useQuickChatRef(null);
  var searchTimerRef = useQuickChatRef(null);
  var searchRef = useQuickChatRef("");
  // Measured to auto-size the Electron window to the content height.
  var headerRef = useQuickChatRef(null);
  var mainRef = useQuickChatRef(null);
  var contentRef = useQuickChatRef(null);

  function bridge() {
    return (window.cyrene && window.cyrene.quickChat) || null;
  }

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

  // Keep the transcript pinned to the newest message as the reply streams in.
  useQuickChatEffect(function () {
    var el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread]);

  // Keep the accent live-synced when the user changes the theme color in the
  // main window (it's also applied at module load so the first paint is correct).
  useQuickChatEffect(function () {
    quickChatApplyAccent();
    function onAccentChange() { quickChatApplyAccent(); }
    window.addEventListener("cyrene-tweak-accent-change", onAccentChange);
    window.addEventListener("cyrene-tweak-theme-change", onAccentChange);
    return function () {
      window.removeEventListener("cyrene-tweak-accent-change", onAccentChange);
      window.removeEventListener("cyrene-tweak-theme-change", onAccentChange);
    };
  }, []);

  // Auto-size the Electron window to the content height so no state (empty /
  // permission banner / transcript / multi-line draft) leaves dead space, and
  // the upward permission menu always has room. .wbq-content hugs its content,
  // so its height + header + main padding is the exact desired window height.
  useQuickChatEffect(function () {
    var b = bridge();
    if (!b || typeof b.resize !== "function" || typeof ResizeObserver === "undefined") return;
    var raf = 0;
    var last = 0;
    function measure() {
      raf = 0;
      var header = headerRef.current, main = mainRef.current, content = contentRef.current;
      if (!header || !main || !content) return;
      var cs = window.getComputedStyle(main);
      var padV = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
      var h = Math.ceil(header.offsetHeight + content.offsetHeight + padV);
      if (!h || Math.abs(h - last) < 4) return;
      last = h;
      try { b.resize({ height: h }); } catch (e) {}
    }
    function schedule() { if (!raf) raf = window.requestAnimationFrame(measure); }
    var ro = new ResizeObserver(schedule);
    ro.observe(contentRef.current);
    schedule();
    return function () {
      if (raf) window.cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

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

  // Full reset for a brand-new quick session (a fresh shortcut trigger). Clears
  // the transcript, forgets the pinned/auto-created chat, wipes the namespaced
  // draft and remounts the composer so its internal state resets.
  function resetSession() {
    var composerChatId = selected ? selected.chatId : "";
    if (composerChatId && typeof window.wbcClearComposerDraft === "function") {
      window.wbcClearComposerDraft(composerChatId, "quick-chat:");
    }
    if (abortRef.current) { try { abortRef.current.abort(); } catch (e) {} }
    createdChatIdRef.current = "";
    abortRef.current = null;
    streamIdRef.current = "";
    setThread([]);
    setSelected(null);
    setSendError("");
    setSending(false);
    setComposerKey(function (k) { return k + 1; });
  }

  function appendThreadMessage(message) {
    setThread(function (prev) { return prev.concat([message]); });
  }

  function updateThreadMessage(id, updater) {
    setThread(function (prev) {
      return prev.map(function (m) { return m.id === id ? updater(m) : m; });
    });
  }

  // Ensure a streaming assistant bubble exists; created lazily so a reply_delta
  // that lands before reply_start still renders.
  function ensureAssistantBubble() {
    if (streamIdRef.current) return streamIdRef.current;
    var id = "a" + Date.now() + "-" + Math.random().toString(36).slice(2, 7);
    streamIdRef.current = id;
    appendThreadMessage({ id: id, role: "assistant", text: "", streaming: true });
    return id;
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
  // The window stays open and the reply streams into the in-place transcript so
  // the user can keep chatting; follow-ups reuse the same (pinned) chat.
  function handleSend(input) {
    if (sending) return;
    setSendError("");
    var target = selected;
    var projectId = target ? target.projectId : (defaultProject ? defaultProject.id : "");
    appendThreadMessage({
      id: "u" + Date.now() + "-" + Math.random().toString(36).slice(2, 7),
      role: "user",
      text: input.message || "",
      attachmentCount: (input.attachments || []).length,
    });
    streamIdRef.current = "";
    setSending(true);
    ensureChatId(target, projectId).then(function (resolved) {
      var ac = (typeof AbortController !== "undefined") ? new AbortController() : null;
      abortRef.current = ac;
      return model.sendMessage(resolved.chatId, input, {
        onAck: function () {
          // The run is durable server-side; tell the main window so its chat list
          // / transcript stays in sync. We keep our own stream open and render the
          // reply here rather than closing, so the conversation can continue.
          notifySent(resolved.projectId, resolved.chatId);
        },
        onReplyStart: function () { ensureAssistantBubble(); },
        onReplyDelta: function (delta) {
          var id = ensureAssistantBubble();
          updateThreadMessage(id, function (m) { return { ...m, text: m.text + (delta || "") }; });
        },
        onReplyDone: function (full) {
          var id = ensureAssistantBubble();
          updateThreadMessage(id, function (m) { return { ...m, text: full || m.text, streaming: false }; });
          streamIdRef.current = "";
          abortRef.current = null;
          setSending(false);
        },
        onError: function (err) {
          streamIdRef.current = "";
          setSending(false);
          showSendError(err);
        },
      }, ac ? ac.signal : undefined);
    }).catch(function (err) {
      if (err && err.name === "AbortError") { setSending(false); return; }
      setSending(false);
      showSendError(err);
    });
  }

  function handleInterrupt() {
    if (abortRef.current) { try { abortRef.current.abort(); } catch (e) {} }
    if (streamIdRef.current) {
      updateThreadMessage(streamIdRef.current, function (m) { return { ...m, streaming: false }; });
      streamIdRef.current = "";
    }
    setSending(false);
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

  return (
    <div className="workbench-shell wbq-shell" data-screen-label="Cyrene · quick chat">
      <header className="wbq-header" ref={headerRef}>
        <div className="wbq-brand">
          <span className="brand-mark" aria-hidden="true"></span>
          <strong>{quickChatText("快捷对话", "Quick Chat")}</strong>
        </div>
        <button type="button" className="wbq-close" onClick={closeWindow} aria-label={quickChatText("关闭", "Close")}>ESC</button>
      </header>

      <main className="wbq-main" ref={mainRef}>
        <div className="wbq-content" ref={contentRef}>
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

            {thread.length > 0 ? (
              <div className="wbq-thread" ref={threadRef}>
                {thread.map(function (m) {
                  return (
                    <div key={m.id} className={"wbq-msg " + m.role}>
                      <div className={"wbq-bubble" + (m.streaming ? " streaming" : "")}>
                        {m.role === "assistant"
                          ? (m.text
                              ? <div className="wbq-md" dangerouslySetInnerHTML={{ __html: quickChatRenderMarkdown(m.text) }} />
                              : <span className="wbq-typing" aria-hidden="true"><i></i><i></i><i></i></span>)
                          : <span className="wbq-msg-text">{m.text}</span>}
                        {m.role === "user" && m.attachmentCount > 0
                          ? <span className="wbq-msg-attach">{quickChatText("含截图", "with screenshot")}</span>
                          : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : null}

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

// Apply the user's accent before first paint when this bundle loads inside the
// quick-chat window (the main window drives its own accent via app.jsx).
(function () {
  try {
    var surface = new URLSearchParams(window.location.search || "").get("surface");
    if (surface === "quick-chat") quickChatApplyAccent();
  } catch (e) {}
})();

window.QuickChatApp = QuickChatApp;
