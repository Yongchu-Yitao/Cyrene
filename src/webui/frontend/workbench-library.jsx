import { workbenchServices } from "./shared/runtime/services.jsx"
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

  function L(key, fallback, params) {
    return workbenchServices.i18n().t(key, params || null, fallback);
  }

  function formatNumber(value, options) {
    return workbenchServices.i18n().formatNumber(value, options);
  }

  var PAGE_SIZE = 120;
  // Auto-sync is an app-session convenience, not a polling loop. Remember the
  // projects already checked so navigation and hidden-surface remounts cannot
  // repeatedly contact Zotero or start overlapping imports.
  var autoSyncChecked = {};

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
      vector: ["M12 3v18M3 12h18", "m5 7 4-4 4 4", "m6 13 4 4 4-4"],
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
      image: [h("rect", { key: "r", x: 3, y: 3, width: 18, height: 18, rx: 2.5 }), h("circle", { key: "c", cx: 8.5, cy: 9, r: 1.6 }), "m21 16-5-5L5 21"],
      audio: ["M9 18V5l11-2v13", h("circle", { key: "a", cx: 6, cy: 18, r: 3 }), h("circle", { key: "b", cx: 17, cy: 16, r: 3 })],
      video: [h("rect", { key: "r", x: 3, y: 5, width: 18, height: 14, rx: 2.5 }), h("path", { key: "p", d: "m10 9 5 3-5 3Z" })],
      sheet: [h("rect", { key: "r", x: 3, y: 4, width: 18, height: 16, rx: 2 }), "M3 9h18M3 14h18M9 4v16M15 4v16"],
      slide: [h("rect", { key: "r", x: 3, y: 4, width: 18, height: 13, rx: 2 }), "M7.5 13V11M12 13V8.5M16.5 13v-3M12 17v4M8.5 21h7"],
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
    return workbenchServices.i18n().formatDate(
      d,
      withTime
        ? { dateStyle: "medium", timeStyle: "short" }
        : { dateStyle: "medium" }
    );
  }

  function formatBytes(value) {
    var n = Number(value || 0);
    if (!n) return "—";
    var units = ["B", "KB", "MB", "GB"];
    var i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
    return formatNumber(n, i ? { maximumFractionDigits: 1 } : undefined) + " " + units[i];
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
    return names[0] + L("library.moreAuthors", " and {count} more", { count: names.length - 1 });
  }

  function itemTags(item) {
    return (Array.isArray(item && item.tags) ? item.tags : []).map(function (tag) {
      return typeof tag === "string" ? tag : (tag && (tag.name || tag.tag)) || "";
    }).filter(Boolean);
  }

  function itemTypeLabel(value) {
    var raw = String(value || "document");
    return L("library.itemType." + raw, {
      document: "Document",
      journalArticle: "Journal article",
      conferencePaper: "Conference paper",
      book: "Book",
      bookSection: "Book section",
      thesis: "Thesis",
      report: "Report",
      webpage: "Web page",
    }[raw] || "Document");
  }

  function collectionName(collection) {
    return collection && (collection.name || collection.title || (collection.data && collection.data.name)) || L("library.untitledCollection", "Untitled collection");
  }

  function itemTitle(item) {
    return String(item && item.title || L("library.untitledItem", "Untitled knowledge")).trim();
  }

  function itemKind(item) {
    return itemFileType(item);
  }

  function primaryAttachment(item) {
    var attachments = item && Array.isArray(item.attachments) ? item.attachments : [];
    return attachments.find(function (attachment) {
      return String(attachment && attachment.content_type || "").toLowerCase().split(";")[0] === "application/pdf";
    }) || attachments[0] || null;
  }

  function attachmentPreviewType(item) {
    var attachment = primaryAttachment(item);
    var contentType = String(attachment && attachment.content_type || item && item.content_type || "").toLowerCase().split(";")[0];
    var filename = String(attachment && (attachment.filename || attachment.name) || item && (item.filename || item.attachment_name || item.name) || "").toLowerCase();
    if (contentType.indexOf("image/") === 0 || /\.(avif|bmp|gif|jpe?g|png|webp)$/i.test(filename)) return "image";
    if (contentType.indexOf("video/") === 0 || /\.(m4v|mov|mp4|ogv|webm)$/i.test(filename)) return "video";
    if (contentType.indexOf("audio/") === 0 || /\.(aac|flac|m4a|mp3|oga|ogg|wav|weba)$/i.test(filename)) return "audio";
    if (contentType === "application/pdf" || /\.pdf$/i.test(filename)) return "pdf";
    if (contentType.indexOf("text/") === 0 || /\.(csv|html?|json|log|md|rtf|text|toml|tsv|txt|xml|ya?ml)$/i.test(filename)) return "text";
    return attachment ? "file" : "text";
  }

  function itemFileType(item) {
    var attachment = primaryAttachment(item);
    var contentType = String(attachment && attachment.content_type || item && item.content_type || "").toLowerCase().split(";")[0];
    var filename = String(attachment && (attachment.filename || attachment.name) || item && (item.filename || item.attachment_name || item.name) || "").toLowerCase();
    if (contentType === "application/pdf" || /\.pdf$/i.test(filename)) return "pdf";
    if (contentType.indexOf("image/") === 0 || /\.(avif|bmp|gif|jpe?g|png|webp)$/i.test(filename)) return "image";
    if (contentType.indexOf("audio/") === 0 || /\.(aac|flac|m4a|mp3|oga|ogg|wav|weba)$/i.test(filename)) return "audio";
    if (contentType.indexOf("video/") === 0 || /\.(m4v|mov|mp4|ogv|webm)$/i.test(filename)) return "video";
    if (/spreadsheet|ms-excel|text\/csv|tab-separated/.test(contentType) || /\.(csv|numbers|tsv|xls|xlsm|xlsx)$/i.test(filename)) return "spreadsheet";
    if (/powerpoint|presentation/.test(contentType) || /\.(key|odp|ppt|pptx)$/i.test(filename)) return "presentation";
    if (contentType === "text/uri-list" || /\.(link|url|webloc)$/i.test(filename) || String(item && item.item_type || "") === "webpage") return "link";
    if (/^text\//.test(contentType) || /msword|wordprocessing|opendocument\.text|rtf/.test(contentType) || /\.(doc|docx|html?|json|log|md|rtf|txt|xml|ya?ml)$/i.test(filename)) return "document";
    return attachment || filename ? "other" : "document";
  }

  var FILE_TYPE_LABELS = {
    pdf: "PDF", document: "Documents and text", spreadsheet: "Spreadsheets",
    presentation: "Presentations", image: "Images", audio: "Audio",
    video: "Video", link: "Web pages and links", other: "Other files",
  };

  function itemFileTypeLabel(item) {
    var raw = itemFileType(item);
    return L("library.fileType." + raw, FILE_TYPE_LABELS[raw] || "File");
  }

  var LibraryFileVisual = {
    visualKind: function (item) {
      return {
        document: "doc",
        spreadsheet: "sheet",
        presentation: "slide",
        other: "file",
      }[itemFileType(item)] || itemFileType(item);
    },
    toneForKind: function (kind) {
      return {
        pdf: "red", doc: "blue", sheet: "green", slide: "orange",
        image: "purple", link: "cyan", audio: "amber", video: "red",
      }[kind] || "slate";
    },
    tone: function (item) {
      return LibraryFileVisual.toneForKind(LibraryFileVisual.visualKind(item));
    },
    iconForKind: function (kind) {
      return icon({ doc: "file", sheet: "sheet", slide: "slide" }[kind] || kind, 20);
    },
    icon: function (item) {
      return LibraryFileVisual.iconForKind(LibraryFileVisual.visualKind(item));
    },
  };

  function cardDescription(item) {
    var abstract = String(item && item.abstract || "").trim();
    if (abstract) return abstract;
    var author = authorText(item);
    var venue = String(item && (item.venue || item.publication_title) || "").trim();
    if (author !== "—") return [author, venue].filter(Boolean).join(" · ");
    var filename = String(item && (item.attachment_name || item.filename) || "").trim();
    return [itemFileTypeLabel(item), filename && filename !== itemTitle(item) ? filename : ""].filter(Boolean).join(" · ");
  }

  function renderMarkdownHtml(content) {
    if (!window.marked || !window.DOMPurify) return "";
    return workbenchServices.markdown().render(content, {
      sanitizeOptions: {
        ADD_ATTR: ["data-line", "data-language"],
      },
      errorValue: "",
    });
  }

  function renderSafeHtmlDocument(content) {
    var source = String(content || "").replace(/<meta\b[^>]*http-equiv\s*=\s*["']?refresh["']?[^>]*>/gi, "");
    if (!window.DOMPurify) return source;
    return workbenchServices.markdown().sanitizeHtml(source, {
        WHOLE_DOCUMENT: true,
        FORBID_TAGS: ["script", "iframe", "object", "embed", "form"],
        FORBID_ATTR: ["onerror", "onload", "onclick", "onfocus", "onmouseenter"],
      });
  }

  function hasAttachment(item) {
    return !!(item && ((Array.isArray(item.attachments) && item.attachments.length) || Number(item.attachment_count || 0)));
  }

  function libraryResourcePayload(item, workspace, rawUrl) {
    var itemId = String(item && item.id || "");
    var title = itemTitle(item);
    var ownerSessionId = "library:" + String(workspace || "");
    if (!hasAttachment(item)) {
      var details = [
        "# " + title,
        String(item && item.abstract || "").trim(),
        authorText(item) !== "—" ? "**Author:** " + authorText(item) : "",
        item && item.year ? "**Year:** " + item.year : "",
        String(item && (item.url || item.doi) || "").trim(),
      ].filter(Boolean).join("\n\n");
      return {
        kind: "snippet",
        sourceKind: "library",
        libraryItemId: itemId,
        ownerSessionId: ownerSessionId,
        ownerProjectId: String(workspace || ""),
        stableRef: "library:" + String(workspace || "") + ":" + itemId,
        title: title,
        text: details,
      };
    }
    var name = String(item && (item.attachment_name || item.filename) || title).trim() || title;
    var file = {
      id: "library:" + String(workspace || "") + ":" + itemId,
      name: name,
      content_type: String(item && item.content_type || "application/octet-stream"),
      size: Number(item && item.attachment_size || 0),
      kind: itemFileType(item),
      url: String(rawUrl || ""),
      sourceKind: "library",
      libraryItemId: itemId,
      ownerProjectId: String(workspace || ""),
    };
    return {
      kind: "file",
      sourceKind: "library",
      libraryItemId: itemId,
      ownerSessionId: ownerSessionId,
      ownerProjectId: String(workspace || ""),
      stableRef: "library:" + String(workspace || "") + ":" + itemId,
      title: name,
      name: name,
      url: file.url,
      content_type: file.content_type,
      size: file.size,
      file: file,
    };
  }

  function requestJson(url, options) {
    return workbenchServices.api().json(url, options || {});
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
      createCollection: function (value) { return json("/collections", body("POST", value)); },
      updateCollection: function (id, value) { return json("/collections/" + encodeURIComponent(id), body("PATCH", value)); },
      deleteCollection: function (id) { return json("/collections/" + encodeURIComponent(id), { method: "DELETE" }); },
      tags: function () { return json("/tags", { toast: false }); },
      detail: function (id) { return json("/items/" + encodeURIComponent(id), { toast: false }); },
      create: function (value) { return json("/items", body("POST", value)); },
      update: function (id, value) { return json("/items/" + encodeURIComponent(id), body("PATCH", value)); },
      remove: function (id) { return json("/items/" + encodeURIComponent(id), { method: "DELETE" }); },
      removeMany: function (ids, permanent) { return json("/items/batch-delete", body("POST", { item_ids: ids, permanent: !!permanent })); },
      restore: function (id) { return json("/items/" + encodeURIComponent(id) + "/restore", { method: "POST" }); },
      addNote: function (id, value) { return json("/items/" + encodeURIComponent(id) + "/notes", body("POST", value)); },
      upload: function (files) {
        var form = new FormData();
        Array.prototype.forEach.call(files || [], function (file) { form.append("files", file); });
        return json("/upload", { method: "POST", body: form, timeout: 0 });
      },
      zoteroStatus: function () { return json("/zotero/status", { toast: false }); },
      zoteroSync: function (value) { return json("/zotero/sync", Object.assign(body("POST", value), { timeout: 0 })); },
      embeddingStatus: function () { return requestJson("/api/workbench/library/embedding/status?workspace=" + ws, { toast: false }); },
      vectorizeAll: function () { return requestJson("/api/workbench/library/reembed?workspace=" + ws, { method: "POST" }); },
      citation: function (id, style) { return json("/items/" + encodeURIComponent(id) + "/citation", { toast: false }, { style: style || "ieee" }); },
      rawUrl: function (id) { return root + "/items/" + encodeURIComponent(id) + "/raw?workspace=" + ws; },
    };
  }

  function PdfMark(props) {
    var kind = itemFileType(props.item);
    var iconName = {
      image: "image", audio: "audio", video: "video", spreadsheet: "sheet",
      presentation: "slide", link: "link",
    }[kind] || "file";
    return h("span", { className: "wb-lib-filemark " + kind + (props.large ? " large" : ""), "aria-hidden": "true" },
      icon(iconName, props.large ? 22 : 17),
      kind === "pdf" ? h("small", null, "PDF") : null);
  }

  function Spinner() { return h("span", { className: "wb-lib-spinner", "aria-hidden": "true" }); }

  function Toast(message, type) {
    if (workbenchServices.feedback().showToast) workbenchServices.feedback().showToast(message, type || "success");
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
      props.count != null && h("span", { className: "wb-lib-side-count" }, formatNumber(props.count || 0)));
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
    function sectionHeading(id, label, action) {
      var expanded = !collapsed[id];
      return h("h2", null,
        h("button", {
          type: "button",
          className: "wb-lib-side-section-toggle",
          onClick: function () { toggleSection(id); },
          "aria-expanded": expanded,
        },
          h("span", { className: "wb-lib-side-caret" + (expanded ? " open" : "") }, icon("chevron", 13)),
          h("span", null, label)),
        action && h("button", {
          type: "button",
          className: "wb-lib-side-section-action",
          onClick: action,
          title: L("library.newCollection", "New collection"),
          "aria-label": L("library.newCollection", "New collection"),
        }, icon("plus", 15)));
    }
    var base = [
      { id: "all", label: L("library.all", "All knowledge"), icon: icon("book"), count: stats.total },
      { id: "unclassified", label: L("library.unclassified", "Untagged"), icon: icon("file"), count: stats.unclassified },
      { id: "recent_added", label: L("library.recentAdded", "Recently added"), icon: icon("clock"), count: stats.recent_added },
      { id: "recent_read", label: L("library.recentRead", "Recently read"), icon: icon("clock"), count: stats.recent_read },
      { id: "starred", label: L("library.starred", "Starred"), icon: icon("star"), count: stats.starred },
      { id: "trash", label: L("library.trash", "Trash"), icon: icon("trash"), count: stats.trash },
    ];
    return h("aside", { className: "wb-lib-sidebar workbench-integrated-rail" + (props.sidebarCollapsed ? " is-collapsed" : "") },
      h("div", { className: "wb-lib-sidebar-head workbench-integrated-rail-head" },
        h("h1", null, L("library.title", "Knowledge base")), props.collapseControl),
      h("div", { className: "wb-lib-side-scroll workbench-integrated-rail-body" },
        h("section", { className: "wb-lib-side-section" },
          sectionHeading("library", L("library.myLibrary", "My knowledge base")),
          !collapsed.library && base.map(function (row) {
            return h(SidebarRow, { key: row.id, label: row.label, icon: row.icon, count: row.count, active: props.scope.type === row.id, onClick: function () { props.onScope({ type: row.id }); } });
          })),
        h("section", { className: "wb-lib-side-section wb-lib-collections-section", "data-tour": "knowledge_collections" },
          sectionHeading("collections", L("library.myCollections", "My collections"), props.onCreateCollection),
          !collapsed.collections && (props.collections.length ? props.collections.map(function (collection, index) {
            var id = String(collection.id || collection.key || index);
            return h(SidebarRow, {
              key: id, label: collectionName(collection), count: collection.count, active: props.scope.type === "collection" && String(props.scope.value) === id,
              icon: h("span", { className: "wb-lib-folder-icon", style: { color: collection.color || ["#e35d9c", "#f29f3f", "#47b7c5", "#d5b42e", "#8d68e8"][index % 5] } }, icon("folder", 16)),
              onClick: function () { props.onScope({ type: "collection", value: id, label: collectionName(collection) }); },
            });
          }) : h("p", { className: "wb-lib-side-empty" }, L("library.noCollections", "No collections yet"))),
        h("section", { className: "wb-lib-side-section wb-lib-tag-cloud", "data-tour": "knowledge_tags" },
          sectionHeading("tags", L("library.tagCloud", "Tag cloud")),
          !collapsed.tags && (props.tags.length ? h("div", { className: "wb-lib-cloud" }, props.tags.map(function (tag) {
            var name = typeof tag === "string" ? tag : tag.name;
            return h("button", { key: name, type: "button", className: props.scope.type === "tag" && props.scope.value === name ? "active" : "", onClick: function () { props.onScope({ type: "tag", value: name, label: name }); } }, name, h("span", null, typeof tag === "string" ? "" : tag.count));
          })) : h("p", { className: "wb-lib-side-empty" }, L("library.noTags", "No tags yet")))))),
      props.moduleDock);
  }

  function TableHead(props) {
    return h("div", { className: "wb-lib-table-head wb-lib-table-grid", role: "row" },
      h("label", { className: "wb-lib-check" }, h("input", { type: "checkbox", checked: props.allSelected, onChange: props.onToggleAll, "aria-label": L("library.selectAll", "Select all knowledge") }), h("span")),
      h("span", { className: "wb-lib-title-head" }, L("library.column.title", "Title")), h("span", null, L("library.column.author", "Author")), h("span", null, L("library.column.year", "Year")), h("span", null, L("library.column.source", "Source")), h("span", null, L("library.column.added", "Added")), h("span", null, L("library.column.tags", "Tags")));
  }

  function LibraryRow(props) {
    var item = props.item;
    var tags = itemTags(item);
    var isTrash = props.trash;
    return h("div", {
      className: "wb-lib-row wb-lib-table-grid" + (props.active ? " active" : ""), role: "row", tabIndex: 0,
      "data-cyrene-context-menu": "true",
      draggable: !!props.onDragStart,
      onDragStart: function (event) { if (props.onDragStart) props.onDragStart(event, item); },
      onDragEnd: props.onDragEnd,
      onClick: function () { props.onSelect(item.id); },
      onContextMenu: function (event) {
        event.preventDefault();
        event.stopPropagation();
        props.onContextMenu(item, event);
      },
      onKeyDown: function (event) { if (event.key === "Enter") props.onSelect(item.id); },
    },
      h(StopClick, null, h("label", { className: "wb-lib-check" }, h("input", { type: "checkbox", checked: props.checked, onChange: function () { props.onToggle(item.id); }, "aria-label": L("library.selectItem", "Select {name}", { name: itemTitle(item) }) }), h("span"))),
      h("div", { className: "wb-lib-title-cell" },
        h(StopClick, null, h("button", { type: "button", className: "wb-lib-star" + (item.starred ? " active" : ""), onClick: function () { props.onStar(item); }, title: item.starred ? L("library.removeStar", "Remove star") : L("library.addStar", "Add star") }, icon("star", 16))),
        h(PdfMark, { item: item }),
        h("span", { className: "wb-lib-title-text", title: itemTitle(item) }, itemTitle(item))),
      h("span", { className: "wb-lib-truncate", title: authorText(item, true) }, authorText(item)),
      h("span", null, item.year || (item.date_text && String(item.date_text).slice(0, 4)) || "—"),
      h("span", { className: "wb-lib-truncate", title: item.venue || item.publication_title || "" }, item.venue || item.publication_title || "—"),
      h("span", null, formatDate(item.added_at || item.created_at)),
      h("div", { className: "wb-lib-row-tags" }, tags.slice(0, 2).map(function (tag) { return h("span", { key: tag }, tag); }), tags.length > 2 && h("small", null, "+" + (tags.length - 2)),
        isTrash && h(StopClick, null, h("button", { type: "button", className: "wb-lib-row-action", onClick: function () { props.onRestore(item); }, title: L("library.restore", "Restore") }, icon("restore", 14)))));
  }

  function LibraryCard(props) {
    var item = props.item;
    var tags = itemTags(item);
    return h("article", {
      className: "wb-lib-card" + (props.active ? " active" : ""),
      "data-cyrene-context-menu": "true",
      draggable: !!props.onDragStart,
      onDragStart: function (event) { if (props.onDragStart) props.onDragStart(event, item); },
      onDragEnd: props.onDragEnd,
      onClick: function () { props.onSelect(item.id); },
      onContextMenu: function (event) {
        event.preventDefault();
        event.stopPropagation();
        props.onContextMenu(item, event);
      },
      onKeyDown: function (event) { if (event.key === "Enter") props.onSelect(item.id); },
      tabIndex: 0,
    },
      h(PdfMark, { item: item, large: true }),
      h("div", { className: "wb-lib-card-body" },
        h("div", { className: "wb-lib-card-title-row" },
          h("h3", { title: itemTitle(item) }, itemTitle(item)),
          h("button", {
            type: "button",
            className: "wb-lib-star" + (item.starred ? " active" : ""),
            onClick: function (event) { event.stopPropagation(); props.onStar(item); },
            title: item.starred ? L("library.removeStar", "Remove star") : L("library.addStar", "Add star"),
          }, icon("star", 17)),
          h(StopClick, null, h("label", { className: "wb-lib-check wb-lib-card-check" },
            h("input", {
              type: "checkbox",
              checked: props.checked,
              onChange: function () { props.onToggle(item.id); },
              "aria-label": L("library.selectItem", "Select {name}", { name: itemTitle(item) }),
            }),
            h("span")))),
        h("p", { className: "wb-lib-card-description" }, cardDescription(item)),
        tags.length > 0 && h("div", { className: "wb-lib-row-tags" },
          tags.slice(0, 4).map(function (tag) { return h("span", { key: tag }, tag); }),
          tags.length > 4 && h("small", null, "+" + (tags.length - 4))),
        h("div", { className: "wb-lib-card-foot" },
          h("span", null, itemFileTypeLabel(item) + (item.attachment_size ? " · " + formatBytes(item.attachment_size) : "")),
          h("span", null, L("library.updatedAt", "Updated {date}", { date: formatDate(item.updated_at || item.created_at) })))));
  }

  function StatePanel(props) {
    return h("div", { className: "wb-lib-state " + (props.kind || "") }, props.loading ? h(Spinner) : icon(props.kind === "error" ? "restore" : "book", 42),
      h("h3", null, props.title), props.body && h("p", null, props.body),
      props.action && h("button", { type: "button", className: "wb-lib-state-action", onClick: props.action },
        props.actionIcon && icon(props.actionIcon, 15), h("span", null, props.actionLabel)));
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
    var statusOptions = [{ id: "unread", label: L("library.status.unread", "Unread") }, { id: "reading", label: L("library.status.reading", "Reading") }, { id: "read", label: L("library.status.read", "Read") }];
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
      h("div", { className: "wb-lib-paper-summary", role: "region", tabIndex: 0, "aria-label": L("library.itemInfo", "Item information") },
        h("div", { className: "wb-lib-paper-heading" }, h(PdfMark, { item: item, large: true }), h("div", null, h("h3", null, itemTitle(item)), h("div", { className: "wb-lib-paper-sub" }, authorText(item, true)))),
        h("div", { className: "wb-lib-paper-actions" },
          hasAttachment(item) ? h("a", { className: "wb-lib-secondary", href: props.rawUrl, target: "_blank", rel: "noreferrer" }, L("library.openFile", "Open file"), icon("chevron", 13)) : h("button", { type: "button", className: "wb-lib-secondary", disabled: true }, L("library.noAttachment", "No attachment")),
          h("select", { value: item.reading_status || "unread", onChange: function (event) { props.onUpdate({ reading_status: event.target.value }); }, "aria-label": L("library.readingStatus", "Reading status") }, statusOptions.map(function (option) { return h("option", { key: option.id, value: option.id }, option.label); }))),
        editing ? h("form", { className: "wb-lib-paper-editor", onSubmit: saveMetadata },
          h("label", null, h("span", null, L("library.itemType", "Item type")), h("select", { value: form.item_type, onChange: function (event) { field("item_type", event.target.value); } }, itemTypes.map(function (type) { return h("option", { key: type, value: type }, itemTypeLabel(type)); }))),
          editField(L("library.year", "Year"), "year", "number"),
          editField(L("library.titleField", "Title"), "title", "text", true),
          editField(L("library.author", "Author"), "authors", "text", true),
          editField(L("library.sourceField", "Source"), "venue", "text", true),
          editField(L("library.volume", "Volume"), "volume"), editField(L("library.issue", "Issue"), "issue"), editField(L("library.pages", "Pages"), "pages"),
          editField("DOI", "doi", "text", true), editField("ISBN", "isbn"), editField(L("library.language", "Language"), "language"),
          h("footer", null,
            h("button", { type: "button", className: "wb-lib-secondary", disabled: saving, onClick: function () { setEditing(false); setForm(draftFromItem(item)); } }, L("common.cancel", "Cancel")),
            h("button", { type: "submit", className: "wb-lib-primary", disabled: saving || !form.title.trim() }, saving ? h(Spinner) : null, saving ? L("common.saving", "Saving…") : L("common.save", "Save")))):
          h("dl", { className: "wb-lib-paper-meta" },
            h(MetaLine, { label: L("library.itemType", "Item type"), value: itemTypeLabel(item.item_type || item.type), showEmpty: true }), h(MetaLine, { label: L("library.titleField", "Title"), value: itemTitle(item), showEmpty: true }),
            h(MetaLine, { label: L("library.author", "Author"), value: authorText(item, true), showEmpty: true }), h(MetaLine, { label: L("library.sourceField", "Source"), value: item.venue || item.publication_title, showEmpty: true }),
            h(MetaLine, { label: L("library.volume", "Volume"), value: item.volume, showEmpty: true }), h(MetaLine, { label: L("library.issue", "Issue"), value: item.issue, showEmpty: true }), h(MetaLine, { label: L("library.pages", "Pages"), value: item.pages, showEmpty: true }),
            h(MetaLine, { label: L("library.year", "Year"), value: item.year, showEmpty: true }), h(MetaLine, { label: "DOI", value: item.doi, showEmpty: true }), h(MetaLine, { label: "ISBN", value: item.isbn, showEmpty: true }),
            h(MetaLine, { label: L("library.language", "Language"), value: item.language, showEmpty: true }), h(MetaLine, { label: L("library.addedAt", "Added"), value: formatDate(item.added_at || item.created_at, true), showEmpty: true }),
            h(MetaLine, { label: L("library.updated", "Updated"), value: formatDate(item.updated_at, true), showEmpty: true }),
            h(MetaLine, { label: L("library.attachment", "Attachment"), value: attachment && (attachment.filename || attachment.name), showEmpty: true }))),
      h("div", { className: "wb-lib-work-pane" },
        props.navigation,
        props.tab === "info" ? h("div", { className: "wb-lib-work-cards", role: "region", tabIndex: 0, "aria-label": L("library.summaryNotesTags", "Summary, notes and tags") },
          h("section", { className: "wb-lib-work-card" }, h("h3", null, L("library.abstract", "Abstract")), h("p", null, item.abstract || L("library.noAbstract", "No abstract available"))),
          h("section", { className: "wb-lib-work-card" }, h("div", { className: "wb-lib-work-head" }, h("h3", null, L("library.notes", "Notes")), h("button", { type: "button", onClick: function () { props.onTab("notes"); } }, icon("plus", 14), " ", L("library.addNote", "Add note"))),
            notes.length ? h(React.Fragment, null, h("p", null, notes[0].content || notes[0].text || ""), h("small", null, notes[0].author || L("library.me", "Me"), " · ", formatDate(notes[0].updated_at || notes[0].created_at))) : h("p", { className: "wb-lib-muted" }, L("library.noNotes", "No notes yet."))),
          h("section", { className: "wb-lib-work-card" }, h("h3", null, L("library.tags", "Tags")), h("div", { className: "wb-lib-tag-list" }, itemTags(item).map(function (tag) { return h("span", { key: tag }, tag); }), h("button", { type: "button", onClick: function () { props.onTab("tags"); } }, icon("plus", 14), " ", L("library.addTag", "Add tag"))))) :
          h("div", { className: "wb-lib-work-pane-body" }, props.activeContent)));
  }

  function NotesWorkspace(props) {
    var notes = Array.isArray(props.item.notes) ? props.item.notes : [];
    var state = useState(""); var value = state[0]; var setValue = state[1];
    var busyState = useState(false); var busy = busyState[0]; var setBusy = busyState[1];
    function submit() {
      var content = value.trim();
      if (!content || busy) return;
      setBusy(true);
      props.onAdd({ title: L("library.researchNote", "Research note"), content: content, author: (workbenchServices.data().state.user || {}).name || L("library.me", "Me") }).then(function () { setValue(""); setBusy(false); }, function () { setBusy(false); });
    }
    return h("div", { className: "wb-lib-editor-layout" },
      h("div", { className: "wb-lib-note-compose" }, h("textarea", { value: value, onChange: function (event) { setValue(event.target.value); }, placeholder: L("library.notePlaceholder", "Record findings, questions, or next steps for this item…") }), h("button", { type: "button", className: "wb-lib-primary", disabled: busy || !value.trim(), onClick: submit }, busy ? h(Spinner) : icon("plus", 14), " ", L("library.addNote", "Add note"))),
      h("div", { className: "wb-lib-note-list" }, notes.length ? notes.map(function (note) { return h("article", { key: note.id }, h("h4", null, note.title || L("library.note", "Note")), h("p", null, note.content || note.text || ""), h("small", null, note.author || L("library.me", "Me"), " · ", formatDate(note.updated_at || note.created_at, true))); }) : h("p", { className: "wb-lib-muted" }, L("library.noNotes", "No notes yet."))));
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
    return h("div", { className: "wb-lib-tags-workspace" }, h("h3", null, L("library.itemTags", "Item tags")), h("p", null, L("library.itemTagsHint", "Tags are stored only in this project's knowledge base.")),
      h("div", { className: "wb-lib-tag-editor" }, tags.map(function (tag) { return h("span", { key: tag }, tag, h("button", { type: "button", onClick: function () { save(tags.filter(function (v) { return v !== tag; })); }, title: L("library.removeTag", "Remove {tag}", { tag: tag }) }, icon("close", 12))); }),
        h("input", { value: value, onChange: function (event) { setValue(event.target.value); }, onKeyDown: function (event) { if (event.key === "Enter") { event.preventDefault(); add(); } }, placeholder: L("library.tagInputPlaceholder", "Enter a tag and press Enter") })),
      h("button", { type: "button", className: "wb-lib-secondary", disabled: !value.trim(), onClick: add }, icon("plus", 14), " ", L("common.add", "Add")));
  }

  function RelationsWorkspace(props) {
    var relations = Array.isArray(props.item.relations) ? props.item.relations : [];
    return h("div", { className: "wb-lib-relations" }, relations.length ? relations.map(function (relation, index) {
      return h("article", { key: relation.id || index }, h("span", null, icon("link", 17)), h("div", null, h("h4", null, relation.title || relation.name || relation.target_title || L("library.relatedItem", "Related item")), h("p", null, relation.relation_type || relation.type || L("library.related", "Related"))));
    }) : h(StatePanel, { title: L("library.noRelations", "No related items"), body: L("library.noRelationsHint", "Sync Zotero Related Items or let an Agent create literature relationships in this project.") }));
  }

  function AttachmentsWorkspace(props) {
    var attachments = Array.isArray(props.item.attachments) ? props.item.attachments : [];
    return h("div", { className: "wb-lib-attachments" }, attachments.length ? attachments.map(function (attachment, index) {
      return h("a", { key: attachment.id || index, href: attachment.raw_url || props.rawUrl, target: "_blank", rel: "noreferrer" }, h(PdfMark, { item: Object.assign({}, props.item, { attachments: [attachment] }) }), h("div", null, h("b", null, attachment.filename || attachment.name || L("library.attachment", "Attachment")), h("small", null, formatBytes(attachment.size), attachment.page_count ? " · " + L("library.pageCount", "Pages: {count}", { count: attachment.page_count }) : "")), icon("eye", 16));
    }) : h(StatePanel, { title: L("library.noAttachment", "No attachment"), body: L("library.noAttachmentHint", "Import a PDF from the toolbar or sync attachments from Zotero.") }));
  }

  function CitationCopyControl(props) {
    var openState = useState(false); var open = openState[0]; var setOpen = openState[1];
    var disabled = !props.citation && !props.bibtex;
    function copy(format) { setOpen(false); props.onCopy(format); }
    return h("div", { className: "wb-lib-menu-wrap wb-lib-copy-control" },
      h("button", { type: "button", className: "wb-lib-copy-trigger", disabled: disabled, onClick: function () { setOpen(!open); } }, icon("copy", 13), " ", L("common.copy", "Copy"), h("span", { className: "wb-lib-copy-chevron" }, icon("chevron", 11))),
      h(Dropdown, { open: open, onClose: function () { setOpen(false); }, className: "wb-lib-copy-menu" },
        h("button", { type: "button", disabled: !props.citation, onClick: function () { copy("text"); } }, h("span", null, h("b", null, L("library.copyPlainText", "Copy plain text")), h("small", null, L("library.currentCitationStyle", "Current citation style")))),
        h("button", { type: "button", disabled: !props.bibtex, onClick: function () { copy("bibtex"); } }, h("span", null, h("b", null, L("library.copyBibtex", "Copy BibTeX")), h("small", null, L("library.bibtexImportHint", "Ready to import into reference tools"))))));
  }

  function CitationWorkspace(props) {
    return h("div", { className: "wb-lib-citation-workspace" }, h("div", { className: "wb-lib-citation-toolbar" }, h("label", null, L("library.citationStyle", "Citation style"), h("select", { value: props.style, onChange: function (event) { props.onStyle(event.target.value); } }, ["ieee", "apa", "mla", "chicago-author-date", "gb-t-7714-2015-numeric"].map(function (style) { return h("option", { key: style, value: style }, style.toUpperCase()); }))), h(CitationCopyControl, { citation: props.citation, bibtex: props.bibtex, onCopy: props.onCopy })),
      props.loading ? h(StatePanel, { loading: true, title: L("library.citationGenerating", "Generating citation…") }) : props.error ? h(StatePanel, { kind: "error", title: L("library.citationFailed", "Citation generation failed"), body: props.error, action: props.onRetry, actionLabel: L("common.retry", "Retry") }) : h("blockquote", null, props.citation || L("library.citationUnavailable", "No citation is available.")),
      props.citekey && h("div", { className: "wb-lib-citekey" }, h("span", null, L("library.citationKey", "Citation key")), h("code", null, props.citekey)));
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
    var detailHeightLabel = L("library.detailPanelHeight", "Knowledge detail panel height");
    var resizeRef = useRef(null);
    var resizeHandleRef = useRef(null);
    var tabs = [
      { id: "info", label: L("library.info", "Information") }, { id: "notes", label: L("library.notes", "Notes") }, { id: "tags", label: L("library.tags", "Tags") },
      { id: "relations", label: L("library.relations", "Relations") }, { id: "attachments", label: L("library.attachments", "Attachments") }, { id: "citation", label: L("library.citation", "Citation") },
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
    function setSemanticHeight(input) {
      var handle = resizeHandleRef.current;
      if (!handle || !handle.isConnected) throw new Error("library detail separator is not available");
      var bounds = heightBounds(handle);
      var current = handle.parentElement.getBoundingClientRect().height;
      var next;
      if (input && Number.isFinite(Number(input.value_ratio))) {
        next = bounds.min + ((bounds.max - bounds.min) * Math.max(0, Math.min(1, Number(input.value_ratio))));
      } else {
        var delta = Number(input && input.delta_ratio);
        if (!Number.isFinite(delta)) throw new Error("delta_ratio or value_ratio is required");
        next = current + ((bounds.max - bounds.min) * Math.max(-1, Math.min(1, delta)));
      }
      next = Math.max(bounds.min, Math.min(bounds.max, Math.round(next)));
      rememberHeight(next);
      return { height: next, minimum: bounds.min, maximum: bounds.max };
    }
    useEffect(function () {
      if (!window.CyreneUI.has("uiSurface")) return undefined;
      var uiSurface = workbenchServices.uiSurface();
      return uiSurface.register({
        node_id: "library_detail_separator",
        parent_id: "root",
        scope: "main",
        get_node: function () {
          var handle = resizeHandleRef.current;
          return handle && handle.isConnected ? {
            role: "separator",
            name: detailHeightLabel,
            value_summary: String(Math.round(handle.parentElement.getBoundingClientRect().height)),
            state: { orientation: "horizontal", automatic: panelHeight == null },
          } : null;
        },
        actions: [
          { action_id: "adjust", kind: "adjust", risk: "R1", gesture_aliases: ["pointer_resize", "arrow_key"], input_schema: { delta_ratio: "-1..1" } },
          { action_id: "set_value", kind: "adjust", risk: "R1", gesture_aliases: ["pointer_resize"], input_schema: { value_ratio: "0..1" } },
          { action_id: "reset_size", kind: "invoke", risk: "R1", gesture_aliases: ["double_press"] },
        ],
        handlers: { adjust: setSemanticHeight, set_value: setSemanticHeight, reset_size: resetHeight },
      });
    }, [panelHeight, detailHeightLabel]);

    return h("section", {
      className: "wb-lib-item-workspace" + (panelHeight ? " is-resized" : ""),
      style: panelHeight ? { height: Math.round(panelHeight) + "px" } : null,
    },
      h("div", {
        ref: resizeHandleRef,
        className: "wb-lib-work-resizer",
        role: "separator",
        tabIndex: 0,
        title: L("library.detailPanelResizeHint", "Drag to resize the detail panel; double-click to restore automatic height"),
        "aria-label": L("library.detailPanelResize", "Resize the detail panel"),
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
      h("div", { className: "wb-lib-work-body info" }, props.loading ? h(StatePanel, { loading: true, title: L("library.loadingDetails", "Loading knowledge details…") }) : h(InfoWorkspace, {
        item: props.item,
        rawUrl: props.rawUrl,
        onUpdate: props.onUpdate,
        onTab: props.onTab,
        tab: props.tab,
        navigation: h("nav", { className: "wb-lib-work-tabs", "aria-label": L("library.detailTabs", "Knowledge detail tabs") },
        tabs.map(function (tab) {
          return h("button", {
            key: tab.id,
            type: "button",
            className: props.tab === tab.id ? "active" : "",
            onClick: function () { props.onTab(tab.id); },
          }, tab.label);
        }),
        h("button", { type: "button", className: "wb-lib-work-tabs-more", title: L("common.more", "More"), "aria-label": L("common.more", "More") }, icon("menu", 17))),
        activeContent: props.tab === "notes" ? h(NotesWorkspace, { item: props.item, onAdd: props.onAddNote }) :
          props.tab === "tags" ? h(TagsWorkspace, { item: props.item, onUpdate: props.onUpdate }) :
            props.tab === "relations" ? h(RelationsWorkspace, { item: props.item }) :
              props.tab === "attachments" ? h(AttachmentsWorkspace, { item: props.item, rawUrl: props.rawUrl }) :
                props.tab === "citation" ? h(CitationWorkspace, props.citationProps) : null,
      })));
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
      h("header", null, h("div", null, h("h3", null, L("library.editMetadata", "Edit item information")), h("p", null, L("library.editMetadataHint", "File size and timestamps are maintained by the system; all other metadata can be edited.")))),
      h("label", null, h("span", null, L("library.itemType", "Item type")), h("select", { value: form.item_type, onChange: function (event) { field("item_type", event.target.value); } }, itemTypes.map(function (type) { return h("option", { key: type, value: type }, itemTypeLabel(type)); }))),
      inputField(L("library.titleField", "Title"), "title"),
      h("label", null, h("span", null, L("library.author", "Author")), h("textarea", { value: form.authors, onChange: function (event) { field("authors", event.target.value); }, placeholder: L("library.authorsPlaceholder", "Separate multiple authors with semicolons or line breaks") })),
      h("label", null, h("span", null, L("library.abstract", "Abstract")), h("textarea", { value: form.abstract, onChange: function (event) { field("abstract", event.target.value); }, placeholder: L("library.abstractPlaceholder", "Abstract from the source metadata") })),
      inputField(L("library.sourceField", "Publication"), "venue"),
      inputField(L("library.publisher", "Publisher"), "publisher"),
      h("div", { className: "wb-lib-right-editor-grid" }, inputField(L("library.volume", "Volume"), "volume"), inputField(L("library.issue", "Issue"), "issue"), inputField(L("library.pages", "Pages"), "pages"), inputField(L("library.year", "Year"), "year", "number")),
      inputField("DOI", "doi"),
      inputField("ISBN", "isbn"),
      inputField(L("library.language", "Language"), "language"),
      inputField("URL", "url", "url"),
      inputField(L("library.publicationDate", "Publication date"), "date_text"),
      inputField(L("library.citationKey", "Citation key"), "citekey"),
      h("label", null, h("span", null, L("library.readingStatus", "Reading status")), h("select", { value: form.reading_status, onChange: function (event) { field("reading_status", event.target.value); } },
        h("option", { value: "unread" }, L("library.status.unread", "Unread")), h("option", { value: "reading" }, L("library.status.reading", "Reading")), h("option", { value: "read" }, L("library.status.read", "Read")), h("option", { value: "archived" }, L("library.status.archived", "Archived")))),
      inputField(L("library.tags", "Tags"), "tags"),
      h("label", { className: "wb-lib-right-editor-check" }, h("input", { type: "checkbox", checked: form.starred, onChange: function (event) { field("starred", event.target.checked); } }), h("span", null, L("library.markStarred", "Mark as starred"))),
      h("footer", null,
        h("button", { type: "button", className: "wb-lib-secondary", disabled: saving, onClick: props.onCancel }, L("common.cancel", "Cancel")),
        h("button", { type: "submit", className: "wb-lib-primary", disabled: saving || !form.title.trim() }, saving ? h(Spinner) : null, saving ? L("common.saving", "Saving…") : L("library.saveAllInformation", "Save all information"))));
  }

  function CollectionMembership(props) {
    var collections = Array.isArray(props.collections) ? props.collections : [];
    var selected = (Array.isArray(props.item && props.item.collections) ? props.item.collections : []).map(function (collection) {
      return String(collection.id || collection.collection_id || "");
    }).filter(Boolean);
    function update(next) {
      if (props.onUpdate) props.onUpdate(next);
    }
    function add(event) {
      var id = String(event.target.value || "");
      event.target.value = "";
      if (id && selected.indexOf(id) < 0) update(selected.concat([id]));
    }
    return h("section", { className: "wb-lib-right-collections" },
      h("div", { className: "wb-lib-right-section-head" },
        h("h3", null, L("library.collections", "Collections")),
        collections.length > 0 && h("select", { defaultValue: "", onChange: add, "aria-label": L("library.addToCollection", "Add item to a collection") },
          h("option", { value: "", disabled: true }, L("library.addToCollection", "Add to collection")),
          collections.filter(function (collection) { return selected.indexOf(String(collection.id)) < 0; }).map(function (collection) {
            return h("option", { key: collection.id, value: collection.id }, collectionName(collection));
          }))),
      h("div", { className: "wb-lib-right-card wb-lib-collection-chips" },
        selected.map(function (id) {
          var collection = collections.find(function (value) { return String(value.id) === id; });
          if (!collection) return null;
          return h("span", { key: id }, collectionName(collection), h("button", {
            type: "button",
            onClick: function () { update(selected.filter(function (value) { return value !== id; })); },
            title: L("library.removeFromCollection", "Remove from collection"),
            "aria-label": L("library.removeFromNamedCollection", "Remove from {collection}", { collection: collectionName(collection) }),
          }, icon("close", 12)));
        }),
        !selected.length && h("p", { className: "wb-lib-muted" }, collections.length ? L("library.notInCollection", "Not in a collection yet") : L("library.createCollectionFirst", "Create a collection in the sidebar first"))));
  }

  function RightPanel(props) {
    var editingState = useState(false); var editing = editingState[0]; var setEditing = editingState[1];
    var scrollRef = useRef(null);
    useEffect(function () { setEditing(false); }, [props.item && props.item.id]);
    useEffect(function () {
      if (scrollRef.current) scrollRef.current.scrollTop = 0;
    }, [props.item && props.item.id, props.tab]);
    if (!props.item) return h("aside", { className: "wb-floating-detail-shell wb-lib-right empty" },
      h("div", { className: "wb-floating-detail-card wb-lib-right-panel-card empty" },
        h("div", { className: "wb-detail-empty-state wb-lib-right-placeholder" }, icon("panel", 34), h("p", null, L("library.selectToView", "Select a knowledge item to view details")))));
    var item = props.item;
    var attachment = Array.isArray(item.attachments) && item.attachments[0];
    var annotations = Array.isArray(item.annotations) ? item.annotations : [];
    var embedding = item.embedding_status || {};
    var embeddingLabel = {
      complete: L("library.embeddingComplete", "Vectorized"),
      partial: L("library.embeddingPartial", "Partially vectorized"),
      incompatible: L("library.embeddingIncompatible", "Vectors use another model"),
      none: L("library.embeddingNone", "Not vectorized"),
    }[embedding.state] || L("library.embeddingNone", "Not vectorized");
    var embeddingValue = embedding.state === "partial"
      ? embeddingLabel + " · " + Number(embedding.compatible_chunks || 0) + "/" + Number(embedding.total_chunks || 0)
      : embeddingLabel;
    function panelBody(tabId) { return h("div", {
      className: "wb-lib-right-scroll",
      ref: props.tab === tabId ? scrollRef : null,
      onScroll: function () {
        if (tabId === "content" && props.onContentViewed) props.onContentViewed();
      },
      onWheelCapture: function () {
        if (tabId === "content" && props.onContentViewed) props.onContentViewed();
      },
    },
      h("header", { className: "wb-lib-right-head" }, h(PdfMark, { item: item }), h("b", { title: itemTitle(item) }, itemTitle(item)), hasAttachment(item) && h("a", { href: props.rawUrl, target: "_blank", rel: "noreferrer", title: L("library.viewAttachment", "View attachment") }, icon("eye", 17))),
      tabId === "detail" && editing && h(RightMetadataEditor, { item: item, onSave: props.onUpdate, onCancel: function () { setEditing(false); } }),
      tabId === "detail" && !editing && h(React.Fragment, null,
        h("p", { className: "wb-lib-right-abstract" }, item.abstract || L("library.noAbstract", "No abstract available")),
        h("section", null, h("h3", null, L("library.fileInformation", "File information")), h("div", { className: "wb-lib-right-card" },
          h(MetaLine, { label: L("library.fileSize", "File size"), value: formatBytes(attachment && attachment.size) }), h(MetaLine, { label: L("library.pageTotal", "Pages"), value: attachment && attachment.page_count }),
          h(MetaLine, { label: L("library.createdAt", "Created"), value: formatDate(item.created_at, true) }), h(MetaLine, { label: L("library.updated", "Updated"), value: formatDate(item.updated_at, true) }),
          h(MetaLine, { label: L("library.sourceField", "Source"), value: item.provider === "zotero" ? "Zotero" : (item.provider || "Cyrene") }),
          embedding.state && h(MetaLine, { label: L("library.embeddingStatus", "Vector status"), value: embeddingValue }))),
        h(CollectionMembership, { item: item, collections: props.collections, onUpdate: props.onCollectionsUpdate }),
        h("button", { type: "button", className: "wb-lib-right-edit-button", onClick: function () { setEditing(true); } }, icon("note", 15), L("library.editInformation", "Edit information"))),
      tabId === "content" && h(ContentPreview, { item: item, rawUrl: props.rawUrl, annotations: annotations, onViewed: props.onContentViewed }),
      tabId === "notes" && h(NotesWorkspace, { item: item, onAdd: props.onAddNote }),
      tabId === "tags" && h(TagsWorkspace, { item: item, onUpdate: props.onUpdate }),
      tabId === "related" && h(RelationsWorkspace, { item: item }),
      tabId === "attachments" && h(AttachmentsWorkspace, { item: item, rawUrl: props.rawUrl }),
      tabId === "citation" && h(CitationWorkspace, props.citationProps)); }
    return h("aside", { className: "wb-floating-detail-shell wb-lib-right" + (props.open ? " open" : ""), "aria-label": L("library.detailPanel", "Knowledge details") },
      h("div", { className: "wb-floating-detail-card wb-lib-right-panel-card" },
        h("nav", { className: "wb-detail-accordion wb-lib-right-tabs", "aria-label": L("library.detailPanel", "Knowledge details") },
          h("div", { className: "wb-detail-accordion-head wb-lib-right-tabs-head" },
            h("span", null, L("library.detailPanel", "Knowledge details")),
            h("div", null,
              h("button", { type: "button", className: "wb-detail-card-delete wb-lib-right-delete", disabled: !props.onDelete, onClick: props.onDelete, title: L("library.moveToTrash", "Move to trash"), "aria-label": L("library.moveToTrash", "Move to trash") }, icon("trash", 15)),
              h("button", { type: "button", className: "wb-lib-right-close", onClick: props.onClose, title: L("common.close", "Close"), "aria-label": L("common.close", "Close") }, icon("close", 15)))),
          h("div", { className: "wb-detail-accordion-list wb-lib-right-tab-list" },
            [
              { id: "detail", label: L("library.info", "Information"), icon: "note" },
              { id: "content", label: L("library.content", "Content"), icon: "file" },
              { id: "notes", label: L("library.notes", "Notes"), icon: "note" },
              { id: "tags", label: L("library.tags", "Tags"), icon: "tag" },
              { id: "related", label: L("library.relations", "Relations"), icon: "link" },
              { id: "attachments", label: L("library.attachments", "Attachments"), icon: "file" },
              { id: "citation", label: L("library.citation", "Citation"), icon: "copy" },
            ].map(function (tab) {
              return h(React.Fragment, { key: tab.id },
                h("button", { type: "button", className: "wb-detail-accordion-trigger" + (props.tab === tab.id ? " active" : ""), "aria-expanded": props.tab === tab.id, onClick: function () { var next = props.tab === tab.id ? "" : tab.id; props.onTab(next); if (next === "content" && props.onContentViewed) props.onContentViewed(); } },
                  h("span", { className: "wb-detail-accordion-icon wb-lib-right-tab-icon" }, icon(tab.icon, 17)),
                  h("span", null, tab.label),
                  icon("chevron", 14)),
                h("div", { className: "wb-detail-accordion-panel wb-lib-right-tab-panel" + (props.tab === tab.id ? " open" : ""), "aria-hidden": props.tab !== tab.id },
                  h("div", { className: "wb-detail-accordion-panel-inner" }, panelBody(tab.id))));
            }))),
        h("button", { type: "button", className: "wb-lib-detail-fab", onClick: props.onClose, title: L("library.closeDetails", "Close details") }, icon("panel", 17))));
  }

  function LibraryPdfPreview(props) {
    var pdf = workbenchServices.pdf();
    var containerRef = useRef(null);
    var viewerRef = useRef(null);
    var loadingState = useState(true); var loading = loadingState[0]; var setLoading = loadingState[1];
    var errorState = useState(""); var error = errorState[0]; var setError = errorState[1];
    var pageState = useState({ current: 1, total: 0 }); var page = pageState[0]; var setPage = pageState[1];

    useEffect(function () {
      var container = containerRef.current;
      if (!container || !props.url) {
        setError(L("library.pdfUrlUnavailable", "No PDF URL is available."));
        setLoading(false);
        return;
      }
      if (!pdf.lib || !pdf.viewer || !pdf.setupViewer || !pdf.loadPdf) {
        setError(L("library.pdfViewerUnavailable", "PDF.js is not loaded."));
        setLoading(false);
        return;
      }

      var cancelled = false;
      var abortLoader = new AbortController();
      var loadTimedOut = false;
      var timer = setTimeout(function () {
        loadTimedOut = true;
        abortLoader.abort(new DOMException("PDF loading timed out", "TimeoutError"));
        setError(L("library.pdfLoadTimedOut", "PDF loading timed out."));
        setLoading(false);
      }, 60000);
      var result = pdf.setupViewer(container);
      var viewer = result.viewer;
      var eventBus = result.eventBus;
      var loadedDocument = null;
      viewerRef.current = viewer;
      function onPageChanging(event) {
        if (!cancelled) setPage(function (old) { return { current: event.pageNumber, total: old.total }; });
      }
      eventBus.on("pagechanging", onPageChanging);
      var resizeObserver = new ResizeObserver(function () { viewer.update(); });
      resizeObserver.observe(container);
      var selectionSanitizer = pdf.installSelectionSanitizer
        ? pdf.installSelectionSanitizer(container, viewer, eventBus)
        : null;
      var copyFix = pdf.installCopyFix
        ? pdf.installCopyFix(container, viewer)
        : null;
      pdf.loadPdf(props.url, viewer, abortLoader.signal).then(function (document) {
        loadedDocument = document;
        if (cancelled) {
          try { document.destroy(); } catch (error) {}
          return;
        }
        clearTimeout(timer);
        setPage({ current: 1, total: document.numPages });
        setLoading(false);
        if (props.onViewed) props.onViewed();
      }).catch(function (error) {
        if (!cancelled) {
          clearTimeout(timer);
          setError(loadTimedOut ? L("library.pdfLoadTimedOut", "PDF loading timed out.") : String(error && error.message || L("library.pdfLoadFailed", "PDF loading failed.")));
          setLoading(false);
        }
      });
      return function () {
        cancelled = true;
        clearTimeout(timer);
        abortLoader.abort();
        if (selectionSanitizer) selectionSanitizer.abort();
        if (copyFix) copyFix.abort();
        resizeObserver.disconnect();
        eventBus.off("pagechanging", onPageChanging);
        if (viewerRef.current) {
          try { viewerRef.current.setDocument(null); } catch (error) {}
        }
        if (loadedDocument) {
          try { loadedDocument.destroy(); } catch (error) {}
        }
        viewerRef.current = null;
      };
    }, [props.url]);

    return h("div", { className: "wb-lib-pdf-preview" },
      h("div", { className: "wb-lib-pdf-preview-head" },
        h("span", { title: props.title }, props.title || "PDF"),
        !loading && !error && h("small", null, page.current + " / " + page.total)),
      h("div", { className: "wb-lib-pdf-preview-body" },
        h("div", {
          className: "wb-lib-pdf-viewer",
          ref: containerRef,
          onScroll: props.onViewed,
          onWheelCapture: props.onViewed,
        }),
        loading && h("div", { className: "wb-lib-pdf-preview-state" }, h(Spinner), L("library.pdfLoading", "Loading PDF…")),
        error && h("div", { className: "wb-lib-pdf-preview-state error" },
          h("p", null, L("library.pdfContentFailed", "PDF content could not be loaded")),
          h("small", null, error))));
  }

  function ContentPreview(props) {
    var item = props.item || {};
    var attachment = primaryAttachment(item);
    var previewType = attachmentPreviewType(item);
    var content = item.content || item.indexed_text || "";
    var filename = String(attachment && (attachment.filename || attachment.name) || itemTitle(item));
    var isMarkdown = /\.(md|markdown|mdown|mkd)$/i.test(filename);
    var isHtml = /\.html?$/i.test(filename);
    var markdownHtml = isMarkdown && content ? renderMarkdownHtml(content) : "";
    var safeHtml = isHtml && content ? renderSafeHtmlDocument(content) : "";
    var media = null;
    if (attachment && previewType === "image") {
      media = h("figure", { className: "wb-lib-media-preview image" },
        h("img", { src: props.rawUrl, alt: itemTitle(item), loading: "lazy", onLoad: props.onViewed }),
        h("figcaption", null, filename));
    } else if (attachment && previewType === "video") {
      media = h("figure", { className: "wb-lib-media-preview video" },
        h("video", { src: props.rawUrl, controls: true, preload: "metadata", playsInline: true },
          h("a", { href: props.rawUrl, target: "_blank", rel: "noreferrer" }, L("library.openVideo", "Open video"))),
        h("figcaption", null, filename));
    } else if (attachment && previewType === "audio") {
      media = h("figure", { className: "wb-lib-media-preview audio" },
        h("audio", { src: props.rawUrl, controls: true, preload: "metadata" },
          h("a", { href: props.rawUrl, target: "_blank", rel: "noreferrer" }, L("library.openAudio", "Open audio"))),
        h("figcaption", null, filename));
    } else if (attachment && previewType === "pdf") {
      media = h(LibraryPdfPreview, {
        url: props.rawUrl,
        title: filename,
        onViewed: props.onViewed,
      });
    } else if (attachment && previewType === "file" && !content) {
      media = h("div", { className: "wb-lib-media-fallback" },
        icon("file", 26),
        h("p", null, L("library.previewUnsupported", "This file format cannot be previewed here.")),
        h("a", { href: props.rawUrl, target: "_blank", rel: "noreferrer" }, L("library.openFile", "Open file")));
    }
    return h("div", { className: "wb-lib-content-panel" },
      media || (safeHtml
        ? h("div", { className: "wb-lib-html-preview" }, h("iframe", {
          srcDoc: safeHtml,
          sandbox: "",
          referrerPolicy: "no-referrer",
          title: L("library.htmlPreviewTitle", "HTML content preview for {title}", { title: itemTitle(item) }),
        }))
        : markdownHtml
        ? h("div", { className: "wb-lib-markdown", dangerouslySetInnerHTML: { __html: markdownHtml } })
        : (content ? h("pre", null, content) : h("p", { className: "wb-lib-content-empty" }, L("library.noDisplayContent", "No content is available to display.")))),
      Array.isArray(props.annotations) && props.annotations.length > 0 && h("section", null,
        h("h3", null, L("library.pdfAnnotations", "PDF annotations")),
        props.annotations.map(function (annotation) {
          return h("blockquote", { key: annotation.id }, h("p", null, annotation.quote || annotation.text || annotation.comment), h("small", null, annotation.page_label ? L("library.pageNumber", "Page {page}", { page: annotation.page_label }) : ""));
        })));
  }

  function Dropdown(props) {
    if (!props.open) return null;
    return h(React.Fragment, null, h("button", { type: "button", className: "wb-lib-scrim", onClick: props.onClose, tabIndex: -1, "aria-label": L("common.closeMenu", "Close menu") }), h("div", { className: "wb-lib-dropdown " + (props.className || "") }, props.children));
  }

  function ManualItemModal(props) {
    var initial = { title: "", authors: "", year: "", venue: "", url: "", doi: "", item_type: "document" };
    var formState = useState(initial); var form = formState[0]; var setForm = formState[1];
    var busyState = useState(false); var busy = busyState[0]; var setBusy = busyState[1];
    function field(name, value) { setForm(Object.assign({}, form, (function () { var o = {}; o[name] = value; return o; })())); }
    function submit(event) {
      event.preventDefault(); if (!form.title.trim() || busy) return; setBusy(true);
      var creators = form.authors.split(/[;；\n]+/).map(function (name) { return name.trim(); }).filter(Boolean).map(function (name) { return { name: name, creator_type: "author" }; });
      props.onSave(Object.assign({}, form, { year: form.year ? Number(form.year) : null, creators: creators })).then(function () { setBusy(false); props.onClose(); }, function () { setBusy(false); });
    }
    return h("div", { className: "wb-lib-modal-layer", role: "presentation", onMouseDown: function (event) { if (event.target === event.currentTarget) props.onClose(); } }, h("form", { className: "wb-lib-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "wb-lib-add-title", onSubmit: submit },
      h("header", null, h("div", null, h("h2", { id: "wb-lib-add-title" }, L("library.createItem", "Create a knowledge item")), h("p", null, L("library.createItemHint", "The item will be added only to the current project."))), h("button", { type: "button", onClick: props.onClose, "aria-label": L("common.close", "Close") }, icon("close"))),
      h("label", null, L("library.titleField", "Title"), h("input", { required: true, autoFocus: true, value: form.title, onChange: function (event) { field("title", event.target.value); }, placeholder: L("library.titlePlaceholder", "Knowledge title") })),
      h("label", null, L("library.authorsOrContributors", "Authors or contributors"), h("textarea", { value: form.authors, onChange: function (event) { field("authors", event.target.value); }, placeholder: L("library.authorsSemicolonPlaceholder", "Separate multiple people with semicolons") })),
      h("div", { className: "wb-lib-modal-grid" }, h("label", null, L("library.year", "Year"), h("input", { type: "number", min: 0, max: 9999, value: form.year, onChange: function (event) { field("year", event.target.value); } })), h("label", null, L("library.itemType", "Item type"), h("select", { value: form.item_type, onChange: function (event) { field("item_type", event.target.value); } }, ["document", "webpage", "journalArticle", "conferencePaper", "book", "thesis", "report"].map(function (type) { return h("option", { key: type, value: type }, itemTypeLabel(type)); })))),
      h("label", null, L("library.sourceField", "Source"), h("input", { value: form.venue, onChange: function (event) { field("venue", event.target.value); }, placeholder: L("library.sourcePlaceholder", "Website, publication, or another source") })),
      h("label", null, L("library.sourceLink", "Source link"), h("input", { type: "url", value: form.url, onChange: function (event) { field("url", event.target.value); }, placeholder: "https://…" })),
      h("label", null, "DOI", h("input", { value: form.doi, onChange: function (event) { field("doi", event.target.value); }, placeholder: "10.xxxx/xxxxx" })),
      h("footer", null, h("button", { type: "button", className: "wb-lib-secondary", onClick: props.onClose }, L("common.cancel", "Cancel")), h("button", { type: "submit", className: "wb-lib-primary", disabled: busy || !form.title.trim() }, busy && h(Spinner), " ", L("library.addItem", "Add item")))));
  }

  function CollectionModal(props) {
    var nameState = useState(""); var name = nameState[0]; var setName = nameState[1];
    var busyState = useState(false); var busy = busyState[0]; var setBusy = busyState[1];
    function submit(event) {
      event.preventDefault();
      if (!name.trim() || busy) return;
      setBusy(true);
      props.onSave({ name: name.trim() }).then(function () {
        setBusy(false);
        props.onClose();
      }, function () { setBusy(false); });
    }
    return h("div", { className: "wb-lib-modal-layer", role: "presentation", onMouseDown: function (event) { if (event.target === event.currentTarget) props.onClose(); } },
      h("form", { className: "wb-lib-modal wb-lib-collection-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "wb-lib-collection-title", onSubmit: submit },
        h("header", null, h("div", null, h("h2", { id: "wb-lib-collection-title" }, L("library.newCollection", "New collection")), h("p", null, L("library.collectionProjectHint", "Collections are stored only in the current project's knowledge base."))), h("button", { type: "button", onClick: props.onClose, "aria-label": L("common.close", "Close") }, icon("close"))),
        h("label", null, L("common.name", "Name"), h("input", { required: true, autoFocus: true, value: name, onChange: function (event) { setName(event.target.value); }, placeholder: L("library.collectionNamePlaceholder", "For example: Literature review") })),
        h("footer", null,
          h("button", { type: "button", className: "wb-lib-secondary", disabled: busy, onClick: props.onClose }, L("common.cancel", "Cancel")),
          h("button", { type: "submit", className: "wb-lib-primary", disabled: busy || !name.trim() }, busy && h(Spinner), busy ? L("common.creating", "Creating…") : L("common.create", "Create")))));
  }

  function WorkbenchLibraryPage(props) {
    workbenchServices.i18n().use();
    var workspace = props.project && props.project.id ? String(props.project.id) : "";
    var client = useMemo(function () { return workspace ? libraryApi(workspace) : null; }, [workspace]);
    var scopeState = useState({ type: "all" }); var scope = scopeState[0]; var setScope = scopeState[1];
    var queryState = useState(""); var query = queryState[0]; var setQuery = queryState[1];
    var debouncedState = useState(""); var debouncedQuery = debouncedState[0]; var setDebouncedQuery = debouncedState[1];
    var sortState = useState("updated_at"); var sort = sortState[0]; var setSort = sortState[1];
    var orderState = useState("desc"); var order = orderState[0]; var setOrder = orderState[1];
    var filterState = useState({ file_type: "", item_type: "", status: "", year: "" }); var filters = filterState[0]; var setFilters = filterState[1];
    var viewState = useState(function () {
      try {
        var savedView = window.localStorage.getItem("cyrene.library.viewMode");
        return savedView === "grid" || savedView === "table" ? savedView : "table";
      } catch (_) {
        return "table";
      }
    }); var view = viewState[0]; var setView = viewState[1];
    var dataState = useState({ items: [], total: 0, stats: {}, collections: [], tags: [] }); var data = dataState[0]; var setData = dataState[1];
    var loadingState = useState(false); var loading = loadingState[0]; var setLoading = loadingState[1];
    var loadingMoreState = useState(false); var loadingMore = loadingMoreState[0]; var setLoadingMore = loadingMoreState[1];
    var errorState = useState(""); var error = errorState[0]; var setError = errorState[1];
    var selectedState = useState(""); var selectedId = selectedState[0]; var setSelectedId = selectedState[1];
    var detailState = useState(null); var detail = detailState[0]; var setDetail = detailState[1];
    var detailLoadingState = useState(false); var detailLoading = detailLoadingState[0]; var setDetailLoading = detailLoadingState[1];
    var checkedState = useState([]); var checked = checkedState[0]; var setChecked = checkedState[1];
    var batchDeletingState = useState(false); var batchDeleting = batchDeletingState[0]; var setBatchDeleting = batchDeletingState[1];
    var menuState = useState(""); var menu = menuState[0]; var setMenu = menuState[1];
    var contextMenuState = useState(null); var contextMenu = contextMenuState[0]; var setContextMenu = contextMenuState[1];
    var rightTabState = useState("detail"); var rightTab = rightTabState[0]; var setRightTab = rightTabState[1];
    var rightOpenState = useState(true); var rightOpen = rightOpenState[0]; var setRightOpen = rightOpenState[1];
    var manualState = useState(false); var manualOpen = manualState[0]; var setManualOpen = manualState[1];
    var collectionModalState = useState(false); var collectionModalOpen = collectionModalState[0]; var setCollectionModalOpen = collectionModalState[1];
    var uploadingState = useState(false); var uploading = uploadingState[0]; var setUploading = uploadingState[1];
    var embeddingState = useState({ configured: false, pending_vectors: 0, compatible_vectors: 0, total_chunks: 0, reembed: { running: false } }); var embedding = embeddingState[0]; var setEmbedding = embeddingState[1];
    var citationState = useState({ style: "ieee", text: "", bibtex: "", citekey: "", loading: false, error: "" }); var citation = citationState[0]; var setCitation = citationState[1];
    var fileRef = useRef(null);
    var requestSeq = useRef(0);
    var loadMoreSeq = useRef(0);
    var detailSeq = useRef(0);
    var readMarksRef = useRef({});

    function loadEmbeddingStatus() {
      if (!client) return Promise.resolve();
      return client.embeddingStatus().then(function (payload) {
        setEmbedding(payload || { configured: false, pending_vectors: 0, reembed: { running: false } });
        return payload || {};
      });
    }

    function vectorizeAll() {
      if (!client || !embedding.configured || !Number(embedding.pending_vectors || 0) || (embedding.reembed && embedding.reembed.running)) return;
      client.vectorizeAll().then(function () {
        Toast(L("library.vectorizeStarted", "Vectorization started"));
        return loadEmbeddingStatus();
      }).catch(function (err) { Toast(String(err.message || err), "error"); });
    }

    useEffect(function () { var timer = setTimeout(function () { setDebouncedQuery(query.trim()); }, 240); return function () { clearTimeout(timer); }; }, [query]);
    useEffect(function () {
      try { window.localStorage.setItem("cyrene.library.viewMode", view); } catch (_) {}
    }, [view]);
    useEffect(function () {
      if (!contextMenu) return undefined;
      function closeContextMenu() { setContextMenu(null); }
      function closeContextMenuOnEscape(event) { if (event.key === "Escape") closeContextMenu(); }
      window.addEventListener("resize", closeContextMenu);
      window.addEventListener("scroll", closeContextMenu, true);
      document.addEventListener("keydown", closeContextMenuOnEscape);
      return function () {
        window.removeEventListener("resize", closeContextMenu);
        window.removeEventListener("scroll", closeContextMenu, true);
        document.removeEventListener("keydown", closeContextMenuOnEscape);
      };
    }, [!!contextMenu]);
    useEffect(function () { loadEmbeddingStatus(); }, [client]);
    useEffect(function () {
      if (!(embedding.reembed && embedding.reembed.running)) return undefined;
      var timer = setInterval(function () {
        loadEmbeddingStatus().then(function (next) {
          if (!(next.reembed && next.reembed.running)) {
            if (next.reembed && next.reembed.error) Toast(L("library.vectorizeFailed", "Vectorization failed") + ": " + next.reembed.error, "error");
            else Toast(L("library.vectorizeComplete", "Vectorization complete", { count: Number(next.reembed && next.reembed.updated || 0) }));
            reload({ itemsOnly: true });
          }
        });
      }, 1000);
      return function () { clearInterval(timer); };
    }, [!!(embedding.reembed && embedding.reembed.running)]);

    function listParams() {
      var params = { q: debouncedQuery, sort: sort, order: order, limit: PAGE_SIZE, offset: 0 };
      if (filters.file_type) params.file_type = filters.file_type;
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
      loadMoreSeq.current += 1;
      setLoadingMore(false);
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
        }
        setChecked(function (prev) { return prev.filter(function (id) { return nextItems.some(function (item) { return String(item.id) === String(id); }); }); });
        setLoading(false);
      }).catch(function (err) { if (seq === requestSeq.current) { setError(String(err.message || err)); setLoading(false); } });
    }

    function loadMore() {
      if (!client || loadingMore || data.items.length >= data.total) return;
      var params = listParams(); params.offset = data.items.length;
      var seq = ++loadMoreSeq.current;
      setLoadingMore(true);
      client.list(params).then(function (payload) {
        if (seq !== loadMoreSeq.current) return;
        var items = Array.isArray(payload.items) ? payload.items : [];
        setData(function (prev) {
          var seen = {};
          prev.items.forEach(function (item) { seen[String(item.id)] = true; });
          var uniqueItems = items.filter(function (item) {
            var id = String(item.id);
            if (seen[id]) return false;
            seen[id] = true;
            return true;
          });
          return Object.assign({}, prev, {
            items: prev.items.concat(uniqueItems),
            total: payload.total != null ? payload.total : prev.total,
          });
        });
        setLoadingMore(false);
      }).catch(function (err) {
        if (seq !== loadMoreSeq.current) return;
        setLoadingMore(false);
        Toast(String(err.message || err), "error");
      });
    }

    useEffect(function () {
      setScope({ type: "all" }); setSelectedId(""); setDetail(null); setChecked([]); setData({ items: [], total: 0, stats: {}, collections: [], tags: [] });
    }, [workspace]);
    useEffect(function () { if (props.active !== false) reload(); }, [client, workspace, debouncedQuery, scope.type, scope.value, sort, order, filters.file_type, filters.item_type, filters.status, filters.year, props.active]);

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
          if (changed) Toast(L("library.zoteroAutoSyncComplete", "Zotero auto-sync completed. Changes: {count}", { count: changed }));
          reload();
        });
      }).catch(function () { /* Status and sync errors are already surfaced by explicit actions. */ });
      return function () { alive = false; };
    }, [client, workspace, props.active]);

    useEffect(function () {
      if (!client || !selectedId) { setDetail(null); return; }
      var seq = ++detailSeq.current; setDetail(null); setDetailLoading(true);
      client.detail(selectedId).then(function (payload) { if (seq === detailSeq.current) { setDetail(payload.item || payload); setDetailLoading(false); } }, function (err) { if (seq === detailSeq.current) { setDetailLoading(false); Toast(String(err.message || err), "error"); } });
    }, [client, selectedId]);

    var listItem = useMemo(function () { return data.items.find(function (item) { return String(item.id) === String(selectedId); }) || null; }, [data.items, selectedId]);
    var currentDetail = detail && String(detail.id) === String(selectedId) ? detail : null;
    var selectedItem = currentDetail ? Object.assign({}, listItem || {}, currentDetail) : listItem;

    function select(id) { setDetail(null); setSelectedId(String(id)); setRightTab("detail"); setRightOpen(true); }
    function replaceItem(item) {
      if (!item) return;
      setData(function (prev) { return Object.assign({}, prev, { items: prev.items.map(function (old) { return String(old.id) === String(item.id) ? Object.assign({}, old, item) : old; }) }); });
      if (String(item.id) === String(selectedId)) setDetail(function (old) { return Object.assign({}, old || {}, item); });
    }
    function updateSelected(value) {
      if (!client || !selectedId) return Promise.reject(new Error(L("library.noItemSelected", "No knowledge item is selected.")));
      return client.update(selectedId, value).then(function (payload) { var item = payload.item || payload; replaceItem(item); Toast(L("library.itemUpdated", "Knowledge item updated")); reload({ itemsOnly: true }); return item; });
    }
    function updateSelectedCollections(collectionIds) {
      if (!client || !selectedId) return Promise.reject(new Error(L("library.noItemSelected", "No knowledge item is selected.")));
      return client.update(selectedId, { collection_ids: collectionIds }).then(function (payload) {
        var item = payload.item || payload;
        replaceItem(item);
        Toast(L("library.collectionsUpdated", "Collections updated"));
        reload();
        return item;
      });
    }
    function markSelectedRead(itemId) {
      var viewedId = String(itemId || selectedId || "");
      if (!client || !viewedId) return;
      var now = Date.now();
      if (now - Number(readMarksRef.current[viewedId] || 0) < 2000) return;
      readMarksRef.current[viewedId] = now;
      client.update(viewedId, { reading_status: "read" }).then(function (payload) {
        replaceItem(payload.item || payload);
        reload();
      }).catch(function () {
        delete readMarksRef.current[viewedId];
        /* Reading history is best-effort and must not block content. */
      });
    }
    useEffect(function () {
      if (rightOpen && rightTab === "content" && selectedId) markSelectedRead(selectedId);
    }, [rightOpen, rightTab, selectedId]);
    function toggleStar(item) { client.update(item.id, { starred: !item.starred }).then(function (payload) { replaceItem(payload.item || payload); }, function (err) { Toast(String(err.message || err), "error"); }); }
    function addNote(value) { return client.addNote(selectedId, value).then(function () { Toast(L("library.noteAdded", "Note added")); return client.detail(selectedId).then(function (payload) { setDetail(payload.item || payload); }); }); }
    function removeItem(item) {
      if (!item || !item.id) return;
      var run = function () {
        client.remove(item.id).then(function () {
          Toast(L("library.itemMovedToTrash", "Knowledge item moved to trash"));
          if (String(selectedId) === String(item.id)) { setSelectedId(""); setDetail(null); }
          reload();
        });
      };
      var confirmModal = workbenchServices.feedback().confirmModal;
      if (confirmModal) {
        confirmModal({ title: L("library.confirmMoveToTrashTitle", "Move to trash?"), body: L("library.confirmMoveToTrashBody", "The item will remain in this project's trash."), confirmLabel: L("library.moveToTrash", "Move to trash"), danger: true })
          .then(function (ok) { if (ok) run(); });
      } else {
        run();
      }
    }
    function removeSelected() {
      if (!selectedId) return;
      removeItem(selectedItem || { id: selectedId });
    }
    function restore(item) { client.restore(item.id).then(function () { Toast(L("library.itemRestored", "Knowledge item restored")); reload(); }); }
    function permanentlyDeleteItem(item) {
      if (!item || !item.id) return;
      var run = function () {
        client.removeMany([String(item.id)], true).then(function () {
          Toast(L("library.permanentlyDeletedCount", "Items permanently deleted: {count}", { count: 1 }));
          if (String(selectedId) === String(item.id)) { setSelectedId(""); setDetail(null); }
          reload();
        }).catch(function (err) { Toast(String(err.message || err), "error"); });
      };
      var confirmModal = workbenchServices.feedback().confirmModal;
      if (confirmModal) {
        confirmModal({ title: L("library.confirmPermanentDeleteTitle", "Delete this knowledge item permanently?"), body: L("library.confirmPermanentDeleteBody", "This action cannot be undone."), confirmLabel: L("library.permanentDelete", "Delete permanently"), danger: true })
          .then(function (ok) { if (ok) run(); });
      } else {
        run();
      }
    }
    function openItemContextMenu(item, event) {
      var menuWidth = 220;
      var menuHeight = scope.type === "trash" ? 128 : 250;
      var portalTheme = {};
      var themeSource = document.querySelector(".workbench-shell");
      if (themeSource && typeof getComputedStyle === "function") {
        var computedTheme = getComputedStyle(themeSource);
        [
          "--wb-card-bg", "--wb-surface", "--wb-line", "--wb-line-2",
          "--wb-text", "--wb-muted", "--wb-row-hover-bg", "--wb-red",
          "--wb-ui-font-scale",
        ].forEach(function (name) {
          portalTheme[name] = computedTheme.getPropertyValue(name);
        });
        portalTheme.fontFamily = computedTheme.fontFamily;
        portalTheme.colorScheme = computedTheme.colorScheme;
      }
      setMenu("");
      setContextMenu({
        item: item,
        left: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
        top: Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8)),
        portalTheme: portalTheme,
      });
    }
    function showLibraryItemInFolder(item) {
      setContextMenu(null);
      if (!client || !item || !item.id) return;
      client.detail(item.id).then(function (payload) {
        var fullItem = payload.item || payload || {};
        var attachment = primaryAttachment(fullItem);
        var filePath = String(attachment && attachment.path || "").trim();
        var desktopBridge = window.cyrene;
        if (!filePath || !desktopBridge || typeof desktopBridge.showItemInFolder !== "function") {
          throw new Error(L("library.showInFolderUnavailable", "This file cannot be shown in a folder here."));
        }
        return desktopBridge.showItemInFolder(filePath);
      }).then(function (result) {
        if (!result || result.ok !== true) {
          throw new Error(result && result.error || L("library.showInFolderFailed", "Could not show the file in its folder."));
        }
      }).catch(function (err) { Toast(String(err.message || err), "error"); });
    }
    function removeChecked() {
      if (!client || !checked.length || batchDeleting) return;
      var ids = checked.slice();
      var permanent = scope.type === "trash";
      var title = permanent ? L("library.confirmPermanentDeleteSelectedTitle", "Delete the selected knowledge permanently?") : L("library.confirmMoveSelectedTitle", "Move the selected knowledge to trash?");
      var bodyText = permanent
        ? L("library.confirmPermanentDeleteSelectedBody", "Selected items ({count}) will be permanently deleted. This action cannot be undone.", { count: ids.length })
        : L("library.confirmMoveSelectedBody", "Selected items ({count}) will remain in this project's trash.", { count: ids.length });
      var confirmLabel = permanent ? L("library.permanentDelete", "Delete permanently") : L("library.moveToTrash", "Move to trash");
      var run = function () {
        setBatchDeleting(true);
        client.removeMany(ids, permanent).then(function (payload) {
          var deleted = Number(payload.deleted || 0);
          if (ids.indexOf(String(selectedId)) >= 0) { setSelectedId(""); setDetail(null); }
          setChecked([]);
          Toast(permanent
            ? L("library.permanentlyDeletedCount", "Items permanently deleted: {count}", { count: deleted })
            : L("library.movedToTrashCount", "Items moved to trash: {count}", { count: deleted }));
          return reload();
        }).catch(function (err) {
          Toast(String(err.message || err), "error");
        }).finally(function () {
          setBatchDeleting(false);
        });
      };
      if (workbenchServices.feedback().confirmModal) {
        workbenchServices.feedback().confirmModal({ title: title, body: bodyText, confirmLabel: confirmLabel, danger: true }).then(function (ok) { if (ok) run(); });
      } else {
        run();
      }
    }

    function handleFiles(files) {
      if (!client || !files || !files.length) return;
      setUploading(true);
      client.upload(files).then(function (payload) { setUploading(false); if (fileRef.current) fileRef.current.value = ""; Toast(L("library.importedCount", "Items imported: {count}", { count: (payload.items && payload.items.length) || 0 })); reload(); }, function () { setUploading(false); if (fileRef.current) fileRef.current.value = ""; });
    }
    function createItem(value) { return client.create(value).then(function (payload) { var item = payload.item || payload; Toast(L("library.itemAdded", "Knowledge item added")); reload(); if (item.id) select(item.id); return item; }); }
    function createCollection(value) {
      return client.createCollection(value).then(function (collection) {
        Toast(L("library.collectionCreated", "Collection created"));
        return reload().then(function () { return collection; });
      });
    }
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
        var href = URL.createObjectURL(blob); var link = document.createElement("a"); link.href = href; link.download = "cyrene-library-" + workspace + ".json"; link.click(); setTimeout(function () { URL.revokeObjectURL(href); }, 0); Toast(L("library.exportedCount", "Knowledge items exported: {count}", { count: items.length }));
      }).catch(function (err) { Toast(String(err.message || err), "error"); });
    }
    function toggleChecked(id) { setChecked(function (prev) { var key = String(id); return prev.indexOf(key) >= 0 ? prev.filter(function (v) { return v !== key; }) : prev.concat([key]); }); }
    function toggleAll() { setChecked(checked.length === data.items.length ? [] : data.items.map(function (item) { return String(item.id); })); }
    function startLibraryItemDrag(event, item) {
      var resourceApi = workbenchServices.resources();
      if (!resourceApi || !client || !item) {
        event.preventDefault();
        return;
      }
      resourceApi.setDrag(event, libraryResourcePayload(item, workspace, client.rawUrl(item.id)));
      if (event.currentTarget) event.currentTarget.classList.add("dragging");
    }
    function endLibraryItemDrag(event) {
      if (event.currentTarget) event.currentTarget.classList.remove("dragging");
    }

    function loadCitation(style) {
      if (!client || !selectedId) return;
      var nextStyle = style || citation.style; setCitation({ style: nextStyle, text: "", bibtex: "", citekey: "", loading: true, error: "" });
      client.citation(selectedId, nextStyle).then(function (payload) { setCitation({ style: nextStyle, text: payload.citation || "", bibtex: payload.bibtex || "", citekey: payload.citekey || "", loading: false, error: "" }); }, function (err) { setCitation({ style: nextStyle, text: "", bibtex: "", citekey: "", loading: false, error: String(err.message || err) }); });
    }
    useEffect(function () { setCitation({ style: "ieee", text: "", bibtex: "", citekey: "", loading: false, error: "" }); if (client && selectedId) loadCitation("ieee"); }, [client, selectedId]);
    useEffect(function () { if (rightTab === "citation" && selectedId && !citation.text && !citation.loading && !citation.error) loadCitation(citation.style); }, [rightTab]);
    function copyCitation(format) {
      var value = format === "bibtex" ? citation.bibtex : citation.text;
      if (!value) return;
      navigator.clipboard.writeText(value).then(function () { Toast(format === "bibtex" ? L("library.bibtexCopied", "BibTeX copied") : L("library.citationCopied", "Citation copied")); });
    }

    var scopeTitle = scope.type === "all" ? L("library.title", "Knowledge base") : scope.type === "unclassified" ? L("library.unclassified", "Untagged") : scope.type === "recent_added" ? L("library.recentAdded", "Recently added") : scope.type === "recent_read" ? L("library.recentRead", "Recently read") : scope.type === "starred" ? L("library.starredKnowledge", "Starred knowledge") : scope.type === "trash" ? L("library.trash", "Trash") : (scope.label || L("library.title", "Knowledge base"));
    var activeFilters = [filters.file_type, filters.item_type, filters.status, filters.year].filter(Boolean).length;
    var sortOptions = [{ id: "updated_at", label: L("library.sortUpdated", "Recently updated") }, { id: "created_at", label: L("library.sortAdded", "Recently added") }, { id: "title", label: L("library.sortTitle", "Title") }, { id: "year", label: L("library.sortYear", "Year") }];
    var activeSortLabel = (sortOptions.find(function (option) { return option.id === sort; }) || sortOptions[0]).label;
    var citationProps = { style: citation.style, citation: citation.text, bibtex: citation.bibtex, citekey: citation.citekey, loading: citation.loading, error: citation.error, onStyle: loadCitation, onCopy: copyCitation, onRetry: function () { loadCitation(citation.style); } };
    var contextMenuPortal = contextMenu && typeof ReactDOM !== "undefined"
      ? ReactDOM.createPortal(h("div", { className: "wb-lib-context-layer", style: contextMenu.portalTheme },
        h("div", { className: "wb-lib-context-scrim", onPointerDown: function () { setContextMenu(null); } }),
        h("div", {
          className: "wb-lib-context-menu",
          role: "menu",
          "aria-label": itemTitle(contextMenu.item),
          style: { left: contextMenu.left + "px", top: contextMenu.top + "px" },
          onContextMenu: function (event) { event.preventDefault(); },
        },
          h("button", { type: "button", role: "menuitem", onClick: function () { var item = contextMenu.item; setContextMenu(null); select(item.id); } }, icon("panel", 15), L("library.openDetails", "Open details")),
          scope.type !== "trash" && h("button", { type: "button", role: "menuitem", onClick: function () { var item = contextMenu.item; setContextMenu(null); toggleStar(item); } }, icon("star", 15), contextMenu.item.starred ? L("library.removeStar", "Remove star") : L("library.addStar", "Add star")),
          Number(contextMenu.item.attachment_count || 0) > 0 && h("button", { type: "button", role: "menuitem", onClick: function () { var item = contextMenu.item; setContextMenu(null); window.open(client.rawUrl(item.id), "_blank"); } }, icon("eye", 15), L("library.openOriginal", "Open original file")),
          Number(contextMenu.item.attachment_count || 0) > 0 && h("button", { type: "button", role: "menuitem", onClick: function () { showLibraryItemInFolder(contextMenu.item); } }, icon("folder", 15), L("library.showInFolder", "Show in folder")),
          h("div", { className: "wb-lib-context-separator" }),
          scope.type === "trash"
            ? h(React.Fragment, null,
              h("button", { type: "button", role: "menuitem", onClick: function () { var item = contextMenu.item; setContextMenu(null); restore(item); } }, icon("restore", 15), L("library.restore", "Restore")),
              h("button", { type: "button", role: "menuitem", className: "danger", onClick: function () { var item = contextMenu.item; setContextMenu(null); permanentlyDeleteItem(item); } }, icon("trash", 15), L("library.permanentDelete", "Delete permanently")))
            : h("button", { type: "button", role: "menuitem", className: "danger", onClick: function () { var item = contextMenu.item; setContextMenu(null); removeItem(item); } }, icon("trash", 15), L("library.moveToTrash", "Move to trash"))
        )), document.body)
      : null;

    if (!workspace) return h("section", { className: "wb-lib-page no-project" }, h(StatePanel, { title: L("library.selectProject", "Select a project first"), body: L("library.projectIsolation", "Each project has an isolated knowledge base.") }));

    return h("section", { className: "wb-lib-page" },
      h("input", { ref: fileRef, className: "wb-lib-file-input", type: "file", multiple: true, onChange: function (event) { handleFiles(event.target.files); } }),
      h(LibrarySidebar, { onBack: props.onBack, stats: data.stats, collections: data.collections, tags: data.tags, scope: scope, onCreateCollection: function () { setCollectionModalOpen(true); }, onScope: function (next) { setScope(next); setSelectedId(""); setChecked([]); }, sidebarCollapsed: props.sidebarCollapsed, collapseControl: props.collapseControl, moduleDock: props.moduleDock }),
      h("main", { className: "wb-lib-main" },
        h("div", { className: "wb-workbench-filterbar wb-lib-commandbar" },
        h("header", { className: "wb-lib-main-head" },
          h("div", { className: "wb-lib-heading" }, scope.type !== "all" && h("h2", null, scopeTitle),
            h("div", { className: "wb-lib-menu-wrap" }, h("button", { type: "button", "data-tour": "knowledge_add", className: "wb-lib-primary", title: L("library.addItem", "Add item"), "aria-label": L("library.addItem", "Add item"), onClick: function () { setMenu(menu === "add" ? "" : "add"); } }, icon("plus", 16), " ", L("library.addItem", "Add item")), h(Dropdown, { open: menu === "add", onClose: function () { setMenu(""); } }, h("button", { type: "button", onClick: function () { setMenu(""); setManualOpen(true); } }, icon("note", 16), h("span", null, h("b", null, L("library.manualAdd", "Add manually")), h("small", null, L("library.createItem", "Create a knowledge item")))), h("button", { type: "button", onClick: function () { setMenu(""); fileRef.current && fileRef.current.click(); } }, icon("upload", 16), h("span", null, h("b", null, L("library.uploadFile", "Upload file")), h("small", null, L("library.uploadTypes", "Documents, images, audio and video"))))))),
          h("div", { className: "wb-lib-head-actions" },
            h("span", { className: "wb-lib-count" }, L("library.count", "Items: {count}", { count: formatNumber(data.total || 0) })),
            h("button", { type: "button", className: "wb-lib-head-button", title: !embedding.configured ? L("library.vectorizeUnavailable", "Install and enable an embedding model first") : !Number(embedding.pending_vectors || 0) ? L("library.vectorizedAll", "All chunks are vectorized") : L("library.vectorizeAllHint", "Missing chunks to vectorize: {count}", { count: Number(embedding.pending_vectors || 0) }), "aria-label": L("library.vectorizeAll", "Vectorize all"), disabled: !embedding.configured || !Number(embedding.pending_vectors || 0) || (embedding.reembed && embedding.reembed.running), onClick: vectorizeAll }, embedding.reembed && embedding.reembed.running ? h(Spinner) : icon("vector", 16), embedding.reembed && embedding.reembed.running ? L("library.vectorizing", "Vectorizing…") : L("library.vectorizeAll", "Vectorize all")),
            h("button", { type: "button", "data-tour": "knowledge_upload", className: "wb-lib-head-button", title: uploading ? L("library.importing", "Importing") : L("library.import", "Import"), "aria-label": uploading ? L("library.importing", "Importing") : L("library.import", "Import"), disabled: uploading, onClick: function () { fileRef.current && fileRef.current.click(); } }, uploading ? h(Spinner) : icon("upload", 16), uploading ? L("library.importing", "Importing") : L("library.import", "Import")),
            h("button", { type: "button", className: "wb-lib-head-button", title: L("library.export", "Export"), "aria-label": L("library.export", "Export"), onClick: exportItems, disabled: !data.items.length }, icon("download", 16), L("library.export", "Export")))),
        h("div", { className: "wb-lib-toolbar" },
          h("label", { className: "wb-workbench-searchbox wb-lib-search" }, icon("search", 17), h("input", { value: query, onChange: function (event) { setQuery(event.target.value); }, placeholder: L("library.searchPlaceholder", "Search this project's knowledge base…") }), query && h("button", { type: "button", onClick: function () { setQuery(""); }, title: L("library.clearSearch", "Clear search") }, icon("close", 13))),
          h("div", { className: "wb-workbench-toolbar-controls wb-lib-tools" },
            h("div", { className: "wb-lib-menu-wrap" }, h("button", { type: "button", className: "wb-workbench-filter-tool wb-lib-tool" + (activeFilters ? " active" : ""), onClick: function () { setMenu(menu === "filter" ? "" : "filter"); } }, h("span", null, activeFilters ? L("library.filter", "Filter") : L("library.allFilters", "All types")), activeFilters ? h("small", null, activeFilters) : null, icon("chevron", 13)), h(Dropdown, { open: menu === "filter", onClose: function () { setMenu(""); }, className: "filter" },
              h("label", null, L("library.filterFileType", "File type"), h("select", { value: filters.file_type, onChange: function (event) { setFilters(Object.assign({}, filters, { file_type: event.target.value })); } }, h("option", { value: "" }, L("library.allFileTypes", "All file types")), h("option", { value: "pdf" }, "PDF"), h("option", { value: "document" }, L("library.fileType.document", "Documents and text")), h("option", { value: "spreadsheet" }, L("library.fileType.spreadsheet", "Spreadsheets")), h("option", { value: "presentation" }, L("library.fileType.presentation", "Presentations")), h("option", { value: "image" }, L("library.fileType.image", "Images")), h("option", { value: "audio" }, L("library.fileType.audio", "Audio")), h("option", { value: "video" }, L("library.fileType.video", "Video")), h("option", { value: "link" }, L("library.fileType.link", "Web pages and links")), h("option", { value: "other" }, L("library.fileType.other", "Other files")))),
              h("label", null, L("library.bibliographyType", "Bibliography type"), h("select", { value: filters.item_type, onChange: function (event) { setFilters(Object.assign({}, filters, { item_type: event.target.value })); } }, h("option", { value: "" }, L("library.allBibliographyTypes", "All bibliography types")), h("option", { value: "document" }, L("library.knowledgeItem", "Knowledge item")), h("option", { value: "webpage" }, L("library.webpage", "Web page")), h("option", { value: "journalArticle" }, L("library.journalArticle", "Journal article")), h("option", { value: "conferencePaper" }, L("library.conferencePaper", "Conference paper")), h("option", { value: "book" }, L("library.book", "Book")), h("option", { value: "thesis" }, L("library.thesis", "Thesis")), h("option", { value: "report" }, L("library.report", "Report")))),
              h("label", null, L("library.readingStatus", "Reading status"), h("select", { value: filters.status, onChange: function (event) { setFilters(Object.assign({}, filters, { status: event.target.value })); } }, h("option", { value: "" }, L("library.allStatuses", "All statuses")), h("option", { value: "unread" }, L("library.unread", "Unread")), h("option", { value: "reading" }, L("library.reading", "Reading")), h("option", { value: "read" }, L("library.read", "Read")))),
              h("label", null, L("library.year", "Year"), h("input", { type: "number", value: filters.year, onChange: function (event) { setFilters(Object.assign({}, filters, { year: event.target.value })); }, placeholder: L("library.yearPlaceholder", "e.g. 2025") })),
              activeFilters > 0 && h("button", { type: "button", className: "wb-lib-clear-filter", onClick: function () { setFilters({ file_type: "", item_type: "", status: "", year: "" }); } }, L("library.clearFilters", "Clear filters")))),
            h("div", { className: "wb-lib-menu-wrap" }, h("button", { type: "button", className: "wb-workbench-filter-tool wb-lib-tool active", onClick: function () { setMenu(menu === "sort" ? "" : "sort"); } }, h("span", null, activeSortLabel), icon("chevron", 13)), h(Dropdown, { open: menu === "sort", onClose: function () { setMenu(""); }, className: "sort" }, sortOptions.map(function (option) { return h("button", { key: option.id, type: "button", className: sort === option.id ? "selected" : "", onClick: function () { setSort(option.id); setMenu(""); } }, option.label, sort === option.id && icon("check", 14)); }), h("button", { type: "button", onClick: function () { setOrder(order === "desc" ? "asc" : "desc"); setMenu(""); } }, order === "desc" ? L("library.desc", "Descending") : L("library.asc", "Ascending")))),
            h("div", { className: "wb-lib-view-toggle" }, h("button", { type: "button", className: view === "table" ? "active" : "", onClick: function () { setView("table"); }, title: L("library.tableView", "Table view") }, icon("list", 16)), h("button", { type: "button", className: view === "grid" ? "active" : "", onClick: function () { setView("grid"); }, title: L("library.cardView", "Card view") }, icon("grid", 16))),
            !rightOpen && selectedItem && h("button", { type: "button", className: "wb-lib-tool compact", onClick: function () { setRightOpen(true); }, title: L("library.openDetails", "Open details") }, icon("panel", 16))))),
        checked.length > 0 && h("div", { className: "wb-lib-batch" },
          h("b", null, L("library.selectedCount", "{count} selected", { count: checked.length })),
          h("div", { className: "wb-lib-batch-actions" },
            h("button", { type: "button", className: "danger", disabled: batchDeleting, onClick: removeChecked },
              batchDeleting ? h(Spinner) : icon("trash", 14),
              batchDeleting ? L("library.deleting", "Deleting…") : (scope.type === "trash" ? L("library.permanentDelete", "Delete permanently") : L("library.moveToTrash", "Move to trash"))),
            h("button", { type: "button", disabled: batchDeleting, onClick: function () { setChecked([]); } }, L("library.cancelSelection", "Cancel selection")))),
        error && h("div", { className: "wb-lib-error" }, h("span", null, error), h("button", { type: "button", onClick: function () { reload(); } }, icon("restore", 14), " ", L("library.retry", "Retry"))),
        h("section", { className: "wb-lib-results" },
          loading && !data.items.length ? h(StatePanel, { loading: true, title: L("library.loading", "Loading knowledge base…") }) : !data.items.length ? h(StatePanel, { title: query || activeFilters || scope.type !== "all" ? L("library.noMatch", "No matching knowledge") : L("library.noItems", "This project has no knowledge items yet"), body: query || activeFilters || scope.type !== "all" ? L("library.emptyHint", "Try adjusting your search, categories or filters.") : L("library.importHint", "Upload documents, images, audio or video, or import RIS, BibTeX and Zotero references."), action: query || activeFilters || scope.type !== "all" ? function () { setQuery(""); setFilters({ file_type: "", item_type: "", status: "", year: "" }); setScope({ type: "all" }); } : function () { fileRef.current && fileRef.current.click(); }, actionLabel: query || activeFilters || scope.type !== "all" ? L("library.clearConditions", "Clear conditions") : L("library.importFirstItem", "Import the first item"), actionIcon: query || activeFilters || scope.type !== "all" ? "restore" : "upload" }) :
            view === "table" ? h("div", { className: "wb-lib-table", role: "table" }, h(TableHead, { allSelected: data.items.length > 0 && checked.length === data.items.length, onToggleAll: toggleAll }), h("div", { className: "wb-lib-table-body" }, data.items.map(function (item) { return h(LibraryRow, { key: item.id, item: item, active: String(item.id) === String(selectedId), checked: checked.indexOf(String(item.id)) >= 0, trash: scope.type === "trash", onSelect: select, onContextMenu: openItemContextMenu, onToggle: toggleChecked, onStar: toggleStar, onRestore: restore, onDragStart: scope.type === "trash" ? null : startLibraryItemDrag, onDragEnd: endLibraryItemDrag }); }))) :
              h("div", { className: "wb-lib-card-grid" }, data.items.map(function (item) { return h(LibraryCard, { key: item.id, item: item, active: String(item.id) === String(selectedId), checked: checked.indexOf(String(item.id)) >= 0, onSelect: select, onContextMenu: openItemContextMenu, onToggle: toggleChecked, onStar: toggleStar, onDragStart: scope.type === "trash" ? null : startLibraryItemDrag, onDragEnd: endLibraryItemDrag }); })),
          data.items.length < data.total && h("button", { type: "button", className: "wb-lib-load-more", disabled: loadingMore, onClick: loadMore }, loadingMore ? h(Spinner) : null, loadingMore ? L("library.loadingMore", "Loading…") : L("library.loadMore", "Load more ({shown} / {total})", { shown: data.items.length, total: data.total })),
          loading && data.items.length > 0 && h("div", { className: "wb-lib-loading-bar" }, h(Spinner), " ", L("library.updating", "Updating…")))),
      h(RightPanel, { item: selectedItem, open: rightOpen, onClose: function () { setRightOpen(false); }, tab: rightTab, onTab: setRightTab, onContentViewed: function () { markSelectedRead(selectedId); }, rawUrl: selectedId ? client.rawUrl(selectedId) : "", collections: data.collections, onCollectionsUpdate: updateSelectedCollections, citation: citation.text, bibtex: citation.bibtex, citationLoading: citation.loading, citationProps: citationProps, onCopyCitation: copyCitation, onAddNote: addNote, onUpdate: updateSelected, onDelete: scope.type !== "trash" ? removeSelected : null }),
      contextMenuPortal,
      manualOpen && h(ManualItemModal, { onClose: function () { setManualOpen(false); }, onSave: createItem }),
      collectionModalOpen && h(CollectionModal, { onClose: function () { setCollectionModalOpen(false); }, onSave: createCollection }));
  }

  window.CyreneUI.library = window.CyreneUI.register("library", {
    Page: WorkbenchLibraryPage,
    createApi: libraryApi,
    FileVisual: LibraryFileVisual,
  });
})();
