// Pre-configure marked.js with highlight.js for syntax highlighting.
// Must be loaded after marked.js + highlight.min.js but before chat.jsx.
//
// marked v5+ removed the `highlight` option from setOptions, so the old
// `setOptions({ highlight: ... })` form was silently ignored (dead code).
// The v13-correct integration is a custom `code` renderer piped through
// `marked.use`, returning the full <pre><code>...</code></pre> block.
(function () {
  if (!window.marked || !window.hljs) return;

  window.marked.use({
    gfm: true,
    breaks: true,
    renderer: {
      code: function (code, lang, escaped) {
        var language = String(lang || "").trim();
        var result;
        try {
          if (language && hljs.getLanguage(language)) {
            result = hljs.highlight(code, { language: language, ignoreIllegals: true });
          } else {
            // Automatic detection is unreliable for short snippets, diagrams,
            // logs and pseudocode (for example, an ASCII diagram can be
            // labelled as C++). Only apply syntax highlighting when the
            // markdown fence explicitly declares a supported language.
            language = "text";
            result = { value: String(code)
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;") };
          }
        } catch (e) {
          var safe = String(code)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
          return '<pre><code class="hljs">' + safe + "</code></pre>\n";
        }
        var safeLang = language.replace(/[/\\:+*?.()\[\]{}|^$#@!~`'"&;<>=,\-%]/g, "");
        var lines = result.value.split("\n");
        var numbered = lines
          .map(function (line, i) {
            return (
              '<span class="hljs-ln-line">' +
              '<span class="hljs-ln-n" data-line="' +
              (i + 1) +
              '"></span>' +
              line +
              "</span>"
            );
          })
          .join("\n");
        return (
          '<pre data-language="' +
          language +
          '"><code class="hljs language-' +
          safeLang +
          '" data-language="' +
          language +
          '">' +
          numbered +
          "</code></pre>\n"
        );
      },
    },
  });

  window.CodeHighlight = {
    getLanguageName: function (lang) {
      var map = {
        py: "Python",
        python: "Python",
        js: "JavaScript",
        javascript: "JavaScript",
        ts: "TypeScript",
        typescript: "TypeScript",
        html: "HTML",
        css: "CSS",
        json: "JSON",
        md: "Markdown",
        markdown: "Markdown",
        sh: "Shell",
        bash: "Bash",
        shell: "Shell",
        sql: "SQL",
        yaml: "YAML",
        yml: "YAML",
        toml: "TOML",
        xml: "XML",
        rust: "Rust",
        go: "Go",
        java: "Java",
        cpp: "C++",
        "c++": "C++",
        c: "C",
        rb: "Ruby",
        ruby: "Ruby",
        php: "PHP",
        swift: "Swift",
        kotlin: "Kotlin",
        scala: "Scala",
        r: "R",
        text: "Text",
      };
      return map[lang] || lang || "Code";
    },
  };
})();
