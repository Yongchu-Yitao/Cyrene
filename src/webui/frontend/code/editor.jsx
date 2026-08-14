import { Compartment, EditorState } from "@codemirror/state";
import {
  EditorView,
  drawSelection,
  dropCursor,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  rectangularSelection,
} from "@codemirror/view";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import {
  bracketMatching,
  defaultHighlightStyle,
  foldGutter,
  foldKeymap,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { closeBrackets, closeBracketsKeymap, autocompletion, completionKeymap } from "@codemirror/autocomplete";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

(function (root) {
  "use strict";

  var useEffect = React.useEffect;
  var useRef = React.useRef;

  function languageName(file) {
    var name = String(file && (file.name || file.path) || "").toLowerCase();
    var ext = name.indexOf(".") >= 0 ? name.split(".").pop() : "";
    if (ext === "py") return "python";
    if (["js", "jsx"].indexOf(ext) >= 0) return "javascript";
    if (["ts", "tsx"].indexOf(ext) >= 0) return "typescript";
    if (["html", "htm", "vue", "svelte"].indexOf(ext) >= 0) return "html";
    if (["css", "scss"].indexOf(ext) >= 0) return "css";
    if (ext === "json") return "json";
    if (["md", "mdx", "markdown"].indexOf(ext) >= 0) return "markdown";
    return ext || "text";
  }

  function languageExtension(name) {
    if (name === "python") return python();
    if (name === "javascript") return javascript({ jsx: true });
    if (name === "typescript") return javascript({ jsx: true, typescript: true });
    if (name === "html") return html();
    if (name === "css") return css();
    if (name === "json") return json();
    if (name === "markdown") return markdown();
    return [];
  }

  function isDark() {
    return document.documentElement.dataset.theme === "dark";
  }

  function editorTheme(dark) {
    return EditorView.theme({
      "&": {
        height: "100%",
        color: "var(--wb-text)",
        backgroundColor: "var(--wb-card-bg)",
        fontSize: "12px",
      },
      ".cm-scroller": {
        overflow: "auto",
        fontFamily: 'var(--mono, "IBM Plex Mono", monospace)',
        lineHeight: "1.62",
      },
      ".cm-content": { padding: "10px 0 24px", caretColor: "var(--wb-accent)" },
      ".cm-line": { padding: "0 12px" },
      ".cm-focused": { outline: "none" },
      ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--wb-accent)" },
      ".cm-selectionBackground, ::selection": {
        backgroundColor: dark ? "rgba(106, 150, 255, .25)" : "rgba(74, 125, 220, .18)",
      },
      ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--wb-accent) 6%, transparent)" },
      ".cm-gutters": {
        color: "var(--wb-faint)",
        backgroundColor: "color-mix(in srgb, var(--wb-panel-2) 86%, transparent)",
        borderRight: "1px solid var(--wb-line)",
      },
      ".cm-activeLineGutter": {
        color: "var(--wb-text)",
        backgroundColor: "color-mix(in srgb, var(--wb-accent) 8%, transparent)",
      },
      ".cm-panels": { color: "var(--wb-text)", backgroundColor: "var(--wb-panel-2)" },
      ".cm-searchMatch": { backgroundColor: "rgba(245, 190, 65, .28)" },
      ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "rgba(245, 150, 65, .42)" },
      ".cm-tooltip": {
        color: "var(--wb-text)",
        backgroundColor: "var(--wb-flyout-bg)",
        border: "1px solid var(--wb-line)",
      },
    }, { dark: dark });
  }

  function Editor(props) {
    var hostRef = useRef(null);
    var viewRef = useRef(null);
    var valueRef = useRef(String(props.value == null ? "" : props.value));
    var onChangeRef = useRef(props.onChange);
    var onSaveRef = useRef(props.onSave);
    var themeCompartmentRef = useRef(null);
    var languageCompartmentRef = useRef(null);
    var applyingExternalRef = useRef(false);

    onChangeRef.current = props.onChange;
    onSaveRef.current = props.onSave;

    useEffect(function () {
      if (!hostRef.current) return undefined;
      var themeCompartment = new Compartment();
      var languageCompartment = new Compartment();
      themeCompartmentRef.current = themeCompartment;
      languageCompartmentRef.current = languageCompartment;
      var saveBinding = {
        key: "Mod-s",
        preventDefault: true,
        run: function () {
          if (onSaveRef.current) onSaveRef.current();
          return true;
        },
      };
      var state = EditorState.create({
        doc: valueRef.current,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          highlightSpecialChars(),
          history(),
          foldGutter(),
          drawSelection(),
          dropCursor(),
          EditorState.allowMultipleSelections.of(true),
          indentOnInput(),
          bracketMatching(),
          closeBrackets(),
          autocompletion(),
          rectangularSelection(),
          highlightActiveLine(),
          highlightSelectionMatches(),
          syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
          keymap.of([
            saveBinding,
            indentWithTab,
            ...closeBracketsKeymap,
            ...defaultKeymap,
            ...searchKeymap,
            ...historyKeymap,
            ...foldKeymap,
            ...completionKeymap,
          ]),
          themeCompartment.of(editorTheme(isDark())),
          languageCompartment.of(languageExtension(languageName(props.file))),
          EditorView.updateListener.of(function (update) {
            if (!update.docChanged || applyingExternalRef.current) return;
            valueRef.current = update.state.doc.toString();
            if (onChangeRef.current) onChangeRef.current(valueRef.current);
          }),
        ],
      });
      var view = new EditorView({ state: state, parent: hostRef.current });
      viewRef.current = view;

      function updateTheme() {
        if (!viewRef.current || !themeCompartmentRef.current) return;
        viewRef.current.dispatch({
          effects: themeCompartmentRef.current.reconfigure(editorTheme(isDark())),
        });
      }
      window.addEventListener("cyrene-tweak-theme-change", updateTheme);
      return function () {
        window.removeEventListener("cyrene-tweak-theme-change", updateTheme);
        view.destroy();
        if (viewRef.current === view) viewRef.current = null;
      };
    }, []);

    useEffect(function () {
      var view = viewRef.current;
      var next = String(props.value == null ? "" : props.value);
      valueRef.current = next;
      if (!view) return;
      var current = view.state.doc.toString();
      if (current === next) return;
      applyingExternalRef.current = true;
      view.dispatch({ changes: { from: 0, to: current.length, insert: next } });
      applyingExternalRef.current = false;
    }, [props.value]);

    useEffect(function () {
      var view = viewRef.current;
      var compartment = languageCompartmentRef.current;
      if (!view || !compartment) return;
      view.dispatch({ effects: compartment.reconfigure(languageExtension(languageName(props.file))) });
    }, [props.file && (props.file.path || props.file.name)]);

    return <div ref={hostRef} className="wbc-codemirror-host" aria-label={props.ariaLabel || "Text editor"} />;
  }

  var turndown = new TurndownService({
    headingStyle: "atx",
    bulletListMarker: "-",
    codeBlockStyle: "fenced",
    emDelimiter: "*",
    strongDelimiter: "**",
  });
  turndown.use(gfm);
  turndown.addRule("cyreneInteractiveBlock", {
    filter: function (node) {
      return node.nodeType === 1 && node.hasAttribute("data-wbc-source");
    },
    replacement: function (_content, node) {
      var source = String(node.getAttribute("data-wbc-source") || "");
      try { source = decodeURIComponent(source); } catch (e) {}
      return "\n\n" + source.replace(/^\n+|\n+$/g, "") + "\n\n";
    },
  });

  function markdownFromElement(element) {
    if (!element) return "";
    return turndown.turndown(element.innerHTML)
      .replace(/\u00a0/g, " ")
      .replace(/\n{3,}/g, "\n\n");
  }

  root.CyreneCodeMirror = Object.freeze({
    Editor: Editor,
    languageName: languageName,
    markdownFromElement: markdownFromElement,
  });
})(window);
