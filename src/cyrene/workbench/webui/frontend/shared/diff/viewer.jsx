import { workbenchServices } from "../runtime/services.jsx"

// Diff viewer panel for the right sidebar.
// Registered as the shared Workbench diff service.

function diffT(key, fallback, vars) {
  return workbenchServices.i18n().t(key, vars, fallback);
}

function isBinaryDiff(text) {
  return text && /^Binary files /.test(text.trim());
}

function parseDiff(text) {
  if (!text) return [];
  var lines = text.split("\n");
  var hunks = [];
  var currentHunk = null;
  var leftLine = 0;
  var rightLine = 0;
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    if (line.indexOf("@@") === 0) {
      if (currentHunk) hunks.push(currentHunk);
      var match = line.match(/@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/);
      leftLine = match ? parseInt(match[1], 10) : 0;
      rightLine = match ? parseInt(match[3], 10) : 0;
      currentHunk = {
        header: line,
        context: match ? String(match[5] || "").trim() : "",
        lines: [],
        leftStart: leftLine,
        rightStart: rightLine,
        leftCount: match ? parseInt(match[2] || "1", 10) : 0,
        rightCount: match ? parseInt(match[4] || "1", 10) : 0,
      };
    } else if (currentHunk && line.indexOf("+") === 0) {
      currentHunk.lines.push({ type: "add", text: line.slice(1), leftNum: null, rightNum: rightLine++ });
    } else if (currentHunk && line.indexOf("-") === 0) {
      currentHunk.lines.push({ type: "del", text: line.slice(1), leftNum: leftLine++, rightNum: null });
    } else if (currentHunk && line.indexOf(" ") === 0) {
      currentHunk.lines.push({ type: "ctx", text: line.slice(1), leftNum: leftLine++, rightNum: rightLine++ });
    } else if (currentHunk && line === "\\ No newline at end of file") {
      currentHunk.lines.push({ type: "meta", text: diffT("chat.diff.noFinalNewline", "No newline at end of file"), leftNum: null, rightNum: null });
    }
  }
  if (currentHunk) hunks.push(currentHunk);
  return hunks;
}

function diffRangeLabel(start, count) {
  if (!count) return "∅";
  if (count === 1) return String(start);
  return String(start) + "–" + String(start + count - 1);
}

function compactHunkLines(lines, hunkIndex, expandedFolds) {
  var result = [];
  var index = 0;
  var foldIndex = 0;
  while (index < lines.length) {
    if (lines[index].type !== "ctx") {
      result.push(lines[index++]);
      continue;
    }
    var end = index;
    while (end < lines.length && lines[end].type === "ctx") end += 1;
    var run = lines.slice(index, end);
    var keepBefore = index > 0 ? Math.min(3, run.length) : 0;
    var keepAfter = end < lines.length ? Math.min(3, run.length - keepBefore) : 0;
    var hiddenCount = run.length - keepBefore - keepAfter;
    var foldKey = "h" + hunkIndex + "-f" + foldIndex++;
    if (hiddenCount < 4 || expandedFolds[foldKey]) {
      result = result.concat(run);
    } else {
      if (keepBefore) result = result.concat(run.slice(0, keepBefore));
      result.push({ type: "fold", count: hiddenCount, key: foldKey });
      if (keepAfter) result = result.concat(run.slice(run.length - keepAfter));
    }
    index = end;
  }
  return result;
}

function diffViewerStats(hunks) {
  return hunks.reduce(function (stats, hunk) {
    hunk.lines.forEach(function (line) {
      if (line.type === "add") stats.additions += 1;
      if (line.type === "del") stats.deletions += 1;
      stats.maxLineNumber = Math.max(
        stats.maxLineNumber, Number(line.leftNum || 0), Number(line.rightNum || 0)
      );
    });
    return stats;
  }, { additions: 0, deletions: 0, maxLineNumber: 0 });
}

function DiffHunk({ expandedFolds, hideHeader, hunk, index, onExpand }) {
  var leftRange = diffRangeLabel(hunk.leftStart, hunk.leftCount);
  var rightRange = diffRangeLabel(hunk.rightStart, hunk.rightCount);
  var displayLines = compactHunkLines(hunk.lines, index, expandedFolds);
  return React.createElement("div", { className: "diff-hunk" },
    !hideHeader && React.createElement("div", {
      className: "diff-hunk-header",
      title: hunk.header,
      "aria-label": diffT("chat.diff.changedLines", "Changed lines {left} to {right}", { left: leftRange, right: rightRange }),
    },
      React.createElement("span", { className: "diff-hunk-range old" }, leftRange),
      React.createElement("span", { className: "diff-hunk-arrow", "aria-hidden": "true" }, "→"),
      React.createElement("span", { className: "diff-hunk-range new" }, rightRange),
      hunk.context && React.createElement("span", { className: "diff-hunk-context" }, hunk.context)
    ),
    displayLines.map(function (line, lineIndex) {
      if (line.type === "fold") {
        return React.createElement("button", {
          type: "button", key: line.key, className: "diff-context-fold",
          onClick: function () { onExpand(line.key); },
          "aria-label": diffT("chat.diff.expandUnchanged", "Show {count} unchanged lines", { count: line.count }),
        },
          React.createElement("span", { className: "diff-context-fold-chevron", "aria-hidden": "true" }, "⌄"),
          React.createElement("span", null, diffT("chat.diff.unchangedLines", "{count} unchanged lines", { count: line.count }))
        );
      }
      return React.createElement("div", { key: "l" + lineIndex, className: "diff-line diff-line-" + line.type },
        React.createElement("span", { className: "diff-marker", "aria-hidden": "true" }),
        React.createElement("span", { className: "diff-ln" }, line.type === "del" ? line.leftNum : line.rightNum),
        React.createElement("span", { className: "diff-text" }, line.text)
      );
    })
  );
}

function DiffViewerContent({ binary, diffText, expandedFolds, hideHunkHeaders, hunks, onExpand }) {
  if (!diffText) {
    return React.createElement("div", { className: "diff-viewer-empty" }, diffT("chat.diff.noDifferences", "No differences"));
  }
  if (binary) return React.createElement("div", { className: "diff-viewer-empty" }, diffText.trim());
  if (!hunks.length) {
    return React.createElement("div", { className: "diff-viewer-empty" }, diffT("chat.diff.noDifferences", "No differences"));
  }
  return hunks.map(function (hunk, index) {
    return <DiffHunk key={"h" + index} expandedFolds={expandedFolds} hideHeader={hideHunkHeaders} hunk={hunk} index={index} onExpand={onExpand} />;
  });
}

(function () {
  if (typeof window === "undefined") return;
  if (typeof React === "undefined") return;

  var useState = React.useState;
  var useEffect = React.useEffect;
  var createElement = React.createElement;

  function DiffViewerPanel(props) {
    var dataStore = workbenchServices.data();
    dataStore.useVersion();
    var pluginModules = Array.isArray(dataStore.state.pluginModules)
      ? dataStore.state.pluginModules : [];
    var codeAvailable = pluginModules.indexOf("code") >= 0;
    var [diffText, setDiffText] = useState(props.diff || "");
    var [loading, setLoading] = useState(false);
    var [expandedFolds, setExpandedFolds] = useState({});

    useEffect(function () {
      if (!codeAvailable) {
        setLoading(false);
        return;
      }
      if (props.mode === "file" && props.left && props.right) {
        setLoading(true);
        fetch("/api/code/diff", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "file", left: props.left, right: props.right }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            setDiffText(data.diff || data.error || "");
            setLoading(false);
          })
          .catch(function (e) {
            setDiffText(diffT("chat.diff.errorPrefix", "Error") + ": " + (e.message || e));
            setLoading(false);
          });
      } else {
        setDiffText(props.diff || "");
      }
      setExpandedFolds({});
    }, [props.diff, props.left, props.right, props.mode, codeAvailable]);

    if (!codeAvailable) return null;

    var binary = isBinaryDiff(diffText);
    var hunks = parseDiff(diffText);
    var stats = diffViewerStats(hunks);
    var lineNumberDigits = Math.max(1, String(stats.maxLineNumber || 0).length);
    function expandFold(key) {
      setExpandedFolds(function (current) {
        return Object.assign({}, current, { [key]: true });
      });
    }

    return createElement("div", {
      className: "diff-viewer-panel",
      style: {
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        "--diff-line-number-width": "calc(" + lineNumberDigits + "ch + 14px)",
      },
    },
      // Header (optional for compact embedded viewers such as Workbench Changes)
      !props.hideHeader && createElement("div", { className: "diff-viewer-header" },
        createElement("span", { className: "diff-viewer-title" }, diffT("chat.diff.title", "Diff")),
        props.left && props.right && createElement("span", { className: "diff-viewer-files" },
          props.left + " → " + props.right
        ),
        createElement("span", { style: { flex: 1 } }),
        createElement("span", { className: "diff-viewer-stats", "aria-label": diffT("chat.diff.summary", "{additions} additions, {deletions} deletions", stats) },
          createElement("b", null, "+" + stats.additions),
          createElement("i", null, "−" + stats.deletions)
        ),
        props.onClose && createElement("button", {
          className: "code-editor-close-btn",
          onClick: props.onClose,
          "aria-label": diffT("common.close", "Close"),
        }, "×")
      ),
      // Content
      loading
        ? createElement("div", {
            className: "diff-viewer-empty",
          }, diffT("chat.diff.loading", "Loading diff..."))
        : createElement("div", { className: "diff-viewer-content" },
            createElement(DiffViewerContent, {
              binary: binary, diffText: diffText, expandedFolds: expandedFolds,
              hideHunkHeaders: props.hideHunkHeaders, hunks: hunks, onExpand: expandFold,
            })
          )
    );
  }

  // ── Global API ──

  var diffService = {
    Panel: DiffViewerPanel,
    open: function (diffText) {
      window.dispatchEvent(
        new CustomEvent("cyrene:open-diff", {
          detail: { diff: diffText || "", mode: "text" },
          bubbles: true,
        })
      );
    },
    openFiles: function (left, right) {
      window.dispatchEvent(
        new CustomEvent("cyrene:open-diff", {
          detail: { mode: "file", left: left, right: right },
          bubbles: true,
        })
      );
    },
  };

  window.CyreneUI.diff = window.CyreneUI.register("diff", diffService);
})();
