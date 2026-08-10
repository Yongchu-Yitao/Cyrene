// Register KaTeX-backed math tokens with the shared marked.js parser.
// Every chat/workbench Markdown renderer uses window.marked, so keeping the
// extension here gives all conversation surfaces the same formula support.
(function () {
  if (!window.marked || !window.katex) return;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderFormula(token) {
    try {
      return window.katex.renderToString(token.text, {
        displayMode: Boolean(token.displayMode),
        throwOnError: false,
        strict: "ignore",
        trust: false,
        output: "htmlAndMathml",
      });
    } catch (error) {
      return '<span class="katex-error">' + escapeHtml(token.raw) + "</span>";
    }
  }

  function blockToken(raw, text) {
    return {
      type: "mathBlock",
      raw: raw,
      text: text.trim(),
      displayMode: true,
    };
  }

  function inlineToken(raw, text) {
    return {
      type: "mathInline",
      raw: raw,
      text: text,
      displayMode: false,
    };
  }

  window.marked.use({
    extensions: [
      {
        name: "mathBlock",
        level: "block",
        start: function (src) {
          var match = src.match(/(?:^|\n)[ \t]{0,3}(?:\$\$|\\\[)/);
          return match ? match.index + (match[0].charAt(0) === "\n" ? 1 : 0) : undefined;
        },
        tokenizer: function (src) {
          var dollar = /^[ \t]{0,3}\$\$[ \t]*\n?([\s\S]+?)\n?[ \t]*\$\$[ \t]*(?:\n|$)/.exec(src);
          if (dollar) return blockToken(dollar[0], dollar[1]);

          var bracket = /^[ \t]{0,3}\\\[[ \t]*\n?([\s\S]+?)\n?[ \t]*\\\][ \t]*(?:\n|$)/.exec(src);
          if (bracket) return blockToken(bracket[0], bracket[1]);
          return undefined;
        },
        renderer: function (token) {
          return renderFormula(token) + "\n";
        },
      },
      {
        name: "mathInline",
        level: "inline",
        start: function (src) {
          var dollarIndex = src.indexOf("$");
          var parenIndex = src.indexOf("\\(");
          if (dollarIndex < 0) return parenIndex < 0 ? undefined : parenIndex;
          if (parenIndex < 0) return dollarIndex;
          return Math.min(dollarIndex, parenIndex);
        },
        tokenizer: function (src) {
          var dollar = /^\$(?!\$)((?:\\.|[^\\$\n])+?)\$(?!\$)/.exec(src);
          if (dollar && dollar[1] && !/^\s|\s$/.test(dollar[1])) {
            return inlineToken(dollar[0], dollar[1]);
          }

          var paren = /^\\\(([^\n]+?)\\\)/.exec(src);
          if (paren && paren[1]) return inlineToken(paren[0], paren[1]);
          return undefined;
        },
        renderer: function (token) {
          return renderFormula(token);
        },
      },
    ],
  });
})();
