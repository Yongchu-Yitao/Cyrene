// Workbench Profile page.
//
// Fully independent from the agent UI's profile popover (compiled/app.js). It has
// its own components, helpers, i18n keys (workbench-i18n) and styles (.wbp-* in
// workbench.css). It shares only the data layer (window.DATA / window.bumpData)
// and the backend (PUT /api/profile, GET /api/ui-data), which are infrastructure
// common to both shells — not UI code.
(function () {
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useRef = React.useRef;

  var WBP_COLORS = ["#1D9E75", "#378ADD", "#D4537E", "#BA7517", "#7F77DD", "#D85A30"];
  var WBP_EMOJI = ["😀", "🐱", "🚀", "🌟", "🦊", "🐼", "🌿", "🔥"];
  var WBP_FEATURE_LABELS = {
    web_search: { en: "Web search", zh: "联网搜索" },
    fetch_url: { en: "Fetch page", zh: "网页抓取" },
    run_shell: { en: "Shell", zh: "终端" },
    bash: { en: "Shell", zh: "终端" },
    read_file: { en: "Read file", zh: "读文件" },
    write_file: { en: "Write file", zh: "写文件" },
    edit_file: { en: "Edit file", zh: "改文件" },
    save_project_memory: { en: "Memory", zh: "记忆" },
    recall_memory: { en: "Recall", zh: "回忆" },
    recall_conversation: { en: "Recall chat", zh: "回忆对话" },
    search_project_memory: { en: "Search memory", zh: "搜索记忆" },
    schedule_task: { en: "Schedule", zh: "计划任务" },
    send_message_to_user: { en: "Message", zh: "发消息" },
  };

  function wbpInitials(user) {
    user = user || {};
    if (user.initials) return user.initials;
    var name = String(user.name || "U");
    var parts = name.split(/[\s._-]+/).filter(Boolean);
    return (parts.slice(0, 2).map(function (p) { return p[0]; }).join("") || name.slice(0, 2)).toUpperCase();
  }
  function wbpCompact(n) {
    n = Number(n) || 0;
    if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return String(n);
  }
  function wbpDuration(ms) {
    ms = Number(ms) || 0;
    if (ms < 1000) return "0s";
    var s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h > 0) return h + "h" + (m > 0 ? m + "m" : "");
    if (m > 0) return m + "m" + (sec > 0 ? sec + "s" : "");
    return sec + "s";
  }
  function wbpPeakHour(label, lang) {
    label = String(label || "");
    if (label.indexOf("-") < 0) return label || "—";
    var ends = label.split("-").map(function (s) { return s.replace(":00", ""); });
    return lang === "zh" ? (ends[0] + "–" + ends[1] + "点") : (ends[0] + "–" + ends[1]);
  }
  function wbpFeatureLabel(tool, lang) {
    tool = String(tool || "");
    var hit = WBP_FEATURE_LABELS[tool];
    if (hit) return hit[lang] || hit.en;
    if (tool.indexOf("browser") === 0) return lang === "zh" ? "浏览器" : "Browser";
    return tool.replace(/[_-]+/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }
  function wbpSpend(usage, lang) {
    if (typeof window.formatLocalizedSpend === "function") return window.formatLocalizedSpend(usage, lang);
    return (usage && usage.spend) || "—";
  }

  // Avatar: uploaded image > emoji > initials on an optional colour.
  function WorkbenchAvatar(props) {
    var user = props.user || {};
    var size = props.size || 32;
    var base = { width: size + "px", height: size + "px", fontSize: Math.round(size * 0.42) + "px" };
    if (user.avatar) {
      return <span className="wbp-avatar" style={Object.assign({}, base, { backgroundImage: "url(" + user.avatar + ")", backgroundSize: "cover", backgroundPosition: "center", borderColor: "transparent" })} aria-label={user.name}></span>;
    }
    if (user.avatar_emoji) {
      return <span className="wbp-avatar" style={base} aria-label={user.name}>{user.avatar_emoji}</span>;
    }
    var style = user.avatar_color ? Object.assign({}, base, { background: user.avatar_color, color: "#fff", borderColor: "transparent" }) : base;
    return <span className="wbp-avatar" style={style} aria-label={user.name}>{wbpInitials(user)}</span>;
  }
  window.WorkbenchAvatar = WorkbenchAvatar;

  function Kpi(props) {
    return (
      <div className="wbp-kpi" title={props.title || undefined}>
        <b>{props.value}</b>
        <span>{props.label}</span>
      </div>
    );
  }
  function InsightCard(props) {
    return (
      <div className="wbp-insight-card">
        <div className="wbp-insight-icon">{props.icon}</div>
        <b className="wbp-insight-val">{props.value}</b>
        <span className="wbp-insight-label">{props.label}</span>
      </div>
    );
  }
  function ToolBar(props) {
    var label = wbpFeatureLabel(props.tool, props.lang);
    var pct = props.maxCount > 0 ? (props.count / props.maxCount * 100) : 0;
    return (
      <div className="wbp-toolbar-row">
        <span className="wbp-toolbar-name">{label}</span>
        <div className="wbp-toolbar-track"><div className="wbp-toolbar-fill" style={{ width: pct + "%" }}></div></div>
        <b className="wbp-toolbar-count">{props.count}{props.lang === "zh" ? " 次" : ""}</b>
      </div>
    );
  }

  function WorkbenchProfilePage() {
    var i18n = window.useWorkbenchI18n();
    var t = i18n.t, lang = i18n.lang;
    if (window.useDataVersion) window.useDataVersion();

    var user = (window.DATA && DATA.user) || {};
    var usage = (window.DATA && DATA.dashboard && DATA.dashboard.usage) || {};
    var taskTime = usage.task_time || {};
    var topTools = usage.top_tools || [];
    var heatmap = (window.DATA && DATA.dashboard && DATA.dashboard.activity_heatmap) || { days: [], rows: [] };
    var hmRows = heatmap.rows || [];
    var hmDays = heatmap.days || [];
    var hmMax = 1;
    hmRows.forEach(function (r) { (r.values || []).forEach(function (v) { if (v > hmMax) hmMax = v; }); });

    var editState = useState(false); var editing = editState[0]; var setEditing = editState[1];
    var nameState = useState(user.name || ""); var name = nameState[0]; var setName = nameState[1];
    var bioState = useState(user.bio || ""); var bio = bioState[0]; var setBio = bioState[1];
    var modeState = useState(user.avatar ? "image" : (user.avatar_emoji ? "emoji" : "letter")); var avatarMode = modeState[0]; var setAvatarMode = modeState[1];
    var dataState = useState(user.avatar || ""); var avatarData = dataState[0]; var setAvatarData = dataState[1];
    var emojiState = useState(user.avatar_emoji || ""); var emoji = emojiState[0]; var setEmoji = emojiState[1];
    var colorState = useState(user.avatar_color || WBP_COLORS[0]); var color = colorState[0]; var setColor = colorState[1];
    var savingState = useState(false); var saving = savingState[0]; var setSaving = savingState[1];
    var errState = useState(""); var err = errState[0]; var setErr = errState[1];
    var fileRef = useRef(null);

    function beginEdit() {
      setName(user.name || ""); setBio(user.bio || "");
      setAvatarMode(user.avatar ? "image" : (user.avatar_emoji ? "emoji" : "letter"));
      setAvatarData(user.avatar || ""); setEmoji(user.avatar_emoji || "");
      setColor(user.avatar_color || WBP_COLORS[0]); setErr(""); setEditing(true);
    }
    function onPickImage(file) {
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        var img = new Image();
        img.onload = function () {
          var max = 256, scale = Math.min(1, max / Math.max(img.width, img.height));
          var w = Math.round(img.width * scale), hh = Math.round(img.height * scale);
          var canvas = document.createElement("canvas");
          canvas.width = w; canvas.height = hh;
          canvas.getContext("2d").drawImage(img, 0, 0, w, hh);
          setAvatarData(canvas.toDataURL("image/jpeg", 0.85)); setAvatarMode("image");
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
    }
    function save() {
      setSaving(true); setErr("");
      var payload = { name: name.trim(), bio: bio.trim() };
      if (avatarMode === "image") { payload.avatar = avatarData || ""; payload.avatar_emoji = ""; }
      else if (avatarMode === "emoji") { payload.avatar = ""; payload.avatar_emoji = (emoji || "").trim(); }
      else { payload.avatar = ""; payload.avatar_emoji = ""; payload.avatar_color = color || ""; }
      fetch("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { throw new Error(e.error || "HTTP " + r.status); }); })
        .then(function (d) {
          if (d.user && window.DATA) { DATA.user = d.user; window.bumpData && window.bumpData(); }
          setSaving(false); setEditing(false);
        })
        .catch(function (e) { setSaving(false); setErr(String(e.message || e)); });
    }

    var previewUser = editing
      ? { name: name, avatar: avatarMode === "image" ? avatarData : "", avatar_emoji: avatarMode === "emoji" ? emoji : "", avatar_color: avatarMode === "letter" ? color : "" }
      : user;

    return (
      <div className="wbp-page">
        <div className="wbp-inner">
          <div className="wbp-hero">
            {!editing && (
              <button type="button" className="wbp-edit-fab" title={t("profile.edit")} onClick={beginEdit}>
                <svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l3 3-8 8H4v-3z" /></svg>
              </button>
            )}
            <div className="wbp-hero-avatar">
              <WorkbenchAvatar user={previewUser} size={84} />
              {editing && (
                <button type="button" className="wbp-cam" title={t("profile.avatarImage")} onClick={function () { setAvatarMode("image"); fileRef.current && fileRef.current.click(); }}>
                  <svg width="13" height="13" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 6h3l1.2-1.6h3.6L15 6h0v8H3z" /><circle cx="9" cy="10" r="2.4" /></svg>
                </button>
              )}
            </div>
            {!editing ? (
              <>
                <div className="wbp-name">{user.name}</div>
                <div className="wbp-handle">@{user.handle} · {(window.DATA && DATA.appVersion) || "—"}</div>
                {user.bio ? <div className="wbp-bio">{user.bio}</div> : null}
              </>
            ) : (
              <div className="wbp-edit">
                <input className="wbp-input" value={name} maxLength={60} placeholder={t("profile.namePlaceholder")} onChange={function (e) { setName(e.target.value); }} />
                <input className="wbp-input" value={bio} maxLength={120} placeholder={t("profile.bioPlaceholder")} onChange={function (e) { setBio(e.target.value); }} />
                <input ref={fileRef} type="file" accept="image/*" style={{ display: "none" }} onChange={function (e) { onPickImage(e.target.files && e.target.files[0]); e.target.value = ""; }} />
                <div className="wbp-seg">
                  <button type="button" className={avatarMode === "image" ? "active" : ""} onClick={function () { setAvatarMode("image"); if (!avatarData) fileRef.current && fileRef.current.click(); }}>{t("profile.avatarImage")}</button>
                  <button type="button" className={avatarMode === "emoji" ? "active" : ""} onClick={function () { setAvatarMode("emoji"); }}>{t("profile.avatarEmoji")}</button>
                  <button type="button" className={avatarMode === "letter" ? "active" : ""} onClick={function () { setAvatarMode("letter"); }}>{t("profile.avatarLetter")}</button>
                </div>
                {avatarMode === "image" && <button type="button" className="wbp-upload" onClick={function () { fileRef.current && fileRef.current.click(); }}>{t("profile.upload")}</button>}
                {avatarMode === "emoji" && (
                  <div className="wbp-picks">{WBP_EMOJI.map(function (em) { return <button type="button" key={em} className={"wbp-emoji" + (emoji === em ? " active" : "")} onClick={function () { setEmoji(em); }}>{em}</button>; })}</div>
                )}
                {avatarMode === "letter" && (
                  <div className="wbp-picks">{WBP_COLORS.map(function (c) { return <button type="button" key={c} className={"wbp-swatch" + (color === c ? " active" : "")} style={{ background: c }} onClick={function () { setColor(c); }} aria-label={c}></button>; })}</div>
                )}
                {err ? <div className="wbp-err">{err}</div> : null}
                <div className="wbp-edit-actions">
                  <button type="button" className="wbp-btn" onClick={function () { setEditing(false); }}>{t("profile.cancel")}</button>
                  <button type="button" className="wbp-btn primary" disabled={saving} onClick={save}>{saving ? "…" : t("profile.save")}</button>
                </div>
              </div>
            )}
          </div>

          {!editing && (
            <>
              <div className="wbp-kpis">
                <Kpi value={wbpSpend(usage, lang)} label={t("profile.spend")} />
                <Kpi value={usage.requests != null ? usage.requests : "—"} label={t("profile.requests")} />
                <Kpi value={usage.total_tokens ? wbpCompact(usage.total_tokens) : "—"} label={t("profile.tokens")} title={usage.tokens || ""} />
                <Kpi value={(usage.current_streak || 0) + (lang === "zh" ? " 天" : "d")} label={t("profile.streak")} />
                <Kpi value={(usage.longest_streak || 0) + (lang === "zh" ? " 天" : "d")} label={t("profile.longestStreak")} />
              </div>

              <div className="wbp-activity">
                <div className="wbp-activity-head">
                  <span className="wbp-block-title">{t("profile.activity")} <span className="wbp-hint">· {t("profile.activityRecent")}</span></span>
                  <span className="wbp-legend">{t("profile.legendLess")}<i className="lv1"></i><i className="lv2"></i><i className="lv3"></i><i className="lv4"></i>{t("profile.legendMore")}</span>
                </div>
                {hmRows.length ? (
                  <div className="wbp-heatmap">
                    {hmRows.map(function (r) {
                      return (
                        <div className="wbp-hm-row" key={r.label}>
                          <span className="wbp-hm-label">{r.label}</span>
                          <div className="wbp-hm-cells">
                            {(r.values || []).map(function (v, i) {
                              var lvl = v <= 0 ? 0 : (v >= hmMax * 0.66 ? 3 : (v >= hmMax * 0.33 ? 2 : 1));
                              return <span key={i} className={"wbp-hm-cell lv" + lvl} title={(hmDays[i] || "") + " · " + r.label + " · " + v}></span>;
                            })}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : <div className="wbp-empty">{t("profile.empty")}</div>}
              </div>

              <div className="wbp-lists">
                <div>
                  <div className="wbp-block-title">{t("profile.insights")}</div>
                  <div className="wbp-insights-grid">
                    <InsightCard
                      icon={<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>}
                      value={wbpPeakHour(usage.peak_hour, lang)}
                      label={t("profile.peakHour")}
                    />
                    <InsightCard
                      icon={<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>}
                      value={usage.active_days != null ? usage.active_days : "—"}
                      label={t("profile.activeDays")}
                    />
                    <InsightCard
                      icon={<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 5V3M9 3h6M12 9v4l2.5 2.5"/></svg>}
                      value={wbpDuration(taskTime.total_ms)}
                      label={t("profile.taskTotal")}
                    />
                    <InsightCard
                      icon={<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9z"/></svg>}
                      value={wbpDuration(taskTime.longest_ms)}
                      label={t("profile.taskLongest")}
                    />
                    <InsightCard
                      icon={<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>}
                      value={taskTime.runs != null ? taskTime.runs : "—"}
                      label={t("profile.taskRuns")}
                    />
                  </div>
                </div>
                <div className="wbp-list">
                  <div className="wbp-block-title">{t("profile.topTools")} <span className="wbp-hint">· {t("profile.topToolsHint")}</span></div>
                  {topTools.length
                    ? (function () {
                        var maxCount = Math.max.apply(null, topTools.map(function (it) { return it.count; }));
                        return topTools.map(function (it) { return <ToolBar key={it.tool} tool={it.tool} count={it.count} maxCount={maxCount} lang={lang} />; });
                      })()
                    : <div className="wbp-empty">{t("profile.empty")}</div>}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  window.WorkbenchProfilePage = WorkbenchProfilePage;
})();
