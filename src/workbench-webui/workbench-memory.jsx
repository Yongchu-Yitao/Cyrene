// Workbench Memory page.
//
// Fully independent from the legacy memory UI (`compiled/memory.js`, used by the
// old `--agent` shell). It has its own model, components and styles, and talks
// ONLY to the workspace-scoped `/api/workbench/memory/*` backend, passing the
// active project id as the `workspace` so every project/workspace owns a
// separate memory store. Cross-workspace memory is intentionally not surfaced.
// The embedded skill-learning panel is also implemented here instead of reusing
// the legacy Evolution UI; it calls the learning APIs directly.
(function () {
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useMemo = React.useMemo;
  var useRef = React.useRef;
  var h = React.createElement;

  function useMemoryT() {
    var i18n = window.useWorkbenchI18n
      ? window.useWorkbenchI18n()
      : { t: function (key, params, fallback) { return fallback || key; } };
    return function (key, fallback, params) {
      return i18n.t(key, params || null, fallback);
    };
  }

  // ── date helpers ─────────────────────────────────────────────────────
  function parseDate(s) {
    if (!s) return null;
    var d = new Date(String(s).length <= 10 ? String(s) + "T00:00:00" : s);
    return isNaN(d.getTime()) ? null : d;
  }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  // Relative label for list cards: 今天 / 昨天 / MM-DD / YYYY-MM-DD.
  function formatRel(s) {
    var d = parseDate(s);
    if (!d) return "—";
    var now = new Date();
    var startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var startThat = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var days = Math.round((startToday - startThat) / 86400000);
    if (days === 0) return "今天";
    if (days === 1) return "昨天";
    if (d.getFullYear() === now.getFullYear()) return pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }
  function formatFull(s) {
    var d = parseDate(s);
    if (!d) return "—";
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  // ── classification metadata (icon + tone per category) ───────────────
  function svg(props, children) {
    return h("svg", Object.assign({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" }, props), children);
  }
  var ICON = {
    all: function (s) { return svg({ width: s, height: s, fill: "currentColor", stroke: "none" }, h("path", { d: "M12 3.6 14 9.4 20 11l-6 1.6L12 18l-2-5.4L4 11l6-1.6Z" })); },
    learning: function (s) { return svg({ width: s, height: s, fill: "currentColor", stroke: "none" }, h("path", { d: "M13.6 2.5 4.7 13.1h6.1l-1.4 8.4 9.9-12.1h-6.2Z" })); },
    preference: function (s) { return svg({ width: s, height: s, fill: "currentColor", stroke: "none" }, h("path", { d: "M12 20s-7-4.3-7-9.3A3.7 3.7 0 0 1 12 7a3.7 3.7 0 0 1 7 3.7C19 15.7 12 20 12 20Z" })); },
    project: function (s) { return svg({ width: s, height: s }, h("path", { d: "M4 7.5A1.5 1.5 0 0 1 5.5 6h4l2 2.2H19a1.5 1.5 0 0 1 1.5 1.5V17a1.5 1.5 0 0 1-1.5 1.5H5.5A1.5 1.5 0 0 1 4 17Z" })); },
    habit: function (s) { return svg({ width: s, height: s }, h("circle", { cx: 12, cy: 12, r: 8 }), h("path", { d: "M12 7.5V12l3 2" })); },
    fact: function (s) { return svg({ width: s, height: s }, h("circle", { cx: 12, cy: 8.2, r: 3.4 }), h("path", { d: "M5.5 19a6.5 6.5 0 0 1 13 0" })); },
    conversation: function (s) { return svg({ width: s, height: s }, h("path", { d: "M20 11.4a6.9 6.9 0 0 1-9.6 6.4L5 19l1.1-4.1A6.9 6.9 0 1 1 20 11.4Z" })); },
  };
  var CATS = {
    preference: { label: "个人偏好", tone: "rose" },
    project: { label: "项目背景", tone: "green" },
    habit: { label: "工作习惯", tone: "blue" },
    fact: { label: "事实信息", tone: "amber" },
    conversation: { label: "对话习惯", tone: "violet" },
  };
  var CAT_ORDER = ["preference", "project", "habit", "fact", "conversation"];
  var SOURCE_TONE = { conversation: "violet", knowledge: "amber", manual: "green", agent: "blue", other: "slate" };
  var CONF_TONE = { high: "green", medium: "amber", low: "slate" };

  function catMeta(id) { return CATS[id] || { label: id, tone: "slate" }; }
  function catIcon(id, size) { return (ICON[id] || ICON.fact)(size || 18); }

  // ── API model (workspace-scoped) ─────────────────────────────────────
  function jsonOrThrow(r) {
    return r.json().catch(function () { return {}; }).then(function (p) {
      if (!r.ok) throw new Error(p.error || p.detail || ("HTTP " + r.status));
      return p;
    });
  }
  function api(ws) {
    var qs = "?workspace=" + encodeURIComponent(ws || "default");
    return {
      list: function () { return fetch("/api/workbench/memory" + qs).then(jsonOrThrow); },
      create: function (body) {
        return fetch("/api/workbench/memory" + qs, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(jsonOrThrow);
      },
      update: function (id, body) {
        return fetch("/api/workbench/memory/" + encodeURIComponent(id) + qs, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(jsonOrThrow);
      },
      remove: function (id) {
        return fetch("/api/workbench/memory/" + encodeURIComponent(id) + qs, { method: "DELETE" }).then(jsonOrThrow);
      },
    };
  }

  // ── small presentational pieces ──────────────────────────────────────
  function Chip(props) {
    return h("span", { className: "wb-mem-chip" + (props.tone ? " " + props.tone : "") }, props.children);
  }

  // Donut chart for 记忆来源 built from {label,count,pct} segments.
  function Donut(props) {
    var segs = (props.segments || []).filter(function (s) { return s.count > 0; });
    var total = segs.reduce(function (a, s) { return a + s.count; }, 0);
    var R = 30, C = 2 * Math.PI * R, off = 0;
    if (!total) {
      return h("svg", { className: "wb-mem-donut", viewBox: "0 0 80 80", width: 78, height: 78 },
        h("circle", { cx: 40, cy: 40, r: R, fill: "none", stroke: "var(--wb-line)", strokeWidth: 12 }));
    }
    var arcs = segs.map(function (s, i) {
      var len = (s.count / total) * C;
      var el = h("circle", {
        key: i, cx: 40, cy: 40, r: R, fill: "none",
        stroke: "var(--wb-mem-" + (SOURCE_TONE[s.id] || "slate") + ")", strokeWidth: 12,
        strokeDasharray: len + " " + (C - len), strokeDashoffset: -off,
        transform: "rotate(-90 40 40)",
      });
      off += len;
      return el;
    });
    return h("svg", { className: "wb-mem-donut", viewBox: "0 0 80 80", width: 78, height: 78 },
      arcs,
      h("text", { x: 40, y: 37, textAnchor: "middle", className: "wb-mem-donut-num" }, total),
      h("text", { x: 40, y: 50, textAnchor: "middle", className: "wb-mem-donut-cap" }, "条"));
  }

  // ── create / edit modal ──────────────────────────────────────────────
  function MemoryModal(props) {
    var init = props.draft || {};
    var contentState = useState(init.content || ""); var content = contentState[0]; var setContent = contentState[1];
    var catState = useState(init.category || "fact"); var category = catState[0]; var setCategory = catState[1];
    var srcState = useState(init.source || "manual"); var source = srcState[0]; var setSource = srcState[1];
    var confState = useState(init.confidence || ""); var confidence = confState[0]; var setConfidence = confState[1];
    var tagsState = useState((init.tags || []).join(", ")); var tags = tagsState[0]; var setTags = tagsState[1];
    var ref = useRef(null);
    useEffect(function () { if (ref.current) ref.current.focus(); }, []);

    function submit() {
      var body = {
        content: content.trim(),
        category: category,
        source: source,
        confidence: confidence,
        tags: tags.split(/[,，;；]/).map(function (t) { return t.trim(); }).filter(Boolean),
      };
      if (!body.content) { if (ref.current) ref.current.focus(); return; }
      props.onSubmit(body);
    }

    var sel = function (value, setter, options) {
      return h("div", { className: "wb-mem-seg" }, options.map(function (o) {
        return h("button", { key: o.id, type: "button", className: "wb-mem-seg-btn" + (value === o.id ? " on" : ""), onClick: function () { setter(o.id); } }, o.label);
      }));
    };

    return h("div", { className: "wb-mem-modal-scrim", onMouseDown: function (e) { if (e.target === e.currentTarget) props.onClose(); } },
      h("div", { className: "wb-mem-modal", role: "dialog" },
        h("div", { className: "wb-mem-modal-head" },
          h("b", null, props.mode === "edit" ? "编辑记忆" : "新建记忆"),
          h("button", { type: "button", className: "wb-mem-iconbtn", onClick: props.onClose, title: "关闭" },
            svg({ width: 17, height: 17 }, h("path", { d: "m6 6 12 12M18 6 6 18" })))),
        h("div", { className: "wb-mem-modal-body" },
          h("label", { className: "wb-mem-field-label" }, "记忆内容"),
          h("textarea", { ref: ref, className: "wb-mem-textarea", value: content, placeholder: "描述这条记忆的内容…", onChange: function (e) { setContent(e.target.value); }, rows: 4 }),
          h("label", { className: "wb-mem-field-label" }, "类型"),
          sel(category, setCategory, CAT_ORDER.map(function (c) { return { id: c, label: CATS[c].label }; })),
          h("label", { className: "wb-mem-field-label" }, "来源"),
          sel(source, setSource, [
            { id: "manual", label: "手动添加" }, { id: "conversation", label: "对话" },
            { id: "knowledge", label: "知识库" }, { id: "other", label: "其他" },
          ]),
          h("label", { className: "wb-mem-field-label" }, "置信度"),
          sel(confidence, setConfidence, [
            { id: "", label: "自动" }, { id: "high", label: "高" }, { id: "medium", label: "中" }, { id: "low", label: "低" },
          ]),
          h("label", { className: "wb-mem-field-label" }, "标签"),
          h("input", { className: "wb-mem-input", value: tags, placeholder: "用逗号分隔，如：表达偏好, 沟通方式", onChange: function (e) { setTags(e.target.value); } })),
        h("div", { className: "wb-mem-modal-foot" },
          h("button", { type: "button", className: "wb-btn ghost", onClick: props.onClose }, "取消"),
          h("button", { type: "button", className: "wb-btn primary", onClick: submit, disabled: props.busy }, props.busy ? "保存中…" : "保存"))));
  }

  // ── detail panel ─────────────────────────────────────────────────────
  function MetaRow(props) {
    return h("div", { className: "wb-mem-meta-row" },
      h("label", null, props.label),
      h("div", { className: "wb-mem-meta-val" }, props.children));
  }

  function DetailPanel(props) {
    var m = props.memory;
    var tabState = useState("detail"); var tab = tabState[0]; var setTab = tabState[1];
    useEffect(function () { setTab("detail"); }, [m ? m.id : ""]);
    if (!m) {
      return h("aside", { className: "wb-mem-detail empty" },
        h("div", { className: "wb-mem-detail-ph" },
          svg({ width: 34, height: 34, strokeWidth: 1.4 }, h("path", { d: "M12 3.6 14 9.4 20 11l-6 1.6L12 18l-2-5.4L4 11l6-1.6Z" })),
          h("p", null, "选择一条记忆查看详情")));
    }
    var meta = catMeta(m.category);
    var related = props.related || [];
    var tabs = [
      { id: "detail", label: "详情" },
      { id: "cite", label: "引用 (" + m.citation_count + ")" },
      { id: "related", label: "相关记忆 (" + related.length + ")" },
      { id: "history", label: "编辑历史" },
    ];

    var detailBody = h("div", { className: "wb-mem-detail-scroll" },
      h("div", { className: "wb-mem-detail-hero" },
        h("span", { className: "wb-mem-ico " + meta.tone }, catIcon(m.category, 18)),
        h("p", null, m.content),
        h("div", { className: "wb-mem-hero-actions" },
          h("button", { type: "button", className: "wb-mem-iconbtn", title: "编辑", onClick: function () { props.onEdit(m); } },
            svg({ width: 15, height: 15 }, h("path", { d: "M12 20h9" }), h("path", { d: "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" }))),
          h("button", { type: "button", className: "wb-mem-iconbtn", title: "删除", onClick: function () { props.onDelete(m); } },
            svg({ width: 15, height: 15 }, h("path", { d: "M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" }))))),
      h("div", { className: "wb-mem-meta" },
        MetaRow({ label: "类型", children: h(Chip, { tone: meta.tone }, m.category_label) }),
        MetaRow({ label: "标签", children: h("div", { className: "wb-mem-tagwrap" },
          (m.tags.length ? m.tags : []).map(function (t, i) { return h(Chip, { key: i }, t); }),
          h("button", { type: "button", className: "wb-mem-tag-add", title: "编辑标签", onClick: function () { props.onEdit(m); } }, "+")) }),
        MetaRow({ label: "来源", children: m.source_label }),
        m.stale && MetaRow({ label: "状态", children: h(Chip, { tone: "slate" }, "已过时 · 不注入") }),
        MetaRow({ label: "创建时间", children: formatFull(m.created_at) }),
        MetaRow({ label: "更新时间", children: formatFull(m.updated_at) }),
        MetaRow({ label: "置信度", children: h(Chip, { tone: CONF_TONE[m.confidence] }, m.confidence_label) }),
        MetaRow({ label: "引用次数", children: String(m.citation_count) })),
      h("div", { className: "wb-mem-section" },
        h("div", { className: "wb-mem-section-head" }, "记忆内容"),
        h("p", { className: "wb-mem-content-full" }, m.content)),
      related.length > 0 && h("div", { className: "wb-mem-section" },
        h("div", { className: "wb-mem-section-head" }, "相关记忆", h("button", { type: "button", className: "wb-mem-link", onClick: function () { setTab("related"); } }, "查看全部 (" + related.length + ")")),
        related.slice(0, 3).map(function (r) {
          return h("button", { key: r.id, type: "button", className: "wb-mem-related-row", onClick: function () { props.onSelect(r.id); } },
            h("span", { className: "wb-mem-ico sm " + catMeta(r.category).tone }, catIcon(r.category, 13)),
            h("span", { className: "wb-mem-related-text" }, r.content),
            h("time", null, formatRel(r.updated_at)));
        })));

    var citeBody = h("div", { className: "wb-mem-detail-scroll" },
      h("div", { className: "wb-mem-cite-summary" }, h("b", null, m.citation_count), h("span", null, "次被引用")),
      (m.citations || []).length > 0
        ? h("div", { className: "wb-mem-cite-list" }, m.citations.map(function (c, i) {
            return h("div", { className: "wb-mem-cite-row", key: i },
              h("div", { className: "wb-mem-cite-row-head" },
                h("span", { className: "wb-mem-chip " + (SOURCE_TONE[c.source] || "slate") }, c.source_label || c.source),
                h("time", null, formatFull(c.at))),
              c.snippet && h("p", { className: "wb-mem-cite-snippet" }, c.snippet));
          }))
        : h("div", { className: "wb-mem-empty-soft" },
            svg({ width: 26, height: 26, strokeWidth: 1.5 }, h("path", { d: "M8 10h8M8 14h5" }), h("path", { d: "M21 11.4a6.9 6.9 0 0 1-9.6 6.4L6 19l1.1-4.1A6.9 6.9 0 1 1 21 11.4Z" })),
            h("p", null, "这条记忆还没有被引用过。Agent 在对话中引用此记忆时会自动记录。")));

    var relatedBody = h("div", { className: "wb-mem-detail-scroll" },
      related.length === 0
        ? h("div", { className: "wb-mem-empty-soft" }, h("p", null, "暂无相关记忆"))
        : related.map(function (r) {
          return h("button", { key: r.id, type: "button", className: "wb-mem-related-row", onClick: function () { props.onSelect(r.id); } },
            h("span", { className: "wb-mem-ico sm " + catMeta(r.category).tone }, catIcon(r.category, 13)),
            h("span", { className: "wb-mem-related-text" }, r.content),
            h("time", null, formatRel(r.updated_at)));
        }));

    var historyEvents = (m.history || []).slice().reverse();
    var historyBody = h("div", { className: "wb-mem-detail-scroll" },
      historyEvents.length > 0
        ? h("div", { className: "wb-mem-history" }, historyEvents.map(function (ev, i) {
            return h("div", { className: "wb-mem-history-row", key: i },
              h("span", { className: "wb-mem-dot" + (i === 0 ? "" : " muted") }),
              h("div", null,
                h("b", null, ev.action_label || ev.action),
                ev.detail && h("p", { className: "wb-mem-history-detail" }, ev.detail),
                h("small", null, formatFull(ev.at))));
          }))
        : h("div", { className: "wb-mem-empty-soft" }, h("p", null, "暂无编辑历史")));

    return h("aside", { className: "wb-mem-detail" },
      h("div", { className: "wb-mem-detail-tabs" }, tabs.map(function (t) {
        return h("button", { key: t.id, type: "button", className: "wb-mem-detail-tab" + (tab === t.id ? " active" : ""), onClick: function () { setTab(t.id); } }, t.label);
      })),
      tab === "detail" ? detailBody : tab === "cite" ? citeBody : tab === "related" ? relatedBody : historyBody,
      h("div", { className: "wb-mem-detail-foot" },
        h("button", { type: "button", className: "wb-btn ghost", onClick: function () { props.onEdit(m); } }, "编辑记忆"),
        h("button", { type: "button", className: "wb-btn ghost", disabled: props.busy, title: m.stale ? "恢复后会重新注入 Agent" : "过时后不再注入 Agent，但保留记录", onClick: function () { props.onToggleStale(m); } }, m.stale ? "恢复使用" : "标记过时"),
        h("button", { type: "button", className: "wb-btn danger", onClick: function () { props.onDelete(m); } }, "删除记忆")));
  }

  function learningSnapshot(data) {
    var skills = (data && data.learned_skills) || [];
    var patterns = (data && data.patterns) || [];
    var chains = ((data && data.tool_chains) || []).filter(function (chain) {
      var summary = (chain && chain.summary) || {};
      return (summary.total_steps || ((chain && chain.chain) || []).length || 0) > 0;
    });
    return {
      skills: skills,
      patterns: patterns,
      chains: chains,
      activeSkills: skills.filter(function (s) { return s.status === "active"; }).length,
      shadowSkills: skills.filter(function (s) { return s.status === "shadow"; }).length,
      candidatePatterns: patterns.filter(function (p) { return p.status === "skill_candidate"; }).length,
      reviewedChains: chains.filter(function (c) { return c.review && c.review.decision; }).length,
      recentSkills: skills.slice(0, 6),
      recentPatterns: patterns.slice(0, 5),
    };
  }
  function shortDateTime(value) {
    if (!value) return "—";
    var d = parseDate(value);
    if (!d) return String(value);
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }
  function memRenderMarkdown(text) {
    var source = String(text == null ? "" : text);
    try {
      var raw = window.marked ? window.marked.parse(source) : source;
      return window.DOMPurify ? window.DOMPurify.sanitize(raw, { ADD_ATTR: ["data-line", "data-language"] }) : raw;
    } catch (e) {
      return source;
    }
  }

  function chainTitle(chain, t) {
    if (!chain) return t ? t("memory.learning.noRoundSelected", "No round selected") : "No round selected";
    return chain.round_title || chain.session_title || (chain.user_message ? String(chain.user_message).slice(0, 32) : "") || (t ? t("memory.learning.toolChain", "Tool chain") : "Tool chain");
  }
  function chainSubtitle(chain, t) {
    if (!chain) return "—";
    var summary = chain.summary || {};
    return t
      ? t("memory.learning.chainSubtitle", "{steps} steps · {browserSteps} browser user ops · {time}", { steps: summary.total_steps || 0, browserSteps: summary.browser_user_steps || 0, time: shortDateTime(chain.updated_at || chain.created_at) })
      : (summary.total_steps || 0) + " steps · " + (summary.browser_user_steps || 0) + " browser user ops · " + shortDateTime(chain.updated_at || chain.created_at);
  }
  function reviewText(review, t) {
    var decision = review && review.decision;
    if (decision === "learn") return t ? t("memory.learning.review.learn", "Learn") : "Learn";
    if (decision === "parameterize") return t ? t("memory.learning.review.parameterize", "Parameterize") : "Parameterize";
    if (decision === "skip") return t ? t("memory.learning.review.skip", "Skip") : "Skip";
    return t ? t("memory.learning.pending", "Pending") : "Pending";
  }
  function learningSessions(chains) {
    var byId = {};
    (chains || []).forEach(function (chain) {
      var id = chain.session_id || "unknown";
      if (!byId[id]) {
        byId[id] = {
          id: id,
          title: chain.session_title || chain.user_message || id,
          updated_at: chain.updated_at || chain.created_at || "",
          chains: [],
        };
      }
      byId[id].chains.push(chain);
      if (String(chain.updated_at || "").localeCompare(String(byId[id].updated_at || "")) > 0) {
        byId[id].updated_at = chain.updated_at;
      }
    });
    return Object.keys(byId).map(function (id) { return byId[id]; }).sort(function (a, b) {
      return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    });
  }
  function sessionLabel(session, t) {
    if (!session) return t("memory.learning.noSession", "No session");
    var title = String(session.title || session.id || "").trim();
    var clipped = title.length > 24 ? title.slice(0, 24) + "…" : title;
    return clipped + " · " + shortDateTime(session.updated_at);
  }
  function toolDisplayName(step) {
    return String((step && step.tool) || (step && step.subtype) || (step && step.type) || "tool");
  }
  function translatedToolName(tool, t) {
    var raw = String(tool || "tool");
    var translated = t ? t("toolName." + raw, raw) : raw;
    if (translated !== raw) return translated;
    if (raw.indexOf("browser.user.") === 0) {
      var kind = raw.slice("browser.user.".length);
      return t ? t("toolName.browser.user." + kind, t("toolName.browser.user", "Browser user operation")) : "Browser user operation";
    }
    return translated;
  }
  function compactValue(value) {
    if (value == null) return "";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    try { return JSON.stringify(value); } catch (e) { return String(value); }
  }
  function toolParamsEntries(step) {
    if (!step) return "";
    var args = step.args || {};
    var hasArgs = args && typeof args === "object" && Object.keys(args).length;
    if (hasArgs) {
      return Object.keys(args).slice(0, 8).map(function (key) {
        return { key: key, value: compactValue(args[key]) };
      });
    }
    var fallback = step.input_summary || "";
    return fallback ? [{ key: "input", value: fallback }] : [];
  }
  function toolParamsText(step) {
    return toolParamsEntries(step).map(function (item) { return item.key + ": " + item.value; }).join(" · ");
  }
  function stepDescription(step) {
    if (!step) return "";
    return step.output_summary || step.input_summary || [step.domain, step.type, step.subtype].filter(Boolean).join(" / ") || step.tool || "";
  }
  function latestBrowserStep(chain) {
    var steps = (chain && chain.chain) || [];
    for (var i = steps.length - 1; i >= 0; i--) {
      if (steps[i].source === "user_browser" || String(steps[i].tool || "").indexOf("browser.") === 0) return steps[i];
    }
    return steps[0] || null;
  }
  function hashToolName(tool) {
    var text = String(tool || "");
    var hash = 0;
    for (var i = 0; i < text.length; i++) hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
    return Math.abs(hash);
  }
  function toolIcon(step) {
    var tool = toolDisplayName(step);
    var common = { width: 18, height: 18, strokeWidth: 2 };
    if (tool.indexOf("screenshot") >= 0 || tool.indexOf("snapshot") >= 0) return svg(common, h("rect", { x: 4, y: 6, width: 16, height: 12, rx: 2 }), h("circle", { cx: 12, cy: 12, r: 3 }), h("path", { d: "M9 6l1-2h4l1 2" }));
    if (tool.indexOf("browser") >= 0) return svg(common, h("rect", { x: 3, y: 5, width: 18, height: 14, rx: 2 }), h("path", { d: "M3 9h18M7 7h.01M10 7h.01" }));
    if (tool.indexOf("read") >= 0 || tool.indexOf("file") >= 0) return svg(common, h("path", { d: "M6 3h8l4 4v14H6z" }), h("path", { d: "M14 3v5h5M8 13h8M8 17h6" }));
    if (tool.indexOf("edit") >= 0 || tool.indexOf("write") >= 0) return svg(common, h("path", { d: "M4 20h4L19 9l-4-4L4 16z" }), h("path", { d: "M13 7l4 4" }));
    if (tool.indexOf("shell") >= 0 || tool.indexOf("command") >= 0 || tool.indexOf("bash") >= 0) return svg(common, h("path", { d: "M4 7l5 5-5 5M11 17h9" }));
    if (tool.indexOf("search") >= 0 || tool.indexOf("find") >= 0) return svg(common, h("circle", { cx: 11, cy: 11, r: 6 }), h("path", { d: "m16 16 4 4" }));
    if (tool.indexOf("wait") >= 0) return svg(common, h("circle", { cx: 12, cy: 12, r: 8 }), h("path", { d: "M12 7v5l3 2" }));
    var hval = hashToolName(tool);
    var variant = hval % 4;
    if (variant === 0) return svg(common, h("path", { d: "M12 3 4 8l8 5 8-5z" }), h("path", { d: "m4 14 8 5 8-5" }));
    if (variant === 1) return svg(common, h("path", { d: "M5 12h14M12 5v14" }), h("circle", { cx: 12, cy: 12, r: 8 }));
    if (variant === 2) return svg(common, h("rect", { x: 5, y: 5, width: 14, height: 14, rx: 3 }), h("path", { d: "M9 9h6v6H9z" }));
    return svg(common, h("path", { d: "M12 4 20 12 12 20 4 12z" }), h("circle", { cx: 12, cy: 12, r: 2 }));
  }
  function detailScreenshots(chain) {
    var shots = (chain && chain.screenshots) || [];
    return shots.filter(function (shot) { return !!(shot && (shot.url || shot.path)); });
  }
  function detailScreenshot(chain) {
    var shots = detailScreenshots(chain);
    return shots.length ? shots[0] : null;
  }
  function detailFiles(chain) {
    return ((chain && chain.files) || []).slice(0, 12);
  }
  function mediaUrl(shot) {
    var url = String((shot && shot.url) || "").trim();
    if (url) return url;
    var path = String((shot && shot.path) || "").trim();
    return path ? "/api/tool-chain-media?path=" + encodeURIComponent(path) : "";
  }
  function skillUsageCount(skill) {
    var stats = (skill && skill.run_statistics) || {};
    if (skill && skill.actual_usage_count != null) return Number(skill.actual_usage_count || 0) || 0;
    if (stats.actual_runs != null) return Number(stats.actual_runs || 0) || 0;
    return Number(stats.active_success || 0) + Number(stats.active_failure || 0) || 0;
  }
  function learningErrorText(error, t) {
    var text = String(error || "");
    if (text === "No tool operations executed; cannot learn from empty chain.") {
      return t("memory.learning.emptyChainError", "No tool operations executed; cannot learn from empty chain.");
    }
    return text;
  }
  function skillStatusText(status, t) {
    var value = String(status || "draft");
    return t ? t("memory.learning.skillStatus." + value, value) : value;
  }
  function skillTypeText(type, t) {
    var value = String(type || "draft");
    return t ? t("memory.learning.skillType." + value, value) : value;
  }
  function reviewDecisionMeta(review) {
    var proposed = (review && review.proposed_skill) || {};
    return proposed && proposed._decision ? proposed._decision : {};
  }
  function chainMatchesSkill(chain, skill) {
    if (!chain || !skill) return false;
    var skillId = String(skill.id || "");
    var patternId = String(skill.pattern_id || "");
    var meta = reviewDecisionMeta(chain.review);
    if (skillId && String(meta.target_skill_id || "") === skillId) return true;
    if (patternId && String(meta.target_pattern_id || "") === patternId) return true;
    var similarSkills = Array.isArray(meta.similar_skills) ? meta.similar_skills : [];
    return similarSkills.some(function (item) {
      return (skillId && String(item.skill_id || item.id || "") === skillId)
        || (patternId && String(item.pattern_id || "") === patternId);
    });
  }
  function findSkillSourceChain(skill, chains) {
    if (!skill) return null;
    var list = chains || [];
    for (var i = 0; i < list.length; i++) {
      if (chainMatchesSkill(list[i], skill)) return list[i];
    }
    return null;
  }

  function SkillLearningPanel(props) {
    var t = useMemoryT();
    var shotState = useState(0); var shotIndex = shotState[0]; var setShotIndex = shotState[1];
    var imgErrorState = useState(false); var imgError = imgErrorState[0]; var setImgError = imgErrorState[1];
    var learning = props.learning;
    var loading = learning.loading;
    var detailKind = props.detailKind || "chain";
    var skill = props.skill;
    var chain = props.chain;
    var summary = (chain && chain.summary) || {};
    var review = (chain && chain.review) || {};
    var screenshots = detailScreenshots(chain);
    var boundedShotIndex = Math.min(shotIndex, Math.max(0, screenshots.length - 1));
    var screenshot = screenshots[boundedShotIndex] || detailScreenshot(chain);
    var files = detailFiles(chain);
    var similarity = summary.total_steps ? Math.round(((summary.success_steps || 0) / summary.total_steps) * 100) : 0;
    var hasChains = (learning.data && learning.data.tool_chains && learning.data.tool_chains.length > 0);
    useEffect(function () { setShotIndex(0); setImgError(false); }, [chain && chain.id]);

    function deleteLearnedSkill() {
      if (!skill || !skill.id) return;
      window.confirmModal({
        body: t("memory.learning.deleteSkillConfirm", "Delete skill \"{name}\"? This cannot be undone.", { name: skill.name || skill.id }),
        confirmLabel: t("memory.learning.deleteSkill", "Delete"),
        danger: true,
      }).then(function (ok) {
        if (ok && props.onDeleteSkill) props.onDeleteSkill(skill.id);
      });
    }
    if (detailKind === "skill") {
      var skillSteps = Array.isArray(skill && skill.steps) ? skill.steps : [];
      var trigger = (skill && skill.trigger) || {};
      var examples = Array.isArray(trigger.positive_examples) ? trigger.positive_examples : [];
      return h("aside", { className: "wb-mem-detail wb-mem-skill-panel wb-mem-replay-panel" },
        h("div", { className: "wb-mem-detail-tabs" },
          h("button", { type: "button", className: "wb-mem-detail-tab active" }, t("memory.learning.skillDetails", "Skill details"))),
        !skill ? h("div", { className: "wb-mem-detail-scroll" },
          h("div", { className: "wb-mem-empty-soft" }, t("memory.learning.noSkillSelected", "Select a learned skill to inspect.")),
          learning.error && h("div", { className: "wb-mem-error inline" }, learningErrorText(learning.error, t)))
          : h("div", { className: "wb-mem-detail-scroll" },
            h("div", { className: "wb-mem-skill-hero" },
              h("span", { className: "wb-mem-ico blue" }, ICON.learning(17)),
              h("div", null,
                h("div", { className: "wb-mem-skill-title-row" },
                  h("h3", null, skill.name || skill.id),
                  h(Chip, { tone: skill.status === "active" ? "green" : "blue" }, skillStatusText(skill.status, t))),
                h("p", null, skill.description || t("memory.learning.noSkillDescription", "No description yet.")))),
            h("div", { className: "wb-mem-skill-stats" },
              h("div", null, h("b", null, String(skillUsageCount(skill))), h("span", null, t("memory.learning.references", "References"))),
              h("div", null, h("b", null, String(skill.version || 1)), h("span", null, t("memory.learning.version", "Version")))),
            h("div", { className: "wb-mem-skill-detail-box" },
              h("div", null, h("span", null, t("memory.learning.skillType", "Type")), h("b", null, skillTypeText(skill.skill_type, t))),
              h("div", null, h("span", null, t("memory.learning.updatedAt", "Updated")), h("b", null, shortDateTime(skill.updated_at))),
              h("div", null, h("span", null, t("memory.learning.minMatchScore", "Min match")), h("b", null, String(Math.round((skill.min_match_score || 0) * 100) / 100))),
              h("div", null, h("span", null, t("memory.learning.stepCount", "Steps")), h("b", null, String(skillSteps.length)))),
            examples.length ? h("div", { className: "wb-replay-section" },
              h("h3", null, t("memory.learning.triggerExamples", "Trigger examples")),
              h("div", { className: "wb-mem-skill-examples" }, examples.slice(0, 4).map(function (example, index) {
                return h("p", { key: index }, example);
              }))) : null,
            h("div", { className: "wb-replay-section" },
              h("h3", null, t("memory.learning.skillSteps", "Skill steps")),
              skillSteps.length ? h("div", { className: "wb-mem-skill-step-list" }, skillSteps.map(function (step, index) {
                var ref = (step && step.implementation_reference) || {};
                var tool = ref.tool_name || step.tool || step.subtype || step.type || "";
                return h("div", { key: step.step_id || index, className: "wb-mem-skill-step" },
                  h("span", null, String(index + 1)),
                  h("div", null,
                    h("b", null, step.title || translatedToolName(tool, t)),
                    h("p", null, step.intent || step.raw_description || translatedToolName(tool, t))));
              })) : h("div", { className: "wb-mem-empty-soft compact" }, t("memory.learning.noSteps", "No steps"))),
            (function () {
              var chainsList = (learning.data && learning.data.tool_chains) || [];
              var srcChain = findSkillSourceChain(skill, chainsList);
              if (!srcChain) return null;
              var srcSummary = (srcChain && srcChain.summary) || {};
              var srcReview = (srcChain && srcChain.review) || {};
              var srcTotalSteps = srcSummary.total_steps || 0;
              var srcBrowserSteps = srcSummary.browser_user_steps || 0;
              var srcSuccessSteps = srcSummary.success_steps || 0;
              var srcSuccessRate = srcTotalSteps > 0 ? Math.round((srcSuccessSteps / srcTotalSteps) * 100) : 100;
              var matchScore = skill.min_match_score || 0;
              return h("div", { className: "wb-replay-section" },
                h("h3", null, t("memory.learning.behaviorAnalysis", "Behavior analysis")),
                h("div", { className: "wb-mem-skill-stats" },
                  h("div", null, h("b", null, String(srcTotalSteps)), h("span", null, t("memory.learning.totalSteps", "Total steps"))),
                  h("div", null, h("b", null, String(srcBrowserSteps)), h("span", null, t("memory.learning.browserOps", "User browser ops"))),
                  h("div", null, h("b", null, srcSuccessRate + "%"), h("span", null, t("memory.learning.successRate", "Success rate"))),
                  h("div", null, h("b", null, String(matchScore)), h("span", null, t("memory.learning.confidence", "Confidence")))),
                srcReview.decision ? h("div", { className: "wb-mem-skill-detail-box analysis" },
                  h("div", null, h("span", null, t("memory.learning.duplicateCheck", "Repeat behavior check")), h("b", null, srcReview.decision)),
                  h("div", { className: "wb-mem-skill-review-rationale" }, srcReview.rationale || "")) : null,
                srcReview.rationale && !srcReview.decision ? h("div", { className: "wb-mem-skill-detail-box analysis" },
                  h("span", null, t("memory.learning.learningSuggestion", "Learning suggestion")),
                  h("div", { className: "wb-mem-skill-review-rationale" }, srcReview.rationale)) : null);
            })(),
            (function () {
              var scriptStep = null;
              for (var i = 0; i < skillSteps.length; i++) {
                if (skillSteps[i].implementation_kind === "script") {
                  scriptStep = skillSteps[i];
                  break;
                }
              }
              if (!scriptStep) return null;
              var scriptRef = scriptStep.implementation_reference || {};
              var scriptPath = scriptRef.script_path || "";
              var scriptLang = scriptRef.language || "python";
              if (!scriptPath) return null;
              var scriptName = scriptPath.split("/").pop() || "run.py";
              return h("div", { className: "wb-replay-section" },
                h("h3", null, t("memory.learning.generatedScript", "Generated script")),
                h("div", { className: "wb-detail-files" },
                  h("div", { className: "wb-detail-file-row", style: { cursor: "pointer" }, title: scriptPath, onClick: function () { window.showToast({ body: t("memory.learning.scriptPathCopied", "Script path copied to clipboard"), duration: 2000 }); navigator.clipboard.writeText(scriptPath).catch(function(){}); } },
                    h("span", null, toolIcon({ tool: "file" })),
                    h("div", null,
                      h("b", null, scriptName),
                      h("small", null, scriptPath)))));
            })(),
        skill ? h("div", { className: "wb-mem-skill-delete-bottom" },
          h("button", { type: "button", className: "wb-btn danger", onClick: deleteLearnedSkill }, t("memory.learning.deleteSkill", "Delete")),
          learning.error && h("div", { className: "wb-mem-error inline" }, learningErrorText(learning.error, t)),
          learning.note && h("div", { className: "wb-mem-skill-note" }, learning.note)) : null);
    }

    return h("aside", { className: "wb-mem-detail wb-mem-skill-panel wb-mem-replay-panel" },
      h("div", { className: "wb-mem-detail-tabs" },
        h("button", { type: "button", className: "wb-mem-detail-tab active" }, t("memory.learning.detailsTitle", "Details"))),
      !chain ? h("div", { className: "wb-mem-detail-scroll" },
        h("div", { className: "wb-mem-empty-soft" }, loading ? t("memory.learning.loadingChains", "Loading tool chains...") : t("memory.learning.selectRound", "Select a round to inspect.")),
        learning.error && h("div", { className: "wb-mem-error inline" }, learningErrorText(learning.error, t)))
        : h("div", { className: "wb-mem-detail-scroll" },
        screenshot ? h("figure", { className: "wb-detail-shot" },
          !imgError && mediaUrl(screenshot)
            ? h("img", {
              src: mediaUrl(screenshot),
              alt: screenshot.name || t("memory.learning.screenshotAlt", "Browser screenshot"),
              onError: function () { setImgError(true); },
              onLoad: function () { setImgError(false); },
            })
            : h("div", { className: "wb-detail-shot-error" },
              h("b", null, t("memory.learning.imageLoadFailed", "Image failed to load")),
              h("small", null, screenshot.path || screenshot.name || "—")),
          h("figcaption", null,
            h("span", null, screenshot.name || screenshot.path),
            screenshots.length > 1 && h("span", { className: "wb-detail-shot-pager" },
              h("button", { type: "button", disabled: boundedShotIndex <= 0, onClick: function () { setShotIndex(Math.max(0, boundedShotIndex - 1)); setImgError(false); } }, t("common.previous", "Previous")),
              h("em", null, String(boundedShotIndex + 1) + " / " + screenshots.length),
              h("button", { type: "button", disabled: boundedShotIndex >= screenshots.length - 1, onClick: function () { setShotIndex(Math.min(screenshots.length - 1, boundedShotIndex + 1)); setImgError(false); } }, t("common.next", "Next")))))
          : files.length ? h("div", { className: "wb-detail-files" },
            h("h3", null, t("memory.learning.relatedFiles", "Related files")),
            files.map(function (file, index) {
              return h("div", { key: file.path || index, className: "wb-detail-file-row" },
                h("span", null, toolIcon({ tool: file.tool || "file" })),
                h("div", null,
                  h("b", null, file.name || file.path),
                  h("small", null, file.path || "—")));
            }))
            : h("div", { className: "wb-detail-empty-media" }, t("memory.learning.noMedia", "No screenshot or file artifacts were captured for this round.")),
        h("div", { className: "wb-replay-section" },
          h("h3", null, t("memory.learning.behaviorAnalysis", "Behavior analysis")),
          h("div", { className: "wb-replay-metrics" },
            h("div", null, h("span", null, t("memory.learning.totalSteps", "Steps")), h("b", null, String(summary.total_steps || 0))),
            h("div", null, h("span", null, t("memory.learning.userOps", "User ops")), h("b", null, String(summary.browser_user_steps || 0))),
            h("div", null, h("span", null, t("memory.learning.successRate", "Success")), h("b", null, similarity + "%")),
            h("div", null, h("span", null, t("memory.learning.confidence", "Confidence")), h("b", null, String(Math.round((review.confidence || 0) * 100) / 100))))),
        h("div", { className: "wb-replay-duplicates" },
          h("b", null, t("memory.learning.duplicateCheck", "Repeat behavior check")),
          h("p", null, review.decision ? reviewText(review, t) + " · " + (review.rationale || t("memory.learning.reviewed", "The project-local learning agent reviewed this chain.")) : t("memory.learning.pendingReview", "Waiting for the project-local learning agent."))),
        h("div", { className: "wb-replay-section" },
          h("h3", null, t("memory.learning.learningSuggestion", "Learning suggestion")),
          h("p", null, review.rationale || t("memory.learning.suggestionFallback", "The project-local learning agent decides whether this chain should become a reusable skill."))),
        learning.error && h("div", { className: "wb-mem-error inline" }, learningErrorText(learning.error, t)),
        learning.note && h("div", { className: "wb-mem-skill-note" }, learning.note)),
      hasChains ? h("div", { className: "wb-mem-skill-delete-bottom" },
        h("button", { type: "button", className: "wb-learning-primary", disabled: !!learning.busy, onClick: function () { var id = chain && (chain.turn_id || chain.id); learning.runAction("learn", id); } },
          learning.busy ? t("memory.learning.learning", "Learning...") : t("memory.learning.learnAsSkill", "Learn as Skill ✨"))) : null);
  }

  function SkillLearningMain(props) {
    var t = useMemoryT();
    var learning = props.learning;
    var snap = learningSnapshot(learning.data);
    var loading = learning.loading;
    var busy = learning.busy;
    var sessions = learningSessions(snap.chains);
    var learnedSkills = snap.skills.filter(function (s) { return s.status === "active" || s.status === "shadow"; });
    var shownSkills = learnedSkills.slice(0, 8);
    var activeSessionId = props.sessionId || (sessions[0] && sessions[0].id) || "";
    var activeSession = sessions.find(function (session) { return session.id === activeSessionId; }) || sessions[0] || null;
    var chains = activeSession ? activeSession.chains.slice(0, 40) : [];
    var activeChain = props.chain && chains.some(function (chain) { return chain.id === props.chain.id; }) ? props.chain : chains[0] || null;
    var activeSteps = (activeChain && activeChain.chain) || [];
    var activeSummary = (activeChain && activeChain.summary) || {};
    var activeReview = (activeChain && activeChain.review) || {};
    var activeSkill = props.skill || null;
    var activeSkillSteps = Array.isArray(activeSkill && activeSkill.steps) ? activeSkill.steps : [];
    var activeSkillTrigger = (activeSkill && activeSkill.trigger) || {};
    var activeSkillExamples = Array.isArray(activeSkillTrigger.positive_examples) ? activeSkillTrigger.positive_examples : [];
    var activeSkillSourceChain = findSkillSourceChain(activeSkill, snap.chains);
    var onSelectChain = props.onSelectChain;
    var onSelectSession = props.onSelectSession;
    var onSelectSkill = props.onSelectSkill;
    var detailKind = props.detailKind || "chain";
    var selectedSkillId = props.skillId || "";

    return h("div", { className: "wb-mem-main wb-mem-learning-main" },
      h("div", { className: "wb-learning-layout" },
        h("aside", { className: "wb-learning-session-list" },
          h("section", { className: "wb-learning-side-section skills" },
            h("div", { className: "wb-learning-side-head" },
              h("b", null, t("memory.learning.autoLearnedSkills", "Auto-learned skills")),
              h("span", null, t("memory.learning.skillCount", "{count} skills", { count: learnedSkills.length }))),
            h("div", { className: "wb-learning-skill-list" },
              loading && !learning.data
                ? h("div", { className: "wb-mem-empty-soft compact" }, t("memory.learning.loadingChains", "Loading tool chains..."))
                : shownSkills.length ? shownSkills.map(function (skill) {
                  var usage = skillUsageCount(skill);
                  return h("button", { key: skill.id, type: "button", className: "wb-learning-skill-mini" + (detailKind === "skill" && selectedSkillId === skill.id ? " active" : ""), onClick: function () { onSelectSkill(skill.id); } },
                    h("div", null,
                      h("b", null, skill.name || skill.id),
                      h("small", null, skill.description || skill.skill_type || "—")),
                    h("span", null, t("memory.learning.skillUsedCount", "{count} refs", { count: usage })));
                }) : h("div", { className: "wb-mem-empty-soft compact" }, t("memory.learning.noAutoSkills", "No auto-learned skills yet.")))),
          h("section", { className: "wb-learning-side-section sessions" },
            h("div", { className: "wb-learning-list-head" },
              h("b", null, t("memory.learning.sessionSelect", "Conversation session")),
              h("select", {
                value: activeSession ? activeSession.id : "",
                onChange: function (e) { onSelectSession(e.target.value); },
                disabled: !sessions.length,
              }, sessions.length ? sessions.map(function (session) {
                return h("option", { key: session.id, value: session.id }, sessionLabel(session, t));
              }) : h("option", { value: "" }, t("memory.learning.noSession", "No session")))),
            h("div", { className: "wb-learning-chain-list" },
              loading && !learning.data
                ? h("div", { className: "wb-mem-empty-soft" }, t("memory.learning.loadingChains", "Loading tool chains..."))
                : chains.length ? chains.map(function (chain, index) {
                  var summary = chain.summary || {};
                  return h("button", { key: chain.id, type: "button", className: "wb-learning-chain-card" + (detailKind === "chain" && activeChain && activeChain.id === chain.id ? " active" : ""), onClick: function () { onSelectChain(chain.id); } },
                    h("div", null,
                      h("b", null, shortDateTime(chain.updated_at || chain.created_at) + "  " + chainTitle(chain, t)),
                      h("small", null, t("memory.learning.roundMeta", "{steps} steps · {review}", { steps: summary.total_steps || 0, review: chain.review ? reviewText(chain.review, t) : t("memory.learning.pending", "Pending") }))),
                    h("span", null, String(index + 1)));
                }) : h("div", { className: "wb-mem-empty-soft" }, t("memory.learning.noToolChains", "No tool-call rounds in this session."))))),
          h("section", { className: "wb-learning-detail" },
            h("div", { className: "wb-learning-hero" },
              h("div", null,
                h("h2", null, detailKind === "skill" && activeSkill ? (activeSkill.name || activeSkill.id) : (activeChain ? shortDateTime(activeChain.updated_at || activeChain.created_at) + "  " + chainTitle(activeChain, t) : t("memory.learning.title", "Skill learning"))),
                h("p", null, detailKind === "skill" && activeSkill ? (activeSkill.description || t("memory.learning.noSkillDescription", "No description yet.")) : (activeChain ? chainSubtitle(activeChain, t) : t("memory.learning.emptyIntro", "Tool-call rounds are recorded separately for each project."))),
                detailKind === "skill" && activeSkill
                  ? h("p", { className: "wb-learning-theme" }, t("memory.learning.skillSource", "Source: {source}", { source: activeSkillSourceChain ? chainTitle(activeSkillSourceChain, t) : (activeSkill.pattern_id || activeSkill.id) }))
                  : activeChain && h("p", { className: "wb-learning-theme" }, t("memory.learning.topic", "Topic: {topic}", { topic: activeChain.user_message || activeChain.context_summary || t("memory.learning.autoTopic", "Reusable tool chain") })))),
            learning.error && h("div", { className: "wb-mem-error" }, learningErrorText(learning.error, t)),
            learning.note && h("div", { className: "wb-mem-skill-note main" }, learning.note),
            detailKind === "skill" && activeSkill ? h("div", { className: "wb-learning-content" },
              activeSkillExamples.length ? h("section", { className: "wb-learning-section" },
                h("h3", null, t("memory.learning.triggerExamples", "Trigger examples")),
                h("div", { className: "wb-mem-skill-examples" }, activeSkillExamples.slice(0, 5).map(function (example, index) {
                  return h("p", { key: index }, example);
                }))) : null,
              h("section", { className: "wb-learning-section" },
                h("h3", null, t("memory.learning.skillSteps", "Skill steps")),
                activeSkillSteps.length ? h("div", { className: "wb-learning-step-list" }, activeSkillSteps.map(function (step, index) {
                  var ref = (step && step.implementation_reference) || {};
                  var tool = ref.tool_name || step.tool || step.subtype || step.type || "";
                  return h("div", { key: step.step_id || index, className: "wb-learning-step" },
                    h("span", { className: "wb-learning-step-no" }, String(index + 1)),
                    h("span", { className: "wb-learning-step-icon" }, toolIcon({ tool: tool || step.title || "skill" })),
                    h("div", null,
                      h("b", null, step.title || translatedToolName(tool, t)),
                      h("p", null, step.intent || step.raw_description || translatedToolName(tool, t))),
                    h("i", { className: "ok" }, "✓"));
                })) : h("div", { className: "wb-mem-empty-soft" }, t("memory.learning.noSteps", "No steps"))),
              activeSkillSourceChain ? h("section", { className: "wb-learning-section" },
                h("h3", null, t("memory.learning.sourceRound", "Source round")),
                h("button", { type: "button", className: "wb-learning-source-card", onClick: function () { onSelectChain(activeSkillSourceChain.id); } },
                  h("b", null, shortDateTime(activeSkillSourceChain.updated_at || activeSkillSourceChain.created_at) + "  " + chainTitle(activeSkillSourceChain, t)),
                  h("small", null, chainSubtitle(activeSkillSourceChain, t)))) : null)
              : activeChain ? h("div", { className: "wb-learning-content" },
              h("div", { className: "wb-learning-review-pill " + ((activeReview && activeReview.decision) || "pending") },
                h("b", null, t("memory.learning.agentDecision", "Learning agent decision")),
                h("span", null, reviewText(activeReview, t)),
                h("p", null, (activeReview && activeReview.rationale) || t("memory.learning.decisionFallback", "The project-local learning agent will review the full chain and decide."))),
              h("section", { className: "wb-learning-section" },
                h("h3", null, t("memory.learning.stepTitle", "Tool-chain execution steps ({count} steps)", { count: activeSummary.total_steps || activeSteps.length || 0 })),
                activeSteps.length ? h("div", { className: "wb-learning-step-list" }, activeSteps.map(function (step, index) {
                  var rawToolName = toolDisplayName(step);
                  var params = toolParamsEntries(step);
                  return h("div", { key: step.id || index, className: "wb-learning-step" + (step.source === "user_browser" ? " user" : "") },
                    h("span", { className: "wb-learning-step-no" }, String(index + 1)),
                    h("span", { className: "wb-learning-step-icon" }, toolIcon(step)),
                    h("div", null,
                      h("b", null, translatedToolName(rawToolName, t)),
                      params.length
                        ? h("div", { className: "wb-learning-step-params" }, params.map(function (item) {
                          return h("span", { key: item.key },
                            h("em", null, item.key),
                            h("code", null, item.value));
                        }))
                        : h("p", null, toolParamsText(step) || stepDescription(step))),
                    h("i", { className: step.success ? "ok" : "fail" }, step.success ? "✓" : "!"));
                })) : h("div", { className: "wb-mem-empty-soft" }, t("memory.learning.noSteps", "No steps"))),
              h("section", { className: "wb-learning-section" },
                h("h3", null, t("memory.learning.agentAnswer", "Agent answer")),
                h("div", { className: "wb-learning-answer markdown", dangerouslySetInnerHTML: { __html: memRenderMarkdown(activeChain.agent_response || activeChain.context_summary || t("memory.learning.noAgentAnswer", "This round has no agent answer yet.")) } })))
              : h("div", { className: "wb-mem-empty-soft" }, t("memory.learning.noLearnableRounds", "No learnable rounds yet.")))));
  }

  // ── main page ────────────────────────────────────────────────────────
  function WorkbenchMemoryPage(props) {
    var project = props && props.project;
    var workspace = (project && (project.dataKey || project.id)) || "default";
    var t = useMemoryT();

    var payloadState = useState(null); var payload = payloadState[0]; var setPayload = payloadState[1];
    var loadState = useState(true); var loading = loadState[0]; var setLoading = loadState[1];
    var errState = useState(""); var error = errState[0]; var setError = errState[1];
    var queryState = useState(""); var query = queryState[0]; var setQuery = queryState[1];
    var catState = useState("all"); var activeCat = catState[0]; var setActiveCat = catState[1];
    var srcState = useState(""); var sourceFilter = srcState[0]; var setSourceFilter = srcState[1];
    var sortState = useState("updated"); var sortKey = sortState[0]; var setSortKey = sortState[1];
    var selState = useState(""); var selectedId = selState[0]; var setSelectedId = selState[1];
    var panelState = useState(""); var activePanel = panelState[0]; var setActivePanel = panelState[1];
    var menuState = useState(""); var menu = menuState[0]; var setMenu = menuState[1]; // "type" | "source" | "sort"
    var modalState = useState(null); var modal = modalState[0]; var setModal = modalState[1];
    var busyState = useState(false); var busy = busyState[0]; var setBusy = busyState[1];
    var learningDataState = useState(null); var learningData = learningDataState[0]; var setLearningData = learningDataState[1];
    var learningLoadState = useState(false); var learningLoading = learningLoadState[0]; var setLearningLoading = learningLoadState[1];
    var learningBusyState = useState(""); var learningBusy = learningBusyState[0]; var setLearningBusy = learningBusyState[1];
    var learningErrState = useState(""); var learningError = learningErrState[0]; var setLearningError = learningErrState[1];
    var learningNoteState = useState(""); var learningNote = learningNoteState[0]; var setLearningNote = learningNoteState[1];
    var learningKindState = useState("skill"); var selectedLearningKind = learningKindState[0]; var setSelectedLearningKind = learningKindState[1];
    var learningDetailKindState = useState("chain"); var selectedLearningDetailKind = learningDetailKindState[0]; var setSelectedLearningDetailKind = learningDetailKindState[1];
    var learningSelState = useState(""); var selectedLearningSkillId = learningSelState[0]; var setSelectedLearningSkillId = learningSelState[1];
    var learningPatternSelState = useState(""); var selectedLearningPatternId = learningPatternSelState[0]; var setSelectedLearningPatternId = learningPatternSelState[1];
    var learningChainSelState = useState(""); var selectedLearningChainId = learningChainSelState[0]; var setSelectedLearningChainId = learningChainSelState[1];
    var learningSessionSelState = useState(""); var selectedLearningSessionId = learningSessionSelState[0]; var setSelectedLearningSessionId = learningSessionSelState[1];

    var client = useMemo(function () { return api(workspace); }, [workspace]);

    function load() {
      setLoading(true); setError("");
      return client.list()
        .then(function (p) { setPayload(p); })
        .catch(function (e) { setError(e.message || String(e)); setPayload({ memories: [], categories: [], sources: [], overview: {} }); })
        .finally(function () { setLoading(false); });
    }
    function loadLearning() {
      setLearningLoading(true); setLearningError("");
      return fetch("/api/evolution?project=" + encodeURIComponent(workspace)).then(jsonOrThrow)
        .then(function (p) { setLearningData(p || {}); return p; })
        .catch(function (e) { setLearningError(e.message || String(e)); setLearningData({ learned_skills: [], patterns: [], tool_chains: [] }); })
        .finally(function () { setLearningLoading(false); });
    }
    function runLearningAction(kind, turnId) {
      var url = (kind === "rebuild" ? "/api/patterns/rebuild" : "/api/patterns/learn") + "?project=" + encodeURIComponent(workspace);
      if (kind === "learn" && turnId) url += "&turn_id=" + encodeURIComponent(turnId);
      setLearningBusy(kind); setLearningNote(""); setLearningError("");
      return fetch(url, { method: "POST" }).then(jsonOrThrow)
        .then(function (payload) {
          var stats = payload.stats || payload.result || {};
          setLearningData({
            learned_skills: payload.learned_skills || [],
            patterns: payload.patterns || [],
            tool_chains: payload.tool_chains || [],
            scripts: payload.scripts || [],
          });
          setLearningNote(t("memory.learning.processedNote", "Processed {turns} rounds, reviewed {reviews} chains, created {skills} skills.", {
            turns: stats.processed_turns || 0,
            reviews: stats.learning_reviews || 0,
            skills: stats.skills_created || 0,
          }));
        })
        .catch(function (e) { setLearningError(e.message || String(e)); })
        .finally(function () { setLearningBusy(""); });
    }
    function learnPatternAsSkill(patternId) {
      if (!patternId) return Promise.resolve();
      var busyKey = "pattern:" + patternId;
      setLearningBusy(busyKey); setLearningNote(""); setLearningError("");
      return fetch("/api/patterns/" + encodeURIComponent(patternId) + "/learn-skill?project=" + encodeURIComponent(workspace), { method: "POST" }).then(jsonOrThrow)
        .then(function (payload) {
          setLearningData({
            learned_skills: payload.learned_skills || [],
            patterns: payload.patterns || [],
            tool_chains: payload.tool_chains || [],
            scripts: payload.scripts || [],
          });
          if (payload.skill_id) {
            setSelectedLearningKind("skill");
            setSelectedLearningDetailKind("skill");
            setSelectedLearningSkillId(payload.skill_id);
          }
          setLearningNote(payload.created
            ? t("memory.learning.patternCreatedNote", "Created a skill from this behavior pattern.")
            : t("memory.learning.patternExistingNote", "This behavior pattern already has a skill; switched to that skill."));
        })
        .catch(function (e) { setLearningError(e.message || String(e)); })
        .finally(function () { setLearningBusy(""); });
    }
    function selectLearningSkill(id) {
      setSelectedLearningKind("skill");
      setSelectedLearningDetailKind("skill");
      setSelectedLearningSkillId(id || "");
      var snap = learningSnapshot(learningData);
      var skill = snap.skills.find(function (item) { return item.id === id; }) || null;
      var sourceChain = findSkillSourceChain(skill, snap.chains);
      if (sourceChain) {
        setSelectedLearningSessionId(sourceChain.session_id || "");
        setSelectedLearningChainId(sourceChain.id || "");
      }
    }
    function selectLearningPattern(id) {
      setSelectedLearningKind("pattern");
      setSelectedLearningDetailKind("skill");
      setSelectedLearningPatternId(id || "");
    }
    function handleDeleteLearnedSkill(skillId) {
      if (!skillId) return Promise.resolve();
      setLearningBusy("delete");
      return fetch("/api/learned-skills/" + encodeURIComponent(skillId) + "/delete", { method: "POST" }).then(function (r) { return r.json(); })
        .then(function (data) {
          if (data && data.ok) {
            loadLearning();
            setSelectedLearningDetailKind("chain");
            setSelectedLearningSkillId("");
          }
        })
        .catch(function () {})
        .finally(function () { setLearningBusy(""); });
    }
    useEffect(function () {
      setSelectedId("");
      setActivePanel("");
      setSelectedLearningKind("skill");
      setSelectedLearningDetailKind("chain");
      setSelectedLearningSkillId("");
      setSelectedLearningPatternId("");
      setSelectedLearningChainId("");
      setSelectedLearningSessionId("");
      setActiveCat("all");
      setSourceFilter("");
      load().then(function () {
        var pending = window.__workbenchPendingSelection;
        var pendingMemId = pending && pending.type === "memory" ? (pending.memId || pending.id) : "";
        if (pendingMemId) {
          setSelectedId(pendingMemId);
          setActivePanel("");
          window.__workbenchPendingSelection = null;
        }
      });
    }, [workspace]);

    // Listen for search-navigation events while already mounted.
    useEffect(function () {
      function onNavigate(event) {
        var detail = event && event.detail;
        if (!detail || detail.type !== "memory") return;
        var id = detail.memId || detail.id;
        if (id) { setSelectedId(id); setActivePanel(""); }
      }
      window.addEventListener("cyrene:workbench-navigate", onNavigate);
      return function () { window.removeEventListener("cyrene:workbench-navigate", onNavigate); };
    }, []);
    useEffect(function () {
      if (activePanel === "learning" && !learningData && !learningLoading) loadLearning();
    }, [activePanel]);
    useEffect(function () {
      if (activePanel !== "learning") return undefined;
      var stopped = false;
      function refresh() {
        if (stopped || learningBusy || learningLoading) return;
        loadLearning();
      }
      var timer = setInterval(refresh, 5000);
      function onFocus() { refresh(); }
      function onVisibility() { if (!document.hidden) refresh(); }
      function onChatCreated() { setTimeout(refresh, 800); }
      window.addEventListener("focus", onFocus);
      document.addEventListener("visibilitychange", onVisibility);
      window.addEventListener("cyrene:wbc-chat-created", onChatCreated);
      window.addEventListener("cyrene:wbc-refresh-chats", onChatCreated);
      return function () {
        stopped = true;
        clearInterval(timer);
        window.removeEventListener("focus", onFocus);
        document.removeEventListener("visibilitychange", onVisibility);
        window.removeEventListener("cyrene:wbc-chat-created", onChatCreated);
        window.removeEventListener("cyrene:wbc-refresh-chats", onChatCreated);
      };
    }, [activePanel, workspace, learningBusy, learningLoading]);

    var memories = (payload && payload.memories) || [];
    var categories = (payload && payload.categories) || [];
    var sources = (payload && payload.sources) || [];
    var overview = (payload && payload.overview) || {};
    var learningSnap = learningSnapshot(learningData);
    var selectedLearningSession = selectedLearningSessionId
      ? learningSessions(learningSnap.chains).find(function (s) { return s.id === selectedLearningSessionId; }) || learningSessions(learningSnap.chains)[0] || null
      : learningSessions(learningSnap.chains)[0] || null;
    var sessionChains = selectedLearningSession ? selectedLearningSession.chains : learningSnap.chains;
    var selectedLearningChain = selectedLearningChainId
      ? sessionChains.find(function (c) { return c.id === selectedLearningChainId; }) || sessionChains[0] || null
      : sessionChains[0] || null;
    var selectedLearningSkill = selectedLearningKind === "skill"
      ? learningSnap.skills.find(function (s) { return s.id === selectedLearningSkillId; }) || learningSnap.skills[0] || null
      : null;
    var selectedLearningPattern = selectedLearningKind === "pattern"
      ? learningSnap.patterns.find(function (p) { return p.id === selectedLearningPatternId; }) || learningSnap.patterns[0] || null
      : selectedLearningSkill
        ? learningSnap.patterns.find(function (p) { return p.id === selectedLearningSkill.pattern_id; }) || null
        : null;
    useEffect(function () {
      if (!learningData) return;
      var skills = (learningData.learned_skills || []);
      var patterns = (learningData.patterns || []);
      var chains = (learningData.tool_chains || []);
      var sessions = learningSessions(chains.filter(function (chain) {
        var summary = (chain && chain.summary) || {};
        return (summary.total_steps || ((chain && chain.chain) || []).length || 0) > 0;
      }));
      if (sessions.length && (!selectedLearningSessionId || !sessions.some(function (s) { return s.id === selectedLearningSessionId; }))) {
        setSelectedLearningSessionId(sessions[0].id);
      }
      var activeSession = sessions.find(function (s) { return s.id === (selectedLearningSessionId || (sessions[0] && sessions[0].id)); });
      var activeChains = activeSession ? activeSession.chains : [];
      if (activeChains.length && (!selectedLearningChainId || !activeChains.some(function (c) { return c.id === selectedLearningChainId; }))) {
        setSelectedLearningChainId(activeChains[0].id);
      }
      if (!chains.length && selectedLearningChainId) setSelectedLearningChainId("");
      if (!sessions.length && selectedLearningSessionId) setSelectedLearningSessionId("");
      if (!skills.length && selectedLearningDetailKind === "skill") setSelectedLearningDetailKind("chain");
      if (!skills.length) {
        if (selectedLearningSkillId) setSelectedLearningSkillId("");
        if (patterns.length) {
          if (selectedLearningKind !== "pattern") setSelectedLearningKind("pattern");
          if (!selectedLearningPatternId || !patterns.some(function (p) { return p.id === selectedLearningPatternId; })) {
            setSelectedLearningPatternId(patterns[0].id);
          }
        }
        return;
      }
      if (selectedLearningKind !== "pattern" && (!selectedLearningSkillId || !skills.some(function (s) { return s.id === selectedLearningSkillId; }))) {
        setSelectedLearningSkillId(skills[0].id);
      }
      if (selectedLearningKind === "pattern" && selectedLearningPatternId && !patterns.some(function (p) { return p.id === selectedLearningPatternId; })) {
        setSelectedLearningKind("skill");
        setSelectedLearningSkillId(skills[0].id);
      }
    }, [learningData, selectedLearningKind, selectedLearningDetailKind, selectedLearningSkillId, selectedLearningPatternId, selectedLearningChainId, selectedLearningSessionId]);

    var visible = useMemo(function () {
      var q = query.trim().toLowerCase();
      var list = memories.filter(function (m) {
        if (activeCat !== "all" && m.category !== activeCat) return false;
        if (sourceFilter && m.source !== sourceFilter) return false;
        if (!q) return true;
        return (m.content + " " + (m.tags || []).join(" ")).toLowerCase().indexOf(q) >= 0;
      });
      list.sort(function (a, b) {
        if (sortKey === "created") return String(b.created_at).localeCompare(String(a.created_at));
        if (sortKey === "citations") return (b.citation_count || 0) - (a.citation_count || 0);
        return String(b.updated_at).localeCompare(String(a.updated_at));
      });
      return list;
    }, [memories, query, activeCat, sourceFilter, sortKey]);

    var selected = selectedId ? memories.find(function (m) { return m.id === selectedId; }) || null : null;
    var related = useMemo(function () {
      if (!selected) return [];
      var selTags = (selected.tags || []).map(function (t) { return String(t).toLowerCase(); });
      var stopRe = /[\s,，。.;；、！？!?()\[\]{}「」""'']+/;
      var selWords = (selected.content || "").toLowerCase().split(stopRe).filter(function (w) { return w.length >= 2; });
      return memories
        .filter(function (m) { return m.id !== selected.id; })
        .map(function (m) {
          var score = 0;
          // Same category is a mild signal.
          if (m.category === selected.category) score += 1;
          // Shared tags are the strongest signal.
          var mTags = (m.tags || []).map(function (t) { return String(t).toLowerCase(); });
          for (var i = 0; i < mTags.length; i++) {
            if (selTags.indexOf(mTags[i]) >= 0) score += 3;
          }
          // Same source is a weak tiebreaker.
          if (m.source === selected.source) score += 0.5;
          // Content word overlap (capped so a long doc doesn't dominate).
          var mWords = (m.content || "").toLowerCase().split(stopRe).filter(function (w) { return w.length >= 2; });
          var shared = 0;
          for (var j = 0; j < mWords.length && shared < 5; j++) {
            if (selWords.indexOf(mWords[j]) >= 0) shared++;
          }
          score += shared;
          return { m: m, score: score };
        })
        .filter(function (r) { return r.score > 0; })
        .sort(function (a, b) { return b.score - a.score; })
        .slice(0, 8)
        .map(function (r) { return r.m; });
    }, [selected, memories]);

    function applyPayload(p) {
      setPayload(p);
      return p;
    }
    function handleCreate(body) {
      setBusy(true);
      client.create(body)
        .then(function (p) { applyPayload(p); setModal(null); setActivePanel(""); if (p && p.id) setSelectedId(p.id); })
        .catch(function (e) { setError(e.message || String(e)); })
        .finally(function () { setBusy(false); });
    }
    function handleEditSubmit(id, body) {
      setBusy(true);
      client.update(id, body)
        .then(function (p) { applyPayload(p); setModal(null); })
        .catch(function (e) { setError(e.message || String(e)); })
        .finally(function () { setBusy(false); });
    }
    function handleDelete(m) {
      window.confirmModal({ body: "确定删除这条记忆吗？此操作不可撤销。", confirmLabel: "删除", danger: true }).then(function (ok) {
        if (!ok) return;
        client.remove(m.id)
          .then(function (p) { applyPayload(p); if (selectedId === m.id) setSelectedId(""); })
          .catch(function (e) { setError(e.message || String(e)); });
      });
    }
    // Retire / revive a memory: stale entries stay listed but are no longer
    // injected into agent runs.
    function handleToggleStale(m) {
      setBusy(true);
      client.update(m.id, { stale: !m.stale })
        .then(applyPayload)
        .catch(function (e) { setError(e.message || String(e)); })
        .finally(function () { setBusy(false); });
    }

    if (!project) {
      return h("section", { className: "wb-mem-page" },
        h("div", { className: "wb-mem-empty" }, "请选择一个项目以查看其记忆。"));
    }

    var typeOptions = [{ id: "all", label: "全部类型" }].concat(CAT_ORDER.map(function (c) { return { id: c, label: CATS[c].label }; }));
    var sourceOptions = [{ id: "", label: "全部来源" }, { id: "conversation", label: "对话" }, { id: "knowledge", label: "知识库" }, { id: "manual", label: "手动添加" }, { id: "agent", label: "Agent 记录" }, { id: "other", label: "其他" }];
    var sortOptions = [{ id: "updated", label: "最新更新" }, { id: "created", label: "最近创建" }, { id: "citations", label: "引用最多" }];
    function curLabel(opts, val) { for (var i = 0; i < opts.length; i++) if (opts[i].id === val) return opts[i].label; return opts[0].label; }

    function dropdown(key, label, options, value, setter) {
      return h("div", { className: "wb-mem-tool-wrap" },
        h("button", { type: "button", className: "wb-mem-tool" + (value && value !== "all" ? " on" : ""), onClick: function () { setMenu(menu === key ? "" : key); } },
          h("span", null, label),
          svg({ width: 13, height: 13, strokeWidth: 2 }, h("path", { d: "m6 9 6 6 6-6" }))),
        menu === key && h("div", { className: "wb-mem-menu" }, options.map(function (o) {
          return h("button", { key: o.id, type: "button", className: value === o.id ? "sel" : "", onClick: function () { setter(o.id); setMenu(""); } }, o.label);
        })));
    }

    // ── category rail ──
    var rail = h("aside", { className: "wb-mem-rail" },
      h("div", { className: "wb-mem-rail-head" },
        h("b", null, "记忆"),
        h("button", { type: "button", className: "wb-mem-new-btn", onClick: function () { setModal({ mode: "create", draft: {} }); } },
          svg({ width: 13, height: 13, strokeWidth: 2.4 }, h("path", { d: "M12 5v14M5 12h14" })), h("span", null, "新建记忆"))),
      h("div", { className: "wb-mem-cats" },
        categories.map(function (c) {
          var meta = c.id === "all" ? { tone: "accent" } : catMeta(c.id);
          return h("button", { key: c.id, type: "button", className: "wb-mem-cat" + (!activePanel && activeCat === c.id ? " active" : ""), onClick: function () { setActivePanel(""); setActiveCat(c.id); } },
            h("span", { className: "wb-mem-cat-ico " + meta.tone }, c.id === "all" ? ICON.all(15) : catIcon(c.id, 15)),
            h("span", { className: "wb-mem-cat-label" }, c.label),
            h("span", { className: "wb-mem-cat-count" }, c.count));
        }),
        h("button", { type: "button", className: "wb-mem-cat" + (activePanel === "learning" ? " active" : ""), onClick: function () { setActivePanel("learning"); setSelectedId(""); } },
          h("span", { className: "wb-mem-cat-ico blue" }, ICON.learning(15)),
          h("span", { className: "wb-mem-cat-label" }, "技能学习"),
          h("span", { className: "wb-mem-cat-count" }, "›"))),
      h("div", { className: "wb-mem-card" },
        h("div", { className: "wb-mem-card-head" }, "记忆概览"),
        h("div", { className: "wb-mem-ov-row" }, h("span", null, "总记忆数"), h("b", null, overview.total || 0)),
        h("div", { className: "wb-mem-ov-row" }, h("span", null, "近期新增"), h("b", null, overview.recent_added || 0)),
        h("div", { className: "wb-mem-ov-row" }, h("span", null, "被引用次数"), h("b", null, overview.total_citations || 0)),
        h("div", { className: "wb-mem-ov-row" }, h("span", null, "最后更新"), h("b", null, formatRel(overview.last_updated)))),
      h("div", { className: "wb-mem-card" },
        h("div", { className: "wb-mem-card-head" }, "记忆来源"),
        h("div", { className: "wb-mem-source-body" },
          h(Donut, { segments: sources }),
          h("div", { className: "wb-mem-source-legend" }, sources.map(function (s) {
            return h("div", { key: s.id, className: "wb-mem-legend-row" },
              h("span", { className: "wb-mem-legend-dot " + (SOURCE_TONE[s.id] || "slate") }),
              h("span", { className: "wb-mem-legend-label" }, s.label),
              h("span", { className: "wb-mem-legend-pct" }, s.pct + "%"));
          })))));

    // ── memory card list ──
    function card(m) {
      var meta = catMeta(m.category);
      return h("button", { key: m.id, type: "button", className: "wb-mem-item" + (!activePanel && selectedId === m.id ? " active" : "") + (m.stale ? " stale" : ""), onClick: function () { setActivePanel(""); setSelectedId(m.id); } },
        h("span", { className: "wb-mem-ico " + meta.tone }, catIcon(m.category, 17)),
        h("div", { className: "wb-mem-item-body" },
          h("div", { className: "wb-mem-item-top" },
            h("p", { className: "wb-mem-item-text" }, m.content),
            h("time", null, formatRel(m.updated_at))),
          h("div", { className: "wb-mem-item-tags" },
            h(Chip, { tone: meta.tone }, m.category_label),
            (m.tags || []).slice(0, 2).map(function (t, i) { return h(Chip, { key: i }, t); }),
            h(Chip, { tone: "ghost" }, m.source_label))));
    }

    var learning = {
      data: learningData,
      loading: learningLoading,
      busy: learningBusy,
      error: learningError,
      note: learningNote,
      load: loadLearning,
      runAction: runLearningAction,
      learnPatternAsSkill: learnPatternAsSkill,
    };

    var main = activePanel === "learning" ? h(SkillLearningMain, {
      learning: learning,
      chain: selectedLearningChain,
      skill: selectedLearningSkill,
      sessionId: selectedLearningSession && selectedLearningSession.id,
      detailKind: selectedLearningDetailKind,
      skillId: selectedLearningSkill && selectedLearningSkill.id,
      onSelectSession: function (sessionId) {
        setSelectedLearningDetailKind("chain");
        setSelectedLearningSessionId(sessionId || "");
        var sessions = learningSessions(learningSnap.chains);
        var session = sessions.find(function (s) { return s.id === sessionId; });
        setSelectedLearningChainId(session && session.chains[0] ? session.chains[0].id : "");
      },
      onSelectChain: function (chainId) {
        setSelectedLearningDetailKind("chain");
        setSelectedLearningChainId(chainId || "");
      },
      onSelectSkill: selectLearningSkill,
    }) : h("div", { className: "wb-mem-main" },
      h("div", { className: "wb-mem-toolbar" },
        h("div", { className: "wb-mem-searchbox" },
          svg({ width: 15, height: 15, strokeWidth: 1.9 }, h("circle", { cx: 11, cy: 11, r: 7 }), h("path", { d: "m20 20-3.2-3.2" })),
          h("input", { type: "text", placeholder: "搜索记忆…", value: query, onChange: function (e) { setQuery(e.target.value); } })),
        h("div", { className: "wb-mem-tools" },
          dropdown("type", curLabel(typeOptions, activeCat), typeOptions, activeCat, function (value) { setActivePanel(""); setActiveCat(value); }),
          dropdown("source", curLabel(sourceOptions, sourceFilter), sourceOptions, sourceFilter, setSourceFilter),
          dropdown("sort", curLabel(sortOptions, sortKey), sortOptions, sortKey, setSortKey))),
      error && h("div", { className: "wb-mem-error" }, error),
      h("div", { className: "wb-mem-list-col" },
        h("div", { className: "wb-mem-scroll" },
          loading
            ? h("div", { className: "wb-mem-empty" }, "加载记忆中…")
            : visible.length === 0
              ? h("div", { className: "wb-mem-empty" },
                h("div", { className: "wb-mem-empty-icon" }, ICON.all(38)),
                h("p", null, query || activeCat !== "all" || sourceFilter ? "没有匹配的记忆。" : "还没有记忆内容。"),
                h("button", { type: "button", className: "wb-btn primary", onClick: function () { setModal({ mode: "create", draft: {} }); } }, "新建第一条记忆"))
              : h("div", { className: "wb-mem-list" }, visible.map(card))),
        h("div", { className: "wb-mem-count" }, "共 " + visible.length + " 条记忆")));

    return h("section", { className: "wb-mem-page" },
      rail,
      main,
      activePanel === "learning" ? h(SkillLearningPanel, { learning: learning, detailKind: selectedLearningDetailKind, chain: selectedLearningChain, skill: selectedLearningSkill, pattern: selectedLearningPattern, onDeleteSkill: handleDeleteLearnedSkill }) : h(DetailPanel, {
        memory: selected, related: related, busy: busy,
        onSelect: setSelectedId,
        onEdit: function (m) { setModal({ mode: "edit", id: m.id, draft: { content: m.content, category: m.category, source: m.source, confidence: m.confidence, tags: m.tags } }); },
        onDelete: handleDelete,
        onToggleStale: handleToggleStale,
      }),
      menu && h("div", { className: "wb-mem-scrim", onClick: function () { setMenu(""); } }),
      modal && h(MemoryModal, {
        mode: modal.mode, draft: modal.draft, busy: busy,
        onClose: function () { setModal(null); },
        onSubmit: function (body) { if (modal.mode === "edit") handleEditSubmit(modal.id, body); else handleCreate(body); },
      }));
  }

  window.WorkbenchMemoryPage = WorkbenchMemoryPage;
})();
