// One Markdown/sanitization pipeline shared by Workbench features. Callers may
// select their historical fallback and DOMPurify options without duplicating
// parse/sanitize ordering.
(function (root) {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function fallbackHtml(source, mode) {
    if (mode === "escaped-breaks") return escapeHtml(source).replace(/\n/g, "<br>");
    if (mode === "raw-breaks") return source.replace(/\n/g, "<br>");
    return source;
  }

  function render(value, options) {
    var opts = options || {};
    var source = String(value == null ? "" : value);
    try {
      var html = root.marked && typeof root.marked.parse === "function"
        ? root.marked.parse(source)
        : fallbackHtml(source, opts.fallback || "raw");
      if (root.DOMPurify && typeof root.DOMPurify.sanitize === "function") {
        return root.DOMPurify.sanitize(html, opts.sanitizeOptions || {});
      }
      return html;
    } catch (error) {
      if (Object.prototype.hasOwnProperty.call(opts, "errorValue")) {
        return opts.errorValue;
      }
      return fallbackHtml(source, opts.errorFallback || opts.fallback || "raw");
    }
  }

  function sanitizeHtml(value, options) {
    var source = String(value == null ? "" : value);
    try {
      return root.DOMPurify && typeof root.DOMPurify.sanitize === "function"
        ? root.DOMPurify.sanitize(source, options || {})
        : source;
    } catch (error) {
      return source;
    }
  }

  var service = {
    escapeHtml: escapeHtml,
    render: render,
    renderRich: function (value) {
      return render(value, {
        fallback: "raw",
        sanitizeOptions: { ADD_ATTR: ["data-line", "data-language"] },
      });
    },
    sanitizeHtml: sanitizeHtml,
  };
  root.CyreneUI.markdown = root.CyreneUI.register("markdown", service);
})(window);
