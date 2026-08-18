// One Markdown/sanitization pipeline shared by Workbench features. Callers may
// select their historical fallback and DOMPurify options without duplicating
// parse/sanitize ordering.
(function (root) {
  "use strict";

  function installCjkAutolinkBoundary() {
    if (
      !root.marked
      || typeof root.marked.use !== "function"
      || !root.marked.Tokenizer
      || root.marked.__cyreneCjkAutolinkBoundary
    ) return;

    var defaultUrlTokenizer = root.marked.Tokenizer.prototype.url;
    if (typeof defaultUrlTokenizer !== "function") return;
    // Marked's GFM bare-URL rule stops only at whitespace / `<`. Without this
    // override, `www.example.com），后文` becomes one link because CJK prose
    // commonly has no spaces. Keep explicit Markdown links untouched and trim
    // only auto-detected URLs at full-width / CJK punctuation boundaries.
    var cjkBoundary = /[，。；：！？、（）［］｛｝《》〈〉「」『』【】〔〕〖〗〘〙〚〛]/u;
    root.marked.use({
      tokenizer: {
        url: function (source) {
          var token = defaultUrlTokenizer.call(this, source);
          if (!token || typeof token.raw !== "string") return token || false;
          var boundaryIndex = token.raw.search(cjkBoundary);
          if (boundaryIndex <= 0) return token;
          return defaultUrlTokenizer.call(this, token.raw.slice(0, boundaryIndex) + " ");
        },
      },
    });
    root.marked.__cyreneCjkAutolinkBoundary = true;
  }

  function installStrictStrikethrough() {
    if (
      !root.marked
      || typeof root.marked.use !== "function"
      || root.marked.__cyreneStrictStrikethrough
    ) return;

    // GFM strikethrough uses paired `~~` delimiters. Marked also accepts a
    // single `~`, which makes two unrelated ranges such as `26~30℃` and
    // `25~32℃` form one cross-line <del>. Consume lone tildes as literal
    // text before Marked's built-in delimiter tokenizer sees them, while
    // leaving `~~...~~` untouched.
    root.marked.use({
      extensions: [{
        name: "cyreneSingleTilde",
        level: "inline",
        start: function (source) {
          var index = source.indexOf("~");
          return index < 0 ? undefined : index;
        },
        tokenizer: function (source) {
          if (!/^~(?!~)/.test(source)) return undefined;
          return { type: "text", raw: "~", text: "~" };
        },
      }],
    });
    root.marked.__cyreneStrictStrikethrough = true;
  }

  function installInteractiveBlocks() {
    if (
      !root.marked
      || typeof root.marked.use !== "function"
      || root.marked.__cyreneInteractiveBlocks
    ) return;

    var openingLine = /^ {0,3}:::(details|card|chart|button|actions|grid)(?:[ \t]+([^\n]*?))?[ \t]*(?:\n|$)/;
    var openingLineOnly = /^ {0,3}:::(?:details|card|chart|button|actions|grid)(?:[ \t]+[^\n]*)?[ \t]*$/;

    // Nesting is bounded: containers (:actions / :grid) may hold one level of
    // child blocks, and child blocks never nest further. The closing scan is
    // depth-aware anyway (openings increment, `:::` decrements) so a stray
    // nested directive never swallows the container's real end.
    function findClosingLine(source) {
      var offset = 0;
      var fence = null;
      var depth = 1;
      while (offset < source.length) {
        var newline = source.indexOf("\n", offset);
        var end = newline < 0 ? source.length : newline;
        var line = source.slice(offset, end);
        var rawLength = end - offset + (newline < 0 ? 0 : 1);
        if (fence) {
          var fenceClose = /^ {0,3}(`+|~+)[ \t]*$/.exec(line);
          if (
            fenceClose
            && fenceClose[1].charAt(0) === fence.character
            && fenceClose[1].length >= fence.length
          ) fence = null;
        } else {
          var fenceOpen = /^ {0,3}(`{3,}|~{3,})(?:[^\n]*)$/.exec(line);
          if (fenceOpen) {
            fence = { character: fenceOpen[1].charAt(0), length: fenceOpen[1].length };
          } else if (/^ {0,3}:::[ \t]*$/.test(line)) {
            depth -= 1;
            if (depth <= 0) return { index: offset, length: rawLength };
          } else if (openingLineOnly.test(line)) {
            depth += 1;
          }
        }
        if (newline < 0) break;
        offset += rawLength;
      }
      return null;
    }

    // The allowed child types per container; anything else (or a container
    // inside a container) fails validation and the whole block degrades to
    // plain text.
    function collectChildTypes(body) {
      var types = [];
      var fence = null;
      var lines = String(body || "").split("\n");
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (fence) {
          var fenceClose = /^ {0,3}(`+|~+)[ \t]*$/.exec(line);
          if (
            fenceClose
            && fenceClose[1].charAt(0) === fence.character
            && fenceClose[1].length >= fence.length
          ) fence = null;
          continue;
        }
        var fenceOpen = /^ {0,3}(`{3,}|~{3,})(?:[^\n]*)$/.exec(line);
        if (fenceOpen) {
          fence = { character: fenceOpen[1].charAt(0), length: fenceOpen[1].length };
          continue;
        }
        var opening = openingLine.exec(line);
        if (opening) types.push(opening[1]);
      }
      return types;
    }

    function parseGridCols(title) {
      var match = /^cols:[ \t]*([1-9][0-9]*)/.exec(String(title || "").trim());
      var cols = match ? Number(match[1]) : 1;
      return Math.max(1, Math.min(4, cols));
    }

    root.marked.use({
      extensions: [{
        name: "wbcblock",
        level: "block",
        start: function (source) {
          var match = source.match(/^ {0,3}:::(?:details|card|chart|button|actions|grid)(?:[ \t]+[^\n]*)?[ \t]*$/m);
          return match ? match.index : undefined;
        },
        tokenizer: function (source) {
          var opening = openingLine.exec(source);
          if (!opening) return undefined;
          var rest = source.slice(opening[0].length);
          var closing = findClosingLine(rest);
          if (!closing) return undefined;
          var body = rest.slice(0, closing.index);
          var rawLength = opening[0].length + closing.index + closing.length;
          var title = String(opening[2] || "").trim();
          var token = {
            type: "wbcblock",
            raw: source.slice(0, rawLength),
            blockType: opening[1],
            title: title,
            titleTokens: this.lexer.inlineTokens(title),
            specBody: body,
          };
          if (token.blockType === "chart" || token.blockType === "button") {
            // Chart and button bodies are declarative specs, never parsed as
            // Markdown.
            token.chartType = title;
            token.tokens = [];
          } else if (token.blockType === "actions" || token.blockType === "grid") {
            token.childTypes = collectChildTypes(body);
            token.cols = parseGridCols(title);
            token.tokens = this.lexer.blockTokens(body, []);
          } else {
            token.tokens = this.lexer.blockTokens(body, []);
          }
          return token;
        },
        renderer: function (token) {
          var titleHtml = token.titleTokens.length
            ? this.parser.parseInline(token.titleTokens)
            : "Details";
          var bodyHtml = this.parser.parse(token.tokens);
          var sourceAttr = ' data-wbc-source="' + escapeHtml(encodeURIComponent(String(token.raw || ""))) + '"';
          if (token.blockType === "details") {
            return '<details class="wbc-fold"' + sourceAttr + '><summary>' + titleHtml
              + '</summary><div class="wbc-fold-body">' + bodyHtml
              + '</div></details>\n';
          }
          if (token.blockType === "chart") {
            // The raw spec stays in the DOM as a fallback; the mount script
            // replaces it with the live chart when it can, and environments
            // without a chart mount simply keep the spec text visible.
            var chartSpec = root.CyreneUI.chartSpec;
            var specBody = String(token.specBody || "");
            var specHtml = '<pre class="wbc-chart-spec">' + escapeHtml(specBody) + "</pre>";
            if (chartSpec && typeof chartSpec.buildPayload === "function") {
              try {
                var payload = chartSpec.buildPayload(specBody, token.chartType);
                return '<div class="wbc-chart"' + sourceAttr + ' data-wbc-chart="' + escapeHtml(payload.json)
                  + '">' + specHtml + "</div>\n";
              } catch (error) {
                return '<div class="wbc-chart wbc-chart-error"' + sourceAttr + ' data-wbc-chart-error="'
                  + escapeHtml((error && error.message) || "invalid chart spec")
                  + '">' + specHtml + "</div>\n";
              }
            }
            return '<div class="wbc-chart wbc-chart-error"' + sourceAttr + ' data-wbc-chart-error="chart rendering is unavailable">'
              + specHtml + "</div>\n";
          }
          if (token.blockType === "button") {
            // The fallback renders a readable "[按钮: label]" line instead of
            // the raw spec; the mount script swaps in the real <button>.
            var buttonSpec = root.CyreneUI.chartSpec;
            var buttonBody = String(token.specBody || "");
            if (buttonSpec && typeof buttonSpec.buildButtonPayload === "function") {
              try {
                var buttonPayload = buttonSpec.buildButtonPayload(buttonBody);
                return '<div class="wbc-button"' + sourceAttr + ' data-wbc-button="' + escapeHtml(buttonPayload.json)
                  + '"><pre class="wbc-button-spec">['
                  + escapeHtml("按钮: " + buttonPayload.spec.label) + "]</pre></div>\n";
              } catch (error) {
                return '<div class="wbc-button wbc-button-error"' + sourceAttr + ' data-wbc-button-error="'
                  + escapeHtml((error && error.message) || "invalid button spec")
                  + '"><pre class="wbc-button-spec">' + escapeHtml(buttonBody) + "</pre></div>\n";
              }
            }
            return '<div class="wbc-button wbc-button-error"' + sourceAttr + ' data-wbc-button-error="button rendering is unavailable">'
              + '<pre class="wbc-button-spec">' + escapeHtml(buttonBody) + "</pre></div>\n";
          }
          if (token.blockType === "actions") {
            // Nested blocks are validated by type: an actions row may only
            // contain buttons, and never another container.
            var actionsValid = Array.isArray(token.childTypes)
              && token.childTypes.length > 0
              && token.childTypes.every(function (type) { return type === "button"; });
            if (!actionsValid) {
              return '<div class="wbc-actions wbc-actions-error"' + sourceAttr + '><pre class="wbc-actions-spec">'
                + escapeHtml(token.specBody) + "</pre></div>\n";
            }
            return '<div class="wbc-actions"' + sourceAttr + '>' + bodyHtml + "</div>\n";
          }
          if (token.blockType === "grid") {
            var gridValid = Array.isArray(token.childTypes)
              && token.childTypes.length > 0
              && token.childTypes.every(function (type) {
                return type === "card" || type === "chart";
              });
            if (!gridValid) {
              return '<div class="wbc-grid wbc-grid-error"' + sourceAttr + '><pre class="wbc-grid-spec">'
                + escapeHtml(token.specBody) + "</pre></div>\n";
            }
            return '<div class="wbc-grid"' + sourceAttr + ' style="grid-template-columns: repeat('
              + token.cols + ', minmax(0, 1fr))">' + bodyHtml + "</div>\n";
          }
          var cardTitle = token.title
            ? '<div class="wbc-card-title">' + titleHtml + '</div>'
            : "";
          return '<div class="wbc-card"' + sourceAttr + '>' + cardTitle
            + '<div class="wbc-card-body">' + bodyHtml + '</div></div>\n';
        },
      }],
    });
    root.marked.__cyreneInteractiveBlocks = true;
  }

  installCjkAutolinkBoundary();
  installStrictStrikethrough();
  installInteractiveBlocks();

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

  // Streaming replies hide complete and still-open interactive blocks until
  // reply_done replaces the live message. Code fences are tracked in both the
  // visible text and hidden blocks so a standalone `:::` inside code is never
  // mistaken for a directive boundary.
  function stripInteractiveBlocks(value) {
    var source = String(value == null ? "" : value).replace(/\r\n?/g, "\n");
    var lines = source.split("\n");
    var visible = [];
    var interactive = false;
    var depth = 0;
    var fence = null;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (fence) {
        if (!interactive) visible.push(line);
        var fenceClose = /^ {0,3}(`+|~+)[ \t]*$/.exec(line);
        if (
          fenceClose
          && fenceClose[1].charAt(0) === fence.character
          && fenceClose[1].length >= fence.length
        ) fence = null;
        continue;
      }

      var fenceOpen = /^ {0,3}(`{3,}|~{3,})(?:[^\n]*)$/.exec(line);
      if (fenceOpen) {
        fence = { character: fenceOpen[1].charAt(0), length: fenceOpen[1].length };
        if (!interactive) visible.push(line);
        continue;
      }

      if (interactive) {
        if (/^ {0,3}:::[ \t]*$/.test(line)) {
          depth -= 1;
          if (depth <= 0) {
            interactive = false;
            depth = 0;
          }
        } else if (/^ {0,3}:::(?:details|card|chart|button|actions|grid)(?:[ \t]+[^\n]*)?[ \t]*$/.test(line)) {
          depth += 1;
        }
        continue;
      }
      if (/^ {0,3}:::(?:details|card|chart|button|actions|grid)(?:[ \t]+[^\n]*)?[ \t]*$/.test(line)) {
        interactive = true;
        depth = 1;
        continue;
      }
      visible.push(line);
    }
    return visible.join("\n");
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
    renderRich: function (value, options) {
      var opts = options || {};
      var source = opts.interactive === false ? stripInteractiveBlocks(value) : value;
      return render(source, {
        fallback: "raw",
        sanitizeOptions: { ADD_ATTR: ["data-line", "data-language"] },
      });
    },
    sanitizeHtml: sanitizeHtml,
    stripInteractiveBlocks: stripInteractiveBlocks,
  };
  root.CyreneUI.markdown = root.CyreneUI.register("markdown", service);
})(window);
