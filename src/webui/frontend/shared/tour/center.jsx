// Tutorial center: a portal overlay listing every guide grouped by module,
// with per-guide detail, step preview and a start button. Mirror of the
// search overlay's interaction (portal, Escape, click-outside, focus
// restore) so the tutorial center feels native.
(function (root) {
  "use strict";

  const { useState, useEffect, useRef } = React;

  var tour = function () { return root.CyreneUI.require("tour"); };
  var guides = function () { return root.CyreneUI.require("tour-guides"); };

  function TutorialCenter({ onClose, onStart, initialGuideId }) {
    var { t } = root.CyreneUI.require("i18n").use();
    var catalog = tour().catalog();
    var [selectedId, setSelectedId] = useState(initialGuideId || null);
    var panelRef = useRef(null);

    useEffect(function () {
      var prevActive = document.activeElement;
      if (panelRef.current) panelRef.current.focus();
      return function () {
        if (prevActive && typeof prevActive.focus === "function") prevActive.focus();
      };
    }, []);

    useEffect(function () {
      function onKey(e) {
        if (e.key === "Escape") onClose();
      }
      window.addEventListener("keydown", onKey);
      return function () { window.removeEventListener("keydown", onKey); };
    }, [onClose]);

    // Auto-select the first guide until the user picks one.
    var firstGuideId = null;
    for (var ci = 0; ci < catalog.length; ci += 1) {
      if (catalog[ci].guides.length) { firstGuideId = catalog[ci].guides[0].id; break; }
    }
    useEffect(function () {
      if (!selectedId && firstGuideId) setSelectedId(firstGuideId);
    }, [selectedId, firstGuideId]);

    // Resolve the selected guide entry from the catalog.
    var selected = null;
    for (var i = 0; i < catalog.length && !selected; i += 1) {
      var match = null;
      for (var j = 0; j < catalog[i].guides.length; j += 1) {
        if (catalog[i].guides[j].id === selectedId) { match = catalog[i].guides[j]; break; }
      }
      if (match) selected = match;
    }

    var selectedGuide = selected ? guides().find(selected.id) : null;

    return React.createElement("div", {
      className: "tour-center-overlay",
      onClick: function (e) { if (e.target === e.currentTarget) onClose(); },
    },
      React.createElement("div", {
        className: "tour-center-panel",
        ref: panelRef,
        tabIndex: -1,
        role: "dialog",
        "aria-label": t("tour.center.title"),
        onClick: function (e) { e.stopPropagation(); },
      },
        React.createElement("header", { className: "tour-center-head" },
          React.createElement("div", { className: "tour-center-head-copy" },
            React.createElement("h2", null, t("tour.center.title")),
            React.createElement("p", null, t("tour.center.subtitle")),
          ),
          React.createElement("button", { type: "button", className: "workbench-icon-btn", onClick: onClose, title: t("common.close") },
            React.createElement("svg", { viewBox: "0 0 24 24", width: "16", height: "16", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round" },
              React.createElement("path", { d: "m6 6 12 12M18 6 6 18" })
            )
          ),
        ),
        React.createElement("div", { className: "tour-center-body" },
          React.createElement("nav", { className: "tour-center-list", "aria-label": t("tour.center.listLabel") },
            catalog.map(function (module) {
              return React.createElement("div", { className: "tour-center-module", key: module.id },
                React.createElement("div", { className: "tour-center-module-title" }, t(module.labelKey)),
                module.guides.map(function (guide) {
                  return React.createElement("button", {
                    type: "button",
                    key: guide.id,
                    className: "tour-center-guide" + (selected && guide.id === selected.id ? " active" : ""),
                    onClick: function () { setSelectedId(guide.id); },
                  },
                    React.createElement("span", { className: "tour-center-guide-check" },
                      guide.done
                        ? React.createElement("svg", { viewBox: "0 0 24 24", width: "15", height: "15", fill: "none", stroke: "currentColor", strokeWidth: "2.2", strokeLinecap: "round", strokeLinejoin: "round" },
                          React.createElement("path", { d: "m5 13 4 4L19 7" })
                        )
                        : null
                    ),
                    React.createElement("span", { className: "tour-center-guide-main" },
                      React.createElement("b", null, t(guide.titleKey)),
                      React.createElement("small", null, t("tour.center.meta", { minutes: guide.minutes, steps: guide.total })),
                    )
                  );
                })
              );
            })
          ),
          selectedGuide && React.createElement("div", { className: "tour-center-detail" },
            React.createElement("div", { className: "tour-center-detail-title" }, t(selectedGuide.titleKey)),
            React.createElement("div", { className: "tour-center-detail-desc" }, t(selectedGuide.descKey)),
            React.createElement("div", { className: "tour-center-detail-steps" },
              selectedGuide.steps.map(function (step, idx) {
                var stepTitle = t("tour." + selectedGuide.id + "." + step.id + ".title", null, step.id);
                return React.createElement("div", { className: "tour-center-step" + (step.target ? "" : " tour-center-step-doc"), key: step.id },
                  React.createElement("span", { className: "tour-center-step-index" }, idx + 1),
                  React.createElement("span", { className: "tour-center-step-label" }, stepTitle),
                  React.createElement("span", { className: "tour-center-step-kind" },
                    step.target ? t("tour.center.kind.spotlight") : t("tour.center.kind.doc")
                  ),
                );
              })
            ),
            selected.done
              ? React.createElement("div", { className: "tour-center-detail-done" },
                  t("tour.center.done"),
                  React.createElement("button", { type: "button", className: "wb-btn ghost", onClick: function () { tour().setDone(selected.id, false); } },
                    t("tour.center.reset")
                  )
                )
              : null,
            React.createElement("div", { className: "tour-center-detail-actions" },
              React.createElement("button", { type: "button", className: "wb-btn primary", onClick: function () { onStart(selected.id); } },
                selected.done
                  ? t("tour.center.replay")
                  : t("tour.center.start")
              )
            )
          )
        )
      )
    );
  }

  root.CyreneUI.register("tour-center", { Overlay: TutorialCenter });
})(window);
