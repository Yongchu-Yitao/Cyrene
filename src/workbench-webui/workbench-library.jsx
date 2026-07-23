// Project-scoped literature library for the Workbench.
//
// The page intentionally owns no sample data. Every count, collection, tag and
// item is loaded from /api/workbench/library with the active project.id passed
// as `workspace`, keeping literature and generated embeddings isolated by
// project in exactly the same way as the rest of the Workbench knowledge stack.
(function () {
  var h = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useMemo = React.useMemo;
  var useRef = React.useRef;

  var PAGE_SIZE = 120;
  // Auto-sync is an app-session convenience, not a polling loop. Remember the
  // projects already checked so navigation and hidden-surface remounts cannot
  // repeatedly contact Zotero or start overlapping imports.
  var autoSyncChecked = window.__workbenchLibraryAutoSyncChecked || {};
  window.__workbenchLibraryAutoSyncChecked = autoSyncChecked;

  function icon(name, size) {
    var paths = {
      back: ["m15 18-6-6 6-6"],
      search: [h("circle", { key: "c", cx: 11, cy: 11, r: 7 }), "m20 20-3.2-3.2"],
      plus: ["M12 5v14M5 12h14"],
      filter: ["M3 5h18l-7 8v6l-4-2v-4Z"],
      sort: ["M3 6h11M3 12h8M3 18h5M17 5v14m0 0 3-3m-3 3-3-3"],
      upload: ["M12 16V4", "m7 9 5-5 5 5", "M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"],
      download: ["M12 3v12", "m8 11 4 4 4-4", "M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"],
      sync: ["M20 7h-5V2", "M4 17h5v5", "M5.2 9A8 8 0 0 1 18 5.5L20 7", "M18.8 15A8 8 0 0 1 6 18.5L4 17"],
      grid: [h("rect", { key: "a", x: 3, y: 3, width: 7, height: 7, rx: 1 }), h("rect", { key: "b", x: 14, y: 3, width: 7, height: 7, rx: 1 }), h("rect", { key: "c", x: 3, y: 14, width: 7, height: 7, rx: 1 }), h("rect", { key: "d", x: 14, y: 14, width: 7, height: 7, rx: 1 })],
      list: ["M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"],
      folder: ["M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"],
      clock: [h("circle", { key: "c", cx: 12, cy: 12, r: 9 }), "M12 7v5l3 2"],
      star: ["m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"],
      trash: ["M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"],
      tag: ["M20 13 13 20l-9-9V4h7Z", h("circle", { key: "c", cx: 8, cy: 8, r: 1.2 })],
      book: ["M5 4.5A2.5 2.5 0 0 1 7.5 2H20v15H7.5A2.5 2.5 0 0 0 5 19.5Z", "M5 19.5A2.5 2.5 0 0 0 7.5 22H20"],
      file: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z", "M14 3v5h5"],
      link: ["M10 13a4.5 4.5 0 0 0 6.6.3l2.5-2.5a4.5 4.5 0 0 0-6.4-6.4l-1.4 1.4", "M14 11a4.5 4.5 0 0 0-6.6-.3l-2.5 2.5a4.5 4.5 0 0 0 6.4 6.4l1.4-1.4"],
      eye: ["M1.5 12s4-7 10.5-7 10.5 7 10.5 7-4 7-10.5 7S1.5 12 1.5 12Z", h("circle", { key: "c", cx: 12, cy: 12, r: 3 })],
      copy: [h("rect", { key: "r", x: 9, y: 9, width: 12, height: 12, rx: 2 }), "M5 15V5a2 2 0 0 1 2-2h10"],
      chevron: ["m9 18 6-6-6-6"],
      check: ["m5 12.5 4.5 4.5L19 7"],
      close: ["m6 6 12 12M18 6 6 18"],
      note: [h("rect", { key: "r", x: 4, y: 3, width: 16, height: 18, rx: 2 }), "M8 8h8M8 12h8M8 16h5"],
      restore: ["M3 12a9 9 0 1 0 2.6-6.3", "M3 4v5h5"],
      menu: ["M5 12h.01M12 12h.01M19 12h.01"],
      panel: [h("rect", { key: "r", x: 3, y: 3, width: 18, height: 18, rx: 2 }), "M9 3v18"],
    };
    var content = paths[name] || paths.file;
    return h.apply(null, ["svg", {
      width: size || 17, height: size || 17, viewBox: "0 0 24 24", fill: "none",
      stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round",
      "aria-hidden": "true",
    }].concat(content.map(function (part, index) {
      return typeof part === "string" ? h("path", { key: index, d: part }) : part;
    })));
  }

  function formatDate(value, withTime) {
    if (!value) return "—";
    var d = new Date(value);
    if (isNaN(d.getTime())) return String(value).slice(0, withTime ? 16 : 10);
    var date = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    return withTime ? date + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0") : date;
  }

  function formatBytes(value) {
    var n = Number(value || 0);
    if (!n) return "—";
    var units = ["B", "KB", "MB", "GB"];
    var i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
    return (i ? n.toFixed(1) : n) + " " + units[i];
  }

  function creatorName(creator) {
    if (typeof creator === "string") return creator;
    if (!creator) return "";
    return creator.name || [creator.last_name || creator.lastName, creator.first_name || creator.firstName].filter(Boolean).join(", ");
  }

  function authorText(item, full) {
    var creators = Array.isArray(item && item.creators) ? item.creators : [];
    var names = creators.map(creatorName).filter(Boolean);
    if (!names.length && item && item.authors) names = Array.isArray(item.authors) ? item.authors : [item.authors];
    if (!names.length) return "—";
    if (full || names.length <= 2) return names.join("; ");
    return names[0] + " 等 " + names.length + " 人";
  }

  function itemTags(item) {
    return (Array.isArray(item && item.tags) ? item.tags : []).map(function (tag) {
      return typeof tag === "string" ? tag : (tag && (tag.name || tag.tag)) || "";
    }).filter(Boolean);
  }

  function itemTypeLabel(value) {
    return {
      document: "文档",
      journalArticle: "期刊文章",
      conferencePaper: "会议论文",
      book: "图书",
      bookSection: "图书章节",
      thesis: "学位论文",
      report: "报告",
      webpage: "网页",
    }[String(value || "")] || String(value || "文档");
  }

  function collectionName(collection) {
    return collection && (collection.name || collection.title || (collection.data && collection.data.name)) || "未命名收藏夹";
  }

  function itemTitle(item) {
    return String(item && item.title || "未命名文献").trim();
  }

  function itemKind(item) {
    var attachment = item && Array.isArray(item.attachments) && item.attachments[0];
    var name = String(attachment && (attachment.filename || attachment.name) || item && (item.filename || item.attachment_name) || "");
    if (/\.pdf$/i.test(name) || String(attachment && attachment.content_type || item && item.content_type || "").indexOf("pdf") >= 0) return "pdf";
    return item && (item.item_type || item.type) || "document";
  }

  function hasAttachment(item) {
    return !!(item && ((Array.isArray(item.attachments) && item.attachments.length) || Number(item.attachment_count || 0)));
  }

  function requestJson(url, options) {
    if (window.WorkbenchAPI && typeof window.WorkbenchAPI.json === "function") {
      return window.WorkbenchAPI.json(url, options || {});
    }
    return fetch(url, options || {}).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) throw new Error(payload.error || payload.detail || ("HTTP " + response.status));
        return payload;
      });
    });
  }

  function libraryApi(workspace) {
    var root = "/api/workbench/library";
    var ws = encodeURIComponent(workspace);
    function url(path, params) {
      var query = new URLSearchParams(params || {});
      query.set("workspace", workspace);
      return root + path + "?" + query.toString();
    }
    function json(path, options, params) {
      return requestJson(url(path, params), options || {});
    }
    function body(method, value) {
      return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(value || {}) };
    }
    return {
      list: function (params) { return json("/items", { toast: false }, params); },
      stats: function () { return json("/stats", { toast: false }); },
      collections: function () { return json("/collections", { toast: false }); },
      tags: function () { return json("/tags", { toast: false }); },
      detail: function (id) { return json("/items/" + encodeURIComponent(id), { toast: false }); },
      create: function (value) { return json("/items", body("POST", value)); },
      update: function (id, value) { return json("/items/" + encodeURIComponent(id), body("PATCH", value)); },
      remove: function (id) { return json("/items/" + encodeURIComponent(id), { method: "DELETE" }); },
      restore: function (id) { return json("/items/" + encodeURIComponent(id) + "/restore", { method: "POST" }); },
      addNote: function (id, value) { return json("/items/" + encodeURIComponent(id) + "/notes", body("POST", value)); },
      upload: function (files) {
        var form = new FormData();
        Array.prototype.forEach.call(files || [], function (file) { form.append("files", file); });
        return json("/upload", { method: "POST", body: form, timeout: 0 });
      },
      zoteroStatus: function () { return json("/zotero/status", { toast: false }); },
      zoteroSync: function (value) { return json("/zotero/sync", Object.assign(body("POST", value), { timeout: 0 })); },
      citation: function (id, style) { return json("/items/" + encodeURIComponent(id) + "/citation", { toast: false }, { style: style || "ieee" }); },
      rawUrl: function (id) { return root + "/items/" + encodeURIComponent(id) + "/raw?workspace=" + ws; },
    };
  }

  window.WorkbenchLibraryAPI = libraryApi;

  function PdfMark(props) {
    var pdf = itemKind(props.item) === "pdf";
    return h("span", { className: "wb-lib-filemark " + (pdf ? "pdf" : "paper"), "aria-hidden": "true" },
      pdf ? h(React.Fragment, null, icon("file", props.large ? 24 : 17), h("small", null, "PDF")) : icon("file", props.large ? 24 : 17));
  }

  function Spinner() { return h("span", { className: "wb-lib-spinner", "aria-hidden": "true" }); }

  function Toast(message, type) {
    if (window.showToast) window.showToast(message, type || "success");
  }

  function StopClick(props) {
    return h("span", { onClick: function (event) { event.stopPropagation(); } }, props.children);
  }

  function SidebarRow(props) {
    return h("button", {
      type: "button",
      className: "wb-lib-side-row" + (props.active ? " active" : ""),
      onClick: props.onClick,
      title: props.label,
    }, h("span", { className: "wb-lib-side-icon" }, props.icon), h("span", { className: "wb-lib-side-label" }, props.label),
      props.count != null && h("span", { className: "wb-lib-side-count" }, Number(props.count || 0).toLocaleString()));
  }

  function LibrarySidebar(props) {
    var stats = props.stats || {};
    var collapsedState = useState({ library: false, collections: false, tags: false });
    var collapsed = collapsedState[0];
    var setCollapsed = collapsedState[1];
    function toggleSection(id) {
      setCollapsed(function (prev) {
        return Object.assign({}, prev, { [id]: !prev[id] });
      });
    }
    function sectionHeading(id, label) {
      var expanded = !collapsed[id];
      return h("h2", null,
        h("button", {
          type: "button",
          className: "wb-lib-side-section-toggle",
          onClick: function () { toggleSection(id); },
          "aria-expanded": expanded,
        },
          h("span", { className: "wb-lib-side-caret" + (expanded ? " open" : "") }, icon("chevron", 13)),
          h("span", null, label)));
    }
    var base = [
      { id: "all", label: "全部知识", icon: icon("book"), count: stats.total },
      { id: "unclassified", label: "未分类", icon: icon("file"), count: stats.unclassified },
      { id: "recent_added", label: "最近添加", icon: icon("clock"), count: stats.recent_added },
      { id: "recent_read", label: "最近阅读", icon: icon("clock"), count: stats.recent_read },
      { id: "starred", label: "星标", icon: icon("star"), count: stats.starred },
      { id: "trash", label: "回收站", icon: icon("trash"), count: stats.trash },
    ];
    return h("aside", { className: "wb-lib-sidebar" + (props.open ? " open" : "") },
      h("div", { className: "wb-lib-sidebar-head" },
        h("h1", null, "知识库"),
        h("button", { type: "button", className: "wb-lib-side-close", onClick: props.onClose, title: "收起侧栏" }, icon("close"))),
      h("div", { className: "wb-lib-side-scroll" },
        h("section", { className: "wb-lib-side-section" },
          sectionHeading("library", "我的知识库"),
          !collapsed.library && base.map(function (row) {
            return h(SidebarRow, { key: row.id, label: row.label, icon: row.icon, count: row.count, active: props.scope.type === row.id, onClick: function () { props.onScope({ type: row.id }); } });
          })),
        h("section", { className: "wb-lib-side-section" },
          sectionHeading("collections", "我的收藏夹"),
          !collapsed.collections && (props.collections.length ? props.collections.map(function (collection, index) {
            var id = String(collection.id || collection.key || index);
            return h(SidebarRow, {
              key: id, label: collectionName(collection), count: collection.count, active: props.scope.type === "collection" && String(props.scope.value) === id,
              icon: h("span", { className: "wb-lib-folder-icon", style: { color: collection.color || ["#e35d9c", "#f29f3f", "#47b7c5", "#d5b42e", "#8d68e8"][index % 5] } }, icon("folder", 16)),
              onClick: function () { props.onScope({ type: "collection", value: id, label: collectionName(collection) }); },
            });
          }) : h("p", { className: "wb-lib-side-empty" }, "尚无收藏夹"))),
        h("section", { className: "wb-lib-side-section wb-lib-tag-cloud" },
          sectionHeading("tags", "标签云"),
          !collapsed.tags && (props.tags.length ? h("div", { className: "wb-lib-cloud" }, props.tags.map(function (tag) {
            var name = typeof tag === "string" ? tag : tag.name;
            return h("button", { key: name, type: "button", className: props.scope.type === "tag" && props.scope.value === name ? "active" : "", onClick: function () { props.onScope({ type: "tag", value: name, label: name }); } }, name, h("span", null, typeof tag === "string" ? "" : tag.count));
          })) : h("p", { className: "wb-lib-side-empty" }, "尚无标签")))));
  }

  function TableHead(props) {
    return h("div", { className: "wb-lib-table-head wb-lib-table-grid", role: "row" },
      h("label", { className: "wb-lib-check" }, h("input", { type: "checkbox", checked: props.allSelected, onChange: props.onToggleAll, "aria-label": "选择全部文献" }), h("span")),
      h("span", null, "标题"), h("span", null, "作者"), h("span", null, "年份"), h("span", null, "发布源"), h("span", null, "添加时间"), h("span", null, "标签"));
  }

  function LibraryRow(props) {
    var item = props.item;
    var tags = itemTags(item);
    var isTrash = props.trash;
    return h("div", {
      className: "wb-lib-row wb-lib-table-grid" + (props.active ? " active" : ""), role: "row", tabIndex: 0,
      onClick: function () { props.onSelect(item.id); },
      onKeyDown: function (event) { if (event.key === "Enter") props.onSelect(item.id); },
    },
      h(StopClick, null, h("label", { className: "wb-lib-check" }, h("input", { type: "checkbox", checked: props.checked, onChange: function () { props.onToggle(item.id); }, "aria-label": "选择 " + itemTitle(item) }), h("span"))),
      h("div", { className: "wb-lib-title-cell" },
        h(StopClick, null, h("button", { type: "button", className: "wb-lib-star" + (item.starred ? " active" : ""), onClick: function () { props.onStar(item); }, title: item.starred ? "取消星标" : "添加星标" }, icon("star", 16))),
        h(PdfMark, { item: item }),
        h("span", { className: "wb-lib-title-text", title: itemTitle(item) }, itemTitle(item))),
      h("span", { className: "wb-lib-truncate", title: authorText(item, true) }, authorText(item)),
      h("span", null, item.year || (item.date_text && String(item.date_text).slice(0, 4)) || "—"),
      h("span", { className: "wb-lib-truncate", title: item.venue || item.publication_title || "" }, item.venue || item.publication_title || "—"),
      h("span", null, formatDate(item.added_at || item.created_at)),
      h("div", { className: "wb-lib-row-tags" }, tags.slice(0, 2).map(function (tag) { return h("span", { key: tag }, tag); }), tags.length > 2 && h("small", null, "+" + (tags.length - 2)),
        isTrash && h(StopClick, null, h("button", { type: "button", className: "wb-lib-row-action", onClick: function () { props.onRestore(item); }, title: "恢复" }, icon("restore", 14)))));
  }

  function LibraryCard(props) {
    var item = props.item;
    return h("article", { className: "wb-lib-card" + (props.active ? " active" : ""), onClick: function () { props.onSelect(item.id); }, tabIndex: 0 },
      h("div", { className: "wb-lib-card-top" }, h(PdfMark, { item: item, large: true }), h("button", { type: "button", className: "wb-lib-star" + (item.starred ? " active" : ""), onClick: function (event) { event.stopPropagation(); props.onStar(item); } }, icon("star", 17))),
      h("h3", null, itemTitle(item)), h("p", null, authorText(item)),
      h("div", { className: "wb-lib-card-meta" }, h("span", null, item.year || "—"), h("span", null, item.venue || item.publication_title || "—")),
      h("div", { className: "wb-lib-row-tags" }, itemTags(item).slice(0, 3).map(function (tag) { return h("span", { key: tag }, tag); })));
  }

  function StatePanel(props) {
    return h("div", { className: "wb-lib-state " + (props.kind || "") }, props.loading ? h(Spinner) : icon(props.kind === "error" ? "restore" : "book", 42),
      h("h3", null, props.title), props.body && h("p", null, props.body), props.action && h("button", { type: "button", className: "wb-lib-primary", onClick: props.action }, props.actionLabel));
  }

  function MetaLine(props) {
    var empty = props.value == null || props.value === "";
    if (empty && !props.showEmpty) return null;
    return h("div", { className: "wb-lib-meta-line" }, h("dt", null, props.label), h("dd", null, empty ? "—" : props.value));
  }

  function InfoWorkspace(props) {
    var item = props.item;
    var attachment = Array.isArray(item.attachments) && item.attachments[0];
    var notes = Array.isArray(item.notes) ? item.notes : [];
    var statusOptions = [{ id: "unread", label: "待读" }, { id: "reading", label: "阅读中" }, { id: "read", label: "已读" }];
    var editingState = useState(false); var editing = editingState[0]; var setEditing = editingState[1];
    var savingState = useState(false); var saving = savingState[0]; var setSaving = savingState[1];
    function draftFromItem(value) {
      var authors = authorText(value, true);
      return {
        item_type: value.item_type || "document",
        title: itemTitle(value),
        authors: authors === "—" ? "" : authors,
        venue: value.venue || value.publication_title || "",
        volume: value.volume || "",
        issue: value.issue || "",
        pages: value.pages || "",
        year: value.year || "",
        doi: value.doi || "",
        isbn: value.isbn || "",
        language: value.language || "",
      };
    }
    var formState = useState(draftFromItem(item)); var form = formState[0]; var setForm = formState[1];
    useEffect(function () { setEditing(false); setForm(draftFromItem(item)); }, [item.id]);
    function field(name, value) {
      var next = Object.assign({}, form); next[name] = value; setForm(next);
    }
    function editField(label, name, type, wide) {
      return h("label", { className: wide ? "wide" : "" }, h("span", null, label),
        h("input", { type: type || "text", value: form[name], onChange: function (event) { field(name, event.target.value); } }));
    }
    function saveMetadata(event) {
      event.preventDefault();
      if (!form.title.trim() || saving) return;
      setSaving(true);
      var creators = form.authors.split(/[;；\n]+/).map(function (name) { return name.trim(); }).filter(Boolean).map(function (name) { return { name: name, creator_type: "author" }; });
      props.onUpdate({
        item_type: form.item_type, title: form.title.trim(), creators: creators,
        venue: form.venue.trim(), volume: form.volume.trim(), issue: form.issue.trim(),
        pages: form.pages.trim(), year: form.year, doi: form.doi.trim(),
        isbn: form.isbn.trim(), language: form.language.trim(),
      }).then(function () { setSaving(false); setEditing(false); }, function () { setSaving(false); });
    }
    var itemTypes = ["document", "journalArticle", "conferencePaper", "book", "bookSection", "thesis", "report", "webpage"];
    if (itemTypes.indexOf(form.item_type) < 0) itemTypes.unshift(form.item_type);
    return h("div", { className: "wb-lib-info-workspace" },
      h("div", { className: "wb-lib-paper-summary", role: "region", tabIndex: 0, "aria-label": "文献具体信息" },
        h("div", { className: "wb-lib-paper-heading" }, h(PdfMark, { item: item, large: true }), h("div", null, h("h3", null, itemTitle(item)), h("div", { className: "wb-lib-paper-sub" }, authorText(item, true)))),
        h("div", { className: "wb-lib-paper-actions" },
          hasAttachment(item) ? h("a", { className: "wb-lib-secondary", href: props.rawUrl, target: "_blank", rel: "noreferrer" }, "打开文件", icon("chevron", 13)) : h("button", { type: "button", className: "wb-lib-secondary", disabled: true }, "暂无附件"),
          h("select", { value: item.reading_status || "unread", onChange: function (event) { props.onUpdate({ reading_status: event.target.value }); }, "aria-label": "阅读状态" }, statusOptions.map(function (option) { return h("option", { key: option.id, value: option.id }, option.label); }))),
        editing ? h("form", { className: "wb-lib-paper-editor", onSubmit: saveMetadata },
          h("label", null, h("span", null, "条目类型"), h("select", { value: form.item_type, onChange: function (event) { field("item_type", event.target.value); } }, itemTypes.map(function (type) { return h("option", { key: type, value: type }, itemTypeLabel(type)); }))),
          editField("年份", "year", "number"),
          editField("标题", "title", "text", true),
          editField("作者", "authors", "text", true),
          editField("出版源", "venue", "text", true),
          editField("卷", "volume"), editField("期号", "issue"), editField("页码", "pages"),
          editField("DOI", "doi", "text", true), editField("ISBN", "isbn"), editField("语言", "language"),
          h("footer", null,
            h("button", { type: "button", className: "wb-lib-secondary", disabled: saving, onClick: function () { setEditing(false); setForm(draftFromItem(item)); } }, "取消"),
            h("button", { type: "submit", className: "wb-lib-primary", disabled: saving || !form.title.trim() }, saving ? h(Spinner) : null, saving ? "保存中" : "保存"))):
          h("dl", { className: "wb-lib-paper-meta" },
            h(MetaLine, { label: "条目类型", value: itemTypeLabel(item.item_type || item.type), showEmpty: true }), h(MetaLine, { label: "标题", value: itemTitle(item), showEmpty: true }),
            h(MetaLine, { label: "作者", value: authorText(item, true), showEmpty: true }), h(MetaLine, { label: "出版源", value: item.venue || item.publication_title, showEmpty: true }),
            h(MetaLine, { label: "卷", value: item.volume, showEmpty: true }), h(MetaLine, { label: "期号", value: item.issue, showEmpty: true }), h(MetaLine, { label: "页码", value: item.pages, showEmpty: true }),
            h(MetaLine, { label: "年份", value: item.year, showEmpty: true }), h(MetaLine, { label: "DOI", value: item.doi, showEmpty: true }), h(MetaLine, { label: "ISBN", value: item.isbn, showEmpty: true }),
            h(MetaLine, { label: "语言", value: item.language, showEmpty: true }), h(MetaLine, { label: "添加时间", value: formatDate(item.added_at || item.created_at, true), showEmpty: true }),
            h(MetaLine, { label: "更新时间", value: formatDate(item.updated_at, true), showEmpty: true }),
            h(MetaLine, { label: "附件", value: attachment && (attachment.filename || attachment.name), showEmpty: true }))),
      h("div", { className: "wb-lib-work-cards", role: "region", tabIndex: 0, "aria-label": "摘要、笔记和标签" },
        h("section", { className: "wb-lib-work-card" }, h("h3", null, "摘要"), h("p", null, item.abstract || "无摘要信息")),
        h("section", { className: "wb-lib-work-card" }, h("div", { className: "wb-lib-work-head" }, h("h3", null, "笔记"), h("button", { type: "button", onClick: function () { props.onTab("notes"); } }, icon("plus", 14), " 添加笔记")),
          notes.length ? h(React.Fragment, null, h("p", null, notes[0].content || notes[0].text || ""), h("small", null, notes[0].author || "我", " · ", formatDate(notes[0].updated_at || notes[0].created_at))) : h("p", { className: "wb-lib-muted" }, "还没有笔记。")),
        h("section", { className: "wb-lib-work-card" }, h("h3", null, "标签"), h("div", { className: "wb-lib-tag-list" }, itemTags(item).map(function (tag) { return h("span", { key: tag }, tag); }), h("button", { type: "button", onClick: function () { props.onTab("tags"); } }, icon("plus", 14), " 添加标签")))));
  }

  function NotesWorkspace(props) {
    var notes = Array.isArray(props.item.notes) ? props.item.notes : [];
    var state = useState(""); var value = state[0]; var setValue = state[1];
    var busyState = useState(false); var busy = busyState[0]; var setBusy = busyState[1];
    function submit() {
      var content = value.trim();
      if (!content || busy) return;
      setBusy(true);
      props.onAdd({ title: "研究笔记", content: content, author: (window.DATA && DATA.user && DATA.user.name) || "我" }).then(function () { setValue(""); setBusy(false); }, function () { setBusy(false); });
    }
    return h("div", { className: "wb-lib-editor-layout" },
      h("div", { className: "wb-lib-note-compose" }, h("textarea", { value: value, onChange: function (event) { setValue(event.target.value); }, placeholder: "记录这篇文献的发现、疑问或下一步…" }), h("button", { type: "button", className: "wb-lib-primary", disabled: busy || !value.trim(), onClick: submit }, busy ? h(Spinner) : icon("plus", 14), " 添加笔记")),
      h("div", { className: "wb-lib-note-list" }, notes.length ? notes.map(function (note) { return h("article", { key: note.id }, h("h4", null, note.title || "笔记"), h("p", null, note.content || note.text || ""), h("small", null, note.author || "我", " · ", formatDate(note.updated_at || note.created_at, true))); }) : h("p", { className: "wb-lib-muted" }, "还没有笔记。")));
  }

  function TagsWorkspace(props) {
    var valueState = useState(""); var value = valueState[0]; var setValue = valueState[1];
    var tags = itemTags(props.item);
    function save(next) { props.onUpdate({ tags: next }); }
    function add() {
      var additions = value.split(/[,，;；\n]+/).map(function (v) { return v.trim(); }).filter(Boolean);
      var next = tags.slice();
      additions.forEach(function (tag) { if (next.indexOf(tag) < 0) next.push(tag); });
      if (next.length !== tags.length) save(next);
      setValue("");
    }
    return h("div", { className: "wb-lib-tags-workspace" }, h("h3", null, "文献标签"), h("p", null, "标签只保存在当前项目的文献库中。"),
      h("div", { className: "wb-lib-tag-editor" }, tags.map(function (tag) { return h("span", { key: tag }, tag, h("button", { type: "button", onClick: function () { save(tags.filter(function (v) { return v !== tag; })); }, title: "移除 " + tag }, icon("close", 12))); }),
        h("input", { value: value, onChange: function (event) { setValue(event.target.value); }, onKeyDown: function (event) { if (event.key === "Enter") { event.preventDefault(); add(); } }, placeholder: "输入标签后回车" })),
      h("button", { type: "button", className: "wb-lib-secondary", disabled: !value.trim(), onClick: add }, icon("plus", 14), " 添加"));
  }

  function RelationsWorkspace(props) {
    var relations = Array.isArray(props.item.relations) ? props.item.relations : [];
    return h("div", { className: "wb-lib-relations" }, relations.length ? relations.map(function (relation, index) {
      return h("article", { key: relation.id || index }, h("span", null, icon("link", 17)), h("div", null, h("h4", null, relation.title || relation.name || relation.target_title || "关联条目"), h("p", null, relation.relation_type || relation.type || "related")));
    }) : h(StatePanel, { title: "尚无关联条目", body: "同步 Zotero Related Items，或由 Agent 建立项目内的文献关系。" }));
  }

  function AttachmentsWorkspace(props) {
    var attachments = Array.isArray(props.item.attachments) ? props.item.attachments : [];
    return h("div", { className: "wb-lib-attachments" }, attachments.length ? attachments.map(function (attachment, index) {
      return h("a", { key: attachment.id || index, href: attachment.raw_url || props.rawUrl, target: "_blank", rel: "noreferrer" }, h(PdfMark, { item: Object.assign({}, props.item, { attachments: [attachment] }) }), h("div", null, h("b", null, attachment.filename || attachment.name || "附件"), h("small", null, formatBytes(attachment.size), attachment.page_count ? " · " + attachment.page_count + " 页" : "")), icon("eye", 16));
    }) : h(StatePanel, { title: "暂无附件", body: "可以从工具栏导入 PDF，或从 Zotero 同步附件。" }));
  }

  function CitationCopyControl(props) {
    var openState = useState(false); var open = openState[0]; var setOpen = openState[1];
    var disabled = !props.citation && !props.bibtex;
    function copy(format) { setOpen(false); props.onCopy(format); }
    return h("div", { className: "wb-lib-menu-wrap wb-lib-copy-control" },
      h("button", { type: "button", className: "wb-lib-copy-trigger", disabled: disabled, onClick: function () { setOpen(!open); } }, icon("copy", 13), " 复制", h("span", { className: "wb-lib-copy-chevron" }, icon("chevron", 11))),
      h(Dropdown, { open: open, onClose: function () { setOpen(false); }, className: "wb-lib-copy-menu" },
        h("button", { type: "button", disabled: !props.citation, onClick: function () { copy("text"); } }, h("span", null, h("b", null, "复制纯文本"), h("small", null, "当前引用格式"))),
        h("button", { type: "button", disabled: !props.bibtex, onClick: function () { copy("bibtex"); } }, h("span", null, h("b", null, "复制 BibTeX"), h("small", null, "可直接导入文献工具")))));
  }

  function CitationWorkspace(props) {
    return h("div", { className: "wb-lib-citation-workspace" }, h("div", { className: "wb-lib-citation-toolbar" }, h("label", null, "引用格式", h("select", { value: props.style, onChange: function (event) { props.onStyle(event.target.value); } }, ["ieee", "apa", "mla", "chicago-author-date", "gb-t-7714-2015-numeric"].map(function (style) { return h("option", { key: style, value: style }, style.toUpperCase()); }))), h(CitationCopyControl, { citation: props.citation, bibtex: props.bibtex, onCopy: props.onCopy })),
      props.loading ? h(StatePanel, { loading: true, title: "正在生成引用…" }) : props.error ? h(StatePanel, { kind: "error", title: "引用生成失败", body: props.error, action: props.onRetry, actionLabel: "重试" }) : h("blockquote", null, props.citation || "暂无可用引用。"),
      props.citekey && h("div", { className: "wb-lib-citekey" }, h("span", null, "Citation key"), h("code", null, props.citekey)));
  }

  function ItemWorkspace(props) {
    var heightState = useState(function () {
      try {
        var saved = Number(window.localStorage.getItem("cyrene.library.workspaceHeight") || 0);
        return saved > 0 ? saved : null;
      } catch (_) {
        return null;
      }
    });
    var panelHeight = heightState[0]; var setPanelHeight = heightState[1];
    var resizeRef = useRef(null);
    var tabs = [
      { id: "info", label: "信息" }, { id: "notes", label: "笔记" }, { id: "tags", label: "标签" },
      { id: "relations", label: "关联" }, { id: "attachments", label: "附件" }, { id: "citation", label: "引用" },
    ];

    function heightBounds(handle) {
      var workspaceNode = handle && handle.parentElement;
      var hostNode = workspaceNode && workspaceNode.parentElement;
      var hostHeight = hostNode ? hostNode.getBoundingClientRect().height : window.innerHeight;
      return { min: 170, max: Math.max(170, Math.floor(hostHeight * .5)) };
    }
    function rememberHeight(value) {
      setPanelHeight(value);
      try { window.localStorage.setItem("cyrene.library.workspaceHeight", String(Math.round(value))); } catch (_) {}
    }
    function beginResize(event) {
      if (event.button !== 0) return;
      var workspaceNode = event.currentTarget.parentElement;
      var bounds = heightBounds(event.currentTarget);
      resizeRef.current = {
        pointerId: event.pointerId,
        startY: event.clientY,
        startHeight: workspaceNode.getBoundingClientRect().height,
        min: bounds.min,
        max: bounds.max,
      };
      try { event.currentTarget.setPointerCapture(event.pointerId); } catch (_) {}
      event.preventDefault();
    }
    function moveResize(event) {
      var state = resizeRef.current;
      if (!state || state.pointerId !== event.pointerId) return;
      rememberHeight(Math.max(state.min, Math.min(state.max, state.startHeight + state.startY - event.clientY)));
    }
    function endResize(event) {
      if (!resizeRef.current || resizeRef.current.pointerId !== event.pointerId) return;
      resizeRef.current = null;
      try { event.currentTarget.releasePointerCapture(event.pointerId); } catch (_) {}
    }
    function resizeWithKeyboard(event) {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      var bounds = heightBounds(event.currentTarget);
      var current = event.currentTarget.parentElement.getBoundingClientRect().height;
      var next = current + (event.key === "ArrowUp" ? 24 : -24);
      rememberHeight(Math.max(bounds.min, Math.min(bounds.max, next)));
      event.preventDefault();
    }
    function resetHeight() {
      setPanelHeight(null);
      try { window.localStorage.removeItem("cyrene.library.workspaceHeight"); } catch (_) {}
    }

    return h("section", {
      className: "wb-lib-item-workspace" + (panelHeight ? " is-resized" : ""),
      style: panelHeight ? { height: Math.round(panelHeight) + "px" } : null,
    },
      h("div", {
        className: "wb-lib-work-resizer",
        role: "separator",
        tabIndex: 0,
        title: "拖动调整详情面板高度；双击恢复自动高度",
        "aria-label": "调整详情面板高度",
        "aria-orientation": "horizontal",
        "aria-valuemin": 170,
        "aria-valuenow": panelHeight || undefined,
        onPointerDown: beginResize,
        onPointerMove: moveResize,
        onPointerUp: endResize,
        onPointerCancel: endResize,
        onKeyDown: resizeWithKeyboard,
        onDoubleClick: resetHeight,
      }, icon("menu", 15)),
      h("nav", { className: "wb-lib-work-tabs", "aria-label": "文献详情标签" },
        tabs.map(function (tab) {
          return h("button", {
            key: tab.id,
            type: "button",
            className: props.tab === tab.id ? "active" : "",
            onClick: function () { props.onTab(tab.id); },
          }, tab.label);
        }),
        h("button", { type: "button", className: "wb-lib-work-tabs-more", title: "更多", "aria-label": "更多" }, icon("menu", 17))),
      h("div", { className: "wb-lib-work-body" + (props.tab === "info" ? " info" : "") }, props.loading ? h(StatePanel, { loading: true, title: "正在加载文献详情…" }) :
        props.tab === "info" ? h(InfoWorkspace, { item: props.item, rawUrl: props.rawUrl, onUpdate: props.onUpdate, onTab: props.onTab }) :
          props.tab === "notes" ? h(NotesWorkspace, { item: props.item, onAdd: props.onAddNote }) :
            props.tab === "tags" ? h(TagsWorkspace, { item: props.item, onUpdate: props.onUpdate }) :
              props.tab === "relations" ? h(RelationsWorkspace, { item: props.item }) :
                props.tab === "attachments" ? h(AttachmentsWorkspace, { item: props.item, rawUrl: props.rawUrl }) :
                  h(CitationWorkspace, props.citationProps)));
  }

  function RightMetadataEditor(props) {
    function draftFromItem(item) {
      var authors = authorText(item, true);
      return {
        item_type: item.item_type || "document",
        title: itemTitle(item),
        authors: authors === "—" ? "" : authors,
        abstract: item.abstract || "",
        venue: item.venue || item.publication_title || "",
        publisher: item.publisher || "",
        volume: item.volume || "",
        issue: item.issue || "",
        pages: item.pages || "",
        year: item.year || "",
        doi: item.doi || "",
        isbn: item.isbn || "",
        language: item.language || "",
        url: item.url || "",
        date_text: item.date_text || "",
        citekey: item.citekey || "",
        reading_status: item.reading_status || "unread",
        starred: !!item.starred,
        tags: itemTags(item).join("; "),
      };
    }
    var formState = useState(draftFromItem(props.item)); var form = formState[0]; var setForm = formState[1];
    var savingState = useState(false); var saving = savingState[0]; var setSaving = savingState[1];
    useEffect(function () { setForm(draftFromItem(props.item)); setSaving(false); }, [props.item.id]);
    function field(name, value) {
      var next = Object.assign({}, form); next[name] = value; setForm(next);
    }
    function inputField(label, name, type) {
      return h("label", null, h("span", null, label), h("input", {
        type: type || "text",
        value: form[name],
        onChange: function (event) { field(name, event.target.value); },
      }));
    }
    function submit(event) {
      event.preventDefault();
      if (!form.title.trim() || saving) return;
      setSaving(true);
      var creators = form.authors.split(/[;；\n]+/).map(function (name) { return name.trim(); }).filter(Boolean).map(function (name) { return { name: name, creator_type: "author" }; });
      var tags = form.tags.split(/[,，;；\n]+/).map(function (tag) { return tag.trim(); }).filter(Boolean);
      props.onSave({
        item_type: form.item_type,
        title: form.title.trim(),
        creators: creators,
        abstract: form.abstract.trim(),
        venue: form.venue.trim(),
        publisher: form.publisher.trim(),
        volume: form.volume.trim(),
        issue: form.issue.trim(),
        pages: form.pages.trim(),
        year: form.year,
        doi: form.doi.trim(),
        isbn: form.isbn.trim(),
        language: form.language.trim(),
        url: form.url.trim(),
        date_text: form.date_text.trim(),
        citekey: form.citekey.trim(),
        reading_status: form.reading_status,
        starred: form.starred,
        tags: tags,
      }).then(function () { setSaving(false); props.onCancel(); }, function () { setSaving(false); });
    }
    var itemTypes = ["document", "journalArticle", "conferencePaper", "book", "bookSection", "thesis", "report", "webpage"];
    if (itemTypes.indexOf(form.item_type) < 0) itemTypes.unshift(form.item_type);
    return h("form", { className: "wb-lib-right-editor", onSubmit: submit },
      h("header", null, h("div", null, h("h3", null, "编辑文献信息"), h("p", null, "文件大小和时间由系统维护，其余文献元数据均可修改。"))),
      h("label", null, h("span", null, "条目类型"), h("select", { value: form.item_type, onChange: function (event) { field("item_type", event.target.value); } }, itemTypes.map(function (type) { return h("option", { key: type, value: type }, itemTypeLabel(type)); }))),
      inputField("标题", "title"),
      h("label", null, h("span", null, "作者"), h("textarea", { value: form.authors, onChange: function (event) { field("authors", event.target.value); }, placeholder: "多个作者用分号或换行分隔" })),
      h("label", null, h("span", null, "摘要"), h("textarea", { value: form.abstract, onChange: function (event) { field("abstract", event.target.value); }, placeholder: "来源元数据中的摘要" })),
      inputField("出版源", "venue"),
      inputField("出版社", "publisher"),
      h("div", { className: "wb-lib-right-editor-grid" }, inputField("卷", "volume"), inputField("期号", "issue"), inputField("页码", "pages"), inputField("年份", "year", "number")),
      inputField("DOI", "doi"),
      inputField("ISBN", "isbn"),
      inputField("语言", "language"),
      inputField("URL", "url", "url"),
      inputField("出版日期", "date_text"),
      inputField("引用键", "citekey"),
      h("label", null, h("span", null, "阅读状态"), h("select", { value: form.reading_status, onChange: function (event) { field("reading_status", event.target.value); } },
        h("option", { value: "unread" }, "待读"), h("option", { value: "reading" }, "阅读中"), h("option", { value: "read" }, "已读"), h("option", { value: "archived" }, "已归档"))),
      inputField("标签", "tags"),
      h("label", { className: "wb-lib-right-editor-check" }, h("input", { type: "checkbox", checked: form.starred, onChange: function (event) { field("starred", event.target.checked); } }), h("span", null, "标记为星标")),
      h("footer", null,
        h("button", { type: "button", className: "wb-lib-secondary", disabled: saving, onClick: props.onCancel }, "取消"),
        h("button", { type: "submit", className: "wb-lib-primary", disabled: saving || !form.title.trim() }, saving ? h(Spinner) : null, saving ? "保存中" : "保存全部信息")));
  }

  function RightPanel(props) {
    var editingState = useState(false); var editing = editingState[0]; var setEditing = editingState[1];
    useEffect(function () { setEditing(false); }, [props.item && props.item.id]);
    if (!props.item) return h("aside", { className: "wb-lib-right empty" }, h("div", null, icon("panel", 34), h("p", null, "选择一篇文献查看详情")));
    var item = props.item;
    var attachment = Array.isArray(item.attachments) && item.attachments[0];
    var relations = Array.isArray(item.relations) ? item.relations : [];
    var annotations = Array.isArray(item.annotations) ? item.annotations : [];
    return h("aside", { className: "wb-lib-right" + (props.open ? " open" : "") },
      h("nav", { className: "wb-lib-right-tabs" },
        [{ id: "detail", label: "详情" }, { id: "content", label: "内容" }, { id: "related", label: "关联" }].map(function (tab) { return h("button", { key: tab.id, type: "button", className: props.tab === tab.id ? "active" : "", onClick: function () { props.onTab(tab.id); } }, tab.label); }),
        h("button", { type: "button", className: "wb-lib-right-delete", disabled: !props.onDelete, onClick: props.onDelete, title: "移至回收站", "aria-label": "移至回收站" }, icon("trash", 15)),
        h("button", { type: "button", className: "wb-lib-right-close", onClick: props.onClose, title: "关闭详情" }, icon("close", 15))),
      h("div", { className: "wb-lib-right-scroll" },
        h("header", { className: "wb-lib-right-head" }, h(PdfMark, { item: item }), h("b", { title: itemTitle(item) }, itemTitle(item)), hasAttachment(item) && h("a", { href: props.rawUrl, target: "_blank", rel: "noreferrer", title: "查看附件" }, icon("eye", 17))),
        props.tab === "detail" && editing && h(RightMetadataEditor, { item: item, onSave: props.onUpdate, onCancel: function () { setEditing(false); } }),
        props.tab === "detail" && !editing && h(React.Fragment, null,
          h("p", { className: "wb-lib-right-abstract" }, item.abstract || "无摘要信息"),
          h("section", null, h("h3", null, "文件信息"), h("div", { className: "wb-lib-right-card" },
            h(MetaLine, { label: "文件大小", value: formatBytes(attachment && attachment.size) }), h(MetaLine, { label: "页数", value: attachment && attachment.page_count }),
            h(MetaLine, { label: "创建时间", value: formatDate(item.created_at, true) }), h(MetaLine, { label: "修改时间", value: formatDate(item.updated_at, true) }),
            h(MetaLine, { label: "来源", value: item.provider === "zotero" ? "Zotero" : (item.provider || "Cyrene") }))),
          h("section", null, h("div", { className: "wb-lib-right-section-head" }, h("h3", null, "引用格式"), h(CitationCopyControl, { citation: props.citation, bibtex: props.bibtex, onCopy: props.onCopyCitation })), h("div", { className: "wb-lib-right-card wb-lib-right-citation" }, props.citationLoading ? h(Spinner) : (props.citation || "暂无可用引用。"))),
          h("section", null, h("h3", null, "关联条目"), h("div", { className: "wb-lib-right-card wb-lib-right-relations" }, relations.slice(0, 3).map(function (relation, index) { return h("p", { key: relation.id || index }, (index + 1) + ". ", relation.other_title || relation.title || relation.target_title || "关联条目"); }), !relations.length && h("p", { className: "wb-lib-muted" }, "暂无关联条目"))),
          h("button", { type: "button", className: "wb-lib-right-edit-button", onClick: function () { setEditing(true); } }, icon("note", 15), "编辑信息")),
        props.tab === "content" && h("div", { className: "wb-lib-content-panel" }, item.content || item.indexed_text ? h("pre", null, item.content || item.indexed_text) : h(React.Fragment, null, h("h3", null, "摘要"), h("p", null, item.abstract || "暂无可检索文本。"), annotations.length > 0 && h("section", null, h("h3", null, "PDF 批注"), annotations.map(function (annotation) { return h("blockquote", { key: annotation.id }, h("p", null, annotation.quote || annotation.text || annotation.comment), h("small", null, annotation.page_label ? "第 " + annotation.page_label + " 页" : "")); })))),
        props.tab === "related" && h(RelationsWorkspace, { item: item })),
      h("button", { type: "button", className: "wb-lib-detail-fab", onClick: props.onClose, title: "关闭详情" }, icon("panel", 17)));
  }

  function Dropdown(props) {
    if (!props.open) return null;
    return h(React.Fragment, null, h("button", { type: "button", className: "wb-lib-scrim", onClick: props.onClose, tabIndex: -1, "aria-label": "关闭菜单" }), h("div", { className: "wb-lib-dropdown " + (props.className || "") }, props.children));
  }

  function ManualItemModal(props) {
    var initial = { title: "", authors: "", year: "", venue: "", doi: "", item_type: "journalArticle" };
    var formState = useState(initial); var form = formState[0]; var setForm = formState[1];
    var busyState = useState(false); var busy = busyState[0]; var setBusy = busyState[1];
    function field(name, value) { setForm(Object.assign({}, form, (function () { var o = {}; o[name] = value; return o; })())); }
    function submit(event) {
      event.preventDefault(); if (!form.title.trim() || busy) return; setBusy(true);
      var creators = form.authors.split(/[;；\n]+/).map(function (name) { return name.trim(); }).filter(Boolean).map(function (name) { return { name: name, creator_type: "author" }; });
      props.onSave(Object.assign({}, form, { year: form.year ? Number(form.year) : null, creators: creators })).then(function () { setBusy(false); props.onClose(); }, function () { setBusy(false); });
    }
    return h("div", { className: "wb-lib-modal-layer", role: "presentation", onMouseDown: function (event) { if (event.target === event.currentTarget) props.onClose(); } }, h("form", { className: "wb-lib-modal", onSubmit: submit },
      h("header", null, h("div", null, h("h2", null, "添加文献条目"), h("p", null, "条目只会添加到当前项目。")), h("button", { type: "button", onClick: props.onClose }, icon("close"))),
      h("label", null, "标题", h("input", { required: true, autoFocus: true, value: form.title, onChange: function (event) { field("title", event.target.value); }, placeholder: "文献标题" })),
      h("label", null, "作者", h("textarea", { value: form.authors, onChange: function (event) { field("authors", event.target.value); }, placeholder: "多个作者用分号分隔" })),
      h("div", { className: "wb-lib-modal-grid" }, h("label", null, "年份", h("input", { type: "number", min: 0, max: 9999, value: form.year, onChange: function (event) { field("year", event.target.value); } })), h("label", null, "条目类型", h("select", { value: form.item_type, onChange: function (event) { field("item_type", event.target.value); } }, h("option", { value: "journalArticle" }, "期刊文章"), h("option", { value: "conferencePaper" }, "会议论文"), h("option", { value: "book" }, "图书"), h("option", { value: "thesis" }, "学位论文"), h("option", { value: "report" }, "报告")))),
      h("label", null, "出版源", h("input", { value: form.venue, onChange: function (event) { field("venue", event.target.value); }, placeholder: "期刊、会议或出版社" })),
      h("label", null, "DOI", h("input", { value: form.doi, onChange: function (event) { field("doi", event.target.value); }, placeholder: "10.xxxx/xxxxx" })),
      h("footer", null, h("button", { type: "button", className: "wb-lib-secondary", onClick: props.onClose }, "取消"), h("button", { type: "submit", className: "wb-lib-primary", disabled: busy || !form.title.trim() }, busy && h(Spinner), " 添加条目"))));
  }

  function WorkbenchLibraryPage(props) {
    var workspace = props.project && props.project.id ? String(props.project.id) : "";
    var client = useMemo(function () { return workspace ? libraryApi(workspace) : null; }, [workspace]);
    var scopeState = useState({ type: "all" }); var scope = scopeState[0]; var setScope = scopeState[1];
    var queryState = useState(""); var query = queryState[0]; var setQuery = queryState[1];
    var debouncedState = useState(""); var debouncedQuery = debouncedState[0]; var setDebouncedQuery = debouncedState[1];
    var sortState = useState("updated_at"); var sort = sortState[0]; var setSort = sortState[1];
    var orderState = useState("desc"); var order = orderState[0]; var setOrder = orderState[1];
    var filterState = useState({ item_type: "", status: "", year: "" }); var filters = filterState[0]; var setFilters = filterState[1];
    var viewState = useState("table"); var view = viewState[0]; var setView = viewState[1];
    var dataState = useState({ items: [], total: 0, stats: {}, collections: [], tags: [] }); var data = dataState[0]; var setData = dataState[1];
    var loadingState = useState(false); var loading = loadingState[0]; var setLoading = loadingState[1];
    var loadingMoreState = useState(false); var loadingMore = loadingMoreState[0]; var setLoadingMore = loadingMoreState[1];
    var errorState = useState(""); var error = errorState[0]; var setError = errorState[1];
    var selectedState = useState(""); var selectedId = selectedState[0]; var setSelectedId = selectedState[1];
    var detailState = useState(null); var detail = detailState[0]; var setDetail = detailState[1];
    var detailLoadingState = useState(false); var detailLoading = detailLoadingState[0]; var setDetailLoading = detailLoadingState[1];
    var checkedState = useState([]); var checked = checkedState[0]; var setChecked = checkedState[1];
    var menuState = useState(""); var menu = menuState[0]; var setMenu = menuState[1];
    var workTabState = useState("info"); var workTab = workTabState[0]; var setWorkTab = workTabState[1];
    var rightTabState = useState("detail"); var rightTab = rightTabState[0]; var setRightTab = rightTabState[1];
    var rightOpenState = useState(true); var rightOpen = rightOpenState[0]; var setRightOpen = rightOpenState[1];
    var sidebarOpenState = useState(false); var sidebarOpen = sidebarOpenState[0]; var setSidebarOpen = sidebarOpenState[1];
    var manualState = useState(false); var manualOpen = manualState[0]; var setManualOpen = manualState[1];
    var uploadingState = useState(false); var uploading = uploadingState[0]; var setUploading = uploadingState[1];
    var citationState = useState({ style: "ieee", text: "", bibtex: "", citekey: "", loading: false, error: "" }); var citation = citationState[0]; var setCitation = citationState[1];
    var fileRef = useRef(null);
    var requestSeq = useRef(0);
    var detailSeq = useRef(0);

    useEffect(function () { var timer = setTimeout(function () { setDebouncedQuery(query.trim()); }, 240); return function () { clearTimeout(timer); }; }, [query]);

    function listParams() {
      var params = { q: debouncedQuery, sort: sort, order: order, limit: PAGE_SIZE, offset: 0 };
      if (filters.item_type) params.item_type = filters.item_type;
      if (filters.status) params.status = filters.status;
      if (filters.year) params.year = filters.year;
      if (scope.type === "collection") params.collection = scope.value;
      else if (scope.type === "tag") params.tag = scope.value;
      else if (scope.type === "starred") params.starred = "true";
      else if (scope.type === "trash") params.trash = "true";
      else if (scope.type === "unclassified") params.collection = "__unclassified__";
      else if (scope.type === "recent_added" || scope.type === "recent_read") params.status = scope.type;
      return params;
    }

    function reload(options) {
      options = options || {};
      if (!client || !workspace) return Promise.resolve();
      var seq = ++requestSeq.current;
      setLoading(true); setError("");
      var calls = [client.list(listParams())];
      if (!options.itemsOnly) calls.push(client.stats(), client.collections(), client.tags());
      return Promise.all(calls).then(function (values) {
        if (seq !== requestSeq.current) return;
        var list = values[0] || {};
        var nextItems = Array.isArray(list.items) ? list.items : [];
        setData(function (prev) { return {
          items: nextItems, total: list.total != null ? list.total : nextItems.length,
          stats: values.length > 1 ? (values[1] || {}) : prev.stats,
          collections: values.length > 1 ? ((values[2] && values[2].collections) || []) : prev.collections,
          tags: values.length > 1 ? ((values[3] && values[3].tags) || []) : prev.tags,
        }; });
        if (selectedId && !nextItems.some(function (item) { return String(item.id) === String(selectedId); })) {
          setSelectedId("");
          setDetail(null);
          setChecked(function (prev) { return prev.filter(function (id) { return nextItems.some(function (item) { return String(item.id) === String(id); }); }); });
        }
        setLoading(false);
      }).catch(function (err) { if (seq === requestSeq.current) { setError(String(err.message || err)); setLoading(false); } });
    }

    function loadMore() {
      if (!client || loadingMore || data.items.length >= data.total) return;
      var params = listParams(); params.offset = data.items.length;
      setLoadingMore(true);
      client.list(params).then(function (payload) {
        var items = Array.isArray(payload.items) ? payload.items : [];
        setData(function (prev) { return Object.assign({}, prev, { items: prev.items.concat(items), total: payload.total != null ? payload.total : prev.total }); });
        setLoadingMore(false);
      }).catch(function (err) { setLoadingMore(false); Toast(String(err.message || err), "error"); });
    }

    useEffect(function () {
      setScope({ type: "all" }); setSelectedId(""); setDetail(null); setChecked([]); setData({ items: [], total: 0, stats: {}, collections: [], tags: [] });
    }, [workspace]);
    useEffect(function () { if (props.active !== false) reload(); }, [client, workspace, debouncedQuery, scope.type, scope.value, sort, order, filters.item_type, filters.status, filters.year, props.active]);

    useEffect(function () {
      if (!client || !workspace || props.active === false || autoSyncChecked[workspace]) return;
      autoSyncChecked[workspace] = true;
      var alive = true;
      client.zoteroStatus().then(function (status) {
        if (!alive || !status || !status.auto_sync || !status.available) return;
        var sources = Array.isArray(status.sync_sources) ? status.sync_sources : [];
        var source = sources[0] || {};
        var config = source.config || source.config_json || {};
        return client.zoteroSync({
          library_id: String(source.provider_library_id || status.default_library_id || "0"),
          library_type: String(config.library_type || status.default_library_type || "user"),
          collection_key: String(source.collection_key || ""),
        }).then(function (result) {
          if (!alive) return;
          var changed = Number(result.created || 0) + Number(result.updated || 0) + Number(result.deleted || 0);
          if (changed) Toast("Zotero 自动同步完成：" + changed + " 项变更");
          reload();
        });
      }).catch(function () { /* Status and sync errors are already surfaced by explicit actions. */ });
      return function () { alive = false; };
    }, [client, workspace, props.active]);

    useEffect(function () {
      if (!client || !selectedId) { setDetail(null); return; }
      var seq = ++detailSeq.current; setDetailLoading(true);
      client.detail(selectedId).then(function (payload) { if (seq === detailSeq.current) { setDetail(payload.item || payload); setDetailLoading(false); } }, function (err) { if (seq === detailSeq.current) { setDetailLoading(false); Toast(String(err.message || err), "error"); } });
    }, [client, selectedId]);

    var listItem = useMemo(function () { return data.items.find(function (item) { return String(item.id) === String(selectedId); }) || null; }, [data.items, selectedId]);
    var selectedItem = detail ? Object.assign({}, listItem || {}, detail) : listItem;

    function select(id) { setSelectedId(String(id)); setWorkTab("info"); setRightOpen(true); }
    function replaceItem(item) {
      if (!item) return;
      setData(function (prev) { return Object.assign({}, prev, { items: prev.items.map(function (old) { return String(old.id) === String(item.id) ? Object.assign({}, old, item) : old; }) }); });
      if (String(item.id) === String(selectedId)) setDetail(function (old) { return Object.assign({}, old || {}, item); });
    }
    function updateSelected(value) {
      if (!client || !selectedId) return Promise.reject(new Error("未选择文献"));
      return client.update(selectedId, value).then(function (payload) { var item = payload.item || payload; replaceItem(item); Toast("文献已更新"); reload({ itemsOnly: true }); return item; });
    }
    function toggleStar(item) { client.update(item.id, { starred: !item.starred }).then(function (payload) { replaceItem(payload.item || payload); }, function (err) { Toast(String(err.message || err), "error"); }); }
    function addNote(value) { return client.addNote(selectedId, value).then(function () { Toast("笔记已添加"); return client.detail(selectedId).then(function (payload) { setDetail(payload.item || payload); }); }); }
    function removeSelected() {
      if (!selectedId) return;
      var run = function () { client.remove(selectedId).then(function () { Toast("文献已移至回收站"); setSelectedId(""); setDetail(null); reload(); }); };
      if (window.confirmModal) window.confirmModal({ title: "移至回收站？", body: "文献会保留在当前项目的回收站中。", confirmLabel: "移至回收站", danger: true }).then(function (ok) { if (ok) run(); }); else run();
    }
    function restore(item) { client.restore(item.id).then(function () { Toast("文献已恢复"); reload(); }); }

    function handleFiles(files) {
      if (!client || !files || !files.length) return;
      setUploading(true);
      client.upload(files).then(function (payload) { setUploading(false); if (fileRef.current) fileRef.current.value = ""; Toast("已导入 " + ((payload.items && payload.items.length) || 0) + " 个条目"); reload(); }, function () { setUploading(false); if (fileRef.current) fileRef.current.value = ""; });
    }
    function createItem(value) { return client.create(value).then(function (payload) { var item = payload.item || payload; Toast("文献条目已添加"); reload(); if (item.id) select(item.id); return item; }); }
    function exportItems() {
      if (!client) return;
      var params = listParams(); params.limit = 1000; params.offset = 0;
      client.list(params).then(function (first) {
        var items = Array.isArray(first.items) ? first.items.slice() : [];
        var total = Number(first.total || items.length);
        var requests = [];
        for (var offset = items.length; offset < total; offset += 1000) requests.push(client.list(Object.assign({}, params, { offset: offset })));
        return Promise.all(requests).then(function (pages) { pages.forEach(function (page) { items = items.concat(page.items || []); }); return items; });
      }).then(function (items) {
        var blob = new Blob([JSON.stringify(items, null, 2)], { type: "application/json" });
        var href = URL.createObjectURL(blob); var link = document.createElement("a"); link.href = href; link.download = "cyrene-library-" + workspace + ".json"; link.click(); setTimeout(function () { URL.revokeObjectURL(href); }, 0); Toast("已导出 " + items.length + " 篇文献");
      }).catch(function (err) { Toast(String(err.message || err), "error"); });
    }
    function toggleChecked(id) { setChecked(function (prev) { var key = String(id); return prev.indexOf(key) >= 0 ? prev.filter(function (v) { return v !== key; }) : prev.concat([key]); }); }
    function toggleAll() { setChecked(checked.length === data.items.length ? [] : data.items.map(function (item) { return String(item.id); })); }

    function loadCitation(style) {
      if (!client || !selectedId) return;
      var nextStyle = style || citation.style; setCitation({ style: nextStyle, text: "", bibtex: "", citekey: "", loading: true, error: "" });
      client.citation(selectedId, nextStyle).then(function (payload) { setCitation({ style: nextStyle, text: payload.citation || "", bibtex: payload.bibtex || "", citekey: payload.citekey || "", loading: false, error: "" }); }, function (err) { setCitation({ style: nextStyle, text: "", bibtex: "", citekey: "", loading: false, error: String(err.message || err) }); });
    }
    useEffect(function () { setCitation({ style: "ieee", text: "", bibtex: "", citekey: "", loading: false, error: "" }); if (client && selectedId) loadCitation("ieee"); }, [client, selectedId]);
    useEffect(function () { if (workTab === "citation" && selectedId && !citation.text && !citation.loading && !citation.error) loadCitation(citation.style); }, [workTab]);
    function copyCitation(format) {
      var value = format === "bibtex" ? citation.bibtex : citation.text;
      if (!value) return;
      navigator.clipboard.writeText(value).then(function () { Toast(format === "bibtex" ? "BibTeX 已复制" : "引用已复制"); });
    }

    var scopeTitle = scope.type === "all" ? "知识库" : scope.type === "unclassified" ? "未分类" : scope.type === "recent_added" ? "最近添加" : scope.type === "recent_read" ? "最近阅读" : scope.type === "starred" ? "星标文献" : scope.type === "trash" ? "回收站" : (scope.label || "文献库");
    var activeFilters = [filters.item_type, filters.status, filters.year].filter(Boolean).length;
    var citationProps = { style: citation.style, citation: citation.text, bibtex: citation.bibtex, citekey: citation.citekey, loading: citation.loading, error: citation.error, onStyle: loadCitation, onCopy: copyCitation, onRetry: function () { loadCitation(citation.style); } };

    if (!workspace) return h("section", { className: "wb-lib-page no-project" }, h(StatePanel, { title: "请先选择项目", body: "每个项目拥有相互隔离的文献库。" }));

    return h("section", { className: "wb-lib-page" },
      h("input", { ref: fileRef, className: "wb-lib-file-input", type: "file", multiple: true, accept: ".pdf,.ris,.bib,.bibtex,.json,application/pdf", onChange: function (event) { handleFiles(event.target.files); } }),
      h(LibrarySidebar, { open: sidebarOpen, onClose: function () { setSidebarOpen(false); }, onBack: props.onBack, stats: data.stats, collections: data.collections, tags: data.tags, scope: scope, onScope: function (next) { setScope(next); setSidebarOpen(false); setSelectedId(""); } }),
      h("main", { className: "wb-lib-main" },
        h("header", { className: "wb-lib-main-head" },
          h("button", { type: "button", className: "wb-lib-sidebar-toggle", onClick: function () { setSidebarOpen(true); }, title: "打开文献分类" }, icon("panel", 18)),
          h("div", { className: "wb-lib-heading" }, scope.type !== "all" && h("h2", null, scopeTitle), h("span", null, "共 " + Number(data.total || 0).toLocaleString() + " 个知识")),
          h("div", { className: "wb-lib-head-actions" },
            h("div", { className: "wb-lib-menu-wrap" }, h("button", { type: "button", className: "wb-lib-primary", onClick: function () { setMenu(menu === "add" ? "" : "add"); } }, icon("plus", 16), " 添加条目"), h(Dropdown, { open: menu === "add", onClose: function () { setMenu(""); } }, h("button", { type: "button", onClick: function () { setMenu(""); setManualOpen(true); } }, icon("note", 16), h("span", null, h("b", null, "手动添加"), h("small", null, "创建书目信息"))), h("button", { type: "button", onClick: function () { setMenu(""); fileRef.current && fileRef.current.click(); } }, icon("upload", 16), h("span", null, h("b", null, "上传文件"), h("small", null, "PDF、RIS、BibTeX 或 JSON"))))),
            h("button", { type: "button", className: "wb-lib-head-button", disabled: uploading, onClick: function () { fileRef.current && fileRef.current.click(); } }, uploading ? h(Spinner) : icon("upload", 16), uploading ? "导入中" : "导入"),
            h("button", { type: "button", className: "wb-lib-head-button", onClick: exportItems, disabled: !data.items.length }, icon("download", 16), "导出"))),
        h("div", { className: "wb-lib-toolbar" },
          h("label", { className: "wb-lib-search" }, icon("search", 17), h("input", { value: query, onChange: function (event) { setQuery(event.target.value); }, placeholder: "在当前项目的知识库中搜索…" }), query && h("button", { type: "button", onClick: function () { setQuery(""); }, title: "清空搜索" }, icon("close", 13))),
          h("div", { className: "wb-lib-tools" },
            h("div", { className: "wb-lib-menu-wrap" }, h("button", { type: "button", className: "wb-lib-tool" + (activeFilters ? " active" : ""), onClick: function () { setMenu(menu === "filter" ? "" : "filter"); } }, icon("filter", 16), "筛选", activeFilters ? h("small", null, activeFilters) : null), h(Dropdown, { open: menu === "filter", onClose: function () { setMenu(""); }, className: "filter" },
              h("label", null, "条目类型", h("select", { value: filters.item_type, onChange: function (event) { setFilters(Object.assign({}, filters, { item_type: event.target.value })); } }, h("option", { value: "" }, "全部类型"), h("option", { value: "journalArticle" }, "期刊文章"), h("option", { value: "conferencePaper" }, "会议论文"), h("option", { value: "book" }, "图书"), h("option", { value: "thesis" }, "学位论文"), h("option", { value: "report" }, "报告"))),
              h("label", null, "阅读状态", h("select", { value: filters.status, onChange: function (event) { setFilters(Object.assign({}, filters, { status: event.target.value })); } }, h("option", { value: "" }, "全部状态"), h("option", { value: "unread" }, "待读"), h("option", { value: "reading" }, "阅读中"), h("option", { value: "read" }, "已读"))),
              h("label", null, "年份", h("input", { type: "number", value: filters.year, onChange: function (event) { setFilters(Object.assign({}, filters, { year: event.target.value })); }, placeholder: "例如 2025" })),
              activeFilters > 0 && h("button", { type: "button", className: "wb-lib-clear-filter", onClick: function () { setFilters({ item_type: "", status: "", year: "" }); } }, "清除筛选"))),
            h("div", { className: "wb-lib-menu-wrap" }, h("button", { type: "button", className: "wb-lib-tool", onClick: function () { setMenu(menu === "sort" ? "" : "sort"); } }, icon("sort", 16), "排序"), h(Dropdown, { open: menu === "sort", onClose: function () { setMenu(""); }, className: "sort" }, [{ id: "updated_at", label: "最近更新" }, { id: "created_at", label: "最近添加" }, { id: "title", label: "标题" }, { id: "year", label: "年份" }].map(function (option) { return h("button", { key: option.id, type: "button", className: sort === option.id ? "selected" : "", onClick: function () { setSort(option.id); setMenu(""); } }, option.label, sort === option.id && icon("check", 14)); }), h("button", { type: "button", onClick: function () { setOrder(order === "desc" ? "asc" : "desc"); setMenu(""); } }, order === "desc" ? "降序" : "升序"))),
            h("div", { className: "wb-lib-view-toggle" }, h("button", { type: "button", className: view === "table" ? "active" : "", onClick: function () { setView("table"); }, title: "表格视图" }, icon("list", 16)), h("button", { type: "button", className: view === "grid" ? "active" : "", onClick: function () { setView("grid"); }, title: "卡片视图" }, icon("grid", 16))),
            !rightOpen && selectedItem && h("button", { type: "button", className: "wb-lib-tool compact", onClick: function () { setRightOpen(true); }, title: "打开详情" }, icon("panel", 16)))),
        checked.length > 0 && h("div", { className: "wb-lib-batch" }, h("b", null, "已选择 " + checked.length + " 项"), h("button", { type: "button", onClick: function () { setChecked([]); } }, "取消选择")),
        error && h("div", { className: "wb-lib-error" }, h("span", null, error), h("button", { type: "button", onClick: function () { reload(); } }, icon("restore", 14), " 重试")),
        h("section", { className: "wb-lib-results" + (selectedItem ? " with-workspace" : "") },
          loading && !data.items.length ? h(StatePanel, { loading: true, title: "正在加载文献库…" }) : !data.items.length ? h(StatePanel, { title: query || activeFilters || scope.type !== "all" ? "没有匹配的文献" : "这个项目还没有文献", body: query || activeFilters || scope.type !== "all" ? "尝试调整搜索、分类或筛选条件。" : "导入 PDF、RIS 或 BibTeX；Zotero 导入请前往设置。", action: query || activeFilters || scope.type !== "all" ? function () { setQuery(""); setFilters({ item_type: "", status: "", year: "" }); setScope({ type: "all" }); } : function () { fileRef.current && fileRef.current.click(); }, actionLabel: query || activeFilters || scope.type !== "all" ? "清除条件" : "导入第一篇文献" }) :
            view === "table" ? h("div", { className: "wb-lib-table", role: "table" }, h(TableHead, { allSelected: data.items.length > 0 && checked.length === data.items.length, onToggleAll: toggleAll }), h("div", { className: "wb-lib-table-body" }, data.items.map(function (item) { return h(LibraryRow, { key: item.id, item: item, active: String(item.id) === String(selectedId), checked: checked.indexOf(String(item.id)) >= 0, trash: scope.type === "trash", onSelect: select, onToggle: toggleChecked, onStar: toggleStar, onRestore: restore }); }))) :
              h("div", { className: "wb-lib-card-grid" }, data.items.map(function (item) { return h(LibraryCard, { key: item.id, item: item, active: String(item.id) === String(selectedId), onSelect: select, onStar: toggleStar }); })),
          data.items.length < data.total && h("button", { type: "button", className: "wb-lib-load-more", disabled: loadingMore, onClick: loadMore }, loadingMore ? h(Spinner) : null, loadingMore ? "加载中…" : "加载更多（" + data.items.length + " / " + data.total + "）"),
          loading && data.items.length > 0 && h("div", { className: "wb-lib-loading-bar" }, h(Spinner), " 正在更新…")),
        selectedItem && h(ItemWorkspace, { item: selectedItem, loading: detailLoading, tab: workTab, onTab: setWorkTab, onUpdate: updateSelected, onAddNote: addNote, rawUrl: client.rawUrl(selectedId), citationProps: citationProps })),
      h(RightPanel, { item: selectedItem, open: rightOpen, onClose: function () { setRightOpen(false); }, tab: rightTab, onTab: setRightTab, rawUrl: selectedId ? client.rawUrl(selectedId) : "", citation: citation.text, bibtex: citation.bibtex, citationLoading: citation.loading, onCopyCitation: copyCitation, onUpdate: updateSelected, onDelete: scope.type !== "trash" ? removeSelected : null }),
      manualOpen && h(ManualItemModal, { onClose: function () { setManualOpen(false); }, onSave: createItem }));
  }

  window.WorkbenchLibraryPage = WorkbenchLibraryPage;
})();
