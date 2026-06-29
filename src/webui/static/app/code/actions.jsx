// Inject action buttons (copy, edit) into code blocks rendered in chat messages.
// Uses MutationObserver to handle dynamically loaded messages.

(function () {
  if (typeof document === "undefined") return;

  function createButton(text, title, onClick) {
    var btn = document.createElement("button");
    btn.className = "code-action-btn";
    btn.textContent = text;
    btn.title = title;
    btn.type = "button";
    btn.addEventListener("click", onClick);
    return btn;
  }

  function getCodeText(pre) {
    var code = pre.querySelector("code");
    if (!code) return "";
    // Extract raw text, stripping line-number spans.
    var clone = code.cloneNode(true);
    var nums = clone.querySelectorAll(".hljs-ln-n");
    for (var i = 0; i < nums.length; i++) nums[i].remove();
    return clone.textContent || "";
  }

  function getLanguage(pre) {
    var code = pre.querySelector("code");
    if (code && code.dataset.language) return code.dataset.language;
    if (pre.dataset.language) return pre.dataset.language;
    var cls = pre.className || "";
    var m = cls.match(/language-(\S+)/);
    return m ? m[1] : "";
  }

  function writeClipboardText(text) {
    try {
      if (window.cyrene && typeof window.cyrene.writeClipboardText === "function") {
        var result = window.cyrene.writeClipboardText(text);
        return Promise.resolve(result).then(function (ok) {
          if (ok === false) throw new Error("Desktop clipboard write failed");
        });
      }
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        return navigator.clipboard.writeText(text);
      }
    } catch (error) {
      return Promise.reject(error);
    }

    // Browser fallback for localhost/non-secure contexts where the async
    // Clipboard API is unavailable.
    return new Promise(function (resolve, reject) {
      var input = document.createElement("textarea");
      input.value = text;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      try {
        if (!document.execCommand("copy")) throw new Error("Clipboard copy was rejected");
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        input.remove();
      }
    });
  }

  function addActions(pre) {
    if (pre.dataset.actionsAdded === "1") return;
    pre.dataset.actionsAdded = "1";

    var code = getCodeText(pre);
    var lang = getLanguage(pre);

    var bar = document.createElement("div");
    bar.className = "code-block-actions";

    if (lang) {
      var label = document.createElement("span");
      label.className = "code-lang-label";
      label.textContent =
        (window.CodeHighlight && window.CodeHighlight.getLanguageName(lang)) || lang;
      bar.appendChild(label);
    }

    var spacer = document.createElement("span");
    spacer.style.flex = "1";
    bar.appendChild(spacer);

    bar.appendChild(
      createButton("Copy", "Copy code", function () {
        var button = this;
        writeClipboardText(code).then(
          function () {
            button.textContent = "Copied!";
            button.dataset.state = "success";
            setTimeout(function () {
              button.textContent = "Copy";
              delete button.dataset.state;
            }, 1500);
          },
          function () {
            button.textContent = "Failed";
            button.dataset.state = "error";
            setTimeout(function () {
              button.textContent = "Copy";
              delete button.dataset.state;
            }, 1800);
          }
        );
      })
    );

    bar.appendChild(
      createButton("Edit", "Open in editor", function () {
        var evt = new CustomEvent("cyrene:open-editor", {
          detail: { code: code, language: lang },
          bubbles: true,
        });
        window.dispatchEvent(evt);
      })
    );

    pre.style.position = "relative";
    pre.appendChild(bar);
  }

  function scanMessages(root) {
    var pres = root.querySelectorAll
      ? root.querySelectorAll("pre")
      : [];
    for (var i = 0; i < pres.length; i++) addActions(pres[i]);
    // Also scan the root itself if it's a pre.
    if (root.tagName === "PRE") addActions(root);
  }

  // Legacy chat uses .msg-list; Workbench chat uses .wbc-thread; the
  // Workbench side panel (viewer, context, artifacts) uses .wbc-side-body.
  // These are stable containers that survive tab/session switches within a
  // page. A light 2s poll re-attaches observers after full page navigations
  // (the app is an SPA — pages unmount/remount on navigation).
  var _selectors = [".msg-list", ".wbc-thread", ".wbc-side-body"];
  var _watched = {};

  function watchContainer(selector, container) {
    var prev = _watched[selector];
    if (prev) {
      if (prev.node === container) return;
      prev.observer.disconnect();
    }
    scanMessages(container);
    var observer = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var added = mutations[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          if (added[j].nodeType === 1) scanMessages(added[j]);
        }
      }
    });
    observer.observe(container, { childList: true, subtree: true });
    _watched[selector] = { node: container, observer: observer };
  }

  function start() {
    for (var i = 0; i < _selectors.length; i++) {
      var selector = _selectors[i];
      var container = document.querySelector(selector);
      if (container) {
        watchContainer(selector, container);
      } else if (_watched[selector]) {
        _watched[selector].observer.disconnect();
        delete _watched[selector];
      }
    }
  }

  function init() {
    start();
    setInterval(start, 2000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
