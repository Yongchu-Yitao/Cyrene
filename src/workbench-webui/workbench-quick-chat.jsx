// Quick Chat surface. The Electron main process owns the global shortcut,
// screenshot and window lifecycle; this renderer only loads lightweight
// Workbench data and presents the transient entry surface.

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

function quickChatJson(url) {
  if (window.WorkbenchAPI && typeof window.WorkbenchAPI.json === "function") {
    return window.WorkbenchAPI.json(url, { toast: false });
  }
  return fetch(url).then(function (response) {
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.json();
  });
}

function QuickChatApp() {
  var [loading, setLoading] = useQuickChatState(true);
  var [error, setError] = useQuickChatState("");
  var [projects, setProjects] = useQuickChatState([]);
  var [chats, setChats] = useQuickChatState([]);
  var [context, setContext] = useQuickChatState(null);
  var [targetChatId, setTargetChatId] = useQuickChatState("");
  var [draft, setDraft] = useQuickChatState("");
  var textareaRef = useQuickChatRef(null);

  useQuickChatEffect(function () {
    var cancelled = false;
    var bridge = window.cyrene && window.cyrene.quickChat;
    var contextPromise = bridge && typeof bridge.getLaunchContext === "function"
      ? bridge.getLaunchContext()
      : Promise.resolve({ screenshot: null, screenPermissionStatus: "unknown" });

    Promise.all([
      quickChatJson("/api/projects"),
      quickChatJson("/api/workbench/chats"),
      contextPromise,
    ]).then(function (results) {
      if (cancelled) return;
      var projectPayload = results[0] || {};
      var chatPayload = results[1] || {};
      setProjects(Array.isArray(projectPayload.projects) ? projectPayload.projects : []);
      setChats(Array.isArray(chatPayload.chats) ? chatPayload.chats.slice(0, 40) : []);
      setContext(results[2] || null);
      setLoading(false);
      setTimeout(function () {
        if (textareaRef.current) textareaRef.current.focus();
      }, 0);
    }).catch(function (err) {
      if (cancelled) return;
      setError(String((err && err.message) || err || quickChatText("加载失败", "Failed to load")));
      setLoading(false);
    });

    var unsubscribe = bridge && typeof bridge.onContextUpdated === "function"
      ? bridge.onContextUpdated(function (nextContext) {
          if (!cancelled) setContext(nextContext || null);
        })
      : null;

    function onKeyDown(event) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (bridge && typeof bridge.close === "function") bridge.close();
      else window.close();
    }
    window.addEventListener("keydown", onKeyDown);
    return function () {
      cancelled = true;
      window.removeEventListener("keydown", onKeyDown);
      if (typeof unsubscribe === "function") unsubscribe();
    };
  }, []);

  var projectById = useQuickChatMemo(function () {
    var map = {};
    projects.forEach(function (project) { map[String(project.id || "")] = project; });
    return map;
  }, [projects]);

  var defaultProject = useQuickChatMemo(function () {
    return projects.find(function (project) { return project && project.dataKey === "default"; })
      || projects[0]
      || null;
  }, [projects]);

  function closeWindow() {
    var bridge = window.cyrene && window.cyrene.quickChat;
    if (bridge && typeof bridge.close === "function") bridge.close();
    else window.close();
  }

  function openPermissionSettings() {
    var bridge = window.cyrene && window.cyrene.quickChat;
    if (bridge && typeof bridge.openScreenPermissionSettings === "function") {
      bridge.openScreenPermissionSettings();
    }
  }

  var screenshot = context && context.screenshot;
  var permissionStatus = context && context.screenPermissionStatus;
  var selectedChat = chats.find(function (chat) { return chat.id === targetChatId; }) || null;
  var selectedProject = selectedChat
    ? projectById[String(selectedChat.projectId || "")]
    : defaultProject;

  return (
    <div className="wbq-shell" data-screen-label="Cyrene · quick chat">
      <header className="wbq-header">
        <div className="wbq-brand">
          <span className="brand-mark" aria-hidden="true"></span>
          <div>
            <strong>{quickChatText("快捷对话", "Quick Chat")}</strong>
            <small>{quickChatText("先截图，再选择对话", "Screenshot captured before the window opens")}</small>
          </div>
        </div>
        <button type="button" className="wbq-close" onClick={closeWindow} aria-label={quickChatText("关闭", "Close")}>ESC</button>
      </header>

      <main className="wbq-main">
        {loading ? (
          <div className="wbq-state"><span className="wb-spinner small" />{quickChatText("正在准备快捷对话…", "Preparing quick chat…")}</div>
        ) : error ? (
          <div className="wbq-state is-error">{error}</div>
        ) : (
          <>
            <div className="wbq-toolbar">
              <label className="wbq-target">
                <span>{quickChatText("发送到", "Send to")}</span>
                <select value={targetChatId} onChange={function (event) { setTargetChatId(event.target.value); }}>
                  <option value="">
                    {quickChatText(
                      "在默认项目新建对话",
                      "New chat in the default project"
                    )}
                    {defaultProject ? " · " + defaultProject.name : ""}
                  </option>
                  {chats.map(function (chat) {
                    var project = projectById[String(chat.projectId || "")];
                    return (
                      <option key={chat.id} value={chat.id}>
                        {(project && project.name ? project.name + " · " : "") + (chat.title || quickChatText("新对话", "New chat"))}
                      </option>
                    );
                  })}
                </select>
              </label>

              <div className={"wbq-screenshot-status" + (screenshot ? " is-ready" : "")}>
                <span className="wbq-screenshot-dot" aria-hidden="true"></span>
                {screenshot
                  ? quickChatText(
                      "已截取 " + screenshot.width + " × " + screenshot.height,
                      "Captured " + screenshot.width + " × " + screenshot.height
                    )
                  : quickChatText("未获取截图", "No screenshot available")}
              </div>
            </div>

            {permissionStatus === "denied" || permissionStatus === "restricted" ? (
              <div className="wbq-permission">
                <span>{quickChatText("需要允许 Cyrene 录制屏幕，授权后请重启应用。", "Allow screen recording for Cyrene, then restart the app.")}</span>
                <button type="button" onClick={openPermissionSettings}>{quickChatText("打开系统设置", "Open Settings")}</button>
              </div>
            ) : null}

            <div className="wbq-composer-shell">
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={function (event) { setDraft(event.target.value); }}
                rows={4}
                placeholder={quickChatText("给 Cyrene 发送消息…", "Message Cyrene…")}
              />
              <div className="wbq-composer-footer">
                <span>
                  {selectedProject
                    ? quickChatText("项目：", "Project: ") + selectedProject.name
                    : quickChatText("未找到默认项目", "Default project not found")}
                </span>
                <button type="button" className="wbq-send-placeholder" disabled title={quickChatText("完整输入框将在下一实现阶段接入", "The full composer is connected in the next implementation stage")}>
                  {quickChatText("发送", "Send")}
                </button>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

window.QuickChatApp = QuickChatApp;
