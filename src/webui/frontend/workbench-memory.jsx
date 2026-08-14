// Workbench Memory page.
//
// This page owns its model, components and styles, and talks to the
// workspace-scoped `/api/workbench/memory/*` backend, passing the
// active project id as the `workspace` so every project/workspace owns a
// separate memory store. Cross-workspace memory is intentionally not surfaced.
// The embedded skill-learning panel is also implemented here instead of reusing
// another page; it calls the learning APIs directly.
(function () {
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useMemo = React.useMemo;
  var useRef = React.useRef;
  var h = React.createElement;

  // Keep one snapshot per project instead of making project switches share a
  // single piece of component state. The page remains mounted by the Workbench
  // shell, so this cache makes a recently visited project paint synchronously
  // while always revalidating it in the background. The cache is a paint
  // optimization only; it must never suppress a freshness check.
  var memoryPageCache = {
    payloads: {},
    pending: {},
    learningPayloads: {},
    learningPending: {},
  };
  function fetchMemoryPayload(workspace, client) {
    if (memoryPageCache.pending[workspace]) return memoryPageCache.pending[workspace];
    var request = client.list().then(function (payload) {
      memoryPageCache.payloads[workspace] = { value: payload, updatedAt: Date.now() };
      return payload;
    });
    memoryPageCache.pending[workspace] = request;
    request.then(function () {
      if (memoryPageCache.pending[workspace] === request) delete memoryPageCache.pending[workspace];
    }, function () {
      if (memoryPageCache.pending[workspace] === request) delete memoryPageCache.pending[workspace];
    });
    return request;
  }

  function fetchLearningPayload(learningProject) {
    if (memoryPageCache.learningPending[learningProject]) return memoryPageCache.learningPending[learningProject];
    var request = fetch("/api/evolution?project=" + encodeURIComponent(learningProject) + "&compact=1").then(jsonOrThrow)
      .then(function (payload) {
        memoryPageCache.learningPayloads[learningProject] = { value: payload || {}, updatedAt: Date.now() };
        return payload;
      });
    memoryPageCache.learningPending[learningProject] = request;
    request.then(function () {
      if (memoryPageCache.learningPending[learningProject] === request) delete memoryPageCache.learningPending[learningProject];
    }, function () {
      if (memoryPageCache.learningPending[learningProject] === request) delete memoryPageCache.learningPending[learningProject];
    });
    return request;
  }

  function cacheLearningPayload(projectId, payload) {
    var value = payload || {};
    memoryPageCache.learningPayloads[projectId] = { value: value, updatedAt: Date.now() };
    return value;
  }

  function useMemoryT() {
    var i18n = window.CyreneUI.require("i18n").use();
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
  function formatRel(s, t) {
    var d = parseDate(s);
    if (!d) return "—";
    var now = new Date();
    var startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var startThat = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var days = Math.round((startToday - startThat) / 86400000);
    if (days === 0) return t ? t("memory.today", "Today") : "今天";
    if (days === 1) return t ? t("memory.yesterday", "Yesterday") : "昨天";
    if (d.getFullYear() === now.getFullYear()) return pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }
  function formatFull(s) {
    var d = parseDate(s);
    if (!d) return "—";
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  // ── classification metadata (icon + tone per category) ───────────────
  function svg(props) {
    var children = Array.prototype.slice.call(arguments, 1);
    var svgProps = Object.assign({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" }, props);
    return h.apply(null, ["svg", svgProps].concat(children));
  }
  var ICON = {
    all: function (s) { return svg({ width: s, height: s, fill: "currentColor", stroke: "none" }, h("path", { d: "M12 3.6 14 9.4 20 11l-6 1.6L12 18l-2-5.4L4 11l6-1.6Z" })); },
    learning: function (s) { return svg({ width: s, height: s, fill: "currentColor", stroke: "none" }, h("path", { d: "M13.6 2.5 4.7 13.1h6.1l-1.4 8.4 9.9-12.1h-6.2Z" })); },
    preference: function (s) { return svg({ width: s, height: s, fill: "currentColor", stroke: "none" }, h("path", { d: "M12 20s-7-4.3-7-9.3A3.7 3.7 0 0 1 12 7a3.7 3.7 0 0 1 7 3.7C19 15.7 12 20 12 20Z" })); },
    project: function (s) { return svg({ width: s, height: s }, h("path", { d: "M4 7.5A1.5 1.5 0 0 1 5.5 6h4l2 2.2H19a1.5 1.5 0 0 1 1.5 1.5V17a1.5 1.5 0 0 1-1.5 1.5H5.5A1.5 1.5 0 0 1 4 17Z" })); },
    habit: function (s) { return svg({ width: s, height: s }, h("circle", { cx: 12, cy: 12, r: 8 }), h("path", { d: "M12 7.5V12l3 2" })); },
    fact: function (s) { return svg({ width: s, height: s }, h("circle", { cx: 12, cy: 8.2, r: 3.4 }), h("path", { d: "M5.5 19a6.5 6.5 0 0 1 13 0" })); },
    conversation: function (s) { return svg({ width: s, height: s }, h("path", { d: "M20 11.4a6.9 6.9 0 0 1-9.6 6.4L5 19l1.1-4.1A6.9 6.9 0 1 1 20 11.4Z" })); },
    detail: function (s) { return svg({ width: s, height: s }, h("path", { d: "M6.5 3.5h7.8l3.7 3.7v13.3H6.5Z" }), h("path", { d: "M14 3.5v4h4M9.5 11.5h5M9.5 15.5h5" })); },
    history: function (s) { return svg({ width: s, height: s }, h("path", { d: "M4.5 9.5V4.8m0 4.7h4.7M5 9a8 8 0 1 1-.6 5.7" }), h("path", { d: "M12 7.5V12l3.1 1.8" })); },
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

  function catLabel(id, t) {
    var fallback = (CATS[id] && CATS[id].label) || id || "—";
    return t ? t("memory.category." + id, fallback) : fallback;
  }
  function catMeta(id, t) {
    var meta = CATS[id] || { tone: "slate" };
    return { label: catLabel(id, t), tone: meta.tone };
  }
  function sourceLabel(id, t) {
    var raw = String(id || "other");
    return t ? t("memory.source." + raw, raw) : raw;
  }
  function confidenceLabel(id, t) {
    var raw = String(id || "auto");
    return raw === "auto" ? (t ? t("memory.auto", "Automatic") : "自动") : (t ? t("memory.confidence." + raw, raw) : raw);
  }
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
    var segmentTotal = segs.reduce(function (a, s) { return a + s.count; }, 0);
    var displayTotal = typeof props.total === "number" ? props.total : segmentTotal;
    var R = 30, C = 2 * Math.PI * R, off = 0;
    if (!segmentTotal) {
      return h("svg", { className: "wb-mem-donut", viewBox: "0 0 80 80", width: 78, height: 78 },
        h("circle", { cx: 40, cy: 40, r: R, fill: "none", stroke: "var(--wb-line)", strokeWidth: 12 }),
        h("text", { x: 40, y: 37, textAnchor: "middle", className: "wb-mem-donut-num" }, displayTotal),
        h("text", { x: 40, y: 50, textAnchor: "middle", className: "wb-mem-donut-cap" }, props.t ? props.t("memory.itemsUnit", "items") : "条"));
    }
    var arcs = segs.map(function (s, i) {
      var len = (s.count / segmentTotal) * C;
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
      h("text", { x: 40, y: 37, textAnchor: "middle", className: "wb-mem-donut-num" }, displayTotal),
      h("text", { x: 40, y: 50, textAnchor: "middle", className: "wb-mem-donut-cap" }, props.t ? props.t("memory.itemsUnit", "items") : "条"));
  }

  // ── create / edit modal ──────────────────────────────────────────────
  function MemoryModal(props) {
    var hookT = useMemoryT();
    var t = props.t || hookT;
    var init = props.draft || {};
    var contentState = useState(init.content || ""); var content = contentState[0]; var setContent = contentState[1];
    var catState = useState(init.category || "fact"); var category = catState[0]; var setCategory = catState[1];
    var srcState = useState(init.source || "manual"); var source = srcState[0]; var setSource = srcState[1];
    var confState = useState(init.confidence || ""); var confidence = confState[0]; var setConfidence = confState[1];
    var tagsState = useState((init.tags || []).join(", ")); var tags = tagsState[0]; var setTags = tagsState[1];
    var ref = useRef(null);
    var titleId = "wb-memory-modal-title";
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

    var sel = function (value, setter, options, label) {
      return h("div", { className: "wb-mem-seg", role: "radiogroup", "aria-label": label, onKeyDown: function (event) {
        if (["ArrowLeft", "ArrowRight", "Home", "End"].indexOf(event.key) < 0) return;
        event.preventDefault();
        var current = options.findIndex(function (option) { return option.id === value; });
        var next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (current + (event.key === "ArrowLeft" ? -1 : 1) + options.length) % options.length;
        setter(options[next].id);
        var buttons = event.currentTarget.querySelectorAll("button");
        if (buttons[next]) buttons[next].focus();
      } }, options.map(function (o) {
        return h("button", { key: o.id, type: "button", role: "radio", "aria-checked": value === o.id, className: "wb-mem-seg-btn" + (value === o.id ? " on" : ""), onClick: function () { setter(o.id); } }, o.label);
      }));
    };

    return h("div", { className: "wb-mem-modal-scrim", onMouseDown: function (e) { if (e.target === e.currentTarget) props.onClose(); } },
      h("div", { className: "wb-mem-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": titleId, onKeyDown: function (event) { if (event.key === "Escape" && !props.busy) props.onClose(); } },
        h("div", { className: "wb-mem-modal-head" },
          h("b", { id: titleId }, props.mode === "edit" ? t("memory.edit", "Edit memory") : t("memory.new", "New memory")),
          h("button", { type: "button", className: "wb-mem-iconbtn", onClick: props.onClose, title: t("common.close", "Close"), "aria-label": t("common.close", "Close") },
            svg({ width: 17, height: 17 }, h("path", { d: "m6 6 12 12M18 6 6 18" })))),
        h("div", { className: "wb-mem-modal-body" },
          h("label", { className: "wb-mem-field-label", htmlFor: "wb-memory-content" }, t("memory.content", "Memory content")),
          h("textarea", { id: "wb-memory-content", ref: ref, className: "wb-mem-textarea", value: content, placeholder: t("memory.contentPlaceholder", "Describe this memory…"), onChange: function (e) { setContent(e.target.value); }, rows: 4 }),
          h("label", { className: "wb-mem-field-label" }, t("memory.type", "Type")),
          sel(category, setCategory, CAT_ORDER.map(function (c) { return { id: c, label: catLabel(c, t) }; }), t("memory.type", "Type")),
          h("label", { className: "wb-mem-field-label" }, t("memory.source", "Source")),
          sel(source, setSource, [
            { id: "manual", label: sourceLabel("manual", t) }, { id: "conversation", label: sourceLabel("conversation", t) },
            { id: "knowledge", label: sourceLabel("knowledge", t) }, { id: "other", label: sourceLabel("other", t) },
          ], t("memory.source", "Source")),
          h("label", { className: "wb-mem-field-label" }, t("memory.confidence", "Confidence")),
          sel(confidence, setConfidence, [
            { id: "", label: confidenceLabel("auto", t) }, { id: "high", label: confidenceLabel("high", t) }, { id: "medium", label: confidenceLabel("medium", t) }, { id: "low", label: confidenceLabel("low", t) },
          ], t("memory.confidence", "Confidence")),
          h("label", { className: "wb-mem-field-label", htmlFor: "wb-memory-tags" }, t("memory.tags", "Tags")),
          h("input", { id: "wb-memory-tags", className: "wb-mem-input", value: tags, placeholder: t("memory.tagsPlaceholder", "Comma-separated, e.g. preferences, communication"), onChange: function (e) { setTags(e.target.value); } })),
        h("div", { className: "wb-mem-modal-foot" },
          h("button", { type: "button", className: "wb-btn ghost", onClick: props.onClose }, t("common.cancel", "Cancel")),
          h("button", { type: "button", className: "wb-btn primary", onClick: submit, disabled: props.busy }, props.busy ? t("common.saving", "Saving…") : t("common.save", "Save")))));
  }

  // ── detail panel ─────────────────────────────────────────────────────
  function MetaRow(props) {
    return h("div", { className: "wb-mem-meta-row" },
      h("label", null, props.label),
      h("div", { className: "wb-mem-meta-val" }, props.children));
  }

  function detailTabIcon(id) {
    if (id === "cite") return ICON.conversation(17);
    if (id === "related") return svg({ width: 17, height: 17 }, h("path", { d: "M10 13a4.5 4.5 0 0 0 6.6.3l2.5-2.5a4.5 4.5 0 0 0-6.4-6.4l-1.4 1.4M14 11a4.5 4.5 0 0 0-6.6-.3l-2.5 2.5a4.5 4.5 0 0 0 6.4 6.4l1.4-1.4" }));
    if (id === "history") return ICON.history(17);
    return ICON.detail(17);
  }

  function DetailPanel(props) {
    var hookT = useMemoryT();
    var t = props.t || hookT;
    var m = props.memory;
    var tabState = useState("detail"); var tab = tabState[0]; var setTab = tabState[1];
    useEffect(function () { setTab("detail"); }, [m ? m.id : ""]);
    if (!m) {
      return h("aside", { className: "wb-floating-detail-shell wb-mem-detail", "aria-label": t("memory.details", "Details") },
        h("div", { className: "wb-floating-detail-card wb-mem-detail-card empty" },
          h("div", { className: "wb-detail-empty-state wb-mem-detail-ph" },
            svg({ width: 34, height: 34, strokeWidth: 1.4 }, h("path", { d: "M12 3.6 14 9.4 20 11l-6 1.6L12 18l-2-5.4L4 11l6-1.6Z" })),
            h("p", null, t("memory.selectToView", "Select a memory to view details")))));
    }
    var meta = catMeta(m.category, t);
    var related = props.related || [];
    var tabs = [
      { id: "detail", label: t("memory.details", "Details") },
      { id: "cite", label: t("memory.citationTab", "Citations ({count})", { count: m.citation_count }) },
      { id: "related", label: t("memory.relatedCompactTab", "Related ({count})", { count: related.length }) },
      { id: "history", label: t("memory.historyCompactTab", "History") },
    ];

    var detailBody = h("div", { className: "wb-mem-detail-scroll" },
      h("div", { className: "wb-mem-detail-hero" },
        h("span", { className: "wb-mem-ico " + meta.tone }, catIcon(m.category, 18)),
        h("p", null, m.content),
        h("div", { className: "wb-mem-hero-actions" },
          h("button", { type: "button", className: "wb-mem-iconbtn", title: t("common.edit", "Edit"), onClick: function () { props.onEdit(m); } },
            svg({ width: 15, height: 15 }, h("path", { d: "M12 20h9" }), h("path", { d: "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" }))))),
      h("div", { className: "wb-mem-meta" },
        MetaRow({ label: t("memory.type", "Type"), children: h(Chip, { tone: meta.tone }, catLabel(m.category, t)) }),
        MetaRow({ label: t("memory.tags", "Tags"), children: h("div", { className: "wb-mem-tagwrap" },
          (m.tags.length ? m.tags : []).map(function (t, i) { return h(Chip, { key: i }, t); }),
          h("button", { type: "button", className: "wb-mem-tag-add", title: t("memory.editTags", "Edit tags"), onClick: function () { props.onEdit(m); } }, "+")) }),
        MetaRow({ label: t("memory.source", "Source"), children: sourceLabel(m.source, t) }),
        m.stale && MetaRow({ label: t("memory.status", "Status"), children: h(Chip, { tone: "slate" }, t("memory.stale", "Outdated · not injected")) }),
        MetaRow({ label: t("memory.createdAt", "Created"), children: formatFull(m.created_at) }),
        MetaRow({ label: t("memory.updatedAt", "Updated"), children: formatFull(m.updated_at) }),
        MetaRow({ label: t("memory.confidence", "Confidence"), children: h(Chip, { tone: CONF_TONE[m.confidence] }, confidenceLabel(m.confidence, t)) }),
        MetaRow({ label: t("memory.citationCount", "Citation count"), children: String(m.citation_count) })),
      h("div", { className: "wb-mem-section" },
        h("div", { className: "wb-mem-section-head" }, t("memory.content", "Memory content")),
        h("p", { className: "wb-mem-content-full" }, m.content)),
      related.length > 0 && h("div", { className: "wb-mem-section" },
        h("div", { className: "wb-mem-section-head" }, t("memory.related", "Related memories"), h("button", { type: "button", className: "wb-mem-link", onClick: function () { setTab("related"); } }, t("memory.relatedAll", "View all ({count})", { count: related.length }))),
        related.slice(0, 3).map(function (r) {
          return h("button", { key: r.id, type: "button", className: "wb-mem-related-row", onClick: function () { props.onSelect(r.id); } },
            h("span", { className: "wb-mem-ico sm " + catMeta(r.category, t).tone }, catIcon(r.category, 13)),
            h("span", { className: "wb-mem-related-text" }, r.content),
            h("time", null, formatRel(r.updated_at, t)));
        })));

    var citeBody = h("div", { className: "wb-mem-detail-scroll" },
      h("div", { className: "wb-mem-cite-summary" }, h("b", null, m.citation_count), h("span", null, t("memory.timesCited", "times cited"))),
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
            h("p", null, t("memory.noCitations", "This memory has not been cited yet. Citations are recorded automatically when the Agent uses it in a conversation."))));

    var relatedBody = h("div", { className: "wb-mem-detail-scroll" },
      related.length === 0
        ? h("div", { className: "wb-mem-empty-soft" }, h("p", null, t("memory.noRelated", "No related memories")))
        : related.map(function (r) {
          return h("button", { key: r.id, type: "button", className: "wb-mem-related-row", onClick: function () { props.onSelect(r.id); } },
            h("span", { className: "wb-mem-ico sm " + catMeta(r.category, t).tone }, catIcon(r.category, 13)),
            h("span", { className: "wb-mem-related-text" }, r.content),
            h("time", null, formatRel(r.updated_at, t)));
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
        : h("div", { className: "wb-mem-empty-soft" }, h("p", null, t("memory.noHistory", "No edit history"))));
    var bodies = { detail: detailBody, cite: citeBody, related: relatedBody, history: historyBody };

    return h("aside", { className: "wb-floating-detail-shell wb-mem-detail", "aria-label": t("memory.details", "Details") },
      h("div", { className: "wb-floating-detail-card wb-mem-detail-card" },
        h("nav", { className: "wb-detail-accordion wb-mem-detail-tabs", "aria-label": t("memory.details", "Details") },
          h("div", { className: "wb-detail-accordion-head wb-mem-detail-nav-head" },
            h("span", null, t("memory.detailPanel", "Memory details")),
            h("button", { type: "button", className: "wb-detail-card-delete", title: t("common.delete", "Delete"), "aria-label": t("memory.delete", "Delete memory"), onClick: function () { props.onDelete(m); } },
              svg({ width: 15, height: 15 }, h("path", { d: "M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" })))),
          h("div", { className: "wb-detail-accordion-list" }, tabs.map(function (item) {
            return h(React.Fragment, { key: item.id },
              h("button", { type: "button", className: "wb-detail-accordion-trigger wb-mem-detail-tab" + (tab === item.id ? " active" : ""), "aria-expanded": tab === item.id, onClick: function () { setTab(tab === item.id ? "" : item.id); } },
                h("span", { className: "wb-detail-accordion-icon wb-mem-detail-tab-icon" }, detailTabIcon(item.id)),
                h("span", null, item.label),
                svg({ width: 14, height: 14 }, h("path", { d: "m9 18 6-6-6-6" }))),
              h("div", { className: "wb-detail-accordion-panel wb-mem-detail-tab-panel" + (tab === item.id ? " open" : ""), "aria-hidden": tab !== item.id },
                h("div", { className: "wb-detail-accordion-panel-inner" }, bodies[item.id])));
          }))),
        h("div", { className: "wb-mem-detail-foot" },
          h("button", { type: "button", className: "wb-btn ghost", onClick: function () { props.onEdit(m); } }, t("memory.edit", "Edit memory")),
          h("button", { type: "button", className: "wb-btn ghost", disabled: props.busy, title: m.stale ? t("memory.restoreTitle", "Restore it to inject into the Agent again") : t("memory.staleTitle", "Outdated memories are no longer injected into the Agent, but remain in the record"), onClick: function () { props.onToggleStale(m); } }, m.stale ? t("memory.restore", "Restore") : t("memory.markStale", "Mark outdated")))));
  }

  function learningSnapshot(data) {
    var skills = (data && data.learned_skills) || [];
    var candidates = (data && data.skill_candidates) || [];
    var chains = ((data && data.tool_chains) || []).filter(function (chain) {
      var summary = (chain && chain.summary) || {};
      return (summary.total_steps || ((chain && chain.chain) || []).length || 0) > 0;
    });
    return {
      skills: skills,
      candidates: candidates,
      chains: chains,
      activeSkills: skills.filter(function (s) { return s.status === "active"; }).length,
      recentSkills: skills.slice(0, 6),
    };
  }
  function shortDateTime(value) {
    if (!value) return "—";
    var d = parseDate(value);
    if (!d) return String(value);
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }
  function memRenderMarkdown(text) {
    return window.CyreneUI.require("markdown").renderRich(text);
  }

  function chainTitle(chain, t) {
    if (!chain) return t ? t("memory.learning.noRoundSelected", "No round selected") : "No round selected";
    return chain.purpose || chain.round_title || chain.session_title || (chain.user_message ? String(chain.user_message).slice(0, 32) : "") || (t ? t("memory.learning.toolChain", "Tool chain") : "Tool chain");
  }
  function isInternalProactiveChain(chain) {
    var message = String((chain && chain.user_message) || "").trim();
    return !!(chain && chain.system_initiated)
      || String((chain && chain.round_title) || "") === "proactive check-in"
      || message === "Scheduled proactive check-in"
      || message.indexOf("This is a scheduler-initiated proactive check-in.") === 0;
  }
  function chainTopic(chain, t) {
    if (isInternalProactiveChain(chain)) return t("memory.learning.proactiveTopic", "Scheduled check-in");
    return chain.purpose || chain.user_message || chain.context_summary || t("memory.learning.autoTopic", "Reusable tool chain");
  }
  function chainSubtitle(chain, t) {
    if (!chain) return "—";
    var summary = chain.summary || {};
    return t
      ? t("memory.learning.chainSubtitle", "{steps} steps · {browserSteps} browser user ops · {time}", { steps: summary.total_steps || 0, browserSteps: summary.browser_user_steps || 0, time: shortDateTime(chain.updated_at || chain.created_at) })
      : (summary.total_steps || 0) + " steps · " + (summary.browser_user_steps || 0) + " browser user ops · " + shortDateTime(chain.updated_at || chain.created_at);
  }
  function candidateForTurn(candidates, turnId) {
    return (candidates || []).find(function (candidate) {
      return (candidate.turn_ids || []).indexOf(turnId) >= 0;
    }) || null;
  }
  function candidateStatusText(candidate, t) {
    if (!candidate) return t("memory.learning.noRepeatYet", "No repeat detected");
    if (candidate.status === "observing") return t("memory.learning.observedOnce", "Observed once");
    if (candidate.status === "awaiting_user") return t("memory.learning.awaitingDecision", "Seen twice · awaiting your decision");
    if (candidate.status === "waiting_third") return t("memory.learning.waitingThird", "Seen twice · will learn on the third");
    if (candidate.status === "auto_learned") return t("memory.learning.autoLearnedThird", "Learned automatically on the third occurrence");
    if (candidate.status === "accepted") return t("memory.learning.userLearned", "Learned after user approval");
    if (candidate.status === "dismissed") return t("memory.learning.ignoredWorkflow", "Ignored");
    return candidate.status || t("memory.learning.pending", "Pending");
  }
  function candidateNextStepText(candidate, t) {
    if (!candidate) return "";
    if (candidate.status === "observing") return t("memory.learning.observedNext", "If the same workflow appears again, you will be asked whether to save it.");
    if (candidate.status === "awaiting_user") return t("memory.learning.awaitingNext", "Choose Learn now, Learn on third, or Ignore from the candidate card.");
    if (candidate.status === "waiting_third") return t("memory.learning.waitingThirdNext", "The next matching run will be saved automatically as a parameterized skill.");
    if (candidate.status === "auto_learned") return t("memory.learning.autoLearnedNext", "A parameterized skill has been generated and is ready to use.");
    if (candidate.status === "accepted") return t("memory.learning.userLearnedNext", "This workflow has been saved as a parameterized skill.");
    if (candidate.status === "dismissed") return t("memory.learning.ignoredNext", "This workflow is excluded from automatic learning.");
    return candidate.description || "";
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
  function translatedToolParamName(key, t) {
    var raw = String(key || "");
    return t ? t("memory.learning.toolParam." + raw, raw) : raw;
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
  function SkillLearningPanel(props) {
    var t = useMemoryT();
    var shotState = useState(0); var shotIndex = shotState[0]; var setShotIndex = shotState[1];
    var imgErrorState = useState(false); var imgError = imgErrorState[0]; var setImgError = imgErrorState[1];
    var detailOpenState = useState(true); var detailOpen = detailOpenState[0]; var setDetailOpen = detailOpenState[1];
    var learning = props.learning;
    var loading = learning.loading;
    var detailKind = props.detailKind || "chain";
    var skill = props.skill;
    var chain = props.chain;
    var summary = (chain && chain.summary) || {};
    var chainCandidate = candidateForTurn((learning.data && learning.data.skill_candidates) || [], chain && chain.turn_id);
    var screenshots = detailScreenshots(chain);
    var boundedShotIndex = Math.min(shotIndex, Math.max(0, screenshots.length - 1));
    var screenshot = screenshots[boundedShotIndex] || detailScreenshot(chain);
    var files = detailFiles(chain);
    var similarity = summary.total_steps ? Math.round(((summary.success_steps || 0) / summary.total_steps) * 100) : 0;
    var hasChains = (learning.data && learning.data.tool_chains && learning.data.tool_chains.length > 0);
    useEffect(function () { setShotIndex(0); setImgError(false); }, [chain && chain.id]);

    function deleteLearnedSkill() {
      if (!skill || !skill.id) return;
      window.CyreneUI.require("feedback").confirmModal({
        body: t("memory.learning.deleteSkillConfirm", "Delete skill \"{name}\"? This cannot be undone.", { name: skill.name || skill.id }),
        confirmLabel: t("memory.learning.deleteSkill", "Delete"),
        danger: true,
      }).then(function (ok) {
        if (ok && props.onDeleteSkill) props.onDeleteSkill(skill.id);
      });
    }

    function learningPanelShell(label, body, footer) {
      return h("aside", { className: "wb-floating-detail-shell wb-mem-detail wb-mem-skill-panel wb-mem-replay-panel", "aria-label": t("memory.learning.detailPanel", "Skill learning details") },
        h("div", { className: "wb-floating-detail-card wb-mem-detail-card" },
          h("nav", { className: "wb-detail-accordion wb-mem-detail-tabs", "aria-label": t("memory.learning.detailPanel", "Skill learning details") },
            h("div", { className: "wb-detail-accordion-head wb-mem-detail-nav-head" }, t("memory.learning.detailPanel", "Skill learning details")),
            h("div", { className: "wb-detail-accordion-list" },
              h("button", { type: "button", className: "wb-detail-accordion-trigger wb-mem-detail-tab" + (detailOpen ? " active" : ""), "aria-expanded": detailOpen, onClick: function () { setDetailOpen(!detailOpen); } },
                h("span", { className: "wb-detail-accordion-icon wb-mem-detail-tab-icon" }, detailTabIcon("detail")),
                h("span", null, label),
                svg({ width: 14, height: 14 }, h("path", { d: "m9 18 6-6-6-6" }))),
              h("div", { className: "wb-detail-accordion-panel wb-mem-detail-tab-panel" + (detailOpen ? " open" : ""), "aria-hidden": !detailOpen },
                h("div", { className: "wb-detail-accordion-panel-inner" }, body)))),
          footer));
    }
    if (detailKind === "skill") {
      var skillSteps = Array.isArray(skill && skill.steps) ? skill.steps : [];
      var trigger = (skill && skill.trigger) || {};
      var examples = Array.isArray(trigger.positive_examples) ? trigger.positive_examples : [];
      return learningPanelShell(t("memory.learning.skillDetails", "Skill details"),
        !skill ? h("div", { className: "wb-mem-detail-scroll" },
          h("div", { className: "wb-mem-empty-soft" }, t("memory.learning.noSkillSelected", "Select a learned skill to inspect.")),
          learning.error && h("div", { className: "wb-mem-error inline" }, learningErrorText(learning.error, t)))
          : h("div", { className: "wb-mem-detail-scroll" },
            h("header", { className: "wb-mem-skill-hero" },
              h("span", { className: "wb-mem-ico blue" }, ICON.learning(17)),
              h("div", null,
                h("div", { className: "wb-mem-skill-title-row" },
                  h("h2", null, skill.name || skill.id),
                  h(Chip, { tone: skill.status === "active" ? "green" : "blue" }, skillStatusText(skill.status, t))),
                h("p", null, skill.description || t("memory.learning.noSkillDescription", "No description yet.")))),
            h("div", { className: "wb-mem-skill-stats wb-mem-skill-overview", "aria-label": t("memory.learning.skillOverview", "Skill overview") },
              h("div", null, h("b", null, String(skillUsageCount(skill))), h("span", null, t("memory.learning.references", "References"))),
              h("div", null, h("b", null, String(skill.version || 1)), h("span", null, t("memory.learning.version", "Version")))),
            h("div", { className: "wb-mem-skill-detail-box" },
              h("div", null, h("span", null, t("memory.learning.skillType", "Type")), h("b", null, skillTypeText(skill.skill_type, t))),
              h("div", null, h("span", null, t("memory.learning.updatedAt", "Updated")), h("b", null, shortDateTime(skill.updated_at))),
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
              var declarativeScript = skill.script || {};
              if (declarativeScript.format) {
                return h("div", { className: "wb-replay-section" },
                  h("h3", null, t("memory.learning.parameterizedScript", "Parameterized tool script")),
                  h("pre", { className: "wb-learning-script-json" }, JSON.stringify(declarativeScript, null, 2)));
              }
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
                  h("button", { type: "button", className: "wb-detail-file-row wb-mem-script-file", title: scriptPath, "aria-label": t("memory.learning.copyScriptPath", "Copy script path") + ": " + scriptName, onClick: function () {
                    navigator.clipboard.writeText(scriptPath).then(function () {
                      window.CyreneUI.require("feedback").showToast(t("memory.learning.scriptPathCopied", "Script path copied to clipboard"), "info", { duration: 2000 });
                    }).catch(function () {});
                  } },
                    h("span", null, toolIcon({ tool: "file" })),
                    h("div", null,
                      h("b", null, scriptName),
                      h("small", null, scriptPath)),
                    h("em", null, t("memory.learning.copy", "Copy")))));
            })()),
        skill ? h("div", { className: "wb-mem-skill-delete-bottom" },
          h("button", { type: "button", className: "wb-btn danger", onClick: deleteLearnedSkill }, t("memory.learning.deleteSkill", "Delete")),
          learning.error && h("div", { className: "wb-mem-error inline" }, learningErrorText(learning.error, t)),
          learning.note && h("div", { className: "wb-mem-skill-note" }, learning.note)) : null);
    }

    return learningPanelShell(t("memory.learning.detailsTitle", "Details"),
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
            h("div", null, h("span", null, t("memory.learning.successRate", "Success")), h("b", null, similarity + "%")))),
        h("div", { className: "wb-replay-duplicates" },
          h("b", null, t("memory.learning.duplicateCheck", "Repeat behavior check")),
          h("p", null, candidateStatusText(chainCandidate, t))),
        chainCandidate ? h("div", { className: "wb-replay-section" },
          h("h3", null, t("memory.learning.nextStep", "Next step")),
          h("p", null, candidateNextStepText(chainCandidate, t))) : null,
        learning.error && h("div", { className: "wb-mem-error inline" }, learningErrorText(learning.error, t)),
        learning.note && h("div", { className: "wb-mem-skill-note" }, learning.note)),
      hasChains ? h("div", { className: "wb-mem-skill-delete-bottom" },
        h("button", { type: "button", className: "wb-learning-primary", disabled: !!learning.busy, onClick: function () { var id = chain && (chain.turn_id || chain.id); learning.runAction("learn", id); } },
          learning.busy ? t("memory.learning.learning", "Learning...") : t("memory.learning.learnAsSkill", "Save as skill"))) : null);
  }

  function SkillLearningMain(props) {
    var t = useMemoryT();
    var learning = props.learning;
    var snap = learningSnapshot(learning.data);
    var loading = learning.loading;
    var busy = learning.busy;
    var sessions = learningSessions(snap.chains);
    var learnedSkills = snap.skills.filter(function (s) { return s.status === "active"; });
    var pendingCandidates = snap.candidates.filter(function (candidate) { return candidate.status === "awaiting_user"; });
    var shownSkills = learnedSkills.slice(0, 8);
    var activeSessionId = props.sessionId || (sessions[0] && sessions[0].id) || "";
    var activeSession = sessions.find(function (session) { return session.id === activeSessionId; }) || sessions[0] || null;
    var chains = activeSession ? activeSession.chains.slice(0, 40) : [];
    var activeChain = props.chain && chains.some(function (chain) { return chain.id === props.chain.id; }) ? props.chain : chains[0] || null;
    var activeSteps = (activeChain && activeChain.chain) || [];
    var activeSummary = (activeChain && activeChain.summary) || {};
    var activeCandidate = candidateForTurn(snap.candidates, activeChain && activeChain.turn_id);
    var activeSkill = props.skill || null;
    var activeSkillSteps = Array.isArray(activeSkill && activeSkill.steps) ? activeSkill.steps : [];
    var activeSkillTrigger = (activeSkill && activeSkill.trigger) || {};
    var activeSkillExamples = Array.isArray(activeSkillTrigger.positive_examples) ? activeSkillTrigger.positive_examples : [];
    var onSelectChain = props.onSelectChain;
    var onSelectSession = props.onSelectSession;
    var onSelectSkill = props.onSelectSkill;
    var detailKind = props.detailKind || "chain";
    var selectedSkillId = props.skillId || "";

    return h("div", { className: "wb-mem-main wb-mem-learning-main" },
      h("div", { className: "wb-learning-layout" },
        h("aside", { className: "wb-learning-session-list" },
          h("div", { className: "wb-learning-nav-head" },
            h("button", { type: "button", onClick: props.onExit, title: t("memory.learning.backToMemory", "Back to memory"), "aria-label": t("memory.learning.backToMemory", "Back to memory") },
              svg({ width: 17, height: 17, strokeWidth: 2 }, h("path", { d: "m15 18-6-6 6-6" }))),
            h("b", null, t("memory.learning.title", "Skill learning"))),
          pendingCandidates.length ? h("section", { className: "wb-learning-side-section candidates" },
            h("div", { className: "wb-learning-side-head" },
              h("b", null, t("memory.learning.pendingCandidates", "Ready for your decision")),
              h("span", null, String(pendingCandidates.length))),
            h("div", { className: "wb-learning-candidate-list" }, pendingCandidates.map(function (candidate) {
              var parameterCount = (((candidate.script || {}).parameters) || []).length;
              return h("div", { key: candidate.id, className: "wb-learning-candidate-card" },
                h("div", { className: "wb-learning-candidate-copy" },
                  h("b", null, candidate.name || candidate.purpose || t("memory.learning.repeatedWorkflow", "Repeated workflow")),
                  h("p", null, candidate.description || t("memory.learning.secondOccurrence", "Seen twice. Learn now, wait for the third occurrence, or ignore it.")),
                  h("small", null, t("memory.learning.candidateMeta", "{count} occurrences · {params} parameters", { count: candidate.occurrence_count || 2, params: parameterCount }))),
                h("div", { className: "wb-learning-candidate-actions" },
                  h("button", { type: "button", disabled: !!busy, onClick: function () { learning.decideCandidate(candidate.id, "learn_now"); } }, t("memory.learning.learnNow", "Learn now")),
                  h("button", { type: "button", disabled: !!busy, onClick: function () { learning.decideCandidate(candidate.id, "defer"); } }, t("memory.learning.deferUntilThird", "Decide on third")),
                  h("button", { type: "button", disabled: !!busy, onClick: function () { learning.decideCandidate(candidate.id, "dismiss"); } }, t("memory.learning.dismissCandidate", "Ignore"))));
            }))) : null,
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
                      h("small", null, t("memory.learning.roundMeta", "{steps} steps · {review}", { steps: summary.total_steps || 0, review: candidateStatusText(candidateForTurn(snap.candidates, chain.turn_id), t) }))),
                    h("span", null, String(index + 1)));
                }) : h("div", { className: "wb-mem-empty-soft" }, t("memory.learning.noToolChains", "No tool-call rounds in this session."))))),
          h("section", { className: "wb-learning-detail" },
            h("div", { className: "wb-learning-hero" },
              h("div", null,
                h("h2", null, detailKind === "skill" && activeSkill ? (activeSkill.name || activeSkill.id) : (activeChain ? shortDateTime(activeChain.updated_at || activeChain.created_at) + "  " + chainTitle(activeChain, t) : t("memory.learning.title", "Skill learning"))),
                h("p", null, detailKind === "skill" && activeSkill ? (activeSkill.description || t("memory.learning.noSkillDescription", "No description yet.")) : (activeChain ? chainSubtitle(activeChain, t) : t("memory.learning.emptyIntro", "Tool-call rounds are recorded separately for each project."))),
                detailKind !== "skill" && activeChain
                  ? h("p", { className: "wb-learning-theme" }, t("memory.learning.topic", "Topic: {topic}", { topic: chainTopic(activeChain, t) }))
                  : null)),
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
                })) : h("div", { className: "wb-mem-empty-soft" }, t("memory.learning.noSteps", "No steps"))))
              : activeChain ? h("div", { className: "wb-learning-content" },
              activeCandidate ? h("div", { className: "wb-learning-review-pill " + activeCandidate.status },
                h("b", null, t("memory.learning.learningState", "Learning state")),
                h("span", null, candidateStatusText(activeCandidate, t)),
                h("p", null, candidateNextStepText(activeCandidate, t))) : null,
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
                            h("em", { title: item.key }, translatedToolParamName(item.key, t)),
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
    var active = !props || props.active !== false;
    var workspace = (project && (project.id || project.dataKey)) || "default";
    // Both memory and learning use the canonical Workbench project id. The
    // backend still accepts legacy dataKey values for older clients.
    var learningProject = (project && project.id) || workspace;
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
    var contextState = useState(null); var contextMenu = contextState[0]; var setContextMenu = contextState[1];
    var modalState = useState(null); var modal = modalState[0]; var setModal = modalState[1];
    var busyState = useState(false); var busy = busyState[0]; var setBusy = busyState[1];
    var learningDataState = useState(null); var learningData = learningDataState[0]; var setLearningData = learningDataState[1];
    var learningLoadState = useState(false); var learningLoading = learningLoadState[0]; var setLearningLoading = learningLoadState[1];
    var learningBusyState = useState(""); var learningBusy = learningBusyState[0]; var setLearningBusy = learningBusyState[1];
    var learningErrState = useState(""); var learningError = learningErrState[0]; var setLearningError = learningErrState[1];
    var learningNoteState = useState(""); var learningNote = learningNoteState[0]; var setLearningNote = learningNoteState[1];
    var learningDetailKindState = useState("chain"); var selectedLearningDetailKind = learningDetailKindState[0]; var setSelectedLearningDetailKind = learningDetailKindState[1];
    var learningSelState = useState(""); var selectedLearningSkillId = learningSelState[0]; var setSelectedLearningSkillId = learningSelState[1];
    var learningChainSelState = useState(""); var selectedLearningChainId = learningChainSelState[0]; var setSelectedLearningChainId = learningChainSelState[1];
    var learningSessionSelState = useState(""); var selectedLearningSessionId = learningSessionSelState[0]; var setSelectedLearningSessionId = learningSessionSelState[1];
    var workspaceRef = useRef(workspace); workspaceRef.current = workspace;
    var learningProjectRef = useRef(learningProject); learningProjectRef.current = learningProject;

    var client = useMemo(function () { return api(workspace); }, [workspace]);

    useEffect(function () {
      if (!contextMenu) return undefined;
      function close() { setContextMenu(null); }
      function onKeyDown(event) { if (event.key === "Escape") close(); }
      window.addEventListener("resize", close);
      window.addEventListener("scroll", close, true);
      document.addEventListener("keydown", onKeyDown);
      return function () {
        window.removeEventListener("resize", close);
        window.removeEventListener("scroll", close, true);
        document.removeEventListener("keydown", onKeyDown);
      };
    }, [!!contextMenu]);

    function load(options) {
      var opts = options || {};
      var requestedWorkspace = workspace;
      var cached = memoryPageCache.payloads[requestedWorkspace];
      if (!opts.background && workspaceRef.current === requestedWorkspace) setLoading(true);
      if (workspaceRef.current === requestedWorkspace) setError("");
      return fetchMemoryPayload(requestedWorkspace, client)
        .then(function (p) {
          if (workspaceRef.current === requestedWorkspace) setPayload(p);
          return p;
        })
        .catch(function (e) {
          if (workspaceRef.current === requestedWorkspace) {
            setError(e.message || String(e));
            if (!cached) setPayload({ memories: [], categories: [], sources: [], overview: {} });
          }
          return null;
        })
        .finally(function () { if (workspaceRef.current === requestedWorkspace) setLoading(false); });
    }
    function loadLearning() {
      var requestedProject = learningProject;
      var cached = memoryPageCache.learningPayloads[requestedProject];
      if (learningProjectRef.current === requestedProject) { setLearningLoading(!cached); setLearningError(""); }
      return fetchLearningPayload(requestedProject)
        .then(function (p) {
          if (learningProjectRef.current === requestedProject) setLearningData(p || {});
          return p;
        })
        .catch(function (e) {
          if (learningProjectRef.current === requestedProject) {
            setLearningError(e.message || String(e));
            if (!cached) setLearningData({ learned_skills: [], skill_candidates: [], tool_chains: [] });
          }
          return null;
        })
        .finally(function () { if (learningProjectRef.current === requestedProject) setLearningLoading(false); });
    }
    function runLearningAction(kind, turnId) {
      var url = (kind === "rebuild" ? "/api/learning/rebuild" : "/api/learning/process") + "?project=" + encodeURIComponent(learningProject);
      if (kind === "learn" && turnId) url += "&turn_id=" + encodeURIComponent(turnId);
      setLearningBusy(kind); setLearningNote(""); setLearningError("");
      return fetch(url, { method: "POST" }).then(jsonOrThrow)
        .then(function (payload) {
          var stats = payload.stats || payload.result || {};
          var nextLearning = {
            learned_skills: payload.learned_skills || [],
            skill_candidates: payload.skill_candidates || [],
            tool_chains: payload.tool_chains || [],
            scripts: payload.scripts || [],
          };
          cacheLearningPayload(learningProject, nextLearning);
          setLearningData(nextLearning);
          setLearningNote(t("memory.learning.processedNote", "Processed {turns} rounds, {candidates} candidates await a decision, created {skills} skills.", {
            turns: stats.processed_turns || 0,
            candidates: stats.candidates_awaiting_user || 0,
            skills: stats.skills_created || 0,
          }));
        })
        .catch(function (e) { setLearningError(e.message || String(e)); })
        .finally(function () { setLearningBusy(""); });
    }
    function decideSkillCandidate(candidateId, decision) {
      if (!candidateId) return Promise.resolve();
      setLearningBusy("candidate:" + candidateId); setLearningNote(""); setLearningError("");
      return fetch("/api/skill-candidates/" + encodeURIComponent(candidateId) + "/decision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: decision }),
      }).then(jsonOrThrow)
        .then(function (payload) {
          if (payload.skill_id) {
            setSelectedLearningDetailKind("skill");
            setSelectedLearningSkillId(payload.skill_id);
          }
          setLearningNote(decision === "learn_now"
            ? t("memory.learning.candidateLearned", "The parameterized skill is ready.")
            : decision === "defer"
              ? t("memory.learning.candidateDeferred", "It will be learned automatically if it happens a third time.")
              : t("memory.learning.candidateDismissed", "This workflow will not be learned automatically."));
          return loadLearning();
        })
        .catch(function (e) { setLearningError(e.message || String(e)); })
        .finally(function () { setLearningBusy(""); });
    }
    function selectLearningSkill(id) {
      setSelectedLearningDetailKind("skill");
      setSelectedLearningSkillId(id || "");
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
    function applyPendingMemorySelection() {
      var navigation = window.CyreneUI.require("navigation");
      var pending = navigation.getPending();
      var pendingMemId = pending && pending.type === "memory" ? (pending.memId || pending.id) : "";
      if (pendingMemId) {
        setSelectedId(pendingMemId);
        setActivePanel("");
        navigation.clearPending(pending);
      }
    }

    useEffect(function () {
      var cachedPayload = memoryPageCache.payloads[workspace];
      var cachedLearning = memoryPageCache.learningPayloads[learningProject];
      setPayload(cachedPayload ? cachedPayload.value : null);
      setLoading(!cachedPayload);
      setLearningData(cachedLearning ? cachedLearning.value : null);
      setLearningLoading(false);
      setSelectedId("");
      setActivePanel("");
      setSelectedLearningDetailKind("chain");
      setSelectedLearningSkillId("");
      setSelectedLearningChainId("");
      setSelectedLearningSessionId("");
      setActiveCat("all");
      setSourceFilter("");
    }, [workspace, learningProject]);

    useEffect(function () {
      if (!active) return;
      var cached = memoryPageCache.payloads[workspace];
      load({ background: !!cached }).then(applyPendingMemorySelection);
    }, [workspace, active]);

    // Cached data paints immediately, then focus/visibility and runtime events
    // revalidate it. Pending-request dedupe keeps event bursts to one request.
    useEffect(function () {
      if (!active) return undefined;
      var refreshTimer = null;
      function refreshSoon() {
        if (document.hidden) return;
        if (refreshTimer) clearTimeout(refreshTimer);
        refreshTimer = setTimeout(function () {
          refreshTimer = null;
          load({ background: true });
        }, 250);
      }
      function onVisibility() { if (!document.hidden) refreshSoon(); }
      function onRuntimeEvent(event) {
        if (!event) return;
        if (["tool_call", "assistant_message", "chat_message", "session_update", "goal_loop_update"].indexOf(event.type) >= 0) refreshSoon();
      }
      window.addEventListener("focus", refreshSoon);
      document.addEventListener("visibilitychange", onVisibility);
      var unsubscribe = window.CyreneUI.require("events").subscribe(onRuntimeEvent);
      return function () {
        if (refreshTimer) clearTimeout(refreshTimer);
        window.removeEventListener("focus", refreshSoon);
        document.removeEventListener("visibilitychange", onVisibility);
        unsubscribe();
      };
    }, [workspace, active]);

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
        if (stopped || document.hidden || learningBusy || learningLoading) return;
        loadLearning();
      }
      var timer = setInterval(refresh, 15000);
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
    }, [activePanel, workspace, learningProject, learningBusy, learningLoading]);

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
    var selectedLearningSkill = learningSnap.skills.find(function (s) { return s.id === selectedLearningSkillId; }) || learningSnap.skills[0] || null;
    useEffect(function () {
      if (!learningData) return;
      var skills = (learningData.learned_skills || []);
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
        return;
      }
      if (!selectedLearningSkillId || !skills.some(function (s) { return s.id === selectedLearningSkillId; })) {
        setSelectedLearningSkillId(skills[0].id);
      }
    }, [learningData, selectedLearningDetailKind, selectedLearningSkillId, selectedLearningChainId, selectedLearningSessionId]);

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
      memoryPageCache.payloads[workspace] = { value: p, updatedAt: Date.now() };
      if (workspaceRef.current === workspace) setPayload(p);
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
      window.CyreneUI.require("feedback").confirmModal({ body: t("memory.deleteConfirm", "Delete this memory? This cannot be undone."), confirmLabel: t("common.delete", "Delete"), danger: true }).then(function (ok) {
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
    function openEdit(m) {
      setModal({ mode: "edit", id: m.id, draft: { content: m.content, category: m.category, source: m.source, confidence: m.confidence, tags: m.tags } });
    }
    function openMemoryContextMenu(m, event) {
      event.preventDefault();
      event.stopPropagation();
      setMenu("");
      setContextMenu({
        memory: m,
        left: Math.max(8, Math.min(event.clientX, window.innerWidth - 220 - 8)),
        top: Math.max(8, Math.min(event.clientY, window.innerHeight - 140 - 8)),
      });
    }

    if (!project) {
      return h("section", { className: "wb-mem-page" },
        h("div", { className: "wb-mem-empty" }, t("memory.selectProject", "Select a project to view its memory.")));
    }

    var typeOptions = [{ id: "all", label: t("memory.allTypes", "All types") }].concat(CAT_ORDER.map(function (c) { return { id: c, label: catLabel(c, t) }; }));
    var sourceOptions = [{ id: "", label: t("memory.allSources", "All sources") }, { id: "conversation", label: sourceLabel("conversation", t) }, { id: "knowledge", label: sourceLabel("knowledge", t) }, { id: "manual", label: sourceLabel("manual", t) }, { id: "agent", label: sourceLabel("agent", t) }, { id: "other", label: sourceLabel("other", t) }];
    var sortOptions = [{ id: "updated", label: t("memory.sortUpdated", "Recently updated") }, { id: "created", label: t("memory.sortCreated", "Recently created") }, { id: "citations", label: t("memory.sortCitations", "Most cited") }];
    function curLabel(opts, val) { for (var i = 0; i < opts.length; i++) if (opts[i].id === val) return opts[i].label; return opts[0].label; }

    function dropdown(key, label, options, value, setter) {
      var menuId = "wb-memory-" + key + "-menu";
      return h("div", { className: "wb-mem-tool-wrap" },
        h("button", { type: "button", className: "wb-mem-tool" + (value && value !== "all" ? " on" : ""), "aria-haspopup": "menu", "aria-expanded": menu === key, "aria-controls": menuId, onClick: function () { setMenu(menu === key ? "" : key); } },
          h("span", null, label),
          svg({ width: 13, height: 13, strokeWidth: 2 }, h("path", { d: "m6 9 6 6 6-6" }))),
        menu === key && h("div", { id: menuId, className: "wb-mem-menu", role: "menu", onKeyDown: function (event) {
          if (event.key === "Escape") { setMenu(""); var trigger = event.currentTarget.previousElementSibling; if (trigger) trigger.focus(); return; }
          if (["ArrowUp", "ArrowDown", "Home", "End"].indexOf(event.key) < 0) return;
          event.preventDefault();
          var items = Array.prototype.slice.call(event.currentTarget.querySelectorAll('[role="menuitemradio"]'));
          var current = items.indexOf(document.activeElement);
          var next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (current + (event.key === "ArrowUp" ? -1 : 1) + items.length) % items.length;
          if (items[next]) items[next].focus();
        } }, options.map(function (o) {
          return h("button", { key: o.id, type: "button", role: "menuitemradio", "aria-checked": value === o.id, className: value === o.id ? "sel" : "", onClick: function () { setter(o.id); setMenu(""); } }, o.label);
        })));
    }

    // ── category rail ──
    var rail = h("aside", { className: "wb-mem-rail workbench-integrated-rail" + (props.sidebarCollapsed ? " is-collapsed" : "") },
      h("div", { className: "wb-mem-rail-head workbench-integrated-rail-head" },
          h("b", null, t("memory.title", "Memory")),
        h("div", { className: "workbench-integrated-rail-actions" },
          h("button", { type: "button", className: "wb-mem-new-btn workbench-integrated-rail-primary-action", onClick: function () { setModal({ mode: "create", draft: {} }); } },
            svg({ width: 13, height: 13, strokeWidth: 2.4 }, h("path", { d: "M12 5v14M5 12h14" })), h("span", null, t("memory.new", "New memory"))),
          props.onEditProjectMemory && !props.sidebarCollapsed && h("button", {
            type: "button",
            className: "wb-mem-project-memory-btn",
            onClick: props.onEditProjectMemory,
            title: t("memory.editProjectMemory", "Edit project memory"),
            "aria-label": t("memory.editProjectMemory", "Edit project memory"),
          }, ICON.all(15)),
          props.collapseControl)),
      h("div", { className: "wb-mem-rail-scroll workbench-integrated-rail-body" },
      h("div", { className: "wb-mem-cats" },
        categories.map(function (c) {
          var meta = c.id === "all" ? { tone: "accent" } : catMeta(c.id);
          return h("button", { key: c.id, type: "button", className: "wb-mem-cat" + (!activePanel && activeCat === c.id ? " active" : ""), onClick: function () { setActivePanel(""); setActiveCat(c.id); } },
            h("span", { className: "wb-mem-cat-ico " + meta.tone }, c.id === "all" ? ICON.all(15) : catIcon(c.id, 15)),
            h("span", { className: "wb-mem-cat-label" }, c.id === "all" ? t("memory.allTypes", "All types") : catLabel(c.id, t)),
            h("span", { className: "wb-mem-cat-count" }, c.count));
        }),
        h("button", { type: "button", className: "wb-mem-cat" + (activePanel === "learning" ? " active" : ""), onClick: function () { setActivePanel("learning"); setSelectedId(""); } },
          h("span", { className: "wb-mem-cat-ico blue" }, ICON.learning(15)),
          h("span", { className: "wb-mem-cat-label" }, t("memory.learningNav", "Skill learning")),
          h("span", { className: "wb-mem-cat-count" }, "›"))),
      h("div", { className: "wb-mem-card" },
        h("div", { className: "wb-mem-card-head" }, t("memory.sources", "Memory sources")),
        h("div", { className: "wb-mem-source-body" },
          h(Donut, { segments: sources, total: overview.total || 0, t: t }),
          h("div", { className: "wb-mem-source-legend" }, sources.map(function (s) {
            return h("div", { key: s.id, className: "wb-mem-legend-row" },
              h("span", { className: "wb-mem-legend-dot " + (SOURCE_TONE[s.id] || "slate") }),
              h("span", { className: "wb-mem-legend-label" }, sourceLabel(s.id, t)),
              h("span", { className: "wb-mem-legend-pct" }, s.pct + "%"));
          })),
          h("div", { className: "wb-mem-source-overview" },
            h("div", { className: "wb-mem-ov-row" }, h("span", null, t("memory.recentAdded", "Recently added")), h("b", null, overview.recent_added || 0)),
            h("div", { className: "wb-mem-ov-row" }, h("span", null, t("memory.citations", "Citations")), h("b", null, overview.total_citations || 0)),
            h("div", { className: "wb-mem-ov-row" }, h("span", null, t("memory.lastUpdated", "Last updated")), h("b", null, formatRel(overview.last_updated, t))))))),
      props.moduleDock);

    // ── memory card list ──
    function card(m) {
      var meta = catMeta(m.category, t);
      return h("button", {
        key: m.id,
        type: "button",
        "data-cyrene-context-menu": "true",
        className: "wb-mem-item" + (!activePanel && selectedId === m.id ? " active" : "") + (m.stale ? " stale" : ""),
        onClick: function () { setActivePanel(""); setSelectedId(m.id); },
        onContextMenu: function (event) { openMemoryContextMenu(m, event); },
      },
        h("span", { className: "wb-mem-ico " + meta.tone }, catIcon(m.category, 17)),
        h("div", { className: "wb-mem-item-body" },
          h("div", { className: "wb-mem-item-top" },
            h("p", { className: "wb-mem-item-text" }, m.content),
            h("time", null, formatRel(m.updated_at, t))),
          h("div", { className: "wb-mem-item-tags" },
            h(Chip, { tone: meta.tone }, catLabel(m.category, t)),
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
      decideCandidate: decideSkillCandidate,
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
      onExit: function () { setActivePanel(""); },
    }) : h("div", { className: "wb-mem-main" },
      h("div", { className: "wb-workbench-filterbar wb-mem-toolbar" },
        h("div", { className: "wb-workbench-searchbox wb-mem-searchbox" },
          svg({ width: 15, height: 15, strokeWidth: 1.9 }, h("circle", { cx: 11, cy: 11, r: 7 }), h("path", { d: "m20 20-3.2-3.2" })),
          h("input", { type: "text", placeholder: t("memory.searchPlaceholder", "Search memory…"), value: query, onChange: function (e) { setQuery(e.target.value); } })),
        h("div", { className: "wb-workbench-toolbar-controls wb-mem-tools" },
          dropdown("type", curLabel(typeOptions, activeCat), typeOptions, activeCat, function (value) { setActivePanel(""); setActiveCat(value); }),
          dropdown("source", curLabel(sourceOptions, sourceFilter), sourceOptions, sourceFilter, setSourceFilter),
          dropdown("sort", curLabel(sortOptions, sortKey), sortOptions, sortKey, setSortKey))),
      error && h("div", { className: "wb-mem-error" }, error),
      h("div", { className: "wb-mem-list-col" },
        h("div", { className: "wb-mem-scroll" },
          loading
            ? h("div", { className: "wb-mem-empty" }, t("memory.loading", "Loading memory…"))
            : visible.length === 0
              ? h("div", { className: "wb-mem-empty" },
                h("div", { className: "wb-mem-empty-icon" }, ICON.all(38)),
                h("p", null, query || activeCat !== "all" || sourceFilter ? t("memory.noMatch", "No matching memories.") : t("memory.empty", "No memories yet.")),
                h("button", { type: "button", className: "wb-btn primary", onClick: function () { setModal({ mode: "create", draft: {} }); } }, t("memory.createFirst", "Create the first memory")))
              : h("div", { className: "wb-mem-list" }, visible.map(card))),
        h("div", { className: "wb-mem-count" }, t("memory.count", "{count} memories", { count: visible.length }))));

    return h("section", { className: "wb-mem-page" + (activePanel === "learning" ? " learning-active" : "") },
      rail,
      main,
      activePanel === "learning" ? h(SkillLearningPanel, { learning: learning, detailKind: selectedLearningDetailKind, chain: selectedLearningChain, skill: selectedLearningSkill, onDeleteSkill: handleDeleteLearnedSkill }) : h(DetailPanel, {
        memory: selected, related: related, busy: busy,
        t: t,
        onSelect: setSelectedId,
        onEdit: openEdit,
        onDelete: handleDelete,
        onToggleStale: handleToggleStale,
      }),
      menu && h("div", { className: "wb-mem-scrim", onClick: function () { setMenu(""); } }),
      contextMenu && h("div", { className: "wb-item-context-layer" },
        h("div", { className: "wb-item-context-scrim", onPointerDown: function () { setContextMenu(null); } }),
        h("div", {
          className: "wb-item-context-menu",
          role: "menu",
          "aria-label": contextMenu.memory.content,
          style: { left: contextMenu.left + "px", top: contextMenu.top + "px" },
          onContextMenu: function (event) { event.preventDefault(); },
        },
          h("button", { type: "button", role: "menuitem", onClick: function () { var m = contextMenu.memory; setContextMenu(null); openEdit(m); } },
            svg({ width: 15, height: 15 }, h("path", { d: "m4 16 1-4 8.5-8.5a2.1 2.1 0 0 1 3 3L8 15l-4 1Z" })), t("memory.edit", "Edit memory")),
          h("button", { type: "button", role: "menuitem", onClick: function () { var m = contextMenu.memory; setContextMenu(null); handleToggleStale(m); } },
            svg({ width: 15, height: 15 }, h("path", { d: "M20 12a8 8 0 1 1-2.3-5.7M20 4v6h-6" })), contextMenu.memory.stale ? t("memory.restore", "Restore") : t("memory.markStale", "Mark outdated")),
          h("div", { className: "wb-item-context-separator" }),
          h("button", { type: "button", role: "menuitem", className: "danger", onClick: function () { var m = contextMenu.memory; setContextMenu(null); handleDelete(m); } },
            svg({ width: 15, height: 15 }, h("path", { d: "M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5" })), t("memory.delete", "Delete memory"))
        )),
      modal && h(MemoryModal, {
        mode: modal.mode, draft: modal.draft, busy: busy, t: t,
        onClose: function () { setModal(null); },
        onSubmit: function (body) { if (modal.mode === "edit") handleEditSubmit(modal.id, body); else handleCreate(body); },
      }));
  }

  window.CyreneUI.memory = window.CyreneUI.register("memory", {
    Page: WorkbenchMemoryPage,
  });
})();
